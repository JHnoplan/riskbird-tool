# -*- coding: utf-8 -*-
"""test_crash_forensics_offline.py —— 运行期崩溃取证（需求 8）离线判据。

【这一套在守什么】
2026-11-19 实机报障：Windows 上「搜索采购商完成后程序崩溃」。本轮在同款
PyQt 5.15.11 / Qt 5.15.14 上实测坐实两条机制（不是推测，证据就写在下面的判据里）：
  ① ``QThread.run()`` 里抛出未被接住的 Python 异常 → 进程 SIGABRT（实测退出码 134）；
  ② 由 C++ 侧调用的 Python **槽**（跨线程 queued 信号就是这一路）抛出未捕获异常 → 同样 abort。
搜索完成时界面线程跑的 ``_on_search_finished`` / ``_fill_table`` 正是 ②，后台搜索线程正是 ①。
具体打穿点也实测过：``QTableWidgetItem(13800000000)`` 招 OverflowError（11 位手机号
1.3e10 超过 int32，整数被当成 ``QTableWidgetItem.Type`` 枚举），而 ``QTableWidgetItem(123)``
**不崩但显示空白**——后者比崩更难发现，所以判据不能只测「不抛」。

【为什么单独成套】
取证装置本身是一个「为了报错而新引入的部件」，它最坏的失败形状有三种，全都是本轮真实
踩过的：把工具带崩（在钩子里再去读那个已坏掉的对象）、静默失灵（写不进去却一声不吭）、
跨线程动界面（为了报一个错引入下一场崩溃）。这三种都长得像"业务代码没问题"，混进业务
套件里红起来会被误读成业务缺陷，所以每条契约都放在这个文件里单独钉。

运行：QT_QPA_PLATFORM=offscreen .venv/bin/python b2bcollect_build/test_crash_forensics_offline.py
"""
import ast
import atexit
import os
import re
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# 调试开关一律先清：这些值优先于真实解析，留着会让每条用例都跑在同一个人为状态上，
# 看着像覆盖了、其实一条都没真验（与 test_acl_levels_offline 同一句理由）。
os.environ.pop("B2B_ACL_LEVEL", None)
os.environ.pop("B2B_EDITION", None)

_REPO = Path(__file__).resolve().parent.parent

_PASS = 0
_FAIL = 0


def _assert(cond, msg):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ✅ {msg}")
    else:
        _FAIL += 1
        print(f"  ❌ {msg}")


def _assert_eq(actual, expected, msg):
    _assert(actual == expected, f"{msg}（期望 {expected!r}，实际 {actual!r}）")


# ── 取证的落点必须在临时 HOME 里 ────────────────────────────────────────────
# 不重定向的话，本文件的每一条 report 都会往**真实桌面**写 crash_report.txt：
# 开发机上就是「跑一次测试多一份用户看得见的文件」，CI 上则是把两个平台的落点差异
# 掩盖成"本机绿"。这里在 import 之前就把 HOME 钉走，并当场自证。
_ORIG_HOME = os.environ.get("HOME") or str(Path.home())
_TMP_HOME = tempfile.mkdtemp(prefix="cf_home_")
os.environ["HOME"] = _TMP_HOME
if os.name == "nt":                      # Windows 上 expanduser 认 USERPROFILE
    os.environ["USERPROFILE"] = _TMP_HOME

import crash_forensics as cf              # noqa: E402
from PyQt5.QtCore import Qt               # noqa: E402
from PyQt5.QtWidgets import (QApplication, QCheckBox, QComboBox, QHBoxLayout,  # noqa: E402
                             QLabel, QTableWidget, QTableWidgetItem)

app = QApplication.instance() or QApplication(sys.argv[:1])

import pool_dialogs as pd                 # noqa: E402
import text_shape                         # noqa: E402
import b2bcollect_gui as G                 # noqa: E402


def _hidden_imports(text: str):
    """从打包出口（workflow yaml / 本机脚本）里把 ``--hidden-import`` 的名字抽出来。

    为什么不在这里 import `.github/scripts/check_packaging_manifest.py` 复用那份解析：
      · 本仓没有任何套件真跨目录去引过那个门禁模块（批跑门会把这种引法报成「解释器缺依赖」，
        本轮就是这么被逮住的），为了抽三个名字而把一个 4000 行的交付门接进界面判据，方向不对；
      · 权威解析仍在门禁那边（`test_frozen_delivery_offline` 会跑它），这里只是同一事实的
        第二道岗：宁可抽得宽一点（只多不少报），也不要漏报。
    两种写法都认：`--hidden-import name` 与 `--hidden-import=name`（三份清单历史上各用一种）。
    """
    names = set()
    for m in re.finditer(r"--hidden-import[=\s]+([A-Za-z_][\w.]*)", text):
        names.add(m.group(1))
    return names


def _home_ok():
    return str(Path.home()) == _TMP_HOME or os.path.expanduser("~") == _TMP_HOME


def _fresh():
    """把取证装置回到起点，并把证据文件也清空。

    `reset_for_tests` 只清内存里的面包屑与队列，证据文件是**追加**写的：不清空的话，
    「这一段里不该出现 X」那一类判断读到的就是上一节留下的字节，绿与红都不再对应本节
    （本套件第一版就是在 G9 上这么红的：它验的是「界面先走不算崩溃」，却被上一节
    真崩溃的记录盖绿）。判据不许靠“并到前面没写”碰运气。
    """
    cf.reset_for_tests()
    try:
        p = cf.report_path()
        if p.exists():
            p.write_text("", encoding="utf-8")
    except OSError:
        pass


def _frame_files(text: str):
    """从「线程栈快照」里只取**帧自己的文件名**（`File "...", line N, in f`）。

    为什么不能直接对整段文本做子串判断：`traceback.format_list` 会把那一行的**源码**
    也印出来，而本套件 calling `_stack_of_threads()` 自己就在源码里写了这个名字——
    按文本判就会把「取证装置自己己被滤掉了」误读成“没滤”，方向恰好相反。
    真实不变量只有一条：没有一帧落在取证模块这个文件上。
    """
    return [m for m in re.findall(r'File "([^"]+)", line \d+, in ', text)]


# ── A. 落点与界面启动崩溃日志同源 ───────────────────────────────────────────

def section_a():
    print("\n[A] 取证落点与 gui._fatal_log_dirs 同源（两份真相迟早会说不一样的那一族）")
    mine = [str(p) for p in cf.log_dirs()]
    theirs = [str(p) for p in G._fatal_log_dirs()]
    _assert_eq(mine, theirs, "A1 候选目录逐条相同（顺序也算）")
    _assert(_home_ok(), "A2 本轮 HOME 已钉到临时目录（不往真实桌面写字节）")
    # report_path 取第一个存在的目录；桌面不在时应退到用户目录
    p = cf.report_path()
    _assert(str(p).startswith(_TMP_HOME), f"A3 落点在临时 HOME 内：{p}")
    desk = Path(_TMP_HOME) / "Desktop"
    _assert(not desk.is_dir() or str(p) == str(desk / cf.CRASH_LOG_NAME),
            "A4 桌面存在就优先桌面（用户自己找得到并发得出来）")
    desk.mkdir(parents=True, exist_ok=True)
    _assert_eq(str(cf.report_path()), str(desk / cf.CRASH_LOG_NAME),
               "A5 桌面建好后当场改指桌面（不是启动时算一次就钉死）")


# ── B. 永不抛 & 静默失灵必须问得出来 ───────────────────────────────────────

def section_b():
    print("\n[B] 取证装置自己不许变成第二个故障，也不许悄悄失灵")
    cf.reset_for_tests()
    # B1 写入站点全部不可用：只许返回一句话，不许抛
    real_write, real_dirs = cf._write, cf.log_dirs
    boom = [None]

    def _raise(_text):
        raise OSError(30, "Read-only file system")
    cf._write = _raise
    try:
        out = cf.report("B1 故意让写入失败", ValueError("示例"), where="section_b")
        _assert(not boom[0] and "取证写入失败" in out, f"B1 写不进去也只返回一句话：{out[:40]}")
        _assert_eq(cf.diagnostics()["hook_errors"], 1,
                   "B2 「写不进去」必须被计数（静默失灵才是真风险）")
    except BaseException as e:            # noqa: BLE001 - 这里就是要抓到底
        _assert(False, f"B1/B2 取证装置把异常抛了出来：{type(e).__name__}: {e}")
    finally:
        cf._write = real_write
    cf.reset_for_tests()
    # B3 目录候选整条为空（Home 都被挡）也不许抛
    cf.log_dirs = lambda: []
    try:
        _assert(cf.report_path().name == cf.CRASH_LOG_NAME,
                "B3 候选目录为空时仍返回一个可说的路径，而不是 IndexError")
    except BaseException as e:            # noqa: BLE001
        _assert(False, f"B3 落点计算抛了：{type(e).__name__}: {e}")
    finally:
        cf.log_dirs = real_dirs
    # B3b enable_faulthandler 走的是同一句「候选目录可能为空」：它以前在 try **外面**
    # 直接 log_dirs()[0]，IndexError 会绕过它自己的兜底——同一个口径写两处只做一半等于没写。
    cf.log_dirs = lambda: []
    try:
        fh_ok, fh_info = cf.enable_faulthandler()
        _assert(fh_ok is False and bool(fh_info),
                f"B3b 目录为空时只说「取不到」、不抛：ok={fh_ok} info={str(fh_info)[:40]}")
    except BaseException as e:            # noqa: BLE001
        _assert(False, f"B3b enable_faulthandler 抛了：{type(e).__name__}: {e}")
    finally:
        cf.log_dirs = real_dirs
    # B4 幂等：install 两次不叠钩子
    d1, d2 = cf.install(), cf.install()
    _assert_eq(d1, d2, "B4 install 幂等（两次 diagnostics 逐字相同）")
    _assert(sys.excepthook is cf._hook, "B5 sys.excepthook 确实被换上去了")
    _assert(threading.excepthook is cf._thread_hook, "B6 threading.excepthook 也换上了")
    # B7/B8 reset 必须把钩子**真的摘下来**：只清 `_INSTALLED` 标记的话，下一次 install()
    # 会把还挂在那里的 `_hook` 当成「上一任」存下来 → 链成环（N 段就是这一族的回归点）。
    # 这里判的是安装态本身，N 段判的是最终效果：两个口径都钉住，改任一守卫都会红。
    cf.reset_for_tests()
    _assert(sys.excepthook is not cf._hook,
            "B7 reset 之后钩子真的摘下来了（只清标记 = 下一次装成环）")
    _assert(threading.excepthook is not cf._thread_hook,
            "B8 threading 那一路同样摘干净")


# ── C. 一次 report 只出声一次（队列语义） ──────────────────────────────────

def section_c():
    print("\n[C] 出声通路只有一条：入队、取完即清、测试起点一致")
    cf.reset_for_tests()
    _assert_eq(cf.drain_pending(), [], "C1 reset 之后队列是空的（上一版没清，判据会看见别人家残留）")
    cf.report("C2 一次异常", ZeroDivisionError("除零"), where="section_c")
    got = cf.drain_pending()
    _assert_eq(len(got), 1, "C2 一次 report 只入队一条")
    _assert(got and got[0][0] == "C2 一次异常", f"C3 入队内容就是那句 kind：{got}")
    _assert_eq(cf.drain_pending(), [], "C4 取完即清（不许让界面第二次念同一件事）")
    # C5 slot_guard 里失败也只出声一次：上一版 report 之外又调了一遍 reporter，同一件事说两遍
    @cf.slot_guard("C5 被包的槽")
    def _boom():
        raise ValueError("槽里坏了")
    cf.reset_for_tests()
    _assert(_boom() is cf.NEVER_RETURNS, "C5 槽里的异常不许逃逸（逃逸=abort）")
    _assert_eq(len(cf.drain_pending()), 1,
               "C6 slot_guard 不额外出声（护栏只留证据，不重复播报）")
    # C7 notify=False：只要证据不要打扰（open_report 补现场就是这一路）
    cf.reset_for_tests()
    cf.report("C7 只落证据", None, where="x", notify=False)
    _assert_eq(cf.drain_pending(), [], "C7 notify=False 不入队")
    _assert(cf.report_path().exists(), "C8 但证据仍然写了")
    # C9 队列封顶：取证装置不许自己变成内存增长点
    cf.reset_for_tests()
    for i in range(200):
        cf.report(f"C9 第 {i} 次", None, where="x")
    _assert_eq(len(cf.drain_pending()), 20, "C9 待取队列封顶 20 条（ maxlen 生效）")


# ── D. 抬头与脱敏：这份文件是会被用户整份发给我们的东西 ────────────────────

def section_d():
    print("\n[D] 抬头只报实测值，字段一律脱敏")
    cf.reset_for_tests()
    head = cf.header()
    _assert(f"platform={sys.platform}" in head, "D1 运行形态里写着 platform")
    _assert(f"pid={os.getpid()}" in head, "D2 pid 是实测的（不是 0 也不是猜）")
    _assert("可执行：" in head, "D3 解释器与可执行路径都在")
    # D4 账号只留后四位、设备只留前八位。这里不假设本机绑了哪个号（判据要能在 CI 跑），
    # 所以两条一起判：单独把脱敏函数算对 + 整段抬头里不能出现 11 位连续数字。
    _assert_eq(cf._mask_phone("15088131371"), "****1371", "D4 脱敏形状：只留后四位")
    _assert_eq(cf._mask_phone(None), "(未绑定)", "D5 没绑定就直说没绑定，不留空也不编造")
    _assert(not re.search(r"\d{11}", head), "D6 整段抬头里没有完整手机号（发出去也不会泄露）")
    _assert(not re.search(r"[0-9a-f]{32}", head), "D7 抬头里没有 32 位 session 形状的值")
    # D8 密钥字段名整条略去，且**必须在收口之前判**：截短会让关键字掉出检测窗
    long_secret = ("ACCESS_KEY=" + "a" * 400)
    cf.note("D8 带密钥的一步", 参数=long_secret)
    b = cf.breadcrumbs()[-1]
    _assert("已按密钥字段名略去" in b, f"D8 密钥值整条略去：{b[:60]}")
    _assert("ACCESS_KEY=aaa" not in b, "D9 略去就是略去（不留 key=value 的形状）")
    # D10 超长值走把手而不是字符硬切：断在标点/词边界上，宁长勿截
    long_err = "ERR_CONNECTION_RESET " * 40
    cf.note("D10 超长的一步", 详情=long_err)
    line = cf.breadcrumbs()[-1]
    _assert("ERR_CONNECTION_RES " not in line,
            "D10 不许出现半截英文标识（念半截名字会让人查错方向）")
    _assert(line.count("ERR_CONNECTION_RESET") < long_err.count("ERR_CONNECTION_RESET"),
            "D11 超长确实被收了（不是原样塞进面包屑）")
    # D12 面包屑有条数上限
    cf.reset_for_tests()
    for i in range(cf.BREADCRUMB_CAP + 50):
        cf.note(f"D12 第 {i} 步")
    _assert_eq(len(cf.breadcrumbs()), cf.BREADCRUMB_CAP,
               "D12 面包屑只留最近这些条（取证文件不许长成读不动的体量）")
    # D13 内存行不许引入 psutil：本仓交付态只用标准库（data_pool 里立过的规矩）
    src = Path(__file__).with_name("crash_forensics.py").read_text(encoding="utf-8")
    _assert("psutil" not in re.sub(r"#[^\n]*|\"\"\".*?\"\"\"", "", src, flags=re.S),
            "D13 取证模块不 import psutil（注释里写的是「不引它」这句规矩，不是依赖）")
    _assert("perf_governor" in src, "D14 Windows 内存探测复用既有把手，不另写一份")
    mem = cf._mem_line()
    _assert("未取" in mem or "GB" in mem, f"D15 取不到也要说实话：{mem}")


# ── E. 线程栈：滤掉自家帧，否则每份报告都在自报家门 ────────────────────────

def section_e():
    print("\n[E] 此刻每一根线程的栈：要的是现场，不是取证装置自己")
    cf.reset_for_tests()
    stop = threading.Event()

    def _business():
        stop.wait(2.0)
    t = threading.Thread(target=_business, name="cf-business")
    t.start()
    try:
        text = cf._stack_of_threads()
        # 判「某个函数名出现在文本里」不算数：traceback 会把那一行的**源码**一起印出来，
        # 所以只要源码里提过这个名字，即使栈被滤坏了判据也照样绿（N2 就是被同一个形状骗了
        # 一整轮）。这里只认帧头 `..., line N, in _business`——它只能由真的拍到那一帧产生。
        _assert("in _business" in text,
                "E1 后台线程此刻卡在哪个函数被记下来了（找不到就是快照不可用）")
        _assert(not [f for f in _frame_files(text)
                     if Path(f).name == "crash_forensics.py"],
                "E2 没有一帧落在取证模块上（上一版没滤：每份报告这一段都是三行自报家门）")
        _assert(any(Path(f).name == os.path.basename(__file__) for f in _frame_files(text)),
                "E3 真正在跑的调用方帧保留着（滤错方向比不滤更糟：现场会被滤掉）")
        _assert("线程" in text and "──" in text, "E4 每根线程有分隔行")
    finally:
        stop.set()
        t.join(3)
    # E5~E7 真现场：解释器把异常交给 excepthook 时，业务栈已经展开，当前线程上只剩
    # 取证装置自己的帧——直接调 `_stack_of_threads()` 模拟不出来（本节自己还在栈上），
    # 必须走 `_hook` 那一路。这里同时把旧钩子换成空函数：否则测试输出里多一段 traceback，
    # 下一个读日志的人会以为真崩了一次。
    _fresh()
    cf.install()
    box = {}

    def _capture():
        try:
            raise RuntimeError("E5 栈捕获")
        except RuntimeError:
            box["exc"] = sys.exc_info()
    _capture()
    old_hook = cf._OLD_EXCEPTHOOK
    cf._OLD_EXCEPTHOOK = lambda *a: None
    try:
        cf._hook(*box["exc"])
    finally:
        cf._OLD_EXCEPTHOOK = old_hook
    text = cf.report_path().read_text(encoding="utf-8")
    _assert("未捕获异常（解释器钩子）" in text, "E5 钩子留下的段落有个认得出的标题")
    # E6/E7 本轮实测把上一版的推测纠正了一处：解释器走 excepthook 时，当前线程的栈并
    # 不是「只剩取证装置」——调用方那一帧（这里是本套件）还在，所以真正要保证的是两件事：
    # 业务帧不许被滤掉，自家帧不许混进来。「只剩自家帧」那一句文案只是兜底，所以单独
    # 按源码判它在不在（有人把它删成留个光溜溜的分隔行，这份报告就难读了）。
    stack_part = text.split("此刻每一根线程的栈")[-1]
    _assert(any(Path(f).name == os.path.basename(__file__)
                for f in _frame_files(stack_part)),
            "E6 现场里能看到出事时正在跑的业务帧（被滤掉就是取证不可用）")
    _assert(not [f for f in _frame_files(stack_part)
                 if Path(f).name == "crash_forensics.py"],
            "E7 取证装置自己的帧不混进现场（上一版三行自报家门的噪音就在这）")
    _assert("本线程此刻只在取证装置内" in
            Path(__file__).with_name("crash_forensics.py").read_text(encoding="utf-8"),
            "E8 栈真为空时那句「为何为空」的文案还在（兜底分支不许被删成空分隔行）")
    # 同样只认崩溃点那一句的格式（`在 <函数名>()`）而不是裸函数名：裸名字在源码行里也会出现，
    # 那种判法连「where 字段被写成了取证装置自己」都报不出来。
    _assert("位置：崩溃点：" in text and "在 _capture()" in text,
            "E9 崩溃点指向真实函数（不是取证装置自己，也不是光写一个 where）")
    _assert(cf._crash_site(None) == "", "E10 没有 traceback 时返回空串而不是抛")


# ── F. slot_guard：跨线程 queued 槽的异常绝不许逃逸 ─────────────────────────

def section_f():
    print("\n[F] slot_guard：把 abort 换成「看得见的一条错」")
    cf.reset_for_tests()

    @cf.slot_guard("F1 普通异常")
    def _f1():
        raise TypeError("类型不对")
    _assert_eq(_f1(), cf.NEVER_RETURNS, "F1 异常被接住，返回本模块统一的 None")

    @cf.slot_guard("F2 有返回值")
    def _f2():
        return 42
    _assert_eq(_f2(), 42, "F2 正常路径原样返回（护栏不许吃掉返回值）")

    @cf.slot_guard("F3 不该接的异常")
    def _f3():
        raise KeyboardInterrupt
    _assert_raises(KeyboardInterrupt, _f3, "F3 KeyboardInterrupt 照抛（不是本槽该替全进程作的决定）")

    @cf.slot_guard("F4 不该接的异常")
    def _f4():
        raise SystemExit(7)
    _assert_raises(SystemExit, _f4, "F4 SystemExit 照抛")

    # F5 元数据：判据与调试都要能拿到原函数，__wrapped_slot__ 是这条契约的把手
    @cf.slot_guard("F5 身份")
    def _original(a, b):
        """文档"""
        return a + b
    _assert(callable(_original.__wrapped_slot__) and _original.__wrapped_slot__ is not _original,
            "F5 __wrapped_slot__ 指向被包之前的原函数")
    _assert_eq(_original.__wrapped_slot__(1, 2), 3, "F6 原函数自己还能调（套护栏不丢能力）")
    _assert_eq(_original(1, 2), 3, "F7 装饰后仍可用（functools.wraps 保住签名）")

    # F8 取证内容真的落盘：kind、异常类型、崩溃点、面包屑四样都要在
    _fresh()
    cf.note("F8 崩前最后一步")

    @cf.slot_guard("F8 渲染结果表格")
    def _f8():
        QTableWidgetItem(13800000000)      # 需求 8 的真实打穿点
    _f8()
    text = cf.report_path().read_text(encoding="utf-8")
    _assert("OverflowError" in text, "F8 异常类型逐字进取证文件")
    _assert("F8 崩前最后一步" in text, "F9 面包屑跟着进去了（最后一步就是定位线索）")
    _assert("Traceback (most recent call last)" in text and "_f8" in text,
            "F10 异常段里带着完整栈与出事那一行的函数名")
    _assert("此刻每一根线程的栈" in text, "F11 全线程栈在报告里")
    # F12 报告里必须带着「是哪一句」：kind 与 where 两个字段各说一件事，都不能缺
    _assert("F8 渲染结果表格" in text and "_f8" in text,
            "F12 kind 说业务步骤、函数名说代码位置，两段都在")


def _assert_raises(exc_type, fn, msg):
    global _PASS, _FAIL
    try:
        fn()
    except exc_type:
        _PASS += 1
        print(f"  ✅ {msg}")
        return
    except Exception as e:            # noqa: BLE001
        _FAIL += 1
        print(f"  ❌ {msg}（抛的是 {type(e).__name__}: {e}）")
        return
    _FAIL += 1
    print(f"  ❌ {msg}（没抛异常）")


# ── G. 后台线程：abort 换成「一句实话 + 一次 finished」 ────────────────────

def _mk_worker(cls, *args, **kw):
    return cls(*args, **kw)


def _run_exploding(worker, exc):
    """把 _body 换成「抛这个异常」，然后在**当前线程**直接跑 run()。

    不用 start()：起真线程会把断言变成时序问题（emit 是 queued 的话要跑事件循环），
    而这里要验的是 run() 这一个方法体的接住/递回逻辑，与它在哪根线程上跑无关。
    唯一必须承认的差别是 RuntimeError 的语义——它本来就代表「界面已销毁」，见 G4。
    """
    def _body():
        raise exc
    worker._body = _body
    got = []
    worker.finished.connect(lambda *a: got.append(a))
    worker.run()
    return got


def section_g():
    print("\n[G] WorkerBase.run：异常不再逃逸成 SIGABRT，也不留下永久禁用的按钮")
    _fresh()
    # G1 SearchWorker：ValueError 必须既落证据又递回 finished(False, [], 实话)
    w = _mk_worker(pd.SearchWorker, "光伏组件", [])
    got = _run_exploding(w, ValueError("召回那一步坏了"))
    _assert_eq(len(got), 1, "G1 搜索线程失败后 finished 恰好发一次（不发=按钮永久禁用=「卡死」）")
    _assert(got and got[0][0] is False and got[0][1] == [],
            f"G2 载荷形状对得上信号签名 (bool, list, str)：{got}")
    _assert(got and "搜索未完成" in got[0][2] and "召回那一步坏了" in got[0][2],
            f"G3 文案说出是哪件事、为什么：{got[0][2] if got else None}")
    text = cf.report_path().read_text(encoding="utf-8")
    _assert("后台任务异常：搜索采购商" in text, "G4 取证里写着人话的任务名（不是类名）")
    _assert("SearchWorker._body" in text, "G5 取证里的 where 指向 _body，不是 run")
    # G6 另外两个 worker 各自的重写真的生效（签名不同，基类不许猜）
    for cls, label, kw in ((pd.LoadMasterWorker, "加载数据池", dict(mgr=object())),
                           (pd.RecommendWorker, "智能推荐", dict(mgr=object()))):
        _fresh()
        if cls is pd.LoadMasterWorker:
            w2 = _mk_worker(cls, **kw)
        else:
            w2 = _mk_worker(cls, "similar", {}, [], **kw)
        got2 = _run_exploding(w2, TypeError("精排坏了"))
        _assert(got2 and got2[0][0] is False, f"G6 {label}：失败也递回 finished")
        _assert(label in cf.report_path().read_text(encoding="utf-8"),
                f"G7 {label}：任务名各自重写到位（共用基类默认就是类名，读的人分不清）")
    # G8 RuntimeError 走的是另一条既有支路：界面已销毁，结果不再递送
    _fresh()
    w3 = _mk_worker(pd.SearchWorker, "kw", [])
    got3 = _run_exploding(w3, RuntimeError("wrapped C/C++ object has been deleted"))
    _assert_eq(got3, [], "G8 RuntimeError 不递回（接收者已经没了，递了也是抛）")
    # G9 但绝不许写进取证文件当崩溃：那是正常收尾，不是故障
    _assert("后台任务异常" not in cf.report_path().read_text(encoding="utf-8"),
            "G9 界面先走不被报成后台崩溃（两种成因的修法完全相反）")
    # G10 KeyboardInterrupt / SystemExit 照旧逃逸：本线程不替全进程作决定
    for exc in (KeyboardInterrupt(), SystemExit(9)):
        w4 = _mk_worker(pd.SearchWorker, "kw", [])
        try:
            _run_exploding(w4, exc)
            _assert(False, f"G10 {type(exc).__name__} 被接住了（不该）")
        except type(exc):
            _assert(True, f"G10 {type(exc).__name__} 照旧逃逸")
    # G11 finally 里摘干净：挂着就是「关掉窗口再没人等它」那一族
    w5 = _mk_worker(pd.SearchWorker, "kw", [])
    _run_exploding(w5, ValueError("收尾"))
    _assert(w5 not in pd._RUNNING_WORKERS, "G11 跑完即从 _RUNNING_WORKERS 摘下")
    # G12 空消息的异常也要说得出（旧写法 str(e) 为空 = 文案只剩「搜索未完成: 」）
    _assert(pd._worker_fail_reason(ValueError("")).strip() != "",
            "G12 空异常消息不会产出空文案")


# ── H. 类型把手：需求 8 的唯一口径 ─────────────────────────────────────────

def _item_text(value):
    """真构造一个 QTableWidgetItem 并读回它显示的字——「不抛」和「显示得对」是两件事。"""
    return QTableWidgetItem(pd.cell_text(value)).text()


def section_h():
    print("\n[H] cell_text / cell_join：崩的那一处与静默空白的那一处，一次验两样")
    big = 13800000000                      # 11 位手机号：> int32，直接递给 QTableWidgetItem 招 OverflowError
    # H1 溢出这一族：过把手之后能构造、且显示的就是号码本身
    try:
        QTableWidgetItem(big)
        _assert(False, "H1 基线不成立：这个环境里 QTableWidgetItem(int) 没崩（判据要跟着改口径）")
    except OverflowError:
        _assert(True, "H1 基线坐实：QTableWidgetItem(13800000000) 就是 OverflowError")
    except Exception as e:                 # noqa: BLE001
        _assert(False, f"H1 基线抛的是另一种：{type(e).__name__}: {e}")
    _assert_eq(_item_text(big), "13800000000", "H2 过把手后不但不崩，字还是对的")
    # H3 静默空白这一族（比崩更难发现）：小整数被当成 Type 枚举，text 是空的
    raw = QTableWidgetItem(123)
    _assert_eq(raw.text(), "", "H3 基线坐实：QTableWidgetItem(123) 不崩但显示空白")
    _assert_eq(_item_text(123), "123", "H4 过把手后 123 显示成「123」而不是什么都没有")
    # H5~H10 各种形状
    cases = [(None, ""), (True, "是"), (False, ""), (1.38e10, "13800000000"),
             (float("nan"), ""), (float("inf"), ""), (3.14, "3.14"),
             (b"abc", "abc"), ("正常串", "正常串"), ([1, 2], None), ({"a": 1}, None)]
    for value, expect in cases:
        out = _item_text(value)
        if expect is None:
            _assert(out != "" and "无法显示" not in out,
                    f"H5 容器形状不许报「无法显示」也不许空白：{value!r} -> {out!r}")
        else:
            _assert_eq(out, expect, f"H6 {type(value).__name__}({value!r}) 的口径")
    # H7 numpy/pandas 标量：只认 .item() 这一条既有把手路径，不引第三方
    class _Scalar:
        def item(self):
            return 13800000000
    _assert_eq(_item_text(_Scalar()), "13800000000", "H7 带 .item() 的标量按值取（不写成对象 repr）")

    class _BadItem:
        def item(self):
            raise ValueError("0-d array only")
    _assert(_item_text(_BadItem()) != "", "H8 .item() 自己抛时降级到 str()，不跟着崩")
    # H9 cell_join 的往返等式：全 str 时必须与手拼逐字相同（第一版我写成 sep(生成器)，
    #     TypeError 被「绝不抛」吞掉 → 永远返回空串：防御性 except 把自己的缺陷也藏了）
    strs = ["a@b.com", "13800000000", "0755-1"]
    _assert_eq(pd.cell_join(strs), ", ".join(strs), "H9 cell_join 对纯字符串逐字等于手拼")
    _assert_eq(pd.cell_join([13800000000, "0755-12345678", None, 12]),
               "13800000000, 0755-12345678, 12",
               "H10 混 int/None 的号码列表：不抛、不空白、空项被丢掉")
    _assert_eq(pd.cell_join([]), "", "H11 空列表返回空串")
    _assert_eq(pd.cell_join(None), "", "H12 None 当列表用也返回空串")
    _assert_eq(pd.cell_join(["a", 13800000000], sep=" ｜ "), "a ｜ 13800000000",
               "H13 自定义分隔符生效（只有一个元素时 join 本来就不加分隔符）")
    # H14 first_line 的 ㉓ 族纪律仍然只有一份把手：cell_text 不许自己另写截断
    _assert("first_line" in Path(__file__).with_name("text_shape.py").read_text(encoding="utf-8"),
            "H14 收口与截断同在本文件（第 ㉓ 族那扇门才扫得到）")


# ── I. _fill_table：坏行只坏一行 ───────────────────────────────────────────

class _Think:
    def __init__(self):
        self.lines = []

    def append(self, text, *a, **kw):
        self.lines.append(str(text))


def _mk_dialog():
    """轻量 SearchDialog：按本仓既有配方 __new__ + QDialog.__init__，不跑重量级构造。

    重量级构造会读数据池、建十几个标签页——那既让判据变慢，也会把「渲染表格」这一件事
    的失败原因混进无关的初始化里。这里只挂 _fill_table 真正用到的那几个控件。
    """
    from PyQt5.QtWidgets import QDialog, QToolButton, QWidget   # noqa: F401
    dlg = pd.SearchDialog.__new__(pd.SearchDialog)
    QDialog.__init__(dlg)
    dlg.table = QTableWidget(0, 9)
    dlg.chk_select_all = QCheckBox()
    dlg.lbl_selected = QLabel()
    dlg.chk_mask = QCheckBox()
    dlg.chk_mask.setChecked(False)
    dlg._think = _Think()
    dlg._append_think = dlg._think.append
    dlg._results = []
    dlg._all_results = []
    return dlg


def _cell(dlg, row, col):
    it = dlg.table.item(row, col)
    return it.text() if it is not None else None


def section_i():
    print("\n[I] 渲染结果表格：一条脏数据不再让整张表陪葬")
    _fresh()
    good = {"company": "甲公司", "contacts": [{"name": "张三"}],
            "mobiles": [13800000000], "phones": ["0755-12345678"],
            "emails": ["a@b.com"], "wechats": [12345],
            "products": "光伏组件", "source": "1688", "_score": 0.85}
    good2 = {"company": "乙公司", "mobiles": [], "_score": "50%"}
    cf.reset_for_tests()
    dlg = _mk_dialog()
    recs = [good, "一条不是字典的记录", good2]
    try:
        dlg._fill_table(recs)
        _assert(True, "I1 混着 int 号码与非字典记录，槽里没有异常逃逸（逃逸=abort）")
    except BaseException as e:            # noqa: BLE001
        _assert(False, f"I1 渲染把异常抛出来了：{type(e).__name__}: {e}")
        return
    _assert_eq(dlg.table.rowCount(), 3, "I2 行数等于记录数（坏记录不减行）")
    _assert_eq(_cell(dlg, 0, 1), "甲公司", "I3 正常行照常显示")
    _assert_eq(_cell(dlg, 0, 3), "13800000000", "I4 int 号码显示成号码本身")
    _assert_eq(_cell(dlg, 0, 2), "张三", "I5 dict 形状的联系人读得出名字")
    _assert_eq(_cell(dlg, 0, 7), "85%", "I6 匹配度按百分比显示")
    _assert_eq(_cell(dlg, 2, 7), "50%", "I7 字符串「50%」不再被当数字格式化（旧写法 f'{\"85%\":.0%}' 直接抛）")
    _assert_eq(_cell(dlg, 1, 1), "一条不是字典的记录",
               "I8 非字典记录按「只有一个显示名的伪记录」渲染，不空不炸")
    _assert("非字典记录" in "".join(cf.breadcrumbs()), "I9 这一行在面包屑里留了名（少一行看得见原因）")
    # I9b/I9c 匹配度的坏值与百分数写法（单独重渲染：上面那张表已经有三行，这里只看一行）
    # 取值域已核：pool_matcher 产 round(total, 4)，所以「大于 1」只可能是 85 这种漏写百分号
    dlg._fill_table([{"company": "丙公司", "_score": "不确定"}])
    _assert_eq(_cell(dlg, 0, 7), "0%", "I9b 坏匹配度读成 0%（不抛、不印出怪字）")
    dlg._fill_table([{"company": "丁公司", "_score": 85}])
    _assert_eq(_cell(dlg, 0, 7), "85%", "I9c 「85」这种百分数漏写百分号的读成 85% 而不是 8500%")
    # I10 单行坏掉：其余行必须还在（旧写法整张表只有一层护栏 → 一条坏记录 = 100 行全丢）
    real_join = pd.cell_join
    sentinel = {"company": "丙公司", "mobiles": ["__boom__"]}
    sentinel2 = {"company": "丁公司", "mobiles": ["__boom2__"]}

    def _boom(values, sep=", "):
        items = [str(x) for x in (values or [])]
        if "__boom__" in items:
            raise TypeError("这一行的号码形状没猜到")
        if "__boom2__" in items:
            raise ValueError("")          # 空消息：占位必须回退到异常类型名
        return real_join(values, sep)
    pd.cell_join = _boom
    try:
        _fresh()
        dlg._fill_table([good, sentinel, sentinel2, good2])
        _assert_eq(dlg.table.rowCount(), 4, "I10 坏行不减少行数（表还在，只是那一行标出来）")
        _assert_eq(_cell(dlg, 0, 1), "甲公司", "I11 坏行之前的行完好")
        _assert_eq(_cell(dlg, 3, 1), "乙公司", "I12 坏行之后的行也完好（整表护栏做不到这件事）")
        _assert(_cell(dlg, 1, 1) and "这一行显示不了" in _cell(dlg, 1, 1),
                f"I13 坏行有可见占位而不是空白：{_cell(dlg, 1, 1)!r}")
        _assert("这一行的号码形状没猜到" in (_cell(dlg, 1, 1) or ""),
                "I14 占位里带着原因（了无痕迹的少一行比崩溃更难查）")
        _assert("ValueError" in (_cell(dlg, 2, 1) or ""),
                f"I14b 空消息的异常回退到类型名，占位不能只剩一句空话：{_cell(dlg, 2, 1)!r}")
        think = "｜".join(dlg._think.lines)
        _assert("有 2 行没能显示出来" in think, f"I15 思考面板如实报数：{think[-60:]}")
        rep = cf.report_path().read_text(encoding="utf-8")
        _assert("渲染结果表格第 2 行" in rep and "渲染结果表格第 3 行" in rep,
                "I16 取证文件逐行点名（行号按人的数法，从 1 起）")
    finally:
        pd.cell_join = real_join
    # I17 脱敏开关对 int 号码同样有效（勾选后重渲染）
    _fresh()
    dlg.chk_mask.setChecked(True)
    dlg._fill_table([good])
    _assert_eq(_cell(dlg, 0, 3), "138****0000", "I18 脱敏后的 int 号码是掩码形状")
    dlg.chk_mask.setChecked(False)


# ── J. 静态门：每个取值点都过把手（咬得住反例、也不多咬） ──────────────────

_HANDLES = {"cell_text", "cell_join", "_as_text_list", "_as_list_keep_dict",
            "_contact_names", "_score_of", "_worker_fail_reason",
            "_mask_phone", "_mask_email", "_mask_wechat"}


def _raw_rec_reads(code: ast.AST):
    """找出「递给 QTableWidgetItem 的值里，没被把手包住的 rec.get(...)」。"""
    bad = []
    parents = {}
    for node in ast.walk(code):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    def _wrapped_by_handle(node):
        cur = parents.get(node)
        while cur is not None:
            if isinstance(cur, ast.Call):
                f = cur.func
                name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
                if name in _HANDLES:
                    return True
            cur = parents.get(cur)
        return False

    for node in ast.walk(code):
        if not (isinstance(node, ast.Call)
                and getattr(node.func, "id", getattr(node.func, "attr", "")) == "QTableWidgetItem"):
            continue
        arg = node.args[0] if node.args else None
        if arg is None:
            continue
        for sub in ast.walk(arg):
            if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                    and sub.func.attr == "get" and isinstance(sub.func.value, ast.Name)
                    and sub.func.value.id == "rec" and not _wrapped_by_handle(sub)):
                bad.append(getattr(sub.func.value, "lineno", 0))
    return bad


def section_j():
    print("\n[J] 静态门：渲染表格的每一个取值点都必须过把手")
    src = Path(__file__).with_name("pool_dialogs.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_fill_table"), None)
    _assert(fn is not None, "J1 找得到 _fill_table（找不到=这条门是空的）")
    if fn is None:
        return
    _assert_eq(_raw_rec_reads(fn), [], "J2 现行代码里没有被裸 rec.get 递给 QTableWidgetItem 的取值点")
    # J3 反向对照：这条门必须咬得动「漏过一个取值点」那种改动
    sample = ("def _fill_table(self, results):\n"
              "    for rec in results:\n"
              "        self.table.setItem(0, 1, QTableWidgetItem(rec.get('company')))\n")
    hits = _raw_rec_reads(ast.parse(sample))
    _assert_eq(len(hits), 1, f"J3 反例被咬住（漏一个就报一个）：{hits}")
    # J4 也不多咬：同样一句但过了把手，必须放行
    ok_sample = ("def _fill_table(self, results):\n"
                 "    for rec in results:\n"
                 "        self.table.setItem(0, 1, QTableWidgetItem(cell_text(rec.get('company'))))\n")
    _assert_eq(_raw_rec_reads(ast.parse(ok_sample)), [], "J4 过把手的写法不误报")
    # J5 装饰器登记：queued 槽必须都套上 slot_guard（漏一个就是下一场 abort）
    sd = next((n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef) and n.name == "SearchDialog"), None)
    guarded = set()
    for m in (sd.body if sd else []):
        if isinstance(m, ast.FunctionDef):
            for d in m.decorator_list:
                if (isinstance(d, ast.Call)
                        and getattr(d.func, "attr", "") == "slot_guard"):
                    guarded.add(m.name)
    for name in ("_on_master_loaded", "_on_think_step", "_on_mask_toggled",
                 "_update_analytics", "_on_search_finished", "_fill_table",
                 "_on_recommend_finished"):
        _assert(name in guarded, f"J5 {name} 套了 slot_guard（queued 槽裸奔=异常直接 abort）")
    # J6 但 WorkerBase.run 不许被套上：它的异常必须由 run 自己接住并递回 finished，
    #     用护栏一包就只剩「什么都没发生」——那是把「卡死」从界面搬进静默。
    wb = next((n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef) and n.name == "WorkerBase"), None)
    run_m = next((m for m in (wb.body if wb else [])
                  if isinstance(m, ast.FunctionDef) and m.name == "run"), None)
    _assert(run_m is not None and not run_m.decorator_list,
            "J6 WorkerBase.run 保持裸的（靠自己的 except + _deliver_failure，不靠护栏）")


# ── K. 入库与脱敏同源：脏类型在根上就堵住 ─────────────────────────────────

def section_k():
    print("\n[K] 入库清洗：脏类型不许靠每个显示点各自补救")
    from ingest_cleaning import clean_records
    recs = [{"company": "甲公司", "mobiles": [13800000000], "landlines": [7551234],
             "phones": [13800000000], "email": "a@b.com"},
            {"company": "乙公司", "mobiles": "13900000001", "landlines": None,
             "phones": ["020-12345678"]}]
    # K0 先把签名钉住：`clean_records` 返回的是 (记录, 报告) 二元组。上一版把它当
    # 记录列表用，于是 len(out)==2 这条判据在「两条记录」和「元组有两个元素」上**都是**
    # 绿的——假绿判据比没判据更坏：它让人以为清洗结果被验过了。
    result = clean_records(recs)
    _assert(isinstance(result, tuple) and len(result) == 2,
            f"K0 返回形状是 (记录, 报告)，签名改了要当场说出来：{type(result).__name__}")
    out, rep = result
    _assert_eq(len(out), 2, "K1 两条都留下（清洗不许把数据洗没了）")
    _assert_eq(rep["in"], 2, "K1b 报告里的入库前条数与传入一致")
    for key in ("mobiles", "landlines", "phones"):
        vals = [v for r in out for v in (r.get(key) or [])]
        _assert(all(isinstance(v, str) for v in vals),
                f"K2 {key} 入库后逐项都是 str（实际 {vals}）——显示侧才有唯一口径")
        _assert("13800000000" not in vals or all(isinstance(v, str) for v in vals),
                f"K3 {key} 里没有裸 int 留在库里")
    # K4 标量单值列（Excel 常见形状）不再变成逐字符拆开的列表
    _assert(out[1]["mobiles"] == ["13900000001"],
            f"K4 单值字符串列还是一列一项：{out[1]['mobiles']}")
    # K4b~K4e 收口的四条边：数字标量、记账、不碰已经是列表的值、列表逐项口径
    from ingest_cleaning import clean_one
    try:
        cleaned, reason, fixes = clean_one({"company": "甲公司", "mobiles": 13900000001})
    except BaseException as e:            # noqa: BLE001
        # 旧写法在这里是 `for x in (13900000001 or [])` → TypeError。判据必须把它报成一
        # 条红而不是让整套崩掉：崩了只能得到「变异不成立」，拿不到干净的红可归因。
        _assert(False, f"K4b 数字标量清洗直接抛了：{type(e).__name__}: {e}")
        cleaned, reason, fixes = None, "", {}
    _assert(cleaned is not None, f"K4b 数字标号不再因为洗碎片而变非法记录：{reason}")
    if cleaned is not None:
        _assert_eq(cleaned["mobiles"], ["13900000001"], "K4b 数字标量同样是一项不是十一个字符")
    _assert_eq(fixes.get("列表列标量收口"), 1,
               "K4c 改了数据就要记账（静默改数据与静默改坏是同一种事故）")
    cleaned2, _, fixes2 = clean_one({"company": "乙公司", "mobiles": [13800000000, "13900000001"]})
    _assert("列表列标量收口" not in fixes2,
            "K4d 已经是列表的不重复加一道（只堵标量被逐字拆开这一条，不改现有语义）")
    if cleaned2 is not None:
        _assert_eq(cleaned2["mobiles"], ["13800000000", "13900000001"],
                   "K4e 列表里的 int 逐项收口成 str，顺序与条数不变")
    # K5 脱敏函数对 int 号码同样不出错（与把手共用同一口径，不各写一份 str()）
    _assert_eq(pd._mask_phone(13800000000), "138****0000", "K5 脱敏接 int 不抛且形状对")
    _assert_eq(pd._mask_phone(None), "", "K6 None 脱敏是空串不是 'None'")
    _assert_eq(pd._mask_email(12345), "12345", "K7 没有 @ 的值原样说出，不编造掩码")
    _assert_eq(pd._mask_wechat(None), "", "K8 微信脱敏同样接 None")


# ── L. 界面提示通路：报信不许变成下一场崩溃，也不许抢别人的横幅 ───────────

class _Stub:
    """只借 MainWindow 那两个方法体，不建整个窗口（建窗口要读数据池、起十几个定时器）。"""
    CRASH_NOTICE_TICK_MS = G.MainWindow.CRASH_NOTICE_TICK_MS
    CRASH_NOTICE_TTL_S = G.MainWindow.CRASH_NOTICE_TTL_S

    def __init__(self, banner_hidden=True):
        from PyQt5.QtWidgets import QLabel
        self.banner = QLabel()
        self.banner.setVisible(not banner_hidden)
        self.logs = []
        self.shown = None
        self._crash_banner_live = False
        self._crash_last_notice_at = 0.0

    def _on_log(self, msg):
        self.logs.append(str(msg))

    def _show_banner(self, text, bg, fg):
        self.shown = (text, bg, fg)
        self.banner.setText(text)
        self.banner.setVisible(True)

    def _hide_banner(self):
        self.shown = None
        self.banner.setVisible(False)


def _drain(s):
    G.MainWindow._drain_crash_notices(s)


def section_l():
    print("\n[L] 界面提示：后台只入队、界面按节拍取，横幅绝不抢")
    _fresh()
    s = _Stub(banner_hidden=False)                 # 别人正占着横幅
    cf.report("L1 渲染结果表格第 3 行", ValueError("坏"), where="x")
    _drain(s)
    _assert(s.shown is None, "L1 横幅被别人占着时绝不抢（抢赢=把需求 6 的红线踩掉）")
    _assert(any("crash_report" in m for m in s.logs), "L2 但日志一定落地：证据不因为没出横幅就丢")
    _assert(not s._crash_banner_live, "L3 没贴就不算自己贴的")
    s2 = _Stub(banner_hidden=True)                 # 横幅空着
    _fresh()
    cf.report("L4 后台任务异常：搜索采购商", ValueError("a"), where="y")
    cf.report("L4 数据概览统计", ValueError("b"), where="z")
    _drain(s2)
    text = (s2.shown or [""])[0]
    _assert(s2._crash_banner_live, "L4 横幅空着就贴上（用户必须知道有这份证据存在）")
    _assert("#crash-open" in text, "L5 横幅带得出可点的出口（后台不许自己弹对话框，红线同 #acl-restart）")
    _assert("还有 1 条" in text, "L6 一批多条时只念最近一条并把剩余条数说出来，不逐条骚扰")
    _assert_eq(len(cf.drain_pending()), 0, "L7 取走即清：同一件事不会被念两遍")
    # L8 过期只收自己贴的那张
    s2._crash_last_notice_at = time.time() - (s2.CRASH_NOTICE_TTL_S + 5)
    _drain(s2)
    _assert(not s2._crash_banner_live and s2.shown is None, "L8 过期后收回自己的横幅（旧事不糊当下）")
    s3 = _Stub(banner_hidden=False)                # 别人家的横幅 + 过期
    s3.banner.setText("重启才能生效")
    s3._crash_last_notice_at = time.time() - (s3.CRASH_NOTICE_TTL_S + 5)
    _drain(s3)
    _assert_eq(s3.banner.text(), "重启才能生效", "L9 过期时不许动别人的横幅")
    _assert(not s3._crash_banner_live, "L10 没贴过就不隐藏（隐藏别人的提示是二次事故）")
    # L11 空转：既没新条目也没过期 → 一个字都不动、一条日志都不写
    s4 = _Stub(banner_hidden=True)
    _drain(s4)
    _assert(s4.logs == [] and s4.shown is None, "L11 平时空转不碰界面（这一定时器每两秒跑一次）")
    # L12 模块不在时不许引用不存在的名字（降级能用的意思就是每个名字都先判再引用）
    s5 = _Stub()
    real = G.HAS_CRASH_FORENSICS
    G.HAS_CRASH_FORENSICS = False
    try:
        _drain(s5)
        G.MainWindow._open_crash_report(s5)
        _assert(any("不在包里" in m for m in s5.logs), "L12 模块不在时说得出原因，而不是 NameError")
    except BaseException as e:                    # noqa: BLE001
        _assert(False, f"L12 降级路径抛了：{type(e).__name__}: {e}")
    finally:
        G.HAS_CRASH_FORENSICS = real
    # L13 锚点登记：横幅里出现的每一个 #动作 都必须有分派（加了链接忘了加分派=点了没反应）
    # 先去重再判：同一个锚点在源码里出现多次很常见，按出现次数报会把断言数按成一个信号。
    src = (_REPO / "b2bcollect_build" / "b2bcollect_gui.py").read_text(encoding="utf-8")
    for anchor in sorted(set(re.findall(r'href="(#[a-z\-]+)"', src))):
        _assert(f'link == "{anchor}"' in src, f"L13 锚点 {anchor} 在 _on_banner_link 里有分派")


# ── M. 启动顺序与打包登记 ─────────────────────────────────────────────────

def section_m():
    print("\n[M] 钩子必须在 QApplication 之前装好，模块必须真的进包")
    src = (_REPO / "b2bcollect_build" / "b2bcollect_gui.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    main_fn = next((n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "main"), None)
    _assert(main_fn is not None, "M1 找得到 main()")
    if main_fn is None:
        return
    install_ln = next((n.lineno for n in ast.walk(main_fn)
                       if isinstance(n, ast.Call) and _call_name(n) == "crash_forensics.install"), None)
    app_ln = next((n.lineno for n in ast.walk(main_fn)
                   if isinstance(n, ast.Call) and _call_name(n) == "QApplication"), None)
    begin_ln = next((n.lineno for n in ast.walk(main_fn)
                     if isinstance(n, ast.Call) and _call_name(n) == "_launch_record"), None)
    _assert(install_ln is not None, "M2 main() 里真的调了 install()（没调=取证装置只是个文件）")
    _assert(app_ln is not None, "M3 找得到 QApplication 构造（找不到=这条门的比较基准没了）")
    if install_ln and app_ln:
        _assert(install_ln < app_ln,
                f"M4 钩子早于 QApplication（{install_ln} < {app_ln}）：Qt 的 abort 发生在跑起来之后，"
                f"钩子是唯一还能在 abort 前把话留下来的东西")
    if install_ln and begin_ln:
        _assert(install_ln < begin_ln, "M5 台账 begin 那一行写在装钩子之后（状态得是真的）")
    _assert("取证=已装" in src and "取证=安装失败" in src and "取证=模块不在" in src,
            "M6 三种状态都说得出（装了钩子与钩子还能写是两件事）")
    # M7 三份清单都登记了 crash_forensics：pool_dialogs 在类定义时就用它做装饰器，
    #    漏登记不是「少一个提醒」而是开机 ModuleNotFoundError
    for label, rel in (("Windows workflow", ".github/workflows/build-b2bcollect.yml"),
                       ("Mac workflow", ".github/workflows/build-b2bcollect-mac.yml"),
                       ("本机 Mac 脚本", "debug/build_b2bcollect_mac_local.sh")):
        text = (_REPO / rel).read_text(encoding="utf-8")
        hidden = _hidden_imports(text)
        _assert("crash_forensics" in hidden, f"M7 {label} 的 --hidden-import 里有 crash_forensics")
    # M7b 反向对照：这条门必须咬得动「漏登记」——拿一份确实没有它的文本验一次，
    # 否则「名字在集合里」这句话在清单被改坏时也可能因为是空集而假绿。
    _assert("crash_forensics" not in _hidden_imports("pyinstaller --onefile main.py\n"),
            "M7b 抽名字这件事咬得动漏登记（空清单上报不出名字）")
    # M8 运行产物名被 ignore 挡住（判的是 `git add` 那一刻 git 怎么说）
    import subprocess
    probe = subprocess.run(["git", "-C", str(_REPO), "check-ignore", "--no-index",
                            "-v", "b2bcollect_build/crash_report.txt",
                            "b2bcollect_build/crash_threads.txt",
                            "b2bcollect_build/last_update.json"],
                           capture_output=True, text=True)
    _assert_eq(probe.returncode, 0,
               f"M9 三个运行时产物名都被 ignore 挡住（rc={probe.returncode} out={probe.stdout!r}）")


def _call_name(node: ast.Call) -> str:
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        v = f.value
        return f"{v.id}.{f.attr}" if isinstance(v, ast.Name) else f.attr
    return ""


# ── N. 钩子链不许成环：装了取证之后，异常仍然要打印得出来 ────────────────

_CHILD_SRC = '''# -*- coding: utf-8 -*-
"""子进程：在真・顶层异常与真・后台线程异常上验取证钩子不吞现场。"""
import os
import sys
import threading

sys.path.insert(0, sys.argv[1])
os.environ["HOME"] = sys.argv[2]
os.environ["USERPROFILE"] = sys.argv[2]
import crash_forensics as cf

cf.reset_for_tests()
cf.install()
cf.reset_for_tests()
cf.install()          # 上一版就是在这一步把「上一任」存成了自己的钩子，链当场成环
print("CHAIN_SELF=%s" % (cf._OLD_EXCEPTHOOK is cf._hook), file=sys.stderr)
print("THREAD_CHAIN_SELF=%s" % (cf._OLD_THREADHOOK is cf._thread_hook), file=sys.stderr)


def _thread_boom():
    raise TypeError("后台线程异常也要打印")


t = threading.Thread(target=_thread_boom, name="n-child-thread")
t.start()
t.join()


def _top_boom():
    raise ValueError("顶层异常也要打印")


_top_boom()
'''


def section_n():
    """取证装置最坏的失败形状：它把故障现场本身抹掉了。

    为什么必须开子进程：`sys.excepthook` 只在解释器准备退出的那一刻跑，在本套件里 raise
    再捕获永远只能测到 `_hook` 本身，测不到「钩子链把异常吞了」这件事——本轮整轮判据
    结果静默消失（退出码 1、零行 Traceback）就是这个形状，不当场复现就等于没钉住。
    """
    print("\n[N] 钩子链不许指回自己：异常既要落文件，也仍然要打印得出来")
    import subprocess
    workdir = Path(_TMP_HOME) / "n_child"
    workdir.mkdir(parents=True, exist_ok=True)
    script = workdir / "n_child.py"
    script.write_text(_CHILD_SRC, encoding="utf-8")
    env = dict(os.environ)
    env["HOME"] = str(workdir)
    env["USERPROFILE"] = str(workdir)
    env.pop("B2B_ACL_LEVEL", None)
    env.pop("B2B_EDITION", None)
    try:
        proc = subprocess.run([sys.executable, str(script),
                               str(Path(__file__).resolve().parent), str(workdir)],
                              capture_output=True, text=True, timeout=120, env=env)
    except BaseException as e:            # noqa: BLE001
        _assert(False, f"N0 子进程没跑起来：{type(e).__name__}: {e}")
        return
    err = proc.stderr or ""
    # 子进程输出只在红的时候贴：判据消息是即时求值的，无条件拼上 stderr 会把每一轮
    # 正常输出都搅成一段 traceback，下一个读日志的人分不清哪一段是真事故。
    tail = "" if proc.returncode == 1 else "\n" + err[-600:]
    _assert_eq(proc.returncode, 1,
               f"N1 子进程要按「未捕获异常」退出（rc=1，不许被抹成 0）{tail}")
    # 子进程那两行链状态要先按 KEY=VALUE 解析再判，**不能做子串包含**：上一版写的是
    # `"CHAIN_SELF=False" in err`，而下一行 `THREAD_CHAIN_SELF=False` 的尾巴恰好包含这个
    # 子串——链已经成环、N2 却照样报绿（本轮变异台正是这么假绿了一次，靠直探针才发现）。
    # 同一个形状在别的判据上也一样成立：短 needle 撞进长行是静默失灵，不是运气问题。
    flags = {}
    for ln in err.splitlines():
        key, sep, val = ln.strip().partition("=")
        if sep and key in ("CHAIN_SELF", "THREAD_CHAIN_SELF"):
            flags[key] = val.strip()
    _assert(flags.get("CHAIN_SELF") == "False",
            "N2 装两次之后链里也没有自己（上一版就是这里成环）"
            f"｜实际：{flags or '子进程没打印链状态（多半没跑到那一行）'}")
    _assert(flags.get("THREAD_CHAIN_SELF") == "False",
            "N3 threading 那一路同样不成环"
            f"｜实际：{flags or '子进程没打印链状态（多半没跑到那一行）'}")
    # N4/N5 本轮真正的回归点：递归抛出的 RecursionError 会被钩子里那句
    # except BaseException 静静吞掉，最终形态就是「退出码 1 + 顶层那一条根本不打印」。
    # 上一版 N4 判的是「stderr 里有没有 Traceback」，本轮变异台实测揭穿了这个判法：钩子
    # 成环时 stderr 里**仍然有一段 Traceback**（后台线程那一条走的是 threading 的默认输出），
    # 于是「有 Traceback」为真、顶层异常却一个字都没了——判据被无关段落喂饱，又是假绿。
    # 现在只认顶层那一条自己的类型+消息，以及它自己的栈帧名。
    _assert("ValueError: 顶层异常也要打印" in err,
            "N4 顶层那条异常按自己的类型和消息打印出来（后台线程那段 traceback 喂不饱它）"
            + ("" if "ValueError: 顶层异常也要打印" in err else "\n" + err[-600:]))
    _assert("in _top_boom" in err,
            "N5 打印的栈里能认出出事那个函数（只有异常行没有帧 = 报不了案）"
            + ("" if "in _top_boom" in err else "\n" + err[-600:]))
    _assert("后台线程异常也要打印" in err and "n-child-thread" in err,
            "N6 后台线程也交回了默认行为（只写我们自己的文件 = 用户对着一个什么都不报的终端）")
    # N7~N10 交回 stderr 不等于证据没落：文件是目的，stderr 只是给开发机看的
    report = workdir / "Desktop" / cf.CRASH_LOG_NAME
    if not report.exists():
        report = Path(workdir) / cf.CRASH_LOG_NAME
    _assert(report.exists(), f"N7 子进程的取证文件落在它自己的 HOME 里：{report}")
    if report.exists():
        text = report.read_text(encoding="utf-8")
        _assert("未捕获异常（解释器钩子）" in text, "N8 顶层那一条落了文件")
        _assert("后台线程未捕获异常" in text, "N9 后台线程那一条也落了文件")
        _assert("_top_boom" in text and "_thread_boom" in text,
                "N10 两条都能从函数名认出自己（只有异常类型没有位置 = 报不了案）")


# ── 入口 ──────────────────────────────────────────────────────────────────

def _cleanup():
    """把 HOME 与取证文件放回原样：判据不许在开发机上留现场。"""
    global _CLEANED
    if _CLEANED:
        return
    _CLEANED = True
    try:
        if _ORIG_HOME:
            os.environ["HOME"] = _ORIG_HOME
            if os.name == "nt":
                os.environ["USERPROFILE"] = _ORIG_HOME
        import shutil
        shutil.rmtree(_TMP_HOME, ignore_errors=True)
    except BaseException:               # noqa: BLE001
        pass


def main():
    print("═══ 需求 8：运行期崩溃取证 离线判据 ═══")
    _assert(_home_ok(), "开工自检：取证落点已钉到临时 HOME（不往真实桌面写字节）")
    if not _home_ok():
        print("  ❌ HOME 重定向没生效，后面每一条 report 都会污染真实桌面，直接收工")
        print(f"\n═══ 结果：{_PASS} 通过 / {_FAIL + 1} 失败 ═══")
        return 1                      # 不用 sys.exit 收尾：下面 `__main__` 统一按同一口径退出
    for fn in (section_a, section_b, section_c, section_d, section_e, section_f,
               section_g, section_h, section_i, section_j, section_k, section_l,
               section_m, section_n):
        fn()
    _cleanup()
    print(f"\n═══ 结果：{_PASS} 通过 / {_FAIL} 失败 ═══")
    return 1 if _FAIL else 0          # 打完结论就返回，退出交给 `__main__`（硬门第 23 条）


_CLEANED = False
# 只靠 atexit，不放进 finally：顶层异常时解释器**先**走 excepthook、**后**跑 atexit。
# 上一版写在 finally 里，等于在钩子动手之前就把取证落点搬回真实桌面——本轮实测跑一次
# 判据，用户桌面就多出一份 3MB 的 crash_report.txt，而那正是 A2 声称已经避免的事。
atexit.register(_cleanup)


if __name__ == "__main__":
    # 本套件建过 QApplication，收尾因此走仓里既有的那一族纪律（硬门第 23 条）：把结论打完、
    # flush、再 `os._exit`。把 Qt 与线程的析构留在解释器收尾里，段错误与判定毫无关系却会
    # 把退出码改成非零——一条全绿的套件就这样被批跑读成一次失败。
    # `os._exit` 不跑 atexit，所以正常路径先手工收尾一次（_cleanup 自带幂等）；顶层抛异常的
    # 路径根本不走到这里，那份工由上面的 atexit 兼着。
    _rc = main()
    _cleanup()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(_rc)
