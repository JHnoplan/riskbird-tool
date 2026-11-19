#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""需求 6 的变异复验台：把这次修复逐个退回原状，确认新判据真的会红。

为什么又开一份、不并进 debug/mutation_check_gates.py（重要，否则下一个人会来合并）：
那台装置是在**子进程内存里**重编译函数体（_patch_fn/_patch_method）。而需求 6 的判据
有一半是拿 inspect.getsource 读**磁盘上的源码文本**做 AST 分析（开机顺序、隐藏点数量、
调用点行号）。内存打桩后新函数的 __code__.co_filename 是 "<mutation>"，那些判据只会 OSError 崩掉，
报不出红——把「读源码的判据」接到「只改内存的变异台」上，得到的是一堆 rc=97 的假悬案。
所以这里按**文件**做变异：改的是真源文件，判据读到的也是同一份真文件。

三种结论必须分开，不能共用一个名字（沿用另一台的纪律）：
  ✅ 杀红     —— 声明的判据变红了：这条判据有牙
  ❌ 没牙     —— 声明的判据仍然绿：它可能是装饰，必须补
  ❌ 变异不成立 —— 套件没跑到判定（崩在判据之前）：不能拿来给判据定罪

还原保护：批跑入口的看门狗用 `timeout` 发 SIGTERM 杀进程，Python 默认处置下 finally
**不会执行**，源文件就会被留在变异态（对交付目录来说等于工作区被改坏）。所以这里显式
接管 SIGTERM/SIGINT，先逐字节还原再退出，并在每一步都把还原结果打印出来。

用法：
    python debug/mutation_check_r6.py            # 全部 7 个变异 + 基线
    python debug/mutation_check_r6.py D F        # 只跑指定的
    python debug/mutation_check_r6.py restore    # 出事后手工还原（备份仍在时）
"""
import shutil
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
GUARD = ROOT / "b2bcollect_build" / "access_guard.py"
GUI = ROOT / "b2bcollect_build" / "b2bcollect_gui.py"

# 红行格式必须按套件登记：acl 那一路打 ✅/❌，edition 那一路打 ✓/✗。合并成一条松规则
# 会把详情文本里的符号也当成判据行（另一台装置在这上面栽过，见其文件头）。
SUITES = {
    "acl": (ROOT / "b2bcollect_build" / "test_acl_levels_offline.py", "❌", ("✅", "❌")),
    "edition": (ROOT / "debug" / "edition_offline_test.py", "✗", ("✓", "✗")),
}

MUTS = {
    # ── A/B/C 打在 access_guard.py 上，验收走 acl 套件 ──────────────────────
    # A：_evaluate 里「这次没问到身份」那条独立记账失效，又落回旧的「已移出白名单」else。
    # 锚点带上首行注释是因为 `if accounts is None:` 在 _check() 里也有两处（单行锚点不唯一）。
    # 只声明 refresh_bounded 那一路的两条：prefetch 那一路另有 _check 自己的同类分支，
    # 本变异碰不到它，把那边的判据一起写进来会变成一件假的「没牙」。
    "A": dict(
        file=GUARD, suite="acl",
        pair=("        if accounts is None:\n            # 「这次没问到身份」",
              "        if False:  # mutated\n            # 「这次没问到身份」"),
        needles=["失败刷新后仍保持待确认", "失败刷新也不许谎称被移出名单"]),
    # B：单飞失效，每次都另起一轮（等价于旧的 refresh_blocking）：晚到的确认那一轮被抢掉
    "B": dict(
        file=GUARD, suite="acl",
        pair=("\n                t = self._inflight_thread if self._inflight else None\n",
              "\n                t = None\n"),
        needles=["挂在预热那一轮上接着等就能拿到名单"]),
    # C：「刚落地也算问过」失效：预热已完成时再调一次限时等待会重问一遍（慢网络上多等一拍）
    "C": dict(
        file=GUARD, suite="acl",
        pair=("\n                if (self._accounts is not None\n",
              "\n                if (False and self._accounts is not None\n"),
        needles=["同一开机窗口内重复调用不得再出网"]),
    # ── D/E/F/G 打在 b2bcollect_gui.py 上，验收走 edition 套件 ────────────────
    # D：过场等待不再排在建主窗口之前——回到「先按丐版建、再等身份」的报障原点。
    # 只把调用换成不等待的赋值，绝不删行：本轮第一版拿 4 个空格当锚点而真实行是 12 个空格，
    # count==1 照过（它匹到了行尾那一段），删完剩下的空白与下一行拼成 16 缩进，报出来的是
    # IndentationError 而不是判据红——那是「看起来红了其实没验」，变异台最坏的假案。
    "D": dict(
        file=GUI, suite="edition",
        pair=("\n            _auth_outcome, _auth_waited = _wait_auth_settled(_auth_deadline)\n",
              "\n            _auth_outcome, _auth_waited = (\"mutated\", 0.0)\n"),
        needles=["过场等待也必须在建主窗口之前"]),
    # E：建入口前先不看权限——「没权就一个字节都不留在界面树里」这条不变量被打通
    "E": dict(
        file=GUI, suite="edition",
        pair=("\n        if self.btn_pool_search is not None or not edition.pool_ui_visible():\n",
              "\n        if self.btn_pool_search is not None:\n"),
        # 声明的是 D2 那条安全边界；其余红是同一处改动打穿的旧判据，作为连带列出
        needles=["需求 6·安全边界：确认结果就是丐版时一个字节也不建"]),
    # F：退回那句只讲重启的固定文案（把当场已经建好的也说成要重启）
    "F": dict(
        file=GUI, suite="edition",
        pair=('\n        _chg_txt = "账号权限等级已变更：" + "；".join(parts) + "。"\n',
              '\n        _chg_txt = "账号权限等级已变更，多余入口已当场收起；'
              '缺失的入口需要重启工具才会补齐。"\n'),
        needles=["需求 6·说得出部分胜利", "当场补齐要说一句"]),
    # G：就地长出来时 append 到末尾，而不是插回开机那一路的位置（同一账号看到两套排列）
    "G": dict(
        file=GUI, suite="edition",
        pair=("\n        layout.insertWidget(max(0, at), pool_group)\n",
              "\n        layout.addWidget(pool_group)\n"),
        needles=["两条路径建出的排列逐项一致"]),
}

_BAKS = {}                      # 源文件 -> 备份路径；还原与信号处置都按这份表走


def _backup(path):
    bak = Path("/tmp") / (path.name + ".r6mut.bak")
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
    path, (old, new) = m["file"], m["pair"]
    src = path.read_text(encoding="utf-8")
    # 锚点必须整行唯一，且变异后仍是合法语法：否则报出来的是语法错，不是判据红
    if src.count(old) != 1:
        raise AssertionError(f"变异 {mode} 的锚点不是唯一整行（命中 {src.count(old)} 处）："
                             f"{old!r}\n——实现改过就先修夹具，别把过期锚点当结论")
    dst = src.replace(old, new)
    if dst == src:
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
    # 「判据行一条都没吐」与「判据被杀红」是两件相反的事：前者只能说明孩子走了
    died_early = ("Traceback" in out) or (total == 0)
    return reds, total, p.returncode, died_early


def main():
    for s in (signal.SIGTERM, signal.SIGINT):
        signal.signal(s, _on_signal)
    modes = sys.argv[1:] or (["0"] + list(MUTS))
    if modes[0] == "restore":
        for f in (GUARD, GUI):
            _BAKS.setdefault(f, Path("/tmp") / (f.name + ".r6mut.bak"))
        return 0 if _restore_all("手工") else 1
    bad = 0
    for mode in modes:
        if mode == "0":
            print("\n  ▶ 基线：两套判据都必须真全绿（变异台不建立在全红上）")
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
                print("      尾巴：" + " / ".join(
                    [ln for ln in reds][-1:] or ["(无红行)"]))
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
    print("对拍通过 ✅：需求 6 每条新判据都被自己的变异杀掉" if bad == 0
          else f"对拍不通过 ❌：{bad} 处需要处理")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
