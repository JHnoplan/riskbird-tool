"""
ali1688_crawler.py - 1688 企业数据自动采集核心

链路（实机探测确认）：
  1) 列表页  https://www.1688.com/zw/page.html?hpageId=old-sem-pc-list&keywords=<kw>&beginPage=<n>
     页内内嵌 `var pageConfig = {...}`，其 listOffer[] 每条含 company / offerId /
     odUrl(干净详情URL) / loginId / memberId / price / subject，分页上限约 50 页。
  2) 详情页  odUrl（https://detail.1688.com/offer/<offerId>.html）
     页内 JSON 含 winportUrl(店铺域名) 与 sellerWinportUrlMap.contactinfoUrl。
  3) 联系方式页  contactinfoUrl（https://<shop>.1688.com/page/contactinfo.html）
     电话 / 手机 / 传真 / 地址 为纯文本，按标签提取。

按 memberId 去重后再进详情+联系方式，大幅降低请求量与封禁风险。

浏览器：独立调试端口 + 独立持久化配置目录（不抢其它工具的单实例锁），
登录一次后跨启动复用；被风控拦截时原地等人工过验证后自动继续。
"""

import os
import re
import sys
import time
import json
import random
import shutil
import threading
import subprocess
import urllib.request
from pathlib import Path
from urllib.parse import quote

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

# 复用企业数据采集工具成熟的《客户库数据导入模板》清洗模块（同目录副本）
try:
    import customer_library_cleaner as _cleaner
except ImportError:  # 兼容从上级目录运行
    sys.path.insert(0, str(Path(__file__).parent))
    import customer_library_cleaner as _cleaner

# 数据落点口径：全工具只许有一份实现，就是 riskbird_build/runtime_paths.py。
# 本文件以前自己写了一版候选链，形状与本轮 Mac 崩溃同族，而且缺了最后一级退路——
# 「包旁 + 用户目录都写不进」的机器上把不可写的目录原样 return，调用方一句 mkdir
# 就 OSError 崩在启动那一下（见 runtime_paths.py 与离线回归 test_data_dir_offline.py
# 第 8 节）。在这里补路径而不是再抄一份实现：独立运行本工具时 riskbird_build 不在
# sys.path 上，b2bcollect 打包态与源码态都在。用 append 不 insert，理由与
# site_adapters 一致——本目录同名模块永远优先，别把外部目录顶到前面来遮蔽清洗模块。
try:
    import runtime_paths as _paths
except ImportError:
    sys.path.append(str(Path(__file__).resolve().parent.parent / "riskbird_build"))
    import runtime_paths as _paths

# ChromeDriver 的查找与回退也只许有一份实现：riskbird_build/chrome_driver.py。
# 名单通道（企知道/风鸟/爱企查）用的就是它，1688 与它们跑在同一台 Chrome 上，两套判据
# 在同一台机器上会得出相反的结论（本轮 1688 恒 0 家就是这么来的，见 _attach 注释）。
# 独立的 Ali1688Tool 包里没有这份模块，导入失败时 _attach 退回本文件自带的候选链——
# 那条链也一并修掉了「大版本不等就立刻弃用本地驱动」这个实测错误的判据。
try:
    import chrome_driver as _chrome_driver
except ImportError:
    _chrome_driver = None

try:
    from PyQt5.QtCore import QObject, pyqtSignal
    HAS_PYQT = True
except ImportError:
    HAS_PYQT = False

if HAS_PYQT:
    class CrawlerSignals(QObject):
        log_signal = pyqtSignal(str)
        progress_signal = pyqtSignal(int, int)          # 当前, 总数
        crawl_finished_signal = pyqtSignal()
        blocked_signal = pyqtSignal(str)                # 需人工过验证
        block_cleared_signal = pyqtSignal(str)          # 已过验证继续
        login_required_signal = pyqtSignal(str)         # 需登录
        stats_signal = pyqtSignal(str)                  # 阶段/统计信息
else:
    class CrawlerSignals:
        pass


# ── 1688 风控 / 拦截判据（只用 URL 与标题，绝不整页正文关键词匹配）──────────
BLOCK_URL_MARKERS = (
    "x5secdata", "_____tmd_____", "/punish", "login.alibaba.com",
    "login.1688.com", "x5sec.", "/_____tmd____", "nocaptcha", "cap", "verify.1688",
)
BLOCK_TITLE_MARKERS = ("验证码", "安全验证", "滑块验证", "身份核实", "环境异常",
                       "访问验证", "请验证", "滑动验证")
# 正常列表/详情/联系页标题里含这些词，绝不能当拦截
SAFE_TITLE_HINTS = ("批发", "阿里巴巴", "联系方式页", "供应商", "制造商", "1688")

# 未登录特征：联系方式页要登录才完整展示，未登录会跳登录或空值
LOGIN_URL_MARKERS = ("login.1688.com", "login.alibaba.com", "/member2/login")
# 登录标记 cookie：这几个是登录后才会写进 .1688.com 的会话身份，缺一个都不算登录。
# 2026-11-15 实机对照：同一台共享 Chrome 里爱企查/企知道/风鸟三路都能查到各自的登录
# cookie，1688 这边只有 cna/isg/_m_h5_tk 这类游客埋点 —— 也就是**没登录**，而它的
# 联系方式页正是在这种状态下最容易吃风控（当天 1688 通道第一次取详情就撞「验证码拦截」）。
LOGIN_COOKIE_MARKERS = ("cookie2", "lgc", "_l_g_", "tracknick", "sgcookie")


def judge_login_cookie(cookies):
    """从一份 cookie 判 1688 登录态：True 已登录 / False 确认没登录 / None 判不出。

    「判不出」必须是独立的一档，不许塌成 False：cookie 取空、形状不对、当前页根本
    不在这个域里，都只是**没看到证据**，不是**看到没登录的证据**。这里返回 None 时
    上层就一个字都不多说 —— 误导用户去登录比不提示更糟（本轮改造同一个红线：
    提示文案必须与实际行为一致）。
    """
    if not isinstance(cookies, (list, tuple)):
        return None
    names = set()
    for c in cookies:
        if isinstance(c, dict):
            n = str(c.get("name") or "").strip()
            if n:
                names.add(n)
    if not names:
        return None
    return any(m in names for m in LOGIN_COOKIE_MARKERS)

SEARCH_URL = ("https://www.1688.com/zw/page.html?hpageId=old-sem-pc-list"
              "&keywords={kw}&beginPage={page}&n=y")

# 供应商(公司)搜索渠道：每页约 20 家不同公司，产出远高于商品渠道
COMPANY_SEARCH_URL = ("https://s.1688.com/company/company_search.htm"
                      "&keywords={kw}&beginPage={page}")

# 主搜渠道（1688 站内正规商品搜索页）：每页约 60 条 offer、约 50 家不同公司，
# 实测同一垂直词池深 100+ 家（SEM 落地页仅 20 余条/页且大词也只有约 70 家池子）。
# 公司维度数据藏在 offer 卡片埋点属性 member_id@b2b-xxx^item_id=yyy 里。
MAIN_SEARCH_URL = ("https://s.1688.com/selloffer/offer_search.htm"
                   "&keywords={kw}&beginPage={page}")

PHONE_RE = re.compile(r'(?:86[\s-]?)?(?:0\d{2,3}[\s-]?\d{7,8}|1[3-9]\d{9})')
EMAIL_RE = re.compile(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}')
# 主搜页 offer 卡片埋点属性里的 (memberId, offerId) 对（2026-10-27 实机确认）
MAIN_MEMBER_ITEM_RE = re.compile(r'member_id@(b2b-[0-9a-z]{6,24})\^item_id@(\d+)')
# 主搜结果卡片 <a class="offerCard.." href="../offer/{id}.html">..</a>：列表页即可零成本
# 拿到 offerId + 公司名(卡片 title 属性) + 商品标题(class="titleText--hash" 内文)，
# 用于「进详情前」的早过滤（用平台自身的关键词预测类目+关键词字面做相关性打分，省大量详情导航）。
OFFER_CARD_RE = re.compile(
    r'<a class="offerCard[^"]*"[^>]*?href="https?://detail\.1688\.com/offer/(\d+)\.html[^"]*"[^>]*>(.*?)</a>',
    re.S)
CARD_TITLE_TEXT_RE = re.compile(r'class="titleText--[^"]*"[^>]*>(.*?)</div>', re.S)
CARD_COMPANY_SUFFIX_RE = re.compile(r"(公司|厂|商行|商店|中心|经营部|经销部|部|店|馆|集团|合作社|工作室)$")
# 主搜卡片埋点里的叶子类目：leafCategory@12345^。注意实测全页 60 条同值，
# 是「查询级预测类目」而非 offer 真实类目，逐条过滤不可用它——真实类目在详情页：
DETAIL_LEAF_ID_RE = re.compile(r'"leafCategoryId"\s*:\s*(\d{3,12})')
DETAIL_LEAF_NAME_RE = re.compile(r'"leafCategoryName"\s*:\s*"([^"]{1,20})"')
DETAIL_SHOP_CAT_RE = re.compile(r'shop-category-name">([^<]{2,40})<')   # 主营：xx / xx类目
DETAIL_TITLE_RE = re.compile(r'<title>([^<]{4,80}?)\s*[-–—]?\s*(?:阿里巴巴|1688)?\s*</title>')


def reap_driver_service(driver) -> bool:
    """收掉 driver 自己拉起的那份 chromedriver 子进程，返回是否真收了一次。

    为什么独立包要有这一手：Selenium 4.51 的 ``driver.quit()`` 只结束会话，子进程
    回收（``service.stop()``）只剩「建会话失败」那条异常路径和 ``Service.__del__``
    两个指望，而后者要等引用断了、GC 真跑一次才算 —— 实机取证本机就挂着两个
    PPID 已是 launchd 的孤儿 chromedriver（见 chrome_driver 顶部那段）。
    b2bcollect 包里有 riskbird_build/chrome_driver 的统一口径（create() 建会话时
    就把 quit() 绑成「结束会话 + 收子进程」），但 Ali1688Tool 是单目录打包、
    运行期才 append sys.path，那份模块在包里根本解析不到；本文件 _attach 的退回
    分支又是自己 ``webdriver.Chrome(...)`` 建的会话，没地方绑回收，只能在这里显式补上。
    幂等、吞异常：收工路径绝不能被回收失败带崩。
    """
    service = getattr(driver, "service", None)
    if service is None or getattr(service, "_cd_reaped", False):
        return False
    try:
        service._cd_reaped = True            # 先立牌：无论下面走到哪一步都不收第二遍
    except Exception:
        pass                                 # 立不上牌也不能挡回收（顶多被多收一次）
    try:
        service.stop()
        return True
    except Exception:
        return False


# ── 请求层失败分型：类别必须写在最前面，才活得过后面每一档截断 ──────────────────
# 由来（2026-11-15 实机体检：顺企网被报成「采不到」）：一份 requests 异常的形状是固定的
# 三段——池头 `HTTPSConnectionPool(host=…, port=…)`、`Max retries exceeded with url: …`、
# `(Caused by XxxError(…))`。**决定处置方向的字全在第三段**：连不上＝查出口/域名可达，
# 端口拒绝/无应答＝站点侧，MissingSchema＝代码把 URL 拼错，HTTP 429/403＝限流风控。
# 而旧写法 `str(e).splitlines()[0][:80]` 恰好截在第三段之前，再叠加记账的 [:160]、渲染的
# [:60]/[:70]/[:40]，界面与体检器最终拿到的是
# `HTTPSConnectionPool(host='b2b.11467.com', port=443): Max ret`——一句既没有类别也没有
# 方向的话，逼着人再 curl 一遍才能定性。分型器把**类别放在最前**、尾部挂实测到的关键证据，
# 于是任何一档截断之后剩下的都还是一句能照着处置的话。
# 下面每一档判据都对着本机抓到的真实异常文本（七档各自取过一份 str(e)，夹具见
# test_block_triage_offline 第 ⑪ 节），不是照 urllib3 文档猜的。
# 1688 这一路没有 requests（Selenium 取页），但它的失败原因同样是 `Message: unknown error:
# net::ERR_…` 这种带类别的形状，所以这张表两边共用、**两份实现必须同字节**：分叉一次就会
# 出现「同一件网络故障，1688 说一类、其它站说另一类」那种两套结论的账（第 ⑭ 节 14i 对拍钉的
# 就是这个形状）。
_ERR_POOL_RE = re.compile(r"(?:https?)ConnectionPool\(host='([^']*)'\s*,\s*port=(\d+)\)",
                          re.IGNORECASE)
_ERR_CAUSED_RE = re.compile(r"Caused by\s+", re.IGNORECASE)
# `<HTTPSConnection(host='x', port=443) at 0x108e3b750>`：整段是噪声，里面有嵌套括号但没有
# 尖括号，所以 [^<>]* 足够（真要写递归括号匹配就是过度设计）。
_ERR_OBJ_RE = re.compile(r"<[^<>]*0x[0-9a-f]+>")
# `XxxError(` / `XxxError(54, `：异常类名与它的 errno 参数都是包装，留下后面的消息。
# 要求紧跟左括号，是为了不误删 `ConnectionError: (…)` 这种我们自己拼上去的前缀。
_ERR_CLS_RE = re.compile(r"\b[A-Za-z_]*(?:Error|Exception)\(\s*(?:\d+\s*,\s*)?")
# 池头有两种写法（异常里 `HTTPConnection(host=…, port=…):`、外层 `HTTPConnectionPool(...):`），
# 都是定位信息（host/port 已经进头了），留在证据里就是把同一句话抄两遍。
_ERR_POOL_HEAD_RE = re.compile(r"(?:https?)Connection(?:Pool)?\(host='[^']*'\s*,\s*port=\d+\)\s*:\s*",
                               re.IGNORECASE)
_ERR_HTTP_STATUS_RE = re.compile(r"\bHTTP\s+(\d{3})\b", re.IGNORECASE)
# 匹配顺序＝优先级：越具体越靠前。拒绝／重置／无应答三档共用一句
# `Failed to establish a new connection: …`，只有 errno 与措辞能把它们分开，
# 所以「只有它才有」的那两档必须排在「大家都有」的那一档之前——顺序错了就把
# 「端口没人监听」说成「网络不通」，而这两件事的处方完全不同。
_REQUEST_ERROR_KINDS = (
    ("代理不通", ("proxyerror", "unable to connect to proxy", "proxy url")),
    # 本机断网与「域名解析不了」是两件事：前者整站都出不去（对照组也会红），后者只有这一
    # 个域名没了（对照组正常）。混成一档会让人去改 hosts／换域名，而该做的是接网线。
    ("本机断网", ("net::err_internet_disconnected", "network is unreachable", "no route to host",
                 "[errno 51]", "[errno 65]", "[errno 101]")),
    ("DNS解析不了", ("nameresolutionerror", "failed to resolve", "getaddrinfo",
                    "nodename nor servname", "name or service not known",
                    "net::err_name_not_resolved")),
    ("URL不合法(代码)", ("missingschema", "invalidurl", "no scheme supplied", "invalid url")),
    ("连不上·端口被拒", ("connection refused", "[errno 61]", "[errno 111]",
                      "net::err_connection_refused")),
    ("连不上·被重置", ("connection reset", "connection aborted", "[errno 54]", "[errno 104]",
                     "broken pipe", "remotedisconnected", "net::err_connection_reset",
                     "net::err_socket", "net::err_network_changed")),
    ("TLS握手失败", ("sslerror", "ssl:", "certificate", "handshake", "sslv3", "tlsv1",
                   "net::err_ssl", "net::err_cert")),
    # Chrome 把「站点回了 4xx/5xx、页里又没有可看的内容」也算导航失败：照样落
    # chrome-error://，可这句话的主语是**站点对这一条资源回了错误状态码**，既不是浏览器拦、
    # 也不是网络不通。混进下一档会让人去改启动参数（2026-11-14 实机马可波罗的商铺深链正是
    # 这一档：同一时刻通道入口照常 200、借标签那条腿 29/33 出货），而正确的处方是按每家深链
    # 被拒记账、继续下一家。
    ("站点回了错误状态码", ("err_http_response_code_failure",)),
    # 浏览器自己把这次导航取消了：站点侧根本没看到流量，页面落在 `chrome-error://`。
    # `ERR_BLOCKED_BY_CLIENT` 的实机由来是「始终使用更安全的连接」把明文 http 升级成
    # https、升级失败又不退回明文（同机两个 Chrome 只差这一个开关的差分实测见
    # shared_chrome.NO_HTTPS_UPGRADE_FEATURES）；另一支来源是扩展/本机策略。
    # 它与「连不上」的处方完全不同：那一档查出口网络与域名可达，这一档查浏览器参数与
    # 这一跳用的是哪种协议——所以刻意**不进** DEAD_HOST_KINDS，不该被主机熔断吃掉。
    ("浏览器拦下这一跳", ("err_blocked_by_client", "err_blocked_by_administrator")),
    ("连不上·无应答", ("connecttimeout", "connect timeout", "timed out. (connect",
                     "operation timed out", "[errno 60]", "failed to establish a new connection",
                     "net::err_connection_timed_out", "net::err_timed_out",
                     "net::err_empty_response")),
    ("读响应超时", ("readtimeout", "read timed out", "read timeout", "incompleteread",
                   "timed out")),
    ("返回不可解析(代码)", ("expecting value", "jsondecoder", "char 0", "no json could be",
                         "failed to decode")),
    # 兜底档：requests 的 ConnectionError **类名本身**就是「连接阶段断了」（urllib3 的
    # NewConnectionError / ConnectTimeoutError 都包装成它），可它带的消息偶尔只剩
    # `HTTPSConnectionPool(host=…, port=443): Max retries exceeded with url: /…` 而后半句
    # `Caused by …` 是空的（本机与 gi_pace 夹具都抓到过）——按关键词就掉进【未分型】，那一档
    # 不进熔断账、探测也不挂网络前缀，于是「这台连不上」被说成「站点在拦」，正是这一族事故。
    # 排在最后：上面每一档都按更具体的消息先命中，这一档只兜「类名说是连接问题、消息没细节」。
    ("连不上·链路中断", ("connectionerror", "max retries exceeded")),
)
# HTTP 状态码单独成一档：各站 `_fetch` 里失败原因本来就是 `f"HTTP {r.status_code}"` 这一句
# 裸话，它没有异常链可读，但 429/403/5xx 的方向性最强（限流／风控／站点故障），
# 而这几档恰恰是「等一等就好」与「改代码」的分界线。
_HTTP_STATUS_KINDS = ((429, "HTTP 429·限流"), (403, "HTTP 403·风控/需验证"),
                      (407, "HTTP 407·要代理口令"), (404, "HTTP 404·这一页没了"))
# ── 「本工具自己已经定过性的句子」的类别词表 ──────────────────────────────────────
# 翻页层会把 `_goto` 留下的原因再包一层：`列表页导航失败（【被拦停】防采页没等到放行…）`。
# 分型器过去只认 `startswith("【")`，于是这种**已经带着结论**的话被洗成
# `【未分型】｜列表页导航失败（【被拦停】…）`（2026-11-15 实机 1688 的收尾句、GUI 账目行与
# 体检报告念的全是降级后那一版）。左边界 12 字与 `_DEAD_HOST_RE` 同源——消费者（主机熔断账、
# 看门狗的网络账）本来就能从这种嵌套形状里读出类别，分型器读不出就是两套尺子。
# 只认这张表里的词：站点原文里的「【访问过于频繁】」之类不是我们的结论，不许冒充类别收口。
_OWN_KIND_TOKENS = tuple(name for name, _ in _REQUEST_ERROR_KINDS) + tuple(
    v for _, v in _HTTP_STATUS_KINDS) + ("被拦停", "页面未就绪", "一页都没取到")
# 按长度倒序排：万一某个词是另一个词的前缀，交替式里短的先命中就会把长的截走
# 第 1 组＝类别前面的那句标签（`列表页导航失败（`），它要说的是「哪一层没做成」，
# 必须留在证据里；第 2 组＝类别本身。
_NESTED_KIND_RE = re.compile(
    r"^(.{0,12}?[（(])【(%s)】" % "|".join(re.escape(t)
                                          for t in sorted(_OWN_KIND_TOKENS, key=len, reverse=True)))


# 明文 http 不能被浏览器自己升级成 https 再拦掉：升级失败时 Chrome **不抛异常**，直接把这一跳
# 取消，页面落在 `chrome-error://chromewebdata/`，`Page.navigate` 的响应里只剩一句
# `net::ERR_BLOCKED_BY_CLIENT`——「环境自己拦掉了」就会被说成「站点不给过」。由来与同机差分实测
# （两个 Chrome 只差这一枚开关）见 b2bcollect_build/shared_chrome 那段注释。
# 1688 是另一个 PyInstaller 入口，import 不到那一份，所以这里内联**逐字节相同**的副本，
# 由构建前硬门第 31 条（31g）整段对拍：只比启动参数在不在，比不出「副本少了一档 feature」，
# 而少一档恰好会让另一条升级路径把同一枚事故原样复发。
# 为什么只关自动升级、绝不关证书校验（后者会让带着登录态的浏览器对所有站点失去中间人防护）：
# 同一份注释里已经写过，两处都别改。
NO_HTTPS_UPGRADE_FEATURES = "HttpsUpgrades,HttpsFirstModeV2,HttpsFirstBalancedMode"
CHROME_NO_UPGRADE_FLAGS = (f"--disable-features={NO_HTTPS_UPGRADE_FEATURES}",)


# 原因收口只准一个把手。真源在 b2bcollect_build/text_shape.py，1688 独立包装不进那一份
# （它是另一个 PyInstaller 入口，只带 ali1688_build 自己的文件），所以这里内联一份
# **逐字节相同**的实现，由离线夹具拿源码对拍（22h 与第 ㉓ 节）：只比函数返回值比不出「一边
# 悄悄改了断点表」，那种分叉要换一种异常文本才会暴露。
# 可断的位置：标点、全角分隔符、间隔号、空白（词与词之间）。**不**在任意字符之间断，
# 这正是「一句话的开头」与「半截词」之间的区别。
_REASON_BREAKS = "｜.：，、；)） ·"
# 连接符：左边是词字符、右边也是词字符时，它属于**同一个词**内部，不算边界。
# 由来（第 30 条性质判据在真源上抓到的三条）：`…｜Connection to b2b.`、`…读不了：客户库.`
# 这两条是断在域名/文件名的点号后面（下一字是 `1`、`x`），`…com:` 是断在端号分隔符后面
# （下一字是 `4`）——看起来都「断在标点后」，实际把一个词劈成两半。句子结尾的 `.` 后面
# 跟的是空格，仍然算边界，所以只排除「两边都是词字符」这一种。间隔号同判：
# `连不上·无应答` 是一个词，`冷却）·仅这一条` 才是两件事。
_REASON_JOINERS = ".:：０．·"        # 全角点/冒号/间隔号同判


def _is_break(s, k):
    """下标 k-1 处那个字符，在 k 这个切点上算不算真正的词边界。"""
    ch = s[k - 1]
    if ch not in _REASON_BREAKS:
        return False
    if ch in _REASON_JOINERS:
        left = s[k - 2] if k >= 2 else ""
        right = s[k] if k < len(s) else ""
        if left and right and (left.isalnum() or left in "-_") \
                and (right.isalnum() or right in "-_"):
            return False              # `b2b.11467` / `com:443` / `客户库.xls`：词内部
    return True


def short_reason(text, limit=60):
    """把一句失败原因收成一行读得完的短版：**只在标点或词边界断开，绝不拦腰截字**。

    `【类别】主机:端口｜` 这一截永远整段留住：处方（查出口网络还是改代码）就写在前缀里。
    前缀之后若在 limit 内找不到边界，就**往后**找第一个边界——宁可多带几个字，
    也不留半截词；整句一个边界都没有，就原句带省略号（长词本身就是信息）。
    """
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(s) <= limit:
        return s
    head = 0
    if s.startswith("【"):
        i = s.find("｜")
        if 0 < i:
            head = i + 1              # 类别＋主机整段留住，哪怕它本身已经超过了 limit
    stop = -1
    for k in range(head + 1, min(len(s), max(limit, head)) + 1):
        if _is_break(s, k):
            stop = k
    if stop < 0:
        for i in range(head + 1, len(s)):
            if _is_break(s, i):
                stop = i
                break
    if stop < 0:
        return s + "…"
    out = s[:stop].rstrip("｜：，、； ")
    tail = "…"
    for op, cl in (("（", "）"), ("(", ")")):      # 断点前若留下没合口的括号，补上再收
        gap = out.count(op) - out.count(cl)
        if gap > 0:
            tail += cl * gap
    return out + tail


def first_line(text, limit=120):
    """把异常或多行文本收成一行：**先取首行（自然边界），再只在标点/词边界断**。

    这一支是 `str(e).splitlines()[0][:N]` 的替代品（交付链曾有 30 多处这么写）。取首行
    不算截断——异常消息第一行就是 `XxxError: 消息`；问题出在后面那一刀切在字符上，
    于是 `Message: unknown error: net::ERR_CONNECTION_` 这种半截类名反复出现在日志里。
    空文本回空串：调用方原本就在近旁留着 `or type(e).__name__` 这一档兜底。
    """
    s = str(text or "").strip()
    if not s:
        return ""
    return short_reason(s.splitlines()[0], limit)


# ── [NC-BLOCK-BEGIN] 无联系方式责任分档：与 b2bcollect_build/cdp_base.py 同段逐字节对拍
# （1688 是独立交付包，不能 import 那边，所以按第 30 条同一族规矩内联一份拷贝；
#   两份分叉会被第 32 条硬门直接判红，由来与六档处方见 cdp_base.contact_slot_state 上方）
CONTACT_SLOT_LABEL_RE = re.compile(
    r"(?:联系\s*电话|联系电话|手机\s*号码|移动电话|手机号码|手\s*机|电\s*话|传\s*真)"
    r"[^<]{0,4}[:：]?")
# 只认中文标签：早先试过把 `Tel`/`mobile` 也放进表里，结果 `viewport`、`getElementsByTagName`
# 这类脚本字节全被判成「一栏写着电话」，整页证据当场失真。
CONTACT_SLOT_BLANK = ("-", "—", "－", "–", "暂无", "未填写", "未公开", "待补充", "无", "N/A", "NA")
CONTACT_SLOT_HIDDEN = ("点击查看", "查看号码", "显示号码", "登录", "扫码", "微信", "客服",
                       "实名认证", "解锁", "开通会员", "免费热线登记")
CONTACT_SLOT_NUM_RE = re.compile(r"1[3-9]\d{9}|0\d{2,3}-?\d{7,8}|400[-\s]?\d{3}[-\s]?\d{4}")
# 剥标签用的小刀：`<[^>]{1,160}>` 而不是一路 `<[^>]+>`——模板里偶有把 `<` 写在文字里的脏页，
# 不限长度就会从那个 `<` 一路吞到下一个 `>`，把整段正文当作一个标签吃掉（证据跟着失真）。
CONTACT_SLOT_TAG_RE = re.compile(r"<[^>]{1,160}>")
# 只剥「没合口的半截标签尾巴」：窗口是 120 字硬切的，切在标签中间时上面那把刀要不到 `>`、
# 剥不动，于是 `<img style="margi` 这种残句会一路活到报告里（2026-11-16 第十二轮实机马可波罗）。
# 要求 `<` 后面紧跟标签名字符（字母/`/`/`!`）：`价格 <100 元`、`a < b` 这类正文里的裸 `<`
# 不算标签，否则会把真证据（含号码）从尾巴上吃掉——那才是把证据改没了的那类谎。
CONTACT_SLOT_DANGLING_RE = re.compile(r"<[a-zA-Z/!][^>]*$")
# 砍单样本：每个词只留几家、名字收到多长（取用方见 _cut_sample）。
# 3 家×2 阶段是「一眼能判这一刀该不该砍」的最少样本量，再多界面那一行就没人读了。
CUT_SAMPLE_CAP = 3
CUT_NAME_CAP = 24


def contact_slot_state(html):
    """详情页「电话/手机」那一栏的证据分档 → (档位, 证据串)。

      · `miss`    栏位在、值里**真有号码**，而本次一条都没抽走 —— 我们的解析漏了（该修代码）
      · `hidden`  栏位写着「点击查看/登录后可见/联系客服登记」这类要交互才给号的话 —— 站方藏着
      · `blank`   栏位在、值是 `-`/`暂无`/`未填写` 这类空占位 —— 站方这一家根本没登
      · `no_slot` 整页找不到任何中文联系方式栏位 —— 这一页没这一栏，**无从判定**

    多栏并存时按「先认我们自己的锅」定序：miss > hidden > blank > no_slot。理由很直接——
    只要有一处是我们该取到没取到，把话说成「站方没给」就会让那条真缺陷永久隐身。
    纯函数、无副作用（离线夹具与第 32 条硬门都直接喂 HTML 对拍）。
    """
    page = html or ""
    best, evid = "", ""
    rank = {"miss": 3, "hidden": 2, "blank": 1, "no_slot": 0}
    for m in CONTACT_SLOT_LABEL_RE.finditer(page):
        win = re.sub(r"\s+", " ", CONTACT_SLOT_TAG_RE.sub(" ", page[m.end():m.end() + 120])).strip()
        if CONTACT_SLOT_NUM_RE.search(win):
            kind, show = "miss", win
        elif any(h in win[:16] for h in CONTACT_SLOT_HIDDEN):
            kind, show = "hidden", win
        elif not win or any(win.startswith(b) for b in CONTACT_SLOT_BLANK):
            kind, show = "blank", (win or "（空）")
        else:
            continue            # 值是一段别的文字（栏位标题撞上表头等），不作证据
        if rank[kind] > rank.get(best, -1):
            # 尾巴只切**没合口的半截标签**，这一处不管长度：长度只在渲染那一个把手切一刀。
            # 旧写法 `show[:24]` 是裸字符切，会把中文词砍成半截词（第 30 条），而渲染那一刀
            # 本来就会把整句收到人眼读得动的长度——两把刀的上限互相不知情，留一把就够。
            # 剥尾巴也不碰档位判定（判定仍用完整窗口，切晚了才不会改结论）：窗口是 120 字
            # 硬切的，切在标签中间时 `CONTACT_SLOT_TAG_RE` 要不到 `>`、剥不动，于是
            # `<img style="margi` 这种残句会一路活到报告里（2026-11-16 第十二轮实机马可波罗）。
            # 整窗就是一截未合口标签时**退回原文**而不是编一句描述：`<a href="tel:13800…` 这种
            # 切在标签里的窗口本身就是 miss 档唯一的凭据，换成「窗口里只剩半截标签」这种话
            # 等于把号码从证据里弄没了——那是比半截词更糟的那类谎（说了句不存在于页面的话）。
            tail = CONTACT_SLOT_DANGLING_RE.sub("", show).strip() or show
            best, evid = kind, f"{m.group(0).strip()}→{tail}"
    return (best or "no_slot"), evid


# 分档的人话名字：日志、GUI 看板、体检报告三处只许取这一张表。
# 各自写一句描述就会出现「同一笔账三种说词」，而这一本账的用处就是把说词定到一个处方上。
NC_KIND_LABEL = {
    "miss": "栏位里本有号码、我们一条也没抽走（解析缺陷，该修代码）",
    "hidden": "站方藏着（点击查看/登录/联系客服登记才给）",
    "blank": "站方这一家没登（栏位写 -／暂无，不是缺陷）",
    "no_slot": "这一页没中文联系方式栏（无从判定）",
    "unknown": "本站没留正文证据（判不了，先接 _keep_detail）",
    # 「详情不取页」与「忘了接把手」必须分成两档：前者是本站定的设计边界（带理由声明），
    # 后者是可修的记账漏接。合成一句「先接 _keep_detail」就是给一个已经声明过的站
    # 开了一张执行不了的处方（2026-11-16 第十轮实机：跨国采购网详情吃 IP 配额、声明在先，
    # 报告却写着「先给这站的 crawl_detail 接上 _keep_detail」）。
    "no_page": "本站详情不取页（已声明），没有单店正文可判",
}
# 渲染时的固定序：先报我们自己的锅（miss/unknown），再报设计边界（no_page），最后报站方的锅。
# 没这一张表就会按字典序排出「blank 在前、miss 在后」，读的人第一眼看到的就是“不是缺陷”。
NC_KIND_ORDER = ("miss", "unknown", "no_page", "hidden", "blank", "no_slot")


def nc_kinds_text(kinds, evidence=None):
    """把「无联系方式」的责任分档收成一句人话——**渲染只此一个把手**（日志/GUI 看板/体检报告共用）。

    为什么由记账的主人措词，而不是让每个读的人各写一遍：这一本账的全部用处就是把处方定到
    一句话上，三处各写一遍就会出现「同一笔账三种说词」——界面上写「站方没给」、报告里写
    「取号通路」，而这两个方向的动作是相反的。空账返回空串：调用方据此**不说**这一格，
    不许编一句「无异常」顶上去。
    """
    kinds = kinds or {}
    if not kinds:
        return ""
    ev = evidence or {}
    order = [k for k in NC_KIND_ORDER if k in kinds]
    order += sorted(k for k in kinds if k not in NC_KIND_ORDER)
    seg = []
    for k in order:
        one = f"{NC_KIND_LABEL.get(k, k)} {kinds[k]} 家"
        if ev.get(k):
            one += f"（证据：{short_reason(ev[k], 46)}）"
        seg.append(one)
    return "｜".join(seg)
# ── [NC-BLOCK-END]


# ── [WAIT-BLOCK-BEGIN]
# 「这一路正停在等人」的措辞只这一处定义（名单通道 site_adapters 直接 import 本函数，
# 1688 镜像那份逐字节相同、由门禁按标记对拍）。由来：看板、结束日志、实机体检读的是
# 同一个 `waiting` 键，措辞在一处分叉，另一处就会出现「在等登录」被读成「采不到」。
# 2026-11-16 又补一行：site_adapters 里那句注释一直写着「与 cdp_base._waiting_human 用同两
# 个词」，而 cdp_base 这一半**从来没把这格赋过值**——关键词站等人工过验证等到超时，体检
# 报告仍写「等人=（无）」。注释声称同源而代码并不同源，比两处分叉更坏：它让下一个查这个
# 问题的人直接跳过这一格。
# 「在等登录」有两种输入形状：1688 一路是 `_detect_block()` 交回的那枚哨兵（值恒定、
# 不含任何中文词），其余各路交回的是一句人话。两种形状都只在这里认一次：调用方
# 各自 `== "__LOGIN__"` 或各自 `"登录" in reason`，就又长成两把尺子。
# 调用方**已经确凿知道**是哪一种时（1688 那一路按哨兵判，不按文案猜）显式传进来：
# 措辞层再猜一次就会与流程自己的判定分叉——账上写「在等登录」、循环却在等验证码。
LOGIN_WAIT_SENTINEL = "__LOGIN__"
HUMAN_WAIT_LOGIN_WORDS = ("登录", "扫码", "未登录", "会话失效", "空壳")
HUMAN_WAIT_LOGIN_LABEL = "登录"
HUMAN_WAIT_MANUAL_LABEL = "人工过验证"


def human_wait_is_login(reason):
    """这句拦截原因的主语是不是「要人登录/会话已失效」（而不是要人过验证码）。"""
    text = str(reason or "")
    return text == LOGIN_WAIT_SENTINEL or any(w in text for w in HUMAN_WAIT_LOGIN_WORDS)


def human_wait_label(reason, login=None):
    """拦截原因 → 要等人做什么（HUMAN_WAIT_LOGIN_LABEL / HUMAN_WAIT_MANUAL_LABEL）。

    login=None 时才按词认；认不出就算后者：「人工过验证」是这盏灯里更保守的那一档
    ——它不会把「等你登录」说成「你去过个码」，而说错方向的代价是人白过一遍验证。
    """
    if login is None:
        login = human_wait_is_login(reason)
    return HUMAN_WAIT_LOGIN_LABEL if login else HUMAN_WAIT_MANUAL_LABEL


def human_wait_hold(owner, reason, login=None):
    """点亮这盏灯，并交回改之前的那一格（配对见 `human_wait_release`）。

    为什么「点灯」本身也要一枚把手而不只是措辞：名单通道等验证是会嵌套的（爱企查的
    `_wait_unblock_limited` 会再回调一次 `_wait_unblock`），内层收尾必须**原样归还**外层那一格，
    而不是清成空串——清空会让外层还在等人时灯先灭，那是把「在等验证」报成「采不到」。
    「归还上一个值」这件事在通道层写出来就是一个 `owner._waiting_human = prev` 的旁路赋值，
    而形状判据只能认把手：所以这里把整套灯的状态机接走，通道层一个词也不碰。
    """
    prev = str(getattr(owner, "_waiting_human", "") or "")
    owner._waiting_human = human_wait_label(reason, login)
    return prev


def human_wait_release(owner, prev=""):
    """把灯交还给 `human_wait_hold` 之前那一格（传空串就是普通灭灯）。

    只接 hold 交回的那个值，不接受调用方自己现造的字符串——否则「恢复」与「点亮」
    又分不开，措辞层就白立了。
    """
    owner._waiting_human = str(prev or "")
# ── [WAIT-BLOCK-END]


def page_ledger_line(page_fail, last_page_error, cap=60):
    """通道账那一格怎么念：计数与原因**分家**，所以两半都得念，不许用前者压掉后者。

    `page_fail` 只数「列表那一页整页没取到」（为什么只数列表页见 `_note_page_fail`），
    `_last_page_error` 讲的是「整轮最终败在什么上」，深链的连接类失败只进后者。
    由来（2026-11-16 实机第十三轮 顺企网）：详情腿 14 次 TLS 握手失败全在日志里，
    而收尾与体检都按 `if page_fail` 决定开不开口，账上就成了「取页失败 0 页｜最后一次
    =（无）」——而汇总行的处方恰好写着「去看取页失败那一格非零」，等于把人指向一个 0。
    旧写法只错在「用计数决定原因说不说」：计数为 0 依然要念，因为那一格本来就不归它管。
    两处渲染（采集器收尾账目、实机体检报告）与 1688 镜像那一份共用本把手。
    """
    pf = int(page_fail or 0)
    err = short_reason(last_page_error, cap)
    if not pf and not err:
        return ""
    return f"取页失败 {pf} 页（最后一次：{err or '未记录'}）"


def _request_error_detail(text, cap):
    """把异常文本里「人能照着处置」的那一段剥出来：Caused by 之后优先，池头之后其次。"""
    seg = _ERR_CAUSED_RE.split(text, 1)[-1] if _ERR_CAUSED_RE.search(text) else text
    seg = _ERR_OBJ_RE.sub("", seg)
    seg = _ERR_POOL_HEAD_RE.sub("", seg)
    seg = _ERR_CLS_RE.sub("", seg)
    seg = seg.replace("'", "").replace('"', "")
    seg = re.sub(r",\s*,", ", ", seg)
    seg = re.sub(r"^[\s(),]+|[\s(),]+$", "", seg)
    seg = re.sub(r"\s+", " ", seg).strip()
    # 上面把包装括号一路剥掉以后，消息自带的括号可能只剩左半边，这里补回去——
    # `(connect timeout=8)` 被写成 `(connect timeout=8` 会让下一个人在日志里找不著头脑
    opens = seg.count("(") - seg.count(")")
    if opens > 0:
        seg = seg + ")" * opens
    # 收口一律走 short_reason：以前这里是 `seg[:cap]`，正好把 `(connect timeout=25)` 截成
    # `(connect timeout=2` ——分型器补的右括号又被下一档 [:60]/[:70] 剪掉，实机就是这么读出半截词的
    return short_reason(seg, cap)


def request_error_shape(err, cap=160):
    """请求层失败原因 → `【类别】host:port｜关键证据`（类别在最前，截不丢）。

    幂等：已经带类别的原文直接按 cap 收口，不再二次分型——各站 except 分支先调它一次
    （为了打日志），翻页层随后还会把它交给 `_note_request_error` 记账，两次必须同一句。
    类别写在括号里的那一种（`列表页导航失败（【被拦停】…）`）同样算「已经带类别」：
    把结论提回开头、剩下的当证据，**绝不降级成【未分型】**（词表与由来见 _OWN_KIND_TOKENS 上方）。
    认不出形状时返回 `【未分型】<清洗过的原文>`，**绝不返回空串、也绝不说「采不到」**：
    这一格将来还要长出新档，先让「没分上型」这件事本身可见。"""
    if err is None:
        return "未知请求失败"
    text = str(err) if isinstance(err, str) else f"{type(err).__name__}: {err}"
    text = re.sub(r"\s+", " ", text.strip())
    if not text:
        return "未知请求失败"
    if text.startswith("【"):
        return short_reason(text, cap)
    low = text.lower()
    kind = ""
    rest = text
    nest = _NESTED_KIND_RE.match(text)
    if nest:
        kind = nest.group(2)
        rest = nest.group(1) + text[nest.end():]
    else:
        for name, needles in _REQUEST_ERROR_KINDS:
            if any(n in low for n in needles):
                kind = name
                break
    if not kind:
        m = _ERR_HTTP_STATUS_RE.search(text)
        if m:
            code = int(m.group(1))
            kind = dict(_HTTP_STATUS_KINDS).get(code) or (
                f"HTTP {code}·站点故障" if code >= 500 else f"HTTP {code}·请求被拒")
            rest = (text[:m.start()] + " " + text[m.end():]).strip("（）() ,:；;")
    if not kind:
        kind = "未分型"
        rest = text
    head = f"【{kind}】"
    pool = _ERR_POOL_RE.search(text)
    if pool and pool.group(1):
        head = f"{head}{pool.group(1)}:{pool.group(2)}"
    detail = _request_error_detail(rest, max(0, cap - len(head) - 1))
    return f"{head}｜{detail}" if detail else head

class Ali1688Crawler:
    PLATFORM_NAME = "1688"
    PLATFORM_SLUG = "1688"
    BASE_URL = "https://www.1688.com/"

    def __init__(self, debug_port=9244):
        self.debug_port = debug_port
        self.driver = None
        self._browser_lock = threading.Lock()
        self._attach_only = False

        self.signals = CrawlerSignals() if HAS_PYQT else None
        self.results = []
        self.results_lock = threading.RLock()

        self._crawling = False
        self._stop_requested = False
        self._pause_requested = False

        # 采集速度：基础间隔（秒），会被随机抖动放大；被拦截时自动上调
        self.delay_between = 3.0
        self.jitter = 1.5
        self._current_delay = self.delay_between
        # 熔断（大厂 anti-abuse 的 circuit-breaker 思想）：短窗口内被反复拦截说明已进入
        # 平台限流区，继续逐页死磕只会把软限流熬成硬封号；此时强制长冷却、必要时收本轮。
        self._block_times = []       # 近期被拦时间戳
        self._block_window = 900     # 统计窗口（秒）
        self._max_blocks = 5         # 窗口内达此次数 → 判持续限流，优雅结束本轮
        # 本轮「为什么收的」：与 cdp_base 同一口径的耐久归因（拦停状态收轮前会被复位，
        # 收尾那句读不到），只用于日志归因，不参与控制流。
        self._run_abandon = ""
        self.max_pages = 10          # 每个关键词最多翻几页（1688 上限约 50）
        self.main_channel = True     # 主搜渠道（selloffer/offer_search，每页约50家，池子最深）
        self.company_channel = True  # 同时采集供应商(公司)渠道（实测产出为商品渠道数倍）
        # 站外 SEM 落地页渠道（www.1688.com/zw/page.html?hpageId=old-sem-pc-list）。
        # 2026-11-04 客户现场取证判死：这一页**既不吃 keywords 也不吃 province**，
        # 返的是站外广告默认流——搜「海洋能」能回 18 家义乌饰品厂/玩具厂，而且地址清一色
        # 外省；那一轮 1688 入库的 91 条全部来自它（详情URL 尾部均为 `src=zhanwai`），
        # 其中能按地址/公司名确认属广东的只有 12 条。它又是唯一一条不走类目护栏的渠道，
        # 所以默认关掉（多平台适配器直接置 False）；保留开关供单独验证该页面是否已修。
        self.sem_channel = True
        self.relevance_guard = True  # 主搜渠道类目相关性过滤：类目不符的公司不进联系方式页
        self.max_records = 0         # 采集到 N 条自动收尾（0=不限，低频实测/限量用）
        # 省份精准采集：站方原生 `&province=<简体短名>` 参数（2026-11-04 共享 Chrome
        # 探针 A/B：主搜 20/16 家重合仅 6，公司搜 17/20 家重合 3）。注意：探针只测过
        # 主搜/公司搜两个渠道，站外 SEM 落地页实测不吃这个参数（见上 sem_channel 注），
        # 所以拼参数只是一道提高命中率的优化，**不构成属地保证**：属地核验在适配器里
        # 按地址/公司名逐条把关。空=全国不限；由 b2bcollect GUI 省份选择下发。
        self.province = ""
        self._pf_dropped = 0         # 属地明确外省丢弃计数（适配器核验；结束行如实汇报）
        self._pf_unknown = 0         # 属地无法确认丢弃计数（同上，并回流待核实名单）
        self._filtered_count = 0     # 本轮类目过滤计数
        # **逐词**相关性砍单账（与 cdp_base 同口径，由来见 cdp_base.__init__ 里 kw_filtered 那段）：
        # 1688 一轮 0 家时，「本机闸门砍的」与「1688 真没这些公司」是两种完全不同的处方，
        # 只有全局计数分不开这两句（判据见 test_block_triage_offline 第 ⑭ 节）。
        self.kw_filtered = {}
        self.kw_early = {}
        # 砍单记名 + 无联系方式分档（口径同 cdp_base.__init__，由来见 cdp_base 那两段注释）
        self.kw_early_names = {}
        self.kw_filtered_names = {}
        # 「站方回了多少条」的账（与 cdp_base 同一族：总账 + 两本分词账，走 _note_listed 把手）：
        # 1688 的收尾那句「本轮零产出」要在「链路没通／增量库全跳过／这一词本就只回几条」
        # 之间分叉，而旧写法连 `stat_listed` 都没在这份里赋过值——读它的地方只能拿 getattr
        # 兑一个 0，于是「只要有一页取失败」就被念成「一页都没取到」（第十三轮实机查到）。
        self.stat_listed = 0
        self.kw_items = {}
        self.kw_claimed = {}
        self._nc_kinds = {}
        self._nc_evidence = {}
        self._detail_html = ""
        # 本轮因**判定规则更新**而重新评估的旧「类目不符」家数（口径同 cdp_base）
        self._rel_refilter = 0
        self.debug_collect = False   # 开发调参：记录类目过滤判定输入（不影响生产行为）
        self._guard_debug = []
        self.expand_related = False  # 自动扩展相关关键词（1688 relativeWords 多为全站热搜，保守过滤后追加）
        # 增量种子库（大厂爬虫的 frontier 去重思想）：跨运行记住「已处理过」的公司，
        # 再跑同一批关键词时命中即跳过，绝不重复戳同一家详情页/联系页。既省请求（防封），
        # 又能把每次新出现、之前没采到的公司增量补齐——重复挂过夜反而总数据量最大。
        self.incremental = True
        self._seen_store = {}        # {key: {"at": ts, "st": 状态}}，持久化到 seen_store.json

        self._data_dir = self._stable_data_dir()
        self._seen_store_path = self._data_dir / "seen_store.json"
        self._profile_dir = self._profile_path()
        self._seen_members = set()   # 本轮已处理公司（memberId）去重
        self._seed_clear_error = ""  # 上次「清空种子库」没清成的原因（见 clear_seen_store）
        self._early_skipped = 0      # 本轮列表页早过滤计数（未进详情即判无关）
        # 本轮「增量库已含所以跳过」计数：没有这一笔账，界面上就只剩「新增公司 0 家」，
        # 用户分不清「已经采全了」和「去重口径把活锁死了」——后者恰恰是本轮 1688 出 0 的形态
        self._incremental_skipped = 0
        # 本轮「一页都没取到」的账（2026-11-15 实机体检抓到，口径与 cdp_base 一致）：
        # 请求层失败（导航没成/会话断开）和「这一页真的没有公司」在旧口径里长得一模一样，
        # 而两者的处置完全相反——前者查出口和登录态，后者改代码，所以必须分账分文案。
        self._page_fail = 0
        self._last_page_error = ""
        # `_goto` 当场那一次导航失败的分型原因（每次调用开头清空，只对本次调用有效）：
        # 列表页那句「导航失败」以前只能说「具体原因见紧邻上一行」，界面与体检器问不出类别，
        # 而**只走浏览器**的那几站失败恰恰全在这一层。取用见 _goto_fail_reason。
        self._last_goto_error = ""
        # 本轮结束那一刻是不是还在等人（登录／人工过验证）：1688 上一轮把 1500 秒体检
        # 预算全烧在等登录上，最后 0 家被报成「采不到」。它既没被拦停熔断，也没失败，
        # 只是在等人——这一格就是为了让界面与体检器能问出这一句。
        self._waiting_human = ""
        self._guard_kw = ""          # 类目过滤：当前关键词
        self._guard_pred = None      # 类目过滤：关键词级 PRECISE 预测类目集（id+名）

    # ── 日志 / 结果 ────────────────────────────────────────────
    def _log(self, msg):
        # 前缀与 cdp_base._log 同形（[时间][平台] …）：1688 在 b2bcollect 里与另外九路
        # 并行打同一本日志，只有本路不带平台名——多路混排时这一路说的话认不出是谁说的
        # （2026-11-14 实机体检日志里第 13 行就是这样一句无主的话）。
        line = f"[{time.strftime('%H:%M:%S')}][{self.PLATFORM_NAME}] {msg}"
        print(line, flush=True)
        if self.signals:
            try:
                self.signals.log_signal.emit(line)
            except Exception:
                pass

    def _emit(self, name, *args):
        if self.signals:
            try:
                getattr(self.signals, name).emit(*args)
            except Exception:
                pass

    def _shape_request_error(self, err):
        """站点采集器在 except 分支里把异常整成一句带类别的话（**只整形、不记账**）。

        为什么不顺手调 `_note_request_error`：重试循环里每一次失败都会经过这里，而记账那一格
        讲的必须是「这一轮翻列表最终败在什么上」。中间某次超时、下一次就成功的情形很常见，
        把中间态记进去会让一页正常取回的关键词在收尾时挂着一条旧故障——那种假红比不说更难查。
        """
        return request_error_shape(err)

    def _note_request_error(self, err):
        """把请求层最后一次失败的原因留在**机器可读**的地方（只留原因，不计数）。

        口径与 cdp_base 的同名方法一致：日志会被 tail 截掉、会被 ⚠ 淹没上千行，而
        「本轮为什么 0 家」的归因必须能被界面和体检器直接问出来，不能靠读文案。"""
        # 走分型器而不是 str(err)[:160]：截断从头部砍，砍掉的恰好是唯一决定方向的
        # `(Caused by …)` 那一段（详见 request_error_shape 上方的由来）。
        self._last_page_error = request_error_shape(err, cap=160)

    def _cut_sample(self, bucket, kw, name):
        """砍单记名的唯一把手（每词只留前 CUT_SAMPLE_CAP 家，口径与 cdp_base 一致）。"""
        text = str(name or "").strip()
        if not text:
            return
        got = bucket.setdefault(kw, [])
        if len(got) >= CUT_SAMPLE_CAP:
            return
        got.append(short_reason(text, CUT_NAME_CAP))

    @staticmethod
    def _cut_note(bucket, kw, stage):
        """砍单样本渲染成一句「（复核砍掉：A、B、C）」；没有名单返回空串（口径同 cdp_base）。"""
        got = bucket.get(kw) or []
        if not got:
            return ""
        return f"（{stage}砍掉：{'、'.join(got)}）"

    def _keep_detail(self, html):
        """把这一家的联系页正文留给无联系方式分档（口径同 cdp_base._keep_detail）。"""
        if isinstance(html, str) and html:
            self._detail_html = html
        return html

    def _note_no_contact(self):
        """「无联系方式」按责任分档的唯一把手（六档处方与由来见 cdp_base._note_no_contact）。"""
        html = self._detail_html or ""
        if html:
            kind, evid = contact_slot_state(html)
        else:
            # 声明过「详情不取页」的站记 no_page（设计边界），没声明的记 unknown（把手没接）：
            # 两者处方不同，合成一句就会给已声明的站开一张执行不了的处方
            declared = str(getattr(self, "NO_DETAIL_EVIDENCE", "") or "")
            kind = "no_page" if declared else "unknown"
            evid = declared or "本站详情页没把正文留给分档判据（接 _keep_detail 才有这一档）"
        self._nc_kinds[kind] = self._nc_kinds.get(kind, 0) + 1
        if kind not in self._nc_evidence:
            # 账上存**原文**、这里不收口：长度只在渲染那一个把手处切一刀（nc_kinds_text）。
            # 记账处先切一次、渲染处再切一次就是 30a-2 那一形「调完把手又补一刀」（口径同 cdp_base）。
            self._nc_evidence[kind] = evid.strip()
        self._detail_html = ""

    def _note_early_skip(self, kw, name=""):
        """列表页早过滤**唯一**的记账把手：全局数 + 逐词数 + 逐词样本一起加（口径同 cdp_base）。"""
        self._early_skipped += 1
        self.kw_early[kw] = self.kw_early.get(kw, 0) + 1
        self._cut_sample(self.kw_early_names, kw, name)

    def _note_detail_filter(self, kw, name=""):
        """详情页类目复核砍单的**唯一**记账把手（计数与样本，口径同 cdp_base）。"""
        self._filtered_count += 1
        self.kw_filtered[kw] = self.kw_filtered.get(kw, 0) + 1
        self._cut_sample(self.kw_filtered_names, kw, name)

    def parse_listed_total(self, html):
        """站方在列表页上**自己说**的结果总数（默认不猜，返回 None；子类按站点实现）。

        为什么要有这一格：「我们解析到 N 条」只说明我们的手抓到了几条，说明不了站方给了
        几条。两者不一致时该修的是解析口径（我们的锅），而旧账本只有后者，于是「站方回了
        30 家、卡片改版只认出 1 家」会被写成「这一词货源薄」——把人支使去换词，真凶就在
        解析函数里（2026-11-16 实机 中国制造网「窗帘导轨」取证时正是靠页面上那句
        「共找到 1 个」才把「货源薄」与「解析漏」分开）。取不到就交回 None：说不出＝不知道，
        不许编一个数上去。
        """
        return None

    def _note_listed(self, kw, count, claimed=None):
        """列表页「站方回了多少条」的**唯一**记账把手：总账、分词账、站方自报账一起落。

        为什么必须走把手而不是各站自己 `self.stat_listed += len(entries)`：总账只有一个
        全局数，回答不了「这一词为什么零产出」；而 `_kw_zero_reason` 要说「站方只回 1 条
        （换词换索引）」还是「回 30 条全被砍（改相关性口径）」，凭的正是这一词的条目数。
        各站自己累加时，漏写一处＝那一词的条目账永远是空的，归因句就只能退回旧那一种念法
        （与 `_note_early_skip`/`_note_detail_filter` 同一族纪律，判据见第 33 条硬门）。
        `claimed` 是站方自报的总数（只有实现了 `parse_listed_total` 的站才有）：每一页报的
        都是整个查询的总数，所以按页取**最大值**而不是求和。
        """
        n = int(count or 0)
        self.stat_listed += n
        key = str(kw or "")
        if not key:
            return n
        self.kw_items[key] = self.kw_items.get(key, 0) + n
        if claimed is not None:
            c = int(claimed)
            self.kw_claimed[key] = max(self.kw_claimed.get(key, 0), c)
        return n

    def _note_page_fail(self, err=""):
        """记一笔「这一页列表根本没取到」——只在翻页层调用，一页最多一次。

        原因与计数分家（同 cdp_base）：详情页的失败不该混进「取页失败 N 页」，否则这个
        数随公司数线性增长，收尾的归因（⛔ 链路未通）就没法用它讲话了。"""
        if err:
            self._note_request_error(err)
        elif not self._last_page_error:
            self._note_request_error("请求层未给出原因（见紧邻上一行）")
        self._page_fail += 1
        return self._page_fail

    def _no_result_line(self, kw, page_label, html, noun="公司数据"):
        """「这一页没有公司」与「这一页压根没取到」必须是两句话（同 cdp_base）。

        由来：1688 这一路把「等登录/被风控空页/真的没数据」三种 0 家都说成「无数据」，
        实机体检因此把「没人登录」报成「1688 采不到」。判空页时还要看请求层留没留过原因，
        留了就是链路，没留才是「翻到底/空切片」。"""
        got_page = bool(html.strip()) if isinstance(html, str) else bool(html)
        if not got_page:
            if self._last_page_error:
                return (f"  「{kw}」{page_label}【一页都没取到】（请求层没换回页面，"
                        f"不是本站没有{noun}；最后一次：{short_reason(self._last_page_error, 70)}）")
            return (f"  「{kw}」{page_label}没换回页面（请求层没有报错，多为超出末页的 "
                    f"404 或频控空页；是哪一种看紧邻上一行）")
        return f"  「{kw}」{page_label}无{noun}"

    def snapshot_results(self):
        with self.results_lock:
            return [dict(r) for r in self.results]

    def results_live(self):
        """收尾阶段**就地补字段**用的实时视图（返回 results 里的原字典，不是副本）。

        口径与 b2bcollect_build/cdp_base.py 的同名方法一致：官网回采联系方式必须
        写回真身，整体替换 results 会把并发 append 盖掉；只在采集线程已结束的
        收尾阶段用。对拍见 test_data_dir_offline.py 第 15 节。
        """
        with self.results_lock:
            return list(self.results)

    def _apply_province_filter(self, rec):
        """省份精准采集的入库关卡（基类默认不拦）。

        基类只负责把 `&province=` 拼进搜索 URL（服务端筛），本地**不拿已选省份臆测填
        所在区域**——历史上那句回填把 91 条各省公司全盖章成「广东」，用户在表里看到的
        就是伪数据，连人工抽查都抽不出来（地址写着河北，区域写着广东，谁也不敢信）。
        真正的属地核验由多平台适配器（b2bcollect site_adapters.Ali1688Site）覆写本方法：
        它能 import 到 province_filter 词表；本文件要能给独立工具单用，不依赖那个模块。"""
        return rec

    def _add_result(self, rec):
        rec = self._apply_province_filter(rec)
        if rec is None:
            return
        with self.results_lock:
            self.results.append(rec)
        self._commit_to_store(rec)     # 到终态的公司写进种子库（临时失败不写，下轮重试）
        # 护栏条数：达到即自动收尾（低频实测/限量采集用）
        if self.max_records and len(self.results) >= self.max_records:
            self._log(f"🛡 已达限量 {self.max_records} 条，自动停止")
            self._stop_requested = True

    # ── 增量种子库（跨运行去重）───────────────────────────────
    # 只把「已到终态」的公司永久记住：成功/无电话/无联系方式（卖家确为暂无）、
    # 以及类目不符(已过滤)。凡带「拦截/未解除/导航失败/未登录/待重试」的临时失败
    # 一律不记，下一轮会重新尝试——这样既不重复戳已处理过的家（省请求防封），
    # 又不会把「这次没采成」的公司永久漏掉（保数据最全）。
    TRANSIENT_MARKS = ("拦截", "未解除", "导航失败", "未登录", "待重试")
    # 「这次没取到联系人」不是终审结论：站点可能临时反爬、页面可能改版。空联系人的条目
    # 下一轮重采补一次，最多补 CONTACT_RETRY_MAX 次就永久锁住——与 cdp_base 同口径（两处
    # 必须同规则，对拍见 test_data_dir_offline.py 第 14 节）。1688 这一路以前没有这套：
    # 它把「所有」种子库键都当去重依据，等于「只要进过种子库就永不重采」，于是早期被反爬
    # 空页判成「无联系方式」的公司被永久盖章，之后每次重跑同一个词都是「59 条offer，新增
    # 公司 0 家」——用户看到的就是「以前还能采到，现在一直是 0」，而日志里一个字都没提
    # 「为什么是 0」。
    CONTACT_RETRY_MAX = 2
    # 「类目不符」在种子库里的状态字面量（各处统一引用，改文案只改这一处）
    FILTERED_STATE = "类目不符(已过滤)"
    # 规则更新后每轮最多重新评估多少家旧「类目不符」：一次性放开上千家＝把风控预算
    # 全花在补旧账上，反而挤掉本轮该采的新货。取的是按判定时间最近的一批。
    REL_REFILTER_CAP = 200
    # 判定规则指纹的原料：这几个函数任一改动，指纹即变，旧的「类目不符」盖章作废
    RELEVANCE_RULE_ATTRS = ("_kw_relevant", "_list_relevant")
    # 指纹取不到时的降级常量（口径见 _stale_filtered：取不到就不动存量）
    REL_REV_UNKNOWN = "rev0"
    _REL_REV = None

    @staticmethod
    def _has_any_contact(rec) -> bool:
        """这条记录到底有没有联系方式：联系人/电话/手机/邮箱 四者取一。

        刻意不看 ``网站``：有官网只说明「还能再试试」，不说明联系方式已经取到；而它正是
        新加的「官网回采联系方式」要处理的对象，把它算进「已有联系方式」会让那一轮永远
        排不到活。
        """
        return any(str(rec.get(k) or "").strip()
                   for k in ("联系人", "电话", "手机", "邮箱"))

    @classmethod
    def _entry_locked(cls, entry) -> bool:
        """种子库条目是否仍应「命中即跳过」。空联系人且重试未用尽者不锁定，
        让下一轮重跑自动补采。旧版本写入了无 ct 字段的条目一律按已完整处理：
        否则一次普通重跑会把历史的上千家公司的详情页重新访一遍，反而加重风控。"""
        if not isinstance(entry, dict) or "ct" not in entry:
            return True
        if entry.get("ct"):
            return True
        return int(entry.get("rt") or 0) >= cls.CONTACT_RETRY_MAX

    @classmethod
    def _relevance_revision(cls):
        """相关性判定**规则**的指纹（版本号）。

        增量缓存的是判定结论而不是事实：这一家算不算目标同行，取决于二字组那套命中规则
        长什么样、有哪些前缀后缀被剥掉。规则一改，旧结论必须整体作废，否则修好的口径对
        被误杀入库的那批家永远不生效，用户只能手动清库（等于把修复作废）。
        指纹只取判定真正读到的输入，与运行时机、机器无关；取不到就退回常量——宁可不做
        重评，也不要每轮算出不同版本号把种子库反复洗一遍。
        """
        if cls._REL_REV is None:
            try:
                import hashlib
                import inspect
                import keyword_expander as _ke
                blob = repr(sorted(getattr(_ke, "PEER_CATEGORY", {}).items()))
                for name in cls.RELEVANCE_RULE_ATTRS:
                    # 判定规则一半挂在类上（is_relevant/_peer_words）、一半是模块级函数
                    # （relevance_hit/kw_grams，清洗严格档与采集松档共用同一份）：两处都要取到
                    obj = getattr(cls, name, None) or globals().get(name)
                    if obj is None:
                        continue
                    blob += inspect.getsource(obj.__func__ if hasattr(obj, "__func__") else obj)
                cls._REL_REV = hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]
            except Exception:
                cls._REL_REV = cls.REL_REV_UNKNOWN
        return cls._REL_REV

    @classmethod
    def _stale_filtered(cls, entry):
        """这一条种子库记录是不是**旧规则**下判的「类目不符」——是就不该再命中即跳过。

        指纹取不到（REL_REV_UNKNOWN）时一律判「不陈旧」：降级必须往「不动存量」的方向降,
        否则一次导入失败就把上千家旧盖章一次性放开重戳，那是自己给自己招风控。"""
        rev = cls._relevance_revision()
        if rev == cls.REL_REV_UNKNOWN:
            return False
        return (isinstance(entry, dict) and entry.get("st") == cls.FILTERED_STATE
                and entry.get("rv") != rev)

    def _stale_filtered_keys(self):
        """本轮值得重判的旧「类目不符」键（按判定时间倒序取最近 REL_REFILTER_CAP 家）。"""
        stale = [(str((v or {}).get("at", "")), k) for k, v in self._seen_store.items()
                 if self._stale_filtered(v)]
        stale.sort(reverse=True)
        return {k for _, k in stale[:self.REL_REFILTER_CAP]}

    def _locked_keys(self):
        return {k for k, v in self._seen_store.items()
                if self._entry_locked(v)} - self._stale_filtered_keys()

    def _seen_store_pending(self):
        """种子库还欠着几次「补联系人」的家数（收尾账目与渠道体检都用它）。"""
        try:
            return sum(1 for v in self._seen_store.values() if not self._entry_locked(v))
        except Exception:
            return 0

    def _is_terminal(self, rec):
        st = rec.get("状态", "") or ""
        return st != "" and not any(m in st for m in self.TRANSIENT_MARKS)

    def _rec_seen_keys(self, rec):
        keys = []
        mid = str(rec.get("memberId") or "")
        if mid:
            keys.append(mid if mid.startswith("b2b-") else "shop:" + mid)
        name = self._norm_name(rec.get("公司名称") or "")
        if name:
            keys.append(name)
        return keys

    def _load_seen_store(self):
        try:
            with open(self._seen_store_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._seen_store = {str(k): v for k, v in data.items()}
        except Exception:
            self._seen_store = {}
        return len(self._seen_store)

    def _save_seen_store(self):
        try:
            tmp = self._seen_store_path.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._seen_store, f, ensure_ascii=False)
            tmp.replace(self._seen_store_path)
            return True
        except Exception as e:
            self._log(f"⚠ 种子库保存失败：{str(e).splitlines()[0]}")
            return False

    def _filtered_mark(self, rec):
        """这一家在本关键词下的旧「类目不符」章键（与 _commit_to_store 的写法一一对应）。"""
        mid = str(rec.get("memberId") or "")
        if not mid:
            return ""
        return f"{rec.get('关键词', '')}#{mid}"

    def _commit_to_store(self, rec):
        if not self.incremental or not self._is_terminal(rec):
            return
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        st = rec.get("状态", "")
        if st == self.FILTERED_STATE:
            # 类目不符是「关键词相关」的判定：同一家对 A 词不相关、对 B 词可能正是目标，
            # 故只按「关键词#公司」作用域记录，换关键词重跑时仍会重新评估，绝不全局误跳。
            # rv＝判定规则指纹：规则更新后这一条旧结论自动作废（见 _relevance_revision）。
            mid = str(rec.get("memberId") or "")
            if mid:
                self._seen_store[f"{rec.get('关键词','')}#{mid}"] = {
                    "at": ts, "st": st, "rv": self._relevance_revision()}
            return
        # 重判后改判「相关」并入库：旧的那枚「类目不符」章必须当场撕掉，否则这条陈旧
        # 记录每轮都被算进待重判名单，重判账永远收敛不了（一家白重判一次就白戳一次详情页）
        self._seen_store.pop(self._filtered_mark(rec), None)
        for k in self._rec_seen_keys(rec):
            old = self._seen_store.get(k) or {}
            has_contact = self._has_any_contact(rec)
            entry = {"at": ts, "st": st, "ct": 1 if has_contact else 0}
            if not has_contact:
                entry["rt"] = int(old.get("rt") or 0) + 1
            self._seen_store[k] = entry

    def clear_seen_store(self):
        """清空跨运行种子库（用户想对同一批词从头重采时用）。

        三样一起清：盘上文件、内存字典、**内存去重集合** ``_seen_members``。只清前两样
        等于「点了清空、这一轮还在命中即跳过」——站点侧真正拦住一家公司的是后者。
        unlink 失败（只读卷/权限/被占用）不再被 except 吞掉后照样报成功：那句
        「历史种子库已清空」是用户对这一格的唯一凭据，它必须是事实。
        口径与 b2bcollect 一侧的 ``cdp_base.reset_seen_store`` 逐字一致（本包不 import
        cdp_base，所以这是同一份语义的第二处实现，改动必须两处一起改，对拍见
        test_block_triage_offline 第 ⑰ 节）。"""
        self._seen_store = {}
        self._seen_members = set()
        self._seed_clear_error = ""
        try:
            if self._seen_store_path.exists():
                self._seen_store_path.unlink()
        except Exception as e:
            self._seed_clear_error = f"{type(e).__name__}：{short_reason(str(e).splitlines()[0], 60)}"
            return False
        return True

    # ── Chrome 生命周期 ────────────────────────────────────────
    @staticmethod
    def _stable_data_dir():
        """种子库/数据目录的落点：口径全交给 runtime_paths，本函数只是一层旧名字的门面。

        PyInstaller onefile 下 ``__file__`` 指向每次启动都变、退出即删的 ``_MEIxxxx``，
        拿它当数据目录会让 ``seen_store.json`` 既写不进也活不到下一轮（跨运行去重彻底
        失效）；Mac .app 被 Gatekeeper 转置后整卷只读，包内 mkdir 直接 OSError 30。这两
        件事的解法都不是在本文件里再写一版候选链，而是问一处口径：包旁 / exe 同级 →
        ``~/.b2bcollect/ali1688_data`` → 系统临时目录，逐级写探针实测，永不返回一个写不
        进去的目录。容器名只认 ``.b2bcollect`` 一个（多一个 ``~/.ali1688`` 就等于远程排障
        时要多问一句「你那个是哪年的」）。``dev_dir`` 是本目录：开发机上跑了几年的种子库
        历史就在 ``ali1688_build/ali1688_data``，口径不许替它搬家。
        """
        return _paths.stable_data_dir("ali1688_data",
                                      dev_dir=str(Path(__file__).resolve().parent))

    def _profile_path(self):
        """Chrome 配置目录（登录态真正的家）的落点：口径全交给 runtime_paths。

        以前这里写在 ``%TEMP%\\chrome_1688_profile``，而 TEMP 每个用户各一份、清理程序
        随时可删、注销即弃：2026-11-13 实机换了 Windows 账号之后，1688 一直 0 家而八个
        不需要登录的站照常出数，界面与日志都没有一句「因为登录」。持久位置与老位置接管
        由口径那一处决定（老目录里有 Cookies 就先复制过来，绝不删源），本函数只声明
        「这个工具的老位置在哪」。
        """
        legacy = [_paths.legacy_temp_root() / "chrome_1688_profile",
                  Path("/tmp/chrome_1688_profile")]
        return _paths.browser_profile_dir("chrome_1688_profile", legacy_dirs=legacy)

    def _find_chrome(self):
        candidates = {
            "darwin": [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            ],
            "win32": [
                os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
                os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
                os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
            ],
        }.get(sys.platform, [])
        for c in candidates:
            c = os.path.expanduser(c)
            if os.path.exists(c):
                return c
        return None

    def _debug_alive(self):
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.debug_port}/json/version", timeout=1.5) as r:
                return bool(json.loads(r.read().decode("utf-8", "ignore")).get("Browser"))
        except Exception:
            return False

    def _driver_candidates(self):
        """列出所有「本地已有」的 chromedriver 候选（默认优先级序，排序交给 _driver_order）。

        为什么不能只打一个固定版本、也不能按版本号挑一个就完事：2026-11-14 实机测得
        chromedriver 156 附加 Chrome 157 **可用**，而 135 附加同一台 Chrome **失败**
        （失败文案是 'cannot connect to chrome'，看着像浏览器没响应，其实是驱动太旧）。
        所以候选必须全列出来、按与浏览器版本的接近程度排序逐个实跑，让结果说话；
        单挑「最配套的那一个」会在缺少同版本驱动时把本来能用的 156 也一并判死。"""
        exe_name = "chromedriver.exe" if sys.platform == "win32" else "chromedriver"
        cands = []
        if getattr(sys, "frozen", False):
            meipass = getattr(sys, "_MEIPASS", "")
            if meipass:
                cands.append(Path(meipass) / exe_name)
                # 新版打包：drivers/ 内置最近三个大版本，文件名注版 chromedriver.<N>.exe
                d_dir = Path(meipass) / "drivers"
                if d_dir.is_dir():
                    cands += sorted(d_dir.glob("chromedriver*"), reverse=True)
            exe_dir = Path(sys.executable).resolve().parent
            cands += sorted(exe_dir.glob("chromedriver*"), reverse=True)
        # Selenium Manager 缓存：~/.cache/selenium/chromedriver/<平台>/<版本>/chromedriver[.exe]
        cache_root = Path(os.environ.get("USERPROFILE") or str(Path.home())) \
            / ".cache" / "selenium" / "chromedriver"
        if cache_root.is_dir():
            def _ver_key(p):
                nums = re.findall(r"\d+", p.parent.name)
                return tuple(int(x) for x in nums[:4]) if nums else ()
            cands += sorted(cache_root.glob("**/" + exe_name), key=_ver_key, reverse=True)
        # 开发环境：与本仓库并列的风鸟驱动目录
        dev = Path(__file__).resolve().parent.parent / "riskbird_build"
        dev_sub = "chromedriver-win64/chromedriver.exe" if sys.platform == "win32" \
            else "chromedriver-mac-x64/chromedriver"
        cands.append(dev / dev_sub)
        out = []
        for f in cands:
            try:
                if f.is_file() and str(f) not in out:
                    out.append(str(f))
            except OSError:
                continue
        return out

    @staticmethod
    def _cand_major(path):
        """从路径推断驱动大版本：chromedriver.155.exe 文件名注版，或 SM 缓存的
        <版本目录>/chromedriver.exe；推断不出返回 0。"""
        name = Path(path).name
        m = re.match(r"chromedriver\.(\d+)", name)
        if m:
            return int(m.group(1))
        m = re.match(r"(\d+)\.", Path(path).parent.name)
        return int(m.group(1)) if m else 0

    def _driver_major(self, path):
        """跑 `chromedriver --version` 拿真实大版本（推断不出时的兜底，秒级本地调用）。"""
        try:
            out = subprocess.run([path, "--version"],
                                 capture_output=True, text=True, timeout=5).stdout
            m = re.search(r"ChromeDriver (\d+)", out or "")
            return int(m.group(1)) if m else 0
        except Exception:
            return 0

    def _browser_major(self):
        """调试端口 /json/version 的 Browser 字段（Chrome/155.0.x）取大版本。"""
        m = re.search(r"/(\d+)", self._devtools_browser())
        return int(m.group(1)) if m else 0

    def _driver_order(self, want_major=0, limit=3):
        """本地驱动按「最可能一次成功」排好的前 limit 个（与 chrome_driver.candidates 同口径）：
        大版本差在 DRIVER_MAJOR_SLACK 以内的最前，看不出版本的次之，明知差很远的垫底。
        版本看不出来不等于不配套，所以只是往后排、绝不淘汰。"""
        cands = self._driver_candidates()
        if not (want_major and cands):
            return cands[:limit]

        def _rank(f):
            m = self._cand_major(f) or self._driver_major(f)
            if not m:
                return (1, 0)
            d = abs(m - want_major)
            return (0, d) if d <= self.DRIVER_MAJOR_SLACK else (2, d)

        return sorted(cands, key=_rank)[:limit]

    def _debug_pages(self):
        """列出调试端口上的 page 标签；查询失败返回 None，空列表表示「0 标签」。"""
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.debug_port}/json", timeout=1.5) as r:
                data = json.loads(r.read().decode("utf-8", "ignore"))
            return [t for t in data if isinstance(t, dict) and t.get("type") == "page"]
        except Exception:
            return None

    def _ensure_debug_tab(self):
        """附加前保证调试端口上至少有一个标签页。
        开着但 0 标签的 Chrome 会让 Selenium 附加直接报 'unable to discover open pages'，
        此时没有可接管的页面，通过 CDP /json/new 开一个空白页兜底。"""
        pages = self._debug_pages()
        if pages is None or pages:
            return
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{self.debug_port}/json/new?about:blank", method="PUT")
            urllib.request.urlopen(req, timeout=3).read()
            time.sleep(0.8)
        except Exception:
            pass

    def _wait_debug_ready(self, tries=20):
        for _ in range(tries):
            time.sleep(1)
            if self._debug_alive():
                return True
        return False

    def _kill_stale_profile_chrome(self):
        """关掉仍占用本工具独立配置目录、却没暴露调试端口的残留 Chrome 进程。
        Chrome 按 user-data-dir 单实例：只要旧实例还锁着配置目录，新起的带端口进程
        只会把请求转交给旧实例然后自己退出，调试端口永远起不来。
        Windows 也须能清（PowerShell 按命令行匹配本工具配置目录），否则重启重试全落空。"""
        try:
            if sys.platform == "win32":
                ps = ("Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                      "Where-Object { $_.CommandLine -like '*chrome_1688_profile*' } | "
                      "Select-Object -ExpandProperty ProcessId")
                out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                                     capture_output=True, text=True, timeout=25).stdout
            else:
                out = subprocess.run(["pgrep", "-f", str(self._profile_dir)],
                                     capture_output=True, text=True, timeout=5).stdout
            pids = [int(x) for x in out.split() if x.strip().isdigit()]
        except Exception:
            return
        if not pids:
            return
        self._log(f"清理 {len(pids)} 个占用配置目录的残留 Chrome 进程…")
        for pid in pids:
            try:
                if sys.platform == "win32":
                    # 不带 /F：优雅退出，释放单实例锁（强杀会留脏 session 文件）
                    subprocess.run(["taskkill", "/PID", str(pid)],
                                   capture_output=True, timeout=10)
                else:
                    os.kill(pid, 15)          # SIGTERM：优雅退出，释放单实例锁
            except Exception:
                pass
        time.sleep(1.8)

    # Selenium Manager 联网也连同一个调试端口，救不了「连不上 Chrome」类失败；
    # 只有「驱动与浏览器版本不匹配」才值得让它在线拉配套驱动。
    DRIVER_VERSION_MISMATCH_MARKS = ("only supports chrome version",
                                     "version mismatch",
                                     "unable to match browser version")

    # 驱动与浏览器大版本差几个以内还值得一试：本机实测 156 驱动附加 Chrome 157 成功、
    # 135 驱动附加同一台 Chrome 失败。判据与 chrome_driver 保持同一份常量，别两处各写一个数。
    DRIVER_MAJOR_SLACK = getattr(_chrome_driver, "DRIVER_MAJOR_SLACK", 2)

    def _devtools_browser(self):
        """读调试端口 /json/version 的 Browser 字段（如 Chrome/142.0.x），用于诊断日志。"""
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.debug_port}/json/version", timeout=2) as r:
                return json.loads(r.read().decode("utf-8", "ignore")).get("Browser", "")
        except Exception:
            return ""

    def _attach(self):
        opts = Options()
        opts.debugger_address = f"127.0.0.1:{self.debug_port}"
        want = self._browser_major()
        if _chrome_driver is not None:
            # 【2026-11-14 根修】这里以前有一条短路：本地驱动大版本 != 浏览器大版本就
            # 立刻弃用它，交给 Selenium Manager 联网下载。实测把它推翻了——
            #   chromedriver 156 附加 Chrome 157.0.8103.78 → 成功
            #   chromedriver 135 附加同一台 Chrome      → 失败，报的是最容易被误读的
            #       「session not created: cannot connect to chrome at 127.0.0.1:9255」
            # 而 Selenium Manager 找驱动先看 PATH，本机 PATH 上躺着的正是那份 135（它自己
            # 都打印了「这份可能不兼容，建议删掉」，却照用不误）。于是「明明有能用的驱动，
            # 偏偏换成不能用的」，1688 从此恒 0 家；上层又把那句归成「稍后重试通常就好」，
            # 就永远查不到根因。现在与企知道/风鸟/爱企查走同一个 create()：候选按与浏览器
            # 版本的接近程度排序逐个实跑，全败才联网，联网前先绕过 PATH 上不配套的驱动目录。
            return _chrome_driver.create(opts, self._log, ports=[self.debug_port])
        browser = self._devtools_browser()
        cands = self._driver_order(want)
        if not cands:
            self._log("未检测到本地 chromedriver，改由 Selenium Manager 在线准备驱动"
                      "（首次可能较慢，请耐心等待）…")
            return webdriver.Chrome(options=opts)
        last_exc = None
        for attempt in range(3):
            for drv in cands:
                try:
                    return webdriver.Chrome(service=Service(executable_path=drv),
                                            options=opts)
                except Exception as e:
                    last_exc = e
                    msg = first_line(e, 140) if str(e) else e.__class__.__name__
                    low = msg.lower()
                    if any(k in low for k in self.DRIVER_VERSION_MISMATCH_MARKS):
                        self._log(f"⚠ 内置驱动（{Path(drv).name}）与你的 Chrome（"
                                  f"{browser or '版本未知'}）明确不匹配，改由 Selenium Manager "
                                  f"在线获取配套驱动（可能较慢，请等待）…")
                        return webdriver.Chrome(options=opts)
            # 一整批本地驱动都失败：多半是 Chrome 刚起来还没就绪（2026-11-04 实测同一份
            # 驱动 0.2 秒后附加就成功了），等一等把整批再走一遍，而不是在同一个驱动上死磕。
            if attempt < 2:
                time.sleep(1.5)
                browser = self._devtools_browser() or browser
        # 反复连不上：多半 Chrome 半死或残留实例没起端口，联网回退同样连不上还易卡死。
        # 抛出交给 start_browser：清残留进程 → 重启 Chrome → 再附加。
        self._log(f"⚠ 本地 {len(cands)} 个驱动都没能附加 Chrome（端口 {self.debug_port}，"
                  f"{browser or '无响应'}）：{last_exc.__class__.__name__}，"
                  f"将清理残留 Chrome 后重启重试…")
        raise last_exc

    def _chrome_args(self, headed=True):
        return [
            self._find_chrome(),
            f"--remote-debugging-port={self.debug_port}",
            "--remote-allow-origins=*",
            f"--user-data-dir={self._profile_dir}",
            "--profile-directory=Default",
            "--no-first-run", "--no-default-browser-check",
            "--disable-blink-features=AutomationControlled",
            "--disable-session-crashed-bubble", "--hide-crash-restore-bubble",
            *CHROME_NO_UPGRADE_FLAGS,          # 明文 http 不许被浏览器升级后拦掉（见上面那段）
            "--window-size=1440,960",
        ] + ([] if headed else ["--headless=new"])

    def start_browser(self, headed=True):
        """确保有一个带调试端口、且至少 1 个标签页的独立 Chrome，并用 Selenium 附加。幂等。"""
        with self._browser_lock:
            if self.driver is not None and self._is_session_alive():
                return True

            if not self._find_chrome():
                self._log("❌ 未找到 Google Chrome，请先安装 Chrome")
                return False
            self._profile_dir.mkdir(parents=True, exist_ok=True)

            def launch():
                subprocess.Popen(self._chrome_args(headed),
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            if not self._debug_alive():
                # 端口没起：多半是旧实例锁着同一配置目录却没带调试端口，先清掉再启动
                self._kill_stale_profile_chrome()
                self._log("正在启动 Chrome（独立配置，登录一次即长期复用）...")
                try:
                    launch()
                except Exception as e:
                    self._log(f"❌ 启动 Chrome 失败：{e}")
                    return False
                if not self._wait_debug_ready(20):
                    self._log("⚠ 调试端口未就绪，清理残留 Chrome 后重试…")
                    self._kill_stale_profile_chrome()
                    launch()
                    if not self._wait_debug_ready(15):
                        self._log("❌ Chrome 调试端口未就绪")
                        return False
                time.sleep(1.2)

            # 附加前保证有标签页，否则 0 标签会让 webdriver 附加报 unable to discover open pages
            self._ensure_debug_tab()
            try:
                self.driver = self._attach()
            except Exception as e:
                # 端口在但接不上：通常是崩溃/半死的旧实例，清掉重启一个干净的再接
                self._log(f"⚠ 连接现有 Chrome 失败（{first_line(e, 100) if str(e) else e}），重启后重试…")
                self.close_browser()
                self._kill_stale_profile_chrome()
                try:
                    launch()
                except Exception as e2:
                    self._log(f"❌ 重启 Chrome 失败：{e2}")
                    return False
                if not self._wait_debug_ready(20):
                    self._log("❌ Chrome 调试端口未就绪")
                    return False
                time.sleep(1.2)
                self._ensure_debug_tab()
                try:
                    self.driver = self._attach()
                except Exception as e3:
                    self._log(f"❌ 连接 Chrome 失败：{first_line(e3, 200) if str(e3) else e3}")
                    self._log("请手动关闭所有 Chrome 窗口后，再点一次「打开浏览器」；"
                              "若仍失败，重启一次电脑可释放被占用的独立配置目录。")
                    self.driver = None
                    return False
            self._log("✅ 已连接 Chrome")
            return True

    def close_browser(self):
        if self.driver:
            # 会话 + 子进程一起收：Selenium 4.51 的 quit() 不收 chromedriver，
            # 而这里原先 try/except 一吞，进程就一直留着（口径见 chrome_driver.destroy）
            if _chrome_driver is not None:
                _chrome_driver.destroy(self.driver, self._log)
            else:
                # 独立的 Ali1688Tool 包里没打进 chrome_driver（打包期解析不到运行期
                # 才 append 的 sys.path），这一路只能就地补收：quit() 保证会话结束，
                # reap_driver_service() 保证那份 chromedriver 子进程也跟着没。
                try:
                    self.driver.quit()
                except Exception as e:
                    # 不能吞：quit 没成功等于会话还挂着，下一轮 _attach 就可能
                    # 「session not created」；只说一句失败在哪，比事后猜强（第 32g 条）
                    self._log(f"⚠ 收工结束浏览器会话没成功（Ali1688Crawler）："
                              f"{short_reason(first_line(e, 80), 60)}"
                              f"——若下次打不开浏览器，请手动关掉所有 Chrome 窗口再试")
                reap_driver_service(self.driver)
            self.driver = None

    def _is_session_alive(self):
        try:
            _ = self.driver.current_url
            return True
        except Exception:
            return False

    def try_restore_session(self):
        self.close_browser()
        return self.start_browser()

    def _restart_browser(self):
        return self.try_restore_session()

    # ── 标签管理（全程单标签，绝不新开标签，避免抢焦点/只能挂着）──────
    def _ensure_tab(self):
        """固定复用同一个标签；优先接管已有标签，不主动新建。"""
        handles = self.driver.window_handles
        if not handles:
            self.driver.switch_to.new_window("tab")
            self._tab = self.driver.current_window_handle
            return
        if getattr(self, "_tab", None) in handles:
            if self.driver.current_window_handle != self._tab:
                self.driver.switch_to.window(self._tab)
            return
        # 首次：优先挑一个已在 1688 的标签，否则用第一个，全程不再新建
        chosen = handles[0]
        for h in handles:
            try:
                self.driver.switch_to.window(h)
                if "1688.com" in (self.driver.current_url or ""):
                    chosen = h
                    break
            except Exception:
                continue
        self.driver.switch_to.window(chosen)
        self._tab = chosen

    def _prune_to_own_tab(self):
        """关掉 1688 页面自动弹出的多余标签，只留自己的工作标签并切回。

        关不掉必须开口（第 32g 条）：这里原先两层 `except …: pass`，剪不干净的标签
        就一直堆在共享 Chrome 里——用户看到的是「越跑越多窗口」，而日志一句没说。"""
        stuck = 0
        last = ""
        try:
            handles = self.driver.window_handles
            if len(handles) <= 1:
                if handles:
                    self._tab = handles[0]
                return
            for h in handles:
                if h != self._tab:
                    try:
                        self.driver.switch_to.window(h)
                        self.driver.close()
                    except Exception as e:
                        stuck += 1
                        last = first_line(e, 80)
            if self._tab in self.driver.window_handles:
                self.driver.switch_to.window(self._tab)
            else:
                self._tab = self.driver.window_handles[0]
                self.driver.switch_to.window(self._tab)
            if stuck:
                self._log(f"⚠ 有 {stuck} 枚 1688 弹出来的多余标签没关掉（最后原因："
                          f"{short_reason(last, 60)}）——不影响已采数据，可在浏览器里手动收")
        except Exception as e:
            # 外层这一格只说「整步没跑成」，不反复报每一枚：剪标签不是采数据的必经动作，
            # 说一次、带上真正的原因就够（与内层「枚数 + 最后原因」是两本账）
            self._log(f"⚠ 清理多余标签这一步没跑成：{short_reason(first_line(e, 80), 60)}"
                      f"——1688 弹出的新标签可能留着，不影响已采数据")

    def _goto(self, url, tries=4):
        # 惰性设置页面加载超时：避免个别资源卡住导致 driver.get 长时间挂起（夜间无人值守关键）
        if not getattr(self, "_timeout_set", False):
            try:
                self.driver.set_page_load_timeout(30)
            except Exception:
                pass
            self._timeout_set = True
        backoff = (2, 5, 10, 15)
        waited_for_captcha = False  # 标记是否已经等人机验证通过（防重复刷新验证码）
        self._last_goto_error = ""   # 只对本次调用有效（见 __init__ 处注释与 _goto_fail_reason）
        for i in range(tries):
            try:
                # _ensure_tab 也放进 try：浏览器被关/崩溃时它访问 window_handles 会抛
                # invalid session id，若留在 try 外会冒泡出 crawl 直接把采集线程打崩。
                self._ensure_tab()
                # 【大厂思想】如果刚等完人工过码，先检查当前页是否已在目标URL；
                # 若不在才导航一次（绝不循环重试，否则每次导航都触发新验证码）。
                if waited_for_captcha:
                    cur_url = (self.driver.current_url or "").lower()
                    target_lower = url.lower().split("?")[0]  # 忽略查询参数做前缀比对
                    if cur_url.startswith(target_lower) or target_lower in cur_url:
                        # 已在目标页（验证码自动跳转了），无需再导航
                        self._log(f"   ✅ 过码后已在目标页，无需重复导航")
                        time.sleep(0.8)
                        return True
                    else:
                        # 还在验证码页或别的页，导航到目标URL一次
                        self._log(f"   🔄 过码后未自动跳转，导航到目标页一次…")
                        self.driver.get(url)
                        time.sleep(1.5)
                        self._prune_to_own_tab()
                        block = self._detect_block()
                        if block:
                            # 又触发了新验证码 → 说明用户还没过完，继续等
                            if not self._wait_unblock(block):
                                return False
                            # 再次过码后直接返回（不再循环）
                            self._log(f"   ✅ 二次过码完成，继续采集")
                            return True
                        else:
                            return True
                else:
                    self.driver.get(url)
            except Exception as e:
                # 只记录首行，避免 chromedriver 巨长堆栈刷屏；含超时/断网等
                raw = first_line(e, 200) if str(e) else e.__class__.__name__
                # 分型后的一句才交给日志与归因：类别在最前面，后面每一档 [:60] 截断都截不掉它
                # （旧写法从头部截，砍掉的恰好是 chromedriver 那句 `Message: unknown error:
                # net::ERR_…` 里唯一决定方向的字样）。`_session_dead` 仍**先按原文判**：它的标记
                # 表是照着 Selenium 原文调出来的（见 SESSION_DEAD_MARKS 上方那段「不能用裸
                # disconnected」的教训），只喂整形后的字符串会让这条自愈路径悄悄失效。
                msg = request_error_shape(e, cap=160)
                self._last_goto_error = msg   # 只对本次 _goto 有效：翻页层据此说清败在哪一类
                if self._session_dead(raw) or self._session_dead(msg):
                    # 会话/浏览器已死：原地重试同一个 driver 永远救不回来，必须重启并
                    # 重新附加 Chrome（独立 profile 登录态保留），从卡住处续采而非崩线程。
                    self._log(f"⚠ 浏览器会话已断开（{short_reason(msg, 60)}），正在重启 Chrome 续采…（第{i + 1}/{tries}次）")
                    if not self.try_restore_session():
                        self._log("❌ 无法恢复浏览器会话，本轮到此结束（已采数据已保存，可稍后继续）")
                        self._stop_requested = True
                        return False
                    self._timeout_set = False   # 新 driver 需重设页面加载超时
                    time.sleep(1.5)
                    continue
                self._log(f"导航异常（第{i + 1}/{tries}次）：{msg} → 重试")
                time.sleep(backoff[min(i, len(backoff) - 1)])
                continue
            time.sleep(1.2)
            self._prune_to_own_tab()
            block = self._detect_block()
            if block:
                waited_for_captcha = True  # 标记：已进入人工过码流程
                if not self._wait_unblock(block):
                    self._last_goto_error = "【被拦停】防采页没等到放行（等人过验证或等 IP 冷却）"
                    return False
                # 【关键修复】过码成功后不再 continue 重试导航（那会触发新验证码），
                # 而是进入下一轮循环，由上方 waited_for_captcha 分支处理
                continue
            return True
        return False

    def _goto_fail_reason(self, label="导航失败"):
        """翻页层「这一页根本没取到」的原因句：`_goto` 当场抓到了就写清楚，抓不到才指日志。

        为什么不让 `_goto` 自己记账：同一句「导航失败」既可能来自列表页也可能来自详情页，
        而 `_last_page_error` 讲的是**整轮最终**败在什么上。重试循环里的中间态（第 1 次超时、
        第 2 次就成功）很常见，把它记进整轮那一格，下一页明明正常取回时收尾还挂着一条旧故障，
        `_no_result_line` 就会把「真的没公司」说成「链路没通」——那种假红比不说更难查。
        所以 `_goto` 只留当场的 `_last_goto_error`，由**真的据此下结论的那一层**取用。
        """
        why = str(self._last_goto_error or "").strip()
        return f"{label}（{why}）" if why else f"{label}（具体原因见紧邻上一行）"

    # 浏览器/会话已死的特征（Selenium WebDriverError 文案）：命中即重启续采，绝不崩线程。
    # 注意不能用裸 "disconnected"——断网 ERR_INTERNET_DISCONNECTED 也含该子串，
    # 而断网属正常可原地重试的瞬时错误，绝不该去重启 Chrome（会误杀正在加载的页面）。
    SESSION_DEAD_MARKS = (
        "invalid session id", "session deleted", "browser has closed",
        "chrome not running", "not connected to devtools",
        "no such window", "target window already closed",
        "unable to connect to renderer",
    )

    @classmethod
    def _session_dead(cls, msg):
        m = (msg or "").lower()
        return any(k in m for k in cls.SESSION_DEAD_MARKS)

    # ── 拦截 / 登录 判定 ───────────────────────────────────────
    # JS 探测验证码模态弹窗：1688 的滑块验证是盖在正常页面上的 overlay，
    # URL/标题/正文都与正常搜索页一致，旧三层检测全部漏判。
    # 本脚本扫描 iframe 源、模态容器、关键文案三维度，命中任一即判拦截。
    _CAPTCHA_OVERLAY_JS = """(function(){
        // 1. 扫描 iframe：验证码通常嵌在跨域 iframe 里
        var iframes = document.querySelectorAll('iframe');
        for (var i = 0; i < iframes.length; i++) {
            var src = (iframes[i].src || '').toLowerCase();
            if (src.indexOf('captcha') >= 0 || src.indexOf('verify') >= 0
                || src.indexOf('sufei') >= 0 || src.indexOf('nocaptcha') >= 0
                || src.indexOf('alibaba') >= 0 || src.indexOf('1688') >= 0
                || src.indexOf('wappass') >= 0 || src.indexOf('challenge') >= 0) {
                return 'iframe:' + src.slice(0, 60);
            }
        }
        // 2. 扫描已知验证码容器选择器
        var selectors = ['#nc_mask', '#nc_1_n1z', '.nc-container', '#baxia-dialog',
                         '.baxia-dialog', '#captcha-modal', '.captcha-modal',
                         '[class*="captcha"]', '[class*="verify"]', '[class*="sufei"]',
                         '[class*="nc_"]', '[id*="captcha"]', '[id*="verify"]'];
        for (var j = 0; j < selectors.length; j++) {
            try {
                var els = document.querySelectorAll(selectors[j]);
                for (var k = 0; k < els.length; k++) {
                    var r = els[k].getBoundingClientRect();
                    // 可见且尺寸合理（不是 display:none 的隐藏元素）
                    if (r.width >= 100 && r.height >= 50
                        && r.bottom > 0 && r.top < (window.innerHeight || 900)) {
                        return 'dom:' + selectors[j];
                    }
                }
            } catch(e) {}
        }
        // 3. 扫描页面文案：模态弹窗里的关键文字
        var bodyText = document.body ? document.body.innerText : '';
        var markers = ['正在加载验证码', '拖动滑块', '请按住滑块', '滑动验证',
                       '人机验证', '安全验证', '请完成验证', '反馈码'];
        for (var m = 0; m < markers.length; m++) {
            if (bodyText.indexOf(markers[m]) >= 0) {
                return 'text:' + markers[m];
            }
        }
        return '';
    })()"""

    def _detect_block(self):
        if self.driver is None:
            return ""
        try:
            url = (self.driver.current_url or "").lower()
            title = self.driver.title or ""
        except Exception:
            return ""
        if any(m in url for m in LOGIN_URL_MARKERS):
            return LOGIN_WAIT_SENTINEL
        if any(m in url for m in BLOCK_URL_MARKERS):
            return "页面被跳转到 1688 安全验证/风控页"
        if any(m in title for m in BLOCK_TITLE_MARKERS) and not any(h in title for h in SAFE_TITLE_HINTS):
            return f"出现验证页：{title[:24]}"
        # 正文极短且含验证字样才像插页
        try:
            body = self.driver.find_element("tag name", "body").text or ""
        except Exception:
            body = ""
        if len(body) < 300 and any(m in body for m in ("滑动验证", "拖动滑块", "验证码", "访问过于频繁")):
            return "页面被 1688 风控拦截（滑块/访问频繁）"
        # 【关键修复 2026-11-07】第四层：JS 探测验证码模态弹窗（overlay）
        # 1688 的滑块验证盖在正常搜索页上，URL/标题/正文均正常，前三层全漏判。
        try:
            overlay = self.driver.execute_script(self._CAPTCHA_OVERLAY_JS)
            if overlay:
                return f"页面出现验证码弹窗（{overlay}），请在 Chrome 里手动过验证"
        except Exception:
            pass
        return ""

    def _login_state_by_cookie(self):
        """这个 Chrome 会话里的 1688 到底登录了没（判据见 judge_login_cookie）。
        问不到就返回 None（宁可不说，也不指错方向）。"""
        try:
            return judge_login_cookie(self.driver.get_cookies())
        except Exception:
            return None

    def _unblock_login_hint(self) -> str:
        """撞验证页时的归因补充：只有**确凿没登录**才多说这一句，其余一律闭嘴。
        单独拆成一个小函数，是为了让离线用例能把「已登录/未登录/判不出」三档都钉住
        —— 真机上一次只凑得齐一种状态。"""
        if self._login_state_by_cookie() is False:
            return ("   💡 这个 Chrome 里的 1688 目前「未登录」：联系方式页对未登录会话"
                    "反复出验证、号码也是空的。建议先在这个 Chrome 里登录一次 1688，"
                    "再顺手把验证过掉（登录后不用重启工具，本轮自动继续）。")
        return ""

    def _preflight_login_notice(self) -> bool:
        """开局就把「这个 Chrome 里 1688 没登录」说在前面，返回是否确凿未登录。

        为什么不等第一家撞验证页再说：联系方式页是本轮最先吃风控的那一步，实机上
        0.6 秒附加成功、第一次取联系方式就被拦，用户在提示出现前已经白等了一整轮
        （2026-11-15 取证）。为什么**只提示不拦路**：主搜/SEM/供应商三条列表渠道不
        登录也拿得到公司名，用户一边登录一边采不冲突，真要取不到号码那里自会暂停等人
        （见 _wait_unblock）。判不出（None）时一个字都不多说——没证据就不猜方向。
        必须在全站首页之后调用：cookie 是按当前页面读的。"""
        if self._login_state_by_cookie() is not False:
            return False
        self._log("🔑 这个 Chrome 里的 1688 目前「未登录」：公司名与商品照常采，"
                  "但联系方式页很容易吃验证。建议现在在这个 Chrome 窗口里登录一次 1688"
                  "（登录后不用重启工具、不用重新开始采集，本轮自动继续）。")
        self._emit("login_required_signal",
                   "1688 未登录：请在这个 Chrome 窗口里登录一次（不登录也能先采公司名）")
        return True

    def _register_block(self):
        """记录一次拦截并返回统计窗口内的累计次数（熔断用）。"""
        now = time.time()
        self._block_times = [t for t in self._block_times if now - t < self._block_window]
        self._block_times.append(now)
        return len(self._block_times)

    def _interruptible_sleep(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            if self._stop_requested:
                return
            time.sleep(0.5)

    def _wait_unblock(self, reason, max_wait=3600):
        """被拦截时原地等人工过验证；过验后自动继续，并把采集间隔自动放大。
        非登录类拦截额外走熔断：短窗口内反复被拦＝已进限流区，
        每次都强制递增冷却（拿不到冷却窗口会把软限流熬成硬封号），
        达到阈值则直接优雅结束本轮，绝不死磕。"""
        login = (reason == LOGIN_WAIT_SENTINEL)
        # 「在等人」必须是一格机器可读的账，不是日志里的一句话（2026-11-15 实机：1688 把
        # 整轮体检预算烧在等登录上，最后 0 家被报成「采不到」——它既没熔断也没失败，只是
        # 在等人）。只在等待期间为真，解除或放弃时当场清掉，绝不留给下一句看起来正常的日志。
        self._waiting_human = human_wait_label(reason, login)
        if login:
            self._emit("login_required_signal", "1688 需要登录，请在弹出的 Chrome 里完成登录")
            self._log("🔑 检测到未登录，请在 Chrome 窗口里登录 1688（登录后自动继续）")
        else:
            strikes = self._register_block()
            self._emit("blocked_signal", reason)
            if strikes >= self._max_blocks:
                self._log(f"🛑 {reason}")
                self._log(f"   ⛔ {self._block_window // 60} 分钟内第 {strikes} 次被拦，"
                          f"判定为持续限流。为保护账号，本轮到此为止（已采数据已保存），"
                          f"请稍后（建议 ≥30 分钟）或调大采集间隔后再跑。")
                self._stop_requested = True
                self._waiting_human = ""      # 已经放弃等待并收轮，不再算「在等人」
                self._run_abandon = (f"持续限流（{self._block_window // 60} 分钟内第 {strikes} 次被拦）："
                                     f"{short_reason(reason, 60)}")
                return False
            self._log(f"🛑 {reason}")
            self._log("   已暂停推进（不写入空记录）。请在 Chrome 窗口里手动过验证，通过后自动继续。")
            # 自动降速：每次被拦把间隔上调，最低 8s、最高 30s
            self._current_delay = min(30.0, max(8.0, self._current_delay * 1.6))
            self._log(f"   自动降速：采集间隔上调为约 {self._current_delay:.1f}s")
            # 归因补一句：1688 的联系方式页在**未登录**会话上最容易吃风控，而这种情况
            # 不会跳登录页（所以 _detect_block 只能报「验证页」），用户照着提示反复过码、
            # 过完下一家又被拦 —— 2026-11-15 实机就是这个形状（话术见 _unblock_login_hint）。
            hint = self._unblock_login_hint()
            if hint:
                self._log(hint)

        waited = 0
        while waited < max_wait:
            if self._stop_requested:
                return False
            time.sleep(3)
            waited += 3
            state = self._detect_block()
            if state == "" or (login and state != LOGIN_WAIT_SENTINEL) or (
                    not login and state != LOGIN_WAIT_SENTINEL and not state):
                self._waiting_human = ""      # 人已经处理完了，这一格必须当场收掉
                if login:
                    self._log("✅ 检测到已登录，继续采集")
                else:
                    self._log(f"✅ 验证已解除（等待约 {waited} 秒），继续采集")
                    self._emit("block_cleared_signal", reason)
                    # 冷却窗口：本轮第 2 次起，过验后先强制休息再恢复请求，
                    # 让客户端 IP 真正拿到冷却（立刻继续 = 最容易再次触发）
                    strikes = len([t for t in self._block_times
                                   if time.time() - t < self._block_window])
                    if strikes >= 2:
                        cool = min(1800, 120 * (2 ** (strikes - 1)))
                        self._log(f"   ❄ 已第 {strikes} 次被拦，强制冷却 {cool} 秒再继续"
                                  f"（给 IP 冷却窗口，避免软限流升级成硬封号）")
                        self._interruptible_sleep(cool)
                return True
            if waited % 60 == 0:
                self._log(f"   仍在等待{'登录' if login else '验证'}…已等 {waited} 秒")
        self._log("⚠ 等待超时，本轮结束（已采集数据不受影响）")
        # 超时＝放弃了，「等人」这一格就此收账；真正留下的归因是下面那句 _run_abandon
        self._waiting_human = ""
        self._run_abandon = (f"等待{human_wait_label(reason, login)}超时"
                             f"（{max_wait} 秒没人处理）")
        return False

    def _sleep(self):
        d = self._current_delay + random.uniform(0, self.jitter)
        # 可被停止打断
        end = time.time() + d
        while time.time() < end:
            if self._stop_requested:
                return
            time.sleep(0.3)

    # ── 关键词解析 ─────────────────────────────────────────────
    @staticmethod
    def parse_keywords(text):
        """从上传文档/粘贴文字中识别多个关键词。按换行/常见分隔符切分并去重。"""
        if not text:
            return []
        parts = re.split(r"[\r\n,;，、；|/\t]+", text)
        seen, out = set(), []
        for p in parts:
            k = re.sub(r"\s+", " ", p).strip()
            # 去掉纯序号/Excel 列残留（如 "1. " "、产品："）
            k = re.sub(r"^\d+[.、]\s*", "", k)
            k = re.sub(r"^[^:：]{1,8}[:：]\s*", "", k) if (":" in k or "：" in k) and len(k) < 40 else k
            if k and k not in seen and len(k) <= 40:
                seen.add(k)
                out.append(k)
        return out

    # ── 列表页解析 ─────────────────────────────────────────────
    def _parse_list_page(self, html):
        """从 pageConfig 提取 listOffer 数组。返回 offer dict 列表。"""
        s = html.find("var pageConfig")
        if s < 0:
            return []
        s = html.find("{", s)
        depth = 0
        end = -1
        for i in range(s, min(len(html), s + 4_000_000)):
            c = html[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end < 0:
            return []
        try:
            pc = json.loads(html[s:end + 1])
        except Exception:
            return []
        offers = pc.get("listOffer") or []
        # 相关词（注意：1688 该字段多为全站热搜词，并非语义相关，使用时须保守过滤）
        self._last_relative = pc.get("relativeWords") or []
        return offers

    def _parse_company_page(self, html):
        """供应商(公司)搜索列表页：提取每家公司卡片的公司名与店铺子域URL。"""
        out = []
        for m in re.finditer(
                r'<a class="company-name" href="(https?://[^"/]+\.1688\.com)[^"]*"[^>]*>([^<]+)</a>',
                html):
            winport, name = m.group(1).rstrip("/"), m.group(2).strip()
            sub = winport.split("//", 1)[-1].split(".", 1)[0].lower()
            if sub in ("www", "s", "detail", "m", "login", "air", "page", "show"):
                continue
            seg = html[m.end():m.end() + 1500]
            pos = re.search(r'company-position">.*?<span[^>]*>([^<]{2,24})</span>', seg, re.S)
            out.append({"company": name, "winport": winport, "shop": sub,
                        "location": pos.group(1).strip() if pos else ""})
        return out

    def _province_suffix(self):
        """省份精准采集：站方原生 `&province=<UTF-8 百分号编码的简体短名>`（三个搜索
        渠道同源，2026-11-04 探针 A/B 实锤服务端真过滤）。未选省返回空串。
        多省场景：URL 参数只能指向一个省，多省时放弃参数直筛，退回数据层过滤。"""
        pv = str(getattr(self, "province", "") or "").strip()
        if not pv or "," in pv:
            return ""
        return f"&province={quote(pv)}"

    def _iter_keyword_offers(self, kw):
        """遍历一个关键词的多页列表（站外 SEM 渠道），逐条 yield offer（已按 memberId 去重）。

        旧版这一路是**没有任何相关性护栏**的：列表进多少家就写多少家进结果表（基类
        `guard` 标记只有主搜会带）。实测代价就是那 91 条污染数据。现在与主搜一个口径：
        卡片自带公司名/商品标题先过一道早过滤（不进详情，省请求），进联系方式页前
        再带 `guard=True` 走详情类目复核；判不出信号时依旧 fail-open。"""
        stale_streak = 0
        self._guard_kw = kw
        for page in range(1, self.max_pages + 1):
            if self._stop_requested:
                return
            while self._pause_requested and not self._stop_requested:
                time.sleep(0.5)
            url = SEARCH_URL.format(kw=quote(kw), page=page) + self._province_suffix()
            if not self._goto(url):
                self._note_page_fail(self._goto_fail_reason("列表页导航失败"))
                return
            html = self.driver.page_source
            offers = self._parse_list_page(html)
            # 条目账走唯一把手（总账 + 分词账）：收尾那句「本轮零产出」要分得清「一页都没
            # 取到」与「取到了但一家没出」，凭的就是这一笔
            self._note_listed(kw, len(offers), self.parse_listed_total(html))
            if not offers:
                self._log(self._no_result_line(kw, f"第 {page} 页", html) + "，停止翻页")
                return
            new = skipped = seen = 0
            for o in offers:
                mid = str(o.get("memberId") or o.get("loginId") or o.get("company") or "")
                cname = self._norm_name(o.get("company") or "")
                if not mid:
                    continue     # 卡片没有任何主体标识：无法去重也无法进详情，不算「增量库跳过」
                if mid in self._seen_members or (cname and cname in self._seen_members) \
                        or f"{kw}#{mid}" in self._seen_members:
                    seen += 1
                    self._incremental_skipped += 1
                    continue     # 第四个条件：同一个关键词下已判过「不相关」的，翻页时不重复统计
                subject = o.get("subject") or o.get("simpleSubject") or ""
                if self.relevance_guard and not self._list_relevant(o.get("company") or "", subject):
                    # 卡片文本与关键词/预测类目零重叠：连详情页都不必进（不占全局已见，
                    # 同一个公司对另一个关键词可能就是目标）
                    skipped += 1
                    self._note_early_skip(kw, o.get("company") or cname)
                    self._seen_members.add(f"{kw}#{mid}")
                    continue
                self._seen_members.add(mid)
                if cname:
                    self._seen_members.add(cname)
                new += 1
                yield {"keyword": kw, "page": page, "raw": o,
                       "company": o.get("company") or "",
                       "offerId": o.get("offerId") or "",
                       "odUrl": o.get("odUrl") or "",
                       "memberId": mid,
                       "loginId": o.get("loginId") or "",
                       "price": o.get("price"),
                       "subject": subject,
                       "guard": True}   # 站外页结果参差，与主搜同样进详情前做类目复核
            tail = self._page_tail(skipped, seen)
            self._log(f"  「{kw}」第 {page} 页：{len(offers)} 条，新增公司 {new} 家{tail}")
            # 连续多页 0 新增即枯竭（主搜渠道跑过后尤其如此），别把页数预算耗在轮回的浅池上
            if new == 0:
                stale_streak += 1
                if stale_streak >= 3:
                    self._log(f"  「{kw}」商品渠道连续 {stale_streak} 页无新增，停止翻页")
                    return
            else:
                stale_streak = 0
            self._sleep()
            if len(offers) < 8:   # 尾页
                return

    @staticmethod
    def _page_tail(skipped=0, seen=0):
        """把「本页 0 新增」的两种原因拼成一句尾巴：商品/主搜/供应商三条渠道共用同一拼法。

        早过滤=判它跟关键词不相关；增量库已含=这家已经采过或重试已用尽。以前只有前一种
        有尾巴，「增量库跳过」是完全静默的，于是界面上就只剩一句「59 条offer，新增公司 0 家」
        ——用户无从分辨「真的采全了」和「去重口径把活锁死了」，只能整站重采（更贵、更招风控）。
        """
        parts = []
        if skipped:
            parts.append(f"早过滤 {skipped} 家(未进详情)")
        if seen:
            parts.append(f"增量库已含 {seen} 家(跳过)")
        return ("，" + "，".join(parts)) if parts else ""

    @staticmethod
    def _norm_name(name):
        """公司名归一化：跨渠道去重用（去空白与常见括号差异）。"""
        return re.sub(r"[\s（）()・.、，,]+", "", name or "")

    def _iter_keyword_companies(self, kw):
        """遍历供应商(公司)搜索渠道的多页列表，逐家 yield 公司条目（跨渠道去重）。

        该渠道每页约 20 家公司卡片；实测大词单页新增公司数为商品渠道的 4 倍以上。
        早停：连续 2 页解析不到卡片视为无数据；连续 3 页 0 新增视为池子枯竭。"""
        empty_streak = stale_streak = 0
        for page in range(1, self.max_pages + 1):
            if self._stop_requested:
                return
            while self._pause_requested and not self._stop_requested:
                time.sleep(0.5)
            url = COMPANY_SEARCH_URL.format(kw=quote(kw), page=page) + self._province_suffix()
            if not self._goto(url):
                self._note_page_fail(self._goto_fail_reason("列表页导航失败"))
                return
            entries = self._parse_company_page(self.driver.page_source)
            self._note_listed(kw, len(entries), self.parse_listed_total(self.driver.page_source))
            if not entries:
                empty_streak += 1
                if empty_streak >= 2:
                    if page == 2:
                        # 走到这里说明 _goto 成功、页面真取回来了（导航失败在上面已经记账并
                        # 收轮），所以这一句可以如实说「无公司卡片」；两种来路不能共用一句
                        self._log(f"  「{kw}」供应商渠道无公司卡片"
                                  f"（1688 已改道／暂无该词公司），跳过该渠道")
                    else:
                        self._log(self._no_result_line(kw, f"供应商渠道第 {page - 1} 页起",
                                                       self.driver.page_source)
                                  + "，停止翻页")
                    return
                continue
            empty_streak = 0
            new = 0
            seen = 0
            for e in entries:
                key = "shop:" + e["shop"]
                cname = self._norm_name(e["company"])
                if key in self._seen_members or (cname and cname in self._seen_members):
                    seen += 1
                    self._incremental_skipped += 1
                    continue
                self._seen_members.add(key)
                if cname:
                    self._seen_members.add(cname)
                new += 1
                e["keyword"] = kw
                e["page"] = page
                yield e
            self._log(f"  「{kw}」供应商渠道第 {page} 页：{len(entries)} 家，"
                      f"新增公司 {new} 家{self._page_tail(0, seen)}")
            if new == 0:
                stale_streak += 1
                if stale_streak >= 3:
                    self._log(f"  「{kw}」供应商渠道连续 {stale_streak} 页无新增，公司池已枯竭")
                    return
            else:
                stale_streak = 0
            self._sleep()

    def _parse_main_search_page(self, html):
        """主搜商品搜索页：从 offer 卡片埋点属性提取 (memberId, offerId) 对，
        并从卡片 DOM 附带「公司名 + 商品标题」（列表页零成本），供进详情前早过滤用。

        每页约 60 条 offer、约 50 家不同公司；数据在 DOM 渲染属性里，
        不依赖 window.data（该对象在部分变体页里为空壳）。"""
        # offerId -> (公司名, 商品标题)：来自卡片 DOM，与埋点 (mid,oid) 按 offerId 关联
        meta = {}
        for oid, inner in OFFER_CARD_RE.findall(html):
            comp = ""
            for t in re.findall(r'title="([^"]{4,50})"', inner):
                t = t.strip()
                if t and CARD_COMPANY_SUFFIX_RE.search(t):
                    comp = t
                    break
            title = ""
            mt = CARD_TITLE_TEXT_RE.search(inner)
            if mt:
                title = re.sub(r"<[^>]+>", "", mt.group(1))
                title = re.sub(r"\s+", "", title)[:80]
            if comp or title:
                meta[oid] = (comp, title)
        out, seen = [], set()
        for mid, oid in MAIN_MEMBER_ITEM_RE.findall(html):
            key = (mid, oid)
            if key in seen:
                continue
            seen.add(key)
            comp, title = meta.get(oid, ("", ""))
            out.append({"memberId": mid, "offerId": oid, "company": comp, "title": title})
        return out

    def _parse_keywords_category(self, html):
        """主搜页内嵌 window.data.keywordsCategory：取该关键词的预测类目集合。

        allModels 里 grade 分 PRECISE（高置信）与 RELEVANT（仅相关）两档，
        过滤只认 PRECISE 档的叶子/二级类目 id 与其类目名，避免宽泛误放行。
        解析失败返回 None（后续判定 fail-open，绝不因取不到信号剔除商家）。"""
        i = html.find("keywordsCategory")
        if i < 0:
            return None
        s = html.find("{", i)
        if s < 0:
            return None
        depth = 0
        end = -1
        for j in range(s, min(len(html), s + 300_000)):
            c = html[j]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    end = j
                    break
        if end < 0:
            return None
        try:
            obj = json.loads(html[s:end + 1])
            qp = obj["data"]["qpPredictCateAllPlan"]
        except Exception:
            return None
        leaf_ids, names = set(), set()
        for m in qp.get("allModels") or []:
            if str(m.get("grade", "")).upper() != "PRECISE":
                continue
            for k in ("leafNode", "secondNode"):
                v = str(m.get(k) or "")
                if v.isdigit():
                    leaf_ids.add(v)
            n = str(m.get("cateName") or "").strip()
            if n:
                names.add(n)
        if not leaf_ids:  # 兜底：allModels 缺失时用查询级节点
            v = str(qp.get("postcateLeafNode") or "")
            if v.isdigit():
                leaf_ids.add(v)
            for k in ("postcateLeafNodes", "postcateSecondNodes"):
                for v in qp.get(k) or []:
                    v = str(v)
                    if v.isdigit():
                        leaf_ids.add(v)
        return {"ids": leaf_ids, "names": names}

    @staticmethod
    def _extract_offer_meta(html):
        """详情页零额外代价提取真实类目信号：leafCategoryId/Name、店铺主营类目、标题。"""
        meta = {"leafId": "", "leafName": "", "shopCats": [], "title": ""}
        m = DETAIL_LEAF_ID_RE.search(html)
        if m:
            meta["leafId"] = m.group(1)
        m = DETAIL_LEAF_NAME_RE.search(html)
        if m:
            meta["leafName"] = m.group(1).strip()
        meta["shopCats"] = [x.strip() for x in DETAIL_SHOP_CAT_RE.findall(html) if x.strip()]
        m = DETAIL_TITLE_RE.search(html)
        if m:
            meta["title"] = m.group(1).strip()
        return meta

    def _kw_relevant(self, meta, rec):
        """主搜商家类目相关性判定（双信号，宽松方向保数据量）：
        ① id 信号：详情页 leafCategoryId ∈ 关键词 PRECISE 预测集；
        ② 字面信号：关键词去「批发/厂家」等后缀后的二字组与 叶子类目名/店铺主营类目/
           商品标题/公司名 有重叠（覆盖「玻璃」⊂「光伏玻璃」这类父类目放行）。
        两信号都拿不到时 fail-open 放行——宁多采一家，不误杀。"""
        kw = self._guard_kw
        pred = self._guard_pred
        if pred and meta["leafId"] and meta["leafId"] in pred["ids"]:
            return True
        def grams(s):
            return {s[i:i + 2] for i in range(len(s) - 1)}
        base = re.sub(r"(批发|供应|厂家|定制|加工|工厂)", "", kw or "")
        kg = grams(base)
        # 字面信号仅在中文词时可靠（英文词与中文类目名之间二字组比对无意义）
        literal_ok = bool(kg) and bool(re.search(r"[\u4e00-\u9fa5]", base))
        if literal_ok:
            texts = ([meta["leafName"]] + meta["shopCats"][:3] +
                     [rec.get("公司名称") or ""])
            for t in texts:
                if not t:
                    continue
                segs = [x for x in re.split(r"[、，,;；/\s]+", t) if x] or [t]
                for seg in segs:
                    if kg & grams(seg) or base in seg:
                        return True
            # 标题营销词噪声大（「光伏鼠标垫」与「光伏玻璃」共享二字组即误放行），
            # 故只认「关键词完整词组原样出现在标题里」这一最强字面信号
            if base and base in meta["title"]:
                return True
        if pred and pred["names"] and meta["leafName"]:
            for n in pred["names"]:
                if grams(n) & grams(meta["leafName"]):
                    return True
        # fail-open：没有任何「可对比且可靠」的信号时不剔除。字面判定要有中文
        # 二字组才可靠（单字/纯英文词极易误杀）；字面不可用时只认 id 对比。
        comparable = bool((pred and pred["ids"] and meta["leafId"]) or
                          (literal_ok and (meta["leafName"] or meta["shopCats"] or
                                           meta["title"] or rec.get("公司名称"))))
        return not comparable

    def _list_relevant(self, company, title):
        """列表页「进详情前」的早过滤：用卡片自带的公司名+商品标题，与
        关键词字面 + 平台预测的 PRECISE 类目名做二字组比对，明显无关则连详情页都不必进
        （省一次详情导航=提速+防封）。这是大规模爬虫的通用手法：fetch 前先用廉价信号打分过滤。

        保守 fail-open——只有「有可靠中文信号且卡片文本中文、却零重叠」才判无关跳过；
        任何取不到信号/英文词/卡片文本缺失一律放行去详情，绝不误杀（详情页仍会用真实类目 id 兜底复核）。"""
        kw = self._guard_kw
        pred = self._guard_pred
        text = ((company or "") + " " + (title or "")).strip()
        if not text:
            return True   # 卡片没解析到公司名/标题 → 无法早判 → 进详情
        if not re.search(r"[\u4e00-\u9fa5]", text):
            return True   # 卡片文本非中文 → 字面判定不可靠 → 进详情

        def grams(s):
            return {s[i:i + 2] for i in range(len(s) - 1)}
        base = re.sub(r"(批发|供应|厂家|定制|加工|工厂)", "", kw or "")
        sig = grams(base)
        # 平台预测类目名并入可匹配信号（扩大安全网：如搜「制冷设备」命中「冷水机组」等预测名不误杀）
        for n in ((pred or {}).get("names") or set()):
            sig |= grams(n)
        if not sig:
            return True   # 无可靠中文信号（英文/单字词）→ 不早过滤，交详情页判定
        compact = re.sub(r"\s+", "", text)
        if sig & grams(compact) or (base and base in compact):
            return True
        return False

    def _iter_keyword_main_offers(self, kw):
        """遍历主搜渠道多页列表，逐条 yield 可进详情链路的伪 offer（按 memberId 去重）。

        早停：连续 2 页解析不到卡片视为翻页到底；连续 3 页 0 新增视为池子枯竭。"""
        empty_streak = stale_streak = 0
        self._guard_kw = kw
        self._guard_pred = None
        for page in range(1, self.max_pages + 1):
            if self._stop_requested:
                return
            while self._pause_requested and not self._stop_requested:
                time.sleep(0.5)
            url = MAIN_SEARCH_URL.format(kw=quote(kw), page=page) + self._province_suffix()
            if not self._goto(url):
                self._note_page_fail(self._goto_fail_reason("列表页导航失败"))
                return
            src = self.driver.page_source
            pairs = self._parse_main_search_page(src)
            self._note_listed(kw, len(pairs), self.parse_listed_total(src))
            if page == 1 and self.relevance_guard:
                # 顺带取该关键词的预测类目集（同一页源码，零额外请求）
                self._guard_pred = self._parse_keywords_category(src)
            if not pairs:
                empty_streak += 1
                if empty_streak >= 2:
                    self._log(self._no_result_line(kw, f"主搜渠道第 {page - 1} 页起", src,
                                                   noun="结果") + "，停止翻页")
                    return
                continue
            empty_streak = 0
            new = 0
            page_skipped = 0
            page_seen = 0
            for p in pairs:
                mid = p["memberId"]
                # 命中全局已采公司 或 本关键词下已判「类目不符」的公司 → 跳过，
                # 连详情页都不必再进（省请求=防封）；换关键词时后者不生效，仍会重评估。
                if mid in self._seen_members or f"{kw}#{mid}" in self._seen_members:
                    page_seen += 1
                    self._incremental_skipped += 1
                    continue
                self._seen_members.add(mid)
                # 列表页早过滤：卡片自带的公司名+标题明显与关键词/预测类目无关 → 不进详情
                # （省一次详情导航=提速+防封）。保守 fail-open，只在强信号零重叠时才跳过。
                if self.relevance_guard and not self._list_relevant(p.get("company", ""), p.get("title", "")):
                    page_skipped += 1
                    self._note_early_skip(kw, p.get("company", ""))
                    continue
                new += 1
                yield {"keyword": kw, "page": page, "raw": {},
                       "company": p.get("company", ""), "offerId": p["offerId"],
                       "odUrl": f"https://detail.1688.com/offer/{p['offerId']}.html",
                       "memberId": mid, "loginId": "", "price": "",
                       "subject": p.get("title", ""),
                       "guard": True}   # 主搜结果参差，进联系方式前做类目相关性过滤
            tail = self._page_tail(page_skipped, page_seen)
            self._log(f"  「{kw}」主搜渠道第 {page} 页：{len(pairs)} 条offer，新增公司 {new} 家{tail}")
            if new == 0:
                stale_streak += 1
                if stale_streak >= 3:
                    self._log(f"  「{kw}」主搜渠道连续 {stale_streak} 页无新增，公司池已枯竭")
                    return
            else:
                stale_streak = 0
            self._sleep()

    def _related_candidates(self, kw):
        """从商品列表页 relativeWords 里保守筛选相关词。

        实测 1688 的 relativeWords 多为全站热搜词（搜「光伏玻璃」会给「t恤批发」），
        故只保留与主词去掉「批发/厂家」等后缀后、仍共享至少一个二字子串的候选词。
        多数情况下筛完所剩无几——宁缺毋滥，避免把无关行业词混进采集队列。"""
        def grams(s):
            return {s[i:i + 2] for i in range(len(s) - 1)}
        base = re.sub(r"(批发|供应|厂家|定制|加工|工厂)$", "", kw)
        kg = grams(base)
        if not kg:
            return []
        out = []
        for w in getattr(self, "_last_relative", []) or []:
            w = str(w).strip()
            if not w or w == kw or len(w) > 40 or w in out:
                continue
            core = re.sub(r"(批发|供应|厂家|定制|加工|工厂)$", "", w)
            if grams(core) & kg:
                out.append(w)
        return out[:5]

    # ── 详情 + 联系方式 ────────────────────────────────────────
    def _extract_winport(self, html):
        """详情页里取 winportUrl 与 contactinfoUrl。"""
        winport = contact = company = ""
        m = re.search(r'"winportUrl"\s*:\s*"(https?://[^"]+?\.1688\.com)"', html)
        if m:
            winport = m.group(1)
        m = re.search(r'"contactinfoUrl"\s*:\s*"(https?://[^"]+?contactinfo[^"]*)"', html)
        if m:
            contact = m.group(1).replace("\\/", "/")
        m = re.search(r'"companyName"\s*:\s*"([^"]+)"', html)
        if m:
            company = m.group(1)
        if not contact and winport:
            contact = winport.rstrip("/") + "/page/contactinfo.html"
        return winport, contact, company

    @staticmethod
    def _visible_text(html):
        """提取页面「可见文本」：剔除 <script>/<style> 块、所有 URL、再转纯文本。
        防止图片资源 ID / JS 里的数字串被误判成电话。"""
        t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.I | re.S)
        t = re.sub(r"https?://\S+", " ", t)
        t = re.sub(r"<[^>]+>", " ", t)
        t = t.replace("&nbsp;", " ")
        return re.sub(r"\s+", " ", t)

    def _parse_contact(self, html):
        """联系方式页：按标签提取电话/手机/传真/地址/联系人/邮箱。"""
        def after_label(lbl):
            i = html.find(lbl)
            if i < 0:
                return ""
            seg = html[i:i + 600]
            txt = re.sub(r"<[^>]+>", " ", seg)          # 去掉完整标签
            txt = txt.split(lbl, 1)[-1]                  # 去掉标签名本身
            txt = txt.split("<", 1)[0]                   # 去掉被截断的半个标签
            txt = re.sub(r"\s+", " ", txt)
            for stop in ("电话：", "手机：", "传真：", "地址：", "联系人", "邮编",
                         "主营", "经营模式", "公司：", "http"):
                if stop in txt:
                    txt = txt.split(stop)[0]
            txt = txt.strip(" ：:　")
            return "" if txt in ("暂无", "-", "无") else txt

        phone_raw = after_label("电话：")
        mobile_raw = after_label("手机：")
        fax_raw = after_label("传真：")
        addr_raw = after_label("地址：")

        # 联系方式页存在模板变体：卖家自定义块常写成「姓名：手机号」并与地址挤在一起，
        # 只用「电话：/手机：」标签会漏采。故除标签外，再对整页做号码兜底扫描。
        # 关键：兜底文本必须先剔除 <script>/<style> 块与所有 URL，否则 1688 图片资源 ID
        # （如 cbu01.alicdn.com/img/ibank/13907553770_...）会被当成手机号误采。
        plain = self._visible_text(html)
        mobiles = [x for x in PHONE_RE.findall(mobile_raw) if re.fullmatch(r"1[3-9]\d{9}", x)]
        mobiles += re.findall(r"(?<!\d)1[3-9]\d{9}(?!\d)", plain)
        mobiles = list(dict.fromkeys(mobiles))
        mset = set(mobiles)
        # 固话：标签区固话式号码（排除已被判为手机的）+ 整页带分隔符的固话，避免误抓长数字串
        tel_list = [p for p in PHONE_RE.findall(phone_raw) if p not in mset]
        tel_list += re.findall(r"(?<!\d)(?:\+?86[\s-]?)?(0\d{2,4}[-\s]\d{7,8})(?!\d)", plain)
        # 按纯数字（去国家码）去重，避免「86 0534 …」与「0534 …」重复
        _seen_tel, _tel = set(), []
        for t in tel_list:
            t = re.sub(r"\s+", " ", t).strip()
            digits = re.sub(r"\D", "", t)
            core = digits[2:] if digits.startswith("86") else digits
            if not core or core in _seen_tel:
                continue
            _seen_tel.add(core)
            _tel.append(t)
        tel_list = _tel
        # 丢弃「假固话」：1688 常把 区号+手机 连写（如 86 0512 18912740987），
        # 固话式正则只截到区号+手机前 8 位。去掉前导区号后若以 1[3-9] 开头即手机号，
        # 真实手机号已由整页扫描收入「手机」，这里把它从固话里剔除。
        def _glued_mobile(t):
            d = re.sub(r"\D", "", t)
            d = d[2:] if d.startswith("86") else d
            sub = re.sub(r"^0\d{2,3}", "", d)
            return bool(re.match(r"1[3-9]", sub))
        tel_list = [t for t in tel_list if not _glued_mobile(t)]

        # 联系人：优先「联系人：」标签，其次 1688 常见「X先生/X女士」呈现
        person = after_label("联系人：") or after_label("联系人:")
        if not person:
            mp = re.search(r'>([\u4e00-\u9fa5]{2,4}(?:先生|女士))<', html)
            if not mp:
                mp = re.search(r'([\u4e00-\u9fa5]{2,4}(?:先生|女士))',
                               re.sub(r"<[^>]+>", " ", html))
            if mp:
                person = mp.group(1).strip()

        # 邮箱：只在确有「邮箱：」标签的区域取，绝不整页扫描（整页会把 logo@2x.png
        # 这类图片文件名误判成邮箱）；再过滤掉任何图片/资源后缀。
        email_list = []
        i = html.find("邮箱")
        if i >= 0:
            region = re.sub(r"<[^>]+>", " ", html[i:i + 200])
            for cand in EMAIL_RE.findall(region):
                if not re.search(r"\.(png|jpe?g|gif|webp|svg|bmp|ico)$", cand, re.I):
                    email_list.append(cand)

        return {
            "联系人": person,
            "电话": " / ".join(tel_list),
            "手机": " / ".join(dict.fromkeys(mobiles)),
            "传真": "" if fax_raw in ("暂无", "-", "") else fax_raw,
            "地址": "" if addr_raw == "暂无" else addr_raw,
            "邮箱": " / ".join(dict.fromkeys(email_list)),
        }

    def _crawl_company(self, offer):
        rec = {
            "关键词": offer["keyword"],
            "公司名称": offer["company"],
            "联系人": "",
            "电话": "", "手机": "", "传真": "", "地址": "", "邮箱": "",
            "主营商品": offer["subject"],
            "价格": offer.get("price") or "",
            "店铺URL": "", "详情URL": "", "联系方式URL": "",
            "offerId": offer["offerId"], "memberId": offer["memberId"],
            "采集时间": time.strftime("%Y-%m-%d %H:%M:%S"),
            "状态": "",
        }
        od = offer["odUrl"] or f"https://detail.1688.com/offer/{offer['offerId']}.html"
        if not od.startswith("http"):
            od = "https:" + od
        if not self._goto(od):
            rec["状态"] = "详情页拦截未解除"
            return rec
        rec["详情URL"] = self.driver.current_url
        html = self.driver.page_source
        winport, contact, company = self._extract_winport(html)
        if company and not rec["公司名称"]:
            rec["公司名称"] = company
        rec["店铺URL"] = winport
        rec["联系方式URL"] = contact
        if offer.get("guard"):
            meta = self._extract_offer_meta(html)
            if self.debug_collect:      # 开发调参用：留痕判定输入
                self._guard_debug.append({"memberId": offer.get("memberId"),
                                          "company": rec["公司名称"],
                                          "kw": offer["keyword"], "meta": meta})
            if not rec["主营商品"]:
                rec["主营商品"] = meta["title"] or " / ".join(meta["shopCats"][:3])
            if self.relevance_guard and not self._kw_relevant(meta, rec):
                # 类目与关键词不符：到此为止，不再请求联系方式页（既保精准，
                # 又省下无效商家的后续请求——低频才是防封的关键）
                rec["状态"] = "类目不符(已过滤)"
                return rec
        if not contact:
            rec["状态"] = "无联系方式入口"
            return rec
        self._sleep()
        if not self._goto(contact):
            rec["状态"] = "联系方式页拦截未解除"
            return rec
        # 联系方式页可能仍需登录：跳登录则记录状态、不写空壳
        if self._detect_block() == LOGIN_WAIT_SENTINEL:
            rec["状态"] = "未登录-联系方式不可见"
            return rec
        chtml = self.driver.page_source
        self._keep_detail(chtml)     # 留给「无联系方式」分档（口径同 cdp_base）
        rec.update(self._parse_contact(chtml))
        has_contact = any(rec[k] for k in ("电话", "手机", "邮箱", "地址"))
        # 联系方式页有两套模板（contactinfo.htm / contactinfo.html）：若当前模板没解析到，
        # 换另一套 URL 再试一次（部分卖家页面用自定义块，只有 .html 模板能取到）
        if not has_contact and winport:
            alt = contact if contact and contact.rstrip("/").endswith(".html") \
                else winport.rstrip("/") + "/page/contactinfo.html"
            if alt and alt != contact:
                self._sleep()
                if self._goto(alt) and self._detect_block() != LOGIN_WAIT_SENTINEL:
                    self._keep_detail(self.driver.page_source)
                    r2 = self._parse_contact(self.driver.page_source)
                    rec.update({k: v for k, v in r2.items() if v})
                    has_contact = any(rec[k] for k in ("电话", "手机", "邮箱", "地址"))
        rec["状态"] = ("成功" if any(rec[k] for k in ("电话", "手机", "邮箱"))
                  else ("无电话(1688未公开)" if rec["地址"] else "无联系方式"))
        if rec["状态"] != "成功":
            # 没号码到底是卖家自己没填、要登录才给、还是我们解析漏了——只有那一页能回答
            self._note_no_contact()
        return rec

    def _crawl_shop_company(self, entry):
        """供应商渠道采集：店铺URL直达联系方式页（跳过商品详情页，一家只需一次导航）。"""
        winport = entry["winport"]
        rec = {
            "关键词": entry.get("keyword", ""),
            "公司名称": entry["company"],
            "联系人": "", "电话": "", "手机": "", "传真": "", "地址": "", "邮箱": "",
            "主营商品": "", "价格": "",
            "店铺URL": winport, "详情URL": "", "联系方式URL": winport + "/page/contactinfo.html",
            "offerId": "", "memberId": entry["shop"],
            "采集时间": time.strftime("%Y-%m-%d %H:%M:%S"),
            "状态": "",
        }
        contact = winport + "/page/contactinfo.html"
        if not self._goto(contact):
            rec["状态"] = "联系方式页导航失败"
            return rec
        if self._detect_block() == LOGIN_WAIT_SENTINEL:
            rec["状态"] = "未登录-联系方式不可见"
            return rec
        self._keep_detail(self.driver.page_source)
        rec.update(self._parse_contact(self.driver.page_source))
        if not any(rec[k] for k in ("电话", "手机", "邮箱", "地址")):
            # 两套模板回退：.html 取不到就试 .htm
            alt = winport + "/page/contactinfo.htm"
            self._sleep()
            if self._goto(alt) and self._detect_block() != LOGIN_WAIT_SENTINEL:
                rec["联系方式URL"] = alt
                self._keep_detail(self.driver.page_source)
                rec.update({k: v for k, v in self._parse_contact(self.driver.page_source).items() if v})
        rec["状态"] = ("成功" if any(rec[k] for k in ("电话", "手机", "邮箱"))
                  else ("无电话(1688未公开)" if rec["地址"] else "无联系方式"))
        if rec["状态"] != "成功":
            self._note_no_contact()
        # 联系页没给地址时，用公司卡片上的产地做兜底（不影响上面的状态判定口径）
        if not rec["地址"] and entry.get("location"):
            rec["地址"] = entry["location"]
        return rec

    # ── 主采集循环 ─────────────────────────────────────────────
    def crawl(self, keywords, start_index=0):
        self._crawling = True
        self._stop_requested = False
        self._pause_requested = False
        self._run_abandon = ""
        self._seen_members = set()
        self._filtered_count = 0
        self._early_skipped = 0
        # 逐词那两本同样按轮起账（口径同 cdp_base.crawl）：跨轮累加的砍单数会把上一轮
        # 的相关性判定冒充成本轮「这一词为什么零产出」的证据
        self.kw_filtered = {}
        self.kw_early = {}
        self.kw_early_names = {}
        self.kw_filtered_names = {}
        # 条目账按轮起账（口径同 cdp_base）：跨轮累加会把上一轮的回货量冒充本轮的证据，
        # 而 stat_listed 不清零更会让收尾那句「一页都没取到」永远响不起来
        self.kw_items = {}
        self.kw_claimed = {}
        self.stat_listed = 0
        # 分档账同样按轮起账：跨轮累加会把上一轮的「站方没登」冒充本轮的责任判定
        self._nc_kinds = {}
        self._nc_evidence = {}
        self._detail_html = ""
        # 本轮因**判定规则更新**而重新评估的旧「类目不符」家数（口径同 cdp_base）
        self._rel_refilter = 0
        # 每轮重新起账：这个数跨轮累加会把「上一轮的跳过」算进本轮，界面读数就废了
        self._incremental_skipped = 0
        # 链路账与「等人」账同理（口径与 cdp_base.crawl 一致）：跨轮累加的「取页失败」
        # 会让上一轮的网络故障冒充本轮的归因，而「仍在等登录」留着不清更会让界面在
        # 本轮早就正常跑完之后继续喊「等你登录」。
        self._page_fail = 0
        self._last_page_error = ""
        self._waiting_human = ""
        self._guard_debug = []
        self._block_times = []
        self._current_delay = self.delay_between
        seeded = 0
        if self.incremental:
            seeded = self._load_seen_store()
            # 只把「已完整 / 重试已用尽」的条目当去重依据，缺联系人的历史条目自动解锁
            # （口径与 cdp_base 一致：把整张种子库当去重依据，等于一次反爬空页就把一家公司
            # 永久盖章，之后每轮都是「新增公司 0 家」——那是本轮 1688 出 0 家的真凶）
            locked = self._locked_keys()
            self._seen_members |= locked
            self._rel_refilter = len(self._stale_filtered_keys())
            refill = len(self._seen_store) - len(locked) - self._rel_refilter
            if refill:
                self._log(f"🔓 增量库内有 {refill} 家条目联系人曾取空，本轮重采补联系人")
            if self._rel_refilter:
                # 判定规则升级后，旧的「类目不符」盖章不再免检（重判过的家会带上新 rv，
                # 下一轮这个数字自然掉下去）
                self._log(f"🔁 相关性判定规则已更新：本轮重新评估 {self._rel_refilter} 家旧"
                          f"「类目不符」（单轮上限 {self.REL_REFILTER_CAP} 家，其余下轮续）")

        if self.driver is None or not self._is_session_alive():
            if not self.start_browser():
                self._crawling = False
                self._emit("crawl_finished_signal")
                return

        # 先打开首页建立会话并检测登录
        self._goto(self.BASE_URL)
        if self._detect_block() == LOGIN_WAIT_SENTINEL:
            if not self._wait_unblock(LOGIN_WAIT_SENTINEL):
                self._crawling = False
                self._emit("crawl_finished_signal")
                return
        # 「这个 Chrome 里 1688 没登录」必须在开局说清，而不是等第一家撞验证页再说
        self._preflight_login_notice()

        total = len(keywords)
        chans = []
        if self.main_channel:
            chans.append("主搜")
        if self.sem_channel:
            chans.append("商品SEM")
        if self.company_channel:
            chans.append("供应商")
        skip_note = f"，增量模式已带 {seeded} 家历史公司（本轮命中即跳过、不再重复请求）" if seeded else ""
        self._log(f"🚀 开始采集，共 {total} 个关键词（{'＋'.join(chans)} 渠道），每关键词每渠道最多 {self.max_pages} 页{skip_note}")
        kws = [str(x).strip() for x in keywords]
        done = 0
        ki = start_index
        self._last_relative = []
        while ki < len(kws):
            if self._stop_requested:
                break
            while self._pause_requested and not self._stop_requested:
                time.sleep(0.5)
            kw = kws[ki]
            ki += 1
            if not kw:
                continue
            self._log(f"▶ [{ki}/{len(kws)}] 关键词：{kw}")
            self._last_relative = []
            try:
                if self.main_channel:
                    for offer in self._iter_keyword_main_offers(kw):
                        if self._stop_requested:
                            break
                        while self._pause_requested and not self._stop_requested:
                            time.sleep(0.5)
                        rec = self._crawl_company(offer)
                        if rec.get("状态") == "类目不符(已过滤)":
                            # 垃圾商家不进结果表，也省掉了联系方式页请求（防封关键）
                            self._note_detail_filter(kw, rec.get("公司名称", ""))
                            self._commit_to_store(rec)   # 记住它，下轮连详情页都不必再进
                            if self._filtered_count % 10 == 1:
                                self._log(f"  ⛔ 类目不符已过滤 {self._filtered_count} 家"
                                          f"（最近：{rec['公司名称'] or rec['主营商品'][:20]}）")
                            continue
                        self._add_result(rec)
                        done += 1
                        self._emit("progress_signal", done, max(done, 1))
                        self._emit("stats_signal",
                                   f"关键词 {ki}/{len(kws)}｜已采公司 {done}｜间隔 {self._current_delay:.1f}s")
                        self._sleep()
                for offer in (self._iter_keyword_offers(kw) if self.sem_channel else ()):
                    if self._stop_requested:
                        break
                    while self._pause_requested and not self._stop_requested:
                        time.sleep(0.5)
                    rec = self._crawl_company(offer)
                    # 站外渠道历史上不进这个分支（因为没有 guard），现在与主搜同口径：
                    # 类目不符的只计数不入库，不拿垃圾商家撑行数。
                    if rec.get("状态") == "类目不符(已过滤)":
                        self._note_detail_filter(kw, rec.get("公司名称", ""))
                        self._commit_to_store(rec)
                        if self._filtered_count % 10 == 1:
                            self._log(f"  ⛔ 类目不符已过滤 {self._filtered_count} 家"
                                      f"（最近：{rec.get('公司名称') or str(rec.get('主营商品') or '')[:20]}）")
                        continue
                    self._add_result(rec)
                    done += 1
                    self._emit("progress_signal", done, max(done, 1))
                    self._emit("stats_signal",
                               f"关键词 {ki}/{len(kws)}｜已采公司 {done}｜间隔 {self._current_delay:.1f}s")
                    self._sleep()
                if self.company_channel and not self._stop_requested:
                    for entry in self._iter_keyword_companies(kw):
                        if self._stop_requested:
                            break
                        while self._pause_requested and not self._stop_requested:
                            time.sleep(0.5)
                        rec = self._crawl_shop_company(entry)
                        self._add_result(rec)
                        done += 1
                        self._emit("progress_signal", done, max(done, 1))
                        self._emit("stats_signal",
                                   f"关键词 {ki}/{len(kws)}｜已采公司 {done}｜间隔 {self._current_delay:.1f}s")
                        self._sleep()
                if self.expand_related and not self._stop_requested:
                    added = 0
                    for w in self._related_candidates(kw):
                        if w not in kws:
                            kws.append(w)
                            added += 1
                    if added:
                        self._log(f"🔎 「{kw}」扩展相关词 {added} 个（现共 {len(kws)} 个关键词）")
            except Exception as e:
                # 兜底：迭代器内直接读 driver.page_source/current_url 等极端时序下仍可能
                # 抛会话失效。会话失效→重启续采；其它异常→跳过该关键词继续，绝不让线程崩。
                raw = first_line(e, 200) if str(e) else e.__class__.__name__
                msg = request_error_shape(e, cap=160)
                if self._session_dead(raw) or self._session_dead(msg):
                    self._log(f"⚠ 「{kw}」处理中浏览器会话断开（{short_reason(msg, 60)}），重启后续采…")
                    if not self.try_restore_session():
                        self._log("❌ 无法恢复浏览器会话，本轮结束（已采数据已保存，可稍后继续）")
                        self._stop_requested = True
                    self._timeout_set = False
                else:
                    self._log(f"⚠ 「{kw}」处理异常：{msg} → 跳过该关键词继续")
            if self.incremental:
                self._save_seen_store()   # 每个关键词收尾即落盘，中途被拦/异常也不丢已学到的
        self._crawling = False
        if self.incremental:
            self._save_seen_store()
        tail = (f"，另有 {self._filtered_count} 家类目不相关已过滤"
                if self._filtered_count else "")
        if getattr(self, "province", "") and (self._pf_dropped or getattr(self, "_pf_unknown", 0)):
            pv_display = self.province if "," not in self.province else "目标省份"
            tail += f"、{self._pf_dropped} 家非{pv_display}已排除"
            if getattr(self, "_pf_unknown", 0):
                tail += f"（另有 {self._pf_unknown} 家页面没写属地：已转企知道反查）"
        early = (f"，列表页早过滤 {self._early_skipped} 家(未进详情即省请求)"
                 if self._early_skipped else "")
        # 砍单名单样本 + 「无联系方式是谁的责任」分档（口径与 cdp_base 收尾那一句一致）：
        # 只有总数时，「N 家类目不相关」和「N 家没号码」都是无法执行的一句话
        nc = ("，无联系方式分档：" + nc_kinds_text(self._nc_kinds, self._nc_evidence)
              if self._nc_kinds else "")
        parts = []
        for k in sorted(set(self.kw_filtered_names) | set(self.kw_early_names)):
            one = "、".join(x for x in (self._cut_note(self.kw_filtered_names, k, "复核"),
                                        self._cut_note(self.kw_early_names, k, "早过滤")) if x)
            # 回货数跟着名单一起给，并把「站方自报」与「我们解析到」分开念：只念后者时，
            # 「卡片改版只认出 1 家」会被念成「这一词本就只回 1 条」——两种局的处方正好相反
            # （一个修解析、一个换词）。口径同 cdp_base._kw_zero_reason 的四档分叉。
            _it, _cl = self.kw_items.get(k), self.kw_claimed.get(k)
            if _it and _cl and _cl > _it:
                got = f"（站方自报 {_cl} 条、列表解析只认出 {_it} 条：该修卡片解析口径）"
            elif _it:
                got = f"（站方这一词回了 {_it} 条）"
            if one:
                parts.append(f"「{k}」{one}{got}")
        samp = ("，砍单样本：" + " ".join(parts)) if parts else ""
        # 「0 新增」必须有一句解释从哪来：跳过多少家、其中多少家还欠一次补联系人。
        # 只有「种子库累计 N 家」这一句是不够的——它说的是库有多大，不是这轮为什么没活。
        incr = (f"，增量库跳过 {self._incremental_skipped} 家(已采过或重试已用尽，未重复访问)"
                if self._incremental_skipped else "")
        # 链路那一笔单独记账（2026-11-15 实机体检）：整轮都卡在导航/等登录时，上面每一笔
        # 都是 0、日志里只剩「共 0 家」，看的人以为站点没数据。「一页都没取到」这件事只有
        # 取页那一层知道，所以账要由它记、并在这句收尾里占一格（拼法与 cdp_base 同构，
        # 对拍见 b2bcollect_build/test_data_dir_offline.py 第 14 节）。
        # 计数与原因分家：只拿计数决定开不开口，详情腿整条不通就没人念（拼法与 cdp_base 同构，
        # 共用同一个把手名 page_ledger_line，对拍见 test_data_dir_offline.py 第 14 节）
        _pf_line = page_ledger_line(self._page_fail, self._last_page_error)
        pagefail = f"，{_pf_line}" if _pf_line else ""
        pend = self._seen_store_pending() if self.incremental else 0
        pending = (f"，另有 {pend} 家欠补联系人，下轮自动重采" if pend else "")
        saved = f"，种子库累计 {len(self._seen_store)} 家" if self.incremental else ""
        # 收尾标记与结论同源（口径与 cdp_base 的收尾一致）：被拦/被停止/零产出都各有
        # 一句归因，绝不能因为「跑完了」就一律打 ✅——「1688 一直 0 家却查不出原因」
        # 有一半责任在这句恒 ✅ 的日志上（另一半是驱动判据，见 _attach）。
        if getattr(self, "_run_abandon", ""):
            mark, why = "⛔", f"｜本轮因{self._run_abandon}提前收"
        elif self._waiting_human:
            mark, why = "⛔", (f"｜仍在等{self._waiting_human}（不是采不到：等人处理完"
                               "会自动续采，这一路的配额没烧）")
        elif self._stop_requested:
            mark, why = "⏹", "｜本轮被停止"
        elif not self.results:
            # 「零产出」有两种零，处置完全相反：链路根本没通（查出口/登录态/域名可达）
            # 和增量库把这一轮全跳过了（种子库已含，链路是通的，要新数据得换词）。
            # 合成一句「见上方账目」时，上一轮就是这么把 3 站全跳过、1 站网络不可达
            # 都报成「采不到」的（判据片段与 cdp_base 逐字对拍，见第 14 节）。
            if self._page_fail and not self.stat_listed:
                mark, why = "⛔", (f"｜链路未通：{self._page_fail} 页一页都没取到，"
                                   "这不是本站没有数据（先查出口网络/域名可达，再谈代码）")
            elif self._incremental_skipped:
                mark, why = "⚠", (f"｜增量库全跳过 {self._incremental_skipped} 家"
                                   f"（种子库 {len(self._seen_store)} 家已含，链路是通的；"
                                   "要新数据得换关键词或放开省份）")
            else:
                mark, why = "⚠", "｜本轮零产出（见上方账目）"
        else:
            mark, why = "✅", ""
        self._log(f"{mark} 采集结束，共 {len(self.results)} 家公司"
                  f"{why}{pagefail}{early}{incr}{pending}{tail}{samp}{nc}{saved}")
        self._emit("crawl_finished_signal")

    # 兼容旧契约（GUI 可能调用）
    def search_company(self, name):
        return self._crawl_company({"keyword": "", "company": name, "offerId": "",
                                    "odUrl": "", "memberId": name, "loginId": "",
                                    "price": "", "subject": ""})

    # ── 控制 ───────────────────────────────────────────────────
    def stop_crawl(self):
        self._stop_requested = True
        self._log("正在停止…")

    def pause_crawl(self):
        self._pause_requested = True
        self._log("已暂停")

    def resume_crawl(self):
        self._pause_requested = False
        self._log("已恢复")

    # ── 导出 ───────────────────────────────────────────────────
    EXPORT_HEADERS = ["序号", "关键词", "公司名称", "联系人", "电话", "手机", "传真",
                      "地址", "邮箱", "主营商品", "价格", "店铺URL", "详情URL",
                      "联系方式URL", "offerId", "memberId", "采集时间", "状态"]

    def export_excel(self, output_path):
        rows = self.snapshot_results()
        if not rows:
            self._log("没有数据可导出")
            return False
        try:
            from openpyxl import Workbook
            wb = Workbook()
            ws = wb.active
            ws.title = "1688企业数据"
            ws.append(self.EXPORT_HEADERS)
            for i, r in enumerate(rows, 1):
                ws.append([i] + [r.get(h, "") for h in self.EXPORT_HEADERS[1:]])
            wb.save(output_path)
            self._log(f"✅ 已导出 {len(rows)} 条：{output_path}")
            return True
        except Exception as e:
            self._log(f"❌ 导出失败：{e}")
            return False

    # ── 对接《客户库数据导入模板》清洗（复用成熟模块）───────────
    @staticmethod
    def _norm_landline(tok):
        """把 1688 的 '86 0534 5085777' 归一为清洗模块能识别的 '0534-5085777'。"""
        t = re.sub(r"^86[\s\-]?", "", (tok or "").strip())   # 去国家码
        t = re.sub(r"[\s]+", "-", t)                          # 区号与号码间空格转连字符
        return t

    def _to_cleaner_record(self, r):
        """把 1688 原始记录映射为清洗模块认识的字段。"""
        mobiles = [x for x in re.split(r"[/、,;]", r.get("手机") or "") if x.strip()]
        landlines = [self._norm_landline(x) for x in re.split(r"[/、,;]", r.get("电话") or "") if x.strip()]
        phones = " ; ".join([m.strip() for m in mobiles] + [l for l in landlines if l])
        status = r.get("状态") or ""
        remark = ""
        if r.get("关键词"):
            remark += "关键词:" + r["关键词"]
        # 主营/行业/国家/经营模式/区域/官网 已由结构化字段传递，_remark() 显式写入备注
        return {
            "公司名": r.get("公司名称") or "",
            "联系人": r.get("联系人") or "",
            "岗位": r.get("岗位") or "",
            "电话": phones,
            "邮箱": r.get("邮箱") or "",
            "地址": r.get("地址") or "",
            "官网": r.get("店铺URL") or r.get("详情URL") or "",
            "主营": r.get("主营商品") or r.get("主营产品") or "",
            "行业": r.get("行业") or "",
            "国家": r.get("国家") or "",
            "经营模式": r.get("经营模式") or "",
            "所在区域": r.get("所在区域") or "",
            "数据来源": "1688",
            "状态": "" if status == "成功" else status,
            "备注": remark,
        }

    @staticmethod
    def filter_import_rows(rows, log=None):
        """清洗后入库前的最终过滤：手机号/固话/邮箱 全为空的行直接删除。
        （原始数据不动，只影响清洗后 / 导入云展的数据。）"""
        kept = []
        dropped = 0
        for r in rows or []:
            if any(str(r.get(k) or "").strip() for k in ("手机号", "固话", "邮箱")):
                kept.append(r)
            else:
                dropped += 1
        if dropped and log:
            log(f"🧹 清洗过滤：剔除 {dropped} 条「无手机号/固话/邮箱」的记录（原数据保留）")
        return kept

    def clean_records(self, records, log=None):
        """把 1688 原始记录 → 映射 → 清洗成模板行 → 剔除无任何联系方式的行。
        AutoSaveWorker / 导出 / 云展导入三处统一走这里，保证口径一致。"""
        log = log or self._log
        recs = [self._to_cleaner_record(r) for r in records]
        rows = _cleaner.build_clean_rows(recs, log=log)
        return self.filter_import_rows(rows, log=log)

    def build_clean_rows(self, log=None):
        """返回按模板列结构清洗后的行（跨公司合并去重、拆手机/固话/邮箱、剔除无联系方式）。"""
        log = log or self._log
        return self.clean_records(self.snapshot_results(), log=log)

    def export_cleaned_excel(self, output_path, template_path="", log=None):
        """写出与《客户库数据导入模板.xlsx》完全一致格式的清洗后数据。"""
        log = log or self._log
        rows = self.build_clean_rows(log=log)
        if not rows:
            log("没有可清洗入库的数据")
            return False
        if not template_path:
            template_path = _cleaner.locate_template([str(Path(output_path).parent), str(self._data_dir)])
        return _cleaner.write_clean_excel(rows, output_path, template_path=template_path, log=log)



if __name__ == "__main__":
    # 命令行自测：python ali1688_crawler.py "AHU净化空调箱" [max_pages]
    kws = sys.argv[1].split(",") if len(sys.argv) > 1 else ["AHU净化空调箱"]
    c = Ali1688Crawler()
    if len(sys.argv) > 2:
        c.max_pages = int(sys.argv[2])
    c.delay_between = 2.0
    c.start_browser()
    c.crawl(kws)
    c.export_excel(str(Path(__file__).parent / "1688_测试导出.xlsx"))
