# -*- coding: utf-8 -*-
"""离线自测：交付档位（丐版 lite / 内部版 pro）。

需求本体（2026-11-11 用户原话）：「打包一个丐版的工具出去给外面的人用，不想把全部功能
交底……不是删除功能，而是隐藏了这些按钮，让他们不知道有这些功能，也没有入口……公司名单
这里只留企知道平台……注意不要删了功能，只是做隐藏和 GUI 布局调整，一定要合理安排页面」，
外加一条后来补上的：「采集完自动上传数据到资源池（用户不知道，后台自动上传）」。

这类改动真正要防的五件事，本文件逐个钉住：
  ① 隐藏不彻底——留了 Tab 焦点、右键、快捷键、对话框里的第二条勾选、日志文案，就等于没藏；
  ② 文案先交底——按钮藏住了，标题还写着「三平台」、tooltip 还教用户「历史种子库」；
  ③ 布局塌陷——把控件藏掉而那一行还在，界面莫名其妙空一块；
  ④ 功能被顺手删——内部版本来天天在用这些入口，删了就是自断后路；
  ⑤ 磁盘上的痕迹——界面干净了，采集完却在用户桌面长出一个「数据池缓存」文件夹。
静默上传另算一组：不能出声、不能重复传、不能把没传成写成传过了。

全程不联网、不启 Chrome、不碰真实数据池缓存目录：Qt 走 offscreen，上传线程换成假 worker，
台账/留痕目录指到临时沙箱。
"""
import inspect
import json
import ast
import os
import re
import sys
import textwrap
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "b2bcollect_build"))

ok = 0
fail = []


def check(name, cond, extra=""):
    global ok
    if cond:
        ok += 1
        print(f"  ✓ {name}")
    else:
        fail.append(name)
        print(f"  ✗ {name}  {extra}")


TMP = tempfile.mkdtemp(prefix="edition_test_")

import edition       # noqa: E402
from parallel_session import LIST_CHANNEL_SLUGS, QZD_SLUG  # noqa: E402

# 外部环境里残留的 B2B_EDITION 会把 A 组「未配置默认 pro」那几条直接带偏（CI/开发机
# 挂过这个变量很常见），本文件的每一条都由测试自己设档位，进来先清场。
os.environ.pop(edition.ENV_VAR, None)
# 等级环境变量决定的是「同一个包里这个账号能用到哪一档」，留着它同样会把 A/B 两组的
# 默认结论带偏（比如残留 B2B_ACL_LEVEL=2 会让 pro 组的按钮检查全线报缺），一并清掉。
os.environ.pop(edition.LEVEL_ENV_VAR, None)
edition.reset_for_tests()

# 主窗口一构造就会走 _refresh_yzd_status → 权限徽章 → guard.check()。本文件的承诺是
# **全程不联网**，所以这里把守卫钉成确定性离线态：不建 OSS 客户端、本地副本读到临时
# 沙箱（否则它会读我桌面上真实的名单副本，结论随我的机器状态漂）。
import access_guard as _ag                          # noqa: E402
_ag.AccessGuard._build_client = staticmethod(lambda: None)
_ag._state_dir = lambda: Path(TMP) / "acl_state"

# ══════════════════════════════════════════════════════════════════ A. 档位真源
print("\n[A. 档位只有一处判定]")
check("档位名只有 pro / lite 两种", set(edition.KNOWN_EDITIONS) == {"pro", "lite"},
      edition.KNOWN_EDITIONS)
mx = edition.matrix()
check("两档的能力集合互斥（同一能力不会又开又关）",
      set(mx) == {edition.PRO, edition.LITE})
for _e, row in mx.items():
    check(f"{_e} 档对每个能力都有明确取值（不许缺项靠默认）",
          set(row) == set(edition.ALL_CAPS), sorted(set(edition.ALL_CAPS) - set(row)))
check("「弹窗问同步」与「静默同步」在两档里都恰好只开一个（否则要么弹完窗又静默传一遍、"
      "要么数据谁都不管）",
      all(bool(r[edition.CAP_POOL_PROMPT]) ^ bool(r[edition.CAP_POOL_SILENT])
          for r in mx.values()), mx)
check("对外档不留任何数据池按钮入口、也不弹窗",
      not mx[edition.LITE][edition.CAP_POOL_UI]
      and not mx[edition.LITE][edition.CAP_POOL_PROMPT])
check("对外档把三个内部入口和多余名单通道都关掉",
      not mx[edition.LITE][edition.CAP_CLEAR_SEEN]
      and not mx[edition.LITE][edition.CAP_HISTORY_TASKS]
      and not mx[edition.LITE][edition.CAP_AUTO_EXPAND_UI]
      and not mx[edition.LITE][edition.CAP_LIST_ALL_CHANNELS])
check("内部版四项全开（证明这次改造没有把功能从代码里拿走）",
      all(mx[edition.PRO][c] for c in (edition.CAP_POOL_UI, edition.CAP_POOL_PROMPT,
                                       edition.CAP_CLEAR_SEEN, edition.CAP_HISTORY_TASKS,
                                       edition.CAP_AUTO_EXPAND_UI,
                                       edition.CAP_LIST_ALL_CHANNELS)))
os.environ[edition.ENV_VAR] = "lite"
edition.reset_for_tests()
check("丐版只留企知道一个名单通道，内部版仍是全量（判据来自同一份投影）",
      edition.visible_list_channels(LIST_CHANNEL_SLUGS, QZD_SLUG) == (QZD_SLUG,)
      and edition.title_suffix() != ""
      and edition.auto_expand_visible() is False
      and edition.pool_ui_visible() is False)
edition.reset_for_tests()
os.environ[edition.ENV_VAR] = "pro"
edition.reset_for_tests()
check("内部版名单通道全量可见且标题不带后缀（标题后缀是唯一可辨识的档位标记）",
      edition.visible_list_channels(LIST_CHANNEL_SLUGS, QZD_SLUG) == tuple(LIST_CHANNEL_SLUGS)
      and edition.title_suffix() == "" and edition.pool_ui_visible() is True)
del os.environ[edition.ENV_VAR]
edition.reset_for_tests()
os.environ[edition.ENV_VAR] = "lite"
check("环境变量能把档位定成 lite", edition.resolve()["edition"] == "lite",
      edition.resolve())
os.environ[edition.ENV_VAR] = "  "
edition.reset_for_tests()
check("环境变量是空串算「没配」（B2B_EDITION= 是常见写法，不该被当成配错）",
      edition.resolve()["edition"] == "pro", edition.resolve())
os.environ[edition.ENV_VAR] = "LITE"
edition.reset_for_tests()
check("档位名大小写不敏感", edition.resolve()["edition"] == "lite")
os.environ[edition.ENV_VAR] = "gite"          # 手打错一个字母
edition.reset_for_tests()
check("配错档位一律降级到最保守档（fail-closed：宁可少给功能，不可把内部版发出去）",
      edition.resolve()["edition"] == "lite" and edition.resolve_warnings(),
      edition.resolve())
del os.environ[edition.ENV_VAR]
edition.reset_for_tests()
_tf = Path(tempfile.mkdtemp(prefix="edition_flag_")) / edition.FLAG_FILENAME
_tf.write_text('{"edition": "lite"}', encoding="utf-8")
_real_files = edition._flag_files
edition._flag_files = lambda: [_tf]
check("随包标记文件能把档位定成 lite（打包时用这条，不依赖环境变量）",
      edition.resolve(force=True)["edition"] == "lite", edition.resolve())
_tf.write_text("{ 坏 JSON", encoding="utf-8")
check("标记文件读不懂也按最保守档跑（打包脚本写坏文件不许等于发出全功能版）",
      edition.resolve(force=True)["edition"] == "lite")
edition._flag_files = _real_files
edition.reset_for_tests()
os.environ[edition.ENV_VAR] = "pro"
edition.reset_for_tests()
os.environ.pop(edition.ENV_VAR, None)
check("清掉两处配置就回到内部版（日常开发机行为与改造前一致）",
      edition.resolve(force=True)["edition"] == "pro")
edition.reset_for_tests()

# ══════════════════════════════════════════════════════════════════ B. 界面投影
print("\n[B. 界面上到底有没有这些入口]")
import b2bcollect_gui as gui                                   # noqa: E402
from PyQt5.QtWidgets import (QApplication, QWidget, QPushButton,  # noqa: E402
                             QCheckBox, QGroupBox, QToolButton, QLabel)
from PyQt5.QtCore import QPoint, Qt                  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
gui.QMessageBox.information = staticmethod(lambda *a, **k: gui.QMessageBox.Cancel)
gui.QMessageBox.question = staticmethod(lambda *a, **k: gui.QMessageBox.No)

BANNED_TEXTS = ("导入历史数据", "扫描本地表格", "同步数据池", "搜索采购商",
                "清空历史种子库", "历史任务", "自动扩词", "风鸟", "爱企查")
BANNED_TIPS = BANNED_TEXTS + ("数据池", "历史种子库", "三平台", "OSS")


def _build(ed, lv=None):
    """按指定档位（可再加等级）真造一个 MainWindow（沙箱桌面目录，绝不污染真实任务索引）。

    每个组合用各自的沙箱目录：不然 pro 与 pro+等级2 两个窗口会共用同一份任务索引，
    后建的窗口在「接上次的任务」那条路上读到前一个窗口的状态，结论就不是它自己的。

    心跳必须在这里停掉（2026-11-20 本机取证：批跑红 5 条、单跑 166 条全绿）。
    `_acl_timer` 是 60 秒一拍，每拍走 `_on_acl_event("")`，而那个函数**无条件**调
    `_acl_sync_level()`——把「此刻进程里全局的账号等级」倒灌进这个窗口。本套件一次造
    十几个窗口、又反复 processEvents：只要哪一小节的墙钟越过 60 秒（批跑机器比开发机
    忙，正好越过），后面小节拿到的「按等级 2 建好的干净窗口」其实早被前一拍按更高档
    重投影过了，于是「前置状态：没建数据池入口」这类前提**悄悄失效**，红的全是它的
    下游，而报表看上去像产品写坏了。结论不许由墙钟决定：要演练心跳的小节自己 start()。
    """
    tag = ed if lv is None else f"{ed}_lvl{lv}"
    gui.MainWindow._get_desktop_dir = lambda self, _t=tag: Path(TMP) / f"desk_{_t}"
    (Path(TMP) / f"desk_{tag}").mkdir(parents=True, exist_ok=True)
    os.environ[edition.ENV_VAR] = ed
    # 等级经 B2B_ACL_LEVEL 注入（仅源码运行生效，打包态一律忽略——所以这条只在测试里成立）
    if lv is None:
        os.environ.pop(edition.LEVEL_ENV_VAR, None)
    else:
        os.environ[edition.LEVEL_ENV_VAR] = str(lv)
    edition.reset_for_tests()
    win = gui.MainWindow()
    _timer = getattr(win, "_acl_timer", None)      # 见本函数 docstring：停掉墙钟那一拍
    if _timer is not None:
        _timer.stop()
    return win


def _pin_level(value):
    """把等级环境变量钉成 value，返回进来时的原值给 `_unpin_level` 恢复用。

    这一对存在的理由：等级环境变量是**建窗那一刻的输入**，不是持久状态。本节钉它、
    收尾不恢复，就等于替后面的节改了来路（2026-11-20 实测：留着它会压过
    `set_account_level()` 的内存覆盖，把一条本来能红的等式判据修成恒真）。
    """
    prev = os.environ.get(edition.LEVEL_ENV_VAR)
    if value is None:
        os.environ.pop(edition.LEVEL_ENV_VAR, None)
    else:
        os.environ[edition.LEVEL_ENV_VAR] = str(value)
    return prev


def _unpin_level(prev):
    """恢复 `_pin_level` 记下的原值。

    本文件开头（第 57 行那一句）已经进来先清过等级环境变量，所以今天 `prev` 实际上
    永远是 None——保留「恢复」而不是写成 `pop` 是为了不把两个语义绑死：将来若有人改了
    开头那句清场策略，这一对助手仍然是「本节不渗到后节」，而不是「本节替全文件做主」。
    """
    if prev is None:
        os.environ.pop(edition.LEVEL_ENV_VAR, None)
    else:
        os.environ[edition.LEVEL_ENV_VAR] = prev


# 两个根窗口的等级必须**声明**，不能留给现场（2026-11-20 干净 clone 实测：同一份
# 代码本机绿、clone 红一项）。`lv=None` 会让 `_build` 把 B2B_ACL_LEVEL 从环境里**清掉**，
# 于是 pro 窗口里那三路名单补采平台（企知道/风鸟/爱企查）建不建得出来，取决于这台
# 机器此刻有没有绑定账号、acl/ 里有没有拉到真名单：开发机上永远是「管理员」，clone 里
# 就只剩企知道——「内部版同一句仍报全三平台」那条断言量的其实是「这台机器是谁」。
# 本文件后面的节早就逐条显式钉了等级（`_build("pro", edition.LEVEL_LITE)` 等），只有
# 这两个被全文件复用的根窗口漏了。钉成 ADMIN 与开发机此前一直拿到的值一致，
# 所以不会把别的节改坏；而 lite 也钉 ADMIN，是为了让「藏起来」这一结论只可能由
# 档位造成，不是由权限造成（那正是本套件要证的命题）。
_level_env_before = os.environ.get(edition.LEVEL_ENV_VAR)
w_pro = _build("pro", edition.LEVEL_ADMIN)
w_lite = _build("lite", edition.LEVEL_ADMIN)
# 建完立刻把等级环境变量恢复到本节之前的状态：`_build` 是把它**留在环境里**的，
# 而它压过 `set_account_level()` 的内存覆盖（后面「能力矩阵」那一节正好依赖这个
# 压过语义）。留着不擦，「pro+等级2 == lite 包」这条等式两边会被同钉成同一个等级，
# 于是一条本来能红的判据变成恒真——机器无关性是修好了，判据自己却被修死。
_unpin_level(_level_env_before)
# 这一格只问「有没有恢复到进来时那一副环境」，而不是「环境变量一定不在」：两者今天
# 等价（开头那句清场让 `_level_env_before` 事实上恒为 None），但后一种写法把「本套件自己
# 接管等级」这条局部分工写成了全局断言——将来有人改了开头那句，判据就会红在一件
# 根本不是缺陷的事上。（上一版这里写的理由是错的：它说“外部带着变量会被抹掉”，
# 而第 57 行根本不让那种变量活到这一格——记错的因比缺判据更容易误导下一个人。）
check("根窗口的等级靠声明而不是靠现场（建完就把环境变量恢复原样，"
      "不拿它去污染后面的节）",
      os.environ.get(edition.LEVEL_ENV_VAR) == _level_env_before,
      f"进来时={_level_env_before}｜现在={os.environ.get(edition.LEVEL_ENV_VAR)}")


def _labels(win, cls):
    return [x.text() for x in win.findChildren(cls)]


def _hit_labels(win, words):
    ls = _labels(win, QPushButton) + _labels(win, QCheckBox)
    return [f"{t}×{w}" for t in ls for w in words if w and w in t]


check("内部版界面上四个数据池按钮都在（隐藏是投影出来的，不是删掉的）",
      {"导入历史数据", "扫描本地表格", "同步数据池", "搜索采购商"}
      <= set(_labels(w_pro, QPushButton)), _labels(w_pro, QPushButton))
check("内部版「清空历史种子库 / 历史任务 / 自动扩词」都在",
      "清空历史种子库" in _labels(w_pro, QPushButton)
      and "历史任务" in _labels(w_pro, QPushButton)
      and "自动扩词" in _labels(w_pro, QCheckBox))
check("内部版三平台补采勾选框与登录按钮齐全",
      len(w_pro.chk_list) == len(LIST_CHANNEL_SLUGS)
      and len(w_pro.login_buttons) == len(LIST_CHANNEL_SLUGS)
      and len(LIST_CHANNEL_SLUGS) >= 2, (len(w_pro.chk_list), LIST_CHANNEL_SLUGS))

check("丐版界面上一个被禁按钮的文字都找不到（连灰按钮都不许有——灰按钮也是在告知）",
      not _hit_labels(w_lite, BANNED_TEXTS), _hit_labels(w_lite, BANNED_TEXTS))
check("丐版四个数据池按钮对象根本没被创建（不是 setVisible(False)，所以也不可能被"
      "Tab 走到、被 findChildren 遍历到）",
      w_lite.btn_pool_import is None and w_lite.btn_pool_scan is None
      and w_lite.btn_pool_sync is None and w_lite.btn_pool_search is None)
check("丐版「清空历史种子库 / 历史任务 / 自动扩词」控件不存在",
      w_lite.btn_clear_seen is None and w_lite.btn_history is None
      and w_lite.chk_auto_kw is None)
check("丐版名单区只剩企知道一路（勾选框与「打开 XX」按钮同源收敛）",
      set(w_lite.chk_list) == {QZD_SLUG} and set(w_lite.login_buttons) == {QZD_SLUG},
      (set(w_lite.chk_list), set(w_lite.login_buttons)))
check("丐版企知道补采仍默认勾上（不留一个「看起来没有任何平台在跑」的空区）",
      w_lite.chk_list[QZD_SLUG].isChecked())
check("丐版「未勾选＝不跑」的编排语义没被动过：勾选判据仍只读界面上存在的控件",
      w_lite._list_want_slugs() == [QZD_SLUG], w_lite._list_want_slugs())
check("风鸟/爱企查的采集器与通道注册表都还在（只藏入口，功能一行没删）",
      len(w_lite.session.channels) == len(LIST_CHANNEL_SLUGS)
      and set(w_lite.session.channels) >= {QZD_SLUG},
      set(w_lite.session.channels))

# ── 文案交底扫描：按钮藏了、话没藏，一样算泄露 ────────────────────
def _tip_hits(win, words):
    out = []
    for wd in win.findChildren(QWidget):
        tip = wd.toolTip() or ""
        for w in words:
            if w in tip:
                out.append(f"{wd.metaObject().className()}:{w}")
    for gb in win.findChildren(QGroupBox):
        for w in words:
            if w in (gb.title() or ""):
                out.append(f"GroupBox标题:{w}")
    return out


hits = _tip_hits(w_lite, BANNED_TIPS)
check("丐版整窗的 tooltip 与分区标题里，被禁功能与「数据池/风鸟/爱企查/三平台」这些词"
      "一个都不出现（文字先交底，按钮藏得再好也白搭）", not hits, hits)
check("丐版公司名单区标题改口成「企知道逐家补采」（内部版仍是「三平台逐家补采」）",
      any("公司名单（企知道逐家补采）" in g.title() for g in w_lite.findChildren(QGroupBox))
      and any("公司名单（三平台逐家补采）" in g.title() for g in w_pro.findChildren(QGroupBox)),
      [g.title() for g in w_lite.findChildren(QGroupBox) if "公司名单" in g.title()])


def _title_parts(win) -> list:
    """标题按「·」拆段：gui 里那一句是「产品名 + 档位后缀 + 版本后缀」拼出来的。

    为什么不再写 endswith：需求 2 在档位后面追了一段版本（`· 版本 1.234` /
    `· 版本未能确认`），那三条 endswith 会同时坏在两个方向上——该带「精简版」的那侧红，
    不该带的那侧**真空满足**（没有任何标题以它结尾，这一半等于根本没在跑）。按段核既放得
    下新增的版本段，又把「档位那一段在不在」问得准。
    """
    return [s.strip() for s in str(win.windowTitle()).split("·")]


check("丐版标题栏带「精简版」，内部版不带（跑的是哪版必须一眼可辨）",
      "精简版" in _title_parts(w_lite) and "精简版" not in _title_parts(w_pro),
      (w_lite.windowTitle(), w_pro.windowTitle()))
check("标题栏段序是「产品名 · 档位 · 版本」（版本段插在档位前面就会把档位挤成中间态；"
      "两档除档位那一段以外必须完全相同，否则「一眼可辨」辨的是两件不同的事）",
      _title_parts(w_lite)[0] == _title_parts(w_pro)[0]
      and _title_parts(w_lite)[-1] == _title_parts(w_pro)[-1]
      and _title_parts(w_lite)[1] == "精简版" and len(_title_parts(w_pro)) == 2,
      (_title_parts(w_lite), _title_parts(w_pro)))

# ── 布局不许塌：藏完不能留一行空白 ──────────────────────────────
def _group(win, key):
    for g in win.findChildren(QGroupBox):
        if key in g.title():
            return g
    return None


def _rows(g):
    lay = g.layout()
    return [lay.itemAt(i) for i in range(lay.count())] if lay else []


g_lite = _group(w_lite, "采集参数")
g_pro = _group(w_pro, "采集参数")


def _row_widgets(item):
    """一行布局里真正挂在布局上的控件（嵌套子布局一起收）。"""
    lay = item.layout()
    out = []
    if not lay:
        return out
    for i in range(lay.count()):
        sub = lay.itemAt(i)
        if sub is None:
            continue
        if sub.widget() is not None:
            out.append(sub.widget())
        out.extend(_row_widgets(sub))
    return out


# 判据换过一轮：原来钉的是「丐版把那行整行撤掉，所以只剩 1 行」。功能⑤的清洗档位与
# 功能④的手动补采都恒定挂在这一行（数据口径开关不能跟着功能入口一起藏，见 GUI 注释），
# 于是「行数」不再是可用的判据。真正要守的是**不留空行**：一行里必须至少有一个控件
# 真的可见——只把控件 setVisible(False) 而整行照加，症状和用户说不清的那次一模一样。
_lite_rows = [_row_widgets(x) for x in _rows(g_lite)]
check("丐版「④ 采集参数」不留空行：每一行都得有真的可见控件"
      "（只藏控件不撤行 = 界面莫名其妙空了一块）",
      bool(_lite_rows) and all(any(x.isVisibleTo(g_lite) for x in ws) for ws in _lite_rows),
      [(i, [f"{x.metaObject().className()}:{x.text()}" for x in ws])
       for i, ws in enumerate(_lite_rows)])
# 原来这条钉的是「内部版恰好两行」。瀑布补全/线索分层/到期重验三个开关进来后是 3 行，
# 于是红了——红得没有意义：行数是被功能数量推着走的代理量，钉死它的后果是每加一个合规
# 开关都得回来改期望值，而这一格真正会坏的两件事（控件被挪出这个分区、整行空掉）它一次
# 也没判住。改成按真性质判，与上面丐版那条同口径。
_pro_rows = _rows(g_pro)
check("内部版「④ 采集参数」每行都有真的可见控件（只藏控件不撤行 = 空一块，同丐版口径）",
      bool(_pro_rows) and all(any(x.isVisibleTo(g_pro) for x in _row_widgets(r))
                              for r in _pro_rows),
      [(i, [f"{x.metaObject().className()}:{x.text()}" for x in _row_widgets(r)])
       for i, r in enumerate(_pro_rows)])
check("自动扩词勾选与历史任务入口仍在「④ 采集参数」分区里"
      "（当年从操作行迁进来的那件事不许悄悄回退，也不许被塞到别的分组）",
      any(x.text() == "自动扩词" for x in g_pro.findChildren(QCheckBox))
      and any(x.text() == "历史任务" for x in g_pro.findChildren(QPushButton)),
      [x.text() for x in g_pro.findChildren(QCheckBox)])
check("丐版同样看得见「严格按关键词清洗」与「补采官网联系方式」：数据口径开关不是功能入口，"
      "藏掉它就会出现「界面上没这项、值却还在生效」那种最难解释的账",
      any(x.text() == "严格按关键词清洗" and x.isVisibleTo(w_lite)
          for x in g_lite.findChildren(QCheckBox))
      and any(x.text() == w_lite.BTN_SITE_ENRICH and x.isVisibleTo(w_lite)
              for x in g_lite.findChildren(QPushButton)),
      [x.text() for x in g_lite.findChildren(QCheckBox)]
      + [x.text() for x in g_lite.findChildren(QPushButton)])
# 上面那条把两个开关点名判了一遍，但「点名」这件事本身有代价：以后每加一个口径开关都要
# 回来补一句，忘了补就是安静地漏判。这条改成**按例外写**：先问「内部版这个分区的勾选框，
# 丐版少了哪几个」，答案只许是那一类东西——功能入口（按档位可藏）。新加的口径开关自动落
# 在「不许少」的那一侧，不需要有人记得回来改期望值（同 gui 里 hp3 恒定入布局那条纪律）。
_QK_MAY_HIDE = {"自动扩词"}        # 唯一按档位可藏的勾选框：它是功能入口，不是数据口径
_qk_pro = {x.text() for x in g_pro.findChildren(QCheckBox)}
_qk_lite = {x.text() for x in g_lite.findChildren(QCheckBox) if x.isVisibleTo(g_lite)}
check("丐版没丢掉任何「数据口径开关」：消失的只许是功能入口白名单里那几个",
      _qk_pro - _qk_lite <= _QK_MAY_HIDE, sorted(_qk_pro - _qk_lite))
_co_lite = _group(w_lite, "公司名单")
check("丐版公司名单区仍然有内容（按钮 + 说明 + 一个勾选框），不是一块空板",
      len(_rows(_co_lite)) >= 3
      and _co_lite.findChildren(QPushButton) and _co_lite.findChildren(QCheckBox),
      len(_rows(_co_lite)))

# ── 布局塌陷的另一种形态：不是"空一行"，是"一行被拉高、字只写一行" ──────────
# ② 与更高的 ① 并排。不加 AlignTop 时 QHBoxLayout 会把 ② 一路拉到与 ① 同高，
# 多出来的高度全被那句 wordWrap 说明文字吃掉：实测丐版下 20 像素的一行字被撑到
# 155 像素，中间一大片空白，用户说不出哪里不对、只会觉得界面坏了。
# 这条必须 show() 之后量——没上屏的窗口不跑布局，尺寸全是 0。
for _tag, _win in (("内部版", w_pro), ("丐版", w_lite)):
    _win.resize(1280, 820)
    _win.show()
    app.processEvents()
    _bad = []
    for _key in ("公司名单", "采集参数", "采集平台", "云展动力"):
        _gb = _group(_win, _key)
        if _gb is None:
            _bad.append(f"{_key}:分区不存在")
            continue
        if _gb.height() > _gb.sizeHint().height() + 1:
            _bad.append(f"{_key}:被拉伸 {_gb.height()}> {_gb.sizeHint().height()}")
        for _c in _gb.findChildren(QLabel):
            if _c.height() > _c.fontMetrics().height() * 3 + 4:
                _bad.append(f"{_key}:说明文字撑到 {_c.height()}px"
                            f"（ {_c.height()//max(1,_c.fontMetrics().height())} 行高，实际一行字）")
    check(f"{_tag}：分区高度收到自己该在的位置，说明文字没被拉成一条空板",
          not _bad, _bad)

# ── 运行日志同样不许交底 ────────────────────────────────────────
# 这一条是本轮自审真抓出来的漏网：丐版没勾掉任何功能，但自动扩词的日志教用户
# 「取消勾选『自动扩词』」——界面上根本没那个勾选框，一句话就把隐藏的功能说了出去。
# tooltip 扫不到运行日志，所以必须把流程真跑一遍、把日志收下来查。
# 注意「自动扩词」这个词本身可以出现：丐版确实在扩词（隐藏入口≠改产出口径），
# 不许出现的是**指向一个不存在的控件/入口**的那种说法。
_LOG_BANNED = ("取消勾选", "历史任务", "同步数据池", "清空历史种子库",
               "数据池", "风鸟", "爱企查")
for _tag, _ed, _win in (("丐版", "lite", w_lite), ("内部版", "pro", w_pro)):
    # 文案分支在运行时读档位，所以这里必须把档位切到这台窗口当初构造时的那一档，
    # 否则"内部版应该还在教用户取消勾选"这条会拿着丐版的解析结果去判。
    os.environ[edition.ENV_VAR] = _ed
    edition.reset_for_tests()
    _logs = []
    _win._on_log = _logs.append
    _win.kw_text.setPlainText("暖通空调与制冷设备")
    _win.keywords = ["暖通空调与制冷设备"]
    _win._kw_expanded_text = ""
    _win._auto_expand_on_start()
    if _tag == "丐版":
        check("丐版扩词日志里没有「取消勾选 XX」这种指向不存在控件的说法",
              not [x for x in _logs for k in _LOG_BANNED if k in x],
              [x for x in _logs if any(k in x for k in _LOG_BANNED)][:2])
        check("丐版自动扩词照常生效并写回关键词框（隐藏的是入口，不是把产出口径改小）",
              len(_win.keywords) > 30 and "工业冷水机" in _win.kw_text.toPlainText(),
              len(_win.keywords))
    else:
        check("内部版日志仍照旧教用户「取消勾选『自动扩词』」（同一句文案分档位，功能没删）",
              any("取消勾选「自动扩词」" in x for x in _logs), _logs[-1:])

# ── 启动校验弹窗：把藏起来的平台名字念出来，是最容易漏的一处 ──────────────
# 触发条件在丐版完全能走到：只选公司名单、不填关键词、再取消勾选企知道 →
# 「请至少勾选一个名单补采平台（…）」。清单若照内部版写死，丐版就会报出两个
# 界面上根本不存在的平台。这条只能真点一次才知道，扫 tooltip 扫不到它。
# 钉之前先记一笔原值，而且**在循环外**记：放在循环里，第二轮读到的是第一轮刚钉的
# ADMIN，收尾就变成「把 ADMIN 恢复成 ADMIN」——那一句「恢复」读起来像在做清洁，实际上
# 什么都没恢复。本套件进来就清了等级变量（见文件开头），所以今天这份原值是 None；
# 记值的写法不依赖这一点，后节才能按自己看到的现场拿环境。
_lv_env_before = os.environ.get(edition.LEVEL_ENV_VAR)
for _tag, _ed, _win in (("丐版", "lite", w_lite), ("内部版", "pro", w_pro)):
    _msgs = []

    def _info(_parent, _title, _text, *a, **k):
        _msgs.append(str(_text))
        return gui.QMessageBox.Ok

    gui.QMessageBox.information = staticmethod(_info)
    os.environ[edition.ENV_VAR] = _ed
    # 这句 reset 会重算全局等级：现场不同机器不一样，而窗口里的平台文案是建窗
    # 那一刻投影好的——两边不同步就会变成「本机绿、clone 红」（2026-11-20 实例）。
    # 这里显式钉到与建窗同一档，让这一节只问「档位会不会把藏起来的平台名念出来」。
    # 逐轮钉而不只在循环前钉一次：本轮里 `_on_start()` 走过的那一段有权碰环境，
    # 拿返回值没用（收尾统一按循环外记下的那份恢复）。
    _pin_level(edition.LEVEL_ADMIN)
    edition.reset_for_tests()
    _win.kw_text.setPlainText("")
    _win.keywords = []
    _win._co_source_file = "名单.xlsx"          # 有名单、没关键词、平台一个没勾
    for _c in list(_win.chk_list.values()):
        _c.setChecked(False)
    _win._on_start()
    _hit = [m for m in _msgs if "名单补采平台" in m]
    if _tag == "丐版":
        check("丐版「请勾选名单补采平台」只报企知道，不点名风鸟/爱企查",
              len(_hit) == 1 and "企知道" in _hit[0]
              and "风鸟" not in _hit[0] and "爱企查" not in _hit[0], _hit)
    else:
        check("内部版同一句仍报全三平台（文案是同源投影出来的，不是把内部版改坏了）",
              len(_hit) == 1
              and all(n in _hit[0] for n in ("企知道", "风鸟", "爱企查")), _hit)
# 同样的道理：这一节钉的等级不能渗到后面的节去（上面那段建窗处的注释写了为什么）。
# 走 `_unpin_level` 而不是直接 pop：这一节只该还回自己借走的那一份环境。
_unpin_level(_lv_env_before)
gui.QMessageBox.information = staticmethod(lambda *a, **k: gui.QMessageBox.Cancel)
_hb_lite = [b.text() for b in w_lite.findChildren(QPushButton)
            if b.text() in ("开始采集", "暂停", "停止", "清洗数据", "导出 Excel", "开始新任务")]
_hb_pro = [b.text() for b in w_pro.findChildren(QPushButton)
           if b.text() in ("开始采集", "暂停", "停止", "清洗数据", "导出 Excel", "开始新任务")]
check("丐版保留了全部日常操作按钮（藏的是内部入口，不是把主流程也削了）",
      _hb_lite == _hb_pro and "开始采集" in _hb_lite, (_hb_lite, _hb_pro))
_yl = _group(w_lite, "云展动力客户库")
check("丐版云展动力客户库整块保留（用户点名不隐藏：外部用户要在这里绑账号）",
      _yl is not None
      and [w_lite.bind_yzd_btn, w_lite.import_yzd_btn] == _yl.findChildren(QPushButton)
      and _yl.findChildren(QCheckBox) == [w_lite.auto_import_check],
      (_yl is not None, _yl.findChildren(QPushButton) if _yl else None))

# ── 等级投影：需求 B 的核心等式 ─────────────────────────────────
# 「等级 2 只能使用丐版的功能」这句话的正确实现是：**同一个全功能包 + 等级 2 的账号，
# 看到的界面与 lite 包完全一致**。只断言「lite 包没按钮」不够——那只证明了构建档位；
# 等级这条轴必须单独投影一次，否则「能力 = 构建 ∩ 等级」里的交集没人验过。

# 先在**矩阵层**把等式钉住：界面层相等可能只是两边都少建了同一个控件，矩阵层相等
# 才说明「等级 2 = 丐版那一组功能」这句话真的成立。互斥组归一（见 edition 的
# _EXCLUSIVE_GROUPS）就是在这里被兜住的：pro 包只有弹窗版、等级 2 只允许静默版，
# 纯交集会把采集完成的收尾两边都切掉——数据不回流比少个入口严重得多。
for _b in ("pro", "lite"):
    for _lv in (None, edition.LEVEL_ADMIN, edition.LEVEL_FULL, edition.LEVEL_LITE):
        os.environ[edition.ENV_VAR] = _b
        edition.reset_for_tests()
        edition.set_account_level(_lv)
        _cp = edition.caps_snapshot()
        _sync = [_cp[edition.CAP_POOL_PROMPT], _cp[edition.CAP_POOL_SILENT]]
        check(f"互斥组两边不全关（{_b} + 等级{_lv}：采集完成必有同步收尾）",
              sum(1 for x in _sync if x) == 1, _sync)
        check(f"互斥组也不全开（{_b} + 等级{_lv}：不许弹完窗又静默传一遍）",
              not (_cp[edition.CAP_POOL_PROMPT] and _cp[edition.CAP_POOL_SILENT]), _cp)
os.environ[edition.ENV_VAR] = "pro"
edition.reset_for_tests()
edition.set_account_level(edition.LEVEL_LITE)
_caps_pro2 = edition.caps_snapshot()
os.environ[edition.ENV_VAR] = "lite"
edition.reset_for_tests()
edition.clear_account_level()
_caps_lite = edition.caps_snapshot()
check("矩阵层等式：pro+等级2 的能力集合 == lite 包（等级 2 就是「只能用丐版功能」）",
      _caps_pro2 == _caps_lite, (_caps_pro2, _caps_lite))
edition.reset_for_tests()
os.environ[edition.ENV_VAR] = "pro"
edition.reset_for_tests()

w_lvl2 = _build("pro", edition.LEVEL_LITE)
w_lvl1 = _build("pro", edition.LEVEL_FULL)
w_lite0 = _build("lite", edition.LEVEL_ADMIN)

check("内部包 + 等级2：按钮集合与丐版包**完全相同**（交集算错了就会多出来或缺一个）",
      set(_labels(w_lvl2, QPushButton)) == set(_labels(w_lite, QPushButton))
      and set(_labels(w_lvl2, QCheckBox)) == set(_labels(w_lite, QCheckBox)),
      (sorted(set(_labels(w_lvl2, QPushButton)) - set(_labels(w_lite, QPushButton))),
       sorted(set(_labels(w_lite, QPushButton)) - set(_labels(w_lvl2, QPushButton)))))
check("内部包 + 等级2：数据池/种子库/历史任务/扩词/权限管理入口一个都没建出来",
      w_lvl2.btn_pool_sync is None and w_lvl2.btn_pool_search is None
      and w_lvl2.btn_pool_import is None and w_lvl2.btn_pool_scan is None
      and w_lvl2.btn_clear_seen is None and w_lvl2.btn_history is None
      and w_lvl2.chk_auto_kw is None and w_lvl2.btn_acl_admin is None
      and w_lvl2.acl_status_lbl is None)
check("内部包 + 等级2：标题带「精简版」（档位级文案跟着生效档位走，不是只跟着包走）",
      "精简版" in _title_parts(w_lvl2) and "精简版" not in _title_parts(w_lvl1),
      (w_lvl2.windowTitle(), w_lvl1.windowTitle()))
_hits_lvl2 = _tip_hits(w_lvl2, BANNED_TIPS)
check("内部包 + 等级2：整窗 tooltip 也不交底（受限账号看到的就是丐版那一套说法）",
      not _hits_lvl2, _hits_lvl2)
check("内部包 + 等级2：名单补采运行时判据同样只剩企知道（勾着却没跑的那种不一致不许有）",
      set(w_lvl2.chk_list) == {QZD_SLUG} and w_lvl2._list_want_slugs() == [QZD_SLUG],
      (set(w_lvl2.chk_list), w_lvl2._list_want_slugs()))
check("内部包 + 等级1：功能全在，但没有「权限管理」入口与状态徽章（管理权只给等级 0）",
      w_lvl1.btn_pool_sync is not None and w_lvl1.btn_clear_seen is not None
      and w_lvl1.btn_history is not None and len(w_lvl1.chk_list) == len(LIST_CHANNEL_SLUGS)
      and w_lvl1.btn_acl_admin is None and w_lvl1.acl_status_lbl is None,
      _labels(w_lvl1, QPushButton)[:6])
# 「该有的人没有」验了三遍，「该有的人有」一次都没验过——管理员点开不了面板这种
# 事故（入口没建出来 / 接错槽 / 被能力表藏起来）在上面那几条下依然全绿。
w_adm = _build("pro", edition.LEVEL_ADMIN)
_adm_btns = _labels(w_adm, QPushButton)
check("内部包 + 等级0：「权限管理」入口与状态徽章确实被建出来（管理员进得去面板）",
      w_adm.btn_acl_admin is not None and w_adm.acl_status_lbl is not None
      and "权限管理" in _adm_btns, _adm_btns[:8])
check("权限管理入口点了确实接上开面板的槽（不是个贴上去的死按钮）",
      w_adm.btn_acl_admin.receivers(w_adm.btn_acl_admin.clicked) > 0
      and w_adm.btn_acl_admin.text() == "权限管理")
check("等级0 的入口当前可见（建出来却被能力表藏起来=管理员看不到，等于没做）",
      w_adm.btn_acl_admin.isVisibleTo(w_adm) and w_adm.acl_status_lbl.isVisibleTo(w_adm))

check("丐版包 + 等级0：仍然是丐版界面（等级不能替包长出它没编译进来的入口）",
      w_lite0.btn_pool_sync is None and w_lite0.btn_acl_admin is None
      and w_lite0.btn_clear_seen is None
      and "精简版" in _title_parts(w_lite0),
      _labels(w_lite0, QPushButton)[:6])
os.environ[edition.ENV_VAR] = "pro"
os.environ[edition.LEVEL_ENV_VAR] = str(edition.LEVEL_ADMIN)
edition.reset_for_tests()
_caps_lvl0 = edition.caps_snapshot()
os.environ.pop(edition.LEVEL_ENV_VAR, None)
edition.reset_for_tests()
check("pro 包里「等级 0」与「等级未判定」的能力集合相同：未判定不许被当成受限"
      "（这段时间能不能用由 check() 的 fail-closed 管，不该由界面再判一遍）",
      edition.caps_snapshot() == _caps_lvl0,
      sorted(c for c, v in _caps_lvl0.items() if v != edition.has(c)))
edition.reset_for_tests()

# ── 磁盘痕迹：界面上藏住了、桌面上却长出个叫「数据池缓存」的文件夹，等于白藏 ──
_desktop_pool = str(Path.home() / "Desktop" / "数据池缓存")
os.environ[edition.ENV_VAR] = "pro"
edition.reset_for_tests()
_p_pro = gui.DataPoolManager._default_cache_dir()
os.environ[edition.ENV_VAR] = "lite"
edition.reset_for_tests()
_p_lite = gui.DataPoolManager._default_cache_dir()
check("内部版缓存目录一个字节都没动（同事的排查脚本和既有缓存不能因为这次改造换地方）",
      _p_pro == _desktop_pool, _p_pro)
check("丐版缓存/台账/留痕全都不在桌面上（静默同步会在这条路径下落 silent_sync.log）",
      "Desktop" not in _p_lite and "数据池缓存" not in _p_lite
      and Path(_p_lite).name == "pool", _p_lite)

# ── 窄窗裁切：入口「建出来了」不等于「够得着」──────────────────────────────
# 配置区的横向滚动条是关掉的，窗口比内容最小宽还窄时 Qt 的行为是**直接裁掉视口外那部分**，
# 不是自动换行。于是出现一类最难查的缺陷：控件确实建出来了、isVisible 也是 True、
# 自测里「有没有这个按钮」全绿，用户却看不见也点不到——因为他把窗口拉窄了。
# 等级 0 的「权限管理」正好排在行尾，是最先被裁掉的那一个。
def _clipped_at_min(win):
    """把窗口拉到它自己的最小宽，列出配置区里被裁到视口/窗口之外的可见控件。"""
    sa = win._cfg_scroll
    if sa.horizontalScrollBarPolicy() != Qt.ScrollBarAlwaysOff:
        return []            # 横向能滚就不算裁切：行尾按钮拖一下够得着（那是校准的退路）
    # 本用例只验「最小宽抬到内容所需」这一段；offscreen 的可用屏宽只有几百像素，
    # 留着它会让每条都走「装不下→放开横向滚动」的退路，量的就不是同一件事了。
    # 屏幕宽度那条退路由下面单独一条用例显式验。
    win._width_floor_cap = lambda: 4096
    win.show()
    app.processEvents()
    win.resize(win.minimumWidth(), 700)
    app.processEvents()
    app.processEvents()
    vp = sa.viewport()
    out = []
    for ch in sa.widget().findChildren(QWidget):
        if ch.parentWidget() is sa.widget():
            continue                       # 只查真正的入口（分组框本身不算）
        txt = (getattr(ch, "text", lambda: "")() or "").strip()
        if not txt or not ch.isVisibleTo(win):
            continue
        tl = ch.mapTo(vp, QPoint(0, 0))
        tw = ch.mapTo(win, QPoint(0, 0))
        over = max(tl.x() + ch.width() - vp.width(),      # 视口外：没有横向滚动条可拖
                   tw.x() + ch.width() - win.width(),     # 窗口外：滚动容器整个溢出
                   -tw.x())                               # 左边界外
        if over > 0:
            out.append((txt[:14], over))
    return out


print("\n[B2. 窗口拉到最小宽时不许裁掉任何入口]")
for _ed, _lv, _tag in (("pro", edition.LEVEL_ADMIN, "内部包+等级0"),
                       ("pro", edition.LEVEL_FULL, "内部包+等级1"),
                       ("pro", edition.LEVEL_LITE, "内部包+等级2"),
                       ("lite", edition.LEVEL_ADMIN, "丐版包+等级0"),
                       ("lite", edition.LEVEL_LITE, "丐版包+等级2")):
    _w = _build(_ed, _lv)
    _cut = _clipped_at_min(_w)
    check(f"{_tag}：最小宽下配置区没有入口被裁掉（看不见也点不到=等于没做）",
          not _cut, _cut[:3])
    _w.close()
# 判据自证：把校准顶成空操作，这条量裁切的尺子必须当场变红。否则它可能是永远绿的
# 假闸口（比如哪天 _cfg_scroll 改了名、循环里悄悄一个控件都没查到）。
_w0 = _build("pro", edition.LEVEL_ADMIN)
_w0._apply_width_floor = lambda: None
_cut0 = _clipped_at_min(_w0)
check("上述判据有效：关掉窗口最小宽校准后它确实报出裁切（不是永远绿）",
      bool(_cut0) and any("权限管理" in t or "搜索采购商" in t for t, _ in _cut0), _cut0[:4])
_w0.close()
# 校准的天花板是可用屏宽：屏幕本身装不下整行时，硬把窗口最小宽抬到 1128 只会让窗口
# 伸出屏幕——右侧照样看不见，只是换了个更难查的原因。这条退路必须显式验一遍。
_w1 = _build("pro", edition.LEVEL_ADMIN)
_w1._width_floor_cap = lambda: 900            # 假装屏幕只有 900 宽
_w1.resize(880, 700)
_w1.show()
app.processEvents()
app.processEvents()
_msrc = inspect.getsource(gui.main)
check("退出收尾按「窗口 → 守卫 → QApplication」显式排序（交给解释器 GC 顺序就是线上"
      "那次关工具 SIGSEGV：模块级守卫单例比 app 活得久）",
      "app.exec_()" in _msrc and "finally" in _msrc
      and "_access_guard.shutdown(app)" in _msrc
      and _msrc.index("finally") < _msrc.index("_access_guard.shutdown(app)")
      and "sys.exit(app.exec_())" not in _msrc, _msrc[-300:])
# 需求 6：开机顺序按 AST 判，不按字面串。
# 为什么不能继续用「prefetch_authoritative() 是否作为子串出现」：这条门的本意是
# 「建窗口**之前**必须做过权威定档」，可字面串把调用的**写法**（带不带参数、换不换行）
# 一并钉住了——本轮只是给它传了预算参数，门就红了，报的是一个根本不存在的回归。
# 同一个原因也适用于「等待必须在建窗前」这类**顺序**判据：只有行号能说清谁先谁后。


def _calls_in(_tree, *names):
    """一棵 AST 里这些名字的**调用**行号（``obj.name(...)`` 与 ``Name(...)`` 两种都认）。

    只取调用节点，不取同名引用，也不拿源码做子串匹配：后者会把注释/docstring 里的
    字样也算进去（本轮就被抓过一次：函数文档串里写着「用 QEventLoop 而不是 time.sleep」，
    于是一条「不许 sleep」的门被自己的注释顶红了）。找不到就返回空列表，由调用方判空。
    """
    _out = []
    for _n in ast.walk(_tree):
        if not isinstance(_n, ast.Call):
            continue
        _f = _n.func
        _nm = _f.attr if isinstance(_f, ast.Attribute) else getattr(_f, "id", "")
        if _nm in names:
            _out.append(_n.lineno)
    return _out


def _tree_of(_obj):
    """取函数/方法的 AST。**必须 dedent**。

    ``inspect.getsource`` 对类里的方法会连同它在源文件里的缩进一起返回
    （``    def spin(...)``），直接喂给 ``ast.parse`` 就是 IndentationError。
    本轮先前只把它用在顶层函数上没暴露，接到 ``_AuthGateNotice.spin`` 才炸出来；
    按仓库既有纪律，这类“判据自己错了”的坑要当场补成结构上的正确，不能只改调用方。
    """
    return ast.parse(textwrap.dedent(inspect.getsource(_obj)))


_mtree = _tree_of(gui.main)


def _mcall_lines(*names):
    return _calls_in(_mtree, *names)


_pref = _mcall_lines("prefetch_authoritative")
_warm = _mcall_lines("prefetch_warm")
_gate = _mcall_lines("_wait_auth_settled")
_qapp = _mcall_lines("QApplication")
# 变量名不许占 `_build`：那是上面造窗口函数的名字（本节曾经把它覆盖成行号列表，
# 于是本节之后任何一处再调 `_build(...)` 都会拿到 list 报 not callable）。
_build_lines = _mcall_lines("MainWindow")
check("开机定档走权威判定（按 AST 数调用点，不挑写法）：prefetch_authoritative 被调、"
      "prefetch_local 一次都没有（副本可能是被删除/降级前的旧等级 = 被删的号还能开机摆全套入口）",
      bool(_pref) and not _mcall_lines("prefetch_local"),
      (_pref, _mcall_lines("prefetch_local")))
check("需求 6·顺序：定档发生在建主窗口**之前**（入口是按等级创建的，建晚了就只能靠重启补）",
      bool(_pref) and bool(_build_lines) and max(_pref) < min(_build_lines), (_pref, _build_lines))
check("需求 6·顺序：过场等待也必须在建主窗口之前——等着等着却先把窗口按丐版建出来，"
      "就回到了这次报障的原点（只能重启）",
      bool(_gate) and bool(_build_lines) and max(_gate) < min(_build_lines), (_gate, _build_lines))
check("需求 6·预算：预热排在 QApplication 构造之前（把网络往返与本地启动重叠，不花用户一秒）",
      bool(_warm) and bool(_qapp) and min(_warm) < min(_qapp), (_warm, _qapp))
check("需求 6·不阻塞：过场等待靠真事件循环而不是 sleep（sleep 会把这段时间变成"
      "「界面已经出现却按不动」，那两个按钮也就白做了）",
      bool(_calls_in(_tree_of(gui._AuthGateNotice.spin), "QEventLoop", "exec_"))
      and not _calls_in(_tree_of(gui._AuthGateNotice.spin), "sleep")
      and not _calls_in(_tree_of(gui._wait_auth_settled), "sleep"), "")
# 过场窗口真造一次：它是真 QWidget，只看源码验不出「那一退到底存不存在」。
_gate_probe = gui._AuthGateNotice("测试原因：本次开机没取回名单")
_gate_btns = [b.text() for b in _gate_probe.findChildren(QPushButton)]
check("需求 6·可走：过场窗口给了「先用基础功能」那一退（网络真不通时绝不能把人锁在门口）",
      "先用基础功能" in _gate_btns, _gate_btns)
check("需求 6·可按：同样给了「立即重试」，且等待期间时间在走（有秒数提示，不是白屏）",
      "立即重试" in _gate_btns and "已等" in _gate_probe.elapsed_lbl.text(), _gate_btns)
check("需求 6·说得出原因：定档原因原文逐字搬到过场窗口上，界面不自己编一套故障话术",
      _gate_probe.detail.text() == "测试原因：本次开机没取回名单", _gate_probe.detail.text())
_gate_probe.close()
app.processEvents()
check("屏幕窄到装不下整行时不硬撑窗口，而是把横向滚动还回来（宁可要滚动条，"
      "也不要看不见又点不到的按钮）",
      _w1.minimumWidth() <= 900
      and _w1._cfg_scroll.horizontalScrollBarPolicy() != Qt.ScrollBarAlwaysOff,
      (_w1.minimumWidth(), _w1._cfg_scroll.horizontalScrollBarPolicy()))
_w1.close()
os.environ.pop(edition.ENV_VAR, None)
os.environ.pop(edition.LEVEL_ENV_VAR, None)
edition.reset_for_tests()

# ══════════════════════════════════════════════════════════ C. 静默同步的诚实性
print("\n[C. 采集完成后的后台静默同步]")
os.environ[edition.ENV_VAR] = "lite"
edition.reset_for_tests()
_cache = Path(TMP) / "pool_cache"
_cache.mkdir(parents=True, exist_ok=True)
gui.DataPoolManager._default_cache_dir = staticmethod(lambda: str(_cache))

started = []


class _Sig:
    def __init__(self):
        self.slots = []

    def connect(self, fn, _type=None):
        self.slots.append(fn)

    def emit(self, *a):
        for fn in list(self.slots):
            fn(*a)


class _FakeWorker:
    """假上传线程：只记账「提交了什么」，结果由测试手动 emit（绝不为测试碰网络）。"""

    def __init__(self, records, username=""):
        self.records = list(records)
        self.finished = _Sig()
        self.progress = _Sig()
        self.running = True
        started.append(self)

    def start(self):
        pass

    def isRunning(self):
        return self.running

    def deleteLater(self):
        pass

    def wait(self, ms=0):
        self.running = False
        return True

    def terminate(self):
        self.running = False


gui.SyncWorker = _FakeWorker


def _rec(name, mobiles=(), emails=(), site=""):
    return {"company": name, "company_norm": name, "mobiles": list(mobiles),
            "landlines": [], "emails": list(emails), "wechats": [],
            "website": site, "products": "空调", "address": "广东广州"}


w = w_lite
log_calls = []
w._on_log = lambda m: log_calls.append(m)        # 静默路径要是敢写界面日志，这里就炸
for _m in ("question", "information", "warning", "critical"):
    setattr(gui.QMessageBox, _m,
            staticmethod(lambda *a, **k: (_ for _ in ()).throw(AssertionError(
                "静默同步不许弹窗"))))

w._pool_records = lambda: ([_rec("甲公司", ["13800001111"]),
                            _rec("乙公司", emails=["b@e.com"]),
                            _rec("丙公司", site="c.com")], None)
w._silent_pool_sync()
check("采集完成后真的提交了上传（不点任何按钮也会有数据进池）",
      len(started) == 1 and len(started[0].records) == 3, len(started))
check("静默同步全程不出声：没弹一个窗、没往界面日志写一个字", not log_calls, log_calls)
started[0].finished.emit(True, "成功上传 3 条记录到数据池\n文件名: upload_x.json")
_ledger = _cache / "silent_sync_ledger.json"
check("上传成功后台账落盘，记的就是这批内容的指纹（不是文件名、不是条数）",
      _ledger.is_file() and len(json.loads(_ledger.read_text(encoding="utf-8"))["keys"]) == 3,
      _ledger.read_text(encoding="utf-8")[:120] if _ledger.is_file() else "无文件")
_logf = _cache / "silent_sync.log"
check("留痕写在磁盘上（界面不出声≠什么都没发生过，否则外部用户报「我的数据没进池」只能猜）",
      _logf.is_file() and "开始上传 3 条" in _logf.read_text(encoding="utf-8")
      and "成功上传 3 条" in _logf.read_text(encoding="utf-8"))

w._silent_pool_sync()
check("同一批内容第二次不会重传（上传层 append-only 不去重，没台账就是每晚白灌一遍存储）",
      len(started) == 1, len(started))
check("「跳过」的原因也写进留痕，而不是静默到连查都查不到",
      "与以前传过的完全相同" in _logf.read_text(encoding="utf-8"))

w._pool_records = lambda: ([_rec("甲公司", ["13800001111"]),
                            _rec("甲公司", ["13800001111", "13900002222"]),
                            _rec("丁公司", ["13711112222"])], None)
w._silent_pool_sync()
check("同一家公司补到新号码必须重传（只按公司名去重会让池子永远停在最早那份残缺联系方式）",
      len(started) == 2 and len(started[1].records) == 2, len(started))
started[1].finished.emit(True, "成功上传 2 条记录到数据池\n文件名: upload_y.json")
check("指纹排序去重：号码顺序变了不算新记录；同批里同行同号的重复只传一次",
      len(json.loads(_ledger.read_text(encoding="utf-8"))["keys"]) == 5,
      json.loads(_ledger.read_text(encoding="utf-8"))["count"])
n_before = len(started)
w._pool_records = lambda: ([_rec("甲公司", ["13900002222", "13800001111"])], None)
w._silent_pool_sync()
check("同一批号码换个顺序写出来，仍认成传过的内容（不该为此发一次网络请求）",
      len(started) == n_before, len(started))

w._pool_records = lambda: ([_rec("戊公司", ["13500001111"])], None)
n_before = len(started)
w._silent_pool_sync()
started[-1].finished.emit(False, "上传失败: 网络断了")
_k = json.loads(_ledger.read_text(encoding="utf-8"))["keys"]
check("上传失败绝不写台账（写了就等于对这批数据永久谎报「传过了」，从此不再重试）",
      len(_k) == 5, len(_k))
w._silent_pool_sync()
check("失败的批次下次采集完成会自动重试，不需要任何人动手",
      len(started) == n_before + 2      # n_before 之后：一次失败的提交 + 一次重试
      and started[-1].records[0]["company"] == "戊公司", len(started))
started[-1].finished.emit(True, "成功上传 1 条记录到数据池\n文件名: upload_z.json")
check("重试成功后这批才进台账",
      len(json.loads(_ledger.read_text(encoding="utf-8"))["keys"]) == 6)

# ── 几个必须站得住的边角 ────────────────────────────────────────
w._pool_records = lambda: ([_rec("", ["13600007777"])], None)      # 归一化为空
n_before = len(started)
w._silent_pool_sync()
check("公司名为空的记录照样传（宁多传不瞒报），但不写指纹——否则空串指纹会把所有"
      "无名记录互相认成同一份", len(started) == n_before + 1)
started[-1].finished.emit(True, "成功上传 1 条记录到数据池\n文件名: upload_w.json")
check("空公司名这条没进台账（上面那条判据的后半段）",
      len(json.loads(_ledger.read_text(encoding="utf-8"))["keys"]) == 6)

w._pool_records = lambda: ([_rec("壬公司", ["13222223333"]),
                            _rec("壬公司", ["13222223333"]),
                            _rec("癸公司", ["13333334444"])], None)
n_before, keys_before = len(started), 6
w._silent_pool_sync()
check("同一批里完全相同的两条只传一次（一家公司本次出现两遍不该占两个上传位）",
      len(started) == n_before + 1 and len(started[-1].records) == 2,
      len(started[-1].records) if len(started) > n_before else "没提交")
started[-1].finished.emit(True, "ok\n文件名: upload_v.json")
check("批内去重后台账按去重后的结果记账（下次这两家都算传过）",
      len(json.loads(_ledger.read_text(encoding="utf-8"))["keys"]) == keys_before + 2)

w._silent_worker = _FakeWorker([])          # 假装上一批还在路上
w._pool_records = lambda: ([_rec("辛公司", ["13111112222"])], None)
w._silent_pool_sync()
check("上一批没跑完时不并发第二条上传（只记一个待办，避免两个线程同写一份台账）",
      w._silent_pending is True and len(started) == n_before + 2, len(started))
w._silent_worker = None
w._silent_pending = False
w._pool_records = lambda: ([], "no_rows")
n_before = len(started)
w._silent_pool_sync()
check("没有数据时什么都不做，并在留痕里写清原因",
      len(started) == n_before
      and "本次没有采集结果" in _logf.read_text(encoding="utf-8"))

_src_silent = inspect.getsource(gui.MainWindow._silent_pool_sync)
_src_sync = inspect.getsource(gui.MainWindow._on_pool_sync)
_src_close = inspect.getsource(gui.MainWindow.closeEvent)
check("手动同步与静默同步共用同一个记录漏斗（各写一份转换，迟早一边漏字段）",
      "_pool_records()" in _src_silent and "_pool_records()" in _src_sync)
check("静默路径复用同一个上传线程类（清洗/门禁/分片/原子写不许绕过）",
      "SyncWorker(" in _src_silent)
check("退出路径只在真拿到结果时才落账（把「不确定」写成「已传过」就是永久丢数据）",
      "if self._silent_result is not None" in _src_close
      and "本批不记台账" in _src_close)
check("静默同步不写界面日志、不弹窗（这条由上面的运行时断言兜住，这里再钉一次代码）",
      "_on_log" not in _src_silent and "QMessageBox" not in _src_silent)

# ══════════════════════════════════════════════════════════ D. 判据不许散写
print("\n[D. 档位判据只有一份]")
_gsrc = Path(gui.__file__).read_text(encoding="utf-8")
check("GUI 里没有任何一处自己比较档位字符串（全都问 edition 的能力位）",
      not re.search(r'==\s*["\'](lite|pro)["\']', _gsrc)
      and not re.search(r'edition\(\)\s*==', _gsrc),
      re.findall(r'.*(?:==\s*["\'](?:lite|pro)["\']|edition\(\)\s*==).*', _gsrc)[:3])
_calls = sorted(set(re.findall(r"edition\.([a-z_]+)\(", _gsrc)))
# 真实调用点另算一份：上面那个正则拿的是「文本里提到过哪些名字」，docstring 与注释里的
# 同一个字串会被算进去（本轮 _build_pool_group 的文档串里提了一句 pool_ui_visible 就被
# 判成 6 处调用）。交付链在 --hidden-import 那条门上已经踩过同一个坑并写下「注释必须
# 先剔掉再匹配」，这一族反模式不留在第二处。名字集合仍用文本拿（宽一点安全：多一个名字
# 只会多要求一次白名单登记），个数一律以 AST 为准。
import ast as _ast_mod
_gtree = _ast_mod.parse(_gsrc)


def _callsites(_fname):
    """数「界面上真调了一次 edition._fname()」的次数（只看 Call 节点）。"""
    return sum(1 for _n in _ast_mod.walk(_gtree)
               if isinstance(_n, _ast_mod.Call) and isinstance(_n.func, _ast_mod.Attribute)
               and _n.func.attr == _fname
               and isinstance(_n.func.value, _ast_mod.Name) and _n.func.value.id == "edition")
_ALLOWED_CALLS = {"title_suffix", "pool_ui_visible", "silent_sync_enabled",
                  "prompt_sync_enabled", "visible_list_channels", "auto_expand_visible",
                  "auto_expand_default", "clear_seen_visible", "history_visible",
                  "pool_cache_dir", "describe", "resolve",
                  # 权限等级那一轴只许问这三样：入口有没有（投影函数）、能力位快照
                  # （收口用）、等级文案（管理员徽章用）。**等级数字本身不许进界面**，
                  # 否则界面就开始自己比大小，权限模型又长出第二份。
                  "acl_admin_visible", "caps_snapshot", "level_label",
                  # 需求 6（2026-11-19）：「没问到身份」与「问到是丐版」必须在界面上分开说，
                  # 否则高权限号在新机器首启就只能看到一个与无权账号长得一样的界面。这两个
                  # 都是投影函数（只读、只说原因），能力判定仍然全在 edition 那一份矩阵里。
                  "auth_pending", "auth_reason"}
check("GUI 只能问投影函数：界面里一旦出现 is_lite()/has() 这种原始档位判定，"
      "能力矩阵就管不住它（等价于把判据又写了一份）",
      set(_calls) <= _ALLOWED_CALLS, _calls)
check("GUI 用到的都是 edition 的公开投影函数（没有 private 属性乱翻）",
      all(not c.startswith("_") for c in _calls), _calls)
check("GUI 不读等级数字，也不读能力矩阵（account_level/has/effective_level 都不许出现在界面）",
      not re.search(r"edition\.(account_level|has|effective_level|level_matrix|matrix)\(",
                    _gsrc),
      re.findall(r".*edition\.(?:account_level|has|effective_level|level_matrix|matrix)\(.*",
                 _gsrc)[:3])
check("隐藏点的数量与能力位对得上：每个入口都真在被禁时才不创建（按 AST 数真调用点，"
      "docstring 里提一句不算调用——上一版拿子串数，本轮就在这一处假报了一条）",
      # 数据池五处判点：四个动作各一道 _acl_gate + 就地生长入口自己那一道权限前置条件
      # （「只在当场确认到高权限时才建」是这一组入口存在的唯一条件：已确定为丐版的账号
      #  永远走不到建它那一行，界面树里一个相关字节也不会多）
      _callsites("pool_ui_visible") == 5
      # 清空种子库四处判点：④ 的按钮、复位对话框的勾选项、复位按钮 tooltip 末句、
      # 以及清空动作本身（入口比判定先建好时，只有动作层拦得住）
      and _callsites("clear_seen_visible") == 4
      # 历史任务两处：按钮建不建 + 「接着采」这条动作
      and _callsites("history_visible") == 2
      # 扩词两处：④ 的勾选框建不建 + 日志里那句「怎么关掉换词」的说法
      and _callsites("auto_expand_visible") == 2
      # 名单通道四处：② 的控件集合 + 日志看板的状态行 + 开始采集时的运行时重投影
      # + 缺路判据（「该给却没给」的那几路必须也走同一份投影，否则丐版会在日志里
      #   点名内部平台，等于把藏起来的功能念出来）
      and _callsites("visible_list_channels") == 4
      # 权限管理入口两处：徽章/按钮建不建 + 打开面板前的复核
      and _callsites("acl_admin_visible") == 2,
      (_callsites("pool_ui_visible"), _callsites("clear_seen_visible"),
       _callsites("history_visible"), _callsites("visible_list_channels"),
       _callsites("acl_admin_visible")))
_gates = re.findall(r"self\._acl_gate\(edition\.([a-z_]+)\(\)", _gsrc)
check("每个「建得比等级判定早」的入口都在动作层再问一次（只藏控件不拦动作=一半都没做）",
      sorted(_gates) == sorted(["pool_ui_visible"] * 4 + ["clear_seen_visible",
                                                          "history_visible"]),
      _gates)
check("能力位→控件的登记表只有一份，收口靠它而不是靠逐个 handler 里手写 if",
      hasattr(gui.MainWindow, "_acl_reg") and hasattr(gui.MainWindow, "_acl_sync_level")
      and "_acl_gated.setdefault(cap, []).append(w)" in _gsrc)
check("被顶号的收尾走既有停止链路（数据与进度照常落盘），不是硬杀进程",
      "_on_acl_revoked" in _gsrc and "stop_crawl" in
      inspect.getsource(gui.MainWindow._on_acl_revoked)
      and "QApplication.quit" not in inspect.getsource(gui.MainWindow._on_acl_revoked))
check("权限心跳的间隔与 access_guard 的缓存 TTL 同源（心跳不该多出一次网络请求）",
      "_access_guard.CACHE_TTL_SECONDS" in _gsrc)
check("顶号唤醒用 QueuedConnection 连接：直连等于把模态框嵌进点击调用栈（eventFilter 里套事件循环）",
      "refreshed.connect" in _gsrc and "Qt.QueuedConnection" in _gsrc,
      re.findall(r".*refreshed\.connect.*", _gsrc)[:2])
check("退出时摘掉 refreshed 连接：守卫单例比窗口活得久，留着就是往已销毁的对象排队",
      "refreshed.disconnect" in inspect.getsource(gui.MainWindow.closeEvent))
check("心跳与唤醒信号共用同一条处理体（两份各自实现迟早分叉，只弹一次窗靠的是这一份）",
      "_on_acl_event(" in inspect.getsource(gui.MainWindow._acl_heartbeat)
      and "_on_acl_event(" in inspect.getsource(gui.MainWindow._on_acl_refreshed))
check("等级变更后标题按生效档位重投影（入口与文案出自同一份投影，不能只在构造时定一次）",
      "title_suffix" in inspect.getsource(gui.MainWindow._acl_sync_level))
_adm = (Path(gui.__file__).parent / "acl_admin_dialog.py").read_text(encoding="utf-8")
check("权限面板不自己碰 OSS：读写全部经 AccessGuard（否则名单格式与 version 规则会长成两份）",
      not re.search(r"\b(upload_json|download_json|upload_json_atomic|OSSPoolClient|"
                    r"OSSPoolError)\b", _adm),
      re.findall(r".*(?:upload_json|download_json|OSSPoolClient|OSSPoolError).*", _adm)[:3])
check("权限面板也不自己算等级：只看 edition 的投影与文案函数",
      not re.search(r"\b(resolve_level|_LEVEL_CAPS|_parse_grants|grants)\b", _adm),
      re.findall(r".*(?:resolve_level|_LEVEL_CAPS|_parse_grants|grants).*", _adm)[:3])
check("面板每个写动作都先过 _admin_gate（打开着的几分钟里等级可能已经被别人改掉）",
      _adm.count("self._admin_gate()") >= 4, _adm.count("self._admin_gate()"))
check("读不到名单时面板不许提交（空快照写回=把整份等级表抹成空）",
      "self._write_enabled()" in _adm and "self._loaded = False" in _adm)
check("四个数据池按钮的槽函数与对话框都还在代码里（丐版只是不画入口）",
      all(hasattr(gui.MainWindow, m) for m in
          ("_on_pool_sync", "_on_pool_search", "_on_pool_import", "_on_pool_scan",
           "_prompt_pool_sync"))
      and hasattr(gui, "SyncDialog") and hasattr(gui, "LocalScanDialog"))
check("被隐藏的功能实现仍在：清空种子库与历史任务的槽函数没被顺手删",
      hasattr(gui.MainWindow, "_on_clear_seen_store")
      and hasattr(gui.MainWindow, "_on_history_tasks")
      and hasattr(gui.MainWindow, "_auto_expand_on_start"))
check("风鸟/爱企查的通道注册与三平台台账代码原样保留（回归由 test_list_channels 兜）",
      "LIST_CHANNEL_SLUGS" in Path(gui.__file__).read_text(encoding="utf-8"))

# ── 打包清单三方一致性：门禁能不能进包，取决于这三份文件有没有一起改
#    两个 workflow（Win/Mac）+ 本机打包脚本。漏任何一份，打出去的都是「安静地少一层
#    防护」的包：不会崩，不会报错，外面拿到的就是没有等级、没有单点登录的版本。
# 第三份以前钉的是 b2bcollect_build/B2bCollectTool.spec——那是 **PyInstaller 每次构建
# 自动生成的回显**（.gitignore 里 *.spec 就是为此），既不参与出包也不是人工维护的清单。
# 后果本轮就撞上了：功能④⑤的四个新模块早就进了本机脚本与两份 workflow，这一条照样红，
# 红的原因是「这台机器最近一次本地打包在那之前」。判据的输入取决于打过没打过包，得到的
# 就不是清单对拍而是考古（同一条结论在 test_frozen_delivery_offline.py 的交付链一节已写在案）。
_ROOT = Path(gui.__file__).resolve().parent.parent
_MANIFESTS = {
    "Win workflow": _ROOT / ".github" / "workflows" / "build-b2bcollect.yml",
    "Mac workflow": _ROOT / ".github" / "workflows" / "build-b2bcollect-mac.yml",
    "本机打包脚本": _ROOT / "debug" / "build_b2bcollect_mac_local.sh",
}
_FOUND = {k: v for k, v in _MANIFESTS.items() if v.is_file()}
if len(_FOUND) == len(_MANIFESTS):
    def _mods(text):
        got = re.findall(r"--hidden-import ([A-Za-z_][\w.]*)", text)
        if not got:
            m = re.search(r"^hiddenimports = \[(.*?)\]", text, re.M | re.S)
            got = re.findall(r"'([^']+)'", m.group(1)) if m else []
        return got
    _mf = {k: _mods(p.read_text(encoding="utf-8")) for k, p in _FOUND.items()}
    _TRIO = ["access_guard", "edition", "acl_admin_dialog"]
    check(f"三份打包清单都收了权限三件套 {_TRIO}",
          all(all(t in v for t in _TRIO) for v in _mf.values()),
          {k: [t for t in _TRIO if t not in v] for k, v in _mf.items()
           if any(t not in v for t in _TRIO)})
    check("Win/Mac 两个 workflow 的 --hidden-import 逐条一致（新增本地模块只改一边，"
          "就是「Windows 能用、Mac 打不开」这种最难复现的包）",
          _mf["Win workflow"] == _mf["Mac workflow"],
          [x for x in _mf["Win workflow"] if x not in _mf["Mac workflow"]]
          + [x for x in _mf["Mac workflow"] if x not in _mf["Win workflow"]])
    check("本机打包脚本覆盖 workflow 的全部模块（脚本里多出来的按架构项，允许）",
          set(_mf["Win workflow"]) <= set(_mf["本机打包脚本"]),
          [x for x in _mf["Win workflow"] if x not in set(_mf["本机打包脚本"])])
    # 本地模块只要存在就必须被收进包——这条把「今天新写一个 py、忘了登记」变成当场红
    _locals = {p.stem for p in (_ROOT / "b2bcollect_build").glob("*.py")
               if not p.stem.startswith(("test_", "b2bcollect_gui"))}
    _skip_local = {"gys_crawler", "solarbe_crawler", "unlock_no_contact"}   # 已下线/独立脚本
    _miss = sorted(x for x in _locals - _skip_local
                   if x not in _mf["Win workflow"] or x not in _mf["本机打包脚本"])
    check("b2bcollect_build 下每个在用本地模块都在三份清单里（漏登记当场红，不等打包那天）",
          not _miss, _miss)
else:
    print(f"  ⏭ 打包清单比对跳过：缺 {sorted(set(_MANIFESTS) - set(_FOUND))}")

# ── 引用了标准库模块却没 import：这类缺陷 py_compile 全绿、模块导入也全绿，只在跑到
#    那一行时当场 NameError；打包出去更要等用户点到才炸（2026-11-11 access_guard 里
#    新写的 os.environ 就是躲过了编译与全部既有断言，直到真去算设备指纹才炸）。
_STD = set("""os sys json re time uuid hashlib platform subprocess threading argparse
tempfile shutil traceback io base64 pathlib collections functools itertools random math
string socket sqlite3 csv pickle copy glob logging queue secrets textwrap contextlib enum
warnings zipfile urllib difflib unicodedata statistics heapq bisect inspect signal select
struct shlex""".split())


def _fn_locals(fn):
    """函数内绑定过的名字：局部变量 warnings = [] 不能被当成「用了 warnings 模块」。"""
    out = set()
    a = getattr(fn, "args", None)
    if a is not None:
        for arg in list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs):
            out.add(arg.arg)
        for nm in (a.vararg, a.kwarg):
            if nm:
                out.add(nm.arg)
    for n in ast.walk(fn):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            out.add(n.id)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            out.add(n.name)
    return out


def _assign_names(node):
    ts = ([node.target] if isinstance(node, (ast.AnnAssign, ast.AugAssign))
          else node.targets if isinstance(node, ast.Assign) else [])
    return {t.id for t in ts if isinstance(t, ast.Name)}


def _missing_imports(src):
    tree = ast.parse(src)
    imported = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for al in n.names:
                imported.add((al.asname or al.name).split(".")[0])
        elif isinstance(n, ast.ImportFrom):
            for al in n.names:
                imported.add(al.asname or al.name)
    env = {n.name for n in tree.body
           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    for n in tree.body:
        env |= _assign_names(n)
    bad = []

    def visit(node, scope):
        for c in ast.iter_child_nodes(node):
            s = scope
            if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                s = scope | _fn_locals(c)
                body = getattr(c, "body", [])
                if isinstance(body, list):
                    for st in body:
                        s |= _assign_names(st)
            elif isinstance(c, ast.ClassDef):
                s = scope | {x.name for x in c.body if isinstance(
                    x, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
                for st in c.body:
                    s |= _assign_names(st)
            # 只认「裸名字.属性」且该名字在当前作用域确实没绑定过：sys.platform 里的
            # platform 是属性名而不是裸名字，不会被误判成缺 import platform
            if (isinstance(c, ast.Attribute) and isinstance(c.value, ast.Name)
                    and isinstance(c.value.ctx, ast.Load)
                    and c.value.id in _STD and c.value.id not in s):
                bad.append(f"{c.value.id}.{c.attr}(L{c.lineno})")
            visit(c, s)

    visit(tree, imported | env)
    return bad


_noimp = []
for _p in sorted((_ROOT / "b2bcollect_build").glob("*.py")):
    _noimp += [f"{_p.name}: {m}" for m in _missing_imports(_p.read_text(encoding="utf-8"))]
check("b2bcollect_build 全目录：写成「模块.属性」的用法都找得到对应 import",
      not _noimp, _noimp)
# 判据自己也要验一次：漏 import 的样本必须被抓到，否则这条检查只是装饰
_ag_src = (_ROOT / "b2bcollect_build" / "access_guard.py").read_text(encoding="utf-8")
check("上述判据有效：把 access_guard 的 import os 去掉后它确实会报错（不是永远绿）",
      bool(_missing_imports(_ag_src.replace("\nimport os\n", "\n")))
      and not _missing_imports(_ag_src))

# ── 控制流转移之后还挂着语句＝整段死代码（2026-11-13 实机级事故的一手成因）──────
# solarbe_crawler._iter_keyword 里「跳过已采」那个 if 被补判据时改动过一行，continue 落在了
# if 外面：语法合法、py_compile 全绿、模块导入全绿、日志照旧写「第 1 页：20 家，新增 0」，
# 看上去只是「这批都采过了」，实际是**这一路一家都不可能再产出**。这种形状不会因为
# 改的是哪个站而只影响那个站，所以扫描范围铺到三个交付目录而不是只盯 b2bcollect_build。
_TERMINALS = (ast.Continue, ast.Break, ast.Return, ast.Raise)


def _unreachable(src):
    """返回「跳转语句之后同块还跟着真语句」的位置列表（纯字符串表达式当占位注释跳过）。"""
    tree = ast.parse(src)
    out = []
    for node in ast.walk(tree):
        for label in ("body", "orelse", "finalbody"):
            lst = getattr(node, label, None)
            if not isinstance(lst, list):
                continue
            for i, st in enumerate(lst):
                if not isinstance(st, _TERMINALS):
                    continue
                rest = [r for r in lst[i + 1:]
                        if not (isinstance(r, ast.Expr) and isinstance(r.value, ast.Constant))]
                if rest:
                    out.append(f"{type(st).__name__}@L{st.lineno}→"
                               f"{type(rest[0]).__name__}@L{rest[0].lineno}")
    return out


_dead = []
for _d in ("b2bcollect_build", "riskbird_build", "ali1688_build"):
    for _p in sorted((_ROOT / _d).glob("*.py")):
        _dead += [f"{_d}/{_p.name}: {x}" for x in _unreachable(_p.read_text(encoding="utf-8"))]
check("三个交付目录里没有任何「跳转之后还挂着语句」的死代码"
      "（if 少缩进一行的 continue 会把整路采集成零，而且一路都不报错）",
      not _dead, _dead)
# 反向对照：判据必须真能抓红——把事故那一手的形状原样做成夹具（包在 def 里，
# 否则 yield 出现在模块层，ast.parse 先抛 SyntaxError，红的是夹具不是判据）
_dead_probe = """
def it(entries):
    for e in entries:
        if e in seen:
            skipped += 1
        skipped += 1
        continue
        yield e
"""
check("上述判据有效：事故那个形状（continue 落在 if 外面）做成夹具确实报红",
      bool(_unreachable(_dead_probe)), _unreachable(_dead_probe))
check("上述判据不误伤正常写法：跳转在 if 里、后续语句在 if 外，一个都不报",
      not _unreachable("def it(entries):\n    for e in entries:\n"
                       "        if e in seen:\n            continue\n        yield e\n"),
      _unreachable("def it(entries):\n    for e in entries:\n"
                   "        if e in seen:\n            continue\n        yield e\n"))

# ── 后台权限事件现场验一次「绝不弹模态」（2026-11-11 实测：v2→v3 迁移后第一次
#    开机，等级从旧名单的 LEGACY 回填成 0，_acl_prompt_restart 的 question 框在
#    offscreen 下把整批回归拖死 600s 被 SIGABRT；在用户那边则是采集正跑着时
#    凭空弹一个必须回答的框，主线程被嵌套事件循环接管）
_pops = []


def _no_popup(*a, **k):
    _pops.append(a[1] if len(a) > 1 else str(a))
    raise AssertionError("后台权限事件弹了模态框")


_real_q = gui.QMessageBox.question
_real_w = gui.QMessageBox.warning
_caps_before = dict(w_pro._acl_caps)
try:
    for _m in ("question", "warning"):
        setattr(gui.QMessageBox, _m, staticmethod(_no_popup))
    w_pro._acl_caps = {c: True for c in edition.ALL_CAPS}      # 装作界面是按全功能建的
    edition.set_account_level(edition.LEVEL_LITE)              # 名单把这个号降到受限档
    w_pro._acl_sync_level()                                    # 心跳/refreshed 走的就是这条
    check("等级变更的后台链路一个模态都不弹（question/warning 一旦命中当场炸给测试）",
          not _pops, _pops)
    check("提示改挂在横幅上，并给一个「重启工具现在生效」的动作（不弹窗≠不告诉用户）",
          not w_pro.banner.isHidden()
          and "权限等级已变更" in w_pro.banner.text()
          and '#acl-restart' in w_pro.banner.text(), w_pro.banner.text())
    check("收口的同时标题也重投影（受限档就不能还写着内部版的标题）",
          edition.title_suffix() in w_pro.windowTitle(), w_pro.windowTitle())
    # 用户主动点那条链接时才允许问：**点一次问一次**（2026-11-19 实机报告的「换绑后点
    # 重启无反应」正是旧那个「同进程只问一次」的闸门：默认按钮是「否」，Esc/✕ 也算否，
    # 于是一次误触就永久废掉了这个进程里唯一的重启入口。防骚扰由结构保证——只有主动
    # 点击才问；后台心跳那条链路仍然一个框都不弹，那是上面两条判据盯的事。）
    _asks = []
    gui.QMessageBox.question = staticmethod(
        lambda *a, **k: (_asks.append(1), gui.QMessageBox.No)[1])
    # 这一段的日志必须自己收：上面 B 节把 w_pro._on_log 换成了「只进列表」，界面那个
    # 日志控件从那时起就不会再长字，拿 `w_pro.log` 取证只会收到一个空串，反而把判据读成假红。
    _refuse_logs = []
    _prev_on_log = w_pro._on_log
    w_pro._on_log = lambda m: (_refuse_logs.append(m), _prev_on_log(m))[1]
    try:
        w_pro._on_banner_link("#acl-restart")
        w_pro._on_banner_link("#acl-restart")
    finally:
        w_pro._on_log = _prev_on_log
    check("横幅链接→用户点击→每次都问得到（点了没反应就是需求 4 那一族缺陷："
          "宁可在这里多问一次，也不许留一个会忘掉上下文的一次性闸门）",
          len(_asks) == 2, len(_asks))
    check("拒过之后也要出声：每一次都要把「先不重启」与「这条链接随时可以再点」一起说",
          len(_refuse_logs) == 2
          and all("先不重启" in m and "重启工具现在生效" in m for m in _refuse_logs),
          _refuse_logs)
    # 只看代码行：注释里写出 QMessageBox 是**故意的**（说明为什么不能用），
    # 判据必须是「函数体里没有这条调用」，不是「文件里没出现这个词」。
    _sync_code = [ln.strip() for ln in
                  inspect.getsource(gui.MainWindow._acl_sync_level).splitlines()
                  if ln.strip() and not ln.strip().startswith("#")]
    check("只有用户点链接这一条路能走到重启询问（后台收口函数里没有弹窗与询问调用）",
          not any("QMessageBox" in ln or "_acl_prompt_restart" in ln for ln in _sync_code),
          [ln for ln in _sync_code if "QMessageBox" in ln or "_acl_prompt_restart" in ln])
    # 顶号提示：第一次必须当场说清楚（活都停了，不吭声就是莫名其妙），第二次不许叠框；
    # 并且必须是**非模态**——叫醒这条链路的是心跳/后台刷新，不是用户的点击：模态等于
    # 让一个没人点过的对话框用嵌套事件循环接管主线程（停止收尾、进度、心跳全在框后排队），
    # offscreen/无人值守下 exec 还会永久等一次永远不会来的点击（2026-11-12 实测：整批
    # 回归就是这么被拖到 SIGABRT 的）。所以「说清楚」与「不接管」只能同时靠非模态做到。
    _boxes = []
    _real_box_cls = gui.QMessageBox

    class _RecordingBox(_real_box_cls):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            _boxes.append(self)

        def show(self):                 # 无头环境只记账，不真弹窗（也别往屏幕上抢焦点）
            return None

    gui.QMessageBox = _RecordingBox
    # 上面第 B 节把 w_pro._on_log 换成了「只进列表」（那段要靠收下来的日志查文案），
    # 所以这里不能去读 log 控件，必须自己接一份：顶号这条链路的日志与提示是**两条**
    # 独立出口，只验其中一条，另一条被静默换掉也查不出来。
    _revoke_logs = []
    _real_on_log = w_pro._on_log
    w_pro._on_log = lambda m: (_revoke_logs.append(m), _real_on_log(m))[1]
    try:
        w_pro._acl_revoked_box = None
        w_pro._acl_revoked_box_shown = False
        w_pro._acl_stopped = False
        w_pro._on_acl_revoked("当前云展账号已在新设备使用，本机已停止。")
        w_pro._on_acl_revoked("当前云展账号已在新设备使用，本机已停止。")
        check("顶号只提示一次（第二次只记日志，叠框只会分不清哪次是哪个号）",
              len(_boxes) == 1, len(_boxes))
        check("顶号提示是非模态框（后台事件绝不用嵌套事件循环接管主线程）",
              bool(_boxes) and _boxes[0].isModal() is False,
              _boxes[0].isModal() if _boxes else None)
        check("非模态提示里带上下一步（怎么把使用权限拿回本机）",
              bool(_boxes) and "绑定账号" in _boxes[0].text(),
              _boxes[0].text() if _boxes else "")
        check("顶号事件同时落到日志（提示被人关掉也还有据可查）",
              any(t.startswith("⛔") for t in _revoke_logs), _revoke_logs[:2])
    finally:
        gui.QMessageBox = _real_box_cls
        w_pro._on_log = _real_on_log
        w_pro._acl_revoked_box = None
        w_pro._acl_revoked_box_shown = False
finally:
    gui.QMessageBox.question = _real_q
    gui.QMessageBox.warning = _real_w
    edition.set_account_level(None)
    w_pro._acl_caps = _caps_before
    w_pro._acl_revoked_box_shown = False

# ══════════════════════════ D2. 需求 6：身份确认与界面入口的时序
# 两条路缺一不可：
# ① 开机没问到 → 过场等待把确认**等回来**（主修：让主窗口在身份已知之后才创建，
#    于是所有高级入口天然建全，而不是只能数据池那一组能补）；
# ② 等也没等到（网络真不通）→ 窗口照旧按基础功能开，而确认随后到达时，最要紧的
#    那一组入口当场长出来，不再让人去赌那一秒的重启。
print("\n[D2. 需求 6：身份确认与界面入口的时序]")

# 本节的前提是「w_lvl2 从建好到现在没人重投影过它」。这条前提以前是**隐式**的，
# 所以它一旦被破（见 `_build` 的 docstring），报出来的 5 条红全是下游，看不出从哪坏的。
# 这里把它显式钉住：活动心跳意味着「下一拍会把当时全局等级倒灌进来」，本节不接受这种起点。
# （曾经这里还写了两条「把心跳放回来就会重演红」的自证，当场被自己跑验推翻：
# `_build` 是用 B2B_ACL_LEVEL 把等级钉在 2 的，环境变量压过 `set_account_level` 的内存覆盖，
# 所以那个构造根本造不出「倒灌」。因果没被演示出来就不留在文件里——错的注释比没有更坏。）
check("D2 前提：本节要用的窗口不带活动心跳（结论不许由墙钟决定）",
      not w_lvl2._acl_timer.isActive() and not w_lvl1._acl_timer.isActive(),
      (w_lvl2._acl_timer.isActive(), w_lvl1._acl_timer.isActive()))


def _pool_pos(win):
    """数据池那一组在配置布局里的下标与左右邻居（类型 + 标题/文字）。

    就地生长最怕的不是「没长出来」（看得见的失败），而是「长错了位置」：同一个
    账号因为网络快慢看到两套排列，那本身就是一次新报障。所以拿开机就建好的窗口
    当参照，逐项比对同一份布局里它两边的邻居。
    """
    if win.btn_pool_search is None:
        return None
    grp = win.btn_pool_search.parentWidget()
    while grp is not None and not isinstance(grp, QGroupBox):
        grp = grp.parentWidget()
    if grp is None or grp.parentWidget() is None:
        return None
    lay = grp.parentWidget().layout()
    if lay is None:
        return None

    def _at(k):
        if k < 0 or k >= lay.count():
            return None
        it = lay.itemAt(k)
        w = it.widget() if it is not None else None
        if w is None:
            return "<间距/拉伸>"
        if isinstance(w, QGroupBox):
            return f"group:{w.title()}"
        return f"{type(w).__name__}:{w.text()[:10] if hasattr(w, 'text') else ''}"

    i = lay.indexOf(grp)
    return (i, _at(i - 1), _at(i), _at(i + 1))


_gate_logs = []
_prev_log2 = w_lvl2._on_log
w_lvl2._on_log = lambda m: (_gate_logs.append(m), _prev_log2(m))[1]
_prev_caps2 = dict(w_lvl2._acl_caps)
# 就地生长只能在**内部包**里谈：丐版包根本没把这些入口编译进去，等级再怎么升也不该长出来
# （那正是下面安全边界要反证的另一半）。而此处环境变量还停在上一组用例的 lite 档，
# 直接跑就会把「档位不许越权」误读成「生长没生效」——本轮第一条红就是这么来的。
_env_before2 = os.environ.get(edition.ENV_VAR)
os.environ[edition.ENV_VAR] = "pro"
edition.reset_for_tests()
try:
    check("前置状态：按等级 2 建的窗口确实没建数据池入口（就地生长的前提是先没有）",
          w_lvl2.btn_pool_search is None and w_lvl2.btn_pool_import is None, "")
    # 安全边界的 A 面：同一个窗口、同一份环境，只把身份确认结果换成「就是丐版」。
    # 这一步必须一个字节也不建，否则下一条「确认到等级 1 就长出来」绿得没有意义。
    edition.set_account_level(edition.LEVEL_LITE, "13800000000",
                              reason="白名单等级 2（测试：确认结果就是丐版）")
    check("需求 6·安全边界：确认结果就是丐版时一个字节也不建（「没权就不留在界面树里」"
          "这条不变量本轮一寸没动）",
          w_lvl2._build_pool_group() is False and w_lvl2.btn_pool_search is None, "")

    # B 面：身份确认到等级 1，四键当场长出。与上一步唯一的差别就是确认结果，
    # 所以这两条合起来才能证明「建不建」只由权限决定，不由网络快慢决定。
    edition.set_account_level(edition.LEVEL_FULL, "13800000000",
                              reason="白名单等级 1（测试：确认到达）")
    w_lvl2._acl_sync_level()                                    # 心跳/refreshed 走的就是这条
    _pool_btns = [w_lvl2.btn_pool_import, w_lvl2.btn_pool_scan,
                  w_lvl2.btn_pool_sync, w_lvl2.btn_pool_search]
    check("需求 6：确认到达时数据池四键**当场长出来**（而不是让用户重启）",
          all(b is not None for b in _pool_btns),
          [type(b).__name__ if b else None for b in _pool_btns])
    # 写成 `b is not None and not b.isHidden()`：上一条红的时候这里会直接 AttributeError，
    # 整套件还没跑到剩下的判据就崩——判据不许把「失败」升级成「崩溃」。
    check("长出来的四键没被谁继续藏着（登记收口走的是同一套 _acl_reg）",
          all(b is not None and not b.isHidden() for b in _pool_btns),
          [None if b is None else b.isHidden() for b in _pool_btns])
    check("两条路径建出的排列逐项一致：开机就建与确认后才长，同一账号应当看到同一个界面",
          _pool_pos(w_lvl2) == _pool_pos(w_lvl1) and _pool_pos(w_lvl2) is not None,
          (_pool_pos(w_lvl2), _pool_pos(w_lvl1)))
    check("当场补齐要说一句“不用重启”（只默默把按钮变出来，用户会以为刚才界面卡了一下）",
          any("当场补齐" in m or "已恢复显示" in m for m in _gate_logs), _gate_logs[:3])
    # 上面的判据只能保证「那句话里提了一嘴」，钉不住本轮真正的缺陷：旧文案把四键已经建好
    # 这件事整条丢了，只说「需要重启才会补齐」。这里要求两件事出现在同一句里。
    _said = [m for m in _gate_logs if "权限等级已变更" in m]
    check("需求 6·说得出部分胜利：同一句里既讲「数据池已当场补齐、现在就能用」，又讲清"
          "哪些入口仍需重启（把已经好的说成坏的，用户就白重启一次或直接报「功能坏了」）",
          len(_said) == 1 and "当场补齐" in _said[0] and "现在就能用" in _said[0]
          and edition.CAP_LABELS[edition.CAP_POOL_UI] in _said[0]
          and "需重启工具才会出现" in _said[0], _said[:2])
    check("需求 6·文案不自己编：那句话里被点名的每一项都逐字等于 edition.CAP_LABELS 里的官方能力名"
          "（矩阵与文案只有一份：GUI 另编一套说法，管理员照着面板授权、用户照着界面报障，"
          "两边说的就不是同一件事了）",
          bool(_said) and all(
              p in set(edition.CAP_LABELS.values())
              for seg in _said[0].split("；") if "需重启工具才会出现" in seg
              for p in seg.replace("需重启工具才会出现", "").split("、") if p)
          and any("需重启工具才会出现" in seg and
                  seg.replace("需重启工具才会出现", "").strip("、")
                  for seg in _said[0].split("；")), (_said[:1], sorted(set(edition.CAP_LABELS.values()))))
    # 剩下的边界如实记名（不假装修好了）：另外几类入口仍要求重启，但它必须**说出来**。
    _nogrown = [w_lvl2.btn_clear_seen, w_lvl2.btn_history, w_lvl2.chk_auto_kw]
    check("需求 6·残留边界如实告知：长不出来的那几类仍没建，但必须挂出「重启才补齐」的横幅"
          "（默默少几个入口才是这次报障的根源）",
          all(b is None for b in _nogrown) and bool(w_lvl2._acl_pending),
          ([type(b).__name__ if b else None for b in _nogrown], w_lvl2._acl_pending))
finally:
    w_lvl2._on_log = _prev_log2
    w_lvl2._acl_caps = _prev_caps2
    if _env_before2 is None:
        os.environ.pop(edition.ENV_VAR, None)
    else:
        os.environ[edition.ENV_VAR] = _env_before2
    edition.reset_for_tests()

import threading as _thr                                        # noqa: E402
import time as _tm                                              # noqa: E402

_g = gui._access_guard.get_guard()
_real_start_refresh = getattr(_g, "_start_refresh", None)
_g._start_refresh = lambda: None        # 本用例验的是「等待机制」，不许真出网
os.environ[edition.ENV_VAR] = "pro"     # 同上一段：等待的收获是「能摆全套入口」，只能在内部包计
edition.reset_for_tests()
try:
    # 1) 确认在等待期间到达：必须在到的那一刻收手，而不是把预算耗完
    edition.set_account_level(edition.LEVEL_LITE, "13800000000",
                              reason="测试：开机没问到", pending=True)

    def _late_confirm():
        _tm.sleep(0.35)
        edition.set_account_level(edition.LEVEL_FULL, "13800000000",
                                  reason="测试：确认到达")
        _g._notify_refreshed()          # 与生产同一口径：_fetch 末尾就是这么叫醒界面的

    _thr.Thread(target=_late_confirm, daemon=True).start()
    _t0 = _tm.monotonic()
    _outcome, _waited = gui._wait_auth_settled(_tm.monotonic() + 6.0)
    _spent = _tm.monotonic() - _t0
    check("需求 6·主修：过场等待在确认到达那一拍就收手（6 秒预算只花了 %.2f 秒）" % _spent,
          _outcome == "settled" and 0.2 <= _spent < 3.0, (_outcome, round(_waited, 2)))
    check("需求 6·主修：等到的等级在**建窗口之前**就位（于是所有高级入口天然建全，"
          "不靠事后补）",
          edition.pool_ui_visible() and not edition.auth_pending(), "")

    # 2) 一直问不到：必须在预算处收手，并把界面交给“基础功能 + 待确认横幅”
    edition.set_account_level(edition.LEVEL_LITE, "13800000000",
                              reason="测试：还是没问到", pending=True)
    _t1 = _tm.monotonic()
    _outcome2, _waited2 = gui._wait_auth_settled(_tm.monotonic() + 0.4)
    check("需求 6·预算：问不到就收手回到基础功能，绝不把人挂在门口",
          _outcome2 == "timeout" and _tm.monotonic() - _t1 < 3.0, (_outcome2, round(_waited2, 2)))
    check("需求 6·不越权：等了也不算获得任何额外能力（失败封闭）",
          edition.account_level() == edition.LEVEL_LITE and not edition.pool_ui_visible(), "")
finally:
    if _real_start_refresh is not None:
        del _g._start_refresh           # 必须 del 而不是赋回：类方法不能被当实例属性还回去
    edition.set_account_level(None)
    if _env_before2 is None:
        os.environ.pop(edition.ENV_VAR, None)
    else:
        os.environ[edition.ENV_VAR] = _env_before2
    edition.reset_for_tests()
    app.processEvents()

# ══════════════════════════════════════════════════════════════════ E. 收尾
print("\n" + "=" * 52)
if fail:
    print(f"失败 {len(fail)} 项：")
    for f in fail:
        print("  - " + f)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(1)

print(f"全部通过：{ok} 项")

# 判定到此为止：屏幕上这句和批跑读到的退出码必须是同一个来源。这套件自己建过
# QApplication，而 Qt/线程在解释器收尾时的析构会随机段错误（本机实测：keyword_resume
# 全绿 88 项之后 rc=139，被批跑读成一次失败，单跑又绿——真红就是这么被当成抖动的）。
# flush 之后直接把退出码交给操作系统，不让任何收尾动作再改写判定。
sys.stdout.flush()
sys.stderr.flush()
os._exit(0)
