#!/usr/bin/env python3
r"""handover_closure_offline_test.py —— 给「第 44 条交接闭包」做判据（含反向对照）。

为什么需要（2026-11-20 交接段）：第 44 条不是我预判出来的，是被**干净克隆**打出来的：
本机全量连续几轮 0 失败，同一份提交克隆到 /tmp/b2bclone 后第一轮红 13 路、第二轮红 1 路，
红的全是同一件事——「这台机器上恰好有」被文档和判据当成了「仓库里有」。而这类缺陷的
共同性质是**在原机器上永远不会响**：没有一条门会主动去问「你指路的这个名字随不随仓库走」。
所以本文件存在的意义和它对的那条门一样：让下一轮改动不会悄悄把牙磨掉。

四格坏形状，每格都要有夹具：
  甲  文档指路的路径不随仓库走（本机有/压根不存在 两种分开报；在忽略名单里的那种**不许**
      喊 git add——本门首跑就在 b2bcollect_build/config.json 上喊错过一次，那是催密钥入历）。
      而「在忽略名单里」这句话必须只看**规则**、不看磁盘：`ls-files --others --ignored` 只列
      本机存在的文件，干净检出里密钥文件根本不存在 → 豁免蒸发 → 本机绿、CI 红（甲6b 是这一格的
      永久证人，它验的就是旧实现必然响红的那个世界）。
  乙1 被文档点名的顶层目录整目录不在库、又不在忽略名单（= `git add -A` 的事故半径）。
  乙2 已入库 .py import 同目录未入库的兄弟（今日 0 命中，纯防回归：它坏了就是产物缺模块）。
  丙  判据被宽容的 except 咽掉（「依赖没装」与「自家文件没入库」在 ImportError 这层同形）。

夹具一律写在临时目录里（`_world`），本文件**不改动真仓库任何一份文件**；真仓库只做
两件事：① 今天 0 响（现状证人）② 覆盖面非空（防止四格退化成判空气）。

跑法：`.venv/bin/python debug/handover_closure_offline_test.py`（在批跑清单里，每轮批跑都跑）。
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / ".github" / "scripts" / "check_packaging_manifest.py"

_ok, _failed = 0, []


def check(label, cond, extra=""):
    global _ok
    if cond:
        _ok += 1
        print(f"  ok {label}")
    else:
        _failed.append(label)
        print(f"FAIL {label} {extra}")


def load_gate():
    spec = importlib.util.spec_from_file_location("_handover_gate", str(GATE))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _world:
    """把门禁看到的「仓库」换成一份临时仓库：git 三张表、文档、套件全部由入参钉死。

    为什么要还原：本文件 import 的是**同一份门禁模块**，而真仓库那两格（现状证人、
    覆盖面）就在同一个进程里跑。补丁不摘干净，后面读到的就是上一个夹具的世界。
    """

    def __init__(self, g, tmp, files, head=None, index=None, ignored=(), texts=None, suites=None,
                 real_ignored=False):
        self.g = g
        self.tmp = Path(tmp)
        # 默认世界必须是「其它三格都成立」的世界：不递文档与套件时，门禁会在
        # 「文档读不到」那一格提前返回，被测的那格根本没跑——那会伪装成一堆假红
        # （2026-11-20 本文件首跑 11 条红全是这个形状，包括「甲8 豁免只免一行」这种
        # 看上去完全不相干的条目。夹具不自洽时，红话会把人带向错误的那一侧）。
        files = dict(files)
        files.setdefault("docs/00-交接总览与验收清单.md", DOC)
        files.setdefault("debug/clean_offline_test.py", _CLEAN_SUITE)
        # `head=None` 才是「只有默认世界」；显式递一个集合（包括空集）就是夹具要的那个形状，
        # 不许被兼并——E2「HEAD 读不到」那一格只能靠**真的递空集**表达。
        self.head = set(files) if head is None else set(head)
        self.index = set(index if index is not None else files)
        self.ignored = set(ignored)
        self.real_ignored = real_ignored          # 真值=不替代豁免助手，让真实现跑
        self.suites = list(suites) if suites is not None else ["debug/clean_offline_test.py"]
        self.texts = {"docs/00-交接总览与验收清单.md": DOC} if texts is None else texts
        self._saved = {}
        for rel, body in files.items():
            p = self.tmp / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")

    def __enter__(self):
        g = self.g
        self._saved = {k: getattr(g, k) for k in
                       ("ROOT", "_tracked_sets", "_path_set", "_ignored_by_rules",
                        "in_work_tree", "regression_suites",
                        "_handover_doc_texts", "HANDOVER_POINTER_EXEMPT")}
        g.ROOT = self.tmp
        g._tracked_sets = lambda: (self.index, self.head)
        # 这条补丁必须复刻旧命令的**关键语义**：`ls-files --others --ignored` 只列磁盘上
        # 真实存在的被忽略文件。打成「无条件返回声明集」的话，夹具就比旧实现更宽容，
        # 甲6b 那类「文件不存在」的世界根本测不到（变异复验 M1 第 1 轮就是这样验空的）。
        g._path_set = lambda *a, **k: {p for p in self.ignored if (self.tmp / p).exists()}
        # 「被忽略」在这里是一份**声明**，不依赖磁盘上有没有那个文件——这正是新口径与旧口径
        # 的分界线：旧实现拿 `ls-files --others --ignored` 当答案，那份清单只列磁盘上存在的
        # 文件，于是夹具（会先把文件写到临时磁盘）与实现共用同一个错假设，甲6 首跑就绿了，
        # 而干净检出里同一个豁免直接蒸发（2026-11-20 堵死四条发版通道）。
        if self.real_ignored:
            pass    # 不动它：临时目录不是 git 工作树，真助手会在那里跑出「范围断了」那条回答
        else:
            g._ignored_by_rules = lambda paths: set(self.ignored) & set(paths)
        g.in_work_tree = lambda: True
        g.regression_suites = lambda *a, **k: (0, self.suites, "", "", "夹具")
        if self.texts is not None:
            g._handover_doc_texts = lambda: dict(self.texts)
        return self

    def __exit__(self, *exc):
        for k, v in self._saved.items():
            setattr(self.g, k, v)
        return False


g = load_gate()

DOC = "# 手册\n看 `docs/00-交接总览与验收清单.md`。\n"
# 一份「合格」的判据套件：try/except 里把异常 raise 出去，丙格不咬它。
# 它存在的世界才能测「甲/乙 那一格」，否则丙格的提前返回会捣乱所有其他夹具。
_CLEAN_SUITE = ("#!/usr/bin/env python3\nimport sys\ndef check(a, b, c):\n    pass\n"
                "try:\n    check('x', 1, 1)\nexcept Exception as e:\n    raise e\n")

print("\n═══ 甲：文档指路的路径必须随仓库走 ═══")
with _world(g, tempfile.mkdtemp(), {"docs/00-交接总览与验收清单.md": DOC,
                                    "b2bcollect_build/in_repo.py": "print(1)\n"},
            head={"docs/00-交接总览与验收清单.md", "b2bcollect_build/in_repo.py"},
            texts={"docs/02.md": "跑 `b2bcollect_build/in_repo.py`；还有 `b2bcollect_build/gone.py`"
                                 "、`b2bcollect_build/only_local.py`、`x/secret.json`；"
                                 "占位写法不算路标：`docs/…/x.py`（省略中间目录）、"
                                 "`a/*b.py`（glob）、`a/?b.py`（单字符通配）。"}):
    g.ROOT.mkdir(parents=True, exist_ok=True)
    (g.ROOT / "b2bcollect_build" / "only_local.py").write_text("print(2)\n", encoding="utf-8")
    probs = g.check_handover_closure()
    joined = "\n".join(probs)
    check("甲1 在库的指路路径不响（否则整张文档表都是噪音）",
          "in_repo.py" not in joined, probs[:2])
    check("甲2 磁盘上没有也不在库：响，且给三条修法",
          "gone.py" in joined and "死路" in joined and "HANDOVER_POINTER_EXEMPT" in joined, probs[:2])
    check("甲3 本机有但未入库：响，且喊的是 git add（换机器就是找不到文件）",
          "only_local.py" in joined and "git add b2bcollect_build/only_local.py" in joined, probs[:3])
    # 三种通配/省略形态**各自**配一个证人：实测 `docs/02-…md` 那种写法无论字符类里加不加
    # `…` 都匹配不上（它没有 `.后缀` 这一段），属于杀不死的装饰；换成 `docs/…/x.py` 才是
    # 真能区分地板的形态。一条判据里混着“看着验了、其实验不到”的样本，比少一个样本更隐蔽。
    check("甲4 占位写法（…、*、?）各自不参与判定",
          all(s not in joined for s in ("…/x.py", "*b.py", "?b.py")), probs[:4])
    check("甲5 报出处（文档名:行号），不让人拿着文件名再搜一遍",
          any("docs/02.md" in p for p in probs), probs[:2])

with _world(g, tempfile.mkdtemp(), {},
            ignored={"b2bcollect_build/config.json"},
            texts={"docs/02.md": "密钥放在 `b2bcollect_build/config.json`。"}):
    (g.ROOT / "b2bcollect_build").mkdir(parents=True, exist_ok=True)
    (g.ROOT / "b2bcollect_build" / "config.json").write_text("{}\n", encoding="utf-8")
    probs = g.check_handover_closure()
    check("甲6 在忽略名单里的=仓库已明确处置，**不许**喊 git add（密钥入历就是这门自己造的）",
          not any("config.json" in p for p in probs), probs[:2])

# 甲6b：上面那格的**干净检出版本**——路径被规则拦住、但磁盘上压根没有这个文件。
# 这才是 runner 上的真实世界（它就是因为不入库才不存在的），而旧实现只在这个世界里报红。
# 少了这一格，夹具与旧实现共用「文件在磁盘上」这个假设，本缺陷在原机器上永远不会响。
with _world(g, tempfile.mkdtemp(), {},
            ignored={"b2bcollect_build/config.json"},
            texts={"docs/02.md": "密钥放在 `b2bcollect_build/config.json`。"}):
    probs = g.check_handover_closure()
    check("甲6b 被规则忽略但磁盘上没有（=干净检出态）：依旧不许响（CI 四条通道堵死的根）",
          not any("config.json" in p for p in probs), probs[:2])
    check("甲6b-证 夹具自洽：这份世界里磁盘上确实没有该文件（否则与甲6 同形、白测）",
          not (g.ROOT / "b2bcollect_build" / "config.json").exists(), str(g.ROOT))

# 甲6c：豁免助手读不出结论（git check-ignore 报错）时，必须明说「范围断了」，
# 不许退化成「这些路径都没被忽略」——那是拿 git 故障编一屏交接缺陷。
# 这一格走**真助手**（real_ignored）：上一版这里把助手整个替成 lambda: None，于是真实现里
# `rc → None` 那条分支根本没跑过——实测把真分支改成 `return set()`，判据 34/0 全绿（假绿）。
# 临时目录不是 git 工作树，真助手在那里会跑出 128，正好把那条分支踩一遍。
with _world(g, tempfile.mkdtemp(), {}, real_ignored=True,
            texts={"docs/02.md": "密钥放在 `b2bcollect_build/config.json`。"}):
    probs = g.check_handover_closure()
    check("甲6c 真助手在 git 外跑不出结论：响「范围断了」，不是静默当合格",
          any("check-ignore 失败" in p or "范围断了" in p for p in probs), probs[:2])

# 甲6d：真助手本体——规则命中与磁盘存在性无关。这里**真的** `git init` 一份小仓库：
# 它不吃夹具的方便答案，验的是实现本体「文件不存在时还认不认得出被忽略」——
# 即本段那条 CI 事故的直接反向证人。临时目录不写那个文件，就是干净检出态。
_repo = Path(tempfile.mkdtemp())
(_repo / "sub").mkdir(parents=True, exist_ok=True)
(_repo / ".gitignore").write_text("secret.json\n", encoding="utf-8")
_init = subprocess.run(["git", "init", "-q"], cwd=str(_repo), capture_output=True, text=True)
assert _init.returncode == 0, f"夹具自己没站起来（git init 失败）：{_init.stderr[:120]}"
with _world(g, str(_repo), {}, real_ignored=True):
    check("甲6d-前提 该文件磁盘上确实不存在（否则与甲6 同形、白测）",
          not (_repo / "sub" / "secret.json").exists(), str(_repo / "sub" / "secret.json"))
    check("甲6d-1 真助手：不存在但被规则拦住的=被忽略（旧实现就是在这里蒸发成死路）",
          g._ignored_by_rules(["sub/secret.json"]) == {"sub/secret.json"},
          str(g._ignored_by_rules(["sub/secret.json"])))
    check("甲6d-2 真助手：没被规则拦住的=不被忽略（豁免不是一张大白布）",
          g._ignored_by_rules(["sub/keep.py"]) == set(), str(g._ignored_by_rules(["sub/keep.py"])))
with _world(g, tempfile.mkdtemp(), {}, real_ignored=True):   # 这份临时目录没有 `git init`
    check("甲6d-3 真助手在 git 工作树外报 None（不是空集——空集的含义是「都没被忽略」）",
          g._ignored_by_rules(["sub/secret.json"]) is None,
          repr(g._ignored_by_rules(["sub/secret.json"])))

# 甲6e：钉住本次根因的调用形式——豁免不得回到「只列磁盘上存在的文件」那份清单。
# 这是一条反向对照：谁把旧实现抄回来（哪怕是“两种都兼容”地加一行），本格就红。
with _world(g, tempfile.mkdtemp(), {}, ignored={"a/b.json"}):
    _body = Path(GATE).read_text(encoding="utf-8")
    _seg = _body[_body.index("def check_handover_closure"):]
    _cut = _seg.find("\ndef ", 60)                 # 下一个顶层 def = 本函数体的右边界
    if _cut > 0:
        _seg = _seg[:_cut]
    check("甲6e 甲/乙1 的忽略豁免只许走 _ignored_by_rules：`ls-files --others --ignored` 不许复活",
          "--others" not in _seg and _body.count("def _ignored_by_rules") == 1
          and _seg.count("_ignored_by_rules(") >= 2,
          [ln.strip()[:90] for ln in _seg.splitlines() if "ignored" in ln][:3])

with _world(g, tempfile.mkdtemp(), {},
            texts={"docs/02.md": "见 `debug/never_written.py`。"}):
    g.HANDOVER_POINTER_EXEMPT = {"debug/never_written.py": "夹具：叙事里的运行期产物"}
    check("甲7 豁免生效（登记了就不再响）", g.check_handover_closure() == [], g.check_handover_closure()[:1])
with _world(g, tempfile.mkdtemp(), {},
            texts={"docs/02.md": "见 `debug/never_written.py` 和 `debug/other.py`。"}):
    g.HANDOVER_POINTER_EXEMPT = {"debug/never_written.py": "夹具"}
    _p = g.check_handover_closure()
    check("甲8 豁免只免它自己那一行：同一份文档里的别的路标照判（否则一张表吃掉整格）",
          any("other.py" in x for x in _p), _p[:2])

print("\n═══ 乙1：被点名的顶层目录，要么在库要么被忽略 ═══")
_tmp = tempfile.mkdtemp()
with _world(g, _tmp, {"docs/07.md": "`qcollect/` 与 `backups/` 都在本机。\n"},
            head={"docs/07.md"}, texts={"docs/07.md": "`qcollect/` 与 `backups/` 都在本机。\n"}):
    for d in ("qcollect", "backups"):
        (g.ROOT / d).mkdir(parents=True, exist_ok=True)
        (g.ROOT / d / "c_1.py").write_text("print(1)\n", encoding="utf-8")
    _p = g.check_handover_closure()
    check("乙1-1 整目录不在库、又没进忽略名单：响（这是 `git add -A` 的事故半径）",
          any("qcollect/" in x for x in _p) and any("backups/" in x for x in _p), _p[:2])
    check("乙1-2 红话里带出实测份数与修法两条路（入库 / 忽略并写理由）",
          any("1 份文件" in x and "git add" in x.replace("git add -A", "") or
              "要么真入库" in x for x in _p), _p[:1])
with _world(g, tempfile.mkdtemp(), {"docs/07.md": "`qcollect/` 在本机。\n"},
            head={"docs/07.md"}, ignored={"qcollect/c_1.py"},
            texts={"docs/07.md": "`qcollect/` 在本机。\n"}):
    (g.ROOT / "qcollect").mkdir(parents=True, exist_ok=True)
    (g.ROOT / "qcollect" / "c_1.py").write_text("print(1)\n", encoding="utf-8")
    check("乙1-3 整目录被忽略=处置明确，不响（qcollect 今天的真实状态，永久证人）",
          g.check_handover_closure() == [], g.check_handover_closure()[:1])
with _world(g, tempfile.mkdtemp(), {"docs/07.md": "`riskbird_build/` 在库。\n"},
            head={"docs/07.md", "riskbird_build/a.py"},
            texts={"docs/07.md": "`riskbird_build/` 在库。\n"}):
    (g.ROOT / "riskbird_build").mkdir(parents=True, exist_ok=True)
    (g.ROOT / "riskbird_build" / "a.py").write_text("print(1)\n", encoding="utf-8")
    (g.ROOT / "riskbird_build" / "b.py").write_text("print(2)\n", encoding="utf-8")
    check("乙1-4 只判「整目录 0 份」这一种：部分在库不在本格射程（那是甲格逐个点名判的）",
          g.check_handover_closure() == [], g.check_handover_closure()[:1])

print("\n═══ 乙2：已入库源码的同目录 import 闭包 ═══")
with _world(g, tempfile.mkdtemp(),
            {"riskbird_build/main.py": "import helper\n", "riskbird_build/helper.py": "X = 1\n"},
            head={"riskbird_build/main.py"}):          # helper 在磁盘、不在 HEAD
    _p = g.check_handover_closure()
    check("乙2-1 已入库的 .py import 了未入库的兄弟：响（产物里就是缺模块）",
          any("helper" in x and "git add riskbird_build/helper.py" in x for x in _p), _p[:1])
with _world(g, tempfile.mkdtemp(),
            {"riskbird_build/main.py": "import helper\nfrom . import sibling\nimport os\n",
             "riskbird_build/helper.py": "X = 1\n", "riskbird_build/sibling.py": "Y = 2\n"},
            head={"riskbird_build/main.py", "riskbird_build/helper.py", "riskbird_build/sibling.py"}):
    check("乙2-2 兄弟在库就不响；标准库（os）不许被当成本目录模块",
          g.check_handover_closure() == [], g.check_handover_closure()[:1])
with _world(g, tempfile.mkdtemp(),
            {"riskbird_build/main.py": "import site_adapters\n", "b2bcollect_build/site_adapters.py": ""},
            head={"riskbird_build/main.py"}):
    check("乙2-3 跳目录 import 不归本条（那是第 8 条依赖链闭合的射程，两边不许互相推诿也不许重判）",
          g.check_handover_closure() == [], g.check_handover_closure()[:1])

print("\n═══ 丙：判据不许被宽容的 except 咽掉 ═══")
_HEAD = "#!/usr/bin/env python3\nimport sys\ndef check(a, b, c):\n    pass\n"
_CASES = {
    "吞（只 print）": _HEAD + "try:\n    check('x', 1, 1)\nexcept ImportError:\n    print('跳过')\n",
    "raise": _HEAD + "try:\n    check('x', 1, 1)\nexcept ImportError:\n    raise\n",
    "记账": _HEAD + "try:\n    check('x', 1, 1)\nexcept Exception as e:\n    check('依赖缺失', False, e)\n",
    "点名异常对象": _HEAD + "try:\n    check('x', 1, 1)\nexcept Exception as e:\n"
                       "    sig.emit('m', None, f'{type(e).__name__}: {e}')\n",
    "try/finally": _HEAD + "try:\n    check('x', 1, 1)\nfinally:\n    cleanup()\n",
    "显式豁免标记": _HEAD + "try:\n    check('x', 1, 1)\nexcept Exception:  # HANDOVER-OK 夹具\n    pass\n",
    "判据在嵌套函数里（不算 try 体）": _HEAD + "try:\n    def f():\n        check('x', 1, 1)\n"
                                        "except Exception:\n        print('y')\n",
}
for name, src in _CASES.items():
    with _world(g, tempfile.mkdtemp(), {"debug/s_offline_test.py": src}, head={"docs/x.md"},
                suites=["debug/s_offline_test.py"]):
        _p = g.check_handover_closure()
        swallowed = any("except" in x for x in _p)
        want = name == "吞（只 print）"
        check(f"丙·{name}：{'必须响' if want else '不许响'}", swallowed == want, _p[:1])
with _world(g, tempfile.mkdtemp(), {"debug/s_offline_test.py": _HEAD + "try:\n    check('x', 1, 1)\n"
                                "except ImportError:\n    print('跳过')\n"},
            head={"docs/x.md"}, suites=[]):
    _p = g.check_handover_closure(suites=[])
    check("丙·范围：清单交给夹具但夹具里没这份套件时不许到处乱扫（射程由入参定）",
          not any("s_offline_test.py" in x for x in _p), _p[:1])

print("\n═══ 范围与 fail-open：读不到不等于合格 ═══")
with _world(g, tempfile.mkdtemp(), {"docs/a.md": DOC}, head={"docs/a.md"},
            texts={}):
    _p = g.check_handover_closure()
    check("E1 一份交接文档都没读到：响，且不许按「没发现问题」返回",
          len(_p) == 1 and "交接文档都没读到" in _p[0], _p)
with _world(g, tempfile.mkdtemp(), {"docs/a.md": DOC}, head=set(), index=set(),
            texts={"docs/a.md": DOC}):
    _p = g.check_handover_closure()
    check("E2 有 git 工作树却读不到任何 HEAD 清单：响（范围断了）",
          any("HEAD 清单" in x for x in _p), _p[:1])
with _world(g, tempfile.mkdtemp(), {"docs/a.md": DOC}, head={"docs/a.md"}, texts={"docs/a.md": DOC},
            suites=[]):
    g.regression_suites = lambda *a, **k: (1, [], "夹具：bash 没解析出来", "", "夹具")
    _p = g.check_handover_closure()
    check("E3 批跑清单取不到：丙格要响「这一半根本没跑」，不是静默跳过",
          any("批跑清单拿不到" in x or "判据闭包" in x for x in _p), _p[:1])

print("\n═══ 真仓库现状：门在位、覆盖面非空（防四格退化成判空气）═══")
_real = g.check_handover_closure()
_texts = g._handover_doc_texts()
_ptrs = g._doc_pointers(_texts)
_dirs = g._doc_named_dirs(_texts)
_rc, _files = g.regression_suites()[:2]
check("R1 真仓库今天 0 响（这条如果红了说明门在咬自己人，先修门再谈交付）",
      _real == [], [p.replace("\n", " ")[:200] for p in _real[:3]])
check("R2 覆盖面非空：交接文档、指路路径、顶层目录、批跑清单四者都得读到了东西",
      len(_texts) >= 5 and len(_ptrs) >= 30 and len(_dirs) >= 5 and len(_files) >= 60,
      f"文档 {len(_texts)}｜指路 {len(_ptrs)}｜目录 {len(_dirs)}｜清单 {len(_files)}")
check("R3 甲的视野是「已入库的文档」：没提交的草稿不配给仓库指路",
      all(k in g._tracked_sets()[1] for k in _texts), sorted(_texts)[:4])
check("R4 CJK 边界在位：中文句子里被引号包住的路径不会从词中间切一刀（172 条假红的根）",
      not any(p.split("/")[0].startswith(("_", "与", "动")) for p in _ptrs),
      [p for p in _ptrs if p.split("/")[0][:1] in ("_", "与", "动")][:5])

print(f"\n═══ 交接闭包（第 44 条）判据：{_ok} 通过 / {len(_failed)} 失败 ═══")
if _failed:
    print("失败项：")
    for f in _failed:
        print(f"  - {f}")
    sys.stdout.flush()
    os._exit(1)
print(f"全部通过 ✅ ({_ok}/{_ok})")
sys.stdout.flush()
os._exit(0)
