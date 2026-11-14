# -*- coding: utf-8 -*-
"""field_evidence.py —— 联系方式的「命中层级 + 证据」单一真源（瀑布补全/线索评分/到期重验的共同前置）。

为什么先有它，再有瀑布（对标 Clay waterfall 的第一课）：
瀑布式补全的价值从来不在「多问几家」，而在**每一家问到的东西都能说清是从哪一层、
哪一页来的**。少了这一层，一张表里「13800000000」到底是企业自己在官网挂的、还是
同主体另一行抄过来的、还是域名推出来的，交付时完全分辨不了——而这三者的**外呼成功率
差好几倍**，用户排序、跟进、复盘全靠这个区别。所以：

  · 「命中层级」不是给人看的备注，它是**下一轮排队依据**（低可信的才值得重验）、
    也是**评分的唯一证据来源**（评分不许自己再判一遍值好不好，只读层级）；
  · 层级名、可信分、成本档只在本文件登记一次。谁要写列名、谁要按可信分排序，
    都从这里取——写两份迟早各叫各的（「官网回采」这一列已经验证过这条纪律：
    列名取自 website_contact.SITE_MARK，三处同源，从来没分叉过）。

刻意不做的两件事，写在案上而不是靠口头承诺：
  1) 不做「按邮箱规则猜号」（sales@ + 域名拼手机号那种）。那是**制造**数据而不是
     补全数据，一旦进表就和真号混在一起，事后无法分辨——本工具所有层级的铁律是
     「值必须来自某一页能被指认的原文」；
  2) 不做「静默改写已经有的值」。层级只追加、只升级，永不把高可信降级成低可信，
     也不把 8 个字的旧号码换成新号码（旧值可能是唯一的真号，只是这次没采到）。

运行：本文件只依赖标准库，可被任何一层（爬虫/会话/清洗/界面）直接导入而不引发循环。
"""
import re

# ── 导出列名（原始表用；入库模板是客户契约，这三列**不进模板**）──────────
EVIDENCE_COL = "补全层级"
PROOF_COL = "补全证据"
# 「最后一次真的复核过这一行的联系方式」的时间戳。它必须与「采集时间」分开：
# 到期重验（功能 I）如果拿采集时间当核验时间，一个每年被重新采一次的号就永远不会
# 过期；反过来，重验到同一个号却不刷新时间戳，那一行就会**每轮都被重问一次**
# ——那是把预算花在原地打转上，比不重验更糟。两个错都只有加一列才能同时避免。
VERIFIED_COL = "最后核验时间"

# 参与层级判定的联系方式字段（与 parallel_session.RAW_HEADERS 里的列名一致）
CONTACT_FIELDS = ("电话", "手机", "邮箱")

# ── 层级注册表：slug → (中文名, 可信分, 成本档, 是否已核验) ──────────────
# 可信分的口径是「这一层拿到的值，外呼/发邮件能打通的相对把握」，不是请求难度；
# 成本档才是请求难度（0 不花请求 / 1 裸 HTTP / 2 多页裸 HTTP / 3 要浏览器与配额）。
# 两者**分开登记**是刻意的：瀑布按成本升序跑（先免费的），评分按可信分排序
# （先靠谱的）。只登记一个数就会出现「为了省钱把低可信排到前面」这种自相矛盾。
TIERS = (
    ("carry",    "同主体已有",   70, 0, True,
     "同一主体（同公司名或同注册域名）的另一行已有该值，跨行复用，不花请求"),
    ("mailsite", "邮箱域名反查", 78, 1, True,
     "行内邮箱的域名就是企业官网域名，抓回后过了归属核验"),
    ("site",     "官网直采",     82, 1, True,
     "行自带官网字段直取，页面原文命中且归属证据成立"),
    ("brand",    "品牌域名推导", 62, 2, True,
     "由店铺子域/英文商号拼候选域名，抓回后过归属核验才采信"),
    ("registry", "工商核验",     90, 3, True,
     "企知道/风鸟/爱企查按主体查到的工商登记联系方式"),
    ("source",   "平台自带",     55, 0, True,
     "采集时该平台就直接给了这一列，未经本工具二次核验"),
)

TIER_LABEL = {slug: label for slug, label, _s, _c, _v, _d in TIERS}
TIER_SCORE = {slug: score for slug, _l, score, _c, _v, _d in TIERS}
TIER_COST = {slug: cost for slug, _l, _s, cost, _v, _d in TIERS}
TIER_VERIFIED = {slug: verified for slug, _l, _s, _c, verified, _d in TIERS}
TIER_DOC = {slug: doc for slug, _l, _s, _c, _v, doc in TIERS}
# 值本身为空时的分数（不是「这一层不可信」，而是「根本没有值可评」）
NO_VALUE = 0
# 达到这个可信分就算「够好了，瀑布不必再往下问」——取自 site 层：直采且过了
# 归属核验的官网号码。低于它才继续花钱，等于「命中即停」的闸。
CONFIDENT_MIN = TIER_SCORE["site"]
# 证据串封顶：一格里要塞下 3 个字段 × (层级 + 出处)，Excel 单元格超过这个数
# 就会被截断显示，而「被截断的证据」比没有证据更容易误导人。
PROOF_MAX = 240

_SPLIT = re.compile(r"[｜|]")


def tier_info(slug):
    """层级登记项（查不到返回 None，让调用方自己决定是跳过还是报错）。"""
    if slug not in TIER_LABEL:
        return None
    return {"slug": slug, "label": TIER_LABEL[slug], "score": TIER_SCORE[slug],
            "cost": TIER_COST[slug], "verified": TIER_VERIFIED[slug],
            "doc": TIER_DOC[slug]}


def tier_slug_for(value):
    """按中文名反查 slug（用户手改表格、或从旧表回读时只拿得到中文）。"""
    text = str(value or "").strip()
    for slug, label, _s, _c, _v, _d in TIERS:
        if text == label:
            return slug
    return ""


def evidence_columns():
    """新列名（表头接线处只许调这里，不许再抄一遍字面量）。"""
    return (EVIDENCE_COL, PROOF_COL, VERIFIED_COL)


def verified_at(rec, collected_field="采集时间"):
    """这一行联系方式「最近一次被真的验过」是什么时候：优先取核验时间列。

    退回采集时间是**必需**而不是偷懒：历史表里没有这一列，全表按「不知道」处理会把
    几千行一次性推进重验队列（等于第 1 轮预算全烧在旧数据上，还把新采的挤掉）；
    而采集时间确实是这些值唯一的出处。两者都不是时返回空串，由 is_stale 判为到期。
    """
    got = str((rec or {}).get(VERIFIED_COL) or "").strip()
    if got:
        return got
    return str((rec or {}).get(collected_field) or "").strip()


def stamp_verified(rec, when=""):
    """核验成功时刷新时间戳（只有真的在这一行验到东西才该调它）。"""
    if rec is None:
        return ""
    rec[VERIFIED_COL] = str(when or "").strip() or _now_text()
    return rec[VERIFIED_COL]


def _now_text():
    import time as _t
    return _t.strftime("%Y-%m-%d %H:%M:%S")


# ── 每行两个格子的编解码 ──────────────────────────────────────────────
# 存成「电话:官网直采｜邮箱:同主体已有」而不是每个字段两列：字段只有三个，
# 但列一旦按字段铺开就是 6 列，导出/清洗/索引三层都要各改一遍，分叉概率翻倍。
def parse_evidence(rec):
    """{字段: (层级中文名, slug)}；解析不出来的片段一律忽略但不报错（手改的表要能读）。"""
    out = {}
    for part in _SPLIT.split(str((rec or {}).get(EVIDENCE_COL) or "")):
        part = part.strip()
        if not part or ":" not in part and "：" not in part:
            continue
        field, label = re.split(r"[:：]", part, 1)
        field, label = field.strip(), label.strip()
        if field in CONTACT_FIELDS and label in TIER_LABEL.values():
            out[field] = (label, tier_slug_for(label))
    return out


def parse_proof(rec):
    """{字段: 出处串}（与 parse_evidence 同一条分隔规矩，两者必须成对读）。"""
    out = {}
    for part in _SPLIT.split(str((rec or {}).get(PROOF_COL) or "")):
        part = part.strip()
        if not part:
            continue
        field, proof = re.split(r"[:：]", part, 1)
        field = field.strip()
        if field in CONTACT_FIELDS:
            out[field] = proof.strip()
    return out


def set_field_evidence(rec, field, tier_slug, proof=""):
    """写入某一字段的命中层级与出处。

    四条规矩：
      · 未知字段/未知层级直接返回 False——让调用方**必须**先在注册表里登记，
        比安静收下然后写出一列认不出来的值好得多；
      · 该字段在这一行**没有值**时同样返回 False：层级是「这个值从哪一层来」的证据，
        值不存在就没有证据可说。以前这条只写在三个调用方各自的注释里（website_contact
        先写值再记层、carry_pass 只在真的追加成功时记、merge_field_evidence 显式判空），
        靠约定不设置的结果就是「邮箱:工商核验」而邮箱格是空的——那种格子会让人
        对整个证据列失去信任，而它是可以在这一个函数里一次性堵死的；
      · 只升级不降级：已有层级可信分更高时不覆盖（同一格被后跑的一轮写低，
        是「数据看起来变差了」最典型的成因）；
      · 出处为空时补一句层级的标准解释，不留空（空证据在交付时等于没说）。
    """
    if rec is None or field not in CONTACT_FIELDS:
        return False
    if not str(rec.get(field) or "").strip():
        return False
    info = tier_info(tier_slug)
    if not info:
        return False
    got = parse_evidence(rec)
    cur = got.get(field)
    if cur and cur[1] and TIER_SCORE.get(cur[1], 0) > info["score"]:
        return False
    got[field] = (info["label"], tier_slug)
    proofs = parse_proof(rec)
    proofs[field] = (str(proof or "").strip() or info["doc"])[:120]
    rec[EVIDENCE_COL] = "|".join(f"{f}:{got[f][0]}" for f in CONTACT_FIELDS if f in got)
    rec[PROOF_COL] = "|".join(f"{f}:{proofs[f]}" for f in CONTACT_FIELDS
                              if f in proofs and proofs[f])
    # 封顶后可能被砍掉最后一整段（半句话比没话更糟），所以按段裁剪而不是按字符切。
    rec[PROOF_COL] = _clip_segments(rec[PROOF_COL], PROOF_MAX)
    return True


def _clip_segments(text, limit):
    """按「｜段」为单位裁剪到 limit 以内：宁可少说一个字段，也不留下半句证据。"""
    parts = [p for p in _SPLIT.split(text or "") if p.strip()]
    out = []
    n = 0
    for p in parts:
        add = len(p) + (1 if out else 0)
        if n + add > limit:
            break
        out.append(p)
        n += add
    return "|".join(out)


def clear_field_evidence(rec, field):
    """字段值被清空时同步清掉它的层级（留着就是「没有号却写着官网核验」）。"""
    if rec is None or field not in CONTACT_FIELDS:
        return
    got = parse_evidence(rec)
    proofs = parse_proof(rec)
    got.pop(field, None)
    proofs.pop(field, None)
    rec[EVIDENCE_COL] = "|".join(f"{f}:{got[f][0]}" for f in CONTACT_FIELDS if f in got)
    rec[PROOF_COL] = "|".join(f"{f}:{proofs[f]}" for f in CONTACT_FIELDS
                              if f in proofs and proofs[f])


def field_confidence(rec, field):
    """这一行某字段的可信分：没值 0，有值没层级按「平台自带」兜底。

    兜底是必需的而不是偷懒：历史表里所有行都没有这两列，如果判 0 分，
    升级后第一次评分会把全表判成「建议放弃」——那是把工具自己说成废数据。
    """
    if not str((rec or {}).get(field) or "").strip():
        return NO_VALUE
    got = parse_evidence(rec).get(field)
    if got and got[1] in TIER_SCORE:
        return TIER_SCORE[got[1]]
    return TIER_SCORE["source"]


def row_confidence(rec):
    """整行的联系方式可信分 = 各字段按「值非空」加权的最高分。

    取最高而不是平均：外呼只需要**一个**打得通的号码，一个高可信号配三个废号，
    这行的可用性就是高可信那一档；取平均会被废号拖下去，排序就失真了。
    """
    best = NO_VALUE
    for field in CONTACT_FIELDS:
        if str((rec or {}).get(field) or "").strip():
            best = max(best, field_confidence(rec, field))
    return best


def is_confident(rec, field, min_score=CONFIDENT_MIN):
    """瀑布的「命中即停」闸：这一行这个字段已经够好，不必再往下花钱。"""
    return field_confidence(rec, field) >= int(min_score)


def has_any_value(rec, fields=CONTACT_FIELDS):
    return any(str((rec or {}).get(f) or "").strip() for f in fields)


def missing_fields(rec, fields=CONTACT_FIELDS):
    return [f for f in fields if not str((rec or {}).get(f) or "").strip()]


def merge_field_evidence(target, source_rec, fields=CONTACT_FIELDS):
    """跨行合并时把 source 的层级带进 target（值并进来，证据不能丢）。

    只认「值真的并进了 target」的字段才带层级：值没进来却把层级抄过去，
    就会造出「邮箱:官网直采」而邮箱列是空的这种自相矛盾的格子。
    调用方负责先做值合并，本函数只做证据侧的对齐。
    """
    moved = []
    proofs = parse_proof(source_rec)
    for field, (label, slug) in parse_evidence(source_rec).items():
        if field not in fields:
            continue
        if not str((target or {}).get(field) or "").strip():
            continue
        if not str((source_rec or {}).get(field) or "").strip():
            continue
        if set_field_evidence(target, field, slug, proofs.get(field, "")):
            moved.append(field)
    return moved


# ── 到期重验的字段级 TTL（单一真源也管这一件事）────────────────────────
# 为什么按字段而不是按整行：换得最勤的是手机/座机（企业换前台、搬厂区），
# 换得最慢的是官网（域名几乎不动）。整行一个 TTL 会出现两种错：设短了把
# 一年前查准的官网也重问一遍（白花请求），设长了过期两年的号码还当新数据卖。
TTL_DAYS = {
    "手机": 120,
    "电话": 150,
    "邮箱": 180,
    "_default": 180,
}
# 层级带来的修正：工商核验的号是登记信息，变更频率低于官网挂的营销号码；
# 平台自带最不可靠，过期判据要更紧。倍率只许在这里写一次。
TTL_TIER_FACTOR = {"registry": 1.5, "site": 1.0, "mailsite": 1.0,
                   "brand": 1.0, "carry": 1.0, "source": 0.75}
# 从没记过核验时间的历史行：按「未知」处理并**立刻算到期**。
# 说成「没到期」才是错的——那等于让全表永远不进重验队列，这个功能就白做了。
UNKNOWN_AT = ""


def ttl_days(field, tier_slug=""):
    days = TTL_DAYS.get(str(field or ""), TTL_DAYS["_default"])
    factor = TTL_TIER_FACTOR.get(tier_slug, 1.0)
    return max(30, int(round(days * factor)))


def is_stale(field, tier_slug, verified_at, ttl_days_override=None, today=None):
    """这一行这个字段是否该重验：无值不算，有时间戳按 TTL 判，没时间戳一律算到期。

    时间戳格式在仓库里同时存在 `2026-11-06` 与 `2026-11-06 15:22:31` 两种
    （采集时间列历史上就是两套写法），所以两边都只按**日期**比；解析不出来同样
    算到期——把「不知道什么时候验的」读成「验过且还新」，等于让全表永远不进重验
    队列，那正是这个功能最没用的形态。
    """
    value_days = (ttl_days_override if ttl_days_override is not None
                  else ttl_days(field, tier_slug))
    m_age = field_age_days(verified_at, today)
    if m_age is None:
        return True
    return m_age >= int(value_days)


_DATE_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")


def _to_date(text):
    m = _DATE_RE.search(str(text or ""))
    if not m:
        return None
    try:
        import datetime as _dt
        return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _age_days(text, today=None):
    cur = _to_date(text)
    if cur is None:
        return None
    import datetime as _dt
    # today 允许传 date 对象**或**日期字符串：仓库里所有时间戳本来就是字符串，
    # 调用方顺手传字符串是最自然的写法。上一版只认 date 对象，传字符串就在
    # `base - cur` 处抛 TypeError——而这条路径在「导出分层报表」的 try/except 里，
    # 表现不是报错而是报表静默变空，属于本项目记过账的「静默少功能」形态。
    base = today
    if base is None or (isinstance(base, str) and not base.strip()):
        base = _dt.date.today()          # 没给基准日＝按现在算，这是「未指定」而不是「看不懂」
    elif not hasattr(base, "year"):
        base = _to_date(base)
        if base is None:
            # 给了基准日却认不出来：不能退化成「当作今天」，那会把整表过期判据
            # 读成「都还新」。返回 None 让 is_stale 走 fail-closed 一侧（算到期）。
            return None
    return max(0, (base - cur).days)


def field_age_days(verified_at, today=None):
    """距今多少天；认不出来返回 None（调用方必须能分辨「很久」和「不知道」）。"""
    return _age_days(verified_at, today)
