"""离线验证「爱企查夜间无人处理 → 停止并把剩余名单移交企知道」这条链路。

覆盖（都不启动浏览器，网络与页面读全都用桩顶掉）：
1. NameHandoff 队列语义：give_up 去重、finish 落定、wait_for 能被移交唤醒；
2. 爱企查未超预算：把剩余预算传给等待逻辑，不移交（有人来处理时不该乱动名单）；
3. 爱企查超预算：停止这一路 + 从卡住那一家起全部剩余移交 + 通知界面撤红条；
4. 单平台采集（没有移交对象）：不因 10 分钟上限放弃，沿用原来的长等待；
5. 用户主动停止：不把名单塞给企知道（那违背用户意图）；
6. 爱企查整条 crawl：被拦超时后已采结果不推进、名单不烧；
7. 企知道接收：运行时追加的名字接着采完，且已采过/已排过的名字不会被重复接；
8. 企知道自己名单先跑完：等爱企查落定后再收尾，没移交时不会无限等；
9. 界面双标签工作线程：注入/摘除 handoff、按名字续采过滤、整轮 6 家不重不漏。
"""
import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication  # noqa: E402

app = QApplication([])

import block_guard as bg  # noqa: E402
import dual_session as ds  # noqa: E402
import aiqicha_crawler_v2 as A  # noqa: E402
import qizhidao_crawler as Q  # noqa: E402

BLOCK = "页面被跳转到安全验证页（百度验证）"
NAMES = [f"测试公司{i}" for i in range(1, 7)]


def rec(name, platform):
    # 带上几个关键字段：否则会被当成“空壳记录”走登录闸门，掩盖待测行为
    return {"公司名": name, "状态": "在营", "法定代表人": "张三",
            "地址": "深圳市南山区", "数据来源": platform}


# ── 爱企查桩：只保留与移交相关的判断，页面读写全部顶掉 ──
def make_aqc(handoff=None, limit=600, blocked_at=0.0):
    c = A.AiQiChaCrawlerV2()
    c.handoff = handoff
    c.block_wait_limit = limit
    c._blocked_at = blocked_at
    c.delay_between = 0
    c._login_gate = lambda: True
    c._detect_block = lambda *a, **k: BLOCK
    c.calls = []

    def fake_wait(reason, max_wait=1800, **kw):
        c.calls.append(max_wait)
        return False          # 本轮就是"等不到人"

    c._wait_unblock = fake_wait
    c.emitted = []
    c.signals.handoff_signal.connect(lambda m: c.emitted.append(m))
    return c


def make_qzd(handoff=None):
    c = Q.QiZhiDaoCrawler()
    c.handoff = handoff
    c.handoff_wait = 5.0
    c.delay_between = 0
    c._login_gate = lambda: True
    c._detect_block = lambda: ""
    c._is_session_alive = lambda: True
    c.search_company = lambda name: rec(name, "企知道")
    return c


# ① 队列语义
log1 = []
h = ds.NameHandoff(log=log1.append)
assert h.pending() == [] and not h.gave_up and not h.settled
assert h.give_up(["甲公司", "乙公司", " 甲公司 ", "", None]) == 2, "同名与空名都要滤掉"
assert h.give_up(["甲公司", "丙公司"]) == 1, "已移交过的不该再移交一次"
assert h.gave_up and h.pending() == ["甲公司", "乙公司", "丙公司"]
assert h.pending() == [], "取走即清空"


def _later():
    time.sleep(0.2)          # 模拟“企知道自己名单已经跑完，爱企查这时才撑不住”
    h.give_up(["丁公司"])


threading.Thread(target=_later, daemon=True).start()
t0 = time.time()
assert h.wait_for(5.0) is True, "等待方应被移交唤醒"
assert time.time() - t0 < 2.0, "不该白等整个超时"
assert h.pending() == ["丁公司"]
h.finish()
assert h.settled and h.wait_for(1.0) is False, "落定且没移交就该立刻返回"
assert h.wait_for(5.0, should_stop=lambda: True) is False
print("① NameHandoff：去重、取走即清空、能被移交唤醒、落定即返回 ✓")

# ② 未超预算：把剩余预算交给等待逻辑，不移交
h2 = ds.NameHandoff()
c2 = make_aqc(handoff=h2, limit=600, blocked_at=time.time() - 100)
assert c2._wait_unblock_limited(BLOCK, max_wait=1800, interval=15) is False
assert len(c2.calls) == 1 and 480 <= c2.calls[0] <= 500, f"应只剩约 500 秒预算，实得 {c2.calls}"
assert h2.pending() == [] and not h2.gave_up
assert c2.emitted == [], "没放弃就不该通知界面撤红条"
print("② 还在预算内：按剩余秒数继续等，不动名单 ✓")

# ③ 超预算：停止这一路 + 移交剩余 + 通知界面
h3 = ds.NameHandoff(log=[])
c3 = make_aqc(handoff=h3, limit=600, blocked_at=time.time() - 700)
assert c3._wait_unblock_limited(BLOCK) is False
assert c3.calls == [], "已超预算不该再进等待"
c3.crawl(NAMES, 0)
left3 = h3.pending()          # pending 是取走即清空，只能读一次
assert left3 == NAMES, f"从被拦那一家起全部移交，实得 {left3}"
assert [r["公司名"] for r in c3.results] == [], "被拦时一家都不能写进结果"
assert len(c3.emitted) == 1 and "无人处理" in c3.emitted[0], c3.emitted
print(f"③ 超过 10 分钟无人处理：停止爱企查并移交 {len(left3)} 家 ✓")

# ④ 单平台（无移交对象）：不因 10 分钟上限放弃
c4 = make_aqc(handoff=None, limit=600, blocked_at=time.time() - 5000)
assert c4._wait_unblock_limited(BLOCK, max_wait=1800) is False
assert c4.calls == [1800], f"单平台应保持原有长等待，实得 {c4.calls}"
c4.search_company = lambda name: rec(name, "爱企查")
c4._detect_block = lambda *a, **k: ""
c4.crawl(["甲公司", "乙公司"], 0)
assert [r["公司名"] for r in c4.results] == ["甲公司", "乙公司"]
print("④ 单平台采集：沿用 30 分钟长等待，不提前停下名单 ✓")

# ⑤ 用户主动停止：不移交
h5 = ds.NameHandoff()
c5 = make_aqc(handoff=h5, limit=600, blocked_at=time.time() - 100)


def wait_then_stop(reason, max_wait=1800, **kw):
    # crawl 开头会把 _stop_requested 重置，所以停止只能在等待途中发生（与真实等待循环一致）
    c5._stop_requested = True
    return False


c5._wait_unblock = wait_then_stop
c5.crawl(NAMES, 0)
assert not h5.gave_up and h5.pending() == [], "用户点停止代表全盘打算，不该塞名单给企知道"
assert c5.emitted == []
print("⑤ 用户点停止：不移交名单 ✓")

# ⑥ 搜索途中被拦到超时：同样收掉这一路并移交（状态"拦截未解除"那条路）
h6 = ds.NameHandoff()
c6 = make_aqc(handoff=h6, limit=600, blocked_at=time.time() - 100)
c6._detect_block = lambda *a, **k: ""


def slow_block_search(name):
    # 第一家正常，第二家开始"搜索途中被拦且等不到人"
    if name == NAMES[1]:
        c6._blocked_at = time.time() - 700
        return {"公司名": name, "状态": "拦截未解除"}
    return rec(name, "爱企查")


c6.search_company = slow_block_search
c6.crawl(NAMES, 0)
assert [r["公司名"] for r in c6.results] == [NAMES[0]], "被拦那一家不能写进结果"
left6 = h6.pending()
assert left6 == NAMES[1:], left6
print(f"⑥ 搜索途中被拦到超时：已采 {len(c6.results)} 家保留，移交 {len(left6)} 家 ✓")

# ⑦ 企知道接收：追加的名字接着采完 + 两层去重
h7 = ds.NameHandoff()
c7 = make_qzd(handoff=h7)
h7.give_up([NAMES[2], NAMES[3]])
lst = [NAMES[0], NAMES[1]]
c7.crawl(lst, 0)
assert [r["公司名"] for r in c7.results] == [NAMES[0], NAMES[1], NAMES[2], NAMES[3]], c7.results
assert lst == [NAMES[0], NAMES[1], NAMES[2], NAMES[3]], "移交的名字应追加进本侧名单"
# 重复出现的公司不再采第二遍：已采过的、以及本侧名单里已排着的都要滤掉
h7b = ds.NameHandoff()
c7b = make_qzd(handoff=h7b)
c7b.results = [rec(NAMES[0], "企知道")]
lst2 = [NAMES[1]]
h7b.give_up([NAMES[0], NAMES[1], NAMES[4]])
c7b.crawl(lst2, 0)
assert [r["公司名"] for r in c7b.results] == [NAMES[0], NAMES[1], NAMES[4]], c7b.results
assert lst2.count(NAMES[1]) == 1, "本侧已排着的名字不该再接一遍"
print("⑦ 企知道接收移交名单：接着采完，且不重复接、不重复采 ✓")

# ⑦b 用户点停止：仍按原来的口径记日志，也不能因为要等移交而拖住收尾
h7c = ds.NameHandoff()
c7c = make_qzd(handoff=h7c)
c7c.handoff_wait = 0.5
logs7 = []
c7c.signals.log_signal.connect(logs7.append)


def stop_after_first(name):
    c7c._stop_requested = True      # 采完第一家用户就点了停止
    return rec(name, "企知道")


c7c.search_company = stop_after_first
h7c.give_up(["外公司"])
t0 = time.time()
c7c.crawl([NAMES[0], NAMES[1]], 0)
assert [r["公司名"] for r in c7c.results] == [NAMES[0]], c7c.results
assert any("用户停止采集" in m for m in logs7), logs7
assert time.time() - t0 < 0.4, "停止后不该再去等对面落定"
print("⑦b 用户停止企知道：立即收尾并记原日志，不会卡在等移交上 ✓")

# ⑧ 企知道自己名单先跑完：等爱企查落定，落定前的移交必须接住
h8 = ds.NameHandoff()
c8 = make_qzd(handoff=h8)
threading.Thread(target=lambda: (h8.give_up([NAMES[5]]), h8.finish()), daemon=True).start()
t0 = time.time()
c8.crawl([NAMES[0]], 0)
assert [r["公司名"] for r in c8.results] == [NAMES[0], NAMES[5]], c8.results
assert time.time() - t0 < 4.0, "拿到移交名单后不该再空等"
print("⑧ 企知道先空下来：等到移交再接手，不会把最后几家漏掉 ✓")

# ⑧b 对面正常收工（没移交）：企知道不能无限等
h9 = ds.NameHandoff()
c9 = make_qzd(handoff=h9)
threading.Thread(target=h9.finish, daemon=True).start()
t0 = time.time()
c9.crawl([NAMES[0]], 0)
assert [r["公司名"] for r in c9.results] == [NAMES[0]]
assert time.time() - t0 < 4.0, "落定后应立即收工"
print("⑧b 爱企查正常采完：企知道不等移交，立刻收工 ✓")


# ⑨ 界面双标签工作线程：整轮 6 家不重不漏
class FakeQzd:
    """只测工作线程的接线：真实接收逻辑在 ⑦⑧ 已单独验过"""

    def __init__(self):
        self.results = []
        self.handoff = None
        self.seen = []

    def snapshot_results(self):
        return [dict(r) for r in self.results]

    def crawl(self, names, start):
        self.seen.append(list(names))
        idx = 0
        while idx < len(names):
            n = names[idx]
            idx += 1
            for extra in self.handoff.pending():
                if extra not in names:
                    names.append(extra)
            self.results.append(rec(n, "企知道"))
        self.handoff.wait_for(5.0)
        for extra in self.handoff.pending():
            if extra not in names:
                names.append(extra)
                self.results.append(rec(extra, "企知道"))
        return


class FakeAqc:
    def __init__(self, stop_at=2):
        self.results = []
        self.handoff = None
        self.block_wait_limit = 600
        self.seen = []
        self.stop_at = stop_at

    def snapshot_results(self):
        return [dict(r) for r in self.results]

    def crawl(self, names, start):
        self.seen.append(list(names))
        for i, n in enumerate(names):
            if i >= self.stop_at:
                self.handoff.give_up(names[i:], "验证码/限流持续 10 分钟以上无人处理")
                return
            self.results.append(rec(n, "爱企查"))
        return


import riskbird_gui as G  # noqa: E402

logs = []
orig_setup = G._dual.setup_dual
G._dual.setup_dual = lambda q, a, log, port=None: (True, "双标签已就绪")
try:
    qz, aq = FakeQzd(), FakeAqc()
    wk = G.DualCrawlWorker(qz, aq, NAMES)
    wk.log_signal.connect(logs.append)
    wk.run()
finally:
    G._dual.setup_dual = orig_setup
app.processEvents()   # 采集线程里的 log_signal 是队列投递，要跑一轮事件循环才能收到

done = [r["公司名"] for r in qz.results + aq.results]
assert qz.handoff is None and aq.handoff is None, "跑完必须摘掉移交队列，否则单平台采集会等旧队列"
assert sorted(done) == sorted(NAMES), f"6 家应不重不漏，实得 {done}"
assert len(done) == len(set(done)) == len(NAMES), done
assert any("夜间无人值守" in m for m in logs), logs
assert any("🔁 本轮爱企查中途停止" in m for m in logs), logs
assert any("移交给企知道" in m for m in logs), logs
print("⑨ 双标签工作线程：注入/摘除队列 + 按名字续采 + 6 家不重不漏 ✓")

# ⑨b 续采过滤：两边已采过的公司不再排进待采名单
qz2, aq2 = FakeQzd(), FakeAqc(stop_at=99)
qz2.results = [rec(NAMES[0], "企知道"), rec(NAMES[4], "企知道")]   # 后者是上一轮移交过来采的
aq2.results = [rec(NAMES[1], "爱企查")]
logs2 = []
G._dual.setup_dual = lambda q, a, log, port=None: (True, "双标签已就绪")
try:
    wk2 = G.DualCrawlWorker(qz2, aq2, NAMES)
    wk2.log_signal.connect(logs2.append)
    wk2.run()
finally:
    G._dual.setup_dual = orig_setup
app.processEvents()

assert NAMES[0] not in qz2.seen[0] and NAMES[1] not in aq2.seen[0], \
    f"已采过的不该再排：企知道 {qz2.seen[0]}／爱企查 {aq2.seen[0]}"
assert NAMES[4] not in qz2.seen[0], "上一轮移交过来已采完的也不该重排"
# 移交过来的名字混在企知道结果里，按下标续采会把企知道自己的名单跳过：按名字过滤才不漏
assert NAMES[2] in aq2.seen[0] or NAMES[2] in qz2.seen[0], "未采的必须还在待采名单里"
assert any("家待采" in m for m in logs2), logs2
print("⑨b 续采按公司名过滤：移交过去采过的不会重排，没采的一家都不会漏 ✓")

# ⑩ 界面收到移交通知要撤掉"等待人工处理"红条
w = G.MainWindow()
w._blocked_info = [["爱企查", BLOCK]]
w._login_info = []
got = []
orig_log = w._append_log
w._append_log = lambda m: (got.append(m), orig_log(m))[1]
w._on_platform_handoff("爱企查", "验证码/限流持续 10 分钟以上无人处理")
assert w._blocked_info == [], w._blocked_info
assert any("转给企知道接着采" in m for m in got), got
print("⑩ 界面收到移交信号：撤掉红条并说明公司已转企知道 ✓")

print("HANDOFF_OFFLINE_OK")
