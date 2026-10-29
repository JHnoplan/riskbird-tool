"""qihuo11467_crawler.py —— 顺企网(11467.com) 关键词搜索采集器（纯 HTTP 版）。

实机探测校准（与 b2b168 同思路：能裸 HTTP 绝不开标签）：
  ✅ 搜索入口 GET https://b2b.11467.com/search.php?q=<关键词> 301 到 SEO 地址
     `search/-<hash>.htm`，跟随重定向即可；第 N 页地址为 `<基址去.htm>-pn<N>.htm`
     （实测 pn2 有效、越界页返回不含公司锚点小页，天然作为终止判据）。
  ✅ 列表两种版式并存：第 1 页是产品卡（卡尾 `<dd><a href="https://<子域>.11467.com">
     公司名</a><span class="weizhi">省-市</span></dd>`），第 2 页起是公司直列。
     公司子域统一形如 `<城市拼音><数字id>.11467.com`（handan.11467.com 这类无数字
     的是城市门户页、b2b/www/cp/product 等是系统域，全部剔除）。
  ✅ 公司联系方式在其商铺的 **contact.asp** 服务端渲染页：`<ul class="codl">`
     内逐行 `<li>地址：…</li><li>联系人：…</li><li>手机：…</li><li>Email：…</li>`；
     个别标签用 HTML 实体全角空格（手&#12288;&#12288;机：），解析前先 unescape。
     商铺首页再补「我公司主营…」/标题产品词/QQ客服行等 contact 页缺失字段。
  ⚠ 站点存在间歇性 TCP connection reset（实测约每数次请求遇到一次），并非封禁：
     重试并重建连接池即可恢复，已内置于 _fetch。

号码/邮箱一律在剥掉 URL 与 script 的可见文本上提取；邮箱剔除平台自有域名。
"""
import html as htmllib
import re
import time
from urllib.parse import quote

import requests

from cdp_base import (CdpSiteCrawler, PHONE_MOBILE_RE, PHONE_TEL_RE, EMAIL_RE, clean_address,
                      parse_contact_field, is_placeholder_number, compose_position,
                      extract_legal_rep)

SEARCH_URL = "https://b2b.11467.com/search.php?q="
HOME_URL = "https://www.11467.com/"
SHOP_DOMAIN = "11467.com"

# 公司商铺子域：字母开头、以数字段结尾（<城市拼音><数字id>）；
# 城市门户（handan 等无数字）与系统域（b2b/www/cp…）天然被排除
_SUB_RE = re.compile(r"^[a-z][a-z0-9\-]{1,38}[0-9]$")
_SYSTEM_SUBS = {"www", "b2b", "cp", "product", "news", "blog", "vip", "co", "m",
                "so", "s", "static", "img", "res", "i", "my", "api", "show",
                "qiye", "gongsi", "guoqi", "search", "info"}
# 列表公司锚点：//<子域>.11467.com">公司名</a>（后面可能紧跟 weizhi 区域）
_CARD_RE = re.compile(
    r'<a[^>]*href="(?:https?:)?//([a-z][a-z0-9\-]{2,40})\.11467\.com/?"[^>]*>'
    r'([^<]{4,60})</a>(?:<span class="weizhi">([^<]{2,20})</span>)?')
# 产品卡里公司锚点之前最近的产品标题（info 链接），作为主营弱信号
_PROD_RE = re.compile(r'href="(?:https?:)?//[a-z][a-z0-9\-]*\.11467\.com/info/(\d+)\.htm"[^>]*>([^<]{6,80})</a>')
# contact.asp 的 codl 标签行（unescape 后全角空格变 \u3000，\s 可容忍）。
# 人名标签不止「联系人」：实测（2026-10 全量运行取证）大量商铺用「主管：」「销售总监：」
# 「检测工程师：」等职务标签放联系人，8 家抽样 3 家因此漏采 → 职务形态标签一并识别为人名行。
_ROLE_LABEL_ALT = r"[\u4e00-\u9fa5]{0,4}(?:经理|总监|工程师|专员|主管|厂长|老板|顾问|代表)"
# 部门标签也是放人对接的（实测合肥晶晨 contact.asp「市场部：杨经理」，旧正则不认这一形
# 就整家漏掉联系人）。值仍要过 parse_contact_field 的人名校验，公司名/地址串会被判
# 无效，不会因为多认一种标签而造出假联系人。
_DEPT_LABEL_ALT = r"[\u4e00-\u9fa5]{2,4}部"
# 少数商铺把岗位单独立一行写（「职位：销售经理」），不与人名行混用；旧正则不认这一形
# 就直接丢掉一个明写岗位（实测无岗位记录里爱采购/顺企网是重灾区）。
_TITLE_LABEL_ALT = r"职\s*位|职\s*务|岗\s*位"
_LABEL_RE = re.compile(
    r'<li>\s*(联\s*系\s*人|手\s*机|电\s*话|地\s*址|微\s*信|Q\s*Q|E-?MAIL|邮\s*箱|传\s*真'
    r'|' + _TITLE_LABEL_ALT + r'|' + _ROLE_LABEL_ALT + r'|' + _DEPT_LABEL_ALT + r')'
    r'[：:]\s*([^<]{1,120})</li>', re.I)
_ROLE_LABEL_RE = re.compile("^(?:" + _ROLE_LABEL_ALT + "|" + _DEPT_LABEL_ALT + r")$")
_MAIN_RE = re.compile(r"我公司主营\s*[:：]?\s*([\u4e00-\u9fa5A-Za-z0-9、，,/ 　]{2,160})")
_QQKF_RE = re.compile(r"QQ客服\s*[：:]?\s*(\d{5,12})")
_NAME_CUT_RE = re.compile(r"(公司|地址|电话|手机|邮箱|主营|经营|注册|所在|联系|网站|首页|更多|成立|员工|微信|QQ)")
# 反采集拆位手机号（193/038/80265）：拼接后恰为 11 位才合回，绝不误伤日期/URL 段
_SLASH_MOBILE_RE = re.compile(r"(?<!\d)(1[3-9]\d)[/\s.\-](\d{2,4})[/\s.\-](\d{4,6})(?!\d)")

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


class Qihuo11467Crawler(CdpSiteCrawler):
    PLATFORM_NAME = "顺企网"
    PLATFORM_SLUG = "11467"
    BASE_URL = HOME_URL
    HOME_DOMAIN = "11467.com"
    NEEDS_BROWSER = False          # 纯 HTTP，不占用浏览器标签
    # 2026-10-28 实机取证：站点开了防采挑战，搜索页与首页统一返回 HTTP 429 +
    # 70 字节 JS 挑战体（<script src="https://do.11467.com/v.js">），requests 不执行 JS
    # 就永远拿不到数据。旧实现把它当普通请求失败重试几轮后返回空串，列表解出 0 家、
    # 日志只写一句「无公司结果」，整轮就在这种静默失败里空跑。现在走基类防采看门狗：
    # 弹提醒 + 临时借标签把验证页递到眼前 + 探到过后自动抄 cookie 回快路续采。
    BLOCK_COOKIE_DOMAIN = "11467.com"

    def __init__(self, port=9255):
        super().__init__(port)
        self.delay_between = 2.0
        self.jitter = 1.0
        self.max_pages = 0         # 不限页数：翻到没有公司结果为止
        self._session = self._new_session()
        # HTTP 一直不通但浏览器已过验证时的降级模式：临时改走标签取页，不空跑
        self._browser_fallback = False
        self._fallback_pages = 0

    def _new_session(self):
        s = requests.Session()
        s.headers.update({
            "User-Agent": _UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": "https://b2b.11467.com/",
        })
        return s

    # ── 旁路浏览器：平时不占标签、不启动 Chrome；只有过验证/降级时才借标签 ──
    def start_browser(self, headed=True):
        return True

    def _is_session_alive(self):
        return True

    def reset_run_state(self):
        super().reset_run_state()
        self._browser_fallback = False
        self._fallback_pages = 0
        return True

    def probe_block(self):
        """看门狗恢复探测：必须是「真拿得到正常页」才算解除（只看状态码会把
        挑战页当已通）。HTTP 探一次首页；标签能正常打开本站也算解除（转降级模式）。"""
        try:
            r = self._session.get(HOME_URL, timeout=20)
            if r.status_code == 200 and len(r.text or "") >= 2000 \
                    and not self.http_block_reason(r.status_code, r.text):
                self._block_reason = ""
                return ""
        except Exception:
            pass
        if self._tab_page_ok():
            self._block_reason = ""
            return ""
        return self._block_reason or "顺企网仍在防采/限流状态"

    def _tab_page_ok(self):
        """标签当前是否已能正常打开本站（用户在浏览器里过完验证的直观表现）。"""
        if not self.pin_tab_id:
            return False
        try:
            st = self._page_state() or {}
            if "11467.com" not in (st.get("url") or ""):
                return False
            return int(st.get("len") or 0) >= max(600, self.MIN_PAGE_CHARS)
        except Exception:
            return False

    def _fetch_via_tab(self, url):
        """降级取页：HTTP 仍被拦但浏览器能开，临时用共享 Chrome 标签取同一页，
        让采集继续（比 HTTP 慢，但远好过整轮空转）。返回与 _fetch 同构的 (html, 最终URL)。"""
        try:
            self._cdp_navigate(url)
            self._wait_ready(None, timeout=20)
            self._mark_activity()
            html = self._html() or ""
            if html and not self.http_block_reason(200, html):
                return html, (self._current_url() or url)
        except Exception:
            pass
        return "", url

    # ── 裸 HTTP 取页：站点偶发 connection reset（非封禁），退避重建连接池重试 ──
    def _fetch(self, url, tries=4, allow_redirects=True, guarded=True):
        if self._browser_fallback:
            # 降级模式下隔 20 页探一次 HTTP 是否已恢复，恢复就切回不占标签的快路
            self._fallback_pages += 1
            if self._fallback_pages % 20:
                return self._fetch_via_tab(url)
            if not self.probe_block():
                self._browser_fallback = False
                self._block_reason = ""
                self._release_verify_tab()
                self._log("  ✅ 顺企网 HTTP 链路已恢复，切回纯 HTTP 快路")
        last = ""
        for i in range(max(1, tries)):
            try:
                r = self._session.get(url, timeout=25, allow_redirects=allow_redirects)
                self._mark_activity()
                block = self.http_block_reason(r.status_code, r.text, url)
                if block:
                    # 防采/限流插页：对着它重试只会加重风控，直接交看门狗处置
                    self._block_reason = block
                    last = f"HTTP {r.status_code}（防采插页）"
                    break
                if r.status_code == 200 and r.text:
                    self._block_reason = ""
                    return r.text, r.url
                last = f"HTTP {r.status_code}"
                if r.status_code == 404:
                    return "", url
            except requests.exceptions.ConnectionError as e:
                last = str(e).splitlines()[0][:80] or "连接被重置"
                self._session.close()          # 坏连接已入池，整会话重建
                self._session = self._new_session()
            except Exception as e:
                last = str(e).splitlines()[0][:80]
            if i < tries - 1:
                time.sleep(1.2 * (i + 1))
        if guarded and self._block_reason:
            if self.handle_http_block(url):
                self._block_reason = ""
                return self._fetch(url, tries=tries, allow_redirects=allow_redirects,
                                   guarded=False)
            # 等不到 HTTP 恢复：标签能正常开本站就降级继续采，别让整轮卡死
            if self._tab_page_ok():
                self._browser_fallback = True
                self._fallback_pages = 0
                self._log("  ⚠ 顺企网 HTTP 仍被拦，本轮临时改走浏览器标签续采")
                return self._fetch_via_tab(url)
        self._log(f"  ⚠ 顺企网请求失败（{last}）：{url[:70]}")
        return "", url

    # ── 列表 ───────────────────────────────────────────────────
    def _search_page(self, kw, page, base_cache):
        """第 1 页跟随 search.php 重定向拿 SEO 基址；后续页用 `-pn<N>.htm` 规律拼址。"""
        if page == 1 or kw not in base_cache:
            html, final = self._fetch(SEARCH_URL + quote(kw))
            if html and final and "/search/" in final:
                base_cache[kw] = final
            return html
        base = base_cache.get(kw, "")
        if not base:
            return ""
        url = base[:-4] + f"-pn{page}.htm" if base.endswith(".htm") else f"{base}-pn{page}.htm"
        return self._fetch(url)[0]

    def parse_list(self, html, kw):
        """提取公司条目：子域必须形如 <城市><数字id>；剔除系统域/城市门户/导航词。"""
        out, seen = [], set()
        if not html:
            return out
        for m in _CARD_RE.finditer(html):
            sub, name, region = m.group(1).lower(), (m.group(2) or "").strip(), (m.group(3) or "").strip()
            if sub in _SYSTEM_SUBS or not _SUB_RE.match(sub):
                continue
            if len(name) < 4 or any(x in name for x in
                                    ("首页", "登录", "注册", "黄页", "资讯", "供应", "推广", "法律", "服务条款")):
                continue
            if not re.search(r"公司|厂|中心|部|所|院|馆|企业|商行|商店|经营部|个体|行$", name):
                continue
            if sub in seen or name in seen:
                continue
            seen.add(sub)
            seen.add(name)
            shop = f"https://{sub}.11467.com/"
            # 主营弱信号：取「上一家公司锚点之后、本公司锚点之前」最近的产品标题
            # （仅产品卡版式有；越窗/跨卡拿不到就留空 fail-open，不错归到别家）
            prod = ""
            window = html[max(0, m.start() - 3000):m.start()]
            prev_end = max([cm.end() for cm in _CARD_RE.finditer(window)
                            if cm.start() + len(cm.group(0)) < len(window)] or [0])
            pms = [pm for pm in _PROD_RE.finditer(window) if pm.start() >= prev_end]
            if pms:
                prod = pms[-1].group(2).strip()
            out.append({
                "公司名称": name[:60],
                "公司ID": sub,
                "memberId": sub,
                "公司网址": shop,
                "详情URL": shop,
                "主营产品": prod[:200],
                "所在区域": region,
                "标题": (prod or name)[:80],
            })
        return out

    # ── 覆盖基类翻页：SEO 地址拼 -pn 分页，纯 HTTP ─────────────
    def _iter_keyword(self, kw):
        empty = stale = 0
        page = 0
        base_cache = {}
        while not self._stop_requested:
            page += 1
            if self.max_pages and page > self.max_pages:
                return
            while self._pause_requested and not self._stop_requested:
                time.sleep(0.5)
            html = self._search_page(kw, page, base_cache)
            entries = self.parse_list(html, kw)
            self.stat_pages += 1
            self.stat_listed += len(entries)
            if not entries:
                empty += 1
                if empty >= 1:
                    self._log(f"  「{kw}」第 {page} 页无公司结果，停止翻页")
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
                # 早过滤只看产品信号（公司直列版式无主营产品→fail-open），
                # 相关性判定交给详情页复核。
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
                if stale >= 2:
                    return
            else:
                stale = 0
            self._sleep()

    # ── 详情：contact.asp（联系方式页）+ 商铺首页补缺 ──────────
    def crawl_detail(self, stub):
        rec = self._base_rec(stub)
        sub = stub.get("公司ID") or stub.get("memberId") or ""
        shop = f"https://{sub}.11467.com/"
        rec["公司网址"] = shop
        rec["详情URL"] = shop
        contact_html, _ = self._fetch(shop + "contact.asp")
        if not contact_html:
            # contact.asp 偶发 404/失败：只取商铺首页，仍空才判失败
            home_html, _ = self._fetch(shop)
            if not home_html:
                rec["状态"] = "详情页导航失败"
                return rec
            data = self._parse_home(home_html, stub)
        else:
            data = self._parse_contact(contact_html)
            need_more = not (data.get("联系人") and (data.get("手机") or data.get("电话"))) \
                or not data.get("主营产品")
            if need_more:
                home_html, _ = self._fetch(shop, tries=2)
                if home_html:
                    for k, v in self._parse_home(home_html, stub).items():
                        if v and not data.get(k):
                            data[k] = v
        for k, v in data.items():
            if v and not rec.get(k):
                rec[k] = v
        if not rec.get("公司名称") or "顺企网" in rec.get("公司名称", ""):
            rec["公司名称"] = stub.get("公司名称", rec.get("公司名称", ""))
        if not self.detail_relevant(rec):
            rec["状态"] = "类目不符(已过滤)"
            return rec
        has_phone = bool(rec.get("电话") or rec.get("手机"))
        has_email = bool(rec.get("邮箱"))
        rec["状态"] = ("成功" if (has_phone or has_email)
                       else ("无电话(未公开)" if rec.get("地址") or rec.get("公司网址") else "无联系方式"))
        return rec

    @staticmethod
    def _lines(raw_html):
        """unescape（实体全角空格等）→ 去 script/style → 按标签切行。"""
        t = htmllib.unescape(raw_html)
        t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", t, flags=re.I | re.S)
        t = re.sub(r"<[^>]+>", "\n", t)
        return [l.strip() for l in t.split("\n") if l.strip()]

    @staticmethod
    def _defuse_mobile(text):
        """把拆位写法手机号（193/038/80265）合回 11 位：仅当拼接后恰为合法手机号才替换。"""
        def _r(m):
            joined = "".join(m.groups())
            return joined if re.fullmatch(r"1[3-9]\d{9}", joined) else m.group(0)
        return _SLASH_MOBILE_RE.sub(_r, text)

    @staticmethod
    def _merge_multi(existing, new_vals):
        """号码合并而非覆盖：旧写法手机行覆盖掉电话行里的同号段（实测丢 13938523177）。"""
        cur = [x.strip() for x in (existing or "").split(" / ") if x.strip()]
        return " / ".join(list(dict.fromkeys(cur + [v.strip() for v in new_vals if v.strip()]))[:3])

    # 「我公司主营：A , B , C」后面直接粘着商铺自己的公司名与下一个表单标签（实测
    # 184/227 行顺企网被写成「…太阳能照明灯等光伏应用产品 金尚新能源科技股份有限公司
    # 地址」），外呼/入库都读不懂。从公司名/下一标签起点整段砍掉。
    _PROD_TAIL_RE = re.compile(
        r"[\u4e00-\u9fa5A-Za-z0-9]{2,24}(?:股份有限公司|有限公司|集团|工贸)"
        r"|\s*(?:地址|电话|手机|邮箱|联系人|主营业务)\s*[：:]", re.I)

    @classmethod
    def _clean_products(cls, val):
        v = (val or "").strip()
        if not v:
            return ""
        cut = cls._PROD_TAIL_RE.search(v)
        if cut:
            v = v[:cut.start()]
        return re.sub(r"[,，;；\s]+$", "", v.strip())[:200]

    @classmethod
    def _visible(cls, raw_html):
        # 顺序很重要：先剥标签再剥 URL。反过来会把 href="https://…"> 的收尾引号
        # 删掉，导致下一轮 <[^>]+> 把「>公司名 地址：…」整段当标签吞掉（实测丢地址）。
        t = htmllib.unescape(raw_html)
        t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", t, flags=re.I | re.S)
        t = re.sub(r"<[^>]+>", " ", t)
        t = re.sub(r"https?://\S+", " ", t)
        return cls._defuse_mobile(re.sub(r"\s+", " ", t.replace("\u3000", " ")))

    @classmethod
    def _parse_contact(cls, raw_html):
        data = {}
        t = cls._defuse_mobile(htmllib.unescape(raw_html))
        for m in _LABEL_RE.finditer(t):
            label = re.sub(r"\s+", "", m.group(1)).upper()
            val = re.sub(r"\s+", " ", m.group(2)).strip(" ：:　")
            if not val:
                continue
            # 「联系人」与职务标签（主管/销售总监/检测工程师…）同样当人名行处理：
            # 标签本身就是岗位，值就是人名。旧逻辑把后缀称谓剥掉（「周经理」→「周」），
            # 既弄坏了称呼又弄丢了岗位，现改成保留称谓 + 岗位单独记一列。
            if label == "联系人" or _ROLE_LABEL_RE.fullmatch(label):
                val = _NAME_CUT_RE.split(val)[0].strip()
                name, pos = parse_contact_field(val)
                if name:
                    data["联系人"] = name
                    role = "" if label == "联系人" else label
                    # 「市场部：杨经理」这类标签把部门写在一半、称谓写在人名里，
                    # 直接取 label 会丢「经理」、取后缀会丢「市场」，合成后才完整。
                    merged = compose_position(role, pos)
                    if merged and not data.get("岗位"):
                        data["岗位"] = merged[:20]
            elif label in ("职位", "职务", "岗位"):
                p = compose_position(val, "")
                if p and not data.get("岗位"):
                    data["岗位"] = p[:20]
            elif label == "地址":
                addr = clean_address(re.split(r"技术支|顺企网|商铺管理", val)[0])
                if addr:
                    data["地址"] = addr
            elif label in ("手机",):
                mm = [x for x in PHONE_MOBILE_RE.findall(val) if not is_placeholder_number(x)]
                if mm:
                    data["手机"] = cls._merge_multi(data.get("手机"), mm)
            elif label in ("电话", "传真"):
                if label == "电话":
                    tels = [x for x in PHONE_TEL_RE.findall(val) if not is_placeholder_number(x)]
                    mob = [x for x in PHONE_MOBILE_RE.findall(val) if not is_placeholder_number(x)]
                    if mob:
                        data["手机"] = cls._merge_multi(data.get("手机"), mob)
                    if tels:
                        data["电话"] = cls._merge_multi(data.get("电话"), tels)
            elif label in ("EMAIL", "邮箱"):
                em = EMAIL_RE.findall(val)
                if em and "11467" not in em[0].lower():
                    data["邮箱"] = em[0]
            # QQ/微信 无对应列，值若是手机号形态仍利用
            elif label in ("微信", "QQ"):
                mm = PHONE_MOBILE_RE.findall(val)
                if mm and not data.get("手机"):
                    data["手机"] = mm[0]
        # 兜底：整页可见文本上再找一遍号码/邮箱（个别商铺 codl 结构缺失）
        vis = cls._visible(raw_html)
        if not data.get("手机"):
            mob = [x for x in PHONE_MOBILE_RE.findall(vis) if not is_placeholder_number(x)]
            if mob:
                data["手机"] = " / ".join(list(dict.fromkeys(mob))[:3])
        if not data.get("电话"):
            tels = [x for x in PHONE_TEL_RE.findall(vis) if not is_placeholder_number(x)]
            if tels:
                data["电话"] = " / ".join(list(dict.fromkeys(tels))[:3])
        if not data.get("邮箱"):
            ems = [e for e in EMAIL_RE.findall(vis) if "11467" not in e.lower()]
            if ems:
                data["邮箱"] = ems[0]
        if not data.get("联系人"):
            cm = re.search(r"联\s*系\s*人[：:]\s*([\u4e00-\u9fa5·A-Za-z]{1,10})", vis)
            if cm:
                name, pos = parse_contact_field(_NAME_CUT_RE.split(cm.group(1))[0].strip())
                if name:
                    data["联系人"] = name
                    if pos and not data.get("岗位"):
                        data["岗位"] = pos[:20]
        mp = _MAIN_RE.search(vis)
        if mp:
            data["主营产品"] = cls._clean_products(mp.group(1))
        # 首页/联系页工商段里的「法人代表：陈杰」（实机回访 4/4 页命中）：联系人没留名字时
        # 它是唯一能把电话接到真人手里的入口，能不能当客户名称由清洗层既有口径定。
        if not data.get("法定代表人"):
            lr = extract_legal_rep(t)
            if lr:
                data["法定代表人"] = lr
        return data

    @classmethod
    def _parse_home(cls, raw_html, stub):
        data = {}
        vis = cls._visible(raw_html)
        # 标题形如「产品1_产品2_产品3-公司名」→ 主营产品
        mt = re.search(r"<title>(.*?)</title>", raw_html, re.S | re.I)
        if mt:
            core = re.split(r"\s*[-_|]\s*顺企网", htmllib.unescape(mt.group(1)).strip())[0].strip()
            if "-" in core:
                prods, cname = core.rsplit("-", 1)
                if re.search(r"[\u4e00-\u9fa5]", cname) and len(cname) >= 4:
                    data.setdefault("公司名称", cname.strip()[:60])
                data.setdefault("主营产品", re.sub(r"[,，_]+$", "", prods.strip())[:200])
        mp = _MAIN_RE.search(vis)
        if mp and not data.get("主营产品"):
            data["主营产品"] = cls._clean_products(mp.group(1))
        # 首页页脚同构标签行（联系人/电话/地址…）
        for k, v in cls._parse_contact(raw_html).items():
            if v and not data.get(k):
                data[k] = v
        mq = _QQKF_RE.search(vis)
        if mq:
            num = mq.group(1)
            if re.fullmatch(r"1[3-9]\d{9}", num) and not data.get("手机"):
                data["手机"] = num
        if not data.get("地址"):
            ma = re.search(r"地\s*址[：:]\s*([\u4e00-\u9fa5A-Za-z0-9（）()#\- ]{6,80})", vis)
            if ma:
                addr = clean_address(re.split(r"技术支|顺企网|商铺管理", ma.group(1))[0])
                if addr:
                    data["地址"] = addr
        if not data.get("主营产品"):
            data["主营产品"] = stub.get("主营产品", "")
        return data
