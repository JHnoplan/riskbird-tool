"""gys_crawler.py —— 供应商网(gys.cn) 关键词搜索采集器。

链路（实机探测确认，2026-10）：
  1) 搜索：gys 首页搜索框是 JS「SearchControl」（隐藏域 d=https://product.gys.cn、
     m=104，输入框 #keyinput，按钮 #searchBtn），结果页 URL 的服务端 code 无法由
     关键词直接构造，故**驱动真实搜索 UI**：填 #keyinput → 点 #searchBtn →
     落到 https://www.gys.cn/search/<code>.shtml（如「太阳能」→ s41x.shtml）。
     渲染后结果页内含一批公司自有子域锚点 <sub>.gys.cn + 公司名（实测约 56 家/页）。
     分页：<结果页>?p=2 / ?p=3 …（页内有「下一页」与页码链接）。
  2) 公司详情  https://<sub>.gys.cn/  （公司自有静态子域，字段最全、与列表主机不同源）
     逐行配对解析联系人/电话/邮箱/地址/经营模式/主营产品（复用 scrape_gys.py 生产逻辑，
     实测 huakuang666.gys.cn → 公司名/手机/地址/主营均正确取到）。

编码说明：CDP 取回的是浏览器已解码的 outerHTML（unicode），解析层无 GBK 问题；
搜索走 UI 输入原始关键词，也不需要 GBK 转义（保留 gbk_quote 仅作兼容）。
"""
import re
import json
import time
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from cdp_base import (CdpSiteCrawler, PHONE_MOBILE_RE, PHONE_TEL_RE, EMAIL_RE, clean_address,
                      parse_contact_field, is_placeholder_number, normalize_spaces,
                      strip_name_marketing_tail, compose_position, extract_legal_rep)

SEARCH_HOME = "https://www.gys.cn/"
# 结果页里的公司自有子域锚点：href 指向 <sub>.gys.cn，锚文本即公司名
COMPANY_ANCHOR_RE = re.compile(
    r'<a[^>]*href="https?://([a-z0-9][a-z0-9_\-]+)\.gys\.cn[^"]*"[^>]*>(.*?)</a>',
    re.S | re.I)
SUBDOMAIN_RE = re.compile(r'//([a-z0-9][a-z0-9_\-]+)\.gys\.cn', re.I)
# 系统/平台子域（等值比较，不会误伤 shop123 这类真实公司子域）
EXCLUDED_SUBS = {'www', 'my', 'm', 'res', 'static', 'cdn', 'api', 'product',
                 'cn', 'en', 'img', 'image', 'images', 'news', 'so', 'search',
                 'member', 'membercenter', 'login', 'help', 'about', 'shop',
                 'b2b', 'info', 'cache', 'doc', 'docs', 'gongsi', 'zhanhui',
                 'caigou', 'kf', 'service', 'tools', 'zt', 'topic', 'pifa'}
# 结果页锚文本里的导航/功能项，绝不能当成公司名
NAV_TEXT_MARKS = ("首页", "登录", "注册", "退出", "发布产品", "管理产品", "供应商网",
                  "入驻", "手机站", "移动站点", "客服中心", "联系我们", "更多", "[请",
                  "产品中心", "公司库", "产品分类", "免费", "手机版")
# 平台自身号码，绝不能当成公司电话
GLOBAL_EXCLUDED_PHONES = set()

# 商铺静态子域纯 HTTP 可直取（curl 实测 dongfangxinhong.gys.cn 返回 179KB 完整页，
# 含 JSON-LD 电话与「联系方式」区块），故详情走裸 HTTP、浏览器只做兜底。
GYS_HTTP_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}
# 联系方式区块/联系页的标签 → 统一字段名。gys 用全角空格对齐（「职　位」「电　话」），
# 匹配前必须先把标签里的空白全剥掉。
_LABEL_FIELD = {
    "联系人": "联系人", "姓名": "联系人",
    "职位": "岗位", "职务": "岗位", "岗位": "岗位",
    "电话": "电话", "联系电话": "电话", "手机": "手机", "手机号码": "手机",
    "邮箱": "邮箱", "电子邮箱": "邮箱", "邮件": "邮箱", "email": "邮箱",
    "地址": "地址", "公司地址": "地址", "详细地址": "地址", "联系地址": "地址",
    "所在地区": "所在地区", "经营模式": "经营模式", "主营产品": "主营产品",
}
# 联系页（/contact-information/）的语义 class：实测 linkman/position/mobile/address。
# address-text 必须排在 address 前面：class="address" 的容器里还套着「查看地图」按钮，
# 取容器文本会把按钮文案粘进地址尾部。
_CLASS_FIELD = {"linkman": "联系人", "position": "岗位", "mobile": "手机",
                "tel": "电话", "address-text": "地址", "address": "地址", "email": "邮箱"}
# 传真/QQ/邮编不是联系方式，但必须识别为标签，否则它们的值会被当成人名/地址吃进来
_SKIP_LABELS = ("传真", "qq号码", "qq号", "邮编", "入驻年", "会员年", "所在地")


def _is_platform_email(email):
    """平台自身客服邮箱绝不能当成公司邮箱入库（实测浏览器渲染的未登录页明文写
    「邮箱：kefu@gys.cn」，而 HTTP 取到的同一商铺页根本没有这一行）。"""
    e = (email or "").lower()
    return ("gys.cn" in e or "example" in e or "baidu.com" in e
            or e.startswith(("kefu", "service@", "support@", "admin@", "postmaster@")))


def gbk_quote(kw):
    """关键词按 GBK 编码后 URL 转义（保留兼容，UI 搜索链路不需要它）。"""
    try:
        return quote(kw.encode('gbk'))
    except Exception:
        return quote(kw)


class GysCrawler(CdpSiteCrawler):
    PLATFORM_NAME = "供应商网"
    PLATFORM_SLUG = "gys"
    BASE_URL = "https://www.gys.cn/"
    HOME_DOMAIN = "gys.cn"
    BLOCK_URL_MARKERS = ("verify", "captcha", "/punish", "security", "antispam")
    SAFE_TITLE_HINTS = ("供应商", "公司", "厂家", "黄页", "企业", "价格", "报价", "gys")
    # gys 页面正文规模较小，阈值下调以免把正常页误判为「异常小」
    MIN_PAGE_CHARS = 600
    SMALL_PAGE_CHARS = 1200
    # 限流插页标题（实测 curl/CDP 都能命中「访问异常 - 供应商网」，整页无数据）：
    # 绝不能当正常页解析后写「无电话(未公开)」进种子库——实测那会把限流时刻的
    # 大批公司永久锁成空记录（而真实页面 JSON-LD 里明明有电话）。
    ABNORMAL_TITLE_MARKS = ("访问异常", "访问过于频繁", "安全验证", "拒绝访问")

    def __init__(self, port=9255):
        super().__init__(port)
        # 多子域共享同一后端，IP 级限流风险高（见项目既有结论）：低频是防封第一原则
        self.delay_between = 6.0
        self.jitter = 3.0
        self.max_pages = 0          # 不限页数：一直翻到没数据再切下一个关键词
        # 纯 HTTP 快路退化开关：实测 IP 进入限流区后子域页统一返回 HTTP 520
        # （与 UA/Accept 头无关，换 curl UA 也一样），此时每家再白跑两轮 HTTP 只会
        # 拖慢并加重风控。连续失败到阈值就整轮关掉 HTTP，只走浏览器。
        self._http_fails = 0
        self._http_disabled = False
        self._block_probe_url = ""   # 看门狗探测用的代表页（就是刚被拦的那一页）

    # ── 防采/限流看门狗（两条链路都可能停在插页）───────────────
    def reset_run_state(self):
        super().reset_run_state()
        self._http_fails = 0
        self._http_disabled = False
        self._block_probe_url = ""
        return True

    def _gys_blocked(self):
        """当前是否处于「被站方拦」状态（而不是这家商铺 404/单纯网络抖动）：
        HTTP 侧记下的插页原因，或标签当前页仍是插页/验证页。"""
        if self._block_reason:
            return True
        try:
            return self._is_abnormal_page() or bool(self._detect_block())
        except Exception:
            return False

    def probe_block(self):
        """看门狗恢复探测：先 HTTP 直取刚才被拦的那一页，再看标签当前页。
        任一路已正常就算解除（滑过验证码只清浏览器侧、IP 冷却则两条一起通）。
        探测绝不主动导航：不能把用户正拖到一半的滑块刷掉。"""
        url = self._block_probe_url or self.BASE_URL
        if url:
            try:
                r = requests.get(url, headers=GYS_HTTP_HEADERS, timeout=20)
                if r.status_code == 200 and r.text and not self._html_abnormal(r.text):
                    self._http_fails = 0
                    self._http_disabled = False
                    return ""
            except Exception:
                pass
        try:
            if not self._gys_blocked():
                return ""
        except Exception:
            return ""
        return self._block_reason or "供应商网仍在限流/验证状态"

    # ── 搜索：驱动真实 UI（结果页 code 无法由关键词构造）──────────
    def build_search_url(self, kw, page):
        # gys 走 UI 搜索，_iter_keyword 自行驱动，这里不参与默认翻页
        return None

    def _ui_search(self, kw):
        """在首页填关键词并点搜索，等待落到 /search/<code>.shtml，返回结果页基础 URL。"""
        if not self._goto(SEARCH_HOME, expect_domain="gys.cn"):
            return ""
        script = (
            "(function(){"
            "var i=document.querySelector('#keyinput')||document.querySelector('input[name=\"k\"]');"
            "if(!i)return 'NOINPUT';"
            "i.focus();i.value=%s;"
            "i.dispatchEvent(new Event('input',{bubbles:true}));"
            "i.dispatchEvent(new Event('change',{bubbles:true}));"
            "var b=document.querySelector('#searchBtn')||document.querySelector('.search-action');"
            "if(!b){var f=i.form;if(f){f.submit();return 'SUBMITTED';}return 'NOBTN';}"
            "b.click();return 'CLICKED';})()"
        ) % json.dumps(kw, ensure_ascii=False)
        r = self._eval(script)
        if r in ("NOINPUT", "NOBTN"):
            self._log(f"  「{kw}」未找到搜索控件（{r}），跳过")
            return ""
        deadline = time.time() + 16
        while time.time() < deadline and not self._stop_requested:
            u = self._current_url() or ""
            if re.search(r"/search/[^?/]+\.shtml", u):
                self._wait_ready("gys.cn", timeout=14)
                return u.split("?")[0]
            time.sleep(0.6)
        u = self._current_url() or ""
        return u.split("?")[0] if re.search(r"/search/[^?/]+\.shtml", u) else ""

    def parse_list(self, html, kw):
        """从渲染后的搜索结果页提取公司条目：子域锚点 <sub>.gys.cn + 锚文本公司名。"""
        out, seen = [], set()
        for m in COMPANY_ANCHOR_RE.finditer(html):
            sub = m.group(1).lower()
            if sub in EXCLUDED_SUBS or sub in seen:
                continue
            name = re.sub(r'<[^>]+>', '', m.group(2))
            name = re.sub(r'\s+', '', name).strip()
            # 锚文本同样是「公司名（XX厂家）」的 SEO 写法，入库前剥掉尾巴
            name = strip_name_marketing_tail(name)
            if not name or len(name) < 4:
                continue
            if any(x in name for x in NAV_TEXT_MARKS):
                continue
            seen.add(sub)
            out.append({
                "公司名称": name[:60],
                "公司ID": sub,
                "memberId": sub,
                "公司子域名": f"{sub}.gys.cn",
                "公司网址": f"https://{sub}.gys.cn/",
                "详情URL": f"https://{sub}.gys.cn/",
                "主营产品": "",
                "标题": name[:80],
            })
        return out

    def list_has_more(self, html, page):
        return False

    # 覆盖基类翻页：gys 先 UI 搜索拿结果页 code，再靠 ?p=N 翻页
    def _iter_keyword(self, kw):
        base = self._ui_search(kw)
        if not base and self._gys_blocked():
            # 搜索页就被拦：旧实现只留一句「搜索未跳转，跳过该关键词」静默往下走，
            # 界面表现为「关键词进度一直涨、数据一条不动」。现在先叫看门狗，
            # 人工过完验证自动重搜一次。
            if self.handle_http_block(SEARCH_HOME) and not self._stop_requested:
                base = self._ui_search(kw)
        if not base:
            self._log(f"  「{kw}」搜索未跳转到结果页，跳过该关键词")
            return
        self._log(f"  「{kw}」结果页：{base}")
        empty = stale = 0
        page = 1
        while not self._stop_requested and (self.max_pages <= 0 or page <= self.max_pages):
            while self._pause_requested and not self._stop_requested:
                time.sleep(0.5)
            if page > 1:
                url = f"{base}?p={page}"
                if not self._goto(url, expect_domain="gys.cn"):
                    return
            html = self._html()
            if self._gys_blocked():
                # 结果页停在插页/验证：绝不能按「本页无公司」收掉这个关键词（那会把
                # 整批关键词在无人察觉的空跑里耗光），等验证过去后重取本页。
                if not self.handle_http_block(self._current_url() or base):
                    return
                if not self._goto(base if page == 1 else f"{base}?p={page}",
                                  expect_domain="gys.cn"):
                    return
                html = self._html()
            entries = self.parse_list(html, kw)
            self.stat_pages += 1
            self.stat_listed += len(entries)
            if not entries:
                empty += 1
                if empty >= 1:
                    self._log(f"  「{kw}」第 {page} 页无公司结果，停止")
                    return
            else:
                empty = 0
            new = skipped = 0
            for e in entries:
                mid = e["公司ID"]
                key = f"{self.PLATFORM_SLUG}:{mid}"
                name = self._norm_name(e["公司名称"])
                nkey = f"{self.PLATFORM_SLUG}:name:{name}"
                if key in self._seen_members or nkey in self._seen_members \
                        or f"{self.PLATFORM_SLUG}:{kw}#{mid}" in self._seen_members:
                    continue
                self._seen_members.add(key)
                self._seen_members.add(nkey)
                if not self.is_relevant(kw, self.stub_relevance_text(e)):
                    skipped += 1
                    self._early_skipped += 1
                    self._commit_to_store({"状态": "类目不符(已过滤)", "关键词": kw,
                                           "公司名称": e["公司名称"], "memberId": mid})
                    continue
                e["关键词"] = kw
                new += 1
                yield e
            self._log(f"  「{kw}」第 {page} 页：{len(entries)} 家，新增 {new}"
                      + (f"，早过滤 {skipped}" if skipped else ""))
            if new == 0:
                stale += 1
                if stale >= 1:
                    return
            else:
                stale = 0
            # 有更大页码的分页链接才继续（正则兼容 ?p=/&p=/;p= 等写法，
            # 比子串判据健壮；主保护仍是上面「新增 0 家即枯竭」的内容型早停）。
            if not any(int(n) > page for n in re.findall(r'[?&;]p=(\d+)', html)):
                return
            page += 1
            self._sleep()

    # ── 详情 ───────────────────────────────────────────────────
    def _is_abnormal_page(self):
        """当前标签是否停在限流/拦截插页（标题特征，不走基类 SAFE_TITLE_HINTS 反判）。"""
        t = self._eval("document.title") or ""
        return any(m in t for m in self.ABNORMAL_TITLE_MARKS)

    def _http_get(self, url, tries=2):
        """纯 HTTP 取 gys 静态子域页（实测未被限流时商铺页/联系页都能直取，
        比浏览器快且不占标签）。只识浏览器 UA + 低频，失败返回空串由调用方回退浏览器，
        绝不把网络抖动当「无数据」；连续失败则放弃快路（见 _http_disabled）。
        防采/限流插页（520 等）单独记账：不重试、不当普通失败，交看门狗。"""
        if self._http_disabled:
            return ""
        last = ""
        for i in range(max(1, tries)):
            try:
                r = requests.get(url, headers=GYS_HTTP_HEADERS, timeout=25)
                self._mark_activity()
                block = self.http_block_reason(r.status_code, r.text, url)
                if block:
                    self._block_reason = block
                    return ""
                if r.status_code == 200 and r.text:
                    r.encoding = r.apparent_encoding or "utf-8"
                    self._http_fails = 0
                    self._block_reason = ""
                    return r.text
                last = f"HTTP {r.status_code}"
            except Exception as e:
                last = str(e).splitlines()[0][:80]
            if i < tries - 1:
                time.sleep(1.5 * (i + 1))
        self._http_fails += 1
        if self._http_fails >= 3 and not self._http_disabled:
            self._http_disabled = True
            self._log("  ⚠ gys 连续取页失败，本轮改走浏览器渲染（不再走纯 HTTP）")
        self._log(f"  ⚠ gys HTTP 取页失败（{last}）：{url[:70]}")
        return ""

    @staticmethod
    def _html_abnormal(html):
        """HTTP 取回的页是否限流插页/空壳：标题命中异常词，或体积远小于正常商铺页。"""
        if not html or len(html) < 3000:
            return True
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
        t = (m.group(1) if m else "").strip()
        return any(x in t for x in GysCrawler.ABNORMAL_TITLE_MARKS) \
            or any(x in t for x in ("访问异常", "安全验证", "禁止访问"))

    def _fetch_raw(self, url):
        """取一页 gys 内容（不过看门狗）：HTTP 优先，被拦/失败再回退共享 Chrome。"""
        html = self._http_get(url)
        if html and not self._html_abnormal(html):
            self._block_reason = ""
            return html, "http"
        if self._goto(url, expect_domain="gys.cn") and not self._is_abnormal_page():
            rendered = self._html()
            if rendered and len(rendered) >= 1500:
                self._block_reason = ""
                return rendered, "browser"
        if self._is_abnormal_page():
            # 浏览器渲染也停在插页：记下原因，让上层知道这不是「这家没数据」
            self._block_reason = self._block_reason or "供应商网插页/验证（浏览器渲染同样被拦）"
            self._block_probe_url = url
        return "", ""

    def _fetch_page(self, url):
        """取一页 gys 内容，被拦时交给防采看门狗：弹提醒、等人工过验证/IP 冷却，
        探到恢复后自动重试同一 URL。旧实现只是返空→记一条「详情页访问异常(待重试)」
        就往下跑，验证码根本不弹提醒，整轮就耗在插页里空转。"""
        html, src = self._fetch_raw(url)
        if html:
            return html, src
        if self._gys_blocked():
            if self.handle_http_block(url):
                html, src = self._fetch_raw(url)
                if html:
                    return html, src
            else:
                self._log(f"  ⚠ {self.PLATFORM_NAME}未能恢复，该公司记为待重试（不写终态）")
        return "", ""

    def crawl_detail(self, stub):
        rec = self._base_rec(stub)
        sub = stub.get("公司ID") or stub.get("memberId")
        url = stub.get("详情URL") or f"https://{sub}.gys.cn/"
        html, _src = self._fetch_page(url)
        if not html:
            # 非终态（含「异常/失败」）：不入种子库，换关键词/下次重跑自动补采
            rec["状态"] = "详情页访问异常(待重试)"
            return rec
        data = self._parse_company_page(html, sub)
        self._merge_rec(rec, data)
        # 详情页标题里的公司名更规范，优先覆盖列表锚文本
        if data.get("公司名称"):
            rec["公司名称"] = data["公司名称"]
        # 联系人/号码/邮箱任一缺失 → 补访专用联系页（实测商铺页只放手机，而联系页
        # 明文多出「联系人：朱先生 + 职位 + 固话 + 邮箱」，不补访就白丢联系人）。
        if not (rec.get("联系人") and (rec.get("电话") or rec.get("手机") or rec.get("邮箱"))):
            alt = self._find_contact_url(html, sub) or f"https://{sub}.gys.cn/contact-information/"
            self._sleep()
            more_html, _ = self._fetch_page(alt)
            if more_html:
                more = self._parse_company_page(more_html, sub)
                self._merge_rec(rec, more)
                if rec.get("联系人") or rec.get("邮箱"):
                    rec["详情URL"] = alt
        if not rec.get("公司名称") or rec.get("公司名称") == sub:
            rec["公司名称"] = stub.get("公司名称", "")
        # 详情页复核相关性（弱信号 fail-open，避免仅法人名可比时误杀）
        if not self.detail_relevant(rec):
            rec["状态"] = "类目不符(已过滤)"
            return rec
        has_phone = bool(rec.get("电话") or rec.get("手机"))
        rec["状态"] = ("成功" if (has_phone or rec.get("邮箱"))
                       else ("无电话(未公开)" if (rec.get("地址") or rec.get("公司网址")) else "无联系方式"))
        return rec

    @staticmethod
    def _merge_rec(rec, data):
        """只补空缺、不覆写已有值（多页取源时首页优先）。"""
        for k, v in data.items():
            if v and not rec.get(k):
                rec[k] = v

    # 页内 schema.org 结构化数据（Google 官方标准，SEO 站服务端注水、不受 UI 隐藏影响）
    _LD_JSON_RE = re.compile(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)

    def _parse_ld_json(self, html):
        """从 <script type="application/ld+json"> 提取 Organization 权威字段：
        telephone / address / knowsAbout(主营) / name。解析失败只丢增强不影响主流程。"""
        out = {}
        for m in self._LD_JSON_RE.finditer(html or ""):
            try:
                node = json.loads(m.group(1).strip())
            except Exception:
                continue
            nodes = node if isinstance(node, list) else [node]
            for n in nodes:
                if not isinstance(n, dict):
                    continue
                orgs = n.get("@graph") if isinstance(n.get("@graph"), list) else [n]
                for org in orgs:
                    if not isinstance(org, dict) or org.get("@type") not in (
                            "Organization", "LocalBusiness", "Corporation"):
                        continue
                    tel = re.sub(r"[^\d\-]", "", str(org.get("telephone") or ""))
                    if len(tel) >= 7 and tel.strip("-"):
                        out.setdefault("电话", tel)
                    addr = org.get("address") or {}
                    if isinstance(addr, dict):
                        full = " ".join(x for x in (
                            str(addr.get("addressRegion") or ""), str(addr.get("addressLocality") or ""),
                            str(addr.get("streetAddress") or "")) if x.strip())
                        if full.strip():
                            out.setdefault("地址", clean_address(full))
                    elif isinstance(addr, str) and addr.strip():
                        out.setdefault("地址", clean_address(addr))
                    ka = org.get("knowsAbout")
                    if isinstance(ka, list) and ka:
                        out.setdefault("主营产品", "、".join(str(x) for x in ka if x)[:200])
                    elif isinstance(ka, str) and ka.strip():
                        out.setdefault("主营产品", ka[:200])
                    if org.get("name"):
                        out.setdefault("公司名称", str(org["name"])[:60])
        return out

    @staticmethod
    def _find_contact_url(html, sub):
        """从公司子域首页找「联系我们/联系方式」子页链接。"""
        m = re.search(r'href="([^"]*(?:contact|lianxi|about)[^"]*)"[^>]*>[^<]*(?:联系|关于)', html, re.I)
        if m:
            href = m.group(1)
            if href.startswith("//"):
                return "https:" + href
            if href.startswith("/"):
                return f"https://{sub}.gys.cn" + href
            if href.startswith("http"):
                return href
        return ""

    @staticmethod
    def _norm_label(text):
        """表单标签归一：gys 用全角空格/&nbsp; 对齐成「联 系 人：」「职   位：」，
        在原始文本上逐行匹配必然错开，先把空白与冒号全剥掉再查表。"""
        s = (text or "").replace("\u3000", "").replace("&nbsp;", "").replace("\xa0", "")
        s = re.sub(r"\s+", "", s)
        return s.strip("：:/｜| ").lower()

    def _parse_contact_dom(self, soup):
        """结构化提取联系方式：gys 把标签和值拆在两个节点里——商铺页
        `<label>联系人：</label><span><em>朱先生</em>` 与 `<dt>联系人：</dt><dd>朱先生</dd>`，
        联系页则是 `<div class="col-1">电 话</div><div class="col-2">…`。实测 12 家商铺
        DOM 里 11 家有联系人，而旧的逐行正则 0/12 命中——只认这种拆节点写法。"""
        out = {}

        def put(field, value):
            value = (str(value) if value is not None else "").strip()
            if value and field not in out:
                out[field] = value

        for el in soup.find_all(["dt", "label", "th", "div", "span", "p", "td"]):
            lab = self._norm_label(el.get_text(strip=True))
            if not lab or lab in _SKIP_LABELS:
                continue
            field = _LABEL_FIELD.get(lab)
            if not field:
                continue
            sib = el.find_next_sibling(["dd", "td", "span", "div", "p", "em", "a"])
            if sib is None and el.parent is not None:
                sib = el.parent.find_next_sibling(["dd", "td", "span", "div", "p", "em"])
            if sib is not None:
                put(field, sib.get_text(" ", strip=True))
        for cls, field in _CLASS_FIELD.items():
            el = soup.find(class_=cls)
            if el is not None:
                put(field, el.get_text(" ", strip=True))
        return out

    def _dom_to_fields(self, dom):
        """DOM 原始标签值 → 统一记录字段：联系人拆岗位、号码拆手机/固话并滤掉占位假号。"""
        data = {}
        contact, position = parse_contact_field(dom.get("联系人", ""))
        if contact:
            data["联系人"] = contact
        # gys 把岗位拆在两处：联系区块 <div class="position">（只写「销售」「市场」这种
        # 部门词）与联系人名字后缀（「王经理」）。只取一边就永远只能入库个裸「经理」
        # 或一个部门词，合成后才是「销售经理」这种能直接拿来排话术的完整岗位。
        merged = compose_position(dom.get("岗位", ""), position)
        if merged and merged not in ("联系人", "无"):
            data["岗位"] = merged[:20]
        # 号码：商铺页/联系页都把手号同时写在「电话」行，先归一成形再按属性分流
        phones = []
        for key in ("手机", "电话"):
            raw = dom.get(key, "")
            if not raw or is_placeholder_number(raw):
                continue
            digits = re.sub(r"[^\d]", "", raw)
            mm = PHONE_MOBILE_RE.search(re.sub(r"(\d)\s+(?=\d)", r"\1", raw))
            if mm and not is_placeholder_number(mm.group()):
                phones.append(("手机", mm.group()))
            elif re.fullmatch(r"0?\d{2,4}[-\s]?\d{7,8}", raw.strip()) or len(digits) in (10, 11, 12):
                phones.append(("电话", raw.strip()))
        for field, val in phones:
            data.setdefault(field, val)
        if data.get("电话") and data.get("电话") == data.get("手机"):
            data.pop("电话")      # 同一只手号写两行，不重复入库
        for key, field in (("邮箱", "邮箱"), ("地址", "地址"), ("所在地区", "所在地区"),
                           ("经营模式", "经营模式"), ("主营产品", "主营产品")):
            val = dom.get(key, "")
            if not val:
                continue
            if field == "邮箱":
                em = EMAIL_RE.search(val)
                if em and not _is_platform_email(em.group()):
                    data["邮箱"] = em.group()
            elif field == "地址":
                addr = clean_address(val)
                if len(addr) > 4:
                    data["地址"] = addr[:120]
            else:
                data[field] = normalize_spaces(val)[:200 if field == "主营产品" else 30]
        return data

    def _parse_company_page(self, html, subdomain):
        """复用 scrape_gys.py 生产验证过的公司页解析（逐行配对 + 全页号码/邮箱兜底）。
        映射到统一记录字段：电话拆成手机/固话，详细地址→地址。"""
        data = {"公司子域名": f"{subdomain}.gys.cn",
                "公司网址": f"https://{subdomain}.gys.cn/"}
        soup = BeautifulSoup(html, 'lxml')
        # 结构化联系方式优先（标签与值拆在两个节点里，逐行正则根取不到）
        for k, v in self._dom_to_fields(self._parse_contact_dom(soup)).items():
            data[k] = v

        title = soup.find('title')
        if title:
            # 标题形如「公司名（光伏支架厂家）-主营A、B、C | 供应商网」：名字段与主营段
            # 顺序不固定，故逐段扫描而不是找到名字就 break（否则主营段永远扫不到）。
            parts = [p.strip() for p in re.split(r'[-_|—]', title.get_text(strip=True))]
            for part in parts:
                if not data.get("主营产品") and part.startswith("主营"):
                    prod = re.sub(r'^主营(?:产品|业务)?[：:\s]*', '', part).strip()
                    if len(prod) > 3:
                        data["主营产品"] = prod[:200]
                elif (not data.get("公司名称")
                        and any(k in part for k in ['公司', '厂', '集团', '有限']) and len(part) > 4):
                    # gys 的 SEO 标题就是「公司名（光伏支架厂家）」，不剥会把营销尾巴当名字入库
                    data["公司名称"] = strip_name_marketing_tail(part)[:60]

        text = soup.get_text(separator='\n', strip=True)
        lines = [l.strip() for l in text.split('\n') if l.strip()]

        phone_raw = ''
        for line in lines:
            # 联系人：容忍「联 系 人」空格写法与「姓名：」标签（旧正则不容忍导致
            # gys 大量记录联系人为空）；拆岗位、保留称谓（「朱先生」比「朱」能用）。
            m = re.match(r'(?:联系人|联\s*系\s*人|姓\s*名)[：:\s]+(.+)', line)
            if m and not data.get("联系人"):
                val, pos = parse_contact_field(m.group(1))
                if val:
                    data["联系人"] = val
                if pos and not data.get("岗位"):
                    data["岗位"] = pos[:20]
            for pat in [r'手\s*机[：:\s]+([\d\s-]+)', r'电\s*话[：:\s]+([\d\s-]+)',
                        r'联系电话[：:\s]+([\d\s-]+)']:
                m = re.search(pat, line)
                if m and not phone_raw:
                    p = re.sub(r'\s+', '', m.group(1))
                    if PHONE_MOBILE_RE.match(p) or re.match(r'0?\d{2,4}-?\d{7,8}', p):
                        phone_raw = p
            m = re.search(r'(?:邮\s*箱|E-?mail)[：:\s]+([\w.+-]+@[\w-]+\.[\w.]+)', line, re.I)
            if m and not data.get("邮箱") and not _is_platform_email(m.group(1)):
                data["邮箱"] = m.group(1)
            for pat in [r'地\s*址[：:\s]+(.+)', r'详细地址[：:\s]+(.+)', r'所在地区[：:\s]+(.+)']:
                m = re.search(pat, line)
                if m and not data.get("地址"):
                    val = clean_address(m.group(1).strip())
                    if 5 < len(val) < 150:
                        data["地址"] = val[:120]
            m = re.match(r'经营模式[：:\s]+(.+)', line)
            if m and not data.get("经营模式"):
                data["经营模式"] = m.group(1).strip()[:30]
            m = re.match(r'主营产品[：:\s]+(.+)', line)
            if m and not data.get("主营产品"):
                data["主营产品"] = m.group(1).strip()[:200]

        # 全页号码兜底（排除平台自身号码）——必须在**可见文本**上跑，
        # 在原始 html 上跑会把 script/属性里的时间戳、跟踪 ID 等数字串误当号码。
        if not phone_raw:
            mob = [p for p in PHONE_MOBILE_RE.findall(text) if p not in GLOBAL_EXCLUDED_PHONES]
            if mob:
                phone_raw = mob[0]
            else:
                tel = PHONE_TEL_RE.findall(text)
                if tel:
                    phone_raw = tel[0]
        # 拆分手机 / 固话（DOM 已取到号码时不覆写，只补另一类）
        if phone_raw and not is_placeholder_number(phone_raw):
            digits = re.sub(r'[^\d-]', '', phone_raw)
            if PHONE_MOBILE_RE.search(digits):
                data.setdefault("手机", PHONE_MOBILE_RE.search(digits).group())
            else:
                data.setdefault("电话", digits)
        # schema.org JSON-LD 权威字段兜底：商铺 UI 把号码藏成「登录后可见」时，
        # 页内注水的 Organization.telephone 仍明文直出（实测 gdjt88 → 18923274073）。
        ld = self._parse_ld_json(html)
        if ld.get("电话") and not (data.get("手机") or data.get("电话")):
            ld_digits = re.sub(r"[^\d-]", "", ld["电话"])
            if PHONE_MOBILE_RE.search(ld_digits):
                data["手机"] = PHONE_MOBILE_RE.search(ld_digits).group()
            else:
                data["电话"] = ld_digits
        if ld.get("地址") and not data.get("地址"):
            data["地址"] = ld["地址"]
        if ld.get("主营产品") and not data.get("主营产品"):
            data["主营产品"] = ld["主营产品"]
        if ld.get("公司名称") and not data.get("公司名称"):
            data["公司名称"] = ld["公司名称"]
        if not data.get("邮箱"):
            emails = [e for e in EMAIL_RE.findall(html)
                      if not _is_platform_email(e)
                      and not re.search(r'\.(png|jpe?g|gif|webp|svg|ico)$', e, re.I)]
            if emails:
                data["邮箱"] = emails[0]
        # 工商段里的法定代表人（实测「注册地位于…，法定代表人为杨兵」这类公司简介明文直出）：
        # 没有显式联系人时它是唯一能把电话打到真人手里的入口，口径由清洗层把关。
        if not data.get("法定代表人"):
            lr = extract_legal_rep(text)
            if lr:
                data["法定代表人"] = lr
        return data
