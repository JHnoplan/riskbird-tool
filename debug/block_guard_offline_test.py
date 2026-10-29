# 离线自测：20261028 四项新需求
# ①入库口径：手机号/固话/邮箱/微信号全空的行剔除（见 quality_fix_offline_test 第 9 节）
# ②供应商网 gys 验证码/520 插页看门狗（识别→弹提醒→实测恢复→自动续采同一 URL）
# ③顺企网 429 + v.js 防采看门狗（插页绝不当「本页无数据」写库；抄 Cookie/降级取页）
# ④「开始新任务」复位：本次数据/账目/熔断计数/聚合进度归零，历史种子库保持不动
# 全程不联网、不启 Chrome：网络响应一律注入假会话，等待循环把 sleep 短路。
import sys, time, inspect
sys.path.insert(0, "/Users/jhnoplan/Desktop/PythonDemo/b2bcollect_build")

import cdp_base
from cdp_base import CdpSiteCrawler
from qihuo11467_crawler import Qihuo11467Crawler, HOME_URL
from gys_crawler import GysCrawler
from b2b168_crawler import B2b168Crawler
from aipage_crawler import AipageCrawler
from parallel_session import ParallelSession

ok = 0


def check(name, cond):
    global ok
    assert cond, f"FAIL: {name}"
    ok += 1
    print(f"  ✓ {name}")


class FakeResp:
    def __init__(self, status_code, text, url="https://example.com/p"):
        self.status_code = status_code
        self.text = text
        self.url = url


class FakeSession:
    """按脚本顺序吐响应；记录被请求过的 URL，用于断言探测的是「被拦那一页」。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def get(self, url, timeout=None, headers=None, allow_redirects=True, **kw):
        self.calls.append(url)
        if self.script:
            item = self.script.pop(0)
            return item if isinstance(item, FakeResp) else FakeResp(item, "")
        return FakeResp(200, "<html>" + "正常商铺内容" * 400 + "</html>", url)

    def close(self):
        pass


class Sig:
    def __init__(self):
        self.events = []

    def _make(self, name):
        outer = self

        class S:
            def emit(self, *a):
                outer.events.append((name, a[0] if a else ""))
        return S()

    def __getattr__(self, item):
        if item.startswith("__"):
            raise AttributeError(item)
        return self._make(item)


def watch(c, monkey=True):
    """收集 _emit 事件（不依赖 Qt 信号投递）。"""
    sig = Sig()
    c.signals = sig
    if monkey:
        c._emit = lambda name, *a: sig.events.append((name, a[0] if a else ""))
    return sig


def isolate(c):
    """测试用的假记录绝不能写进真实历史种子库（不可逆污染），当场封掉提交口。"""
    c._commit_to_store = lambda rec: None
    return c


# 顺企网实机取证样本：HTTP 429 + 70 字节 JS 挑战（requests 不执行 JS → 永远空页）
QIHUO_429 = FakeResp(429, '<script src="https://do.11467.com/v.js?v=rollback-20261027"></script>',
                     "https://b2b.11467.com/search/main?k=%E8%B7%AF%E7%81%AF")
# 供应商网实机取证样本：子域 HTTP 520 + 4055 字节「访问异常 - 供应商网」插页
GYS_520 = FakeResp(520, "<title>访问异常 - 供应商网</title>" + "请完成安全验证" * 30,
                   "https://newsunpv.gys.cn/contact.html")
NORMAL_200 = FakeResp(200, "<html><title>东莞某公司商铺</title>" + "主营产品 太阳能路灯 联系方式 " * 300
                      + "</html>", "https://b2b.11467.com/shop/x/contact.asp")

# ══════════════════════════════════════════════════════════
# 1) HTTP 防采判据：该认的必须认，不该误杀的绝不误杀
# ══════════════════════════════════════════════════════════
q = Qihuo11467Crawler()
watch(q)
r = q.http_block_reason(QIHUO_429.status_code, QIHUO_429.text, QIHUO_429.url)
check(f"顺企网 429 + v.js 挑战判定为防采插页（{r}）", bool(r) and "429" in r)
check("顺企网 429 + v.js 分诊为 captcha（JS 挑战在浏览器里执行一下就过，不该让人干等）",
      q._block_kind == "captcha")
check("命中时记下被拦那页作为恢复探测页", q._block_probe_url == QIHUO_429.url)

g = GysCrawler()
watch(g)
r2 = g.http_block_reason(GYS_520.status_code, GYS_520.text, GYS_520.url)
check(f"供应商网 520「访问异常」判定为插页（旧版漏判就是它）（{r2}）", bool(r2) and "520" in r2)
check("gys 同样记下探测页", g._block_probe_url == GYS_520.url)

check("200 正常大页不误判", q.http_block_reason(200, NORMAL_200.text, NORMAL_200.url) == "")
check("404 是商铺失效终态，绝不当风控（否则真无数据的站会白等 30 分钟）",
      q.http_block_reason(404, "<html>not found</html>", "https://x/y.asp") == "")
small = "<html><title>系统提示</title>请完成安全验证后继续访问</html>"
check("200 但体积极小 + 插页特征词照样判定", bool(q.http_block_reason(200, small, "https://x/z")))
big_with_word = "<html>" + ("正常公司简介 太阳能路灯 " * 500) + "安全验证" + "</html>"
check("大页面里内嵌「安全验证」JS 文案不误杀（旧教训：整页关键词匹配会误杀正常页）",
      q.http_block_reason(200, big_with_word) == "")
for code in (403, 405, 412, 429, 502, 503, 520, 521, 522, 523):
    check(f"HTTP {code} 在防采/限流判据内", bool(q.http_block_reason(code, "x" * 9000)))

# ══════════════════════════════════════════════════════════
# 2) 看门狗主循环：弹提醒 → 实测恢复 → 自动续采（不联网，patch sleep）
# ══════════════════════════════════════════════════════════
_real_sleep = time.sleep
cdp_base.time.sleep = lambda s: None
time.sleep = cdp_base.time.sleep
try:
    q = Qihuo11467Crawler()
    q.BLOCK_POLL = 1
    q.BLOCK_MAX_WAIT = 30
    sig = watch(q)
    probes = ["顺企网仍在防采/限流状态", ""]        # 第二轮探测＝用户已过验证
    seq = iter(probes)
    cleared = []
    res = q._wait_manual_unblock("HTTP 429（防采插页）", lambda: next(seq),
                                 on_cleared=lambda: cleared.append(1))
    names = [e[0] for e in sig.events]
    check("看门狗探到验证已解除后返回 True（调用方随即重采同一 URL）", res is True)
    check("被拦时立刻发 blocked_signal 弹提醒（不再静默空跑）", "blocked_signal" in names)
    check("解除时发 block_cleared_signal 收起提醒", "block_cleared_signal" in names)
    check("恢复回调 on_cleared 被触发（gys 用它复位 _http_disabled）", cleared == [1])
    check("提醒文案带上站点名（用户知道是哪一站）",
          any(e[0] == "blocked_signal" and "顺企网" in e[1] for e in sig.events))

    # 等待期间必须能响应停止，不然「停止」按钮会被看门狗吃掉
    q2 = Qihuo11467Crawler()
    q2.BLOCK_POLL = 1
    q2._stop_requested = True
    check("用户点停止时看门狗立即退出（不硬等满 30 分钟）",
          q2._wait_manual_unblock("HTTP 429", lambda: "still blocked") is False)

    # 熔断：短窗口内反复被拦→优雅收本轮，保护 IP（旧版会对着插页死磕到硬封）
    q3 = Qihuo11467Crawler()
    q3._block_times = [time.time()] * (q3._max_blocks - 1)
    sig3 = watch(q3)
    res3 = q3._wait_manual_unblock("HTTP 429（防采插页）", lambda: "still blocked")
    check("窗口内累计被拦达阈值→停掉本站本轮（不再浪费机会/扛封 IP）",
          res3 is False and q3._stop_requested is True)
    check("熔断时仍发一次提醒并写清原因", any(e[0] == "blocked_signal" for e in sig3.events))
    check("熔断后复位 _blocked（界面横幅不会一直挂着）", q3._blocked is False)

    # 措辞分型：HTTP 状态码拦下的是 IP 冷却，页面里没滑块，催用户找验证码只会白等
    q4 = Qihuo11467Crawler()
    q4.BLOCK_POLL = 1
    q4.BLOCK_MAX_WAIT = 125
    q4.signals = None
    logs4 = []
    q4._log = logs4.append
    q4._wait_manual_unblock("站点返回 HTTP 520（防采/限流插页）", lambda: "仍在限流")
    j4 = "\n".join(logs4)
    check("HTTP 限流提醒不再催用户手动过验证（改成 IP 冷却说法）",
          "IP 级限流/冷却" in j4 and "请在 Chrome 窗口里手动过验证" not in j4)
    check("等待期间日志写「仍在等站点放行」而不是「仍在等待验证」",
          "仍在等站点放行" in j4 and "仍在等待验证" not in j4)

    q5 = Qihuo11467Crawler()
    q5.BLOCK_POLL = 1
    q5.BLOCK_MAX_WAIT = 125
    q5.signals = None
    logs5 = []
    q5._log = logs5.append
    q5._wait_manual_unblock("页面弹出验证控件（#tencent-captcha）", lambda: "仍有控件")
    j5 = "\n".join(logs5)
    check("真验证码场景仍明确要求人工过滑块", "请在 Chrome 窗口里手动过验证" in j5
          and "仍在等待验证" in j5)
finally:
    cdp_base.time.sleep = _real_sleep
    time.sleep = _real_sleep

# ══════════════════════════════════════════════════════════
# 3) 恢复探测=能力探测：绝不把挑战页当「已通」谎报恢复
# ══════════════════════════════════════════════════════════
q = Qihuo11467Crawler()
watch(q)
q._block_reason = "HTTP 429（防采插页）"
q._block_probe_url = QIHUO_429.url
q._session = FakeSession([QIHUO_429])
check("HTTP 仍 429 时 probe_block 报「未解除」（探一次就谎报会空转刷接口）",
      bool(q.probe_block()))
check("顺企网探测走首页（429 是 IP 级限流，首页最轻最能代表恢复）",
      q._session.calls == [HOME_URL])

# 基类能力探测：纯 HTTP 站默认探「刚被拦那一页」（子类可换成首页，但都得真拿到数据）
base = CdpSiteCrawler()
base.NEEDS_BROWSER = False
base.BASE_URL = HOME_URL
base._block_reason = "HTTP 429（防采插页）"
base._block_probe_url = QIHUO_429.url
base._session = FakeSession([QIHUO_429])
check("基类探测优先打被拦那一页", bool(base.probe_block()) and base._session.calls == [QIHUO_429.url])

q._session = FakeSession([FakeResp(200, "挑战页", "https://do.11467.com/v.js")])
check("200 但正文过小仍算未解除（挑战页也是 200）", bool(q.probe_block()))

q._session = FakeSession([NORMAL_200])
check("真拿到 ≥2000 字节正常页才算解除", q.probe_block() == "")
check("解除时清掉 _block_reason", q._block_reason == "")
base._session = FakeSession([NORMAL_200])
base._block_reason = "HTTP 429（防采插页）"
check("基类需 ≥3000 字节正常页方判解除", base.probe_block() == "" and base._block_reason == "")

g = GysCrawler()
watch(g)
import gys_crawler as gm
_real_requests = gm.requests
gm_requests = []


class _FakeRequestsModule:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def get(self, url, headers=None, timeout=None, **kw):
        self.calls.append(url)
        item = self.script.pop(0) if self.script else FakeResp(520, "访问异常", url)
        return item if isinstance(item, FakeResp) else FakeResp(item, "")


try:
    # A) IP 侧恢复：HTTP 直取被拦那一页就能拿到正常页
    fake = _FakeRequestsModule([NORMAL_200])
    gm.requests = fake
    g._block_probe_url = GYS_520.url
    g._block_reason = "站点返回 HTTP 520（防采/限流插页）"
    g._http_disabled = True
    g._http_fails = 3
    nav = []
    g._cdp_navigate = lambda *a, **k: nav.append(1)
    g._gys_blocked = lambda: True
    res = g.probe_block()
    check("gys 探测优先 HTTP 直取被拦那页", res == "" and fake.calls == [GYS_520.url])
    check("gys 探测绝不主动导航（不能把用户正拖到一半的滑块刷掉）", nav == [])
    check("恢复后主链路切回纯 HTTP（_http_disabled 复位）", g._http_disabled is False)
    check("恢复后失败计数归零", g._http_fails == 0)

    # B) 两路都未恢复 → 继续等（谎报恢复会对着插页空转）
    fake = _FakeRequestsModule([GYS_520])
    gm.requests = fake
    g._block_probe_url = GYS_520.url
    g._gys_blocked = lambda: True
    check("gys HTTP 仍 520 且标签仍插页 → 报未解除", bool(g.probe_block()))

    # C) 滑块只在浏览器侧过（HTTP 仍限流）→ 标签正常也算解除
    fake = _FakeRequestsModule([GYS_520])
    gm.requests = fake
    g._gys_blocked = lambda: False
    check("gys 标签页已正常也算解除（人工过滑块只清浏览器侧）", g.probe_block() == "")
finally:
    gm.requests = _real_requests

b = B2b168Crawler()
watch(b)
check("八方不再一刀切关掉浏览器：真出现可人工过的验证插页时借标签递到眼前"
      "（客户「这么久只跳出过 1 次」= 它确实会有）", b.BLOCK_OPEN_BROWSER is True)
check("八方也声明 clearance Cookie 域（过完验证抄回 HTTP 会话）",
      B2b168Crawler.BLOCK_COOKIE_DOMAIN == "b2b168.com")
check("八方 IP 级限流仍不拉 Chrome（靠 _block_kind 分诊拦住，不再靠关掉开关）",
      b.http_block_reason(502, "Bad Gateway", "https://www.b2b168.com/")
      and b._block_kind == "ip")
check("爱采购同样不拉起 Chrome", AipageCrawler.BLOCK_OPEN_BROWSER is False)
a = AipageCrawler()
watch(a)
check("顺企网声明 clearance Cookie 域（过验后抄回 HTTP 会话）",
      Qihuo11467Crawler.BLOCK_COOKIE_DOMAIN == "11467.com")

# 纯 HTTP 站被拦时临时借标签：必须没有本地 ensure_tab 空桩覆盖基类实现
src = inspect.getsource(Qihuo11467Crawler)
check("顺企网不再用空桩覆盖 ensure_tab/close_browser（借标签能力来自基类）",
      "def ensure_tab" not in src and "def close_browser" not in src)
check("顺企网被拦处置入口就位", hasattr(q, "handle_http_block") and hasattr(q, "open_verify_page"))
check("顺企网具备降级取页能力（HTTP 不通就用标签继续采）", hasattr(q, "_fetch_via_tab"))

# ══════════════════════════════════════════════════════════
# 4) 插页绝不当「本页无数据」：_fetch 既不写库也不返回垃圾正文
# ══════════════════════════════════════════════════════════
q = Qihuo11467Crawler()
watch(q)
q._session = FakeSession([QIHUO_429])
q.handle_http_block = lambda url="", reason="": False      # 当作等不到恢复
q._tab_page_ok = lambda: False                              # 标签也开不了
html, u = q._fetch(QIHUO_429.url, tries=1)
check("429 插页不返回正文（旧版会把挑战页交 parse_list → 0 家 → 整轮静默空跑）",
      html == "")
check("记下的拦截原因不会静默丢失", bool(q._block_reason))
check("单次插页绝不重试打满（只请求一次就交看门狗）", len(q._session.calls) == 1)

q2 = Qihuo11467Crawler()
watch(q2)
q2._session = FakeSession([QIHUO_429, NORMAL_200])
q2.handle_http_block = lambda url="", reason="": True      # 当作用户过了验证
q2.BLOCK_POLL = 1
html2, u2 = q2._fetch(QIHUO_429.url, tries=1)
check("看门狗报恢复后自动重采同一 URL 并拿到数据", html2.startswith("<html>") and len(q2._session.calls) == 2)

q3 = Qihuo11467Crawler()
watch(q3)
q3._session = FakeSession([QIHUO_429])
q3.handle_http_block = lambda url="", reason="": False
q3._tab_page_ok = lambda: True
q3._fetch_via_tab = lambda url: ("<html>标签渲染的正常页 " * 300, url)
html3, u3 = q3._fetch(QIHUO_429.url, tries=1)
check("HTTP 等不到恢复但浏览器能开→降级取页继续采（不清 Cookie 也不空转）", len(html3) > 3000)
check("降级模式被置位（后续页面走标签，且定期探 HTTP 恢复）", q3._browser_fallback is True)

# gys：详情页命中插页→走看门狗，绝不写「无联系方式」
gg = GysCrawler()
watch(gg)
fetched = []
gg._fetch_raw = lambda url: (fetched.append(url) or ("", ""))
gg._gys_blocked = lambda: True
retried = []
gg.handle_http_block = lambda url="", reason="": (retried.append(url) or False)
out = gg._fetch_page("https://x.gys.cn/contact.html")
check("gys 插页时 _fetch_page 返回空且不谎称取到页面", out[0] == "")
check("gys 详情页同样接看门狗（不只搜索页）", bool(retried))

# ══════════════════════════════════════════════════════════
# 5) 「开始新任务」复位：清本次数据/账目/熔断，但绝不丢历史种子库
# ══════════════════════════════════════════════════════════
q = isolate(Qihuo11467Crawler())
watch(q)
# 种子库读写改指临时目录：测试绝不碰、也绝不依赖真实跨运行去重文件
import json, tempfile
from pathlib import Path as _P
_tmp = _P(tempfile.mkdtemp(prefix="b2b_seen_test_"))
q._data_dir = _tmp
q._seen_store_path = _tmp / "seen_store_11467.json"
STORE_JSON = json.dumps({"顺企网": {"A 公司": {"ct": 1, "st": "成功"}}}, ensure_ascii=False)
q._seen_store_path.write_text(STORE_JSON, encoding="utf-8")
q._add_result({"公司名称": "A 公司", "数据来源": "顺企网", "电话": "13912345678", "状态": "成功"})
q.stat_listed = q.stat_pages = q.stat_ok = 7
q.stat_no_contact = 3
q.stat_failed = 2
q._filtered_count = 5
q._early_skipped = 4
q._block_times = [time.time(), time.time()]
q._block_reason = "HTTP 429（防采插页）"
q._blocked = True
q._current_delay = 30.0
q._seen_store = {"顺企网": {"A 公司": {"ct": 1}}}
before = dict(q._seen_store)
st = q.reset_run_state()
check("reset_run_state 返回成功", st is True)
check("本次已采数据清空", q.snapshot_results() == [])
check("各站账目归零（看板不再显示上一批数字）",
      q.stats()["listed"] == 0 and q.stats()["pages"] == 0 and q.stats()["ok"] == 0)
check("熔断计数清零（新任务不被上一轮限流拖累）", q._block_times == [])
check("拦截状态与等待标记复位", q._blocked is False and q._block_reason == "")
check("采集间隔恢复成界面设定（上一轮的自动降速不带走）", q._current_delay == q.delay_between)
check("停止/暂停标记复位（新任务能正常起跑）",
      q._stop_requested is False and q._pause_requested is False)
check("历史种子库内存条目不动（清与否由界面勾选决定）", q._seen_store == before)
check("历史种子库磁盘文件内容一字不变（跨运行去重成果没被误删）",
      q._seen_store_path.read_text(encoding="utf-8") == STORE_JSON)
names = sorted(p.name for p in _tmp.iterdir())
check("复位不产生任何多余文件/不误删旧文件", names == ["seen_store_11467.json"])
check("早过滤/相关性剔除计数归零", q._filtered_count == 0 and q._early_skipped == 0)

ps = ParallelSession()
# 站点 slug 跟当前 SITE_ORDER 对齐（gys 供应商网已下线，不再存在于 crawlers）
ps.set_enabled(["mic", "b2b168", "11467", "aipage"])
for slug in ("mic", "11467"):
    c = isolate(ps.crawlers[slug])
    c._add_result({"公司名称": f"{slug} 公司", "数据来源": c.PLATFORM_NAME,
                   "电话": "13912345678", "状态": "成功"})
    c.stat_ok = 3
ps._done_sites = {"mic"}
ps._total_done = 9
ps._kw_progress = {"mic": (3, 5)}
rows = ps.snapshot_results()
check("复位前有两站数据", len(rows) == 2)
check("复位前清洗链路能读到数据（有手机号的行不会入库口径误杀）",
      len(ps.clean_records(log=lambda m: None)) == 2)
ps.reset_results()
check("ParallelSession.reset_results 清掉跨站数据", ps.snapshot_results() == [])
check("聚合完成度归零", ps._total_done == 0 and ps._done_sites == set())
check("关键词进度归零（进度条不会延续上一轮的百分比）", ps._kw_progress == {})
check("各站账目一并复位", all(c.stats()["ok"] == 0 for c in ps.crawlers.values()))
check("复位后清洗/入库链路自然为空（不会把上一批数据再写进新表）",
      ps.clean_records(log=lambda m: None) == [])

# 界面层入口齐备（不实例化窗口，只查接口，避免离线跑 Qt 事件循环）
import b2bcollect_gui as gui
W = gui.MainWindow
check("界面：被拦弹窗入口", hasattr(W, "_show_block_alert") and hasattr(W, "_close_block_alert"))
check("界面：被拦时既弹横幅也弹窗（横幅在最小化时看不见，就是「不弹提醒」的根因）",
      "_show_block_alert" in inspect.getsource(W._on_blocked))
check("界面：解除/结束时收回弹窗", "_close_block_alert" in inspect.getsource(W._on_block_cleared)
      and "_close_block_alert" in inspect.getsource(W._on_finished))
check("界面：「开始新任务」按钮与槽函数就位（按钮已加入控制行）",
      hasattr(W, "_on_reset_task") and "btn_reset" in inspect.getsource(W._build_ui)
      and "btn_reset.clicked.connect" in inspect.getsource(W._connect_signals))
check("界面：复位前先同步落盘（尾部不满 20 条的数据不丢）",
      hasattr(W, "_final_save_sync") and "_final_save_sync" in inspect.getsource(W._on_reset_task))
src_reset = inspect.getsource(W._on_reset_task)
check("界面：复位释放时间戳文件夹（下次开始采集新建目录）",
      "_first_start = None" in src_reset and "session_dir = None" in src_reset)
check("界面：运行中拒绝复位（不能把正在写的采集线程抽掉）", "isRunning()" in src_reset)
check("界面：历史种子库默认保留，勾选才清", "chk_seen" in src_reset and "clear_seen_store" in src_reset)
# 入库口径必须同时管住「自动保存的 Excel」与「云展导入」两条链（同源 clean_records）
check("自动保存的清洗表走统一入库口径",
      "clean_records" in inspect.getsource(gui.AutoSaveWorker._write_cleaned))
check("云展导入上传的数据走同一入库口径（四项全空不进后台客户库）",
      "clean_records" in inspect.getsource(gui.YzdImportWorker))

print(f"\n全部 {ok} 项断言通过 ✅")
