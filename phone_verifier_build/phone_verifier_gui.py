# -*- coding: utf-8 -*-
"""
号码智能核验工具 —— PyQt5 图形界面
====================================

功能（免费 / 离线 / 合规层，第①层做满）：
  上传含「姓名 / 公司名 / 手机号」的 Excel → 自动识别列 → 逐号核验分级 →
  颜色标记 → 汇总统计 → 保留原值导出（绝不删号）。

设计约束（沿用本项目历史踩坑）：
  - 核验在 QThread 中执行，槽函数绝不同步阻塞主线程（防「未响应」）。
  - 跨线程只传纯数据（log 传 str，进度传 int）。
  - 读值 data_only=True；导出保留原始全部列，另加核验列，不删任何行。
  - 字体 Microsoft YaHei 为主并配跨平台回退（Mac 无该字体不报警告）。
  - 号码有效性/运营商/归属地为参考信息，只标记不删除。
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QFont
from PyQt5.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QMainWindow, QMessageBox, QProgressBar, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

import openpyxl
import phonenumbers
from openpyxl.styles import Font as XLFont, PatternFill

import phone_verifier as V

# ---------------------------------------------------------------------------
# 跨平台字体
# ---------------------------------------------------------------------------
def _ui_font() -> QFont:
    f = QFont()
    if sys.platform == "win32":
        f.setFamily("Microsoft YaHei")
    elif sys.platform == "darwin":
        f.setFamily("PingFang SC")
    else:
        f.setFamily("Noto Sans CJK SC")
    return f


# 分级 -> 颜色
_TIER_COLOR = {
    V.TIER_VALID: QColor(200, 245, 200),    # 绿
    V.TIER_SUSPECT: QColor(255, 243, 200),  # 黄
    V.TIER_INVALID: QColor(250, 215, 215),  # 红
}
_TIER_FILL = {
    V.TIER_VALID: PatternFill("solid", fgColor="C8F5C8"),
    V.TIER_SUSPECT: PatternFill("solid", fgColor="FFF3C8"),
    V.TIER_INVALID: PatternFill("solid", fgColor="FAD7D7"),
}

# ---------------------------------------------------------------------------
# 智能列识别（表头语义 + 内容抽样打分）
# ---------------------------------------------------------------------------
_PHONE_HEADERS = ("手机", "电话", "号码", "联系方式", "联系电话", "手机号", "tel", "mobile", "phone")
_NAME_HEADERS = ("姓名", "名字", "联系人", "客户姓名", "称呼", "name")
_COMPANY_HEADERS = ("公司", "企业", "单位", "厂商", "公司名", "企业名称", "company")

_COMPANY_TAIL_RE = re.compile(r"(公司|集团|有限|厂|企业|中心|商行|工作室|合作社|事务所)$")
_CN_NAME_RE = re.compile(r"^[\u4e00-\u9fa5]{2,4}$")


def _score_header(text: str, aliases) -> int:
    t = (text or "").strip().lower()
    if not t:
        return 0
    for a in aliases:
        if a.lower() in t:
            return 100
    return 0


def _content_scores(values) -> dict:
    """对一列内容抽样，返回各字段的命中比例。"""
    sample = [V.cell_to_text(v) for v in values if V.cell_to_text(v)]
    if not sample:
        return {"phone": 0.0, "name": 0.0, "company": 0.0}
    sample = sample[:200]
    n = len(sample)
    phone_hits = name_hits = comp_hits = 0
    for s in sample:
        digits = re.sub(r"\D", "", s)
        if len(digits) >= 7 and re.search(r"\d", s):
            # 含足够数字且能解析出号码，视为电话候选
            try:
                phonenumbers.parse(s.split("；")[0].split(";")[0], "CN")
                phone_hits += 1
            except Exception:
                pass
        if _COMPANY_TAIL_RE.search(s) or ("公司" in s) or ("厂" in s):
            comp_hits += 1
        elif _CN_NAME_RE.match(s):
            name_hits += 1
    return {"phone": phone_hits / n, "name": name_hits / n, "company": comp_hits / n}


def detect_columns(headers, columns_values):
    """综合表头 + 内容打分，返回 (phone_idx, name_idx, company_idx)，-1 表示未识别。

    headers: [str, ...] 表头文本
    columns_values: [[...], [...]] 每列的数据值（不含表头）
    """
    n_cols = len(headers)
    scores = [{"phone": 0.0, "name": 0.0, "company": 0.0} for _ in range(n_cols)]
    for i in range(n_cols):
        h = headers[i] if i < len(headers) else ""
        vals = columns_values[i] if i < len(columns_values) else []
        cs = _content_scores(vals)
        scores[i] = {
            "phone": _score_header(h, _PHONE_HEADERS) * 0.6 + cs["phone"] * 100 * 0.4,
            "name": _score_header(h, _NAME_HEADERS) * 0.6 + cs["name"] * 100 * 0.4,
            "company": _score_header(h, _COMPANY_HEADERS) * 0.6 + cs["company"] * 100 * 0.4,
        }

    def pick(field_key, threshold):
        best_idx, best_score = -1, 0.0
        for i in range(n_cols):
            sc = scores[i][field_key]
            if sc > best_score:
                best_idx, best_score = i, sc
        return (best_idx, best_score) if best_score >= threshold else (-1, best_score)

    phone_idx, _ = pick("phone", 40)
    company_idx, _ = pick("company", 40)
    name_idx, _ = pick("name", 40)

    # 去冲突：同一列被多个字段选中时，保留分最高的
    chosen = {}
    for field_key, idx in (("phone", phone_idx), ("name", name_idx), ("company", company_idx)):
        if idx < 0:
            continue
        sc = scores[idx][field_key]
        if idx in chosen and chosen[idx][1] >= sc:
            continue
        chosen[idx] = (field_key, sc)
    phone_idx = name_idx = company_idx = -1
    for idx, (field_key, _sc) in chosen.items():
        if field_key == "phone":
            phone_idx = idx
        elif field_key == "name":
            name_idx = idx
        else:
            company_idx = idx
    return phone_idx, name_idx, company_idx


# ---------------------------------------------------------------------------
# Excel 读取（非只读，防静默截断）
# ---------------------------------------------------------------------------
def read_sheet(path):
    """读取首个有数据的工作表。返回 (headers, rows, sheet_title)。

    rows: [[cell, ...], ...] 原始单元格值（未转字符串），保留原类型。
    """
    wb = openpyxl.load_workbook(path, data_only=True, read_only=False)
    ws = None
    for cand in wb.worksheets:
        if cand.max_row and cand.max_row >= 1 and cand.max_column:
            ws = cand
            break
    if ws is None:
        ws = wb.active
    all_rows = list(ws.iter_rows(values_only=True))
    wb.close()
    if not all_rows:
        return [], [], ws.title

    # 第一非空行当表头
    header_row_idx = 0
    for i, r in enumerate(all_rows):
        if any(V.cell_to_text(c) for c in r):
            header_row_idx = i
            break
    headers = [V.cell_to_text(c) for c in all_rows[header_row_idx]]
    rows = [list(r) for r in all_rows[header_row_idx + 1:]
            if any(V.cell_to_text(c) for c in r)]
    return headers, rows, ws.title


def export_report(src_path, out_path, headers, rows, results,
                  phone_idx, name_idx, company_idx):
    """导出核验报告：保留原始全部列 + 追加核验列，绝不删行。"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "核验结果"

    new_cols = ["核验分级", "类型", "规范手机号", "规范固话", "归属地", "运营商", "核验说明"]
    ws.append(list(headers) + new_cols)
    for c in range(1, len(headers) + len(new_cols) + 1):
        ws.cell(row=1, column=c).font = XLFont(bold=True)

    for r_i, (row, cr) in enumerate(zip(rows, results), start=2):
        base = [V.cell_to_text(c) for c in row]
        # 补齐长度
        while len(base) < len(headers):
            base.append("")
        regions = "、".join(sorted({n.region for n in cr.numbers
                                    if n.tier == V.TIER_VALID and n.region}))
        carriers = "、".join(sorted({n.carrier for n in cr.numbers
                                     if n.tier == V.TIER_VALID and n.carrier}))
        extra = [
            cr.best_tier,
            "、".join(sorted({n.kind for n in cr.numbers if n.tier == V.TIER_VALID and n.kind})),
            cr.normalized_mobile,
            cr.normalized_fixed,
            regions,
            carriers,
            cr.summary,
        ]
        ws.append(base + extra)
        fill = _TIER_FILL.get(cr.best_tier)
        if fill:
            tier_col = len(headers) + 1
            ws.cell(row=r_i, column=tier_col).fill = fill

    # 冻结表头 + 自适应列宽（粗略）
    ws.freeze_panes = "A2"
    for c in range(1, len(headers) + len(new_cols) + 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(c)].width = 16
    wb.save(out_path)
    wb.close()


# ---------------------------------------------------------------------------
# 后台核验线程
# ---------------------------------------------------------------------------
class VerifyWorker(QThread):
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int, int)      # done, total
    finished_signal = pyqtSignal(list)          # List[CellResult]

    def __init__(self, values, region="CN", parent=None):
        super().__init__(parent)
        self._values = values
        self._region = region
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        results = []
        total = len(self._values)
        try:
            for i, v in enumerate(self._values):
                if self._stop:
                    break
                results.append(V.verify_cell(v, self._region))
                if i % 50 == 0 or i == total - 1:
                    self.progress_signal.emit(i + 1, total)
        except Exception as e:  # noqa
            self.log_signal.emit(f"[错误] 核验中断：{str(e).splitlines()[0]}")
        self.progress_signal.emit(total, total)
        self.finished_signal.emit(results)


# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("号码智能核验工具（免费离线版）")
        self.setFont(_ui_font())
        self.resize(1180, 720)

        self.src_path = ""
        self.headers = []
        self.rows = []
        self.results = []
        self.phone_idx = -1
        self.name_idx = -1
        self.company_idx = -1
        self.worker = None

        self._build_ui()

    # ------------------------------ UI ------------------------------
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # 1) 文件
        file_box = QGroupBox("① 选择 Excel 文件（含姓名 / 公司名 / 手机号）")
        fb = QHBoxLayout(file_box)
        self.path_label = QLabel("未选择文件")
        self.path_label.setStyleSheet("color:#666;")
        btn_open = QPushButton("上传 Excel…")
        btn_open.clicked.connect(self._on_open)
        fb.addWidget(btn_open)
        fb.addWidget(self.path_label, 1)
        root.addWidget(file_box)

        # 2) 列映射
        col_box = QGroupBox("② 列映射（自动识别，可手动调整）")
        cb = QHBoxLayout(col_box)
        cb.addWidget(QLabel("手机号列:"))
        self.combo_phone = QComboBox()
        self.combo_phone.setMinimumWidth(160)
        cb.addWidget(self.combo_phone)
        cb.addWidget(QLabel("姓名列:"))
        self.combo_name = QComboBox()
        self.combo_name.setMinimumWidth(140)
        cb.addWidget(self.combo_name)
        cb.addWidget(QLabel("公司名列:"))
        self.combo_company = QComboBox()
        self.combo_company.setMinimumWidth(160)
        cb.addWidget(self.combo_company)
        cb.addStretch(1)
        root.addWidget(col_box)

        # 3) 操作
        act = QHBoxLayout()
        self.btn_verify = QPushButton("▶ 开始核验")
        self.btn_verify.clicked.connect(self._on_verify)
        self.btn_verify.setEnabled(False)
        self.btn_export = QPushButton("导出核验报告 (.xlsx)")
        self.btn_export.clicked.connect(self._on_export)
        self.btn_export.setEnabled(False)
        act.addWidget(self.btn_verify)
        act.addWidget(self.btn_export)
        act.addStretch(1)
        self.stat_label = QLabel("就绪。核验完全离线、无请求限制、不删除任何号码。")
        self.stat_label.setStyleSheet("color:#444;")
        act.addWidget(self.stat_label)
        root.addLayout(act)

        self.progress = QProgressBar()
        self.progress.setValue(0)
        root.addWidget(self.progress)

        # 4) 预览表
        self.table = QTableWidget(0, 0)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self.table, 1)

        # 5) 日志
        self.log_label = QLabel("")
        self.log_label.setStyleSheet("color:#888;")
        self.log_label.setWordWrap(True)
        root.addWidget(self.log_label)

        for combo in (self.combo_phone, self.combo_name, self.combo_company):
            combo.currentIndexChanged.connect(self._on_col_changed)

    # ------------------------------ 日志 ------------------------------
    def _append_log(self, msg: str):
        self.log_label.setText(msg)

    # ------------------------------ 打开文件 ------------------------------
    def _on_open(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 Excel 文件", "", "Excel 文件 (*.xlsx *.xlsm);;所有文件 (*)")
        if not path:
            return
        if path.lower().endswith(".xls"):
            QMessageBox.warning(self, "格式不支持", "请将旧版 .xls 另存为 .xlsx 后再导入。")
            return
        try:
            headers, rows, title = read_sheet(path)
        except Exception as e:  # noqa
            QMessageBox.critical(self, "读取失败", f"无法读取文件：\n{str(e).splitlines()[0]}")
            return
        if not headers or not rows:
            QMessageBox.warning(self, "空文件", "未读到有效数据行，请检查文件内容。")
            return

        self.src_path = path
        self.headers = headers
        self.rows = rows
        self.path_label.setText(f"{os.path.basename(path)}　（工作表: {title}，{len(rows)} 行 × {len(headers)} 列）")
        self.path_label.setStyleSheet("color:#000;")

        # 智能列识别
        cols_values = []
        for i in range(len(headers)):
            cols_values.append([r[i] if i < len(r) else None for r in rows])
        p_idx, n_idx, c_idx = detect_columns(headers, cols_values)
        self._fill_combos(headers, p_idx, n_idx, c_idx)
        self._append_log(
            f"已加载 {len(rows)} 行。自动识别："
            f"手机号列=「{headers[p_idx] if p_idx>=0 else '未识别'}」，"
            f"姓名列=「{headers[n_idx] if n_idx>=0 else '未识别'}」，"
            f"公司名列=「{headers[c_idx] if c_idx>=0 else '未识别'}」，可手动调整。")
        self.btn_verify.setEnabled(True)
        self.btn_export.setEnabled(False)
        self._render_preview(None)

    def _fill_combos(self, headers, p_idx, n_idx, c_idx):
        for combo in (self.combo_phone, self.combo_name, self.combo_company):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("（未识别）", -1)
            for i, h in enumerate(headers):
                combo.addItem(f"{h or '(空列名)'}  [第{i+1}列]", i)
            combo.blockSignals(False)
        self.combo_phone.setCurrentIndex(max(0, p_idx + 1))
        self.combo_name.setCurrentIndex(max(0, n_idx + 1))
        self.combo_company.setCurrentIndex(max(0, c_idx + 1))

    def _on_col_changed(self):
        if not self.headers:
            return
        self.phone_idx = self.combo_phone.currentData()
        self.name_idx = self.combo_name.currentData()
        self.company_idx = self.combo_company.currentData()

    # ------------------------------ 核验 ------------------------------
    def _on_verify(self):
        self._on_col_changed()
        if self.phone_idx is None or self.phone_idx < 0:
            QMessageBox.warning(self, "未指定手机号列", "请在②中选择包含手机号的列后再核验。")
            return
        values = [r[self.phone_idx] if self.phone_idx < len(r) else None for r in self.rows]
        self.btn_verify.setEnabled(False)
        self.btn_export.setEnabled(False)
        self.progress.setValue(0)
        self._append_log(f"开始核验 {len(values)} 个号码（离线，无网络请求）…")

        self.worker = VerifyWorker(values, "CN")
        self.worker.log_signal.connect(self._append_log)
        self.worker.progress_signal.connect(self._on_progress)
        self.worker.finished_signal.connect(self._on_verify_done)
        self.worker.start()

    def _on_progress(self, done, total):
        if total:
            self.progress.setValue(int(done * 100 / total))
        self.stat_label.setText(f"核验中… {done}/{total}")

    def _on_verify_done(self, results):
        self.results = results
        valid = sum(1 for r in results if r.best_tier == V.TIER_VALID)
        susp = sum(1 for r in results if r.best_tier == V.TIER_SUSPECT)
        inv = sum(1 for r in results if r.best_tier == V.TIER_INVALID)
        mobile = sum(1 for r in results if r.is_mobile)
        self.stat_label.setText(
            f"完成：共 {len(results)} 行　✅有效 {valid}　⚠️存疑 {susp}　❌无效 {inv}　"
            f"（含有效手机号 {mobile}）　—— 所有号码均已保留，未删除任何行。")
        self._append_log(
            f"核验完成。有效 {valid} / 存疑 {susp} / 无效 {inv}；有效手机号 {mobile} 个。"
            f"存疑=形似号码但号段未在规划库（可能新号段），建议人工复核，未删除。")
        self.btn_verify.setEnabled(True)
        self.btn_export.setEnabled(True)
        self._render_preview(results)

    # ------------------------------ 预览 ------------------------------
    def _render_preview(self, results):
        if not self.headers:
            return
        show_cols = []
        if self.name_idx is not None and self.name_idx >= 0:
            show_cols.append(("姓名", self.name_idx))
        if self.company_idx is not None and self.company_idx >= 0:
            show_cols.append(("公司名", self.company_idx))
        if self.phone_idx is not None and self.phone_idx >= 0:
            show_cols.append(("手机号(原始)", self.phone_idx))
        if not show_cols:
            show_cols = [(h or f"列{i+1}", i) for i, h in enumerate(self.headers)]

        extra = ["分级", "类型", "规范号码", "归属地", "运营商", "说明"] if results else []
        headers = [t for t, _ in show_cols] + extra
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(self.rows))

        limit = len(self.rows)
        for r in range(limit):
            row = self.rows[r]
            c = 0
            for _t, idx in show_cols:
                val = V.cell_to_text(row[idx]) if idx < len(row) else ""
                self.table.setItem(r, c, QTableWidgetItem(val))
                c += 1
            if results and r < len(results):
                cr = results[r]
                cells = [
                    cr.best_tier,
                    "、".join(sorted({n.kind for n in cr.numbers if n.tier == V.TIER_VALID and n.kind})),
                    cr.normalized_mobile or cr.normalized_fixed,
                    "、".join(sorted({n.region for n in cr.numbers if n.tier == V.TIER_VALID and n.region})),
                    "、".join(sorted({n.carrier for n in cr.numbers if n.tier == V.TIER_VALID and n.carrier})),
                    cr.summary,
                ]
                brush = QBrush(_TIER_COLOR.get(cr.best_tier, QColor(255, 255, 255)))
                for k, txt in enumerate(cells):
                    item = QTableWidgetItem(txt)
                    if k == 0:
                        item.setBackground(brush)
                    self.table.setItem(r, c + k, item)
        self.table.resizeColumnsToContents()
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)

    # ------------------------------ 导出 ------------------------------
    def _on_export(self):
        if not self.results:
            return
        base = os.path.splitext(os.path.basename(self.src_path))[0] if self.src_path else "号码核验"
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_dir = os.path.join(os.path.expanduser("~"), "Desktop")
        default_path = os.path.join(default_dir, f"{base}_核验结果_{ts}.xlsx")
        path, _ = QFileDialog.getSaveFileName(self, "导出核验报告", default_path,
                                              "Excel 文件 (*.xlsx)")
        if not path:
            return
        try:
            export_report(self.src_path, path, self.headers, self.rows, self.results,
                          self.phone_idx, self.name_idx, self.company_idx)
        except PermissionError:
            QMessageBox.critical(self, "导出失败", "文件被占用，请先关闭同名 Excel 后重试。")
            return
        except Exception as e:  # noqa
            QMessageBox.critical(self, "导出失败", str(e).splitlines()[0])
            return
        self._append_log(f"已导出：{path}")
        QMessageBox.information(self, "导出成功", f"核验报告已保存到：\n{path}")


def main():
    app = QApplication(sys.argv)
    app.setFont(_ui_font())
    w = MainWindow()
    w.show()
    # ── 有序收尾：窗口 → QApplication（与 b2bcollect_gui.main 同一条纪律）─────────
    # app 比窗口**先**定义，解释器拆帧按定义顺序清局部变量 → QApplication 先析构、窗口
    # wrapper 后析构，PyQt 拆后者时撞上的是一具已经随 app 没掉的 C++ 对象：退出 SIGSEGV
    # （b2bcollect 线上实测抓到过同一条栈，用户侧表现是「关掉工具弹一次系统崩溃报告」）。
    # 所以趁 app 还活着先把窗口收掉。本工具的 MainWindow **没有** closeEvent（没有要落盘的
    # 东西），这两句纯粹是把 wrapper 抢在 app 之前交出去；仍写 isVisible() 是为以后万一加了
    # closeEvent，也不会把同一段收尾跑两遍（用户自己关窗口那一次已经跑过了）。
    rc = app.exec_()
    try:
        # 后台线程还在跑就先请它停、再有界地等一会儿：直接拆窗口时 Qt 会报
        # "QThread: Destroyed while thread is still running"，运气不好连着一起崩
        # （riskbird 与 ali1688 的 closeEvent 里早就有同一段有界等待，这两个工具一直没有人管）。
        _wk = getattr(w, "worker", None)
        if _wk is not None and _wk.isRunning():
            _wk.stop()
            _wk.wait(5000)
    except Exception:               # noqa: BLE001 - 收尾失败不该改退出码，也不该再弹框
        pass
    try:
        if w.isVisible():
            w.close()
    except Exception:               # noqa: BLE001 - 同上
        pass
    w = None
    sys.exit(rc)


if __name__ == "__main__":
    main()
