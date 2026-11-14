# -*- coding: utf-8 -*-
# 离线自测：性能治理模块 perf_governor + 官网回采的接线（P2 闸门 / P3 连接复用 /
# P5 去忙等 / P6 内存封顶）。
#
# 用户诉求原话：「优化多线程及性能……使工具使用起来更流畅，在任何配置的电脑都能
# 顺利流畅跑起来」。这句话在本套件里被拆成五件**可证伪**的事：
#   ① 探测拿不到可信数时报 None，绝不猜（静默返 0 比没探测更危险：它会让人以为内存充足）；
#   ② 并发数是观测结果的函数——收要立刻收（乘性）、涨要慢慢涨（加性）、永远有下界与上界；
#   ③ 等待方不再定时白问（DoneBoard：有事才醒，且**线程内异常也要广播**）；
#   ④ 缓存按字节封顶 + LRU 淘汰，热点站不许被整批踢掉（那是弱机「越跑越慢」的元凶）；
#   ⑤ 治理层整个缺席时（_gov=None），回采必须照旧出全部数据——它是治理，不是正确性依赖。
#
# 全程不联网、不启 Qt、不碰真浏览器：时钟与压力都注入，所以断言的是**策略本身**，
# 不是「真等 20 秒看它涨不涨」（那种断言既慢又抖，还会在 CI 上随机红）。
# DNS 与 requests 都装了硬闸：真发一次查询/请求就判红（2026-11-10 记过账的老坑）。
import os
import sys
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import perf_governor as pg
import website_contact as wc

FAILS = []
PASS = 0


def check(name, cond, extra=""):
    global PASS
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAILS.append(name)
        print(f"  FAIL {name}{('：' + extra) if extra else ''}")


# ── 离线硬闸（先把地基钉死，后面所有"没联网"的断言都靠它）──────────
_dns_misses = []
_net_trips = []
_fake_addrinfo = wc.socket.getaddrinfo


def _addrinfo(host, *a, **k):
    h = str(host or "").lower().rstrip(".")
    if h.endswith(".test"):                 # 本套件夹具专用 TLD（RFC 6761 保留，永不解析）
        return [(2, 1, 6, "", ("58.87.101.11", 0))]
    _dns_misses.append(h)
    raise OSError(f"离线用例查了夹具外的域名：{h}")


class _Tripwire:
    """requests.get 的探针：真被调用就说明有人绕过了 fetch_html 这层桩。"""

    def __init__(self, real):
        self.real = real

    def __call__(self, url, *a, **k):
        _net_trips.append(str(url))
        raise AssertionError(f"离线用例真发出了 HTTP 请求：{url}")


_real_requests = None
try:
    import requests as _rq
    _real_requests = _rq.get
    _rq.get = _Tripwire(_rq.get)
except Exception:
    _rq = None

wc.socket.getaddrinfo = _addrinfo


class Clock:
    """可拨的钟：20 秒的上探间隔在这里是 0 真实耗时，且每一步都可复现。"""

    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, secs):
        self.t += float(secs)


def make_gate(permits=4, cap=8, sat=0.1, mem=0.5, **kw):
    """闸门工厂：默认压力是「有余量」，让失败率单独说话（压力本身另开一节验）。"""
    ck = Clock()
    g = pg.Gate("t", max_permits=cap, initial=permits, clock=ck,
                pressure=(lambda: (sat, mem)), **kw)
    return g, ck


def feed(g, n_ok=0, n_bad=0):
    for _ in range(n_ok):
        g.record(True, latency_ms=500.0)
    for _ in range(n_bad):
        g.record(False, latency_ms=9000.0)


try:
    # ══════════════════════════════════════════════════════════
    print("\n── ① 探测：拿不到可信数就报 None（宁可不知道，也不许猜）")

    check("①a NaN 判 None（NaN 参与比较永远 False，放过它等于放过一切垃圾数）",
          pg._sanity(float("nan"), 0.0, 1e9) is None)
    check("①b 负数判 None（'可用内存 -3G' 这种数看起来也像'快满了'的信号）",
          pg._sanity(-3.0, 0.0, None) is None)
    check("①c 上界外判 None：负载 1e12 不是负载，是探测坏了",
          pg._sanity(1e12, 0.0, 100000.0) is None)
    check("①d 边界内照收：负载 0.0 是合法值（机器完全空闲），不能被当成'没探到'",
          pg._sanity(0.0, 0.0, 100000.0) == 0.0)
    check("①e lo/hi 传 None 表示那一侧不限（上一版在这里 TypeError，是实测踩到的）",
          pg._sanity(9.9, 0.0, None) == 9.9 and pg._sanity(9.9, None, None) == 9.9)
    check("①f 非数字（None/字符串）判 None 且不抛异常",
          pg._sanity(None, 0.0, None) is None and pg._sanity("12G", 0.0, None) is None)

    s0 = pg._probe()              # 就在本机真探一次：这模块首先得在这台机上说真话
    check("①g 本机真探测：核数是正整数", isinstance(s0.cpu_count, int) and s0.cpu_count > 0,
          f"cpu_count={s0.cpu_count}")
    check("①h 本机真探测：内存总量落在 256MB~4TB（单位搞错会一眼假）",
          s0.mem_total is not None and 256 * 1024 ** 2 <= s0.mem_total <= 4096 * 1024 ** 3,
          f"mem_total={s0.mem_total}")
    check("①i describe() 把 CPU/内存/探测路径都写出来了（排障第一眼看的就是这行）",
          all(k in s0.describe() for k in ("CPU", "内存", "探测路径")), s0.describe())
    check("①j 探测路径可核对：这串记的是真走了哪几条 syscall",
          "cpu:" in s0.source and "mem:" in s0.source, s0.source)
    check("①k 本机这一趟没查出过一次真 DNS（探测只读内核计数器，不发网络包）",
          not _dns_misses, str(_dns_misses[:3]))

    pg.invalidate()
    sa = pg.snapshot()
    sb = pg.snapshot()
    check("①l TTL 内两次 snapshot 复用同一对象（每次拿许可都真读一遍内核是白付的钱）",
          sa is sb)
    pg.invalidate()
    check("①m invalidate 后重新探测（新任务开始时不许沿用几分钟前的负载结论）",
          pg.snapshot() is not sa)
    _t0 = time.perf_counter()
    for _ in range(2000):
        pg.snapshot()
    _cached_ms = (time.perf_counter() - _t0) * 1000.0 / 2000
    check("①n 命中缓存的 snapshot 每次 < 0.05 毫秒（治理开销必须远小于一次网络请求）",
          _cached_ms < 0.05, f"{_cached_ms:.5f} ms/次")

    w8 = pg.recommended_io_workers(snap=pg.Snapshot(cpu_count=8, mem_total=16 * 1024 ** 3,
                                                    mem_available=12 * 1024 ** 3))
    w2 = pg.recommended_io_workers(snap=pg.Snapshot(cpu_count=2, mem_total=2 * 1024 ** 3,
                                                    mem_available=int(1.5 * 1024 ** 3)))
    check(f"①o 8 核 16G 的起始路数（{w8}）明显多于 2 核 2G（{w2}）——"
          f"「任何配置都能跑」就落在这个差值上", w8 > w2 and w2 >= 1)
    check(f"①p 小内存机封顶到最小一档（2G 给 {w2} 路）：内存是比核数更硬的墙", w2 == 2)
    check("①q cap 说话算数：只要 1 路就绝不给 2 路（弱机上用户手动降档必须被尊重）",
          pg.recommended_io_workers(cap=1) == 1)
    lowmem = pg.recommended_io_workers(snap=pg.Snapshot(
        cpu_count=16, mem_total=64 * 1024 ** 3, mem_available=int(0.10 * 64 * 1024 ** 3)))
    check(f"①r 可用内存只剩 10% 时即使 16 核也退回 2 路（{lowmem}）", lowmem == 2)
    nores = pg.recommended_io_workers(snap=pg.Snapshot(cpu_count=None, mem_total=None))
    check(f"①s 什么都探不到时仍有安全默认值（{nores}），不抛异常也不给 0（0 路=停摆）",
          nores >= 1)
    unknown_mem = pg.recommended_io_workers(snap=pg.Snapshot(cpu_count=8, mem_total=8 * 1024 ** 3,
                                                             mem_available=None))
    check(f"①t macOS 这类「可用内存探不到」的机器按未知处理：仍按核数给 {unknown_mem} 路，"
          f"而不是当它是 0 可用直接压死", unknown_mem == 6)

    # ══════════════════════════════════════════════════════════
    print("\n── ② AIMD 策略：收要立刻收，涨要慢慢涨")

    g, ck = make_gate(permits=4, cap=8)
    check("②a 起始许可 = 传入的 initial（弱机从中位起步往上探，不从最大值开始撞）",
          g.permits == 4)
    feed(g, n_ok=6)
    check("②b 样本不够一个窗口时什么都不做（拿 6 条样本调并发是猜，不是治理）",
          g.permits == 4 and g.stats()["decrease"] == 0 and g.stats()["increase"] == 0,
          str(g.stats()))

    g, ck = make_gate(permits=4, cap=8)
    ck.advance(25.0)                      # 越过 20 秒的上探间隔
    feed(g, n_ok=12)                      # 整窗全好
    check(f"②c 整窗全好 + 间隔到 → 只 +1 路（4 → {g.permits}）：加性上探，绝不翻倍",
          g.permits == 5 and g.stats()["increase"] == 1)
    feed(g, n_ok=12)
    check("②d 又一窗全好但间隔没到 → 纹丝不动（上探靠时间闸门，不靠'信心'）",
          g.permits == 5)
    ck.advance(25.0)
    feed(g, n_ok=12)
    check(f"②e 间隔到了才再 +1（→ {g.permits}）：两次上探之间至少隔 increase_after",
          g.permits == 6 and g.stats()["increase"] == 2)

    g, ck = make_gate(permits=6, cap=8)
    feed(g, n_ok=11, n_bad=1)
    check(f"②f 一窗里只有 1 条坏（1/12）不收敛（仍 {g.permits}）：偶发一家站挂了"
          f"不该惩罚本机", g.permits == 6 and g.stats()["decrease"] == 0)

    # 这一节是**上一版真 bug 的回归位**：收敛冷却曾经读「上次任何改动」的时间，
    # 于是「刚上探过就砍不动」，AIMD 的不对称被写成了对称。
    g, ck = make_gate(permits=4, cap=8)
    ck.advance(25.0)
    feed(g, n_ok=12)
    up = g.permits
    feed(g, n_ok=7, n_bad=5)              # 紧接着同一窗补满坏样本：5/12 ≥ 0.34
    check(f"③★ 刚上探到 {up} 路就立刻遇到一窗坏 → **当场**砍到 {g.permits}（应当是 "
          f"{-(-up // 2)}）：收不许被'刚才涨过'拦住", g.permits == -(-up // 2))
    check("②h 这一刀计入 decrease 计数（治理动作必须留痕）", g.stats()["decrease"] == 1)

    g, ck = make_gate(permits=8, cap=8)
    feed(g, n_bad=12)
    check(f"②i 一整窗全坏 → 一次砍一半（8 → {g.permits}）：弱机上继续加路只会让"
          f"更多请求一起超时", g.permits == 4)
    ck.advance(1.0)
    feed(g, n_bad=12)
    check(f"②j 5 秒冷却内继续坏不再连砍（仍 {g.permits}）：一次砍一半已经很重，"
          f"连砍会直接砍到停摆", g.permits == 4)
    ck.advance(6.0)
    feed(g, n_bad=12)
    check(f"②k 冷却过了再坏 → 再砍一半（→ {g.permits}）", g.permits == 2)
    for _ in range(6):
        ck.advance(6.0)
        feed(g, n_bad=12)
    check(f"②l 一路坏到底也**不会低于 1 路**（{g.permits}）：治理永远不能变成停摆",
          g.permits == 1)
    check("②m 到到下界后不再动刀（decrease 停在 3 次：8→4→2→1）——下界是真界，"
          "不是每次都'试着一刀'", g.stats()["decrease"] == 3,
          str(g.stats()["decrease"]))

    g, ck = make_gate(permits=1, cap=3)
    for _ in range(40):
        ck.advance(25.0)
        feed(g, n_ok=12)
    check(f"②n 上探到 max 就停（{g.permits}/3）：闸门不许无限涨，这是"
          f"「把自己打死」的边界", g.permits == 3)

    g, ck = make_gate(permits=4, cap=8)
    ck.advance(25.0)
    feed(g, n_ok=11, n_bad=1)
    check(f"②o 窗口里混进 1 条坏 → 这一窗不再上探（仍 {g.permits}）：涨的三个条件"
          f"缺一个都不涨", g.permits == 4 and g.stats()["increase"] == 0)

    # ══════════════════════════════════════════════════════════
    print("\n── ③ 资源压力直接封顶（不等攒够样本）")

    g, ck = make_gate(permits=6, cap=8, sat=1.8)
    feed(g, n_ok=1)
    check(f"③a 负载 1.8×核数 → 立刻压到 1 路（{g.permits}）：负载爆表时失败率还没"
          f"涨上来就该退", g.permits == 1)
    check("③b 压力封顶写明原因（'负载'两个字进 last_pressure_reason，日志看得见）",
          "负载" in g.stats()["last_pressure_reason"], g.stats()["last_pressure_reason"])
    n0 = g.stats()["pressure_cut"]
    feed(g, n_ok=12)
    check("③c 同样的压力反复出现时 pressure_cut 只记一次（每来一家就报一遍'过载'"
          "会把日志刷成噪声，掩盖真实的上探动作）", g.stats()["pressure_cut"] == n0)

    g, ck = make_gate(permits=6, cap=8, sat=1.2)
    feed(g, n_ok=1)
    check(f"③d 负载 1.2×（有活排队但没爆表）→ 封到一半（{g.permits}）", g.permits == 4)

    g, ck = make_gate(permits=6, cap=8, mem=0.05)
    feed(g, n_ok=1)
    check(f"③e 可用内存只剩 5% → 压到 1 路（{g.permits}）：内存比 CPU 更快要命",
          g.permits == 1)

    ck2 = Clock()
    g2 = pg.Gate("t", max_permits=8, initial=6, clock=ck2, pressure=lambda: (1.8, 0.5))
    g2.record(True)
    cut = g2.permits
    g2._pressure = lambda: (0.1, 0.5)     # 压力退了（改注入点本身，不是改判据）
    ck2.advance(0.5)
    g2.record(True)
    check(f"③f 压力退了**不自动放回原值**（收敛前 {cut}，现在 {g2.permits}）："
          f"「刚脱离过载就踩满油门」是振荡的经典配方", g2.permits == cut)
    ck2.advance(25.0)
    feed(g2, n_ok=12)
    check(f"③g 但上探通道活着：一窗全好后 +1（{g2.permits}）——不是压下去就再也回不来",
          g2.permits == cut + 1)

    # ══════════════════════════════════════════════════════════
    print("\n── ④ 许可账目：拿了要还，还不上就别硬挤")

    g = make_gate(permits=2, cap=4)[0]
    check("④a 两路许可可以连拿两次", g.acquire(timeout=0.1) and g.acquire(timeout=0.1))
    check("④b 第三路拿不到 → 返回 False 而不是抛异常/无限等（调用方据此**跳过**这一件活）",
          g.acquire(timeout=0.05) is False)
    check("④c 拿不到的那一路计入 acquire_timeout（'被限速'必须可数）",
          g.stats()["acquire_timeout"] == 1)
    g.release()
    check("④d 释放后等待方立刻能拿到（notify 落在 release 里，不靠下一次轮询）",
          g.acquire(timeout=0.1) is True)
    g.release()
    g.release()
    check(f"④e 多余 release 不把 in_use 压成负数（现在 {g.in_use}）：负数会让闸门"
          f"'看起来'永远有空位", g.in_use == 0)

    # 这一条是**回归位**：本模块文档承诺「时钟可注入」，而第一版实现把注入的钟同时
    # 用在了阻塞等待的 deadline 上。假时钟不走路 → deadline 永远在前方 → 就地转圈
    # 睡死。整个离线用例当时的表现是 RC=124 卡在 ④a 之后一句不出——修之前这条必然挂，
    # 修之后它才第一次把「等待走的是真实时间」这件事钉成可证的东西。
    g, ck = make_gate(permits=1, cap=1)
    ck.t = 5_000_000.0                     # 假时钟彻底冻住（不 advance，起点也挪走）
    assert g.acquire(timeout=0.05) is True
    _tw0 = time.perf_counter()
    _ret = g.acquire(timeout=0.05)
    _real_ms = (time.perf_counter() - _tw0) * 1000.0
    check(f"④e2 假时钟冻结时 acquire(timeout) 仍按**真实时间**返回 False（实测 "
          f"{_real_ms:.0f}ms）：策略的表和等待的表必须是两条时间轴，合成一条就是睡死；"
          f"下限同样有讲究——秒回说明它根本没等，那在生产里就是空转抢 CPU",
          _ret is False and 10.0 <= _real_ms < 1000.0, f"{_real_ms:.1f}ms")
    g.release()

    g = make_gate(permits=1, cap=1)[0]
    with g.slot(timeout=0.1) as s:
        held_in = g.in_use
    check(f"④f slot 正常退出会释放（块内 {held_in}，块后 {g.in_use}）",
          held_in == 1 and g.in_use == 0)
    try:
        with g.slot(timeout=0.1) as s2:
            assert s2.held
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    check("④g 块内抛异常同样释放（漏一次的后果是许可永久流失，闸门会把自己锁死，"
          "比没有闸门更糟）", g.in_use == 0)

    g = make_gate(permits=1, cap=1)[0]
    got = []

    def holder():
        if g.acquire(timeout=1.0):
            time.sleep(0.2)
            got.append("held")
            g.release()

    def waiter():
        ok = g.acquire(timeout=2.0)
        got.append("wait:" + str(ok))
        # 拿到就得还：这条线程是「等到叫醒」的证据，不是许可的终点站。
        # 早先漏了这一句，④i 量出来的 in_use 是 1——那红的是夹具自己的账，
        # 不是闸门的账，但两种错误在日志里长得一模一样，所以账目必须闭合。
        if ok:
            g.release()

    th1, th2 = threading.Thread(target=holder), threading.Thread(target=waiter)
    th1.start()
    time.sleep(0.02)
    th2.start()
    th1.join()
    th2.join()
    check(f"④h 第二个线程真的等到了第一个释放（拿到的是事件叫醒，不是白问出来的）：{got}",
          "wait:True" in got and "held" in got, str(got))
    check("④i 两条线程跑完 in_use 归零（没有流失）", g.in_use == 0)

    g, _ = make_gate(permits=4, cap=6)
    check(f"④j resize_max 放大后当前值不被动抬高（上界是允许，不是命令）："
          f"max {g.resize_max(10)}、permits {g.permits}",
          g.max_permits == 10 and g.permits == 4)
    g.resize_max(2)
    check(f"④k 上界收到 2 时当前许可跟着夹住（{g.permits}）：上界低于当前值是非法状态",
          g.permits == 2 and g.max_permits == 2)
    g_a = pg.get_gate("shared_test", max_permits=3, initial=3)
    g_b = pg.get_gate("shared_test", max_permits=9)
    check("④l 同名闸门是同一个对象（多处各建一个＝总量失控，等于没有治理）", g_a is g_b)
    check(f"④m 第二次调用带的新上限真的生效（max={g_b.max_permits}）：上一版把它静默"
          f"丢掉——用户调到 9 路、实际还在 3 路上跑", g_b.max_permits == 9)
    check("④n 对齐上界时不清反馈窗口（那一窗失败率是刚刚真打出来的证据）",
          g_b.stats()["samples"] == 0)      # 这一路还没喂过样本，此处只钉住"没被伪造"
    pg.drop_gate("shared_test")
    check("④o drop_gate 能按名清掉（清掉后新对象不带旧账）",
          pg.get_gate("shared_test", max_permits=2).permits == 2)
    pg.drop_gate()

    g = make_gate(permits=3, cap=5)[0]
    st = g.stats()
    g.record(True, latency_ms=1000.0)
    g.record(True, latency_ms=3000.0)
    st2 = g.stats()
    check(f"④p 耗时只进观测不进判据：两条样本后 avg={st2['avg_ms']}ms、"
          f"max={st2['max_ms']}ms，而并发没变（慢站不等于本机撑不住）",
          st2["avg_ms"] == 2000.0 and st2["max_ms"] == 3000.0 and st2["permits"] == 3)
    g.record(True, latency_ms=float("nan"))
    g.record(True, latency_ms=-5.0)
    check("④q NaN/负数耗时不进账（否则 avg 会被 NaN 传染成 NaN，日志上就是一片空白）",
          g.stats()["avg_ms"] == 2000.0, str(g.stats()["avg_ms"]))
    check("④r stats 里没有 None 崩掉格式化的字段（窗口空时 window_bad 可为 None，"
          "但计数一定在）", st["acquire"] == 0 and set(
              {"permits", "in_use", "waiters", "ok", "bad"}) <= set(st))

    # ══════════════════════════════════════════════════════════
    print("\n── ⑤ DoneBoard：等待方有事才醒")

    bd = pg.DoneBoard()
    check("⑤a 初始计数 0", bd.n == 0)
    t0 = time.perf_counter()
    bd.wait(0.05, 0)
    idle = time.perf_counter() - t0
    check(f"⑤b 没人 ping 时睡满兜底超时（{idle * 1000:.0f}ms）：心跳与看表不能只靠事件，"
          f"一批线程全卡在网络超时时可能一分钟都没有一次 ping", idle >= 0.04)
    bd.ping()
    t0 = time.perf_counter()
    seen = bd.wait(2.0, 0)
    woke = time.perf_counter() - t0
    check(f"⑤c 计数已变时**立刻返回**（{woke * 1000:.1f}ms，没睡那 2 秒）：这就是"
          f"「点停止没反应」被修掉的地方", woke < 0.05 and seen == 1)
    t0 = time.perf_counter()
    ths = [threading.Thread(target=bd.wrap(lambda: time.sleep(0.08))) for _ in range(6)]
    for t in ths:
        t.start()
    last = bd.n
    while any(t.is_alive() for t in ths):
        last = bd.wait(1.0, last)
    woke_all = time.perf_counter() - t0
    check(f"⑤d 6 条线程全退后被叫醒用了 {woke_all * 1000:.0f} 毫秒（旧口径逐条 "
          f"join(timeout=0.5) 最慢要 3000 毫秒）", woke_all < 0.4, f"{woke_all * 1000:.0f}ms")
    check(f"⑤e wrap 过的每条线程各 ping 一次（6 条 = 6 次，不漏不多；现在 n={bd.n}）",
          bd.n == 7, f"n={bd.n}")

    def _boom():
        raise ValueError("x")

    try:
        bd.wrap(_boom)()                 # 同一条代码路径，异常在 wrap 的 finally 里被接住
    except ValueError:
        pass
    check("⑤f **线程内异常同样广播**（不包这一层，异常退出没人 ping，等待方只能睡满"
          "兜底超时——而'线程炸了'恰恰是最该立刻发现的时候）", bd.n == 8, f"n={bd.n}")

    bd2 = pg.DoneBoard()
    t0 = time.perf_counter()
    last = 0
    for i in range(1, 201):
        bd2.ping()
        last = bd2.wait(2.0, last)     # 刚 ping 完就来问：必须立刻返回，一秒都不许睡
    spin = time.perf_counter() - t0
    check(f"⑤g ping 与 wait 交替 200 轮共 {spin * 1000:.0f} 毫秒（丢唤醒的话这里会是 "
          f"200×2 秒）：丢唤醒的根源是「用布尔问有没有事件」，这里问的是「计数到哪了」",
          spin < 1.0, f"{spin * 1000:.0f}ms")
    check(f"⑤h 计数只增不减、且每次都读到了最新值（{last}/200）", bd2.n == 200 and last == 200)

    # ══════════════════════════════════════════════════════════
    print("\n── ⑥ 官网回采接线：字节预算缓存 / 判据宽度 / 闸门喂数")

    check("⑥a 生产预算值是 96MB（改这个数要连着改下面的机制断言，别只改一处）",
          wc.HOST_CACHE_BUDGET_BYTES == 96 * 1024 ** 2)

    wc.reset_caches()
    _real_budget = wc.HOST_CACHE_BUDGET_BYTES
    wc.HOST_CACHE_BUDGET_BYTES = 1024 * 1024      # 机制测试用小预算：同样本量下更快见胜负
    try:
        page = "<p>安培电子 联系我们</p>" * 100      # 约 1.4KB，便于凑满 1MB
        unit = len(page)
        n_urls = (2 * 1024 * 1024) // unit + 5       # 故意塞到预算的两倍再加几条
        for i in range(n_urls):
            wc._cache_pages(f"https://c{i}.test/", [(page, f"https://c{i}.test/", "首页")], "")
        acc = wc._HOST_CACHE_BYTES[0]
        check(f"⑥b 塞进 {n_urls} 条（约 {n_urls * unit / 1024 / 1024:.1f}MB）后字节账 "
              f"{acc / 1024:.0f}KB ≤ 预算 {wc.HOST_CACHE_BUDGET_BYTES / 1024:.0f}KB："
              f"按条数封顶的老写法在这里会一路涨到几个 G",
              acc <= wc.HOST_CACHE_BUDGET_BYTES, f"acc={acc}")
        check("⑥c 条目数同步被压住（淘汰真发生了，不是只记了个账）",
              len(wc._HOST_CACHE) < n_urls, f"{len(wc._HOST_CACHE)} < {n_urls}")
        check("⑥d 字节账与表内条目一致（漏减一项会让缓存'永远满'，从此谁也不进缓存）",
              abs(acc - sum(wc._entry_bytes(v[0], v[1])
                            for v in wc._HOST_CACHE.values())) <= 1, f"acc={acc}")
        check("⑥e 被淘汰的是**最久没用**的那条（c0 最先塞进、之后再没碰过）",
              wc._cache_get("https://c0.test/") is None)
        hot = f"https://c{n_urls - 1}.test/"
        check("⑥f 最后塞进去的那条一定还在（'保留至少一条'：否则刚抓完就被自己踢掉，白联网）",
              wc._cache_get(hot) is not None)
        for _ in range(3):
            wc._cache_get(hot)
        for i in range(200):
            wc._cache_pages(f"https://d{i}.test/", [(page, "u", "首页")], "")
        check("⑥g 热点站被反复读之后**没被踢**（旧的整表 clear 会把一批热点站全丢掉，"
              "接下来的同站记录全部重新联网——弱机上表现成'越跑越慢'）",
              wc._cache_get(hot) is not None)
        one_big = "y" * (4 * 1024 * 1024)          # 单条就超预算：极端但真实（大站 6 页）
        wc._cache_pages("https://huge.test/", [(one_big, "u", "首页")], "")
        check("⑥h 单条就超预算时**留着它**并且不进入死循环（淘汰到只剩一条就停："
              "刚抓完的东西立刻扔掉等于没抓）",
              wc._cache_get("https://huge.test/") is not None and
              wc._HOST_CACHE_BYTES[0] >= len(one_big))
        before = len(wc._HOST_CACHE)
        wc._cache_pages("https://c9.test/", [(page, "u", "首页")], "")
        wc._cache_pages("https://c9.test/", [(page, "u", "首页")], "")
        check("⑥i 同一 URL 覆盖写不重复记账（重复计会让账虚高、把还活着的条目全淘汰掉）",
              len(wc._HOST_CACHE) == before and
              abs(wc._HOST_CACHE_BYTES[0] - sum(
                  wc._entry_bytes(v[0], v[1]) for v in wc._HOST_CACHE.values())) <= 1,
              f"bytes={wc._HOST_CACHE_BYTES[0]}")
    finally:
        wc.HOST_CACHE_BUDGET_BYTES = _real_budget
    wc.reset_caches()
    check("⑥j reset_caches 把字节账归零（不清账的话下一轮一进来就以为缓存满了）",
          len(wc._HOST_CACHE) == 0 and wc._HOST_CACHE_BYTES[0] == 0)

    for why in ("不可达(ReadTimeout)", "不可达(ConnectTimeout)", "HTTP429", "HTTP503", "抓取异常"):
        check(f"⑥k 「{why}」算坏（本机/链路撑不住的那一类）",
              bool(wc.GATE_BAD_WHY_RE.search(why)))
    for why in ("不可达(ConnectionError)", "不可达(DNSError)", "证书校验失败", "首页不可达",
                "停放/被抢注域名", "字段指向平台页（非企业官网）", "非公网地址（已拦截）",
                "官网字段不是网址", "内部异常(TypeError)", "页面认不出主体（官网可能已易主或填错）"):
        check(f"⑥l 「{why}」**不算坏**（那是对方或代码的事，砍并发毫无用处）",
              not bool(wc.GATE_BAD_WHY_RE.search(why)))
    check("⑥m 闸门判据严格窄于留痕用的 TRANSIENT_WHY_RE：差集就是'环境暂时不给力但不该"
          "限速'那一批——写宽了会因为别人停机把自己越跑越慢",
          all(bool(wc.TRANSIENT_WHY_RE.search(w)) for w in
              ("不可达(ConnectionError)", "证书校验失败", "首页不可达"))
          and not any(bool(wc.GATE_BAD_WHY_RE.search(w)) for w in
                      ("不可达(ConnectionError)", "证书校验失败", "首页不可达")))
    check("⑥n 找到号码与否不是判据：页面没写电话是一家完全健康的公司（"
          "ok=not bad 这条把'无可用值'归到全好）",
          not bool(wc.GATE_BAD_WHY_RE.search("页面未见可用号码/邮箱")))

    PHONE = "021-12345678"                # 与功能④套件同一夹具：已知能过形态闸与打分
    calls = {"n": 0}

    def fake_fetch(url, timeout=wc.TIMEOUT):
        calls["n"] += 1
        return (f"<html><body><div>安培电子 联系我们</div>"
                f'<a href="tel:{PHONE}">{PHONE}</a>'
                f'<a href="mailto:sale@anpei.test">sale@anpei.test</a>'
                f"</div></body></html>", url, "")

    def timeout_fetch(url, timeout=wc.TIMEOUT):
        calls["n"] += 1
        return None, url, "不可达(ReadTimeout)"

    _real_fetch = wc.fetch_html
    wc.fetch_html = fake_fetch
    try:
        pg.drop_gate()
        wc.reset_caches()
        calls["n"] = 0
        recs = [{"公司名称": f"安培电子{i}有限公司", "公司网址": f"https://a{i}.test/"}
                for i in range(12)]
        logs = []
        st = wc.enrich_records(recs, log=logs.append, workers=2, budget=60, limit=50,
                               timeout=1)
        check(f"⑥o 假 fetch 下回采照常出值（接线没改判据：{st['ok']} 行都补到了号码）",
              st["ok"] == 12 and all(PHONE in str(r.get("电话")) for r in recs),
              f"ok={st['ok']}")
        check("⑥p 12 个不同域名各联网一次（域名级缓存不该跨站生效）",
              calls["n"] == 12, f"fetch={calls['n']}")
        gs = st.get("gate") or {}
        check(f"⑥q 闸门收到了这一轮的真实样本（ok+bad={gs.get('ok', 0) + gs.get('bad', 0)}"
              f"=联网家数）", gs.get("ok", 0) + gs.get("bad", 0) == 12, str(gs))
        check("⑥r stats 里带收工档位与单家平均耗时（慢的时候有数可查，不靠猜）",
              "permits" in st and "site_avg_ms" in st, str(sorted(st)))
        check("⑥s 档位可见：日志第一句就写明本机并发档位与依据",
              any("并发档位" in m and "路" in m for m in logs), "\n".join(logs[:2]))

        # 全超时的一轮：闸门必须当场收起来（"弱机自适应"唯一真正落地的证据）
        pg.drop_gate()
        wc.reset_caches()
        calls["n"] = 0
        recs2 = [{"公司名称": f"安培机械{i}有限公司", "公司网址": f"https://b{i}.test/"}
                 for i in range(80)]
        wc.fetch_html = timeout_fetch       # 不换成这一把，下面三条量的还是"全成功"
        try:
            st2 = wc.enrich_records(recs2, log=lambda m: None, workers=6, budget=60,
                                    limit=100, timeout=1)
        finally:
            wc.fetch_html = fake_fetch
        g2s = st2["gate"]
        check(f"⑥t 连续超时让闸门动刀收了（decrease={g2s['decrease']}、收工档位 "
              f"{g2s['permits']}/{g2s['max']}）：自适应不是注释里的口号",
              g2s["decrease"] >= 1 and g2s["permits"] < g2s["max"])
        check(f"⑥u 压到最低也还剩 {g2s['permits']} 路（绝不因治理而停摆）",
              g2s["permits"] >= 1)
        check(f"⑥v 超时样本全部喂进了闸门（bad={g2s['bad']} = 联网 {calls['n']} 次）",
              g2s["bad"] == calls["n"] and calls["n"] == 80,
              f"bad={g2s['bad']} fetch={calls['n']}")
        marks = [str(r.get(wc.SITE_MARK) or "") for r in recs2]
        check("⑥w 超时的留痕是「待重试」而不是终态：限速/超时不许被写成'这家没官网'"
              "（写成终态＝用户手工清列才能重跑，静默劣化）",
              st2["retry"] > 0 and all(m.startswith(wc.RETRY_MARK) or not m for m in marks),
              f"retry={st2['retry']} 例：{marks[0][:40]}")

        # 同站多行：第二轮起全缓存命中，一条样本都不许喂
        pg.drop_gate()
        wc.reset_caches()
        calls["n"] = 0
        same = [{"公司名称": f"安培电子{i}有限公司", "公司网址": "https://same.test/"}
                for i in range(10)]
        st3 = wc.enrich_records(same, log=lambda m: None, workers=1, budget=60,
                                limit=50, timeout=1)
        first_round_calls = calls["n"]
        pg.drop_gate()                      # 新闸门：账目从零开始，差值断言才不含糊
        # 第二轮必须抹掉留痕才会重新排队——留痕是排队依据，不清的话 targets 直接为空，
        # 那一轮"什么都没干"会被错当成"缓存命中"，⑥y/⑥z 量的就不是同一件事了。
        again = []
        for r in same:
            d = dict(r)
            d.pop(wc.SITE_MARK, None)
            again.append(d)
        st3b = wc.enrich_records(again, log=lambda m: None,
                                 workers=1, budget=60, limit=50, timeout=1)
        g3 = st3b["gate"]
        check(f"⑥x 同站 10 行只联网 {first_round_calls} 次（域名级缓存生效）",
              first_round_calls == 1 and st3["sites"] == 1, f"calls={first_round_calls}")
        check(f"⑥y 全缓存命中的第二轮一个样本都没喂闸门（喂进去就是灌一窗假「全好」，"
              f"机器明明撑不住还在加路）：喂样 {g3['ok'] + g3['bad']} 次、联网 "
              f"{calls['n'] - first_round_calls} 次",
              g3["ok"] + g3["bad"] == 0 and calls["n"] == first_round_calls,
              f"{str(g3)} calls={calls['n']}")
        check(f"⑥z 拿许可的次数正好等于家数（{g3['acquire']}＝10 家）：队列判空排在 "
              f"acquire **之前**，没有一次'为一个不存在的活排队'——那种等待会在每轮"
              f"收尾多出「线程数×2 秒」的尾巴", g3["acquire"] == 10, str(g3))
    finally:
        wc.fetch_html = _real_fetch
        pg.drop_gate()
        wc.reset_caches()

    # ══════════════════════════════════════════════════════════
    print("\n── ⑦ HTTP 连接复用：Session 按线程一份")

    s1 = wc._http_session()
    s2 = wc._http_session()
    check("⑦a 同线程内拿到的是同一个 Session（这就是'复用'的全部含义：同站第 2~6 页"
          "不必重新 TCP+TLS 握手）", s1 is s2)
    box = {}
    threading.Thread(target=lambda: box.__setitem__("s", wc._http_session())).start()
    time.sleep(0.1)
    check("⑦b 换一个线程就是另一个 Session（requests.Session 不是线程安全的，"
          "共享一份等于把并发问题请回来）", box["s"] is not s1)
    adapter = s1.get_adapter("https://a0.test/")
    check(f"⑦c 池子挂上了且有上限（pool_maxsize={getattr(adapter, '_pool_maxsize', None)}）："
          f"无界池在长任务里就是无界连接数",
          getattr(adapter, "_pool_maxsize", 0) > 0)
    check("⑦d 传输层 max_retries.total=0：重试交给上层「待重试」台账，别在传输层"
          "偷偷再打三次——那会把预算耗尽变成'谁也说不清这一家到底请求了几次'",
          adapter.max_retries.total == 0, str(adapter.max_retries))
    check("⑦e Session 带上了 UA 头（裸 requests.get 每次也要重设，这里一次搞定）",
          "Chrome" in (s1.headers.get("User-Agent") or ""), str(s1.headers.get("User-Agent")))
    wc.close_thread_session()
    check("⑦f close_thread_session 后本线程重开一份（关掉的 Session 不许再用）",
          wc._http_session() is not s1)
    wc.close_thread_session()
    wc.close_thread_session()
    check("⑦g 重复 close 不抛异常（线程收尾路径上任何异常都会少一路并发）", True)
    check("⑦h 本套件从未真发出 HTTP 请求（所有轮次都走 fetch_html 的桩）",
          not _net_trips, str(_net_trips[:3]))
    check("⑦i 从未查过夹具外的域名（离线用例悄悄联网是记过账的老坑）",
          not _dns_misses, str(_dns_misses[:3]))

    # ══════════════════════════════════════════════════════════
    print("\n── ⑧ 并行正确性：闸门收紧时一家都不许丢")

    _real_fetch = wc.fetch_html
    lock8 = threading.Lock()
    hit = {"n": 0}

    def slow_fetch(url, timeout=wc.TIMEOUT):
        with lock8:
            hit["n"] += 1
        time.sleep(0.01)
        return fake_fetch(url, timeout)

    wc.fetch_html = slow_fetch
    try:
        pg.drop_gate()
        wc.reset_caches()
        recs3 = [{"公司名称": f"安培电子{i}有限公司", "公司网址": f"https://p{i}.test/"}
                 for i in range(60)]
        st4 = wc.enrich_records(recs3, log=lambda m: None, workers=8, budget=120,
                                limit=100, timeout=1)
        check(f"⑧a 8 路并行 60 家全部落笔（先要许可再认领的口径下没有一家被跳过）："
              f"marked={st4['marked']}、fetch={hit['n']}",
              st4["marked"] == 60 and hit["n"] == 60 and st4["skipped"] == 0,
              f"marked={st4['marked']} fetch={hit['n']}")
        check("⑧b 每一行都拿到了值（并发改写同一列的账目对得上）",
              all(PHONE in str(r.get("电话")) and "sale@anpei.test" in str(r.get("邮箱"))
                  for r in recs3))
        check(f"⑧c 跨行去重的账在锁里并：60 行同一号同一邮箱 → 净新增 "
              f"{st4['new_values']}（不是 120）", st4["new_values"] == 2,
              f"new_values={st4['new_values']}")
        check("⑧d 许可数从没超过闸门当期的路数（in_use 上限是 permits，不是线程数）",
              st4["gate"]["in_use"] == 0 and st4["gate"]["permits"] >= 1)

        pg.drop_gate()
        wc.reset_caches()
        hit["n"] = 0
        st5 = wc.enrich_records([{"公司名称": f"安培电子{i}有限公司",
                                  "公司网址": f"https://q{i}.test/"} for i in range(60)],
                                log=lambda m: None, workers=4, budget=0.05, limit=100,
                                timeout=1)
        check(f"⑧e 预算到点时 skipped 与 marked 相加等于待办数（进度账自洽）："
              f"marked={st5['marked']} skipped={st5['skipped']} fetch={hit['n']}",
              st5["marked"] + st5["skipped"] == 60)
        check("⑧f 落笔数不超过真联网数（halt 之后不许再写）",
              st5["marked"] <= hit["n"], f"marked={st5['marked']} fetch={hit['n']}")
        check("⑧g 预算到点没有卡死：函数自己返回了（DoneBoard 的兜底超时保证看表在跑）",
              "seconds" in st5 and st5["seconds"] >= 0)
    finally:
        wc.fetch_html = _real_fetch
        pg.drop_gate()
        wc.reset_caches()

    # 治理层整个缺席：必须退回固定并发并照常出全部数据
    _saved_gov = wc._gov
    _real_fetch = wc.fetch_html
    wc.fetch_html = fake_fetch
    try:
        wc._gov = None
        wc.reset_caches()
        calls["n"] = 0
        deg = [{"公司名称": "安培电子有限公司", "公司网址": "https://deg.test/"}]
        st6 = wc.enrich_records(deg,
                                log=lambda m: None, workers=2, budget=30, limit=10,
                                timeout=1)
        # 查的是**落到记录里的那一列**，不是 stats：st6 里只有计数和原因，压根不带号码，
        # 拿 PHONE in str(st6) 判"数据没少"红的一定是断言自己（上一版就红在这里）。
        check(f"⑧h _gov 不可用时仍完成回采（治理层缺席只该少自适应，不该少数据）："
              f"ok={st6['ok']}、联网 {calls['n']} 次",
              st6["ok"] == 1 and PHONE in str(deg[0].get("电话"))
              and "sale@anpei.test" in str(deg[0].get("邮箱")) and calls["n"] == 1,
              str(deg[0])[:160])
        check("⑧i 降级路径不带 gate/permits 字段（不假装量过东西），且留痕正常写入",
              "gate" not in st6 and "permits" not in st6)
        wc.reset_caches()
        st7 = wc.enrich_records([{"公司名称": f"安培电子{i}有限公司",
                                  "公司网址": f"https://deg{i}.test/"}
                                 for i in range(60)],
                                log=lambda m: None, workers=4, budget=0.05, limit=100,
                                timeout=1)
        check("⑧j 降级路径的进度账同样自洽且能自己返回（预算/停止判定不依赖治理层）："
              f"marked={st7['marked']} skipped={st7['skipped']}",
              st7["marked"] + st7["skipped"] == 60 and "gate" not in st7,
              str(st7)[:160])
    finally:
        wc._gov = _saved_gov
        wc.fetch_html = _real_fetch
        wc.reset_caches()
        pg.drop_gate()
        check("⑧k 桩与全局状态已还原（本套件跑完不影响后续套件的真实性）",
              wc.fetch_html is _real_fetch and wc._gov is not None
              and wc._HOST_CACHE_BYTES[0] == 0)
    # ══════════════════════════════════════════════════════════
    print("\n── ⑨ 界面上的并发档位要当场生效（界面 → 会话 → 闸门那条链）")

    from parallel_session import ParallelSession        # noqa: E402

    # 把「这台机器现在什么样」钉成一份夹具：``recommended_io_workers`` 读的是
    # snapshot()，而 snapshot 带 2 秒 TTL——不固定它，这一节比的就是「两次探到的机器
    # 一样不一样」，那是环境噪声测试，不是判据测试。固定 _probe 而不是固定
    # recommended_io_workers：后者正是被测对象，桩掉它就什么都没证明。
    _fixed = pg.Snapshot(cpu_count=8, load1=4.0, mem_total=16 * 1024 ** 3,
                         mem_available=8 * 1024 ** 3, source="fixture")
    _saved_probe = pg._probe
    pg._probe = lambda: _fixed
    pg.invalidate()
    sess = ParallelSession()
    msgs = []
    try:
        sess.signals.log_signal.connect(msgs.append)
    except Exception:
        pass
    _cap = sess.site_workers_cap()
    _exp_auto = pg.recommended_io_workers(cap=_cap, snap=_fixed)

    check(f"⑨a 并发上限三处同源（界面滑块范围、编排层建线程、闸门上界问的是同一个数）："
          f"pg.IO_WORKERS_MAX={pg.IO_WORKERS_MAX}、wc.io_workers_cap()={wc.io_workers_cap()}、"
          f"session.site_workers_cap()={_cap}",
          pg.IO_WORKERS_MAX == wc.io_workers_cap() == _cap)
    check(f"⑨b 会话默认就是自动档，路数取本机实测值（{sess.site_enrich_workers}）：把默认"
          f"钉成固定 6 的话，弱机上界被写死、强机也吃不到本机余量",
          sess.site_workers_auto is True and sess.site_enrich_workers == _exp_auto,
          f"auto={sess.site_workers_auto} workers={sess.site_enrich_workers} exp={_exp_auto}")

    sess.set_site_workers(2, announce=False)
    check("⑨c 手动 2 路就是 2 路，且口径切回手动（界面那格显示『自动』时会话不许偷偷按别的跑）",
          sess.site_enrich_workers == 2 and sess.site_workers_auto is False)
    sess.set_site_workers(999, announce=False)
    check(f"⑨d 越界值夹到上限（{sess.site_enrich_workers}）：写 999 得到 {_cap} 是"
          f"「按能力上限执行」，而悄悄退回 6 就是「设置不生效」那种当场查不出来的形态",
          sess.site_enrich_workers == _cap)
    sess.set_site_workers(-3, announce=False)
    check("⑨e 0 与负数都表示自动（界面用 0 这档显示『自动』，两条路必须同一口径）",
          sess.site_workers_auto is True and sess.site_enrich_workers == _exp_auto)

    # 闸门**已经建起来**才是这条链的重点：只等下一轮生效的话，用户要救的那一轮已经白跑了
    pg.drop_gate()
    g9 = pg.get_gate(wc.GATE_NAME, max_permits=4, initial=4)
    sess.set_site_workers(9, announce=False)
    check(f"⑨f 调档时当场对齐闸门上界（现在 {g9.max_permits}）：只改会话不改闸门，"
          f"正在跑的这一轮还按 4 路走", g9.max_permits == 9)
    feed(g9, n_bad=12)                        # 先打出一窗真实的失败证据（4 路 → 当场砍到 2）
    _permits_before = g9.permits
    _samples_before = g9.stats()["samples"]
    sess.set_site_workers(3, announce=False)
    check(f"⑨g 上界往上调时**不许把刚砍下去的路数放回原值**（收敛后 {_permits_before} 路，"
          f"上界调到 3 仍是 {g9.permits} 路）：上界是「最多允许几路」，不是「应该有几路」，"
          f"对齐上界时顺手抬回去就是刚脱离过载就踩满油门（与 ③f 同一条纪律）",
          g9.permits == _permits_before and g9.max_permits == 3
          and g9.permits <= g9.max_permits, f"{g9.permits}/{g9.max_permits}")
    check(f"⑨h 走会话这条路同样不清反馈窗口（窗口里仍留着 {_samples_before} 个刚打出来的样本、"
          f"降档 {g9.stats()['decrease']} 次都还在账上）：清空它等于让闸门忘记这台机刚才撑不住过",
          g9.stats()["samples"] == _samples_before and g9.stats()["decrease"] >= 1,
          str(g9.stats())[:140])

    # 降到**当前值以下**才是用户降档时真正要的那个动作。上面那个闸门已经被收敛到 2 路，
    # 在它身上看不出「夹住」有没有发生（2 本来就 ≤ 3），所以重新建一个满额起步的。
    pg.drop_gate()
    g9b = pg.get_gate(wc.GATE_NAME, max_permits=8, initial=8)
    sess.set_site_workers(3, announce=False)
    check(f"⑨i 上界降到当前值以下时当前值一起夹住（现在 {g9b.permits}/{g9b.max_permits}）："
          f"只降上界不降在用的那一份，改完还在按 8 路打——用户降档就是为了少撞几家，"
          f"这一条不成立就是「设置了但没执行」", g9b.permits == 3 and g9b.max_permits == 3)

    msgs.clear()
    got = sess.set_site_workers(5)
    _joined = " ".join(msgs)
    check(f"⑨j 用户动手改档必须当场有一行生效值（返回 {got}、日志 {len(msgs)} 行）："
          f"这行是「我调了、它听没听」唯一可核对的证据，缺了它就只能靠用户自己猜",
          got == 5 and len(msgs) == 1 and "并发档位" in _joined and "本次按 5 路上限" in _joined,
          _joined[:150])
    check("⑨k 那行还报出闸门同步到的数（正在跑的那一轮也一起对齐，不是只说下一轮）",
          "已同步到 5 路" in _joined, _joined[:150])
    msgs.clear()
    sess.configure(site_workers=7)
    check("⑨l configure 下发同样落到会话与闸门，且不重复报数（回采自己会打一行档位，"
          "改一次刷两遍才是噪声）",
          sess.site_enrich_workers == 7 and g9b.max_permits == 7 and not msgs,
          f"workers={sess.site_enrich_workers} gate={g9b.max_permits} msgs={len(msgs)}")

    check("⑨m 闸门还没建起来时 set_cap 返回 None 且不抛异常：那是正常情况"
          "（建的时候直接用新上限），不是失败",
          pg.set_cap("这一节里根本不存在的闸门", 4) is None)
    pg.drop_gate()
    check("⑨n 没有闸门时 gates_state() 给空列表（界面据此不显示那一栏，"
          "而不是显示一个平『0/0 路』让人觉得出了事）", pg.gates_state() == [])
    g10 = pg.get_gate(wc.GATE_NAME, max_permits=3, initial=3)
    g10.acquire(timeout=0.1)
    st = pg.gates_state()
    check(f"⑨o gates_state() 给的是账目**拷贝**（name={st[0]['name']}、"
          f"in_use={st[0]['in_use']}）：界面直接摸注册表就会在遍历时撞上采集线程改闸门",
          len(st) == 1 and st[0]["name"] == wc.GATE_NAME and st[0]["in_use"] == 1)
    st[0]["in_use"] = 99
    st[0]["permits"] = -1
    check(f"⑨p 改拷贝不动真账（现在 {g10.in_use}/{g10.permits}）：界面格式化写错"
          f"就不该把治理层带歪", g10.in_use == 1 and g10.permits == 3,
          f"{g10.in_use}/{g10.permits}")
    g10.release()
    pg.drop_gate()
    # 先还原夹具再断言，而不是反过来：⑨q 要证的是「这一节的收口真的做干净了」，
    # 拿还没还原的状态去查「已还原」必然真——那种断言只是装饰。
    # 还原之后再算一次自动档，期望值就**不能**再用 _exp_auto（那是夹具里 8 核 16G 的
    # 数，真机不一定一样）：这里只要求它落在合法区间内并且用的是真机探测。
    pg._probe = _saved_probe
    pg.invalidate()
    got0 = sess.set_site_workers(0, announce=False)
    check(f"⑨q 夹具收口干净：探测函数已还原、自动档在真机数据上仍算得出合法路数"
          f"（{got0} 路，上限 {_cap}）——留一个被改过的上限或桩在外面，"
          f"后面的章节量的就不是默认形态了",
          pg._probe is _saved_probe and 1 <= got0 <= _cap
          and sess.site_workers_auto is True, f"got={got0}")

    # ══════════════════════════════════════════════════════════
finally:
    wc.socket.getaddrinfo = _fake_addrinfo
    if _rq is not None and _real_requests is not None:
        _rq.get = _real_requests
    pg.drop_gate()
    wc.reset_caches()

# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
if FAILS:
    print(f"失败 {len(FAILS)} 项 / 通过 {PASS} 项")
    for f in FAILS:
        print(f"  - {f}")
    sys.exit(1)
print(f"全部通过 ✅ ({PASS}/{PASS})")
sys.exit(0)
