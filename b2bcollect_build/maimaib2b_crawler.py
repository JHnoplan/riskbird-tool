"""maimaib2b_crawler.py —— 麦麦B2B(www.maimaib2b.com) 关键词搜索采集器。

2026-11-02 实机探测校准（沿用 11467/八方的「先裸 HTTP、被拦才借标签」路线）：
  ⚠ 站点挂在 Cloudflare 后面：无 clearance Cookie 的裸 HTTP 一律 403 + 5.8KB
     「Just a moment...」薄壳（正文页 24~47KB，体积差一个量级，按体积判最稳）。
     但**共享 Chrome 打开同一页约 12 秒自行放行**，多数时候连人工都不用点；
     放行后用 CDP Storage.getCookies 把 `cf_clearance` 抄给 requests 会话，
     实测 6 家 /Shop/<id>/link.html 全 200、24~26KB、0.6~1.1s/家——所以采集
     平时完全不占标签，只在 Cookie 过期/被重新质询时借一个标签过盾，抄回 Cookie
     继续跑（cf_clearance 同时绑 UA，抄 Cookie 必须连 UA 一起同步，否则照样被拒）。
  ✅ 列表：GET `/search/Company/<关键词>/`、翻页 `/search/Company/<关键词>/p<N>/`，
     20 家一页；条目是 `<dl itemtype='...Organization' itemscope>`，
     `itemprop='name'` 给公司名（名字里会插 `<font><b>` 高亮关键词，取 title 属性最干净）、
     `itemprop='tel'` 直接给号码；页头 `共<em>N</em>条…相关公司`、页脚
     `当前第X页，共Y页，共Z条记录`，按「共Y页」精确收尾（实测 空调 53 页/1042 家）。
     本站翻页**没有 6 页那种硬上限**，翻到底为止。
  ✅ 详情：`/Shop/<公司id>/link.html` 免登录明文，左侧「联系方式」块给 联系人/座机/手机/微信/QQ，
     右侧「公司地址」块给 联系人/手机号码/固定电话/公司地址/电子邮箱/邮政编码。
     随机抽 40 家：公司名称 100%、联系人 98%、手机 78%、固话 55%、任一电话 90%、
     登记表要求的三项（名称+联系人+电话）齐全 90%、占位假号 0。
     注意商家常把固话填进「手机」栏（实测 86-0755-89810939 同时写在两栏），
     一律按号码形态重新分列，别照着标签名照抄。
  ℹ 店铺 id 来自全站点 sitemap（47 个子图去重得 549,770 家），关键词通道是其中的检索入口。
"""
import re
import time
import html as _htmlmod
from urllib.parse import quote, urlsplit

import requests

from cdp_base import (CdpSiteCrawler, PHONE_MOBILE_RE, PHONE_TEL_RE, EMAIL_RE, clean_address,
                      parse_contact_field, is_placeholder_number, dept_only, compose_position,
                      strip_name_marketing_tail, strip_country_code, HONORIFIC_ONLY_RE)

HOME_URL = "https://www.maimaib2b.com/"
SEARCH_URL = "https://www.maimaib2b.com/search/Company/{kw}/"
SEARCH_PAGE_URL = "https://www.maimaib2b.com/search/Company/{kw}/p{page}/"
LINK_URL = "https://www.maimaib2b.com/Shop/{sid}/link.html"

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# 列表条目：一个 <dl itemtype=...Organization> 块就是一家公司
_ROW_RE = re.compile(r"<dl[^>]*data-vocabulary\.org/Organization(.*?)</dl>", re.S)
_NAME_A = re.compile(r"itemprop=['\"]?name['\"]?[^>]*>")
_A_TITLE = re.compile(r"<a[^>]*title=['\"]([^'\"]{4,60})['\"][^>]*href=['\"]/?Shop/(\d+)/?['\"]")
_SHOP_HREF = re.compile(r"href=['\"]/?Shop/(\d+)/?['\"]")
_TEL_SPAN = re.compile(r"itemprop=['\"]?tel['\"]?[^>]*>(.*?)</span>", re.S)
_PROD_DD = re.compile(r"<strong>\s*主营产品\s*[:：]\s*</strong>(.*?)</dd>", re.S)
_TOTAL_RE = re.compile(r"共<em>\s*([\d,]+)\s*</em>")
_PAGEINFO_RE = re.compile(r"当前第\s*(\d+)\s*页[，,]\s*共\s*(\d+)\s*页")
# 详情：左侧「联系方式」与右侧「公司地址」两块都是 <label>标签：</label> + 值。
# 标签里夹着一串 &nbsp;（实测「座&nbsp;&nbsp;...&nbsp;机：」），所以宽度放到 80 再按归一后的长度筛
_FIELD_RE = re.compile(r"<label>(.{2,80}?)</label>(.*?)</li>", re.S)
_H1_RE = re.compile(r"<h1>\s*([^<]{4,60}?)\s*</h1>")
_SMALL_RE = re.compile(r'<p class="small"[^>]*>([^<]{2,60})</p>')
_TAG_RE = re.compile(r"<[^>]+>")
_BREAD_RE = re.compile(r'id="divsitemap"[^>]*>(.*?)</div>', re.S)
_QQ_RE = re.compile(r"tencent://message/\?uin=(\d+)")
# 站方自己域名（「公司官网」栏实测就写着自己商铺地址，不能当客户官网）
_SELF_DOMAINS = ("maimaib2b.com",)
# 实测查到的站方默认/灌填假号（随机 40 家样本里 0 次，但商铺首页底部「联系我们」见过）：
# 当联系方式入库会让外呼打到空号，一律作废。
_FAKE_NUMBERS = {"15805447789", "0534-8222227", "13800138000", "400-000-0000"}
# 服务类商铺常把 h1/标题写成关键词串（实测「深圳空调安装|深圳空调拆装|深圳空调维修|…」），
# 竖线后面是灌进来的业务词不是公司名；只留首段，短到不像名字的（<6 字）不动，
# 免得把「山东启杨净化设备」这类正常短名误伤。
_NAME_PIPE_RE = re.compile(r"\s*[|｜┃│]\s*")


class Maimaib2bCrawler(CdpSiteCrawler):
    PLATFORM_NAME = "麦麦B2B"
    PLATFORM_SLUG = "maimaib2b"
    BASE_URL = HOME_URL
    HOME_DOMAIN = "maimaib2b.com"
    # 平时纯 HTTP 不占标签；被 Cloudflare 质询时才借一个标签过盾，抄回 cf_clearance 再继续
    NEEDS_BROWSER = False
    BLOCK_OPEN_BROWSER = True
    BLOCK_COOKIE_DOMAIN = "maimaib2b.com"
    # CF 的「正在进行安全验证/请稍候…」转圈页会自己卡住不动（实测 2026-11-02 停在同一页
    # 1 分钟以上、正文始终 0 字符，看门狗只能干等）。这种页没有任何可点控件，刷新才会
    # 重新走一遍挑战并放行，所以打开「卡住自动刷新」。
    BLOCK_AUTO_REFRESH = True
    # 推进验证页的节奏：用户实测「隔十秒左右点一下刷新此页面就会解开」，所以探得密一点
    BLOCK_POLL = 10
    # 借标签取页时撞上未放行插页的等待上限（实测放着不动 10~50 秒会放行，超了就如实报错）
    TAB_WAIT_UNBLOCK = 45
    # 连续几次「浏览器通行证已抄进 HTTP 会话、裸请求仍被拦」就判定本机裸 HTTP 通路是死的。
    # 实测依据（2026-11-03 麦麦B2B）：浏览器 4 页连开全秒过（每页 40 张卡片），同一分钟把
    # 刚过验证的 cf_clearance 抄给 Python 裸请求连打 12 发＝12 个 403（TLS/HTTP2 指纹对不上）。
    # 更要命的是每一发 403 都在给站方的挑战计数加码（卡死那份会话里 cf_chl_rc_ni 已经数到 11），
    # 越试越走不出去——所以到点就收手，本轮别再发裸 HTTP。
    HTTP_PROBE_MAX_FAIL = 2
    # 一家公司 = 1 个详情请求（偶尔补 1 个商铺首页），节流下沉到请求级才压得住速率
    PER_REQUEST_PACE = True
    PACE_FACTOR = 0.5
    MIN_DELAY = 3.0
    # CF 薄壳 5.8KB vs 正文 24~47KB：把「插页体积上限」抬高才能认出 200 状态的空壳
    HTTP_BLOCK_MAX_BYTES = 15000
    HTTP_BLOCK_MARKS = CdpSiteCrawler.HTTP_BLOCK_MARKS + (
        "Just a moment", "请稍候", "cf-chl", "Attention Required", "checking your browser")
    MIN_PAGE_CHARS = 15000

    def __init__(self, port=9255):
        super().__init__(port)
        self.delay_between = 3.0
        self.jitter = 1.5
        self.max_pages = 0          # 不限页数：按页脚「共 Y 页」精确收尾
        self._session = self._new_session()
        # 盾已经过了、通行证却抄不回会话时的降级模式：临时改走标签取页，不空跑一轮
        self._browser_fallback = False
        self._fallback_pages = 0
        # 本轮是否已经试过「借标签取页」（借标签会真开一个窗口，不能每页都试一次）
        self._fallback_attempted = False
        # 本机裸 HTTP 通路是否已判死（见 HTTP_PROBE_MAX_FAIL）＋连续失败计数
        self._http_dead = False
        self._http_probe_fails = 0

    def reset_run_state(self):
        super().reset_run_state()
        self._browser_fallback = False
        self._fallback_pages = 0
        self._fallback_attempted = False
        self._http_dead = False
        self._http_probe_fails = 0
        return True

    def _new_session(self):
        s = requests.Session()
        s.headers.update({
            "User-Agent": _UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": HOME_URL,
        })
        return s

    # ── 旁路浏览器：本站不常驻标签、被质询时才借 ────────────────
    def start_browser(self, headed=True):
        return True

    def ensure_tab(self, log=None):
        return True

    def close_browser(self):
        return None

    def _is_session_alive(self):
        return True

    def _harvest_browser_cookies(self):
        """抄 Cookie 必须连 UA 一起抄：cf_clearance 与「浏览器 UA + 出口 IP」三元绑定，
        用别的 UA 带着它去请求照样被拒（实测）。"""
        n = super()._harvest_browser_cookies()
        if n:
            try:
                ua = self._eval("navigator.userAgent")
                if ua:
                    self._session.headers.update({"User-Agent": ua})
                    self._log(f"   ↺ HTTP 会话 User-Agent 已对齐浏览器（cf_clearance 认 UA）")
            except Exception:
                pass
        return n

    def probe_block(self):
        """看门狗恢复探测：必须是「真能解析出公司条目」才算解除（只看状态码会把 CF 质询页当已通）。
        本站的通行证先在浏览器手里，所以探测按三步走（不能只靠 HTTP 探，否则永远探不通）：
          ① HTTP 直探：通行证已在会话里时一步到位；
          ② 不通就把标签里已拿到的通行证抄给会话（连 UA 一起抄）再探一次；
          ③ 还是不通但标签已能正常打开本站正文＝盾过了、通行证抄不回来，
             转降级模式用标签取页继续，别整轮卡在这干等。
        探测自己绝不走 _fetch：_fetch 开头会按降级状态反过来叫 probe_block，互相递归。
        通路判死（_http_dead）后跳过①②只做③：那时候再发裸 HTTP 就是拿注定 403 的请求
        给站方风控计数加码，把用户刚点出来的进度顶回去（实测「人工点了还是回到这一页」）。"""
        if not self._http_dead:
            if self._http_probe_ok():
                self._block_reason = ""
                return ""
            try:
                harvested = bool(self._harvest_browser_cookies())
            except Exception:
                harvested = False
            if harvested:
                if self._http_probe_ok():
                    self._block_reason = ""
                    return ""
                self._http_probe_fails += 1
                if self._http_probe_fails >= self.HTTP_PROBE_MAX_FAIL:
                    self._http_dead = True
                    self._log(f"   ⛔ 浏览器通行证已抄进会话、裸 HTTP 仍连吃 "
                              f"{self._http_probe_fails} 次拦截：本机这条路是死的，"
                              f"本轮全部改走标签取页（再发只会给站方挑战计数加码）")
        if self._tab_page_ok(self._block_probe_url or self.build_search_url("公司", 1)):
            self._browser_fallback = True
            self._fallback_pages = 0
            self._block_reason = ""
            return ""
        return self._block_reason or "麦麦B2B 仍在 Cloudflare 质询状态"

    def _http_probe_ok(self):
        """拿被拦那一页（没有则用一个通用关键词的首页）真试一次：能解析出公司条目才算通。"""
        if self._http_dead:
            return False
        url = self._block_probe_url or self.build_search_url("公司", 1)
        try:
            r = self._session.get(url, timeout=20)
            if r.status_code == 200:
                if "charset" not in (r.headers.get("Content-Type") or "").lower():
                    r.encoding = "utf-8"
                return bool(self.parse_list(r.text or "", "公司"))
        except Exception:
            pass
        return False

    def _tab_page_ok(self, url=""):
        """标签能不能真把要的那一页取回来（能力判据，不看正文长度）。
        三道坎都得过，实测 2026-11-02~03 各踩过一次：
          · 旧写法拿 innerText 长度当门槛（>=5000）：「制冷」列表页 outerHTML 41561 字符、
            能解析出 20 家，但 innerText 只到 1562 字符（卡片文字大多写在 title 属性里），
            于是盾已经过了也被判成「没过」，整轮干等到超时；
          · 只看「标签当前页」又不行：首页已放行、搜索页还在「请稍候…」，拿当前页验会误报
            盾过了（实测独立浏览器里就误判成 True，紧接着降级取页取回 0 字符）；
          · 拿 outerHTML 长度当门槛更不行：质询插页自己就有 44675 字符（见 _tab_real_page）。
        所以给了 url 就按那一页真取一次；没给 url 才退而验当前页。"""
        if not self.pin_tab_id:
            return False
        if url:
            return bool(self._fetch_via_tab(url))
        try:
            st = self._page_state() or {}
            if "maimaib2b.com" not in (st.get("url") or ""):
                return False
            return self._tab_real_page(self._html() or "")
        except Exception:
            return False

    def _tab_real_page(self, page):
        """标签当前这一页是不是站点真内容。
        实测坑（2026-11-03 独立浏览器）：Cloudflare 质询页的 outerHTML 有 44675 字符，
        比正文门槛（15000）还大、又不带英文「Just a moment」特征词，旧判据把它当正文取回，
        结果解析出 0 家、不报错、还把会话切成了降级模式——「一直采不到数据」就是这么来的。
        判据改按能力：插页特征词直接拒；列表页要能解析出公司卡片，
        其它页（详情/这个词没结果）看 innerText 是否实打实有内容。"""
        page = page or ""
        if not page or self._auto_challenge_page():
            return False
        # 这里只“看一眼这页像不像真内容”，所以直接查插页特征词，不调 http_block_reason：
        # 那个函数会顺手改写拦截分诊状态（_block_kind、_block_probe_url），
        # 而分诊状态是看门狗用来决定「等人工过验证」还是「等 IP 冷却」的依据，
        # 被一次取页判断改掉就会把后面整轮的处置方式带偏（该借标签的变成干等冷却）。
        if len(page) < self.HTTP_BLOCK_MAX_BYTES and any(
                m in page[:4000] for m in self.HTTP_BLOCK_MARKS):
            return False
        if self.parse_list(page, "公司"):
            return True
        try:
            return int((self._page_state() or {}).get("len") or 0) >= 400
        except Exception:
            return False

    @staticmethod
    def _page_key(u):
        """取一个地址的「页面身份」：只看域名+路径，丢掉质询令牌那串 query。
        Cloudflare 跳转时地址会变成 https://www.maimaib2b.com/?__cf_chl_rt_tk=xxx，
        它跟目标页同一个会话、马上就要落回目标页，不能当成「另一个页」。"""
        try:
            p = urlsplit(u or "")
            return (p.netloc.lower(), (p.path or "").rstrip("/")) if p.netloc else ("", "")
        except Exception:
            return ("", "")

    def _fetch_via_tab(self, url):
        """降级取页：HTTP 仍被拦但浏览器已过盾，临时用共享 Chrome 标签取同一页，
        让采集继续（比 HTTP 慢，但远好过整轮空等）。
        撞上还没放行的插页时不是直接拿空串：按看门狗同一套路推它（先点「刷新此页面」，
        插页上没入口就把它带回前台等它自己跑完，实测 10~50 秒会放行）再取。
        已经在去这一页的路上就**不要再导航**（实测 2026-11-03）：每导航一次就是
        向站点重开一场挑战，连着几次就永远停在「Just a moment…」（248 秒不过）。"""
        if not self.pin_tab_id:
            return ""
        try:
            cur = self._current_url() or ""
        except Exception:
            cur = ""
        key_cur, key_want = self._page_key(cur), self._page_key(url)
        # 同一个页（包括 CF 拿着我们的请求在跳转的 ?__cf_chl_rt_tk=… 中间态）就别再导航
        same_page = bool(key_want[0]) and key_cur[0] == key_want[0] and (
            key_cur == key_want or "__cf_chl" in cur.lower())
        if not same_page:
            try:
                self._cdp_navigate(url)
                self._wait_ready(None, timeout=25)
            except Exception:
                return ""
        waited = 0
        nap = max(3, int(self.BLOCK_POLL))
        while True:
            html = self._html() or ""
            if self._tab_real_page(html):
                self._mark_activity()
                return html
            # 不是插页却仍没内容＝这页本来就没东西（404/空结果），别对着它死等
            if waited >= self.TAB_WAIT_UNBLOCK or not self._auto_challenge_page():
                return ""
            # 推一把时要明确告诉它“我要的就是这一页”：保底重载若回首页或旧的探测页，
            # 下面一次 _html() 就会把别的页当成这一页收走（采到串页的假数据）。
            self._refresh_stalled_challenge(waited, target=url)
            time.sleep(nap)
            waited += nap

    def _after_block_cleared(self):
        """降级模式下得把标签留在手里取页，不能按常规流程把它归还。"""
        if self._browser_fallback:
            self._block_reason = ""
            return
        super()._after_block_cleared()

    # ── 裸 HTTP 取页 ───────────────────────────────────────────
    def _fetch(self, url, tries=3, guarded=True, referer=""):
        if self._browser_fallback:
            # 降级模式下隔 20 个请求真探一次 HTTP（只认「会话里已有通行证且能拉到公司条目」，
            # 不能拿 probe_block 当恢复判据——它看到标签能用也会报“已解除”，会把降级模式误切回）；
            # 通路已判死时连这次试探也不发，直接走标签。
            self._fallback_pages += 1
            if self._http_dead or self._fallback_pages % 20 or not self._http_probe_ok():
                return self._fetch_via_tab(url)
            self._browser_fallback = False
            self._block_reason = ""
            self._release_verify_tab()
            self._log("  ✅ 麦麦B2B HTTP 链路已恢复（通行证已拿回会话），切回不占标签的快路")
        last = ""
        for i in range(max(1, tries)):
            try:
                self._pace_before_request()
                headers = {"Referer": referer} if referer else None
                r = self._session.get(url, timeout=25, headers=headers)
                self._mark_activity()
                block = self.http_block_reason(r.status_code, r.text, url, r.headers)
                if block:
                    # CF 质询/限流：对着它重试只会加重风控，直接交看门狗（借标签过盾）
                    self._block_reason = block
                    last = f"HTTP {r.status_code}（防采插页）"
                    break
                if r.status_code == 200 and r.text:
                    if "charset" not in (r.headers.get("Content-Type") or "").lower():
                        r.encoding = "utf-8"      # 页内声明 charset=utf-8，HTTP 头没带就别按 latin-1 解
                    self._block_reason = ""
                    self._fallback_attempted = False   # 通路又好了，下次卡住还可以再试一次降级
                    return r.text
                last = f"HTTP {r.status_code}"
                if r.status_code == 404:
                    return ""
            except requests.exceptions.ConnectionError as e:
                last = str(e).splitlines()[0][:80] or "连接被重置"
                self._session.close()
                self._session = self._new_session()
            except Exception as e:
                last = str(e).splitlines()[0][:80]
            if i < tries - 1:
                time.sleep(1.2 * (i + 1))
        if guarded and self._block_reason:
            if self.handle_http_block(url):
                self._block_reason = ""
                return self._fetch(url, tries=tries, guarded=False, referer=referer)
            # 等不到 HTTP 恢复，但探测已判定标签能正常开本站：降级继续采
            if self._browser_fallback:
                return self._fetch_via_tab(url)
        # HTTP 这条路到底没拿到这一页，而浏览器已经放行：改用标签取。
        # 绝不能就这么返回空串——那一页的公司就整批丢了（实测 2026-11-02：探活报「已解除」
        # 把等待循环收了尾、标签也归还了，紧接着正式请求又 403，「制冷」第 1 页就这么 0 家）。
        # 只试一次（借标签会真开窗口），直到 HTTP 又成功拿到一页才重新给机会。
        if self._block_reason and not self._fallback_attempted:
            self._fallback_attempted = True
            if not self.pin_tab_id:
                self.open_verify_page(url)
            # 按「那一页能不能真取回来」验，不拿当前页的长相当盾过了（避免误判后取回 0 字符）
            page = self._fetch_via_tab(url)
            if page:
                self._browser_fallback = True
                self._fallback_pages = 0
                self._block_reason = ""
                self._log("  ↪ HTTP 仍拿不到页、浏览器已放行：改用标签取这一页（降级模式）")
                return page
        self._log(f"  ⚠ 麦麦B2B 请求失败（{last}）：{url[:80]}")
        return ""

    # ── 列表 ───────────────────────────────────────────────────
    def build_search_url(self, kw, page):
        kw = quote(str(kw))
        return SEARCH_URL.format(kw=kw) if page <= 1 else SEARCH_PAGE_URL.format(kw=kw, page=page)

    def parse_list(self, html, kw):
        """Organization 微数据卡片 → 公司条目（按店铺 id 去重）。"""
        out, seen = [], set()
        for chunk in (html or "").split("data-vocabulary.org/Organization")[1:]:
            chunk = chunk[:4000]
            tm = _A_TITLE.search(chunk)
            if tm:
                name, sid = tm.group(1), tm.group(2)
            else:
                sm = _SHOP_HREF.search(chunk)
                if not sm:
                    continue
                sid = sm.group(1)
                am = re.search(r"<a[^>]*Shop/(\d+)/?[^>]*>(.*?)</a>", chunk, re.S)
                if not am or am.group(1) != sid:
                    continue
                name = _htmlmod.unescape(_TAG_RE.sub("", am.group(2))).strip()
            name = _htmlmod.unescape(name).strip()
            sid = str(sid).strip()
            if not sid or sid in seen or len(name) < 4:
                continue
            if any(x in name for x in ("首页", "登录", "注册", "更多", "下一页")):
                continue
            seen.add(sid)
            prod = _htmlmod.unescape(_TAG_RE.sub("", _PROD_DD.search(chunk).group(1))).strip() \
                if _PROD_DD.search(chunk) else ""
            tel = _htmlmod.unescape(_TAG_RE.sub("", _TEL_SPAN.search(chunk).group(1))).strip() \
                if _TEL_SPAN.search(chunk) else ""
            out.append({
                "公司名称": name[:60],
                "公司ID": sid,
                "memberId": sid,
                "公司网址": f"https://www.maimaib2b.com/Shop/{sid}/",
                "详情URL": LINK_URL.format(sid=sid),
                "主营产品": re.sub(r"\s+", " ", prod)[:200],
                "经营模式": "",
                "所在区域": "",
                "列表电话": re.sub(r"\s+", "", tel)[:60],
                "标题": name[:80],
            })
        return out

    def list_has_more(self, html, page):
        """按页脚「当前第X页，共Y页」判定；读不到就交给“翻到没条目自然停”兜底。"""
        m = _PAGEINFO_RE.search(html or "")
        if not m:
            return bool(html)
        return int(m.group(1)) < int(m.group(2))

    def slice_total(self, html):
        m = _TOTAL_RE.search(html or "")
        return int(m.group(1).replace(",", "")) if m else -1

    # ── 列表翻页：必须自己走裸 HTTP（基类那套是用浏览器标签取页的）──────
    def _iter_keyword(self, kw):
        """列表页取页必须走本类的 _fetch：它才有「403 薄壳→借标签过盾→抄 cf_clearance
        回会话→重试」这条链，也才有限流刹车。基类的 _iter_keyword 是用 _goto + _html
        （浏览器取页）实现的，而本站把 start_browser/ensure_tab 覆盖成了空壳（平时不该占
        标签），于是列表一条都取不到、又不经过 _fetch 就永远不会报「请求失败」、界面只显示
        「0 家成功」（2026-11-02 实机复现：每个关键词空转 80 秒、一家不产出、日志一句话没有）。
        其余记账口径（去重键、早过滤、stat_*）与基类逐字对齐，别改出第二套统计。"""
        empty = stale = 0
        page = 0
        while not self._stop_requested:
            page += 1
            if self.max_pages and page > self.max_pages:
                return
            while self._pause_requested and not self._stop_requested:
                time.sleep(0.5)
            url = self.build_search_url(kw, page)
            html = self._fetch(url)
            if not html:
                # 拿不到页＝这一页没翻到（超末页 404）或被防采/限流拦到底了，两种都不该再往下翻
                self._log(f"  「{kw}」第 {page} 页没取到页面（到底了或仍在防采），停止翻页")
                return
            entries = self.parse_list(html, kw)
            self.stat_pages += 1
            self.stat_listed += len(entries)
            if not entries:
                empty += 1
                if empty >= 2:
                    self._log(f"  「{kw}」第 {page-1} 页起无公司结果，停止翻页")
                    return
                continue
            empty = 0
            new = skipped = seen = 0
            for e in entries:
                mid = str(e.get("公司ID") or e.get("memberId") or "")
                key = f"{self.PLATFORM_SLUG}:{mid}"
                name = self._norm_name(e["公司名称"])
                nkey = f"{self.PLATFORM_SLUG}:name:{name}"
                if key in self._seen_members or nkey in self._seen_members \
                        or f"{self.PLATFORM_SLUG}:{kw}#{mid}" in self._seen_members:
                    seen += 1
                    self._incremental_skipped += 1
                    continue
                # 列表早过滤只看产品信号（列表页有主营产品，判定比按名字猜靠谱）
                # 砍掉的**只按「关键词#主体」标记**，不占全局已见：相关性是「对这个关键词」的判定，同一家对 A 词不符、对 B 词可能正是目标（实机砍掉的名单里就有真同行）。
                if not self.is_relevant(kw, self.stub_relevance_text(e)):
                    skipped += 1
                    self._early_skipped += 1
                    self._seen_members.add(f"{self.PLATFORM_SLUG}:{kw}#{mid}")
                    self._commit_to_store({"状态": "类目不符(已过滤)", "关键词": kw,
                                           "公司名称": e["公司名称"], "memberId": mid})
                    continue
                self._seen_members.add(key)
                self._seen_members.add(nkey)
                e["关键词"] = kw
                new += 1
                yield e
            tail = self._page_tail(skipped, seen)
            self._log(f"  「{kw}」第 {page} 页：{len(entries)} 条，新增 {new} 家{tail}")
            if new == 0:
                stale += 1
                if stale >= 2:
                    self._log(f"  「{kw}」连续 {stale} 页无新增，公司池已枯竭")
                    return
            else:
                stale = 0
            if not self.list_has_more(html, page):
                return
            self._sleep()

    # ── 详情：/Shop/<id>/link.html 两块联系信息合并 ────────────
    def crawl_detail(self, stub):
        rec = self._base_rec(stub)
        sid = stub.get("公司ID") or stub.get("memberId") or ""
        if not sid:
            rec["状态"] = "详情页导航失败"
            return rec
        url = LINK_URL.format(sid=sid)
        rec["详情URL"] = url
        html = self._fetch(url, referer=stub.get("公司网址", HOME_URL))
        if not html:
            rec["状态"] = "详情页导航失败"
            return rec
        for k, v in self._parse_link(html, stub).items():
            if v and not rec.get(k):
                rec[k] = v
        # link.html 没拿到号码时，商铺首页底部「联系我们」还有一份
        if not (rec.get("手机") or rec.get("电话")):
            self._sleep()
            home = self._fetch(f"https://www.maimaib2b.com/Shop/{sid}/", referer=url)
            for k, v in self._parse_bottom(home).items():
                if v and not rec.get(k):
                    rec[k] = v
        if not rec.get("公司名称") or len(rec.get("公司名称", "")) < 4:
            rec["公司名称"] = self._clean_name(strip_name_marketing_tail(stub.get("公司名称", "")))
        if not rec.get("所在区域"):
            rec["所在区域"] = self._region_from(rec.get("地址", ""))[:30]
        if not self.detail_relevant(rec):
            rec["状态"] = "类目不符(已过滤)"
            return rec
        has_phone = bool(rec.get("电话") or rec.get("手机"))
        rec["状态"] = ("成功" if (has_phone or rec.get("邮箱"))
                       else ("无电话(未公开)" if rec.get("地址") or rec.get("公司网址") else "无联系方式"))
        return rec

    @staticmethod
    def _clean_name(name):
        """关键词串型店名只留首段（「深圳空调安装|深圳空调拆装|…」→「深圳空调安装」）。"""
        name = (name or "").strip()
        if not name:
            return ""
        head = _NAME_PIPE_RE.split(name, maxsplit=1)[0].strip()
        return head if len(head) >= 6 else name

    @classmethod
    def _parse_link(cls, html, stub):
        out = {}
        m = _H1_RE.search(html)
        if m and len(m.group(1).strip()) >= 4:
            out["公司名称"] = cls._clean_name(_htmlmod.unescape(m.group(1)).strip())[:60]
        mp = _SMALL_RE.search(html)
        if mp:
            out["主营产品"] = _htmlmod.unescape(mp.group(1)).strip()[:200]
        # 两块（联系方式 / 公司地址）标签写法一致：姓名侧「联 系 人」「联系人」、
        # 号码侧「座机」「固定电话」「手机」「手机号码」，统一按语义归并
        fields = {}
        for k, v in _FIELD_RE.findall(html):
            k = re.sub(r"\s+", "", _htmlmod.unescape(k)).strip("：:")
            v = _htmlmod.unescape(_TAG_RE.sub(" ", v)).strip(" ：:")
            if not k or k not in fields:
                fields[k] = v
        person = fields.get("联系人", "")
        cname, cpos = parse_contact_field(person)
        if cname:
            out["联系人"] = cname
        pos = compose_position(fields.get("职务", "") or fields.get("职位", ""),
                               "" if HONORIFIC_ONLY_RE.match(cpos or "") else cpos)
        if pos:
            out["岗位"] = pos[:20]
        if not cname and not cpos:
            d = dept_only(person)
            if d:
                out["岗位"] = d[:20]

        blob = " ".join([fields.get(k, "") for k in
                         ("座机", "固定电话", "电话", "手机", "手机号码", "微信", "联系方式")]
                        + [stub.get("列表电话", "")])
        mobiles, tels = [], []
        for v in re.split(r"[/、,;\s]+", blob):
            v = strip_country_code(v)
            if not v or is_placeholder_number(v) or v in _FAKE_NUMBERS:
                continue
            if PHONE_MOBILE_RE.fullmatch(v) and v not in mobiles:
                mobiles.append(v)
            elif PHONE_TEL_RE.fullmatch(v) and v not in tels:
                tels.append(v)
        if mobiles:
            out["手机"] = " / ".join(mobiles[:3])
        if tels:
            out["电话"] = " / ".join(tels[:3])

        em = fields.get("电子邮箱", "") or fields.get("邮箱", "")
        g = EMAIL_RE.search(em)
        if g and not any(d in g.group(0).lower() for d in _SELF_DOMAINS):
            out["邮箱"] = g.group(0)
        # 地址行用 <a> 拆了省/市/区三段，剥标签后留下一串连空格（实测「广东   深圳 宝安区」）
        addr = clean_address(re.sub(r"\s+", " ", fields.get("公司地址", "") or fields.get("地址", "")))
        if addr:
            out["地址"] = addr
        site = fields.get("公司官网", "")
        if site and site.startswith("http") and not any(d in site for d in _SELF_DOMAINS):
            out["公司网址"] = site
        if fields.get("经营模式"):
            out["经营模式"] = fields["经营模式"][:20]
        if not out.get("主营产品") and fields.get("主营产品"):
            out["主营产品"] = fields["主营产品"][:200]
        if not out.get("主营产品") and fields.get("企业类型"):
            out["主营产品"] = fields["企业类型"][:200]
        # 面包屑「企业名录 » 行业 » 省行业」里补行业名（列表页常常没有主营产品）
        b = _BREAD_RE.search(html)
        if b:
            items = [re.sub(r"企业名录$", "", x).strip()
                     for x in re.findall(r"title=['\"]([^'\"]+)['\"]", b.group(1))]
            ind = next((x for x in items if x and len(x) >= 2), "")
            if ind and not out.get("主营产品"):
                out["主营产品"] = ind[:200]
        qq = _QQ_RE.search(html)
        if qq and not qq.group(1).startswith(("0", "10000")):
            out["QQ"] = qq.group(1)
        return out

    @staticmethod
    def _parse_bottom(html):
        """商铺首页底部 `<dl class="bottom"><dt>联系我们</dt><dd>名称：…电话：…手机：…地址：…</dd>`。"""
        out = {}
        if not html:
            return out
        i = html.find("联系我们")
        if i < 0:
            return out
        seg = html[i:i + 2500]
        for k, v in re.findall(r"<dd>\s*([^<：:]{2,6})\s*[：:]\s*(.*?)</dd>", seg, re.S):
            k = k.strip()
            v = _htmlmod.unescape(_TAG_RE.sub(" ", v)).strip()
            if not v:
                continue
            if k == "名称" and len(v) >= 4:
                out["公司名称"] = v[:60]
            elif k == "地址":
                out["地址"] = clean_address(re.sub(r"\s+", " ", v))
            elif k in ("电话", "手机"):
                digits = strip_country_code(v)
                if is_placeholder_number(digits) or digits in _FAKE_NUMBERS:
                    continue
                if PHONE_MOBILE_RE.fullmatch(digits):
                    out["手机"] = digits
                elif PHONE_TEL_RE.fullmatch(digits):
                    out["电话"] = digits
        return out

    @staticmethod
    def _region_from(address):
        """地址行实测以「省 市 区」开头（空格分隔），取前两级当所在区域。
        但直辖市那种只切两段（2026-11-03「光伏」自测实测：「天津 天津市静海区大邱
        庄镇前尚码头村西1000米」）：第二段把市、区、镇、村名全粘在一起，原样拼进去
        就等于「所在区域」列又把完整地址重填了一次。所以每一级只取到行政区划的尾巴，
        长的那段从开头截出「…市/区/县/旗」，而「天津」+「天津市…」这种重复只留更具体的那个。"""
        parts = [p for p in re.split(r"[\s,，]+", address or "") if p]
        out = []
        for p in parts:
            if len(out) >= 2 or not re.search(r"[\u4e00-\u9fa5]", p):
                break
            if len(p) > 8:          # 这一段已经混进街道/村名，只截行政区划部分
                m = re.match(r"[\u4e00-\u9fa5]{1,10}(?:自治区|自治州|地区|市|区|县|旗)", p)
                if not m:
                    break
                p = m.group()
            if out and (p in out[-1] or out[-1] in p):
                if len(p) > len(out[-1]):
                    out[-1] = p
                continue
            out.append(p)
        return "".join(out[:2]) if out else ""
