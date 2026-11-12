#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打包清单硬门：构建开始前先证明「产物该有的东西，清单里真的写了」。

为什么必须有这一步（2026-11-12 的实测教训）：
  b2bcollect 的名单补采有三个平台（企知道 / 风鸟 / 爱企查），它们的采集器源码放在
  ali1688_build、riskbird_build 这两个**别的工具的项目目录**里，运行时由
  b2bcollect_build/site_adapters.py 去 sys._MEIPASS 对应子目录 import。PyInstaller
  的依赖分析只看入口可达的 import 图，而那几个 import 是运行时按路径动态发的，
  **分析器永远看不见**，只能靠 --add-data 一条条手抄。手抄的副本一共有三处
  （Windows workflow、Mac workflow、本机打包脚本），三处都漏了同一对文件
  （riskbird_crawler_v2.py / aiqicha_crawler_v2.py），于是：
    · 构建成功、产物自检通过、CI 全绿；
    · 用户拿到的包在「② 公司名单」里只有企知道一路，等级 0 的管理员也一样；
    · 症状和「档位没给这个功能」长得一模一样，第一轮排查方向直接被带去查权限。
  这类缺陷的共同点是：**少一个文件不会报错，只会少一个功能**，所以在构建前用一张
  机器可读的清单把它拦住，比事后靠用户截图发现便宜得多。

口径（谁是真源）：
  · 跨目录模块清单 = b2bcollect_build/site_adapters.py 里的 EXTERNAL_MODULES
    ——由**使用者**声明自己运行时需要什么，而不是由打包脚本各自记一遍；
  · 故意不跟着进包的传递依赖 = 同文件的 SHADOWED_EXTERNAL_MODULES（每项都要写理由）；
  · 本目录模块清单 = b2bcollect_build/*.py 扫出来的实际文件（新增模块忘了登记
    --hidden-import 正是历史上反复出现的漏项，workflow 注释里那句「维护约定」
    从今天起由这个脚本执行，不再靠人逐行比对）；
  · 三方消费本脚本：两个 workflow 的构建前步骤、离线回归测试
    b2bcollect_build/test_frozen_delivery_offline.py。

检查项（每一条都对应一次真实故障，不是随手加的规矩）：
  1 跨目录模块齐不齐、有没有落对目录；
  2 本目录新模块登记 --hidden-import 了没有；
  3 反向检查：登记了源码里已经不存在的名字；
  4 运行期数据文件在不在清单里；
  5 跨目录 .py 在**磁盘**上是否存在；
  6 改了这些目录能不能触发重打包（on.push.paths）；
  7 要进包的源码是否真的被 **git 跟踪**——2026-11-12 的第二刀：
     `riskbird_build/runtime_paths.py` 本机一切正常，CI 却报「--add-data 指向的源码
     不存在」。根因不是清单写错，是**这个文件从来没被提交过**。5 与 7 缺一不可：
     5 管「本机有没有」，7 管「检出出来有没有」。症状与第 1 条一字不差——本机绿、
     CI 红、用户拿到少一块功能的包；
  8 跨目录模块的**传递**本地依赖是否也登记了（或明确由本目录同名模块供给）：
     `ali1688_crawler` 顶层 import 的 `customer_library_cleaner` 就是这一类——它不在
     清单里、磁盘上也在、git 里也在，第 5、7 两条全都绿，包里却压根没有它。
  9 打包入口里的 shell 文案有没有 `$变量` 紧跟中文标点这种写法——2026-11-12 干跑
     NSIS 步骤时当场炸出来：bash 把全角括号的字节读进变量名，`set -u` 下失败的是那句
     「解释失败原因」的 echo，不带 `set -u` 时更坏，值被静默印成空。

用法：
  python .github/scripts/check_packaging_manifest.py        # 校验，失败退出码 1
  python .github/scripts/check_packaging_manifest.py --list  # 只打印当前清单
"""
from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[2]
ADAPTERS = ROOT / "b2bcollect_build" / "site_adapters.py"
BUILD_DIR = ROOT / "b2bcollect_build"

# 三处打包入口必须同口径：任何一处漏抄，那个平台的产物就少一块功能。
CALLERS = {
    "Windows workflow": ROOT / ".github" / "workflows" / "build-b2bcollect.yml",
    "Mac workflow": ROOT / ".github" / "workflows" / "build-b2bcollect-mac.yml",
    "本机 Mac 打包脚本": ROOT / "debug" / "build_b2bcollect_mac_local.sh",
}

# 不进包的本地模块：入口脚本自己（PyInstaller 以文件为入口，不需要 hidden-import）、
# 一次性命令行工具、已下线但源码还留着的采集站。每一项都必须写清理由——
# 「忘了登记」和「有意不登记」在症状上完全一样，只有理由能把它们分开。
NOT_PACKAGED = {
    "b2bcollect_gui": "打包入口脚本（作为位置参数传给 PyInstaller）",
    "unlock_no_contact": "一次性命令行修复工具，界面不引用",
    "gys_crawler": "已下线，不再打包（见 workflow 注释）",
    "solarbe_crawler": "已下线，不再打包（见 workflow 注释）",
}
TEST_PREFIX = "test_"

# 第三方包名（--hidden-import 里合法、但本目录没有同名 .py 的那些）。
# 这份表只用于「反向检查」：登记了一个既不是本地模块、又不在这里的名字，就说明
# 它要么是打错的模块名、要么是已删除模块的残留——两种都该在构建前说清楚。
# pip 名≠import 名的两个写法（websocket-client→websocket/websocket_client、
# beautifulsoup4→bs4、pillow→PIL）都收进来，那是刻意双写，不是笔误。
PIP_NAMES = {
    "requests", "websocket", "websocket_client", "openpyxl", "phonenumbers",
    "pandas", "xlrd", "bs4", "lxml", "PIL", "selenium", "PyQt5", "yaml",
}

# 随包必须存在的运行期数据：漏了不崩，但功能会静默降级或全员丐版。
REQUIRED_DATA = {
    "config.json": "随包凭证（缺它 = 外部机器一律精简版）",
    "mk_phone_font.json": "马可波罗图片码字形字典（缺它 = 手机号整列空白）",
    "enf_clean": "清洗子包源码目录（缺它 = enhanced_cleaner 整条链路降级）",
}

# 进包但不该被 git 跟踪的输入：构建时现生成、或 CI 现场下载。与 NOT_PACKAGED 同理，
# 每一项都要写清理由——「忘了提交」和「有意不提交」在 CI 上的症状完全一样，只有理由
# 能把它们分开。这张表不是为了放行，而是为了让第 7 条只在**真漏提交**时响：一条谁也
# 修不掉的告警会把整道门禁消耗成背景噪音，那比没有门禁更糟。
GENERATED_AT_BUILD = {
    "config.json": "Actions Secrets 现写（见 inject_build_config.py），密钥绝不入仓库",
    "chromedriver": "CI 现场下载的二进制驱动，不提交",
    "drivers": "CI 现场下载的驱动目录，不提交",
}

# 目录型 --add-data 里会混进来的本地垃圾：既不要求入库，也不该跟着进包。
JUNK_DIR_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache"}
JUNK_SUFFIXES = (".pyc", ".pyo", ".DS_Store")


def _literal(node):
    """取 ast 字面量：只认字符串与元组/列表/字典，不接受任何求值。"""
    try:
        return ast.literal_eval(node)
    except Exception:
        return None


def module_manifest() -> tuple:
    """从 site_adapters.py 的源码里读出两张表（不 import，免得把 selenium 拖进来）。"""
    tree = _adapter_tree()
    found = {}
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name) and t.id in ("EXTERNAL_MODULES",
                                                        "EXPECTED_LIST_CHANNELS"):
                    val = _literal(node.value)
                    if isinstance(val, (list, tuple)):
                        found[t.id] = tuple(str(v) for v in val)
    missing_keys = [k for k in ("EXTERNAL_MODULES", "EXPECTED_LIST_CHANNELS")
                    if k not in found]
    if missing_keys:
        raise SystemExit(f"❌ {ADAPTERS.name} 里读不到 {missing_keys}："
                         "打包清单的判据本身没了，这一步必须红")
    return found["EXTERNAL_MODULES"], found["EXPECTED_LIST_CHANNELS"]


def _adapter_tree():
    return ast.parse(ADAPTERS.read_text(encoding="utf-8"), filename=str(ADAPTERS))


def shadowed_modules() -> dict:
    """读 SHADOWED_EXTERNAL_MODULES（外部模块里**故意不跟着进包**的那几个传递依赖）。

    单独一个函数、单独一次 ast：`module_manifest()` 的返回值形状已经被三个消费方按
    位置解包了，为了加一张表去改公共形状，等于把风险从「漏一个文件」换成「漏一个调用
    点」——那正是本轮要根治的那类缺陷。表读不到时按空表处理：漏登记的依赖会由
    `check_closure` 照常报红，不会因为读不到表就整条判据静默失效。
    """
    out = {}
    for node in _adapter_tree().body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name) and t.id == "SHADOWED_EXTERNAL_MODULES":
                    val = _literal(node.value)
                    if isinstance(val, dict):
                        out = {str(k): str(v) for k, v in val.items()}
    return out


def local_modules() -> list:
    """b2bcollect_build 下应当进包的模块名（排除入口、测试与有意不打包的）。"""
    out = []
    for p in sorted(BUILD_DIR.glob("*.py")):
        name = p.stem
        if name.startswith(TEST_PREFIX) or name in NOT_PACKAGED:
            continue
        out.append(name)
    return out


def _strip_comments(text: str) -> str:
    """剔掉整行注释，只留真正会执行的参数文本。

    这一步不是洁癖：workflow 的注释里就写着「两个名字都写」「--hidden-import 覆盖
    全部本地模块」这类话，不剔掉的话扫描器会把注释里的字当成参数，报出
    「--hidden-import 里登记了『两边都显式写上。』」这种荒唐告警——而一条荒唐的告警
    会让人整条门禁都不信，那比没有门禁更糟。
    """
    kept = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        kept.append(line)
    return "\n".join(kept)


def _flag_values(text: str, flag: str) -> list:
    """扫出 ``--add-data "a;b"`` / ``--hidden-import name`` 后面的取值。

    刻意不依赖 YAML 或 shell 解析器：三处入口的写法各不相同（yml 里是一整行 bash、
    本机脚本是反斜杠续行），能稳定取到「flag 后面那个 token」就够了。
    """
    values = []
    for m in re.finditer(re.escape(flag) + r"[\s=]+", text):
        rest = text[m.end():].lstrip()
        if rest[:1] in ("\"", "'"):
            quote = rest[0]
            end = rest.find(quote, 1)
            token = rest[1:end if end > 0 else len(rest)]
        else:
            token = re.split(r"[\s\\]+", rest, maxsplit=1)[0]
        if token:
            values.append(token.strip())
    return values


def _split_add_data(token: str) -> tuple:
    """拆 --add-data 取值：Windows 用 ``;``、类 Unix 用 ``:`` 分隔源与目标。"""
    src = token.replace("\\", "/")
    for sep in (";", ":"):
        if sep in src:
            a, b = src.split(sep, 1)
            return a.strip(), b.strip()
    return src.strip(), ""


def trigger_paths(text: str) -> set:
    """取 ``on.push.paths`` 里那几条 glob（只认 ``- 'xxx'`` 形式的列表项）。

    不用 PyYAML：这一步跑在 pip 安装之前，多一个依赖就多一类「本机绿、CI 红」。
    列表里允许有整行注释（我们真的写了注释），遇到第一个非列表项就收尾。
    """
    m = re.search(r"^[ \t]*paths:[ \t]*$", text, re.M)
    if not m:
        return set()
    out = set()
    for line in text[m.end():].splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        mm = re.match(r"-\s*['\"](.+?)['\"]\s*$", s)
        if not mm:
            break
        out.add(mm.group(1))
    return out


def _is_source_add_data(src: str) -> bool:
    """这条 --add-data 带的是**仓库里的源码**（改了就该重打包），还是现场下载的产物？

    必须分开：chromedriver、drivers/ 这类二进制由 CI 步骤现下载，本地既不存在也
    永远不会有人提交，把它们算进「该触发的目录」只会得到一条谁也修不了的假告警——
    而一条修不了的告警会让整道门禁失去可信度。判据用「源码」这两个字本来的意思：
    .py 文件，或者目录里确实有 .py。
    """
    if src.endswith(".py"):
        return True
    try:
        target = (ROOT / src[3:]).resolve()
    except Exception:
        return False
    return target.is_dir() and any(target.rglob("*.py"))


def check_trigger_dirs(text: str, add_data: list) -> list:
    """跨目录进包的那些目录，必须同时出现在 workflow 的触发 paths 里。

    这是「少一个文件只少一个功能」的孪生缺陷——**产物过期**：改完
    riskbird_build/riskbird_crawler_v2.py 却不触发重打包，CI 全绿、清单全对、
    界面上报的却还是老逻辑，用户那边「明明修好了怎么还崩」。而且它的症状会被
    当成「修复没生效＝修错了」，把人带去重新排查一条已经修完的链路。
    判据取自 --add-data 自己：源码目录改了要重打包这件事，本来就是「它进了包」
    的推论，所以不另立一张表（另立一张就会和清单漂移）。
    """
    problems = []
    paths = trigger_paths(text)
    if not paths:
        return ["这个 workflow 读不到 on.push.paths（触发条件被改了？跨目录源码将无法带动重打包）"]
    need = set()
    for src, _dst in add_data:
        if not src.startswith("../"):
            continue                     # 本目录的改动天然在 b2bcollect_build/** 里
        if not _is_source_add_data(src):
            continue                     # CI 现场下载的二进制/驱动，没人提交它
        top = src[3:].split("/")[0]
        if top:
            need.add(top + "/**")
    for want in sorted(need - paths):
        problems.append(f"on.push.paths 缺 {want}：这个目录的源码会进包，改了却不会触发重新打包"
                        f"（产物永远带着旧代码，症状是「修好了但没生效」）")
    return problems


def _git(*args) -> tuple:
    """跑 git，返回 (退出码, stdout)。git 不可用时返回 (-1, "")，调用方自己决定算不算错。"""
    try:
        r = subprocess.run(["git", *args], cwd=str(ROOT),
                           capture_output=True, text=True)
    except OSError:
        return -1, ""
    return r.returncode, r.stdout.strip()


def in_work_tree() -> bool:
    rc, out = _git("rev-parse", "--is-inside-work-tree")
    return rc == 0 and out == "true"


def _path_set(cmd: list) -> set:
    """跑一条 git 列文件的命令，按 **NUL** 拆分收成路径集合（失败返回空集）。

    必须加 `-z`：git 默认把非 ASCII 的文件名八进制转义并套上引号
    （``"\\345\\220\\257\\345\\212\\250….bat"``），那种字符串和任何真实路径都对不上，
    于是每一个中文名文件都被判成「从未被 git 跟踪」。本仓库真有中文名启动脚本，离线
    回归里那条「HEAD 清单里没有反斜杠」的断言就是被抓了个正着之后补上的。
    调用方给的 cmd 不必自己带 -z，这里统一加。
    """
    rc, raw = _git_raw(*cmd, "-z")
    if rc != 0:
        return set()
    names = raw.decode("utf-8", "surrogateescape").split("\0")
    return {n for n in names if n}


def _git_raw(*args) -> tuple:
    """二进制版 _git：-z 的输出里有 NUL，text=True 会把换行处理搅进来。"""
    try:
        r = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True)
    except OSError:
        return -1, b""
    return r.returncode, r.stdout


_INDEX, _HEAD = {}, {}


def _tracked_sets() -> tuple:
    """索引里 / HEAD 提交里的文件清单，各取一次（进程内缓存，别一个文件 spawn 一次 git）。

    仓库还没有任何提交时把「提交里」等同于「索引里」：这时没有任何东西能被检出，
    照 HEAD 判会让每一个输入都报红，第一条告警就把整道门禁变成噪音。
    """
    if not _INDEX:
        index = _path_set(["ls-files"])
        head = (_path_set(["ls-tree", "-r", "--name-only", "HEAD"])
                if _git("rev-parse", "--verify", "HEAD")[0] == 0 else index)
        _INDEX.update(files=index)
        _HEAD.update(files=head)
    return _INDEX["files"], _HEAD["files"]


def _rel_posix(path: Path):
    """仓库相对路径的 **git 写法**：分隔符永远是 `/`。仓库外返回 None。

    这一行归一不是洁癖，是 2026-11-12 Windows 构建实机踩出来的：`git ls-files` 与
    `git ls-tree` 在任何平台上都输出正斜杠，而 Windows 上 `str(Path(...))` 给的是反斜杠。
    两边直接比对的结果是**每一个进包文件都被判成「从未被 git 跟踪」**——门禁在 Mac 上
    全绿、在 Windows 上把整条清单刷成红，那是门禁自己坏了，比没有门禁更糟（人会直接把
    这一步跳过）。判据跨平台可比，前提是先把说法统一到 git 那一边。
    """
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        text = str(path).replace("\\", "/")
        return None if text.startswith("/") else text


def tracked_state(path: Path) -> str:
    """`ok` 在提交里 / `staged` 只 add 没提交 / `missing` 压根没被跟踪。"""
    rel = _rel_posix(path)
    if rel is None:
        return "ok"                       # 仓库外的路径不归这条判据管
    index, head = _tracked_sets()
    if rel in head:
        return "ok"
    return "staged" if rel in index else "missing"


def is_tracked(path: Path) -> bool:
    """兼容旧调用点：在索引里就算被跟踪。判红用 `tracked_state`，它更贴近 CI 的现实。"""
    return tracked_state(path) != "missing"


def _state_hint(rel: str, state: str) -> str:
    if state == "staged":
        return (f"{rel} 已 git add 但还没提交——CI 检出的是**提交**，本机绿、构建机上没这个"
                f"文件：先 git commit {rel}")
    return (f"{rel} 从未被 git 跟踪（本机绿、CI 检出后没这个文件）："
            f"git add {rel} && git commit")


def _package_input(src: str) -> Path:
    """把 --add-data 的源换算成仓库内路径：`../x/y` → 仓库根，裸名 → 本目录。"""
    if src.startswith("../"):
        return ROOT / src[3:]
    return ROOT / "b2bcollect_build" / src


def _junk(p: Path) -> bool:
    """目录型 --add-data 里的本地垃圾：缓存、字节码、Finder 元数据。"""
    return (any(part in JUNK_DIR_NAMES for part in p.parts)
            or p.name in JUNK_DIR_NAMES
            or p.name.startswith(".")
            or p.suffix in JUNK_SUFFIXES)


# 入口文本里出现的「构建期脚本/文件」引用：这些不进 --add-data，但没有它们构建照样断。
# 用一张模式表而不是全仓库扫描，是为了只判**真的被命令用到**的那几类路径；判据仍然取自
# 入口文本自己，不另立清单（另立一份就会和入口漂移）。
REF_PATTERNS = (r"\.github/scripts/[\w.\-]+\.(?:py|sh)",
                r"b2bcollect_build/installer/[\w.\-]+",
                r"installer[/\\][\w.\-]+",
                r"debug/build_b2bcollect_mac_local\.sh")


def referenced_inputs(text: str) -> list:
    """从入口文本里扫出仓库内的构建期文件（只回报磁盘上真存在的那些）。

    同一个相对写法可能在两种基准下成立：workflow 里 `cd b2bcollect_build` 之后写的
    `installer/x.nsi`，与本目录无关的 `.github/scripts/x.py` 则从仓库根算。两种基准都试，
    命中哪一种算哪一种——只认一种的话，另一种就是「文件在、判据看不见」的静默漏检。
    Windows 的 yml 里路径还可能写成反斜杠，所以先归一再拼路径。
    """
    out = []
    for pat in REF_PATTERNS:
        for m in re.finditer(pat, text):
            rel = m.group(0).replace("\\", "/")
            for base in (ROOT, BUILD_DIR):
                p = base / rel
                if p.is_file() and p not in out:
                    out.append(p)
                    break
    return out


def check_tracked(add_data: list, text: str = "") -> list:
    """第 7 条：凡是要进包的源码与构建期脚本，必须真的被 git 跟踪。

    为什么磁盘存在不够（2026-11-12 的第二刀）：CI 的检出来自 git，工作区里没提交的文件
    在 runner 上压根不存在。`riskbird_build/runtime_paths.py` 本机写好了、清单也登记了、
    自检也绿了，`git add` 从来没执行过——于是两个平台的构建在打包前硬门那一步直接报
    「--add-data 指向的源码不存在」。这条判据抓的是**开发流程漏提交**，与清单对不对齐
    无关，任何一处写清单都躲不过它。
    """
    problems = []
    for src, _dst in add_data:
        base = _package_input(src)
        name = base.name
        if name in GENERATED_AT_BUILD:
            continue                       # 构建时现生成/CI 现下载：仓库里本来就不该有
        if not base.exists():
            continue                        # 缺失由第 5 条与 workflow 的前置检查负责报
        files = ([f for f in sorted(base.rglob("*")) if f.is_file() and not _junk(f)]
                 if base.is_dir() else [base])
        for f in files:
            state = tracked_state(f)
            if state == "ok":
                continue
            problems.append(f"{_state_hint(_rel_posix(f), state)}"
                            f"（它随 --add-data {src} 进包）")
    # 本目录模块：--hidden-import 只保证「登记了」，磁盘上有没有、提交没提交是另一件事。
    # 漏提交一个新的 b2bcollect_build/*.py 时，PyInstaller 会报一句谁也看不懂的
    # "Hidden import 'xxx' not found"，比少一个 --add-data 更难查，所以一起判。
    for name in local_modules():
        p = BUILD_DIR / f"{name}.py"
        state = tracked_state(p)
        if p.exists() and state != "ok":
            problems.append(_state_hint(_rel_posix(p), state)
                            + f"（它是本地模块 {name}，靠 --hidden-import 进包）")
    # 入口引用的构建期脚本：没有它们，连这道门禁本身都跑不起来。
    for p in referenced_inputs(text):
        state = tracked_state(p)
        if state != "ok":
            problems.append(_state_hint(_rel_posix(p), state)
                            + "（入口命令直接引用它）")
    return problems


def local_imports(path: Path) -> set:
    """一个 .py 里 import 到的**同目录**模块文件名（ast 扫，不执行、不装依赖）。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"),
                         filename=str(path))
    except (SyntaxError, ValueError, OSError):
        return set()
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                names.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[0])
    out = set()
    for n in names:
        cand = path.parent / f"{n}.py"
        if cand.exists():
            out.add(n)
    return out


def check_closure(externals: tuple, shadowed: dict) -> list:
    """第 8 条：跨目录模块的传递本地依赖也必须登记，或者明确说明由谁供给。

    为什么第 1 条不够：第 1 条只比对「使用者声明的那张表」，而这张表是人手抄的。
    `ali1688_build/ali1688_crawler.py` 顶层 `import customer_library_cleaner`——同目录
    确实有同名文件，PyInstaller 的分析器看不见（它是运行时按路径 exec 的），清单也没写，
    于是第 1、5、7 三条全绿，包里却压根没有它。这一类缺陷的成因是「依赖链」而不是
    「清单项」，所以判据必须顺着 import 走一遍，而不是等人再抄一遍。
    """
    problems = []
    known = set(externals)
    seen, queue = set(), list(externals)
    importers = {}                      # 模块名 → 谁 import 它（报错要点名来源）
    while queue:
        rel = queue.pop()
        if rel in seen:
            continue
        seen.add(rel)
        src = ROOT / rel
        if not src.exists():
            continue                    # 不存在由第 5 条报，这里不重复刷屏
        for n in sorted(local_imports(src)):
            importers.setdefault(n, rel)
            dep = f"{src.parent.relative_to(ROOT)}/{n}.py"
            if dep in known:
                if dep not in seen:
                    queue.append(dep)
                continue
            if n in shadowed:
                if not (BUILD_DIR / f"{n}.py").exists():
                    problems.append(
                        f"SHADOWED_EXTERNAL_MODULES 登记了 {n}，但 {BUILD_DIR.name}/ 没有同名"
                        f"文件可供给它（{importers[n]} 运行时 import 不到）")
                continue
            if (BUILD_DIR / f"{n}.py").exists():
                problems.append(
                    f"{rel} 的传递依赖 {dep} 没登记：本目录恰好有同名模块会顶替它，"
                    f"必须在 site_adapters.SHADOWED_EXTERNAL_MODULES 写清「谁供给、为什么"
                    f"不跟着抄」（两份同名实现同时进包时，谁赢取决于 import 顺序）")
                continue
            problems.append(
                f"{rel} 的传递依赖 {dep} 没进 EXTERNAL_MODULES：清单只登记了顶层那一个，"
                f"打包后 {importers[n]} 运行时 import 不到它，症状同样是「少一个功能」")
            known.add(dep)
            queue.append(dep)
    for n, why in sorted(shadowed.items()):
        if any(x.endswith(f"/{n}.py") for x in known):
            problems.append(
                f"SHADOWED_EXTERNAL_MODULES 里的 {n} 同时也在 EXTERNAL_MODULES："
                f"两份同名实现一起进包，谁生效取决于 import 顺序（理由：{why}）")
    return problems


VAR_BEFORE_CJK = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)[^\x00-\x7F]")


def check_shell_hygiene(text: str) -> list:
    """第 9 条：shell 里 `$VAR` 后面紧跟中文（或任何非 ASCII）必须写成 `${VAR}`。

    这不是风格问题，是 2026-11-12 拿假 makensis 干跑新加的 NSIS 步骤时当场撞出来的：
    `echo "· 安装包编译器：$MK（$VOUT，sha256 已校验）"` 在 bash 里把变量名一路读到
    全角括号／全角逗号那几个字节里去，于是
      · 带 `set -u` 的步骤直接 `MK\uff1a: unbound variable` 退出——**失败的是那句解释
        失败原因的 echo**，日志里只剩一句谁也看不懂的 unbound variable；
      · 不带 `set -u` 的步骤更坏：静默把值印成空，「退出码 ）」「编译器：（）」，
        远程排障时最该看见的那个数字凭空消失。
    中文标点在这条链路上到处都是（本仓库所有 ::error:: 文案都是中文），所以判据必须
    机械化，而不是靠人记住「加花括号」。三个打包入口一起判：workflow 与本机脚本同规矩。
    """
    problems = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue                       # 注释里的 `$VAR（` 不会被执行
        for m in VAR_BEFORE_CJK.finditer(line):
            name, punct = m.group(1), m.group(0)[-1]
            problems.append(
                f"第 {lineno} 行的 ${name} 后面紧跟「{punct}」：shell 会把这些非 ASCII "
                f"字节一起读进变量名（带 set -u 时 unbound variable 当场炸，不带时把值"
                f"印成空），必须写成 ${{{name}}}「{punct}」")
    return problems


def check_caller(label: str, text: str, externals: tuple, modules: list,
                 with_triggers: bool = False) -> list:
    """单个打包入口的体检，返回问题清单（空 = 合格）。"""
    problems = []
    text = _strip_comments(text)
    add_data = [_split_add_data(t) for t in _flag_values(text, "--add-data")]
    hidden = set(_flag_values(text, "--hidden-import"))

    # 1) 跨目录模块：site_adapters 运行时要找的每一个 .py 都必须在清单里，
    #    而且**必须落到它被 import 的那个目录**。路径写法允许 "../riskbird_build/x.py"
    #    或 "riskbird_build/x.py"，判据是尾巴对上；但只比对 src 是不够的——目标目录
    #    写错（比如整包平铺进 "."）文件确实在包里，运行时按 _MEIPASS/<子目录>/x.py
    #    照样找不到，症状与「压根没打」一字不差，同样是静默少功能。
    covered = {}
    for src, dst in add_data:
        covered.setdefault(src, set()).add(dst)
    for rel in externals:
        hits = [(s, d) for s, ds in covered.items()
                if (s == rel or s.endswith("/" + rel)) for d in ds]
        if not hits:
            problems.append(f"--add-data 缺跨目录模块 {rel}"
                            f"（site_adapters 运行时要按这个路径 import 它）")
            continue
        want = Path(rel).parent.as_posix()      # ali1688_build/x.py → ali1688_build
        if not any(d == want or d.endswith("/" + want) for _s, d in hits):
            problems.append(
                f"--add-data 里 {rel} 的目标目录不是 {want}"
                f"（实际 {' / '.join(sorted({d for _s, d in hits}))}）："
                f"文件进了包但落错地方，运行时按 _MEIPASS/{want}/ 仍然 import 不到")

    # 2) 本目录模块：新增一个 .py 却没登记 --hidden-import 是最常见的一类漏项
    for name in modules:
        base = name.split(".")[0]
        if name not in hidden and base not in hidden:
            problems.append(f"--hidden-import 缺本地模块 {name}")

    # 3) 反向检查：登记了源码里已经不存在的**本地**模块（下线站残留也算）。
    #    只判本地名：pip 包名与 enf_clean.* 这类子模块不在本目录，不该被误报。
    stale = sorted(n for n in hidden
                   if "." not in n
                   and n not in PIP_NAMES
                   and not (BUILD_DIR / f"{n}.py").exists())
    for name in stale:
        problems.append(f"--hidden-import 里登记了 {name}，但 b2bcollect_build/ 没有这个文件")

    # 4) 运行期数据文件
    dst_names = {Path(src).name for src, _ in add_data} | {src for src, _ in add_data}
    for key, why in REQUIRED_DATA.items():
        if not any(d == key or d.endswith("/" + key) or Path(d).name == key
                   for d in dst_names):
            problems.append(f"--add-data 缺运行期数据 {key}：{why}")

    # 5) 跨目录的 .py 源码在仓库里得真的存在（改名/删除会在这里现形，而不是等到
    #    用户发现界面上少一个平台）。驱动目录那类由 CI 现场下载，本地没有属正常，
    #    把它们混进来只会得到一条永远说不清的假告警。
    for src, _dst in add_data:
        if not src.startswith("../") or not src.endswith(".py"):
            continue
        if not (ROOT / src[3:]).exists():
            problems.append(f"--add-data 指向的源码不存在：{src}")

    # 6) 只有 workflow 才有触发条件：跨目录源码进了包，就必须能带动重打包。
    if with_triggers:
        problems += check_trigger_dirs(text, add_data)

    # 7) 磁盘上有、仓库里没有：本机全绿、CI 检出后一堆文件不存在（见函数文档）。
    #    非 git 工作区（源码包直构）跳过，但要明确说一声——静默跳过等于这条判据
    #    在那些环境下凭空消失，下个人无从判断它是「过了」还是「没跑」。
    if in_work_tree():
        problems += check_tracked(add_data, text)
    else:
        print(f"   ℹ {label}：当前不是 git 工作区，跳过「必须被 git 跟踪」这条")

    # 9) shell 文案里的变量写法：中文标点紧跟变量名时，坏的是**报错那一行本身**
    #    （见函数文档：一处 unbound variable 直接把失败原因替换成另一句失败）。
    problems += check_shell_hygiene(text)
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="b2bcollect 打包清单硬门（构建前）")
    ap.add_argument("--list", action="store_true", help="只打印当前清单与判定，不改退出码语义")
    args = ap.parse_args(argv)

    externals, expected_channels = module_manifest()
    modules = local_modules()

    print(f"跨目录模块（site_adapters.EXTERNAL_MODULES，{len(externals)} 项）：")
    for rel in externals:
        print(f"   · {rel}")
    print(f"名单通道应有路数（EXPECTED_LIST_CHANNELS）：{len(expected_channels)} 路 "
          f"＝ {' / '.join(expected_channels)}")
    print(f"本目录应进包模块：{len(modules)} 个"
          f"（另排除 {len(NOT_PACKAGED)} 个入口/一次性/已下线模块）")

    # 先验清单本身：链没收齐的话，三处入口抄得再一致也是错的（第 8 条）。
    bad = 0
    closure = check_closure(externals, shadowed_modules())
    if closure:
        bad += 1
        print("❌ 跨目录模块的依赖链不闭合（清单少登记，三处入口一致也没用）：")
        for p in closure:
            print(f"   · {p}")
            print(f"::error::跨目录依赖链：{p}")

    for label, path in CALLERS.items():
        if not path.exists():
            print(f"❌ {label}：找不到文件 {path.relative_to(ROOT)}")
            bad += 1
            continue
        problems = check_caller(label, path.read_text(encoding="utf-8", errors="replace"),
                                 externals, modules,
                                 with_triggers=path.name.endswith(".yml"))
        if problems:
            bad += 1
            print(f"❌ {label}（{path.relative_to(ROOT)}）打包清单不合格：")
            for p in problems:
                print(f"   · {p}")
                if path.name.endswith(".yml"):
                    print(f"::error::{label} 打包清单：{p}")
        else:
            extra = "，触发目录覆盖跨目录源码" if path.name.endswith(".yml") else ""
            print(f"✅ {label}：跨目录模块、本地模块、运行期数据、git 跟踪四项齐全{extra}")

    if bad:
        print(f"\n❌ 打包清单硬门：{bad} 处不合格。少一个文件不会报错、只会少一个"
              f"功能，所以这一步不放行就不许开建。")
        return 1
    print("\n✅ 打包清单硬门：三处入口与 site_adapters 的清单一致，"
          "依赖链闭合、进包源码都已在提交里")
    return 0


if __name__ == "__main__":
    sys.exit(main())
