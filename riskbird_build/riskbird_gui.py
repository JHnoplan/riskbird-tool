"""
riskbird_gui.py - 公司名查企业数据工具 GUI（风鸟 / 企知道 / 爱企查 三平台采集 + 云展动力客户库导入）
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
    QListWidget, QListWidgetItem, QStackedWidget, QButtonGroup, QInputDialog,
    QAbstractItemView,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont, QIcon, QColor, QPalette

from riskbird_crawler_v2 import RiskBirdCrawlerV2, is_chrome_installed, download_and_install_chrome
from qizhidao_crawler import QiZhiDaoCrawler
from aiqicha_crawler_v2 import AiQiChaCrawlerV2
import block_guard   # 续采比对公司名要用同一套归一化规则，不能各写一份

try:
    import dual_session as _dual
    HAS_DUAL = True
except ImportError:
    HAS_DUAL = False

try:
    # 断点续采进度台账（名单快照 + 条目状态机）：读不到不影响采集，只是不能续采
    import crawl_checkpoint as _cp
    HAS_CHECKPOINT = True
except Exception:
    _cp = None
    HAS_CHECKPOINT = False

try:
    import customer_library_cleaner as _cleaner
    HAS_CLEANER = True
except ImportError:
    HAS_CLEANER = False

try:
    # 云展动力客户库对接（接口 3/4/5/6）+ 采集任务索引/绑定账号持久化
    import yzd_client as _yzd
    import task_registry as _tasks
    HAS_YZD = True
except ImportError:
    HAS_YZD = False

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


# ══════════════════════════════════════════
#  自动保存的两份固定文件（与“导出 Excel”同效果，不分平台）
# ══════════════════════════════════════════
RAW_FILE_NAME = "企业数据_全部平台.xlsx"
CLEAN_FILE_NAME = "清洗后客户库入库数据.xlsx"

# 统一表头：常用字段优先，其余按首次出现顺序补充
_PREFERRED_HEADERS = [
    "公司名", "状态", "数据来源", "法定代表人", "注册资本", "成立日期",
    "统一社会信用代码", "电话", "邮箱", "官网", "地址", "简介",
    "经营范围", "经营业务范围", "所属行业", "企业类型", "营业期限",
    "登记机关", "核准日期", "参保人数", "搜索结果URL",
]


def _quiet_log(msg):
    """没有日志回调时吐到 stdout（打包后无窗口也无影响），不报错中断写盘"""
    print(msg)


def _drop_tmp(path):
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def _atomic_save_workbook(wb, filepath):
    """先写同目录临时文件再原子替换。

    自动保存是反复覆盖同一个文件：直接写目标文件时，一旦写一半时进程被杀或磁盘满，
    上一份好文件就变成半个坏文件。换成 tmp + os.replace 后，要么新要么旧，不会坏。
    文件被 Excel/WPS 占用时 os.replace 报 PermissionError，由调用方提示。
    """
    tmp = f"{filepath}.tmp"
    _drop_tmp(tmp)
    try:
        wb.save(tmp)
        os.replace(tmp, filepath)
    finally:
        _drop_tmp(tmp)


def collect_headers(results: list) -> list:
    """合并多平台字段：各平台字段不一样，按首次出现顺序补齐"""
    keys = []
    for r in results:
        for k in r.keys():
            if k not in keys:
                keys.append(k)
    return ([k for k in _PREFERRED_HEADERS if k in keys]
            + [k for k in keys if k not in _PREFERRED_HEADERS])


def write_merged_excel(results: list, filepath: str, log=None) -> int:
    """把三个平台的全部结果合并写成一个原数据 Excel（统一表头 + 样式，含“数据来源”列）"""
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

    log = log or _quiet_log
    headers = collect_headers(results)

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
    _atomic_save_workbook(wb, filepath)
    log(f"✅ 原数据文件已保存 {len(results)} 条 → {filepath}")
    return len(results)


def write_cleaned_excel(results: list, filepath: str, template_dirs=None, log=None) -> int:
    """按《客户库数据导入模板》写清洗后入库数据，返回写入行数（0=本次没写）"""
    log = log or _quiet_log
    if not HAS_CLEANER:
        log("⚠ 清洗模块未加载，跳过入库数据生成")
        return 0
    rows = _cleaner.build_clean_rows(results, log=log)
    if not rows:
        log("⚠ 清洗后无有效入库记录（均为未找到/失败/空），本次未更新入库文件")
        return 0
    template = _cleaner.locate_template(start_dirs=[d for d in (template_dirs or []) if d])
    tmp = f"{filepath}.tmp"
    _drop_tmp(tmp)
    try:
        _cleaner.write_clean_excel(rows, tmp, template_path=template, log=log)
        os.replace(tmp, filepath)
    finally:
        _drop_tmp(tmp)
    log(f"✅ 清洗后入库文件已保存 {len(rows)} 条 → {filepath}")
    return len(rows)


class AutoSaveWorker(QThread):
    """自动保存放后台线程写盘。

    实测 480 条数据「合并 + 清洗 + 两份写盘」约 3.6 秒，放在 GUI 线程会让界面
    每几十秒僵一下。写盘用的是一份 dict 快照（进线程前就拷好），之后不再碰爬虫对象。
    """
    logged = pyqtSignal(str)
    finished_save = pyqtSignal(bool, str)   # ok, 结果摘要

    def __init__(self, combined: list, folder: str, template_dirs=None):
        super().__init__()
        self.combined = combined
        self.folder = folder
        self.template_dirs = template_dirs or []

    def run(self):
        raw_path = os.path.join(self.folder, RAW_FILE_NAME)
        try:
            raw_n = write_merged_excel(self.combined, raw_path, log=self.logged.emit)
        except PermissionError:
            self.finished_save.emit(False, f"{RAW_FILE_NAME} 正被 Excel/WPS 占用，本次自动保存已跳过（数据仍在内存，关掉文件后下一轮会补上）")
            return
        except Exception as e:
            self.finished_save.emit(False, f"自动保存原数据失败：{e}")
            return

        clean_path = os.path.join(self.folder, CLEAN_FILE_NAME)
        clean_n = 0
        note = ""
        try:
            clean_n = write_cleaned_excel(self.combined, clean_path,
                                          template_dirs=self.template_dirs,
                                          log=self.logged.emit)
        except PermissionError:
            note = f"（{CLEAN_FILE_NAME} 被占用，本轮未更新）"
        except Exception as e:
            note = f"（清洗文件本轮未更新：{e}）"
        self.finished_save.emit(True, f"已自动保存：原数据 {raw_n} 条 / 清洗后入库 {clean_n} 条{note}")


# ══════════════════════════════════════════════
#  外部数据智能清洗（「清洗数据」按钮：别的渠道拿到的名单 → 入库格式）
# ══════════════════════════════════════════════
class ExternalCleanWorker(QThread):
    """读外部表格 → 智能匹配列名 → 清洗 → 写出《清洗后客户库入库数据》。

    多个文件全量读写可能几十秒，放 GUI 线程会直接白屏，所以跟自动保存一样走后台线程。
    单个文件读不了（旧 .xls / 空表）只记一条错误接着处理剩下的，不会一坏就全盘停。
    """
    logged = pyqtSignal(str)
    finished_clean = pyqtSignal(bool, str)     # ok, 给用户看的摘要

    def __init__(self, paths, out_path, template_dirs=None):
        super().__init__()
        self.paths = list(paths or [])
        self.out_path = out_path
        self.template_dirs = [d for d in (template_dirs or []) if d]

    def run(self):
        try:
            self._run_all()
        except Exception as e:
            self.finished_clean.emit(False, f"清洗异常：{e}")

    def _run_all(self):
        if not HAS_CLEANER:
            self.finished_clean.emit(False, "清洗模块未加载，无法清洗数据")
            return
        records, report = _cleaner.clean_external_records(self.paths, log=self.logged.emit)
        bad = [r for r in report if r.get("error")]
        if not records:
            detail = "\n".join(f"· {r['file']}：{r['error']}" for r in bad) or "· 所选文件里没有数据行"
            self.finished_clean.emit(False,
                                    f"没读到任何数据：\n{detail}\n\n"
                                    f"请确认文件里有公司名（或企业名称）列。")
            return
        rows = _cleaner.build_external_rows(records, log=self.logged.emit)
        if HAS_YZD:
            # 与导入云展同一口径的批内去重，保证“清洗结果”和“真传上去的行数”对得上
            rows = _yzd.dedupe_rows(rows, log=self.logged.emit)
        if not rows:
            self.finished_clean.emit(False, "清洗后没有可入库的记录（公司名全为空或状态异常）")
            return

        template = _cleaner.locate_template(start_dirs=self.template_dirs)
        tmp = f"{self.out_path}.tmp"
        _drop_tmp(tmp)
        try:
            _cleaner.write_clean_excel(rows, tmp, template_path=template, log=self.logged.emit)
            os.replace(tmp, self.out_path)
        except PermissionError:
            _drop_tmp(tmp)
            self.finished_clean.emit(False,
                                     f"目标文件正被 Excel/WPS 打开，写不进去：\n{self.out_path}\n"
                                     f"请先关掉这个文件再重做一次清洗。")
            return
        except Exception as e:
            _drop_tmp(tmp)
            self.finished_clean.emit(False, f"写出清洗结果失败：{e}")
            return
        _drop_tmp(tmp)

        files = "\n".join(
            f"· {r['file']}（{r['sheet']}）：{r['rows']} 条" for r in report if not r.get("error"))
        skipped = "\n".join(f"⚠ {r['file']}：{r['error']}" for r in bad)
        matched = "；".join(
            "，".join(r.get("identified") or []) for r in report if r.get("identified"))
        summary = (f"共清洗 {len(rows)} 条入库数据 →\n{self.out_path}\n\n"
                   f"读入的文件：\n{files or '· 无'}")
        if skipped:
            summary += f"\n\n被跳过：\n{skipped}"
        self.logged.emit(f"🧾 列匹配结果：{matched}" if matched else "🧾 本次没用到列名识别结果")
        self.finished_clean.emit(True, summary)


# ══════════════════════════════════════════════
#  云展动力：账号绑定 / 客户库分组 / 导入
# ═══════════════════════════════════════════════
YZD_IMPORT_CHUNK = 5000   # 接口6 建议单次 ≤5000 行，超出自动切分成多次上传


def _yzd_font(size=10, bold=False):
    return QFont("Microsoft YaHei" if sys.platform == "win32" else "Arial", size, QFont.Bold if bold else QFont.Normal)


class YzdBindWorker(QThread):
    """接口3：校验手机号格式与账号是否存在。

    必须放线程里跑：这是真实网络请求，写在槽函数里会让界面白屏几秒，
    后端慢的时候用户只会以为程序挂了。
    """
    finished_bind = pyqtSignal(bool, str)   # ok, 成功=手机号 / 失败=给用户看的提示

    def __init__(self, phone):
        super().__init__()
        self.phone = phone

    def run(self):
        try:
            _yzd.YzdClient().check_phone(self.phone)
            self.finished_bind.emit(True, self.phone)
        except _yzd.YzdError as e:
            self.finished_bind.emit(False, str(e))
        except Exception as e:
            self.finished_bind.emit(False, f"校验手机号异常：{e}")


class YzdGroupsWorker(QThread):
    """接口4：拉取分组列表（foreign_flag 0=国内 1=国外）"""
    finished_groups = pyqtSignal(int, list, str)   # flag, groups, error

    def __init__(self, phone, foreign_flag, keyword=""):
        super().__init__()
        self.phone = phone
        self.foreign_flag = foreign_flag
        self.keyword = keyword

    def run(self):
        try:
            groups = _yzd.YzdClient(self.phone).list_groups(
                foreign_flag=self.foreign_flag, group_name=self.keyword)
            self.finished_groups.emit(self.foreign_flag, groups, "")
        except Exception as e:
            self.finished_groups.emit(self.foreign_flag, [], str(e))


class YzdCreateGroupWorker(QThread):
    """接口5：新建分组（同名后端会自动追加序号后缀，这里不自造）"""
    finished_create = pyqtSignal(dict, str)   # group, error

    def __init__(self, phone, group_name, foreign_flag):
        super().__init__()
        self.phone = phone
        self.group_name = group_name
        self.foreign_flag = foreign_flag

    def run(self):
        try:
            group = _yzd.YzdClient(self.phone).create_group(
                self.group_name, foreign_flag=self.foreign_flag)
            self.finished_create.emit(group, "")
        except Exception as e:
            self.finished_create.emit({}, str(e))


class YzdImportWorker(QThread):
    """接口6：把若干采集任务逐个清洗成 9 列 Excel 后导入指定分组。

    逐任务上传（用户确认的口径）：每个任务单独一组分片调用，进度里能看到
    「正在导入 2/3」，某个任务失败也只影响它自己，不会连累已上传成功的。
    """
    logged = pyqtSignal(str)
    progress = pyqtSignal(int, int, str)                    # done, total, 阶段文案
    task_done = pyqtSignal(str, int, int, int, str)         # 任务名, importTotal, saveTotal, failTotal, error
    finished_all = pyqtSignal(list, str)                    # 逐任务结果, 致命错误

    def __init__(self, phone, jobs, group_id, foreign_flag, tmp_dir):
        super().__init__()
        self.phone = phone
        self.jobs = jobs            # [{"name":…, "records": list|None, "raw_file":…}]
        self.group_id = group_id
        self.foreign_flag = foreign_flag
        self.tmp_dir = tmp_dir

    def _records(self, job):
        """当前会话优先用内存快照，历史任务回读会话目录里的原数据文件"""
        if job.get("records") is not None:
            return job["records"]
        return _tasks.read_records_from_xlsx(job.get("raw_file", ""))

    def run(self):
        """整个流程包一层兜底：中途抛到外面的异常会把线程直接结束，
        而界面还在等 finished_all 解锁，用户看到的就是一个关不掉的加载框。"""
        try:
            self._run_all()
        except Exception as e:
            self.finished_all.emit([], f"导入线程异常：{e}")

    def _run_all(self):
        total = len(self.jobs)
        results = []
        client = _yzd.YzdClient(self.phone, log=self.logged.emit)
        for idx, job in enumerate(self.jobs, 1):
            name = job.get("name") or f"任务{idx}"
            self.progress.emit(idx - 1, total, f"正在导入 {idx}/{total}：{name}")
            try:
                records = self._records(job)
            except Exception as e:
                self.logged.emit(f"⚠ 读取「{name}」数据失败：{e}")
                results.append((name, 0, 0, 0, f"读取数据失败：{e}"))
                self.task_done.emit(name, 0, 0, 0, f"读取数据失败：{e}")
                continue
            if not records:
                msg = "该任务没有原数据"
                self.logged.emit(f"⚠ 「{name}」跳过：{msg}")
                results.append((name, 0, 0, 0, msg))
                self.task_done.emit(name, 0, 0, 0, msg)
                continue
            try:
                if not HAS_CLEANER:
                    raise RuntimeError("清洗模块未加载")
                rows = _cleaner.build_clean_rows(records, log=self.logged.emit)
                # 批内去重：接口6 自己不做重复校验，同一批里完全一样的行只传一次
                rows = _yzd.dedupe_rows(rows, log=self.logged.emit)
            except Exception as e:
                msg = f"清洗失败：{e}"
                self.logged.emit(f"⚠ 「{name}」{msg}")
                results.append((name, 0, 0, 0, msg))
                self.task_done.emit(name, 0, 0, 0, msg)
                continue
            if not rows:
                msg = "清洗后没有可导入的记录"
                self.logged.emit(f"⚠ 「{name}」跳过：{msg}")
                results.append((name, 0, 0, 0, msg))
                self.task_done.emit(name, 0, 0, 0, msg)
                continue

            chunks = [rows[i:i + YZD_IMPORT_CHUNK] for i in range(0, len(rows), YZD_IMPORT_CHUNK)] or [[]]
            imp = sav = fail = 0
            err = ""
            for c_idx, chunk in enumerate(chunks, 1):
                tag = f"{name}（{c_idx}/{len(chunks)}）" if len(chunks) > 1 else name
                self.progress.emit(idx - 1, total,
                                   f"正在导入 {idx}/{total}：{tag} · {len(chunk)} 条")
                file_path = os.path.join(self.tmp_dir, f"yzd_import_{idx}_{c_idx}.xlsx")
                try:
                    written = _yzd.write_import_workbook(chunk, file_path, log=self.logged.emit)
                    if written == 0:
                        raise RuntimeError("清洗结果全为空行，没有可上传的数据")
                    self.logged.emit(f"⬆ {tag}：已生成上传文件 {written} 行，正在提交云展…")
                    res = client.import_excel(file_path, group_id=self.group_id,
                                              foreign_flag=self.foreign_flag)
                    imp += res["importTotal"]
                    sav += res["saveTotal"]
                    fail += res["failTotal"]
                    self.logged.emit(f"  ↳ 云展返回：导入 {res['importTotal']} / 成功 {res['saveTotal']} / 失败 {res['failTotal']}")
                except Exception as e:
                    err = str(e)
                    self.logged.emit(f"❌ {tag} 导入失败：{err}")
                    break
                finally:
                    try:
                        if os.path.exists(file_path):
                            os.remove(file_path)
                    except Exception:
                        pass
            results.append((name, imp, sav, fail, err))
            self.task_done.emit(name, imp, sav, fail, err)
        self.progress.emit(total, total, "导入结束")
        self.finished_all.emit(results, "")


class YzdProgressDialog(QDialog):
    """导入期间的模态遮罩：锁住主界面 + busy 进度条动画。

    总耗时取决于任务条数和后端处理速度，事先给百分比只会是假数字，
    所以用不确定进度条（一直跑动）+ 文案显示当前任务，比假百分比诚实。
    没做完时不给关掉：中途关窗口只会让用户以为导入停了（其实线程还在跑）。
    """

    def __init__(self, parent, title="正在导入云展客户库"):
        super().__init__(parent)
        self._allow_close = False
        self.setWindowTitle(title)
        self.setModal(True)
        self.setWindowFlags(Qt.Dialog | Qt.CustomizeWindowHint | Qt.WindowTitleHint)
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(24, 22, 24, 20)
        self.info_lbl = QLabel("准备中…")
        self.info_lbl.setFont(_yzd_font(11))
        self.info_lbl.setWordWrap(True)
        layout.addWidget(self.info_lbl)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)          # busy 动画
        self.bar.setTextVisible(False)
        self.bar.setMinimumHeight(20)
        layout.addWidget(self.bar)
        self.detail_lbl = QLabel("导入期间请勿关闭程序，也不要执行其他操作。")
        self.detail_lbl.setFont(_yzd_font(9))
        self.detail_lbl.setStyleSheet("color: #999;")
        self.detail_lbl.setWordWrap(True)
        layout.addWidget(self.detail_lbl)

    def set_text(self, main: str, detail: str = ""):
        self.info_lbl.setText(main)
        if detail:
            self.detail_lbl.setText(detail)

    def allow_close(self):
        """导入跑完后才能真关（close() 也要走 closeEvent）"""
        self._allow_close = True

    def closeEvent(self, event):
        # 没做完不给关：关掉也只是让用户以为导入停了（线程其实还在跑）
        if self._allow_close:
            event.accept()
        else:
            event.ignore()


class YzdGroupDialog(QDialog):
    """「导入云展客户库」弹窗（对应云展的两张图）：

      第一页：国内/国外 tab + 分组名模糊搜索 + 「+ 新建分组」+ 分组单选列表，
              底部「取消 / 导入其他 / 导入最新」，分组必选；
      第二页：所有历史采集任务，按采集时间倒序，多选 + 全选，必选。

    分组必选、任务必选、未绑定提示这三件事全部在这里拦住，
    MainWindow 只负责“拿到 group + mode + tasks 去上传”。

    pick_group_only=True 时是「采集完自动导入」预先选分组的场景：只选分组、
    不马上导入，所以藏掉「导入其他」并把确认按钮改成「选用该分组」，
    避免用户以为点下去就开始传数据。
    """
    MODE_LATEST = "latest"
    MODE_OTHERS = "others"
    HINT_IMPORT = "选完分组后点右下「导入最新」或「导入其他」"
    HINT_PICK = "选一个分组作为采集完自动导入的目标，然后点右下「选用该分组」"

    def __init__(self, phone, parent=None, pick_group_only=False):
        super().__init__(parent)
        self.phone = phone
        self._pick_only = bool(pick_group_only)
        self._hint_text = self.HINT_PICK if self._pick_only else self.HINT_IMPORT
        self.mode = ""
        self.chosen_group = None
        self.chosen_tasks = []
        self._groups = {0: None, 1: None}    # 每个 tab 缓存一次结果，切回去不重复请求
        self._flag = 0
        self._worker = None
        self._create_worker = None
        self._pending_name = ""
        self._created_id = ""
        self.setWindowTitle("选择自动导入的客户库分组" if self._pick_only else "导入云展客户库")
        self.setMinimumSize(560, 460)
        self.resize(600, 500)
        self._init_ui()
        self._load_groups()

    # ── UI ──
    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)
        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.stack.addWidget(self._build_group_page())
        self.stack.addWidget(self._build_task_page())

    def _build_group_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setSpacing(10)

        tab_row = QHBoxLayout()
        self.tab_btns = {}
        self.tab_group = QButtonGroup(self)
        self.tab_group.setExclusive(True)
        for flag, text in ((0, "国内客户库"), (1, "国外客户库")):
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setChecked(flag == 0)
            btn.setMinimumHeight(32)
            btn.setStyleSheet(
                "QPushButton { background: #eef1f6; color: #1a3c6e; border: 1px solid #d0d8e8; }"
                "QPushButton:checked { background: #4472C4; color: white; font-weight: bold; }")
            btn.clicked.connect(lambda _c, f=flag: self._switch_flag(f))
            self.tab_group.addButton(btn, flag)
            self.tab_btns[flag] = btn
            tab_row.addWidget(btn)
        tab_row.addStretch()
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setMinimumHeight(32)
        self.refresh_btn.setMinimumWidth(64)
        self.refresh_btn.setToolTip(
            "重新向云展后端查一次分组和客户数。\n"
            "「客户数」是后端统计值（最长约 60 秒才更新），刚导完立刻看可能还是旧数字。")
        self.refresh_btn.clicked.connect(self._refresh_groups)
        tab_row.addWidget(self.refresh_btn)
        self.new_group_btn = QPushButton("+ 新建分组")
        self.new_group_btn.setMinimumHeight(32)
        self.new_group_btn.clicked.connect(self._create_group)
        tab_row.addWidget(self.new_group_btn)
        lay.addLayout(tab_row)

        search_row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索分组名称（模糊匹配）")
        self.search_edit.setMinimumHeight(30)
        self.search_edit.returnPressed.connect(self._search)
        search_row.addWidget(self.search_edit, 1)
        self.search_btn = QPushButton("搜索")
        self.search_btn.setMinimumHeight(30)
        self.search_btn.setMinimumWidth(70)
        self.search_btn.clicked.connect(self._search)
        search_row.addWidget(self.search_btn)
        lay.addLayout(search_row)

        self.group_status_lbl = QLabel("正在加载分组…")
        self.group_status_lbl.setFont(_yzd_font(9))
        self.group_status_lbl.setStyleSheet("color: #666;")
        self.group_status_lbl.setWordWrap(True)
        lay.addWidget(self.group_status_lbl)

        self.group_list = QListWidget()
        self.group_list.setMinimumHeight(180)
        self.group_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.group_list.itemSelectionChanged.connect(self._update_hint)
        lay.addWidget(self.group_list, 1)

        self.hint_lbl = QLabel(self._hint_text)
        self.hint_lbl.setFont(_yzd_font(9))
        self.hint_lbl.setStyleSheet("color: #666;")
        self.hint_lbl.setWordWrap(True)
        lay.addWidget(self.hint_lbl)

        btn_row = QHBoxLayout()
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setMinimumHeight(36)
        self.cancel_btn.setMinimumWidth(90)
        self.cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self.cancel_btn)
        btn_row.addStretch()
        self.others_btn = QPushButton("导入其他")
        self.others_btn.setMinimumHeight(36)
        self.others_btn.setMinimumWidth(110)
        self.others_btn.clicked.connect(self._show_tasks)
        btn_row.addWidget(self.others_btn)
        self.latest_btn = QPushButton("导入最新")
        self.latest_btn.setMinimumHeight(36)
        self.latest_btn.setMinimumWidth(110)
        self.latest_btn.setDefault(True)
        self.latest_btn.clicked.connect(self._accept_latest)
        btn_row.addWidget(self.latest_btn)
        if self._pick_only:
            # 预先选分组：不需要选任务，确认也不是“立刻导入”
            self.others_btn.setVisible(False)
            self.latest_btn.setText("选用该分组")
        lay.addLayout(btn_row)
        return page

    def _build_task_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setSpacing(10)

        top = QHBoxLayout()
        self.back_btn = QPushButton("← 返回")
        self.back_btn.setMinimumHeight(32)
        self.back_btn.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        top.addWidget(self.back_btn)
        title = QLabel("选择要导入的采集任务（可多选，必选）")
        title.setFont(_yzd_font(11, True))
        top.addWidget(title, 1)
        self.all_chk = QCheckBox("全选")
        self.all_chk.toggled.connect(self._toggle_all)
        top.addWidget(self.all_chk)
        lay.addLayout(top)

        self.task_count_lbl = QLabel("共 0 个任务")
        self.task_count_lbl.setFont(_yzd_font(9))
        self.task_count_lbl.setStyleSheet("color: #666;")
        lay.addWidget(self.task_count_lbl)

        self.task_list = QListWidget()
        self.task_list.setMinimumHeight(200)
        self.task_list.setSelectionMode(QAbstractItemView.NoSelection)
        self.task_list.itemChanged.connect(lambda _i: self._update_task_hint())
        lay.addWidget(self.task_list, 1)

        self.task_hint_lbl = QLabel("至少选择一个任务")
        self.task_hint_lbl.setFont(_yzd_font(9))
        self.task_hint_lbl.setStyleSheet("color: #666;")
        self.task_hint_lbl.setWordWrap(True)
        lay.addWidget(self.task_hint_lbl)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.task_cancel_btn = QPushButton("取消")
        self.task_cancel_btn.setMinimumHeight(36)
        self.task_cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self.task_cancel_btn)
        self.task_ok_btn = QPushButton("确认导入")
        self.task_ok_btn.setMinimumHeight(36)
        self.task_ok_btn.setMinimumWidth(110)
        self.task_ok_btn.clicked.connect(self._accept_others)
        btn_row.addWidget(self.task_ok_btn)
        lay.addLayout(btn_row)
        return page

    # ── 分组加载 ──
    def _set_busy(self, busy: bool):
        """等接口期间把会触发重复请求的按钮先锁住（连点两次会起两个线程）"""
        for w in (self.new_group_btn, self.search_btn, self.search_edit,
                  self.refresh_btn, self.latest_btn, self.others_btn):
            w.setEnabled(not busy)
        if busy:
            QApplication.setOverrideCursor(Qt.WaitCursor)
        else:
            QApplication.restoreOverrideCursor()

    def _switch_flag(self, flag: int):
        if flag == self._flag and self._groups[flag] is not None:
            return
        self._flag = flag
        cached = self._groups.get(flag)
        if cached is None:
            self._load_groups()
        else:
            self._render_groups(cached, "")

    def _search(self):
        self._groups[self._flag] = None      # 搜索结果是另一个集合，不能拿旧缓存当新结果
        self._load_groups()

    def _refresh_groups(self):
        """主动绕过缓存重查一次：客户数是后端算的，不刷就只会一直显示旧值"""
        self._groups[self._flag] = None
        self._load_groups()

    def _load_groups(self):
        if self._worker is not None and self._worker.isRunning():
            return
        keyword = self.search_edit.text().strip()
        self.group_status_lbl.setText("正在加载分组…")
        self._set_busy(True)
        self._worker = YzdGroupsWorker(self.phone, self._flag, keyword)
        self._worker.finished_groups.connect(self._on_groups)
        self._worker.start()

    def _on_groups(self, flag: int, groups: list, error: str):
        self._groups[flag] = groups if not error else None
        if flag != self._flag:
            self._set_busy(False)      # 切了 tab，旧请求的结果只入缓存
            return
        self._render_groups(groups, error)
        self._set_busy(False)

    def _render_groups(self, groups: list, error: str):
        self.group_list.clear()
        if error:
            self.group_status_lbl.setText(f"分组加载失败：{error}")
            self.group_status_lbl.setStyleSheet("color: #c62828;")
            self._update_hint()
            return
        self.group_status_lbl.setStyleSheet("color: #666;")
        if not groups:
            keyword = self.search_edit.text().strip()
            self.group_status_lbl.setText(
                f"没搜到分组，换个关键词试试" if keyword else
                "该账号下还没有分组，请先点「+ 新建分组」创建一个")
            self._update_hint()
            return
        self.group_status_lbl.setText(
            f"共 {len(groups)} 个分组，选中一个后点右下角导入" if not self._pick_only else
            f"共 {len(groups)} 个分组，选一个当自动导入目标")
        self.group_status_lbl.setToolTip(
            "括号里的「客户数」直接取自云展后端接口返回（不是本地统计）。\n"
            "后端是缓存值，最长约 60 秒才更新一次；刚导完看到数字没变是正常的，"
            "点右上「刷新」重新查一次即可。")
        for g in groups:
            num = g.get("clientNum")
            text = f"{g.get('groupName', '')}"
            if num is not None:
                # 文档说明 clientNum 是缓存值（最长 60 秒更新），所以不强调精确
                text += f"　（客户数 {num}）"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, g)
            self.group_list.addItem(item)
        if self._created_id and self._select_group_by_id(self._created_id):
            # 刚新建完就默认选中它：用户的下一步八成就是往这个新分组里导入
            self._created_id = ""
        self._update_hint()

    def _current_group(self):
        items = self.group_list.selectedItems()
        return items[0].data(Qt.UserRole) if items else None

    def _update_hint(self, error: bool = False):
        """分组必选：选中就清空提示，没选上才标红提醒（默认只给中性引导文案）"""
        if self._current_group():
            self.hint_lbl.setText("")
            self.hint_lbl.setStyleSheet("color: #666;")
        elif error:
            self.hint_lbl.setText("请先选择一个分组（必选）")
            self.hint_lbl.setStyleSheet("color: #c62828;")
        else:
            self.hint_lbl.setText(self._hint_text)
            self.hint_lbl.setStyleSheet("color: #666;")

    def _need_group(self):
        """导入前统一拦住：没选分组就不往下走（必选）"""
        group = self._current_group()
        if group is None:
            self._update_hint(error=True)
            QMessageBox.warning(self, "提示", "请选择一个分组")
        return group

    # ── 新建分组（接口5）──
    def _create_group(self):
        if not self.phone:
            QMessageBox.warning(self, "提示", "请先绑定云展动力账号")
            return
        name, ok = QInputDialog.getText(self, "新建分组", "群组名称（最多 10 个字符）：")
        if not ok:
            return
        name = (name or "").strip()
        if not name:
            QMessageBox.warning(self, "提示", "群组名称不能为空！")
            return
        if len(name) > _yzd.GROUP_NAME_MAX:
            QMessageBox.warning(self, "提示", f"群组名称最多输入{_yzd.GROUP_NAME_MAX}个字符！")
            return
        if self._create_worker is not None and self._create_worker.isRunning():
            return
        self._set_busy(True)
        self.group_status_lbl.setText(f"正在创建分组「{name}」…")
        self._pending_name = name
        self._create_worker = YzdCreateGroupWorker(self.phone, name, self._flag)
        self._create_worker.finished_create.connect(self._on_group_created)
        self._create_worker.start()

    def _on_group_created(self, group: dict, error: str):
        if error:
            self._set_busy(False)
            self.group_status_lbl.setText(f"新建分组失败：{error}")
            QMessageBox.warning(self, "新建分组失败", error)
            return
        # 后端同名会自动加序号后缀（返回的 groupName 才是最终名字），先丢分组再按名字重查
        self._groups = {0: None, 1: None}
        self._created_id = str(group.get("id") or "")
        self.search_edit.clear()
        self.group_status_lbl.setText(f"已创建分组「{group.get('groupName') or self._pending_name}」")
        self._load_groups()

    def _select_group_by_id(self, gid: str):
        for i in range(self.group_list.count()):
            item = self.group_list.item(i)
            data = item.data(Qt.UserRole) or {}
            if str(data.get("id")) == gid:
                self.group_list.setCurrentItem(item)
                return True
        return False

    # ── 导入最新 ──
    def _accept_latest(self):
        group = self._need_group()
        if group is None:
            return
        self.mode = self.MODE_LATEST
        self.chosen_group = group
        self.accept()

    # ── 导入其他 ──
    def _show_tasks(self):
        group = self._need_group()
        if group is None:
            return
        self.chosen_group = group
        self._fill_tasks()
        self.stack.setCurrentIndex(1)

    def _fill_tasks(self):
        self.task_list.clear()
        self.all_chk.blockSignals(True)
        self.all_chk.setChecked(False)
        self.all_chk.blockSignals(False)
        tasks = _tasks.sorted_for_import(_tasks.load_tasks())
        self._task_map = {}
        for t in tasks:
            raw = t.get("raw_file") or ""
            name = t.get("name") or t.get("id") or "未命名任务"
            text = (f"{name}\n"
                    f"采集时间 {t.get('started_at') or '—'}"
                    f"　{t.get('count') or 0} 条"
                    f"　[{t.get('status') or '—'}]")
            item = QListWidgetItem(text)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            item.setData(Qt.UserRole, t)
            self.task_list.addItem(item)
            self._task_map[id(item)] = t
        if not tasks:
            self.task_count_lbl.setText(
                "暂无采集任务记录：先在主界面完成一次「开始采集 → 采集完成」")
        else:
            self.task_count_lbl.setText(
                f"共 {len(tasks)} 个任务（按采集时间倒序），任务名取本次导入的采集文档")
        self._update_task_hint()

    def _toggle_all(self, checked: bool):
        for i in range(self.task_list.count()):
            self.task_list.item(i).setCheckState(Qt.Checked if checked else Qt.Unchecked)

    def _checked_tasks(self):
        out = []
        for i in range(self.task_list.count()):
            item = self.task_list.item(i)
            if item.checkState() == Qt.Checked:
                task = item.data(Qt.UserRole)
                if task:
                    out.append(task)
        return out

    def _update_task_hint(self, error: bool = False):
        n = len(self._checked_tasks())
        if n:
            self.task_hint_lbl.setText("")
            self.task_hint_lbl.setStyleSheet("color: #666;")
        elif error:
            self.task_hint_lbl.setText("请至少选择一个任务（必选）")
            self.task_hint_lbl.setStyleSheet("color: #c62828;")
        else:
            self.task_hint_lbl.setText("勾选要导入的采集任务（可多选）")
            self.task_hint_lbl.setStyleSheet("color: #666;")
        self.task_ok_btn.setText(f"确认导入（{n}）" if n else "确认导入")

    def _accept_others(self):
        tasks = self._checked_tasks()
        if not tasks:
            self._update_task_hint(error=True)
            QMessageBox.warning(self, "提示", "请至少选择一个任务")
            return
        self.mode = self.MODE_OTHERS
        self.chosen_tasks = tasks
        self.accept()

    def closeEvent(self, event):
        # 后台线程还在发请求时不能直接拆窗口（Qt 会警告“destroying thread while running”）
        for w in (self._worker, self._create_worker):
            if w is not None and w.isRunning():
                w.wait(3000)
        event.accept()


class MainWindow(QMainWindow):
    APP_TITLE = "公司名查企业数据工具"
    # 数据来源下拉顺序（风鸟排最后）：(key, 中文名, 站点域名)，_current_source 就是这里的下标；
    # 平台判定一律用 key，以后再调顺序不用到处改魔法数字
    SOURCES = (("qizhidao", "企知道", "qizhidao.com"),
               ("aiqicha", "爱企查", "aiqicha.baidu.com"),
               ("riskbird", "风鸟", "riskbird.com"))
    SOURCE_KEYS = tuple(s[0] for s in SOURCES)
    SOURCE_LABELS = {s[0]: s[1] for s in SOURCES}
    SOURCE_DOMAINS = {s[0]: s[2] for s in SOURCES}
    PLATFORM_NAMES = [s[1] for s in SOURCES]
    # 合并同名公司时“先出现的非空字段胜出”，这里保持改造前的数据优先级不动
    MERGE_ORDER = ("riskbird", "qizhidao", "aiqicha")

    def __init__(self):
        super().__init__()
        # 初始化三个爬虫
        self.riskbird_crawler = RiskBirdCrawlerV2()
        self.qizhidao_crawler = QiZhiDaoCrawler()
        self.aiqicha_crawler = AiQiChaCrawlerV2()
        self.crawler = self.qizhidao_crawler  # 默认使用企知道
        
        # key → 爬虫；_all_crawlers 与下拉框同序，下标可直接配 _current_source
        self._by_key = {"riskbird": self.riskbird_crawler,
                        "qizhidao": self.qizhidao_crawler,
                        "aiqicha": self.aiqicha_crawler}
        self._all_crawlers = [self._by_key[k] for k in self.SOURCE_KEYS]

        # 连接信号
        for platform, crawler in zip(self.PLATFORM_NAMES, self._all_crawlers):
            crawler.signals.log_signal.connect(self._append_log)
            crawler.signals.progress_signal.connect(self._update_progress)
            crawler.signals.crawl_finished_signal.connect(self._on_crawl_finished)
            # 平台安全验证/风控拦截：醒目提示“去浏览器完成验证”，而不是一直挂着等超时
            crawler.signals.blocked_signal.connect(lambda msg, p=platform: self._on_blocked(p, msg))
            crawler.signals.block_cleared_signal.connect(lambda msg, p=platform: self._on_block_cleared(p))
            # 未登录：弹窗让用户决定“去登录/仍然继续/停止”，不能默默把名单烧成空数据
            crawler.signals.login_required_signal.connect(lambda msg, p=platform: self._on_login_required(p, msg))
            # 某一路已放弃等人工（夜间无人处理）：红条要撤掉，否则看起来像整个工具卡死
            crawler.signals.handoff_signal.connect(lambda msg, p=platform: self._on_platform_handoff(p, msg))
        # 风鸟特有的信号
        self.riskbird_crawler.signals.login_status_signal.connect(self._on_login_status)
        self.riskbird_crawler.signals.manual_check_signal.connect(self._on_manual_check)

        self._companies = []
        self._excel_paths = []  # 支持多文件
        self._col_pick_cache = {}  # 公司名列人工确认结果缓存（表头组合 → 列号）
        self._crawl_thread = None
        self._current_source = 0  # 下拉框下标，默认企知道（顺序见 SOURCE_KEYS）
        self._dual_running = False  # 是否正在双标签并行采集
        # 跨平台续采 + 桌面会话文件夹 + 每 10 条自动保存（_all_crawlers 已在信号区建好）
        self._session_folder = None       # 桌面/企业数据自动采集工具/<第一次开始采集时间>
        self._last_autosave_bucket = 0    # 每 10 条自动保存的计数桶
        self._autosave_worker = None        # 后台写盘线程（存引用防被 GC）
        self._autosave_pending = None       # 上一次还在写时，只记最后一次请求，写完补一轮
        self._blocked_info = []           # 正在等人工处理的平台：[[平台名, 原因], …]
        self._blocked_box = None          # 非模态提示框（不阻塞界面，用户要去浏览器操作）
        self._login_info = []             # 正在等人工登录的平台：[[平台名, 原因], …]
        # 云展动力客户库（绑定账号 + 导入）
        if HAS_YZD:
            # 两边必须用同一个桌面：GUI 自己有一套 OneDrive 判定，任务索引不能另算一份
            _tasks.set_desktop_dir(self._get_desktop_dir())
        self._yzd_phone = _tasks.load_account() if HAS_YZD else ""
        self._yzd_worker = None           # 绑定账号线程（存引用防被 GC）
        self._yzd_import_worker = None    # 导入线程（同上）
        self._yzd_dialog = None           # 选分组/选任务弹窗
        self._yzd_progress = None         # 导入锁定遮罩
        self._yzd_importing = False       # 导入中标志（closeEvent 也看它）
        self._yzd_jobs = []               # 本轮导入的任务快照（用于回写导入结果）
        self._yzd_group_name = ""         # 本轮导入的目标分组名（回写任务索引用）
        self._clean_worker = None         # 「清洗数据」后台线程（存引用防被 GC）
        self._cleaning = False            # 正在清洗中（closeEvent 与重复点按钮都看它）
        self._clean_progress = None       # 清洗期间的锁定遮罩
        self._auto_import_group = None    # 「采集完自动导入」选定的分组
        self._task_id = None              # 本次采集会话对应的任务 id（=会话文件夹名）
        # 断点续采：进度台账 + 原始名单顺序快照（奇偶分工/待采集合都按它算）
        self._checkpoint = None           # crawl_checkpoint.CrawlCheckpoint 或 None
        self._companies_snapshot = []     # 原始名单顺序快照（含已采完的家）
        self._cp_cursor = {}              # 各爬虫已写进台账的结果条数（增量扫描用）

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
        self.title_label = QLabel(self.APP_TITLE)
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
        for _i, _key in enumerate(self.SOURCE_KEYS):
            self.source_combo.addItem(f"{self.SOURCE_LABELS[_key]} ({self.SOURCE_DOMAINS[_key]})")
        self.source_combo.setCurrentIndex(0)  # 默认企知道
        self.source_combo.setMinimumWidth(220)
        self.source_combo.setMinimumHeight(30)
        self.source_combo.currentIndexChanged.connect(self._on_source_changed)
        title_bar.addWidget(self.source_combo)

        # 打开网页按钮
        self.open_web_btn = QPushButton("打开网页")
        self.open_web_btn.setMinimumHeight(34)
        self.open_web_btn.setMinimumWidth(80)
        self.open_web_btn.clicked.connect(self._open_website)
        title_bar.addWidget(self.open_web_btn)

        main_layout.addLayout(title_bar)

        # 提示
        self.hint_lbl = QLabel("提示：建议先点“打开网页”，在浏览器里确认已登录目标平台，再开始采集")
        self.hint_lbl.setFont(QFont("Microsoft YaHei" if sys.platform == "win32" else "Arial", 9))
        self.hint_lbl.setStyleSheet("color: #999; padding: 2px 0;")
        self.hint_lbl.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(self.hint_lbl)

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

        # ── 控制区（按钮一行、选项一行：勾选项就在“开始采集”下方）──
        ctrl_group = QGroupBox("  采集控制")
        ctrl_layout = QVBoxLayout(ctrl_group)
        btn_row = QHBoxLayout()

        self.start_btn = QPushButton("开始采集")
        self.start_btn.setMinimumHeight(42)
        self.start_btn.setMinimumWidth(100)
        self.start_btn.setEnabled(False)
        self.start_btn.clicked.connect(self._start_crawl)
        btn_row.addWidget(self.start_btn)

        self.pause_btn = QPushButton("暂停")
        self.pause_btn.setMinimumHeight(42)
        self.pause_btn.setMinimumWidth(80)
        self.pause_btn.setEnabled(False)
        self.pause_btn.clicked.connect(self._pause_crawl)
        btn_row.addWidget(self.pause_btn)

        self.resume_btn = QPushButton("恢复")
        self.resume_btn.setMinimumHeight(42)
        self.resume_btn.setMinimumWidth(80)
        self.resume_btn.setEnabled(False)
        self.resume_btn.clicked.connect(self._resume_crawl)
        btn_row.addWidget(self.resume_btn)

        self.stop_btn = QPushButton("停止采集")
        self.stop_btn.setMinimumHeight(42)
        self.stop_btn.setMinimumWidth(100)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop_crawl)
        btn_row.addWidget(self.stop_btn)

        self.export_btn = QPushButton("导出 Excel")
        self.export_btn.setMinimumHeight(42)
        self.export_btn.setMinimumWidth(100)
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._export_excel)
        btn_row.addWidget(self.export_btn)

        # 清洗数据：不依赖本工具采到的数据，随时可用（拿别人给的名单也能洗）
        self.clean_btn = QPushButton("清洗数据")
        self.clean_btn.setMinimumHeight(42)
        self.clean_btn.setMinimumWidth(100)
        self.clean_btn.setToolTip(
            "把其他渠道拿到的 Excel/CSV 名单清洗成《清洗后客户库入库数据》（与自动保存同格式）。\n"
            "支持多选文件；列名会自动识别（企业名/联系电话/邮箱/联系人/职位/地址等），\n"
            "列名对不上时按单元格内容猜，识别结果与未使用的列都会写进运行日志。\n"
            "自动剔除空公司名、完成电话/邮箱格式清洗与跨文件去重合并。\n"
            "电话只分类不删：识别到的号码全部拆进「手机号」「固话」两栏，一家几个都保留。")
        self.clean_btn.clicked.connect(self._clean_external_data)
        btn_row.addWidget(self.clean_btn)

        btn_row.addStretch()
        ctrl_layout.addLayout(btn_row)

        # 延迟设置
        delay_layout = QHBoxLayout()
        delay_layout.addWidget(QLabel("请求间隔(秒):"))
        self.delay_spin = QLineEdit("0.5")
        self.delay_spin.setFixedWidth(50)
        delay_layout.addWidget(self.delay_spin)

        # 风鸟专属：是否逐家进详情页抽取全量联系方式（消耗每日详情额度）
        self.rb_detail_check = QCheckBox("风鸟：进详情页抽全量联系方式")
        self.rb_detail_check.setToolTip(
            "风鸟账号有两层独立的每日额度（均 0 点恢复）：\n"
            "  1) 关键词搜索（模糊查询）次数——控制搜索结果页，每家只给 1 个电话 + 1 个邮箱；\n"
            "  2) 详情页查询次数（每账号每日 50 次，工具限制到 40 次）——才能拿到“更多”里的全量电话/邮箱。\n"
            "不勾选：只用搜索结果页，用完一次模糊查询额度就采不了新公司；\n"
            "勾选：逐家进详情页，适合重点名单（建议 ≤40 家）。\n"
            "两者撞墙后工具会自动降级到不计次的补全接口（只拿登记信息），\n"
            "并把未采到的写进 riskbird_data/风鸟待补采清单.txt 供次日重跑。\n"
            "已采过的公司会缓存在 riskbird_data/风鸟列表缓存.json（30 天内直接复用、不耗次数，\n"
            "想强制重新采集删掉该文件即可）。"
        )
        self.rb_detail_check.toggled.connect(self._on_force_detail_toggled)
        delay_layout.addWidget(self.rb_detail_check)
        self.rb_detail_check.setVisible(getattr(self, "_current_source", 0) == self.SOURCE_KEYS.index("riskbird"))

        # ── 双标签并行采集（企知道 + 爱企查，单双数分公司）──
        self._dual_pref = True   # 用户意愿（切到风鸟时临时置灰，切回来恢复）
        self.dual_tab_check = QCheckBox("双标签并行采集（企知道+爱企查）")
        self.dual_tab_check.setChecked(True)
        self.dual_tab_check.setToolTip(
            "勾选后两个平台同时采集（共用一个浏览器、各占一个窗口，登录态共用、互不干扰）：\n"
            "  · 窗口A = 企知道，采第 1、3、5… 家；\n"
            "  · 窗口B = 爱企查，采第 2、4、6… 家。\n"
            "两边结果自动合并，导出时照样生成《清洗后客户库入库数据》，\n"
            "自动保存也各存各的（企知道.xlsx / 爱企查.xlsx）。\n"
            "实测比单平台逐个跑省约一半时间，且不会采串公司。\n"
            "夜间挂机：爱企查被验证码/限流拦住超过 10 分钟没人处理时会自动停这一路，\n"
            "剩下的公司转给企知道接着采，不会整晚卡在一个验证码上。\n"
            "不勾选：只按上面选中的平台单独采集。\n"
            "风鸟不支持双标签（它有每日额度限制，并行只会更快用完）。"
        )
        self.dual_tab_check.toggled.connect(self._on_dual_tab_toggled)
        delay_layout.addWidget(self.dual_tab_check)
        self._sync_dual_tab_enabled()

        # ── 采集完自动导入云展客户库（默认不勾选）──
        self.auto_import_check = QCheckBox("采集完自动导入云展客户库")
        self.auto_import_check.setToolTip(
            "勾选时会先让你选一个客户库分组；采集完成后自动把本次采集的数据\n"
            "上传到该分组，并在日志里提示导入结果（不需再手动点「导入云展客户库」）。\n"
            "需先绑定云展动力账号。手动点“停止采集”不会触发自动导入。\n"
            "不勾选（默认）：采集完只能自己点按钮导入。")
        self.auto_import_check.toggled.connect(self._on_auto_import_toggled)
        delay_layout.addWidget(self.auto_import_check)
        ctrl_layout.addLayout(delay_layout)

        main_layout.addWidget(ctrl_group)

        # ── 云展动力客户库（绑定账号 + 导入）──
        yzd_group = QGroupBox("  云展动力客户库")
        yzd_layout = QHBoxLayout(yzd_group)

        self.yzd_status_lbl = QLabel("未绑定云展动力账号")
        self.yzd_status_lbl.setFont(QFont("Microsoft YaHei" if sys.platform == "win32" else "Arial", 10))
        self.yzd_status_lbl.setMinimumWidth(200)
        yzd_layout.addWidget(self.yzd_status_lbl)
        yzd_layout.addStretch()

        self.bind_yzd_btn = QPushButton("绑定云展动力账号")
        self.bind_yzd_btn.setMinimumHeight(36)
        self.bind_yzd_btn.setMinimumWidth(150)
        self.bind_yzd_btn.setToolTip(
            "输入云展动力手机号，调云展接口校验格式与账号是否存在，校验通过后才保存绑定。\n"
            "绑定信息存在：桌面/企业数据自动采集工具/_云展账号.json（跨重启仍有效）。"
        )
        self.bind_yzd_btn.clicked.connect(self._bind_yzd_account)
        yzd_layout.addWidget(self.bind_yzd_btn)

        self.import_yzd_btn = QPushButton("导入云展客户库")
        self.import_yzd_btn.setMinimumHeight(36)
        self.import_yzd_btn.setMinimumWidth(140)
        self.import_yzd_btn.setObjectName("exportBtn")
        self.import_yzd_btn.setToolTip(
            "选择客户库分组后，把采集结果上传到云展动力客户库。\n"
            "· 导入最新：只传最近一次采集任务；\n"
            "· 导入其他：从历史采集任务里挑（可多选）。\n"
            "上传中间会锁定界面并显示加载动画，走完弹结果汇总。"
        )
        self.import_yzd_btn.clicked.connect(self._open_yzd_import)
        yzd_layout.addWidget(self.import_yzd_btn)

        main_layout.addWidget(yzd_group)
        self._refresh_yzd_status()

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
        self.platform_count_lbl = QLabel("    ".join(f"{n}: 0 条" for n in self.PLATFORM_NAMES) + "    合计: 0 条")
        self.platform_count_lbl.setFont(QFont(count_font, 10, QFont.Bold))
        self.platform_count_lbl.setStyleSheet(
            "QLabel { color: #1a73e8; background: #eef4ff; border: 1px solid #d0e0ff; border-radius: 4px; padding: 6px; }"
        )
        self.platform_count_lbl.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(self.platform_count_lbl)

        # ── 平台拦截提示条（默认隐藏，触发安全验证/风控时置顶提醒）──
        self.blocked_lbl = QLabel("")
        self.blocked_lbl.setFont(QFont(count_font, 10, QFont.Bold))
        self.blocked_lbl.setWordWrap(True)
        self.blocked_lbl.setStyleSheet(
            "QLabel { color: #ffffff; background: #c62828; border-radius: 4px; padding: 8px; }"
        )
        self.blocked_lbl.setAlignment(Qt.AlignCenter)
        self.blocked_lbl.hide()
        main_layout.addWidget(self.blocked_lbl)

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
            self.preview_btn.setEnabled(True)
            self._check_ready()
            self._append_log(f"从 {len(paths)} 个文件加载 {len(all_companies)} 家公司，去重后 {len(self._companies)} 家")
            # 已有会话时再选文件 = 往当前任务里加料，不拆新任务
            self._update_task_files()
            # 名单载入完就找断点：不要等用户点了“开始采集”才发现要从头烧一遍配额
            self._maybe_offer_resume()
            self._refresh_company_count()

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
        self._task_id = None
        # 进度台账跟会话走：换了名单就得重建，不能拿旧任务的账去算新名单
        self._checkpoint = None
        self._companies_snapshot = []
        self._cp_cursor = {}
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

    # ── 断点续采（任务清单 + 条目状态机 + 幂等键）───────
    def _platform_name(self, key: str) -> str:
        return self.SOURCE_LABELS.get(key, key or "")

    def _crawler_key_by_source(self, source) -> str:
        """记录里的“数据来源”→ 爬虫 key（回读原数据 Excel 时分派用）

        合并后的记录可能写成“企知道,爱企查”，按靠前的平台归位即可：
        续采只关心“这家公司采过没有”，不靠这里做去重。
        """
        text = str(source or "")
        for key in self.SOURCE_KEYS:
            if self.SOURCE_LABELS.get(key) and self.SOURCE_LABELS[key] in text:
                return key
        return ""

    def _sessions_base(self):
        """会话目录的父目录：桌面/企业数据自动采集工具"""
        return self._get_desktop_dir() / "企业数据自动采集工具"

    def _merge_snapshot(self) -> list:
        """原始名单顺序快照：老名单保持原位，新加的接在后面

        双标签靠位置取奇偶分工，续采时顺序变了会让同一家公司换平台（配额、登录态都不一样），
        所以分工一律按这份快照，不按“本次待采子集”。
        """
        out, seen = [], set()
        for name in list(self._companies_snapshot) + list(self._companies):
            text = str(name or "").strip()
            key = block_guard.norm_company(text)
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(text)
        return out

    def _refresh_company_count(self):
        """名单载入后的状态行：有台账就报「待采/已完成/失败待重试/无结果跳过」四个数"""
        if not hasattr(self, "company_count_lbl"):
            return
        cp = self._checkpoint
        if cp is not None:
            names = self._companies_snapshot or list(self._companies)
            if names:
                self.company_count_lbl.setText(cp.text_summary(names))
                return
        self.company_count_lbl.setText(f"共 {len(self._companies)} 家（去重后）")

    def _todo_companies(self, include_keys=None) -> list:
        """待采 = 名单快照 − done − no_result（临时失败计入待采重试）

        台账是跨进程的账本，内存里各爬虫的 results 是本轮的账，两边都要认：
        中途切平台时上一个平台采过的公司不该再烧一次请求。
        """
        snap = self._companies_snapshot or list(self._companies)
        todo = self._checkpoint.pending(snap) if self._checkpoint is not None else snap
        got = set()
        for key in (include_keys or self.SOURCE_KEYS):
            crawler = self._by_key.get(key)
            if crawler is None:
                continue
            for r in (crawler.snapshot_results() if hasattr(crawler, "snapshot_results")
                      else getattr(crawler, "results", []) or []):
                name = block_guard.norm_company(r.get("公司名") if isinstance(r, dict) else "")
                if name:
                    got.add(name)
        return [n for n in todo if block_guard.norm_company(n) not in got]

    def _find_resumable(self) -> list:
        """扫描未完成会话 → [(会话目录, 台账, 已完成数, 任务名, 任务状态)]，最接近采完的排前面"""
        if not HAS_CHECKPOINT:
            return []
        base = self._sessions_base()
        titles, statuses = {}, {}
        if HAS_YZD:
            try:
                for t in _tasks.load_tasks():
                    tid = str(t.get("id") or "")
                    titles[tid] = str(t.get("name") or "")
                    statuses[tid] = str(t.get("status") or "")
            except Exception:
                pass
        try:
            folders = sorted([p for p in base.iterdir() if p.is_dir()], reverse=True)[:40]
        except Exception:
            return []
        out = []
        for folder in folders:
            if self._session_folder is not None and folder.name == self._session_folder.name:
                continue          # 当前会话不跟自己弹续采
            if not os.path.exists(os.path.join(str(folder), _cp.CHECKPOINT_FILE_NAME)):
                continue
            cp, msg = _cp.inspect_folder(folder, self._companies, self._excel_paths,
                                         log=self._append_log)
            if cp is None:
                if msg and "损坏" in msg:
                    self._append_log(f"ℹ 会话 {folder.name}：{msg}")
                continue
            done_n = cp.summary(self._companies)[_cp.STATUS_DONE]
            out.append((folder, cp, done_n, titles.get(folder.name) or folder.name,
                        statuses.get(folder.name, "")))
        # 采集中/已停止的任务先提（本来就是没采完的），同一类里按已完成家数倒序
        unfinished = ((_tasks.TASK_STATUS_RUNNING, _tasks.TASK_STATUS_STOPPED)
                      if HAS_YZD else ())
        return sorted(out, key=lambda it: (it[4] not in unfinished, -it[2]))

    def _maybe_offer_resume(self):
        """发现断点 → 弹窗「从断点继续（默认）/ 重新开始 / 取消」"""
        if not HAS_CHECKPOINT or not self._companies:
            return
        found = self._find_resumable()
        if not found:
            return
        folder, cp, _done_n, task_name, status = found[0]
        c = cp.summary(self._companies)
        extra = ""
        if len(found) > 1:
            extra = f"\n（还找到 {len(found) - 1} 个更早的未完成会话，需要时可从那个目录手动恢复）"
        box = QMessageBox(self)
        box.setWindowTitle("发现未完成的采集")
        box.setIcon(QMessageBox.Question)
        box.setText(
            f"上次未完成的采集：〈{task_name}〉{f'（{status}）' if status else ''}\n"
            f"会话目录：{folder.name}\n"
            f"名单 {c['total']} 家 / 已完成 {c[_cp.STATUS_DONE]} 家 / "
            f"临时失败待重试 {c[_cp.STATUS_FAILED]} 家 / 确认无结果不再采 {c[_cp.STATUS_NO_RESULT]} 家\n\n"
            f"是否从断点继续（本次只采剩下 {c['todo']} 家）？{extra}")
        again_btn = box.addButton("重新开始", QMessageBox.ActionRole)
        keep_btn = box.addButton("从断点继续", QMessageBox.AcceptRole)
        cancel_btn = box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(keep_btn)
        box.exec_()
        clicked = box.clickedButton()
        if clicked is keep_btn:
            self._adopt_checkpoint(cp, folder, task_name)
        elif clicked is again_btn:
            self._restart_session(folder)
        elif clicked is cancel_btn or clicked is None:
            self._append_log("ℹ 本次不按断点续采：旧进度文件保留，下次选同一份名单仍可继续")
        self._refresh_company_count()

    def _adopt_checkpoint(self, cp, folder, task_name: str = ""):
        """继续：接回会话目录与台账，并把原数据 Excel 里的记录灌回各平台 results"""
        self._session_folder = Path(folder)
        self._task_id = Path(folder).name
        self._checkpoint = cp
        records = _cp.load_records_from_raw_excel(folder)
        restored = self._restore_results(records)
        self._companies_snapshot = self._merge_snapshot()
        cp.set_snapshot(self._companies_snapshot)
        cp.files = _cp.files_fingerprint(self._excel_paths)
        cp.flush(force=True)
        self._append_log(
            f"↻ 从断点继续：{task_name or Path(folder).name}，"
            f"回读原数据 {len(records)} 条、恢复到平台 {restored} 条")
        self._append_log(f"   {cp.text_summary(self._companies_snapshot)}")
        self._update_platform_counts()

    def _restore_results(self, records: list) -> int:
        """按「数据来源」把记录放回对应平台的 results（归一化公司名去重，只补不覆盖）

        内存里已有的结果比 Excel 里的新（正在跑的这一轮还没写盘），续采时不能拿旧记录把它换掉。
        """
        got = {}
        for key in self.SOURCE_KEYS:
            crawler = self._by_key.get(key)
            got[key] = {block_guard.norm_company(r.get("公司名"))
                        for r in (getattr(crawler, "results", None) or [])}
        restored = 0
        for row in records or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("公司名") or row.get("企业名称") or "").strip()
            if not name:
                continue
            key = self._crawler_key_by_source(row.get("数据来源"))
            crawler = self._by_key.get(key)
            if crawler is None or not hasattr(crawler, "results"):
                continue
            nk = block_guard.norm_company(name)
            if nk in got[key]:
                continue
            got[key].add(nk)
            crawler.results.append(dict(row))
            if self._checkpoint is not None:
                self._checkpoint.mark_record(row, self._platform_name(key))
            restored += 1
        for key in self.SOURCE_KEYS:
            crawler = self._by_key.get(key)
            self._cp_cursor[key] = len(getattr(crawler, "results", None) or [])
        return restored

    def _restart_session(self, folder):
        """重新开始：清进度文件与任务状态，会话目录接着用（原数据 Excel 覆盖写）"""
        try:
            os.remove(os.path.join(str(folder), _cp.CHECKPOINT_FILE_NAME))
        except OSError as e:
            self._append_log(f"⚠ 进度文件没删掉（{e}）：本次仍按全量重采")
        self._session_folder = Path(folder)
        self._task_id = Path(folder).name
        self._checkpoint = None
        self._companies_snapshot = []
        self._cp_cursor = {}
        if HAS_YZD:
            try:
                _tasks.finish_task(Path(folder).name, _tasks.TASK_STATUS_STOPPED, 0)
            except Exception as e:
                self._append_log(f"⚠ 任务状态重置失败：{e}")
        self._append_log(f"↺ 重新开始：已清空该会话的采集进度，本次全量重采 {len(self._companies)} 家")

    def _ensure_checkpoint(self, folder):
        """开始采集前保证有台账：新会话建一份，已有会话补名单与指纹"""
        if not HAS_CHECKPOINT or folder is None:
            return None
        if self._checkpoint is not None and str(self._checkpoint.folder) == str(folder):
            cp = self._checkpoint
        else:
            cp = _cp.CrawlCheckpoint.create(
                Path(folder).name, str(folder),
                self._companies_snapshot or self._companies,
                file_paths=self._excel_paths, log=self._append_log)
            self._checkpoint = cp
            self._companies_snapshot = list(cp.snapshot)
            for key in self.SOURCE_KEYS:
                self._cp_cursor.setdefault(key, 0)
        cp.set_snapshot(self._companies_snapshot or self._companies)
        cp.files = _cp.files_fingerprint(self._excel_paths)
        cp.flush(force=True)
        return cp

    def _cp_mark_new(self):
        """各爬虫新增的结果写进台账（按条数游标增量扫）；台账自己每采满 5 条落一次盘"""
        cp = self._checkpoint
        if cp is None:
            return
        for key in self.SOURCE_KEYS:
            crawler = self._by_key.get(key)
            results = getattr(crawler, "results", None) or []
            start = self._cp_cursor.get(key, 0)
            if len(results) < start:
                start = 0        # 结果被清过：游标退回，否则以后永远对不上
                self._cp_cursor[key] = 0
            if len(results) == start:
                continue
            cp.mark_records(results[start:], self._platform_name(key))
            self._cp_cursor[key] = len(results)

    def _cp_flush(self, reason: str = ""):
        """暂停/停止/完成/关窗：把未落盘的状态强制写一次，崩之前采的都不会白采"""
        cp = self._checkpoint
        if cp is None:
            return
        self._cp_mark_new()
        ok = cp.flush(force=True)
        if reason:
            self._append_log((f"🧭 采集进度已落盘（{reason}）：" if ok
                              else f"⚠ 采集进度落盘失败（{reason}）：") + cp.text_summary())

    # ─ 登录 ───────────────────────────────────
    def _on_force_detail_toggled(self, checked):
        """风鸟“进详情页抽全量联系方式”开关"""
        crawler = getattr(self, "riskbird_crawler", None)
        if crawler is not None:
            crawler.force_detail = bool(checked)
        if checked:
            self._append_log(
                "⚠ 已开启风鸟详情页模式：每家会消耗 1 次每日详情额度（本轮上限 40 家），"
                "额度用完自动回到仅列表页采集（详情额度与搜索额度相互独立）"
            )

    # ── 双标签并行采集 ──────────────
    def _sync_dual_tab_enabled(self):
        """风鸟有每日额度限制，不参与双标签；其余两个平台可用"""
        chk = getattr(self, "dual_tab_check", None)
        if chk is None:
            return
        usable = HAS_DUAL and self._source_key() != "riskbird"
        chk.setEnabled(usable)
        if usable:
            # 从风鸟切回来时恢复用户原本的勾选意愿
            if self._dual_pref and not chk.isChecked():
                chk.setChecked(True)
        elif chk.isChecked():
            chk.blockSignals(True)
            chk.setChecked(False)
            chk.blockSignals(False)
            if hasattr(self, "log_text"):
                self._append_log("ℹ 风鸟有每日额度限制，不参与双标签，已自动改为单平台采集")
        if not HAS_DUAL:
            chk.setToolTip("双标签模块未加载（dual_session.py 缺失），仅能单平台采集")

    def _dual_enabled(self) -> bool:
        """本次采集是否走双标签并行"""
        return bool(HAS_DUAL and getattr(self, "dual_tab_check", None)
                    and self.dual_tab_check.isChecked()
                    and self._source_key() != "riskbird")

    def _on_dual_tab_toggled(self, checked):
        self._dual_pref = bool(checked)
        if not hasattr(self, "log_text"):
            return
        if checked:
            self._append_log(
                "⚡ 已开启双标签并行：企知道采单数（1、3、5…）、爱企查采双数（2、4、6…），"
                "两个窗口共用一份登录态、各自独立采集，结果自动合并"
            )
        else:
            self._append_log("已关闭双标签并行，只按上面选中的平台单独采集")

    def _log_dual_platform(self, tag):
        """并行时给两个平台的日志加前缀（缓存函数对象，否则后续 disconnect 对不上）"""
        cache = self.__dict__.setdefault("_dual_log_fns", {})
        if tag not in cache:
            def _emit(msg, _t=tag):
                self._append_log(f"【{_t}】{msg}")
            cache[tag] = _emit
        return cache[tag]

    def _attach_dual_logs(self, on: bool):
        """并行期间把两个爬虫的日志/进度/完成信号改接到双标签专用处理（幂等）"""
        on = bool(on)
        if getattr(self, "_dual_logs_on", False) == on:
            return   # 重复调用会把 _append_log 连两遍，造成日志翻倍，这里直接短路
        pairs = [
            (self.qizhidao_crawler, self._log_dual_platform("企知道")),
            (self.aiqicha_crawler, self._log_dual_platform("爱企查")),
        ]
        try:
            for crawler, prefixed in pairs:
                if crawler.signals is None:
                    continue
                if on:
                    try:
                        crawler.signals.log_signal.disconnect(self._append_log)
                    except TypeError:
                        pass
                    crawler.signals.log_signal.connect(prefixed)
                    try:
                        crawler.signals.progress_signal.disconnect(self._update_progress)
                        crawler.signals.crawl_finished_signal.disconnect(self._on_crawl_finished)
                    except TypeError:
                        pass
                else:
                    try:
                        crawler.signals.log_signal.disconnect(prefixed)
                    except TypeError:
                        pass
                    crawler.signals.log_signal.connect(self._append_log)
                    crawler.signals.progress_signal.connect(self._update_progress)
                    crawler.signals.crawl_finished_signal.connect(self._on_crawl_finished)
        finally:
            self._dual_logs_on = on

    def _source_key(self, index=None) -> str:
        """下拉框下标 → 平台 key：平台判定统一走这里，不再用魔法数字"""
        i = self._current_source if index is None else index
        return self.SOURCE_KEYS[i] if 0 <= i < len(self.SOURCE_KEYS) else ""

    def _on_source_changed(self, index):
        """切换数据来源"""
        old_crawler = self.crawler
        old_index = self._current_source
        # 切换前先把已有数据落盘（不足 10 条未触发自动保存时避免丢失）
        if old_index != index:
            self._auto_save("切换平台")
        self._current_source = index
        key = self._source_key(index)
        self.crawler = self._by_key[key]
        self._append_log(f"已切换到：{self.SOURCE_LABELS[key]} ({self.SOURCE_DOMAINS[key]})")
        
        # 切换时关闭旧爬虫的浏览器（释放 Chrome 配置目录锁）
        # 双标签模式下企知道/爱企查共用一个浏览器，不能关
        dual = self._dual_enabled()
        if old_crawler is not self.crawler and old_crawler.driver is not None:
            keep = dual and old_crawler in (self.qizhidao_crawler, self.aiqicha_crawler)
            if not keep:
                self._append_log("关闭上一个数据源的浏览器...")
                try:
                    old_crawler.close_browser()
                except Exception:
                    old_crawler.driver = None
        
        # 更新窗口标题
        self._update_window_title()
        # 详情页开关只对风鸟有意义
        if hasattr(self, "rb_detail_check"):
            self.rb_detail_check.setVisible(key == "riskbird")
        # 双标签开关：风鸟不可用
        self._sync_dual_tab_enabled()
        if self._dual_enabled():
            self._append_log(
                "⚡ 双标签已开：本次将同时采集企知道（单数）+ 爱企查（双数），"
                "上面的数据源选择只决定“登录/打开网页”默认操作哪个平台"
            )
        # 刷新各平台已采集条数显示，并提示续采起点
        self._update_platform_counts()
        _cnt = len(self.crawler.results)
        _g = self._get_global_index()
        self._append_log(f"{self.SOURCE_LABELS[key]} 平台已采集 {_cnt} 条；全局累计 {_g} 条，点开始采集将从第 {_g + 1} 条继续")
    
    def _update_window_title(self):
        """窗口标题固定为工具名（不随数据源切换而变）"""
        self.setWindowTitle(self.APP_TITLE)
        # 同时更新界面内的大标题
        if hasattr(self, 'title_label'):
            self.title_label.setText(self.APP_TITLE)
        
    def _show_login(self):
        dlg = LoginDialog(self.crawler, self)
        dlg.exec_()
    
    def _open_website(self):
        """打开当前选择的网站（后台线程启动浏览器，不阻塞 GUI）"""
        self.open_web_btn.setEnabled(False)
        self.open_web_btn.setText("加载中...")
        QApplication.processEvents()
        # 开始计时：“打开网页很慢”要能答出到底慢在哪一步（登录态副本/驱动/附加/首页导航）
        self._open_started = time.monotonic()
        
        dual = self._dual_enabled()
        # 关闭其他爬虫的浏览器（避免 Chrome 配置目录锁定冲突）
        for c in [self.riskbird_crawler, self.qizhidao_crawler, self.aiqicha_crawler]:
            if dual and c in (self.qizhidao_crawler, self.aiqicha_crawler):
                continue   # 双标签：这两个共用一个浏览器，不能关
            if c is not self.crawler and c.driver is not None:
                try:
                    c.close_browser()
                except Exception:
                    c.driver = None
        
        # 用后台线程启动浏览器，避免卡死 GUI
        targets = (self.qizhidao_crawler, self.aiqicha_crawler) if dual else None
        self._open_thread = _OpenBrowserWorker(self.crawler, dual_targets=targets)
        self._open_thread.finished.connect(self._on_open_finished)
        self._open_thread.log_signal.connect(self._append_log)
        self._open_thread.start()

    def _on_open_finished(self, success: bool, msg: str):
        """浏览器启动完成回调"""
        self.open_web_btn.setEnabled(True)
        self.open_web_btn.setText("打开网页")
        spent = time.monotonic() - getattr(self, "_open_started", time.monotonic())
        if spent >= 10:
            self._append_log(f"⏱ 本次“打开网页”共用了 {spent:.1f} 秒（超过 10 秒才会写这一行）")
        
        if success:
            if self._dual_running or (self._dual_enabled() and getattr(self, "_open_thread", None)
                                       and getattr(self._open_thread, "dual_targets", None)):
                self._append_log("已打开双标签：企知道 + 爱企查各一个窗口，如需登录请分别在两个窗口完成")
                return
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
            
            site_name = self.SOURCE_DOMAINS.get(self._source_key(), "未知平台")
            self._append_log(f"已打开 {site_name} 网站")
        else:
            self._append_log(f"打开网站失败：{msg}")

    def _on_login_status(self, success: bool, msg: str):
        """风鸟登录状态信号：界面上的登录/未登录标识已按需求去掉，只写进日志"""
        self._append_log(("✅ 已检测到登录态：" if success else "⚠ 未检测到登录态：") + (msg or ""))

    def _on_manual_check(self, msg: str):
        self.statusBar().showMessage(msg, 5000)

    # ── 采集控制 ───────────────────────────────
    def _start_crawl(self):
        if not self._companies:
            QMessageBox.warning(self, "提示", "请先选择 Excel 文件")
            return

        dual = self._dual_enabled()
        if dual:
            # 双标签：风鸟的浏览器必须让位（它启动时会清理/独占 Chrome），
            # 企知道与爱企查共用一个带调试端口的浏览器
            if self.riskbird_crawler.driver is not None:
                try:
                    self.riskbird_crawler.close_browser()
                except Exception:
                    self.riskbird_crawler.driver = None
        else:
            # 关闭其他爬虫的浏览器（避免 Chrome 配置目录锁定冲突）
            for c in [self.riskbird_crawler, self.qizhidao_crawler, self.aiqicha_crawler]:
                if c is not self.crawler and c.driver is not None:
                    try:
                        c.close_browser()
                    except Exception:
                        c.driver = None

        # 设置延迟（双标签时两个平台都要设）
        try:
            delay = float(self.delay_spin.text())
            delay = max(0.1, delay)
        except ValueError:
            delay = 0.5
        for c in ([self.qizhidao_crawler, self.aiqicha_crawler] if dual else [self.crawler]):
            c.delay_between = delay
        # 同步风鸟详情页开关（避免切换平台后残留设置与实际不符）
        self.riskbird_crawler.force_detail = self.rb_detail_check.isChecked()

        # 断点续采：续采起点由「名单集合差集」决定，不再拿已采条数当索引（进程一重启就归零）。
        # 三个爬虫的契约 crawl(companies, start_index=0) 不变：这里只改“交给它哪一份名单”。
        # 双标签的待采只看企知道+爱企查两边（风鸟的历史结果不计入单双数分工）。
        self._companies_snapshot = self._merge_snapshot()
        snapshot = self._companies_snapshot
        pending = self._todo_companies(("qizhidao", "aiqicha") if dual else None)
        if not pending:
            QMessageBox.information(
                self, "提示",
                f"名单 {len(snapshot)} 家已全部采完或已确认无结果，无需再采。\n\n"
                f"如需重采全部公司，请先点「清空文件」再重新选择名单文件。")
            return
        # 首次开始采集时创建桌面会话文件夹（以第一次开始采集的时间命名）
        folder = self._get_or_create_session_folder()
        # 爱企查“认不出结果条目”时把页面实况存到会话目录：改版和平台静默拒绝
        # 在日志里长得一样，处置却完全相反，没现场文件就只能靠猜
        try:
            self.aiqicha_crawler.diag_dir = str(folder)
        except Exception:
            pass
        self._register_task(folder)
        self._ensure_checkpoint(folder)
        self._last_autosave_bucket = self._get_global_index() // 10
        skipped = len(snapshot) - len(pending)
        if skipped:
            self._append_log(f"↻ 续采：跳过已完成/无结果 {skipped} 家，本次待采 {len(pending)} 家"
                             f"（名单共 {len(snapshot)} 家）")
        else:
            self._append_log(f"▶ 本次待采 {len(pending)} 家（名单共 {len(snapshot)} 家）")
        self._refresh_company_count()

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

        if dual:
            self._dual_running = True
            self._attach_dual_logs(True)
            self._append_log("⚡ 双标签并行：正在准备共享浏览器与两个会话（首次启动约十几秒）…")
            self._crawl_thread = DualCrawlWorker(
                self.qizhidao_crawler, self.aiqicha_crawler, snapshot, pending)
            self._crawl_thread.progress.connect(self._update_progress)
            self._crawl_thread.log_signal.connect(self._append_log)
            self._crawl_thread.setup_failed.connect(self._on_dual_setup_failed)
            self._crawl_thread.finished.connect(self._on_dual_finished)
            self._crawl_thread.start()
            self.statusBar().showMessage("双标签采集中（企知道 + 爱企查）...")
            return

        self._crawl_thread = CrawlWorker(self.crawler, pending, 0)
        # 上一轮会话/切换数据源留下的空白窗口白占内存，开跑前洗一次
        # （双标签模式在 setup_dual 里已经洗过，这里只管单平台）
        if HAS_DUAL and _dual.is_shared_chrome_alive():
            _dual.cleanup_blank_tabs(self._append_log,
                                     keep_ids=[self.aiqicha_crawler.pin_tab_id])
        self._crawl_thread.finished.connect(self._on_crawl_finished)
        self._crawl_thread.start()

        self.statusBar().showMessage("采集中...")

    def _active_crawlers(self):
        """当前受“暂停/继续/停止”控制的爬虫"""
        if self._dual_running:
            return [self.qizhidao_crawler, self.aiqicha_crawler]
        return [self.crawler]

    def _pause_crawl(self):
        for c in self._active_crawlers():
            c.pause_crawl()
        self.pause_btn.setEnabled(False)
        self.resume_btn.setEnabled(True)
        self.statusBar().showMessage("已暂停")
        self._pause_timer()
        self._update_platform_counts()
        self._cp_flush("点击暂停")
        self._auto_save("点击暂停")

    def _resume_crawl(self):
        for c in self._active_crawlers():
            c.resume_crawl()
            # 如果这个爬虫正卡在登录闸门里，“继续采集”就等于“我登录好了，重新验一下”
            c.resolve_login_gate("login_done")
        self.pause_btn.setEnabled(True)
        self.resume_btn.setEnabled(False)
        self.statusBar().showMessage("双标签采集中..." if self._dual_running else "采集中...")
        self._resume_timer()

    def _stop_crawl(self):
        for c in self._active_crawlers():
            c.stop_crawl()
            c.resolve_login_gate("stop")   # 卡在登录闸门里时要能立刻退出
        self.stop_btn.setEnabled(False)
        self.pause_btn.setEnabled(False)
        self.resume_btn.setEnabled(False)
        self.start_btn.setEnabled(True)  # 允许重新开始
        self.export_btn.setEnabled(True)  # 允许导出
        self._stop_timer()
        self._clear_blocked_state()
        self._update_platform_counts()
        # 输出用时摘要
        count = sum(len(c.results) for c in self._active_crawlers())
        self._append_log(f" {self._get_time_summary(count)}")
        self._cp_flush("点击停止")
        self._auto_save("点击停止")
        self._finish_task(_tasks.TASK_STATUS_STOPPED, self._get_global_index())

    def _on_crawl_finished(self):
        if self._dual_running:
            return   # 双标签由 DualCrawlWorker 统一收尾，不响应单平台的完成信号
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.select_file_btn.setEnabled(True)
        self.export_btn.setEnabled(bool(self._all_results()))
        self._stop_timer()
        self._clear_blocked_state()
        count = len(self.crawler.results)
        total_all = self._get_global_index()
        self.statusBar().showMessage(f"采集完成 — 本平台 {count} 条，全部平台合计 {total_all} 条")
        self.progress_bar.setFormat(f"完成 — 合计 {total_all} 条数据")
        self._update_platform_counts()
        # 输出用时摘要
        self._append_log(f"✅ {self._get_time_summary(count)}")
        self._cp_flush("采集完成")
        self._auto_save("采集完成")
        if HAS_YZD:
            self._finish_task(_tasks.TASK_STATUS_DONE, total_all)
            self._maybe_auto_import("采集完成")

    def _on_dual_setup_failed(self, msg: str):
        """双标签环境没准备好（共享浏览器/会话附加失败）"""
        self._dual_running = False
        self._attach_dual_logs(False)
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.pause_btn.setEnabled(False)
        self.resume_btn.setEnabled(False)
        self.select_file_btn.setEnabled(True)
        self._stop_timer()
        self._clear_blocked_state()
        self.statusBar().showMessage("双标签启动失败")
        self.progress_bar.setFormat("双标签启动失败")
        self._append_log(f"❌ 双标签启动失败：{msg}")
        self._append_log("ℹ 可取消勾选“双标签并行采集”后按单平台重试")
        QMessageBox.warning(self, "双标签启动失败",
                            f"{msg}\n\n可取消勾选“双标签并行采集”，改按单平台采集。")

    def _on_dual_finished(self):
        """双标签并行采集结束（两个会话都跑完）"""
        self._dual_running = False
        self._attach_dual_logs(False)
        qn = len(self.qizhidao_crawler.results)
        an = len(self.aiqicha_crawler.results)
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.pause_btn.setEnabled(False)
        self.resume_btn.setEnabled(False)
        self.select_file_btn.setEnabled(True)
        self.export_btn.setEnabled(bool(self._all_results()))
        self._stop_timer()
        self._clear_blocked_state()
        total_all = self._get_global_index()
        self.statusBar().showMessage(f"双标签采集完成 — 企知道 {qn} 条 / 爱企查 {an} 条，合计 {total_all} 条")
        self.progress_bar.setFormat(f"完成 — 企知道 {qn} + 爱企查 {an}，合计 {total_all} 条")
        self._update_platform_counts()
        self._append_log(f"✅ 双标签并行结束：企知道 {qn} 条、爱企查 {an} 条 {self._get_time_summary(qn + an)}")
        self._cp_flush("双标签采集完成")
        self._auto_save("双标签采集完成")
        if HAS_YZD:
            self._finish_task(_tasks.TASK_STATUS_DONE, total_all)
            self._maybe_auto_import("双标签采集完成")
        self._append_log("ℹ 共享浏览器保持打开（下次开双标签直接复用）；如不需要可手动关掉它")

    def _update_progress(self, current: int, total: int):
        pct = int(current / total * 100) if total else 0
        self.progress_bar.setValue(pct)
        self.progress_bar.setFormat(f"采集中... {current}/{total} ({pct}%)  ⏱ {self._format_time(self._crawl_elapsed)}")
        # 刷新各平台已采集条数显示
        self._update_platform_counts()
        # 刚采完一条就把状态写进台账（台账内部每采满 5 条落一次盘）：
        # 强杀进程最多丢最后几条，不会整批重采
        self._cp_mark_new()
        self._refresh_company_count()
        # 每采集 10 条自动保存一次
        total_done = self._get_global_index()
        bucket = total_done // 10
        if bucket > self._last_autosave_bucket:
            self._last_autosave_bucket = bucket
            self._auto_save(f"每10条自动保存·已{total_done}条")

    # ── 平台拦截（安全验证/风控）提示 ──────────────
    def _on_blocked(self, platform: str, reason: str):
        """采集线程发现页面被安全验证拦下或正在被限流：醒目提示用户

        采集线程自己会在 block_guard 里原地等恢复，这里只负责把“要不要你动手”
        这件事说到脸上：以前只会在一堆日志里夹一行，用户看到的只是“卡死了”。
        reason 以 ⏳ 开头表示没有验证页、只是平台限流：此时叫用户去验证是误导，
        工具已在退避实测，不需要人动手。
        """
        throttle = str(reason or "").startswith("⏳")
        found = False
        for item in self._blocked_info:
            if item[0] == platform:
                item[1] = reason or item[1]
                found = True
                break
        if not found:
            self._blocked_info.append([platform, reason or "页面被平台拦截"])
        self._refresh_blocked_banner()
        self._append_log(f"🛑【{platform}】{reason}")
        if throttle:
            self._append_log("   名单已暂停推进（不会写入空记录）。平台在限流，工具正在退避重试，"
                             "实测能拿到数据会自动继续；若浏览器里有验证页请顺手完成。")
        else:
            self._append_log("   名单已暂停推进（不会写入空记录）。请在浏览器窗口完成验证，通过后自动继续。")
        # 采集正在跑且用户没主动暂停时，才由拦截接管计时（否则验证等待会把平均耗时算虚高）
        if self.stop_btn.isEnabled() and not self.resume_btn.isEnabled():
            self._pause_timer()
        self._show_blocked_dialog(throttle)

    def _on_block_cleared(self, platform: str):
        """人工完成验证/登录，采集已自动继续"""
        self._blocked_info = [it for it in self._blocked_info if it[0] != platform]
        self._login_info = [it for it in self._login_info if it[0] != platform]
        self._append_log(f"✅【{platform}】已恢复，采集继续")
        if not (self._blocked_info or self._login_info):
            self._hide_blocked_dialog()
            if self.stop_btn.isEnabled() and not self.resume_btn.isEnabled():
                self._resume_timer()
        self._refresh_blocked_banner()

    def _on_platform_handoff(self, platform: str, reason: str):
        """某一路采集撑不住已放弃等人工（爱企查被拦超时）：把它的待处理提示撤掉

        名单已经交给企知道继续采，工具仍在跑，不该再挂“等你去验证”的红条与弹窗。
        """
        self._blocked_info = [it for it in self._blocked_info if it[0] != platform]
        self._login_info = [it for it in self._login_info if it[0] != platform]
        self._append_log(f"↪【{platform}】不再等人工处理：{reason}；剩下的公司已转给企知道接着采")
        if not (self._blocked_info or self._login_info):
            self._hide_blocked_dialog()
            if self.stop_btn.isEnabled() and not self.resume_btn.isEnabled():
                self._resume_timer()
        self._refresh_blocked_banner()

    def _crawler_by_name(self, platform: str):
        for name, crawler in zip(self.PLATFORM_NAMES, self._all_crawlers):
            if name == platform:
                return crawler
        return None

    def _on_login_required(self, platform: str, reason: str):
        """采集线程发现未登录：弹窗问用户是去登录、还是不登录继续采

        采集线程会在登录闸门里原地等这个选择，所以这里用模态框不会卡死采集：
        名单没推进、也没写空记录，用户怎么选都不会丢数据。
        以前未登录也能直接开采，整份名单会被采成“只有公司名”的空数据。
        """
        crawler = self._crawler_by_name(platform)
        if crawler is None:
            return
        found = False
        for item in self._login_info:
            if item[0] == platform:
                item[1] = reason or item[1]
                found = True
                break
        if not found:
            self._login_info.append([platform, reason or "未检测到登录状态"])
        self._refresh_blocked_banner()
        # 等人工登录期间不计入采集用时（否则平均耗时会被等待撑大）
        if self.stop_btn.isEnabled() and not self.resume_btn.isEnabled():
            self._pause_timer()
        box = QMessageBox(self)
        box.setWindowTitle("未检测到登录状态")
        box.setIcon(QMessageBox.Warning)
        box.setText(f"【{platform}】{reason}")
        box.setInformativeText(
            "未登录时详情页里的法定代表人、注册资本、邮箱、地址这些字段全是空的（实测只有电话能采到），"
            "继续采就是把整份名单白烧掉。\n\n"
            "· 去浏览器登录（推荐）：在浏览器窗口里登录该平台，登录成功后点界面上的“继续采集”，"
            "会从卡住的这一家重新采；\n"
            "· 仍然继续采集：不等登录了，直接往下采；\n"
            "· 停止采集：本轮结束，已采到的数据不受影响。"
        )
        b_login = box.addButton("去浏览器登录（推荐）", QMessageBox.AcceptRole)
        b_cont = box.addButton("仍然继续采集", QMessageBox.ActionRole)
        b_stop = box.addButton("停止采集", QMessageBox.RejectRole)
        box.setDefaultButton(b_login)
        box.exec_()
        clicked = box.clickedButton()
        if clicked is b_cont:
            # 闸门放行后会自己发“已恢复”信号，界面提示交给它统一收起
            crawler.resolve_login_gate("continue")
            self._append_log(f"ℹ【{platform}】按你的选择继续采集（未登录时字段可能仍然是空的）")
        elif clicked is b_stop:
            crawler.resolve_login_gate("stop")
            self._stop_crawl()
        else:
            # 选了“去登录”或直接关掉弹窗：不回应闸门，采集线程继续原地等，
            # 出口是界面上的“继续采集”（_resume_crawl 会替用户回答 login_done）
            self.pause_btn.setEnabled(False)
            self.resume_btn.setEnabled(True)
            self.statusBar().showMessage(f"等待 {platform} 登录…登录完成后点“继续采集”")
            self._append_log(f"⏳【{platform}】等你在浏览器里登录，登录完成后点“继续采集”接着采")

    def _refresh_blocked_banner(self):
        if not hasattr(self, "blocked_lbl"):
            return
        parts = []
        if self._blocked_info:
            names = "、".join(it[0] for it in self._blocked_info)
            reason = str(self._blocked_info[-1][1] or "")
            if reason.startswith("⏳"):
                # 限流：页面上没有验证件，叫用户“完成验证”会让人对着正常页面找滑块
                tail = "（平台限流中，工具已退避实测，能拿到数据会自动继续）"
            else:
                tail = "（请在浏览器窗口完成验证，没有验证页时稍候会自动恢复）"
            parts.append(f"{names} 被平台拦截：{reason}{tail}")
        if self._login_info:
            lnames = "、".join(it[0] for it in self._login_info)
            parts.append(f"{lnames} 未登录：{self._login_info[-1][1]}"
                         f"（请在浏览器窗口登录后点“继续采集”）")
        if parts:
            waiting = "、".join(sorted({it[0] for it in self._blocked_info + self._login_info}))
            self.blocked_lbl.setText(
                "⚠ " + "；".join(parts) + " —— 名单已暂停推进，不会写入空记录"
            )
            self.blocked_lbl.show()
            self.statusBar().showMessage(f"等待人工处理：{waiting}")
        else:
            self.blocked_lbl.hide()
            if self._crawl_start_time:
                self.statusBar().showMessage(
                    "双标签采集中..." if self._dual_running else "采集中...")

    @staticmethod
    def _blocked_dialog_message(throttle: bool):
        """拦截提示框的标题与正文（纯函数，便于离线核对两套口径）

        throttle=True 时页面上根本没有验证件，只是平台临时限流：叫用户去验证会
        让人对着一个正常页面找不存在的滑块，所以文案必须分开。
        """
        if throttle:
            return "平台正在限流", (
                "平台暂时不给数据（页面上并没有要你填的验证码）。\n\n"
                "工具已在退避重试，并会实测“能不能真拿到数据”才继续，通常几分钟内自行恢复。\n"
                "这时不需要关工具、也不需要重开浏览器；如果浏览器里确实有验证页，去点几下完成即可。\n\n"
                "名单已暂停推进，不会把空记录写进结果；建议适当调大“采集间隔”再续采。"
            )
        return "需要人工验证", (
            "采集页面被平台安全验证拦住了。\n\n"
            "请切到浏览器窗口，手动完成验证（滑块/图形验证码）。\n"
            "验证通过后采集会自动继续，不需要在本窗口点任何按钮。\n"
            "如果验证已经做完了但采集仍未前进，工具会自动重试（它现在会实测能不能拿到数据，"
            "而不是只看验证页还在不在）。\n\n"
            "名单已暂停推进，不会把空记录写进结果。"
        )

    def _show_blocked_dialog(self, throttle: bool = False):
        """用非模态提示框：模态框会冻结本窗口，而用户此刻需要去的是浏览器窗口"""
        if self._blocked_box is not None:
            self._blocked_box.raise_()
            return
        title, body = self._blocked_dialog_message(throttle)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(title)
        box.setText(body)
        box.setStandardButtons(QMessageBox.Ok)
        box.finished.connect(self._hide_blocked_dialog)
        self._blocked_box = box
        box.show()

    def _hide_blocked_dialog(self):
        if self._blocked_box is not None:
            box, self._blocked_box = self._blocked_box, None
            box.close()

    def _clear_blocked_state(self):
        """采集结束/停止时清掉拦截与登录提示，避免红条残在界面上"""
        self._blocked_info = []
        self._login_info = []
        self._hide_blocked_dialog()
        self._refresh_blocked_banner()

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
        """合并三个平台的全部采集结果（按 MERGE_ORDER：风鸟→企知道→爱企查）

        拿的是每个爬虫的快照（连 dict 一起拷），采集线程同时追加也不会写坏。
        这里的顺序不能随便改：同名公司合并时先出现的非空字段会胜出，
        与下拉框的展示顺序无关。
        """
        combined = []
        for key in self.MERGE_ORDER:
            c = self._by_key[key]
            if hasattr(c, "snapshot_results"):
                combined.extend(c.snapshot_results())
            else:
                combined.extend([dict(r) for r in c.results])
        return combined

    def _get_global_index(self) -> int:
        """全局进度 = 所有平台已采集条数之和（只给进度条/统计用，续采起点靠台账算）"""
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

    # ── 采集任务登记（“导入其他”要列历史任务，靠这份索引）──
    def _register_task(self, folder):
        """一次“开始采集”对应一个任务：任务 id = 会话文件夹名，任务名取采集文档名

        重复点“开始采集”（暂停后继续、采完又接着跑同一批文件）走同一个任务，
        所以这里必须保留最早那次 started_at，不能把历史任务的采集时间刷成现在。
        """
        if not HAS_YZD or folder is None:
            return
        try:
            tid = Path(folder).name
            task = _tasks.new_task(tid, self._excel_paths, str(folder),
                                   count=self._get_global_index())
            existing = next((t for t in _tasks.load_tasks() if t.get("id") == tid), None)
            if existing:
                task["started_at"] = existing.get("started_at") or task["started_at"]
            _tasks.upsert_task(task)
            self._task_id = tid
            if existing is None:
                self._append_log(f"📁 已登记采集任务：{task['name']}")
        except Exception as e:
            self._append_log(f"⚠ 采集任务登记失败：{e}")

    def _update_task_files(self):
        """采集途中加文件：不拆新任务，只把当前任务的任务名/文件列表补齐"""
        if not HAS_YZD or not self._task_id:
            return
        try:
            _tasks.update_task(self._task_id,
                               name=_tasks.make_task_name(self._excel_paths),
                               files=[os.path.basename(p) for p in self._excel_paths])
        except Exception as e:
            self._append_log(f"⚠ 采集任务文件列表更新失败：{e}")

    def _finish_task(self, status: str, count: int):
        """采集完成/停止：补上结束时间与条数（“导入其他”列表里能看到采了多少条）"""
        if not HAS_YZD or not self._task_id:
            return
        try:
            if _tasks.finish_task(self._task_id, status, count):
                self._append_log(f"📁 任务「{status}」已记录：合计 {count} 条")
        except Exception as e:
            self._append_log(f"⚠ 任务结束状态写入失败：{e}")

    def _update_platform_counts(self):
        """刷新界面上各平台已采集条数显示"""
        if not hasattr(self, "platform_count_lbl"):
            return
        counts = [len(c.results) for c in self._all_crawlers]
        parts = [f"{name}: {cnt} 条" for name, cnt in zip(self.PLATFORM_NAMES, counts)]
        total = sum(counts)
        self.platform_count_lbl.setText("    ".join(parts) + f"    合计: {total} 条")

    def _start_autosave(self, combined: list, folder: str, reason: str = ""):
        """起一个后台写盘线程（引用存在 self 上，否则 QThread 被 GC 会直接崩）"""
        worker = AutoSaveWorker(combined, folder,
                                template_dirs=[folder, self._get_last_import_dir()])
        worker.logged.connect(self._append_log)
        worker.finished_save.connect(self._on_autosave_finished)
        self._autosave_worker = worker
        worker.start()
        if reason:
            self._append_log(f"💾 自动保存（{reason}）：共 {len(combined)} 条原数据 → 两份文件")

    def _on_autosave_finished(self, ok: bool, summary: str):
        self._append_log(("✅ " if ok else "⚠ ") + summary)
        if ok and HAS_YZD and self._task_id:
            # 条数跟着自动保存同步进任务索引，“导入其他”列表里的条数才不会是旧值
            try:
                _tasks.update_task(self._task_id, count=self._get_global_index())
            except Exception:
                pass
        pending, self._autosave_pending = self._autosave_pending, None
        if pending:
            combined, folder, reason = pending
            self._start_autosave(combined, folder, reason or "补保存")

    # ── 自动保存 ──────────────────────────────
    def _auto_save(self, reason: str = ""):
        """自动保存 = 跟“导出 Excel”同效果：一次写两份文件，不再按平台分文件

        桌面会话文件夹里固定两个名字（反复覆盖，不堆文件）：
          企业数据_全部平台.xlsx      原数据（三个平台合并）
          清洗后客户库入库数据.xlsx  按入库模板清洗后的数据

        并发：写盘放后台线程；上一轮还在写时只保留最后一次请求（而不是排队），
        这样“暂停/停止/采集完成”那一次必然落盘，也不会堆积重复写盘。"""
        try:
            combined = self._all_results()
            if not combined:
                return
            folder = self._get_or_create_session_folder()
            if folder is None:
                return
            if self._autosave_worker is not None and self._autosave_worker.isRunning():
                self._autosave_pending = (combined, str(folder), reason)
                return
            self._start_autosave(combined, str(folder), reason)
        except Exception as e:
            self._append_log(f"⚠ 自动保存失败：{e}")

    # ── 导出（合并风鸟/企知道/爱企查全部数据 + 生成清洗后入库数据） ──
    def _export_excel(self):
        # 后台自动保存可能在写同一批数据：先让它收尾，避免两个线程同时写盘
        if self._autosave_worker is not None and self._autosave_worker.isRunning():
            self._append_log("⏳ 自动保存正在写盘，等它完成后开始导出…")
            self._autosave_worker.wait(20000)
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
            detail = " / ".join(f"{name} {len(c.results)} 条"
                                 for name, c in zip(self.PLATFORM_NAMES, self._all_crawlers))
            cleaned_path = self._export_cleaned_library(combined, path)
            msg = (
                f"已合并导出 {len(combined)} 条数据到:\n{path}\n\n"
                f"{detail}"
            )
            if cleaned_path:
                msg += f"\n\n已另存清洗后入库数据到:\n{cleaned_path}"
            QMessageBox.information(self, "导出成功", msg)
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def _export_cleaned_library(self, combined: list, merged_path: str) -> str:
        """手动导出时另存一份带时间戳的清洗后入库数据（自动保存用固定名，两者不相互覆盖）"""
        if not HAS_CLEANER:
            self._append_log("⚠ 清洗模块未加载，跳过入库数据生成")
            return ""
        merged_dir = os.path.dirname(merged_path) or "."
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        cleaned_path = os.path.join(merged_dir, f"清洗后客户库入库数据_{stamp}.xlsx")
        try:
            self._append_log("🧹 开始生成清洗后客户库入库数据…")
            if write_cleaned_excel(combined, cleaned_path,
                                   template_dirs=[merged_dir, self._get_last_import_dir()],
                                   log=self._append_log):
                return cleaned_path
            return ""
        except PermissionError:
            self._append_log("⚠ 入库文件正被 Excel/WPS 占用，本次未能写出")
            return ""
        except Exception as e:
            self._append_log(f"⚠ 生成清洗后入库数据失败：{e}")
            return ""

    def _get_last_import_dir(self) -> str:
        """返回最近一次选择文件所在目录，用于就近查找入库模板。"""
        files = getattr(self, "_excel_paths", None) or []
        return os.path.dirname(files[-1]) if files else ""

    def _write_merged_excel(self, results: list, filepath: str):
        """薄封装：与自动保存共用模块级写盘函数，保证两条路径出的表完全一致"""
        return write_merged_excel(results, filepath, log=self._append_log)

    # ── 清洗数据（其他渠道的名单 → 入库格式）──────
    def _clean_external_data(self):
        """「清洗数据」入口：多选外部表格 → 智能匹配列 → 清洗 → 写出入库格式文件"""
        if not HAS_CLEANER:
            QMessageBox.warning(self, "提示", "清洗模块未加载，无法清洗数据")
            return
        if self._cleaning:
            QMessageBox.information(self, "提示", "正在清洗数据，请等当前清洗完成")
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择要清洗的数据文件（可多选）", self._get_last_import_dir(),
            "表格文件 (*.xlsx *.xlsm *.csv);;Excel (*.xlsx *.xlsm);;CSV (*.csv);;所有文件 (*)")
        if not paths:
            return
        self._append_log(f"🧹 清洗数据：选中 {len(paths)} 个文件")

        default_dir = os.path.dirname(paths[0]) or str(self._get_desktop_dir())
        out, _ = QFileDialog.getSaveFileName(
            self, "清洗结果保存为（与自动保存的入库文件同格式）",
            os.path.join(default_dir, CLEAN_FILE_NAME), "Excel 文件 (*.xlsx)")
        if not out:
            self._append_log("已取消清洗数据（未选保存位置）")
            return
        if not out.lower().endswith(".xlsx"):
            out += ".xlsx"
        if os.path.exists(out):
            if QMessageBox.question(self, "覆盖已有文件",
                                    f"目标文件已存在，确认覆盖？\n{out}",
                                    QMessageBox.Yes | QMessageBox.No,
                                    QMessageBox.No) != QMessageBox.Yes:
                self._append_log("已取消清洗数据（不覆盖已有文件）")
                return

        # 清洗也要走后台：多个大文件读下来几十秒，直接在 GUI 线程里做就是白屏
        self._cleaning = True
        self.setEnabled(False)
        prog = YzdProgressDialog(None, "正在清洗数据")
        prog.set_text(f"正在清洗 {len(paths)} 个文件…", "清洗在后台进行，请勿关闭程序。")
        self._clean_progress = prog
        prog.show()

        worker = ExternalCleanWorker(paths, out, template_dirs=[
            os.path.dirname(out), default_dir, self._get_last_import_dir()])
        worker.logged.connect(self._append_log)
        worker.finished_clean.connect(self._on_clean_finished)
        self._clean_worker = worker
        worker.start()

    def _on_clean_finished(self, ok: bool, msg: str):
        self._cleaning = False
        self.setEnabled(True)
        if self._clean_progress is not None:
            try:
                self._clean_progress.allow_close()
                self._clean_progress.close()
            except Exception:
                pass
            self._clean_progress = None
        self.activateWindow()
        self._append_log(("✅ " if ok else "❌ ") + msg.replace("\n", "  "))
        if ok:
            QMessageBox.information(self, "清洗完成", msg)
        else:
            QMessageBox.warning(self, "清洗未完成", msg)

    # ── 云展动力：绑定账号 / 导入客户库 ──────
    def _refresh_yzd_status(self):
        """把绑定态反到界面上：启动时从本地文件恢复，绑完/换号后刷新"""
        if not hasattr(self, "yzd_status_lbl"):
            return
        if not HAS_YZD:
            self.yzd_status_lbl.setText("云展模块未加载（缺 yzd_client / task_registry）")
            self.yzd_status_lbl.setStyleSheet(
                "QLabel { padding: 4px 10px; border-radius: 4px; background: #fdecea; color: #c62828; }")
            self.bind_yzd_btn.setEnabled(False)
            self.import_yzd_btn.setEnabled(False)
            return
        if self._yzd_phone:
            self.yzd_status_lbl.setText(f"已绑定云展账号：{self._yzd_phone}")
            self.yzd_status_lbl.setStyleSheet(
                "QLabel { padding: 4px 10px; border-radius: 4px; background: #e8f5e9; color: #2e7d32; }")
            self.bind_yzd_btn.setText("更换绑定账号")
        else:
            self.yzd_status_lbl.setText("未绑定云展动力账号")
            self.yzd_status_lbl.setStyleSheet(
                "QLabel { padding: 4px 10px; border-radius: 4px; background: #f0f0f0; color: #999; }")
            self.bind_yzd_btn.setText("绑定云展动力账号")

    def _bind_yzd_account(self):
        """接口3：先本地校格式（省一次往返，提示也能即时出现），后端 msg 直接当用户文案"""
        if not HAS_YZD:
            QMessageBox.warning(self, "提示", "云展模块未加载，无法绑定账号")
            return
        if self._yzd_worker is not None and self._yzd_worker.isRunning():
            return
        tip = "请输入云展动力手机号"
        if self._yzd_phone:
            tip += f"（当前绑定：{self._yzd_phone}）"
        phone, ok = QInputDialog.getText(self, "绑定云展动力账号", tip,
                                         QLineEdit.Normal, self._yzd_phone)
        if not ok:
            return
        phone = (phone or "").strip().replace(" ", "").replace("-", "")
        if not phone or not _yzd.PHONE_RE.match(phone):
            QMessageBox.warning(self, "提示", "请输入正确的手机号")
            return
        self.bind_yzd_btn.setEnabled(False)
        self.statusBar().showMessage("正在校验云展手机号…")
        self._append_log(f"🔗 正在校验云展动力账号 {phone} …")
        self._yzd_worker = YzdBindWorker(phone)
        self._yzd_worker.finished_bind.connect(self._on_bind_finished)
        self._yzd_worker.start()

    def _on_bind_finished(self, ok: bool, msg: str):
        self.bind_yzd_btn.setEnabled(HAS_YZD)
        if ok:
            self._yzd_phone = msg
            saved = _tasks.save_account(msg)
            self._append_log(f"✅ 已绑定云展动力账号：{msg}"
                             + ("" if saved else "（本地保存失败，下次启动需重新绑定）"))
            QMessageBox.information(self, "绑定成功", f"已绑定云展动力账号：{msg}")
        else:
            # 后端文案就是“请输入正确的手机号”/“云展账号不存在”，不自造一套
            self._append_log(f"⚠ 绑定云展账号失败：{msg}")
            QMessageBox.warning(self, "绑定失败", msg)
        self._refresh_yzd_status()
        if not self.stop_btn.isEnabled():
            self.statusBar().showMessage("就绪")

    def _open_yzd_import(self):
        """“导入云展客户库”入口：未绑定先拦下，绑定后进选分组弹窗"""
        if not HAS_YZD:
            QMessageBox.warning(self, "提示", "云展模块未加载，无法导入客户库")
            return
        if self._yzd_importing:
            QMessageBox.information(self, "提示", "正在导入云展客户库，请等当前导入完成")
            return
        if not self._yzd_phone:
            QMessageBox.warning(self, "提示", "请先绑定云展动力账号")
            return
        # 把装上本功能之前的历史会话补登进来，不然那些采好的数据永远列不出来
        try:
            added = _tasks.backfill_tasks()
            if added:
                self._append_log(f"📁 已补登记历史采集会话 {added} 个，可在「导入其他」里选中")
        except Exception as e:
            self._append_log(f"⚠ 历史会话补登记失败：{e}")
        dlg = YzdGroupDialog(self._yzd_phone, self)
        self._yzd_dialog = dlg
        if dlg.exec_() != QDialog.Accepted:
            return
        group = dlg.chosen_group or {}
        tasks = dlg.chosen_tasks if dlg.mode == YzdGroupDialog.MODE_OTHERS else None
        self._start_yzd_import(group, tasks)

    def _current_task_id(self):
        return self._session_folder.name if self._session_folder is not None else ""

    # ── 采集完自动导入云展客户库 ──────
    def _on_auto_import_toggled(self, checked: bool):
        """勾选时就先把分组选好：等采完了才弹窗问导哪个库，人早就不在电脑前了"""
        if not hasattr(self, "log_text"):
            return
        if not checked:
            self._auto_import_group = None
            self._append_log("已关闭「采集完自动导入云展客户库」，采集完成后需手动点导入")
            return
        if not HAS_YZD:
            self._revert_auto_import("云展模块未加载，无法自动导入客户库")
            return
        if self._yzd_importing or self._cleaning:
            self._revert_auto_import("当前有任务正在执行，请等它结束后再开启自动导入")
            return
        if not self._yzd_phone:
            self._revert_auto_import("请先点「绑定云展动力账号」，绑定后才能选自动导入的目标分组")
            return
        dlg = YzdGroupDialog(self._yzd_phone, self, pick_group_only=True)
        self._yzd_dialog = dlg
        if dlg.exec_() != QDialog.Accepted or not dlg.chosen_group:
            self._auto_import_group = None
            chk = getattr(self, "auto_import_check", None)
            if chk is not None:
                chk.blockSignals(True)
                chk.setChecked(False)
                chk.blockSignals(False)
            self._append_log("ℹ 没选定分组，“采集完自动导入”已取消")
            return
        self._auto_import_group = dlg.chosen_group
        flag = self._flag_from_group(self._auto_import_group)
        self._append_log(
            f"🤖 已设置采集完自动导入：分组「{self._auto_import_group.get('groupName') or ''}」"
            f"（{'国外' if flag else '国内'}客户库），采集完成后自动上传并在日志提示结果")

    def _revert_auto_import(self, msg: str):
        """开不了就把勾退回未勾选，别让界面显示“已开启”但实际不生效"""
        self._auto_import_group = None
        chk = getattr(self, "auto_import_check", None)
        if chk is not None:
            chk.blockSignals(True)
            chk.setChecked(False)
            chk.blockSignals(False)
        self._append_log(f"⚠ {msg}")
        QMessageBox.warning(self, "无法开启自动导入", msg)

    def _maybe_auto_import(self, reason: str):
        """采集正常结束时按勾选自动上传；点“停止采集”不走这里（人工中断不算“采集完”）"""
        chk = getattr(self, "auto_import_check", None)
        if chk is None or not chk.isChecked():
            return
        if self._yzd_importing:
            return
        group = self._auto_import_group or {}
        if not group.get("id"):
            self._append_log("⚠ 已勾选自动导入但没拿到分组，本次不自动上传；可手动点「导入云展客户库」")
            return
        if not self._all_results():
            self._append_log("ℹ 本次没采到数据，自动导入已跳过")
            return
        tid = self._current_task_id()
        if not tid:
            self._append_log("⚠ 本次采集没登记到任务索引（会话目录未创建），自动导入已跳过")
            return
        # 固定传本次会话（即使索引里有更早的任务也不会错传）
        tasks = [t for t in _tasks.load_tasks() if t.get("id") == tid] or [
            {"id": tid, "name": f"本次采集 {tid}", "folder": str(self._session_folder)}]
        self._append_log(
            f"🤖 {reason}，按勾选自动导入云展客户库：分组「{group.get('groupName') or ''}」")
        self._start_yzd_import(group, tasks)

    def _build_jobs(self, tasks: list) -> list:
        """把任务列表换成线程可吃的 jobs

        当前会话直接吃内存快照（避开“刚采完还没落盘”的空档），
        历史任务才回读会话目录里的原数据文件。
        """
        jobs = []
        tid = self._current_task_id()
        for t in tasks or []:
            name = t.get("name") or t.get("id") or "未命名任务"
            if t.get("id") and t.get("id") == tid and self._all_results():
                jobs.append({"id": t.get("id"), "name": name,
                             "records": self._all_results(), "raw_file": ""})
                continue
            raw_file = t.get("raw_file") or ""
            if not raw_file and t.get("folder"):
                raw_file = os.path.join(str(t.get("folder")), RAW_FILE_NAME)
            jobs.append({"id": t.get("id") or "", "name": name,
                         "records": None, "raw_file": raw_file})
        return jobs

    def _start_yzd_import(self, group: dict, tasks):
        if self._yzd_importing:
            return
        if tasks is None:
            latest = _tasks.latest_task()
            if not latest:
                QMessageBox.warning(self, "提示",
                                    "暂无采集任务：请先在主界面完成一次采集")
                return
            jobs = self._build_jobs([latest])
        else:
            jobs = self._build_jobs(tasks)
        if not jobs:
            QMessageBox.warning(self, "提示", "没有可导入的采集任务")
            return
        empty = [j for j in jobs if not j["raw_file"] and not (j["records"] or [])]
        if len(empty) == len(jobs):
            QMessageBox.warning(self, "提示", "选中的采集任务里没有数据")
            return

        import tempfile
        try:
            tmp_dir = tempfile.mkdtemp(prefix="yzd_import_")
        except Exception as e:
            QMessageBox.critical(self, "导入失败", f"无法创建临时上传目录：{e}")
            return

        self._yzd_jobs = jobs
        self._yzd_importing = True
        group_name = group.get("groupName") or ""
        self._yzd_group_name = group_name
        flag = self._flag_from_group(group)
        self._append_log(f"⬆ 开始导入云展客户库：分组「{group_name}」（{'国外' if flag else '国内'}）"
                         f"· 共 {len(jobs)} 个任务")

        # 锁界面：直接把主窗口 setEnabled(False)。比逐个缓存按钮状态可靠——
        # 导入期间采集线程的完成/停止槽函数还会改子控件状态，
        # 而父窗口禁用时子控件的有效状态永远是禁用，解锁后自然回到它们自己的最新状态。
        self.setEnabled(False)
        prog = YzdProgressDialog(None, f"正在导入云展客户库：{group_name}")
        self._yzd_progress = prog
        prog.show()

        worker = YzdImportWorker(self._yzd_phone, jobs, group.get("id"), flag, tmp_dir)
        worker.logged.connect(self._append_log)
        worker.progress.connect(self._on_yzd_import_progress)
        worker.finished_all.connect(self._on_yzd_import_finished)
        self._yzd_import_worker = worker
        worker.start()

    @staticmethod
    def _flag_from_group(group: dict) -> int:
        value = group.get("foreignFlag")
        try:
            return int(value) if value is not None else 0
        except (TypeError, ValueError):
            return 0

    def _on_yzd_import_progress(self, done: int, total: int, text: str):
        if self._yzd_progress is not None:
            self._yzd_progress.set_text(text,
                                        f"已完成 {done}/{total} 个任务。导入期间请勿关闭程序。")

    def _unlock_yzd_import(self):
        self._yzd_importing = False
        self.setEnabled(True)
        if self._yzd_progress is not None:
            try:
                self._yzd_progress.allow_close()
                self._yzd_progress.close()
            except Exception:
                pass
            self._yzd_progress = None
        self.activateWindow()

    def _on_yzd_import_finished(self, results: list, fatal: str):
        # 后端只按“导入行数”回计数，失败行只计 failTotal 不说原因，这里只如实透传三个数
        if fatal:
            self._append_log(f"❌ 导入异常：{fatal}")
            self._unlock_yzd_import()
            QMessageBox.critical(self, "导入失败", fatal)
            return
        lines = []
        saved_total = 0
        for idx, item in enumerate(results or []):
            name, imp, sav, fail, err = item
            saved_total += sav
            if err:
                lines.append(f"· {name}：导入失败（{err}）")
                self._append_log(f"❌ {name}：{err}")
            else:
                lines.append(f"· {name}：上传 {imp} 条 / 入库成功 {sav} 条 / 失败 {fail} 条")
                self._append_log(f"✅ {name}：上传 {imp} / 入库 {sav} / 失败 {fail}")
            if idx < len(self._yzd_jobs):
                job = self._yzd_jobs[idx]
                if job.get("id"):
                    try:
                        _tasks.update_task(
                            job["id"],
                            yzd_import_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            yzd_group=self._yzd_group_name,
                            yzd_saved=sav)
                    except Exception as e:
                        self._append_log(f"⚠ 任务导入结果回写失败：{e}")
        # 先解锁再弹结果：锁着弹框会让用户以为还没导完
        self._unlock_yzd_import()
        self._append_log("ℹ 云展接口不做重复校验，同一批数据重复导入会在客户库里产生多条记录")
        self._append_log("ℹ 分组名括号里的「客户数」是云展后端统计值（最长约 60 秒才更新），"
                         "刚导完数字没变是正常的，在弹窗里点「刷新」重查即可")
        summary = "\n".join(lines) if lines else "没有任何任务被处理"
        if saved_total == 0:
            QMessageBox.warning(self, "导入结果",
                                f"未入库任何客户：\n{summary}\n\n请查看运行日志里的后端返回。")
        else:
            QMessageBox.information(self, "导入结果",
                                    f"共入库 {saved_total} 条客户：\n\n{summary}")
        import shutil
        worker, self._yzd_import_worker = self._yzd_import_worker, None
        if worker is not None:
            try:
                shutil.rmtree(worker.tmp_dir, ignore_errors=True)
            except Exception:
                pass

    # ── 退出 ───────────────────────────────
    def closeEvent(self, event):
        """关窗口前给后台自动保存留时间：写完再退

        写盘是“临时文件 + 原子替换”，即使被打断也不会把已有文件写成半个，
        但这里等一下能让最后一次保存真正落盘。"""
        if self._yzd_importing:
            # 导入中途退程序会把云展那边留下“传了一半”的数据，而且线程被强杀只会拿到半个结果
            QMessageBox.information(self, "请等一下",
                                    "正在导入云展客户库，请等导入完成后再关闭程序。")
            event.ignore()
            return
        if self._cleaning:
            # 清洗线程还在读写文件，强杀只会得到一个没写完的 Excel
            QMessageBox.information(self, "请等一下",
                                    "正在清洗数据，请等清洗完成后再关闭程序。")
            event.ignore()
            return
        worker = self._autosave_worker
        if worker is not None and worker.isRunning():
            self._append_log("⏳ 自动保存正在写盘，稍等几秒…")
            worker.wait(15000)
        # 进度台账最后一条也要落盘：下次开工具才能从真正的位置继续
        self._cp_flush("关闭窗口")
        bind_worker = self._yzd_worker
        if bind_worker is not None and bind_worker.isRunning():
            bind_worker.wait(5000)
        super().closeEvent(event)


# ═══════════════════════════════════════════════
#  打开浏览器工作线程
# ═══════════════════════════════════════════════
class _OpenBrowserWorker(QThread):
    """后台启动浏览器，不阻塞 GUI"""
    finished = pyqtSignal(bool, str)  # success, message
    log_signal = pyqtSignal(str)

    def __init__(self, crawler, dual_targets=None):
        super().__init__()
        self.crawler = crawler
        self.dual_targets = dual_targets   # (企知道爬虫, 爱企查爬虫) → 双标签模式

    def run(self):
        try:
            if self.dual_targets:
                qzd, aqc = self.dual_targets
                ok, msg = _dual.setup_dual(qzd, aqc, self.log_signal.emit)
                self.finished.emit(ok, msg if ok else f"双标签准备失败：{msg}")
                return

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


# ══════════════════════════════════
#  双标签并行采集工作线程（企知道采单数 + 爱企查采双数）
# ══════════════════════════════════
class DualCrawlWorker(QThread):
    """两个会话共用一个浏览器、各占一个窗口，同时采集互不干扰。

    分工按原始位置单双数切分（第 1、3、5… 家 → 企知道；第 2、4、6… 家 → 爱企查）。
    夜间无人值守：爱企查被拦超过 block_wait_limit（默认 10 分钟）没人处理时，它会停止
    这一路并把未采名单放进 NameHandoff 队列，企知道取走接着采，不致于采一晚只到一半。
    """
    progress = pyqtSignal(int, int)
    log_signal = pyqtSignal(str)
    setup_failed = pyqtSignal(str)

    def __init__(self, qzd_crawler, aqc_crawler, companies: list, pending=None):
        super().__init__()
        self.qzd = qzd_crawler
        self.aqc = aqc_crawler
        self.companies = companies
        # 待采子集（由进度台账算出来）：奇偶分工仍按原始名单位置取，
        # 否则续采时同一家公司会换平台（配额与登录态都不同）
        self.pending = list(pending) if pending is not None else None

    def run(self):
        ok, msg = _dual.setup_dual(self.qzd, self.aqc, self.log_signal.emit)
        if not ok:
            self.setup_failed.emit(msg)
            self.finished.emit()
            return

        # 移交队列只在本轮有效：两个爬虫是长驻对象，跑完必须摘掉，
        # 否则下次单平台采集会拿着一个已经落定的旧队列去等移交
        handoff = _dual.NameHandoff(log=self.log_signal.emit)
        self.qzd.handoff = handoff
        self.aqc.handoff = handoff
        try:
            self._run_both(handoff)
        finally:
            handoff.finish()          # 万一上面临时报错，企知道也不能无限等下去
            self.qzd.handoff = None
            self.aqc.handoff = None

    def _todo_list(self, names: list, done: set) -> list:
        """按“两边已采到的公司名”过滤待采名单

        续采不能只按下标跳过：爱企查移交过来的名字会混在企知道的结果里，
        按下标算会把企知道自己没采的那几家一并跳过；而企知道采过的公司如果
        还留在爱企查的待采名单里，下一轮会被重复采一遍。按下标改按名字，两个
        问题一起消失，也顺便把原名单里重复出现的公司只采一次。
        """
        return [n for n in names if str(n or "").strip()
                and block_guard.norm_company(n) not in done]

    def _run_both(self, handoff):
        qzd_names, aqc_names = _dual.split_parity(self.companies)
        total = len(self.companies)
        done = {block_guard.norm_company(r.get("公司名"))
                for r in self.qzd.snapshot_results() + self.aqc.snapshot_results()}
        if self.pending is not None:
            # 台账已把“采过的/确认查不到的”剔出待采集；这里只拿它做奇偶分组后的交集，
            # 保证同一家永远归同一个平台且不重不漏
            want = {block_guard.norm_company(n) for n in self.pending}
            qzd_names = [n for n in qzd_names if block_guard.norm_company(n) in want]
            aqc_names = [n for n in aqc_names if block_guard.norm_company(n) in want]
        qzd_todo = self._todo_list(qzd_names, done)
        aqc_todo = self._todo_list(aqc_names, done)
        mins = max(1, int(getattr(self.aqc, "block_wait_limit", 600)) // 60)
        self.log_signal.emit(
            f"⚡ 双标签分工：企知道 {len(qzd_todo)} 家待采（单数，已完成 {len(self.qzd.results)}）、"
            f"爱企查 {len(aqc_todo)} 家待采（双数，已完成 {len(self.aqc.results)}）"
        )
        self.log_signal.emit(
            f"🌙 夜间无人值守：爱企查被拦超过 {mins} 分钟没人处理时会自动停止这一路，"
            f"剩下的公司转给企知道接着采，不会整晚卡在一个验证码上"
        )

        stop = threading.Event()

        def monitor():
            # 两个会话各自跑，进度合并上报（每秒一次）
            while not stop.wait(1.0):
                done = len(self.qzd.results) + len(self.aqc.results)
                self.progress.emit(min(done, total), total)

        mon = threading.Thread(target=monitor, daemon=True)
        mon.start()

        def run_one(crawler, names, tag):
            try:
                crawler.crawl(names, 0)
            except Exception as e:
                self.log_signal.emit(f"❌ {tag} 采集中断：{e}")
            finally:
                if crawler is self.aqc:
                    # 爱企查收工就要落定：企知道自己名单跑完时靠这个信号
                    # 决定是等移交名单还是直接收工
                    handoff.finish()

        t1 = threading.Thread(target=run_one, args=(self.qzd, qzd_todo, "企知道"))
        t2 = threading.Thread(target=run_one, args=(self.aqc, aqc_todo, "爱企查"))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        stop.set()
        done_count = len(self.qzd.results) + len(self.aqc.results)
        self.progress.emit(min(done_count, total), total)
        if handoff.gave_up:
            self.log_signal.emit(
                f"🔁 本轮爱企查中途停止，剩余公司已由企知道接手（企知道 {len(self.qzd.results)} 条 / "
                f"爱企查 {len(self.aqc.results)} 条）"
            )
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

