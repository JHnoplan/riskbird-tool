# -*- coding: utf-8 -*-
"""test_exchange_master_offline.py —— 披露主数据层（exchange 级的数据侧）离线回归。

行为侧（exchange_pass 把值写进三格、候选首位、账目形状）在 test_waterfall_leads_offline.py
的 G2 一节；本套件管的是**那一层脚下的地基**：三档防错配、值形态闸、索引口径、读盘/写盘、
拉取预算与重试闸。这条分界线不是整理癖——本模块最大的风险（把 A 公司的号挂到 B 公司头上）
在行为侧根本看不见：那边只会如实写「名录说这两条是同一家」。所以每一档的**正反例**都必须
在这里各钉一次，否则「防错配」就只是一段没人验过的代码。

  A. 归一口径的契约与唯一登记点（尾缀表窄于「表内认亲」那张；核心名可被剥成空串；
     与瀑布 align_key 同源——两处各写一份时同一个名字会算出两个键）
  B. 值形态：披露口径也须过全仓唯一那道闸（传真不进电话列、占位串不算值、
     手机与座机分桶、邮箱统一小写、平台域不算披露官网、这一路零 DNS）
  C. 索引：主体标识是全名去标点而不是 SECUCODE（多市场各一条要合得上）、count 是**家数**、
     合并取值而不是二选一、合并不改索引原记录（可重复性）
  D. 三档正反例：exact / abbr（简称直写与括号别名两种形状）/ prefix（命中那一条靠注入
     口径走通——生产口径剥到不动点，那一支自己走不到，不能因此就当它是死代码）
  E. 九条防错配闸各钉一次，且**被拒与名录里没这家分开记账**（两种形状下一步处置完全不同）：
     档一歧义、档二简称歧义、档二拿三字的商号撞带尾缀的登记名、档三前缀指向多家、
     档三余段非纯尾缀（真实名录打回过的
     「中国船舶集团」与「(暂定)」两种形状）、机构类只认档一（含一条**真错配**形状：
     机构行括号里写母公司的证券简称）、别名与全称看不出同一主体
     （这一条只有注入口径才走得到，走不通就是死代码）；另有「索引未就位」那道护栏单独验
     ——它是护栏不是业务结论，不许报成「名录里没这家」
  F. 读盘四种形状都如实报；status 只读文件头那一小段（不解析整表）；头部形状不对时
     退回完整读法并报出真实失败原因
  G. refresh：拉完、预算到点、被停止、一页都没拿到**不毁旧数据**、拉到了但落不了盘、
     原子写不留 tmp、分页之间留间隔、慢的过程要在日志里看得见
     （拉五十几秒却一声不响＝用户报的那次「鼠标转圈」）
  H. ensure：快路径零读盘零建索引、口径对象换了必须重建、AUTO_FETCH=False 时一个包都不发、
     RETRY_GAP_S 重试闸（拉失败后不许每 60 秒重开一轮全量）
  I. 接口面：__all__ 与实际函数对拍（少一个导出名，调用方就自己重抄一遍实现形状），
     且导出的都是函数或纯数据、都定义在本模块里

运行：python test_exchange_master_offline.py
"""
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))
# 状态落点钉进沙箱：本套件会真写主数据文件，绝不能落在开发机的真实状态目录
_STATE_SANDBOX = Path(tempfile.mkdtemp(prefix="exm_state_"))
os.environ["B2B_STATE_DIR"] = str(_STATE_SANDBOX)

_passed = 0
_failed = 0


def _assert(condition, label):
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  ok   {label}")
    else:
        _failed += 1
        print(f"  FAIL {label}")


import exchange_master as em                                          # noqa: E402
import website_contact as wc                                          # noqa: E402

# ── 离线硬闸：数据层唯一该出网的地方是 refresh，而它每一处都由注入的 fetcher 接管 ──
_net_trips = []
_real_getaddrinfo = wc.socket.getaddrinfo


def _blocked_getaddrinfo(host, *a, **k):
    _net_trips.append(str(host))
    raise OSError(f"离线用例查了 DNS：{host}")


wc.socket.getaddrinfo = _blocked_getaddrinfo
try:
    import requests as _requests
    _real_requests_get = _requests.get

    def _tripwire_get(*a, **k):
        _net_trips.append(str(a[0] if a else ""))
        raise AssertionError("离线用例真发出了 HTTP 请求")

    _requests.get = _tripwire_get
except Exception:
    _requests = None
    _real_requests_get = None

# AUTO_FETCH 是最外层那道兜：关掉之后 allow_fetch=True 的调用点也只许读盘。
# G/H 两节要验拉取流程，靠的是**注入 fetcher**（不发真包），不是靠放开这道闸。
_real_auto_fetch = em.AUTO_FETCH
em.AUTO_FETCH = False

_TMP = Path(tempfile.mkdtemp(prefix="exm_case_"))


def _path(name):
    return str(_TMP / name)


def _write(name, text):
    """按文件名写一份夹具，返回**字符串**路径（本模块所有路径参数都收 str，与 _path 同形）。"""
    p = _TMP / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def _row(code, secucode, abbr, org, tel="", email="", web=""):
    return {"SECURITY_CODE": code, "SECUCODE": secucode, "SECURITY_NAME_ABBR": abbr,
            "ORG_NAME": org, "ORG_TEL": tel, "ORG_EMAIL": email, "ORG_WEB": web}


# 合成名录：字段形状取自实测接口（列名、多值逗号分隔、带空格的邮箱、缺位用 "-"）。
# 「夜光微/上海钢联/恒通检测/中国船舶」这几家的**形状**是照着真实名录里出现过的那几类
# 错配做的：一家多市场、两家同核心名、一家集团 vs 上市公司——每一条都对应一道闸。
_M_YG_BJ = _row("831761", "831761.BJ", "夜光微", "宁波夜光电材料股份有限公司",
                tel="0574-63886666, 13800002222", email=" IR@yeguangming.com ",
                web="www.yeguangming.com")
_M_YG_NQ = _row("430521", "430521.NQ", "夜光微", "宁波夜光电材料股份有限公司",
                 tel="0574-63998877", email="ir@yeguangming.com", web="-")
_M_STEEL = _row("300226", "300226.SZ", "上海钢联", "上海钢联物联网股份有限公司",
                tel="021-10108800", email="ir@steel-example.com", web="www.steel-example.com")
_M_CSSC = _row("600150", "600150.SH", "中国船舶", "中国船舶工业股份有限公司",
               tel="010-63880000", email="ir@cssc-example.com", web="www.cssc-example.com")
_M_CDP = _row("600482", "600482.SH", "中国动力", "中国船舶重工集团动力股份有限公司",
              tel="010-88880000")
_M_HT1 = _row("300901", "300901.SZ", "恒通检测", "江苏恒通检测有限公司", tel="0519-88001100")
_M_HT2 = _row("833902", "833902.BJ", "恒通检测", "江苏恒通检测股份有限公司", tel="0519-88002200")
_M_JUNK = _row("839001", "839001.BJ", "某某新材", "东莞某某新材料股份有限公司",
               tel="400-800-80,null,--,无,0", email="not-an-email,test@junk---.com")
_M_PLAT = _row("839002", "839002.BJ", "某某智能", "苏州某某智能装备股份有限公司",
               tel="0512-60001100", web="https://www.1688.com/company/foo.html")
_ROWS = [_M_YG_BJ, _M_YG_NQ, _M_STEEL, _M_CSSC, _M_CDP, _M_HT1, _M_HT2, _M_JUNK, _M_PLAT]


def _use(rows, align=None, path=None, as_of="2026-11-05"):
    """把一份内存名录装成当前索引（不读盘、不出网）。"""
    em.reset_index()
    return em.load_rows(rows, align=align or em.default_align,
                        as_of=as_of, path=path)


# ── A. 归一口径：契约与唯一登记点 ─────────────────────────────────────
print("A. 对齐口径与尾缀表")
_assert(em.name_core("宁波夜光电材料股份有限公司") == "宁波夜光电材料",
        "核心名剥掉的是纯粹法律形式尾缀（股份有限公司）")
_assert(em.name_core("深圳市金田实业股份有限公司") == em.name_core("深圳市金田实业"),
        "同一家写法不同算出同一个键（剥缀是幂等的）")
_assert(em.name_core("某某集团有限公司") == "某某集团",
        "「集团」**留在核心名里**：这张表比「表内认亲」那张窄，剥了就把母公司与子公司当一家")
_assert(em.name_core("怡星机电有限公司") != em.name_core("怡星贸易有限公司"),
        "行业词一个都不剥（剥了就把两家不同的公司并成一家）")
_assert(em.name_core("A公司") == "a",
        "口径允许被剥得很短（空串与短串都由调用方判：档三要拿「余段能不能整段剥空」做闸）")
_assert(em.strip_punct(" 宁波夜光电 材料（股份）  ") == "宁波夜光电材料股份",
        "strip_punct 只去空白标点、**一个词都不动**（连「股份」都留着：剔词是 name_core 的活，"
        "两个把手各管一段才判得出「同主体」与「同核心名」的区别）")
_assert(em.paren_abbr("金田实业(集团)(000402)") == "000402",
        "括号别名取**最后**一段（代码/地名常挂在末尾，多个括号时前一个是备注）")
_assert(em.paren_abbr("无括号公司") == "" and em.paren_abbr("") == "",
        "没有括号返回空串而不是 None（调用方要能直接判长度）")
for _s in ("研究所", "研究院", "学院", "大学", "设计院", "实验室", "医院", "协会", "部队"):
    _inst_bad = [x for x in (f"中国某某{_s}", f"某某集团第七〇五{_s}")
                 if not em.is_institution(x)]
    if _inst_bad:
        break
_assert(not _inst_bad,
        f"机构类主体（研究所/研究院/学院/…/部队）一律认得出来——它们只许走档一（漏：{_inst_bad}）")
_assert(not em.is_institution("中国船舶工业股份有限公司"),
        "普通企业不许被认成机构类（否则简称与前缀兜底会被一起禁掉，白丢一批能补的行）")
_inst_leak = [x for x in ("某某装备研究院(上海钢联)", "某某装备研究院（上海钢联）",
                          "上海化工研究院有限公司", "中机国际工程设计研究院有限责任公司")
              if not em.is_institution(x)]
_assert(not _inst_leak,
        f"机构行的**括号备注**与**改制尾缀**都不许把它洗成普通企业（漏判：{_inst_leak}）："
        "漏一行就是让某家研究院去撞母公司的证券简称、拿到别人的总机号")
_assert(not em.is_institution("某某研究院技术代理服务有限公司"),
        "反向对照：「研究院」只在名字中间、本体是普通代理/贸易企业时不禁兜底"
        "（放宽到「含研究院就算」会白丢一批能安全对齐的行）")
import enrich_waterfall as wf                                          # noqa: E402
_assert(all(wf.align_key(n) == em.default_align(n) for n in
            ("宁波夜光电材料股份有限公司", "中国船舶集团有限公司", "某某集团有限公司",
             "某某装备研究院(上海钢联)", "上海化工研究院有限公司")),
        "与瀑布 align_key **逐字同源**（连「是否机构类」那一项也是）：同一个名字在两条路上"
        "算出两个键时，错配与漏配会同时存在，而日志里只能看到其中一种")
_assert(em.__all__.count("default_align") == 1 and "entity_codes" in em.__all__,
        "口径实现与主体标识取值都是**公开出口**（调用方自己重抄形状就是分家的开始）")

# ── B. 值形态：披露口径也须过全仓那道闸 ──────────────────────────────
print("B. 披露值的形态闸")
_c = em.contacts(_M_YG_BJ)
_assert(_c["电话"] == ["0574-63886666"] and _c["手机"] == ["13800002222"],
        f"座机与手机**分桶**（同一格混着写时按形态分流，不整串塞进电话列）：{_c}")
_assert(_c["邮箱"] == ["ir@yeguangming.com"],
        f"带空格的「 IR@…」被修回小写可用值（原值直接进表就是脏邮箱）：{_c['邮箱']}")
_j = em.contacts(_M_JUNK)
_assert(_j.get("电话", []) == [],
        f"占位串与不成形的 400 一个都不进表（null/--/无/0 是接口的缺位写法，"
        f"400-800-80 少两位）：{_j}")
_assert(all("not-an-email" not in v for v in (_j.get("邮箱") or [])),
        f"邮箱格里没有「不是邮箱」的值（not-an-email 被丢掉而不是照抄进表）：{_j.get('邮箱')}")
_assert(em.contacts(_row("1", "1.BJ", "甲", "甲公司", tel="010-88390786"))["电话"]
        == ["010-88390786"],
        "正向对照：形态合格的号码照样收（闸不是「凡披露号都疑」，是把脏的挡在外面）")
_assert("ORG_FAX" not in em.COLUMNS,
        "拉取的列里**没有传真**：把传真写进电话列，用户拨过去就是 fax tone")
_assert(em.disclosed_site(_M_YG_BJ) == "https://www.yeguangming.com/",
        "裸域名补成可抓的 https（那是主体自己报给交易所的域名，直接当权威官网）")
_assert(em.disclosed_site(_M_PLAT) == "",
        "披露口径写了平台页（1688 公司页）时不算官网：那上面的电话是平台客服号")
_assert(em.disclosed_site(_row("1", "1.BJ", "甲", "甲公司", web="只有个名字没有点")) == "",
        "没有点的串不当域名（脏数据里这种写法很常见）")
_assert(em.disclosed_site(_row("1", "1.BJ", "甲", "甲公司",
                              web="http://www.acme.com.cn/index.html?a=1"))
        == "https://www.acme.com.cn/",
        "披露值带路径与查询串时只取域名并升到 https（原样交出去就是抓一个随时 404 的深链）")
_assert(em.disclosed_site(_row("1", "1.BJ", "甲", "甲公司",
                              web="https://www.1688.com/x.html, www.acme.com/contact"))
        == "https://www.acme.com/",
        "一格写两个（平台页 + 真官网）时跳过平台页取下一个，而不是退回路径末段")
_assert("foo.html" not in em.disclosed_site(_M_PLAT),
        "反向对照：平台页被拦下之后不许把 URL 的**路径末段**当域名（上一版实测返回 "
        "https://foo.html/：切多值那个把手的分隔符里带斜杠，把域名一路切没了）")
_assert(not _net_trips,
        f"值形态这一路一个 DNS 都没查（{_net_trips[:3]}）：预览与对齐不许为判断域名去联网")

# ── C. 索引：主体家数、多市场合并 ────────────────────────────────────
print("C. 索引口径与多市场合并")
_meta = _use(_ROWS)
_assert(_meta["present"] and _meta["count"] == 8,
        f"9 条记录 → 8 家主体（{_meta['count']}）：夜光电在 BJ/NQ 各一条必须合上，"
        "否则「名录 N 家」与「命中几家」说的不是一个东西")
_idx = em.index()
# 取这一家只用**不会把套件带崩**的写法：上一版这里直接 `[...][0]`，于是「键不是全名」那条
# 一旦变红（正是变异要做的事），下一句就 IndexError 把整个子进程带走——在变异台上那会被
# 读成「变异不成立（rc=97）」，而真正坏的是判据的夹具；一条红不该把后面的判据一起捂掉。
_yg_keys = [k for k in _idx["entities"] if "夜光" in k]
_assert(len(_yg_keys) == 1,
        f"夜光电在索引里只占**一个**主体键（实得 {len(_yg_keys)} 个：{_yg_keys}）："
        "零个就是键不是全名，两个就是多市场没合上")
_yg_key = _yg_keys[0] if _yg_keys else ""
_yg_ents = [e for e in _idx["entities"].values() if e["name"].startswith("宁波夜光电")]
_assert(len(_yg_ents) == 1, f"同一个全名只对应一个主体（实得 {len(_yg_ents)} 个）")
_yg = _yg_ents[0] if _yg_ents else {"secucodes": [], "web": "", "fields": {}}
_assert(len(_idx["entities"]) == 8 and not any("." in k for k in _idx["entities"]),
        "主体标识是**全名去标点**而不是 SECUCODE（键里一个「.BJ/.SH」那种形状都没有："
        "用 SECUCODE 时每条记录一个键，多市场永远合不上，家数会报成记录数 9）")
_assert(_yg_key == em.strip_punct("宁波夜光电材料股份有限公司").lower(),
        f"那个键就是全名去标点再不分大小写（{_yg_key}）——不是核心名、也不是代码："
        "核心名正是分组的键，拿它判「同主体」是恒真的")
_assert(_yg["secucodes"] == ["831761.BJ", "430521.NQ"],
        f"同一主体的多个市场代码都留在账上（证据串要说清是两个市场，不是两家）：{_yg['secucodes']}")
_assert(em.entity_codes(_yg) == ["831761.BJ", "430521.NQ"]
        and em.entity_codes({"secucode": "600150.SH", "secucodes": []}) == ["600150.SH"],
        "entity_codes 对「新形状 / 旧单条形状」都取得出代码（调用方不必重抄这个分支）")
_assert(_yg["web"] == "https://www.yeguangming.com/",
        "合并时取**有值的那一个**（NQ 那条披露的是缺位写法「-」，不能把域名抹成空）")
_assert(_idx["bad_align"] == 0,
        f"名录里没有「剥成空串又被丢掉」的行（bad_align 是这类形状的计数：{_idx['bad_align']}）")
_assert(_yg["fields"]["电话"] == ["0574-63886666", "0574-63998877"],
        "同主体多市场的取值在**建索引时**就合并好了（二选一就是丢数据：另一个市场那个号"
        "可能是唯一能通的）"
        )
_assert(em._merge([_yg]) is _yg,
        "组里只有一条时原样返回、不再造一份拷贝（造了拷贝，上层就会拿到两个「同一主体」的对象）")
_before = [list(v) for v in _yg["fields"].values()]
_r2 = em._merge([_yg, {**_yg, "fields": {"电话": ["0000-1111"]}, "secucodes": ["X.Y"]}])
_assert(_yg["fields"] is not _r2["fields"]
        and [list(v) for v in _yg["fields"].values()] == _before,
        "合并不改索引里的原记录：就地合并会把上一轮的取值混进这一轮，"
        "同一个索引再 match 第二次就不是原来的数了")
_assert(em.entity_codes(_r2) == ["831761.BJ", "430521.NQ", "X.Y"],
        f"合并结果带上全部市场代码（{em.entity_codes(_r2)}）")

# ── D+E. 三档正反例与五道拒 ──────────────────────────────────────────
print("D. 三档对齐（命中）")
_ent, _grade, _why = em.match("宁波夜光电材料股份有限公司", em.default_align)
_assert(_grade == "exact" and _ent and _ent["fields"]["手机"] == ["13800002222"],
        f"档一 全称一致（{_why}）：写法与名录逐字相同时不该用到兜底档")
_assert(_ent and _ent["fields"]["电话"] == ["0574-63886666", "0574-63998877"],
        f"档一命中同也带着多市场合并的取值（{_ent and _ent['fields']['电话']}）")
_ent2, _grade2, _ = em.match("上海钢联", em.default_align)
_assert(_grade2 == "abbr" and _ent2 and _ent2["name"] == "上海钢联物联网股份有限公司",
        f"档二 简称一致（名单只写简称那一类形状）：{_grade2}")
_e3, _g3, _w3 = em.match("某某贸易(上海钢联)", em.default_align)
_assert(_g3 == "abbr",
        f"括号里的证券简称也当别名用（真实名单常写「全称(证券简称)」）：{_g3}｜{_w3}")
_ent4, _grade4, _ = em.match("夜光微", em.default_align)
_assert(_grade4 == "abbr" and _ent4 and em.entity_codes(_ent4)[:1] == ["831761.BJ"],
        "简称命中时取的是那一家（不是名录里第一个键：夜光微只指向夜光电）")

# 档三的**命中**那一条：生产口径 name_core 把尾缀剥到不动点，于是「余段整段都是尾缀」
# 这种形状在默认口径下压根到不了这里（能整段剥掉的尾巴，算核心名时就已经剥掉了）。
# 那不是安全保证，而是构造结果——所以这一支必须用注入的口径走一遍：
# 契约写明「调用方可以交自己的口径」，走不通就说明那段代码是死的，而死了没人知道。
def _align_stub(name):
    """假口径：整串本身就是法律形式尾缀才剥空，其余一律保留（刻意不做不动点）。"""
    s = em.strip_punct(name).lower()
    return ("" if em.name_core(s) == "" else s), em.paren_abbr(name), em.is_institution(name)


_use([_M_STEEL], align=_align_stub)
_e5, _g5, _w5 = em.match("上海钢联股份有限公司", _align_stub)
_assert(_g5 == "prefix" and _e5 and _e5["name"] == "上海钢联物联网股份有限公司",
        f"档三 前缀严格档**可达**（{_g5}｜{_w5}）：换一种口径它就是活的，"
        "离线不许因为「默认口径走不到」而把它当死代码放过去")
_use([_M_STEEL], align=em.default_align)
_e6, _g6, _w6 = em.match("上海钢联股份有限公司", em.default_align)
_assert(_g6 == "abbr",
        f"同一份名录换回生产口径时这一行落进的是**档二**而不是档三（{_g6}｜{_w6}）："
        "生产口径把尾缀剥到不动点，「余段整段是尾缀」这一条在默认口径下自己走不到")
print("E. 九条防错配闸（被拦下与没这家分开）")
# 上一段为了让档三可达，把名录换成一条、口径换成假的那一份；这里必须换回生产现场——
# 不然下面这些「名录里确实有名字像的」场景全跑在一份小名录上，验的就是「没这家」而不是闸。
_use(_ROWS)
_jj = []                       # (归口, 说明)：每条拒都留底，收尾统一验「归得到某一道」

_e7, _g7, _w7 = em.match("江苏恒通检测有限公司", em.default_align)
_assert(_e7 is None and _g7 == em.REJECTED and "档一" in _w7,
        f"档一遇到同核心名两家主体就拒，且**不再往下兜底**（同一个歧义换个键再撞一次，"
        f"只是把「拿不准」洗成「看起来拿准了」）：{_w7}")
_jj.append(("档一", _w7))
_e8, _g8, _w8 = em.match("恒通检测", em.default_align)
_assert(_e8 is None and _g8 == em.REJECTED and "档二" in _w8 and "不止一家" in _w8,
        f"两家主体共用同一个证券简称时档二也拒（简称重复在真实名录里是常态，不是假设）：{_w8}")
_jj.append(("档二", _w8))
_e9, _g9, _w9 = em.match("恒通检测中心", em.default_align)
_assert(_e9 is None and _g9 == em.REJECTED and "档三" in _w9 and "不止一家" in _w9,
        f"档三的前缀命中两组主体时拒（这一档最容易「看起来只差一个尾缀」就放行）：{_w9}")
_jj.append(("档三", _w9))
_e10, _g10, _w10 = em.match("中国船舶集团有限公司", em.default_align)
_assert(_e10 is None and _g10 == em.REJECTED and "集团" in _w10,
        f"真实名录打回过的那一条：集团是母公司、600150 是它底下的上市公司，"
        f"**两个法人两个总机**（余段「集团」判不出纯尾缀 → 拒）：{_w10}")
_jj.append(("档三", _w10))
_e11, _g11, _w11 = em.match("上海钢联(暂定)", em.default_align)
_assert(_e11 is None and _g11 == em.REJECTED and "暂定" in _w11,
        f"括号里是备注（短于四个字）时不当别名用，也不拿整串去撞档三——"
        f"余段「暂定」不是纯尾缀 → 拒：{_w11}")
_jj.append(("档三", _w11))
_e12, _g12, _w12 = em.match("中国船舶集团第七〇五研究所", em.default_align)
_assert(_e12 is None and _g12 == em.REJECTED and "机构类" in _w12,
        f"机构类主体只认档一，简称与前缀兜底一律禁用（兜底在这种形状上必然出错）：{_w12}")
_jj.append(("机构类", _w12))
# 上一条只验到「账目归到哪一道」（机构类的尾缀不是法律形式词，档三的余段闸本来也会拦一下）。
# 这一条才是那道闸存在的理由：名单里的机构行拿集团/母公司的**证券简称**当备注是常见写法，
# 而档二不看尾缀——少了机构类这道闸，上市公司那个总机就直接挂到研究院头上了。
_e12b, _g12b, _w12b = em.match("某某装备研究院(上海钢联)", em.default_align)
_assert(_e12b is None and _g12b == em.REJECTED and "机构类" in _w12b,
        f"机构类行括号里写着母公司的证券简称时也不得兜底对齐（实得档位 {_g12b or 'NO_MATCH'}｜"
        f"{_w12b}）：这一条红就意味着某家研究院拿到了别人的总机号")
_jj.append(("机构类", _w12b))
# 上一轮真实名录跑出来的第二种错配形状：名单交的是一行**登记名**（带法律形式尾缀），
# 而名录里那三个字的证券简称正好等于被尾缀包着的那段商号。「大自然有限公司」撞上
# 「杭州大自然科技股份有限公司」就是这个形状——三个字不足以定主体，与档三同一道下限。
_e12c, _g12c, _w12c = em.match("夜光微有限公司", em.default_align)
_assert(_e12c is None and _g12c == em.REJECTED and "三个字" in _w12c,
        f"名单写带尾缀的登记名、剥完只剩三个字时不兜底（实得档位 {_g12c or 'NO_MATCH'}｜{_w12c}）："
        "这一条红就意味着某个不相干的同名小公司拿到了别人的董秘办总机")
_jj.append(("档二", _w12c))
_e12d, _g12d, _ = em.match("夜光微", em.default_align)
_assert(_g12d == "abbr" and _e12d is not None,
        "正向对照：名单只写简称本身（不带尾缀）时照收——下限不是「凡三字简称都拒」，"
        "而是「拿三个字的商号去撞一家带尾缀的登记名」（放宽它就是白丢一批能安全对齐的行）")


# 「括号里的简称与全称看不出同一主体」那道闸用生产口径永远撞不到：strip_punct 只去括号
# 本身、留着里面的文字，所以别名必然是 core 的子串。它唯一的可达路径是调用方交了
# 「括号整段不进核心名」的口径（现实中确有这种归一写法，而那正是这道闸存在的前提）。
# 所以这里注入口径把它走一遍，而不是留一段没人验过的代码——与档三那一支同一个道理。
def _align_drop_paren(name):
    """假口径：核心名只取第一个括号**之前**那段（括号里的别名一律不进 core）。"""
    return (em.name_core(re.split(r"[（(]", str(name or ""))[0]),
            em.paren_abbr(name), em.is_institution(name))


_use(_ROWS, align=_align_drop_paren)
_e13, _g13, _w13 = em.match("某某贸易(上海钢联)", _align_drop_paren)
_assert(_e13 is None and _g13 == em.REJECTED and "看不出同一主体" in _w13,
        f"别名与全称看不出同一主体时不放行（拿它去撞名录简称就是凭空攀亲）：{_w13}")
_jj.append(("档二", _w13))
_use(_ROWS)
_re_label = re.compile(r"^(档[一二三]被拒|机构类主体)")
# 闸的条数在这里钉死：新增一道防错配闸就必须同时在这里加一笔、并把它自己的归口一起登记。
# 这一条不是拿实现对自证（那永远红不了）：它是**改动时的闸**——只加了闸没登记归口，
# 报表那一栏就会开始混进「不知道为什么没补」，而这一条当场不肯绿。
_assert(len(_jj) == 9 and all(_re_label.match(_t) for _, _t in _jj),
        f"每一条被拒的说明都能归到某一道闸上（实得 {len(_jj)} 条：{[k for k, _ in _jj]}）："
        "新增一条闸而不登记归口，报表那一栏就会开始混进「不知道为什么没补」")
print("     没这家（NO_MATCH）与护栏（未就位）")
_e14, _g14, _w14 = em.match("怡星机电设备有限公司", em.default_align)
_assert(_e14 is None and _g14 == em.NO_MATCH and "三档都没对上" in _w14,
        f"名录里根本没这家 = NO_MATCH，不是 REJECTED（{_g14 or 'NO_MATCH'}｜{_w14}）："
        "混成一个「没对上」就答不出「这家上市公司为什么没补到」")
_e15, _g15, _w15 = em.match("A公司", em.default_align)
_assert(_e15 is None and _g15 == em.NO_MATCH and "两个字" in _w15,
        f"核心名短于两个字时任何一档都不许对齐（拿「中国」「上海」那种地名段去攀亲必错）：{_w15}")
em.reset_index()
_e16, _g16, _w16 = em.match("宁波夜光电材料股份有限公司", em.default_align)
_assert(_e16 is None and _g16 == em.REJECTED and "未就位" in _w16,
        f"索引没热时 match 不许猜（{_w16}）：这条路的形状是「一次都没问过」，"
        "把它报成「名录里没这家」就是把护栏破坏伪装成业务结论")
_assert(_g14 == _g15 == em.NO_MATCH and _g16 == em.REJECTED
        and em.NO_MATCH == "" and em.REJECTED == "rejected",
        "两个结论各只有一个取值来源（空串=没这家、rejected=被拦下，别处不许再定义第三种）")

# ── F. 读盘与只读头部 ────────────────────────────────────────────────
print("F. 读盘四形状与 status 只读头部")
_p_missing = _path("没有这个文件.json")
_d, _why = em.read_master(_p_missing)
_assert(_d is None and "还没有主数据文件" in _why, f"文件不存在如实报（{_why}）")
_p_bad = _write("坏json.json", "{ 坏 JSON")
_d, _why = em.read_master(_p_bad)
_assert(_d is None and "读不出来" in _why, f"坏文件如实报（{_why}）")
_p_norows = _write("形状不对.json", json.dumps({"as_of": "2026-11-01", "count": 1},
                                              ensure_ascii=False))
_d, _why = em.read_master(_p_norows)
_assert(_d is None and "没有 rows" in _why, f"形状不对如实报（{_why}）")
_p_empty = _write("空表.json", json.dumps({"as_of": "2026-11-01", "count": 0, "rows": []},
                                         ensure_ascii=False))
_d, _why = em.read_master(_p_empty)
_assert(_d is None and "一条记录都没有" in _why, f"零条如实报（{_why}）")
_p_rows = _write("带坏行.json", json.dumps({"as_of": "2026-11-01",
                                           "rows": [_M_CSSC, "坏一行", 3],
                                           "count": 3, "partial": False},
                                          ensure_ascii=False))
_d, _why = em.read_master(_p_rows)
_assert(_d and len(_d["rows"]) == 1 and _d["as_of"] == "2026-11-01",
        "非 dict 的行被剔掉而不是整表作废（文件被人动过一行就本级全体停摆是不成比例的）")
# status「不解析整表」这一条要可观察：故意做一份**头部完好、整表坏掉**的文件。
# 头部读得出就说明只看了那一小段；这时如果它还去解析整表，必然报「读不出来」。
_p_head = _write("头部可读整表坏.json",
                 '{"as_of":"2026-11-02","count":2,"total":9,"pages":1,"partial":true,'
                 '"rows":[{"SECUCODE":"600150.SH"}' + " " * 3000 + "坏掉的一刀")
_st = em.status(_p_head)
_assert(_st["present"] and _st["rows"] == 2 and _st["as_of"] == "2026-11-02"
        and _st["partial"] is True,
        f"status 从文件头那一小段就报得出账（present/rows/as_of/partial）：{_st}")
_d, _why = em.read_master(_p_head)
_assert(_d is None and _why, "反向对照：同一份文件按完整读法是坏的——"
      "所以上面那条绿了只能是没解析整表（而不是「两种读法都读得出」）")
_p_nohdr = _write("被人改过.json", json.dumps({"as_of": "2026-11-03", "count": 1,
                                              "rows": [_M_CSSC]},
                                             ensure_ascii=False, indent=2))
_st2 = em.status(_p_nohdr)
_assert(_st2["present"] and _st2["rows"] == 1,
        f"头部不是我们写的那种紧凑形状时退回完整读法（不是「读不出来」）：{_st2}")
_assert(em.status(_path("根本没有.json"))["present"] is False,
        "status 对「盘上没有」给一句现成的话，不抛异常（界面线程要直接念这一句）")
_age = em._age_days("2026-11-01", today=time.struct_time((2026, 11, 11, 0, 0, 0, 0, 1, -1)))
_assert(_age == 10 and em._age_days("不是日期") is None and em._age_days("") is None,
        "多少天前：认不出日期一律回 None（由调用方按「不知道」处理，不猜一个 0）")

# ── G. refresh：分页、预算、停止、不毁旧数据 ─────────────────────────
print("G. 拉全量（注入 fetcher，不发真包）")


def _pages(n, per=2, total=6):
    """一份「共 total 条、每页 per 条、最多 n 页可拿」的假接口。

    合成名录只有 9 条而有的用例要 25 页，所以取数**取模回收**：不回收的话第 10 页就是
    空页，refresh 会在那里 break，于是「每 N 页报一句进度」这一条永远验不到（页根本没走到）。
    """
    state = {"i": 0}

    def f(params):
        state["i"] += 1
        if state["i"] > n:
            raise OSError("假接口：没有更多页了")
        lo = (state["i"] - 1) * per
        return {"result": {"count": total, "pages": (total + per - 1) // per,
                           "data": [_ROWS[j % len(_ROWS)] for j in range(lo, lo + per)]}}
    return f


# 分页之间那个 sleep 要在**不真睡**的前提下验：拿一层代理挡住 sleep、其余照原样交给
# 真 time 模块（预算与日期那两路还都得走真的）。整节共用它，省掉五秒钟的真等待——
# 这一条验的是「慢的过程看得见 + 页与页之间留间隔」，不是「该睡多久」。
_real_time = em.time


class _PaceSpy:
    def __init__(self):
        self.gaps = []

    def sleep(self, secs):
        self.gaps.append(secs)

    def __getattr__(self, name):
        return getattr(_real_time, name)


_pace = _PaceSpy()
em.time = _pace
try:
    _rf = em.refresh(path=_path("拉完了.json"), fetcher=_pages(3, per=1, total=3),
                     log=lambda m: None)
    _assert(_rf["ok"] and not _rf["partial"] and _rf["count"] == 3 and _rf["reason"] == "",
            f"拉完的形状：ok、不算半截、账上三条、reason 空着（{_rf}）——"
            "下面那几条「半截」判据全靠这一条把「完成」长什么样钉住")
    _assert(_pace.gaps == [em.REFRESH_GAP_S] * 2,
            f"页与页之间各留一次间隔、共页数减一次（实得 {_pace.gaps}）：把公开接口打成压测"
            "是这一级唯一可能伤到别人的地方，而它不在任何返回值里，只能从 sleep 上看")

    # 预算到点：给**负预算**而不是 0——`time.time() - t0 > 0` 要靠前一页真跑出时间差才成立，
    # 把判据挂在时钟精度上就是给自己留一个偶发红。负预算让它只看「已经拉过至少一页」。
    _p_g = _path("预算到点.json")
    _logs_g = []
    r = em.refresh(path=_p_g, fetcher=_pages(5), log=_logs_g.append, budget=-1.0)
    _assert(r["ok"] and r["count"] == 2 and r["pages"] == 1 and r["partial"]
            and "预算到点" in r["reason"],
            f"预算到点时把已拿到的落盘并在账上说明为什么只有半截（{r['count']} 条 / "
            f"{r['pages']} 页｜{r['reason']}）：「半截主数据」与「没有主数据」下一步完全不同")
    _assert(any("剩下的下一轮接着拉" in m for m in _logs_g),
            f"半截落盘要**说在日志里**（{_logs_g[-1:]}）：只在返回值里带 partial，"
            "用户看到的就是一句「拉好了」而不知道名录只有几分之一")
    em.reset_index()
    _dg, _wg = em.read_master(_p_g)
    _assert(_dg and len(_dg["rows"]) == 2 and _dg["partial"] is True,
            f"落盘的是真文件、partial 记在盘上（不是只在返回值里）：{_dg and len(_dg['rows'])}")
    _assert(not Path(_p_g + ".tmp").exists(),
            "原子写不留 tmp（读半个 JSON 的线程会安静当「没有主数据」）")

    # 进度那句账：整轮约 50 页，而它跑在工作线程里——一声不响五十几秒就是用户报的转圈。
    _p_slow = _path("慢一点.json")
    _logs_s = []
    em.refresh(path=_p_slow, fetcher=_pages(25, per=1, total=25), log=_logs_s.append)
    _p_prog = [m for m in _logs_s if "拉取中" in m]
    _assert(len(_p_prog) == 2,
            f"25 页恰好报两句（第 10、第 20 页），剩下的归收尾那句总数账（{_p_prog}）："
            "一句都不报的话，「正在拉」与「卡死」在界面上长得一模一样")
    _assert(_logs_s and "已落盘" in _logs_s[-1] and "25 条 / 25 页" in _logs_s[-1],
            f"收尾有一句总数账，条数与页数都对得上（{_logs_s[-1:]}）")
    _p_keep = _write("已有好数据.json", json.dumps({"as_of": "2026-11-01", "count": 1,
                                                   "rows": [_M_CSSC],
                                                   "partial": False}, ensure_ascii=False))

    def _boom(params):
        raise OSError("接口挂了")

    r2 = em.refresh(path=_p_keep, fetcher=_boom, log=lambda m: None)
    _assert(not r2["ok"] and r2["count"] == 0 and "第 1 页拉取失败" in r2["reason"],
            f"第一页就失败：如实报第几页（{r2['reason']}）")
    _dk, _wk = em.read_master(_p_keep)
    _assert(_dk and len(_dk["rows"]) == 1,
            "失败不毁旧数据：一份能用的主数据不能被一次坏拉取写成空文件（那是把能补的行全判死）")
    _assert(not Path(_p_keep + ".tmp").exists(), "失败路径也要清掉 tmp")
    _stops = {"n": 0}

    def _paged_slow(params):
        _stops["n"] += 1
        return {"result": {"count": 100, "pages": 50, "data": [_M_CSSC]}}

    _p_stop = _path("被停止.json")
    r3 = em.refresh(path=_p_stop, fetcher=_paged_slow,
                    stop=lambda: _stops["n"] >= 3, log=lambda m: None)
    _assert(r3["ok"] and r3["partial"] and "被停止" in r3["reason"],
            f"被停止时账上算「半截」而不是「完成」（{r3['count']} 条 / {r3['pages']} 页｜"
            f"{r3['reason']}）：用户按了停止，已花掉的请求要留下能用的那部分，但下一步还得接着拉")
    _assert(_stops["n"] == 3 == r3["count"],
            f"stop 生效在页与页之间、下一次就不再发请求（实发 {_stops['n']} 次、落盘 "
            f"{r3['count']} 条）：按了停止还多要几页，等于把用户的停止按钮当建议")
    _dk3, _ = em.read_master(_p_stop)
    _assert(_dk3 and _dk3["partial"] is True,
            "停止那一轮的半截也记在盘上（否则下一轮无从判断要不要接着拉）")
finally:
    em.time = _real_time

_r_shape = em.refresh(path=_path("形状.json"), fetcher=_pages(1, per=2, total=2),
                      log=lambda m: None)
_assert(sorted(_r_shape) == sorted(["ok", "count", "total", "pages", "as_of",
                                    "partial", "reason"]),
        f"refresh 的账目形状固定（{sorted(_r_shape)}）："
        "ensure 与 stage_lines 都是照这七个键说话的")
# 「拉到了但落不了盘」：把落点指到一个**已存在的目录**上——临时文件写得出去，原子替换
# 那一步必然失败。真盘满在离线沙箱里造不出来，而这一支是唯一能把「重试」与「查磁盘」
# 分开的出口，不能因为不好造就不验。
_p_dir = _TMP / "目录占位"
_p_dir.mkdir(exist_ok=True)
r4 = em.refresh(path=str(_p_dir), fetcher=_pages(1, per=2, total=2), log=lambda m: None)
_assert(not r4["ok"] and "写不下去" in r4["reason"] and r4["count"] == 2,
        f"拉到了但落不了盘：报的是「拉到了但写不下去」而不是「一页都没拿到」（{r4['reason']}）："
        "两种形状下一步完全不同（重试 vs 查磁盘）")
_assert(not Path(str(_p_dir) + ".tmp").exists(), "写失败也要清掉 tmp（不留一次比一次旧的残骸）")

# ── H. ensure：快路径、口径身份、重试闸 ─────────────────────────────
print("H. ensure（快路径 / 口径身份 / 重试闸）")
_reads, _builds = [], []
_real_read, _real_build = em.read_master, em.build_index


def _spy_read(*a, **k):
    _reads.append(1)
    return _real_read(*a, **k)


def _spy_build(*a, **k):
    _builds.append(1)
    return _real_build(*a, **k)


em.read_master, em.build_index = _spy_read, _spy_build
try:
    _p_h = _write("就位.json", json.dumps({"as_of": time.strftime("%Y-%m-%d"),
                                          "count": len(_ROWS), "rows": _ROWS,
                                          "partial": False}, ensure_ascii=False))
    em.reset_index()
    _e_first = em.ensure(path=_p_h, align=em.default_align, allow_fetch=False)
    _reads.clear()
    _builds.clear()
    _e_second = em.ensure(path=_p_h, align=em.default_align, allow_fetch=False)
    _assert(_e_first["present"] and _e_second["present"]
            and _e_first["count"] == _e_second["count"] == 8,
            f"两次都拿到同一份账（8 家主体）：{_e_second['count']}")
    _assert(not _reads and not _builds,
            f"盘没动、口径没变时快路径**连文件都不开**（读盘 {len(_reads)} 次、"
            f"建索引 {len(_builds)} 次）：边采边补每 60 秒问一次，两万多条解析两秒多")
    _assert(all(k in _e_second for k in ("present", "count", "as_of", "age_days",
                                         "partial", "fetched", "reason")),
            "ensure 交回的是那七个键（本级「为什么一行都没补」永远有一句现成的话）")
    # 口径对象换了：必须重建。这里比的是 `is`，不是「实现是否等价」——
    # 等价判断做不了，而漏掉这一步就会拿旧口径的键去接新口径的查询。
    _builds.clear()
    em.ensure(path=_p_h, align=_align_stub, allow_fetch=False)
    _assert(len(_builds) == 1,
            f"换了口径对象就重建索引（建索引 {len(_builds)} 次）：静默沿用旧键时，"
            "同一个名字在两条路上算出两个键而账上完全看不出来")
    # 盘上没有 + 不许拉取：一句现成的话，且一个包都不发。
    em.reset_index()
    _calls = {"n": 0}

    def _counting_fetcher(params):
        _calls["n"] += 1
        return {"result": {"count": 1, "pages": 1, "data": [_M_CSSC]}}

    _e_none = em.ensure(path=_path("盘上没有.json"), align=em.default_align,
                        allow_fetch=False, fetcher=_counting_fetcher)
    _assert(not _e_none["present"] and "还没有主数据文件" in _e_none["reason"]
            and _calls["n"] == 0,
            f"allow_fetch=False 时本级如实退出且不出网（{_e_none['reason']}）")
    # AUTO_FETCH 那道全局兜：allow_fetch 不传时取它。
    _e_auto = em.ensure(path=_path("盘上没有.json"), align=em.default_align,
                        fetcher=_counting_fetcher)
    _assert(_calls["n"] == 0 and _e_auto["reason"],
            "AUTO_FETCH=False 时连「调用方没表态」都不许出网（离线套件的护栏就挂在这上面）")
    # 重试闸：拉失败之后，同进程内 RETRY_GAP_S 之内不许重开第二轮。
    em.reset_index()
    _fails = {"n": 0}

    def _failing_fetcher(params):
        _fails["n"] += 1
        raise OSError("假接口坏了")

    _e_try1 = em.ensure(path=_path("重试闸.json"), align=em.default_align,
                        allow_fetch=True, fetcher=_failing_fetcher)
    _e_try2 = em.ensure(path=_path("重试闸.json"), align=em.default_align,
                        allow_fetch=True, fetcher=_failing_fetcher)
    _assert(_fails["n"] == 1,
            f"第一次试过之后，第二次不再拉全量（实发 {_fails['n']} 轮）：少了这道闸，"
            "边采边补每 60 秒就会重开一轮五十几秒的全量——把免费的一级做成新的转圈来源")
    _assert("分钟前试过" in _e_try2["reason"] and "第 1 页拉取失败" in _e_try2["reason"],
            f"重试闸那一轮仍然把上一次的真结论念出来（{_e_try2['reason']}）："
            "只说「本地还没有主数据文件」是在把护栏破坏伪装成业务结论；而念的必须是"
            "refresh 报的那一句（第几页失败），异常对象自带的中文不在上报路上")
    em.reset_index()
    em.ensure(path=_path("重试闸.json"), align=em.default_align,
              allow_fetch=True, fetcher=_failing_fetcher)
    _assert(_fails["n"] == 2,
            "reset_index 把重试账一起清掉（不清的话第二轮离线用例验的就不是「没试过」）")
    # 拉到半截时盘上那份照用，只是不再重试。
    em.reset_index()
    _p_half = _path("半截.json")
    em.refresh(path=_p_half, fetcher=_pages(5), log=lambda m: None, budget=-1.0)
    em.reset_index()
    _half_calls = {"n": 0}

    def _count2(params):
        _half_calls["n"] += 1
        raise OSError("坏了")

    _e_half = em.ensure(path=_p_half, align=em.default_align, allow_fetch=True,
                        fetcher=_count2)
    _e_half2 = em.ensure(path=_p_half, align=em.default_align, allow_fetch=True,
                         fetcher=_count2)
    _assert(_e_half["present"] and _e_half["partial"] and _e_half["fetched"]
            and _half_calls["n"] == 1,
            f"第一问：盘上有半截能用的就照用，而且这一问**真的试过一次**（"
            f"present={_e_half['present']} partial={_e_half['partial']} "
            f"fetched={_e_half['fetched']}、请求 {_half_calls['n']} 次）")
    _assert(_e_half2["present"] and not _e_half2["fetched"] and _half_calls["n"] == 1
            and "沿用盘上那份" in _e_half2["reason"],
            f"第二问：重试闸拦下第二轮，但半截那份照旧交付并说清为何没重试"
            f"（{_e_half2['reason']}）：半截主数据比「没有」强得多，而不重试的代价必须看得见")
finally:
    em.read_master, em.build_index = _real_read, _real_build

# ── I. 接口面与收尾 ──────────────────────────────────────────────────
print("I. 接口面")
_missing = [n for n in em.__all__ if not hasattr(em, n)]
_assert(not _missing, f"__all__ 里没有空头承诺（缺：{_missing}）")
_odd = {n: type(getattr(em, n)).__name__ for n in em.__all__
        if not callable(getattr(em, n))
        and not isinstance(getattr(em, n), (str, int, float, tuple, frozenset, dict))}
_assert(not _odd,
        f"导出的每一项要么是函数、要么是纯数据（常量/元组/映射；异常形状：{_odd}）："
        "模块、类、句柄那一类带状态的对象不该出现在出口名单里——调用方拿到的就不是一个值，"
        "而是一能改坏、二看不见谁在改的共享现场")
_foreign = [n for n in em.__all__ if callable(getattr(em, n))
            and getattr(getattr(em, n), "__module__", "") != em.__name__]
_assert(not _foreign,
        f"导出的函数都定义在本模块里（外来货：{_foreign}）：从 website_contact 顺手转导出的"
        "名字不会随本模块一起被读代码的人审到，形同开后门")
_assert(em.AUTO_FETCH is False,
        "跑到这里 AUTO_FETCH 必须还是本套件关掉的那份（中途没人摸开它）")
_assert(em.read_master is _real_read and em.build_index is _real_build,
        "H 段那两个读盘/建索引探针已还原（留在上面，后面每一套件的读盘计数都是假的）")
em.AUTO_FETCH = _real_auto_fetch
_assert(em.AUTO_FETCH is _real_auto_fetch is True,
        "收尾把 AUTO_FETCH 交还真值：它是全仓最外层那道兜，留在 False 上，下一套件的"
        "拉取判据就全是假绿")
em.reset_index()
_assert(em.index() is None, "收尾复位：下一个套件拿到的是「没读过盘」而不是本套件的索引")
_assert(not _net_trips, f"全程零 DNS 零 HTTP（实查：{_net_trips[:3]}）")

print("\n" + "=" * 60)
total_n = _passed + _failed
print((f"全部通过 ✅ ({_passed}/{total_n})") if _failed == 0
      else f"通过 {_passed}/{total_n}，失败 {_failed} ❌")
print("=" * 60)
sys.stdout.flush()
sys.stderr.flush()
os._exit(1 if _failed else 0)
