"""
riskbird_gui.py - 美亚风鸟企业数据采集工具 GUI
使用 PyQt5 构建，支持 Excel 上传、登录管理、批量采集、日志输出、Excel 导出
"""

import sys
import os
import re
import time
import threading
from pathlib import Path
from datetime import datetime

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTextEdit, QProgressBar, QFileDialog,
    QGroupBox, QFrame, QSplitter, QMessageBox, QDialog,
    QLineEdit, QCheckBox, QStatusBar, QSizePolicy, QScrollArea,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont, QIcon, QColor, QPalette

from riskbird_crawler_v2 import RiskBirdCrawlerV2, is_chrome_installed, download_and_install_chrome
from qizhidao_crawler import QiZhiDaoCrawler
from aiqicha_crawler_v2 import AiQiChaCrawlerV2

try:
    import customer_library_cleaner as _cleaner
    HAS_CLEANER = True
except ImportError:
    HAS_CLEANER = False

try:
    import openpyxl
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False


# ══════════════════════════════════════════════
#  登录对话框
# ═══════════════════════════════════════════════
class LoginDialog(QDialog):
    def __init__(self, crawler, parent=None):
        super().__init__(parent)
        self.crawler = crawler
        self.setWindowTitle("美亚风鸟 - 登录")
        self.setFixedSize(420, 320)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        layout.setContentsMargins(30, 25, 30, 25)

        # 标题
        title = QLabel("美亚风鸟 账号登录")
        title.setFont(QFont("Microsoft YaHei" if sys.platform == "win32" else "Arial", 16, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        layout.addSpacing(10)

        # 手机号
        phone_layout = QHBoxLayout()
        phone_layout.addWidget(QLabel("手机号："))
        self.phone_edit = QLineEdit()
        self.phone_edit.setPlaceholderText("请输入登录手机号")
        self.phone_edit.setMinimumHeight(36)
        phone_layout.addWidget(self.phone_edit)
        layout.addLayout(phone_layout)

        # 密码
        pwd_layout = QHBoxLayout()
        pwd_layout.addWidget(QLabel("密  码："))
        self.pwd_edit = QLineEdit()
        self.pwd_edit.setPlaceholderText("请输入密码")
        self.pwd_edit.setEchoMode(QLineEdit.Password)
        self.pwd_edit.setMinimumHeight(36)
        pwd_layout.addWidget(self.pwd_edit)
        layout.addLayout(pwd_layout)

        layout.addSpacing(5)

        # 按钮
        btn_layout = QHBoxLayout()

        self.auto_btn = QPushButton("自动登录")
        self.auto_btn.setMinimumHeight(40)
        self.auto_btn.setStyleSheet(
            "QPushButton { background-color: #4472C4; color: white; border-radius: 5px; font-size: 14px; }"
            "QPushButton:hover { background-color: #3560A8; }"
        )
        self.auto_btn.clicked.connect(self._do_auto_login)
        btn_layout.addWidget(self.auto_btn)

        self.manual_btn = QPushButton("手动登录")
        self.manual_btn.setMinimumHeight(40)
        self.manual_btn.setStyleSheet(
            "QPushButton { background-color: #70AD47; color: white; border-radius: 5px; font-size: 14px; }"
            "QPushButton:hover { background-color: #5E9639; }"
        )
        self.manual_btn.clicked.connect(self._do_manual_login)
        btn_layout.addWidget(self.manual_btn)

        layout.addLayout(btn_layout)

        # 提示
        tip = QLabel(" 自动登录：输入账号密码后点击\n 手动登录：打开浏览器手动操作后点验证")
        tip.setFont(QFont("Microsoft YaHei" if sys.platform == "win32" else "Arial", 9))
        tip.setStyleSheet("color: #666;")
        tip.setAlignment(Qt.AlignCenter)
        layout.addWidget(tip)

        # 状态标签
        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status_label)

    def _do_auto_login(self):
        phone = self.phone_edit.text().strip()
        pwd = self.pwd_edit.text().strip()
        if not phone or not pwd:
            self.status_label.setText("请输入手机号和密码")
            self.status_label.setStyleSheet("color: red;")
            return
        self.status_label.setText("正在启动浏览器...")
        self.status_label.setStyleSheet("color: blue;")
        self.auto_btn.setEnabled(False)
        self.manual_btn.setEnabled(False)

        def _login():
            # 确保浏览器已启动
            if self.crawler.driver is None:
                self.crawler.start_browser(headed=True)
            ok = self.crawler.login_with_credentials(phone, pwd)
            self.auto_btn.setEnabled(True)
            self.manual_btn.setEnabled(True)
            if ok:
                self.status_label.setText("登录成功！")
                self.status_label.setStyleSheet("color: green;")
                QTimer.singleShot(1000, self.accept)
            else:
                self.status_label.setText("登录失败，请检查账号密码或尝试手动登录")
                self.status_label.setStyleSheet("color: red;")

        from PyQt5.QtCore import QTimer
        threading.Thread(target=_login, daemon=True).start()

    def _do_manual_login(self):
        self.status_label.setText("正在启动浏览器，请稍候...")
        self.status_label.setStyleSheet("color: blue;")
        self.auto_btn.setEnabled(False)
        self.manual_btn.setEnabled(False)

        def _start_and_login():
            try:
                if self.crawler.driver is None:
                    self.status_label.setText("正在启动 Chrome...")
                    self.crawler.start_browser(headed=True)
                    self.status_label.setText("Chrome 已启动，正在打开登录页...")
                else:
                    self.status_label.setText("浏览器已就绪，正在打开登录页...")
                self.crawler.login_manual()
                # 回到 GUI 线程更新界面
                self.auto_btn.setEnabled(True)
                self.manual_btn.setEnabled(True)
                self.status_label.setText("请在浏览器中完成登录...")
                self.status_label.setStyleSheet("color: blue;")
                # 添加"我已登录"按钮
                from PyQt5.QtCore import QTimer
                def _add_verify_btn():
                    # 如果按钮已存在，先删除
                    if hasattr(self, 'verify_btn'):
                        self.verify_btn.deleteLater()
                    # 创建新按钮
                    self.verify_btn = QPushButton("✅ 我已登录")
                    self.verify_btn.setMinimumHeight(45)
                    self.verify_btn.setStyleSheet(
                        "QPushButton { background-color: #28a745; color: white; border-radius: 5px; font-size: 15px; font-weight: bold; padding: 10px; }"
                        "QPushButton:hover { background-color: #218838; }"
                    )
                    self.verify_btn.clicked.connect(self._do_verify)
                    # 直接添加到布局
                    self.layout().addWidget(self.verify_btn)
                QTimer.singleShot(100, _add_verify_btn)
            except Exception as e:
                import traceback
                err_detail = traceback.format_exc()
                self.auto_btn.setEnabled(True)
                self.manual_btn.setEnabled(True)
                self.status_label.setText(f"启动浏览器失败: {e}")
                self.status_label.setStyleSheet("color: red;")
                print("=== 浏览器启动错误详情 ===")
                print(err_detail)
                print("========================")

        threading.Thread(target=_start_and_login, daemon=True).start()

    def _do_verify(self):
        ok = self.crawler.verify_manual_login()
        if ok:
            self.status_label.setText("验证成功！")
            self.status_label.setStyleSheet("color: green;")
            from PyQt5.QtCore import QTimer
            QTimer.singleShot(1000, self.accept)
        else:
            self.status_label.setText("尚未检测到登录状态")
            self.status_label.setStyleSheet("color: red;")


# ═══════════════════════════════════════════════
#  主窗口
# ═══════════════════════════════════════════════
# ══════════════════════════════════════════════
#  公司名列智能识别（表头语义打分 + 单元格内容抽样打分，低置信时人工确认）
# ══════════════════════════════════════════════
_COMPANY_SUFFIX_RE = re.compile(
    r"(有限责任公司|股份有限公司|有限公司|集团公司|合作社|经营部|工作室|事务所|"
    r"研究中心|中心|集团|公司|工厂|厂|商行|商店|门市部|营业部|分公司|办事处|协会|商会|研究院|研究所)$"
)
_COMPANY_BODY_RE = re.compile(r"(公司|集团|工厂|商行|商店|门市部|经营部|工作室|事务所|中心|厂)")
_ORG_KEYWORDS = ("银行", "医院", "学校", "学院", "大学", "政府", "管理局", "委员会", "合作社")
_PERSON_NAME_RE = re.compile(r"^[\u4e00-\u9fa5]{2,3}$")

# 表头精确命中（归一化后全等）
_HEADER_EXACT = {
    "公司名", "公司名称", "企业名", "企业名称", "单位名称", "公司", "企业", "单位",
    "客户名称", "客户", "厂商名称", "厂家名称", "供应商名称", "商户名称", "店铺名称",
    "企业名称公司名称", "companyname", "company", "企业名称或姓名",
}
# 表头强包含命中
_HEADER_STRONG = ("公司名称", "企业名称", "公司名称", "单位名称", "客户名称", "供应商名称",
                  "厂商名称", "厂家名称", "公司名", "企业名", "单位名称", "商户名称")
# 表头弱命中（还需内容打分佐证）
_HEADER_WEAK = ("公司", "企业", "单位", "客户", "厂商", "厂家", "供应商", "商户", "店铺", "名称")
# 表头负向词（命中则大幅降分，防止"公司电话""企业地址"等误判）
_HEADER_PENALTY = ("电话", "手机", "联系", "地址", "邮箱", "邮件", "网址", "官网", "法人", "代表人",
                   "税号", "编号", "编码", "账号", "银行", "状态", "类型", "日期", "时间", "金额",
                   "注册资本", "营业", "范围", "所在", "地区", "省份", "城市", "备注", "来源",
                   "姓名", "负责人", "经理", "职位", "职务", "QQ", "qq", "微信")


def _normalize_header(h) -> str:
    s = re.sub(r"[\s：:：*#\d一二三四五六七八九十（）()\[\]【】/\\\-_.。、,，]", "", str(h or ""))
    return s.lower()


def _header_score(header) -> int:
    """表头语义打分：精确 > 强包含 > 弱包含，负向词扣分"""
    norm = _normalize_header(header)
    if not norm:
        return 0
    if norm in _HEADER_EXACT:
        score = 100
    elif any(k in norm for k in _HEADER_STRONG):
        score = 80
    elif any(k in norm for k in _HEADER_WEAK):
        score = 40
    else:
        score = 0
    if any(p in str(header or "") for p in _HEADER_PENALTY):
        score -= 60
    return max(score, 0)


def _company_name_score(value) -> float:
    """单元格内容像不像公司名（0~1），抽样打分的核心"""
    if value is None:
        return 0.0
    s = str(value).strip()
    if not s or s.lower() in ("none", "null", "-"):
        return 0.0
    if len(s) < 4 or len(s) > 60:
        return 0.0
    # 纯数字/日期/电话/邮箱/网址 → 绝不可能是公司名
    if re.fullmatch(r"[\d\s.,\-/年月日:：()（）万亿千百元整]+", s):
        return 0.0
    if re.search(r"[@]|\.(com|cn|net|org)|^(https?://|www\.)|1[3-9]\d{9}|0\d{2,3}-\d{7,8}", s):
        return 0.0
    han = re.findall(r"[\u4e00-\u9fa5]", s)
    if len(han) < 4:
        return 0.0
    # 以企业组织后缀结尾 → 最像
    if _COMPANY_SUFFIX_RE.search(s):
        return 1.0
    # 含公司/集团等主体词且足够长
    if _COMPANY_BODY_RE.search(s) and len(s) >= 6:
        return 0.9
    # 机构类名称（银行/医院/学校等）
    if len(s) >= 6 and any(k in s for k in _ORG_KEYWORDS):
        return 0.6
    # 2~3 个纯汉字 → 更像联系人姓名
    if _PERSON_NAME_RE.fullmatch(s):
        return 0.1
    return 0.2


def _detect_company_column(rows) -> dict:
    """综合表头与内容识别公司名列。rows 为前若干行的元组列表（第 1 行可能是表头）。
    返回 {"col", "start_row", "need_confirm", "candidates"}"""
    if not rows:
        return {"col": 0, "start_row": 1, "need_confirm": False, "candidates": []}
    first = rows[0]
    ncols = max(len(r) for r in rows)

    # 第一行本身就是数据（无表头文件）的判断
    first_scores = [_company_name_score(v) for v in first]
    headerless = sum(1 for s in first_scores if s >= 0.9) >= max(1, len(first_scores) // 2)

    candidates = []
    for c in range(ncols):
        header = str(first[c]).strip() if c < len(first) and first[c] is not None else ""
        hs = 0 if headerless else _header_score(header)
        vals = [r[c] if c < len(r) else None for r in rows[(0 if headerless else 1):21]]
        vals = [v for v in vals if v is not None and str(v).strip()]
        cs = (sum(_company_name_score(v) for v in vals[:20]) / min(len(vals), 20)) * 100 if vals else 0.0
        total = hs * 0.4 + cs * 0.6
        candidates.append({
            "col": c, "header": header,
            "sample": "、".join(str(v).strip()[:20] for v in vals[:3]),
            "header_score": round(hs), "content_score": round(cs), "total": round(total),
        })
    candidates.sort(key=lambda x: -x["total"])

    best = candidates[0] if candidates else None
    need_confirm = True
    if headerless:
        need_confirm = best is None or best["content_score"] < 60
    elif best:
        second = candidates[1] if len(candidates) > 1 else None
        ambiguous = second and abs(best["total"] - second["total"]) < 15 and best["header_score"] == 0
        need_confirm = best["total"] < 60 or bool(ambiguous)
    return {
        "col": best["col"] if best else 0,
        "start_row": 0 if headerless else 1,
        "need_confirm": need_confirm,
        "candidates": candidates,
    }


class CompanyColumnDialog(QDialog):
    """低置信时让用户确认公司名列（默认选中打分最高列）"""

    def __init__(self, candidates, best_col, filename, parent=None):
        super().__init__(parent)
        self.selected_col = best_col
        self.remember = True
        self.setWindowTitle("确认公司名列")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            f"文件「{os.path.basename(filename)}」无法确定公司名所在列，请人工选择：\n"
            "（已按可能性排序，选项格式：列号 | 表头 | 内容示例）"
        ))
        self._boxes = {}
        for cand in candidates[:10]:
            text = f"第{cand['col'] + 1}列 | {cand['header'] or '(无表头)'} | {cand['sample'] or '(空)'}"
            cb = QCheckBox(text)
            cb.setChecked(cand["col"] == best_col)
            cb.toggled.connect(lambda on, c=cand["col"]: on and self._pick(c))
            self._boxes[cand["col"]] = cb
            layout.addWidget(cb)
        self._remember_chk = QCheckBox("记住选择（本次运行中同类文件不再询问）")
        self._remember_chk.setChecked(True)
        layout.addWidget(self._remember_chk)
        btn_row = QHBoxLayout()
        ok_btn = QPushButton("确定")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    def _pick(self, col):
        # 单选互斥：勾一个自动取消其它
        for c, box in self._boxes.items():
            if c != col and box.isChecked():
                box.blockSignals(True)
                box.setChecked(False)
                box.blockSignals(False)
        self.selected_col = col

    def accept(self):
        self.remember = self._remember_chk.isChecked()
        super().accept()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        # 初始化三个爬虫
        self.riskbird_crawler = RiskBirdCrawlerV2()
        self.qizhidao_crawler = QiZhiDaoCrawler()
        self.aiqicha_crawler = AiQiChaCrawlerV2()
        self.crawler = self.qizhidao_crawler  # 默认使用企知道
        
        # 连接信号
        for crawler in [self.riskbird_crawler, self.qizhidao_crawler, self.aiqicha_crawler]:
            crawler.signals.log_signal.connect(self._append_log)
            crawler.signals.progress_signal.connect(self._update_progress)
            crawler.signals.crawl_finished_signal.connect(self._on_crawl_finished)
        # 风鸟特有的信号
        self.riskbird_crawler.signals.login_status_signal.connect(self._on_login_status)
        self.riskbird_crawler.signals.manual_check_signal.connect(self._on_manual_check)

        self._companies = []
        self._excel_paths = []  # 支持多文件
        self._col_pick_cache = {}  # 公司名列人工确认结果缓存（表头组合 → 列号）
        self._crawl_thread = None
        self._current_source = 1  # 0=风鸟，1=企知道，2=爱企查
        # 跨平台续采 + 桌面会话文件夹 + 每 10 条自动保存
        self._all_crawlers = [self.riskbird_crawler, self.qizhidao_crawler, self.aiqicha_crawler]
        self._session_folder = None       # 桌面/企业数据自动采集工具/<第一次开始采集时间>
        self._last_autosave_bucket = 0    # 每 10 条自动保存的计数桶

        self._update_window_title()
        self.setMinimumSize(900, 700)
        self.resize(1000, 750)
        self._init_ui()
        self._apply_style()

    # ── UI 初始化 ──────────────────────────────
    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(12)
        main_layout.setContentsMargins(15, 15, 15, 15)

        # ── 顶部：标题栏 ──
        title_bar = QHBoxLayout()
        self.title_label = QLabel("企知道 企业数据采集工具")  # 默认企知道，动态更新
        self.title_label.setFont(QFont("Microsoft YaHei" if sys.platform == "win32" else "Arial", 18, QFont.Bold))
        self.title_label.setStyleSheet("color: #1a3c6e;")
        title_bar.addWidget(self.title_label)
        title_bar.addStretch()

        # 数据来源选择
        source_lbl = QLabel("数据来源:")
        source_lbl.setFont(QFont("Microsoft YaHei" if sys.platform == "win32" else "Arial", 10))
        title_bar.addWidget(source_lbl)
        
        from PyQt5.QtWidgets import QComboBox
        self.source_combo = QComboBox()
        self.source_combo.addItem("风鸟 (riskbird.com)")
        self.source_combo.addItem("企知道 (qizhidao.com)")
        self.source_combo.addItem("爱企查 (aiqicha.baidu.com)")
        self.source_combo.setCurrentIndex(1)  # 默认企知道
        self.source_combo.setMinimumWidth(220)
        self.source_combo.setMinimumHeight(30)
        self.source_combo.currentIndexChanged.connect(self._on_source_changed)
        title_bar.addWidget(self.source_combo)

        # 登录状态
        self.login_status_lbl = QLabel("未登录")
        self.login_status_lbl.setFont(QFont("Microsoft YaHei" if sys.platform == "win32" else "Arial", 10))
        self.login_status_lbl.setStyleSheet(
            "QLabel { padding: 5px 12px; border-radius: 4px; background: #f0f0f0; color: #999; }"
        )
        title_bar.addWidget(self.login_status_lbl)

        # 打开网页按钮
        self.open_web_btn = QPushButton("打开网页")
        self.open_web_btn.setMinimumHeight(34)
        self.open_web_btn.setMinimumWidth(80)
        self.open_web_btn.clicked.connect(self._open_website)
        title_bar.addWidget(self.open_web_btn)

        main_layout.addLayout(title_bar)

        # 提示
        hint_lbl = QLabel("提示：如未登录请先登录后再操作其他功能")
        hint_lbl.setFont(QFont("Microsoft YaHei" if sys.platform == "win32" else "Arial", 9))
        hint_lbl.setStyleSheet("color: #999; padding: 2px 0;")
        hint_lbl.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(hint_lbl)

        # ─ 文件选择区 ──
        file_group = QGroupBox("  数据文件（支持多选）")
        file_layout = QVBoxLayout(file_group)

        # 第一行：文件路径显示
        path_layout = QHBoxLayout()
        self.file_path_lbl = QLabel("请选择 Excel 文件（.xlsx），支持多选")
        self.file_path_lbl.setStyleSheet("color: #666; font-size: 13px;")
        self.file_path_lbl.setWordWrap(True)
        path_layout.addWidget(self.file_path_lbl, 1)
        file_layout.addLayout(path_layout)

        # 第二行：按钮
        btn_layout = QHBoxLayout()
        self.select_file_btn = QPushButton("选择文件（可多选）")
        self.select_file_btn.setMinimumHeight(36)
        self.select_file_btn.setMinimumWidth(140)
        self.select_file_btn.clicked.connect(self._select_files)
        btn_layout.addWidget(self.select_file_btn)

        self.clear_files_btn = QPushButton("清空文件列表")
        self.clear_files_btn.setMinimumHeight(36)
        self.clear_files_btn.setMinimumWidth(120)
        self.clear_files_btn.setEnabled(False)
        self.clear_files_btn.clicked.connect(self._clear_files)
        btn_layout.addWidget(self.clear_files_btn)

        self.preview_btn = QPushButton("预览公司列表")
        self.preview_btn.setMinimumHeight(36)
        self.preview_btn.setMinimumWidth(120)
        self.preview_btn.setEnabled(False)
        self.preview_btn.clicked.connect(self._preview_companies)
        btn_layout.addWidget(self.preview_btn)

        self.company_count_lbl = QLabel("共 0 家")
        self.company_count_lbl.setStyleSheet("color: #4472C4; font-weight: bold; font-size: 13px;")
        btn_layout.addWidget(self.company_count_lbl)
        btn_layout.addStretch()
        file_layout.addLayout(btn_layout)

        main_layout.addWidget(file_group)

        # ── 控制区 ──
        ctrl_group = QGroupBox("  采集控制")
        ctrl_layout = QHBoxLayout(ctrl_group)

        self.start_btn = QPushButton("开始采集")
        self.start_btn.setMinimumHeight(42)
        self.start_btn.setMinimumWidth(100)
        self.start_btn.setEnabled(False)
        self.start_btn.clicked.connect(self._start_crawl)
        ctrl_layout.addWidget(self.start_btn)

        self.pause_btn = QPushButton("暂停")
        self.pause_btn.setMinimumHeight(42)
        self.pause_btn.setMinimumWidth(80)
        self.pause_btn.setEnabled(False)
        self.pause_btn.clicked.connect(self._pause_crawl)
        ctrl_layout.addWidget(self.pause_btn)

        self.resume_btn = QPushButton("恢复")
        self.resume_btn.setMinimumHeight(42)
        self.resume_btn.setMinimumWidth(80)
        self.resume_btn.setEnabled(False)
        self.resume_btn.clicked.connect(self._resume_crawl)
        ctrl_layout.addWidget(self.resume_btn)

        self.stop_btn = QPushButton("停止采集")
        self.stop_btn.setMinimumHeight(42)
        self.stop_btn.setMinimumWidth(100)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop_crawl)
        ctrl_layout.addWidget(self.stop_btn)

        self.export_btn = QPushButton("导出 Excel")
        self.export_btn.setMinimumHeight(42)
        self.export_btn.setMinimumWidth(100)
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._export_excel)
        ctrl_layout.addWidget(self.export_btn)

        ctrl_layout.addStretch()

        # 延迟设置
        delay_layout = QHBoxLayout()
        delay_layout.addWidget(QLabel("请求间隔(秒):"))
        self.delay_spin = QLineEdit("0.5")
        self.delay_spin.setFixedWidth(50)
        delay_layout.addWidget(self.delay_spin)
        ctrl_layout.addLayout(delay_layout)

        main_layout.addWidget(ctrl_group)

        # ── 进度条 + 计时器 ──
        progress_layout = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimumHeight(28)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("就绪")
        progress_layout.addWidget(self.progress_bar, 1)

        self.timer_lbl = QLabel("⏱ 00:00:00")
        self.timer_lbl.setFont(QFont("Menlo" if sys.platform == "darwin" else "Consolas", 11))
        self.timer_lbl.setStyleSheet("color: #1a73e8; font-weight: bold; padding: 0 12px;")
        self.timer_lbl.setMinimumWidth(100)
        self.timer_lbl.setAlignment(Qt.AlignCenter)
        progress_layout.addWidget(self.timer_lbl)

        main_layout.addLayout(progress_layout)

        # ── 各平台已采集条数（跨平台续采时直观显示进度） ──
        count_font = "Microsoft YaHei" if sys.platform == "win32" else "Arial"
        self.platform_count_lbl = QLabel("风鸟: 0 条    企知道: 0 条    爱企查: 0 条    合计: 0 条")
        self.platform_count_lbl.setFont(QFont(count_font, 10, QFont.Bold))
        self.platform_count_lbl.setStyleSheet(
            "QLabel { color: #1a73e8; background: #eef4ff; border: 1px solid #d0e0ff; border-radius: 4px; padding: 6px; }"
        )
        self.platform_count_lbl.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(self.platform_count_lbl)

        # 采集计时器
        from PyQt5.QtCore import QTimer
        self._crawl_timer = QTimer()
        self._crawl_timer.timeout.connect(self._update_timer)
        self._crawl_start_time = None
        self._crawl_elapsed = 0  # 累计已用时间（秒），暂停时保留

        # ── 日志区 ──
        log_group = QGroupBox("  运行日志")
        log_layout = QVBoxLayout(log_group)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Menlo" if sys.platform == "darwin" else "Consolas", 10))
        self.log_text.setMinimumHeight(300)
        log_layout.addWidget(self.log_text)

        # 日志控制
        log_ctrl = QHBoxLayout()
        self.clear_log_btn = QPushButton("清空日志")
        self.clear_log_btn.clicked.connect(self.log_text.clear)
        log_ctrl.addWidget(self.clear_log_btn)
        log_ctrl.addStretch()
        self.log_count_lbl = QLabel("日志: 0 条")
        log_ctrl.addWidget(self.log_count_lbl)
        log_layout.addLayout(log_ctrl)

        main_layout.addWidget(log_group, 1)

        # ── 状态栏 ──
        self.statusBar().showMessage("就绪 — 请选择 Excel 文件并登录")

    # ── 样式 ───────────────────────────────────
    def _apply_style(self):
        font_family = "Microsoft YaHei" if sys.platform == "win32" else "Arial"
        self.setStyleSheet(f"""
            QMainWindow {{ background-color: #f5f7fa; }}
            QGroupBox {{
                font-size: 14px; font-weight: bold; font-family: '{font_family}'; color: #1a3c6e;
                border: 1px solid #d0d8e8; border-radius: 6px;
                margin-top: 10px; padding-top: 15px;
            }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 5px; }}
            QPushButton {{
                background-color: #4472C4; color: white; border: none;
                border-radius: 4px; font-size: 13px; padding: 6px 16px;
            }}
            QPushButton:hover {{ background-color: #3560A8; }}
            QPushButton:disabled {{ background-color: #b0b0b0; }}
            QPushButton#stopBtn {{ background-color: #ED7D31; }}
            QPushButton#stopBtn:hover {{ background-color: #D06A20; }}
            QPushButton#exportBtn {{ background-color: #70AD47; }}
            QPushButton#exportBtn:hover {{ background-color: #5E9639; }}
            QProgressBar {{
                border: 1px solid #ccc; border-radius: 4px;
                text-align: center; background: #e8e8e8;
            }}
            QProgressBar::chunk {{ background-color: #4472C4; border-radius: 3px; }}
            QTextEdit {{ border: 1px solid #d0d8e8; border-radius: 4px; background: white; }}
        """)
        self.stop_btn.setObjectName("stopBtn")
        self.export_btn.setObjectName("exportBtn")

    # ── 文件选择 ───────────────────────────────
    def _select_files(self):
        """选择多个 Excel 文件"""
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择 Excel 文件（可多选）", "",
            "Excel 文件 (*.xlsx *.xls);;所有文件 (*)"
        )
        if paths:
            self._excel_paths.extend(paths)
            # 显示文件列表
            if len(paths) == 1:
                self.file_path_lbl.setText(paths[0])
            else:
                self.file_path_lbl.setText(f"已选择 {len(paths)} 个文件")
            self.file_path_lbl.setStyleSheet("color: #333; font-size: 12px;")
            self.clear_files_btn.setEnabled(True)
            
            # 加载所有文件的公司名
            all_companies = []
            for path in paths:
                companies = self._load_companies_from_file(path)
                all_companies.extend(companies)
            
            # 去重
            self._companies = list(dict.fromkeys(all_companies))
            self.company_count_lbl.setText(f"共 {len(self._companies)} 家（去重后）")
            self.preview_btn.setEnabled(True)
            self._check_ready()
            self._append_log(f"从 {len(paths)} 个文件加载 {len(all_companies)} 家公司，去重后 {len(self._companies)} 家")

    def _load_companies_from_file(self, path: str) -> list:
        """从单个文件加载公司名（智能识别公司名列：表头语义 + 内容抽样打分，低置信弹窗确认）"""
        try:
            from openpyxl import load_workbook
            wb = load_workbook(path, read_only=False, data_only=True)
            ws = wb.active

            # 取前 21 行做检测样本（1 行表头 + 20 行内容）
            sample = [row for row in ws.iter_rows(min_row=1, max_row=21, values_only=True)]
            det = _detect_company_column(sample)
            company_col = det["col"]
            start_row = det["start_row"] + 1  # iter_rows 的 min_row 是 1-based

            best = det["candidates"][0] if det["candidates"] else None
            self._append_log(
                f"公司名列识别：第{company_col + 1}列 "
                f"({best['header'] if best else 'N/A'}) "
                f"[表头分{best['header_score'] if best else 0}/内容分{best['content_score'] if best else 0}]"
            )

            # 低置信 → 弹窗人工确认（本会话已记住的表头组合直接复用）
            if det["need_confirm"] and det["candidates"]:
                cache_key = tuple(c["header"] for c in sorted(det["candidates"], key=lambda x: x["col"]))
                if cache_key in self._col_pick_cache:
                    company_col = self._col_pick_cache[cache_key]
                    self._append_log(f"  ↳ 使用已记住的选择：第{company_col + 1}列")
                else:
                    dlg = CompanyColumnDialog(det["candidates"], company_col, path, self)
                    if dlg.exec_() == QDialog.Accepted:
                        company_col = dlg.selected_col
                        if dlg.remember:
                            self._col_pick_cache[cache_key] = company_col
                        self._append_log(f"  ↳ 人工确认公司名列：第{company_col + 1}列")
                    else:
                        wb.close()
                        self._append_log("  ↳ 已取消选择，未加载该文件")
                        return []

            # 读取数据行（跳过空行与无效值）
            companies = []
            for row in ws.iter_rows(min_row=start_row, values_only=True):
                if row and len(row) > company_col:
                    cell_value = row[company_col]
                    if cell_value is not None:
                        name = str(cell_value).strip()
                        if name and name != "None":
                            companies.append(name)
            wb.close()
            return companies
        except Exception as e:
            self._append_log(f"读取文件失败 {path}: {e}")
            return []

    def _clear_files(self):
        """清空文件列表"""
        self._excel_paths = []
        self._companies = []
        self.file_path_lbl.setText("请选择 Excel 文件（.xlsx），支持多选")
        self.file_path_lbl.setStyleSheet("color: #666; font-size: 13px;")
        self.company_count_lbl.setText("共 0 家")
        self.preview_btn.setEnabled(False)
        self.clear_files_btn.setEnabled(False)
        self._check_ready()
        # 清空文件视为开启新会话：重置自动保存目录与每10条计数桶（下次开始采集会新建时间戳文件夹）
        self._session_folder = None
        self._last_autosave_bucket = 0
        self._append_log("已清空文件列表")

    def _load_companies(self, path: str):
        """（兼容旧入口）委托给 _load_companies_from_file 的智能识别逻辑"""
        companies = self._load_companies_from_file(path)
        self._companies = companies
        self.company_count_lbl.setText(f"共 {len(companies)} 家")
        self.preview_btn.setEnabled(True)
        self._check_ready()
        self._append_log(f"已加载 {len(companies)} 家公司")

    def _preview_companies(self):
        preview = "\n".join(self._companies[:20])
        if len(self._companies) > 20:
            preview += f"\n... 还有 {len(self._companies) - 20} 家"
        QMessageBox.information(self, f"公司列表预览（前20条）", preview)

    def _check_ready(self):
        ready = bool(self._companies)
        self.start_btn.setEnabled(ready)

    # ─ 登录 ───────────────────────────────────
    def _on_source_changed(self, index):
        """切换数据来源"""
        old_crawler = self.crawler
        old_index = self._current_source
        # 切换前先把旧平台数据落盘（不足 10 条未触发自动保存时避免丢失）
        if old_index != index:
            self._save_platform_file(old_index, "切换平台")
        self._current_source = index
        if index == 0:
            self.crawler = self.riskbird_crawler
            self._append_log("已切换到：风鸟 (riskbird.com)")
        elif index == 1:
            self.crawler = self.qizhidao_crawler
            self._append_log("已切换到：企知道 (qizhidao.com)")
        else:
            self.crawler = self.aiqicha_crawler
            self._append_log("已切换到：爱企查 (aiqicha.baidu.com)")
        
        # 切换时关闭旧爬虫的浏览器（释放 Chrome 配置目录锁）
        if old_crawler is not self.crawler and old_crawler.driver is not None:
            self._append_log("关闭上一个数据源的浏览器...")
            try:
                old_crawler.close_browser()
            except Exception:
                old_crawler.driver = None
        
        # 更新窗口标题
        self._update_window_title()
        # 刷新各平台已采集条数显示，并提示续采起点
        self._update_platform_counts()
        _names = ["风鸟", "企知道", "爱企查"]
        _cnt = len(self._all_crawlers[index].results)
        _g = self._get_global_index()
        self._append_log(f"{_names[index]} 平台已采集 {_cnt} 条；全局累计 {_g} 条，点开始采集将从第 {_g + 1} 条继续")
    
    def _update_window_title(self):
        """根据当前数据源更新窗口标题和界面标题"""
        site_names = ["风鸟", "企知道", "爱企查"]
        name = site_names[self._current_source] if self._current_source < len(site_names) else "未知"
        self.setWindowTitle(f"{name} 企业数据采集工具")
        # 同时更新界面内的大标题
        if hasattr(self, 'title_label'):
            self.title_label.setText(f"{name} 企业数据采集工具")
        
    def _show_login(self):
        dlg = LoginDialog(self.crawler, self)
        dlg.exec_()
    
    def _open_website(self):
        """打开当前选择的网站（后台线程启动浏览器，不阻塞 GUI）"""
        self.open_web_btn.setEnabled(False)
        self.open_web_btn.setText("加载中...")
        QApplication.processEvents()
        
        # 关闭其他爬虫的浏览器（避免 Chrome 配置目录锁定冲突）
        for c in [self.riskbird_crawler, self.qizhidao_crawler, self.aiqicha_crawler]:
            if c is not self.crawler and c.driver is not None:
                try:
                    c.close_browser()
                except Exception:
                    c.driver = None
        
        # 用后台线程启动浏览器，避免卡死 GUI
        self._open_thread = _OpenBrowserWorker(self.crawler)
        self._open_thread.finished.connect(self._on_open_finished)
        self._open_thread.log_signal.connect(self._append_log)
        self._open_thread.start()

    def _on_open_finished(self, success: bool, msg: str):
        """浏览器启动完成回调"""
        self.open_web_btn.setEnabled(True)
        self.open_web_btn.setText("打开网页")
        
        if success:
            # 导航到网站（CDP 爬虫不需要，用户已手动打开）
            from aiqicha_crawler_v2 import AiQiChaCrawlerV2
            if not isinstance(self.crawler, AiQiChaCrawlerV2):
                try:
                    # 关闭多余标签页并验证导航到平台首页（解决 hao123 劫持/多标签）
                    if hasattr(self.crawler, "_goto_homepage_verified"):
                        self.crawler._goto_homepage_verified()
                    else:
                        self.crawler.driver.get(self.crawler.BASE_URL)
                except Exception as e:
                    self._append_log(f"导航失败：{e}")
            
            site_names = ["riskbird.com", "qizhidao.com", "aiqicha.baidu.com"]
            site_name = site_names[self._current_source] if self._current_source < len(site_names) else "未知"
            self._append_log(f"已打开 {site_name} 网站")
        else:
            self._append_log(f"打开网站失败：{msg}")

    def _on_login_status(self, success: bool, msg: str):
        if success:
            self.login_status_lbl.setText("已登录")
            self.login_status_lbl.setStyleSheet(
                "QLabel { padding: 5px 12px; border-radius: 4px; background: #d4edda; color: #155724; }"
            )
        else:
            self.login_status_lbl.setText("未登录")
            self.login_status_lbl.setStyleSheet(
                "QLabel { padding: 5px 12px; border-radius: 4px; background: #f8d7da; color: #721c24; }"
            )

    def _on_manual_check(self, msg: str):
        self.statusBar().showMessage(msg, 5000)

    # ── 采集控制 ───────────────────────────────
    def _start_crawl(self):
        if not self._companies:
            QMessageBox.warning(self, "提示", "请先选择 Excel 文件")
            return

        # 关闭其他爬虫的浏览器（避免 Chrome 配置目录锁定冲突）
        for c in [self.riskbird_crawler, self.qizhidao_crawler, self.aiqicha_crawler]:
            if c is not self.crawler and c.driver is not None:
                try:
                    c.close_browser()
                except Exception:
                    c.driver = None

        # 设置延迟
        try:
            delay = float(self.delay_spin.text())
            self.crawler.delay_between = max(0.1, delay)
        except ValueError:
            self.crawler.delay_between = 0.5

        # 跨平台续采：从所有平台已采集的总条数继续（切换平台/暂停后不重头开始）
        start_index = self._get_global_index()
        if start_index >= len(self._companies):
            QMessageBox.information(self, "提示", f"全部 {len(self._companies)} 家公司已采集完成，无需继续。")
            return
        # 首次开始采集时创建桌面会话文件夹（以第一次开始采集的时间命名）
        self._get_or_create_session_folder()
        self._last_autosave_bucket = start_index // 10
        if start_index > 0:
            self._append_log(f"↻ 续采：已完成 {start_index} 条，从第 {start_index + 1} 条继续")

        self.start_btn.setEnabled(False)
        self.pause_btn.setEnabled(True)
        self.resume_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.select_file_btn.setEnabled(False)
        self.export_btn.setEnabled(True)  # 采集中也可以导出
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("采集中... 0%")

        # 开始计时
        self._start_timer()

        self._crawl_thread = CrawlWorker(self.crawler, self._companies, start_index)
        self._crawl_thread.finished.connect(self._on_crawl_finished)
        self._crawl_thread.start()

        self.statusBar().showMessage("采集中...")

    def _pause_crawl(self):
        self.crawler.pause_crawl()
        self.pause_btn.setEnabled(False)
        self.resume_btn.setEnabled(True)
        self.statusBar().showMessage("已暂停")
        self._pause_timer()
        self._update_platform_counts()
        self._auto_save("点击暂停")

    def _resume_crawl(self):
        self.crawler.resume_crawl()
        self.pause_btn.setEnabled(True)
        self.resume_btn.setEnabled(False)
        self.statusBar().showMessage("采集中...")
        self._resume_timer()

    def _stop_crawl(self):
        self.crawler.stop_crawl()
        self.stop_btn.setEnabled(False)
        self.pause_btn.setEnabled(False)
        self.resume_btn.setEnabled(False)
        self.start_btn.setEnabled(True)  # 允许重新开始
        self.export_btn.setEnabled(True)  # 允许导出
        self._stop_timer()
        self._update_platform_counts()
        # 输出用时摘要
        count = len(self.crawler.results)
        self._append_log(f" {self._get_time_summary(count)}")
        self._auto_save("点击停止")

    def _on_crawl_finished(self):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.select_file_btn.setEnabled(True)
        self.export_btn.setEnabled(bool(self._all_results()))
        self._stop_timer()
        count = len(self.crawler.results)
        total_all = self._get_global_index()
        self.statusBar().showMessage(f"采集完成 — 本平台 {count} 条，全部平台合计 {total_all} 条")
        self.progress_bar.setFormat(f"完成 — 合计 {total_all} 条数据")
        self._update_platform_counts()
        # 输出用时摘要
        self._append_log(f"✅ {self._get_time_summary(count)}")
        self._auto_save("采集完成")

    def _update_progress(self, current: int, total: int):
        pct = int(current / total * 100) if total else 0
        self.progress_bar.setValue(pct)
        self.progress_bar.setFormat(f"采集中... {current}/{total} ({pct}%)  ⏱ {self._format_time(self._crawl_elapsed)}")
        # 刷新各平台已采集条数显示
        self._update_platform_counts()
        # 每采集 10 条自动保存一次
        total_done = self._get_global_index()
        bucket = total_done // 10
        if bucket > self._last_autosave_bucket:
            self._last_autosave_bucket = bucket
            self._auto_save(f"每10条自动保存·已{total_done}条")

    # ── 计时器 ────────────────────────────────
    @staticmethod
    def _format_time(seconds: int) -> str:
        """格式化秒数为 HH:MM:SS"""
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h:02d}:{m:02d}:{s:02d}"

    def _update_timer(self):
        """每秒更新计时器显示"""
        if self._crawl_start_time:
            self._crawl_elapsed = int(time.time() - self._crawl_start_time)
        self.timer_lbl.setText(f" {self._format_time(self._crawl_elapsed)}")

    def _start_timer(self):
        """开始计时"""
        self._crawl_elapsed = 0
        self._crawl_start_time = time.time()
        self._crawl_timer.start(1000)  # 每秒更新
        self.timer_lbl.setText("⏱ 00:00:00")

    def _stop_timer(self):
        """停止计时"""
        self._crawl_timer.stop()
        if self._crawl_start_time:
            self._crawl_elapsed = int(time.time() - self._crawl_start_time)
        self.timer_lbl.setText(f"⏱ {self._format_time(self._crawl_elapsed)}")

    def _pause_timer(self):
        """暂停计时（保留已用时间）"""
        self._crawl_timer.stop()
        if self._crawl_start_time:
            self._crawl_elapsed = int(time.time() - self._crawl_start_time)
        self._crawl_start_time = None

    def _resume_timer(self):
        """恢复计时（在已用时间基础上继续）"""
        self._crawl_start_time = time.time() - self._crawl_elapsed
        self._crawl_timer.start(1000)

    def _get_time_summary(self, count: int) -> str:
        """生成用时摘要"""
        total_seconds = self._crawl_elapsed
        time_str = self._format_time(total_seconds)
        if count > 0:
            avg_seconds = total_seconds / count
            if avg_seconds < 60:
                avg_str = f"{avg_seconds:.1f}秒"
            else:
                avg_min = avg_seconds / 60
                avg_str = f"{avg_min:.1f}分钟"
            return f"总用时 {time_str}，共 {count} 条，平均 {avg_str}/条"
        return f"总用时 {time_str}"

    # ── 日志 ───────────────────────────────────
    def _append_log(self, msg: str):
        # 使用 insertPlainText + moveCursor 避免 QTextCursor 跨线程问题
        self.log_text.moveCursor(self.log_text.textCursor().End)
        self.log_text.insertPlainText(msg + "\n")
        # 自动滚动到底部
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        # 更新计数
        count = self.log_text.document().blockCount()
        self.log_count_lbl.setText(f"日志: {count} 条")

    # ── 跨平台进度 / 桌面会话文件夹 / 自动保存 ──
    def _all_results(self) -> list:
        """合并三个平台的全部采集结果（风鸟→企知道→爱企查 顺序）"""
        combined = []
        for c in self._all_crawlers:
            combined.extend(c.results)
        return combined

    def _get_global_index(self) -> int:
        """全局进度 = 所有平台已采集条数之和（跨平台续采的起点）"""
        return sum(len(c.results) for c in self._all_crawlers)

    def _get_desktop_dir(self) -> Path:
        """获取桌面路径（兼容 Windows OneDrive 重定向）"""
        desktop = Path.home() / "Desktop"
        if desktop.exists():
            return desktop
        for env in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
            od = os.environ.get(env)
            if od:
                d = Path(od) / "Desktop"
                if d.exists():
                    return d
        try:
            desktop.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return desktop

    def _get_or_create_session_folder(self):
        """本次采集会话的桌面子文件夹：桌面/企业数据自动采集工具/<第一次开始采集时间>"""
        if self._session_folder is not None and self._session_folder.exists():
            return self._session_folder
        try:
            base = self._get_desktop_dir() / "企业数据自动采集工具"
            base.mkdir(parents=True, exist_ok=True)
            # Windows 文件夹名不能含冒号，用 年-月-日_时-分-秒 安全格式
            stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            folder = base / stamp
            folder.mkdir(parents=True, exist_ok=True)
            self._session_folder = folder
            self._append_log(f"📁 自动保存目录：{folder}")
            return folder
        except Exception as e:
            self._append_log(f"⚠ 创建自动保存目录失败：{e}")
            return None

    def _update_platform_counts(self):
        """刷新界面上各平台已采集条数显示"""
        if not hasattr(self, "platform_count_lbl"):
            return
        counts = [len(c.results) for c in self._all_crawlers]
        total = sum(counts)
        self.platform_count_lbl.setText(
            f"风鸟: {counts[0]} 条    企知道: {counts[1]} 条    爱企查: {counts[2]} 条    合计: {total} 条"
        )

    def _save_platform_file(self, source_index: int, reason: str = ""):
        """将指定平台结果保存为 桌面会话文件夹/<平台名>.xlsx（同名覆盖，每平台最多 1 个文件）"""
        if not (0 <= source_index < len(self._all_crawlers)):
            return
        c = self._all_crawlers[source_index]
        if not c.results:
            return
        folder = self._get_or_create_session_folder()
        if folder is None:
            return
        name = ["风鸟", "企知道", "爱企查"][source_index]
        filepath = folder / f"{name}.xlsx"
        try:
            c.export_excel(str(filepath))
            reason_text = f"（{reason}）" if reason else ""
            self._append_log(f"✅ 已自动保存 {name} {len(c.results)} 条 → {filepath} {reason_text}")
        except Exception as e:
            self._append_log(f"⚠ {name} 自动保存失败：{e}")

    # ── 自动保存 ──────────────────────────────
    def _auto_save(self, reason: str = ""):
        """自动保存当前平台数据到桌面会话文件夹（覆盖该平台同名文件）"""
        try:
            self._save_platform_file(self._current_source, reason)
        except Exception as e:
            self._append_log(f"️ 自动保存失败：{e}")

    # ── 导出（合并风鸟/企知道/爱企查全部数据 + 生成清洗后入库数据） ──
    def _export_excel(self):
        combined = self._all_results()
        if not combined:
            QMessageBox.warning(self, "提示", "没有数据可导出")
            return

        default_name = f"企业数据_全部平台_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 Excel（合并全部平台数据）", default_name,
            "Excel 文件 (*.xlsx)"
        )
        if not path:
            return
        try:
            self._write_merged_excel(combined, path)
            counts = [len(c.results) for c in self._all_crawlers]
            cleaned_path = self._export_cleaned_library(combined, path)
            msg = (
                f"已合并导出 {len(combined)} 条数据到:\n{path}\n\n"
                f"风鸟 {counts[0]} 条 / 企知道 {counts[1]} 条 / 爱企查 {counts[2]} 条"
            )
            if cleaned_path:
                msg += f"\n\n已另存清洗后入库数据到:\n{cleaned_path}"
            QMessageBox.information(self, "导出成功", msg)
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def _export_cleaned_library(self, combined: list, merged_path: str) -> str:
        """按《客户库数据导入模板》生成清洗后入库数据，写到合并文件同目录。"""
        if not HAS_CLEANER:
            self._append_log("⚠ 清洗模块未加载，跳过入库数据生成")
            return ""
        cleaner = _cleaner
        try:
            self._append_log("🧹 开始生成清洗后客户库入库数据…")
            rows = cleaner.build_clean_rows(combined, log=self._append_log)
            if not rows:
                self._append_log("⚠ 清洗后无有效入库记录（均为未找到/失败/空），已跳过")
                return ""
            template = cleaner.locate_template(
                start_dirs=[os.path.dirname(merged_path), self._get_last_import_dir()]
            )
            merged_dir = os.path.dirname(merged_path) or "."
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            cleaned_path = os.path.join(merged_dir, f"清洗后客户库入库数据_{stamp}.xlsx")
            cleaner.write_clean_excel(rows, cleaned_path, template_path=template,
                                       log=self._append_log)
            return cleaned_path
        except Exception as e:
            self._append_log(f"⚠ 生成清洗后入库数据失败：{e}")
            return ""

    def _get_last_import_dir(self) -> str:
        """返回最近一次选择文件所在目录，用于就近查找入库模板。"""
        files = getattr(self, "_excel_paths", None) or []
        return os.path.dirname(files[-1]) if files else ""

    def _write_merged_excel(self, results: list, filepath: str):
        """把多平台结果合并写入一个 Excel（统一表头 + 样式，含"数据来源"列区分平台）"""
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

        # 统一表头：常用字段优先，其余按首次出现顺序补充
        preferred = [
            "公司名", "状态", "数据来源", "法定代表人", "注册资本", "成立日期",
            "统一社会信用代码", "电话", "邮箱", "官网", "地址", "简介",
            "经营范围", "经营业务范围", "所属行业", "企业类型", "营业期限",
            "登记机关", "核准日期", "参保人数", "搜索结果URL",
        ]
        keys = []
        for r in results:
            for k in r.keys():
                if k not in keys:
                    keys.append(k)
        headers = [k for k in preferred if k in keys] + [k for k in keys if k not in preferred]

        wb = Workbook()
        ws = wb.active
        ws.title = "企业数据(全部平台)"

        header_font = Font(bold=True, size=11, color="FFFFFF")
        header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
        thin = Border(left=Side(style="thin"), right=Side(style="thin"),
                      top=Side(style="thin"), bottom=Side(style="thin"))

        cell = ws.cell(row=1, column=1, value="序号")
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align
        cell.border = thin
        for col, h in enumerate(headers, 2):
            cell = ws.cell(row=1, column=col, value=h)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = header_align
            cell.border = thin

        data_align = Alignment(vertical="top", wrap_text=True)
        center_align = Alignment(horizontal="center")
        for row_idx, r in enumerate(results, 2):
            ws.cell(row=row_idx, column=1, value=row_idx - 1).alignment = center_align
            for col_idx, h in enumerate(headers, 2):
                cell = ws.cell(row=row_idx, column=col_idx, value=r.get(h, ""))
                cell.alignment = data_align
                cell.border = thin

        for col in ws.columns:
            max_len = 0
            letter = col[0].column_letter
            for c in col:
                if c.value:
                    max_len = max(max_len, len(str(c.value)))
            ws.column_dimensions[letter].width = min(max_len + 4, 50)

        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        wb.save(filepath)
        self._append_log(f"✅ 已合并导出 {len(results)} 条到 {filepath}")


# ═══════════════════════════════════════════════
#  打开浏览器工作线程
# ═══════════════════════════════════════════════
class _OpenBrowserWorker(QThread):
    """后台启动浏览器，不阻塞 GUI"""
    finished = pyqtSignal(bool, str)  # success, message
    log_signal = pyqtSignal(str)

    def __init__(self, crawler):
        super().__init__()
        self.crawler = crawler

    def run(self):
        try:
            # 检查会话是否存活
            if self.crawler.driver is None or not self.crawler._is_session_alive():
                self.log_signal.emit("浏览器会话已断开，正在重启...")
                try:
                    if self.crawler.driver:
                        try:
                            self.crawler.driver.quit()
                        except Exception:
                            pass
                        self.crawler.driver = None
                except Exception:
                    self.crawler.driver = None

                success = self.crawler.start_browser(headed=True)
                if not success:
                    self.finished.emit(False, "浏览器启动失败")
                    return

            self.finished.emit(True, "浏览器已启动")
        except Exception as e:
            self.finished.emit(False, str(e))


# ═══════════════════════════════════════════════
#  采集工作线程
# ═══════════════════════════════════════════════
class CrawlWorker(QThread):
    finished = pyqtSignal()

    def __init__(self, crawler, companies: list, start_index: int = 0):
        super().__init__()
        self.crawler = crawler
        self.companies = companies
        self.start_index = start_index

    def run(self):
        self.crawler.crawl(self.companies, self.start_index)
        self.finished.emit()


# ═══════════════════════════════════════════════
#  启动
# ═══════════════════════════════════════════════
def main():
    # 全局异常处理，防止闪退
    def global_excepthook(exctype, value, tb):
        import traceback
        err_msg = "".join(traceback.format_exception(exctype, value, tb))
        # 写入日志文件方便排查
        try:
            with open("crash_log.txt", "a", encoding="utf-8") as f:
                f.write(f"\n{'='*50}\n")
                f.write(f"Crash at: {datetime.now().isoformat()}\n")
                f.write(err_msg)
        except Exception:
            pass

    sys.excepthook = global_excepthook

    # PyInstaller --onefile 启动保护
    # 1. 清理旧进程和临时目录（防止强制关闭后无法启动）
    if getattr(sys, 'frozen', False):
        import shutil
        import subprocess

        # 1.1 杀掉同名的旧进程（防止端口/文件锁定）
        try:
            current_pid = os.getpid()
            if sys.platform == "win32":
                subprocess.run(
                    [r"C:\Windows\System32\tasklist.exe", "/FI", "IMAGENAME eq RiskBirdTool.exe", "/FO", "CSV", "/NH"],
                    capture_output=True, text=True, timeout=5
                )
                # 用 taskkill 杀掉其他实例（保留当前进程）
                subprocess.run(
                    [r"C:\Windows\System32\taskkill.exe", "/F", "/FI", "IMAGENAME eq RiskBirdTool.exe", "/FI", f"PID ne {current_pid}"],
                    capture_output=True, timeout=5
                )
        except Exception:
            pass

        # 1.2 清理旧的临时目录（排除当前进程使用的）
        try:
            current_mei = getattr(sys, '_MEIPASS', '')
            temp_dir = os.environ.get("TEMP", r"C:\temp")
            for d in os.listdir(temp_dir):
                if d.startswith("_MEI"):
                    full_path = os.path.join(temp_dir, d)
                    # 跳过当前进程正在使用的目录
                    if os.path.abspath(full_path) == os.path.abspath(current_mei):
                        continue
                    try:
                        shutil.rmtree(full_path, ignore_errors=True)
                    except Exception:
                        # 如果删除失败，尝试重命名（Windows 可能锁定）
                        try:
                            old_name = full_path + "_old"
                            if os.path.exists(old_name):
                                shutil.rmtree(old_name, ignore_errors=True)
                            os.rename(full_path, old_name)
                        except Exception:
                            pass
        except Exception:
            pass

    # 2. 写入运行标记（正常退出时删除）
    if getattr(sys, 'frozen', False):
        crash_marker = os.path.join(os.path.dirname(sys.executable), ".crashed")
        with open(crash_marker, "w") as f:
            f.write("running")

    # 3. 注册退出时清理
    import atexit
    def _on_exit():
        try:
            if getattr(sys, 'frozen', False):
                marker = os.path.join(os.path.dirname(sys.executable), ".crashed")
                if os.path.exists(marker):
                    os.remove(marker)
        except Exception:
            pass
    atexit.register(_on_exit)

    # 高 DPI 支持
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # 全局字体
    font_family = "Microsoft YaHei" if sys.platform == "win32" else "Arial"
    font = QFont(font_family, 10)
    app.setFont(font)

    # 启动画面
    splash_text = QLabel("正在启动...\n请稍候")
    splash_text.setFont(QFont(font_family, 14, QFont.Bold))
    splash_text.setStyleSheet(
        "QLabel { background: #1a3c6e; color: white; padding: 30px; border-radius: 8px; }"
    )
    splash_text.setAlignment(Qt.AlignCenter)
    splash_text.setWindowFlags(Qt.SplashScreen | Qt.FramelessWindowHint)
    splash_text.show()
    QApplication.processEvents()

    try:
        window = MainWindow()

        # 检测 Chrome 是否已安装
        if not is_chrome_installed():
            reply = QMessageBox.warning(
                window, "未检测到 Chrome",
                "本工具需要 Google Chrome 浏览器才能运行。\n\n"
                "是否现在自动下载安装 Chrome？\n"
                "（约 1 分钟，需要联网）",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )
            if reply == QMessageBox.Yes:
                window._append_log("正在自动下载安装 Chrome...")
                window.setEnabled(False)

                def _install_chrome():
                    ok = download_and_install_chrome(callback=window._append_log)
                    window.setEnabled(True)
                    if ok:
                        window._append_log("Chrome 安装完成，可以继续使用了")
                    else:
                        window._append_log("Chrome 安装失败，请手动下载安装:")
                        window._append_log("https://www.google.com/chrome/")

                threading.Thread(target=_install_chrome, daemon=True).start()
            else:
                window._append_log("提示: 请手动安装 Chrome 后重启程序")
                window._append_log("下载地址: https://www.google.com/chrome/")

        # 关闭启动画面
        splash_text.close()
        window.show()

        # 尝试自动恢复登录（安全包装）
        def try_restore():
            try:
                if window.crawler.try_restore_session():
                    window._on_login_status(True, "Cookie 恢复成功")
                else:
                    window._append_log("提示: 未找到有效登录信息，请点击'登录'按钮")
            except Exception as e:
                window._append_log(f"登录恢复异常: {e}")
                window._append_log("请点击'登录'按钮重新登录")

        threading.Thread(target=try_restore, daemon=True).start()

    except Exception as e:
        splash_text.close()
        QMessageBox.critical(None, "启动失败", f"程序启动失败:\n{e}\n\n请重试或联系开发者。")
        sys.exit(1)

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

