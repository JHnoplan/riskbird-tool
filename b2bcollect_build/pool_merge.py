# -*- coding: utf-8 -*-
"""pool_merge.py —— 记录合并与实体去重（阻塞键分桶，线性复杂度）。

为什么需要这个模块：
    旧实现把「疑似重复」判定交给 dedup_engine.merge_duplicates，它对所有记录
    做两两 SequenceMatcher 比较，实测单对约 85μs：1000 条 = 42s、
    5000 条 = 90s、6.7 万条 = 以「年」计。数据池一旦跨过万级，
    这条 pass 就等于卡死（上传后再打开搜索框永远转圈）。

大厂做法（Entity Resolution / Record Linkage 标准链路）：
    blocking（阻塞分桶）→ matching（桶内两两打分）→ union-find 聚组 → merge。
    Google/Microsoft 的实体解析、LinkedIn 的 People Skills、阿里「同名实体归一」
    都是这个骨架：先用廉价代价把「不可能重复」的记录对剔除，只在候选块内做
    昂贵比较，把 O(n²) 降到 O(n·块大小)。

本模块的阻塞键设计（每条记录产出多个键，任意键相同即进入同一块）：
    norm:<company_norm>          归一化全名（最强）
    pre:<前2字>:<长度桶>          「苏州快可光伏电子」vs「苏州快可光伏电子股份」
    sha:<名字 bigram>             名字二元组（可捕获中间字不同的错写）
    ph:<号码数字尾11位>            共享号码（不同来源同一企业的铁证）
    em:<邮箱>  /  dom:<非免费域名>  共享邮箱 / 共享企业域名
    web:<官网主域名>               共享官网域名
块容量上限 BLOCK_CAP：某个块成员过多说明该键过于泛用（如「科技」bigram、
qq.com 邮箱），直接整块跳过不参与——漏合几个可后续人工补，误合则不可逆。

口径统一：单组合并逻辑只有 merge_group 一份实现，
DataPoolManager._merge_records 与 dedup_engine 后处理 pass 都调它，
避免「两处合并规则不同 → 同一批数据两种结果」。
"""
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

# 与清洗侧/数据池一致的联系方式列表字段
LIST_FIELDS = ("phones", "mobiles", "landlines", "emails", "wechats")
# 块成员上限：超过说明该阻塞键过于泛用，整块跳过
BLOCK_CAP = 25
# 免费邮箱域名不参与「企业域名」阻塞（否则会把所有 qq.com 公司塞进一块）
_FREE_MAIL_DOMAINS = frozenset((
    "qq.com", "163.com", "126.com", "sina.com", "sina.cn", "sohu.com", "foxmail.com",
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "live.com", "aol.com",
    "icloud.com", "139.com", "aliyun.com", "yeah.net", "sohu.com", "21cn.com",
    "189.cn", "vip.qq.com", "vip.163.com",
))

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


# ─────────────────────────────────────────────────────────────────────────────
#  单组合并（唯一口径实现）
# ─────────────────────────────────────────────────────────────────────────────

def _digits(phone: Any) -> str:
    """号码归一：只留数字，带国家码的中国手机号取末 11 位。"""
    d = re.sub(r"\D", "", str(phone or ""))
    if len(d) > 11 and (d.startswith("86") or d.startswith("0086")):
        d = d[-11:]
    return d


def merge_group(group: List[dict], base: Optional[dict] = None) -> dict:
    """把同一实体的多条记录合并成一条（联系方式取并集，绝不丢号）。

    规则（与历史实现完全一致，只做两处缺陷修正）：
      · 基底 = 联系方式最多的一条（字段完整性优先）
      · phones/mobiles 共用一个去重集：同一号码同时出现在两列时只留 phones
      · landlines 独立去重集（修：旧 dedup 后处理把固话也塞进 phones 的
        去重集，导致「固话与手机号数字相同」时固话被吞）
      · source 用「 / 」拼接全部来源（修：旧后处理写成 phantom 键 sources，
        下游统计/导出只读 source，等于来源被丢）
      · is_domestic：**并集证据**按 record_region 的多信号加权判（与搜索过滤、
        清洗分区键、导出范围完全同一个口径）；只有并集后仍然零证据时，才退到
        组内显式值的多数投票——历史缺陷是反过来：投票决定 is_domestic/region，
        而 province 用证据判据派生，同一个函数里两套口径，实测合并结果落成
        「region=dom 但 province=德国」，勾「国内」+「德国」两条筛选互相抵消
      · 输出永远带显式 is_domestic 与 region 键（读侧分区过滤的数据基础），
        并打上当前判据版本标记（否则下一轮同步把整表重判一遍）
      · industry 这类**分区键**按组内多数票取（票数→来源新鲜度→字面序定胜），
        不按组内顺序挑第一个：分区键决定筛选与统计口径，挑法必须与记录顺序无关
        才能重放（哨兵「未分类」等同缺失，不投票，留给 enrich 按并集证据派生）
    """
    group = [r for r in group if isinstance(r, dict)]
    if not group:
        return {}
    if base is None:
        base = max(group, key=_contact_richness)

    phones: List[str] = []
    mobiles: List[str] = []
    landlines: List[str] = []
    emails: List[str] = []
    wechats: List[str] = []
    contacts: List[dict] = []
    sources: List[str] = []

    seen_phone: set = set()
    seen_landline: set = set()
    seen_email: set = set()
    seen_wechat: set = set()
    seen_contact: set = set()

    for rec in group:
        for p in rec.get("phones") or []:
            if p and p not in seen_phone:
                seen_phone.add(p)
                phones.append(p)
        for m in rec.get("mobiles") or []:
            if m and m not in seen_phone:
                seen_phone.add(m)
                mobiles.append(m)
        for l in rec.get("landlines") or []:
            if l and l not in seen_landline:
                seen_landline.add(l)
                landlines.append(l)
        for e in rec.get("emails") or []:
            el = str(e).strip().lower()
            if el and el not in seen_email:
                seen_email.add(el)
                emails.append(e)
        for w in rec.get("wechats") or []:
            if w and w not in seen_wechat:
                seen_wechat.add(w)
                wechats.append(w)
        for c in rec.get("contacts") or []:
            if not isinstance(c, dict):
                continue
            ckey = f"{c.get('name', '')}|{c.get('title', '')}"
            if ckey not in seen_contact:
                seen_contact.add(ckey)
                contacts.append(c)
        src = str(rec.get("source", "") or "").strip()
        if src and src not in sources:
            sources.append(src)

    times = [str(r.get("source_time", "") or "") for r in group if r.get("source_time")]
    latest_time = max(times) if times else ""

    dom_votes = sum(1 for r in group if r.get("is_domestic") is True)
    intl_votes = sum(1 for r in group if r.get("is_domestic") is False)

    company = base.get("company", "") or _first_nonempty(group, "company")
    out = {
        "company": company,
        "company_norm": base.get("company_norm") or _first_nonempty(group, "company_norm"),
        "contacts": contacts,
        "phones": phones,
        "mobiles": mobiles,
        "landlines": landlines,
        "emails": emails,
        "wechats": wechats,
        "address": _first_nonempty(group, "address"),
        "products": _first_nonempty(group, "products"),
        "website": _first_nonempty(group, "website"),
        "industry": _majority_value(group, "industry"),
        "source": " / ".join(sources),
        "source_time": latest_time,
        "source_user": base.get("source_user", "") or _first_nonempty(group, "source_user"),
        "country": _first_nonempty(group, "country"),
    }
    # 平铺单值字段（导出/客户库映射用）：有则沿用，避免覆盖成空
    for key in ("contact", "title", "phone", "email", "landline"):
        val = _first_nonempty(group, key)
        if val:
            out[key] = val
    # ── 属地：在**并集之后**判（这一行必须在 out 建好之后，早判等于漏证据）──
    # 证据判据是唯一口径；只有并集后仍然零证据（没地址、没号码、没国名、没域名后缀），
    # 才用组内显式值的多数投票兜底——此时投票比「按公司名语言瞎猜」信息量大。
    strength = _domestic_signal_strength(out)
    if strength is None:
        is_domestic = (dom_votes >= intl_votes) if (dom_votes or intl_votes) else True
    elif strength == (0, 0):
        is_domestic = dom_votes >= intl_votes
    else:
        is_domestic = strength[0] >= strength[1]
    # 分区键与质量分必须跟着合并结果重算：组内并集后联系方式比任何单条都全，
    # 沿用某条的旧值会低估质量分；region 沿用旧值则可能与属地判据矛盾。
    out["is_domestic"] = is_domestic
    out["region"] = "dom" if is_domestic else "intl"
    out["province"] = (_derive_province(out, out["region"])
                       or _first_nonempty(group, "province"))
    out["quality"] = _quality(out)
    # 版本标记：这一条已经按当前判据在并集证据上判过，下一轮读取才允许走快捷路径
    # （不打标就会让每次同步都把整表重判一遍，实测 8.7 万条白跑几十秒）
    out["region_rule"] = _region_rule_version()
    out["province_rule"] = _partition_rule_version()
    return out


def _derive_province(rec: dict, region: str = "") -> str:
    try:
        from ingest_cleaning import derive_province
        return derive_province(rec, region or None)
    except Exception:
        return ""


def _domestic_signal_strength(rec: dict):
    """并集证据的 (境内得分, 境外得分)；判据不可用时返回 None（调用方退回投票）。"""
    try:
        from data_pool import _domestic_signals
        return _domestic_signals(rec)
    except Exception:
        return None


def _region_rule_version() -> str:
    try:
        from data_pool import REGION_RULE_VERSION
        return REGION_RULE_VERSION
    except Exception:
        return ""


def _partition_rule_version() -> str:
    try:
        from ingest_cleaning import PARTITION_RULE_VERSION
        return PARTITION_RULE_VERSION
    except Exception:
        return ""


def _quality(rec: dict) -> int:
    try:
        from data_pool import compute_quality_score
        return compute_quality_score(rec)
    except Exception:
        return 0


def _contact_richness(rec: dict) -> int:
    """联系方式总量（选基底用：信息最丰富的一条优先）。"""
    total = 0
    for key in ("phones", "mobiles", "landlines", "emails", "wechats"):
        v = rec.get(key)
        total += len(v) if isinstance(v, (list, tuple, set)) else (1 if v else 0)
    return total


def _first_nonempty(records: List[dict], key: str) -> str:
    for rec in records:
        v = str(rec.get(key, "") or "").strip()
        if v:
            return v
    return ""


def _unknown_industry() -> str:
    """行业哨兵值（Unknown 成员）：等于「没有标签」，不参与投票。"""
    try:
        from ingest_cleaning import INDUSTRY_UNKNOWN
        return INDUSTRY_UNKNOWN
    except Exception:
        return "未分类"


def _majority_value(records: List[dict], key: str, skip: tuple = ()) -> str:
    """受控词表字段（分区键）按组内多数取，票数相同按「更新鲜的来源 → 字面序」定胜。

    自由文本用 _first_nonempty 就够（先有哪个就用哪个），但 industry 是**分区键**，
    决定筛选和统计口径：按组内顺序挑，等于让「哪个展会文件先下载完」决定一家公司
    算哪个行业（真实规模实测两次全量重建有 11 组行业漂移）。多数票 + 确定性
    tie-break 才是可重放的：同一批 Bronze 数据在任何一次重建里给出同一张总表。
    哨兵值（未分类）按既定原则等同缺失——不投票、不当选中，留空交给下游 enrich
    按并集证据重新派生。
    """
    unknown = _unknown_industry()
    skip = tuple(skip) + ("", unknown)
    buckets: Dict[str, List] = {}
    for rec in records:
        v = str(rec.get(key, "") or "").strip()
        if v in skip:
            continue
        b = buckets.setdefault(v, [0, ""])
        b[0] += 1
        t = str(rec.get("source_time", "") or "")
        if t > b[1]:
            b[1] = t
    if not buckets:
        return ""
    # 字面序先排一遍再取 max：max 返回第一个最大值，于是票数与新鲜度都打平时
    # 稳定落在字面序最小的那个值上（结果与组内记录顺序完全无关）
    ranked = sorted(buckets.items(), key=lambda kv: kv[0])
    return max(ranked, key=lambda kv: (kv[1][0], kv[1][1]))[0]


# ─────────────────────────────────────────────────────────────────────────────
#  阻塞键
# ─────────────────────────────────────────────────────────────────────────────

def blocking_keys(rec: dict) -> List[str]:
    """产出一条记录的全部阻塞键（同键即可能重复，进同一块）。

    注意：键必须是「廉价可得 + 高判别度」的，泛用键（如名字里的「科技」）
    会形成巨型块，由 BLOCK_CAP 在分桶阶段整块丢弃，不进两两比较。
    """
    keys: List[str] = []
    norm = str(rec.get("company_norm") or "").strip()
    if not norm:
        norm = _norm_name(str(rec.get("company") or ""))
    if norm:
        keys.append("norm:" + norm)
        if len(norm) >= 2:
            keys.append("pre:%s:%d" % (norm[:2], len(norm) // 4))
        for bg in _name_bigrams(norm):
            keys.append("sha:" + bg)
    for field in ("phones", "mobiles", "landlines"):
        for p in rec.get(field) or []:
            d = _digits(p)
            if len(d) >= 7:
                keys.append("ph:" + d[-11:])
    for e in rec.get("emails") or []:
        s = str(e).strip().lower()
        if "@" not in s:
            continue
        keys.append("em:" + s)
        dom = s.split("@", 1)[1]
        if dom not in _FREE_MAIL_DOMAINS:
            keys.append("dom:" + dom)
    web = _domain_of(rec.get("website") or "")
    if web:
        keys.append("web:" + web)
    return keys


def _norm_name(name: str) -> str:
    try:
        from data_pool import normalize_company_name
        return normalize_company_name(name)
    except Exception:
        return (name or "").strip().lower()


def _name_bigrams(norm: str) -> Tuple[str, ...]:
    """公司名二元组（仅中文部分，用于捕获错写/多字少字）。"""
    chars = _CJK_RE.findall(norm)
    if len(chars) < 2:
        return ()
    return tuple(chars[i] + chars[i + 1] for i in range(len(chars) - 1))


def _domain_of(url: str) -> str:
    s = str(url or "").strip().lower()
    if not s:
        return ""
    s = re.sub(r"^[a-z]+://", "", s)
    s = s.split("/", 1)[0]
    if s.startswith("www."):
        s = s[4:]
    return s if "." in s else ""


# ─────────────────────────────────────────────────────────────────────────────
#  分块 → 桶内比较 → 聚组 → 合并
# ─────────────────────────────────────────────────────────────────────────────

def build_blocks(records: List[dict]) -> Dict[str, List[int]]:
    """阻塞分桶：阻塞键 → 记录序号列表。

    同一条记录可能产出重复键（如 "+1-306-206-1055" 与 "+13062061055" 归一后
    是同一个号码），必须按键去重，否则同一序号会在块内出现两次，
    candidate_pairs 就会产出 (i, i) 自配对——白白比较还算错命中数。
    """
    blocks: Dict[str, List[int]] = {}
    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            continue
        for key in dict.fromkeys(blocking_keys(rec)):
            lst = blocks.get(key)
            if lst is None:
                blocks[key] = [i]
            elif len(lst) <= BLOCK_CAP:
                lst.append(i)
    return blocks


def candidate_pairs(blocks: Dict[str, List[int]]) -> set:
    """从分桶结果取「同块不同条」的候选对（去重、有序化 i<j）。"""
    pairs: set = set()
    for lst in blocks.values():
        n = len(lst)
        if n < 2 or n > BLOCK_CAP:
            continue  # 泛用键大块：跳过（宁可漏合不误合）
        for a in range(n - 1):
            ia = lst[a]
            for b in range(a + 1, n):
                ib = lst[b]
                if ia == ib:
                    continue
                pairs.add((ia, ib) if ia < ib else (ib, ia))
    return pairs


def find_duplicate_pairs(records: List[dict], threshold: float = 0.85,
                         max_pairs: int = 200000) -> List[Tuple[int, int, float]]:
    """阻塞后的桶内两两判定，返回 [(i, j, score)]（按分数降序）。

    与旧 O(n²) 实现的区别只在于「比较哪些对」，打分函数完全复用
    dedup_engine.compute_duplicate_score，因此判定阈值语义不变。
    """
    from dedup_engine import compute_duplicate_score
    blocks = build_blocks(records)
    pairs = candidate_pairs(blocks)
    out: List[Tuple[int, int, float]] = []
    for i, j in pairs:
        score, _signals = compute_duplicate_score(records[i], records[j])
        if score >= threshold:
            out.append((i, j, score))
            if len(out) >= max_pairs:
                break
    out.sort(key=lambda x: x[2], reverse=True)
    return out


def merge_duplicates_fast(records: List[dict], threshold: float = 0.85) -> List[dict]:
    """线性复杂度的模糊去重：分桶 → 桶内打分 → 并查集聚组 → 合并。"""
    if not records or len(records) < 2:
        return list(records or [])
    pairs = find_duplicate_pairs(records, threshold=threshold)
    if not pairs:
        return list(records)

    parent = list(range(len(records)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, j, _s in pairs:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    groups: Dict[int, List[int]] = {}
    for idx in range(len(records)):
        groups.setdefault(find(idx), []).append(idx)

    merged: List[dict] = []
    for members in groups.values():
        if len(members) == 1:
            merged.append(records[members[0]])
        else:
            group = [records[k] for k in members]
            merged.append(merge_group(group))
    return merged


def dedup_exact(records: List[dict]) -> List[dict]:
    """按 company_norm 精确归并（主链：万级以内一次哈希即可完成）。"""
    groups: Dict[str, List[dict]] = {}
    order: List[str] = []
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        norm = str(rec.get("company_norm") or "").strip() or _norm_name(
            str(rec.get("company") or ""))
        if not norm:
            continue
        if norm not in groups:
            groups[norm] = []
            order.append(norm)
        groups[norm].append(rec)
    return [merge_group(groups[norm]) for norm in order]
