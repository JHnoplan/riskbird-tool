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
import sys, pathlib, re, json
sys.path.insert(0, "/Users/jhnoplan/Desktop/PythonDemo/b2bcollect_build")

import cdp_base                                                   # noqa: E402
from maimaib2b_crawler import Maimaib2bCrawler                    # noqa: E402
from parallel_session import ParallelSession, SITE_ORDER, SITE_CLASSES, SITE_NAMES  # noqa: E402

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
        class R:
            status_code, text, headers, url = 403, "", {}, url
            def __init__(s, t): s.text = t
        r = R(self.html); r.status_code = 403; r.headers = {"Content-Type": "text/html"}
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
    c5.pin_tab_id = "FAKE"
    c5._page_state = lambda: {"url": "https://www.maimaib2b.com/search/Company/%E7%A9%BA%E8%B0%83/",
                              "len": 42000}
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
    c5._html = lambda: body
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

# ── 10. 注册到并行编排 ────────────────────────────────────────
check("SITE_ORDER 含 maimaib2b", "maimaib2b" in SITE_ORDER)
check("SITE_CLASSES/SITE_NAMES 均注册", SITE_CLASSES["maimaib2b"] is Maimaib2bCrawler
      and SITE_NAMES["maimaib2b"] == "麦麦B2B")
sess = ParallelSession()
check("ParallelSession 实例化出 maimaib2b 采集器", "maimaib2b" in sess.crawlers)
check("种子库按 slug 隔离", c._seen_store_path.name == "seen_store_maimaib2b.json")

print(f"\nALL PASS ({ok} checks)")
