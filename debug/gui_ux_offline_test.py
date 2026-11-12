# -*- coding: utf-8 -*-
"""本轮 GUI 四项优化的离线断言（offscreen 无头跑，不弹用户桌面、不碰真实数据目录）：
① 页数控件彻底删除、翻页一律不限（max_pages 固定 0）
② 「后台运行」默认勾选
③ 看板由 5 行压成 1 行、次要文字按深浅主题自适应（根治「已采 xxx 家下方空一块把日志挤出屏幕」）
④ 界面整洁化：长说明进 tooltip、参数区 1 行、主按钮唯一、日志折叠抽屉默认收起
   （2026-11-12 追加：主动作行钉在滚动容器之外 + 窗口最小宽按屏宽分档，见下面 ④ 段）
"""
import inspect
import io
import os
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, "/Users/jhnoplan/Desktop/PythonDemo/b2bcollect_build")

from PyQt5.QtWidgets import (QApplication, QGroupBox, QLabel, QPushButton)  # noqa: E402
from PyQt5.QtGui import QImage, QPainter                                        # noqa: E402

import b2bcollect_gui as gui  # noqa: E402

SRC = io.open("/Users/jhnoplan/Desktop/PythonDemo/b2bcollect_build/b2bcollect_gui.py",
              encoding="utf-8").read()

ok = 0


def check(name, cond, detail=None):
    global ok
    assert cond, f"FAIL: {name}" + (f"  -> {detail}" if detail is not None else "")
    ok += 1
    print(f"  ✓ {name}")


def _scroll_of(wd):
    """控件所在的可滚动容器；没有就返回 None（=它钉在常驻区，不用滚就能看到）。"""
    p = wd.parent()
    while p is not None and not isinstance(p, gui.QScrollArea):
        p = p.parent()
    return p


def _floor_at(cap_w):
    """把可用屏宽假装成 cap_w，量一次「窗口最小宽被抬到多少 / 退路是什么」。

    offscreen 的假屏只有 800 宽，直接跑永远只落在「封顶 + 退回横向滚动」那一档，
    「按内容抬起地板」这条真机上天天生效的主路在 CI 里等于没人测。这里造两种屏各量一次。
    """
    gui.MainWindow._width_floor_cap = lambda self, _c=cap_w: _c
    win = gui.MainWindow()
    win.show()
    app.processEvents()
    content = win._cfg_scroll.widget()
    # 最宽的几个控件一起带回来：内容最小宽突然变大时，光看一个总数等于什么也没说
    # （本轮就被「1466 > 1440」这条红拖了一整轮，因为它只报了总数、没报是谁顶起来的）。
    widest = sorted(((wd.minimumSizeHint().width(), type(wd).__name__,
                      (wd.title() if isinstance(wd, QGroupBox) else wd.text())[:28])
                     for wd in content.findChildren((QLabel, QGroupBox))), reverse=True)[:3]
    got = (win.minimumWidth(),
           content.minimumSizeHint().width(),
           win._cfg_scroll.horizontalScrollBarPolicy(),
           win._gb_list.title() if getattr(win, "_gb_list", None) is not None else "",
           widest)
    win.close()
    win.deleteLater()
    app.processEvents()
    gui.MainWindow._width_floor_cap = lambda self: 800      # 恢复：后面还用得上小屏那一档
    return got


def _floor_after_morph(cap_w):
    """首次显示**之后**把配置内容撑宽（模拟启动回填上次任务、告警挂上分区标题），
    量窗口最小宽跟不跟得上，再改回去量它降不降得下来。

    这一组是「同一份代码两次跑出门禁结果不同」的正解：那些改变形状的动作全部发生在
    showEvent 之后，只在那一刻算一次地板的用例永远量不到它们（红的时候像布局缺陷，
    绿的时候又什么都没说）。现在把「变宽要跟上、变窄要松开」钉成可复现的显式动作。
    """
    gui.MainWindow._width_floor_cap = lambda self, _c=cap_w: _c
    win = gui.MainWindow()
    win.show()
    app.processEvents()
    orig = win._gb_list.title()
    before = (win.minimumWidth(), win._cfg_scroll.widget().minimumSizeHint().width())
    win._gb_list.setTitle("② 公司名单（" + "补" * 60 + "）")
    for _ in range(4):
        app.processEvents()
    wider = (win.minimumWidth(), win._cfg_scroll.widget().minimumSizeHint().width())
    win._gb_list.setTitle(orig)                        # 原样换回：别的机器的标题可能带告警后缀
    for _ in range(4):
        app.processEvents()
    back = (win.minimumWidth(), win._cfg_scroll.widget().minimumSizeHint().width())
    win.close()
    win.deleteLater()
    app.processEvents()
    gui.MainWindow._width_floor_cap = lambda self: 800
    return before, wider, back


def _badge_pixels(win):
    """把折叠条右半边（角标占位区）真渲染一遍取像素。

    为什么非要取像素：状态改对了、paintEvent 却没画（忘记 update、坐标算出界、
    提前 return），在纯状态断言下全都一片绿——而用户看到的就是一条没有角标的折叠条。
    「画出来的东西」只能由画出来的结果验收。
    """
    btn = win.btn_log_toggle
    img = QImage(btn.size(), QImage.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    btn.render(p)
    p.end()
    left = max(0, btn.width() - btn._worst_badge_width())
    zone = img.copy(left, 0, max(1, btn.width() - left), btn.height())
    return bytes(zone.bits().asstring(zone.bytesPerLine() * zone.height()))


def _badge_painted(win):
    """同一控件在「有告警 / 无告警」两态下渲染必须不同，否则角标等于没画。"""
    btn = win.btn_log_toggle
    keep = (btn._notice_error, btn._notice_warn)
    try:
        btn.set_notice(1, 0)
        with_badge = _badge_pixels(win)
        btn.set_notice(0, 0)
        without = _badge_pixels(win)
    finally:
        btn.set_notice(*keep)
    return with_badge != without and len(with_badge) > 0


app = QApplication.instance() or QApplication([])
# 窗口最小宽的天花板就是可用屏宽，而 offscreen 的假屏只有 800x600：这条用例今天在任何
# 机器上量到的都是「小屏退路」那一档（横向滚动条 + 最小宽不抬）。把 cap 显式钉死，
# 换真机、换 CI 机器跑的才是同一件事，不会哪天在同事的 5K 屏上莫名红一条。
gui.MainWindow._width_floor_cap = lambda self: 800
w = gui.MainWindow()
w.show()

# ── ① 页数控件删除 + 一律不限页数 ────────────────────────────
check("① 旧页数控件属性已彻底移除",
      not hasattr(w, "chk_unlimited") and not hasattr(w, "spin_pages"))
check("① 页数控件不再被创建（旧版那个复选框+spinbox 彻底消失）",
      not re.search(r'QCheckBox\("[^"]*不限页数|QLabel\("限定时|QSpinBox\(\)', SRC))
start_src = gui.inspect.getsource(w._on_start) if hasattr(gui, "inspect") else ""
if not start_src:
    import inspect
    start_src = inspect.getsource(w._on_start)
check("① _on_start 固定传 max_pages=0（翻到没有新数据为止）", "max_pages=0" in start_src)
check("① _on_start 不再引用被删控件", "spin_pages" not in start_src and "chk_unlimited" not in start_src)

# ── ② 后台运行默认勾选 ──────────────────────────────────────
check("② 后台运行复选框默认勾选", w.chk_bg.isChecked() is True)
check("② 过滤不相关/增量模式仍默认勾选（原有防封默认值不回退）",
      w.chk_guard.isChecked() and w.chk_incr.isChecked())

# ── ③ 看板压成一行 + 自适应配色（"空一块" 的根因）──────────────
FAKE = {
    "供应商网": dict(got=42, ok=30, pages=12, listed=180, no_contact=8, failed=4, early=2, filtered=1),
    "中国制造网": dict(got=28, ok=20, pages=9, listed=140, no_contact=5, failed=3, early=1, filtered=0),
    "八方资源网": dict(got=51, ok=40, pages=15, listed=260, no_contact=6, failed=5, early=3, filtered=2),
    "顺企网": dict(got=33, ok=25, pages=11, listed=200, no_contact=4, failed=2, early=1, filtered=1),
    "爱采购": dict(got=19, ok=15, pages=7, listed=95, no_contact=2, failed=1, early=0, filtered=0),
}
w.session.site_stats = lambda: FAKE
w._refresh_board()
board = w.lbl_board.text()
check("③ 看板渲染为总计一行（旧的每站一行已消失）", board.startswith("明细："))
check("③ 看板文本不含换行（占位高度=1 行，不再压出大块空白）", "\n" not in board)
check("③ 看板数值取五站求和",
      f"成功 {sum(s['ok'] for s in FAKE.values())}" in board
      and f"无联系方式 {sum(s['no_contact'] for s in FAKE.values())}" in board)
check("③ 逐站明细移到悬停 tooltip（信息没被砍掉，只是不占行）",
      all(name in w.lbl_board.toolTip() for name in FAKE))
check("③ 看板不再写死近黑色 #333（深色模式下隐形却占位的元凶）",
      "#333" not in w.lbl_board.styleSheet())

# 空标签在 Qt 里照样占一整行：未采集时不该占位（把行高让给日志）
w2 = gui.MainWindow()
w2.show()
check("③  fresh 窗口上看板/保存回显隐藏（不白占两行）",
      not w2.lbl_board.isVisible() and not w2.lbl_save_info.isVisible())
w2.session.site_stats = lambda: FAKE
w2._refresh_board()
check("③ 一有数据看板自动显形（不需手动刷新）", w2.lbl_board.isVisible())
w2.close()

# 深浅主题下都必须"看得见"：次要色与窗口底色要拉开足够差值
pal = app.palette()
bg_rgb = pal.color(gui.QPalette.Window).getRgb()[:3]
fg_rgb = pal.color(gui.QPalette.WindowText).getRgb()[:3]
mc = gui._muted_color()
muted_rgb = tuple(int(mc[i:i + 2], 16) for i in (1, 3, 5))
gap = max(abs(a - b) for a, b in zip(muted_rgb, bg_rgb))
check(f"③ 次要文字色按主题自适应且与底色可辨（当前底色{bg_rgb} 差值{gap}）", gap >= 40)
check("③ _muted_color 返回合法 6 位十六进制", bool(re.fullmatch(r"#[0-9a-f]{6}", mc)))

# 强制切换主题，验证两套红/绿/中性都各就各位（不依赖用户当前系统设置）
_real_dark = gui._is_dark_theme
try:
    gui._is_dark_theme = lambda: True
    check("③ 深色主题下提醒红改用亮红 #ff6b6b", "#ff6b6b" in gui._danger_style())
    check("③ 深色主题下徽标走深色底（不再贴浅色便签）",
          "background: #3a2224" in gui._status_badge_style("error")
          and "background: #1f3324" in gui._status_badge_style("ok"))
    dark_html = gui.build_stats_html("已采 10 家　中国制造网 5", {"mic": "done"})
    check("③ 深色主题下状态行绿色提亮为 #66bb6a", gui.STATS_STYLE_DARK["done"][0] in dark_html)
    gui._is_dark_theme = lambda: False
    check("③ 浅色主题下提醒红仍为 #c62828", "#c62828" in gui._danger_style())
    check("③ 浅色主题下徽标仍用原浅底配色（旧观感不变）",
          "background: #fdecea" in gui._status_badge_style("error"))
    light_html = gui.build_stats_html("已采 10 家　中国制造网 5", {"mic": "done"})
    check("③ 浅色主题下状态行沿用 STATS_STYLE 原色", gui.STATS_STYLE["done"][0] in light_html)
    check("③ 深浅两套色板色相一致（只调亮度、不改变用户的既成认知）",
          [i for _, i in gui.STATS_STYLE.values()] == [i for _, i in gui.STATS_STYLE_DARK.values()])
finally:
    gui._is_dark_theme = _real_dark

check("④ 主窗口默认尺寸按小屏收拢（宽<=1000 且高<=760）",
      w.width() <= 1000 and w.height() <= 760,
      f"{w.width()}x{w.height()}")
check("④ 最小尺寸允许进一步压缩", w.minimumWidth() <= 900 and w.minimumHeight() <= 640,
      f"{w.minimumWidth()}x{w.minimumHeight()}")
# ── ④ 整洁化：布局行数 / 长文案归位 / 主按钮唯一 ────────────────
# 日志区（2026-11-12 用户反馈「一屏显示不全……把日志区域缩小，正常用户不会经常看日志，
# 太占用屏幕了」）：老契约「最小高写死 180 常驻」正是这次的账——偶发诊断区常驻半屏，
# 主流程就被挤出屏幕。新契约钉四件事：新用户默认收起、收起态定高两行（挂机仍能扫最后
# 一行 ⚠/⛔）、展开态拿回旧底线、展开/收起时富余高度在配置区与日志之间正确换手。
check("④ 日志默认收起（偶发诊断区不再常驻半屏）", gui._LOG_DEFAULT_EXPANDED is False)
w._apply_log_area(False)
check("④ 收起态定高且至少两行（是折叠，不是彻底藏掉）",
      w.log.minimumHeight() == w.log.maximumHeight()
      >= 2 * w.log.fontMetrics().lineSpacing(),
      f"{w.log.minimumHeight()}x{w.log.maximumHeight()}")
check("④ 收起态不抢配置区的富余高度", w._root_lay.stretch(w._log_index) == 0)
w._apply_log_area(True)
check("④ 展开态拿回旧的小屏底线（150）且高度能跟着窗口长",
      w.log.minimumHeight() >= 150 and w.log.maximumHeight() > w.log.minimumHeight())
check("④ 展开态才把富余高度让给日志", w._root_lay.stretch(w._log_index) == 1)
check("④ 折叠条文字跟着档位走（用户看得见还能收回去）",
      w.btn_log_toggle.text() == gui.MainWindow.LOG_TOGGLE_EXPANDED
      and "日志" in w.btn_log_toggle.text())
w._apply_log_area(False)
# 折叠的固有代价是「看不见就等于没有」：收起态里滚过去的告警必须留下计数，否则折叠
# 自己就成了下一轮「静默少功能」的藏身处（本轮用户就是因为界面不吭声才去查权限）。
# 计数还必须按严重度分档并封顶：挂机一晚 ⚠ 能刷上千行，跟 ⛔ 混成一个数字就等于壁纸。
# 而计数一律**画成角标**、绝不拼进按钮文字：拼进文字按钮就跟着条数变宽，窗口一窄
# 文字截成「▸ 展开日志（⚠ 3 条…」，布局门禁量到的形状还会随磁盘状态漂移。
w._log_notice_error = w._log_notice_warn = 0
_geo0 = (w.btn_log_toggle.minimumWidth(), w.btn_log_toggle.sizeHint().width(),
         w._action_lay.totalMinimumSize().width())
w._on_log("⚠ 风鸟 / 爱企查 在你当前账号等级下本该可用，本机却没加载出来")
w._on_log("这一行是普通进度，不算提醒")
w._on_log("正文里偶然出现 ⚠ 这种符号，不算一条提醒")
w._on_log("  ⛔ 持续限流，本站本轮结束")
check("④ 收起态告警计入角标，且按严重度分两档（⚠ 提醒 / ⛔❌🚫 失败）",
      w._log_notice_warn == 1 and w._log_notice_error == 1
      and w.btn_log_toggle.badge_label() == "1"
      and "1 条失败" in w.btn_log_toggle.toolTip()
      and "1 条提醒" in w.btn_log_toggle.toolTip(),
      f"{w._log_notice_error}/{w._log_notice_warn}｜{w.btn_log_toggle.badge_label()}")
check("④ 只有失败级才用失败色（红色是稀缺资源，天天红=天天不红）",
      w.btn_log_toggle.badge_colors()[0] == gui._badge_tone_colors("error")[1],
      w.btn_log_toggle.badge_colors())
w._log_notice_error = 0
w._refresh_log_toggle()
check("④ 只剩提醒时角标换提醒档色相",
      w.btn_log_toggle.badge_colors()[0] == gui._badge_tone_colors("pause")[1],
      w.btn_log_toggle.badge_colors())
check("④ 严重度词汇必须和色表档位同名（对不上不报错，只是静默落到默认灰=角标哑成没消息）",
      w.btn_log_toggle.badge_colors()[0] != gui._badge_tone_colors("__unknown__")[1]
      and w.btn_log_toggle.badge_tone() in ("", "error", "pause", "ok", "run"),
      f"{w.btn_log_toggle.badge_tone()}｜{w.btn_log_toggle.badge_colors()}")
check("④ 角标必须实心（低饱和底在 15px 胶囊上和窗口底色分不出来，去掉符号后它就是唯一轻重通道）",
      w.btn_log_toggle.badge_colors()[0] != w.btn_log_toggle.badge_colors()[1]
      and gui._badge_tone_colors("pause")[0] not in w.btn_log_toggle.badge_colors(),
      w.btn_log_toggle.badge_colors())
check("④ 两档底色互不相同（同一种颜色说不清「失败」和「提醒」）",
      gui._badge_tone_colors("error")[1] != gui._badge_tone_colors("pause")[1])
check("④ 角标上的字符只许是 ASCII（drawText 不走字体回退链，符号在缺字体的机器上=豆腐块）",
      w.btn_log_toggle.badge_label().isascii()
      and w.btn_log_toggle.badge_label().replace("+", "").isdigit(),
      w.btn_log_toggle.badge_label())
w._log_notice_error = 1
w._refresh_log_toggle()
check("④ 悬停折叠条能看出最近一条是什么（不必先展开再翻日志）",
      "持续限流" in w.btn_log_toggle.toolTip(), w.btn_log_toggle.toolTip()[-40:])
check("④ 角标计数不改几何（0 条与 1 条时按钮一样宽，文字永不被截）",
      (w.btn_log_toggle.minimumWidth(), w.btn_log_toggle.sizeHint().width(),
       w._action_lay.totalMinimumSize().width()) == _geo0,
      f"{_geo0} → {(w.btn_log_toggle.minimumWidth(), w.btn_log_toggle.sizeHint().width(), w._action_lay.totalMinimumSize().width())}")
# 封顶：角标是信号不是台账，刷一万行也只能看到 99+
w._log_notice_error = 0
w._log_notice_warn = 250
w._refresh_log_toggle()
check("④ 条数显示封顶 99+（刷屏期不变成没人看的壁纸，也不再逐行重画）",
      w.btn_log_toggle.badge_display() == "99+"
      and w.btn_log_toggle.badge_label() == "99+"
      and "250" not in w.btn_log_toggle.toolTip(),
      f"{w.btn_log_toggle.badge_label()}｜{w.btn_log_toggle.toolTip()[-50:]}")
w._log_notice_error = 250
w._refresh_log_toggle()
check("④ 两档都爆表时角标报失败档色、悬停把两个 99+ 都报全（信息不因为只显示一个而丢）",
      w.btn_log_toggle.badge_label() == "99+"
      and w.btn_log_toggle.badge_colors()[0] == gui._badge_tone_colors("error")[1]
      and "99+ 条失败" in w.btn_log_toggle.toolTip()
      and "99+ 条提醒" in w.btn_log_toggle.toolTip(),
      f"{w.btn_log_toggle.badge_label()}｜{w.btn_log_toggle.toolTip()[-60:]}")
w._log_notice_error = 1
w._log_notice_warn = 0
w._refresh_log_toggle()
check("④ 角标真的画出来了（像素级取证：占位区里两态渲染必须不同，不是只改了内部状态）",
      _badge_painted(w), "占位区两态像素一致 → paintEvent 没生效")
w._apply_log_area(True)
check("④ 展开即视为已读：两档计数一起归零、文字换回收起口吻、角标撤掉",
      w._log_notice_error == 0 and w._log_notice_warn == 0
      and w.btn_log_toggle.badge_label() == ""
      and w.btn_log_toggle.text() == gui.MainWindow.LOG_TOGGLE_EXPANDED)
w._apply_log_area(False)
check("④ 再折回去不凭空冒出条数（计数只由新告警驱动）",
      w.btn_log_toggle.badge_label() == "", w.btn_log_toggle.badge_label())
check("④ 折叠条两种文案共用同一个宽度地板（切换状态不改变几何）",
      w.btn_log_toggle.minimumWidth() >= max(
          w.btn_log_toggle.fontMetrics().horizontalAdvance(gui.MainWindow.LOG_TOGGLE_COLLAPSED),
          w.btn_log_toggle.fontMetrics().horizontalAdvance(gui.MainWindow.LOG_TOGGLE_EXPANDED))
      + w.btn_log_toggle._worst_badge_width(),
      f"{w.btn_log_toggle.minimumWidth()}")
# 主动作行移出滚动容器：这条是「开始采集要滑动才能看到」的正解（只挪日志不改结构，
# 窗口一矮照样要滚到最底下才能开工）。
check("④ 主动作行钉在滚动容器之外（不在容器里=永远不用滚就能开工）",
      _scroll_of(w.btn_start) is None)
check("④ 常驻行整行最小宽放得进窗口最小宽（滚出去的那一行没有裁切退路，只能压扁）",
      w._action_lay.totalMinimumSize().width() <= w.minimumWidth() - 40,
      f"{w._action_lay.totalMinimumSize().width()} vs {w.minimumWidth()}")
check("④ 常驻行按钮按布局会让出的宽度钉住（不会被压成「导出 Exc…」）",
      all(b.minimumWidth() >= b.minimumSizeHint().width()
          for b in (w.btn_start, w.btn_pause, w.btn_stop, w.btn_clean,
                    w.btn_export, w.btn_reset)),
      [(b.text(), b.minimumWidth(), b.minimumSizeHint().width())
       for b in (w.btn_pause, w.btn_stop)])
check("④ 数据池四个入口搬进可滚动的配置区（次要工具不再陪跑常驻行）",
      w.btn_pool_sync is not None and _scroll_of(w.btn_pool_sync) is not None)
check("④ 待机时「就绪」只说一次（进度条内不写字，状态行是唯一口径：两处同样的词=看着像卡住）",
      w.progress.format() == "" and w.lbl_stats.text() == "就绪",
      f"{w.progress.format()!r}｜{w.lbl_stats.text()!r}")
_small, _big = _floor_at(800), _floor_at(1440)
check("④ 小屏放不下时退路是「封顶 + 横向滚动」，不是把窗口撑出屏幕",
      _small[0] < _big[0] and _small[2] != gui.Qt.ScrollBarAlwaysOff,
      f"屏 800 → 最小宽 {_small[0]}／屏 1440 → {_big[0]}")
# 大屏这一档必须把两条合法出路都认下来：内容放得下就抬地板（不裁按钮），
# 内容比屏还宽就封顶 + 横向滚动（不把窗口撑出屏幕）。旧断言只认第一条，于是
# 「内容 1466 > 屏 1440」这种**正确行为**会被判红——门禁红在界面说真话上，
# 比漏红更糟：下一次没人相信红了。
check("④ 大屏下窗口按内容抬地板，或按退路封顶+横向滚动（两条出路都要自洽）",
      _big[0] >= _big[1] or (_big[0] == 1440 and _big[2] != gui.Qt.ScrollBarAlwaysOff),
      f"最小宽 {_big[0]}｜内容最小宽 {_big[1]}｜横向策略 {_big[2]}｜"
      f"② 标题={_big[3]}｜最宽控件={_big[4]}")
_b0, _b1, _b2 = _floor_after_morph(2000)
check("④ 首次显示之后内容才变宽的（启动回填/告警挂标题），地板当场跟得上",
      _b1[0] > _b0[0] and _b1[0] >= _b1[1], f"变更前 {_b0} → 变更后 {_b1}")
check("④ 内容变窄回去时地板也松开（只抬不降会把窗口永久钉死在很宽）",
      _b2[0] <= _b0[0] + gui.MainWindow._WIDTH_FLOOR_HYSTERESIS
      and _b2[0] >= _b2[1], f"变更后 {_b1} → 换回 {_b2}")
# 注意顺序：上面那几个 helper 会临时把 _width_floor_cap 顶成 1440/2000，而**已开着的
# 窗口**在下一次重排时也会用那个假屏宽重新定地板（自动再校准的必然结果，不是缺陷）。
# 所以「默认尺寸/最小尺寸」必须在动 cap 之前量（见本段开头那两条），在这儿量到的是
# 别人借用的假屏宽，断言的就不是用户看到的形状了。

groups = {g.title(): g for g in w.findChildren(QGroupBox)}
# ② 的标题在「本机缺采集通道」时会追加一句 ⚠ 告警（安装包漏文件的现场提示）。告警是
# 追加在同一条标题里的，短句判定必须切掉它——否则这条用例会因为界面**多说了真话**而红。
check("④ 分区标题一律短句（长说明已迁 tooltip；允许带一句括注，告警后缀不计）",
      all(len(t.split(gui.MainWindow.LOST_HINT_SEP)[0]) <= 20 for t in groups),
      str(list(groups)))
check("④ 每个分区都挂了悬停说明（信息量不减）",
      all(g.toolTip() for g in groups.values()),
      [t for t, g in groups.items() if not g.toolTip()])
check("④ 站点说明提为模块常量且覆盖全部在采站（qzd 是额外名单通道，不在 SITE_ORDER 里）",
      set(gui.SITE_ORDER) <= set(gui.SITE_TIPS) and "qzd" in gui.SITE_TIPS)

# 采集参数区：从 3 行压到 1 行
gb_p = groups["④ 采集参数"]        # ③ 让给了「采集平台」（站点勾选提为独立分区）
rows = [gb_p.layout().itemAt(i) for i in range(gb_p.layout().count())]
_lrows = [r for r in rows if r.layout()]


def _widgets(row):
    lay = row.layout()
    return {lay.itemAt(i).widget() for i in range(lay.count())
            if lay.itemAt(i).widget() is not None}


# 行数从「必须 1 行」改成「最多 2 行且每行放什么」：自动扩词/历史任务后来从操作行
# 迁到了这里，硬要求 1 行只会逼人把有用控件塞回按钮那一排（更挤）。
check("④ 采集参数区不超过 2 行控件", len(_lrows) <= 2, str(len(_lrows)))
check("④ 第一行是采集间隔与三个开关",
      _widgets(_lrows[0]) >= {w.spin_delay, w.chk_guard, w.chk_incr, w.chk_bg},
      sorted(type(x).__name__ for x in _widgets(_lrows[0])))
check("④ 第二行只有自动扩词与历史任务",
      _widgets(_lrows[1]) == {w.chk_auto_kw, w.btn_history},
      sorted(type(x).__name__ for x in _widgets(_lrows[1])))
check("④ 页数说明并入采集间隔 tooltip（不新开控件）",
      "翻页一律不限制" in w.spin_delay.toolTip())

# 云展：绑定状态 + 自动导入勾选 + 两个按钮同一行
gb_y = groups["云展动力客户库"]
check("④ 自动导入勾选并入云展分组（不再单独占一行）",
      w.auto_import_check in gb_y.findChildren(type(w.auto_import_check)))
check("④ 云展分组只有 1 行布局",
      gb_y.layout() is not None and gb_y.layout().count() >= 3)

# 主操作唯一：满屏强调色 = 没有重点
primaries = [b for b in w.findChildren(QPushButton) if b.objectName() == "primary"]
check("④ 全窗口仅「开始采集」一个主按钮吃强调色",
      len(primaries) == 1 and primaries[0] is w.btn_start)
check("④ 全局样式用 #primary 选择器而非逐按钮写死",
      "QPushButton#primary" in gui._app_stylesheet())

# 全局样式绝不写死窗口底色/文字色（否则深色模式又是一块隐形区）
appss = gui._app_stylesheet()
check("④ 全局样式边框走 palette()，跟随系统深浅色",
      "palette(mid)" in appss and "background-color: #f" not in appss)

# 源码级红线：不再允许任何写死的次要灰/提醒红字面量
hard = re.findall(r'setStyleSheet\("color: #(666|999|333|c62828)[^"]*"\)', SRC)
check("④ 全文件零残留写死次要色（13 处已全部改为主题自适应）", not hard, str(hard))

# ── ⑤ 绑定弹窗诚实化：标题与正文必须同源，失败绝不允许顶着「绑定成功」出门 ──
# 实机缺陷（用户截图）：绑定等级 2 账号时设备登记连不上 OSS，弹窗标题仍是
# 「绑定成功」、正文却是「设备登记失败」——成功弹窗只应属于整条链路都成功。
_bd = gui.MainWindow._bind_result_dialog
t, b, warn = _bd("13800000000", True, True, "", True, "")
check("⑤ 全链路成功才配「绑定成功」标题（信息脸）",
      t == "绑定成功" and not warn and "已获得使用权限" in b, (t, b))
t, b, warn = _bd("13800000000", True, False, "无法连接权限服务器，设备登记失败",
                 False, "无法连接权限服务器，请检查网络后重试")
check("⑤ 设备登记失败：标题降级为「账号已绑定，设备登记未完成」（警示脸）",
      "绑定成功" not in t and "设备登记未完成" in t and warn, (t, b))
check("⑤ 网络型失败必须承诺自动补齐并给手动重试出口（与自愈路径同源）",
      "自动" in b and "绑定云展动力账号" in b and "无法连接权限服务器" in b, b)
t, b, warn = _bd("13800000000", True, False, "本机设备登记被另一台设备抢先（机房本），请稍后重试",
                 False, "正在校验本机设备绑定")
check("⑤ 被抢先绝不承诺自动补齐：分片还在别人手里，自愈永远不会触发",
      "抢先" in t and "自动" not in b and "再点一次" in b, (t, b))
t, b, warn = _bd("13800000000", True, True, "", False, "当前账号无使用权限，请联系管理员获得使用权限")
check("⑤ 登记好了但不在名单：指向管理员而不是让人白查网络",
      "未获得使用权限" in t and "白名单" in b and "网络" not in b, (t, b))
t, b, warn = _bd("13800000000", False, True, "", True, "")
check("⑤ 账号没落盘=「绑定未完成」：saved=False 必须排在成功分支之前判定",
      t == "绑定未完成" and warn and "重新绑定" in b, (t, b))
_bsrc = inspect.getsource(gui.MainWindow._on_bind_finished)
_bcode = "\n".join(l for l in _bsrc.splitlines() if not l.strip().startswith("#"))
check("⑤ 弹窗文案唯一口径：_on_bind_finished 只许走 _bind_result_dialog，"
      "不许再就地拼「绑定成功」字面量（异常分支也在函数内，天然覆盖旧漏点）",
      "_bind_result_dialog" in _bcode and "绑定成功" not in _bcode, _bsrc[:200])

# ── ⑥ 「登记成功但没被放行」的下一步必须按 reason 分类 ──────────
# 顺着 ⑤ 追出来的第二个同类缺陷：check() 的 reason 有八种来源，旧口径不分青红皂白
# 一句「请联系管理员将该账号加入白名单」——可其中「连不上服务器」「还在确认」
# 「号在另一台机器上」三类找管理员完全没用，指错方向要用户和管理员一起买单。
_ttl = int(getattr(getattr(gui, "_access_guard", None), "CACHE_TTL_SECONDS", 60) or 60)
_den = gui.MainWindow._acl_denial
t, adv = _den("无法连接权限服务器，请检查网络后重试；仍无法使用请联系管理员")
check("⑥ 连不上服务器＝「还在确认中」，不叫人去找管理员加白名单",
      "确认中" in t and "白名单" not in adv and "自动" in adv, (t, adv))
check("⑥ 自动确认的时间必须与 CACHE_TTL_SECONDS 同源（写死数字会在改常量后变成假承诺）",
      str(_ttl) in adv, adv)
t, adv = _den("该云展账号当前绑定在另一台设备上（张三的 MacBook），本机还没有使用权限。")
check("⑥ 号在另一台机器：标题如实说清，且不重复 reason 已给出的操作指引",
      "另一台设备" in t and adv == "", (t, adv))
t, adv = _den("该云展账号的设备绑定已被解除（管理员解绑）。\n请点「绑定云展动力账号」重新登记本机后继续使用。")
check("⑥ 被解绑：交给 reason 自带的「重新绑定」指引，不再叠加无关建议",
      "重新确认" in t and adv == "", (t, adv))
t, adv = _den("当前账号无使用权限，请联系管理员获得使用权限")
check("⑥ 真·不在白名单才指向管理员", "未获得使用权限" in t and "白名单" in adv, (t, adv))
t, adv = _den("绑定的云展账号格式有误，请重新绑定")
check("⑥ 账号格式问题：让用户重绑，而不是找管理员",
      "核对" in t and "绑定云展动力账号" in adv and "管理员" not in adv, (t, adv))
check("⑥ denial 的每一种标题都不得冒充成功",
      all("绑定成功" not in _den(r)[0] for r in (
          "", "无法连接权限服务器", "当前账号无使用权限，请联系管理员获得使用权限",
          "正在校验本机设备绑定，请稍候重试", "权限校验异常，请重试", "该云展账号当前绑定在另一台设备上（X）")),
      [_den(r)[0] for r in ("", "无法连接权限服务器", "无使用权限")])
_ddsrc = inspect.getsource(gui.MainWindow._bind_result_dialog)
_ddcode = "\n".join(l for l in _ddsrc.splitlines() if not l.strip().startswith("#"))
check("⑥ 下一步口径唯一化：_bind_result_dialog 只许走 _acl_denial，"
      "不许再就地写「白名单」这类结论（否则新增的 reason 又会落到旧的一句流于形式的指引）",
      "_acl_denial" in _ddcode and "白名单" not in _ddcode, _ddsrc[:160])

# ── ⑦ 认领失败也必须分类：不能对「自愈永不触发」的两类承诺自动补齐 ──
# 只有远端确实还没有分片时，_on_shard_absent → _auto_enroll 才会自动补登记；
# 「被抢先」与「防顶号冷却」两类分片在别人手里，承诺自动补齐 = 引导用户干等。
_cf = gui.MainWindow._claim_failure
_cool = ("该账号刚在另一台设备上绑定过；为防止两台设备互相顶号，"
         "请 59 秒后再认领本机，或联系管理员确认设备归属")
t, b = _cf("13800000000", _cool)
check("⑦ 防顶号冷却：如实说明只能手动重试，绝不承诺自动抢回",
      "冷却" in t and "不会自动" in b and "手动重试" in b, (t, b))
t, b = _cf("13800000000", "本机设备登记被另一台设备抢先（李四的电脑），请稍后重试")
check("⑦ 被抢先：标题点名抢先，指向本机再点一次顶回",
      "抢先" in t and "顶回当前设备" in b and "不会自动" not in b, (t, b))
t, b = _cf("13800000000", "无法连接权限服务器，设备登记失败")
check("⑦ 网络型失败：自动补齐＋「号在别人手上仍需手动」的条件都写清（不片面承诺）",
      "自动补齐" in b and "另一台设备" in b and "绑定云展动力账号" in b, b)
t, b = _cf("13800000000", "手机号格式有误，无法登记设备")
check("⑦ 手机号格式：让用户核对重绑，不提自动也不提管理员",
      "设备登记未完成" in t and "核对手机号" in b and "自动" not in b, (t, b))
check("⑦ 四种认领失败各有各的标题，且都不许冒充成功",
      len({_cf("13800000000", m)[0] for m in
           (_cool, "本机设备登记被另一台设备抢先（X），请稍后重试",
            "无法连接权限服务器，设备登记失败", "设备登记写入失败：OSSPoolError")}) == 3
      and all("绑定成功" not in _cf("13800000000", m)[0] for m in
              (_cool, "抢先", "无法连接权限服务器", "手机号格式有误，无法登记设备")),
      [_cf("13800000000", m)[0] for m in (_cool, "抢先", "无法连接权限服务器")])
_cfcode = "\n".join(l for l in inspect.getsource(gui.MainWindow._bind_result_dialog).splitlines()
                    if not l.strip().startswith("#"))
check("⑦ 认领失败口径唯一化：_bind_result_dialog 只许走 _claim_failure，"
      "不许就地残留「抢先」分支（否则新增文案又会退回一句通用承诺）",
      "_claim_failure" in _cfcode and "抢先" not in _cfcode, _cfcode[:160])

# ── ⑧ 落盘失败＝整个绑定作废：内存身份不许先于磁盘切换 ──────────
# access_guard 判定权限时读的是磁盘上的绑定账号（task_registry.load_account）。
# 旧写法先改 self._yzd_phone 再存盘，存盘失败就留下两个身份：界面/云展功能按新号，
# 权限守卫按旧号——旧号的授权会被新号白拿，且比「绑定失败」本身难查得多。
t, b, warn = _bd("13900000000", False, True, "", True, "")
check("⑧ saved=False 文案必须说「本次绑定没有生效」，不再承诺「重启后会丢失」"
      "（身份已回滚，那句会描述一个不存在的情况）",
      t == "绑定未完成" and "没有生效" in b and "重启后会丢失" not in b, (t, b))
t, b, warn = _bd("13900000000", False, True, "", True, "", acl=False)
check("⑧ 权限层没加载时同一结论同一口径（两条 saved=False 分支不能一个更新一个陈旧）",
      t == "绑定未完成" and "没有生效" in b, (t, b))
check("⑧ saved=False 与登记成败无关：claim_ok 停在默认 True 也不能被读成成功",
      _bd("13900000000", False, True, "", True, "")[2] is True
      and _bd("13900000000", True, True, "", True, "")[2] is False)
_bidx = _bcode.find("self._yzd_phone = msg")
check("⑧ 内存身份只在落盘成功后切换：self._yzd_phone = msg 必须被 if saved 罩住",
      _bidx > 0 and "if saved" in _bcode[max(0, _bidx - 60):_bidx], _bcode[:260])
check("⑧ 日志标记与结论同源：保存失败时不许再打 ✅（同一缺陷在日志里的翻版）",
      '"✅ " if saved else "⚠ "' in _bcode, _bcode[:200])

# ── ⑨ 结论必须落在正文首行：macOS 上弹窗标题栏可能根本不显示 ──────
# 既往实机取证：QMessageBox 在 macOS 常以 sheet 呈现，windowTitle() getter 恒返回
# 空串。⑤~⑦ 把标题改诚实了，可如果标题看不见，用户读到的正文首行仍是
# 「已绑定云展动力账号」——读起来还是成功。所以结论必须同时是正文的第一行。
check("⑨ 成功/失败两条弹窗路径都把结论写在正文首行（不依赖标题栏可见）",
      'f"{title}\\n\\n{body}"' in _bcode and "绑定失败\\n\\n" in _bcode,
      _bcode[-420:])

print(f"\n全部通过：{ok} 项")
