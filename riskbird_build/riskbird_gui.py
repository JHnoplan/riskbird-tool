"""
riskbird_gui.py - 美亚风鸟企业数据采集工具 GUI
使用 PyQt5 构建，支持 Excel 上传、登录管理、批量采集、日志输出、Excel 导出
"""

import sys
import os
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
        self._crawl_thread = None
        self._current_source = 1  # 0=风鸟，1=企知道，2=爱企查

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

        # ── 进度条 ──
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimumHeight(28)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("就绪")
        main_layout.addWidget(self.progress_bar)

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
        """从单个文件加载公司名"""
        try:
            from openpyxl import load_workbook
            wb = load_workbook(path, read_only=True)
            ws = wb.active
            companies = []
            for row in ws.iter_rows(min_row=2, values_only=True):
                if row and row[0]:
                    name = str(row[0]).strip()
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
        self._append_log("已清空文件列表")

    def _load_companies(self, path: str):
        try:
            from openpyxl import load_workbook
            wb = load_workbook(path, read_only=True)
            ws = wb.active
            companies = []
            for row in ws.iter_rows(min_row=2, values_only=True):
                if row and row[0]:
                    name = str(row[0]).strip()
                    if name and name != "None":
                        companies.append(name)
            wb.close()
            self._companies = companies
            self.company_count_lbl.setText(f"共 {len(companies)} 家")
            self.preview_btn.setEnabled(True)
            self._check_ready()
            self._append_log(f"已加载 {len(companies)} 家公司")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"读取 Excel 失败:\n{e}")

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
        """打开当前选择的网站"""
        try:
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
                
            # 检查会话是否存活
            if self.crawler.driver is None or not self.crawler._is_session_alive():
                self._append_log("浏览器会话已断开，正在重启...")
                try:
                    if self.crawler.driver:
                        try:
                            self.crawler.driver.quit()
                        except Exception:
                            pass
                        self.crawler.driver = None
                except Exception:
                    self.crawler.driver = None
                self.crawler.start_browser(headed=True)
            
            # 导航到网站（CDP 爬虫不需要，用户已手动打开）
            from aiqicha_crawler_v2 import AiQiChaCrawlerV2
            if not isinstance(self.crawler, AiQiChaCrawlerV2):
                self.crawler.driver.get(self.crawler.BASE_URL)
            
            site_names = ["riskbird.com", "qizhidao.com", "aiqicha.baidu.com"]
            site_name = site_names[self._current_source] if self._current_source < len(site_names) else "未知"
            self._append_log(f"已打开 {site_name} 网站")
        except Exception as e:
            self._append_log(f"打开网站失败：{e}")
            # 尝试重启浏览器
            try:
                self._append_log("尝试重启浏览器...")
                if self.crawler.driver:
                    try:
                        self.crawler.driver.quit()
                    except Exception:
                        pass
                    self.crawler.driver = None
                self.crawler.start_browser(headed=True)
                # CDP 爬虫不需要导航
                from aiqicha_crawler_v2 import AiQiChaCrawlerV2
                if not isinstance(self.crawler, AiQiChaCrawlerV2):
                    self.crawler.driver.get(self.crawler.BASE_URL)
                self._append_log("重启成功，已打开网站")
            except Exception as e2:
                self._append_log(f"重启也失败：{e2}")
        finally:
            self.open_web_btn.setEnabled(True)
            self.open_web_btn.setText("打开网页")

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

        # 如果是恢复采集，从上次停止的位置继续
        start_index = len(self.crawler.results) if self.crawler.results else 0

        self.start_btn.setEnabled(False)
        self.pause_btn.setEnabled(True)
        self.resume_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.select_file_btn.setEnabled(False)
        self.export_btn.setEnabled(True)  # 采集中也可以导出
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("采集中... 0%")

        self._crawl_thread = CrawlWorker(self.crawler, self._companies, start_index)
        self._crawl_thread.finished.connect(self._on_crawl_finished)
        self._crawl_thread.start()

        self.statusBar().showMessage("采集中...")

    def _pause_crawl(self):
        self.crawler.pause_crawl()
        self.pause_btn.setEnabled(False)
        self.resume_btn.setEnabled(True)
        self.statusBar().showMessage("已暂停")

    def _resume_crawl(self):
        self.crawler.resume_crawl()
        self.pause_btn.setEnabled(True)
        self.resume_btn.setEnabled(False)
        self.statusBar().showMessage("采集中...")

    def _stop_crawl(self):
        self.crawler.stop_crawl()
        self.stop_btn.setEnabled(False)
        self.pause_btn.setEnabled(False)
        self.resume_btn.setEnabled(False)
        self.start_btn.setEnabled(True)  # 允许重新开始
        self.export_btn.setEnabled(True)  # 允许导出

    def _on_crawl_finished(self):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.select_file_btn.setEnabled(True)
        self.export_btn.setEnabled(bool(self.crawler.results))
        self.statusBar().showMessage(f"采集完成 — 共 {len(self.crawler.results)} 条数据")
        self.progress_bar.setFormat(f"完成 — {len(self.crawler.results)} 条数据")

    def _update_progress(self, current: int, total: int):
        pct = int(current / total * 100) if total else 0
        self.progress_bar.setValue(pct)
        self.progress_bar.setFormat(f"采集中... {current}/{total} ({pct}%)")

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

    # ── 导出 ──────────────────────────────────
    def _export_excel(self):
        if not self.crawler.results:
            QMessageBox.warning(self, "提示", "没有数据可导出")
            return

        default_name = f"美亚风鸟_企业数据_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 Excel", default_name,
            "Excel 文件 (*.xlsx)"
        )
        if path:
            self.crawler.export_excel(path)
            QMessageBox.information(self, "导出成功", f"数据已保存到:\n{path}")


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

    # PyInstaller --onefile 首次运行解压保护
    # 清理上次崩溃残留的临时文件
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
        # 检查是否有崩溃标记
        crash_marker = os.path.join(os.path.dirname(sys.executable), ".crashed")
        if os.path.exists(crash_marker):
            # 上次非正常退出，清理临时文件
            try:
                import shutil
                temp_base = os.path.join(os.environ.get("TEMP", "/tmp"), "_MEI")
                for d in os.listdir(os.environ.get("TEMP", "/tmp")):
                    if d.startswith("_MEI"):
                        full = os.path.join(os.environ.get("TEMP", "/tmp"), d)
                        try:
                            shutil.rmtree(full, ignore_errors=True)
                        except Exception:
                            pass
            except Exception:
                pass
            try:
                os.remove(crash_marker)
            except Exception:
                pass

    # 写入运行标记（正常退出时删除）
    if getattr(sys, 'frozen', False):
        crash_marker = os.path.join(os.path.dirname(sys.executable), ".crashed")
        with open(crash_marker, "w") as f:
            f.write("running")

    # 注册退出时清理
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
