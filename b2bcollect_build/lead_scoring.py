# -*- coding: utf-8 -*-
"""lead_scoring.py —— 线索评分与分层导出（功能 B + K）：一张能自解释的「先打谁」表。

对标的做法（Clay / Apollo / ZoomInfo 这类 outbound 平台在导出环节的共同形状）：
瀑布补齐之后**不是**直接把表交给销售，而是先出一个 score 列，把一张表切成
「现在就打 / 先养着 / 这轮别碰」三档。它存在的理由很实际：外呼产能是有限的，
一份 5000 行的表和一份「前 800 行是 5000 行里的全部可用线索」的表，
销售的实际产出差好几倍；而分档一旦被质疑（一定会被质疑），
必须能指着某一列说清「凭什么它排前面」——所以**评分依据那一列是功能，不是装饰**。

【三条实现纪律，都是从本仓库既有账目纪律推出来的，不是照抄概念】
  1) 可信度**只读** field_evidence 的命中层级，本模块绝不自己再判一遍「这个号像不像真的」。
     判据写两份迟早各叫各的，而且第二份永远比第一份松（不然它显不出存在感）。
  2) 分层不许只看总分：总分是「这行信息多不多」，可信层是「这个号能不能打」。
     一个 55 分（平台自带、未经二次核验）的号配上很满的其它列，刷到 70 分很容易，
     但它和工商核验过的号打通率不是一回事——所以「立即跟进」除了分数还**必须过可信闸**。
  3) 不改客户的入库模板列（那是接口契约，见 yzd_client.IMPORT_HEADERS 的 9 列）。
     本功能的产物是一份**附加报表**，用「企业名称」和清洗表对上，
     爆炸半径等于零：不勾它就一行代码都不执行，勾了也不会动任何既有产出。

【刻意不做的两件事】
  · 不做机器学习/回归拟合的评分：没有标注数据（谁打通过、谁成了单）就学不出权重，
    硬编一个「模型」只会给用户一个看起来更权威、实际更没根据的数字。
    今天的权重全部写在明面上、写进「口径说明」那张表里，用户可以自己改着试。
  · 不把「同主体多行」合成一行后就丢掉行数：同一主体在本表里有几行、
    分别来自哪几个平台，是用户复核时必须能看见的东西（合并本身也是按主体键做的，
    和瀑布第 1 级共用同一份实现，不分两口径）。

运行：纯标准库 + 同级三个模块；openpyxl 只在真要写表时才 import（离线用例可只算分不落盘）。
"""
from __future__ import annotations

import enrich_waterfall as _wf
import field_evidence as _fe
import website_contact as _wc

__all__ = [
    "LEAD_HEADERS", "CHANGE_HEADERS", "TIER_WORK", "TIER_NURTURE", "TIER_IDLE",
    "WEIGHTS", "score_record", "aggregate_leads", "lead_rows", "write_lead_report",
]

# ── 分层名与门槛（单一来源：日志、报表、界面文案都从这里取）────────────
TIER_WORK = "立即跟进"
TIER_NURTURE = "先培育"
TIER_IDLE = "本轮别碰"
# 总分门槛：70/45 这两条线是**按权重复算出来的**，不是拍的——
# 见「口径说明」表里的推导行：70 ≈ 有手机级可信号 + 常见字段基本齐；
# 45 ≈ 有联系方式但可信层只有「平台自带」，或信息明显残缺。
WORK_MIN = 70
NURTURE_MIN = 45
# 「立即跟进」的硬闸：可信分低于这一档一律降为「先培育」。
# 取值就是瀑布的命中即停闸（官网直采及以上），两处共用一个数才自洽：
# 既然补全环节认为「到这层就不用再问了」，评分环节就该认它能直接打。
WORK_CONF_MIN = _fe.CONFIDENT_MIN

# 三个维度各自的满分（加起来 100，且每一分都能在「评分依据」里被指认）
WEIGHTS = {
    "confidence": 45,    # 联系方式可信度（来自命中层级）
    "completeness": 35,  # 外呼用得上的字段齐不齐
    "freshness": 20,     # 数据有多新（含 TTL 过期折扣）
}
# 完整度只数**外呼真用得上**的列：不数 memberId/详情URL 这类过程列，
# 那会把「采集流水」误当成「线索质量」。权重合计 35。
COMPLETENESS_FIELDS = (
    ("手机", 8), ("电话", 6), ("邮箱", 6), ("联系人", 6),
    ("所在区域", 4), ("主营产品", 3), ("公司网址", 2), ("地址", 0),
)
# 新鲜度阶梯：本工具的历史数据最远也就几个月，阶梯放在 30/90/180/365 天
FRESH_STEPS = ((30, 20), (90, 15), (180, 10), (365, 5))
FRESH_UNKNOWN = 0        # 采集时间认不出来：按「不知道有多新」给 0 分（宁可少不可吹）
STALE_FACTOR = 0.6       # 已过字段 TTL 的可信分折扣：过期数据不能按新数据卖
# 分层的固定展示顺序（报表、日志、看板都按这个次序）。以前这个名字叫
# FRESHNESS_ORDER，但它装的是分层而不是新鲜度——名字和内容不符的表，
# 下一个人会照着名字去加内容（把 TTL 阶梯加进来），所以直接改名而不是加注释。
TIER_ORDER = (TIER_WORK, TIER_NURTURE, TIER_IDLE)


# ── 单行评分 ────────────────────────────────────────────────────────
def _best_contact(rec):
    """这一行最能打的那一档联系方式：返回 (字段, 层级slug, 层级中文名, 原始可信分)。

    取最高而不是取「第一个非空」：外呼只要一个打得通的号，行里同时有工商核验的
    手机和平台自带的座机时，这行的可用性就该按手机那一档算。
    """
    best = ("", "", "", 0)
    for field in _fe.CONTACT_FIELDS:
        if not str((rec or {}).get(field) or "").strip():
            continue
        got = _fe.parse_evidence(rec).get(field)
        slug = (got or ("", ""))[1] if got else ""
        score = _fe.field_confidence(rec, field)
        if score > best[3]:
            best = (field, slug, _fe.TIER_LABEL.get(slug, _fe.TIER_LABEL["source"]), score)
    return best


def score_record(rec, at_field="采集时间", today=None):
    """给一行（或一行聚合后的线索）打分：返回 (总分, 分层, 评分依据列表)。

    依据列表逐条写「加了哪一分、为什么」，包括**没加的那部分**（缺什么、为什么过期打折）：
    只报喜的评分表在用户那里活不过第二次复盘。
    """
    rec = rec or {}
    why = []
    field, slug, label, raw_conf = _best_contact(rec)
    conf_w = int(WEIGHTS["confidence"])

    age = _fe.field_age_days(rec.get(at_field), today)
    stale = False
    # 「有多新」看**采集时间**（这行数据是什么时候来的），
    # 「过没过期」看**最后核验时间**（这个号最近一次被证实还在用是什么时候）——
    # 两条必须分开，否则一个被重验过三次的老行会被判过期，而一个刚被搬来的错号
    # 会因为采集时间是今天而被判新（pick_targets 那边用的也是同一对判据）。
    verified = _fe.verified_at(rec, at_field)
    if field:
        stale = _fe.is_stale(field, slug, verified, today=today)
    conf = raw_conf
    if stale:
        conf = int(round(raw_conf * STALE_FACTOR))
    if not field:
        why.append(f"联系方式：三个字段全空（0/{conf_w}）")
    else:
        why.append(f"联系方式：{field} 来自「{label}」原始可信 {raw_conf} 分"
                   f" → {conf} × {conf_w}/100 ＝ +{round(conf * conf_w / 100)}"
                   + (f"（已过 TTL，按 {int(STALE_FACTOR * 100)}% 计）" if stale else ""))
    s = round(conf * conf_w / 100)

    comp_w = 0
    comp_max = int(WEIGHTS["completeness"])
    blank = []
    for name, pts in COMPLETENESS_FIELDS:
        if not pts:
            continue
        if str(rec.get(name) or "").strip() or (
                name == "公司网址" and _wc.pick_site(rec)):
            comp_w += pts
        else:
            blank.append(name)
    why.append(f"字段齐备度：+{comp_w}/{comp_max}"
               + (f"（缺 {'、'.join(blank)}）" if blank else "（齐全）"))
    s += comp_w

    if age is None:
        fresh = FRESH_UNKNOWN
        why.append(f"新鲜度：{at_field} 认不出来，按未知计 +{fresh}/{WEIGHTS['freshness']}")
    else:
        fresh = 0
        for days, pts in FRESH_STEPS:
            if age <= days:
                fresh = pts
                break
        why.append(f"新鲜度：距今 {age} 天 +{fresh}/{WEIGHTS['freshness']}")
    s += fresh

    score = max(0, min(100, int(s)))
    tier = _tier_of(score, conf, stale)
    if tier == TIER_NURTURE and field and conf < WORK_CONF_MIN:
        # 把「为什么明明 88 分却只算先培育」写在案上，而不是让用户自己猜
        why.append(f"闸：可信层「{label}」低于立即跟进要求（{WORK_CONF_MIN}），"
                   f"判为先培育——号码没经过二次核验，直接打不划算")
    if tier == TIER_NURTURE and stale:
        why.append("闸：这一档联系方式已过 TTL，先重验再打")
    return score, tier, why


def _tier_of(score, conf, stale):
    if score >= WORK_MIN and conf >= WORK_CONF_MIN and not stale:
        return TIER_WORK
    if score >= NURTURE_MIN:
        return TIER_NURTURE
    return TIER_IDLE


def missing_hint(rec):
    """这一条线索还缺什么（用于报表「待补缺口」列，和排队用的是同一份判据）。"""
    got = _fe.missing_fields(rec)
    return "、".join(got) if got else "—"


def freshness_text(rec, at_field="采集时间", today=None):
    """新鲜度的文字版：距今 N 天 + 是否已过 TTL（过期看「最后核验时间」）。"""
    field, slug, _label, _c = _best_contact(rec)
    age = _fe.field_age_days(rec.get(at_field), today)
    if not field:
        return "无联系方式"
    if _fe.is_stale(field, slug, _fe.verified_at(rec, at_field), today=today):
        return (f"{age} 天（已过期）" if age is not None else "已过期（核验时间未知）")
    return f"{age} 天" if age is not None else "采集时间未知"


# ── 同主体聚合：一张表里同一家的多行合成一条线索 ──────────────────────
def aggregate_leads(records, log=None):
    """按**同主体键**（与瀑布第 1 级同一份实现）把多行合成一条线索。

    合并规矩和采集侧那条硬教训对齐：电话/手机/邮箱取**并集**（英文逗号去重），
    可信层级按「哪个字段赢的是哪一行」逐字段带过来——值并进来却把别行的层级抄过去，
    就会造出「邮箱:工商核验」而那个邮箱其实来自平台自带的自相矛盾的格子。
    刻意**不**走 customer_library_cleaner.merge_records：那条路的 SOURCE_FIELDS 是
    「首个非空」语义，且服务的是客户入库模板；本报表要的是「逐字段最高可信」，
    两者判据不同，硬共用一份反而会把入库那条链改坏。
    """
    log = log or (lambda m: None)
    buckets = {}
    order = []
    skipped = 0
    for rec in records or []:
        if not isinstance(rec, dict):
            skipped += 1
            continue
        name = str(rec.get("公司名称") or rec.get("公司名") or "").strip()
        if not name:
            skipped += 1
            continue
        key = _wf.company_key(rec) or ("raw:" + name)
        item = buckets.get(key)
        if item is None:
            item = buckets[key] = {
                "公司名称": name, "rec": {k: "" for k in _fe.CONTACT_FIELDS},
                "_rows": 0, "_sources": [], "_kw": [], "_urls": "",
                "_contact": {}, "_at": "", "_vat": "",
            }
            order.append(key)
        item["_rows"] += 1
        src = str(rec.get("数据来源") or "").strip()
        if src and src not in item["_sources"]:
            item["_sources"].append(src)
        kw = str(rec.get("关键词") or "").strip()
        if kw and kw not in item["_kw"] and len(item["_kw"]) < 4:
            item["_kw"].append(kw)
        # 「最后核验时间」用同一主体各行里**最新**的那个采集时间：
        # 老掉的那一行不能把整家的新鲜度拖下去（那是重复采集的常态，不是数据过期）
        # 比较走 _wf.time_newer（和下面 _vat 同一份实现）：同一个函数里两份
        # 「哪个时间更新」的判据迟早算出两个答案，这里以前就自己抄了一份日期解析。
        at = str(rec.get("采集时间") or "").strip()
        if _wf.time_newer(at, item["_at"]):
            item["_at"] = at
        # 核验时间**只认那一列**（不退回采集时间）：退回值由 _fe.verified_at 统一处理，
        # 在这里混进来就会把「采集时间」写成「核验时间」，报表上看着像刚验过——那是捏造。
        vat = str(rec.get(_fe.VERIFIED_COL) or "").strip()
        if _wf.time_newer(vat, item["_vat"]):
            item["_vat"] = vat
        for col in ("公司网址", "详情URL", "地址", "主营产品", "所在区域", "联系人",
                    "岗位", "经营模式"):
            val = str(rec.get(col) or "").strip()
            if val and not str(item["rec"].get(col) or "").strip():
                item["rec"][col] = val
        for field in _fe.CONTACT_FIELDS:
            for value in [x for x in _wc._SPLIT_RE.split(str(rec.get(field) or "")) if x]:
                pool = item["_contact"].setdefault(field, [])
                vk = _wc._value_key(value)
                if vk and all(vk != _wc._value_key(x) for x in pool):
                    pool.append(value)
            # 层级逐字段取最高可信那一行的（并带它的出处），低可信的不许盖上来
            got = _fe.parse_evidence(rec).get(field)
            if not got or not str(rec.get(field) or "").strip():
                continue
            keep = item["rec"].get("_tier_" + field)
            if not keep or _fe.TIER_SCORE.get(got[1], 0) > _fe.TIER_SCORE.get(keep[1], 0):
                item["rec"]["_tier_" + field] = (got[0], got[1],
                                                 _fe.parse_proof(rec).get(field, ""))
    leads = []
    for key in order:
        item = buckets[key]
        rec = dict(item["rec"])
        # 公司名取这一家**首次出现时的那一行写法**（归并键只用于认亲，不能当展示名：
        # 它是剥掉「有限公司」这类尾缀后的形态，拿去和客户表对照会对不上号）
        rec["公司名称"] = item["公司名称"]
        for field in _fe.CONTACT_FIELDS:
            pool = item["_contact"].get(field) or []
            rec[field] = _wc.merge_values("", pool) if pool else ""
            got = rec.pop("_tier_" + field, None)
            # 只有值真的在这条线索上才登记层级（值没并进来却带着一层证据，
            # 就是「邮箱:工商核验」而邮箱为空那种自相矛盾的格子）
            if got and rec[field] and got[1]:
                _fe.set_field_evidence(rec, field, got[1], got[2] or got[0])
        rec["采集时间"] = item["_at"]
        # 只有「真的记过核验时间、且比最新采集时间还新」才带这一列；
        # 其余情况留给 _fe.verified_at 的采集时间退回，两者等价但不在这里造出一个假格子
        if _wf.time_newer(item["_vat"], item["_at"]):
            rec[_fe.VERIFIED_COL] = item["_vat"]
        rec["数据来源"] = ",".join(item["_sources"])
        rec["关键词"] = "、".join(item["_kw"])
        leads.append({"rec": rec, "rows": item["_rows"], "key": key})
    if skipped:
        # 「为什么报表比原始表少几行」必须自己说清楚，否则用户只会觉得报表不可信
        log(f"ℹ 线索分层：{skipped} 行没有公司名（或不是记录形状），按主体归并不了，"
            f"没进本报表——原始表里它们一行没少")
    return leads


# ── 报表数据（不落盘也能算，离线用例吃这一份）────────────────────────
def lead_rows(records, log=None, today=None):
    """→ 报表行列表（已按线索分降序、同分按同主体行数降序）。每行是 dict。

    `today` 只给离线用例钉基准日用（生产一律留 None＝按真实当天）：不把这个数透进来，
    回归套件的档位判定就会跟着开发机的日历漂移——今天绿、下个月红的门，
    和没有门等价，而且更难查（没人会去怀疑一句断言变了）。
    新鲜度文字与评分必须吃同一个 `today`：两处各判就会出现「分数说过期、文字说 0 天」。
    """
    out = []
    for lead in aggregate_leads(records, log=log):
        rec = lead["rec"]
        score, tier, why = score_record(rec, today=today)
        field, slug, label, raw_conf = _best_contact(rec)
        out.append({
            "企业名称": rec.get("公司名称") or "",
            "分层": tier,
            "线索分": score,
            "优先联系": (f"{field}（{label}）" if field else "—"),
            "手机": rec.get("手机") or "",
            "电话": rec.get("电话") or "",
            "邮箱": rec.get("邮箱") or "",
            "联系人": rec.get("联系人") or "",
            "岗位": rec.get("岗位") or "",
            "官网": _wc.pick_site(rec) or "",
            "所在区域": rec.get("所在区域") or "",
            "主营产品": rec.get("主营产品") or "",
            "数据来源": rec.get("数据来源") or "",
            "关键词": rec.get("关键词") or "",
            "命中层级": rec.get(_fe.EVIDENCE_COL) or "",
            "补全证据": rec.get(_fe.PROOF_COL) or "",
            "待补缺口": missing_hint(rec),
            "数据新鲜度": freshness_text(rec, today=today),
            "采集时间": rec.get("采集时间") or "",
            "同主体行数": lead["rows"],
            "评分依据": "；".join(why),
            "_score": score, "_tier": tier, "_conf": raw_conf,
        })
    out.sort(key=lambda r: (-int(r["_score"]), -int(r["同主体行数"]), r["企业名称"]))
    return out


def tier_counts(rows):
    """分档计数（日志与看板共用这一份口径，别再数一遍）。"""
    got = {t: 0 for t in TIER_ORDER}
    for r in rows or []:
        got[r.get("_tier") or r.get("分层") or TIER_IDLE] = \
            got.get(r.get("_tier") or r.get("分层") or TIER_IDLE, 0) + 1
    return got


# 报表列顺序（表头即列名，和行 dict 的键一一对应，避免两处各排一次）
LEAD_HEADERS = [
    "序号", "企业名称", "分层", "线索分", "优先联系", "手机", "电话", "邮箱",
    "联系人", "岗位", "官网", "所在区域", "主营产品", "数据来源", "关键词",
    "命中层级", "补全证据", "待补缺口", "数据新鲜度", "采集时间", "同主体行数", "评分依据",
]

CHANGE_HEADERS = ["序号", "企业名称", "字段", "变化", "旧值", "新值", "命中层级", "补全证据"]

HEALTH_HEADERS = ["序号", "通道", "本轮行数", "本轮家数", "有联系方式", "填充率%",
                  "平均线索分", "立即跟进", "零产出连续轮次", "红牌", "最近一轮", "说明"]

# ── 口径说明（把权重和门槛摊开写进报表，用户不必读代码就能复核）────────
def caliber_lines():
    lines = [
        "本表是**附加报表**：客户入库模板（9 列接口契约）与清洗表列一律未改动，"
        "按「企业名称」可与清洗表对照使用。",
        f"线索分 = 联系方式可信度({WEIGHTS['confidence']}) + 字段齐备度({WEIGHTS['completeness']})"
        f" + 新鲜度({WEIGHTS['freshness']})，满分 100。",
        "可信度**只读**「补全层级」这一列，本工具不在这里二次判断号码真假；"
        "层级由补全现场登记，含义见下面每一档的说明。",
    ]
    for slug, label, score, _cost, _v, doc in _fe.TIERS:
        lines.append(f"　层级「{label}」可信 {score} 分：{doc}")
    lines.append(f"分层门槛：≥{WORK_MIN} 分且可信层 ≥{WORK_CONF_MIN}（未经 TTL 过期）→ {TIER_WORK}；"
                 f"≥{NURTURE_MIN} 分 → {TIER_NURTURE}；其余 → {TIER_IDLE}。")
    lines.append(f"过期折扣：已过字段 TTL 的可信分按 {int(STALE_FACTOR * 100)}% 计，"
                 f"且一律不得判为{TIER_WORK}（TTL：手机 {_fe.TTL_DAYS['手机']} 天、"
                 f"电话 {_fe.TTL_DAYS['电话']} 天、邮箱 {_fe.TTL_DAYS['邮箱']} 天，"
                 f"工商核验 ×{_fe.TTL_TIER_FACTOR['registry']}、平台自带 ×{_fe.TTL_TIER_FACTOR['source']}）。")
    lines.append("同主体多行会合成一条线索：电话/手机/邮箱取并集（英文逗号去重），"
                 "每个字段的可信层取「该行集合里最高那一档」，出处一并带过来。")
    lines.append("采集时间认不出来的行按「未知」给 0 新鲜分（宁可少排不可误排为可立即外呼）。")
    return lines


# ── 落盘 ─────────────────────────────────────────────────────────
def write_lead_report(path, rows, changes=None, health=None, log=None):
    """把分层表（+可选的字段变更表、通道健康表、口径说明）写成一个 xlsx。

    返回写成的 sheet 名列表；任何异常都由调用方兜住并**如实报出**——
    报表没落成文件和报表算错数，对用户是同一件事。
    """
    log = log or (lambda m: None)
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "线索分层"
    ws.append(LEAD_HEADERS)
    for i, r in enumerate(rows or [], 1):
        ws.append([i] + [r.get(h, "") for h in LEAD_HEADERS[1:]])
    written = [ws.title]
    if changes:
        cs = wb.create_sheet("字段变更")
        cs.append(CHANGE_HEADERS)
        for i, c in enumerate(changes, 1):
            cs.append([i, c.get("企业名称", ""), c.get("字段", ""), c.get("变化", ""),
                       c.get("旧值", ""), c.get("新值", ""),
                       c.get("命中层级", ""), c.get("补全证据", "")])
        written.append(cs.title)
    if health:
        hs = wb.create_sheet("通道健康")
        hs.append(HEALTH_HEADERS)
        for i, h in enumerate(health, 1):
            hs.append([i] + [h.get(k, "") for k in HEALTH_HEADERS[1:]])
        written.append(hs.title)
    note = wb.create_sheet("口径说明")
    note.append(["项", "说明"])
    for line in caliber_lines():
        note.append(["", line])
    written.append(note.title)
    wb.save(str(path))
    log(f"★ 线索分层报表已导出：{path}（{'、'.join(written)}）")
    return written
