"""gongchang_crawler.py —— 世界工厂网(qiye.gongchang.com) 关键词搜索采集器（纯 HTTP 版）。

2026-11-05 实机探测校准（与跨国采购网/顺企网同路线：能裸 HTTP 绝不开标签）：
  ✅ 搜索入口 GET `https://qiye.gongchang.com/search.php?kw=<关键词>&page=<N>`，
     SSR 直出（无 JS 挑战），20 家/页，每词上限 10 页；编码 UTF-8。
     列表页 107KB+，公司条目包在 `<div class="company2">` 内，联系方式（电话）明文可见。
  ⚠ 公司名内嵌 `<span class="highlight">` 高亮标签（搜索关键词命中部分），
     旧实现 `[^<]+` 正则只取到第一个 span 之前的碎片，21/23 关键词被判 0 结果。
     现改用 `company2-h1[^>]*>(.*?)</a>` + re.S 后剥标签，覆盖全部形态。
  ⚠ 频率限制：同一 session 连续请求约 10 次后触发，返 ~1714 字节薄壳（含 TOKEN= 字样）。
     实测恢复窗口极短（约 2 秒），请求间隔 ≥2s 可 100% 避免；
     采集器内置 session 重建 + 退避重试，薄壳绝不当作「本页无数据」。
  ✅ 省份参数 areaid（1~34）：单省时拼进搜索 URL（服务端筛），
     多省时放弃参数、退回数据层 province_filter 核验（trust-but-verify）。
     areaid 映射：1=北京 2=上海 3=天津 4=重庆 5=河北 6=山西 7=内蒙古 8=辽宁 9=吉林
     10=黑龙江 11=江苏 12=浙江 13=安徽 14=福建 15=江西 16=山东 17=河南 18=湖北
     19=湖南 20=广东 21=广西 22=海南 23=四川 24=贵州 25=云南 26=西藏 27=陕西
     28=甘肃 29=青海 30=宁夏 31=新疆 32=台湾 33=香港 34=澳门
  ✅ 详情页（店铺首页）：电话 100%（3~14 个号码）、邮箱 60%、地址 20%；
     列表页电话覆盖率仅 28%（前 3 页 ~50%，5 页后 = 0%），故 72% 公司须进详情补电话。
  ⚠ 平台假号：01970 开头（备案号）、17421 开头，必须过滤。
  ✅ chanpin.gongchang.com 有 SHA-256 PoW(12 bits) 质询，但 qiye 子域常态无挑战，
     本采集器只走 qiye 子域；万一命中 PoW 薄壳按频控重试处理（不破解，避免复杂化）。
"""
import html as htmllib
import re
import time
from urllib.parse import quote

import requests

from cdp_base import (CdpSiteCrawler, PHONE_MOBILE_RE, PHONE_TEL_RE, EMAIL_RE, clean_address,
                      parse_contact_field, is_placeholder_number, compose_position,
                      strip_name_marketing_tail, NET_FAIL_TAG)

HOME_URL = "https://qiye.gongchang.com/"
SEARCH_URL = "https://qiye.gongchang.com/search.php?kw={kw}&page={page}"

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# ── 省份 areaid 映射（从 qiye.gongchang.com 搜索页侧栏提取，2026-11-05 实机核实）──
GONGCHANG_AREAID = {
    "北京": 1, "上海": 2, "天津": 3, "重庆": 4,
    "河北": 5, "山西": 6, "内蒙古": 7, "辽宁": 8, "吉林": 9, "黑龙江": 10,
    "江苏": 11, "浙江": 12, "安徽": 13, "福建": 14, "江西": 15, "山东": 16,
    "河南": 17, "湖北": 18, "湖南": 19, "广东": 20, "广西": 21, "海南": 22,
    "四川": 23, "贵州": 24, "云南": 25, "西藏": 26, "陕西": 27, "甘肃": 28,
    "青海": 29, "宁夏": 30, "新疆": 31, "台湾": 32, "香港": 33, "澳门": 34,
}

# ── 平台假号（实测 01970 开头是备案号、17421 开头是平台生成的占位号）──
_FAKE_PHONE_PREFIXES = ("01970", "17421")

# ── 列表页正则 ──
# 公司条目块：<div class="company2"> ... </div>（按 company2 类分割）
# 公司名：class="company2-h1" 内的 <a> 标签，内部可能有 <span class="highlight"> 高亮
_NAME_RE = re.compile(r'class="company2-h1"[^>]*>\s*<a[^>]*>(.*?)</a>', re.S)
# 店铺 URL：href="https://qiye.gongchang.com/<slug>/" >进入店铺</a>
_SHOP_RE = re.compile(r'href="(https://qiye\.gongchang\.com/[^/"]+/)"[^>]*>进入店铺')
# 主营产品
_PROD_RE = re.compile(r'<span>主营：</span>(.*?)</div>', re.S)
# 成立年限 / 规模 / 注册资本
_YEAR_RE = re.compile(r'成立<em>([^<]+)</em>')
_SCALE_RE = re.compile(r'规模<em>([^<]+)</em>')
_CAPITAL_RE = re.compile(r'注册资本<em>([^<]+)</em>')
# 联系方式（列表页电话，明文写在 span 后面）
_CONTACT_RE = re.compile(r'<span>联系方式：</span>(.*?)(?:</div>|<)', re.S)
# 经营模式
_BIZ_RE = re.compile(r'title="([^"]*)"[^>]*><em></em></span>')
# 总页数
_TOTAL_PAGES_RE = re.compile(r'共(\d+)页')
# HTML 标签剥离
_TAG_RE = re.compile(r'<[^>]+>')
# 公司名开头的地级市（补「所在区域」）
_CITY_RE = re.compile(r"^([\u4e00-\u9fa5]{2,8}?(?:市|县|旗|州))")
# 薄壳/挑战页特征
_CHALLENGE_MARKS = ("TOKEN=", "VERIFY=", "挑战", "captcha")
# 薄壳体积上限（正常列表页 107KB+，薄壳 ~1714B）
_THIN_SHELL_MAX = 5000


class GongchangCrawler(CdpSiteCrawler):
    PLATFORM_NAME = "世界工厂网"
    PLATFORM_SLUG = "gongchang"
    BASE_URL = HOME_URL
    HOME_DOMAIN = "gongchang.com"
    NEEDS_BROWSER = False           # 纯 HTTP，不占用浏览器标签
    BLOCK_OPEN_BROWSER = False      # 本站没有可人工点的验证，频控只等冷却
    # 频控参数（2026-11-05 实测：间隔 2s 可 100% 避免触发，恢复窗口约 2 秒）
    MIN_DELAY = 2.0                 # 安全下限：低于 2s 会触发频控
    max_pages_default = 10          # 站方硬上限：每词最多 10 页
    MAX_PAGES_HARD_CAP = 10

    def __init__(self, port=9255):
        super().__init__(port)
        self.delay_between = 2.5
        self.jitter = 1.0
        self.max_pages = 0          # 0 = 不限（由 MAX_PAGES_HARD_CAP 兜底）
        self._session = self._new_session()
        self._thin_shell_streak = 0  # 连续薄壳计数（用来判断是否进入频控冷却）

    def _new_session(self):
        s = requests.Session()
        s.headers.update({
            "User-Agent": _UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": HOME_URL,
        })
        return s

    def reset_run_state(self):
        super().reset_run_state()
        self._thin_shell_streak = 0
        return True

    # ── 旁路浏览器：本站不占标签 ─────────────────────────────────
    def start_browser(self, headed=True):
        return True

    def _is_session_alive(self):
        return True

    # ── 省份参数 ────────────────────────────────────────────────
    def _province_areaid(self):
        """单省时返回 areaid 整数，多省/无省份返回 None。
        多省时站方参数只能指向一省，必须放弃参数直筛、退回数据层过滤。"""
        pv = str(self.province or "").strip()
        if not pv or "," in pv:
            return None
        return GONGCHANG_AREAID.get(pv)

    # ── URL 构造 ────────────────────────────────────────────────
    def build_search_url(self, kw, page):
        url = SEARCH_URL.format(kw=quote(str(kw)), page=max(1, int(page)))
        areaid = self._province_areaid()
        if areaid is not None:
            url += f"&areaid={areaid}"
        return url

    # ── 裸 HTTP 取页（带频控检测 + session 重建）─────────────────
    def _fetch(self, url, tries=4):
        """取页：检测薄壳/挑战页，触发频控时重建 session 退避重试。
        返回 (html, is_thin_shell)。is_thin_shell=True 时调用方应停止翻页。"""
        last = ""
        for i in range(max(1, tries)):
            try:
                r = self._session.get(url, timeout=20)
                self._mark_activity()
                if r.status_code == 200 and r.text:
                    text = r.text
                    # 薄壳/挑战页检测：体积小 + 含特征词
                    if len(text) < _THIN_SHELL_MAX and any(m in text[:2000] for m in _CHALLENGE_MARKS):
                        self._thin_shell_streak += 1
                        last = f"频控薄壳（{len(text)}B）"
                        # 重建 session 避免继续被打
                        self._session.close()
                        self._session = self._new_session()
                        if i < tries - 1:
                            wait = 2.0 * (i + 1)
                            self._log(f"  ↻ 世界工厂网触发频控，等待 {wait:.0f}s 后重试…")
                            time.sleep(wait)
                        continue
                    # 正常页面
                    self._thin_shell_streak = 0
                    return text, False
                elif r.status_code == 404:
                    return "", False
                else:
                    last = f"HTTP {r.status_code}"
                    if r.status_code in (403, 429, 503):
                        self._thin_shell_streak += 1
                        self._session.close()
                        self._session = self._new_session()
                        if i < tries - 1:
                            time.sleep(2.0 * (i + 1))
                        continue
            except requests.exceptions.ConnectionError as e:
                last = str(e).splitlines()[0][:80] or "连接被重置"
                self._session.close()
                self._session = self._new_session()
            except Exception as e:
                last = str(e).splitlines()[0][:80]
            if i < tries - 1:
                time.sleep(1.5 * (i + 1))
        self._log(f"  ⚠ 世界工厂网请求失败（{last}）：{url[:70]}")
        return "", self._thin_shell_streak > 0

    # ── 列表解析 ────────────────────────────────────────────────
    def parse_list(self, html, kw):
        """从 qiye.gongchang.com 搜索页提取公司条目。
        公司名必须处理内嵌 <span class="highlight"> 标签。"""
        out, seen = [], set()
        if not html:
            return out
        blocks = re.split(r'<div class="company2">', html)
        for block in blocks[1:]:
            rec = {}
            # 公司名（剥掉内嵌 highlight span）
            name_m = _NAME_RE.search(block)
            if name_m:
                raw_name = _TAG_RE.sub("", name_m.group(1)).strip()
                raw_name = htmllib.unescape(raw_name)
                rec["公司名称"] = raw_name[:60]
            if not rec.get("公司名称"):
                continue
            # 去重
            name_key = self._norm_name(rec["公司名称"])
            if name_key in seen:
                continue
            seen.add(name_key)
            # 店铺 URL
            shop_m = _SHOP_RE.search(block)
            if shop_m:
                rec["公司网址"] = shop_m.group(1)
                rec["详情URL"] = shop_m.group(1)
                # 从 URL 提取 slug 作为 memberId
                slug_m = re.search(r'gongchang\.com/([^/]+)/', shop_m.group(1))
                if slug_m:
                    rec["memberId"] = slug_m.group(1)
                    rec["公司ID"] = slug_m.group(1)
            # 主营产品
            prod_m = _PROD_RE.search(block)
            if prod_m:
                rec["主营产品"] = _TAG_RE.sub("", prod_m.group(1)).strip()[:200]
            # 成立年限
            year_m = _YEAR_RE.search(block)
            if year_m:
                rec["成立年限"] = year_m.group(1).strip()
            # 规模
            scale_m = _SCALE_RE.search(block)
            if scale_m:
                rec["规模"] = scale_m.group(1).strip()
            # 注册资本
            capital_m = _CAPITAL_RE.search(block)
            if capital_m:
                val = capital_m.group(1).strip()
                if val and val != "-":
                    rec["注册资本"] = val
            # 联系方式（列表页电话）
            contact_m = _CONTACT_RE.search(block)
            if contact_m:
                contact_text = _TAG_RE.sub("", contact_m.group(1)).strip()
                phones = self._extract_phones(contact_text)
                if phones:
                    rec["列表电话"] = " / ".join(phones)
            # 经营模式
            biz_m = _BIZ_RE.search(block)
            if biz_m:
                rec["经营模式"] = biz_m.group(1).strip()
            # 所在区域（从公司名提取地级市）
            rec["所在区域"] = self._region_from_name(rec["公司名称"])
            # 标题（用于相关性判定）
            rec["标题"] = rec.get("主营产品") or rec["公司名称"]
            out.append(rec)
        return out

    def slice_total(self, html):
        m = _TOTAL_PAGES_RE.search(html or "")
        return int(m.group(1)) if m else -1

    def list_has_more(self, html, page):
        """世界工厂网每词上限 10 页，超过后即使有数据也不翻。"""
        if not html:
            return False
        total = self.slice_total(html)
        if total > 0:
            return page < min(total, 10)
        # 没找到总数时按本页有条目判断
        return page < 10

    # ── 号码提取（过滤平台假号）──────────────────────────────────
    @staticmethod
    def _extract_phones(text):
        """从文本提取电话，过滤平台假号（01970/17421 开头）。"""
        phones = []
        for p in PHONE_MOBILE_RE.findall(str(text or "")):
            if not is_placeholder_number(p) and not p.startswith(_FAKE_PHONE_PREFIXES):
                phones.append(p)
        for p in PHONE_TEL_RE.findall(str(text or "")):
            if not is_placeholder_number(p) and not p.startswith(_FAKE_PHONE_PREFIXES):
                phones.append(p)
        return list(dict.fromkeys(phones))[:5]

    @staticmethod
    def _region_from_name(name):
        """从公司名开头的地级市补「所在区域」（列表页本身没有区域字段）。"""
        m = _CITY_RE.match(name or "")
        if not m:
            return ""
        return re.sub(r"(市|县|旗|州)$", "", m.group(1))[:12]

    # ── 列表翻页（纯 HTTP）──────────────────────────────────────
    def _iter_keyword(self, kw):
        """与跨国采购网同口径：取页必须过本类 _fetch（才有序控/频控检测），
        记账字段（stat_*、_seen_members、_early_skipped）与基类逐字对齐。"""
        empty = stale = 0
        page = 0
        while not self._stop_requested:
            page += 1
            if self.max_pages and page > self.max_pages:
                return
            if page > 10:   # 站方硬上限
                return
            while self._pause_requested and not self._stop_requested:
                time.sleep(0.5)
            url = self.build_search_url(kw, page)
            html, is_thin = self._fetch(url)
            if is_thin:
                self._log(f"  「{kw}」第 {page} 页触发频控薄壳，停止翻页（已采数据不丢）")
                return
            if not html:
                self._log(f"  「{kw}」第 {page} 页没取到页面，停止翻页")
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
                mid = e.get("公司ID") or e.get("memberId") or e.get("公司名称", "")
                key = f"{self.PLATFORM_SLUG}:{mid}"
                name = self._norm_name(e.get("公司名称", ""))
                nkey = f"{self.PLATFORM_SLUG}:name:{name}"
                if key in self._seen_members or nkey in self._seen_members \
                        or f"{self.PLATFORM_SLUG}:{kw}#{mid}" in self._seen_members:
                    seen += 1
                    self._incremental_skipped += 1
                    continue
                # 早过滤
                if not self.is_relevant(kw, self.stub_relevance_text(e)):
                    skipped += 1
                    self._early_skipped += 1
                    self._seen_members.add(f"{self.PLATFORM_SLUG}:{kw}#{mid}")
                    self._commit_to_store({"状态": "类目不符(已过滤)", "关键词": kw,
                                           "公司名称": e.get("公司名称", ""), "memberId": mid})
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

    # ── 详情：进店铺首页取电话/邮箱/地址 ─────────────────────────
    def crawl_detail(self, stub):
        """世界工厂网列表页电话覆盖率仅 28%，必须进详情页补电话/邮箱/地址。
        详情页就是店铺首页（qiye.gongchang.com/<slug>/），SSR 直出。"""
        rec = self._base_rec(stub)
        shop_url = stub.get("详情URL") or stub.get("公司网址") or ""
        if not shop_url:
            # 没有店铺 URL，只用列表页数据
            return self._finalize_rec(rec, stub)
        # 取详情页
        detail_html, is_thin = self._fetch(shop_url)
        if is_thin:
            # 频控：返回已有数据，状态标「详情被频控」
            rec["状态"] = "详情被频控(列表数据)"
            return self._finalize_rec(rec, stub)
        if not detail_html:
            rec["状态"] = "详情页导航失败"
            return self._finalize_rec(rec, stub)
        # 解析详情页
        data = self._parse_detail_page(detail_html)
        # 合并数据（详情页优先，列表页兜底）
        for k, v in data.items():
            if v and not rec.get(k):
                rec[k] = v
        # 列表页带过来的电话也要用
        if not rec.get("手机") and not rec.get("电话"):
            list_phone = stub.get("列表电话", "")
            if list_phone:
                mobiles, tels = self._split_numbers(list_phone)
                if mobiles:
                    rec["手机"] = " / ".join(mobiles[:3])
                if tels:
                    rec["电话"] = " / ".join(tels[:3])
        return self._finalize_rec(rec, stub)

    def _parse_detail_page(self, html):
        """从详情页提取联系方式。"""
        data = {}
        # 电话（过滤假号）
        phones = self._extract_phones(html)
        if phones:
            mobiles, tels = [], []
            for p in phones:
                if PHONE_MOBILE_RE.fullmatch(p):
                    mobiles.append(p)
                elif PHONE_TEL_RE.fullmatch(p):
                    tels.append(p)
                else:
                    # 按位数归类
                    digits = re.sub(r"\D", "", p)
                    if len(digits) == 11 and digits[0] in "1":
                        mobiles.append(digits)
                    elif len(digits) >= 7:
                        tels.append(p)
            if mobiles:
                data["手机"] = " / ".join(list(dict.fromkeys(mobiles))[:3])
            if tels:
                data["电话"] = " / ".join(list(dict.fromkeys(tels))[:3])
        # 邮箱（剔除平台邮箱）
        emails = EMAIL_RE.findall(html)
        real_emails = [e for e in emails
                       if not any(x in e.lower() for x in
                                  ['gongchang', 'gcimg', 'example', 'sentry', 'baidu', 'google'])]
        if real_emails:
            data["邮箱"] = " / ".join(list(dict.fromkeys(real_emails))[:3])
        # 地址
        addr_m = re.search(r'(?:地址|address|公司地址|注册地址)[^<]*[:：]\s*</span>\s*([^<]{5,120})',
                           html, re.I)
        if not addr_m:
            addr_m = re.search(r'(?:地址|address|公司地址|注册地址)[:：]\s*([^<]{5,120})', html, re.I)
        if not addr_m:
            # 尝试从 meta 或 JSON-LD 提取
            addr_m = re.search(r'"address"\s*:\s*"([^"]{5,120})"', html)
        if addr_m:
            addr = clean_address(htmllib.unescape(_TAG_RE.sub("", addr_m.group(1))))
            if addr:
                data["地址"] = addr
        # 公司名（详情页 title）
        title_m = re.search(r'<title>([^<]*?)(?:[_\-—|]|—)', html)
        if title_m:
            detail_name = _TAG_RE.sub("", title_m.group(1)).strip()
            if detail_name and len(detail_name) >= 4:
                data.setdefault("公司名称", detail_name[:60])
        # 联系人
        contact_m = re.search(r'联\s*系\s*人[：:]\s*([\u4e00-\u9fa5·A-Za-z]{2,10})', html)
        if contact_m:
            name, pos = parse_contact_field(contact_m.group(1).strip())
            if name:
                data["联系人"] = name
            if pos:
                data["岗位"] = pos[:20]
        # 主营产品（详情页可能有更多）
        main_m = re.search(r'主营.*?[:：]\s*(.*?)(?:</div>|<)', html, re.I)
        if main_m:
            prod = _TAG_RE.sub("", main_m.group(1)).strip()[:200]
            if prod:
                data.setdefault("主营产品", prod)
        return data

    def _finalize_rec(self, rec, stub):
        """最终记录整理：状态判定 + 相关性复核。"""
        # 确保公司名
        if not rec.get("公司名称"):
            rec["公司名称"] = stub.get("公司名称", "")
        # 补主营产品
        if not rec.get("主营产品"):
            rec["主营产品"] = stub.get("主营产品", "")
        # 补所在区域
        if not rec.get("所在区域"):
            rec["所在区域"] = self._region_from_name(rec.get("公司名称", ""))
        # 详情页相关性复核
        if not self.detail_relevant(rec):
            rec["状态"] = "类目不符(已过滤)"
            return rec
        # 状态判定
        has_phone = bool(rec.get("电话") or rec.get("手机"))
        has_email = bool(rec.get("邮箱"))
        if not rec.get("状态"):
            rec["状态"] = ("成功" if has_phone
                           else ("有联系方式(仅邮箱)" if has_email
                                 else "无联系方式"))
        return rec

    @staticmethod
    def _split_numbers(raw):
        """把号码串拆成 (手机列表, 固话列表)。"""
        mobiles, tels = [], []
        for v in re.split(r"[/、,;;\s]+", str(raw or "")):
            v = v.strip()
            if not v or is_placeholder_number(v) or v.startswith(_FAKE_PHONE_PREFIXES):
                continue
            if PHONE_MOBILE_RE.fullmatch(v) and v not in mobiles:
                mobiles.append(v)
            elif PHONE_TEL_RE.fullmatch(v) and v not in tels:
                tels.append(v)
        return mobiles, tels
