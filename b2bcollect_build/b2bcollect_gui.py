"""
b2bcollect_gui.py —— B2B 多平台企业数据自动采集工具（PyQt5 GUI）

一个共享 Chrome，需浏览器的站各占一个标签并行采集：
  · 关键词并行（9 站）：中国制造网 made-in-china.com / 八方资源网 b2b168.com /
    顺企网 11467.com / 爱采购 b2b.aipage.com / 麦麦B2B maimaib2b.com /
    跨国采购网 globalimporter.net / 马可波罗 cn.makepolo.com / 世界工厂网 qiye.gongchang.com
    （除中国制造网外均纯 HTTP 不占标签）/ 1688（需登录，默认不勾）；
  · 名单驱动（企知道）：从「待采集公司名单」逐家补采，看门狗语义——名单空则休眠，
    关键词采集清洗掉的无联系方式废数据会自动回流到名单、唤醒企知道补采（变废为宝）。
导入或粘贴一批关键词（或上传公司名单）→ 智能匹配行业列表页 → 进详情页取联系方式 →
边采边自动保存 → 导出「原始 + 清洗后入库 + 国内只留手机号」三份 Excel；支持断点续采、被验证码/风控拦截时
弹窗等人工过验证后自动继续、导入云展动力客户库。

运行：python b2bcollect_gui.py
"""
import os
import re
import sys
import json
import time
import hashlib
import zipfile
import threading
from pathlib import Path
from datetime import datetime

from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer, QSize, QObject, QEvent, QRect
from PyQt5.QtGui import QFont, QPalette, QColor, QPainter, QFontMetrics
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTextEdit, QFileDialog, QSpinBox, QDoubleSpinBox,
    QProgressBar, QMessageBox, QGroupBox, QPlainTextEdit, QDialog,
    QLineEdit, QCheckBox, QListWidget, QListWidgetItem, QAbstractItemView,
    QStackedWidget, QButtonGroup, QInputDialog, QDialogButtonBox, QFrame,
    QSizePolicy, QGridLayout, QRadioButton, QScrollArea, QStyle, QStyleOptionButton,
)

# ── 启动失败的唯一收口 ──────────────────────────────────────────────────────
# 这一组定义必须排在下面那些「本项目自己的模块」之前：导入它们中的任何一个都可能
# 在打包态当场失败，而那时 main() 还没开始跑，没有 except 兜着，屏幕上只剩一段裸
# traceback（2026-11-12 Windows 实机那段 base_library.zip 就是用户从黑窗口里逐字抄
# 出来的）。收口写在前面，才够得到那一类故障。

# 崩溃日志的文件名：界面话术、落盘、使用说明三处说的是同一件事，只许有一份写法
# （使用说明里那句「桌面上的 B2B采集工具启动错误.log」由离线测试对着这个常量验）。
FATAL_LOG_NAME = "B2B采集工具启动错误.log"


def _fatal_log_dirs():
    """崩溃日志的候选落点：桌面优先（用户自己就能找到并发出来），其次用户目录。

    这里刻意写字面量 ``.b2bcollect`` 而不是问 tool_config：会走到这一步的包，很可能
    正是 tool_config 自己没加载上——报信的路径不能依赖那个正在被报信的模块。
    """
    return [Path.home() / "Desktop", Path.home() / ".b2bcollect"]


def _fatal_log_header() -> str:
    """崩溃日志的抬头：先写「这段栈是在什么运行形态下产生的」。
    只有栈没有形态，同一句 ``Read-only file system`` 可能是转置包、可能是没权限的
    Program Files、也可能是被安全软件拦住的桌面目录——三种成因给的是三个不同的处置。
    这里说的一律是**本机实测到的路径**，不写「大概是哪种」。永不抛异常：连日志
    自己都把程序带崩，就等于本次事故再没有留下任何证据。
    """
    lines = [f"运行形态：platform={sys.platform} "
             f"frozen={'是' if getattr(sys, 'frozen', False) else '否'}",
             f"    可执行文件：{sys.executable}",
             f"    随包数据目录：{getattr(sys, '_MEIPASS', '（源码态，无此目录）')}"]
    try:
        import tool_config as _tc
        lines.append(f"    转置运行（App Translocation）：{_tc.app_translocated()}")
        lines.append(f"    持久状态目录：{_tc.state_dir()}")
    except Exception:
        pass
    try:
        import runtime_paths as _rp
        lines.append(f"    风鸟/爱企查数据目录：{_rp.stable_data_dir()}")
    except Exception:
        pass
    return "\n".join(lines) + "\n\n"


def _fatal_log(exc):
    """启动即崩时把 traceback 落盘：Finder 双击打开没有终端，错误看不见，
    日志就是在对方电脑上排查「图标转一下消失」的唯一线索。"""
    import traceback
    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    for d in _fatal_log_dirs():
        try:
            d.mkdir(parents=True, exist_ok=True)
            (d / FATAL_LOG_NAME).write_text(
                _fatal_log_header() + text, encoding="utf-8")
            break
        except OSError:
            continue


def _report_fatal_startup(exc) -> None:
    """把「工具起不来」这件事实说人话，并在用户找得到的地方留下一份日志。

    两种形态共用这一份话术（同一事实只许有一份写法）：
      ① main() 里建界面失败；
      ② **导入本项目模块就失败**——它发生在 main() 之前，屏幕上连一个像素都还没有，
         PyInstaller 只会甩一段裸 traceback，非技术用户既看不懂也发不出来。
    本函数**永不抛异常**：报告这件事自己再把程序带崩，本次事故就什么证据都没留下。
    """
    _fatal_log(exc)
    try:
        paths = "\n".join(str(d / FATAL_LOG_NAME) for d in _fatal_log_dirs())
        body = (f"{type(exc).__name__}: {exc}\n\n"
                "详细堆栈见这份日志——先看第一个位置，找不到就看第二个：\n" + paths)
        # 没有可用图形会话时（CI 的 offscreen、无头机器）绝不弹模态框：弹了就等于
        # 等一次永远不会来的点击，症状从「起不来」变成「卡死」，比原故障更难查。
        if "offscreen" in str(os.environ.get("QT_QPA_PLATFORM") or "").lower():
            print(f"❌ 工具启动失败（无图形会话，日志见下）：\n{body}", flush=True)
        else:
            QMessageBox.critical(None, "工具启动失败", body)
    except Exception:
        pass


# 本项目自己的模块。这些名字**不是可选依赖**：少任何一个，界面上的按钮、通道、档位
# 投影全都无从建立，所以失败时不降级、直接收口退出——但收口之后桌面上会多一份带运行
# 形态抬头的日志，而不是一段只能靠用户手抄的裸 traceback（打包态的 ``<frozen ...>``
# 栈帧对用户没有任何信息量，对发包方却有全部信息量）。
try:
    from parallel_session import (ParallelSession, SITE_ORDER, SITE_NAMES,
                                  LIST_CHANNEL_SLUGS, STATS_LABELS, STATUS_ORDER, QZD_SLUG)
    from cdp_base import BLOCK_TAG_IP, BLOCK_TAG_CAP, CdpSiteCrawler
    import customer_library_cleaner as _cleaner
    import keyword_expander as _kwexp
    import province_filter as _pfilter
    # 交付档位（对内 full / 对外精简版）的唯一真源：界面上「哪些入口存在」全部由它投影，
    # 不在几十个按钮上各写一遍 if lite（那样必然漏入口，见 edition.py 模块头）。
    import edition
except Exception as _bootstrap_exc:               # noqa: BLE001 - 收口后按启动失败退出
    _report_fatal_startup(_bootstrap_exc)
    # SystemExit 而不是原样 raise：原样抛出去会让 PyInstaller 再弹一个自带 traceback 的
    #  fatal error 框，用户面对两个框、其中一个是天书。堆栈已经完整落进上面那份日志，
    # 退出码非零也照样能被 CI 抓到——证据一样不少，只少一个吓人的框。
    raise SystemExit(1) from _bootstrap_exc

try:
    import enhanced_cleaner as _enhanced
    HAS_ENHANCED = True
except ImportError:
    HAS_ENHANCED = False

try:
    from pool_dialogs import (SyncDialog, SearchDialog, ImportPreviewDialog,
                              LocalScanDialog, SyncWorker)
    from data_pool import DataPoolManager
    from oss_pool_client import OSSPoolError
    HAS_POOL = True
except ImportError:
    HAS_POOL = False

try:
    import access_guard as _access_guard
    HAS_ACL_GUARD = True
except ImportError:
    HAS_ACL_GUARD = False

# 平台适配器层：它同时是「名单通道模块到底加载上了没有」的唯一判据来源
# （missing_list_channels 由使用者自己声明，界面不另数一遍平台名）。
try:
    import site_adapters as _adapters
    HAS_ADAPTERS = True
except Exception:
    HAS_ADAPTERS = False

# 统一配置/凭证解析链：它自己也是**产物自检要验的一项**（漏收它就等于产物读不到
# 任何配置），所以这里按可选依赖的同一套写法收口，绝不在调用点各写一份 try/except。
try:
    import tool_config as _tool_config
    HAS_TOOL_CONFIG = True
except ImportError:
    HAS_TOOL_CONFIG = False

# 状态行站点状态标记（图标+颜色+文字三通道，对齐 GitHub Actions/Jenkins 任务实况惯例）：
# done=✅绿粗「已完成」/ stopped=■橙粗「已停止」/ running=●蓝「进行中」
# 字形选高覆盖度的 ✅/●/■（避开 ⏳ 这类小字号下会糊成方块的 emoji）
STATS_STYLE = {
    "done": ('#2e7d32', "✅"),
    "stopped": ('#e65100', "■"),
    "running": ('#1a73e8', "●"),
}
# 深色主题下的提亮版：色相完全一致，只把亮度抬起来。
# #2e7d32 这种深绿在近黑底上对比度不足（看着像「已完成」压根没点亮），
# 状态行是最需要一眼扫到的信息，绝不能靠用户去猜颜色。
STATS_STYLE_DARK = {
    "done": ('#66bb6a', "✅"),
    "stopped": ('#ffa726', "■"),
    "running": ('#8ab4f8', "●"),
}


def _muted_color():
    """次要文字颜色按系统深浅色自适应。
    写死 #666/#999 在深色模式下会「字在但看不见」——实测深色系统上五行站点看板
    在近黑底上完全隐形，只剩一块空白把输出日志挤出屏幕。取前景与底色的中间值，
    深浅两套主题下都保证可读，同时仍是视觉上的次要层级。"""
    try:
        pal = QApplication.palette()
        fg = pal.color(QPalette.WindowText).getRgb()[:3]
        bg = pal.color(QPalette.Window).getRgb()[:3]
        return "#%02x%02x%02x" % tuple((f + b) // 2 for f, b in zip(fg, bg))
    except Exception:
        return "#888888"


def _muted_style(extra=""):
    return f"color:{_muted_color()};{extra}"


def _is_dark_theme():
    """当前是否深色主题（写死颜色在深/浅底上的可读性差很多，必须先看一眼背景亮度）。"""
    try:
        return QApplication.palette().color(QPalette.Window).lightness() < 128
    except Exception:
        return False


def _danger_style(extra=""):
    """错误/提醒红：深色底用亮红，浅色底用深红（同一个 #c62828 在深底上几乎看不清）。"""
    return f"color:{'#ff6b6b' if _is_dark_theme() else '#c62828'};{extra}"


def _tab_toggle_style():
    """分组页「国内/国外」切换按钮的默认态配色按主题给两套。
    写死浅蓝底会在深色面板上亮出一块补丁，和主窗口风格不一致。"""
    checked = "QPushButton:checked { background: #4472C4; color: white; font-weight: bold; }"
    if _is_dark_theme():
        return ("QPushButton { background: #3a3d42; color: #d5d8dc; border: 1px solid #4a4e55; }"
                + checked)
    return ("QPushButton { background: #eef1f6; color: #1a3c6e; border: 1px solid #d0d8e8; }"
            + checked)


def _badge_tone_colors(tone):
    """胶囊徽标的（底色, 前景色）：深浅主题各一套，低饱和底 + 同层级前景。

    单独成一个函数而不是埋在样式串里：界面里既有「用样式表画的徽标 QLabel」，也有
    「用 QPainter 画的角标按钮」，两种画法必须共用同一张色表——否则同一个「失败」在
    两处出现两种红，用户就得先学会辨认哪一种是真红（同一事实只许有一份）。
    """
    dark = _is_dark_theme()
    if tone == "error":
        return ("#3a2224", "#ff6b6b") if dark else ("#fdecea", "#c62828")
    if tone == "ok":
        return ("#1f3324", "#7fc97f") if dark else ("#e8f5e9", "#2e7d32")
    if tone == "run":
        return ("#1d2f4a", "#8ab4f8") if dark else ("#e8f0fe", "#1a63c9")
    if tone == "pause":
        return ("#3a2f1e", "#ffcc80") if dark else ("#fff4e5", "#a85c00")
    return ("#2f3134", _muted_color()) if dark else ("#f0f0f0", _muted_color())


def _status_badge_style(tone):
    """胶囊状态徽标（ok 绿 / error 红 / run 蓝 / pause 橙 / neutral 灰）。
    写死浅底深字在深色主题下像贴了张便签，各态亮度差还会被放大成一个很跳两个很暗；
    这里按主题给成套的低饱和底色加同层级前景，三态只靠色相区分、不靠亮度抢视线。"""
    bg, fg = _badge_tone_colors(tone)
    return ("QLabel { padding: 4px 10px; border-radius: 4px; background: %s; color: %s; }" % (bg, fg))


def _accent_color():
    """强调色（选中态、进度条、主按钮同一支蓝）：深色底用亮蓝，浅色底用深蓝。
    跟 #1a73e8 主按钮同一个色相，界面上「能点的东西」只有一个颜色语言。"""
    return "#8ab4f8" if _is_dark_theme() else "#1a63c9"


def _fmt_task_time(raw):
    """任务时间戳翻成人话：20261030_130000 → 2026-10-30 13:00。
    索引里存的是文件夹名那种时间戳，直接摆给小白看等于摆一串乱码。"""
    s = str(raw or "").strip()
    m = re.match(r"^(\d{4})(\d{2})(\d{2})[\-_ ](\d{2})(\d{2})", s)
    if m:
        return "%s-%s-%s %s:%s" % m.groups()
    return s or "—"


_SUB_SEP = "　·　"   # 次要行分段符：宽度不够时按整段舍弃，不留半截话


def task_row_view(t, exists, done_n=0, current_id=""):
    """把一条任务索引记录翻成「历史任务」列表里看得懂的一行（纯函数，不碰界面）。

    抽成纯函数是为了能离线断言：徽章落在哪个色位、进度按什么算、文件夹没了的行
    是不是真不让选、主标题到底写的是关键词还是那串时间戳——全都能脱离窗口测。
    主标题优先用关键词摘要（用户认得出来自己采的是什么），时间/平台/原词降为次要行。"""
    kws = [str(k) for k in (t.get("keywords") or []) if str(k).strip()]
    total = len(kws)
    cnt = int(t.get("count") or 0)
    # 状态直接比对字面值（task_registry 是可选模块，抽成纯函数后它不该依赖对方）
    status = str(t.get("status") or "")
    tid = str(t.get("id") or "")
    sites = [str(SITE_NAMES.get(s, s)) for s in (t.get("sites") or [])]
    done_n = max(0, min(int(done_n or 0), total)) if total else 0

    if kws:
        head = "、".join(kws[:3]) + ("…" if total > 3 else "")
        title = f"{head}　等 {total} 个关键词" if total > 3 else head
    else:
        title = str(t.get("name") or "").strip() or "未命名任务"

    parts = [_fmt_task_time(t.get("started_at") or tid)]
    if sites:
        parts.append("平台 " + "、".join(sites[:3]) + ("…" if len(sites) > 3 else ""))
    raw = str(t.get("kw_raw") or "").strip().replace("\n", "、")
    if raw and raw != title:
        parts.append("你填的：" + (raw[:18] + "…" if len(raw) > 18 else raw))
    sub = _SUB_SEP.join(parts)

    why = ("选中这条，点「接着采这一条」：关键词、平台、采集间隔都恢复成上次的样子，"
           "已采完的关键词自动跳过，数据接着写进原来的文件夹。")
    if not exists:
        badge, tone = "■ 文件夹已删·接不上", "error"
        why = ("这条任务的数据文件夹已经不在原来的位置（被删了或被拷走改名了），"
               "进度和已采数据都读不到，所以接不上。\n想接着采这批词：把关键词重新填一遍，开一个新任务。")
    elif not kws:
        badge, tone = "■ 没记下关键词·接不上", "error"
        why = "这条是老版本记下的任务，当时没保存关键词与进度，接不上。\n重新填关键词开新任务即可。"
    elif current_id and tid == str(current_id):
        badge, tone = "● 当前已接上", "ok"
        why = "这就是界面上已经载入的这条任务。"
    elif status == "已完成":
        badge, tone = "✅ 已跑完", "ok"
        why = "这批关键词上次已全部跑完。接着采只会补两件事：你新加的词、以及站内新出现的商家。"
    elif status == "已暂停":
        badge, tone = "■ 上次暂停了", "pause"
    elif status == "已停止":
        badge, tone = "■ 上次手动停了", "pause"
    elif status == "采集中":
        badge, tone = "● 上次没跑完", "run"
    else:
        badge, tone = "● 可接着采", "neutral"

    return {
        "title": title,
        "sub": sub,
        "sub_parts": list(parts),
        "badge": badge,
        "tone": tone,
        "count_text": f"已采 {cnt} 家",
        "total": total,
        "done": done_n,
        "percent": 100 if (kws and exists and status == "已完成")
        else (int(done_n * 100 / total) if total else 0),
        "progress_text": (f"已跑完 {done_n}/{total} 个关键词" if (kws and exists) else ""),
        "selectable": bool(kws and exists),
        "tooltip": why,
    }


class TaskRow(QFrame):
    """「历史任务」列表里的一行：圆点 + 主标题/徽章 + 次要信息/已采条数 + 关键词进度条。

    选中态刻意不依赖 QListWidget 原生高亮——macOS 上弹窗一失焦，那条高亮就褪成
    几乎看不见的浅灰，用户回来只会问「我到底选了哪条」。这里选中给足四个通道：
    左侧圆点变实心、整行铺淡强调底、左缘 3px 强调竖条、主标题变强调色（颜色+
    形状+文字三通道，不靠单一颜色传态，色觉障碍也看得懂）；颜色全按系统深浅色给两套。
    不可接上的行：圆点保持空心、文字整行置灰、鼠标悬停不亮（明摆着告诉你点不了）。
    """

    def __init__(self, view, parent=None):
        super().__init__(parent)
        self._v = dict(view or {})
        self._can = bool(self._v.get("selectable"))
        self._sel = False
        self._hover = False
        self.setCursor(Qt.PointingHandCursor if self._can else Qt.ArrowCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 7, 10, 7)
        lay.setSpacing(10)
        # 圆点用字形而不是 QSS 画框：border-radius 在 QLabel 上实测渲染成方框，
        # 而方框在用户眼里是「可多选勾选」，与这里「单选一条任务」的语义相反。
        self.dot = QLabel("○")
        self.dot.setAlignment(Qt.AlignCenter)
        self.dot.setFixedWidth(16)
        df = self.dot.font()
        df.setPointSize(max(11, df.pointSize() + 2))
        self.dot.setFont(df)
        lay.addWidget(self.dot, 0, Qt.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(2)
        line1 = QHBoxLayout()
        line1.setSpacing(8)
        self.lbl_title = QLabel(self._v.get("title") or "")
        self.lbl_title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        f = self.lbl_title.font()
        f.setBold(True)
        self.lbl_title.setFont(f)
        self.lbl_title.setTextFormat(Qt.PlainText)
        line1.addWidget(self.lbl_title, 1)
        self.lbl_badge = QLabel(self._v.get("badge") or "")
        self.lbl_badge.setStyleSheet(_status_badge_style(self._v.get("tone") or "neutral"))
        line1.addWidget(self.lbl_badge, 0, Qt.AlignVCenter)
        col.addLayout(line1)
        line2 = QHBoxLayout()
        line2.setSpacing(8)
        self.lbl_sub = QLabel(self._v.get("sub") or "")
        self.lbl_sub.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        line2.addWidget(self.lbl_sub, 1)
        self.lbl_cnt = QLabel(self._v.get("count_text") or "")
        line2.addWidget(self.lbl_cnt, 0, Qt.AlignVCenter)
        col.addLayout(line2)
        self.bar = QProgressBar()
        self.bar.setFixedHeight(5)
        self.bar.setFixedWidth(150)
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 100)
        self.bar.setValue(int(self._v.get("percent") or 0))
        self.bar.setVisible(bool(self._v.get("progress_text")))
        line3 = QHBoxLayout()
        line3.setSpacing(8)
        line3.addWidget(self.bar, 0)
        self.lbl_prog = QLabel(self._v.get("progress_text") or "")
        self.lbl_prog.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        line3.addWidget(self.lbl_prog, 1)
        col.addLayout(line3)
        lay.addLayout(col, 1)
        self.setToolTip(self._v.get("tooltip") or "")
        # 完整文案另存一份：显示时可能被压成「…」，工具提示里要给全
        self._full = {self.lbl_title: self._v.get("title") or "",
                      self.lbl_prog: self._v.get("progress_text") or ""}
        self._sub_parts = [str(p) for p in (self._v.get("sub_parts") or [])]
        self.set_selected(False)

    def set_selected(self, on):
        self._sel = bool(on and self._can)
        self._apply()

    def enterEvent(self, ev):
        if self._can:
            self._hover = True
            self._apply()
        return super().enterEvent(ev)

    def leaveEvent(self, ev):
        self._hover = False
        self._apply()
        return super().leaveEvent(ev)

    def resizeEvent(self, ev):
        self._elide()
        return super().resizeEvent(ev)

    def _elide(self):
        """宽度不够时把主标题/进度文案压成「…」，而不是把右侧的状态标签和
        「已采 N 家」挤出屏幕——那两处恰恰是选中标识要一眼看到的信息。"""
        for lbl in (self.lbl_title, self.lbl_prog):
            full = self._full.get(lbl, "")
            avail = lbl.width()
            if not full or avail <= 0:
                continue
            fm = lbl.fontMetrics()
            shown = full if fm.horizontalAdvance(full) <= avail \
                else fm.elidedText(full, Qt.ElideRight, avail)
            if lbl.text() != shown:
                lbl.setText(shown)
        self._elide_sub()

    def _elide_sub(self):
        """次要行按「整段」取舍：放不下就整段丢掉并补一个省略号。
        按字符砍会留下「你填的：」这种半截话，比不显示更让人琢磨不透。"""
        avail = self.lbl_sub.width()
        if not self._sub_parts or avail <= 0:
            return
        fm = self.lbl_sub.fontMetrics()
        n = len(self._sub_parts)
        while n > 1 and fm.horizontalAdvance(_SUB_SEP.join(self._sub_parts[:n])) > avail:
            n -= 1
        shown = _SUB_SEP.join(self._sub_parts[:n])
        if n < len(self._sub_parts):
            shown += " …"
        if fm.horizontalAdvance(shown) > avail:
            shown = fm.elidedText(shown, Qt.ElideRight, avail)
        if self.lbl_sub.text() != shown:
            self.lbl_sub.setText(shown)

    def _apply(self):
        dark = _is_dark_theme()
        accent = _accent_color()
        if self._sel:
            bg = "#22304a" if dark else "#e8f0fe"
            edge = accent
        elif self._hover:
            bg = "#2a2d31" if dark else "#f4f6fa"
            edge = "transparent"
        else:
            bg = "transparent"
            edge = "transparent"
        self.setStyleSheet(
            f"TaskRow {{ background: {bg}; border: none; border-left: 3px solid {edge};"
            f" border-radius: 6px; }}")
        self.dot.setText("●" if self._sel else "○")
        muted = _muted_color()
        faded = "#5f6368" if dark else "#b9bfc7"
        self.dot.setStyleSheet(
            "QLabel { background: transparent; border: none; color: %s; }"
            % (accent if self._sel else (muted if self._can else faded)))
        if not self._can:
            self.lbl_title.setStyleSheet("color: %s;" % muted)
        elif self._sel:
            self.lbl_title.setStyleSheet("color: %s;" % accent)
        else:
            self.lbl_title.setStyleSheet("")
        self.lbl_sub.setStyleSheet(_muted_style())
        self.lbl_cnt.setStyleSheet(_muted_style("font-weight:bold;"))
        self.lbl_prog.setStyleSheet(_muted_style())
        track = "#3a3d42" if dark else "#dfe3ea"
        self.bar.setStyleSheet(
            f"QProgressBar {{ border: none; background: {track}; border-radius: 2px; }}"
            f"QProgressBar::chunk {{ background: {accent}; border-radius: 2px; }}")


class _ResizeWatcher(QObject):
    """把某个控件的某类事件转成一次回调（QListWidget 没有现成的 resize 信号）。

    事件类型做成参数而不是写死 Resize：窗口最小宽的再校准要盯的是 LayoutRequest
    （内容**重排**才意味着所需宽度变了，单纯改变控件尺寸反而是结果不是原因）。
    一个类两用比再造一个同构的 watcher 好——两个几乎一样的 eventFilter 迟早一个改了
    另一个没改，而那正是「有些事件漏听」的生成方式。
    """

    def __init__(self, fn, parent=None, ev_types=(QEvent.Resize,)):
        super().__init__(parent)
        self._fn = fn
        self._ev_types = tuple(ev_types)

    def eventFilter(self, obj, ev):
        if ev.type() in self._ev_types:
            self._fn()
        return False


class _PressWatcher(QObject):
    """监听列表视口上的鼠标左键按下，回调收到命中的索引（禁用行不发 clicked）。"""

    def __init__(self, lst, fn, parent=None):
        super().__init__(parent)
        self._lst = lst
        self._fn = fn

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.MouseButtonPress and ev.button() == Qt.LeftButton:
            self._fn(self._lst.indexAt(ev.pos()))
        return False


# 各站链路、防采策略说明：从面板标题里抽出来挂到 tooltip，界面只留短句，
# 详细机制悬停可查（避免一段长文字把整个分区挤乱）。
SITE_TIPS = {
    "mic": "中国制造网 cn.made-in-china.com：中文公司搜索→showroom 联系页（meta 明文中文联系人/电话）。"
           "搜索需浏览器标签过验证码；联系方式采集会复用会话 Cookie 走后台 HTTP，"
           "命中 302 自动回落标签，标签越少可见时间越短。验证码同样接看门狗，过完自动续采。",
    "b2b168": "八方资源网 b2b168.com：纯 HTTP 搜索(Index.aspx POST)→公司自有子域商铺首页+「关于我们」页，"
              "完全不占用浏览器标签（所以看不到它的标签是正常的），速度最快。\n"
              "防采：一家公司最多要发 5 个请求，所以「采集间隔」已下沉到每个请求前对齐，"
              "并有 4s 安全下限（低于它会被 IP 限流）。识别到 4xx/5xx 插页即停手拉长间隔实测、等恢复，"
              "绝不当空数据写库。\n"
              "重要：八方绝大多数拦截是 502 = 服务端拒当前出口 IP，不是验证码，页面上没有滑块可点，"
              "所以那种情况下不会也不该开页面让你找入口（提醒会写明「IP限流·无需操作」）；"
              "想马上恢复只能重拨宽带/换网络（手机热点）换个出口 IP，或等 ≥30 分钟。已采数据不丢，断点续采。\n"
              "八方偶尔也会返真正可人工过的验证插页，那时会自动借一个标签把页面摆到眼前，"
              "过完把 clearance Cookie 抄回 HTTP 会话续采。\n"
              "界面上只剩两个按钮要认：「我换好网络了，马上继续」= 换完上网地址后立刻重测"
              "（不用等下一轮探测，也不用重启工具）；「打开网站让我看一眼」= 自己看看是真有"
              "滑块还是单纯被拦。提醒里不再出现「请到 Chrome 找验证码」这类对白等的话。",
    "11467": "顺企网 11467.com：纯 HTTP 关键词搜索(b2b.11467.com)→公司商铺子域联系方式页(contact.asp)+商铺首页，"
              "联系人/手机/固话/邮箱/QQ 微信都在标签行里直接解析；平时不占用浏览器标签。\n"
              "防采：请求过快会返 HTTP 429 + v.js 人机挑战（纯 HTTP 不执行 JS，拿到的就是空页）——"
              "这不是「没有公司」，已接看门狗：弹窗提醒 + 临时借一个标签把验证页摆到眼前，"
              "人工过验后把 clearance Cookie 抄回 HTTP 会话自动续采；抄不回来就降级用标签继续采。",
    "aipage": "爱采购 b2b.aipage.com（百度爱采购镜像站）：纯 HTTP 关键词搜索→搜索接口翻页(/s/a)→"
              "进店联系方式页(/shop?xzhid=&tpath=contact)，联系人/手机/固话/邮箱/微信/地址服务端直接注水解析；"
              "完全不占用浏览器标签。公司按店铺 id 去重，同店多产品只采一次。\n"
              "防采：限流插页识别到即停手冷却等恢复（不拉浏览器），绝不当空数据写库。",
    "maimaib2b": "麦麦B2B www.maimaib2b.com（全行业企业库，实数 54 万家商铺）：关键词公司搜索"
              "(/search/公司名/)→逐家进 /Shop/编号/link.html，公司名/联系人/手机/固话/地址明文给"
              "（实测抽样：联系人 98%、任一电话 90%，假占位号 0）。\n"
              "防采：这个网站前面挂着一道自动防线，直接请求会被挡（它回一句“请稍候…”）。"
              "实测共享 Chrome 打开约 12 秒会自己放行，多数时候不需你点；"
              "放行后工具把浏览器拿到的“通行证”抄回后台请求，平均 1 秒拿一家且不占标签；"
              "通行证过期再被拦时，才临时借一个标签把页面摆到眼前（必要时你点一下验证框），"
              "过完自动接着跑。\n"
              "商家常把固话填进“手机”栏，工具按号码长相归回正确列，不会拿着区号当手机号。",
    "globalimporter": "跨国采购网 www.globalimporter.net（外贸企业库）：关键词公司搜索"
              "→列表页就把公司名/联系人/电话/主营产品直接写在页面上，所以一家只读一页、"
              "不进公司详情，既快又省（实测一页 20 家，20 家写着联系人、19 家写着号码）。\n"
              "防采：这个站对每个网络地址有当日限量（站方原话：超量后请 2 小时后再看）。"
              "工具不会因此报错也不会当成“没有公司”，而是自己停下来等，到点接着跑；"
              "期间其它站照常采，已采数据不丢。因为它限量里没有可人工点的验证框，"
              "所以这种情况不会弹页面让你白等（提醒里会写明“等一会自动继续”）。\n"
              "这个站的防护服务还会换着法子拦（另一种是一页“您的请求过于频繁”的告示），"
              "工具也认得，同样归为“等一会”而不是“这个行业没公司”，不会把拦住的一页记成空数据。\n"
              "它还有一种不是拦你，而是先要你“点个头”才给看：那一页上没内容，只写了一句"
              "“按这个地址跳一下”。真人用浏览器打开时这一步是浏览器自动走的，所以从来没人"
              "注意到它；工具一开始没走这一步，就在那儿白等了七分钟（2026-11-04 实测：走完之后"
              "同一页马上给出 20 家公司）。现在这一步工具自己会走，你不用管，也不需要开网页。\n"
              "真正要等的是另一件事：这个站限的是你这台机器当时的网络地址，不是工具的模样——"
              "我们特意用真浏览器打开也一样只给那句“请求过于频繁”的告示，所以那种时候没有"
              "“换个姿势立刻能过”的办法，只能等站点自己放行（期间别的站照常采、已采的不丢）。\n"
              "个别商家把手机号拆成“区号-号码-尾号”写，工具会按规则拼回真实号码，"
              "拼不回的宁可不存也不编造。",
    "makepolo": "马可波罗 cn.makepolo.com（全行业企业库，量最大：一个关键词就有上百万条产品）："
              "关键词产品搜索→按商铺去重成公司→每家读两页（它自己的产品页 + 联系方式页）"
              "取齐公司名/联系人/手机/固话/邮箱/地址。同行找得齐：这个页上写的“主营”是一串"
              "行业类目词（冷却塔、冷冻机、制冰机…），经常和搜索词不同字，所以工具把公司名字"
              "也一起看着认（实测只认“主营”会漏掉四成同行，两家一起认就多找回了 25%）。\n"
              "同一家在列表里往往挂好几个产品，工具会把这一家的几张卡并在一起认（实机有一家两张卡"
              "分别写“其他机床附件”和“通用五金配件”，只看前一张就把它当成不相干砍掉了）；"
              "同一类设备的其它叫法也认得——搜“数控机床”时，只写“车床/铣床/加工中心”的同行跟搜索词"
              "一个字都不重样，工具按行业类目对照把它们收回来（实机两页砍掉的名单里就有 5 家这样的同行），"
              "而“货运代理、滤芯、涂料”这类不相干的照样砍。砍掉的也只对当前这个词作废，换一个词还会"
              "重新判一次，不会被永久漏掉。\n"
              "手机号：这个站在联系方式页上把手机号画成一张小图片，但同一个号通常会在它"
              "自己的产品页上直接写成文字——工具先看那处文字（实测一整轮 43 家里有 25 家这样拿到"
              "手机号；把站方已经下架的 12 家剔掉，剩下 31 家活店里 25 家都有手机号），文字没有才动用自带的“看图识号”能力（本机识别、不联网、不花一分钱）"
              "去读那张图；产品页那一页不给看时，还会换同一家公司的另一个产品页再试一轮"
              "（实测近一半公司在列表里就不止一个产品，这一步不花额外的搜索流量）。"
              "读不准的号码会按号码格式校验卡住，宁缺不假。还会顺手从三条路补手机："
              "①商铺网址本身就是手机号 ②手机邮箱（如 138xxxx@139.com）的号码段前缀 ③页面少数商家明写。"
              "站上不同时间给图多少不一样，不给图时这几条路仍能拿到号码；"
              "长时间一张图都拿不到时，工具会自动停止白耗流量的图片请求，只走文字。\n"
              "只收这家公司自己的号码：这个站在页面里挂了“同行企业电话”“联系商家”一类推荐位，"
              "里面的号码是平台广告主的、并不属于这家公司（实测有两家毫不相干的公司挂着一模一样的"
              "两个上海号码）。工具按“号码有没有写在这家公司自己的联系栏里”来认，"
              "还盯住同一个号码挂在第几家店上——挂太多家就不入库，免得把别人的号码卖给你。\n"
              "防采：识别到验证/限流插页即停手拉长间隔等恢复，绝不当空数据写库。\n"
              "另外还有几件小事：①这个站把“传真”与“电话”分开写，工具会把传真单独认出来，"
              "不会把传真机号码当电话入库（那样外呼打不通）；②少数公司的店铺在这个站上已经下架了"
              "（它的全部页面都回一句“不给看”），工具会写成“商铺已关闭”，这是这家真的没公开联系方式，"
              "不是工具没采到，过两轮就会不再重试；③有的商家在“联系人”那一栏把产品和人名连在一起写，"
              "前面粘（“热处理备件杨经理”）后面粘（“田经理二手机床”）都实机抓到过，工具只在贴着人名两头时"
              "把产品词切掉、只留“杨经理”“田经理”这种叫得出口的称呼；像“李总经理”这种本来就完整的职称不会"
              "被切半截，切不出的宁可原样保留也绝不猜名字；"
              "④随手填的坏邮箱（如 test@ 开头、或把 QQ 号抄进域名里的那种）发过去必然退信，工具宁可不存"
              "也不猜一个地址出来，免得把你的邮件发到陌生人邮箱里；⑤有的商家把手机号的前缀当成区号写进“电话”栏"
              "（如手机 19352178592 被写成“0193-52178592”），这种号看着像固话但全国没有这个区号、一定拨不通，"
              "工具会对着真实长途区号表核一遍再决定入库，手机号仍留在手机那一列。",
    "gongchang": "世界工厂网 qiye.gongchang.com（全行业企业库）：关键词公司搜索→列表页取基本信息→"
                "进店铺详情页补电话/邮箱/地址。纯 HTTP 不占浏览器标签。\n"
                "列表页每页 20 家，每词最多 10 页（200 家），但电话覆盖率只有 28%，"
                "所以工具会自动进每家店铺详情页补全联系方式（电话 100%、邮箱 60%、地址 20%）。\n"
                "防采：连续请求过快会触发频控（返薄壳），工具自动重建会话并退避重试，已采数据不丢。\n"
                "省份精准：选中一个省份时工具会把省份参数拼进搜索 URL（服务端筛），"
                "选多个省时退回数据层核验（用公司名+地址判定属地）。",
    "1688": "1688 www.1688.com：关键词→商品/店铺搜索→公司详情页。需先登录（有登录限制，故默认不勾选）；"
            "勾选后会弹窗提示并可一键打开 1688 登录页，请先在浏览器里确认已登录再开始采集。\n"
            "与其它需浏览器的站共享同一个 Chrome（各占自己标签），绝不会另开浏览器。",
    "qzd": "企知道 www.qizhidao.com：**名单驱动**通道（不参与关键词并行）。从「待采集公司名单」逐家模拟人工查询，"
           "拿工商详情/联系方式。需登录（点右侧「打开企知道」按钮先登录）。\n"
           "看门狗：名单空则休眠零占用，关键词采集清洗掉的无联系方式废数据自动回流入列后自动苏醒补采；"
           "采过/查不到的公司名会从名单剔除，避免重复采集。",
    "riskbird": "风鸟 www.riskbird.com：**名单驱动**通道（与企知道/爱企查共用同一份待采名单）。"
           "默认走搜索结果列表页拿 1 电话 1 邮箱，命中本地 30 天卡片缓存的公司不发请求；"
           "列表额度撞墙后自动降级用补全接口登记线索，还可用剩余的详情额度逐家补全。\n"
           "需登录（点「打开风鸟」按钮）。当天额度用尽时这一路会自动让路（不报错、不白烧请求），"
           "名单原样留着，明天或额度恢复后重新开始任务会自动接续。\n"
           "与另外两路共享同一个 Chrome（只占自己一个标签），绝不另开浏览器、也不关别人的标签。",
    "aiqicha": "爱企查 aiqicha.baidu.com（百度旗下企业工商信息）：**名单驱动**通道（与企知道/风鸟共用同一份名单）。"
           "按公司名逐家查联想→详情页，拿工商登记地址/法定代表人/电话/邮箱；"
           "工商登记地址是全体系最硬的属地证据，选省份时按它把关，对不上的不入库。\n"
           "需登录（点「打开爱企查」按钮）。被拦或掉登录时这一路限时等人工（不会拖满半小时），"
           "到点自动让路，未查的公司全部留在名单里下次接续。\n"
           "同窗共享 Chrome 只占自己一个标签，不碰别的平台的页面。",
}


def _lost_list_channels() -> tuple:
    """**该显示却加载不出来**的名单补采通道（＝发包缺陷，与权限无关）。

    为什么必须与「档位藏起来的通道」分开说：两者的界面结果一模一样（② 只剩企知道），
    用户的第一反应却永远是「我账号没这个权限」，于是排查从界面一路查到权限矩阵，
    而真正缺的是包里的两个 .py（2026-11-12 实测：等级 0 的管理员在产物里只看到企知道，
    风鸟/爱企查的采集器根本没进包）。判据由模块的使用者自己给出，界面不数第二遍。

    「该不该给」取 edition 的投影函数，界面这里绝不自己 ``has(CAP_…)``：档位判据
    只有一份是这套改造的红线（否则将来加一档能力位，界面里那份就会说旧话）。
    取交集的意思很直白：只报「档位要给、模块却没有」的那几路——对外档位本来就不
    显示它们，此时点名平台名等于替用户透露一个他无权使用的功能（文字不许先交底）。
    企知道自己没加载上来时两档都会报缺：那一路是所有档位都该有的一路，不存在泄露。
    """
    if not HAS_ADAPTERS:
        return ()
    try:
        expected = tuple(getattr(_adapters, "EXPECTED_LIST_CHANNELS", ()))
        given = set(edition.visible_list_channels(expected, QZD_SLUG))
        return tuple(s for s in _adapters.missing_list_channels() if s in given)
    except Exception:
        return ()


def build_stats_html(text, site_status=None):
    """把状态行文本「已采 N 家 站名 x｜站名 y」渲染为带图标+着色的富文本。

    紧凑徽章式布局（对齐 GitHub Actions / Jenkins 任务实况惯例）：
      · 图标即状态（✅完成 / ●进行中 / ■停止），不再重复「·已完成」文字
      · 按状态分组排列：进行中 → 已完成 → 已停止，视觉焦点在「还在跑的」
      · 站名+数字紧凑拼接，站间用细竖线分隔
      · 完整明细（含状态文字）挂 tooltip，鼠标悬停可读
    纯函数、无副作用，便于离线断言；无站点状态时原样返回纯文本（降级兼容）。"""
    status = site_status or {}
    if not status or "<" in (text or ""):
        return text or ""
    # 总量前缀与各路之间是**全角空格**（parallel_session 发的就是「已采 N 家　站名 x｜…」）。
    # 早先按半角空格切，head 只剩「已采」两个字，「1072 家」连同分隔符一起被吞——
    # 看板上恰恰最需要那个总数（2026-11-10 回归脚本抓到，实机同样存在）。
    head, sep, tail = text.partition("　")
    if not sep:
        head, _s, tail = text.partition(" ")
    palette = STATS_STYLE_DARK if _is_dark_theme() else STATS_STYLE

    # 解析各路数据：提取路名+数字。名单补采通道（企知道/风鸟/爱企查）也必须在这里
    # 解析——过去只遍历 SITE_ORDER，「风鸟补采 12」这种段落在富文本渲染时被整段丢掉，
    # 日志里看得见、看板上一片空白，三平台并行时用户完全无法判断哪一路出了数。
    #
    # 档位不显示的名单通道在这里剔一次：状态行是编排层按**通道注册表**拼的，不受档位
    # 影响；界面没有那一路的勾选框，看板却报「风鸟补采 N 家」，等于凭空把隐藏的功能
    # 喊了出来。判据仍走 edition，不在渲染函数里另写一份 lite 常量。
    _hidden_ch = set(LIST_CHANNEL_SLUGS) - set(
        edition.visible_list_channels(LIST_CHANNEL_SLUGS, QZD_SLUG))
    site_data = []
    for s in STATUS_ORDER:
        if s in _hidden_ch:
            continue
        name = STATS_LABELS.get(s)
        if not name or name not in tail:
            continue
        i = tail.index(name)
        j = tail.find("｜", i)
        raw = tail[i:j].strip() if j >= 0 else tail[i:].strip()
        st = status.get(s, "running")
        # 从 raw 里提取站名后面的数字（如 "中国制造网 47" → "47"，"1688 36" → "36"）
        after_name = raw[len(name):].strip()
        num_m = re.search(r"(\d+)", after_name)
        num = num_m.group(1) if num_m else "0"
        site_data.append((s, name, num, st))

    if not site_data:
        return text

    # 按状态分组排序：running 优先 → done → stopped
    _st_order = {"running": 0, "done": 1, "stopped": 2}
    site_data.sort(key=lambda x: _st_order.get(x[3], 1))

    # 构建紧凑 HTML 段
    segs = []
    tooltip_parts = []
    for s, name, num, st in site_data:
        color, icon = palette.get(st, palette["running"])
        mark = {"done": "已完成", "stopped": "已停止"}.get(st, "进行中")
        # 紧凑格式：图标+站名+数字（不加粗，靠颜色区分）
        segs.append(f'<span style="color:{color}">{icon}{name} {num}</span>')
        tooltip_parts.append(f"{icon} {name} {num} {mark}")

    # 用细竖线分隔，比 ｜ 更紧凑
    _sep = ' <span style="color:#888">|</span> '
    html = head + '　' + _sep.join(segs)
    # 完整明细挂 tooltip
    _tip = '  ·  '.join(tooltip_parts)
    return '<span title="' + _tip + '">' + html + '</span>'

try:
    import yzd_client as _yzd
    import task_registry as _tasks
    HAS_YZD = True
except ImportError:
    HAS_YZD = False


# ── 关键词文件读取（无第三方依赖，docx 走 zip+xml）────────────────
def read_keywords_from_file(path):
    p = Path(path)
    ext = p.suffix.lower()
    if ext in (".txt", ".csv", ".md", ".json", ""):
        return p.read_text(encoding="utf-8", errors="ignore")
    if ext in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook
        wb = load_workbook(p, read_only=False, data_only=True)
        lines = []
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                for cell in row:
                    if cell is not None and str(cell).strip():
                        lines.append(str(cell).strip())
        wb.close()
        return "\n".join(lines)
    if ext == ".docx":
        with zipfile.ZipFile(p) as z:
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
        lines = []
        for para in re.split(r"</w:p>", xml):
            texts = re.findall(r"<w:t[^>]*>(.*?)</w:t>", para, flags=re.S)
            line = "".join(texts)
            line = (line.replace("&amp;", "&").replace("&lt;", "<")
                        .replace("&gt;", ">").replace("&quot;", '"'))
            if line.strip():
                lines.append(line.strip())
        return "\n".join(lines)
    return p.read_text(encoding="utf-8", errors="ignore")


# ── 采集线程：各平台并行由 ParallelSession 内部各起一个线程 ─────────
class CrawlWorker(QThread):
    def __init__(self, session, keywords):
        super().__init__()
        self.session = session
        self.keywords = keywords

    def run(self):
        try:
            self.session.crawl(self.keywords, start_index=0)
        except Exception as e:
            self.session._log(f"❌ 采集线程异常：{e}")


# ── 自动保存线程：各平台合并写「原始 + 清洗后入库 + 国内只留手机号」三份 ──────
class AutoSaveWorker(QThread):
    saved = pyqtSignal(str, int, int)   # 路径, 原始条数, 国内手机条数
    failed = pyqtSignal(str)

    def __init__(self, session, rows, session_dir):
        super().__init__()
        self.session = session
        self.rows = rows
        self.session_dir = session_dir

    def run(self):
        try:
            ts_dir = Path(self.session_dir)
            ts_dir.mkdir(parents=True, exist_ok=True)
            raw = ts_dir / (_tasks.RAW_FILE_NAME if HAS_YZD else "企业数据_原始.xlsx")
            self._write_raw(raw, self.rows)
            cl = ts_dir / "企业数据_清洗后入库数据.xlsx"
            self._write_cleaned(cl, self.rows)
            # 精细化：额外生成「国内只留手机号」文件，与导入客户库同一口径
            cl_mobile = ts_dir / "企业数据_国内只留手机号.xlsx"
            mobile_n = self._write_cn_mobile_file(cl_mobile, self.rows)
            self.saved.emit(str(raw), len(self.rows), mobile_n)
        except PermissionError:
            self.failed.emit("文件正被 Excel/WPS 打开占用，本次自动保存跳过，关闭后下轮补上")
        except Exception as e:
            self.failed.emit(f"自动保存失败：{e}")

    @staticmethod
    def _write_raw(path, rows):
        from openpyxl import Workbook
        from parallel_session import RAW_HEADERS
        from cdp_base import clean_cell
        tmp = str(path) + ".tmp"
        wb = Workbook(); ws = wb.active; ws.title = "B2B多平台企业数据"
        ws.append(RAW_HEADERS)
        for i, r in enumerate(rows, 1):
            # 过 clean_cell：采集到的地址/主营可能带控制字符，
            # 不过滤会让 openpyxl 抛 IllegalCharacterError 炸掉整轮自动保存。
            ws.append([i] + [clean_cell(r.get(h, "")) for h in RAW_HEADERS[1:]])
        wb.save(tmp)
        os.replace(tmp, str(path))

    def _write_cleaned(self, path, rows):
        clean_rows = self.session.clean_records(rows, log=lambda m: None)
        template = _cleaner.locate_template([self.session_dir, str(Path.home() / "Downloads")])
        tmp = str(path) + ".tmp"
        _cleaner.write_clean_excel(clean_rows, tmp, template_path=template, log=lambda m: None)
        os.replace(tmp, str(path))

    def _write_cn_mobile_file(self, path, rows):
        """清洗 → 过滤中国手机号 → 写 18 列模板 Excel，返回写入行数。

        与「清洗数据」三份文件中的「国内只留手机号」同一口径，
        自动保存时同步落盘，方便用户直接拿来核对导入客户库的数据。
        """
        clean_rows = self.session.clean_records(rows, log=lambda m: None)
        mobile_rows = _yzd.filter_cn_mobile_rows(clean_rows, log=lambda m: None)
        template = _cleaner.locate_template([self.session_dir, str(Path.home() / "Downloads")])
        tmp = str(path) + ".tmp"
        _cleaner.write_clean_excel(mobile_rows, tmp, template_path=template, log=lambda m: None)
        os.replace(tmp, str(path))
        return len(mobile_rows)


# ═══════════════════════════════════════════
#  外部表格智能清洗（「清洗数据」按钮：任意 Excel/CSV 名单 → 入库模版格式）
# ═══════════════════════════════════════════
class ExternalCleanWorker(QThread):
    """读外部表格 → 智能匹配列名 → 清洗 → 写出对齐入库模版的《清洗后入库数据》。

    多个文件全量读写可能几十秒，放 GUI 线程会直接白屏，所以跟自动保存一样走后台线程；
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

    @staticmethod
    def _drop(p):
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass

    def _run_all(self):
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
            # 与导入云展同一口径的批内去重，保证清洗结果与入库行数对得上
            rows = _yzd.dedupe_rows(rows, log=self.logged.emit)
        if not rows:
            self.finished_clean.emit(False, "清洗后没有可入库的记录（公司名全为空或状态异常）")
            return
        template = _cleaner.locate_template(start_dirs=self.template_dirs)
        tmp = f"{self.out_path}.tmp"
        try:
            _cleaner.write_clean_excel(rows, tmp, template_path=template, log=self.logged.emit)
            os.replace(tmp, self.out_path)
        except PermissionError:
            self._drop(tmp)
            self.finished_clean.emit(False,
                                     f"目标文件正被 Excel/WPS 打开，写不进去：\n{self.out_path}\n"
                                     f"请先关掉这个文件再重做一次清洗。")
            return
        except Exception as e:
            self._drop(tmp)
            self.finished_clean.emit(False, f"写出清洗结果失败：{e}")
            return
        self._drop(tmp)
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


# ═══════════════════════════════════════════
#  增强三文件清洗（libphonenumber + 区域推断 → 国内/国内手机/国外）
# ═══════════════════════════════════════════
class EnhancedExternalCleanWorker(QThread):
    """增强清洗：libphonenumber 深度解析电话 + 五层区域推断 → 输出三个文件。

    与 ExternalCleanWorker 共用同一套列匹配/合并管线，只替换电话解析与区域判定环节。
    输出：国内（全量）、国内只留手机号（至少一条中国手机号）、国外（境外记录）。
    """
    logged = pyqtSignal(str)
    finished_clean = pyqtSignal(bool, str)

    def __init__(self, paths, output_dir):
        super().__init__()
        self.paths = list(paths or [])
        self.output_dir = output_dir

    def run(self):
        try:
            self._run_all()
        except Exception as e:
            self.finished_clean.emit(False, f"增强清洗异常：{e}")

    def _run_all(self):
        if not HAS_ENHANCED:
            self.finished_clean.emit(False, "增强清洗模块未加载（enhanced_cleaner 导入失败）")
            return
        result, report = _enhanced.enhanced_clean_three_files(
            self.paths, self.output_dir, log=self.logged.emit)
        if not result:
            self.finished_clean.emit(False, "增强清洗后没有可入库的记录")
            return
        bad = [r for r in report if r.get("error")]
        files = "\n".join(
            f"· {r['file']}（{r['sheet']}）：{r['rows']} 条" for r in report if not r.get("error"))
        skipped = "\n".join(f"⚠ {r['file']}：{r['error']}" for r in bad)
        matched = "；".join(
            "，".join(r.get("identified") or []) for r in report if r.get("identified"))
        self.logged.emit(f"🧾 列匹配结果：{matched}" if matched else "🧾 本次没用到列名识别结果")

        lines = ["增强清洗完成（libphonenumber + 五层区域推断）\n"]
        for label in ("国内", "国内只留手机号", "国外"):
            path, count = result.get(label, ("-", 0))
            lines.append(f"  {label}：{count} 条 → {path}")
        lines.append(f"\n读入的文件：\n{files or '· 无'}")
        if skipped:
            lines.append(f"\n被跳过：\n{skipped}")
        summary = "\n".join(lines)
        self.finished_clean.emit(True, summary)


# ══════════════════════════════════════════════
#  云展动力：账号绑定 / 客户库分组 / 导入
# ═══════════════════════════════════════════════
YZD_IMPORT_CHUNK = 5000


def _yzd_font(size=10, bold=False):
    return QFont("Microsoft YaHei" if sys.platform == "win32" else "Arial",
                 size, QFont.Bold if bold else QFont.Normal)


class YzdBindWorker(QThread):
    finished_bind = pyqtSignal(bool, str)

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
    finished_groups = pyqtSignal(int, list, str)

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
    finished_create = pyqtSignal(dict, str)

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
    logged = pyqtSignal(str)
    progress = pyqtSignal(int, int, str)
    task_done = pyqtSignal(str, int, int, int, str)
    finished_all = pyqtSignal(list, str)

    def __init__(self, session, phone, jobs, group_id, foreign_flag, tmp_dir):
        super().__init__()
        self.session = session
        self.phone = phone
        self.jobs = jobs
        self.group_id = group_id
        self.foreign_flag = foreign_flag
        self.tmp_dir = tmp_dir

    def _records(self, job):
        if job.get("records") is not None:
            return job["records"]
        return _tasks.read_records_from_xlsx(job.get("raw_file", ""))

    def run(self):
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
                rows = self.session.clean_records(records, log=self.logged.emit)
                rows = _yzd.dedupe_rows(rows, log=self.logged.emit)
                # 精细化导入：与「清洗数据」三份文件中的「国内只留手机号」同一口径，
                # 没有中国手机号的行不进客户库（固话/邮箱再多也不够格）
                rows = _yzd.filter_cn_mobile_rows(rows, log=self.logged.emit)
            except Exception as e:
                msg = f"清洗失败：{e}"
                self.logged.emit(f"⚠ 「{name}」{msg}")
                results.append((name, 0, 0, 0, msg))
                self.task_done.emit(name, 0, 0, 0, msg)
                continue
            if not rows:
                msg = "清洗后没有含中国手机号的记录可导入"
                self.logged.emit(f"⚠ 「{name}」跳过：{msg}")
                results.append((name, 0, 0, 0, msg))
                self.task_done.emit(name, 0, 0, 0, msg)
                continue
            chunks = [rows[i:i + YZD_IMPORT_CHUNK] for i in range(0, len(rows), YZD_IMPORT_CHUNK)] or [[]]
            imp = sav = fail = 0
            err = ""
            for c_idx, chunk in enumerate(chunks, 1):
                tag = f"{name}（{c_idx}/{len(chunks)}）" if len(chunks) > 1 else name
                self.progress.emit(idx - 1, total, f"正在导入 {idx}/{total}：{tag} · {len(chunk)} 条")
                file_path = os.path.join(self.tmp_dir, f"yzd_import_{idx}_{c_idx}.xlsx")
                try:
                    written = _yzd.write_import_workbook(chunk, file_path, log=self.logged.emit)
                    if written == 0:
                        raise RuntimeError("清洗结果全为空行，没有可上传的数据")
                    self.logged.emit(f"⬆ {tag}：已生成上传文件 {written} 行，正在提交云展…")
                    res = client.import_excel(file_path, group_id=self.group_id,
                                              foreign_flag=self.foreign_flag)
                    imp += res["importTotal"]; sav += res["saveTotal"]; fail += res["failTotal"]
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


class ProvincePickerDialog(QDialog):
    """「选择省份」弹窗（多选版）：全国 31 省 + 港澳台，支持复选。
    对齐大厂（淘宝/京东/美团）地址多选交互：
      · 快捷操作：全选 / 清空 / 按大区一键选
      · 滚动区域：34 个省级行政区分 6 列排布，不撑爆窗口
      · 实时摘要：底部标签动态显示「已选 N 个省：广东、浙江…」「全国（不限省份）」
    选中结果写 dialog.chosen_list（list）；空列表 = 全国不限。"""

    # 大区快捷选择（对齐民政部行政区划分组，用户心智模型一致）
    REGIONS = {
        "华北": ["北京", "天津", "河北", "山西", "内蒙古"],
        "东北": ["辽宁", "吉林", "黑龙江"],
        "华东": ["上海", "江苏", "浙江", "安徽", "福建", "江西", "山东"],
        "中南": ["河南", "湖北", "湖南", "广东", "广西", "海南"],
        "西南": ["重庆", "四川", "贵州", "云南", "西藏"],
        "西北": ["陕西", "甘肃", "青海", "宁夏", "新疆"],
        "特区": ["香港", "澳门", "台湾"],
    }

    def __init__(self, current_list=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择采集省份（可多选）")
        self.setMinimumWidth(560)
        self.chosen_list = list(current_list or [])   # 返回给调用方的选中列表
        self._checks = {}                             # {province_name: QCheckBox}
        self._init_ui()

    # ── UI 构建 ───────────────────────────────────────────────
    def _init_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(10)

        # 说明文字
        tip = QLabel("可多选省份，只采这些省份的企业。支持地区筛选的网站（中国制造网 / 马可波罗 / 1688）"
                     "额外交给网站筛；其余网站按页面地址与公司名把关；看不出省份的转名单补采通道"
                     "（企知道 / 风鸟 / 爱企查）按工商地址反查。"
                     "不选 = 全国不限。")
        tip.setWordWrap(True)
        tip.setStyleSheet(_muted_style())
        lay.addWidget(tip)

        # ── 快捷操作栏 ──
        quick = QHBoxLayout()
        quick.setSpacing(6)
        btn_all = QPushButton("全选")
        btn_all.clicked.connect(lambda: self._set_all(True))
        btn_none = QPushButton("清空")
        btn_none.clicked.connect(lambda: self._set_all(False))
        quick.addWidget(btn_all)
        quick.addWidget(btn_none)
        quick.addSpacing(8)
        quick.addWidget(QLabel("大区："))
        for region_name, provinces in self.REGIONS.items():
            btn = QPushButton(region_name)
            btn.setToolTip(f"快速选择{region_name}地区：{'、'.join(provinces)}")
            btn.clicked.connect(lambda checked, pvs=provinces: self._select_region(pvs))
            quick.addWidget(btn)
        quick.addStretch(1)
        lay.addLayout(quick)

        # ── 省份复选框滚动区域 ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(260)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        container = QWidget()
        grid = QGridLayout(container)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(6)
        grid.setContentsMargins(8, 4, 8, 4)
        for i, pv in enumerate(_pfilter.PROVINCES):
            cb = QCheckBox(pv)
            if pv in self.chosen_list:
                cb.setChecked(True)
            cb.toggled.connect(self._update_summary)
            self._checks[pv] = cb
            grid.addWidget(cb, i // 6, i % 6)
        scroll.setWidget(container)
        lay.addWidget(scroll)

        # ── 已选摘要 ──
        self._summary = QLabel()
        self._summary.setWordWrap(True)
        self._summary.setStyleSheet("font-weight: bold; padding: 4px 0;")
        lay.addWidget(self._summary)
        self._update_summary()

        # ── 确定 / 取消 ──
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("确定")
        bb.button(QDialogButtonBox.Cancel).setText("取消")
        bb.accepted.connect(self._on_ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    # ── 交互方法 ──────────────────────────────────────────────
    def _set_all(self, checked):
        """全选 / 清空所有省份。"""
        for cb in self._checks.values():
            cb.blockSignals(True)
            cb.setChecked(checked)
            cb.blockSignals(False)
        self._update_summary()

    def _select_region(self, provinces):
        """大区快捷选择：清空其他，只选该大区内的省份（单选互斥，避免误叠加）。"""
        for pv, cb in self._checks.items():
            cb.blockSignals(True)
            cb.setChecked(pv in provinces)
            cb.blockSignals(False)
        self._update_summary()

    def _update_summary(self):
        """实时更新底部摘要标签（已选数量 + 省份名列表）。"""
        selected = [pv for pv, cb in self._checks.items() if cb.isChecked()]
        if not selected:
            self._summary.setText("🗺 全国（不限省份）")
            self._summary.setStyleSheet(_muted_style("font-weight: bold; padding: 4px 0;"))
        else:
            self._summary.setText(f"🗺 已选 {len(selected)} 个省：{'、'.join(selected)}")
            self._summary.setStyleSheet(
                f"color:{_accent_color()}; font-weight: bold; padding: 4px 0;")

    def _on_ok(self):
        self.chosen_list = [pv for pv, cb in self._checks.items() if cb.isChecked()]
        self.accept()


class YzdProgressDialog(QDialog):
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
        self.bar.setRange(0, 0)
        self.bar.setTextVisible(False)
        self.bar.setMinimumHeight(20)
        layout.addWidget(self.bar)
        self.detail_lbl = QLabel("导入期间请勿关闭程序，也不要执行其他操作。")
        self.detail_lbl.setFont(_yzd_font(9))
        self.detail_lbl.setStyleSheet(_muted_style())
        self.detail_lbl.setWordWrap(True)
        layout.addWidget(self.detail_lbl)

    def set_text(self, main, detail=""):
        self.info_lbl.setText(main)
        if detail:
            self.detail_lbl.setText(detail)

    def allow_close(self):
        self._allow_close = True

    def closeEvent(self, event):
        event.accept() if self._allow_close else event.ignore()


class YzdGroupDialog(QDialog):
    """「导入云展客户库」弹窗：分组选择 + 历史任务多选。"""
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
        self._groups = {0: None, 1: None}
        self._flag = 0
        self._worker = None
        self._create_worker = None
        self._pending_name = ""
        self._created_id = ""
        self._task_map = {}
        self.setWindowTitle("选择自动导入的客户库分组" if self._pick_only else "导入云展客户库")
        self.setMinimumSize(560, 460)
        self.resize(600, 500)
        self._init_ui()
        self._load_groups()

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
            btn.setStyleSheet(_tab_toggle_style())
            btn.clicked.connect(lambda _c, f=flag: self._switch_flag(f))
            self.tab_group.addButton(btn, flag)
            self.tab_btns[flag] = btn
            tab_row.addWidget(btn)
        tab_row.addStretch()
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setMinimumHeight(32)
        self.refresh_btn.setMinimumWidth(64)
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
        self.group_status_lbl.setStyleSheet(_muted_style())
        self.group_status_lbl.setWordWrap(True)
        lay.addWidget(self.group_status_lbl)

        self.group_list = QListWidget()
        self.group_list.setMinimumHeight(180)
        self.group_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.group_list.itemSelectionChanged.connect(self._update_hint)
        lay.addWidget(self.group_list, 1)

        self.hint_lbl = QLabel(self._hint_text)
        self.hint_lbl.setFont(_yzd_font(9))
        self.hint_lbl.setStyleSheet(_muted_style())
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
        self.task_count_lbl.setStyleSheet(_muted_style())
        lay.addWidget(self.task_count_lbl)
        self.task_list = QListWidget()
        self.task_list.setMinimumHeight(200)
        self.task_list.setSelectionMode(QAbstractItemView.NoSelection)
        self.task_list.itemChanged.connect(lambda _i: self._update_task_hint())
        lay.addWidget(self.task_list, 1)
        self.task_hint_lbl = QLabel("至少选择一个任务")
        self.task_hint_lbl.setFont(_yzd_font(9))
        self.task_hint_lbl.setStyleSheet(_muted_style())
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

    def _set_busy(self, busy):
        for w in (self.new_group_btn, self.search_btn, self.search_edit,
                  self.refresh_btn, self.latest_btn, self.others_btn):
            w.setEnabled(not busy)
        if busy:
            QApplication.setOverrideCursor(Qt.WaitCursor)
        else:
            QApplication.restoreOverrideCursor()

    def _switch_flag(self, flag):
        if flag == self._flag and self._groups[flag] is not None:
            return
        self._flag = flag
        cached = self._groups.get(flag)
        if cached is None:
            self._load_groups()
        else:
            self._render_groups(cached, "")

    def _search(self):
        self._groups[self._flag] = None
        self._load_groups()

    def _refresh_groups(self):
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

    def _on_groups(self, flag, groups, error):
        self._groups[flag] = groups if not error else None
        if flag != self._flag:
            self._set_busy(False)
            return
        self._render_groups(groups, error)
        self._set_busy(False)

    def _render_groups(self, groups, error):
        self.group_list.clear()
        if error:
            self.group_status_lbl.setText(f"分组加载失败：{error}")
            self.group_status_lbl.setStyleSheet(_danger_style())
            self._update_hint()
            return
        self.group_status_lbl.setStyleSheet(_muted_style())
        if not groups:
            keyword = self.search_edit.text().strip()
            self.group_status_lbl.setText(
                "没搜到分组，换个关键词试试" if keyword else
                "该账号下还没有分组，请先点「+ 新建分组」创建一个")
            self._update_hint()
            return
        self.group_status_lbl.setText(
            f"共 {len(groups)} 个分组，选中一个后点右下角导入" if not self._pick_only else
            f"共 {len(groups)} 个分组，选一个当自动导入目标")
        for g in groups:
            num = g.get("clientNum")
            text = f"{g.get('groupName', '')}"
            if num is not None:
                text += f"　（客户数 {num}）"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, g)
            self.group_list.addItem(item)
        if self._created_id and self._select_group_by_id(self._created_id):
            self._created_id = ""
        self._update_hint()

    def _current_group(self):
        items = self.group_list.selectedItems()
        return items[0].data(Qt.UserRole) if items else None

    def _update_hint(self, error=False):
        if self._current_group():
            self.hint_lbl.setText("")
            self.hint_lbl.setStyleSheet(_muted_style())
        elif error:
            self.hint_lbl.setText("请先选择一个分组（必选）")
            self.hint_lbl.setStyleSheet(_danger_style())
        else:
            self.hint_lbl.setText(self._hint_text)
            self.hint_lbl.setStyleSheet(_muted_style())

    def _need_group(self):
        group = self._current_group()
        if group is None:
            self._update_hint(error=True)
            QMessageBox.warning(self, "提示", "请选择一个分组")
        return group

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

    def _on_group_created(self, group, error):
        if error:
            self._set_busy(False)
            self.group_status_lbl.setText(f"新建分组失败：{error}")
            QMessageBox.warning(self, "新建分组失败", error)
            return
        self._groups = {0: None, 1: None}
        self._created_id = str(group.get("id") or "")
        self.search_edit.clear()
        self.group_status_lbl.setText(f"已创建分组「{group.get('groupName') or self._pending_name}」")
        self._load_groups()

    def _select_group_by_id(self, gid):
        for i in range(self.group_list.count()):
            item = self.group_list.item(i)
            data = item.data(Qt.UserRole) or {}
            if str(data.get("id")) == gid:
                self.group_list.setCurrentItem(item)
                return True
        return False

    def _accept_latest(self):
        group = self._need_group()
        if group is None:
            return
        self.mode = self.MODE_LATEST
        self.chosen_group = group
        self.accept()

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
            name = t.get("name") or t.get("id") or "未命名任务"
            text = (f"{name}\n采集时间 {t.get('started_at') or '—'}"
                    f"　{t.get('count') or 0} 条　[{t.get('status') or '—'}]")
            item = QListWidgetItem(text)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            item.setData(Qt.UserRole, t)
            self.task_list.addItem(item)
            self._task_map[id(item)] = t
        if not tasks:
            self.task_count_lbl.setText("暂无采集任务记录：先在主界面完成一次「开始采集 → 采集完成」")
        else:
            self.task_count_lbl.setText(f"共 {len(tasks)} 个任务（按采集时间倒序）")
        self._update_task_hint()

    def _toggle_all(self, checked):
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

    def _update_task_hint(self, error=False):
        n = len(self._checked_tasks())
        if n:
            self.task_hint_lbl.setText("")
            self.task_hint_lbl.setStyleSheet(_muted_style())
        elif error:
            self.task_hint_lbl.setText("请至少选择一个任务（必选）")
            self.task_hint_lbl.setStyleSheet(_danger_style())
        else:
            self.task_hint_lbl.setText("勾选要导入的采集任务（可多选）")
            self.task_hint_lbl.setStyleSheet(_muted_style())
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
        for w in (self._worker, self._create_worker):
            if w is not None and w.isRunning():
                w.wait(3000)
        event.accept()


# ── 界面偏好（目前只有一项：日志区展开/收起）──────────────────────────────
# 为什么落文件而不是 QSettings：本工具的长期状态本来就统一放在可写目录里
# （种子库、名单台账、自检报告），再开一条注册表/plist 的路，只是多一处
# 「只读包里写失败怎么办」要操心，而收益是零。
# 读写都必须**静默降级**：偏好丢了大不了下次回到默认，绝不能为这件事弹错误框，
# 更不能让它挡住窗口显示——一个勾选框的记忆不值得用户重启工具。
_LOG_PREF_KEY = "log_expanded"
_LOG_DEFAULT_EXPANDED = False   # 新机器第一屏就该一屏放得下，所以默认收起


def _ui_prefs_path() -> Path:
    return Path.home() / ".b2bcollect" / "ui_prefs.json"


def _load_log_expanded_pref() -> bool:
    """上次是展开还是收起；读不到一律按默认（收起）。"""
    try:
        raw = json.loads(_ui_prefs_path().read_text(encoding="utf-8"))
        return bool(raw.get(_LOG_PREF_KEY, _LOG_DEFAULT_EXPANDED))
    except Exception:
        return _LOG_DEFAULT_EXPANDED


def _save_log_expanded_pref(expanded: bool) -> None:
    try:
        p = _ui_prefs_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        try:
            cur = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(cur, dict):
                data = cur
        except Exception:
            data = {}
        data[_LOG_PREF_KEY] = bool(expanded)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


# ── 带角标的按钮（收起面板上的「有几条没看」）────────────────────
class _NoticeBadgeButton(QPushButton):
    """把未读告警计数**画**在右上角，而不是拼进按钮文字。

    为什么必须是画的（2026-11-12 两条实测缺陷都记在这笔账上）：把「⚠ 3 条提醒」写进
    文字后，按钮宽度就跟着告警条数长——冷启动按当时文字钉好的宽度地板被顶大，而折叠条
    的策略是 QSizePolicy.Maximum（允许被削到 sizeHint 以下），窗口稍窄文字立刻截成
    「▸ 展开日志（⚠ 3 条…」。同一份文字还让布局门禁量到的形状随磁盘上的历史任务漂移：
    这台机器全绿、那台机器红一条，红得像布局缺陷却查不出复现条件。

    IDE 问题面板、浏览器控制台、macOS Dock 的计数一律是画上去的角标：信号强度一点不减，
    几何却完全恒定（计数从 0 到 99+ 宽高一个像素都不动）。轻重靠**底色 + 悬停原话**两条
    通道：红色胶囊=有失败必须点进去看，琥珀色=只是提醒；悬停里逐档写着「N 条失败 / M 条
    提醒」，色觉障碍或系统高对比度下靠文字照样读得出来（角标上不放符号，理由见 badge_label）。
    """

    CAP = 99                  # 显示封顶：角标是「有事发生」的信号，不是台账
    HEIGHT = 15               # 胶囊高度（比按钮文字小一号，抢文字但不抢焦点）
    H_PAD = 4                 # 胶囊内左右留白
    EDGE = 5                  # 胶囊距按钮右缘
    TEXT_MARGIN = 14          # 文字与胶囊之间必留的间隙（flat 按钮无内边框可借）

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._notice_error = 0
        self._notice_warn = 0
        self._pin_texts = ()
        # 文字靠左：居中的文字会把留白劈成两半，右边就只剩半个占位区，最坏形态的角标
        # （99+）会压到最后一个字上（离线出图当场看到的就是这个）。靠左之后，
        # pin_texts 钉下的那份占位完整地留在右侧。
        # Qt5 的 QPushButton 没有 setAlignment，靠左只有样式表这一条路；这里**只**给
        # 一条 text-align，不碰 border/background，flat 观感由原样式继续负责。
        self.setStyleSheet("text-align: left;")

    def badge_font(self):
        """画角标用的字体：比按钮文字小一号加粗。唯一出口，占位与绘制都从它量宽度
        （两处各量一次迟早对不上，那时就是「角标压住文字」这种看上去像随机的缺陷）。"""
        f = self.font()
        if f.pointSize() > 7:
            f.setPointSize(max(7, f.pointSize() - 2))
        f.setBold(True)
        return f

    def badge_pill_width(self, label=None) -> int:
        label = self.badge_label() if label is None else label
        if not label:
            return 0
        return self.badge_font_metrics().horizontalAdvance(label) + 2 * self.H_PAD

    def badge_font_metrics(self):
        return QFontMetrics(self.badge_font())

    def content_left_inset(self) -> int:
        """按钮文字左边真正吃掉的内边距（问样式，不猜常数）。

        钉宽度时必须把它算进去：靠左对齐之后文字是从 inset 起排的，漏算就等于把角标
        往文字上压。样式各平台不同（macOS/Windows/Fusion 都不一样），写死一个数就等着
        在某个系统上重叠。
        """
        try:
            opt = QStyleOptionButton()
            opt.initFrom(self)
            return max(0, int(self.style().subElementRect(
                QStyle.SE_PushButtonContentRect, opt, self).left()))
        except Exception:
            return 0

    def _worst_badge_width(self) -> int:
        """角标占位：按**最坏形态**（99+）实测，绝不按当前计数算。

        这一步是「几何恒定」的全部实现：占位如果跟着计数走，角标一出现按钮就变宽，
        本类存在的理由就白写了。宽度用画它的那支字体量（见 badge_font），换系统字号
        也照样量得准。
        """
        return (self.badge_pill_width(f"{self.CAP}+")
                + self.EDGE + self.TEXT_MARGIN)

    def badge_display(self) -> str:
        """角标上的计数文本（封顶后写 99+，不写 250+：写具体大数就等于继续刷壁纸）。"""
        n = self._notice_error or self._notice_warn
        if n <= 0:
            return ""
        return f"{self.CAP}+" if n > self.CAP else str(n)

    def badge_tone(self) -> str:
        """角标当前的严重度，返回值**直接用色表的档位名**。

        这一条是被打出来的教训：上一版这里返回 "warn"，而色表里那一格叫 "pause"，
        取色时静默落到默认灰——「有提醒」和「没消息」在角标上变成同一个颜色。两套词汇
        对不上不会报错，只会把信号哑掉，所以这里不给角标另起名字。

        失败优先：⛔ 一出现就得点进去看，提醒只在没有失败时才占角标；提醒沿用色表
        pause 那一格（界面上「要看一眼但不严重」的琥珀色只许有一支）。
        """
        if self._notice_error:
            return "error"
        if self._notice_warn:
            return "pause"
        return ""

    def badge_label(self) -> str:
        """角标上真正画出来的那几个字符。

        **只许是 ASCII 数字**（外加封顶那个 '+'）：这里不放 ⛔/⚠ 这类符号，因为
        角标是 QPainter 直接 drawText 画上去的，走的不是按钮文字那条字体回退链——
        同一枚 U+26D4 在 macOS 可能出彩色 emoji、在 Windows 可能出单色描边、在
        缺字体的机器上就是一个豆腐块，而打包出去的 exe 恰恰最可能落在最后一种机器上。
        一个「看上去像 bug」的方框会把整套告警机制的信用一起带走，而轻重本来就由
        **胶囊底色 + 悬停里的原话**双通道传达，符号在这里只是重复表达一次风险。
        """
        return self.badge_display()

    def badge_colors(self) -> tuple:
        """角标的（底色, 前景色）：单独成方法只为可测——画出来的东西若只能靠肉眼验收，
        离线门禁就永远管不到它（而「画错颜色」和「没画」在截图外是同一种故障）。

        这里刻意**反着取**那张色表：表里「低饱和底 + 同层级前景」是给大面积状态标签
        配的，原样搬到 15px 高的胶囊上，底色和窗口底色根本分不出来（离线出图当场就是
        「只有一个红字、没有角标」——去掉符号之后实心底更是唯一的轻重通道，不能哑）。
        小面积信号一律实心：拿表里那支前景色当底，文字按主题取对比的一侧（浅色主题白
        字压深底，深色主题深字压亮底）。色表仍是一份，只是取的是另一个通道。
        """
        fill = _badge_tone_colors(self.badge_tone() or "muted")[1]
        return (fill, "#ffffff" if not _is_dark_theme() else "#202124")

    def set_notice(self, error: int, warn: int) -> None:
        """更新两档计数；画面真的变了才重绘（挂机刷屏期每行都 update 是白活）。"""
        pair = (max(0, int(error)), max(0, int(warn)))
        if pair == (self._notice_error, self._notice_warn):
            return
        self._notice_error, self._notice_warn = pair
        self.update()

    def pin_texts(self, *texts) -> None:
        """把宽度钉成「这几个候选文案里最宽的一个 + 角标占位」。

        必须在文案切换之前调用一次：之后无论展开/收起、有无角标，布局都拿得到同一个
        下限，不会再出现「状态一变文字就被截」的那条路。
        """
        self._pin_texts = tuple(t for t in texts if t)
        fm = self.fontMetrics()
        w = max([fm.horizontalAdvance(t) for t in self._pin_texts] or [0])
        self.setMinimumWidth(max(1, w + self._worst_badge_width() + self.content_left_inset(),
                                 self.minimumSizeHint().width()))

    def changeEvent(self, ev):
        """字号/字体变了就重新钉一次宽度：钉的是像素数，字体一换那个数就不作数了
        （用户在系统里改字号、深色模式切换都可能带来一次 FontChange）。"""
        super().changeEvent(ev)
        if ev.type() == QEvent.FontChange and self._pin_texts:
            self.pin_texts(*self._pin_texts)

    def paintEvent(self, ev):
        super().paintEvent(ev)
        label = self.badge_label()
        if not label or not self.isEnabled():
            return
        # 文案截断时（窄到连最宽候选都放不下）不再画角标：压着文字画一个胶囊，
        # 比少一个角标更糟——「看不清」和「看不懂」同时发生。
        r = self.rect()
        if r.width() < self.minimumWidth():
            return
        bg, fg = self.badge_colors()
        w = self.badge_pill_width()
        box = QRect(r.right() - self.EDGE - w, (r.height() - self.HEIGHT) // 2,
                    w, self.HEIGHT)
        # 兜底：文字右缘 + 间隙越过角标左缘就不画（窗口被压到地板以下时的事）。
        # 少一个角标只是少个信号，压着字画则是把两个信息一起毁掉，还顺带看着像 bug。
        fm = self.fontMetrics()
        if box.left() < self.content_left_inset() + fm.horizontalAdvance(self.text()):
            return
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing, True)
            p.setFont(self.badge_font())
            h = self.HEIGHT
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(bg))
            p.drawRoundedRect(box, h // 2, h // 2)
            p.setPen(QColor(fg))
            p.drawText(box, Qt.AlignCenter, label)
        finally:
            p.end()


# ── 主窗口 ─────────────────────────────────────────────────────
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        # 标题只写工具名：平台清单在面板「③ 采集平台」里已逐站列出，
        # 塞进标题栏会被系统截断，反而看不清。
        # 档位后缀由 edition 投影：一是让外部用户知道自己拿的是精简版（不制造「功能
        # 怎么都消失了」的错觉），二是让我在验收时能一眼确认「现在跑的确实是丐版」——
        # 没有可辨识标记，就无法证明看到的界面是哪一个档位。
        # 工具名单独存一份：等级变更后界面形态会变（全功能号被降到受限档 = 精简版
        # 那一组能力），标题必须跟着重投影，不能只在构造时定一次就再也不过问。
        self._base_title = "B2B 多平台企业数据自动采集工具"
        self.setWindowTitle(self._base_title + edition.title_suffix())
        # 小屏自适应：初始高度绝不超出可用屏高。写死尺寸在 13 寸笔电上会把
        # 底部日志裁到屏幕外（就是「输出日志看不到、得把窗口拉大」的另一半原因）。
        self.setMinimumSize(880, 620)
        try:
            avail = QApplication.primaryScreen().availableGeometry()
            self.resize(min(1120, max(880, avail.width() - 100)),
                        min(940, max(640, avail.height() - 80)))
        except Exception:
            self.resize(1000, 720)
        self.session = ParallelSession()
        self.worker = None
        self.autosave = None
        self._pending_save = False
        self._opening_browser = False
        self._paused = False
        self.keywords = []
        self._kw_raw = ""        # 用户原始输入（智能展开前），续采与任务快照回溯用
        self._kw_source_file = ""
        self._co_source_file = ""   # 公司名单文件（企知道/风鸟/爱企查补采）
        # 防重复点「打开 XX」：按平台各记一路，三平台并行时可以连着点（后台线程互不干扰）
        self._opening_logins = set()
        self.selected_provinces = []  # 省份精准采集（多选）：[]=全国不限（「选择省份」弹窗设定）
        self._kw_expanded_text = ""  # 上次自动扩词写回的内容（同内容不再二次展开）
        # 「自动扩词」的**生效值**（不是控件状态）：对外档位没有勾选框，读控件会直接
        # AttributeError；把状态存成数据，控件在不在都由同一份判据说话。
        self._auto_expand = edition.auto_expand_default()
        # 后台静默同步数据池的在途状态（只有对外档位会走到，内部版走弹窗）：
        # worker 只允许一个在跑，跑完期间又采集完成时把最新一份挂到 pending 排队。
        self._silent_worker = None
        self._silent_pending = False
        # 当前这一批被提交的指纹：只有上传成功才并进台账，失败的一律留给下次重试
        self._silent_keys_batch = set()
        # 本批结果是否已落账（信号回调与退出等待两处都可能落，谁先到算谁的）
        self._silent_committed = False
        # worker 报告的结果副本（DirectConnection 在上传线程里写）：关闭窗口时排队信号
        # 可能永远不跑，退出路径就靠它知道这批到底传成了什么样；None = 还没结果。
        self._silent_result = None
        self.session_dir = None
        self._first_start = None
        if HAS_YZD:
            _tasks.set_desktop_dir(self._get_desktop_dir())
        self._yzd_phone = _tasks.load_account() if HAS_YZD else ""
        self._yzd_worker = None
        self._yzd_import_worker = None
        self._yzd_dialog = None
        self._yzd_progress = None
        self._yzd_importing = False
        self._yzd_jobs = []
        self._yzd_group_name = ""
        self._auto_import_group = None
        self._task_id = ""
        # 防采/验证码人工提醒弹窗（非模态，全局唯一）：只在被拦时存在，过验后关闭
        self._block_alert = None
        # ── 权限相关的界面状态（能力位→控件的唯一登记表）──
        # 入口的创建由 edition 投影决定；这份表只是记下「哪个入口是哪条能力位画的」，
        # 等级在运行中被管理员改动时才有地方把多余的入口收掉。收不掉的（对话框里的
        # 勾选项、名单通道勾选）由**动作层的运行时复核**兜住，不靠控件可见性讲安全。
        self._acl_gated = {}
        self._acl_caps = {}              # 建界面那一刻的能力位快照
        self._acl_pending = False        # 已收口、等待重启彻底生效（只提示一次）
        self._acl_stopped = False        # 被顶号后已走过优雅停止，不重复触发
        self._acl_restart_asked = False  # 等级变更的重启询问只问一次（不成骚扰）
        self._acl_revoked_box_shown = False   # 顶号模态只弹一次（弹窗期间心跳仍在跑）
        self._relaunch_pending = None    # 落盘链路走完才拉起新实例
        self._build_ui()
        self._acl_caps = edition.caps_snapshot()
        self._connect_signals()
        self._refresh_yzd_status()
        # 自动保存双触发：①每满 AUTOSAVE_EVERY 条（见 _on_progress，采集驱动即时落盘）；
        # ②定时兜底（长时间单关键词深翻页也能按时间保存），另在开始/结束时各存一次。
        self.AUTOSAVE_EVERY = 20
        self._last_autosave_bucket = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.request_autosave)
        self._timer.start(3 * 60 * 1000)
        # 采集计时（对齐企知道/1688 工具）：秒级刷新；验证等待/手动暂停时接管计时，
        # 避免人工等待把平均耗时算虚高。
        self._crawl_timer = QTimer(self)
        self._crawl_timer.timeout.connect(self._update_timer)
        self._crawl_start_ts = 0.0
        self._crawl_elapsed = 0.0
        self._hold_from = 0.0
        self._held_seconds = 0.0
        # 权限心跳：60 秒一次。采集是长任务，用户跑起来就不会再点按钮，只靠点击路径
        # 校验的话，被别的机器顶掉的本机还能继续跑几十分钟——心跳是这件事的保底。
        # 间隔与 access_guard 的缓存 TTL 同源（CACHE_TTL_SECONDS），心跳不会额外出网，
        # 它只是把「本来就该刷新的那次」提前安排下来。
        self._acl_timer = QTimer(self)
        self._acl_timer.timeout.connect(self._acl_heartbeat)
        if HAS_ACL_GUARD:
            self._acl_timer.start(max(15_000, int(_access_guard.CACHE_TTL_SECONDS * 1000)))
            # 心跳只是保底，它交出的顶号事件是**上一轮**刷新落下的：被顶掉的机器最坏
            # 要空跑两拍（120 秒）才停手。刷新一落地就有 refreshed 叫醒界面，把这段
            # 延迟压到零。QueuedConnection 不是可选项：发射点是后台刷新线程（含
            # refresh_blocking 起的线程），而处理体要弹模态框并走停止链路，直连等于把
            # 模态框嵌进别人的调用栈（点击路径就是嵌套事件循环）。
            self._acl_refreshed_slot = self._on_acl_refreshed
            _access_guard.get_guard().refreshed.connect(
                self._acl_refreshed_slot, Qt.QueuedConnection)
            # 开机权威解析（prefetch_authoritative）若恰在「prefetch 超时返回」与
            # 「连接 refreshed 信号」之间才把后台线程收尾并把等级写成 pro，这个事件
            # 会在没人监听时发出——界面就停在按丐版建的形态且不再被叫醒（心跳最快 15s）。
            # 补一次 1.5s 的轻量收口：只比对当前 edition 与已建入口，不额外出网，把这种
            # 「其实已确认、却被建窄了」的情况当场用横幅 + 一键重启补齐完整入口。
            QTimer.singleShot(1500, self._acl_startup_reconcile)
        # 打开工具就接上次的任务：关键词、平台勾选、参数、采到哪儿一并回填（静默）。
        # 延到事件循环开始再做：读回上次数据要一两秒，卡在构造里窗口会先白屏。
        QTimer.singleShot(0, self._restore_last_task)
        # 通道缺失开机播报：② 的标题与 tooltip 要鼠标移过去才看得到，而这次的缺陷形状
        # 恰恰是「静默少功能」——不主动出声，用户就会把它当成权限问题一直查错方向。
        # 只在真缺时出声（正常机器上日志里一条也不会多），不骚扰没碰到这个问题的人。
        QTimer.singleShot(0, self._log_channel_health)
        # 运行形态播报：Mac 的只读转置包（App Translocation）与「刚被重启链路拉起来」
        # 都属于「界面已经开起来了，但用户该知道眼下是什么状态」，见两个方法本身。
        QTimer.singleShot(0, self._announce_relaunch)
        QTimer.singleShot(0, self._warn_read_only_runtime)
        # 窗口最小宽的校准留到首次显示：那时视口宽度才是真的（见 showEvent /
        # _apply_width_floor），构造阶段量出来的是默认尺寸，算出来的闸口是假的。
        self._width_floor_done = False
        # 三个校准状态：base=构造时那个显式下限（允许降回去的位置，首次校准时记下），
        # pending=这一轮重排已经排过队（合并噪声），running=正在自己改下限（别再被自己的
        # 重排回调追尾）。
        self._width_floor_base = None
        self._floor_pending = False
        self._width_floor_running = False

    def showEvent(self, ev):
        """首次显示校准窗口最小宽，并从此盯住内容重排（见 _watch_cfg_layout）。"""
        super().showEvent(ev)
        if not self._width_floor_done:
            self._width_floor_done = True
            self._calibrate_width_floor()
            try:
                self._watch_cfg_layout()
            except Exception:
                pass      # 没挂上监视只是回到「只在首次显示校准一次」，不能挡住窗口显示

    def _calibrate_width_floor(self):
        try:
            self._apply_width_floor()
        except Exception:
            pass          # 校准失败最多回到「窄窗裁切」的老行为，绝不能挡住窗口显示

    def _watch_cfg_layout(self):
        """内容一重排就再校准窗口最小宽，而不是只在首次显示校准那一次。

        为什么必需（2026-11-12 布局门禁「同一份代码两次跑出不同结果」的根因）：界面
        形状不是构造完就定死的——启动回填上次任务、通道缺失告警挂上 ② 标题、等级心跳
        把 pro 入口补齐，这些都发生在首次显示**之后**。地板只按显示那一刻的内容算，之后
        内容变宽，Qt 的行为照旧是裁掉行尾按钮：正是这条路径让 CI 与开发机看到的形状
        不一致，而且没人能复现。列「哪些动作会改形状」迟早漏一条，盯 LayoutRequest
        才是把不变量交给 Qt 自己维护——形状一变，地板一定跟着变。
        """
        targets = [self._cfg_scroll.widget(), self.centralWidget()]
        for wd in targets:
            if wd is not None:
                wd.installEventFilter(_ResizeWatcher(
                    self._schedule_width_floor, self, ev_types=(QEvent.LayoutRequest,)))

    def _schedule_width_floor(self):
        """把这次重排合并进下一轮事件循环：拖窗口时 LayoutRequest 每帧都有，逐条重算
        既没必要（Qt 还没排完）也危险（自己改下限又会引发新一轮重排）。"""
        if self._width_floor_running or self._floor_pending:
            return
        self._floor_pending = True
        QTimer.singleShot(0, self._run_width_floor)

    def _run_width_floor(self):
        self._floor_pending = False
        self._calibrate_width_floor()

    # 余量与噪声门限：SLACK 是字体四舍五入的余量，HYSTERESIS 是「差这么点就别动」——
    # 后者防的是两个取值互相追尾（抬一下又落一下），那会让界面在没人操作时自己抖。
    _WIDTH_FLOOR_SLACK = 2
    _WIDTH_FLOOR_HYSTERESIS = 4

    def _apply_width_floor(self):
        """把窗口最小宽定到「本次真正建出来的配置内容」所需宽度，杜绝窄窗裁切。

        配置区横向滚动是关掉的（不想让「开始采集」这种主按钮要左右拖才看到），而 Qt 在
        widgetResizable + 横向关闭下的行为是「内容按其最小宽排开，视口外的部分直接裁掉」，
        不是自动换行。窗口最小宽过去写死 880，实测 pro + 等级 0 的配置内容最小宽 981：
        窗口落在 880~981 这段时，行尾的「搜索采购商」「权限管理」被裁掉——按钮确实建出来了、
        也确实 isVisible，却看不见也点不到（等级 0 的管理员会直接被自己的面板锁在外面）。

        余量全部**实测**，不拼常数：窗口宽减视口宽就是边距加边框加当前那条竖向滚动条，
        竖向滚动条此刻没出现时再预留一条的宽度（macOS 上它是 overlay，预留了也只是宽几像素，
        反过来漏算就会正好裁掉行尾几像素——「按钮在界面上但差 11px 点不到」是最难复现的
        一类投诉）。档位/等级投影出的控件少，内容最小宽就小（lite 实测 867），丐版用户的
        小窗口依旧合法——判定只看内容量，不写 if lite。

        本方法是**双向**的：内容变窄时地板跟着降回去。只抬不降的做法会让一次偶发的宽内容
        （比如上次任务里那串很长的关键词回显）永久把窗口钉死在很宽，用户想缩小都缩不了。
        """
        sa = getattr(self, "_cfg_scroll", None)
        if sa is None or sa.widget() is None:
            return
        content = max(sa.widget().minimumSizeHint().width(), sa.widget().sizeHint().width())
        if content <= 0:
            return
        if self._width_floor_base is None:
            # 抬之前就记下构造时那个显式下限：它既是「绝不低于」的地板，也是本方法
            # 唯一允许把 minimumWidth 往下放到的位置（不然就会把布局自己的下限也削掉）。
            self._width_floor_base = self.minimumWidth()
        bar = sa.verticalScrollBar()
        reserve = 0 if (sa.verticalScrollBarPolicy() == Qt.ScrollBarAlwaysOff
                          or bar.isVisible()) else bar.sizeHint().width()
        chrome = max(0, self.width() - sa.viewport().width()) + reserve
        need = content + chrome + self._WIDTH_FLOOR_SLACK
        # 常驻区（滚动容器之外的那几行：主动作行、进度条、状态行、日志折叠条）也要算进来。
        # 主动作行移出滚动容器之后，这一步就从「可选优化」变成了「必需」：滚动容器里的
        # 内容挤不下时 Qt 裁掉、常驻行挤不下时 Qt 压扁，而 vertical 布局的最小宽取的是
        # 各行最小宽的最大值，正好就是「整窗口至少要这么宽」这句话。
        try:
            need = max(need, self._root_lay.totalMinimumSize().width()
                       + self._WIDTH_FLOOR_SLACK)
        except Exception:
            pass
        # 上限是可用屏宽：小屏笔电上「窗口最小宽 1128、屏幕只有 1024」会让窗口伸出屏幕，
        # 右侧照样看不见——只是换了个更难查的原因。真装不下时把横向滚动还回去：
        # 宁可要一条看得见的横向滚动条，也不要一个看不见又点不到的按钮。
        cap = int(self._width_floor_cap() or 0)
        fits = not cap or need <= cap
        want_policy = Qt.ScrollBarAlwaysOff if fits else Qt.ScrollBarAsNeeded
        if sa.horizontalScrollBarPolicy() != want_policy:
            sa.setHorizontalScrollBarPolicy(want_policy)
        target = need if fits else max(self._width_floor_base, cap)
        if abs(target - self.minimumWidth()) < self._WIDTH_FLOOR_HYSTERESIS:
            return                                  # 差在噪声范围内就别动界面
        self._width_floor_running = True
        try:
            self.setMinimumWidth(target)
            if self.width() < target:
                self.resize(target, self.height())  # 已经太窄就当场撑开，不留裁切
        finally:
            self._width_floor_running = False

    def _width_floor_cap(self) -> int:
        """最小宽的天花板：可用屏宽。单独成方法只为可测（离线用例顶掉它验退路）。"""
        return QApplication.primaryScreen().availableGeometry().width()

    def _acl_reg(self, cap, *widgets):
        """把「按某条能力位创建的入口」登记进来，供等级变动时收口。

        登记表是必需的而不是多余的：控件一旦建好就再也问不到「当初为什么建它」，
        而在每个 handler 里各写一遍 if 正是这套改造要避免的老路。
        """
        for w in widgets:
            if w is not None:
                self._acl_gated.setdefault(cap, []).append(w)

    def _acl_gate(self, allowed: bool, label: str) -> bool:
        """动作层的权限复核（返回 True 才继续）。

        控件层的「不创建」只在**建界面那一刻**有效：等级是之后才判定/改动的，
        收口也只是 setVisible(False)。凡「建得比判定早」的入口都必须在这里再问一次，
        否则「藏住入口」只做了一半——另一半是：既然能勾上，就得真能跑。
        """
        if allowed:
            return True
        self._on_log(f"ℹ 「{label}」不在当前账号的权限范围内，本次操作已取消"
                     "（权限等级已变更时，重启工具后入口才会消失）")
        return False

    def _get_desktop_dir(self):
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

    # ── 名单补采通道在文字里的口径（标题/tooltip/开机日志共用同一份判据）──────
    # 平台数量是 3，但这里不写死「三平台」这三个字：将来加一路通道时，写死的说法会
    # 当场变成假话，而按数量投影只会跟着变。超出表范围退回阿拉伯数字，宁可土一点也不撒谎。
    _PLATFORM_COUNT_WORDS = {1: "", 2: "两平台", 3: "三平台", 4: "四平台", 5: "五平台"}
    # 缺路告警在标题里的分隔前缀：单独成常量，是为了让「分区标题必须短句」那条回归
    # 断言可以把告警后缀切出去单独看——短句是常态，告警是例外，例外必须能被识别出来，
    # 而不是把长度上限整体放宽（放宽就等于没有约束）。
    LOST_HINT_SEP = "｜⚠ 本机缺："

    def _list_built_slugs(self) -> tuple:
        """界面上**真的有一路可用**的名单通道：勾选框建出来了、也没被等级收掉。
        用 isHidden 而不是 isVisible：后者在父窗口还没 show 时一律返回 False，
        建界面那一刻问它等于什么都问不到。"""
        return tuple(s for s, c in getattr(self, "chk_list", {}).items() if not c.isHidden())

    def _province_tip(self) -> str:
        """「选择省份」的说明。通道清单只从 self._list_* 读，绝不在这另数一遍——
        ① 这里数一遍、② 那里数一遍，就会出现「按钮藏好了、tooltip 还在报内部平台名」。"""
        return ("可选多个省份，只采这些省份的企业数据。默认不选=全国都采。\n"
                "・支持按地区筛选的网站（中国制造网 / 马可波罗 / 1688）：把省份交给网站筛，"
                "采回来的每一条仍然用地址/公司名本地核一遍（网站筛不作为保证）；\n"
                "・其余网站页面上没地区筛选：按页面上写的地区与公司名把关，"
                "不是所选省份的不入库；\n"
                "・页面上与公司名都看不出省份的：不拍脑袋入库，转名单补采通道"
                f"（{self._list_vis_names}"
                + ("，勾了哪路走哪路" if self._list_multi else "")
                + "）按工商登记地址反查一次（在目标省就连联系方式一起补上，外省则丢弃）。\n"
                "支持全选/清空/大区快捷选择，选好点「确定」即可。")

    def _apply_list_channel_hint(self):
        """② 的标题、② 与「选择省份」的 tooltip 一次性按当前状态重拼（唯一出口）。

        两份事实分开说，是这个方法存在的全部理由：
          · 「界面上有几路」——档位投影 + 模块是否加载，共同决定用户能勾到几路；
          · 「本该有几路却没有」——只在档位确实该给时才算发包缺陷（见 _lost_list_channels）。
        以前这两句话写在 _build_ui 的字面量里，于是等级 0 的管理员拿到漏文件的包时，
        界面只显示企知道、也不解释为什么，所有人第一反应都是「我权限不够」。
        """
        built = self._list_built_slugs()
        lost = _lost_list_channels()
        self._list_lost = lost
        self._list_vis_slugs = built
        self._list_vis_names = " / ".join(SITE_NAMES.get(s, s) for s in built)
        self._list_multi = len(built) > 1
        if len(built) > 1:
            lead = self._PLATFORM_COUNT_WORDS.get(len(built)) or f"{len(built)}平台"
        elif built:
            lead = SITE_NAMES.get(built[0], "企知道")
        else:
            lead = "暂无可用平台"
        lost_names = " / ".join(SITE_NAMES.get(s, s) for s in lost)
        gb = getattr(self, "_gb_list", None)
        if gb is not None:
            gb.setTitle("② 公司名单（" + lead + "逐家补采"
                        + ((self.LOST_HINT_SEP + lost_names) if lost else "") + "）")
            gb.setToolTip(
                "可选：上传一份公司名文件（xlsx/csv/txt，有一列是公司名即可），"
                + ("勾选的平台逐家" if self._list_multi else f"由{lead}逐家")
                + "模拟人工查询补采。\n"
                "只选名单、不填关键词也能点「开始采集」——就是「按公司名精准找联系方式」的用法。\n"
                + ("勾了几个平台，同一份名单就每个平台都从第 1 家查到最后 1 家"
                   "（不是几个平台分摊这份名单）；\n" if self._list_multi else "")
                + "采完自动按公司名合并到同一个文件：多个手机号/邮箱等取并集、用英文逗号分隔。\n"
                "开始采集后任务文件夹里会生成「待采集公司名单.xlsx」"
                "（与原数据/清洗入库数据同层级）；\n"
                "关键词采集清洗掉的无联系方式废数据会自动回流进这份名单、被补采（变废为宝）。\n"
                "采过/查不到的公司名会自动剔除，不重复采集。"
                + self._lost_channels_text(lost, lost_names)
                + self._lost_channels_detail(lost))
        btn = getattr(self, "btn_province", None)
        if btn is not None:
            btn.setToolTip(self._province_tip())

    # 缺路说明拆成「三行短句 + 一行细节」：短句进日志，细节只进 tooltip。
    # 排序按「事实 → 原因 → 处置」，处置刻意排在**最后一条**（2026-11-12 自审发现）：
    # 收起态只看得见最后两三行，把结论写在最前面等于把结论藏起来——同一段话在展开态
    # 和收起态会给出两个不同的重点，那比不写还糟。发包方专属的那条命令很长，摊进日志
    # 会把三行短句全顶出可见区，所以它只挂在悬停说明上（该看到的人自然会悬上去看）。
    @staticmethod
    def _lost_channels_errors() -> dict:
        """逐路采集器的加载错误（唯一取法，原因行与细节行共用一份证据）。"""
        if not HAS_ADAPTERS:
            return {}
        try:
            return dict(getattr(_adapters, "ADAPTER_ERRORS", {}) or {})
        except Exception:
            return {}

    @staticmethod
    def _lost_channels_kinds(lost: tuple) -> set:
        """逐路失败的种类（``missing`` / ``broken`` / ``unknown``）。

        判据**不在界面**：这一路到底是「文件没进包」还是「本机加载它时报错」，只有
        site_adapters 知道（它亲手 import 过那些模块），见 ``channel_failure_kind``。
        界面以前自己按异常前缀分类，会把 Mac 实机那种「文件明明在包里、只是只读卷写
        不进去」的 FileNotFoundError 报成「安装包缺文件」——把发包方指到打包清单上去
        查一个根本不缺的文件。
        """
        if not HAS_ADAPTERS:
            return {"unknown"}
        try:
            return set((_adapters.channel_failure_kind(s) or "unknown") for s in lost)
        except Exception:
            return {"unknown"}

    @staticmethod
    def _lost_channels_reason(lost: tuple) -> str:
        """「原因」那一行按**实测到的失败种类**说话，不押宝在某一种猜测上。

        同一条「② 只剩企知道」背后有三种完全不同的成因：采集器 .py 没进包（补
        --add-data）、随包依赖没进（多半补 --hidden-import）、文件都在但初始化抛异常。
        说死其中一种，发包方就会照着错的那句去查；证据不止一种时退回覆盖得下的那句
        ——界面话术的信任是一次性资源，错一次，下次真告警用户也不会当回事。
        逐路的错误原文在悬停说明里（_lost_channels_detail），这里只给一句结论。
        """
        kinds = MainWindow._lost_channels_kinds(lost)
        if len(kinds) == 1 and "missing" in kinds:
            return ("原因：安装包缺了它们运行所需的文件（采集器本身或它的依赖没进包，"
                    "不是账号权限/设置问题，重登录、换账号、重装都不会让它回来）")
        if len(kinds) == 1 and "broken" in kinds:
            return ("原因：文件在包里，但本机加载这些采集器时报错了（多半是磁盘只读、"
                    "目录不可写或缺运行组件，不是账号权限/设置问题；"
                    "具体错误在悬停说明与产物自检报告里）")
        return ("原因：安装包缺了、或本机加载不出这些采集器（不是账号权限/设置问题，"
                "具体原因见产物自检 --doctor 报告）")

    @staticmethod
    def _lost_channels_text(lost: tuple, lost_names: str) -> str:
        """缺路说明（tooltip 与开机日志共用一份话术，免得两处说得不一样）。"""
        if not lost:
            return ""
        return ("\n\n⚠ " + lost_names + " 在你当前账号等级下本该可用，本机却没加载出来"
                "\n⚠ " + MainWindow._lost_channels_reason(lost) +
                "\n⚠ 处置：找发包方要重新打的包（产物自检 --doctor 现在会因缺通道直接失败退出）")

    @staticmethod
    def _lost_channels_detail(lost: tuple) -> str:
        """发包方专属细节（只进 tooltip，不进日志，理由见 _lost_channels_text）。

        这里才放逐路的加载错误原文：它是给打包的人定位用的，不是给使用者读的，
        且长度不可控（异常第一行可能带一长串路径），摊进日志正好会顶掉那三行短句。
        """
        if not lost:
            return ""
        errs = MainWindow._lost_channels_errors()
        lines = [f"　{SITE_NAMES.get(s, s)}：{str(errs.get(s) or '（无加载错误记录）')[:120]}"
                 for s in lost]
        return ("\n各路加载错误：" + "\n".join(lines)
                + "\n发包方自查：在仓库里执行 python .github/scripts/check_packaging_manifest.py"
                "，它会点名三处打包入口各自缺哪一条 --add-data。")

    def _log_channel_health(self):
        """把「该有却没有的补采平台」念一遍（文字与 tooltip 同源，不另写一份）。

        三行短句各占一条日志：收起态看得见最后两三行，而最后一条就是「该怎么办」，
        所以用户不展开也知道这不是权限问题、该找谁。完整细节在 ② 的悬停说明里。
        同一份事实只播一次：开机播一次，等级在运行中变更（_acl_sync_level）再播一次——
        按上次播报的集合去重，免得心跳每 60 秒把同一条警告重复刷屏。
        """
        lost = tuple(getattr(self, "_list_lost", ()) or ())
        if lost == getattr(self, "_list_lost_reported", None):
            return
        self._list_lost_reported = lost
        if not lost:
            return
        names = " / ".join(SITE_NAMES.get(s, s) for s in lost)
        for _ln in self._lost_channels_text(lost, names).strip().splitlines():
            if _ln:
                self._on_log(_ln)

    def _build_ui(self):
        root = QWidget()
        lay = QVBoxLayout(root)
        # 常驻布局留一份引用：日志折叠要按槽位改 stretch（见 _apply_log_area），
        # 每次都去 centralWidget().layout() 绕一圈不如直接攥着源头。
        self._root_lay = lay
        # 统一节奏：分区之间留固定空隙、内边距收紧，避免控件贴边挤成一堆。
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)
        self.setCentralWidget(root)

        # ── 配置区（①关键词 ②名单 ③平台 ④参数 动作按钮 客户库）统一放进可滚动容器 ──
        # 为什么是"滚动"而不是"压缩"（2026-11-11 实测总根因）：本机 2560x1600 Retina
        # 逻辑分辨率 1280x800、可用高度约 775，按屏幕自适应出来的窗口只有 650 上下；
        # 而配置＋状态＋日志各行的自然最小高度合计 909。窗口比内容矮一截时 Qt 不是裁掉
        # 底部，而是把没设硬最小值的控件一路压扁——关键词输入框被压到 10 像素、按钮文字
        # 截成「入关键词文」，正是用户反馈「这块排版不优雅」的账。密集配置面板的业界统一
        # 做法就是让配置区自己滚，把进度条/状态行/输出日志钉在下方常驻：挂机采集时用户
        # 盯的是那三样，绝不能被滚走。
        cfg_body = QWidget()
        cfg = QVBoxLayout(cfg_body)
        cfg.setContentsMargins(0, 0, 0, 0)
        cfg.setSpacing(10)

        # ── 顶部双输入区：左＝关键词（5 平台并行搜索），右＝公司名单（企知道补采）──
        # 名单补采通道在界面上的可见集合由档位投影（对外只留企知道），整段只算这一次：
        # ① 的省份说明文案、② 的按钮与勾选框、日志看板的状态行都读这同一份集合。
        # 各处各自数一遍，迟早会出现「② 藏住了、① 的 tooltip 还在报平台名」这种
        # 文字先交底、按钮白藏的情况。
        # 不显示的通道**不可能被勾上**，而「未勾选＝本轮不跑」是编排层既有语义，
        # 所以这里完全不碰通道注册表与台账：功能一行没删，放开档位即恢复显示。
        _vis_slugs = edition.visible_list_channels(LIST_CHANNEL_SLUGS, QZD_SLUG)
        # 存成属性：校验提示（_on_start）与「选择省份」的 tooltip 都在 ② 定稿之前/之外，
        # 也得报同一份平台名。那里的文案如果各写一遍平台清单，就会出现在丐版里点名
        # 隐藏平台的风险。
        # 定稿在 _apply_list_channel_hint：勾选框建完后按**界面上真正存在的**那几路重算，
        # 等级运行中变更时再调一次，文案就不会停在旧集合上（多路判据、缺路判据同源）。
        self._list_vis_slugs = _vis_slugs
        self._list_vis_names = " / ".join(SITE_NAMES.get(s, s) for s in _vis_slugs)
        self._list_multi = len(_vis_slugs) > 1
        self._list_lost = ()
        top = QHBoxLayout()
        top.setSpacing(10)

        gb_kw = QGroupBox("① 关键词（5 平台并行搜索）")
        gb_kw.setToolTip("上传文档或直接在框里粘贴（点进框按 Ctrl+V），自动识别多个关键词（换行/逗号/顿号分隔）。")
        v = QVBoxLayout(gb_kw)
        v.setSpacing(8)
        # 排版（2026-11-11 按用户实机反馈重排）：这一行只留三个「对关键词文本做动作」的
        # 按钮。「从剪贴板粘贴」去掉了——粘贴本来就是文本框自带的本事（点进去 Ctrl+V），
        # 单占一个按钮位反而让五个词挤在一行，小屏上直接被削成「入关键词文」。
        hr = QHBoxLayout()
        hr.setSpacing(8)
        self.btn_file = QPushButton("导入关键词文件")
        self.btn_file.setToolTip("支持 txt / csv / xlsx / docx / md，自动识别里面的关键词。")
        self.btn_parse = QPushButton("解析关键词")
        self.btn_parse.setToolTip("把框里的词按换行/逗号/顿号拆开去重，算出这次要搜的关键词（不解析也能直接开始采集）。")
        self.btn_smart = QPushButton("智能解析")
        self.btn_smart.setToolTip(
            "把「行业类别名」自动展开成一批「具体产品词」，帮你采到更多数据。\n"
            "例：「制冷与热泵核心部件」→ 制冷压缩机、冷凝器、蒸发器、膨胀阀、热泵机组……\n"
            "纯服务/流程词（研发、咨询、招投标、物流、运维等）会自动忽略，因为站内搜索没有对应商品。\n"
            "注意：解析结果由内置行业知识库生成，仅供参考，不一定完全符合你的需求，请检查后再采集。")
        for _b in (self.btn_file, self.btn_parse, self.btn_smart):
            # 三个按钮按字体实测算出同一个最小宽度：一样宽看着才不散；策略取 Minimum，
            # 意思是「布局你绝不能压得我比这更窄」——文字被截断的老问题就此断根，
            # 而不是靠猜一个像素值（换系统字体又会截）。
            _w = max(_b.fontMetrics().horizontalAdvance("导入关键词文件"),
                     _b.fontMetrics().horizontalAdvance(_b.text())) + 24
            _b.setMinimumWidth(_w)
            _b.setMinimumHeight(30)
            _b.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
            hr.addWidget(_b)
        hr.addStretch(1)
        v.addLayout(hr)
        # 省份精准采集（多选）：和 1688 登录一样点按钮弹窗；默认不选（全国不限）。
        # 「当前范围」与「改范围」是同一件事的两半，所以并到一行：左边一句话读范围，
        # 右边一个按钮改范围；不再像以前那样按钮孤零零吊在关键词框下面找不到归属。
        self.btn_province = QPushButton("选择省份")
        # 第三行列出的是「看不出省份时数据去哪」，通道名必须跟 ② 显示的集合一致：
        # 这里自己写死「企知道 / 风鸟 / 爱企查」，丐版就成了按钮藏好、tooltip 还在
        # 报名字的泄露点。「勾了哪路走哪路」只在真有多路时才有意义。
        # 文案走 _province_tip()：等级在运行中被改动时，这段要和 ② 一起重投影（见
        # _apply_list_channel_hint），否则就是「界面已经变了、tooltip 还在报旧集合」。
        self.btn_province.setToolTip(self._province_tip())
        self.btn_province.setMinimumHeight(30)
        self.btn_province.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.lbl_province = QLabel("🗺 采集范围：全国（不限省份）")
        self.lbl_province.setStyleSheet(_muted_style())
        # 折行是底线：解析完可能拼出「采集范围：广东、江苏、浙江…」长串，不折行会把
        # 窗口最小宽度顶宽，小屏上得横向拖动才能看到「开始采集」。
        self.lbl_province.setWordWrap(True)
        self.lbl_province.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        # 硬底线一行：折行的 QLabel 在窗口偏矮时会被布局一路压到 1 像素高（看不见字），
        # 给个 minimumHeight 就锁死「至少看得见」。
        self.lbl_province.setMinimumHeight(self.lbl_province.fontMetrics().height())
        hrp = QHBoxLayout()
        hrp.setSpacing(8)
        hrp.addWidget(self.lbl_province, 1)
        hrp.addWidget(self.btn_province)
        v.addLayout(hrp)
        self.kw_text = QPlainTextEdit()
        self.kw_text.setPlaceholderText(
            "每行一个关键词（也可用逗号/顿号分隔），例如：太阳能路灯、光伏储能电池、LED显示屏\n"
            "有现成的词表想贴进来：点这个框按 Ctrl+V 就行，贴完点「解析关键词」")
        self.kw_text.setMinimumHeight(76)      # 3 行硬底线：任何窗口高度都不许压成一条缝
        self.kw_text.setMaximumHeight(120)     # 最多 5 行：再高就该用「导入关键词文件」
        v.addWidget(self.kw_text)
        self.lbl_kw = QLabel("识别到 0 个关键词")
        self.lbl_kw.setStyleSheet(_muted_style())
        # 解析完会拼出「识别到 12 个关键词：A、B、C…」长串：不折行就会把窗口
        # 最小宽度顶宽，小屏上直接得横向拖动才能看到开始采集按钮。
        self.lbl_kw.setWordWrap(True)
        self.lbl_kw.setMinimumHeight(self.lbl_kw.fontMetrics().height())
        v.addWidget(self.lbl_kw)
        top.addWidget(gb_kw, 3)

        # 标题口径跟着可见通道走（判据在 _apply_list_channel_hint 里，两处共用一份）：
        # 只剩企知道时还写「三平台」，就是先用文字把隐藏的功能告诉用户，再去藏按钮——顺序错了。
        # 但「只剩企知道」有两种截然不同的原因，必须在标题上分开说：档位本来就不给
        # （对外版，正常）与档位给了、模块却没进包（发包缺陷，异常）。后者不写出来，
        # 用户就只会去查权限——2026-11-12 那次等级 0 只看到企知道，就是这么被搁置的。
        gb_co = QGroupBox("② 公司名单")
        self._gb_list = gb_co
        vc = QVBoxLayout(gb_co)
        hc = QHBoxLayout()
        self.btn_co_file = QPushButton("选择公司名单文件")
        self.btn_co_file.setToolTip("选择需补采的公司名文件（xlsx/csv/txt，自动识别公司名列）。")
        hc.addWidget(self.btn_co_file)
        # 「打开 XX」只给本机真正可用的平台生成按钮：缺采集器的机器上留一个点了必失败的
        # 按钮比少一个按钮更糟（不可用清单由 bind_pending 明确播报，不会静默少跑一路）。
        self.login_buttons = {}
        for _slug in _vis_slugs:
            if _slug not in self.session.channels:
                continue
            _btn = QPushButton(f"打开{SITE_NAMES[_slug]}")
            _btn.setToolTip(f"{SITE_NAMES[_slug]}需登录后才能补采。"
                            f"点此在共享浏览器打开{SITE_NAMES[_slug]}首页，请完成登录。")
            self.login_buttons[_slug] = _btn
            hc.addWidget(_btn)
        hc.addStretch(1)
        vc.addLayout(hc)
        self.lbl_co = QLabel("未选择公司名单（可选：不传也能靠废数据回流自动补采）")
        self.lbl_co.setStyleSheet(_muted_style())
        self.lbl_co.setWordWrap(True)
        self.lbl_co.setMinimumHeight(self.lbl_co.fontMetrics().height())
        vc.addWidget(self.lbl_co)
        # 三路勾选框：企知道默认勾（保持老习惯），风鸟/爱企查默认不勾（按需再开）。
        # 三路跑的是同一份名单，取消勾选只是本轮不再去查，已补到的结果照样进产出文件。
        # 档位不显示的通道**不创建控件**（不是建好再 setVisible(False)）：隐藏控件仍会
        # 留在 Tab 焦点顺序里，用键盘能选中、能空格勾选——那就是没隐藏。
        self.chk_list = {}
        for _slug in _vis_slugs:
            if _slug not in self.session.channels:
                continue
            _name = SITE_NAMES[_slug]
            _chk = QCheckBox(f"{_name}补采" + ("（废数据自动回流·挂机过夜推荐）"
                                             if _slug == QZD_SLUG else "（同步·与企知道同一份名单）"))
            _chk.setChecked(_slug == QZD_SLUG)
            _chk.setToolTip(
                f"勾选后：① 上传的公司名单会由{_name}逐家补采；"
                "② 关键词采集清洗掉的无联系方式废数据自动回流名单并被补采。\n"
                # 机制说明与「③ 采集平台」面板同源（SITE_TIPS 一份文字两处用），
                # 另起一份字典迟早会有一处改了另一处没改。
                + SITE_TIPS.get(_slug, ""))
            self.chk_list[_slug] = _chk
            vc.addWidget(_chk)
        # 历史引用名：界面旧代码与既有回归断言都认 chk_waste（企知道那一路）
        self.chk_waste = self.chk_list.get(QZD_SLUG)
        # 企知道以外那几路是 list_all_channels 画的：等级被下调后要收口的就是它们
        # （企知道两档都有，不属于任何「多余入口」）。
        for _slug, _chk in self.chk_list.items():
            if _slug != QZD_SLUG:
                self._acl_reg(edition.CAP_LIST_ALL_CHANNELS, _chk)
        # 标题与两段 tooltip 在这里一次性定稿：此刻勾选框已建完，「界面上到底有几路」
        # 才是实测值而不是档位推算值（缺模块的那几路压根没建控件）。
        self._apply_list_channel_hint()
        # AlignTop：本区自然高度比 ① 矮（尤其对外档只剩一路勾选框）。不加对齐时
        # QHBoxLayout 会把 ② 一路拉到与 ① 同高，多出来的高度全被那句 wordWrap 说明
        # 文字吃掉——文字占 155 像素高却只写了一行，中间一片空白，看上去像界面坏了。
        # 对齐后 ② 收到自己该有的高度，空白留在两块之外，两档都一样整齐。
        top.addWidget(gb_co, 2, Qt.AlignTop)

        cfg.addLayout(top)

        gb_site = QGroupBox("③ 采集平台（关键词并行）")
        # 多平台合并显示：除 1688 需单独勾选（有登录限制）外，其余站统一归为「多平台」
        _multi_slugs = [s for s in SITE_ORDER if s != "1688"]
        _multi_names = " / ".join(SITE_NAMES.get(s, s) for s in _multi_slugs)
        gb_site.setToolTip(
            f"多平台包含：{_multi_names}，勾选后全部同时启用。\n"
            "任一站点命中防采验证都会弹窗提醒并由看门狗自动续采。\n"
            "八方资源网 / 顺企网 / 爱采购 / 麦麦B2B / 跨国采购网 / 马可波罗 / 世界工厂网 "
            "均为纯 HTTP，不占浏览器标签；\n"
            "中国制造网占一个浏览器标签；1688 需登录，默认不勾选。\n\n"
            + "\n\n".join(SITE_TIPS.get(s, "") for s in SITE_ORDER))
        vs = QVBoxLayout(gb_site)
        gs = QGridLayout()
        gs.setContentsMargins(0, 0, 0, 0)
        self.site_checks = {}
        # 「多平台」复选框：一个视觉 checkbox 控制所有非 1688 站点
        chk_multi = QCheckBox("多平台（8站）")
        chk_multi.setChecked(True)
        chk_multi.setToolTip(f"包含：{_multi_names}\n"
                             "全部为纯 HTTP 采集，不占浏览器标签（被反采质询时临时借标签过盾）。\n"
                             "勾选后 8 个平台同时并行采集。")
        for s in _multi_slugs:
            self.site_checks[s] = chk_multi     # 多个 slug 共享同一 checkbox
        gs.addWidget(chk_multi, 0, 0)
        # 1688 单独勾选（有登录限制，默认不勾）
        if "1688" in [s for s in SITE_ORDER if s in self.site_checks or True]:
            from parallel_session import SITE_CLASSES as _sc
            if "1688" in _sc:
                chk_1688 = QCheckBox("1688")
                chk_1688.setChecked(False)
                chk_1688.setToolTip(SITE_TIPS.get("1688", ""))
                chk_1688.toggled.connect(lambda on: self._on_site_toggled("1688", on))
                self.site_checks["1688"] = chk_1688
                gs.addWidget(chk_1688, 0, 1)
        vs.addLayout(gs)
        cfg.addWidget(gb_site)

        gb_p = QGroupBox("④ 采集参数")
        # 分区标题只留短句，三个开关的机制差异集成交互说明挂在分区本身：
        # 悬停就能看清「勾了会少发多少请求」，不用把长句子平铺到界面上。
        gb_p.setToolTip(
            "采集间隔：相邻请求等待秒数。各站共用同一个出口 IP，挂着过夜建议调到 6~10 秒更安全。\n"
            "翻页一律不限制：一直翻到没有新数据，再自动切下一个关键词。\n"
            "过滤行业不相关商家：拿关键词和公司名+主营比对，明显无关的连详情页都不进（省请求=提速+防封）；"
            "信号取不到时一律放行不误杀。\n"
            "增量模式：跨运行记住已采到/已判不相关的商家，再跑同一批关键词命中即跳过；"
            "因拦截没采成的不记，下轮仍重试。\n"
            "后台运行：开始采集后自动最小化共享 Chrome；命中验证码会自动还原窗口递到眼前，过完再最小化。")
        vp = QVBoxLayout(gb_p)
        hp = QHBoxLayout()
        hp.addWidget(QLabel("采集间隔(秒)："))
        self.spin_delay = QDoubleSpinBox()
        self.spin_delay.setRange(2.0, 60.0); self.spin_delay.setValue(5.0); self.spin_delay.setSingleStep(1.0)
        # 页数不再对外暴露：各平台一律「翻到没新数据为止」（max_pages 固定 0），
        # 旧版那个「不限页数 / 限定时最大页数」开关实际从不关，留着只增加认知负担。
        self.spin_delay.setToolTip("相邻请求的等待秒数。各站共享同一个 IP，挂着过夜建议调到 6~10 秒更安全。\n"
                                   "翻页一律不限制：一直翻到没有新数据再自动切下一个关键词。")
        hp.addWidget(self.spin_delay)
        hp.addSpacing(10)
        self.chk_guard = QCheckBox("过滤行业不相关商家")
        self.chk_guard.setChecked(True)
        self.chk_guard.setToolTip("用关键词与「公司名+主营」做相关性比对，明显无关的连详情页都不进（省请求=提速+防封）。"
                                  "信号取不到时一律放行不误杀。")
        self.chk_incr = QCheckBox("增量模式·跳过已采商家")
        self.chk_incr.setChecked(True)
        self.chk_incr.setToolTip("跨运行记住已采到/已判不相关的商家，再跑同一批关键词命中即跳过、不重复请求"
                                 "（省请求=防封），只补新出现的商家；因拦截没采成的不记，下轮仍重试。")
        self.chk_bg = QCheckBox("后台运行·最小化浏览器")
        # 默认勾选：多平台并行时用户根本不需要盯浏览器，挂着自动最小化才能真「无人值守」；
        # 命中验证码仍会自动还原窗口把验证递到眼前。
        self.chk_bg.setChecked(True)
        self.chk_bg.setToolTip("开始采集后自动最小化共享 Chrome（八方资源网/顺企网/爱采购/麦麦B2B 本就不开标签）。\n"
                               "命中验证码时会自动还原窗口提醒人工过验证，过后重新最小化；\n"
                               "启动浏览器已带防后台节流开关，最小化不影响页面渲染。\n"
                               "注：对启动前已在跑的旧 Chrome 进程无效（开关只能启动时传入），会改用前置窗口方式。")
        hp.addWidget(self.chk_guard); hp.addWidget(self.chk_incr); hp.addWidget(self.chk_bg)
        hp.addStretch(1)
        # 「清空历史种子库」是内部运维入口（一把删光跨运行去重成果，下轮全量重请反而
        # 抬高防封风险）。对外档位不创建该按钮；复位对话框里的同名勾选项一并撤掉——
        # 只藏按钮、留第二条入口，等于没藏。
        self.btn_clear_seen = None
        if edition.clear_seen_visible():
            self.btn_clear_seen = QPushButton("清空历史种子库")
            self.btn_clear_seen.setToolTip("删除各站跨运行去重记录，让下次对同一批关键词从头重采。")
            hp.addWidget(self.btn_clear_seen)
            self._acl_reg(edition.CAP_CLEAR_SEEN, self.btn_clear_seen)
        vp.addLayout(hp)
        # 第二行：自动扩词 + 历史任务（从操作行迁入，和采集参数放一起更合理）
        # 对外档位两个入口都不显示，这时**整行不加入布局**：只把控件藏掉会留下一条
        # 高度空档，用户说不出哪里不对、只会觉得界面莫名其妙空了一块。
        hp2 = QHBoxLayout()
        self.chk_auto_kw = None
        self.btn_history = None
        if edition.auto_expand_visible():
            self.chk_auto_kw = QCheckBox("自动扩词")
            self.chk_auto_kw.setChecked(self._auto_expand)
            self.chk_auto_kw.setToolTip(
                "点「开始采集」时自动把「暖通空调与制冷设备」这类行业类别名展开成一批具体产品词\n"
                "（制冷压缩机、冷凝器、工业冷水机……）。\n"
                "原因：站内搜索只认产品名，类别名不是商品，实测一个词只能翻出十几家还混着无关商家，\n"
                "挂一天也只有几百条。展开后的词会写回关键词框，可随时删改；\n"
                "不想要就取消勾选，完全按你填的词采。")
            # 勾选状态落成数据而不是只存在控件里：续采快照、开始采集前的判据都读它，
            # 档位切换（控件在/不在）不该让这些读取点各写一份兜底。
            self.chk_auto_kw.toggled.connect(self._set_auto_expand)
            hp2.addWidget(self.chk_auto_kw)
            self._acl_reg(edition.CAP_AUTO_EXPAND_UI, self.chk_auto_kw)
        if edition.history_visible():
            self.btn_history = QPushButton("历史任务")
            self.btn_history.setToolTip(
                "列出以前采过的任务：关键词、平台勾选、采集间隔、采到哪儿都记着。\n"
                "选中一条点「接着采」→ 点「开始采集」就从上次停下的地方继续，\n"
                "已采完的关键词自动跳过，数据仍写进同一个文件夹（不会盖掉已采的）。")
            self.btn_history.setMinimumHeight(30)
            if self.chk_auto_kw is not None:
                hp2.addSpacing(16)
            hp2.addWidget(self.btn_history)
            self._acl_reg(edition.CAP_HISTORY_TASKS, self.btn_history)
        if self.chk_auto_kw is not None or self.btn_history is not None:
            hp2.addStretch(1)
            vp.addLayout(hp2)
        cfg.addWidget(gb_p)

        hb = QHBoxLayout()
        self.btn_start = QPushButton("开始采集")
        self.btn_pause = QPushButton("暂停")
        self.btn_stop = QPushButton("停止")
        self.btn_clean = QPushButton("清洗数据")
        self.btn_clean.setToolTip(
            "清洗两种数据之一：\n"
            "① 上传的 Excel/CSV 表格——任何格式都行，表头+内容双路智能匹配列名，\n"
            "   洗成符合入库模版的《清洗后入库数据》；\n"
            "② 本工具已采数据——生成/刷新任务文件夹里的「原始」与「清洗后入库数据」，\n"
            "   无联系方式的废数据回流「待采集公司名单」交企知道补采。")
        self.btn_export = QPushButton("导出 Excel")
        self.btn_reset = QPushButton("开始新任务")
        self.btn_reset.setToolTip(
            "换一批关键词从零重采时用：清空本次已采数据、各站进度账目、进度条/计时/看板，\n"
            "并释放当前数据文件夹——下次点「开始采集」会新建一个以时间戳命名的文件夹保存新数据。\n"
            "复位前先把剩余数据同步落盘（不满一个自动保存批次的尾部也不丢）。"
            # 最后一句在讲复位对话框里的「同时清空历史种子库」勾选项。对外档位没有这个
            # 勾选项（也没有那个按钮），文案却先一步把能力说出来了——入口藏住、话没藏住。
            + ("\n跨运行历史种子库默认保留（省请求=防封），需要连去重记录一起清就在对话框里勾选。"
               if edition.clear_seen_visible() else ""))
        # 主操作唯一化：只有「开始采集」吃强调色，其余保持默认样式（满屏彩色=没有重点）；
        # 按「准备 / 跑 / 产出」三组拉开间距，一眼能看出动作顺序。
        self.btn_start.setObjectName("primary")
        self.btn_start.setMinimumWidth(120)
        for b in (self.btn_start, self.btn_pause, self.btn_stop,
                  self.btn_clean, self.btn_export, self.btn_reset):
            b.setMinimumHeight(36)
        hb.addWidget(self.btn_start)
        hb.addSpacing(14)
        for b in (self.btn_pause, self.btn_stop):
            hb.addWidget(b)
        hb.addSpacing(14)
        hb.addStretch(1)
        for b in (self.btn_clean, self.btn_export, self.btn_reset):
            hb.addWidget(b)
        self.btn_pause.setEnabled(False); self.btn_stop.setEnabled(False)
        # 动作行**不进滚动容器**（2026-11-12 按用户反馈改：「开始采集那些要滑动才能
        # 看到，体验很不好」）。主操作跟着配置一起滚，等于让窗口高度去决定「今天能不能
        # 开工」；密集面板的通行做法正相反——主操作钉在常驻区，只有配置本身允许滚
        # （IDE 的运行条、CI 的提交按钮都在滚动区之外）。这里仍按原顺序把控件建在
        # cfg 之后，只是**添加**改到滚动容器落位之后（见下面 lay.addLayout(hb)）。
        self._action_lay = hb
        # 常驻行的按钮统一钉住「布局真正会让出的宽度」。这一步在「移出滚动容器」之后
        # 是必需的：滚动容器里窗口太窄时 Qt 的行为是**裁掉**视口外的内容（文字始终完整），
        # 而直接挂在窗口上的布局是**压扁**控件（「导出 Exc…」这种截断就是这么来的）。
        # 基线取 minimumSizeHint 而不是只按字体算：Qt 给按钮的默认下限（80）比两个字的
        # 文字宽度大得多，只钉文字宽度等于没钉——实测「暂停」会被削到 57 像素。
        # 最坏形态因此从「主按钮被压扁」变成「右侧次要按钮掉出窗口」，而「开始采集」排在
        # 最左边，永远看得见点得到，与 _apply_width_floor 的降级口径正好配套。
        for _i in range(hb.count()):
            _it = hb.itemAt(_i)
            _wd = _it.widget() if _it is not None else None
            if isinstance(_wd, QPushButton):
                _wd.setMinimumWidth(max(_wd.minimumWidth(), _wd.minimumSizeHint().width(),
                                        _wd.fontMetrics().horizontalAdvance(_wd.text()) + 24))

        # ── 数据池入口（导入历史 + 扫描本地 + 同步 + 搜索）：整组在配置区内，不占常驻行 ──
        # 对外档位整组**不创建**：这四个入口一旦画出来就说不清了——灰着的按钮同样告诉
        # 用户「本来有这件事」，而 Tab 键还能逐个走过去。数据链路本身不关（采集完成后
        # 由 _silent_pool_sync 后台同步），关掉的只是「让人知道有这功能」的那一层。
        # 位置（2026-11-12 按用户反馈「开始采集那些要滑动才能看到」重排）：这四个是
        # 「管已有数据」的后台动作，和「跑本轮」的主动作不是一件事，以前却挤在同一条常驻行里，
        # 把整行的最小宽顶到 1127——比窗口还宽。常驻区一旦放不下，布局只能压扁按钮或把
        # 主按钮挤出屏幕，正是用户看到的「要滑动才能点开始采集」。所以常驻行只留跑批要用的
        # 六个按钮（最小宽 600 上下），数据池搬进可滚动的配置区，和同样要滚的「云展动力
        # 客户库」排在一起：主流程永远一屏可见，次要工具挪到它该在的那一层。
        self.btn_pool_import = None
        self.btn_pool_scan = None
        self.btn_pool_sync = None
        self.btn_pool_search = None
        if edition.pool_ui_visible():
            pool_group = QGroupBox("团队数据池")
            pool_group.setToolTip(
                "团队共享的历史数据池：把以前采过的表格导进来、把本机散落的相关表格扫出来入池、\n"
                "把本次采集结果同步上去，也可以直接按行业搜别人池子里的下游采购商。")
            pl = QHBoxLayout(pool_group)
            pl.setSpacing(8)
            self.btn_pool_import = QPushButton("导入历史数据")
            self.btn_pool_import.setToolTip("选择本地 Excel/CSV 文件，自动转换后批量上传到团队数据池。\n适合把以前采集的大量数据一次性导入，不用就浪费了。")
            self.btn_pool_sync = QPushButton("同步数据池")
            self.btn_pool_sync.setToolTip("把本次采集数据上传到团队共享数据池，供所有人搜索使用。")
            self.btn_pool_search = QPushButton("搜索采购商")
            self.btn_pool_search.setToolTip("从团队数据池智能搜索下游采购商，支持 DeepSeek 语义匹配。")
            self.btn_pool_scan = QPushButton("扫描本地表格")
            self.btn_pool_scan.setToolTip(
                "全盘扫描本机 Excel/CSV，逐个判断能不能入池，出清单给你勾选后再上传。\n"
                "已传过的内容、同系列旧快照、说明文档类表格会自动分开标注。")
            for _b in (self.btn_pool_import, self.btn_pool_scan,
                       self.btn_pool_sync, self.btn_pool_search):
                _b.setMinimumHeight(32)
                _b.setMinimumWidth(max(88, _b.fontMetrics().horizontalAdvance(_b.text()) + 20))
                pl.addWidget(_b)
            if not HAS_POOL:
                self.btn_pool_import.setEnabled(False)
                self.btn_pool_sync.setEnabled(False)
                self.btn_pool_search.setEnabled(False)
                self.btn_pool_scan.setEnabled(False)
            pl.addStretch()
            cfg.addWidget(pool_group)
            self._acl_reg(edition.CAP_POOL_UI, self.btn_pool_import, self.btn_pool_scan,
                          self.btn_pool_sync, self.btn_pool_search)

        self.auto_import_check = QCheckBox("采集完自动导入云展客户库")
        self.auto_import_check.setToolTip(
            "勾选时先让你选一个客户库分组；采集完成后自动把本次数据上传到该分组并在日志提示结果。\n"
            "需先绑定云展动力账号。手动点「停止」不触发自动导入。")
        self.auto_import_check.toggled.connect(self._on_auto_import_toggled)

        # 绑定状态 + 自动导入勾选 + 两个动作合并成一行，不再各占一块（省下一行高度给日志）
        yzd_group = QGroupBox("云展动力客户库")
        # 措辞避开了「历史任务」这四个字：那是对内才有的续采入口（④ 里按档位投影），
        # 而这里讲的是导入弹窗里「挑以前采过的哪几个任务一起导入」。两个功能同名不同事，
        # 用同一个词会让外部用户以为界面还少了一个叫「历史任务」的东西。
        yzd_group.setToolTip(
            "把本次采到的企业按联系人口径清洗后导入云展动力客户库指定分组。\n"
            "需先绑定云展动力账号；手动点「导入客户库」可选分组、也能挑以前采过的任务一起导入，"
            "勾选「采集完自动导入」则先选一个分组、跑完自动上传并在日志提示结果。\n"
            "手动点「停止」不触发自动导入（中途停手的数据不会静默上传）。")
        yl = QHBoxLayout(yzd_group)
        self.yzd_status_lbl = QLabel("未绑定云展动力账号")
        self.yzd_status_lbl.setFont(_yzd_font(10))
        self.yzd_status_lbl.setMinimumWidth(180)
        yl.addWidget(self.yzd_status_lbl)
        yl.addSpacing(12)
        yl.addWidget(self.auto_import_check)
        yl.addStretch()
        self.bind_yzd_btn = QPushButton("绑定账号")
        self.bind_yzd_btn.setMinimumHeight(30)
        self.bind_yzd_btn.setToolTip("绑定云展动力手机号账号（导入客户库前需先绑定）")
        self.bind_yzd_btn.clicked.connect(self._bind_yzd_account)
        yl.addWidget(self.bind_yzd_btn)
        self.import_yzd_btn = QPushButton("导入客户库")
        self.import_yzd_btn.setMinimumHeight(30)
        self.import_yzd_btn.clicked.connect(self._open_yzd_import)
        yl.addWidget(self.import_yzd_btn)
        # ── 管理员专属：本机权限自查 + 权限管理入口 ──
        # 只有「构建 pro 且账号等级 0」才**创建**这两个控件（不是建好再藏）：外部用户
        # 拿到包也翻不出「原来还能管别人」。等级 1/2 的账号界面上不出现任何等级字样——
        # 权限模型是他们看不见的东西，看得见的只有能用的功能。
        # 状态徽章留给管理员是有用处的：改完名单得有个地方一眼确认「本机现在按哪档跑」。
        self.acl_status_lbl = None
        self.btn_acl_admin = None
        if edition.acl_admin_visible():
            self.acl_status_lbl = QLabel("")
            self.acl_status_lbl.setFont(_yzd_font(10))
            self.acl_status_lbl.setStyleSheet(_muted_style())
            yl.addSpacing(12)
            yl.addWidget(self.acl_status_lbl)
            self.btn_acl_admin = QPushButton("权限管理")
            self.btn_acl_admin.setMinimumHeight(30)
            self.btn_acl_admin.setToolTip(
                "查看白名单与每个账号的权限等级、各自绑定在哪台设备上；\n"
                "加账号/改等级/强制解绑（踢下线）。改动写入 OSS 名单，\n"
                "本机立即生效，其他设备的工具最迟 60 秒内生效。")
            self.btn_acl_admin.clicked.connect(self._open_acl_admin)
            yl.addWidget(self.btn_acl_admin)
            self._acl_reg(edition.CAP_ACL_ADMIN, self.btn_acl_admin, self.acl_status_lbl)
        cfg.addWidget(yzd_group)

        # 配置区→滚动容器：窗口不够高时滚配置，绝不把输入框压扁（详见上面 cfg 的注释）。
        # 富余高度全给配置区（日志收起时 stretch=0，见 _apply_log_area）：大屏上配置
        # 一屏多看一行是一行，小屏上日志的收起态也只有两行，两边都不互相抢地方。
        cfg_scroll = QScrollArea()
        cfg_scroll.setWidgetResizable(True)
        cfg_scroll.setFrameShape(QFrame.NoFrame)
        cfg_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # 横向一定不滚，所以配置区的自然宽度就是内容宽度：窗口再窄也只竖着滚，
        # 不会出现「按钮行要左右拖才能看到开始采集」这种二次伤害。
        cfg_scroll.setMinimumHeight(180)       # 配置可视区底线：再矮就只能挪窗口了
        cfg_scroll.setWidget(cfg_body)
        lay.addWidget(cfg_scroll, 3)
        # 主动作行钉在滚动容器之外（见上面 self._action_lay 的注释）：窗口再矮，
        # 「开始采集」也永远在屏幕上，要滚的只有配置本身。
        lay.addLayout(self._action_lay)
        # 配置区滚动容器留一份引用：首次显示时按它的真实视口宽度校准窗口最小宽
        # （见 _apply_width_floor）。这里不直接算，是因为构造阶段视口宽度还不是最终值。
        self._cfg_scroll = cfg_scroll

        self.banner = QLabel("")
        self.banner.setVisible(False)
        self.banner.setWordWrap(True)
        # 横幅要能带一个可点的动作（「重启工具现在生效」这类），所以显式打开链接交互：
        # 后台事件一律不许弹模态（offscreen 直接卡死、正采集时抢焦点），但「用户该做一件事」
        # 的情形又不能只写在日志里等人自己翻——一条带按钮的横幅才是两边都成立的解。
        # Qt5 里没有 Qt::LinksInteractive（那是 Qt6 才加的标志位），等价写法是下面这两个
        # 「可点/可用键盘触发」的链接标志；顺手保留鼠标选中，方便用户把提示原文复制出来问。
        self.banner.setTextInteractionFlags(
            Qt.TextSelectableByMouse
            | Qt.LinksAccessibleByMouse | Qt.LinksAccessibleByKeyboard)
        self.banner.setOpenExternalLinks(False)   # href 以 # 开头，全部走下面的槽
        self.banner.linkActivated.connect(self._on_banner_link)
        lay.addWidget(self.banner)

        prog_row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setMinimumHeight(26)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        # 待机时条内不写字：「就绪」由下面那行状态口径唯一给出。旧版条内、条下各写一个
        # 「就绪」，同一屏两个同样的词，看着就像界面卡住没刷新（离线出图当场看到）。
        self.progress.setFormat("")
        prog_row.addWidget(self.progress, 1)
        self.timer_lbl = QLabel("⏱ 00:00:00")
        f = self.timer_lbl.font(); f.setBold(True); f.setPointSize(11)
        self.timer_lbl.setFont(f)
        prog_row.addWidget(self.timer_lbl)
        lay.addLayout(prog_row)
        self.lbl_stats = QLabel("就绪")
        # 状态行支持富文本：每站按状态加图标+颜色+文字（✅绿已完成 / ■橙已停止 / ●蓝进行中），
        # 三通道编码不只依赖颜色，色觉障碍同样可辨（任务实况仪表惯例）
        self.lbl_stats.setTextFormat(Qt.AutoText)
        lay.addWidget(self.lbl_stats)
        self.lbl_board = QLabel("")
        self.lbl_board.setWordWrap(True)
        self.lbl_save_info = QLabel("")
        # 次要信息（看板明细/保存回显）统一小一号 + muted 色：与主状态行拉开层级，
        # 一进门只需先读「已采多少家」，其余悬停/扫读即可。
        for _lbl in (self.lbl_board, self.lbl_save_info, self.lbl_kw):
            _f = _lbl.font(); _f.setPointSize(10); _lbl.setFont(_f)
        self.lbl_board.setStyleSheet(_muted_style())
        # 空标签在 Qt 里照样占一整行高：未开始采集/未落盘时不该占位。
        # 有内容才显形，把省下的行高全让给输出日志（小屏上日志多一行是一行）。
        self.lbl_board.setVisible(False)
        lay.addWidget(self.lbl_board)
        self.lbl_save_info.setStyleSheet(_muted_style())
        self.lbl_save_info.setVisible(False)
        lay.addWidget(self.lbl_save_info)

        # ── 输出日志：默认收成一条「最近动态」，要看细节时一键展开 ─────────────
        # 为什么默认收起（2026-11-12 用户反馈「一屏显示不全……把日志区域缩小，
        # 正常用户不会经常看日志，太占用屏幕了」）：日志是**偶发**的诊断区，采集是
        # **天天点**的主流程，让偶发用途常驻占掉半屏，等于让所有人为它付高度。
        # 业界同类面板的统一做法就是折叠抽屉：IDE 的输出面板、CI 的构建日志都默认
        # 收起，因为「现在跑到哪了、采了多少家」本来就有常驻的状态行与进度条在讲，
        # 收起日志一条信息都不丢——那两样在这里也确实在滚动区之外常驻着。
        # 收起态仍留两行（不是彻底藏掉）：挂机时扫一眼最后一行有没有 ⚠/⛔ 是刚需。
        self.btn_log_toggle = _NoticeBadgeButton()
        self.btn_log_toggle.setFlat(True)
        self.btn_log_toggle.setCursor(Qt.PointingHandCursor)
        self.btn_log_toggle.setMinimumHeight(22)
        self.btn_log_toggle.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        # 宽度先钉死再画：两种文案谁宽谁窄一次性量定，之后角标出现/消失、展开/收起
        # 都不再改变几何（Maximum 策略允许被削到 sizeHint 以下，不钉住就会截文字）。
        self.btn_log_toggle.pin_texts(self.LOG_TOGGLE_COLLAPSED, self.LOG_TOGGLE_EXPANDED)
        self.btn_log_toggle.setToolTip(
            "展开/收起输出日志。采集进度、各路家数、保存结果都在上面那行常驻显示，"
            "日志里是逐条明细，需要排查时再展开。")
        self.btn_log_toggle.clicked.connect(self._toggle_log_area)
        lay.addWidget(self.btn_log_toggle)

        self.log = QTextEdit(); self.log.setReadOnly(True)
        self.log.setFont(QFont("Menlo", 10) if sys.platform != "win32" else QFont("Consolas", 9))
        # 性能（同事反馈挂机后电脑卡）：过夜采集日志能刷上万行，QTextEdit 无上限
        # 累积会让文档越来越大、每次 append 重排越来越慢、内存持续上涨。
        # 设最大块数：只保留最近 N 行，超出自动从头丢弃，渲染/内存恒定。
        self.log.document().setMaximumBlockCount(3000)
        lay.addWidget(self.log, 0)
        # 日志在常驻布局里的槽位下标：折叠时要把它的 stretch 归零，靠下标改最稳
        # （按 widget 找要遍历布局，按名字找更是把布局结构焊死在字符串里）。
        self._log_index = lay.indexOf(self.log)
        # 折叠态在这里一次性定稿（含从偏好恢复「上次是展开还是收起」）：
        # 高度、stretch、按钮文字三件事必须同一处生效，分开写迟早会有一处改了另一处没改。
        self._apply_log_area(_load_log_expanded_pref())

    # ── 输出日志折叠抽屉 ─────────────────────────────────────────────
    LOG_COLLAPSED_HEIGHT = 60       # 收起态约两行：够扫一眼最后一行有没有 ⚠/⛔
    LOG_EXPANDED_MIN_HEIGHT = 150   # 展开态硬底线：沿用「日志不许被压成一条缝」的旧口径
    _WIDGET_SIZE_MAX = 16777215     # Qt 的 QWIDGETSIZE_MAX，写常量免得依赖导入顺序

    def _apply_log_area(self, expanded: bool):
        """把日志区按收起/展开定稿：高度上下限、布局 stretch、按钮文字一次改齐。

        三件事必须同时改，只改高度不改 stretch 的话，大屏上日志会一边被顶到最大
        一边说「我只要两行」——那是同一条口径写两处的必然结果。
        """
        self._log_expanded = bool(expanded)
        if self._log_expanded:
            self.log.setMinimumHeight(self.LOG_EXPANDED_MIN_HEIGHT)
            self.log.setMaximumHeight(self._WIDGET_SIZE_MAX)
        else:
            self.log.setMinimumHeight(self.LOG_COLLAPSED_HEIGHT)
            self.log.setMaximumHeight(self.LOG_COLLAPSED_HEIGHT)
        # 收起时把富余高度整份让给配置区（日志 stretch 归零）：小屏上配置少滚一点，
        # 大屏上关键词框自然长高，两种窗口都各得其所。
        try:
            self._root_lay.setStretch(self._log_index, 1 if self._log_expanded else 0)
        except Exception:
            pass
        if self._log_expanded:
            # 展开就是「我已经看过了」：两档计数一起归零，收起态才需要角标
            self._log_notice_error = self._log_notice_warn = 0
        # 按钮文字的唯一出口（折叠态与条数在这里一起算，不在上面再拼一遍）
        self._refresh_log_toggle()

    # 折叠条文案的唯一出处：两个状态各一句，**恒定不带计数**（计数走角标绘制）。
    # 写成常量而不是就地拼串，是因为 pin_texts 要在画之前用同一句量宽度——文案若在
    # 两处各写一遍，钉住的宽度迟早和真实文字对不上，那就是又回到「状态一变文字被截」。
    LOG_TOGGLE_COLLAPSED = "▸ 展开日志"
    LOG_TOGGLE_EXPANDED = "▾ 收起日志"

    # 收起态里滚过去的告警行要计入角标。按严重度分两档计数，而不是揉成一个数字：
    # 挂机一晚，请求失败、限流提示这类 ⚠ 能刷上千行，和 ⛔「本轮结束/权限被中止」混在
    # 同一个计数里，真正的失败就被常规噪音冲成壁纸——那是过度告警的经典死法（角标一旦
    # 常年很大就没人看）。分开计数才能做到「数字很大也不慌，⛔ 一出现就得点」。
    _LOG_NOTICE_ERROR_MARKS = ("⛔", "❌", "🚫")
    _LOG_NOTICE_WARN_MARKS = ("⚠",)
    # 显示封顶：与角标类同一个数（真源在 _NoticeBadgeButton.CAP，这里只引用不再写一遍）。
    _LOG_NOTICE_CAP = _NoticeBadgeButton.CAP

    def _notice_display(self, n: int) -> str:
        # 封顶显示的是「上限+」而不是「原始数+」：写 250+ 就把封顶这件事抵消了——
        # 用户看到的还是一个吓人的具体数字，壁纸效应照旧。
        return f"{self._LOG_NOTICE_CAP}+" if n > self._LOG_NOTICE_CAP else str(n)

    def _refresh_log_toggle(self):
        """折叠条：文字恒定，未读告警按严重度画成角标，细节全在悬停说明里。

        折叠抽屉的固有代价是「看不见就等于没有」——业界（IDE 问题面板、浏览器控制台）
        的统一解法就是给收起的面板挂计数，并且**按错误/警告分档、错误用红色**：收起的是
        内容，不收起的是「有事发生」，而「有事」还得能一眼分出轻重。
        """
        if getattr(self, "btn_log_toggle", None) is None:
            return
        err = getattr(self, "_log_notice_error", 0)
        warn = getattr(self, "_log_notice_warn", 0)
        if self._log_expanded:
            self.btn_log_toggle.setText(self.LOG_TOGGLE_EXPANDED)
            self.btn_log_toggle.set_notice(0, 0)
            return
        self.btn_log_toggle.setText(self.LOG_TOGGLE_COLLAPSED)
        self.btn_log_toggle.set_notice(err, warn)
        base = ("展开/收起输出日志。采集进度、各路家数、保存结果都在上面那行常驻显示，"
                "日志里是逐条明细，需要排查时再展开。")
        tiers = []
        if err:
            tiers.append(f"{self._notice_display(err)} 条失败（⛔）")
        if warn:
            tiers.append(f"{self._notice_display(warn)} 条提醒（⚠）")
        if tiers:
            # 两档都在悬停里报全：角标只能显示一个，另一档的信息绝不能因此丢掉
            base += ("\n收起期间累计：" + "、".join(tiers)
                     + "——角标只报最重的那一档，两个数都在这里")
        last = getattr(self, "_log_notice_last", "")
        self.btn_log_toggle.setToolTip(base + (f"\n最近一条：{last}" if last else ""))

    def _count_log_notice(self, msg):
        """收起态里滚过去的告警行计入角标（展开态不需要提醒，用户正在看）。"""
        if getattr(self, "btn_log_toggle", None) is None or self._log_expanded:
            return
        # 只看行首（去掉缩进）：正文里偶然出现的符号（清洗统计引用的原文、多行明细的
        # 中间行）不该算成一条提醒；行首才是爬虫约定打标记的位置。
        head = str(msg).lstrip()
        if head.startswith(self._LOG_NOTICE_ERROR_MARKS):
            key = "_log_notice_error"
        elif head.startswith(self._LOG_NOTICE_WARN_MARKS):
            key = "_log_notice_warn"
        else:
            return
        n = getattr(self, key, 0) + 1
        setattr(self, key, n)
        self._log_notice_last = head.strip()[:200]
        # 封顶之后角标不再变化，也就不必再动界面（与上面日志只留 3000 行同一份挂机性能
        # 账）。取舍写明白：悬停里的「最近一条」会停在第 100 条——刷屏期没人悬停，
        # 而每刷一行都改一次界面才是真问题。
        if n <= self._LOG_NOTICE_CAP + 1:
            self._refresh_log_toggle()

    def _toggle_log_area(self):
        """点一下换一档，并把这一档记下来：下次开工具就是用户上次要的样子。"""
        self._apply_log_area(not self._log_expanded)
        _save_log_expanded_pref(self._log_expanded)

    def _connect_signals(self):
        s = self.session.signals
        s.log_signal.connect(self._on_log)
        s.progress_signal.connect(self._on_progress)
        s.kw_progress_signal.connect(self._on_kw_progress)
        s.stats_signal.connect(self._render_stats)
        s.crawl_finished_signal.connect(self._on_finished)
        s.site_finished_signal.connect(lambda name: self._on_log(f"🏁 {name} 采集完成"))
        s.blocked_signal.connect(self._on_blocked)
        s.block_cleared_signal.connect(self._on_block_cleared)
        if hasattr(s, "login_required_signal"):
            s.login_required_signal.connect(lambda m: self._on_log("🔐 " + m))
        self.btn_file.clicked.connect(self._on_import_file)
        self.btn_parse.clicked.connect(self._on_parse)
        self.btn_smart.clicked.connect(self._on_smart_parse)
        self.btn_province.clicked.connect(self._on_pick_province)
        # 入口控件按档位创建，这里（以及后面所有使用点）只问「控件在不在」。
        # 分工是刻意的：**创建点问档位，使用点问对象**——这样以后再加一档、或某个
        # 入口换名字，使用点不会因为记错档位判据而漏改（漏改的表现是启动即崩）。
        if self.btn_history is not None:
            self.btn_history.clicked.connect(self._on_history_tasks)
        self.btn_co_file.clicked.connect(self._on_import_company_file)
        # 三路补采平台的「打开 XX」按钮逐个接线（本机可用的才有按钮，也就不会点到 None）
        for _slug, _btn in self.login_buttons.items():
            _btn.clicked.connect(lambda _c=False, s=_slug: self._open_site_login(s))
        self.btn_start.clicked.connect(self._on_start)
        self.btn_pause.clicked.connect(self._on_pause)
        self.btn_stop.clicked.connect(self._on_stop)
        self.btn_clean.clicked.connect(self._on_clean_now)
        self.btn_export.clicked.connect(self._on_export)
        if self.btn_clear_seen is not None:
            self.btn_clear_seen.clicked.connect(self._on_clear_seen_store)
        self.btn_reset.clicked.connect(self._on_reset_task)
        if HAS_POOL and self.btn_pool_import is not None:
            self.btn_pool_import.clicked.connect(self._on_pool_import)
            self.btn_pool_scan.clicked.connect(self._on_pool_scan)
            self.btn_pool_sync.clicked.connect(self._on_pool_sync)
            self.btn_pool_search.clicked.connect(self._on_pool_search)

    def _on_log(self, msg):
        self.log.append(msg)
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())
        # 日志被折起来时，告警行不能跟着一起折没（见 _count_log_notice）
        self._count_log_notice(msg)

    # ── 进度条 / 计时 / 看板 ──────────────────────────────
    @staticmethod
    def _format_time(seconds):
        seconds = int(max(0, seconds))
        return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"

    def _update_bar(self, prefix=None):
        pct = getattr(self, "_bar_pct", 0)
        cur = getattr(self, "_bar_companies", 0)
        self.progress.setValue(pct)
        if prefix is None:
            # 纯名单模式（没关键词、只勾了补采平台）时写「采集中」用户会以为在搜关键词，
            # 进度也确实一直不动（2026-11-11 实机反馈「跑了 14 分钟还是 0%」）：
            # 名字按本轮实际干的活来，数字改由三路台账驱动（见 session.list_progress）。
            prefix = "名单补采中" if getattr(self, "_bar_list_only", False) else "采集中"
        unit = ""
        if getattr(self, "_bar_list_on", False):
            try:
                ld, lt = self.session.list_progress()
            except Exception:
                ld = lt = 0
            if lt:
                # 「家次」＝家数 × 路数：三路共用同一份名单，每一路都要从第 1 家查到
                # 最后一家，只看「已采多少家」看不出这一路其实才起步。
                unit = f"｜名单 {ld}/{lt} 家次"
        # 时间只给右侧独立计时器显示：旧版进度条文本里也嵌一份⏱，同一屏出现两个
        # 不同数值的时间（一个是累计耗时、一个是刷新时刻），看着像数据对不上。
        self.progress.setFormat(f"{prefix} {pct}%｜已采 {cur} 家{unit}")

    def _on_kw_progress(self, _slug, done_units, total_units):
        self._bar_pct = int(done_units / total_units * 100) if total_units else 0
        self._update_bar()

    def _refresh_board(self):
        """量化看板压成**一行**总计（成功/无联系方式/失败可重采/早过滤/详情不相关），
        逐站明细移到本行 tooltip 悬停可查。旧版每站一行共 5 行、且写死深色文字，
        在深色系统上整块隐形却照样占位，把输出日志挤到屏幕外。"""
        try:
            stats = self.session.site_stats()
        except Exception:
            return
        if not stats:
            return
        keys = ("got", "ok", "pages", "listed", "no_contact", "failed", "early", "filtered",
                "pf_miss", "pf_unknown")
        tot = {k: sum(s.get(k, 0) for s in stats.values()) for k in keys}
        pf = (f"｜省份把关 外省 {tot['pf_miss']}·待核实 {tot['pf_unknown']}"
              if (tot['pf_miss'] or tot['pf_unknown']) else "")
        self.lbl_board.setText(
            f"明细：成功 {tot['ok']}｜无联系方式 {tot['no_contact']}｜失败可重采 {tot['failed']}｜"
            f"早过滤 {tot['early']}｜详情不相关 {tot['filtered']}｜翻 {tot['pages']} 页见 {tot['listed']} 条{pf}")
        self.lbl_board.setToolTip("\n".join(
            f"{name}：采到 {s.get('got', 0)} 家（成功 {s.get('ok', 0)}）｜翻 {s.get('pages', 0)} 页见 "
            f"{s.get('listed', 0)} 条｜无联系方式 {s.get('no_contact', 0)}｜失败/可重采 "
            f"{s.get('failed', 0)}｜早过滤 {s.get('early', 0)}｜详情不相关 {s.get('filtered', 0)}｜"
            f"省份把关：外省 {s.get('pf_miss', 0)}·属地未标注转名单补采 {s.get('pf_unknown', 0)}"
            for name, s in stats.items()))
        self.lbl_board.setVisible(True)

    def _render_stats(self, text, site_status=None):
        """状态行渲染：交给纯函数 build_stats_html（图标+颜色双通道），降级纯文本不报错。"""
        self.lbl_stats.setText(build_stats_html(text, site_status))

    def _on_progress(self, cur, _total):
        self._bar_companies = cur
        # 采集驱动自动保存：每满 AUTOSAVE_EVERY 条触发一次（与 3 分钟定时兜底双触发）
        bucket = cur // self.AUTOSAVE_EVERY
        if bucket > self._last_autosave_bucket:
            self._last_autosave_bucket = bucket
            self.request_autosave()
        self._update_bar()
        self._refresh_board()

    def _show_banner(self, text, bg, fg):
        self.banner.setText(text)
        self.banner.setStyleSheet(f"background:{bg};color:{fg};padding:8px;border-radius:4px;font-weight:bold;")
        self.banner.setVisible(True)

    def _hide_banner(self):
        self.banner.setVisible(False)

    def _on_banner_link(self, link: str):
        """横幅里那条链接的唯一入口：以 # 开头的动作都在这里分派。

        做成「用户点一下才发生」而不是「后台自己弹」，是为了同时满足两件事：
        提示必须在场（不然没人知道要重启），打断必须零次（不然正跑的任务被 modal
        事件循环卡在中间，offscreen 测试还会直接永久挂住）。
        """
        if link == "#acl-restart":
            self._acl_prompt_restart()
        elif link == "#app-relocate":
            self._offer_app_relocation()
        # 其余未知动作静默忽略：横幅文案由别处拼装，将来加新链接不该在这里炸界面

    def _on_blocked(self, reason):
        """被拦：醒目横幅 + 独立弹窗提醒 + 接管计时（等待不计入采集耗时）。

        两类拦截必须分开说（客户原话：八方出验证码却找不到入口，只能在你设计的软件点确认）：
        · 网站拒这台电脑的上网地址（IP 限流，八方 502 那种）：页面里根本没滑块，工具
          也没开浏览器，所以绝不能再写「已在 Chrome 里打开对应页面」——那句谎话就是
          用户满屏找入口的原因；也不去还原窗口（没东西可看）；
        · 真要人过验证（1688 滑块那种）：才提「去 Chrome 里把滑块拖完」。
        两类都给一个「我换好网络了，马上继续」按钮：换出口 IP 是前者唯一立刻有效的招，
        而这一下只能人动手，点完工具自己重测，不用干等下一轮探测。
        分型依据是爬虫在 reason 里打的前缀标记，界面不再靠猜文案关键字。"""
        kind, name, detail = self._split_block(reason)
        self._block_site = name
        label = name or "这个网站"
        if kind == "ip":
            self._show_banner(
                f"⚠ {label}：暂时拦住了你这台电脑的上网地址（不是验证码，不需要你操作）。"
                f"换个网络（比如连手机热点）后，点弹窗里的「我换好网络了，马上继续」就接着采。",
                "#fff3cd", "#664d03")
        else:
            self._show_banner(
                f"⚠ {label}：需要你亲手过一下验证——到 Chrome 窗口把滑块拖完，过完自动接着采；"
                f"没看到网页就点弹窗里的「打开网站让我看一眼」。",
                "#f8d7da", "#721c24")
        self._show_block_alert(kind, name, detail)
        self._hold_timer(True)
        if kind != "ip" and self.chk_bg.isChecked():
            threading.Thread(target=lambda: self.session.set_browser_background(False),
                             daemon=True).start()

    @staticmethod
    def _split_block(reason):
        """把爬虫发出的拦截文本拆成（类型, 站点名, 原因）。
        没有类型标记的（第三方适配器旧文案）按可人工过验证处理——宁可多叫一次人，
        也不要反过来让人对着没滑块的页面干找。"""
        reason = reason or ""
        if reason.startswith(BLOCK_TAG_IP):
            kind, body = "ip", reason[len(BLOCK_TAG_IP):]
        elif reason.startswith(BLOCK_TAG_CAP):
            kind, body = "captcha", reason[len(BLOCK_TAG_CAP):]
        else:
            kind, body = "captcha", reason
        name, sep, detail = body.partition("：")
        # 只有“冒号前短得像个站名”时才拿它当站点名（1688 等旧适配器直接 emit 整句
        # 拦截描述，里头的冒号是句子标点，当成站名会把同一句话重复显示两遍）。
        if sep and 0 < len(name) <= 12:
            return kind, name, detail
        return kind, "", body

    def _show_block_alert(self, kind, name, detail):
        """拦截提醒弹窗：非模态（模态会阻塞主窗口事件循环，用户反而不能操作），
        全局只留一个实例，多站同时被拦时只更新文案不叠窗。

        文案按拦截类型分开写，而且写成没技术背景的人也看得懂的大白话：
        只讲「发生了什么 + 要不要你动手 + 动手就点哪个按钮」，
        不堆「IP 级限流/退避/熔断」这类只有开发者看得懂的词——
        旧那句「已在 Chrome 里打开对应页面」对拦 IP 这一类根本不成立，
        就是客户「找不到入口、只能在你软件里点确认」的根源。
        两个按钮都由用户主动点：「我换好网络了，马上继续」与「打开网站让我看一眼」。"""
        ip_mode = kind == "ip"
        label = name or "这个网站"
        box = getattr(self, "_block_alert", None)
        if box is None:
            box = QMessageBox(self)
            box.setStandardButtons(QMessageBox.Ok)
            box.setModal(False)
            box.button(QMessageBox.Ok).setText("知道了，先等一等")
            b_retry = box.addButton("▶ 我换好网络了，马上继续", QMessageBox.ActionRole)
            b_retry.clicked.connect(self._on_block_retry_now)
            b_check = box.addButton("🌐 打开网站让我看一眼", QMessageBox.ActionRole)
            b_check.clicked.connect(self._on_block_selfcheck)
            self._block_retry_btn = b_retry
            self._block_check_btn = b_check
            self._block_alert = box
        box.setWindowTitle("网站暂时拦了一下（不需要你操作）" if ip_mode else "需要你亲手过一下验证")
        box.setIcon(QMessageBox.Information if ip_mode else QMessageBox.Warning)
        if ip_mode:
            box.setText(
                f"⚠ {label}：这个网站暂时不肯让你这台电脑访问了。\n"
                f"（网站那边回的一句话：{detail}）\n\n"
                "「重点：这不是验证码」\n"
                "页面里没有滑块要拖，也不用输文字，\n"
                "所以你不用找入口，也不用回到这里点任何确认。\n\n"
                "为什么会这样：网站觉得你这台电脑来得太快，把这个「上网地址」\n"
                "暂时拦了。只要换一个新的上网地址，它立刻就放行。\n\n"
                "照这两步做（最快，两分钟就好）：\n"
                "第 1 步：给电脑换个上网方式\n"
                "    ・手机上打开「个人热点」，让电脑连上手机热点；\n"
                "    ・或者把家里路由器拔电、数 60 秒再插上。\n"
                "第 2 步：点下面这个按钮「我换好网络了，马上继续」\n"
                "    工具会自己马上再试一次，通了就接着往下采，不用你管。\n\n"
                "已经采到的数据都存在电脑上，一条不会丢，接着往下采就行。\n"
                "不想换网络也没关系：什么都不用点，等 30 分钟它一般会自己放行。")
        else:
            box.setText(
                f"⚠ {label}：这个网站要你亲手过一下验证。\n"
                f"（网站那边回的一句话：{detail}）\n\n"
                "怎么做：\n"
                "第 1 步：看电脑上的 Chrome 浏览器窗口，里面应该已经开着这个网站；\n"
                "    ・有滑块 / 有图片要点 → 照着拖完点完（和平时上网一样）；\n"
                "    ・没看到 Chrome 窗口 → 点下面「打开网站让我看一眼」。\n"
                "第 2 步：过完验证什么都不用点，工具自己会发现能采了，接着往下采。\n\n"
                "注意：如果网页能正常打开、根本没有滑块要拖，\n"
                "那就不是验证码，而是网站暂时拦了这个上网地址——\n"
                "碰到那种情况：换个网络（连手机热点）后点「我换好网络了，马上继续」。\n\n"
                "已经采到的数据都存着，不会丢；关掉本窗口也不会停止等待。")
        box.show()
        box.raise_()
        box.activateWindow()

    def _run_block_action(self, btn, method_name, busy_msg):
        """拦截弹窗里两个按钮共用的后台执行器：都是可能拉浏览器/发网络请求的活，
        在主线程里直接做会把整个窗口卡成「未响应」（同类老坑），所以走线程 + 轮询恢复按钮。"""
        if getattr(self, "_opening_check", False):
            return
        name = getattr(self, "_block_site", "")
        self._opening_check = True
        if btn is not None:
            btn.setEnabled(False)
        self._on_log(busy_msg)

        def task():
            try:
                ok, msg = getattr(self.session, method_name)(name)
                self.session._log(("✅ " if ok else "⚠ ") + msg)
            except Exception as e:
                self.session._log(f"⚠ 操作失败：{str(e).splitlines()[0][:120]}")
            finally:
                self._opening_check = False

        threading.Thread(target=task, daemon=True).start()

        def poll():
            if getattr(self, "_opening_check", False):
                QTimer.singleShot(400, poll)
            elif btn is not None:
                btn.setEnabled(True)
        QTimer.singleShot(400, poll)

    def _on_block_selfcheck(self):
        """「打开网站让我看一眼」：用户主动点才去拉/前置 Chrome 窗口（与自动拉窗相对）。"""
        self._run_block_action(getattr(self, "_block_check_btn", None), "open_site_check",
                               "正在 Chrome 里打开这个网站的页面（首次会拉起共享浏览器）…")

    def _on_block_retry_now(self):
        """「我换好网络了，马上继续」：他刚换了出口 IP，让工具不等退避立即重测。
        这一步对不懂技术的人就是「点一下」：被拦计数与降速都会清零，不需要重启工具。"""
        self._run_block_action(getattr(self, "_block_retry_btn", None), "retry_site_now",
                               "正在让网站马上再试一次（已重置本站被拦计数与采集间隔）…")

    def _close_block_alert(self):
        box = getattr(self, "_block_alert", None)
        if box is not None:
            box.hide()
            box.deleteLater()
            self._block_alert = None

    def _on_block_cleared(self, platform):
        self._hide_banner()
        self._close_block_alert()
        self._hold_timer(False)
        if self.chk_bg.isChecked():
            threading.Thread(target=lambda: self.session.set_browser_background(True),
                             daemon=True).start()

    # ── 采集计时 ──────────────────────────────────────
    def _update_timer(self):
        if self._crawl_start_ts:
            self._crawl_elapsed = time.time() - self._crawl_start_ts - self._held_seconds
        self.timer_lbl.setText(f"⏱ {self._format_time(self._crawl_elapsed)}")
        self._update_bar()

    def _on_pick_province(self):
        """「选择省份」弹窗（多选）：选完回显到 lbl_province（一眼看清本次采哪些省）。"""
        dlg = ProvincePickerDialog(self.selected_provinces, self)
        if dlg.exec_() != QDialog.Accepted:
            return
        self.selected_provinces = dlg.chosen_list
        self._refresh_province_label()
        if self.selected_provinces:
            names = "、".join(self.selected_provinces)
            self._on_log(f"🗺 采集范围已设为「{names}」（{len(self.selected_provinces)} 个省）："
                         "开始采集后每个平台都会按这些省份把关")
        else:
            self._on_log("🗺 采集范围已改回「全国」：开始采集后不限省份")

    def _refresh_province_label(self):
        if not self.selected_provinces:
            self.lbl_province.setText("🗺 采集范围：全国（不限省份）")
        elif len(self.selected_provinces) <= 3:
            names = "、".join(self.selected_provinces)
            self.lbl_province.setText(f"🗺 采集范围：{names}（只采这些省份的企业）")
        else:
            names = "、".join(self.selected_provinces[:3])
            self.lbl_province.setText(
                f"🗺 采集范围：已选 {len(self.selected_provinces)} 个省（{names}…）")

    def _start_timer(self):
        self._crawl_start_ts = time.time()
        self._crawl_elapsed = 0.0
        self._hold_from = 0.0
        self._held_seconds = 0.0
        self._crawl_timer.start(1000)

    def _stop_timer(self):
        self._crawl_timer.stop()
        self._update_timer()

    def _hold_timer(self, on):
        """验证等待/手动暂停期间接管计时：暂停段不计入采集耗时（否则人工等待
        会把平均耗时算虚高，与企知道工具同口径）。"""
        if not hasattr(self, "_held_seconds"):
            self._held_seconds = 0.0
        if on:
            if not self._hold_from:
                self._hold_from = time.time()
        elif self._hold_from:
            self._held_seconds += time.time() - self._hold_from
            self._hold_from = 0.0

    def _enabled_sites(self):
        return [slug for slug in SITE_ORDER if self.site_checks[slug].isChecked()]

    # ── 关键词 ─────────────────────────────────────────────────
    def _on_import_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择关键词文件", "", "支持的文件 (*.txt *.csv *.xlsx *.xlsm *.docx *.md);;所有文件 (*)")
        if not path:
            return
        try:
            text = read_keywords_from_file(path)
            self.kw_text.setPlainText(text)
            self._kw_source_file = path
            self._on_parse()
            self._on_log(f"已导入文件：{path}")
        except Exception as e:
            QMessageBox.warning(self, "导入失败", f"读取文件出错：{e}")

    def _on_parse(self):
        self._kw_raw = self.kw_text.toPlainText()
        _junk = []
        self.keywords = ParallelSession.parse_keywords(self._kw_raw, junk_out=_junk)
        self.lbl_kw.setText(f"识别到 {len(self.keywords)} 个关键词：" +
                            ("、".join(self.keywords[:12]) + ("…" if len(self.keywords) > 12 else "")
                             if self.keywords else "无"))
        if _junk:
            show = "、".join(k for k, _ in _junk[:6]) + ("…" if len(_junk) > 6 else "")
            self._on_log(
                f"🧹 已自动剔掉 {len(_junk)} 个不是关键词的碎片（{show}）。\n"
                "   这些是行业报告里的小节标题、切坏的半截括号、机构名称，B2B 站内搜索"
                "必然一条结果都搜不出来，却每个都要花掉真实请求；\n"
                "   跨国采购网这类站更划算不起：同一个网络地址看约 50 个页面就要休息约 90 分钟，"
                "留着这些碎片等于偷走一轮配额。界面上原来的输入没动，只是不拿去检索。")
        self._warn_abstract_keywords(self.keywords)

    # 环节/服务/流程类词（非具体产品词）：B2B 站内搜索几乎无商品与之对应，
    # 实测这类词每词只能翻出 0~2 家，是「采了很多关键词却只有几百条数据」的主因。
    _ABSTRACT_KW_MARKS = ("研发", "标准支持", "咨询", "方案设计", "招投标", "招标",
                          "交付验收", "客户开发", "点位开发", "工程配套", "系统集成",
                          "安装调试", "现场安装", "跨境供应链", "解决方案", "关键词")

    def _warn_abstract_keywords(self, keywords):
        """关键词质量提醒（只提醒、不删改）：把看起来是「行业报告目录/价值链环节名」
        而非「具体产品词」的关键词列出来，建议换成产品词，避免用户白采。
        低误报：只命中明确的环节/服务/流程词，不碰含材料/部件/设备等的品类词。"""
        abstract = [k for k in keywords
                    if any(m in k for m in self._ABSTRACT_KW_MARKS)]
        if not abstract:
            return
        show = "、".join(abstract[:8]) + ("…" if len(abstract) > 8 else "")
        self._on_log(
            f"⚠ 关键词质量提醒：{len(abstract)}/{len(keywords)} 个词像是「环节/服务名」而非具体产品词（{show}）。\n"
            "   B2B 站内搜索按产品/商品匹配，这类抽象词每词往往只翻出 0~2 家，是数据量偏少的主因。\n"
            "   建议换成具体产品词（例：把「制冷与热泵核心部件」拆成「制冷压缩机」「冷凝器」「蒸发器」「膨胀阀」「热泵机组」），数据量可提升一个量级。\n"
            "   也可直接点「智能解析」按钮，自动把这些类别名展开成产品词。")

    # 「智能解析」结果提示语（润色后·通俗易懂）：内置知识库展开，结果仅供参考，
    # 不一定符合用户预期，务必让用户先检查再采集——放在界面标签 + 日志各一份。
    _SMART_HINT = ("智能解析已把行业类别名自动换成常见产品词，结果仅供参考，"
                   "不一定完全符合你的需求，请对照下方列表检查、增删后再开始采集。")

    def _on_smart_parse(self):
        """智能解析：把「行业报告目录/价值链环节名」按内置知识库展开成具体产品词，
        结果写回输入框供用户检查编辑，并给出「仅供参考、请检查」的提示。
        与「解析关键词」的区别：后者只做切分清洗、不改词；本功能会做语义展开。"""
        raw = self.kw_text.toPlainText()
        if not raw.strip():
            QMessageBox.information(self, "请先输入关键词",
                                    "智能解析需要先有内容：请粘贴或导入一批行业类别名/关键词，再点「智能解析」。")
            return
        try:
            rep = _kwexp.expand_keywords(raw)
        except Exception as e:
            self._on_log(f"⚠ 智能解析失败：{str(e).splitlines()[0][:120]}")
            return
        kws = rep["keywords"]
        if not kws:
            self._on_log("⚠ 智能解析：没识别到可展开的内容，请检查输入。")
            return
        # 写回输入框（一行一个），方便用户直接检查、增删
        self.kw_text.setPlainText("\n".join(kws))
        self.keywords = list(kws)
        self._kw_expanded_text = chr(10).join(kws)
        self.lbl_kw.setText(
            f"智能解析完成：得到 {len(kws)} 个产品词（展开 {len(rep['expanded'])} 个类别"
            f"{('、忽略 ' + str(len(rep['dropped'])) + ' 个服务/流程词') if rep['dropped'] else ''}）。"
            "请对照下方日志核查。")
        # 日志给出可核对的明细：展开了哪些类别、忽略了哪些词、保留了哪些原词
        self._on_log(f"🪄 智能解析：{len(kws)} 个产品词（由 {len(rep['expanded'])} 个类别展开）。")
        for seg, name, added in rep["expanded"]:
            self._on_log(f"   ・「{seg}」→ 归入【{name}】，新增 {added} 个产品词")
        if rep["kept"]:
            self._on_log(f"   ・原样保留（本就是产品词）：{'、'.join(rep['kept'][:20])}"
                         + ("…" if len(rep["kept"]) > 20 else ""))
        if rep["dropped"]:
            self._on_log(f"   ・已忽略（纯服务/流程词，站内无对应商品）：{'、'.join(rep['dropped'])}")
        self._on_log(f"⚠ {self._SMART_HINT}")

    # ── 公司名单（企知道 / 风鸟 / 爱企查 补采）/ 登录页 ───────────────────
    def _list_want_slugs(self):
        """界面上勾选的补采平台 slug（按注册表顺序，本机没有的平台不可能被勾上）。
        三路跑同一份名单：这里返回几个，就有几条看门狗各自从名单第 1 家查到最后一家。

        这里必须再过一遍**当前生效**的可见集合，不能只信控件状态：控件是建界面那一刻
        按当时的等级画的，而等级要拉到名单才知道。只靠「不创建」收口，就等于承认权限
        判定是一次性快照——账号等级被下调后，旧控件里勾着的风鸟照样会跑起来。
        """
        _vis = set(edition.visible_list_channels(LIST_CHANNEL_SLUGS, QZD_SLUG))
        out, dropped = [], []
        for slug in LIST_CHANNEL_SLUGS:
            chk = self.chk_list.get(slug)
            if chk is None or not chk.isChecked():
                continue
            (out if slug in _vis else dropped).append(slug)
        if dropped:
            # 不声不响少跑一路是最难查的那种不一致：勾着却没跑，用户只能猜
            self._on_log("ℹ 按当前账号权限，本次不走这些名单补采平台："
                         + "、".join(SITE_NAMES.get(s, s) for s in dropped)
                         + "（重启工具后勾选框也会消失）")
        return out

    def _on_import_company_file(self):
        """选择需补采的公司名文件：先预览识别到多少家（真正入列在开始采集时
        由 session.bind_pending 完成，那里才知道任务文件夹）。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择公司名单文件", "", "支持的文件 (*.xlsx *.xlsm *.xls *.csv *.txt);;所有文件 (*)")
        if not path:
            return
        self._co_source_file = path
        fname = Path(path).name
        try:
            from pending_companies import extract_company_names
            n = len(extract_company_names(path))
        except Exception as e:
            n = -1
            self._on_log(f"⚠ 公司名单预览失败：{str(e).splitlines()[0][:120]}")
        if n > 0:
            self.lbl_co.setText(f"已选择：{fname}（识别到 {n} 家公司，开始采集后写入待采名单）")
            self._on_log(f"📋 已选择公司名单：{fname}，识别到 {n} 家公司"
                         f"｜勾选了哪几个补采平台，就有几路各自从第 1 家查到第 {n} 家")
            # 传了名单就是想补采：一个平台都没勾时自动勾上默认那一路（免用户漏勾）
            if not self._list_want_slugs():
                default = self.chk_list.get(QZD_SLUG) or next(iter(self.chk_list.values()), None)
                if default is not None:
                    default.setChecked(True)
        elif n == 0:
            self.lbl_co.setText(f"已选择：{fname}（未识别到公司名，请确认有一列是公司名）")
            self._on_log(f"⚠ {fname} 里没识别到公司名（需有一列是公司名）")
        else:
            self.lbl_co.setText(f"已选择：{fname}")

    def _on_site_toggled(self, slug, checked):
        """勾选 1688 时弹窗提示需登录，确认后打开 1688 登录页（通俗易懂的话术）。"""
        if slug != "1688" or not checked:
            return
        ret = QMessageBox.information(
            self, "1688 需要先登录",
            "1688 有登录限制，登录后才能采集（所以默认没帮你勾选）。\n\n"
            "点「确定」会立即在共享浏览器里打开 1688，请你先看一眼是否已登录：\n"
            "・已登录 → 直接关掉提示，开始采集即可；\n"
            "・未登录 → 在打开的 1688 页面扫码/输账号登录，登录好再开始采集。\n\n"
            "点「取消」则暂不打开，你可以稍后自己在浏览器登录。",
            QMessageBox.Ok | QMessageBox.Cancel, QMessageBox.Ok)
        if ret == QMessageBox.Ok:
            self._open_site_login("1688")

    def _open_site_login(self, slug):
        """打开 1688 / 企知道 / 风鸟 / 爱企查 登录页：附加浏览器有网络 IO，一律走后台线程，
        绝不在主线程同步启动（否则窗口会「未响应」）。回控只走信号/主线程轮询。

        防重复点按平台各记各的：三平台并行时用户会连着点三个按钮，用一把全局锁会把
        后两个点击静默吞掉（用户看到的是「点了没反应」）。"""
        if slug in self._opening_logins:
            return
        label = SITE_NAMES.get(slug, slug)
        self._opening_logins.add(slug)
        btn = self.login_buttons.get(slug)
        if btn:
            btn.setEnabled(False)
        self._on_log(f"正在打开{label}登录页（首次会拉起共享浏览器，请稍候）…")

        def task():
            try:
                ok, msg = self.session.open_login_page(slug)
                self.session._log(("✅ " if ok else "⚠ ") + msg)
            except Exception as e:
                self.session._log(f"⚠ 打开{label}失败：{str(e).splitlines()[0][:120]}")
            finally:
                self._opening_logins.discard(slug)

        threading.Thread(target=task, daemon=True).start()

        def poll():
            if slug in self._opening_logins:
                QTimer.singleShot(400, poll)
            elif btn:
                btn.setEnabled(True)
        QTimer.singleShot(400, poll)

    # ── 采集控制 ───────────────────────────────────────────────
    def _on_clear_seen_store(self):
        if not self._acl_gate(edition.clear_seen_visible(), "清空历史种子库"):
            return
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "提示", "采集进行中，请先停止后再清空种子库。")
            return
        if QMessageBox.question(self, "确认", "确定清空全部平台历史种子库？清空后将对同一批关键词从头重采。") \
                == QMessageBox.Yes:
            self.session.clear_seen_store()
            QMessageBox.information(self, "完成", "历史种子库已清空。")

    def _final_save_sync(self):
        """同步收尾保存：复用自动保存的写盘方法，把最后不满一个批次的尾部数据也落盘。
        不然「停止→立刻开新任务」会让这批内存数据直接消失（自动保存是每 20 条才触发）。"""
        if not self.session_dir:
            return False
        if self.autosave and self.autosave.isRunning():
            self.autosave.wait(8000)   # 等上一个写盘线程退出，避免同文件互踩
        rows = self.session.snapshot_results()
        if not rows:
            return False
        try:
            ts_dir = Path(self.session_dir)
            ts_dir.mkdir(parents=True, exist_ok=True)
            raw = ts_dir / (_tasks.RAW_FILE_NAME if HAS_YZD else "企业数据_原始.xlsx")
            cl = ts_dir / "企业数据_清洗后入库数据.xlsx"
            worker = AutoSaveWorker(self.session, rows, self.session_dir)
            worker._write_raw(raw, rows)
            worker._write_cleaned(cl, rows)
            self._on_log(f"💾 收尾保存 {len(rows)} 条 → {raw}")
            return True
        except PermissionError:
            self._on_log("⚠ 收尾保存失败：Excel/WPS 正打开两份结果表占用，请关闭后手动点「导出 Excel」兼容尾部数据")
            return False
        except Exception as e:
            self._on_log(f"⚠ 收尾保存失败：{e}")
            return False

    def _on_reset_task(self):
        """「开始新任务」（需求：换一批关键词从零重采）：
        先同步落盘收尾→清空本次已采数据/各站账目/进度条/计时/看板 →
        释放当前时间戳文件夹（_first_start/session_dir 置空，下次「开始采集」自动新建）。
        跨运行历史种子库默认不动（清了就丢去重成果，下轮全量重请反而更容易被封 IP），
        要不要一并清由用户在对话框里勾选决定。"""
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "提示",
                                    "采集进行中。请先点「停止」并等日志出现「采集完成/已停止」后再开新任务。")
            return
        total = len(self.session.snapshot_results())
        dlg = QDialog(self)
        dlg.setWindowTitle("开始新任务 · 确认复位范围")
        dlg.setModal(True)
        dv = QVBoxLayout(dlg)
        tip = (f"当前已采 {total} 条数据、已保存到：\n{self.session_dir or '（尚未建立数据文件夹）'}\n\n"
               "复位会执行：先把尾部数据落盘 → 清空内存结果与各站进度账目 →\n释放当前文件夹（下次「开始采集」新建时间戳文件夹）→\n"
               "进度条/计时/看板/状态行归零。已保存的 Excel 与文件夹本身不会被删除。\n\n请勾选额外项：" if total else
               "当前内存里没有已采数据（本次无需落盘）。\n复位会清空各站进度账目并释放当前数据文件夹，\n"
               "下次点「开始采集」会新建一个时间戳文件夹。\n\n请勾选额外项：")
        dv.addWidget(QLabel(tip))
        # 复位对话框里的「同时清空历史种子库」是被隐藏按钮的**第二条入口**：只撤工具栏
        # 那个按钮、把这里留着打勾，一样能清光去重记录，还顺带把这个名词教给了用户。
        # 所以两处必须一起撤，判据也共用同一个 edition 能力位。
        chk_seen = None
        if edition.clear_seen_visible():
            chk_seen = QCheckBox("同时清空历史种子库（跨运行去重记录一起清，同一批关键词也彻底从头重采）")
            chk_seen.setToolTip("不勾：新关键词仍从零开文件夹、进度归零，但老关键词已采过的商家仍会命中跳过（省请求=防封）。\n"
                                "勾选：完全当全新任务，所有商家重新请求一遍（短时间重请会抬高防采风险）。")
        chk_kw = QCheckBox("同时清空关键词输入框（换一批关键词时用）")
        chk_log = QCheckBox("同时清空日志区")
        for c in (chk_seen, chk_kw, chk_log):
            if c is not None:
                dv.addWidget(c)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("确认开始新任务")
        bb.button(QDialogButtonBox.Cancel).setText("取消")
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        dv.addWidget(bb)
        if dlg.exec_() != QDialog.Accepted:
            return

        if total:
            self._final_save_sync()
        if chk_seen is not None and chk_seen.isChecked():
            self.session.clear_seen_store()
            self._on_log("🧹 历史种子库已清空（各平台跨运行去重记录全部删除）")
        self.session.reset_results()

        # 释放当前数据文件夹：_on_start 里 not self._first_start 分支会重建新时间戳目录
        self._first_start = None
        self.session_dir = None
        self._task_id = ""
        if chk_kw.isChecked():
            self.kw_text.clear()
            self.keywords = []
            self._kw_source_file = ""
            self.lbl_kw.setText("识别到 0 个关键词")
        if chk_log.isChecked():
            self.log.clear()
        self._last_autosave_bucket = 0
        self._bar_pct = 0
        self._bar_companies = 0
        # 进度条口径跟着「下一次开始采集」重新判定：复位后若还留着上一轮的名单标记，
        # 待机文本就会写着「名单补采中」而实际什么都没跑。
        self._bar_list_on = False
        self._bar_list_only = False
        self._manual_stop = False
        self._paused = False
        self._held_seconds = 0.0
        self._hold_from = 0.0
        self._crawl_elapsed = 0.0
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("")     # 与构造时同一口径：待机文字只由状态行说一次
        self.timer_lbl.setText("⏱ 00:00:00")
        self.lbl_stats.setText("就绪")
        self.lbl_board.setText("")
        self.lbl_save_info.setText("")
        self.lbl_board.setVisible(False)
        self.lbl_save_info.setVisible(False)
        self._hide_banner()
        self._close_block_alert()
        self.btn_start.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.btn_stop.setEnabled(False)
        self.btn_pause.setText("暂停")
        self._on_log("🆕 已清空任务：本次数据已落盘并从内存清空，采集进度/计时/看板归零，"
                     "数据文件夹已释放——下次点「开始采集」会新建一个时间戳文件夹从头重采。")
        QMessageBox.information(self, "已复位",
                                "任务已清空：进度、内存数据、计时、看板均归零。\n"
                                "下次点「开始采集」会新建一个时间戳文件夹保存新数据。")

    def _on_start(self):
        if not self.keywords:
            self._on_parse()
        has_co = bool(self._co_source_file)
        want_slugs = self._list_want_slugs()
        list_names = "、".join(SITE_NAMES.get(s, s) for s in want_slugs)
        # 三种用法都必须能启动：①只关键词 ②只公司名单（精准按名查联系方式，用户反馈需求）
        # ③两者都有。三者全空才是真的无事可做。
        if not self.keywords and not has_co and not want_slugs:
            QMessageBox.information(self, "提示",
                                    "请先输入/导入关键词，或选择公司名单文件"
                                    "（只选名单也能采集：勾上的补采平台会逐家按公司名查联系方式）。")
            return
        enabled = self._enabled_sites()
        if not self.keywords and not want_slugs:
            # 只跑名单时补采平台就是主任务：一个都没勾等于没有执行者（不能空跑一轮）
            # 平台名必须取 ``_list_vis_names``（档位在界面上显示的那几路）：这条提示在
            # 丐版也会弹（用户取消勾选企知道就能触发），照内部版点名「风鸟 / 爱企查」
            # 就等于把藏起来的平台又念了一遍——而那两个平台在界面上连个影子都没有。
            QMessageBox.information(self, "提示",
                                    "没有填关键词，本次只按公司名单采集：请至少勾选一个"
                                    f"名单补采平台（{self._list_vis_names}）。")
            return
        if not enabled and not want_slugs:
            QMessageBox.information(self, "提示", "请至少勾选一个采集平台。")
            return
        if self.worker and self.worker.isRunning():
            return
        # 自动启动浏览器（原「打开浏览器」按钮功能，现在集成到开始采集里）
        self._on_log("正在启动共享浏览器…")
        self.btn_start.setEnabled(False)
        self.btn_start.setText("启动浏览器中…")
        browser_ok = [False]

        def _start_browser_bg():
            try:
                browser_ok[0] = self.session.start_browser(headed=True)
            except Exception:
                browser_ok[0] = False

        t = threading.Thread(target=_start_browser_bg, daemon=True)
        t.start()
        t.join(timeout=30)
        if not browser_ok[0]:
            self.btn_start.setEnabled(True)
            self.btn_start.setText("开始采集")
            QMessageBox.warning(self, "浏览器启动失败",
                                "共享浏览器未能启动，请确认已安装 Google Chrome。\n"
                                "如果 Chrome 正在运行，请关闭后重试。")
            return
        self._on_log("✅ 共享浏览器已就绪")
        self.btn_start.setText("开始采集")
        # 自动扩词：行业类别名 → 具体产品词（放在开始采集前，用户不用记着先点「智能解析」）
        self._auto_expand_on_start()
        self.session.set_enabled(enabled)
        self.session.configure(delay_between=self.spin_delay.value(),
                               max_pages=0,      # 不限页数：翻到没有新数据自然切下一个关键词
                               relevance_guard=self.chk_guard.isChecked(),
                               incremental=self.chk_incr.isChecked(),
                               province=self.selected_provinces)
        if self.selected_provinces:
            names = "、".join(self.selected_provinces)
            self._on_log(f"🗺 本次按 {len(self.selected_provinces)} 个省「{names}」精准采集："
                         "支持地区筛选的站交给网站筛、采回来的每条再按地址与公司名本地复核；"
                         "其余站直接按页面标注地区与公司名把关，对不上的不入库；"
                         "看不出省份的转名单补采通道按工商地址反查后再定")
        if not self._first_start:
            self._first_start = datetime.now().strftime("%Y%m%d_%H%M%S")
            base = _tasks.tool_dir(self._get_desktop_dir()) if HAS_YZD else \
                (self._get_desktop_dir() / "B2B多平台企业数据自动采集工具")
            self.session_dir = str(base / self._first_start)
        # 进度台账与数据 Excel 同文件夹：拷走整个文件夹就把「采到哪儿」一起带走了
        self.session.set_task_folder(self.session_dir)
        # 绑定待采集公司名单（任务文件夹里生成「待采集公司名单.xlsx」，与原数据/清洗入库同层级）：
        # 上传名单入列 + 清洗废数据持续回流；want_slugs=界面勾选的补采平台（可多路）。
        try:
            _started, _added = self.session.bind_pending(self.session_dir, self._co_source_file,
                                                         want_slugs=want_slugs)
        except Exception as e:
            _started = False
            self._on_log(f"⚠ 待采名单绑定失败（不影响关键词采集）：{str(e).splitlines()[0][:120]}")
        self._register_task(_tasks.TASK_STATUS_RUNNING)
        self._paused = False
        self.btn_start.setEnabled(False)
        self.btn_pause.setEnabled(True); self.btn_pause.setText("暂停")
        self.btn_stop.setEnabled(True)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self._bar_pct = 0
        self._bar_companies = 0
        # 进度条口径交给本轮实际干的活：只跑名单时前缀写「名单补采中」，
        # 另外把三路台账的「家次」一起显示（家数×路数），不然用户看不到分母只能猜。
        self._bar_list_on = bool(want_slugs)
        self._bar_list_only = bool(want_slugs) and not self.keywords
        self._last_autosave_bucket = 0
        self._start_timer()
        self._update_bar()
        _sites = ("、".join(SITE_NAMES[s] for s in enabled) if enabled else "无关键词平台") \
            if self.keywords else "只按公司名单查号"
        _kw_note = f"{len(self.keywords)} 个关键词" if self.keywords else "未填关键词（精准查号模式）"
        # 以会话里真正启用的那几路为准报尾串：勾了但本机没有采集器的平台不能算「已启用」
        _on = self.session.enabled_list_slugs()
        if _on:
            _tail = ("｜" + "、".join(SITE_NAMES[s] for s in _on)
                     + " 名单补采已启用（同一份名单，每一路都从第 1 家查到最后一家）")
        elif list_names:
            _tail = f"｜⚠ 勾选的补采平台（{list_names}）本机不可用，本轮不补采"
        else:
            _tail = ""
        self._on_log(f"▶ 开始采集：{_sites}｜{_kw_note}{_tail}")
        self.worker = CrawlWorker(self.session, self.keywords)
        self.worker.start()
        self.request_autosave()
        if self.chk_bg.isChecked():
            # 标签是各站线程启动后才 pin 的：延迟 5s/30s 各试一次最小化，
            # 确保晚建立的标签所在窗口也能后台化。
            QTimer.singleShot(5000, self._apply_background)
            QTimer.singleShot(30000, self._apply_background)

    def _apply_background(self):
        if self.chk_bg.isChecked() and self.worker and self.worker.isRunning():
            threading.Thread(target=lambda: self.session.set_browser_background(True),
                             daemon=True).start()

    def _on_pause(self):
        if self._paused:
            self.session.resume_crawl()
            self._paused = False
            self.btn_pause.setText("暂停")
            self._hold_timer(False)
        else:
            self.session.pause_crawl()
            self._paused = True
            self.btn_pause.setText("继续")
            self._hold_timer(True)
        # 暂停那一刻就把进度写盘：用户随时关工具，下次打开看到的进度都不差于最后一次落盘
        self._persist_progress(_tasks.TASK_STATUS_PAUSED if self._paused
                               else _tasks.TASK_STATUS_RUNNING)

    def _on_stop(self):
        self._manual_stop = True
        self.session.stop_crawl()

    def _on_finished(self):
        self.btn_start.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.btn_stop.setEnabled(False)
        self.btn_pause.setText("暂停")
        self._stop_timer()
        stopped = bool(getattr(self, "_manual_stop", False))
        self._manual_stop = False
        total = len(self.session.snapshot_results())
        # 收尾口径（2026-11-11 实机反馈「风鸟和爱企查好像停止运行了」）：名单还有家次
        # 没查完时**绝不**画满 100%，否则同一屏上「进度条满了」和「名单还剩 52 家」
        # 两张嘴各说各话，用户只能靠猜。跑干净了才给 100%。
        try:
            ld, lt = self.session.list_progress()
        except Exception:
            ld = lt = 0
        left = max(0, int(lt) - int(ld))
        self.progress.setRange(0, 100)
        self.progress.setValue(100 if not left else min(99, int(getattr(self, "_bar_pct", 0))))
        tail = f"｜共 {total} 家｜耗时 {self._format_time(self._crawl_elapsed)}"
        if left:
            # 一路在跑时「家次」＝家数，两路以上才叫家次：口径跟着本轮实际勾选的路数走
            unit = "家" if len(self.session.enabled_list_slugs()) <= 1 else "家次"
            self.progress.setFormat(f"{'已停止' if stopped else '本轮结束'}｜"
                                    f"名单还剩 {left} {unit}未查（重新开始任务自动接续）{tail}")
        else:
            self.progress.setFormat(f"{'已停止' if stopped else '完成'}{tail}")
        self._refresh_board()
        self._hide_banner()
        self._close_block_alert()   # 停止/完成都要收掉验证弹窗，不然留着会误导用户还在等
        self.request_autosave()
        self._finish_task(_tasks.TASK_STATUS_STOPPED if stopped else _tasks.TASK_STATUS_DONE,
                          len(self.session.snapshot_results()))
        # 收尾把「关键词 + 采到哪儿」写回任务文件夹与索引，下次打开直接接着采
        self._persist_progress(_tasks.TASK_STATUS_STOPPED if stopped
                               else _tasks.TASK_STATUS_DONE)
        if not stopped:
            self._maybe_auto_import("采集完成")
        # 每词产出反馈闭环：把本轮证明高产的词派生同族近义词补进词表（要用户再点开始才采）
        self._boost_high_yield()
        # 采集完成后的数据池动作，两条档位走两条路（互斥性写在 edition 的能力矩阵里，
        # 不在这里 if/else 兜运气）：内部版照旧弹窗问一句；对外版不打扰用户，后台只把
        # 这次新出来的记录传进去。
        #
        # 触发点与内部版**完全一致**：手动「停止」也算一轮结束（既有 _prompt_pool_sync
        # 就是这个位置）。这与云展「停止不静默导入」的口径不同，是刻意的——外部用户
        # 大量是手动停手，若停止就不同步，等于把大部分产出挡在池子外面。
        # 要改成「只有跑完才传」只需把下面整块挪进上面的 `if not stopped:` 里。
        if HAS_POOL and total > 0:
            if edition.silent_sync_enabled():
                self._silent_pool_sync()
            elif edition.prompt_sync_enabled():
                self._prompt_pool_sync(total)

    # ── 自动保存 / 导出 ────────────────────────────────────────
    def request_autosave(self):
        if not self.session_dir:
            return
        if self.autosave and self.autosave.isRunning():
            self._pending_save = True
            return
        rows = self.session.snapshot_results()
        if not rows:
            return
        self.autosave = AutoSaveWorker(self.session, rows, self.session_dir)
        self.autosave.saved.connect(self._on_saved)
        self.autosave.failed.connect(lambda m: self._on_log("⚠ " + m))
        self.autosave.finished.connect(self._after_autosave)
        self.autosave.start()

    def _after_autosave(self):
        if self._pending_save:
            self._pending_save = False
            self.request_autosave()

    def _on_saved(self, path, n, mobile_n=0):
        now = time.strftime("%H:%M:%S")
        mobile_hint = f"（国内手机 {mobile_n} 条）" if mobile_n else ""
        self._on_log(f"💾 已自动保存 {n} 条{mobile_hint} → {path}")
        self.lbl_save_info.setText(f"💾 上次自动保存：{now}（原始 {n} 条{mobile_hint}）→ {path}")
        self.lbl_save_info.setVisible(True)
        if HAS_YZD and self._task_id:
            try:
                _tasks.update_task(self._task_id, count=n)
            except Exception:
                pass
        # 每落一次数据就同步刷一次进度台账：中途强杀/断电，下次也能从最后落盘处接上
        self._persist_progress()

    def _on_clean_now(self):
        """「清洗数据」：直接选文件 → 选输出目录 → 增强清洗输出三文件（国内/国内手机/国外）。
        不再弹模式选择对话框，用户只管上传文档即可。"""
        if getattr(self, "_cleaning", False):
            QMessageBox.information(self, "提示", "正在清洗数据，请等当前清洗完成")
            return
        if HAS_ENHANCED:
            self._clean_external_enhanced()
        else:
            self._clean_external_data()

    def _clean_external_data(self):
        """外部表格清洗入口：多选任意 Excel/CSV → 后台智能清洗 → 写入库格式文件"""
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择要清洗的数据文件（可多选）",
            getattr(self, "_clean_last_dir", "") or str(self._get_desktop_dir()),
            "表格文件 (*.xlsx *.xlsm *.xls *.csv);;Excel (*.xlsx *.xlsm *.xls);;CSV (*.csv);;所有文件 (*)")
        if not paths:
            return
        self._clean_last_dir = os.path.dirname(paths[0]) or ""
        self._on_log(f"🧹 清洗数据：选中 {len(paths)} 个文件")
        default_dir = os.path.dirname(paths[0]) or str(self._get_desktop_dir())
        out, _ = QFileDialog.getSaveFileName(
            self, "清洗结果保存为（入库模版格式）",
            os.path.join(default_dir, "清洗后入库数据.xlsx"), "Excel 文件 (*.xlsx)")
        if not out:
            self._on_log("已取消清洗数据（未选保存位置）")
            return
        if not out.lower().endswith(".xlsx"):
            out += ".xlsx"
        if os.path.exists(out) and QMessageBox.question(
                self, "覆盖已有文件",
                f"目标文件已存在，确认覆盖？\n{out}",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No) != QMessageBox.Yes:
            self._on_log("已取消清洗数据（不覆盖已有文件）")
            return
        # 清洗走后台：多个大文件读下来几十秒，直接在 GUI 线程里做就是白屏
        self._cleaning = True
        self.setEnabled(False)
        prog = YzdProgressDialog(None, "正在清洗数据")
        prog.set_text(f"正在清洗 {len(paths)} 个文件…", "清洗在后台进行，请勿关闭程序。")
        self._clean_progress = prog
        prog.show()
        worker = ExternalCleanWorker(paths, out, template_dirs=[
            os.path.dirname(out), default_dir, str(Path.home() / "Downloads")])
        worker.logged.connect(self._on_log)
        worker.finished_clean.connect(self._on_external_clean_finished)
        self._clean_worker = worker
        worker.start()

    def _clean_external_enhanced(self):
        """增强三文件清洗入口：多选文件 → 选输出目录 → 后台清洗 → 国内/国内手机/国外 三个文件"""
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择要清洗的数据文件（可多选）",
            getattr(self, "_clean_last_dir", "") or str(self._get_desktop_dir()),
            "表格文件 (*.xlsx *.xlsm *.xls *.csv);;Excel (*.xlsx *.xlsm *.xls);;CSV (*.csv);;所有文件 (*)")
        if not paths:
            return
        self._clean_last_dir = os.path.dirname(paths[0]) or ""
        self._on_log(f"🧹 增强清洗：选中 {len(paths)} 个文件")
        default_dir = os.path.dirname(paths[0]) or str(self._get_desktop_dir())
        output_dir = QFileDialog.getExistingDirectory(
            self, "选择三文件输出目录（国内 / 国内只留手机号 / 国外）", default_dir)
        if not output_dir:
            self._on_log("已取消增强清洗（未选输出目录）")
            return
        # 检查是否已有同名文件
        import glob as _glob
        existing = _glob.glob(os.path.join(output_dir, "清洗后_*_*.xlsx"))
        if existing and QMessageBox.question(
                self, "覆盖已有文件",
                f"目标目录下已有 {len(existing)} 个清洗文件，确认覆盖？\n{output_dir}",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No) != QMessageBox.Yes:
            self._on_log("已取消增强清洗（不覆盖已有文件）")
            return
        self._cleaning = True
        self.setEnabled(False)
        prog = YzdProgressDialog(None, "正在增强清洗数据")
        prog.set_text(f"正在清洗 {len(paths)} 个文件（libphonenumber 深度解析）…",
                      "清洗在后台进行，请勿关闭程序。")
        self._clean_progress = prog
        prog.show()
        worker = EnhancedExternalCleanWorker(paths, output_dir)
        worker.logged.connect(self._on_log)
        worker.finished_clean.connect(self._on_external_clean_finished)
        self._clean_worker = worker
        worker.start()

    def _on_external_clean_finished(self, ok: bool, msg: str):
        self._cleaning = False
        self.setEnabled(True)
        prog = getattr(self, "_clean_progress", None)
        if prog is not None:
            try:
                prog.allow_close()
                prog.close()
            except Exception:
                pass
            self._clean_progress = None
        self.activateWindow()
        self._on_log(("✅ " if ok else "❌ ") + msg.replace("\n", "  "))
        if ok:
            QMessageBox.information(self, "清洗完成", msg)
        else:
            QMessageBox.warning(self, "清洗未完成", msg)

    def _clean_session_data(self):
        """把当前已采数据立即跑一遍清洗，落盘原始+清洗入库两份，
        废数据经 clean_records 的 waste_sink 回流待采名单。数据量有限，同步执行秒级完成。"""
        rows = self.session.snapshot_results()
        if not rows:
            QMessageBox.information(self, "提示", "还没有采集到数据，无法清洗。")
            return
        if self.autosave and self.autosave.isRunning():
            self.autosave.wait(8000)   # 等上一个写盘线程退出，避免同文件互踩
        if not self.session_dir:
            self._first_start = datetime.now().strftime("%Y%m%d_%H%M%S")
            base = _tasks.tool_dir(self._get_desktop_dir()) if HAS_YZD else \
                (self._get_desktop_dir() / "B2B多平台企业数据自动采集工具")
            self.session_dir = str(base / self._first_start)
        try:
            ts_dir = Path(self.session_dir)
            ts_dir.mkdir(parents=True, exist_ok=True)
            raw = ts_dir / (_tasks.RAW_FILE_NAME if HAS_YZD else "企业数据_原始.xlsx")
            cl = ts_dir / "企业数据_清洗后入库数据.xlsx"
            AutoSaveWorker._write_raw(raw, rows)
            # 清洗只跑一次（避免重复触发 waste_sink）：直接写已算好的 clean_rows
            clean_rows = self.session.clean_records(rows, log=lambda m: None)
            template = _cleaner.locate_template([self.session_dir, str(Path.home() / "Downloads")])
            tmp = str(cl) + ".tmp"
            _cleaner.write_clean_excel(clean_rows, tmp, template_path=template, log=lambda m: None)
            os.replace(tmp, str(cl))
            self.lbl_save_info.setText(f"🧼 上次清洗：{time.strftime('%H:%M:%S')}（入库 {len(clean_rows)} 条）→ {cl}")
            self.lbl_save_info.setVisible(True)
            self._on_log(f"🧼 已清洗 {len(rows)} 条 → 入库 {len(clean_rows)} 条｜{cl}")
            # 「会逐家补采」只能承诺给真的还在跑的那些路：三路各自的熔断状态是独立的
            _who = "、".join(SITE_NAMES[s] for s in self.session.enabled_list_slugs())
            _can = self.session._list_can_run()
            tail = (f"，{_who}会逐家补采。" if _can and _who
                    else "（补采通道本轮没接上，下次任务会自动接续）。")
            QMessageBox.information(
                self, "清洗完成",
                f"原始 {len(rows)} 条已清洗，入库 {len(clean_rows)} 条。\n\n"
                f"清洗后入库数据：\n{cl}\n\n"
                f"无联系方式的废数据已自动加入「待采集公司名单」{tail}")
        except PermissionError:
            self._on_log("⚠ 清洗失败：结果表正被 Excel/WPS 打开占用，请关闭后重试")
            QMessageBox.warning(self, "清洗失败", "结果表正被 Excel/WPS 打开占用，请关闭后重试。")
        except Exception as e:
            self._on_log(f"⚠ 清洗失败：{e}")
            QMessageBox.warning(self, "清洗失败", str(e))

    def _on_export(self):
        rows = self.session.snapshot_results()
        if not rows:
            QMessageBox.information(self, "提示", "还没有采集到数据。")
            return
        default = f"B2B多平台企业数据_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
        path, _ = QFileDialog.getSaveFileName(self, "导出 Excel",
                                              str(self._get_desktop_dir() / default), "Excel 文件 (*.xlsx)")
        if not path:
            return
        ok = self.session.export_excel(path)
        if ok:
            cpath = str(Path(path).with_name(Path(path).stem + "_清洗后入库数据.xlsx"))
            mpath = str(Path(path).with_name(Path(path).stem + "_国内只留手机号.xlsx"))
            try:
                res = self.session.export_cleaned_excel(cpath)
            except Exception as e:
                res = ""
                self._on_log(f"清洗导出失败：{e}")
            # 同步生成「国内只留手机号」文件
            mobile_res = ""
            if res:
                try:
                    clean_rows = self.session.clean_records(rows, log=lambda m: None)
                    mobile_rows = _yzd.filter_cn_mobile_rows(clean_rows, log=self._on_log)
                    template = _cleaner.locate_template([self.session_dir, str(Path.home() / "Downloads")])
                    tmp = mpath + ".tmp"
                    _cleaner.write_clean_excel(mobile_rows, tmp, template_path=template, log=lambda m: None)
                    os.replace(tmp, mpath)
                    mobile_res = mpath
                except Exception as e:
                    self._on_log(f"国内手机文件导出失败：{e}")
            if res and mobile_res:
                QMessageBox.information(self, "导出完成",
                                        f"原始明细：\n{path}\n\n"
                                        f"清洗后入库数据：\n{cpath}\n\n"
                                        f"国内只留手机号：\n{mobile_res}")
            elif res:
                QMessageBox.information(self, "导出完成",
                                        f"原始明细：\n{path}\n\n清洗后入库数据（对齐客户库模板）：\n{cpath}")
            else:
                QMessageBox.information(self, "导出完成",
                                        f"原始明细已导出（无满足入库条件的数据）：\n{path}")

    # ── 数据池：同步 / 搜索 ──────────────────────────────────

    def _pool_records(self):
        """本次采集结果 → 数据池标准记录（手动同步与后台静默**共用这一个漏斗**）。

        返回 ``(records, reason)``：records 非空时 reason 为 None；否则 reason 是
        ``"no_rows"`` / ``"no_valid"`` / 一个异常。**怎么把空结果告诉用户由调用方决定**
        （内部版弹窗、对外版静默写日志），判据本身只有一份。

        两条入口绝不各写一遍转换：各写一份的结局必然是一边漏掉
        ``raw_record_to_standard`` 的号码拆分，传进池子的不再是同一种数据——而池子里
        看不出这种差别，只有半年后搜索的人发现某列全空时才暴露。
        """
        rows = self.session.snapshot_results()
        if not rows:
            return [], "no_rows"
        try:
            records = []
            for row in rows:
                # 使用 raw_record_to_standard 直接转换原始记录（保留所有字段）
                rec = DataPoolManager.raw_record_to_standard(row)
                if rec:
                    records.append(rec)
        except Exception as e:
            return [], e
        if not records:
            return [], "no_valid"
        return records, None

    def _on_pool_sync(self):
        """「同步数据池」：把本次采集数据上传到团队 OSS 数据池。"""
        if not self._acl_gate(edition.pool_ui_visible(), "同步数据池"):
            return
        if not HAS_POOL:
            QMessageBox.warning(self, "模块缺失", "数据池模块未加载，无法使用此功能。")
            return
        records, reason = self._pool_records()
        if not records:
            if isinstance(reason, Exception):
                self._on_log(f"❌ 同步数据池失败: {reason}")
                QMessageBox.critical(self, "同步失败", str(reason))
            elif reason == "no_rows":
                QMessageBox.information(self, "无数据", "还没有采集到数据，请先完成采集。")
            else:
                QMessageBox.information(self, "无有效记录",
                                        "本次采集数据中没有可同步的有效记录。")
            return
        try:
            dlg = SyncDialog(records, parent=self)
            dlg.exec_()
        except Exception as e:
            self._on_log(f"❌ 同步数据池失败: {e}")
            QMessageBox.critical(self, "同步失败", str(e))

    # ── 后台静默同步数据池（对外档位；界面无感，磁盘留痕）──────────────────
    #
    # 设计约束，四条都不可让：
    #   ① 口径同源——与手动同步共用 _pool_records 与 SyncWorker/upload_session，
    #      清洗、有效性门禁、分片、原子写一个都不绕过；
    #   ② 只传增量——上传层是 append-only 且不去重，没有本地指纹台账的话，
    #      每天挂机跑一轮就会把同一批几千家重复灌进池子；
    #   ③ 绝不出声——不弹窗、不提问、不写界面日志区（写了就等于告诉用户有这么件事）；
    #   ④ 必须留证据——只「不出声」而什么都不记，外部用户说「我的数据没进池」时只能猜。
    #      留痕落在 silent_sync.log，界面永远看不到它。
    # 台账只在上传**成功**后写；失败不写 ⇒ 下次采集完成自动重试。

    SILENT_LOG_TAIL = 200          # 留痕文件超限时只保留最后这么多行

    def _silent_paths(self):
        """(台账, 留痕) 两个文件路径；目录建不出来时返回 (None, None)。"""
        try:
            cache = Path(DataPoolManager._default_cache_dir())
        except Exception:
            # 兜底路径同样要过一遍档位投影：写死桌面的话，丐版就在这个异常分支上
            # 又给用户造出一个叫「数据池缓存」的桌面文件夹——正是主路径要避免的事。
            cache = Path(edition.pool_cache_dir(
                str(Path(self._get_desktop_dir()) / "数据池缓存")))
        try:
            cache.mkdir(parents=True, exist_ok=True)
        except Exception:
            return None, None
        return cache / "silent_sync_ledger.json", cache / "silent_sync.log"

    def _silent_log(self, text):
        """静默同步留痕：只写磁盘。任何异常都吞掉——留痕失败不能连累采集主流程。"""
        _, path = self._silent_paths()
        if path is None:
            return
        try:
            if path.exists():
                lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
                if len(lines) > 2000:
                    path.write_text("\n".join(lines[-self.SILENT_LOG_TAIL:]) + "\n",
                                    encoding="utf-8")
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {text}\n")
        except Exception:
            pass

    @staticmethod
    def _silent_key(rec):
        """一条记录在台账里的身份：**公司归一名 + 全部联系方式**（取不到返回 None）。

        为什么不能只按公司名：同一家公司这次没有号码、下次补到了手机号，那是必须
        重传的新信息；只按公司名去重会把它永久当成「传过了」，池子里就永远停在最早
        那份残缺联系方式。为什么不把地址/主营也放进指纹：它们对下游的价值远低于联系
        方式，放进去会让每次采集都重传大半数据，台账等于没有。
        联系方式**排序去重**后拼接：同一批号码换个顺序不该被当成新记录。
        """
        parts = [str(rec.get("company_norm") or "").strip()]
        if not parts[0]:
            return None            # 没有归一名就定不了身份，宁可当新记录传，不写错指纹
        for field in ("mobiles", "landlines", "emails", "wechats"):
            vals = {str(v).strip() for v in (rec.get(field) or []) if str(v).strip()}
            parts.append(",".join(sorted(vals)))
        parts.append(str(rec.get("website") or "").strip().lower())
        return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:24]

    def _silent_ledger(self):
        """读台账 → (已传过的指纹集合, 路径)。

        读不懂 / 不存在一律返回空集合：方向必须是这样——**宁可重传一遍（多占存储，
        读取端合并幂等，不产生脏数据），不可漏传**（漏传是外部用户的数据永远进不了池）。
        """
        path, _ = self._silent_paths()
        if path is None:
            return set(), None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            keys = data.get("keys") if isinstance(data, dict) else None
            return {str(k) for k in (keys or [])}, path
        except Exception:
            return set(), path

    @staticmethod
    def _silent_ledger_write(path, keys):
        """原子写台账（临时文件 + replace）：读到半截 JSON 就等于下次全部重传。"""
        if path is None:
            return False
        tmp = path.with_name(path.name + ".part")
        try:
            tmp.write_text(json.dumps({
                "version": 1,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "count": len(keys),
                "keys": sorted(keys),
            }, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
            return True
        except Exception:
            try:
                os.remove(tmp)
            except Exception:
                pass
            return False

    def _silent_pool_sync(self):
        """采集完成后把这次**新出来**的记录静默传到数据池。"""
        if self._silent_worker is not None and self._silent_worker.isRunning():
            # 上一批还在路上：只记一个「待重试」标记，回来时用**最新**的内存数据重算
            # 增量（队列里塞旧记录会让它按旧口径上传，而这段时间又采到了一批新的）。
            self._silent_pending = True
            self._silent_log("上一批仍在上传，本次排队到它结束后重算增量")
            return
        records, reason = self._pool_records()
        if not records:
            if isinstance(reason, Exception):
                self._silent_log(f"跳过（记录转换失败，下次采集会再试）：{reason}")
            else:
                self._silent_log(f"跳过（{'本次没有采集结果' if reason == 'no_rows' else '没有可同步的有效记录'}）")
            return
        known, ledger_path = self._silent_ledger()
        fresh, fresh_keys, dup = [], set(), 0
        for rec in records:
            k = self._silent_key(rec)
            if k is None:
                fresh.append(rec)                      # 定不了身份：照传，但不进台账
                continue
            if k in known:
                dup += 1
                continue
            known.add(k)                               # 同一批里同公司同行号只传一次
            fresh_keys.add(k)
            fresh.append(rec)
        if not fresh:
            self._silent_log(f"跳过：{len(records)} 条记录的内容与以前传过的完全相同")
            return
        self._silent_keys_batch = fresh_keys
        self._silent_committed = False      # 新一批：落账标记复位
        self._silent_result = None          # 上一批的结果不许串到这一批
        self._silent_log(f"开始上传 {len(fresh)} 条"
                         f"（本次共 {len(records)} 条，{dup} 条内容与已传过的相同）")
        w = SyncWorker(fresh)
        # 两条连接各有用处：Direct 那份只是把结果抄在属性上（在上传线程里同步跑，
        # 做的事足够小才敢这么干），保证窗口正在关闭时也能拿到真实成败；
        # 排队那份才是正常的落账与续跑入口。
        w.finished.connect(self._silent_capture, Qt.DirectConnection)
        w.finished.connect(self._on_silent_sync_finished)
        self._silent_worker = w
        try:
            w.start()
        except Exception as e:
            # 线程起不来（极少见，通常是解释器正在退出）：不记台账、不打扰用户，
            # 下次采集完成自然重试。这里必须留一行，否则连「试过一次」都查不到。
            self._silent_worker = None
            self._silent_log(f"启动上传线程失败（下次采集完成会自动再试）：{e}")

    def _silent_capture(self, success, message):
        """把 worker 报的成败抄成属性（DirectConnection，在上传线程内同步执行）。

        这个槽**只许赋值**：不碰控件、不写盘、不弹窗。跨线程跑的东西一旦多做一件事，
        就会在关闭窗口的竞态里变成难以复现的崩溃；而这一句赋值换来的，是退出路径上
        终于能知道「这批到底传成什么样」。
        """
        self._silent_result = (bool(success), str(message))

    def _silent_commit(self, success, message):
        """把这一批的结果落账落盘。幂等且有防重入标记，允许两处调用（信号回调 / 退出等待）。

        为什么要有第二个调用点：关闭窗口时主线程在 ``wait()`` 上阻塞，worker 的
        ``finished`` 是排队信号，事件循环可能就此结束、回调根本不跑——那样这批就白传了
        （台账没记，下次开机整套重传一遍）。退出路径必须自己把账补上。
        """
        if self._silent_committed:
            return
        self._silent_committed = True
        if success:
            known, ledger_path = self._silent_ledger()
            known |= self._silent_keys_batch or set()
            ok = self._silent_ledger_write(ledger_path, known)
            # 台账里记的是「这批内容传过了」。上传前清洗按门禁剔除的行也算传过——
            # 它们不会因为重传就变成有效数据，不记的话每次采集都白跑一趟网络。
            self._silent_log(f"完成：{message}"
                             + ("" if ok else "（台账没写成，下次会把这批再传一遍）"))
        else:
            self._silent_log(f"失败（不重试到界面，下次采集完成会自动再试）：{message}")

    def _on_silent_sync_finished(self, success, message):
        w, self._silent_worker = self._silent_worker, None
        self._silent_commit(success, message)
        if w is not None:
            w.deleteLater()
        if self._silent_pending:
            self._silent_pending = False
            self._silent_pool_sync()

    def _on_pool_search(self):
        """「搜索采购商」：打开数据池搜索对话框。"""
        if not self._acl_gate(edition.pool_ui_visible(), "搜索采购商"):
            return
        if not HAS_POOL:
            QMessageBox.warning(self, "模块缺失", "数据池模块未加载，无法使用此功能。")
            return
        try:
            dlg = SearchDialog(parent=self)
            dlg.exec_()
        except Exception as e:
            self._on_log(f"❌ 打开搜索对话框失败: {e}")
            QMessageBox.critical(self, "搜索失败", str(e))

    def _on_pool_import(self):
        """「导入历史数据」：多选文件/压缩包 → 列映射预览确认 → 上传到数据池。"""
        if not self._acl_gate(edition.pool_ui_visible(), "导入历史数据"):
            return
        if not HAS_POOL:
            QMessageBox.warning(self, "模块缺失", "数据池模块未加载，无法使用此功能。")
            return
        import shutil
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择历史数据（可按住 Cmd 多选，也可直接选压缩包）", "",
            "Excel/CSV/压缩包 (*.xlsx *.xlsm *.xls *.csv *.zip *.tar.gz *.tgz *.tar);;所有文件 (*)",
        )
        if not files:
            return
        staging = None
        try:
            from data_pool import expand_import_files
            self._on_log(f"📂 展开选择的 {len(files)} 个文件…")
            # 压缩包解到临时目录（staging），与直接选的文件走同一条链路
            pairs, skipped, staging = expand_import_files(files)
            for name, reason in skipped:
                self._on_log(f"⚠ 跳过 {name}：{reason}")
            if not pairs:
                detail = "\n".join(f"· {n}：{r}" for n, r in skipped[:8])
                QMessageBox.information(
                    self, "没有可导入的文件",
                    "选中的内容里没有找到能导入的 Excel/CSV 文件。\n\n"
                    + (detail + "\n\n" if detail else "")
                    + "支持：.xlsx / .xlsm / .csv 文件，或里面装着"
                      "这些文件的压缩包（zip / tar.gz 等）。")
                return
            self._import_pairs_to_pool(pairs, pre_skipped=skipped)
        except Exception as e:
            self._on_log(f"❌ 导入历史数据失败: {e}")
            QMessageBox.critical(self, "导入失败", str(e))
        finally:
            # 解压出来的临时文件在预览/读取全部结束后统一清理
            if staging:
                shutil.rmtree(staging, ignore_errors=True)

    # ── 本地台账（内容指纹 → 是否已经入过池）──────────────────────

    def _ledger(self):
        """台账句柄；模块不可用返回 None（本次不记账，功能照常可用）。"""
        try:
            from local_scan import Ledger
            return Ledger().load()
        except Exception as e:
            self._on_log(f"⚠ 本地台账不可用，本次不记录已导入文件：{e}")
            return None

    def _ledger_items(self, led, pairs):
        """给每个待处理文件算内容指纹，产出可记账的项（改过才重算，其余命中缓存）。

        指纹是**内容**的指纹：文件改名、换目录、另存一份副本都不影响结果，
        所以手动导入过的表格，之后全盘扫描同样认成「这份内容已经传过」——
        两个入口共用一份台账是刻意的，各记各的就会各传各的。
        """
        out = []
        if led is None:
            return out
        for path, display in pairs:
            try:
                st = os.stat(path)
                fp, _cached = led.fp_for(path, st.st_size, int(st.st_mtime))
            except Exception:
                continue
            out.append({"path": path, "display": display, "fp": fp,
                        "size": st.st_size, "mtime": int(st.st_mtime),
                        "rows_kept": 0, "rows_in": 0, "kept": 0, "in": 0})
        return out

    def _import_pairs_to_pool(self, pairs, pre_skipped=None):
        """一批表格 → 列映射确认 → 逐文件读取 → 同步上传 → 台账回写。

        「导入历史数据」和「扫描本地表格」两条入口都走这里：同一套列映射确认、
        同一套清洗口径、同一个上传对话框。两条路各写一份判据必然会出现
        「扫描入口传得进去、手动入口传不进去」这类说不清的分叉。

        台账记账的三条硬规矩：
          · 逐文件记录真实 kept/in，只给 kept>0 的文件写「已入库」；
          · 上传没成功（取消/失败/没点开始同步）一律不记账，下次照旧列出；
          · 扫出来「像有数据」但权威链路真读一条都留不下的，按不合格记账，
            免得下次扫描再把它当成待入库反复误导用户。
        """
        if not pairs:
            return
        led = self._ledger()
        try:
            from data_pool import DataPoolManager
            # 第一步：批量列映射预览（自动识别 + 可修正 + 公司名必选校验）。
            # 大厂 ETL 的 schema mapping 确认思想：错列在入库前拦截，
            # 而不是入库后靠用户发现数据不对再返工
            preview_dlg = ImportPreviewDialog(pairs, parent=self)
            if preview_dlg.exec_() != QDialog.Accepted:
                return
            override_all = preview_dlg.get_mapping_override() or {}
            skip_paths = preview_dlg.get_skipped_files()
            # 第二步：逐文件按最终映射读取汇总（单个文件读失败不拖垮整批）
            records = []
            n_files = 0
            failed = []
            try:
                from ingest_cleaning import merge_reports
            except Exception:
                # 模块缺失时不阻塞导入：报告合并不了就不传，
                # 同步对话框会自己按同一口径预演一遍（结果一致）
                merge_reports = None
            batch_report: dict = {}
            per_file = {it["path"]: it for it in self._ledger_items(led, pairs)}
            empty_kept = []
            for path, display in pairs:
                if path in skip_paths:
                    continue
                it = per_file.get(path)
                try:
                    self._on_log(f"📂 读取: {display}")
                    rep: dict = {}
                    recs = DataPoolManager.read_records_from_excel(
                        path, mapping_override=override_all.get(path),
                        report_out=rep)
                    if it is not None:
                        it["kept"] = it["rows_kept"] = int(rep.get("kept") or len(recs))
                        it["in"] = it["rows_in"] = int(rep.get("in") or len(recs))
                    # 每个文件的清洗明细逐份进日志（出问题能定位到文件），
                    # 界面上只给一份合并汇总（用户读十几遍同样字段没有意义）
                    if rep:
                        if merge_reports is not None:
                            merge_reports(batch_report, rep)
                        self._on_log(
                            f"  🧪 {display}：读取 {rep.get('in', 0)} → "
                            f"可入库 {rep.get('kept', 0)}，剔除 {rep.get('dropped', 0)}")
                    if recs:
                        records.extend(recs)
                        n_files += 1
                    elif it is not None:
                        # 扫描说「这文件有救」，权威链路真读一条都留不下：
                        # 以权威口径为准记进台账，下次不再重复评估、也不再
                        # 在清单里把它当成待入库（这才是「预估与实际一致」）
                        empty_kept.append(it)
                except Exception as e:
                    failed.append((display, str(e).splitlines()[0][:120]))
            if led is not None and empty_kept:
                try:
                    from local_scan import ST_INVALID
                    led.mark_skipped(empty_kept, ST_INVALID,
                                     "按权威入库链路真读后没有一条可入库记录"
                                     "（多为说明文档/接口文档/纯名单类表格）")
                    self._on_log(f"🧾 {len(empty_kept)} 个文件整表真读后无可入库数据，"
                                 f"已按不合格记入台账")
                except Exception:
                    pass
            if not records:
                QMessageBox.information(self, "没有读到数据",
                    "按当前勾选和列映射，没有读取到任何有效记录。\n"
                    "请重新导入，在预览界面确认文件勾选和「公司名」列的映射。\n\n"
                    + (f"读不了的文件：{len(failed)} 个（详见日志）" if failed else ""))
                return
            # 读不了/被跳过的文件汇总给用户确认（部分成功也要先说清楚）
            problems = failed + list(pre_skipped or [])
            if problems:
                detail = "\n".join(f"· {n}：{r}" for n, r in problems[:8])
                more = (f"\n……等共 {len(problems)} 个"
                        if len(problems) > 8 else "")
                reply = QMessageBox.question(
                    self, "部分文件没能导入",
                    f"能导入的：{n_files} 个文件，共 {len(records)} 条记录。\n\n"
                    f"下面这些读不了，会先跳过：\n{detail}{more}\n\n"
                    f"继续上传能导入的部分吗？",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
                if reply != QMessageBox.Yes:
                    return
            dlg = SyncDialog(records, parent=self,
                             title_prefix=f"导入历史数据（{n_files} 个文件）",
                             cleaning_report=batch_report or None)
            dlg.exec_()
            # ── 台账记账：只有真的传成功的那几个文件才写「已入库」──
            if led is None:
                return
            if not dlg.uploaded_ok:
                self._on_log("ℹ 本次没有成功上传，台账不记账（下次扫描还会照常列出这些文件）")
                return
            ok_paths = [it for it in per_file.values() if it["kept"] > 0]
            if not ok_paths:
                return
            marked = led.mark_ingested(ok_paths, batch=dlg.uploaded_batch)
            self._on_log(f"🧾 本地台账记 {marked} 个文件为已入库"
                         f"（上传 {dlg.uploaded_kept} 条，下次扫描自动跳过这些内容）")
        except Exception as e:
            self._on_log(f"❌ 导入历史数据失败: {e}")
            QMessageBox.critical(self, "导入失败", str(e))

    def _on_pool_scan(self):
        """「扫描本地表格」：全盘扫描 → 清单勾选 → 走与手动导入完全同一条链路。"""
        if not self._acl_gate(edition.pool_ui_visible(), "扫描本地表格"):
            return
        if not HAS_POOL:
            QMessageBox.warning(self, "模块缺失", "数据池模块未加载，无法使用此功能。")
            return
        try:
            dlg = LocalScanDialog(parent=self)
            if dlg.exec_() != QDialog.Accepted:
                return
            items = dlg.selected_items or []
            if not items:
                return
            self._on_log(f"🔎 扫描清单确认：{len(items)} 个文件待入库")
            # 包内成员往下传的是**逻辑位置**（``包名/包内目录/表名.xlsx``）而不是光一个
            # 文件名：列映射预览里两个包里同名的表必须能区分开，且这与手动导入展开
            # 压缩包时的展示口径一致（同一个意思在两条路上只有一种说法）。
            pairs = [(it["path"], it.get("logical") or it["display"])
                     for it in items]
            self._import_pairs_to_pool(pairs)
        except Exception as e:
            self._on_log(f"❌ 扫描本地表格失败: {e}")
            QMessageBox.critical(self, "扫描失败", str(e))

    def _prompt_pool_sync(self, total: int):
        """采集完成后弹出提示：是否同步到数据池？（计划三.4）"""
        reply = QMessageBox.question(
            self,
            "同步到数据池",
            f"本次采集完成，共 {total} 条记录。\n\n是否同步到团队数据池？\n"
            "（同步后其他同事可以搜索到你采集的数据）",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply == QMessageBox.Yes:
            self._on_pool_sync()

    # ── 云展动力：任务登记 / 绑定 / 导入 ───────────────────────
    # ── 自动扩词 / 历史任务续采（任务 = 配置快照 + 关键词级游标）────
    # 对齐大厂批量采集的通用做法：把「一次采集」当成可恢复的任务对象，而不是一次性
    # 进程。配置快照决定下次打开显示什么，关键词级游标决定从哪儿继续，条目级去重
    # 键（各站历史种子库）保证绝不重复烧请求。
    def _set_auto_expand(self, on):
        """扩词生效值的唯一写入口：控件在就同步控件，控件不在（对外档位）只写数据。
        读侧一律走 ``self._auto_expand``，没有第二个来源。"""
        self._auto_expand = bool(on)
        w = self.chk_auto_kw
        if w is not None and w.isChecked() != self._auto_expand:
            w.setChecked(self._auto_expand)   # toggled 会回调本方法，值相同即止

    def _params_snapshot(self):
        """采集参数快照：续采时照原样恢复，不该让用户为接着采再调一遍参数。"""
        return {"delay_between": float(self.spin_delay.value()),
                "relevance_guard": bool(self.chk_guard.isChecked()),
                "incremental": bool(self.chk_incr.isChecked()),
                "background": bool(self.chk_bg.isChecked()),
                "auto_keyword_expand": bool(self._auto_expand),
                "province": list(self.selected_provinces or [])}

    def _apply_params(self, params):
        if not isinstance(params, dict):
            return
        try:
            if params.get("delay_between"):
                self.spin_delay.setValue(float(params["delay_between"]))
            for key, widget in (("relevance_guard", self.chk_guard),
                                ("incremental", self.chk_incr),
                                ("background", self.chk_bg)):
                if key in params:
                    widget.setChecked(bool(params[key]))
            # 扩词单独走 _set_auto_expand：控件在就顺带勾回界面，控件不在（对外档位）
            # 也要把生效值恢复出来——快照里记着什么就照用什么，不能让 None 控件吃掉它。
            if "auto_keyword_expand" in params:
                self._set_auto_expand(params["auto_keyword_expand"])
            # 省份精准采集（多选）：老快照可能是字符串（单省）或列表（多选），统一兼容
            pv_raw = params.get("province") or ""
            if isinstance(pv_raw, list):
                self.selected_provinces = [str(p) for p in pv_raw if str(p).strip()]
            elif isinstance(pv_raw, str) and pv_raw.strip():
                # 兼容老快照：单省字符串转列表
                self.selected_provinces = [p.strip() for p in pv_raw.split(",") if p.strip()]
            else:
                self.selected_provinces = []
            self._refresh_province_label()
        except Exception:
            pass

    def _auto_expand_on_start(self):
        """点「开始采集」前自动展开关键词。
        只在勾选「自动扩词」且真展开出新词时改词表，改完写回输入框——用户看得见
        改成什么了，随时能删改；不想要就取消勾选，完全按原词采。
        判据读 ``self._auto_expand`` 而不是读勾选框：对外档位没有那个勾选框，但扩词
        本身照旧生效（隐藏的是入口，不是产出口径；行为与提示必须一致，不能悄悄改词
        却不写回，那会让「框里 1 个词、实际跑 30 个词」变成无从核对的现象）。"""
        if not self._auto_expand:
            return
        raw = self.kw_text.toPlainText()
        if not raw.strip():
            return
        try:
            rep_ = _kwexp.expand_keywords(raw)
        except Exception as e:
            self._on_log(f"⚠ 自动扩词失败（按你填的原词继续采）：{str(e).splitlines()[0][:120]}")
            return
        kws = list(rep_.get("keywords") or [])
        if not kws or kws == self.keywords:
            return
        # 框里就是上次展开写回的内容时不再二次展开：否则连点两次「开始采集」，
        # 词表会在用户没动过的情况下变长（第二轮会再匹配出别的行业类别）。
        if self._kw_expanded_text and raw == self._kw_expanded_text:
            return
        before = len(self.keywords)
        self._kw_raw = raw                      # 保留展开前的原始输入
        self._kw_expanded_text = chr(10).join(kws)
        self.kw_text.setPlainText("\n".join(kws))
        self.keywords = kws
        # 标签只留一行（两行以上在小窗口会被裁成残句），完整说明写在日志里
        self.lbl_kw.setText(f"自动扩词：{before} 个 → {len(kws)} 个产品词"
                            "（已写回关键词框，可删改，请对照下方日志核查）")
        self._on_log(f"⚠ {self._SMART_HINT}")
        self._on_log(f"🪄 自动扩词：{before} 个词 → {len(kws)} 个产品词"
                     f"（展开 {len(rep_.get('expanded') or [])} 个行业类别）。")
        for seg, name, added in (rep_.get("expanded") or [])[:12]:
            self._on_log(f"   ・「{seg}」→ 归入【{name}】，新增 {added} 个产品词")
        if rep_.get("dropped"):
            self._on_log(f"   ・已忽略（纯服务/流程词，站内没有对应商品）："
                         f"{'、'.join(rep_['dropped'])}")
        if rep_.get("truncated"):
            self._on_log(f"   ↳ 词表已达单次上限（{len(kws)} 个）自动收住："
                         "先采完这批再补下一批更安全（一次灌上千个词会把请求量放大几十倍）。")
        # 这一句在告诉用户「怎么关掉换词」。对外档位没有那个勾选框，照原样说出来就是
        # 在日志里报一个界面上不存在的控件——按钮藏得再好，日志一句话就把功能交底了。
        # 所以文案跟着 ``auto_expand_visible()`` 走：有勾选框就教怎么取消，没有就给一条
        # 用户真能做的替代动作（自己把类别名写成产品名），不编一个不存在的开关。
        if edition.auto_expand_visible():
            self._on_log("   ↳ 不想自动换词：取消勾选「自动扩词」，就按你填的原词采。")
        else:
            self._on_log("   ↳ 想只按自己填的词采：把行业类别名改成具体产品名再点开始采集。")

    def _boost_high_yield(self, min_yield=20):
        """产出反馈闭环：把本轮净新增 ≥ min_yield 家的高产词，按同族派生一批近义词
        追加进关键词框。刻意不自动开下一轮——一夜之间把请求量放大好几倍会抬高防采
        风险，所以只把词备好，由用户点「开始采集」决定要不要继续。"""
        boost = getattr(_kwexp, "boost_high_yield", None)
        if not callable(boost) or not self.session_dir:
            return
        try:
            pairs = self.session.top_keywords(limit=60) or []
        except Exception:
            return
        kw_yield = {}
        for k, n in pairs:
            kw_yield[str(k)] = int(n or 0)
        if not kw_yield:
            return
        try:
            res = boost(list(self.keywords), kw_yield, min_yield=min_yield)
        except Exception as e:
            self._on_log(f"⚠ 高产词派生新词失败：{str(e).splitlines()[0][:120]}")
            return
        added = res.get("added") or []
        if not added:
            return
        new_words = [w for _k, _n, ws in added for w in ws]
        self.keywords = list(res.get("keywords") or self.keywords)
        self.kw_text.setPlainText("\n".join(self.keywords))
        self.lbl_kw.setText(f"高产词派生：新增 {len(new_words)} 个同族产品词（已写回关键词框）。"
                            f"共 {len(self.keywords)} 个词")
        for kw, n, sib in added[:8]:
            self._on_log(f"   ・「{kw}」净新增 {n} 家 → 同族派生：{'、'.join(sib)}")
        self._on_log(f"📈 已把 {len(added)} 个高产词的同族近义词（共 {len(new_words)} 个）加进关键词框："
                     "点「开始采集」就顺着往下采；不需要的话现在删掉即可。")

    def _persist_progress(self, status=None):
        """把「关键词/站点/参数 + 每词进度」写回任务文件夹和任务索引。
        自动保存、暂停、结束、退出前都会调，保证下次打开看到的进度是最新的。"""
        if not self.session_dir:
            return
        try:
            self.session.set_task_folder(self.session_dir)
            self.session.save_progress()
        except Exception as e:
            self._on_log(f"⚠ 采集进度保存失败（不影响本次数据）：{str(e).splitlines()[0][:100]}")
        self._register_task(status)

    def _restore_site_checks(self, sites):
        """按任务快照恢复「③ 采集平台」勾选态：这是**程序在恢复状态**，不是用户点了勾选框。

        1688 的 toggled 上挂着「先去登录」模态弹窗，恢复期间必须屏蔽信号：否则每次启动
        接着上次任务都会被顺手弹一次（用户什么都没点就被打断），而自动化/offscreen 下这个
        模态框会永久卡住整个回填流程（实测启动停在 _restore_task 里出不来）。
        空 sites 保持界面现状不动（老任务快照没记站点时不能把平台全关掉）。
        """
        if not sites:
            return
        for _chk in self.site_checks.values():
            _chk.blockSignals(True)
        try:
            for slug, chk in self.site_checks.items():
                chk.setChecked(slug in sites)
        finally:
            for _chk in self.site_checks.values():
                _chk.blockSignals(False)

    def _restore_last_task(self):
        """启动自动回填：取最近一条「能接着采」的任务，静默恢复关键词/参数/进度。"""
        if not HAS_YZD:
            return
        try:
            tasks = [t for t in _tasks.load_tasks()
                     if t.get("status") in _tasks.RESUMABLE_STATUS
                     and (t.get("keywords") or [])
                     and self._session_folder_ok(str(t.get("folder") or ""))]
        except Exception:
            return
        if not tasks:
            return
        tasks.sort(key=lambda t: str(t.get("started_at") or ""), reverse=True)
        last = tasks[0]
        folder = str(last.get("folder") or "")
        if not folder or not os.path.isdir(folder):
            return
        # 恢复成功后不再补一条「已自动显示上次任务…」日志：_restore_task 内部已经打过
        # 一条带全部分项数字的 ↻，横幅也已经把「点开始采集即接着采」说给用户了。同一件事
        # 在启动那一屏留三条（两条日志 + 一条横幅）时，第二条起就只是噪音；而任务名本身
        # 常带「N 个关键词」，再拼一次就成了「13 个关键词（13 个关键词）」这种读起来像 bug
        # 的句子（离线出图当场看到）。
        self._restore_task(last, silent=True)

    def _restore_task(self, task, silent=False):
        """按任务恢复现场：关键词/站点/参数 + 逐词游标 + 已采数据装回内存。
        恢复完只等用户点「开始采集」——那时各站会跳过已跑完的关键词，从未采的继续。
        silent=True 用于启动自动回填：不打扰，只在日志和横幅里说明。"""
        folder = str(task.get("folder") or "")
        if folder and not self._session_folder_ok(folder):
            # 索引里混进非工具目录下的条目（离线测试造的临时任务）时，无论
            # 启动回填还是手动选历史任务，都不许把它当会话目录接着写数据。
            if silent:
                self._on_log("↻ 上次任务的数据文件夹不在工具目录下，未自动回填：" + folder)
            else:
                QMessageBox.warning(self, "没法接着采",
                                    f"这条任务的数据文件夹不在工具的采集目录里：\n{folder}\n\n"
                                    "请重新点「开始新任务」采一批。")
            return False
        if not folder or not os.path.isdir(folder):
            msg = (f"找不到上次的数据文件夹：\n{folder}\n\n"
                   "文件夹被删除或移动过就接不上了，请重新点「开始新任务」采一批。")
            if silent:
                self._on_log("↻ 上次任务的数据文件夹已不存在，未自动回填：" + folder)
            else:
                QMessageBox.warning(self, "没法接着采", msg)
            return False

        self._first_start = task.get("id") or Path(folder).name
        self.session_dir = folder
        self._task_id = task.get("id") or self._first_start
        self.session.set_task_folder(folder)
        data = self.session.load_progress(folder) or {}

        # 词表：进度文件里的词最权威（就是上一轮实际参与采集的那批），
        # 任务索引里的词兜底（老任务可能只有索引）。
        kws = [str(k).strip() for k in ((data.get("keywords") or task.get("keywords") or []))
               if str(k).strip()]
        # 老进度文件里存的是当年那版解析器放行过的词，其中混着行业报告的文档碎片
        # （'#### 【TOP3 核心装备'、'平台类】'、机构名……实机 92 个词里 15 个）。这条
        # 回填路径不经过 parse_keywords，所以必须在这里按同一判据再洗一遍，否则新规则
        # 对「继续上次任务」等于没装：碎片照样进下一轮，继续白烧跨国采购网那约 50 页配额。
        # 抽验过碎片搜出来的东西：'平台类】' → 展览服务公司（主营「出口电商平台类展区」）、
        # 机床量具厂（主营「铸铁平板平台类」）——是要清的噪音，不是多出来的数据。
        _junk = [k for k in kws if CdpSiteCrawler._kw_junk_reason(k)]
        if _junk:
            kws = [k for k in kws if not CdpSiteCrawler._kw_junk_reason(k)]
            show = "、".join(_junk[:6]) + ("…" if len(_junk) > 6 else "")
            self._on_log(f"🧹 接着上次采之前，已剔掉 {len(_junk)} 个不是关键词的碎片"
                         f"（{show}）：这些是报告标题/切坏的半截括号/机构名，站内搜索"
                         "搜不出对口产品，却每个都要花真实请求。")
        if not kws:
            if silent:
                return False
            QMessageBox.information(self, "这是旧版本的任务",
                                    "这条任务没有记下关键词（老版本只记名字和条数）。\n"
                                    "请把关键词重新填回输入框再采集；上次已采的数据仍在数据文件夹里。")
            return False
        self._kw_raw = task.get("kw_raw") or "\n".join(kws)
        self.kw_text.setPlainText("\n".join(kws))
        self.keywords = list(kws)
        self._kw_source_file = ""
        self._kw_expanded_text = chr(10).join(kws)

        sites = [s for s in ((data.get("sites") or task.get("sites") or [])) if s in self.site_checks]
        self._restore_site_checks(sites)
        self._apply_params(task.get("params") or {})

        resumed = self.session.apply_progress() if data else 0
        done_n = _tasks.done_keywords(task) if HAS_YZD else 0
        seeded = 0
        raw_path = task.get("raw_file") or os.path.join(folder, _tasks.RAW_FILE_NAME)
        if os.path.exists(raw_path):
            try:
                seeded = self.session.seed_results(_tasks.read_records_from_xlsx(raw_path))
            except Exception as e:
                self._on_log(f"⚠ 读回上次数据失败（不影响接着采，只是本轮不会累加显示旧数据）："
                             f"{str(e).splitlines()[0][:120]}")
        # 关键词名单本身就在上面那个输入框里，这里再抄一遍就是把同一个事实摆成三份
        # （输入框 + 状态行 + 横幅），而横幅与状态行都是换行显示，白吃掉两行竖向空间——
        # 用户要的是「一屏看全」，界面就不该用重复内容把它撑爆。
        self.lbl_kw.setText(f"已接上次任务：{len(kws)} 个关键词（已跑完 {done_n} 个）")
        self._on_log(f"↻ 恢复任务：{task.get('name') or self._first_start}｜{len(kws)} 个关键词"
                     f"｜已跑完 {done_n} 个｜已采 {seeded} 条装回内存｜{resumed} 个通道已接上游标")
        self._show_banner(
            f"已接上次的任务（{len(kws)} 个关键词、已采 {seeded} 条）："
            "点「开始采集」就从上次停下的地方接着采，已采过的商家不会重复采。",
            "#e8f5e9", "#1b5e20")
        return True

    def _on_history_tasks(self):
        """历史任务列表：选中一条 →「接着采」恢复关键词与进度（数据写回原文件夹）。"""
        if not self._acl_gate(edition.history_visible(), "历史任务"):
            return
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "提示",
                                    "采集进行中。请先点「停止」并等日志出现「采集完成/已停止」"
                                    "后再选历史任务。")
            return
        tasks = []
        if HAS_YZD:
            try:
                tasks = list(_tasks.load_tasks())
            except Exception as e:
                QMessageBox.warning(self, "读不到历史任务", f"任务索引读取失败：{e}")
                return
        if not tasks:
            QMessageBox.information(self, "还没有历史任务",
                                    "以前采过的任务会列在这里（关键词、平台勾选、采集间隔、采到哪儿）。\n"
                                    "第一次采集完成后就有记录，之后随时能选回来接着采。")
            return
        tasks.sort(key=lambda t: str(t.get("started_at") or ""), reverse=True)

        dlg = QDialog(self)
        dlg.setWindowTitle("历史任务 · 选一条接着上次采")
        dlg.setModal(True)
        dlg.resize(860, 520)
        dv = QVBoxLayout(dlg)
        dv.setSpacing(10)
        dv.addWidget(QLabel(
            "点一下（或用键盘上下方向键）选中一条任务：选中的那条会变成「实心蓝点 + 淡蓝底 + 左边一条蓝竖线」，\n"
            "右下角也会立刻写明要接着采哪一条，然后按回车或点「接着采这一条」。\n"
            "整行发灰、带红标签的那几条是数据文件夹已经不在、接不上的任务，选不了。"))
        dlg.setMinimumSize(720, 420)
        lst = QListWidget()
        lst.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        lst.setSelectionMode(QAbstractItemView.SingleSelection)
        lst.setMouseTracking(True)
        # 选中态改由行控件自己画：macOS 上弹窗一失焦，QListWidget 原生那条高亮
        # 就褪成几乎看不见的浅灰——客户反馈的「选择标识不明显」根子就在这。
        lst.setStyleSheet(
            "QListWidget { border: 1px solid palette(mid); border-radius: 6px;"
            " padding: 4px; background: palette(base); }"
            "QListWidget::item { border: none; background: transparent; }"
            "QListWidget::item:selected { background: transparent; color: palette(text); }"
            "QListWidget::item:focus { border: none; outline: none; background: transparent; }")
        rows = []
        cur_id = str(self._current_task_id() or "")
        for t in tasks:
            folder = str(t.get("folder") or "")
            exists = os.path.isdir(folder)
            kws = [str(k) for k in (t.get("keywords") or []) if str(k).strip()]
            done_n = _tasks.done_keywords(t) if (kws and exists) else 0
            v = task_row_view(t, exists, done_n, cur_id)
            it = QListWidgetItem()
            it.setData(Qt.UserRole, t)
            it.setData(Qt.UserRole + 1, v)
            rw = TaskRow(v)
            it.setSizeHint(rw.sizeHint())
            if not v["selectable"]:
                # 不给任何标志位：实测只留 ItemIsEnabled 时，点一下「当前项」照样会
                # 停在这条上并画出淡色框，用户会误以为选中了（本次要修的正是这个）。
                it.setFlags(Qt.NoItemFlags)
            lst.addItem(it)
            lst.setItemWidget(it, rw)
            rows.append((it, rw))

        def _fit_rows(*_a):
            """行宽跟着列表视口走：行控件按整段文字算出来的宽度比弹窗还宽，
            右侧的状态标签和「已采 N 家」会被整块裁到外面——而它们正是选中标识。"""
            w0 = max(360, lst.viewport().width() - 2)
            for it0, rw0 in rows:
                h0 = rw0.sizeHint().height()
                if it0.sizeHint().width() != w0 or it0.sizeHint().height() != h0:
                    it0.setSizeHint(QSize(w0, h0))
                if rw0.width() != w0:
                    rw0.resize(w0, h0)
                    rw0._elide()

        lst.viewport().installEventFilter(_ResizeWatcher(_fit_rows, lst))
        _fit_rows()

        hint = QLabel()
        hint.setWordWrap(True)
        hint.setStyleSheet(_muted_style())
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_ok = bb.button(QDialogButtonBox.Ok)
        btn_ok.setText("接着采这一条")
        btn_ok.setObjectName("primary")
        btn_ok.setMinimumHeight(34)
        btn_ok.setDefault(True)
        bb.button(QDialogButtonBox.Cancel).setText("关闭")
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)

        def _sync(*_a):
            """选中变化就把四件事一起刷新：行高亮、回执文字、按钮可用与按钮上的目标。"""
            cur = lst.currentItem()
            for it0, rw0 in rows:
                rw0.set_selected(it0 is cur)
            v = cur.data(Qt.UserRole + 1) if cur is not None else None
            # current 与 selected 是两回事：灰行也会被设成当前项，这里再判一次
            # selectable，绝不把接不上的任务当成「用户已经选好了」。
            if isinstance(v, dict) and v.get("selectable"):
                tail = "　·　".join(x for x in (v["progress_text"], v["count_text"]) if x)
                hint.setText(f"将接着采：{v['title']}（{tail}）")
                hint.setStyleSheet(_muted_style())
                btn_ok.setEnabled(True)
            else:
                hint.setText("请先选中一条能接着采的任务（左侧圆圈是空心的那几条）。")
                hint.setStyleSheet(_danger_style())
                btn_ok.setEnabled(False)

        def _on_press(idx):
            """点在接不上的灰行上：不改选中，但把「为什么接不上」讲在回执里——
            否则用户连点两下没反应，只会以为按钮坏了。灰行不发 clicked 信号，
            只能自己在视口上命中测试。点正常行则把回执刷回真正选中的那条。"""
            it0 = lst.itemFromIndex(idx) if idx.isValid() else None
            v = it0.data(Qt.UserRole + 1) if it0 is not None else None
            if isinstance(v, dict) and not v.get("selectable"):
                hint.setText("这条接不上：" + str(v.get("tooltip") or "").split("\n")[0]
                             + "　（要接着采的还是之前选中的那条，想换就点带空心圆圈的行）")
                hint.setStyleSheet(_danger_style())
            else:
                _sync()

        lst.currentItemChanged.connect(_sync)
        lst.viewport().installEventFilter(_PressWatcher(lst, _on_press, lst))
        lst.itemDoubleClicked.connect(
            lambda it0: dlg.accept() if (it0.data(Qt.UserRole + 1) or {}).get("selectable") else None)
        bottom = QHBoxLayout()
        bottom.setSpacing(10)
        bottom.addWidget(hint, 1)
        bottom.addWidget(bb, 0, Qt.AlignBottom)
        dv.addWidget(lst, 1)
        dv.addLayout(bottom)
        # 默认就把该选的那条摆好：已经接在界面上的那条优先（用户点进来多半是想
        # 确认「我接对了吗」），否则选第一条能接着采的，别让用户先手动点一下
        want = -1
        for i, (it0, _rw) in enumerate(rows):
            tid0 = str((it0.data(Qt.UserRole) or {}).get("id") or "")
            if (it0.flags() & Qt.ItemIsSelectable) and tid0 == cur_id:
                want = i
                break
        if want < 0:
            for i, (it0, _rw) in enumerate(rows):
                if it0.flags() & Qt.ItemIsSelectable:
                    want = i
                    break
        lst.setCurrentRow(want)
        _fit_rows()
        _sync()
        if dlg.exec_() != QDialog.Accepted:
            return
        chosen = None
        it = lst.currentItem()
        if it is not None:
            chosen = it.data(Qt.UserRole)
        if not isinstance(chosen, dict):
            QMessageBox.information(dlg, "提示", "请先在列表里选一条可以接着采的任务。")
            return
        self._restore_task(chosen)

    def _current_task_id(self):
        return Path(self.session_dir).name if self.session_dir else ""

    def _session_folder_ok(self, folder):
        """会话目录不变量：一次采集的数据夹必须是桌面工具目录
        「B2B三站企业数据自动采集工具」的直接子目录——新建任务就是这么建的。
        实机事故：离线测试在 /var/folders 临时目录造的假任务混进了正式任务
        索引，启动回填选中它后，整轮真实采集（89 条）被写进了随系统清理的
        临时目录。回填和登记两侧都按这条不变量把关，测试数据从此进不了真实链路。"""
        if not HAS_YZD or not folder:
            return bool(folder)
        try:
            base = _tasks.tool_dir(self._get_desktop_dir())
            return Path(folder).resolve().parent == base.resolve()
        except Exception:
            return False

    def _register_task(self, status=None):
        """登记/刷新任务索引：除了名字和条数，还要存下「关键词、站点、参数」这份
        配置快照——没有它，关掉工具就什么都不剩，续采无从谈起。"""
        if not HAS_YZD or not self.session_dir:
            return
        if not self._session_folder_ok(self.session_dir):
            # 见 _session_folder_ok：正常采集永远命中不了，只有离线测试或索引
            # 被外部改坏时才会走到这里——宁可不登记，也不让假条目进用户的历史。
            self._on_log(f"⚠ 数据文件夹不在工具目录下，本次不登记任务索引：{self.session_dir}")
            return
        try:
            tid = self._current_task_id()
            name_src = [self._kw_source_file] if self._kw_source_file else list(self.keywords)
            task = _tasks.new_task(tid, name_src, self.session_dir,
                                   count=len(self.session.snapshot_results()),
                                   keywords=list(self.keywords),
                                   sites=self._enabled_sites(),
                                   params=self._params_snapshot(),
                                   kw_raw=self._kw_raw or "")
            existing = next((t for t in _tasks.load_tasks() if t.get("id") == tid), None)
            if existing:
                task["started_at"] = existing.get("started_at") or task["started_at"]
                # 状态只在调用方明确指定时才改，否则保留上次的（别让自动保存把
                # 「已停止」刷回「采集中」，用户在历史列表里会看不懂）。
                task["status"] = status or existing.get("status") or task["status"]
                task["finished_at"] = existing.get("finished_at") or task["finished_at"]
            elif status:
                task["status"] = status
            if not self._kw_source_file and self.keywords:
                # 关键词任务的名字：make_task_name 是给「上传的文件」设计的，
                # 直接拿词表去套会写成「制冷压缩机 等 85 个文件」——用户填的是词
                # 不是文件，历史列表里这句话会让人以为软件在采文档。
                k0 = str(self.keywords[0])
                task["name"] = k0 if len(self.keywords) == 1 else f"{k0} 等 {len(self.keywords)} 个关键词"
            _tasks.upsert_task(task)
            self._task_id = tid
            if existing is None:
                self._on_log(f"📁 已登记采集任务：{task['name']}")
        except Exception as e:
            self._on_log(f"⚠ 采集任务登记失败：{e}")

    def _finish_task(self, status, count):
        if not HAS_YZD or not self._task_id:
            return
        try:
            if _tasks.finish_task(self._task_id, status, count):
                self._on_log(f"📁 任务「{status}」已记录：合计 {count} 条")
        except Exception as e:
            self._on_log(f"⚠ 任务结束状态写入失败：{e}")

    def _refresh_yzd_status(self):
        if not hasattr(self, "yzd_status_lbl"):
            return
        if not HAS_YZD:
            self.yzd_status_lbl.setText("云展模块未加载（缺 yzd_client / task_registry）")
            self.yzd_status_lbl.setStyleSheet(_status_badge_style("error"))
            self.bind_yzd_btn.setEnabled(False)
            self.import_yzd_btn.setEnabled(False)
            return
        if self._yzd_phone:
            self.yzd_status_lbl.setText(f"已绑定云展账号：{self._yzd_phone}")
            self.yzd_status_lbl.setStyleSheet(_status_badge_style("ok"))
            self.bind_yzd_btn.setText("更换绑定账号")
        else:
            self.yzd_status_lbl.setText("未绑定云展动力账号")
            self.yzd_status_lbl.setStyleSheet(_status_badge_style("neutral"))
            self.bind_yzd_btn.setText("绑定云展动力账号")
        self._refresh_acl_status()

    # ── 权限：状态徽章 / 心跳 / 顶号收尾 / 等级收口 ──────────────────────────

    def _refresh_acl_status(self):
        """管理员徽章：本机跑在哪一档、是不是该账号当前的绑定设备。

        只给等级 0 的账号看（控件本身就是按能力位创建的，其他档位根本没有这块）。
        这条信息对管理员是**取证**用的：改完名单要能一眼确认生效了没有，否则只能靠
        「点一下按钮看会不会被拦」猜，而猜的过程恰好会把人误导到点击路径的缓存上。
        """
        lbl = getattr(self, "acl_status_lbl", None)
        if lbl is None or not HAS_ACL_GUARD:
            return
        try:
            guard = _access_guard.get_guard()
            phone = guard.account()
            lv = guard.level()
            parts = [f"权限：{edition.level_label(lv)}"]
            if not phone:
                parts.append("未绑定账号")
            else:
                # wait=False：徽章不是用户点出来的，绝不能替它把 UI 线程按住 3 秒等网络
                ok, reason = guard.check(wait=False)
                parts.append("本机 ✓" if ok else "本机 ✕")
                if not ok:
                    tip = (reason or "").splitlines()[0][:60]
                    parts.append(tip)
                    self.acl_status_lbl.setStyleSheet(_status_badge_style("error"))
                else:
                    self.acl_status_lbl.setStyleSheet(_status_badge_style("ok"))
            lbl.setText("｜".join(parts))
            lbl.setToolTip("当前账号在 OSS 白名单里的等级，以及本机是不是它的绑定设备。\n"
                           + ("判定：" + (reason or "正常") if phone else "尚未绑定云展动力账号"))
        except Exception as e:
            lbl.setText(f"权限：自查失败（{type(e).__name__}）")

    def _acl_heartbeat(self):
        """60 秒一次的保底：催一次刷新，再交出上一轮已经落下的顶号事件。

        这里调 ``tick()``（会安排后台刷新）而不是直接读信箱，为的就是信号万一没接上
        ——比如守卫模块加载失败、或者刷新那一刻界面正忙——心跳还能兜第二次。
        """
        if not HAS_ACL_GUARD:
            return
        try:
            self._on_acl_event(_access_guard.get_guard().tick())
        except Exception as e:
            # 心跳本身出问题绝不能拖垮采集，也绝不能被吞掉——它悄悄停摆就等于
            # 顶号检测失效，而那只能由下一次心跳的日志发现
            self._on_log(f"⚠ 权限心跳异常（不影响已在跑的采集）：{type(e).__name__}: {e}")

    def _on_acl_refreshed(self):
        """后台刷新一结束就叫醒：刚被顶号的机器不必干等下一拍心跳。

        信号只负责唤醒，不带结论——结论仍然由 ``take_revoked()`` 这一份一次性信箱
        交出。所以心跳与信号两条路同时到达也只弹一次：后到的那次取到的是空串。
        """
        if not HAS_ACL_GUARD:
            return
        try:
            self._on_acl_event(_access_guard.get_guard().take_revoked())
        except Exception as e:
            self._on_log(f"⚠ 权限刷新回调异常（不影响已在跑的采集）：{type(e).__name__}: {e}")

    def _acl_startup_reconcile(self):
        """开机 1.5s 的一次轻量收口（配合 prefetch_authoritative 的建界面即定档）。

        只处理「事件已经在信箱里、但发出那一刻信号还没连上」的竞态：取一次顶号信箱、
        再按当前 edition 与已建入口比对收口。绝不在这里再发一次网络请求——权威判定
        开机已经做过，这里纯粹是把可能漏听的结论捡回来。
        """
        if not HAS_ACL_GUARD:
            return
        try:
            self._on_acl_event(_access_guard.get_guard().take_revoked())
        except Exception as e:
            self._on_log(f"⚠ 开机权限收口异常（不影响已在跑的采集）：{type(e).__name__}: {e}")

    def _on_acl_event(self, msg: str):
        """顶号与等级变更的统一处理体（心跳、refreshed 信号共用同一份逻辑）。

        顺序不能反：先把「停止收尾」做完再收口界面，否则一边在收尾落盘、一边把
        正在用的入口隐藏掉，日志里会出现两套互相解释不了的动作。
        """
        if msg:
            self._on_acl_revoked(msg)
        self._acl_sync_level()
        self._refresh_acl_status()

    def _on_acl_revoked(self, reason):
        """被顶号的收尾：干净停手 → 落盘 → 说清楚为什么、下一步怎么办。

        顺序很重要。硬杀进程最省事，代价是那一批数据停在半路（用户视角就是
        「软件把我的活干了一半扔了」）；只弹窗不停手更糟——它还会继续跑十几分钟。
        所以走既有的「停止 → _on_finished → 自动保存 → 进度落盘」那条链路。
        """
        self._on_log("⛔ " + reason.replace("\n", " "))
        try:
            if (self.worker and self.worker.isRunning()) or self._paused:
                self._manual_stop = True
                self.session.stop_crawl()
                self._on_log("已按停止流程收尾：本轮数据与进度照常落盘，不会丢。")
        except Exception as e:
            self._on_log(f"⚠ 收尾停止失败（请手动点「停止」）：{type(e).__name__}")
        self._acl_stopped = True
        self._refresh_acl_status()
        # 这条链路是**后台事件**（心跳 / guard.refreshed 信号）叫醒的，用户什么都没点，
        # 所以这里绝不能用 QMessageBox.warning 这种模态写法（2026-11-12 实测两处事故）：
        #   1) 模态 = 嵌套事件循环，主线程被接管，心跳、进度、停止收尾全在框后排队——
        #      「先把活收尾」和「拦住用户」的顺序反了，采集数据可能停在半路；
        #   2) offscreen / 无人值守（CI 界面自检、远程排障）下 exec() 永久等一次根本不会
        #      来的点击，进程直接挂死：比崩溃更难查，因为日志看起来像「跑到一半没声音」。
        # 与拦截提醒同一套做法：常驻一个非模态框，说清楚「发生了什么 + 数据在不在 +
        # 下一步做什么」，用户点不点它，工具都已经按自己的节奏收好尾。
        if self._acl_revoked_box_shown:
            return
        self._acl_revoked_box_shown = True
        box = getattr(self, "_acl_revoked_box", None)
        if box is None:
            box = QMessageBox(self)
            box.setModal(False)
            box.setIcon(QMessageBox.Critical)
            box.setWindowTitle("本机使用权限已中止")
            box.setStandardButtons(QMessageBox.Ok)
            box.button(QMessageBox.Ok).setText("知道了")
            self._acl_revoked_box = box
        box.setText(reason + "\n\n"
                    "本轮已采到的数据与进度都按停止流程落盘，不会丢。\n"
                    "如果确实是你要换到这台电脑上用：点主界面「绑定账号」重新绑定即可把使用权限"
                    "转到本机（转到本机后另一台会停用）。")
        box.show()
        box.raise_()
        box.activateWindow()

    def _acl_sync_level(self):
        """等级变了就当场收口：多出来的入口立刻隐掉，缺的入口提示重启补齐。

        这里必须存在而不能只依赖建界面时的投影：等级是拉到名单之后才知道的，
        开机第一屏永远可能是「按上一份副本/默认档」建的。收口解决「已经摆出来的
        不该有」，重启解决「本该有的没建出来」——两者都不能省。
        """
        cur = edition.caps_snapshot()
        if cur == self._acl_caps:
            return
        lost = [c for c, v in self._acl_caps.items() if v and not cur.get(c)]
        gained = [c for c, v in cur.items() if v and not self._acl_caps.get(c)]
        for cap in lost:
            for w in self._acl_gated.get(cap, []):
                w.setVisible(False)
        for cap in gained:
            for w in self._acl_gated.get(cap, []):
                w.setVisible(True)
        self._acl_caps = cur
        # 标题跟着生效档位重新投影：降到受限档后界面就是「精简版那一组能力」，标题还
        # 写着全功能版，用户会以为是功能坏了；反过来外部用户看到无后缀的标题，会以为
        # 拿到的是完整版并向你要更新。文案与入口必须由同一份投影给出（见 edition 第 5 条）。
        new_title = self._base_title + edition.title_suffix()
        if new_title != self.windowTitle():
            self.setWindowTitle(new_title)
        # ② 的标题/tooltip 与「选择省份」的 tooltip 跟着重投影：它们报的都是「现在
        # 界面上有几路补采平台」，等级一变这个集合就变了，留着旧文案等于说假话
        # （降级时泄露内部平台名，升级时又承诺界面上还没有的东西）。
        # 顺带补一次缺路播报：按集合去重，正常机器上一条也不会多。
        self._apply_list_channel_hint()
        self._log_channel_health()
        # 收不掉的那一类：控件当初压根没创建（构建时等级不够），显示不出来
        need_restart = bool(lost) or any(not self._acl_gated.get(c) for c in gained)
        if not need_restart:
            self._on_log(f"ℹ 权限等级已更新，界面无需调整（{'、'.join(gained)} 已恢复显示）")
            return
        self._acl_pending = True
        self._on_log("ℹ 账号权限等级已变更，多余入口已当场收起；缺失的入口需要重启工具才会补齐。")
        # 这里绝不能再弹 QMessageBox.question：这条链路是后台刷新/心跳唤醒的，
        # 弹窗等于「用户什么都没点，界面自己蹦出一个必须回答的框」——正跑着采集时
        # 它会用嵌套事件循环把主线程接管过去（心跳、进度、停止全在模态里排队），
        # offscreen 回归里更是直接永久挂住（实测把整批拖死 600s 被 SIGABRT）。
        # 提示改在横幅上给一个可点的「重启工具现在生效」，问不问由用户决定。
        self._show_banner(
            "权限等级已变更：多余入口已收起，重启工具后界面才会完整对齐。"
            '<a href="#acl-restart">重启工具现在生效</a>',
            "#fff3cd", "#7a5a00")

    def _acl_prompt_restart(self) -> bool:
        """问一次要不要重启，返回 True = 已安排重启。同一进程只问一次，
        不然每次心跳都弹一回就成了骚扰（用户拒了之后就靠横幅与日志提示）。

        调用点只有一个：横幅上那条「重启工具现在生效」链接（用户主动点击）。
        后台链路一律不许走到这里——判据由 debug/edition_offline_test.py 钉住。
        """
        if getattr(self, "_acl_restart_asked", False):
            return False
        self._acl_restart_asked = True
        ret = QMessageBox.question(
            self, "权限等级已变更",
            "当前账号的权限等级与界面已建好的入口不一致：\n"
            "· 多出来的入口已经当场收起，用不了；\n"
            "· 本该有却没建出来的入口，需要重启工具才会出现。\n\n"
            "现在重启吗？（会先把本轮数据与进度落盘，再退出、再自动打开）",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret == QMessageBox.Yes:
            self._relaunch_tool()
            return True
        return False

    def _relaunch_tool(self):
        """安全收尾后重开一个实例：先走 closeEvent 那条落盘链路，再起新进程。

        为什么用「延时拉起 + 自己退出」而不是 os.execv：execv 会把进程原地换掉，
        closeEvent（停止采集、等静默上传、进度落盘、关浏览器）一次都不会跑——
        那正是最不能省的一部分。
        """
        cmd = self._relaunch_command()
        if not cmd:
            QMessageBox.information(self, "需要手动重启",
                                    "没能确定本工具的启动方式，请直接关掉窗口再重新打开一次。")
            return
        self._relaunch_pending = cmd
        self.close()          # closeEvent 真退出时才把新进程拉起来

    def _relaunch_command(self):
        """新实例怎么启动：冻结态直接起自身可执行文件，源码态起解释器 + 本脚本。

        打包态这里取的是 ``sys.executable``——onedir 产物里它就是磁盘上那个真实 exe，
        不再指向任何临时解包目录。（这也是本轮把 Windows 从 onefile 改成 onedir 的
        原因之一：onefile 下「重启自己」这件事天生要跟 %TEMP% 的回收时机赛跑。）
        """
        try:
            if getattr(sys, "frozen", False):
                return [sys.executable]
            return [sys.executable, str(Path(__file__).resolve())]
        except Exception:
            return None

    def _spawn_relaunch(self):
        """closeEvent 收尾之后调用：拉起新实例，并把「等我退出」这件事交给新实例去做。

        三条改动都对应实机故障，不是风格问题：
          1. **工作目录不再是包内**（旧写法 ``Path(__file__).parent`` 在打包态就是随包
             数据目录）：子进程占着那个目录，旧实例退出时自己的临时目录就删不干净，
             Windows 上 ``base_library.zip`` 报 FileNotFoundError 正是这条链路（图二图三）；
          2. **不再用 ``cmd /c start ... timeout /t 2`` 猜延时**：改由新实例观测旧 PID
             真退出（见 :func:`_wait_for_previous_instance`），既不抢 Chrome 端口也不白等；
          3. **不再走 shell=True**：命令行里有中文路径、空格、8.3 短名（实机日志里就是
             ``C:\\Users\\ADMINI~1\\...``），过一层 shell 就多一层引号歧义。
        """
        cmd = getattr(self, "_relaunch_pending", None)
        if not cmd:
            return
        self._relaunch_pending = None
        import subprocess
        cmd = list(cmd) + [RELAUNCH_FLAG, str(os.getpid())]
        note = getattr(self, "_relaunch_note", "")
        if note:
            # 重启原因跟着参数走，不落到任何文件里：新实例要说的那句话由「为什么重启」
            # 决定，而这件事只有这一次启动需要知道，没必要在磁盘上留一份会过期的状态。
            self._relaunch_note = ""
            cmd.append(note)
        kwargs = {"cwd": _relaunch_workdir(), "close_fds": True,
                  "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                  "stderr": subprocess.DEVNULL}
        try:
            if sys.platform.startswith("win"):
                # DETACHED_PROCESS + 新进程组：新实例不跟着旧实例的控制台一起被带走
                flags = (getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
                         | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200))
                subprocess.Popen(cmd, creationflags=flags, **kwargs)
            else:
                subprocess.Popen(cmd, start_new_session=True, **kwargs)
        except Exception as e:
            self._on_log(f"⚠ 自动重启失败（请手动重新打开工具）：{type(e).__name__}: {e}")

    def _announce_relaunch(self):
        """被重启链路拉起来时道一声「已经生效了」：闭环必须合上。

        用户在横幅上点了「重启工具现在生效」，新实例开起来后界面本该多出风鸟/爱企查
        那几路——但界面不会自己说「这就是我刚才让你重启换来的结果」，于是那一次点击
        在用户眼里永远悬着（万一没生效呢？）。只播一条日志，不弹横幅：横幅会被
        后面的告警抢掉，而这条只是确认，不该占用唯一的告警位。
        """
        pid = _relaunch_source_pid(sys.argv[1:])
        if not pid:
            return
        if _relaunch_note(sys.argv[1:]) == RELAUNCH_NOTE_MOVED:
            # 搬家那一次闭环要说的是「隔离解除了没有」，而这件事必须由本进程自己实测，
            # 不能照抄上一轮那句「权限入口一致了」——两句都指不上对方要确认的东西。
            bundle, still = "", False
            if HAS_TOOL_CONFIG:
                try:
                    bundle = str(_tool_config.app_bundle_dir() or "")
                    still = bool(_tool_config.app_translocated())
                except Exception:
                    still = False
            self._on_log(
                f"↻ 已移进「应用程序」并从新位置打开（上一个实例 PID={pid} 已退出）："
                f"{bundle or sys.executable}\n"
                + ("   ⚠ macOS 仍在按只读临时副本运行它：这属于系统层面的异常，"
                   "请把这份日志和桌面那份启动错误日志一起发给发包方"
                   if still else "   本次起不再被 macOS 挂到只读临时副本上运行"))
            return
        self._on_log(f"↻ 工具已重启（上一个实例 PID={pid} 已退出），"
                     "权限等级对应的入口现在与界面一致了")

    def _warn_read_only_runtime(self):
        """Mac 的只读转置包（App Translocation）：把运行形态这件事说给用户听。

        为什么值得单独出声：被 Gatekeeper 隔离过的 .app 双击打开时，macOS 会把它挂到
        ``/private/var/folders/.../AppTranslocation/<随机>/d/`` 上以**只读**方式运行，
        每次启动都换一个地址。功能上本轮修完之后已经不受影响（所有落点都走包外），
        但这件事仍然值得说：同一个 app 每次的包旁目录都不同，持久数据一律退到用户目录，
        而且下次开机启动会更慢。用户按提示把 app 拖进「应用程序」就彻底回到正常形态。
        只在**真处于转置态**时播一次（正常安装的人一条也不会多），也绝不弹模态。
        """
        translocated = False
        if HAS_TOOL_CONFIG:
            try:
                translocated = bool(_tool_config.app_translocated())
            except Exception:
                translocated = False
        if not translocated:
            return
        self._on_log("ℹ 本次是从 macOS 的只读临时副本启动的（下载后没移进「应用程序」文件夹）："
                     "工具功能不受影响，登录状态与数据会落在「个人目录/.b2bcollect」里")
        self._show_banner(
            "本工具正被 macOS 隔离运行（只读临时副本，每次启动都换地址）："
            "把 <B2bCollectTool.app> 拖进「应用程序」文件夹、并从那里打开，"
            "启动更快、数据也落在更直观的位置。" + self._app_relocate_link(),
            "#fff3cd", "#7a5a00")

    @staticmethod
    def _app_relocate_link() -> str:
        """转置横幅尾部那条动作链接：能代做就说代做，只能指路就实话指路。

        链接**永远给**（既然已经在播报只读副本了，就必须在同一屏给出出口，否则这条
        提示只是让人烦躁），但话术按判定结果分两种：工具能一步搬好的时候不许写成
        「请你自己拖」，搬不了的时候也绝不承诺「点一下就搞定」。
        """
        plan = MainWindow._app_migration_plan_safe()
        if str(plan.get("status") or "") == "ready":
            return '<a href="#app-relocate">帮我移进「应用程序」文件夹</a>'
        return '<a href="#app-relocate">在访达里打开这两个位置</a>'

    @staticmethod
    def _app_migration_plan_safe() -> dict:
        """搬家判定（只查不动磁盘）的永不抛异常包装。

        判据本体在 tool_config（路径策略的唯一口径），这里只负责「判据自己出问题」
        这一种情况：一条运行形态提醒绝不能把界面带崩——本轮 Mac 崩溃的形状就是
        「提醒/日志链路里出了未捕获异常」。返回空 dict 时调用方按「不知道」说话。
        """
        if not HAS_TOOL_CONFIG:
            return {}
        try:
            return dict(_tool_config.app_migration_plan() or {})
        except Exception:
            return {}

    def _offer_app_relocation(self):
        """横幅那条链接的落点：把「下载目录里的只读副本」变成「应用程序里的正常安装」。

        一次点击能不能做完，取决于 tool_config 的判定；四种做不完的形态（目标同名、
        多份副本、读不到包标识、压根没找到）都退回「在访达里打开两个位置」这句实话。
        为什么值得代做：本轮 Mac 崩溃的直接触发条件就是这种运行形态，而处置办法历来
        只有一个，说明书里写这句话多数人不会去做。
        """
        plan = self._app_migration_plan_safe()
        status = str(plan.get("status") or "")
        dest = plan.get("dest")
        if status == "ready":
            self._confirm_app_relocation(plan)
            return
        if status == "already_installed":
            self._on_log("ℹ 「应用程序」文件夹里已经有同一份工具，请从那里打开："
                         f"{dest}（这个由只读副本启动的窗口关掉就好）")
            _tool_config.reveal_in_finder([dest])
            return
        detail = str(plan.get("detail") or status or "没能确认运行形态")
        self._on_log(f"ℹ 这一步工具没法代做（{detail}）："
                     "把 B2bCollectTool.app 拖进「应用程序」文件夹，再从那里打开即可")
        self._reveal_migration_dirs(plan)

    def _confirm_app_relocation(self, plan: dict):
        """把要动哪两个目录逐字写在确认框里：代用户搬家不留「点了会发生什么」的悬念。"""
        src, dest = plan.get("source"), plan.get("dest")
        ret = QMessageBox.question(
            self, "移进「应用程序」文件夹",
            "这一步只做一件事——同卷改名（不复制、不占额外空间、瞬间完成）：\n"
            f"· 从：{src}\n"
            f"· 到：{dest}\n\n"
            "移完本工具会自动退出，并从新位置重新打开；此后不再被 macOS 挂到只读临时副本上。\n"
            "现在做吗？（选「否」则只在访达里打开这两个位置，你自己拖过去也一样）",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if ret == QMessageBox.Yes:
            self._do_app_relocation(plan)
            return
        self._reveal_migration_dirs(plan)

    def _do_app_relocation(self, plan: dict):
        """执行搬家并闭环：成功就从新位置重开（走同一条落盘收尾链路），失败就交回访达。"""
        src, dest, exe = plan.get("source"), plan.get("dest"), plan.get("dest_exe")
        if not src or not dest:
            return
        ok, why = _tool_config.move_app_bundle(src, dest)
        if not ok:
            # 跨卷、需要管理员授权这类一律不在这里硬做：那会卡界面或留下半个副本
            self._on_log(f"⚠ 没能自动移过去：{why}；已在访达里打开这两个位置，拖过去即可")
            self._reveal_migration_dirs(plan)
            return
        if not exe or not Path(str(exe)).is_file():
            # 搬是搬成了，但新位置的可执行文件对不上：绝不自动重启一个来路不明的路径
            self._on_log(f"↻ 已移进「应用程序」文件夹：{dest}\n"
                         "   请从那里（访达/启动台）打开，这个窗口关掉就行")
            _tool_config.reveal_in_finder([dest])
            return
        self._on_log(f"↻ 已移进「应用程序」文件夹：{dest}\n"
                     "   正在从新位置打开，本次起不再受只读临时副本限制")
        # 新进程用搬家后的路径启动，其余机制与「重启工具现在生效」完全同一条链路：
        # 先走 closeEvent 的落盘收尾，再由新实例确认旧 PID 真退出后才建窗口。
        self._relaunch_pending = [str(exe)]
        self._relaunch_note = RELAUNCH_NOTE_MOVED
        self.close()

    @staticmethod
    def _reveal_migration_dirs(plan: dict):
        """把「源在哪、该放到哪」两个目录开在访达里：代做不成时至少让人少找一步。"""
        paths = []
        for key in ("dest", "source"):
            p = plan.get(key)
            if p:
                paths.append(Path(str(p)).parent)
        try:
            _tool_config.reveal_in_finder(paths)
        except Exception:
            pass

    def _open_acl_admin(self):
        """权限管理面板（只有等级 0 的账号能看到这个入口）。"""
        if not edition.acl_admin_visible():
            QMessageBox.information(self, "提示", "当前账号没有权限管理入口。")
            return
        if not HAS_ACL_GUARD:
            QMessageBox.warning(self, "提示", "权限模块未加载，无法管理白名单")
            return
        try:
            from acl_admin_dialog import AclAdminDialog
        except Exception as e:
            QMessageBox.warning(self, "提示", f"权限管理面板加载失败：{type(e).__name__}: {e}")
            return
        dlg = AclAdminDialog(self)
        dlg.exec_()
        # 面板里可能改过名单/等级/绑定：回来先按新结果把自己重算一遍（含徽章与收口）
        self._refresh_acl_status()
        self._acl_sync_level()

    def _bind_yzd_account(self):
        if not HAS_YZD:
            QMessageBox.warning(self, "提示", "云展模块未加载，无法绑定账号")
            return
        if self._yzd_worker is not None and self._yzd_worker.isRunning():
            return
        tip = "请输入云展动力手机号"
        if self._yzd_phone:
            tip += f"（当前绑定：{self._yzd_phone}）"
        phone, ok = QInputDialog.getText(self, "绑定云展动力账号", tip, QLineEdit.Normal, self._yzd_phone)
        if not ok:
            return
        phone = (phone or "").strip().replace(" ", "").replace("-", "")
        if not phone or not _yzd.PHONE_RE.match(phone):
            QMessageBox.warning(self, "提示", "请输入正确的手机号")
            return
        self.bind_yzd_btn.setEnabled(False)
        self._on_log(f"🔗 正在校验云展动力账号 {phone} …")
        self._yzd_worker = YzdBindWorker(phone)
        self._yzd_worker.finished_bind.connect(self._on_bind_finished)
        self._yzd_worker.start()

    # 权限校验没通过时的「标题 + 下一步」口径。check() 的 reason 有八种来源
    # （不在白名单 / 连不上服务器 / 还没来得及回读 / 号在另一台机器 / 被解绑 /
    # 账号格式 / 校验异常 / 顶号），把它们笼统写成「请联系管理员加白名单」是错的：
    # 其中三类根本不需要人介入，等一次自动刷新就好；两类的答案是「本机再点一次绑定」。
    # 指错方向的代价由用户和管理员两边一起付——与「绑定成功」弹窗是同一类缺陷。
    @staticmethod
    def _acl_denial(reason):
        """返回 (标题, 追加的下一步)；下一步为空表示 reason 自己已经说清楚了。"""
        r = reason or ""
        # 权限服务本身没配好（包里没带密钥 / 模块缺失 / 密钥被拒）：这一类**永远不会
        # 等一次刷新就好**，必须排在所有分支最前面。除了「下一步」不同，顺序还决定
        # 一件更糟的事——被拒的文案里带着「不用改白名单」，如果先过白名单分支，
        # 就会把人精确地引到那句正在否认的动作上。
        try:
            kind = _access_guard.AccessGuard.classify_reason(r)
        except Exception:
            kind = ""
        if kind:
            title = {
                "credential": "账号已绑定，但这个安装包没带权限凭证",
                "module": "账号已绑定，但权限模块没能加载",
                "denied": "账号已绑定，但权限服务拒绝了访问",
                "clock": "账号已绑定，但这台电脑的系统时间不准",
                "tls": "账号已绑定，但这台电脑的加密连接被拦截了",
                "other": "账号已绑定，但权限服务返回异常",
            }.get(kind, "账号已绑定，但权限服务不可用")
            advice = _access_guard.AccessGuard.SERVICE_NEXT_STEP.get(kind, "")
            return (title, advice)
        if "白名单" in r or "无使用权限" in r:
            return ("账号已绑定，但未获得使用权限",
                    "请联系管理员将该账号加入白名单。")
        if "另一台设备" in r or "新设备使用" in r:
            # reason 原文已带「点『绑定云展动力账号』即可登记到本机」，不再重复一遍
            return ("账号已绑定，但使用权限在另一台设备上", "")
        if "解除" in r:
            return ("账号已绑定，设备登记需重新确认", "")
        if "格式有误" in r or "请先绑定" in r:
            return ("账号已绑定，但账号信息需要核对",
                    "请再点一次「绑定云展动力账号」确认手机号无误。")
        # 「无法连接权限服务器」「正在校验本机设备绑定，请稍候重试」「权限校验异常」
        # 都落在这一支：机器侧已经登记成功，缺的只是一次成功的远端确认。时间必须
        # 照实写，而且直接取那个真正的节拍常量：写死数字的话，哪天调了常量这句
        # 承诺就悄悄变成假的（这正是本次要消灭的「提示与实际不符」）。
        _pace = max(1, int(getattr(_access_guard, "CACHE_TTL_SECONDS", 60) or 60))
        return ("账号已绑定，权限还在确认中",
                "本机设备已经登记好，这一步不用你做什么：连回权限服务器后工具会"
                f"自动完成确认（每 {_pace} 秒校验一次），确认后如需完整功能会提示"
                "重启生效。")

    # 认领（把「这个号 = 这台机器」写上 OSS）失败的下一步口径。
    # 为什么必须分类：只有「远端确实还没有这个号的分片」那几类，才存在
    # ``_on_shard_absent`` → ``_auto_enroll`` 这条自动补登记的路；而「被抢先」和
    # 「防顶号冷却」两类，分片好端端地在别人手里，自愈永远不会触发——对它们承诺
    # 「工具会自动继续登记」就是把用户引到一个根本不会发生的结果上。
    @staticmethod
    def _claim_failure(phone, claim_msg):
        m = claim_msg or ""
        # 服务侧压根没配好（缺凭证/缺模块/密钥被拒）时，远端根本写不进去，
        # 后面那句「网络恢复后自动补齐登记」就是一个永远不会兑现的承诺——
        # 所以这个分支必须在自动补齐承诺之前，且不能与冷却/抢先共用文案。
        try:
            kind = _access_guard.AccessGuard.classify_reason(m)
        except Exception:
            kind = ""
        if kind:
            # 标题与「下一步」都直接取 _acl_denial 的口径：这两条路撞的是同一个
            # 故障，只是发现时机不同；各写一份标题迟早会互相矛盾（本轮修的正是这个）。
            title, advice = MainWindow._acl_denial(m)
            return (title, f"已绑定云展动力账号：{phone}\n{m}\n{advice}")
        if "抢先" in m:
            return ("设备登记被另一台设备抢先",
                    f"账号已绑定：{phone}\n{m}\n"
                    "这通常发生在两台机器几乎同时绑定的时候；稍后请再点一次"
                    "「绑定云展动力账号」，本机就会顶回当前设备。")
        if "再认领本机" in m or "刚在另一台设备" in m:
            return ("设备登记被防顶号冷却拦住",
                    f"账号已绑定：{phone}\n{m}\n"
                    "这一步只能由你手动重试：冷却是专门拦住两台设备互相顶号的，"
                    "工具不会自动替你抢回来。等提示的秒数过后再点一次"
                    "「绑定云展动力账号」即可。")
        if "格式有误" in m:
            return ("账号已绑定，设备登记未完成",
                    f"已绑定云展动力账号：{phone}\n设备登记未成功：{m}\n"
                    "请重新点一次「绑定云展动力账号」核对手机号。")
        # 连不上服务器 / 写入失败 / 回读校验失败：远端多半还没有这个号的分片，网络
        # 恢复后那次刷新会自动补登记。但「读不到绑定状态」也可能分片其实属于别人，
        # 所以自动承诺之外必须留一个说清条件的手动出口。
        return ("账号已绑定，设备登记未完成",
                f"已绑定云展动力账号：{phone}\n设备登记未成功：{m}\n"
                "账号已经保存好了，不用重新输入：网络恢复后工具会自动补齐这台设备"
                "的登记；要是这个号还挂在另一台设备上，则需要你再点一次"
                "「绑定云展动力账号」把它顶回本机。")

    @staticmethod
    def _bind_result_dialog(phone, saved, claim_ok, claim_msg, allowed, reason,
                            acl=True):
        """绑定结果弹窗的唯一口径（标题与正文必须同源，绝不允许互相矛盾）。

        旧版无论设备登记成没成都顶着「绑定成功」四个字，正文里却夹着
        「设备登记失败」——大厂口径里成功弹窗的成立条件是**整条链路都成功**；
        只要有一环没成，标题就必须如实降级，并且当场给出下一步。
        具体每一类失败该说什么、能不能承诺自动补齐，分别由 _acl_denial（设备登记
        成了但没被放行）与 _claim_failure（登记本身没成）定口径——它们的判据不同，
        能兑现的下一步也完全不同，不能共用一句流于形式的建议。
        返回 (标题, 正文, 是否警示脸)。
        """
        if not acl:
            # 权限层没加载（如离线测试夹具）：没有设备登记环节，绑定本身就是全部
            if not saved:
                return ("绑定未完成",
                        f"账号 {phone} 没能保存到本地，本次绑定没有生效"
                        "（界面与权限判定都仍按之前记录的账号）。\n"
                        "请检查磁盘是否可写（桌面目录能否新建文件），然后重新绑定一次。", True)
            return "绑定成功", f"已绑定云展动力账号：{phone}", False
        # 「没保存」必须排在成功分支之前：saved=False 时后面的环节根本没跑，
        # claim_ok/allowed 还停在默认值，顺序反了就会把没落盘的账号吹成绑定成功。
        if not saved:
            return ("绑定未完成",
                    f"账号 {phone} 没能保存到本地，本次绑定没有生效"
                    "（界面与权限判定都仍按之前记录的账号），设备登记也未进行。\n"
                    "请检查磁盘是否可写（桌面目录能否新建文件），然后重新绑定一次。", True)
        if claim_ok and allowed:
            return ("绑定成功",
                    f"已绑定云展动力账号：{phone}\n"
                    "本机已登记为当前使用设备，该账号已获得使用权限。", False)
        if claim_ok and not allowed:
            # 登记好了却没被放行：拦下它的可能是名单、网络、另一台机器、被解绑或
            # 账号本身，这几种情况的下一步完全不同，按 reason 分类给准确指引
            title, advice = MainWindow._acl_denial(reason)
            body = (f"已绑定云展动力账号：{phone}\n"
                    f"本机已登记为当前使用设备，但权限校验未通过：{reason}")
            return (title, body + (f"\n{advice}" if advice else ""), True)
        title, body = MainWindow._claim_failure(phone, claim_msg)
        return (title, body, True)

    def _on_bind_finished(self, ok, msg):
        self.bind_yzd_btn.setEnabled(HAS_YZD)
        if ok:
            saved = _tasks.save_account(msg)
            # 内存身份只在落盘成功时才切换：绑定账号的唯一事实来源是磁盘
            # （access_guard 直接 task_registry.load_account），先改内存就会出现
            # 「界面与云展功能是新号、权限判定按旧号」的两个身份——比绑定失败本身
            # 难查得多，而且旧号的授权会被新号白拿。存不上就整体作废这次绑定。
            if saved:
                self._yzd_phone = msg
            # 日志标记也必须与结论同源：✅ 配「不生效」正是本次弹窗缺陷在日志里的翻版
            self._on_log(("✅ " if saved else "⚠ ") + f"绑定云展动力账号 {msg}：" +
                         ("已保存" if saved else "本地保存失败，本次绑定不生效"))
            # 单点登录第二重：绑定即认领——把「这个账号 = 这台机器」写到 OSS 上。
            # 顺序不能反：先认领再判定，否则刚绑完的人会被自己的设备校验挡在门外
            # （远端还没有分片，判定只能回来说「正在校验本机设备绑定」）。
            claimed, claim_msg, allowed, reason = True, "", True, ""
            if HAS_ACL_GUARD:
                if saved:
                    try:
                        _guard = _access_guard.get_guard()
                        _guard.invalidate()
                        claimed, claim_msg = _guard.claim_device(msg)
                        self._on_log(("🔗 " if claimed else "⚠ ") + f"设备登记：{claim_msg}")
                        if claimed:
                            self._acl_stopped = False
                        _guard.refresh_blocking(timeout=6.0)
                        allowed, reason = _guard.check()
                        # 等级与界面不一致时（换了个不同等级的账号）当场收口 + 提示重启
                        self._acl_sync_level()
                    except Exception as e:
                        # 异常也是一次失败的登记：必须进弹窗口径。旧版在这里只记
                        # 日志、弹窗照旧「绑定成功」——同一个缺陷的第二个入口。
                        claimed, claim_msg = False, f"登记过程出错（{type(e).__name__}）"
                else:
                    claim_msg = "账号没能保存到本地，未进行设备登记"
            title, body, warn = self._bind_result_dialog(
                msg, saved, claimed, claim_msg, allowed, reason,
                acl=HAS_ACL_GUARD)
            # 结论必须同时出现在正文首行：macOS 上 QMessageBox 常以 sheet 呈现，
            # 标题栏根本看不到（实测 windowTitle() getter 还恒返回空串），只把结论
            # 写进标题等于没写——那正是本次缺陷「标题说成功、正文说失败」的镜像面。
            (QMessageBox.warning if warn else QMessageBox.information)(
                self, title, f"{title}\n\n{body}")
        else:
            self._on_log(f"⚠ 绑定云展账号失败：{msg}")
            QMessageBox.warning(self, "绑定失败", f"绑定失败\n\n{msg}")
        self._refresh_yzd_status()

    def _open_yzd_import(self):
        if not HAS_YZD:
            QMessageBox.warning(self, "提示", "云展模块未加载，无法导入客户库")
            return
        if self._yzd_importing:
            QMessageBox.information(self, "提示", "正在导入云展客户库，请等当前导入完成")
            return
        if not self._yzd_phone:
            QMessageBox.warning(self, "提示", "请先绑定云展动力账号")
            return
        try:
            added = _tasks.backfill_tasks()
            if added:
                self._on_log(f"📁 已补登记历史采集会话 {added} 个，可在「导入其他」里选中")
        except Exception as e:
            self._on_log(f"⚠ 历史会话补登记失败：{e}")
        dlg = YzdGroupDialog(self._yzd_phone, self)
        self._yzd_dialog = dlg
        if dlg.exec_() != QDialog.Accepted:
            return
        group = dlg.chosen_group or {}
        tasks = dlg.chosen_tasks if dlg.mode == YzdGroupDialog.MODE_OTHERS else None
        self._start_yzd_import(group, tasks)

    def _on_auto_import_toggled(self, checked):
        if not checked:
            self._auto_import_group = None
            self._on_log("已关闭「采集完自动导入云展客户库」，采集完成后需手动点导入")
            return
        if not HAS_YZD:
            self._revert_auto_import("云展模块未加载，无法自动导入客户库")
            return
        if self._yzd_importing:
            self._revert_auto_import("当前正在导入，请等它结束后再开启自动导入")
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
                chk.blockSignals(True); chk.setChecked(False); chk.blockSignals(False)
            self._on_log("ℹ 没选定分组，「采集完自动导入」已取消")
            return
        self._auto_import_group = dlg.chosen_group
        flag = self._flag_from_group(self._auto_import_group)
        self._on_log(f"🤖 已设置采集完自动导入：分组「{self._auto_import_group.get('groupName') or ''}」"
                     f"（{'国外' if flag else '国内'}客户库），采集完成后自动上传并在日志提示结果")

    def _revert_auto_import(self, msg):
        self._auto_import_group = None
        chk = getattr(self, "auto_import_check", None)
        if chk is not None:
            chk.blockSignals(True); chk.setChecked(False); chk.blockSignals(False)
        self._on_log(f"⚠ {msg}")
        QMessageBox.warning(self, "无法开启自动导入", msg)

    def _maybe_auto_import(self, reason):
        chk = getattr(self, "auto_import_check", None)
        if chk is None or not chk.isChecked():
            return
        if self._yzd_importing:
            return
        group = self._auto_import_group or {}
        if not group.get("id"):
            self._on_log("⚠ 已勾选自动导入但没拿到分组，本次不自动上传；可手动点「导入云展客户库」")
            return
        if not self.session.snapshot_results():
            self._on_log("ℹ 本次没采到数据，自动导入已跳过")
            return
        tid = self._current_task_id()
        if not tid:
            self._on_log("⚠ 本次采集没登记到任务索引（会话目录未创建），自动导入已跳过")
            return
        tasks = [t for t in _tasks.load_tasks() if t.get("id") == tid] or [
            {"id": tid, "name": f"本次采集 {tid}", "folder": str(self.session_dir)}]
        self._on_log(f"🤖 {reason}，按勾选自动导入云展客户库：分组「{group.get('groupName') or ''}」")
        self._start_yzd_import(group, tasks)

    def _build_jobs(self, tasks):
        jobs = []
        tid = self._current_task_id()
        rows = self.session.snapshot_results()
        for t in tasks or []:
            name = t.get("name") or t.get("id") or "未命名任务"
            if t.get("id") and t.get("id") == tid and rows:
                jobs.append({"id": t.get("id"), "name": name, "records": rows, "raw_file": ""})
                continue
            raw_file = t.get("raw_file") or ""
            if not raw_file and t.get("folder"):
                raw_file = os.path.join(str(t.get("folder")), _tasks.RAW_FILE_NAME)
            jobs.append({"id": t.get("id") or "", "name": name, "records": None, "raw_file": raw_file})
        return jobs

    def _start_yzd_import(self, group, tasks):
        if self._yzd_importing:
            return
        if tasks is None:
            latest = _tasks.latest_task()
            if not latest:
                QMessageBox.warning(self, "提示", "暂无采集任务：请先在主界面完成一次采集")
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
        self._on_log(f"⬆ 开始导入云展客户库：分组「{group_name}」（{'国外' if flag else '国内'}）· 共 {len(jobs)} 个任务")
        self.setEnabled(False)
        prog = YzdProgressDialog(None, f"正在导入云展客户库：{group_name}")
        self._yzd_progress = prog
        prog.show()
        worker = YzdImportWorker(self.session, self._yzd_phone, jobs, group.get("id"), flag, tmp_dir)
        worker.logged.connect(self._on_log)
        worker.progress.connect(self._on_yzd_import_progress)
        worker.finished_all.connect(self._on_yzd_import_finished)
        self._yzd_import_worker = worker
        worker.start()

    @staticmethod
    def _flag_from_group(group):
        value = group.get("foreignFlag")
        try:
            return int(value) if value is not None else 0
        except (TypeError, ValueError):
            return 0

    def _on_yzd_import_progress(self, done, total, text):
        if self._yzd_progress is not None:
            self._yzd_progress.set_text(text, f"已完成 {done}/{total} 个任务。导入期间请勿关闭程序。")

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

    def _on_yzd_import_finished(self, results, fatal):
        if fatal:
            self._on_log(f"❌ 导入异常：{fatal}")
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
                self._on_log(f"❌ {name}：{err}")
            else:
                lines.append(f"· {name}：上传 {imp} 条 / 入库成功 {sav} 条 / 失败 {fail} 条")
                self._on_log(f"✅ {name}：上传 {imp} / 入库 {sav} / 失败 {fail}")
            if idx < len(self._yzd_jobs):
                job = self._yzd_jobs[idx]
                if job.get("id"):
                    try:
                        _tasks.update_task(job["id"],
                                           yzd_import_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                           yzd_group=self._yzd_group_name, yzd_saved=sav)
                    except Exception as e:
                        self._on_log(f"⚠ 任务导入结果回写失败：{e}")
        self._unlock_yzd_import()
        self._on_log("ℹ 云展接口不做重复校验，同一批数据重复导入会在客户库里产生多条记录")
        summary = "\n".join(lines) if lines else "没有任何任务被处理"
        if saved_total == 0:
            QMessageBox.warning(self, "导入结果", f"未入库任何客户：\n{summary}\n\n请查看运行日志里的后端返回。")
        else:
            QMessageBox.information(self, "导入结果", f"共入库 {saved_total} 条客户：\n\n{summary}")
        import shutil
        worker, self._yzd_import_worker = self._yzd_import_worker, None
        if worker is not None:
            try:
                shutil.rmtree(worker.tmp_dir, ignore_errors=True)
            except Exception:
                pass

    def closeEvent(self, ev):
        if self._yzd_importing:
            QMessageBox.information(self, "请等一下", "正在导入云展客户库，请等导入完成后再关闭程序。")
            ev.ignore()
            return
        # 断开 refreshed 连接：守卫是进程级单例，比本窗口活得久。窗口析构后后台线程
        # 再发一次信号，排队的槽调用就落在一个已经销毁的 QObject 上。心跳定时器挂在
        # 自己（窗口）身上、随窗口析构自动停；信号连接挂在单例上，必须自己摘掉。
        if HAS_ACL_GUARD:
            try:
                _access_guard.get_guard().refreshed.disconnect(self._acl_refreshed_slot)
            except Exception:
                pass
        try:
            self.session.stop_crawl()
            if self.autosave and self.autosave.isRunning():
                self.autosave.wait(3000)
            # 静默上传还在路上时先等它把这片写完：upload_session 是「临时 key → copy →
            # delete」的原子写，中途被强杀最多在池子里留一个临时对象，不会写坏池子里的数据。
            # 但等这几秒能让这批内容正常落进台账；不等就是「其实传上去了、本地却记成没传」，
            # 下次开机整套重传——传上去的东西收不回来，只能白占存储。
            #
            # 落账必须在主线程这里补做：finished 是排队信号，事件循环就此结束的话不会跑。
            # 但**只能照 worker 自己报的结果落**：拿不到结果（还在跑、或被 terminate）就
            # 一律不记——把「不确定」写成「已传过」，这批数据就再也没有重试的机会了。
            w = self._silent_worker
            if w is not None and w.isRunning():
                if not w.wait(8000):
                    w.terminate()
                    w.wait(1000)
                if self._silent_result is not None:
                    ok, msg = self._silent_result
                    self._silent_commit(ok, msg + "（关闭窗口时补记）")
                else:
                    self._silent_log("退出时这一批没有拿到上传结果，"
                                     "本批不记台账（下次采集完成会整批重试）")
            # 退出前留一份「关键词 + 进度」，下次打开工具自动回填并可续采
            self._persist_progress()
            self.session.close_browser()
        except Exception:
            pass
        super().closeEvent(ev)
        # 权限等级变更后的自动重启：必须排在上面那整条落盘链路**之后**。
        # 放在 closeEvent 开头会变成「新进程已经起来、旧进程还没把 Chrome 和
        # 这批数据放下来」，两个实例抢同一个调试端口，谁都不确定谁能活下来。
        if not ev.isAccepted():
            self._relaunch_pending = None
        else:
            self._spawn_relaunch()


def _app_stylesheet():
    """全局样式：只动「层级」不动颜色主题，跟随系统深浅色。
    颜色一律不写死背景/文字（写死 #333 在深色模式下会隐形，就是旧版看板那块空白），
    只给主按钮上强调色、给分区标题降一级字重不抢戏。"""
    return """
    QGroupBox {
        font-weight: bold;
        padding-top: 10px;
        margin-top: 6px;
        border: 1px solid palette(mid);
        border-radius: 6px;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 4px;
    }
    QPushButton#primary {
        background-color: #1a73e8;
        color: #ffffff;
        border: none;
        padding: 0 18px;
        border-radius: 5px;
        font-weight: bold;
    }
    QPushButton#primary:hover   { background-color: #1765cc; }
    QPushButton#primary:pressed { background-color: #1454a8; }
    QPushButton#primary:disabled { background-color: palette(mid); color: palette(text); }
    QProgressBar {
        border: 1px solid palette(mid);
        border-radius: 5px;
        text-align: center;
        height: 26px;
    }
    QProgressBar::chunk { background-color: #1a73e8; border-radius: 4px; }
    QTextEdit, QPlainTextEdit {
        border: 1px solid palette(mid);
        border-radius: 6px;
    }
    """


# （启动失败那一组常量与函数——FATAL_LOG_NAME / _fatal_log_dirs / _fatal_log_header /
#  _fatal_log / _report_fatal_startup——统一住在文件开头「启动失败的唯一收口」那里：
#  导入本项目模块失败时它们必须已经定义好，放在这里就来不及报信了。）


# ── 重启链路（一键「重启工具现在生效」背后的全部机制）──────────────────────
# 新实例靠这个开关认出「我是被上一个实例拉起来的」，参数写法刻意与 doctor 系列
# 不同族：_doctor_request 只把含 doctor 或以 --acl 开头的写法当成「像自检却认不出」，
# 这个开关必须走建界面那条路，绝不能被误判成自检参数（离线测试逐字钉住这条边界）。
RELAUNCH_FLAG = "--relaunch-from"
# 等新实例的前任退出最多等多久：超过这个时间还没走，就当它已经不会走了
# （旧实例可能卡在某个不该卡的收尾里），让新实例正常起来比死等更重要。
RELAUNCH_WAIT_SECONDS = 12.0
# 重启**原因**的标记：搬家（移进应用程序文件夹）之后拉起的那一次，新实例要说的是
# 「已经在新位置了」，而不是「权限等级对应的入口一致了」。两种话术共用同一条重启链路，
# 差别只在这一句上；写法与 doctor 系列不同族（不含 doctor、不以 --acl 开头），
# 由离线测试逐字钉住「不许被误判成自检参数」这条边界。
RELAUNCH_NOTE_MOVED = "--relaunch-moved"


def _relaunch_note(argv) -> str:
    """这组参数里的重启原因；不是重启拉起的进程返回空串。纯函数，便于离线断言。"""
    argv = [str(a) for a in (argv or [])]
    return RELAUNCH_NOTE_MOVED if RELAUNCH_NOTE_MOVED in argv else ""


def _relaunch_source_pid(argv) -> int:
    """从参数里取出「前任实例的 PID」，不是重启拉起的进程返回 0。纯函数，便于离线断言。"""
    argv = list(argv or [])
    for i, a in enumerate(argv):
        if a == RELAUNCH_FLAG and i + 1 < len(argv):
            try:
                return max(0, int(argv[i + 1]))
            except (TypeError, ValueError):
                return 0
    return 0


def _pid_alive(pid: int) -> bool:
    """这个 PID 还在不在。不引第三方库（psutil 从来不在随包清单里，为这一件事加依赖不值）。"""
    pid = int(pid or 0)
    if pid <= 0:
        return False
    if sys.platform.startswith("win"):
        try:
            import ctypes
            # PROCESS_QUERY_LIMITED_INFORMATION：只查状态，不需要目标进程的任何权限
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            kernel32.CloseHandle(handle)
            return True
        except Exception:
            return False
    try:
        import errno
        import os
        os.kill(pid, 0)
        return True
    except OSError as e:
        # EPERM＝进程在、只是没权限探它（绝不能当成「已经退出」，那是反向误判）
        return e.errno == errno.EPERM
    except Exception:
        return False


def _wait_for_previous_instance(pid: int, timeout: float = RELAUNCH_WAIT_SECONDS) -> bool:
    """等前任实例真退出再继续；返回 True = 观测到它已退出。

    为什么把「等」放在新实例这一侧、还要带上限（旧写法是父进程用 cmd 延时 2 秒再拉起）：
      · 2 秒是猜的。共享 Chrome 只有一个调试端口，猜短了两个实例抢同一个端口，新实例
        起不来（用户看到「重启一下工具就打不开了」），猜长了则每次白屏多等好几秒；
      · 真实判据是「前任的进程句柄没了」，那就直接观测它，不再猜时长；
      · 上限同样重要：前任万一卡在收尾里不退出，新实例不能陪它一起永不现身。
    """
    import time
    deadline = time.time() + max(0.0, float(timeout))
    while _pid_alive(pid) and time.time() < deadline:
        time.sleep(0.2)
    return not _pid_alive(pid)


def _relaunch_workdir() -> str:
    """拉起新实例时给它的工作目录：**绝不用包内目录**。
    旧写法取 ``Path(__file__).resolve().parent``，打包态那是随包数据目录（Windows
    onefile 下正是 %TEMP%\\_MEIxxxx）——子进程把它当当前目录，旧实例退出时就删不掉、
    也删不干净自己的临时目录，``base_library.zip`` 就是这么在半删的状态下报
    FileNotFoundError 的（2026-11-12 Windows 实机）。现在：打包态用 exe 同级目录，
    写不动再退用户目录，源码态保持模块目录。
    """
    if getattr(sys, "frozen", False):
        try:
            import tool_config as _tc
            ed = _tc.exe_dir()
            if ed is not None and _tc.probe_writable(ed):
                return str(ed)
        except Exception:
            pass
        try:
            home = Path.home() / ".b2bcollect"
            home.mkdir(parents=True, exist_ok=True)
            return str(home)
        except Exception:
            return str(Path.home())
    return str(Path(__file__).resolve().parent)


# access_guard.DOCTOR_FLAGS 的退路副本：只在权限模块本身就没加载进来时使用
# （那正是自检要报的故障）。离线测试逐字对拍两个集合，防止写偏。
_DOCTOR_FLAGS_FALLBACK = ("--doctor", "--acl-doctor")


def _doctor_flags() -> tuple:
    """产物自检开关的可选写法。

    为什么界面上也要认 --doctor：CI 工作流写的是 --acl-doctor，管理员手敲源码 CLI
    看到的是 --doctor，两个写法只要有一个 exe 认不出来，打包态的后果不是报错而是
    **安静地去建界面**——CI 的 Start-Process -Wait 一路干等到 job 超时，日志里连
    一行自检输出都没有，比失败更难查。
    优先取 access_guard 那份（唯一口径）；它没加载进来时退回本地常量——那正是门禁
    要报的故障，此时开关还得认得出来，否则故障包连自检都启动不了。
    """
    if HAS_ACL_GUARD:
        try:
            return tuple(_access_guard.DOCTOR_FLAGS)
        except AttributeError:
            pass
    return _DOCTOR_FLAGS_FALLBACK


def _doctor_request(argv) -> tuple:
    """这组参数是不是「要跑产物自检」。返回 ``(要自检, 像自检却认不出的写法)``。

    拆成纯函数的原因：分派发生在 QApplication 之前，是产物自检唯一真正的入口，
    而它的失败形态是「挂到 CI 超时」而不是「抛异常」——没法用异常测出来。做成
    不建界面就能调的函数，离线测试才能把两种拼写和错拼都跑一遍。
    """
    flags = _doctor_flags()
    if any(a in flags for a in argv):
        return True, []
    return False, [a for a in argv
                   if "doctor" in a.lower() or a.startswith("--acl")]


def _acl_doctor_entry() -> int:
    """产物自检入口的收口：把「模块没打进包」也当成一种自检失败，而不是崩栈。

    这一步在打包态最可能遇到的失败恰恰是「依赖缺失」（本轮修的正是发包漏配凭证
    与它同族的漏收模块）。让它返回分类退出码，CI 才有稳定可判的口径。
    """
    # 第一件事是修控制台编码：Windows 英文系统上 Python 用 cp1252 编码 stdout，
    # 打中文直接抛 UnicodeEncodeError。那时屏幕上留下的最后一行会是 codecs 的
    # 栈，而不是这一层要说的结论——排查的人会去查权限服务，而坏掉的只是打印。
    if HAS_TOOL_CONFIG:
        _tool_config.force_utf8_stdout()
    if not HAS_ACL_GUARD:
        print("❌ acl-doctor：权限模块（access_guard）没能加载，"
              "说明打包清单漏收依赖——这本身就是发包缺陷")
        if not HAS_TOOL_CONFIG:
            print("   · tool_config 同样没加载到：配置与凭证解析链整条缺失，"
                  "请在打包工作流里补 --hidden-import tool_config")
        return 3
    try:
        return int(_access_guard.run_doctor())
    except Exception as e:
        print(f"❌ acl-doctor 自身异常：{type(e).__name__}: {e}")
        return 6


def main():
    # 产物自检（发包门禁）：必须在 QApplication 之前分派。两个原因：
    # ① CI runner 上常常根本没有可用的图形会话，建 QApplication 会先炸在显示上，
    #    而它要验证的只是「这个包能不能连上权限服务」，与界面无关；
    # ② 自检必须在**产物自己**里跑——开发机跑通的永远是源码，不是发货的那个包。
    run_doctor_requested, wrong = _doctor_request(sys.argv[1:])
    if run_doctor_requested:
        sys.exit(_acl_doctor_entry())
    # 兜底：写法里带 doctor 却没被认出来时，绝不退化成「安静启动界面」。
    # 打包态最坏的失败不是报错而是卡住——CI 用 Start-Process -Wait 取退出码，一旦
    # 走到建界面这条路（runner 上多半根本没有可用的图形会话），这一步要么崩在显示
    # 上，要么一路挂到 job 超时，日志里连一行自检结论都留不下。当场说清正确写法并
    # 返回非零，坏掉的就只是这一步，而且原因直接写在最后一行上。
    if wrong:
        if HAS_TOOL_CONFIG:
            _tool_config.force_utf8_stdout()
        print(f"❌ 没认出这几个参数：{' '.join(wrong)}；"
              f"产物自检的正确写法是 {' 或 '.join(_doctor_flags())}")
        sys.exit(3)
    # 重启链路的交接：新实例先等前任真退出，再建 QApplication 与共享 Chrome。
    # 放在这里（而不是界面起来之后）的原因：抢同一个调试端口的风险只存在于「两个实例
    # 都在启动」这段时间里，而这段时间正好是 QApplication + 建窗口；等完之后窗口才现身，
    # 用户看到的就是「重启了一下，工具开了」，不是「重启了一下，工具没了」。
    previous_pid = _relaunch_source_pid(sys.argv[1:])
    if previous_pid and not _wait_for_previous_instance(previous_pid):
        # 说清楚而不是悄悄多等：真到超时说明前任卡在收尾里，此时继续启动是对的
        # （宁可两个实例并存几秒让用户能操作，也不要一个永远不出现的窗口）。
        print(f"ℹ 上一个实例（PID={previous_pid}）在限定时间内没退出，本实例直接启动")
    try:
        app = QApplication(sys.argv)
        app.setStyleSheet(_app_stylesheet())
        # 双重验证守卫（QApplication 级 eventFilter：主窗口与所有对话框的
        # 按钮点击都会先过「已绑定云展账号 + OSS 白名单」两道校验）
        if HAS_ACL_GUARD:
            _access_guard.install(app)
            # 建界面**之前**做一次**权威**身份判定：每次开机都联网查 OSS，据此决定第一屏
            # 是完整版还是丐版。为什么不能再像旧版那样只读本地副本（prefetch_local）：
            # 副本可能是「这个号还没被删除/降级时」的旧等级——只信副本，被管理员删掉的号
            # 下次开机照样按旧等级摆出全套入口，正是「被删除的账号还能使用」。
            # 判定口径：OSS 拿到名单且本号在里面→按真实等级建（0/1完整、2丐版）；
            # 拿不到/超时/未绑定/不在名单→一律按丐版建（宁严勿宽，与 check() 同源）。
            # 高权限号若因这次断网先起了丐版，联网后由后台刷新确认真实等级，再走
            # _acl_sync_level 的「横幅 + 一键重启」补齐完整版入口（建好的界面长不出新按钮）。
            _access_guard.prefetch_authoritative()
        w = MainWindow()
        if not HAS_ACL_GUARD:
            try:
                w._on_log("⚠ 权限校验模块（access_guard）未加载，本次运行不做白名单拦截")
            except Exception:
                pass
        w.show()
    except Exception as e:
        _fatal_log(e)
        try:
            _fatal_paths = [str(d / FATAL_LOG_NAME) for d in _fatal_log_dirs()]
            QMessageBox.critical(
                None, "工具启动失败",
                f"{type(e).__name__}: {e}\n\n详细堆栈见这份日志——先看第一个位置，"
                "找不到就看第二个：\n" + "\n".join(_fatal_paths))
        except Exception:
            pass
        raise
    try:
        rc = app.exec_()
    finally:
        # ── 有序收尾：窗口 → 守卫 → QApplication ─────────────────────────────
        # 不写这一段的话，退出时谁先被销毁由解释器收尾的 GC 顺序决定，而那个顺序是
        # 反的：模块级全局（守卫单例）比 main 的帧活得久，QApplication 的 wrapper 先析构
        # 时 PyQt 还会遍历剩下所有 wrapper——撞上一个 C++ 侧已随 app 销毁的对象就是
        # 退出 SIGSEGV（线上实测抓到过这条栈）。用户侧的表现是「关工具弹一次系统崩溃
        # 报告」，顺带最后一次自动保存可能没落盘。所以趁 app 还活着，先把窗口和守卫拆掉。
        try:
            if w.isVisible():          # 关窗口退出时 closeEvent 已经跑过，不再补第二次
                w.close()
        except Exception:
            pass
        if HAS_ACL_GUARD:
            try:
                _access_guard.shutdown(app)
            except Exception:
                pass
        w = None
    sys.exit(rc)


if __name__ == "__main__":
    main()
