# 离线自测：拦截分诊（IP 限流 vs 可人工过验证）—— 2026-10-29 八方资源网客户反馈
# 客户原话：「八方大概采到 200 多个就被防采了，怎么点都被限制进入」「它有时候没跳出
# 八方资源网这个网页，只能在你设计的这个软件点确认」；而 1688 出验证码会正常开页。
# 四条根因与本测试对应：
#   ① 弹窗文案说谎：八方 BLOCK_OPEN_BROWSER=False 根本不开 Chrome，界面却写「已打开」
#      → 断言 reason 带分型前缀、界面按前缀出两套文案、IP 类不声称已开页；
#   ② IP 限流被当“等人工”：断言 IP 类绝不拉浏览器、熔断阈值降到 3；
#   ③ 探针自己打断冷却：断言探测间隔从 45s 起按被拦次数指数退避、听 Retry-After；
#   ④ 记录级节流导致 3~5 倍超速：断言八方开了请求级节流且每个请求前都对齐节奏。
# 全程不联网、不启 Chrome：响应注入假会话，cdp_base 的 time 换成可推进的假时钟。
import sys
import inspect

sys.path.insert(0, "/Users/jhnoplan/Desktop/PythonDemo/b2bcollect_build")

import cdp_base
from cdp_base import CdpSiteCrawler, BLOCK_TAG_IP, BLOCK_TAG_CAP
from b2b168_crawler import B2b168Crawler
from parallel_session import ParallelSession

ok = 0


def check(name, cond):
    global ok
    assert cond, f"FAIL: {name}"
    ok += 1
    print(f"  ✓ {name}")


import time as _real_time


class FakeTime:
    """假时钟：sleep 不真等，只把 now 往前推；未实现的属性回落真 time 模块。"""

    def __init__(self, start=1000.0):
        self.now = start
        self.slept = []
        self.on_sleep = None

    def reset(self, start=1000.0):
        self.now = start
        self.slept = []
        self.on_sleep = None

    def time(self):
        return self.now

    def monotonic(self):
        return self.now

    def sleep(self, s):
        self.slept.append(s)
        self.now += s
        cb, self.on_sleep = self.on_sleep, None      # 模拟用户在等待中途点了按钮
        if cb:
            cb()

    def __getattr__(self, item):
        return getattr(_real_time, item)


FT = FakeTime()
cdp_base.time = FT            # 看门狗/节流的等待全部走假时钟（否则离线跑要等几十分钟）


class Resp:
    def __init__(self, status_code, text, headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self.encoding = "utf-8"
        self.apparent_encoding = "utf-8"


class HttpSession:
    """按脚本吐响应，并记下每次请求（method, url）。"""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    def request(self, method, url, timeout=None, **kw):
        self.requests.append((method, url))
        if self.script:
            return self.script.pop(0)
        return Resp(200, "<html>" + "公司主营 联系方式 " * 400 + "</html>")


def equip(c):
    """装上事件/日志收集器，返回 (事件列表, 日志列表)。"""
    events, logs = [], []
    c._emit = lambda name, *a: events.append((name, a[0] if a else ""))
    c._log = lambda msg="": logs.append(str(msg))
    c.signals = None
    c._commit_to_store = lambda rec: None
    FT.reset()
    return events, logs


B2B168_502 = "八方资源网 502 插页样本"
REASON_502 = "站点返回 HTTP 502（IP 级限流/冷却，非验证码）"

# ══════════════════════════════════════════════════════════
# 1) 分诊：状态码类 = IP 限流（没有滑块可点）；带验证词的小插页 = 可人工过
# ══════════════════════════════════════════════════════════
c = B2b168Crawler()
events, logs = equip(c)

r = c.http_block_reason(502, B2B168_502, "https://b2b168.com/x", {"Retry-After": "120"})
check(f"八方 502 判为拦截（{r}）", bool(r))
check("502 归类为 ip（服务端拒出口 IP，不是验证码）", c._block_kind == "ip")
check("IP 类原因明确写出「非验证码」，界面与日志都不会再骗人去找入口",
      "IP 级限流" in r and "非验证码" in r)
check("服务端 Retry-After 被读走（冷却时长听站点的，不再我们自己猜）", c._retry_after == 120)

c._retry_after = 0
for code in (429, 503, 520, 521):
    c.http_block_reason(code, "x" * 9000, "https://b2b168.com/y")
    check(f"HTTP {code} 同样归入 ip 类", c._block_kind == "ip")
check("无 Retry-After 时记 0", c._retry_after == 0)

c._block_kind = ""
r2 = c.http_block_reason(200, "<title>请完成安全验证</title>滑动验证" + "y" * 100,
                         "https://b2b168.com/z")
check(f"小插页含「滑动验证」归入 captcha（确实可能人能过）（{r2}）", c._block_kind == "captcha")
c._block_kind = ""
r3 = c.http_block_reason(200, "<title>访问异常</title>系统繁忙" + "z" * 100,
                         "https://b2b168.com/w")
check("小插页只写「访问异常/系统繁忙」（无验证词）仍归 ip，不叫人工", c._block_kind == "ip")
check("200 大页不误判", c.http_block_reason(200, "正常商铺" * 2000) == "")
check("_parse_retry_after 认秒数、忽略非法值、缺头不报错",
      CdpSiteCrawler._parse_retry_after({"Retry-After": "90"}) == 90 and
      CdpSiteCrawler._parse_retry_after({"Retry-After": "Wed, 21 Oct 2026"}) == 0 and
      CdpSiteCrawler._parse_retry_after(None) == 0)

# ══════════════════════════════════════════════════════════
# 2) IP 类处置：绝不拉起 Chrome，界面拿到的是【IP限流·无需操作】前缀
# ══════════════════════════════════════════════════════════
opened = []
c.open_verify_page = lambda url="": opened.append(url) or True
probed = []


def still_blocked():
    probed.append(FT.time())
    return REASON_502


c._block_kind = "ip"
c._retry_after = 0
c._block_reason = REASON_502
c.BLOCK_POLL_IP, c.BLOCK_POLL_IP_MAX, c.BLOCK_MAX_WAIT_IP = 45, 600, 2700
c.probe_block = still_blocked
res_first = c.handle_http_block("https://b2b168.com/x", c._block_reason)
check("八方 IP 限流时完全不拉浏览器（靠分诊拦住，不再拿 BLOCK_OPEN_BROWSER=False 一刀切）",
      opened == [])
check("未恢复时返回 False（上层按待重试记账，不写空记录）", res_first is False)
check(f"第一次被拦的探测间隔为 45s（旧实现固定 15s，等于自己把冷却打断）"
      f"（首轮 {round(probed[0] - 1000.0)}s）", probed and round(probed[0] - 1000.0) == 45)
check(f"探测期间持续拉长间隔实测，不会 15s 一次地撞站（共探 {len(probed)} 次）",
      len(probed) > 3)
tagged = [t for n, t in events if n == "blocked_signal"]
check(f"blocked_signal 文本带 IP 分型前缀（{tagged[0][:34]}…）",
      bool(tagged) and tagged[0].startswith(BLOCK_TAG_IP))
check("前缀里带站名，界面能直接拆出「八方资源网」", "八方资源网" in tagged[0])
check("日志里没有「请在 Chrome 窗口里手动过验证」这类叫人的话",
      not any("手动过验证" in m for m in logs))
check("日志明确写出「没有验证码可点也不需要你操作」",
      any("没有验证码可点也不需要你操作" in m for m in logs))

# ══════════════════════════════════════════════════════════
# 3) 指数退避 + Retry-After + IP 熔断阈值 3
# ══════════════════════════════════════════════════════════
FT.reset()
probed.clear()
c._retry_after = 120
c.handle_http_block("https://b2b168.com/x", REASON_502)
check(f"服务端给了 Retry-After 就先等够那么久再探（首轮 {round(probed[0] - 1000.0)}s）",
      round(probed[0] - 1000.0) >= 120)

c2 = B2b168Crawler()
ev2, lg2 = equip(c2)
seen = []
gaps = []


def probe_gap():
    seen.append(FT.now)
    if len(seen) > 1:
        gaps.append(FT.now - seen[-2])
    return REASON_502                   # 永不解除，逼出整轮退避节奏


c2._block_reason = REASON_502
c2.BLOCK_POLL_IP, c2.BLOCK_MAX_WAIT_IP = 45, 260
c2._max_blocks_ip = 99              # 本段只测节奏，不让熔断抢先
c2._wait_manual_unblock(REASON_502, probe_gap)
check(f"同一轮内探测间隔恒为该轮基准 45s（{gaps[:3]}）", gaps and all(g == 45 for g in gaps))

# 换一个实例累计被拦次数，验证「越拦越久」
c2._block_times = [FT.now]          # 窗口内已有 1 次 → 本轮 strikes=2 → gap 90
FT.reset()
seen.clear()
gaps.clear()
c2._wait_manual_unblock(REASON_502, probe_gap)
check(f"第 2 次被拦基准间隔翻倍到 90s（{gaps[:2]}）", gaps and gaps[0] == 90)
c2._block_times = [FT.now, FT.now]  # strikes=3 → 180s
FT.reset()
seen.clear()
gaps.clear()
c2.BLOCK_MAX_WAIT_IP = 400
c2._wait_manual_unblock(REASON_502, probe_gap)
check(f"第 3 次被拦基准间隔再翻倍到 180s（{gaps[:2]}）", gaps and gaps[0] == 180)
c2.BLOCK_POLL_IP_MAX, c2.BLOCK_MAX_WAIT_IP = 600, 700
c2._block_times = [FT.now] * 4
FT.reset()
seen.clear()
gaps.clear()
c2._wait_manual_unblock(REASON_502, probe_gap)
check(f"退避封顶不超过 BLOCK_POLL_IP_MAX=600s（{gaps[:2]}）",
      gaps and max(gaps) <= 600)

c3 = B2b168Crawler()
ev3, lg3 = equip(c3)
c3._block_reason = REASON_502
c3.BLOCK_POLL_IP, c3.BLOCK_MAX_WAIT_IP = 45, 200
c3._max_blocks_ip = 3
c3.open_verify_page = lambda url="": False
c3.probe_block = lambda: REASON_502
r1 = c3.handle_http_block("https://b2b168.com/a", REASON_502)
r2 = c3.handle_http_block("https://b2b168.com/a", REASON_502)
r3 = c3.handle_http_block("https://b2b168.com/a", REASON_502)
check("第 1、2 次被拦都原地等冷却（未熔断）", r1 is False and r2 is False)
check("IP 限流第 3 次即优雅收尾（阈值 3，旧版 5 次太宽会一路撞成硬封）",
      c3._stop_requested is True)
check("熔断日志说明「这不是验证码，人工点不掉」",
      any("这不是验证码，人工点不掉" in m for m in lg3))
check("熔断日志给出可操作的恢复手段（换 IP / 等 30 分钟 / 调大采集间隔）",
      all(k in "".join(lg3) for k in ("重拨宽带", "采集间隔", "断点续采")))
check("熔断时也没借过浏览器标签", c3._borrowed_tab is False)

# ══════════════════════════════════════════════════════════
# 4) 对照：可人工过的验证页仍然拉浏览器 + 5s/15s 轮询 + 前缀【需人工验证】
# ══════════════════════════════════════════════════════════
m = B2b168Crawler()
evm, lgm = equip(m)
m.BLOCK_OPEN_BROWSER, m.NEEDS_BROWSER = True, True
m.BLOCK_POLL, m.BLOCK_MAX_WAIT = 15, 200
m._block_kind = "captcha"
m._block_reason = "出现验证/拦截页：滑块验证"
opened2 = []
m.open_verify_page = lambda url="": opened2.append(url) or True
hits = []


def cap_probe():
    hits.append(FT.now)
    return "" if len(hits) >= 2 else m._block_reason      # 第二次探测即已解除


m.probe_block = cap_probe
res = m.handle_http_block("https://b2b168.com/v", m._block_reason)
check("可人工过的验证页确实把页面递到 Chrome 眼前（与八方相反）",
      opened2 == ["https://b2b168.com/v"])
check("探测到解除后 handle_http_block 返回 True（上层重试同一请求）", res is True)
check(f"首轮探测只要 5s（用户秒过验证时不用白等一轮）（{round(hits[0] - 1000.0)}s）",
      hits and round(hits[0] - 1000.0) == 5)
check(f"之后按 BLOCK_POLL=15s 轮询（{round(hits[1] - hits[0])}s）",
      len(hits) > 1 and round(hits[1] - hits[0]) == 15)
cap_ev = [t for n, t in evm if n == "blocked_signal"]
check(f"验证页类前缀为【需人工验证】（{cap_ev[0][:24]}…）", cap_ev[0].startswith(BLOCK_TAG_CAP))
check("验证页类日志仍然叫用户在 Chrome 里手动过验证",
      any("请在 Chrome 窗口里手动过验证" in s for s in lgm))
check("解除后发出 block_cleared_signal（界面收横幅）",
      any(n == "block_cleared_signal" for n, _ in evm))

FT.reset()
hits.clear()
m._block_times = []
res2 = m._wait_manual_unblock(m._block_reason, cap_probe)
check("验证页类可重复进入等待循环（解除后不会卡住后续轮次）", res2 is True)

# ════════════════════════════════════════════════════════
# 4b) 分诊升级：命中状态码也要看响应体。客户「这么久只跳出过 1 次八方验证页」
#      ——说明它确实也会返回可人工过的插页，按状态码一律归 ip 会把这一道堵死
# ════════════════════════════════════════════════════════
V_JS = '<script src="https://do.11467.com/v.js?v=rollback-1"></script>'   # 顺企网实测 429 + 70 字节
s = B2b168Crawler()
evs, lgs = equip(s)
FT.reset()
rs = s.http_block_reason(429, V_JS, "https://b2b168.com/q")
check(f"429 + v.js JS 挑战归 captcha（上一版按状态码一律归 ip，会把「其实能过」误杀成干等）（{rs}）",
      s._block_kind == "captcha")
check("captcha 类原因不带「HTTP」字样（避免被上层兜底猜成 IP 限流）", "HTTP" not in rs)
s2 = B2b168Crawler()
s2.http_block_reason(429, "Server Error" * 1000, "https://b2b168.com/q")
check("429 + 无验证特征的报错体仍归 ip（不乱叫人工）", s2._block_kind == "ip")
s3 = B2b168Crawler()
s3.http_block_reason(502, "<title>访问异常</title>系统繁忙请稍后再试" + "p" * 200,
                     "https://b2b168.com/q")
check("八方 502「访问异常/系统繁忙」（客户本次那种）仍归 ip", s3._block_kind == "ip")
check("security 这类报错页满天飞的泛词不算验证特征",
      B2b168Crawler()._looks_like_challenge("https://example.com/security/error") is False)

s._block_reason = rs
s._block_times = []
s._borrowed_tab = False
s.BLOCK_POLL, s.BLOCK_MAX_WAIT = 15, 100
opened3 = []
s.open_verify_page = lambda url="": opened3.append(url) or True
s.probe_block = lambda: ""                 # 一探就过：本段只验分诊路由
res3 = s.handle_http_block("https://b2b168.com/q", rs)
check("八方真出现验证插页时会借标签把页面递到眼前（不再让客户找不到入口）",
      opened3 == ["https://b2b168.com/q"])
check("八方不再写死 BLOCK_OPEN_BROWSER=False（一刀切会把可过的插页一并堵死）",
      B2b168Crawler.BLOCK_OPEN_BROWSER is True)
check("八方声明 clearance cookie 回收域（浏览器过完验证抄给 HTTP 会话）",
      B2b168Crawler.BLOCK_COOKIE_DOMAIN == "b2b168.com")
check("验证插页即使带 429 状态码也走等人工节奏（前缀【需人工验证】）",
      any(n == "blocked_signal" and t.startswith(BLOCK_TAG_CAP) for n, t in evs))


class NoOpenCrawler(B2b168Crawler):
    """模拟「平时不借标签」的纯 HTTP 站（如 aipage）：旧写法会把它的验证插页误判成 ip。"""
    BLOCK_OPEN_BROWSER = False


k1 = NoOpenCrawler()
equip(k1)
k1._block_kind = "captcha"
k1._wait_manual_unblock("站点返 429 且是人机验证插页（可在浏览器里手动过）", lambda: "")
check("分诊结果优先于 BLOCK_OPEN_BROWSER 兜底（kind=captcha 不会被误判成干等）",
      k1._ip_cooldown is False)
k2 = NoOpenCrawler()
equip(k2)
k2._block_kind = "ip"
k2._wait_manual_unblock("站点返回 HTTP 502（IP 级限流/冷却，非验证码）", lambda: "")
check("kind=ip 时 BLOCK_OPEN_BROWSER=True 也不会被拉去等人工（八方那种）",
      k2._ip_cooldown is True)

# ══════════════════════════════════════════════════════════
# 4c) 小白逃生口：换了出口 IP 之后点「我换好网络了，马上继续」，当场重测
#      （旧行为只能干等下一个 45s~600s 探测周期，读不懂也等不起）
# ══════════════════════════════════════════════════════════
w1 = B2b168Crawler()
eq1, lq1 = equip(w1)
w1._block_reason = REASON_502
w1._block_times = []
w1._block_probe_url = "https://b2b168.com/x"     # 刚被拦的那一页
w1._current_delay = 30.0                 # 被拦后自动降速到的值
w1.BLOCK_POLL_IP, w1.BLOCK_MAX_WAIT_IP = 45, 300
w1._max_blocks_ip = 3
hit_t = []


def wake_probe():
    hit_t.append(FT.now)
    return "" if len(hit_t) >= 2 else REASON_502     # 换完网络第二次就通


FT.on_sleep = w1.retry_now               # 等待中途模拟用户按下按钮
res_w = w1._wait_manual_unblock(REASON_502, wake_probe)
check(f"点了「马上继续」就不等 45s 退避，立刻真探一次（首次探测在 {round(hit_t[0] - 1000.0, 1)}s）",
      hit_t and hit_t[0] - 1000.0 < 5)
check("重测通过即恢复续采（返回 True 让上层重试同一请求）", res_w is True)
check("retry_now 把被拦计数清零（换的是新出口 IP，不该按旧 IP 黑历史熔断）",
      w1._block_times == [])
check("retry_now 把自动降速的间隔复原（新 IP 不需要背着 30s 的包袱）",
      w1._current_delay == w1.delay_between)
check("retry_now 后仍记着被拦页地址，重测打的就是那一页",
      w1._block_probe_url == "https://b2b168.com/x")
check("等待日志里能看到「不等本轮退避，立刻重测」",
      any("不等本轮退避" in m for m in lq1))

FT.on_sleep = None
w2 = B2b168Crawler()
eq2, lq2 = equip(w2)
check("没在被拦时点按钮也不谎称在重测（如实说明当前没在等放行）",
      w2.retry_now() is False and any("没在等放行" in m for m in lq2))

# ══════════════════════════════════════════════════════════
# 5) 请求级节流：一家公司 5 个请求不再背靠背打出去
# ══════════════════════════════════════════════════════════
check("八方开启请求级节流（只按记录收尾睡一次是它被 502 的直接原因）",
      B2b168Crawler.PER_REQUEST_PACE is True)
check("八方 PACE_FACTOR=0.5（摊平突发又不至吞吐掉到 1/5）", B2b168Crawler.PACE_FACTOR == 0.5)
check("八方默认间隔从 2.0 上调到与安全下限一致", B2b168Crawler().delay_between == 4.0)
check("八方声明 4s 采集间隔安全下限", B2b168Crawler.MIN_DELAY == 4.0)
check("基类默认无下限（不把浏览器站的节奏一并拉慢）", CdpSiteCrawler.MIN_DELAY == 0.0)
check("基类默认不开请求级节流（浏览器站一条记录=一次导航，开了只会莫名慢 2~3 倍）",
      CdpSiteCrawler.PER_REQUEST_PACE is False and CdpSiteCrawler.PACE_FACTOR == 1.0)

p = B2b168Crawler()
evp, lgp = equip(p)
p._session = HttpSession([Resp(200, "<html>" + "商铺正文 " * 500 + "</html>"),
                          Resp(200, "<html>" + "商铺正文 " * 500 + "</html>"),
                          Resp(200, "<html>" + "商铺正文 " * 500 + "</html>")])
paces = []
orig_pace = B2b168Crawler._pace_before_request
p._pace_before_request = lambda: (paces.append(len(p._session.requests)), orig_pace(p))
p._fetch("GET", "https://x.b2b168.com/")
p._fetch("GET", "https://x.b2b168.com/contact.aspx", tries=1)
p._fetch("GET", "https://x.b2b168.com/home.aspx", tries=1)
check(f"每个真实请求前都对齐一次节奏（3 请求 3 次）（{len(paces)}）", len(paces) == 3)
check(f"节流真的付了等待时间（假时钟推进 {round(FT.now - 1000.0)}s）", FT.now > 1000.0)

q = B2b168Crawler()
evq, _ = equip(q)
q.PER_REQUEST_PACE = False
q._last_activity = FT.time()
base = len(FT.slept)
q._pace_before_request()
check("开关关掉时 _pace_before_request 不产生任何等待（不会拖累其它站点）",
      len(FT.slept) == base)
q.PER_REQUEST_PACE = True
q._pace_before_request()
check("开关打开时按 PACE_FACTOR 付一段等待", len(FT.slept) > base)

q2 = B2b168Crawler()
equip(q2)
q2._last_activity = 0.0
s0 = len(FT.slept)
q2._pace_before_request()
check("还没发过任何请求（首轮）不空等", len(FT.slept) == s0)

# ══════════════════════════════════════════════════════════
# 6) 界面层：按前缀出两套文案，IP 类不再声称「已在 Chrome 里打开对应页面」
# ══════════════════════════════════════════════════════════
import b2bcollect_gui as gui

W = gui.MainWindow
sp = W._split_block
check("拆 IP 类：类型/站名/原因三样都对",
      sp(BLOCK_TAG_IP + "八方资源网：站点返回 HTTP 502（IP 级限流/冷却，非验证码）") ==
      ("ip", "八方资源网", "站点返回 HTTP 502（IP 级限流/冷却，非验证码）"))
check("拆验证页类：前缀剥离后归 captcha",
      sp(BLOCK_TAG_CAP + "1688：出现验证/拦截页：滑块验证")[0] == "captcha")
check("旧适配器整句文本（不是「站名：原因」结构）不误拆成站名",
      sp("检测到滑块验证，请手动完成") == ("captcha", "", "检测到滑块验证，请手动完成"))
check("无前缀的文本一律按可人工过处理（宁可多叫一次人，也不让人对着没滑块的页干找）",
      sp("出现验证/拦截页")[0] == "captcha")
src_blk = inspect.getsource(W._on_blocked)
check("界面按类型分派两套文案（不再一个模板走天下）",
      'kind == "ip"' in src_blk and "_show_block_alert(kind, name, detail)" in src_blk)
check("IP 类不还原/前置 Chrome 窗口（没开页面，还原了也只是让人对着空白找）",
      'kind != "ip"' in src_blk)
src_alert = inspect.getsource(W._show_block_alert)
# 只看两套 setText 实文本（不能拿整个函数体包，docstring 里引用旧谎话作对比说明）
ip_text = src_alert.split("if ip_mode:", 1)[1].split("else:", 1)[0]
cap_text = src_alert.split("else:", 1)[1]
check("IP 类弹窗不再出现「已在 Chrome 里打开对应页面」那句谎话",
      "已在 Chrome 里打开对应页面" not in ip_text)
check("IP 类弹窗直说「不是验证码」并给看得懂的两步做法",
      all(k in ip_text for k in ("不是验证码", "手机热点", "马上继续", "不会丢")))
check("IP 类弹窗正文不堆开发者术语（没技术背景的人也读得懂）",
      not any(k in ip_text for k in ("IP 级", "限流", "退避", "熔断", "看门狗")))
check("IP 类弹窗不再把用户支使去 Chrome 找入口（只给可选的自查）",
      "请切到那个 Chrome 窗口" not in ip_text)
check("验证页弹窗讲清「第1步拖滑块、第2步什么都不用点」",
      all(k in cap_text for k in ("拖完点完", "第 1 步", "第 2 步", "接着往下采")))
check("验证页弹窗也提示「没有滑块就是被拦，换网络点马上继续」",
      "根本没有滑块" in cap_text and "马上继续" in cap_text)
check("弹窗提供「我换好网络了，马上继续」按钮（换完 IP 一键立刻重测，不用干等）",
      "我换好网络了，马上继续" in src_alert and hasattr(W, "_on_block_retry_now"))
check("弹窗提供「打开网站让我看一眼」按钮（想看才开页）",
      "打开网站让我看一眼" in src_alert and hasattr(W, "_on_block_selfcheck"))
check("两个按钮共用一个后台执行器（主线程里拉浏览器会整窗「未响应」）",
      "threading.Thread" in inspect.getsource(W._run_block_action) and
      "_run_block_action" in inspect.getsource(W._on_block_selfcheck) and
      "_run_block_action" in inspect.getsource(W._on_block_retry_now))
check("两套弹窗正文都不出现开发者术语（界面层用大白话）",
      not any(k in ip_text + cap_text for k in ("退避", "熔断", "看门狗", "轮询")))
check("分型前缀由采集端集中定义，界面与爬虫共用同一常量（不会各自漂移）",
      gui.BLOCK_TAG_IP is cdp_base.BLOCK_TAG_IP and gui.BLOCK_TAG_CAP is cdp_base.BLOCK_TAG_CAP)

# ══════════════════════════════════════════════════════════
# 7) open_site_check：用户主动点才借标签开页
# ══════════════════════════════════════════════════════════
calls2 = []


class FakeCrawler:
    PLATFORM_NAME = "八方资源网"
    BASE_URL = "https://www.b2b168.com/"
    _block_probe_url = "https://b2b168.com/x"
    _blocked = True
    retried = 0

    def open_verify_page(self, url=""):
        calls2.append(url)
        return True

    def retry_now(self):
        self.retried += 1
        return True


class NoTabCrawler:
    """旧式适配器：没有借标签开页的能力（只能提示用户自己去浏览器里看）。"""
    PLATFORM_NAME = "某旧站"
    BASE_URL = "https://old.example.com/"
    _block_probe_url = "https://old.example.com/list"
    _blocked = True


class NoPageCrawler(FakeCrawler):
    PLATFORM_NAME = "无地址站"
    _block_probe_url = ""
    BASE_URL = ""


class FakeSessionMini:
    """只装 ParallelSession 的选站/开页/重测三个方法（不建真采集器、不联网）。"""
    port = 9255
    _pick_site = ParallelSession._pick_site

    def __init__(self, crawlers):
        self.crawlers = crawlers
        self.brought = None

    def _log(self, m):
        pass

    def set_browser_background(self, on):
        self.brought = on


sess = FakeSessionMini({"b2b168": FakeCrawler()})
ok1, msg1 = ParallelSession.open_site_check(sess, "八方资源网")
check(f"按站名找到被拦站并打开被拦那一页（{msg1}）",
      ok1 is True and calls2 == ["https://b2b168.com/x"])
check("打开后把窗口拉到前台（用户点了就要看得见）", sess.brought is False)

calls2.clear()
sess2 = FakeSessionMini({"b2b168": FakeCrawler()})
ok2, msg2 = ParallelSession.open_site_check(sess2, "")
check("站名缺失（旧适配器文本）时按「当前被拦的站」兜底", ok2 is True and calls2)

sess3 = FakeSessionMini({"old": NoTabCrawler()})
ok3, msg3 = ParallelSession.open_site_check(sess3, "某旧站")
check("站点不具备开页能力时给明确提示而不是崩", ok3 is False and "打不开它的网页" in msg3)

sess4 = FakeSessionMini({"b2b168": FakeCrawler()})
ok4, msg4 = ParallelSession.open_site_check(sess4, "不存在的站")
check("站点没在采集时不假装成功", ok4 is False and "没有在采集" in msg4)

sess5 = FakeSessionMini({"x": NoPageCrawler()})
ok5, msg5 = ParallelSession.open_site_check(sess5, "无地址站")
check("站点没有任何可打开地址时如实返回失败", ok5 is False and "没有可打开的地址" in msg5)

sess6 = FakeSessionMini({"b2b168": type("X", (FakeCrawler,), {"open_verify_page": lambda self, url="": False})()})
ok6, msg6 = ParallelSession.open_site_check(sess6, "八方资源网")
check("共享浏览器不可用时如实说失败（旧实现根本开不了页却谎称已打开）",
      ok6 is False and "共享浏览器" in msg6)

fc = FakeCrawler()
sess7 = FakeSessionMini({"b2b168": fc})
ok7, msg7 = ParallelSession.retry_site_now(sess7, "八方资源网")
check(f"「马上继续」按站名找到对应采集器并叫醒看门狗（{msg7}）",
      ok7 is True and fc.retried == 1)
ok7b, msg7b = ParallelSession.retry_site_now(FakeSessionMini({"old": NoTabCrawler()}), "某旧站")
check("旧适配器没有这个能力时如实提示，不假装重测了",
      ok7b is False and "不支持" in msg7b)
ok7c, msg7c = ParallelSession.retry_site_now(sess7, "不存在的站")
check("站点没在采集时不假装成功（马上继续）", ok7c is False and "没有在采集" in msg7c)

# ══════════════════════════════════════════════════════════
# 8) 采集间隔安全下限：用户把间隔拉到最小也不能重现「200 多家后被限 IP」
# ══════════════════════════════════════════════════════════
ps = ParallelSession()
ps.set_enabled(["b2b168", "mic"])
log_lines = []
ps._log = lambda m="": log_lines.append(str(m))
ps.configure(delay_between=2.0)
check(f"八方被钳回安全下限 4s（界面设 2s 也不放行）（{ps.crawlers['b2b168'].delay_between}）",
      ps.crawlers["b2b168"].delay_between == 4.0 and ps.crawlers["b2b168"]._current_delay == 4.0)
check("没有下限的站照用户设定执行（不无端拖慢）", ps.crawlers["mic"].delay_between == 2.0)
check("钳制时写日志说明原因（用户不会以为界面设了却「没生效」）",
      any("安全下限" in m and "八方资源网" in m for m in log_lines))
log_lines.clear()
ps.configure(delay_between=10.0)
check("界面设得比下限大时不干预", ps.crawlers["b2b168"].delay_between == 10.0)
check("未被钳制时不打扰日志", log_lines == [])

print(f"\n全部 {ok} 项断言通过 ✅")
