#!/usr/bin/env python3
"""batch_runner_offline_test.py —— 给「批跑的开跑前那一道现场检查」做判据。

为什么需要这一套：`debug/offline_env_probe.py` 是「这份解释器跑得动本轮全部离线套件吗」
这句话的**唯一出口**。它一旦把「不齐」读成「齐」，后面几十路套件红成一片 ModuleNotFoundError
而人只会去查业务代码——本轮（2026-11-19）就实实在这片红里查了一整轮，根因是批跑入口那道检查
只写死了 `import PyQt5`，而真正缺的是 import 链末端 `mk_phone_ocr` 里那句 `from PIL import Image`。
反过来它也有第二种死法：**把永远装不上的名字当成硬要求**（`import sip` 那句包在
`except Exception` 里，是退路本身），那会让批跑在开跑前就被挡死，谁也修不动。

所以这套判据钉的是三件事：
  A) 闭包口径算得准：什么算硬要求、什么算有退路、什么算随包代码而不是第三方依赖；
  B) 范围只有一条来路：只认 `run_offline_regression.sh --list`，且「范围断了」绝不许被读成
     「没缺依赖」（本轮那次 `--list` 退出码 1、0 行，直接把 frozen 里 `_files[0]` 崩掉的形状）；
  C) 接线真的有牙：批跑入口必须**调用**探针、必须分开接住四种退出码、必须排在 --list 之后
     （否则探针去读清单会把自己拉起来，递归）、必须带 `< /dev/null`。
「函数写了没人调」「门只印字不改退出码」这两类，本仓库已经付过两次学费。

跑法：`.venv/bin/python debug/batch_runner_offline_test.py`（在批跑清单的 FILES 里，每轮批跑都跑）。
"""
import ast
import contextlib
import io
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import offline_env_probe as P     # noqa: E402  被测对象就是同目录那份探针

_ok, _failed = 0, []


def check(label, cond, extra=""):
    global _ok
    if cond:
        _ok += 1
        print(f"  ok {label}")
    else:
        _failed.append(label)
        print(f"FAIL {label} {extra}")


_TMP = []


def _fixture(**files):
    """在临时目录里搭一座小仓库：ROOT 被探针读成 tmp，文件按 kwargs 名字写进去。

    这座仓库必须收尾拆掉：「把假数据留在开发机上」这一问靠的是逐套件快照对拍，
    而任何一层守卫都没看过「临时目录没回收」这一格。
    """
    tmp = tempfile.mkdtemp(prefix="b2b_probe_fixture_")
    _TMP.append(tmp)
    for d in ("b2bcollect_build", "riskbird_build", "ali1688_build", "debug"):
        os.makedirs(os.path.join(tmp, d), exist_ok=True)
    for name, body in files.items():
        path = os.path.join(tmp, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
    return tmp


def _req(**files):
    """搭好现场→跑闭包→返回 (require 表, 闭包文件, problems)。真仓库的 ROOT 会被还原。"""
    tmp = _fixture(**files)
    real = P.ROOT
    try:
        P.ROOT = tmp
        return P.closure([f for f in files if f.startswith("debug/test_")])
    finally:
        P.ROOT = real


class _StubGate:
    """把「读清单」那一步换成给定结果：范围那一问的四种形状都要能人造。"""

    def __init__(self, rc, files):
        self._r = (rc, files, "夹具 stderr", "", "夹具通道")

    def regression_suites(self):
        return self._r


# ── A. 闭包口径：硬要求、有退路、随包代码，各判各的 ──────────────────────
print("A. import 闭包的口径")
req, chain, prob = _req(**{
    "debug/test_chain_offline.py": "import mid_a\n",
    "b2bcollect_build/mid_a.py": "import leaf_a\n",
    "b2bcollect_build/leaf_a.py": "import third_a\n",
})
check("① 传递闭包算到链尾：套件→中间模块→叶子模块里的三方 import 必须进硬要求"
      "（本轮那只 PIL 就是这一形，只扫套件顶层永远看不见它）",
      req.get("third_a", {}).get("optional") is False, f"实得 {sorted(req)}")
check("② 闭包里的每一份随包文件都留了名（归属要能从证据读出来）",
      any("leaf_a.py" in c for c in chain) and any("mid_a.py" in c for c in chain), chain)

req, _c, _p = _req(**{
    "debug/test_late_offline.py": "def go():\n    import third_late\n",
})
check("③ 函数体内的延迟 import 也算硬要求：它一旦被调到就崩，症状与本轮那次假红完全一样"
      "（真仓库现在 10 只全部在位，所以这条不会因为死代码误伤；真误伤了走 OPTIONAL_OK 登记）",
      req.get("third_late", {}).get("optional") is False, f"实得 {req}")

# 合并规则必须**两种传入顺序都验**：closure 拿 `queue.pop()` 取任务，处理顺序是传入顺序的倒序。
# 只测一种，「最后一条说话」与「只更新一次」两类退行都能靠顺序躲过变异台——而躲过的变异会被
# 报成「判据是装饰」，那比没有判据更坏。
_MIX_HARD = "debug/test_merge_hard_offline.py"
_MIX_OPT = "debug/test_merge_opt_offline.py"
_mix = {_MIX_HARD: "import third_mix\n",
        _MIX_OPT: "try:\n    import third_mix\nexcept ImportError:\n    pass\n"}
_mix_got = []
for _order in ([_MIX_HARD, _MIX_OPT], [_MIX_OPT, _MIX_HARD]):
    _tmp = _fixture(**_mix)
    _real = P.ROOT
    try:
        P.ROOT = _tmp
        _r, _c, _p = P.closure(_order)
    finally:
        P.ROOT = _real
    _mix_got.append(_r.get("third_mix", {}).get("optional"))
check("③b 闭包**合并那一步**也按非可选优先：两处 require 同一只、只有一处写了退路就必须拦停，"
      "且与两份文件的处理顺序无关（㉑ 与 ㊳ 钉的是 required_names 那一格纯函数，"
      "这一条钉的是 closure 里 setdefault 那三行）",
      _mix_got == [False, False], f"两种顺序实得 {_mix_got}")

req, _c, _p = _req(**{
    "debug/test_opt_offline.py": "try:\n    import third_body\nexcept ImportError:\n"
                                 "    third_body = None\n",
})
check("④ 主支包在 `except ImportError` 里 → 只记录不拦停：那本来就是有退路的写法，"
      "拦停就等于把「功能降级」喊成「现场坏了」",
      req.get("third_body", {}).get("optional") is True, f"实得 {req}")

req, _c, _p = _req(**{
    "debug/test_broad_offline.py": "try:\n    import third_broad\nexcept Exception:\n    pass\n",
})
check("④b `except Exception` 也算退路（ModuleNotFoundError 是它的孙子）：现场那句裸 "
      "`import sip` 就包在这一形里，只认 ImportError 就会拿一个永远装不上的名字拦停整个批跑",
      req.get("third_broad", {}).get("optional") is True, f"实得 {req}")

req, _c, _p = _req(**{
    "debug/test_fallback_offline.py": "try:\n    from PyQt5 import sipish\n"
                                      "except Exception:\n    import third_handler\n",
})
check("⑤ except 支里的那只同样是退路（access_guard 那句裸 `import sip` 就是这一形：它在 mac 上"
      "永远装不上，当成硬要求会让批跑再也没能开跑）",
      req.get("third_handler", {}).get("optional") is True, f"实得 {req}")

req, _c, _p = _req(**{
    "debug/test_custom_offline.py": "try:\n    import third_custom\nexcept RuntimeError:\n"
                                    "    pass\n",
})
check("⑥ 只接自定义异常的 handler **不算退路**：ImportError 不会被它接住，"
      "照实算硬要求（判不准时宁可多要求一只，不许悄悄放过）",
      req.get("third_custom", {}).get("optional") is False, f"实得 {req}")

_here = "posix" if os.name == "posix" else os.name
req, _c, _p = _req(**{
    "debug/test_guard_offline.py": (
        f"if os.name == {_here!r}:\n    import third_here\n"
        "if os.name == 'win_nt':\n    import third_there\n"
        "import os\n"),
})
check("⑦ 当前平台真会走到的守卫分支里那只必须拦得住（`if os.name == 'posix'` 里的就在 mac 上跑）",
      req.get("third_here", {}).get("optional") is False, f"实得 {sorted(req)}")
check("⑧ 只在别的平台才走到的守卫分支不许拦停本机批跑（否则 mac 的 CI 会被一只永远装不上的"
      "Windows 包挡在开跑之前）",
      "third_there" not in req, f"实得 {sorted(req)}")

req, _c, _p = _req(**{
    "debug/test_union_offline.py": "import same_name\n",
    "b2bcollect_build/same_name.py": "import third_b2b\n",
    "riskbird_build/same_name.py": "import third_rb\n",
})
check("⑨ 同名跨目录的两份实现都进闭包（并集）：只取第一份的话，另一份缺的那只永远进不了现场判定",
      req.get("third_b2b", {}).get("optional") is False
      and req.get("third_rb", {}).get("optional") is False, f"实得 {sorted(req)}")

req, _c, _p = _req(**{
    "debug/test_sibling_offline.py": "import sibling_mod\n",
    "debug/sibling_mod.py": "import third_sibling\n",
})
check("⑨b 发起 import 那一份文件**自己所在的目录**（运行时的 sys.path[0]）也算随包代码："
      "debug/ 下引同级那份 scrape_solarbe_full.py 就是这一形，漏了它就被当成第三方依赖拦停",
      req.get("third_sibling", {}).get("optional") is False, f"实得 {sorted(req)}")

req, _c, _p = _req(**{
    "debug/test_pkg_offline.py": "from repo_pkg.inner import x\n",
    "repo_pkg/__init__.py": "import third_in_pkg\n",
    "repo_pkg/inner.py": "import third_inner\n",
    "notapkg/readme.txt": "这个目录没有 __init__.py\n",
})
check("⑩ 仓库目录包（有 __init__.py）不是第三方依赖，而且要跟着走进去——它的 __init__ 与"
      "被点到的那个子模块各自的三方 import 都得算（enf_clean 那一形）",
      "repo_pkg" not in req and req.get("third_in_pkg", {}).get("optional") is False
      and req.get("third_inner", {}).get("optional") is False, f"实得 {sorted(req)}")
req2, _c2, _p2 = _req(**{
    "debug/test_dirnotpkg_offline.py": "import notapkg\n",
    "notapkg/readme.txt": "只有目录、没有 __init__.py：那不是仓库包\n",
})
check("⑪ 只有同名目录、没有 __init__.py 的（dist/ 这一类）仍按第三方算——判据是「有没有"
      "__init__.py」，不是「有没有目录」（与 check_requirements_coverage 同一口径）",
      req2.get("notapkg", {}).get("optional") is False, f"实得 {sorted(req2)}")

req, _c, _p = _req(**{
    "debug/test_std_offline.py": "import json\nimport os\nfrom xml.etree import ElementTree\n"
                                 "import third_real\n",
})
check("⑫ 标准库不算第三方（否则 require 集会塞进几十只根本不用装的名字，把真缺的挤出屏幕）",
      not ({"json", "os", "xml"} & set(req)) and req.get("third_real", {}).get("optional") is False,
      f"实得 {sorted(req)}")
check("⑬ 点号名只取最上层那只包（`from xml.etree import …` 不算要求一个叫 ElementTree 的包）",
      "ElementTree" not in req, f"实得 {sorted(req)}")

req, _c, prob = _req(**{
    "debug/test_missing_offline.py": "import third_x\n",
})
missing_suites = []
P_ROOT_real = P.ROOT
try:
    P.ROOT = _fixture(**{"debug/test_absent_offline.py": "import third_y\n"})
    _r, _c, prob = P.closure(["debug/test_absent_offline.py", "debug/test_ghost.py"])
    check("⑭ 清单里这一路在磁盘上没有 → 必须点名：悄悄少算一路，症状与「跑得动」一模一样",
          any("test_ghost.py" in x for x in prob), f"实得 {prob}")
    os.makedirs(os.path.join(P.ROOT, "debug"), exist_ok=True)
    with open(os.path.join(P.ROOT, "debug", "test_broken.py"), "w", encoding="utf-8") as fh:
        fh.write("def broken(\n")
    _r, _c, prob = P.closure(["debug/test_broken.py"])
    check("⑮ 语法过不去的那一路 → 点名并报「它的依赖进不了闭包」，不许退成「没报缺 = 现场齐」",
          any("语法过不去" in x for x in prob), f"实得 {prob}")
finally:
    P.ROOT = P_ROOT_real
assert missing_suites == []


# ── B. 范围只有一条来路，且「范围断了」绝不许读成「没缺依赖」 ─────────────
print("B. 范围与四种结论")
_PROBE_PATH = os.environ.get("B2B_PROBE_SRC") or os.path.join(HERE, "offline_env_probe.py")
_src = open(_PROBE_PATH, encoding="utf-8").read()
if "B2B_PROBE_SRC" in os.environ:
    print(f"   ⑯/⑯b 取证对象：{_PROBE_PATH}（夹具副本，变异台）")
_tree = ast.parse(_src)
# 取调用面必须两种 func 都收：`os.path.isfile(...)`、`gate.regression_suites()` 的 func 是
# Attribute 而不是 Name，只读 `.id` 会在这一行 AttributeError（本轮实发）——而更糟的是「顺手
# 加个 try 把它跳过」：那等于让这条判据在残缺的调用面上跑，看起来绿、其实没取证。
_names, _attr_calls = set(), []
for _n in ast.walk(_tree):
    if not isinstance(_n, ast.Call):
        continue
    if isinstance(_n.func, ast.Name):
        _names.add(_n.func.id)
    elif isinstance(_n.func, ast.Attribute):
        _names.add(_n.func.attr)
        _attr_calls.append(_n.func.attr)
_defs = {n.name for n in ast.walk(_tree)
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
check("⑯ 范围唯一来自批跑清单：探针里没有第二套发现逻辑（不 glob、不列目录、不自己抄 FILES）",
      "regression_suites" in _names and "import glob" not in _src and "listdir" not in _src,
      f"调用面 {sorted(_names)}")
check("⑯b 而且要确认它是**调用**硬门那一份实现，不是探针自己同名重写一遍再自证："
      "判据只看「有个叫 regression_suites 的调用」的话，探针里自己定义一个同名函数就能骗过去"
      "（本轮那只 PIL 之所以能溜过去，就是因为检查的对象写在了自己要查的那句话里）",
      "regression_suites" in _attr_calls and "regression_suites" not in _defs,
      f"属性调用 {_attr_calls.count('regression_suites')} 处｜本地定义 {sorted(_defs)}")
_real_gate = P._gate
try:
    with contextlib.redirect_stdout(io.StringIO()):
        P._gate = lambda: _StubGate(1, [])
        rc1 = P.main(["--check"])
        _files1, _note1 = P.scope()
        P._gate = lambda: _StubGate(0, [])
        rc2 = P.main(["--check"])
        _files2, _note2 = P.scope()
    check("⑰ 清单退出码非零 → 「范围取不到」那一格，绝不许按 0 返回（也不许按「缺依赖」返回："
          "那不是环境缺包，是范围断了）", rc1 == P.RC_NO_SCOPE and not _files1, rc1)
    check("⑱ rc=0 但展开 0 行的孪生形状同等对待：空范围上的「现场齐」没有任何含义"
          "（本轮 frozen 里 `_files[0]` 那一崩就是被这一形喂出来的）",
          rc2 == P.RC_NO_SCOPE and not _files2, rc2)
    check("⑲ 两种「取不到」的文案都要把诊断串交出来（红得能看出修哪一层，不是「没缺就往下跑」）",
          bool(_note1) and bool(_note2) and _note1 != _note2, f"{_note1!r}｜{_note2!r}")
finally:
    P._gate = _real_gate

tmp = _fixture(**{"debug/test_probe_self.py": "import third_zzz\n"})
P.ROOT = tmp
try:
    _r, _c, _p = P.closure(["debug/test_probe_self.py"])
    names = P.required_names(_r)
    miss = P.check_here(names)
    check("⑳ 真缺的那只（当前解释器里不存在）必须被 check_here 点名，而不是「没报缺」",
          miss and miss[0][0] == "third_zzz", f"实得 {names}｜{miss}")
    check("㉑ required_names 只交硬要求：有退路的那只留在表里但不拦停（文案要分开两个数）",
          P.required_names({"a": {"who": [], "optional": False},
                           "b": {"who": [], "optional": True}}) == ["a"])
finally:
    P.ROOT = P_ROOT_real

rc, text = P.audit("/definitely/not/a/python", ["os"])
check("㉒ --audit 遇到不可执行的解释器 → 「探针起不来」那一格（不许是缺依赖，也不许是齐）",
      rc == P.RC_BROKEN, f"{rc}｜{text}")
with tempfile.TemporaryDirectory() as _tmpdir:
    # 桩必须是 **shell 脚本**而不是 `.py`：audit 拿候选解释器跑的是 `-c <探针自带的代码串>`，
    # 那份 `.py` 桩的 body 永远不会被执行——上一版拿 `die.py`/`garbage.py` 做夹具时三条全是
    # 靠「拼进去的子进程代码自己 TypeError」才“绿”的，判据在空集上跑了一轮都没发现。
    # shell 桩才能造出现场那三种真实形状：跑了但非零退出 / 输出不是约定形状 / 噪声行在前、约定行在后。
    for name, body, want, label, tag, give in (
            ("die.sh", "echo '[]'\nexit 4\n", P.RC_BROKEN,
             "候选解释器退出码非零：哪怕它 stdout 里吐了一份看着很对的表，也不许判成「齐」", "a", ["os"]),
            ("garbage.sh", "echo 这不是约定形状\n", P.RC_BROKEN,
             "体检输出不是约定形状：宁可报「问不出结论」，不许退成「齐」", "b", ["os"]),
            ("noise.sh", "echo 'warning: 前面一排噪声'\necho '[]'\n", P.RC_OK,
             "只取最后一行作约定输出：子进程在 stderr/stdout 里打警告不能把体检抹坏", "c", ["os"]),
    ):
        p = os.path.join(_tmpdir, name)
        open(p, "w", encoding="utf-8").write("#!/bin/sh\n" + body)
        os.chmod(p, 0o755)
        rc, text = P.audit(p, give)
        check(f"㉓{tag} {label}", rc == want, f"{rc}｜{text}")
    rc, text = P.audit(sys.executable, ["os", "this_module_really_does_not_exist"])
    _after = text.split("缺：", 1)[-1]
    check("㉔ --audit 报缺时点名到具体哪一只，且**只**点真缺的那只：名字走 argv 传进子进程，"
          "传丢了子进程就报空表、此后每一句「现场齐」都是空的（本轮拼字符串那一版就是这样："
          "子进程里跑的是 `json.loads([\"os\"])`，恒 TypeError）",
          rc == P.RC_MISSING and _after == "this_module_really_does_not_exist", f"{rc}｜{text}")
    rc, text = P.audit(sys.executable, [])
    check("㉔b 空名单下的「齐」必须报 0 只：闭包算空了不能伪造成一次成功的体检",
          rc == P.RC_OK and "0 只" in text, f"{rc}｜{text}")

# ── C. 接线：批跑入口真调用、分开接四种退出码、排在 --list 之后 ──────────
print("C. 批跑入口的接线（函数写了没人调＝这一路从没跑过）")
# 取证对象可以由 B2B_PROBE_SH 换掉：这个口子只为变异台存在（接线在 shell 文件里，不给副本
# 那九条判据就永远只能对真文件说「绿」，等于没有证人）。没置变量时读的必然是真入口，
# 而每轮批跑都是没置的那一种——屏幕上那一行后缀就是区分两种取证面的凭证。
_SH_PATH = os.environ.get("B2B_PROBE_SH") or os.path.join(HERE, "run_offline_regression.sh")
print(f"   取证对象：{_SH_PATH}" + ("（夹具副本，变异台）" if "B2B_PROBE_SH" in os.environ else "（真入口）"))
_sh = open(_SH_PATH, encoding="utf-8").read()
# 取证只认**剥掉整行注释后的可执行文本**，并按「调用行」逐条取。上一版拿 `_sh[:_at + 200]` 这种
# 窗口做判定，结果调用上方那段讲 `< /dev/null` 由来的注释本身就能把判据满足——一条能靠注释
# 变绿的判据就是没有判据（本轮变异台实测：把真那一句里的 `< /dev/null` 删掉，判据仍绿）。
# 同理不只剥注释还要逐行取：窗口会被“前面的无关文本”喂饱，逐行取才能钉住“每一处调用都得这样写”。
_code = "\n".join(ln for ln in _sh.splitlines() if not ln.lstrip().startswith("#"))
_call = [ln for ln in _code.splitlines() if "offline_env_probe.py --check" in ln]
_at = _code.find("offline_env_probe.py --check")
check("㉕ 批跑入口真的调了探针（可执行文本里至少有一处，不是只在注释里提了一句）", bool(_call))
check("㉖ 每一处调用都带 < /dev/null：后台起批跑、父终端被回收时 fd 0 是已关闭的描述符，"
      "python 在跑第一行之前就崩——那会被读成「现场不齐」（第 20 条 20f 同一形状）",
      bool(_call) and all("< /dev/null" in ln for ln in _call), _call)
check("㉗ 退出码必须在接管它的那一句里取（`|| probe_rc=$?`），不能下一行再读 `$?`",
      bool(_call) and all("|| probe_rc=$?" in ln for ln in _call), _call)
check("㉘ 四种结论分开接：缺依赖 / 清单取不到 / 探针起不来 各有自己的文案，"
      "且最后一条 `-ne 0` 兜住全部非零（不许「没报缺就往下跑」）",
      "-eq 2" in _code and "-eq 3" in _code and "[ $probe_rc -ne 0 ]" in _code, _call)
check("㉙ 探针调用排在 `--list` 那段**之后**：写在它前面，探针读清单就会把批跑入口再拉起来一次"
      "（自己叫自己，红话里没人看得出来）",
      0 <= _code.find('"${1:-}" = "--list"') < _at, "--list 的早退在探针调用之后")
# 本文件把「为什么不再写死 import PyQt5」的来历就写在调用上方那段注释里，拿原文做子串判定
# 就会把**历史说明**读成**现存硬编码**（绿不红全看注释措辞）。
# 真正的硬编码检查长这样的：`$PY -c "import PyQt5"`。
check("㉚ 旧的「写死 import PyQt5」那一问已经从批跑入口的**可执行文本**里拿掉了"
      "（留着就是第二套现场清单：它漏的正是 PIL 那一类藏在 import 链末端的名字）",
      "import PyQt5" not in _code, "批跑入口里还有一句硬编码依赖检查")
check("㉛ 探针与判据各就各位：判据套件在 FILES 里跑，探针在 EXCLUSIONS 里带理由不进批跑",
      "debug/batch_runner_offline_test.py" in _sh.split("EXCLUSIONS")[0]
      and "debug/offline_env_probe.py|" in _sh)
# 本轮 frozen 实发的一格：批跑入口被注入一个空清单（B2B_REGRESSION_ONLY=" "）时，原来的写法
# 要先跑探针。探针读同一份清单也得到 0 行 → 回「范围断了、问不出依赖」（退出码 2），于是那句
# 最有用的「没有任何套件被执行」永远到不了屏幕——而 frozen 认的就是这一句。这一格红了以后读成
# 「环境坏了」去补包，补多久都不绿：诊断的**层序**本身就是判据，不是排版问题。
_empty_at = _code.find("没有任何套件被执行")
_early_at = _code.find("批跑清单是空的")
check("㊷ 空清单在**开跑前、探针之前**就报出「没有任何套件被执行」（晚一步就被探针的「范围断了」接走）"
      "，且当场 exit 1（只印字不改退出码的门等于公告栏，与 ㉘ 同族）",
      0 <= _early_at < _at and 0 <= _empty_at < _at and "exit 1" in _code[_early_at:_early_at + 500],
      f"早退在 {_early_at}｜探针调用在 {_at}｜第一处「没有任何套件被执行」在 {_empty_at}")
check("㊷b 开跑前与收工后两处都要在：判的对象不同（一个是 FILES、一个是累计出的 total），"
      "删掉任一处都只剩一个证人——而 total 那一处恰好是本轮红的时候唯一还说话的那一句",
      _code.count("没有任何套件被执行") == 2, f"实得 {_code.count('没有任何套件被执行')} 处")
# 本轮自审抓出来的一格（本机实测）：`python 不存在的文件` 的退出码也是 2，而 2 在批跑入口里
# 意为「清单取不到」。探针不在提交里时，CI 上响的会是「范围断了」——修的人去查 FILES 手写表，
# 而真正缺的是那一份前置工具自己（它进了排除表、不在批跑清单里，第 24 条那份「套件都要已提交」
# 的门照不到它）。两种相反的红不许退成一个词：这一条与 ㉘ 同族，只是这一回撞的是退出码。
_guard = _code.find('-f debug/offline_env_probe.py')
check("㊸ 探针文件不在位单独一格（且排在真调用之前、当场 exit）：不许与「清单取不到」共用一个退出码 2",
      0 <= _guard < _at and "exit" in _code[_guard:_guard + 500],
      f"守卫在 {_guard}｜探针调用在 {_at}")
_lines = [ln for ln in _sh.split("EXCLUSIONS=\"")[1].split('"', 1)[0].splitlines() if ln.strip()]
check("㉜ EXCLUSIONS 每一项都必须带**自洽的**理由（不靠「同上」指上行：逐行读表的人看不出它指谁，"
      "而不写理由的排除等于把判据改成沉默）",
      all("|" in ln and len(ln.split("|", 1)[1].strip()) > 4
          and not ln.split("|", 1)[1].strip().startswith("同上") for ln in _lines),
      [ln for ln in _lines
       if "|" not in ln or len(ln.split("|", 1)[1].strip()) <= 4
       or ln.split("|", 1)[1].strip().startswith("同上")])
_guide = [ln.split("pip install -r ", 1)[1].split('"')[0].split()[0]
          for ln in _code.splitlines() if "pip install -r " in ln]
check("㉝ 入口里指路的依赖清单必须真在位（本轮抓到它指着 riskbird_build/requirements.txt，"
      "而那份文件压根不存在——照着做的人第一步就撞空）",
      bool(_guide) and all(os.path.isfile(os.path.join(P.ROOT, g)) for g in _guide),
      f"指路 = {_guide}")

# ── D. 登记与自证：不许空跑、不许留过期条目 ─────────────────────────────
print("D. 登记的形状与真实现场")
check("㉞ OPTIONAL_OK 里每项都必须带非空理由（与 EXCLUSIONS 同一纪律）",
      all(isinstance(v, str) and v.strip() for v in P.OPTIONAL_OK.values()),
      {k: v for k, v in P.OPTIONAL_OK.items() if not str(v).strip()})
_suites, _note = P.scope()
check("㉟ 真实现场：范围来自批跑清单且非空（这一句直接依赖 --list，跑通就说明接线没断）",
      len(_suites) >= 30, f"{len(_suites)} 项｜{_note}")
_reqs, _chain, _probs = P.closure(_suites)
_hard = P.required_names(_reqs)
check("㊱ 真实现场的硬要求里必须有 PyQt5 与那只引发误诊的 PIL"
      "（缺一个就说明闭包与套件分家了，「现场齐」又是空的）",
      "PyQt5" in _hard and "PIL" in _hard, f"实得 {_hard}")
check("㊲ 真实现场闭包不小：随包模块过百份、problems 为空（读不到的那一路必须当场可见）",
      len(_chain) > 100 and not _probs, f"{len(_chain)} 份｜problems={_probs}")
check("㊳ 同一只包名一处有退路、另一处没有 → 按硬要求算（非可选优先，"
      "反过来一律按「有退路」判就等于把本轮那只 PIL 放回去）",
      P.required_names({"x": {"who": [("a", "模块层")], "optional": True},
                        "y": {"who": [], "optional": False}}) == ["y"])

# ── E. 声明闭环：探针算出的范围交给交付链硬门那一问，不自己重判 ──────────
# 为什么要这一条：探针能回答「本机齐不齐」，但「齐」不等于「声明过」。CI 与新克隆的开发机
# 是拿 .github/requirements/*.txt 装环境的：闭包里有一只包没写进清单，探针在本机说绿（因为开
# 发机历史上手动装过），CI 那里就一片 ModuleNotFoundError——又是「症状与业务缺陷逐字节一样」。
# 判定本身不在这里重写：直接叫交付链硬门那一问（它手上有 import 名↔发行名的对应表与连带关系），
# 这一层只负责把**探针算出的那份范围**递给它。
print("E. 依赖声明闭环（闭包 ↔ CI 装的清单）")
REQ_FILES = (".github/requirements/b2bcollect.txt",     # b2bcollect 交付（Mac/Windows 同一份）
             ".github/requirements/riskbird.txt",       # riskbird 交付
             ".github/requirements/build-1688.txt")     # 1688 打包
_gate_mod = P._gate()
_real_req = sorted(os.path.join(".github", "requirements", n)
                   for n in os.listdir(os.path.join(P.ROOT, ".github", "requirements"))
                   if n.endswith(".txt"))
check("㊶ 上面这张手写表必须等于目录里真有的那几份：手写清单第一次加文件就失效"
      "（与探针不抄 FILES 同一个道理），所以这张表自己要有对拍——它不拿目录当范围，只防「指错了几个文件」",
      _real_req == sorted(REQ_FILES), f"目录={_real_req}｜表={sorted(REQ_FILES)}")
_decl = _gate_mod.check_requirements_coverage(requirement_files=REQ_FILES,
                                             source_files=tuple(Path(x) for x in _chain))
check("㊵ 本轮批跑真会跑到的每一份文件（import 闭包全部）用到的第三方包，都写在 CI 会 pip install "
      "的那几份清单里：缺一个名字就是「本机绿、新机器跑不动」那一族假案的下一个入口",
      not _decl, f"{len(_decl)} 处：{_decl[:2]}")
_neg_tmp = _fixture(**{"debug/probe_decl_negative.py": "import this_pkg_is_not_declared\n"})
_neg = _gate_mod.check_requirements_coverage(
    requirement_files=REQ_FILES,
    source_files=(Path(_neg_tmp, "debug", "probe_decl_negative.py"),))
check("㊵b 负夹具自证：清单里没有的那只必须被这条对拍点名——不验这一句，㊵ 的「空表」有可能"
      "只是因为「它压根没扫到东西」（能在空集上通过的判据就是没有判据）",
      bool(_neg) and any("this_pkg_is_not_declared" in x for x in _neg), _neg)

for _d in _TMP:
    shutil.rmtree(_d, ignore_errors=True)
check("㊴ 本轮搭的临时仓库全部收了：探针只读盘不写盘，而「用例把假数据留在开发机上」这一格"
      "靠的是状态文件快照，它盖不到 /var 下面这些没人认领的目录", 
      all(not os.path.isdir(_d) for _d in _TMP) and len(_TMP) >= 10,
      [d for d in _TMP if os.path.isdir(d)])

print(f"\n═══ 现场检查这一层：{_ok} 项通过 ═══")
if _failed:
    print(f"失败 {len(_failed)} 项：")
    for f in _failed:
        print(f"  - {f}")
    sys.stdout.flush()
    os._exit(1)
print(f"全部通过 ✅ ({_ok}/{_ok})")
sys.stdout.flush()
os._exit(0)
