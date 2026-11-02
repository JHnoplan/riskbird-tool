"""solarbe_crawler.py —— 索比光伏网企业库(company.solarbe.com) 关键词搜索采集器（纯 HTTP 版）。

站点是 Destoon CMS 的企业名录，2026-11-02 实机探测校准（能裸 HTTP 绝不开标签）：
  ✅ 列表：GET `https://company.solarbe.com/search.php?kw=<词>&catid=<行业>&areaid=<地区>&page=<N>`
     免登录明文，每张 `qyk_sublistleft` 卡片给 公司名/经营模式/主营产品/公司产地/
     是否核实/VIP 等级，20 家一页，页头「共找到 N 条」给本片总数。
  ⚠ **翻页硬上限 6 页**：实测任意切片第 7 页起返回的内容与第 6 页逐字相同
     （走查过 1~12/50/664 页），也就是每片最多能取回 120 家。所以全库 13285 家
     不能靠“一个词猛翻页”拿，必须**按切片细分**：行业 catid 15 个 × 地区 areaid 35 个。
     本采集器按“先整片、发现被 120 截断就顺着未用过的维度再切一刀”的广度优先展开，
     单词请求量由 KW_REQUEST_BUDGET 兜底，不会失控。
  ⚠ 联系方式桌面版被 Destoon 图片化：`api/image.png.php?auth=<密文>` 渲染电话/手机/邮箱，
     裸 HTTP 只能拿到一串图片地址。但**模板是按 User-Agent 切的**（与 URL 参数无关）：
     用 iPhone UA 请求同一个 `/contact/` 页，`<div class="content bd-t">` 里直接给
     明文「公司名称/公司地址/公司电话/联 系 人」，邮箱放在 `mailto:`、号码另在 `tel:` 里各写一遍。
     所以详情必须带手机 UA 取，**无需登录、无需图像识别**（实测抽样 77 家：
     名称 100%、联系人 100%、任一电话 95%、邮箱 39%）。
  ⚠ 偶发 HTTP 200 + 0 字节空正文（同一时刻别家正常）：按“可重采的导航失败”处理，
     绝不当“这家公司没联系方式”写进种子库锁死；连续多页都空才判 IP 限流交看门狗。

登记的三项要求是 公司名称/联系人/手机电话，邮箱为加分项，全部在同一个详情页里一次拿齐。
"""
import re
import time
import html as _htmlmod
from collections import deque
from urllib.parse import quote

import requests

from cdp_base import (CdpSiteCrawler, PHONE_MOBILE_RE, PHONE_TEL_RE, EMAIL_RE, clean_address,
                      parse_contact_field, is_placeholder_number, dept_only, compose_position,
                      strip_name_marketing_tail, strip_country_code, HONORIFIC_ONLY_RE)

HOME_URL = "https://company.solarbe.com/"
SEARCH_URL = "https://company.solarbe.com/search.php"
CONTACT_TAIL = "/contact/"

# 桌面 UA 用于列表（卡片结构在桌面模板里），手机 UA 用于详情（只有手机版给明文）
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
_UA_MOBILE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 "
              "(KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1")

# 企业库行业切片（search.php?catid=）：2026-11-02 从筛选栏实测取值与各家数量，
# 数量会漂移，这里只保留“切片维度”本身，不作为阈值依据。
CATIDS = {
    4846: "仪器仪表", 299: "光伏应用产品", 1434: "光伏原材料", 1435: "薄膜原料",
    1437: "光伏生产设备", 1438: "控制器/逆变器", 1439: "电站配套设备", 1440: "光伏系统安装",
    1441: "行业服务", 1557: "晶体硅组件生产商", 1558: "薄膜组件生产商",
    1559: "聚光及其他材质组件生产商", 1572: "BIPV光伏建筑", 1573: "太阳能热水器",
    1574: "其他未分类",
}
# 地区切片（search.php?areaid=）：31 省市 + 港澳台 + 海外（共 35 个）
AREAIDS = {
    100: "北京", 101: "上海", 102: "天津", 103: "重庆", 104: "河北", 105: "山西",
    106: "内蒙古", 107: "辽宁", 108: "吉林", 109: "黑龙江", 110: "江苏", 111: "浙江",
    112: "安徽", 113: "福建", 114: "江西", 115: "山东", 116: "河南", 117: "湖北",
    118: "湖南", 119: "广东", 120: "广西", 121: "海南", 122: "四川", 123: "贵州",
    124: "云南", 125: "西藏", 126: "陕西", 127: "甘肃", 128: "青海", 129: "宁夏",
    130: "新疆", 131: "香港", 132: "澳门", 133: "台湾", 1300026: "海外",
}

_TOTAL_RE = re.compile(r"共找到[^0-9]{0,40}([\d,]+)")
# 列表卡片：qyk_sublistleft 块里的公司锚点（href 带 slug，文本是公司名）
_CARD_ANCHOR = re.compile(
    r'href="https?://company\.solarbe\.com/([a-z0-9_-]+)/"[^>]*>\s*([^<]{4,60}?)\s*</a>')
_CARD_FIELD = re.compile(r"<span>(公司名称|经营模式|主营产品|公司产地|企业地址|客户群体|企业资料)"
                         r"：</span>(.*?)</li>", re.S)
_CONTENT_BLOCK = re.compile(r'class="content bd-t">(.*?)</div>', re.S)
_TEL_HREF = re.compile(r'href="tel:([^"]+)"')
_MAILTO = re.compile(r'mailto:([^"\'>?\s]+)')
# 平台自己的邮箱不是公司联系方式
_JUNK_MAIL = ("solarbe.com", "solarbe.cn", "destoon")


class SolarbeCrawler(CdpSiteCrawler):
    PLATFORM_NAME = "索比光伏网"
    PLATFORM_SLUG = "solarbe"
    BASE_URL = HOME_URL
    HOME_DOMAIN = "solarbe.com"
    NEEDS_BROWSER = False          # 纯 HTTP，不占用浏览器标签
    BLOCK_OPEN_BROWSER = False     # 本站只会限流/空白页，没有可人工点的滑块
    MIN_PAGE_CHARS = 1200          # 手机版联系页 5.8KB、桌面列表 75KB，低于此就是异常页
    # 单关键词最多发多少个列表请求（切片展开的总量兜底，避免长尾行业把一轮拖没完）
    KW_REQUEST_BUDGET = 240
    # 本站被限的形态就是「200 + 空正文」，实测 2s/请求 是安全节奏；界面设得更小就钳上来，
    # 免得用户拉小间隔把出口 IP 打进一直空白页（与八方的 4s 下限同理）
    MIN_DELAY = 2.0

    def __init__(self, port=9255):
        super().__init__(port)
        self.delay_between = 2.0
        self.jitter = 1.0
        self.max_pages = 0
        self._session = self._new_session()
        self._empty_streak = 0

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

    def probe_block(self):
        """能力探测：重发一次真实搜索页，能解析出公司卡片才算解除。
        只看状态码会把空白页当已通（本站的异常正是 200 + 空正文）。"""
        try:
            html = self._fetch(f"{SEARCH_URL}?kw={quote('光伏')}&catid=0&areaid=0&page=1",
                               tries=1, guarded=False)
            if html and self.parse_list(html, "光伏"):
                self._block_reason = ""
                return ""
        except Exception:
            pass
        return self._block_reason or "索比光伏网仍在限流状态"

    # ── 裸 HTTP 取页 ───────────────────────────────────────────
    def _fetch(self, url, tries=4, ua=None, guarded=True):
        """桌面/手机两套 UA 共用一条会话：详情靠手机 UA 才拿得到明文联系方式。
        200 但正文小于 MIN_PAGE_CHARS 是本站的异常形态（实测同一时刻别家正常），
        先重建会话重试；连续多页都空才升格为 IP 级限流交看门狗，绝不静默当空数据。"""
        last = ""
        for i in range(max(1, tries)):
            try:
                self._pace_before_request()
                headers = {"User-Agent": ua} if ua else None
                r = self._session.get(url, timeout=25, headers=headers)
                self._mark_activity()
                block = self.http_block_reason(r.status_code, r.text, url, r.headers)
                if block:
                    self._block_reason = block
                    last = f"HTTP {r.status_code}（防采插页）"
                    break
                if r.status_code == 200 and len(r.text or "") >= self.MIN_PAGE_CHARS:
                    self._block_reason = ""
                    self._empty_streak = 0
                    return r.text
                last = f"HTTP {r.status_code} 正文 {len(r.text or '')} 字节"
                if r.status_code == 404:
                    return ""
                if r.status_code == 200:
                    self._empty_streak += 1
                    self._session.close()
                    self._session = self._new_session()
            except requests.exceptions.ConnectionError as e:
                last = str(e).splitlines()[0][:80] or "连接被重置"
                self._session.close()
                self._session = self._new_session()
            except Exception as e:
                last = str(e).splitlines()[0][:80]
            if i < tries - 1:
                time.sleep(1.2 * (i + 1))
        if self._empty_streak >= 3 and not self._block_reason:
            self._block_reason = "站点连续返回空白页（IP 级限流，等冷却即可）"
            self._block_kind = "ip"
        if guarded and self._block_reason:
            if self.handle_http_block(url):
                self._block_reason = ""
                self._empty_streak = 0
                return self._fetch(url, tries=tries, ua=ua, guarded=False)
        self._log(f"  ⚠ 索比光伏网请求失败（{last}）：{url[:80]}")
        return ""

    # ── 列表 ───────────────────────────────────────────────────
    def build_search_url(self, kw, page, catid=0, areaid=0):
        return (f"{SEARCH_URL}?kw={quote(str(kw))}&catid={int(catid or 0)}"
                f"&areaid={int(areaid or 0)}&page={int(page)}")

    def parse_list(self, html, kw):
        """qyk_sublistleft 卡片 → 公司条目（按 slug 去重）。
        卡片里的 slug 才是公司主页，导航/推荐位那些锚点不在卡片内，天然被排除。"""
        out, seen = [], set()
        for chunk in (html or "").split("qyk_sublistleft")[1:]:
            m = _CARD_ANCHOR.search(chunk)
            if not m:
                continue
            slug = m.group(1).strip().lower()
            name = _htmlmod.unescape(m.group(2)).strip()
            if not slug or slug in seen or len(name) < 4:
                continue
            fields = {k.strip(): _htmlmod.unescape(re.sub(r"<[^>]+>", "", v)).strip()
                      for k, v in _CARD_FIELD.findall(chunk)}
            seen.add(slug)
            home = f"http://company.solarbe.com/{slug}/"
            out.append({
                "公司名称": name[:60],
                "公司ID": slug,
                "memberId": slug,
                "公司网址": "",
                "详情URL": home + "contact/",
                "主营产品": fields.get("主营产品", "")[:200],
                "经营模式": fields.get("经营模式", "")[:20],
                "所在区域": re.sub(r"[/\s]+", "", fields.get("公司产地", ""))[:30],
                "地址": fields.get("企业地址", "")[:200],
                "是否核实": "已核实" if "已核实" in fields.get("企业资料", "") else "未核实",
                "标题": name[:80],
            })
        return out

    def slice_total(self, html):
        """本页标称总条数（“共找到 N 条”，数字被 <b class="red"> 包着，不能按纯文本找）。"""
        m = _TOTAL_RE.search(html or "")
        return int(m.group(1).replace(",", "")) if m else -1

    # ── 翻页：切片广度优先展开，绕开 6 页硬上限 ────────────────
    def _expand(self, catid, areaid):
        """被 120 家截断的切片，顺着还没用过的维度再切一刀。"""
        if not catid:
            return [(c, areaid) for c in CATIDS]
        if not areaid:
            return [(catid, a) for a in AREAIDS]
        return []                     # 已经细到「行业×地区」，只能接受 120 家的截断

    def _iter_keyword(self, kw):
        """切片广度优先：每一片先老老实实翻满 6 页（120 家）把拿得到的都拿到手，
        翻完发现标称数还是被单片上限截断了，才顺着还没用过的维度再切一刀补量。
        （不能一在第 1 页看到「共 575 家」就丢下本片去细分：行业片、地区片层层都超上限，
        于是一片数据都没入库，一个关键词跑完一家都不产出。）"""
        queue = deque([(0, 0)])
        split = set()                 # 已经细分过的片（每片只切一刀，避免反复入队）
        budget = self.KW_REQUEST_BUDGET
        yielded = 0
        while queue and not self._stop_requested and budget > 0:
            catid, areaid = queue.popleft()
            prev_sig = None
            stale = 0
            total = -1                # 本片标称总条数
            got = 0                   # 本片实际列出的条数
            for page in range(1, 7):          # 实测第 7 页起复制第 6 页，翻到 6 就到底
                if self._stop_requested or budget <= 0:
                    break
                while self._pause_requested and not self._stop_requested:
                    time.sleep(0.5)
                budget -= 1
                html = self._fetch(self.build_search_url(kw, page, catid, areaid))
                entries = self.parse_list(html, kw)
                self.stat_pages += 1
                self.stat_listed += len(entries)
                sig = tuple(e["公司ID"] for e in entries)
                if not sig or sig == prev_sig:    # 空切片 / 内容与上一页逐字相同＝已到顶
                    break
                prev_sig = sig
                total = max(total, self.slice_total(html))
                got += len(sig)
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
                    # 早过滤只看卡片的主营产品（拿不到就 fail-open），判定交给详情页复核
                    if not self.is_relevant(kw, self.stub_relevance_text(e)):
                        skipped += 1
                        self._early_skipped += 1
                        self._commit_to_store({"状态": "类目不符(已过滤)", "关键词": kw,
                                               "公司名称": e["公司名称"], "memberId": mid})
                        continue
                    e["关键词"] = kw
                    new += 1
                    yielded += 1
                    yield e
                if new == 0:
                    stale += 1
                    if stale >= 2:
                        break               # 连续两页全是已采过的公司，这片再翻下去没有增量
                else:
                    stale = 0
                self._log(f"  「{kw}」行业{catid or '不限'}/地区{areaid or '不限'}"
                          f" 第 {page} 页：{len(entries)} 家，新增 {new}"
                          + (f"，早过滤 {skipped}" if skipped else ""))
                self._sleep()
            # 本片已经翻到底，标称数却比拿到手的多＝被 120 上限截断：再切一刀补量
            if total > got and (catid, areaid) not in split:
                subs = self._expand(catid, areaid)
                if subs:
                    split.add((catid, areaid))
                    queue.extend(subs)
                    self._log(f"  「{kw}」切片 行业{catid or '不限'}/地区{areaid or '不限'} 标称 {total} 家，"
                              f"单片只能取 {got} 家 → 按{'行业' if not catid else '地区'}细分成 "
                              f"{len(subs)} 片继续取")
        if not yielded and budget <= 0:
            self._log(f"  「{kw}」本轮列表请求量已达上限 {self.KW_REQUEST_BUDGET}，改日续采")

    # ── 详情：手机版联系页明文 ─────────────────────────────────
    def crawl_detail(self, stub):
        rec = self._base_rec(stub)
        slug = stub.get("公司ID") or stub.get("memberId") or ""
        if not slug:
            rec["状态"] = "详情页导航失败"
            return rec
        url = f"http://company.solarbe.com/{slug}/contact/"
        rec["详情URL"] = url
        html = self._fetch(url, tries=3, ua=_UA_MOBILE)
        if not html:
            rec["状态"] = "详情页导航失败"
            return rec
        if "公司不存在" in html:
            # 名录里已删除/改名的公司：终态，别再拿它占下一轮的采集量
            rec["状态"] = "无联系方式"
            return rec
        parsed = self._parse_contact(html, stub)
        for k, v in parsed.items():
            if v and not rec.get(k):
                rec[k] = v
        if not rec.get("公司名称") or len(rec.get("公司名称", "")) < 4:
            rec["公司名称"] = strip_name_marketing_tail(stub.get("公司名称", "")) or rec.get("公司名称", "")
        if not rec.get("所在区域"):
            rec["所在区域"] = stub.get("所在区域", "")
        # 官网只认正文「公司网址」栏（_parse_contact 已取）。实测扫页面里的 href 当兜底
        # 会拿到 http://wpa.qq.com/msgrd?uin=… 这类 QQ 聊天按钮，当成客户官网入库就是脏数据。
        if not self.detail_relevant(rec):
            rec["状态"] = "类目不符(已过滤)"
            return rec
        has_phone = bool(rec.get("电话") or rec.get("手机"))
        has_email = bool(rec.get("邮箱"))
        rec["状态"] = ("成功" if (has_phone or has_email)
                       else ("无电话(未公开)" if rec.get("地址") or rec.get("公司网址") else "无联系方式"))
        return rec

    @staticmethod
    def _parse_contact(html, stub):
        """手机版联系页 → 记录字段。
        正文块是 `<div class="content bd-t">公司名称：…<br/>公司地址：…</div>` 的纯文本行，
        号码同时在 `tel:` 链接里重复一次、邮箱只在 `mailto:` 链接里出现（没有标签）。"""
        out = {}
        m = _CONTENT_BLOCK.search(html)
        body = m.group(1) if m else ""
        kv = {}
        for line in re.sub(r"<[^>]+>", "\n", body).splitlines():
            line = line.strip()
            if "：" not in line or len(line) > 160:
                continue
            k, v = line.split("：", 1)
            k, v = re.sub(r"\s+", "", k), v.strip()
            if k and v and k not in kv:
                kv[k] = v
        name = kv.get("公司名称", "")
        if len(name) >= 4:
            out["公司名称"] = name[:60]
        raw_person = kv.get("联系人", "")
        cname, cpos = parse_contact_field(raw_person)
        if cname:
            out["联系人"] = cname
        dept = kv.get("部门(职位)", "") or kv.get("职位", "")
        # 括号里只写性别称谓（实测「赵福兴 （先生）」）就不立岗位列，免得存一排「先生」废数据
        pos = compose_position(dept, "" if HONORIFIC_ONLY_RE.match(cpos or "") else cpos)
        if pos:
            out["岗位"] = pos[:20]
        if not cname and not cpos:
            d = dept_only(raw_person)
            if d:
                out["岗位"] = d[:20]

        # 号码：正文标签行 + tel: 链接一起去重，按形态分手机/固话（传真行不参与，
        # 实测有人把传真填成号码形态，取进来会变成打不通的“电话”）
        blob = " ".join([kv.get(k, "") for k in ("公司电话", "联系电话", "手机号码", "手机")]
                        + _TEL_HREF.findall(html))
        mobiles, tels = [], []
        for v in re.split(r"[/、,;\s]+", blob):
            v = strip_country_code(v)
            if not v or is_placeholder_number(v):
                continue
            if PHONE_MOBILE_RE.fullmatch(v):
                if v not in mobiles:
                    mobiles.append(v)
            elif PHONE_TEL_RE.fullmatch(v):
                if v not in tels:
                    tels.append(v)
        if mobiles:
            out["手机"] = " / ".join(mobiles[:3])
        if tels:
            out["电话"] = " / ".join(tels[:3])

        mails = []
        for cand in _MAILTO.findall(html) + [kv.get("电子邮件", ""), kv.get("邮箱", "")]:
            em = (cand or "").strip().lower()
            g = EMAIL_RE.search(em) if em else None
            if g and not any(j in g.group(0) for j in _JUNK_MAIL):
                mails.append(g.group(0))
        if mails:
            out["邮箱"] = " / ".join(list(dict.fromkeys(mails))[:3])

        addr = clean_address(kv.get("公司地址", "") or stub.get("地址", ""))
        if addr:
            out["地址"] = addr
        site = kv.get("公司网址", "")
        if site and site not in ("http://", "无", "/"):
            out["公司网址"] = site if site.startswith("http") else f"http://{site}"
        prod = kv.get("主营产品", "") or stub.get("主营产品", "")
        if prod:
            out["主营产品"] = prod[:200]
        if kv.get("经营模式"):
            out["经营模式"] = kv["经营模式"][:20]
        return out
