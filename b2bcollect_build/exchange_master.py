# -*- coding: utf-8 -*-
"""exchange_master.py —— 交易所公开披露主数据（瀑布 ``exchange`` 级的数据层）。

【这一级为什么值得单独一个模块，而不是塞进 website_contact 的候选池】
中国大陆上市/挂牌主体把联系方式**报给监管**（年报、招股书里的董秘办电话、IR 邮箱、
公司网址），东方财富 F10 的 ``RPT_F10_ORG_BASICINFO`` 就是那份披露口径的公开镜像。
它和瀑布其它几级有本质区别：其它几级都是「拿一个域名去抓、抓回来再核验归属」，
而这一级给的是**主体自己申报的值**。所以它产的是联系方式本身，不是待抓候选——
把它接进候选池就等于把最硬的一层降级成一次普通的官网抓取，还会为它白付请求
（本模块逐行对齐一个包都不发）。

【全量主数据对齐，而不是逐条查询】（2026-11-10 与 11-18 两次实机核实）
逐条查的写法是「公司名 → suggest 转证券代码 → F10 详情」，一家两个请求、还要吃
限流与「偶发查不到」；而**不带 filter 参数就是全量**：一页 500 条、共约 50 页，
一轮约 55 秒把两万四千多家全部拉到本地，之后所有任务、所有行都只是字典命中。
成本从「每行两发」变成「每 90 天一发批」，且不再有「查不到」这种漏检形状。

【三档防错配（本模块的全部风险都集中在这里）】
主数据按名称对齐，最坏的事不是漏对齐，而是**把上市公司 A 的号挂到同名/近名的 B 头上**。
深海装备那轮实机抓到过一条真实错配：名单「中国船舶集团第七〇五研究所」的核心名前 4 字
与上市公司「中国船舶」完全一致，简称兜底会把它错配到 600150。于是这一层只有三档，
按严格度排，任何一档都还要求名录侧唯一（同一主体在多市场各挂一条不算歧义）：
  档一 全称一致 —— 名单核心名 == 名录 ORG_NAME 核心名；同主体多条时合并取值
  档二 简称一致 —— 名单核心名（或名单括号里那段**够长**的别名）== 名录证券简称，
                且该简称只指向一家主体；名单写的是带法律形式尾缀的登记名、
                剩下来的核心名又不足 ABBR_MIN_LEN 字时不兜底（「大自然有限公司」）
  档三 前缀严格 —— 名单核心名以名录简称为前缀，**余段必须整段都是组织形式尾缀**
                   （align(余段) 的核心名为空才算纯尾缀），简称不少于 4 字，且名录侧唯一主体
  机构类主体（研究所/研究院/学院/大学/设计院…）只许档一：那类名字天然共享集团前缀，
  兜底档在这种形状上必然出错，宁可不对齐。
  三档共同的最后一道闸是**名录侧唯一主体**，而它比的是全名去标点而不是核心名：核心名
  正是分组的键，拿它判「同主体」是恒真的，那道闸就会形同虚设（写错过一次，锁在判据里）。
宁缺毋滥在这里同样成立：漏一家 = 那行走后面的级；错一家 = 交付出去的号是别人的。

【运行边界】
· 逐行对齐**零网络**：只读本地主数据文件；文件缺失/损坏/没装上时 ``ensure`` 如实报告，
  本级不参与，绝不抛异常（瀑布任何一级出错都不该冒泡到采集主流程）。
· 同一份盘与同一个口径之间只读一次：``ensure`` 有快路径，``status`` 只读文件开头那
  一小段（那是一份两万多条的文件，而预览与边采边补都会反复问这一句账）。
· 唯一的出网点是 ``refresh``：分页拉全量并原子写盘，有预算、有停止位、失败不毁旧数据。
· **对齐口径由调用方交进来**（``align(name) -> (核心名, 括号简称, 是否机构类)``）：
  「什么算核心名」这条判断在瀑布里已经有 company_key 与 site_discovery.brand_core 两份，
  第三处只会让三处各判各的——同一个名字在两条路上算出两个键，账就永远说不清。
  因此本模块不自己规定归一写法，只规定口径必须满足的契约：核心名允许被剥成空串
  （档三要用「余段是不是整段尾缀」这一判据），且同一个字符串每次都要算出同一个键。
  而 :func:`name_core` 就是瀑布默认交进来的那一份的实现（尾缀词表只登记一次），
  调用方仍可换口径（离线用例靠这一点注入假口径）。
运行：纯标准库 + website_contact（形态校验把手）。可离线全量验证（fetcher 可注入）。
"""
from __future__ import annotations

import json
import os
import re
import time

import website_contact as _wc

__all__ = [
    "MASTER_FILE_NAME", "MASTER_FRESH_DAYS", "PAGE_SIZE", "AUTO_FETCH",
    "GRADES", "GRADE_LABEL", "NO_MATCH", "REJECTED", "master_path",
    "read_master", "status", "ensure", "refresh", "index", "reset_index",
    "match", "contacts", "disclosed_site", "paren_abbr", "is_institution",
    "name_core", "strip_punct", "entity_codes", "default_align", "load_rows",
]

# ── 接口契约（东财公开数据中心）───────────────────────────────────────
API_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
REPORT_NAME = "RPT_F10_ORG_BASICINFO"
# 只取对齐与联系方式用得上的列。刻意**不取 ORG_FAX**：传真不是电话，写进「电话」列
# 会让用户拨过去听 fax tone；也不取 ORG_PROFILE 那种长文本——它把本地文件撑大好几倍，
# 而瀑布一行都不读。
COLUMNS = ("SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,ORG_NAME,"
           "ORG_TEL,ORG_EMAIL,ORG_WEB")
PAGE_SIZE = 500          # 一页 500 条实测响应体约 135KB，远低于抓取封顶 1.2MB
MAX_PAGES = 200          # 两万四千多家约 50 页；到顶还没拉完说明接口口径变了，宁可停
REFRESH_GAP_S = 0.2      # 分页之间留一点间隔，别把公开接口打成压测
FETCH_TIMEOUT = 15.0     # 单页读数上限
FETCH_BUDGET_S = 120.0   # 整轮拉取封顶：到点就把已拿到的部分落盘并说明没拉完
# 每拉这么多页报一句进度。整轮约 50 页、五十几秒，而它跑在工作线程里且**卡在这一步时
# 界面看起来和卡死没有区别**（2026-11-18 用户报的就是转圈）——一条不动的日志比慢更可怕。
PROGRESS_PAGES = 10

# ── 本地主数据文件 ────────────────────────────────────────────────────
# 名字必须留在 ``*_FILE_NAME`` 这一族常量里：交付链的门禁会从源码抽出这一族名字，
# 逐个问 git「.gitignore 挡不挡得住它」。写成别的名字等于绕开那道防误提交的闸。
MASTER_FILE_NAME = "exchange_master.json"
# 多久算该重新拉一次。上市主体的披露联系方式一年改不了几次，但**新上市的公司每周都在长**：
# 90 天取的是「一轮任务最多白一次」与「别把公开接口当自己的库压」之间的位置。
MASTER_FRESH_DAYS = 90
# 逐行对齐路上允许不允许**为了拉全量而出网**。真正的开关在调用方（run_waterfall 的
# ``master_fetch``），这里只是最外层的兜：改成 False 之后本级只认盘上已有的那份。
AUTO_FETCH = True
# 拉过一次之后多久之内不再重试（进程内账，:func:`reset_index` 会清掉）。
# 这一条不是可选优化：拉失败或只拉到半截时，盘上没有任何「拉完过」的标记，``stale``
# 永远为真，于是**边采边补每 60 秒就会重开一轮五十几秒的全量拉取**——把「本该免费的
# 一级」做成新的转圈来源，正是 2026-11-18 用户报的那个形状。盘上有半截能用的时候
# 照旧用它，只是不再重试；一轮都没拿到的时候，上一次的失败结论会原样报进账里。
RETRY_GAP_S = 6 * 3600.0

# 披露值里常见的空值写法：接口用这些占位而不是 null，按值判才不会被写进表。
_NULLISH = {"", "null", "none", "na", "n/a", "-", "--", "无", "未知", "0"}
# 机构类主体：简称与前缀兜底在这种形状上必然出错（集团前缀 + 编号是它们的登记方式）。
_INSTITUTION_RE = re.compile(
    r"(?:研究所|研究院|学院|大学|设计院|实验室|医院|协会|学会|联合会|商会|委员会|"
    r"部队|武警|军事科学院|科学院)$")
# 主数据对齐**专用**的尾缀表：只收「去掉它不改变主体是谁」的纯粹法律形式词。
# 为什么不能拿 company_key 那张宽表过来用（上一版就是，被真实名录打回了）：
# 「中国船舶集团有限公司」剥掉「集团」就只剩「中国船舶」，而名录里那个简称属于
# 600150 中国船舶工业股份有限公司——集团是母公司、上市公司是它底下的子公司，
# **两个法人、两个总机**。同一件事在「表内认亲」那边只是多并几行（方向安全），
# 在「与两万四千家外部主体对齐」这边就是错补。所以集团/厂/中心/部/分公司/子公司
# 这些**能区分主体**的词一律留在核心名里，只有下面这几个词可以剔。
_ALIGN_TAIL_RE = re.compile(
    r"(?:股份有限公司|有限责任公司|有限公司|股份公司|公司)$")
_PAREN_RE = re.compile(r"[（(]([^（）()]{2,24})[）)]")
_SEP_RE = re.compile(r"[\s（）()【】\[\]·、,，.。\-—_&]+")
_DATE_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")

# 三档的名字：账目、证据列与判据共用这一份，别处不再各起一个说法。
GRADES = ("exact", "abbr", "prefix")
GRADE_LABEL = {"exact": "全称一致", "abbr": "简称一致", "prefix": "前缀严格"}
# 没对上与**被闸拦下**是两件事，必须分开数：前者是「名录里没有这家公司」（正常形状，
# 那行继续走后面的级），后者是「名录里有名字像的、但防错配闸不让用」（需要人工认一下）。
# 混成一个「没匹配上」，用户问「这家上市公司为什么没补到」时就只能给出一个错答案。
NO_MATCH = ""
REJECTED = "rejected"
# 简称的可用长度：档三拿名单核心名的**前缀**去撞名录简称，短于这个长度就等于拿
# 「中国」「上海」这种地名段去攀亲（名录里两三个字的简称背后是一整串不同的主体）。
# 档二也用它做下限，但只卡「名单写的是带法律形式尾缀的登记名」那一形状（见 match 里
# 那一句）：名单只写三个字的简称本身（「夜光微」）与「大自然有限公司」不是一回事，
# 前者就是照着证券简称写的，后者那三个字后面还跟着一家公司的名字。
ABBR_MIN_LEN = 4
# 括号里那一段当简称用时要求的长度：名单里的括号绝大多数是地名或备注
# （「(深圳)」「(暂定)」），两个字就拿去撞名录简称，撞上了也不是同一主体。
# 真实的企业别名基本都在四个字以上（中国平安 / 海尔智家 / 宁德时代）。
PAREN_MIN_LEN = 4


def master_path():
    """主数据落点：只问 tool_config.state_file（与 dns_cache_path 同一条落点纪律）。

    退路存在理由也在那儿：打包态的当前工作目录由「谁把进程拉起来」决定，用相对路径
    会把同一份数据落到四个地方，其中一个是退出即删的临时目录——那等于每次归零。
    """
    try:
        import tool_config as _tc
        try:
            return str(_tc.state_file(MASTER_FILE_NAME))
        except Exception:
            return str(_tc.user_state_dir() / MASTER_FILE_NAME)
    except Exception:
        return os.path.join(os.path.expanduser("~"), ".b2bcollect",
                            "b2bcollect_state", MASTER_FILE_NAME)


def _stamp(path):
    try:
        st = os.stat(path)
        return (round(st.st_mtime, 3), int(st.st_size))
    except OSError:
        return None


def read_master(path=None):
    """读盘：返回 (数据, 失败原因)。任何读不出结论的形状都如实报，不猜、不抛。"""
    path = master_path() if path is None else str(path)
    if not os.path.exists(path):
        return None, "本地还没有主数据文件"
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return None, f"主数据文件读不出来（{type(e).__name__}）"
    if not isinstance(data, dict) or not isinstance(data.get("rows"), list):
        return None, "主数据文件形状不对（没有 rows）"
    rows = [r for r in data["rows"] if isinstance(r, dict)]
    if not rows:
        return None, "主数据文件里一条记录都没有"
    return {"as_of": str(data.get("as_of") or "").strip(), "rows": rows,
            "partial": bool(data.get("partial")),
            "count": int(data.get("count") or len(rows))}, ""


def _age_days(as_of, today=None):
    """主数据是多少天前的。认不出日期一律按「不知道」处理，由调用方决定重拉。"""
    m = _DATE_RE.search(str(as_of or ""))
    if not m:
        return None
    import datetime as _dt
    try:
        made = _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    now = today or time.localtime()
    return (_dt.date(now.tm_year, now.tm_mon, now.tm_mday) - made).days


# ── 值形态：披露口径同样要过全仓唯一的形态闸 ─────────────────────────
def _values(raw):
    """一个披露字段 → 干净值列表（"NULL"/"--" 这类占位不算值）。"""
    out = []
    for part in _wc._SPLIT_RE.split(str(raw or "")):
        v = _wc.normalize_display(part)
        if not v or v.strip().lower() in _NULLISH:
            continue
        out.append(v)
    return out


def contacts(row):
    """披露记录 → 按**列**分好的联系方式（电话/手机/邮箱），形态不合格的整条丢掉。

    复用 plausible_phone / plausible_email / split_mobile 而不是在这里另判一遍：
    披露口径是「企业报给监管的字符串」，那里面同样有 400 写成两截、把传真占位号、
    邮箱格填了带空格的写法。写进用户表格的号码只许经过全仓那一道形态闸——两处各判时，
    松的那一处会成为脏号的来源，而它偏偏是「数据质量最高」那一层，最没人会怀疑。
    """
    out = {"电话": [], "手机": [], "邮箱": []}
    for v in _values(row.get("ORG_TEL")):
        if not _wc.plausible_phone(v):
            continue
        bucket = out["手机" if _wc.split_mobile(v) else "电话"]
        if v not in bucket:
            bucket.append(v)
    for v in _values(row.get("ORG_EMAIL")):
        low = v.lower()
        if "@" not in low or not _wc.plausible_email(low) or low in out["邮箱"]:
            continue
        out["邮箱"].append(low)
    return {k: v for k, v in out.items() if v}


def disclosed_site(row):
    """披露口径的公司网址（可直接当权威域名：那是主体自己报给交易所的，不需要猜）。

    刻意**不走 :func:`_values`**：那个把手是按「一格多值」切电话与邮箱用的，分隔符里带着
    斜杠——拿它切 URL 会把 ``https://www.1688.com/company/x.html`` 切成几截，平台域那一截
    被拦下之后，剩下的 ``x.html`` 反倒像个工作域名（实测就会返回 ``https://x.html/``：
    把披露值里的**路径末段**当成了主体官网）。所以这里整串当一个值，只在逗号/分号上切。

    返回的总是 ``https://<host>/`` 那个根：路径与查询串是披露当时的页面位置，不是主体
    的域名；把它原样交给后面的抓取只会抓到一个随时 404 的深链，而抓不到官网首页。
    """
    raw = str(row.get("ORG_WEB") or "").strip()
    if not raw or raw.lower() in _NULLISH:
        return ""
    for token in re.split(r"[,;，；、]+", raw):
        v = token.strip()
        if not v or v.lower() in _NULLISH:
            continue
        # allow_lookup=False：这一级在预览与对齐路上都不许碰 DNS（同 discover_site 那条纪律）
        host = _wc.host_of(v) if "//" in v else re.split(r"[/?#]", v)[0]
        host = str(host or "").strip().lower().strip(".")
        if "." not in host or _wc.is_platform_host(host):
            continue
        if not _wc.is_public_host(host, allow_lookup=False):
            continue
        return f"https://{host}/"
    return ""


# ── 索引：三档各有自己的键，算一次、进程内复用 ───────────────────────
def build_index(rows, align):
    """rows → 索引。同一家公司在 A 股/B 股/新三板各有一条记录、各披露一个号码是常态，
    所以键里存的是**主体标识**（全名去标点、不分大小写），值合并在主体上：档一才能
    既命中又不漏值。

    刻意不用 SECUCODE 当主体标识（上一版就是）：那个代码**每条记录**一个，同一家在多
    市场各挂一条时永远合不上，两万四千多条记录会被当成两万四千多家主体来记账，
    ``count`` 报的数与界面那句话说的数也不是一个东西（实测合并掉 2578 条重复记录）。
    """
    by_core = {}
    by_abbr = {}
    entities = {}
    bad_align = 0
    for row in rows or []:
        full = str(row.get("ORG_NAME") or "").strip()
        if not full:
            continue
        core, _paren, _inst = align(full)
        abbr = str(row.get("SECURITY_NAME_ABBR") or "").strip()
        abbr_core = align(abbr)[0] if abbr else ""
        if len(core) < 2 and len(abbr_core) < 2:
            bad_align += 1
            continue
        # 主体标识用**全名去标点 + 不分大小写**，不是 SECUCODE：SECUCODE 每条记录都不同
        # （同一家在 A 股/B 股/新三板各一个号），拿它当键就把「多市场各挂一条」这个
        # 真实形状永远合不上：两万四千多条记录会被当成两万四千多家主体来记账。
        norm = _SEP_RE.sub("", full).lower()
        eid = norm or core or abbr_core
        ent = entities.get(eid)
        got = contacts(row)
        if ent is None:
            ent = {"code": str(row.get("SECURITY_CODE") or ""),
                   "secucode": str(row.get("SECUCODE") or ""),
                   "secucodes": [str(row.get("SECUCODE") or "")],
                   "name": full, "abbr": abbr, "core": core,
                   "norm": norm,
                   "web": disclosed_site(row),
                   "fields": {k: list(v) for k, v in got.items()}}
            entities[eid] = ent
        else:
            # 同一主体的第二条记录（另一个市场）通常只有**另一串号码**可披露，合并而不是二选一
            ent["web"] = ent["web"] or disclosed_site(row)
            ent["core"] = ent["core"] or core
            ent["abbr"] = ent["abbr"] or abbr
            sc = str(row.get("SECUCODE") or "")
            if sc and sc not in ent["secucodes"]:
                ent["secucodes"].append(sc)
            for f, vals in got.items():
                keep = ent["fields"].setdefault(f, [])
                for v in vals:
                    if v not in keep:
                        keep.append(v)
        if len(core) >= 2:
            by_core.setdefault(core, [])
            if eid not in by_core[core]:
                by_core[core].append(eid)
        if len(abbr_core) >= 2:
            by_abbr.setdefault(abbr_core, [])
            if eid not in by_abbr[abbr_core]:
                by_abbr[abbr_core].append(eid)
    return {"by_core": by_core, "by_abbr": by_abbr, "entities": entities,
            "abbr_keys": sorted(by_abbr), "bad_align": bad_align}


_INDEX = None
_INDEX_STAMP = None
_INDEX_ALIGN = None
# 上一次建索引时的账（present/count/as_of/age_days/partial/reason）：有了它，「盘没动、
# 口径没变」的那一问可以连文件都不开（见 :func:`ensure` 的快路径）。
_INDEX_META = {}
# 本进程上一次**尝试拉全量**的时刻与结论（``(monotonic, 结论)``）：只给 RETRY_GAP_S 那
# 条闸用。结论串直接进账目，让用户看到的还是那句真话而不是「今天没拉到」。
_FETCH_TRY = (0.0, "")


def reset_index():
    """丢掉进程内缓存（重新拉盘之后、以及每一轮离线用例之间用）。

    连 :data:`_FETCH_TRY` 一起清：那个时间戳只在本进程内有效，用例之间若不清掉，
    第二次「盘上没有、允许拉取」的场景会被重试闸拦成「一次都没试过」——那正是这条闸
    自己要防的形状反过来发生。
    """
    global _INDEX, _INDEX_STAMP, _INDEX_ALIGN, _INDEX_META, _FETCH_TRY
    _INDEX = _INDEX_STAMP = _INDEX_ALIGN = None
    _INDEX_META = {}
    _FETCH_TRY = (0.0, "")


def index():
    """当前索引；没就位就是 None。**不读盘、不建索引、更不出网**（预览路径靠这一条）。"""
    return _INDEX


def default_align(name):
    """本模块自带的那一份口径（也是瀑布 :func:`enrich_waterfall.align_key` 委派到的实现）。

    只给夹具与「调用方没交口径」的用法兜底：生产路仍然必须显式交口径（模块头那条
    契约不为一个方便的内设值而破），因为「什么算核心名」一旦在两处各定一份，
    同一个名字就会算出两个键。
    """
    return name_core(name), paren_abbr(name), is_institution(name)


def load_rows(rows, align=None, as_of="", path=None):
    """把一份**内存里的**披露记录装成当前索引：不读盘、不出网，数据就是交进来的那一份。

    存在的理由不是省几行测试代码：离线套件必须能在没有真主数据文件的沙箱里把
    exchange 级当本地字典测一遍。而 :func:`ensure` 的快路径认的是「同一份盘 + 同一个口径
    对象」，所以这里同时写回 :data:`_INDEX_STAMP`（按真路径算：沙箱里没文件时它是 None，
    与 ensure 算出的那一个天然相等）与 :data:`_INDEX_ALIGN`。

    两个使用约束（都是能被判据抓到的那种，不是雅观问题）：
      · ``align`` 必须与后面交给 :func:`ensure` 的是**同一个函数对象**，否则这里装好的
        索引会被认成「口径换了」而重建，夹具静默退回「盘上没有」；
      · 内存装的那一份在 ``allow_fetch=True`` 且盘上没文件时会被 :func:`ensure` 换成
        读盘结论（它只信盘）：要测拉取流程就写真文件，不要指望这里。
    """
    global _INDEX, _INDEX_STAMP, _INDEX_ALIGN, _INDEX_META
    align = align or default_align
    _INDEX = build_index(rows or [], align)
    _INDEX_STAMP = _stamp(master_path() if path is None else str(path))
    _INDEX_ALIGN = align
    _INDEX_META = {"present": bool(_INDEX["entities"]),
                   "count": len(_INDEX["entities"]), "as_of": str(as_of or ""),
                   "age_days": _age_days(as_of), "partial": False,
                   "fetched": False, "reason": ""}
    return dict(_INDEX_META)


# refresh 写盘时把这几个键放在最前面，就是为了让「看一眼主数据新不新」不必解析整表：
# 那份文件有两万多条、完整读一遍实测 0.19 秒，而这一眼账在界面线程上也可能被问。
_HEADER_MAX_CHARS = 2048
_HEADER_AS_OF = re.compile(r'"as_of":"([^"]*)"')
_HEADER_COUNT = re.compile(r'"count":(\d+)')
_HEADER_PARTIAL = re.compile(r'"partial":(true|false)')


def _header(path):
    """只读文件开头一小段 → {"as_of","rows","partial"}；形状不对就返回 None（不猜）。"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            head = f.read(_HEADER_MAX_CHARS)
    except OSError:
        return None
    m_as, m_ct = _HEADER_AS_OF.search(head), _HEADER_COUNT.search(head)
    if not (m_as and m_ct):
        return None
    m_pa = _HEADER_PARTIAL.search(head)
    return {"as_of": m_as.group(1), "rows": int(m_ct.group(1)),
            "partial": bool(m_pa) and m_pa.group(1) == "true"}


# ── 拉全量（本模块唯一出网的地方）────────────────────────────────────
def _default_fetcher(params):
    """走 website_contact 的传输层（超时拆分、连接复用、响应封顶只有一份实现）。

    刻意不在这里自己 ``requests.get``：那样就有第二份传输层，SSRF 闸与字节封顶
    只在一份里演进，另一份迟早漂。
    """
    q = "&".join(f"{k}={v}" for k, v in params.items())
    text, _final, why = _wc._fetch_once(f"{API_URL}?{q}", timeout=FETCH_TIMEOUT)
    if not text:
        raise OSError(why or "无响应")
    return json.loads(text)


def refresh(path=None, fetcher=None, log=None, stop=None, budget=FETCH_BUDGET_S):
    """分页拉全量并原子写盘：返回 {"ok","count","total","pages","as_of","partial","reason"}。

    四条纪律：
      · **失败不毁旧数据**：一页都没拿到时直接返回，绝不把一份能用的主数据写成空文件；
      · 部分成功也算成功（``partial=True``），但账上必须说清为什么只有半截——「半截主数据」
        与「没有主数据」在日志里长得一样，而它们的下一步完全不同；
      · tmp + 原子替换：瀑布可能同时有几个线程在 ``ensure``，读到半个 JSON 的那一级
        会安静地当「没有主数据」，那是最难复现的一种空转；
      · ``stop`` 一到就收尾，已拿到的部分照样落盘（用户按停止不该把这一轮的请求全白花）。
    """
    path = master_path() if path is None else str(path)
    fetcher = fetcher or _default_fetcher
    log = log or (lambda m: None)
    t0 = time.time()
    rows, page_no = [], 0
    total = pages = 0
    reason = ""
    while page_no < MAX_PAGES:
        if stop is not None and stop():
            reason = "被停止"
            break
        if page_no and time.time() - t0 > budget:
            reason = f"预算到点（{int(budget)}s）"
            break
        page_no += 1
        try:
            data = fetcher({"reportName": REPORT_NAME, "columns": COLUMNS,
                            "pageNumber": page_no, "pageSize": PAGE_SIZE,
                            "source": "HSF10", "client": "PC"})
            res = (data or {}).get("result") or {}
            got = res.get("data") or []
            total = int(res.get("count") or 0) or total
            pages = int(res.get("pages") or 0) or pages
        except Exception as e:
            reason = f"第 {page_no} 页拉取失败（{type(e).__name__}）"
            page_no -= 1
            break
        rows.extend(got)
        if PROGRESS_PAGES and page_no % PROGRESS_PAGES == 0:
            # 慢不可怕，可怕的是慢得看不出来：整轮五十几秒全部堆在「开始补采」与
            # 第一行账目之间，用户读到的就是上一轮那句「鼠标转圈」。
            log(f"🗂 披露主数据拉取中：{page_no}/{pages or '?'} 页、已拿 {len(rows)} 条"
                f"（只有第一次会拉，之后每 {MASTER_FRESH_DAYS} 天才更新一次）")
        if not got:
            break
        if total and len(rows) >= total:
            break
        if pages and page_no >= pages:
            break
        if REFRESH_GAP_S:
            time.sleep(REFRESH_GAP_S)
    if not rows:
        return {"ok": False, "count": 0, "total": total, "pages": page_no,
                "as_of": "", "partial": False, "reason": reason or "一页都没拿到"}
    as_of = time.strftime("%Y-%m-%d")
    partial = bool(reason) or bool(total and len(rows) < total)
    payload = {"source": API_URL, "report": REPORT_NAME, "as_of": as_of,
               "count": len(rows), "total": total or len(rows),
               "pages": page_no, "partial": partial, "rows": rows}
    tmp = path + ".tmp"
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
    except Exception as e:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return {"ok": False, "count": len(rows), "total": total, "pages": page_no,
                "as_of": as_of, "partial": partial,
                "reason": f"拉到了但写不下去（{type(e).__name__}）"}
    reset_index()
    log(f"🗂 披露主数据已落盘：{len(rows)} 条 / {page_no} 页（as_of {as_of}）"
        + (f"；{reason}，剩下的下一轮接着拉" if partial and reason else ""))
    return {"ok": True, "count": len(rows), "total": total, "pages": page_no,
            "as_of": as_of, "partial": partial, "reason": reason}


def _try_note(r):
    """一次拉取的结果 → 一句「下次为什么不必再试」的话（重试闸上的账，直接进 ``reason``）。"""
    if not r["ok"]:
        return str(r["reason"] or "拉取没成功")
    if r["partial"]:
        return f"只拉到半截（{r['pages']} 页 / {r['count']} 条）"
    return ""


def ensure(path=None, align=None, allow_fetch=None, fetcher=None, log=None,
           stop=None, budget=FETCH_BUDGET_S):
    """拿到可用的索引：先读盘，盘上没有（或太旧/只有半截）才**最多**拉一次。

    返回的那七个键就是本级在账目里要说的七件事，``exchange_pass`` 原样转进 stats——
    这样「这一级为什么一行都没补」永远有一句现成的话可以说，不用现场编。
    其中 ``count`` 是**主体家数**（同一家在多市场各挂一条只算一家），不是文件里的记
    条数：界面那句「名录 N 家」与判据里「命中几家」说的必须是同一个东西，不能一边数行、一边数家。

    快路径（边采边补每 60 秒就问一次、界面点一下也要问一次）：盘上那份没动、口径没变、
    而且还不需重拉时，**连文件都不开**直接回上次的账。少了这一条，本级自己就会变成
    新的转圈来源：那是一份两万多条、解析加建键要两秒多的文件。
    反方向同样要闸：拉失败或只拉到半截时盘上永远算「该重拉」，每 60 秒重开一轮
    全量就是把本级从「零请求」做成「最贵的一级」——:data:`RETRY_GAP_S` 管的就是这一条。
    """
    global _INDEX, _INDEX_STAMP, _INDEX_ALIGN, _INDEX_META, _FETCH_TRY
    log = log or (lambda m: None)
    path = master_path() if path is None else str(path)
    allow_fetch = AUTO_FETCH if allow_fetch is None else bool(allow_fetch)
    out = {"present": False, "count": 0, "as_of": "", "age_days": None,
           "partial": False, "fetched": False, "reason": ""}
    if not callable(align):
        out["reason"] = "对齐口径没接上（调用方没交 align）"
        return out
    stamp = _stamp(path)
    if (_INDEX is not None and _INDEX_STAMP == stamp and _INDEX_ALIGN is align):
        age = _INDEX_META.get("age_days")
        fresh = (age is not None and age <= MASTER_FRESH_DAYS
                 and not _INDEX_META.get("partial"))
        if not allow_fetch or fresh:
            out.update(_INDEX_META)
            return out
    data, why = read_master(path)
    age = _age_days(data["as_of"]) if data else None
    if data:
        out["as_of"], out["age_days"] = data["as_of"], age
    if data is None and not allow_fetch:
        out["reason"] = why
        return out
    stale = data is None or age is None or age > MASTER_FRESH_DAYS or data["partial"]
    if allow_fetch and stale:
        waiting = _FETCH_TRY[0] > 0.0 and (time.monotonic() - _FETCH_TRY[0]) < RETRY_GAP_S
        if waiting:
            # 上一轮已经试过：不再拉第二轮。盘上什么都没有时这一句就是本级的全部结论，
            # 盘上还有半截可用时照旧用它——两种形状都得把「为什么没重试」说出来，
            # 不能只留一句「拉取失败」让人以为是接口坏了。
            mins = int((time.monotonic() - _FETCH_TRY[0]) // 60)
            if data is None:
                out["reason"] = (f"{_FETCH_TRY[1] or '上一次拉取没成功'}"
                                 f"（{mins} 分钟前试过，不重复拉全量）")
                return out
            out["reason"] = (f"{_FETCH_TRY[1] or '上一次已拉全'}"
                             f"（{mins} 分钟前试过，不重复拉全量；本轮沿用盘上那份）")
        else:
            out["fetched"] = True
            if data is None:
                log(f"🗂 第一次用披露主数据：先从东方财富拉全量"
                    f"（分页共约 50 页、一次性，之后每 {MASTER_FRESH_DAYS} 天才更新一次）")
            r = refresh(path=path, fetcher=fetcher, log=log, stop=stop, budget=budget)
            _FETCH_TRY = (time.monotonic(), _try_note(r))
            if not r["ok"] and data is None:
                out["reason"] = r["reason"] or "拉取失败"
                return out
            data, why = read_master(path)
            if data is None:
                out["reason"] = why
                return out
            out["as_of"], out["age_days"] = data["as_of"], _age_days(data["as_of"])
    elif data is None:
        out["reason"] = why
        return out
    stamp = _stamp(path)
    if _INDEX is None or _INDEX_STAMP != stamp or _INDEX_ALIGN is not align:
        _INDEX = build_index(data["rows"], align)
        _INDEX_STAMP = stamp
        _INDEX_ALIGN = align
    out["partial"] = bool(data["partial"])
    out["count"] = len(_INDEX["entities"])
    out["present"] = out["count"] > 0
    if not out["present"]:
        out["reason"] = "主数据装上来了但一条都没建成键（对齐口径把名字全剥空了）"
    _INDEX_META = dict(out)
    return out


def status(path=None):
    """只读一眼账（不建索引、不出网、**不解析整表**）：界面与报表那句「主数据就位没有」。

    这里报的是文件里的**记录条数**（rows）而不是主体家数：后者只有建过索引才算得出，
    而这一条路径存在的理由恰恰是「不为了一个字读完整份文件」。两个数在
    「一家公司多市场各挂一条」时天然不等，所以键名也不同，不拿同一个名字装两种口径。
    """
    path = master_path() if path is None else str(path)
    if not os.path.exists(path):
        return {"present": False, "rows": 0, "as_of": "", "partial": False,
                "age_days": None, "reason": "本地还没有主数据文件"}
    head = _header(path)
    if head is None:
        data, why = read_master(path)          # 不是我们写的那种形状（被人改过）：退回完整读法
        if not data:
            return {"present": False, "rows": 0, "as_of": "", "partial": False,
                    "age_days": None, "reason": why}
        head = {"as_of": data["as_of"], "rows": data["count"], "partial": data["partial"]}
    return {"present": True, "rows": head["rows"], "as_of": head["as_of"],
            "partial": head["partial"], "age_days": _age_days(head["as_of"]),
            "reason": ""}


# ── 三档对齐 ─────────────────────────────────────────────────────────
def _group(idx, table, key):
    """取一个键下的全部主体记录（组内可能不止一条：多市场、也可能真的同名不同司）。"""
    return [idx["entities"][i] for i in table.get(key, []) if i in idx["entities"]]


def _same_subject(ents):
    """这一组是不是同一家公司：比的是**全名（只去空白标点、不分大小写）**，不是核心名。

    这里不能偷懒比 core：core 正是分组的键，组内当然个个相等——那样这道闸就永远放行，
    「XX检测中心」与「XX检测」这种被尾缀表抹平的两家主体就会被当成一家而各取一条记录。
    全名不同就是两个主体，歧义一律拒，这是三档共同的最后一道闸。
    """
    return len({e["norm"] for e in ents}) == 1


def entity_codes(ent):
    """一个主体名下的全部证券代码（多市场各一条；旧夹具里可能只有单个 secucode）。

    证据列与判据都从这里取，不在调用方各写一份 ``ent["secucodes"]``：那个键是索引内部
    形状（旧夹具、单条记录、合并后的拷贝三种来源写法不同），调用方自己判就会漏一种。
    """
    got = [str(x) for x in (ent.get("secucodes") or []) if x]
    if not got and ent.get("secucode"):
        got = [str(ent["secucode"])]
    return got


def _merge(ents):
    """同主体在多个市场各挂一条时合并取值（它们常常各披露一个号码，二选一就是丢数据）。

    返回的是新造的一份，不改索引里的原记录：那一页同时被别的键引用，就地合并会把
    上一轮的取值混进这一轮，离线用例也别想再指望索引是可重复的。
    """
    if len(ents) == 1:
        return ents[0]
    base = dict(ents[0])
    base["fields"] = {}
    for f, vals in (ents[0].get("fields") or {}).items():
        base["fields"][f] = list(vals)
    codes = list(entity_codes(ents[0]))
    for e in ents[1:]:
        for f, vals in (e.get("fields") or {}).items():
            keep = base["fields"].setdefault(f, [])
            for v in vals:
                if v not in keep:
                    keep.append(v)
        base["web"] = base["web"] or e.get("web", "")
        for sc in entity_codes(e):
            if sc not in codes:
                codes.append(sc)
    base["secucodes"] = codes
    return base


def _resolve(ents, grade, reject):
    """一组候选 → (主体, 档位, 说明)：歧义就拒，同主体就合并多条取值。

    拒的时候返回的档位是 ``REJECTED`` 而不是空串：调用方要把「被闸拦下」与
    「名录里根本没这家」分开记账，那两件事的下一步处置完全不同。
    """
    if not ents:
        return None, NO_MATCH, ""
    if not _same_subject(ents):
        return None, REJECTED, reject
    return _merge(ents), grade, GRADE_LABEL[grade]


def match(name, align):
    """(主体, 档位, 说明)。没对上时主体为 None，说明仍然给一句能进日志的话。

    档位那一项只有三种取值：``GRADES`` 里的一个、``REJECTED``（闸拦下了）、
    ``NO_MATCH``（名录里没这家）。说明里带「被拒」的那几条不是失败装饰：它们是这一级
    唯一的可复核证据——用户问「这家上市公司为什么没补到」时，「三档都没对上」和
    「档二被拒：简称歧义」是两种完全不同的处置（前者无解，后者是名录里有两家同名，
    需要人工认一下）。
    """
    idx = _INDEX
    if idx is None:
        return None, REJECTED, "主数据未就位（没读盘、没建索引，这一级一次都没问过）"
    core, paren, institution = align(name)
    if len(core) < 2:
        return None, NO_MATCH, "核心名不足两个字，任何一档都不许对齐"
    # 档一：全称核心名完全一致
    got = _resolve(_group(idx, idx["by_core"], core), "exact",
                   "档一被拒：同一核心名在名录里指向不止一家主体")
    if got[0] is not None or got[1] == REJECTED:
        # 注：被拒就直接收尾，不再往下兜底。同一个歧义换个键再撞一次只会把「拿不准」
        # 洗成「看起来拿准了」——名录里两家同核心名的主体，简称档也不可能是另一家。
        return got
    # 机构类主体到此为止：那类名字天然共享集团前缀，兜底档在这种形状上必然错配
    if institution:
        return None, REJECTED, "机构类主体只认全称完全一致（简称与前缀兜底一律禁用）"
    # 档二：简称一致——名单只写简称、或名单在括号里写了简称，两种形状走同一个键
    ab = core
    if paren:
        p = align(paren)[0]
        # 括号里那一段只有**够长**才当别名用；不可用时不用空串顶上去，而是退回
        # 「名单本身就是简称」那个 ab=core——剥空了去撞键会把所有短括号当成同一家。
        if len(p) >= PAREN_MIN_LEN:
            ab = p
    if len(ab) >= 2:
        ents = _group(idx, idx["by_abbr"], ab)
        # 三个字以内的简称只在一个形状上危险：名单交来的是**登记名**（带法律形式尾缀，
        # 所以口径剥完之后只剩三个字），而名录里那三个字正好是某家的证券简称——
        # 「大自然有限公司」撞上「杭州大自然科技股份有限公司」（简称大自然）就是这个形状，
        # 真实名录里这种两个字三个字的商号一抓一大把，撞上了也不是同一主体。
        # 反过来，名单只写三个字的简称本身（「夜光微」）就是照着证券简称写的，照收。
        # 只在**名录里真有这个简称**时才记这一笔拒：名录里根本没这个简称的本来就落不到档二，
        # 报成「被拒」就是把没发生过的事写成拦下过一次。
        if ents and len(ab) < ABBR_MIN_LEN and strip_punct(name).lower() != core:
            return None, REJECTED, (
                f"档二被拒：简称「{ab}」只有 {len(ab)} 字，而名单写的是一家带尾缀的登记名"
                "（三个字不足以定主体，与档三同一道下限）")
        got = _resolve(ents, "abbr", "档二被拒：同一简称在名录里指向不止一家主体")
        if got[0] is not None or got[1] == REJECTED:
            if got[0] is not None and paren and ab != core \
                    and ab not in core and core not in ab:
                # 括号里的别名与全称看不出同一主体：那多半是英文名或无关备注，
                # 拿它去撞名录简称就是凭空攀亲
                return None, REJECTED, "档二被拒：括号里的简称与全称看不出同一主体"
            return got
    # 档三：前缀严格档。从最长的那一段试起（越长的简称越具体），取**第一个在名录里**
    # 的简称前缀；并要求余段整段都是组织形式尾缀（align(余段) 核心名为空）。
    # 这一条是这一档全部的安全边界：少了它，「中国船舶重工集团动力」会因为前 4 字
    # 是上市公司「中国船舶」而被挂到 600150 头上；有余段闸，余下「重工集团动力」判不出纯尾缀 → 拒。
    # 为什么必须比 ABBR_MIN_LEN 更长一段：刚好等长的那一档就是档二，递不到这里。
    if len(core) > ABBR_MIN_LEN:
        for ln in range(len(core) - 1, ABBR_MIN_LEN - 1, -1):
            key = core[:ln]
            ents = _group(idx, idx["by_abbr"], key)
            if not ents:
                continue                       # 这一段不是名录里的简称，往前缩一段再试
            if not _same_subject(ents):
                return None, REJECTED, "档三被拒：同一简称前缀指向不止一家主体"
            rest = core[ln:]
            if align(rest)[0]:
                return None, REJECTED, (
                    f"档三被拒：简称「{key}」之外还多出「{rest}」，判不出同一主体")
            return _merge(ents), "prefix", GRADE_LABEL["prefix"]
    return None, NO_MATCH, "三档都没对上（名录里没有这家上市/挂牌主体）"


# ── 对齐口径（默认那一份，也是尾缀词汇表的唯一登记点）─────────────
def name_core(name):
    """核心名 = 去空白标点 + 剔纯粹法律形式尾缀 + 不分大小写（契约见模块头）。

    允许剥成空串：档三判「简称之外剩下的那段是不是整段都是尾缀」用的正是这个能力
    （剥不空就说明还带着专名段 → 拒）。行业词（机电/贸易/装备）一个都不剔：剔了就把
    「怡星机电」与「怡星贸易」当成一家，那是搜索查询要的宽，不是主体对齐要的严。

    这一份就是**瀑布默认交进来的那一份**（:func:`enrich_waterfall.align_key` 直接委派到
    这里）：尾缀词汇表只在这里登记一次，调用点再抄一份就是「同一个名字两条路算出两个键」
    的开端；而调用方仍然可以交自己的口径（离线用例靠这一点注入假口径）。
    """
    s = _SEP_RE.sub("", str(name or "")).lower()
    while True:
        t = _ALIGN_TAIL_RE.sub("", s)
        if t == s:
            return t
        s = t


def strip_punct(name):
    """只去空白与标点，不动任何词——对齐口径的第一步，也是判定「同主体」用的那个形式。

    这一份为什么摆在本模块外面让调用方共用：同一个名字在两条路上算出两个键时，
    错配与漏配会同时存在，而日志里只能看到其中一种。
    """
    return _SEP_RE.sub("", str(name or ""))


def paren_abbr(name):
    """名单里括号内那一段（简称的常见写法）；没有括号返回空串。"""
    got = _PAREN_RE.findall(str(name or ""))
    return got[-1].strip() if got else ""


def is_institution(name):
    """机构类主体（研究所/研究院/学院/大学/设计院…）——它们只许走档一。

    判定前先把**括号那段整块切掉**、再拿 :func:`name_core` 剥法律形式尾缀，两个动作
    各挡一类漏判（都是真实名单里见过的写法）：
      · 「某某研究院(上海钢联)」——机构行常拿集团/母公司的证券简称当备注。按整串尾部判
        它就不算机构类了，而这一行恰恰会落在档二（别名撞简称）上——上市公司那个总机
        就这么挂到研究院头上；
      · 「某某研究院有限公司」——院所改制后的登记名，本质仍是那类天然共享集团前缀的
        主体，一个法律形式尾缀不该把它洗成普通企业。

    剥尾缀而不是另维护一张表：尾缀词表只有 :func:`name_core` 一个登记点（模块头那条契约）。
    刻意**不放宽成「名字里含研究院就行」**：那样会把「××研究院技术代理服务有限公司」这类
    普通企业也禁掉兜底，白丢一批能安全对齐的行——这一道的口径是「它本身是不是那个主体」。
    """
    head = re.split(r"[（(]", str(name or ""))[0]
    return bool(_INSTITUTION_RE.search(name_core(head)))
