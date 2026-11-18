#!/usr/bin/env python3
"""offline_env_probe.py —— 「这一份解释器跑不跑得动本轮全部离线套件」这一问的唯一出口。

由来（2026-11-19 本轮实发的误诊）：`test_frozen_delivery_offline.py` 被拿 Homebrew 的
python3 跑了一遍，那一份没装 PyQt5，第 782 行 `import b2bcollect_gui` 当场
ModuleNotFoundError：屏幕上一段 traceback、退出码 1，读起来和「某条判据真红」逐字节一样，
人顺着「红」去找业务根因，而真正坏的是**现场**。同一族症状还有一只更阴的：批跑入口那一道
解释器检查只写死了 `import PyQt5`，可 PIL 不在任何人手抄的清单里，而它是
「平行会话 → makepolo_crawler → mk_phone_ocr」这条 import 链末端真会执行的一句
`from PIL import Image`。教训是两句：**「清单里没写」不等于「跑得动」**，而
**「手抄的现场清单」第一次改名就失效**（同 `run_offline_regression.sh` 里对状态文件监视
那句「清单式的守卫第一次改名就失效」是同一个道理）。

所以这一问的答案必须由**代码自己声明的那一半**算出来：
  1) 范围 = 批跑清单本身（`run_offline_regression.sh --list`，走第 24 条那套双通道读法）。
     这里不重实现发现逻辑（DELIVERY_DIRS + FILES + EXCLUSIONS）：重实现就会和批跑分叉，
     而分叉的方向永远是「探针比批跑宽或窄」——窄了漏判，宽了红在探针自己的口径上。
  2) 依赖 = 从清单里每一份套件的 import 图**传递闭包**算出来的第三方名字。哪天新增一个
     随包模块、它顶层 import 一只新的三方包，探针第二天就自动多要求一只，不需要谁回来加名字。
  3) 判定 = 在候选解释器里对每个名字问一次 `importlib.util.find_spec`：**不执行**任何随包
     代码（不联网、不建窗口、不挂死），所以这一问才放得下开跑之前。

结论分四类，各有退出码，绝不合成一个词（合成就等于把本轮那次误诊重新造一遍）：
    0 现场齐          1 缺依赖（点名缺哪一只、被谁 require）
    2 范围取不到      清单读失败或展开为空——「齐不齐」无从回答，不许当 0
    3 探针起不来      候选解释器不可执行、超时、或源码语法过不去（读不到就是没验）
  （4 是 --explain 单独用的「这个名字不在闭包里」，不参与批跑那一句的判定。）

跑法：
    .venv/bin/python debug/offline_env_probe.py --check        # 自我体检（批跑入口用这一句）
    .venv/bin/python debug/offline_env_probe.py --audit python3  # 体检另一份解释器
    .venv/bin/python debug/offline_env_probe.py --requires     # 只打印算出来的依赖集合
    .venv/bin/python debug/offline_env_probe.py --explain PIL  # 谁把这只拖进了闭包
"""
import argparse
import ast
import importlib.util
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GATE_PATH = os.path.join(ROOT, ".github", "scripts", "check_packaging_manifest.py")
# 随包源码的三个平铺目录：模块名 = 文件名，解析规则与运行时一致（先自己所在目录）。
DELIVERY_DIRS = ("b2bcollect_build", "riskbird_build", "ali1688_build")
# 明知是可选的那几只：每一项必须带理由，空理由=没登记（与批跑清单 EXCLUSIONS 同一做法）。
# 之所以需要这张表而不是「一律拦」：随包代码里有一类 import 走的是**有退路**的写法
# （try/except ImportError 之外，还有「只在某平台上才会走到」的分支），把它们的缺席也
# 拦成「现场不齐」，CI 就会在另一台机器上被一只根本不该装的包挡住整个批跑。
OPTIONAL_OK = {}
# 只在其它平台才需要的 import（`sys.platform == "win32"` 之类）由 _guard_state 自动排除；
# 万一将来出现「守卫写得判不出来、又被排掉了」的形状，就在这里点名并写清是哪一份文件
# （闭包会把这类名字按「两支都算」保留，所以默认是宁可多要求一只，不是悄悄漏一只）。

RC_OK, RC_MISSING, RC_NO_SCOPE, RC_BROKEN, RC_NOT_FOUND = 0, 1, 2, 3, 4


# ── 1) 范围：批跑清单，同一条读法 ────────────────────────────────────────
def _gate():
    """按路径装载交付链硬门那一份模块（`regression_suites` 是清单读法的唯一实现）。"""
    if not os.path.isfile(GATE_PATH):
        raise OSError(f"硬门模块不在位：{GATE_PATH}")
    spec = importlib.util.spec_from_file_location("_pkg_manifest_gate", GATE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def scope():
    """→ (套件相对路径列表, 诊断串)。列表为空时诊断串必须能说清是哪一步断的。"""
    try:
        gate = _gate()
    except (OSError, SyntaxError, ValueError) as exc:
        return [], f"硬门模块装载失败（{type(exc).__name__}: {exc}）"
    try:
        rc, files, err, _out, chan = gate.regression_suites()
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return [], f"清单通道起不来（{type(exc).__name__}: {exc}）"
    if rc != 0 or not files:
        return [], (f"退出码 {rc}、展开 {len(files)} 行｜stderr: {err or '(空)'}｜{chan}")
    return files, f"退出码 {rc}、{len(files)} 项"


# ── 2) import 图：带「执行上下文」的走法 ─────────────────────────────────
def _tree(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return ast.parse(fh.read(), filename=path)


def _guard_state(test):
    """这条 if 在**当前平台**上走不走得到：True 走得到、False 走不到、None 判不出来。

    只认最直白的写法：`sys.platform <op> "xxx"` / `os.name <op> "xxx"`（`in (…)` 也算）。
    为什么要三态而不是布尔：交付链里有 Windows 专属的 import（本机 mac 上压根没那只包），
    一律拦就等于让 mac 的批跑被一只**永远不该装**的包挡住；反过来 `if os.name == "posix"`
    里的那只在 mac 上必须拦得住——那正是本轮 PIL 那类「跑起来才崩」的入口。判不出来时
    返回 None 并按「两支都算」处理：宁可现场多要求一只，也不可把真要执行的那支漏掉。
    """
    if not isinstance(test, ast.Compare) or len(test.ops) != 1:
        return None
    operand_names = []
    for side in [test.left] + list(test.comparators):
        if isinstance(side, ast.Attribute) and isinstance(side.value, ast.Name) \
                and side.value.id in ("sys", "os") and side.attr in ("platform", "name"):
            operand_names.append((side.value.id, side.attr))
        elif isinstance(side, ast.Tuple):
            for elt in side.elts:
                if not (isinstance(elt, ast.Constant) and isinstance(elt.value, str)):
                    return None
                operand_names.append(None)   # 占位：元组里全是字面量
    literals = []
    for side in [test.left] + list(test.comparators):
        if isinstance(side, ast.Constant) and isinstance(side.value, str):
            literals.append(side.value)
        elif isinstance(side, ast.Tuple):
            literals.extend(e.value for e in side.elts
                            if isinstance(e, ast.Constant) and isinstance(e.value, str))
    key = next((k for k in operand_names if k), None)
    if key is None or not literals:
        return None
    mine = sys.platform if key[1] == "platform" else os.name
    # 前缀同族算同一个平台（macOS 是 "darwin"、Windows 是 "win32"/"cygwin"）：写守卫的人
    # 常常只贴一个名字，判成「走不到」就会把真要跑的那条分支漏干净。
    same = any(mine == v or mine.startswith(v) or v.startswith(mine) for v in literals)
    op = test.ops[0]
    if isinstance(op, ast.In):
        return same
    if isinstance(op, ast.NotIn):
        return not same
    if isinstance(op, (ast.Eq, ast.Is)):                # 与 NotEq/IsNot 反向，两支极性相反
        return same
    if isinstance(op, (ast.NotEq, ast.IsNot)):
        return not same
    return None


def _emit(node, late, gone, opt):
    """一条 import / from-import 语句 → 它要求的名字（带三个上下文位）。"""
    out = []
    for alias in node.names:
        raw = alias.name if isinstance(node, ast.Import) else (node.module or "")
        if not raw or (isinstance(node, ast.ImportFrom) and node.level):
            continue
        out.append((raw.split(".")[0], raw, late, gone, opt))
    return out


def _walk_stmt(stmt, late, gone, opt):
    """一条语句里的 import。

    单独开这一个口子而不是直接 `_walk(stmt)`：`_walk` 是从**子节点**开始收的，把一条 Import
    本身递给它就永远收不到那个名字。本轮实实踩过这一手：If / Try 的 body 递进去之后，
    「退路里的那只被标成可选」那条判据其实是在空集上跑的，另一条因为名字写在 handler 里
    才恰好没露。一个能在空集上「通过」的判据就是没有判据。
    """
    if isinstance(stmt, (ast.Import, ast.ImportFrom)):
        return _emit(stmt, late, gone, opt)
    return _walk(stmt, late=late, gone=gone, opt=opt)


def _walk(node, late=False, gone=False, opt=False):
    """产出 (基础名, 完整点号名, 是否延迟 import, 当前平台走不到, 有退路)。

    递归自己写而不是 `ast.walk`：这里要带三个上下文——「在不在函数体里」（决定这只包是
    import 阶段就需要、还是被调到才需要）、「外面的 if 守卫在当前平台走不走得到」、「有没有
    try/except ImportError 退路」。三者都必须**沿树传递**，所以只能自己下钻。完整点号名另
    带一份，因为仓库目录包（`from enf_clean.normalize import …`）要能顺着点到那一份文件。
    """
    out = []
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.Import, ast.ImportFrom)):
            out.extend(_emit(child, late, gone, opt))
            continue                                    # import 语句里不会再有 import
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # 函数签名里的默认值在 def 执行时求值，但里面的 import 不会；整段按「延迟」处理。
            out.extend(_walk(child, late=True, gone=gone, opt=opt))
            continue
        if isinstance(child, ast.ClassDef):
            # 类体在 import 阶段就执行，所以里面的 import 算模块层（late 不变）。
            out.extend(_walk(child, late=late, gone=gone, opt=opt))
            continue
        if isinstance(child, ast.If):
            state = _guard_state(child.test)
            for stmt in child.body:
                out.extend(_walk_stmt(stmt, late, gone or (state is False), opt))
            for stmt in child.orelse:
                out.extend(_walk_stmt(stmt, late, gone or (state is True), opt))
            continue
        if isinstance(child, ast.Try):
            # 有退路的三支要分开看：
            #   · 主支：缺了会走 except，所以不拦停（mk_phone_ocr 那只 PIL 不在 try 里，照样拦得住）；
            #   · except 支：它**就是退路本身**（access_guard 那句 `import sip` 就是这一形：
            #     PyQt5≥5.12 只有 PyQt5.sip，裸 sip 压根不存在，把退路当成硬要求就是拿一个
            #     永远装不上的名字拦停整个批跑），所以也只记录不拦停。
            fallback = _catches_import_error(child)
            for stmt in child.body:
                out.extend(_walk_stmt(stmt, late, gone, opt or fallback))
            for stmt in child.orelse + child.finalbody:
                out.extend(_walk_stmt(stmt, late, gone, opt))
            for handler in child.handlers:
                out.extend(_walk(handler, late=late, gone=gone, opt=True))
            continue
        out.extend(_walk(child, late=late, gone=gone, opt=opt))
    return out


def _catches_import_error(try_node):
    """这一个 try 接得住「模块找不到」吗？

    不只是 `except ImportError`：`except Exception` / `except BaseException` / 裸 `except:`
    同样接得住（ModuleNotFoundError 是 ImportError 的子类，ImportError 又是 Exception 的子类）。
    本轮实测到的现场就是 access_guard 里那句 `import sip`——它包在 `except Exception` 里（那
    是退路本身：PyQt5≥5.12 只供 PyQt5.sip，裸 sip 永远装不上），只认 ImportError 的写法会把
    它当成硬要求，从此整个批跑被一只不存在的名字挡死。反过来只抓自定义异常的那一支接不住
    ImportError，所以那里仍按硬要求算（判不准时宁可多要求一只）。
    """
    catch_all = {"ImportError", "ModuleNotFoundError", "Exception", "BaseException"}
    for h in try_node.handlers:
        if h.type is None:                       # 裸 except:
            return True
        names = {n.id for n in ast.walk(h.type) if isinstance(n, ast.Name)}
        if names & catch_all:
            return True
    return False



# ── 3) 传递闭包：随包模块继续往里走，直到只剩三方与标准库 ─────────────────
def resolve_shipped(dotted, own_dir):
    """一个 import 名 → 仓库内真代码文件列表（空 = 不是随包代码，那就是三方或标准库）。

    四个形状都必须收，每一条都对应运行时的一个真实路径：
      · **发起 import 那一份文件自己所在的目录**：python 把 sys.path[0] 定为被运行脚本的
        目录，所以 debug/ 下的套件 import 同级那份 scrape_solarbe_full.py 是寻常事；
      · **三个交付目录的平铺同名模块**（并集，不只取第一份）：套件都自己 sys.path.insert
        包目录；同名跨目录（task_registry 这类）两份实现都进闭包，只取一份的话另一份缺的
        那只就永远进不了现场判定；
      · **仓库根的平铺脚本**：批跑入口在仓库根起跑，不少 debug 套件拉到根上的工具脚本；
      · **仓库目录包**（ROOT/enf_clean/ 这类）：enhanced_cleaner 先自己把仓库根插进
        sys.path 再 `from enf_clean.normalize import …`，它不是第三方依赖。这一条的判据是
        「有没有 __init__.py」而不是「有没有同名目录」，直接接
        `check_packaging_manifest.check_requirements_coverage` 那一句、不另定一套：同名
        目录可能只是 dist/、debug/ 这种压根不该被 import 的东西。
    """
    parts = dotted.split(".")
    head = parts[0]
    hits = []
    if len(parts) == 1 and ".." not in head:
        for d in (own_dir,) + tuple(os.path.join(ROOT, x) for x in DELIVERY_DIRS) + (ROOT,):
            p = os.path.join(d, head + ".py")
            if os.path.isfile(p):
                hits.append(p)
        if hits:
            return hits
    init = os.path.join(ROOT, head, "__init__.py")
    if os.path.isfile(init):
        hits.append(init)                        # import 一个包先跑它的 __init__.py
        sub = os.path.join(ROOT, *parts) + ".py"
        if os.path.isfile(sub):
            hits.append(sub)
        return hits
    return []


def closure(suites):
    """→ (require 表, 闭包内的随包文件, 问题列表)。

    require 表：`{三方包名: {"who": [(文件, 模块层/延迟)], "optional": bool}}`，
    拦不拦由 `required_names()` 读 `optional` 这一位。两条口径都是刻意的：
      · **并集**——同名跨目录（task_registry 这类）的两份实现都进闭包。只取第一份的话，
        另一份缺的那只就永远进不了现场判定，而「同一形状两份实现谁赢取决于 import 顺序」
        正是本仓库 SHADOWED_EXTERNAL_MODULES 那一节花整段拆过的缺陷族；
      · **非可选优先**——同一个包名在 A 处有 `except ImportError` 退路、在 B 处没有，那
        B 处缺了照样崩，所以只要有一处是硬要求就必须拦；反过来一律按「有退路」判就等于
        把本轮那只 PIL 放回去了。
    """
    stdlib = set(sys.stdlib_module_names)
    requires, problems = {}, []
    seen = set()
    queue = [os.path.join(ROOT, s) for s in suites]
    while queue:
        path = queue.pop()
        if path in seen:
            continue
        seen.add(path)
        if not os.path.isfile(path):
            problems.append(f"清单里这一路在磁盘上没有：{os.path.relpath(path, ROOT)}")
            continue
        try:
            tree = _tree(path)
        except (SyntaxError, ValueError) as exc:
            # 语法过不去 = 这一路的依赖读不到。不能「跳过它继续报齐」：那正是本轮要拦的形状
            # （读不到却被说成没问题）。
            problems.append(f"{os.path.relpath(path, ROOT)} 语法过不去，"
                            f"它的依赖进不了闭包（{type(exc).__name__}: {exc}）")
            continue
        rel = os.path.relpath(path, ROOT)
        own_dir = os.path.dirname(path)
        for base, dotted, late, unreachable, optional in _walk(tree):
            if unreachable:
                continue
            hits = resolve_shipped(dotted, own_dir)
            if hits:
                queue.extend(hits)               # 随包代码继续往深处走（PIL 就是这么出来的）
                continue
            if base in stdlib or base == "__future__":
                continue
            rec = requires.setdefault(base, {"who": [], "optional": True})
            rec["who"].append((rel, "延迟" if late else "模块层"))
            if not optional and base not in OPTIONAL_OK:
                rec["optional"] = False
    return requires, sorted(seen), problems


def required_names(requires):
    """require 表 → 真要拦停的那几只（排序后的名字）。有退路的只记录、不拦停。"""
    return sorted(n for n, rec in requires.items() if not rec["optional"])


# ── 4) 判定：在候选解释器里逐只 find_spec（不执行随包代码） ───────────────
def check_here(names):
    """在当前解释器里逐只问一遍（只问真要拦停的那几只）：→ [(名字, 缺失说明)]。"""
    miss = []
    for name in sorted(names):
        try:
            spec = importlib.util.find_spec(name)
        except (ImportError, ValueError) as exc:
            spec = None
            note = f"{type(exc).__name__}: {exc}"
        else:
            note = ""
        if spec is None:
            miss.append((name, note or "这份解释器里找不到这个名字"))
    return miss


def audit(py, names=()):
    """父侧入口：体检**另一份**解释器。→ (退出码, 报告文本)。

    体检名字由父进程算好闭包后**原样带进**子进程——不在子进程里重算，否则两份解释器可能
    算出两个闭包（版本差异、语法差异），红话就说不清是哪一层解释器的哪一块。

    名字走 **argv** 而不是拼进 `-c` 的源码文本：拼字符串那一版写的是
    `reqs = json.loads(json.dumps([...]))`，看起来对称，实际 `json.dumps` 的返回值被当作
    Python 字面量内插，子进程里跑的是 `json.loads(["os"])` → 恒 TypeError。而这一族 bug 在
    只依赖 argv 的写法下压根写不出来（不必担心名字里的引号、非 ASCII、空表）。子进程只跑这一
    段 stdlib 代码，不碰随包代码，所以拿它去体检一份什么都不装的解释器也不会先崩在 import 上。
    """
    code = (
        "import importlib.util, json, sys\n"
        "miss = []\n"
        "for n in sys.argv[1:]:\n"
        "    try:\n"
        "        spec = importlib.util.find_spec(n)\n"
        "    except (ImportError, ValueError):\n"
        "        spec = None\n"
        "    if spec is None:\n"
        "        miss.append(n)\n"
        "print(json.dumps(miss))\n"
    )
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    try:
        r = subprocess.run([py, "-c", code, *sorted(names)], capture_output=True, text=True,
                           cwd=ROOT, env=env, timeout=180, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return RC_BROKEN, f"{py} 体检超时 180s（探针自己没结果不等于没缺）"
    except OSError as exc:
        return RC_BROKEN, f"{py} 不可执行（{exc}）：这一份候选跑不动任何套件"
    if r.returncode != 0:
        return RC_BROKEN, (f"{py} 连体检脚本都没跑起来（退出码 {r.returncode}）："
                           f"{(r.stderr or '').strip()[-160:]}")
    try:
        miss = json.loads((r.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return RC_BROKEN, f"{py} 的体检输出不是约定形状：{(r.stdout or '')[:120]!r}"
    if not miss:
        return RC_OK, f"{py}：现场齐（{len(names)} 只第三方名字全部在位）"
    return RC_MISSING, f"{py} 缺：{', '.join(miss)}"


def _report(requires, names, missing, chain, problems, source):
    lines = []
    lines.append(f"范围来源：{source}")
    lines.append(f"import 闭包：随包模块 {len(chain)} 份 → 第三方硬要求 {len(names)} 只"
                 f"（另有 {len(requires) - len(names)} 只写了 ImportError 退路，只记录不拦停）")
    if missing:
        lines.append(f"❌ 这份解释器（{sys.executable}）缺 {len(missing)} 只：")
        for name, note in missing:
            who = requires.get(name, {}).get("who") or []
            seen_who = []
            for f, kind in who:
                if f not in seen_who:
                    seen_who.append(f)
            lines.append(f"   · {name}｜{note}｜被这些文件 require："
                         f"{', '.join(seen_who[:4]) or '(登记在 OPTIONAL_OK)'}"
                         + ("…（余下用 --explain 看）" if len(seen_who) > 4 else ""))
        lines.append("   这不是判据红：先把现场补齐（pip install 上面那几只），"
                     "或把 PY= 指到装了依赖的解释器。")
    else:
        lines.append(f"✅ 现场齐：{len(names)} 只第三方名字在位")
    if problems:
        lines.append(f"⚠ 闭包本身有 {len(problems)} 处读不到（这些一路的依赖没被验过，不算齐）：")
        for p in problems[:8]:
            lines.append(f"   · {p}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="离线批跑的现场探针（范围取自批跑清单）")
    ap.add_argument("--check", action="store_true", help="体检当前解释器（批跑入口用这一句）")
    ap.add_argument("--audit", metavar="PYTHON", help="体检另一份解释器")
    ap.add_argument("--requires", action="store_true", help="只打印算出来的第三方依赖集合")
    ap.add_argument("--chain", action="store_true", help="只打印 import 闭包里的随包模块")
    ap.add_argument("--explain", metavar="NAME", help="谁把这只依赖拖进了闭包")
    args = ap.parse_args(argv)

    suites, note = scope()
    if not suites:
        print(f"❌ 批跑清单取不到，探针无法回答「现场齐不齐」：{note}", file=sys.stderr)
        return RC_NO_SCOPE
    requires, chain, problems = closure(suites)
    names = required_names(requires)

    if args.requires:
        for n in names:
            print(n)
        return RC_OK if not problems else RC_BROKEN
    if args.chain:
        for p in chain:
            print(os.path.relpath(p, ROOT))
        return RC_OK if not problems else RC_BROKEN
    if args.explain:
        rec = requires.get(args.explain)
        if not rec:
            print(f"{args.explain} 不在闭包里（硬要求 {len(names)} 只，见 --requires）")
            return RC_NOT_FOUND
        for f, kind in sorted(set(rec["who"])):
            print(f"{f}\t{kind}")
        return RC_OK
    if args.audit:
        rc, text = audit(args.audit, names)
        print(text)
        return rc
    # 默认与 --check 同义：自我体检。批跑入口只需要这一种用法。
    missing = check_here(names)
    print(_report(requires, names, missing, chain, problems,
                  f"run_offline_regression.sh --list｜{note}"))
    if problems:
        return RC_BROKEN
    return RC_MISSING if missing else RC_OK


if __name__ == "__main__":
    sys.exit(main())
