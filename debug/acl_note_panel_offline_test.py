# -*- coding: utf-8 -*-
"""acl_note_panel_offline_test.py —— 权限管理面板的「备注」那一栏（2026-11-19 需求 1）。

用户原话：「期望可以修改备注，方便备注是谁的账号，添加账号时多一栏输入框，即填备注内容」。

为什么单独成套、为什么必须真建一个面板
--------------------------------------
备注这个功能的价值全部落在四条**行为**上：读回来看得见、格子里改得动、没改动时一个字
都不写、超长时当场拒绝而不是存个「前半句」。在源码里 grep 到「备注」两个字不能证明其中
任何一条；grep 只能证明那句话存在，而面板里最贵的 bug 恰好都是「话说了、事没做」。

后台一律替身：`AccessGuard` 的真实写回动的是**生产 OSS 名单**——「跑一遍测试」就会变成
「改一遍线上」。替身把每次写回调用逐条记账，因为「一次应用改动只递一个 apply_changes」
这种契约看返回值看不出来，只有看调用次数与参数才卡得住。

九组断言各自盯着一个具体事故
--------------------------
① 备注列存在且叫「备注」；② 设备状态从备注里拆出去（旧版把机器事实写进备注列，管理员
想标「这是张三的号」根本没有落点）；③ 只有备注格可编辑（表格级放开触发后不逐格关就会
长出「双击能改、改完什么也没存」的假入口）；④ 改备注只递一次写回；⑤ 等级+备注同屏改
仍是一次（两次提交=两个 version+一份别人看得见的半成品名单）；⑥ 没改动绝不写（点一下
就 +1 会让 version 从此不可解读）；⑦ 超长当场拒并说清多长；⑧ 清空备注与「没动过」不是
一件事；⑨ 就地编辑器里还没交回表格的那句话也要读到（打完字直接点按钮是最高频路径）；
⑩ 添加账号一屏三栏、手机号就地校验、默认最保守档、备注与新号一次写入。

运行：QT_QPA_PLATFORM=offscreen .venv/bin/python debug/acl_note_panel_offline_test.py
"""
import copy
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "b2bcollect_build"))

# 状态落点钉进沙箱：本套件真建权限管理面板、真走设备顶号那一段，而
# `.acl_device.json` 的开发态落点就在 b2bcollect_build/acl/ 里。2026-11-20 干净 clone
# 批跑实测：这一路凭空新建了那份文件（开发机上那是老文件，即使被改也看不出来），
# 被批跑的状态对拍门点名并压红总判定。改写设备指纹比写脏缓存严重：它会让下一次
# 真开工具自认新设备，用户看到的是「该账号已在另一台设备使用」。
_STATE_SANDBOX = Path(tempfile.mkdtemp(prefix="aclnote_state_"))
os.environ["B2B_STATE_DIR"] = str(_STATE_SANDBOX)

from PyQt5.QtCore import Qt                                        # noqa: E402
from PyQt5.QtWidgets import (                                      # noqa: E402
    QApplication, QDialog, QLineEdit,
)

import acl_admin_dialog as aad                                     # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])

ADMIN = "15088131371"       # 本机管理员号（避开「降级自己」那扇确认门要用的号）
NOTED = "19224997664"       # 名单里本来就带备注的号
BLANK = "13417047813"       # 旧名单里没有备注条目的号
NEW = "13700137000"         # 「添加账号」那一屏要加的号

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


class FakeAcl:
    """面板看到的那一份 AccessGuard：固定名单 + 写回调用记账。"""

    def __init__(self):
        self.calls = []
        self.levels = {ADMIN: 0, NOTED: 1, BLANK: 2}
        self.notes = {NOTED: "张三（市场部）"}
        self.doc = {"accounts": [ADMIN, NOTED, BLANK], "grants": dict(self.levels),
                    "account_notes": dict(self.notes), "schema": "acl-v3",
                    "updated_at": "2026-11-19T01:00:00", "version": 7}

    # ── 读 ──
    def load_document(self, client=None):
        return copy.deepcopy(self.doc), int(self.doc["version"])

    def list_devices(self, accounts, client=None):
        out = []
        for p in accounts:
            if p == ADMIN:
                out.append({"phone": p, "bound": True, "is_self": True,
                            "device_label": "本机这台机器", "device_id": "DEV_SELF",
                            "generation": 3, "bound_at": "2026-11-18T09:12:00",
                            "history": []})
            elif p == NOTED:
                out.append({"phone": p, "bound": True, "is_self": False,
                            "device_label": "办公室那台", "device_id": "DEV_OTHER",
                            "generation": 1, "bound_at": "2026-11-17T18:00:00",
                            "history": []})
            else:
                out.append({"phone": p, "bound": False})
        return out

    def describe_levels(self, doc=None, client=None):
        return dict(self.levels), True

    def describe_account_notes(self, doc=None, client=None):
        # 与真后端同一个口径：备注是**存储字段**，旧名单没这条就是空，不是报错
        return dict((doc or self.doc).get("account_notes") or {})

    # ── 写（只记账，绝不碰网络）──
    def apply_changes(self, level_entries=None, note_entries=None, client=None):
        self.calls.append(("apply_changes", list(level_entries or []),
                           list(note_entries or [])))
        return {"version": 8, "levels": {}, "notes": {},
                "grants": dict(self.levels), "account_notes": dict(self.notes)}

    def add_accounts(self, entries, account_notes=None, client=None):
        self.calls.append(("add_accounts", list(entries), dict(account_notes or {})))
        return {"version": 8, "added": list(entries), "grants": dict(self.levels),
                "account_notes": dict(account_notes or {})}

    def set_levels(self, entries, client=None):
        self.calls.append(("set_levels", list(entries)))
        return {"version": 8, "changed": {}}

    def set_account_notes(self, entries, client=None):
        self.calls.append(("set_account_notes", list(entries)))
        return {"version": 8, "changed": {}}


fake = FakeAcl()


def _sync_run(self, label, fn, on_done, on_failed=None):
    """把面板的后台任务改成同步跑。

    offscreen 里等 QThread 就是等一件不能确定何时结束的事，而本套件要验的是数据流
    （读回 → 填表 → diff → 一次写回），不是线程调度。线程本身由面板的 closeEvent
    与 ``_AclTask`` 负责，那里另有判据。
    """
    on_done(fn())


def _row_of(table, phone):
    for r in range(table.rowCount()):
        if table.item(r, aad.COL_PHONE).text() == phone:
            return r
    raise AssertionError(f"名单里没找到 {phone}：面板把行弄丢了")


def build_panel():
    """按「管理员打开面板」那条路真建一个 AclAdminDialog。"""
    dlg = aad.AclAdminDialog()
    dlg._app_ref = app                   # 只是给 GC 留个引用，别让面板先被回收
    table = dlg.table
    return dlg, table


def main():
    print("═══ 权限面板 · 账号备注 离线测试 ═══")
    saved = {
        "AccessGuard": aad.AccessGuard,
        "run": aad.AclAdminDialog._run,
        "visible": aad._ed.acl_admin_visible,
        "get_guard": aad._ag.get_guard,
    }
    aad.AccessGuard = fake
    aad.AclAdminDialog._run = _sync_run
    # 面板每个动作都会再问一次能力位（开着面板期间可能被别人降级）。这里顶掉的是
    # 「问」这一步，判定矩阵本身不动——本套件测的是备注的数据流，不是等级矩阵。
    aad._ed.acl_admin_visible = lambda: True
    aad._ag.get_guard = lambda: type("_G", (), {"account": lambda s: ADMIN})()
    try:
        dlg, table = build_panel()
        _test_readonly_shape(dlg, table)
        _test_diff(dlg, table)
        _test_overlong(dlg, table)
        _test_clear(dlg, table)
        _test_inline_editor(dlg, table)
        _test_add_dialog()
        table.parent()                  # 别让 widgets 在断言中途被回收
    finally:
        aad.AccessGuard = saved["AccessGuard"]
        aad.AclAdminDialog._run = saved["run"]
        aad._ed.acl_admin_visible = saved["visible"]
        aad._ag.get_guard = saved["get_guard"]
        # 沙箱用完即回收：状态对拍门骂过「/var 下没人认领的目录」，自己造的临时目录要自己
        # 收干净。放在 finally 里——判据红了也一样收，别让一次失败顺手留下永久残留。
        shutil.rmtree(_STATE_SANDBOX, ignore_errors=True)
        check("状态沙箱用完回收（不在 /var 下留没人认领的目录）",
              not _STATE_SANDBOX.exists(), str(_STATE_SANDBOX))
    print(f"\n═══ 结果：{_PASS} 通过 / {_FAIL} 失败 ═══")
    return 0 if _FAIL == 0 else 1


def _test_readonly_shape(dlg, table):
    print("\n── ① 列形态：备注是人的标注，设备状态是机器事实 ──")
    check("表格有备注列且表头就叫「备注」",
          aad.COL_NOTE < table.columnCount()
          and table.horizontalHeaderItem(aad.COL_NOTE).text() == "备注")
    check("设备状态单独成列（旧版把「本机/其他设备在用」写进备注，那一栏既不是给人写的"
          "又不能改，管理员想标「这是张三的号」根本没有落点）",
          aad.COL_STATUS != aad.COL_NOTE
          and table.horizontalHeaderItem(aad.COL_STATUS).text() == "设备状态")
    r_noted = _row_of(table, NOTED)
    r_blank = _row_of(table, BLANK)
    check("备注按后端那一份读口径填到行里（面板不自己解析名单 JSON）",
          table.item(r_noted, aad.COL_NOTE).text() == "张三（市场部）",
          table.item(r_noted, aad.COL_NOTE).text())
    check("旧名单里没这条备注的号是空格，不是报错也不是 None",
          table.item(r_blank, aad.COL_NOTE).text() == "",
          repr(table.item(r_blank, aad.COL_NOTE).text()))
    flags = lambda r, c: table.item(r, c).flags()
    check("只有备注格可编辑（Qt 的条目默认就带 ItemIsEditable，表格级放开触发后"
          "不逐格关掉就会长出「双击能改、改完什么也没存」的假入口）",
          bool(flags(r_noted, aad.COL_NOTE) & Qt.ItemIsEditable)
          and not bool(flags(r_noted, aad.COL_PHONE) & Qt.ItemIsEditable)
          and not bool(flags(r_noted, aad.COL_DEVICE) & Qt.ItemIsEditable)
          and not bool(flags(r_noted, aad.COL_STATUS) & Qt.ItemIsEditable)
          and not bool(flags(r_noted, aad.COL_GEN) & Qt.ItemIsEditable)
          and not bool(flags(r_noted, aad.COL_BOUND_AT) & Qt.ItemIsEditable))
    check("设备状态那一列给的是机器事实（本机就是本机，别人在用就写别人）",
          table.item(_row_of(table, ADMIN), aad.COL_STATUS).text() == "本机"
          and table.item(_row_of(table, NOTED), aad.COL_STATUS).text() == "其他设备在用",
          (table.item(_row_of(table, ADMIN), aad.COL_STATUS).text(),
           table.item(_row_of(table, NOTED), aad.COL_STATUS).text()))
    check("备注列表头写了怎么用（双击可改、改完要点应用；且不影响权限判定）",
          "双击" in (table.horizontalHeaderItem(aad.COL_NOTE).toolTip() or ""),
          table.horizontalHeaderItem(aad.COL_NOTE).toolTip())
    check("按钮名字是「应用改动」而不是「应用等级改动」（它现在管两路改动）",
          dlg.btn_apply.text() == "应用改动", dlg.btn_apply.text())


def _test_diff(dlg, table):
    print("\n── ② diff：改了什么就写什么，没改一个字也不写 ──")
    r_blank = _row_of(table, BLANK)
    table.item(r_blank, aad.COL_NOTE).setText("李四（供应链）")
    lv, nt, pb = dlg._collect_changes()
    check("改备注被 diff 接住，且不会顺手递一个等级改动",
          lv == [] and nt == [f"{BLANK}=李四（供应链）"], str((lv, nt, pb)))
    fake.calls.clear()
    dlg._on_apply_changes()
    check("写回走 apply_changes 一次提交（拆成 set_levels+set_account_notes 就是两个 "
          "version + 一份别人看得见的半成品名单）",
          [c[0] for c in fake.calls] == ["apply_changes"], str(fake.calls))
    check("那一次提交里备注参数带的是新内容",
          fake.calls and fake.calls[0][2] == [f"{BLANK}=李四（供应链）"], str(fake.calls))

    # 等级与备注同屏改：仍必须只有一次
    table.item(r_blank, aad.COL_NOTE).setText("李四（市外）")
    combo = table.cellWidget(r_blank, aad.COL_LEVEL)
    combo.setCurrentIndex(combo.findData(1))
    fake.calls.clear()
    dlg._on_apply_changes()
    check("等级+备注同屏改动也只递一次写回（一次编辑=一个 version 才算说得清「谁改的」）",
          len(fake.calls) == 1 and fake.calls[0][1] == [f"{BLANK}=1"]
          and fake.calls[0][2] == [f"{BLANK}=李四（市外）"], str(fake.calls))

    # 无改动：重读之后面板与「最近读回的样子」一致，一个字都不许写
    fake.calls.clear()
    dlg._on_apply_changes()
    check("没有改动就不写名单（点一下就 +1 会让 version 从此不可解读）",
          fake.calls == [] and "没有改动" in dlg.status_lbl.text()
          and "version 不变" in dlg.status_lbl.text(), dlg.status_lbl.text())


def _test_overlong(dlg, table):
    print("\n── ③ 超长备注：当场拒绝，而不是存个「前半句」 ──")
    r_blank = _row_of(table, BLANK)
    limit = aad._ag.ACCOUNT_NOTE_MAX
    warned = []
    real = aad.QMessageBox.warning
    aad.QMessageBox.warning = staticmethod(lambda *a, **k: warned.append(a))
    try:
        table.item(r_blank, aad.COL_NOTE).setText("啊" * (limit + 20))
        lv, nt, pb = dlg._collect_changes()
        check(f"超过 {limit} 字的备注被列进 problems（后端会截着读，但那是读侧的事）",
              len(pb) == 1 and nt == [], str((lv, nt, pb)))
        fake.calls.clear()
        dlg._on_apply_changes()
        check("超长时一条改动都没递到后端（部分提交在权限后台最难查）",
              fake.calls == [], str(fake.calls))
        check("拒绝的那句话能行动：上限、当前字数、得删几个字都在第首行",
              warned and "备注最长" in warned[0][2]
              and f"现在是 {limit + 20} 个" in warned[0][2]
              and f"请删掉 {limit + 20 - limit} 个字" in warned[0][2]
              and BLANK in warned[0][2], str(warned)[:200])
        # 上限边界本身：正好这么长必须能写，否则正常用法被挡在门外
        table.item(r_blank, aad.COL_NOTE).setText("啊" * limit)
        lv, nt, pb = dlg._collect_changes()
        check(f"正好 {limit} 字可以写（把边界误判成超长等于挡掉正常用法）",
              pb == [] and nt == [f"{BLANK}={'啊' * limit}"], str((lv, nt, pb)))
    finally:
        aad.QMessageBox.warning = real


def _test_clear(dlg, table):
    print("\n── ④ 清空备注与「没动过备注」是两件事 ──")
    r_blank = _row_of(table, BLANK)
    dlg._rows[BLANK]["note"] = "李四（供应链）"        # 假装刚才那条已落盘
    table.item(r_blank, aad.COL_NOTE).setText("")
    lv, nt, pb = dlg._collect_changes()
    check("清空会递上（写成 手机号= 就是删这条，不是「没改」）",
          nt == [f"{BLANK}="], str(nt))
    check("复述里看得出这条是删（只说「已更新」的话，管理员分不清删了还是没动）",
          "备注已清空" in dlg._change_summary([], nt), dlg._change_summary([], nt))
    nt2 = [f"{NOTED}=王五（新来的）"]
    check("复述把内容原样写出来（备注容易看错，复述一遍才能当场发现不是自己要的那句）",
          "王五（新来的）" in dlg._change_summary([], nt2), dlg._change_summary([], nt2))
    lv2 = [f"{BLANK}=1"]
    check("等级改动也在同一句复述里（两路改动汇报不能只报一路）",
          "等级" in dlg._change_summary(lv2, []), dlg._change_summary(lv2, []))


def _test_inline_editor(dlg, table):
    print("\n── ⑤ 就地编辑器里那句还没交回表格的话也要读到 ──")
    r_blank = _row_of(table, BLANK)
    item = table.item(r_blank, aad.COL_NOTE)
    table.setCurrentItem(item)
    table.edit(table.model().index(r_blank, aad.COL_NOTE))
    app.processEvents()
    editor = table.focusWidget()
    if not isinstance(editor, QLineEdit):
        # offscreen 平台在某些机器上不给就地编辑器焦点。这时这条路径确实没法真跑，
        # 但绝不能把判据悄悄改成「不用验」——红在这里，人就一定会去看。
        check("就地编辑器可开（开不了就是这条判据没跑起来，不许当它通过）", False,
              repr(editor))
        return
    editor.setText("王五（刚入职）")
    check("读得到编辑器里那句还没回写的话（打完字直接点「应用改动」是最高频路径，"
          "读到旧值就会报「没有改动」——用户明明写了字却被告诉没写）",
          dlg._note_text(r_blank) == "王五（刚入职）"
          and item.text() == "",
          (dlg._note_text(r_blank), item.text()))
    lv, nt, pb = dlg._collect_changes()
    check("编辑器里那句话能直接进 diff（不是先要求用户按一次回车）",
          nt == [f"{BLANK}=王五（刚入职）"], str(nt))
    table.closeEditor(editor, False)


def _test_add_dialog():
    print("\n── ⑥ 添加账号那一屏：手机号 / 等级 / 备注三栏 ──")
    dlg = aad._AddAccountDialog()
    accepted = []
    real_accept = QDialog.accept
    QDialog.accept = lambda self: accepted.append(self.phone())
    try:
        dlg.edit_phone.setText("123")
        dlg._on_accept()
        check("手机号填错时不提交，原因就地写在框里（不用等一次网络往返才知道）",
              accepted == [] and "11 位" in dlg.err_lbl.text(), dlg.err_lbl.text())
        dlg.edit_phone.setText(NEW)
        dlg.edit_note.setText("  新人试用  ")
        dlg._on_accept()
        check("格式对了就能提交（上一行的拦截没把整个按钮弄坏）",
              accepted == [NEW], str(accepted))
    finally:
        QDialog.accept = real_accept
    check("备注栏把首尾空白收掉（存「  新人试用  」下次读回来会变成「新人试用」，"
          "同一次编辑两种样子就等于界面在说假话）",
          dlg.note() == "新人试用", repr(dlg.note()))
    check("等级默认选的是最保守那一档（提权永远是显式动作，看错下拉框的代价不该是多人掌权）",
          dlg.level() == aad._ed.UNKNOWN_ACCOUNT_LEVEL, dlg.level())
    check("三栏真的都在这一屏里（用户要的就是「添加账号时多一栏输入框」）",
          dlg.edit_note is not None and dlg.edit_phone is not None
          and dlg.combo_level is not None)

    # 面板侧：备注必须跟着新号一次写入
    fake.calls.clear()
    panel, table = build_panel()
    real_exec = aad.QDialog.exec_
    aad.QDialog.exec_ = lambda self: QDialog.Accepted
    try:
        # _on_add 里会自己 new 一个对话框，这里顶掉构造，让它拿到已经填好的这一份
        filled = aad._AddAccountDialog()
        filled.edit_phone.setText(NEW)
        filled.edit_note.setText("新人试用")
        real_ctor = aad._AddAccountDialog
        aad._AddAccountDialog = lambda parent=None: filled
        try:
            panel._on_add()
        finally:
            aad._AddAccountDialog = real_ctor
    finally:
        aad.QDialog.exec_ = real_exec
    check("加号与备注同一次写入（拆两次就留下「有号无主」那份中间态，"
          "第二次失败时界面只能说「号加上了但备注没存」）",
          fake.calls == [("add_accounts", [f"{NEW}={aad._ed.UNKNOWN_ACCOUNT_LEVEL}"],
                          {NEW: "新人试用"})], str(fake.calls))
    fake.calls.clear()
    filled2 = aad._AddAccountDialog()
    filled2.edit_phone.setText(NEW)
    real_ctor2 = aad._AddAccountDialog
    aad._AddAccountDialog = lambda parent=None: filled2
    real_exec2 = aad.QDialog.exec_
    aad.QDialog.exec_ = lambda self: QDialog.Accepted
    try:
        panel._on_add()
    finally:
        aad.QDialog.exec_ = real_exec2
        aad._AddAccountDialog = real_ctor2
    check("备注留空时根本不传那一路（后端没有任何路径替管理员猜一句备注，"
          "猜错了就是一条比真实情况更可信的假记号）",
          fake.calls == [("add_accounts", [f"{NEW}={aad._ed.UNKNOWN_ACCOUNT_LEVEL}"], {})],
          str(fake.calls))


if __name__ == "__main__":
    rc = main()
    sys.stdout.flush()
    sys.stderr.flush()
    # 判定与退出码同一个来源：本套件建过 QApplication，Qt 在解释器收尾时的析构会
    # 随机段错误（本机实测：全绿之后 rc=139，被批跑读成一次失败、单跑又绿）。
    os._exit(rc)
