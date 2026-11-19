# -*- coding: utf-8 -*-
"""update_offline_test.py —— 版本提示与一键升级接到界面上的那一段（需求 2 的第二份判据）。

引擎层在 b2bcollect_build/test_tool_update_offline.py（管「五种结论对不对、下载校验严不严、
交接链分不分岔」）；这一份只管**接线**：开机有没有真去问一次、有新版本时那句话弹不弹得
出来、非强制是不是真的非强制、点「现在就装」之后交接命令齐不齐、上一回升级的结果有没有
人替用户说出来。

为什么要单独一份：引擎全绿而界面没接上，是这一族最常见的「功能做完了没人能用」的形状
（本仓库已经为此改过一次：某条横幅链接的槽被删过一格，引擎与界面各自都绿）。所以这里
用**真 MainWindow**——只顶掉线程、弹框与出网那三样，界面本身是真的，「忘了接线」「方法名
打错」「横幅写了两条链接却只分派一条」这类红只有真建界面才看得见。

顶掉的四样东西与原因
------------------
① `UpdateWorker`：改成 start() 里同步跑。offscreen 里等 QThread 就是等一件不能确定何时
   结束的事，而本套件验的是数据流与话术，不是线程调度。
② `QMessageBox.exec_` / `information` / `warning` / `question`：模态框在无人值守下会永久
   挂住（批跑曾因此被拖死 20 分钟）。这里记下标题、正文、按钮名，并按指定那条「点下去」。
③ `_tool_update` 模块：换成一层壳——只有 `check` / `download` / `read_report` 三样按脚本
   回答，其余（install_steps / handoff_kind / notes_view / should_prompt / SNOOZE_KEY /
   portable_url）全部转给**真模块**。这样「弹窗里那几步」测的是真话术，而不是测试自己写的
   一句近似说法。
④ `_ui_prefs_path`：指到 debug/ 下一份临时文件，免打扰与「报告已播过」都落在它身上。

还要手动拧一下的一个开关
--------------------
界面里 `_headless_session()` 回答的是「屏幕前有没有人」。offscreen 下它天然是 True，于是
`_prompt_update` 会绕开弹框只留日志与横幅——那确实是要测的一态（CI 里 exec 模态等于等一次
永远不会来的点击，本仓库整批被这样拖死过）。但弹窗正文、按钮分派、免打扰记账全都长在另一
条路上，所以需要一个 `FakeDisplay()` 把它按成 False。弹框本身仍然由上面第 ② 样接管，按下去
不会真等任何人。两条路各自都要有断言：只测弹窗那一路，将来有人把 `_headless_session()` 那
个分支删掉也还是绿的，而那正是 CI 卡死的形状。

十五组断言各自盯一个具体事故
--------------------------
① 开机真的问了一次（不问就永远不知道有新版）；② 已是最新/问不出时一条消息都不许多；
③ 有新版时弹的那一句：两个版本号 + 更新说明 + 会发生什么，全在同一段正文里；
④ 非强制：点「先不装」不会开始下载，也不许改免打扰记录；⑤ 点「这一版先不提示」按**版本号**
记住，且出更新的版本照样提；⑥ 不适配本机时**不许**在场摆「现在就装」，但必须递下载页；
⑦ 清单坏了要说「不是你这台机器坏了」；⑧ 「没能确认」不许说成「已是最新」；
⑨ 手动点检查：每一态都要出声（点了没反应与按钮坏了在人眼里是同一件事）；
⑩ 下载过了校验才安排交接，命令四开关齐全并真的关窗；⑪ 下载没过校验一步都不往下走；
⑫ 采集进行中先问一句、默认「否」；⑬ 上一回升级的结果只播一次、失败那条必须挂在屏幕上；
⑭ 模块没加载上时界面不许崩，且自报/交接两个入口都不许退化成建界面；
⑮ 版本号进标题与状态行，未能确认时不许拼出假号。

运行：QT_QPA_PLATFORM=offscreen .venv/bin/python debug/update_offline_test.py
"""
import os
import re
import sys
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "b2bcollect_build"))

from PyQt5.QtWidgets import QApplication, QMessageBox                    # noqa: E402

import b2bcollect_gui as gui                                             # noqa: E402
import tool_update as real_tu                                            # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])

PREFS = Path(__file__).resolve().parent / "update_offline_prefs.json"

CHROME_OK_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

_PASS = 0
_FAIL = 0


def check(name, cond, detail=None):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ✅ {name}")
    else:
        _FAIL += 1
        print(f"  ❌ {name}" + (f"  -> {detail}" if detail is not None else ""))


def log_text(win):
    return str(win.log.toPlainText())


# ── 替身一：五态脚本 ────────────────────────────────────────────────────
def artifact(key="mac_arm64"):
    """一份**过得了真校验**的制品条目（走真模块的 _artifact_entry，不自己拼形状）。"""
    entry = {"file": real_tu.ARTIFACT_BY_KEY[key],
             "url": f"https://{real_tu.BUCKET_HOST}/{real_tu.RELEASE_PREFIX}/"
                    f"{real_tu.ARTIFACT_BY_KEY[key]}",
             "sha256": "a" * 64, "size": 123456789}
    got = real_tu._artifact_entry(entry, key)
    assert got.get("ok"), got
    return got


def verdict(state, key="mac_arm64", build=234, current="1.231", latest="1.234",
            why="", notes="修了一个换绑后点重启没反应的问题。"):
    """真模块的 _verdict 形状（测试不自己造字段表，免得测一份过期结构）。"""
    art = artifact(key) if state == "update-available" else None
    arts = {key: art} if art else {}
    return real_tu._verdict(state, why=why, current=current, latest=latest, build=build,
                            notes=notes, published_at="2026-11-19T10:00:00Z",
                            artifacts=arts, artifact=art,
                            distance=(1 if state == "update-available" else 0))


# ── 替身二：_tool_update 的壳（只顶掉出网的三样）────────────────────────
class FakeUpdate:
    """check/download/read_report 按脚本答，其余全部转给真模块。

    为什么不整体替掉模块：弹窗里那几步、免打扰那句「按版本号」、下载页那个地址，都要
    是**真模块**说的话；测试另写一份近似实现，红了也证明不了界面接的是真话术。
    """

    def __init__(self):
        self.checks = []
        self.downloads = []
        self.script_check = verdict("up-to-date")
        self.script_download = {"ok": True, "path": "/tmp/B2bCollectTool_Mac_arm64.zip",
                                "sha256": "a" * 64, "why": ""}
        self.script_report = {}
        self.written_reports = []

    def check(self, log=None, manifest_fetch=None, key=None):
        self.checks.append(key)
        return self.script_check

    def download(self, art, log=None, cancel=None, dest_dir=None):
        self.downloads.append(dict(art or {}))
        return dict(self.script_download)

    def read_report(self, path=None, now=None, max_age=None):
        return dict(self.script_report)

    def write_report(self, res, log=None, dest=None):
        self.written_reports.append(dict(res))
        return {"ok": True, "path": str(dest or "x"), "why": ""}

    def __getattr__(self, name):
        return getattr(real_tu, name)


# ── 替身三：线程（同步跑，不发 QThread）────────────────────────────────
class _Signal:
    def __init__(self):
        self._slots = []

    def connect(self, fn):
        self._slots.append(fn)

    def emit(self, *a):
        for fn in self._slots:
            fn(*a)


class SyncUpdateWorker:
    started = []

    def __init__(self, mode="check", artifact=None):
        self.mode = mode
        self.artifact = artifact or {}
        self.logged = _Signal()
        self.finished_update = _Signal()

    def start(self):
        SyncUpdateWorker.started.append(self.mode)
        eng = gui._tool_update
        try:
            if self.mode == "download":
                res = eng.download(self.artifact, log=lambda m: self.logged.emit(m))
                self.finished_update.emit("download", res, "")
                return
            self.finished_update.emit("check", eng.check(), "")
        except Exception as e:
            self.finished_update.emit(self.mode, None, f"{type(e).__name__}: {e}")

    def isRunning(self):
        return False


class SilentChromeWorker:
    """Chrome 体检在这里不该出场：给一份「已装」并只记一笔，免得抢横幅。"""

    def __init__(self, mode="inspect", only=None):
        self.logged = _Signal()
        self.finished_doctor = _Signal()

    def start(self):
        self.finished_doctor.emit("inspect", {"installed": True, "certain": True,
                                              "probe_error": "", "launch_path": CHROME_OK_PATH,
                                              "evidence": []}, "")

    def isRunning(self):
        return False


# ── 替身四：模态框 ─────────────────────────────────────────────────────
class Modals:
    """记下每一次弹框，并按指定那条按钮「点下去」。

    一并接管 setWindowTitle：QMessageBox 把标题缓在自己私有那份里，到 show 才写给窗口，
    所以下窗之前 `windowTitle()` 永远读回空串（实测）。不接管就等于「标题写错了」这一整类
    缺陷在这里测不到——而本套件的正文首行红线正是冲着同一件事来的。
    """

    def __init__(self):
        self.box = []
        self.informations = []
        self.warnings = []
        self.questions = []     # [(标题, 正文, 其余实参)]：默认按钮就住在其余实参里
        self.pick = None        # 下一次 exec_ 要点掉的按钮名（None = 点取消那一路）
        self.answer = QMessageBox.No     # question 的答复：默认「否」，与界面那个确认框的默认按钮一致
        self.titles = {}

    def clear(self):
        """只清记录，不动 pick/answer：调用点都是「先定好怎么点，再跑那一步」。"""
        for items in (self.box, self.informations, self.warnings, self.questions):
            del items[:]
        self.titles.clear()

    def _exec(self, box):
        names = [str(b.text()) for b in box.buttons()]
        self.box.append((self.titles.get(id(box), ""), str(box.text()), names))
        target = None
        for b in box.buttons():
            if self.pick is not None and self.pick in str(b.text()):
                target = b
                break
        box.clickedButton = lambda _t=target: _t
        return 0

    def install(self):
        self._saved = (QMessageBox.exec_, QMessageBox.information, QMessageBox.warning,
                       QMessageBox.question, QMessageBox.setWindowTitle)
        holder = self
        saved_title = QMessageBox.setWindowTitle

        def _exec_via(self_box):
            return holder._exec(self_box)

        def _title_via(self_box, title):
            holder.titles[id(self_box)] = str(title)
            return saved_title(self_box, title)

        QMessageBox.exec_ = _exec_via
        QMessageBox.setWindowTitle = _title_via
        QMessageBox.information = staticmethod(
            lambda parent, title, text, *a: self.informations.append((title, text))
            or QMessageBox.Ok)
        QMessageBox.warning = staticmethod(
            lambda parent, title, text, *a: self.warnings.append((title, text))
            or QMessageBox.Ok)
        QMessageBox.question = staticmethod(
            lambda parent, title, text, *a: (self.questions.append((title, text, a))
                                             or holder.answer))

    def restore(self):
        (QMessageBox.exec_, QMessageBox.information, QMessageBox.warning,
         QMessageBox.question, QMessageBox.setWindowTitle) = self._saved


def banner_links(win):
    return re.findall(r'href="(#[a-z-]+)"', win.banner.text())


class CloseSpy:
    """记下每一次关窗的**结局**：[(窗口, True/False)]。

    为什么不用 `isVisible()`/`isHidden()`：offscreen 下这些窗口从来没 show 过，实测两个值
    在 close() 前后都不变——拿它们当「关没关」的判据等于永远绿而什么也没量到。
    为什么不用 eventFilter：事件过滤器跑在 closeEvent **之前**，那一刻 `isAccepted()` 还是
    Qt 给的默认值 True，被 `ev.ignore()` 拦掉那一次也读成 True（本轮实测撞上的形状）。
    所以这里包一层 closeEvent：先让真的处理跑完，再看结局——包装只加一行记账，
    closeEvent 本身一个字不改。
    """

    events = []

    @staticmethod
    def of(win):
        return [accepted for who, accepted in CloseSpy.events if who is win]


_ORIG_CLOSE_EVENT = gui.MainWindow.closeEvent


def _spied_close_event(self, ev):
    _ORIG_CLOSE_EVENT(self, ev)
    CloseSpy.events.append((self, bool(ev.isAccepted())))


gui.MainWindow.closeEvent = _spied_close_event


def closes_of(win):
    return CloseSpy.of(win)


def reset_prefs():
    try:
        PREFS.unlink()
    except OSError:
        pass


def read_prefs():
    import json
    try:
        return json.loads(PREFS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def build(state=None, key="mac_arm64", delay_zero=True, worker_running=False,
          latest="1.234", report=None, has_update=True):
    """按「用户打开工具」那条路真建一个主界面，并把四样替身接上去。"""
    eng = FakeUpdate()
    if state is not None:
        eng.script_check = verdict(state, key=key, latest=latest)
    if report is not None:
        eng.script_report = report
    gui._tool_update = eng
    gui.HAS_TOOL_UPDATE = bool(has_update)
    gui.HAS_TOOL_VERSION = True
    gui.UpdateWorker = SyncUpdateWorker
    SyncUpdateWorker.started = []
    gui._chrome_doctor = types.SimpleNamespace(
        inspect=lambda: {"installed": True, "certain": True, "evidence": []},
        install_plan=lambda info=None: [], install=lambda **k: {"ok": True, "tried": []},
        summary_text=lambda info=None: "已安装", PAGE_OFFICIAL="https://example/chrome",
        open_official_page=lambda: {"ok": True, "url": "https://example/chrome"})
    gui.ChromeDoctorWorker = SilentChromeWorker
    gui._ui_prefs_path = lambda: PREFS
    saved_delay = gui.MainWindow.UPDATE_CHECK_DELAY_MS
    if delay_zero:
        gui.MainWindow.UPDATE_CHECK_DELAY_MS = 0        # 让开机那一次当场就能被 processEvents 叫醒
    win = gui.MainWindow()
    if delay_zero:
        gui.MainWindow.UPDATE_CHECK_DELAY_MS = saved_delay
    win._update_worker = None
    win.spawns = []
    win.handoffs = []
    win._relaunch_command = lambda: ["/Applications/B2bCollectTool.app"]
    win._spawn_relaunch = lambda: win.spawns.append("relaunch")
    win._spawn_update_handoff = lambda: win.handoffs.append("handoff")
    if worker_running:
        win.worker = types.SimpleNamespace(isRunning=lambda: True)
    app.processEvents()         # 把 __init__ 里那几个 singleShot 叫醒（开机自动检查就在其中）
    return win, eng


def close_window(win):
    gui.MainWindow.UPDATE_CHECK_DELAY_MS = 999_999      # 收尾期间别再被叫醒
    win.close()
    # 收尾这一次关窗不该被当成证据：它发生在所有断言之后，但同一个窗口名下只多不少，
    # 不如当场清干（下一个测试建的是新窗口，本来也不该看到上一轮的旧记录）。
    CloseSpy.events[:] = [(who, acc) for who, acc in CloseSpy.events if who is not win]


_REAL_HEADLESS = gui._headless_session


class FakeDisplay:
    """把「屏幕前有人」按成 True，模态那一路才走得到（弹框本身仍由 Modals 接管）。"""

    def __enter__(self):
        gui._headless_session = lambda: False
        return self

    def __exit__(self, *exc):
        gui._headless_session = _REAL_HEADLESS
        return False


# ── ①② 开机真的问一次；问不出「有新版」就一个字也不许多 ──────────────────
def _test_silent_states(modals):
    print("\n── ①② 开机问一次；问不出「有新版」就一个字也不许多 ──")
    reset_prefs()
    modals.clear()
    win, eng = build(state="up-to-date")
    try:
        check("打开工具就自己去问了一次（不问就永远不知道有新版）",
              "check" in SyncUpdateWorker.started, SyncUpdateWorker.started)
        check("而且只问一次（多问一次就是多一次出网、多一份抢启动的磁盘）",
              len(eng.checks) == 1, eng.checks)
        check("已是最新 ⇒ 四种框一个也不弹",
              not (modals.box or modals.informations or modals.warnings
                   or modals.questions), modals.box)
        check("已是最新 ⇒ 横幅不出（黄条挂着等于让人去找一件不存在的事）",
              win.banner.isHidden() and not win._update_banner_live, win.banner.text())
        check("已是最新 ⇒ 自动那一次日志也保持安静（出声的口径只属于手动）",
              "已经是最新版" not in log_text(win), log_text(win)[-200:])
        check("自动检查不替用户记任何东西（免打扰与报告都是一个字不改）",
              not read_prefs(), read_prefs())
        win2, _eng2 = build(state="unknown")
        try:
            check("问不出（没网/超时）⇒ 一样安静，且绝不写成「已是最新」",
                  not modals.box and win2.banner.isHidden()
                  and "已是最新" not in log_text(win2), log_text(win2)[-200:])
        finally:
            close_window(win2)
        # CI/无头：有新版也不许 exec 模态，但结论与出口仍得留在屏幕上
        modals.clear()
        win3, _eng3 = build(state="update-available")
        try:
            check("无图形会话时有新版也不弹模态（弹了就是等一次永远不会来的点击）",
                  not modals.box, modals.box)
            check("但结论与出口都留在屏幕上：日志一行 + 横幅两条链接",
                  "有新版本可装" in log_text(win3)
                  and banner_links(win3) == ["#update-show", "#update-recheck"],
                  (log_text(win3)[-160:], banner_links(win3)))
            check("同一句结论只打一遍（打两遍的日志远程排障时没人看得下去）",
                  log_text(win3).count("有新版本可装：1.234") == 1, log_text(win3)[-300:])
        finally:
            close_window(win3)
    finally:
        close_window(win)


# ── ③ 弹出来的那一句 ─────────────────────────────────────────────────
def _test_prompt_content(modals):
    print("\n── ③ 弹出来的那一句：两个版本号 + 更新说明 + 会发生什么 ──")
    reset_prefs()
    modals.clear()
    with FakeDisplay():
        win, _eng = build(state="update-available")
        try:
            title, text, names = modals.box[-1]
            check("正文首行就是结论（macOS 上弹框常以 sheet 呈现，标题栏那一格根本看不到）",
                  text.startswith("发现新版本：1.234"), text[:60])
            check("标题也写了（Windows 上有标题栏，空着就是没人知道这框是干什么的）",
                  title == "有新版本可以装", title)
            check("两个版本号都在：远端最新 + 你现在用的是（只报一个就没法判断）",
                  "1.234" in text and "你现在用的是：1.231" in text, text[:200])
            check("更新说明原样递上来（工具不替管理员编一句，也不把管理员写的丢掉）",
                  "换绑后点重启没反应" in text, text[:300])
            check("说清「点确定之后会走完哪几步」：同意必须发生在信息之后",
                  "工具会自己走完这几步" in text and "指纹" in text, text[:300])
            check("三个出口：现在就装 / 这一版先不提示 / 先不装（少一个就是替用户决定）",
                  len(names) == 3 and any("现在就装" in n for n in names)
                  and any("这一版先不提示" in n for n in names), names)
            check("「非强制」写在按钮文字里：取消那一条也说自己下次还会提",
                  any("下次有新版还会提" in n for n in names), names)
        finally:
            close_window(win)
        # 那几步必须按**清单里那份制品**说话，不能拿本机去推：Windows 清单递 Mac 包时
        # 「这一步不需要管理员授权」就是错的，而这句话写在同意之前
        for key, want, forbid in (("win", "管理员授权", "挪到旁边"),
                                  ("mac_arm64", "挪到旁边", "管理员授权")):
            reset_prefs()
            modals.clear()
            win2, _eng2 = build(state="update-available", key=key)
            try:
                text = modals.box[-1][1]
                check(f"递来的是 {key} 那一档时，那几步写的是那一档的事"
                      f"（含「{want}」不含「{forbid}」）",
                      want in text and forbid not in text, text[:300])
            finally:
                close_window(win2)


# ── ④ 非强制：点「先不装」之后一个字节也不动 ─────────────────────────
def _test_not_forced(modals):
    print("\n── ④ 非强制：点「先不装」之后一个字节也不动 ──")
    reset_prefs()
    modals.clear()
    with FakeDisplay():
        modals.pick = "先不装"
        win, eng = build(state="update-available")
        try:
            check("没点「现在就装」就不会开始下载（一个字节也不许在同意之前下）",
                  SyncUpdateWorker.started == ["check"], SyncUpdateWorker.started)
            check("也没安排交接、没关窗（「先不装」不是「退出工具」）",
                  not win._update_handoff_cmd and win.handoffs == []
                  and win.spawns == [] and closes_of(win) == [],
                  (win._update_handoff_cmd, win.handoffs, win.spawns, closes_of(win)))
            check("取消那一次不许写免打扰记录（那是另一条路，按「这一版先不提示」才记）",
                  real_tu.SNOOZE_KEY not in read_prefs(), read_prefs())
            check("并把区别说清：下次开机还会提，入口也当场给了",
                  "下次开机还会" in log_text(win) and "检查更新" in log_text(win),
                  log_text(win)[-200:])
        finally:
            close_window(win)


# ── ⑤「这一版先不提示」按版本号记账 ───────────────────────────────────
def _test_snooze_by_version(modals):
    print("\n── ⑤「这一版先不提示」按版本号记账 ──")
    reset_prefs()
    modals.clear()
    with FakeDisplay():
        modals.pick = "这一版先不提示"
        win, _eng = build(state="update-available", latest="1.234")
        try:
            check("记住的是**这一个版本号**（记成布尔位就等于永久关掉更新提示）",
                  read_prefs().get(real_tu.SNOOZE_KEY) == "1.234", read_prefs())
            check("跳过之后当场回话，并把「想装随时能装」那条入口给出来",
                  "不再自动提示" in log_text(win) and "检查更新" in log_text(win),
                  log_text(win)[-200:])
        finally:
            close_window(win)
        modals.clear()
        modals.pick = "先不装"
        win2, _eng2 = build(state="update-available", latest="1.234")
        try:
            check("同一版下次开机不再提（说了先不提示还天天弹，用户只会把工具关掉）",
                  not modals.box, modals.box)
        finally:
            close_window(win2)
        modals.clear()
        win3, _eng3 = build(state="update-available", latest="1.235")
        try:
            check("出了更新的版本照旧提（跳过的只是 1.234 那一版）",
                  bool(modals.box) and "发现新版本：1.235" in modals.box[-1][1], modals.box)
        finally:
            close_window(win3)
        modals.clear()
        win4, _eng4 = build(state="update-available", latest="1.234")
        try:
            check("前置：被跳过的那一版自动那一路确实闭着嘴",
                  not modals.box, modals.box)
            win4._check_app_update(manual=True,
                                   forced=verdict("update-available", latest="1.234"))
            check("但手动点「检查更新」照样弹（免打扰关的是自动弹窗，不能顺手把入口也关掉）",
                  bool(modals.box) and "发现新版本：1.234" in modals.box[-1][1], modals.box)
        finally:
            close_window(win4)


# ── ⑥ 不适配本机：不许摆「现在就装」，但必须递下载页 ──────────────────
def _test_unsupported(modals):
    print("\n── ⑥ 没适配本机：只递出口，不摆兑现不了的按钮 ──")
    reset_prefs()
    modals.clear()
    win, _eng = build(state="unsupported")
    try:
        check("开机自动遇到「没适配本机」不出横幅（这台机器上他无事可做）",
              win.banner.isHidden() and not modals.box, win.banner.text())
        win._check_app_update(manual=True,
                              forced=verdict("unsupported", why="识别到的平台是 linux"))
        links = banner_links(win)
        check("人主动问过之后必须留一条常显出口（只写日志与按钮坏了分不出来）",
              not win.banner.isHidden() and "没适配本机" in win.banner.text(),
              win.banner.text())
        check("横幅两个动作都在：看说明与手动下载 / 再检查一次",
              links == ["#update-show", "#update-recheck"], links)
        check("「没适配本机」不许说成「没有新版」（两态串档就是话术分家）",
              "没有新版" not in log_text(win), log_text(win)[-200:])
        modals.clear()
        with FakeDisplay():
            win._offer_update_show()      # 横幅那条 #update-show 真的点下去
        text = modals.informations[-1][1]
        check("不适配时点「看更新说明」给的是把话说完的一段话，不是升级弹窗那个壳",
              "没适配本机" in text and "发布那头写的说明" in text, text[:200])
        check("那一段里一个「现在就装」也不许出现（点下去只会拿「缺少可用的下载地址」回报）",
              not modals.box, modals.box)
    finally:
        close_window(win)
    # update-available 但制品不可用（解压版那一档）：按钮必须跟着通路走
    reset_prefs()
    modals.clear()
    with FakeDisplay():
        ev = verdict("update-available")
        name = real_tu.ARTIFACT_WINDOWS_PORTABLE
        port = real_tu._artifact_entry(
            {"file": name,
             "url": f"https://{real_tu.BUCKET_HOST}/{real_tu.RELEASE_PREFIX}/{name}",
             "sha256": "b" * 64, "size": 10}, "win_portable")
        assert port.get("ok"), port
        ev["artifact"] = port
        ev["artifacts"] = {"win_portable": port}
        win2, _eng2 = build()
        try:
            win2._prompt_update(ev)
            names = modals.box[-1][2]
            check("即便清单把「解压版」那一档递到眼前，也不许摆「现在就装」"
                  "（正在跑的解压版 exe 不能被就地覆盖）",
                  not any("现在就装" in n for n in names)
                  and any("打开新版下载页" in n for n in names), names)
            check("不能自动装时也不许把本机那几步列成「可走的路」（那几步在这台机器上"
                  "一步也走不通）",
                  "没有能替你自动安装的做法" in modals.box[-1][1]
                  and "可走的路" not in modals.box[-1][1], modals.box[-1][1][:300])
            modals.pick = "打开新版下载页"
            import webbrowser
            opened = []
            saved_open = webbrowser.open
            webbrowser.open = lambda url, *a: opened.append(url) or True
            try:
                win2._prompt_update(ev)
            finally:
                webbrowser.open = saved_open
            check("点「打开新版下载页」真的把地址交出去（只说一句不行等于没说）",
                  opened == [real_tu.portable_url()], opened)
        finally:
            close_window(win2)


# ── ⑦ 清单坏了：要说清「不是你这台机器坏了」────────────────────────
def _test_bad_manifest(modals):
    print("\n── ⑦ 清单坏了：责任在发布那头，重试没用 ──")
    reset_prefs()
    modals.clear()
    win, _eng = build(state="bad-manifest")
    try:
        check("自动那一次不出横幅（发布的事不该天天在用户屏幕上敲一下）",
              win.banner.isHidden(), win.banner.text())
        win._check_app_update(manual=True,
                              forced=verdict("bad-manifest", why="清单里的 sha256 不像话"))
        text = log_text(win)
        check("这一态要把责任说清：不是本机坏了，并把「找谁」说出来",
              "不是你这台机器坏了" in text and "反馈给发布人" in text, text[-300:])
        check("原因逐字带上（远程排障时这句就是发布那边的复现步骤）",
              "清单里的 sha256 不像话" in text, text[-300:])
        check("同时留一条能点的出口，但话里不说「重试就能好」",
              not win.banner.isHidden() and "#update-recheck" in banner_links(win)
              and "修发布才有用" in text, win.banner.text())
        check("坏了的清单不许被读成「已是最新」（那一形会让人从此再也收不到更新）",
              "已经是最新版" not in text, text[-300:])
    finally:
        close_window(win)


# ── ⑧「没能确认」不许说成「已是最新」，也不许抹掉先前那条结论 ────────
def _test_unknown(modals):
    print("\n── ⑧「问不出」≠「已是最新」，也不能反过来抹掉「有新版」 ──")
    reset_prefs()
    modals.clear()
    win, _eng = build()
    try:
        win._check_app_update(manual=True, forced=verdict("update-available"))
        win._check_app_update(manual=True,
                              forced=verdict("unknown", why="连不上发布的地址"))
        text = log_text(win)
        check("问不出时说的就是「问不出」：不写「已是最新」也不写「没有新版」",
              "没能确认有没有新版本" in text and "问不出" in text
              and "已经是最新版" not in text, text[-300:])
        check("原因照原话带出来（只说「检查失败」用户下一步只能猜）",
              "连不上发布的地址" in text, text[-300:])
        check("拿一个「问不出」抹掉刚刚那条「有新版」的横幅=让用户以为那个版本从来不存在",
              not win.banner.isHidden() and "#update-show" in banner_links(win),
              win.banner.text())
        check("两条结论各留一行、不互相覆盖（先成功后失败这个顺序本身就是证据）",
              text.count("有新版本可装：1.234") == 1
              and text.count("没能确认有没有新版本") == 1,
              (text.count("有新版本可装：1.234"), text.count("没能确认有没有新版本")))
        win._check_app_update(manual=True, forced=verdict("up-to-date"))
        check("真确认过没得装了，先前那条横幅就该收掉（挂着一条不成立的提示比不挂更坏）",
              win.banner.isHidden() and not win._update_banner_live, win.banner.text())
        # 本层自身的异常（连问都没问出口）也不能被汇成一句好消息
        before = log_text(win)
        win._on_update_checked("check", None, "TimeoutError: 连不上发布地址")
        text = log_text(win)
        check("worker 自身报错时只说这一句，不拿它当「已是最新」",
              "这一步没跑成" in text[len(before):] and "最新版" not in text[len(before):],
              text[len(before):])
    finally:
        close_window(win)


# ── ⑨ 手动点「检查更新」：五态都要出声 ───────────────────────────
def _test_manual_speaks(modals):
    print("\n── ⑨ 手动检查：五种状态每一种都要出声，且只出一行 ──")
    reset_prefs()
    modals.clear()
    expect = {"update-available": "有新版本可装",
              "up-to-date": "已经是最新版",
              "unsupported": "没适配本机",
              "bad-manifest": "读不出可用内容",
              "unknown": "没能确认有没有新版本"}
    win, eng = build()
    try:
        for state, frag in expect.items():
            before = log_text(win)
            win._check_app_update(manual=True, forced=verdict(state))
            text = log_text(win)
            added = text[len(before):]
            check(f"手动检查「{state}」有回声：{frag}", frag in added, (state, added[:160]))
            check(f"「{state}」那一句当场只打一遍（同一次检查打两遍就是话术分家）",
                  added.count(frag) == 1, (state, added))
        lines = [gui._update_state_line(verdict(s)) for s in expect]
        check("五态那五句话互不相同（汇成一句「检查完成」就是拿一个框换回一堆猜）",
              len(set(lines)) == 5 and not any("检查完成" in x for x in lines), lines)
        # 上一路还在跑：拦下也要说清为什么（静默不理会被当成界面卡住）
        before = log_text(win)
        started_before = len(eng.checks)
        win._update_worker = types.SimpleNamespace(isRunning=lambda: True)
        win._check_app_update(manual=True)
        check("检查还在路上时不并发起第二路，且把「等一下就行」说出来",
              len(eng.checks) == started_before
              and "上一个动作还在路上" in log_text(win)[len(before):],
              log_text(win)[len(before):])
        win._update_worker = None
        # 接错销子那一形：按钮存在但按下去不走到 manual=True
        seen = []
        win._check_app_update = lambda manual=False, forced=None: seen.append(manual)
        win.btn_update_check.click()
        check("日志行那颗「检查更新」真的接在 manual=True 上（接错=五态里四态点了没反应）",
              seen == [True], seen)
    finally:
        close_window(win)


# ── ⑩ 下载过了校验才安排交接 ────────────────────────────────────
def _test_download_then_handoff(modals):
    print("\n── ⑩ 校验过了才安排交接：四开关齐全、旧重启让位、窗真关掉 ──")
    reset_prefs()
    modals.clear()
    with FakeDisplay():
        modals.pick = "现在就装"
        win, eng = build(state="update-available")
        try:
            app_path = "/Applications/B2bCollectTool.app"
            art = eng.script_check.get("artifact") or {}
            check("点「现在就装」之后才动下载（下载只在同意之后）",
                  SyncUpdateWorker.started == ["check", "download"],
                  SyncUpdateWorker.started)
            check("下载用的就是清单里那一份制品：地址、指纹、字节数全按清单，界面不自己拼",
                  eng.downloads == [art] and art.get("url"), eng.downloads)
            check("交接命令四个开关齐全且顺序可复现：包、等谁退出、目标构建号、装完拉起谁",
                  win._update_handoff_cmd == [app_path, gui.UPDATE_APPLY_FLAG,
                                              eng.script_download["path"],
                                              gui.UPDATE_WAIT_PID_FLAG, str(os.getpid()),
                                              gui.UPDATE_EXPECT_BUILD_FLAG, "234",
                                              gui.UPDATE_RELAUNCH_FLAG, app_path],
                  win._update_handoff_cmd)
            check("开关名与 tool_update 那份逐字相同（拼不出同一个名字，交接进程会当成"
                  "普通启动、屏幕上静悄悄多出一个窗口）",
                  (gui.UPDATE_APPLY_FLAG, gui.UPDATE_WAIT_PID_FLAG,
                   gui.UPDATE_EXPECT_BUILD_FLAG, gui.UPDATE_RELAUNCH_FLAG)
                  == (real_tu.APPLY_FLAG, real_tu.WAIT_PID_FLAG,
                      real_tu.EXPECT_BUILD_FLAG, real_tu.RELAUNCH_FLAG))
            check("交接进程在 closeEvent 收尾之后真的被丢出去",
                  win.handoffs == ["handoff"], win.handoffs)
            check("而且确实关过一次窗、也被放行（不关窗交接进程永远等不到旧 PID 退出）",
                  closes_of(win) == [True], closes_of(win))
            check("不另起普通重启：两个进程同时拉起只会互抢安装目录与调试端口",
                  win.spawns == [] and win._relaunch_pending is None,
                  (win.spawns, win._relaunch_pending))
            check("关窗之前把「下面按顺序做哪三件事」说清（数据会不会丢是这一刻唯一要紧的问题）",
                  "落盘" in log_text(win) and "请别手动再开" in log_text(win),
                  log_text(win)[-300:])
            check("界面自己不写升级报告（那是交接进程的活；这里写了就会把成功那一笔盖掉）",
                  eng.written_reports == [], eng.written_reports)
        finally:
            close_window(win)
        # 关窗被拦下的那一支：需求 4 的同一口径，必须说话且不要求重下
        reset_prefs()
        modals.clear()
        win2, eng2 = build()
        try:
            eng2.script_download = {"ok": True, "path": "/tmp/B2bCollectToolSetup.exe",
                                    "sha256": "a" * 64, "why": ""}
            modals.pick = "现在就装"
            win2._yzd_importing = True           # 现成的「关窗被拒」形状：导入进行中
            win2._check_app_update(manual=True, forced=verdict("update-available"))
            text = log_text(win2)
            check("关窗请求真的发出去了且被拦下（不是根本没走到这一步）",
                  closes_of(win2) == [False], closes_of(win2))
            check("关窗被拦下时必须说话（需求 4 那一族：点了确定、没装上、屏幕上也没一句话）",
                  "没能开始升级" in text, text[-300:])
            check("拦下之后交接命令作废，不留「已经安排好了」的假状态",
                  not win2._update_handoff_cmd and not win2._update_handoff_fallback
                  and win2.handoffs == [], (win2._update_handoff_cmd, win2.handoffs))
            check("那句给的是可执行的下一步且不要求重下（包已经校验过了）",
                  "不必重下" in text and "检查更新" in text, text[-300:])
            check("拦下的是关窗而不是把工具弄死：那一句「请等一下」当场弹了，交接一步也没走",
                  bool(modals.informations) and "请等一下" in modals.informations[-1][0]
                  and win2.handoffs == [], (modals.informations, win2.handoffs))
        finally:
            win2._yzd_importing = False
            close_window(win2)


# ── ⑪ 下载没过校验：一步都不许往下走 ──────────────────────────────
def _test_download_failed(modals):
    print("\n── ⑪ 下载没过校验：一步也不往下走 ──")
    reset_prefs()
    modals.clear()
    with FakeDisplay():
        win, eng = build()
        try:
            eng.script_download = {"ok": False, "path": "", "why": "指纹对不上，包已经删掉了"}
            modals.pick = "现在就装"
            win._check_app_update(manual=True, forced=verdict("update-available"))
            text = log_text(win)
            check("没过校验不安排交接、不关窗（最坏的做法是拿着一个来路不明的包去装）",
                  not win._update_handoff_cmd and win.handoffs == []
                  and win.spawns == [] and closes_of(win) == [],
                  (win._update_handoff_cmd, win.handoffs, closes_of(win)))
            check("原因逐字带出来，并说清本机一个字没动",
                  "指纹对不上，包已经删掉了" in text and "本机版本一个字没动" in text,
                  text[-300:])
            check("当场也给个框（日志里一行字会被当成跑完了的噪声）",
                  bool(modals.warnings) and "没拿到可用的安装包" in modals.warnings[-1][1],
                  modals.warnings)
            check("重试的出口说出来（只说失败不说下一步，用户只能关工具）",
                  "再点一次「检查更新」" in text, text[-300:])
            check("失败那一支不许写升级报告（写了下一次开机就会播一句「已经装上」）",
                  eng.written_reports == [] and "已经装上" not in text,
                  eng.written_reports)
            # 自相矛盾那一形：报成功却没递路径
            reset_prefs()
            modals.clear()
            eng.script_download = {"ok": True, "path": "", "sha256": "a" * 64, "why": ""}
            win._update_worker = None
            win._check_app_update(manual=True, forced=verdict("update-available"))
            text = log_text(win)
            check("「下成了」却没递路径时同样停下：拿空路径起交接进程只会多一个报错窗口",
                  "没给出安装包路径" in text and not win._update_handoff_cmd
                  and win.handoffs == [], text[-200:])
        finally:
            close_window(win)


# ── ⑫ 采集进行中先问一句、默认「否」───────────────────────────
def _test_crawling_guard(modals):
    print("\n── ⑫ 采集进行中点「现在就装」：先问一句，默认「否」 ──")
    reset_prefs()
    modals.clear()
    with FakeDisplay():
        modals.pick = "现在就装"
        modals.answer = QMessageBox.No
        win, _eng = build(state="update-available", worker_running=True)
        try:
            title, text, extra = modals.questions[-1]
            check("采集正在跑时不会闷头把窗口关掉（跑了一半被升级打断必须事先知道）",
                  "现在正在采集" in title, (title, text[:120]))
            check("问的话两边都说：已采的会落盘，正在跑的那几家会停下",
                  "落盘，不会丢" in text and "正在跑的那几家会停下" in text, text[:260])
            check("默认按钮是「否」（默认值就是替用户做的那个决定，升级不该是默认）",
                  QMessageBox.No in extra, extra)
            check("答「否」之后不下载、不关窗，并把入口留在横幅上",
                  SyncUpdateWorker.started == ["check"] and closes_of(win) == []
                  and not win.banner.isHidden() and "#update-show" in banner_links(win),
                  (SyncUpdateWorker.started, closes_of(win), banner_links(win)))
            check("那句回话说清下一步（「先不动」之后人想知道什么时候能动）",
                  "先不动这次采集" in log_text(win), log_text(win)[-200:])
        finally:
            close_window(win)
        reset_prefs()
        modals.clear()
        modals.pick = "现在就装"
        modals.answer = QMessageBox.Yes
        win2, _eng2 = build(state="update-available", worker_running=True)
        try:
            check("答「是」之后照样往下走：问了不往下走与按钮坏了在人眼里是同一件事",
                  SyncUpdateWorker.started == ["check", "download"]
                  and win2.handoffs == ["handoff"]
                  and closes_of(win2) == [True],
                  (SyncUpdateWorker.started, win2.handoffs, closes_of(win2)))
        finally:
            close_window(win2)


# ── ⑬ 上一回升级的结果只播一次、失败那条必须挂在屏幕上 ────────────
def _test_report_announce(modals):
    print("\n── ⑬ 上一回升级的结果由这一次开窗口说，且只说一次 ──")
    reset_prefs()
    modals.clear()
    rep_ok = {"ts": "1760000000", "ok": True, "fell_back": False, "verified": True,
              "expect_build": 234, "why": ""}
    win, eng = build(report=rep_ok)
    try:
        check("交接进程stdio 进黑洞，成败都得由下一个实例转述",
              "上一次升级已经装上" in log_text(win) and "234" in log_text(win),
              log_text(win)[-300:])
        check("成功那条只进日志，不挂横幅（屏幕上不留一条不需要任何人做事的黄条）",
              win.banner.isHidden(), win.banner.text())
        check("播过一次就按 ts 记住（同一条天天重说，很快就被当背景噪声划掉）",
              read_prefs().get(gui._UPDATE_REPORT_PREF_KEY) == "1760000000", read_prefs())
    finally:
        close_window(win)
    modals.clear()
    win2, _eng2 = build(report=rep_ok)
    try:
        check("第二次开机不重播同一条（只播紧接升级那一次才有用）",
              "上一次升级已经装上" not in log_text(win2), log_text(win2)[-200:])
    finally:
        close_window(win2)
    rep_bad = {"ts": "1760000099", "ok": False, "fell_back": True, "verified": False,
               "expect_build": 234, "why": "新包自报的构建号跟清单对不上"}
    modals.clear()
    win3, _eng3 = build(report=rep_bad)
    try:
        text = log_text(win3)
        check("失败那条必须挂在屏幕上（用户此刻想知道的是「那我怎么办」）",
              not win3.banner.isHidden()
              and banner_links(win3) == ["#update-recheck"], banner_links(win3))
        check("「旧版本已经重新打开了」与「没换成新版」分开说：不许说成工具没了",
              "原来的版本重新打开" in text and "没换成新版本" in text, text[-300:])
        check("原因逐字带上来（交接进程自己已经不存在了，这是唯一一份证据）",
              "新包自报的构建号跟清单对不上" in text, text[-300:])
    finally:
        close_window(win3)
    rep_unverified = {"ts": "1760000199", "ok": True, "fell_back": False,
                      "verified": False, "expect_build": 234, "why": ""}
    modals.clear()
    win4, _eng4 = build(report=rep_unverified)
    try:
        check("装上了但没能再对一次新包自报的号：这一句必须留在日志里",
              "没能再对一次新包自报的构建号" in log_text(win4), log_text(win4)[-300:])
        check("但它不是失败：不许因此挂一条「没装上」的横幅",
              win4.banner.isHidden(), win4.banner.text())
    finally:
        close_window(win4)
    # 没有报告的常态：一句也不许多
    reset_prefs()
    modals.clear()
    win5, _eng5 = build(report={})
    try:
        check("上一回没升级过就一个字也不提（不制造不存在的历史）",
              "上一次升级" not in log_text(win5) and win5.banner.isHidden(),
              log_text(win5)[-200:])
    finally:
        close_window(win5)


# ── ⑭ 模块没加载上时界面不许崩 ─────────────────────────────────
def _test_module_missing(modals):
    print("\n── ⑭ 升级模块没进包：不提更新、不报崩、两个入口也不退化成建界面 ──")
    reset_prefs()
    modals.clear()
    win, eng = build(state="update-available", has_update=False)
    try:
        check("模块没加载上只说这一句，不拿一个猜的版本号冒充结论",
              "升级模块" in log_text(win) and "tool_update" in log_text(win),
              log_text(win)[-300:])
        check("不弹框、不报崩（想提醒用户升级这件事本身绝不能把工具带崩）",
              not modals.box and closes_of(win) == [], modals.box)
        check("横幅上也不许留一条点开会出错的链接",
              win.banner.isHidden() and not banner_links(win), win.banner.text())
        win._check_app_update(manual=True)          # 用户还是按了那颗按钮
        check("手动点下去照样只说那一句（不拆接口、不报未定义）",
              log_text(win).count("升级模块") == 2, log_text(win)[-200:])
    finally:
        close_window(win)
    saved = (gui.HAS_TOOL_VERSION, gui.HAS_TOOL_UPDATE)
    gui.HAS_TOOL_VERSION = gui.HAS_TOOL_UPDATE = False
    try:
        rc_print = gui._print_version_entry([gui.PRINT_VERSION_FLAG])
        check("自报入口在模块缺失时必须以非零退出码说话（发包门禁靠的就是这个码）",
              rc_print == 3, rc_print)
        rc_apply = gui._apply_update_entry([gui.UPDATE_APPLY_FLAG, "/tmp/x.zip"])
        check("交接入口同样：认出 --apply-update 就绕不到建界面那一路，缺失时红给它看",
              rc_apply == 3, rc_apply)
    finally:
        gui.HAS_TOOL_VERSION, gui.HAS_TOOL_UPDATE = saved


# ── ⑮ 版本号进标题与状态行，未能确认时不许拼假号 ────────────────
def _test_version_display(modals):
    print("\n── ⑮ 版本号：进标题与状态行，未能确认时不许拼出看着合法的号 ──")
    saved = (gui._tool_version, gui.HAS_TOOL_VERSION)
    reset_prefs()
    modals.clear()
    try:
        gui.HAS_TOOL_VERSION = True
        gui._tool_version = types.SimpleNamespace(
            version_text=lambda: "1.0-dev（未注入构建号，本机版本未能确认）",
            certain=lambda: False, build_number=lambda: 0)
        check("未能确认时短形式不许拼出一个看着合法的号（「1.0」会被当成第一版报上来）",
              gui._version_short() == "版本未能确认", gui._version_short())
        win, _eng = build(state="up-to-date")
        try:
            check("标题带版本号（远程支持第一句就是「你那版是几」）",
                  "版本未能确认" in win.windowTitle(), win.windowTitle())
            check("长诊断句不进界面几何：状态行只放短形式（一行长文案能把主按钮顶出屏幕）",
                  win.lbl_version.text() == "版本未能确认"
                  and "未注入构建号" not in win.lbl_version.text(), win.lbl_version.text())
            check("tooltip 保留完整那一句（短形式是给眼睛的，完整是给远程排障的）",
                  "未注入构建号" in win.lbl_version.toolTip(), win.lbl_version.toolTip())
        finally:
            close_window(win)
        gui.HAS_TOOL_VERSION = False
        check("版本模块没加载上也要照实说，不退回一个看起来正常的号",
              "版本未知" in gui._version_display() and "1.0" not in gui._version_short(),
              (gui._version_display(), gui._version_short()))
    finally:
        gui._tool_version, gui.HAS_TOOL_VERSION = saved


# ── 界面上不许分叉的几条写法 ──────────────────────────────────
def _test_wiring_source_rules():
    print("\n── ⑩～⑮ 的背面：同一件事只许有一份口径 ──")
    import inspect as _inspect
    src_all = (_ROOT / "b2bcollect_build" / "b2bcollect_gui.py").read_text(encoding="utf-8")
    src_main = _inspect.getsource(gui.main)
    i_app = src_main.find("QApplication(")
    check("--print-version / --apply-update 在 QApplication 之前就分派（晚一步就会在正占着"
          "安装目录的进程里再开一个窗口）",
          0 <= src_main.find("PRINT_VERSION_FLAG") < i_app
          and 0 <= src_main.find("UPDATE_APPLY_FLAG") < i_app,
          (src_main.find("PRINT_VERSION_FLAG"), src_main.find("UPDATE_APPLY_FLAG"), i_app))
    check("「屏幕前有没有人」只有一个写法（多处各判一次迟早有一处忘了判，而那一处只在流水线上红）",
          src_all.count('os.environ.get("QT_QPA_PLATFORM")') == 1,
          src_all.count('os.environ.get("QT_QPA_PLATFORM")'))
    for name in ("_on_update_checked", "_show_update_banner"):
        body = _inspect.getsource(getattr(gui.MainWindow, name))
        check(f"{name} 只按 state 分派，不另设 ok/has_update 布尔位（两套口径迟早分家）",
              'get("ok")' not in body and "has_update" not in body and 'state"' in body,
              body[:200])
    src_prompt = _inspect.getsource(gui.MainWindow._prompt_update)
    check("升级弹窗里「能不能自动装」问的是这份制品，不是本机（本机推出来的那几步可能是假话）",
          "handoff_kind(art)" in src_prompt and "install_steps(art)" in src_prompt
          and "platform_key()" not in src_prompt, src_prompt[:200])
    for name in ("_start_update_download", "_after_update_download", "_begin_update_handoff"):
        body = _inspect.getsource(getattr(gui.MainWindow, name))
        check(f"{name} 不自己拼下载地址与指纹（那一块的真源只有清单与 tool_update）",
              "BUCKET_HOST" not in body and "sha256" not in body
              and "RELEASE_PREFIX" not in body, body[:160])
    dispatch = _inspect.getsource(gui.MainWindow._on_banner_link)
    check("横幅上两条链接都能被 _on_banner_link 认出来（写了链接没分派=点了没反应）",
          all(f'"#{link}"' in dispatch
              for link in ("update-show", "update-recheck")), dispatch[:300])
    # 兼容默认值那一排：模块没加载上时用的就是这几个字面量，与真模块不一致等于两个名字
    for lit, want in (("UPDATE_APPLY_FLAG", real_tu.APPLY_FLAG),
                      ("UPDATE_WAIT_PID_FLAG", real_tu.WAIT_PID_FLAG),
                      ("UPDATE_EXPECT_BUILD_FLAG", real_tu.EXPECT_BUILD_FLAG),
                      ("UPDATE_RELAUNCH_FLAG", real_tu.RELAUNCH_FLAG)):
        got = re.search(lit + r' = "([^"]+)"', src_all)
        check(f"{lit} 的字面量默认值与 tool_update 那份同字（{(got.group(1)) if got else None}）",
              bool(got) and got.group(1) == want, (lit, want, got.group(1) if got else None))


def main():
    print("═══ 版本提示与一键升级 · 界面接线 离线测试 ═══")
    modals = Modals()
    modals.install()
    saved = (gui._tool_update, gui.HAS_TOOL_UPDATE, gui.HAS_TOOL_VERSION,
             gui.UpdateWorker, gui._chrome_doctor, gui.ChromeDoctorWorker,
             gui._ui_prefs_path)
    try:
        _test_silent_states(modals)
        _test_prompt_content(modals)
        _test_not_forced(modals)
        _test_snooze_by_version(modals)
        _test_unsupported(modals)
        _test_bad_manifest(modals)
        _test_unknown(modals)
        _test_manual_speaks(modals)
        _test_download_then_handoff(modals)
        _test_download_failed(modals)
        _test_crawling_guard(modals)
        _test_report_announce(modals)
        _test_module_missing(modals)
        _test_version_display(modals)
        _test_wiring_source_rules()
    finally:
        modals.restore()
        (gui._tool_update, gui.HAS_TOOL_UPDATE, gui.HAS_TOOL_VERSION,
         gui.UpdateWorker, gui._chrome_doctor, gui.ChromeDoctorWorker,
         gui._ui_prefs_path) = saved
    print(f"\n═══ 结果：{_PASS} 通过 / {_FAIL} 失败 ═══")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    _rc = main()
    sys.stdout.flush()
    sys.stderr.flush()
    # 本套件建过 QApplication：Qt 在解释器收尾时的析构会随机段错误（同一族实测：
    # 全绿之后 rc=139，被批跑读成一次失败、单跑又绿）。判定已经打完、输出已经 flush，
    # 所以退出码由自己给，不留给析构去改写（硬门第 23 条判的就是这件事）。
    os._exit(_rc)
