"""b2b168_crawler.py —— 八方资源网(b2b168.com) 关键词搜索采集器（纯 HTTP 版）。

本轮实机重新校准（推翻旧链路）：
  ❌ 旧入口 GET https://www.b2b168.com/search/?keyword=<kw> 经取证**完全不按关键词过滤**
     ——任意关键词都返回同一批默认黄页（47694 字节、c168 id 一模一样），且详情页
     c168-<id>.html 已失效，导致「39 家新增 0」的假阳性。
  ✅ 真正可用的搜索是首页表单 **POST https://www.b2b168.com/Index.aspx**，字段
     q=关键词 / act=ComSearch / page=页码。服务端直接渲染**关键词相关**的公司列表，
     条目结构稳定：`<a class="list-item-title-text" title="公司名"
     href="http://<子域>.b2b168.com/">公司名</a>`；分页 page=N 实测有效
     （page1=42、page2=39、仅 4 家重叠）。
  ✅ 公司联系方式在其**自有子域商铺首页**（http→301→https，服务端渲染，含
     联系人/手机/地址/主营产品），与 gys 静态子域同构。

因此本采集器整体改走裸 HTTP(requests)，不占用浏览器标签、无验证码、更快更稳，
是「之前没用过的方法」。号码/邮箱一律在剥掉 URL 与 script 的可见文本上提取，
避免把产品图片文件名（s.b2b168.com/…/17580381501…png）里的数字误当成手机号。
"""
import re
import time

import requests
from bs4 import BeautifulSoup

from cdp_base import (CdpSiteCrawler, PHONE_MOBILE_RE, PHONE_TEL_RE, EMAIL_RE, clean_address,
                      parse_contact_field, is_placeholder_number, normalize_spaces,
                      compose_position, dept_only)

SEARCH_POST_URL = "https://www.b2b168.com/Index.aspx"
HOME_URL = "https://www.b2b168.com/"

# 商铺主机两种形态实测并存（实机取证：wxlianghuizd.cn.b2b168.com 可访而
# wxlianghuizd.b2b168.com 404，另一家则相反）：404 时互换 .cn 段重试。
_HOTLINE_RE = re.compile(r"(?:全国统一热线|服务热线|热\s*线)[：:]?\s*([\d\-()（ ）]{7,16})")
_CONTACT_NAME_RE = re.compile(r"(?:联系人|联\s*系\s*人|姓\s*名)\s*(?:[：:]|是)?\s*([\u4e00-\u9fa5·A-Za-z]{1,10})")
_NAME_CUT_RE = re.compile(r"(公司|地址|电话|手机|邮箱|主营|经营|注册|所在|联系|网站|首页|更多|成立|员工)")
# 联系方式页把字段全堆在一个 li/div 里用 <br/> 分隔，标签里还有全角空格对齐
# （实测「姓\u3000\u3000名：王国民<span>先 生</span><span>（经理）</span>」），
# 必须先把块级标签/<br> 换成换行、再把 \u3000 与标签清掉，才能按行拆标签与值。
_LINE_BREAK_RE = re.compile(r"</(?:li|div|p|tr|td|h[1-6]|ul|dl|dt|dd|section)>|<br\s*/?>", re.I)

# 公司自有子域商铺根址：http(s)://<主机>.b2b168.com/（<主机> 可能形如 sub 或 sub.cn）
SHOP_ROOT_RE = re.compile(r"^(?:https?:)?//([a-z0-9][a-z0-9_.\-]{1,60})\.b2b168\.com/?$", re.I)

# 商铺分页版次的尾部杂质（实测 SEO 标题/锚文本出现「…材料有限公司3.」）：
# 只剥「中文名 + 结尾纯数字/句点」，不动 3M、999 这类名字中间/结尾带字母数字的正规字号。
_NAME_TAIL_JUNK_RE = re.compile(r"(?<=[\u4e00-\u9fa5\)）])\s*[0-9０-９]+\s*[.。·]*\s*$")


def _strip_name_junk(name):
    return _NAME_TAIL_JUNK_RE.sub("", (name or "").strip())


# 系统/导航子域，绝不能当成某家公司
EXCLUDED_SUBS = {
    "www", "info", "m", "so", "s", "l", "news", "product", "static", "res",
    "kf", "bill", "biz", "cent", "cn", "en", "mip", "tr", "i", "gys", "img",
    "image", "login", "member", "membercenter", "my", "api", "css", "js",
}
# 平台自身推广/统计号码，绝不能当成公司电话
GLOBAL_EXCLUDED_PHONES = {"15493102355", "14003232414", "17304420938", "05923689198"}

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


class B2b168Crawler(CdpSiteCrawler):
    PLATFORM_NAME = "八方资源网"
    PLATFORM_SLUG = "b2b168"
    BASE_URL = HOME_URL
    HOME_DOMAIN = "b2b168.com"
    # 本站改走裸 HTTP，不需要共享 Chrome 的标签
    NEEDS_BROWSER = False
    # 本站只有 IP 限流型拦截（无验证码）：等冷却就行，不为它拉起 Chrome 窗口
    BLOCK_OPEN_BROWSER = False

    def __init__(self, port=9255):
        super().__init__(port)
        self.delay_between = 2.0
        self.jitter = 1.0
        self.max_pages = 0          # 不限页数：一直翻到没数据再切下一个关键词
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": _UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": HOME_URL,
            "Origin": HOME_URL,
        })

    # ── 旁路浏览器：本站不占用标签、不启动 Chrome ──────────────
    def start_browser(self, headed=True):
        return True

    def ensure_tab(self, log=None):
        return True

    def close_browser(self):
        return None

    def _is_session_alive(self):
        return True

    def probe_block(self):
        """能力探测：按真实搜索形状（POST Index.aspx）发一次，能解析出公司条目才算解除。"""
        try:
            html = self._fetch("POST", SEARCH_POST_URL, tries=1, guarded=False,
                               data={"q": "公司", "act": "ComSearch", "page": ""})
            if html and self.parse_list(html, "公司"):
                self._block_reason = ""
                return ""
        except Exception:
            pass
        return self._block_reason or "八方资源网仍在限流状态"

    # ── 裸 HTTP 取页（带重试，网络抖动不伪装成空页）────────────
    def _fetch(self, method, url, tries=3, guarded=True, **kw):
        last = ""
        for i in range(max(1, tries)):
            try:
                r = self._session.request(method, url, timeout=25, **kw)
                self._mark_activity()
                block = self.http_block_reason(r.status_code, r.text, url)
                if block:
                    # 防采/限流插页：重试只会加重风控，直接交看门狗（旧实现把它当普通
                    # 失败返空串，列表解 0 家就默默切下一个关键词）
                    self._block_reason = block
                    last = f"HTTP {r.status_code}（防采插页）"
                    break
                if r.status_code == 200 and r.text:
                    r.encoding = r.apparent_encoding or "utf-8"
                    self._block_reason = ""
                    return r.text
                last = f"HTTP {r.status_code}"
            except Exception as e:
                last = str(e).splitlines()[0][:100]
            if i < tries - 1:
                time.sleep(1.2 * (i + 1))
        if guarded and self._block_reason:
            if self.handle_http_block(url):
                self._block_reason = ""
                return self._fetch(method, url, tries=tries, guarded=False, **kw)
        self._log(f"  ⚠ b2b168 请求失败（{last}）：{url[:70]}")
        return ""

    def _search_page(self, kw, page):
        data = {"q": kw, "act": "ComSearch", "page": str(page) if page > 1 else ""}
        return self._fetch("POST", SEARCH_POST_URL, data=data)

    # ── 列表解析 ───────────────────────────────────────────────
    def parse_list(self, html, kw):
        """从搜索结果页提取公司条目：class=list-item-title-text 的锚点 → 子域商铺。"""
        out, seen = [], set()
        if not html:
            return out
        soup = BeautifulSoup(html, "lxml")
        anchors = soup.find_all("a", class_="list-item-title-text") or soup.find_all("a", href=True)
        for a in anchors:
            href = a.get("href") or ""
            m = SHOP_ROOT_RE.match(href.strip())
            if not m:
                continue
            host = m.group(1).lower().strip(".")   # 保留完整主机（含可能的 .cn 段）
            first = host.split(".")[0]
            if first in EXCLUDED_SUBS or host in EXCLUDED_SUBS or host in seen:
                continue
            name = (a.get("title") or a.get_text(strip=True) or "").strip()
            name = _strip_name_junk(name)
            if len(name) < 4 or any(x in name for x in ("首页", "登录", "注册", "更多", "八方业务", "供应信息")):
                continue
            seen.add(host)
            shop = f"https://{host}.b2b168.com/"
            out.append({
                "公司名称": name[:60],
                "公司ID": host,
                "memberId": host,
                "公司网址": shop,
                "详情URL": shop,
                "主营产品": "",
                "标题": name[:80],
            })
        return out

    def list_has_more(self, html, page):
        return bool(html)

    # ── 覆盖基类翻页：POST 分页，纯 HTTP ───────────────────────
    def _iter_keyword(self, kw):
        empty = stale = 0
        page = 0
        while not self._stop_requested:
            page += 1
            if self.max_pages and page > self.max_pages:
                return
            while self._pause_requested and not self._stop_requested:
                time.sleep(0.5)
            html = self._search_page(kw, page)
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
                # 列表早过滤只看产品信号（本 stub 无主营产品→fail-open），
                # 避免拿法人名误杀；相关性判定交给详情页复核。
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

    # ── 详情：GET 公司自有子域商铺首页（服务端渲染含联系方式）──
    @staticmethod
    def _alt_host_url(host, base_url):
        """主机 .cn 段互换：形如 a.cn → a；a → a.cn（实测两种形态并存，
        单一形态 404 不代表商铺不存在，换另一种即可取到）。"""
        if host.endswith(".cn"):
            alt = host[:-3]
        else:
            alt = f"{host}.cn"
        return base_url.replace(f"{host}.b2b168.com", f"{alt}.b2b168.com"), alt

    def crawl_detail(self, stub):
        rec = self._base_rec(stub)
        host = stub.get("公司ID") or stub.get("memberId") or ""
        url = stub.get("详情URL") or f"https://{host}.b2b168.com/"
        html = self._fetch("GET", url)
        if not html:
            # 404/瞬时失败：互换 .cn 段再试一次（实测同一主机两种形态只有一种可访）
            alt_url, alt_host = self._alt_host_url(host, url)
            html = self._fetch("GET", alt_url)
            if html:
                url, host = alt_url, alt_host
                rec["公司网址"] = alt_url
                rec["详情URL"] = alt_url
                rec["memberId"] = alt_host
        if not html:
            rec["状态"] = "详情页导航失败"
            return rec
        data = self._parse_shop(html, stub.get("公司名称", ""), url)
        for k, v in data.items():
            if v and not rec.get(k):
                rec[k] = v
        if not rec.get("公司网址"):
            rec["公司网址"] = url
        # 关键修正：本站联系人/地址/邮箱的真正数据源是商铺自己的「联系方式」页，
        # 而旧链路只访商铺首页 + home.aspx（实测这两页都没有联系人）→ 大面积空。
        # 缺任一项就补访 /contact.aspx（桌面），再不成补访 /m/contact.aspx（移动版，
        # 明文「联系人：王国民先生 （经理）」+邮箱+地址，实测取回）。
        if not (rec.get("联系人") and rec.get("地址") and (rec.get("邮箱") or rec.get("手机") or rec.get("电话"))):
            for suffix in ("/contact.aspx", "/m/contact.aspx"):
                page = self._fetch("GET", url.rstrip("/") + suffix, tries=1)
                if not page:
                    continue
                more = self._parse_contact_page(page)
                hit = False
                for k, v in more.items():
                    if v and not rec.get(k):
                        rec[k] = v
                        hit = True
                if rec.get("联系人") and rec.get("邮箱") and rec.get("地址"):
                    if hit:
                        rec["详情URL"] = url.rstrip("/") + suffix
                    break
                # 桌面页拿齐了联系人但没邮箱时继续试移动版：实测 m/contact.aspx
                # 多一行明文「邮 箱：3022212375@qq.com」，而桌面页只给「在线询价」链接。
                self._sleep()
        # 商铺首页/home.aspx 都拿不到时，再试一次「关于我们」(home.aspx)：
        # 实机取证该页含「全国统一热线：139…」等首页没有的字段。
        if not (rec.get("联系人") and (rec.get("手机") or rec.get("电话"))):
            about = self._fetch("GET", url.rstrip("/") + "/home.aspx", tries=1)
            if about:
                more = self._parse_shop(about, stub.get("公司名称", ""), url, from_about=True)
                for k, v in more.items():
                    if v and not rec.get(k):
                        rec[k] = v
        # 详情页复核相关性（公司名+主营；弱信号 fail-open 见基类 detail_relevant）
        if not self.detail_relevant(rec):
            rec["状态"] = "类目不符(已过滤)"
            return rec
        has_phone = bool(rec.get("电话") or rec.get("手机"))
        has_email = bool(rec.get("邮箱"))
        rec["状态"] = ("成功" if (has_phone or has_email)
                       else ("无电话(未公开)" if rec.get("地址") or rec.get("公司网址") else "无联系方式"))
        return rec

    @staticmethod
    def _contact_lines(html):
        """联系方式页转「一行一个字段」：块级标签与 <br> 换行、标签剥成空格、
        全角对齐空格 \u3000 转普通空格。不先换行会把「姓　名：X 电话：Y 地址：Z」
        整块当成一行，逐字段抽取无从下手。"""
        t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html or "", flags=re.I | re.S)
        t = _LINE_BREAK_RE.sub("\n", t)
        t = re.sub(r"<[^>]+>", " ", t)
        t = t.replace("\u3000", " ").replace("&nbsp;", " ").replace("\xa0", " ")
        t = re.sub(r"[ \t]+", " ", t)
        return [x.strip() for x in re.split(r"[\n\r]+", t) if x.strip()]

    def _parse_contact_page(self, html):
        """解析商铺「联系方式」页（桌面 /contact.aspx 与移动 /m/contact.aspx 同一口径）。
        实测有三种写法：「姓 名：王国民 先 生 （经理）」「联系人：王国民先生 （经理）」
        「姓名： 周经理」，都靠 parse_contact_field 统一拆人名+岗位；同时该页还携带
        商铺首页没有的地址与邮箱，一并补采。"""
        data = {}
        for line in self._contact_lines(html):
            # 商铺页正文里出现「联系人是孟凤环。」（真实夹具取证）：「是」也当冒号用，
            # 否则 partition 分不开、整行姓名/岗位直接丢。
            line = re.sub(r"^(姓\s*名|联\s*系\s*人|职\s*位|职\s*务|岗\s*位)\s*是\s*", r"\1：", line, count=1)
            label, sep, value = line.partition("：")
            if not sep or not value.strip():
                continue
            key = re.sub(r"\s+", "", label).lower()
            value = value.strip()
            if key in ("姓名", "联系人") and not data.get("联系人"):
                name, pos = parse_contact_field(value)
                if name:
                    data["联系人"] = name
                # 姓名行只写了部门（「联系人：销售部」）：名字作废，归进岗位列
                raw_dept = dept_only(value)
                merged = compose_position(pos, raw_dept) if not name else pos
                if merged:
                    data["岗位"] = merged[:20]
            elif key in ("职位", "职务", "岗位") and not data.get("岗位"):
                # 部分商铺 contact.aspx 把职位单独立一行（与 11467 同形），
                # 旧解析不认这个标签就直接丢掉一个明写岗位。
                p = compose_position(value, "") or dept_only(value)
                if p:
                    data["岗位"] = p[:20]
            elif key in ("手机", "手机号码"):
                mm = PHONE_MOBILE_RE.search(re.sub(r"(\d)[\s-]+(?=\d)", r"\1", value))
                if mm and not is_placeholder_number(mm.group()):
                    data.setdefault("手机", mm.group())
            elif key in ("电话", "联系电话"):
                raw = re.sub(r"\s", "", value)
                if is_placeholder_number(raw):
                    continue
                mm = PHONE_MOBILE_RE.search(raw)
                if mm:
                    data.setdefault("手机", mm.group())   # 把手机号填在「电话」行的站点不少
                else:
                    data.setdefault("电话", raw)
            elif key in ("地址", "公司地址", "详细地址"):
                addr = clean_address(value)
                if len(addr) > 5:
                    data.setdefault("地址", addr[:120])
            elif key in ("邮箱", "电子邮箱", "邮件留言"):
                em = EMAIL_RE.search(value)
                if em and "b2b168" not in em.group().lower():
                    data.setdefault("邮箱", em.group())
        if data.get("电话") and data.get("电话") == data.get("手机"):
            data.pop("电话")
        return data

    def _parse_shop(self, html, fallback_name, url, from_about=False):
        data = {}
        soup = BeautifulSoup(html, "lxml")
        title = soup.find("title")
        tt = title.get_text(strip=True) if title else ""
        # 标题形如「产品1,产品2,产品3_公司名 - 八方资源网」
        core = re.split(r"\s+-\s+八方资源网", tt)[0].strip()
        if "_" in core:
            prods, cname = core.rsplit("_", 1)
            data["公司名称"] = _strip_name_junk(cname)[:60]
            data["主营产品"] = re.sub(r"[,，]+$", "", prods.strip())[:200]
        elif core and "黄页" not in core:
            data["公司名称"] = _strip_name_junk(core)[:60]
        if not data.get("公司名称"):
            data["公司名称"] = _strip_name_junk(fallback_name)[:60]

        visible = self._visible_text(html)
        # 联系人：实机取证商铺页真实标签是「姓名：」（旧正则只认「联系人：」导致
        # 大量记录联系人为空）；现在不再剥称谓（历史上把「周经理」洗成「周」是自残），
        # 只截掉粘连的下个表单标签/营销尾巴，并把括号里的岗位单独记一列。
        mc = _CONTACT_NAME_RE.search(visible)
        if mc:
            val, pos = parse_contact_field(_NAME_CUT_RE.split(mc.group(1))[0])
            if val:
                data["联系人"] = val
            merged = pos or dept_only(mc.group(1))
            if merged:
                data["岗位"] = merged[:20]
        ma = re.search(r"(?:公司地址|地\s*址)\s*[：:]\s*([\u4e00-\u9fa5A-Za-z0-9（）()#\- ]{6,80})", visible)
        if ma:
            addr = clean_address(ma.group(1))
            if addr:
                data["地址"] = addr
        # 号码/邮箱只在剥掉 URL 与 script 的可见文本上取，规避图片文件名里的数字串；
        # 占位假号（实测 contact.aspx 明写「电　话：000-000000」）一律滤掉。
        mob = [p for p in PHONE_MOBILE_RE.findall(visible)
               if p not in GLOBAL_EXCLUDED_PHONES and not is_placeholder_number(p)]
        if mob:
            data["手机"] = " / ".join(list(dict.fromkeys(mob))[:3])
        if not data.get("手机"):
            tel = [t for t in PHONE_TEL_RE.findall(visible)
                   if re.sub(r"\D", "", t) not in GLOBAL_EXCLUDED_PHONES
                   and not is_placeholder_number(t)]
            if tel:
                data["电话"] = " / ".join(list(dict.fromkeys(tel))[:3])
        # 「全国统一热线：139…」：home.aspx/页脚里的平台化展示号码，作为号码补充来源
        hm = _HOTLINE_RE.search(visible)
        if hm:
            raw = re.sub(r"\s", "", hm.group(1))
            digits = re.sub(r"\D", "", raw)
            if re.fullmatch(r"1[3-9]\d{9}", digits) and digits not in GLOBAL_EXCLUDED_PHONES:
                data.setdefault("手机", digits)
            elif len(digits) >= 7 and not data.get("电话"):
                data["电话"] = raw
        ems = [e for e in EMAIL_RE.findall(visible)
               if "b2b168" not in e.lower() and not re.search(r"\.(png|jpe?g|gif|webp|svg|ico)$", e, re.I)]
        if ems:
            data["邮箱"] = ems[0]
        if not data.get("主营产品"):
            mp = re.search(r"主营产品[：:]\s*([\u4e00-\u9fa5A-Za-z0-9、,，/ ]{4,120})", visible)
            if mp:
                data["主营产品"] = mp.group(1).strip()[:200]
        # 「关于我们」页的工商信息（仅补空缺，不强写）
        if from_about:
            for key, pat, lim in (("经营模式", r"企业性质[：:]?\s*([^\s，,；;]{2,20})", 30),
                                  ("所在区域", r"(?:公司地址|地\s*址)\s*[：:]\s*([\u4e00-\u9fa5]{2,10}(?:省[\u4e00-\u9fa5]{2,8}市)?)", 30)):
                gm = re.search(pat, visible)
                if gm and not data.get(key):
                    data[key] = gm.group(1)[:lim]
        data["公司网址"] = url
        return data

    @staticmethod
    def _visible_text(html):
        t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.I | re.S)
        # 先剥标签再剥 URL：反过来会把 href="https://…"> 的收尾引号删掉，
        # 下一轮 <[^>]+> 就会把「>公司名 地址：…」整段当标签吞掉（顺企网同款实测踩坑）。
        t = re.sub(r"<[^>]+>", " ", t)
        t = re.sub(r"https?://\S+", " ", t)
        return re.sub(r"\s+", " ", t.replace("&nbsp;", " "))
