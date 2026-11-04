"""gi_crawler.py —— 跨国采购网(www.globalimporter.net) 关键词搜索采集器（纯 HTTP 版）。

2026-11-04 实机探测校准（与麦麦B2B/11467 同路线：能裸 HTTP 绝不开标签）：
  ⚠ 站点挂在 WebShield（国产 WAF）后面，未验证的请求一律吃 405~442 字节薄壳，正文只有一句
     JS：`setTimeout("JumpSelf()",700)` 里 `self.location="/?WebShieldSessionVerify=<token>"`，
     且响应头 `Set-Cookie:` 是**空的**（站方主动清会话）。过这一层的正确动作是
     **照 JS 原样跟一跳**，不是抄 Cookie（2026-11-04 现场实测，纠正本文件旧结论）：
       GET 首页 → 薄壳 + token
       → GET `/?WebShieldSessionVerify=<token>`（该页只回 361 字，仍不下发 Cookie）
       → 再 GET 首页＝92,790 字真首页并下发 ASPSESSIONIDxxxx；
         再 GET 搜索页＝36,285 字真列表（实测一次通过，零 JS 引擎，requests 即可）
     两个**全新 TCP 连接、零 Cookie** 的后续请求直接拿到真页，说明「已验证」记在
     **服务端的出口 IP 状态**上：不绑连接、不在 Cookie 名字里，抄 Cookie／换 UA 都替代不了
     那一跳。只把 token 抄进原来那个 URL 再请求确实仍不给数据（旧实测那句没错），
     缺的是「跟完之后要回头再取一次首页/搜索页」。
  ⚠ 详情页另有 **IP 配额**（实测原话两行）：
       「网站已启用防采集功能，非会员可浏览每个行业的前3页信息(或不超过50个内容页信息)」
       「Your IP has too many visits in a short time. Please continue browsing after 2 hours!」
     所以列表页就是数据主体，详情一条都不发（把 IP 配额全留给翻页）——实测列表页每页 20 家、
     20/20 都带「联系人 + 电话」，号码是**明文**，主营产品也在列表里，本就无需进详情。
  ⚠ WAF 后面**还有第二层**：安全狗「拦截页面」（HTTP 200 + 正文五千字，写着
     「您的请求过于频繁，已被网站管理员设置拦截！…请登录安全狗」）。基类比对插页词有个
     体积门槛（只扫小于 HTTP_BLOCK_MAX_BYTES=3000 的正文），而这一页五千字；话术又是
     「请求过于频繁」而词表里是「访问过于频繁」——**两重条件都没命中，整页被当正常内容返回**，
     parse_list 得 0 家 → 上层判「这个关键词没公司」→ stat_pages 照常自增 → 关键词被记成
     「已跑完」、下一轮自动跳过＝静默丢数据（正面违反「不得把风控页当空结果」）。
     修法是本站专用：_BLOCK_PAGE_MARKS 并入 HTTP_BLOCK_MARKS，并覆盖 http_block_reason
     做**无条件全文扫**（不看体积），命中就归 ip 处置、等站点放行，绝不写空记录。
  ⚠ 安全狗这层的**恢复节奏**和拦截本身一样要命（2026-11-04 实机量出两个窗口的账）：
     窗口一 08:38:14–08:47:28（9 分 14 秒）入库 99 家；被拦后约 90 分钟（08:47→10:17）
     才放行，其间旧代码每 60 秒探测一次共约 40 次，**放行时刻并未被推后**；窗口二
     10:17–10:22（5 分钟）入库 289 家 ≈ 58 家/分钟。所以「本站一天只能采几十条」彻底
     不成立，卡产能的只有两件事：站方冷却本身（实测 90 分钟）+ 工具的等待窗口比它短。
     曾猜「是探测自己把冷却续期」并把静默调到 20 分钟，已被上面这组实测推翻：20 分钟
     一探只会让放行之后白等 20 分钟，按 58 家/分钟等于每轮丢上千家。现在 BLOCK_POLL_IP
     =180s 一探、probe_block 单请求判定、BLOCK_MAX_WAIT_IP=10800s 跨过实测 90 分钟。
  ⚠ 上面那句只适用于**安全狗那一层**：它拦的才是出口 IP，不是 HTTP 指纹（2026-11-04 取证：
     真浏览器（共享 Chrome，完整执行 JS）打开首页与搜索页，标题同样是「拦截页面」；换 UA/补
     Referer/抄浏览器 Cookie 都无从谈起）。穷举过的入口也一样不给看：首页、用户给的
     sellindexindex.html、公司库分类页、sell_search.asp、m 站——全部同一形状。所以本站
     **不加浏览器通路**，只按 ip 等待恢复；冷却时长站方自己没说死（另一句原话是「2 小时后再看」），
     期间已采数据与断点都不丢。
  ⛔ 但**绝不能把 WebShield 薄壳也归到这一层**（2026-11-04 就是栽在这）：薄壳是「会话质询没走完」，
     跟一跳就解，等冷却等于永远不解。分诊口径：正文含 `WebShieldSessionVerify=` → 先按
     _bootstrap 跟随跳转重走（_shell_hits 限 2 次）；跟过仍只给薄壳/给出安全狗话术 → 才归 ip。
     两类混成一条日志的后果是界面显示「已等 420 秒、本站 0 家」，而真相是本地少发一个跳转请求。
  ✅ 列表：GET `/search_company.asp?keyword=<关键词按 GBK 百分号编码>&curpage=<N>`，20 家一页；
     页头「检索结果，相关企业 422家」。条目是两行 <tr>：
       `<td>N <a href="http://<slug>.cn.globalimporter.net">公司名</a></td>`
       `<td colspan=2>主营产品描述<br><font size=-1 color=9999FF>联系人:X<br>  电话: Y</font>`
     号码形态实测三种：纯手机「13392381815」、带区号固话「021-36110118」、
     **商家把手机拆成「区号-手机-尾号」写的脏号「010-13920758-444」**——这一串的同店商铺
     子域正好是 `w13920758444`，两个独立来源互证，说明本体就是 13920758444，按「0 位区号 +
     11 位手机」规则还原（见 _split_numbers，绝不凭空补数字）。
  ℹ 编码：HTTP 头写 charset=gb2312，正文按 gb18030 解最稳（gb2312 缺字会整段乱码）。
"""
import re
import time
import html as _htmlmod
from urllib.parse import quote

import requests

from cdp_base import (CdpSiteCrawler, PHONE_MOBILE_RE, PHONE_TEL_RE,
                      parse_contact_field, is_placeholder_number, dept_only, compose_position,
                      strip_name_marketing_tail, strip_country_code, HONORIFIC_ONLY_RE,
                      NET_FAIL_TAG)

HOME_URL = "https://www.globalimporter.net/"
SEARCH_URL = "https://www.globalimporter.net/search_company.asp?keyword={kw}&curpage={page}"

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# 条目锚点：序号 + 商铺子域 + 公司名（子域就是本站的公司唯一号，比 cdetail 的产品页 id 稳定）
_ROW_RE = re.compile(
    r"<td>\s*\d+\s*<a\s+href=\"http://([\w\-]+)\.cn\.globalimporter\.net\"[^>]*>([^<]{4,60})</a>", re.I)
# 联系块：联系人/电话写在同一个 <font size=-1 color=9999FF> 里（实测电话有时为空串）
_CONTACT_RE = re.compile(r"联系人[:：]\s*([^<]{0,40}?)<br>\s*(?:电\s*话|电话)[:：]\s*([^<]{0,80})", re.I)
# 主营产品：锚点之后的第一个 <td colspan=2> 正文（到 <br> 或 <font> 为止）
_PROD_RE = re.compile(r"<td[^>]*colspan\s*=\s*2[^>]*>(.{6,600}?)(?:<br>|<font)", re.S | re.I)
# 产品详情静态页（只当「详情URL」登记，不请求：IP 配额有限，进详情会把翻页配额吃掉）
_CDETAIL_RE = re.compile(r"(?:https?://www\.globalimporter\.net)?(/cdetail_\d+_\d+\.html)")
# 薄壳/配额页特征（442 与 541 字节两种，都远小于列表页 37KB）
_SHELL_MARKS = ("WebShieldSessionVerify", "防采集", "too many visits", "continue browsing",
                "会员注册", "您的IP")
# WebShield 薄壳里 JS 的跳转目标（`self.location="/?WebShieldSessionVerify=<token>"`）。
# 这一跳**必须真跟**：跟完服务端才把这个出口记成「已验证」，详见 _bootstrap 的实测记录。
_VERIFY_TOKEN_RE = re.compile(r"WebShieldSessionVerify=([A-Za-z0-9]+)")
_VERIFY_MAX_HOPS = 3
# 安全狗（WebShield 背后的另一层）的 IP 拦截页：正文 5140 字节，比基类「体积极小
# 才算插页」的门槛（3000）大，而且它的话术是「请求过于频繁」而不是基类词表里的「访问过
# 于频繁」——一词之差整页被当成正常页面递回去，解析出 0 家公司就被当成「这个关键词没结果」，
# 关键词还会被记成「已跑完」，下一轮直接跳过（实机 2026-11-04 踩到，详见 http_block_reason）。
_BLOCK_PAGE_MARKS = ("拦截页面", "请求过于频繁", "已被网站管理员设置拦截",
                     "安全狗", "服云")
# 平台自己灌的假号（实测列表页未出现，保留判据以防商家照抄模板）
_FAKE_NUMBERS = {"400-000-0000", "000-0000000", "13800138000"}
# 「区号 + 手机」混写的还原式：可选 0 开头 1~3 位区号，尾部必须是完整 11 位手机
_MOBILE_WITH_AREACODE_RE = re.compile(r"^(?:0\d{1,3})?(1[3-9]\d{9})$")
# 公司名开头的地级市（用来补「所在区域」，列表页本身没有区域字段）
_CITY_RE = re.compile(r"^([\u4e00-\u9fa5]{2,8}?(?:市|县|旗|州))")
_TAG_RE = re.compile(r"<[^>]+>")


class GiCrawler(CdpSiteCrawler):
    PLATFORM_NAME = "跨国采购网"
    PLATFORM_SLUG = "globalimporter"
    BASE_URL = HOME_URL
    HOME_DOMAIN = "globalimporter.net"
    NEEDS_BROWSER = False           # 纯 HTTP：首页拿 Cookie，之后全靠 requests
    # 本站没有可人工过的验证（薄壳是 WAF 会话质询、超配额是 IP 冷却），
    # 拉 Chrome 只会多一个被打的出口，所以一律不弹浏览器，交给冷却退避。
    BLOCK_OPEN_BROWSER = False
    # 冷却时长与探测节奏要按实测账来，不能照站方文案字面值定（2026-11-04 两个窗口）：
    #   窗口一 08:38:14–08:47:28 入库 99 家 → 08:47 被拦
    #   冷却   08:47 → 10:17 放行，实测约 90 分钟（站方原话是「after 2 hours」的上限口径）
    #   窗口二 10:17–10:22 入库 289 家，约 58 家/分钟
    # 关键取证：被拦那 47 分钟里旧实现每 60 秒探测一次、每探还先走 _bootstrap 多发
    # 1~3 个请求，共约 40 次，放行时刻并没有因此后移——「探测会续期冷却」是误判，据此
    # 把静默调到 20 分钟反而是新缺陷：放行后每多等一分钟就少采一分钟（58 家/分钟）。
    # 所以静默取 180 秒（比实测无害的 60 秒再保守 3 倍，仍远低于人工浏览速率），而
    # 等待上限必须大于实测 90 分钟——旧值 2400 秒（40 分钟）小于冷却，等于每次都刚好
    # 错开放行时刻（本次能恢复是超时后下一个关键词恰好已放行，属运气不是设计）。
    BLOCK_POLL_IP = 180             # 首次恢复探测前静默 3 分钟（秒）
    BLOCK_POLL_IP_MAX = 600         # 退避上限：最长 10 分钟探一次
    BLOCK_MAX_WAIT_IP = 10800       # 单次最多等 3 小时：跨过实测 90 分钟与站方 2 小时
    HTTP_BLOCK_MARKS = CdpSiteCrawler.HTTP_BLOCK_MARKS + _SHELL_MARKS + _BLOCK_PAGE_MARKS
    HTTP_BLOCK_MAX_BYTES = 3000     # 薄壳 442/541B vs 列表页 37KB，按体积判最稳
    MIN_PAGE_CHARS = 3000
    # 非会员「每个行业前 3 页」是站方公开策略（实测原话），所以这一项不是偏好而是硬上限：
    # 界面下发「不限页数」时必须钉住 3 页，否则一个词就能把整轮配额撞光（之后就 2 小时冷却）。
    max_pages_default = 3
    MAX_PAGES_HARD_CAP = 3
    # 一轮里的请求预算必须钉在站方那句原话之下：「非会员可浏览每个行业的前3页信息
    # (或不超过50个内容页信息)」。到了预算就优雅收本轮（已采数据不丢，下一轮自动续采），
    # 比让站方来封 IP 好。旧值 90 是永远到不了的预算——实机 2026-11-04 两个放行窗口
    # 合计只翻了 41 页（14＋27）就撞配额页，也就是说每次都先撞墙、再赔约 90 分钟冷却，
    # 界面数字正是在那 90 分钟里一动不动（09:44 看到停在 99 家就是这一条）。
    # 一页只花 1 个请求（详情零请求），45 页留 5 页余量给会话引导与重试。
    REQ_BUDGET = 45
    # 列表页是唯一请求，节流按页走；IP 配额敏感，间隔给到分钟感而不是秒感
    PER_REQUEST_PACE = True
    PACE_FACTOR = 1.0
    MIN_DELAY = 6.0

    def __init__(self, port=9255):
        super().__init__(port)
        self.delay_between = 6.0
        self.jitter = 3.0
        self.max_pages = self.max_pages_default
        self._session = self._new_session()
        self._booted = False        # 是否已拿到 WebShield 认的 ASP 会话 Cookie
        self._boot_hops = 0         # 本轮引导跟过几次质询跳转（0＝首页直接给内容）
        self._boot_len = 0          # 引导最后看到的正文字数（日志区分「没过质询」与「被拦」）
        self._shell_hits = 0        # 连续薄壳次数（用来判断「引导也没用了＝IP 进冷却」）
        self._req_used = 0          # 本轮已发请求数（对 REQ_BUDGET 记账）

    def _new_session(self):
        s = requests.Session()
        s.headers.update({
            "User-Agent": _UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": HOME_URL,
        })
        return s

    def reset_run_state(self):
        super().reset_run_state()
        self._booted = False
        self._shell_hits = 0
        self._req_used = 0
        return True

    # ── 旁路浏览器：本站不常驻标签 ─────────────────────────────
    def start_browser(self, headed=True):
        return True

    def ensure_tab(self, log=None):
        return True

    def close_browser(self):
        return None

    def _is_session_alive(self):
        return True

    def http_block_reason(self, status, html="", url="", headers=None):
        """先走基类（状态码 + 小体积 + 插页词），再补一道本站专用的全文扫。

        为什么要补：安全狗的 IP 拦截页正文 5140 字节，基类那条「体积 < 3000 才去比对页词」
        的门槛把它放过去了，而它的话术与基类词表差一个字（请求/访问过于频繁），于是整页被
        当成正常内容返回：列表解析得到 0 家 → 「这个关键词没公司」→ 关键词被记成已跑完，
        下一轮自动跳过——这正是「绝不能把风控页当空结果」要拦住的事。
        这几个短语只可能出现在拦截页上（真实列表页是 B2B 条目，不会写「已被网站管理员设置
        拦截」），所以本站不看体积也扫；归到 ip 处置（站方原话就是 IP 访问频繁，页面上
        没有可点的验证）。"""
        reason = super().http_block_reason(status, html, url, headers)
        if reason:
            # 配额页（那句「网站已启用防采集功能…」，实测 275 字）体积够小，命中的是
            # 基类那条按体积比对页词的分支，分到的层同样是 ip。但基类话术不带等待预期，
            # 界面就只剩「防采插页（防采集）」——看不出还要不要等。这里补成与本站专用
            # 分支同一口径（实测冷却约 90 分钟，站方原话「after 2 hours」）。
            if getattr(self, "_block_kind", "") == "ip" and "再浏览" not in reason:
                reason = f"{reason}·站方要求约 2 小时后再浏览"
            return reason
        hit = next((m for m in _BLOCK_PAGE_MARKS if m in (html or "")), "")
        if hit:
            if url:
                self._block_probe_url = url
            self._block_kind = "ip"
            return f"防采插页（{hit}）·站方要求约 2 小时后再浏览"
        return ""

    # ── 会话引导：走完 WebShield 的质询跳转，再回首页确认放行 ───────────
    def _bootstrap(self):
        """2026-11-04 实机纠错（旧实现只打首页一次，永远判不出放行，见下）。

        放行前首页只给 405 字节薄壳：`setTimeout("JumpSelf()",700)` 里
        `self.location="/?WebShieldSessionVerify=<token>"`，响应头 `Set-Cookie:` 为空。
        旧实现按「首页正文 >8000 字」判引导成功，于是恒判失败；而它从没跟过 JS 那一跳，
        服务端就永远等不到这个出口「已验证」。结果是把「质询没走完」一路误归成
        「IP 进冷却」，界面上 GI 采 0 家、日志喊「已等 420 秒」。

        实测跟完那一跳（质询页自己只回 361 字、仍不下发 cookie）之后：再取搜索页就是
        36,285 字真列表并下发 ASPSESSIONIDAEDTSTDD；随后两个**全新连接、零 cookie** 的
        请求直接拿到 92,790 / 92,818 字真首页——可见「已验证」记在**服务端的出口 IP 状态**
        上，不绑 TCP 连接、也不靠 cookie 名字，抄 cookie／换 UA 都替代不了这一跳。

        所以引导的正确形状是：首页 →（有 token 就跟，最多 3 跳）→ 回补一次首页，
        用回补那次的体积当放行判据（浏览器跟完质询页本来就要回到首页）。
        跟完仍不给内容，才轮到安全狗那句 IP 冷却处置（那是另一层，见模块 docstring）。"""
        try:
            r = self._session.get(HOME_URL, timeout=25)
            self._mark_activity()
            text = self._decode(r)
            hops = 0
            while hops < _VERIFY_MAX_HOPS:
                m = _VERIFY_TOKEN_RE.search(text)
                if not m:
                    break
                time.sleep(0.8 + 0.4 * hops)          # 薄壳里 JS 等的是 700ms，按它的节奏来
                r = self._session.get(f"{HOME_URL}?WebShieldSessionVerify={m.group(1)}",
                                      headers={"Referer": HOME_URL}, timeout=25)
                self._mark_activity()
                text = self._decode(r)
                hops += 1
            if hops:
                # 跟完质询页回补首页：这一步才是「拿到真内容 + 下发 ASP Cookie」
                time.sleep(0.8)
                r = self._session.get(HOME_URL, headers={"Referer": HOME_URL}, timeout=25)
                self._mark_activity()
                text = self._decode(r)
            self._boot_hops = hops
            self._boot_len = len(text or "")
            self._booted = r.status_code == 200 and self._boot_len > 8000
            if hops and self._booted:
                self._log(f"  ✓ 跨国采购网跟随 WebShield 会话质询跳转 {hops} 次，会话已建立")
            elif not self._booted and hops:
                self._log(f"  ↻ 跨国采购网已跟随质询跳转 {hops} 次，首页仍不给内容"
                          f"（{self._boot_len} 字）——这不是缺跳转，是站方在拦这个出口")
            return self._booted
        except Exception as e:
            self._log(f"  ⚠ 跨国采购网首页会话引导失败（{str(e)[:60]}）")
            self._booted = False
            return False

    def probe_block(self):
        """恢复探测：一次请求定成败——直接取列表页，能解析出公司条目才算解除。

        为什么改成单请求（2026-11-04 实机，这条直接决定本站一天能采多少）：旧实现每
        探一次都先走 _bootstrap（1~3 个请求）再取列表，配合 60 秒的探测间隔，等于在
        站方已经写明「too many visits … after 2 hours」的冷却期里持续高频访问这个
        出口，冷却计时被自己一遍遍续上，放行窗口再也不出现——现场就是 08:47 被拦后
        界面「跨国采购网」死钉 99 家（08:38 那个窗口 9 分钟采到 99 家，量并不小）。
        静默交给 BLOCK_POLL_IP（3 分钟起），这里只负责「一发判定」：
          · 拿到真列表 → 解除；
          · 拿到 WAF 薄壳（会话质询没走完）→ 才值得再花请求跟一跳，跟完回头重取；
          · 拿到安全狗/配额页 → 不浪费请求，照原样报「仍在拦」；
          · 连接层抛异常 → 归 NET_FAIL_TAG，这是本机断网，不是站点在拦。
        绝不能只看状态码——超配额页同样是 200，正文却只有 541 字节的提示。"""
        url = self._block_probe_url or self.build_search_url("公司", 1)

        def once():
            """发一个请求判定。返回 (是否解除, 未解除说明, 是否 WAF 会话质询薄壳)。"""
            try:
                r = self._session.get(url, timeout=25, headers={"Referer": HOME_URL})
                self._mark_activity()
            except Exception as e:
                self._session.close()
                self._session = self._new_session()
                self._booted = False
                return False, f"{NET_FAIL_TAG}{type(e).__name__}", False
            text = self._decode(r)
            block = self.http_block_reason(r.status_code, text, url)
            if r.status_code == 200 and not block:
                if self.parse_list(text, "公司"):
                    self._block_reason = ""
                    self._shell_hits = 0
                    self._booted = True
                    return True, "", False
                return False, "跨国采购网列表页取到了但没有公司条目", False
            shell = "WebShieldSessionVerify" in (text or "")[:2000]
            return False, (block or f"HTTP {r.status_code}"), shell

        ok, why, shell = once()
        if ok:
            return ""
        if shell and self._bootstrap():
            # 只有 WAF 会话质询值得补花请求：跟完一跳再取一次就给真页（见 _bootstrap）。
            # 安全狗/配额页跟跳转没用，一次多余请求都不该往冷却期里的出口上打。
            ok, why2, _ = once()
            if ok:
                return ""
            why = why2 or why
        return why or (self._block_reason or "跨国采购网仍在配额/限流状态")

    @staticmethod
    def _decode(r):
        """HTTP 头写 gb2312，正文一律按 gb18030 解（gb2312 字库缺字会整段乱码）。"""
        try:
            return r.content.decode("gb18030", "ignore")
        except Exception:
            return r.text or ""

    # ── 裸 HTTP 取页 ───────────────────────────────────────────
    def _fetch(self, url, tries=3, guarded=True):
        if self._req_used >= self.REQ_BUDGET:
            self._log(f"  ⛔ 跨国采购网本轮已用到 {self._req_used} 个页面（站方规定同一个网络地址"
                      f"最多看约 50 个页面，主动收本轮比被它拦下快），"
                      f"已采数据不丢，下次开始会从断点接着跑")
            return ""
        if not self._booted:
            self._bootstrap()
        last = ""
        for i in range(max(1, tries)):
            try:
                self._pace_before_request()
                r = self._session.get(url, timeout=25, headers={"Referer": HOME_URL})
                self._mark_activity()
                text = self._decode(r)
                block = self.http_block_reason(r.status_code, text, url, r.headers)
                if block:
                    if any(m in text[:2000] for m in ("WebShieldSessionVerify",)):
                        # WAF 会话质询（不是配额）：重新建立首页会话后再要这一页。
                        # 但反复引导仍只给薄壳，就不是「缺 Cookie」了——实机上那是本出口
                        # 已被站方收进限流冷却（它另一句原话是 too many visits … after 2 hours），
                        # 必须归到拦截处置里等恢复，绝不能当成「本关键词没公司」直接收工。
                        self._shell_hits += 1
                        last = "WAF 会话质询薄壳"
                        if self._shell_hits <= 2:
                            self._log("  ↻ 跨国采购网 WAF 会话质询：按页面 JS 跟随质询跳转"
                                      "后再取这一页")
                            time.sleep(2.0 * self._shell_hits)
                            if self._bootstrap():
                                continue
                        self._block_reason = block
                        break
                    else:
                        # 「too many visits / 非会员前 3 页」＝IP 配额，重试只会加重
                        self._block_reason = block
                        last = "IP 配额/防采集提示页"
                        break
                elif r.status_code == 200 and text:
                    self._block_reason = ""
                    self._shell_hits = 0
                    self._req_used += 1
                    return text
                else:
                    last = f"HTTP {r.status_code}"
                    if r.status_code == 404:
                        return ""
            except requests.exceptions.ConnectionError as e:
                last = str(e).splitlines()[0][:80] or "连接被重置"
                self._session.close()
                self._session = self._new_session()
                self._booted = False
            except Exception as e:
                last = str(e).splitlines()[0][:80]
            if i < tries - 1:
                time.sleep(1.5 * (i + 1))
        if guarded and self._block_reason:
            # 本站的拦截没有一种是人在浏览器里能点掉的：WAF 薄壳是会话质询、超配额是 IP 冷却
            # （站方另一句原话是 too many visits … after 2 hours）。分诊词表会把带 challenge
            # 字样的薄壳归成「可人工过的验证插页」，结果就是既不弹浏览器、又喊“请在 Chrome
            # 窗口里手动过验证”，还按 15s 高频白探——对着两小时的冷却窗口等于自己把冷却打断。
            # 只在本站这一侧纠正归类，不动基类的通用兜底（那条要留给「真滑块 + 平时不弹标签」的站）。
            self._block_kind = "ip"
            if self.handle_http_block(url):
                self._block_reason = ""
                self._booted = False
                return self._fetch(url, tries=tries, guarded=False)
        self._log(f"  ⚠ 跨国采购网请求失败（{last}）：{url[:80]}")
        return ""

    # ── 列表 ───────────────────────────────────────────────────
    def build_search_url(self, kw, page):
        # 关键词必须按 GBK 百分号编码：站点是 ASP + gb2312，UTF-8 编码的 query 直接 0 结果
        return SEARCH_URL.format(kw=quote(str(kw).encode("gbk", "ignore")), page=max(1, int(page)))

    def parse_list(self, html, kw):
        """以「序号+商铺子域+公司名」为锚切条目，段内再取 主营产品 / 联系人 / 电话。"""
        out, seen = [], set()
        html = html or ""
        marks = list(_ROW_RE.finditer(html))
        for idx, m in enumerate(marks):
            slug = (m.group(1) or "").strip().lower()
            name = _htmlmod.unescape((m.group(2) or "").strip())
            if not slug or len(name) < 4 or slug in seen:
                continue
            if any(x in name for x in ("首页", "登录", "注册", "更多", "下一页")):
                continue
            seen.add(slug)
            seg = html[m.end(): marks[idx + 1].start() if idx + 1 < len(marks) else m.end() + 3000]
            prod = ""
            pm = _PROD_RE.search(seg)
            if pm:
                prod = re.sub(r"\s+", " ", _htmlmod.unescape(_TAG_RE.sub("", pm.group(1)))).strip()
            person = tel = ""
            cm = _CONTACT_RE.search(seg)
            if cm:
                person = _htmlmod.unescape(cm.group(1)).strip()
                tel = _htmlmod.unescape(cm.group(2)).strip()
            dm = _CDETAIL_RE.search(seg)
            shop = f"http://{slug}.cn.globalimporter.net/"
            out.append({
                "公司名称": name[:60],
                "公司ID": slug,
                "memberId": slug,
                "公司网址": shop,
                "详情URL": ("https://www.globalimporter.net" + dm.group(1)) if dm else shop,
                "主营产品": prod[:200],
                "经营模式": "",
                "所在区域": self._region_from_name(name),
                "列表联系人": person[:40],
                "列表电话": re.sub(r"\s+", "", tel)[:60],
                "标题": name[:80],
            })
        return out

    def slice_total(self, html):
        m = re.search(r"相关企业\s*([\d,]+)\s*家", html or "")
        return int(m.group(1).replace(",", "")) if m else -1

    def list_has_more(self, html, page):
        """列表页给到 curpage=20 的翻页链接；按「已达上限页 / 本页无条目」收。
        实测站方配额是非会员每行业前 3 页，所以真正让我们停下来的多半是配额页（_fetch 返回空）。"""
        if not html:
            return False
        nums = [int(x) for x in re.findall(r"curpage=(\d+)", html)]
        return bool(nums) and max(nums) > page

    @staticmethod
    def _region_from_name(name):
        """列表页没有区域字段，公司名开头的地级市是本站唯一属地线索（「东莞市企石唐蚁…」→ 东莞）。"""
        m = _CITY_RE.match(name or "")
        if not m:
            return ""
        return re.sub(r"(市|县|旗|州)$", "", m.group(1))[:12]

    # ── 号码清洗：三种实测形态 + 一个「区号拆号」还原式 ──────────
    @staticmethod
    def _split_numbers(raw):
        """返回 (手机列表, 固话列表)。
        「010-13920758-444」这类脏号按 ^(0 区号)?(11 位手机)$ 还原成 13920758444：
        只重组商家已经写全的数字，绝不补位、绝不猜测——位数对不上就整个作废。"""
        mobiles, tels = [], []
        for v in re.split(r"[/、,;;\s]+", str(raw or "")):
            v = strip_country_code(v)
            if not v or is_placeholder_number(v) or v in _FAKE_NUMBERS:
                continue
            digits = re.sub(r"\D", "", v)
            if PHONE_MOBILE_RE.fullmatch(v) and v not in mobiles:
                mobiles.append(v)
                continue
            mm = _MOBILE_WITH_AREACODE_RE.match(digits)
            if mm and not PHONE_TEL_RE.fullmatch(v) and mm.group(1) not in mobiles:
                mobiles.append(mm.group(1))          # 区号+手机混写：取手机本体
                continue
            if PHONE_TEL_RE.fullmatch(v) and v not in tels:
                tels.append(v)
        return mobiles, tels

    # ── 列表翻页：必须自己走裸 HTTP（基类那套是用浏览器标签取页的）──────
    def _iter_keyword(self, kw):
        """与 maimaib2b 同口径：取页必须过本类 _fetch（才有序话引导/分诊/限速），
        记账字段（stat_*、_seen_members、_early_skipped）与基类逐字对齐。"""
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
                self._log(f"  「{kw}」第 {page} 页没取到页面（配额到底或仍在冷却），停止翻页")
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
            new = skipped = 0
            for e in entries:
                mid = e["公司ID"]
                key = f"{self.PLATFORM_SLUG}:{mid}"
                name = self._norm_name(e["公司名称"])
                nkey = f"{self.PLATFORM_SLUG}:name:{name}"
                if key in self._seen_members or nkey in self._seen_members \
                        or f"{self.PLATFORM_SLUG}:{kw}#{mid}" in self._seen_members:
                    continue
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
            tail = f"，早过滤 {skipped} 家(未进详情)" if skipped else ""
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

    # ── 详情：列表页已是完整数据，这里**一个请求都不发** ─────────
    def crawl_detail(self, stub):
        """本站详情/内容页吃的是 IP 配额（超了直接 2 小时冷却），而列表页已经把
        公司名/联系人/手机/固话/主营产品全给明文，所以详情就是一次纯本地组装。"""
        rec = self._base_rec(stub)
        mobiles, tels = self._split_numbers(stub.get("列表电话", ""))
        if mobiles:
            rec["手机"] = " / ".join(mobiles[:3])
        if tels:
            rec["电话"] = " / ".join(tels[:3])
        person = stub.get("列表联系人", "")
        cname, cpos = parse_contact_field(person)
        # 「联系人:东莞市企石唐蚁制冷设备厂」实测就是把公司名灌进联系人栏
        if cname and cname not in strip_name_marketing_tail(stub.get("公司名称", "")):
            rec["联系人"] = cname
        pos = compose_position("", "" if HONORIFIC_ONLY_RE.match(cpos or "") else cpos)
        if pos:
            rec["岗位"] = pos[:20]
        if not rec.get("联系人") and not rec.get("岗位"):
            d = dept_only(person)
            if d:
                rec["岗位"] = d[:20]
        if not self.detail_relevant(rec):
            rec["状态"] = "类目不符(已过滤)"
            return rec
        has_phone = bool(rec.get("电话") or rec.get("手机"))
        rec["状态"] = "成功" if has_phone else ("有联系方式(仅邮箱)" if rec.get("邮箱") else "无联系方式")
        return rec
