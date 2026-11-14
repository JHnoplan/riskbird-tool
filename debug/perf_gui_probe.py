#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""perf_gui_probe.py —— 界面与导出链路的成本基线（多线程/性能优化取证第二部分）。

CDP 那条线由 perf_cdp_probe.py 量。这里量的是**用户能直接感觉到的两处**（A/B），
外加链路、写入与治理层的对照档（C~G）：

  A. 日志上屏：采集时每产出一条都要过 MainWindow._on_log。今天的实现是
     「append 一行 + 把滚动条硬拧到最底」，一行一次、无一节流。低配机器上
     「界面卡住、鼠标转圈」多数就是这里——主线程被 GUI 更新占满，
     跟采集快慢没关系。所以要把「每行多少毫秒」钉成数字。
  B. 自动保存：每满 20 条 / 每 3 分钟落一次盘，三份 Excel + 两次全表清洗。
     表越大越贵，贵到一定程度就是「每隔一会儿界面明显一顿」。
  C. 后台导出 × 主线程的 GIL 争抢；D. 写《清洗后入库数据》三档对照；
     E. 一轮自动保存整体前后对照（旧实现从 git HEAD 取，不手抄）。

测法：offscreen 起 QApplication，用**真实**的 MainWindow._on_log 和真实的
AutoSaveWorker._write_raw / ParallelSession.clean_records，不造替代实现。
offscreen 不做真正的光栅化，所以这里的每行成本是**下界**（真机只会更贵），
汇报时必须带着这句话讲。

  F. HTTP 连接复用（`--skip-reuse` 可跳）：本机起一个 HTTPS 夹具（自签名证书 +
     127.0.0.1），对照「每次现建 Session」和「线程内复用 Session」。这一档给出
     website_contact._http_session 那段注释里承诺的毫秒数，并且**在服务器侧数连接数**
     ——省掉握手这件事必须是被数出来的，不是被推断的。
  G. 治理层自身开销（`--skip-gov` 可跳）：闸门 record/acquire、DoneBoard ping/wait、
     资源探测冷热的每毫秒成本，分母直接取 F 档的单请求实测值。

用法：
  QT_QPA_PLATFORM=offscreen python debug/perf_gui_probe.py
  QT_QPA_PLATFORM=offscreen python debug/perf_gui_probe.py --lines 3000 --rows 8000
  QT_QPA_PLATFORM=offscreen python debug/perf_gui_probe.py --skip-gui --skip-export \
      --skip-contention --skip-xlsx --skip-round          # 只量 F/G 两档
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "b2bcollect_build"))


def _median(vals):
    return round(statistics.median(vals), 3) if vals else None


def best_of(fn, repeats: int = 2) -> float:
    """同一个动作跑 repeats 次取**最小值**（毫秒）。

    为什么要重复：同一进程里连着量好几个档位时，先跑的那档把堆撑大、把 gc 要跟踪的
    对象留下，后跑的档位凭空贵一截。上一轮 D 档就吃了这个假象——head 报 7230ms、
    fast 报 3988ms，看着只差 1.8 倍；取最小值以后是 5196 / 1420，差 3.7 倍，
    而且跟「单独跑一遍生产函数」量出来的 1518ms 对得上了。
    取最小不是挑好看的数：耗时下界才是这段代码本身的成本，堆压力和 GC 是现场噪声，
    前后两档都受影响但受影响程度不同（越靠后越亏），不消掉就会把差距算小。
    """
    import gc
    best = None
    for _ in range(max(1, repeats)):
        gc.collect()
        s = time.perf_counter()
        fn()
        ms = (time.perf_counter() - s) * 1000.0
        best = ms if best is None else min(best, ms)
    return round(best, 1)


# ══════════════════════════════════════════════════════════════════
#  A. 日志上屏：真实 _on_log 的每行成本
# ══════════════════════════════════════════════════════════════════
def probe_log(app, lines: int) -> dict:
    import b2bcollect_gui as gui
    from PyQt5.QtWidgets import QPlainTextEdit, QTextEdit

    out = {}
    gui.MainWindow._width_floor_cap = lambda self: 800
    win = gui.MainWindow()
    win.resize(1120, 714)
    win.show()
    app.processEvents()

    msg = "  ▸ 第 0812 家 · 深圳市某某科技有限公司 ｜ 手机 13800001111 ｜ 采自搜索结果列表页"

    # 计时口径（11-14 自查改过一版）：必须是「一组 N 行的总耗时 ÷ N」。
    # 早先写的是「每 20 行只给第 20 行计时再除以 20」，等于把 19 行的开销整段丢掉，
    # 量出来的每行成本比真值小一个数量级——这种数拿去定优化方向会定错方向。
    def run_variant(name, batch, per_line, repaint_each):
        """batch 行一批：批内逐条走 per_line()，批末按 repaint_each 决定是否刷一次屏。"""
        costs = []
        total = 0
        while total < lines:
            n = min(batch, lines - total)
            s = time.perf_counter()
            for _ in range(n):
                per_line()
            if repaint_each:
                app.processEvents()
            costs.append((time.perf_counter() - s) * 1000.0 / n)
            total += n
        return {"per_line_ms": _median(costs),
                "worst_line_ms": round(max(costs), 3) if costs else None,
                "batches": len(costs)}

    variants = {}

    def fill(widget, n=3000):
        """灌到行数上限：采集中途的稳态才是用户卡的那一下，空文档不算数。"""
        widget.clear()
        for _ in range(n):
            widget.appendPlainText(msg) if isinstance(widget, QPlainTextEdit) \
                else widget.append(msg)
        app.processEvents()

    def old_line(widget):
        """HEAD 上那份 _on_log 每一行干了什么（append + 每行硬拧到底 + 告警计数）。
        这里只复刻**行为**，控件用下面真的 QTextEdit——「优化前」必须是真旧件，
        不是我以为的旧件（D/E 两档都在这上面翻过车）。"""
        widget.append(msg)
        sb = widget.verticalScrollBar()
        sb.setValue(sb.maximum())
        win._count_log_notice(msg)

    # ── 富文本档（= HEAD 那版生产代码用的控件）──
    def make_rich():
        w = QTextEdit()
        w.setReadOnly(True)
        w.setFont(win.log.font())
        w.document().setMaximumBlockCount(3000)
        w.resize(win.log.size())
        w.show()
        return w

    rich = make_rich()
    fill(rich)
    variants["rich_append_only"] = run_variant(
        "rich_append_only", 50, lambda: rich.append(msg), False)
    fill(rich)
    variants["rich_head_on_log"] = run_variant(
        "rich_head_on_log", 50, lambda: old_line(rich), False)
    fill(rich)
    variants["rich_one_repaint_per_line"] = run_variant(
        "rich_one_repaint_per_line", 1, lambda: old_line(rich), True)
    rich.close()

    # ── 纯文本档（= 现在的生产控件 win.log）──
    fill(win.log)
    variants["plain_append_only"] = run_variant(
        "plain_append_only", 50, lambda: win.log.appendPlainText(msg), False)
    # 只换控件、行为一字不改（每行硬拧到底）：这一档与 rich_head_on_log 的差
    # 才是「换控件」这一件事的收益，不混进滚动策略
    fill(win.log)
    def _plain_hard():
        win.log.appendPlainText(msg)
        sb = win.log.verticalScrollBar()
        sb.setValue(sb.maximum())
        win._count_log_notice(msg)
    variants["plain_head_scroll"] = run_variant(
        "plain_head_scroll", 50, _plain_hard, False)
    # 现在：直接调生产 _on_log（换控件 + 贴底才跟随都在里面），尺子量的就是屏幕上那条路
    fill(win.log)
    variants["plain_now_on_log"] = run_variant(
        "plain_now_on_log", 50, lambda: win._on_log(msg), False)
    fill(win.log)
    variants["plain_one_repaint_per_line"] = run_variant(
        "plain_one_repaint_per_line", 1, lambda: win._on_log(msg), True)
    # 攒批（50 行一次上屏）：量出来只是「少刷几次屏」，日志本身每行没变便宜；
    # 代价是最后一批要等一个节拍才显示，实测差距不足以换掉即时性 → 记数不采纳
    fill(win.log)
    variants["plain_batch50"] = run_variant(
        "plain_batch50", 50, lambda: win.log.appendPlainText(msg), True)

    # 顺序对照：把「优化前」那档放到最后再量一次。同一进程里先跑的档位会撑大堆、
    # 留下 GC 要跟踪的对象，后跑的凭空贵一截（D 档就是这么被骗过 2 倍）。
    # 两次数字对得上，才说明上面那串倍数不是排队顺序造出来的。
    rich2 = make_rich()
    fill(rich2)
    variants["rich_head_on_log_recheck"] = run_variant(
        "rich_head_on_log_recheck", 50, lambda: old_line(rich2), False)
    rich2.close()

    # 空转一次 processEvents（把「一次上屏」的固定开销单列出来，
    # 上面每行成本里凡是带上屏的档位都要减去这一笔才看得清是代码贵还是刷屏贵）
    costs = []
    for _ in range(30):
        s = time.perf_counter()
        app.processEvents()
        costs.append((time.perf_counter() - s) * 1000.0)
    variants["processevents_idle"] = {"per_line_ms": _median(costs)}

    out["variants"] = variants

    def _ms(key):
        v = variants.get(key, {}).get("per_line_ms")
        return v or 0.0

    out["control_swap_x"] = round(_ms("rich_head_on_log") / max(1e-9, _ms("plain_head_scroll")), 2)
    out["follow_scroll_x"] = round(_ms("plain_head_scroll") / max(1e-9, _ms("plain_now_on_log")), 2)
    out["total_x"] = round(_ms("rich_head_on_log") / max(1e-9, _ms("plain_now_on_log")), 2)
    out["order_drift_pct"] = round(
        abs(_ms("rich_head_on_log") - _ms("rich_head_on_log_recheck"))
        / max(1e-9, _ms("rich_head_on_log")) * 100.0, 1)
    out["blocks"] = win.log.document().blockCount()
    win.close()

    return out


# ══════════════════════════════════════════════════════════════════
#  B. 自动保存/导出：几千行时每轮多少钱
# ══════════════════════════════════════════════════════════════════
def make_rows(session, n: int):
    import parallel_session as ps
    import datetime
    slug = ps.SITE_ORDER[0]
    plat = session.crawlers[slug].PLATFORM_NAME
    cols = [h for h in ps.RAW_HEADERS if h != "序号"]
    rows = []
    for i in range(n):
        r = {h: "" for h in cols}
        r.update({
            "关键词": "太阳能电池板",
            "公司名称": f"深圳市某某新能源科技{i}有限公司",
            "联系人": f"张{i}",
            "岗位": "销售经理",
            "电话": f"0755-86{i:04d}",
            "手机": f"138{i:08d}",
            "邮箱": f"sales{i}@example{i}.com",
            "地址": f"广东省深圳市宝安区某某街道第{i}号厂房 A 栋 3 楼",
            "主营产品": "太阳能电池板,光伏支架,逆变器,储能电池",
            "经营模式": "生产厂家",
            "所在区域": "广东 深圳",
            "公司网址": f"https://company{i}.example.com",
            "详情URL": f"https://www.site.example.com/shop/{i}/contact.html",
            "memberId": f"m{i}",
            "数据来源": plat,
            "采集时间": datetime.datetime(2026, 11, 14, 10, 30, i % 60).strftime("%Y-%m-%d %H:%M:%S"),
            "状态": "成功",
        })
        rows.append(r)
    return rows


def probe_export(app, rows_n: int) -> dict:
    import parallel_session as ps
    import b2bcollect_gui as gui
    import customer_library_cleaner as _cleaner

    out = {"rows": rows_n}
    session = ps.ParallelSession(enabled=[])
    rows = make_rows(session, rows_n)

    # B1 快照：采集线程每加一条都会走 snapshot_results（copy 语义决定它有多贵）
    slug = ps.SITE_ORDER[0]
    c = session.crawlers[slug]
    c.seed_results(rows)
    s = time.perf_counter()
    snap = c.snapshot_results()
    out["snapshot_ms"] = round((time.perf_counter() - s) * 1000.0, 1)
    out["snapshot_rows"] = len(snap)

    tmp = Path(tempfile.mkdtemp(prefix="perf_export_"))
    try:
        # B2 原始表：今天的实现是普通 Workbook 全量重写
        p = tmp / "raw.xlsx"
        s = time.perf_counter()
        gui.AutoSaveWorker._write_raw(p, rows)
        out["write_raw_ms"] = round((time.perf_counter() - s) * 1000.0, 1)
        out["write_raw_kb"] = p.stat().st_size // 1024 if p.exists() else 0

        # B3 清洗：现在每轮只算一次（E 档数的是真次数）；这里第二次纯粹是
        # 把「洗一遍要多久」再量一次做参照，两轮结果应当一致
        s = time.perf_counter()
        clean1 = session.clean_records(rows, log=lambda m: None)
        out["clean_ms"] = round((time.perf_counter() - s) * 1000.0, 1)
        out["clean_rows"] = len(clean1)
        s = time.perf_counter()
        clean2 = session.clean_records(rows, log=lambda m: None)
        out["clean_ms_second"] = round((time.perf_counter() - s) * 1000.0, 1)
        out["clean_same"] = len(clean1) == len(clean2)

        # B4 写入库模板表
        s = time.perf_counter()
        tmp2 = str(tmp / "clean.xlsx") + ".tmp"
        _cleaner.write_clean_excel(clean1, tmp2, template_path="", log=lambda m: None)
        out["write_clean_ms"] = round((time.perf_counter() - s) * 1000.0, 1)
    except Exception as e:
        out["export_error"] = f"{e.__class__.__name__}: {str(e)[:160]}"
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    # 一轮的总账不再在这里手加：那串部件拼不出真实现场（少算了一份派生表、
    # 少算了两次模板定位），改由 probe_round_ab 直接跑生产路径给出。
    return out


def probe_contention(app, rows_n: int) -> dict:
    """后台导出在跑时，主线程往日志里塞一行要多久。

    AutoSaveWorker 是 QThread，但 Python 有 GIL：openpyxl 写表与跨站清洗都是
    纯 Python 密集段，「放到后台线程」并不等于「主线程不受影响」——主线程要等
    解释器切换回来才做得动 GUI。这一档量的就是「明明在后台跑，界面还是一顿一顿」。
    """
    import threading
    import parallel_session as ps
    import b2bcollect_gui as gui
    import customer_library_cleaner as _cleaner

    session = ps.ParallelSession(enabled=[])
    rows = make_rows(session, rows_n)
    gui.MainWindow._width_floor_cap = lambda self: 800
    win = gui.MainWindow()
    win.resize(1120, 714)
    win.show()
    app.processEvents()
    msg = "  ▸ 第 0812 家 · 深圳市某某科技有限公司 ｜ 手机 13800001111"
    for _ in range(3000):
        win.log.appendPlainText(msg)
    app.processEvents()
    # 摆成采集中的真实姿态：用户正贴着底看最新一行（新 _on_log 只在贴底时才拧滚动条，
    # 不摆到底的话下面量出来的每行成本会少算掉滚动那一笔，与旧口径不可比）
    win.log.verticalScrollBar().setValue(win.log.verticalScrollBar().maximum())
    app.processEvents()

    def measure_lines(n: int):
        """每行成本量的是**生产 _on_log**：界面线程上真正跑的就是它。
        早先这里手抄了一遍「append + 硬拧到底」，生产改了这里不跟着改，
        争抢档量出来的就是假数（同一个坑在 D/E 档踩过两次）。"""
        costs = []
        for _ in range(n):
            s = time.perf_counter()
            win._on_log(msg)
            costs.append((time.perf_counter() - s) * 1000.0)
        return costs

    out = {"rows": rows_n}
    idle = measure_lines(150)
    out["idle_line_ms"] = _median(idle)

    tmp = Path(tempfile.mkdtemp(prefix="perf_gil_"))
    stop = threading.Event()
    rounds = [0]

    def hog():
        # 后台跑的是**生产那一轮**（AutoSaveWorker.run），不是手抄的简化版：
        # 手抄版会随生产改动变成假数字，争抢档也就跟着失真。
        while not stop.is_set():
            try:
                gui.AutoSaveWorker(session, rows, str(tmp)).run()
                rounds[0] += 1
            except Exception:
                return

    th = threading.Thread(target=hog, daemon=True, name="perf-export-hog")
    th.start()
    try:
        time.sleep(0.3)                       # 先让后台真跑起来
        busy = measure_lines(400)
        out["busy_line_ms"] = _median(busy)
        out["busy_worst_ms"] = round(max(busy), 2)
        out["p95_busy_ms"] = round(sorted(busy)[int(len(busy) * 0.95) - 1], 2)
    finally:
        stop.set()
        th.join(timeout=120)
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    out["bg_rounds"] = rounds[0]
    if out["idle_line_ms"]:
        out["slowdown_x"] = round((out["busy_line_ms"] or 0) / out["idle_line_ms"], 1)
    win.close()
    return out


def load_git_function(rel_path: str, func_name: str, ref: str = "HEAD"):
    """从 git 里把某文件的历史版本取出来当模块导入，返回其中的函数。

    前后对照只用**真的旧实现**，绝不手抄旧写法：上一轮 D 档就是手抄了一份「每格新建
    Border + 新建 Alignment」的夹具当基线，量出 3.42 倍并报进汇报，而生产里 Border
    早就共用了——对照的基线一旦是抄来的，「快了几倍」就成了自证。
    """
    import subprocess
    import types
    src = subprocess.run(["git", "show", f"{ref}:{rel_path}"], capture_output=True,
                         text=True, cwd=str(ROOT))
    if src.returncode != 0:
        raise RuntimeError(f"git show {ref}:{rel_path} 失败：{src.stderr.strip()[:120]}")
    name = f"_perf_{ref}_{Path(rel_path).stem}"
    mod = types.ModuleType(name)
    mod.__file__ = str(ROOT / rel_path)
    exec(compile(src.stdout, mod.__file__, "exec"), mod.__dict__)
    return mod.__dict__[func_name]


def probe_write_clean_ab(rows_n: int) -> dict:
    """写《清洗后入库数据》三个档位同一份数据各跑一遍：

      · head     = git HEAD 的生产实现（真·优化前，每格新建 Alignment）
      · fallback = 现在这份代码里「拿不到样式号」时的降级路径（共用样式对象）
      · fast     = 现在的生产实现（正文样式号全表共用一份 StyleArray）

    三档各存一份文件，再逐格读回对齐/框线比一致——省下的时间必须没改动外观，
    这条比时间更重要：外观变了用户会发现，而时间只会「感觉快了」。
    """
    import customer_library_cleaner as cl_mod
    from openpyxl import load_workbook

    header = cl_mod.FALLBACK_HEADER
    cols = len(header)
    rows = [{k: f"文本{i}-{j}" for j, k in enumerate(header[:8])} for i in range(rows_n)]
    tmp = Path(tempfile.mkdtemp(prefix="perf_clean_ab_"))
    out = {"rows": rows_n, "cols": cols}
    orig = cl_mod._reuse_body_style
    try:
        head_fn = load_git_function("b2bcollect_build/customer_library_cleaner.py",
                                    "write_clean_excel")
    except Exception as e:
        head_fn = None
        out["head_error"] = f"{e.__class__.__name__}: {str(e)[:120]}"

    def timed(fn, tag):
        p = tmp / f"{tag}.xlsx"
        out[f"{tag}_ms"] = best_of(lambda: fn(rows, str(p), template_path="",
                                              log=lambda m: None))
        return p

    try:
        paths = {}
        if head_fn:
            paths["head"] = timed(head_fn, "head")
        cl_mod._reuse_body_style = lambda *a, **k: None      # 降级：逐格设共用样式对象
        paths["fallback"] = timed(cl_mod.write_clean_excel, "fallback")
        cl_mod._reuse_body_style = orig                       # 快路：复用样式号
        paths["fast"] = timed(cl_mod.write_clean_excel, "fast")

        # 外观核对：三种行位置 × 三种列位置，逐格读回对齐与框线
        def look(p):
            ws = load_workbook(p).active
            seen = set()
            for r in (2, max(2, rows_n // 2), rows_n + 1):
                for c in (1, cols // 2, cols):
                    cell = ws.cell(row=r, column=c)
                    seen.add((bool(cell.alignment.wrap_text), cell.alignment.vertical,
                              cell.border.left.style, cell.border.bottom.style))
            return seen
        out["looks"] = {tag: sorted(map(str, look(p))) for tag, p in paths.items()}
        out["look_identical"] = len(set(map(str, out["looks"].values()))) <= 1
        out["sizes_kb"] = {tag: p.stat().st_size // 1024 for tag, p in paths.items()}
    finally:
        cl_mod._reuse_body_style = orig
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    base = out.get("head_ms") or out.get("fallback_ms")
    if out.get("fast_ms") and base:
        out["speedup_x"] = round(base / max(1.0, out["fast_ms"]), 2)
    return out


def probe_round_ab(app, rows_n: int) -> dict:
    """一轮自动保存的真实开销：直接同步调生产路径 AutoSaveWorker.run()，跑两遍。

    这一档是整条导出链路的总账（原始表 + 两份派生表 + 中间几次全表清洗），
    也是界面线程每满 20 条就被拽进 GIL 竞争时每轮要付的那笔。
    慢档不靠手抄老代码：① 写表函数换成 git HEAD 里那份真的旧实现（逐格新建样式），
    ② 两份派生表各自重洗一遍全表（把传进去的 clean_rows 丢掉，等于老实现的调用形状）。
    同时数 clean_records 真的被调了几次——次数差才是「重复清洗」的直接证据，
    它不只多花 CPU，还会把废数据回流（pending.add_waste）和 ♻ 日志触发两遍。
    """
    import parallel_session as ps
    import b2bcollect_gui as gui
    import customer_library_cleaner as cl_mod

    session = ps.ParallelSession(enabled=[])
    rows = make_rows(session, rows_n)
    tmp = Path(tempfile.mkdtemp(prefix="perf_round_ab_"))
    out = {"rows": rows_n}
    orig_write = cl_mod.write_clean_excel
    orig_cleaned = gui.AutoSaveWorker._write_cleaned
    orig_mobile = gui.AutoSaveWorker._write_cn_mobile_file
    calls = [0]
    real_clean = session.clean_records

    mode = ["now"]
    skipped = [False]

    def counted(rows_, log=None, **kw):
        if mode[0] == "legacy" and not skipped[0]:
            # run() 开头那一次清洗是老实现里没有的（老版是两份派生表各洗各的），
            # 慢档只跳过这一次，剩下两次才是老实现真的付过的两遍全表清洗。
            # 判据用专门的标记而不是「计数还是 0」：计数为 0 会连着出现两次，
            # 那样会把两份派生表的清洗也一起跳掉，量出一条谁都没洗的假「优化前」。
            skipped[0] = True
            return rows_
        calls[0] += 1
        return real_clean(rows_, log=log)

    def legacy_cleaned(self, path, rows_, clean_rows=None):
        return orig_cleaned(self, path, rows_)          # 丢掉复用，逼它自己再洗一遍

    def legacy_mobile(self, path, rows_, clean_rows=None):
        return orig_mobile(self, path, rows_)

    head_write = None
    try:
        head_write = load_git_function("b2bcollect_build/customer_library_cleaner.py",
                                       "write_clean_excel")
    except Exception as e:
        # 取不到旧实现就不量慢档，绝不拿「降级路径」冒充「优化前」：
        # 两个数长得越像，越容易在汇报里被当成同一件事
        out["legacy_error"] = f"{e.__class__.__name__}: {str(e)[:120]}"

    try:
        session.clean_records = counted

        def _round_once():
            calls[0] = 0                      # 次数按「一轮」计，best_of 重复跑时各自复位
            skipped[0] = False
            gui.AutoSaveWorker(session, rows, str(tmp)).run()

        def one_round():
            # 同步跑 run()：量的是这一轮的纯开销，不等线程调度；取最小值消堆噪声
            return best_of(_round_once)

        calls[0] = 0
        out["now_ms"] = one_round()
        out["now_clean_calls"] = calls[0]

        # ── 还原成优化前的样子：旧实现从 git HEAD 取，不手抄（同一份数据、同一次运行）──
        if head_write:
            mode[0] = "legacy"
            cl_mod.write_clean_excel = head_write            # 逐格新建样式的老写法
            gui.AutoSaveWorker._write_cleaned = legacy_cleaned
            gui.AutoSaveWorker._write_cn_mobile_file = legacy_mobile
            calls[0] = 0
            out["legacy_ms"] = one_round()
            out["legacy_clean_calls"] = calls[0]
        out["files_per_round"] = sorted(p.name for p in tmp.glob("*.xlsx"))
    finally:
        cl_mod.write_clean_excel = orig_write
        gui.AutoSaveWorker._write_cleaned = orig_cleaned
        gui.AutoSaveWorker._write_cn_mobile_file = orig_mobile
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    if out.get("now_ms") and out.get("legacy_ms"):
        out["speedup_x"] = round(out["legacy_ms"] / max(1.0, out["now_ms"]), 2)
    # 限流判据：优化后一轮多久 → 下一轮至少隔多久（写盘占墙钟 5%，夹在 3~120 秒）
    cost = out.get("now_ms") or 0
    out["gap_s_now"] = round(min(120.0, max(3.0, cost / 1000.0 / 0.05)), 1)
    out["gap_s_legacy"] = round(min(120.0, max(3.0, (out.get("legacy_ms") or 0)
                                               / 1000.0 / 0.05)), 1)
    return out


def probe_http_reuse(req_n: int = 24) -> dict:
    """F 档：HTTP 连接复用前后对照——只量 `_http_session` 这一层，不碰真外网。

    为什么要在本机起一个 HTTPS 夹具，而不是直接打公网站点：这一档要证明的东西是
    「省掉的是握手，不是运气」。打公网的话，DNS、RTT、对方负载、限流退避全混进数字里，
    同一份代码中午和晚上能差三倍，那种数写进汇报就是编故事。本地夹具把这些变量全
    消掉：证书是现造的自签名、服务器绑 127.0.0.1、页面是一个固定的静态 HTML，
    两档唯一的差别就是「每次现建 Session」还是「线程内复用 Session」。

    **这个数省下的是下界**：localhost 上手握手的网络延迟为 0，量到的只有 TLS 的 CPU
    成本；真公网上一次握手还要额外付 1 个 TCP RTT + 1~2 个 TLS RTT，所以实际省下的
    只会比这里多。汇报时必须连着这句话讲，否则就是拿本地数冒充外网数。

    夹具用 `verify=False`：这是探针自己的本地环回服务器 + 自签名证书，关掉校验不影响
    被量的对象（连接复用），也不是生产口径——`website_contact.fetch_html` 里 TLS 校验
    一律不关，那条纪律有它自己的用例守着（见 test_site_enrich_offline.py）。
    """
    import http.server
    import ssl
    import subprocess
    import threading
    from pathlib import Path as _P

    out = {"req_n": req_n}
    # 环回必须直连：如果机器上挂着 HTTPS_PROXY，两档都会被顶到代理上去，量到的
    # 就不是"握手的差别"而是代理链路的差别。用 no_proxy 环境变量而不是改 Session，
    # 是为了让被量的那一层代码保持生产原样（改了 trust_env 就等于测另一份代码）。
    _saved_np = {k: os.environ.get(k) for k in ("no_proxy", "NO_PROXY")}
    os.environ["no_proxy"] = "127.0.0.1,localhost"
    os.environ["NO_PROXY"] = "127.0.0.1,localhost"

    def _restore_np():
        # 探针改过进程环境变量就必须自己收干净：留着 no_proxy 出去，后面所有档位
        # （以及同一进程里跑的生产代码）都会以为这台机不需要代理——那是把探针的
        # 假设偷偷种进被测量者的环境里。
        for _k, _v in _saved_np.items():
            if _v is None:
                os.environ.pop(_k, None)
            else:
                os.environ[_k] = _v

    try:
        import requests
        try:
            import urllib3
            urllib3.disable_warnings()          # 自签名证书别把 stderr 刷满
        except Exception:
            pass
    except Exception as e:
        _restore_np()
        out["error"] = f"requests 不可用：{e.__class__.__name__}"
        return out

    tmp = _P(tempfile.mkdtemp(prefix="perf_tls_"))
    cert, key = tmp / "c.pem", tmp / "k.pem"
    try:
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                        "-keyout", str(key), "-out", str(cert), "-days", "1",
                        "-subj", "/CN=localhost"],
                       check=True, capture_output=True)
    except Exception as e:
        import shutil as _sh
        _sh.rmtree(tmp, ignore_errors=True)
        _restore_np()
        out["error"] = f"造不出本地证书，F 档缺测：{e.__class__.__name__}: {str(e)[:120]}"
        return out

    page = (b"<html><body><h1>\xe5\xae\x89\xe5\x9f\xb9\xe7\x94\xb5\xe5\xad\x90</h1>"
            + b"<p>" + b"x" * 4096 + b"</p></body></html>")
    cnt = {"conn": 0, "req": 0}

    class _Handler(http.server.BaseHTTPRequestHandler):
        # HTTP/1.1 才允许 keep-alive：留 1.0 的话服务器每答完一次就断，
        # "复用"这一档根本没有可复用的连接，量出来会和直连一模一样——
        # 那种"两边都不变"的红是夹具的错，不是优化的错，所以这一行是夹具的关键。
        protocol_version = "HTTP/1.1"

        def setup(self):
            cnt["conn"] += 1
            super().setup()

        def log_message(self, *a):
            pass

        def do_GET(self):
            cnt["req"] += 1
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

    srv = None
    stop = threading.Event()
    dead = {}
    try:
        import website_contact as wc
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(cert), str(key))
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)

        def _loop():
            # 不用 `serve_forever()` + `shutdown()`：监听套接字被 SSL 包住之后，TLS 握手
            # 是在 `accept()` 里面做的，一条半途而废的连接理论上能把服务线程钉在那里，
            # 那时 `shutdown()` 会一直等它"确认已停"——探针把自己跑成挂死，比量不到数字
            # 更糟（本次批跑里真正停住的是 G 档，原因见那里的注释；这一档是被同一处
            # 排队连累的，所以顺手把它也改成"不可能拦住退出"的写法）。
            # `handle_request()` 每轮最多阻塞一个 select 周期，停止标志一看就能退。
            try:
                while not stop.is_set():
                    srv.handle_request()
            except Exception as e:
                dead["why"] = f"{type(e).__name__}: {str(e)[:120]}"
        th = threading.Thread(target=_loop, daemon=True)
        th.start()
        url = f"https://127.0.0.1:{srv.server_address[1]}/"

        def _alive():
            # 夹具线程死了就立刻缺测：继续发请求只会让每一发都撞到 10 秒超时，
            # 二十几发下来"探针挂了"和"这台机真慢"长得一模一样，读不出原因。
            return th.is_alive() and not dead

        assert _alive(), f"夹具服务器没起来：{dead.get('why')}"

        def _one_plain(u):
            r = requests.get(u, timeout=10, verify=False)
            try:
                return r.content
            finally:
                r.close()

        def _one_pool(u):
            r = wc._http_session().get(u, timeout=10, verify=False)
            try:
                return r.content
            finally:
                r.close()

        cnt["conn"] = cnt["req"] = 0
        t = time.perf_counter()
        for _ in range(req_n):
            _one_plain(url)
        out["plain_ms"] = round((time.perf_counter() - t) * 1000.0, 1)
        out["plain_conns"] = cnt["conn"]
        out["plain_reqs"] = cnt["req"]
        assert _alive(), f"量到一半夹具服务器退了：{dead.get('why')}"

        cnt["conn"] = cnt["req"] = 0
        _one_pool(url)                        # 预热一次：把首条连接建起来再计时
        # 只清**请求数**，连接数连着预热一起数。两种"图省事"的写法都会把证据写歪：
        # 全清零 → 报出"24 次请求开了 0 条连接"那种废话；全不清 → 请求数报 25 发而
        # 时间只含 24 发（第一版就是这样，账目对不上但数字看着还挺像），这一档要交付的
        # 恰恰是"次数"这件事，所以两个口径必须各自说清楚自己数的是哪一段。
        warm_reqs = cnt["req"]                  # =1
        cnt["req"] = 0                          # 连接数这里**不**清零：要的就是总共开了几条
        t = time.perf_counter()
        for _ in range(req_n):
            _one_pool(url)
        out["pool_ms"] = round((time.perf_counter() - t) * 1000.0, 1)
        # cnt["conn"] 从头到尾没清过，所以它本身就已经是"预热+计时"的总连接数；
        # 再叠一次 warm_conns 就是把它数了两遍（1 条复用连接会被报成 2 条）。
        out["pool_conns"] = cnt["conn"]
        out["pool_reqs"] = cnt["req"]           # 只有计时段那 req_n 发
        out["pool_reqs_all"] = warm_reqs + cnt["req"]
        wc.close_thread_session()

        out["plain_per_req_ms"] = round(out["plain_ms"] / req_n, 2)
        out["pool_per_req_ms"] = round(out["pool_ms"] / req_n, 2)
        # 夹具自证：两档都必须正好收到 req_n 发。少一发说明有一发在路上掉了，
        # 那"每发多少毫秒"这个除法就不成立——宁可报缺测，也不报一个分母对不上的均值。
        out["fixture_ok"] = (out["plain_reqs"] == req_n and out["pool_reqs"] == req_n)
        if not out["fixture_ok"]:
            # 说话要算数：上面那句"对不上就不报均值"必须真的拦住，否则它就是一句装饰。
            out["error"] = (f"夹具实收次数与 req_n 不符（直连 {out['plain_reqs']}/"
                            f"复用 {out['pool_reqs']}，应为 {req_n}），本档数字不作数")
        out["speedup_x"] = (round(out["plain_ms"] / out["pool_ms"], 2)
                            if out["pool_ms"] else None)
        out["saved_per_req_ms"] = round(out["plain_per_req_ms"] - out["pool_per_req_ms"], 2)
        # 一家官网最多 6 页（MAX_PAGES_PER_SITE）：换算成"回采一家"这个业务单位才看得出意义
        pages = getattr(wc, "MAX_PAGES_PER_SITE", 6)
        out["site_pages"] = pages
        out["site_plain_ms"] = round(out["plain_per_req_ms"] * pages, 1)
        out["site_pool_ms"] = round(out["pool_per_req_ms"] * pages, 1)
        # 一家官网要付几次握手——这是这一档真正要交付的那句话（复用档只剩首页那一次）
        out["site_handshakes_plain"] = pages
        out["site_handshakes_pool"] = 1
    except Exception as e:
        out["error"] = f"{e.__class__.__name__}: {str(e)[:160]}"
    finally:
        stop.set()
        if srv is not None:
            try:
                # 这里**不调用 shutdown()**，理由写在上面 _loop 的注释里。收口只要做到
                # "不会拦住进程退出"：服务线程与处理器线程都是 daemon，
                # block_on_close=False 让 server_close() 不去 join 任何线程，
                # keep-alive 那条连接已经由 close_thread_session() 关掉了。
                srv.block_on_close = False
                srv.server_close()
            except Exception:
                pass
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
        _restore_np()
    return out


def probe_governor(req_ms: float = None) -> dict:
    """G 档：治理层自身每样本开销——"自适应"不能是拿性能换的口号。

    判据只有一条：治理的开销必须比一次网络请求小一两个数量级，否则闸门还没开始
    治理就已经把吞吐吃掉了。分母直接取 F 档实测的单请求毫秒数，不引入外部假设。
    """
    out = {}
    try:
        import perf_governor as pg
    except Exception as e:
        out["error"] = f"perf_governor 不可用：{e.__class__.__name__}"
        return out
    n = 5000                                      # 都是微秒级动作，5000 次足够稳定
    pg.invalidate()
    t = time.perf_counter()
    for _ in range(n):
        pg.snapshot()
    out["snapshot_warm_ms"] = round((time.perf_counter() - t) * 1000.0 / n, 5)
    t = time.perf_counter()
    for _ in range(200):                      # 冷探测真读内核，次数给少一点
        pg.invalidate()
        pg.snapshot()
    out["snapshot_cold_ms"] = round((time.perf_counter() - t) * 1000.0 / 200, 4)

    g = pg.Gate("probe", max_permits=8, initial=4)
    t = time.perf_counter()
    for _ in range(n):
        g.record(True, latency_ms=800.0)
    out["record_ms"] = round((time.perf_counter() - t) * 1000.0 / n, 5)
    t = time.perf_counter()
    for _ in range(n):
        if g.acquire(timeout=1.0):
            g.release()
    out["acquire_release_ms"] = round((time.perf_counter() - t) * 1000.0 / n, 5)

    # DoneBoard 分两个数：ping 本身多贵，以及**等待方被 ping 叫醒要多久**。
    # 这里踩过一个自己的坑：第一版写成 `b.ping(); b.wait(0.05, b.n)`——ping 已经把
    # 计数加过，再拿新计数去 wait 就等于"条件不满足"，每次都睡满 50ms；20000 次就是
    # 17 分钟，看上去像探针挂死。量的根本不是开销，是自己造出来的睡眠。
    # 正确做法：让另一条线程带着**旧**计数睡在板上，再 ping，量它多久返回。
    b = pg.DoneBoard()
    t = time.perf_counter()
    for _ in range(n):
        b.ping()
    out["board_ping_ms"] = round((time.perf_counter() - t) * 1000.0 / n, 5)
    import threading as _th
    lat = []
    last = b.n
    tp = {}

    def _sleeper():
        s = time.perf_counter()
        b.wait(1.0, last)                     # 睡在板上，等一次 ping
        e = time.perf_counter()
        # 只算「ping 之后多久返回」。主线程在 ping 之前先睡了 3ms（等对方真的进到
        # wait 里），第一版从等待方一进 wait 就开始计时，于是那 3ms 被原样计进了
        # "唤醒延迟"——拿夹具自己的等待冒充被测者的成本，报出来的数会比真实唤醒贵一个
        # 数量级，恰好把"事件等待到底快多少"这件事糊掉。
        t0 = tp.get("t")
        lat.append(max(0.0, (e - t0) * 1000.0) if t0 else (e - s) * 1000.0)

    for _ in range(200):
        last = b.n
        tp = {}
        w = _th.Thread(target=_sleeper, daemon=True)
        w.start()
        time.sleep(0.003)                     # 确认真的睡进去了再叫醒它
        tp["t"] = time.perf_counter()
        b.ping()
        w.join(2.0)
    out["board_wakeup_avg_ms"] = round(sum(lat) / len(lat), 4) if lat else None
    out["board_wakeup_med_ms"] = _median(lat)
    # 中位数太小 = 等待方根本没睡下去就被叫醒了，那一档量的是线程启停不是唤醒延迟
    out["board_wakeup_credible"] = bool(out["board_wakeup_med_ms"]
                                        and out["board_wakeup_med_ms"] >= 0.02)
    out["old_poll_period_calc_ms"] = 500.0 * 6    # 旧口径按下表**算**出来的：不是量出来的
    pg.drop_gate("probe")

    per = (out["record_ms"] + out["acquire_release_ms"] + out["board_ping_ms"])
    # 注意别把 snapshot_warm_ms 再加一遍：Gate.record() 内部就会走一次
    # _apply_pressure_locked() → snapshot()，那一笔已经含在 record_ms 里了。
    # 单列 snapshot_warm_ms 只是为了说明"它凭什么可以忽略"，不是第四个独立开销。
    out["gate_total_ms_per_site"] = round(per, 5)
    if req_ms:
        out["vs_one_request_pct"] = round(per / float(req_ms) * 100.0, 4)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lines", type=int, default=2000, help="日志压测行数")
    ap.add_argument("--rows", type=int, default=5000, help="导出压测行数")
    ap.add_argument("--skip-gui", action="store_true")
    ap.add_argument("--skip-export", action="store_true")
    ap.add_argument("--skip-contention", action="store_true")
    ap.add_argument("--skip-xlsx", action="store_true", help="跳过 D 档（写入库表三档对照）")
    ap.add_argument("--skip-round", action="store_true", help="跳过 E 档（一轮自动保存前后对照）")
    ap.add_argument("--skip-reuse", action="store_true", help="跳过 F 档（HTTP 连接复用前后对照）")
    ap.add_argument("--skip-gov", action="store_true", help="跳过 G 档（治理层自身开销）")
    ap.add_argument("--reuse-n", type=int, default=24, help="F 档每档请求次数")
    args = ap.parse_args()

    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    result = {"lines": args.lines, "rows": args.rows,
              "note": "offscreen 不做光栅化，每行成本是下界"}
    ru = {}                                      # F 档结果；跳过时 G 档也要能拿到分母=None
    if not args.skip_gui:
        result["log"] = probe_log(app, args.lines)
        lg = result["log"]
        v = lg["variants"]
        print(f"──── A. 日志上屏（真实 MainWindow.log，稳态 {lg['blocks']} 行，共 {args.lines} 行）────")
        for key, label in (
                ("rich_append_only", "旧控件：只 append"),
                ("rich_head_on_log", "旧控件 + 旧 _on_log（每行拧到底）"),
                ("rich_one_repaint_per_line", "旧控件：每行刷一次屏"),
                ("plain_append_only", "换控件后：只 append"),
                ("plain_head_scroll", "换控件 + 旧滚动（隔离变量用）"),
                ("plain_now_on_log", "现在：生产 _on_log"),
                ("plain_one_repaint_per_line", "现在：每行刷一次屏"),
                ("plain_batch50", "现在 + 攒 50 行才刷屏"),
                ("rich_head_on_log_recheck", "旧控件+旧 _on_log 复测（验排队偏）"),
        ):
            d = v.get(key) or {}
            print(f"  {label:<34}: {d.get('per_line_ms')} ms/行"
                  f"（最坏一批 {d.get('worst_line_ms')} ms/行）")
        print(f"  {'一次空转 processEvents':<34}: "
              f"{v.get('processevents_idle', {}).get('per_line_ms')} ms")
        print(f"  → 换控件省 {lg['control_swap_x']} 倍；不抢滚动再省 "
              f"{lg['follow_scroll_x']} 倍；合计 {lg['total_x']} 倍")
        print(f"     「优化前」那档首尾各量一次，两次差 {lg['order_drift_pct']}%"
              "（差得远就说明倍数是排队顺序造出来的，这组数不能用）")
    if not args.skip_export:
        result["export"] = probe_export(app, args.rows)
        ex = result["export"]
        print(f"──── B. 自动保存/导出（{args.rows} 行）────")
        if ex.get("export_error"):
            print(f"  ⚠ 导出链路异常      : {ex['export_error']}")
        print(f"  snapshot_results    : {ex.get('snapshot_ms')} ms（{ex.get('snapshot_rows')} 行）")
        print(f"  写原始表            : {ex.get('write_raw_ms')} ms（{ex.get('write_raw_kb')} KB）")
        print(f"  全表清洗（一遍）    : {ex.get('clean_ms')} ms（→{ex.get('clean_rows')} 行）")
        print(f"  同一份再洗一遍      : {ex.get('clean_ms_second')} ms"
              f"（结果一致={ex.get('clean_same')}；每轮真洗几次看 E 档的次数）")
        print(f"  写入库模板表        : {ex.get('write_clean_ms')} ms")
    if not args.skip_contention:
        result["contention"] = probe_contention(app, args.rows)
        ct = result["contention"]
        print(f"──── C. 后台导出 × 主线程（同 {ct['rows']} 行，GIL 争抢）────")
        print(f"  主线程一行日志·空闲时 : {ct['idle_line_ms']} ms")
        print(f"  主线程一行日志·后台写盘: {ct['busy_line_ms']} ms"
              f"（P95 {ct['p95_busy_ms']} ms，最坏一次 {ct['busy_worst_ms']} ms）"
              f" → 中位数慢 {ct.get('slowdown_x')} 倍")
        print(f"  后台这段时间完成整轮导出: {ct['bg_rounds']} 轮")
        print("     ↑ 后台线程跑的就是生产那一路 AutoSaveWorker.run()。中位数看着没变"
              "并不代表界面没受影响：P95 和最坏那一次才是用户手感到的「顿一下」。")
    if not args.skip_xlsx:
        result["write_clean"] = probe_write_clean_ab(min(args.rows, 5000))
        xs = result["write_clean"]
        print(f"──── D. 写《清洗后入库数据》三档对照（{xs['rows']} 行 × {xs['cols']} 列 = "
              f"{xs['rows'] * xs['cols'] // 1000}k 单元格）────")
        if xs.get("head_error"):
            print(f"  ⚠ 取不到 git HEAD 的旧实现，head 档缺测：{xs['head_error']}")
        for key, label in (("head_ms", "head：优化前（每格新建样式）"),
                           ("fallback_ms", "fallback：降级路（共用样式对象）"),
                           ("fast_ms", "fast：现在（共用样式号）")):
            print(f"  {label:<30}: {xs.get(key)} ms（{xs.get('sizes_kb', {}).get(key[:-3])} KB）")
        print(f"  → 相对优化前快 {xs.get('speedup_x')} 倍；"
              f"外观逐格读回一致 = {xs.get('look_identical')}")
        print(f"     读回的样式：{list(xs.get('looks', {}).values())[:1]}")
    if not args.skip_round:
        result["round"] = probe_round_ab(app, args.rows)
        rd = result["round"]
        print(f"──── E. 一轮自动保存 = 生产路径 AutoSaveWorker.run()（{rd['rows']} 行）────")
        if rd.get("legacy_error"):
            print(f"  ⚠ 旧实现取不到，慢档缺测：{rd['legacy_error']}")
        print(f"  优化前 : {rd.get('legacy_ms')} ms（全表清洗 {rd.get('legacy_clean_calls')} 次）")
        print(f"  优化后 : {rd.get('now_ms')} ms（全表清洗 {rd.get('now_clean_calls')} 次）"
              f" → 快 {rd.get('speedup_x')} 倍")
        print(f"  每轮产物 : {rd.get('files_per_round')}")
        print(f"  按「写盘占墙钟 5%」折算的自适应间隔："
              f"优化前 {rd.get('gap_s_legacy')} 秒一轮 → 优化后 {rd.get('gap_s_now')} 秒一轮")
    if not args.skip_reuse:
        result["reuse"] = probe_http_reuse(args.reuse_n)
        ru = result["reuse"]
        print(f"──── F. HTTP 连接复用（本地 HTTPS 夹具 {args.reuse_n} 次请求，绕过系统代理）────")
        if ru.get("error"):
            print(f"  ⚠ 夹具起不来，F 档缺测：{ru['error']}")
        else:
            print(f"  每次现建 Session : {ru['plain_ms']} ms / {ru['plain_reqs']} 次请求"
                  f"（{ru['plain_per_req_ms']} ms/次，真建了 {ru['plain_conns']} 条连接）")
            print(f"  线程内复用       : {ru['pool_ms']} ms / {ru['pool_reqs']} 次请求"
                  f"（{ru['pool_per_req_ms']} ms/次，含预热共 {ru['pool_reqs_all']} 次请求"
                  f"只开了 {ru['pool_conns']} 条连接）")
            print(f"  → 快 {ru['speedup_x']} 倍；每次请求省 {ru['saved_per_req_ms']} ms；"
                  f"一家官网 {ru['site_pages']} 页：{ru['site_plain_ms']} ms → {ru['site_pool_ms']} ms")
            print("     ↑ 连接数是**数出来的**（服务器侧 setup() 每开一条连接计一次），"
                  f"不是推断：复用档 {ru['pool_reqs_all']} 次请求只开了 {ru['pool_conns']} 条连接，"
                  "这就是「省掉握手」这件事本身的证据。")
            print("     ↑ 这是下界：localhost 上握手没有网络延迟，只算了 TLS 的 CPU；"
                  "真公网每次还要多付 1 个 TCP RTT + 1~2 个 TLS RTT。")
            print(f"     ↑ 夹具自证：服务器侧实收 直连 {ru['plain_reqs']} 发 / 复用 "
                  f"{ru['pool_reqs']} 发（应各等于 {ru['req_n']}）→ {ru.get('fixture_ok')}；"
                  "对不上就不报均值，报缺测。")
    if not args.skip_gov:
        result["gov"] = probe_governor(ru.get("plain_per_req_ms") if not args.skip_reuse else None)
        gv = result["gov"]
        print("──── G. 治理层自身开销（闸门+看板+资源探测）────")
        if gv.get("error"):
            print(f"  ⚠ {gv['error']}")
        else:
            print(f"  snapshot（TTL 命中）: {gv['snapshot_warm_ms']} ms/次")
            print(f"  snapshot（冷探真读内核）: {gv['snapshot_cold_ms']} ms/次")
            print(f"  gate.record 一个样本 : {gv['record_ms']} ms"
                  f"（含内部那一次 snapshot，不另计）")
            print(f"  acquire+release 一对 : {gv['acquire_release_ms']} ms")
            print(f"  DoneBoard.ping       : {gv['board_ping_ms']} ms")
            print(f"  等待方被 ping 叫醒   : 平均 {gv['board_wakeup_avg_ms']} ms、"
                  f"中位 {gv['board_wakeup_med_ms']} ms"
                  f"（可信={gv['board_wakeup_credible']}）")
            print(f"     ↑ 对照：旧口径 join(0.5) 逐条线程问一遍 × 6 路 = 一轮 "
                  f"{gv['old_poll_period_calc_ms']:.0f} ms 才问完一次"
                  "——这个是**按旧代码算出来的**，不是量出来的，两者不能混着报。")
            print(f"  → 闸门一家公司的全部治理开销 {gv['gate_total_ms_per_site']} ms"
                  + (f"，占一次真实请求的 {gv['vs_one_request_pct']}%" if gv.get("vs_one_request_pct")
                     is not None else "（F 档未跑，没有分母）"))
    print(f"PROBE_JSON {json.dumps(result, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    sys.exit(main())
