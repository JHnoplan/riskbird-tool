# -*- coding: utf-8 -*-
"""布局体检：逐个控件比对「实际尺寸 vs 布局会保证的最小尺寸」。
QLabel 折行、按钮文字被裁这类问题，肉眼看截图容易误判，直接量几何更可靠。

为什么要跑多个尺寸：窗口高度小于内容自然最小值时，Qt 不是裁掉底部，而是把没设
硬最小值的控件（多行 QLabel、QPlainTextEdit）一路压到 1~10 像素——文字截断就是这么
来的。所以必须同时体检「真实启动尺寸」和「能拖到的最小尺寸」两档。

为什么要顶掉 _width_floor_cap：offscreen 的假屏幕只有 800x600，而窗口最小宽的天花板
就是可用屏宽。不顶掉它，「内容放不下时抬起窗口最小宽」这条主路在小屏假环境里会直接
退化成「封顶 + 退回横向滚动」，体检就永远测不到真机上真正生效的那一档。所以这里按
目标屏宽构造窗口，把真实显示环境搬进 CI。

还有一条主流程硬判定（2026-11-12 用户反馈「开始采集那些要滑动才能看到」）：主动作行
必须钉在滚动容器之外、且在窗口可见区内**完整可见**——用户不用滚就能开工。这一条挂了
就不再是「看着挤」的体验问题，而是回归。
用法：python check_gui_fit.py [模块名]（默认 b2bcollect_gui，可传改动前的临时副本做对照）
     档位/等级用环境变量换着跑：B2B_EDITION=lite B2B_ACL_LEVEL=2 python debug/check_gui_fit.py
     （丐版创建的控件少、内容最小宽不同，只测 pro 等于只测了一半形状）"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, "/Users/jhnoplan/Desktop/PythonDemo/b2bcollect_build")

from PyQt5.QtWidgets import QApplication, QScrollArea  # noqa: E402

mod_name = sys.argv[1] if len(sys.argv) > 1 else "b2bcollect_gui"
G = __import__(mod_name)

app = QApplication(sys.argv)

WANT_CORE = ["btn_start", "btn_pause", "btn_stop", "btn_export", "kw_text", "log",
             "btn_log_toggle"]
# 次要控件按「存在才体检」处理：档位投影会整组不创建它们（见 edition 投影），
# 写死清单会把「按档位藏起来」误报成「控件丢了」，而真正的丢失由 WANT_CORE 兜住。
WANT_MORE = ["btn_file", "btn_parse", "btn_smart", "btn_province", "lbl_province",
             "lbl_kw", "btn_clean", "btn_reset", "btn_clear_seen", "chk_guard",
             "chk_incr", "chk_bg", "chk_auto_kw", "btn_history",
             "btn_pool_import", "btn_pool_scan", "btn_pool_sync", "btn_pool_search",
             "btn_acl_admin", "bind_yzd_btn", "import_yzd_btn"]


def _want_for(w):
    # 判存在要看值不是看 hasattr：丐版路径上这些属性是**显式赋 None** 的
    # （控件根本没创建，但名字在类里有），hasattr 一律为真就会把「按档位收掉的入口」
    # 报成「控件丢了」——那是误报，而误报多了这条体检就没人信了。
    return WANT_CORE + [n for n in WANT_MORE if getattr(w, n, None) is not None]


def _in_scroll_area(wd) -> bool:
    """控件是否挂在可滚动容器里（挂在里面的话「要滚才能看到」是设计，不是事故）。"""
    p = wd.parent()
    while p is not None:
        if isinstance(p, QScrollArea):
            return True
        p = p.parent()
    return False


# 三档屏 × 两档窗口高：本机 Retina 逻辑 1280x800（可用 ~775）、常见 13 寸笔电
# 1280x775、小屏笔电 1024x640（低于配置内容最小宽，验证退路是「横向滚动条」而不是
# 「按钮被压扁」）。窗口尺寸取源码里那套自适应公式，测的就是用户实际会看到的形状。
CASES = [
    ("本机1280x800", 1280, 775, min(1120, max(880, 1280 - 100)), min(940, max(640, 775 - 80))),
    ("笔电1280x775", 1280, 775, 1000, 700),
    ("小屏1024x640", 1024, 640, 1000, 620),
]

all_bad = []
for tag, scr_w, scr_h, ww, hh in CASES:
    # 每档屏重新构造一个窗口：最小宽地板在首次 show 时按屏宽定死，复用同一个窗口
    # 改 cap 只会让「已经抬起来的地板」赖着不走，测出来的是混合状态。
    G.MainWindow._width_floor_cap = lambda self, _w=scr_w: _w
    w = G.MainWindow()
    w.show()
    app.processEvents()
    lay = w.centralWidget().layout()
    print(f"\n── {tag}｜屏宽 {scr_w} → 窗口最小 {w.minimumWidth()}x{w.minimumHeight()}"
          f"｜测试尺寸 {ww}x{hh}｜内容自然最小 "
          f"{lay.totalMinimumSize().width()}x{lay.totalMinimumSize().height()}")
    bad = []
    # ① 主动作行：不在滚动区里 + 完整落在窗口可见区内 + 没被压扁
    for i in range(w._action_lay.count()):
        it = w._action_lay.itemAt(i)
        b = it.widget() if it is not None else None
        if b is None or not isinstance(b, G.QPushButton) or not b.isVisible():
            continue
        label = b.text() or b.objectName()
        if _in_scroll_area(b):
            bad.append(f"{label}:在滚动容器里")
            print(f"  BAD  {label:12s} 主动作被放进了滚动容器（要滚才能点）")
            continue
        r = b.rect()
        top = b.mapTo(w, r.topLeft())
        bottom = b.mapTo(w, r.bottomRight())
        visible = (top.x() >= 0 and top.y() >= 0
                   and bottom.x() <= w.width() - 1 and bottom.y() <= w.height() - 1)
        need_w = min(max(b.minimumWidth(), b.minimumSizeHint().width()), b.maximumWidth())
        if not visible:
            bad.append(f"{label}:不在可见区")
            print(f"  BAD  {label:12s} 窗口 {w.width()}x{w.height()} 内看不到"
                  f"（位置 {top.x()},{top.y()} 尺寸 {r.width()}x{r.height()}）")
        elif b.width() < need_w:
            bad.append(f"{label}:被压扁")
            print(f"  BAD  {label:12s} 实际 {b.width()}x{b.height()} 宽需要 {need_w}")
    # ② 逐个控件比几何（含滚动区里的配置控件）
    for size_tag, tw, th in (("测试尺寸", ww, min(hh, scr_h)),
                             ("最小尺寸", w.minimumWidth(), w.minimumHeight())):
        w.resize(tw, th)
        app.processEvents()
        sbad = []
        for name in _want_for(w):
            wd = getattr(w, name, None)
            if wd is None:
                sbad.append(f"{name}:不存在")
                continue
            if not wd.isVisible():
                continue          # 隐藏控件的几何是陈旧值，量了只会误报
            geo = wd.geometry()
            # 布局真正会保证的是 max(硬性 minimumSize, minimumSizeHint)，只看后者会把
            # 「已经显式锁死底线」的控件误报成挤压，也漏掉「只设 hint 却没锁住」的真事故。
            # 再夹一次 maximumHeight：折叠抽屉的收起态是**故意**定高的（min==max==两行），
            # 它向布局要的那点自然高度拿不到是设计，不是事故。
            need_h = min(max(wd.minimumHeight(), wd.minimumSizeHint().height()),
                         wd.maximumHeight())
            need_w = min(max(wd.minimumWidth(), wd.minimumSizeHint().width()),
                         wd.maximumWidth())
            ok = geo.height() >= need_h - 1 and geo.width() >= need_w - 1
            if not ok:
                sbad.append(f"{name}({geo.width()}x{geo.height()}<{need_w}x{need_h})")
        print(f"  {size_tag} {w.width()}x{w.height()}: "
              + ("全部够位" if not sbad else f"挤压 {sbad}"))
        bad += [s.split("(")[0] for s in sbad]
    # ③ 日志折叠抽屉：默认收起时绝不该把配置区挤掉（收起态定高、展开态才抢富余高度）
    w._apply_log_area(False)
    w.resize(ww, min(hh, scr_h))
    app.processEvents()
    if w.log.height() != w.log.maximumHeight():
        bad.append("日志收起态没定高")
        print(f"  BAD  日志收起态高度 {w.log.height()} != 上限 {w.log.maximumHeight()}")
    if not _in_scroll_area(w.log):
        print(f"  OK   收起态日志 {w.log.height()}px｜配置可视区 "
              f"{w._cfg_scroll.viewport().height()}px｜窗口 {w.height()}px")
    all_bad += [f"[{tag}] {b}" for b in bad]
    w.deleteLater()
    app.processEvents()

print(f"\n[{mod_name}] RESULT:", "ALL FIT（0 处挤压）" if not all_bad else f"挤压 -> {all_bad}")
sys.exit(1 if all_bad else 0)
