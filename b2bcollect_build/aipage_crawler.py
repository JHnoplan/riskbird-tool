"""aipage_crawler.py —— 爱采购搜货网(b2b.aipage.com) 关键词搜索采集器（纯 HTTP 版）。

b2b.aipage.com 是百度爱采购(b2b.baidu.com) 的 SEO 反代壳，但实测**数据并不依赖 JS**：
实机探测校准（与前四站同思路：能裸 HTTP 绝不开标签）：
  ✅ 搜索首屏 GET `https://b2b.aipage.com/s?q=<关键词>` 即回服务端注水的
     `window.data = {…productList…}`（221KB，之前"JS壳无数据"的判断是只找了 DOM 卡片
     造成的误判；产品卡在 CSR 模板里，但数据 JSON 一直都在原始 HTML 中）。
     页内 `window.loginStatus.csrf_token` 是后续 AJAX 的门票。
  ✅ 翻页走 `GET /s/a?ajax=1&csrf_token=<32位>&q=<kw>&o=0&p=<N>&f=[]&s=30&adn=0&resType=product`
     （浏览器点"下一页"实抓参数；p 即页码，实测 p=20 正常、p=60 productList 为空，
     越界自然终止；URL 上的 pn/page/pageNumber 等参数均无效，唯一有效的是接口 + p）。
  ✅ 联系方式在**进店联系方式页**（用户实机指认的路径）：
     `GET /shop?xzhid=<店铺id>&tpath=contact` 同样注水 `window.data`：
     `cardInfo` 直接明文给出 联系人/手机/固话/邮箱/微信/QQ/地址，无需调任何
     取电密文接口（contact_info 解密链路可整体绕开）；`entInfo` 补
     公司全名/主营(mainProduct)/地址/爱企查URL。aipage 与 b2b.baidu.com 两域
     该页内容一致，统一走 aipage 域。
  ⚠ 列表产品卡按 xzhid(店铺id) 去重后才是公司维度；advert=1 为广告卡。

号码/邮箱一律在 JSON 字段值上提取；固话形态的 phoneNumber 归入电话列。
"""
import json
import re
import time
from urllib.parse import quote

import requests

from cdp_base import (CdpSiteCrawler, PHONE_MOBILE_RE, PHONE_TEL_RE, EMAIL_RE, clean_address,
                      parse_contact_field, is_placeholder_number, dept_only)

HOME_URL = "https://b2b.aipage.com/"
SEARCH_URL = "https://b2b.aipage.com/s?q="
API_URL = "https://b2b.aipage.com/s/a"
SHOP_URL = "https://b2b.aipage.com/shop"
BAIDU_SHOP_URL = "https://b2b.baidu.com/shop"

# 注水的页面数据：<script> 里 window.data = {…}; 到行尾分号为止
_WIN_DATA_RE = re.compile(r"window\.data\s*=\s*(\{.*?\});\s*\n", re.S)
_CSRF_RE = re.compile(r'"csrf_token"\s*:\s*"([0-9a-f]{32})"')

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
# 平台/渠道自有邮箱域（不是公司联系方式）
_JUNK_MAIL = ("baidu.com", "bcebos.com", "aipage")


class AipageCrawler(CdpSiteCrawler):
    PLATFORM_NAME = "爱采购"
    PLATFORM_SLUG = "aipage"
    BASE_URL = HOME_URL
    HOME_DOMAIN = "aipage.com"
    NEEDS_BROWSER = False          # 纯 HTTP，不占用浏览器标签
    # 本站只有 IP 限流型拦截（无验证码）：等冷却就行，不为它拉起 Chrome 窗口
    BLOCK_OPEN_BROWSER = False

    def __init__(self, port=9255):
        super().__init__(port)
        self.delay_between = 2.0
        self.jitter = 1.0
        self.max_pages = 0         # 不限页数：翻到没有结果为止
        self._session = self._new_session()

    def _new_session(self):
        s = requests.Session()
        s.headers.update({
            "User-Agent": _UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": HOME_URL,
        })
        return s

    # ── 旁路浏览器：本站不占用标签、不启动 Chrome ──────────────
    def start_browser(self, headed=True):
        return True

    def ensure_tab(self, log=None):
        return True

    def close_browser(self):
        return None

    def _is_session_alive(self):
        return True

    # ── 裸 HTTP：偶发连接重置/瞬时非 200 → 退避重建 Session 重试 ──
    def probe_block(self):
        """能力探测：重发一次真实搜索页，能拿到注水的 window.data 才算解除。"""
        try:
            html, _ = self._fetch(SEARCH_URL + quote("公司"), tries=1, guarded=False)
            if self._window_data(html) is not None:
                self._block_reason = ""
                return ""
        except Exception:
            pass
        return self._block_reason or "爱采购仍在限流状态"

    def _fetch(self, url, tries=4, headers=None, guarded=True):
        last = ""
        for i in range(max(1, tries)):
            try:
                r = self._session.get(url, timeout=25, headers=headers)
                self._mark_activity()
                block = self.http_block_reason(r.status_code, r.text, url)
                if block:
                    # 防采/限流插页：对着它重试只会加重风控，直接交看门狗
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
                self._session.close()
                self._session = self._new_session()
            except Exception as e:
                last = str(e).splitlines()[0][:80]
            if i < tries - 1:
                time.sleep(1.2 * (i + 1))
        if guarded and self._block_reason:
            if self.handle_http_block(url):
                self._block_reason = ""
                return self._fetch(url, tries=tries, headers=headers, guarded=False)
        self._log(f"  ⚠ 爱采购请求失败（{last}）：{url[:80]}")
        return "", url

    @staticmethod
    def _window_data(html):
        """解析原始 HTML 里服务端注水的 window.data JSON。"""
        if not html:
            return None
        m = _WIN_DATA_RE.search(html)
        if not m:
            return None
        try:
            return json.loads(m.group(1))
        except Exception:
            return None

    # ── 列表 ───────────────────────────────────────────────────
    def parse_list(self, data, kw):
        """productList → 公司条目（按 xzhid 去重；同店多产品取首卡）。"""
        out, seen = [], set()
        if not isinstance(data, dict):
            return out
        for p in data.get("productList") or []:
            if not isinstance(p, dict) or p.get("advert"):
                continue
            sid = str(p.get("xzhid") or "").strip()
            name = (p.get("fullProviderName")
                    or (p.get("tpProvider") or {}).get("name") or "").strip()
            if not sid or not name or len(name) < 4:
                continue
            if sid in seen or name in seen:
                continue
            seen.add(sid)
            seen.add(name)
            shop = f"{SHOP_URL}?xzhid={sid}&tpath=contact"
            main = ";".join(t for t in re.split(r"[;；]", p.get("prodTag") or "") if t)[:200]
            out.append({
                "公司名称": name[:60],
                "公司ID": sid,
                "memberId": sid,
                "公司网址": f"{BAIDU_SHOP_URL}/{sid}",
                "详情URL": shop,
                "主营产品": main,
                "所在区域": (p.get("location") or "").strip(),
                "标题": (p.get("fullName") or name)[:80],
            })
        return out

    # ── 翻页：首访拿 csrf_token，之后逐页调 /s/a 接口 ──────────
    def _api_page(self, kw, page, ctx):
        if "csrf" not in ctx:
            html, _ = self._fetch(SEARCH_URL + quote(kw))
            data = self._window_data(html)
            m = _CSRF_RE.search(html or "")
            if not m:
                return None
            ctx["csrf"] = m.group(1)
            if data is not None:
                ctx["p1"] = data
        if page == 1 and ctx.get("p1") is not None:
            return ctx["p1"]
        url = (f"{API_URL}?ajax=1&csrf_token={ctx['csrf']}&q={quote(kw)}"
               f"&o=0&p={page}&f=%5B%5D&s=30&adn=0&resType=product")
        html, _ = self._fetch(url, headers={
            "Referer": SEARCH_URL + quote(kw),
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
        })
        try:
            j = json.loads(html) if html else None
        except Exception:
            j = None
        if isinstance(j, dict) and j.get("status") == 0 and isinstance(j.get("data"), dict):
            return j["data"]
        return None

    def _iter_keyword(self, kw):
        ctx = {}
        stale = 0
        page = 0
        while not self._stop_requested:
            page += 1
            if self.max_pages and page > self.max_pages:
                return
            while self._pause_requested and not self._stop_requested:
                time.sleep(0.5)
            data = self._api_page(kw, page, ctx)
            if data is None:
                # 接口异常（csrf 失效/风控页）：换会话重试一次，再不行停该词
                if page == 1:
                    ctx.clear()
                    self._session.close()
                    self._session = self._new_session()
                    data = self._api_page(kw, 1, ctx)
                if data is None:
                    self._log(f"  「{kw}」第 {page} 页接口无数据，停止翻页")
                    return
            entries = self.parse_list(data, kw)
            self.stat_pages += 1
            self.stat_listed += len(entries)
            if not entries:
                self._log(f"  「{kw}」第 {page} 页无公司结果，停止翻页")
                return
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
                # 早过滤只用产品信号（prodTag 弱信号时 fail-open），判定交给详情复核
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

    # ── 详情：进店联系方式页 window.data.cardInfo + entInfo ────
    def crawl_detail(self, stub):
        rec = self._base_rec(stub)
        sid = stub.get("公司ID") or stub.get("memberId") or ""
        if not sid:
            rec["状态"] = "详情页导航失败"
            return rec
        url = f"{SHOP_URL}?xzhid={sid}&tpath=contact"
        rec["详情URL"] = url
        html, _ = self._fetch(url)
        data = self._window_data(html)
        if data is None:
            if not html:
                rec["状态"] = "详情页导航失败"
            else:
                rec["状态"] = "详情解析异常"
            return rec
        for k, v in self._parse_shop(data, stub).items():
            if v and not rec.get(k):
                rec[k] = v
        if not self.detail_relevant(rec):
            rec["状态"] = "类目不符(已过滤)"
            return rec
        has_phone = bool(rec.get("电话") or rec.get("手机"))
        has_email = bool(rec.get("邮箱"))
        rec["状态"] = ("成功" if (has_phone or has_email)
                       else ("无电话(未公开)" if rec.get("地址") or rec.get("公司网址") else "无联系方式"))
        return rec

    @staticmethod
    def _parse_shop(data, stub):
        """进店联系方式页注水 JSON → 记录字段。"""
        card = data.get("cardInfo") or {}
        ent = data.get("entInfo") or {}
        addr_obj = card.get("addressV2") or {}
        data_out = {}

        name = (ent.get("fullname") or data.get("entName") or "").strip()
        if name and re.search(r"[\u4e00-\u9fa5]", name) and len(name) >= 4:
            data_out["公司名称"] = name[:60]

        cn = str(card.get("contactName") or "")
        # 实测该字段脏值多：直接填手机号「18962536677」、带营销尾巴「王经理推销勿扰」——
        # 统一走 parse_contact_field：纯号码作废、截掉尾巴、顺带拆出岗位。
        cname, cpos = parse_contact_field(cn)
        if cname:
            data_out["联系人"] = cname
        if cpos:
            data_out["岗位"] = cpos[:20]
        # 商家在联系人框里填的是部门（与 mic 同形）：名字作废、部门归岗位列
        if not cname and not cpos:
            d = dept_only(cn)
            if d:
                data_out["岗位"] = d[:20]

        # 手机号 / 固话：phoneNumber 可能是手机也可能是固话形态，按形态归列
        mobiles, tels = [], []
        for f in ("phoneNumber", "telNumber", "wechatNumber"):
            for v in re.split(r"[/、,;]", str(card.get(f) or "")):
                v = v.strip()
                if not v:
                    continue
                if PHONE_MOBILE_RE.fullmatch(v) or re.fullmatch(r"1[3-9]\d{9}", v):
                    if v not in mobiles and not is_placeholder_number(v):
                        mobiles.append(v)
                elif PHONE_TEL_RE.fullmatch(v) or re.fullmatch(r"0\d{2,3}-?\d{7,8}", v):
                    if v not in tels and not is_placeholder_number(v):
                        tels.append(v)
        if mobiles:
            data_out["手机"] = " / ".join(mobiles[:3])
        if tels:
            data_out["电话"] = " / ".join(tels[:3])

        em = str(card.get("email") or "").strip()
        if em and EMAIL_RE.search(em) and not any(j in em.lower() for j in _JUNK_MAIL):
            data_out["邮箱"] = EMAIL_RE.search(em).group(0)

        addr = ""
        for src in (addr_obj.get("full") if isinstance(addr_obj, dict) else "",
                    card.get("externalAddress"), ent.get("address")):
            src = re.sub(r"\s+", " ", str(src or "")).strip()
            if src:
                addr = src
                break
        addr = clean_address(addr)
        # full 里可能粘连"省+市"重复段或尾挂厂名，clean_address 截噪后再限长
        if addr:
            data_out["地址"] = addr

        # 主营产品去重（实测同一产品串会重复 5 遍，堆在表里既难读又占满 200 字上限）
        prods = [p.strip() for p in re.split(r"[;；,，、]", str(ent.get("mainProduct") or "")) if p.strip()]
        mp = ";".join(dict.fromkeys(prods))
        if mp.strip():
            data_out["主营产品"] = mp[:200]

        # 所在区域：以 entInfo 的省/市（企业主页登记的属地）为准，列表页 stub 带的
        # 区域只是投放地（实测苏州公司被写成「云南曲靖」，与地址行矛盾）。
        loc = str(ent.get("province") or "") + str(ent.get("city") or "")
        loc = re.sub(r"\s+", "", loc) or re.sub(r"\s+", "", stub.get("所在区域") or "")
        if loc:
            data_out["所在区域"] = loc[:30]

        xin = str(ent.get("xin_url") or "").strip()
        if xin.startswith("http"):
            data_out["爱企查URL"] = xin
        sid = str(data.get("xzhId") or stub.get("公司ID") or "")
        if sid:
            data_out["公司网址"] = f"{BAIDU_SHOP_URL}/{sid}"
        return data_out
