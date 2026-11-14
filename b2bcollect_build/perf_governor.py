"""资源探测 + 自适应并发闸门（性能治理，零第三方依赖）。

为什么要有这个模块
────────────────
工具在一台 8 核 8G 的老笔记本和在一台 32 核工作站上跑的是**同一段代码、同一个
硬编码并发数**（官网回采 6 路、导出 1 路后台写盘……）。硬编码的后果是两头都不对：
弱机上线程把自己压死（界面卡、浏览器卡、请求超时被误判成「网站拦我」，于是又触发
验证码与重采——把资源问题错记成风控问题），强机上 CPU 空着等网络。

顶尖大厂在这一层的做法不是把数字调小，而是**把并发数变成观测结果的函数**：
Netflix 的 concurrency-limits、gRPC/Envoy 的 adaptive concurrency limiter、
TCP 的 AIMD 拥塞控制，是同一条思路的两个说法——
  · 用**测出来的**延迟/失败/饱和度决定并发，而不是拍一个常数；
  · 压力上来时**乘性收敛**（快、坚决），有余量时**加性上探**（慢、谨慎）；
  · 永远有下界（1 路，绝不因为治理而停摆）和上界（不许无限涨，防止把自己打死）。

本模块只做两件事，不掺任何业务判断：
  1) snapshot()：这台机现在有多少资源、被用掉了多少（探测失败就如实给 None，绝不猜）；
  2) Gate：一个会自己收放的许可闸门，业务把「一次会联网/会吃 CPU 的工作」包在里面。

平台口径（都在本机实测过，不是抄文档）
──────────────────────────────────
· CPU 路数  os.cpu_count() / os.sysconf("SC_NPROCESSORS_ONLN")，本机 8。
· 系统负载  os.getloadavg()，POSIX 可用（本机实测 3.5 左右），Windows 没有 → None。
· 内存总量  os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")，
            本机算得 8589934592，与 sysctl hw.memsize 一字不差。
· 可用内存  Linux 读 /proc/meminfo；Windows 走 GlobalMemoryStatusEx；
            macOS 试过 host_statistics64（rc=5 全零）与 ctypes 读 vm.swapusage
            （rc=-1），两条都拿不到**可信**的数——于是 macOS 干脆不猜：available
            给 None，闸门退化成「只用负载 + 实测失败率」两个可信信号。
            （见 _sanity()：宁可 None，也不要一个看着合理其实是 0 的数——
             静默返 0 的探测比没有探测更危险，它会让人以为「内存充足」。）
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
import time

__all__ = ["Snapshot", "snapshot", "invalidate", "recommended_io_workers",
           "Gate", "get_gate", "drop_gate", "describe", "gates_state", "set_cap",
           "IO_WORKERS_MAX", "DoneBoard"]

# 联网型工作线程的**硬上限**（界面上能调到的最大值也用它，见 b2bcollect_gui）。
# 为什么要有这一个常量：这条链上有三处要问「最多几路」——界面的滑块范围、编排层建
# 几路线程、闸门的上界。各写各的数字时，用户把滑块拉到 16、编排层 clamp 到 8，
# 剩下的 8 路是**静默丢的**（表现成「设置不生效」）。单一真源，改一处就三处一起改。
IO_WORKERS_MAX = 16

# 探测结果缓存多久。2 秒的依据：闸门每开始一家公司就要问一次压力，
# 而 /proc 一次读取约几十微秒、getloadavg 约 1 微秒——真正的开销不在读，
# 在「读到的数还在不在有效窗口内」。负载是 1 分钟均值的 EMA，1 秒内不会变出
# 一个新事实；2 秒已经比一次 HTTP 请求（实测几百毫秒到十几秒）灵敏得多。
SNAPSHOT_TTL_S = 2.0

_MIN = 1            # 闸门下界：治理永远不能变成停摆
_MB = 1024 * 1024


class Snapshot:
    """某一时刻的机器资源快照。任何一项都可能 None（探测不到就是探测不到）。"""

    __slots__ = ("cpu_count", "load1", "mem_total", "mem_available", "source", "ts")

    def __init__(self, cpu_count=None, load1=None, mem_total=None,
                 mem_available=None, source="", ts=None):
        self.cpu_count = cpu_count
        self.load1 = load1
        self.mem_total = mem_total
        self.mem_available = mem_available
        self.source = source          # 走了哪条探测路径，排障时第一眼要看的东西
        self.ts = time.monotonic() if ts is None else ts

    # ── 派生指标：都返回 None 表示「不知道」，调用方必须按未知处理 ──
    def saturation(self):
        """1 分钟平均负载 / 逻辑核数。>1 说明已经有活排队，>2 说明明显过载。"""
        if self.load1 is None or not self.cpu_count:
            return None
        return self.load1 / float(self.cpu_count)

    def mem_available_ratio(self):
        if not self.mem_total or self.mem_available is None:
            return None
        return self.mem_available / float(self.mem_total)

    def describe(self) -> str:
        """给人看的一行机器状态。探测不到的项直接说「不可用」，不装作知道。"""
        bits = []
        bits.append(f"CPU {self.cpu_count} 核" if self.cpu_count else "CPU 核数不可用")
        sat = self.saturation()
        bits.append(f"负载 {self.load1:.2f}（{sat:.2f}×核数）" if sat is not None
                    else "负载不可用")
        if self.mem_total:
            gb = self.mem_total / (1024 ** 3)
            bits.append("内存 %.0fG" % gb
                        + ("" if self.mem_available is None
                           else "（可用 %.1fG）" % (self.mem_available / (1024 ** 3))))
        else:
            bits.append("内存不可用")
        bits.append(f"探测路径 {self.source or 'none'}")
        return "｜".join(bits)

    def as_dict(self) -> dict:
        return {"cpu_count": self.cpu_count, "load1": self.load1,
                "mem_total": self.mem_total, "mem_available": self.mem_available,
                "saturation": None if self.saturation() is None else round(self.saturation(), 2),
                "mem_avail_ratio": None if self.mem_available_ratio() is None
                else round(self.mem_available_ratio(), 2),
                "source": self.source}


def _sanity(value, lo, hi):
    """把明显不可能的探测值判成 None。lo/hi 传 None 表示这一侧不限。

    这不是防御性编程的装饰：macOS 上 host_statistics64 原型写错时 rc=5、结构体
    **全零**地返回——如果照单全收，「可用内存 0 字节」会让闸门永远压到 1 路，
    而「可用内存 0 字节」这种数看起来还特别像真的。
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v != v:                              # NaN
        return None
    if lo is not None and v < lo:
        return None
    if hi is not None and v > hi:
        return None
    return v


def _read_proc_meminfo():
    """Linux：MemAvailable 是内核自己算的「不给系统加 swap 就能拿到多少」。"""
    try:
        with open("/proc/meminfo", "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return float(line.split()[1]) * 1024.0      # 文件里单位是 kB
    except Exception:
        return None
    return None


def _read_windows_memory():
    """Windows：GlobalMemoryStatusEx 一次给出总量、可用量和使用百分比。

    这台开发机是 macOS，这条路无法在此实测，所以每一处都做了解释器级别的
    防御：结构体大小、dwLength、字段名任何一个不满足就返回 (None, None)。
    失败的表现是「闸门少一个信号」，绝不是「采集崩掉」。
    """
    try:
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_uint32), ("dwMemoryLoad", ctypes.c_uint32),
                        ("ullTotalPhys", ctypes.c_uint64), ("ullAvailPhys", ctypes.c_uint64),
                        ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64),
                        ("ullTotalVirtual", ctypes.c_uint64), ("ullAvailVirtual", ctypes.c_uint64),
                        ("ullAvailExtendedVirtual", ctypes.c_uint64)]

        kernel32 = ctypes.windll.kernel32        # type: ignore[attr-defined]
        m = MEMORYSTATUSEX()
        m.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
            return None, None
        return float(m.ullTotalPhys), float(m.ullAvailPhys)
    except Exception:
        return None, None


def _probe():
    """真正去问操作系统。每一项独立 try：一个信号挂不能把其它一起带走。"""
    src = []
    cpu = None
    try:
        cpu = os.cpu_count() or None
        if cpu is None:
            n = os.sysconf("SC_NPROCESSORS_ONLN")
            cpu = int(n) if n and int(n) > 0 else None
        if cpu:
            src.append("cpu:sysconf")
    except Exception:
        cpu = None

    load1 = None
    try:
        load1 = _sanity(os.getloadavg()[0], 0.0, 100000.0)
        if load1 is not None:
            src.append("load:getloadavg")
    except Exception:
        load1 = None                      # Windows 上没有 getloadavg，正常现象

    total = avail = None
    try:
        if sys.platform.startswith("win"):
            total, avail = _read_windows_memory()
            if total or avail:
                src.append("mem:GlobalMemoryStatusEx")
        elif sys.platform == "darwin":
            page = os.sysconf("SC_PAGE_SIZE")
            pages = os.sysconf("SC_PHYS_PAGES")
            total = _sanity(page * pages, 256 * _MB, 4096 * _MB * 1024)
            # macOS 的「可用内存」没有既便宜又可信的读法（见模块头），
            # 留 None 让闸门改用负载与失败率，而不是拿一个猜的数去放宽并发。
            src.append("mem:sysconf+total-only")
        else:
            page = os.sysconf("SC_PAGE_SIZE")
            pages = os.sysconf("SC_PHYS_PAGES")
            total = _sanity(page * pages, 256 * _MB, 4096 * _MB * 1024)
            avail = _read_proc_meminfo()
            src.append("mem:proc")
    except Exception:
        pass

    return Snapshot(cpu_count=cpu, load1=load1, mem_total=_sanity(total, 256 * _MB, None),
                    mem_available=_sanity(avail, 0.0, None) if avail is not None else None,
                    source=",".join(src) or "none")


_lock = threading.Lock()
_cached = None
_cached_at = 0.0


def snapshot(force=False) -> Snapshot:
    """带 TTL 缓存的快照。缓存是为了让「每次 acquire 都问一次压力」这件事免费。"""
    global _cached, _cached_at
    now = time.monotonic()
    if not force and _cached is not None and (now - _cached_at) < SNAPSHOT_TTL_S:
        return _cached
    with _lock:
        now = time.monotonic()
        if not force and _cached is not None and (now - _cached_at) < SNAPSHOT_TTL_S:
            return _cached
        snap = _probe()
        _cached, _cached_at = snap, now
        return snap


def invalidate():
    """丢掉缓存（新一轮任务开始时调用，避免沿用几分钟前的负载判断）。"""
    global _cached_at
    with _lock:
        _cached_at = 0.0


def recommended_io_workers(cap=None, snap=None) -> int:
    """联网型工作线程的**起始**路数建议（真正的收放交给 Gate）。

    依据不是玄学，是三条事实：
    ① 这些线程几乎全程在等网络，CPU 不是瓶颈；瓶颈是内存（Chrome 每标签页几百 MB）、
       GIL（HTML 解析在进程内）和界面线程的手感；
    ② 弱机上「多开几路」换来的吞吐，会被超时率与验证码率吃回去（本项目记账：
       超时→重采→更忙，是正反馈）；
    ③ 起始值只影响冷启动，稳态由实测反馈决定，所以宁可从中位起步往上探。
    """
    s = snap or snapshot()
    cpu = s.cpu_count or 4
    base = min(8, max(2, cpu - 2))               # 2 核机给 2 路，8 核给 6 路，16 核封顶 8
    ratio = s.mem_available_ratio()
    if ratio is not None and ratio < 0.15:       # 可用内存不到 15%：先退到最小一档
        base = min(base, 2)
    if s.mem_total is not None and s.mem_total < 4 * 1024 * _MB:
        base = min(base, 2)                      # 4GB 及以下：这台机扛不住多个标签页
    return min(base, cap) if cap else base


class Gate:
    """会自己收放的许可闸门（AIMD）。

    与 Semaphore 的差别只有两点，但这两点正是治理的全部意义：
    · 许可数**不是固定的**：压力上来乘性收敛，压力退了加性上探；
    · 它看得懂业务喂给它的结果（record(ok=..., latency_ms=..., timed_out=...)）。

    时钟与压力源都可以注入，因为这套策略必须能在离线用例里被**证明**，
    而不是靠真等 20 秒看它涨不涨（见 test_perf_governor_offline.py）。
    """

    def __init__(self, name, max_permits=None, min_permits=_MIN, initial=None,
                 clock=time.monotonic, pressure=None,
                 increase_after_s=20.0, decrease_cooldown_s=5.0,
                 window=12, bad_ratio=0.34, over_saturation=1.6,
                 low_mem_ratio=0.12):
        self.name = name
        self._clock = clock
        # pressure() -> (饱和度, 可用内存比例)，任一未知就传 None
        self._pressure = pressure or self._default_pressure
        self._min = max(_MIN, int(min_permits))
        self._max = int(max_permits) if max_permits else 8
        if self._max < self._min:
            self._max = self._min
        self._permits = self._clamp(initial or self._max)
        self._cond = threading.Condition(threading.Lock())
        self._in_use = 0
        self._waiters = 0
        # 反馈窗口：只记最近 window 个结果，够判断「这一阵子是不是不行了」
        self._window = max(4, int(window))
        self._bad_ratio = float(bad_ratio)
        self._results = []                # True/False 的滑动窗口（定长，见 _push 的注释）
        self._increase_after = float(increase_after_s)
        self._decrease_cooldown = float(decrease_cooldown_s)
        self._over_saturation = float(over_saturation)
        self._low_mem_ratio = float(low_mem_ratio)
        self._last_change = self._clock()
        self._last_increase = self._clock()
        # 收敛与上探各自记自己的时间戳：拿「上次涨」的时间去拦「该收的那一刀」，
        # 是上一版真实的 bug（回归位在离线用例的 ③★：刚上探完立刻喂一窗坏样本，
        # 必须当场砍一半，绝不能因为「20 秒前才涨过」而刀举着不落）
        self._last_decrease = self._clock() - self._decrease_cooldown
        # 计数留给人看：治理如果不透明，用户只会觉得「这工具莫名其妙地慢」
        self.stats_counts = {"acquire": 0, "acquire_timeout": 0, "increase": 0,
                             "decrease": 0, "pressure_cut": 0, "ok": 0, "bad": 0}
        # 耗时账（观测用，见 record()）：没有它，「这一轮慢」到底是路数不够还是站点慢，
        # 日志里就只有一句主观判断。
        self._lat_n = 0
        self._lat_sum = 0.0
        self._lat_max = 0.0

    # ── 基本盘 ────────────────────────────────────────────────
    def _default_pressure(self):
        s = snapshot()
        return s.saturation(), s.mem_available_ratio()

    def _clamp(self, n) -> int:
        return int(max(self._min, min(self._max, n)))

    @property
    def permits(self) -> int:
        with self._cond:
            return self._permits

    @property
    def in_use(self) -> int:
        with self._cond:
            return self._in_use

    @property
    def max_permits(self) -> int:
        with self._cond:
            return self._max

    def resize_max(self, n) -> int:
        """运行方改了并发上限时对齐上界（用户在界面把 6 路调成 10 路）。

        只动上界并把当前值夹住，**不重置反馈窗口**：那一窗失败率是刚刚真打出来的
        证据，清空它等于让闸门「忘记」这台机刚才撑不住过。
        """
        with self._cond:
            n = max(self._min, int(n))
            if n != self._max:
                self._max = n
                self._permits = self._clamp(self._permits)
                self._cond.notify_all()
            return self._max

    def _available(self) -> int:
        return self._permits - self._in_use

    # ── 拿许可 / 还许可 ───────────────────────────────────────
    def acquire(self, timeout=None) -> bool:
        """占用一路许可。超时返回 False —— 调用方**应当跳过这一件活**，
        而不是硬挤进去；跳过的东西会由业务的重试台账在下一轮捡回来。

        这里**故意不用 self._clock**：注入的时钟是给策略用的——「上探间隔 20 秒」
        「收敛冷却 5 秒」这类「多久才算过了一阵子」的判断必须能在离线用例里被拨快；
        而阻塞等待是操作系统的事。把等待的那块表也交给假时钟，假时钟不走路时这里就
        是**转圈睡死**：离线用例第一次跑就在 ④a 打印之后彻底没输出（RC=124），
        卡住的正是这一行；现在由 ④e2 把它钉成回归断言。
        两条时间轴分开，策略和等待才各自都能被证明。
        """
        deadline = None if timeout is None else time.monotonic() + float(timeout)
        with self._cond:
            self._waiters += 1
            try:
                while self._available() <= 0:
                    if deadline is None:
                        self._cond.wait(1.0)          # 兜醒一次：重算超时、看有没有被唤醒
                    else:
                        left = deadline - time.monotonic()
                        if left <= 0:
                            self.stats_counts["acquire_timeout"] += 1
                            return False
                        self._cond.wait(min(1.0, left))
                self._in_use += 1
                self.stats_counts["acquire"] += 1
                return True
            finally:
                self._waiters -= 1

    def release(self):
        with self._cond:
            if self._in_use > 0:
                self._in_use -= 1
            self._cond.notify()

    class _Slot:
        def __init__(self, gate, timeout):
            self._g = gate
            self.timeout = timeout
            self.held = False

        def __enter__(self):
            self.held = self._g.acquire(timeout=self.timeout)
            return self

        def __exit__(self, exc_type, exc, tb):
            if self.held:
                self._g.release()
                self.held = False
            return False

    def slot(self, timeout=None):
        """`with gate.slot(timeout=30) as s: if not s.held: return`。

        为什么要有这个壳：手工 try/finally 的写法在审查里最容易被漏掉一条 return
        分支，而漏一次的后果是许可永久流失——闸门会把自己锁死，比没有闸门更糟。
        """
        return Gate._Slot(self, timeout)

    # ── 反馈：这是「自适应」三个字唯一真正落地的地方 ─────────
    def _push(self, bad: bool):
        # 只保留**定长**的最近窗口：判据要的是"这一阵子撑不撑得住"，不是全生命周期账本。
        # 这里曾经还挂着一份 _samples（带时间戳的样本流，注释写着"用于健康度统计"），
        # 但审查时逐处确认过：全仓没有任何地方读它，而它按 60 秒时间窗裁剪——高频率喂样
        # 时那份列表可以一直涨，等于一笔**只进不出的记账**。写-only 的观测状态比没有
        # 观测更糟（它同时花掉内存、CPU 和读者的注意力），所以整块删掉，而不是"留着以后用"。
        self._results.append(bad)
        if len(self._results) > self._window:
            del self._results[:-self._window]
        self.stats_counts["bad" if bad else "ok"] += 1

    def record(self, ok: bool, latency_ms=None, timed_out=False):
        """喂一个业务结果回来。timed_out 一律算坏（超时就是最贵的那种坏）。

        `latency_ms` **只进观测、不进判据**：企业站的正常耗时本来就是 0.3 秒到 12 秒
        一个数量级，拿绝对阈值当压力信号会把「这家站慢」误判成「本机撑不住」——
        那是判据错误，不是治理。真正该收敛的信号是超时/失败率（下面窗口里那一位）。
        """
        with self._cond:
            if latency_ms is not None:
                try:
                    v = float(latency_ms)
                    if v == v and v >= 0.0:            # 同 _sanity：NaN/负数不进账
                        self._lat_n += 1
                        self._lat_sum += v
                        if v > self._lat_max:
                            self._lat_max = v
                except (TypeError, ValueError):
                    pass
            self._push(bool(timed_out) or not bool(ok))
            self._maybe_adjust_locked()

    def note_pressure(self):
        """外部（比如另一条链路）观测到压力时手动踩一脚。"""
        with self._cond:
            self._apply_pressure_locked(force=True)

    def _bad_in_window(self):
        if not self._results:
            return None
        return sum(1 for x in self._results if x) / float(len(self._results))

    def _maybe_adjust_locked(self):
        now = self._clock()
        self._apply_pressure_locked()
        ratio = self._bad_in_window()
        if ratio is None or len(self._results) < self._window:
            return                            # 样本不够，不猜
        if ratio >= self._bad_ratio:
            # 乘性收敛：一次至少砍一半。超时/失败的代价是非线性的——弱机上继续
            # 加路数只会让更多请求一起超时，所以「坚决收」比「温和收」更正确。
            # 冷却只挡**连续收敛**（防抖），绝不拿「刚才涨过一次」当理由推迟收敛：
            # AIMD 的 A 与 I 必须不对称——收要立刻收，涨要慢慢涨。（离线用例 ③★
            # 就是照着这条写的：刚上探完立刻喂一窗坏样本，必须马上砍下去。）
            if now - self._last_decrease >= self._decrease_cooldown:
                target = self._clamp(-(-self._permits // 2))       # ceil(p/2)
                if target < self._permits:
                    self._permits = target
                    self._last_decrease = now
                    self._last_change = now
                    self.stats_counts["decrease"] += 1
                    self._cond.notify_all()
        elif ratio == 0.0 and self._permits < self._max:
            # 加性上探：整窗全好 + 距上次上探够久 + 压力有余量，才 +1。
            # 三个条件缺一个都不涨：这套闸门的目的就是「宁可慢一点也不把自己打死」。
            sat, mem_ratio = self._pressure()
            if (now - self._last_increase >= self._increase_after
                    and (sat is None or sat < 0.85)
                    and (mem_ratio is None or mem_ratio > self._low_mem_ratio * 2)):
                self._permits = self._clamp(self._permits + 1)
                self._last_increase = now
                self._last_change = now
                self.stats_counts["increase"] += 1
                self._cond.notify_all()

    def _apply_pressure_locked(self, force=False):
        """资源压力直接封顶（不等攒够样本）：负载爆表时，失败率还没涨上来就该退了。"""
        sat, mem_ratio = self._pressure()
        ceiling = self._max
        reason = ""
        if sat is not None and sat >= self._over_saturation:
            ceiling = min(ceiling, self._min)
            reason = f"负载 {sat:.2f}×核数 已过载"
        elif sat is not None and sat >= 1.0:
            ceiling = min(ceiling, max(self._min, self._max // 2))
            reason = f"负载 {sat:.2f}×核数 已排队长活"
        if mem_ratio is not None and mem_ratio < self._low_mem_ratio:
            ceiling = min(ceiling, self._min)
            reason = (reason + "；" if reason else "") + f"可用内存仅 {mem_ratio:.0%}"
        if ceiling < self._permits:
            self._permits = self._clamp(ceiling)
            self._last_change = self._clock()
            if force or self.stats_counts.get("last_reason") != reason:
                self.stats_counts["pressure_cut"] += 1
                self.stats_counts["last_reason"] = reason
            self._cond.notify_all()
        # 压力退了这里**什么都不做**：放开上界由 record() 的加性上探一格一格来。
        # 「刚脱离过载就立刻放回最大」是振荡的经典配方——那一脚油门会立刻再造出
        # 一波超时，然后又被砍回来，用户看到的就是并发数来回抽风。

    # ── 可观测性 ─────────────────────────────────────────────
    def stats(self) -> dict:
        with self._cond:
            return {"name": self.name, "permits": self._permits, "min": self._min,
                    "max": self._max, "in_use": self._in_use, "waiters": self._waiters,
                    "window_bad": self._bad_in_window(),
                    "samples": len(self._results),
                    "avg_ms": round(self._lat_sum / self._lat_n, 1) if self._lat_n else None,
                    "max_ms": round(self._lat_max, 1) if self._lat_n else None,
                    **{k: v for k, v in self.stats_counts.items() if k != "last_reason"},
                    "last_pressure_reason": self.stats_counts.get("last_reason", "")}

    def describe(self, snap=None) -> str:
        s = snap or snapshot()
        return f"{self.name} 并发 {self._permits}/{self._max} 路｜{s.describe()}"


class DoneBoard:
    """一组工作线程的「有进展了」广播板（替代 join(timeout=0.5) 那种定时问一遍）。

    为什么需要：标准库要想知道「线程跑完了没有」，只能 `join(timeout=x)` 问一次。
    等待循环里对 N 条线程各问一次，一个周期就是 x×N 秒——6 路、x=0.5 时用户点
    「停止」最慢 3 秒才有反应，预算到点同样拖 3 秒；而绝大多数那次唤醒都是白问
    （并没有线程刚好在那一刻结束）。反过来纯 `join()` 又不行：预算与心跳检查就
    没了落脚点。

    所以这里把「有变化」做成事件：`wait(timeout, last)` 睡到计数超过 last 或超时，
    调用方自己带着上一轮的计数来比。两个细节是刻意的——
    · 计数与等待在**同一把锁**里比较，杜绝「刚检查完就被 ping」的丢唤醒；
    · 超时参数留着（不是无限等），因为心跳打印与预算检查不能只靠事件驱动：
      一批线程全卡在网络超时上时几分钟都没有一次 ping，日志必须照样自证在推进。
    """

    __slots__ = ("_cond", "n")

    def __init__(self):
        self._cond = threading.Condition()
        self.n = 0

    def ping(self):
        with self._cond:
            self.n += 1
            self._cond.notify_all()

    def wait(self, timeout, last):
        """睡到 `n != last` 或超时；返回当前计数（调用方存回去就是新的 last）。"""
        with self._cond:
            if self.n == last:
                self._cond.wait(max(0.0, float(timeout)))
            return self.n

    def wrap(self, fn):
        """包一个线程入口：函数返回或抛异常都算「这一路结束了」，ping 一次。"""
        def runner(*a, **kw):
            try:
                return fn(*a, **kw)
            finally:
                self.ping()
        runner.__name__ = getattr(fn, "__name__", "wrapped")
        return runner


# ── 共享闸门注册表 ─────────────────────────────────────────────
# 为什么按名字共享：同一个进程里可能有多处想开并发（官网回采、图片/头像补全、
# 数据池批量下载）。如果每处各建一个闸门，8 核机上一共还是几十路——闸门各自
# 收放但**总量失控**，等于没有治理。同名共用一个闸门才谈得上「全局限流」。
_gates = {}
_gates_lock = threading.Lock()


def get_gate(name, max_permits=None, initial=None, **kw) -> Gate:
    """按名字取共享闸门；**已存在时对齐上界**，不是把调用方给的数丢掉。

    为什么必须对齐：闸门按名字共享、跨轮次留存，而这一轮与上一轮的并发上限可以
    不一样（界面上把 6 路调成 10 路）。直接返回旧闸门的话新上限被静默丢弃——用户
    调到 10 路、实际还在 6 路上跑，这种「设置不生效」比报错难查得多。
    """
    with _gates_lock:
        g = _gates.get(name)
        if g is None:
            if max_permits is None:
                max_permits = recommended_io_workers()
            g = Gate(name, max_permits=max_permits, initial=initial, **kw)
            _gates[name] = g
        elif max_permits is not None:
            g.resize_max(max_permits)
        return g


def drop_gate(name=None):
    """丢掉闸门（新一轮任务开始时让上下的失败率账目清零）。"""
    with _gates_lock:
        if name is None:
            _gates.clear()
        else:
            _gates.pop(name, None)


def describe() -> str:
    """给日志用的一行「本机档位」——自动的决定必须可见，否则用户只能猜。"""
    with _gates_lock:
        gs = list(_gates.values())
    head = snapshot().describe()
    if not gs:
        return f"{head}｜尚无闸门"
    return head + "｜" + "；".join(
        f"{g.name} {g.permits}/{g.max_permits} 路" for g in gs)


def gates_state() -> list:
    """所有闸门的当前账目（给界面看板用），没有闸门时返回空列表。

    为什么单独给界面开一个口子、而不是让界面去读 ``_gates``：注册表是模块私有状态，
    界面直接摸它就会在遍历时和「新建/丢弃闸门」的采集线程抢字典（RuntimeError:
    dictionary changed size during iteration）。这里在锁内拷贝一份再取数，界面拿到的
    是**快照**而不是活对象——它随后怎么格式化都不会再碰治理层。
    """
    with _gates_lock:
        gs = list(_gates.values())
    return [g.stats() for g in gs]


def set_cap(name, n):
    """把**已存在**闸门的并发上限改成 n（用户在界面上调档时立刻对齐）。

    返回新的上限；闸门还没建起来时返回 None——那是正常情况而不是失败：闸门是
    第一轮官网回采时才建的，建的时候会用调用方传的上限，改不到就等它建的时候生效。
    为什么不能只靠 ``get_gate`` 的对齐：那条路径只在**下一轮**开始时才走，而
    「我在界面上调了，这轮就得按新的跑」是用户的当场预期；上一版把新上限静默丢掉，
    表现成「设置不生效」——比报错难查得多（见 get_gate 的注释）。
    """
    with _gates_lock:
        g = _gates.get(name)
    return g.resize_max(n) if g is not None else None
