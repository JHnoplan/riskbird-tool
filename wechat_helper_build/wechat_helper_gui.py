# -*- coding: utf-8 -*-
"""
微信/钉钉加好友半自动助手 —— PyQt5 图形界面（Mac 本地测试版）
============================================================

操作流（与用户约定一致）：
  打开工具 → 检测手机连接 → 选平台（微信/钉钉）→ 上传 Excel（姓名/公司/手机号）→
  编辑话术模板 → 用户在手机上手动打开到「添加好友-搜索」页 → 点开始 →
  工具自动：粘贴手机号→搜索→判断真实用户→点添加→把话术填进输入框→
  【停下并响铃+界面高亮】提示操作员在手机上手动点「发送」→ 工具自动识别
  屏幕「已发送/等待验证」字样后续条（也可空格确认/Esc 跳过）→ 循环。

红线（硬编码于 wechat_flow，本界面只是呈现）：
  - 绝不自动点「发送」，最后一步永远由人完成。
  - 启动前必须勾选「号码来源合法」门禁。
  - 触发风控即熔断暂停；每日/每小时处理上限可调，超限自动停。

依赖：PyQt5、openpyxl、Pillow、numpy、pytesseract、phonenumbers
     （复用号码核验内核做预筛，OCR 定位复用 locator）。
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
from datetime import datetime
from typing import Dict, List

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QBrush, QKeySequence
from PyQt5.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar, QPushButton,
    QShortcut, QSpinBox, QTableWidget, QTableWidgetItem, QTextEdit,
    QVBoxLayout, QWidget,
)

# phone_verifier 位于同项目上一级的 phone_verifier_build/，其余模块与本文件同目录；
# 把上一级加入搜索路径，使 Mac 直跑与 PyInstaller 打包均可解析。
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "phone_verifier_build"))

import phone_verifier as PV          # 复用：离线号码核验（先筛掉明显无效号）
import template_engine as T          # 话术渲染
import wechat_flow as WF             # 状态机（微信/钉钉双平台）
import locator as LOC                # OCR 屏幕证据识别（已发送自动续条）
from adb_controller import ADBController

try:
    import openpyxl
except Exception:  # noqa
    openpyxl = None


def _ui_font() -> QFont:
    f = QFont()
    f.setFamily("Microsoft YaHei" if sys.platform == "win32"
                else ("PingFang SC" if sys.platform == "darwin" else "Noto Sans CJK SC"))
    return f


_STATUS_COLOR = {
    WF.ST_REAL_WAIT: QColor(200, 245, 200),
    WF.ST_ALREADY: QColor(210, 225, 250),
    WF.ST_NOT_FOUND: QColor(245, 235, 210),
    WF.ST_RISK: QColor(250, 200, 200),
    WF.ST_ERROR: QColor(250, 210, 210),
    WF.ST_SKIPPED: QColor(235, 235, 235),
    WF.ST_PREFILTER: QColor(225, 225, 225),
}


# ---------------------------------------------------------------------------
# 电脑端提示音（跨平台，失败静默不崩）
# ---------------------------------------------------------------------------
def beep_alert():
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["afplay", "/System/Library/Sounds/Glass.aiff"])
        elif sys.platform == "win32":
            import winsound
            winsound.Beep(1000, 500)
        else:
            QApplication.beep()
    except Exception:  # noqa
        try:
            QApplication.beep()
        except Exception:  # noqa
            pass


# ---------------------------------------------------------------------------
# 后台工作线程
# ---------------------------------------------------------------------------
class Worker(QThread):
    log_sig = pyqtSignal(str)
    progress_sig = pyqtSignal(int, int)
    finished_sig = pyqtSignal(list)
    prompt_sig = pyqtSignal(dict)          # 到"该点发送"时通知主线程高亮+响铃

    def __init__(self, adb, items, greeting_fn, caps, delay_range,
                 flow_cls=None, auto_detect=True, parent=None):
        super().__init__(parent)
        self.adb = adb
        self.items = items
        self.greeting_fn = greeting_fn
        self._q = queue.Queue()
        self._pause = threading.Event()
        self._stop = threading.Event()
        self._auto_detect = bool(auto_detect)
        cls = flow_cls or WF.WeChatFlow
        self._flow = cls(
            adb, logger=self.log_sig.emit,
            wait_human=self._wait_human, sleeper=self._sleep,
            should_stop=self._stop.is_set,
            daily_cap=caps[0], hourly_cap=caps[1], delay_range=delay_range)

    # 点完发送后屏幕上会出现的证据词（微信/钉钉通用，真机校准后可增补）
    SENT_EVIDENCE = ("已发送", "等待验证", "等待对方验证", "发送成功",
                     "好友请求已发送", "已发送好友申请", "已发出")

    # 供 flow 调用：阻塞直到操作员确认或自动识别到已发送
    def _wait_human(self, ctx: Dict) -> str:
        self.prompt_sig.emit(ctx)
        import time as _t
        last_poll = 0.0
        while not self._stop.is_set():
            # 自动续条：操作员在手机上点完发送后，屏幕出现证据词即自动进入下一条，
            # 省去回 GUI 点「我已发送」；识别不到时仍可靠按钮/空格确认。
            if self._auto_detect and _t.time() - last_poll >= 1.2:
                last_poll = _t.time()
                try:
                    txt = LOC.ocr_text(self.adb.screencap_png())
                except Exception:  # noqa
                    txt = ""
                if any(k in txt for k in self.SENT_EVIDENCE):
                    self.log_sig.emit("检测到屏幕出现『已发送/等待验证』，自动进入下一条。")
                    return "sent"
            try:
                return self._q.get(timeout=0.2)
            except queue.Empty:
                continue
        return "skipped"

    def _sleep(self, seconds: float):
        # 可被停止打断的 sleep
        import time
        end = time.time() + seconds
        while time.time() < end and not self._stop.is_set():
            time.sleep(min(0.2, max(0.0, end - time.time())))

    def confirm_sent(self):
        self._q.put("sent")

    def confirm_skip(self):
        self._q.put("skipped")

    def pause(self):
        self._pause.set()

    def resume(self):
        self._pause.clear()

    def stop(self):
        self._stop.set()
        self._pause.clear()

    def run(self):
        def _on_item(i, total, r):
            self.progress_sig.emit(i, total)
        try:
            results = self._flow.run_batch(self.items, self.greeting_fn, on_item=_on_item)
        except Exception as e:  # noqa
            self.log_sig.emit(f"[异常] {str(e).splitlines()[0]}")
            results = []
        self.finished_sig.emit(results)


# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("微信/钉钉加好友半自动助手（人工确认发送）")
        self.setFont(_ui_font())
        self.resize(1180, 800)

        self.adb = ADBController()
        self.serial = ""
        self.headers: List[str] = []
        self.rows: List[list] = []
        self.items: List[Dict] = []
        self.worker = None
        self.results: List[WF.FlowResult] = []
        self._prefiltered: List[Dict] = []
        self._blink_on = False
        self._awaiting = False
        self._preflight_ok = False

        self._build_ui()

    # --------------------------- UI ---------------------------
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # 合规门禁
        gate_box = QGroupBox("合规前提")
        gb = QHBoxLayout(gate_box)
        self.chk_gate = QCheckBox(
            "我确认：名单为展会/名片等【已授权留资】号码，仅用于本人知情下的商务联系，"
            "且我了解自动化搜索仍可能触发微信风控导致该微信号被限制。")
        self.chk_gate.setStyleSheet("color:#a00;")
        gb.addWidget(self.chk_gate)
        root.addWidget(gate_box)

        # 设备
        dev_box = QGroupBox("① 连接手机（USB 调试已开、已授权本机）")
        db = QHBoxLayout(dev_box)
        self.btn_dev = QPushButton("检测手机")
        self.btn_dev.clicked.connect(self._on_detect)
        self.dev_label = QLabel("未检测")
        self.dev_label.setStyleSheet("color:#666;")
        db.addWidget(self.btn_dev)
        db.addWidget(self.dev_label, 1)
        db.addWidget(QLabel("平台:"))
        self.combo_platform = QComboBox()
        self.combo_platform.addItems(["微信", "钉钉"])
        self.combo_platform.currentIndexChanged.connect(self._on_detect)
        db.addWidget(self.combo_platform)
        root.addWidget(dev_box)

        # Excel
        x_box = QGroupBox("② 上传 Excel（含 姓名 / 公司名 / 手机号）")
        xb = QHBoxLayout(x_box)
        self.btn_x = QPushButton("上传 Excel…")
        self.btn_x.clicked.connect(self._on_open)
        self.x_label = QLabel("未选择")
        self.x_label.setStyleSheet("color:#666;")
        xb.addWidget(self.btn_x)
        xb.addWidget(self.x_label, 1)
        xb.addWidget(QLabel("手机号列:"))
        self.combo_phone = QComboBox(); self.combo_phone.setMinimumWidth(140)
        xb.addWidget(self.combo_phone)
        root.addWidget(x_box)

        # 模板编辑器
        t_box = QGroupBox("③ 话术模板（{列名} 为占位符，按每行数据自动替换）")
        tb = QVBoxLayout(t_box)
        self.tpl_edit = QTextEdit()
        self.tpl_edit.setFixedHeight(72)
        self.tpl_edit.setPlainText(
            "{姓名}先生你好，我是鸿威集团的{昵称}，我了解到您对{兴趣点}感兴趣。")
        self.tpl_edit.textChanged.connect(self._refresh_preview)
        tb.addWidget(self.tpl_edit)
        var_row = QHBoxLayout()
        var_row.addWidget(QLabel("插入变量:"))
        self.var_combo = QComboBox(); self.var_combo.setMinimumWidth(140)
        var_row.addWidget(self.var_combo)
        btn_ins = QPushButton("插入")
        btn_ins.clicked.connect(self._insert_var)
        var_row.addWidget(btn_ins)
        var_row.addWidget(QLabel("固定值 昵称:"))
        self.edit_nick = QLineEdit(); self.edit_nick.setPlaceholderText("如：小李")
        self.edit_nick.textChanged.connect(self._refresh_preview)
        var_row.addWidget(self.edit_nick)
        var_row.addWidget(QLabel("兴趣点:"))
        self.edit_interest = QLineEdit(); self.edit_interest.setPlaceholderText("如：制冷设备")
        self.edit_interest.textChanged.connect(self._refresh_preview)
        var_row.addWidget(self.edit_interest)
        var_row.addStretch(1)
        tb.addLayout(var_row)
        self.preview_label = QLabel("预览：—")
        self.preview_label.setWordWrap(True)
        self.preview_label.setStyleSheet("color:#06c;")
        tb.addWidget(self.preview_label)
        root.addWidget(t_box)

        # 参数
        p_box = QGroupBox("④ 节流与风控护栏")
        pb = QHBoxLayout(p_box)
        pb.addWidget(QLabel("随机间隔(秒) 起:"))
        self.spin_lo = QSpinBox(); self.spin_lo.setRange(1, 120); self.spin_lo.setValue(6)
        pb.addWidget(self.spin_lo)
        pb.addWidget(QLabel("止:"))
        self.spin_hi = QSpinBox(); self.spin_hi.setRange(1, 300); self.spin_hi.setValue(10)
        pb.addWidget(self.spin_hi)
        pb.addWidget(QLabel("每日上限:"))
        self.spin_day = QSpinBox(); self.spin_day.setRange(1, 2000); self.spin_day.setValue(80)
        pb.addWidget(self.spin_day)
        pb.addWidget(QLabel("每小时上限:"))
        self.spin_hour = QSpinBox(); self.spin_hour.setRange(1, 500); self.spin_hour.setValue(25)
        pb.addWidget(self.spin_hour)
        self.chk_auto = QCheckBox("发送后自动识别并继续（推荐）")
        self.chk_auto.setChecked(True)
        pb.addWidget(self.chk_auto)
        pb.addStretch(1)
        root.addWidget(p_box)

        # 控制
        act = QHBoxLayout()
        self.btn_start = QPushButton("▶ 开始执行")
        self.btn_start.clicked.connect(self._on_start)
        self.btn_start.setEnabled(False)
        self.btn_pause = QPushButton("暂停"); self.btn_pause.clicked.connect(self._on_pause)
        self.btn_pause.setEnabled(False)
        self.btn_stop = QPushButton("停止"); self.btn_stop.clicked.connect(self._on_stop)
        self.btn_stop.setEnabled(False)
        self.btn_export = QPushButton("导出台账"); self.btn_export.clicked.connect(self._on_export)
        self.btn_export.setEnabled(False)
        for b in (self.btn_start, self.btn_pause, self.btn_stop, self.btn_export):
            act.addWidget(b)
        act.addStretch(1)
        self.stat_label = QLabel("就绪")
        act.addWidget(self.stat_label)
        root.addLayout(act)

        self.progress = QProgressBar(); root.addWidget(self.progress)

        # 人工确认提示条（醒目）
        self.prompt_bar = QLabel("　")
        self.prompt_bar.setAlignment(Qt.AlignCenter)
        self.prompt_bar.setMinimumHeight(56)
        self.prompt_bar.setStyleSheet("background:#eee; color:#888; font-size:16px;")
        root.addWidget(self.prompt_bar)
        self.btn_sent = QPushButton("✅ 我已手动点发送")
        self.btn_sent.clicked.connect(self._on_sent)
        self.btn_skip = QPushButton("⏭ 跳过这条")
        self.btn_skip.clicked.connect(self._on_skip)
        self.btn_sent.setEnabled(False); self.btn_skip.setEnabled(False)
        prow = QHBoxLayout()
        prow.addWidget(self.btn_sent); prow.addWidget(self.btn_skip)
        root.addLayout(prow)

        # 快捷键：空格=已发送、Esc=跳过（仅等待确认时启用，避免干扰模板编辑）
        self.sc_sent = QShortcut(QKeySequence(Qt.Key_Space), self)
        self.sc_sent.activated.connect(self._on_sent)
        self.sc_skip = QShortcut(QKeySequence(Qt.Key_Escape), self)
        self.sc_skip.activated.connect(self._on_skip)
        self.sc_sent.setEnabled(False)
        self.sc_skip.setEnabled(False)

        # 结果表
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["手机号", "姓名", "公司", "状态", "备注", "时间"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self.table, 1)

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setFixedHeight(130)
        self.log_box.setStyleSheet("color:#555; background:#fafafa;")
        root.addWidget(self.log_box)

        # 高亮闪烁
        from PyQt5.QtCore import QTimer
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._blink)
        self._timer.start(600)

    # --------------------------- 通用 ---------------------------
    def _log(self, m: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_box.append(f"[{ts}] {m}")
        sb = self.log_box.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _blink(self):
        if not getattr(self, "_awaiting", False):
            return
        self._blink_on = not self._blink_on
        if self._blink_on:
            self.prompt_bar.setStyleSheet(
                "background:#ff5a5a; color:#fff; font-size:20px; font-weight:bold;")
        else:
            self.prompt_bar.setStyleSheet(
                "background:#ffd36b; color:#7a0000; font-size:20px; font-weight:bold;")

    # --------------------------- 设备体检 ---------------------------
    def _on_detect(self):
        """一键设备体检：设备/系统/目标App/输入法/OCR 链路逐项检查，不过不许开始。"""
        self.dev_label.setText("体检中…")
        self.serial = ""
        ok, report = self._preflight()
        self._preflight_ok = ok
        self.dev_label.setText(report)
        self.dev_label.setStyleSheet("color:#080;" if ok else "color:#a00;")
        self._log(("设备体检通过：" if ok else "设备体检未通过：") + report)
        self._update_start_enabled()

    def _preflight(self):
        """接入体检。返回 (关键项是否全部通过, 报告文本)。"""
        try:
            devs = self.adb.list_devices()
        except Exception as e:  # noqa
            return False, f"adb 不可用：{str(e).splitlines()[0]}"
        if not devs:
            return False, "未检测到设备（插 USB、开调试、授权本机）"
        self.serial = devs[0]
        self.adb.serial = self.serial
        checks = [f"设备 {self.serial}"]
        critical = True
        # 系统版本 / 分辨率
        try:
            ver = (self.adb.shell("getprop ro.build.version.release") or "").strip()
            if ver:
                checks.append(f"Android {ver}")
        except Exception:  # noqa
            pass
        try:
            w, h = self.adb.screen_size()
            checks.append(f"{w}x{h}")
        except Exception:  # noqa
            pass
        # 目标 App 已安装（随平台下拉变化）
        plat = self.combo_platform.currentText()
        pkg = "com.alibaba.android.rimet" if plat == "钉钉" else "com.tencent.mm"
        try:
            installed = pkg in (self.adb.shell(f"pm list packages {pkg}") or "")
        except Exception:  # noqa
            installed = False
        if installed:
            checks.append(f"已装{plat}")
        else:
            checks.append(f"未安装{plat}")
            critical = False
        # ADBKeyboard（中文话术必需）
        try:
            has_kb = "com.android.adbkeyboard" in (self.adb.shell(
                "pm list packages com.android.adbkeyboard") or "")
        except Exception:  # noqa
            has_kb = False
        if has_kb:
            try:
                self.adb.set_adb_keyboard(True)
                checks.append("ADBKeyboard已启用")
            except Exception:  # noqa
                checks.append("ADBKeyboard启用失败")
        else:
            checks.append("未装ADBKeyboard(中文无法输入)")
            critical = False
        # OCR 链路自检（真机实拍实读，验证该机渲染下 OCR 可用）
        if not LOC.ocr_available():
            checks.append("电脑缺tesseract")
            critical = False
        else:
            try:
                n = len(LOC.ocr_text(self.adb.screencap_png()).strip())
                checks.append(f"OCR自检{n}字符" if n > 0
                              else "OCR自检0字符(纯图标页可忽略)")
            except Exception:  # noqa
                checks.append("OCR自检失败")
        # 亮屏状态
        try:
            if "mWakefulness=Awake" not in (self.adb.shell("dumpsys power") or ""):
                checks.append("手机疑似息屏,请亮屏")
        except Exception:  # noqa
            pass
        return critical, "　|　".join(checks)

    # --------------------------- Excel ---------------------------
    def _on_open(self):
        if openpyxl is None:
            QMessageBox.critical(self, "缺依赖", "未安装 openpyxl")
            return
        from PyQt5.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(self, "选择 Excel", "", "Excel (*.xlsx *.xlsm)")
        if not path:
            return
        if path.lower().endswith(".xls"):
            QMessageBox.warning(self, "格式不支持", "请把 .xls 另存为 .xlsx 再导入")
            return
        try:
            headers, rows = self._read_sheet(path)
        except Exception as e:  # noqa
            QMessageBox.critical(self, "读取失败", str(e).splitlines()[0])
            return
        if not headers or not rows:
            QMessageBox.warning(self, "空文件", "未读到有效数据")
            return
        self.headers, self.rows = headers, rows
        self.x_label.setText(f"{os.path.basename(path)}（{len(rows)} 行 × {len(headers)} 列）")
        self.x_label.setStyleSheet("color:#000;")
        # 识别手机号列
        cols = [[r[i] if i < len(r) else None for r in rows] for i in range(len(headers))]
        p_idx = self._detect_phone_col(headers, cols)
        self.combo_phone.clear()
        self.combo_phone.addItem("（选择列）", -1)
        for i, h in enumerate(headers):
            self.combo_phone.addItem(f"{h or '(空)'}  [第{i+1}列]", i)
        if p_idx >= 0:
            self.combo_phone.setCurrentIndex(p_idx + 1)
        self.var_combo.clear()
        self.var_combo.addItems(headers)
        self._update_start_enabled()
        self._refresh_preview()
        self._log(f"已加载 {len(rows)} 行；自动识别手机号列="
                  f"{headers[p_idx] if p_idx>=0 else '未识别，请手动选择'}")

    def _read_sheet(self, path):
        wb = openpyxl.load_workbook(path, data_only=True, read_only=False)
        ws = wb.active
        all_rows = list(ws.iter_rows(values_only=True))
        wb.close()
        if not all_rows:
            return [], []
        hi = 0
        for i, r in enumerate(all_rows):
            if any(PV.cell_to_text(c) for c in r):
                hi = i; break
        headers = [PV.cell_to_text(c) for c in all_rows[hi]]
        rows = [list(r) for r in all_rows[hi + 1:] if any(PV.cell_to_text(c) for c in r)]
        return headers, rows

    def _detect_phone_col(self, headers, cols):
        import re
        best, best_sc = -1, 0.0
        for i, h in enumerate(headers):
            hl = (h or "").lower()
            sc = 0
            if any(k in hl for k in ("手机", "电话", "号码", "联系方式", "mobile", "phone", "tel")):
                sc += 60
            sample = [PV.cell_to_text(v) for v in cols[i] if PV.cell_to_text(v)][:100]
            if sample:
                hit = sum(1 for s in sample if re.search(r"\d{7,}", s)) / len(sample)
                sc += hit * 40
            if sc > best_sc:
                best, best_sc = i, sc
        return best if best_sc >= 40 else -1

    # --------------------------- 模板 ---------------------------
    def _insert_var(self):
        name = self.var_combo.currentText()
        if name:
            self.tpl_edit.insert(f"{{{name}}}")

    def _sample_mapping(self) -> Dict[str, str]:
        if not self.rows:
            return {"姓名": "张经理", "公司名": "某某公司"}
        r = self.rows[0]
        return {self.headers[i] or f"列{i+1}": PV.cell_to_text(r[i])
                for i in range(len(self.headers)) if i < len(r)}

    def _overrides(self) -> Dict[str, str]:
        return {"昵称": self.edit_nick.text().strip(),
                "兴趣点": self.edit_interest.text().strip()}

    def _refresh_preview(self, *_):
        tpl = self.tpl_edit.toPlainText()
        txt = T.render(tpl, self._sample_mapping(), self._overrides())
        self.preview_label.setText("预览：" + (txt or "—"))

    # --------------------------- 启动 ---------------------------
    def _update_start_enabled(self):
        ok = (self.chk_gate.isChecked() and bool(self.serial)
              and self._preflight_ok
              and self.combo_phone.currentData() is not None
              and self.combo_phone.currentData() >= 0 and bool(self.rows))
        self.btn_start.setEnabled(bool(ok))

    def _build_items(self):
        p_idx = self.combo_phone.currentData()
        items = []
        skipped = 0
        seen = set()  # 本批号码去重：一行多号拆分后与后续行重复也拦截
        for r in self.rows:
            raw = PV.cell_to_text(r[p_idx]) if p_idx < len(r) else ""
            cr = PV.verify_cell(raw)
            # 一行可能含多个号码（逗号/分隔符隔开）：逐个拆开各自搜索，
            # 而不是整串搜索导致谁也搜不出（真机实测踩过）。
            cand = [n for n in cr.numbers
                    if n.tier in (PV.TIER_VALID, PV.TIER_SUSPECT)
                    and "手机号" in n.kind and n.national]
            mapping = {self.headers[i] or f"列{i+1}": PV.cell_to_text(r[i])
                       for i in range(len(self.headers)) if i < len(r)}
            name = next((mapping[h] for h in self.headers if h and ("名" in h or "联系" in h)), "")
            comp = next((mapping[h] for h in self.headers if h and ("公司" in h or "企业" in h)), "")
            if not cand:
                # 无可搜手机号（含纯固话/无效号）：不占用搜索，直接记跳过（保留原值）
                skipped += 1
                items.append({"phone": raw or "(空)", "name": "", "company": "",
                              "mapping": mapping, "_prefilter": True})
                continue
            for n in cand:
                ph = n.national.split("转")[0]  # 带分机取基号：微信搜索不认分机
                if ph in seen:
                    skipped += 1
                    items.append({"phone": ph, "name": name, "company": comp,
                                  "mapping": mapping, "_prefilter": True,
                                  "_skip_note": "重复号码，本批已排入，未重复搜索"})
                    continue
                seen.add(ph)
                items.append({"phone": ph, "name": name, "company": comp,
                              "mapping": mapping, "_prefilter": False})
        return items, skipped

    def _on_start(self):
        self._update_start_enabled()
        if not self.btn_start.isEnabled():
            QMessageBox.warning(self, "前置未完成",
                                "请先勾选合规门禁、连接手机、上传 Excel 并选择手机号列。")
            return
        self.items, skipped = self._build_items()
        todo = [it for it in self.items if not it.get("_prefilter")]
        if not todo:
            QMessageBox.information(self, "无可核验号码", "本表没有通过离线核验的有效手机号。")
            return
        platform = self.combo_platform.currentText()
        QMessageBox.information(
            self, "请准备好手机",
            f"请在手机上手动打开{platform}「添加好友 → 搜索」页面并停在那里，\n"
            "工具将从该页开始逐条执行；每条填好话术后会响铃提示你在手机上手动点「发送」，\n"
            "点完发送工具会自动识别并继续下一条。\n"
            f"（本次待处理 {len(todo)} 条，另有 {skipped} 条（无有效手机号/重复号码）将跳过、仅记录）")

        # 切到 ADBKeyboard 以便输入中文话术
        try:
            self.adb.set_adb_keyboard(True)
        except Exception:  # noqa
            pass

        tpl = self.tpl_edit.toPlainText()
        ov = self._overrides()

        def greeting_fn(it):
            return T.render(tpl, it.get("mapping", {}), ov)

        caps = (self.spin_day.value(), self.spin_hour.value())
        lo, hi = self.spin_lo.value(), self.spin_hi.value()
        delay = (lo, max(lo, hi))
        self._prefiltered = [it for it in self.items if it.get("_prefilter")]
        flow_cls = WF.DingTalkFlow if platform == "钉钉" else WF.WeChatFlow
        self.worker = Worker(self.adb, todo, greeting_fn, caps, delay,
                             flow_cls=flow_cls, auto_detect=self.chk_auto.isChecked())
        self.worker.log_sig.connect(self._log)
        self.worker.prompt_sig.connect(self._on_prompt)
        self.worker.progress_sig.connect(self._on_progress)
        self.worker.finished_sig.connect(self._on_finished)
        self.worker.start()

        self.btn_start.setEnabled(False)
        self.btn_pause.setEnabled(True)
        self.btn_stop.setEnabled(True)
        self._awaiting = False
        self.progress.setMaximum(max(1, len(todo)))
        self.progress.setValue(0)
        self.stat_label.setText(f"执行中…（待处理 {len(todo)} 条）")
        self._log(f"开始执行：待处理 {len(todo)} 条，预筛跳过 {len(self._prefiltered)} 条。")

    def _on_progress(self, i: int, total: int):
        self.progress.setValue(min(i, self.progress.maximum()))
        self.stat_label.setText(f"执行中…（{i}/{total}）")

    def _on_prompt(self, ctx: Dict):
        self._awaiting = True
        r = ctx.get("result")
        who = ctx.get("name") or (r.phone if r else "")
        self.prompt_bar.setText(
            f"⚠ 请在手机上【手动点击「发送」】（识别到已发送后会自动继续）　—　{who}"
            f"　｜　话术：{ctx.get('greeting','')[:40]}")
        self.btn_sent.setEnabled(True)
        self.btn_skip.setEnabled(True)
        self.sc_sent.setEnabled(True)
        self.sc_skip.setEnabled(True)
        beep_alert()

    def _on_sent(self):
        if not self._awaiting:
            return
        self._awaiting = False
        self._reset_prompt()
        if self.worker:
            self.worker.confirm_sent()

    def _on_skip(self):
        if not self._awaiting:
            return
        self._awaiting = False
        self._reset_prompt()
        if self.worker:
            self.worker.confirm_skip()

    def _reset_prompt(self):
        self.prompt_bar.setText("　")
        self.prompt_bar.setStyleSheet("background:#eee; color:#888; font-size:16px;")
        self.btn_sent.setEnabled(False)
        self.btn_skip.setEnabled(False)
        self.sc_sent.setEnabled(False)
        self.sc_skip.setEnabled(False)

    def _on_pause(self):
        if not self.worker:
            return
        if self.worker._pause.is_set():
            self.worker.resume()
            self.btn_pause.setText("暂停")
            self._log("已继续。")
        else:
            self.worker.pause()
            self.btn_pause.setText("继续")
            self._log("已暂停（下一条开始前会停下）。")

    def _on_stop(self):
        if self.worker:
            self.worker.stop()
            self._log("正在停止…")

    def _on_finished(self, results: List[WF.FlowResult]):
        self._awaiting = False
        self._reset_prompt()
        combined = list(results)
        for it in self._prefiltered:
            combined.append(WF.FlowResult(
                phone=it.get("phone", ""), name=it.get("name", ""),
                company=it.get("company", ""), status=WF.ST_PREFILTER,
                note=it.get("_skip_note") or "无有效手机号，未搜索（保留原值未删）"))
        self.results = combined
        self.btn_start.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.btn_stop.setEnabled(False)
        self.btn_export.setEnabled(bool(combined))
        self.progress.setValue(self.progress.maximum())
        self._render_table(combined)
        n_wait = sum(1 for r in results if r.status == WF.ST_REAL_WAIT)
        self.stat_label.setText(
            f"完成：处理 {len(results)} 条，真实用户待发送 {n_wait} 条，"
            f"预筛跳过 {len(self._prefiltered)} 条。")
        self._log("本轮结束。台账可导出。")

    def _render_table(self, results):
        self.table.setRowCount(len(results))
        for i, r in enumerate(results):
            vals = [r.phone, r.name, r.company, r.status, r.note, r.at]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(str(v))
                if j == 3:
                    col = _STATUS_COLOR.get(r.status)
                    if col:
                        it.setBackground(QBrush(col))
                self.table.setItem(i, j, it)
        self.table.resizeColumnsToContents()

    # --------------------------- 导出 ---------------------------
    def _on_export(self):
        if not self.results or openpyxl is None:
            return
        from PyQt5.QtWidgets import QFileDialog
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        default = os.path.join(os.path.expanduser("~"), "Desktop", f"微信加好友台账_{ts}.xlsx")
        path, _ = QFileDialog.getSaveFileName(self, "导出台账", default, "Excel (*.xlsx)")
        if not path:
            return
        try:
            wb = openpyxl.Workbook(); ws = wb.active; ws.title = "台账"
            ws.append(["手机号", "姓名", "公司", "状态", "备注", "时间"])
            for c in range(1, 7):
                ws.cell(1, c).font = openpyxl.styles.Font(bold=True)
            for r in self.results:
                ws.append([r.phone, r.name, r.company, r.status, r.note, r.at])
            ws.freeze_panes = "A2"
            wb.save(path); wb.close()
        except PermissionError:
            QMessageBox.critical(self, "导出失败", "文件被占用，请关闭同名 Excel 后重试")
            return
        except Exception as e:  # noqa
            QMessageBox.critical(self, "导出失败", str(e).splitlines()[0])
            return
        self._log(f"台账已导出：{path}")
        QMessageBox.information(self, "导出成功", f"台账已保存：\n{path}")


# ---------------------------------------------------------------------------
def main():
    app = QApplication(sys.argv)
    app.setFont(_ui_font())
    w = MainWindow()
    w.show()
    # ── 有序收尾：窗口 → QApplication（与 b2bcollect_gui.main 同一条纪律）─────────
    # app 比窗口**先**定义，解释器拆帧按定义顺序清局部变量 → QApplication 先析构、窗口
    # wrapper 后析构，PyQt 拆后者时撞上的是一具已经随 app 没掉的 C++ 对象：退出 SIGSEGV
    # （b2bcollect 线上实测抓到过同一条栈，用户侧表现是「关掉工具弹一次系统崩溃报告」）。
    # 所以趁 app 还活着先把窗口收掉。本工具的 MainWindow **没有** closeEvent（退出时没有
    # 要落盘的东西），这几句纯粹是把 wrapper 抢在 app 之前交出去；仍写 isVisible() 是为
    # 以后万一加了 closeEvent，也不会把同一段收尾跑两遍（用户自己关窗口那次已经跑过了）。
    rc = app.exec_()
    try:
        # 后台线程还在跑就先请它停、再有界地等一会儿：直接拆窗口时 Qt 会报
        # "QThread: Destroyed while thread is still running"，运气不好连着一起崩
        # （riskbird 与 ali1688 的 closeEvent 里早就有同一段有界等待，本工具一直没有人管）。
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
