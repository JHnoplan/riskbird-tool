# -*- coding: utf-8 -*-
"""test_site_enrich_offline.py —— 功能④「采集的数据同步去官网拿联系方式」离线回归。

用户诉求原话：「如果数据有官网，同步去它的官网把电话和邮箱采集下来，如果数据本来就有
电话或邮箱，可以合并去重用英文逗号隔开」。这句话的正确性全在判据里，不在抓取动作里，
所以本套件按**判据 → 出口 → 会话 → 界面**四层钉住，全程不联网（fetch_html 与
socket.getaddrinfo 都换成本表驱动的实现，requests.get 上装硬闸，真发一次请求就判红）：

  A. 口径同源：留痕列名只许有一份写法（RAW_HEADERS/EXPORT_HEADERS/_base_rec/SITE_MARK 对齐）
  B. 「什么算官网」：norm_site 规整与拒绝形态、平台页边界口径、SSRF 闸（字面 IP 与 DNS 两分支）
  C. 证据分级：tel:/mailto:=3、标签语境=2、孤立=1、**只出现在 <script> 里=0**、
     负向语境（专利号/备案号/邮编/版权年）=0、建站商署名的 400 热线=0
  D. 排版盲区：号码被切成两个 span、区号后空格、4-3-3 的 400、带分机、86 字冠
  E. 号码/邮箱形态闸与显示归一（不改写出另一个号）
  F. 停放/被抢注/挂马、归属证据缺失 → 整站不采信（fail-closed，200 也不算）
  G. 合并出口：英文逗号去重、只追加不覆盖、手机/固话/邮箱各归各列、跨行净新增按值去重
  H. enrich_records 的账：同域名只请求一次、终态不再花预算、待重试累加、到上限转终态
     （且留痕与 needs_retry 自洽）、halt 后一律不落笔、单条异常不冒泡、就地写回原字典
  I. 会话层：site_enrich 默认关（引擎侧防「自称离线的用例真联网」）、手动 force 放行、
     preview 与真跑同一份判据、reset_results 清进程内缓存
  J. 界面层：手动按钮恒定存在、四条前置各回一句话、跑在后台线程、回来恢复按钮并落盘、
     退出时等这一轮、与采集/复位不并发、话术与代码不两张皮（docstring 承诺的入口必须真存在）

运行：python test_site_enrich_offline.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))

# 状态落点钉进沙箱：本套件会真造 MainWindow，绝不能把假任务写进开发机的真实索引
_STATE_SANDBOX = Path(tempfile.mkdtemp(prefix="site_state_"))
os.environ["B2B_STATE_DIR"] = str(_STATE_SANDBOX)
_DESK_SANDBOX = Path(tempfile.mkdtemp(prefix="site_desk_"))

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


import website_contact as wc                                        # noqa: E402
import task_registry as _tasks                                      # noqa: E402
from cdp_base import CdpSiteCrawler                                 # noqa: E402
from parallel_session import RAW_HEADERS, ParallelSession            # noqa: E402

_assert(str(_tasks.index_path()).startswith(str(_STATE_SANDBOX)), "隔离生效：任务索引落在沙箱")

# ── 离线硬闸：本表之外的域名一律报错，requests 真被调用就判红 ──────────
# 假 IP 一律用**真公网段**：203.0.113.0/24、198.51.100.0/24、192.0.2.0/24 这三段
# 虽然叫「文档用例段」，在 Python 的 ipaddress 里却归 is_private——拿它们当公网会让
# SSRF 闸按设计拦下夹具，红的是判据本身而不是被测代码。
_DNS_TABLE = {
    "www.mingliang-led.com": ["58.87.101.1"],
    "mingliang-led.com": ["58.87.101.1"],
    "www.a-b.com": ["58.87.101.2"],
    "www.c-d.com": ["58.87.101.3"],
    "www.e-f.com": ["58.87.101.4"],
    "www.slow-1.com": ["58.87.101.5"],
    "www.slow-2.com": ["58.87.101.6"],
    "www.slow-3.com": ["58.87.101.7"],
    "www.slow-4.com": ["58.87.101.8"],
    "intranet.corp": ["10.0.0.8"],
    "metadata.example": ["169.254.169.254"],
}
_dns_misses = []
_net_trips = []


def _fake_getaddrinfo(host, *a, **k):
    ips = _DNS_TABLE.get(str(host).lower().rstrip("."))
    if not ips:
        _dns_misses.append(host)
        raise OSError(f"离线用例查了表外域名：{host}")
    return [(2, 1, 6, "", (ip, 0)) for ip in ips]


_real_getaddrinfo = wc.socket.getaddrinfo
wc.socket.getaddrinfo = _fake_getaddrinfo

try:
    import requests as _requests
    _real_requests_get = _requests.get

    def _tripwire_get(*a, **k):
        _net_trips.append(a[:1] and str(a[0]) or "")
        raise AssertionError("离线用例真发出了 HTTP 请求")

    _requests.get = _tripwire_get
except Exception:                                     # requests 不在环境里也照常跑
    _requests = None
    _real_requests_get = None

_PAGES = {}           # host -> 首页 HTML（按 host 存，避免 norm_site 补出的斜杠差异坑夹具）
_calls = []           # 真被"访问"过的 URL（含重复，用于数请求次数）


def _fake_fetch(url, timeout=wc.TIMEOUT):
    _calls.append(url)
    html = _PAGES.get(wc.host_of(url))
    if html is None:
        return None, url, "不可达(ConnectionError)"
    return html, url, ""


_real_fetch = wc.fetch_html
wc.fetch_html = _fake_fetch


def _reset_net():
    _calls.clear()
    _net_trips.clear()
    _dns_misses.clear()
    wc.reset_caches()


# ── A. 口径同源 ─────────────────────────────────────────────
print("A. 留痕列口径同源")
_assert(wc.SITE_MARK == "官网回采", f"留痕列名固定（{wc.SITE_MARK}）")
_assert(RAW_HEADERS[-1] == wc.SITE_MARK, "原始表最后一列就是官网回采列")
_assert(CdpSiteCrawler.EXPORT_HEADERS[-1] == wc.SITE_MARK, "清洗导出表同列同名（两处不许各写一份）")
_rec = CdpSiteCrawler.__new__(CdpSiteCrawler)
CdpSiteCrawler.PLATFORM_NAME = "测试站"
_base = _rec._base_rec({"公司名称": "测试公司", "公司网址": "www.mingliang-led.com"})
_assert(wc.SITE_MARK in _base and _base[wc.SITE_MARK] == "",
        "_base_rec 建的每一行都带空的留痕列（空=本轮没对它跑过回采）")
_assert(list(RAW_HEADERS).count(wc.SITE_MARK) == 1, "留痕列只出现一次（重复列会让导出错位）")

# ── B. 什么算官网 / SSRF 边界 ───────────────────────────────
print("B. norm_site / 平台页边界 / SSRF")
_assert(wc.norm_site("www.mingliang-led.com") == "https://www.mingliang-led.com", "裸域名补 https")
_assert(wc.norm_site("  http://a-b.com/contact/  ") == "http://a-b.com/contact/", "去空白保留原协议")
_assert(wc.norm_site("“www.a-b.com”") == "https://www.a-b.com", "中文引号也剥掉")
_assert(wc.norm_site("暂无") == "" and wc.norm_site("-") == "", "中文占位符不当官网")
_assert(wc.norm_site("http://127.0.0.1:8080/x") == "", "非 web 端口不是官网（是扫描目标）")
_assert(wc.norm_site("javascript:void(0)") == "", "js 伪协议不发请求")
_assert(wc.norm_site("user:pass@a-b.com") == "", "带凭据的 URL 一律不发请求")
_assert(wc.norm_site("ftp://a-b.com") == "", "只走 http(s)")
_assert(wc.is_platform_host("qizhidao.com") and wc.is_platform_host("xxx.jdzj.com"),
      "平台页命中（含租户子域）")
_assert(not wc.is_platform_host("loco-china.cn"),
      "边界口径：裸子串会把 loco-china.cn 这类企业自有域名误杀（实测踩过的坑）")
_assert(wc.is_platform_host(""), "空 host 不当官网")
_assert(wc.is_public_host("58.87.101.1") is True, "公网 IP 放行")
_assert(wc.is_public_host("192.0.2.6") is False,
        "文档用例段在 ipaddress 里归私网：夹具不能拿它当公网（这条同时钉住闸的真实口径）")
_assert(wc.is_public_host("127.0.0.1") is False, "回环拦截")
_assert(wc.is_public_host("10.0.0.8") is False, "私网段拦截")
_assert(wc.is_public_host("169.254.169.254") is False, "元数据地址拦截")
_assert(wc.is_public_host("localhost") is False and wc.is_public_host("nas.local") is False,
      ".local / localhost 拦截")
_assert(wc.is_public_host("intranet.corp") is False, "域名解析到私网 → 字面看无害也拦（DNS 分支）")
_assert(wc.is_public_host("www.mingliang-led.com") is True, "解析到公网 → 放行")
_assert(wc.is_public_host("not-in-table.example") is True,
      "查不到不等于危险：判不出时不拦，让请求自己失败（不许把没网说成不安全）")
_assert(wc.pick_site({"公司网址": "https://xxx.jdzj.com/shop/1", "官网": "www.mingliang-led.com"})
        == "https://www.mingliang-led.com", "平台页跳过、按优先级取下一个像官网的字段")

# ── C. 证据分级 ─────────────────────────────────────────────
print("C. 证据分级（页面哪里出现的）")
PHONE = "021-12345678"
PAGE_TEL = ('<html><body><div>上海明亮照明</div>'
            f'<a href="tel:{PHONE}">{PHONE}</a></body></html>')
ph, em = wc.extract(PAGE_TEL)
gp, ge = wc.score_contacts(PAGE_TEL, ph, em)
_assert(gp and gp[0][1] == 3, f"tel: 链接=3 分（作者明确标注这是联系方式）：{gp}")
PAGE_MAIL = ('<html><body><div>明亮照明</div>'
             '<a href="mailto:info@mingliang-led.com">info@mingliang-led.com</a></body></html>')
ph2, em2 = wc.extract(PAGE_MAIL)
_assert(wc.score_contacts(PAGE_MAIL, ph2, em2)[1][0][1] == 3, "mailto: 链接=3 分")
PAGE_LABEL = ('<html><body><div>明亮照明 联系我们</div>'
              f'<p>电话：{PHONE}</p></body></html>')
ph3, em3 = wc.extract(PAGE_LABEL)
_assert(wc.score_contacts(PAGE_LABEL, ph3, em3)[0][0][1] == 2, "命中处 ±40 字有标签词=2 分")
PAGE_ALONE = ('<html><body><div>明亮照明</div>'
              '<p>厂区沿河路 88 号，西侧二期在建，欢迎参观。</p>'
              f'<p style="color:#999">{PHONE}</p></body></html>')
ph4, em4 = wc.extract(PAGE_ALONE)
_assert(wc.score_contacts(PAGE_ALONE, ph4, em4)[0][0][1] == 1, "可见但无标签语境=1 分（仍留但降级）")
PAGE_JS = ('<html><body><div>明亮照明</div><p>版权所有，翻录必究，页面正在加载。</p>'
           '<script>var dates = ["05172743000", "01041426525"];</script></body></html>')
ph5, em5 = wc.extract(PAGE_JS)
_assert(bool(ph5), "抽取器确实扫到了脚本里的数字串（不放宽判据，靠打分剔除）")
_assert(not wc.score_contacts(PAGE_JS, ph5, em5)[0],
      "只出现在 <script> 里=0 分直接丢弃（实测 05172743000 就是这样混进来的）")
PAGE_PATENT = ('<html><body><div>明亮照明</div>'
               '<p>专利号：ZL201310628718.0</p><p>更多专利详见国家知识产权局。</p></body></html>')
ph6, em6 = wc.extract(PAGE_PATENT)
_assert(not wc.score_contacts(PAGE_PATENT, ph6, em6)[0],
      "专利号里的数字串不算电话（NEG_CTX 命中降为 0）")
PAGE_AGENCY = ('<html><body><div>明亮照明</div>'
               '<p>技术支持：某某网络 400-888-1234</p></body></html>')
ph7, em7 = wc.extract(PAGE_AGENCY)
_assert(not wc.score_contacts(PAGE_AGENCY, ph7, em7)[0],
      "建站商署名的 400 热线不算厂家电话（有标签语境也不给分）")
PAGE_SPAM_MAIL = ('<html><body><div>明亮照明</div><p>联系邮箱 '
                  'test@example.com、sentry.wixpress.com、logo@2x.png</p></body></html>')
ph8, em8 = wc.extract(PAGE_SPAM_MAIL)
_assert(not wc.score_contacts(PAGE_SPAM_MAIL, ph8, em8)[1], "邮箱垃圾闸：示例域/图片名/sentry 都不算")

# ── D. 排版盲区 ─────────────────────────────────────────────
print("D. 排版盲区（判据没放宽，只是看得全了）")
# 切断位置按**真实排版**来：企业站把号码拆进两个标签时，分界只有「区号 | 本地号」和
# 「4 位 | 4 位」两处。刻意从中间任意切一刀（021-123 | 45678 这种 3-5 分组）去断言，
# 等于要求抽取器承认「任意空格都能拼成号码」——那判据就真放宽了，日期和编号会一起被拼进来。
_ph_ac = wc.extract('<p><span>021-</span><span>12345678</span></p>')[0]
_assert(_ph_ac == ["021- 12345678"], f"区号后被切成两个标签也能拼回来：{_ph_ac}")
_assert(_ph_ac and wc.normalize_display(_ph_ac[0]) == PHONE,
        "拼回来的带空格写法，出口前归一成可直接外呼的号")
_assert(wc.extract('<p><span>021-1234</span><span>5678</span></p>')[0] == [PHONE],
        "本地号 4-4 分段被切成两个标签也能拼回来")
_assert(wc.extract('<p>电话：010- 64229311</p>')[0], "区号后隔一格的排法能命中")
_assert(wc.extract('<p>服务热线 4008-123-456</p>')[0] == ["4008-123-456"],
        "400 的 4-3-3 分段写法能命中")
_assert(wc.extract('<p>总机：021-12345678-转123</p>')[0] == [PHONE],
        "带分机的总机收主号：不把「-转123」整串当号（外呼拨不通），也不拆出个假号")
_assert(len(wc.extract(f'<p>{PHONE}</p><p>{PHONE[:4]} {PHONE[4:]}</p>')[0]) == 1,
        "同一号码两种排版只留第一次出现（去重按数字串）")

# ── E. 形态闸与显示归一 ─────────────────────────────────────
print("E. plausible_* / normalize_display")
_assert(wc.plausible_phone("13800138138") is True, "正常手机号放行")
_assert(wc.plausible_phone("13800138000") is False, "演示号段拉黑")
_assert(wc.plausible_phone("11111111111") is False, "叠字/同字符不算号")
_assert(wc.plausible_phone("0211234567") is False, "区号+7 位且无分隔一律拒（已基本退役）")
_assert(wc.plausible_phone("021-12345678") is True, "区号带分隔的 8 位本地号放行")
_assert(wc.plausible_phone("021-32563256") is False,
        "本地号前后叠词（3256 重复两遍）是页面数字，不是电话")
_assert(wc.plausible_email("sale@mingliang-led.com") is True, "企业邮箱放行")
_assert(wc.plausible_email("noreply@mingliang-led.com") is False, "noreply 不算联系人邮箱")
_assert(wc.normalize_display("010- 64229311") == "010-64229311", "排版归一成可直接外呼的写法")
_assert(wc.normalize_display("+86 21 36129977") == "021-36129977", "86 字冠归一")
_assert(wc.normalize_display("400-888-1234") == "400-888-1234", "400 不乱改（改了就是另一个号）")

# ── F. 停放 / 归属：fail-closed ─────────────────────────────
print("F. 停放与被劫持的官网整站不采信")
PARKED = ('<html><body><title>buy this domain</title><div>明亮照明</div>'
          f'<p>电话：{PHONE}</p></body></html>')
_assert(wc._grade_pages([(PARKED, "http://www.a-b.com/", "首页")], "上海明亮照明有限公司")[3] is True,
        "停放页判 dead（200 可达 + 页上写着号码也不采信）")
ENTITY_PARKED = ('<html><body><div>&#39;域名&#x5df2;&#x6ce8;&#x518c;&#39;</div>'
                 f'<p>电话：{PHONE}</p></body></html>')
_assert(wc.PARKED_RE.search(wc.strip_tags(ENTITY_PARKED)),
        "先解 HTML 实体再判：灰产标题写成实体形式时正则也要看得见")
BODY_LOST = ('<html><body><div>某家电俱乐部 电子竞技 投注</div>'
             f'<p>电话：{PHONE}</p></body></html>')
_assert(wc._grade_pages([(BODY_LOST, "http://www.a-b.com/", "首页")],
                        "上海明亮照明有限公司")[2], "被抢注挂灰产内容 → 给出人话原因")
NO_ATTR = ('<html><body><div>某某食品加工厂</div>'
           f'<p>电话：{PHONE}</p></body></html>')
gp_n, _ge_n, why_n, dead_n = wc._grade_pages(
    [(NO_ATTR, "http://www.mingliang-led.com/", "首页")], "广东旭日智能装备有限公司")
_assert(not gp_n and "认不出主体" in why_n and dead_n is False,
        f"归属证据一条都没有 → 整站不采信（fail-closed）：{why_n}")
gp_y, _ge_y, why_y, _d = wc._grade_pages(
    [('<html><body><div>明亮照明工程有限公司官网</div>'
      f'<a href="tel:{PHONE}">{PHONE}</a></body></html>',
      "http://www.mingliang-led.com/", "首页")], "上海明亮照明有限公司")
_assert(bool(gp_y) and not why_y, "页面认得出这家公司（品牌段见于正文）才采信")
_assert(wc.brand_grams("上海明亮照明有限公司") == {"明亮", "亮照", "照明"},
        "区划与组织形式不进品牌段（否则整页正文都能「对上」，归属判据形同没有）")
_assert(wc.has_attr("", "任意正文", "unknown-host.cn") is True,
        "名字规整后一个可判段都不剩时不强卡（fail-open：那是行本身缺名字，不是官网易主）")
_assert(wc.has_attr("某某贸易有限公司", "任意正文里没这个词", "unknown-host.cn") is False,
        "有可判段但页面对不上 → 不采信（fail-closed 才是默认，fail-open 只留给无可判段）")

# ── G. 合并出口 ─────────────────────────────────────────────
print("G. 合并去重（用户要的就是这句：英文逗号隔开）")
_assert(wc.merge_values("021-12345678", ["021-1234 5678", "021-87654321"])
        == "021-12345678,021-87654321", "同值的另一种排版不再追加，异值用英文逗号合并")
_assert(wc.merge_values("", ["a@b-c.com"]) == "a@b-c.com", "原本没有也能追加")
_assert(wc.merge_values("021-12345678／021-87654321", []) == "021-12345678,021-87654321",
      "原值里的全角斜杠写法拆开重排（分隔符集合刻意不含空格）")
_assert(wc.merge_values("13800138138 ", ["13800138138"]) == "13800138138",
      "已有值不会被覆盖成别的号（只追加、不覆盖）")
_assert(wc.split_mobile("13800138138") and not wc.split_mobile(PHONE), "手机/固话分列判据")

# ── H. enrich_records 的账 ─────────────────────────────────
print("H. 批量回采的账（真线程池 + 假网络）")
GOOD = ('<html><body><div>明亮照明 联系我们</div>'
        f'<a href="tel:{PHONE}">{PHONE}</a>'
        '<a href="mailto:sale@mingliang-led.com">sale@mingliang-led.com</a>'
        '<p>手机 13800138138</p></body></html>')
NOWHERE = ('<html><body><div>明亮照明</div>'
           '<p>本公司专注道路照明，欢迎参观指导。</p></body></html>')
UNREACH_HTML = None


def _rec(name, site, **kw):
    r = {"公司名称": name, "公司网址": site, "电话": "", "手机": "", "邮箱": "", wc.SITE_MARK: ""}
    r.update(kw)
    return r


_reset_net()
_PAGES.clear()
_PAGES["www.mingliang-led.com"] = GOOD
_PAGES["www.a-b.com"] = NOWHERE
rows = [_rec("上海明亮照明有限公司", "www.mingliang-led.com"),
        _rec("上海明亮照明有限公司（第二家门店）", "www.mingliang-led.com"),
        _rec("上海明亮照明有限公司", "https://www.a-b.com/")]
_ref0 = rows[0]
st = wc.enrich_records(rows, workers=1, budget=30, limit=50)
_assert(_calls and len([u for u in _calls if wc.host_of(u) == "www.mingliang-led.com"]) == 1,
        f"同一官网只请求一次（本页访问 "
        f"{len([u for u in _calls if wc.host_of(u) == 'www.mingliang-led.com'])} 次）")
_assert(_ref0["电话"] == PHONE and "sale@mingliang-led.com" in _ref0["邮箱"],
        f"就地写回原字典：电话={_ref0['电话']} 邮箱={_ref0['邮箱']}")
_assert(_ref0["手机"] == "13800138138", "手机号进「手机」列、固话进「电话」列（不串列）")
_assert(wc.SITE_MARK in _ref0 and "净新增" in _ref0[wc.SITE_MARK],
        "每行留下可复核的账：" + _ref0[wc.SITE_MARK])
_assert(st["ok"] == 2 and st["new_values"] == 3,
        f"跨行去重后的净新增按「值」算：{st['new_values']}（同一家两条记录不重复计数）")
_assert(st["total"] == 3 and st["marked"] == 3, "三条都落了笔（含没出值那条，它也有一条账）")
_assert(rows[2][wc.SITE_MARK].startswith(wc.SITE_MARK) and
        not rows[2][wc.SITE_MARK].startswith(wc.RETRY_MARK),
        "「页面没号码」是终态、不进重试队列：" + rows[2][wc.SITE_MARK])

# 终态留痕不再花第二次预算
_reset_net()
row_done = _rec("上海明亮照明有限公司", "www.mingliang-led.com")
row_done[wc.SITE_MARK] = f"{wc.SITE_MARK}：官网疑似停放/被劫持（整站不采信）"
st2 = wc.enrich_records([row_done], workers=1, budget=30, limit=50)
_assert(_calls == [], "已判终态的行不再请求（预算留给没试过的站）")
_assert(st2["cached"] == 1 and st2["eligible"] == 0, f"账上记成「沿用上次结论」：cached={st2['cached']}")

# 待重试累加 → 到上限转终态（留痕与 needs_retry 必须自洽）
_reset_net()
row_transient = _rec("上海明亮照明有限公司", "www.c-d.com")      # 表内没页面 → 不可达
st3 = wc.enrich_records([row_transient], workers=1, budget=30, limit=50)
_assert(row_transient[wc.SITE_MARK].startswith(wc.RETRY_MARK)
        and "(1次)" in row_transient[wc.SITE_MARK],
        "暂时失败留「待重试(1次)」：" + row_transient[wc.SITE_MARK])
_assert(wc.needs_retry(row_transient[wc.SITE_MARK]) is True, "待重试仍排队（企业站停机是常态）")
_reset_net()
row_transient[wc.SITE_MARK] = f"{wc.RETRY_MARK}(2次)：不可达(ConnectionError)"
wc.enrich_records([row_transient], workers=1, budget=30, limit=50)
_assert(not wc.needs_retry(row_transient[wc.SITE_MARK]),
        f"到上限转终态：{row_transient[wc.SITE_MARK]}")
_assert("清空这一列" in row_transient[wc.SITE_MARK],
        "终态留痕写明怎么重开（不许留一句永远兑不到的承诺）")
_assert(not row_transient[wc.SITE_MARK].startswith(wc.RETRY_MARK),
        "转终态后不再挂「待重试」字样（文案与排队行为一致）")

# halt 之后一律不落笔：慢站 + 极小预算，让「已认领但没写完」真的发生
_reset_net()


def _slow_fetch(url, timeout=wc.TIMEOUT):
    import time as _t
    _t.sleep(0.25)
    return _fake_fetch(url, timeout)


_slow_real = wc.fetch_html
wc.fetch_html = _slow_fetch
try:
    slow_rows = [_rec(f"上海明亮照明有限公司{i}", f"www.slow-{i + 1}.com") for i in range(4)]
    for i in range(1, 5):
        _PAGES[f"www.slow-{i}.com"] = GOOD
    st4 = wc.enrich_records(slow_rows, workers=1, budget=0.35, limit=50)
finally:
    wc.fetch_html = _slow_real
_assert(st4["budget_hit"] >= 1 and st4["skipped"] >= 1,
        f"预算到点如实报剩余量：budget_hit={st4['budget_hit']} skipped={st4['skipped']}")
_dirty = [r for r in slow_rows if r[wc.SITE_MARK] == "" and (r["电话"] or r["手机"] or r["邮箱"])]
_assert(not _dirty,
        "被 halt 拦在半路的行一个字都不写（不留「已认领未写完」的脏值）：" + str(len(_dirty)) + " 行")

# 单条内部异常不冒泡
_reset_net()


def _boom_fetch(url, timeout=wc.TIMEOUT):
    raise RuntimeError("假实现故意炸")


_orig = wc.fetch_html
wc.fetch_html = _boom_fetch
try:
    st5 = wc.enrich_records([_rec("上海明亮照明有限公司", "www.mingliang-led.com")],
                            workers=1, budget=30, limit=50)
finally:
    wc.fetch_html = _orig
_assert(st5["marked"] == 0 and any(k.startswith("内部异常") for k in st5["reasons"]),
        f"工作线程里的异常被收口成一条原因，不冒泡：{st5['reasons']}")
_assert(_net_trips == [], "全程没有一次真 HTTP 请求（requests.get 硬闸从未被触发）")

# ── I. 会话层 ───────────────────────────────────────────────
print("I. 会话层接线（ParallelSession）")
sess = ParallelSession()
logs = []
sess._log = lambda m: logs.append(str(m))
sess._user_stop = False
_assert(sess.site_enrich is False, "引擎默认关：防「自称离线的用例真联网」，界面上的勾选才是默认开")
sess_rows = [_rec("上海明亮照明有限公司", "www.mingliang-led.com"),
             _rec("某某食品公司", "")]
sess._all_live_records = lambda: list(sess_rows)
_reset_net()
_assert(sess.enrich_websites(reason="离线用例") is None and _calls == [],
        "未勾选时早退，一次请求都不发")
_assert(any("未勾选" in m and "不回采" in m for m in logs),
        "没做也要说清为什么没做：" + next((m for m in logs if "未勾选" in m), ""))
_reset_net()
sess.site_enrich = True
sess.site_enrich_workers = 1
sess.site_enrich_budget = 30
sess.site_enrich_limit = 50
s_stats = sess.enrich_websites(reason="离线用例")
_assert(s_stats and s_stats["ok"] >= 1, f"勾选后自动收尾轮真跑：{s_stats and s_stats['ok']} 行补到值")
_assert(any("净新增" in m for m in logs), "收尾账目里写着净新增几个联系方式")
_reset_net()
sess.site_enrich = False
# 上面那一轮已经把 sess_rows 标成终态了（终态不再花第二次预算，那是 H 节钉住的判据），
# 所以手动这一轮必须换一批没试过的行——否则 _calls 为空是「按设计跳过」，红的是夹具。
sess._all_live_records = lambda: [_rec("上海明亮照明有限公司", "www.mingliang-led.com")]
sess.enrich_websites(reason="手动补采", force=True)
_assert(bool(_calls), "force=True（界面手动点的那一次）不被链路开关拦住")
_assert(any("手动补采" in m and "照跑" in m for m in logs),
        "自动回采没勾选但手动点了：日志当场说清这是手动放行")
sess_rows2 = [_rec("上海明亮照明有限公司", "www.mingliang-led.com")]
sess._all_live_records = lambda: list(sess_rows2)
total, with_site, missing = sess.site_enrich_preview()
_assert((total, with_site, missing) == (1, 1, 1),
        f"preview 与真跑同一份判据：{(total, with_site, missing)}")
sess._all_live_records = lambda: [_rec("某某食品公司", "https://xxx.jdzj.com/shop/1")]
_assert(sess.site_enrich_preview()[1] == 0, "preview 不把平台页算成可回采官网")

# ── J. 界面手动入口 ─────────────────────────────────────────
print("J. 界面手动入口（offscreen 真 MainWindow）")
import b2bcollect_gui as G                                        # noqa: E402
from PyQt5.QtWidgets import QApplication, QMessageBox              # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
boxes = []
QMessageBox.information = staticmethod(
    lambda *a, **k: boxes.append(" | ".join(str(x) for x in a[1:3])) or QMessageBox.Ok)
QMessageBox.warning = staticmethod(
    lambda *a, **k: boxes.append(" | ".join(str(x) for x in a[1:3])) or QMessageBox.Ok)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)
G.MainWindow._get_desktop_dir = lambda self: str(_DESK_SANDBOX)
w = G.MainWindow()
w.show()
app.processEvents()
ui_logs = []
w._on_log = lambda m: ui_logs.append(str(m))
saved = []
w.request_autosave = lambda: saved.append(1)

_assert(hasattr(w, "btn_site_now") and w.btn_site_now is not None, "手动补采按钮已建出")
_assert(w.btn_site_now.text() == w.BTN_SITE_ENRICH,
        f"按钮文本取自单一常量（{w.BTN_SITE_ENRICH}），恢复时不会写成另一句")
_assert(w.btn_site_now.isVisibleTo(w), "按钮在任何档位都可见（不是藏起来的功能）")
_assert(w.btn_site_now.toolTip() and "英文逗号" in w.btn_site_now.toolTip(),
        "按钮自带说明写清合并口径")
_assert("不用重跑采集" in w.chk_site.toolTip(),
        "勾选框文案指向手动按钮（不再教用户重跑一轮采集去触发收尾）")

w.session._all_live_records = lambda: []
boxes.clear()
w.btn_site_now.click()
app.processEvents()
_assert(any("没有数据" in b for b in boxes), "没数据时点它：说清没东西可补，而不是点了没反应")
_assert(w._site_worker is None, "没数据时不起线程")

w.session._all_live_records = lambda: [_rec("某某食品公司", "")]
boxes.clear()
w.btn_site_now.click()
app.processEvents()
_assert(any("没有可回采的官网" in b for b in boxes), "有数据但一行官网都没有：单独一句话说明白")
_assert(w._site_worker is None, "无官网时也不起线程")

w.session._all_live_records = lambda: [_rec("上海明亮照明有限公司", "www.mingliang-led.com")]


class _BusyWorker:
    def isRunning(self):
        return True


w.worker = _BusyWorker()
ui_logs.clear()
w.btn_site_now.click()
app.processEvents()
_assert(any("采集还在跑" in m for m in ui_logs), "采集进行中点它：说明白为什么这次不动")
_assert(w._site_worker is None, "采集进行中不起并发的一轮")
w.worker = None

_calls.clear()
w.chk_site.setChecked(False)
ui_logs.clear()


class _Sig:
    """替 pyqtSignal：connect 记下槽、emit 当场调用——回调链路照原样验，不必真起线程。"""

    def __init__(self):
        self.cb = None

    def connect(self, fn):
        self.cb = fn

    def emit(self, value):
        if self.cb is not None:
            self.cb(value)


class _FakeThread:
    """替掉真线程：跑起来时不发结果，由测试在验完「在途形态」后手动 emit。"""

    def __init__(self, session):
        self.session = session
        self.done = _Sig()
        self._running = True
        _threads.append(self)

    def isRunning(self):
        return self._running

    def start(self):
        pass        # 真线程在这里才会去联网；离线用例把结果交给测试手动发回


_threads = []
_orig_thread = G.SiteEnrichWorker
G.SiteEnrichWorker = _FakeThread
w.btn_site_now.click()
app.processEvents()
G.SiteEnrichWorker = _orig_thread
_assert(bool(_threads), "前置都过了：起了一轮补采（走的是后台线程，不在 GUI 线程里联网）")
_assert(w.btn_site_now.isEnabled() is False and w.btn_site_now.text() == "补采中…",
        "跑起来时按钮自己表态（灰着 + 改字），不用用户猜有没有点上")
_assert(w.chk_site.isChecked() is False,
        "手动点这一次不改用户的勾选状态（那条开关管的是自动跑）")
_assert(any("手动补采开始" in m for m in ui_logs),
        "点下去当场报账：" + next((m for m in ui_logs if "手动补采开始" in m), ""))
ui_logs.clear()
w.btn_site_now.click()          # 故意在途再点一次
app.processEvents()
_assert(len(_threads) == 1 and not ui_logs, "上一轮还在跑时不叠第二轮（也不留一句噪音日志）")
ui_logs.clear()
saved.clear()
_threads[0].done.emit({"ok": 1, "sites": 1, "new_values": 3, "marked": 1})
app.processEvents()
_assert(w.btn_site_now.isEnabled() and w.btn_site_now.text() == w.BTN_SITE_ENRICH,
        "回来恢复按钮（漏恢复就等于要用户重启工具）")
_assert(any("手动补采结束" in m for m in ui_logs), "回来报一次账")
_assert(saved, "补完自动落盘（用户要的是表格里的值，不是内存里的值）")
saved.clear()
ui_logs.clear()
_threads[0].done.emit(None)
app.processEvents()
_assert(not saved and any("没有实际访问官网" in m for m in ui_logs),
        "没跑成的那一轮不假装落盘，只说明原因")
_assert(w.btn_site_now.isEnabled(), "异常收口后按钮同样恢复")

# 真线程类本身：异常也必须发 done（卡在「补采中…」是最坏的失败形态）
_run_done = []
w.session.enrich_websites = lambda **kw: (_ for _ in ()).throw(RuntimeError("故意炸"))
w._site_worker = None
t = G.SiteEnrichWorker(w.session)
t.done.connect(lambda v: _run_done.append(v))
_orig_log = w.session._log
w.session._log = lambda m: None
t.run()
w.session._log = _orig_log
_assert(_run_done == [None], "线程里炸了也照发 done：界面绝不会停在「补采中…」")
w.session.enrich_websites = ParallelSession.enrich_websites.__get__(w.session)

# 并发拒止与退出收口（源码级判据：这些路径点到界面上验不全）
_gsrc = Path(G.__file__).read_text(encoding="utf-8")
_ci = _gsrc.index("def closeEvent", _gsrc.index("class MainWindow"))
_close = _gsrc[_ci:_ci + 3000]
_assert("_site_worker" in _close and "_final_save_sync()" in _close,
        "退出时等这一轮并把已补到的值同步落盘（排队信号在退出路径上不会跑）")
_start = _gsrc[_gsrc.index("def _on_start"):_gsrc.index("def _on_start") + 6000]
_assert("_site_worker" in _start, "补采在跑时「开始采集」拒并发（同一份官网缓存与预算）")
_reset = _gsrc[_gsrc.index("def _on_reset_task"):_gsrc.index("def _on_reset_task") + 4000]
_assert("_site_worker" in _reset, "补采在跑时「开始新任务」拒并发（否则补到的值被复位丢掉）")
_ps = Path(G.__file__).with_name("parallel_session.py").read_text(encoding="utf-8")
_doc = _ps[_ps.index("def enrich_websites"):_ps.index("def site_enrich_preview")]
_assert("手动" in _doc and "force" in _doc, "会话 docstring 说的手动入口真存在")
_assert('enrich_websites(reason="手动补采", force=True)' in _gsrc,
        "界面那一处确实用 force 走通了 docstring 承诺的那条路（说到必须做到）")

# ── 收口：把网络桩全部还原，证明本套件没有留下后门 ─────────────
wc.fetch_html = _real_fetch
wc.socket.getaddrinfo = _real_getaddrinfo
if _requests is not None and _real_requests_get is not None:
    _requests.get = _real_requests_get
_assert(wc.fetch_html is not _fake_fetch and wc.socket.getaddrinfo is _real_getaddrinfo,
        "桩已还原（本套件跑完不影响后续套件的真实性）")
_assert(_net_trips == [], "整轮从未发出真请求")

print("\n" + "=" * 60)
total_n = _passed + _failed
print((f"全部通过 ✅ ({_passed}/{total_n})") if _failed == 0
      else f"通过 {_passed}/{total_n}，失败 {_failed} ❌")
print("=" * 60)
sys.exit(1 if _failed else 0)
