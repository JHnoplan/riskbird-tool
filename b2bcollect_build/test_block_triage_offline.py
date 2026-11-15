# 离线自测：拦截分诊（IP 限流 vs 可人工过验证）—— 2026-10-29 八方资源网客户反馈
# 客户原话：「八方大概采到 200 多个就被防采了，怎么点都被限制进入」「它有时候没跳出
# 八方资源网这个网页，只能在你设计的这个软件点确认」；而 1688 出验证码会正常开页。
# 四条根因与本测试对应：
#   ① 弹窗文案说谎：八方 BLOCK_OPEN_BROWSER=False 根本不开 Chrome，界面却写「已打开」
#      → 断言 reason 带分型前缀、界面按前缀出两套文案、IP 类不声称已开页；
#   ② IP 限流被当“等人工”：断言 IP 类绝不拉浏览器、熔断阈值降到 3；
#   ③ 探针自己打断冷却：断言探测间隔从 45s 起按被拦次数指数退避、听 Retry-After；
#   ④ 记录级节流导致 3~5 倍超速：断言八方开了请求级节流且每个请求前都对齐节奏。
# 第 9 节另钉一件事：1688 的「未登录」归因 —— 只有「确凿未登录」才说话，判不出就闭嘴，
# 而且开局（crawl 开头）就要说在前面，不能等第一家撞验证页才归因（2026-11-15 实机：
# 未登录会话在联系方式页反复吃验证，老提示只说「过验证」，用户照着反复过码白等）。
# 第 10 节钉 1688 的收尾：Selenium 4.51 的 quit() 只删会话、不收 chromedriver 子进程，
# 独立的 Ali1688Tool 包里又没有 chrome_driver 那套口径，必须就地补收（幂等、吞异常）。
# 第 12 节钉「连不上」这一族账（2026-11-14 实机：顺企网 80/443 全部连接超时，界面却按「仍在
# 防采/限流状态」说话，用户照着等两小时 IP 冷却）：连接/读数两个数分开、连接类连续两次拉黑主机、
# 恢复探测与对照实测这两条支路也不许把失败原因吞掉（12m 不许裸截断异常文本、12n 不许静默 except、
# NET_FAIL_TAG 只准基类那几把把手拼一次）。
# 全程不联网、不启 Chrome：响应注入假会话，cdp_base 的 time 换成可推进的假时钟。
import sys
import inspect
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cdp_base
from cdp_base import CdpSiteCrawler, BLOCK_TAG_IP, BLOCK_TAG_CAP
from b2b168_crawler import B2b168Crawler
from parallel_session import ParallelSession

ok = 0


def check(name, cond, extra=""):
    global ok
    assert cond, f"FAIL: {name}｜{extra}" if extra else f"FAIL: {name}"
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
        # 真实 ParallelSession 必有 channels（三路名单通道）：_pick_site 现在把
        # 风鸟/爱企查也纳入「打开网站让我看一眼 / 马上重测」的可选对象，
        # 假会话必须带上这个字段，否则接口漂移只能在真机上才暴露。
        self.channels = {}
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

# ══════════════════════════════════════════════════════════
# 9) 1688 撞验证页时的归因：确凿未登录才说「先去登录」，判不出就闭嘴
# ══════════════════════════════════════════════════════════
# 2026-11-15 实机：1688 通道 0.6 秒附加成功、列表渠道正常，第一次去取联系方式页就撞
# 「验证码拦截」（落在 shop*.1688.com/page/contactinfo.html）。这台共享 Chrome 里
# 爱企查/企知道/风鸟都能查到登录 cookie，1688 只有游客埋点 —— 也就是没登录。
# 老提示只会说「请手动过验证」，用户照着反复过码、下一家照样被拦，工具看着像坏掉。
from pathlib import Path as _Path

_REPO = _Path(__file__).resolve().parents[1]
for _d in ("riskbird_build", "ali1688_build"):
    sys.path.insert(0, str(_REPO / _d))
import ali1688_crawler as _a1688                                  # noqa: E402

_GUEST_1688 = [{"name": n} for n in ("cna", "isg", "_m_h5_tk", "_m_h5_tk_enc",
                                     "tfstk", "JSESSIONID", "sid", "pid")]
_LOGGED_1688 = _GUEST_1688 + [{"name": "cookie2"}, {"name": "lgc"}]

check("1688 只有游客埋点 → 判「确认未登录」", _a1688.judge_login_cookie(_GUEST_1688) is False)
check("1688 有会话身份 cookie → 判「已登录」", _a1688.judge_login_cookie(_LOGGED_1688) is True)
check("cookie 取空/形状不对 → 判不出（独立一档，不许塌成未登录）",
      all(_a1688.judge_login_cookie(x) is None
          for x in ([], None, "cookie2", [{"nope": 1}], [None])))


class _DrvStub:
    def __init__(self, cookies=None, boom=False):
        self._cookies, self._boom = cookies, boom

    def get_cookies(self):
        if self._boom:
            raise RuntimeError("invalid session id")
        return self._cookies


class _CrawlerStub:
    """只借真判据与真话术，不建会话（离线套件不许起浏览器）。"""
    _login_state_by_cookie = _a1688.Ali1688Crawler._login_state_by_cookie
    _unblock_login_hint = _a1688.Ali1688Crawler._unblock_login_hint

    def __init__(self, driver):
        self.driver = driver


check("未登录 + 被拦 → 多说一句「先登录」（这正是过码反复白等的根因）",
      "未登录" in _CrawlerStub(_DrvStub(_GUEST_1688))._unblock_login_hint())
hint_ok = _CrawlerStub(_DrvStub(_LOGGED_1688))._unblock_login_hint()
check("已登录时不误导用户去登录", hint_ok == "")
for _tag, _drv in (("cookie 拿不到", _DrvStub(boom=True)), ("cookie 为空", _DrvStub([]))):
    _silence = _CrawlerStub(_drv)._unblock_login_hint()
    check(f"{_tag}时一个字都不多说（没证据就不猜方向）", _silence == "")
_hint = _CrawlerStub(_DrvStub(_GUEST_1688))._unblock_login_hint()
check("话术与实际行为一致：不声称已自动开页，只说清在哪登录、登录后不用重启工具",
      "Chrome" in _hint and "登录" in _hint and "不用重启工具" in _hint and "已打开" not in _hint)


class _Pre1688:
    """只借真 preflight：记录界面话术与事件，离线不建会话、不发请求。"""
    _login_state_by_cookie = _a1688.Ali1688Crawler._login_state_by_cookie
    _preflight_login_notice = _a1688.Ali1688Crawler._preflight_login_notice

    def __init__(self, driver):
        self.driver, self.logs, self.emits = driver, [], []

    def _log(self, m):
        self.logs.append(m)

    def _emit(self, name, payload=""):
        self.emits.append((name, payload))


_p_guest = _Pre1688(_DrvStub(_GUEST_1688))
check("未登录 → 开局就把话说在前面（不等第一家撞验证页才归因）",
      _p_guest._preflight_login_notice() is True
      and len(_p_guest.logs) == 1 and "未登录" in _p_guest.logs[0])
check("开局提示同时发 login_required_signal：界面上那条 🔐 提示与日志同一件事",
      _p_guest.emits == [("login_required_signal", _p_guest.emits[0][1])]
      and "登录" in _p_guest.emits[0][1])
check("每轮只提醒一次：pre-flight 在 crawl 开头调用一次，不掺进关键词循环里",
      inspect.getsource(_a1688.Ali1688Crawler.crawl).count("_preflight_login_notice()") == 1)
for _tag, _drv in (("已登录", _DrvStub(_LOGGED_1688)),
                   ("cookie 拿不到", _DrvStub(boom=True)),
                   ("cookie 为空", _DrvStub([]))):
    _p = _Pre1688(_drv)
    check(f"{_tag}时开局一个字都不多说、也不发登录事件",
          _p._preflight_login_notice() is False and not _p.logs and not _p.emits)
check("开局话术不拦路：说清「公司名照常采」，不把未登录当成停止采集的理由",
      "照常采" in _p_guest.logs[0] and "不用重新开始采集" in _p_guest.logs[0])

print("⑨ 1688 未登录归因：开局说在前面 + 撞验证页补一句，判不出则全部闭嘴 ✓")

# ── 10. 独立 Ali1688Tool 包也要收掉 chromedriver 子进程 ────────────────────
# 同一件事的第二处口径缺口：b2bcollect 包里 1688 的会话出自 chrome_driver.create()
# （quit 当场被绑成「结束会话 + 收子进程」），但 Ali1688Tool 是单目录打包，运行期才
# append 的 sys.path 在包里解析不到那份模块，_attach 只能退回本文件自带的候选链，
# 建会话用裸 webdriver.Chrome —— 没有地方绑回收。Selenium 4.51 的 quit() 只删会话，
# 于是那包的每一次收工都留一个进程（与本轮实机取证的孤儿 chromedriver 同一根因）。
class _Svc10:
    def __init__(self, boom=False):
        self.stops, self.boom = 0, boom

    def stop(self):
        if self.boom:
            raise RuntimeError("process lookup failed")
        self.stops += 1


class _Drv10:
    """独立包里的会话形状：quit 没被绑过（裸 webdriver.Chrome 建的），只管会话本身。"""

    def __init__(self, svc=None, quit_err=""):
        self.service, self.quits, self._quit_err = svc, 0, quit_err

    def quit(self):
        self.quits += 1
        if self._quit_err:
            raise RuntimeError(self._quit_err)


class _Close10:
    """只借真 close_browser：套一个假会话，离线不建任何浏览器。"""
    close_browser = _a1688.Ali1688Crawler.close_browser

    def __init__(self, driver):
        self.driver = driver

    def _log(self, m):
        pass


def _run_close(driver, cd_none=True):
    """按「包里有没有 chrome_driver」两种形态各跑一次收尾，返回 (会话数, 回收数)。"""
    _cd_orig = _a1688._chrome_driver
    _a1688._chrome_driver = None if cd_none else _cd_orig
    try:
        _Close10(driver).close_browser()
    finally:
        _a1688._chrome_driver = _cd_orig


class _Cd10:
    """有 chrome_driver 时的形态：口径在那边，本文件只许把会话交出去、不许自己再收。"""

    def __init__(self):
        self.destroys = []

    def destroy(self, driver, log=None):
        self.destroys.append(driver)


def _run_close(driver, cd):
    """在「包里有 / 没有 chrome_driver」两种形态下各跑一次收尾，现场按原值还回去。"""
    box = _Close10(driver)
    _orig = _a1688._chrome_driver
    _a1688._chrome_driver = cd
    try:
        box.close_browser()
    finally:
        _a1688._chrome_driver = _orig
    return box


# ① 独立 Ali1688Tool 包（_chrome_driver is None）：quit 只管会话，子进程要显式补收
_solo = _Drv10(_Svc10())
_box = _run_close(_solo, None)
check("独立包收尾：会话结束与子进程补收各发生一次（老写法只有关闭会话这一下）",
      _solo.quits == 1 and _solo.service.stops == 1,
      f"quits={_solo.quits} stops={_solo.service.stops}")
check("独立包收尾：driver 引用落地为 None（留着引用下一轮就拿半死会话判活）",
      _box.driver is None)
# 会话早就没了的时候 quit 必抛：老写法 try/except 一吞，进程就一直留着
_svc_err = _Svc10()
_drv_err = _Drv10(_svc_err, quit_err="invalid session id")
_run_close(_drv_err, None)
check("独立包收尾：quit() 抛异常时子进程照样收到（自愈重启正是这一档）",
      _drv_err.quits == 1 and _svc_err.stops == 1)

# ② b2bcollect 包：同一句收尾必须交回 chrome_driver 的统一口径，别在本地长出第二套
_cd10 = _Cd10()
_shared = _Drv10(_Svc10())
_run_close(_shared, _cd10)
check("有 chrome_driver 时：只走 destroy 那一套，本地绝不另收一遍（两处判据迟早分叉）",
      _cd10.destroys == [_shared] and _shared.quits == 0 and _shared.service.stops == 0,
      f"destroys={len(_cd10.destroys)} quits={_shared.quits} stops={_shared.service.stops}")

# ③ 回收原语本身的三条硬要求
check("reap：driver 为 None / 没有 service 属性 → 什么都不做，也不抛",
      _a1688.reap_driver_service(None) is False
      and _a1688.reap_driver_service(object()) is False)
_s2 = _Svc10()
check("reap 幂等：同一份 Service 只收一次（destroy 与 atexit 两层会各叫一遍）",
      _a1688.reap_driver_service(_Drv10(_s2)) is True
      and _a1688.reap_driver_service(_Drv10(_s2)) is False and _s2.stops == 1)
_s3 = _Svc10(boom=True)
check("reap 吞异常：停不掉也只是少收一次，绝不把收工路径带崩",
      _a1688.reap_driver_service(_Drv10(_s3)) is False
      and _a1688.reap_driver_service(_Drv10(_s3)) is False)

print("⑩ 独立 1688 包收尾：quit() 之外补收 chromedriver 子进程，幂等且吞异常 ✓")

# ── ⑪ 请求层失败分型：类别写在最前面，才活得过渲染层每一档截断 ─────────────────
# 由来（2026-11-15 实机体检）：顺企网 www.11467.com 三个 IP、80/443 端口 TCP 全连不上
# （对照组同机 b2b168／世界工厂网／中国制造网全 200，另一条独立网络路径打同一域名也超时），
# 结论是**站点侧不可达**。可工具当时说的是「顺企网采不到」，因为界面与体检器拿到的最后一次
# 失败原因长这样：`HTTPSConnectionPool(host='b2b.11467.com', port=443): Max ret`——
# 决定方向的 `Caused by ConnectTimeoutError(… timed out. (connect timeout=8))` 被请求层那句
# `str(e).splitlines()[0][:80]` 从头部截掉了，再叠加记账 [:160] 与渲染 [:60]/[:70]/[:40]，
# 一句话里既没有类别也没有方向。这一节钉的就是「分型器分得对」+「旧写法确实分不出来」。
# 下面每一段文本都是本机 requests 真抛出来的 str(e)（现场抓的，不是照 urllib3 文档编的）：
# 编的形状只能证明判据自洽，证不了它认得现实。
_shape = cdp_base.request_error_shape
# (期望类别, 真实异常类名, 真实消息, 必须留下的关键证据)
_ERR_SAMPLES = (
    ("连不上·无应答", "ConnectTimeout",
     "HTTPSConnectionPool(host='b2b.11467.com', port=443): Max retries exceeded with url: / "
     "(Caused by ConnectTimeoutError(<HTTPSConnection(host='b2b.11467.com', port=443) "
     "at 0x108e3b750>, 'Connection to b2b.11467.com timed out. (connect timeout=8)'))",
     "connect timeout=8"),
    ("DNS解析不了", "ConnectionError",
     "HTTPConnectionPool(host='no-such-host-zzqq.invalid', port=80): Max retries exceeded with url: / "
     "(Caused by NameResolutionError(\"HTTPConnection(host='no-such-host-zzqq.invalid', port=80): "
     "Failed to resolve 'no-such-host-zzqq.invalid' ([Errno 8] nodename nor servname provided, "
     "or not known)\"))",
     "Errno 8"),
    ("连不上·端口被拒", "ConnectionError",
     "HTTPConnectionPool(host='127.0.0.1', port=1): Max retries exceeded with url: / "
     "(Caused by NewConnectionError(\"HTTPConnection(host='127.0.0.1', port=1): Failed to establish "
     "a new connection: [Errno 61] Connection refused\"))",
     "Connection refused"),
    ("读响应超时", "ReadTimeout",
     "HTTPConnectionPool(host='127.0.0.1', port=49923): Read timed out. (read timeout=2)",
     "Read timed out"),
    ("代理不通", "ProxyError",
     "HTTPConnectionPool(host='127.0.0.1', port=1): Max retries exceeded with url: http://example.com/ "
     "(Caused by ProxyError('Unable to connect to proxy', NewConnectionError(\"HTTPConnection"
     "(host='127.0.0.1', port=1): Failed to establish a new connection: [Errno 61] Connection refused\")))",
     "Unable to connect to proxy"),
    ("TLS握手失败", "SSLError",
     "HTTPSConnectionPool(host='self-signed.badssl.com', port=443): Max retries exceeded with url: / "
     "(Caused by SSLError(SSLCertVerificationError(1, '[SSL: CERTIFICATE_VERIFY_FAILED] certificate "
     "verify failed: self-signed certificate (_ssl.c:1032)')))",
     "CERTIFICATE_VERIFY_FAILED"),
    ("连不上·被重置", "ConnectionError",
     "('Connection aborted.', ConnectionResetError(54, 'Connection reset by peer'))",
     "Connection reset by peer"),
    ("URL不合法(代码)", "MissingSchema",
     "Invalid URL 'example.com/': No scheme supplied. Perhaps you meant https://example.com/?",
     "No scheme supplied"),
    ("返回不可解析(代码)", "JSONDecodeError", "Expecting value: line 1 column 1 (char 0)",
     "Expecting value"),
    # 类名兜底那一档（2026-11-14 实机跨国采购网引导失败就是这一形状：只剩池头与
    # `Max retries exceeded`，`Caused by` 那半句是空的）。按消息关键词认不出来，可 requests 的
    # ConnectionError 类名本身就写着「连接阶段断了」——让它掉进【未分型】，这一台既不进熔断账、
    # 探测也不挂网络前缀，界面就会说成「站点在拦」。
    ("连不上·链路中断", "ConnectionError",
     "HTTPSConnectionPool(host='www.globalimporter.net', port=443): Max retries exceeded "
     "with url: /",
     "Max retries exceeded"),
)


def _as_err(cls_name, text):
    """造一个「类名与真异常一致、消息与真异常一致」的异常（不必 import 那九个 requests 类）。"""
    return type(cls_name, (Exception,), {})(text)


_shaped = {}
for _kind, _cls, _text, _proof in _ERR_SAMPLES:
    _shaped[_kind] = _shape(_as_err(_cls, _text))
    check(f"11a 分型正确：{_cls} → 【{_kind}】",
          _shaped[_kind].startswith(f"【{_kind}】"), _shaped[_kind])
    # 光有类别等于把「为什么」又丢了：现场证据必须在句子里（这一条挡的是过度清洗）
    check(f"11b 关键证据留在句子里：{_kind}", _proof in _shaped[_kind], _shaped[_kind])

# 11c 反向对照：旧写法在同一批真实文本上**一个类别都给不出**。不验这一条，上面 18 项断言
#     可能只是装饰——判据只有在「坏了会响」时才算存在。
_old_blind = [k for k, _c, _t, _p in _ERR_SAMPLES
              if "【" not in str(_as_err(_c, _t)).splitlines()[0][:80]]
check("11c 反向对照：旧写法 str(e).splitlines()[0][:80] 在这九份真文本上全blind",
      len(_old_blind) == len(_ERR_SAMPLES),
      f"只有 {len(_old_blind)}/{len(_ERR_SAMPLES)} 份认不出类别")

# 11d 性质判据：渲染层用的是**截断之后**的字符串（channel_health [:40]、收尾 [:60]、
#     体检器与 GUI [:70]），所以「类别在头部」要按这些真实数字逐档验，而不是只验返回值。
#     以后有人把类别挪到句中或把 host 挪到最前，这一条会先响。
_caps = (40, 60, 70)
_too_far = [f"{k}@{n}→{v[:n]!r}" for k, v in _shaped.items() for n in _caps
            if "】" not in v[:n]]
check(f"11d 每一档截断（{'、'.join(str(n) for n in _caps)}）之后都还读得出类别",
      not _too_far, "；".join(_too_far[:3]))
_no_host = [f"{k}→{_shaped[k][:44]!r}" for k, _c, _t, _p in _ERR_SAMPLES
            if "host='" in _t and ":" not in _shaped[k].split("｜")[0]]
check("11e 原文带池头时 host:port 紧跟在类别后面（截断后仍知道是哪个域名败的）",
      not _no_host, "；".join(_no_host))

# 11f 表序＝优先级：被拒／无应答／代理三档共用一句 `Failed to establish a new connection`，
#     只有 errno 与措辞分得开。顺序写错就把「端口没人监听」说成「网络不通」，处方完全不同。
_shared = [_t for _k, _c, _t, _p in _ERR_SAMPLES if "Failed to establish a new connection" in _t]
check("11f 共用同一句话的三档各自归位（这一条钉住判据表的顺序）",
      len(_shared) >= 2
      and _shaped["连不上·端口被拒"] != _shaped["连不上·无应答"]
      and _shape(_as_err("ConnectionError", _shared[0])).startswith("【连不上·端口被拒】")
      and _shape(_as_err("ProxyError", _shared[-1])).startswith("【代理不通】"),
      f"共用句 {len(_shared)} 份｜被拒={_shaped['连不上·端口被拒'][:26]}"
      f"｜无应答={_shaped['连不上·无应答'][:26]}")

# 11g 幂等：各站 except 分支先整形一次（为了打日志），翻页层随后把**同一句**交给
#     `_note_request_error` 记账；两次必须同一句话，否则日志与界面会各说一种。
_once = _shape(_as_err("ConnectTimeout", _ERR_SAMPLES[0][2]))
check("11g 幂等：整过形的句子再过一次分型器不变样",
      _shape(_once) == _once and _shape(_once[:160]) == _once[:160], _shape(_once))

# 11h 空手而归不许冒充产出结论：None/空串也必须有一句可念的话，而任何一档都**不许**出现
#     「采不到」三个字——那是产出结论，本轮假红恰恰红在这三个字被链路故障借用。
check("11h 空输入也有话可说，且十分型里没有任何一句说「采不到」",
      _shape("") == "未知请求失败" and _shape(None) == "未知请求失败"
      and _shape("   ") == "未知请求失败"
      and all("采不到" not in v for v in _shaped.values()),
      repr(_shape("")) + " / " + repr(_shape(None)))

# 11i HTTP 状态档：各站 `_fetch` 的失败原因本来就是 `f"HTTP {code}"` 这一句裸话，而
#     429/403/5xx 恰恰是「等一等就好」与「改代码」的分界线。附带验「（防采插页）」这种
#     站点自己补的说明不会被状态码档吃掉。
check("11i HTTP 状态自带方向（429 限流／403 风控／5xx 站点故障／404 这一页没了）",
      _shape("HTTP 429（防采插页）").startswith("【HTTP 429·限流】")
      and "防采插页" in _shape("HTTP 429（防采插页）")
      and _shape("HTTP 403").startswith("【HTTP 403·风控/需验证】")
      and _shape("HTTP 502").startswith("【HTTP 502·站点故障】")
      and _shape("HTTP 404").startswith("【HTTP 404·这一页没了】"),
      _shape("HTTP 429（防采插页）") + "｜" + _shape("HTTP 502"))

# 11j Chrome/Selenium 那一侧的 net::ERR_*：1688 与各名单通道取页失败都是这个形状，共用同
#     一张表才不会「同一件网络故障，两个平台说两种话」。断网与解析失败必须分档（对照组红不
#     红是两回事），混成一档会让人去改 hosts 而该做的是接网线。
check("11j Chrome net::ERR_* 归同一张表，且「本机断网」不与「DNS解析不了」混档",
      _shape("Message: unknown error: net::ERR_NAME_NOT_RESOLVED").startswith("【DNS解析不了】")
      and _shape("net::ERR_CONNECTION_TIMED_OUT").startswith("【连不上·无应答】")
      and _shape("net::ERR_CONNECTION_REFUSED").startswith("【连不上·端口被拒】")
      and _shape("net::ERR_INTERNET_DISCONNECTED").startswith("【本机断网】"),
      _shape("net::ERR_INTERNET_DISCONNECTED"))

# 11k 认不出形状时**必须留下痕迹**：【未分型】是这张表的缺口清单（本轮就有三档是这样长出来的），
#     静默返回空串或返回原文都会让缺口谁也看不见。
check("11k 没分上型的带【未分型】并保留原文（缺口可见才补得上）",
      _shape("某种没见过的新错误").startswith("【未分型】")
      and "某种没见过的新错误" in _shape("某种没见过的新错误"),
      _shape("某种没见过的新错误"))

# 11l 两份镜像同答：1688 独立包自己实现了一份，同一条异常两边必须给同一句话
_a16_shape = _a1688.request_error_shape
_diff = [(k, _shaped[k], _a16_shape(_as_err(c, t))) for k, c, t, _p in _ERR_SAMPLES
         if _a16_shape(_as_err(c, t)) != _shaped[k]]
check("11l 1688 那份镜像逐字同答（分叉＝同一件事两套结论）", not _diff, str(_diff[:1]))
_src_cdp11 = (Path(__file__).resolve().parent / "cdp_base.py").read_text(encoding="utf-8")
_src_a1611 = (Path(__file__).resolve().parents[1] / "ali1688_build" / "ali1688_crawler.py"
              ).read_text(encoding="utf-8")


def _between(src, start, end):
    """源文件里 [起始标记, 结束标记] 这一段（含结束标记本身）；任一边缺标记就返回哨兵让它响。"""
    if start not in src or end not in src:
        return f"（缺标记：start={start in src} end={end in src}）"
    i = src.index(start)
    j = src.index(end, i) + len(end)
    return src[i:j]


_SHAPE_TAIL = '    return f"{head}｜{detail}" if detail else head\n'
check("11m 分型器两份**源码同字节**（只比答案比不出注释里的由来，也拦不住悄悄改一边）",
      _between(_src_cdp11, "def request_error_shape(err, cap=160):", _SHAPE_TAIL)
      == _between(_src_a1611, "def request_error_shape(err, cap=160):", _SHAPE_TAIL),
      "两份 request_error_shape 函数体有差异")
check("11n 判据表两份同字节（新加一档只加一边，就会有一个平台一直【未分型】）",
      _between(_src_cdp11, "_REQUEST_ERROR_KINDS = (", "_HTTP_STATUS_KINDS")
      == _between(_src_a1611, "_REQUEST_ERROR_KINDS = (", "_HTTP_STATUS_KINDS"),
      "_REQUEST_ERROR_KINDS 两份不一致")
check("11o _goto_fail_reason 两份同字节（翻页层那句「导航失败」的取用口径两边共用）",
      _between(_src_cdp11, '    def _goto_fail_reason(self, label="导航失败"):',
               '        return f"{label}（{why}）" if why else f"{label}（具体原因见紧邻上一行）"')
      == _between(_src_a1611, '    def _goto_fail_reason(self, label="导航失败"):',
                  '        return f"{label}（{why}）" if why else f"{label}（具体原因见紧邻上一行）"'),
      "_goto_fail_reason 两份不一致")

# 11p 结构判据（同类查全）：**请求层不许再自己从头部截断**。只测函数挡不住下一个新站点照抄
#     一句 `str(e).splitlines()[0][:80]`——本轮九站就是各抄一份才凑齐这个缺陷的。
_head_cut = sorted(p.name for p in Path(__file__).resolve().parent.glob("*_crawler.py")
                   if "str(e).splitlines()[0][" in p.read_text(encoding="utf-8"))
check("11p 没有一份站点采集器还在自己 str(e).splitlines()[0][:N]（截断只交给分型器）",
      not _head_cut, "、".join(_head_cut))
# 整形了却不记账 = 界面照样问不到（本轮 gys 就是这样：日志说得出、账上是空的）
_unrecorded = sorted(p.name for p in Path(__file__).resolve().parent.glob("*_crawler.py")
                     if "_shape_request_error(" in p.read_text(encoding="utf-8")
                     and "_note_request_error(" not in p.read_text(encoding="utf-8"))
check("11q 整形过的站点一定也记了账（只打日志的失败在界面上等于没发生）",
      not _unrecorded, "、".join(_unrecorded))
print("⑪ 请求层失败分型：类别在头部， [:40]/[:60]/[:70] 三档截断之后仍读得出方向 ✓")

# ══════════════════════════════════════════════════════════
# ⑫ 主机级熔断 + 连接/读数超时拆分：连不上的主机不该再吃整轮预算
# ══════════════════════════════════════════════════════════
# 由来（本轮实机体检抓到两条同族缺陷，都不在解析代码里）：
#   1) 裸 HTTP 把 `timeout=25` 同时当**连接**超时和**读数**超时。同机实测：
#      b2b.11467.com(59.36.210.149) 与 www.11467.com(58.61.157.108) 的 80/443 全部
#      TCP 连接超时、ICMP 100% 丢包，而对照组（同机 b2b168／世界工厂网／中国制造网／
#      爱采购）全 200 —— 站点侧不可达。旧写法下顺企网每个关键词的第一页要付
#      4×25s+退避≈107s，两个关键词 3 分钟全喂给超时，能出货的路子反而被饿死。
#   2) 更糟的是**每一页都重新付一遍**：连不上是主机的属性，不是这一页的属性，
#      重试、换关键词、借浏览器标签都改变不了它。大厂口径是主机级隔离（dead-host
#      quarantine）＋冷却后再给一次机会，而不是「本轮无限重试」。
# 连接档取 10s 的实测依据：正常站 0.03~0.4s，偶发 SYN 重传 1~7s（爱采购抽到过一次
# 「第一次 5s 没连上、第二次 0.03s」），10s 既容得下重传又能在真连不上时快速失败。
import ast as _ast
import re
import tempfile as _tf
import importlib.util as _ilu
from pathlib import Path as _P12

HERE = _P12(__file__).resolve().parent
import website_contact                                        # noqa: E402


def _load_live_check():
    """把实机体检器当普通模块载进来（只取它的判据函数，绝不跑 main）。

    为什么要跨目录载：熔断这笔账最终是在体检报告里给人看的，账记对了、报告不读，
    用户看到的还是一句「采不到」。载成模块就能让 ⑫ 节同时钉住两端。
    """
    path = _REPO / "debug" / "platform_live_check.py"
    if not path.exists():
        raise AssertionError(f"FAIL: 体检器不在预期位置（判据失去对拍对象）：{path}")
    spec = _ilu.spec_from_file_location("_live_check_for_test", path)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_LIVE_SRC = (_REPO / "debug" / "platform_live_check.py").read_text(encoding="utf-8")


def _def_src(src, name, cls_name=None):
    """某个函数/方法的源码段（可按类限定）。找不到就返回哨兵，让「判据没落地」自己响。"""
    tree = _ast.parse(src)
    scopes = ([n for n in _ast.walk(tree)
               if isinstance(n, _ast.ClassDef) and n.name == cls_name]
              if cls_name else [tree])
    for sc in scopes:
        for fn in (n for n in _ast.walk(sc)
                   if isinstance(n, _ast.FunctionDef) and n.name == name):
            seg = _ast.get_source_segment(src, fn)
            if seg:
                return seg
    return f"（找不到 {cls_name or '<模块>'}.{name}）"


# 12a 取值形状：拆成二元组，读数沿用调用方给的数（不许把慢页面等不到的那一份削掉）
check("12a http_timeout(N) → (10, N)：连接与读数是两个数",
      cdp_base.http_timeout(25) == (10, 25) and cdp_base.http_timeout(20) == (10, 20)
      and cdp_base.http_timeout() == (10, 25) and cdp_base.http_timeout(0) == (10, 25),
      repr(cdp_base.http_timeout(25)))
check("12a- 连接档两处必须同一个数（cdp_base 与官网回采各定各的＝又一套口径）",
      cdp_base.HTTP_CONNECT_TIMEOUT == website_contact.CONNECT_TIMEOUT == 10,
      f"cdp_base={cdp_base.HTTP_CONNECT_TIMEOUT} website_contact={website_contact.CONNECT_TIMEOUT}")


_HTTP_RECV_RE = re.compile(r"session|requests|\bjar\b")
# 本机回路（CDP 控制面）不参与这两笔账：那是秒级预算的 127.0.0.1，熔断/超时口径都不适用
_LOCAL_EXEMPT_RE = re.compile(r"local_session|local_url|/json/|127\.0\.0\.1|localhost")
# 变量名绑的是 HTTP 客户端（`s = self._session`、`s = requests.Session()`、
# `s = self._ensure_http()`）才算取页接收者；会话工厂在各站有不同名字，这里逐个列出来，
# 新增工厂名却没登记的话，判据会安静地把那一处当成字典取值放过
_HTTP_BIND_RE = re.compile(r"[Ss]ession|_ensure_http|_http_client|\bjar\b")
# 反面：绑的是**响应**而不是客户端（`ver = requests.get(...).json()` 之后那句
# `ver.get("webSocketDebuggerUrl")` 是字典取值），按上面那条会被认成客户端——必须排除，
# 否则门开始在编事故（本轮 site_adapters 的爱企查标签那一处就是这个形状）
_HTTP_RESPONSE_RE = re.compile(r"\.(?:get|post|request|head)\s*\(")


def _http_client_names(src, root):
    """这棵子树里被**赋值为 HTTP 客户端**的局部变量名。

    为什么不能只看接收者的名字：`s.get(url, timeout=…)`（mic 的重定向探测）是真的取页，
    `s.get("dead_hosts")`（cdp_base.stats 里的字典取值）是账本读数——两处写法只差参数。
    收进名单就两头都对，按名字排除就一头瞎、一头吵，所以按「这个名字在本函数里被什么赋值」判。
    """
    names = set()
    for node in _ast.walk(root):
        if not isinstance(node, _ast.Assign):
            continue
        val = _ast.get_source_segment(src, node.value) or ""
        if not _HTTP_BIND_RE.search(val) or _HTTP_RESPONSE_RE.search(val):
            continue
        for t in node.targets:
            if isinstance(t, _ast.Name):
                names.add(t.id)
    return names


def _http_calls_in(src, root):
    """root 这棵子树里**真发 HTTP 请求**的调用节点。

    两条排除都是必需的，缺一条判据就变成噪音，噪音会被下一个人整条关掉：
      ① 接收者得是 requests/session 那一族（含上面按赋值认出来的局部名）——
         `queue.get(timeout=5)`、`s.get("dead_hosts")` 这种同名方法不算；
      ② 本机 CDP 控制面不算（见 _LOCAL_EXEMPT_RE）。
    """
    bound = _http_client_names(src, root)
    out = []
    for node in _ast.walk(root):
        if not isinstance(node, _ast.Call) or not isinstance(node.func, _ast.Attribute):
            continue
        if node.func.attr not in ("get", "post", "request", "head"):
            continue
        recv = _ast.get_source_segment(src, node.func.value) or ""
        whole = _ast.get_source_segment(src, node) or ""
        is_http = bool(_HTTP_RECV_RE.search(recv)) or (
            isinstance(node.func.value, _ast.Name) and node.func.value.id in bound)
        if not is_http or _LOCAL_EXEMPT_RE.search(whole):
            continue
        out.append(node)
    return out


def _bare_timeout_problems(src):
    """AST 判据：真·HTTP 取页的 timeout 必须走 http_timeout()（回采那一处必须带 CONNECT_TIMEOUT）。

    为什么按 AST 而不是按行：本轮就有两份是**跨行调用**（跨国采购网质询页那一跳），
    按行扫会漏；而 `queue.get(timeout=5)`、`s.get("dead_hosts")` 这些同名方法按行扫
    会误响。行扫描的门在自己身上造新缺陷，就失去修它的意义了。
    """
    out = []
    tree = _ast.parse(src)
    for node in _http_calls_in(src, tree):
        kwargs = {k.arg: k.value for k in node.keywords if k.arg}
        if "timeout" not in kwargs:
            continue                       # 没写 timeout＝用的是 requests 默认值，另说
        val = kwargs["timeout"]
        if isinstance(val, _ast.Call) and getattr(val.func, "id", "") == "http_timeout":
            continue
        if (isinstance(val, _ast.Tuple) and val.elts
                and isinstance(val.elts[0], _ast.Name) and "CONNECT" in val.elts[0].id):
            continue
        out.append(f"第 {node.lineno} 行 {node.func.attr}(timeout=…) 没拆连接/读数")
    return out


_HTTP_SRC = {p.name: p.read_text(encoding="utf-8")
             for p in [HERE / "cdp_base.py", HERE / "website_contact.py", HERE / "site_adapters.py"]
             + sorted(HERE.glob("*_crawler.py"))}
_found_bare = {n: r for n, s in _HTTP_SRC.items() if (r := _bare_timeout_problems(s))}
check(f"12b 交付链没有一处裸 HTTP 还把一个数当连接+读数用（扫了 {len(_HTTP_SRC)} 份）",
      not _found_bare, str({k: v[:2] for k, v in _found_bare.items()}))
check("12b- 反向对照：把这一行退回 `timeout=25`，判据当场响并且点出第几行",
      _bare_timeout_problems('r = self._session.get(url, timeout=25, headers=headers)\n')
      and _bare_timeout_problems('r = _http_session().get(url, timeout=timeout)\n')
      and not _bare_timeout_problems('r = self._session.get(url, timeout=http_timeout(25))\n')
      and not _bare_timeout_problems('r = _http_session().get(url, timeout=(CONNECT_TIMEOUT, t))\n'),
      "退回旧写法的夹具没被抓住＝这条门是摆设")
check("12b-- 反向对照：CDP 控制面与队列的同名 .get 不误响（门不能靠禁掉判据来保持安静）",
      not _bare_timeout_problems('return local_session().get(local_url(port, "/json/version"),'
                                 ' timeout=3).status_code == 200\n')
      and not _bare_timeout_problems('item = self._queue.get(timeout=5)\n')
      and not _bare_timeout_problems('v = self._cache.get("k")\n'),
      "误响的门会被下一个人在注释里关掉")


def _own_flag_false(cls, name):
    """类**自己这一层**有没有写 `NAME = False`（不含继承、不含注释——搜字符串两者都骗得过）。"""
    return any(isinstance(st, _ast.Assign)
               and any(isinstance(t, _ast.Name) and t.id == name for t in st.targets)
               and _ast.unparse(st.value) == "False"
               for st in cls.body)


def _pure_http_classes(src):
    """这份源码里自己声明 NEEDS_BROWSER = False 的类名：主机熔断判据的作用范围。"""
    return [cls.name for cls in (n for n in _ast.walk(_ast.parse(src))
                                 if isinstance(n, _ast.ClassDef))
            if _own_flag_false(cls, "NEEDS_BROWSER")]


def _fuse_names(src):
    """基类里**真的会去问主机熔断**的方法名：_skip_dead_host 本体 + 任何调用它的封装。

    名单从源码现推，不写死两个名字：以后再加一层封装（探测账、批量账）不用改判据；
    而各站自造一个「听起来像熔断」的私有方法，因为没人问那本账，就一定不会被认出来——
    这正是这条判据要挡的东西。
    """
    names = {"_skip_dead_host"}
    for fn in (n for n in _ast.walk(_ast.parse(src)) if isinstance(n, _ast.FunctionDef)):
        attrs = {c.func.attr for c in _ast.walk(fn)
                 if isinstance(c, _ast.Call) and isinstance(c.func, _ast.Attribute)}
        if "_skip_dead_host" in attrs:
            names.add(fn.name)
    return names


_FUSE = _fuse_names(_HTTP_SRC["cdp_base.py"])


def _fetch_hook_problems(src):
    """AST 判据：**任何真的发出 HTTP 请求的函数**，都必须先问一句「这台熔断了吗」。

    为什么不按 NEEDS_BROWSER 划范围：那个旗标说的是「要不要占浏览器标签」，不是「有没有
    裸 HTTP 取页」。gys 是混合站（HTTP 快路 + 浏览器回退），类上压根没写 NEEDS_BROWSER=False，
    靠旗标筛就会整份漏掉它——删掉它的钩子判据照样绿。按「谁真的在发请求」划才封得住。
    反向夹具（12d-/12d--）就是本轮回填真实踩到的坑：钩子插进多行 docstring 中间，语法、
    编译、真跑都不报错，那段代码成了字符串的一部分——只有 AST 读得出它在不在函数体里。
    """
    out = []
    for fn in (n for n in _ast.walk(_ast.parse(src)) if isinstance(n, _ast.FunctionDef)):
        reqs = _http_calls_in(src, fn)
        if not reqs:
            continue                                   # 不发请求的函数不归这笔账管
        first_req = min(r.lineno for r in reqs)
        hooks = [n for n in _ast.walk(fn)
                 if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Attribute)
                 and n.func.attr in _FUSE]
        if not hooks:
            out.append(f"{fn.name}(第 {fn.lineno} 行) 真发 HTTP 请求却没先问主机熔断")
        elif min(h.lineno for h in hooks) > first_req:
            out.append(f"{fn.name}(第 {fn.lineno} 行) 熔断问在请求之后"
                       f"（第 {first_req} 行已经付过整轮超时了）")
    return out


# 扫描集＝整条交付链，不只 *_crawler.py：上一版只 glob 采集器，**基类自己**那句
# `except: pass`（谎报「站点仍在限流」）正好落在盲区里——各站的写法是照基类抄的，
# 基类破了这一族就是十份跟着破，而门却是绿的。website_contact 有自己那本主机账
# （_HOST_CACHE.dead_why + CONNECT_TIMEOUT），豁免理由写在 12n= 那条检查里。
_hook_srcs = dict(_HTTP_SRC)
_hook_srcs.pop("website_contact.py", None)
_hook_bad = {n: r for n, r in ((n, _fetch_hook_problems(s)) for n, s in _hook_srcs.items()) if r}
_pure = sorted(n for n, s in _hook_srcs.items() if _pure_http_classes(s))
_http_fns = sum(len(_http_calls_in(s, n))
                for s in _hook_srcs.values()
                for n in (m for m in _ast.walk(_ast.parse(s)) if isinstance(m, _ast.FunctionDef)))
check(f"12d 每一处真发 HTTP 请求的取页函数都先问了主机熔断（{len(_hook_srcs)} 份采集器、"
      f"{_http_fns} 个发请求的函数）",
      not _hook_bad and _http_fns >= 20 and len(_pure) >= 8,
      f"{_hook_bad}｜纯 HTTP 站={_pure}")
check("12d--- 熔断名单是从基类现推的：本体与探测那层封装都在内（写死名单＝加一层就判不到）",
      "_skip_dead_host" in _FUSE and "_skip_dead_host_probe" in _FUSE,
      f"现推名单={sorted(_FUSE)}")
check("12d- 反向对照：把钩子写在 docstring 里（今天真实踩到过的写法）必须被判定为没挂",
      bool(_fetch_hook_problems(
          'class A:\n'
          '    NEEDS_BROWSER = False\n'
          '    def _fetch(self, url, tries=3):\n'
          '        """取页。\n'
          '        # 主机级熔断\n'
          '        if self._skip_dead_host(url):\n'
          '            return ""\n'
          '        """\n'
          '        return requests.get(url, timeout=http_timeout(25)).text\n')),
      "插进 docstring 的钩子＝静默失效，这种夹具不响就说明判据在读注释")
check("12d-- 反向对照：正常挂法的夹具放行、摘掉钩子或挂晚了的夹具要响（两头都试才叫判据）",
      not _fetch_hook_problems(
          'class A:\n    NEEDS_BROWSER = False\n'
          '    def _fetch(self, url):\n        """取页。"""\n'
          '        if self._skip_dead_host(url):\n            return ""\n'
          '        return requests.get(url, timeout=http_timeout(25)).text\n')
      and bool(_fetch_hook_problems(
          'class A:\n    NEEDS_BROWSER = False\n'
          '    def _fetch(self, url):\n'
          '        return requests.get(url, timeout=http_timeout(25)).text\n'))
      and bool(_fetch_hook_problems(
          'class A:\n    NEEDS_BROWSER = False\n'
          '    def _fetch(self, url):\n'
          '        r = requests.get(url, timeout=http_timeout(25))\n'
          '        if self._skip_dead_host(url):\n            return ""\n'
          '        return r.text\n')),
      "恒响或恒不响的门都等于没有门")


class DeadHostCrawler(CdpSiteCrawler):
    """最小纯 HTTP 站：只为验证基类那本熔断账，站点解析逻辑一概不碰（也不许碰数据目录）。"""

    PLATFORM_SLUG = "zz_deadhost"
    PLATFORM_NAME = "熔断夹具站"
    HOME_DOMAIN = "dead.example.com"
    NEEDS_BROWSER = False

    _TMP = _tf.mkdtemp(prefix="dead_host_")

    @staticmethod
    def _stable_data_dir():
        return Path(DeadHostCrawler._TMP)     # 夹具的 seen_store 绝不落进真数据目录

    def __init__(self):
        super().__init__()
        self.calls = self.skips = 0
        self.script = []                      # 每页一种死法：callable 抛异常或正常回页

    def _fetch(self, url, tries=2):
        if self._skip_dead_host(url):
            self.skips += 1
            return ""
        self.calls += 1
        act = self.script.pop(0) if self.script else (lambda: "<html>正常页</html>")
        try:
            html = act()
        except Exception as e:
            self._note_request_error(self._shape_request_error(e))
            return ""
        if html:                       # 与交付链各站同一口径：真的取到一页就销一次账
            self._mark_activity()
        return html

    def build_search_url(self, kw, page):
        return f"{self.BASE_URL}s?q={kw}&p={page}"

    def parse_list(self, html, kw):
        return []

    def crawl_detail(self, stub):
        return {}


def _raise(text, cls="ConnectTimeout"):
    """造一个「调用就抛」的动作：夹具用它把每一页的死法安排明白。"""
    return lambda: (_ for _ in ()).throw(_as_err(cls, text))


_CONNECT_TEXT = _ERR_SAMPLES[0][2]          # 顺企网那一页的真异常（本机抓的，不是编的）
_HOST = "b2b.11467.com"                     # 池头里那个 host：分型器与熔断账都从这里取
_LIST_URL = "https://b2b.11467.com/search.php?q=泵"
_NEXT_URL = "https://b2b.11467.com/search/-abc.htm"
_d = DeadHostCrawler()
_e, _logs = equip(_d)

_d.script = [_raise(_CONNECT_TEXT)]
check("12e 第 1 次连不上：不拉黑（偶发一次就掐活路＝自己把能出货的路断了）",
      _d._fetch(_LIST_URL) == "" and not _d._dead_hosts
      and _d._dead_strikes.get("b2b.11467.com") == 1,
      f"strikes={_d._dead_strikes} dead={_d._dead_hosts}")
_d.script = [_raise(_CONNECT_TEXT)]
check("12e- 第 2 次连不上：按主机拉黑，并且存的就是分型好那一句（类别在头部）",
      _d._fetch(_NEXT_URL) == "" and "b2b.11467.com" in _d._dead_hosts
      and _d._dead_hosts["b2b.11467.com"][1].startswith("【连不上·无应答】b2b.11467.com:443"),
      str(_d._dead_hosts))
_before = (_d.calls, _d.skips)
for _ in range(4):
    _d._fetch(_NEXT_URL)
check("12e-- 拉黑之后一次网络都不发（省下来的就是本轮别人的预算）",
      _d.calls == _before[0] and _d.skips == _before[1] + 4,
      f"calls={_d.calls} skips={_d.skips}")
check("12e--- 「本轮不再请求」那句只打一次，且说清这不是采不到（刷屏与误导都算缺陷）",
      sum(1 for x in _logs if "本轮不再请求" in x) == 1
      and any("这不是采不到" in x and "改解析代码没有用" in x for x in _logs),
      str([x[:80] for x in _logs if "不再请求" in x]))
check("12e---- stats() 给得出这笔（界面与体检器问的是账，不是日志文案）",
      list(_d.stats()["dead_hosts"]) == ["b2b.11467.com"]
      and _d.stats()["dead_hosts"]["b2b.11467.com"].startswith("【连不上·无应答】"),
      str(_d.stats()["dead_hosts"]))
check("12e----- 收尾两处渲染都接了这笔：0 家那行与账目行（少一处＝某一轮看不见熔断）",
      "已按主机熔断，本轮不再重试" in _HTTP_SRC["cdp_base.py"]
      and 'lk += f"｜主机熔断 ' in _HTTP_SRC["cdp_base.py"],
      "cdp_base 收尾没把 dead_hosts 并进渲染")

# 探测账（12k）：看门狗每几十秒探一次，比取页更烧预算；更要紧的是**说对来路**——
# 熔断＝这一次探测根本没发出去，把它报成「仍在限流/仍在等验证」就是让人干等。
_fuse_word = _d._skip_dead_host_probe(_LIST_URL)      # _d 此刻已拉黑 _HOST
check("12k 探测门给的是带 NET_FAIL_TAG 前缀的一句话（看门狗按前缀分网络账与站点账）",
      _fuse_word is not None and _fuse_word.startswith(cdp_base.NET_FAIL_TAG)
      and _HOST in _fuse_word, repr(_fuse_word))
check("12k- 反向对照：没熔断的主机必须回 None（真能探的时候不许谎说网络不通）",
      _d._skip_dead_host_probe("https://www.makepolo.com/spc_new.php?q=泵") is None,
      repr(_d._skip_dead_host_probe("https://www.makepolo.com/spc_new.php?q=泵")))
check("12k-- 问探测门不算一次新死法：黑名单还是那一台，取页失败账一页都没多记",
      list(_d._dead_hosts) == [_HOST] and _d.stats()["page_fail"] == 0,
      f"dead={list(_d._dead_hosts)} page_fail={_d.stats()['page_fail']}")


def _probe_fuse_problems(src):
    """AST 判据：`probe_block` 这类**回报文**的探测，只准用 `_skip_dead_host_probe`。

    直接问 `_skip_dead_host` 拿到的是布尔，探测函数十有八九会顺手回「仍在限流」或「已解除」——
    前者让人干等两小时，后者更糟：以为通了，回头取页还是一台都连不上。封装那句子里带了
    NET_FAIL_TAG 前缀，看门狗才分得清这是网络账（见 cdp_base._skip_dead_host_probe）。
    """
    out = []
    for fn in (n for n in _ast.walk(_ast.parse(src))
               if isinstance(n, _ast.FunctionDef) and n.name == "probe_block"):
        if not _http_calls_in(src, fn):
            continue
        used = {c.func.attr for c in _ast.walk(fn)
                if isinstance(c, _ast.Call) and isinstance(c.func, _ast.Attribute)}
        if "_skip_dead_host_probe" not in used:
            out.append(f"probe_block(第 {fn.lineno} 行) 没用探测门（回报文必须带网络归因）")
    return out


_probe_bad = {n: r for n, r in ((n, _probe_fuse_problems(s)) for n, s in _hook_srcs.items()) if r}
_probe_srcs = sorted(n for n, s in _hook_srcs.items() if "def probe_block" in s)
check(f"12k--- 每一站的恢复探测（{len(_probe_srcs)} 份）都走探测门，不带网络归因的写法判不过",
      not _probe_bad and len(_probe_srcs) >= 5, f"{_probe_bad}｜探测站={_probe_srcs}")
check("12k---- 反向对照：探测里用裸布尔门（回「仍在限流」那种写法）必须被判定为不合格",
      bool(_probe_fuse_problems(
          'class A:\n'
          '    def probe_block(self):\n'
          '        if self._skip_dead_host(self.url):\n            return "仍在限流"\n'
          '        return requests.get(self.url, timeout=http_timeout(20)).text\n'))
      and not _probe_fuse_problems(
          'class A:\n'
          '    def probe_block(self):\n'
          '        f = self._skip_dead_host_probe(self.url)\n'
          '        if f:\n            return f\n'
          '        return requests.get(self.url, timeout=http_timeout(20)).text\n'),
      "探测账的归因分叉没有反夹具＝下一次「顺手写成限流」没人拦")


def _mark_clear_problems(src):
    """AST 判据：问过熔断门、又真发了请求的函数，成功那一路必须给这笔账销一次。

    少了这一步，探测/对照就变成「只记账不销账」：站点明明回了话，连击照挂，下一次抖动就把
    一台活着的主机拉黑——比不设熔断更糟（用户看到的是「偶发一次好，之后再也不来」）。
    本轮补完 9 处熔断门之后，正是这条判据把 5 个只探测不销账的函数点出来的。
    """
    out = []
    for fn in (n for n in _ast.walk(_ast.parse(src)) if isinstance(n, _ast.FunctionDef)):
        attrs = {c.func.attr for c in _ast.walk(fn)
                 if isinstance(c, _ast.Call) and isinstance(c.func, _ast.Attribute)}
        if not (attrs & _FUSE) or not _http_calls_in(src, fn):
            continue
        if "_mark_activity" not in attrs:
            out.append(f"{fn.name}(第 {fn.lineno} 行) 真发了请求却没给熔断账销账")
    return out


_clear_bad = {n: r for n, r in ((n, _mark_clear_problems(s)) for n, s in _hook_srcs.items()) if r}
_clear_fns = sum(1 for s in _hook_srcs.values()
                 for fn in _ast.walk(_ast.parse(s))
                 if isinstance(fn, _ast.FunctionDef)
                 and ({c.func.attr for c in _ast.walk(fn)
                       if isinstance(c, _ast.Call) and isinstance(c.func, _ast.Attribute)} & _FUSE)
                 and _http_calls_in(s, fn))
check(f"12l 挂了熔断门的函数一定也销账（{len(_hook_srcs)} 份采集器、{_clear_fns} 个挂了门的函数）",
      not _clear_bad, str(_clear_bad))
check("12l- 反向对照：只挂门不销账的夹具必须响（这条门就是本轮抓那 5 处用的）",
      bool(_mark_clear_problems(
          'class A:\n'
          '    def _fetch(self, url):\n'
          '        if self._skip_dead_host(url):\n            return ""\n'
          '        return requests.get(url, timeout=http_timeout(20)).text\n'))
      and not _mark_clear_problems(
          'class A:\n'
          '    def _fetch(self, url):\n'
          '        if self._skip_dead_host(url):\n            return ""\n'
          '        r = requests.get(url, timeout=http_timeout(20))\n'
          '        self._mark_activity()\n        return r.text\n'),
      "只验一条腿的门＝下一次漏销账照样看不见")

# 一次成功的往来必须把账清掉：否则站点抖动两次就永久拉黑（这是「连续」二字的分量）
_c = DeadHostCrawler()
_e2, _l2 = equip(_c)
_c.script = [_raise(_CONNECT_TEXT), lambda: "<html>正常一页</html>", _raise(_CONNECT_TEXT)]
for _ in range(3):
    _c._fetch(_LIST_URL)
check("12f 拉黑看的是「连续」：失败·成功·失败 之后仍不拉黑（抖动不是死刑）",
      not _c._dead_hosts and _c._dead_strikes.get(_HOST) == 1,
      f"strikes={_c._dead_strikes} dead={_c._dead_hosts}")
_c.script = [_raise(_CONNECT_TEXT)]
_c._fetch(_LIST_URL)                    # 这次是紧接着的第二次失败 → 真的拉黑
check("12f- 前提成立：这一次确实进了黑名单（下面那条销账才有对照组）",
      _HOST in _c._dead_hosts, f"strikes={_c._dead_strikes} dead={_c._dead_hosts}")
_c._skip_dead_host(_LIST_URL)           # 只为本轮把「当前主机」记进这笔账
_c._mark_activity()                     # 同一主机在**没挂钩的那条路**上取到页（详情/官网回采）
check("12f-- 一次真实往来就销账：连击与黑名单同时清空（不清＝抖动两次再也回不来）",
      not _c._dead_hosts and not _c._dead_strikes,
      f"strikes={_c._dead_strikes} dead={_c._dead_hosts}")

# 冷却：拉黑不等于本轮之后再也不试（用户可能中途换网络，站点也可能自己回来）
_g = DeadHostCrawler()
_e3, _l3 = equip(_g)
_g.script = [_raise(_CONNECT_TEXT)] * cdp_base.DEAD_HOST_STRIKES
while _g.script:
    _g._fetch(_LIST_URL)
check("12g 冷却未到：仍然跳过（不是一到时间就猛试）",
      _g._skip_dead_host(_LIST_URL) and not any("冷却已满" in x for x in _l3),
      str(_g._dead_hosts))
FT.now += cdp_base.DEAD_HOST_COOLDOWN + 1
check("12g- 冷却期满：放一次真实机会，并清掉两本账（拉黑是有期限的隔离，不是死刑）",
      not _g._skip_dead_host(_LIST_URL) and not _g._dead_hosts and not _g._dead_strikes
      and any("冷却已满" in x for x in _l3), f"dead={_g._dead_hosts}｜logs={[x[:40] for x in _l3]}")

# 反面：说明「连接是通的」那些档，一律不许进熔断账
_h = DeadHostCrawler()
_e4, _l4 = equip(_h)
for _txt, _cls in ((_ERR_SAMPLES[3][2], "ReadTimeout"),        # 读响应超时：站慢，不是连不上
                   (_ERR_SAMPLES[5][2], "SSLError"),           # TLS：连上了才谈得上证书
                   # 这一格**必须按字符串**喂：各站 `_fetch` 拿到响应后就是这么记的（`f"HTTP {code}"`），
                   # 类名兜底那一档只在「拿不到响应」的异常上生效——真响应永远不会是 ConnectionError，
                   # 所以两者不会同时出现（要是哪天出现了，说明那一档得让状态码先行，改这里就是提醒）。
                   ("HTTP 429（防采插页）", "ConnectionError"),  # 限流：链路明明通着
                   ("某种没见过的新错误", "Exception")):        # 未分型：缺口清单，不是死刑
    _h._note_request_error(_txt)
check("12h 只有「连接阶段」那七档能拉黑：读超时/TLS/429/未分型一律不进账",
      not _h._dead_hosts and not _h._dead_strikes, f"{_h._dead_strikes}｜{_h._dead_hosts}")
_j = DeadHostCrawler()
_e6, _l6 = equip(_j)
for _ in range(cdp_base.DEAD_HOST_STRIKES):
    _j._note_request_error(cdp_base.request_error_shape(
        _as_err("ConnectionError", _ERR_SAMPLES[-1][2])))     # 消息里没细节的 ConnectionError
check("12h---- 类名兜底那一档同样能拉黑：池头说得出主机就轮得到熔断账（否则这类死法整轮白付超时）",
      list(_j._dead_hosts) == ["www.globalimporter.net"]
      and _j._skip_dead_host("https://www.globalimporter.net/"),
      f"dead={list(_j._dead_hosts)}｜shape={_j._dead_hosts and list(_j._dead_hosts.values())[0][:60]}")
_i = DeadHostCrawler()
_e5, _l5 = equip(_i)
for _ in range(cdp_base.DEAD_HOST_STRIKES):        # 同一台主机连续两次连不上
    _i._note_request_error(cdp_base.request_error_shape(
        _as_err("ConnectTimeout", _CONNECT_TEXT)))
check("12h- 拉黑按主机而不是按站：商铺子域与列表域各记一本账（列表连不上≠子域也不能试）",
      list(_i._dead_hosts) == [_HOST]
      and not _i._skip_dead_host("https://sz12345.11467.com/contact.asp")
      and _i._skip_dead_host("https://B2B.11467.COM/search/-abc.htm"),
      f"dead={list(_i._dead_hosts)}")
check("12h-- _host_of 认得大小写/端口/userinfo/IPv6/无 scheme（认不出主机＝这笔账永远记不上）",
      CdpSiteCrawler._host_of("https://B2B.11467.COM:443/x") == "b2b.11467.com"
      and CdpSiteCrawler._host_of("http://u:p@sz1.11467.com/a") == "sz1.11467.com"
      and CdpSiteCrawler._host_of("https://[::1]:8080/x") == "::1"
      and CdpSiteCrawler._host_of("b2b.11467.com/search") == ""
      and CdpSiteCrawler._host_of("") == "", str([
          CdpSiteCrawler._host_of("https://[::1]:8080/x"),
          CdpSiteCrawler._host_of("b2b.11467.com/search")]))

# 名单通道（1688/企知道那一族走浏览器）没有「跳过取页」这一步：故意不给这一格，
# 给了恒空的键就会让体检器把「没这件事」读成「查过了，一台都没拉黑」。
_1688_stats_src = _def_src(_HTTP_SRC["site_adapters.py"], "stats", "Ali1688Site")
_lchan_stats = _def_src(_HTTP_SRC["site_adapters.py"], "stats", "QizhidaoSite")
_lchan_extras = _def_src(_HTTP_SRC["site_adapters.py"], "_list_channel_extras")
check("12i 走浏览器的通道 stats() 里没有 dead_hosts（文档里写明了为什么故意不给）",
      '"dead_hosts"' not in _1688_stats_src and '"dead_hosts"' not in _lchan_stats
      and '"dead_hosts"' not in _lchan_extras and "故意不给" in _1688_stats_src,
      _1688_stats_src[:120])

# 实机体检器读的是同一本账：报告里那一路要能说「已按主机熔断」，而不是「采不到」
_live = _load_live_check()
_mark, _verdict = _live._attrib({"page_fail": 3, "listed": 0, "last_page_error": "【连不上·无应答】x:443｜…",
                                 "waiting": "", "incr_skip": 0,
                                 "dead_hosts": {"b2b.11467.com": "【连不上·无应答】b2b.11467.com:443｜…"}},
                                0, False, "")
check("12j 体检器把「链路未通」与「熔断主机」一起报出来（读数只问 stats，不猜文案）",
      _mark == "❌" and "已按主机熔断" in _verdict and "b2b.11467.com" in _verdict,
      _verdict)
_mark2, _verdict2 = _live._attrib({"page_fail": 3, "listed": 0, "last_page_error": "",
                                   "waiting": "", "incr_skip": 0}, 0, False, "")
check("12j- 反向对照：没熔断账时不许凭空写「已熔断」（假安心比假红更难查）",
      _mark2 == "❌" and "已按主机熔断" not in _verdict2, _verdict2)
# 12m 同类查全：分型器修好了 9 个取页点，**同样决定方向的其它 except 分支**不许还在原地截断。
# 本轮实机跨国采购网就是这一族——日志里那句「首页会话引导失败（HTTPSConnectionPool(host=…」
#  [:60] 砍掉的正好是 `(Caused by …)`，而引导失败的主语通常是「这台主机连不上」：既不说类别，
#  也不给熔断账记连击，于是整轮每个关键词都重新付一遍跳转链的超时（已改成走 _note_request_error）。
def _raw_exc_slice_problems(src):
    """AST 判据：except 分支里不许出现 `str(那个异常名)[a:b]`。

    只认「绑定了 except 名字的那一层」里的切片，所以 `str(rec["备注"])[:80]` 这类与异常无关的
    普通截断不误伤；也不管没截断的 `str(e)`（信息是全的，只是长）。
    适用边界：这一条只管**裸 HTTP 那条链**（cdp_base 一族 + 1688 那份镜像）。riskbird 那棵树
    走 Selenium、没有主机熔断这本账，它的导航异常文案不归这笔管（口径不同的两份实现各判各的，
    比硬套一个判据然后把另一侧整条注释掉要好）。
    """
    out = []
    for h in (n for n in _ast.walk(_ast.parse(src))
              if isinstance(n, _ast.ExceptHandler) and n.name):
        for sub in (n for n in _ast.walk(h) if isinstance(n, _ast.Subscript)):
            v = sub.value
            if (isinstance(v, _ast.Call) and isinstance(v.func, _ast.Name) and v.func.id == "str"
                    and len(v.args) == 1 and isinstance(v.args[0], _ast.Name)
                    and v.args[0].id == h.name):
                out.append(f"第 {sub.lineno} 行 str({h.name})[…] 直接截断异常文本"
                           f"（该走 request_error_shape / _note_request_error）")
    return out


_raw_srcs = dict(_HTTP_SRC)
_raw_srcs["ali1688_crawler.py"] = (_REPO / "ali1688_build" / "ali1688_crawler.py").read_text(encoding="utf-8")
_raw_bad = {n: r for n, r in ((n, _raw_exc_slice_problems(s)) for n, s in _raw_srcs.items()) if r}
check(f"12m 交付链没有任何一处还在拿 str(异常)[:N] 当失败归因（扫了 {len(_raw_srcs)} 份）",
      not _raw_bad and len(_raw_srcs) >= 13, str(_raw_bad))
check("12m- 反向对照：gi 引导那一处的原样写法做成夹具必须响（不响就是判据抓不到这一族）",
      bool(_raw_exc_slice_problems(
          'try:\n    r = s.get(url, timeout=http_timeout(25))\n'
          'except Exception as e:\n'
          '    self._log(f"  ⚠ 首页会话引导失败（{str(e)[:60]}）")\n'))
      and not _raw_exc_slice_problems(
          'try:\n    r = s.get(url, timeout=http_timeout(25))\n'
          'except Exception as e:\n'
          '    self._note_request_error(e)\n'
          '    self._log(f"  ⚠ 首页会话引导失败（{self._last_page_error[:70]}）")\n')
      and not _raw_exc_slice_problems(
          'x = str(rec["备注"])[:80]\nnote = str(cfg)[:40]\n'),
      "抓不到旧写法、或对与异常无关的截断误响，都会让这条门下一个人整个关掉")

# 12n 探测这一族的账：取页函数吞了异常至少还会打一行日志，探测函数吞掉的这一笔**直接决定**
# 看门狗接下来那句是「仍在限流」「已解除」还是「本机网络不通」。2026-11-14 实机顺企网 80/443
# 全部连接超时，界面却说「顺企网仍在防采/限流状态」——处方因此从「换出口网络」变成「干等两小时」。
_PROBE_FAMILY_RE = re.compile(r"probe|_control_ok")


def _account_handles(base_src):
    """基类里**会把失败原因记进请求层账**的把手名（现推，不写死名单）。

    判「有没有交账」要认的就是这一组：`_note_request_error` 本体，加上任何调它或调分型器的封装
    （`_probe_net_fail`、`_note_page_fail`、`_shape_request_error`…）。以后新加一把把手，只要它
    在基类里走了这两条路，各站用它收口就自动被认——写死名单的话，下一个封装出来就得改判据，
    而人大概率会改成「先注释掉这条门」。
    """
    names = {"_note_request_error"}
    for fn in (n for n in _ast.walk(_ast.parse(base_src)) if isinstance(n, _ast.FunctionDef)):
        body = _ast.get_source_segment(base_src, fn) or ""
        if "_note_request_error" in body or "request_error_shape" in body:
            names.add(fn.name)
    return names


_ACCOUNT_HANDLES = _account_handles(_HTTP_SRC["cdp_base.py"])


def _probe_family_fns(src):
    """探测/对照这一族的函数节点：名字命中的，以及定义在它们**里面**的闭包。

    闭包这一层不能省：跨国采购网的 `once()` 就写在 probe_block 肚子里，真发请求的是它，
    只看外层名字等于把那一处整个放行。
    """
    fns = [n for n in _ast.walk(_ast.parse(src)) if isinstance(n, _ast.FunctionDef)]
    picked = {}
    for n in fns:
        if not _PROBE_FAMILY_RE.search(n.name):
            continue
        picked[id(n)] = n
        for m in fns:
            if m is not n and n.lineno <= m.lineno and (m.end_lineno or 0) <= (n.end_lineno or 0):
                picked.setdefault(id(m), m)
    return list(picked.values())


def _probe_account_problems(src):
    """AST 判据：**包着真实 HTTP 请求**的 try，在探测/对照函数里就不许把失败原因吞掉。

    三种历史写法都判不过（都是本轮从真站上摘下来的）：
      · `except: pass` → 落到「站点仍在限流」；
      · `except: return ""` → 谎报「已解除」，刚放行就撞第二次死链；
      · `except: return False`（对照探测）→ 布尔没错，但那笔原因谁也没记，熔断账少一次连击。
    只按 try 里真发了请求来划范围：gys/maimaib2b 各有一个包着浏览器取页的 except，那是 CDP
    那本账的事，硬套这条判据只会逼着人给控制面也记一笔网络账。
    另一半：helper 记了账，`probe_block` 收尾就得把那本账**读出来**，否则原因躺在
    `_last_page_error` 里、界面照旧说「仍在限流」（麦麦B2B 修前正是这一格）。
    """
    out = []
    for fn in _probe_family_fns(src):
        for t in (x for x in _ast.walk(fn) if isinstance(x, _ast.Try)):
            if not _http_calls_in(src, t):
                continue
            for h in t.handlers:
                called = {c.func.attr for c in _ast.walk(h)
                          if isinstance(c, _ast.Call) and isinstance(c.func, _ast.Attribute)}
                if called & _ACCOUNT_HANDLES or any(isinstance(x, _ast.Raise) for x in _ast.walk(h)):
                    continue
                out.append(f"{fn.name}(第 {fn.lineno} 行) 第 {h.lineno} 行的 except 把探测失败"
                           f"的原因吞了（没记账也没交出话，看门狗只能猜）")
    tree = _ast.parse(src)
    fns = [n for n in _ast.walk(tree) if isinstance(n, _ast.FunctionDef)]
    head = next((n for n in fns if n.name == "probe_block"), None)
    if head is not None:
        head_src = _ast.get_source_segment(src, head) or ""
        helpers = sorted({n.name for n in fns
                          if n.name != "probe_block" and _PROBE_FAMILY_RE.search(n.name)
                          and "_note_request_error" in (_ast.get_source_segment(src, n) or "")})
        # 记账的那一把只要被 probe_block 叫到（自己收口＝`_probe_net_fail`，或收尾读账＝
        # `_probe_net_word`）就算交出；两把都没出现，说明原因记在字段里却没人说给看门狗
        if helpers and not any(h in head_src for h in ("_probe_net_word", "_probe_net_fail")):
            out.append(f"probe_block 没读 helper（{helpers}）记下的那笔账（原因记了但没人说，等于没记）")
    return out


_probe_acc_bad = {n: r for n, r in ((n, _probe_account_problems(s)) for n, s in _hook_srcs.items()) if r}
_probe_fns = sum(len(_probe_family_fns(s)) for s in _hook_srcs.values())
check(f"12n 探测/对照这一族（{_probe_fns} 个函数、{len(_hook_srcs)} 份源码）的 except 一律交出原因",
      not _probe_acc_bad and _probe_fns >= 12, str(_probe_acc_bad))
check("12n- 反向对照：三种真实旧写法（pass 谎报限流 / return \"\" 谎报解除 / return False 不记账）"
      "都必须响，改后的写法必须放行",
      bool(_probe_account_problems(
          'class A:\n    def probe_block(self):\n        try:\n'
          '            r = self._session.get(u, timeout=http_timeout(20))\n'
          '            return "" if r.status_code == 200 else "仍在限流"\n'
          '        except Exception:\n            pass\n'
          '        return "站点仍在限流"\n'))
      and bool(_probe_account_problems(
          'class A:\n    def probe_block(self):\n        try:\n'
          '            return "" if self._session.get(u, timeout=http_timeout(20)).status_code == 200 else "x"\n'
          '        except Exception:\n            return ""\n'))
      and bool(_probe_account_problems(
          'class A:\n    def _control_ok(self):\n        try:\n'
          '            return self._session.get(u, timeout=http_timeout(20)).status_code == 200\n'
          '        except Exception:\n            return False\n'))
      and not _probe_account_problems(
          'class A:\n    def probe_block(self):\n        try:\n'
          '            r = self._session.get(u, timeout=http_timeout(20))\n'
          '            self._mark_activity()\n            return ""\n'
          '        except Exception as e:\n            return self._probe_net_fail(e)\n')
      and not _probe_account_problems(
          'class A:\n    def _control_ok(self):\n        try:\n'
          '            return self._session.get(u, timeout=http_timeout(20)).status_code == 200\n'
          '        except Exception as e:\n'
          '            self._note_request_error(e)\n            return False\n'
          '    def probe_block(self):\n'
          '        return self._probe_net_word() or "仍在限流"\n'),
      "三种旧写法里任何一条不被抓到，这一族就会在下一次重构里长回来")
check("12n-- 反向对照：包着浏览器/CDP 的 except（没发 HTTP）不许误响，helper 记账而 probe 不读要响",
      not _probe_account_problems(
          'class A:\n    def probe_block(self):\n        try:\n'
          '            hit = self._eval("document.title")\n'
          '        except Exception:\n            hit = ""\n        return "仍在限流"\n')
      and bool(_probe_account_problems(
          'class A:\n    def _http_probe_ok(self):\n'
          '        try:\n            return bool(self._session.get(u, timeout=http_timeout(20)))\n'
          '        except Exception as e:\n'
          '            self._note_request_error(e)\n            return False\n'
          '    def probe_block(self):\n'
          '        if self._http_probe_ok():\n            return ""\n'
          '        return "仍在限流"\n')))
check("12n--- 认账把手是从基类现推的：探测那两把（记原因的与给看门狗那句话的）都在名单里"
      "（写死名单＝加一把就判不到）",
      "_note_request_error" in _ACCOUNT_HANDLES and "_probe_net_fail" in _ACCOUNT_HANDLES
      and "_note_page_fail" in _ACCOUNT_HANDLES, f"现推名单={sorted(_ACCOUNT_HANDLES)}")
_tag_srcs = {n: s for n, s in _hook_srcs.items() if n != "cdp_base.py"}
_tag_bad = {n: [x.lineno for x in _ast.walk(_ast.parse(s))
                if isinstance(x, _ast.Name) and x.id == "NET_FAIL_TAG"] for n, s in _tag_srcs.items()}
_tag_bad = {n: v for n, v in _tag_bad.items() if v}
check("12n= NET_FAIL_TAG 只在基类那几把把手里拼一次（各站自己挂前缀＝把代码异常也说成断网，"
      "而且不给熔断账记连击）",
      not _tag_bad and len(_tag_srcs) >= 11, str(_tag_bad))
check("12n=- 反向对照：gi 探测里那句 `f\"{NET_FAIL_TAG}{type(e).__name__}\"` 的原样写法必须响",
      bool([1 for x in _ast.walk(_ast.parse(
          'def once():\n    try:\n        r = s.get(u)\n'
          '    except Exception as e:\n'
          '        return False, f"{NET_FAIL_TAG}{type(e).__name__}", False\n'))
          if isinstance(x, _ast.Name) and x.id == "NET_FAIL_TAG"]))
print("⑫ 主机级熔断 + 连接/读数超时拆分：连不上的主机不再吃整轮预算 ✓")


print(f"\n全部 {ok} 项断言通过 ✅")
