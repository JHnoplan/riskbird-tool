"""离线验证未登录门禁：判据、闸门三种出路、采集循环里的实际行为、界面弹窗。

全部不启动浏览器、不联网（都不超过 1 秒）：
1. 登录判据只看「登录之后才会下发的 Cookie」：企知道匿名会话也有名为 token 的
   Cookie，所以按"有没有 token"判会误判；读不到 Cookie 时返回 unknown 不拦人。
2. 空壳记录判据：法人/注册资本/地址 全空才算空壳；"未找到/失败"类不参与计数
   （风鸟"仅列表页"模式只有状态有值，本来就不该被这套判据拦）。
3. LoginGate：继续/停止/登录好了三种选择，重复提醒有上限，停止能立刻打断。
4. 企知道采集循环：未登录时一家都不采、不写空记录；选"仍然继续"后才往下走。
5. 空壳两家连出：不写入、不推进名单；用户登录好之后从卡住那家重新采。
6. 界面 _on_login_required：三个按钮各自的后续状态与红条文案。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QMessageBox  # noqa: E402

app = QApplication([])

import block_guard as bg  # noqa: E402
import qizhidao_crawler as Q  # noqa: E402
import riskbird_gui as G  # noqa: E402
from riskbird_gui import MainWindow  # noqa: E402

# ── 1. 登录判据 ──────────────────────────────────────
ANON_QZD = ["token", "acw_tc", "sensorsdata2015jssdkcross", "HMACCOUNT", "wz_uuid"]
LOGIN_QZD = ANON_QZD + ["accessToken", "creditNo", "param_sign"]
assert bg.login_state(ANON_QZD, "企知道")[0] == "missing", "只有匿名 token 不能算已登录"
assert bg.login_state(LOGIN_QZD, "企知道")[0] == "ok", "有 accessToken 即已登录"
assert bg.login_state(["BAIDUID", "HMACCOUNT"], "爱企查")[0] == "missing"
assert bg.login_state(["BAIDUID", "BDUSS"], "爱企查")[0] == "ok"
assert bg.login_state(None, "企知道")[0] == "unknown", "读不到 Cookie 时不能判未登录"
assert bg.login_state(["anything"], "没有这个平台")[0] == "unknown"
missing_detail = bg.login_state(ANON_QZD, "企知道")[1]
assert "accessToken" in missing_detail, "提示要说清缺哪个凭证"
print("① 登录判据：按登录凭证认，读不到时不误拦 ✓")

# ── 2. 空壳判据 ──────────────────────────────────────
SHELL_QZD = {"公司名": "聊城市鸿金管材有限公司", "状态": "", "法定代表人": "",
             "注册资本": "", "地址": "", "电话": "18996493859", "数据来源": "企知道"}
FULL_QZD = dict(SHELL_QZD, 状态="在营", 法定代表人="张三", 注册资本="500万", 地址="某某路1号")
assert bg.is_shell_record(SHELL_QZD) is True, "今天这种只有电话的记录必须算空壳"
assert bg.is_shell_record(FULL_QZD) is False
# 已知的查无/失败由别的分支处理，不参与空壳计数
assert bg.is_shell_record({"公司名": "x", "状态": "未找到"}) is False
assert bg.is_shell_record({"公司名": "x", "错误": "会话异常"}) is False
# 风鸟"仅列表页"模式：只有状态有值，但风鸟不走空壳闸门（这里只验证判据本身）
assert bg.is_shell_record({"公司名": "x", "状态": "在营"}) is True
# 爱企查把状态填成"成功"，仍应按硬字段判空壳
assert bg.is_shell_record({"公司名": "x", "状态": "成功", "法定代表人": "",
                           "注册资本": "", "地址": ""}) is True
assert bg.is_shell_record({"公司名": "x", "状态": "成功", "法定代表人": "李四",
                           "注册资本": "", "地址": ""}) is False
print("② 空壳判据：只有电话/只有状态的记录都能识别 ✓")


# ── 3. LoginGate 三种出路 ────────────────────────────
def make_gate(decisions, recheck_seq=None):
    """decisions：每次弹窗后要交回的选择；recheck_seq：重新验登录时的返回序列"""
    logs, asks, cleared = [], [], []
    seq = list(recheck_seq or [])
    gate = bg.LoginGate("企知道", logs.append, asks.append, lambda: False,
                        lambda: seq.pop(0) if seq else ("missing", "还是没有"),
                        emit_cleared=cleared.append)

    def emit(i):
        # 弹窗时立刻把用户的选择交回去（模拟界面同步应答，测试不用等）
        if i < len(decisions) and decisions[i]:
            gate.resolve(decisions[i])

    return gate, logs, asks, cleared, emit


# 3a 仍然继续采集 → 放行并且界面提示要收起
gate, logs, asks, cleared, emit = make_gate(["continue"])
_bg_real_wait = gate._wait
gate._wait = lambda ev, mw: (emit(0), True)[1]
assert gate.ensure("未登录") is True and asks == ["未登录"] and cleared, "选择继续后要通知界面收起提示"

# 3b 停止 → 不放行
gate, logs, asks, cleared, emit = make_gate(["stop"])
gate._wait = lambda ev, mw: (emit(0), True)[1]
assert gate.ensure("未登录") is False and cleared == []

# 3c 去登录 → 点"继续采集"后重新验一次，这次 Cookie 有了就放行
gate, logs, asks, cleared, emit = make_gate(["login_done"], [("ok", "已检测到登录凭证 accessToken")])
gate._wait = lambda ev, mw: (emit(0), True)[1]
assert gate.ensure("未登录") is True and cleared, "登录好了要自动继续"

# 3d 反复没登录：提醒有上限，不会无限弹窗
gate, logs, asks, cleared, emit = make_gate(["login_done"] * 5)
rounds = {"n": 0}


def wait_round(ev, mw):
    emit(rounds["n"])
    rounds["n"] += 1
    return True


gate._wait = wait_round
assert gate.ensure("未登录", rounds=3) is False and len(asks) == 3, f"提醒上限 3 次：{len(asks)}"

# 3e 等待期间用户点停止 → 立刻退出（不靠 _wait 打桩，走真实等待）
gate, logs, asks, cleared, emit = make_gate([None])
gate._is_stopped = lambda: True
assert gate.ensure("未登录") is False and asks == [], "已经请求停止时不该再弹窗"
print("③ 登录闸门：继续/停止/登录好/上限/停止打断 ✓")


# ── 4/5. 企知道采集循环里的实际行为 ──────────────────
class FakeSignal:
    def __init__(self, name, sink):
        self.name, self.sink = name, sink

    def emit(self, *a):
        self.sink(self.name, *a)


class FakeSignals:
    def __init__(self, sink):
        self.sink = sink

    def __getattr__(self, name):
        return FakeSignal(name, self.sink)


def make_qzd(decisions, login_seq=None, by_name=None,
             default_state=("missing", "浏览器里没有 accessToken 登录凭证，当前是未登录状态")):
    """构造一个不碰浏览器的企知道爬虫：搜索直接按公司名返回预置记录"""
    c = Q.QiZhiDaoCrawler()
    c.delay_between = 0
    seen, asked = [], []
    logins = list(login_seq or [])
    c.check_login = (lambda: logins.pop(0) if logins else default_state)
    c._detect_block = lambda: ""
    c._is_session_alive = lambda: True

    def search(name):
        seen.append(name)
        rec = (by_name or {}).get(name, SHELL_QZD)
        rec = rec(name) if callable(rec) else rec
        return dict(rec, 公司名=name)

    c.search_company = search

    def sink(sig_name, *a):
        if sig_name == "login_required_signal":
            asked.append(a[0])
            if decisions:
                choice = decisions.pop(0)
                if choice:
                    c.resolve_login_gate(choice)

    c.signals = FakeSignals(sink)
    return c, seen, asked


SHELL = dict(SHELL_QZD, 数据来源="企知道")
FULL = dict(FULL_QZD, 数据来源="企知道")

# 4a 未登录 + 用户选停止：一家都不该被搜索，也不该写任何记录
c, seen, asked = make_qzd(["stop"])
c.crawl(["甲公司", "乙公司", "丙公司"], 0)
assert asked and "accessToken" in asked[0], f"应弹窗提醒未登录：{asked}"
assert seen == [], f"未登录时一家都不能去采：{seen}"
assert c.results == [], f"不该写入任何记录：{c.results}"

# 4b 未登录 + 用户选"仍然继续"：正常往下采（用户自己要的后果）
c, seen, asked = make_qzd(["continue"], by_name={"甲公司": FULL, "乙公司": FULL})
c.crawl(["甲公司", "乙公司"], 0)
assert seen == ["甲公司", "乙公司"], f"选择继续后应照常采集：{seen}"
assert len(c.results) == 2, "继续采集后不该再把记录拦在门外"

# 4c 登录正常：不打扰用户，直接开采
c, seen, asked = make_qzd([], login_seq=[("ok", "已检测到登录凭证 accessToken")],
                          by_name={"甲公司": FULL})
c.crawl(["甲公司"], 0)
assert asked == [] and seen == ["甲公司"] and len(c.results) == 1, f"已登录不该弹窗：{asked}"

OK_STATE = ("ok", "已检测到登录凭证 accessToken")

# 5 登录凭证在、但连采两家空壳（登录失效/权限变化）：提醒一次、不写入
c, seen, asked = make_qzd(
    ["stop"], login_seq=[OK_STATE], default_state=OK_STATE,
    by_name={"甲公司": SHELL, "乙公司": SHELL, "丙公司": FULL})
c.crawl(["甲公司", "乙公司", "丙公司"], 0)
assert len(asked) == 1 and "连续 2 家" in asked[0], f"空壳到 2 家要提醒：{asked}"
assert "登录凭证仍在" in asked[0], f"Cookie 还在时要说清不是登录问题：{asked}"
assert seen == ["甲公司", "乙公司"], f"空壳不写入结果：{seen}"
assert c.results == [], f"空壳记录不能进结果：{c.results}"

# 5b 用户选"仍然继续"：空壳仍然不入库（垃圾数据不能污染客户库），名单继续往后走
c, seen, asked = make_qzd(
    ["continue"], login_seq=[OK_STATE], default_state=OK_STATE,
    by_name={"甲公司": SHELL, "乙公司": SHELL, "丙公司": FULL})
c.crawl(["甲公司", "乙公司", "丙公司"], 0)
assert len(asked) == 1, f"只该问一次，不能反复弹窗：{asked}"
assert seen == ["甲公司", "乙公司", "丙公司"], f"继续后应往下采：{seen}"
assert [r["公司名"] for r in c.results] == ["丙公司"], f"两家空壳被排除：{c.results}"

# 5c 登录补上后重新开跑：因为空壳没写入，续采从第 1 家补齐
c, seen, asked = make_qzd([], login_seq=[OK_STATE], default_state=OK_STATE,
                          by_name={"甲公司": FULL, "乙公司": FULL, "丙公司": FULL})
c.crawl(["甲公司", "乙公司", "丙公司"], 0)
assert asked == [] and len(c.results) == 3, f"登录后三家都要采到：{c.results}"
assert all(r["法定代表人"] for r in c.results), "登录后采到的都是完整记录"
print("④⑤ 采集循环：未登录不开采、空壳不入库、登录补上后续采补齐 ✓")


# ── 6. 界面弹窗 ──────────────────────────────────────
class FakeBox:
    """替掉 QMessageBox：记录按钮并按预设答案"点击"其中一个

    addButton 返回一个唯一的假按钮对象，这样界面里的 `clicked is b_cont`
    比对的是对象身份，不会因为字符串常量是否驻留而错乱。
    """
    answers = []
    Warning = Information = Question = 0
    AcceptRole = ActionRole = RejectRole = 0

    class Btn:
        def __init__(self, label):
            self.label = label

        def __str__(self):
            return self.label

    def __init__(self, parent=None):
        self.buttons = []
        self.clicked = None
        self.text = ""
        self.informative = ""

    def setWindowTitle(self, _t):
        pass

    def setIcon(self, _i):
        pass

    def setText(self, t):
        self.text = t

    def setInformativeText(self, t):
        self.informative = t

    def addButton(self, label, role):
        btn = FakeBox.Btn(label)
        self.buttons.append(btn)
        return btn

    def setDefaultButton(self, _b):
        pass

    def exec_(self):
        answer = FakeBox.answers.pop(0) if FakeBox.answers else "去浏览器登录（推荐）"
        for btn in self.buttons:
            if btn.label == answer:
                self.clicked = btn
        return 0

    def clickedButton(self):
        return self.clicked


w = MainWindow()
w.stop_btn.setEnabled(True)          # 伪装成"正在采集中"
w.pause_btn.setEnabled(True)
w.resume_btn.setEnabled(False)
G.QMessageBox = FakeBox              # 只影响界面测试，不动真实弹窗实现

# 6a 选"去浏览器登录"：红条出现、按钮切成"等你登录"、闸门不被回应
FakeBox.answers = ["去浏览器登录（推荐）"]
w.qizhidao_crawler.signals.login_required_signal.emit("浏览器里没有 accessToken 登录凭证，当前是未登录状态")
app.processEvents()
assert [i[0] for i in w._login_info] == ["企知道"], f"应记下等待登录的平台：{w._login_info}"
assert not w.blocked_lbl.isHidden() and "未登录" in w.blocked_lbl.text(), w.blocked_lbl.text()
assert "accessToken" in w.blocked_lbl.text(), "红条要带上具体缺哪个凭证"
assert w.resume_btn.isEnabled(), "等待登录期间要让用户能点“继续采集”"
assert w.pause_btn.isEnabled() is False, "等待登录期间暂停按钮该禁用"

# 6b 登录好后点"继续采集"：闸门收到 login_done，恢复信号收起提示
resolved = []
w.qizhidao_crawler.resolve_login_gate = lambda choice: resolved.append(choice)
w._resume_crawl()
assert "login_done" in resolved, f"继续采集要回答闸门：{resolved}"
w.qizhidao_crawler.signals.block_cleared_signal.emit("x")
app.processEvents()
assert w._login_info == [] and w.blocked_lbl.isHidden(), "恢复后提示要收起"

# 6c 选“仍然继续采集”：把选择交回闸门，界面提示由恢复信号收掉
FakeBox.answers = ["仍然继续采集"]
resolved = []
w.aiqicha_crawler.resolve_login_gate = lambda choice: resolved.append(choice)
w.aiqicha_crawler.signals.login_required_signal.emit("浏览器里没有 BDUSS 登录凭证，当前是未登录状态")
app.processEvents()
assert resolved == ["continue"], f"要放行采集线程：{resolved}"
assert w._login_info and w._login_info[0][0] == "爱企查", "未登录提示应显示在界面上"
w.aiqicha_crawler.signals.block_cleared_signal.emit("x")
app.processEvents()
assert w._login_info == [] and w.blocked_lbl.isHidden(), "放行后提示要收起"

# 6d 选“停止采集”：直接结束本轮并清理提示
FakeBox.answers = ["停止采集"]
resolved = []
w.qizhidao_crawler.resolve_login_gate = lambda choice: resolved.append(choice)
w.qizhidao_crawler.signals.login_required_signal.emit("未登录")
app.processEvents()
assert "stop" in resolved, f"停止要交回闸门：{resolved}"
assert w._login_info == [] and w.blocked_lbl.isHidden(), "停止后红条不能残留"
print("⑥ 界面弹窗：三个按钮各自的后续状态 ✓")

print("LOGIN_GATE_OFFLINE_OK")
