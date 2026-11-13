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
     CI 红、用户拿到少一块功能的包。同一头还差最后一格：文件**在提交里、工作区却改过
     没提交**——这时 5 与 7 都绿，可本机验证的是工作区那份字节，CI 检出并打包的是提交
     那份，「回归全绿」对交付没有担保（判据量的是不会被交付的内容，比缺一块文件更难查）；
  8 跨目录模块的**传递**本地依赖是否也登记了（或明确由本目录同名模块供给）：
     `ali1688_crawler` 顶层 import 的 `customer_library_cleaner` 就是这一类——它不在
     清单里、磁盘上也在、git 里也在，第 5、7 两条全都绿，包里却压根没有它。
  9 打包入口里的 shell 文案有没有 `$变量` 紧跟中文标点这种写法——2026-11-12 干跑
     NSIS 步骤时当场炸出来：bash 把全角括号的字节读进变量名，`set -u` 下失败的是那句
     「解释失败原因」的 echo，不带 `set -u` 时更坏，值被静默印成空。判据同时覆盖交付链
     上另外几份手写脚本（HYGIENE_SCRIPTS：派发云打包、取制品、批跑回归），因为它们出错
     的代价是「CI/本机排障时看不见真数字」，和打包入口同一类。
 10 跨目录**同名模块**的几份拷贝是否字节一致（不一致就必须在 DIVERGENT_VENDORED
     里登记理由）：`import yzd_client` 拿到的是哪一份由 sys.path 顺序决定，内容分叉
     等于「按加载顺序挑一份实现」。本轮实测到的正是没登记的代价——两处上传侧修复
     只落在 riskbird 那份 yzd_client 里，产物生效的却是 b2bcollect 那份旧实现。
 11 交付链上每一段手写 shell 能不能被 **bash 自己的解析器**读懂（bash -n，只解析不
     执行）：第 9 条那种正则只能覆盖「已经想到的写法」。本轮实测的那条是
     `${APP_VERSION:-（空）」`——右花括号前是全角标点时 bash 3.2 直接解析失败，整步
     在 `set -e`、判据、日志统统出场之前就死在语法错误上，症状与「机器没配好」一模一样。
     判据交给解析器，覆盖面就不依赖有人记得住哪几个字节会带跑词法扫描。
 12 交付链上的 Python 脚本（.github/scripts/*.py，Windows runner 上也会执行的那批）与
     子进程/控制台之间的编码是不是**由脚本自己写定**：第 11 条上线当天就在云端炸出这条——
     `subprocess.run(..., input=<str>, text=True)` 在本机（UTF-8）全绿，上了 Windows
     runner 在**写 stdin 那一刻**抛 UnicodeEncodeError（'charmap'，即控制台代码页 cp1252），
     构建前硬门自己炸在打包之前。同一族的另一半是往控制台写中文：本机复现不出来，症状却
     与「脚本内容有问题」一模一样。凡是「按环境决定编码」的地方，换一台镜像就换一种结果。
 13 Windows **装配清单**（package_win_artifacts.REQUIRED_INTERNAL）与 Windows 构建命令里的
     --add-data 双向对拍：第 1 条的方向是「运行时需要什么 → 构建命令抄了没有」，管不到
     反向那一半——构建命令明明把文件放进了包，装配校验却不知道它存在，于是它哪天没下载
     成功、被装配步骤剔掉、布局改了名，check_layout 一声不响、安装包照样出。本轮实测漏
     登记的是 block_guard.py / chrome_driver.py / drivers（内置 chromedriver 多版本目录）。
 14 交付链 shell 里凡是**按外部工具的文本输出取值**的地方——`grep "模式" "$某文件`（读别人
     落下的文件）或 `某工具 … | grep -oE "模式"`（读别人的管道）——近旁必须写明那批字节的
     出处：一次真实运行的 run/job 号、原字节样本、「实测」，或一句「格式依据＝这个取值不
     依赖那个工具的话术」。2026-11-13 云端那次红不是编译器坏、也不是判据逻辑坏，是判据按
     **想象中的** makensis 横幅格式取版本——而本机同一套测试全绿，因为仿真的假编译器印的
     正是那句假话。格式无穷，猜是猜不完的；能落地的纪律是「猜过一次就要留下证据，改的人
     先看见证据」。自家 `printf`/`echo` 搬进 grep 的那些不算：那是同仓自己印的文案，改了
     打印那一行当场就红，不需要出处（把它也算进来只会逼人写假出处）。
 15 set -e（含 Actions bash 步的默认值）生效的那一段里，「正常情形也会返回非零」的命令
     （pgrep 没匹配、grep 没命中、ls/cat/find 没这个文件、command -v 查不到）不许裸着当
     赋值来源 `VAR=$(…)`：裸赋值把非零原样交给 set -e，脚本从那一行起**静默结束**，后面的
     门一道也不会跑。2026-11-13 本机实发——Mac 打包脚本第 6 道门（真双击验窗）就是这么安静地
     没执行，日志停第 5 步 ✅ 之后一行不剩；而它此前一直「绿」，只因机器上恰好留着一枚旧实例
     让 pgrep 返回 0。修法要么 `|| true`，要么 `|| { echo 为什么停; exit 1; }`；同一天脚本
     还加了 ERR 陷阱，让这一类退出至少留下一行行号（判据与夹具见离线套件 Z 节）。同一族的
     另一半是「装了 ERR 陷阱却没开 -E（errtrace）」：实测函数与命令替换里的非零根本不走陷阱，
     脚本看上去像处理过了，出事时照样一行不剩——所以看见 `trap … ERR` 就同时要到 -E。
     第三半是 `set +e … set -e` 那种**整段关保护**的写法：区间里后来人插一条命令，它失败了
     没人知道，而区间看着像「已经开回来了」。接管一条命令的退出码从来不需要关整段
     （`cmd || rc=$?`；管道要首段用 `… | tee "$LOG" || rc=${PIPESTATUS[0]}`，实测成立）。
 16 入库路径两两在**大小写·Unicode 变体不敏感**的文件系统（macOS/Windows 默认）里不许撞
     成同一个文件。2026-11-13 本机实发，而且先红在测试自己身上：离线夹具写成
     `zz_errexit_trap_noe.sh` / `zz_errexit_trap_noE.sh`，本意两份，mac 上是一份——后写的
     静默盖掉先写的，判据读出的是隔壁那份内容，红了一条不相干的话；Linux CI 大小写敏感，
     同一份用例在云端全绿。同一形状落到交付上更贵：`Foo.py` 与 `foo.py` 同时入库，Windows
     检出的那棵树里只有一个，装进包的就是「最后被写入的那一份」，而 CI 永远不会为此变红。
 17 交付 zip 的**门本身**在不在链路上（结构判据，逐个接线点拔一次验证）：字节级那次
     （绿色版 zip 条目名必须 ASCII，根因是中央目录 create_system 那一字节）定下的教训是
     「构建全绿、包照样解不开」。判据有四路：条目名门 + 目标系统真解一次门都在；生产者打完
     zip 立刻调用、结果并进 problems；Mac workflow 也带 --os-extract；门里「这台机器没有该
     工具」必须是问题而不是打印。接线一旦断了，日志照样打 ✅——所以这一条判的是链路，
     不是字节（字节那一路在 check_release_zip.py 与离线套件 G 节里各验一次）。
 18 pip 安装不许挂在一个外部门口：2026-11-13 云端两个 run 同时红在
     `pip install <包名…> -i <单一镜像>` 那一行（清华源 5 次 ReadTimeout，pip 只留一句
     `No matching distribution found`，看日志的人先怀疑依赖写错了）。冗余与有界收进
     .github/scripts/pip_install.py：多源顺序回退、每源有界超时、全挂才非零、逐源点名它
     说了什么；包名清单收进 .github/requirements/*.txt，Mac 与 Windows 读同一份文件
     （此前靠注释「逐行对齐」人肉同步）。18e 是同一族的另一半：清单收成一份文件后「抄漏
     一个包」的位置变了、症状没变，所以进包源码 import 到的每个第三方包都必须能在清单里
     找到（pip 名≠import 名、以及由清单里某发行包连带提供的，都要在案上写明凭什么）。
 19 注释里指路的「某套件 第 N 节」必须真的走得通。2026-11-13 本机自审：把「择路不许跟着
     本机历史走」写进 tool_config / runtime_paths 时，两边都写了「对拍判据见某套件第 10~13
     节」，而那文件当时只编到第 9 节——一共 13 处这样的指路，全部指向从没写出来的小节，其
     中四个函数（页尾拼法、联系方式判据、补采上限、收尾取真身）在全部离线套件里 0 覆盖，
     正是用户报「以前能采到、现在一直是 0」那条链上的守卫缺口。「判据在哪一节」这句话本身
     不构成判据：可达性必须机械化核对（被指路的套件在不在交付目录、它的节是不是数字编号、
     每一个节号——含并列与区间两种写法——在不在案），改编号时两边一起改，与第 10 条同族。

用法：
  python .github/scripts/check_packaging_manifest.py        # 校验，失败退出码 1
  python .github/scripts/check_packaging_manifest.py --list  # 只打印当前清单
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path, PurePosixPath

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

# 打包入口：NOT_PACKAGED 记的是「不进 hidden-import 那张表」，而入口脚本本身确实是
# PyInstaller 的位置参数、确实进包。第三方 import 对拍（第 18 条 e 路）要把它们算进
# 「进包源码」，所以这里单独列一份——它不在位时报红，不静默少扫一个文件。
ENTRY_SCRIPTS = ("b2bcollect_gui",)

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
    """跑 git，返回 (退出码, stdout)。git 不可用时返回 (-1, "")，调用方自己决定算不算错。

    encoding 必须写死：Windows runner 上 text=True 若不指定编码，Python 按**环境代码页**
    解码（英文镜像 cp1252）。这里取的都是 rev-parse 一类 ASCII 输出，看着没事，可只要
    有人把 `core.quotePath` 关掉（中文文件名不再八进制转义），同一句就会在解码那一刻抛
    UnicodeDecodeError——门禁的结论被这条异常盖掉，症状与「仓库坏了」一模一样。
    """
    try:
        r = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
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


_INDEX, _HEAD, _DIRTY = {}, {}, {}


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


def _dirty_paths() -> set:
    """工作区（含索引）与 HEAD 内容不一致的**已跟踪**文件，进程内取一次。

    为什么第 7 条要区分「在 HEAD 里，但被改过」：CI 检出的是**提交**，本机回归跑的是
    **工作区**。只改不提交时，老判据看它在 HEAD 里就放行，于是「本机 713 条断言全绿」
    对交付的那点担保当场断掉——打出来的包用的是旧内容，缺陷要等用户点开才被发现。
    这一条把「验证过的内容」与「交付的内容」钉成同一份，等价于大厂 CI 里的
    clean-checkout 前提。仓库还没有提交、或 git 压根不可用时退回空集——宁可不判，
    也不在没把握的地方误报（第一条误报就会让人开始跳过这一步）。
    """
    if "files" not in _DIRTY:
        head_ok = _git("rev-parse", "--verify", "HEAD")[0] == 0
        _DIRTY["files"] = _path_set(["diff", "--name-only", "HEAD"]) if head_ok else set()
    return _DIRTY["files"]


def tracked_state(path: Path) -> str:
    """`ok` 在提交里 / `staged` 只 add 没提交 / `missing` 压根没被跟踪 / `dirty` 在提交里但改过没提交。"""
    rel = _rel_posix(path)
    if rel is None:
        return "ok"                       # 仓库外的路径不归这条判据管
    index, head = _tracked_sets()
    if rel in head:
        return "dirty" if rel in _dirty_paths() else "ok"
    return "staged" if rel in index else "missing"


def is_tracked(path: Path) -> bool:
    """兼容旧调用点：在索引里就算被跟踪。判红用 `tracked_state`，它更贴近 CI 的现实。"""
    return tracked_state(path) != "missing"


def reset_git_cache() -> None:
    """清掉三张 git 清单的进程内缓存。

    没有它，离线回归里「先造一个改过的文件、再问判据认不认得」必然空跑：缓存是在
    第一次调用时抓的，探针落地时那张表早就定稿了。判据自己也要能被当场改动验证。
    """
    _INDEX.clear()
    _HEAD.clear()
    _DIRTY.clear()


def _state_hint(rel: str, state: str) -> str:
    if state == "dirty":
        return (f"{rel} 在提交里，但工作区改过没提交——本机跑绿的是工作区，CI 检出并打包的是"
                f"提交，两份内容不是同一份（验证结论对交付没有担保）：先 git commit {rel}")
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


# 交付链里这三个目录会各自出现**同名**的本地模块（vendor 副本）。运行时拿到哪一份
# 由 sys.path 顺序决定：b2bcollect_build 永远优先，其次才是被 --add-data 带进包的
# riskbird_build/ali1688_build 子目录。所以「同名但内容不同」等价于「同一件事有两套
# 行为，用哪套取决于加载顺序」——这是本轮实测到的第 10 类缺陷：
#   yzd_client.py 的两处上传侧修复（超长号码列按**整条**递减保留、无任何联系方式的行
#   不入库）只落在 riskbird_build 那一份里，b2bcollect_build 与 ali1688_build 两份仍是
#   旧实现。产物用的是 b2bcollect 那一份：界面不报错、日志不报错、CI 全绿，客户库里却
#   多了没有联系方式的行，超长的手机号被从中间切成打不通的半截号。
# 判据不是「不许分叉」，而是「分叉必须在案」：下面这张表里每一项都写清为什么允许不同
# （新模块又分叉却没登记 → 直接红）。登记的意义和 NOT_PACKAGED 那张表一样——把「忘了
# 同步」与「有意各留一份」这两种症状完全一样的形态分开。
SHARED_SOURCE_DIRS = ("b2bcollect_build", "riskbird_build", "ali1688_build")
DIVERGENT_VENDORED = {
    "customer_library_cleaner.py":
        "两套清洗口径按各自产品演进：b2bcollect 那份有岗位族标注/公司名尾巴剥离/"
        "Excel 非法字符，riskbird 那份有多列号码合并/补区号/入库出口脏行。合并是一次"
        "独立重构，不在交付日动；此项登记是为了让分叉**可见**，不是为了放行",
    "task_registry.py":
        "两份的任务目录名与原数据文件名按产品不同（B2B三站…/企业数据_原始 对 "
        "企业数据自动采集工具/企业数据_全部平台），b2bcollect 那份另有续采快照与"
        "进度台账。差异刻意，但同样必须被看见",
}


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_shared_source() -> list:
    """第 10 条：三个目录里同名的 .py 要么字节一致，要么在 DIVERGENT_VENDORED 里登记理由。"""
    problems = []
    copies = {}
    for name in SHARED_SOURCE_DIRS:
        d = ROOT / name
        if not d.is_dir():
            problems.append(f"交付目录 {name}/ 不在了（这条判据没法核对同名模块）")
            continue
        for p in sorted(d.glob("*.py")):
            if p.name.startswith(TEST_PREFIX):
                continue            # 测试文件不参与交付：各自的夹具与断言本来就该不同
            copies.setdefault(p.name, []).append(p)
    for name, paths in sorted(copies.items()):
        if len(paths) < 2:
            continue
        digests = {p: _file_digest(p) for p in paths}
        if len(set(digests.values())) == 1:
            if name in DIVERGENT_VENDORED:
                problems.append(
                    f"{name}：三份拷贝已经字节一致，DIVERGENT_VENDORED 里那条登记该删了"
                    f"（登记着「允许不同」却早就同步了，下次真分叉时这张表就不值得信了）")
            continue
        if name in DIVERGENT_VENDORED:
            reason = str(DIVERGENT_VENDORED[name] or "").strip()
            if not reason:
                problems.append(
                    f"{name}：登记为「允许不同」却没写理由——「忘了同步」与「有意分叉」"
                    f"症状完全一样，只有理由能把它们分开")
            continue
        where = "、".join(str(p.relative_to(ROOT)) for p in paths)
        problems.append(
            f"{name}：{len(paths)} 份拷贝内容不一致（{where}）。运行时吃到哪一份由 "
            f"sys.path 顺序决定，等于「同一件事两套行为」；要么把改动同步到每一份，"
            f"要么在 DIVERGENT_VENDORED 里写清为什么允许各留一份")
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


# 交付链上手写的 shell 脚本（不在 CALLERS 里：它们没有打包清单要核对，只有文案要核对）。
# 为什么也要判：这几份脚本出错时的代价是「排障时看不见真数字」——派发云打包的脚本报不出
# run id、取制品的脚本把退出码印成空、批跑回归的脚本在「清单不完整」那句里把目录名印成空，
# 三种都恰好发生在最需要读那行字的时候。登记在表里的脚本如果不在位，同样判红：
# 一条静默不跑的判据比没有判据更危险（会让人以为这一项被验过）。
HYGIENE_SCRIPTS = (
    "debug/dispatch_b2bcollect_builds.sh",
    "debug/fetch_b2bcollect_artifacts.sh",
    "debug/poll_b2bci.sh",
    "debug/run_offline_regression.sh",
)


def check_script_hygiene() -> list:
    """第 9 条在「非打包入口」那几份脚本上的同样一份判据。"""
    problems = []
    for rel in HYGIENE_SCRIPTS:
        path = ROOT / rel
        if not path.is_file():
            problems.append(f"{rel} 不在位（登记了却读不到，这条判据正在空转）")
            continue
        state = tracked_state(path) if in_work_tree() else "ok"
        if state != "ok":
            # 与第 7 条同一个坑，而且更阴：这里读的是**工作区**那份文本，CI 上跑的却是
            # 提交里那份。改过没提交时，「文案卫生」验的是不会被交付的内容，判据看着绿，
            # 真正上云的那份脚本可能正带着同一个 bug。所以脏也要红，措辞按状态分开。
            problems.append(_state_hint(rel, state))
        for msg in check_shell_hygiene(path.read_text(encoding="utf-8", errors="replace")):
            problems.append(f"{rel} {msg}")
    return problems


# ---------------------------------------------------------------------------
# 第 11 条：交付链上的每一段 shell 都得过**真解析器**（bash -n），不能只靠正则列举雷区。
#
# 为什么第 9 条那种正则不够：2026-11-12 给 NSIS 步骤加版本号判据时，写下
#   echo "...实际拿到「${APP_VERSION:-（空）」」..."
# 这句在 bash 3.2 的词法扫描里直接解析失败（报 unexpected EOF while looking for
# matching `"'），整步连第一行都跑不到。它和第 9 条管的是同一族坑（中文与变量写法的
# 接缝），但触发点是**字节级**的：实测 `${V:-空}`、`${V:-（空）}`、`${V:-x」}` 全都正常，
# 只有 `）」}` 这一种组合会把解析器带跑——这种清单没人记得住，也不该靠人记住。
# 更糟的是它发生在**执行之前**：`set -e`、判据、日志统统没有机会出场，CI 上只剩一句
# 语法错误。所以这一条把脚本原样交给 bash 自己判，判据的覆盖面等于解析器的覆盖面。
#
# 范围：四个 workflow 里每一个真正交给 bash 执行的 run 步（pwsh 步另由离线测试 M 节
# 按 PowerShell 口径判），加交付链上手写的每一份 .sh。漏切一种写法=这条门在那种写法
# 上是空的，所以这里同时判「切出来的块数」与「文件里 run: 的总数」对不对得上。
# ---------------------------------------------------------------------------

STEP_HEAD = re.compile(r"^\s*-\s+(?:name|uses):")
STEP_NAME = re.compile(r"^\s*-\s+name:\s*(.+?)\s*$")
RUN_BLOCK_KEY = re.compile(r"^(\s*)run:\s*([|>][-+\d]*)\s*$")
RUN_INLINE_KEY = re.compile(r"^(\s*)run:\s*(\S.*)$")
ANY_RUN_KEY = re.compile(r"^\s*run:\s*")
SHELL_KEY = re.compile(r"^\s*shell:\s*(\w+)")
RUNS_ON = re.compile(r"^\s*runs-on:\s*[\w-]*windows[\w-]*", re.M)
GH_EXPRESSION = re.compile(r"\$\{\{.*?\}\}")
# `${{ ... }}` 被替换掉之后留下的记号。它必须是**在案的常量**而不是随手写的一串字：
# 下游判据（第 18 条 c 路「-r 的清单路径是运行时才拼出来的」）要靠它认出「这里原本是个
# 表达式」。上一版那一判直接搜 `${{`，而脚本早在切块阶段就被替换成了占位符，于是夹具里
# `-r .github/requirements/${{ matrix.os }}.txt` 报成「清单不在仓库里」——话是说对了方向，
# 归因却错了，读日志的人会去补文件而不是去写死路径。
GH_PLACEHOLDER = "GH_EXPR"

# 判据覆盖的文件。登记在表里的东西不在位就响：一条静默不跑的判据比没有判据更危险
# （会让人以为这一项被验过）。workflow 全收：另外两条（1688、riskbird）虽然不走
# b2bcollect 的清单，但 shell 语法坏掉的代价一模一样。
WORKFLOWS = tuple(f".github/workflows/{n}" for n in (
    "build-b2bcollect.yml", "build-b2bcollect-mac.yml",
    "build-1688.yml", "build-riskbird.yml"))
SHELL_SOURCES = tuple(list(HYGIENE_SCRIPTS) + ["debug/build_b2bcollect_mac_local.sh"])


def workflow_default_shell(text: str) -> str:
    """这个 workflow 里没写 `shell:` 的 run 步由谁执行。

    GitHub 的口径：作业级 defaults.run.shell 优先，否则是 runner 默认——Windows 上
    是 pwsh，macOS/Ubuntu 上是 bash。判错默认值会把 bash 步当 pwsh 步放过（或反过来，
    拿 bash 去解析 PowerShell 脚本，得到一条永远修不掉的假红）。
    """
    m = re.search(r"^\s*defaults:\s*\n\s*run:\s*\n\s*shell:\s*(\w+)", text, re.M)
    if m:
        return m.group(1)
    return "pwsh" if RUNS_ON.search(text) else "bash"


def run_blocks_in_workflow(text: str) -> list:
    """切出 workflow 里每一段 run 脚本：[(步名, 起始行号, 执行它的 shell, 脚本)]。

    三处容易切错、切错了就等于门是空的：
    · 块标量的缩进由**第一个非空行**决定，不是 `run:` 的缩进 +2（本机两份 workflow
      恰好都是 +2，照 +2 写死的话，下次有人多缩一级就整段漏判）；
    · `run: 一行写完` 也得收（只认 `run: |` 的话，这种步压根不进判据）；
    · `${{ ... }}` 是 Actions 在交给 bash 之前就替换掉的表达式，原样喂给解析器只会
      得到一句与真 bug 无关的语法错，所以先替换成占位符再判。
    """
    lines = text.splitlines()
    default = workflow_default_shell(text)
    heads = [i for i, ln in enumerate(lines) if STEP_HEAD.match(ln)]
    out = []
    for idx, start in enumerate(heads):
        region = lines[start:heads[idx + 1] if idx + 1 < len(heads) else len(lines)]
        name_m = STEP_NAME.match(region[0])
        name = name_m.group(1) if name_m else f"(第 {start + 1} 行的步)"
        shells = [m.group(1) for ln in region if (m := SHELL_KEY.match(ln))]
        shell = shells[-1] if shells else default      # 一步里多个 shell: 时以最后一个为准
        for j, ln in enumerate(region):
            block, inline = RUN_BLOCK_KEY.match(ln), RUN_INLINE_KEY.match(ln)
            if block:
                key_indent, strip, body, p = len(block.group(1)), None, [], j + 1
                while p < len(region):
                    raw = region[p]
                    if raw.strip():
                        cur = len(raw) - len(raw.lstrip())
                        if cur <= key_indent:
                            break
                        if strip is None:
                            strip = cur
                        body.append(raw[strip:])
                    else:
                        body.append("")
                    p += 1
                script = "\n".join(body)
            elif inline:
                script = inline.group(2).strip()
            else:
                continue
            out.append((name, start + j + 1, shell,
                        GH_EXPRESSION.sub(GH_PLACEHOLDER, script)))
    return out


def check_shell_parse(script: str, bash: str) -> list:
    """把一段 bash 脚本原样交给 `bash -n`：只解析、不执行，所以不会有副作用。

    两头都必须显式按字节走，不能用 `text=True`。2026-11-12 云端第一个红就是这条判据
    自己带回来的：Windows runner 上 Python 给子进程用的默认编码是控制台代码页（cp1252），
    而被判的脚本里全是中文文案——`input=<str>` 在写 stdin 那一刻抛 UnicodeEncodeError，
    构建前硬门自己炸在打包之前。本机（UTF-8 环境）永远复现不出来，症状与「脚本内容有问题」
    一模一样。判据读的是要被执行的字节，那就连编码都不该交给环境决定。
    """
    proc = subprocess.run([bash, "-n"], input=script.encode("utf-8"),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode == 0:
        return []
    err = (proc.stderr.decode("utf-8", errors="replace")
           or proc.stdout.decode("utf-8", errors="replace")).strip()
    return [err.replace("\n", " | ")]


def check_shell_syntax(workflows: tuple = None, sources: tuple = None) -> list:
    """交付链上所有手写 shell 的语法判据（真解析器，不看内容长什么样）。

    两份清单做成参数是为了**判据自己可被反向对照**：离线测试要能拿一份「故意写着
    `${V:-（空）」` 的假 workflow」喂进来，要求这一条必须报红。写死路径的判据只能
    验「现在没坏」，验不出「坏了会不会响」——后者才是这条门的全部价值。
    """
    workflows = WORKFLOWS if workflows is None else workflows
    sources = SHELL_SOURCES if sources is None else sources
    bash = shutil.which("bash")
    if not bash:
        # 这条判据的存在意义是「拦在开建之前」。这台机器上没有 bash 就静默放行，
        # 等于在最需要它的环境（Windows 开发机直构）里把门拆了——必须响。
        return [f"找不到 bash，第 11 条没法执行（{len(workflows)} 份 workflow + "
                f"{len(sources)} 份脚本全部未判）：装 Git Bash 后重跑这一步"]
    problems = []
    for rel in sources:
        path = ROOT / rel
        if not path.is_file():
            problems.append(f"{rel} 不在位（登记了却读不到，这条判据正在空转）")
            continue
        for msg in check_shell_parse(path.read_text(encoding="utf-8", errors="replace"),
                                     bash):
            problems.append(f"{rel}：{msg}")
    for rel in workflows:
        path = ROOT / rel
        if not path.is_file():
            problems.append(f"{rel} 不在位（登记了却读不到，这条判据正在空转）")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        blocks = run_blocks_in_workflow(text)
        total = sum(1 for ln in text.splitlines() if ANY_RUN_KEY.match(ln))
        # 切少了=有 run 步没被判到；切多了=步边界认错了。两种都要响：
        # 「门看起来在跑、实际漏了这一步」比直接报错更难被发现。
        if len(blocks) != total:
            problems.append(f"{rel}：文件里有 {total} 个 run 步，只切出 {len(blocks)} 段"
                            f"（切漏=那几步的语法没人判，切多=判错了东西）")
        for name, lineno, shell, script in blocks:
            if shell != "bash":
                continue          # pwsh 步由离线测试 M 节按 PowerShell 口径判：本机没有 pwsh
            for msg in check_shell_parse(script, bash):
                problems.append(f"{rel} 第 {lineno} 行「{name}」：{msg}")
    return problems


# ---------------------------------------------------------------------------
# 第 14 条：从**别的工具留下的那份输出**里按文本取值的那一句 grep，必须就近写明
# 这批字节的出处（一次真实运行的 run/job 号，或「原字节 / 实测 / 真字节」样本）。
#
# 2026-11-13 凌晨云端那次红给了这一族坑一个此前没见过的形状：判据没错、编译器没错、
# 114MB 安装包也真编出来了，最后死在「makensis 的横幅长什么样」这句**想象**上——脚本
# 去找 "Nullsoft Install System v…"，而那台 NSIS 3.10 自报的是
# "MakeNSIS v3.10 - Copyright 1999-2023 Contributors"。代价不是少一行日志，是整条
# Windows 交付没有安装包。
# 更值得堵的其实是第二半：本机这一整段**全绿**——仿真的假编译器印的就是判据想要的那句
# 假话，判据与证据两头抄的是同一个想象。「多想到一种写法」堵不住这类坑（写法无穷），
# 能堵的是纪律：**对外部工具的文本输出做格式假设时，假设的出处必须写在旁边**，让下一个
# 人改判据之前先看见那批真字节是从哪一次真实运行里取的。没有出处的格式假设一律红。
#
# 触发形状只有两种，判据都是同一句话：「喂给 grep 的那批字节不是这条 shell 自己写的」。
#   ① grep 拿一个带变量的引号路径当**文件参数**——`grep -haoE "模式" "$LOG"`，读的是别的
#      工具落下来的那份输出；
#   ② grep 读管道，而喂它的是别的命令——`certutil … | grep -oE "[0-9a-fA-F]{64}"` 那种，
#      字节同样是别人产的。
# 刻意不算的一种：喂它的是自家 `printf`/`echo`。批跑脚本里 `printf '%s\n' "$out" |
# grep -E "PASS|FAIL"` 那一族读的是同仓自己印的文案，格式由打印它的那一行决定，改了
# 当场就红，不需要出处；把这种也算进来，只会逼人为了消红去写一个假出处——那正是这条
# 门要防的动作，第一天就不能自己示范。
# 判不到的形状也写在这里，免得这条门给人「所有取值都被管住了」的错觉：不带引号的字面
# 路径（`grep x /etc/os-release`）、以及 awk/sed/cut 按列取值，都不在这一条的视野里。
#
# 同一族在**运行期**的清点结果（2026-11-13 全仓扫过一遍，结论记在这儿而不是只写在对话里）：
#   · b2bcollect_build/access_guard.py `_raw_machine_id()`：从 `ioreg` 输出里取
#     IOPlatformUUID——取不到就退到「随机 UUID 落盘」，坏消息是重新授权一次，不是崩；
#   · riskbird_build/chrome_driver.py `chrome_major()/driver_version()`：从
#     `--version` / PowerShell ProductVersion 里取第一段数字，取不到返回空串，只影响
#     驱动候选的**排序**，不影响有没有驱动可用；
#   · riskbird_build/chrome_driver.py `classify_driver_error()`：认的是 chromedriver 报错
#     原文里的两句关键字——措辞改了只会把 version 误分成 connect（多试一轮 + 日志说得
#     不够准），不会把一个能用的驱动判成坏的。
# 这三处都不在交付链上，也不符合「想象错一次就没有交付」的代价形状，所以**不**纳入本条
# 的红；纳进来只会让维护者为了消红去给运行期代码写假出处。要动它们的时候，判据是「取值
# 失败会不会让功能消失」，不是「有没有按行号切」。
# ---------------------------------------------------------------------------
PROVENANCE_GREP = re.compile(r"(?:^|[\s;&|`(])grep(?:\s|$)")
# 引号包起来的一串（模式或参数）；单双引号都算，取值假设最常写在双引号里
PROVENANCE_QUOTED = re.compile(r'"[^"]*"|\'[^\']*\'')
# 一个以变量开头的引号参数："$LOG" / "${LOG}" / "$(/bin/x)"
PROVENANCE_VARARG = re.compile(r'^"?\$\{?[A-Za-z_(!]')
# 上游命令里出现这两个词就当那是自家文案（放在引号里的 "echo" 会被误判成自家文案，
# 那种漏判写在上面那段注释里，不假装不存在）
PROVENANCE_OWN_FEEDER = re.compile(r"(?:^|[\s;&|`(])(?:printf|echo)(?:\s|$)")
# 出处的三种可接受形态：一次真实云构建的编号 / 贴过真实字节 / 显式声明这里取值不靠猜
PROVENANCE_BASIS = re.compile(r"(?:run|job)\s+\d{6,}|原字节|实测|真字节|格式依据")
# 回看窗口。为什么不是「紧邻的注释块」：NSIS 那两处哈希取值共用同一段解释（一个函数、
# 两条分支），中间还夹着 `if command -v` 与 `else` 两行代码，紧邻注释块判据会把第二处
# 判成没出处。为什么不是无限回看：窗口一大，文件顶上随便一句「实测过」就能给全文件的
# 取值当免检牌——那就不再是出处，是护身符。24 行的代价是「隔了半屏的注释仍然算」，
# 比逼人在每一行上面重抄一遍假出处便宜，也比无限回看可信。
PROVENANCE_LOOKBACK = 24


def _shell_segments(stmt: str) -> list:
    """把一句 shell 切成「一条条命令」，分隔符只认**引号外**的 `|`/`||`/`&&`/`;`/换行。

    两处必须做对，否则判据从第一步就开始指错方向：
    · `grep -E "PASS|FAIL|通过|失败"` 里的 `|` 是模式的一部分，切开了就等于凭空多出一个
      「上游」；单引号里的 `$(` 也是字面量（`printf '%s\\n'` 那种），不能当子命令起点。
    · `$(printf '%s\\n' "$out" | grep -c .)` 这种嵌在引号里的命令替换，**要**切：那才是
      grep 真正的上游。所以扫描栈里区分三种状态——在双引号里、在单引号里、在 `$(…)` 里。
    """
    segs, buf, stack = [], [], []

    def flush():
        text = "".join(buf).strip()
        if text:
            segs.append(text)
        del buf[:]

    i, n = 0, len(stmt)
    while i < n:
        ch = stmt[i]
        top = stack[-1] if stack else ""
        if top == "'":                       # 单引号里一切都是字面量
            buf.append(ch)
            if ch == "'":
                stack.pop()
            i += 1
            continue
        if ch == "\\" and top != "`":        # 转义：连同下一个字符一起原样带走
            buf.append(stmt[i:i + 2])
            i += 2
            continue
        if ch in "\"'":
            # 同种引号再出现一次是**闭合**（shell 里引号不嵌套）。写成无脑 push 的话，
            # `printf '%s\n' "$out" | grep …` 从第一个闭合引号起就永远在「引号内」，
            # 整行切不开、grep 没有上游——这条门会安静地不判任何东西，还全绿。
            if top == ch:
                stack.pop()
            else:
                stack.append(ch)
            buf.append(ch)
            i += 1
            continue
        if ch == "$" and stmt[i + 1:i + 2] == "(":
            stack.append("(")                # 子命令里的引号与分隔符是另一套
            flush()
            i += 2
            continue
        if ch == ")" and top == "(":
            stack.pop()
            flush()
            i += 1
            continue
        if top in ("", "("):
            if stmt[i:i + 2] in ("||", "&&"):
                flush()
                i += 2
                continue
            if ch in "|;&\n":
                flush()
                i += 1
                continue
        buf.append(ch)
        i += 1
    flush()
    return segs


def _grep_takes_value(seg: str) -> bool:
    """这一段里的 grep 是否带「变量指着的那份文件」当参数（第一个引号参数是模式）。"""
    m = PROVENANCE_GREP.search(seg)
    if not m:
        return False
    args = [s for s in PROVENANCE_QUOTED.findall(seg[m.end():])
            if not s.lstrip("\"'").startswith("-")]       # --include="*.py" 是开关不是文件
    return any(PROVENANCE_VARARG.match(s) for s in args[1:])


def _provenance_sites(text: str) -> list:
    """[(行号, 那一句)]：按外部工具的文本输出取值的 grep，且附近没写明这批字节的出处。"""
    lines = text.splitlines()
    # 反斜杠续行先并成一条逻辑行：`grep -E "x" \` 换行 `    "$LOG"` 是一句话，
    # 拆开判就等于没判（文件参数在下一行，上一行看着像「只是模式」）。
    logical, buf, start = [], "", 1
    for no, ln in enumerate(lines, 1):
        stripped = ln.strip()
        if not buf:
            start = no
        if stripped.startswith("#"):
            continue                       # 注释里写的正是「哪种写法是猜的」，不是违规现场
        if stripped.endswith("\\"):
            buf += " " + stripped[:-1]
            continue
        logical.append((start, (buf + " " + stripped).strip()))
        buf = ""
    if buf.strip():
        logical.append((start, buf.strip()))
    out = []
    for start, stmt in logical:
        segs = _shell_segments(stmt)
        # 一句里可能有不止一个 grep（`… | grep -oE "…失败" | tail -1 | grep -oE "[0-9]+"`
        # 就是批跑脚本里的原话），只判第一个的话，后面那个取值假设可以随便改而没人响。
        hits = [i for i, s in enumerate(segs) if PROVENANCE_GREP.search(s)]
        if not hits:
            continue
        offending = False
        for i in hits:
            feeders = segs[:i]
            takes_file = _grep_takes_value(segs[i])
            if not takes_file and any(PROVENANCE_OWN_FEEDER.search(f) for f in feeders):
                continue                   # 自家 printf/echo 搬来的文案，格式由同仓那行决定
            if not (takes_file or feeders):
                continue                   # 既没读变量里的文件，管道上游也没别的工具
            offending = True
            break
        if not offending:
            continue
        lookback = "\n".join(lines[max(0, start - 1 - PROVENANCE_LOOKBACK):start - 1])
        if PROVENANCE_BASIS.search(lookback):
            continue                       # 出处在案：这一句是按真字节写的，改的人能看见
        out.append((start, stmt))
    return out


def check_external_output_provenance(workflows: tuple = None, sources: tuple = None,
                                     texts: tuple = ()) -> list:
    """交付链 shell 里「对外部工具输出格式的假设」必须带出处。

    workflows/sources 与第 11 条同口径（登记表可整体替换，方便反向对照）；texts 是
    内存里的 [(标签, 脚本)]，离线测试拿它喂「同一句 grep，带出处 / 不带出处」这一对，
    不必为了判一次去动真文件。写死路径的判据只能验「现在没坏」，验不出「坏了会响」。
    """
    workflows = WORKFLOWS if workflows is None else workflows
    sources = SHELL_SOURCES if sources is None else sources
    corpus = []
    for rel in sources:
        path = ROOT / rel
        if not path.is_file():
            continue                    # 不在位由第 11 条点名，这里不重复报
        corpus.append((rel, path.read_text(encoding="utf-8", errors="replace")))
    for rel in workflows:
        path = ROOT / rel
        if not path.is_file():
            continue
        for name, start, shell, script in run_blocks_in_workflow(
                path.read_text(encoding="utf-8", errors="replace")):
            if shell != "bash":
                continue
            corpus.append((f"{rel} 第 {start} 行「{name}」", script))
    corpus.extend(texts)
    problems = []
    for label, script in corpus:
        for lineno, stmt in _provenance_sites(script):
            problems.append(
                f"{label} 第 {lineno} 行的 grep 按外部工具的文本输出取值，却查不到这批字节的"
                f"出处（往上 {PROVENANCE_LOOKBACK} 行内补一句注释，写明 run/job 号、原字节"
                f"样本，或「格式依据」＝这个取值不依赖那个工具的话术）：{stmt[:140]}")
    return problems


# ---------------------------------------------------------------------------
# 第 15 条：set -e 生效的 shell 里，「正常情况下也会返回非零」的命令不许裸着当赋值来源。
#
# 为什么单独立一条（2026-11-13 本机实发）：debug/build_b2bcollect_mac_local.sh 的第 6 道门
# （真双击验窗）安静地没跑——日志停在第 5 步的 ✅ 之后，一行报错都没有。那一行是
#     BEFORE_PIDS=$(pgrep -x B2bCollectTool 2>/dev/null | tr '\n' ' ')
# 「机器上没有旧实例」是**最常见**的情形（干净机器就该一个都没有），pgrep 在这种情形返回 1，
# pipefail 让整条管道非零，裸赋值把这个非零原样交给 set -e，脚本当场结束且零解释。
# 它此前一直看起来是绿的，只因为那台机器上恰好留着一枚我自己起的旧实例——判据依赖运气。
#
# 三条口径要说清，否则这条门要么漏要么假红：
#   · 只有**裸赋值** `VAR=$(...)` 会把替换的退出码交给 set -e；`local/export/readonly`
#     前缀会把它吞掉（实测 rc=0 继续跑），所以那些写法不报（它们的问题是另一类：静默放过）；
#   · 命令替换出现在别的命令的参数里（`echo "$(cat f)"`）退出码被外层命令覆盖，也不报；
#   · 有 `|| true`／`|| { echo ...; exit 1; }`／在 if、while、&&、|| 列表里的，都是**作者
#     已经表过态**的写法，一律放过——本条要拦的是「没表过态」那一种。
# 判的是「这一段 shell 在 -e 下会怎么走」，与第 11 条（能不能被解析）正交：第 11 条绿、
# 这一条红的脚本，症状正是「跑了一半，后面的门安静地不存在」。
#
# 同一族的另一半（15b，本轮补判据时实测出来）：脚本装了 `trap … ERR` 却没有 `set -E`
# （errtrace）。陷阱在函数与命令替换里根本不生效（/bin/bash 实测：`set -euo pipefail`
# + 陷阱 + `f(){ false; }; f` 一声不响，`set -Eeuo pipefail` 同一份才打印），症状和
# 「没装陷阱」完全一样，但脚本看上去却像处理过了——所以只要看见 ERR 陷阱就同时要到 -E，
# 而不是只改本轮那一句 `set`。
# ---------------------------------------------------------------------------

# 正常情形（没匹配、没找到、文件不存在）就返回非零的外部命令。
NONZERO_PRONE = ("pgrep", "pidof", "grep", "egrep", "fgrep", "rg", "ls", "which",
                 "diff", "cmp", "cat", "find", "test")
# `command -v x` 与 `type x` 是「查得到=0／查不到=1」的代表，写法上不是单个命令名。
NONZERO_PRONE_PREFIX = ("command -v", "builtin command -v")
# 会吞掉替换退出码的赋值前缀（实测：local/export 后面的 `=$(false)` 不触发 set -e）。
MASKING_PREFIX = re.compile(r"^\s*(?:local|export|readonly|declare|typeset)\s")
# 裸赋值：VAR=$( ... )，整行就是这一句（行尾还有 `|| ...` 的说明作者表过态）。
BARE_ASSIGN = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)=\$\((.*)\)\s*$")
# set 的开关行。短选项那一串字母里**大小写不等价**（-e=errexit，-E=errtrace），长选项
# 写法是 `-o errexit` / `-o errtrace`。这一条判据第一版写的是 `-[a-z]*e[a-z]*`，于是
# `set -Eeuo pipefail` 整行认不出来——第 15 条对出事的那份脚本直接空转（加了 -E 之后
# 大小写混排是必然写法），所以这里逐个字母判，而不是拿一串正则整行判。
SET_LINE = re.compile(r"^\s*set\s+(\S.*)$")
SET_OPT = re.compile(r"([+-])([A-Za-z]+)(?:[ \t]+([A-Za-z]+))?")
# 装了 ERR 陷阱（`trap '…' ERR`）；`trap - ERR` 是把陷阱摘掉，不算装。
ERR_TRAP = re.compile(r"^\s*trap\s+(?!-\s)(.*?)\s+([A-Za-z0-9 ]+)\s*$")
# errtrace：让函数与命令替换里也继承 ERR 陷阱。实测（/bin/bash 3.2 与 5.x 同）：
#   set -euo pipefail + trap … ERR + f(){ false; }; f  → 陷阱**不响**，脚本静默退出；
#   set -Eeuo pipefail 同一份                         → 陷阱响，留下一行人话。
# 也就是说只写 `set -euo` 的陷阱只覆盖脚本主体那一层，恰恰盖不住最容易静默结束的位置。


def _set_state(line: str):
    """这一行 `set` 把 errexit／errtrace 打开（True）、关掉（False）还是没提（None）。

    口径：一行一句（`if …; then set +e` 这种把 set 藏在中间的不在本条覆盖内，报出来
    也只会是漏报而不是假红——与第 11 条一样，判的是脚本自己写下来的那一行）。
    """
    m = SET_LINE.match(line)
    if not m:
        return None, None
    er = et = None
    for sign, opts, arg in SET_OPT.findall(m.group(1)):
        on = sign == "-"
        if opts == "o":
            if arg == "errexit":
                er = on
            elif arg == "errtrace":
                et = on
        else:
            if "e" in opts:
                er = on
            if "E" in opts:
                et = on
    return er, et


def _is_err_trap(line: str) -> bool:
    """这一行是不是「装 ERR 陷阱」（信号列表里含 ERR），注释行与 `trap - ERR` 都不算。"""
    if line.strip().startswith("#"):
        return False
    m = ERR_TRAP.match(line)
    return bool(m) and "ERR" in m.group(2).split()


def _errtrap_problems(label: str, base: int, segments: list) -> list:
    """第 15 条的另一半：装了 ERR 陷阱，脚本却让它压根没有触发的机会。

    上面那半管的是「一句非零把脚本静默带走」，这一半管的是「已经决定要留一行话了，
    结果那句话在出事的时候不会说」——两种症状都是「日志到此为止、没有解释」，但修法
    不同：前者是给那一句接管非零，后者是在 set 里补一个 E。判据必须在装陷阱的那一刻
    就把 -E 一起要到，否则下一来看见的是一副「已经处理过了」的样子，比什么都没有更危险。
    """
    out = []
    traps = [(lineno, line) for lineno, line, _a in segments if _is_err_trap(line)]
    if not traps:
        return out
    lineno, line = traps[0]
    if not any(active for _n, _l, active in segments):
        out.append(
            f"{label} 第 {base + lineno} 行装了 ERR 陷阱，但整份脚本没有任何一段在 -e 下跑："
            f"errexit 没开就不会产生 ERR 事件，这句 trap 永远不响（出错时既没有非零退出也没有"
            f"一行解释）。要么补 `set -euo pipefail`，要么把这行删掉别留一副处理过的样子："
            f"{line.strip()[:120]}")
        return out
    # errtrace 看整份脚本有没有被打开过（行序、中途关掉再开不在本条口径内）：
    # 装陷阱的人要的就是「函数里的非零也留得下一句话」，这份脚本整体没这个能力就该响。
    if not any(_set_state(ln)[1] for _n, ln, _a in segments
               if not ln.strip().startswith("#")):
        out.append(
            f"{label} 第 {base + lineno} 行装了 ERR 陷阱，但 set 里没有 -E（errtrace）："
            f"实测陷阱在函数与命令替换里不生效，只盖脚本主体那一层——而「静默结束」最常发生在"
            f"函数里的那一次调用。补成 `set -Eeuo pipefail`：{line.strip()[:120]}")
    return out


def _setplus_problems(label: str, base: int, segments: list) -> list:
    """第 15 条的第三半：`set +e` 把**一整段**的保护关掉。

    上面两半管的是「一句非零把脚本带走」与「陷阱响了却没开 -E」，这一半管的是反向的那
    种侥幸：`set +e … set -e` 之间每一条命令都失去了拦路资格，后来人在中间插一句（换
    个落点、加一次查找）它失败了也不会有人知道，而这一段自己看着是「已经开回来」的。
    接管某一条命令的退出码从来不需要关整段：`cmd || rc=$?`（管道要首段就
    `cmd | tee log || rc=${PIPESTATUS[0]}`，实测 bash 3.2 与 5.x 都成立）。
    """
    out = []
    for lineno, line, _active in segments:
        if line.strip().startswith("#"):
            continue
        if _set_state(line)[0] is not False:
            continue
        out.append(
            f"{label} 第 {base + lineno} 行用 `set +e` 关掉了整段的 errexit：这一句到"
            f"`set -e` 之间的每一条命令都失去了拦路资格，后来人插进来的那一句失败不会"
            f"有人知道。只接管这一条的退出码请写 `cmd || rc=$?`（管道里要首段就 "
            f"`… | tee \"$LOG\" || rc=${{PIPESTATUS[0]}}`）：{line.strip()[:120]}")
    return out


def _errexit_segments(text: str, default: bool) -> list:
    """把脚本切成 [(起行号, 该行原文, -e 是否生效)]。

    Actions 的 bash 步默认就带 `-e`（`shell: bash` 时还是 `-o pipefail`），本地脚本要看
    有没有 `set -e`，中间还可能被 `set +e` 关掉——判据必须跟着脚本自己的口径走，
    否则会在「作者明确关掉 -e 的那一段」报一堆无意义的红。
    """
    active = default
    out = []
    for lineno, line in enumerate(text.splitlines(), 1):
        er, _et = _set_state(line)
        if er is not None and not line.strip().startswith("#"):
            active = er
        out.append((lineno, line, active))
    return out


def _nonzero_prone_head(seg: str) -> str:
    """这一段命令的首个命令是不是「正常情形也返回非零」的那批。"""
    s = seg.strip()
    for prefix in NONZERO_PRONE_PREFIX:
        if s.startswith(prefix):
            return prefix
    head = re.split(r"[\s|;&)]", s, maxsplit=1)[0]
    return head if head in NONZERO_PRONE else ""


def check_shell_errexit_hazards(sources: tuple = None, workflows: tuple = None,
                                texts: tuple = ()) -> list:
    """第 15 条：-e 下的裸赋值 + 正常也会非零的命令 = 脚本会静默结束（带行号点名）。

    sources/workflows/texts 与第 11、14 条同口径：登记表可整体替换，texts 是内存里的
    [(标签, 脚本)]——离线测试要能拿「同一句 pgrep 赋值，裸着写 / 加 || true」这一对喂进来，
    要求裸着的那份必须报红、加了的那份必须干净。判据验不出「坏了会响」就等于没有判据。
    """
    sources = SHELL_SOURCES if sources is None else sources
    workflows = WORKFLOWS if workflows is None else workflows
    corpus = []
    for rel in sources:
        path = ROOT / rel
        if not path.is_file():
            continue                    # 不在位由第 11 条点名，这里不重复报
        text = path.read_text(encoding="utf-8", errors="replace")
        # 本地脚本：只有显式 `set -e` 之后才算（脚本里没写过就一路不设，本条不适用）。
        corpus.append((rel, 0, _errexit_segments(text, default=False)))
    for rel in workflows:
        path = ROOT / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for name, start, shell, script in run_blocks_in_workflow(text):
            if shell != "bash":
                continue
            # Actions 的默认值：bash 步就是 -e（显式写了 set +e 的话由上面那段切换处理）。
            # start 是 `run:` 那一行，脚本体从 start+1 开始 → 块内第 n 行 = 文件第 start+n 行。
            corpus.append((f"{rel}「{name}」", start,
                           _errexit_segments(script, default=True)))
    for label, script in texts:
        corpus.append((label, 0, _errexit_segments(script, default=True)))
    problems = []
    for label, base, segments in corpus:
        problems.extend(_errtrap_problems(label, base, segments))
        problems.extend(_setplus_problems(label, base, segments))
        for lineno, line, active in segments:
            if not active:
                continue
            s = line.strip()
            if s.startswith("#"):
                continue
            m = BARE_ASSIGN.match(line)
            if not m or MASKING_PREFIX.match(line):
                continue
            inner = m.group(2)
            if "||" in inner or "&&" in inner:
                continue            # 作者已经接管了非零那条路
            bad = ""
            for seg in inner.split("|"):
                seg = re.sub(r"^\s*(?:if|while|until|then|else|do|!)\s+", " ", seg)
                found = _nonzero_prone_head(seg)
                if found:
                    bad = found
                    break
            if not bad:
                continue
            problems.append(
                f"{label} 第 {base + lineno} 行：`{m.group(1)}=$(… {bad} …)`——{bad} 在正常情形下"
                f"就返回非零（没匹配／没这个文件／机器上没这个进程），裸赋值把这个非零交给"
                f" set -e，脚本从这里起**静默结束**，后面的门一道也不会跑。"
                f"要么 `|| true`（取不到就算了），要么 `|| {{ echo \"为什么停\"; exit 1; }}`"
                f"（取不到就不能往下走）：{s[:120]}")
    return problems


# ---------------------------------------------------------------------------
# 第 16 条：同一份仓库里不许有两个「在大小写不敏感文件系统上其实是同一个文件」的路径。
#
# 为什么单独立一条（2026-11-13 本机实发，红在**离线套件自己身上**）：Z 节加反向对照时写
# 了两个夹具 `zz_errexit_trap_noe.sh` 与 `zz_errexit_trap_noE.sh`，本意是两份；在 macOS
# （APFS 默认大小写不敏感）与 Windows 上它们是同一个 inode，后写的把先写的静默盖掉，于是
# 「没开 -e」那条判据读到的是「没开 -E」那份内容，红了一条完全不相干的话。Linux CI 大小写
# 敏感，同一份用例在云端是两份文件、全绿——**症状只在开发机上出现，且长得像判据写错了**。
#
# 同一形状在交付链上有两份得管的账：
#   · 入库路径：`Foo.py` 与 `foo.py` 同时提交，Linux/macOS 都能检出，Windows/macOS  checkout
#     时后者会覆盖前者（git 还会一路报 modified），装进包的就是被盖掉那一份；
#   · 同名不同目录在 PyInstaller 里也是坑（--add-data 目标同名），但那已有第 1、13 条管；
# 这里只补没人管的那一维：**归一化之后同名**。
#
# 口径：比较键用 Unicode 的「大小写＋变体无关匹配」（casefold 两次 + NFD），因为 macOS 的
# HFS+/APFS 既不分大小写也不分组合形式（`é` 与 `e\u0301` 是同一个文件名）；只比**同一仓库
# 相对路径**，跨平台一律以 git 的 `/` 写法为准（与 _rel_posix 同口径）。git 不可用时不判。
# ---------------------------------------------------------------------------


def fs_insensitive_key(name: str) -> str:
    """大小写不敏感 + 变体不敏感文件系统（macOS/Windows 默认）眼里的同一个名字。

    UTS#36 的 canonical caseless matching：casefold → NFD → 再 casefold（一次 casefold 后
    出现的组合段可能又需要折叠，所以标准做法是折叠两次）。
    """
    return unicodedata.normalize("NFD", name.casefold()).casefold()


def check_case_colliding_paths(paths=None) -> list:
    """第 16 条：把「在 mac/Windows 上其实是同一个文件」的入库路径对点名出来。"""
    if paths is None:
        if not in_work_tree():
            return []
        paths = sorted(_tracked_sets()[0])
    groups = {}
    for p in paths:
        groups.setdefault(fs_insensitive_key(p), set()).add(p)
    problems = []
    for _key, names in sorted(groups.items()):
        if len(names) < 2:
            continue
        problems.append(
            f"仓库里这几条路径在 macOS/Windows 的默认文件系统（大小写·变体不敏感）里是"
            f"**同一个文件**：{sorted(names)}——Linux CI 检出是两份、开发机检出只剩一份，"
            f"而那份还是哪一次写入的最后一个；改名的时候请把两个用途分开写全（不要只动大小写）")
    return problems


# ---------------------------------------------------------------------------
# 第 12 条：交付链上的 Python 脚本，跟子进程与控制台之间的编码必须由脚本自己写定。
#
# 为什么单独立一条（2026-11-12 云端第二个红，由第 11 条自己带回来）：
#   `subprocess.run([bash, "-n"], input=<str>, text=True)` 在本机（UTF-8）跑得好好的，
#   上了 Windows runner 当场抛 UnicodeEncodeError: 'charmap' codec can't encode
#   characters in position 15-16——栈停在 subprocess._stdin_write，也就是**写 stdin
#   那一刻**。原因是 Windows 上 Python 给子进程用的默认编码是控制台代码页（英文 runner
#   是 cp1252），而被判的 shell 脚本里全是中文文案。构建前硬门自己炸在打包之前，症状与
#   「脚本内容有问题」一模一样，排查方向会被带去查第 11 条判出来的东西。
#
#   同一族的另一半是往控制台打印：本轮之前已经有一条门在管「第一次打印之前先修编码」，
#   但它只盯着四个入口。这里把它推广到**在 Windows runner 上也会执行的那整批脚本**
#   （.github/scripts/*.py，按目录枚举而不是手抄清单——新增一份就自动进判据，
#   「登记表漏登记新脚本」本身就是这一族缺陷的形状）。
#
# 三条子判据（都能被反向对照，见离线回归 M 节）：
#   a) text=True / universal_newlines=True 却没写 encoding → 红：解码子进程输出时
#      按环境代码页，读到 UTF-8 字节（git 关掉 core.quotePath 就会输出中文文件名）
#      直接 UnicodeDecodeError；
#   b) input= 给的是 str 字面量 → 红：写进子进程 stdin 时按环境编码，中文一进去就抛；
#   c) 脚本里有往控制台写的非 ASCII 文本（print / sys.stdout.write / sys.exit(msg) /
#      raise SystemExit(msg)），却没有在任何一句之前 reconfigure(encoding=) → 红。
#      中文的 SystemExit 消息尤其要紧：那常常是门禁**唯一要说清失败原因**的那一句。
# ---------------------------------------------------------------------------

IO_SCRIPTS_DIR = ".github/scripts"
SUBPROC_FUNCS = {"run", "Popen", "check_output", "check_call", "getoutput"}


def io_scripts() -> tuple:
    """枚举交付链上会在 Windows runner 上也执行的 Python 脚本（相对仓库根）。"""
    d = ROOT / IO_SCRIPTS_DIR
    if not d.is_dir():
        return ()
    return tuple(sorted(f"{IO_SCRIPTS_DIR}/{p.name}" for p in d.glob("*.py")))


def _call_target(node: ast.Call) -> str:
    """取被调函数的名字：``subprocess.run`` 与 ``run`` 都算 subprocess.run。"""
    f = node.func
    if isinstance(f, ast.Attribute):
        base = f.value.id if isinstance(f.value, ast.Name) else ""
        if base in ("subprocess", ""):
            return f.attr
        return ""
    return f.id if isinstance(f, ast.Name) else ""


def _kwarg(node: ast.Call, name: str):
    for k in node.keywords:
        if k.arg == name:
            return k.value
    return None


def _is_true(node) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _has_non_ascii(node) -> bool:
    try:
        return any(ord(ch) > 127 for ch in ast.unparse(node))
    except Exception:
        return True          # 读不懂就当有非 ASCII：宁可响一声让人来看，不要静默放行


def _console_writer(node: ast.Call) -> bool:
    """这一句会不会把文本送到控制台？print / sys.stdout.write / sys.exit(msg) /
    raise SystemExit(msg) 都算——后两种常被当成「只是退出」，可 Python 打印那条消息
    走的正是 stderr，编码问题一分不少。只盯 print 的判据会放过中文的 SystemExit 消息，
    而那恰好是门禁**唯一要说清失败原因**的那一句。
    """
    f = node.func
    if isinstance(f, ast.Name):
        return f.id in ("print", "SystemExit")
    if isinstance(f, ast.Attribute):
        base = f.value
        if f.attr == "write":
            # sys.stderr.write(...) 与 sys.stdout.write(...)
            return (isinstance(base, ast.Attribute) and base.attr in ("stdout", "stderr")
                    and isinstance(base.value, ast.Name) and base.value.id == "sys")
        if f.attr == "exit":
            return isinstance(base, ast.Name) and base.id == "sys"
    return False


def check_text_io_encoding(paths: tuple = None) -> list:
    """交付链 Python 脚本的子进程/控制台编码判据（返回问题清单，空=合格）。

    paths 做成参数是为了让判据自己可被反向对照：离线测试要能喂一份「故意写 text=True
    不写 encoding」的假脚本，要求这一条必须报红。
    """
    paths = io_scripts() if paths is None else paths
    problems = []
    for rel in paths:
        path = ROOT / rel
        if not path.is_file():
            problems.append(f"{rel} 不在位（登记了却读不到，这条判据正在空转）")
            continue
        src = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(src, filename=str(path))
        except SyntaxError as e:
            problems.append(f"{rel} 第 {e.lineno} 行语法读不懂（{e.msg}）："
                            "第 12 条在它上面没跑，这份脚本连 CI 都进不去")
            continue
        reconf = [n.lineno for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "reconfigure"
                  and _kwarg(n, "encoding") is not None]
        writes = [n for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and _console_writer(n) and _has_non_ascii(n)]
        if writes and not reconf:
            first = min(n.lineno for n in writes)
            problems.append(
                f"{rel} 第 {first} 行起有 {len(writes)} 处往控制台写中文/符号，却没有任何"
                "一处 reconfigure(encoding=\"utf-8\")：Windows runner 的控制台代码页"
                "（英文镜像 cp1252）会在那一句写出的当下抛 UnicodeEncodeError，"
                "门禁的结论反而被这条异常盖掉（本机 UTF-8 永远复现不出来）")
        elif writes and min(reconf) > min(n.lineno for n in writes):
            problems.append(
                f"{rel}：reconfigure 在第 {min(reconf)} 行，但第一次中文写控制台在第 "
                f"{min(n.lineno for n in writes)} 行——编码要在**第一次写控制台之前**定好，"
                "晚一步那一句就已经抛了")
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call) or _call_target(n) not in SUBPROC_FUNCS:
                continue
            loc = f"{rel} 第 {n.lineno} 行 subprocess.{_call_target(n)}"
            text_kw = _kwarg(n, "text")
            uni_kw = _kwarg(n, "universal_newlines")
            enc = _kwarg(n, "encoding")
            if (_is_true(text_kw) or _is_true(uni_kw)) and enc is None:
                problems.append(
                    f"{loc} 用 text=True 却没写 encoding：子进程输出一律按**环境代码页**解码，"
                    "Windows runner 是 cp1252，读到 UTF-8 字节（比如 git 输出中文文件名）"
                    "就 UnicodeDecodeError；写 encoding=\"utf-8\", errors=\"replace\"，"
                    "或者干脆按字节读再自己 decode")
            inp = _kwarg(n, "input")
            if isinstance(inp, ast.Constant) and isinstance(inp.value, str):
                problems.append(
                    f"{loc} 的 input= 给的是 str：写进子进程 stdin 时按环境编码，"
                    "内容里只要有一个中文就当场 UnicodeEncodeError（本轮云端那条红就停在这里）。"
                    "改成字节：input=\"...\".encode(\"utf-8\") 并把 text=True 去掉")
            elif inp is not None and (_is_true(text_kw) or _is_true(uni_kw)) and enc is None:
                problems.append(
                    f"{loc} 往子进程写数据（input=）却只写了 text=True：编码交给环境代码页，"
                    "换一台镜像就换一种编码。显式写 encoding=")
    return problems


# ---------------------------------------------------------------------------
# 第 13 条：Windows 装配清单必须与 Windows 构建命令里的 --add-data **互相**对拍。
#
# 前面第 1 条判的是「site_adapters 运行时要 import 的东西，三处入口都抄了没有」，
# 方向是**运行时 → 构建命令**。它管不到另一半：构建命令里明明把文件放进了包，
# 装配校验器（package_win_artifacts.REQUIRED_INTERNAL）却不知道它存在——于是这个文件
# 哪天没下载成功、被 prune 掉、dist 布局改了名，check_layout 一声不响，安装包照样出。
# 本轮实测到的正是这一格：workflow 的 --add-data 里有 riskbird_build/block_guard.py、
# riskbird_build/chrome_driver.py 与 drivers（三个大版本的 chromedriver），装配清单三条
# 都没登记。chrome_driver 缺席的症状与「用户机器没网所以驱动下不来」一模一样。
#
# 反方向同样要判：清单里登记了一项却没有任何 --add-data 会产出它（也不在 BY_BUILDER
# 里），check_layout 就会永远红在「_internal 缺 x」——真原因却是「构建命令少抄了一条」，
# 报在装配那一步会把人带偏去查 PyInstaller。两头都要对上，才对得起「清单只有一个真源」。
# ---------------------------------------------------------------------------

WIN_WORKFLOW_LABEL = "Windows workflow"
WIN_ASSEMBLER = ROOT / ".github" / "scripts" / "package_win_artifacts.py"


def win_assembler():
    """把 Windows 装配脚本当模块载进来：REQUIRED_INTERNAL / BY_BUILDER / is_dir_entry
    都只认它那一份。这里不抄第二份清单，也不自己判目录型条目——抄的那一刻就是漂移的开始。
    """
    import importlib.util
    if not WIN_ASSEMBLER.is_file():
        raise SystemExit(f"❌ 读不到装配脚本 {WIN_ASSEMBLER.relative_to(ROOT)}："
                         "第 13 条没有清单可对，这一步必须红而不是安静跳过")
    spec = importlib.util.spec_from_file_location("_win_asm", WIN_ASSEMBLER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def add_data_internal_key(src: str, dst: str) -> str:
    """``--add-data "src;dst"`` 在 onedir 里落到 ``_internal`` 的相对路径（不含前缀）。

    PyInstaller 的口径：src 是**文件**时落在 ``dst/<文件名>``，src 是**目录**时整棵树的
    内容落在 ``dst/`` 下（dst 就是它在包里的名字）；``dst="."`` 就是 _internal 根。
    目录/文件按「最后一段有没有扩展名分隔点」判——与装配清单 is_dir_entry 同一口径，
    不在这里读磁盘：CI 上 drivers/ 是这一步之后才下载的。
    """
    base = src.replace("\\", "/").rstrip("/").split("/")[-1]
    d = dst.replace("\\", "/").strip().rstrip("/")
    if "." not in base:                                  # 目录型
        return base if d in ("", ".") else d
    return base if d in ("", ".") else f"{d}/{base}"


def check_win_payload_manifest(win_text: str, asm) -> list:
    """Windows workflow 的 --add-data ↔ 装配清单 REQUIRED_INTERNAL 双向对拍。"""
    problems = []
    required = getattr(asm, "REQUIRED_INTERNAL", None)
    by_builder = getattr(asm, "BY_BUILDER", None)
    is_dir = getattr(asm, "is_dir_entry", None)
    if required is None or by_builder is None or is_dir is None:
        return [f"{WIN_ASSEMBLER.name} 里读不到 REQUIRED_INTERNAL / BY_BUILDER / "
                "is_dir_entry：第 13 条的对照面没了"]

    pairs = [_split_add_data(t) for t in _flag_values(_strip_comments(win_text), "--add-data")]
    if not pairs:
        return [f"{WIN_WORKFLOW_LABEL} 里切不出任何 --add-data："
                "构建命令被改了？第 13 条无从对拍，这一步不能算过"]
    produced = {}
    for src, dst in pairs:
        produced.setdefault(add_data_internal_key(src, dst), []).append(f"{src} → {dst}")

    for key in sorted(produced):
        if key not in required:
            problems.append(
                f"构建命令把 {key} 放进了包（--add-data {'；'.join(produced[key])}），"
                f"装配清单 REQUIRED_INTERNAL 却没有它：这个文件哪天没下载成功、被装配步骤"
                f"剔掉、或布局改了名，check_layout 一声不响，安装包照样出——"
                f"「少一个文件只少一个功能」正是本项目反复出现的那一类")
        elif not str(required[key]).strip():
            problems.append(f"REQUIRED_INTERNAL['{key}'] 没写理由：每一项都要说清"
                            "「缺了会怎样」，否则这张表迟早混进凑数的条目，真漏项也就淹在里面")
        elif "缺" not in str(required[key]):
            problems.append(f"REQUIRED_INTERNAL['{key}'] 的理由没写清后果（要出现「缺」"
                            f"字并说明症状）：现在的值是「{required[key]}」")

    for key in sorted(set(required) - set(produced)):
        if key in by_builder:
            if not str(by_builder[key]).strip():
                problems.append(f"BY_BUILDER['{key}'] 没写它由谁产出：这张表是第 13 条的"
                                "放行名单，空白理由等于放行任何名字")
            continue
        problems.append(
            f"装配清单登记了 {key}，但 {WIN_WORKFLOW_LABEL} 里没有任何 --add-data 会把它"
            f"放进包（也不在 BY_BUILDER 登记的「构建系统自产」名单里）："
            f"装配那一步会永远红在「_internal 缺 {key}」，而真原因是构建命令少抄了一条——"
            f"报在校验上会把人带去查 PyInstaller，而不是查这一行")
    for key in sorted(set(by_builder) - set(required)):
        problems.append(f"BY_BUILDER 登记了 {key}，REQUIRED_INTERNAL 里却没有它："
                        "两个名单的差集必须是 REQUIRED_INTERNAL 的子集，否则这条登记在空转")
    return problems


# ---------------------------------------------------------------------------
# 第 17 条：交付 zip 的门必须真的挂在链路上，生产者也不许往包里写非 ASCII 条目名。
#
# 这条门的起因是一次字节级对拍（2026-11-13 本机）：Windows 绿色版 zip 里 3909 个条目，
# 用 ditto 全解得开，用 Apple 的 unzip 少一个——就是根上那份 `使用说明.txt`。名字节是
# 正确的 UTF-8、0x800 标志位在本地头与中央目录都置着，这些**都不是**决定项；把中央目录
# 记录里「version made by」的高字节（create_system）单独改一个字节，红绿就互换：
# 合成包 3→0 当场解不开，真制品 0→3 当场 3909 个全落地。而 CPython 的 zipfile 在 win32
# 上把这一字节写死成 0（DOS），我们改不动标准库，也不该把「用户能不能把包装开」押在
# 某个解包工具怎么解释这一字节上。
#
# 于是这条硬门判四件事，全部是**结构**判据（不看某一次构建的运气）：
#   ① zip 的门只有一个实现（check_release_zip.py），条目名与「目标系统真解一次」两路都在
#      它里面；「这台机器没有那个工具」必须写进不合格清单，不能只印一行就算跑过——
#      静默不跑的判据比没有判据更危险（与第 11/13/15 条同一口径）。
#   ② 生产者往 zip 根上写说明时，条目名必须 ASCII、且由 GUIDE_SRC 派生（不再抄第二份
#      字面量）；安装目录里那个中文名归 NSIS 的 /oname 管，那是 NTFS 文件名，不走 zip 字节。
#   ③ 装配脚本必须在打完 zip 之后立刻调用这道门（green 的 zip 与交付的 zip 是同一份字节）。
#   ④ Mac workflow 也必须过这道门：只在 Windows 一侧验，等于承认「另一边没人问」。
# ---------------------------------------------------------------------------

RELEASE_ZIP_SCRIPT = ROOT / ".github" / "scripts" / "check_release_zip.py"
MAC_WORKFLOW_LABEL = "Mac workflow"


def release_zip_checker():
    """把交付 zip 的门当模块载进来：这一条判据只认它自己那一份实现。"""
    import importlib.util
    if not RELEASE_ZIP_SCRIPT.is_file():
        raise SystemExit(f"❌ 读不到交付 zip 的门 {RELEASE_ZIP_SCRIPT.relative_to(ROOT)}："
                         "第 17 条没有对照面，这一步必须红而不是安静跳过")
    spec = importlib.util.spec_from_file_location("_release_zip_gate", RELEASE_ZIP_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def check_release_zip_chain(asm_text: str, mac_text: str, asm=None) -> list:
    """交付 zip 的门：实现、生产者接线、Mac 侧调用三头对拍。"""
    problems = []
    try:
        gate = release_zip_checker()
    except SystemExit as exc:                       # 门不在位：这一条必须红，不能安静跳过
        return [str(exc).lstrip("❌ ")]
    src = RELEASE_ZIP_SCRIPT.read_text(encoding="utf-8", errors="replace")
    code = "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("#"))

    # ① 门的两路判据都得在：条目名字节 + 目标系统真解一次。少了任何一路，日志里
    #    看到的「✅ zip 合格」指的东西就少一半，而下一个人不会知道少的是哪一半。
    for fn, why in (("check_zip_entries", "条目名与顶层形状那一路"),
                    ("extract_with_os_tools", "拿系统自带工具真解一次并对拍那一路")):
        if not getattr(gate, fn, None):
            problems.append(f"check_release_zip.py 里没有 {fn}()（{why}没了）："
                            f"这道门的日志照样会说自己过了，判的却是半件事")
    # 「这台机器没有这个工具」必须进不合格清单。只 print 不 append 的写法会让一次
    # 「压根没验」的构建报绿——与第 11/13/15 条反复钉的同一类假绿。
    if not re.search(r"problems\.append\([^\n]*这一路没验", code):
        problems.append("check_release_zip.py 把「这台机器没有该解包工具」只印进日志、"
                        "没写进不合格清单：没验过的一次构建照样报绿")
    # 命令行那一头的退出码必须吃下「真解一次」的结果。本轮我自己写的初版就是只把条目名
    # 的 problems 接进 return，解包少落地只打印 ❌ 却退 0——workflow 判的是退出码，于是
    # 这道门在我眼皮底下假绿了一整轮。一门有两路时，每一路都要问到「它进退出码了吗」。
    if not re.search(r"problems\s*(\+=|\.extend\()\s*ep\b", code):
        problems.append("check_release_zip.py 的命令行退出码没接住真解一次那一路（ep）："
                        "Mac workflow 判的就是这一步的退出码，少落地一个文件也会照样上传")

    # ② 生产者的条目名：ASCII、且由源文件名派生，不许再抄一份字面量。
    #    asm_code 在这里就要算好：② 的 `z.write(...)` 判的是装配脚本，一开始误写成扫
    #    门脚本的 `code`，那条判据抓谁都抓不到——是离线用例的反向对照（把 GUIDE_ZIP_ENTRY
    #    换回 GUIDE_DEST 后端上一句都没有）把它抓出来的。一条从不报警的判据比漏一条更糟。
    asm_code = "\n".join(ln for ln in asm_text.splitlines() if not ln.strip().startswith("#"))
    entry = getattr(asm, "GUIDE_ZIP_ENTRY", None) if asm is not None else None
    if entry is None:
        problems.append("装配脚本里没有 GUIDE_ZIP_ENTRY："
                        "绿色版 zip 根上那份说明的条目名没有单一来源，迟早有人把中文名直接写回去")
    else:
        if not str(entry).isascii():
            problems.append(f"GUIDE_ZIP_ENTRY 不是 ASCII（{entry!r}）：Windows runner 上 Python 打的包，"
                            "中央目录 create_system 那一字节是 0（DOS），Apple 的 unzip 会把这种名字"
                            "再过一遍 OEM→Native 转换后落不下去（实测 rc=50，它说的是 disk full）")
        if asm is not None and str(entry) != PurePosixPath(str(getattr(asm, "GUIDE_SRC", ""))).name:
            problems.append(f"GUIDE_ZIP_ENTRY={entry!r} 与 GUIDE_SRC 的文件名不再是同一个："
                            "条目名又变成手抄的第二份，改一处漏一处")
    if re.search(r"z\.write\([^,]+,\s*GUIDE_DEST\s*\)", asm_code) or \
            re.search(r"z\.write\([^,]+,\s*[\"'][^\"']*[一-鿿][^\"']*[\"']\s*\)", asm_code):
        problems.append("生产者把非 ASCII 条目名写进了 zip（GUIDE_DEST 只属于安装目录那份 NTFS 文件名）："
                        "绿色版的条目名字节必须是 ASCII，中文名交给 NSIS 的 /oname")

    # ③ 装配脚本必须在打完 zip 之后立刻过这道门。看的是**调用点**的先后，不是定义点：
    #    定义顺序在文件里天生是「先 build 后 check」，拿定义顺序判就等于永远判不出回归。
    if "def check_portable_zip(" not in asm_code:
        problems.append("package_win_artifacts.py 里没有 check_portable_zip()："
                        "交付 zip 的门在装配这一步缺席，坏了的字节照样上传")
    else:
        m_build = re.search(r"^\s+build_portable_zip\(", asm_code, re.M)
        m_check = re.search(r"^\s+check_portable_zip\(", asm_code, re.M)
        if not m_check:
            problems.append("check_portable_zip 没被装配主流程调用："
                            "定义了却不跑的门等于没有门")
        elif not m_build or m_build.start() > m_check.start():
            problems.append("装配主流程里 check_portable_zip 的调用排在 build_portable_zip 之前："
                            "验的是上一轮留下的旧 zip，本轮打出来的字节没人看过")
        if not re.search(r"check_portable_zip\(\s*portable\s*,\s*problems\s*\)", asm_code):
            problems.append("check_portable_zip 的调用没把不合格项接进 problems："
                            "门红了自己往下走，zip 照样上传")
    if "check_release_zip" not in asm_code:
        problems.append("package_win_artifacts.py 没有引用 check_release_zip：这道门出现第二个实现"
                        "（各自判一份）只是时间问题")

    # ④ Mac 侧：交付包也得过同一道门。只验 Windows 就是承认 Mac 那边没人问。
    if "check_release_zip.py" not in mac_text:
        problems.append(f"{MAC_WORKFLOW_LABEL} 里没有交付 zip 的门（check_release_zip.py）："
                        "Mac 的包上传前没有任何一处验过条目名与真解一次")
    elif "--os-extract" not in mac_text:
        problems.append(f"{MAC_WORKFLOW_LABEL} 调了 zip 的门却没带 --os-extract："
                        "只判了名字，没在这台机器上真解过一次")
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


# ── 第 18 条：交付链的 pip 安装不许挂在一个外部门口 ──────────────────────────
# 2026-11-13 云端两个 run 同时红在同一行：`pip install <包名…> -i
# https://pypi.tuna.tsinghua.edu.cn/simple` 连吃 5 次 ReadTimeout，pip 说的是
# `No matching distribution found for PyQt5 (from versions: none)`。那句话完全没提
# 「源不通」，看日志的人先怀疑的是依赖写错了；而 Mac 与 Windows 两个制品同时没有——
# **一条外部镜像的可用性，就是我们交付链的单点**。这种地方大厂只有两条做法：冗余 + 有界。
# 于是安装逻辑收进 .github/scripts/pip_install.py（多源顺序回退、每源有界超时、全挂才
# 非零、逐源点名它说了什么），包名清单收进 .github/requirements/*.txt（Mac 与 Windows
# 读同一个文件，「逐行对齐」那种人肉同步就此失去立足点）。这一条判的是**结构**：任何一处
# 又退回单源、又 inline 包名、或者安装器自己变成只会说话不会拦人，都在这里响。
PIP_INSTALLER = ROOT / ".github" / "scripts" / "pip_install.py"
PIP_REQUIREMENTS_DIR = ".github/requirements"
# `pip install` / `python -m pip install` / `pip3 install` 都算直接下命令；
# `python .github/scripts/pip_install.py …` 那一行不算（它就是安装器本身）。
PIP_DIRECT = re.compile(r"(?:^|[;&|]\s*)python\d*(?:\.\d+)?\s+-m\s+pip3?\s+install\b|"
                        r"(?:^|[;&|]\s*)\bpip3?\s+install\b")


# 「读不到这个赋值」与「这个赋值的值是 None」必须能分开：拿 None 当缺位哨兵的话，
# 有人把 INDEX_SOURCES 写成 None 就会得到一句「只有 0 个源」——听起来像数错了，其实
# 是那份文件里根本没有这张表。和 NOT_PACKAGED/GENERATED_AT_BUILD 同理：报错要说得可照做。
_MISSING = object()


def _str_or(value, index: int) -> str:
    """取 (label, url) 这一项的第 index 个元素；形状不对就按空串走，由上层数量判据兜住。"""
    try:
        return str(value[index])
    except (TypeError, IndexError, KeyError):
        return ""


def _ast_literal(tree: ast.Module, name: str):
    """取模块顶层某个赋值的字面值；读不到返回 _MISSING（和「值是 None」区分开）。"""
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id == name for t in targets):
                val = _literal(node.value)
                return _MISSING if val is None and not isinstance(node.value, ast.Constant) else val
    return _MISSING


def _code_only_text(text: str) -> str:
    """把源码里的**散文**剔干净，只留真会执行的部分（注释由 ast 丢弃、docstring 显式跳过）。

    为什么不能拿整份文本 grep：本轮离线夹具第一次验第 18 条就抓到我自己的判据被骗——
    `install()` 的 docstring 里写着「一个『没有 --timeout』的假安装现场」，把真正的
    `PIP_NET_FLAGS` 删成 `()` 之后那条判据照样闭口不言、报绿。注释与 docstring 是写给下一个
    读代码的人的，不该替代码作证（第 11 条把 shell 交给 `bash -n` 真解析器是同一个道理）。
    """
    tree = ast.parse(text)
    for node in list(ast.walk(tree)):
        body = getattr(node, "body", None)
        if isinstance(body, list):
            while (body and isinstance(body[0], ast.Expr)
                   and isinstance(body[0].value, ast.Constant)
                   and isinstance(body[0].value.value, str)):
                body.pop(0)                     # 逐个剔掉函数/模块开头的 docstring
    return "\n".join(ast.unparse(stmt) for stmt in tree.body)


def check_pip_source_resilience(workflows: tuple = None, installer_text: str = "") -> list:
    """pip 安装的冗余判据：workflow 不许绕开安装器，安装器不许只有一个源（空=合格）。

    两份清单做成参数是为了**判据自己可被反向对照**：离线用例要能喂一份「改回 -i 单源」的
    假 workflow 与一份「源被删到一条」的假安装器，并要求它们必须红。一条从不报警的判据
    比漏一条更糟——它会让后面的人以为这一项被验过。
    """
    problems: list = []
    codes: dict = {}
    for rel in (WORKFLOWS if workflows is None else workflows):
        path = Path(rel) if Path(rel).is_absolute() else ROOT / rel
        if not path.is_file():
            problems.append(f"{rel} 读不到：第 18 条在这份 workflow 上没跑（登记了却没文件）")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        label = path.name
        # a) 不许再出现直接的 pip install：那一行等于把交付链重新挂到单个索引源上
        #    b) 装没装安装器、-r 指的是哪份清单——两路都只认 run 步里真会执行的行。
        #    拿整份 yml grep 的话，一句注释「为什么走 pip_install.py 而不是直接 pip」就能
        #    顶替真的调用（假绿），也能顶替真的 -r（把不存在的清单说成在位）；本轮的注释里
        #    就真有这两句话，夹具第一版就差点点不亮。
        kept_lines = []
        for name, base, shell, script in run_blocks_in_workflow(text):
            for k, ln in enumerate(script.splitlines(), 1):
                body = ln.strip()
                if not body or body.startswith("#"):
                    continue
                kept_lines.append(body)
                if PIP_DIRECT.search(body):
                    problems.append(
                        f"{label}「{name}」第 {base + k} 行直接调用 pip（{body[:120]}）："
                        "绕开 .github/scripts/pip_install.py 就等于绕开多源回退与有界超时——"
                        "2026-11-13 两个 run 同时没有制品，红就落在这样的一行上")
        codes[rel] = "\n".join(kept_lines)
        calls = re.findall(r"pip_install\.py[^\n]*", codes[rel])
        if not calls:
            problems.append(f"{label} 没有调用 pip_install.py：这一路的依赖安装没人管冗余，"
                            "镜像一超时就是整个平台没有交付")
        # c) -r 指向的清单必须在仓库里、落在 requirements/ 下、非空
        for call in calls:
            for req in re.findall(r"-r\s+(\S+)", call):
                # 先分清「路径不是静态可确认的」与「静态路径但文件不在」：把后者当成前者，
                # 或反过来，都会把人支去错的方向——一句「清单不在仓库里」的去补文件，而真正
                # 要改的是那一行还带着 `${{ }}` / `$变量` / 通配。GH_PLACEHOLDER 是切块阶段
                # 就替换出来的记号，所以这里必须一起认（见它的定义处注释）。
                dynamic = [t for t in (GH_PLACEHOLDER, "${{", "$", "`", "*", "?")
                           if t in req]
                if dynamic:
                    problems.append(f"{label}：-r 的清单路径是运行时才拼出来的（{req}，"
                                    f"含 {sorted(set(dynamic))}），判据没法确认文件在不在——"
                                    "清单与 workflow 一起入库就写死路径")
                    continue
                rp = ROOT / req
                if not rp.is_file():
                    problems.append(f"{label} 引用的依赖清单 {req} 不在仓库里：安装器会在这一步红，"
                                    "但红在构建中途比红在门禁晚一步")
                    continue
                if rp.parent.name != "requirements":
                    problems.append(f"{label}：依赖清单 {req} 没放在 {PIP_REQUIREMENTS_DIR}/ 下："
                                    "清单落点一散开，「Mac 与 Windows 是不是同一份」就没人判得了")
                    continue
                pkgs = [x.strip() for x in rp.read_text(encoding="utf-8", errors="replace").splitlines()
                        if x.strip() and not x.strip().startswith("#")]
                if not pkgs:
                    problems.append(f"{label}：依赖清单 {req} 里一个包都没有——空清单装不出依赖，"
                                    "产物缺包只在用户点击功能时才崩")
    # d) 两个 b2bcollect 入口必须读同一份清单。这条的存在理由就是原先那句注释「清单与
    #    Windows 版逐行对齐」：人肉同步换成文件级同步之后，一旦哪天两边各指一份，当场响。
    per_b2b = {rel: set(re.findall(r"pip_install\.py[^\n]*?-r\s+(\S+)", c))
               for rel, c in codes.items() if "build-b2bcollect" in Path(rel).name}
    if len(per_b2b) == 2 and all(per_b2b.values()):
        if len({frozenset(v) for v in per_b2b.values()}) > 1:
            problems.append("Mac 与 Windows 的 b2bcollect 读的不是同一份依赖清单"
                            f"（{ {k: sorted(v) for k, v in per_b2b.items()} }）：两份清单就要靠人"
                            "逐行对齐，加一个包漏一处时构建全绿、产物缺包")
    # e) 安装器本体：冗余、有界、不挂住、失败必须进退出码
    if not installer_text:
        if not PIP_INSTALLER.is_file():
            problems.append(f"{_rel(PIP_INSTALLER)} 不在位：pip 安装没有实现，"
                            "上面那些引用它的 workflow 全都跑不起来")
            installer_text = ""
        else:
            installer_text = PIP_INSTALLER.read_text(encoding="utf-8", errors="replace")
    if installer_text:
        try:
            tree = ast.parse(installer_text)
            code = _code_only_text(installer_text)
        except SyntaxError as exc:
            problems.append(f"pip_install.py 第 {exc.lineno} 行语法过不去（{exc.msg}）："
                            "结构判据读不出它，这一步不能当成过了")
            return problems
        srcs = _ast_literal(tree, "INDEX_SOURCES")
        if srcs is _MISSING:
            problems.append("pip_install.py 里读不到 INDEX_SOURCES 的字面值：源清单必须是写死的"
                            "元组字面量——运行时拼出来的话，这一条门连判什么都没了（判据只认"
                            "ast 里的字面值，不认注释与 docstring，见 _code_only_text）。"
                            "读不到就到此为止：再报一句「只有 0 个源」会把人引去数源，"
                            "而真正要修的是那张表没了")
            srcs = None
        else:
            srcs = [(_str_or(x, 0), _str_or(x, 1)) for x in srcs] \
                if isinstance(srcs, (tuple, list)) else []
        if srcs is not None:
            if len(srcs) < 2:
                problems.append(f"pip_install.py 的 INDEX_SOURCES 只有 {len(srcs)} 个源："
                                "一条外部镜像的可用性又一次变成交付链的单点（今天那种红会原样回来）")
            urls = [u for _, u in srcs if u]
            if len(set(urls)) != len(urls):
                problems.append("pip_install.py 的索引源里有重复 URL：看着两条路其实是一条")
        flags = _ast_literal(tree, "PIP_NET_FLAGS")
        if flags is _MISSING:
            problems.append("pip_install.py 读不到 PIP_NET_FLAGS 的字面值：给 pip 的网络参数必须是"
                            "写死的元组（拿它当变量拼来拼去，这一条就退回到读注释判）")
        else:
            pairs = list(flags)

            def _bounded(flag: str) -> bool:
                if flag not in pairs:
                    return False
                nxt = pairs.index(flag) + 1
                return nxt < len(pairs) and str(pairs[nxt]).isdigit()

            _lost = [f for f in ("--timeout", "--retries") if not _bounded(f)]
            if _lost:
                # 一条报出来点名缺了哪几个：两处缺的是同一行的同一个修法，拆成两条会让人以为
                # 是两个缺陷（本条门的其他几路都是一缺陷一报，口径要一致）。
                problems.append(f"pip_install.py 没有把 {' / '.join(_lost)} 带数字传给 pip："
                                "单个连接自己就是无界的，外层超时只是兜底（今天那种整段 "
                                "ReadTimeout 就是把时间花在一个不会自己停的连接上）")
        if not re.search(r"timeout\s*=\s*PER_SOURCE_TIMEOUT", code):
            problems.append("pip_install.py 的 subprocess.run 没有 timeout=PER_SOURCE_TIMEOUT："
                            "挂住的源会把 job 一直拖到 Actions 超时，日志里一句原因都留不下")
        if not re.search(r"stdin\s*=\s*subprocess\.DEVNULL", code):
            problems.append("pip_install.py 没接 stdin=DEVNULL：pip 一旦觉得需要答复就回头问人，"
                            "后台那条路上没人答，于是不结束（取制品那一链刚红过同一件事）")
        if not re.search(r"if\s+len\(INDEX_SOURCES\)\s*<\s*2", code):
            problems.append("pip_install.py 少了一道自守卫（源少于两条就不干）：有人把源删到一条时，"
                            "workflow 那边完全看不出来")
        if not re.search(r"if\s+problems:\s*\n(?:.*\n)*?\s+return\s+1", code):
            problems.append("pip_install.py 全挂时没有 return 1：一条只会逐源打印失败、退出码照样 0"
                            "的安装器，等于把「没装上」交给后面每一步去猜")
        if not re.search(r"if\s+not\s+attempted", code):
            problems.append("pip_install.py 没有「一个源都没试」这一路的兜底：sources 传空或被改成"
                            "空表时，循环一次都不跑、返回的问题清单也就是空的——「没装」会被当成"
                            "「装上了」，这条链上最坏的失败样子就是这个样子")
    return problems


# ── 第 18 条的 e 路：清单收拢成一份文件之后，「抄漏一个包」必须当场响 ─────────
# 依赖清单从两份 workflow 的 inline 包名收拢成 .github/requirements/b2bcollect.txt 之后，
# 抄漏的位置从 workflow 挪到了清单里，症状却一模一样：pip 装成功、PyInstaller 全绿
# （它对缺的包只打一行 warning，不拦构建），制品到了用户机上、点到那个功能才崩——用户
# 报上来的那两条实机栈（Mac 打开即崩、Windows 重启后崩）都长着这张脸。所以清单不能只
# 靠「我记得写全了」，要和**进包源码里真的 import 到的第三方名**对拍一次。
# 方向只有一个：代码里有 import、清单里没有 = 红。反过来不红——清单里写着的 pyinstaller
# 是构建工具、xlrd 是被 pandas 间接读老 xls 的，按「没被 import 就删掉」去判会报一堆
# 修不掉的假红，而假红的下场是这条门被人整条关掉（本轮已经栽过两次，不再栽第三次）。
B2B_REQUIREMENTS = ".github/requirements/b2bcollect.txt"

# import 名 ≠ 发行包名的那些。这不是我们的选择，是 PyPI 的历史包袱：pip 装的名字和代码
# 里能 import 的名字对不上。每一项都写明「装哪个发行包、为什么对不上」，下一个人往这张
# 表里加东西时才知道自己加的是什么性质的一条。
IMPORT_TO_DIST = {
    "PIL": ("pillow", "图像库 import 名是包内的 PIL，发行名叫 pillow"),
    "bs4": ("beautifulsoup4", "BeautifulSoup 的发行名带版本号，import 名不带"),
    "websocket": ("websocket-client", "客户端发行名带 -client，模块名只到 websocket"),
    "pkg_resources": ("setuptools", "读包元数据的库随 setuptools 提供，没有叫 pkg_resources 的发行包"),
}

# 由清单里**另一个发行包连带提供**的 import：不单独登记，但把连带关系写在案上。
# 判据会确认「它靠的那个发行包还在清单里」——哪天 PyQt5 被删，sip 这类立刻没着落，
# 这一条就当场红；不写这张表的话，那种时候报出来的是「sip 没登记」这种没法照做的话。
IMPORT_FROM_LISTED = {
    "sip": ("PyQt5", "sip 是 PyQt5 的 wheel 里带的 CPython 绑定，PyPI 上没有可单装的 sip"),
}


def _dist_key(name: str) -> str:
    """发行包名的比较键：PEP 503 的归一化（大小写、`-`/`_`/`.` 都算同一个名字）。"""
    return re.sub(r"[-_.]+", "-", str(name)).lower()


def _requirement_dists(path: Path) -> set:
    """清单里的发行包名（剔注释与空行，带上限者与 extras 之前的那一段）。"""
    out = set()
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.add(_dist_key(re.split(r"[<>=!;\[\s]", line, maxsplit=1)[0]))
    return out


def _packaged_source_files(externals: tuple = None) -> list:
    """真正会进包的那批源码：本目录模块 + 打包入口 + 跨目录模块。

    口径直接复用 `module_manifest()` / `local_modules()`：这张「谁进包」的表已经有四处
    消费方，再在这里抄一遍模块清单，就等于把本轮刚立的那条「单一真源」当场破掉。
    入口脚本要单独补：`local_modules()` 按「它不是 hidden-import 项」把它排除了
    （NOT_PACKAGED 说的是「不进那张登记表」），可它明明是 PyInstaller 的位置参数。
    """
    if externals is None:
        externals, _ = module_manifest()
    files = [BUILD_DIR / f"{name}.py" for name in local_modules()]
    files += [BUILD_DIR / f"{name}.py" for name in ENTRY_SCRIPTS]
    files += [ROOT / rel for rel in externals]
    return [p for p in files if p.is_file()]


def _top_level_imports(path: Path) -> set:
    """一个源文件里所有**顶层** import 名（`from a.b import c` 取 a，相对导入不参与）。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
    except (SyntaxError, ValueError):
        raise SystemExit(f"{path} 语法过不去：第三方 import 对拍读不到东西，"
                         "这一步不能安静跳过（读不到就是没验）")
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if not node.level and node.module:
                names.add(node.module.split(".")[0])
    return names


def _rel(p: Path) -> str:
    """报告里写路径：仓库内写成相对路径（短、可比），仓库外原样写绝对路径。

    仓库外不是异常——离线用例就是把夹具建在系统临时目录里喂进来的（G 节的 `_ztmp`、
    第 17 条的 `_t17` 都是同一个手法）。用 `p.relative_to(ROOT)` 裸写的话，夹具一放到仓库外
    就先抛一个 ValueError，报出来的是「对拍没跑成」而不是它本来要判的那件事。
    """
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def check_requirements_coverage(requirement_files: tuple = None,
                                source_files: tuple = None) -> list:
    """进包源码 import 到的第三方包，必须都能在依赖清单里找到（空列表=合格）。"""
    problems: list = []
    req_paths = tuple(ROOT / rel for rel in (requirement_files or (B2B_REQUIREMENTS,)))
    dists: set = set()
    for rp in req_paths:
        if not rp.is_file():
            problems.append(f"依赖清单 {_rel(rp)} 不在位：没有清单就没法对拍，"
                            "这一路不能当成「过了」")
            continue
        dists |= _requirement_dists(rp)
    files = list(source_files) if source_files is not None else _packaged_source_files()
    if not files:
        problems.append("第三方 import 对拍一个源文件都没扫到：进包源码的口径没了，"
                        "这时候报「全部覆盖」是自欺")
        return problems
    if source_files is None:
        for name in ENTRY_SCRIPTS:
            if not (BUILD_DIR / f"{name}.py").is_file():
                problems.append(f"打包入口 {name}.py 不在位：它对拍就少扫一个文件，"
                                "而入口恰恰是 import 最密的那一份")
    seen: dict = {}
    for path in files:
        for name in _top_level_imports(path):
            seen.setdefault(name, []).append(path)
    stdlib = set(sys.stdlib_module_names)
    repo_names = {p.stem for p in files}
    for name in sorted(seen):
        where = ", ".join(sorted({_rel(x) for x in seen[name][:3]}))
        if name in stdlib or name in repo_names:
            continue
        # 仓库里的**目录包**（enf_clean 这类随 --add-data 一起打进包的源码目录）不是第三方
        # 依赖；判据是「有没有 __init__.py」，不是「有没有同名的目录」——同名目录可能只是
        # dist/、debug/ 这种压根不该被 import 的东西，放行就等于把这一路又开一个洞。
        if (BUILD_DIR / name / "__init__.py").is_file() or (ROOT / name / "__init__.py").is_file():
            continue
        dist = IMPORT_TO_DIST.get(name, (name, ""))[0]
        if _dist_key(dist) in dists:
            continue
        provider = IMPORT_FROM_LISTED.get(name)
        if provider and _dist_key(provider[0]) in dists:
            continue
        if provider:
            problems.append(f"{name}（{where}）靠 {provider[0]} 连带提供，"
                            f"但 {provider[0]} 不在依赖清单里：连带关系断了，"
                            "这个 import 在产物里就是运行时 FileNotFoundError")
            continue
        problems.append(f"进包源码 import 了第三方包 {name}（见 {where}），"
                        f"但依赖清单里没有它（发行名 {dist}）：pip 与 PyInstaller 都不会拦这一步"
                        "（构建照样全绿），缺的那一只到用户点上那个功能才崩——"
                        f"要么加进 {B2B_REQUIREMENTS}，要么在 IMPORT_TO_DIST/"
                        "IMPORT_FROM_LISTED 里写明它凭什么不算漏")
    return problems


# ── 第 19 条：注释里指路的「某套件 第 N 节」必须真指到那套件里存在的小节 ──────────
# 由来（2026-11-13 本机自审）：本轮把「择路不许跟着本机历史走」这条口径写进 tool_config
# 与 runtime_paths 时，两边都补了「对拍判据见 test_data_dir_offline.py 第 10~13 节」，而
# 那个文件当时只有 1~9 节——「判据在哪一节」这句话本身就是纸上纪律：读注释的人按节号找
# 过去，找不到也只会以为是自己没翻到，而真正缺的是那份从没写出来的对拍（_page_tail、
# _has_any_contact、CONTACT_RETRY_MAX、results_live 四个函数在全部离线套件里 0 覆盖，正
# 是用户报「以前能采到、现在一直是 0」那条链的守卫缺口）。
# 这条判据把「指路」与「路存在」钉成同一件事：它不判注释写得好不好，只判那句「见第 N 节」
# 现在能不能真的走过去。改小节编号时必须两边一起改，这与第 10 条（同名拷贝分叉）同族。
# 分隔符三式都得收下，因为它们在同一份注释里都出现过，而漏一式的后果是**漏报**：
#   「第 1、2、3、8 节」并列   「第 10~13 节」区间   「第 10、12 节」混着写
# 区间的写法尤其不能放过：本轮那句悬空引用原文就是「第 10~13 节」，而当时套件只到第 9 节
# ——只认单号与并列的门对这一句会安静放行，恰好在最需要它的那一次失灵。
SECTION_REF_RE = re.compile(
    r"([A-Za-z0-9_]{4,}\.py)(?:(?!\.py)[^。；\n\"]){0,12}?第\s*"
    r"([0-9０-９]+(?:\s*[、,，~～\-–]\s*[0-9０-９]+)*)\s*节")
SECTION_HEAD_RE = re.compile(r"(?:═══|─{2,})\s*([0-9]+|[A-Z])\s*[\.、]")
_FW_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
_SECTION_ITEM_RE = re.compile(r"([0-9０-９]+)\s*([、,，~～\-–]?)\s*")


def _expand_section_items(raw: str) -> list:
    """把「1、10~13，20」这类写法展平成单个节号字符串列表（区间两端都含）。

    倒着写的区间（``13~10``）不猜作者想说什么，只把两端都并入待验集合：这样任何一端不
    存在都会照旧报红，而「安静当成只有一个数」会把笔面漏掉的那一节盖住。
    """
    toks = [(int(a.translate(_FW_DIGITS)), b)
            for a, b in _SECTION_ITEM_RE.findall(raw) if a]
    out, i = [], 0
    while i < len(toks):
        num, sep = toks[i]
        if sep in ("~", "～", "-", "–") and i + 1 < len(toks):
            lo, hi = sorted((num, toks[i + 1][0]))
            out.extend(str(x) for x in range(lo, min(hi, lo + 40) + 1))
            i += 2
        else:
            out.append(str(num))
            i += 1
    return out


# 扫描范围：交付目录、本机交付链脚本与门禁脚本。为什么不扫整仓：仓库根下有大量一次性
# 脚本与文档，它们不参与交付，把它们的注释也纳进来只会让这条门在无关文本上误报，
# 而误报的代价和漏报一样——人开始跳过这一步。
SECTION_REF_DIRS = tuple(list(SHARED_SOURCE_DIRS) + ["debug", ".github/scripts", ".github/workflows"])
SECTION_REF_SUFFIXES = (".py", ".sh", ".yml", ".md")


def _ref_scan_text(text: str) -> str:
    """把注释折回一行：折行的「见某回归套件 ／ 第 11 节」是同一条指路。

    中文注释在源码里按 79 列折行是常态，逐行匹配会把「折了行」当成「没引用」放过，
    而放过的方向恰好是漏报——漏报比没有判据更危险（它让人以为这一项被验过）。
    """
    return " ".join(re.sub(r"^\s*#\s?", "", ln) for ln in text.splitlines())


def _load_ref_texts() -> tuple:
    """默认扫描范围：交付目录 + 交付链脚本 + workflow，按 git 索引取（不 walk 文件系统）。

    为什么用索引而不是 glob：第 7 条已经证明「本机有、检出没有」是真实存在的一种坏，
    指路注释如果只在本机那份没提交的草稿里，CI 检出的树里根本没有那句话，判它没有意义；
    反过来，索引里有而磁盘上读不到，必须**报一条问题**而不是安静少扫一份——扫不到的
    那一格里如果正好有悬空引用，它就是这条门唯一没看守的地方。
    """
    problems, texts = [], {}
    index, _head = _tracked_sets()
    for rel in sorted(index or []):
        if not any(rel == d or rel.startswith(d + "/") for d in SECTION_REF_DIRS):
            continue
        if "/dist/" in f"/{rel}" or rel.endswith("/build"):
            continue                      # 构建产物里的注释是上一版的，不该拿来判今天
        if not rel.endswith(SECTION_REF_SUFFIXES):
            continue
        try:
            texts[rel] = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            problems.append(f"{rel}：读不进去，这一条指路没法核对（读不到≠不用判）")
    return texts, problems


def _load_ref_suites(texts: dict) -> dict:
    """被指路的套件本体：优先用扫到的文本，再按目录补齐（注释可以指到同目录之外的套件）。"""
    suites = {}
    for rel, text in texts.items():
        name = rel.rsplit("/", 1)[-1]
        if name.startswith(TEST_PREFIX):
            suites.setdefault(name, text)
    for d in SECTION_REF_DIRS:
        dp = ROOT / d
        if dp.is_dir():
            for p in sorted(dp.glob(f"{TEST_PREFIX}*.py")):
                try:
                    suites.setdefault(p.name, p.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    pass                  # 上一段已经把读不进去的那份报过，这里不重复刷
    return suites


def check_section_pointer_references(texts=None, suites=None) -> list:
    """texts: {相对路径: 源码} 待扫的文本；suites: {文件名: 源码} 被指路的套件本体。

    两个参数都留出注入面：离线夹具不许依赖本机仓库状态（第 16/17 条同一条纪律），
    否则「反向对照」这一步在别的机器上就跑不出来。
    """
    problems = []
    if texts is None:
        texts, problems = _load_ref_texts()
        # 登记的扫描目录必须真的在位：目录改名或被删之后，这一条会「扫到 0 份文本」并报
        # ✅，而 ✅ 的分量和没有这条门不一样——AB 节量过同一格（扫 0 个文件的「全覆盖」是自欺）。
        for d in SECTION_REF_DIRS:
            if not (ROOT / d).is_dir():
                problems.append(f"{d}：第 19 条登记了这份扫描目录，可它不在了（这一路安静空转）")
        if len(texts) < 40:
            problems.append(f"指路扫描只读到 {len(texts)} 份文本：口径大概已经和交付目录脱节，"
                            f"这时报「全部走得通」没有任何担保（至少应有交付三目录 + 脚本 + workflow）")
    if suites is None:
        suites = _load_ref_suites(texts)
    for rel, text in sorted(texts.items()):
        if rel.rsplit("/", 1)[-1].startswith(TEST_PREFIX):
            continue          # 套件自己的注释里写「第 13 节」是内部编号，不指路
        prose = _ref_scan_text(text)
        for m in SECTION_REF_RE.finditer(prose):
            suite, raw_nums = m.group(1), m.group(2)
            wanted = _expand_section_items(raw_nums)
            body = suites.get(suite)
            if body is None:
                problems.append(f"{rel}：注释把判据指到 {suite}，但交付目录里没有这份文件"
                                "（那句「见某某套件」现在谁也走不通）")
                continue
            labels = set(SECTION_HEAD_RE.findall(body))
            digits = sorted(int(x) for x in labels if x.isdigit())
            if not digits:
                problems.append(f"{rel}：注释说判据在 {suite} 第 {'、'.join(wanted)} 节，"
                                f"但那套件的节是用字母编的（{sorted(labels)[:6]}），"
                                "数字指路在那里根本不存在")
                continue
            miss = [n for n in wanted if n not in labels]
            if miss:
                problems.append(
                    f"{rel}：注释说判据在 {suite} 第 {'、'.join(miss)} 节，"
                    f"而那套件现有的数字小节是 {digits}——那句指路走过去不存在"
                    "（「判据见第 N 节」写成一句走不通的话，就等于那份对拍从没存在过）")
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

    # 再验同名拷贝：清单齐、链路闭合，也可能「生效的那一份是旧的」（第 10 条）。
    dup = check_shared_source()
    if dup:
        bad += 1
        print("❌ 交付目录里同名模块分叉且未在案（哪一份生效由 import 顺序决定）：")
        for p in dup:
            print(f"   · {p}")
            print(f"::error::同名模块拷贝：{p}")
    else:
        print(f"✅ 同名模块拷贝：{'/'.join(SHARED_SOURCE_DIRS)} 里同名的源码"
              f"要么字节一致，要么在 DIVERGENT_VENDORED 登记了理由")

    # 交付链上那几份手写脚本的文案卫生（同第 9 条，判据共用一个函数）
    hygiene = check_script_hygiene()
    if hygiene:
        bad += 1
        print("❌ 交付链 shell 脚本不合格（`$VAR` 紧跟中文会把报错那行本身弄坏）：")
        for p in hygiene:
            print(f"   · {p}")
            print(f"::error::shell 文案：{p}")
    else:
        print(f"✅ 交付链 shell 脚本：{len(HYGIENE_SCRIPTS)} 份文案卫生合格")

    # 交付链上所有手写 shell 的语法（第 11 条）：判据是 bash 自己的解析器，
    # 不是又一条「我猜到了哪几种写法会坏」的正则。
    syntax = check_shell_syntax()
    if syntax:
        bad += 1
        print("❌ 交付链 shell 语法不合格（语法坏掉的步骤连第一行都跑不到，判据和日志都没机会出场）：")
        for p in syntax:
            print(f"   · {p}")
            print(f"::error::shell 语法：{p}")
    else:
        print(f"✅ 交付链 shell 语法：{len(SHELL_SOURCES)} 份脚本 + "
              f"{len(WORKFLOWS)} 份 workflow 里的 bash 步全部通过 bash -n")

    # 对外部工具文本输出的格式假设必须带出处（第 14 条）：第 11 条管「这段 shell 能不能
    # 被解析」，管不到「解析出来的那个值是不是按想象中的格式取的」——后者本轮在云端红过
    # 一次，而且本机全绿着红的那一次，代价是一整个交付没有安装包。
    prov = check_external_output_provenance()
    if prov:
        bad += 1
        print("❌ 交付链 shell 里有按外部工具输出取值的 grep 没写明这批字节的出处"
              "（想象中的格式=迟早把编好的产物拦在门外）：")
        for p in prov:
            print(f"   · {p}")
            print(f"::error::外部输出格式出处：{p}")
    else:
        print("✅ 外部工具输出格式假设：每一处按文本取值的地方都在近旁写明了那批字节的来源")

    # -e 下的静默退出（第 15 条）：第 11 条保证「这段 shell 能被解析」，保证不了
    # 「解析出来的一段跑到一半被 set -e 安静带走」。本轮红的正是后者，而且红在**最后一道门**。
    hazards = check_shell_errexit_hazards()
    if hazards:
        bad += 1
        print("❌ 交付链 shell 里有会让脚本「跑一半就没声音」的写法（裸赋值把非零交给 set -e、"
              "装了 ERR 陷阱却没开 -E／-e、或整段 set +e 把保护关掉；后面的门不会跑，"
              "日志里也不会有第二句话）：")
        for p in hazards:
            print(f"   · {p}")
            print(f"::error::errexit 静默退出：{p}")
    else:
        print("✅ errexit 静默退出：-e 生效的 shell 段里没有「正常也会非零」的裸赋值，"
              "装了的 ERR 陷阱也都带着 -E 生效，整段关保护的那种侥幸一处也没有"
              f"（{len(SHELL_SOURCES)} 份脚本 + {len(WORKFLOWS)} 份 workflow 的 bash 步）")

    # 大小写·变体不敏感文件系统上的同名路径（第 16 条）：这一条管的是「同一份仓库在不同
    # 平台上不是同一棵树」。云端 Linux 检得出两份、开发机 mac 只有一份，且留哪一份取决于
    # 写入顺序——这类差异不会让 CI 变红，只会让「我这边是好的」变成一句真话加一个坏包。
    collide = check_case_colliding_paths()
    if collide:
        bad += 1
        print("❌ 仓库里有只在大小写/Unicode 变体上不同的入库路径（mac 与 Windows 检出时"
              "是同一个文件）：")
        for p in collide:
            print(f"   · {p}")
            print(f"::error::同名路径撞车：{p}")
    else:
        print("✅ 同名路径撞车：入库路径两两在大小写不敏感文件系统下也不会撞到同一个文件")

    # 交付链 Python 脚本与子进程/控制台之间的编码（第 12 条）：本机是 UTF-8，Windows
    # runner 的控制台代码页是 cp1252——凡是把编码交给环境决定的写法，都在这里拦一次，
    # 而不是等它在云端炸开一条看不到根因的 UnicodeError。
    enc = check_text_io_encoding()
    if enc:
        bad += 1
        print("❌ 交付链 Python 脚本的编码没有写死（换一台镜像换一种结果）：")
        for p in enc:
            print(f"   · {p}")
            print(f"::error::子进程与控制台编码：{p}")
    else:
        print(f"✅ 交付链 Python 脚本编码：{len(io_scripts())} 份脚本"
              f"（{IO_SCRIPTS_DIR}/*.py）的 input/text/encoding 与控制台写入都写定了 UTF-8")

    # Windows 装配清单与构建命令互相钉死（第 13 条）：第 1 条只管「运行时要什么 →
    # 构建命令抄了没有」，管不到反方向——构建命令放了进包的文件，装配校验却不知道。
    _asm = None
    try:
        _asm = win_assembler()
        _wf = CALLERS[WIN_WORKFLOW_LABEL]
        if not _wf.is_file():
            win_problems = [f"读不到 {_wf.relative_to(ROOT)}：第 13 条的构建命令没了对照面，"
                            f"这一步必须红而不是安静跳过"]
        else:
            win_problems = check_win_payload_manifest(
                _wf.read_text(encoding="utf-8", errors="replace"), _asm)
    except SystemExit as exc:          # 装配脚本不在位：这条判据不能安静跳过
        win_problems = [str(exc)]
    if win_problems:
        bad += 1
        print("❌ Windows 装配清单与构建命令的 --add-data 对不上（单向清单等于没有清单）：")
        for p in win_problems:
            print(f"   · {p}")
            print(f"::error::Windows 装配清单对拍：{p}")
    else:
        print(f"✅ Windows 装配清单对拍：REQUIRED_INTERNAL 与构建命令的 --add-data "
              f"双向一致（装配校验判的就是构建命令真正放进包的那些文件）")

    # 交付 zip 的门在链路上（第 17 条）：字节级对拍定出的那个 create_system 教训——
    # 「构建全绿、包照样解不开」的第三种形状。判的是结构：门有几路、生产者接线没有、
    # Mac 侧调没调，任一处塌了都在这里响，而不是等用户的解压工具替我们响。
    try:
        _zip_problems = check_release_zip_chain(
            WIN_ASSEMBLER.read_text(encoding="utf-8", errors="replace"),
            Path(CALLERS[MAC_WORKFLOW_LABEL]).read_text(encoding="utf-8", errors="replace"),
            _asm)
    except (OSError, SystemExit) as exc:            # 任一份对照文件不在位：必须红，不安静跳过
        _zip_problems = [str(exc)]
    if _zip_problems:
        bad += 1
        print("❌ 交付 zip 的门不合格（条目名字节/真解一次/接线/两侧调用）：")
        for p in _zip_problems:
            print(f"   · {p}")
            print(f"::error::交付 zip 的门：{p}")
    else:
        print("✅ 交付 zip 的门：条目名与真解一次两路都在、生产者用派生的 ASCII 条目名并"
              "在打完 zip 后立刻调用、Mac workflow 也带 --os-extract")

    # pip 安装的冗余（第 18 条）：2026-11-13 两个 run 同时没有制品，红在一行
    # `pip install … -i <单一镜像>` 上——外部依赖的可用性不是我们能控制的，能控制的是
    # 「它失效时这条链还有第二条路，且每条路都有尽头」。这一条把那句话变成结构判据。
    pip_problems = check_pip_source_resilience()
    # 同一族的另一半（18e）：清单从两份 workflow 的 inline 包名收成一份文件之后，
    # 「抄漏一个包」的位置变了、症状没变。这一路和上面的冗余路各报各的，因为修法是
    # 两件事：一个改源与超时，一个改清单内容；混成一条会让人只改一半。
    # 读不进源码必须是红、不能是跳过：这一路靠 ast 解析整棵进包源码，一份文件语法过不去
    # 就少扫一份，安静跳过的下场是「✅ 覆盖完全」底下藏着一个从没被扫过的模块。
    try:
        pip_problems += check_requirements_coverage()
    except (OSError, ValueError, SystemExit) as exc:
        pip_problems.append(f"第三方 import 对拍没跑成：{exc}")
    if pip_problems:
        bad += 1
        print("❌ pip 安装不合格（单点索引源 / 绕开安装器 / 清单缺席 / 清单漏包）：")
        for p in pip_problems:
            print(f"   · {p}")
            print(f"::error::pip 安装与依赖清单：{p}")
    else:
        print("✅ pip 安装冗余：四份 workflow 都走 pip_install.py，多源回退 + 每源有界超时 + "
              "全挂才非零；b2bcollect 两个平台读同一份依赖清单（不再是两份各自抄写的包名），"
              "且那份清单覆盖进包源码 import 到的每一个第三方包")

    # 注释里指路的「某套件 第 N 节」必须走得通（第 19 条）：这一条判的不是文案，是
    # 「那份对拍存不存在」。本轮写出 13 处这样的指路而对应小节压根没写，用户报的
    # 「以前能采到、现在一直是 0」那条链上四个函数就是那时候没有守卫的。
    section_refs = check_section_pointer_references()
    if section_refs:
        bad += 1
        print("❌ 交付链源码/脚本/workflow 里有指路注释走到不存在的小节（那句「见第 N 节」"
              "现在谁也走不通＝那份对拍从没存在过）：")
        for p in section_refs:
            print(f"   · {p}")
            print(f"::error::指路节号对拍：{p}")
    else:
        print("✅ 指路节号对拍：注释里每一句「某套件 第 N 节」都能在那套件里走到真实小节")

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
          "依赖链闭合、进包源码都已在提交里、同名拷贝分叉都在案、交付链 shell 都过了解析器、"
          "shell 里没有被 set -e 静默带走的裸赋值、装了的 ERR 陷阱也都带着 -E、"
          "入库路径在大小写不敏感文件系统上也不撞车、"
          "交付链脚本的编码都写死在脚本里、Windows 装配清单与构建命令互相钉死、"
          "交付 zip 的门两侧都在链路上、"
          "注释里每一句指路节号都走得通")
    return 0


if __name__ == "__main__":
    sys.exit(main())
