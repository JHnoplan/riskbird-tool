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
     完整/受限两档形状由本脚本自己逐档量（见下面 _SHAPES 那段），不需要调用方
     带环境变量——以前那句「档位/等级用环境变量换着跑」写在头上却从来没人带，
     等于每轮批跑只量了一个形状。构建档位仍可换：B2B_EDITION=lite python debug/check_gui_fit.py"""
import os
import pathlib
import shutil
import socket
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "b2bcollect_build"))

# ── 体检自己必须是只读旁观者（不做到这一步，下面的数字都不可信）─────────────
# 建真实 MainWindow 会连带起权限心跳（QTimer，间隔 max(15s, CACHE_TTL)）和 1.5s
# 开机收口；而下面每档屏都要 processEvents()，两档形状 × 三档屏跑下来早过 15 秒
# ——心跳就会拿**本机绑定的真凭据**去 OSS 拉真名单，把真实手机号写进工具状态目录
# 里的 acl/.acl_cache.json，顺便让这条名字里带着 offline 的批跑出一次真网。
# 后果不止是脏：test_acl_levels_offline 随后会读到那份真名单，同一份代码在本机红、
# 在干净 clone 绿（2026-11-20 实测：批跑 10:33:35 起、缓存写于 10:35:13，正对
# check_gui_fit.log 的 mtime）。而换一台没绑账号、没 OSS 凭据的机器（交接后的新
# Mac），这一格量的又是另一种形状——几何体检到底在量哪套控件全凭现场状态。
# 所以三件事一起做：状态目录整体挪进临时目录、封掉域名解析、把要量的等级
# 写进循环（完整/受限两档都量，见下），让体检只依赖声明、不依赖现场。
_STATE_TMP = pathlib.Path(tempfile.mkdtemp(prefix="gui_fit_state_"))
_net_attempts = []


def _blocked_getaddrinfo(*args, **kwargs):
    _net_attempts.append(args[0] if args else "?")
    raise socket.gaierror(8, "check_gui_fit：布局体检不许出网")


socket.getaddrinfo = _blocked_getaddrinfo

from PyQt5.QtWidgets import QApplication, QScrollArea  # noqa: E402

mod_name = sys.argv[1] if len(sys.argv) > 1 else "b2bcollect_gui"
G = __import__(mod_name)

import access_guard as _ag  # noqa: E402
import edition as _ed       # noqa: E402
import task_registry as _tr  # noqa: E402

_ag._state_dir = lambda: _STATE_TMP
assert _ag._state_dir() == _STATE_TMP, "状态目录没接上：三份 .acl_*.json 会写到真实桌面目录"

# 同一族的第二个写入口：会话目录与任务索引都挂在 _get_desktop_dir() 下，而
# task_registry.tool_dir() 自己就写着「不存在则创建」。今天实测桌面那个目录没被动
# （deleteLater 不走 closeEvent，所以 _persist_progress 没被触发），但那是「当前
# 实现恰好没踩到」而不是「构造上不可能」：GUI 侧加一句建窗期落盘就会变成真写盘，
# 后果是用户真实采集会话被指向临时目录（2026-11-04 实案， 89 条数据险丢）。
# 仓库里其它建真实 MainWindow 的套件（waterfall / site_enrich / keyword_resume /
# province / edition）都已经重定向了这一处，本脚本不能豁免。
_DESK_SANDBOX = _STATE_TMP / "desktop"
_DESK_SANDBOX.mkdir()
_desk_patch = lambda self: _DESK_SANDBOX   # noqa: E731  留着引用只为收尾能比对身份
G.MainWindow._get_desktop_dir = _desk_patch

# 对拍用指纹，不是「存在性检查」：新建一个会话夹、改写一份索引都会体现在这里。
# 目录本身不在时得到空指纹，这也要对拍——体检不该把它顺手建出来。
_DESK_REAL = pathlib.Path.home() / "Desktop" / _tr.TOOL_DIR_NAME


def _desk_fingerprint():
    rows = []
    if _DESK_REAL.exists():
        for p in sorted(_DESK_REAL.rglob("*")):
            rel = p.relative_to(_DESK_REAL).as_posix()
            if p.is_dir():
                rows.append(f"D|{rel}")
            else:
                st = p.stat()
                rows.append(f"F|{rel}|{st.st_size}|{st.st_mtime_ns}")
    return rows


_desk_before = _desk_fingerprint()

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
_SCREEN_CASES = [
    ("本机1280x800", 1280, 775, min(1120, max(880, 1280 - 100)), min(940, max(640, 775 - 80))),
    ("笔电1280x775", 1280, 775, 1000, 700),
    ("小屏1024x640", 1024, 640, 1000, 620),
]

# 两档形状都量，而不是只量 pro：文件开头自己就写着「只测 pro 等于只测了一半
# 形状」，可批跑从来不带环境变量——那句话于是只是一句用法备注，从未被执行。
# 等级走 B2B_ACL_LEVEL 这个声明式接缝（仅源码运行生效），而不是靠本机绑定关系
# 碰运气：开发机拉到真名单是 pro 形状，新机器什么也没有就是另一种形状，
# 同一份判据在两台机器上量两套控件——那比不量更容易骗人。
_SHAPES = ((_ed.LEVEL_ADMIN, "完整"), (_ed.LEVEL_LITE, "受限"))
CASES = [(f"{shape}·{tag}", lvl, scr_w, scr_h, ww, hh)
         for lvl, shape in _SHAPES
         for (tag, scr_w, scr_h, ww, hh) in _SCREEN_CASES]

all_bad = []
_prev_lvl = None
for tag, lvl, scr_w, scr_h, ww, hh in CASES:
    if lvl != _prev_lvl:            # 同一等级内切屏不必反复重定档
        os.environ[_ed.LEVEL_ENV_VAR] = str(lvl)
        _ed.reset_for_tests()       # 等级挂在模块全局上，不清会把上一档带到下一档
        _prev_lvl = lvl
        print(f"\n【等级 {lvl}｜{_ed.LEVEL_NAMES.get(lvl, '?')}】")
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
# 出网尝试只报数、不改判定：拦停本身已经消除了危害（写脏真名单、烧真额度），
# 而这个数字要让人看见——它从 0 变成非 0，说明开机路径上多了一处建窗即取网络。
print(f"· 体检期间出网尝试 {len(_net_attempts)} 次（已全部拦停）")
# 用户真实桌面工具目录必须逐字节没动过：这一句把上面那个「恰好没踩到」钉成不变量，
# 以后谁在建窗路径上加一句落盘，这里就会红（而不是等到用户的采集数据找不到）。
_desk_after = _desk_fingerprint()
assert _desk_after == _desk_before, (
    f"体检改动了用户真实桌面目录 {_DESK_REAL}："
    f"新增 {sorted(set(_desk_after) - set(_desk_before))}｜"
    f"消失 {sorted(set(_desk_before) - set(_desk_after))}")
print(f"· 真实桌面目录指纹未变（{_DESK_REAL.name if _DESK_REAL.exists() else '目录不在'}，"
      f"{_desk_before and len(_desk_before) or 0} 项）")
# 临时状态目录当场回收：本套件的收尾走 os._exit（见下面那段说明），Qt 析构与
# atexit 都不会替我们删；而批跑每天要建 6 个形状 × 若干份 .acl_*.json，留在 /var
# 下就是「没人认领的目录」——判据 ㊴ 骂的正是这一格，不能只对探针生效、对体检豁免。
shutil.rmtree(_STATE_TMP, ignore_errors=True)
assert not _STATE_TMP.exists(), f"临时状态目录没回收：{_STATE_TMP}"
assert _ag._state_dir() == _STATE_TMP, "状态目录中途被换回去了（守卫自己重新判定了落点）"
assert socket.getaddrinfo is _blocked_getaddrinfo, "DNS 硬闸被拆了——收尾前不该有人恢复它"
assert G.MainWindow._get_desktop_dir is _desk_patch, "桌面重定向中途被拆了（下面就要收临时目录）"
# 判定到此为止：屏幕上这句和批跑读到的退出码必须是同一个来源。这套件自己建过
# QApplication，而 Qt/线程在解释器收尾时的析构会随机段错误（本机实测：keyword_resume
# 全绿 88 项之后 rc=139，被批跑读成一次失败，单跑又绿——真红就是这么被当成抖动的）。
# flush 之后直接把退出码交给操作系统，不让任何收尾动作再改写判定。
sys.stdout.flush()
sys.stderr.flush()
os._exit(1 if all_bad else 0)
