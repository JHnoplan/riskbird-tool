"""断点续采的真 GUI 回路离线验证（offscreen 起窗口，但不启动浏览器、不联网）。

覆盖 test_checkpoint_offline.py 覆盖不到的“接线”部分：
1. 采集进度信号 → 台账逐条落状态并每采满 5 条写盘（崩溃前采的都在盘上）；
2. 载入名单后自动发现未完成会话并弹窗，选「从断点继续」→ 回读原数据 Excel、
   按数据来源灌回各平台 results、待采 = 名单 − 已完成 − 无结果；
3. 弹窗选「取消」时不改会话目录、不动进度文件；
4. 状态行按大厂口径报「本次待采 N 家（名单/已完成/失败待重试/无结果跳过）」。
"""
import json
import os
import shutil
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication  # noqa: E402

app = QApplication([])

import block_guard                          # noqa: E402  (幂等键口径比对)
import crawl_checkpoint as CP               # noqa: E402
import task_registry as _tasks              # noqa: E402
import riskbird_gui as G                    # noqa: E402
from openpyxl import Workbook               # noqa: E402

ROOT = Path(tempfile.mkdtemp(prefix="resume_gui_"))
SNAMES = ["A公司", "B公司", "C公司", "D公司", "E公司", "F公司"]
DESK = ROOT / "desktop"
DESK.mkdir(parents=True, exist_ok=True)
SESS = DESK / "企业数据自动采集工具" / "2026-01-05_09-30-00"
SESS.mkdir(parents=True, exist_ok=True)


def rec(name, status="存续", source="企知道"):
    return {"公司名": name, "状态": status, "数据来源": source, "电话": "13800001111"}


def new_window():
    """真 MainWindow（会话目录与任务索引都指到临时桌面，不碰用户真实桌面）"""
    w = G.MainWindow()
    w._get_desktop_dir = lambda: DESK
    _tasks.set_desktop_dir(str(DESK))
    w._companies = list(SNAMES)
    w._companies_snapshot = []
    w._excel_paths = []
    return w


# ── 1. 采集进度信号驱动的台账：每条落状态，满 5 条自动写盘 ──
w = new_window()
cp = w._ensure_checkpoint(SESS)
assert cp is not None and Path(cp.path).parent == SESS, cp
w.qizhidao_crawler.results.extend([rec(n) for n in ("A公司", "B公司", "D公司", "E公司")])
w.qizhidao_crawler.results.append(rec("C公司", status="未找到"))
w._update_progress(5, len(SNAMES))          # 爬虫每采完一家都会发这个信号
with open(cp.path, encoding="utf-8") as fh:
    on_disk = json.load(fh)
items = on_disk["items"]
assert block_guard.norm_company("A公司") == "A公司", "幂等键就是 norm_company，不能另算一套"
assert items["A公司"]["status"] == CP.STATUS_DONE, items["A公司"]
assert items["C公司"]["status"] == CP.STATUS_NO_RESULT, items["C公司"]
assert items["F公司"]["status"] == CP.STATUS_PENDING, items["F公司"]
assert w.company_count_lbl.text().startswith("本次待采 1 家"), w.company_count_lbl.text
print("1 ✅ 进度信号→台账落盘:", w.company_count_lbl.text())

# ── 2. 自动保存写出的原数据 Excel（续采就靠它回读） ──
raw = SESS / _tasks.RAW_FILE_NAME
wb = Workbook()
ws = wb.active
ws.append(["序号", "公司名", "状态", "数据来源", "电话"])
for r in w.qizhidao_crawler.results:
    ws.append([1, r["公司名"], r["状态"], r["数据来源"], r["电话"]])
wb.save(str(raw))
wb.close()

# ── 3. 重启后载入同一份名单：自动发现断点 + 选「从断点继续」的全回路 ──
clicked = []
REAL_BOX = G.QMessageBox


class FakeBox:
    """只替掉模态弹窗：按钮对象与默认按钮逻辑保持原样，好验证点默认键走了哪条分支"""

    Question = 0
    ActionRole = 0
    AcceptRole = 1
    RejectRole = 2

    def __init__(self, parent=None):
        self.text = ""
        self._buttons = {}
        self._default = None

    def setWindowTitle(self, _t):
        pass

    def setIcon(self, _i):
        pass

    def setText(self, t):
        self.text = t

    def addButton(self, label, _role):
        btn = type("Btn", (), {"label": label})()
        self._buttons[label] = btn
        return btn

    def setDefaultButton(self, btn):
        self._default = btn

    def exec_(self):
        clicked.append(self._default.label)
        return 0

    def clickedButton(self):
        return self._default


G.QMessageBox = FakeBox
try:
    boot = new_window()                       # 相当于重启进程：内存里没有任何结果
    boot._maybe_offer_resume()
finally:
    G.QMessageBox = REAL_BOX
assert clicked == ["从断点继续"], f"默认按钮必须是「从断点继续」：{clicked}"
assert boot._session_folder == SESS and boot._task_id == SESS.name, boot._session_folder
assert len(boot.qizhidao_crawler.results) == 5, boot.qizhidao_crawler.results
assert boot._todo_companies() == ["F公司"], boot._todo_companies()
assert boot._cp_cursor["qizhidao"] == 5, boot._cp_cursor
assert "C公司" not in boot._todo_companies(), "确认无结果的公司不该再请求"
print("2 ✅ 续采回路：待采", boot._todo_companies(), "/", boot._checkpoint.text_summary())

# ── 4. 弹窗选「取消」：会话目录与进度文件都不该被动过 ──
kept_file = SESS / CP.CHECKPOINT_FILE_NAME
before = kept_file.read_text(encoding="utf-8")


class CancelBox(FakeBox):
    def setDefaultButton(self, btn):
        self._default = self.addButton("取消", 2)     # 用户点取消

    def exec_(self):
        clicked.append("取消")
        return 0


G.QMessageBox = CancelBox
try:
    no = new_window()
    no._maybe_offer_resume()
finally:
    G.QMessageBox = REAL_BOX
assert no._session_folder is None and no._checkpoint is None, "取消后不该接会话"
assert kept_file.read_text(encoding="utf-8") == before, "取消后进度文件必须原样保留"
print("3 ✅ 取消分支不写不乱")

# ── 5. 幂等：再点一次「继续」不会把同一家公司重复灌进 results ──
G.QMessageBox = FakeBox
try:
    again = new_window()
    again._maybe_offer_resume()
    # 用户反复点「继续」（或弹窗后又跑了一轮）不能把同一家公司灌两遍
    again._adopt_checkpoint(again._checkpoint, SESS)
finally:
    G.QMessageBox = REAL_BOX
assert len(again.qizhidao_crawler.results) == 5, again.qizhidao_crawler.results
assert again._todo_companies() == ["F公司"], again._todo_companies()
print("4 ✅ 重复续采不重复灌数据")

shutil.rmtree(str(ROOT), ignore_errors=True)
print("\nRESUME_GUI_OFFLINE_OK")
