# -*- coding: utf-8 -*-
"""本轮 GUI 四项优化的离线断言（offscreen 无头跑，不弹用户桌面、不碰真实数据目录）：
① 页数控件彻底删除、翻页一律不限（max_pages 固定 0）
② 「后台运行」默认勾选
③ 看板由 5 行压成 1 行、次要文字按深浅主题自适应（根治「已采 xxx 家下方空一块把日志挤出屏幕」）
④ 界面整洁化：长说明进 tooltip、参数区 1 行、主按钮唯一、日志给死最小高度
"""
import io
import os
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, "/Users/jhnoplan/Desktop/PythonDemo/b2bcollect_build")

from PyQt5.QtWidgets import QApplication, QGroupBox, QPushButton  # noqa: E402

import b2bcollect_gui as gui  # noqa: E402

SRC = io.open("/Users/jhnoplan/Desktop/PythonDemo/b2bcollect_build/b2bcollect_gui.py",
              encoding="utf-8").read()

ok = 0


def check(name, cond, detail=None):
    global ok
    assert cond, f"FAIL: {name}" + (f"  -> {detail}" if detail is not None else "")
    ok += 1
    print(f"  ✓ {name}")


app = QApplication.instance() or QApplication([])
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

# ── ④ 整洁化：布局行数 / 长文案归位 / 主按钮唯一 ────────────────
check("④ 日志给死最小高度（小屏绝不再被顶出屏幕）", w.log.minimumHeight() >= 180)
check("④ 主窗口默认尺寸按小屏收拢（宽<=1000 且高<=760）",
      w.width() <= 1000 and w.height() <= 760)
check("④ 最小尺寸允许进一步压缩", w.minimumWidth() <= 900 and w.minimumHeight() <= 640)

groups = {g.title(): g for g in w.findChildren(QGroupBox)}
check("④ 分区标题一律短句（长说明已迁 tooltip；允许带一句括注）",
      all(len(t) <= 20 for t in groups), str(list(groups)))
check("④ 五个分区都挂了悬停说明（信息量不减）",
      all(g.toolTip() for g in groups.values()))
check("④ 站点说明提为模块常量且覆盖全部在采站（qzd 是额外名单通道，不在 SITE_ORDER 里）",
      set(gui.SITE_ORDER) <= set(gui.SITE_TIPS) and "qzd" in gui.SITE_TIPS)

# 采集参数区：从 3 行压到 1 行
gb_p = groups["④ 采集参数"]        # ③ 让给了「采集平台」（站点勾选提为独立分区）
rows = [gb_p.layout().itemAt(i) for i in range(gb_p.layout().count())]
check("④ 采集参数区只剩 1 行控件",
      len([r for r in rows if r.layout()]) == 1, str(len(rows)))
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

print(f"\n全部通过：{ok} 项")
