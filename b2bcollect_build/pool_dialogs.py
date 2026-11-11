# -*- coding: utf-8 -*-
"""pool_dialogs.py —— 数据池 GUI 对话框（同步上传 + 搜索采购商）。

交互约定（与现有导入云展对话框对齐）：
  · 长耗时操作期间锁定 UI + 显示加载动画，不允许执行其他操作
  · 前置条件缺失就地阻断（无采集数据 → 弹窗提示）
  · 错误信息直接展示后端/网络原始文案，不自行编造
  · 搜索期间结果表格实时更新（先本地毫秒出结果，DeepSeek 异步追加）
"""
import json
import os
import re
import sys
import time
import traceback
from datetime import datetime
from typing import List, NamedTuple, Optional

from PyQt5.QtCore import Qt, QThread, pyqtSignal, QSize, QPropertyAnimation, QEasingCurve, QPoint
from PyQt5.QtGui import QFont, QColor, QCursor
from PyQt5.QtWidgets import (
    QApplication, QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QTableWidget, QTableWidgetItem, QHeaderView,
    QProgressBar, QMessageBox, QFileDialog, QCheckBox, QAbstractItemView,
    QComboBox, QSplitter, QFrame, QSizePolicy, QPlainTextEdit, QTabWidget,
    QListWidget, QListWidgetItem, QSpinBox, QToolButton, QInputDialog,
)

# ── 模块导入（容错：打包态可能找不到）──
try:
    from oss_pool_client import OSSPoolClient, OSSPoolError
    from data_pool import (DataPoolManager, normalize_company_name, EXPORT_HEADER,
                           record_region)
    from data_pool import _CN_PROVINCES as CN_PROVINCES
    from pool_matcher import CompanyMatcher
    from mapping_recommender import MappingRecommender
    from audit_logger import get_audit_logger
    _HAS_POOL = True
except ImportError:
    _HAS_POOL = False
    # 打包态导入失败时的兜底：清单为空，回退分支自然不产出任何省份选项
    CN_PROVINCES = ()

# 分区键哨兵（清洗层的单一来源）：界面按它把「判不出地区/行业」从"覆盖"计数里
# 排除，但分布里仍然如实保留——两边都必须用同一份常量，不能在这儿抄字符串
try:
    from ingest_cleaning import INDUSTRY_UNKNOWN, PROVINCE_UNKNOWN
except ImportError:
    INDUSTRY_UNKNOWN, PROVINCE_UNKNOWN = "未分类", "未知地区"

# 用户反馈存储（可选依赖）
try:
    from feedback_store import get_feedback_store, FEEDBACK_UP, FEEDBACK_DOWN, FEEDBACK_DISLIKE
    _HAS_FEEDBACK_STORE = True
except ImportError:
    _HAS_FEEDBACK_STORE = False

# 搜索建议（可选依赖）
try:
    from search_suggest import get_search_suggester
    _HAS_SUGGEST = True
except ImportError:
    _HAS_SUGGEST = False


# ── 敏感数据脱敏工具函数 ──
def _mask_phone(phone: str) -> str:
    """手机号脱敏：13812345678 → 138****5678"""
    if not phone:
        return ""
    digits = re.sub(r"\D", "", phone)
    if len(digits) >= 11 and re.match(r"^1[3-9]", digits[-11:]):
        # 国内手机号：保留前3后4
        return phone[:3] + "****" + phone[-4:]
    elif len(digits) >= 7:
        # 固话或其他：保留前3后2
        return phone[:3] + "***" + phone[-2:]
    return phone


def _mask_email(email: str) -> str:
    """邮箱脱敏：john@example.com → j***@example.com"""
    if not email or "@" not in email:
        return email or ""
    local, domain = email.rsplit("@", 1)
    if len(local) <= 1:
        masked_local = local + "***"
    else:
        masked_local = local[0] + "***"
    return f"{masked_local}@{domain}"


def _mask_wechat(wechat: str) -> str:
    """微信号脱敏：保留首尾字符"""
    if not wechat:
        return ""
    if len(wechat) <= 2:
        return wechat[0] + "*"
    return wechat[0] + "***" + wechat[-1]


# ─────────────────────────────────────────────────────────────────────────────
#  辅助 UI 组件
# ─────────────────────────────────────────────────────────────────────────────

class PoolProgressDialog(QDialog):
    """不可关闭的加载进度框（同 YzdProgressDialog 风格）。"""

    def __init__(self, parent, title="处理中…"):
        super().__init__(parent)
        self._allow_close = False
        self.setWindowTitle(title)
        self.setModal(True)
        self.setWindowFlags(Qt.Dialog | Qt.CustomizeWindowHint | Qt.WindowTitleHint)
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        lay.setContentsMargins(22, 18, 22, 16)
        self.info_lbl = QLabel("准备中…")
        f = self.info_lbl.font()
        f.setPointSize(11)
        f.setBold(True)
        self.info_lbl.setFont(f)
        self.info_lbl.setWordWrap(True)
        lay.addWidget(self.info_lbl)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.bar.setTextVisible(False)
        self.bar.setMinimumHeight(18)
        lay.addWidget(self.bar)
        self.detail_lbl = QLabel("请勿关闭程序…")
        f2 = self.detail_lbl.font()
        f2.setPointSize(9)
        self.detail_lbl.setFont(f2)
        self.detail_lbl.setStyleSheet("color: #888;")
        self.detail_lbl.setWordWrap(True)
        lay.addWidget(self.detail_lbl)

    def set_text(self, main: str, detail: str = ""):
        self.info_lbl.setText(main)
        if detail:
            self.detail_lbl.setText(detail)

    def allow_close(self):
        self._allow_close = True

    def closeEvent(self, event):
        event.accept() if self._allow_close else event.ignore()


# ─────────────────────────────────────────────────────────────────────────────
#  Toast 通知组件（Material Design Snackbar / Ant Design Message 思想）
# ─────────────────────────────────────────────────────────────────────────────

class ToastWidget(QWidget):
    """非阻塞醒目通知（滑入动画 + 自动消失 + 颜色编码）。

    设计参考：
      · Material Design Snackbar：底部/顶部滑入，自动消失
      · Ant Design Message：顶部居中，带颜色编码
      · Notion / Slack Toast：轻量非阻断，操作后即时反馈
    """
    _instances = set()  # 防止 GC 回收正在显示的 toast

    _COLORS = {
        'success': '#2e7d32',
        'info':    '#1565c0',
        'warning': '#e65100',
        'error':   '#c62828',
    }
    _ICONS = {
        'success': '\u2705',   # ✅
        'info':    '\u2139\ufe0f',  # ℹ️
        'warning': '\u26a0\ufe0f',  # ⚠️
        'error':   '\u274c',   # ❌
    }

    def __init__(self, message, parent=None, toast_type='info', duration_ms=3500):
        super().__init__(parent)
        self._duration = duration_ms
        bg = self._COLORS.get(toast_type, self._COLORS['info'])
        icon = self._ICONS.get(toast_type, '')

        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 12, 18, 12)

        if icon:
            ic = QLabel(icon)
            ic.setStyleSheet('font-size: 16px;')
            lay.addWidget(ic)

        msg = QLabel(message)
        f = msg.font()
        f.setPointSize(11)
        f.setBold(True)
        msg.setFont(f)
        msg.setStyleSheet('color: white;')
        msg.setWordWrap(True)
        lay.addWidget(msg, 1)

        close_btn = QPushButton('\u00d7')  # ×
        close_btn.setFixedSize(24, 24)
        close_btn.setStyleSheet(
            'QPushButton { background: transparent; color: white; '
            'font-size: 16px; border: none; }'
            'QPushButton:hover { color: #ccc; }'
        )
        close_btn.clicked.connect(self._hide)
        lay.addWidget(close_btn)

        self.setStyleSheet(
            f'ToastWidget {{ background: {bg}; border-radius: 8px; }}'
        )
        self.setFixedWidth(420)
        self.setMinimumHeight(48)
        self.setMaximumHeight(64)
        self.setGraphicsEffect(None)
        self._opacity = 1.0

    def show(self):
        parent = self.parentWidget()
        if parent:
            pg = parent.geometry()
            x = (pg.width() - self.width()) // 2
            y = 15
            self.setParent(parent)
            self.move(max(x, 10), y)
        super().show()
        self.raise_()
        ToastWidget._instances.add(self)

        # 滑入动画（从上方滑入）
        self._anim = QPropertyAnimation(self, b'pos')
        self._anim.setDuration(300)
        self._anim.setStartValue(self.pos() + QPoint(0, -30))
        self._anim.setEndValue(self.pos())
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.start()

        # 定时自动消失
        from PyQt5.QtCore import QTimer
        QTimer.singleShot(self._duration, self._hide)

    def _hide(self):
        """淡出动画后关闭。"""
        try:
            ToastWidget._instances.discard(self)
            self._fade = QPropertyAnimation(self, b'windowOpacity')
            self._fade.setDuration(250)
            self._fade.setStartValue(1.0)
            self._fade.setEndValue(0.0)
            self._fade.setEasingCurve(QEasingCurve.InCubic)
            self._fade.finished.connect(self.close)
            self._fade.start()
        except Exception:
            self.close()


def _show_toast(parent, message, toast_type='info', duration_ms=3500):
    """在 parent 上显示一个非阻塞 Toast 通知。"""
    toast = ToastWidget(message, parent, toast_type, duration_ms)
    toast.show()


class RecallCounts(NamedTuple):
    """界面那两个总数：关键词粗筛数 + 当前范围总数，各自带「有没有被封顶」。

    · None 表示视图不可用、这个数拿不到——宁可少说一个数字，绝不把「不知道」
      显示成 0 条；
    · *_capped 为真表示该值是封顶值（见 pool_store.COUNT_CAP），必须说成
      「N 条以上」，把封顶值当精确值报出去就是报假数；
    · by_keyword 表示本次真的按关键词粗筛过。同地区推荐是「空关键词 + 筛选
      下压」，对它说「关键词先粗筛出 N 家」同样是宣称没发生过的动作，所以这个
      事实必须随数字一起传下去，而不是在每个文案点靠 query 再猜一遍。
    """
    matched: Optional[int] = None
    scope_total: Optional[int] = None
    matched_capped: bool = False
    scope_capped: bool = False
    by_keyword: bool = False

    @property
    def merged(self) -> bool:
        """两个都是精确值且相等（关键词在整个范围里全命中）→ 只说一次。"""
        return (self.by_keyword and self.matched is not None
                and self.matched == self.scope_total
                and not self.matched_capped and not self.scope_capped)


def _counts_of(mgr, query: str, regions, filters,
               cap: Optional[int] = None) -> RecallCounts:
    """两个总数只在这里取一次（口径唯一实现），封顶信息一起带出。

    空关键词不报粗筛数：本次压根没有关键词可粗筛，硬把「范围总数」当「命中数」
    说成「关键词先粗筛出 N 条」就是宣称没发生过的动作（同地区推荐正是空关键词
    + 筛选下压的路径）。顺带省掉一次全表计数。
    """
    has_query = bool(str(query or "").strip())
    # by_keyword 先落好：万一两个总数取不到，文案仍要知道本次到底粗筛过没有
    counts = RecallCounts(by_keyword=has_query)
    if mgr is None:
        return counts
    try:
        m = (mgr.store_count_capped(query, regions, filters, cap=cap)
             if has_query else None)
        s = mgr.store_count_capped("", regions, filters, cap=cap)
    except Exception:
        return counts
    return RecallCounts(
        m[0] if m else None, s[0] if s else None,
        bool(m and not m[1]), bool(s and not s[1]), has_query)


def _n(value, capped: bool, unit: str = "条") -> str:
    """数字的写法唯一实现：封顶值一律带「以上」，没封顶才当精确值报。"""
    if value is None:
        return ""
    return f"{value} {unit}以上" if capped else f"{value} {unit}"


def _scope_parts(counts: RecallCounts, unit: str = "条") -> List[str]:
    """两个总数的措辞唯一实现：关键词粗筛数、当前范围总数（搜索文案与概览共用）。

    · merged（两个精确值本就相等）只留「当前范围共 N 条」一句，说两遍等于噪声；
    · 任一为 None（视图未就绪、老链路拿不到总数）就只报已知的部分，宁可少说
      一个数字，绝不把「不知道」显示成 0 条。
    """
    matched, scope_total = counts.matched, counts.scope_total
    if counts.merged:
        scope_total, matched = matched, None
    parts = []
    if matched is not None:
        parts.append("关键词先粗筛出 " + _n(matched, counts.matched_capped, unit))
    if scope_total is not None:
        parts.append("当前范围共 " + _n(scope_total, counts.scope_capped, unit))
    return parts


def _reach_note(mgr) -> str:
    """把总数拆成「可外呼 / 仅线索」两句大实话（判据来自 mgr.reach_summary）。

    资源池从 6 万涨到 8 万这一轮带进来 1.3 万条只有官网或地址的展商线索行。
    它们不是错误数据（后续可以拿公司名去三个平台补采），但只报「总表 87450 家」
    会让人以为这些号都能直接打电话——用户按这个预期导出数据后才发现一半是空的，
    比一开始就少给一条信息更伤。拿不到覆盖度就整句不说，绝不显示成 0 家。
    """
    try:
        rs = mgr.reach_summary() if mgr is not None else None
    except Exception:
        rs = None
    if not rs or not rs.get("total"):
        return ""
    total = int(rs.get("total") or 0)
    reach = int(rs.get("reachable") or 0)
    # lead_only 缺键时按互补口径算：总数与可外呼数都是实打实的，两者之差就是线索行数。
    # 直接把缺键当成 0 会说谎（把线索藏起来），当成 None 又会显示成「None 家」。
    lead = int(rs.get("lead_only", max(total - reach, 0)))
    return (f"其中 {reach} 家有手机号/固话/邮箱/微信（可直接外呼），"
            f"{lead} 家暂时只有官网或地址"
            + ("（勾「只看有联系方式」可把它们排除）" if lead else ""))


def _scope_note(counts: RecallCounts, shown: int, unit: str = "条") -> str:
    """把「粗筛出多少 / 当前范围一共多少 / 这次给你看多少」三个数分开说。

    三个数字在百万级里必然不相等，混着说就是骗人（用大白话，不写「召回/候选」
    这类内部术语，见界面文案规范）：
      · matched 粗筛数：数据库按关键词粗筛出的条数。粗筛故意放宽（OR 命中）
        ——搜一整家公司名也会粗筛出上万条，把这条写成「池内共 N 条」会让用户
        以为池里有一万家长得一样的公司（历史缺陷正是这种混用）。
      · scope_total 范围总数：当前「国内/国外 + 筛选条件」下的总量，与关键词
        无关，回答「这个范围有多大」，用来决定要不要放宽筛选或直接导出。
      · shown 返回数：算完匹配度后真正进结果表、用户能翻页看到的条数。
    """
    parts = _scope_parts(counts, unit)
    if not parts:
        return ""
    baseline = (counts.scope_total if counts.merged
                else counts.matched if counts.matched is not None
                else counts.scope_total)
    if baseline is not None and shown < baseline:
        parts.append(f"按匹配度取前 {shown} {unit}")
    return "（" + "、".join(parts) + "）"


def _recall_note(recall: str, scope_n: int,
                 counts: RecallCounts = RecallCounts(),
                 cap: Optional[int] = None) -> str:
    """一句话说明「这批公司是怎么取到的」（唯一实现，搜索与推荐共用）。

    每种来源必须说各自的真话，也只能说一遍：
      · 检索视图 —— 真的按关键词在库里粗筛过，说得出粗筛数；
      · 关键词索引 —— 下层 pool_matcher 已经播过「先按关键词挑出 N 家」，
        这里再写一句同一件事，两处措辞迟早对不上，所以返回空串；
      · 全表扫描 —— 没有关键词清单，逐家比对是事实，必须如实说「会慢一些」，
        把回退说成「粗筛」就是宣称没发生过的动作。
    候选集合有上限（RECALL_CAP）：命中多到被截断时必须讲清「只对其中 N 家算
    匹配度」，报一个没比过的数字，用户会以为整池都排过序。判据只看两处都站得住
    的事实——候选数正好占满上限，或精确的命中数确实大于候选数；命中数拿不到时
    只说「不止这些」，绝不编一个数出来。
    """
    if recall == "检索视图":
        truncated = ((cap is not None and scope_n >= cap)
                     or (counts.matched is not None
                         and not counts.matched_capped
                         and counts.matched > scope_n))
        if truncated:
            if counts.by_keyword and counts.matched is not None:
                return (f"⚡ 关键词命中 "
                        f"{_n(counts.matched, counts.matched_capped, '家')}"
                        f"，先取相关性最高的 {scope_n} 家算匹配度")
            return (f"⚡ 符合条件的公司不止这些，只对前 {scope_n} 家算了匹配度"
                    f"（缩小范围可看全）")
        verb = "关键词先粗筛出" if counts.by_keyword else "按当前范围先取出"
        return f"⚡ {verb} {scope_n} 家公司，再逐家算匹配度"
    if recall == "全表扫描":
        return (f"⚡ 本次没有可用的关键词清单，改成逐家比对 {scope_n} 家公司，"
                f"会慢一些（找到的范围不变）")
    return ""


# ─────────────────────────────────────────────────────────────────────────────
#  后台线程
# ─────────────────────────────────────────────────────────────────────────────

class SyncWorker(QThread):
    """后台上传线程。"""
    progress = pyqtSignal(str, str)   # (main_text, detail_text)
    finished = pyqtSignal(bool, str)  # (success, message)

    def __init__(self, records: list, username: str = ""):
        super().__init__()
        self.records = records
        self.username = username
        # 实际入库报告（kept/dropped），供对话框如实显示与写审计日志
        self.report: dict = {}

    def run(self):
        try:
            self.progress.emit("正在连接数据池…", "")
            mgr = DataPoolManager()
            self.progress.emit(
                f"正在上传 {len(self.records)} 条记录…",
                f"用户: {mgr.username}"
            )
            # 每步进度回调：实时显示当前在做什么
            def _on_step(text: str):
                self.progress.emit(text, f"用户: {mgr.username}")
            # report_out 拿到的是**真实入库条数**：界面曾按 len(records) 报
            # 「成功上传 N 条」，而上传口自己还会再清洗一次，N 与实际入库数
            # 可以不相等——数字对不上就是误导，一律以清洗后的 kept 为准。
            report: dict = {}
            self.report = report
            filename = mgr.upload_session(self.records,
                                          progress_callback=_on_step,
                                          report_out=report)
            kept = int(report.get("kept", len(self.records)))
            self.progress.emit("上传完成", f"文件: {filename}")
            note = "" if kept == len(self.records) else f"（清洗后入库 {kept} 条）"
            self.finished.emit(
                True,
                f"成功上传 {kept} 条记录到数据池{note}\n文件名: {filename}")
        except Exception as e:
            self.finished.emit(False, f"上传失败: {e}")


class LoadMasterWorker(QThread):
    """后台加载总表线程（复用对话框持有的同一实例，保证索引/总表同源）。

    大厂秒开思想（Google Drive / Dropbox）：
      1. 本地缓存秒开（不等 OSS 网络）
      2. 后台静默同步（有新数据才更新 UI）
      3. 统计信息从本地缓存计算（零网络延迟）
    """
    progress = pyqtSignal(str)               # 阶段进度文案
    finished = pyqtSignal(bool, list, str)  # (success, master, message)
    refreshed = pyqtSignal(list, str)       # 后台刷新完成 (master, message)

    def __init__(self, force_refresh: bool = False, mgr=None):
        super().__init__()
        self.mgr = mgr or DataPoolManager()
        self.force_refresh = force_refresh
        self._view_pct = -1                 # 上次播报的整理进度（同百分比不重复发）

    def _emit_view_progress(self, done: int, total: int):
        """pool_store.rebuild 进度回调的适配器：那边传的是 (done, total) 两个整数。

        绝不可把 self.progress.emit 直接当回调传过去：progress 是 pyqtSignal(str)，
        收到两个 int 会抛 TypeError，而回调是在重建事务里被调的，异常会让整段重建
        回滚——视图永远建不起来，搜索静默走慢路径。而且这条只在两万条以上才会被
        触发（小池子根本不走回调），所以必须显式适配签名并单独覆盖这条路径。
        """
        if total <= 0:
            return
        pct = int(done * 100 / total)
        if pct == self._view_pct:
            return
        self._view_pct = pct
        self.progress.emit(
            f"正在整理数据以便快速搜索：已整理 {done} / {total} 条（{pct}%）")

    def _sync_view(self, mgr) -> bool:
        """让本地加速数据跟上限表对齐；已对齐时只读一次元数据（毫秒级）。"""
        self._view_pct = -1
        try:
            return bool(mgr.sync_store(progress_cb=self._emit_view_progress))
        except Exception:
            return False

    def run(self):
        try:
            self.progress.emit("正在加载数据池…")
            self._do_load()
        except Exception as e:
            self.finished.emit(False, [], f"加载数据池失败: {e}")

    def _do_load(self):
        """秒开加载：本地缓存立即返回 + 后台静默同步。"""
        mgr = self.mgr
        # 检查本地缓存状态
        has_local = (mgr._cache_dir / "master.json").exists()
        if has_local:
            self.progress.emit("正在加载本地缓存…")
        else:
            self.progress.emit("首次加载，正在从远端拉取数据…")

        # 核心：get_merged_master 有本地缓存时秒返回（不等 OSS）
        master = mgr.get_merged_master(force_refresh=self.force_refresh)

        # Gold 物化视图在后台线程与本线程内构建/校验：
        # 已对齐时 is_current 只读一次元数据（毫秒级），真正重建只发生在
        # 首次或数据变更时。放在这里而不是搜索路径，点击「搜索」才可能不卡。
        self.progress.emit("正在整理数据以便快速搜索…")
        view_ok = self._sync_view(mgr)

        # 统计从本地缓存计算（零网络延迟）
        stats = mgr.get_master_stats()
        msg = (
            f"总表 {len(master)} 家公司\n"
            f"数据来自 {stats.get('users', 0)} 位用户，"
            f"共 {stats.get('total_uploads', 0)} 条原始记录"
        )
        _rn = _reach_note(mgr)
        if _rn:
            msg += "\n" + _rn
        if view_ok:
            msg += "\n搜索加速已就绪：几百万条也能秒出结果"
        else:
            msg += "\n本次改用较慢的方式查找（结果范围完全一样），后台会自行加速"
        self.finished.emit(True, master, msg)

        # 后台静默同步（有新数据才刷新 UI）
        if has_local and not self.force_refresh:
            self.progress.emit("正在同步远端数据…")
            try:
                new_master = mgr.refresh_from_oss()
                if new_master is not None:
                    # 加速数据必须跟着新总表走：同步后不重建，「已同步」的数据就检索不到
                    self._sync_view(mgr)
                    new_stats = mgr.get_master_stats()
                    new_msg = (
                        f"总表 {len(new_master)} 家公司（已同步）\n"
                        f"数据来自 {new_stats.get('users', 0)} 位用户，"
                        f"共 {new_stats.get('total_uploads', 0)} 条原始记录"
                    )
                    _rn2 = _reach_note(mgr)
                    if _rn2:
                        new_msg += "\n" + _rn2
                    self.refreshed.emit(new_master, new_msg)
            except Exception:
                pass  # 后台同步失败静默忽略，不影响已展示的数据


class SearchWorker(QThread):
    """后台搜索线程（召回在 Gold 物化视图，精排在 CompanyMatcher）。

    两级检索是搜索引擎的标准形态，也是百万级唯一扛得住的形态：
      · 召回 —— SQLite+FTS5 全文 + 分区裁剪 + 筛选下压，毫秒级返回候选集合
      · 精排 —— CompanyMatcher 用同一批 token 精确算 overlap/Jaccard 定相关度
    视图不可用时逐级回退：磁盘倒排索引 → 内存全表扫描。回退只换速度，
    不换结果口径（筛选在每条路径上都用同一个 apply_filters 实现）。
    候选集合的获取全部在本线程：倒排索引是 5.9MB JSON，解析放在点击路径上
    就是每次搜索白卡一次界面。
    """
    progress = pyqtSignal(str)   # 思考步骤文案
    finished = pyqtSignal(bool, list, str)  # (success, results, message)

    # 进精排集合的候选上限：BM25 决定谁进来，最终顺序由精排决定
    RECALL_CAP = 3000

    def __init__(self, query: str, master: list, use_deepseek: bool = False,
                 candidate_indices=None, max_results: int = 100,
                 mgr=None, regions=None, filters=None):
        super().__init__()
        self.query = query
        self.master = master
        self.use_deepseek = use_deepseek
        self.candidate_indices = candidate_indices
        self.max_results = max_results
        self.mgr = mgr
        self.regions = regions or []
        self.filters = filters or {}

    def _recall(self):
        """取精排用的候选集，返回 (记录列表, 召回来源说明, RecallCounts)。

        粗筛数与范围总数是两个必须分开报的数字（见 RecallCounts）：前者是本次
        关键词在视图里命中的候选条数，后者是不看关键词的范围内总数。只拿前者当
        「池内共 N 条」显示，用户会以为池里全是同一家公司。
        两个数都取封顶值：百万级里精确 count 宽词要 2 秒，而界面只需要知道
        「量大到可以忽略精排截断的影响」还是「就这么多」。
        """
        if self.mgr is None:
            return self.master, "", RecallCounts()
        # 1) Gold 物化视图：全文 + 国内外分区 + 筛选全部在 SQL 里下压
        try:
            records = self.mgr.search_candidates(self.query, self.regions,
                                                 self.filters,
                                                 limit=self.RECALL_CAP)
        except Exception:
            records = None
        if records is not None:
            return (records, "检索视图",
                    _counts_of(self.mgr, self.query, self.regions, self.filters))
        # 2) 磁盘倒排索引（视图还没建好时用它，索引解析在本线程完成）
        if self.candidate_indices is None:
            try:
                self.candidate_indices = self.mgr.search_by_index(self.query)
            except Exception:
                self.candidate_indices = None
        if self.candidate_indices:
            return self.master, "关键词索引", RecallCounts()
        return self.master, "全表扫描", RecallCounts()

    def run(self):
        try:
            matcher = CompanyMatcher()
            # 两种模式都先召回：AI 模式的产业链分析同样只在候选里做
            scope, recall, counts = self._recall()
            note = _recall_note(recall, len(scope), counts, cap=self.RECALL_CAP)
            if note:
                self.progress.emit(note)
            if self.use_deepseek:
                # 智能匹配：自动识别公司名/关键词，选最佳策略
                self.progress.emit("🤖 启动 AI 深度分析...")
                self.progress.emit(f"📝 正在理解「{self.query}」的含义...")
                results = matcher.search_smart(
                    self.query, scope,
                    candidate_indices=(self.candidate_indices
                                       if recall == "关键词索引" else None),
                    top_k=self.max_results,
                    progress_cb=lambda msg: self.progress.emit(msg),
                )
                method = "智能匹配"
            else:
                # 本地搜索：候选集内评分
                self.progress.emit(f"🔍 开始搜索「{self.query}」...")
                if recall == "关键词索引":
                    results = matcher.search_with_index(
                        self.query, self.master, self.candidate_indices,
                        top_k=self.max_results,
                        progress_cb=lambda msg: self.progress.emit(msg),
                    )
                else:
                    results = matcher.search_local(
                        self.query, scope,
                        top_k=self.max_results,
                        progress_cb=lambda msg: self.progress.emit(msg),
                    )
                method = "本地搜索"
            # 视图路径的筛选已在 SQL 里下压；回退路径用同一个 apply_filters
            # 补上（口径唯一实现），两条路径给用户的范围必须一致
            if recall != "检索视图" and self.filters:
                results = CompanyMatcher.apply_filters(results, self.filters)
            message = f"{method}：找到 {len(results)} 条相关结果"
            message += _scope_note(counts, len(results))
            self.finished.emit(True, results, message)
        except Exception as e:
            self.finished.emit(False, [], f"搜索失败: {e}")


# ─────────────────────────────────────────────────────────────────────────────
#  同步上传对话框
# ─────────────────────────────────────────────────────────────────────────────

class RecommendWorker(QThread):
    """后台推荐线程：候选召回走 Gold 视图，打分只在候选里做。

    修掉的是「一点推荐就白屏」：旧实现直接在 **UI 主线程**上对整张总表逐条
    分词 + Jaccard（六万条几秒、百万级几十秒，期间界面完全不响应、无法取消）。
    但推荐要的候选本来就是目标公司产品/地区命中的那批记录，视图一次下压就能
    拿到——全表扫描从来不是必需，只是当年没接召回（LinkedIn/淘宝的 related
    items 同样是「候选生成 → 精排」两段，没有谁对全库逐条算相似度）。

    降级链与搜索完全一致（检索视图 → 磁盘倒排 → 全表），回退只换速度，
    不换结果口径；地域推荐尊重用户已选地区，不拿目标公司省份去覆盖筛选。
    """
    progress = pyqtSignal(str)
    finished = pyqtSignal(bool, list, str)   # (success, results, 结论文案)

    RECALL_CAP = 3000      # 与 SearchWorker 同一口径：候选有界，精排定最终顺序
    TOP_K = 20

    def __init__(self, rec_type: str, seed: dict, master: list, mgr=None,
                 regions=None, filters=None):
        super().__init__()
        self.rec_type = rec_type
        self.seed = seed or {}
        self.master = master or []
        self.mgr = mgr
        self.regions = regions or []
        self.filters = filters or {}

    def _plan(self):
        """按推荐类型决定 (召回关键词, 下压筛选, 冲突提示)。"""
        seed = self.seed
        filters = dict(self.filters)
        if self.rec_type == "regional":
            province = ""
            try:
                from ingest_cleaning import partition_province, PROVINCE_UNKNOWN
                # 哨兵值不是地区：拿「未知地区」当筛选键等于「所有判不出地区的
                # 公司都算同地区」，那是假推荐，必须当成没有证据
                derived = str(partition_province(seed) or "")
                province = "" if derived == PROVINCE_UNKNOWN else derived
            except Exception:
                province = ""
            if not province:
                return "", filters, ("目标公司判不出所在地区（地址/号码/邮箱里"
                                     "都没有省份或国家线索），同地区推荐需要其中一项")
            user_region = str(filters.get("region") or "").strip()
            if user_region and user_region != "全部" and user_region != province:
                return "", filters, (f"当前地区筛选是「{user_region}」，与目标公司所在"
                                     f"「{province}」不一致；已按筛选优先，"
                                     f"清空地区筛选后再推荐即可按目标公司属地查找")
            filters["region"] = province
            return "", filters, ""
        if self.rec_type == "supply_chain":
            term = (str(seed.get("industry") or "").strip()
                    or str(seed.get("products") or "").strip())
            if not term:
                try:
                    from pool_matcher import detect_industry
                    term = str(detect_industry(str(seed.get("company") or "")) or "")
                except Exception:
                    term = ""
            return term, filters, ("" if term else "目标公司没有可用于供应链匹配的行业/产品")
        term = (str(seed.get("products") or "").strip()
                or str(seed.get("company") or "").strip())
        return term, filters, ("" if term else "目标公司没有产品/公司名称可用于相似度匹配")

    def _recall(self, query: str, filters: dict):
        """返回 (候选记录列表, 召回来源, RecallCounts)。

        候选有界，视图不可用才逐级回退；两个总数只在视图路径拿得到，回退路径
        如实返回空 RecallCounts（宁可少报数字，绝不猜）。
        """
        if self.mgr is not None:
            try:
                got = self.mgr.search_candidates(query, self.regions, filters,
                                                 limit=self.RECALL_CAP)
            except Exception:
                got = None
            if got is not None:
                return (got, "检索视图",
                        _counts_of(self.mgr, query, self.regions, filters))
            if query:
                try:
                    idx = self.mgr.search_by_index(query)
                except Exception:
                    idx = None
                if idx:
                    n = len(self.master)
                    return ([self.master[i] for i in idx if 0 <= i < n],
                            "关键词索引", RecallCounts())
        return self.master, "全表扫描", RecallCounts()

    def run(self):
        try:
            from recommend_engine import RecommendEngine
        except Exception as e:                       # 打包态缺模块要如实说
            self.finished.emit(False, [], f"推荐引擎不可用: {e}")
            return
        try:
            query, filters, conflict = self._plan()
            if conflict:
                self.finished.emit(False, [], conflict)
                return
            scope, recall, counts = self._recall(query, filters)
            # by_keyword 由 counts 带着：query 为空时不许报「关键词命中 N 家」
            note = _recall_note(recall, len(scope), counts, cap=self.RECALL_CAP)
            if note:
                self.progress.emit(note)
            engine = RecommendEngine()
            name = str(self.seed.get("company", ""))[:15]
            if self.rec_type == "supply_chain":
                results = engine.find_supply_chain(self.seed, scope, top_k=self.TOP_K)
                title = f"「{name}」的供应链相关公司"
            elif self.rec_type == "regional":
                results = engine.find_regional(self.seed, scope, top_k=self.TOP_K)
                title = f"与「{name}」同地区的公司"
            else:
                results = engine.find_similar(self.seed, scope, top_k=self.TOP_K)
                title = f"与「{name}」相似的公司"
            message = (f"{title}：{len(results)} 家"
                       + _scope_note(counts, len(results), unit="家"))
            self.finished.emit(True, results, message)
        except Exception as e:
            self.finished.emit(False, [], f"推荐失败: {e}")


class CleaningPreviewWorker(QThread):
    """入库前清洗的空跑预览：只出报告，不上传、不写磁盘。

    为什么值得多跑一遍：上传是不可逆动作（进了团队 OSS 就是别人的数据），
    大厂 ETL 一律在 commit 前把「这次会入库多少、剔了多少、为什么剔」摊给
    用户看。clean_one 会就地修复记录（与真正上传时同一个动作，重复执行
    幂等），预览不额外复制数据；报告与实际入库结果出自同一入口，必然一致。
    """
    finished = pyqtSignal(dict, str)  # (report, 渲染好的文本)

    def __init__(self, records: list):
        super().__init__()
        self.records = records

    def run(self):
        try:
            from ingest_cleaning import clean_records, format_report
            _kept, report = clean_records(self.records)
            self.finished.emit(report, format_report(report))
        except Exception as e:
            self.finished.emit({}, f"清洗预览失败：{e}")


class SyncDialog(QDialog):
    """把本次采集数据同步到团队数据池。"""

    def __init__(self, records: list, parent=None, title_prefix: str = "",
                 cleaning_report: dict = None):
        super().__init__(parent)
        self.records = records
        # 导入链路已经算过报告就直接用，避免对同一批数据白跑一遍清洗
        self._cleaning_report = cleaning_report or None
        title = f"{title_prefix} 同步到数据池" if title_prefix else "同步到数据池"
        self.setWindowTitle(title)
        self.setMinimumSize(520, 560)
        self.resize(560, 640)
        self._worker = None
        self._preview_worker = None
        # 上传是否真的成功落地 + 成功那次的文件名：调用方（GUI）拿它决定
        # 「本地扫描台账要不要记成已入库」。默认 False 是硬要求——把「用户点了
        # 上传」当成「已经传进去了」会让台账永久跳过其实没进池的文件，那是丢数据。
        self.uploaded_ok = False
        self.uploaded_batch = ""
        self.uploaded_kept = 0
        self._init_ui()

    def _init_ui(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.setContentsMargins(18, 16, 18, 16)

        # 标题
        title = QLabel("📤 同步采集数据到团队数据池")
        f = title.font()
        f.setPointSize(13)
        f.setBold(True)
        title.setFont(f)
        lay.addWidget(title)

        # 预览
        info = QLabel(f"本次采集 {len(self.records)} 条记录，预览前 5 条：")
        info.setStyleSheet("color: #555;")
        lay.addWidget(info)

        preview = QPlainTextEdit()
        preview.setReadOnly(True)
        preview.setMaximumHeight(120)
        preview.setStyleSheet("font-size: 11px; background: #f8f8f8;")
        lines = []
        for rec in self.records[:5]:
            company = rec.get("company", "?")
            phones = ", ".join(rec.get("phones", [])[:2]) or "无电话"
            lines.append(f"  · {company}  |  {phones}")
        if len(self.records) > 5:
            lines.append(f"  … 还有 {len(self.records) - 5} 条")
        preview.setPlainText("\n".join(lines))
        lay.addWidget(preview)

        # ── 入库前清洗报告（Bronze→Silver 落地情况，上传前必须看得见）──
        # 未入库清单按钮先建好再排版：清洗报告渲染时就要按剔除数决定它出不出现，
        # 建在报告之后会踩「渲染时属性还没有」的时序坑。
        # 被门禁拦下的行必须有去处：点了就落一份可核对的清单（默认先隐藏，
        # 只有真有剔除时才出现，别把用户引到一个空文件上）。
        self.btn_export_drops = QPushButton("导出未入库清单")
        self.btn_export_drops.setMinimumHeight(36)
        self.btn_export_drops.setToolTip(
            "把这次被清洗门禁剔除的记录整份导出成 Excel（带剔除原因和原有\n"
            "号码/邮箱/官网/地址/主营）。不入库不等于消失：列映射选错的回去改映射\n"
            "重导，文件本来没号码的就是精准补采名单的输入。")
        self.btn_export_drops.setVisible(False)
        self.btn_export_drops.clicked.connect(self._on_export_drops)

        self.clean_title = QLabel("🧪 入库前清洗报告")
        cf = self.clean_title.font()
        cf.setBold(True)
        self.clean_title.setFont(cf)
        self.clean_title.setStyleSheet("color: #1565c0;")
        lay.addWidget(self.clean_title)

        self.clean_text = QPlainTextEdit()
        self.clean_text.setReadOnly(True)
        self.clean_text.setMaximumHeight(150)
        self.clean_text.setStyleSheet(
            "font-size: 11px; background: #f4f8fb; color: #263238;")
        if self._cleaning_report:
            self._set_cleaning_text(self._cleaning_report)
        else:
            self.clean_text.setPlainText("正在按入库口径预演清洗…")
        lay.addWidget(self.clean_text)

        # 说明
        note = QLabel(
            "数据将上传到团队共享的 OSS 数据池，其他同事可以搜索到这些公司。\n"
            "同一公司名（去后缀后）的联系方式会自动合并，不会覆盖已有数据。"
        )
        note.setStyleSheet("color: #888; font-size: 10px;")
        note.setWordWrap(True)
        lay.addWidget(note)

        lay.addStretch()

        # 按钮行
        btn_row = QHBoxLayout()
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setMinimumHeight(36)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_sync = QPushButton("开始同步")
        self.btn_sync.setMinimumHeight(36)
        self.btn_sync.setMinimumWidth(120)
        self.btn_sync.setObjectName("primary")
        self.btn_sync.setStyleSheet(
            "background: #1a73e8; color: white; font-weight: bold; "
            "border-radius: 4px; padding: 6px 20px;"
        )
        self.btn_sync.clicked.connect(self._start_sync)
        btn_row.addWidget(self.btn_cancel)
        btn_row.addWidget(self.btn_export_drops)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_sync)
        lay.addLayout(btn_row)

        # 结果标签
        self.result_lbl = QLabel("")
        self.result_lbl.setWordWrap(True)
        self.result_lbl.setVisible(False)
        lay.addWidget(self.result_lbl)

    def _set_cleaning_text(self, report: dict):
        """渲染清洗报告（同一份渲染逻辑给预览和结果两处用）。"""
        try:
            from ingest_cleaning import format_report
            text = format_report(report)
        except Exception:
            text = ""
        dropped = int((report or {}).get("dropped", 0))
        kept = int((report or {}).get("kept", 0))
        head = []
        if (report or {}).get("skipped_sheets"):
            head.append("⚠ 有整表被判定为说明表/进度表")
        if dropped:
            head.append(f"⚠ {dropped} 条不会入库（原因见下）")
        if kept and not dropped:
            head.append("✅ 全部记录符合入库口径")
        self.clean_text.setPlainText(
            ("\n".join(head) + "\n\n" if head else "") + (text or "（无报告）"))
        color = "#c62828" if dropped else "#2e7d32"
        self.clean_title.setStyleSheet(f"color: {color}; font-weight: bold;")
        # 剔除数写在按钮上：用户不用先猜「这次到底有没有被拦掉的行」
        if dropped and (report or {}).get("dropped_rows") is not None:
            self.btn_export_drops.setText(f"导出未入库清单（{dropped} 条）")
            self.btn_export_drops.setVisible(True)
        else:
            self.btn_export_drops.setVisible(False)

    def _on_export_drops(self):
        """导出未入库清单：路径由用户选，写成功后给出条数与位置。"""
        report = self._cleaning_report or {}
        if not (report.get("dropped_rows") or []):
            QMessageBox.information(self, "未入库清单", "这次没有被剔除的记录。")
            self.btn_export_drops.setVisible(False)
            return
        default_name = (f"未入库清单_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx")
        path, _ = QFileDialog.getSaveFileName(
            self, "导出未入库清单", default_name, "Excel 文件 (*.xlsx)")
        if not path:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            n, saved = self._export_drops_to(path)
        finally:
            QApplication.restoreOverrideCursor()
        over = int(report.get("dropped_overflow") or 0)
        QMessageBox.information(
            self, "导出成功",
            f"已导出 {n} 条未入库记录到:\n{saved}"
            + (f"\n另有 {over} 条超出保留上限，只在「汇总」表里计数" if over else ""))

    def _export_drops_to(self, path: str):
        """真正写文件（与报告同一个数据源，绝不在界面侧重算一遍剔除口径）。

        几万行的写入是秒级动作，压一次就把「锁定 UI + 等待光标」这条交互约定用上，
        而不是再造一套线程状态机。
        """
        from ingest_cleaning import export_dropped_rows
        return export_dropped_rows(self._cleaning_report or {}, path)

    def showEvent(self, event):
        """对话框可见后再跑清洗预览：数据量大时（几万条）不能在 UI 线程算。"""
        super().showEvent(event)
        if self._cleaning_report or self._preview_worker is not None:
            return
        if not self.records:
            return
        self._preview_worker = CleaningPreviewWorker(self.records)
        self._preview_worker.finished.connect(self._on_preview_ready)
        self._preview_worker.start()

    def _on_preview_ready(self, report: dict, text: str):
        self._cleaning_report = report or None
        if report:
            self._set_cleaning_text(report)
        else:
            self.clean_text.setPlainText(text or "清洗预览无结果")

    def _start_sync(self):
        self.btn_sync.setEnabled(False)
        self.btn_cancel.setEnabled(False)

        # 显示进度
        self._progress = PoolProgressDialog(self, "同步到数据池")
        self._progress.show()

        self._worker = SyncWorker(self.records)
        self._worker.progress.connect(self._progress.set_text)
        self._worker.finished.connect(self._on_sync_finished)
        self._worker.start()

    def _uploaded_count(self) -> int:
        """真正入库的条数：以清洗报告 kept 为准，报告缺失才退回记录数。"""
        report = getattr(self._worker, "report", None) or {}
        kept = report.get("kept")
        return int(kept) if kept is not None else len(self.records)

    def _on_sync_finished(self, success: bool, message: str):
        if hasattr(self, '_progress'):
            self._progress.allow_close()
            self._progress.close()

        self.result_lbl.setVisible(True)
        if success:
            uploaded = self._uploaded_count()
            # 回写给调用方：只有这里才是「真的传进去了」的唯一证据点
            self.uploaded_ok = True
            self.uploaded_kept = uploaded
            m = re.search(r"文件名: \[?([^\]\n]+)", message or "")
            self.uploaded_batch = m.group(1).strip() if m else ""
            self.result_lbl.setStyleSheet("color: #2e7d32; font-weight: bold;")
            self.result_lbl.setText(f"✅ {message}")
            self.btn_sync.setText("完成")
            self.btn_cancel.setText("关闭")
            self.btn_cancel.setEnabled(True)
            # 上传后就显示最新清洗报告（与真正入库那次是同一口径）
            report = getattr(self._worker, "report", None) or {}
            if report:
                self._set_cleaning_text(report)
            # 醒目 Toast 通知（非阻塞，自动消失，大厂做法）
            main_win = self.parent()
            if main_win:
                _show_toast(main_win,
                    f"\u2705 成功上传 {uploaded} 条记录到数据池",
                    'success')
            # 审计日志：上传成功
            if _HAS_POOL:
                try:
                    mgr = DataPoolManager()
                    get_audit_logger().log("upload", mgr.username, {
                        "record_count": uploaded,
                        "record_submitted": len(self.records),
                        "message": message,
                    })
                except Exception:
                    pass
        else:
            self.result_lbl.setStyleSheet("color: #c62828;")
            self.result_lbl.setText(f"❌ {message}")
            self.btn_sync.setEnabled(True)
            self.btn_cancel.setEnabled(True)
            self.btn_sync.setText("重试")

    def done(self, result):
        """统一收尾：清洗预览线程还在跑就先断开信号再等它结束。

        QThread 对象被 Python 销毁时线程仍在运行会直接崩进程；预览是本地
        内存计算（万级亚秒），等它几百毫秒的成本可以忽略。
        """
        w = self._preview_worker
        if w is not None and w.isRunning():
            try:
                w.finished.disconnect(self)
            except TypeError:
                pass
            w.wait(5000)
        super().done(result)

    def _show_toast(self, message, toast_type='info'):
        """显示非阻塞 Toast 通知。"""
        _show_toast(self, message, toast_type)


# ─────────────────────────────────────────────────────────────────────────────
#  带自动补全的搜索输入框
# ─────────────────────────────────────────────────────────────────────────────

class SuggestLineEdit(QWidget):
    """带下拉建议列表的搜索输入框（参考 Google / 百度搜索框）。

    信号：
      - suggest_selected(str): 用户点击了某个建议项
      - returnPressed():       用户按了回车
    """
    suggest_selected = pyqtSignal(str)
    returnPressed = pyqtSignal()

    def __init__(self, placeholder: str = "", parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.input = QLineEdit()
        self.input.setPlaceholderText(placeholder)
        self.input.setMinimumHeight(34)
        self.input.returnPressed.connect(self.returnPressed.emit)
        self.input.textChanged.connect(self._on_text_changed)
        lay.addWidget(self.input)

        # 下拉建议列表
        self.list_widget = QListWidget()
        self.list_widget.setMaximumHeight(200)
        self.list_widget.setVisible(False)
        self.list_widget.setStyleSheet(
            "QListWidget { background: white; border: 1px solid #ccc; "
            "border-radius: 4px; font-size: 12px; }"
            "QListWidget::item { padding: 6px 10px; }"
            "QListWidget::item:hover { background: #e8f0fe; }"
            "QListWidget::item:selected { background: #1a73e8; color: white; }"
        )
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        lay.addWidget(self.list_widget)

        self._suggester = None
        self._master = []

    def set_data(self, suggester, master: list):
        """设置建议引擎和数据池。"""
        self._suggester = suggester
        self._master = master

    def text(self) -> str:
        return self.input.text()

    def setText(self, text: str):
        self.input.setText(text)

    def setFocus(self):
        self.input.setFocus()

    def _on_text_changed(self, text: str):
        """输入变化时更新建议列表。"""
        text = text.strip()
        if not self._suggester or len(text) < 2:
            self.list_widget.setVisible(False)
            return
        suggestions = self._suggester.get_suggestions(text, self._master)
        if not suggestions:
            self.list_widget.setVisible(False)
            return
        self.list_widget.clear()
        type_icons = {"history": "\U0001f550", "company": "\U0001f3e2",
                      "product": "\U0001f4e6", "synonym": "\U0001f4a1"}
        for s in suggestions:
            icon = type_icons.get(s.get("type", ""), "")
            item = QListWidgetItem(f"{icon} {s['text']}")
            item.setData(Qt.UserRole, s["text"])
            self.list_widget.addItem(item)
        self.list_widget.setVisible(True)

    def _on_item_clicked(self, item: QListWidgetItem):
        """点击建议项：填入搜索框并触发搜索。"""
        text = item.data(Qt.UserRole)
        if text:
            self.input.setText(text)
            self.list_widget.setVisible(False)
            self.suggest_selected.emit(text)

    def show_history(self):
        """显示搜索历史（搜索框获得焦点但无输入时）。"""
        if not self._suggester:
            return
        history = self._suggester.get_history(limit=8)
        if not history:
            return
        popular = self._suggester.get_popular(top_n=3)
        self.list_widget.clear()
        # 分区展示
        if popular:
            header1 = QListWidgetItem("\U0001f525 热门搜索")
            header1.setFlags(header1.flags() & ~Qt.ItemIsSelectable)
            header1.setForeground(QColor("#888"))
            self.list_widget.addItem(header1)
            for q, count in popular:
                item = QListWidgetItem(f"   {q}")
                item.setData(Qt.UserRole, q)
                self.list_widget.addItem(item)
        if history:
            header2 = QListWidgetItem("\U0001f550 搜索历史")
            header2.setFlags(header2.flags() & ~Qt.ItemIsSelectable)
            header2.setForeground(QColor("#888"))
            self.list_widget.addItem(header2)
            for q, ts, count in history[:5]:
                item = QListWidgetItem(f"   {q}")
                item.setData(Qt.UserRole, q)
                self.list_widget.addItem(item)
        self.list_widget.setVisible(True)


# ─────────────────────────────────────────────────────────────────────────────
#  搜索采购商对话框
# ─────────────────────────────────────────────────────────────────────────────

class SearchDialog(QDialog):
    """从数据池智能搜索下游采购商。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("搜索采购商")
        self.setMinimumSize(900, 600)
        self.resize(1000, 650)
        self._master: List[dict] = []
        self._results: List[dict] = []
        self._all_results: Optional[List[dict]] = None  # 筛选前原始结果
        self._load_worker = None
        self._search_worker = None
        self._recommend_worker = None   # 推荐走后台线程，界面不再被全表打分冻住
        self._last_use_deepseek = False  # 筛选重检时沿用上一次的模式，未搜索过则为本地搜索
        self._current_query = ""         # 本次结果对应的关键词（概览/导出/审计共用）
        # 持久数据池管理器：加载总表与搜索定位候选共用同一实例，
        # 保证倒排索引与内存总表同源（错位索引会把搜索结果全弄丢）
        self._pool_mgr = DataPoolManager() if _HAS_POOL else None
        self._init_ui()
        self._load_master()

    def _init_ui(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        lay.setContentsMargins(14, 12, 14, 12)

        # ── 搜索栏（带自动补全） ──
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("搜索："))
        if _HAS_SUGGEST:
            self.search_input = SuggestLineEdit(
                "输入公司名、产品关键词或行业描述，如「光伏组件」「储能电池」「逆变器制造商」"
            )
            self.search_input.returnPressed.connect(self._on_search_local)
            self.search_input.suggest_selected.connect(self._on_suggest_selected)
        else:
            # 降级：无搜索建议模块时用普通 QLineEdit
            self.search_input = QLineEdit()
            self.search_input.setPlaceholderText(
                "输入公司名、产品关键词或行业描述，如「光伏组件」「储能电池」「逆变器制造商」"
            )
            self.search_input.setMinimumHeight(34)
            self.search_input.returnPressed.connect(self._on_search_local)
        search_row.addWidget(self.search_input, 1)

        self.btn_search = QPushButton("搜索")
        self.btn_search.setMinimumHeight(34)
        self.btn_search.setMinimumWidth(80)
        self.btn_search.clicked.connect(self._on_search_local)
        search_row.addWidget(self.btn_search)

        self.btn_deepseek = QPushButton("智能匹配")
        self.btn_deepseek.setMinimumHeight(34)
        self.btn_deepseek.setMinimumWidth(100)
        self.btn_deepseek.setToolTip(
            "调用 DeepSeek AI 进行语义理解匹配。\n"
            "能识别「光伏组件制造商」匹配到「太阳能板生产企业」这类语义关联。\n"
            "需要配置 DeepSeek API Key（已配置则自动调用）。"
        )
        self.btn_deepseek.clicked.connect(self._on_search_deepseek)
        search_row.addWidget(self.btn_deepseek)
        lay.addLayout(search_row)

        # ── 搜索选项行：结果数量 + 脱敏开关 ──
        opts_row = QHBoxLayout()
        opts_row.setSpacing(16)

        # 结果数量
        lbl_max = QLabel("最多显示：")
        lbl_max.setStyleSheet("color: #888; font-size: 11px;")
        opts_row.addWidget(lbl_max)
        self.spin_max_results = QSpinBox()
        self.spin_max_results.setRange(10, 1000)
        self.spin_max_results.setValue(100)
        self.spin_max_results.setSingleStep(10)
        self.spin_max_results.setMinimumWidth(70)
        self.spin_max_results.setToolTip("设置搜索结果的最大返回数量（默认 100 条）")
        opts_row.addWidget(self.spin_max_results)
        lbl_unit = QLabel("条")
        lbl_unit.setStyleSheet("color: #888; font-size: 11px;")
        opts_row.addWidget(lbl_unit)

        opts_row.addSpacing(24)

        # 脱敏开关
        self.chk_mask = QCheckBox("隐藏联系方式（脱敏）")
        self.chk_mask.setChecked(False)
        self.chk_mask.setToolTip("勾选后手机号、邮箱、微信号将显示为密文（如 138****5678）")
        self.chk_mask.stateChanged.connect(self._on_mask_toggled)
        opts_row.addWidget(self.chk_mask)

        opts_row.addStretch()
        lay.addLayout(opts_row)

        # ── 高级筛选面板（可折叠，默认收起） ──
        self.filter_toggle = QPushButton("高级筛选 \u25bc")
        self.filter_toggle.setFlat(True)
        self.filter_toggle.setStyleSheet(
            "QPushButton { color: #1a73e8; font-size: 11px; border: none; text-align: left; }"
        )
        self.filter_toggle.clicked.connect(self._toggle_filter_panel)
        lay.addWidget(self.filter_toggle)

        self.filter_frame = QFrame()
        self.filter_frame.setVisible(False)
        self.filter_frame.setStyleSheet(
            "QFrame { background: #f8f9fa; border: 1px solid #e0e0e0; border-radius: 6px; }"
        )
        filter_lay = QVBoxLayout(self.filter_frame)
        filter_lay.setContentsMargins(12, 8, 12, 8)
        filter_lay.setSpacing(6)

        # 第一行：国内外多选（查询范围） + 地区 + 排序
        f_row1 = QHBoxLayout()
        f_row1.addWidget(QLabel("查询范围："))
        self.chk_region_dom = QCheckBox("国内")
        self.chk_region_dom.setChecked(True)   # 默认勾选国内
        self.chk_region_dom.setToolTip("勾选后搜索结果只含国内公司（可与「国外」同时勾选）")
        self.chk_region_intl = QCheckBox("国外")
        self.chk_region_intl.setToolTip("勾选后搜索结果只含国外公司（可与「国内」同时勾选）")
        f_row1.addWidget(self.chk_region_dom)
        f_row1.addWidget(self.chk_region_intl)
        f_row1.addSpacing(16)
        f_row1.addWidget(QLabel("地区："))
        self.combo_region = QComboBox()
        self.combo_region.setMinimumWidth(120)
        self.combo_region.addItem("全部")
        f_row1.addWidget(self.combo_region, 1)
        f_row1.addSpacing(16)
        f_row1.addWidget(QLabel("排序："))
        self.combo_sort = QComboBox()
        self.combo_sort.addItems(["相关度\u2193", "公司名 A-Z", "数据新鲜度\u2193", "联系方式完整度\u2193"])
        f_row1.addWidget(self.combo_sort, 1)
        filter_lay.addLayout(f_row1)

        # 第二行：联系方式勾选
        f_row2 = QHBoxLayout()
        # 「只看有联系方式」：资源池里有一成多只有官网/地址的展商线索行（后续可以
        # 拿公司名去三个平台补采）。想要能直接打电话的名单时一键排除它们；不勾就
        # 是全量，一条都不藏——筛选是视图层的开关，绝不是把数据删掉。
        self.chk_f_any = QCheckBox("只看有联系方式")
        self.chk_f_any.setToolTip("勾选后只保留有手机号/固话/邮箱/微信的公司；"
                                  "只有官网或地址的公司被暂时排除在本次结果外（数据仍在池里）")
        f_row2.addWidget(self.chk_f_any)
        f_row2.addSpacing(16)
        f_row2.addWidget(QLabel("必须有："))
        self.chk_f_mobile = QCheckBox("手机号")
        self.chk_f_email = QCheckBox("邮箱")
        self.chk_f_wechat = QCheckBox("微信")
        self.chk_f_phone = QCheckBox("固话")
        f_row2.addWidget(self.chk_f_mobile)
        f_row2.addWidget(self.chk_f_email)
        f_row2.addWidget(self.chk_f_wechat)
        f_row2.addWidget(self.chk_f_phone)
        f_row2.addSpacing(16)
        f_row2.addWidget(QLabel("新鲜度："))
        self.combo_fresh = QComboBox()
        self.combo_fresh.addItems(["全部", "30天内", "90天内", "180天内", "半年内"])
        f_row2.addWidget(self.combo_fresh)
        f_row2.addSpacing(16)
        f_row2.addWidget(QLabel("规模："))
        self.combo_scale = QComboBox()
        self.combo_scale.addItems(["全部", "大型(集团/股份)", "中型(有限公司)", "小型(个体/厂)"])
        f_row2.addWidget(self.combo_scale)
        f_row2.addStretch()
        filter_lay.addLayout(f_row2)

        # 应用筛选按钮
        f_row3 = QHBoxLayout()
        f_row3.addStretch()
        self.btn_filter_apply = QPushButton("应用筛选")
        self.btn_filter_apply.setMinimumHeight(28)
        self.btn_filter_apply.setStyleSheet(
            "QPushButton { background: #1a73e8; color: white; border-radius: 4px; "
            "padding: 4px 16px; font-size: 11px; }"
        )
        self.btn_filter_apply.clicked.connect(self._on_apply_filter)
        f_row3.addWidget(self.btn_filter_apply)
        self.btn_filter_reset = QPushButton("重置")
        self.btn_filter_reset.setMinimumHeight(28)
        self.btn_filter_reset.setStyleSheet(
            "QPushButton { background: #e0e0e0; color: #333; border-radius: 4px; "
            "padding: 4px 12px; font-size: 11px; }"
        )
        self.btn_filter_reset.clicked.connect(self._on_reset_filter)
        f_row3.addWidget(self.btn_filter_reset)
        filter_lay.addLayout(f_row3)

        lay.addWidget(self.filter_frame)

        # ── 数据概览面板（可折叠，默认收起） ──
        self.analytics_toggle = QPushButton("\U0001f4ca 数据概览 \u25bc")
        self.analytics_toggle.setFlat(True)
        self.analytics_toggle.setStyleSheet(
            "QPushButton { color: #1a73e8; font-size: 11px; border: none; text-align: left; }"
        )
        self.analytics_toggle.clicked.connect(self._toggle_analytics_panel)
        lay.addWidget(self.analytics_toggle)

        self.analytics_frame = QFrame()
        self.analytics_frame.setVisible(False)
        self.analytics_frame.setStyleSheet(
            "QFrame { background: #fafafa; border: 1px solid #e0e0e0; border-radius: 6px; }"
        )
        analytics_lay = QVBoxLayout(self.analytics_frame)
        analytics_lay.setContentsMargins(12, 8, 12, 8)
        analytics_lay.setSpacing(6)

        # 概览卡片
        cards_row = QHBoxLayout()
        self.card_total = QLabel("\U0001f4ca --")
        self.card_region = QLabel("\U0001f4cd --")
        self.card_industry = QLabel("\U0001f3ed --")
        self.card_contact = QLabel("\U0001f4de --")
        for card in [self.card_total, self.card_region, self.card_industry, self.card_contact]:
            card.setStyleSheet(
                "QLabel { background: white; border: 1px solid #e0e0e0; border-radius: 6px; "
                "padding: 8px; font-size: 12px; font-weight: bold; }"
            )
            card.setAlignment(Qt.AlignCenter)
            card.setMinimumWidth(140)
            cards_row.addWidget(card)
        analytics_lay.addLayout(cards_row)

        # 分布统计文本区
        self.analytics_text = QPlainTextEdit()
        self.analytics_text.setReadOnly(True)
        self.analytics_text.setMaximumHeight(120)
        self.analytics_text.setStyleSheet(
            "QPlainTextEdit { background: white; border: 1px solid #e0e0e0; "
            "border-radius: 4px; font-size: 11px; font-family: monospace; }"
        )
        self.analytics_text.setPlainText("搜索后自动展示行业与地区分布")
        analytics_lay.addWidget(self.analytics_text)

        lay.addWidget(self.analytics_frame)

        # ── 思考过程面板（参考 ChatGPT / 通义千问的“思考中”展示） ──
        self.think_frame = QFrame()
        self.think_frame.setVisible(False)
        self.think_frame.setMaximumHeight(120)
        self.think_frame.setStyleSheet(
            "QFrame { background: #1e1e2e; border: 1px solid #333; border-radius: 6px; }"
        )
        think_lay = QVBoxLayout(self.think_frame)
        think_lay.setContentsMargins(10, 8, 10, 8)
        think_lay.setSpacing(2)

        think_header = QHBoxLayout()
        self.think_icon = QLabel("\U0001f916")
        self.think_icon.setStyleSheet("font-size: 14px; border: none;")
        think_header.addWidget(self.think_icon)
        self.think_title = QLabel("思考中...")
        f_think = self.think_title.font()
        f_think.setPointSize(10)
        f_think.setBold(True)
        self.think_title.setFont(f_think)
        self.think_title.setStyleSheet("color: #a0a0c0; border: none;")
        think_header.addWidget(self.think_title)
        think_header.addStretch()
        think_lay.addLayout(think_header)

        self.think_text = QPlainTextEdit()
        self.think_text.setReadOnly(True)
        self.think_text.setMaximumHeight(80)
        self.think_text.setStyleSheet(
            "QPlainTextEdit { background: transparent; color: #c0c0d0; "
            "border: none; font-size: 11px; font-family: 'Menlo', 'Consolas', monospace; }"
        )
        think_lay.addWidget(self.think_text)
        lay.addWidget(self.think_frame)

        # ── 状态栏（醒目：大字号 + 加粗，深色主题下也清晰可见） ──
        self.status_lbl = QLabel("正在加载数据池…")
        f_status = self.status_lbl.font()
        f_status.setPointSize(12)
        f_status.setBold(True)
        self.status_lbl.setFont(f_status)
        self.status_lbl.setStyleSheet("color: #e0e0e0; padding: 4px 0;")
        lay.addWidget(self.status_lbl)

        # 思考步骤追加方法（供搜索期间实时显示）
        self._think_lines = 0

        # ── 结果表格 ──
        self.table = QTableWidget()
        self.table.setColumnCount(9)
        self.table.setHorizontalHeaderLabels([
            "✓", "公司名", "联系人", "手机号", "固话/邮箱", "微信", "主营/来源", "匹配度", "反馈"
        ])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(7, QHeaderView.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(8, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 30)
        self.table.setColumnWidth(2, 70)
        self.table.setColumnWidth(3, 120)
        self.table.setColumnWidth(5, 90)
        self.table.setColumnWidth(7, 70)
        self.table.setColumnWidth(8, 100)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_table_context_menu)
        lay.addWidget(self.table, 1)

        # ── 底部操作栏 ──
        bottom = QHBoxLayout()

        # 全选
        self.chk_select_all = QCheckBox("全选")
        self.chk_select_all.stateChanged.connect(self._on_select_all)
        bottom.addWidget(self.chk_select_all)

        # 选中计数
        self.lbl_selected = QLabel("已选 0 条")
        self.lbl_selected.setStyleSheet("color: #888;")
        bottom.addWidget(self.lbl_selected)

        bottom.addStretch()

        # 导出
        self.btn_export = QPushButton("导出 Excel")
        self.btn_export.setMinimumHeight(34)
        self.btn_export.setMinimumWidth(100)
        self.btn_export.setEnabled(False)
        self.btn_export.clicked.connect(self._on_export)
        bottom.addWidget(self.btn_export)

        # 关闭
        self.btn_close = QPushButton("关闭")
        self.btn_close.setMinimumHeight(34)
        self.btn_close.clicked.connect(self.close)
        bottom.addWidget(self.btn_close)

        lay.addLayout(bottom)

        # ── 匹配理由面板（底部展开） ──
        self.reason_frame = QFrame()
        self.reason_frame.setVisible(False)
        self.reason_frame.setMaximumHeight(80)
        self.reason_frame.setStyleSheet(
            "QFrame { background: #f5f5f5; border: 1px solid #ddd; border-radius: 4px; }"
        )
        reason_lay = QVBoxLayout(self.reason_frame)
        self.reason_lbl = QLabel("")
        self.reason_lbl.setWordWrap(True)
        self.reason_lbl.setStyleSheet("color: #333; font-size: 11px; border: none;")
        reason_lay.addWidget(self.reason_lbl)
        lay.addWidget(self.reason_frame)

        # 表格选中事件 → 显示理由
        self.table.itemSelectionChanged.connect(self._on_row_selected)

    # ── 加载总表 ──

    def _load_master(self):
        # 秒开加载：本地缓存立即返回 + 后台静默同步
        self._load_worker = LoadMasterWorker(
            force_refresh=False, mgr=self._pool_mgr
        )
        self._load_worker.progress.connect(
            lambda msg: self.status_lbl.setText(msg)
        )
        self._load_worker.finished.connect(self._on_master_loaded)
        self._load_worker.refreshed.connect(self._on_refreshed)
        self._load_worker.start()

    def _on_master_loaded(self, success: bool, master: list, message: str):
        if success:
            self._master = master
            self._all_results = None  # 筛选前原始结果
            self._populate_region_combo()  # 填充筛选面板的地区下拉
            # 设置搜索建议数据
            if _HAS_SUGGEST and hasattr(self.search_input, 'set_data'):
                suggester = get_search_suggester()
                self.search_input.set_data(suggester, master)
            self.status_lbl.setText(message)
            self.status_lbl.setStyleSheet(
                "color: #4caf50; font-weight: bold; padding: 6px 0; "
                "background: #1b3a1b; border-radius: 4px;"
            )
            # 醒目 Toast 通知（非阻塞，自动消失，大厂做法）
            self._show_toast(
                f"\u2705 数据池已就绪 \u2014 {message}", 'success')
            # 检查 DeepSeek 可用性
            try:
                from pool_matcher import CompanyMatcher
                key = CompanyMatcher._load_api_key()
                if not key:
                    self.btn_deepseek.setEnabled(False)
                    self.btn_deepseek.setToolTip("未配置 DeepSeek API Key，仅可使用本地搜索")
            except Exception:
                self.btn_deepseek.setEnabled(False)
        else:
            self.status_lbl.setText(f"加载失败: {message}")
            self.status_lbl.setStyleSheet(
                "color: #ef5350; font-weight: bold; padding: 6px 0; "
                "background: #3a1b1b; border-radius: 4px;"
            )
            QMessageBox.warning(
                self, "数据池加载失败",
                f"{message}\n\n请检查网络连接后重试。"
            )

    def _on_refreshed(self, master: list, message: str):
        """后台同步完成：有新数据时更新 UI 并 Toast 通知。"""
        self._master = master
        self.status_lbl.setText(message)
        self.status_lbl.setStyleSheet(
            "color: #42a5f5; font-weight: bold; padding: 6px 0; "
            "background: #1a2a3a; border-radius: 4px;"
        )
        # 非阻塞 Toast（后台同步有新数据才通知）
        self._show_toast(f"\u2705 数据已同步 \u2014 {message}", 'info')

    # ── 搜索 ──

    def _show_toast(self, message, toast_type='info'):
        """显示非阻塞 Toast 通知。"""
        _show_toast(self, message, toast_type)

    def _on_search_local(self):
        query = self.search_input.text().strip()
        if not query:
            QMessageBox.warning(self, "请输入搜索内容", "请在搜索框中输入公司名或产品关键词")
            return
        if not self._master:
            QMessageBox.warning(self, "数据池未加载", "请等待数据池加载完成后再搜索")
            return
        self._do_search(query, use_deepseek=False)

    def _on_search_deepseek(self):
        query = self.search_input.text().strip()
        if not query:
            QMessageBox.warning(self, "请输入搜索内容", "请在搜索框中输入公司名或产品关键词")
            return
        if not self._master:
            QMessageBox.warning(self, "数据池未加载", "请等待数据池加载完成后再搜索")
            return
        self._do_search(query, use_deepseek=True)

    def _on_suggest_selected(self, text: str):
        """用户点击了搜索建议项，自动触发搜索。"""
        if text and self._master:
            self._do_search(text, use_deepseek=True)

    # ── 国内外查询范围 ──

    def _selected_regions(self) -> list:
        """读取国内外勾选：["dom"] / ["intl"] / ["dom","intl"]；都未勾返回 []。

        双选等于不限：不预先过滤候选集，但结果仍带显式 is_domestic，
        后续改勾选再搜索/筛选口径始终一致。
        """
        regions = []
        if self.chk_region_dom.isChecked():
            regions.append("dom")
        if self.chk_region_intl.isChecked():
            regions.append("intl")
        return regions

    def _region_scope_ok(self) -> bool:
        """国内外至少勾一个才能查（就地阻断，不允许静默返空）。"""
        if self._selected_regions():
            return True
        self._show_toast("⚠ 请至少勾选「国内」或「国外」一个查询范围", 'warning')
        return False

    def _region_scope_label(self) -> str:
        """查询范围标签（思考面板/提示文案用）。"""
        labels = {"dom": "国内", "intl": "国外"}
        regions = self._selected_regions()
        if len(regions) == 2:
            return "国内+国外"
        return labels.get(regions[0], "") if regions else ""

    def _do_search(self, query: str, use_deepseek: bool):
        if not self._region_scope_ok():
            return
        self._current_query = query  # 保存当前查询供审计日志使用
        self._last_use_deepseek = bool(use_deepseek)  # 筛选重检时沿用同一模式
        # 记录搜索历史
        if _HAS_SUGGEST:
            try:
                get_search_suggester().add_history(query)
            except Exception:
                pass
        self.btn_search.setEnabled(False)
        self.btn_deepseek.setEnabled(False)
        self.status_lbl.setText("搜索中…")
        self.status_lbl.setStyleSheet("color: #42a5f5; font-weight: bold; padding: 4px 0;")

        # 显示思考面板并清空历史内容
        self.think_frame.setVisible(True)
        self.think_text.clear()
        self._think_lines = 0
        mode_text = "AI 智能分析" if use_deepseek else "本地搜索"
        self.think_title.setText(f"💭 思考中... ({mode_text})")
        self.think_icon.setText("\U0001f916" if use_deepseek else "\U0001f50d")

        # 第一步：展示用户输入了什么
        scope_label = self._region_scope_label()
        self._append_think(f"📝 用户输入了「{query}」（范围：{scope_label}）")

        # 读取用户设置的最大结果数
        max_results = self.spin_max_results.value()
        self._append_think(f"🎯 准备返回最多 {max_results} 条结果")

        # 候选集由后台线程取（视图 → 倒排索引 → 全表 逐级回退）：
        # 这里绝不做任何磁盘解析，点击路径上只发消息
        filters = self._get_filter_params()
        if filters:
            # 只报已经确定的事：这一步还不知道最终走哪条检索路径，
            # 提前说「用了检索视图」就是宣称没发生过的动作
            self._append_think(f"🔧 已带上 {len(filters)} 项筛选条件，"
                              f"筛选在取数据时一起生效（不是先取一页再挑一遍）")
        self._search_worker = SearchWorker(query, self._master, use_deepseek,
                                           None, max_results,
                                           mgr=self._pool_mgr,
                                           regions=self._selected_regions(),
                                           filters=filters)
        # 思考步骤同时更新到面板和状态栏
        self._search_worker.progress.connect(self._on_think_step)
        self._search_worker.finished.connect(self._on_search_finished)
        self._search_worker.start()

    def _on_think_step(self, msg: str):
        """思考步骤：同时更新状态栏和思考面板。"""
        self.status_lbl.setText(msg)
        self._append_think(msg)

    def _append_think(self, text: str):
        """追加一行思考日志到面板。"""
        self._think_lines += 1
        timestamp = time.strftime("%H:%M:%S")
        self.think_text.appendPlainText(f"[{timestamp}] {text}")
        # 自动滚动到底部
        sb = self.think_text.verticalScrollBar()
        if sb:
            sb.setValue(sb.maximum())

    def _on_mask_toggled(self, state):
        """脱敏开关切换：如果已有搜索结果，立即重新渲染表格。"""
        if self._results:
            self._fill_table(self._results)

    # ── 高级筛选 ──

    def _toggle_filter_panel(self):
        """折叠/展开高级筛选面板。"""
        visible = not self.filter_frame.isVisible()
        self.filter_frame.setVisible(visible)
        self.filter_toggle.setText("高级筛选 \u25b2" if visible else "高级筛选 \u25bc")

    def _populate_region_combo(self):
        """地区下拉选项：优先用 Gold 视图的真实分布，视图没就绪才回退静态清单。

        口径唯一：选项只来自 province 分区列（与筛选下压、概览分布同源），
        因此「下拉里看到浙江」=「按浙江能筛出东西」=「分布图里浙江那条的长度」，
        三处必然是同一个数。旧实现还并了 country 列，等于给出一个按分区键
        筛不到的选项（选了它结果清零）。
        """
        regions = set()
        mgr = self._pool_mgr
        used_view = False
        if mgr is not None and mgr.store_ready():
            try:
                regions = {str(name).strip()
                           for name, count in (mgr.store_facets("province",
                                                                limit=500) or [])
                           if str(name).strip() and count > 0}
                used_view = bool(regions)
            except Exception:
                used_view = False
        if not used_view:
            # 回退：静态行政区划清单（补齐旧清单缺的西藏/青海/内蒙古/宁夏/
            # 新疆/香港/澳门/台湾），只在地址里出现时列出
            for rec in self._master:
                addr = str(rec.get("address", ""))
                country = str(rec.get("country", "")).strip()
                for province in CN_PROVINCES:
                    if province in addr:
                        regions.add(province)
                        break
                if country and country not in ("中国", ""):
                    regions.add(country)
        # 清空并重新填充
        self.combo_region.blockSignals(True)
        self.combo_region.clear()
        self.combo_region.addItem("全部")
        for r in sorted(regions):
            self.combo_region.addItem(r)
        self.combo_region.blockSignals(False)

    def _get_filter_params(self) -> dict:
        """从筛选面板控件读取当前筛选参数。"""
        filters = {}
        regions = self._selected_regions()
        if regions:
            filters["regions"] = regions
        region = self.combo_region.currentText()
        if region and region != "全部":
            filters["region"] = region
        if self.chk_f_any.isChecked():
            filters["has_contact"] = True
        if self.chk_f_mobile.isChecked():
            filters["has_mobile"] = True
        if self.chk_f_email.isChecked():
            filters["has_email"] = True
        if self.chk_f_wechat.isChecked():
            filters["has_wechat"] = True
        if self.chk_f_phone.isChecked():
            filters["has_phone"] = True
        fresh_text = self.combo_fresh.currentText()
        fresh_map = {"30天内": 30, "90天内": 90, "180天内": 180, "半年内": 180}
        if fresh_text in fresh_map:
            filters["freshness_days"] = fresh_map[fresh_text]
        scale_text = self.combo_scale.currentText()
        if "大型" in scale_text:
            filters["scale"] = "large"
        elif "中型" in scale_text:
            filters["scale"] = "medium"
        elif "小型" in scale_text:
            filters["scale"] = "small"
        return filters

    def _get_sort_key(self) -> str:
        """从排序下拉框读取当前排序键。"""
        sort_text = self.combo_sort.currentText()
        sort_map = {"相关度\u2193": "score", "公司名 A-Z": "name_az",
                    "数据新鲜度\u2193": "fresh", "联系方式完整度\u2193": "contact"}
        return sort_map.get(sort_text, "score")

    def _on_apply_filter(self):
        """应用筛选：让筛选**参与检索**，而不是只在已返回的那一页里挑。

        页内筛选是「筛后 30 / 100」这类误导数字的来源：池里符合条件的可能有
        几千条，只是没被返回。视图可用时重新下压检索（实测 66,893 条 0.05s，
        用户无感），拿不到视图时才退回页内筛——那时结果集本来就只有一页。
        """
        if not self._results:
            self._show_toast("\u26a0 请先搜索，再筛选", 'warning')
            return
        if not self._region_scope_ok():
            return
        query = self._current_query
        worker_running = (self._search_worker is not None
                          and self._search_worker.isRunning())
        if (query and not worker_running and self._pool_mgr is not None
                and self._pool_mgr.store_ready()):
            # 沿用上一次的模式（AI 语义扩展不能被筛选悄悄换成本地检索）
            self._do_search(query, self._last_use_deepseek)
            return
        from pool_matcher import CompanyMatcher
        filters = self._get_filter_params()
        sort_key = self._get_sort_key()
        # 先筛选再排序
        filtered = CompanyMatcher.apply_filters(self._all_results or self._results, filters)
        sorted_results = CompanyMatcher.apply_sort(filtered, sort_key)
        self._results = sorted_results
        self._fill_table(sorted_results)
        self.btn_export.setEnabled(bool(sorted_results))
        n_before = len(self._all_results) if self._all_results else len(sorted_results)
        dom_n = sum(1 for r in sorted_results if record_region(r) == "dom")
        intl_n = len(sorted_results) - dom_n
        self._show_toast(
            f"\u2705 筛选完成：{len(sorted_results)} / {n_before} 条（国内 {dom_n} · 国外 {intl_n}）",
            'success'
        )

    def _on_reset_filter(self):
        """重置筛选：恢复搜索结果原始状态。"""
        self.chk_region_dom.setChecked(True)
        self.chk_region_intl.setChecked(False)
        self.combo_region.setCurrentIndex(0)
        self.chk_f_mobile.setChecked(False)
        self.chk_f_email.setChecked(False)
        self.chk_f_wechat.setChecked(False)
        self.chk_f_phone.setChecked(False)
        self.chk_f_any.setChecked(False)
        self.combo_fresh.setCurrentIndex(0)
        self.combo_scale.setCurrentIndex(0)
        self.combo_sort.setCurrentIndex(0)
        if self._all_results:
            self._results = list(self._all_results)
            self._fill_table(self._results)
            self.btn_export.setEnabled(bool(self._results))

    # ── 数据概览 ──

    def _toggle_analytics_panel(self):
        """折叠/展开数据概览面板。"""
        visible = not self.analytics_frame.isVisible()
        self.analytics_frame.setVisible(visible)
        self.analytics_toggle.setText(
            "\U0001f4ca 数据概览 \u25b2" if visible else "\U0001f4ca 数据概览 \u25bc"
        )
        if visible and self._results:
            self._update_analytics(self._results)

    def _analytics_counters(self, results: list, filters: dict):
        """概览用的地区/行业计数：优先用 Gold 视图的 group by，视图不在才本地聚合。

        返回 (地区, 行业, 口径说明, RecallCounts)。口径必须自洽：
        视图路径给的是「本次关键词在池内召回的全部候选」的分布，
        本地路径给的是「本页返回记录」的分布——两者数字含义不同，
        所以口径写进返回值由界面如实标注，绝不能一边显示 100 家、
        一边显示按 15,053 家算出的行业数（历史缺陷正是这种跨口径混用）。

        粗筛数与范围总数是两个必须分开报的数字（词汇与 _scope_note 完全一致，
        见 RecallCounts）：前者按关键词命中、后者只看当前分区 + 筛选范围。
        OR 粗筛宽于精排，搜一整家公司名也可能命中上万条候选，把前者当成
        「池内共 N 条」显示，等于让用户以为池里有一万家长得一样的公司。

        分布与旁边的总数必须来自同一批记录：store_facets 一定要把 filters 带上
        （历史缺陷是分布不带筛选、总数带筛选，同一屏两套口径）。

        分区键口径唯一（与清洗层同源）：
          · 地区 = province 分区键（国内省份名 / 国外国家名 / 未知地区哨兵），
                   视图没就绪时按 ingest_cleaning.partition_province 现算；
          · 行业 = industry 分区键，同样按 partition_industry 现算。
        两个键都「永远非空」，判不出的落显式哨兵桶，所以分布加总 == 同条件总数。
        这里绝不再抄一份「光伏/太阳能/solar」关键词表：历史缺陷就是概览与入库
        两套行业词表，同一批数据在筛选面板和概览卡片里能报出两个行业数。
        """
        from collections import Counter
        query = self._current_query
        mgr = self._pool_mgr
        if mgr is not None and mgr.store_ready():
            regions_sel = self._selected_regions()
            try:
                reg_f = mgr.store_facets("province", query=query,
                                         regions=regions_sel, limit=200,
                                         filters=filters)
                ind_f = mgr.store_facets("industry", query=query,
                                         regions=regions_sel, limit=200,
                                         filters=filters)
                counts = _counts_of(mgr, query, regions_sel, filters)
            except Exception:
                reg_f = ind_f = None
                counts = RecallCounts()
            if reg_f is not None and ind_f is not None:
                regions = Counter({k: v for k, v in reg_f if k})
                industries = Counter({k: v for k, v in ind_f if k})
                parts = _scope_parts(counts)
                if parts:
                    scope_text = "地区/行业分布（" + "、".join(parts) + "）"
                elif counts.by_keyword:
                    # 粗筛确实做过、只是总数没取到：说清范围，绝不编条数
                    scope_text = "地区/行业分布按关键词粗筛出的那批记录统计"
                else:
                    scope_text = "地区/行业分布按刚才那批搜索结果统计"
                return (regions, industries, scope_text, counts)
        try:
            from ingest_cleaning import partition_province, partition_industry
        except Exception:
            partition_province = partition_industry = None
        regions, industries = Counter(), Counter()
        for rec in results:
            # 分区键一律走清洗层的唯一实现（判不出落哨兵桶），这样概览分布的加总
            # 恒等于本页条数；旧写法把判不出的记录直接丢掉，同一屏两个数字对不上
            if partition_province is not None:
                regions[partition_province(rec)] += 1
                industries[partition_industry(rec)] += 1
                continue
            prov = str(rec.get("province") or "").strip()
            if prov:
                regions[prov] += 1
            ind = str(rec.get("industry") or "").strip()
            if ind:
                industries[ind] += 1
        return (regions, industries,
                f"本页 {len(results)} 条（按刚才列出的结果统计，"
                f"本次没用到关键词粗筛）", RecallCounts())

    def _update_analytics(self, results: list):
        """根据搜索结果更新概览卡片和分布统计。"""
        # ── 概览卡片 ──
        total = len(results)
        filters = self._get_filter_params()
        regions, industries, scope, counts = \
            self._analytics_counters(results, filters)
        # 三个数字各说各的事：本页给你看多少 / 关键词粗筛多少 / 当前范围总量
        # （措辞直接复用 _scope_parts，同一个含义在界面里只有一种说法）
        segs, labels = [f"{total} 条"], ["本页"]
        if counts.matched is not None and not counts.merged:
            labels.append("粗筛")
            segs.append(_n(counts.matched, counts.matched_capped))
        if counts.scope_total is not None:
            labels.append("当前范围")
            segs.append(_n(counts.scope_total, counts.scope_capped))
        if labels == ["本页"]:
            # 视图未就绪：只有本页数字是可靠的，绝不猜池内规模
            self.card_total.setText(f"\U0001f4ca 总结果\n{total} 家")
        else:
            self.card_total.setText("\U0001f4ca " + " / ".join(labels) + "\n"
                                    + " / ".join(segs))
        # 「覆盖」是对数据质量的宣称，哨兵桶不算覆盖：把「未知地区/未分类」也
        # 计进「覆盖 N 个」，等于宣称池里覆盖了一个叫「未知地区」的地区。分布里
        # 仍然保留它们（本身就是可筛选的补录清单），只是不参与「覆盖」计数
        self.card_region.setText(
            f"\U0001f4cd 覆盖地区\n{sum(1 for k in regions if k and k != PROVINCE_UNKNOWN)} 个")
        self.card_industry.setText(
            f"\U0001f3ed 涉及行业\n{sum(1 for k in industries if k and k != INDUSTRY_UNKNOWN)} 个")
        # 联系方式完整度：这一项始终按本页统计（视图侧要多次下压才能给全，
        # 而本页样本已足够回答「这批结果干不干净」，卡片上如实标注范围）
        has_contact = sum(1 for r in results
                          if r.get("mobiles") or r.get("phones") or r.get("emails"))
        pct = f"{has_contact * 100 // total}%" if total else "--"
        self.card_contact.setText(f"\U0001f4de 有联系方式（本页）\n{pct}")
        # ── 分布统计文本 ──
        lines = [f"统计口径：{scope}"]
        if regions:
            lines.append("")
            lines.append("【地区分布 Top 10】")
            lines.extend(self._bars(regions))
        if industries:
            lines.append("")
            lines.append("【行业分布 Top 10】")
            lines.extend(self._bars(industries, width=12))
        if len(lines) == 1:
            lines.append("暂无足够数据生成分布统计")
        self.analytics_text.setPlainText("\n".join(lines))

    @staticmethod
    def _bars(counter, width: int = 8, top: int = 10) -> list:
        """把计数分布渲染成条形行：条长按最大值归一（视图数字可达上万，
        固定 20 格会全部顶满看不出差异）。"""
        items = counter.most_common(top)
        if not items:
            return []
        peak = max(count for _, count in items) or 1
        lines = []
        for name, count in items:
            bar = "\u2588" * max(1, int(round(count * 20 / peak)))
            lines.append(f"  {str(name)[:width]:{width}s} {bar} {count}")
        return lines

    def _on_search_finished(self, success: bool, results: list, message: str):
        self.btn_search.setEnabled(True)
        self.btn_deepseek.setEnabled(True)
        self._results = results
        self._all_results = list(results)  # 保存原始结果供筛选重置

        if success:
            if results:
                dom_n = sum(1 for r in results if record_region(r) == "dom")
                intl_n = len(results) - dom_n
                message = f"{message}｜国内 {dom_n} · 国外 {intl_n}"
            self.status_lbl.setText(message)
            self.status_lbl.setStyleSheet(
                "color: #4caf50; font-weight: bold; padding: 4px 0;"
            )
            # 更新思考面板标题
            self.think_title.setText(f"✅ 思考完成")
            self._append_think(f"🎉 {message}")
            if results:
                self._append_think(f"💡 可以点击“导出”把结果保存为 Excel")
            self._fill_table(results)
            self.btn_export.setEnabled(bool(results))
            # 更新数据概览（如果面板已展开）
            if self.analytics_frame.isVisible():
                self._update_analytics(results)
            # 审计日志：搜索成功
            if _HAS_POOL:
                try:
                    query = self._current_query
                    use_deepseek = 'deepseek' in message.lower() or '智能' in message
                    get_audit_logger().log("search", "local_user", {
                        "query": query,
                        "result_count": len(results),
                        "mode": "deepseek" if use_deepseek else "local",
                    })
                except Exception:
                    pass
        else:
            self.status_lbl.setText(f"搜索失败: {message}")
            self.status_lbl.setStyleSheet(
                "color: #ef5350; font-weight: bold; padding: 4px 0;"
            )
            self.think_title.setText(f"❌ 搜索失败")
            self._append_think(f"❌ {message}")

    # ── 表格填充 ──

    def _fill_table(self, results: list):
        # 重置全选框与勾选状态：新结果不继承上一次的勾选
        self.chk_select_all.blockSignals(True)
        self.chk_select_all.setChecked(False)
        self.chk_select_all.blockSignals(False)
        self.lbl_selected.setText("已选 0 条")

        # 脱敏开关：勾选时隐藏联系方式，默认不勾选（显示明文）
        do_mask = self.chk_mask.isChecked()

        self.table.setRowCount(len(results))
        for row_idx, rec in enumerate(results):
            # 复选框（新表格行默认不勾选）
            chk = QCheckBox()
            chk.setChecked(False)
            chk.stateChanged.connect(self._update_selected_count)
            w = QWidget()
            w_lay = QHBoxLayout(w)
            w_lay.addWidget(chk)
            w_lay.setAlignment(Qt.AlignCenter)
            w_lay.setContentsMargins(4, 2, 4, 2)
            self.table.setCellWidget(row_idx, 0, w)

            # 公司名
            self.table.setItem(row_idx, 1, QTableWidgetItem(rec.get("company", "")))

            # 联系人
            contacts = rec.get("contacts", [])
            contact_str = ", ".join(c.get("name", "") for c in contacts if c.get("name"))
            self.table.setItem(row_idx, 2, QTableWidgetItem(contact_str))

            # 手机号（根据脱敏开关决定显示方式）
            mobiles = rec.get("mobiles", [])
            if not mobiles:
                mobiles = rec.get("phones", [])
            if do_mask:
                display_mobiles = [_mask_phone(m) for m in mobiles[:2]]
            else:
                display_mobiles = mobiles[:2]
            self.table.setItem(row_idx, 3, QTableWidgetItem(", ".join(display_mobiles)))

            # 固话/邮箱（根据脱敏开关决定显示方式）
            phones = rec.get("phones", [])
            emails = rec.get("emails", [])
            if do_mask:
                display_phones = [_mask_phone(p) for p in phones[:1]]
                display_emails = [_mask_email(e) for e in emails[:1]]
            else:
                display_phones = phones[:1]
                display_emails = emails[:1]
            contact_info = ", ".join(display_phones + display_emails)
            self.table.setItem(row_idx, 4, QTableWidgetItem(contact_info))

            # 微信（根据脱敏开关决定显示方式）
            wechats = rec.get("wechats", [])
            if do_mask:
                display_wechats = [_mask_wechat(w) for w in wechats[:1]]
            else:
                display_wechats = wechats[:1]
            self.table.setItem(row_idx, 5, QTableWidgetItem(", ".join(display_wechats)))

            # 主营/来源
            products = rec.get("products", "")[:30]
            source = rec.get("source", "")[:20]
            self.table.setItem(row_idx, 6, QTableWidgetItem(
                f"{products} | {source}" if products else source
            ))

            # 匹配度
            score = rec.get("_score", 0)
            score_item = QTableWidgetItem(f"{score:.0%}")
            score_item.setTextAlignment(Qt.AlignCenter)
            # 颜色编码：>0.6 绿，>0.3 橙，否则灰
            if score > 0.6:
                score_item.setForeground(QColor("#2e7d32"))
            elif score > 0.3:
                score_item.setForeground(QColor("#e65100"))
            else:
                score_item.setForeground(QColor("#888"))
            self.table.setItem(row_idx, 7, score_item)

            # 反馈按钮（👍/👎/🚫）
            if _HAS_FEEDBACK_STORE:
                fb_widget = QWidget()
                fb_lay = QHBoxLayout(fb_widget)
                fb_lay.setContentsMargins(2, 2, 2, 2)
                fb_lay.setSpacing(2)
                btn_up = QToolButton()
                btn_up.setText("\U0001f44d")  # 👍
                btn_up.setToolTip("这家相关 / 有用")
                btn_up.setFixedSize(28, 24)
                btn_up.setStyleSheet("QToolButton { border: none; font-size: 14px; }")
                btn_up.clicked.connect(lambda _, r=row_idx: self._on_feedback(r, "up"))
                fb_lay.addWidget(btn_up)
                btn_down = QToolButton()
                btn_down.setText("\U0001f44e")  # 👎
                btn_down.setToolTip("这家不相关")
                btn_down.setFixedSize(28, 24)
                btn_down.setStyleSheet("QToolButton { border: none; font-size: 14px; }")
                btn_down.clicked.connect(lambda _, r=row_idx: self._on_feedback(r, "down"))
                fb_lay.addWidget(btn_down)
                btn_dislike = QToolButton()
                btn_dislike.setText("\U0001f6ab")  # 🚫
                btn_dislike.setToolTip("不感兴趣，以后不再展示")
                btn_dislike.setFixedSize(28, 24)
                btn_dislike.setStyleSheet("QToolButton { border: none; font-size: 14px; }")
                btn_dislike.clicked.connect(lambda _, r=row_idx: self._on_feedback(r, "dislike"))
                fb_lay.addWidget(btn_dislike)
                fb_lay.addStretch()
                self.table.setCellWidget(row_idx, 8, fb_widget)

    # ── 选择/导出 ──

    def _on_select_all(self, state):
        checked = state == Qt.Checked
        for row_idx in range(self.table.rowCount()):
            w = self.table.cellWidget(row_idx, 0)
            if w:
                chk = w.findChild(QCheckBox)
                if chk:
                    chk.setChecked(checked)
        # 全选/反选后主动重算计数，不依赖逐行 stateChanged 转发（批量 setChecked
        # 下信号时序不可靠），保证「已选」文案与表格勾选状态始终一致。
        self._update_selected_count()

    def _update_selected_count(self):
        count = 0
        selected = []
        for row_idx in range(self.table.rowCount()):
            w = self.table.cellWidget(row_idx, 0)
            if w:
                chk = w.findChild(QCheckBox)
                if chk and chk.isChecked():
                    count += 1
                    if row_idx < len(self._results):
                        selected.append(self._results[row_idx])
        # 同时勾到国内外时把拆分口径前置亮出来：与导出范围弹窗、搜索统计
        # 「国内 N · 国外 M」同一套数字，用户点导出前就能预判弹窗选项。
        text = f"已选 {count} 条"
        if _HAS_POOL and selected:
            dom_n = sum(1 for r in selected if record_region(r) == "dom")
            intl_n = len(selected) - dom_n
            if dom_n and intl_n:
                text = f"{text}（国内 {dom_n} · 国外 {intl_n}）"
        self.lbl_selected.setText(text)

    # ── 反馈机制 ──

    def _on_feedback(self, row_idx: int, feedback_type: str):
        """处理用户对某行结果的反馈（👍/👎/🚫）。"""
        if not _HAS_FEEDBACK_STORE or row_idx >= len(self._results):
            return
        rec = self._results[row_idx]
        company = rec.get("company", "")
        query = self._current_query
        # 询问原因（可选）
        reason = ""
        if feedback_type in ("down", "dislike"):
            reason, ok = QInputDialog.getText(
                self, "反馈原因（可选）",
                "告诉我们为什么，帮助改进搜索质量：",
            )
            if not ok:
                reason = ""
        # 记录反馈
        fb_store = get_feedback_store()
        fb_store.record(query, company, feedback_type, reason)
        # 视觉反馈
        type_text = {"up": "\u2705 已标记为相关", "down": "\u274c 已标记为不相关",
                     "dislike": "\U0001f6ab 已标记为不感兴趣"}
        self._show_toast(f"{type_text.get(feedback_type, '已反馈')}：{company[:20]}",
                         'success' if feedback_type == 'up' else 'info')
        # 不感兴趣：立即从结果中移除该行
        if feedback_type == "dislike":
            self.table.removeRow(row_idx)
            if row_idx < len(self._results):
                self._results.pop(row_idx)

    def _on_table_context_menu(self, pos):
        """表格右键菜单：反馈 + 推荐相关公司。"""
        from PyQt5.QtWidgets import QMenu, QAction
        row_idx = self.table.rowAt(pos.y())
        if row_idx < 0 or row_idx >= len(self._results):
            return
        rec = self._results[row_idx]
        company = rec.get("company", "")
        menu = QMenu(self)
        # 反馈子菜单
        fb_menu = menu.addMenu(f"\U0001f4dd 反馈：{company[:20]}")
        act_up = QAction("\U0001f44d 相关/有用", self)
        act_up.triggered.connect(lambda: self._on_feedback(row_idx, "up"))
        fb_menu.addAction(act_up)
        act_down = QAction("\U0001f44e 不相关", self)
        act_down.triggered.connect(lambda: self._on_feedback(row_idx, "down"))
        fb_menu.addAction(act_down)
        act_dislike = QAction("\U0001f6ab 不感兴趣", self)
        act_dislike.triggered.connect(lambda: self._on_feedback(row_idx, "dislike"))
        fb_menu.addAction(act_dislike)
        menu.addSeparator()
        # 推荐子菜单
        rec_menu = menu.addMenu(f"\U0001f50d 推荐相关公司")
        act_similar = QAction("\U0001f3e2 找相似公司", self)
        act_similar.triggered.connect(lambda: self._on_recommend(row_idx, "similar"))
        rec_menu.addAction(act_similar)
        act_chain = QAction("\U0001f517 找供应链上下游", self)
        act_chain.triggered.connect(lambda: self._on_recommend(row_idx, "supply_chain"))
        rec_menu.addAction(act_chain)
        act_regional = QAction("\U0001f4cd 找同地区公司", self)
        act_regional.triggered.connect(lambda: self._on_recommend(row_idx, "regional"))
        rec_menu.addAction(act_regional)
        menu.exec_(self.table.viewport().mapToGlobal(pos))

    def _on_recommend(self, row_idx: int, rec_type: str):
        """推荐一律走后台线程：候选由检索视图召回，主线程只负责发消息。

        旧实现在 UI 线程上对整张总表逐条分词打分，六万条就白屏几秒。
        """
        if row_idx >= len(self._results) or not self._master:
            return
        if rec_type not in ("similar", "supply_chain", "regional"):
            return
        if self._recommend_worker is not None and self._recommend_worker.isRunning():
            self._show_toast("\u26a0 上一次推荐还在进行，请稍候", 'warning')
            return
        rec = self._results[row_idx]
        self.status_lbl.setText("推荐中…")
        self.status_lbl.setStyleSheet("color: #42a5f5; font-weight: bold; padding: 4px 0;")
        self.think_frame.setVisible(True)
        self.think_text.clear()
        self._think_lines = 0
        self.think_title.setText("💭 思考中... (相关推荐)")
        self.think_icon.setText("\U0001f9e9")
        self._append_think(f"🔗 以「{str(rec.get('company', ''))[:20]}」为锚点推荐（"
                           f"{rec_type}），尊重当前地区与筛选条件")
        self._recommend_worker = RecommendWorker(
            rec_type, rec, self._master, mgr=self._pool_mgr,
            regions=self._selected_regions(), filters=self._get_filter_params())
        self._recommend_worker.progress.connect(self._on_think_step)
        self._recommend_worker.finished.connect(self._on_recommend_finished)
        self._recommend_worker.start()

    def _on_recommend_finished(self, success: bool, results: list, message: str):
        """推荐收尾：文案与搜索同一口径（_scope_note），空结果/冲突都如实说。"""
        self.status_lbl.setStyleSheet("")
        self.status_lbl.setText(message)
        self._append_think(("✅ " if success and results else "\u26a0 ") + message)
        if success and results:
            self._results = results
            self._all_results = list(results)
            self._fill_table(results)
            self.btn_export.setEnabled(True)
            self._show_toast(f"\u2705 {message}", 'success')
        else:
            self._show_toast(f"\u26a0 {message}", 'warning')

    def _on_row_selected(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            self.reason_frame.setVisible(False)
            return
        row_idx = rows[0].row()
        if row_idx >= len(self._results):
            return
        rec = self._results[row_idx]
        reason = rec.get("_ai_reason", "")
        relationship = rec.get("_ai_relationship", "")
        detail = rec.get("_score_detail", {})

        parts = []
        if relationship:
            parts.append(f"关系: {relationship}")
        if reason:
            parts.append(f"理由: {reason}")
        if detail:
            parts.append(
                f"评分明细: 公司名={detail.get('name', 0):.0%} "
                f"主营={detail.get('product', 0):.0%} "
                f"联系方式={detail.get('contact', 0):.0%} "
                f"新鲜度={detail.get('fresh', 0):.0%}"
            )
        if parts:
            self.reason_lbl.setText("  |  ".join(parts))
            self.reason_frame.setVisible(True)
        else:
            self.reason_frame.setVisible(False)

    @staticmethod
    def _split_by_region(records):
        """按属地把记录拆成 (国内列表, 国外列表)。

        复用 data_pool.record_region（含 _region_key 就地缓存与存量多信号推导），
        与搜索过滤、结果统计「国内 N · 国外 M」口径完全一致。
        依赖缺失（打包态导入失败）时返回 (None, None) 表示「无法判定」。
        """
        if not _HAS_POOL:
            return None, None
        dom = [r for r in records if record_region(r) == "dom"]
        intl = [r for r in records if record_region(r) == "intl"]
        return dom, intl

    def _ask_export_scope(self, dom_n, intl_n):
        """勾选结果同时含国内外时询问导出范围，返回 "all"/"dom"/"intl"/None(取消)。

        按钮布局对齐桌面软件惯例：默认按钮=导出全部（回车即确认），
        「仅国内/仅国外」带实时条数让用户所见即所得，「取消」随时退出。
        """
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("选择导出范围")
        box.setText(
            f"已勾选 {dom_n + intl_n} 条记录，其中国内 {dom_n} 条、国外 {intl_n} 条。\n"
            "请选择本次导出的范围："
        )
        btn_all = box.addButton("导出全部", QMessageBox.AcceptRole)
        btn_dom = box.addButton(f"仅国内（{dom_n} 条）", QMessageBox.ActionRole)
        btn_intl = box.addButton(f"仅国外（{intl_n} 条）", QMessageBox.ActionRole)
        box.addButton("取消", QMessageBox.RejectRole)
        # 显式指定默认按钮：不指定的话 QMessageBox 纯 addButton 布局下 defaultButton()
        # 为空，回车/Esc 没有确定落点（实机探针实测）。指定后：回车=导出全部，
        # Esc=取消（RejectRole 自动接手），与主流桌面软件一致。
        box.setDefaultButton(btn_all)
        box.exec_()
        clicked = box.clickedButton()
        if clicked is btn_all:
            return "all"
        if clicked is btn_dom:
            return "dom"
        if clicked is btn_intl:
            return "intl"
        return None

    def _on_export(self):
        # 收集选中的行
        selected = []
        for row_idx in range(self.table.rowCount()):
            w = self.table.cellWidget(row_idx, 0)
            if w:
                chk = w.findChild(QCheckBox)
                if chk and chk.isChecked() and row_idx < len(self._results):
                    selected.append(self._results[row_idx])

        if not selected:
            QMessageBox.information(self, "未选择", "请先勾选要导出的记录")
            return

        # 导出范围：勾选结果同时含国内外 → 弹窗让用户选全部/仅国内/仅国外；
        # 只含一类 → 不打扰，直接按该类范围进入保存位置选择。
        dom_list, intl_list = self._split_by_region(selected)
        if dom_list is None:  # 属地判定依赖缺失，不阻断导出，按全部处理
            scope, selected = "all", selected
        elif dom_list and intl_list:
            scope = self._ask_export_scope(len(dom_list), len(intl_list))
            if scope is None:
                return  # 用户取消，不做任何导出
            if scope == "dom":
                selected = dom_list
            elif scope == "intl":
                selected = intl_list
        elif dom_list:
            scope = "dom"
        else:
            scope = "intl"
        scope_tag = {"dom": "国内", "intl": "国外"}.get(scope, "全部")

        # 选择保存路径（默认文件名带范围，导出后凭文件名即可区分数据范围）
        default_name = (f"数据池搜索导出_{scope_tag}_"
                        f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx")
        path, _ = QFileDialog.getSaveFileName(
            self, f"导出 Excel（{scope_tag} {len(selected)} 条）", default_name,
            "Excel 文件 (*.xlsx)"
        )
        if not path:
            return

        try:
            self._export_to_excel(selected, path, scope_tag)
            QMessageBox.information(
                self, "导出成功",
                f"已导出{scope_tag} {len(selected)} 条记录到:\n{path}"
            )
            # 审计日志：导出成功（scope 落盘，便于回溯导出口径）
            if _HAS_POOL:
                try:
                    get_audit_logger().log("export", "local_user", {
                        "record_count": len(selected),
                        "filepath": os.path.basename(path),
                        "scope": scope,
                    })
                except Exception:
                    pass
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    @staticmethod
    def _export_to_excel(records: list, filepath: str, scope_label: str = ""):
        """导出为 12 列标准 schema（11 个必选字段 + 数据来源，上传/导出统一口径）。

        scope_label（全部/国内/国外）只写进工作表名与文档属性，**不动列结构**，
        保证客户库模板列映射不变；打开文件就能看到本次导出口径。
        """
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill

        HEADER = EXPORT_HEADER  # 11 个标准化字段

        wb = Workbook()
        ws = wb.active
        ws.title = f"搜索结果_{scope_label}" if scope_label else "搜索结果"
        if scope_label:
            wb.properties.title = f"数据池搜索导出（{scope_label}）"
            wb.properties.subject = f"scope={scope_label}; count={len(records)}"

        # 表头
        header_font = Font(bold=True, size=10)
        header_fill = PatternFill(start_color="E8EAF6", end_color="E8EAF6", fill_type="solid")
        for col_idx, h in enumerate(HEADER, 1):
            cell = ws.cell(row=1, column=col_idx, value=h)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")

        # 数据行：标准 schema 逐列填充（顺序与 EXPORT_HEADER 严格对齐）
        def _is_mobile(num: str) -> bool:
            """国内手机号判定：剥掉非数字后取末 11 位匹配 1[3-9] 开头
            （兼容 +86/086 国家码前缀与号码内连字符）。"""
            digits = re.sub(r"\D", "", num or "")
            return len(digits) >= 11 and re.match(r"^1[3-9]\d{9}$", digits[-11:]) is not None

        for row_idx, rec in enumerate(records, 2):
            contacts = rec.get("contacts", [])
            contact = contacts[0] if contacts else {}

            # 电话分类：手机号归「电话」，其余归「固话」
            # （landlines 单独存放的固话也并入，dict.fromkeys 保序去重）
            all_nums = list(dict.fromkeys(
                list(rec.get("mobiles", [])) + list(rec.get("landlines", []))
                + list(rec.get("phones", []))
            ))
            phones_col = [p for p in all_nums if _is_mobile(p)]
            landline_col = [p for p in all_nums if p not in phones_col]

            ws.cell(row=row_idx, column=1, value=contact.get("name", ""))
            ws.cell(row=row_idx, column=2, value=contact.get("title", ""))
            ws.cell(row=row_idx, column=3, value=rec.get("company", ""))
            ws.cell(row=row_idx, column=4, value=",".join(phones_col[:3]))
            ws.cell(row=row_idx, column=5, value=",".join(rec.get("emails", [])[:3]))
            ws.cell(row=row_idx, column=6, value=",".join(landline_col[:3]))
            ws.cell(row=row_idx, column=7, value=rec.get("website", ""))
            ws.cell(row=row_idx, column=8, value=rec.get("products", ""))
            ws.cell(row=row_idx, column=9, value=rec.get("industry", ""))
            ws.cell(row=row_idx, column=10, value=rec.get("address", ""))
            ws.cell(row=row_idx, column=11, value=rec.get("country", ""))
            ws.cell(row=row_idx, column=12, value=rec.get("source", ""))

        # 列宽（与 EXPORT_HEADER 12 列对应）
        widths = [12, 12, 25, 20, 25, 18, 20, 25, 12, 30, 10, 20]
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w

        # 原子写盘
        tmp = filepath + ".tmp"
        wb.save(tmp)
        os.replace(tmp, filepath)

    # ── 关闭收尾：后台线程还在跑时先断开信号再等它结束 ──
    # 对话框销毁而 QThread 仍在运行 = 解释端直接崩（与 ImportPreviewDialog
    # 同一纪律）。加载/搜索/推荐三个线程一起收，缺一个都可能在关闭时闪退。

    def _shutdown_workers(self):
        for worker in (self._load_worker, self._search_worker, self._recommend_worker):
            if worker is None or not worker.isRunning():
                continue
            try:
                worker.finished.disconnect(self)
            except TypeError:
                pass
            worker.wait(3000)

    def reject(self):
        self._shutdown_workers()
        super().reject()

    def closeEvent(self, event):
        self._shutdown_workers()
        event.accept()


# ─────────────────────────────────────────────────────────────────────────────
#  导入映射预览对话框（列 → 字段，可确认修正后再入库）
# ─────────────────────────────────────────────────────────────────────────────

class PreviewWorker(QThread):
    """后台逐个解析多个 Excel 的列映射预览（多文件×大表不卡 UI）。"""
    finished = pyqtSignal(list, str)  # (file_previews, error)

    def __init__(self, file_pairs):
        super().__init__()
        self.file_pairs = list(file_pairs)  # [(路径, 展示名), ...]

    def run(self):
        # 单个文件失败只记账（error 字段），绝不拖垮整批解析
        results = []
        for path, display in self.file_pairs:
            try:
                sheets = DataPoolManager.preview_excel_mapping(path)
                results.append({"path": path, "display": display,
                                "sheets": sheets, "error": ""})
            except Exception as e:
                results.append({"path": path, "display": display,
                                "sheets": [], "error": str(e)})
        self.finished.emit(results, "")


class ImportPreviewDialog(QDialog):
    """批量导入映射预览：左侧文件清单（勾掉即跳过），右侧当前文件的列映射。

    大厂 ETL（Informatica / Power Query / DataWorks）批量导入的 schema
    mapping 确认思想：多来源先给一张清单，逐来源确认列映射——机器
    打分给初始值、人抽查纠错，把「错列入库后才发现」提前到入库之前。
    公司名为必选字段——它是数据池去重合并的依据（company_norm 分组），
    没有公司名的记录无法归组。
    """

    # 下拉选项：与 customer_library_cleaner.EXTERNAL_FIELDS 同口径
    NO_FIELD = "（不导入）"
    FIELD_OPTIONS = [NO_FIELD, "公司名", "联系人", "岗位", "电话", "邮箱",
                     "微信号", "官网", "主营", "行业", "国家", "地址",
                     "状态", "数据来源", "备注"]
    REQUIRED_FIELD = "公司名"
    # 映射字段 → 11 项标准字段的展示名（缺失时给用户的提示）
    _FIELD_TO_STANDARD = {
        "联系人": "客户姓名", "岗位": "职位", "公司名": "公司名",
        "电话": "电话/固话", "邮箱": "邮箱", "官网": "官网",
        "主营": "主营", "行业": "行业", "地址": "公司地址", "国家": "国家",
    }

    def __init__(self, file_pairs, parent=None):
        super().__init__(parent)
        self._file_pairs = list(file_pairs)  # [(路径, 展示名), ...]
        self._files: list = []               # PreviewWorker 的逐文件结果
        self._combos = {}                    # {(文件路径, 表名, 列下标): QComboBox}
        self._snapshots = {}                 # {(文件路径, 表名): {列下标: 字段 or ""}}
        self._current_fpath: Optional[str] = None  # 正在右侧展示的文件
        self._picked_skips: set = set()      # 用户取消勾选的文件路径
        self._override = None                # 确认后的 mapping_override
        self._recommender = MappingRecommender() if _HAS_POOL else None  # 智能推荐
        self.setWindowTitle("导入数据池 · 批量列映射预览")
        self.setMinimumSize(880, 560)
        self.resize(1040, 680)
        self._worker = None
        self._init_ui()
        self._start_parse()

    # ── UI ──

    def _init_ui(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        tip = QLabel(
            "左边是文件清单：点一个文件，右边看它的列映射；不想导的文件把前面的勾去掉。\n"
            "每列已按「表头 + 内容」自动识别含义，下拉可修正。\n"
            "「公司名」为必选字段（数据池按它去重合并）；其余选填，"
            "映射越全，同事搜索到的信息越完整。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color: #555; font-size: 12px;")
        lay.addWidget(tip)

        splitter = QSplitter(Qt.Horizontal)
        self.file_list = QListWidget()
        self.file_list.setMinimumWidth(250)
        self.file_list.itemChanged.connect(self._on_item_changed)
        self.file_list.currentRowChanged.connect(self._on_file_selected)
        splitter.addWidget(self.file_list)

        self.tabs = QTabWidget()
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([280, 760])
        lay.addWidget(splitter, 1)

        self.lbl_state = QLabel("正在解析文件…")
        self.lbl_state.setStyleSheet("color: #666; font-size: 11px;")
        lay.addWidget(self.lbl_state)

        self.warn_label = QLabel("")
        self.warn_label.setWordWrap(True)
        self.warn_label.setStyleSheet(
            "color: #8a6100; background: #fff7e0; border: 1px solid #e8d48b;"
            "border-radius: 4px; padding: 6px; font-size: 11px;")
        self.warn_label.setVisible(False)
        lay.addWidget(self.warn_label)

        btns = QHBoxLayout()
        # 下载模板按钮（左侧）
        self.btn_template = QPushButton("📥 下载导入模板")
        self.btn_template.setMinimumHeight(32)
        self.btn_template.setToolTip("下载标准 Excel 模板，按模板准备历史数据可自动识别 100% 列映射")
        self.btn_template.clicked.connect(self._on_download_template)
        btns.addWidget(self.btn_template)
        btns.addStretch(1)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setMinimumHeight(32)
        self.btn_cancel.clicked.connect(self.reject)
        btns.addWidget(self.btn_cancel)
        self.btn_ok = QPushButton("确认导入")
        self.btn_ok.setMinimumHeight(32)
        self.btn_ok.setMinimumWidth(110)
        self.btn_ok.setEnabled(False)
        self.btn_ok.clicked.connect(self._on_confirm)
        btns.addWidget(self.btn_ok)
        lay.addLayout(btns)

    def _on_download_template(self):
        """下载标准导入模板（11 列 + 示例数据），用户按模板准备历史数据可自动识别 100% 列映射。"""
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        
        # 选择保存路径
        default_name = f"数据池导入模板_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        path, _ = QFileDialog.getSaveFileName(
            self, "保存导入模板", default_name,
            "Excel 文件 (*.xlsx)"
        )
        if not path:
            return
        
        try:
            wb = Workbook()
            ws = wb.active
            ws.title = "历史数据模板"
            
            # 11 列标准表头（与 EXPORT_HEADER 对齐）
            headers = ["公司名", "联系人", "岗位", "电话", "邮箱", "微信号",
                      "官网", "主营", "行业", "国家", "地址"]
            
            # 表头样式
            header_font = Font(bold=True, size=11, color="FFFFFF")
            header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
            header_alignment = Alignment(horizontal="center", vertical="center")
            
            # 写表头
            for col_idx, header in enumerate(headers, 1):
                cell = ws.cell(row=1, column=col_idx, value=header)
                cell.font = header_font
                cell.fill = header_fill
                cell.alignment = header_alignment
            
            # 示例数据（2 行，让用户直观理解每列应该填什么）
            examples = [
                ["常州天合光能有限公司", "张经理", "销售总监",
                 "13800138000", "sales@trina.com", "",
                 "www.trinasolar.com", "光伏组件、储能系统", "制造业",
                 "中国", "江苏省常州市天宁区"],
                ["ESTEK SOLAR LLC", "John Smith", "CEO",
                 "+1-555-1234567", "john@estek.com", "",
                 "www.estek.com", "Solar panel, inverter", "批发业",
                 "美国", "Texas, USA"],
            ]
            
            # 写示例数据
            for row_idx, row_data in enumerate(examples, 2):
                for col_idx, value in enumerate(row_data, 1):
                    ws.cell(row=row_idx, column=col_idx, value=value)
            
            # 列宽自适应
            col_widths = [30, 12, 12, 20, 25, 15, 25, 25, 15, 10, 30]
            for i, width in enumerate(col_widths, 1):
                ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = width
            
            # 冻结首行
            ws.freeze_panes = "A2"
            
            # 保存
            wb.save(path)
            
            QMessageBox.information(
                self, "下载成功",
                f"导入模板已保存到:\n{path}\n\n"
                f"模板包含 {len(headers)} 列标准字段 + 2 行示例数据。\n"
                f"请按模板格式准备历史数据，导入时列映射可自动识别 100%。"
            )
        except Exception as e:
            QMessageBox.critical(
                self, "下载失败",
                f"生成模板时出错：\n{e}"
            )

    # ── 解析 ──

    def _start_parse(self):
        self._worker = PreviewWorker(self._file_pairs)
        self._worker.finished.connect(self._on_parsed)
        self._worker.start()

    def _on_parsed(self, files: list, err: str):
        self._worker = None
        if err:
            self.lbl_state.setText(f"❌ 解析失败: {err}")
            self.lbl_state.setStyleSheet("color: #c62828; font-size: 11px;")
            return
        self._files = files
        if not files:
            self.lbl_state.setText("❌ 没有读到任何文件")
            self.lbl_state.setStyleSheet("color: #c62828; font-size: 11px;")
            return
        self.file_list.blockSignals(True)
        for f in files:
            if f["error"]:
                text = f"❌ {f['display']}（读不出）"
            elif not f["sheets"]:
                text = f"⚠ {f['display']}（没读到数据）"
            else:
                n = sum(p["total_rows"] for p in f["sheets"])
                text = f"{f['display']}（{len(f['sheets'])} 表 {n} 行）"
            item = QListWidgetItem(text)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            item.setToolTip(f["path"])
            self.file_list.addItem(item)
        self.file_list.blockSignals(False)
        self.file_list.setCurrentRow(0)  # 触发右侧展示第一个文件
        self.btn_ok.setEnabled(True)
        self._update_summary()
        self._refresh_warning()

    # ── 文件清单交互 ──

    def _snapshot_current(self):
        """切换文件前把当前文件的列映射选择收进快照。

        tabs.clear() 会销毁下拉控件：不先快照，用户对上一个文件的
        修改会丢失；_combos 里残留的引用也会指向已销毁的控件，
        之后再遍历映射直接抛 RuntimeError。所以切走前先把选择存进
        _snapshots，并清掉即将失效的下拉引用。
        """
        for key, combo in list(self._combos.items()):
            fpath, sheet, col_idx = key
            if fpath != self._current_fpath:
                continue
            field = combo.currentText()
            self._snapshots.setdefault((fpath, sheet), {})[col_idx] = (
                "" if field == self.NO_FIELD else field)
            del self._combos[key]

    def _on_file_selected(self, row: int):
        """懒加载：点哪个文件才构建哪个文件的映射表格。"""
        if not (0 <= row < len(self._files)):
            return
        f = self._files[row]
        self._snapshot_current()
        self.tabs.clear()
        self._current_fpath = f["path"]
        if f["error"]:
            lbl = QLabel(f"这个文件读不出来：\n{f['error']}\n\n"
                         "不影响其他文件，继续导入时它会自动跳过。")
            lbl.setWordWrap(True)
            lbl.setStyleSheet("color: #c62828; padding: 14px; font-size: 12px;")
            self.tabs.addTab(lbl, "读不出")
            return
        if not f["sheets"]:
            lbl = QLabel("这个文件里没有读到数据行，导入时会自动跳过。")
            lbl.setWordWrap(True)
            lbl.setStyleSheet("color: #8a6100; padding: 14px; font-size: 12px;")
            self.tabs.addTab(lbl, "没数据")
            return
        for pv in f["sheets"]:
            self._build_sheet_tab(f["path"], pv)

    def _on_item_changed(self, item):
        """勾选/取消勾选文件 → 更新跳过集合与汇总。"""
        idx = self.file_list.row(item)
        if not (0 <= idx < len(self._files)):
            return
        path = self._files[idx]["path"]
        if item.checkState() == Qt.Checked:
            self._picked_skips.discard(path)
        else:
            self._picked_skips.add(path)
        self._update_summary()
        self._refresh_warning()

    def _update_summary(self):
        picked = [f for f in self._files
                  if f["path"] not in self._picked_skips
                  and not f["error"] and f["sheets"]]
        rows = sum(p["total_rows"] for f in picked for p in f["sheets"])
        bad = sum(1 for f in self._files if f["error"] or not f["sheets"])
        off = len(self._files) - len(picked) - bad
        parts = [f"✓ 将导入 {len(picked)} 个文件 / {rows} 行数据"]
        if off > 0:
            parts.append(f"{off} 个文件已取消勾选")
        if bad > 0:
            parts.append(f"{bad} 个读不出或没数据（自动排除）")
        self.lbl_state.setText("；".join(parts))

    def _build_sheet_tab(self, fpath: str, pv: dict):
        sheet = pv["sheet"]
        table = QTableWidget(len(pv["headers"]), 3)
        table.setHorizontalHeaderLabels(["源列名", "识别为", "样例值"])
        table.horizontalHeader().setStretchLastSection(True)
        table.verticalHeader().setVisible(False)
        table.setColumnWidth(0, 210)
        table.setColumnWidth(1, 150)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        for col_idx, header in enumerate(pv["headers"]):
            # 源列名
            name = header or f"（第 {col_idx + 1} 列 · 无表头）"
            item = QTableWidgetItem(name)
            table.setItem(col_idx, 0, item)
            # 识别字段下拉（可修正）；初值优先级：快照 > 智能推荐 > 自动识别
            combo = QComboBox()
            combo.addItems(self.FIELD_OPTIONS)
            if (fpath, sheet) in self._snapshots and col_idx in self._snapshots[(fpath, sheet)]:
                field = self._snapshots[(fpath, sheet)][col_idx]
            elif self._recommender:
                rec = self._recommender.recommend(pv["headers"])
                field = rec.get(col_idx, pv["mapping"].get(col_idx, ""))
            else:
                field = pv["mapping"].get(col_idx, "")
            combo.setCurrentIndex(
                self.FIELD_OPTIONS.index(field) if field in self.FIELD_OPTIONS else 0)
            combo.currentTextChanged.connect(
                lambda _t: self._refresh_warning())
            table.setCellWidget(col_idx, 1, combo)
            self._combos[(fpath, sheet, col_idx)] = combo
            # 样例值
            samples = pv["samples"].get(col_idx, [])
            sample_text = " ｜ ".join(samples) if samples else "（空列）"
            item2 = QTableWidgetItem(sample_text)
            item2.setToolTip(sample_text)
            table.setItem(col_idx, 2, item2)
        self.tabs.addTab(table, sheet)

    # ── 校验与确认 ──

    def _current_mapping(self, fpath: str, pv: dict) -> dict:
        """当前该文件该 sheet 的列映射（含未改动列的自动识别初值）。

        三级回退：右侧正在展示的下拉控件 → 切换前存下的快照 →
        自动识别初值。未点开过的文件落回初值——批量导入的心智
        是「机器识别 + 抽查纠错」，绝大多数文件不需要逐个点开。
        """
        result = {}
        snap = self._snapshots.get((fpath, pv["sheet"]), {})
        for col_idx in range(len(pv["headers"])):
            combo = self._combos.get((fpath, pv["sheet"], col_idx))
            if combo is not None:
                field = combo.currentText()
                result[col_idx] = "" if field == self.NO_FIELD else field
            elif col_idx in snap:
                result[col_idx] = snap[col_idx]
            else:
                result[col_idx] = pv["mapping"].get(col_idx, "")
        return result

    def _refresh_warning(self):
        """必选缺失（红色阻断确认）与 11 项缺失提示（黄色，非阻断）。"""
        problems = []
        for f in self._files:
            if f["path"] in self._picked_skips or f["error"] or not f["sheets"]:
                continue
            for pv in f["sheets"]:
                fields = set(v for v in
                             self._current_mapping(f["path"], pv).values() if v)
                if not fields:
                    continue  # 整表不导入，不打扰
                tag = f"「{f['display']} · {pv['sheet']}」"
                if self.REQUIRED_FIELD not in fields:
                    problems.append(
                        f"{tag}没有认出「公司名」列，导入前必须指定")
                    continue
                missing = [self._FIELD_TO_STANDARD[x]
                           for x in self._FIELD_TO_STANDARD if x not in fields]
                if missing:
                    problems.append(
                        f"{tag}未映射: {'、'.join(missing)}（选填，不影响导入）")
        if problems:
            # 文件多时只展示前几条，避免黄条滚出屏幕
            self.warn_label.setText(
                "⚠ " + "；".join(problems[:8])
                + (f"……等共 {len(problems)} 条" if len(problems) > 8 else ""))
            self.warn_label.setVisible(True)
        else:
            self.warn_label.setVisible(False)

    def _on_confirm(self):
        # 必选校验：每个要导入的文件至少一列映射为公司名
        bad = []
        for f in self._files:
            if f["path"] in self._picked_skips or f["error"] or not f["sheets"]:
                continue
            ok = any(
                self.REQUIRED_FIELD in set(
                    self._current_mapping(f["path"], pv).values())
                for pv in f["sheets"])
            if not ok:
                bad.append(f["display"])
        if bad:
            QMessageBox.warning(
                self, "缺少公司名列",
                "「公司名」是数据池去重合并的依据，下面这些文件还没指定公司名列：\n"
                + "\n".join(f"· {s}" for s in bad[:10])
                + (f"\n……等共 {len(bad)} 个" if len(bad) > 10 else "")
                + "\n\n请点选文件，在右侧把公司名那一列的下拉改成「公司名」。\n"
                "如果这个文件本来就不想导，把左边清单里它前面的勾去掉就行。")
            return
        override = {}
        for f in self._files:
            if f["path"] in self._picked_skips or f["error"] or not f["sheets"]:
                continue
            sheet_map = {}
            for pv in f["sheets"]:
                m = self._current_mapping(f["path"], pv)
                if any(m.values()):
                    sheet_map[pv["sheet"]] = {int(k): v for k, v in m.items()}
            if sheet_map:
                override[f["path"]] = sheet_map
        self._override = override or None
        # 更新历史映射（智能推荐学习）
        if self._recommender:
            for f in self._files:
                if f["path"] in self._picked_skips or f["error"] or not f["sheets"]:
                    continue
                for pv in f["sheets"]:
                    m = self._current_mapping(f["path"], pv)
                    # 过滤掉空字段
                    valid_mapping = {int(k): v for k, v in m.items() if v}
                    if valid_mapping:
                        self._recommender.update(pv["headers"], valid_mapping)
        self.accept()

    def get_mapping_override(self):
        """确认后的 mapping_override：{文件路径: {表名: {列下标: 字段 or ""}}}。"""
        return self._override

    def get_skipped_files(self):
        """不参与导入的文件路径集合（勾掉的 + 读不出的 + 没数据的）。"""
        skipped = set(self._picked_skips)
        for f in self._files:
            if f["error"] or not f["sheets"]:
                skipped.add(f["path"])
        return skipped

    # ── 关闭收尾：解析线程还在跑时先断开信号再等它结束，
    # 避免 Python 端销毁 dialog 时 QThread 仍在运行导致崩溃 ──

    def _shutdown_worker(self):
        w = self._worker
        if w is not None and w.isRunning():
            try:
                w.finished.disconnect(self)
            except TypeError:
                pass
            w.wait(3000)  # 本地文件解析，正常秒级完成

    def reject(self):
        self._shutdown_worker()
        super().reject()

    def closeEvent(self, event):
        self._shutdown_worker()
        event.accept()


# ─────────────────────────────────────────────────────────────────────────────
#  本地表格全盘扫描（发现 → 指纹台账去重 → 勾选 → 交回既有导入链路）
# ─────────────────────────────────────────────────────────────────────────────

class ScanWorker(QThread):
    """后台跑 local_scan.scan_all（发现层剪枝 + 内容指纹 + 轻量采样识别）。

    取消语义：``cancel()`` 只置标志，由 scan_all 在文件边界上自查——绝不在
    openpyxl 读表的中途硬切，那种做法会留下半开的 workbook 和泄漏的文件句柄。
    取消后**已经扫到的部分照常返回**，用户看到的是「扫到哪儿算哪儿」，
    而不是按下取消就白等两分钟。
    """
    progress = pyqtSignal(str, int, int)     # 文案, 已完成, 总数
    finished = pyqtSignal(bool, list, dict, str)

    def __init__(self, roots=None, parent=None):
        super().__init__(parent)
        self.roots = roots
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            from local_scan import Ledger, PoolIndex, scan_all
        except Exception as e:
            self.finished.emit(False, [], {}, f"扫描模块加载失败: {e}")
            return
        try:
            ledger = Ledger().load()
            pool_index = None
            pool_note = ""
            # 池内比对的唯一数据源是本地物化总表。这里必须把「有没有缓存」判清楚：
            # get_merged_master 在无缓存时会走 _full_rebuild（全量下载 OSS，几分钟），
            # 那样一来「点扫描」的第一件事就是长时间卡住且毫无反馈——把已经实测过
            # 的「有缓存秒开」不变量用错了地方。没缓存就不比对，照常勾选、照常上传：
            # 宁可多传一份重复内容（读取端按公司名归并，幂等），
            # 也绝不因为索引缺失而漏掉任何一个文件。
            cached = os.path.join(DataPoolManager._default_cache_dir(),
                                  "master.json")
            if os.path.exists(cached):
                self.progress.emit("正在准备数据池比对…", 0, 0)
                try:
                    pool_index = PoolIndex()
                except Exception as e:
                    pool_index = None
                    pool_note = f"本次不做池内比对：{str(e)[:60]}"
            else:
                pool_note = "本地总表缓存尚未建立，本次不做池内比对"
            items, stats = scan_all(
                roots=self.roots, ledger=ledger, pool_index=pool_index,
                should_stop=lambda: self._cancel,
                on_progress=lambda msg, done, total: self.progress.emit(
                    msg, done, total))
            if pool_note:
                stats["pool_note"] = pool_note
            note = "已取消，下面是取消前已经扫到的部分" if self._cancel else ""
            self.finished.emit(True, items, stats, note)
        except Exception as e:
            self.finished.emit(False, [], {}, f"扫描失败: {e}")


class LocalScanDialog(QDialog):
    """「扫描本地表格」结果清单：逐文件状态与理由全部可见，勾选由用户拍板。

    为什么不是「一键全自动上传」：工具能证明的是「这个文件读得出企业数据、
    这份内容本机台账里记着已经传过」，不能证明的是「这批数据是你现在想要的」。
    大厂批量导入（DataWorks / Airflow 的 backfill、Google Cloud Transfer）
    一律是 **dry-run 出清单 → 人工确认 → 执行**，本对话框就是这个 dry-run。

    三重去重在这里各管一段（详见 local_scan 模块头注释）：
      · 内容指纹：改名/挪目录/另存副本都不会被当成新数据重传
      · 同族快照：同一系列的时间戳版本只默认勾最新的那一份
      · 池内比对：只作提示文字，绝不自动取消勾选（漏传是丢数据，多传不丢）
    """

    # 状态 → (中文名, 颜色, 默认是否参与排序权重)
    _STATUS_LABELS = {
        "new": ("待入库", "#2e7d32", 0),
        "unconfirmed": ("待复核", "#ef6c00", 1),
        "family_sibling": ("同系列旧版", "#1565c0", 2),
        "dup_content": ("同内容副本", "#7b1fa2", 3),
        "ingested": ("已传过", "#546e7a", 4),
        "invalid": ("不符合入库条件", "#c62828", 5),
        "unreadable": ("读不了", "#6d4c41", 6),
    }

    def __init__(self, parent=None, roots=None):
        super().__init__(parent)
        self.setWindowTitle("扫描本地表格入数据池")
        self.setMinimumSize(980, 620)
        self.resize(1120, 700)
        self._items: List[dict] = []
        self._stats: dict = {}
        self._roots = list(roots) if roots else None
        self._worker: Optional[ScanWorker] = None
        self.selected_items: List[dict] = []
        self._notes: List[tuple] = []    # 压缩包没取全的原始记账（明细窗口读它）
        self._init_ui()
        self._start_scan()

    # ── UI ──

    def _init_ui(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.setContentsMargins(16, 14, 16, 14)

        head = QLabel("🔎 全盘扫描本机表格")
        f = head.font()
        f.setPointSize(13)
        f.setBold(True)
        head.setFont(f)
        lay.addWidget(head)

        self.roots_lbl = QLabel("")
        self.roots_lbl.setWordWrap(True)
        self.roots_lbl.setStyleSheet("color: #555;")
        lay.addWidget(self.roots_lbl)

        self.summary_lbl = QLabel("正在准备扫描…")
        self.summary_lbl.setWordWrap(True)
        self.summary_lbl.setStyleSheet("font-weight: bold;")
        lay.addWidget(self.summary_lbl)

        cols = ["选", "文件", "所在目录", "大小", "预估行", "状态", "为什么是这个结论"]
        self.table = QTableWidget(0, len(cols))
        self.table.setHorizontalHeaderLabels(cols)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.Interactive)
        header.setSectionResizeMode(6, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 40)
        self.table.setColumnWidth(1, 300)
        self.table.setColumnWidth(2, 220)
        self.table.setColumnWidth(3, 70)
        self.table.setColumnWidth(4, 70)
        self.table.setColumnWidth(5, 120)
        lay.addWidget(self.table, 1)

        # 压缩包没有全部取出的情况必须逐条说清楚（哪些包、为什么）。汇总行里那句
        # 「详见清单下方」是一笔账，这里就是兑现它的地方：只报个数不报原因，等于
        # 告诉用户「有东西没拿到」却不告诉他去哪儿补。
        # 同一句话在 15 个包上重复（本机实测）会把「加密取不出」「路径不安全」挤到
        # 屏幕外，所以正文按原因合并；合并只发生在展示层，「查看明细」仍是全量逐条
        # ——悬停提示既不能滚动也不能复制，把唯一的完整明细托付给它是不合格的。
        self.notes_lbl = QLabel("")
        self.notes_lbl.setWordWrap(True)
        self.notes_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.notes_lbl.setStyleSheet(
            "color: #8a6100; background: #fdf6e3; border-radius: 4px; padding: 8px;")
        self.notes_lbl.setVisible(False)
        self.notes_detail_btn = QPushButton("查看明细")
        # 按钮文字后面会带上条数（「查看明细（16 条）」），固定宽度会把数字截掉
        self.notes_detail_btn.setMinimumWidth(96)
        self.notes_detail_btn.setVisible(False)
        self.notes_detail_btn.clicked.connect(self._show_notes_detail)
        notes_row = QHBoxLayout()
        notes_row.setSpacing(6)
        notes_row.addWidget(self.notes_lbl, 1)
        notes_row.addWidget(self.notes_detail_btn, 0, Qt.AlignTop)
        lay.addLayout(notes_row)

        self.detail_lbl = QLabel("选中一行看这个文件的详细判断依据。")
        self.detail_lbl.setWordWrap(True)
        self.detail_lbl.setStyleSheet(
            "color: #333; background: #f6f7f9; border-radius: 4px; padding: 8px;")
        self.detail_lbl.setMinimumHeight(56)
        lay.addWidget(self.detail_lbl)

        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.bar.setTextVisible(False)
        self.bar.setMaximumHeight(10)
        lay.addWidget(self.bar)

        self.stage_lbl = QLabel("")
        self.stage_lbl.setStyleSheet("color: #888; font-size: 11px;")
        self.stage_lbl.setWordWrap(True)
        lay.addWidget(self.stage_lbl)

        row = QHBoxLayout()
        self.btn_rescan = QPushButton("重新扫描")
        self.btn_rescan.setMinimumHeight(34)
        self.btn_rescan.clicked.connect(self._start_scan)
        self.btn_cancel_scan = QPushButton("取消扫描")
        self.btn_cancel_scan.setMinimumHeight(34)
        self.btn_cancel_scan.clicked.connect(self._on_cancel_scan)
        self.btn_all_new = QPushButton("只选「待入库」")
        self.btn_all_new.setMinimumHeight(34)
        self.btn_all_new.setToolTip(
            "只勾上「待入库 / 待复核」这些**该传**的行，其余（已传过、重复副本、"
            "同系列旧版、不合格、读不了）一律取消勾选。")
        self.btn_all_new.clicked.connect(self._on_only_new)
        self.btn_check_all = QPushButton("全选（含重复/旧版）")
        self.btn_check_all.setMinimumHeight(34)
        self.btn_check_all.setToolTip(
            "连「同内容副本 / 同系列旧版 / 已传过 / 不合格」一起勾上。\n"
            "点下去会先把会多传多少行说清楚再问一次——去重是扫描层算出来的，"
            "绕过它必须是看得见决定。")
        self.btn_check_all.clicked.connect(self._on_select_all)
        self.btn_invert = QPushButton("反选")
        self.btn_invert.setMinimumHeight(34)
        self.btn_invert.setToolTip(
            "在「待入库 / 待复核」这些行里翻转勾选；重复副本、同系列旧版、"
            "不合格、读不了的行一律留在不勾（要传它们请逐行勾）。")
        self.btn_invert.clicked.connect(self._invert_checks)
        self.btn_tomb = QPushButton("剔除已入库的错误数据…")
        self.btn_tomb.setMinimumHeight(34)
        self.btn_tomb.setToolTip(
            "数据池是只追加的原始层，传错了不能直接删文件（会破坏审计与并发安全）。\n"
            "这里给指定企业打「剔除标记」，所有人在读取时都会自动跳过它。")
        self.btn_tomb.clicked.connect(self._on_open_tombstone)
        self.btn_upload = QPushButton("上传勾选项")
        self.btn_upload.setMinimumHeight(36)
        self.btn_upload.setMinimumWidth(140)
        self.btn_upload.setObjectName("primary")
        self.btn_upload.setStyleSheet(
            "background: #1a73e8; color: white; font-weight: bold; "
            "border-radius: 4px; padding: 6px 18px;")
        self.btn_upload.clicked.connect(self._on_upload)
        self.btn_close = QPushButton("关闭")
        self.btn_close.setMinimumHeight(34)
        self.btn_close.clicked.connect(self.reject)
        for b in (self.btn_rescan, self.btn_cancel_scan, self.btn_all_new,
                  self.btn_check_all, self.btn_invert):
            row.addWidget(b)
        row.addWidget(self.btn_tomb)
        row.addStretch(1)
        row.addWidget(self.btn_close)
        row.addWidget(self.btn_upload)
        lay.addLayout(row)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        self._refresh_upload_label()

    # ── 扫描 ──

    def _start_scan(self):
        if self._worker is not None and self._worker.isRunning():
            return
        self._items = []
        self.table.setRowCount(0)
        self.bar.setRange(0, 0)
        self.bar.setVisible(True)
        self.summary_lbl.setText("正在扫描…（第一次要遍历目录、算文件指纹并展开压缩包，"
                                 "几十秒到几分钟量级）")
        self.btn_upload.setEnabled(False)
        self.btn_cancel_scan.setEnabled(True)
        try:
            from local_scan import default_roots, accepted_types_text  # noqa: F401
            shown = self._roots if self._roots else default_roots()
            self.roots_lbl.setText(
                "扫描范围：" + "、".join(os.path.basename(r.rstrip("/")) or r
                                          for r in shown)
                + "（自动跳过虚拟环境、系统目录与 Office 临时锁文件；认 "
                  + accepted_types_text() + "，压缩包里的表格会被解出来一起判）")
        except Exception:
            pass
        self._worker = ScanWorker(roots=self._roots, parent=self)
        self._worker.progress.connect(self._on_scan_progress)
        self._worker.finished.connect(self._on_scan_finished)
        self._worker.start()

    def _on_scan_progress(self, msg: str, done: int, total: int):
        self.stage_lbl.setText(msg)
        if total > 0:
            if self.bar.maximum() == 0:
                self.bar.setRange(0, total)
            self.bar.setValue(done)

    def _on_cancel_scan(self):
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self.stage_lbl.setText("正在取消：处理完当前这个文件就停下…")
            self.btn_cancel_scan.setEnabled(False)

    def _on_scan_finished(self, ok: bool, items: list, stats: dict, note: str):
        self.bar.setRange(0, 0)
        self.bar.setVisible(False)
        self.btn_cancel_scan.setEnabled(False)
        if not ok:
            self.summary_lbl.setText(note or "扫描失败")
            QMessageBox.warning(self, "扫描失败", note or "未知错误")
            return
        self._items = list(items)
        self._stats = dict(stats or {})
        self._populate()
        try:
            from local_scan import summary_line
            line = summary_line(self._stats)
        except Exception:
            line = f"发现 {len(self._items)} 个文件"
        self.summary_lbl.setText(("⚠ " + note + "\n" if note else "") + line)
        # 兑现 summary_line 里「详见清单下方」那句话：哪些包没取全、为什么。
        notes = self._stats.get("archive_notes") or []
        self._notes = list(notes)
        try:
            from local_scan import archive_notes_text
            brief = archive_notes_text(notes)
        except Exception:
            brief = f"{len(notes)} 个压缩包/成员没有全部取出" if notes else ""
        if notes:
            self.notes_lbl.setText(brief)
            self.notes_lbl.setToolTip(
                "同原因的多个包已合并成一句；逐条明细点右侧「查看明细」")
            self.notes_lbl.setVisible(True)
            self.notes_detail_btn.setVisible(True)
            self.notes_detail_btn.setText(f"查看明细（{len(notes)} 条）")
        else:
            self.notes_lbl.clear()
            self.notes_lbl.setVisible(False)
            self.notes_detail_btn.setVisible(False)
        # 读取能力升级后重判了多少个文件必须说清楚：不解释的话，用户看到的是
        # 「上次记读不了的文件这次自己有了结论」，像工具在偷偷改口（台账里的
        # 「读不了/不合格」只在旧版读法下成立，新版本会主动作废它们重判）。
        if self._stats.get("negatives_reset"):
            self.summary_lbl.setText(
                self.summary_lbl.text() +
                f"\n读取能力已更新：本次重判了 {self._stats['negatives_reset']} 个"
                "此前记为「读不了 / 不合格」的文件（已入库的记录不受影响）")
        # 扫完了就不许再挂着「正在识别 xxx…」：那是进度回调留下的尾巴，用户对着
        # 已经能点的「上传勾选项」看见「正在…」，会以为还得再等一会儿——界面里每
        # 一句现在进行时都必须真的是现在进行时。
        self.stage_lbl.setText(
            "扫描已结束。勾好要传的行再点右下角「上传勾选项」；"
            "要撤回池子里的错误数据，点「剔除已入库的错误数据…」。")
        if self._stats.get("denied"):
            self.stage_lbl.setText(
                f"有 {self._stats['denied']} 个目录没有读取权限被跳过"
                "（系统设置 → 隐私与安全性 → 文件与文件夹 里授权后重新扫描）")
        self.btn_upload.setEnabled(True)
        self._refresh_upload_label()

    def _show_notes_detail(self):
        """逐条明细窗口：全量列出「哪个包、哪一句实话」，可滚动、可选中复制。

        合并只发生在正文那一行，这里一条都不合并——用户要核对的是「到底少了哪些」，
        给他一份归纳过的摘要等于替他做结论。悬停提示不能滚动也不能复制，所以完整
        明细必须有这个独立入口。
        """
        notes = list(self._notes or [])
        try:
            from local_scan import archive_notes_detail
            text = archive_notes_detail(notes)
        except Exception:
            text = "\n".join(f"· {n}：{r}" for n, r in notes)
        dlg = QDialog(self)
        dlg.setWindowTitle(f"压缩包没有全部取出的明细（{len(notes)} 条）")
        dlg.resize(780, 480)
        d_lay = QVBoxLayout(dlg)
        tip = QLabel("下面是本次扫描对每个压缩包/成员的原始记录："
                     "同原因的包归在一起只是排版，每条包名与结论都按原文列出。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color: #555;")
        d_lay.addWidget(tip)
        box = QPlainTextEdit()
        box.setReadOnly(True)
        box.setPlainText(text or "没有记录")
        box.setStyleSheet("font-family: Menlo, monospace; font-size: 12px;")
        d_lay.addWidget(box, 1)
        close = QPushButton("关闭")
        close.clicked.connect(dlg.accept)
        d_lay.addWidget(close, 0, Qt.AlignRight)
        dlg.exec_()

    def _where_tip(self, it: dict) -> str:
        """鼠标停在文件名上该看到什么：包内成员必须先说**它在哪个包里**。

        成员的 path 是工具自己解出来的缓存文件，把它单独端给用户只会让人以为
        磁盘上凭空多了个哈希命名的 Excel；真实位置在 origin（那个包）里。
        """
        if it.get("logical"):
            return (f"在压缩包里的位置：{it['logical']}\n"
                    f"压缩包在哪：{it.get('origin') or '（未知）'}\n"
                    f"扫描时解出的缓存副本：{it.get('path')}")
        return str(it.get("path") or "")

    def _populate(self):
        order = {k: v[2] for k, v in self._STATUS_LABELS.items()}
        items = sorted(self._items,
                       key=lambda it: (order.get(it["status"], 9),
                                       -int(it.get("est_rows") or 0),
                                       str(it.get("display") or "")))
        self._items = items
        self.table.setRowCount(len(items))
        try:
            from local_scan import human_size
        except Exception:
            human_size = lambda n: f"{n}B"
        for r, it in enumerate(items):
            label, color, _ = self._STATUS_LABELS.get(
                it["status"], (it["status"], "#333", 9))
            checked = bool(it.get("checked"))
            cell0 = QTableWidgetItem()
            cell0.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled
                           | Qt.ItemIsSelectable)
            cell0.setCheckState(Qt.Checked if checked else Qt.Unchecked)
            self.table.setItem(r, 0, cell0)
            name = QTableWidgetItem(str(it.get("display") or ""))
            name.setToolTip(self._where_tip(it))
            dir_cell = QTableWidgetItem(str(it.get("dir") or ""))
            dir_cell.setToolTip(self._where_tip(it))
            size = QTableWidgetItem(human_size(int(it.get("size") or 0)))
            rows = it.get("est_rows") or 0
            row_cell = QTableWidgetItem(
                (f"约{rows}" if it.get("rows_partial") else str(rows)) if rows else "—")
            st = QTableWidgetItem(label)
            st.setForeground(QColor(color))
            why = QTableWidgetItem(str(it.get("reason") or ""))
            why.setToolTip(str(it.get("reason") or ""))
            for c, cell in ((1, name), (2, dir_cell), (3, size),
                            (4, row_cell), (5, st), (6, why)):
                self.table.setItem(r, c, cell)
            self.table.setRowHeight(r, 24)
        if items:
            self.table.selectRow(0)

    # ── 勾选 ──

    def _row_item(self, row: int) -> Optional[dict]:
        try:
            return self._items[row]
        except Exception:
            return None

    def _statuses(self):
        """状态常量的惰性导入（对话框可以在没有 local_scan 的情况下被构造测试）。"""
        try:
            from local_scan import ELIGIBLE_STATUSES, DUPLICATE_STATUSES, BAD_STATUSES
            return ELIGIBLE_STATUSES, DUPLICATE_STATUSES, BAD_STATUSES
        except Exception:
            return ("new", "unconfirmed"), ("ingested", "dup_content",
                                            "family_sibling"), ("invalid",
                                                                "unreadable")

    def _set_check(self, row: int, it: dict, want: bool):
        cell = self.table.item(row, 0)
        if cell is None:
            return
        cell.setCheckState(Qt.Checked if want else Qt.Unchecked)
        it["checked"] = want

    def _check_by_status(self, statuses):
        """把勾选精确设为某个状态集合。批量越界只允许走「全选」的确认框。"""
        for r in range(self.table.rowCount()):
            it = self._row_item(r)
            if it is None:
                continue
            self._set_check(r, it, it["status"] in statuses)
        self._refresh_upload_label()

    def _on_only_new(self):
        """「只选「待入库」」：判据就是扫描层的 ELIGIBLE_STATUSES，不在界面重写。"""
        elig, _dup, _bad = self._statuses()
        self._check_by_status(elig)

    def _check_all_bulk(self):
        """批量只作用于「可批量勾选范围」：副本/旧版/不合格一律不勾。"""
        elig, _dup, _bad = self._statuses()
        self._check_by_status(elig)

    def _on_select_all(self):
        """「全选」＝真的全勾，但必须先让人看到自己越过了去重的边界。

        工具不替用户做决定（他可能确实要把旧快照再传一遍），但也绝不允许
        一次点击就把扫描层算出来的重复项静默变成待上传：这里把越界的行数与
        原因分布摊开问一次，取消就维持原勾选不变。
        """
        elig, dup, bad = self._statuses()
        extra = [it for it in self._items if it["status"] not in elig]
        if not extra:
            self._check_all_bulk()
            return
        rows = sum(int(it.get("est_rows") or 0) for it in extra)
        cnt = {}
        for it in extra:
            label = self._STATUS_LABELS.get(it["status"], (it["status"],))[0]
            cnt[label] = cnt.get(label, 0) + 1
        dist = "、".join(f"{k} {v} 个" for k, v in sorted(
            cnt.items(), key=lambda kv: -kv[1]))
        reply = QMessageBox.question(
            self, "全选会勾上重复和不合格的行",
            f"清单里除了「待入库」，还有 {len(extra)} 行是重复或不能入库的：\n"
            f"{dist}\n合计约 {rows} 行。\n\n"
            "重复内容在读取端会按公司名自动归并，不会把池子弄脏，"
            "但会把同一批数据再传一遍（多占上传、多占存储）。\n"
            "「只勾待入库」是推荐的默认；确实要一起传再点「仍然全选」。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            self._check_all_bulk()
            return
        for r in range(self.table.rowCount()):
            it = self._row_item(r)
            if it is not None:
                self._set_check(r, it, True)
        self._refresh_upload_label()

    def _invert_checks(self):
        """反选只在「可批量勾选范围」内翻转；重复/旧版/不合格一律留在不勾。

        逐行手动勾上的旧版在这里会被取消掉——反选本来就是「换一批」的意思，
        真要保留那一份就重新勾它一次，代价远小于反选把 168 份快照静默勾上。
        """
        elig, _dup, _bad = self._statuses()
        for r in range(self.table.rowCount()):
            cell = self.table.item(r, 0)
            it = self._row_item(r)
            if cell is None or it is None:
                continue
            if it["status"] not in elig:
                self._set_check(r, it, False)
                continue
            self._set_check(r, it, cell.checkState() != Qt.Checked)
        self._refresh_upload_label()

    def _on_row_selected(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        it = self._row_item(rows[0].row())
        if not it:
            return
        parts = [f"路径：{it.get('path')}",
                 f"状态：{self._STATUS_LABELS.get(it['status'], (it['status'],))[0]}",
                 f"体积：{it.get('size', 0)} 字节｜修改于 {time.strftime('%Y-%m-%d %H:%M', time.localtime(it.get('mtime') or 0))}"]
        if it.get("fields"):
            parts.append("识别到的字段：" + "、".join(it["fields"]))
        if it.get("est_rows"):
            parts.append(f"采样看到的数据行：{it['est_rows']}"
                         + ("（只看了前 200 行，实际更多）" if it.get("rows_partial") else ""))
        if it.get("preview_source") == "full":
            parts.append("判定依据来自权威整表读取（采样读不出内容时的兜底路径）")
        if it.get("pool_seen"):
            parts.append(f"抽样 {it['pool_seen']} 个公司名里，池子还没有 {it['pool_missing']} 个"
                         "（只是提示，不影响勾选——真读时按整表合并）")
        if it.get("dup_of"):
            parts.append("同一份内容的那一份在：" + str(it["dup_of"]))
        if it.get("note"):
            parts.append("备注：" + it["note"])
        parts.append("文件指纹：" + str(it.get("fp") or "-")[:16])
        self.detail_lbl.setText("\n".join(parts))

    def _checked_items(self) -> List[dict]:
        out = []
        for r in range(self.table.rowCount()):
            cell = self.table.item(r, 0)
            it = self._row_item(r)
            if cell is not None and it is not None and cell.checkState() == Qt.Checked:
                out.append(it)
        return out

    def _refresh_upload_label(self):
        got = self._checked_items()
        rows = sum(int(it.get("est_rows") or 0) for it in got)
        self.btn_upload.setText(
            f"上传勾选项（{len(got)} 个文件" + (f"·约 {rows} 行" if rows else "") + "）")
        self.btn_upload.setEnabled(bool(got))

    def _on_upload(self):
        got = self._checked_items()
        if not got:
            QMessageBox.information(self, "没有勾选", "先勾上要上传的文件（左边那一列）。")
            return
        _elig, dup_statuses, bad_statuses = self._statuses()
        # 只把「内容重复」的两类挑出来问一次：台账显示传过的、本次清单里字节完全
        # 相同的副本、同系列里没被选为代表的新版本。用户比工具更知道自己为什么要
        # 重传一份（比如上次传到一半断了），所以这里只确认、不禁止。
        dup = [it for it in got if it["status"] in dup_statuses]
        if dup:
            rows = sum(int(it.get("est_rows") or 0) for it in dup)
            reply = QMessageBox.question(
                self, "有内容重复的文件被勾上了",
                f"勾选里有 {len(dup)} 个文件的内容与已传过的、清单里另一份字节"
                f"完全相同的、或同系列里更新的一份重复（约 {rows} 行）。\n\n"
                f"例如：{dup[0].get('display')}\n{dup[0].get('reason')}\n\n"
                "重复内容在读取端会按公司名自动归并，不会把池子弄脏，"
                "但会多占一次上传。仍要一起上传吗？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                return
        bad = [it for it in got if it["status"] in bad_statuses]
        if bad:
            reply = QMessageBox.question(
                self, "有被判为不合格的文件被勾上了",
                f"勾选里有 {len(bad)} 个文件扫描时判定为不合格/读不了。\n\n"
                f"例如：{bad[0].get('display')}｜{bad[0].get('reason')}\n\n"
                "继续的话下一步会按权威入库链路真读一遍，读不出记录就是这个文件"
                "确实没有可用数据（会如实告诉你，不会静默跳过）。继续吗？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if reply != QMessageBox.Yes:
                return
        self.selected_items = got
        self.accept()

    def _on_open_tombstone(self):
        dlg = PoolTombstoneDialog(parent=self)
        dlg.exec_()
        # 剔完之后重新扫一次才知道「池里已经有这些」的比对结论变了
        if dlg.removed_count:
            self._start_scan()

    def done(self, result):
        """关掉窗口前收尾：扫描线程还在跑就先断开信号再等它。

        QThread 对象被销毁时线程仍在运行会直接崩掉整个程序，这是 Qt 的硬规则。
        """
        w = self._worker
        if w is not None and w.isRunning():
            w.cancel()
            try:
                w.finished.disconnect(self)
                w.progress.disconnect(self)
            except TypeError:
                pass
            w.wait(15000)
        super().done(result)


# ─────────────────────────────────────────────────────────────────────────────
#  剔除已入库的错误数据（删除向量 / tombstone 的管理界面）
# ─────────────────────────────────────────────────────────────────────────────

class PoolTombstoneDialog(QDialog):
    """给数据池里的指定企业打「剔除标记」。

    为什么不能直接删 OSS 上的文件：原始层（Bronze）只追加、不改写——一个对象里
    混着多次采集的数据，改写它等于拿读-改-写赌并发，而且历史采集记录一旦被动过
    就再也无法回溯。工业界（Delta Lake / Iceberg / Cassandra）的统一做法是
    **追加一条不可撤回的删除标记，读取端用当前标记重放**。本对话框就是这条链路的
    入口，判据与执行都在 data_pool.DataPoolManager.tombstone_companies 里。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("剔除数据池里的错误数据")
        self.setMinimumSize(860, 560)
        self.resize(980, 640)
        self._mgr = None
        self._master: List[dict] = []
        self._hits: List[dict] = []
        self.removed_count = 0
        self._init_ui()
        self._load_master()

    def _init_ui(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.setContentsMargins(16, 14, 16, 14)

        head = QLabel("🗑 剔除数据池里的错误数据")
        f = head.font()
        f.setPointSize(13)
        f.setBold(True)
        head.setFont(f)
        lay.addWidget(head)

        tip = QLabel(
            "适用于：联调测试数据、误传的说明文档、确认不该进池的企业。\n"
            "打标记后**所有人**的搜索结果、导出、去重合并都会自动跳过这些企业；"
            "原始文件保持不动，审计与回溯能力不受影响。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color: #666;")
        lay.addWidget(tip)

        row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入公司名关键字（支持部分匹配），回车查找")
        self.search_input.returnPressed.connect(self._on_find)
        self.btn_find = QPushButton("查找")
        self.btn_find.setMinimumHeight(32)
        self.btn_find.clicked.connect(self._on_find)
        row.addWidget(self.search_input, 1)
        row.addWidget(self.btn_find)
        lay.addLayout(row)

        cols = ["选", "公司名", "电话", "邮箱", "官网", "数据来源", "提交人", "已剔除"]
        self.table = QTableWidget(0, len(cols))
        self.table.setHorizontalHeaderLabels(cols)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 40)
        self.table.setColumnWidth(2, 150)
        self.table.setColumnWidth(3, 160)
        self.table.setColumnWidth(4, 140)
        self.table.setColumnWidth(5, 120)
        self.table.setColumnWidth(6, 90)
        self.table.setColumnWidth(7, 70)
        lay.addWidget(self.table, 1)

        # 原因绝不打好默认值：预填的文案会被原样写进团队可见的标记里，
        # 给一家真实重复企业写「联调测试数据」就是往审计日志里造第二条错数据
        self.reason_input = QLineEdit("")
        self.reason_input.setPlaceholderText(
            "剔除原因（必填，写进标记、团队都能看见，例如「联调测试数据」）")
        self.reason_input.setMinimumHeight(32)
        lay.addWidget(self.reason_input)

        self.status_lbl = QLabel("总表加载中…")
        self.status_lbl.setWordWrap(True)
        self.status_lbl.setStyleSheet("color: #888; font-size: 11px;")
        lay.addWidget(self.status_lbl)

        btn = QHBoxLayout()
        self.btn_marked = QPushButton("查看已打的剔除标记")
        self.btn_marked.setMinimumHeight(34)
        self.btn_marked.clicked.connect(self._on_show_marked)
        self.btn_close = QPushButton("关闭")
        self.btn_close.setMinimumHeight(34)
        self.btn_close.clicked.connect(self.reject)
        self.btn_ok = QPushButton("剔除勾选的企业")
        self.btn_ok.setMinimumHeight(36)
        self.btn_ok.setMinimumWidth(140)
        self.btn_ok.setObjectName("primary")
        self.btn_ok.setStyleSheet(
            "background: #c62828; color: white; font-weight: bold; "
            "border-radius: 4px; padding: 6px 18px;")
        self.btn_ok.clicked.connect(self._on_tombstone)
        btn.addWidget(self.btn_marked)
        btn.addStretch(1)
        btn.addWidget(self.btn_close)
        btn.addWidget(self.btn_ok)
        lay.addLayout(btn)

    def _load_master(self):
        try:
            self._mgr = DataPoolManager()
        except Exception as e:
            self.status_lbl.setText(f"数据池模块不可用：{e}")
            self.btn_ok.setEnabled(False)
            return
        self._load_worker = LoadMasterWorker(force_refresh=False, mgr=self._mgr)
        self._load_worker.progress.connect(self.status_lbl.setText)
        self._load_worker.finished.connect(self._on_loaded)
        self._load_worker.start()

    def _on_loaded(self, success: bool, master: list, message: str):
        if not success:
            self.status_lbl.setText(f"总表加载失败：{message}")
            return
        self._master = master or []
        self.status_lbl.setText(
            f"{message}｜当前池子 {len(self._master)} 家企业，"
            f"已有剔除标记 {len(self._mgr.tombstone_list())} 条")

    def _on_find(self):
        kw = self.search_input.text().strip()
        if not kw:
            QMessageBox.information(self, "没给关键字", "先输入公司名关键字再查找。")
            return
        if not self._master:
            QMessageBox.information(self, "总表还没好", "总表还在加载，稍等一下再查。")
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            hits = self._mgr.find_for_tombstone(kw, limit=300)
        finally:
            QApplication.restoreOverrideCursor()
        self._hits = hits
        self.table.setRowCount(len(hits))
        for r, h in enumerate(hits):
            cell0 = QTableWidgetItem()
            cell0.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled
                           | Qt.ItemIsSelectable)
            cell0.setCheckState(Qt.Unchecked)
            self.table.setItem(r, 0, cell0)
            vals = [h.get("company") or "",
                    "、".join(h.get("phones") or []) or "—",
                    "、".join(h.get("emails") or []) or "—",
                    h.get("website") or "—",
                    h.get("source") or "—",
                    h.get("source_user") or "—",
                    "已剔" if h.get("tombstoned") else "—"]
            for c, v in enumerate(vals, start=1):
                it = QTableWidgetItem(str(v))
                it.setToolTip(str(v))
                if h.get("tombstoned"):
                    it.setForeground(QColor("#9e9e9e"))
                    if c == 1:
                        # 已经剔过的行必须能查出「谁、什么时候、因为什么」，
                        # 不然过两周没人知道这条为什么不在池子里
                        it.setToolTip(
                            f"已由 {h.get('source_user') or '?'} 剔除"
                            f"｜原因：{h.get('reason') or '未填写'}")
                self.table.setItem(r, c, it)
        self.status_lbl.setText(
            f"关键字「{kw}」命中 {len(hits)} 家"
            + ("（只显示前 300 家，换更长的关键字缩小范围）" if len(hits) >= 300 else "")
            + "，勾上要剔除的行")
        if not hits:
            QMessageBox.information(
                self, "没找到",
                f"数据池里没有公司名含「{kw}」的记录。\n"
                "可能是名字写法不同，换一个关键字再试。")

    def _checked_names(self):
        """返回 ``(待打标企业名, 已经打过的家数)``。

        已经打过标记的行**不再重复打标**：标记只追加、不可撤回，同一企业再写一份
        等于给后来的同事留下两条互相矛盾的剔除记录（原因还可能不一样）。
        """
        out, already = [], 0
        for r in range(self.table.rowCount()):
            cell = self.table.item(r, 0)
            if cell is None or cell.checkState() != Qt.Checked:
                continue
            if r >= len(self._hits):
                continue
            h = self._hits[r]
            if h.get("tombstoned"):
                already += 1
                continue
            name = h.get("company") or ""
            if name:
                out.append(name)
        return out, already

    def _on_tombstone(self):
        names, already = self._checked_names()
        if not names:
            if already:
                QMessageBox.information(
                    self, "已经剔过了",
                    f"勾选的 {already} 家之前已经打过剔除标记，本次不会重复打标\n"
                    "（标记只追加、不可撤回，重复打会给同事留下互相矛盾的记录）。\n"
                    "点「查看已打的剔除标记」可以看到是谁、什么时候、因为什么剔的。")
            else:
                QMessageBox.information(self, "没勾选", "先勾上要剔除的企业。")
            return
        # 标记不可撤回，原因留空就等于给团队留一条查无依据的记录
        reason = self.reason_input.text().strip()
        if not reason:
            QMessageBox.information(
                self, "请填写剔除原因",
                "剔除标记会同步给所有同事且不可撤回，必须写清原因\n"
                "（例如「联调测试数据」「重复上传的测试样本」）。")
            self.reason_input.setFocus()
            return
        preview = "\n".join(f"· {n}" for n in names[:10])
        more = f"\n……共 {len(names)} 家" if len(names) > 10 else ""
        skipped = (f"\n（另有 {already} 家已经打过标记，本次自动跳过）\n"
                   if already else "\n")
        reply = QMessageBox.question(
            self, "确认剔除",
            f"将给下面 {len(names)} 家企业打「剔除标记」：\n\n{preview}{more}\n\n"
            f"原因：{reason}\n"
            + skipped +
            "标记是**团队级**的：所有同事的数据池搜索结果与导出都会不再包含这些企业。\n"
            "原始文件不会被删除（保留审计与回溯），标记本身也是只追加、不可撤回。\n\n"
            "确认执行？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            res = self._mgr.tombstone_companies(names, reason=reason)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "剔除失败", str(e))
            return
        QApplication.restoreOverrideCursor()
        self.removed_count += int(res.get("marked") or 0)
        try:
            from audit_logger import get_audit_logger
            get_audit_logger().log("pool_tombstone", self._mgr.username, {
                "marked": res.get("marked"), "removed": res.get("removed"),
                "reason": reason, "filename": res.get("filename"),
                "names": names[:50],
            })
        except Exception:
            pass
        main_win = self.parent()
        if main_win is not None:
            _show_toast(main_win,
                        f"🗑 已剔除 {res.get('marked')} 家（本机总表去掉 "
                        f"{res.get('removed')} 条）", 'success')
        self.status_lbl.setText(
            f"已写剔除标记：{res.get('filename')}｜本机总表去掉 {res.get('removed')} 条。"
            "同事们的下一次同步会自动跳过这些企业。")
        self._on_find()   # 重新查一遍，让「已剔」列即时反映最新状态

    def _on_show_marked(self):
        entries = self._mgr.tombstone_list() if self._mgr else []
        if not entries:
            QMessageBox.information(self, "剔除标记", "目前没有任何剔除标记。")
            return
        lines = []
        for e in entries[:200]:
            lines.append(f"· {e.get('company')}｜{e.get('time')}｜"
                         f"{e.get('actor')}｜{e.get('reason')}")
        more = f"\n……共 {len(entries)} 条，只显示前 200 条" if len(entries) > 200 else ""
        box = QMessageBox(self)
        box.setWindowTitle(f"已打的剔除标记（{len(entries)} 条）")
        box.setIcon(QMessageBox.Information)
        box.setText("以下企业在数据池的读取口径里已被剔除（原始文件未改动）：\n\n"
                    + "\n".join(lines) + more)
        box.exec_()

    def done(self, result):
        w = getattr(self, "_load_worker", None)
        if w is not None and w.isRunning():
            try:
                w.finished.disconnect(self)
                w.progress.disconnect(self)
            except TypeError:
                pass
            w.wait(20000)
        super().done(result)
