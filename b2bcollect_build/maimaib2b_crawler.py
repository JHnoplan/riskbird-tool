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
from urllib.parse import quote

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

    def reset_run_state(self):
        super().reset_run_state()
        self._browser_fallback = False
        self._fallback_pages = 0
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
        探测自己绝不走 _fetch：_fetch 开头会按降级状态反过来叫 probe_block，互相递归。"""
        if self._http_probe_ok():
            self._block_reason = ""
            return ""
        try:
            if self._harvest_browser_cookies() and self._http_probe_ok():
                self._block_reason = ""
                return ""
        except Exception:
            pass
        if self._tab_page_ok():
            self._browser_fallback = True
            self._fallback_pages = 0
            self._block_reason = ""
            return ""
        return self._block_reason or "麦麦B2B 仍在 Cloudflare 质询状态"

    def _http_probe_ok(self):
        """拿被拦那一页（没有则用一个通用关键词的首页）真试一次：能解析出公司条目才算通。"""
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

    def _tab_page_ok(self):
        """借来的标签当前是否已能正常打开本站正文页（用户在浏览器里过完盾的直观表现）。"""
        if not self.pin_tab_id:
            return False
        try:
            st = self._page_state() or {}
            if "maimaib2b.com" not in (st.get("url") or ""):
                return False
            return int(st.get("len") or 0) >= self.MIN_PAGE_CHARS // 3
        except Exception:
            return False

    def _fetch_via_tab(self, url):
        """降级取页：HTTP 仍被拦但浏览器已过盾，临时用共享 Chrome 标签取同一页，
        让采集继续（比 HTTP 慢，但远好过整轮空等）。"""
        try:
            self._cdp_navigate(url)
            self._wait_ready(None, timeout=25)
            self._mark_activity()
            html = self._html() or ""
            if len(html) >= self.MIN_PAGE_CHARS and not self.http_block_reason(200, html):
                return html
        except Exception:
            pass
        return ""

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
            # 不能拿 probe_block 当恢复判据——它看到标签能用也会报“已解除”，会把降级模式误切回）
            self._fallback_pages += 1
            if self._fallback_pages % 20 or not self._http_probe_ok():
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
        """地址行实测以「省 市 区」开头（空格分隔），直接取前两级当所在区域。"""
        parts = [p for p in re.split(r"[\s,，]+", address or "") if p][:3]
        if not parts:
            return ""
        if len(parts[0]) <= 8 and re.search(r"[\u4e00-\u9fa5]", parts[0]):
            return "".join(parts[:2])
        return ""
