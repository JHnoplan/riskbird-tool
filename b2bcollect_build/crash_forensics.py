# -*- coding: utf-8 -*-
"""运行期崩溃取证（需求 8：2026-11-19 实机报障「Windows 搜索采购商完成后程序崩溃」）。

【这条模块为什么必须存在】
本轮在交付同款的 PyQt 5.15.11 / Qt 5.15.14 上实测坐实了两件事（证据在
debug 侧的判据里逐字钉住）：
  ① ``QThread.run()`` 里抛出未被接住的 Python 异常 → 进程 ``SIGABRT``（实测退出码 134）；
  ② 由 C++ 侧调用的 Python **槽**（跨线程 queued 信号就是这一路）抛出未捕获异常 → 同样
     ``SIGABRT``。搜索完成时 GUI 线程跑的 ``_on_search_finished`` 正是这一路。
两条都发生在「事情做完的那一刻」，与用户描述的「搜索完后崩」完全对得上；而打包态
Windows 没有控制台，默认 excepthook 打出来的那几行直接进了虚无——现场一片空白，只能互相猜。
本模块让下一次发生时必须留下可定性的证据：异常文本、每一根线程此刻的栈、崩溃前走过哪些
步骤、以及这段栈是在什么运行形态下产生的。

【三条设计约束，都是本轮踩出来的】
1. 本模块**永不抛异常**。取证装置把程序带崩（比如在钩子里再去读那个已经坏掉的对象），
   等于本次事故再没留下任何证据，还把原来的一个故障变成两个。所有对外函数都吞掉自己的失败，
   并把「写不进去」这件事本身记进台账。
2. 落点与 ``b2bcollect_gui._fatal_log_dirs`` 同一条理由：桌面优先（用户自己就能找到并发出来），
   其次用户目录。**刻意不写** ``from b2bcollect_gui import ...``——pool_dialogs 与 gui 互相
    import 会成环；也刻意不依赖 tool_config：正在被报信的模块不许决定报信的路径。
   两边落点是否分叉由离线判据逐条对拍（两份真相迟早会说不一样的那句老话）。
3. 面包屑只说已经发生过的事实，且必须脱敏：手机号只留后四位、设备标识只留前八位，
   绝不写任何密钥值。取证文件是会被用户整份发给我们的东西。
"""
import faulthandler
import collections
import os
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from text_shape import first_line

CRASH_LOG_NAME = "crash_report.txt"
FAULTHANDLER_NAME = "crash_threads.txt"
BREADCRUMB_CAP = 240          # 面包屑条数上限：只留最近这些次，取证文件不许长成读不动的体量
NOTE_FIELD_CAP = 160          # 单个字段值的长度上限
REPORT_BYTES_CAP = 4_000_000  # 超过就裁尾：与启动台账同一条「条数与字节都要卡」的纪律
NEVER_RETURNS = None          # 本模块所有公开函数的失败返回值都是这个，绝不外抛

_INSTALLED = False
_HOOK_ERRORS = 0              # 取证装置自己失败的次数——必须能被问出来，静默失灵才是真风险
_BREADCRUMBS: List[str] = []
_BREAD_LOCK = threading.Lock()
# 待界面取的提示队列（懒建，见 _pending_deque）。这里刻意**只留这一条**出声通路：
# 上一版还并存一个 `_UI_REPORTER` 回调，没人注册等于死代码，一旦有人注册就又回到
# 「后台线程直接动 QWidget」——那是为了报一个错而引入下一场崩溃，比原问题更难查。
_PENDING: "collections.deque" = None
_FH_FILE = None               # faulthandler 落盘的文件句柄，必须长期持有引用（掉了就自动关）
_SEQ = 0


# ── 落点 ────────────────────────────────────────────────────────────────────

def log_dirs() -> List[Path]:
    """崩溃取证的候选落点：桌面优先，其次用户目录。

    与 ``b2bcollect_gui._fatal_log_dirs()`` 必须是同一组目录（离线判据逐条对拍）：远程支持
    只需要问一个地方，问错地方就是「用户说崩了、我们说没日志」这种谁也说不清的僵局。
    """
    return [Path.home() / "Desktop", Path.home() / ".b2bcollect"]


def report_path() -> Path:
    """当前这份取证文件应该落在哪（第一个能写的目录；全都写不进就回首选，由写失败播报说这件事）。"""
    dirs = log_dirs()
    for d in dirs:
        try:
            if d.is_dir():
                return d / CRASH_LOG_NAME
        except OSError:
            continue
    # 候选目录整条为空（Home 被挡、路径解析失败、被测试换成空表）时也必须给出一个说得出口的路径。
    # 这里以前直接写 ``log_dirs()[0]``，列表一空就 IndexError——而本模块的第一条纪律就是「永不抛」：
    # 取证装置在「找不到落点」这一步把话带崩，等于事故现场一个字节都不剩（离线判据 B3 的凭据）。
    return (dirs[0] if dirs else Path.cwd()) / CRASH_LOG_NAME


def _write(text: str) -> str:
    """追加写入取证文件；每个候选落点试一遍，全部失败也只返回一个可说的路径，绝不抛。"""
    for d in log_dirs():
        try:
            path = d / CRASH_LOG_NAME
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > REPORT_BYTES_CAP:
                # 裁尾不能按字节直接切：一个中文是三字节，正好切在中间会让这份
                # 取证文件开头就是非法 UTF-8，以后任何一个用严格模式读它的工具（包括
                # 我们的「把最近几条报错贴到界面」）都会在读日志这一步本身崩掉。
                # 先把尾部按忽略非法字节解码，再重新编码写回，保证落盘的永远是合法文本。
                keep = path.read_bytes()[-REPORT_BYTES_CAP // 2:]
                head = "…（前面的取证记录已按字节上限裁掉）\n"
                path.write_bytes(head.encode("utf-8")
                                 + keep.decode("utf-8", errors="ignore").encode("utf-8"))
            with open(path, "a", encoding="utf-8") as f:
                f.write(text)
            return str(path)
        except OSError:
            continue
        except Exception:                       # 编码/权限之外的任何意外同样不许外溢
            break
    return str(report_path())


# ── 现场（抬头） ────────────────────────────────────────────────────────────

def _mask_phone(v: Any) -> str:
    s = str(v or "")
    return ("****" + s[-4:]) if len(s) >= 7 else "(未绑定)"


def _tool_versions() -> str:
    """版本与构建号：能问就问，问不到只报「问不出」，绝不因为取证而改变运行结果。"""
    try:
        import tool_version as tv
        return f"tool_version={tv.version_text()} build={tv.build_number()}"
    except BaseException as e:                  # 包内模块缺失正是取证要记录的事实之一
        return f"tool_version=?（{type(e).__name__}: {e}）"


def _qt_versions() -> str:
    try:
        from PyQt5.QtCore import QT_VERSION_STR, PYQT_VERSION_STR
        return f"PyQt={PYQT_VERSION_STR} Qt={QT_VERSION_STR}"
    except BaseException as e:
        return f"PyQt=?（{type(e).__name__}: {e}）"


def _identity() -> str:
    """绑定账号与设备标识：只给脱敏后的形态。取证文件会被整份发出来，明文手机号不算证据链。

    只走 access_guard 的公开出口（``account()`` / ``device_identity()``），不伸私有方法：
    取证装置是最后一步才跑的东西，它依赖的每一条内部路径都必须比业务代码更稳。
    """
    try:
        import access_guard as ag
        g = ag.get_guard()
        phone = _mask_phone(g.account())
        did = str(ag.device_identity().get("device_id") or "")[:8]
    except BaseException as e:
        return f"身份=?（{type(e).__name__}: {e}）"
    return f"账号={phone} 设备={did or '?'}"


def _mem_line() -> str:
    """物理内存总量/可用量与本进程峰值 RSS：Windows 上「崩溃」与「整机换页僵住」的分辨全靠这行。

    三条纪律都是从本仓库既有教训里抄来的，不是新发明：
    1. **不引 psutil**。它从来不在随包清单里（data_pool._approx_free_memory_bytes 同一处
       注释已经立过这条规矩），为一个取证字段加交付依赖是拿整体稳定性换一个便利。
    2. **Windows 探测复用 perf_governor._read_windows_memory() 这个唯一把手**。在取证模块里
       再搭一份 ctypes 结构体，就是本仓「同一个判据两份实现只修一份」反复吃过的那次亏。
    3. **ru_maxrss 的单位必须按平台判定**：macOS/BSD 是字节，Linux 是 KB（见
       data_pool.py 2026-11-18 的实测教训）。取证字段带错单位比不带更坏——我们会把一次
       普通的 OverflowError 崩溃误诊成「内存吃光」，整条处置方向都跟着歪。

    取不到的那一项如实写「未取」，绝不用 0 顶替：0 会让「读不到」和「真的没内存了」混成
    同一个数，而这两种情况给用户的建议完全相反。
    """
    parts = []
    total = 0
    # None 代「这一档探测没给我可用量读数」，0 是一个**有效读数**（内存真的吃光了）。
    # 两者不许共用一个哨兵：这是 data_pool._approx_free_memory_bytes 里已经写明的那条
    # 区分（拿 0 当「没读到」会让最该拦的机器被放行），取证字段同理——只是方向相反：
    # 这里混用的后果是把「可用 0GB」写成「未取」，看过往记录的人就错过最决定性的那一个数。
    avail: Optional[int] = None
    why = ""
    try:
        if os.name == "nt":
            import perf_governor
            t, a = perf_governor._read_windows_memory()
            total = int(t or 0)
            avail = int(a) if a is not None else None
            why = "GlobalMemoryStatusEx"
        else:
            try:
                total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
                why = "sysconf"
            except (ValueError, OSError, KeyError, AttributeError):
                total = 0
            # POSIX 没有「系统可用量」的统一标准读数（Linux 的 MemAvailable 够用，macOS 没有），
            # 这里刻意不做近似：把「估算值」当「实测值」写进取证文件，正是最难拆的一种误导。
            # 那 data_pool._approx_free_memory_bytes 的保守估算读数是干嘛的？它就是为「不够就
            # 别硬扛」这个决定准备的，取证只报真实读数，判断复用那一个口径，不另立第二个。
    except BaseException as e:
        why = f"探测失败（{type(e).__name__}）"
    gb = lambda n: f"{int(n) >> 30}GB"          # 只看这里：没读数就写「未取」，不拿 0 冒充
    parts.append(f"物理内存={gb(total) if total > 0 else '未取'}"
                 f"/可用={gb(avail) if avail is not None else '未取'}"
                 f"{'（' + why + '）' if why else ''}")
    rss = 0
    try:
        import resource
        rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        if sys.platform != "darwin":      # Linux/Windows 报 KB，macOS 报字节
            rss *= 1024
        if rss <= 0 or rss > (1 << 40):   # 明显不合理就当未知，不写进报告
            rss = 0
    except BaseException:
        rss = 0                           # Windows 没有 resource 模块：如实「未取」
    parts.append(f"进程峰值RSS={f'{rss >> 20}MB' if rss > 0 else '未取'}")
    return " ".join(parts)


def header() -> str:
    """抬头：先说「这段栈是在什么运行形态下产生的」。只有栈没有形态，同一句
    ``OverflowError`` 可能是转置包、可能是没权限的目录、也可能是被安全软件拦下——三种成因
    给的是三个处置。这里一律只报本机实测到的值，不写推测。"""
    frozen = getattr(sys, "frozen", False)
    lines = [
        f"时间：{datetime.now().isoformat(timespec='seconds')}",
        f"运行形态：platform={sys.platform} frozen={frozen} pid={os.getpid()}",
        f"解释器：{sys.version.split()[0]}  可执行：{sys.executable}",
        f"{_tool_versions()}  {_qt_versions()}",
        _identity(),
        _mem_line(),
    ]
    return "\n".join(lines) + "\n"


# ── 面包屑：崩溃前走过哪些步骤 ──────────────────────────────────────────────

def _clip(v: Any) -> str:
    """把任意字段值收进取证行。**只走 text_shape.first_line 这一个把手**。

    不在这里再写一份「按字符切」：本仓第 ㉓ 族已经为这件事立过门（判据 23b 当场把我
    初版里的 `str(e)[:200]` 拉出来了）。取证文件里念出 `ERR_CONNECTION_` 这种半截名字，
    排查的人会照着那半截去查，方向直接错；而把手会断在标点/词边界上，宁长勿截。
    密钥脱敏必须在收口**之前**判：否则 `ACCESS_KEY=abc…` 被截短后反而让关键字掉出检测窗。
    """
    s = str(v)
    if any(k in s for k in ("SECRET", "ACCESS_KEY", "access_key", "token", "password")):
        return "（已按密钥字段名略去）"
    return first_line(s, NOTE_FIELD_CAP)


def note(action: str, **fields: Any) -> None:
    """记一条「已经发生过的事实」。

    只在阶段边界调用（召回完成、精排完成、开始渲染第几行），不追求逐帧；崩溃时的价值恰恰是
    「最后走到哪一步」。永不抛异常：取证装置不许影响被取证的功能。
    """
    global _SEQ
    _SEQ += 1
    text = f"[{_SEQ}] {time.strftime('%H:%M:%S')} {_clip(action)}"
    for k, v in fields.items():
        text += f" {k}={_clip(v)}"
    try:
        with _BREAD_LOCK:
            _BREADCRUMBS.append(text)
            if len(_BREADCRUMBS) > BREADCRUMB_CAP:
                del _BREADCRUMBS[:len(_BREADCRUMBS) - BREADCRUMB_CAP]
    except BaseException:
        pass


def breadcrumbs() -> List[str]:
    """最近这些步的快照（判据与报告都用它，别直接读内部列表）。"""
    with _BREAD_LOCK:
        return list(_BREADCRUMBS)


def reset_for_tests() -> None:
    """测试专用：清掉面包屑、待取提示，并把钩子**真的摘下来**，让每条判据从同一起点出发。

    待取队列以前没清：判据 A 里 report 过一次，判据 B 再 `drain_pending()` 就会看见
    别人家的残留，于是「队列里有几条」这个数在任何一条判据上都不等于它声称验证的东西。

    安装态也不是「清个标记」就算回到起点：上一版只把 `_INSTALLED` 改回 False，钩子却还挂在
    `sys.excepthook` 上；于是下一次 `install()` 把**自己的钩子**当成「上一任」存下来，链当场
    成环，后续异常既不进文件也不打印。本轮判据整轮结果静默消失就是这个成因（N 段盯住它）。
    """
    global _INSTALLED, _OLD_EXCEPTHOOK, _OLD_THREADHOOK, _HOOK_ERRORS, _FH_FILE, _SEQ
    with _BREAD_LOCK:
        _BREADCRUMBS.clear()
    _HOOK_ERRORS = 0
    _SEQ = 0
    _pending_deque().clear()
    try:
        if sys.excepthook is _hook:
            sys.excepthook = _OLD_EXCEPTHOOK or sys.__excepthook__
    except BaseException:                       # 解释器不让改（嵌入式运行）：记下就好
        pass
    try:
        if threading.excepthook is _thread_hook:
            threading.excepthook = _OLD_THREADHOOK      # None 就是 threading 的默认行为
    except BaseException:
        pass
    _INSTALLED = False
    _OLD_EXCEPTHOOK = None
    _OLD_THREADHOOK = None
    _FH_FILE = None


# ── 报告主体 ────────────────────────────────────────────────────────────────

def _stack_of_threads() -> str:
    """每一根线程此刻的 Python 栈。

    为什么不能只留异常那一段：需求 8 这类「工作线程刚完、GUI 线程正在渲染」的事故，单看
    一条栈分不清是谁先把锁占了；而 C 层 ``sample``/``lldb`` 那套在用户机器上一支都跑不起来
    （本轮那次挂死前后二十分钟才问出路径，见 b2bcollect_gui._launch_stall_watchdog 的记录）。

    但必须滤掉取证装置自己的帧：解释器把异常交给 excepthook 时已经先把业务栈展开，此刻
    当前线程的栈就只剩 ``_hook → report → _stack_of_threads`` 这三行自家的代码（本轮自检实测
    就是这个形状）。不滤的话，每一份崩溃报告的这一段都是三行自报家门的噪音，真正重要的
    工作线程栈反而淹在里面。滤完空了得**如实说一句空的原因**，不能留个光溜溜的分隔行。
    """
    out = []
    own = os.path.realpath(__file__)
    try:
        cur = threading.get_ident()
        for tid, frame in sys._current_frames().items():
            # 结构化过滤（看 FrameSummary.filename），不去解析 format_stack 的文本：
            # 打包态路径、符号链接、文件名里带引号都能把文本切法坑倒，而滤错方向就是把
            # 真实现场滤掉了——那比不滤更糟。
            sums = [s for s in traceback.extract_stack(frame)
                    if os.path.realpath(s.filename or "") != own]
            out.append(f"── 线程 {tid}{'（当前）' if tid == cur else ''} ──")
            if sums:
                out.append("".join(traceback.format_list(sums)))
            else:
                out.append("  （本线程此刻只在取证装置内：异常栈已展开，真实位置见上面的「异常」段）\n")
    except BaseException as e:
        out.append(f"（线程栈取不全：{type(e).__name__}: {e}）")
    return "\n".join(out)


def _pending_deque() -> "collections.deque":
    """待提示队列：封顶 20 条——取证装置不许自己变成一个内存增长点。"""
    global _PENDING
    if _PENDING is None:
        _PENDING = collections.deque(maxlen=20)
    return _PENDING


def drain_pending() -> List[Tuple[str, str]]:
    """界面取走“该告知的崩溃”（取完即清）。

    为什么不在 report 里直接调用界面函数：`report()` 常常是在**工作线程**里被调的
    （后台任务异常、逐行渲染失败都是这一路），而 QWidget 只能在界面线程动。为了报一个错
    反而引入一个跨线程崩溃，是比原问题更难查的那一类。现在本模块只入队，界面自己按时间取：
    多慢一点（最多一个轮询周期）不会多崩一次。
    """
    d = _pending_deque()
    out = list(d)
    d.clear()
    return out


def report(kind: str, exc: Optional[BaseException] = None, where: str = "",
            extra: str = "", notify: bool = True) -> str:
    """写一份取证段落，返回文件路径。**永不抛**。"""
    global _HOOK_ERRORS
    body = [f"\n════════ {kind} @ {datetime.now().isoformat(timespec='seconds')} ════\n",
            header()]
    if where:
        body.append(f"位置：{where}\n")
    if exc is not None:
        body.append("异常：\n")
        try:
            body.append("".join(traceback.format_exception(
                type(exc), exc, exc.__traceback__)))
        except BaseException as e:
            body.append(f"（异常文本渲染失败：{type(e).__name__}: {e}）\n")
    if extra:
        body.append(extra.rstrip() + "\n")
    body.append("崩溃前的步骤（面包屑，最后一条即最近一步）：\n")
    for b in breadcrumbs()[-40:]:
        body.append("  " + b + "\n")
    body.append("\n此刻每一根线程的栈：\n" + _stack_of_threads() + "\n")
    try:
        path = _write("".join(body))
    except BaseException as e:                  # _write 自己已经吞了 OSError，这里兜最后一层
        _HOOK_ERRORS += 1
        path = f"(取证写入失败：{type(e).__name__}: {e})"
    if notify:
        # 一次 report 只出声一次（以前 slot_guard 还另外调了一遍 reporter，同样的事
        # 会说两次）。入队这件事本身也不许把本函数带崩。
        try:
            _pending_deque().append((kind, path))
        except BaseException:
            pass
    return path


# ── 钩子 ────────────────────────────────────────────────────────────────────

def _crash_site(tb) -> str:
    """从 traceback 取最后一帧，拼成「崩溃点：文件:行号 在 函数」。

    只要一行：完整的栈已经在 report() 里逐字写出来了，这里再把整段栈塞进「位置」字段，
    等于同一份证据印两遍——真正的代价不是磁盘，是读的人分不清哪一段才是重点。
    """
    try:
        if tb is None:
            return ""
        while tb.tb_next:
            tb = tb.tb_next
        f = tb.tb_frame
        return f"崩溃点：{f.f_code.co_filename}:{tb.tb_lineno} 在 {f.f_code.co_name}()"
    except BaseException:
        return ""


def _hook(etype, value, tb) -> None:
    """``sys.excepthook``：先落证据，再交回原来那一份。

    PyQt 在 abort 之前也会走这里（实测 traceback 有打印），所以这条钩子必须在它手上抢先把
    东西写进文件——否则用户那边只剩「程序不见了」。钩子自身出错只累加计数，绝不外抛。
    """
    global _HOOK_ERRORS
    try:
        report("未捕获异常（解释器钩子）", value, where=_crash_site(tb))
    except BaseException:
        _HOOK_ERRORS += 1
    try:
        # 交回原来那一份是这条钩子的**义务**：只落文件不交回，开发机上就变成「进程默默
        # 退出不打印任何异常」——本轮判据整轮结果静默消失就是这个形状（成因见 install 里
        # 那段「链不许指回自己」）。这里再判一次是自增防御：即使安装态被谁改坏，也不让
        # 递归把异常吞进那句 except BaseException 里。
        nxt = _OLD_EXCEPTHOOK
        if nxt is _hook or nxt is None:
            nxt = sys.__excepthook__
        if nxt is not None:
            nxt(etype, value, tb)
    except BaseException:
        pass


def _thread_hook(args) -> None:
    """``threading.excepthook``：非 Qt 线程（守护线程、采集 worker）里的异常同样只留证据不溜走。"""
    global _HOOK_ERRORS
    try:
        report("后台线程未捕获异常", args.exc_value,
               where=f"线程={getattr(args.thread, 'name', '?')}")
    except BaseException:
        _HOOK_ERRORS += 1
    try:
        # 与 sys 那一路同一条纪律：交回默认行为（它会打印「Exception in thread xxx」）。
        # 上一版这里只落文件就结束，等于把后台线程的 stderr 输出整个换成了只有我们能看见
        # 的文件——用户在自己机器上复现时对着一个什么都不打印的终端，比崩了还难反馈。
        if _OLD_THREADHOOK is not None:
            _OLD_THREADHOOK(args)
        else:
            default = getattr(threading, "__excepthook__", None)
            if default is not None and default is not _thread_hook:
                default(args)
    except BaseException:
        pass


_OLD_EXCEPTHOOK = None
_OLD_THREADHOOK = None


def enable_faulthandler() -> Tuple[bool, str]:
    """把 C 层致命信号（SIGSEGV/SIGABRT/SIGFPE…）的全线程栈也落盘。

    为什么 Python 钩子还不够：Qt/SQLite 自己踩到野指针时根本没有 Python 异常可hook，进程
    直接没了；``faulthandler`` 是内置模块、不需要随包文件，正是为这种情况准备的（与
    b2bcollect_gui._launch_stall_dump 用它的理由一致）。文件句柄必须长期持有，局部变量一
    出作用域就被 GC 关掉，等于开了个寂寞。
    """
    global _FH_FILE
    # 这里以前直接写 log_dirs()[0]：候选目录整条为空就 IndexError，而它是在 try **外面**
    # 算的，连「取不到就返回一句话」那条兜底都绕过了。同一个口径写两处却只做了一半
    # （report_path 已经修过一次），等于没修。
    dirs = log_dirs()
    path = (dirs[0] if dirs else Path.cwd()) / FAULTHANDLER_NAME
    try:
        for d in log_dirs():
            p = d / FAULTHANDLER_NAME
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                f = open(p, "a", encoding="utf-8", buffering=1)
            except OSError:
                continue
            _FH_FILE = f
            faulthandler.enable(file=f, all_threads=True)
            return True, str(p)
    except BaseException as e:
        return False, f"{type(e).__name__}: {e}"
    return False, str(path)


def install() -> Dict[str, Any]:
    """装上三个钩子（幂等）。返回现场摘要，供启动台账与判据核对，不返回结论靠人猜。"""
    global _INSTALLED, _OLD_EXCEPTHOOK, _OLD_THREADHOOK
    if _INSTALLED:
        return diagnostics()
    # 「上一任」绝不能是本模块自己的钩子：一旦发生（模块被重载、reset 之后重装、两个入口
    # 各装一次），链就变成 _hook → _hook → …，递归抛出的 RecursionError 又被钩子里那句
    # except BaseException 静静吞掉，最终形态是**异常既不打印也不落文件**——取证装置反过来
    # 把故障现场抹掉了。本轮判据整轮结果就是这么消失过一次，所以这里当场退回解释器默认的
    # 那一份，并把它写死在判据里（test_crash_forensics_offline 的 N 段：真开子进程跑）。
    prev = sys.excepthook
    _OLD_EXCEPTHOOK = sys.__excepthook__ if prev is _hook or prev is None else prev
    prev_t = threading.excepthook
    _OLD_THREADHOOK = None if prev_t is None or prev_t is _thread_hook else prev_t
    fh_ok, fh_path = enable_faulthandler()
    try:
        sys.excepthook = _hook
    except BaseException:
        fh_path = fh_path + "（sys.excepthook 安装失败）"
    try:
        threading.excepthook = _thread_hook
    except AttributeError:                       # 3.7 及以前没有这个钩子：如实记下而不是跳过
        pass
    _INSTALLED = True
    note("取证装置已安装", faulthandler=fh_ok, 落点=fh_path)
    return diagnostics()


def open_report() -> Tuple[bool, str]:
    """用系统默认程序打开取证文件。返回 ``(是否成功, 一句话说明)``，**永不抛**。

    做成「用户点横幅上的链接才打开」而不是自动弹：与 #acl-restart 那一条同一红线
    （后台不许抢对话）。三条实测口径都沿用本仓既成写法：
      · 不走 shell=True：路径里有中文、空格和 8.3 短名，多一层 shell 就多一层引号歧义；
      · 失败要把话说出来：静默返回 False 等于用户点了没反应，还是得回来问我们；
      · 文件此刻不在（被裁掉、被用户自己删了）就当场补一份现场，而不是让系统报「找不到文件」
        ——用户是来取证的，取证装置不许交回一个空手。
    """
    path = report_path()
    try:
        if not path.exists():
            report("用户打开取证文件时还没有任何记录", None,
                   where="open_report", notify=False)
        if not path.exists():
            return False, f"取证文件还没能写出来：{path}（见启动台账里的取证状态）"
        import subprocess
        if sys.platform.startswith("win"):
            # 用 stdlib 的 os.startfile 而不是 cmd.exe /c start：后者那一层引号里
            # 「第一个带引号参数算标题」的规则专坑中文路径（本仓 _spawn_relaunch 已经把
            # 「不走 shell」记过一次），而 startfile 直接把路径交给资源管理器，没有引号歧义。
            # 代价是没注册 .txt 关联时会抛 OSError，但那只影响返回值，不影响“话说得清”：
            # 抛上来的异常名与原文都写进返回里，用户那一跳看到的就是原因而不是没反应。
            os.startfile(str(path))          # noqa: S606  只在 Windows 分支执行
        else:
            cmd = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.Popen([cmd, str(path)], close_fds=True, stdin=subprocess.DEVNULL)
        return True, str(path)
    except BaseException as e:
        return False, f"打不开取证文件：{type(e).__name__}: {e}（路径 {path}）"


def slot_guard(label: str):
    """把一个「由 C++ 调用的槽」包起来：里面的异常绝不许逃逸出这个函数。

    这不是掩盖错误——异常照样落进取证文件、照样在界面上说得出，只是不再让整个进程为一条坏
    数据陪葬。实测口径：跨线程 queued 信号所触发的槽里抛出任何异常，PyQt 5.15 直接 ``abort()``
    （判据里那条 rc=134 就是这件事的凭据），表现即用户报的「搜索完就崩」。
    """
    import functools

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            try:
                return fn(*a, **kw)
            except Exception as e:               # 不接 KeyboardInterrupt / SystemExit
                try:
                    # report 自己已经入队并会提醒界面，这里再调一次 reporter 就是同一件事
                    # 说两遍（上一版就是这么写的）。
                    report(f"界面步骤异常：{label}", e, where=fn.__qualname__)
                except BaseException:
                    pass
            return NEVER_RETURNS
        wrapper.__wrapped_slot__ = fn            # 判据与调试都要能拿到原函数
        return wrapper
    return deco


def diagnostics() -> Dict[str, Any]:
    """取证装置自身的现场：装没装、写在哪、自己失败过几次、面包屑多少条。

    「装了钩子」与「钩子真的还能写」是两件事（磁盘满、桌面被 OneDrive 重定向、只读卷都可能
    让后者悄悄失灵），所以必须有一个能被问出来的数。
    """
    return {
        "installed": bool(_INSTALLED),
        "report": str(report_path()),
        "hook_errors": int(_HOOK_ERRORS),
        "breadcrumbs": len(breadcrumbs()),
        "faulthandler_on": bool(_FH_FILE and not _FH_FILE.closed),
        # 还没被界面取走的提示条数：上一版这里报的是 ui_reporter 回调有没有注册，而
        # 回调这条路已经废掉（见 _PENDING 上方那段），继续报它就是报一个恒为 False 的死信。
        "pending": len(_pending_deque()),
    }
