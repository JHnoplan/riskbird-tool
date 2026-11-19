# -*- coding: utf-8 -*-
"""chrome_env_offline_test.py —— Chrome 体检/一键安装接到界面上的那一段（需求 3）。

引擎层的判据在 b2bcollect_build/test_chrome_doctor_offline.py（那边管「结论对不对」）；
这一份只管**接线**：体检有没有真的在打开工具时跑起来、没装时横幅出不出来、横幅上那两个
动作点下去会不会走到该走的地方、装完横幅收不收得掉、浏览器启动失败那一句有没有被换掉。

为什么要单独一份：引擎测全绿而界面没接上是这一族最常见的「功能做完了没人能用」的形状
（本仓库已经为此改过一次：某条横幅链接的槽被删过一格，引擎与界面各自都绿）。这里刻意
用**真 MainWindow**——顶掉线程与弹框，但界面本身是真的，所以「忘了接线」「方法名打错」
「横幅写了两条链接但只分派了一条」这类红只有真建界面才看得见。

顶掉的三样东西与原因
------------------
① `ChromeDoctorWorker`：改成 start() 里同步跑。offscreen 里等 QThread 就是等一件不能
   确定何时结束的事，而本套件要验的是数据流与话术，不是线程调度。
② `QMessageBox.exec_` / `information` / `warning`：模态框在无人值守下会永久挂住（本仓库
   批跑曾因此被拖死 20 分钟）。这里记下弹框的标题、正文、按钮，并按指定那一条"点下去"。
③ `_chrome_doctor` 整个模块：换成只记账的替身，保证「跑一遍测试不会装一个浏览器」，
   也不会真去 dl.google.com。

十三组断言各自盯一个具体事故
--------------------------
① 打开工具就真的量一次（不量就永远不知道对方没装）；② 装了的人一条消息都不许多；
③ 「读不到」绝不当成「没装」去推销安装；④ 没装时横幅必须出现且两条动作都在；
⑤ 不抢别人在场的横幅；⑥ 弹框逐条说清会发生什么、按钮名跟着通路走；
⑦ 一次只跑一路（并发两次安装只会互相抢）；⑧ 装完必须当场告诉等了几分钟的人；
⑨ 装不成时仍留官网出口；⑩ 官网那条兜底把地址真递到手边；⑪ 浏览器启动失败按三种情况分岔；
⑫ 模块没打进包时界面不许崩；⑬ 「我已经装好了」必须重新量一遍并收起横幅。

运行：QT_QPA_PLATFORM=offscreen .venv/bin/python debug/chrome_env_offline_test.py
"""
import os
import sys
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "b2bcollect_build"))

from PyQt5.QtWidgets import QApplication, QMessageBox                    # noqa: E402

import b2bcollect_gui as gui                                             # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])

MISSING_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

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


# ── 替身一：chrome_doctor 模块 ──────────────────────────────────────────
class FakeDoctor:
    """界面看到的那一份引擎：只按脚本给的事实回答，并记下被问过什么。"""

    PAGE_OFFICIAL = "https://www.google.com/chrome/"
    PAGE_CN = "https://www.google.cn/chrome/"

    def __init__(self, info=None):
        self.info = info or {"installed": False, "certain": True,
                             "probe_error": "", "path": "", "launch_path": "",
                             "version": "", "platform": "macOS 15.7",
                             "evidence": ["候选路径不在：/Applications/Google Chrome.app"],
                             "manager": ""}
        self.calls = []
        self.install_result = {"ok": False, "tried": [], "info": self.info,
                               "page": self.PAGE_OFFICIAL}
        self.page_result = {"ok": True, "url": self.PAGE_OFFICIAL}

    def inspect(self):
        self.calls.append("inspect")
        return dict(self.info)

    def install_plan(self, info=None):
        self.calls.append("install_plan")
        info = info or self.info
        if not info.get("certain", True):
            return [{"kind": "recheck", "how": "recheck",
                     "label": "先重新确认一次这台机器有没有 Chrome",
                     "detail": "这次体检没读成功：%s" % info.get("probe_error", "")},
                    self._page()]
        return [{"kind": "winget", "how": "winget",
                 "label": "用 Windows 自带的「应用安装程序」装（推荐）",
                 "detail": "命令：winget install -e --id Google.Chrome --silent\n"
                           "微软可能弹一次授权窗"},
                self._page()]

    @staticmethod
    def _page():
        return {"kind": "page", "how": "page", "label": "打开 Chrome 官网自己下载",
                "detail": "官方页面：%s\n国内可试：%s" % (FakeDoctor.PAGE_OFFICIAL,
                                                          FakeDoctor.PAGE_CN)}

    def install(self, log=None, only=None):
        self.calls.append(("install", tuple(only or ())))
        if log:
            log("正在用 Windows 自带的「应用安装程序」装…")
        res = dict(self.install_result)
        res["info"] = dict(res.get("info") or self.info)
        return res

    def summary_text(self, info=None):
        info = info or self.info
        if info.get("installed"):
            head = "已安装：%s" % info.get("launch_path", "")
        elif not info.get("certain", True):
            head = "没读到 Chrome（这次体检本身失败了），所以**不能断定没装**：%s" % \
                info.get("probe_error", "")
        else:
            head = "没有找到 Google Chrome：本工具的采集要用它。"
        return head + "\n· " + "\n· ".join(info.get("evidence") or [])

    def open_official_page(self):
        self.calls.append("open_official_page")
        return dict(self.page_result)


# ── 替身二：同步跑的 worker ────────────────────────────────────────────
class SyncWorker:
    """`start()` 里当场把 `run()` 跑完：offscreen 不等线程，只看数据流。"""

    started = []
    live = False

    def __init__(self, mode="inspect", only=None):
        self.mode = mode
        self.only = only
        self.logged = _Signal()
        self.finished_doctor = _Signal()

    def isRunning(self):
        return SyncWorker.live

    def start(self):
        SyncWorker.started.append((self.mode, tuple(self.only or ())))
        self.run()

    def run(self):
        try:
            if self.mode == "install":
                res = gui._chrome_doctor.install(log=lambda m: self.logged.emit(m),
                                                 only=self.only)
                self.finished_doctor.emit("install", res, "")
                return
            self.finished_doctor.emit("inspect", gui._chrome_doctor.inspect(), "")
        except Exception as e:
            self.finished_doctor.emit(self.mode, None, f"{type(e).__name__}: {e}")


class _Signal:
    def __init__(self):
        self._slots = []

    def connect(self, fn):
        self._slots.append(fn)

    def emit(self, *a):
        for fn in self._slots:
            fn(*a)


# ── 替身三：模态框（无人值守下 exec_ 会永久挂住）──────────────────────
class Modals:
    """记下每一次弹框，并按指定那一条按钮"点下去"。

    为什么要一并接管 `setWindowTitle`：QMessageBox 把标题缓存在自己私有那份里，
    到 show 时才写给窗口，所以下窗之前 `box.windowTitle()` 永远读回空串（实测）。
    不接管它就等于「弹框标题写错了」这一整类缺陷在这里测不到。
    """

    def __init__(self):
        self.box = []           # [(标题, 正文, [按钮名])]
        self.informations = []
        self.warnings = []
        self.pick = None        # 下一次 exec_ 要点掉的按钮名（None = 点「取消」那一路）
        self.titles = {}        # id(box) -> setWindowTitle 递进去的那一句

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
            lambda parent, title, text, *a: self.box.append((title, text, []))
            or QMessageBox.No)

    def restore(self):
        (QMessageBox.exec_, QMessageBox.information, QMessageBox.warning,
         QMessageBox.question, QMessageBox.setWindowTitle) = self._saved


def build(info=None):
    """按「管理员打开工具」那条路真建一个主界面，并把三样替身接上去。"""
    doctor = FakeDoctor(info)
    gui._chrome_doctor = doctor
    gui.HAS_CHROME_DOCTOR = True
    gui.ChromeDoctorWorker = SyncWorker
    SyncWorker.started = []
    SyncWorker.live = False
    win = gui.MainWindow()
    win._chrome_worker = None            # __init__ 里那个真实 worker 不算
    return win, doctor


def banner_links(win):
    import re
    return re.findall(r'href="(#[a-z-]+)"', win.banner.text())


def main():
    print("═══ Chrome 体检 · 界面接线 离线测试 ═══")
    modals = Modals()
    modals.install()
    saved = (gui._chrome_doctor, gui.HAS_CHROME_DOCTOR, gui.ChromeDoctorWorker)
    try:
        _test_installed(modals)
        _test_missing_banner(modals)
        _test_probe_uncertain(modals)
        _test_offer_and_install(modals)
        _test_install_outcome(modals)
        _test_page_fallback(modals)
        _test_launch_failed(modals)
        _test_module_missing(modals)
        _test_recheck_closes(modals)
        _test_wiring(modals)
    finally:
        modals.restore()
        (gui._chrome_doctor, gui.HAS_CHROME_DOCTOR,
         gui.ChromeDoctorWorker) = saved
    print(f"\n═══ 结果：{_PASS} 通过 / {_FAIL} 失败 ═══")
    return 0 if _FAIL == 0 else 1


# ── ①② 装了的人一条消息都不许多 ────────────────────────────────────────
def _test_installed(modals):
    print("\n── ① 已装 Chrome：安静 ──")
    info = {"installed": True, "certain": True, "probe_error": "",
            "path": MISSING_PATH, "launch_path": MISSING_PATH, "version": "131.0.0.0",
            "platform": "macOS 15.7", "evidence": ["候选路径在"], "manager": ""}
    win, doctor = build(info)
    try:
        win._check_chrome_env()
        check("打开工具真的量了一次（不量就永远不知道对方没装）",
              doctor.calls.count("inspect") == 1, doctor.calls)
        check("已装 ⇒ 横幅不出（正常机器一条也不会多）", win.banner.isHidden(),
              win.banner.text())
        check("已装 ⇒ 也不弹任何东西", not modals.box and not modals.informations,
              modals.box)
        check("已装 ⇒ 不问安装通路（一次 install_plan 都没递）",
              "install_plan" not in doctor.calls, doctor.calls)
    finally:
        win.close()


# ── ④⑤ 没装时横幅必须出现，且不抢别人的 ────────────────────────────────
def _test_missing_banner(modals):
    print("\n── ② 没装：横幅 + 两个能点的动作 ──")
    win, doctor = build()
    try:
        win._check_chrome_env()
        check("没装 ⇒ 横幅出现（这句提示必须常显，写在日志里等人翻等于没说）",
              not win.banner.isHidden())
        links = banner_links(win)
        check("横幅上两个动作都在：怎么装 / 我已经装好了（只给一个就会让人以为只能自动）",
              "#chrome-fix" in links and "#chrome-recheck" in links, links)
        check("两个动作都能被 _on_banner_link 认出来（写了链接没分派=点了没反应，"
              "正是需求 4 那一族形状）",
              all(link in _dispatchable() for link in links), links)
        check("体检结论同时进日志（远程排障只看日志也要能回答「这台有没有」）",
              any("没有找到 Google Chrome" in m for m in win.log.toPlainText().splitlines()),
              win.log.toPlainText()[:200])
        # 已经有别的提示在场
        win2, doctor2 = build()
        try:
            win2._show_banner("🔐 需要登录", "#fff3cd", "#664d03")
            win2._chrome_banner_live = False
            win2._check_chrome_env()
            check("已经有别的横幅在场时不抢（登录那一条被顶掉的话，用户就不知道自己欠什么了）",
                  "需要登录" in win2.banner.text(), win2.banner.text())
            check("不抢但仍然出声：那句话落到日志里",
                  "没有找到 Google Chrome" in win2.log.toPlainText(),
                  win2.log.toPlainText()[:200])
        finally:
            win2.close()
    finally:
        win.close()


def _dispatchable():
    """`_on_banner_link` 真正认得的动作名（从源码里读，不另抄一份清单）。"""
    import inspect as _inspect
    src = _inspect.getsource(gui.MainWindow._on_banner_link)
    return [s for s in ("#acl-restart", "#app-relocate", "#chrome-fix", "#chrome-recheck")
            if f'"{s}"' in src]


# ── ③ 「读不到」绝不当成「没装」 ────────────────────────────────────────
def _test_probe_uncertain(modals):
    print("\n── ③ 读不到 ≠ 没装：不推销安装、不出横幅 ──")
    info = {"installed": False, "certain": False,
            "probe_error": "ImportError: no module named selenium", "path": "",
            "launch_path": "", "version": "", "platform": "macOS 15.7",
            "evidence": ["启动器没读到"], "manager": ""}
    win, doctor = build(info)
    try:
        win._check_chrome_env()
        text = win.log.toPlainText()
        check("日志把「读不到」与「没装」分开说（混起来就会劝一台装好了的机器重装）",
              "读不到" in text and "没装" in text, text[:300])
        check("这种情况不出横幅（横幅是「去做一件事」的入口，而这里没有该做的事）",
              win.banner.isHidden(), win.banner.text())
        check("一条安装通路都不递（install_plan 只在用户主动点的时候才问）",
              "install_plan" not in doctor.calls, doctor.calls)
        # 用户还是点了那条链接：给的是「先重新确认」，不是「装一个浏览器」
        doctor.calls.clear()
        modals.pick = "先重新确认"
        SyncWorker.live = False
        win._chrome_info = info
        win._offer_chrome_fix()
        check("主动点进去时第一步是「重新确认」，按钮名跟着通路走（不许统一写成「自动安装」："
              "点了它并不会装任何东西）",
              any(any("先重新确认" in b for b in names) for _t, _x, names in modals.box),
              modals.box)
        check("那一条按下后走的是重新体检，不是安装",
              SyncWorker.started and SyncWorker.started[-1][0] == "inspect",
              SyncWorker.started)
        check("重新确认那一步没动过任何安装通路（install_plan 只是被问了一下有哪些路）",
              not any(isinstance(c, tuple) and c[0] == "install" for c in doctor.calls),
              doctor.calls)
    finally:
        win.close()


# ── ⑥⑦ 引导弹框与并发闸门 ─────────────────────────────────────────────
def _test_offer_and_install(modals):
    print("\n── ④ 引导弹框：逐条说清会发生什么 ──")
    win, doctor = build()
    try:
        win._check_chrome_env()
        modals.box.clear()
        modals.pick = "先不装"
        win._offer_chrome_fix()
        title, text, names = modals.box[-1]
        check("弹框把每条通路的 label 与 detail 都写上了（只写「是否安装」= 用户不知道"
              "会不会弹授权、要等多久）",
              "winget install" in text and "授权" in text, text[:300])
        check("按钮一共三个：那条自动通路 / 打开官网 / 先不装（少一个就等于替用户决定）",
              len(names) == 3, names)
        check("点了「先不装」就一条都不动（不下载、不安装、不开网页）",
              doctor.calls.count("open_official_page") == 0
              and not any(str(c).startswith("install") and isinstance(c, tuple)
                          for c in doctor.calls), doctor.calls)

        # 点自动安装：only 只该带非 page 的通路
        doctor.calls.clear()
        modals.box.clear()
        modals.pick = "用 Windows 自带"
        SyncWorker.started = []
        win._offer_chrome_fix()
        check("点那条自动通路 ⇒ 只递非 page 的动作名（page 不是能 install 的 how）",
              SyncWorker.started and SyncWorker.started[-1] == ("install", ("winget",)),
              SyncWorker.started)

        # 并发闸门：一路还在跑时第二路必须被挡
        doctor.calls.clear()
        SyncWorker.started = []
        SyncWorker.live = True
        win._chrome_worker = SyncWorker("install", only=["winget"])
        win._start_chrome_install(["winget"])
        check("安装还在跑时再点一次不起第二路（两条安装同时跑只会互相抢，且用户会看到两个进度）",
              not SyncWorker.started, SyncWorker.started)
        check("挡住了也要说清为什么（静默不理会被当成界面卡住）",
              "还在跑" in win.log.toPlainText(), win.log.toPlainText()[-300:])
        SyncWorker.live = False
    finally:
        win.close()


# ── ⑧⑨ 安装回来的两种收场 ─────────────────────────────────────────────
def _test_install_outcome(modals):
    print("\n── ⑤ 装完了要当场说话，装不成要留出口 ──")
    win, doctor = build()
    try:
        win._check_chrome_env()
        check("前置：横幅已经挂着", not win.banner.isHidden() and win._chrome_banner_live)
        doctor.install_result = {
            "ok": True,
            "tried": [{"how": "winget", "label": "用 Windows 自带的「应用安装程序」装",
                       "ok": True, "why": ""}],
            "info": {"installed": True, "certain": True, "probe_error": "",
                     "launch_path": MISSING_PATH, "version": "131.0.0.0",
                     "evidence": [], "platform": "macOS", "manager": ""},
            "page": FakeDoctor.PAGE_OFFICIAL}
        modals.informations.clear()
        win._chrome_worker = SyncWorker("install", only=["winget"])
        win._after_chrome_install(doctor.install_result)
        check("装成 ⇒ 横幅收起（挂着一条已经不成立的提示比没有提示更坏）",
              win.banner.isHidden() and not win._chrome_banner_live, win.banner.text())
        check("装成 ⇒ 当场告诉等了几分钟的人（只进日志等于让人一直盯着屏幕等）",
              any("Chrome 装好了" in t for t, _x in modals.informations),
              modals.informations)
        check("话里带上「不用重启工具」（这句能省掉一次无谓的关开）",
              any("不用重启" in x for _t, x in modals.informations), modals.informations)
        check("重新量过一次的结论被缓存下来（之后浏览器启动失败那一路直接用它）",
              win._chrome_info.get("installed") is True, win._chrome_info)

        # 失败那一路
        win2, doctor2 = build()
        try:
            win2._check_chrome_env()
            doctor2.install_result = {
                "ok": False,
                "tried": [{"how": "winget", "label": "用 Windows 自带的「应用安装程序」装",
                           "ok": False, "why": "这台机器上没有这个命令"},
                          {"how": "google_installer", "label": "从 Google 官方地址下载并安装",
                           "ok": False, "why": "连不上对方：没有网络"}],
                "info": {"installed": False, "certain": True, "probe_error": "",
                         "launch_path": "", "evidence": [], "platform": "macOS",
                         "manager": ""},
                "page": FakeDoctor.PAGE_OFFICIAL}
            log0 = win2.log.toPlainText()
            modals.informations.clear()
            win2._after_chrome_install(doctor2.install_result)
            text = win2.log.toPlainText()
            check("失败 ⇒ 逐条台账进日志（哪条路、为什么没走通，只有一句「安装失败」没法行动）",
                  "这台机器上没有这个命令" in text and "连不上对方" in text,
                  text[len(log0):][:400])
            check("失败 ⇒ 横幅仍留着官网那条出口，并且仍可「我已经装好了」",
                  "#chrome-recheck" in banner_links(win2)
                  and FakeDoctor.PAGE_OFFICIAL in win2.banner.text(), win2.banner.text())
            check("失败 ⇒ 不弹「装好了」那种窗（一条成功都没发生）",
                  not any("装好了" in t for t, _x in modals.informations),
                  modals.informations)
        finally:
            win2.close()
    finally:
        win.close()


# ── ⑩ 官网兜底 ─────────────────────────────────────────────────────────
def _test_page_fallback(modals):
    print("\n── ⑥ 官网那一条：打不开也要把地址递到手边 ──")
    win, doctor = build()
    try:
        doctor.page_result = {"ok": True, "url": FakeDoctor.PAGE_OFFICIAL}
        win._open_chrome_page()
        check("能打开就说清「装完回来点再确认一次」（不这么说人就只会重启工具）",
              "再确认一次" in win.log.toPlainText(),
              win.log.toPlainText()[-400:])
        doctor.page_result = {"ok": False, "url": FakeDoctor.PAGE_OFFICIAL,
                              "why": "这台机器的默认浏览器打不开"}
        app.clipboard().setText("")
        win._open_chrome_page()
        text = win.log.toPlainText()
        check("打不开 ⇒ 把官方地址复制到剪贴板并说明（没浏览器的机器正是这一族用户）",
              FakeDoctor.PAGE_OFFICIAL in app.clipboard().text()
              and "剪贴板" in text, (app.clipboard().text(), text[-300:]))
    finally:
        win.close()


# ── ⑪ 浏览器启动失败那三岔 ─────────────────────────────────────────────
def _test_launch_failed(modals):
    print("\n── ⑦ 浏览器启动失败：不再只留一句「请确认已安装」 ──")
    win, doctor = build()
    try:
        # (a) 已经量过且确实没装 ⇒ 直接给处置
        win._check_chrome_env()
        modals.box.clear()
        modals.warnings.clear()
        modals.pick = "先不装"
        win._on_browser_launch_failed()
        check("已知没装 ⇒ 直接走处置那一路（旧那句「请确认已安装 Google Chrome」"
              "就是把判断推回给人）",
              modals.box and "这台机器没有 Google Chrome" in modals.box[-1][0],
              modals.box)
        check("这一路不再打那句万金油",
              not any("请确认已安装" in t for _t, t in modals.warnings), modals.warnings)

        # (b) 压根还没量过 ⇒ 先量，别猜
        win2, doctor2 = build()
        try:
            doctor2.calls.clear()
            SyncWorker.started = []
            win2._chrome_info = None
            modals.warnings.clear()
            modals.pick = "知道了"
            win2._on_browser_launch_failed()
            check("没量过 ⇒ 先起一次后台体检（宁可晚一句，也不拿猜的结论劝人）",
                  SyncWorker.started and SyncWorker.started[-1][0] == "inspect",
                  SyncWorker.started)
            check("同时那句原话照说，并给一个「确认一下本机有没有 Chrome」的动作",
                  modals.box
                  and any(any("确认一下本机" in b for b in names)
                          for _t, _x, names in modals.box),
                  modals.box)
            # (c) 装着但起不来 ⇒ 说的是关窗口，不是装浏览器
            win3, doctor3 = build({"installed": True, "certain": True, "probe_error": "",
                                   "launch_path": MISSING_PATH, "path": MISSING_PATH,
                                   "version": "131.0.0.0", "evidence": [],
                                   "platform": "macOS", "manager": ""})
            try:
                win3._check_chrome_env()
                modals.box.clear()
                modals.pick = "知道了"
                win3._on_browser_launch_failed()
                text = modals.box[-1][1]
                check("Chrome 明明装着却起不来 ⇒ 说的是「先完全关掉 Chrome 再试」，"
                      "不是劝人再装一遍",
                      "关掉" in text and "没装" not in text, text[:300])
            finally:
                win3.close()
        finally:
            win2.close()
    finally:
        win.close()


# ── ⑫ 模块没进包时不许崩 ───────────────────────────────────────────────
def _test_module_missing(modals):
    print("\n── ⑧ chrome_doctor 没加载上：退回人工提示，界面不许崩 ──")
    win, doctor = build()
    try:
        win._check_chrome_env()
        check("前置：正常情况横幅会出", not win.banner.isHidden())
        gui.HAS_CHROME_DOCTOR = False
        try:
            win._check_chrome_env()
            win._offer_chrome_fix()
            win._open_chrome_page()
            win._chrome_recheck_pending = True
            win._on_browser_launch_failed()
            check("四个入口全走一遍都不抛异常（装浏览器这件事绝不能把工具带崩）", True)
            check("退回那句人工提示（不能因为模块缺失就一句话都不说）",
                  any("请确认已安装" in t or "Google Chrome" in t
                      for _t, t in modals.warnings), modals.warnings)
            check("并且说清是检查模块没加载上（否则用户会以为是自己的机器坏了）",
                  "没能加载" in win.log.toPlainText(),
                  win.log.toPlainText()[-400:])
        finally:
            gui.HAS_CHROME_DOCTOR = True
    finally:
        win.close()


# ── ⑬ 我已经装好了 ─────────────────────────────────────────────────────
def _test_recheck_closes(modals):
    print("\n── ⑨ 「我已经装好了」必须重新量一遍 ──")
    win, doctor = build()
    try:
        win._check_chrome_env()
        check("前置：没装时横幅挂着", not win.banner.isHidden())
        doctor.calls.clear()
        doctor.info = {"installed": True, "certain": True, "probe_error": "",
                       "launch_path": MISSING_PATH, "path": MISSING_PATH,
                       "version": "131.0.0.0", "evidence": [], "platform": "macOS",
                       "manager": ""}
        SyncWorker.started = []
        win._on_banner_link("#chrome-recheck")
        check("点「我已经装好了」真的重新问了一次引擎（不重问就等于告诉用户「还没装」）",
              doctor.calls.count("inspect") == 1, doctor.calls)
        check("重问出已装 ⇒ 横幅当场收起", win.banner.isHidden(), win.banner.text())
        check("并且缓存更新（下一次启动浏览器失败就不会再劝人装）",
              win._chrome_info.get("installed") is True, win._chrome_info)
    finally:
        win.close()


def _test_wiring(modals):
    print("\n── ⑩ 接线本体：源码里那几处必须真的存在 ──")
    import inspect as _inspect
    src = Path(_ROOT / "b2bcollect_build" / "b2bcollect_gui.py").read_text(
        encoding="utf-8")
    check("打开工具就排了一次体检（只写在方法里没人调用=永远不知道对方没装）",
          "QTimer.singleShot(0, self._check_chrome_env)" in src)
    check("浏览器启动失败那一路改成了带动作的方法（旧那句只有一条话）",
          "self._on_browser_launch_failed()" in src
          and len(_user_text_hits(src, "请确认已安装 Google Chrome")) <= 1)
    _body = _inspect.getsource(gui.MainWindow._on_browser_launch_failed)
    check("那句旧话只出现在「体检模块没加载」那一处兜底里，主路不拿它顶替处置；"
          "同时方法里三岔都在（没装→递处置、没量过→先量、确认装着→说关掉重试）",
          len(_user_text_hits(src, "请确认已安装 Google Chrome"))
          == len(_user_text_hits(_body, "请确认已安装 Google Chrome")) == 1
          and "自动检查模块没能加载" in _body
          and all(s in _body for s in ("_offer_chrome_fix()", "_check_chrome_env()",
                                       "本机已确认装着")),
          _user_text_hits(src, "请确认已安装 Google Chrome"))
    check("体检与安装都在后台跑（安装实测 1-3 分钟，在界面线程里跑就是整窗冻结）",
          "class ChromeDoctorWorker(QThread)" in src)
    check("worker 把任何未捕获异常也发回界面（不然屏幕上的「正在检查」永远挂着）",
          "finished_doctor.emit(self.mode, None," in src)
    check("可选依赖写法在场（漏收模块时是少一个功能，不是崩一次）",
          "HAS_CHROME_DOCTOR = False" in src)
    check("chrome_doctor 与 download_verify 都登记进打包清单",
          all("hidden-import " + n in _all_manifests() for n in
              ("chrome_doctor", "download_verify")))


def _user_text_hits(src, needle):
    """源码里真会被显示出来的文字在哪些行（走 AST 取字符串常量，跳过 docstring）。

    不能拿整份文本数一遍：旧那句话现在只在注释和说明里被引用，数错就把「已经改完了」
    读成「还没改」，反过来也会把「主路上又写了一遍」读成绿。真正要盯的是用户看得见的那句。
    """
    import ast
    import textwrap
    tree = ast.parse(textwrap.dedent(src))
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            first = node.body[0] if node.body else None
            if (isinstance(first, ast.Expr)
                    and isinstance(getattr(first, "value", None), ast.Constant)
                    and isinstance(first.value.value, str)):
                docs.add(id(first.value))
    return [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docs and needle in n.value]


def _all_manifests():
    text = ""
    for p in (_ROOT / ".github" / "workflows").glob("build-b2bcollect*.yml"):
        text += p.read_text(encoding="utf-8")
    local = _ROOT / "debug" / "build_b2bcollect_mac_local.sh"
    if local.exists():
        text += local.read_text(encoding="utf-8")
    return text


if __name__ == "__main__":
    _rc = main()
    sys.stdout.flush()
    sys.stderr.flush()
    # 同上：本套件建过 QApplication，退出码不能交给解释器收尾里的 Qt 析构去改写
    # （全绿的结论被一句析构崩溃读成失败，是批跑里最难复现的那种假红）。
    os._exit(_rc)
