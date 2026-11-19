#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""需求 8 的变异复验台：把这次的加固逐个退回原状，确认新判据真的会红。

为什么又开一份、不并进 debug/mutation_check_r6.py：那台的 SUITES 只挂了需求 6 的两套
判据，锚点也全在那两个文件里；需求 8 的判据在 test_crash_forensics_offline.py，变异点
还横跨 crash_forensics / pool_dialogs / b2bcollect_gui 三个文件。并进去会让"哪条判据守
哪个变异"这张表变成两个需求混在一起查，出红时分不清是谁的家事。

与 r6 同一套纪律（那些坑都是真踩出来的，这里逐条继承）：
  · 按**文件**做变异，不做内存打桩：本套件的 J/M 段是拿磁盘源码做 AST 分析的，内存桩的
    __code__.co_filename 是 "<mutation>"，那些判据只会崩，报不出红。
  · 锚点必须整行唯一：`count!=1` 当场拒绝。上一轮有人拿 4 空格当锚点而真实行是 12 空格，
    锚点匹到了行尾那一段、变异把下一行拼成错误缩进，报出来的是 IndentationError——看着红
    其实什么都没验。
  · apply 之前先 `compile()`：语法错的变异必须死在夹具里，不许伪装成判据红。
  · 三种结论分开：✅ 杀红 / ❌ 没牙（判据仍绿，必须补）/ ❌ 变异不成立（套件没跑到判定）。
  · 显式接管 SIGTERM/SIGINT：批跑看门狗用 timeout 杀进程时 finally **不跑**，源文件会被
    留在变异态，对交付目录等于工作区被改坏。

用法：
    python debug/mutation_check_r8.py            # 基线 + 全部 8 个变异
    python debug/mutation_check_r8.py A N        # 只跑指定的
    python debug/mutation_check_r8.py restore    # 出事后手工还原（备份仍在时）
"""
import shutil
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
CF = ROOT / "b2bcollect_build" / "crash_forensics.py"
PD = ROOT / "b2bcollect_build" / "pool_dialogs.py"
GUI = ROOT / "b2bcollect_build" / "b2bcollect_gui.py"

SUITES = {
    # 本套件登记的是 ✅/❌ 那一族（与 acl 同形），结果行含「结果：」
    "forensics": (ROOT / "b2bcollect_build" / "test_crash_forensics_offline.py",
                  "❌", ("✅", "❌")),
}

MUTS = {
    # ── A/B/C：取证钩子的三条「交回原来那一份」的支路 ─────────────────────────
    # A：三道守卫**同时**退回——这才是本轮真炸过的形状（旧代码三道一道都没有）。只退
    #    其中一道不会红，且原因各不相同（本轮一个个量过，不是推想）：
    #      · 只退 install 那道 → reset 会把钩子真摘下来，install 捕不到自己；
    #      · 只退 reset 那道 → install 的 `prev is _hook` 判定把链救回来（就是下面的 B）；
    #      · 两道都退 → `_hook` 调用时还会纠正自环，Traceback 照旧打印（本轮实测）。
    #    上一轮这里报「N2/N4 仍然绿」，我把它当成「防线冗余」收了；这一轮直探针拿到
    #    CHAIN_SELF=True、与套件报绿直接矛盾，查下去才发现真正的原因是 **N2 自己就是假绿**：
    #    它写的是子串包含 `"CHAIN_SELF=False" in err`，而下一行 `THREAD_CHAIN_SELF=False`
    #    的尾巴恰好含有这个子串——链已经成环、判据却看不见。已改成按 KEY=VALUE 解析。
    #    同一个台子又探出第 2 条假绿：变异态下 stderr 里仍然有**一段** Traceback（后台线程
    #    那一段走 threading 默认输出），而 N4 旧写法只判「有没有 Traceback」——顶层那一条
    #    已经被递归吞干净了，判据却为真。现在 N4/N5 只认顶层那条自己的类型+消息与栈帧名。
    "A": dict(
        file=CF, suite="forensics",
        pairs=[("    _OLD_EXCEPTHOOK = sys.__excepthook__ if prev is _hook or prev is None else prev\n",
                "    _OLD_EXCEPTHOOK = prev\n"),
               ("        if sys.excepthook is _hook:\n",
                "        if False:  # mutated\n"),
               ("        if nxt is _hook or nxt is None:\n            nxt = sys.__excepthook__\n",
                "        if nxt is None:  # mutated3：调用时也不纠正自环\n            nxt = sys.__excepthook__\n")],
        needles=["N2 装两次之后链里也没有自己", "N4 顶层那条异常按自己的类型",
                 "N5 打印的栈里能认出出事那个函数"],
    ),
    # B：reset_for_tests 只清标记、不摘钩子。A 的守卫还在，所以 N 段仍然绿——这条变异
    #    专门证明 B7 那道岗不是 A 的复读：两道岗各自咬得住自己那一半。
    "B": dict(
        file=CF, suite="forensics",
        pair=("        if sys.excepthook is _hook:\n",
              "        if False:  # mutated\n"),
        needles=["B7 reset 之后钩子真的摘下来了"],
    ),
    # C：后台线程不再交回默认行为（只写我们自己的文件 = 用户对着一个什么都不报的终端）。
    # 整块 if/else 一起删：本机实测 Python 3.13 的 `threading.excepthook` 默认值不是 None
    # 而是内建函数（与 `threading.__excepthook__` 同一个对象），只删 else 那支的话 if 支
    # 照样打印——那一次「判据仍绿」是变异点选错了，不是判据是装饰（本轮就这么红过一次）。
    "C": dict(
        file=CF, suite="forensics",
        pairs=[("""        if _OLD_THREADHOOK is not None:
            _OLD_THREADHOOK(args)
        else:
            default = getattr(threading, "__excepthook__", None)
            if default is not None and default is not _thread_hook:
                default(args)
""", "        pass  # mutated：不交回原来那一份\n")],
        needles=["N6 后台线程也交回了默认行为"],
    ),
    # ── D/E/H：三处「永不抛 / 同一口径两处都要做」的兜底 ──────────────────────
    # D：_score_of 数字支路的 ÷100 退回原状（docstring 承诺了归一，只做字符串支路等于没做）
    "D": dict(
        file=PD, suite="forensics",
        pair=("        return f / 100.0 if abs(f) > 1 else f\n",
              "        return f\n"),
        needles=["I9c"],
    ),
    # E：report_path 的空目录兜底退回（本模块第一条纪律就是「永不抛」）
    "E": dict(
        file=CF, suite="forensics",
        pair=("    return (dirs[0] if dirs else Path.cwd()) / CRASH_LOG_NAME\n",
              "    return log_dirs()[0] / CRASH_LOG_NAME\n"),
        needles=["B3 落点计算抛了"],
    ),
    # H：同一个形状在 enable_faulthandler 里的另一处（它算在 try 外面，连自己的兜底都绕过）
    "H": dict(
        file=CF, suite="forensics",
        pair=("    path = (dirs[0] if dirs else Path.cwd()) / FAULTHANDLER_NAME\n",
              "    path = log_dirs()[0] / FAULTHANDLER_NAME\n"),
        needles=["B3b enable_faulthandler 抛了"],
    ),
    # ── F/G：界面红线与静态门 ────────────────────────────────────────────────
    # F：横幅抢占规矩退回（抢掉需求 3/4/6 的提示是本轮最容易复发的一类越界）
    "F": dict(
        file=GUI, suite="forensics",
        pair=("            if not self.banner.isHidden():\n",
              "            if False:  # mutated\n"),
        needles=["L1 横幅被别人占着时绝不抢"],
    ),
    # G：渲染表格的取值点漏过把手——行为上这一列还是好的（company 本来就是字符串），
    #    只有静态门 J2 能报出来。这条变异存在的意义就是证明 J2 不是装饰。
    "G": dict(
        file=PD, suite="forensics",
        pair=('            self.table.setItem(row_idx, 1, QTableWidgetItem(cell_text(rec.get("company", ""))))\n',
              '            self.table.setItem(row_idx, 1, QTableWidgetItem(rec.get("company", "")))\n'),
        needles=["J2 现行代码里没有被裸 rec.get"],
    ),
    # N：入库清洗不再收口标量列——号码列给到字符串时被逐字符洗成 ['1','3','9','0']，
    #    不崩不报错，只是所有联系方式再也打不通（比崩更贵的那种）。
    "N": dict(
        file=ROOT / "b2bcollect_build" / "ingest_cleaning.py", suite="forensics",
        pair=('    if isinstance(value, (list, tuple, set, frozenset)):\n        items: List[Any] = list(value)\n',
              '    if True:\n        items: List[Any] = list(value)\n'),
        needles=["K4 单值字符串列还是一列一项", "K4b 数字标量清洗直接抛了"],
    ),
}

_BAKS = {}                      # 源文件 -> 备份路径；还原与信号处置都按这份表走


def _backup(path):
    bak = Path("/tmp") / (path.name + ".r8mut.bak")
    shutil.copy2(path, bak)
    _BAKS[path] = bak


def _restore(path):
    bak = _BAKS.get(path)
    if bak is None or not bak.exists():
        return False
    shutil.copy2(bak, path)
    return bak.read_bytes() == path.read_bytes()


def _restore_all(tag=""):
    """把所有动过的文件按备份还原，并逐字节核对（核对结果必须当众打印，不能自说自话）。"""
    bad = []
    for p, bak in _BAKS.items():
        same = _restore(p)
        print(f"    已还原 {p.name}：{same}")
        if not same:
            bad.append(p)
    if bad and tag:
        print(f"    ‼ {len(bad)} 个文件没能按备份还原，请用 git 恢复：{[p.name for p in bad]}")
    return not bad


def _on_signal(signum, _frame):
    """看门狗/Ctrl-C 也要还原：默认处置下 finally 根本不跑，交付目录会被留在变异态。"""
    print(f"\n    收到信号 {signum}，先还原再退出")
    _restore_all()
    sys.exit(128 + signum)


def _apply(mode):
    m = MUTS[mode]
    path = m["file"]
    # 支持一处与多处锚点：有的缺陷是两道冗余守卫一起扛的（本轮的钩子链成环就是），
    # 只拆一道仍然正确 —— 那种「没红」得拆干净了才算得到结论。
    pairs = m.get("pairs") or [m["pair"]]
    src = path.read_text(encoding="utf-8")
    for old, new in pairs:
        if src.count(old) != 1:
            raise AssertionError(f"变异 {mode} 的锚点不是唯一整行（命中 {src.count(old)} 处）："
                                 f"{old!r}\n——实现改过就先修夹具，别把过期锚点当结论")
        src = src.replace(old, new)
    dst = src
    if dst == path.read_text(encoding="utf-8"):
        raise AssertionError(f"变异 {mode} 没生效：等于拿原文件当变异跑，会得到一次永远绿色的假验证")
    compile(dst, str(path), "exec")
    _backup(path)
    path.write_text(dst, encoding="utf-8")


def _run_suite(name):
    script, fail_prefix, total_prefixes = SUITES[name]
    p = subprocess.run([PY, str(script)], capture_output=True, text=True)
    out = p.stdout + "\n" + p.stderr
    reds = [ln.strip()[len(fail_prefix):].strip()
            for ln in out.splitlines() if ln.strip().startswith(fail_prefix)]
    total = sum(1 for ln in out.splitlines() if ln.strip().startswith(total_prefixes))
    verdict = [ln for ln in out.splitlines()
               if any(v in ln for v in ("全部通过", "失败 ", "结果："))]
    print(f"    {name}：rc={p.returncode}  {verdict[-1].strip() if verdict else '(没有判定行)'}"
          f"  判据 {total} 条 / 红 {len(reds)} 条")
    for ln in reds[:6]:
        print(f"      红｜{ln[:120]}")
    # 「判据行一条都没吐」与「判据被杀红」是两件相反的事：N 段的子进程输出里本来就带着
    # "Traceback" 字样，只看这个词会把一次正常的红误判成「孩子早退」，所以要求它出现在
    # 判据行之外——本套件正常收尾时会打出「结果：」那一行，用它当存活凭证更实在。
    died_early = total == 0 or not verdict
    return reds, total, p.returncode, died_early


def main():
    for s in (signal.SIGTERM, signal.SIGINT):
        signal.signal(s, _on_signal)
    modes = sys.argv[1:] or (["0"] + list(MUTS))
    if modes[0] == "restore":
        for f in (CF, PD, GUI, ROOT / "b2bcollect_build" / "ingest_cleaning.py"):
            _BAKS.setdefault(f, Path("/tmp") / (f.name + ".r8mut.bak"))
        return 0 if _restore_all("手工") else 1
    bad = 0
    for mode in modes:
        if mode == "0":
            print("\n  ▶ 基线：判据必须真全绿（变异台不建立在全红上）")
            for name in SUITES:
                reds, total, rc, early = _run_suite(name)
                ok = (not reds) and rc == 0 and total > 0 and not early
                print(f"    → {'✅ 基线绿' if ok else '❌ 基线就不干净，先修它，别急着跑变异'}")
                bad += 0 if ok else 1
            continue
        if mode not in MUTS:
            print(f"未知变异 {mode}，可选：0 / " + " / ".join(MUTS))
            return 2
        m = MUTS[mode]
        print(f"\n  ▶ 变异 {mode}（{m['file'].name} → {m['suite']} 套件）")
        try:
            _apply(mode)
        except AssertionError as e:
            print(f"    ❌ {e}")
            _restore_all()
            bad += 1
            continue
        try:
            reds, total, rc, early = _run_suite(m["suite"])
            if early and not reds:
                print("    ❌ 变异不成立：套件没跑到判定就崩了，不能拿来给判据定罪")
                bad += 1
                continue
            killed = [n for n in m["needles"] if any(n in r for r in reds)]
            missed = [n for n in m["needles"] if not any(n in r for r in reds)]
            for n in killed:
                print(f"    ✅ 「{n}」被这条变异杀红")
            for n in missed:
                print(f"    ❌ 「{n}」仍然绿 → 这条判据是装饰，必须补")
            extra = [r for r in reds if not any(n in r for n in m["needles"])]
            for r in extra:
                print(f"      连带红（人工确认盯的是不是同一个子规则）：{r[:100]}")
            bad += len(missed)
        finally:
            if not _restore_all():
                bad += 1
    print("\n" + ("═" * 52))
    print("对拍通过 ✅：需求 8 每条新判据都被自己的变异杀掉" if bad == 0
          else f"对拍不通过 ❌：{bad} 处需要处理")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
