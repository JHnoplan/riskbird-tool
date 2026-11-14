# -*- coding: utf-8 -*-
"""test_waterfall_leads_offline.py —— 质量四件套（B/I/J/K）+ 瀑布式多源补全（A）离线回归。

这五个功能合起来是把工具从「一次采一张表」变成「会自己补、会自己排序、会自己刷新的资产」。
它们的正确性**全部在判据里**，不在抓取动作里，所以本套件按
「口径同源 → 证据编解码 → TTL → 瀑布四级 → 评分分层 → 通道健康 → 会话 → 界面」八段钉，
全程不联网（fetch_html / socket.getaddrinfo / requests.get 三处都换成表驱动或硬闸）：

  A. 层级注册表与阶段表同源：名字只许一套；STAGES 按成本升序（Clay 那条「顺序比源重要」
     的可执行化）；可信分与成本**分开**登记（混成一个数就会出现为省钱把低可信排前面）
  B. 证据两格的编解码：往返、全角分隔符、只升级不降级、清空值同步清层级、按段裁剪不留半句
  C. 最后核验时间：与采集时间**分家**（正反两个错各钉一次）、无时间戳一律算到期（fail-closed）、
     层级倍率 registry 放宽 / source 收紧
  D. 同主体键、邮箱反查、品牌 token、候选域名提议（免费邮箱域不算、短缩写不拼、平台页不算官网）
  E. carry_pass：零请求补齐、**继承 donor 的层级与核验时间**（2026-11-14 自查抓到的降级卖号 bug）、
     只追加不覆盖、重跑幂等
  F. pick_targets：命中即停不花请求、勾重验后过期行进队列、不勾时一律不进
  G. workable_count：三口径并集（单跳数不到的行必须数到）——界面拦不拦按钮就靠这个数
  H. run_waterfall：五级顺序、账目齐全、registry 投递走注入回调并封顶、
     任一级异常只影响那一级（不许冒泡把已采数据一起丢掉）
  I. snapshot/diff：新增 / 补充 / **改写(异常)** 三种变化形态
  J. 评分与分层：三维权重、硬可信闸、过期打折且闸掉立即跟进、依据列可解释、
     「多新看采集时间、过没过期看核验时间」两条判据各钉一次正反
  K. 同主体归并：并集 + 逐字段取最高层并带出处、公司名不许空、核验时间不捏造、
     归并不了的行要在日志里说清为什么报表少几行
  L. 报表：四张 sheet、列与表头一一对应、**客户入库模板 9 列一概不动**
  M. 通道健康：家数按同主体去重（不是行数）、「没跑」与「零产出」分开、红牌首次跨阈值只警一次、
     坏台账文件的 error 必须被念出来（静默清零＝红牌永远不再亮）、未算分印「—」不印 0
  N. 会话层：引擎默认三关全关（离线护栏）、configure 下发生效、两条支路都出一张报表
     （静默少功能）、报表失败不影响主交付、两种账目形态的摘要口径同源
  O. 界面层：三个勾选恒定可见（档位藏不住口径开关）、默认值、值/控件/会话三处同步、
     依赖级联与前置回弹、快照恢复不许弹模态框、话术跟着口径换

运行：python test_waterfall_leads_offline.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))

# 状态落点钉进沙箱：本套件会真造 MainWindow、真落报表，绝不能写进开发机的真实任务目录
_STATE_SANDBOX = Path(tempfile.mkdtemp(prefix="wfl_state_"))
os.environ["B2B_STATE_DIR"] = str(_STATE_SANDBOX)
_DESK_SANDBOX = Path(tempfile.mkdtemp(prefix="wfl_desk_"))
os.environ["B2B_DESKTOP_DIR"] = str(_DESK_SANDBOX)

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


import field_evidence as fe                                          # noqa: E402
import enrich_waterfall as wf                                        # noqa: E402
import lead_scoring as ls                                            # noqa: E402
import channel_health as ch                                          # noqa: E402
import website_contact as wc                                         # noqa: E402

# ── 离线硬闸：本套件一次真请求都不该发出 ─────────────────────────────
# 分清两类「碰网络」的动作：DNS 解析是 SSRF 闸（is_public_host）的固有判据，
# 它在解析失败时按「解析不了 ≠ 危险」放行给 fetch 层去报不可达；而 HTTP/页面抓取
# 才是这个功能真正花的钱。所以下面两套账分开记，E 段那句「第一级一行请求都没发」
# 只该管后者——把前者也算成违规，判据就成了假的（真机上照样会查 DNS）。
_net_trips = []       # 任何网络邻近动作（含 DNS）
_http_trips = []      # 真·出网抓取尝试（HTTP / fetch_html）
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
        _http_trips.append(str(a[0] if a else ""))
        raise AssertionError("离线用例真发出了 HTTP 请求")

    _requests.get = _tripwire_get
except Exception:                                     # requests 不在也照样跑
    _requests = None
    _real_requests_get = None

_real_fetch = wc.fetch_html


def _blocked_fetch(url, timeout=None):
    _net_trips.append(str(url))
    _http_trips.append(str(url))
    raise AssertionError(f"离线用例真去抓页面了：{url}")


wc.fetch_html = _blocked_fetch

# run_waterfall 的网络级由本表接管（打桩而不是真抓）：只记调用次序与参数，
# 把「第 2~4 级确实被问过、第 1 级确实没花请求」变成可核对的账。
# eligible/sites 按**传进来的行数**如实回，不用固定值：桩如果无论收到几条都报 0，
# 下面那句「网络级有账目」就永远在自我循环（桩替被测代码编数字，是假绿的标准形状）。
_enrich_calls = []


def _stub_enrich(records, log=None, **kw):
    _enrich_calls.append(dict(kw))
    return {"eligible": len(records), "sites": len(records), "ok": 0, "added": 0,
            "new_values": 0, "cached": 0, "no_site": 0, "reasons": {"夹具：一律不出值": len(records)},
            "budget_hit": 0, "marked": 0, "retry": 0, "skipped": 0,
            "seconds": 0, "verified": 0}


_real_enrich = wc.enrich_records
wc.enrich_records = _stub_enrich


def _rec(name, **kw):
    """造一条采集记录：默认补齐四个留痕/证据键，形状与 _base_rec 一致。"""
    r = {"公司名称": name, "数据来源": kw.pop("source", "测试站"),
         "采集时间": kw.pop("at", "2026-11-01"), "公司网址": kw.pop("site", ""),
         "电话": kw.pop("电话", ""), "手机": kw.pop("手机", ""), "邮箱": kw.pop("邮箱", ""),
         fe.EVIDENCE_COL: kw.pop(fe.EVIDENCE_COL, ""), fe.PROOF_COL: kw.pop(fe.PROOF_COL, ""),
         fe.VERIFIED_COL: kw.pop(fe.VERIFIED_COL, "")}
    r.update(kw)
    return r


# ── A. 层级注册表与阶段表同源 ───────────────────────────────────────
print("A. 层级与阶段口径同源")
_assert([s for s, *_ in fe.TIERS] and set(wf.STAGES) <= {s for s, *_ in fe.TIERS},
        "阶段表里的 slug 全部在层级注册表里（不存在只有瀑布认、评分不认的层）")
_assert(all(wf.stage_name(slug) == fe.TIER_LABEL[slug] for slug in wf.STAGES),
        "阶段中文名**现查**层级表（以前这里有一张导入期快照，同一个名字两处写法）")
assert not hasattr(wf, "STAGE_LABELS"), \
    "STAGE_LABELS 那张快照必须真的不在：留着就有两条路可问同一个问题，总有人会走错那条"
_x = _rec("甲节夹具公司", 电话="021-57655345")
_assert(fe.set_field_evidence(_x, "电话", "mailsite"), "夹具成立：有值的字段才写得上层级")
_assert(wf.stage_name("mailsite") in _x[fe.EVIDENCE_COL],
        f"跨模块自证：瀑布阶段名「{wf.stage_name('mailsite')}」正是证据列里写出去的那一个"
        f"（报表与原始表对不上号就是这么产生的）")
_costs = [fe.TIER_COST[slug] for slug in wf.STAGES]
_assert(_costs == sorted(_costs),
        f"STAGES 按成本升序执行（{wf.STAGES} → 成本 {_costs}）：这是「顺序决定账单」那条结论的落点")
_assert(len({fe.TIER_SCORE[s] for s, *_ in fe.TIERS}) > 1
        and not all(fe.TIER_SCORE[a] < fe.TIER_SCORE[b]
                    for a, b in zip(wf.STAGES, wf.STAGES[1:])),
        "可信分与成本是**分开**登记的：按成本排出来的顺序里可信度并不单调（混成一个数才是错）")
_assert(fe.CONFIDENT_MIN == fe.TIER_SCORE["site"],
        "命中即停的闸取自「官网直采」那一层，不是另拍的数")
_assert(fe.evidence_columns() == (fe.EVIDENCE_COL, fe.PROOF_COL, fe.VERIFIED_COL),
        "证据三列的名字与顺序只有一个来源")
_assert(fe.CONFIDENT_MIN == ls.WORK_CONF_MIN,
        "评分「立即跟进」的可信闸与瀑布的命中即停闸同一个数（两处各判就会自相矛盾）")

# ── B. 证据两格的编解码 ─────────────────────────────────────────────
print("B. 证据编解码")
_r = _rec("甲有限公司", 电话="021-57655345", 邮箱="sales@mingliang-led.com")
_assert(fe.set_field_evidence(_r, "电话", "site", "官网 contact 页原文"), "写入层级返回 True")
_assert(fe.set_field_evidence(_r, "邮箱", "carry", "同主体另一行（甲）"), "第二个字段可独立写入")
got = fe.parse_evidence(_r)
_assert(got.get("电话") == (fe.TIER_LABEL["site"], "site")
        and got.get("邮箱") == (fe.TIER_LABEL["carry"], "carry"),
        f"往返解析出「字段→(中文名, slug)」：{_r[fe.EVIDENCE_COL]}")
_assert(fe.parse_proof(_r).get("电话") == "官网 contact 页原文", "出处与层级成对读回")
_assert(fe.set_field_evidence(_r, "电话", "brand", "拼出来的站") is False,
        "只升级不降级：82 分的层不被 62 分覆盖（后跑一轮把数据「写差」是最难解释的变更）")
_assert(fe.set_field_evidence(_r, "电话", "registry", "工商登记") is True,
        "升级到 90 分放行")
_assert(fe.field_confidence(_r, "电话") == 90 and fe.row_confidence(_r) == 90,
        "升级后可信分跟着走（取最高：外呼只要一个能打的号）")
_empty = _rec("空值有限公司", 电话="", 手机="", 邮箱="")
_assert(fe.set_field_evidence(_empty, "邮箱", "site", "官网原文") is False
        and _empty[fe.EVIDENCE_COL] == "",
        "值为空时拒绝记层级：以前这条只写在三个调用方各自的注释里（靠约定不设置），"
        "漏一次就造出「邮箱:官网直采」而邮箱格为空——那种格子会让人不信任整个证据列")
_bad = {fe.EVIDENCE_COL: f"电话：{fe.TIER_LABEL['site']}|邮箱:不存在的层|乱写的东西"}
_assert(fe.parse_evidence(_bad).get("电话") == (fe.TIER_LABEL["site"], "site")
        and "邮箱" not in fe.parse_evidence(_bad),
        f"手改过的表：全角冒号照读（{_bad[fe.EVIDENCE_COL]}），"
        "认不出的层级忽略而不报错（用户手改的表必须能读）")
_assert(fe.set_field_evidence(_r, "传真", "site") is False
        and fe.set_field_evidence(_r, "电话", "不存在的层") is False,
        "未知字段/未知层级一律拒绝：逼调用方先去注册表登记，而不是安静写出一列认不出的值")
fe.clear_field_evidence(_r, "电话")
_assert("电话" not in fe.parse_evidence(_r) and "电话" not in fe.parse_proof(_r),
        "值清空时层级与出处一起清（留着就是「没号却写着官网核验」）")
_assert(fe.field_confidence(_r, "电话") == fe.TIER_SCORE["source"]
        and fe.TIER_LABEL["site"] not in _r[fe.EVIDENCE_COL],
        "清掉层级后有值的字段退回「平台自带」兜底，且证据列里不再留着那一层")
_long = _rec("乙有限公司", 电话="021-57655345", 手机="13800001111",
             邮箱="sales@mingliang-led.com")
for _i, _f in enumerate(fe.CONTACT_FIELDS):
    fe.set_field_evidence(_long, _f, "site", "很长的出处" * 40)
_assert(len(_long[fe.PROOF_COL]) <= fe.PROOF_MAX,
        f"证据串封顶 {fe.PROOF_MAX}（Excel 截断的证据比没证据更误导人）")
_segs = [p for p in _long[fe.PROOF_COL].split("|") if p.strip()]
_assert(_segs and all(len(fe.parse_proof({fe.PROOF_COL: p})) == 1 for p in _segs),
        f"裁剪后每一段都还是完整的「字段:出处」（{_segs}）：宁可少一个字段，也不留半句")
_assert(len(fe.parse_proof(_long)) < len(fe.CONTACT_FIELDS),
        "夹具成立：三字段全写时长到必须掉一整段（不掉段的话上面那句验的是不截断的情况）")

# ── C. 最后核验时间与 TTL ──────────────────────────────────────────
print("C. 核验时间与 TTL（分家判据）")
_c1 = _rec("丙有限公司", **{fe.VERIFIED_COL: "2026-01-01", "采集时间": "2026-11-10"})
_assert(fe.verified_at(_c1) == "2026-01-01", "有核验列就取核验列")
_c2 = _rec("丁有限公司", **{fe.VERIFIED_COL: ""})
_assert(fe.verified_at(_c2) == "2026-11-01",
        "没有核验列退回采集时间（历史表必需：全按「未知」处理会把几千行一次性推进重验队列）")
_c3 = _rec("戊有限公司", **{"采集时间": "", fe.VERIFIED_COL: ""})
_assert(fe.verified_at(_c3) == "", "两列都没有＝未知（空串，不假装是今天）")
_before_keys = set(_c1)
fe.stamp_verified(_c1, "2026-11-12 09:00:00")
_assert(_c1[fe.VERIFIED_COL] == "2026-11-12 09:00:00" and set(_c1) == _before_keys,
        "stamp_verified 只写时间戳那一格，不顺手改别的列")
fe.stamp_verified(_c2)
_assert(str(_c2[fe.VERIFIED_COL])[:4].isdigit() and len(_c2[fe.VERIFIED_COL]) >= 10,
        f"不传时间＝当场盖今天的章（重验到同一个号必须刷新，否则那行每轮被重问）")
_today = "2026-11-14"
_assert(fe.is_stale("电话", "site", "", today=_today) is True,
        "从没记过核验时间＝算到期（读成「还新」等于这功能永远不生效）")
_assert(fe.is_stale("电话", "site", "看不懂的时间", today=_today) is True,
        "格式认不出来同样算到期（fail-closed 的同一侧）")
_assert(fe.is_stale("电话", "site", "2026-11-01", today=_today) is False,
        "13 天的官网号未过期（电话 150 天）")
_d1, _d2 = fe.ttl_days("电话", "registry"), fe.ttl_days("电话", "source")
_assert(_d1 > fe.ttl_days("电话", "site") > _d2,
        f"层级倍率生效：工商 {_d1} 天放宽、平台自带 {_d2} 天收紧（登记信息改得慢）")
_assert(fe.ttl_days("邮箱", "site") == fe.TTL_DAYS["邮箱"],
        "未登记的层级按 1.0 倍率（不因为查不到就偷偷给个默认天数）")
_assert(fe.field_age_days("2026-11-04 18:00:00", _today) == fe.field_age_days("2026-11-04", _today),
        "带不带时分秒的两种历史写法算出同一个天数（按字符串比会得出假先后）")
import datetime as _dtm                                                    # noqa: E402
_today_obj = _dtm.date(2026, 11, 14)
_assert(fe.field_age_days("2026-11-01", _today_obj) == 13
        and fe.is_stale("电话", "site", "2026-11-01", today=_today_obj) is False,
        "基准日传 date 对象同样成立（两种写法都必须是合法入参）")
_assert(fe.field_age_days("2026-01-01", "看不懂") is None
        and fe.is_stale("电话", "site", "2020-01-01", today="看不懂") is True,
        "基准日传了但认不出来＝算不出天数并按到期处理：退化成「当作今天」会把整表"
        "过期判据读成都还新，而这条路径包在报表导出的 try/except 里，只会静默少功能")

# ── D. 同主体键 / 邮箱反查 / 品牌 token ─────────────────────────────
print("D. 认亲与官网发现的前置判据")
_k1 = wf.company_key(_rec("上海明亮照明有限公司"))
_k2 = wf.company_key(_rec("上海明亮照明 有限公司 "))
_k3 = wf.company_key(_rec("上海明亮照明有限公司（总部）"))
_assert(_k1 and _k1 == _k2, f"空白/尾随空格这类写法差异归一到同一个键（{_k1}）")
_assert(_k1 != _k3, f"「（总部）」这种带分支标记的名字**不**并进主体键（{_k1} ≠ {_k3}）："
        "归一只能是保守的——认得粗的后果是少复用几家（漏补），"
        "认得错的后果是把总部的号盖到分公司头上（错补），后者绝不接受")
_assert(wf.company_key(_rec("")) == "", "认不出返回空串，调用方必须跳过而不是硬凑")
_assert(wf.company_key(_rec("深圳市张三电子厂")) != wf.company_key(_rec("深圳市李四电子厂")),
        "不同主体绝不相等（按域名搬值的错补就是这么来的）")
_assert(wf.email_domains(_rec("A", 邮箱="sales@mingliang-led.com")) == ["mingliang-led.com"],
        "企业邮箱域名反查得到注册域")
_assert(wf.email_domains(_rec("B", 邮箱="abc@qq.com, xyz@163.com")) == [],
        "免费邮箱域不参与反查（否则会把腾讯总机当成某五金厂的电话）")
_assert(wf.email_domains(_rec("C", 邮箱="not-an-email")) == [], "认不出的邮箱不参与")
_bt = wf.brand_tokens(_rec("D", 详情URL="https://abc123shop.11467.com/123.html"))
_assert("abc123shop" in _bt, f"品牌段取自店铺前缀：{_bt}")
_assert("11467" not in _bt, f"平台主域不进候选（拿它拼域名就是把平台当厂家）：{_bt}")
_assert("abc" not in wf.brand_tokens(_rec("E", 详情URL="https://abc.11467.com/x.html")),
        "3 位以下缩写不拼域名（撞车概率太高，拼回来必然不是那一家）")
_assert(wf.brand_tokens(_rec("F", 公司名称="ABCD 五金厂有限公司")) == ["abcd"],
        "英文商号可作候选（只提议，采信权在归属核验）："
        "这一条原来恒为 0——上一版把大写段先按「只留小写字母」清洗再判长度，"
        "而登记英文名几乎全是大写，于是第 4 级对最常见的形状整年不出候选，"
        "日志里只看到「品牌层 0 家」，没人会读成「功能没生效」")
_assert(wf.brand_tokens(_rec("F2", 公司名称="ABC 五金厂")) == [],
        "3 个字母的商号不拼域名：够长的英文段才可能是商号，短缩写拼回来的站几乎必然是别人")
_assert(wf.brand_tokens(_rec("F3", 公司名称="SHENZHEN HUAWEI TECHNOLOGIES CO.,LTD"))
        == ["huawei", "shenzhen"],
        "公司形式词（TECHNOLOGIES/CO/LTD）剔掉、地名段降到末尾但**不删**："
        "确有以地名注册为商号的小公司，删掉就是把它们判死")
_assert(wf.discover_site(_rec("F4", 公司名称="SHENZHEN HUAWEI TECHNOLOGIES CO.,LTD"))
        == ("https://huawei.com/", "brand"),
        "第一个候选是商号而不是地名（否则每条记录先白烧一次 shenzhen.com 再被归属核验拒绝）")
_ds = wf.discover_site(_rec("G", 邮箱="info@mingliang-led.com"))
_assert(_ds == ("https://mingliang-led.com/", "mailsite"),
        f"邮箱域排在品牌拼域名前面（同一档成本里它更可信）：{_ds}")
_assert(wf.discover_site(_rec("H", 邮箱="a@qq.com", 公司名称="张三五金店")) == ("", ""),
        "只有免费邮箱 + 纯中文商号＝认不出官网（返回空而不是硬拼一个站）")
_assert(wf.discover_site(_rec("I", 邮箱="sales@1688.com"))[1] != "mailsite",
        "平台域名当邮箱域时不走 mailsite（那是平台客服号的入口）")
_res = wf.resolver_for([_rec("J", site="www.mingliang-led.com/contact"),
                        _rec("J", 邮箱="sales@mingliang-led.com")])
_url, _slug = _res(_rec("J", 邮箱="sales@mingliang-led.com"))
_assert(_url == "https://www.mingliang-led.com/contact" and _slug == "mailsite",
        f"同域名另一行本来带着官网→借那个现成 URL，但来源仍是推导（记 mailsite 不记 site）：{(_url, _slug)}")
_assert(isinstance(wf.registry_source_names(), set) and wf.registry_source_names(),
        "工商三路的来源名取自现成的通道标签（不写第二份站名）")
_rn = sorted(wf.registry_source_names())[0]
_rg = _rec("工商来源公司", source=_rn, 电话="021-57655345")
_assert(wf.stamp_source_evidence([_rg]) == 1
        and fe.parse_evidence(_rg).get("电话", ("", ""))[1] == "registry"
        and _rn in fe.parse_proof(_rg).get("电话", ""),
        f"来源是「{_rn}」的既有值被标成工商核验层（90 分）而不是平台自带（55 分）："
        "把最好的数据判成最差的卖，排序就失真了，且没有任何一条报错会暴露它")
_assert(wf.stamp_source_evidence([_rg]) == 0, "重跑幂等：已记过层级的行不再改写出处")

# ── E. carry_pass：免费的第一级 ─────────────────────────────────────
print("E. 同主体复用（第一级）")
_donor = _rec("same-co 明亮照明有限公司", 手机="13800001111")
fe.set_field_evidence(_donor, "手机", "registry", "工商登记（企知道）")
fe.stamp_verified(_donor, "2026-11-05")
_taker = _rec("same-co 明亮照明有限公司", 电话="021-57655345")
_stat = wf.carry_pass([_donor, _taker])
_assert(_taker["手机"] == "13800001111", "同主体另一行的手机补进来了")
_assert(_donor["电话"] == "021-57655345", "反方向同样成立：复用池是整组行共用的，不是只让后一行拿前一行的值")
_assert(_stat["filled"] == 2 and _stat["values"] == 2
        and _stat["by_field"] == {"电话": 1, "手机": 1}, f"账目按字段分开记：{_stat}")
_assert(fe.parse_evidence(_taker).get("手机") == (fe.TIER_LABEL["registry"], "registry"),
        f"继承 donor 的层级而不是降级成 carry（{_taker[fe.EVIDENCE_COL]}）"
        "——把工商核验的号当中等数据卖，会连带把「立即跟进」闸压进培育池")
_assert("工商登记" in fe.parse_proof(_taker).get("手机", ""),
        "出处一并带过来（顺着能查回 donor）")
_assert(_taker[fe.VERIFIED_COL] == "2026-11-05",
        "继承 donor 的核验时间（留空会让这一行永远算过期，免费一级变成烧预算一级）")
_assert(_donor[fe.VERIFIED_COL] == "2026-11-05",
        "反向：donor 借了别行的座机也不把自己的核验时间刷成那行的旧时间（把自己的章盖没了，"
        "下一轮又会把它排进重验队列）")
_n = wf.carry_pass([_donor, _taker])
_assert(_taker["手机"] == "13800001111" and _n["filled"] == 0,
        "重跑幂等：已有的值不重复计、不重复追加")
_solo = _rec("独占一人有限公司", 邮箱="a@solo-xyz.com", 电话="")
wf.carry_pass([_donor, _solo])
_assert(_solo["邮箱"] == "a@solo-xyz.com" and _solo["电话"] == "",
        "没有同主体来源时原值一字不动、缺的那格也不硬凑（认亲失败就跳过，不猜）")
_pair_a = _rec("pp 明亮照明有限公司", 手机="13800001111")
_pair_b = _rec("pp 明亮照明有限公司", 手机="13800001111,13900002222")
_stat3 = wf.carry_pass([_pair_a, _pair_b])
_assert(_pair_a["手机"] == "13800001111" and _stat3["filled"] == 0,
        f"已有值的格子**不**被 carry 追加别行的号（只补空档）：{_pair_a['手机']}｜{_stat3}。"
        "并进来的话同一主体的两行会变成完全相同的重复行，清洗按行去重时看着像「重复数据」"
        "而被砍掉；而多一个可拨号码这件事，分层报表按主体聚合时本来就已经给了并集")
_assert(len(_enrich_calls) == 0 and not _http_trips,
        "第一级一行请求都没发（成本 0 是它排在最前的唯一理由）")

# ── F. pick_targets：命中即停与到期重验 ─────────────────────────────
print("F. 待办队列（命中即停 / 到期重验）")
_full = _rec("齐全有限公司", 电话="021-11112222", 手机="13800001111", 邮箱="a@qqq-abc.com")
for _f in fe.CONTACT_FIELDS:
    fe.set_field_evidence(_full, _f, "registry", "工商登记")
fe.stamp_verified(_full, "2026-11-10")
_keep, _skip, _stale = wf.pick_targets([_full])
_assert(_keep == [] and _skip == 1 and _stale == 0,
        "三档齐且都到工商层：一次请求都不该再花（命中即停）")
_old = _rec("过期有限公司", 电话="021-11112222", 手机="13800001111", 邮箱="a@qqq-abc.com")
for _f in fe.CONTACT_FIELDS:
    fe.set_field_evidence(_old, _f, "registry", "工商登记")
fe.stamp_verified(_old, "2025-01-01")          # 值很齐、层很高，但两年多没重验过
k2, s2, st2 = wf.pick_targets([_old], include_stale=True)
_assert(len(k2) == 1 and st2 == 1,
        f"勾了重验＋核验时间两年多前：进队列且计入 stale（{(len(k2), s2, st2)}）")
k3, s3, st3 = wf.pick_targets([_old], include_stale=False)
_assert(k3 == [] and s3 == 1 and st3 == 0,
        "反向对照：没勾重验时同一行绝不进队列（这一档默认关，不勾就等于没这功能）")
_lowconf = _rec("低可信有限公司", 电话="021-57655345")
fe.set_field_evidence(_lowconf, "电话", "source", "平台自带")
k4, s4, _t4 = wf.pick_targets([_lowconf])
_assert(len(k4) == 1, "只有平台自带层的一个号：没到闸，仍待补（非空≠够好）")
_missing = _rec("缺邮箱有限公司", 电话="021-57655345", 手机="13800001111")
for _f in ("电话", "手机"):
    fe.set_field_evidence(_missing, _f, "registry", "工商登记")
_assert(len(wf.pick_targets([_missing])[0]) == 1, "缺一个字段就仍待办（只补还空着的）")

# ── G. workable_count：界面拦不拦按钮靠这个数 ───────────────────────
print("G. 可补家数（口径跟着开关换）")
_rows = [_rec("带官网公司", site="www.mingliang-led.com"),
         _rec("只有邮箱公司", 邮箱="sales@onlymail-abc.com"),
         _rec("同主体公司", 手机=""), _rec("同主体公司", 手机="13800001111")]
_assert(wc.pick_site(_rows[1]) == "" and wf.discover_site(_rows[1])[0],
        "夹具成立：第 2 行在单跳口径下不算可补、在瀑布下算")
_n_all = wf.workable_count(_rows)
_n_single = len([r for r in _rows if wc.pick_site(r)])
_assert(_n_all >= 3 and _n_all > _n_single,
        f"三口径并集 {_n_all} 行 > 单跳口径 {_n_single} 行"
        "（勾了瀑布还按单跳数，界面就会拦住明明能补的按钮——用户读到「功能没生效」）")

# ── H. run_waterfall：五级顺序与账目 ────────────────────────────────
print("H. 瀑布总入口")
_enrich_calls.clear()
_log_lines = []
_rows2 = [_rec("h1 同主体甲公司", 手机="13800001111"),
          _rec("h1 同主体甲公司"),                       # 第 1 级免费补齐
          _rec("h2 只有邮箱乙公司", 邮箱="info@onlymail-abc.com"),   # 第 2 级
          _rec("h3 带官网丙公司", site="www.mingliang-led.com"),      # 第 3 级
          _rec("h4 品牌丁公司", 详情URL="https://brandtoken.shop-name.com/p.html"),
          _rec("h5 什么都没有戊公司")]
_queued = []
_stats = wf.run_waterfall(_rows2, log=lambda m: _log_lines.append(str(m)),
                          registry_queue=lambda names: _queued.append(list(names)) or len(names))
_assert(_stats["carry"]["filled"] == 1,
        f"第 1 级先把同主体的那一行补齐（账目 {_stats['carry']}）")
_assert(len(_enrich_calls) == 1, "网络级只发起**一次** enrich_records（一批一次，不是逐行一次）")
_kw = _enrich_calls[0] if _enrich_calls else {}
_assert(callable(_kw.get("resolver")),
        "把官网发现器传给 website_contact（第 2/4 级靠它，本模块不自己抓）")
_assert(_stats["network"].get("eligible", 0) >= 1,
        f"网络级有待办行数账目：{_stats['network'].get('eligible')}")
_assert(_queued and isinstance(_queued[0], list),
        f"最后把还是缺值的名投给现成的工商补采通道：{_queued[:1]}")
_assert(_stats["registry"]["queued"] == len(_queued[0]),
        "投出去几家由回调说了返回几家定（台账没绑定时收下 0 家也要照实在账上写 0）")
_assert("changes" in _stats, "stats 里必须有变更表这一格（哪怕为空）")
_add = [r for r in _stats["changes"] if r["变化"] == "新增"]
_assert(len(_add) == 1 and _add[0]["字段"] == "手机",
        f"快照在**任何一级动手之前**就拍了：第 1 级免费补齐的那一行必须出现在变更表里"
        f"（漏了它就是「值变了但变更表是空的」那种最伤信任的形状）：{_stats['changes']}")
_assert(_stats["registry"]["missing"] >= 1, "缺值的家数与投出去的家数分开记")
_no_cb = wf.run_waterfall([_rec("无回调公司")], log=lambda m: _log_lines.append(str(m)))
_assert(isinstance(_no_cb.get("registry"), dict) and _no_cb["registry"].get("queued") == 0,
        "没注入投递回调时第 5 级不做，并在账上写 queued=0（不静默跳过）")
_calm = _rec("h0 已齐公司", 电话="021-57655345", 手机="13800001111", 邮箱="a@qqq-abc.com")
for _f in fe.CONTACT_FIELDS:
    fe.set_field_evidence(_calm, _f, "registry", "工商登记")
fe.stamp_verified(_calm, "2026-11-10")
_quiet = wf.run_waterfall([_calm], log=lambda m: None, registry_queue=lambda names: len(names))
_assert(_quiet["changes"] == [] and _quiet["confident_skip"] == 1,
        f"反向对照：一行都没动时变更表就必须是空的（凭空造出「变更」比漏报更难被发现）"
        f"：{(_quiet['confident_skip'], _quiet['changes'])}")


class _Boom(Exception):
    pass


def _raise_enrich(records, log=None, **kw):
    raise _Boom("模拟网络级整轮炸掉")


wc.enrich_records = _raise_enrich
_eaten = []
_boom = wf.run_waterfall([_rec("炸掉公司", 邮箱="x@boom-abc.com"),
                          _rec("boom2 同主体乙公司", 电话="021-57655345"),
                          _rec("boom2 同主体乙公司")],
                         log=lambda m: _eaten.append(str(m)),
                         registry_queue=lambda names: len(names))
_assert(_boom.get("network", {}).get("error") == "_Boom",
        f"网络级异常记在账上而不是抛出：{_boom.get('network')}")
_assert(_boom["carry"]["filled"] == 1, "异常只影响那一级：免费那一级照样完成")
wc.enrich_records = _stub_enrich

# ── I. snapshot / diff：三种变化形态 ────────────────────────────────
print("I. 字段变更留痕")
_snap_src = [_rec("i1 变更甲公司", 电话="021-57655345", 邮箱=""),
             _rec("i2 变更乙公司", 手机="13800001111")]
_snap = wf.snapshot_values(_snap_src)
_snap_src[0]["邮箱"] = "a@new-mail.com"                       # 新增
_snap_src[0]["电话"] = "021-57655345,021-11112222"            # 补充
_snap_src[1]["手机"] = "13900002222"                          # 旧值消失＝异常
for _r, _f in ((_snap_src[0], "邮箱"), (_snap_src[0], "电话")):
    fe.set_field_evidence(_r, _f, "site", "官网原文")
_d = wf.diff_changes(_snap_src, _snap)
_kinds = {(r["企业名称"], r["字段"]): r["变化"] for r in _d}
_assert(_kinds.get(("i1 变更甲公司", "邮箱")) == "新增", f"原来为空＝新增：{_kinds}")
_assert(_kinds.get(("i1 变更甲公司", "电话")) == "补充", "原值还在、追加了一个＝补充")
_assert(_kinds.get(("i2 变更乙公司", "手机")) == "改写(异常)",
        "旧值凭空消失必须点名（本模块承诺只追加不覆盖，这条是承诺的反面）")
_row = next(r for r in _d if r["变化"] == "补充")
_assert(_row["新值"] == "021-11112222" and _row["旧值"] == "021-57655345",
        f"变更行写的是「加了哪个值」而不是整格新内容：{_row['旧值']} → {_row['新值']}")
_assert(_row["命中层级"] == fe.TIER_LABEL["site"] and _row["补全证据"] == "官网原文",
        "变更行自带层级与出处（不然还得回原始表里翻）")
_assert(wf.diff_changes(_snap_src, {}) == [], "没有快照时不造变更行（按对象身份索引，回头找会找错）")
# ── J. 评分与分层 ───────────────────────────────────────────────────
print("J. 评分与分层（可解释是硬要求）")
_assert(sum(ls.WEIGHTS.values()) == 100,
        f"三维权重合计 100：{ls.WEIGHTS}（报表里写的满分必须和算分用的对得上）")
_assert(sum(p for _n, p in ls.COMPLETENESS_FIELDS) == ls.WEIGHTS["completeness"],
        "完整度各列权重之和＝那一维的满分（否则再好的表也刷不到高分，报表会说谎）")
_j_full = _rec("j1 齐全甲公司", 电话="021-57655345", 手机="13800001111",
               邮箱="sales@mingliang-led.com", 联系人="王经理", 所在区域="广东 深圳",
               主营产品="LED 灯具", 公司网址="www.mingliang-led.com",
               采集时间="2026-11-12")
for _f in fe.CONTACT_FIELDS:
    fe.set_field_evidence(_j_full, _f, "registry", "工商登记")
fe.stamp_verified(_j_full, "2026-11-12")
_j_s, _j_t, _j_w = ls.score_record(_j_full, today=_today)
_assert(_j_t == ls.TIER_WORK and ls.WORK_MIN <= _j_s <= 100,
        f"工商核验＋齐全＋刚验过＝立即跟进（{_j_s} 分）")
_assert(len(_j_w) >= 3, f"依据至少三条（可信度/齐备度/新鲜度各一条）：{_j_w}")
_assert(all(("+" in x) or ("缺" in x) or ("未知" in x) for x in _j_w),
        "每一条依据都带得上分数字或减分原因（只报喜的评分表活不过第二次复盘）")
_j_src = _rec("j2 平台自带乙公司", 电话="021-57655345", 手机="13800001111",
              邮箱="sales@mingliang-led.com", 联系人="王经理", 所在区域="广东 深圳",
              主营产品="LED 灯具", 公司网址="www.mingliang-led.com",
              采集时间="2026-11-12")
for _f in fe.CONTACT_FIELDS:
    fe.set_field_evidence(_j_src, _f, "source", "平台自带")
fe.stamp_verified(_j_src, "2026-11-12")
_s2, _t2, _w2 = ls.score_record(_j_src, today=_today)
_assert(_s2 >= ls.WORK_MIN and _t2 == ls.TIER_NURTURE,
        f"总分够 70 但可信层只有平台自带的行**不**进立即跟进（{_s2} 分 → {_t2}）："
        "总分只说明「信息多」，不说明「这号码能打」")
_assert(any("低于立即跟进要求" in x for x in _w2),
        f"闸掉的原因必须写在依据里而不是让用户自己猜：{_w2}")
_j_stale = _rec("j3 过期丙公司", 电话="021-57655345", 手机="13800001111",
                邮箱="sales@mingliang-led.com", 联系人="王经理", 所在区域="广东 深圳",
                主营产品="LED 灯具", 公司网址="www.mingliang-led.com",
                采集时间="2026-11-12")
for _f in fe.CONTACT_FIELDS:
    fe.set_field_evidence(_j_stale, _f, "registry", "工商登记")
fe.stamp_verified(_j_stale, "2025-01-01")          # 刚采的表，但号两年多没被验过
_s3, _t3, _w3 = ls.score_record(_j_stale, today=_today)
_assert(_t3 != ls.TIER_WORK and any("已过 TTL" in x for x in _w3),
        f"采集时间是今天但核验时间两年多前：过期闸生效（{_s3} 分 → {_t3}）")
_assert(_s3 < _j_s, f"过期后可信分打折，总分必须低于同形的未过期行（{_s3} < {_j_s}）")
_assert("已过期" in ls.freshness_text(_j_stale, today=_today),
        "新鲜度文字与评分读同一对判据（两处各判就会出现「分数说过期、文字说 0 天」）")
_j_aged = _rec("j4 老采新验丁公司", 电话="021-57655345", 手机="13800001111",
               邮箱="sales@mingliang-led.com", 联系人="王经理", 所在区域="广东 深圳",
               主营产品="LED 灯具", 公司网址="www.mingliang-led.com",
               采集时间="2025-01-01")
for _f in fe.CONTACT_FIELDS:
    fe.set_field_evidence(_j_aged, _f, "registry", "工商登记")
fe.stamp_verified(_j_aged, "2026-11-10")
_s4, _t4, _w4 = ls.score_record(_j_aged, today=_today)
_assert(_t4 == ls.TIER_WORK,
        f"反向对照：采集时间很久但**刚重验过**的行仍是立即跟进（{_s4} 分）——"
        "「有多新」看采集时间、「过没过期」看核验时间，两条判据分家的另一侧")
_assert(_s4 < _j_s, f"老采集时间只扣新鲜分，不扣可信分（{_s4} < {_j_s}）")
_j_nodate = _rec("j5 时间未知戊公司", 电话="021-57655345", 采集时间="昨天")
fe.set_field_evidence(_j_nodate, "电话", "site", "官网原文")
_s5, _t5, _w5 = ls.score_record(_j_nodate, today=_today)
_assert(any("认不出来" in x for x in _w5)
        and any("已过 TTL" in x or "未知" in x for x in _w5),
        f"时间格式认不出：新鲜度按未知给 0 分并把原因写出来（不假装是今天）：{_w5[-1:]}")
_assert(ls.missing_hint(_rec("j6 空公司")) == "、".join(fe.CONTACT_FIELDS)
        and ls.missing_hint(_j_full) == "—",
        "待补缺口：全空列三项、齐全印「—」（印空串会被读成「这列坏了」而不是「没缺口」）")
_assert(ls.WORK_MIN > ls.NURTURE_MIN and ls.WORK_CONF_MIN == fe.CONFIDENT_MIN,
        "两道分数线与那道可信闸各自独立（把闸写成另一个数就会和瀑布的命中即停打架）")

# ── K. 同主体归并 ───────────────────────────────────────────────────
print("K. 同主体归并成一条线索")
_ka = _rec("kk 明亮照明有限公司", 手机="13800001111", 数据来源="顺企网",
           关键词="灯具", 采集时间="2026-10-01")
_kb = _rec("kk 明亮照明有限公司", 手机="13900002222", 电话="021-57655345",
           数据来源="八方", 关键词="LED", 采集时间="2026-11-06")
fe.set_field_evidence(_ka, "手机", "registry", "工商登记（企知道）")
fe.set_field_evidence(_kb, "手机", "source", "平台自带")
fe.set_field_evidence(_kb, "电话", "site", "官网 contact 页")
_kl = ls.aggregate_leads([_ka, _kb])
_assert(len(_kl) == 1 and _kl[0]["rows"] == 2,
        f"同主体两行合成一条线索且行数如实记 2：{[(x['key'], x['rows']) for x in _kl]}")
_kr = _kl[0]["rec"]
_assert(set(str(_kr["手机"]).split(",")) == {"13800001111", "13900002222"},
        f"手机取两行并集（英文逗号一格）：{_kr['手机']}")
_assert(fe.parse_evidence(_kr).get("手机", ("", ""))[1] == "registry",
        "逐字段取**最高**那一层的行，而不是最后一行盖掉前面（并集了值却把最硬的层丢了，"
        "报表就会把工商核验的号按平台自带卖）")
_assert("工商登记" in fe.parse_proof(_kr).get("手机", ""),
        "出处跟着最高层一起带过来（不然证据列等于没说）")
_assert(fe.parse_evidence(_kr).get("电话", ("", ""))[1] == "site", "另一个字段各认各的层")
_assert(_kr["采集时间"] == "2026-11-06", "采集时间取最新那行（老掉的一行不拖整家新鲜度）")
_assert(fe.VERIFIED_COL not in _kr,
        "两行都没真记过核验时间时**不**凭空造出那一格（把采集时间写成核验时间＝捏造「刚验过」）")
_kv = _rec("kk 明亮照明有限公司", 手机="13800001111", 采集时间="2026-11-06",
           **{fe.VERIFIED_COL: "2026-11-12"})
fe.set_field_evidence(_kv, "手机", "registry", "工商登记")
_kl2 = ls.aggregate_leads([_kv])
_assert(_kl2[0]["rec"].get(fe.VERIFIED_COL) == "2026-11-12",
        "真记过且比采集时间新才带这一格（正向对照）")
_assert(_kl[0]["rec"]["数据来源"] == "顺企网,八方",
        f"来源按出现顺序拼接，一家被两路采到时两边都记账：{_kr['数据来源']}")
_ksrc = ls.lead_rows([_ka, _kb], today=_today)
_kt = ls.tier_counts(_ksrc)
_assert(list(_kt) == list(ls.TIER_ORDER) and sum(_kt.values()) == 1,
        f"分档计数的键恰是三个档位、总数恰是归并后的线索数（多一挡少一挡都会让日志"
        f"和报表各说一套）：{_kt}")
_assert(_kt[ls.TIER_WORK] == 1 and _ksrc[0]["分层"] == ls.TIER_WORK,
        f"归并出的那一条带着工商核验层落进「{ls.TIER_WORK}」（{_ksrc[0]['线索分']} 分 ≥"
        f"{ls.WORK_MIN}，可信 {_ksrc[0]['_conf']} ≥{ls.WORK_CONF_MIN}）")
# 反向对照：把基准日推到 TTL 之后，同一条线索必须掉出立即跟进。
# 这一条钉的不是评分，是 `today` 这个参数真的被下发到了 score_record 和新鲜度文字——
# 签名里留着参数、函数体里忘了传，是「本地跑绿、报表口径随日历漂移」的标准形状。
_kt_late = ls.tier_counts(ls.lead_rows([_ka, _kb], today="2028-01-01"))
_assert(_kt_late[ls.TIER_WORK] == 0 and sum(_kt_late.values()) == 1,
        f"基准日推到核验满期之后，同一条线索不再进立即跟进：{_kt_late}")
_krow_late = ls.lead_rows([_ka, _kb], today="2028-01-01")[0]
_assert("已过期" in _krow_late["数据新鲜度"] and "已过 TTL" in _krow_late["评分依据"],
        f"过期时分数与文字必须同时改口（一处改一处不改＝报表自己打自己脸）："
        f"{_krow_late['数据新鲜度']}")
# ── L. 报表落盘与列契约 ───────────────────────────────────────────────
print("L. 报表落盘（真回读）与「不许碰的列」")
import cdp_base as _cdp                                                      # noqa: E402
import parallel_session as ps                                                # noqa: E402
import yzd_client as _yzd                                                    # noqa: E402
from openpyxl import load_workbook                                           # noqa: E402

_L_TMP = Path(tempfile.mkdtemp(prefix="wfl_report_"))
_L_REC = _rec("l1 分层落盘公司", 电话="021-57655345", 手机="13800001111",
              联系人="王经理", 所在区域="广东 深圳", 主营产品="LED 灯具",
              公司网址="www.mingliang-led.com", 采集时间="2026-11-10")
for _f in ("电话", "手机"):
    _assert(fe.set_field_evidence(_L_REC, _f, "site", "官网原文"),
            f"夹具成立：{_f} 有值才写得上层级（写不上则下面全部断言在空转）")
fe.stamp_verified(_L_REC, "2026-11-10")
_assert("联系人" not in fe.CONTACT_FIELDS
        and fe.set_field_evidence(_L_REC, "联系人", "site", "官网原文") is False,
        "只有联系方式三项能记层级（本行刚踩过这条：给「联系人」记层级会把「谁接电话」"
        "写成「号码来路」，报表里两回事混成一格）")
_L_ROWS = ls.lead_rows([_L_REC], today=_today)
_L_HEALTH = ch.health_rows(ch.metrics_from([_L_REC], {"测试站": {"ok": 1, "pages": 2}}))
_L_CHG = [{"企业名称": "l1 分层落盘公司", "字段": "电话", "变化": "新增",
           "旧值": "", "新值": "021-57655345",
           "命中层级": fe.TIER_LABEL["site"], "补全证据": "官网原文"}]
_L_PATH = str(_L_TMP / ps.ParallelSession.LEAD_REPORT_FILENAME)
_L_SHEETS = ls.write_lead_report(_L_PATH, _L_ROWS, changes=_L_CHG,
                                 health=_L_HEALTH, log=lambda m: None)
_assert(_L_SHEETS == ["线索分层", "字段变更", "通道健康", "口径说明"],
        f"四张表按固定顺序落成（少一张就是功能半截，多一张是没登记的产出）：{_L_SHEETS}")
_wb = load_workbook(_L_PATH)
_assert(_wb.sheetnames == _L_SHEETS,
        f"回读工作表名与写入返回一致（返回值不能只代表「调用过」）：{_wb.sheetnames}")
_ws = _wb["线索分层"]
_head = [c.value for c in _ws[1]]
_assert(_head == ls.LEAD_HEADERS,
        f"表头与 LEAD_HEADERS 逐列同名同序（报表列名是给人看的契约）：{len(_head)} 列")
_body = [[c.value for c in row] for row in _ws.iter_rows(min_row=2)]
_assert(len(_body) == 1 and len(_body[0]) == len(ls.LEAD_HEADERS),
        f"一行数据、每行列数与表头相等（「序号」那列偏移错位是这类拼装最经典的错法）："
        f"{len(_body)} 行 × {len(_body[0]) if _body else 0} 列")
_bmap = dict(zip(_head, _body[0]))
_assert(_bmap["序号"] == 1 and _bmap["企业名称"] == "l1 分层落盘公司"
        and _bmap["分层"] == _L_ROWS[0]["分层"] and _bmap["线索分"] == _L_ROWS[0]["线索分"],
        f"落盘后回读的分层/线索分与算分结果一致（中间没有任何「写的是另一份数」）："
        f"{_bmap['分层']} / {_bmap['线索分']}")
_assert(_bmap["手机"] == "13800001111"
        and _bmap["命中层级"] == _L_ROWS[0]["命中层级"]
        and _bmap["补全证据"] == _L_ROWS[0]["补全证据"],
        f"证据两格**原样**落盘（回读值＝内存那一行的值，报表与原始表读的是同一格）："
        f"{_bmap['命中层级']}｜{_bmap['补全证据']}")
_assert(_bmap["命中层级"].count(fe.TIER_LABEL["site"]) == 2
        and _bmap["补全证据"].count("官网原文") == 2
        and all(f in _bmap["命中层级"] for f in ("电话", "手机")),
        "两个字段各记各的层级与出处，落盘的是「一格多段」而不是只剩最后一段"
        "（拼装时覆盖前一段，报表就只说得清一个号码的来路）")
# 最硬的一条：把回读出来的格子重新喂给解析器，还能认得出层级——
# 那一格如果只是给人看的字符串，报表与后续轮次的判据就接不上。
_re = dict(_L_REC)
_re[fe.EVIDENCE_COL] = _bmap["命中层级"]
_re[fe.PROOF_COL] = _bmap["补全证据"]
_reparsed = fe.parse_evidence(_re)
_assert(_reparsed.get("电话", ("", ""))[1] == "site"
        and _reparsed.get("手机", ("", ""))[1] == "site",
        f"落盘的格子能解析回层级（{_reparsed.get('电话')}）：报表→原始表→下一轮判据闭环")
_assert("工商核验" in "".join(ls.caliber_lines())
        and str(ls.WORK_MIN) in "".join(ls.caliber_lines()),
        "口径说明那张表里写着权重与门槛（看表的人不必读代码就能复核）")
_cs = _wb["字段变更"]
_assert([c.value for c in _cs[1]] == ls.CHANGE_HEADERS
        and [c.value for c in _cs[2]][1:4] == ["l1 分层落盘公司", "电话", "新增"],
        "变更表列与 CHANGE_HEADERS 对齐（列名一错，用户筛「改写(异常)」就永远筛不到）")
_hs = _wb["通道健康"]
_assert([c.value for c in _hs[1]] == ls.HEALTH_HEADERS,
        f"健康表列与 HEALTH_HEADERS 对齐：{len([c.value for c in _hs[1]])} 列")
_hmap = dict(zip([c.value for c in _hs[1]], [c.value for c in _hs[2]]))
_assert(_hmap["通道"] == "测试站" and _hmap["本轮家数"] == 1
        and _hmap["本轮行数"] == 1,
        f"健康表真的落了数（不是只有表头）：{_hmap['通道']} {_hmap['本轮家数']} 家")
_wb.close()
# 列契约：附加报表之所以是「附加」的，判据在它一处都没挤进既有交付
_assert(len(_yzd.IMPORT_HEADERS) == 9
        and not (set(_yzd.IMPORT_HEADERS) & set(fe.evidence_columns())),
        f"入库接口 9 列契约里没有任何证据列（加一列就会把云展上传打坏）："
        f"{_yzd.IMPORT_HEADERS}")
_assert(fe.evidence_columns() == tuple(_cdp.CdpSiteCrawler.EXPORT_HEADERS[-3:])
        == tuple(ps.RAW_HEADERS[-3:]),
        "原始表表尾三列＝证据三列，且两处列序同源（不是各排一次排出来的巧合）")
_assert(ps.RAW_HEADERS is _cdp.CdpSiteCrawler.EXPORT_HEADERS,
        "会话层与爬虫层的原始表列是**同一个列表对象**（手抄的第二份迟早差一列，"
        "而差的总是最后加进来的那列留痕）")
_assert(ps.ParallelSession.LEAD_REPORT_FILENAME not in (
            "企业数据_原始.xlsx", "企业数据_清洗后入库数据.xlsx")
        and ps.ParallelSession.LEAD_REPORT_FILENAME != ch.LEDGER_FILENAME
        and ch.LEDGER_FILENAME.endswith(".json"),
        "报表文件名既不与原始/清洗表撞名、也不与台账撞名（撞名那次是交付文件被 JSON 覆盖）")
# ── M. 通道健康账（红牌要抓的是「悄悄不出货」）────────────────────────
print("M. 通道健康与红牌")
_M_TMP = Path(tempfile.mkdtemp(prefix="wfl_health_"))
_M_LED = _M_TMP / ch.LEDGER_FILENAME
# 同一家在一路里出 20 行产品是常态：按行数记就会把「只有 20 行重复」的一路当成健康一路
# （公司名称**刻意重复**，之前这里写成了 20 个不同名，数出 20 家是对的、红的却是夹具）
_m_same = [_rec("m1 明亮照明有限公司", 电话="021-57655345", 数据来源="顺企网",
                主营产品=f"LED 灯具型号{i}")
           for i in range(20)]
_m_one = ch.metrics_from(_m_same, {"顺企网": {"ok": 20}})
_assert(len({w["公司名称"] for w in _m_same}) == 1,
        "夹具成立：这 20 行确实是同一家（不然下面那条去重断言在空转）")
_assert(_m_one["顺企网"]["rows"] == 20 and _m_one["顺企网"]["companies"] == 1,
        f"家数按同主体去重、行数如实另记（20 行≠20 家）："
        f"{_m_one['顺企网']['rows']} 行 / {_m_one['顺企网']['companies']} 家")
_m_mix = [_rec("m2 有联系方式甲公司", 电话="021-57655345", 数据来源="八方网"),
          _rec("m3 空号乙公司", 数据来源="八方网")]
_m_m2 = ch.metrics_from(_m_mix, {"八方网": {"ok": 2}})
_assert(_m_m2["八方网"]["fill_rate"] == 50.0 and _m_m2["八方网"]["with_contact"] == 1,
        f"填充率按行数分母如实算：{_m_m2['八方网']['fill_rate']}%")
# 「本轮没跑」与「本轮零产出」必须分开：跑过但一家没出，才是红牌要抓的对象
_m_ran = ch.metrics_from([], {"某一站": {"ok": 0}})
_m_noran = ch.metrics_from([], {})
_assert(_m_ran["某一站"]["ran"] is True and _m_noran.get("某一站") is None,
        f"跑过但零产出必须进账；没跑过的连账都不进（勾掉一站只是为了省时间，"
        f"第二天看到红牌就会整张账不信）")
_assert(ch.note_from_stats(_m_ran["某一站"]).startswith("翻 0 页见 0 条")
        or "0 家" in ch.note_from_stats(_m_ran["某一站"]),
        f"零产出那一路的说明如实写本轮读数：{ch.note_from_stats(_m_ran['某一站'])}")
_assert("不参与连续计数" in ch.note_from_stats(
        {"ran": False, "companies": 0, "stats": {}}),
        "没跑的那一路在表里直说「不参与连续计数」，不留下一个看起来像零产出的空格")
# 连续三轮零产出：只在**首次跨过阈值**那一次报警（否则挂十几轮的通道把日志刷成噪音）
_alerts = []
for _rnd in range(1, 4):
    _r = ch.update_ledger(str(_M_LED), ch.metrics_from([], {"死站": {"ok": 0}}),
                          log=lambda m: None, round_label=f"第{_rnd}轮")
    _alerts.append([a["channel"] for a in _r["alerts"]])
_assert(_alerts == [[], ["死站"], []],
        f"红牌只在首次跨阈值时警一次（阈值 {ch.RED_CARD_ROUNDS} 轮）：{_alerts}")
_m_data, _m_err = ch.load_ledger(str(_M_LED))
_assert(_m_err == "" and _m_data["zero_streak"]["死站"] == 3
        and [x["label"] for x in _m_data["rounds"]] == ["第1轮", "第2轮", "第3轮"],
        f"连续轮次与逐轮明细都如实落盘：streak={_m_data['zero_streak']}")
_assert(not (_M_LED.with_suffix(_M_LED.suffix + ".tmp")).exists(),
        "台账走 tmp + os.replace 原子换名，目录里不留半截的 .tmp")
# 恢复出货 → 连续轮次立刻清零（红牌不能挂着不走）
_r = ch.update_ledger(str(_M_LED), ch.metrics_from(
    [_rec("m4 又出货了公司", 电话="021-57655345", 数据来源="死站")], {"死站": {"ok": 1}}),
    log=lambda m: None)
_assert(_r["alerts"] == [] and _r["saved"] is True, "恢复出货那一轮不报警且照常落盘")
_m_data, _ = ch.load_ledger(str(_M_LED))
_assert(_m_data["zero_streak"]["死站"] == 0,
        f"一旦有产出连续轮次归零（红牌要读的是「现在」，不是历史最差值）")
# 台账坏了必须出声：静默清零＝红牌永远不再亮，那是本项目记过账的最阴失效方式
_M_BAD = _M_TMP / "坏台账.json"
_M_BAD.write_text("{这不是 JSON", encoding="utf-8")
_m_logs = []
_r = ch.update_ledger(str(_M_BAD), ch.metrics_from([], {"某站": {"ok": 0}}),
                      log=lambda m: _m_logs.append(str(m)))
_assert(_r["error"] and any("重新数起" in x and "⚠" in x for x in _m_logs),
        f"坏台账：按空账重开但**当场念出来**（error={_r['error'][:40]}）：{_m_logs}")
_assert(len(_r["alerts"]) == 0,
        "坏台账重开的第一轮不警红牌：读数还不连续，凭一次空账报警就是假警报")
# 没绑定任务文件夹（落点空串）：本轮账仍在，但必须说明连续计数只在内存里
_m_logs2 = []
_r = ch.update_ledger("", ch.metrics_from([], {"某站": {"ok": 0}}),
                      log=lambda m: _m_logs2.append(str(m)))
_rows = ch.health_rows(ch.metrics_from([], {"某站": {"ok": 0}}))
_assert(_r["saved"] is False and any("台账没落盘" in x for x in ch.alert_lines(_r, None)),
        f"没落盘也要说清连续计数不可信：{ch.alert_lines(_r, None)}")
# 红牌既要在「红牌」列上是「是」，也要在说明里点明（只看列的人和只看说明的人都能看到）。
# 这里必须走一遍真 update_ledger：红牌读数只能来自那一份标注，不能靠报表行自己判一遍。
_m_red = ch.metrics_from([], {"死站": {"ok": 0}})
for _i in range(ch.RED_CARD_ROUNDS):      # 上面那轮已恢复出货、计数归过零，所以从零重跑满阈值
    ch.update_ledger(str(_M_LED), _m_red, log=lambda m: None)
_red_rows = ch.health_rows(_m_red)
_assert(_red_rows[0]["红牌"] == "是" and _red_rows[0]["说明"].startswith("红牌：")
        and _red_rows[0]["零产出连续轮次"] >= ch.RED_CARD_ROUNDS,
        f"红牌那一路既有一列「是」也在说明里点明："
        f"{_red_rows[0]['零产出连续轮次']} 轮｜{_red_rows[0]['说明']}")
try:
    ch.health_rows(_m_red, {"alerts": [{"channel": "死站", "streak": 9}]})
    _no_dead_arg = False
except TypeError:
    _no_dead_arg = True
_assert(_no_dead_arg,
        "health_rows 不再收那个「签名里摆着、函数体里一个字都不读」的 result 参数"
        "（摆设参数会让人以为传了才算红牌，而事实恰好相反）")
# 未算分 vs 平均分 0：两回事
_m_rows_nos = ch.health_rows(ch.metrics_from(_m_mix, {"八方网": {"ok": 2}}))
_m_rows_s = ch.health_rows(ch.metrics_from(_m_mix, {"八方网": {"ok": 2}},
                                           {"八方网": (88, 1)}))
_assert(_m_rows_nos[0]["平均线索分"] == "—" and _m_rows_nos[0]["立即跟进"] == "—",
        f"没勾分层时印「—」而不是 0（印 0 会被读成「这一路全是废数据」，"
        f"而事实只是没算分）：{_m_rows_nos[0]['平均线索分']}")
_assert(_m_rows_s[0]["平均线索分"] == 88 and _m_rows_s[0]["立即跟进"] == 1,
        f"算了分就印真数：{_m_rows_s[0]['平均线索分']}／{_m_rows_s[0]['立即跟进']}")
_assert(_rows[0]["红牌"] == "" and _rows[0]["零产出连续轮次"] == 0,
        "没经过台账标注的 metrics 绝不自己判红牌（判据两份迟早各叫各的）")
# 明细只留最近 MAX_ROUNDS 轮（台账不许无限变大）
_M_MANY = _M_TMP / "多轮.json"
for _i in range(ch.MAX_ROUNDS + 12):
    ch.update_ledger(str(_M_MANY), ch.metrics_from([], {"站": {"ok": 0}}),
                     log=lambda m: None)
_m_data, _ = ch.load_ledger(str(_M_MANY))
_assert(len(_m_data["rounds"]) == ch.MAX_ROUNDS
        and _m_data["zero_streak"]["站"] == ch.MAX_ROUNDS + 12,
        f"明细封顶 {ch.MAX_ROUNDS} 轮、连续计数不受封顶影响（现在 "
        f"{_m_data['zero_streak']['站']} 轮）")
# ── N. 会话层接线 ─────────────────────────────────────────────────────
print("N. 会话层接线（ParallelSession）")
import inspect as _ins                                                        # noqa: E402

_N_TMP = Path(tempfile.mkdtemp(prefix="wfl_sess_"))
_n_logs = []
_n = ps.ParallelSession()
_n._log = lambda m: _n_logs.append(str(m))
_n._user_stop = False
_assert(_n.site_enrich is False and _n.waterfall is False
        and _n.lead_export is False and _n.reverify is False,
        "引擎默认四个开关全关：这是「自称离线的用例绝不真联网」的护栏，界面上的默认开"
        "只存在于界面层（两边各一套默认，测试就会替用户做决定）")
_n.configure(waterfall=True, lead_export=True, reverify=True)
_assert(_n.waterfall and _n.lead_export and _n.reverify,
        "configure 下发真落到会话属性上（签名里接着参数、函数体不赋值＝界面勾了会话收不到）")
_n.configure(waterfall=None)
_assert(_n.waterfall is True, "configure 传 None 是「不改这一项」，不是「关掉这一项」")
_n.task_folder = str(_N_TMP)
_assert(_n.lead_report_path().endswith(ps.ParallelSession.LEAD_REPORT_FILENAME)
        and _n.health_ledger_path().endswith(ch.LEDGER_FILENAME)
        and _n.lead_report_path() != _n.health_ledger_path(),
        f"报表与台账是两个文件、两个扩展名（撞名那次：JSON 覆盖在 .xlsx 上，"
        f"用户双击打不开）：{Path(_n.lead_report_path()).name} / "
        f"{Path(_n.health_ledger_path()).name}")
_n_no = ps.ParallelSession()
_n_no._log = lambda m: None
_assert(_n_no.lead_report_path() == "" and _n_no.health_ledger_path() == "",
        "没绑定任务文件夹时两个落点都是空串（不硬猜一个用户找不到的目录）")
# 报表出几次：这是「两条支路都出一张、且只出一张」的账
_n_writes = []
_real_write = ls.write_lead_report


def _spy_write(path, rows, changes=None, health=None, log=None):
    _n_writes.append({"path": path, "rows": len(rows or []),
                      "changes": len(changes or []), "health": len(health or [])})
    return _real_write(path, rows, changes=changes, health=health, log=log)


ls.write_lead_report = _spy_write
_n_rows = [_rec("n1 会话甲公司", 电话="021-57655345", 公司网址="www.mingliang-led.com",
                采集时间="2026-11-10"),
           _rec("n2 会话乙公司", 邮箱="sales@mingliang-led.com", 采集时间="2026-11-10")]
try:
    _n._all_live_records = lambda: list(_n_rows)
    _n._all_result_crawlers = lambda: []
    _n.site_enrich_workers, _n.site_enrich_budget, _n.site_enrich_limit = 1, 30.0, 50
    # ① 勾瀑布 + 勾分层
    _n.site_enrich, _n.waterfall, _n.lead_export = True, True, True
    _n_writes.clear()
    _n._health_written = _n._lead_written = False
    _n.enrich_websites(reason="用例①")
    _n._export_lead_standalone(reason="用例①")
    _assert(len(_n_writes) == 1,
            "瀑布支路跑成后一轮只出一张报表（含收尾后的独立支路重查）："
            f"{len(_n_writes)} 次")
    # ② 只勾回采不勾瀑布（老行为）
    _n.waterfall = False
    _n_writes.clear()
    _n._lead_written = False                 # 用例里用一次调用代表一轮
    _n.enrich_websites(reason="用例②")
    _n._export_lead_standalone(reason="用例②")
    _assert(len(_n_writes) == 1 and _n_writes[0]["rows"] == 2,
            f"只走单跳那一支照样出一张报表（少一张就是静默少功能）：{len(_n_writes)} 次")
    # ③ 勾了分层但没勾回采链路：报表不该因此消失
    _n.site_enrich, _n.waterfall, _n.lead_export = False, False, True
    _n_writes.clear()
    _n_logs.clear()
    _n._lead_written = False
    _assert(_n.enrich_websites(reason="用例③") is None and _n_writes == [],
            "回采链路没勾时补全那一轮确实不跑（也不该由它顺带出报表）")
    _n._export_lead_standalone(reason="用例③")
    _assert(len(_n_writes) == 1,
            "但分层报表照出：它一次网络请求都不发，不该被一个它不需要的开关决定生死")
    # ③b 同一轮里再走一遍独立支路：不重出第二张（两个入口共用「本轮已出」判据）
    _n._export_lead_standalone(reason="用例③b")
    _assert(len(_n_writes) == 1,
            f"独立支路被调到两次也只落一张表（写成两遍＝用户看到两条同名报表，"
            f"读起来像跑了两个任务）：{len(_n_writes)} 次")
    # ③c 界面上的手动补采（force=True）确实刷新报表
    _n.site_enrich = True
    _n.enrich_websites(reason="用例③c", force=True)
    _assert(len(_n_writes) == 2,
            f"手动补采是一次新的补全，报表跟着刷新（不刷新就是拿着旧分层的表交差）："
            f"{len(_n_writes)} 次")
    # ④ 三个开关全关：一行报表代码都不执行
    _n.lead_export = False
    _n_writes.clear()
    _n._lead_written = False
    _n.enrich_websites(reason="用例④", force=True)
    _n._export_lead_standalone(reason="用例④")
    _assert(_n_writes == [], "不勾分层时报表代码一次都不执行（爆炸半径为零）")
    # ⑤ 本轮没数据：不落一张只有表头的表，但要说明为什么
    _n.lead_export = True
    _n._all_live_records = lambda: []
    _n_writes.clear()
    _n_logs.clear()
    _n._export_lead_standalone(reason="用例⑤")
    _assert(_n_writes == [] and any("没东西可排" in m for m in _n_logs),
            f"没数据时不落空表并如实说明：{[m for m in _n_logs if '分层' in m]}")
    _n._all_live_records = lambda: list(_n_rows)
    # ⑥ 报表坏了不连累主交付
    _n_writes.clear()

    def _boom_write(*a, **k):
        raise PermissionError("结果表正被 Excel 打开占用")

    ls.write_lead_report = _boom_write
    _n_logs.clear()
    _st = _n._export_lead_tiers(list(_n_rows), reason="用例⑥")
    ls.write_lead_report = _spy_write
    _assert(_st["leads"] == 2 and _st["path"] == ""
            and any("不受影响" in m for m in _n_logs),
            f"附加报表失败只少一张表：已算好的分层与既有产出一行都不丢（{_st['leads']} 家 / "
            f"path={_st['path']!r}）")
    # ⑦ 台账落点与格式各归各（撞名那条缺陷的直接回归）
    _n.lead_export = True
    _n.site_enrich = True
    _n._health_written = _n._lead_written = False
    _n_writes.clear()
    _n.enrich_websites(reason="用例⑦")
    _n._report_channel_health()
    _p_rep, _p_led = _n.lead_report_path(), _n.health_ledger_path()
    _json = json.loads(Path(_p_led).read_text(encoding="utf-8"))
    _wb2 = load_workbook(_p_rep)
    _assert(isinstance(_json.get("zero_streak"), dict) and "rounds" in _json
            and _wb2.sheetnames[0] == "线索分层" and "口径说明" in _wb2.sheetnames,
            f"台账是真 JSON、报表是真 xlsx，两个文件互不覆盖："
            f"{sorted(_json.keys())} / {_wb2.sheetnames}")
    _wb2.close()
    # ⑧ 一轮只许推进一次连续轮次（勾了分层的跑法不该把轮次数成两倍）
    _before = len(_json["rounds"])
    _n._report_channel_health()
    _n._report_channel_health()
    _after = len(json.loads(Path(_p_led).read_text(encoding="utf-8"))["rounds"])
    _assert(_after == _before,
            "同一轮里再怎么读台账都只推进一次（%d → %d）" % (_before, _after))
    _n._report_channel_health(write_ledger=False)
    _after2 = len(json.loads(Path(_p_led).read_text(encoding="utf-8"))["rounds"])
    _assert(_after2 == _after, "手动读数（write_ledger=False）不占轮次：" + str(_after2))
    # ⑨ 接线本身在案：crawl 里必须「本轮开始复位 → 先回采 → 再补独立支路 → 记健康账」
    _csrc = _ins.getsource(_n.crawl)
    _assert("self.enrich_websites(reason=\"采集收尾\")" in _csrc
            and "self._export_lead_standalone(reason=\"采集收尾\")" in _csrc
            and _csrc.index("self.enrich_websites(reason=\"采集收尾\")")
            < _csrc.index("self._export_lead_standalone(reason=\"采集收尾\")"),
            "crawl 末尾两条都接上了、且独立支路排在回采之后（定义了没接线＝功能原地隐身）")
    _assert("self._lead_written = False" in _csrc
            and _csrc.index("self._lead_written = False")
            < _csrc.index("self._export_lead_standalone(reason=\"采集收尾\")"),
            "「本轮已出报表」的判据在 crawl 开始时复位：不复位的话第二轮永远不出表"
            "（一轮只出一张的另一半含义是「每轮都要出一张」）")
finally:
    ls.write_lead_report = _real_write
_assert(ls.write_lead_report is _real_write, "桩已还原（后续套件读到的是真落盘函数）")

# ── O. 界面层：三个质量口径勾选 ─────────────────────────────────
print("O. 界面层：三个口径勾选的可见性 / 同步 / 级联 / 快照恢复 / 话术同源")
import b2bcollect_gui as G                                          # noqa: E402
from PyQt5.QtWidgets import QApplication, QMessageBox               # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
_o_boxes = []
_real_box = {n: getattr(QMessageBox, n) for n in ("information", "warning", "question")}


def _o_box(*a, **k):
    """把弹下来的框记成文本再吞掉：界面层的判据有一半是「用户当时看到哪句话」。"""
    _o_boxes.append(" | ".join(str(x) for x in a[1:3]))
    return QMessageBox.Ok


QMessageBox.information = staticmethod(_o_box)
QMessageBox.warning = staticmethod(_o_box)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)
G.MainWindow._get_desktop_dir = lambda self: str(_DESK_SANDBOX)
w = G.MainWindow()
w.show()
app.processEvents()
_o_logs = []
w._on_log = lambda m: _o_logs.append(str(m))

# ① 三条勾选必须**看得见**：它们是数据口径开关，不是可选功能入口
for _nm in ("chk_waterfall", "chk_lead", "chk_reverify"):
    _cb = getattr(w, _nm, None)
    _assert(_cb is not None and _cb.isVisibleTo(w), f"{_nm} 造出来了且恒定可见")
_texts = [w.chk_waterfall.text(), w.chk_lead.text(), w.chk_reverify.text()]
_assert(len(set(_texts)) == 3 and all(t.strip() for t in _texts),
        f"三条各有名字、不叫「启用」：{_texts}")
_build_src = _ins.getsource(G.MainWindow._build_ui)
_hp3 = _build_src[_build_src.index("hp3 = QHBoxLayout()"):_build_src.index("vp.addLayout(hp3)")]
_assert("edition." not in _hp3,
        "这一行不许按交付档位藏控件：藏掉勾选而值还生效，比多一行更难解释"
        "（清洗档位同一纪律，见 b2bcollect_gui 那段注释）")

# ② 默认值：省请求的两条默认开、烧请求的那条默认关
_assert(w._waterfall_on is True and w._lead_report_on is True and w._reverify_on is False,
        "界面默认＝瀑布开、分层开、到期重验关（重验要多花请求，默认开＝默认烧钱）")
_assert(w.chk_waterfall.isChecked() and w.chk_lead.isChecked() and not w.chk_reverify.isChecked(),
        "控件显示的默认值与内部生效值一致（不是只有标志对、勾没打上）")
_assert(w.session.waterfall is True and w.session.lead_export is True
        and w.session.reverify is False,
        "构造期就把生效值推给了会话：只勾界面不推会话＝「界面亮着、功能没生效」那一类")

# ③ 唯一写入口：改一处，值 / 控件 / 会话三处一起动
w.chk_lead.setChecked(False)
_assert(not w._lead_report_on and not w.chk_lead.isChecked() and not w.session.lead_export,
        "取消「线索分层报表」走唯一写入口：三处同步（漏推会话就是下一轮口径不一致）")
_assert(any("台账每轮仍照记" in m for m in _o_logs) and not any("停更" in m for m in _o_logs),
        "关档那句话不撒谎：健康台账挂在采集收尾，与这条勾选无关")
w.chk_lead.setChecked(True)
_assert(w.session.lead_export is True, "再勾回来三处又一致（不是一次性的）")

# ④ 前置校验：没勾回采时瀑布开不了，且不许顺手替用户勾别的
w.chk_site.setChecked(True)
w.chk_site.setChecked(False)
_assert(not w._waterfall_on and not w.chk_waterfall.isChecked() and not w.session.waterfall,
        "关回采 → 瀑布跟着被收掉（留着勾就是一个「亮着但一行都不做」的开关）")
_assert(any("跟着一起关掉" in m for m in _o_logs), "级联收掉要在日志里说清为什么")
_o_boxes.clear()
_o_logs.clear()
w.chk_waterfall.setChecked(True)
_assert(not w._waterfall_on and not w.chk_waterfall.isChecked() and not w.session.waterfall,
        "前置不满足时就地回弹：三处都留在关（不是界面勾上、后台什么都不跑）")
_assert(not w.chk_site.isChecked(), "回弹不许替用户勾上「采集后回采」——他没要求过这个开关")
_assert(any("暂时开不了瀑布补全" in b and "再勾这条" in b for b in _o_boxes),
        f"当场弹窗点名缺哪一条、怎么补：{_o_boxes}")
_assert(not any("多源瀑布补全：开" in m for m in _o_logs),
        "回弹路径不许再打一句「已打开」（说开了又没开＝话术与行为分家）")

# ⑤ 反向不许自动打开；两条都勾上才算真开
_o_logs.clear()
w.chk_site.setChecked(True)
_assert(not w.chk_waterfall.isChecked() and not w.session.waterfall,
        "反向不打开：勾回采不会自动把瀑布勾上（他刚关的那条是自己的决定）")
w.chk_waterfall.setChecked(True)
_assert(w._waterfall_on and w.chk_waterfall.isChecked() and w.session.waterfall,
        "两条都勾上后瀑布才真开（前置校验不是永久拦路）")
w.chk_reverify.setChecked(True)
_assert(w.session.reverify is True and any("额外花网络请求" in m for m in _o_logs),
        "打开重验时把「要多花请求」讲在明面上（这一档的代价不能等跑了才发现）")
w.chk_reverify.setChecked(False)

# ⑥ 五级说法同源：界面、会话日志、引擎日志、「补全层级」列用的是同一串名字
_chain = wf.stage_chain()
_assert(_chain == " → ".join(fe.TIER_LABEL[s] for s in wf.STAGES) and "转工商补采" not in _chain,
        f"stage_chain 就是层级表里那五个值（{_chain}）——不是第二套命名")
_assert(wf.stage_no("registry") == len(wf.STAGES) and wf.stage_tag("registry")
        == f"第 {len(wf.STAGES)} 级（{wf.stage_name('registry')}）",
        f"序号由 STAGES 的位置算出来（{wf.stage_tag('registry')}）：以前界面数到⑤、"
        "引擎日志写「第 3 级」，同一件事两套编号")


def _o_missing_marks():
    """界面 tooltip 里缺了哪几级的「同源写法」。正向判据和反向对照跑的是**同一个表达式**——
    反向对照如果另写一套，它红不红都证明不了正向那条在盯什么。"""
    return [wf.stage_mark(s) for s in wf.STAGES if wf.stage_mark(s) not in w.chk_waterfall.toolTip()]


_missing = _o_missing_marks()
_assert(not _missing,
        f"瀑布 tooltip 把五级按同源序号点齐（缺：{_missing}）——用户就是照这段话决定要不要勾的")
_assert(wf.stage_name("registry") in w.chk_waterfall.toolTip(),
        f"tooltip 里的第 5 级就叫「{wf.stage_name('registry')}」（与「补全层级」列落库的值同名，"
        "以前写成「转工商补采」，用户拿表找不到界面讲过的那一级）")
_o_logs.clear()
w.chk_waterfall.setChecked(False)
w.chk_waterfall.setChecked(True)
_assert(any(_chain in m for m in _o_logs), "开关日志也念同一串顺序（不各写一份）")
_reg_line = "".join(wf.stage_lines({"registry": {"missing": 7, "queued": 5}}))
_assert(wf.stage_name("registry") in _reg_line and "转工商补采" not in _reg_line,
        f"分档账目那行也是同一个名字：{_reg_line}")
_assert("stage_chain()" in _ins.getsource(ps.ParallelSession._run_waterfall),
        "会话那句「顺序＝…」取自同源函数（在三处手抄同一串名字，改命名时必然漏一处）")
_ps_src = Path(ps.__file__).read_text(encoding="utf-8")
_copied = [wf.stage_name(s) for s in wf.STAGES if wf.stage_name(s) in _ps_src]
_assert(not _copied,
        f"会话层整份文件里不许再出现任何一级的手抄名字（一律走 stage_chain/stage_name）：{_copied}")

# ⑦ 快照：三条都登记，且恢复时不许弹模态框
snap = w._params_snapshot()
for _k in ("waterfall", "lead_report", "reverify"):
    _assert(_k in snap, f"任务快照登记了 {_k}（不登记＝「接着采」悄悄换成引擎默认口径）")
w.chk_site.setChecked(True)
w.chk_waterfall.setChecked(True)
w.chk_lead.setChecked(True)
w.chk_reverify.setChecked(True)
snap = w._params_snapshot()
w.chk_reverify.setChecked(False)
w.chk_lead.setChecked(False)
w.chk_waterfall.setChecked(False)
_o_boxes.clear()
_o_logs.clear()
w._apply_params(snap)
app.processEvents()
_assert(_o_boxes == [],
        f"恢复参数一个框都不弹（用户只是在接着采上一轮，被对话框按在原地是最坏的形态）：{_o_boxes}")
_assert(w._waterfall_on and w._lead_report_on and w._reverify_on,
        f"三处按快照原样恢复（快照里记着什么就照用什么）：{snap}")
_assert(w.session.waterfall and w.session.lead_export and w.session.reverify,
        "恢复完真下发到会话（只改内部标志的话，跑起来还是打乱后的口径）")

# ⑧ 老快照里的非法组合就地收敛，并说清收敛了什么
_o_logs.clear()
w._apply_params({"site_enrich": False, "waterfall": True, "lead_report": True, "reverify": False})
_assert(not w._waterfall_on and not w.chk_waterfall.isChecked() and not w.session.waterfall,
        "老快照「瀑布开 + 回采关」就地收敛（不收的话这份非法快照还会被原样记回下一轮）")
_assert(any("那两个开关后来有了依赖关系" in m for m in _o_logs),
        "收敛要点名：静默改用户的勾选，他会以为功能自己坏了")

# ⑨ 一个无关字段读坏，不许连累三条口径开关（原来整段是 except: pass）
w.chk_site.setChecked(True)
_o_logs.clear()
w._apply_params({"delay_between": "这不是数字", "waterfall": True,
                 "lead_report": False, "reverify": True, "site_enrich": True})
_assert(any("没恢复" in m for m in _o_logs),
        "坏字段必须出声点名（静默 pass 的话只看得见「参数好像没恢复」，查不到为什么）")
_assert(w._waterfall_on is True and w._lead_report_on is False and w._reverify_on is True,
        "质量三开关在大 try 之后单独恢复：不被一个无关字段（老的 delay_between 写法）连累")
_assert(w.session.waterfall and not w.session.lead_export and w.session.reverify,
        "上面那条判据的落点：会话上真是这个口径")

# ⑩ 界面话术里的数字与倍率取自单一真源（调 TTL 那一轮界面必须跟着换）
_rtip = w.chk_reverify.toolTip()
for _f in ("手机", "电话", "邮箱"):
    _assert(f"{_f} {fe.TTL_DAYS[_f]} 天" in _rtip,
            f"tooltip 里「{_f}」的保鲜期 = field_evidence 的单一真源（{fe.TTL_DAYS[_f]} 天）")
_assert(f"{fe.TTL_TIER_FACTOR['registry']} 倍" in _rtip
        and f"{fe.TTL_TIER_FACTOR['source']} 倍" in _rtip,
        "两档倍率也照抄真源：写成死数字的话，调倍率那轮界面还在讲旧天数")
_gu_src = Path(G.__file__).read_text(encoding="utf-8")
_dead = [f"{_f} {fe.TTL_DAYS[_f]} 天" for _f in ("手机", "电话", "邮箱")
         if f"{_f} {fe.TTL_DAYS[_f]} 天" in _gu_src]
_assert(not _dead, f"GUI 源码里不许出现写死的 TTL 天数（只能插值出来）：{_dead}")
_assert(w.session.LEAD_REPORT_FILENAME in w.chk_lead.toolTip(),
        f"「线索分层报表」把落盘文件名念出来（{w.session.LEAD_REPORT_FILENAME}）："
        "勾了要能在任务文件夹里找到那张表，找不到就成了静默少功能")

# ⑪ 反向对照：同源判据不是空转——改一处层级名，正向那条必须当场变红
_orig_label = fe.TIER_LABEL["registry"]
try:
    fe.TIER_LABEL["registry"] = "ZZZ临时改名"
    _assert(wf.stage_name("registry") == "ZZZ临时改名"
            and "ZZZ临时改名" in wf.stage_chain(),
            "阶段名现查层级表：改了层级表，stage_name / stage_chain 立刻跟着走"
            "（留一份导入期快照的话，改名的那一轮界面与日志都会留在旧名上）")
    _marks_after = _o_missing_marks()
    _assert(bool(_marks_after) and any("ZZZ临时改名" in m for m in _marks_after),
            "反向对照：上面那条「tooltip 点齐五级」用的就是同一个表达式，改名后它必须变红"
            f"（实测缺：{_marks_after}）")
finally:
    fe.TIER_LABEL["registry"] = _orig_label
_assert(wf.stage_name("registry") == _orig_label and not _o_missing_marks(),
        "层级名已还原，正向判据重新成立（不是靠还原前那条红撑着的）")

for _n2, _fn in _real_box.items():
    setattr(QMessageBox, _n2, _fn)
_assert(QMessageBox.information is not _o_box, "模态框桩已还原")

# ── 收尾：趁 QApplication 还活着把窗口拆掉（顺序与 b2bcollect_gui.main 的 finally 同一条）──
# 套件从不调 app.exec_()，也就绕过了生产那段有序收尾；而生产那段存在的理由正是「让解释器
# 自己拆 Qt 对象会撞上退出 SIGSEGV」（线上实测抓到过栈）。这里实测同样 6 次崩 1 次，且崩在
# 打印汇总**之后**——批跑只看退出码，全绿也会被判成一次失败。
# 走 hide() 而不是 close()：close 会进生产 closeEvent（等线程、弹确认框），本套件测的不是
# 退出那条路；hide 之后把引用清掉，refcount 当场让 sip 删掉 C++ 对象，此时 app 仍在。
w.hide()
w = None
app.processEvents()

print("\n" + "=" * 60)
total_n = _passed + _failed
print((f"全部通过 ✅ ({_passed}/{total_n})") if _failed == 0
      else f"通过 {_passed}/{total_n}，失败 {_failed} ❌")
print("=" * 60)
sys.exit(1 if _failed else 0)
