# -*- coding: utf-8 -*-
"""test_export_scope_offline.py —— 搜索采购商「导出 Excel」范围选择离线回归。

契约（用户需求 + 桌面软件惯例）：
  · 勾选结果同时含国内外 → 先弹窗问「导出全部 / 仅国内(N 条) / 仅国外(M 条) / 取消」
  · 只含一类（纯国内或纯国外）→ 不打扰，不弹窗，直接进保存位置选择
  · 取消 → 不做任何导出；未勾选 → 提示后阻断
  · 默认文件名、保存对话框标题、成功弹窗、审计日志都带范围口径（全部/国内/国外）
全程 offscreen，不联网、不写真实文件、不碰真实审计日志。
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_passed = 0
_failed = 0


def _assert(condition, label):
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  ok   {label}")
    else:
        _failed += 1
        print(f"  FAIL {label}")


from PyQt5.QtWidgets import QApplication, QMessageBox, QFileDialog, QPushButton
import pool_dialogs
from pool_dialogs import SearchDialog
from data_pool import record_region

app = QApplication.instance() or QApplication(sys.argv)

# 属地是**判据的输出**，不是可以凭空声明的字段：裸 is_domestic 不带判据版本
# 标记时不参与判定（否则属地判据升级永远落不到存量数据上）。所以语料必须给出
# 可判定的字段证据，形态与真实采集记录一致。
DOM = {"company": "国内A有限公司", "address": "广东省深圳市南山区科技园",
       "is_domestic": True}
DOM2 = {"company": "国内B有限公司", "address": "江苏省南京市江宁区",
        "is_domestic": True}
INTL = {"company": "OverseasC GmbH",
        "address": "Musterstrasse 12, 10115 Berlin, Germany", "is_domestic": False}
LEGACY_INTL = {"company_name": "Samsung Electronics", "country": "South Korea"}


class FakeCheckBoxArea:
    """模拟表格第 0 列的勾选容器：只需要 findChild(QCheckBox) 能力。"""

    def __init__(self, checked):
        from PyQt5.QtWidgets import QCheckBox
        self._chk = QCheckBox()
        self._chk.setChecked(checked)

    def findChild(self, _cls):
        return self._chk


class FakeTable:
    def __init__(self, checks):
        self._cells = [FakeCheckBoxArea(c) for c in checks]

    def rowCount(self):
        return len(self._cells)

    def cellWidget(self, row, col):
        return self._cells[row]


class RealMsgBoxStub(QMessageBox):
    """替换 pool_dialogs.QMessageBox：捕获 information/critical 调用，
    同时保留 addButton/clickedButton/exec_ 真实能力供 _ask_export_scope 使用。"""

    infos = []
    criticals = []

    @staticmethod
    def information(parent, title, text, *a, **k):
        RealMsgBoxStub.infos.append((title, text))
        return QMessageBox.Ok


def bare_dialog():
    """不跑 SearchDialog.__init__（不触网、不建整套 UI），只要一个真 Qt 实例。"""
    from PyQt5.QtWidgets import QDialog
    dlg = SearchDialog.__new__(SearchDialog)
    QDialog.__init__(dlg)
    return dlg


def make_dialog(records, checks):
    """绕过 __init__（不触网、不建完整 UI），只装配 _on_export 依赖的部件。"""
    dlg = bare_dialog()
    dlg.table = FakeTable(checks)
    dlg._results = records
    dlg._exported = []
    dlg._scope_calls = []

    def fake_export(recs, path, tag=""):
        # 生产侧固定传三个参数（记录/路径/口径），这里全部留痕供断言
        dlg._exported.append((list(recs), path, tag))

    dlg._export_to_excel = fake_export

    def fake_ask(dom_n, intl_n):
        dlg._scope_calls.append((dom_n, intl_n))
        return fake_ask.result

    fake_ask.result = "all"
    dlg._ask_export_scope = fake_ask
    # 底部「已选 N 条」标签（计数口径用例需要）
    from PyQt5.QtWidgets import QLabel
    dlg.lbl_selected = QLabel("已选 0 条")
    return dlg


audit_logs = []


class FakeAudit:
    def log(self, action, user, detail):
        audit_logs.append((action, user, detail))


saved = {}


class FakeFileDialog:
    """替掉 QFileDialog.getSaveFileName：记录标题与默认文件名，返回临时路径。"""

    @staticmethod
    def getSaveFileName(parent, title, default_name, *a, **k):
        saved["title"] = title
        saved["name"] = default_name
        return os.path.join(tempfile.gettempdir(), default_name), ""


def reset_stubs():
    RealMsgBoxStub.infos.clear()
    RealMsgBoxStub.criticals.clear()
    saved.clear()
    audit_logs.clear()
    pool_dialogs.QMessageBox = RealMsgBoxStub
    pool_dialogs.QFileDialog = FakeFileDialog


pool_dialogs.get_audit_logger = lambda: FakeAudit()

print("A. _split_by_region 纯函数（与搜索过滤同口径）")
dom, intl = SearchDialog._split_by_region([DOM, DOM2, INTL])
_assert([r["company"] for r in dom] == ["国内A有限公司", "国内B有限公司"], "混合拆分：国内 2 条")
_assert([r["company"] for r in intl] == ["OverseasC GmbH"], "混合拆分：国外 1 条")
_assert(len(dom) + len(intl) == 3, "拆分不丢不重（总账守恒）")
dom, intl = SearchDialog._split_by_region([DOM])
_assert(intl == [], "纯国内：国外为空 → 上层不弹窗")
dom, intl = SearchDialog._split_by_region([INTL])
_assert(dom == [], "纯国外：国内为空 → 上层不弹窗")
dom, intl = SearchDialog._split_by_region([LEGACY_INTL])
_assert(dom == [] and len(intl) == 1, "存量无 is_domestic → 多信号推导归国外")
_assert(LEGACY_INTL.get("_region_key") == "intl", "record_region 就地缓存键已写入")

print("B. 未勾选 → 提示阻断，不导出")
reset_stubs()
dlg = make_dialog([DOM], [False])
dlg._on_export()
_assert(not dlg._exported, "未勾选：未调用导出")
_assert(any(t == "未选择" for t, _ in RealMsgBoxStub.infos), "未勾选：弹出「未选择」提示")

print("C. 纯国内勾选 → 不弹范围窗，直接保存，范围=国内")
reset_stubs()
dlg = make_dialog([DOM, DOM2], [True, True])
dlg._on_export()
_assert(len(dlg._exported) == 1 and len(dlg._exported[0][0]) == 2, "纯国内：直接导出 2 条")
_assert(dlg._exported[0][2] == "国内", "纯国内：传给写盘逻辑的口径=国内")
_assert(dlg._scope_calls == [], "纯国内：未调用范围弹窗（不打扰）")
_assert("国内" in saved["name"] and saved["name"].endswith(".xlsx"), "默认文件名含「国内」范围标识")
_assert("国内" in saved["title"] and "2 条" in saved["title"], "保存对话框标题带范围与条数")
_assert(audit_logs and audit_logs[-1][2].get("scope") == "dom", "审计日志 scope=dom")
_assert(any("已导出国内" in t for _, t in RealMsgBoxStub.infos), "成功弹窗文案「已导出国内 N 条」")

print("D. 纯国外勾选 → 不弹范围窗，范围=国外")
reset_stubs()
dlg = make_dialog([INTL], [True])
dlg._on_export()
_assert(dlg._scope_calls == [], "纯国外：未调用范围弹窗")
_assert("国外" in saved["name"], "默认文件名含「国外」范围标识")
_assert(audit_logs[-1][2].get("scope") == "intl", "审计日志 scope=intl")

print("E. 混合勾选 → 先弹范围窗；按选择过滤导出集")
dlg = make_dialog([DOM, DOM2, INTL], [True, True, True])
dlg._ask_export_scope.result = "dom"
reset_stubs()
expected_scope_call = [(2, 1)]
dlg._on_export()
_assert(dlg._scope_calls == expected_scope_call, "混合：弹窗一次且条数=（国内2, 国外1）")
_assert(len(dlg._exported[0][0]) == 2, "选「仅国内」：只导出国内 2 条")
_assert("国内" in saved["name"], "选「仅国内」：文件名带国内")
dlg2 = make_dialog([DOM, DOM2, INTL], [True, True, True])
dlg2._ask_export_scope.result = "intl"
reset_stubs()
dlg2._on_export()
_assert(len(dlg2._exported[0][0]) == 1, "选「仅国外」：只导出国外 1 条")
_assert(dlg2._exported[0][2] == "国外", "选「仅国外」：口径=国外")
_assert(audit_logs[-1][2].get("scope") == "intl", "选「仅国外」：审计 scope=intl")
dlg3 = make_dialog([DOM, DOM2, INTL], [True, True, True])
dlg3._ask_export_scope.result = "all"
reset_stubs()
dlg3._on_export()
_assert(len(dlg3._exported[0][0]) == 3, "选「导出全部」：3 条全导")
_assert(dlg3._exported[0][2] == "全部", "选「全部」：口径=全部")
_assert("全部" in saved["name"], "选「全部」：文件名带全部")
_assert(audit_logs[-1][2].get("scope") == "all", "选「全部」：审计 scope=all")

print("F. 范围窗点「取消」→ 不做任何导出")
dlg4 = make_dialog([DOM, DOM2, INTL], [True, True, True])
dlg4._ask_export_scope.result = None
reset_stubs()
dlg4._on_export()
_assert(not dlg4._exported, "取消：未调用导出")
_assert(RealMsgBoxStub.infos == [], "取消：不弹成功/提示窗")

print("G. _ask_export_scope 真实按钮映射（真 QMessageBox + 同步 click）")


def ask_with_click(btn_text):
    """不 mock 按钮：用真实 QMessageBox 建窗（addButton/Role 全走生产代码），
    exec_ 处同步点击目标按钮——clickedButton() 由 Qt 自己记录，
    验证的是真实的按钮→返回值映射，而不是测试自己写的字典。"""

    class AutoClick(QMessageBox):
        def exec_(self):
            for b in self.buttons():
                if b.text() == btn_text:
                    b.click()
                    break
            return self.result()

    orig = pool_dialogs.QMessageBox
    pool_dialogs.QMessageBox = AutoClick
    try:
        return SearchDialog._ask_export_scope(bare_dialog(), 2, 1)
    finally:
        pool_dialogs.QMessageBox = orig


_assert(ask_with_click("导出全部") == "all", "点「导出全部」→ all")
_assert(ask_with_click("仅国内（2 条）") == "dom", "点「仅国内（2 条）」→ dom")
_assert(ask_with_click("仅国外（1 条）") == "intl", "点「仅国外（1 条）」→ intl")
_assert(ask_with_click("取消") is None, "点「取消」→ None（不导出）")

print("G2. 关窗口/Esc（无任何按钮点击）→ 返回 None")


class _NoClick(QMessageBox):
    def exec_(self):
        return QMessageBox.Rejected


orig = pool_dialogs.QMessageBox
pool_dialogs.QMessageBox = _NoClick
_assert(SearchDialog._ask_export_scope(bare_dialog(), 2, 1) is None,
        "未点任何按钮 → None（取消）")
pool_dialogs.QMessageBox = orig

print("G3. 弹窗构成：标题/按钮/默认按钮/模态（交互一致性）")
built = {}


class ShapeProbe(QMessageBox):
    """不 mock 生产代码，只采集它真正设进去的参数。

    注：windowTitle() 在子窗体未上屏前 getter 恒为空（Qt 行为），
    所以标题以 setWindowTitle 调用参数为准。
    """

    def setWindowTitle(self, t):
        built["title"] = t
        return super().setWindowTitle(t)

    def setText(self, t):
        built["text"] = t
        return super().setText(t)

    def setIcon(self, i):
        built["icon"] = i
        return super().setIcon(i)

    def exec_(self):
        built["buttons"] = sorted(b.text() for b in self.buttons())
        db = self.defaultButton()
        built["default"] = db.text() if db else None
        built["modal"] = self.isModal()
        return QMessageBox.Rejected


orig = pool_dialogs.QMessageBox
pool_dialogs.QMessageBox = ShapeProbe
SearchDialog._ask_export_scope(bare_dialog(), 2, 1)
pool_dialogs.QMessageBox = orig
_assert(built["title"] == "选择导出范围", "弹窗标题固定为「选择导出范围」")
_assert(built["icon"] == QMessageBox.Question, "图标为 Question（提问而非告警）")
_assert(built["buttons"] == sorted(["导出全部", "仅国内（2 条）", "仅国外（1 条）", "取消"]),
        "四个按钮：全部/仅国内/仅国外/取消，带条数")
_assert(built["default"] == "导出全部", "默认按钮=导出全部（回车即确认）")
_assert(built["modal"], "范围窗为模态（防用户在等待时继续操作）")

print("H. 弹窗文案自查（所见即所得：条数与勾选内容一致）")
_assert("国内 2 条" in built["text"] and "国外 1 条" in built["text"],
        "弹窗正文含准确国内/国外条数")
_assert("3 条记录" in built["text"], "弹窗正文含勾选总数")

print("H2. 条数随勾选变化（不写死、不串数据）")
seen = {}


class TextSpy(ShapeProbe):
    def exec_(self):
        seen["text"] = self.text()
        return QMessageBox.Rejected


pool_dialogs.QMessageBox = TextSpy
SearchDialog._ask_export_scope(bare_dialog(), 7, 3)
pool_dialogs.QMessageBox = orig
_assert("国内 7 条、国外 3 条" in seen["text"] and "10 条记录" in seen["text"],
        "10 条（国内7/国外3）文案与传入条数严格一致")

print("I. 导出按钮纳入权限守卫豁免表之外——仍受 ACL 拦截（点击时先过守卫）")
# 说明：导出按钮文字「导出 Excel」不在豁免词表，无权限账号会被守卫拦截，
# 这是预期行为（功能权限 > 导出范围），此处仅固化契约防误改。
from access_guard import AccessGuard
guard = AccessGuard()
_assert(not any(k in "导出 Excel" for k in guard._EXEMPT_TEXTS),
         "「导出 Excel」不豁免：无权限账号无法导出（预期拦截）")

print("J. 底部「已选」计数与导出弹窗同一套数字（所见即所得）")
dlg = make_dialog([DOM, DOM2, INTL], [True, True, True])
dlg._update_selected_count()
_assert(dlg.lbl_selected.text() == "已选 3 条（国内 2 · 国外 1）",
        "混合勾选：标签亮出国内/国外拆分")
_assert(dlg._scope_calls == [], "只刷标签不会弹窗")
dlg = make_dialog([DOM, DOM2], [True, True])
dlg._update_selected_count()
_assert(dlg.lbl_selected.text() == "已选 2 条", "纯国内：不加后缀（直接进保存位置）")
dlg = make_dialog([DOM, DOM2, INTL], [True, False, False])
dlg._update_selected_count()
_assert(dlg.lbl_selected.text() == "已选 1 条", "只勾国内1 条：无拆分后缀")
dlg = make_dialog([DOM, DOM2, INTL], [False, False, False])
dlg._update_selected_count()
_assert(dlg.lbl_selected.text() == "已选 0 条", "未勾选：只显示总数、不拆分")

print("J2. 全选/取消全选后计数主动重算（不依赖逐行信号时序）")
from PyQt5.QtCore import Qt
dlg = make_dialog([DOM, DOM2, INTL], [False, False, False])
dlg._on_select_all(Qt.Checked)
_assert(dlg.lbl_selected.text() == "已选 3 条（国内 2 · 国外 1）",
        "全选后：标签同步为 3 条带拆分")
dlg.table._cells[2]._chk.setChecked(False)   # 手动去掉国外
assert dlg.table._cells[2]._chk.isChecked() is False
dlg._update_selected_count()
_assert(dlg.lbl_selected.text() == "已选 2 条", "去掉国外后：回到纯国内无后缀")
dlg._on_select_all(Qt.Unchecked)
_assert(dlg.lbl_selected.text() == "已选 0 条", "取消全选：计数归零")

print("K. 端到端：真写 Excel + 回读（验证过滤后的记录真进文件、列结构不变）")


def std_rec(name, country, dom, mobile=""):
    return {
        "company": name, "country": country, "is_domestic": dom,
        "contacts": [{"name": "张三", "title": "采购经理"}],
        "mobiles": [mobile] if mobile else [], "landlines": [], "phones": [],
        "emails": [f"{name[:3].lower()}@example.com"],
        "website": "", "products": "光伏组件", "industry": "新能源",
        "address": "", "source": "1688",
    }


REAL = [std_rec("国内甲公司与", "中国", True, "13800138000"),
        std_rec("国内乙厂", "中国", True),
        std_rec("Munich Solar GmbH", "Germany", False),
        std_rec("Seoul Battery Inc", "South Korea", False)]

from openpyxl import load_workbook


def run_real_export(scope_result, out_dir):
    """只假面范围弹窗与保存框，其余全走生产代码（真 _export_to_excel）。"""
    dlg = make_dialog(REAL, [True] * len(REAL))
    dlg._ask_export_scope.result = scope_result
    reset_stubs()

    def save(parent, title, default_name, *a, **k):
        saved["title"] = title
        saved["name"] = default_name
        return os.path.join(out_dir, default_name), ""

    pool_dialogs.QFileDialog = type("FD", (), {"getSaveFileName": staticmethod(save)})
    # 不拦 _export_to_excel：换回真实实现（真写盘），才能验证口径传递与列结构
    dlg._export_to_excel = SearchDialog._export_to_excel
    dlg._on_export()
    path = os.path.join(out_dir, saved["name"])
    wb = load_workbook(path)
    ws = wb.active
    rows = [[c.value for c in row] for row in ws.iter_rows(min_row=2)]
    return wb, ws, rows


with tempfile.TemporaryDirectory() as td:
    wb, ws, rows = run_real_export("dom", td)
    _assert(len(rows) == 2, "选「仅国内」：文件里只有 2 行国内记录")
    _assert(all(r[2] in ("国内甲公司与", "国内乙厂") for r in rows), "国内记录落盘正确")
    _assert(len(rows[0]) == 12, "列结构仍为 12 列标准 schema（客户库模板列映射不变）")
    _assert(ws.title == "搜索结果_国内", "工作表名标出口径：搜索结果_国内")
    _assert("国内" in wb.properties.title, "文档属性带口径")
    _assert(rows[0][3] == "13800138000", "手机号归「电话」列（不串到固话）")
    _assert("国内" in saved["name"], "默认文件名带国内口径")

    wb, ws, rows = run_real_export("intl", td)
    _assert(len(rows) == 2 and all(r[10] in ("Germany", "South Korea") for r in rows),
            "选「仅国外」：只有 2 行海外记录（国家列印证）")
    _assert(ws.title == "搜索结果_国外", "工作表名标出口径：搜索结果_国外")

    wb, ws, rows = run_real_export("all", td)
    _assert(len(rows) == 4, "选「导出全部」：4 行全量落盘")
    _assert(ws.title == "搜索结果_全部", "工作表名：搜索结果_全部")

print("\n" + "=" * 60)
total = _passed + _failed
if _failed == 0:
    print(f"全部通过 ✅ ({_passed}/{total})")
else:
    print(f"通过 {_passed}/{total}，失败 {_failed} ❌")
print("=" * 60)
sys.exit(1 if _failed else 0)
