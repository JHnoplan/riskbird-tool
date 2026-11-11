# 离线自测：麦麦B2B(www.maimaib2b.com) 新站点关键逻辑（真实页面缓存，不触网、不开浏览器）
#
# 覆盖 2026-11-02 实机探测确认的几个关键点：
#   · 站前挂着 Cloudflare：403/200 + 5.8KB「Just a moment」薄壳必须判成**可人工过**的
#     captcha（借标签过盾抄 cf_clearance），而不是只能干等的 IP 限流；
#   · cf_clearance 绑 UA，抄 Cookie 必须连 User-Agent 一起同步回 HTTP 会话；
#   · 列表按页脚「当前第X页，共Y页」精确收尾（实测 空调 53 页/1042 家）；
#   · 商家把固话填进「手机」栏 → 一律按号码形态重新分列；86- 国际前缀归一；
#   · 站方默认/灌填的假号、联系人栏写「未开」都不能入库；「公司官网」栏写着
#     自己的商铺地址，不能当客户官网。
import sys, pathlib, re, json, time
sys.path.insert(0, "/Users/jhnoplan/Desktop/PythonDemo/b2bcollect_build")

import cdp_base                                                   # noqa: E402
from maimaib2b_crawler import Maimaib2bCrawler                    # noqa: E402
from parallel_session import ParallelSession, SITE_ORDER, SITE_CLASSES, SITE_NAMES  # noqa: E402

# 把「碰真浏览器」的那扇门从根上关掉（2026-11-03 踩到）：_ws_url 在「pin 标签找不到」时会按域名
# 在当前 Chrome 里找回一个标签接着跑 JS——本机正有采集任务开着本站标签时，标着「不触网、不开浏览器」
# 的离线断言就会偷偷连到那个真标签上，把它的标签 id 当成自己的（pin_tab_id 被换掉、断言连带跑偏）。
cdp_base.CdpSiteCrawler._get_tabs = lambda self: []
cdp_base.CdpSiteCrawler._ws_url = lambda self: ""

CACHE = pathlib.Path(__file__).parent / "probe_maimaib2b_cache"
def cached(name):
    return (CACHE / name).read_text(encoding="utf-8", errors="ignore")

ok = 0
def check(name, cond):
    global ok
    assert cond, f"FAIL: {name}"
    ok += 1
    print(f"  ✓ {name}")

c = Maimaib2bCrawler()
c.incremental = False                 # 自测绝不写种子库

# ── 1. 链路形态：平时不占标签、被质询才借一个标签过盾 ──────────
check("NEEDS_BROWSER=False（平时不占浏览器标签）", c.NEEDS_BROWSER is False)
check("BLOCK_OPEN_BROWSER=True（被 CF 质询时借标签过盾）", c.BLOCK_OPEN_BROWSER is True)
check("BLOCK_COOKIE_DOMAIN=maimaib2b.com（过盾后抄 clearance 回会话）",
      c.BLOCK_COOKIE_DOMAIN == "maimaib2b.com")
check("节流下沉到每个请求（PER_REQUEST_PACE + 3s 下限）",
      c.PER_REQUEST_PACE is True and c.MIN_DELAY >= 3.0)
check("插页体积阈值抬到 15000（CF 壳 5.8KB vs 正文 24~47KB）", c.HTTP_BLOCK_MAX_BYTES >= 15000)

# ── 2. Cloudflare 薄壳分诊（真实缓存：无 Cookie 裸 HTTP 拿到的壳）──
shell = cached("shop_2.html")
check(f"CF 薄壳判 captcha 而非干等（{c.http_block_reason(403, shell, 'u')}）",
      bool(c.http_block_reason(403, shell, "u")) and c._block_kind == "captcha")
check("200 状态的薄壳也认得出（按体积+特征词）",
      "Just a moment" in (c.http_block_reason(200, shell, "u") or "") and c._block_kind == "captcha")
body = cached("http_search_company_空调.html")
check("正常正文页绝不误判成插页", c.http_block_reason(200, body, "u") == "")

# ── 3. 列表（真实缓存：/search/Company/空调/ 第 1 页）──────────
entries = c.parse_list(body, "空调")
check(f"列表解析出 {len(entries)} 家/页", len(entries) == 20)
check("公司ID=店铺 id 且唯一", len({e["公司ID"] for e in entries}) == len(entries)
      and all(re.fullmatch(r"\d+", e["公司ID"]) for e in entries))
check("详情URL 指向 /Shop/<id>/link.html",
      all(e["详情URL"] == f"https://www.maimaib2b.com/Shop/{e['公司ID']}/link.html" for e in entries))
check("公司名取 title 属性（不带 <font><b> 高亮标签）",
      all("<" not in e["公司名称"] and "font" not in e["公司名称"] for e in entries))
check("列表卡片上的电话带进详情兜底（列表电话）",
      sum(1 for e in entries if e["列表电话"]) >= 10)
check(f"页头「共N条」= {c.slice_total(body)} 家", c.slice_total(body) == 1042)
check("首页 URL 无 p 段、翻页带 /p<N>/",
      c.build_search_url("空调", 1) == "https://www.maimaib2b.com/search/Company/%E7%A9%BA%E8%B0%83/"
      and c.build_search_url("空调", 3).endswith("/p3/"))
check("按页脚「共53页」收尾：第 1 页还要翻、第 53 页到底",
      c.list_has_more(body, 1) is True and c.list_has_more("当前第53页，共53页，共1042条记录", 53) is False)

# ── 4. 详情：/Shop/<id>/link.html 左右两块合并（真实缓存 4 家）──
link = {sid: c._parse_link(cached(f"cdp_link_{sid}.html"), {}) for sid in ("387364", "703072", "773605")}
r = link["387364"]
check(f"固话明文（{r.get('电话')}）", r.get("电话") == "0755-89810939")
check(f"公司名取 <h1>（{r.get('公司名称')}）", r.get("公司名称") == "深圳得盈机械设备有限公司")
check(f"联系人（{r.get('联系人')}）", bool(r.get("联系人")))
check("地址行「省 市 区」不残留一串空白", "   " not in r.get("地址", "") and r.get("地址", "").startswith("广东 深圳"))
check(f"地址前两级当所在区域（{c._region_from(r.get('地址', ''))}）",
      c._region_from(r.get("地址", "")) == "广东深圳")
# 2026-11-03「光伏」自测实测：直辖市那种地址只切两段，第二段把市/区/镇/村全粘在
# 一起（「天津 天津市静海区大邱庄镇…」），旧写法把整条地址塞进了「所在区域」列
check("直辖市地址（市、区、镇、村粘成一段）只收到区划两级",
      c._region_from("天津 天津市静海区大邱庄镇前尚码头村西1000米") == "天津市静海区"
      and c._region_from("北京 北京市昌平区回龙观东大街28号") == "北京市昌平区")
check("正常三段地址、两段写法都不受影响", c._region_from("江苏 苏州 姑苏区 石路街道") == "江苏苏州"
      and c._region_from("云南昆明") == "云南昆明" and c._region_from("123 456") == "")
check("「公司官网」栏写自家商铺地址 → 不当客户官网", "maimaib2b.com" not in r.get("公司网址", ""))
r = link["703072"]
check(f"手机/固话同时给（{r.get('手机')}／{r.get('电话')}）",
      r.get("手机") == "18928697860" and r.get("电话") == "0757-29395639")
r = link["773605"]
check(f"商家把固话填进手机栏也按形态归列（{r.get('电话')}）",
      r.get("电话") == "0530-6838858" and r.get("手机") == "15215306768")
check("联系人栏写「未开」→ 不当人名（占位词作废、宁缺毋滥）",
      not link["773605"].get("联系人"))
check("纯性别称谓不立岗位列", not any(x.get("岗位") == "先生" for x in link.values()))
check("关键词串店名只留首段（端到端实跑撞到的形态）",
      c._clean_name("深圳空调安装|深圳空调拆装|深圳空调维修|深圳空调加雪种") == "深圳空调安装")
check("正常店名不被竖线清洗误伤",
      c._clean_name("深圳得盈机械设备有限公司") == "深圳得盈机械设备有限公司"
      and c._clean_name("山东启杨净化设备") == "山东启杨净化设备")

# ── 5. 假号与首页底部兜底（真实缓存：944956 全套）──────────────
p944 = c._parse_link(cached("cdp__hop_944956_link_html.html"), {})
check("站方灌填的假号 15805447789/0534-8222227 绝不入库",
      "15805447789" not in json.dumps(p944, ensure_ascii=False)
      and "0534-8222227" not in json.dumps(p944, ensure_ascii=False))
check("link.html 无号码时仍能拿邮箱/官网/地址",
      p944.get("邮箱") == "dezhouyingyang@163.com" and p944.get("公司网址", "").startswith("https://"))
bottom = c._parse_bottom(cached("cdp_shop_944956.html"))
check(f"商铺首页底部「联系我们」补名称/地址（{bottom.get('地址')}）",
      bottom.get("公司名称") == "德州市陵城区盈阳环保材料中心" and bool(bottom.get("地址")))
check("底部那对假号同样不入库",
      "15805447789" not in json.dumps(bottom, ensure_ascii=False)
      and "0534-8222227" not in json.dumps(bottom, ensure_ascii=False))

# ── 6. crawl_detail 全流程（_fetch 走缓存，不触网）─────────────
c._sleep = lambda: None
asked = []
def route(url, tries=3, guarded=True, referer=""):
    asked.append(url)
    if url.endswith("link.html"):
        return cached(f"cdp_link_{url.split('/Shop/')[1].split('/')[0]}.html")
    return ""                         # 商铺首页：本样本不需要兜底
c._fetch = route
rec = c.crawl_detail({"公司名称": "佛山市金联兴不锈钢有限公司", "公司ID": "703072",
                      "公司网址": "https://www.maimaib2b.com/Shop/703072/", "详情URL": "",
                      "列表电话": "", "所在区域": "", "主营产品": ""})
check("详情采到 手机+固话+联系人 → 状态成功",
      rec.get("状态") == "成功" and rec.get("手机") == "18928697860" and rec.get("联系人") == "肖泽峰")
check("link.html 有号码就不多发商铺首页请求（省一次请求）",
      [u for u in asked if u.endswith("/") or "link.html" not in u] == [])

asked.clear()
def route2(url, tries=3, guarded=True, referer=""):
    asked.append(url)
    if "link.html" in url:
        return cached("cdp__hop_944956_link_html.html")
    return cached("cdp_shop_944956.html")
c._fetch = route2
rec = c.crawl_detail({"公司名称": "德州市陵城区盈阳环保材料中心", "公司ID": "944956",
                      "公司网址": "", "详情URL": "", "列表电话": "", "所在区域": "", "主营产品": ""})
check("link.html 没号码 → 自动补取商铺首页（两跳兜底）", len(asked) == 2)
check("首页底部也只有假号时，靠邮箱判成功而不是假号", rec.get("状态") == "成功" and rec.get("邮箱"))
c._fetch = lambda url, tries=3, guarded=True, referer="": ""
r = c.crawl_detail({"公司名称": "取页失败的公司", "公司ID": "2", "所在区域": ""})
check("拿不到页面判「详情页导航失败」（瞬时状态、可重采）",
      r.get("状态") == "详情页导航失败" and any(m in r["状态"] for m in c.TRANSIENT_MARKS))

# ── 7. 过盾后抄 Cookie 必须连 UA 一起同步（cf_clearance 认 UA）──
orig = cdp_base.CdpSiteCrawler._harvest_browser_cookies
cdp_base.CdpSiteCrawler._harvest_browser_cookies = lambda self: 1
try:
    c3 = Maimaib2bCrawler()
    c3.incremental = False
    c3._eval = lambda js: "Mozilla/5.0 (Macintosh; Intel Mac OS X) Chrome/124.0.0.0"
    n = c3._harvest_browser_cookies()
    check("抄回 1 条 Cookie 后 HTTP 会话 UA 已对齐浏览器",
          n == 1 and c3._session.headers.get("User-Agent", "").endswith("Chrome/124.0.0.0"))
    c4 = Maimaib2bCrawler()
    c4.incremental = False
    c4._eval = lambda js: (_ for _ in ()).throw(RuntimeError("无标签"))
    cdp_base.CdpSiteCrawler._harvest_browser_cookies = lambda self: 0
    before = c4._session.headers.get("User-Agent")
    c4._harvest_browser_cookies()
    check("没抄到 Cookie 就不动会话 UA", c4._session.headers.get("User-Agent") == before)
finally:
    cdp_base.CdpSiteCrawler._harvest_browser_cookies = orig

# ── 9. 盾过了但通行证抄不回：降级走标签取页，绝不整轮空等 ──────
# （真实事故：旧实现探测只走裸 HTTP，而 cf_clearance 只在浏览器里、抄 Cookie 又依赖
#   探测成功，两边互相等 —— 看门狗空转满 BLOCK_MAX_WAIT 也没采到一家。）
c5 = Maimaib2bCrawler()
c5.incremental = False
c5._pace_before_request = lambda: None
c5._sleep = lambda *a: None

class _BlockedSession:
    """裸 HTTP 永远拿 CF 薄壳（403）的会话替身，并记下发了几次请求。"""
    def __init__(self, html): self.html, self.gets = html, []
    def get(self, url, timeout=25, headers=None):
        self.gets.append(url)
        r = _ok_r(url, self.html)          # 先造正常响应，再改状态码
        r.status_code = 403                # 旧写法在类体里引用外层函数的 url 参数，会直接报错
        r.headers = {"Content-Type": "text/html"}
        return r
    def close(self): pass

class _OkSession(_BlockedSession):
    """通行证已拿回会话：HTTP 直接 200 正文。"""
    def get(self, url, timeout=25, headers=None):
        self.gets.append(url)
        return _ok_r(url, self.html)


def _ok_r(url, html):
    """模拟“通行证已拿回会话”的正常 200 正文响应。"""
    class R:
        status_code = 200
        headers = {"Content-Type": "text/html; charset=utf-8"}
    r = R(); r.text, r.url = html, url
    return r

import cdp_base as _cb
orig_harvest = _cb.CdpSiteCrawler._harvest_browser_cookies
try:
    _cb.CdpSiteCrawler._harvest_browser_cookies = lambda self: 0   # 通行证就是抄不回
    body = cached("http_search_company_空调.html")

    # ① 既没通行证又没可用标签：如实报「仍在质询」，绝不谎报已解除
    c5._session = _BlockedSession(shell)
    c5.pin_tab_id = ""
    still = c5.probe_block()
    check(f"无通行证且无标签：如实报仍在质询（{still[:26]}）",
          bool(still) and "质询" in still and c5._browser_fallback is False)

    # ② 标签已能正常打开本站正文：转降级模式，探测返回「已解除」
    # 取页也换成假实现（真缓存页）：判据看的是「能不能把那一页取回来」，不是正文长度
    c5.pin_tab_id = "FAKE"
    c5._page_state = lambda: {"url": "https://www.maimaib2b.com/search/Company/%E7%A9%BA%E8%B0%83/",
                              "len": 42000}
    c5._html = lambda: body
    c5._cdp_navigate = lambda url, *a, **k: None   # 自测绝不真开浏览器
    c5._wait_ready = lambda *a, **k: None
    check("标签已能开正文：探测报已解除并转降级模式", c5.probe_block() == ""
          and c5._browser_fallback is True)
    c5._borrowed_tab = True
    c5._after_block_cleared()
    check("降级模式下收尾绝不把标签归还（后面还要靠它取页）",
          c5.pin_tab_id == "FAKE" and c5._borrowed_tab is True)

    # ③ 降级取页：走标签导航拿正文，不再白发 HTTP
    nav = []
    c5._cdp_navigate = lambda url, *a, **k: nav.append(url)
    c5._wait_ready = lambda *a, **k: None
    http_gets = len(c5._session.gets)
    got = c5._fetch("https://www.maimaib2b.com/search/Company/%E7%A9%BA%E8%B0%83/")
    check("降级取页真的走了标签且拿到 20 家", nav and len(c.parse_list(got, "空调")) == 20)
    check("降级取页不再白发 HTTP 请求", len(c5._session.gets) == http_gets)

    # ④ 隔 20 个请求真探一次 HTTP：还没通行证就继续降级，别切回快路又撞墙
    c5._fallback_pages = 19
    c5._fetch("https://www.maimaib2b.com/Shop/1/link.html")
    check("第 20 个请求才试 HTTP（前 19 个直接走标签）",
          c5._browser_fallback is True and len(c5._session.gets) == http_gets + 1)

    # ⑤ 通行证拿回会话：切回不占标签的快路并归还标签
    c5._fetch_via_tab = lambda url: ""          # 屏蔽标签链路，验证切回后走的是 HTTP
    released = []
    c5._release_verify_tab = lambda: released.append(1)
    c5._session = _OkSession(body)
    c5._borrowed_tab = True
    c5._fallback_pages = 19
    c5._fetch("https://www.maimaib2b.com/Shop/1/link.html")
    check("HTTP 恢复后切回快路（拿到正文且走的是会话而非标签）",
          c5._browser_fallback is False and "link.html" in c5._session.gets[-1])
    check("切回快路同时调了归还标签的收尾（不能一直占着）", bool(released))
finally:
    _cb.CdpSiteCrawler._harvest_browser_cookies = orig_harvest

# ── 8. 列表翻页必须走裸 HTTP（2026-11-02 实机事故：麦麦B2B 整轮 0 家、日志一句话没有）─
# 真实事故：本类当时没覆盖基类的 _iter_keyword，而基类那套是用浏览器标签取页（_goto +
# _html）实现的；本站平时不占标签（start_browser/ensure_tab 是空壳），于是列表一页都取不到，
# 又不经过 _fetch 就永远不会写「请求失败」也不会有防采提醒——界面表现为「关键词一直在涨、
# 数据一条不动、成功 0 家」（实机每个关键词空转 80 秒，5 个关键词一家不产出）。
c6 = Maimaib2bCrawler()
c6.incremental = False
c6._sleep = lambda *a, **k: None
c6._pace_before_request = lambda: None
c6._seen_members = set()
c6.stat_pages = c6.stat_listed = c6._early_skipped = 0
c6.results = []


def _mini(page, ids, total_pages=3):
    """造一页列表（卡片结构照真实缓存 http_search_company_空调.html）。"""
    cards = "".join(
        '<dl itemtype="http://data-vocabulary.org/Organization"><dd>'
        f'<a itemprop="name" title="公司{p}空调设备有限公司" href="/Shop/{p}/">x</a>'
        f'<span itemprop="tel">0755-8981093{p % 10}</span>'
        '<strong>主营产品：</strong>空调</dd></dl>' for p in ids)
    return cards + (f"<span>当前第{page}页，共{total_pages}页，共"
                    f"{total_pages * len(ids)}条记录。</span>")


asserted_goto = []
c6._goto = lambda url, *a, **k: asserted_goto.append(url) or (_ for _ in ()).throw(
    AssertionError("列表页绝不能走浏览器 _goto"))
asked6 = []
c6._fetch = lambda url, tries=3, guarded=True, referer="": (
    asked6.append(url) or _mini(1, range(111, 121)) if url.endswith("%E7%A9%BA%E8%B0%83/")
    else (asked6.append(url) or _mini(2, range(121, 131))) if "/p2/" in url
    else (asked6.append(url) or _mini(3, range(131, 141))))
stubs = list(c6._iter_keyword("空调"))
check(f"列表翻页走的是 HTTP（3 页共 {len(stubs)} 家）而绝不碰浏览器标签",
      len(stubs) == 30 and not asserted_goto and c6.stat_listed == 30)
check("翻页 URL 逐级递增且首页不带 p 段",
      asked6[0].endswith("/search/Company/%E7%A9%BA%E8%B0%83/")
      and "/p2/" in asked6[1] and "/p3/" in asked6[2])
check("按页脚「共3页」精确收尾，到底就不再发请求", len(asked6) == 3)

asked6.clear()
n2 = list(c6._iter_keyword("空调"))
check("同一批公司不重复入库（去重台账生效，连续两页无新增就枯竭退出）",
      n2 == [] and len(asked6) == 2)

asked6.clear()
c6._seen_members = set()
c6._fetch = lambda url, tries=3, guarded=True, referer="": (
    asked6.append(url) or (_mini(1, range(201, 211)) if len(asked6) == 1 else ""))
n3 = list(c6._iter_keyword("空调"))
check("第 2 页取不到页面（到底或被拦到底）→ 立即停止翻页、不无限重试",
      n3 and len(asked6) == 2)

c7 = Maimaib2bCrawler()
c7.incremental = False
c7._sleep = lambda *a, **k: None
c7._pace_before_request = lambda: None
c7._seen_members = set()
c7._session = _BlockedSession(shell)
c7._block_probe_url = ""
bCalled = []
c7.handle_http_block = lambda url: (bCalled.append(url) or False)
c7._release_verify_tab = lambda: None
c7.open_verify_page = lambda url="": False      # 自测绝不真借标签（那会开真窗口）
c7._page_state = lambda: {"url": "", "len": 0}
c7._cdp_navigate = lambda *a, **k: None         # 降级取页也不真开浏览器
c7._wait_ready = lambda *a, **k: None
c7._html = lambda: shell                        # 降级取回来的还是 CF 薄壳＝还没放行
list(c7._iter_keyword("空调"))
check(f"列表页撞 CF 薄壳会走「借标签过盾」这条路（{bCalled[:1]}）",
      bool(bCalled) and "/search/Company/" in bCalled[0])
check("过盾不成时如实停住，绝不把 0 家当「这个词没公司」记过去",
      c7._block_reason != "" and c7.stat_listed == 0)

# ── 9b. 质询页卡住不动 → 看门狗自己推一把（2026-11-02 实测停在「请稍候…」转圈页）──
print("9b. 卡住的验证页自动推进")


class _CmdLog(list):
    """命令名序列（仍是普通 list，旧的 ==/切片/count 断言照用），额外记下每条命令的参数。"""

    def __init__(self):
        super().__init__()
        self.params = {}

    def add(self, method, p):
        self.append(method)
        self.params.setdefault(method, []).append(p)

    def arg(self, method, index=0):
        got = self.params.get(method) or []
        return got[index] if index < len(got) else {}


# 标签里常见的本站 Cookie：两张 Cloudflare 的（通行证＋挑战计数）加一张站方的会话 Cookie
_DEFAULT_CHL_COOKIES = [
    {"name": "cf_clearance", "domain": ".maimaib2b.com", "path": "/", "value": "CLEARANCE"},
    {"name": "cf_chl_rc_ni", "domain": ".maimaib2b.com", "path": "/", "value": "11"},
    {"name": "PHPSESSID", "domain": ".maimaib2b.com", "path": "/", "value": "keepme"},
]


def _stub_tab(cw, url="https://www.maimaib2b.com/search/Company/%E5%88%B6%E5%86%B7/",
              title="请稍候…", bodylen=0, captcha="", refresh="", frame_refresh="",
              widget="", cookies=None, human=""):
    """把标签那一侧全部换成假实现：只记录点了什么/导航去哪，绝不碰真浏览器。
    refresh＝主文档上那个「刷新此页面」链接的文字；frame_refresh＝它在跨域 iframe 里；
    widget＝页面上验证勾选框的屏幕位置（JSON 串，空串＝没有）；cookies＝标签里现有的本站 Cookie；
    human＝页面上摆着要人亲手拖的滑块（非 Cloudflare 控件），空串＝没有。"""
    nav, shown, logs, conns, cmds = [], [], [], [], _CmdLog()

    def _ev(script, timeout=20):
        # 控件探针现在有两份：HUMAN_CAPTCHA_JS（要人亲手拖的滑块，只含腾讯/极验这些
        # 选择器）与 CAPTCHA_WIDGET_JS/_turnstile_widget（含 Cloudflare 自家容器）。
        # 桩按「这份 JS 里出现了哪种选择器」分派，human 只在真摆了滑块时给值——
        # 否则用例根本分不清自己是在测「禁区控件」还是「可代点的勾选框」。
        if "tencent-captcha" in script and human:
            return human
        if "cf-turnstile" in script or "challenge-stage" in script:
            return widget
        return refresh if "challenge-refresh" in script else captcha

    cw.pin_tab_id = "TAB-FAKE"
    cw._block_probe_url = url
    cw._block_reason = "站点返 403 且是人机验证插页"
    cw._eval = _ev
    cw._current_url = lambda: url
    cw._title = lambda: title
    cw._page_state = lambda: {"rs": "complete", "url": url, "len": bodylen}
    cw._cdp_navigate = lambda u: nav.append(u)
    cw._wait_ready = lambda dom=None, timeout=22: True
    cw._show_tab_window = lambda: shown.append(True)
    cw._log = lambda m: logs.append(m)
    cw._after_block_cleared = lambda: None

    def _resp(method, params):
        if method == "Network.getCookies":
            return {"cookies": (cookies if cookies is not None
                                else _DEFAULT_CHL_COOKIES)}
        if method == "Page.getFrameTree":
            kid = {"frame": {"id": "CH0", "url": "https://challenges.cloudflare.com/ch"}}
            return {"frameTree": {"frame": {"id": "MAIN", "url": url},
                                  "childFrames": ([kid] if frame_refresh else [])}}
        if method == "Page.createIsolatedWorld":
            return {"executionContextId": 7}
        if method == "Runtime.evaluate":
            # 带 contextId 的就是送进跨域帧的那次；主文档那次交给上面的 _ev 桩
            return {"result": {"value": frame_refresh if params.get("contextId") else ""}}
        return {}

    class _FakeWs:
        """假 CDP 连接：记录这条连接上按顺序发了哪些命令（用来验「一条连接做完」）。"""

        def __init__(self):
            self._pending = []

        def send(self, payload):
            m = json.loads(payload)
            cmds.add(m["method"], m.get("params") or {})
            self._pending.append(json.dumps({"id": m["id"],
                                             "result": _resp(m["method"], m.get("params") or {})}))

        def recv(self):
            return self._pending.pop(0) if self._pending else "{}"

        def close(self):
            pass

    cw._cdp_open = lambda timeout=15: (conns.append(True) or _FakeWs())
    return nav, shown, logs, conns, cmds


c8 = Maimaib2bCrawler()
check("本站开了「卡住自动推进」且探得密（10 秒量级）",
      c8.BLOCK_AUTO_REFRESH is True and c8.BLOCK_POLL <= 15)
nav8, shown8, logs8, conns8, cmds8 = _stub_tab(c8)
check("识别为纯自动挑战页（标题「请稍候…」）", c8._auto_challenge_page() is True)
check("跨域帧里的元素也是在同一条连接上取的（先 Page.enable 再查帧树）",
      c8._frames_eval("1") == [] and conns8 == [True]
      and cmds8[:2] == ["Page.enable", "Runtime.enable"]
      and "Page.getFrameTree" in cmds8)
check("刚卡住时不整页重载（实测 8 秒一刷连着 4 次反而一直停在「Just a moment…」）",
      c8._refresh_stalled_challenge(10) is False and nav8 == [])
check("不重载也把窗口带回前台，让挑战继续跑", shown8 == [True])
check("卡到保底时间才整页重载一次（兜底，防对着死页干等）",
      c8._refresh_stalled_challenge(c8.BLOCK_RELOAD_LAST_RESORT) is True and len(nav8) == 1)
check("保底重载一轮只做一次，不反复重开挑战",
      c8._refresh_stalled_challenge(600) is False and len(nav8) == 1)

_c8f = Maimaib2bCrawler()
_cf, _sf, _lf, _cnf, _cmf = _stub_tab(_c8f, frame_refresh="刷新此页面")
check("帧里的刷新入口点得到时，跨域 iframe 也只用一条连接（取帧树和执行不能分两条）",
      _c8f._click_in_frames(_c8f.CHALLENGE_REFRESH_JS) == "刷新此页面"
      and _cnf == [True] and _cmf.count("Page.createIsolatedWorld") == 1
      and _cmf.count("Runtime.evaluate") == 2)

c8r = Maimaib2bCrawler()
nr, sr, lr, _cr, _mr = _stub_tab(c8r, refresh="刷新此页面")
check("优先点插页上那个「刷新此页面」（拿现有令牌再试一次，不重新开一场挑战）",
      c8r._refresh_stalled_challenge(10) is True and nr == []
      and any("已点页面上的" in m and "刷新此页面" in m for m in lr))
check("点得动就不必整页重载（推进次数上限也跟着放宽到够撑满等待窗口）",
      c8r.BLOCK_REFRESH_MAX * c8r.BLOCK_REFRESH_EVERY >= c8r.BLOCK_MAX_WAIT
      and c8r.BLOCK_REFRESH_MAX * c8r.BLOCK_REFRESH_EVERY >= 300)

c8f = Maimaib2bCrawler()
nf, sf, lf, _cf2, _cm2 = _stub_tab(c8f, frame_refresh="刷新此页面")
check("刷新入口在跨域 iframe 里也点得到（CDP 逐帧开执行上下文）",
      c8f._refresh_stalled_challenge(10) is True and nf == []
      and any("已点页面上的" in m for m in lf))

_c8u, _c8t, _c8p = c8._current_url, c8._title, c8._page_state
c8._title = lambda: "制冷-制冷批发、货源、厂家-麦麦B2B"
c8._page_state = lambda: {"rs": "complete", "url": nav8[0], "len": 30000}
check("正常页不算卡住的验证页，绝不乱刷新", c8._auto_challenge_page() is False)
c8._title, c8._current_url, c8._page_state = _c8t, _c8u, _c8p

c9 = Maimaib2bCrawler()
# 滑块/勾选控件必须走 widget= 这个口子：CAPTCHA_WIDGET_JS 探针查的就是它。
# 早先写在 captcha= 上，桩里那条探针拿到空串，等于把「页面上摆着要人拖的滑块」
# 这个前提悄悄改没了——断言看着在测「绝不刷新」，实际测的是「没有控件的纯挑战页」。
nav9, _s9, _l9, _c9c, _c9m = _stub_tab(c9, human=".tcaptcha-body")
check("带滑块/验证码控件的页面绝不刷新（会把用户正在拖的滑块重置掉）",
      c9._auto_challenge_page() is False and c9._refresh_stalled_challenge(120) is False
      and nav9 == [])

c10 = Maimaib2bCrawler()
_n10, _s10, _l10, _c10c, _c10m = _stub_tab(c10)
c10.pin_tab_id = ""                      # 没借到标签：没东西可推进
check("手里没标签时不推进（也不会报错）", c10._refresh_stalled_challenge(120) is False)

# 「盾过了却判成没过」：2026-11-02 实测标签已开到真列表页（outerHTML 41561、20 家），
# 但 innerText 只有 1562 字符（卡片文字大多写在 title 属性里）——旧判据拿长度当门槛，
# 于是工具对着已过盾的标签干等到超时，这就是“一直采不到数据”的直接原因。
c13 = Maimaib2bCrawler()
c13.incremental = False
nav13, _s13, _l13, _c13c, _c13m = _stub_tab(c13, title="【制冷】黄页|公司名录 - MaiMaiB2B网", bodylen=1562)
c13._html = lambda: body                 # 真实缓存页（41K+ 字符、20 家卡片）
check("正文短但能解析出公司条目 → 判为盾已过（旧判据在这干等死）", c13._tab_page_ok() is True)
c13._html = lambda: shell                # CF 薄壳 5.8KB
check("插页薄壳仍判没过（不能拿壳当正文）", c13._tab_page_ok() is False)

# 拿「当前页」当凭据会把「首页已放行」当成「搜索页已放行」（独立浏览器实测误判过）：
# 给了 url 就要按那一页真取一次；但同一页绝不重复导航（导航＝重开一场挑战）
_c13_nav = []
_c13_cur = {"u": "https://www.maimaib2b.com/"}      # 标签当下停在首页（已放行）
_c13_page = {"html": body}                          # 首页看着就像真页
_c13_state = lambda: _c13_page["html"]
_c13_still_blocked = {"yes": True}         # 搜索页当下还拿不拿得到真内容
TARGET13 = "https://www.maimaib2b.com/search/Company/%E5%88%B6%E5%86%B7/"


def _c13_goto(u, *a, **k):
    _c13_nav.append(u)
    _c13_cur["u"] = u
    _c13_page["html"] = shell if _c13_still_blocked["yes"] else body


c13._html = _c13_state
c13._cdp_navigate = _c13_goto
c13._current_url = lambda: _c13_cur["u"]
check("首页已放行不算数：按目标页真取一次，只拿到薄壳仍判没过",
      c13._tab_page_ok(TARGET13) is False and len(_c13_nav) == 1)
check("标签已经停在这一页上就绝不重复导航（实测每导航一次＝向站点重开一场挑战，"
      "连着几次就永远停在「Just a moment…」，248 秒都过不去）",
      c13._tab_page_ok(TARGET13) is False and len(_c13_nav) == 1)
_c13_cur["u"] = "https://www.maimaib2b.com/?__cf_chl_rt_tk=ABC-1793636192"
_real_sleep13 = time.sleep
time.sleep = lambda s: None                 # 这一段只验「不再导航」，不等真等 45 秒
try:
    c13._fetch_via_tab(TARGET13)
finally:
    time.sleep = _real_sleep13
check("停在 CF 拿着这页请求跳转的中间态（?__cf_chl_rt_tk=…）也只等不刷、不再导航",
      len(_c13_nav) == 1)
_c13_still_blocked["yes"] = False          # 搜索页也真放行了
_c13_page["html"] = body
_c13_cur["u"] = TARGET13                    # 插页跑完会落回干净的地址（不再带令牌）
check("插页自己跑完、目标页真取得到才算过", c13._tab_page_ok(TARGET13) is True)
_c13_nav.clear()
_c13_cur["u"] = "https://www.maimaib2b.com/"
c13._fetch_via_tab(c13.build_search_url("空调", 1))
check("要的确实是另一页时才导航过去", len(_c13_nav) == 1 and "/p/" not in _c13_nav[0])

# 看门狗主循环：等了一会儿仍是插页，到点就得自己推一把，不能干等
real_sleep = cdp_base.time.sleep
cdp_base.time.sleep = lambda s: None
try:
    c11 = Maimaib2bCrawler()
    nav11, _s11, logs11, _c11c, _c11m = _stub_tab(c11)
    pushes = []
    _real_click = c11._click_in_frames
    c11._click_in_frames = lambda js: (pushes.append(True) or _real_click(js))
    c11.BLOCK_POLL = 1
    c11.BLOCK_REFRESH_EVERY = 6
    c11.BLOCK_REFRESH_MAX = 8
    c11.BLOCK_RELOAD_LAST_RESORT = 12
    c11._max_blocks = 99
    c11.probe_block = lambda: "仍在质询"
    c11._wait_manual_unblock("站点返 403 且是人机验证插页", c11.probe_block, max_wait=20)
    check(f"等待循环到点会自动推进验证页（本轮推了 {len(pushes)} 次）", len(pushes) >= 2)
    check("推进有上限，不会对着死页无限刷", len(pushes) <= c11.BLOCK_REFRESH_MAX)
    check("插页上没可点入口时，只在保底时间重载那一次（不再 8 秒一次重开挑战）",
          len(nav11) == 1)
    c12 = Maimaib2bCrawler()
    nav12, _s12, _l12, _c12c, _c12m = _stub_tab(c12)
    c12.BLOCK_POLL = 1
    c12.BLOCK_REFRESH_EVERY = 6
    c12._block_kind = "ip"                # IP 限流那一类：页面里本就没东西可点
    c12.probe_block = lambda: "仍在限流"
    c12._wait_manual_unblock("HTTP 429 IP 限流", c12.probe_block, max_wait=15)
    check("IP 级限流绝不推进验证页（按设计拉长间隔干等冷却）", nav12 == [])
finally:
    cdp_base.time.sleep = real_sleep

# ── 9c. HTTP 到底拿不到页、浏览器已放行 → 必须拿标签把这一页取回来 ──
# （2026-11-02 实机：探活报「已解除」收了等待循环、标签也归还了，紧接着正式请求又 403，
#   旧写法直接回空串——「制冷」第 1 页 20 家就这么静默丢了，本轮 0 家。）
print("9c. HTTP 不通时借标签兜住这一页")
c14 = Maimaib2bCrawler()
c14.incremental = False
c14._sleep = lambda *a, **k: None
c14._pace_before_request = lambda: None
c14._mark_activity = lambda: None
c14._session = _BlockedSession(shell)          # 裸 HTTP 永远 403 薄壳
c14._block_probe_url = ""
c14.handle_http_block = lambda url: False       # 看门狗也没能恢复（等超时/熔断）
c14.pin_tab_id = ""
c14._page_state = lambda: {"url": "https://www.maimaib2b.com/search/Company/%E7%A9%BA%E8%B0%83/",
                           "len": 1562}        # 实测真页的 innerText 就是这么短
c14._html = lambda: body
c14._cdp_navigate = lambda *a, **k: None
c14._wait_ready = lambda *a, **k: None
logs14 = []
c14._log = lambda m: logs14.append(m)


def _borrow(url=""):
    c14.pin_tab_id = "BORROWED"
    return True


c14.open_verify_page = _borrow
c14._eval = lambda script, timeout=20: ""      # 绝不碰真 9255（_tab_real_page 会先看一眼当前页）
got14 = c14._fetch(c14.build_search_url("空调", 1))
check(f"HTTP 403 但浏览器已放行 → 借标签把这 {len(c.parse_list(got14 or '', '空调'))} 家取回来",
      len(c.parse_list(got14 or "", "空调")) == 20 and c14._browser_fallback is True)
check("没标签时会先借一个（而不是直接放弃这一页）", c14.pin_tab_id == "BORROWED")
check("降级取页会如实说明，不静默少一页", any("改用标签取这一页" in m for m in logs14))

# 借标签会真开一个窗口，绝不能每页都试一次：拿不到页后只试一次，直到 HTTP 又拿到一页
logs15 = []
c14._session = _BlockedSession(shell)
c14._browser_fallback = False
c14._fallback_attempted = False
c14.pin_tab_id = ""
borrowed = []
c14.open_verify_page = lambda url="": borrowed.append(url) or False
c14._page_state = lambda: {"url": "https://www.maimaib2b.com/search/Company/x/", "len": 0}
list(c14._iter_keyword("空调"))
list(c14._iter_keyword("制冷"))
check(f"拿不到页时只借一次标签试降级（本轮借了 {len(borrowed)} 次，不是每页都借）",
      len(borrowed) <= 1)

# ── 9d. 质询插页自己也很「大」（2026-11-03 独立浏览器实测 44675 字符）────
# 旧降级取页只按 outerHTML 长度判（>=15000），于是把「请稍候…」插页当正文取回来：
# 解析出 0 家、不报错、还把会话切成降级模式——「其他站都正常、只麦麦一直 0 家」就是这条。
print("9d. 大号质询插页不能当正文")
import maimaib2b_crawler as mm_mod              # noqa: E402
_real_sleep = mm_mod.time.sleep
mm_mod.time.sleep = lambda s: None
big_shell = "<html><body>" + "x" * 44675 + "</body></html>"      # 实测插页体量
try:
    c16 = Maimaib2bCrawler()
    c16.incremental = False
    _n16, _s16, _l16, _c16c, _c16m = _stub_tab(c16)
    c16._title = lambda: "【空调】黄页|公司名录 - MaiMaiB2B网"      # 插页也会顶个正常标题
    c16._page_state = lambda: {"rs": "complete",
                               "url": "https://www.maimaib2b.com/search/Company/x/", "len": 119}
    check("44675 字符的「请稍候…」插页判为真页=False（旧长度判据在这上判成 True）",
          len(big_shell) > c16.MIN_PAGE_CHARS and c16._tab_real_page(big_shell) is False)
    c16._page_state = lambda: {"rs": "complete",
                               "url": "https://www.maimaib2b.com/search/Company/x/", "len": 2151}
    check("真列表页（能解析出 20 家）判为真页=True", c16._tab_real_page(body) is True)
    no_cards = "<html>" + "导航文字" * 6000 + "</html>"          # 真页但这个词没结果
    check("真页却一条公司都没有（词没结果）→ 仍算取到页，不判成被拦",
          c16._tab_real_page(no_cards) is True)

    # 插页 → 推一把 → 放行 → 再取：这一页不能丢
    pages = [(big_shell, "请稍候…"), (big_shell, "请稍候…"),
             (body, "【空调】黄页|公司名录 - MaiMaiB2B网")]
    state16 = {"len": 119}

    def _html16():
        p = pages.pop(0) if pages else (body, "【空调】黄页|公司名录 - MaiMaiB2B网")
        c16._title = lambda: p[1]
        state16["len"] = 119 if pages else 2151
        return p[0]

    def _st16():
        return {"rs": "complete",
                "url": "https://www.maimaib2b.com/search/Company/x/", "len": state16["len"]}

    c16._html = _html16
    c16._page_state = _st16
    got16 = c16._fetch_via_tab(c16.build_search_url("空调", 1))
    check(f"撞上插页时等它放行再取，这一页 {len(c16.parse_list(got16 or '', '空调'))} 家拿回来了",
          len(c16.parse_list(got16 or "", "空调")) == 20)
    check("等插页期间把窗口带回前台并按帧里/标题找刷新入口（不只干睡）",
          len(_s16) >= 1 and len(_c16c) >= 1)

    # 不是插页、又确实拿不到东西：立即如实返回空，不对着死页等 45 秒
    c17 = Maimaib2bCrawler()
    c17.incremental = False
    _n17, _s17, _l17, _c17c, _c17m = _stub_tab(c17, title="【不存在的词】黄页", bodylen=900)
    c17._page_state = lambda: {"rs": "complete", "url": "chrome-error://chromewebdata/", "len": 900}
    c17._current_url = lambda: "chrome-error://chromewebdata/"
    c17._html = lambda: ""
    t17 = time.time()
    check("导航失败/不在本站＝不是插页 → 立即返空不干等",
          c17._fetch_via_tab(c17.build_search_url("空调", 1)) == ""
          and time.time() - t17 < 5)
finally:
    mm_mod.time.sleep = _real_sleep

# ── 9e. 自审补的两道紧箍咒（2026-11-03）────────────────────────
# ① 判断「这一页是不是真页」时绝不能顺手改掉拦截分诊状态（旧写法调了 http_block_reason，
#    会把 _block_kind / _block_probe_url 写脏：看门狗下一步就会拿 IP 冷却的方式去等一个人过就能清的盾）；
# ② 借标签取页时的保底重载必须回「我要的那一页」，不能回首页或旧的探测页（不然会把别的页
#    当成这一页收走，采到串页的假数据）。
print("9e. 取页判断不改分诊状态、保底重载回目标页")
c18 = Maimaib2bCrawler()
c18.incremental = False
_n18, _s18, _l18, _c18c, _c18m = _stub_tab(c18, title="正在验证您是真人", bodylen=119)
c18._block_kind = ""
c18._block_probe_url = ""
check("小插页（带防采特征词）仍判为假页", c18._tab_real_page(shell) is False)
check("但这次判断绝不改写拦截分诊状态（_block_kind / _block_probe_url 得原封不动）",
      c18._block_kind == "" and c18._block_probe_url == "")

WANT18 = c18.build_search_url("光伏", 2)
c18._block_probe_url = "https://www.maimaib2b.com/Shop/999999/link.html"   # 上一次被拦的旧页
c18.BLOCK_RELOAD_LAST_RESORT = 0      # 直接跳到保底重载这一拍
c18.BLOCK_POLL = 1
c18.TAB_WAIT_UNBLOCK = 3
c18._html = lambda: shell             # 插页一直不放行
_real_sleep18 = mm_mod.time.sleep
mm_mod.time.sleep = lambda s: None
try:
    c18._fetch_via_tab(WANT18)
finally:
    mm_mod.time.sleep = _real_sleep18
check(f"保底重载只回要取的那一页（导航记录：{[u.split('?')[0][-28:] for u in _n18]}）",
      _n18 and _n18[-1] == WANT18 and not any("Shop/999999" in u for u in _n18))

# ── 9f. Turnstile「请验证您是真人」勾选框：只点不刷，卡到底才清通行证重开 ──
# 2026-11-03 用户实测「人工点了验证还是停在这一页、刷新也回到这一页」的两条真因：
#   ① 控件是 300×65，旧的控件尺寸门槛（宽≥220 且高≥90）根本看不到它 → 被判成「纯自动页」
#      → 到点就整页重载，把用户刚点的进度清零（日志 11:19:32 “保底整页重新加载”）；
#   ② 会话里 cf_chl_rc_ni 已经数到 11（反复挑战 11 次），拿同一张废通行证再刷只会一直循环。
print("9f. 真人勾选框只点不刷、保底清通行证再重开")

WIDGET19 = json.dumps({"x": 500.0, "y": 380.0, "w": 300.0, "h": 65.0})
c19 = Maimaib2bCrawler()
n19, s19, l19, cn19, cm19 = _stub_tab(c19, title="正在验证您是真人", widget=WIDGET19)
check("300×65 的勾选框认得出来（旧门槛 220×90 会漏：高只有 65）",
      c19._turnstile_widget() == {"x": 500.0, "y": 380.0, "w": 300.0, "h": 65.0})
check("摆着勾选框时也算插页（不能因为它不是「纯转圈」就当正常页放过去）",
      c19._auto_challenge_page() is True)
check("有勾选框 → 按坐标替用户点一下，绝不刷新、绝不整页重载、也不清通行证",
      c19._refresh_stalled_challenge(120) is True and n19 == [] and cn19 == [True]
      and cm19.count("Input.dispatchMouseEvent") == 3
      and "Network.deleteCookies" not in cm19)
_m19 = cm19.arg("Input.dispatchMouseEvent", 1)
check("鼠标事件按控件几何坐标发（左端 30 像素、竖向居中），按下/抬起走同一条连接",
      abs(float(_m19.get("x") or 0) - 530.0) < 1 and abs(float(_m19.get("y") or 0) - 412.5) < 1
      and _m19.get("button") == "left" and set(cm19) == {"Input.dispatchMouseEvent"})
check("刚点过就不连着点（CF 自己要用 5~20 秒跑完，这期间再点等于重开一轮）",
      c19._refresh_stalled_challenge(130) is False and cn19 == [True] and n19 == [])
_c19u, _c19p = c19._current_url, c19._page_state
c19._page_state = lambda: {"rs": "complete", "url": "https://www.maimaib2b.com/search/Company/", "len": 30000}
c19._title = lambda: "【光伏】黄页|公司名录 - MaiMaiB2B网"
check("真页面上哪怕摆着表单验证码也绝不去点（先认定是插页才动手）",
      c19._refresh_stalled_challenge(120) is False and cn19 == [True])
c19._current_url, c19._page_state = _c19u, _c19p

c20 = Maimaib2bCrawler()
n20, s20, l20, cn20, cm20 = _stub_tab(c20, title="请稍候…")      # 纯转圈页：没勾选框、没刷新入口
WANT20 = c20.build_search_url("光伏", 3)
check("保底时间从 60 秒放宽到 90 秒（放着不动实测 10~50 秒就会放行）",
      c20.BLOCK_RELOAD_LAST_RESORT >= 90)
check("没到保底时间什么都不做（只把窗口带回前台），不重载也不清通行证",
      c20._refresh_stalled_challenge(60, target=WANT20) is False and n20 == []
      and "Network.deleteCookies" not in cm20 and s20 == [True])
check("卡到保底时间 → 先清掉本站 Cloudflare 通行证再重开要的那一页",
      c20._refresh_stalled_challenge(95, target=WANT20) is True
      and n20[-1] == WANT20 and "Network.getCookies" in cm20
      and any("清掉本站通行证" in m for m in l20))
_d20 = cm20.params.get("Network.deleteCookies", [])
check("只删本站那几张 Cloudflare Cookie（cf_clearance/cf_chl_*），站方会话 Cookie 不动",
      sorted(d.get("name") or "" for d in _d20) == ["cf_chl_rc_ni", "cf_clearance"])
check("保底清通行证＋重开一轮只做一次（再卡也不反复重开挑战）",
      c20._refresh_stalled_challenge(200, target=WANT20) is False and len(n20) == 1)

# ── 9g. 本机裸 HTTP 通路已死：别再拿注定 403 的请求给站方风控计数加码 ──
# 2026-11-03 实测：浏览器 4 页连开全秒过（每页 40 张卡片），同一分钟把刚过验证的
# cf_clearance 抄给 Python 裸请求连打 12 发＝12 个 403（TLS/HTTP2 指纹对不上）。
print("9g. 通行证抄进会话仍被拦 → 本轮彻底改走标签")


class _Always403:
    """拿着浏览器通行证仍被拦的本机会话替身（实测 12/12 全 403）。"""

    def __init__(self):
        self.gets = []

    def get(self, url, timeout=25, headers=None):
        self.gets.append(url)
        r = _ok_r(url, shell)
        r.status_code = 403
        r.headers = {"Content-Type": "text/html"}
        return r

    def close(self):
        pass


c21 = Maimaib2bCrawler()
c21.incremental = False
c21._session = _Always403()
c21.pin_tab_id = "TAB-FAKE"
c21._harvest_browser_cookies = lambda: 3          # 通行证每次都抄得回（3 条）
c21._tab_page_ok = lambda url="": True            # 标签这边一直好用
l21 = []
c21._log = lambda m: l21.append(m)
c21.probe_block()
check("第一次「抄到了还是 403」：先记一笔，还没判死",
      c21._http_probe_fails == 1 and c21._http_dead is False and len(c21._session.gets) == 2)
c21.probe_block()
check("连续 2 次抄回通行证仍被拦 → 判本机裸 HTTP 通路是死的（日志如实说明）",
      c21._http_dead is True and any("本机这条路是死的" in m for m in l21))
_gets21 = len(c21._session.gets)
c21.probe_block()
check("判死后探测一步都不再发裸 HTTP（每发 403 都在给站方挑战计数加码）",
      len(c21._session.gets) == _gets21 and c21._browser_fallback is True)
navs21 = []
c21._fetch_via_tab = lambda u: (navs21.append(u) or "<html>ok</html>")
c21._fallback_pages = 19                          # 下一拍正好是「隔 20 个真探一次 HTTP」
check("降级取页到了该真探 HTTP 那一拍也不探，直接走标签",
      c21._fetch("https://www.maimaib2b.com/search/Company/%E5%85%89%E4%BC%8F/") == "<html>ok</html>"
      and len(c21._session.gets) == _gets21 and len(navs21) == 1)
c21.reset_run_state()
check("下一轮重新给裸 HTTP 一次机会（判死只在本轮有效）",
      c21._http_dead is False and c21._http_probe_fails == 0)

# ── 10. 注册到并行编排 ────────────────────────────────────────
check("SITE_ORDER 含 maimaib2b", "maimaib2b" in SITE_ORDER)
check("SITE_CLASSES/SITE_NAMES 均注册", SITE_CLASSES["maimaib2b"] is Maimaib2bCrawler
      and SITE_NAMES["maimaib2b"] == "麦麦B2B")
sess = ParallelSession()
check("ParallelSession 实例化出 maimaib2b 采集器", "maimaib2b" in sess.crawlers)
check("种子库按 slug 隔离", c._seen_store_path.name == "seen_store_maimaib2b.json")

print(f"\nALL PASS ({ok} checks)")
