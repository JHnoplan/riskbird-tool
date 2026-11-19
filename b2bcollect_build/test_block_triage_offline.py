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
import types
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cdp_base
import text_shape
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
    # append，不是 insert(0)：这一段只是要**借读**隔壁目录独有的 ali1688_crawler，
    # 不是要把本产品的模块换成隔壁那份同名副本（customer_library_cleaner /
    # task_registry / yzd_client 三个目录各有一份，已经把本目录抢在前面锁住了）。
    sys.path.append(str(_REPO / _d))
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

# ── ⑨-2. overlay 探测第 1 维（iframe）只认验证码特征域 ──────────────────────
# 【误伤订正 2026-11-17 的防回退断言】旧判据把 src 含 "alibaba"/"1688" 的 iframe
# 也当验证码——那正是详情页描述、店铺客服这些**正常业务 iframe** 的家域，等于每进
# 一家详情必报一次验证码，而 _wait_unblock 的解除判据读的又是同一个函数，误报永不
# 解除、等到超时收轮，整轮 0 家。这里钉死两头：业务 iframe 必须放行，真正的验证码
# 特征域（sufei/nocaptcha/wappass/baxia/punish/captcha/challenge/verify）必须拦下。
# _detect_block 对 self 的全部依赖就是 driver，用 SimpleNamespace 即可、不碰 __init__。
class _OverlayDriver:
    """_detect_block 需要的最小 driver：url/title 正常，正文足够长（绕开第三层
    「极短正文」判据），第四层返回值由用例指定。"""

    def __init__(self, script_ret):
        self.current_url = "https://s.1688.com/selloffer/offer_search.htm?k=太阳能路灯"
        self.title = "太阳能路灯-1688"
        self._script_ret = script_ret

    def find_element(self, *a, **k):
        class _Body:
            text = "正文" * 300      # 长正文：前三层（URL/标题/短正文）全都不命中
        return _Body()

    def execute_script(self, js, *a, **k):
        return self._script_ret


_detect_block = _a1688.Ali1688Crawler._detect_block
_MARKS = _a1688.Ali1688Crawler.CAPTCHA_IFRAME_MARKS
# ① 词表白名单语义：站点自家域绝不在表内；业务 iframe 的 src 不命中任何特征域。
# （fake driver 只能回显 JS 的判定结果，验不了 JS 逻辑本身；词表是单一真源，
#   JS 由它渲染，所以直接对拍词表——业务域不命中 ＝ 修复后 JS 对它们返回空。）
check("iframe 特征域词表不含站点自家域（alibaba/1688）",
      "alibaba" not in _MARKS and "1688" not in _MARKS)
for _src in ("https://airdescribing.alibaba.com/desc.html?id=3",
             "https://air.1688.com/cs/chat.html"):
    check(f"业务 iframe {_src[:36]} 不命中任何特征域（旧判据必误报）",
          not any(w in _src for w in _MARKS))
# ② JS 由词表渲染（防「词表改了 JS 还是旧列表」两处手抄漂移）
for _w in _MARKS:
    check(f"JS 渲染含特征域 '{_w}'", f"'{_w}'" in _a1688.Ali1688Crawler._CAPTCHA_OVERLAY_JS)
# ③ _detect_block 第四层接线：JS 判定结果非空 → 判拦截，空 → 放行。
#    self 除了 driver 还要取 _CAPTCHA_OVERLAY_JS，SimpleNamespace 必须一起注入
#    （缺了会在第四层 try 里抛 AttributeError 被吞掉，_detect_block 恒返回空）。
for _tag, _ret, _expect in (
    ("JS 判定为 sufei/nocaptcha 验证 iframe",
     "iframe:https://sufei.taobao.com/nocaptcha/get?x=1", "验证码弹窗"),
    ("JS 判定为 wappass 风控 iframe",
     "iframe:https://ynuf.alihttp.org/wappass/x", "验证码弹窗"),
    ("JS 判定为 baxia 安全容器 iframe",
     "iframe:https://castatic.fegine.com/baxia/entry", "验证码弹窗"),
    ("JS 判定无验证码（业务 iframe 放行后的空返回）", "", ""),
):
    _blk = _detect_block(types.SimpleNamespace(
        driver=_OverlayDriver(_ret),
        _CAPTCHA_OVERLAY_JS=_a1688.Ali1688Crawler._CAPTCHA_OVERLAY_JS))
    if _expect:
        check(f"{_tag} → 判拦截（含「{_expect}」）", bool(_blk) and _expect in _blk)
    else:
        check(f"{_tag} → 不判拦截", _blk == "")

print("⑨-2 overlay iframe 判据白名单：业务 iframe 放行、验证域拦截，误伤订正防回退 ✓")

# ── ⑨-3. 开局确凿未登录的**有限等待**：等到就继续，等不到本轮优雅跳过 ──────
# 2026-11-17 重设计的产能开关：未登录继续跑＝烧 IP 信誉+反复吃验证（两轮实机同因），
# 死等 3600s＝用户以为卡死。本轮行为：等待最多 5 分钟、等人灯亮着、每分钟报进度；
# 登录了立即继续，超时不参与本轮（不烧请求不喂风控，下一轮自动再试）。
_wfl = _a1688.Ali1688Crawler._wait_for_login
_real_sleep = _a1688.time.sleep
_a1688.time.sleep = lambda s: None      # 假时钟：等待循环不真睡，轮询序号由用例控制
try:
    class _LoginSeq:
        """按调用次序吐登录态（_login_state_by_cookie 的替身）。"""

        def __init__(self, seq):
            self._seq, self._i = list(seq), -1

        def __call__(self):
            self._i += 1
            return self._seq[min(self._i, len(self._seq) - 1)]

    logs3 = []
    _emit3 = lambda *a, **k: None          # 登录成功要发解除信号，fake self 也得带 _emit
    _s3a = types.SimpleNamespace(
        _waiting_human="", _stop_requested=False, _emit=_emit3,
        _log=logs3.append, _login_state_by_cookie=_LoginSeq([False] * 20 + [True]))
    check("⑨-3a 等待中检测到已登录 → 继续采集", _wfl(_s3a, max_wait=300) is True)
    check("⑨-3a- 等人灯当场收账（成功后 _waiting_human 复位）", _s3a._waiting_human == "")
    check("⑨-3a- 逐分钟有进度日志（用户知道工具在等人、没卡死）",
          any("仍在等待登录" in m for m in logs3), str(logs3))
    _s3b = types.SimpleNamespace(
        _waiting_human="", _stop_requested=False, _emit=_emit3,
        _log=logs3.append, _login_state_by_cookie=_LoginSeq([False]))
    check("⑨-3b 一直没人登录 → 超时优雅退出", _wfl(_s3b, max_wait=30) is False)
    check("⑨-3b- 超时后等人灯收账（不许挂着灯退出）", _s3b._waiting_human == "")
    _s3c = types.SimpleNamespace(
        _waiting_human="", _stop_requested=True, _emit=_emit3,
        _log=logs3.append, _login_state_by_cookie=_LoginSeq([False]))
    check("⑨-3c 停止位优先：等登录循环立刻让路", _wfl(_s3c, max_wait=300) is False)
finally:
    _a1688.time.sleep = _real_sleep
check("⑨-3d crawl 开局接线：preflight 确凿未登录 → _wait_for_login（防回退）",
      "_wait_for_login" in inspect.getsource(_a1688.Ali1688Crawler.crawl))

print("⑨-3 开局未登录有限等待：等到即跑、超时跳过、等人灯全程可见 ✓")

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
# 整形了却不记账 = 界面照样问不到（本轮 gys 就是这样：日志说得出、账上是空的）。
# 自第 ⑬ 节起，取页失败的分账统一走 `_note_fetch_fail(last, deep)`（它内部就是
# `_note_request_error`，只是多问一句「这一笔是通道入口的还是某一家的深链」），
# 所以两个名字都算记了账——只认真名就会把接入分账的站点判成漏记，门自己造假红。
_unrecorded = sorted(p.name for p in Path(__file__).resolve().parent.glob("*_crawler.py")
                     if "_shape_request_error(" in p.read_text(encoding="utf-8")
                     and "_note_request_error(" not in p.read_text(encoding="utf-8")
                     and "_note_fetch_fail(" not in p.read_text(encoding="utf-8"))
check("11q 整形过的站点一定也记了账（只打日志的失败在界面上等于没发生）",
      not _unrecorded, "、".join(_unrecorded))

# 11r 「本工具自己已经定过性的句子」不许被分型器降级。由来（2026-11-15 实机 1688）：
#     翻页层把 `_goto` 留下的原因又包了一层——`列表页导航失败（【被拦停】防采页没等到放行…）`，
#     旧判据只有 `startswith("【")`，于是这句被洗成 `【未分型】｜列表页导航失败（【被拦停】…）`，
#     收尾句、GUI 账目行与体检报告念的全是降级版（那一轮的 1688 明明写着「在等人过验证」，
#     界面上却只剩「未分型」）。
_SHAPE_NESTED = (
    ("被拦停", "列表页导航失败（【被拦停】防采页没等到放行（等人过验证或等 IP 冷却））"),
    ("页面未就绪", "导航失败（【页面未就绪】等不到 https://a.b/c 的完整正文）"),
    ("连不上·无应答", "翻页失败（【连不上·无应答】b2b.11467.com:443｜Connection timed out）"),
)
for _tk, _tt in _SHAPE_NESTED:
    _ts = _shape(_tt)
    check(f"11r 括号里已有的类别【{_tk}】被提回开头，不再降级成未分型",
          _ts.startswith(f"【{_tk}】") and "未分型" not in _ts, _ts)
    check(f"11r- 标签与证据一个都不丢（提类别不许顺手删掉「哪一层没做成」）",
          _tt.split("（")[0] in _ts and _tt.split("】", 1)[1].rstrip("）") in _ts, _ts)
    check(f"11s 整过形之后再过一次分型器不变样（嵌套这一档也要幂等）",
          _shape(_ts) == _ts, _shape(_ts))
# 11t 反向对照：这三句**都不以【开头**——旧那条 `startswith("【")` 判据必然放过去，
#     这正是「已经有结论却被洗成缺口」的入口。不钉这一条，11r 可能只是碰巧。
check("11t 反向对照：旧判据只看句首，这三句在旧尺子下全掉进未分型",
      all(not t.startswith("【") and "【" in t for _k, t in _SHAPE_NESTED))
# 11u 词表之外的【】不许冒充类别：站点原文里的「访问过于频繁」不是本工具下的结论，
#     认回来就是把「未分型」这个缺口清单悄悄抹掉（缺口看不见就再也补不上，见 11k）。
_forged = _shape("导航失败（【访问过于频繁】站点让等一下）")
check("11u 只认自己词表里的类别（站点文案的【】不许顶掉缺口标记）",
      _forged.startswith("【未分型】") and "访问过于频繁" in _forged, _forged)
# 11v 边界一致：熔断账/看门狗用的 `_DEAD_HOST_RE` 允许类别前有 12 个字加一个左括号，
#     分型器用的是同一个边界。两处数字一旦分叉，就会出现「账上算网络、日志说未分型」的
#     两套结论，所以按 DEAD_HOST_KINDS 全档逐一走一遍。
_vgap = [k for k in cdp_base.DEAD_HOST_KINDS
         if not cdp_base.request_error_is_connect(_shape(f"列表页导航失败（【{k}】x）"))]
check("11v 连接类每一档从嵌套形状里都能提回来，且仍算网络账（与 _DEAD_HOST_RE 同一边界）",
      not _vgap, "、".join(_vgap))
# 11w 1688 那份镜像同答（同一条句子两边必须同一句话，见 11l/11m 的同一族纪律）
check("11w 嵌套类别这一档两份镜像逐字同答",
      all(_a16_shape(t) == _shape(t) for _k, t in _SHAPE_NESTED),
      str([(_k, _a16_shape(t)) for _k, t in _SHAPE_NESTED if _a16_shape(t) != _shape(t)][:1]))
# 11x 结构判据（同类查全）：两份文件的**词表与正则**也必须同字节。只比答案比不出
#     「一边多登记了一个 token」——那种分叉要等下一句新文案才会暴露。
check("11x 类别词表与嵌套正则两份同字节（新加一档只加一边＝有一个平台一直降级）",
      _between(_src_cdp11, "_OWN_KIND_TOKENS = tuple(", "reverse=True)))")
      == _between(_src_a1611, "_OWN_KIND_TOKENS = tuple(", "reverse=True)))"),
      "_OWN_KIND_TOKENS/_NESTED_KIND_RE 两份不一致")
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
# 纯 HTTP 站那一个下限不能写成一个字面数：上一版写的就是 `>= 8`，而 8 是当时扫描集里
# 恰好有八份声明了 NEEDS_BROWSER=False 的源码算出来的——世界工厂网下站（文件已删）它就
# 当场哑响，响的原因却不是「谁没挂熔断」。换成从注册表现推：凡在 SITE_ORDER 里且类上
# 声明 NEEDS_BROWSER=False 的平台，都必须在这轮的扫描集里被真扫到，加一站少一站都不必
# 改这个数，而「glob 被改窄只认某几份」依旧会被拓下来。
from parallel_session import SITE_ORDER as _SO12, SITE_CLASSES as _SC12
_registry_pure = sorted({f"{_SC12[s].__module__.split('.')[-1]}.py" for s in _SO12
                         if _SC12.get(s) is not None
                         and getattr(_SC12[s], "NEEDS_BROWSER", True) is False})
_missing_pure = [n for n in _registry_pure if n not in _pure]
# 同一句话的完整版：扫描集必须覆盖注册表里的**每一路关键词平台**（不只纯 HTTP 那几路）。
# 上一版这里写的是字面个数（`len(_tag_srcs) >= 11`），它与 12d 那个 8 是同一个坑：数字对
# 不上时门会响，但响的原因不是「谁没挂前缀闸」而是「今天有几份文件」，下站/加站都得改数。
_registry_srcs = sorted({f"{_SC12[s].__module__.split('.')[-1]}.py" for s in _SO12
                         if _SC12.get(s) is not None})
check("12d= 纯 HTTP 站范围从注册表现推，不靠字面数：界上有的每一路纯 HTTP 关键词平台"
      "都要真进扫描集（写死个数会在下站/加站时哑响或哑掉）",
      not _missing_pure and len(_registry_pure) >= 5,
      f"注册表={_registry_pure}｜没扫到={_missing_pure}")
_http_fns = sum(len(_http_calls_in(s, n))
                for s in _hook_srcs.values()
                for n in (m for m in _ast.walk(_ast.parse(s)) if isinstance(m, _ast.FunctionDef)))
check(f"12d 每一处真发 HTTP 请求的取页函数都先问了主机熔断（{len(_hook_srcs)} 份采集器、"
      f"{_http_fns} 个发请求的函数）",
      not _hook_bad and _http_fns >= 20 and not _missing_pure,
      f"{_hook_bad}｜纯 HTTP 站={_pure}｜注册表里该在却没被扫到的={_missing_pure}")
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
          '    self._log(f"  ⚠ 首页会话引导失败（{short_reason(self._last_page_error, 70)}）")\n')
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
      "而且不给熔断账记连击）；范围从注册表现推：每一路在册平台都要被扫到",
      not _tag_bad and not [n for n in _registry_srcs if n not in _tag_srcs],
      f"{str(_tag_bad)}｜该扫却没扫到的平台={[n for n in _registry_srcs if n not in _tag_srcs]}")
check("12n=- 反向对照：gi 探测里那句 `f\"{NET_FAIL_TAG}{type(e).__name__}\"` 的原样写法必须响",
      bool([1 for x in _ast.walk(_ast.parse(
          'def once():\n    try:\n        r = s.get(u)\n'
          '    except Exception as e:\n'
          '        return False, f"{NET_FAIL_TAG}{type(e).__name__}", False\n'))
          if isinstance(x, _ast.Name) and x.id == "NET_FAIL_TAG"]))
print("⑫ 主机级熔断 + 连接/读数超时拆分：连不上的主机不再吃整轮预算 ✓")

# ══════════════════════════════════════════════════════════
# ⑬ 深链被拒 ≠ 通道级拦停：某一家的 403 不该把整条通道钉死
# ══════════════════════════════════════════════════════════
# 由来（2026-11-15 第四轮实机，马可波罗整轮只出 4 家、白等 765 秒）：
#   contact_us.html 大面积 403，而同机同期搜索页/产品页/商铺首页全 200。旧口径把
#   这一句写进 `_block_reason`，并把 `_block_probe_url` 钉在那条永远不会放行的深链上：
#   「恢复探测」探的就是它 → 永远不成功 → 整条通道按 IP 冷却干等，而数据一直在。
#   这是 probe 这一族的第八个缺陷，只是这回藏在「深链 vs 入口」的粒度上。
# 口径（唯一真源＝cdp_base.http_block_reason 的 deep 分支）：
#   · 通道入口被拒＝本机出口的问题 → 写通道账、钉探测页、看门狗等放行；
#   · 某一家的深链被拒＝这一家/这条路的问题 → 只进 stats()["deep_block"] 单笔账，
#     不参与控制流；但深链的**连接类**死法照旧进通道账（连不上是主机的属性）。
_DEEP_AWARE = {                      # 会产生深链请求的站：这些 helper 必须分两本账
    "makepolo_crawler.py": ["_fetch"],
    "b2b168_crawler.py": ["_fetch"],
    "aipage_crawler.py": ["_fetch"],
    "maimaib2b_crawler.py": ["_fetch"],
    "qihuo11467_crawler.py": ["_fetch"],
    "solarbe_crawler.py": ["_fetch"],
    "gys_crawler.py": ["_http_get", "_fetch_raw"],
}
# 反向登记：这些站今天**不产生深链请求**，所以没有第二本账可分。理由写死在这儿——
# 哪天给它们加了详情请求，13f 会响，逼着把分账一起接上，而不是悄悄退回旧口径。
_DEEP_WAIVED = {
    "gi_crawler.py": "详情是纯本地组装，一个请求都不发（站方对详情/内容页吃 IP 配额）",
    "mic_crawler.py": "详情走共享 Chrome 标签，整条路径根本不写通道级 _block_reason",
    # 世界工厂网那一路不在这张表里了：已按用户指令下站、采集器文件一并删除。
    # 登记表跟着代码走：文件不在位时这一格必须一起摘，否则「反向登记表」会在一堆不存在
    # 的名字上转圈，而它该报的是「今天真在跑的那些站里谁没分账」。
}
# 全站「会写通道级 `_block_reason`（非清空）」的函数总表。这张表由下面的判据从源码
# **现算再逐字对照**：多一处（新增升级通道账的地方没登记）或少一处（那条路被删了却没
# 更新这里）都会响。刻意做成全集，是为了让「下一个站长出同形缺陷」必须过一次人工登记。
_BLOCK_WRITERS = {
    "aipage_crawler.py": {"_fetch"}, "b2b168_crawler.py": {"_fetch"},
    "gi_crawler.py": {"_fetch"}, "gys_crawler.py": {"_http_get", "_fetch_raw"},
    "maimaib2b_crawler.py": {"_fetch"}, "makepolo_crawler.py": {"_fetch"},
    "qihuo11467_crawler.py": {"_fetch"}, "solarbe_crawler.py": {"_fetch"},
}


def _fn_seg(src, name):
    """某个函数的源码段（取第一个同名定义）。"""
    for fn in (n for n in _ast.walk(_ast.parse(src)) if isinstance(n, _ast.FunctionDef)):
        if fn.name == name:
            return _ast.get_source_segment(src, fn) or ""
    return ""


def _block_writers(src):
    """这一份文件里把**非空**原因写进通道级 `_block_reason` 的函数名集合。"""
    out = set()
    for fn in (n for n in _ast.parse(src).body if isinstance(n, _ast.ClassDef)):
        for m in (x for x in _ast.walk(fn) if isinstance(x, _ast.FunctionDef)):
            for st in (s for s in _ast.walk(m) if isinstance(s, _ast.Assign)):
                if not any(isinstance(t, _ast.Attribute) and t.attr == "_block_reason"
                           for t in st.targets):
                    continue
                val = (ast_get(src, st.value) or "").strip()
                if val and val not in ('""', "''") and not val.startswith("''"):
                    out.add(m.name)
    return out


def ast_get(src, node):
    return _ast.get_source_segment(src, node) or ""


def _deep_wiring_problems(src, helpers):
    """登记的取页 helper 有没有真的分两本账。返回问题清单（空＝合格）。"""
    bad = []
    for h in helpers:
        seg = _fn_seg(src, h)
        if not seg:
            bad.append(f"{h}：登记表里的 helper 不在源码里（改名要同步这张表）")
            continue
        head = seg.split("\n", 1)[0]
        if "deep" not in head:
            bad.append(f"{h}：签名没有 deep 形参（{head.strip()[:60]}）")
        for c in (n for n in _ast.walk(_ast.parse(seg)) if isinstance(n, _ast.Call)):
            called = getattr(c.func, "attr", "") or getattr(c.func, "id", "")
            if called == "http_block_reason" and not any(k.arg == "deep" for k in c.keywords):
                bad.append(f"{h}：http_block_reason 没表态 deep=（默认按通道算）")
            if called == "_note_request_error":
                bad.append(f"{h}：直接记通道账，绕过了深链/通道的分账把手")
        if "self._block_reason = " in seg and "_note_deep_block(" not in seg:
            bad.append(f"{h}：会写通道账却没有深链那一本账")
    return bad


# 13a 基类：deep 只回原因，通道状态一件都不许动
_c13 = B2b168Crawler()
_c13.reset_run_state()
_probe_before, _kind_before, _ra_before = (_c13._block_probe_url, _c13._block_kind,
                                           _c13._retry_after)
_deep_reason = _c13.http_block_reason(403, "x", "http://shop7.cn.makepolo.com/contact_us.html",
                                      headers={"Retry-After": "60"}, deep=True)
check("13a 深链 403：回原因，但不钉探测页、不分诊、不记 Retry-After、不写拦截原因",
      "通道入口未受影响" in _deep_reason and _c13._block_probe_url == _probe_before
      and _c13._block_kind == _kind_before and _c13._retry_after == _ra_before
      and not _c13._block_reason,
      f"reason={_deep_reason} probe={_c13._block_probe_url!r} kind={_c13._block_kind!r} "
      f"retry={_c13._retry_after!r} block={_c13._block_reason!r}")
_entry_reason = _c13.http_block_reason(403, "x", "http://caigou.makepolo.com/spc_new.php?a=1",
                                       headers={"Retry-After": "60"})
check("13a- 反向对照（同一函数同一状态码）：通道入口那一跳必须照旧钉探测页＋分诊＋Retry-After",
      "HTTP 403" in _entry_reason and _c13._block_probe_url.endswith("spc_new.php?a=1")
      and _c13._block_kind == "ip" and _c13._retry_after == 60,
      f"probe={_c13._block_probe_url} kind={_c13._block_kind} retry={_c13._retry_after}")
_shell_deep = _c13.http_block_reason(200, "访问过于频繁，请稍后再试", "http://a.b/x.html",
                                     deep=True)
_probe_after_shell_deep = _c13._block_probe_url      # 深链那一跳不许动上一条钉好的探测页
_shell_entry = _c13.http_block_reason(200, "访问过于频繁，请稍后再试", "http://a.b/list.php")
check("13a= 薄壳体积那一支同样分两档：深链只回原因，入口照旧钉页",
      "深链" in _shell_deep and "通道入口未受影响" in _shell_deep
      and _shell_entry == "防采插页（访问过于频繁）"
      and _probe_after_shell_deep.endswith("spc_new.php?a=1")
      and _c13._block_probe_url.endswith("list.php"),
      f"deep={_shell_deep} entry={_shell_entry} "
      f"probe_after_deep={_probe_after_shell_deep} probe_now={_c13._block_probe_url}")
check("13a== 反向对照：deep 形参不在签名里（退回旧口径）时判据抓得住",
      bool(_deep_wiring_problems(
          'class A:\n    def _fetch(self, url):\n'
          '        b = self.http_block_reason(403, "", url)\n'
          '        if b:\n            self._block_reason = b\n'
          '        self._note_request_error("HTTP 403")\n        return ""\n', ["_fetch"])))

# 13b 深链账：首次打一行、复记只累加；stats 与收尾账目都读得到；reset 起账
_lines = []
_c13._log = lambda msg, *a: _lines.append(msg)
_c13.reset_run_state()
_c13._note_deep_block("http://shop7.cn.makepolo.com/contact_us.html", _deep_reason)
_c13._note_deep_block("http://shop7.cn.makepolo.com/contact_us.html", _deep_reason)
_s13 = _c13.stats()
check("13b 同一主机复记只累加、日志只打第一次那一行（第 37 条重复日志不是账）",
      len(_lines) == 1 and _s13["deep_block"].get("shop7.cn.makepolo.com", [0])[0] == 2,
      f"行数={len(_lines)} 账={_s13['deep_block']}")
check("13b- 深链账是机器可读的（界面/体检器问得出主机与原因）",
      "deep_block" in _s13 and _deep_reason[:20] in str(_s13["deep_block"]), str(_s13["deep_block"]))
# 13b2 日志的**聚合口径**：马可波罗每家商铺一个子域，按主机逐家开口会把一轮刷成
# 52 行同一句话（2026-11-14 实机）——刷屏的账等于没账，人只会跳过。账仍按主机细分。
_rs13 = _fn_seg(_HTTP_SRC["cdp_base.py"], "reset_run_state")
_lines2 = []
_c13._log = lambda msg, *a: _lines2.append(msg)
_c13.reset_run_state()
for _u13 in ("http://sgjjskjc.cn.makepolo.com/contact_us.html",
             "http://gulnag.cn.makepolo.com/contact_us.html",
             "http://shop7.cn.makepolo.com/contact_us.html",
             "http://a.aipage.net/detail/x.html"):
    _c13._note_deep_block(_u13, _deep_reason)
_s13b = _c13.stats()
check("13b2 同主域三家商铺合成一行、别的主域各开一行：账按每家都记到（4 个主机）",
      len(_lines2) == 2 and len(_s13b["deep_block"]) == 4
      and {"sgjjskjc.cn.makepolo.com", "gulnag.cn.makepolo.com"} <= set(_s13b["deep_block"])
      and "cn.makepolo.com" in _lines2[0] and "a.aipage.net" in _lines2[1],
      f"行数={len(_lines2)} 行={_lines2} 账={_s13b['deep_block']}")
check("13b2- 聚合键＝最后三段（≈主域）：商铺子域并到 cn.makepolo.com，"
      "三段主机原样保留，空主机不炸（键算错会把别的主域一起吞掉）",
      _c13._deep_log_key("sgjjskjc.cn.makepolo.com") == "cn.makepolo.com"
      and _c13._deep_log_key("a.aipage.net") == "a.aipage.net"
      and _c13._deep_log_key("www.11467.com") == "www.11467.com"
      and _c13._deep_log_key("") == "",
      f"{_c13._deep_log_key('sgjjskjc.cn.makepolo.com')} "
      f"{_c13._deep_log_key('a.aipage.net')}")
_lines3 = []
_c13._log = lambda msg, *a: _lines3.append(msg)
_c13.reset_run_state()
_c13._note_deep_block("http://newshop.cn.makepolo.com/contact_us.html", _deep_reason)
check("13b2= reset 之后同主域必须重新开口（跨轮静音＝下一轮真不通没人知道），"
      "聚合凭据与账一起清零",
      len(_lines3) == 1 and _c13._deep_logged == {"cn.makepolo.com"}
      and list(_c13._deep_blocks) == ["newshop.cn.makepolo.com"],
      f"行数={len(_lines3)} 凭据={_c13._deep_logged} 账={list(_c13._deep_blocks)}")
check("13b2== 反向对照：聚合凭据不在 reset_run_state 里清零的话，第二轮就永远静音",
      "self._deep_blocks = {}" in _rs13 and "_deep_logged" in _rs13,
      "reset_run_state 没清 _deep_logged")
_c13._note_request_error(_deep_reason)          # 收尾那行的深链段与 page_fail 无关，先清
_c13._last_page_error = ""
_c13.reset_run_state()
check("13b= reset_run_state 把深链账与通道三件（探测页/分诊/Retry-After）一起清零",
      _c13.stats()["deep_block"] == {} and _c13._block_probe_url == ""
      and _c13._block_kind == "" and _c13._retry_after == 0
      and not _c13._block_reason,
      f"probe={_c13._block_probe_url!r} kind={_c13._block_kind!r} retry={_c13._retry_after}")
_crawl_src13 = "\n".join(_ast.get_source_segment(_HTTP_SRC["cdp_base.py"], n) or ""
                         for n in _ast.walk(_ast.parse(_HTTP_SRC["cdp_base.py"]))
                         if isinstance(n, _ast.FunctionDef) and n.name == "crawl")
check("13b== 收尾账目格把深链被拒念出来（只记 stats 不渲染＝用户仍然看不见）",
      's.get("deep_block")' in _crawl_src13 and "深链被拒" in _crawl_src13,
      "收尾那行找不到深链段")

# 13c 分账把手：四档各有归属（深链×状态码 / 深链×连接 / 入口×任意 / 深链×无异常）
for _nm, _deep, _txt, _want in (
        ("深链·状态码级被拒", True, "HTTP 403（深链被拒，通道入口未受影响）", False),
        ("深链·连接类死法", True, "【连不上·无应答】b2b.11467.com:443｜Connection timed out", True),
        ("入口·状态码级被拒", False, "HTTP 502 站点返回 502", True),
        ("深链·没有异常可记", True, "", False)):
    _c13.reset_run_state()
    _got = _c13._note_fetch_fail(_txt, _deep)
    _st = _c13.stats()
    check(f"13c {_nm}：{'该' if _want else '不该'}进通道取页账"
          f"（{'连不上是主机属性，照记' if _want else '被拒是那一家/那条路的属性'}）",
          _got is _want and bool(_st["last_page_error"]) is _want,
          f"返回={_got} last_page_error={_st['last_page_error']!r}")
_c13.reset_run_state()
_c13._note_fetch_fail("【连不上·无应答】shop9.cn.makepolo.com:80｜Connection timed out", True)
check("13c- 深链的连接类死法同样累计熔断连击（否则只在大站深链上暴露的断路永远拉黑不了）",
      _c13._dead_strikes.get("shop9.cn.makepolo.com") == 1, str(_c13._dead_strikes))

# 13d 结构判据：每张登记表的 helper 都真分了账（正对照＝仓库现状，反对照＝旧写法）
_bad13 = {}
for _f13, _hs13 in _DEEP_AWARE.items():
    _p = _bad13.setdefault(_f13, [])
    _p.extend(_deep_wiring_problems(_HTTP_SRC[_f13], _hs13))
    if "deep=True" not in _fn_seg(_HTTP_SRC[_f13], "crawl_detail"):
        _p.append("crawl_detail 里没有一处 deep=True（deep 形参成了空转的旋钮）")
_bad13 = {k: v for k, v in _bad13.items() if v}
check(f"13d {len(_DEEP_AWARE)} 站取页层都按「深链/入口」分账（{sum(len(v) for v in _DEEP_AWARE.values())} 个 helper）",
      not _bad13, str(_bad13))
check("13d- 反向对照：把 deep 参数删掉 / 不表态 deep= / 绕过把手，三种旧写法都要响",
      bool(_deep_wiring_problems('class A:\n    def _fetch(self, url, deep=False):\n'
                                 '        b = self.http_block_reason(403, "", url, deep=deep)\n'
                                 '        self._note_request_error("x")\n        return ""\n',
                                 ["_fetch"])
      and _deep_wiring_problems('class A:\n    def _fetch(self, url):\n'
                                '        b = self.http_block_reason(403, "", url, deep=False)\n'
                                '        self._note_fetch_fail("x", deep)\n        return ""\n',
                                ["_fetch"])
      and _deep_wiring_problems('class A:\n    def _fetch(self, url, deep=False):\n'
                                '        self._block_reason = "x"\n        return ""\n',
                                ["_fetch"])))

# 13e 总表逐字对拍：谁会把被拒升级成通道账，必须当场登记（同类查全的门）
_now13 = {p.name: _block_writers(s) for p, s in
          ((HERE / n, src) for n, src in _HTTP_SRC.items() if n.endswith("_crawler.py"))
          if _block_writers(s)}
check("13e 全站『升级通道账』的函数总表与登记表完全一致（新增一处必须先来过目）",
      _now13 == _BLOCK_WRITERS, f"现算={_now13}")
check("13e- 反向对照：一个把深链 403 写进通道账的新函数会被现算抓到",
      _block_writers('class A:\n    def _fetch_detail(self, u):\n'
                     '        b = self.http_block_reason(403, "", u)\n'
                     '        self._block_reason = b\n        return ""\n') == {"_fetch_detail"})

# 13f 豁免三站：理由必须仍然成立（不成立就要迁进登记表，而不是把门关掉）
_waive_bad = []
for _f13, _why in _DEEP_WAIVED.items():
    _src13 = _HTTP_SRC[_f13]
    _det = _fn_seg(_src13, "crawl_detail")
    if _f13 == "gi_crawler.py":
        if "self._fetch(" in _det:
            _waive_bad.append("gi：详情开始发请求了，豁免理由失效 → 必须接入 deep 分账")
        _ov = _fn_seg(_src13, "http_block_reason")
        if "deep=False" not in _ov.split("\n", 1)[0] or "deep=deep" not in _ov:
            _waive_bad.append("gi：http_block_reason 覆写没收 deep 或没透传给基类（基类加参数就失联）")
    elif _block_writers(_src13):
        _waive_bad.append(f"{_f13}：出现了写通道账的函数，豁免理由（{_why}）已不成立")
check("13f 豁免三站的理由逐条仍然成立（豁免表不是永久免检）", not _waive_bad, str(_waive_bad))

# 13g 通路取证固化：马可波罗那三条实测结论不许在代码里悄悄翻案
_mpsrc = _HTTP_SRC["makepolo_crawler.py"]
check("13g 产品页走 http（https 那张证书是 WAF 默认证书，hostname 对不上→每次都 SSLError）",
      'PRODUCT_URL = "http://china.makepolo.com' in _mpsrc and "volcwaf" in _mpsrc,
      "PRODUCT_URL 或取证注释不见了")
check("13g- 联系页被拒时有第二条腿（商铺首页 12/12 全 200 是实测样本，不是猜的）",
      "SHOP_HOME_URL = " in _mpsrc and "SHOP_HOME_URL.format(slug=slug)" in _mpsrc
      and "12/12" in _mpsrc, "第二条腿或它的实机样本量被删了")
check("13g= 图片码那条路的死因不再被吞（停用日志必须带上最后一次为什么）",
      "img_last" in _mpsrc and "最后一次：%s" in _mpsrc
      and "except Exception:\n                body = b\"\"" not in _mpsrc,
      "码图的 except 又变回吞原因了：停用图片码时用户只会看到「不放号码图」")
# 13h 体检器的判据盲区：有产出那一侧过去只看「>0」，4 家与 62 家同样打 ✅
# ——2026-11-15 第四轮马可波罗整轮 4 家、白等 765 秒，报告当时给的就是 ✅
#   「4 家 / 命中 4 / 差分与来源戳 OK」。深链账与同轮参照这两格就是为了堵这个洞。
_mk13 = {"page_fail": 0, "listed": 30, "failed": 0,
         "deep_block": {"cn.makepolo.com": [37, "HTTP 403（深链被拒，通道入口未受影响）"]}}
_h13, _v13 = _live._attrib(_mk13, 4, False, "", diff="unknown", peer=40)
check("13h 深链账 + 产出显著低于同轮参照：必须红，且把证据（哪台主机、多少次）带进结论",
      _h13 == "❌" and "深链被拒" in _v13 and "cn.makepolo.com" in _v13 and "37" in _v13,
      _v13)
_h13b, _v13b = _live._attrib({"page_fail": 0, "listed": 40, "failed": 30}, 5, False, "",
                             diff="ok", peer=40)
check("13h- 详情失败造成的低产出同样要红（少了 35 家不是平台没货，是落地这条腿坏了）",
      _h13b == "❌" and "详情失败" in _v13b, _v13b)
_h13c, _v13c = _live._attrib({"page_fail": 0, "listed": 12}, 3, False, "", diff="ok", peer=40)
check("13h-- 账上没证据的低产出只给 ⚠（把「平台本来货少」也判成缺陷，下一轮就没人信这份报告）",
      _h13c == "⚠" and "无异常证据" in _v13c, _v13c)
_h13d, _v13d = _live._attrib(_mk13, 4, False, "", imp=4)
check("13h--- 反向对照（旧口径本身）：不传差分状态与参照时旧判据确实放行——"
      "所以「产出>0 即合格」这句话必须由夹具钉死不许复活"
      "（这一条必须显式传 imp：复算不成时新判档会拦下来，那就不是在验旧口径而是在验新档）",
      _h13d == "", f"{_h13d} {_v13d}")

# 13i 差分三态：跑不成 ≠ 跑通过
_d_ok = _live._diff_judge({"泵": {"A", "B", "C"}, "生物质锅炉": {"D", "E", "F"}}, ["泵", "生物质锅炉"])[0]
_d_sus = _live._diff_judge({"泵": {"A", "B", "C"}, "生物质锅炉": {"A", "B", "D"}}, ["泵", "生物质锅炉"])[0]
_d_unk = _live._diff_judge({"泵": {"A", "B"}}, ["泵", "生物质锅炉"])[0]
check("13i 差分三态分得开：显著不同=ok、高度重合=suspect、第二词没出货=unknown（不是 ok）",
      _d_ok == "ok" and _d_sus == "suspect" and _d_unk == "unknown",
      f"{_d_ok}/{_d_sus}/{_d_unk}")
_sus13 = _live._attrib({"page_fail": 0}, 60, False, "", diff="suspect", peer=60)
check("13i- 假命中（suspect）在 _attrib 里必须比产出高低优先判红，且结论说「不能用」",
      _sus13[0] == "❌" and "不能用" in _sus13[1], str(_sus13))
check("13i== 旧的假安心文案「差分与来源戳 OK」不许复活（那行字在差分没跑成时是谎）",
      "差分与来源戳 OK" not in _LIVE_SRC and "差分 OK" in _LIVE_SRC)

# 13j 同轮参照的算法性质：中位数、剔 0、样本不足不装懂
check("13j 参照取中位数不是取最大值（某站恰好爆量会把基线抬到谁都过不了）",
      _live._peer_baseline([4, 22, 33, 40, 47, 62, 74]) == 40, str(_live._peer_baseline([4, 22, 33, 40, 47, 62, 74])))
check("13j- 样本不足两站时返回 0（没有参照就不判产出高低，比硬造一个基线诚实）",
      _live._peer_baseline([47]) == 0 and _live._peer_baseline([]) == 0
      and _live._peer_baseline([0, 0]) == 0)


def _rep(slug, n, blocked=False, waiting="", err="", imp=None, imp_err=""):
    """按 main 里 reports 元组的字段顺序造一条假读数（下标 0/2/5/7/9/11 被 _peer_for 读到）。
    imp=None＝这一路没复算入库口径（真读数里只有「没有清洗映射」或复算异常才会是 None），
    此时参照退回原始家数——与 _peer_for 的口径一致。imp_err 是复算不成的原因（第 15 格）。"""
    return (slug, slug, n, n, 0, blocked, "", {"waiting": waiting}, "ok", err, "", imp, [], "",
            imp_err)


check("13k 参照剔掉自己/拦停/等人/读数失败四种（每种留下的 0 家都会把满轮正常的站判成异常）",
      _live._peer_for([_rep("a", 4), _rep("b", 60), _rep("c", 70), _rep("d", 80)], "a") == 70
      and _live._peer_for([_rep("a", 4), _rep("b", 0, blocked=True), _rep("c", 70),
                           _rep("d", 80)], "a") == 75
      and _live._peer_for([_rep("a", 4), _rep("b", 0, waiting="登录"), _rep("c", 70),
                           _rep("d", 80)], "a") == 75
      and _live._peer_for([_rep("a", 4), _rep("b", 0, err="boom"), _rep("c", 70),
                           _rep("d", 80)], "a") == 75,
      f"含自己={_live._peer_for([_rep('a', 4), _rep('b', 60), _rep('c', 70), _rep('d', 80)], 'a')}")
check("13k- 反向对照：把「自己」留在参照里（旧写法）基线就会被异常低的这一路亲手拉低",
      _live._peer_baseline([4, 60, 70, 80]) != _live._peer_for(
          [_rep("a", 4), _rep("b", 60), _rep("c", 70), _rep("d", 80)], "a"))
# 13k= 参照基数换成「可入库数」：抓到 60 家、能入库 3 家那种路，不许再拿 60 去抬基线，
#      也不许因为它原始数够高就躲过产出异常判定（马可波罗 51 家/可入库 2 家的真实形状）。
_p16peer = _live._peer_for([_rep("a", 4), _rep("b", 60, imp=3), _rep("c", 70, imp=4),
                            _rep("d", 80, imp=5)], "a")
check("13k= 同轮参照按可入库数算，不是按抓到的家数算",
      _p16peer == 4 and _live._peer_baseline([60, 70, 80]) == 70,
      f"imp 基数={_p16peer}｜原始基数={_live._peer_baseline([60, 70, 80])}")

# 13l 报告渲染：深链账要单独成行、差分状态要跟着返回值走，读数只问 stats
_f13 = _fn_seg(_LIVE_SRC, "_fmt_site")
check("13l 体检报告把深链被拒单独念成一行（自第 ㉓ 节起这一行只由 `_host_reason_line` 写出），"
      "并把差分状态随元组返回（判定不回头猜文案）",
      "deep_block" in _f13 and '_host_reason_line("深链被拒"' in _f13
      and 'f"  {title}: "' in _LIVE_SRC
      and _f13.rstrip().endswith("imp_n, zero, _legs, imp_err)"),
      _f13.rstrip()[-90:])
# 反向对照不在这里重抄一份：「把那一行改回就地 [:46]」那一形由 23h/23j- 直接拿真把手跑，
# 这里再写一条「替换后字符串不在原文里」式的永真断言，只会多一条骗过所有人的绿。
check("13l- BLOCK_WORDS 认得「深链被拒」这一行（这一族的原因可能只有「访问过于频繁」而没有"
      " 403，被关键字表筛掉就等于把唯一能定性的证据扔了）",
      bool(re.search(r"BLOCK_WORDS\s*=.*深链被拒", _LIVE_SRC, re.S)))
check("13l-- main 的 ✅ 那行只念真判过的格子（没有参照时明说「未判」，不许写「相当」）",
      "同轮参照不足" in _fn_seg(_LIVE_SRC, "main")
      and "产出与同轮参照" in _fn_seg(_LIVE_SRC, "main"))
print("⑬ 深链被拒 vs 通道级拦停分账：一家商铺的 403 不再钉死整条通道 ✓")

# ════════════════════════════════════════════════════════════════════════
# ⑭ 逐词相关性砍单账：某一词零产出时，「本机闸门砍的」与「平台真没货」必须分得开
# 由来（2026-11-15 第五轮实机，世界工厂网关键词「橡胶手套」）：站方回了 7 条列表条目，
# 7 条全被详情复核判「类目不符」，而日志写的是「⚠ 「橡胶手套」无新增产出（被拦/链路未通/
# 本页无数据）」，体检报告跟着写「差分没跑成，建议换词复跑」——三条处方没有一条指向真正
# 该动的地方（相关性口径/同族类目桥）。这是「归因分档」这一族的第五个缺陷，也是唯一一个
# 出在有产出那一侧的：产出总量正常（39 家），坏的是**某一个词被整词砍光**这件事看不见。
# 口径：两本按词账（kw_early＝列表页早过滤，kw_filtered＝详情页复核）由唯一把手写，
#      收尾归因、stats 读数、体检器三处都只问这两本账，不许再猜日志文案。
# ════════════════════════════════════════════════════════════════════════
_SRC14 = dict(_HTTP_SRC)
_SRC14["ali1688_crawler.py"] = (HERE.parent / "ali1688_build" / "ali1688_crawler.py").read_text(
    encoding="utf-8")
# 1688 不在 b2bcollect_build 下，但同属交付链，同类查全必须把它算进扫描集
_SRC14["site_adapters.py"] = _HTTP_SRC["site_adapters.py"]


def _aug_sites(src):
    """源码对 `_early_skipped`/`_filtered_count` 做 `+=` 的函数名（把手本体走登记表放行）。"""
    out = []
    for fn in (n for n in _ast.walk(_ast.parse(src)) if isinstance(n, _ast.FunctionDef)):
        for st in (s for s in _ast.walk(fn) if isinstance(s, _ast.AugAssign)):
            t = st.target
            if isinstance(t, _ast.Attribute) and t.attr in ("_early_skipped", "_filtered_count"):
                out.append(f"{fn.name}:{t.attr}")
    return out


_crawl_src14 = "\n".join(
    _ast.get_source_segment(_SRC14["cdp_base.py"], n) or ""
    for n in _ast.walk(_ast.parse(_SRC14["cdp_base.py"]))
    if isinstance(n, _ast.FunctionDef) and n.name == "crawl")


_ALLOWED_AUG = {"_note_early_skip:_early_skipped", "_note_detail_filter:_filtered_count"}
_bad14 = {n: sorted(set(_aug_sites(s)) - _ALLOWED_AUG) for n, s in _SRC14.items()}
_bad14 = {k: v for k, v in _bad14.items() if v}
check(f"14c 相关性计数只许由把手写（扫了 {len(_SRC14)} 份交付链源码，裸 += 一律算未登记）",
      not _bad14, str(_bad14))
# 反向对照要按**同一把尺子**量：把手本体里 += 是合法的（那本来就是唯一记账口），
# 调用点里 += 才是未登记。所以判据必须是「减掉登记表还剩不剩」，不是「扫不扫得到」。
check("14c- 反向对照：调用点裸 += 当场响；同样一行搬进把手本体就合法",
      set(_aug_sites('class A:\n    def _fetch(self, kw):\n        self._early_skipped += 1\n'
                     '        return 1\n')) - _ALLOWED_AUG
      and not set(_aug_sites('class A:\n    def _note_early_skip(self, kw):\n'
                             '        self._early_skipped += 1\n        return 1\n')) - _ALLOWED_AUG)

# 14a 把手行为：两本账各自按词累加，全局数同步，reset 一起清零
_c14 = B2b168Crawler()
_c14.reset_run_state()
_c14._note_early_skip("橡胶手套")
_c14._note_early_skip("橡胶手套")
_c14._note_detail_filter("泵")
_s14 = _c14.stats()
check("14a 早过滤与复核分两本按词账，全局数同步（只有全局数就分不开「砍的」与「没货」）",
      _s14["kw_early"] == {"橡胶手套": 2} and _s14["kw_filtered"] == {"泵": 1}
      and _s14["early"] == 2 and _s14["filtered"] == 1,
      f"early={_s14['kw_early']} filtered={_s14['kw_filtered']}")
_c14.reset_run_state()
check("14a- reset_run_state 把两本按词账一起清零（跨轮留着＝上一轮的判定冒充本轮归因）",
      _c14.stats()["kw_early"] == {} and _c14.stats()["kw_filtered"] == {},
      str(_c14.stats()["kw_early"]))

# 14b 归因把手：三种账各说什么（这一句是收尾日志里唯一决定处方方向的话）
check("14b 账上没有砍单时返回空串：调用方必须退回旧那句，不许编归因",
      _c14._kw_zero_reason("没跑过的词") == "", repr(_c14._kw_zero_reason("没跑过的词")))
_c14._note_early_skip("橡胶手套")
_c14._note_early_skip("橡胶手套")
_c14._note_detail_filter("橡胶手套")
# 条目账也得记上：第十三轮之前这一句是拿「只有砍单数」的账念出来的，而那种账下
# 「只回 1 条」与「回 30 条全被砍」长得一模一样（四档分叉见本节 26b）
_c14._note_listed("橡胶手套", 12)
_r14 = _c14._kw_zero_reason("橡胶手套")
check("14b- 有砍单时说清阶段与家数，并明说「不是平台没数据」＋该改判定口径",
      "列表页早过滤砍 2 家" in _r14 and "详情页复核砍 1 家" in _r14
      and "不是平台没数据" in _r14 and "相关性口径" in _r14, _r14)
check("14b-- 同一句里必须连同「站方回了几条」一起念（只有砍单数时，个位数货源与全被砍两种局"
      "长得一模一样，处方正好相反）", "累计回 12 条" in _r14 and "另外 9 条没被砍" in _r14, _r14)
check("14b== 反向对照：旧那三档里根本没有「相关性」这一档（本轮要堵的就是这个洞）",
      "相关性" not in "被拦/链路未通/本页无数据"
      and 'self._kw_zero_reason(kw)' in _crawl_src14
      and "无新增产出（被拦/链路未通/本页无数据）" in _crawl_src14,
      "crawl 收尾分支没接把手，或旧那句被删掉了")

# 14d/14e 体检器侧：diff=unknown 时有没有砍单账决定 ❌/⚠ 与处方方向
_k14 = _live._kw_starve({"kw_filtered": {"橡胶手套": 7}}, "橡胶手套")
check("14d 体检器读得到逐词砍单账（与 cdp_base 同一套键名，十路一把尺子）；条目账没记上时"
      "它必须自己承认「说不出站方回了几条」（拿没记账的数冒充货量是反方向的谎）",
      _k14 == "详情页复核砍 7 家、站方回货：条目账没记上（说不出这一词站方回了几条）"
      and _live._kw_starve({}, "橡胶手套") == "", repr(_k14))
check("14d+- 条目账记上时，同一句要念出自报与解析两个数（两种局处方相反）",
      _live._kw_starve({"kw_filtered": {"k": 7}, "kw_items": {"k": 7},
                        "kw_claimed": {"k": 30}}, "k")
      == "详情页复核砍 7 家、站方回货：自报 30 条、解析到 7 条"
      and _live._kw_starve({"kw_items": {"k": 9}}, "k") == ""
      and _live._kw_starve({"kw_early": {"k": 2}, "kw_claimed": {"k": 2}}, "k")
      == "列表页早过滤砍 2 家、站方回货：自报 2 条",
      _live._kw_starve({"kw_filtered": {"k": 7}, "kw_items": {"k": 7}}, "k"))
_m14, _v14 = _live._attrib({"listed": 47, "kw_filtered": {"橡胶手套": 7}}, 39, False, "",
                           diff="unknown", peer=60, killed="「橡胶手套」" + _k14)
check("14d- 差分没跑成＋账上有砍单：必须红，处方改成「判定口径」，不许再劝人换词复跑",
      _m14 == "❌" and "相关性闸门砍掉" in _v14 and "判定口径" in _v14
      and "换词复跑" not in _v14, f"{_m14} {_v14}")
_m14b, _v14b = _live._attrib({"listed": 40}, 39, False, "", diff="unknown", peer=60)
check("14d== 反向对照：账上没有砍单时旧那句 ⚠ 必须原样保留（新归因不许把旧档吞掉）",
      _m14b == "⚠" and "换词复跑" in _v14b, f"{_m14b} {_v14b}")
_m14c, _v14c = _live._attrib({"listed": 40, "kw_early": {"橡胶手套": 9}}, 39, False, "",
                             diff="unknown", peer=60, killed="「橡胶手套」列表页早过滤砍 9 家")
check("14e 早过滤砍光这一档同样要说出来（这一类连详情页都没进，取证只能来自 kw_early）",
      _m14c == "❌" and "早过滤" in _v14c, f"{_m14c} {_v14c}")
_m14d, _v14d = _live._attrib({"listed": 40, "kw_filtered": {"数控机床": 60}}, 4, False, "",
                             diff="ok", peer=60, killed="「数控机床」详情页复核砍 60 家")
check("14e- 低产出那一档也把砍单当决定性证据（旧版只认深链/取页/详情失败三种）",
      _m14d == "❌" and "相关性砍单" in _v14d and "闸门砍的" in _v14d, f"{_m14d} {_v14d}")

# 14f 深链账的渲染聚合（马可波罗每家商铺一个子域：按主机念＝3000 字一坨）
_d14 = _live._deep_group({f"shop{i}.cn.makepolo.com": [1, "HTTP 403（这一家的这条资源不给看）"]
                          for i in range(50)})
check("14f 深链账按主域聚合渲染：50 家商铺并成一句话，条数照加",
      list(_d14) == ["cn.makepolo.com"] and _d14["cn.makepolo.com"][0] == 50, str(_d14))
check("14f- 聚合键必须复用 cdp_base 那一份（两处各算一遍，口径早晚会自己漂移）",
      "_deep_log_key" in _fn_seg(_LIVE_SRC, "_deep_group"))
check("14f== 反向对照（旧渲染）：按主机逐家拼接的那句 3000 字文案不许复活在体检器里",
      "sorted(_deep.items())" not in _fn_seg(_LIVE_SRC, "_fmt_site")
      and "_deep_group(" in _fn_seg(_LIVE_SRC, "_fmt_site"))

# 14f2 同一本账两种念法（2026-11-16 第十二轮实机 马可波罗）：体检器那边 14f 早就按主域
# 聚合了，采集器收尾那一行却还在按主机逐家拼接——46 家商铺念出 1254 字，读数的人读不动，
# 「这一条腿今天不通」就又回到没人看见。两边必须共用同一个聚合键、同一个把手。
_AH14 = {f"shop{i}.cn.makepolo.com": [1, "HTTP 403（这一家的这条资源不给看）"] for i in range(46)}
_AH14["a.aipage.net"] = [3, "HTTP 403"]
_acct14 = _c14._host_account_line(_AH14, "深链被拒")
check("14f2 采集器收尾那一行也按主域聚合：46 家商铺并成一段、次数照加（旧写法同输入 1254 字）",
      _acct14 == "｜深链被拒 cn.makepolo.com 46 次、a.aipage.net 3 次"
      and len(_acct14) < 60
      and len("｜深链被拒 " + "、".join(f"{h} {v[0]} 次" for h, v in sorted(_AH14.items()))) > 1000,
      _acct14)
check("14f2- 段内按次数降序：把只挂 1 次的那家排在最前，读的人第一眼看到的就是最不重要的那条",
      _c14._host_account_line({"a.x.com": [1, "r"], "b.x.com": [9, "r"],
                               "c.y.com": [4, "r"]}, "落点不符")
      == "｜落点不符 b.x.com 9 次、c.y.com 4 次、a.x.com 1 次")
check("14f2== 空账返回空串：调用方据此**不说**这一格（拿「｜深链被拒 0 次」顶上去＝凭空造一格）",
      _c14._host_account_line({}, "深链被拒") == ""
      and _c14._host_account_line(None, "深链被拒") == "")
check("14f2=== 聚合键只认 _deep_log_key 那一份（14f- 已把体检器那一侧钉住，这一条钉采集器这一侧）",
      "_deep_log_key" in _fn_seg(_HTTP_SRC["cdp_base.py"], "_host_account_line"))
_TITLE14 = ("深链被拒", "落点不符")


def _acct_off_helper(src_text):
    """收尾那两笔主机账「念出来时没走把手」的那几处：标题字面量只许出现在
    `_host_account_line(...)` 的实参里。按 AST 认，注释里的旧写法不算（同 23g 那条教训：
    把旧写法写进注释就集体失灵）。"""
    _t14o = _ast.parse(src_text)
    _p14o = {}
    for _n14o in _ast.walk(_t14o):
        for _c14o in _ast.iter_child_nodes(_n14o):
            _p14o[_c14o] = _n14o
    bad = []
    for _n14o in _ast.walk(_t14o):
        if not (isinstance(_n14o, _ast.Constant) and isinstance(_n14o.value, str)
                and any(x in _n14o.value for x in _TITLE14)):
            continue
        _par14 = _p14o.get(_n14o)
        if (isinstance(_par14, _ast.Call) and isinstance(_par14.func, _ast.Attribute)
                and _par14.func.attr == "_host_account_line"):
            continue
        bad.append((_n14o.lineno, _n14o.value[:24]))
    return bad


check("14f2==== 结构判据：收尾那两笔主机账只许由把手念出来（退回就地拼接、或把手换个名字，都当场响）",
      not _acct_off_helper(_crawl_src13), str(_acct_off_helper(_crawl_src13)))
_old14f2 = ('if s.get("deep_block"):\n    lk += "｜深链被拒 " + "、".join(\n'
             '        f"{h} {v[0]} 次" for h, v in sorted(s["deep_block"].items()))\n')
# 「把手改名」那一档的输入单独提成变量：它内部全是双引号，塞进 f-string 的花括号里就得写 \"，
# 而**花括号里带反斜杠是 Python 3.12 才放开的写法**——CI 打包机按 workflow 声明的 3.11 装解释器，
# 上一版就是这一句让第 26/27 条「读不到这一份」而整条流水线停在构建前硬门。判据见第 34 条。
_renamed14f2 = 'lk += self._acct(s.get("deep_block"), "深链被拒")\n'
check("14f2===== 判据不是空跑：修复前那三行与「把手改名」两种形状喂给同一条判据都必须响并点出行号"
      "（永真式反向对照比没有更坏：下一次真分叉会混在噪音里）",
      _acct_off_helper(_old14f2) == [(2, "｜深链被拒 ")]
      and _acct_off_helper(_renamed14f2) == [(1, "深链被拒")],
      f"{_acct_off_helper(_old14f2)} ↔ {_acct_off_helper(_renamed14f2)}")

# 14g 同族类目桥：这一词被砍光的根因修复（实机取证写进表注释，泛词一律不收）
import keyword_expander as _ke14                                    # noqa: E402
_pe14 = _ke14.peer_words("橡胶手套")
check("14g 「手套」族桥接上：乳胶/防护/劳保这类类目层写法算同族（实机砍掉的湖南奥龙就是这类）",
      "个体防护" in _pe14 and "乳胶手套" in _pe14 and "劳保用品" in _pe14, str(_pe14))
_c14.relevance_guard = True
check("14g- 桥生效的正对照：主营只写行业大类的乳胶制品厂判为相关",
      _c14.is_relevant("橡胶手套", "湖南省奥龙时代乳胶制品有限公司 工业防护,防护装备,个体防护"))
check("14g== 反向对照（泛词不许放宽）：同一批里「地桩,餐车,滚筒刷」「包装膜胶带」仍判不相关——"
      "桥表只收类目层词，收泛词等于把垃圾商家放进来",
      not _c14.is_relevant("橡胶手套", "阳东县江华实业有限公司 地桩,新能源智能餐车,滚筒刷")
      and not _c14.is_relevant("橡胶手套", "河南省蓝天医疗器械有限公司 棉花包装膜胶带,不干胶原材料"))
check("14g=== 机床族桥不许被这次新增挤掉（老证据必须还在表里）",
      "加工中心" in _ke14.peer_words("数控机床") and "电火花" in _ke14.peer_words("机床"))

# 14h 1688 那一路（不在 b2bcollect_build 下，但同属交付链）必须同口径
_a14 = _SRC14["ali1688_crawler.py"]
check("14h 1688 同样接上两本按词账（十路一体：缺一格就把「砍光这一词」报成「1688 没货」）",
      "_note_early_skip" in _a14 and "_note_detail_filter" in _a14
      and "self.kw_early[kw]" in _a14 and "self.kw_filtered[kw]" in _a14,
      "1688 侧把手缺失")
check("14h- 适配器的 stats() 必须把两把键透出去（不给＝体检器对 1688 永远读不到这一格）",
      "kw_filtered" in _1688_stats_src and "kw_early" in _1688_stats_src,
      "Ali1688Site.stats() 没透两本按词账")
# 把手调用点数量本身就是回归线：早过滤每站一处（9 站＋1688 两处＝11 个调用点），
# 详情复核只在两个基类里做（cdp_base 一处＋1688 两处）。
# 而且从本轮起**调用点必须把公司名传进去**：只有一本计数时，那句处方（「该复核判定口径」）
# 执行不了——复核的人手上没名单（2026-11-16 第十轮实机：三个 ❌ 全站都只说「该复核」）。
_CALL14_EARLY = re.compile(r"_note_early_skip\(kw,\s*[^\s)]")
_CALL14_DETAIL = re.compile(r"_note_detail_filter\(kw,\s*[^\s)]")
_early_calls = {n: len(_CALL14_EARLY.findall(s)) for n, s in _SRC14.items()}
_detail_calls = {n: len(_CALL14_DETAIL.findall(s)) for n, s in _SRC14.items()}
check("14h== 每一路交付链源码都要有把手，且调用点必须传公司名（只给计数不给名单的处方落不了地）",
      sum(_early_calls.values()) >= 11 and sum(_detail_calls.values()) >= 3,
      f"early={sum(_early_calls.values())} detail={sum(_detail_calls.values())} "
      f"{({k: v for k, v in _early_calls.items() if v})}")
# 反向对照：把某一站的传名改成裸调用，回归线必须当场把它名出来（不然这一条只是今天对了）
_bare14 = _SRC14["gys_crawler.py"].replace(
    "self._note_early_skip(kw, e[\"公司名称\"])", "self._note_early_skip(kw)")
check("14h=== 反向对照：同一把尺子量「裸调用」必须归 0（不然下一站漏传名字没人拦）",
      _bare14 != _SRC14["gys_crawler.py"] and not _CALL14_EARLY.search(_bare14),
      "克隆没生效或判据读不到裸调用")

print("⑭ 逐词相关性砍单账：某一词被闸门砍光不再被说成「平台没货」 ✓")

# ═══════════════════════════════════════════════════════════════════════
# ⑮ 判定规则版本化：改了相关性口径，种子库里旧的「类目不符」盖章必须作废重判
# 由来（2026-11-15 补「手套」族桥后自查）：第 ⑭ 节把归因修准了，但**修好的口径对
# 已经被误杀入库的那批家一个字都不生效**——早过滤砍掉时按「关键词#主体」写进种子库，
# 下一轮命中即跳过，连判定都不再走一次。增量缓存的是**结论**不是**事实**，规则升级
# 不让旧结论作废，用户就只能手动清库（等于把修复作废）。
# 口径：判定规则指纹（桥表+规则函数源码）写进条目 rv；rv 不配的「类目不符」不再锁定；
# 单轮重判限量（补旧账不许挤掉本轮新货）；改判相关并入库时旧章当场撕掉（否则永远收敛不了）。
# ═══════════════════════════════════════════════════════════════════════
_c15 = B2b168Crawler()
_rev15 = _c15._relevance_revision()
check("15a 规则指纹取得到、且同一段代码连取两次同值（每轮算出不同版本号＝种子库天天被重洗）",
      _rev15 != "rev0" and len(_rev15) == 12 and _c15._relevance_revision() == _rev15,
      repr(_rev15))
_ke15 = sys.modules["keyword_expander"]
_ke15.PEER_CATEGORY["__探针族__"] = ("甲类", "乙类")
type(_c15)._REL_REV = None
_rev15b = _c15._relevance_revision()
del _ke15.PEER_CATEGORY["__探针族__"]
type(_c15)._REL_REV = None
check("15b 桥表加一族（本轮就干了这件事）＝指纹必变；撤掉探针后指纹回到原值（不是每次取都变）",
      _rev15b != _rev15 and _c15._relevance_revision() == _rev15,
      f"{_rev15} vs {_rev15b}")

_c15._seen_store = {
    "b2b168:橡胶手套#旧版无rv": {"at": "2026-11-14 10:00:00", "st": "类目不符(已过滤)"},
    "b2b168:泵#旧规则判的": {"at": "2026-11-14 11:00:00", "st": "类目不符(已过滤)", "rv": "zzzzzzzzzzzz"},
    "b2b168:泵#新规则判的": {"at": "2026-11-14 12:00:00", "st": "类目不符(已过滤)", "rv": _rev15},
    "b2b168:采过且齐了": {"at": "2026-11-14 13:00:00", "st": "成功", "ct": 1},
}
_lk15 = _c15._locked_keys()
check("15c 陈旧「类目不符」不再命中即跳过（旧版没写 rv 也算陈旧），新判定与正常记录照旧锁",
      _lk15 == {"b2b168:泵#新规则判的", "b2b168:采过且齐了"}, str(sorted(_lk15)))
check("15c- 陈旧名单不算「欠补联系人」（两本账各说各的，混了界面的待办数就是假的）",
      _c15._seen_store_pending() == 0, str(_c15._seen_store_pending()))


def _locked_keys_old(store):
    """旧口径原样请回来：种子库只要进过就永久免检（陈旧判定也锁着）。"""
    return {k for k, v in store.items() if CdpSiteCrawler._entry_locked(v)}


check("15c== 反向对照：旧 _locked_keys 在这张表上确实把两条陈旧判定一起锁死（15c 不是装饰）",
      _locked_keys_old(_c15._seen_store) - _lk15 ==
      {"b2b168:橡胶手套#旧版无rv", "b2b168:泵#旧规则判的"},
      str(sorted(_locked_keys_old(_c15._seen_store))))
_c15._commit_to_store({"状态": "类目不符(已过滤)", "关键词": "泵", "memberId": "旧规则判的"})
check("15d 重判后再判一次不符＝盖上带新指纹的章（同一轮不许反复重判同一家）",
      _c15._seen_store["b2b168:泵#旧规则判的"]["rv"] == _rev15,
      str(_c15._seen_store["b2b168:泵#旧规则判的"]))
_c15._commit_to_store({"状态": "成功", "关键词": "泵", "memberId": "旧规则判的",
                       "公司名称": "湖南奥龙乳胶制品", "电话": "0731-1234567"})
check("15e 改判相关并入库＝旧「类目不符」章当场撕掉（留着它每轮都进重判名单，永远收敛不了）",
      "b2b168:泵#旧规则判的" not in _c15._seen_store,
      str(sorted(_c15._seen_store)))

_st15 = {f"b2b168:泵#{i}": {"at": f"2026-11-{i:04d} 10:00:00",
                           "st": "类目不符(已过滤)", "rv": "zzzzzzzzzzzz"}
         for i in range(300)}
_c15._seen_store = dict(_st15)
_k15 = _c15._stale_filtered_keys()
check("15f 单轮重判有上限，且取的是判定时间最近的一批（一次性放开上千家＝风控预算全花在补旧账）",
      len(_k15) == _c15.REL_REFILTER_CAP == 200
      and _k15 == {f"b2b168:泵#{i}" for i in range(100, 300)}, f"{len(_k15)} 家")
_c15._seen_store = {f"b2b168:泵#{i}": {"at": f"2026-11-{i:04d} 10:00:00",
                                       "st": "类目不符(已过滤)", "rv": _rev15}
                    for i in range(50)}
check("15g 全新一轮（所有章都带当前指纹）＝重判名单空（这条路径只在规则真升级那轮才付代价）",
      _c15._stale_filtered_keys() == set(), str(len(_c15._stale_filtered_keys())))

# 15h 取不到规则指纹时的降级：宁可不做重评，也不许每轮算出不同版本号把库洗一遍
_ke_keep = sys.modules.get("keyword_expander")
sys.modules["keyword_expander"] = None      # 打包缺模块/导入被打断时的真实形态
type(_c15)._REL_REV = None
try:
    _rev15f = _c15._relevance_revision()
    _c15._seen_store = {"b2b168:泵#降级": {"at": "2026-11-14 10:00:00",
                                          "st": "类目不符(已过滤)", "rv": _rev15}}
    _lk15f = _c15._locked_keys()
finally:
    sys.modules["keyword_expander"] = _ke_keep
    type(_c15)._REL_REV = None
check("15h 规则取不到（模块缺失/源码读不出）＝退回常量并按「不重判」降级，绝不每轮漂移版本号",
      _rev15f == "rev0" and "b2b168:泵#降级" in _lk15f, f"{_rev15f} {_lk15f}")
check("15h- 指纹取一次就缓存（每轮重算＝每轮读一遍源码；同一进程内改表不生效是刻意的）",
      _c15._relevance_revision() == _rev15)

# 15i/15j 交付链接线：crawl 起账与扣账、stats 读数、1688 同口径
_st15src = _fn_seg(_SRC14["cdp_base.py"], "stats")
check("15i crawl 把重判家数记进本轮账，并从「联系人曾取空」那句里扣掉（不扣＝报错误的待办数）",
      "self._rel_refilter = len(self._stale_filtered_keys())" in _crawl_src14
      and "- self._rel_refilter" in _crawl_src14
      and "相关性判定规则已更新" in _crawl_src14)
check("15i- stats 透出 rel_refilter（体检器/界面问得到这一格，不靠读日志文案）",
      '"rel_refilter": self._rel_refilter' in _st15src, _st15src[:0])
_rs15 = _fn_seg(_SRC14["cdp_base.py"], "reset_run_state")
check("15j reset_run_state 把重判账清零（跨轮留着＝上一轮规则升级的账冒充本轮又重判了一遍）",
      "_rel_refilter" in _rs15 or "_rel_refilter" in _crawl_src14, _rs15[:0])
_a15 = _SRC14["ali1688_crawler.py"]
check("15k 1688 同口径接上（十路一体：这一路不接，改判后的旧盖章就永远免检）",
      "_stale_filtered_keys" in _a15 and "_relevance_revision" in _a15
      and "self._rel_refilter = len(self._stale_filtered_keys())" in _a15
      and '"rv": self._relevance_revision()' in _a15, "1688 侧未接线")
check("15k- 1688 的重判账同样进 stats（适配器读数与 cdp_base 同一把键）",
      "rel_refilter" in _1688_stats_src, "Ali1688Site.stats() 没透 rel_refilter")
check("15l 两边同用一枚 FILTERED_STATE 常量（判定作废认的是状态字面量，两处拼写漂移就认不出）",
      CdpSiteCrawler.FILTERED_STATE == _a1688.Ali1688Crawler.FILTERED_STATE == "类目不符(已过滤)"
      and "st == self.FILTERED_STATE" in _SRC14["cdp_base.py"]
      and "st == self.FILTERED_STATE" in _a15)
# 15m 十路共用一本日志：每行必须自带「是谁说的」。1688 那一以前只打时间戳，
# 实机体检日志里就会出现一句认不出主人的话（第 13 行「重新评估 31 家」）
_log15 = _fn_seg(_SRC14["ali1688_crawler.py"], "_log")
check("15m 1688 的日志前缀与九路同形（[时间][平台]）：并行混排时每一行都认得出是谁说的",
      "][{self.PLATFORM_NAME}] " in _log15, _log15)
check("15m- 反向对照：旧那句只打时间戳的写法不许复活（无主的行等于没记账）",
      "strftime('%H:%M:%S')}] {msg}" not in _log15, _log15)

print("⑮ 判定规则版本化：改了相关性口径，旧的「类目不符」盖章不再免检 ✓")

# ════════════════════════════════════════════════════════════════════════
# ⑯ 「这条数据能不能用」只许有一把尺子 + 漏斗必须按档说出原因
# 由来（2026-11-15/16 实机体检，马可波罗）：分站报告用「电话/手机/邮箱/公司网址任一」
# 把 51 家全判成命中并打 ✅，而生产漏斗按「手机号/固话/邮箱/微信号四项全空」剔掉 49 条，
# 汇总只留下一句「漏斗吞掉 50 条」。两件事各自都对，拼在一起就是没人能回答
# 「到底能不能采到数据」——弱的那把尺子负责打勾，吞掉的那一档却连分解都拿不出来。
# 口径：①判定与复算一律走 make_row + filter_import_rows（生产同源，探针不另写一份）；
#      ②build_clean_rows 把每一档的数写进 stats_out，clean_records 汇成 funnel_out；
#      ③体检器按档打印并点名「四项全空」属于哪一路；④两道判据（_has_contact 与
#      filter_import_rows）必须逐行同答案，对不上就是有人新刻了一把尺子。
# ════════════════════════════════════════════════════════════════════════
import customer_library_cleaner as _cl                                    # noqa: E402

_cl_src = (Path(__file__).resolve().parent / "customer_library_cleaner.py").read_text(
    encoding="utf-8")
_cx16 = B2b168Crawler()
_rows16 = [
    {"公司名称": "甲一机械有限公司", "手机": "13800001111", "数据来源": "八方资源网"},
    {"公司名称": "甲二机械有限公司", "电话": "0755-88889999", "数据来源": "八方资源网"},
    {"公司名称": "甲三机械有限公司", "公司网址": "http://www.jia3.example.com",
     "数据来源": "八方资源网"},      # 只有官网：宽口径算命中，入库口径算死数据
]
_wide16 = sum(1 for r in _rows16 if any(str(r.get(k) or "").strip() for k in _live.CONTACT_KEYS))
check("16a 体检器复算可入库数走生产同一把尺子：只有官网的那一家判「不能入库」（宽口径判 3/3）",
      _live._importable(_cx16, _rows16) == (2, "") and _wide16 == 3,
      f"入库口径={_live._importable(_cx16, _rows16)}｜宽口径={_wide16}")
check("16a- 没货/复算不了必须返回 None 而不是 0：0 会把正常的一路判成死数据（探针自己造红）",
      _live._importable(_cx16, []) == (None, "")
      and _live._importable(type("NoMap", (), {"PLATFORM_NAME": "x"})(), _rows16)[0] is None)

# 16b 「抓到 N 家、能入库 0 家」是独立的一档，比低产出、比差分都先判红
_m16, _v16 = _live._attrib({"legs": {"联系页": 12, "商铺首页": 3}, "leg_pid_raw": "0/40"},
                           51, False, "", diff="ok", peer=40, imp=0)
check("16b 全是死数据必须判红，且把通路账带进结论（马可波罗 51 家那轮的真实形状）",
      _m16 == "❌" and "一条都不能用" in _v16 and "通路账" in _v16 and "0/40" in _v16, _v16)
check("16b- 同一读数不传 imp（复算不了）时不许判红——判红必须基于测过的数，"
      "但也不许就此放行（探针自己的故障不能冒充绿）",
      _live._attrib({"legs": {"联系页": 12}}, 51, False, "", diff="ok", peer=0, imp=None)[0]
      == "⚠")
# 16b2 标题句只许说测到的事实：旧写法在最前面替分档下了结论（「取号通路没出货」），
# 而同一行的后半句按分档可能写「这不是通路缺陷」——世界工厂网那 6 家全是站方没登的一轮，
# 两句自相矛盾地印在同一行里，读的人只会照着前半句去修本来没坏的通路。
_m16b2, _v16b2 = _live._attrib({"listed": 6, "nc_kinds": {"blank": 6},
                                "nc_evidence": {"blank": "联系电话→-"}},
                               6, False, "", diff="ok", peer=9, imp=0)
check("16b2 四项全空且分档说「站方没登」时，标题句不许抢答「通路没出货」（只看事实，责任归下一行）",
      _m16b2 == "❌" and "取号通路没出货" not in _v16b2
      and "站方自己没登" in _v16b2 and "一行都进不了客户库" in _v16b2, _v16b2)
# 16b3 同一形状换一档：已声明「详情不取页」的站（跨国采购网）拿到的必须是自己的处方，
# 不许再写「先给这站的 crawl_detail 接上 _keep_detail」——那张处方对这站根本执行不了。
_m16b3, _v16b3 = _live._attrib({"listed": 1, "nc_kinds": {"no_page": 1},
                                "nc_evidence": {"no_page": "本站详情不取页（列表页明文组装）"}},
                               1, False, "", diff="ok", peer=9, imp=0)
check("16b3 no_page 那一档的处方不许是「接把手」（给已声明设计边界的站开假处方，是另一种谎）",
      _m16b3 == "❌" and "_keep_detail" not in _v16b3
      and "详情不取页" in _v16b3 and "这不是漏接把手" in _v16b3, _v16b3)
_m16c, _v16c = _live._attrib({"listed": 51, "page_fail": 0}, 51, False, "",
                             diff="ok", peer=40, imp=3)
check("16c 可入库率是大头缺口时，证据链要把「四项全空 48 家」念出来（旧写法只念第一条证据），"
      "且账上没有分档时不许再写「先修取号通路」这种没证据的处方",
      _m16c == "❌" and "四项全空 48 家" in _v16c and "可入库仅 3/51" in _v16c
      and "先修取号通路" not in _v16c and "不在「无联系方式」这一档" in _v16c, _v16c)
# 16c- 账上有分档时结论必须跟着分档走：马可波罗那一轮「无联系方式」根本没被记上
# （那批只有官网，状态是「成功」），把这种局说成「站方没登」同样是说谎
_m16c3, _v16c3 = _live._attrib(
    {"listed": 51, "nc_kinds": {"blank": 40}, "nc_evidence": {"blank": "联系电话→- "}},
    51, False, "", diff="ok", peer=40, imp=3)
check("16c- 分档为 blank 时处方必须是「站方自己没登、要换词换站」，不许说修解析",
      _m16c3 == "❌" and "站方自己没登" in _v16c3 and "先修我们的解析" not in _v16c3,
      _v16c3)
check("16c== 反向对照（旧那句处方）：「先修取号通路再谈换词」不得在任何一档里复活"
      "（档位直接取自 NC_KIND_ORDER，新增一档不必回来补这里的枚举）",
      "先修取号通路再谈换词" not in "\n".join(
          _live._attrib({"nc_kinds": {k: 9}}, 51, False, "",
                        diff="ok", peer=40, imp=3)[1]
          for k in cdp_base.NC_KIND_ORDER)
          + _v16c + _v16c3)

# 16d 零产出的词必须点名（旧文案硬写「第二个关键词」，实机点错了一个词）
_m16d, _v16d = _live._attrib({"listed": 12, "kw_early": {"泵": 3}}, 12, False, "",
                             diff="unknown", peer=0, zero=["泵"],
                             killed="「泵」列表页早过滤砍 3 家")
check("16d 差分跑不成时点名**真正**零产出的词（旧文案把第一个词写成「第二个关键词」）",
      _m16d == "❌" and "「泵」零产出" in _v16d and "第二个关键词" not in _v16d, _v16d)
check("16d- 没点名时也只能说「其中一个关键词」，不许猜位置",
      "其中一个关键词" in _live._attrib({"listed": 9}, 9, False, "", diff="unknown")[1])
check("16d== 通路账按请求数倒序、并带上「列表卡带产品 id」的比例（静默断腿要一眼看出断在哪）",
      _live._legs_line({"legs": {"图片码": 2, "产品页": 40, "联系页": 12}, "leg_pid_raw": "40/40"})
      == "产品页 40 次、联系页 12 次、图片码 2 次｜列表卡带产品 id 40/40"
      and _live._legs_line({}) == "")

# 16e/16f build_clean_rows 的 stats_out：每一档的数 + 四项全空按来源归账
_rec16 = [
    {"公司名": "合并甲公司", "电话": "13800001111", "数据来源": "甲平台", "状态": ""},
    {"公司名": "合并甲公司", "电话": "0755-88889999", "数据来源": "乙平台", "状态": ""},
    {"公司名": "只有官网乙公司", "官网": "http://www.y.example.com", "数据来源": "甲平台", "状态": ""},
    {"公司名": "查不到的丙公司", "数据来源": "甲平台", "状态": "未找到"},
    {"公司名": "只有微信丁公司", "微信号": "wx_ding_1", "数据来源": "乙平台", "状态": ""},
]
_logs16, _waste16, _st16 = [], [], {}
_rows16b = _cl.build_clean_rows(_rec16, log=_logs16.append, waste_sink=_waste16.extend,
                                stats_out=_st16)
check("16e 漏斗分档数外读：原始/剔无效/跨平台合并/行数/四项全空各是多少，一目了然",
      _st16["in"] == 5 and _st16["invalid"] == 1 and _st16["merged"] == 1
      and _st16["rows"] == 3 and _st16["no_contact"] == 1, str(_st16))
check("16e- 四项全空必须按来源归账（哪一路成批死数据，就去修哪一路的取号通路）",
      _st16["no_contact_by_source"] == {"甲平台": 1}
      and "只有官网乙公司" in _waste16, f"{_st16['no_contact_by_source']} {_waste16}")
check("16e== 账目同时进日志：吞掉的那一档说不出归属，报表就还是一句「吞了 50 条」",
      any("四项（手机/固话/邮箱/微信）全空" in x and "甲平台 1 条" in x for x in _logs16),
      "‖".join(_logs16)[-160:])
# 16e=== 自己给自己挖的那个坑：跨平台合并的一行有两个来源，按来源归会计两次。
#        若把「分项合计」当成剔除条数，filter_import_rows 剔 1 条、账上写 2 条，
#        体检器那句「口径分叉」自检就会当场谎报——行数与归属分布必须是两个字段。
_multi16 = [{"公司名": "两站都没号的公司", "官网": "http://a.example.com", "数据来源": "甲平台", "状态": ""},
            {"公司名": "两站都没号的公司", "地址": "广东省东莞市", "数据来源": "乙平台", "状态": ""}]
_st16m, _lg16m = {}, []
_rows16m = _cl.build_clean_rows(_multi16, log=_lg16m.append, stats_out=_st16m)
check("16e=== 合并来的死数据只算 1 条，但两个来源各记一次（分项合计大于条数是设计不是分叉）",
      _st16m["no_contact"] == 1 and _st16m["no_contact_by_source"] == {"甲平台": 1, "乙平台": 1}
      and CdpSiteCrawler.filter_import_rows(_rows16m) == []      # 真剔了 1 条＝与行数对得上
      and "分项合计可能大于条数" in _lg16m[-1], f"{_st16m}｜{_lg16m[-1:]}")
check("16f 不传 stats_out 时行为与旧版一致（老调用方一行不改也不炸，账只是不外读）",
      len(_cl.build_clean_rows(_rec16, log=lambda m: None)) == 3)
check("16f- 入库口径与 _has_contact 逐行同答案（对不上＝有人又刻了一把尺子）",
      all(bool(_cl._has_contact(r)) == bool(CdpSiteCrawler.filter_import_rows([r]))
          for r in _rows16b), str(_rows16b))

# 16g 端到端：ParallelSession.clean_records 的 funnel_out 与分站复算必须同一个数
_f16 = {}
_raw16 = [
    {"公司名称": "漏斗甲机械", "手机": "13800002222", "数据来源": "八方资源网", "关键词": "泵"},
    {"公司名称": "漏斗乙机械", "邮箱": "a@example.com", "数据来源": "八方资源网", "关键词": "泵"},
    {"公司名称": "漏斗丙贸易", "公司网址": "http://www.loubing.example.com",
     "数据来源": "八方资源网", "关键词": "生物质锅炉"},
]
_kept16 = ps.clean_records(records=_raw16, log=lambda m: None, funnel_out=_f16)
check("16g clean_records 交出整条漏斗的账：喂进/剔无效/合并/四项全空/入库，每一档都有数",
      _f16["fed"] == 3 and _f16["rows"] == 3 and _f16["invalid"] == 0
      and _f16["merged"] == 0 and _f16["import_dropped"] == 1 and len(_kept16) == 2, str(_f16))
check("16g- 两道判据在真漏斗上也必须同数（import_dropped == no_contact，本轮实机分叉的那一处）",
      _f16["import_dropped"] == _f16["no_contact"] == 1
      and _f16["no_contact_by_source"] == {"八方资源网": 1}, str(_f16))
check("16g== 分站复算与整条漏斗同一个口径：同一批行两边都得说「能入库 2 条」",
      len(_kept16) == _live._importable(ps.crawlers["b2b168"], _raw16)[0] == 2,
      f"漏斗={len(_kept16)}｜分站={_live._importable(ps.crawlers['b2b168'], _raw16)}")

# 16h 结构判据（同类查全）：宽口径只许留在对照行里，判定与渲染都必须接上新尺子
check("16h 四项全空只有一把尺子：build_clean_rows 问 _has_contact，不再自己拆一遍电话/邮箱",
      "_has_contact(row)" in _fn_seg(_cl_src, "build_clean_rows")
      and "split_phones(f.get" not in _fn_seg(_cl_src, "build_clean_rows"),
      _fn_seg(_cl_src, "build_clean_rows")[:0])
check("16h- 体检器把可入库数传进判定，且复算走 _importable（不是又写一份判断）",
      "imp=imp_n" in _fn_seg(_LIVE_SRC, "main")
      and "_importable(crawler, rows)" in _fn_seg(_LIVE_SRC, "_fmt_site"))
check("16h== 旧的假安心文案「{n} 家 / 命中 {contact_n}/{n}」不许复活（马可波罗假绿的原文）",
      'f"{n} 家 / 命中 {contact_n}/{n}' not in _LIVE_SRC
      and "elif contact_n * 2 < n" not in _LIVE_SRC)
check("16i 漏斗账必须**打印**出来（只算不念，报告还是那一句「吞掉 50 条」）",
      "漏斗分解" in _LIVE_SRC and "funnel_out=funnel" in _LIVE_SRC
      and "四项全空按来源" in _LIVE_SRC)
check("16i- 口径分叉自检也在打印里：两道判据数对不上要当场响，不等下一轮人去猜",
      "口径分叉" in _LIVE_SRC and "必有一把是错的" in _LIVE_SRC)

# 16j 「探针没测到」与「站点没货」必须是两种颜色：None 那一档只许说「未判」，
#     不许顺着旧写法长出一个 ✅（假绿的最后半条路：绿灯本身没坏，只是通往绿灯的
#     那条路上多了一格没测，而报告把「没测」念成了「没问题」）
_m16j, _v16j = _live._attrib({"listed": 40}, 40, False, "", diff="ok", peer=0, imp=None,
                             imp_err="AttributeError：缺字段「电话」")
check("16j 复算不成单独成档（⚠ 未判）并念出为什么不成（旧写法在这里直接打 ✅）",
      _m16j == "⚠" and "AttributeError" in _v16j and "不许把它当绿" in _v16j, _v16j)
check("16j- 反向对照：测过且全都能用时这一档不许开口（「未判」不许变成常驻结论）",
      _live._attrib({"listed": 40}, 40, False, "", diff="ok", peer=0, imp=40) == ("", ""))
check("16j= 故障要跟着每一条更重的结论走（低产出那一档不许把「没测到」吞掉）",
      "入库口径未判" in _live._attrib({"listed": 40, "failed": 30}, 5, False, "",
                                      diff="ok", peer=40, imp=None)[1])
check("16j== 产出异常那句不许拿抓到的家数冒充「可入库 N 家」（没测就是没测）",
      "可入库 未测" in _live._attrib({"listed": 12}, 3, False, "", diff="ok", peer=40)[1])
_m16k, _v16k = _live._importable(type("BadMap", (), {"PLATFORM_NAME": "x"})(), _rows16)
check("16k 缺清洗映射时原因要写清楚（旧文案硬写「没有清洗映射」，异常那一支的原因被吞）",
      _m16k is None and "_to_cleaner_record" in _v16k, repr(_v16k))
check("16k- main 侧还有第二道锁：_attrib 万一放行，imp 缺失时 ✅ 那支也不许打勾",
      "elif imp_n is None" in _fn_seg(_LIVE_SRC, "main")
      and "不许打绿" in _fn_seg(_LIVE_SRC, "main")
      and "else '未复算'" not in _LIVE_SRC)
print("⑯ 入库口径一把尺子 + 漏斗按档说原因（含通路账）：51 家假 ✅ 那一族当场判红 ✓")


# ════════════════════════════════════════════════════════════════════════
# ⑰ 「清空历史种子库」：清了要真清得动，没清成要说得出（2026-11-16 全局自审）
# 由来：站点侧真正拦住一家公司的这一格是内存集合 _seen_members，而关键词站的
# clear_seen_store 过去只清盘上文件与 _seen_store 字典（1688 那一路却两样都清，两把
# 尺子）；更坏的一半是 unlink 失败被 except: pass 吞掉后照样 return True，界面于是
# 弹「历史种子库已清空」。用户信了这句话，下一轮看到的仍是「命中即跳过 0 家」——
# 与「这个平台采不到数据」完全同形，却比它更难查（账上一点痕迹都没有）。
# 口径：①清库动作只有 cdp_base.reset_seen_store 一份实现（独立 1688 包同名语义逐字对拍）；
#      ②三样一起清：盘上文件、内存字典、内存去重集合；③删不掉要说得出原因并落进账；
#      ④会话层逐路点名失败，全清成功才许闭嘴；⑤界面两处入口共用同一份说法。
# ════════════════════════════════════════════════════════════════════════
import tempfile as _tf17                                     # noqa: E402

_AD_SRC17 = (_REPO / "b2bcollect_build" / "site_adapters.py").read_text(encoding="utf-8")
_A1688_SRC17 = (_REPO / "ali1688_build" / "ali1688_crawler.py").read_text(encoding="utf-8")
_GUI_SRC17 = (_REPO / "b2bcollect_build" / "b2bcollect_gui.py").read_text(encoding="utf-8")
_AGUI_SRC17 = (_REPO / "ali1688_build" / "ali1688_gui.py").read_text(encoding="utf-8")


class _SeedCrawler17(B2b168Crawler):
    """只借基类的清库动作，落点换到临时目录——绝不拿开发机上真种子库做删除实验。"""

    def __init__(self, path):
        super().__init__()
        self._seen_store_path = path


_tmp17 = Path(_tf17.mkdtemp(prefix="seed17_"))
_CDP_SRC17 = (_REPO / "b2bcollect_build" / "cdp_base.py").read_text(encoding="utf-8")
_reset17 = _fn_seg(_CDP_SRC17, "reset_seen_store")
_p17 = _tmp17 / "seen_store_b2b168.json"
_p17.write_text('{"甲公司": {"at": "x", "st": "成功", "ct": 1}}', encoding="utf-8")
_c17 = _SeedCrawler17(_p17)
_c17._seen_store = {"b2b168:name:甲公司": {"at": "x"}}
_c17._seen_members = {"b2b168:name:甲公司", "甲公司"}
check("17a 清库三样一起清：盘上文件、内存字典、**内存去重集合**（最后那样才是真拦着公司的这一格）",
      _c17.clear_seen_store() is True and not _p17.exists()
      and _c17._seen_store == {} and _c17._seen_members == set()
      and _c17._seed_clear_error == "", f"{_c17._seed_clear_error!r} 文件在否={_p17.exists()}")
check("17a- 反向对照：旧写法只清字典不清 _seen_members——点了「清空」这一轮照样跳过全部",
      "_seen_members = set()" in _reset17)

_dir17 = _tmp17 / "占位目录"                       # unlink 一个目录：mac 抛 IsADirectoryError、
_dir17.mkdir()                                    # Windows 抛 PermissionError，都是 OSError
_c17b = _SeedCrawler17(_dir17)
_c17b._seen_store = {"k": {}}
_c17b._seen_members = {"k"}
check("17b 删不掉时不许假成功：返回 False 且原因进账（旧写法在这里 except: pass 后 return True）",
      _c17b.clear_seen_store() is False and "Error" in _c17b._seed_clear_error
      and _dir17.exists(), repr(_c17b._seed_clear_error))
check("17b- 失败也不留半成品：内存两样照样清掉，用户修好权限再清一次时不该还有旧集合拦人",
      _c17b._seen_store == {} and _c17b._seen_members == set())
check("17b== 清库不许把异常抛给调用方（只读卷上抛出去就是「点按钮整个工具没反应」）",
      "except Exception as e" in _reset17 and "raise" not in _reset17)

_ada17 = _def_src(_AD_SRC17, "clear_seen_store", "Ali1688Site")
_cdb17 = _def_src(_CDP_SRC17, "clear_seen_store", "CdpSiteCrawler")
check("17c 清库动作只有一份实现：1688 适配器与基类都只是薄薄一层，删文件那一句只在 "
      "reset_seen_store 里出现一次",
      "cdp_base.reset_seen_store" in _ada17 and "unlink()" not in _ada17
      and "reset_seen_store(" in _cdb17 and "unlink()" not in _cdb17
      and _reset17.count("unlink()") == 1, f"适配器={_ada17[:60]}｜基类={_cdb17[:60]}")
_seg17d = _fn_seg(_A1688_SRC17, "clear_seen_store")
check("17d 独立 1688 包那份（本包不 import cdp_base，是同一语义的第二处）三样都对齐",
      "_seen_members = set()" in _seg17d and "_seed_clear_error" in _seg17d
      and "return False" in _seg17d
      and "except Exception:\n            pass\n        return True" not in _seg17d, _seg17d[:80])

# 17e 会话层汇总：真实种子库先脱钩（落点改到临时目录），再拿两路假件试判据
_saved17 = {}
for _o17 in list(ps.crawlers.values()) + list(ps.channels.values()):
    _pp = getattr(_o17, "_seen_store_path", None)
    if _pp is not None:
        _saved17[id(_o17)] = _pp
        _o17._seen_store_path = _tmp17 / f"脱钩_{getattr(_o17, 'PLATFORM_SLUG', 'x')}.json"


class _FakeBad17:
    PLATFORM_NAME = "坏路甲"

    def clear_seen_store(self):
        self._seed_clear_error = "OSError：模拟只读卷"
        return False


class _FakeNone17:
    PLATFORM_NAME = "没有清库入口的路"


_log17, _old_log17 = [], ps._log
ps._log = _log17.append
ps.crawlers["_fake_bad17"] = _FakeBad17()
ps.channels["_fake_none17"] = _FakeNone17()
try:
    ps.clear_seen_store()
    _miss17 = dict(ps._seed_clear_missed)
    ps.channels.pop("_fake_none17")                 # 只留「都能清成」的那些路
    ps.crawlers.pop("_fake_bad17")
    _n_ok17 = ps.clear_seen_store()                 # 第二次：临时目录里没文件，全路都应清成
    _miss_ok17 = list(ps._seed_clear_missed)
finally:
    ps._log = _old_log17
    ps.crawlers.pop("_fake_bad17", None)
    ps.channels.pop("_fake_none17", None)
    for _o17 in list(ps.crawlers.values()) + list(ps.channels.values()):
        if id(_o17) in _saved17:
            _o17._seen_store_path = _saved17[id(_o17)]
check("17e 会话层逐路点名没清成的（路名＋原因），并提醒「下一轮仍会命中即跳过」",
      "模拟只读卷" in _miss17.get("坏路甲", "")
      and "没有清库入口的路" in _miss17
      and any("没清干净" in x for x in _log17), str(_miss17))
check("17e- 「没有这一格」与「没清成」分得开：前者说入口缺失，后者带真实原因",
      "入口" in _miss17.get("没有清库入口的路", ""), str(_miss17))
check("17e== 全清成功时不许无端报警（告警只跟着失败响，否则下一轮没人看这句话）",
      _miss_ok17 == [] and _n_ok17 >= 9 and not any("没清干净" in x for x in _log17[1:]),
      f"{_miss_ok17} 成功路数={_n_ok17}")

check("17f 界面两处入口共用同一份说法（过去工具栏讲事实、「开始新任务」无条件讲「已清空」）",
      _GUI_SRC17.count('"历史种子库已清空。"') == 1
      and _GUI_SRC17.count("_clear_seen_store_now()") >= 2
      and "部分没清成" in _GUI_SRC17)
check("17f- 独立 1688 界面同样按返回值说话，不看按钮点了没",
      "ok = self.crawler.clear_seen_store()" in _AGUI_SRC17 and "没清成" in _AGUI_SRC17)
print("⑰ 清种子库：三样一起清 + 失败说得出原因 + 逐路点名（假「已清空」那一族封死）✓")

# ════════════════════════════════════════════════════════════════════════
# ⑱ 两种「拿回来了但没用」：HTTP 200 的整页质询壳 ＋ 商铺首页 block2 壳
# ════════════════════════════════════════════════════════════════════════
# 由来（2026-11-15 第七轮实机，马可波罗 34 家只有 6 家可入库，通路账「码图 0 次」）：
#   A) 请求量上来之后站方回 **HTTP 200 + 整页火山引擎 WAF 质询壳**（14711 字节，可见文本只有
#      「正在检测」）。旧插页判据要求「体积 < HTTP_BLOCK_MAX_BYTES」，大壳整个放过去，被当
#      正常页解析出 0 条 →「这家店没联系方式」。症状与「平台就是不给号」同形，处方却完全不同。
#   B) 商铺首页是 **block2 壳**：标签带 class、值落在紧接其后的那一条 span、码图是
#      buildStringToimg**2**.php?…&fei=1&en_corpid=<每店一份的密文>。旧 _FIELD_RE 只认不带
#      属性的 <span>、旧 _CODE_RE 被那个 2 顶开 → 这一跳拿回 44KB 却一个字段、一张码图原料
#      都读不出（旧注释还写着「两页壳同构、原样能用」，是一句错话）。
# 夹具都是按实机留档的**形状**逐条转写的（公司名/号码/ token 换成假值），所以能随代码提交、
# 每轮回归都真跑——不依赖 debug/fixtures/ 里那些含真实数据的留档页面。
import makepolo_crawler as _mp18                                       # noqa: E402

_MP_WAF_SHELL = ('<!DOCTYPE html><html><head><script>var c={"v":1,"cid":"a","seed":"b",'
                 '"token":"eyJhbGciOiJFZDI1NTE5IiwidHlwIjoiWUFmX1ZlcmlmaWNhdGlvbl9jaGFsbGVuZ2UifQ"};'
                 '</script><script src="https://lf-rc1.yhgfb-cn-static.com/obj/rc-verifycenter/'
                 'sec_sdk_build/4.0.18/captcha/index.js"></script>'
                 '<script>new Image().src="https://waf-captcha-proxy.volcbiz.com/p?x=1";</script>'
                 '</head><body><div id="app">正在检测</div></body></html>')
_MP_REAL_HOME = ('<html><head><title>某公司商铺-马可波罗网</title></head><body>'
                 + '<div class="connect_msg"><span class="block2">联系人：</span>'
                   '<span class="block2" style="color:#333;">时晓琳</span> <br/>'
                   '<span class="block2">手机：</span> <span class="block2" '
                   'id=\'yellow_left_link_block2\'> <em class="yellow_left_mobile_cpc"> '
                   '<img src="/purchase/buildStringToimg2.php?t_page=y_left&phone=' + "9e54" + "T" * 50
                   + '&fei=1&en_corpid=5255N-KT7-ABC"/></em> </span> <br/>'
                   '<span class="block2" >固定电话：</span><span class="block2"> '
                   '<em class="show_cpc_fixphone"><img src="/purchase/buildStringToimg2.php'
                   '?t_page=y_left&phone=3d83' + "T" * 50 + '&fei=2&en_corpid=5255N-KT7-ABC"/></em>'
                   ' <!-- --> <!-- <em class="show_sub_cpc_fixphone">025-****1400</em>--> </span>'
                   '<span class="block2">传真：</span><span class="block2" style="color:#333;"></span>'
                   '<span class="block2">公司地址：</span><span class="block2">南京市江宁区某路 9 号</span>'
                   '</div>' + "<p>主营产品 输送机 配件</p>" * 60 + '</body></html>')
_MP_WEAK_ONLY = ('<html><body>正常详情页正文 ' + "真页正文 " * 400 + '<script src="https://lf-rc1'
                 '.yhgfb-cn-static.com/obj/rc-verifycenter/sec_sdk_build/4.0.18/captcha/index.js">'
                 '</script></body></html>')
_MP_CP_LIST = ('<div class="pro_list"><a href="/product/101104014473.html">输送机 A</a>'
               '<a href="//china.makepolo.com/product-detail/101104014515.html">输送机 B</a></div>')


def _mp_fields(page):
    """把 crawl_detail 里那三行取值口径原样复算一遍（判据改了这里就跟着红）。"""
    out = {}
    for k, v in _mp18._FIELD_RE.findall(_mp18._COMMENT_RE.sub(" ", page or "")):
        v = _mp18.clean_text(v)
        v = _mp18._CODE_STRIP_RE.sub("", v).strip(" ：:|/")
        if v and (k not in out or not out[k]):
            out[k] = v[:120]
    return out


# 18a 质询壳判据：三条签名齐全才算，弱签名独中不算，超出前 60KB 窗口不算
check("18a 200·整页质询壳认得出（旧体积判据放过的那一档）",
      CdpSiteCrawler._looks_like_challenge_shell(_MP_WAF_SHELL) is True)
check("18a- 反向对照：真商铺页（44KB 量级、带 block2 与码图）不许判成壳",
      CdpSiteCrawler._looks_like_challenge_shell(_MP_REAL_HOME) is False)
check("18a= 只预载了验证 SDK 的正常页不算壳（弱签名独中不许开火——误判的代价是整条通道白等冷却）",
      CdpSiteCrawler._looks_like_challenge_shell(_MP_WEAK_ONLY) is False)
check("18a== 窗口是有边的：签名藏在 60KB 之后就不算（判据口径与常量一致）",
      CdpSiteCrawler._looks_like_challenge_shell("x" * 70000 + _MP_WAF_SHELL) is False)

# 18b 通道级分诊：档位跟着「本站借不借得到浏览器」，不是跟着「这是不是验证页」
_c18 = _mp18.MakepoloCrawler()
_c18.reset_run_state()
_r18 = _c18.http_block_reason(200, _MP_WAF_SHELL, "http://caigou.makepolo.com/spc_new.php?q=1")
check("18b 裸 HTTP 站（BLOCK_OPEN_BROWSER=False）撞壳：按冷却档处置，但原因仍写清是人机验证壳",
      _c18._block_kind == "ip" and "人机验证壳" in _r18 and _c18._block_needs_js is True
      and _c18._block_probe_url.endswith("spc_new.php?q=1"),
      f"kind={_c18._block_kind} reason={_r18}")
_c18b = _mp18.MakepoloCrawler()
_c18b.BLOCK_OPEN_BROWSER = True                     # 同一个站、同一份壳，只是借得到标签页
_c18b.reset_run_state()
_r18b = _c18b.http_block_reason(200, _MP_WAF_SHELL, "http://caigou.makepolo.com/x")
check("18b- 借得到浏览器的站撞同一份壳：归 captcha（执行 JS 确实能过），且文案不带「HTTP」"
      "（上层拿它兜底判 IP 限流，带上这个词就把能过的页误杀成干等）",
      _c18b._block_kind == "captcha" and "HTTP" not in _r18b and "浏览器执行 JS" in _r18b, _r18b)
_c18c = _mp18.MakepoloCrawler()
_c18c.reset_run_state()
_k0, _p0, _n0 = _c18c._block_kind, _c18c._block_probe_url, _c18c._block_needs_js
_r18c = _c18c.http_block_reason(200, _MP_WAF_SHELL, "http://a.cn.makepolo.com/contact_us.html",
                                deep=True)
check("18c 深链那一跳的壳照旧只回原因：通道三件状态一件都不动（与 13a 同口径，只是这一档是 200）",
      "人机验证壳" in _r18c and "通道入口未受影响" in _r18c
      and (_c18c._block_kind, _c18c._block_probe_url, _c18c._block_needs_js) == (_k0, _p0, _n0),
      f"reason={_r18c}")

# 18d 浏览器那条路同样认壳（正文几十字、HTML 上万字节，旧那道体积门正好放过）
_c18d = B2b168Crawler()
_c18d.reset_run_state()
_c18d._current_url = lambda: "http://a.cn.makepolo.com/"
_c18d._title = lambda: "某公司商铺"
_c18d._page_state = lambda: {"len": 6}
_c18d._eval = lambda *a: ""
_c18d._html = lambda: _MP_WAF_SHELL
check("18d 浏览器链路：正文极小 + HTML 很大 + 命中签名 → 判壳（不再当正常页解析出 0 条）",
      "人机验证壳" in (_c18d._detect_block() or ""), _c18d._detect_block())
_c18d._html = lambda: _MP_REAL_HOME
check("18d- 反向对照：同样「正文小 HTML 大」的正常页不误判", _c18d._detect_block() == "")

# 18e 冷却档的劝告话跟着事实走：壳页上不许说「这不是验证码」
_c18e = _mp18.MakepoloCrawler()
_ev18, _lg18 = equip(_c18e)
_c18e._block_needs_js = True
_c18e._wait_manual_unblock("站点返 200 但整页正文是人机验证壳", lambda: "还在拦", max_wait=0)
_c18f = _mp18.MakepoloCrawler()
_ev18f, _lg18f = equip(_c18f)
_c18f._wait_manual_unblock("站点返回 HTTP 502（IP 级限流/冷却，非验证码）", lambda: "还在拦",
                           max_wait=0)
check("18e 壳页：说「这是 JS 质询壳，页里没有可点的框」（旧那句「这不是验证码」在这种页上是假话）",
      any("JS 质询壳" in m for m in _lg18) and not any("这不是验证码" in m for m in _lg18),
      str(_lg18))
check("18e- 反向对照：真·IP 限流那一档的说法一字不改（改这一族文案要两处都对得上）",
      any("这不是验证码，人工点不掉" not in m and "IP 级限流/冷却" in m for m in _lg18f)
      and not any("JS 质询壳" in m for m in _lg18f), str(_lg18f))

# 18f/g/h block2 壳的字段判据：放宽只多认形状，旧模板与「空值不串栏」都不许退化
_f18 = _mp_fields(_MP_REAL_HOME)
check("18f 商铺首页 block2 壳：值落在下一条带属性的 span 里也读得出（旧写法整页读空）",
      _f18.get("联系人") == "时晓琳" and "某路 9 号" in _f18.get("公司地址", ""), str(_f18))
check("18f- 码图栏不算明文值（摘掉整条 URL 后剩空串就不入库），注释里的掩码更不算",
      "手机" not in _f18 and "固定电话" not in _f18 and "传真" not in _f18
      and "1400" not in str(_f18.values()) and "****" not in str(_f18.values()), str(_f18))
check("18g 反向对照：旧两种模板（值在同一条 span / 在 <li> 里）一字不变仍命中",
      _mp_fields('<div><span>手机：13812345678</span><span>联系人：王强</span></div>')
      == {"手机": "13812345678", "联系人": "王强"}
      and _mp_fields('<ul><li>联系电话：0755-12345678</li></ul>')["联系电话"] == "0755-12345678")
check("18h 空值栏不许串走下一栏的值（跨一条开标签放宽之后的必查项）",
      _mp_fields('<span>传真：</span><span></span><span>地址：</span><span>深圳市xx路</span>')
      == {"地址": "深圳市xx路"})

# 18i/j 码图：归属照旧、URL 必须照页面原样（fei/en_corpid 少一个就 0 字节）
_c18g = _mp18.MakepoloCrawler()
_c18g.reset_run_state()
_c18g._log = lambda *a, **k: None
_tk18 = _c18g._shop_code_tokens(_MP_REAL_HOME, "njyuantai01")
check("18i buildStringToimg**2**.php 认得（旧 `_\\.php` 被那个 2 顶开＝通路账「码图 0 次」的根因）",
      len(_tk18) == 2 and all(t[2].startswith("/purchase/buildStringToimg2.php") for t in _tk18))
check("18i- 页面原样 URL 带着 fei 与 en_corpid（重建 URL 会丢这两个参数，站方回 0 字节）",
      all("fei=" in t[2] and "en_corpid=" in t[2] for t in _tk18), str([t[2][:60] for t in _tk18]))
check("18i= 推广位仍然一张不收（判据放宽只多认形状，归属这道闸没松）",
      _c18g._shop_code_tokens('<div class="phone_mask_list"><span>现货微信同步</span>'
                              '<img src="/purchase/buildStringToimg2.php?t_page=y_left&phone='
                              + "T" * 54 + '&fei=1&en_corpid=ZZZ"/></div>', "njyuantai01") == [])
_u18 = []


class _R18:
    content, status_code, headers, text = b"", 200, {}, ""


class _S18:
    def get(self, url, timeout=None, headers=None):
        _u18.append(url)
        return _R18()


_c18g._session = _S18()
_c18g._pace_before_request = lambda *a, **k: None
_c18g._codes("njyuantai01", _MP_REAL_HOME, "http://njyuantai01.cn.makepolo.com/")
check("18j 取图请求＝页面原样 src 拼成的绝对地址（base 用这张页真正来自的那条 URL）",
      _u18 and all(u.startswith("http://njyuantai01.cn.makepolo.com/purchase/"
                               "buildStringToimg2.php?") and "en_corpid=" in u for u in _u18),
      str(_u18[:2]))
_u18.clear()
_c18g._codes("njyuantai01", '<li>联系电话：<img src="/purchase/buildStringToimg.php?t_page=y_left'
               '&phone=' + "T" * 54 + '"/></li>', "http://njyuantai01.cn.makepolo.com/"
                                                 "contact_us.html")
check("18j- 反向对照：旧 contact_us 形状（没有 fei/en_corpid）照旧走同一条相对拼接",
      len(_u18) == 1 and _u18[0].endswith("buildStringToimg.php?t_page=y_left&phone=" + "T" * 54),
      str(_u18))

# 18k 第三条腿（列表卡 id 不给看 → 本店产品中心页的 id）：出货/配额/没原料/归属四档
_MP_PRODUCT = ('<h2>公司名片</h2><ul class="mes_cont"><li>手机号：<span>13675360858 </span></li>'
               '<li>联系人：<span>张艳美</span></li><li>公司名称：<span>'
               '<a href="//shangdonghaiguang.cn.makepolo.com">山东某设备有限公司</a></span></li></ul>'
               '<div class="supplier_b_mes"><span>电</span>话 :&nbsp;0536-2105088 &nbsp;'
               '<span>地</span>址 :&nbsp;潍坊市某区某路 1 号</div>')
_MP_PRODUCT_OTHER = _MP_PRODUCT.replace("shangdonghaiguang", "burenslug")


def _mp_with(pages, slug="shangdonghaiguang"):
    """装一台只发字典里那些页的采集器（返回 (crawler, 请求列表, 日志列表)）。"""
    cc = _mp18.MakepoloCrawler()
    cc.reset_run_state()
    hits = []
    lgs = []
    cc._log = lambda m="", *a: lgs.append(str(m))
    cc._pace_before_request = lambda *a, **k: None

    class _RR:
        def __init__(self, body, code):
            self.content = body.encode("utf-8")
            self.status_code = code
            self.headers = {}
            self.text = body

    class _SS:
        def get(self, url, timeout=None, headers=None):
            hits.append(url)
            # 先按「路径结尾」配、再退回「包含」：商铺首页那条键（…makepolo.com/）同时也是
            # 产品中心页 URL 的一段前缀，只按包含配会把这一跳喂成首页（夹具自己先骗了判据）
            for key, (body, code) in pages.items():
                if url.endswith(key):
                    return _RR(body, code)
            for key, (body, code) in pages.items():
                if key in url:
                    return _RR(body, code)
            return _RR("", 404)

    cc._session = _SS()
    return cc, hits, lgs


_c18h, _u18h, _lg18h = _mp_with({"companyProduct.html": (_MP_CP_LIST, 200),
                                 "101104014473": (_MP_PRODUCT, 200)})
_pd18 = _c18h._shop_product_alternates("shangdonghaiguang", {})
check("18k 列表卡 id 不给看时，自家产品中心页的 id 能把明文手机取回来（实机：同店另一 id 给全量名片）",
      _pd18.get("手机") == "13675360858" and _pd18.get("联系人") == "张艳美"
      and _c18h._cp_hits == 1 and _c18h._cp_shops == 1, str(_pd18))
check("18k- 两种产品链接形状都认（商铺自己的 /product/<id>.html 与站方 /product-detail/<id>.html）",
      _mp18._CP_PID_RE.findall(_MP_CP_LIST) == ["101104014473", "101104014515"])
_c18i, _u18i, _lg18i = _mp_with({"companyProduct.html": (_MP_CP_LIST, 200),
                                 "101104014473": (_MP_PRODUCT_OTHER, 200)})
_pd18b = _c18i._shop_product_alternates("shangdonghaiguang", {})
check("18k= 归属闸门不许因为「id 来自本店页面」就免检：名片指向别家子域照样整页丢弃",
      not _pd18b.get("手机") and _c18i._cp_hits == 0, str(_pd18b))
_c18j, _u18j, _lg18j = _mp_with({"companyProduct.html": ("<html>一个 id 都没列</html>", 200)})
_c18j._shop_product_alternates("shangdonghaiguang", {})
check("18k== 这一跳 200 却没列出一个 id：开口说「这条腿没原料」，不悄悄空转（站方改 JS 注水就长这样）",
      any("没原料" in m for m in _lg18j) and len([u for u in _u18j if "product-detail" in u]) == 0,
      str(_lg18j))
_c18k, _u18k, _lg18k = _mp_with({"companyProduct.html": (_MP_CP_LIST, 200),
                                 "101104014473": (_MP_PRODUCT, 200)})
_c18k._cp_shops = _c18k.CP_SHOP_CAP
_pd18c = _c18k._shop_product_alternates("shangdonghaiguang", {})
check("18k=== 成本闸真的落下：配额走满后一家也不多打，但要说一次上限在哪（悄悄烧预算最贵）",
      not _u18k and any("上限" in m for m in _lg18k), str(_lg18k))

# 18l 通路账渲染：第三条腿的账看得见，且没有这条腿的站不许凭空多一段
_s18 = _c18h.stats()
check("18l 第三条腿的账进 stats（走了几家/出货几家），号码来源仍只算「产品页」那一格",
      _s18.get("cp_leg") == "自家产品中心 1/1 家" and _s18["legs"]["产品页"] == 0, str(_s18))
check("18l- 体检器把这段渲染进通路账那一行",
      "自家产品中心 1/1 家" in _live._legs_line(_s18), _live._legs_line(_s18))
check("18l= 反向对照：没走过这条腿时渲染与旧口径一字不变（不给别的站凭空多一段空账）",
      _live._legs_line({"legs": {"产品页": 3}, "leg_pid_raw": "5/8"})
      == "产品页 3 次｜列表卡带产品 id 5/8"
      and _live._legs_line({}) == "")

# 18m 两条页腿都没正文时，那一行说的是真死因
_c18l = _mp18.MakepoloCrawler()
_c18l.reset_run_state()
_c18l._note_deep_block("http://a.cn.makepolo.com/contact_us.html",
                       "HTTP 403（这一家的这条资源不给看，通道入口未受影响）")
_c18l._note_deep_block("http://a.cn.makepolo.com/",
                       _c18l.http_block_reason(200, _MP_WAF_SHELL, "http://a.cn.makepolo.com/",
                                               deep=True))
check("18m 死因按真话拼：403 那页说 403，200 壳那页说人机验证壳（旧写法两页一律印「都不给看（403）」）",
      "HTTP 403" in _c18l._refuse_why((("联系页", "http://a.cn.makepolo.com/contact_us.html", 403),
                                       ("商铺首页", "http://a.cn.makepolo.com/", 200)))
      and "人机验证壳" in _c18l._refuse_why((("商铺首页", "http://a.cn.makepolo.com/", 200),)),
      _c18l._refuse_why((("联系页", "http://a.cn.makepolo.com/contact_us.html", 403),
                         ("商铺首页", "http://a.cn.makepolo.com/", 200))))
check("18m- 没发出请求／账上没原因时不编原因（只说空响应）",
      _c18l._refuse_why((("联系页", "http://nope.example/x", 0),)) == "联系页空响应"
      and _c18l._refuse_why((("联系页", "", 0),)) == "两条页腿都没发出请求",
      _c18l._refuse_why((("联系页", "http://nope.example/x", 0),)))

# 18n 端到端：联系页 403 → 首页 block2 → 列表 id 是通用壳 → 自家产品中心 → 名片明文手机
_SHELL_PAGE = "<html><head><title>马可波罗</title></head><body>" + "推荐产品 " * 1200 + "</body></html>"
_c18m, _u18m, _lg18m = _mp_with({"contact_us.html": ("", 403),
                                 "shangdonghaiguang.cn.makepolo.com/": (_MP_REAL_HOME, 200),
                                 "product-detail/9999999": (_SHELL_PAGE, 200),
                                 "companyProduct.html": (_MP_CP_LIST, 200),
                                 "101104014473": (_MP_PRODUCT, 200)})
_rec18 = _c18m.crawl_detail({"公司名称": "山东某设备有限公司", "公司ID": "shangdonghaiguang",
                             "关键词": "输送机", "主营产品": "输送机",
                             "产品ID": "999999999999", "公司网址": ""})
check("18n 端到端（第七轮那一路的形状）：状态成功、手机取到、通路账与第三条腿都有数",
      _rec18["状态"] == "成功" and _rec18["手机"] == "13675360858"
      and _c18m.stats()["legs"]["产品页"] == 2 and _c18m.stats()["cp_leg"] == "自家产品中心 1/1 家",
      str(_rec18) + str(_c18m.stats()["legs"]))
check("18n- 这一家花的请求有数（403 一次 + 首页 + 两个列表 id 之一 + 产品中心 + 出货那一跳）："
      "预算要能对着账看",
      len(_u18m) <= 6, str(_u18m))
check("18n== 收尾账目格把深链原因念出来这件事仍然成立（新增一档不许把 13b 的渲染判据挤掉）",
      's.get("deep_block")' in _fn_seg(_HTTP_SRC["cdp_base.py"], "crawl")
      and CdpSiteCrawler.HTTP_CHALLENGE_SHELL_WINDOW > CdpSiteCrawler.HTTP_BLOCK_MAX_BYTES)
print("⑱ 200·整页质询壳 + block2 壳：拿回来了读不出／被拦了说成没货，这一族两处都堵上 ✓")

# ════════════════════════════════════════════════════════════════════════
# ⑲ 同一族缺陷在别的站的形状：本站自己的体积门只看「太薄」，看不到「太大的人机验证壳」
# ════════════════════════════════════════════════════════════════════════
# ⑱ 把 cdp_base.http_block_reason 补上签名判据之后，同类查全查出三处「各站自己写的体积门」：
#   · maimaib2b._tab_real_page —— 它刻意不调 http_block_reason（那函数会改分诊状态，
#     一次取页判断不该把「等人工」扭成「干等冷却」），所以基类的新档对它**不生效**；
#     CF 质询页 outerHTML 实测 44675 字符 > 本站体积门 15000，旧判据直接放行给解析器；
#   · gys._html_abnormal —— 恢复探测（probe_block 第 154 行）拿它判「已解除」，
#     体积这一条只拦 <3000 的薄页，大壳会被判成正常页 → 谎报恢复；
#   · qihuo11467.probe_block —— 它的体积门后面跟着 `not self.http_block_reason(...)`，
#     基类新档自动生效，属于「已经安全的那一类」，19c 钉住它不许被改成裸体积判断。
# 判据本身是纯函数（不看体积、不改任何状态），所以能安全接进这些「只看不记账」的位置。
import gys_crawler as _g19                                             # noqa: E402
import maimaib2b_crawler as _mb19                                      # noqa: E402

_BIG_SHELL = _MP_WAF_SHELL + "<div class='pad'>" + "正在为您验证，请稍候 " * 2000 + "</div>"
_BIG_REAL = ("<html><head><title>供应商网-企业列表</title></head><body>"
             + "<div class='card'>某机械设备有限公司 张经理 13800000000</div>" * 60
             + "</body></html>")
check("19 夹具本身的体积：壳页 15KB 以上（否则体积门这条判据测不出来）",
      len(_BIG_SHELL) > max(_mb19.Maimaib2bCrawler.HTTP_BLOCK_MAX_BYTES,
                            _g19.GysCrawler.HTTP_BLOCK_MAX_BYTES or 3000, 3000),
      str(len(_BIG_SHELL)))

# 19a gys：大壳不许当正常页（取页与恢复探测两条支路共用这一个判据）
check("19a gys 的 _html_abnormal 现在认得大壳（旧写法只看体积，十几 KB 的质询壳会被当正常页交回）",
      _g19.GysCrawler._html_abnormal(_BIG_SHELL) is True
      and _g19.GysCrawler._html_abnormal(_BIG_REAL) is False)
check("19a- 反向对照：薄页那一档旧口径不许回归（空页/短页仍算异常）",
      _g19.GysCrawler._html_abnormal("") is True
      and _g19.GysCrawler._html_abnormal("<html>短</html>") is True)

# 19b maimaib2b：标签那一页是不是真内容（它刻意绕开 http_block_reason，所以必须自己看签名）


class _MB19Stub:
    """只喂 _tab_real_page 用到的那几把手：插页特征词按本站词表、解析按「有没有卡片」。"""
    HTTP_BLOCK_MAX_BYTES = _mb19.Maimaib2bCrawler.HTTP_BLOCK_MAX_BYTES
    HTTP_BLOCK_MARKS = _mb19.Maimaib2bCrawler.HTTP_BLOCK_MARKS
    _looks_like_challenge_shell = _mb19.Maimaib2bCrawler._looks_like_challenge_shell

    def __init__(self, cards=0, text_len=9000):
        self._cards, self._text_len = cards, text_len

    def _auto_challenge_page(self):
        return False

    def parse_list(self, page, kw):
        return [{}] * self._cards

    def _page_state(self):
        return {"len": self._text_len}


check("19b 麦麦B2B：CF/WAF 质询壳渲染后有 9000 字可见文本也不算真页（旧判据只看体积门会放行）",
      _mb19.Maimaib2bCrawler._tab_real_page(_MB19Stub(), _BIG_SHELL) is False)
check("19b- 真列表页（能解析出卡片）照旧算真页，薄壳（<15000 带 Just a moment）照旧算插页",
      _mb19.Maimaib2bCrawler._tab_real_page(_MB19Stub(cards=2), _BIG_REAL) is True
      and _mb19.Maimaib2bCrawler._tab_real_page(
          _MB19Stub(), "<html><body>Just a moment</body></html>") is False)

# 19c 同类查全的机器判据：任何「拿体积当插页判据」的函数都必须同时问一次签名判据
_SIG_RE = __import__("re").compile(r"len\([^()]*\)\s*[<>]=?\s*\d{3,}")


def _shell_gate_problems(src):
    """返回「有体积门、没签名判据」的函数名。

    只划这类形状：函数体里既有 len(...) 与三位以上数字的比较，又读 HTTP_BLOCK_MARKS
    （＝它在判『这页是不是插页』），却没调 _looks_like_challenge_shell／没把判定
    交给 http_block_reason。后者是基类那条已经含新档的总判据，走它也算合格。
    """
    out = []
    for fn in (n for n in _ast.walk(_ast.parse(src)) if isinstance(n, _ast.FunctionDef)):
        seg = _ast.get_source_segment(src, fn) or ""
        if not _SIG_RE.search(seg) or "HTTP_BLOCK_MARKS" not in seg:
            continue
        if "_looks_like_challenge_shell" in seg or "http_block_reason" in seg:
            continue
        out.append(fn.name)
    return out


_gate_hits = {n: v for n, s in _HTTP_SRC.items() if (v := _shell_gate_problems(s))}
check(f"19c 全仓体积门判插页的地方都接上了签名判据（{len(_HTTP_SRC)} 份采集器源码，一处不许漏）",
      not _gate_hits, str(_gate_hits))
check("19c- 反向对照：⑱/⑲ 之前的两种旧写法必须被这条判据点出来（判据本身不许是摆设）",
      _shell_gate_problems(
          'class A:\n    def _tab_real_page(self, page):\n'
          '        if len(page) < 15000 and any(m in page for m in self.HTTP_BLOCK_MARKS):\n'
          '            return False\n        return True\n') == ["_tab_real_page"]
      and _shell_gate_problems(
          'class A:\n    def _html_abnormal(html):\n'
          '        if len(html) < 3000:\n            return True\n'
          '        return any(m in html for m in HTTP_BLOCK_MARKS)\n') == ["_html_abnormal"]
      and not _shell_gate_problems(
          'class A:\n    def _tab_real_page(self, page):\n'
          '        if self._looks_like_challenge_shell(page):\n            return False\n'
          '        if len(page) < 15000 and any(m in page for m in self.HTTP_BLOCK_MARKS):\n'
          '            return False\n        return True\n')
      and not _shell_gate_problems(
          'class A:\n    def probe(self, r):\n'
          '        if r.status_code == 200 and len(r.text) >= 2000 \\\n'
          '                and not self.http_block_reason(r.status_code, r.text):\n'
          '            return ""\n'))
check("19c== gys 那句「已解除」的判据确实共用同一个把手（各写一份＝下一个新壳形态只补一处）",
      "_html_abnormal" in _fn_seg(_HTTP_SRC["gys_crawler.py"], "probe_block"),
      _fn_seg(_HTTP_SRC["gys_crawler.py"], "probe_block")[:200])
print("⑲ 体积门这一族查全：三处本站判据补上签名档，机器判据钉住『下一个站长别再漏』 ✓")

# ════════════════════════════════════════════════════════════════════════
# ⑳ 深链死因「按主机留最后一条」= 把别人的死因安在这一条腿上（2026-11-16 实机假话）
# ════════════════════════════════════════════════════════════════════════
# 第八轮实机马可波罗日志（本轮抓到、当场修）：
#   两条页腿都没拿到正文（联系页 HTTP 403（正文是 200·人机验证壳（站方 WAF 质询…·仅这一条深链，通道入口未）、
#   商铺首页 HTTP 200（…））
# 联系页明明是 403，后面括号里却是商铺首页那页的壳原因——`_deep_blocks` 按主机归并、
# 每主机只留**最后一次**原因，而同一主机下两条路径的死因本来就可以不同（这正是 ⑬ 分账的
# 前提）。而且那句还被 `[:48]` 拦腰截断成「通道入口未」，读的人既拿到假原因又拿到半截话。
# 修法：主机账（条数/收尾一行）不动，另立一本**按 URL** 的死因原件；取原因只按 URL 查，
# 查不到就只报状态码（不编），截断一律落在分隔符上。
_host1, _url1 = "sjdskjc.cn.makepolo.com", "http://sjdskjc.cn.makepolo.com/contact_us.html"
_url2 = "http://sjdskjc.cn.makepolo.com/"
_c20 = _mp18.MakepoloCrawler()
_c20.reset_run_state()
_c20._note_deep_block(_url1, "HTTP 403（这一家的这条资源不给看，通道入口未受影响）")
_c20._note_deep_block(_url2, _c20.http_block_reason(200, _MP_WAF_SHELL, _url2, deep=True))
_w20 = _c20._refuse_why((("联系页", _url1, 403), ("商铺首页", _url2, 200)))
check("20a 同一主机两条路径、两种死因：各念各的，403 那一腿不许带上「人机验证壳」",
      "人机验证壳" not in _w20.split("、")[0] and "人机验证壳" in _w20.split("、")[1], _w20)
check("20a- 主机账照旧按主机聚合（条数与收尾那一行不受影响，别把 ⑬ 的分账改坏）",
      len(_c20.stats()["deep_block"]) == 1
      and sum(v[0] for v in _c20.stats()["deep_block"].values()) == 2,
      str(_c20.stats()["deep_block"]))
check("20b 没记过的 URL 就只报状态码：宁可不说话，也不把同主机别人的原因搬过来",
      _c20._refuse_why((("产品页", "http://sjdskjc.cn.makepolo.com/product/1.html", 439),))
      == "产品页 HTTP 439",
      _c20._refuse_why((("产品页", "http://sjdskjc.cn.makepolo.com/product/1.html", 439),)))
check("20b- 反向对照：旧写法（按主机取原因）在同样两笔账上会说假话——这条差分就是本节的立项依据",
      "人机验证壳" in str((_c20._deep_blocks.get(_host1) or [0, ""])[1]))

# 20c 截断只落在分隔符上（半截话比不说更误导）
_long = "正文是 200·人机验证壳（站方 WAF 质询，页里只有「正在检测」）·仅这一条深链，通道入口未受影响"
_s20 = CdpSiteCrawler._why_short(_long)
check("20c 深链那半句是重复信息（这话本来就只在深链路上说），短版把它去掉",
      _s20 == "正文是 200·人机验证壳（站方 WAF 质询，页里只有「正在检测」）", _s20)
_s20b = CdpSiteCrawler._why_short("这一家的这条资源不给看，通道入口未受影响，"
                                  "所以这一条腿按每家记账而不是整通道停手，"
                                  "下一次撞见同主域只累加不重复报", limit=30)
check("20c- 真要截断时截在标点前并带省略号，绝不留「通道入口未」这种半截词",
      _s20b.endswith("…") and not _s20b.rstrip("…").endswith(("未", "过", "的")), _s20b)
check("20c== 短句原样返回（判据不许把正常句子改花）",
      CdpSiteCrawler._why_short("HTTP 403 拒绝") == "HTTP 403 拒绝")

# 20d 有界：满了丢最早，别拿上一轮的旧原因念今天这一条
_c20d = _mp18.MakepoloCrawler()
_c20d.reset_run_state()
_c20d.DEEP_WHY_CAP = 3
for _i in range(5):
    _c20d._note_deep_block(f"http://s{_i}.cn.makepolo.com/contact_us.html", f"原因{_i}")
check("20d URL 死因原件有界（超上限丢最早那条），主机账的条数仍然全在",
      len(_c20d._deep_why) == 3 and _c20d._deep_block_why("http://s0.cn.makepolo.com/contact_us.html") == ""
      and _c20d._deep_block_why("http://s4.cn.makepolo.com/contact_us.html") == "原因4"
      and sum(v[0] for v in _c20d._deep_blocks.values()) == 5,
      str(_c20d._deep_why))
check("20d- 同一条 URL 再撞时原因会被最新那句覆盖（站方从限流壳换成放行，账要跟着改口）",
      _c20d._note_deep_block("http://s4.cn.makepolo.com/contact_us.html", "原因4新")
      == "s4.cn.makepolo.com"
      and _c20d._deep_block_why("http://s4.cn.makepolo.com/contact_us.html") == "原因4新")
check("20d== reset_run_state 也清这本账：跨轮留着，下一轮同一条 URL 换了死因也会被旧那句念出来",
      "self._deep_why = {}" in _fn_seg(_HTTP_SRC["cdp_base.py"], "reset_run_state"),
      _fn_seg(_HTTP_SRC["cdp_base.py"], "reset_run_state")[:120])

# 20e 机器判据：不许再有「拿主机账配单条 URL」的写法
# 合法碰这本主机账的只有这几处：写字人、按主机念次数的收尾、以及两处按轮清零。
_DEEP_HOST_OK = {"_note_deep_block", "_undo_deep_block", "stats", "reset_run_state", "__init__"}


def _host_why_problems(src):
    """除了那几处合法用法，任何函数都不许再碰 `_deep_blocks`。

    合法写字人只有两个：**记**（`_note_deep_block`）与**退**（`_undo_deep_block`——
    补救腿把这一家救回来了要退一笔，否则账上写着「被拒」而实际已出货＝假归因）。
    剩下三处（`stats`、`reset_run_state`、`__init__`）是读账和清零。
    其它地方碰这本「每主机只留最后一条」的账，就必然是同主机两条路径互相顶包——
    那一本从来就不是按 URL 设计的。要读原因只有 `_deep_block_why(url)` 这一把手，
    它查不到就回空串（不编）。
    """
    out = []
    for fn in (n for n in _ast.walk(_ast.parse(src)) if isinstance(n, _ast.FunctionDef)):
        seg = _ast.get_source_segment(src, fn) or ""
        if "_deep_blocks" in seg and fn.name not in _DEEP_HOST_OK:
            out.append(fn.name)
    return out


_h_hits = {n: v for n, s in _HTTP_SRC.items() if (v := _host_why_problems(s))}
check("20e 全仓没有第二处「按主机找单条 URL 的死因」（gys/11467 等深链账同口径，一处不许漏）",
      not _h_hits, str(_h_hits))
check("20e- 反向对照：⑳ 修前那一行写法必须被这条判据点出来，而按 URL 读原因的写法不许误响",
      _host_why_problems('class A:\n    def _refuse_why(self, legs):\n'
                         '        return self._deep_blocks.get(self._host_of(u))[1]\n')
      == ["_refuse_why"]
      and not _host_why_problems('class A:\n    def _refuse_why(self, legs):\n'
                                 '        return self._deep_block_why(u)\n'
                                 '    def _note_deep_block(self, url, reason):\n'
                                 '        self._deep_blocks[h] = [1, reason]\n'))
print("⑳ 深链死因按 URL 立原件：同主机两种死因不许互相顶包，半截话也不许再上日志 ✓")

# ════════════════════════════════════════════════════════════════════════
# ㉑ JS 质询壳的第三条路：借一枚标签把挑战跑完，再把通行证抄回裸 HTTP 会话
# ════════════════════════════════════════════════════════════════════════
# ⑱ 把壳**认出来**之后，第八轮实机（2026-11-16，注塑机/消防水带）的账显示：认出来仍然
# 只是不说谎，数据还是没拿回来——马可波罗商铺首页 41 跳里 19 跳是壳，而 BLOCK_OPEN_BROWSER
# =False 的站按 ip 档处置就是「等站方放行」，等于这一路当天作废。
# 壳里那段 JS 是**机器自己就能跑完**的（不需要任何人操作），所以这与「叫人工过滑块」是两件事：
# 借一枚标签（open_verify_page 那套现成机制）跑完挑战 → 抄通行证 → 重试这一跳。
# 关键是这条腿的**成败口径要诚实**：跑完挑战≠后面那一跳一定出数据，所以账上只写「跑完几次」，
# 而每一跳的结果仍由深链账自己回答；预算（每轮上限 + 一条链最多重一次）必须真落下。
_c21 = _mp18.MakepoloCrawler()
_c21.reset_run_state()
_ev21, _lg21 = equip(_c21)
_c21._pace_before_request = lambda *a, **k: None
_open21, _rel21 = [], []
_c21.open_verify_page = lambda url="": _open21.append(url) or True
_c21._release_verify_tab = lambda: _rel21.append(1)


class _R21:
    def __init__(self, body, code):
        self.content = body.encode("utf-8")
        self.status_code = code
        self.headers = {}
        self.text = body


class _SS21:
    """按「第几次请求」回第几份正文：壳 → （借标签跑完）→ 真页。"""

    def __init__(self, seq):
        self.seq, self.n = seq, 0

    def get(self, url, timeout=None, headers=None):
        body, code = self.seq[min(self.n, len(self.seq) - 1)]
        self.n += 1
        return _R21(body, code)


# 21a 五档成败：预算用满／借不到标签／挑战没跑完／没抄到通行证／跑通
_c21._session = _SS21([(_MP_WAF_SHELL, 200), (_MP_REAL_HOME, 200)])
_c21._html = lambda: _MP_WAF_SHELL                    # 标签里那页还是壳
_c21._harvest_browser_cookies = lambda: 2
_ft21 = _c21._fetch("http://a.cn.makepolo.com/", enc="utf-8", tries=1, guarded=False, deep=True)
check("21a 挑战没跑完（标签里那页还是壳）：不许谎报成功，这一跳照旧按深链被拒收尾",
      _c21._js_tab_tries == 1 and _c21._js_tab_hits == 0 and not _ft21
      and any("还是质询壳" in m for m in _lg21), str(_c21._js_tab_tries))
check("21a- 失败也把临时借的标签还掉（finally 那条路，挂着标签＝占着别人的窗口）",
      _rel21 == [1], str(_rel21))
_c21._session = _SS21([(_MP_WAF_SHELL, 200), (_MP_REAL_HOME, 200)])   # 每一档都从「第一跳就是壳」重新起
_c21._html = lambda: _MP_REAL_HOME                    # 挑战跑完了，标签里那页像真页
_c21._harvest_browser_cookies = lambda: 0             # 但通行证一条都没抄到
_c21._current_url = lambda: "http://a.cn.makepolo.com/index.html"   # 站方把挑战后的落点改去了首页
_c21._fetch("http://a.cn.makepolo.com/contact_us.html", enc="utf-8", tries=1, guarded=False, deep=True)
check("21b 跑完挑战却抄不到通行证：说清「站方把放行记在 IP 上」，不许算成这条腿成功了",
      _c21._js_tab_hits == 0 and any("没抄到本站通行证" in m for m in _lg21),
      str(_c21._js_tab_hits))
check("21b- 同主机但落点不是这一页：身份门必须拒收，且拒收的理由要说得出（串页假数据比采不到更坏）",
      _c21._js_tab_body_hits == 0
      and any("标签落的不是这一页" in m and "a.cn.makepolo.com/index.html" in m for m in _lg21),
      str(_lg21[-2:]))
# 21b2 第九轮实机逼出来的第二条出货路：马可波罗 3/3 全是「挑战跑完了、浏览器里一条本站
# cookie 都没有」——放行记在 IP／会话上，requests 那条 TLS 指纹对不上。此时标签里那一页
# **已经是真页**，旧写法把它扔掉、回去重走注定还是壳的 HTTP 一跳，这条腿就永远 0 命中。
_c21._session = _SS21([(_MP_WAF_SHELL, 200), (_MP_REAL_HOME, 200)])
_c21._harvest_browser_cookies = lambda: 0             # 站方就是不按 cookie 放行
_c21._current_url = lambda: "http://a.cn.makepolo.com/contact_us.html"   # 落点正是我要的这一页
_ft21b2 = _c21._fetch("http://a.cn.makepolo.com/contact_us.html", enc="utf-8", tries=1,
                      guarded=False, deep=True)
check("21b2 抄不到通行证但标签里就是这一页：正文直接拿走，这条腿从此能出数（不再只能报「等站方放行」）",
      "block2" in (_ft21b2 or "") and _c21._js_tab_body_hits == 1 and _c21._js_tab_hits == 0,
      str(_ft21b2)[:60])
check("21b2- 走标签取页这一档时**不再多发那一跳**（裸 HTTP 明知还是壳，重取只是白花一次配额）",
      _c21._session.n == 1, f"请求={_c21._session.n}")
_c21._session = _SS21([(_MP_WAF_SHELL, 200), (_MP_REAL_HOME, 200)])
_c21._harvest_browser_cookies = lambda: 3
_c21._js_tab_tries = 0
_open21.clear()                       # 本档只看「这一跳」借了几次标签，不累计前两档
_ft21b = _c21._fetch("http://a.cn.makepolo.com/", enc="utf-8", tries=1, guarded=False, deep=True)
check("21c 端到端：第一跳是壳 → 借标签跑完挑战并抄回通行证 → 重试这一跳拿到 block2 真页",
      "block2" in (_ft21b or "") and _c21._js_tab_hits == 1 and _c21._js_tab_tries == 1,
      str(_ft21b)[:60])
check("21c- 借标签确实只多花了一次请求（壳 1 次 + 重试 1 次），预算不是纸面上的",
      _c21._session.n == 2 and _open21 == ["http://a.cn.makepolo.com/"],
      f"请求={_c21._session.n} 借页={_open21}")
_c21._session = _SS21([(_MP_WAF_SHELL, 200), (_MP_REAL_HOME, 200)])          # 这一档：站方每一跳都回壳
_c21._js_tab_hits = 0                              # 21c/21b2 已让这条腿出过货，预算放开到 40
_c21._js_tab_body_hits = 0                         # 「预算由账给」要成立，先清零这一轮的账
_c21._js_tab_tries = _c21.JS_SHELL_TAB_MAX
_open21.clear()
_c21._fetch("http://a.cn.makepolo.com/", enc="utf-8", tries=1, guarded=False, deep=True)
check("21d 每轮上限真的落下：到点就不再开标签，并说一句「已到上限」（不开口就是悄悄放弃这条路）",
      not _open21 and any("已到上限" in m for m in _lg21), str(_open21))
check("21d- 上限那句要念得出**是几次**：只说「已到上限」而数字来自哪一档没人知道，调参时必猜错",
      any(f"已到上限（{_c21.JS_SHELL_TAB_MAX} 次）" in m for m in _lg21), str(_lg21[-1:]))
# 21d2 预算由账给：一次货都没出只许试错 3 次；出过货放开到 40——同一个数字要么把能出货的
# 路堵死，要么让出不了货的路白烧标签（马可波罗两种结局都实测过）。
_c21._js_tab_tries = _c21.JS_SHELL_TAB_MAX
check("21d2 这条腿本轮一次货都没出→预算就是试错档（JS_SHELL_TAB_MAX）",
      _c21._js_tab_budget() == _c21.JS_SHELL_TAB_MAX, str(_c21._js_tab_budget()))
_c21._js_tab_body_hits = 1
check("21d2- 只要**标签取页**出过一次货（抄不到通行证那种站）预算就该放开——旧口径只认 cookie"
      "那一档，马可波罗正好卡在这一档被 3 次判死",
      _c21._js_tab_budget() == _c21.JS_SHELL_TAB_MAX_OK, str(_c21._js_tab_budget()))
_c21._js_tab_body_hits, _c21._js_tab_hits = 0, 1
check("21d2== 抄通行证出过货同样放开预算（两条出货路对称）",
      _c21._js_tab_budget() == _c21.JS_SHELL_TAB_MAX_OK, str(_c21._js_tab_budget()))
_c21._js_tab_hits = 0
check("21d2=== 预算按轮清零：上一轮 0 命中不许把这一轮直接判死",
      _c21._js_tab_budget() == _c21.JS_SHELL_TAB_MAX, str(_c21._js_tab_budget()))

# 21d- 闩：重取到的那一跳**又是壳**时不许再借一次标签（预算会全烧在同一家身上）
_c21l = _mp18.MakepoloCrawler()
_c21l.reset_run_state()
_evl, _lgl = equip(_c21l)
_c21l._pace_before_request = lambda *a, **k: None
_opens_l = []
_c21l.open_verify_page = lambda url="": _opens_l.append(url) or True
_c21l._release_verify_tab = lambda: None
_c21l._html = lambda: _MP_REAL_HOME               # 标签里那页能跑完挑战
_c21l._harvest_browser_cookies = lambda: 3
_c21l._session = _SS21([(_MP_WAF_SHELL, 200)] * 9)    # 但这一家就是不给真页
_c21l._fetch("http://a.cn.makepolo.com/", enc="utf-8", tries=1, guarded=False, deep=True)
check("21d- 同一条调用链最多因壳重取一次：重试回来的还是壳时不再借第二次标签（旧写法会把"
      "整轮预算钉死在一家门上）",
      _c21l._js_tab_tries == 1 and len(_opens_l) == 1 and _c21l._session.n == 2,
      f"借标签={_c21l._js_tab_tries} 开页={len(_opens_l)} 请求={_c21l._session.n}")

# 21e 通道入口那一档：跑通就不进几十分钟的 IP 等待；跑不通照旧等（不许假装解开了）
_c21e = _mp18.MakepoloCrawler()
_c21e.reset_run_state()
_ev21e, _lg21e = equip(_c21e)
_c21e._block_reason = ""
_c21e._block_needs_js = True
_c21e._solve_js_shell_via_tab = lambda url: True
check("21e 通道级撞上壳且借标签跑通：直接返回「可续采」，不进 IP 档那几十分钟干等",
      _c21e.handle_http_block("http://cn.makepolo.com/", "站点返 200 但整页正文是人机验证壳")
      is True and not FT.slept, str(FT.slept[:3]))
_c21f = _mp18.MakepoloCrawler()
_c21f.reset_run_state()
_ev21f, _lg21f = equip(_c21f)
_c21f._block_needs_js = True
_c21f._solve_js_shell_via_tab = lambda url: False
_c21f.probe_block = lambda: "站点返 200 但整页正文是人机验证壳"
_c21f.BLOCK_POLL_IP, _c21f.BLOCK_MAX_WAIT_IP = 45, 45
FT.reset()
check("21f- 反向对照：这条腿跑不通时照旧按 IP 档等待，不因「试过标签」就谎报已解除",
      _c21f.handle_http_block("http://cn.makepolo.com/", "站点返 200 但整页正文是人机验证壳")
      is False, "")

# 21g 抄 Cookie 的域：没声明 BLOCK_COOKIE_DOMAIN 的站退回 HOME_DOMAIN（漏了就永远抄 0 条）
_c21g = _mp18.MakepoloCrawler()
_c21g.reset_run_state()
_got21 = []


class _J21:
    def set(self, name, value, domain=None, path=None):
        _got21.append((name, domain))


_c21g._session = __import__("types").SimpleNamespace(cookies=_J21())
_c21g.pin_tab_id = "TAB1"
_c21g._send = lambda method, timeout=None: {"result": {"cookies": [
    {"name": "wdw", "value": "x", "domain": ".makepolo.com", "path": "/"},
    {"name": "other", "value": "y", "domain": ".example.com", "path": "/"}]}}
check("21g 马可波罗没写 BLOCK_COOKIE_DOMAIN，抄 Cookie 退回 HOME_DOMAIN 并只收本站那条",
      _c21g.BLOCK_COOKIE_DOMAIN == "" and _c21g._harvest_browser_cookies() == 1
      and _got21 == [("wdw", ".makepolo.com")], str(_got21))

# 21h 机器判据：**看过 HTTP 响应**才记深链账的函数必须同时挂上这条腿（七站同形状，抄漏就分叉）。
# 为什么以 `http_block_reason(` 为界：这条腿干的事是「把裸 HTTP 会话没跑成的 JS 挑战交给浏览器跑一遍」，
# 而 gys `_fetch_raw` 那一处记的是**浏览器已经渲染完仍然停在插页**（`深链渲染出防采插页（浏览器同样不给看）`）——
# 再借一枚标签只是把刚失败的那件事重做一遍，那种调用点不该被误伤（误伤＝下一站真漏了时判据已被人当噪音跳过）。
def _shell_leg_problems(src):
    out = []
    for fn in (n for n in _ast.walk(_ast.parse(src)) if isinstance(n, _ast.FunctionDef)):
        seg = _ast.get_source_segment(src, fn) or ""
        args = [a.arg for a in (fn.args.args + fn.args.kwonlyargs)]
        if ("deep" in args and "_note_deep_block(" in seg
                and "http_block_reason(" in seg and "_deep_shell_retry(" not in seg):
            out.append(fn.name)
    return out


_leg_hits = {n: v for n, s in _HTTP_SRC.items()
             if n != "cdp_base.py" and (v := _shell_leg_problems(s))}
check(f"21h 每一站的深链分支都接上了「借标签跑壳」这条腿（{len(_HTTP_SRC) - 1} 份源码）",
      not _leg_hits, str(_leg_hits))
check("21h- 反向对照：只记深链账、不接这条腿的旧形状必须被点出来",
      _shell_leg_problems('class A:\n    def _fetch(self, url, deep=False):\n'
                          '        block = self.http_block_reason(r.status_code, r.text, url, deep=deep)\n'
                          '        if block:\n            self._note_deep_block(url, block)\n'
                          '            return ""\n') == ["_fetch"])
check("21h= 上限/等待/开关都是类常量（写死在函数里＝下一个站想调就得改代码）",
      all(hasattr(CdpSiteCrawler, x) for x in
          ("BLOCK_JS_SHELL_TAB", "JS_SHELL_TAB_MAX", "JS_SHELL_TAB_WAIT", "DEEP_WHY_CAP")))
check("21h== 浏览器渲染那一档的死链账不许被这条判据误伤（判据只问『看过响应体』的调用点，"
      "否则 gys 那种合法豁免会被当成噪音整条划掉）",
      _shell_leg_problems('class A:\n    def _fetch_raw(self, url, deep=False):\n'
                          '        if self._is_abnormal_page() and deep:\n'
                          '            self._note_deep_block(url, "深链渲染出防采插页")\n'
                          '        return "", ""\n') == [])
# gys 这一处是真豁免还是漏接，得由源码自己回答：它渲染那一路确实没经过 http_block_reason
check("21h=== gys `_fetch_raw` 的深链账确实只来自浏览器渲染（不是漏接这条腿的借口）",
      "http_block_reason(" not in (_ast.get_source_segment(
          _HTTP_SRC["gys_crawler.py"],
          next(n for n in _ast.walk(_ast.parse(_HTTP_SRC["gys_crawler.py"]))
               if isinstance(n, _ast.FunctionDef) and n.name == "_fetch_raw")) or ""))
# ── 21i 身份门本体：从浏览器标签把正文当「某一家的深链正文」交出去之前，必须证明
#    标签当前那一页就是我要的那一页。域名＋路径算同一页，query 里那串质询令牌不算。
_c21i = _mp18.MakepoloCrawler()
_c21i.reset_run_state()
equip(_c21i)
_WANT21 = "http://a.cn.makepolo.com/contact_us.html"
_c21i._pace_before_request = lambda *a, **k: None
_id_cases = [
    ("同一页", _WANT21, _WANT21, True, ""),
    ("站方在地址后面补了质询令牌（路径没变）", _WANT21, _WANT21 + "?__jsl_clearance=s", True, ""),
    ("锚点不同仍是同一页", _WANT21, _WANT21 + "#contact", True, ""),
    ("末尾斜杠写法差异", "http://a.cn.makepolo.com/shop/", "http://a.cn.makepolo.com/shop", True, ""),
    ("同主机不同路径（落到别家/首页）", _WANT21, "http://a.cn.makepolo.com/index.html",
     False, "标签落的不是这一页"),
    ("换到别家子域（马可波罗每家一个子域）", _WANT21, "http://b.cn.makepolo.com/contact_us.html",
     False, "标签不在本站"),
    ("标签停在验证页", _WANT21, "http://www.makepolo.com/verify", False, "标签不在本站"),
    ("地址根本读不出来", _WANT21, "", False, "标签不在本站"),
]
for _lbl21, _want, _cur, _exp_ok, _exp_why in _id_cases:
    _c21i._current_url = (lambda u=_cur: u)
    _ok21, _why21 = _c21i._tab_page_is(_want)
    check(f"21i 身份门·{_lbl21} → {'放行' if _exp_ok else '拒收（' + _exp_why + '）'}",
          _ok21 is _exp_ok and (_exp_ok or _exp_why in _why21), f"{_ok21}｜{_why21}")
# token 模式（mic 那种「凭 supplier user 串在地址里」的站）：认地址里的凭据，不硬比路径
_c21i._current_url = lambda: "https://www.mic.cn/shop/ab123-contact-us.html"
check("21i-token 给了凭据时按「同主机＋地址带这段」判，站方改写文件名不该把它拒掉",
      _c21i._tab_page_is("https://www.mic.cn/shop/ab123-contact.html", token="ab123") == (True, ""), "")
_c21i._current_url = lambda: "https://www.mic.cn/shop/zz999-contact.html"     # 标签其实在别家
check("21i-token- 反向对照：标签停在**别家**的页上就必须拒（把 A 家的页当 B 家＝串页）",
      _c21i._tab_page_is("https://www.mic.cn/shop/ab123-contact.html", token="ab123")[0] is False
      and "凭据" in _c21i._tab_page_is("https://www.mic.cn/shop/ab123-contact.html",
                                        token="ab123")[1],
      str(_c21i._tab_page_is("https://www.mic.cn/shop/ab123-contact.html", token="ab123")))
# same_host=False：请求的主机名本来就是猜的（mic 英文子域），站方换主机名不算换供应商
_c21i._current_url = lambda: "https://www.mic.cn/showroom/ab123/"
check("21i-same 猜出来的子域被站方领到统一 showroom 路径：凭据还在→放行（继续要求同主机＝把好渠道判死）",
      _c21i._tab_page_is("https://ab123.mic.cn/", token="ab123", same_host=False)[0] is True, "")
check("21i-same- 反向对照：同一地址上不许顺手放掉身份——去掉凭据后这条跨主机路必须被拒",
      _c21i._tab_page_is("https://ab123.mic.cn/", same_host=False)[0] is False, "")
check("21i-same== 反向对照：带着凭据但标签其实是别家（换主机≠免检）",
      _c21i._tab_page_is("https://ab123.mic.cn/", token="zz999", same_host=False)[0] is False, "")
# 正文那一档：地址对了还要问「这页能不能算正文」
for _lbl21, _body21, _exp in [
        ("空正文", "", "读不出来"),
        ("还是质询壳", _MP_WAF_SHELL, "还是质询壳"),
        ("几十十字的跳转中间态", "<html>" + "x" * 200 + "</html>", "不像一页正文")]:
    _c21i._current_url = lambda: _WANT21
    _c21i._html = lambda b=_body21: b
    _got21i, _why21i = _c21i._tab_body_for(_WANT21)
    check(f"21i-体积/内容门·{_lbl21}：不许当正文交出，且要说清为什么（{_exp}）",
          not _got21i and _exp in _why21i, f"{_why21i}")
_c21i._current_url = lambda: _WANT21
_c21i._html = lambda: _MP_REAL_HOME
check("21i== 地址对、内容像一页正文→放行（这条腿的正路不能被门误伤）",
      _c21i._tab_body_for(_WANT21)[0] == _MP_REAL_HOME, "")

# ── 21j 回账配对：调用方的固定顺序是「先记被拒、再叫这条腿」，腿成了不回账，体检报告就会
#    把一条能出数据的路判成「近乎零产出」（第九轮实机马可波罗正是这一档）。
_c21j = _mp18.MakepoloCrawler()
_c21j.reset_run_state()
equip(_c21j)
_U21 = "http://a.cn.makepolo.com/contact_us.html"
_c21j._note_deep_block(_U21, "HTTP 200（正文是 200·人机验证壳）")
check("21j 夹具成立：记完之后主机账 1 笔、URL 死因 1 条、同主域已开口",
      _c21j._deep_blocks["a.cn.makepolo.com"][0] == 1 and _c21j._deep_block_why(_U21)
      and "cn.makepolo.com" in _c21j._deep_logged, str(_c21j._deep_blocks))
_c21j._undo_deep_block(_U21)
check("21j- 补救腿出货后主机账与 URL 死因都要退干净（留着就是归因说谎）",
      "a.cn.makepolo.com" not in _c21j._deep_blocks and _c21j._deep_block_why(_U21) == "",
      f"{_c21j._deep_blocks}｜{_c21j._deep_why}")
check("21j== 连「同主域只开口一次」那份静音凭据一起撤：这一路后面真被拒时还得再说话",
      "cn.makepolo.com" not in _c21j._deep_logged, str(_c21j._deep_logged))
_c21j._note_deep_block(_U21, "HTTP 403 防采插页")
_c21j._note_deep_block("http://b.cn.makepolo.com/x.html", "HTTP 403 防采插页")
_c21j._undo_deep_block(_U21)
check("21j=== 只退这一家：同主机别的路径的账不许被一起抹掉（退多了＝把真被拒的说成没被拒）",
      _c21j._deep_blocks.get("b.cn.makepolo.com", [0])[0] == 1, str(_c21j._deep_blocks))
_c21j._undo_deep_block(_U21)
check("21j==== 重复退不许把账退成负数（负数会在收尾那行念出「被拒 -1 次」这种疯话）",
      all(v[0] >= 0 for v in _c21j._deep_blocks.values())
      and _c21j._deep_blocks.get("a.cn.makepolo.com") is None, str(_c21j._deep_blocks))
_c21j._undo_deep_block("http://never-noted.cn.makepolo.com/y.html")
check("21j===== 没记过的 URL 退账不许抛异常（补救腿常在异常路径上，炸了会把正文一起吞掉）", True)
# 端到端：腿从标签取到正文时，回账必须跟着发生（不是只靠调用方记得写）
_c21k = _mp18.MakepoloCrawler()
_c21k.reset_run_state()
_evk, _lgk = equip(_c21k)
_c21k._pace_before_request = lambda *a, **k: None
_c21k.open_verify_page = lambda url="": True
_c21k._release_verify_tab = lambda: None
_c21k._html = lambda: _MP_REAL_HOME
_c21k._harvest_browser_cookies = lambda: 0
_c21k._current_url = lambda: "http://a.cn.makepolo.com/contact_us.html"
_c21k._session = _SS21([(_MP_WAF_SHELL, 200)])
_c21k._note_deep_block("http://a.cn.makepolo.com/contact_us.html", "HTTP 200（正文是 200·人机验证壳）")
_out21k = _c21k._deep_shell_retry("http://a.cn.makepolo.com/contact_us.html", 200,
                                  _MP_WAF_SHELL, lambda: "")
check("21j2 端到端：标签取页这条路出货时，深链那本账当场退一笔（不用调用方自己记得）",
      _out21k == _MP_REAL_HOME and "a.cn.makepolo.com" not in _c21k._deep_blocks
      and _c21k._js_tab_body_hits == 1, str(_c21k._deep_blocks))
check("21j2- 退完这笔之后 stats() 里不能再出现这一路（收尾那行念的是**净**被拒数）",
      "a.cn.makepolo.com" not in str(_c21k.stats().get("deep_block", "")),
      str(_c21k.stats().get("deep_block")))
_c21m = _mp18.MakepoloCrawler()
_c21m.reset_run_state()
equip(_c21m)
_c21m._harvest_browser_cookies = lambda: 4
_c21m._js_tab_body = ""
check("21j3 抄到通行证那一档：正文格必须是空的（两条出货路不许互相冒充，否则 _deep_shell_retry"
      "会把空串当『标签已经交货』收走）",
      _c21m._deep_shell_retry("http://a.cn.makepolo.com/x.html", 200, _MP_WAF_SHELL,
                              lambda: "REFETCHED") == "REFETCHED"
      and _c21m._js_tab_body == "", "")

# ── 21k 机器判据：**直接跑构建前那条硬门（第 31 条）本尊**，不在这里重写一遍判据。
#    为什么必须复用而不是抄一份：同一个族在本仓已经有过两次教训——抄一份就会长成两份口径，
#    而改实现的人只会撞到其中一份（第 30 条的把手、`_page_key` 都是这样收拢的）。这里跑真的那份，
#    离线开发环与构建前门禁就对「什么算串页出口」只有一套答案。
#    判据的存在理由：同一个坑在四个站各长了一遍（麦麦只防了「重载回哪一页」、顺企网只看主机名、
#    MIC 只拒 errordocs、gys 只断域名），靠人记是记不住的——第五个站一定会漏。
def _load_gate31():
    import importlib.util
    path = HERE.parent / ".github" / "scripts" / "check_packaging_manifest.py"
    if not path.is_file():
        return None, f"{path} 读不到：第 31 条的门不在位（这一条不许默默跳过，要响）"
    spec = importlib.util.spec_from_file_location("_gate31_mirror", path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        return None, f"门禁脚本加载失败（{type(e).__name__}: {e}）：第 31 条没有对照面"
    return mod, ""


_gate31, _gate31_err = _load_gate31()
check("21k 构建前那条硬门（第 31 条）能加载——加载不到就等于离线这一侧失明",
      not _gate31_err, _gate31_err)
if _gate31 is not None:
    _p31 = _gate31.check_tab_body_identity()
    check("21k 全交付链「把标签正文交出去」的每一处出口都在案、且登记的门真在源码里",
          not _p31, " ｜ ".join(x.splitlines()[0] for x in _p31[:4]))
    # 反向对照：门被逐处摘掉时判据必须响（这些形状就是本次修复前的原样，取自真实源码）
    _frag31 = {
        "摘掉 gys 深链那道门": ('class A:\n    def _fetch_raw(self, url, deep=False):\n'
                               '        self._goto(url, expect_domain="gys.cn")\n'
                               '        rendered = self._html()\n'
                               '        return rendered, "browser"\n'),
        "摘掉麦麦取正文那道门（只把 _html() 直接交出）": ('class A:\n    def _fetch_via_tab(self, url):\n'
                                                        '        html = self._html()\n'
                                                        '        return html\n'),
        "把门改成死代码后仍然交出正文（关键字在≠门在）": ('class A:\n    def _fallback_en(self, user, rec):\n'
                                                        '        html = self._html()\n'
                                                        '        self._parse_contact(html, rec)\n'),
        # 这一形专门钉「污点传播只认 Call」那个盲区：门摘掉时人常写成元组转手，出口若因此从
        # 清单上消失，表里那一行会反过来变成「过期登记」，把删门糊成「这条腿已经不取标签了」
        "摘门时把正文经元组赋值转手（body, why = html, \"\"）": ('class A:\n    def _fetch_via_tab(self, url, rec):\n'
                                                              '        html = self._html()\n'
                                                              '        body, why = html, ""\n'
                                                              '        return body\n'),
    }
    for _lbl31, _src31 in _frag31.items():
        _t31 = _ast.parse(_src31)
        # 摘门之后两种「有人在管」的样子都拦不住：①这条出口从没登记过；②登记成域名级
        # `_goto`（表好看、源码里也真有 `_goto(`，可签名带着「这一家」的把手，域名级不够）
        _row31 = {n: (g[0] if g else "_goto") for n, _l, _h, g in
                  _gate31._tab_body_exits(_t31)}
        _got31 = _gate31._tab_exit_problems("夹具", _t31, {})
        _got31b = _gate31._tab_exit_problems("夹具", _t31, _row31)
        check(f"21k- 反向对照（{_lbl31}）：未登记的出口必须被点出来",
              bool(_got31), str(_got31))
        check(f"21k-1b 反向对照（{_lbl31}）：拿域名级门糊深链出口也必须被点出来",
              bool(_got31b), str(_got31b))
    check("21k-2 反向对照：登记了 `_tab_body_for` 而源码里那道门已被摘掉 → 必须响",
          bool(_gate31._tab_exit_problems(
              "夹具", _ast.parse('class A:\n    def _fetch_via_tab(self, url):\n'
                                 '        html = self._html()\n        return html\n'),
              {"_fetch_via_tab": "_tab_body_for"})), "")
    check("21k-3 反向对照：深链只登记域名级 `_goto`（签名里带着「这一家」的把手）→ 必须响",
          bool(_gate31._tab_exit_problems(
              "夹具", _ast.parse('class A:\n    def _fetch_page(self, user, rec):\n'
                                 '        self._goto(url)\n        html = self._html()\n'
                                 '        self._parse_contact(html, rec)\n'),
              {"_fetch_page": "_goto"})), "")
    check("21k== 反向对照：在案且门在位的两种写法都不许误响（误伤一次，判据下一次就被人当噪音划掉）",
          _gate31._tab_exit_problems(
              "夹具", _ast.parse('class A:\n    def _fetch_via_tab(self, url):\n'
                                 '        body, why = self._tab_body_for(url)\n'
                                 '        return body\n'),
              {"_fetch_via_tab": "_tab_body_for"}) == []
          and _gate31._tab_exit_problems(
              "夹具", _ast.parse('class A:\n    def _iter_keyword(self, kw):\n'
                                 '        self._goto(url, expect_domain="x.com")\n'
                                 '        html = self._html()\n'
                                 '        return self.parse_list(html, kw)\n'),
              {"_iter_keyword": "_goto"}) == [], "")
    check("21k==2 反向对照：能力探针（交出去的是判断不是正文）不进出口清单",
          _gate31._tab_body_exits(_ast.parse('class A:\n    def _tab_page_ok(self, url):\n'
                                             '        return bool(self._tab_real_page'
                                             '(self._html() or ""))\n')) == [], "")
    check("21k=== 身份门的地址判据只有一份真源：三个把手都只准定义在基类",
          all(sum(s.count(f"def {n}(") for s in _HTTP_SRC.values()) == 1
              for n in _gate31.TAB_ID_HANDLES),
          str({n: [f for f in _HTTP_SRC if f"def {n}(" in _HTTP_SRC[f]]
               for n in _gate31.TAB_ID_HANDLES}))
print("㉑ 壳认出来之后还要能自己走过去：借标签跑挑战＋抄通行证＋标签取页＋账上看得见 ✓")

# ════════════════════════════════════════════════════════════════════════
# ㉒ 原因的收口只准一个把手：半截词比不说更误导
# ════════════════════════════════════════════════════════════════════════
# 由来（2026-11-16 第八轮实机体检报告里原样的一行）：
#   ❌ 顺企网: 链路未通：2 页一页都没取到（最后一次：【连不上·无应答】b2b.11467.com:443｜
#      Connection to b2b.11467.com timed out. (con）——先查出口网络/域名可达…
# 这一句每个字都对，唯独最后被 [:70] 剪掉了半边：`(con）` 里那个右括号还是分型器补的，
# 补完又被剪——读的人既要猜「timed out 之后还有什么」，又要猜那对括号是不是我漏看的。
# 同一条链上有三处各写各的截断（分型器 cap、日志 [:60]/[:70]、体检报告 [:70]），⑳ 修掉的
# 「通道入口未」只是其中一档。整族收进 `short_reason` 一个把手，并用机器判据钉住其余各处。
_SR = cdp_base.short_reason
_R22 = ("【连不上·无应答】b2b.11467.com:443｜Connection to b2b.11467.com timed out. "
        "(connect timeout=10)")
_s22 = _SR(_R22, 70)
check("22a 实机那一句收口后断在标点前：括号里那段整块不保留，也不留半截词",
      _s22.endswith("timed out.…") and "connect timeout" not in _s22
      and _s22.startswith("【连不上·无应答】b2b.11467.com:443"), _s22)
check("22a- 【类别】主机:端口｜ 这一截永远整段留住（上限再小也留住）：处方就写在前缀里",
      _SR(_R22, 30).startswith("【连不上·无应答】b2b.11467.com:443｜Connection")
      and len(_SR(_R22, 30)) > 30, _SR(_R22, 30))
check("22b 本来就读得完的句子一个字都不改（收口不是改写，也不是加话）",
      _SR("HTTP 403（这一家的这条资源不给看）", 48) == "HTTP 403（这一家的这条资源不给看）")
check("22c 整句一个边界都没有时**宁可整句带省略号**，也不截半截词",
      _SR("x" * 90, 20) == "x" * 90 + "…", _SR("x" * 90, 20)[:24])

# 22d 机器性质：拿 ⑪ 那批**真实异常文本**逐档收口，断点必须落在原句的边界上
_bad22 = []
for _k22, _c22, _t22, _p22 in _ERR_SAMPLES:
    _orig22 = _shape(_as_err(_c22, _t22))
    for _cap22 in (24, 40, 60, 70, 110):
        _r22 = _SR(_orig22, _cap22)
        if _r22 == _orig22:
            continue
        _base = _r22.split("…", 1)[0]
        if not _base or not _orig22.startswith(_base):
            _bad22.append((_k22, _cap22, _r22))
        elif (len(_base) < len(_orig22)
              and _base[-1] not in cdp_base._REASON_BREAKS
              and _orig22[len(_base)] not in cdp_base._REASON_BREAKS):
            _bad22.append((_k22, _cap22, "断点不在边界上：" + _r22))
        # 带类别的句子：`【…】host:port｜` 这一截在任何上限下都必须整段还在（处方在前缀里）
        if _orig22.startswith("【") and "｜" in _orig22 and not _base.startswith(
                _orig22.split("｜", 1)[0] + "｜"):
            _bad22.append((_k22, _cap22, "前缀被截短：" + _base))
check(f"22d 每一档收口的断点都落在原句的标点/词边界上（{len(_ERR_SAMPLES)} 档真异常 × 5 档上限）",
      not _bad22, str(_bad22[:2]))
check("22d- 收口后括号必成对（旧写法先补右括号再一刀切，实机就长出 `(con）`）",
      all(_SR(_shape(_as_err(_c22, _t22)), _cap22).count("(") ==
          _SR(_shape(_as_err(_c22, _t22)), _cap22).count(")")
          for _k22, _c22, _t22, _p22 in _ERR_SAMPLES for _cap22 in (24, 40, 60, 70, 110)))

# 22f 反向对照（立项依据）：同一句真异常按**旧渲染写法**收口，必须真的长出半截话
_old22 = _shape(_as_err("ConnectTimeout", _ERR_SAMPLES[0][2]))[:70]
check("22f 反向对照：旧写法 `整形后的句子[:70]` 在这一档上确实截出孤括号/半截词"
      "（不响就是这条门没有存在必要）",
      _old22.count("(") != _old22.count(")") or bool(re.search(r"[A-Za-z]{3}$", _old22)), _old22)

# 22g 机器判据（同类查全）：交付链里**已经定过性的原因句子**不许再被谁自己 [:N]
_REASON_NAMES = ("why", "shape", "reason", "last_err", "still", "msg",
                 "_last_page_error", "last_page_error", "read_err")


def _slice_problems(src):
    out = []
    for node in _ast.walk(_ast.parse(src)):
        if (isinstance(node, _ast.Subscript) and isinstance(node.slice, _ast.Slice)
                and node.slice.lower is None
                and isinstance(node.slice.upper, _ast.Constant)
                and isinstance(node.slice.upper.value, int)):
            seg = _ast.get_source_segment(src, node.value) or ""
            if any(re.search(rf"\b{n}\b", seg) for n in _REASON_NAMES):
                out.append(f"第 {node.lineno} 行 {seg}[:{node.slice.upper.value}]")
    return out


_slice_srcs = dict(_HTTP_SRC)
_slice_srcs["ali1688_crawler.py"] = _src_a1611
_slice_srcs["platform_live_check.py"] = _LIVE_SRC
_slice_srcs.update({p.name: p.read_text(encoding="utf-8") for p in
                    (HERE / "b2bcollect_gui.py", HERE / "parallel_session.py") if p.exists()})
_hits22 = {n: v for n, s in _slice_srcs.items() if (v := _slice_problems(s))}
check(f"22g 交付链没有一处还在对定过性的原因自己 [:N]（扫了 {len(_slice_srcs)} 份源码）",
      not _hits22, str(_hits22))
check("22g- 反向对照：这一族历史上写过的三种形状（日志 [:60]／报告 [:70]／深链 [:48]）都得被点出来",
      _slice_problems('self._log(f"（{why[:60]}）")')
      and _slice_problems('x = f"{last_err[:70]}"')
      and _slice_problems('return self._why_short(reason)[:48]'),
      "抓不到旧写法＝这条门下一个人只会把它整个关掉")
check("22g== 走把手的写法与「跟原因无关的截断」都不许误响（误响＝真缺口被当成噪音划掉）",
      not _slice_problems('self._log(short_reason(why, 60))')
      and not _slice_problems('self._log(url[:70])')
      and not _slice_problems('note = str(rec["备注"])[:80]'))

# 22h 两份镜像（1688 独立包自己一份）：收口这件事分叉＝同一件故障两边读出两种长度。
#     自第 ㉓ 节起真源搬到 b2bcollect_build/text_shape.py，1688 那份照旧内联，所以这里比的
#     是**把手整段**（断点表 + 连接符表 + `_is_break` + 两个函数），与硬门 30b 同一段边界。
_HANDLE_SRC = (_REPO / "b2bcollect_build" / "text_shape.py").read_text(encoding="utf-8")
_REASON_BLOCK = ("# 可断的位置", "    return short_reason(s.splitlines()[0], limit)")
check("22h 把手整段两份镜像**同字节**（只比答案比不出「断点表」被谁悄悄改短）",
      _between(_HANDLE_SRC, *_REASON_BLOCK) == _between(_src_a1611, *_REASON_BLOCK),
      "text_shape 与 ali1688_crawler 内联那段有差异")
check("22h- 分型器收口两份同字节（一边截词一边不截＝两套结论）",
      _between(_src_cdp11, "def _request_error_detail(text, cap):",
               "    return short_reason(seg, cap)\n")
      == _between(_src_a1611, "def _request_error_detail(text, cap):",
                  "    return short_reason(seg, cap)\n"))
check("22h== 两份镜像在体检报告那一档（cap=70）逐字同答",
      all(_a16_shape(_as_err(_c22, _t22), cap=70) == _shape(_as_err(_c22, _t22), cap=70)
          for _k22, _c22, _t22, _p22 in _ERR_SAMPLES),
      "1688 与其余站在同一句异常上读出两种短版")
check("22h=== cdp_base 只做转出口，不许再留第二份实现（两处把手＝下一轮分叉的起跑线）",
      "from text_shape import" in _src_cdp11
      and "def short_reason(" not in _src_cdp11 and "def first_line(" not in _src_cdp11
      and cdp_base.short_reason is text_shape.short_reason
      and cdp_base.first_line is text_shape.first_line)

# 22j ⑳ 那把深链短版把手现在只做「剥重复尾巴」，截断本身交给同一个收口
_fn22 = next(n for n in _ast.walk(_ast.parse(_src_cdp11))
             if isinstance(n, _ast.FunctionDef) and n.name == "_why_short")
_why_seg22 = _ast.get_source_segment(_src_cdp11, _fn22) or ""
_own_cut22 = [n.lineno for n in _ast.walk(_fn22)
              if isinstance(n, _ast.Subscript) and isinstance(n.slice, _ast.Slice)
              and getattr(n.slice.upper, "id", "") == "limit"]
check("22j 深链短版不再自己截字（截断这件事全仓只留在 short_reason 一处），剥尾巴照旧",
      not _own_cut22 and "short_reason(" in _why_seg22 and "仅这一条深链" in _why_seg22,
      f"自己截字的行：{_own_cut22}")
check("22j- 实机那类长句走剥尾巴＋收口之后，「通道入口未」这种半截说法不再出现",
      "通道入口未…" not in CdpSiteCrawler._why_short(_long := (
          "HTTP 200（正文是 200·人机验证壳（站方 WAF 质询，页里只有「正在检测」）"
          "·仅这一条深链，通道入口未受影响）·后面还有第二种死因要说"), 48)
      and not CdpSiteCrawler._why_short(_long, 48).endswith("通道入口未"),
      CdpSiteCrawler._why_short(_long, 48))
print("㉒ 原因收口只有一个把手：断在边界上、前缀整段留住、括号不成对不许上日志 ✓")

# ════════════════════════════════════════════════════════════════════════
# ㉓ 「按字符数切一句话」这一族在交付链里有 41 处：一次修全，并把形状钉死
# ════════════════════════════════════════════════════════════════════════
# 由来（㉒ 之后自审，2026-11-16）：㉒ 把「已经分过型的原因」收进把手，可当时那条判据（11p）
# 扫的是 `*_crawler.py`，而同一形状 `str(e).splitlines()[0][:120]` 还留在 cdp_base /
# b2bcollect_gui / parallel_session / site_adapters / access_guard / channel_health /
# local_scan / oss_pool_client 里，一共 41 处各写各的。**判据的视野比缺陷小，就等于没修**：
# 用户最先读到的半截词，恰恰出在 GUI 告警与会话日志这些路上。
# 现在真源是 b2bcollect_build/text_shape.py（零依赖，所以最先加载的权限自检也接得到），
# 1688 独立包内联同一段，另两个接不到把手的位置走「整行带出」。本节钉六件事：
#   23a 把手零依赖（不许拉 requests/selenium/cdp_base，否则 access_guard 起不来）；
#   23b 旧形状在三个交付目录的运行时代码里归零（与硬门 30a 同视野同形状，两份判据互咬）；
#   23c first_line 只做「先取首行、再按边界收」，空文本回空串（调用方那档 `or 类名` 照旧）；
#   23d 夹在两个词字符中间的分隔符**不算边界**（本轮性质判据在真源自己身上抓出的三条）；
#   23e OSS 的 XML 错误体改成「取值」而不是截断（旧写法只留下半截标签）；
#   23f 已经收口的一句话不许再被 `[:N]` 补一刀（硬门 30a-2 的形状，chrome_driver 那处就是漏网）。
import oss_pool_client as _opc
_ts = text_shape

# 全仓「把一句话收成一行」的把手名：调完它们又补 `[:N]` 等于把选好的边界重新劈开（23f）。
_ts_HANDLES = {"short_reason", "first_line", "_oss_brief", "_short_err", "_why_short",
               "_request_error_detail", "request_error_shape", "probe_block_reason",
               "http_block_reason"}


def _top_import_names(src):
    tree = _ast.parse(src)
    out = set()
    for node in tree.body:
        if isinstance(node, _ast.Import):
            out |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, _ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module.split(".")[0])
    return out


check("23a 把手只依赖标准库 re（access_guard 要在采集器全坏时还能说话，不能为了缩一句话拉起整套浏览器栈）",
      _top_import_names(_HANDLE_SRC) <= {"re"}, str(sorted(_top_import_names(_HANDLE_SRC))))
check("23a- 反向对照：把手里多 import 一个 requests 必须响（不响就是这条判据只看得到自己想到的）",
      _top_import_names("import re\nimport requests\n") > {"re"})

_BUILD_DIRS = (_REPO / "b2bcollect_build", _REPO / "ali1688_build", _REPO / "riskbird_build")


def _reason_sources():
    out = {}
    for d in _BUILD_DIRS:
        for p in sorted(d.glob("*.py")):
            if p.name.startswith(("test_", "debug_", "probe_")):
                continue
            out[p] = p.read_text(encoding="utf-8", errors="replace")
    return out


def _zero_index(node):
    """这个 Subscript 是不是 `[0]`：Python 3.9 起解析树里不再有 `ast.Index` 那层壳。
    （判据只认旧壳的话，在 3.13 上一条也命中不了——23b- 那条反向对照就是为了拦住这种空壳。）"""
    if not isinstance(node, _ast.Subscript):
        return False
    sl = node.slice
    if isinstance(sl, _ast.Index):
        sl = sl.value
    return isinstance(sl, _ast.Constant) and sl.value == 0


def _shape_hits(src, rel="", exempt=None):
    """四种形状：`X.splitlines()[0][:N]`、`把手(...)[:N]`、`str(异常)[:N]`、
    读一本收过口的原因账又切一刀（含隔着循环变量那一跳）。

    为什么这里要自己实现一份而不是只调门禁：第 30 条在本仓已经 twice 拓过「只有一份实现」的
    教训（11p 只扫 *_crawler.py、判据只认旧解析树壳）。两份独立实现对着同一份代码互咬，
    才能拓住「门禁自己把眼睛改窄」这一种进不去的坏法；豁免表则**只取门禁那一份**（下面
    23b== 钉两处读到的必须同一张），否则豁免会在一个地方慢慢长开。"""
    exempt = exempt if exempt is not None else _ts_EXEMPT
    out = []
    tree = _ast.parse(src)

    def _slices(nodes):
        for node in nodes:
            if not (isinstance(node, _ast.Subscript) and isinstance(node.slice, _ast.Slice)
                    and node.slice.lower is None
                    and isinstance(node.slice.upper, _ast.Constant)
                    and isinstance(node.slice.upper.value, int)):
                continue
            yield node

    # 前三形不需要同名字表，所以走全树；只有「读原因账」那一形必须按作用域算
    # 上一版本这里错用了 `_scope_nodes(tree)`（它不下钻嵌套函数）——函数体里写的
    # `str(e)[:60]` 全部看不见，整条判在空跑；23g- 就是拦住这一种错用
    for node in _slices(_ast.walk(tree)):
        base = node.value
        if (_zero_index(base) and isinstance(base.value, _ast.Call)
                and isinstance(base.value.func, _ast.Attribute)
                and base.value.func.attr == "splitlines"):
            out.append(node.lineno)
        elif isinstance(base, _ast.Call):
            fn = base.func.id if isinstance(base.func, _ast.Name) else getattr(base.func, "attr", "")
            if fn in _ts_HANDLES:
                out.append(node.lineno)
    for node in _slices(_ast.walk(tree)):
        base = node.value
        if (isinstance(base, _ast.Call) and isinstance(base.func, _ast.Name)
                and base.func.id == "str" and len(base.args) == 1
                and isinstance(base.args[0], _ast.Name)
                and re.fullmatch(r"(?:e|err|exc|ex|error|exception)\d*", base.args[0].id)):
            out.append(node.lineno)
    scopes = [tree] + [n for n in _ast.walk(tree)
                       if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.Lambda))]
    taint = _ts_param_taint(tree)
    lines = src.split("\n")
    for sc in scopes:
        names = _reason_names_of(sc, taint.get(getattr(sc, "name", "")))
        for node in _slices(_scope_nodes(sc)):
            bad = set()
            for sub in _ast.walk(node.value):
                if (isinstance(sub, _ast.Constant) and isinstance(sub.value, str)
                        and sub.value in _ts_LEDGER_KEYS):
                    bad.add(sub.value)
                elif isinstance(sub, _ast.Attribute) and sub.attr in _ts_LEDGER_ATTRS:
                    bad.add(sub.attr)
                elif isinstance(sub, _ast.Name) and sub.id in names:
                    bad.add(sub.id)
            if not bad:
                continue
            if (rel, lines[node.lineno - 1].strip()) in exempt:
                continue
            out.append(node.lineno)
    return sorted(set(out))


_ts_LEDGER_KEYS = frozenset({
    "last_page_error", "last_goto_error", "block_reason", "dead_hosts", "deep_block",
    "landing", "page_error", "nav_error", "load_error", "reason", "_ai_reason",
    "error", "errs", "read_err", "imp_err", "last_err"})
_ts_LEDGER_ATTRS = frozenset({"_last_page_error", "_last_goto_error", "_block_reason",
                             "_page_error"})


def _scope_nodes(scope):
    """本作用域的节点，不下钻嵌套函数/lambda（名字表必须按作用域算，理由见 23i）。"""
    out, stack = [], [scope]
    while stack:
        n = stack.pop()
        out.append(n)
        if (isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.Lambda))
                and n is not scope):
            continue
        stack.extend(_ast.iter_child_nodes(n))
    return out


def _reason_names_of(scope, seed=None):
    names = set(seed or ())
    binders = [n for n in _scope_nodes(scope)
               if isinstance(n, (_ast.Assign, _ast.For, _ast.comprehension))]
    for _ in range(8):
        grew = False
        for node in binders:
            if isinstance(node, _ast.Assign):
                src, dsts = node.value, list(node.targets)
            else:
                src, dsts = node.iter, [node.target]
            if not any((isinstance(s, _ast.Constant) and isinstance(s.value, str)
                        and s.value in _ts_LEDGER_KEYS)
                       or (isinstance(s, _ast.Attribute) and s.attr in _ts_LEDGER_ATTRS)
                       or (isinstance(s, _ast.Name) and s.id in names)
                       for s in _ast.walk(src)):
                continue
            for d in dsts:
                if d is None:
                    continue
                for x in _ast.walk(d):
                    if isinstance(x, _ast.Name) and x.id not in names:
                        names.add(x.id)
                        grew = True
        if not grew:
            break
    return names


def _ts_param_taint(tree):
    """把原因色跨过函数边界递一次（与硬门 `_reason_param_taint` 同一件事的另一份实现）。
    不递这一跳就判不出「把渲染处退回旧写法」：账在调用点、刀在被调函数里（本轮立项那一行）。"""
    fns = {}
    for n in _ast.walk(tree):
        if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            fns.setdefault(n.name, n)
    taint = {name: set() for name in fns}
    for _ in range(8):
        grew = False
        for caller in fns.values():
            names = _reason_names_of(caller, taint.get(caller.name))
            for node in _scope_nodes(caller):
                if not (isinstance(node, _ast.Call) and isinstance(node.func, _ast.Name)
                        and node.func.id in fns):
                    continue
                callee = fns[node.func.id]
                aarg = [a.arg for a in callee.args.posonlyargs + callee.args.args
                        if a.arg != "self"]
                for i, a in enumerate([x for x in node.args
                                       if not isinstance(x, _ast.Starred)]):
                    if i < len(aarg) and _reads25(a, names) and aarg[i] not in taint[callee.name]:
                        taint[callee.name].add(aarg[i])
                        grew = True
                for kw in node.keywords:
                    if kw.arg is None or not _reads25(kw.value, names):
                        continue
                    for i, an in enumerate(aarg):
                        if an == kw.arg and an not in taint[callee.name]:
                            taint[callee.name].add(an)
                            grew = True
        if not grew:
            break
    return taint


def _reads25(node, names):
    for sub in _ast.walk(node):
        if (isinstance(sub, _ast.Constant) and isinstance(sub.value, str)
                and sub.value in _ts_LEDGER_KEYS):
            return True
        if isinstance(sub, _ast.Attribute) and sub.attr in _ts_LEDGER_ATTRS:
            return True
        if isinstance(sub, _ast.Name) and sub.id in names:
            return True
    return False


# 豁免表只取门禁那一份：两份判据共用一张表，才是「互咬」而不是「各说各话」。
_ts_EXEMPT = frozenset(_gate31.REASON_SLICE_EXEMPT) if _gate31 is not None else frozenset()


_rs = _reason_sources()
_bad23 = {p.relative_to(_REPO).as_posix(): _shape_hits(t, p.relative_to(_REPO).as_posix())
          for p, t in _rs.items()
          if _shape_hits(t, p.relative_to(_REPO).as_posix())}
check(f"23b 三个交付目录 {len(_rs)} 份运行时代码里，四种「按字符数切一句话」的形状都归零"
      "（视野与硬门 30a/30a-2/30a-3/30a-4 同口径）",
      not _bad23, str(sorted(_bad23.items())))
check("23b- 反向对照：这一族历史上写过的四种形状（异常取首行又切 [:120]／收口结果 [:40]／"
      "整行把手 [:90]／裸 str(异常) [:60]）都得被点出来",
      bool(_shape_hits('x = str(e).splitlines()[0][:120]'))
      and bool(_shape_hits('z = short_reason(err, 70)[:40]'))
      and bool(_shape_hits('w = f"{_short_err(e)[:90]}"'))
      and bool(_shape_hits('s = f"展开时出错：{str(exc)[:60]}"')),
      "抓不到旧写法＝下一轮同类写法又会混进来")
check("23b== 走把手的写法与公司名/URL 那种「本来就该限宽」的切片都不许误响",
      not _shape_hits('log(short_reason(why, 70))')
      and not _shape_hits('name = rec["公司名称"][:24]')
      and not _shape_hits('u = url[:80]'))

_LONG_ERR = ("ConnectionError: HTTPSConnectionPool(host='b2b.11467.com', port=443): "
             "Max retries exceeded with url: /search/%E5%85%89%E4%BC%8F.html "
             "(Caused by ConnectTimeoutError(<urllib3.connection.HTTPSConnection object at "
             "0x7f9e>, 'Connection to b2b.11467.com timed out. (connect timeout=10)'))\n"
             "Traceback (most recent call last):\n  File \"crawl.py\", line 88, in run")
_f23 = _ts.first_line(_LONG_ERR, 120)
check("23c first_line 只留异常第一行（堆栈不进日志一行里），而且这一行仍按边界收口",
      "Traceback" not in _f23 and not _f23.endswith("_") and _f23.endswith("…")
      and "ConnectionError" in _f23, _f23[-40:])
check("23c- 空异常文本回空串：调用方近旁那档 `or type(e).__name__` 兜底照旧成立",
      _ts.first_line("") == "" and _ts.first_line(None) == ""
      and _ts.first_line("   \n  ") == "")
check("23c== 短得不用再收的句子一个字都不改（收口不是改写）",
      _ts.first_line("ValueError: bad html", 120) == "ValueError: bad html")

check("23d 夹在两个词字符中间的分隔符不算边界：b2b.11467 / com:443 / 客户库.xls / 哈利·波特 "
      "这四处断刀都是劈词（本轮性质判据在真源自己身上抓出来的正是前三条）",
      not _ts._is_break("b2b.11467", 4) and not _ts._is_break("11467.com:443", 10)
      and not _ts._is_break("读不了：客户库.xlsx", 8) and not _ts._is_break("哈利·波特", 3))
check("23d- 句子结尾的标点、括号后面的间隔号仍然是边界（否则整句一个边界都没有，"
      "收口只能整句带省略号，等于没缩）",
      _ts._is_break("timed out. 下一句", 10) and _ts._is_break("冷却）·仅这一条", 4)
      and _ts._is_break("a，b", 2) and _ts._is_break("x｜y", 2))
_R23_HOST = ("【连不上·无应答】b2b.11467.com:443｜Connection to b2b.11467.com timed out. "
             "(connect timeout=10)")
_half23 = [(n, _ts.short_reason(_R23_HOST, n)) for n in (30, 40, 60, 70, 120)
           if re.search(r"(b2b\.|\.com:|\.cn:|客户库\.)…$", _ts.short_reason(_R23_HOST, n))]
check("23d== 实机那一句在五档上限下都不长出 `b2b.`／`com:` 这种半截主机（要么整段在、"
      "要么断在真边界后）", not _half23, str(_half23))

_XML = ('<?xml version="1.0" encoding="UTF-8"?>\n<Error><Code>AccessDenied</Code>'
        '<Message>You have no right to access this object because of bucket acl.'
        ' 这条资源的 ACL 不给这个 RAM 角色读，请改桶策略或者换一个可写的前缀再来一次。</Message>'
        '<RequestId>652F0A9B1A2C3D4E5</RequestId><HostId>b2b-collect.oss-cn-shenzhen.aliyuncs.com</HostId>'
        '<EC>0003-00000001</EC></Error>')
_b23 = _opc._oss_brief(_XML)
check("23e OSS 的 XML 错误体改成「取值」：Code/Message/RequestId/EC 四段都在，标签一个不留",
      _b23.startswith("Code=AccessDenied") and "bucket acl" in _b23 and "652F0A9B1A2C3D4E5" in _b23
      and "0003-00000001" in _b23 and "<" not in _b23, _b23)
_old200 = _XML[:200]
check("23e- 反向对照：旧写法 `resp.text[:200]` 在同一句上①念不出结论 ②把处置要引用的 "
      "RequestId/EC 两格整格丢掉 ③留下一堆没合口的标签（这就是这一档立项的样子）",
      "Code=AccessDenied" not in _old200 and "652F0A9B1A2C3D4E5" not in _old200
      and "0003-00000001" not in _old200 and _old200.count("<") > _old200.count(">")
      and _old200 != _XML, _old200[-30:])
check("23e== 不是 XML（网关 HTML 错误页／空体）时退回同一个把手，空体说「空响应体」",
      _opc._oss_brief("<html>502 Bad Gateway</html>").startswith("<html>")
      and _opc._oss_brief("") == "（空响应体）"
      and _opc._oss_brief("plain text") == "plain text"
      and _opc._oss_brief("x" * 400).endswith("…"), _opc._oss_brief("x" * 400)[-14:])

_cd_src = (_REPO / "riskbird_build" / "chrome_driver.py").read_text(encoding="utf-8")
_f_short = next(n for n in _ast.walk(_ast.parse(_cd_src))
               if isinstance(n, _ast.FunctionDef) and n.name == "_short_err")
_short_seg = _ast.get_source_segment(_cd_src, _f_short) or ""
check("23f 第三支口径（接不到把手的独立包）＝整行带出：_short_err 只取首行、不按字符数切",
      "splitlines()[0]" in _short_seg and not re.search(r"splitlines\(\)\[0\]\[:\d", _short_seg),
      _short_seg)
check("23f- 它的四个调用点也不许再补一刀（历史漏网就是 `{_short_err(e)[:90]}`：看着像走了口径，"
      "实际又把边界劈开）",
      not re.search(r"_short_err\([^)]*\)\[:\d", _cd_src),
      "仍有 `把手(...)[:N]`")
check("23f== 全仓「一句话收口」的实现只有两份：text_shape 与 1688 内联那份（第三支不产生新实现）",
      sum("def short_reason(" in t for t in _rs.values()) == 2
      and sum("def first_line(" in t for t in _rs.values()) == 2,
      "又长出第三份断句实现＝下一轮分叉的起跑线")
# ── 23g 同一族的第三种写法：`str(异常)[:N]`（本轮同类查全拓出来的最大一族：28 处）。
#    为什么 23b 一开始看不见它：那条判据钉的是 `splitlines()[0][:N]` 这个形状，而
#    `local_scan.py`/`access_guard.py`/riskbird 那几份写的是 `f"…失败：{str(e)[:60]}"`，
#    没有「先取首行」那一跳。症状完全相同（异常里最长的恰恰是尾巴上的 Session info/路径），
#    但形状不同就一路混过。口径按位置分两档：能接到把手的 b2bcollect 走 `first_line`，
#    另两个独立包与三目录同名拷贝就整行带出（后者只能整行，因为抄一份断句进去就又长出第三份实现）。
_TS_EXC_SHAPE = re.compile(r"str\((?:e|err|exc|ex|error|exception)\d*\)\[:\d")


def _code_only(text):
    """剔掉「不会执行到的一句话」之后的代码：整行注释与文档字符串（函数/模块级那句裸文本）。
    这一族的注释与说明里本来就写着旧写法作为对比（cdp_base 那段「怎么读日志」的文档里就有一句
    `except: return str(e)[:60]`），当代码扫会一直响。但不能反过来把句子都剔了：
    带尾注释的真语句、以及 f-string 内联的写法都饶不了（见 23g==）。
    只按 AST 剔「表达式语句就是一个字面串」那一段，不拿正则猜，也不依赖某个 Python 版本的
    f-string 分词（旧版本里整条 f-string 是一个 STRING token，抹字符串就连同真代码一起抹了）。"""
    lines = text.splitlines()
    for n in _ast.walk(_ast.parse(text)):
        if (isinstance(n, _ast.Expr) and isinstance(n.value, _ast.Constant)
                and isinstance(n.value.value, str)):
            for i in range(n.lineno, (n.end_lineno or n.lineno) + 1):
                if 1 <= i <= len(lines):
                    lines[i - 1] = ""
    return "\n".join(ln for ln in lines if not ln.lstrip().startswith("#"))


check("23g 真树上 `str(异常)[:N]` 这一形归零：三个交付目录的运行时代码里一处也不留",
      not any(_TS_EXC_SHAPE.search(_code_only(t)) for t in _rs.values()),
      sorted(p.name for p, t in _rs.items() if _TS_EXC_SHAPE.search(_code_only(t))))
check("23g== 只剔「不会执行的句子」：带尾注释的真语句、f-string 内联、以及写在字符串表达式里"
      "的拼接都饶不了（不然下一个人只要把旧写法往注释里一写，这一判就集体失灵）",
      bool(_TS_EXC_SHAPE.search(_code_only('x = f"败：{str(e)[:60]}"  # 旧写法')))
      and not _code_only('# 这里写的是 str(e)[:60]\nx = 1\n').count("str(e)")
      and not _code_only('def f():\n    """旧写法是 str(e)[:60]"""\n    return 1\n').count("str(e)")
      and bool(_TS_EXC_SHAPE.search(_code_only('y = str(err1)[:80]')))
      and bool(_TS_EXC_SHAPE.search(_code_only('def f(e):\n    raise RuntimeError("展平出错：%s" % str(e)[:60])\n'))))
check("23g- 反向对照：这一形喂进去必须响（上一轮的判据正是只认 splitlines 那一跳，所以一路空跑）",
      len(_shape_hits('def f(e):\n    return f"展开时出错：{str(e)[:80]}"\n')) == 1
      and len(_shape_hits('def f(err1):\n    return str(err1)[:60]\n')) == 1)
check("23g== 走 first_line 的写法、以及对**非异常变量**的 str(...)[:N] 都不许误响"
      "（误响就会把界面那些「公司名限宽」的写法一起拖进噪音）",
      not _shape_hits('def f(e):\n    return first_line(e, 80)\n')
      and not _shape_hits('def f(row):\n    return str(row)[:20]\n')
      and not _shape_hits('def f(x):\n    return str(x.get("地址"))[:30]\n'))
_ls23 = next(t for p, t in _rs.items() if p.name == "local_scan.py")
_qz23 = next(t for p, t in _rs.items() if p.name == "qizhidao_crawler.py")
check("23g=== 两档口径各自落地：b2bcollect 那几处真改成了 first_line，riskbird 那几处真整行带出"
      "（只查「形状归零」不够：两处都归零但其中一处是被删掉了调用，读报告的人就又看不见原因了）",
      _ls23.count("first_line(e, 80)") == 2
      and 'f"展开时出错：{first_line(e, 80)}"' in _ls23
      and _ls23.count("first_line(e, 60)") == 6
      and sum(t.count("first_line(e,") for t in _rs.values()) >= 12
      and "{str(e)" not in _qz23 and "{e}" in _qz23,
      f"local_scan={_ls23.count('first_line(e,')} qizhidao 仍带 str={{}}：{_qz23.count('{str(e)')}")

# ── 23h 同一族的第四种写法：读一本**已经收过口的原因账**又切一刀。这一形是本轮立项的那一行：
#    第十一轮实机报告里 `深链被拒: china.makepolo.com 42 次（…仅这一条深链，通道入）`。
#    记账处（cdp_base._note_deep_block）走的是 short_reason(reason, 120)，渲染处当年写的是
#    str(v[1])[:46]：两刀之间隔了一层字典与一层循环变量，三种已有形状都认不出来。
_EXEMPT_LINE = 'on_progress(f"正在识别 {it[\'display\'][:30]}…", len(items), len(files))'
_SRC23H = ('def f(st, files, items):\n'
           '    _deep = st.get("deep_block") or {}\n'
           '    for h, v in _deep.items():\n'
           '        yield f"{h} {v[0]} 次（{str(v[1])[:46]}）"\n')
check("23h 反向对照：实机那一行的原形（原因落进字典→循环变量接手→[:46]）必须被点出来",
      len(_shape_hits(_SRC23H)) == 1, _shape_hits(_SRC23H))
check("23h- 同一个位置改成走把手必须安静（不安静就是判据在挡所有切片，而不是只挡切原因）",
      not _shape_hits(_SRC23H.replace("str(v[1])[:46]", "short_reason(v[1], 46)")))
check("23h== 不隔那一跳的两种直读也得响：账键 s.get('last_page_error')[:40] 与账属性 "
      "self._block_reason[:40]（channel_health / cdp_base 本轮各修掉一处）",
      len(_shape_hits('def f(s):\n    return s.get("last_page_error")[:40]\n')) == 1
      and len(_shape_hits('def f(self):\n    return f"仍在等验证（{self._block_reason[:40]}）"\n')) == 1)
check("23h=== 数字账与名单账不许被误伤：同一本 stats 里 st.get('pages')[:3] / "
      "st.get('kw_filtered')[:2] 都不是「一句原因」，判据不能见到 stats 就响",
      not _shape_hits('def f(st):\n    return st.get("pages")[:3] + st.get("kw_filtered", {})[:2]\n'))
check("23h4 豁免表只钉原文：同一行把上限从 30 改成 31（换了衣服）必须又响，"
      "否则豁免表会长成谁也不敢删的抽屉",
      len(_shape_hits('def g(items, files):\n    x = items.get("reason")\n    for it in items["deep_block"]:\n'
                      f'        {_EXEMPT_LINE}\n', "b2bcollect_build/local_scan.py")) == 0
      and len(_shape_hits('def g(items, files):\n    x = items.get("reason")\n    for it in items["deep_block"]:\n'
                          f'        {_EXEMPT_LINE.replace("[:30]", "[:31]")}\n',
                          "b2bcollect_build/local_scan.py")) == 1)
_bad23_nox = {p.name: _shape_hits(t, p.relative_to(_REPO).as_posix(), exempt=frozenset())
              for p, t in _rs.items()}
check("23h5 今天那两处豁免都是「真的在用」：把表抽掉必须恰好报出那两处（一处也没报＝这一判在空跑）",
      sorted(n for n, v in _bad23_nox.items() if v) == ["local_scan.py", "parallel_session.py"],
      str({n: v for n, v in _bad23_nox.items() if v}))

# ── 23i 名字传播必须按**函数作用域**算。本轮第一版实现把整份文件共用一张名字表，
#    实跑报出 127 处、其中真缺陷只 7 处：`r`/`e`/`rec` 这些短名在某个函数里确实接过原因，
#    就把原因色传染到全仓同名变量。误报到这个量级，下一条门就会被人整个关掉。
_SRC23I = ('def a(st):\n'
           '    r = st.get("reason")\n'
           '    return r[:20]\n'           # 真缺陷：切的确实是原因
           'def b(rows):\n'
           '    for r in rows:\n'
           '        yield r["display"][:20]\n')   # 同名但与本档无关
check("23i 同一个短名在两个函数里：只有真接过原因账那一个要响（作用域不传染）",
      len(_shape_hits(_SRC23I)) == 1, _shape_hits(_SRC23I))

# ── 23j 行为对拍（不只是形状）：探针把这两行收成了 `_host_reason_line`，夹具直接喂一句
#    真死因看它断在哪里。旧写法在这一句上切出的是「通道入口未受」——那半截词看着像能说清。
_WHY23J = ("【被拦停】站点返 200 但整页正文是人机验证壳（WAF 质询）·仅这一条深链，"
           "通道入口未受影响")
_line23j = _live._host_reason_line("深链被拒", {"china.makepolo.com": [42, _WHY23J]})
check("23j 探针那一行现在把 46 个字的上限断在真标点上：不出现「通道入」这种半截词、括号成对、以省略号收尾",
      "通道入" not in _line23j and _line23j.endswith("仅这一条深链…）")
      and _line23j.count("（") == _line23j.count("）")
      and "42 次（" in _line23j, _line23j)
check("23j- 反向对照：旧写法 str(原因)[:46] 在同一句上真的长出半截词（这就是立项那一行）",
      _WHY23J[:46].endswith("未受") and "通道入口未受" in _WHY23J[:46], _WHY23J[:46])
check("23j== 两处渲染（落点不符 / 深链被拒）共用同一支把手，不各写一遍"
      "（只问会执行的代码：把手那段说明里本来就写着旧写法 `str(v[1])[:46]` 作为反面样本）",
      _LIVE_SRC.count("_host_reason_line(") == 3
      and 'f"{h} {v[0]} 次（{short_reason(v[1], limit)}）"' in _LIVE_SRC
      and not re.search(r"str\(v\[1\]\)\[:\d", _code_only(_LIVE_SRC)),
      _LIVE_SRC.count("_host_reason_line("))

# ── 23k 两份判据互咬：离线自己那份与构建前那条硬门，必须在同一份代码上给同一个答案，
#    并且视野里必须包含实机报告的渲染层（那行半截词就是从那里念出来的）。
check("23k 硬门本尊今天对真实树报 0 条（与 23b 同一答案；两份判据互咬，不是一个抄一个）",
      _gate31 is None or _gate31.check_reason_one_handle() == [],
      str((_gate31.check_reason_one_handle() if _gate31 else _gate31_err)[:1]))
_g23rs = _gate31._runtime_reason_sources() if _gate31 is not None else {}
check("23k- 硬门视野包含 debug/platform_live_check.py：按目录名把它排除掉，"
      "就等于「报告那一行永远没人判」——上一轮正是这样过的绿",
      any(str(k).endswith("debug/platform_live_check.py") for k in _g23rs),
      len(_g23rs))
check("23k== 离线这一份的视野不比硬门窄：两份读到的文件集要一致（窄一份就是那一族没人判）",
      _gate31 is None or {p.name for p in _rs} <= {Path(k).name for k in _g23rs},
      sorted({p.name for p in _rs} - {Path(k).name for k in _g23rs}))

# ── 23l 隔着函数那一跳（本轮立项那一行的完整形状）：账在**调用点**、刀在**被调函数**里。
#    只按单个作用域算名字时，被调那个函数的形参永远接不到原因色——于是把渲染处退回
#    `str(v[1])[:46]` 也不会响。「修好了但判据看不见回归」与「没修」对下一个人是同一件事。
_SRC23L = ('def _host_line(title, mapping, limit=46):\n'
           '    return f"  {title}: " + "｜".join(f"{h} {v[0]} 次（{str(v[1])[:46]}）"\n'
           '                                   for h, v in (mapping or {}).items())\n'
           'def fmt(st):\n'
           '    _deep = st.get("deep_block") or {}\n'
           '    return _host_line("深链被拒", _deep)\n')
check("23l 跨函数那一跳要接到原因色：账在调用点、刀在被调函数里，退回旧写法要响",
      len(_shape_hits(_SRC23L)) == 1, _shape_hits(_SRC23L))
check("23l- 同一份代码改成走把手不许响（传染只认「这是一句原因」，不是「这是个参数」）",
      not _shape_hits(_SRC23L.replace("str(v[1])[:46]", "short_reason(v[1], limit)")))
check("23l== 调用点交的是数字账时不许传染（把 mapping 认成原因＝见参数就响的开始）",
      not _shape_hits(_SRC23L.replace('st.get("deep_block")', 'st.get("pages")')))
check("23l=== 两份实现对同一份跨函数代码同答案（一份响一份不响＝互咬没咬上）",
      _gate31 is None or (
          len([p for p in _gate31.check_reason_one_handle(
              files={_REPO / "b2bcollect_build/zz_23l.py": _SRC23L})
               if "按字符数切了一刀" in p]) == 1
          and not [p for p in _gate31.check_reason_one_handle(
              files={_REPO / "b2bcollect_build/zz_23l.py":
                     _SRC23L.replace("str(v[1])[:46]", "short_reason(v[1], limit)")})
               if "按字符数切了一刀" in p]))
print("㉓ 半截词这一族：41 处独立写法收进一个把手，形状与性质两边都有人钉 ✓")

# ══════════════════════════════════════════════════════════
# ㉔ 导航层的事实必须留痕：「这一跳根本没发出去」不许被说成「站点在拦」，
#    更不许被说成「挑战跑完了」
# ══════════════════════════════════════════════════════════
# 由来（2026-11-14~16 实机马可波罗）：借标签那条腿连着三轮 0/3，日志说的是「这条腿帮不上忙」，
# 听起来像站点不给看。本机差分实测把方向整个翻了过来：同一台机器起两个 headless Chrome，
# 只差 `--disable-features=HttpsUpgrades,…` 这一个开关——A 组连 example.com 的明文 http 都被
# `net::ERR_BLOCKED_BY_CLIENT` 拦掉，B 组全部正常（马可波罗的深链本来就是 http，它的 https
# 证书连自己域名都不覆盖）。根因在浏览器参数那一侧，而 `Page.navigate` **不抛异常**：它把
# Chrome 的原话放在响应的 errorText 里，页面落在 `chrome-error://chromewebdata/`。旧写法把
# 整份响应丢掉，于是那一格唯一的留痕没了，下游只能猜——猜错的代价是把预算喂给一条不走的路。
# 这一节钉四件事：① errorText 必须被接住并按类别记账；②「没加载出来」那句必须念 Chrome 原话，
# 且**方向只由分型器给**（分不上型就说还不知道）；③ 结论句不许把推测当观察（「挑战跑完了」）；
# ④ 上面三条的判据本身要被反证一次。
CHROME_ERR_URL = "chrome-error://chromewebdata/"
_WANT24 = "http://a.cn.makepolo.com/contact_us.html"


def _mk24():
    """每一档都要一枚干净的爬虫：`_js_tab_tries` 的上限是 3，共用就会把后几档判成「预算用满」。"""
    c = _mp18.MakepoloCrawler()
    c.reset_run_state()
    _e, lg = equip(c)
    c._pace_before_request = lambda *a, **k: None
    c.open_verify_page = lambda url="": True
    c._release_verify_tab = lambda: None
    c._harvest_browser_cookies = lambda: 0
    return c, lg


def _nav24(c, err_text, host="a.cn.makepolo.com"):
    """把「Page.navigate 回了 errorText」这件事按真路径演一遍（不直接写那一格，写就验不到）。"""
    c._cur_fetch_host = host
    c._send = lambda method, params=None, timeout=None: {"result": {"errorText": err_text}}
    return c._cdp_navigate(_WANT24)


# 24a errorText 必须被接住，而且只有「连接阶段」那几档才进熔断账
_c24 = _mk24()[0]
check("24a Page.navigate 被拦时不抛异常：errorText 被接进 _last_nav_error（这件事唯一的留痕）",
      _nav24(_c24, "net::ERR_BLOCKED_BY_CLIENT")
      and _c24._last_nav_error == "net::ERR_BLOCKED_BY_CLIENT", _c24._last_nav_error)
check("24a- 「浏览器拦下这一跳」不进主机熔断账：那是环境/参数问题，拉黑这台等于自己掐活路",
      not _c24._dead_strikes, str(_c24._dead_strikes))
_c24 = _mk24()[0]
_nav24(_c24, "net::ERR_TIMED_OUT")
check("24b 连不上那一档（net::ERR_TIMED_OUT）照旧进熔断账：真连不上时再开几次标签也白开",
      _c24._last_nav_error == "net::ERR_TIMED_OUT"
      and _c24._dead_strikes.get("a.cn.makepolo.com") == 1, str(_c24._dead_strikes))
_c24 = _mk24()[0]
_nav24(_c24, "net::ERR_BLOCKED_BY_CLIENT")
_c24._send = lambda method, params=None, timeout=None: {"result": {}}
_c24._cdp_navigate(_WANT24)
check("24b- 反向对照：本轮导航干净时那一格必须被清空（留着上一家的原因，界面就会张冠李戴——"
      "类别看着很像，方向却是错的）", _c24._last_nav_error == "", _c24._last_nav_error)

# 24c 那句「没加载出来」：只陈述事实，方向交给类别词
for _lbl24, _err24, _exp24 in [
        ("浏览器把明文 http 升级失败后拦掉", "net::ERR_BLOCKED_BY_CLIENT", "浏览器拦下这一跳"),
        ("证书 CN 不覆盖域名", "net::ERR_CERT_COMMON_NAME_INVALID", "TLS握手失败"),
        ("站点在这条深链上直接回错误码", "net::ERR_HTTP_RESPONSE_CODE_FAILURE", "站点回了错误状态码"),
        ("连接阶段没应答", "net::ERR_TIMED_OUT", "连不上·无应答")]:
    _line24 = cdp_base.tab_load_failed_line(CHROME_ERR_URL, _err24)
    check(f"24c {_lbl24}：Chrome 的原话被念出来，类别在前（{_exp24}）→ {_line24[:56]}",
          "没加载出来" in _line24 and _exp24 in _line24, _line24)
check("24c- 拿不到错误码时只说「页里没写着错误码」，绝不挑一个方向下结论",
      cdp_base.tab_load_failed_line(CHROME_ERR_URL, "") .count("【") == 0
      and "没写着错误码" in cdp_base.tab_load_failed_line(CHROME_ERR_URL, ""),
      cdp_base.tab_load_failed_line(CHROME_ERR_URL, ""))
check("24c-2 errorText 空着才退回错误页正文里那个码（复用旧标签/站方自己导航时正是这一档）",
      "DNS解析不了" in cdp_base.tab_load_failed_line(
          CHROME_ERR_URL, "", "This page isn’t valid. net::ERR_NAME_NOT_RESOLVED"),
      cdp_base.tab_load_failed_line(CHROME_ERR_URL, "", "x net::ERR_NAME_NOT_RESOLVED y"))
check("24c-3 反向对照：errorText 与正文里的码不一致时**只信本轮这次导航的**（上一跳留下的旧码"
      "会把方向说反），且这一次是浏览器拦而不是连不上",
      "浏览器拦下这一跳" in cdp_base.tab_load_failed_line(
          CHROME_ERR_URL, "net::ERR_BLOCKED_BY_CLIENT", "net::ERR_TIMED_OUT")
      and "连不上" not in cdp_base.tab_load_failed_line(
          CHROME_ERR_URL, "net::ERR_BLOCKED_BY_CLIENT", "net::ERR_TIMED_OUT"),
      cdp_base.tab_load_failed_line(CHROME_ERR_URL, "net::ERR_BLOCKED_BY_CLIENT",
                                    "net::ERR_TIMED_OUT"))
check("24c== 反向对照：正常页面上这一句必须完全闭嘴（把每一页都说成没加载出来＝新话术变成噪音）",
      cdp_base.tab_load_failed_line(_WANT24, "", "net::ERR_TIMED_OUT in the text") == "",
      cdp_base.tab_load_failed_line(_WANT24, "", "x"))

# 24d 身份门那一侧：落到 Chrome 错误页时不许再交出一句没有方向的「标签不在本站」
_c24d, _lg24d = _mk24()
_nav24(_c24d, "net::ERR_BLOCKED_BY_CLIENT")
_c24d._current_url = lambda: CHROME_ERR_URL
_c24d._html = lambda: "<html>net::ERR_BLOCKED_BY_CLIENT</html>"
_ok24d, _why24d = _c24d._tab_page_is(_WANT24)
check("24d 身份门拒收这一页，并把 Chrome 原话一起交出去（上游拿到的不再是看不出方向的那句）",
      _ok24d is False and "没加载出来" in _why24d and "浏览器拦下这一跳" in _why24d, _why24d)
_c24d._current_url = lambda: "http://a.cn.makepolo.com/index.html"
_c24d._last_nav_error = "net::ERR_BLOCKED_BY_CLIENT"
_ok24d2, _why24d2 = _c24d._tab_page_is(_WANT24)
check("24d- 反向对照：地址落在别家/首页那一档才是「串页」，**不许**被这轮的错误码污染成"
      "「这一跳没发出去」（两种处方互斥）",
      _ok24d2 is False and "标签落的不是这一页" in _why24d2 and "没加载出来" not in _why24d2,
      _why24d2)

# 24e 结论句：借标签那条腿撞上 chrome-error 时，三种方向各说各的话，且谁都不许说「跑完了」
_NAV24_CASES = (
    ("浏览器把这一跳拦下了", "net::ERR_BLOCKED_BY_CLIENT", "浏览器拦下这一跳",
     "是浏览器没能把这一跳发出去"),
    ("站点回错误状态码", "net::ERR_HTTP_RESPONSE_CODE_FAILURE", "站点回了错误状态码",
     "站点在这条深链上直接回了错误码"),
    ("连接阶段没应答", "net::ERR_TIMED_OUT", "连不上·无应答", "这一台连不上"),
    ("认不出的错误码", "net::ERR_BRAND_NEW_THING", "未分型", "还没分上型"),
)
for _lbl24, _err24, _kind24, _tail24 in _NAV24_CASES:
    _c24e, _lg24e = _mk24()
    _nav24(_c24e, _err24)
    _c24e._current_url = lambda: CHROME_ERR_URL
    _c24e._html = lambda: "<html>whoops</html>"
    _got24e = _c24e._solve_js_shell_via_tab(_WANT24, allow_body=True)
    check(f"24e {_lbl24}：这条腿老实回 False，并把方向按类别说清（{_kind24}→{_tail24}）",
          _got24e is False and any(_tail24 in m for m in _lg24e)
          and any("没加载出来" in m and _kind24 in m for m in _lg24e),
          str(_lg24e[-2:]))
    check(f"24e- {_lbl24}：这一档一条通行证都没抄到、正文也没被交出（账上不许出现假命中）",
          _c24e._js_tab_hits == 0 and _c24e._js_tab_body_hits == 0
          and not _c24e._js_tab_body, str(_c24e._js_tab_body[:40]))
    check(f"24e== {_lbl24}：没有任何一句把这一跳说成「挑战跑完了」（推测当观察＝这一族的根因）",
          not any("跑完了" in m for m in _lg24e), str([m for m in _lg24e if "跑完" in m]))
_c24f, _lg24f = _mk24()
_c24f._html = lambda: _MP_REAL_HOME
_c24f._current_url = lambda: _WANT24
_c24f._harvest_browser_cookies = lambda: 3
check("24f 反向对照：标签里真是那一页时这条照旧出货，新加的判据不许把正路误伤",
      _c24f._solve_js_shell_via_tab(_WANT24) is True and _c24f._js_tab_hits == 1
      and not any("没加载出来" in m for m in _lg24f), str(_lg24f[-1:]))
_c24g, _lg24g = _mk24()
_c24g._html = lambda: _MP_WAF_SHELL
_c24g._current_url = lambda: _WANT24
check("24g 壳一直没退时说的还是「那页还是质询壳：挑战没跑完」（这一档本来就没被误伤，钉住它别改坏）",
      _c24g._solve_js_shell_via_tab(_WANT24) is False
      and any("还是质询壳" in m and "挑战没跑完" in m for m in _lg24g)
      and not any("没加载出来" in m for m in _lg24g), str(_lg24g[-1:]))

# 24h 机器判据：直接跑构建前那条硬门里「导航证据」的本尊，判据自己也过一遍反证
_gate31b, _g31err = _load_gate31()
check("24h 门禁脚本可加载（拿不到就等于离线这一侧对导航证据失明）", not _g31err, _g31err)
if _gate31b is not None:
    _src24 = _HTTP_SRC["cdp_base.py"]
    _t24 = _ast.parse(_src24)
    check("24h 真源上三处证据都在、结论句只陈述观察",
          not _gate31b._tab_nav_evidence_problems("cdp_base.py", _t24, strict_names=True),
          " ｜ ".join(x.splitlines()[0] for x in
                      _gate31b._tab_nav_evidence_problems("cdp_base.py", _t24,
                                                           strict_names=True)[:3]))
    _clone24 = {
        "把 errorText 那一行摘掉（回到本轮修复前的原样）":
            _src24.replace('err = str(((resp or {}).get("result") or {})'
                           '.get("errorText") or "").strip()',
                           'err = ""'),
        "把「这一跳加载出来没有」那一问摘掉":
            _src24.replace("                failed = self._tab_load_failed()\n"
                           "                if failed:\n                    break\n", ""),
        "把错误页判据改成就地重抄字面量":
            _src24.replace("if not any(m in cur for m in CHROME_ERROR_URL_MARKS):",
                           'if "chrome-error://" not in cur:'),
        "结论句退回「挑战跑完了」":
            _src24.replace('"   ⚠ 标签里那页已经不是质询壳了，这条腿却还是帮不上：既没抄到本站通行证"',
                           '"   ⚠ 挑战跑完了，这条腿还是帮不上：既没抄到本站通行证"'),
    }
    for _lbl24, _s24 in _clone24.items():
        _got24 = _gate31b._tab_nav_evidence_problems("克隆", _ast.parse(_s24), strict_names=True)
        check(f"24h- 反向对照（{_lbl24}）：这一族退回去时判据必须响",
              _s24 != _src24 and bool(_got24),
              "克隆没生效（替换的锚点对不上真源）" if _s24 == _src24 else str(_got24))
    # 24h-2 上面 24e 的行为对照要成立，那四句 tail 必须**各自独立**在源码里：两条路共用一句
    # 话术、或者某一句被复制了两遍，24e 的 `any(...)` 就会蒙过去。这一条钉的是判据的地基。
    _tails24 = [t for _l, _e, _k, t in _NAV24_CASES]
    _fn24 = next(n for n in _ast.walk(_t24) if isinstance(n, _ast.FunctionDef)
                 and n.name == "_solve_js_shell_via_tab")
    _flat24 = _ast.unparse(_fn24)
    check("24h-2 四种方向各有一句自己的话术（24e 的行为对照靠这一条才不是蒙对的）",
          all(_flat24.count(t) == 1 for t in _tails24) and len(set(_tails24)) == len(_tails24),
          {t: _flat24.count(t) for t in _tails24})
print("㉔ 导航层的事实必须留痕：「这一跳没发出去」不许被说成站点在拦，更不许被说成挑战跑完了 ✓")

# ══════════════════════════════════════════════════════════
# ㉕ 无联系方式责任分档 + 砍单记名 + 借标签归还：三本「只累加、从不外读」的账
# ══════════════════════════════════════════════════════════
# 由来（2026-11-16 第十轮实机，同一个下午现形的三件事）：
#   ① 世界工厂网「电梯导轨」推到 10 家、四项口径只剩 4 家，报告按旧口径写「大头连四项
#      联系方式都没有，先修取号通路再谈换词」。真翻页面 `联系电话` 那一栏写着 `-`（站方已把
#      号码改成「联系客服登记」才给）：通路没坏，这句谎却把人支使去改本来正确的解析代码。
#      → 「无联系方式」必须按**是谁的责任**分档（档位与定序只由 NC_KIND_ORDER 定义，
#        现共六档：miss/unknown/no_page/hidden/blank/no_slot）。
#   ② 三个 ❌ 全站都只说「该复核判定口径」，可复核的人手上没有名单——一句无法执行的处方。
#      → 砍单必须**记名**（每词每阶段留样本）。
#   ③ 六份纯 HTTP 站把 `close_browser` 覆写成空壳 `return None`，降级取页时借来的标签一路
#      留到收工之后（用户的共享 Chrome 里挂着一枚 maimaib2b 的商铺页没人认领）。
#      → 借来的标签必须还，而且「关失败」不许被 `except: pass` 抹平成「已经收工」。
# 本节先按**行为**对拍（判据喂真 HTML、把手走真路径、收尾走真 crawl），再由 25g 直接调用
# 构建前第 32 条硬门的本尊：现网必须全绿，而每一族退回修复前的样子都必须响。

# ── 25a 分档判据本体：只看「页面上那一栏自己怎么写」，不看任何站点的解析结果 ──
_SLOT_BLANK = ('<table><tr><td>联系电话</td><td>-</td>'
               '<td>公司地址</td><td>中山市小棚镇XX路 8 号</td></tr></table>')
_SLOT_HIDDEN = '<div class="tel">联系电话：</div><div class="v">点击登录后查看号码</div>'
_SLOT_KEFU = '<p>手机号码：<span>如需号码请联系客服登记</span></p>'
_SLOT_MISS = '<dl><dt>手机号码</dt><dd>13800138000</dd></dl>'
_SLOT_ENGLISH = ('<div class="contact">Tel: 0086-760-88886666</div>'
                 '<div>Mobile: 13800138000</div>'
                 '<script>var t = document.getElementsByTagName("a");</script>')
_SLOT_BOTH = '<p>联系电话：-</p><p>手机号码：13900001111</p>'
_SLOT_OTHER = '<tr><th>联系电话</th><th>面议</th></tr>'

for _lbl25, _html25, _want25 in (("栏位在、值写着 `-`（世界工厂网今天的样子）", _SLOT_BLANK, "blank"),
                                 ("点击查看才给号", _SLOT_HIDDEN, "hidden"),
                                 ("栏位写着「联系客服登记」才给号", _SLOT_KEFU, "hidden"),
                                 ("栏位在、值里本有号码（我们没抽走）", _SLOT_MISS, "miss"),
                                 ("只有英文标签", _SLOT_ENGLISH, "no_slot"),
                                 ("值是一段别的文字（不作证据）", _SLOT_OTHER, "no_slot"),
                                 ("一页都没这一栏", "<p>公司简介</p>", "no_slot"),
                                 ("空页", "", "no_slot")):
    _k25, _e25 = cdp_base.contact_slot_state(_html25)
    check(f"25a {_lbl25} → 判进 {_want25}", _k25 == _want25, f"实得 {_k25}｜{_e25}")
check("25a- 两栏并存时先认我们自己的锅（有一处该取到没取到，把话说成「站方没给」就会让那条真缺陷隐身）",
      cdp_base.contact_slot_state(_SLOT_BOTH)[0] == "miss", cdp_base.contact_slot_state(_SLOT_BOTH))
check("25a-2 证据里带着原文那一小段（只报档位不报原文，下一轮想复查还得自己重翻页面）",
      "联系电话" in cdp_base.contact_slot_state(_SLOT_BLANK)[1]
      and "-" in cdp_base.contact_slot_state(_SLOT_BLANK)[1],
      cdp_base.contact_slot_state(_SLOT_BLANK)[1])
check("25a-3 英文标签不入表是**事实**而不是口味：Tel/mobile 进了表，viewport、"
      "getElementsByTagName 这类脚本字节全被判成「一栏写着电话」，整页证据当场失真",
      cdp_base.contact_slot_state(_SLOT_ENGLISH)[1] == "", cdp_base.contact_slot_state(_SLOT_ENGLISH)[1])
# 号码只认栏位后面那一小段窗口：拿整页任意一处号码当证据，就是把「这一家」的账记到邻居家
_W25 = cdp_base.contact_slot_state('<p>联系电话</p>' + "厂" * 200 + "13800138000")
check("25a-4 号码离栏位超过一屏就不算这一栏的证据（窗口只取 120 字）",
      _W25[0] != "miss", str(_W25))
_dirty25 = "<" + "y" * 400 + ">13800138000"
check("25a-5 剥标签的小刀限长：模板里把 `<` 写在文字里的脏页不许被一口吞掉整段正文",
      "13800138000" in cdp_base.CONTACT_SLOT_TAG_RE.sub(" ", _dirty25), _dirty25[:24])

# ── 25b 证据一次一清：后一页（更具体的那一页）覆盖前一页，空串不覆盖 ──
_c25 = B2b168Crawler()
_e25, _lg25 = equip(_c25)
_c25._detail_html = ""
check("25b _keep_detail 原样交回正文（写成链式才拦得住「取了不用」那一族：接在哪一步都不改流程）",
      _c25._keep_detail(_SLOT_BLANK) == _SLOT_BLANK and _c25._detail_html == _SLOT_BLANK)
_c25._keep_detail("")
check("25b- 空串不许覆盖已有证据（非200/空页时把上一腿的证据抹掉，分档就退成没证据的 unknown）",
      _c25._detail_html == _SLOT_BLANK, _c25._detail_html[:24])
_c25._keep_detail(None)
check("25b-2 连 None 也不许覆盖，并原样交回（helper 不许把取页结果改成另一种东西）",
      _c25._detail_html == _SLOT_BLANK and _c25._keep_detail(None) is None)
_c25._keep_detail(_SLOT_HIDDEN)
check("25b-3 后一次取页覆盖前一次（多数站先商铺首页、后专用联系页，要问的是最后那一页）",
      _c25._detail_html == _SLOT_HIDDEN)

# ── 25c 分档记账：无证据时宁可说 unknown/no_page，也不许借 no_slot 把「我们没看」说成「页面没这一栏」──
_c25.reset_run_state() if hasattr(_c25, "reset_run_state") else None
_c25._nc_kinds, _c25._nc_evidence, _c25._detail_html = {}, {}, ""
_c25._keep_detail(_SLOT_BLANK)
_c25._note_no_contact()
check("25c 有正文时按页面自己那句话定性（blank 就是站方这一家没登，不是我们的缺陷）",
      _c25._nc_kinds == {"blank": 1} and "联系电话" in _c25._nc_evidence["blank"],
      str(_c25._nc_evidence))
check("25c- 证据用完即清：下一家不许拿着上一家的栏目当自己的分档凭据（串页说谎同一族）",
      _c25._detail_html == "", _c25._detail_html[:24])
_c25._note_no_contact()
check("25c-2 拿不到正文时单列一档 unknown（不借 no_slot 的名义）：判不了就说判不了",
      _c25._nc_kinds.get("unknown") == 1 and "没把正文留给" in _c25._nc_evidence["unknown"],
      str(_c25._nc_evidence))
_c25._nc_kinds, _c25._nc_evidence = {}, {}
_c25.NO_DETAIL_EVIDENCE = "本站详情只取列表卡字段，本来就没有单店正文可判"
_c25._note_no_contact()
check("25c-3 类里声明过理由的站走 no_page（本站的设计边界），不许冒充 unknown（把手没接）："
      "两者长相一样、处方相反——给已声明详情不取页的站开「先接把手」的处方是执行不了的谎",
      _c25._nc_kinds == {"no_page": 1}
      and "只取列表卡字段" in _c25._nc_evidence["no_page"], str(_c25._nc_evidence))
del _c25.NO_DETAIL_EVIDENCE
_c25._nc_kinds, _c25._nc_evidence = {}, {}
_c25._note_no_contact()
check("25c-3b 同一个把手、同一条无正文路径，把声明摘掉就必须换一档（no_page 不是给所有站兜底的"
      "退路：它只在类里真写过理由时才成立）",
      _c25._nc_kinds == {"unknown": 1}, str(_c25._nc_kinds))
_c25._nc_kinds, _c25._nc_evidence, _c25._detail_html = {}, {}, ""
_c25._keep_detail(_SLOT_BLANK)
_c25._note_no_contact()
_c25._keep_detail('<p>联系电话：</p><p>暂无</p>')
_c25._note_no_contact()
check("25c-4 每一档只留第一条证据（这一格是给人抽看「那句话到底是不是站方写的」，留全量就变没人读的日志）",
      _c25._nc_kinds["blank"] == 2 and "-" in _c25._nc_evidence["blank"], str(_c25._nc_evidence))

# ── 25d 砍单记名：只有一本计数时那句处方（「该复核判定口径」）是不可执行的 ──
_c25.reset_run_state()
for _i25 in range(6):
    _c25._note_early_skip("电梯导轨", f"样本公司{_i25}")
check(f"25d 每个词只留前 {cdp_base.CUT_SAMPLE_CAP} 家（这本账给人抽看不是名单导出，再多界面那一行就没人读）",
      len(_c25.kw_early_names["电梯导轨"]) == cdp_base.CUT_SAMPLE_CAP,
      str(_c25.kw_early_names))
check("25d- 但计数照旧全量（样本只是给人看的，预算与差分靠的是数）",
      _c25.kw_early["电梯导轨"] == 6, str(_c25.kw_early))
_c25._note_early_skip("玻璃钢格栅", "")
check("25d-2 名字传空就不进样本（不许用空字符串占住一个名额，那一档变成「有名无实」）",
      _c25.kw_early_names.get("玻璃钢格栅", []) == [], str(_c25.kw_early_names))
check("25d-3 没名单就返回空串——调用方照常拼，不许编一句空括号上日志",
      _c25._cut_note(_c25.kw_early_names, "玻璃钢格栅", "早过滤") == "", "")
_long25 = "中山市古镇镇长鸿电梯导轨配件厂、第二制造厂分厂中山部长长"
_c25._note_detail_filter("电梯导轨", _long25)
_s25 = _c25.kw_filtered_names["电梯导轨"][0]
check(f"25d-4 名字里找到边界就按边界收口（上限 {cdp_base.CUT_NAME_CAP} 字）",
      len(_s25) <= cdp_base.CUT_NAME_CAP + 2 and _long25.startswith(_s25.rstrip("…"))
      and "、" not in _s25.rstrip("…"), _s25)
_nobreak25 = "中山部长长长长长长长长长长长长长长长长长长长长长长长长有限公司分厂"
_c25b = B2b168Crawler()
equip(_c25b)
_c25b._cut_sample(_c25b.kw_early_names, "电梯导轨", _nobreak25)
check("25d-4b 一个边界都没有时宁可整名带出：半截公司名比长名字更误导"
      "（复核的人拿着半截名根本搜不到这家，收口把手的契约写在 text_shape.short_reason）",
      _c25b.kw_early_names["电梯导轨"] == [_nobreak25 + "…"], str(_c25b.kw_early_names))
check("25d-5 零产出归因那句必须带着名单（报告说「该复核」，复核的人手上必须有几家名字可看）",
      "样本公司0" in _c25._kw_zero_reason("电梯导轨"), _c25._kw_zero_reason("电梯导轨"))

# ── 25e 说词只有一个把手：日志/GUI 看板/体检报告三处取同一张表 ──
check("25e 空账返回空串（调用方据此**不说**这一格，不许编一句「无异常」顶上去）",
      cdp_base.nc_kinds_text({}) == "" and cdp_base.nc_kinds_text(None) == "")
_t25 = cdp_base.nc_kinds_text({"blank": 3, "miss": 1}, {"miss": "手机号码→13800138000"})
check("25e- 渲染按固定序而不是字典序：没这张表就会排出 blank 在前，读的人第一眼就是「不是缺陷」",
      _t25.index("栏位里本有号码") < _t25.index("站方这一家没登"), _t25)
_all25 = cdp_base.nc_kinds_text({k: 1 for k in cdp_base.NC_KIND_ORDER})
# 每一档必需自带一句自己的处方。这张表**故意不**从 NC_KIND_LABEL 自动推导：它是人写的
# 「这一档该干什么」的清单，而 NC_KIND_ORDER 是产品侧的档位表。两表不一致就是
# 「新增了一档而处方没跟上」，那一档会落回最后一句模棱两可的话（25e-4 管这个）。
_EACH25 = {
    "miss": "该修代码",
    "unknown": "先接 _keep_detail",
    "no_page": "详情不取页",
    "hidden": "点击查看",
    "blank": "不是缺陷",
    "no_slot": "无从判定",
}
check(f"25e-2 每一档都带着自己的处方（{'／'.join(cdp_base.NC_KIND_ORDER)} 共 "
      f"{len(cdp_base.NC_KIND_ORDER)} 档各说一句，合成一档就只剩一句模棱两可的话）",
      set(_EACH25) == set(cdp_base.NC_KIND_ORDER)
      and all(any(w in seg for seg in _all25.split("｜")) for w in _EACH25.values())
      and _all25.count("1 家") == len(cdp_base.NC_KIND_ORDER),
      f"档位表={set(cdp_base.NC_KIND_ORDER) ^ set(_EACH25)}｜渲染={_all25}")
check("25e-3 miss 那一档把证据一并念出来（只报档位不报原文，下一轮复查还得自己重翻一百页）",
      "证据" in _t25 and "13800138000" in _t25, _t25)
check("25e-4 档位表里 miss/unknown 都在站方那两档前面（先认自己的锅这条纪律写在渲染层而不是靠自觉），"
      "设计边界那一档（no_page）排在站方三档之前",
      cdp_base.NC_KIND_ORDER[:2] == ("miss", "unknown")
      and cdp_base.NC_KIND_ORDER.index("no_page")
      < min(cdp_base.NC_KIND_ORDER.index(k) for k in ("hidden", "blank", "no_slot")),
      str(cdp_base.NC_KIND_ORDER))
check("25e-5 no_page 与 unknown 的处方不许合成一句（两者长相一样、动作相反：一个是本站已声明的"
      "设计边界、一个是漏接把手；第十轮实机就是给已声明详情不取页的站开了张「先接把手」的假处方）",
      "_keep_detail" not in cdp_base.NC_KIND_LABEL["no_page"]
      and "_keep_detail" in cdp_base.NC_KIND_LABEL["unknown"], str(cdp_base.NC_KIND_LABEL))

# ── 25f 收尾走真循环：从 crawl_detail 到那一行日志，中间不许有一环靠注释保证 ──
class _Fix25(B2b168Crawler):
    """最小夹具站：列表直接给出、详情只回一页正文，但**记账、分档、收尾渲染全走基类真路**。"""
    PLATFORM_SLUG = "fix25"
    PLATFORM_NAME = "夹具站"
    STUBS = ({"公司名称": "夹具电梯导轨有限公司", "memberId": "a1"},
             {"公司名称": "夹具玻璃钢格栅厂", "memberId": "a2"})
    PAGE = _SLOT_BLANK
    KEEP = True

    def _is_session_alive(self):
        return True

    def start_browser(self):
        return True

    def _iter_keyword(self, kw):
        self.stat_pages = 1
        self.stat_listed = len(self.STUBS)
        for _s in self.STUBS:
            yield dict(_s)

    def crawl_detail(self, stub):
        rec = self._base_rec(stub)
        rec["公司名称"] = stub.get("公司名称", "")
        if self.KEEP:
            self._keep_detail(self.PAGE)
        rec["状态"] = "无联系方式"
        return rec


def _run25(page, keep=True):
    c = _Fix25()
    c.incremental = False
    c.resume = False
    c.KEEP, c.PAGE = keep, page
    _e, lg = equip(c)
    c.crawl(["电梯导轨"])
    return c, [m for m in lg if "本轮账目" in m]

_c25f, _acc25 = _run25(_SLOT_BLANK)
check("25f 收尾那一行真念出了分档（不只在 stats() 里累加——「只累加从不外读」正是通路账那一族的原病）",
      len(_acc25) == 1 and "无联系方式分档" in _acc25[0] and "站方这一家没登" in _acc25[0],
      _acc25[-1][:120] if _acc25 else "没打出账目行")
check("25f- 两家用同一页版式时也只留一条证据，但家数记两笔（否则「这一档几家」这句话没凭据）",
      _c25f._nc_kinds == {"blank": 2}, str(_c25f._nc_kinds))
_c25g, _acc25g = _run25(_SLOT_MISS)
check("25f-2 miss 那一档进了账目行时，说词必须是「该修代码」（站方藏着与我们的锅不许合用一句话）",
      any("栏位里本有号码" in m and "该修代码" in m for m in _acc25g), _acc25g[-1][:120] if _acc25g else "")
_c25h, _acc25h = _run25("", keep=False)
check("25f-3 忘了接证据的站，那一行只能说「本站没留正文证据（判不了）」——把「我们没看」说成"
      "「页面没这一栏」就是同一方向的谎换个壳",
      any("没留正文证据" in m for m in _acc25h), _acc25h[-1][:120] if _acc25h else "")
_c25i = _Fix25()
_c25i.incremental = _c25i.resume = False
_e25i, _lg25i = equip(_c25i)
_c25i._landing_mismatch = {"b2b168.com": [3, "落在别人家的 showroom"]}
_c25i.crawl(["电梯导轨"])
check("25f-4 落点不符那一笔在正常出货的轮次里也要露出来（旧口径只进 stats()，全局 grep 不到一处渲染）",
      any("落点不符" in m and "b2b168.com 3 次" in m for m in _lg25i),
      [m for m in _lg25i if "本轮账目" in m][-1][:120])
check("25f-5 stats() 里三格同名字段同路进出（界面与体检器读的就是这一份，少一格那一处就回到只看总数）",
      {"nc_kinds", "nc_evidence", "nc_note"} <= set(_c25f.stats())
      and _c25f.stats()["nc_note"] == cdp_base.nc_kinds_text(_c25f._nc_kinds, _c25f._nc_evidence),
      str(sorted(k for k in _c25f.stats() if k.startswith("nc_"))))

# ── 25g 借标签必须还：行为、六站覆写、以及「关失败」不许被抹平 ──
import shared_chrome as _sc25                                       # noqa: E402
_closed25 = []
_real_close25 = _sc25.close_tab


def _rec25(port, tab):
    _closed25.append((port, tab))


_sc25.close_tab = _rec25
try:
    _c25r = B2b168Crawler()
    _e, _lg25r = equip(_c25r)
    _c25r.pin_tab_id = "TAB-1"
    _c25r._borrowed_tab = True
    check("25g 收工把借来的那枚标签真的关掉了（只改标志位不算关：共享 Chrome 里还挂着它）",
          _c25r.close_browser() is None and _closed25 == [(_c25r.port, "TAB-1")], str(_closed25))
    check("25g- 归还后 pin_tab_id 与借用标志一起复位（只清一个，下一轮就会去关一枚早已不存在的标签）",
          _c25r.pin_tab_id == "" and _c25r._borrowed_tab is False, str(_c25r.pin_tab_id))
    _closed25.clear()
    _c25r.pin_tab_id = ""
    check("25g-2 手里没标签时一声不响地过（纯 HTTP 站平时不碰浏览器，不许把共享 Chrome 拉起来陪跑）",
          _c25r.release_tab("收工") is True and not _closed25, str(_closed25))

    def _boom(port, tab):
        raise OSError("连接被拒：Chrome 已经不在了")

    _sc25.close_tab = _boom          # 下面六站那一圈要重新装回记录器，别把「关失败」带进对照
    _c25r.pin_tab_id = "TAB-2"
    _ok25r = _c25r.release_tab("过验证")
    check("25g-3 关失败必须开口并回 False（旧写法 except: pass 把「没关掉」抹平成「已经收工」）",
          _ok25r is False and any("归还标签没成功" in m and "TAB" not in m for m in _lg25r)
          and any("过验证" in m for m in _lg25r), str(_lg25r[-2:]))
    check("25g-4 归还没成功时仍然把 pin 清掉（不清就会被下一轮当成「还有一枚」反复关）",
          _c25r.pin_tab_id == "")
    # 六站覆写逐个跑一遍：这一族的病根正是「基类对了，覆写那一份是空壳」
    import aipage_crawler as _m25a                                   # noqa: E402
    import gi_crawler as _m25g                                       # noqa: E402
    import maimaib2b_crawler as _m25m                                # noqa: E402
    import solarbe_crawler as _m25s                                  # noqa: E402
    import makepolo_crawler as _m25p                                  # noqa: E402
    for _cls25 in (_m25a.AipageCrawler, _m25g.GiCrawler, _m25m.Maimaib2bCrawler,
                   _c25r.__class__, _m25s.SolarbeCrawler, _m25p.MakepoloCrawler):
        _sc25.close_tab = _rec25
        _closed25.clear()
        _c25s = _cls25()
        equip(_c25s)
        _c25s.pin_tab_id = f"TAB-{_c25s.PLATFORM_SLUG}"
        _want25g = (f"TAB-{_c25s.PLATFORM_SLUG}",)
        _c25s.close_browser()
        check(f"25g-5 {_cls25.__name__} 的 close_browser 覆写不是空壳（本轮实机用户的 Chrome 里"
              f"就挂着一枚 maimaib2b 的商铺页）",
              _closed25 == [(_c25s.port, _want25g[0])] and _c25s.pin_tab_id == "",
              str(_closed25))
finally:
    _sc25.close_tab = _real_close25

# ── 25j 归还链路的「事实」必须一层层往上交：最底层报不了，上面每一句都是空话 ──
# 25g 验的是「上层肯不肯说」，这一节验的是「上层拿不拿得到可说的东西」：
# 旧写法里 shared_chrome.close_tab 把异常吞成 pass，cdp_base.release_tab 那句
# 「关失败必须开口」就挂在一条永不执行的分支上——行为夹具不抽掉这一层，就一直照不到。
_real_ls25j, _real_lpt25j = _sc25.local_session, _sc25.list_page_targets


class _Resp25j:
    def __init__(self, status=200, text="Target is closing"):
        self.status_code, self.text = status, text


class _Seq25j:
    """按脚本回标签列表的假把手：`pages` 里每一项是一次查询的结果（或一个异常）。

    连 `strict` 一起记下来：25j-9= 要验的就是「拿列表当证据的那一次走的是严格口径」。
    脚本用完后重复最后一项（而不是报 IndexError）：那样才能拿「查了几次」当差分证据。
    """

    def __init__(self, pages):
        self.pages, self.calls = list(pages), []

    def __call__(self, port=9255, strict=False, **_):
        self.calls.append((port, strict))
        item = self.pages[min(len(self.calls) - 1, len(self.pages) - 1)]
        if isinstance(item, Exception):
            raise item
        return item


class _Local25j:
    """假的本机控制面：可按脚本回响应，也可把请求直接抛挂。"""

    def __init__(self, script=None, boom=None):
        self.script, self.boom, self.urls = list(script or []), boom, []

    def get(self, url, timeout=0):
        self.urls.append(url)
        if self.boom is not None:
            raise self.boom
        return self.script.pop(0) if self.script else _Resp25j()

    def put(self, url, timeout=0):
        return self.get(url, timeout)


try:
    _sc25.local_session = lambda: _Local25j()
    _sc25.list_page_targets = lambda port=9255, **_: []
    check("25j-1 没给 target_id 时不许回空串（什么都没关却说「已还完」是同一句谎）",
          "没关" in (_sc25.close_tab(9255, "") or ""), repr(_sc25.close_tab(9255, "")))

    _sc25.local_session = lambda: _Local25j(boom=ConnectionError("127.0.0.1 拒绝连接"))
    _why25j = _sc25.close_tab(9255, "TAB-X")
    check("25j-2 关闭请求没送达时必须把原因交上去（旧写法：except: pass 把这里抹平）",
          "关闭请求没送达" in _why25j and "ConnectionError" in _why25j, repr(_why25j))

    _sc25.local_session = lambda: _Local25j()
    check("25j-3 关完再查一次列表，查不到才算真的关掉（Chrome 对不存在的 target 也回 200，"
          "只信回执就会被它骗成成功）",
          _sc25.close_tab(9255, "TAB-GONE") == "", repr(_sc25.close_tab(9255, "TAB-GONE")))
    _sc25.list_page_targets = lambda port=9255, **_: [{"id": "TAB-ALIVE", "url": "https://x/"}]
    _why25j2 = _sc25.close_tab(9255, "TAB-ALIVE")
    check("25j-4 回执 200 但标签还在列表里时不许当作成功（带上回执给人看）",
          "标签仍在列表里" in _why25j2 and "HTTP 200" in _why25j2, repr(_why25j2))

    # 25j-9/10：同一枚标签的「在不在」只能拿复查当证据，而证据本身也有读不到的时候。
    # 由来（2026-11-16 第十二轮实机 中国制造网）：收工那行「标签仍在列表里（关闭回执 HTTP 200
    # Target is closing）」是一句假警——Chrome 的 /json/close 是异步的，回执 200 时这一枚还能
    # 在 /json/list 里挂上几百毫秒，旧写法只查一次就把「还在收尾」说成「没关掉」。
    _sq25j = _Seq25j([[{"id": "TAB-LATE"}], [], []])
    _sc25.list_page_targets = _sq25j
    _why25j9 = _sc25.close_tab(9255, "TAB-LATE")
    check("25j-9 回执 200 后第一次复查仍在、第二次不在 → 不许报失败（旧写法只查一次，"
          "就在这一形上响假警；本项输入它能接住、旧写法接不住，不是拿旧样本喂判据）",
          _why25j9 == "" and len(_sq25j.calls) == 2, repr(_why25j9) + " " + str(_sq25j.calls))
    check("25j-9= 拿列表当证据的那几次查询必须全部走严格口径（strict=True）：默认口径读不到时"
          "回 []，「控制面不通」会被念成「已经还完」", all(s for _, s in _sq25j.calls),
          str(_sq25j.calls))
    _sq25j2 = _Seq25j([[{"id": "TAB-REALLY-ALIVE"}]])
    _sc25.list_page_targets = _sq25j2
    _why25j9b = _sc25.close_tab(9255, "TAB-REALLY-ALIVE")
    check("25j-9== 每次都查到在才算真没关掉，并把「复查了几次」交上去（读的人才知道这不是"
          "没等够，而是真的没关掉）",
          "标签仍在列表里" in _why25j9b and f"{len(_sc25.TAB_CLOSE_SETTLE)} 次" in _why25j9b
          and len(_sq25j2.calls) == len(_sc25.TAB_CLOSE_SETTLE),
          repr(_why25j9b) + " " + str(len(_sq25j2.calls)))
    _sq25j3 = _Seq25j([[]])
    _sc25.list_page_targets = _sq25j3
    check("25j-9=== 第一次就不在 → 只查一次就收（不许为了兜圈盲睡三轮，收工是八路并行的）",
          _sc25.close_tab(9255, "TAB-QUICK") == "" and len(_sq25j3.calls) == 1,
          str(_sq25j3.calls))
    _sq25j4 = _Seq25j([_sc25.TabListUnavailable("ConnectionError: 127.0.0.1 拒绝连接")])
    _sc25.list_page_targets = _sq25j4
    _why25j10 = _sc25.close_tab(9255, "TAB-UNKNOWN")
    check("25j-10 复查本身没送达时不许回空串（旧写法：查询失败吞成 [] → 「不在了」 → 向用户"
          "宣布已还完，与 32e/32g 那一族同形）",
          _why25j10 != "" and "没送达" in _why25j10 and "手动确认" in _why25j10
          and "ConnectionError" in _why25j10, repr(_why25j10))
    check("25j-10= 也不许把它说成「仍在列表里」：我们根本没看见它在不在了，只能说没核对上",
          "仍在列表里" not in _why25j10, repr(_why25j10))

    # release_tabs：拿到的事实必须点名说出来，而且不能因为一枚没关就少关下一枚
    _at25j, _lg25j = [], []

    def _fake_close25j(port, tid):
        _at25j.append(tid)
        return "标签仍在列表里（关闭回执 HTTP 200）" if tid == "A" else ""

    _real_close_keep = _sc25.close_tab
    _sc25.close_tab = _fake_close25j
    try:
        _sc25.list_page_targets = lambda port=9255, **_: [{"id": "A"}, {"id": "B"}, {"id": "C"}]
        _sc25.release_tabs(["A", "B"], 9255, lambda m: _lg25j.append(m))
        check("25j-5 收工批次里每一枚都要试过（没关掉那一枚不许把计数减掉，否则下一枚被连带不关）",
              _at25j == ["A", "B"], str(_at25j))
        check("25j-6 批次收工里没关掉的那一枚必须点名（旧写法连 log 参数都从不调用）",
              len(_lg25j) == 1 and "A" in _lg25j[0] and "手动" in _lg25j[0], str(_lg25j))
        # 同一批收工的另一条死路：列表本身读不到 → `keep_alive = len([]) = 0` → `keep_alive > 1`
        # 把整批归还动作全跳过，日志却照旧一路念到「收工」（这一枚标签根本没人去关）。
        _lg25j11 = []
        _sc25.list_page_targets = _Seq25j([_sc25.TabListUnavailable("ConnectionError: 控制面不通")])
        _sc25.release_tabs(["T1", "T2"], 9255, lambda m: _lg25j11.append(m))
        check("25j-11 收工批次里列表读不到时必须开口（不许一声不响地把整批归还跳过去）",
              len(_lg25j11) == 1 and "没核对" in _lg25j11[0] and "手动确认" in _lg25j11[0]
              and "ConnectionError" in _lg25j11[0], str(_lg25j11))
    finally:
        _sc25.close_tab = _real_close_keep

    # cdp_base.release_tab 必须消费返回值：只看异常接不到事实，等于白修了下面那一层
    _c25j = B2b168Crawler()
    _e25j, _lg25jc = equip(_c25j)
    _real_close_keep2 = _sc25.close_tab
    _sc25.close_tab = lambda port, tid: "标签仍在列表里（关闭回执 HTTP 200）"
    try:
        _c25j.pin_tab_id = "TAB-9"
        _ok25j = _c25j.release_tab("收工")
        check("25j-7 最底层回「没关掉」时 release_tab 必须回 False 并把那句话念完"
              "（只听异常的实现会在这里直接报成功）",
              _ok25j is False and any("归还标签没成功" in m and "标签仍在列表里" in m for m in _lg25jc),
              str(_lg25jc[-1:]))
    finally:
        _sc25.close_tab = _real_close_keep2
    _c25k = B2b168Crawler()
    _e25k, _lg25kc = equip(_c25k)
    _sc25.close_tab = lambda port, tid: ""
    try:
        _c25k.pin_tab_id = "TAB-10"
        check("25j-8 最底层确认关掉时不编多余告警（日志里只留事实，不留「可能」）",
              _c25k.release_tab("收工") is True and not _lg25kc, str(_lg25kc))
    finally:
        _sc25.close_tab = _real_close_keep2
finally:
    _sc25.local_session, _sc25.list_page_targets = _real_ls25j, _real_lpt25j

# ── 25h 机器判据：直接跑构建前第 32 条硬门的本尊，退回修复前的每一族都必须响 ──
_g32, _g32err = _load_gate31()
check("25h 门禁脚本可加载（加载不到就等于离线这一侧对三本账失明）", not _g32err, _g32err)
if _g32 is not None:
    check("25h0 现网真源上第 32 条全绿（后面每一档反向对照都要拿它当对照面）",
          not _g32.check_no_contact_accountability(),
          " ｜ ".join(x.splitlines()[0] for x in _g32.check_no_contact_accountability()[:4]))
    _F32 = _g32._runtime_reason_sources()
    _T32 = {rel: (_g32.ROOT / rel).read_text(encoding="utf-8", errors="replace")
            for rel in _g32.NC_READERS}
    check(f"25h0- 第 32 条的视野不是空集合（读了 {len(_F32)} 份交付链源码）",
          len(_F32) > 40, str(len(_F32)))

    def _mut32(rel, old, new, cnt=1):
        """把真源里的一段换回修复前的样子，再让门禁本尊判一次（锚点对不上就当场响）。

        `cnt=-1` 那一档是「这一族每处都要改回去」的写法：把手在体检器里被 import 了一次、
        调用了若干次，只改第一处就还是「代码没改干净」而不是「判据看不见」。"""
        f, t, hit = dict(_F32), dict(_T32), False
        for p in list(f):
            if str(p).endswith(rel) and old in f[p]:
                f[p] = f[p].replace(old, new, cnt)
                hit = True
        if rel in t and old in t[rel]:
            t[rel] = t[rel].replace(old, new, cnt)
            hit = True
        assert hit, f"25h 夹具锚点对不上真源（{rel}）：源码改了形状而夹具还在替旧代码作证"
        return _g32.check_no_contact_accountability(f, t)

    _MUT32 = {
        "把 aipage 详情那页的证据摘掉（回到只往 rec 里填字段）":
            ("b2bcollect_build/aipage_crawler.py",
             "self._keep_detail(", "self._keep_detail_x("),
        "把基类早过滤的传名改回只传词（「该复核」又变成不行使的处方）":
            ("b2bcollect_build/cdp_base.py",
             'self._note_early_skip(kw, e.get("公司名称", ""))', "self._note_early_skip(kw)"),
        "把手本体只 += 计数不写名单":
            ("b2bcollect_build/cdp_base.py",
             "self._cut_sample(self.kw_early_names, kw, name)", "pass"),
        "stats() 不再透出 nc_note":
            ("b2bcollect_build/cdp_base.py", '"nc_note": nc_kinds_text(', '"nc_note_x": nc_kinds_text('),
        "收尾那一行退回只报「无联系方式 N」的总数":
            ("b2bcollect_build/cdp_base.py", "｜无联系方式分档：", "｜无联系方式合计："),
        "体检器自己拼说词（不走唯一把手，import 与调用点一起摘）":
            ("debug/platform_live_check.py", "nc_kinds_text", "nc_say_it_my_way", -1),
        "界面红牌那一行不读分档（两处取值一起改回去才叫不读）":
            ("b2bcollect_build/channel_health.py", "nc_note", "nc_note_unused", -1),
        "close_browser 又覆写成空壳":
            ("b2bcollect_build/makepolo_crawler.py",
             'self.release_tab("收工")', "self._borrowed_tab = False"),
        "两份镜像只改一个字（1688 那一包不会自己报分叉）":
            ("ali1688_build/ali1688_crawler.py", "站方藏着（点击查看", "站方藏着呢（点击查看"),
        "镜像标记被改名（对拍两边都抓不到，必须响而不是默默相等）":
            ("ali1688_build/ali1688_crawler.py", "# ── [NC-BLOCK-END]", "# ── [NC-BLOCK-EDN]"),
        # ↓ 32g 本轮新立的那一问：最底层重新吞掉失败时，判据必须自己看到
        "shared_chrome.close_tab 又把失败吞成 pass（最底层一吞，上面每一句「关失败必须开口」都变死分支）":
            ("b2bcollect_build/shared_chrome.py",
             '    except Exception as e:\n        return f"关闭请求没送达（{type(e).__name__}）：'
             '{first_line(e, 80)}"',
             '    except Exception:\n        pass'),
    }
    for _lbl32, _case32 in _MUT32.items():
        _got32 = _mut32(*_case32)
        check(f"25h- 反向对照（{_lbl32}）：这一族退回去时第 32 条必须响",
              bool(_got32), "克隆没生效或判据看不见这一族" if not _got32 else "")
    check("25h-2 换掉镜像那一个字，响的确实是「两份逐字节对拍」那一条（而不是被别的判据顺便拦下）",
          any("分叉" in p for p in _mut32("ali1688_build/ali1688_crawler.py",
                                          "站方藏着（点击查看", "站方藏着呢（点击查看")),
          str(_mut32("ali1688_build/ali1688_crawler.py",
                     "站方藏着（点击查看", "站方藏着呢（点击查看")[:1]))
    # 正面形状：判据只拦「没说」，不拦「怎么说」——三种合格答案都必须安静
    _SYN25 = {
        "抽象桩（基类模板方法，它一页都不取）": (
            "class A:\n    def crawl_detail(self, stub):\n"
            '        """子类实现。"""\n        raise NotImplementedError\n', False),
        "具体实现取了页却没留证据": (
            "class A:\n    def crawl_detail(self, stub):\n        return self._parse(self._html())\n", True),
        "证据在它调下去的第二条腿里（沿调用闭包找，不看它在第几层）": (
            "class A:\n    def crawl_detail(self, stub):\n        return self._one(stub)\n"
            "    def _one(self, stub):\n        return self._two()\n"
            "    def _two(self):\n        return self._keep_detail(self._html())\n", False),
        "确实没有单店正文可判，但写了理由": (
            'class A:\n    NO_DETAIL_EVIDENCE = "详情不取页"\n'
            "    def crawl_detail(self, stub):\n        return {}\n", False),
        "砍单把手按关键字传名": (
            "class A:\n    def _go(self, kw, rec):\n"
            '        self._note_early_skip(kw, name=rec["公司名称"])\n', False),
        "自己 quit 掉会话（不叫 release_tab 也算归还）": (
            "class A:\n    def close_browser(self):\n        self._driver.quit()\n", False),
        # ↓ 32g 按**动作**认人：改名、抽 helper、Selenium 写法都拦不住判据的视线
        "归还动作换了名字（只按函数名登记表发判时这一形就能逃掉）": (
            "class A:\n    def tidy_up(self):\n        try:\n"
            "            self._close_own_tab()\n        except Exception:\n            pass\n", True),
        "Selenium 写法 driver.close() 失败被吞（不写 close_browser 也一样要响）": (
            "class A:\n    def _nav(self):\n        try:\n"
            "            self.driver.close()\n        except Exception:\n            pass\n", True),
        "文件 close 失败被吞（f.close() 不是关标签，不许误伤）": (
            "class A:\n    def _read_cfg(self):\n        try:\n            f = open('x')\n"
            "            f.close()\n        except Exception:\n            pass\n", False),
        "归还放在 finally、except 只包导航（那一格不负责关标签，不许误伤）": (
            "class A:\n    def _nav(self):\n        try:\n            self.driver.get('u')\n"
            "        except Exception:\n            pass\n        finally:\n"
            "            self._close_own_tab()\n", False),
    }
    for _lbl25s, (_src25s, _want_fire) in _SYN25.items():
        _f25s = dict(_F32)
        _f25s[_P12("b2bcollect_build/zz_fixture25_crawler.py")] = _src25s
        _got25s = [p for p in _g32.check_no_contact_accountability(_f25s, dict(_T32))
                   if "zz_fixture25" in p]
        check(f"25h-3 「{_lbl25s}」期望{'响' if _want_fire else '不响'}"
              f"（误伤一次，下一个真漏的人就会被当成噪音放过去）",
              bool(_got25s) is _want_fire, str(_got25s[:1]))
    # 视野断了不许报绿：三个 *_build 全落空时，「每一路都留了证据」这句话没任何含义
    _empty32 = _g32.check_no_contact_accountability({}, {})
    check("25h-4 反向对照：视野空集合时这一条必须响（在空集合上报绿等于这一条门没跑）",
          _empty32 and "一份源码都没读到" in _empty32[0], str(_empty32[:1]))
    # 两份镜像从离线这一侧也对拍一次（不依赖门禁脚本能跑）
    _blk25a = _g32._nc_block_of(_T32["b2bcollect_build/cdp_base.py"])
    _blk25b = _g32._nc_block_of(_T32["ali1688_build/ali1688_crawler.py"])
    check(f"25h-5 两份分档判据逐字节相同（{len(_blk25a)} 字）：1688 是独立交付包不能 import，"
          f"只能内联一份拷贝，而拷贝漂移时没人会发现",
          _blk25a == _blk25b and len(_blk25a) > 1000, f"{len(_blk25a)} ↔ {len(_blk25b)}")
    # 32h 本轮新立的那一问：NC-BLOCK 只圈住判据（常量与纯函数），而 no_page/unknown
    # 这一分叉住在**方法体**里。只改镜像那一侧的方法体时判据段依旧逐字节相同，
    # 1688 那一路就会悄悄退回「把已声明的设计边界说成忘了接把手」。
    _h32 = _mut32("ali1688_build/ali1688_crawler.py",
                  '            kind = "no_page" if declared else "unknown"',
                  '            kind = "unknown"')
    check("25h-6 镜像里把 no_page 分叉退回「一律 unknown」必须响在「行为指纹」那一条，"
          "而不是误响在逐字节对拍那一条（这一形里判据段确实没动）",
          any("行为不同" in p for p in _h32) and not any("分叉" in p for p in _h32),
          str([p.splitlines()[0][:60] for p in _h32]))
    check("25h-7 32h 不许误伤「注释不一样」：两份镜像的 docstring 本就该按读者写两份，"
          "只改注释时行为指纹那一条必须安静（否则下一次真分叉会混在一堆噪音里）",
          not any("行为不同" in p for p in _mut32(
              "ali1688_build/ali1688_crawler.py", "按责任分档的唯一把手",
              "按责任分档的唯一把手本体（注释换个说法）")),
          str([p.splitlines()[0][:60] for p in _mut32(
              "ali1688_build/ali1688_crawler.py", "按责任分档的唯一把手",
              "按责任分档的唯一把手本体（注释换个说法）")]))
    _kd32 = _mut32("ali1688_build/ali1688_crawler.py",
                   '        if isinstance(html, str) and html:\n            self._detail_html = html',
                   '        self._detail_html = html')
    check("25h-8 另一个把手（_keep_detail）在镜像里退回「空串也覆盖」同样要响："
          "非200/空页那一腿会把上一腿的证据抹掉，分档退成没证据的 unknown",
          any("_keep_detail" in p and "行为不同" in p for p in _kd32),
          str([p.splitlines()[0][:60] for p in _kd32]))

# ── 25i 归因层：旧那句谎不许复活，每一档给的是不同的处方 ──
import importlib.util as _ilu25                                      # noqa: E402
_spec25 = _ilu25.spec_from_file_location("plc25", HERE.parent / "debug" / "platform_live_check.py")
_plc25 = _ilu25.module_from_spec(_spec25)
_spec25.loader.exec_module(_plc25)
_ADVICE25 = {
    "miss": ("先修我们的解析", "不该换成「站方没给」"),
    "unknown": ("crawl_detail 接上 _keep_detail", "不该把没看说成没栏"),
    "no_page": ("这不是漏接把手", "处方与 unknown 相反：已声明详情不取页的站只能加一条详情腿"),
    "hidden": ("换通路", "不该让人去改本来正确的解析代码"),
    "blank": ("换词或换站", "不是通路缺陷"),
    "no_slot": ("取的是不是这一家的页", "先查串页"),
}
check("25i-0 处方表与档位表必须是同一集合（产品侧新增一档而这里没跟上，那一档就会落回"
      "最后那句模棱两可的话——正是本节要消灭的那句谎）",
      set(_ADVICE25) == set(cdp_base.NC_KIND_ORDER),
      f"只差集={set(_ADVICE25) ^ set(cdp_base.NC_KIND_ORDER)}")
for _k25i, (_want25i, _note25i) in _ADVICE25.items():
    _adv25 = _plc25._nc_advice({"nc_kinds": {_k25i: 7}})
    check(f"25i {_k25i} 那一档给的是自己的处方（{_want25i}）——{_note25i}",
          _want25i in _adv25, _adv25)
check("25i- 旧那句谎不得复活：把四项全空一律归给「取号通路」（世界工厂网今天就能拆穿它）",
      not any("先修取号通路再谈换词" in _plc25._nc_advice({"nc_kinds": {k: 1}})
              for k in _ADVICE25), str(_plc25._nc_advice({"nc_kinds": {"blank": 1}})))
check("25i-2 一本账都没时不许编处方（说不出就闭嘴，这一格归详情失败/通路账去看）",
      "详情失败" in _plc25._nc_advice({"nc_kinds": {}}) or "通路账" in _plc25._nc_advice({}),
      _plc25._nc_advice({}))
check("25i-3 体检报告那一行念的是唯一把手的句子（报告与日志两种说词时，「谁的责任」就没唯一答案）",
      _plc25._nc_line({"nc_kinds": {"blank": 2}, "nc_evidence": {"blank": "联系电话→-"}})
      == cdp_base.nc_kinds_text({"blank": 2}, {"blank": "联系电话→-"}),
      _plc25._nc_line({"nc_kinds": {"blank": 2}}))
check("25i-4 两档「拿不到证据」的处方必须相反：unknown 要接把手、no_page 不许接把手"
      "（把两者归成一档，本轮就对跨国采购网说了那句执行不了的假处方）",
      "_keep_detail" in _plc25._nc_advice({"nc_kinds": {"unknown": 3}})
      and "_keep_detail" not in _plc25._nc_advice({"nc_kinds": {"no_page": 3}})
      and "详情不取页" in _plc25._nc_advice({"nc_kinds": {"no_page": 3}}),
      _plc25._nc_advice({"nc_kinds": {"no_page": 3}}))
check("25i-5 处方只说自己那一档：no_page 不许顺手把站方那几档的话术带进来（一档一处方，"
      "一句里出现三种动作等于没给处方）",
      not any(w in _plc25._nc_advice({"nc_kinds": {"no_page": 3}})
              for w in ("该修我们的解析", "站方自己没登", "根本没有联系方式栏")),
      _plc25._nc_advice({"nc_kinds": {"no_page": 3}}))

# ── 25k 证据那句话本身：120 字窗口切在标签中间时，报告里不许留下没合口的半截标签 ──
# 由来（2026-11-16 第十二轮实机 马可波罗）：账目行末尾那句
#   ｜无联系方式分档：站方藏着 …… 9 家（证据：联系电话 ：→点击查看联系 <img style="margi）
# 一句话里两处「半截词就是说谎」：`margi` 是被裸字符切砍掉一半的属性名，而整句读起来
# 像我们的解析坏了——页面上写的其实只是「点击查看联系」（站方藏着，该换通路不该改代码）。
_SLOT_DANGLE = ('<td>联系电话</td><td>：</td>'
                '<a href="#" style="margin-top: 5px">点击查看联系</a><img style="margi')
_k25k, _e25k = cdp_base.contact_slot_state(_SLOT_DANGLE)
check("25k 尾巴不许影响档位（判定仍用完整窗口，切晚了才不会把 hidden 判成别的档）",
      _k25k == "hidden", f"{_k25k} {_e25k}")
check("25k- 证据里没有没合口的标签、也没有被砍成一半的属性名（`<` 要么带 `>` 一起留下、要么整截收掉）",
      "<" not in _e25k and "margi" not in _e25k, _e25k)
check("25k-2 收掉尾巴不等于收掉证据：页面原文那三个字必须还在（下一轮要靠它去页面上核对）",
      "点击查看" in _e25k, _e25k)
_old25k = ""
for _m25k in cdp_base.CONTACT_SLOT_LABEL_RE.finditer(_SLOT_DANGLE):
    _w25k = re.sub(r"\s+", " ", cdp_base.CONTACT_SLOT_TAG_RE.sub(
        " ", _SLOT_DANGLE[_m25k.end():_m25k.end() + 120])).strip()
    if any(h in _w25k[:16] for h in cdp_base.CONTACT_SLOT_HIDDEN):
        _old25k = f"{_m25k.group(0).strip()}→{_w25k[:24]}"      # 旧那一刀：裸字符切、不剥尾巴
        break
check("25k== 判据不是空跑：同一份 HTML 走修复前那一刀，必须真能现出那截没合口的尾巴"
      "（不然上面三条就是在空集合上报绿；差分只看「尾巴没合口」这一性质，不看具体断在哪几个字——"
      "断点随窗口起点偏移而变，性质才随写法而变）",
      "<" in _old25k and ">" not in _old25k.split("<", 1)[1] and "<" not in _e25k, _old25k)
for _in25k, _want25k in (("点击查看<img style=\"margi", "点击查看"),
                         ("登录可见</a><img src=\"a", "登录可见</a>"),
                         ("价格 <100 元", "价格 <100 元"),            # 正文里的裸 `<` 不是标签
                         ("a < b 登录后可见", "a < b 登录后可见"),
                         ("<a href=\"tel:13800138000", "<a href=\"tel:13800138000")):
    _got25k = (cdp_base.CONTACT_SLOT_DANGLING_RE.sub("", _in25k).strip() or _in25k)
    check(f"25k-3 尾巴小刀只切「没合口的标签」：{_in25k!r} → {_want25k!r}"
          f"（切错方向就会把真证据吃掉，那比半截词更糟）", _got25k == _want25k, _got25k)
_k25k4, _e25k4 = cdp_base.contact_slot_state('<p>联系电话</p><td><a href="tel:13800138000')
check("25k-4 整窗就是一截未合口标签时退回原文而不是编一句漂亮话：那一截里真有号码，"
      "它是 miss 档唯一的凭据（换成「看不到」这种话等于把号码从证据里弄没）",
      _k25k4 == "miss" and "13800138000" in _e25k4, f"{_k25k4} {_e25k4}")
_LONGWIN25k = '<p>联系电话：</p>' + "点击查看，厂家直营支持定制可开票据，" * 4
_k25kl, _e25kl = cdp_base.contact_slot_state(_LONGWIN25k)
check("25k-5 生成处不再管长度：窗口里多长就交多长（旧写法先按 24 字一刀把句子砍在词中间，"
      "而渲染那一刀本来就会把整句收到人眼读得动的长度——两把刀上限互相不知情，留一把就够）",
      _k25kl == "hidden" and len(_e25kl) > len("联系电话：→") + 24, f"{_k25kl}｜{len(_e25kl)} 字｜{_e25kl[:40]}")
_line25kl = cdp_base.nc_kinds_text({_k25kl: 1}, {_k25kl: _e25kl})
check("25k-6 但渲染那一刀必须在：整句有界、括号成对（一刀都不切就变回 120 字 × 六档没人读得动，"
      "而「只许一刀」被人拆成零刀同样是这一条要拦住的事）",
      len(_line25kl) < 160 and _line25kl.count("（") == _line25kl.count("）"), _line25kl)

# 32i 的真身跑在真源上（不另写一份离线副本：两份实现早晚分叉，这里要钉的就是门禁本体）
check("25k-7 两份镜像现网都绿：分档证据这一本账只挨了渲染那一刀（32i 本尊）",
      not _g32._nc_evidence_no_cut_problems("cdp_base.py", _ast.parse(_HTTP_SRC["cdp_base.py"]))
      and not _g32._nc_evidence_no_cut_problems("ali1688_crawler.py",
                                                _ast.parse(_SRC14["ali1688_crawler.py"])),
      " ｜ ".join((_g32._nc_evidence_no_cut_problems("cdp_base.py", _ast.parse(_HTTP_SRC["cdp_base.py"]))
                   + _g32._nc_evidence_no_cut_problems("ali1688_crawler.py",
                                                       _ast.parse(_SRC14["ali1688_crawler.py"]))
                   )[:2]))
for _lbl25k, _old25k_a, _new25k_a, _shape25k in (
        ("记账处退回预收口", "self._nc_evidence[kind] = evid.strip()",
         "self._nc_evidence[kind] = short_reason(evid, 60)", "short_reason"),
        ("生成处裸字符切", 'best, evid = kind, f"{m.group(0).strip()}→{tail}"',
         'best, evid = kind, f"{m.group(0).strip()}→{show[:24]}"', "切到 24 字"),
        ("换个名字再切（两步写法）", 'tail = CONTACT_SLOT_DANGLING_RE.sub("", show).strip() or show',
         "tail = show[:24]", "切到 24 字"),
        ("渲染那一刀被拆掉", 'one += f"（证据：{short_reason(ev[k], 46)}）"',
         'one += f"（证据：{ev[k]}）"', "一处都没收口")):
    _base25k = _HTTP_SRC["cdp_base.py"]
    _mut25k = _base25k.replace(_old25k_a, _new25k_a, 1)
    check(f"25k-7- 「{_lbl25k}」这一形要在样本里真改得动（改不动就是拿旧样本喂判据，"
          f"下一条「必须响」就变成永真）", _mut25k != _base25k, _old25k_a[:60])
    _got25k7 = _g32._nc_evidence_no_cut_problems(_lbl25k, _ast.parse(_mut25k))
    check(f"25k-7== 「{_lbl25k}」必须响并点名形状（{_shape25k}）：只改一行的分叉也要接住",
          any(_shape25k in x for x in _got25k7), str([x.splitlines()[0][:70] for x in _got25k7]))
check("25k-7=== 120 字窗口不算收口（它有下界，算「取哪一段正文」）：误伤一次，"
      "下一次就有人把这一条改成永真",
      "25 字" not in "".join(_g32._nc_cut_shapes(
          _ast.parse("def f(page, m):\n    return page[m.end():m.end() + 120]\n"))),
      str(_g32._nc_cut_shapes(_ast.parse("def f(page, m):\n    return page[m.end():m.end() + 120]\n"))))
_c25._nc_kinds, _c25._nc_evidence, _c25._detail_html = {}, {}, ""
_c25.NO_DETAIL_EVIDENCE = "本站详情只取列表卡字段（靠列表明文组装）" * 4      # 长过旧那把 60 字的刀
_c25._note_no_contact()
check("25k-8 账上存原文：长过 60 字的声明不再被记账那一刀砍成「…」——旧写法先切 60、渲染再切 46，"
      "两把刀的上限互相不知情（把渲染上限调到 90 也照样只看到 60 那刀留下的省略号）",
      "…" not in _c25._nc_evidence["no_page"]
      and _c25._nc_evidence["no_page"] == _c25.NO_DETAIL_EVIDENCE,
      _c25._nc_evidence["no_page"][:70])
_line25k = cdp_base.nc_kinds_text({"no_page": 2}, dict(_c25._nc_evidence))
check("25k-9 渲染那一刀仍是唯一一刀：句子有界、括号成对（切在人眼读得动的长度上，不切在词中间）",
      len(_line25k) < 140 and _line25k.count("（") == _line25k.count("）"), _line25k)
del _c25.NO_DETAIL_EVIDENCE

# ── 25l 第 32j 条：拿标签列表当「关掉了吗／还剩几个」的证据时只能走严格口径，且关后要重查 ──
# 不另写一份离线副本：要钉的就是门禁本体（两份实现早晚分叉）。三份真源现网都得绿，
# 再逐一退回修复前的形状，每条都得响——判据看不见回归就等于没修。
_TAB_SRC25l = {}
for _rel25l in ("b2bcollect_build/shared_chrome.py", "b2bcollect_build/site_adapters.py",
                "riskbird_build/dual_session.py"):
    _p25l = _g32.ROOT / _rel25l
    _TAB_SRC25l[_rel25l] = (_p25l.read_text(encoding="utf-8", errors="replace")
                            if _p25l.is_file() else "")
check("25l-0 三份「关标签」的现场都读到了（读不到就是一份空视野，下面那句「都绿」没有任何含义）",
      all(_TAB_SRC25l.values()), str({k: len(v) for k, v in _TAB_SRC25l.items()}))
_got25l = [f"{_rel25l}：{x}" for _rel25l, _t25l in _TAB_SRC25l.items()
           for x in _g32._tab_list_strict_problems(_rel25l, _ast.parse(_t25l))]
check("25l-1 现网三份真源都绿（32j 本尊）：亲自发 /json/close 的函数会重查、重查走严格口径；"
      "也不误伤拿同一把手做展示的地方（`acquire_tab`/`set_all_windows_state` 仍是默认口径，"
      "它们只「看看现在挂着哪些」，不拿它下结论）", not _got25l,
      " ｜ ".join(x.splitlines()[0][:100] for x in _got25l))
for _lbl25l, _key25l, _old25l, _new25l, _shape25l in (
        ("重查退回默认口径（读不到回 [] → 「已经不在了」）", "b2bcollect_build/shared_chrome.py",
         'ids = {t.get("id") for t in list_page_targets(port, strict=True)}',
         'ids = {t.get("id") for t in list_page_targets(port)}', "默认口径"),
        ("重查拆掉（只信回执，第十二轮那句假警与风鸟那句「已清理」都长这样）",
         "b2bcollect_build/shared_chrome.py",
         'ids = {t.get("id") for t in list_page_targets(port, strict=True)}',
         "ids = set()", "从不回头重查"),
        ("携命名空间的写法也不许漏（site_adapters 那一腿）", "b2bcollect_build/site_adapters.py",
         "targets = sc.list_page_targets(self.debug_port, strict=True)",
         "targets = sc.list_page_targets(self.debug_port)", "默认口径"),
        ("开头那份枚举也不许当「已经干净」（风险：安静地少洗了一批窗口）",
         "riskbird_build/dual_session.py", "pages = list_page_targets(port, strict=True)",
         "pages = list_page_targets(port)", "默认口径")):
    _base25l = _TAB_SRC25l[_key25l]
    _mut25l = _base25l.replace(_old25l, _new25l, 1)
    check(f"25l-2- 「{_lbl25l}」要在样本里真改得动（改不动就是拿旧样本喂判据，下一条就是永真）",
          _mut25l != _base25l, _old25l[:70])
    _got25l2 = _g32._tab_list_strict_problems(_key25l, _ast.parse(_mut25l))
    check(f"25l-2== 「{_lbl25l}」必须响并点名形状（{_shape25l}）：只改一行的分叉也要接住",
          any(_shape25l in x for x in _got25l2),
          " ｜ ".join(x.splitlines()[0][:100] for x in _got25l2))
check("25l-3 只看调用实参里的字符串，不看注释：`site_adapters` 的 docstring 里就写着「/json/close "
      "关闭」，拿全文子串发判会把一个只写了注释的函数当成干这事的（误伤一次就有人改成永真）",
      not _g32._calls_tab_close(_ast.parse(
          'def f():\n    """改走 CDP /json/list 只读 URL、/json/close 关闭"""\n    return 1\n')),
      "docstring 里的 /json/close 被当成了关闭动作")
print("25l 第 32j 条（标签列表口径 + 关后重查）本尊跑在真源上，四种退回形状全响 ✓")

print("㉕ 无联系方式分档 + 砍单记名 + 借标签归还：三本账各自只有一个把手、并被四处读到；"
      "证据那句只挨渲染那一刀；标签列表当证据时口径不混、关后必重查 ✓")

# ── ㉖ 列表条目账：说不出「站方这一词回了几条」，就不许替读者挑处方（第 33 条） ──
# 由来（2026-11-16 第十二轮实机 中国制造网「窗帘导轨」）：那一词零净新增，日志与体检报告都写
# 「站方回的货有……该复核的是相关性口径/同族类目桥」。真去取那一页，站方自己写着「共找到 1 个」——
# 砍掉 1 家必然零产出，把相关性阈值改到天上也变不出货。账上只有「砍了几家」时，「这一词只回 1 条」
# 与「回 30 条全被砍」这两种**处方正好相反**的局只能念成同一句话，那句话就是谎。
# 本节钉四件事：①条目账走唯一把手、且是真翻页循环落下来的（26a）；②四档处方各归各位，同一砍单
# 数下回货 1 条与 30 条必须给不同的话（26b）；③档位住在 KW_THIN_SUPPLY_ITEMS、索引说明住在
# SEARCH_INDEX_NOTE，改常量话术就跟着改（26c/26d）；④构建前第 33 条硬门的本尊跑在真源上，
# 每一种退回形状都得响（26e）；外加站方自报那一格的取数口径（26f）。
from mic_crawler import MicCrawler as _Mic26                      # noqa: E402


class _Fix26(CdpSiteCrawler):
    """最小夹具站：翻页走基类**真循环**（`_iter_keyword`），只把取页/解析换成定好的样本。

    ENTRIES 每一项＝这一页我们解析到的条数；CLAIMS 每一项＝站方在这一页自报的总数。
    三本账（总账 / kw_items / kw_claimed）都必须由这一趟真循环记下来，夹具才不是在替注释作证。
    """
    PLATFORM_SLUG = "fix26"
    PLATFORM_NAME = "条目账夹具站"
    ENTRIES = (1,)
    CLAIMS = ()
    SEED = 0              # 预置「增量库已含」的条数：它们进不了产出，但照样进条目账
    THIN = None           # 实例级覆盖 KW_THIN_SUPPLY_ITEMS（None＝用类里那一档）
    INDEX_NOTE = None     # 实例级覆盖 SEARCH_INDEX_NOTE

    def _is_session_alive(self):
        return True

    def start_browser(self):
        return True

    def is_relevant(self, kw, text):
        return True                   # 早过滤这一刀由另一条夹具单独管，这里只验「详情复核」

    def build_search_url(self, kw, page):
        if page == 1 and self.SEED:
            # crawl() 一进门就把 _seen_members 清了，所以只能在第一页取 URL 时种回去
            self._seen_members |= {f"{self.PLATFORM_SLUG}:p1i{i}" for i in range(2, self.SEED + 2)}
        return (f"https://fix26.test/s?k={kw}&p={page}" if page <= len(self.ENTRIES) else "")

    def _goto(self, url):
        self._pg26 = getattr(self, "_pg26", 0) + 1
        return True

    def _html(self):
        idx = self._pg26 - 1
        n = self.CLAIMS[idx] if idx < len(self.CLAIMS) else None
        return f'共找到 <span class="red">{n}</span>个 公司' if n is not None else "这一页没写总数"

    def parse_list(self, html, kw):
        cnt = self.ENTRIES[self._pg26 - 1]
        return [{"公司名称": f"夹具厂{self._pg26}第{i}号有限公司",
                 "memberId": f"p{self._pg26}i{i}"} for i in range(1, cnt + 1)]

    def list_has_more(self, html, page):
        return page < len(self.ENTRIES)

    # 站方自报总数那一格直接借真源：夹具自己再抄一遍正则，就成了「夹具替自己的实现作证」
    parse_listed_total = _Mic26.parse_listed_total

    def crawl_detail(self, stub):
        rec = self._base_rec(stub)
        rec["公司名称"] = stub.get("公司名称", "")
        self._keep_detail("<html>这一页没有联系电话栏</html>")
        rec["状态"] = "类目不符(已过滤)"        # 全砍：让这一词落到零净新增那一档
        return rec


def _run26(entries=(1,), claims=(), seed=0, thin=None, note=None):
    c = _Fix26()
    c.incremental = c.resume = False
    c.ENTRIES, c.CLAIMS, c.SEED = entries, claims, seed
    c._pg26 = 0
    if thin is not None:
        c.KW_THIN_SUPPLY_ITEMS = thin
    if note is not None:
        c.SEARCH_INDEX_NOTE = note
    _e, lg = equip(c)
    c.crawl(["窗帘导轨"])
    return c, next((m for m in lg if "零净新增" in m), "")


_c26a, _why26a = _run26(entries=(1,), claims=(1,))
check("26a 真翻页循环把三本账一起落下来了（总账 + 我们解析到的分词账 + 站方自报账）",
      _c26a.stat_listed == 1 and _c26a.kw_items == {"窗帘导轨": 1}
      and _c26a.kw_claimed == {"窗帘导轨": 1},
      f"{[_c26a.stat_listed, _c26a.kw_items, _c26a.kw_claimed]}")
check("26a- stats() 把两本分词账透出（界面与体检器读的就是这一份，少一格那一处就回到只看砍单数）",
      _c26a.stats()["kw_items"] == {"窗帘导轨": 1}
      and _c26a.stats()["kw_claimed"] == {"窗帘导轨": 1},
      str({k: v for k, v in _c26a.stats().items() if k.startswith("kw_")})[:160])
_c26m, _ = _run26(entries=(5, 5), claims=(10, 10))
check("26a-2 站方每页报的都是**整个查询**的总数：按页取最大值而不是求和（三页 10 条不能记成 30）",
      _c26m.kw_claimed == {"窗帘导轨": 10} and _c26m.kw_items == {"窗帘导轨": 10},
      f"{[_c26m.kw_items, _c26m.kw_claimed]}")

# ① 个位数货源 → 换词/换索引；② 回货多且全被砍 → 相关性口径。两者砍单数都是 1，处方必须相反
check("26b-1 只回 1 条时那句必须说「换关键词或搜索索引」，不许把读者支使去改相关性阈值",
      "个位数" in _why26a and "该换的是关键词或搜索索引" in _why26a
      and "该复核的是相关性口径" not in _why26a, _why26a[:200])
_c26b, _why26b = _run26(entries=(30,), claims=(30,))
check("26b-2 同样砍 1 家、但回货 30 条时那句必须改口「该复核相关性口径」（两种局不许共用一句）",
      "累计回 30 条" in _why26b and "该复核的是相关性口径/同族类目桥" in _why26b
      and "个位数" not in _why26b, _why26b[:200])
check("26b-3 同一砍单数下两档处方真的分叉了（这一条是整个 ㉖ 节立项的那件事）",
      _why26a != _why26b and "变不出货" in _why26a and "链路通" in _why26b)
_c26c, _why26c = _run26(entries=(1,), claims=(30,))
check("26b-4 站方自报 30 条而我们只认出 1 条 → 这一档的锅在解析口径，既不是货源也不是相关性",
      "自报 30 条" in _why26c and "列表解析只认出 1 条" in _why26c
      and "该修的是列表卡片解析口径" in _why26c, _why26c[:200])


class _Fix26NoBook(_Fix26):
    """修复前那一形：这一路只往总账里 +=，分词账永远是空的。"""

    def _iter_keyword(self, kw):
        self.stat_pages += 1
        self.stat_listed += 5                       # 旧写法：只有全局数，答不了「这一词几条」
        self._note_early_skip(kw, "夹具厂1第1号有限公司")
        return iter(())


_c26d, _why26d = None, ""
_i26d = _Fix26NoBook()
_i26d.incremental = _i26d.resume = False
_i26d._pg26 = 0
_e26d, _lg26d = equip(_i26d)
_i26d.crawl(["窗帘导轨"])
_why26d = next((m for m in _lg26d if "零净新增" in m), "")
check("26b-5 条目账没记上时，那句必须先承认「说不出站方回了几条」并点名把手，"
      "而不是替读者挑一个可能完全相反的处方（说不出就报自己的缺账）",
      "列表条目账没记上" in _why26d and "_note_listed" in _why26d
      and "该复核的是相关性口径" not in _why26d and "个位数" not in _why26d, _why26d[:200])
check("26b-6 砍单名单跟数字一起给（「该复核」这句处方必须有材料：复核的人手上得有几家名字）",
      "夹具厂1第1号有限公司" in _why26d, _why26d[-90:])

# ── 26c 档位与索引说明都住在常量里：改常量话术就跟着改，句子里不许写死数字 ──
_, _why26c0 = _run26(entries=(1,), claims=(1,), thin=0)
check("26c 上限调到 0 时「只回 1 条」就不再被算作货源薄（档位真源是 KW_THIN_SUPPLY_ITEMS，"
      "不是句子内写死的数字）", "个位数" not in _why26c0 and "累计回 1 条" in _why26c0,
      _why26c0[:160])
_, _why26c1 = _run26(entries=(30,), claims=(30,), thin=100)
check("26c- 上限调到 100 时 30 条也被称为货源薄（同一档位表在两档之间挪边界，话术必须跟着换档）",
      "个位数货量" in _why26c1, _why26c1[:160])
_, _why26n = _run26(entries=(1,), claims=(1,), note="这一路搜的是公司目录而不是产品目录")
check("26c-2 站点写明 SEARCH_INDEX_NOTE 时那句要原样带上（告诉读者该换的是索引；基类默认空串时"
      "这一句一个字都不许编）", "公司目录" in _why26n, _why26n[:160])
check("26c-3 基类默认交回空串：不知道这一路是什么索引就不许说（说错索引比不说更害人）",
      cdp_base.CdpSiteCrawler.SEARCH_INDEX_NOTE == "",
      repr(cdp_base.CdpSiteCrawler.SEARCH_INDEX_NOTE)[:80])

# ── 26d 体检器的「薄/不薄」判定：拿没记账的数判案是反方向的谎 ──
check("26d 两条腿读的是同一本账（体检器与归因句两种口径时，报告与日志会各说一句相反的处方）",
      _plc25._kw_supply({"kw_items": {"k": 1}, "kw_claimed": {"k": 30}}, "k") == (1, 30)
      and _plc25._kw_supply({}, "k") == (None, None),
      str(_plc25._kw_supply({"kw_items": {"k": 1}}, "k")))
check("26d- 账上没条目数时不许判成「货源薄」（宁可退回旧那句，也不把我们的解析漏说成平台没货）",
      _plc25._kw_thin({"kw_filtered": {"k": 1}}, "k") is False,
      "没记账被当成货源薄")
check("26d-2 自报 30 条而只认出 1 条是「解析漏」，不是「货源薄」：两档处方相反",
      _plc25._kw_thin({"kw_items": {"k": 1}, "kw_claimed": {"k": 30}}, "k") is False)
check("26d-3 两本账都是 1 条才是货源薄（上限同样取自 KW_THIN_SUPPLY_ITEMS，不另写一个数）",
      _plc25._kw_thin({"kw_items": {"k": 1}}, "k") is True
      and _plc25._kw_thin({"kw_items": {"k": cdp_base.CdpSiteCrawler.KW_THIN_SUPPLY_ITEMS}}, "k")
      is True
      and _plc25._kw_thin({"kw_items": {"k": cdp_base.CdpSiteCrawler.KW_THIN_SUPPLY_ITEMS + 1}},
                           "k") is False)

# ── 26e 站方自报那一格：真页形状、千分位、没那句话、以及「只报告不记账」 ──
_mic26 = _Mic26.__new__(_Mic26)
check("26e 站方写着「共找到 1 个」时交出 1（第十二轮实机那一页的取证形状）",
      _mic26.parse_listed_total('前缀共找到 <span class="red">1</span>个 <h1 class="red">'
                                '<strong>窗帘导轨</strong></h1>公司 后缀') == 1,
      str(_mic26.parse_listed_total('共找到 <span class="red">1</span>个公司')))
check("26e- 千分位不能截成 1（把 1,234 念成 1 就正好把「货源足」说成「货源薄」）",
      _mic26.parse_listed_total('共找到 <span class="red">1,234</span>个公司') == 1234,
      str(_mic26.parse_listed_total('共找到 <span class="red">1,234</span>个公司')))
check("26e-2 页面上没那句话时交回 None：说不出＝不知道，不许拿解析到的条数冒充站方自报",
      _mic26.parse_listed_total("这一页没写总数") is None
      and _mic26.parse_listed_total("") is None and _mic26.parse_listed_total(None) is None)
check("26e-3 有「共找到」但窗口里没有数字时也交回 None（不许从句子外面随便拓一个数）",
      _mic26.parse_listed_total("共找到 个公司" + " filler" * 40 + "9999") is None
      and _mic26.parse_listed_total("共找到 个公司 9 家") == 9,          # 窗口内才作数
      str(_mic26.parse_listed_total("共找到 个公司" + " filler" * 40 + "9999")))
_mic26.stat_listed, _mic26.kw_items, _mic26.kw_claimed = 0, {}, {}
_mic26.parse_listed_total('共找到 <span class="red">7</span>个公司')
check("26e-4 这一钩子只报告不记账（两处写＝两本账；记账统一住在 _note_listed）",
      _mic26.stat_listed == 0 and not _mic26.kw_items and not _mic26.kw_claimed,
      f"{[_mic26.stat_listed, _mic26.kw_items, _mic26.kw_claimed]}")
check("26e-5 中国制造网确实接上了这一格（没接的站只能念「这一词一共只回 N 条」那一档）",
      _Mic26.SEARCH_INDEX_NOTE and "companysearch" in _Mic26.SEARCH_INDEX_NOTE,
      _Mic26.SEARCH_INDEX_NOTE[:60])

print("㉖ 列表条目账：三本账由真翻页循环一起落；四档处方各归各位（1 条↔换词、30 条↔改口径、"
      "自报>解析↔修解析、没记账↔承认说不出）；档位与索引说明都住在常量里 ✓")

# ── 26g 机器判据：直接跑构建前第 33 条硬门的本尊，每一种退回形状都必须响 ──
_g33 = _g32 if _g32 is not None else _load_gate31()[0]
check("26g 门禁脚本可加载（加载不到就等于离线这一侧对条目账失明）", _g33 is not None, _g32err)
if _g33 is not None:
    _P33 = _g33.check_listing_accountability()
    check("26g0 现网真源上第 33 条全绿（下面每一档反向对照都拿它当对照面）", not _P33,
          " ｜ ".join(x.splitlines()[0] for x in _P33[:4]))
    _F33 = _g33._runtime_reason_sources()
    check(f"26g0- 第 33 条的视野不是空集合（读了 {len(_F33)} 份交付链源码）",
          len(_F33) > 40, str(len(_F33)))

    def _mut33(muts, extra=None):
        """把真源改回修复前的形状再让门禁本尊判一次（锚点对不上就当场响，不许拿旧样本喂判据）。

        `extra` 是给「整段删除/注入一段代码」那种形状留的口子（按行删、插函数体，replace 做不了）。
        """
        f = dict(_F33)
        for rel, old, new in muts:
            hit = False
            for p in list(f):
                if str(p).endswith(rel):
                    assert old in f[p], f"26g 夹具锚点对不上真源（{rel}）：{old[:70]}"
                    f[p] = f[p].replace(old, new)
                    hit = True
            assert hit, f"26g 视野里没有 {rel}"
        if extra:
            extra(f)
        return _g33.check_listing_accountability(f)

    _DROP_ANCHOR = lambda f: [f.__setitem__(
        p, re.sub(r"^\s*self\.kw_filtered = \{\}\n", "", f[p], flags=re.M))
        for p in list(f) if "self.kw_filtered = {}" in f[p]]
    _ADD_BOOK = lambda f: [f.__setitem__(
        p, f[p].replace("    def _note_early_skip", "    def _note_probe(self, kw):\n"
                        "        self.kw_probe[kw] = 1\n        return 1\n\n"
                        "    def _note_early_skip", 1))
        for p in list(f) if "    def _note_early_skip" in f[p]]

    _MUT33 = {
        "某一站又自己 `+=` 总账（条目账长出第二个写点，分词账就永远是空的）":
            ([("b2bcollect_build/b2b168_crawler.py",
               "self._note_listed(kw, len(entries), self.parse_listed_total(html))",
               "self.stat_listed += len(entries)")], "第二个写点"),
        "把手只记总账（分词账那一格被摘掉，归因句照样说不出货量）":
            ([("b2bcollect_build/cdp_base.py",
               "self.kw_items[key] = self.kw_items.get(key, 0) + n", "pass")], "只记总账"),
        "两份镜像把手行为分叉（1688 那一包给出另一种归因）":
            ([("ali1688_build/ali1688_crawler.py",
               "self.kw_claimed[key] = max(self.kw_claimed.get(key, 0), c)",
               "self.kw_claimed[key] = c")], "行为不同"),
        "归因句不读条目账（记了不念，两种局又念成同一句）":
            ([("b2bcollect_build/cdp_base.py", "items = self.kw_items.get(kw)",
               "items = None")], "记了不念"),
        "清零点漏一本（上一轮的回货量冒充本轮证据）":
            ([("b2bcollect_build/cdp_base.py",
               "        self.kw_items = {}\n        self.kw_claimed = {}",
               "        self.kw_claimed = {}")], "却没清 kw_items"),
        "新加一本逐词账却从来不清":
            ([], _ADD_BOOK, "没有任何清零点"),
        "拿 getattr 的默认值读总账（缺账被盖成「这本账是 0」）":
            ([("ali1688_build/ali1688_crawler.py",
               'if self._page_fail and not self.stat_listed:',
               'if self._page_fail and not getattr(self, "stat_listed", 0):')], "兜了默认值"),
        "site_adapters 不再透出条目账（界面那一处回到只看砍单数）":
            ([("b2bcollect_build/site_adapters.py",
               '"kw_items": dict(self.kw_items), "kw_claimed": dict(self.kw_claimed),',
               "")], "真取用"),
        "体检器把键名改掉（读不到就当没这本账）":
            ([("debug/platform_live_check.py", 'st.get("kw_items")', 'st.get("kw_items_x")')],
             "真取用"),
        "钩子自己动了条目账（两处写＝两本账）":
            ([("b2bcollect_build/mic_crawler.py", '        i = (html or "").find(LISTED_TOTAL_MARK)',
               '        self.stat_listed += 0\n        i = (html or "").find(LISTED_TOTAL_MARK)')],
             "只许**报告**"),
        "四档处方合回同一顶帽子（旧那句谎的机器形状：句子还在、只是两档说了同一件事）":
            ([("b2bcollect_build/cdp_base.py", "该换的是关键词或搜索索引",
               "该复核的是相关性口径")], None, "再也没人说这几句处方"),
        "上限写死成数字（档位表从常量跳回句子里，下一档改常量就听不见了）":
            ([("b2bcollect_build/cdp_base.py", "if items <= self.KW_THIN_SUPPLY_ITEMS:",
               "if items <= 3:")], None, "KW_THIN_SUPPLY_ITEMS"),
    }
    for _l33, _v33 in _MUT33.items():
        _m33, _x33, _want33 = _v33 if len(_v33) == 3 else (_v33[0], None, _v33[1])
        _got33 = _mut33(_m33, _x33)
        check(f"26g- 「{_l33}」必须响并点名「{_want33}」", any(_want33 in x for x in _got33),
              " ｜ ".join(x.splitlines()[0][:110] for x in _got33) or "判据没响＝这一形今天没人管")
    _got33 = _mut33([], _DROP_ANCHOR)
    check("26g- 「kw_filtered 的清零点被整处删掉」必须响（锚不见了不等于账干净，那一形最安静）",
          any("没有一处把" in x or "没有任何清零点" in x for x in _got33),
          " ｜ ".join(x.splitlines()[0][:110] for x in _got33) or "判据没响")

    def _drop_zero(f):
        for p in list(f):
            if "    def _kw_zero_reason(self, kw):" in f[p]:
                i = f[p].index("    def _kw_zero_reason(self, kw):")
                j = f[p].index("    def ", i + 10)
                f[p] = f[p][:i] + f[p][j:]

    _got33 = _mut33([], _drop_zero)
    check("26g- 「归因把手被整份删掉」必须响：上面每一档都是「函数不在就返 []」的形状，"
          "安静放行等于把门拆了", any("没有 `_kw_zero_reason`" in x for x in _got33),
          " ｜ ".join(x.splitlines()[0][:110] for x in _got33) or "判据没响")
    _got33 = _mut33([("b2bcollect_build/cdp_base.py",
                      "_why = self._kw_zero_reason(kw)", "_why = \"\"")])
    check("26g- 「定义了却从不叫」也必须响（四档处方写好了没人念，用户那一行还是旧那句）",
          any("从没有被调用" in x for x in _got33),
          " ｜ ".join(x.splitlines()[0][:110] for x in _got33) or "判据没响")
    _got33 = _g33.check_listing_accountability({})
    check("26g- 空视野不许绿（读不到源码时「每一路都记账了」这句话没有任何含义）",
          bool(_got33) and any("一份源码都没读到" in x for x in _got33), str(_got33)[:120])
    _got33 = _g33.check_listing_accountability(
        {"x.py": "def f():\n    x = (\n"})
    check("26g- 解析不过的那一份按不合格算，不许安静跳过", bool(_got33), str(_got33)[:120])
    check("26g- 记账把手与总账同名才罚：`stat_listed_x` 那种改名累加不该被当成第二把刀"
          "（误伤一次就有人把判据改成永真）",
          not _g33._listed_write_problems("x.py", _g33._listed_facts(
              "x.py", "class C:\n    def f(self):\n        self.stat_listed_x += 1\n")))
print("26g 第 33 条本尊跑在真源上：真树全绿 + 十四种退回形状各响 ✓")

print("㉗ 第 33 条（列表条目账）：门禁本尊与四档处方在离线这一侧对拍过 ✓")

# ── ㉘ 搜索参数 GBK 口径：三个渠道的 keywords/province 都要按站方 GBK 编码 ────
# 2026-11-17 实机 A/B 实锤（debug/1688_gbk_main_probe.html vs 1688_utf8_main_probe.html）：
# s.1688.com 服务端按 GBK 解 query 参数——UTF-8 的「太阳能路灯」被解成 mojibake 后
# 拿去搜，60 张卡片全是无关推荐流（关键词在页面 0 命中，member_id 埋点照样 120 处）；
# GBK 编码后同一页 422 次命中、卡片全相关。旧 quote()（UTF-8）等于每轮都在搜乱码词，
# 是 1688 产出「时有时无、质量差、砍单率极高」的更上游根因——三处渠道与省份后缀
# 一起锁死在本节，谁改回 UTF-8 谁当场响。
_gbk = _a1688._urlencode_gbk
check("㉘a 关键词按 GBK 编码（「太阳能路灯」→ %CC%AB%D1%F4%C4%DC%C2%B7%B5%C6）",
      _gbk("太阳能路灯") == "%CC%AB%D1%F4%C4%DC%C2%B7%B5%C6", _gbk("太阳能路灯"))
check("㉘a- 绝不混入 UTF-8 形态（UTF-8 的「太」首字节 %E5%A4）",
      "%E5%A4" not in _gbk("太阳能路灯"), _gbk("太阳能路灯"))
check("㉘b GBK 编不出的生僻字整词回退 UTF-8（不抛异常、不半词）",
      bool(_gbk(chr(0x20000))) and _gbk(chr(0x20000)) == quote(chr(0x20000)))
_src_gbk = ((Path(__file__).resolve().parents[1] / "ali1688_build" / "ali1688_crawler.py")
            .read_text(encoding="utf-8"))
check("㉘c 三个渠道的 keywords 都用 _urlencode_gbk（漏一处＝那一路搜乱码词）",
      _src_gbk.count("kw=_urlencode_gbk(kw)") == 3
      and "kw=quote(kw)" not in _src_gbk,
      f"count={_src_gbk.count('kw=_urlencode_gbk(kw)')}")
check("㉘d 省份后缀同口径（UTF-8 省名被解成 mojibake 后永远匹配不到省）",
      "province={_urlencode_gbk(pv)}" in _src_gbk
      and "province={quote(pv)}" not in _src_gbk)
_ns28 = types.SimpleNamespace(province="广东")
_sfx28 = _a1688.Ali1688Crawler._province_suffix(_ns28)
check("㉘e 省份后缀出 GBK 形态（「广东」→ %B9%E3%B6%AB）",
      "%B9%E3%B6%AB" in _sfx28, _sfx28)
_ns28.province = ""
check("㉘e- 未选省返回空串（防回退：空值不许带出参数名）",
      _a1688.Ali1688Crawler._province_suffix(_ns28) == "")
_ns28.province = "广东,江苏"
check("㉘e- 多省逗号串放弃参数直筛（退数据层过滤的旧契约不变）",
      _a1688.Ali1688Crawler._province_suffix(_ns28) == "")
print("㉘ 搜索参数 GBK 口径：三渠道 keywords + 省份后缀全部锁死在站方编码上 ✓")

# ── ㉙ 可见验证弹层探测（腾讯 captcha overlay）：企知道 175 家被误丢的根因 ────
# 2026-11-17 实机实锤：企知道上线 tencent-captcha-dy 弹层，它是 DOM overlay——
# URL/标题/正文长度全不变，原有三层判据全漏；搜索页打开但结果区被挡，
# 备用方案判「无结果」→ MISSING → 一轮名单 175 家被丢（实机 09:14：183 家仅成 8）。
# 修复：block_guard 第 4 层只认精确容器 class + 可见性尺寸闸；企知道 _detect_block
# 叠加 + _get_hash_via_search 判「无结果」前先查弹层（有 → 等人工过码，绝不记
# 「查不到」）。实机四标签对照（共享 Chrome）：企知道命中、爱企查/1688/风鸟全干净。
import block_guard as _bg
_js29 = _bg.CAPTCHA_OVERLAY_JS
check("㉙a 只认精确容器锚点（__warp/__full-header，绝不文本 contains）",
      "tencent-captcha-dy__warp" in _js29 and "tencent-captcha-dy__full-header" in _js29
      and "innerText" not in _js29)
check("㉙a- 可见性闸齐全（getClientRects 非空 + 尺寸 100×50）",
      "getClientRects" in _js29 and "offsetWidth>=100" in _js29
      and "offsetHeight>=50" in _js29)
_logs29 = []
_drv29 = types.SimpleNamespace(execute_script=lambda expr: _logs29.append(expr) or "腾讯安全验证弹层（请在浏览器里完成验证）")
check("㉙b detect_overlay_selenium 透传本尊 JS 并回传原因串",
      _bg.detect_overlay_selenium(_drv29) == "腾讯安全验证弹层（请在浏览器里完成验证）"
      and _logs29 and _logs29[0] == _js29)
check("㉙b- driver=None/执行异常都安静返空（探测层绝不把链路问题变成误报）",
      _bg.detect_overlay_selenium(None) == ""
      and _bg.detect_overlay_selenium(types.SimpleNamespace(execute_script=lambda e: (_ for _ in ()).throw(RuntimeError("x")))) == "")
check("㉙c detect_overlay_cdp 同一套 JS（爱企查裸 CDP 同判据）",
      _bg.detect_overlay_cdp(lambda e: "腾讯安全验证弹层（请在浏览器里完成验证）")
      == "腾讯安全验证弹层（请在浏览器里完成验证）"
      and _bg.detect_overlay_cdp(None) == "")
_qzd29 = ((Path(__file__).resolve().parents[1] / "riskbird_build" / "qizhidao_crawler.py")
          .read_text(encoding="utf-8"))
check("㉙d 企知道 _detect_block 叠加了弹层层（漏了它弹层挡住的页照样被读空）",
      "detect_overlay_selenium" in _qzd29.split("def _wait_unblock")[0])
check("㉙d- _get_hash_via_search 判「无结果」前先查弹层：有就等人工过码重找，"
      "等不到回 None（可重采）——「查不到」只许发生在确认无弹层之后",
      "detect_overlay_selenium" in _qzd29.split("def _get_hash_via_search")[1]
      and "_wait_unblock" in _qzd29.split("def _get_hash_via_search")[1]
      and "return None" in _qzd29.split("def _get_hash_via_search")[1])
check("㉙d- 风鸟/爱企查的弹层探测能力就位（block_guard 第 4 层是三站共用的）",
      callable(_bg.detect_overlay_selenium) and callable(_bg.detect_overlay_cdp)
      and (Path(__file__).resolve().parents[1] / "riskbird_build" / "block_guard.py").exists())
print("㉙ 可见验证弹层：精确锚点+可见闸锁定；企知道两处接线防回退；三站共用 ✓")

# ── ㉚ 边采边补（功能④并行化）：采集进行中就补，收尾退化为兜底 ─────
# 用户口径：「有些平台采集很慢，其他平台都采集完了，它还只采了一部分」——旧链路
# 把官网回采挂在 crawl() 收尾，采集慢的那一路直接拖住补采起点。并行化 = 生产者
# （采集线程只管产出，记录 append 后不再改，与消费者天然无并发写）- 消费者
# （独立线程按节拍扫 live 视图增量回采缺联系方式的行），收尾全量轮保留为兜底
# （补过的行带「官网回采」留痕，needs_retry 直接跳过，行为基线不变）。
import parallel_session as _ps_mod                                        # noqa: E402
import time as _time30                                                   # noqa: E402
_src30 = ((Path(__file__).resolve().parents[1] / "b2bcollect_build"
           / "parallel_session.py").read_text(encoding="utf-8"))
check("㉚a crawl 启动消费者：在各路线程开跑后、join 等待前",
      "self._start_enrich_worker()" in _src30
      and _src30.index("self._start_enrich_worker()")
      > _src30.index("name=f\"crawl-{slug}\"")
      and _src30.index("self._start_enrich_worker()")
      < _src30.index("for t in self._threads.values():"))
check("㉚b crawl 收尾先停消费者再算总账（与收尾全量轮串行）",
      "self._stop_enrich_worker()" in _src30
      and _src30.index("self._stop_enrich_worker()")
      < _src30.index("total = sum(len(c.results) for c in actives)"))


def _body30(src, name):
    """按方法定位函数体（旧写法用 `split("def X")[1].split("def Y")` 借住了两个函数
    中间只有彼此这个巧合；现在中间多了一层方法，靠名字取下一个 `def ` 才不会把隔壁
    方法的代码读进本轮要断言的那一段。"""
    import re as _re30
    m = _re30.search(r"\n    def " + name + r"\b", src)
    if not m:
        return ""
    nxt = _re30.search(r"\n    (?:@|def )", src[m.end():])
    return src[m.end(): m.end() + nxt.start()] if nxt else src[m.end():]


def _code30(body):
    """剔掉函数自己的 docstring，只留下能跑的代码。

    docstring 里常常**引述**被废弃的旧口径（比如「旧行为是 only_missing=True」），
    拿整段文本去做「旧口径不许再出现」型断言，就会把「注释解释了为什么不用它」
    误判成「代码又回退了」——2026-11-18 ㉚c 就是这样红的。"""
    i = body.find('"""')
    if i < 0:
        return body
    j = body.find('"""', i + 3)
    return body[:i] + (body[j + 3:] if j >= 0 else "")


_hop30 = _code30(_body30(_src30, "_enrich_single_hop"))
_wf30 = _code30(_body30(_src30, "_run_waterfall"))
_er30 = _code30(_body30(_src30, "_enrich_round"))
check("㉚c 消费者每一轮都走**完整瀑布**（2026-11-17 整轮实机改口径：旧写法把后台钉在"
      " only_missing=True 那一档，582 行里只有 16 行属于它，42 轮只碰 18 个官网、净新增 1 个值；"
      "工商队列也因此整轮空转，收尾才投 474 家×3 路＝1432 家次永远查不完）",
      "self._run_waterfall(" in _er30 and "only_missing=True" not in _er30
      and "self._enrich_single_hop(" in _er30, _er30[:240])
check("㉚d 收尾叫停是温和收工：两条支路的 stop lambda 都只认用户级停止，不作废当轮增量"
      "（口径跟着实现搬到了 _run_waterfall/_enrich_single_hop，消费者只负责选走哪一条）",
      "stop=lambda: bool(self._user_stop)" in _wf30
      and "stop=lambda: bool(self._user_stop)" in _hop30
      and "_enrich_worker_stop" not in _wf30.split("stop=")[1].split("limit=")[0],
      "stop 口径回退成认后台停止位（当轮增量会被整轮作废）")
check("㉚e enrich_websites 与消费者互斥（大锁包住两条支路与报表）",
      "with self._enrich_mutex:" in _src30.split("def enrich_websites")[1]
      .split("def enrich_summary")[0])
check("㉚f 收尾总账单列（后台的功劳不被收尾 cached 计数吞掉）",
      "边采边补总账" in _src30 and "_enrich_stream" in _src30)

_logs30 = []
_ps30 = ParallelSession()
_ps30._log = lambda m="": _logs30.append(str(m))
# 未勾开关不启动（旧关闭语义原样）
_ps30.site_enrich = False
_ps30._start_enrich_worker()
check("㉚g 未勾「采集后回采」不启动消费者", _ps30._enrich_worker is None)
# 勾选后启动 + 重复启动不叠加
_ps30.site_enrich = True
_ps30._start_enrich_worker()
_w30 = _ps30._enrich_worker
check("㉚h 勾选后消费者启动且账目初始化（账里必须有 queued 那一栏：工商投队列的功劳不能被报成 0）",
      _w30 is not None and _w30.is_alive()
      and _ps30._enrich_stream == {"rounds": 0, "sites": 0, "ok": 0, "new_values": 0,
                                   "queued": 0, "reasons": {}}
      and any("边采边补线程已启动" in m for m in _logs30))
_w_before = _ps30._enrich_worker
_ps30._start_enrich_worker()
check("㉚i 已有活消费者时重复启动不叠加", _ps30._enrich_worker is _w_before)

# 轮次行为：monkeypatch 官网回采引擎，验参数与账目
_calls30 = {}
def _fake_enrich30(records, log=None, workers=6, budget=900, timeout=None,
                   stop=None, limit=1500, only_missing=False, resolver=None):
    _calls30["kw"] = {"workers": workers, "budget": budget,
                      "only_missing": only_missing, "limit": limit,
                      "stop_result": (stop() if callable(stop) else None),
                      "n": len(records)}
    return {"sites": 3, "ok": 2, "new_values": 5, "reasons": {"超时": 1}}
class _WcStub30:
    """只替 `enrich_records` 这一个把手（验参数与账目）：排队口径那几步
    （pick_site / _has_any / needs_retry）仍走真实现。旧替身只定义了一个方法，
    而 `_enrich_round` 现在把活派给 `_enrich_single_hop`——那一层要先过排队闸，
    替身不代理就会在「缺方法」的 AttributeError 上假失败，把要验的引擎调用绕过去了。"""
    enrich_records = staticmethod(_fake_enrich30)

    def __getattr__(self, name):
        import website_contact as _wc30
        return getattr(_wc30, name)

_orig_wc30 = _ps_mod._site_wc
_ps_mod._site_wc = _WcStub30()
try:
    # 记录里必须带官网：`_enrich_round` 现在把活派给 `_enrich_single_hop`，而它在
    # 「一行可回采官网都没有」时直接早退（不花预算）——没官网的样本根本走不到引擎那一步，
    # 拿它验参数就是在验一个没被调用的替身。
    _ps30._all_live_records = lambda: [{"公司名称": "甲公司", "公司网址": "https://acme.example.com"},
                                       {"公司名称": "乙公司", "公司网址": "https://beta.example.com"}]
    _logs30.clear()
    _ps30._enrich_round()
    check("㉚j 未勾瀑布时的轮次参数（only_missing 不再钉住——只补「四项全空」那一档会把后台饿死；"
          "预算取两值较小者；stop 只认用户级停止）",
          _calls30["kw"]["only_missing"] is False
          and _calls30["kw"]["budget"] == min(_ps_mod.ENRICH_STREAM_BUDGET,
                                              _ps30.site_enrich_budget)
          and _calls30["kw"]["stop_result"] is False
          and _calls30["kw"]["n"] == 2)
    check("㉚k 账目累计 + 有产出才打日志",
          _ps30._enrich_stream["rounds"] == 1 and _ps30._enrich_stream["sites"] == 3
          and _ps30._enrich_stream["ok"] == 2 and _ps30._enrich_stream["new_values"] == 5
          and _ps30._enrich_stream["reasons"] == {"超时": 1}
          and any("边采边补第 1 轮" in m for m in _logs30))
    # 锁被占（手动补采在跑）时让路
    _calls30.clear()
    _ps30._enrich_mutex.acquire()
    try:
        _ps30._enrich_round()
    finally:
        _ps30._enrich_mutex.release()
    check("㉚l 手动补采持锁时消费者让路（绝不两路并发写同一条记录）",
          _calls30.get("kw") is None and _ps30._enrich_stream["rounds"] == 1)
    # 单轮异常不冒泡
    class _WcBoom30(_WcStub30):
        def enrich_records(self, *a, **kw):
            raise RuntimeError("boom")
    _ps_mod._site_wc = _WcBoom30()
    _logs30.clear()
    _ps30._enrich_round()
    check("㉚m 消费者单轮异常不冒泡且日志里看得见（两层兜底：本轮层与引擎层任一层报都行，"
          "但绝不允许只静默丢掉一轮）",
          any("已跳过，不影响已采数据" in m and "boom" in m for m in _logs30)
          and _ps30._enrich_stream["rounds"] == 1)
finally:
    _ps_mod._site_wc = _orig_wc30
# 本轮无数据时空转
_calls30.clear()
_ps30._all_live_records = lambda: []
_ps30._enrich_round()
check("㉚n 本轮无数据时消费者空转（不算轮次）",
      _calls30.get("kw") is None and _ps30._enrich_stream["rounds"] == 1)

# 停止语义
_ps30._user_stop = True
_t30 = _time30.time()
_ps30._enrich_worker_loop()          # 直接调：立即 return，不睡满节拍
check("㉚o 用户级停止使消费者立即收手", _time30.time() - _t30 < 2.0)
_ps30._user_stop = False
_t30 = _time30.time()
_ps30._stop_enrich_worker()          # worker 在首轮 sleep 中：置位后 1 秒粒度退出
check("㉚p 收尾 join：消费者退出且登记清空（与收尾补采严格串行）",
      _ps30._enrich_worker is None and _time30.time() - _t30 < 5.0)

# ㉚s 会话层**每一个**「self.某方法(...)」调用的参数名都必须是实现接得住的（AST 扫全模块）。
# 为什么要这一条而不是在替身里手写一张完整形参表：那张表就是第二套编号。本轮把
# ``master_fetch`` 加进 ``_run_waterfall`` 的调用点，替身没跟上，TypeError 被
# ``_enrich_round`` 那层「任何异常都不许冒泡」的兜底吞成「这一轮没跑瀑布」——判据当场红，
# 红的形状却完全指不到真凶（看起来像业务参数错了，实则接线错了一个名字）。同一类错在
# 任何一处调用点都长同一个样子：日志安静、数据里看不出来，所以扫的是整个模块而不是本轮那一处。
def _arg_gate_30(source, cls, cls_name):
    node = next(n for n in _ast.parse(source).body
                if isinstance(n, _ast.ClassDef) and n.name == cls_name)
    bad = []
    for n in _ast.walk(node):
        f = n.func if isinstance(n, _ast.Call) else None
        if not (isinstance(f, _ast.Attribute) and isinstance(f.value, _ast.Name)
                and f.value.id == "self"):
            continue
        target = getattr(cls, f.attr, None)
        fn = getattr(target, "__func__", target)          # classmethod/staticmethod
        if not inspect.isfunction(fn):
            continue                                       # 属性、缺失名都不算接线
        params = inspect.signature(fn).parameters
        if any(p.kind is p.VAR_KEYWORD for p in params.values()):
            continue                                       # 接得住任意名字的跳过
        if any(isinstance(a, _ast.Starred) for a in n.args):
            continue                                       # *args 拆包看不出数量
        kw = [k.arg for k in n.keywords if k.arg]
        pos = len(n.args)
        room = sum(1 for p in params.values()
                   if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD))
        if params and list(params)[0] in ("self", "cls"):
            room -= 1          # 调用点不写 self，形参表里却算了它一个
        if any(p.kind is p.VAR_POSITIONAL for p in params.values()):
            room = pos
        if [k for k in kw if k not in params] or pos > room:
            bad.append((f.attr, n.lineno, kw, pos, room))
    return bad


_arg_bad_30 = _arg_gate_30(inspect.getsource(_ps_mod), _ps_mod.ParallelSession,
                           "ParallelSession")
check("㉚s 会话层每一个 self 方法调用的参数名与个数都被实现接住（AST 扫全模块）",
      not _arg_bad_30, f"错配：{_arg_bad_30[:3]}｜名字打错一个字母在生产里就是 TypeError，"
                       "而它会安静地落在一个兜底 try 里")
# ㉚s- 这条门自己的牙。为什么不能拿变异台验它：变异台改的是**内存里的函数对象**，
# 而这一条读的是**文件源码**，两者根本不在一份字上（拿它去对拍只能报「没红」，那是假案）。
# 静态门的牙只能这么验：喂它一份注定有错的源码，要求它报得出那两种错，而且不许多报——
# 带 **kw 的方法接得住任意名字，误报一次下次就有人把它关掉。
_gate_src_30 = (
    "class Fake:\n"
    "    def take(self, a, b=None, *, verbose=True): pass\n"
    "    def wide(self, *args, **kw): pass\n"
    "    def use(self):\n"
    "        self.take(1, b=2, wrong=3)\n"      # 名字接不住
    "        self.take(1, 2, 3)\n"              # 个数超出
    "        self.wide(anything=1)\n"           # 带 **kw：不许报
    "        self.take(1, verbose=False)\n"     # 正向对照：不许报
)
_gate_ns_30 = {}
exec(compile(_gate_src_30, "<arg_gate_fixture>", "exec"), _gate_ns_30)
_gate_bad_30 = _arg_gate_30(_gate_src_30, _gate_ns_30["Fake"], "Fake")
check("㉚s- 这条门咬得动夹具里那两种错、也不多咬（静态门的牙只能这么验）",
      [x[0] for x in _gate_bad_30] == ["take", "take"],
      f"实报：{_gate_bad_30}｜只许那两处红，带 **kw 的与正向对照都不许被误报")

# ㉚q/㉚q2/㉚q3/㉚r 勾上瀑布的那一条支路（本轮功能背书的正题：后台每一轮都走完整瀑布，而不是
# 只跑单跳）：_run_waterfall 在实例上接替身，验「派到了哪一条、参数对不对、账上不上」。
_wf_seen = {}
_wf_sig30 = inspect.signature(_ps_mod.ParallelSession._run_waterfall)


def _fake_wf30(reason, records, **kw):
    """替身按**实现自己的签名**收参数（不在这里重抄一张形参表）。

    重抄的那张表就是上面 ㉚s 记过账的那个坑；而只收 ``**kw`` 又会把「调用点写了实现接不住
    的名字」吞成无痕，所以这里自己先对一遍签名，再把参数**按实现的默认值**补齐——判据读到
    的是「这一轮真正生效的参数」，不是替身里那一套我猜的默认值。
    """
    bad = [k for k in kw if k not in _wf_sig30.parameters]
    if bad:
        raise TypeError(f"调用点传了 _run_waterfall 接不住的关键字：{bad}")
    bound = _wf_sig30.bind(_ps30, reason, records, **kw)
    bound.apply_defaults()
    _wf_seen.update({k: v for k, v in bound.arguments.items()
                     if k not in ("self", "records")})
    _wf_seen["n"] = len(records)
    return {"sites": 3, "ok": 2, "new_values": 5, "reasons": {"超时": 1},
            "registry": {"queued": 7}}


_ps30.waterfall = True
_ps30._run_waterfall = _fake_wf30
_ps30._all_live_records = lambda: [{"公司名称": "甲公司", "公司网址": "https://acme.example.com"},
                                   {"公司名称": "乙公司", "公司网址": "https://beta.example.com"}]
_calls30.clear()
_logs30.clear()
_ps30._enrich_round()
check("㉚q 勾瀑布时每一轮都走完整瀑布（不钉 only_missing、不逐行刷报表）",
      _calls30.get("kw") is None and _wf_seen.get("reason") == "边采边补"
      and _wf_seen.get("n") == 2 and _wf_seen.get("only_missing") is False
      and _wf_seen.get("verbose") is False
      and _wf_seen.get("budget") == min(_ps_mod.ENRICH_STREAM_BUDGET,
                                        _ps30.site_enrich_budget),
      f"生效参数：{ {k: v for k, v in _wf_seen.items() if k not in ('reason', 'n')} }"
      f"｜日志尾巴：{_logs30[-2:]}（这一路有兜底 try，接线错了只会体现在日志里）")
check("㉚r 工商队列投出去的家数上账且在日志里可见（旧写法只到收尾才投队列，"
      "采集那 45 分钟三路几乎无活可干）",
      _ps30._enrich_stream["queued"] == 7 and _ps30._enrich_stream["rounds"] == 2
      and any("工商队列新增 7 家" in m and "累计 7" in m for m in _logs30))
# ㉚q2/㉚q3 披露主数据那一次性全量拉取**由谁付**（f4f10 接进来后新增的口径）。
# 边采边补每 60 秒开一轮，绝不能为「顺手补」开一次两万四千多家、五十几秒的批量下载——
# 那一下花在后台路上既看不到也说不清，正是用户报的那次「鼠标转圈」。
# 但收尾那一路必须付这一次：两条都钉 False 的话主数据永远拉不下来，而「披露级整轮没值」
# 在日志里长得和「名录里没这家」一模一样，谁也不会去查文件根本不存在。
check("㉚q2 后台那一路不承当主数据全量拉取（master_fetch=False）",
      _wf_seen.get("master_fetch") is False,
      f"边采边补实传：{_wf_seen.get('master_fetch', '（没传，走模块默认＝允许拉）')}")
_wf_seen.clear()
_ps30.lead_export = False
_ps30.enrich_websites(reason="采集收尾", force=True)
check("㉚q3 收尾那一路不把 master_fetch 钉成 False：全量拉取必须有人付一次，"
      "否则披露级永远空转",
      _wf_seen.get("reason") == "采集收尾"
      and _wf_seen.get("master_fetch") is not False,
      f"收尾实传：{_wf_seen.get('master_fetch', '（没传，走模块默认＝允许拉）')}")
_ps30.waterfall = False
print("㉚ 边采边补：生产者-消费者接线齐全、互斥/温和收工/异常兜底/停止语义/两条支路全锁死 ✓")

print(f"\n全部 {ok} 项断言通过 ✅")
