"""mic_crawler.py —— 中国制造网(made-in-china.com) 中文联系方式采集器。

本轮实机重新校准（推翻旧「英文版 products-search + contactinfo not available」链路）：
  · ✅ 中文公司搜索主入口（本轮浏览器实机证实，非纯 HTTP——HTTP 会 302 反爬）：
    https://cn.made-in-china.com/companysearch.do?subaction=hunt&style=b&mode=and
    &code=0&order=0&searchType=0&page=N&newCn=1&word=<GBK 百分号编码>&comProvince=nolimit
    ——**关键：word 必须按 GBK 编码**（CN 站是 GBK，UTF-8 提交会变乱码→查无结果，
    这正是旧版「MIC 第 1 页起无结果」的真因）。渲染后服务端直出公司卡片，每张含：
    `<label class="co-name"><a href="https://<user>.cn.made-in-china.com" title="中文公司名">`
    与「主营产品：… 经营模式：…」文本，实测每页 30 家、page=N 分页有效（第 1/2 页 0 重叠），
    并会把 .do 重写成 SEO 的 <token>-gongsi-N.html（用 .do+page 参数稳定复现即可）。
  · ✅ 真正的联系方式入口（用户实机指路，已证实）：
    https://cn.made-in-china.com/showroom/<user>-contact.html —— 页面 **GBK**，
    中文公司名/联系人/电话以**明文**写在 <meta name="Description"> 里
    （形如「中国制造网，青岛圣孚莱新能源有限公司，联系人：steven，联系电话：18653250688」），
    无需再点「查看电话号码」。这修正了旧版误判「MIC 不公开联系方式」的结论。

为何全程走浏览器而非纯 HTTP：搜索页与 contact 页对**无会话**的 requests 都会迅速 302 到
captcha.cn.made-in-china.com（实测冷启动首条 200、随后整批全拦）。而本工具用**同一个
人过的共享 Chrome**：命中验证码时由基类的人在环机制暂停、等人工过一次验证后，会话
cookie 即打通，后续搜索翻页与所有 <user>-contact.html 直接 200——既过防采又拿全中文数据。

相关性：companysearch 卡片自带「主营产品」，列表阶段即可用产品信号精准早过滤（关键词
不符的如「展览公司」会被正确过滤，命中产品的绝不误杀），无需再依赖公司名二字组。
"""
import re
import time
import html as _htmlmod
import requests
from urllib.parse import quote

from cdp_base import (CdpSiteCrawler, PHONE_MOBILE_RE, PHONE_TEL_RE, EMAIL_RE, clean_address,
                      parse_contact_field, dept_only)

# 中文公司搜索（word 需 GBK 百分号编码；page 分页；须用浏览器过验证码）
COMPANY_SEARCH_URL = (
    "https://cn.made-in-china.com/companysearch.do?subaction=hunt&style=b&mode=and"
    "&code=0&order=0&searchType=0&page={page}&newCn=1&word={kw}&comProvince=nolimit")
# 中文联系方式主入口（GBK 页面，明文电话在 meta Description）
CONTACT_URL = "https://cn.made-in-china.com/showroom/{user}-contact.html"

# companysearch 卡片：class="co-name" 锚点里取子域主机 + 中文公司名；正文取主营产品
_CO_NAME_ANCHOR = re.compile(
    r'<a href="https?://([a-z0-9][a-z0-9\-]{1,40})\.((?:cn|en)\.made-in-china\.com)"'
    r'[^>]*title="([^"]+)"', re.I)
_MAIN_PROD_RE = re.compile(r'主\s*营\s*产品[：:]\s*(.+?)\s*(?:经营模式|身份认证|>|$)', re.S)

# 供应商子域：捕获完整主机（cn 或 en 后缀），详情 URL 用实际捕获到的主机，避免硬编码 .cn
SUBDOMAIN_RE = re.compile(r'https?://([a-z0-9][a-z0-9\-]{1,40})\.((?:cn|en)\.made-in-china\.com)', re.I)
EXCLUDED_SUBS = {'www', 'cn', 'en', 'm', 'static', 'res', 'search', 'login', 'member',
                 'membercenter', 'my', 'api', 'image', 'images', 'img', 'css', 'js', 'b2b',
                 'news', 'so', 'caigou', 'zhanhui', 'i', 'photo', 'photos', 'video', 'sale',
                 'hot', 'activity', 'passport', 'account', 'service', 'help', 'about',
                 'captcha', 'errordocs'}

# 公司名判据（用于从产品锚文本里挑出更像公司名的候选）
COMPANY_MARKS = ('公司', '厂', '集团', '有限', '实业', '企业', 'Co.', 'Co,', 'Ltd',
                 'Corp', 'Inc', 'Limited', 'Company')

REGION_MAP = {
    '北京': '北京市', '天津': '天津市', '上海': '上海市', '重庆': '重庆市',
    '河北': '河北省', '山西': '山西省', '辽宁': '辽宁省', '吉林': '吉林省',
    '黑龙江': '黑龙江省', '江苏': '江苏省', '浙江': '浙江省', '安徽': '安徽省',
    '福建': '福建省', '江西': '江西省', '山东': '山东省', '河南': '河南省',
    '湖北': '湖北省', '湖南': '湖南省', '广东': '广东省', '海南': '海南省',
    '四川': '四川省', '贵州': '贵州省', '云南': '云南省', '陕西': '陕西省',
    '甘肃': '甘肃省', '青海': '青海省', '台湾': '台湾省', '内蒙古': '内蒙古自治区',
    '广西': '广西壮族自治区', '西藏': '西藏自治区', '宁夏': '宁夏回族自治区',
    '新疆': '新疆维吾尔自治区',
}

# meta Description 明文：中国制造网，{公司名}，联系人：{X}，联系电话：{号码}
_DESC_RE = re.compile(r'name=["\']Description["\'][^>]+content=["\']([^"\']+)["\']', re.I)
_DESC_FIELD_RE = re.compile(
    r'中国制造网[，,]\s*(?P<name>.+?)[，,]\s*联系人[：:]\s*(?P<contact>[^，,]+)'
    r'[，,]\s*联系电话[：:]\s*(?P<phone>[\d][\d\-+()\s]{5,24})')


def _is_company_name(t):
    return bool(t) and any(x in t for x in COMPANY_MARKS)


def extract_region(address, card_text=''):
    for src in (address, card_text):
        if not src:
            continue
        for prov, full in REGION_MAP.items():
            if prov in src:
                idx = src.index(prov) + len(prov)
                city_m = re.search(r'[\u4e00-\u9fff]{2,4}(?:市|县|区|镇)', src[idx:])
                return f"{full} {city_m.group(0)}" if city_m else full
        cm = re.search(r'([\u4e00-\u9fff]{2,4}市)', src)
        if cm:
            return cm.group(1)
    return ""


def _classify_phone(raw):
    """把 meta Description 里的联系电话按位数特征拆成手机/固话两类字符串。"""
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        return "", ""
    if re.fullmatch(r"1[3-9]\d{9}", digits):
        return digits, ""
    if len(digits) >= 7:
        return "", digits
    return "", ""


class MicCrawler(CdpSiteCrawler):
    PLATFORM_NAME = "中国制造网"
    PLATFORM_SLUG = "mic"
    # 标签首页打开中文版，避免用户看到「为什么是英文版」
    BASE_URL = "https://cn.made-in-china.com/"
    HOME_DOMAIN = "made-in-china.com"
    BLOCK_URL_MARKERS = ("captcha", "verify", "verification", "antispam", "/punish")
    SAFE_TITLE_HINTS = ("中国制造网", "Made-in-China", "Wholesale", "products",
                        "公司", "供应商", "制造商", "厂家", "联系方式")
    MIN_PAGE_CHARS = 800

    def __init__(self, port=9255):
        super().__init__(port)
        # MIC 验证码极敏感：低频是防封第一原则
        self.delay_between = 6.0
        self.jitter = 3.0
        self.max_pages = 0          # 不限页数：一直翻到没数据再切下一个关键词
        # 会话 Cookie 复用裸 HTTP（实机探针取证：浏览器过验证后，用同一 cookie 直接
        # GET contact 页 15 家串行 3.2s、10/15 家 200 且 meta Description 明文中文
        # 联系方式；少数 user 仍 302 到 captcha）：详情主走此通道，
        # 命中 302/异常自动回落浏览器标签，并适时重取 cookie。
        self._http = None
        self._http_at = 0.0
        self._http_miss = 0

    # ── 列表 ───────────────────────────────────────────────────
    @staticmethod
    def _gbk_quote(kw):
        """CN 站 word 必须按 GBK 百分号编码（UTF-8 提交会变乱码→查无结果）。"""
        try:
            return quote(str(kw).encode("gbk"))
        except UnicodeEncodeError:
            return quote(str(kw).encode("gbk", "ignore"))

    def build_search_url(self, kw, page):
        return COMPANY_SEARCH_URL.format(page=page, kw=self._gbk_quote(kw))

    def parse_list(self, html, kw):
        """公司搜索页：从 class="co-name" 锚点逐张卡片取子域/主机/中文公司名 + 主营产品。
        每段以 class="co-name" 切分；段首即该公司名称锚点，其后紧跟主营产品文本。"""
        out, seen = [], set()
        for chunk in (html or "").split('class="co-name"')[1:]:
            m = _CO_NAME_ANCHOR.search(chunk)
            if not m:
                continue
            user = m.group(1).lower().strip("-")
            host = m.group(2).lower()
            if user in EXCLUDED_SUBS or user in seen:
                continue
            name = _htmlmod.unescape(m.group(3)).strip()
            head = re.sub(r'<[^>]+>', ' ', chunk[:1500])
            pm = _MAIN_PROD_RE.search(head)
            prod = _htmlmod.unescape(re.sub(r'\s+', ' ', pm.group(1))).strip()[:120] if pm else ""
            seen.add(user)
            out.append(self._card_stub(user, host, name or user, prod))
        if out:
            return out
        # 兜底：卡片结构变动时整页正则扫子域（仍保持 fail-open）
        for m in SUBDOMAIN_RE.finditer(html or ""):
            user = m.group(1).lower()
            host = m.group(2).lower()
            if user in EXCLUDED_SUBS or user in seen:
                continue
            seen.add(user)
            out.append(self._card_stub(user, host, user, ""))
        return out

    def _card_stub(self, user, host, name, prod=""):
        home = f"https://{user}.{host}/"
        return {
            "公司名称": name[:60],
            "memberId": user,
            "公司ID": user,
            "用户名": user,
            "主机": host,
            "公司网址": home,
            "详情URL": CONTACT_URL.format(user=user),
            "地址": "",
            "主营产品": prod,
            "经营模式": "",
            "所在区域": "",
            "标题": name[:80],
        }

    def list_has_more(self, html, page):
        """只要有「下一页」就一直翻（不限页数，翻到没数据自然停）。"""
        return bool(html) and "下一页" in html

    # ── 详情：主走 CN showroom contact 页（明文中文电话）─────────
    def crawl_detail(self, stub):
        rec = self._base_rec(stub)
        user = stub.get("用户名") or stub.get("memberId")
        host = stub.get("主机") or "en.made-in-china.com"
        if not user:
            rec["状态"] = "详情页导航失败"
            return rec

        # 1) 会话 Cookie 复用裸 HTTP（~0.2s/家，不占浏览器标签）
        got = self._fetch_contact_http(user, rec)

        # 2) 回落：裸 HTTP 未命中(302/无明文)时才用浏览器标签导航
        if not got or not (rec.get("手机") or rec.get("电话") or rec.get("邮箱")):
            self._sleep()
            self._fetch_contact_page(user, rec)

        # 3) 再回退：仍无联系方式时进英文子域首页兜底
        if not (rec.get("手机") or rec.get("电话") or rec.get("邮箱")):
            self._sleep()
            self._fallback_en(user, host, rec)

        # 公司名兜底（仍未拿到中文名的情况下用列表名）
        if not rec.get("公司名称") or rec.get("公司名称") == user:
            list_name = (stub.get("公司名称") or "").strip()
            rec["公司名称"] = list_name if _is_company_name(list_name) else (rec.get("公司名称") or list_name)

        if not rec.get("所在区域"):
            rec["所在区域"] = extract_region(rec.get("地址", ""), rec.get("主营产品", ""))
        if not rec.get("公司网址"):
            rec["公司网址"] = f"https://{user}.{host}/"

        if not self.detail_relevant(rec):
            rec["状态"] = "类目不符(已过滤)"
            return rec
        has_phone = bool(rec.get("电话") or rec.get("手机"))
        rec["状态"] = ("成功" if (has_phone or rec.get("邮箱"))
                       else ("无电话(未公开)" if (rec.get("地址") or rec.get("公司网址")) else "无联系方式"))
        return rec

    # ── 会话 Cookie 复用裸 HTTP ──────────────────────────────
    def _ensure_http(self):
        """从共享 Chrome 取 made-in-china.com 的 cookie（含 httpOnly，CDP
        Storage.getCookies）建裸 HTTP 会话。10 分钟内复用，超期重取。"""
        now = time.time()
        if self._http is not None and now - self._http_at < 600:
            return self._http
        try:
            resp = self._send("Storage.getCookies", timeout=10)
            cookies = (resp or {}).get("result", {}).get("cookies", [])
            mic_ck = [c for c in cookies if "made-in-china.com" in (c.get("domain") or "")]
            if not mic_ck:
                return None
            ua = self._eval("navigator.userAgent") or (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
            s = requests.Session()
            s.headers.update({"User-Agent": ua, "Accept-Language": "zh-CN,zh;q=0.9",
                              "Referer": "https://cn.made-in-china.com/"})
            s.cookies.clear()
            for c in mic_ck:
                try:
                    s.cookies.set(c["name"], c["value"], domain=c.get("domain"), path=c.get("path") or "/")
                except Exception:
                    pass
            self._http, self._http_at, self._http_miss = s, now, 0
            return s
        except Exception:
            self._http = None
            return None

    def _drop_http(self):
        """命中 302/验证：丢弃当前 cookie 会话，后续家改走浏览器标签，
        下次重建时自然带上人工过验证后的新 cookie。"""
        self._http = None

    def _fetch_contact_http(self, user, rec):
        """裸 HTTP GET contact 页（GBK）。200 且解析到明文→True；
        302/非200/异常→False，由调用方回落浏览器。"""
        if not self.pin_tab_id:
            return False
        s = self._ensure_http()
        if s is None:
            return False
        url = CONTACT_URL.format(user=user)
        try:
            r = s.get(url, timeout=20, allow_redirects=False)
            self._mark_activity()
            if r.status_code in (301, 302, 303, 307):
                loc = (r.headers.get("Location") or "").lower()
                if "captcha" in loc or "verification" in loc:
                    self._http_miss += 1
                    if self._http_miss >= 3:      # 连坐失败＝cookie 已失效，重建会话
                        self._drop_http()
                return False
            if r.status_code != 200 or not r.text:
                return False
            r.encoding = "gbk"
            self._parse_contact(r.text, rec)
            self._http_miss = 0
            rec["详情URL"] = url
            return True
        except Exception:
            self._drop_http()
            return False

    def _fetch_contact_page(self, user, rec):
        """导航 CN showroom contact 页，解析 meta Description 明文联系方式。
        返回取到的 HTML（供上层兜底判断）；被 404/未就绪时返回空串。"""
        url = CONTACT_URL.format(user=user)
        if not self._goto(url, expect_domain="made-in-china.com", tries=2):
            rec["状态"] = "详情页导航失败"
            return ""
        cur = (self._current_url() or "").lower()
        # 该供应商无中文 showroom：302 到 404 文档页（留在 made-in-china.com 域内，不算拦截）
        if "errordocs" in cur or "404" in cur:
            return ""
        html = self._html()
        self._parse_contact(html, rec)
        rec["详情URL"] = url
        return html

    def _parse_contact(self, html, rec):
        """从渲染后的 contact 页取中文公司名/联系人/电话（meta Description 明文）+ 地址/主营。"""
        if not html:
            return
        m = _DESC_RE.search(html)
        desc = _htmlmod.unescape(m.group(1)) if m else ""
        if desc:
            fm = _DESC_FIELD_RE.search(desc)
            if fm:
                cn_name = fm.group("name").strip()[:60]
                cur = rec.get("公司名称") or ""
                # 中文 meta 公司名优先覆盖列表阶段的英文/子域占位名（无中文才覆盖）
                if cn_name and (not cur or not re.search(r'[\u4e00-\u9fa5]', cur)):
                    rec["公司名称"] = cn_name
                if not rec.get("联系人"):
                    # 统一走共用口径：保留「杨经理」这类可读称呼、拆出岗位，
                    # 并拦住把号码/占位词写进联系人列的脏值
                    name, pos = parse_contact_field(fm.group("contact"))
                    if name:
                        rec["联系人"] = name
                    if pos and not rec.get("岗位"):
                        rec["岗位"] = pos[:20]
                    # mic 商家把部门直接填进联系人框（实机取证 scjxb88
                    # 「联系人：客服部」）：名字作废，部门归进岗位列——本页没有
                    # 明写职位的字段，这是 mic 岗位覆盖率(5.7%)唯一不臆造的增量。
                    if not name and not rec.get("岗位"):
                        d = dept_only(fm.group("contact"))
                        if d:
                            rec["岗位"] = d[:20]
                mobile, tel = _classify_phone(fm.group("phone"))
                if mobile and not rec.get("手机"):
                    rec["手机"] = mobile
                elif tel and not rec.get("电话"):
                    rec["电话"] = tel

        visible = self._visible_text(html)
        # 名称兜底：<title>「点击查看{公司名}联系方式」
        if not rec.get("公司名称"):
            tm = re.search(r'点击查看\s*(.{2,40}?(?:公司|厂|集团|企业))', visible)
            if tm:
                rec["公司名称"] = tm.group(1).strip()[:60]
        # 联系人兜底
        if not rec.get("联系人"):
            cm = re.search(r'联系人[：:]\s*([\u4e00-\u9fa5A-Za-z]{2,10})', visible)
            if cm:
                name, pos = parse_contact_field(cm.group(1))
                if name:
                    rec["联系人"] = name
                if pos and not rec.get("岗位"):
                    rec["岗位"] = pos[:20]
                if not name and not rec.get("岗位"):
                    d = dept_only(cm.group(1))
                    if d:
                        rec["岗位"] = d[:20]
        # 号码兜底（可见文本，按个数截断，绝不按字符截断）
        if not rec.get("手机"):
            mob = PHONE_MOBILE_RE.findall(visible)
            if mob:
                rec["手机"] = " / ".join(list(dict.fromkeys(mob))[:3])
        if not rec.get("电话"):
            tel = PHONE_TEL_RE.findall(visible)
            if tel:
                rec["电话"] = " / ".join(list(dict.fromkeys(tel))[:3])
        # 邮箱
        if not rec.get("邮箱"):
            ems = [e for e in EMAIL_RE.findall(visible)
                   if 'made-in-china' not in e.lower()
                   and not re.search(r'\.(png|jpe?g|gif|webp|svg|ico)$', e, re.I)]
            if ems:
                rec["邮箱"] = ems[0]
        # 地址 / 主营
        if not rec.get("地址"):
            ma = re.search(r'(?:公司地址|地\s*址)[：:]\s*([\u4e00-\u9fa5A-Za-z0-9（）()#\- ]{6,80})', visible)
            if ma:
                addr = clean_address(ma.group(1))
                if addr:
                    rec["地址"] = addr
        if not rec.get("主营产品"):
            mp = re.search(r'主\s*营[:：]?\s*([\u4e00-\u9fa5A-Za-z0-9、,，/ ]{4,120})', visible)
            if mp:
                rec["主营产品"] = mp.group(1).strip()[:120]

    def _fallback_en(self, user, host, rec):
        """英文子域首页 + contactinfo 兜底（仅补空缺字段，取不到不强写英文假数据）。"""
        for path in ("/", "/contactinfo.html"):
            alt = f"https://{user}.{host}{path}"
            if not self._goto(alt, expect_domain="made-in-china.com", tries=1):
                continue
            html = self._html()
            self._parse_contact(html, rec)
            if rec.get("手机") or rec.get("电话") or rec.get("邮箱"):
                rec["详情URL"] = rec.get("详情URL") or alt
                return
        self._mark_activity()

    @staticmethod
    def _visible_text(html):
        t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.I | re.S)
        # 先剥标签再剥 URL：避免 href 收尾引号被 URL 剥剩后引发「整段当标签」误吞
        t = re.sub(r"<[^>]+>", " ", t)
        t = re.sub(r"https?://\S+", " ", t)
        return re.sub(r"\s+", " ", _htmlmod.unescape(t.replace("&nbsp;", " ")))
