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
 20 交付链 shell 里每一次 curl 都必须**自己会停**，写进文件的字节必须**跨次能用**：
     2026-11-13 本机取制品连着两趟都是外层 timeout 把脚本杀掉（rc=124），日志一句原因都没
     留。字节级拆开是三件事：curl 只在连不上时自己退（实测单连接 2 MiB 用 182.98 秒 =
     11.5 KB/s，它认为这完全正常）；限速挂在单条连接而非链路上（实测四并发 166 秒拿到
     8 MiB ≈ 50 KB/s，所以给大文件写 max-time 既救不了慢还会弄死正常的慢）；zip 落在 mktemp
     出来的工作目录里、被 trap EXIT 删掉，于是每次重跑都从第 0 字节开始——跑一百次也不会有
     进展。三判据：20a 每次 curl 要么有总时限（--max-time/-m）要么有停顿时限（--speed-limit
     + --speed-time 两个都要）；20b 写进文件的目标不许来自同一份脚本里 mktemp 赋出的变量
     （-o - 与 /dev/null 豁免）；20c 要么自己会接着要（-C/--continue-at/-r/--range），要么近旁
     （40 行内）有完整性收口（sha256/certutil/check_release_zip/真解一次）——NSIS 那一步走
     后者，续传在那儿反而是错的（每轮换镜像重下并核哈希）。同一趟还在派发脚本上抓到两条裸
     curl：派发失败长得和成功一模一样，人就开始等一个从没起来过的 run。

 21 之后（21 Qt 退出顺序 / 22 状态文件与 Chrome 配置目录防护 / 23 套件判定与退出码同源 /
     24 离线套件必须被 git 跟踪 / 25 交付链不许写死开发机路径 / 26 TEMP 只许一处口径 /
     27 模块级不许取用未绑定的名字 / 28 shell 必须自己表态 errexit / 29 交付链必须有写在
     仓库里的行尾立场 / 30 失败原因只准一个把手收口 / 31 标签交出的正文必须过页面身份门、
     且「这一跳根本没加载出来」必须按 Chrome 原话归因（31g 还要证明那枚启动参数真的带上了，
     而且三条路径上的浏览器带的是同一份）：这十一条各自的由来与口径写在**它们函数上方**的注释块里——那十一条都是
     一次真实故障之后补的，故障经过比摘要长，写在这里会让这份文件一半是故事。此处只留一句
     提醒：**这张编号表不是全集**，只看到 20 的人会以为后面没有了。

用法：
  python .github/scripts/check_packaging_manifest.py        # 校验，失败退出码 1
  python .github/scripts/check_packaging_manifest.py --list  # 只打印当前清单
"""
from __future__ import annotations

import argparse
import ast
import builtins
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unicodedata
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

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
    "debug/fetch_run_log.sh",
    "debug/poll_b2bci.sh",
    "debug/run_offline_regression.sh",
)


# 这一条门一次判三件事，三件事的**处置完全不同**，所以每条问题都带类目标签：
#   【文案】脚本里 `$VAR` 紧跟中文 → 改那一行的写法；
#   【在位】登记表里的脚本读不到 → 要么把文件放回去，要么把登记删掉（不许留静默空转）；
#   【未提交】工作区那份和提交里那份不是同一份 → 先 git commit，本机跑绿才算给交付担保。
# 由来（2026-11-15 本机自审抓到）：今天满屏都是【未提交】那一类，而标题写的是
# 「`$VAR` 紧跟中文会把报错那行本身弄坏」——照着标题去找文案 bug 的人会白找一轮，
# 而这一条门报的红越「说不清自己在报什么」，下一次就越容易被整条跳过。
HYGIENE_TAGS = ("文案", "在位", "未提交")


def check_script_hygiene() -> list:
    """第 9 条在「非打包入口」那几份脚本上的同样一份判据（三类问题各自带标签）。"""
    problems = []
    for rel in HYGIENE_SCRIPTS:
        path = ROOT / rel
        if not path.is_file():
            problems.append(f"【在位】{rel} 不在位（登记了却读不到，这条判据正在空转）")
            continue
        state = tracked_state(path) if in_work_tree() else "ok"
        if state != "ok":
            # 与第 7 条同一个坑，而且更阴：这里读的是**工作区**那份文本，CI 上跑的却是
            # 提交里那份。改过没提交时，「文案卫生」验的是不会被交付的内容，判据看着绿，
            # 真正上云的那份脚本可能正带着同一个 bug。所以脏也要红，措辞按状态分开。
            problems.append(f"【未提交】{_state_hint(rel, state)}")
        for msg in check_shell_hygiene(path.read_text(encoding="utf-8", errors="replace")):
            problems.append(f"【文案】{rel} {msg}")
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
SHELL_SOURCES = tuple(list(HYGIENE_SCRIPTS) + [
    "debug/build_b2bcollect_mac_local.sh",
    # 性能探针的统一入口（优化前后同一把尺子）：它不产交付物，但它产「快了 8 倍」
    # 这句话的证据，判据链上就得有它的位置。
    "debug/run_perf_probe.sh",
])


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
    """第 15 条的第三半：`set +e` 把**一整段**的保护关掉（开头的表态除外）。

    上面两半管的是「一句非零把脚本带走」与「陷阱响了却没开 -E」，这一半管的是反向的那
    种侥幸：`set +e … set -e` 之间每一条命令都失去了拦路资格，后来人在中间插一句（换
    个落点、加一次查找）它失败了也不会有人知道，而这一段自己看着是「已经开回来」的。
    接管某一条命令的退出码从来不需要关整段：`cmd || rc=$?`（管道要首段就
    `cmd | tee log || rc=${PIPESTATUS[0]}`，实测 bash 3.2 与 5.x 都成立）。
    """
    out = []
    # 例外只开给**第一条 errexit 表态**、且它把 -e 关掉的情形：那是第 28 条要求的「自己声明
    # 立场」，不是从别人手里抢走一段保护。2026-11-14 Windows 云端实发之后才有这一格——调用方
    # 的 `-e -o pipefail` 会被 bash 交给子脚本，一份从没用过 errexit 的脚本唯一的自保方式就是
    # 在第一句把它关掉；把这种写法也判红，等于逼下一个人在批跑入口里补一句 `set -e`（那才是
    # 真把一百多条裸赋值交到 errexit 手上）。关掉**已经打开的**保护，仍旧逐条报。
    declared = None
    for lineno, line, _active in segments:
        if line.strip().startswith("#"):
            continue
        st = _set_state(line)[0]
        if st is None:
            continue
        declared = (lineno, st)
        break
    for lineno, line, _active in segments:
        if line.strip().startswith("#"):
            continue
        if _set_state(line)[0] is not False:
            continue
        if declared == (lineno, False):
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
        # 第一段（到脚本自己第一条 errexit 表态为止）一律按 **-e 生效** 判，不再按「脚本里没写
        # 就是没开」：调用方的 `-e` 会被 bash 交给子脚本（第 28 条整条就是这一件事，2026-11-14
        # Windows 云端实发），所以「脚本自己没提」恰恰不能推出「这一段没有 errexit」。
        # 一份后面写了 `set +e` 的脚本，它**表态之前**那几行同样交在别人手里——把默认值跟着
        # 表态走，等于在最需要保守的那一侧开了一个假绿的口子。表态之后的段落仍由
        # _errexit_segments 逐行跟着脚本自己的 `set` 切换，不会把作者明确关掉的段判红。
        corpus.append((rel, 0, _errexit_segments(text, default=True)))
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
# 第 28 条：每一份手写 shell 都要在**第一条真命令之前**对 errexit 表态（2026-11-14 云端实发）。
#
# 现场：Windows 云端「Packaging manifest gate」这一步红，红话说的是
#   批跑清单取不到（`bash debug/run_offline_regression.sh --list` 退出码 1）
# 而那条命令在本机一模一样是绿的。把云端那次 job 的日志逐行读完才看清签名：**退出码 1、
# stdout 0 字节、stderr 0 字节**——批跑入口连它的第一句诊断（`清单完整性：…`，走 stderr）
# 都没来得及打，等于死在第 78 行之前。本机拿同一条命令喂上 Actions 给 bash 步的那对选项
# （`bash -e -o pipefail debug/run_offline_regression.sh --list`）当场复现出逐字节一致的签名，
# `bash -x` 停在 ``hits=$(ls "$d"/test_*_offline.py 2>/dev/null)``：ali1688_build 按登记就是
# 没有 *_offline.py，ls 找不到匹配返回非零，报错又被自己的 2>/dev/null 吃掉，裸赋值把这个
# 非零交给 errexit，脚本一声不响地结束。
#
# 为什么只有 Windows 红：Actions 两步都是 `shell: bash`，但 macOS 上那一位是 /bin/bash 3.2，
# 实测不把 -e 交给子 bash；Windows 上的 Git Bash 会交。**同一份脚本、一个平台绿一个平台红，
# 差的不是代码，是谁带着什么选项把它拉起来的。**
#
# 这一族今天一共 14 处（第 15 条按「-e 生效」重判全仓的结果）：批跑入口 8 处、取制品 6 处，
# 全部已改。而真正被本轮证伪的是第 15 条原来那句口径——「脚本里没写过 set -e 就一路不设」：
# 选项可以从调用方来，脚本自己没写不等于没生效。所以这里补一条**立场**判据，并把第 15 条的
# 默认值改成跟着脚本自己的表态走（没表态就按最坏的 -e 生效判，同时第 28 条先为「没表态」响）。
# ---------------------------------------------------------------------------
SET_POSTURE = re.compile(r"^\s*set\b")


def _errexit_posture(text: str) -> tuple:
    """脚本开头有没有对 errexit 表态：(表没表态, 表成什么, 表态行号, 第一条真命令行号)。

    只认「第一条真命令之前那一串 `set`」：`set -u` / `set -x` 这类没提到 errexit 的开关算
    路过（继续往后找），赋值与其它命令算真命令（当场停）。为什么卡这么紧：表态发生在第一句
    可能失败的命令**之后**，那句就已经有机会静默把脚本带走了，这一格要的正是「先定规矩再动手」。
    """
    first_real = 0
    for lineno, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        er, _et = _set_state(line)
        if er is not None:
            return True, er, lineno, 0
        if SET_POSTURE.match(line):
            continue
        first_real = lineno
        break
    return False, None, 0, first_real


def check_shell_errexit_posture(sources: tuple = None, texts: tuple = ()) -> list:
    """第 28 条：手写 shell 必须自己声明 errexit 立场，不许由调用方决定。

    sources/texts 与第 15 条同口径：登记表可整体替换，texts 是内存里的 [(标签, 脚本)]——
    离线对拍要能拿「同一句裸赋值，开头表态 / 不表态」这一对喂进来，要求不表态的那份必须
    响、表态的那份干净。判据验不出「坏了会响」就等于没有判据。
    """
    sources = SHELL_SOURCES if sources is None else sources
    corpus = []
    for rel in sources:
        path = ROOT / rel
        if not path.is_file():
            continue                    # 不在位由第 11 条点名，这里不重复报
        corpus.append((rel, path.read_text(encoding="utf-8", errors="replace")))
    for label, script in texts:
        if label in set(sources):
            continue                    # 登记表里那份已经按磁盘读过了，不重复判
        corpus.append((label, script))
    if not corpus:
        return ["第 28 条一份手写 shell 都没读到（登记表里那些路径在磁盘上都不在位，喂进来的"
                "夹具也是空的）：这时「都表态了」这句话没有任何含义，不许报绿"]
    problems = []
    for label, text in corpus:
        ok, _on, _at, first_real = _errexit_posture(text)
        if ok:
            continue
        if first_real:
            problems.append(
                f"{label}：第 {first_real} 行已经是第一条真命令，可它之前没有任何一句 `set` 提到"
                " errexit——**这份脚本有两种生死**：调用方带 `-e` 起来（Actions 的 bash 步默认就是 "
                "`-e -o pipefail`，新版 bash 会把它交给子脚本）它就在那一句失败处静默结束，调用方"
                "没带就是另一副样子。本轮云端就是这么把 `run_offline_regression.sh --list` 读成"
                "「退出码 1、stdout 0 字节、stderr 0 字节」的。要么在开头写 `set -Eeuo pipefail`"
                "（并把每一条正常也会非零的取值都 `|| rc=$?` 接管），要么写 `set +e` 明说这份脚本"
                "的错误处理走显式分支，不靠 errexit")
        else:
            problems.append(f"{label}：整份脚本没有任何一句 `set` 提到 errexit，也没有一条真命令"
                            "——一份空脚本不该出现在交付链登记表里，请核对它是不是被清空了")
    return problems


# ── 第 29 条：交付链必须对「检出出来是什么行尾」有一份**写在仓库里**的立场 ──────────
# 由来（2026-11-15 Windows 云端实发）：Windows 的 GitHub runner 镜像把 git 全局
# `core.autocrlf` 设成 true，`actions/checkout@v4` 不覆盖它；本仓库此前没有 .gitattributes，
# 也就是**对行尾一个字都没说**。同一份提交在两台机器上检出成两种字节，而 shell 只在 LF 上
# 能跑——本机把 debug/run_offline_regression.sh 整份转成 CRLF 实测过后果（macOS bash 3.2）：
#   退出码 2｜stdout 0 字节｜stderr 360 字节
#   `line 31: set: +: invalid option`、`line 85: syntax error near unexpected token 'do'`
# 最要紧的一格是：**`bash -n` 对同一份 CRLF 文件是过的**（第 12 条那道语法门只解析不执行），
# 所以行尾差异骗得过语法检查，只在真跑那一刻炸。这条门补的就是「解析得过、跑不起来」这一段
# 空白，与第 15/28 条（errexit 由谁决定）同族：判的都是**换一台机器之后这行命令还成不成立**。
LINE_ENDING_STANCE_FILE = ".gitattributes"
# 问哪两格：text 决定「这份文件要不要做行尾转换」，eol 决定「检出成哪一种」。
# 值的形状来自 `git check-attr`：属性被定义时是 set/unset/auto/lf/crlf，**没定义时是空串**。
# 用 `--stdin -z` 而不是解析普通输出：普通输出会把非 ASCII 路径八进制转义再套引号
# （``"riskbird_build/\345\220\257\345\212\250….bat"``），那一格就不是路径了——同 _path_set。
_STANCE_ATTRS = ("text", "eol")
# 一个字节的名字叫 CR：写成 `b"\r"` 的字面量放进 f-string 表达式里，在 3.11（Windows 构建机
# 用的就是它）是 SyntaxError，而在 3.13 上能过——这种「本机过得去、构建机过不去」的差别本身
# 就是这条门要拦的形状，所以这里给它一个名字，谁都用变量。
CR = b"\r"


def _line_ending_scope(sources: tuple = None) -> list:
    """第 29 条的视野：交付 shell（登记表）+ 门禁脚本 + workflow + 那几份 shell 的语法夹具。

    范围以登记表为准，不写死数字：新增一份 shell 会先进第 11 条的登记表，登记了却不进这一条
    就等于「它在别的机器上行尾没人管」，而那正是这条门要拦的事。
    """
    sources = SHELL_SOURCES if sources is None else tuple(sources)
    out = list(sources) + list(WORKFLOWS)
    for path in sorted((ROOT / ".github" / "scripts").glob("*.py")):
        rel = _rel_posix(path)
        if rel not in out:
            out.append(rel)
    return out


def _line_ending_stances(paths: list) -> dict:
    """问 git：这些路径按本仓库的立场检出时 text/eol 各是什么（{路径: {属性: 值}}）。

    取不到就返回空表，由调用方判红——**不许按「没定义也没事」返回**：这一条问的是立场在不在
    仓库里，问不到问题本身就是结论。
    """
    if not paths:
        return {}
    payload = "\0".join(paths) + "\0"
    try:
        proc = subprocess.run(["git", "check-attr", "--stdin", "-z", *_STANCE_ATTRS],
                              cwd=str(ROOT), input=payload.encode("utf-8", "surrogateescape"),
                              capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if proc.returncode != 0:
        return {}
    rows = proc.stdout.decode("utf-8", "surrogateescape").split("\0")
    out = {}
    # 三元组逐格读：属性未定义时**值那一格是空串**，所以不能靠「跳过空串」对齐，只能按 3 步长
    # 走（末尾多出的那一个空串是最后一个 NUL 留下的，用 len-2 收边）。
    for i in range(0, len(rows) - 2, 3):
        rel, attr, value = rows[i], rows[i + 1], rows[i + 2]
        if rel and attr:
            out.setdefault(rel, {})[attr] = value
    return out


def check_line_ending_stance(sources: tuple = None, texts: tuple = ()) -> list:
    """第 29 条：交付文本不许带着 CR，且 eol 必须由仓库里的 .gitattributes 定成 lf。

    sources/texts 与第 28 条同口径（登记表可整体替换，texts 是内存里的 [(标签, 内容)]）：
    离线对拍要能拿「同一份 shell，LF / CRLF」这一对喂进来，要求 CRLF 那份必须响。判据验不出
    「坏了会响」就等于没有判据。
    """
    scope = _line_ending_scope(sources)
    problems = []
    # 判一：字节层面不许有 CR。磁盘上那一份与喂进来的夹具走同一条判据，不分轻重。
    checked = 0
    for rel in scope:
        path = ROOT / rel
        if not path.is_file():
            continue                        # 不在位由第 11 条点名，这里不重复报
        checked += 1
        raw = path.read_bytes()
        crs = raw.count(CR)
        if crs:
            problems.append(
                f"{rel}：工作区这一份里有 {crs} 处 CR。shell 带着 CRLF 会被 bash 死在"
                "第一句真命令上（本机实测签名：退出码 2、`syntax error near unexpected token 'do'`），"
                "而 `bash -n` 查不出来——构建语法门全绿、真跑才崩，正是最难查的那一类")
    for label, script in texts:
        checked += 1
        blob = script.encode("utf-8", "surrogateescape") if isinstance(script, str) else bytes(script)
        crs = blob.count(CR)
        if crs:
            problems.append(f"{label}：喂进来的这一份里有 {crs} 处 CR（同上：bash 死在"
                            "第一句，而 bash -n 查不出来）")
    if not checked:
        return ["第 29 条一份交付文本都没读到（登记表里的路径在磁盘上都不在位，夹具也是空的）："
                "这时「行尾合格」这句话没有任何含义，不许报绿"]
    # 判二：立场必须写在仓库里。只在这一条真跑过 git 时才判，且判不到问题一律算红。
    real = [rel for rel in scope if (ROOT / rel).is_file()]
    stances = _line_ending_stances(real)
    if not stances:
        problems.append("第 29 条问不到 git 的行尾立场（git 不可用，或 `check-attr` 一句没回）："
                        "这一条判的就是「检出出来长什么样」，问不到就不许按合格返回")
    else:
        bad = sorted(rel for rel in real if (stances.get(rel) or {}).get("eol") != "lf")
        if bad:
            got = "、".join(f"{rel}→{(stances.get(rel) or {}).get('eol') or '未定义'}"
                            for rel in bad[:4])
            problems.append(
                f"{len(bad)} 份交付文本的 eol 不是 lf（{got}"
                f"{' 等' if len(bad) > 4 else ''}）：Windows 构建机与 Windows 用户克隆出来就是 "
                "CRLF，shell 当场死。修法是在 "
                f"{LINE_ENDING_STANCE_FILE} 里对这一类显式写 `text eol=lf`，而不是去改某台机器的"
                "全局 git 配置——写在机器上的立场换一台机器就没了，那正是本轮踩到的形状")
    stance_path = ROOT / LINE_ENDING_STANCE_FILE
    state = tracked_state(stance_path)
    if state != "ok":
        problems.append(f"{LINE_ENDING_STANCE_FILE}：{_state_hint(LINE_ENDING_STANCE_FILE, state)}"
                        "——行尾立场必须跟着提交走，否则 CI 检出的仓库里没有任何东西管着 eol，"
                        "本机绿与云端红可以同时成立很久")
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


# ---------------------------------------------------------------------------
# 第 20 条：交付链上每一次 curl 都必须**自己会停**，写进文件的字节必须**跨次能用**，
#          调出去的外部命令必须**不许问人**，而这几道门必须**看得见每一份脚本**。
#
# 由来（2026-11-13 本机实发，连着两趟一样的红）：取制品跑到外层 timeout 被杀（rc=124），
# 日志停在「下载中」之后一句解释都没有。字节级看是三件事叠在一起，缺任何一件都不会让
# 这件事「永远完不成」：
#   ① curl 默认只在**连不上**的时候自己退；连上之后 11 KB/s 它认为完全正常。本机实测：
#      同一个 artifact（14034890976）单连接 Range 取 2 MiB 用了 182.98 秒。
#   ② 那个慢挂在**单条连接**上而不是链路上——实测四并发各取 2 MiB、总 166 秒拿到 8 MiB
#      （≈50 KB/s）。所以给人一个大 max-time 既救不了它，又会同时弄死正常的慢链路：
#      时间上限这条路对体下载是错的，正确的形状是「停顿判定 + 按块续传」。
#   ③ zip 落在 mktemp 出来的工作目录里，trap EXIT 顺手把它删了——于是每一次重跑都从第
#      0 字节开始，跑一百次也不会有进展。慢不是缺陷，「慢到永远完不成」才是。
# 同一趟还在另一份脚本上顺手抓到一个同族的（dispatch 那两条 curl 原本都是裸的；本机这一刻
# api.github.com 的连接时间实测会 15 秒过不去）：派发失败长得和派发成功一模一样，人就开始
# 等一个从没起来过的 run。所以这一条判的是**结构**，不是这一分钟的网络：
#   20a 每一次 curl 都要自己会停：有总时限（--max-time/-m）**或**停顿时限
#       （--speed-limit 与 --speed-time 两个都要，只有一个就是没判）。
#   20b 把响应体写进文件的那一次，落点不许是本次即弃的临时目录（同一份脚本里由 mktemp
#       赋出来的变量开头就算）。`-o -`（交给调用方）与 /dev/null 不算：那里没有要续传的字节。
#   20c 写进文件的下载必须跨次可用：要么自己会接着要（--continue-at/-C 或 Range/-r），
#       要么就近有完整性收口（sha256/md5/certutil/check_release_zip/真解一次）。NSIS 那一步
#       走的是后者（每轮换镜像重下并核哈希，续传在那儿反而是错的），两条合法路都在案上，
#       免得逼人为了消红去写一个用不上的 -C。
#   20d 会「开口问人」的外部命令必须显式关掉交互：`git credential fill` 在 helper 里没凭证时
#       不是报错，而是去开 /dev/tty 问密码——它读的不是脚本喂给 stdin 的那条 printf，所以
#       「管道里已经喂了东西」挡不住它。四处调用（派发/取制品/轮询/取证）原本全都只喂 stdin。
#       文件级 `export GIT_TERMINAL_PROMPT=0` 也算关好；整行注释里的提及不算调用（不滤掉就是
#       一条与真缺陷一模一样的假红，而假红的代价从来是人开始跳过这一步）。
#   20e 交付链目录里每一份 .sh 都必须在 SHELL_SOURCES 或 SHELL_EXEMPT 里在案。由来：取证脚本
#       debug/fetch_run_log.sh 一直不在表里，文案卫生、bash -n、curl 有界、外部输出出处四道门
#       在它身上全是空的——它是本轮唯一一份「天天有人跑、却从没被任何一道门看过」的脚本；20c 一
#       接上就在它身上红了一条真缺陷。登记缺口不需要出事才修，需要的是「新写一份没登记」当场红。
#   20f 手写脚本里每一次**独立起来**的 python 都必须自带 fd 0（`< /dev/null`），除非它的 stdin
#       来自单管道右侧或显式 heredoc。由来是本机实发的一起：脚本后台起跑、启动它的终端随后被回收，
#       fd 0 成了已关闭的描述符，python 在解释器初始化阶段就崩（init_sys_streams / Bad file
#       descriptor），门自己的 print 一句没留——「交付 zip 的门不合格」被当成结论，而真相是门根本
#       没跑起来。workflow 的 bash 步刻意不判（Actions 自己就把 stdin 关好了），这条边界写在案上。
# 判不到的形状也写明白，免得这条门给人「所有下载都被管住了」的错觉：wget、curl 通过别的
# 变量名间接拼出来的参数、以及 .py 里 subprocess 调 curl（本仓没有，pip 那一路由第 18 条管）
# 都不在这一条的视野里。
# ---------------------------------------------------------------------------
CURL_HEAD_RE = re.compile(r"^(?:[A-Za-z_]\w*=\S*\s+)*(?:command\s+)?curl(?:\s|$)")
CURL_TOTAL_BOUND = ("--max-time", "-m")
CURL_STALL_BOUND = ("--speed-limit", "--speed-time")
CURL_OUT_FLAGS = ("-o", "--output", "-O", "--remote-name", "--remote-output")
# 落到「当前目录 + URL 同名」的那几个开关：确实写了文件，但路径不是脚本写出来的，
# 20b 归因不了（猜一个错路径比说「判不了」更糟），20c 照判。
CURL_OUT_CWD_FLAGS = ("-O", "--remote-name", "--remote-name-all", "--remote-output")
# 组合短开关里允许出现在 o 之前的字母：取自 curl -h 里**不带参数**的那些短开关
# （-a -f -g -G -I -j -J -k -L -M -n -N -q -s -S -v，以及 -0..-9 与 -#）。带参数的那些
# （-b -c -d -e -m -r -t -T -u -U -w -X -x -y -Y -z -H -K -F -D -C -o…）一律留在表外，
# 表外的字母出现在 o 之前就不再把这一串当组合开关——`-XPOST` 里的 O、`-ccookiejar` 里
# 的 o 都属于「别人的值」，把它们当成落点会得到一个假归因，而假归因正好绕开 mktemp
# 那一判（漏报的方向）。
CURL_NOARG_SHORTS = set("aGILMNafgjknqSsv0123456789#")
CURL_NO_LANDING = ("/dev/null", "-")           # 体没落地：这一路没有要续传的东西
CURL_RESUME_FLAGS = ("--continue-at", "-C", "--range", "-r")
CURL_INTEGRITY_WORDS = ("sha256", "md5sum", "certutil", "check_release_zip",
                        "--os-extract", "unzip -t", "gzip -t")
# 20c 的就近窗口：完整性收口必须写在同一件事旁边。为什么不是全文找：窗口一大，文件顶上
# 一句「我们核过哈希」就能给全文件的下载当免检牌——那就不再是收口，是护身符（第 14 条
# 同一道理）。为什么不是紧邻下一行：NSIS 那一步里 `curl` 与 `got=$(sha256 …)` 之间还夹着
# `|| { echo 换下一条; continue; }` 一行，40 行的代价是「隔几行的收口仍然算」。
CURL_INTEGRITY_LOOKON = 40


def _shell_tokens(text: str) -> list:
    """按 shell 的空格分词，引号内整体保留（找 `-o` 的值要用，不能按空格乱切）。"""
    out, buf, quote = [], [], ""
    for ch in text:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch.isspace():
            if buf:
                out.append("".join(buf))
                buf = []
        else:
            buf.append(ch)
    if buf:
        out.append("".join(buf))
    return out


def _logical_lines(text: str) -> list:
    """[(这一句命令, 起始行号 1 基)]，把 `\\` 折行拼回一行。

    折行不拼回来，判据就会把 `curl -sSL` 与下一行的 `--connect-timeout 20 …` 当成两条，
    于是每一条带续行的 curl 都被判成「没界」——假红逼人在中间插一行注释，而那条注释
    什么也不担保。行号仍报起始行，让人一眼走到。
    """
    lines = text.splitlines()
    out, buf, base = [], [], 1
    for i, raw in enumerate(lines, 1):
        if not buf:
            base = i
        if raw.endswith("\\"):
            buf.append(raw[:-1])
        else:
            buf.append(raw)
            out.append(("".join(buf), base))
            buf = []
    if buf:
        out.append(("".join(buf), base))
    return out


def _mktemp_vars(text: str) -> set:
    """这份脚本里由 mktemp 赋出来的变量名（20b 认的就是它们：那是本次即弃的落点）。"""
    return {m.group(1) for m in re.finditer(r"^\s*([A-Za-z_]\w*)=[^\n]*\bmktemp\b", text, re.M)}


def _curl_out_value(toks: list):
    """返回 (落点种类, 值)：
      ("", "")        这一句没往文件里写体（20b/20c 都不判）
      ("path", "x")   写向一个明确路径；`-o -` 与 `/dev/null` 也走这里，由调用方豁免
      ("cwd", "")     `-O`/`--remote-name`：确实落地了，但文件名由 URL 决定，路径前缀
                      判不出来——20b 只能不判（说「判不了」比猜一个错归因诚实），20c 照判

    三种写法都要认，认漏一种就是在那种写法上安静放行：
      `-o 值` / `--output 值`（值在下一个 token）
      `-o值` / `--output=值` / 组合短开关里带 o（`-so值`，值粘在同一 token 上）
      `-O` / `--remote-name` / 组合开关里带大写 O（落到当前目录）
    组合开关的规矩按 curl 自己的口径来：小写字母后面如果还有别的字符，那剩下的就是值
    （`-so"$X"` 的值是 `"$X"`）；`o` 是最后一个字符才去读下一个 token。上一版只看
    「前四个字符里有没有 o」，于是 `-o"$WORK/a.zip"` 把**下一个 token（URL）**当成了落点
    ——那是个假归因，而且正好绕开 mktemp 那一判（漏报的方向）。
    """
    for i, t in enumerate(toks):
        if t in CURL_OUT_FLAGS:                       # -o / --output / -O / --remote-name
            if t in CURL_OUT_CWD_FLAGS:
                return "cwd", ""
            return ("path", toks[i + 1].strip("\"'")) if i + 1 < len(toks) else ("path", "")
        if t.startswith("--") and "=" in t:           # --output=…
            key, val = t.split("=", 1)
            if key in CURL_OUT_FLAGS:
                return "path", val.strip("\"'")
            continue
        if t.startswith("-") and not t.startswith("--") and len(t) > 1:
            body = t[1:]
            for j, ch in enumerate(body):
                if ch in ("o", "O"):
                    if ch == "O":
                        return "cwd", ""
                    rest = body[j + 1:]
                    if rest:
                        return "path", rest.strip("\"'")
                    return ("path", toks[i + 1].strip("\"'")) if i + 1 < len(toks) else ("path", "")
                if ch not in CURL_NOARG_SHORTS:
                    break                  # 带参数的短开关出现在 o 之前：后面那串是它的值
            continue
    return "", ""


def curl_sites(text: str) -> list:
    """[(行号, 那一句命令, [token])]：这份 shell 里的每一条 curl 调用（折行已拼回一行）。

    单独做成一个函数，是为了让离线夹具能直接数「今天到底看到了几条 curl」——判据若只在
    内部循环里数，测试就只能复述那段循环（复述出来的口径会和真口径分叉，第 10 条同族）。
    """
    out = []
    for logical, lineno in _logical_lines(text):
        for seg in _shell_segments(logical):
            if CURL_HEAD_RE.match(seg):
                out.append((lineno, seg, _shell_tokens(seg)))
    return out


def shell_gate_inputs(needles: tuple = ("curl",)):
    """第 20 条各小判据真正要扫的那些 shell 文本：返回 ({标签: 文本}, 读不到时的清单)。

    needles 决定「一段文本值不值得收进来」：curl 那一族只判含 curl 的，交互挂起那一族
    只判含 `credential fill` 的。留成参数而不是各写一份读取循环，是为了让两族用的是
    **同一张登记表**——一张表各抄一遍的话，加一份脚本只会补到其中一处（第 10 条同族坑）。
    """
    texts, problems = {}, []
    for rel in SHELL_SOURCES:
        path = ROOT / rel
        if not path.is_file():
            problems.append(f"{rel} 不在位（登记了却读不到，这条判据正在空转）")
            continue
        body = path.read_text(encoding="utf-8", errors="replace")
        if not any(n in body for n in needles):
            continue                     # 与 workflow 那一侧同一个口径：不含这个关键词就不必收
        texts[rel] = body
    for rel in WORKFLOWS:
        path = ROOT / rel
        if not path.is_file():
            problems.append(f"{rel} 不在位（登记了却读不到，这条判据正在空转）")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for name, lineno, shell, script in run_blocks_in_workflow(text):
            if shell != "bash":
                continue                 # pwsh 步由离线测试 M 节按 PowerShell 口径判
            if not any(n in script for n in needles):
                continue
            texts[f"{rel} 第 {lineno} 行「{name}」"] = script
    return texts, problems


def check_curl_bounds(texts=None) -> list:
    """texts: {标签: shell 文本}。留注入面是给离线夹具的（第 16/17/19 条同一条纪律）：
    判据若只能读本机仓库，「坏了会不会响」这一半就永远验不了。
    """
    problems = []
    if texts is None:
        texts, problems = shell_gate_inputs(("curl",))
    # 这一句必须留在 `if texts is None` 之外。放里面的时候我踩过一次：注入 `{}` 的调用方
    # 得到的是「一条问题都没有」，而它和「一份文件都没读到」是同一件事——留到默认口径外
    # 就等于这条 fail-closed 只保护我自己写的那条路，夹具和将来任何别的调用方都在保护之外。
    if not texts:
        problems.append("第 20 条一份 shell 文本都没读到：这时报「所有 curl 都有界」"
                        "没有任何担保（扫 0 个文件的全覆盖是自欺，AB 节量过同一格）")
    total = 0
    for label, text in sorted(texts.items()):
        lines = text.splitlines()
        mkvars = _mktemp_vars(text)
        # 被注释掉的那句 curl 不会被执行：判它得到的是假红，而假红的代价是人开始跳过这一步。
        # 20c 那个「就近收口」的窗口反过来要看原文（它自己在下面滤注释），所以这里只换调用点
        # 这一侧；行号由 _drop_comment_lines 保位，报出来的行仍然走得通。
        for lineno, seg, toks in curl_sites(_drop_comment_lines(text)):
            total += 1
            flags = {t for t in toks if t.startswith("-")}
            shown = seg.strip()[:120]
            # 20a 自己会停
            has_total = any(f in flags for f in CURL_TOTAL_BOUND) or any(
                t.startswith("-m") and len(t) > 2 and t[2:].isdigit() for t in toks)
            has_stall = all(f in flags for f in CURL_STALL_BOUND)
            if not (has_total or has_stall):
                problems.append(f"{label} 第 {lineno} 行的 curl 谁也不会让它停（既没有 "
                                f"--max-time/-m，也没有 --speed-limit + --speed-time）："
                                f"「{shown}」——挂住的连接只会让外层超时来杀，而它杀完一句"
                                f"原因都留不下（本机取制品那两趟 rc=124 就是这个形状）")
            # 20b/20c 只有真的把体写进文件才判
            kind, out = _curl_out_value(toks)
            if kind == "":
                continue                         # 这一句根本没往文件写体（响应交给调用方/丢弃）
            if kind == "path" and (not out or out in CURL_NO_LANDING):
                continue                     # -o - / -o /dev/null：没有要续传的字节，也没有落点
            shown_out = out if kind == "path" else "当前目录（-O/--remote-name）"
            if kind == "path":               # cwd 那一格路径不由脚本决定，20b 归因不了
                for var in mkvars:
                    if out.startswith(f"${{{var}") or out.startswith(f"${var}"):
                        problems.append(f"{label} 第 {lineno} 行把响应体写进 ${var} 指的地方，而 "
                                        f"{var} 是 mktemp 出来的（本次即弃）：进度随它一起被删，"
                                        f"下一次重跑从第 0 字节开始——慢可以接受，「永远完不成」不行")
            resume = any(f in flags for f in CURL_RESUME_FLAGS) or any(
                t.startswith("-C") or (t.startswith("-r") and len(t) > 2) for t in toks)
            if not resume:
                # 注释行不算收口。这一格是本轮自审抓到的：旧版取制品脚本第 244 行那条无续传
                # 下载，被第 279 行一句「这里跑的也是 check_release_zip.py」免掉了，而真正的
                # 那次调用在第 288 行——早就出了 40 行窗口。收口必须是被**执行**的字节；一句
                # 描述收口的话不是收口（和第 19 条「注释里的指路自己不构成判据」同一个道理）。
                window = "\n".join(ln for ln in lines[lineno - 1: lineno - 1 + CURL_INTEGRITY_LOOKON]
                                   if not ln.lstrip().startswith("#"))
                if not any(w in window for w in CURL_INTEGRITY_WORDS):
                    problems.append(f"{label} 第 {lineno} 行往「{shown_out}」写体，既不续传"
                                    f"（--continue-at/-C 或 Range/-r 一个都没有），近 {CURL_INTEGRITY_LOOKON} "
                                    f"行内也没有完整性收口（{'/'.join(CURL_INTEGRITY_WORDS)} 一个都没出现）："
                                    f"断了就只能整段重下，或更坏——下了一半却没人能看出来")
    if texts and total == 0:
        problems.append(f"第 20 条在 {len(texts)} 份 shell 文本里一条 curl 都没找到：口径已经和"
                        "交付链脱节（这一条判的是「每一次 curl」，一次都没有等于没判）")
    return problems


# ── 20d：会「开口问人」的外部命令必须显式关掉交互 ────────────────────────────
# 由来（2026-11-13 本机自审，就在把第 20 条接上之后）：`git credential fill` 在 helper
# 里没有 github.com 凭证时不是报错，而是去开 /dev/tty 问用户名密码。它读的不是脚本喂给
# stdin 的那条 printf，所以「管道里已经喂了东西」一点也挡不住它。这一刻和 20a 管的是同一
# 件事：进程不会自己停，日志一个字都不留，人只能对着「取制品」那行标题等。清点下来仓库里
# 四处这样的调用（派发 / 取制品 / 轮询 / 取证）全都只喂了 stdin、没关交互。
PROMPT_CAPABLE = (
    ("git credential fill", "credential fill"),   # 没凭证时它开 /dev/tty，喂 stdin 没用
)
NO_PROMPT_ENV = "GIT_TERMINAL_PROMPT=0"
FILE_WIDE_NO_PROMPT = "export GIT_TERMINAL_PROMPT=0"


def _drop_comment_lines(text: str) -> str:
    """把整行注释换成空行（行号不变）。注释里那句「走 git credential fill」不是调用，
    不滤掉会得到一条与真缺陷一模一样的假红——而假红的代价从来都是「人开始跳过这一步」。
    """
    return "\n".join("" if ln.lstrip().startswith("#") else ln
                     for ln in text.splitlines())


def check_no_prompt_hang(texts=None) -> list:
    """交付链 shell 里每一处会开口的命令都得带上「不许问人」的开关（第 20 条 20d）。"""
    problems = []
    if texts is None:
        texts, problems = shell_gate_inputs(("credential fill",))
    if not texts:
        # 与 20a~20c 同一个 fail-closed：登记表里一份含凭证调用的 shell 都没读到，可能是
        # 真的不再取凭证，也可能是扫描口径和交付链脱节——两种都得由人写一句话确认，不能让
        # 这一条安静地报绿。
        problems.append("第 20 条 20d 一份含 credential fill 的 shell 都没读到：报「交互都关掉了」"
                        "没有担保（要么改这张表里的关键词，要么在判据里说明交付链已不取凭证）")
        return problems
    for label, text in sorted(texts.items()):
        body = _drop_comment_lines(text)
        if FILE_WIDE_NO_PROMPT in body:
            continue                     # 文件级开关：一次写清，逐行就不必再带
        # 那句 export 必须是被执行的字节：留在原文里，一句「# 记得 export GIT_TERMINAL_PROMPT=0」
        # 就能给整份文件免检——和 20c 那个「注释里也核哈希」是同一个方向的洞（假绿比假红更贵）。
        for logical, lineno in _logical_lines(body):
            for human, needle in PROMPT_CAPABLE:
                if needle in logical and NO_PROMPT_ENV not in logical:
                    problems.append(f"{label} 第 {lineno} 行的 `{human}` 没带 {NO_PROMPT_ENV}："
                                    "helper 里没凭证时它会去开 /dev/tty 问密码（stdin 里喂的"
                                    "printf 挡不住它），整趟就此挂住，日志一句原因都不会留")
    return problems


# ── 20e：交付链目录里的每一份 shell 都必须在登记表里 ──────────────────────────
# 由来（同一天，紧接着 20d）：取证脚本 debug/fetch_run_log.sh 一直不在 SHELL_SOURCES 里，
# 于是文案卫生、bash -n、curl 有界、外部工具输出出处这四道门在它身上**全是空的**——它是
# 本轮唯一一份「会被人天天跑、却从没被任何一道门看过」的交付链脚本。登记缺口不需要出事才
# 修，需要的是让「新写一份没登记」当场红。
SHELL_SCAN_DIRS = ("debug", ".github/scripts")
SHELL_EXEMPT: tuple = ()          # 不判的 shell：写清理由才有资格进这张表（今天是空的）


def check_shell_registration() -> list:
    """扫交付链目录，凡是不在 SHELL_SOURCES / SHELL_EXEMPT 里的 .sh 都点名（第 20 条 20e）。"""
    problems = []
    registered = set(SHELL_SOURCES) | set(SHELL_EXEMPT)
    seen = 0
    for rel_dir in SHELL_SCAN_DIRS:
        root = ROOT / rel_dir
        if not root.is_dir():
            problems.append(f"{rel_dir} 不在位：20e 的扫描目录读不到，这条登记缺口判据正在空转")
            continue
        for path in sorted(root.glob("*.sh")):
            seen += 1
            rel = path.relative_to(ROOT).as_posix()
            if rel in registered:
                continue
            problems.append(f"{rel}：交付链目录里的 shell 没登记进 SHELL_SOURCES（也不在豁免表里）"
                            f"——文案卫生、bash -n、curl 有界、外部输出出处这几道门在这一份上全是空的"
                            f"（新写一份派发/取证脚本忘了登记，第一次上机才会看出来）")
    if not seen:
        problems.append("第 20 条 20e 在扫描目录里一份 .sh 都没看到：交付链 shell 要么真的没了，"
                        "要么口径已经和交付链脱节（扫 0 个文件的「登记齐全」是自欺）")
    return problems


# ── 20f：独立起来的 python 不许继承调用方的 stdin ────────────────────────────
# 由来（2026-11-13 本机实发，就在分块续传把 261 MB 完整取回来之后）：脚本被后台启动、启动它
# 的那个终端随后被回收，于是 fd 0 是个**已关闭**的描述符；python 在解释器初始化阶段就崩了：
#   Fatal Python error: init_sys_streams: can't initialize sys standard streams
#   OSError: [Errno 9] Bad file descriptor
# 崩在 `-c`/脚本体第一行之前，所以门自己的 print 一句都不会出现——日志里只剩上一行的标题。
# 这一条和 20a/20d 同族（都是「不会自己停 / 不会自己说话」），但形状不同：那种是等待，这种是
# **连启动都没完成**。写进文件的下载都完整了却过不了 zip 门，人只能看到「门不合格」四个字。
# 判的是结构而不是这一刻：`| python3 -c`（stdin 来自管道）与 `python3 - <<'PY'`（显式喂）都
# 自带 fd 0；只有「既不在管道右侧、也没有任何重定向」的那一类需要显式 `</dev/null`。
# 为什么范围只限手写脚本、不含 workflow 的 bash 步：Actions 给每一步的 stdin 本身就是关好的，
# 那边一处 11 个调用点全都要改却没有对应的风险——把「本机被终端回收」这件事写成云端的要求，
# 得到的只是噪音（判据越界和判据缺失一样会逼人绕过它）。
PY_INVOKE_RE = re.compile(r"(?:^|[\s=(\"'])("
                          r"(?:[.\w+/~-]*python3?|/[\w.+-]*/python3?)"
                          r"|\$\{?[A-Za-z_]\w*\}?"
                          r")(?=[\s\"';|&)]|$)")
PY_STDIN_MARKS = ("<<", "< /dev/null", "</dev/null", "< /dev/null/")
# 断开一条命令的单字符分隔符：走到这里就是「本段的起点」。
PY_SEG_BREAK = "|&;()"
# 前面这几个词说明那个 python 是**被查询的名字**而不是被执行的命令（`command -v python3`
# 找解释器、`which python3` 同理）：它们不启动解释器，也就没有 fd 0 的事。
PY_LOOKUP_WORDS = ("-v", "-V", "--version", "whereis")
# 这几个动词本身是「查询」而不是「执行」：后面跟着 -v 之类时，那个 python 只是被查的名字。
# `command` 是唯一的例外，它不带 -v 时真的会执行后面的命令（`command python3 x.py`），
# 所以要和 PY_LOOKUP_WORDS 配对才降级成查询。
PY_LOOKUP_COMMANDS = {"command"}
# 这几个动词**永远**只是在问「叫什么、在哪儿」，参数不带 -v 也一样：本轮夹具实测时
# `which python3` 被当成调用点，就是一条例如有害的假红（这条门的读者会照着行号去改脚本）。
PY_QUERY_ONLY = {"which", "where", "whereis", "type"}
# 这几个动词自己不是解释器，但它们**会执行**后面的参数：看门狗、提权、缓冲包装都算。
# `timeout -s ABRT -k 10 python3 -u x.py`（批跑那条就是这个形状）里 python 确实被执行，
# 只是隔了一层；把它当「前面有别的东西所以不算」得到的不是假红就是假绿——两种都见过，
# 所以按动词分类而不是按位置数。
PY_WRAPPERS = {"timeout", "gtimeout", "nohup", "sudo", "env", "time", "stdbuf",
               "nice", "ionice", "caffeinate", "xargs", "exec"}
# 这些词出现在命令位之前不占用命令位：`if ! python3 …` 里 python 仍然是那条命令。
PY_PREFIX_WORDS = {"if", "!", "then", "else", "elif", "while", "until", "for", "do", "in"}
# 一个「词位上的变量展开」：`$PY`、`${PY}`、"$PY"、"$TMO"。
PY_WORD_VAR_RE = re.compile(r'^"?\$\{?[A-Za-z_]\w*\}?"?$')


def _quote_masks(text: str):
    """返回 (inert, quoted) 两张位图：
      inert[i]  —— 第 i 位落在**单引号**内容里：那整段是纯文本。`python3 -c '…; python3 y'`
                   里那半个 python 不是调用，把它当调用点会得到一条和真缺陷一模一样的假红。
      quoted[i] —— 落在单/双任一种引号内容里：双引号不改变命令位（`"$PY" build.py` 是真调用），
                   但里面的 `;` `|` 只是字符，不能拿来切段。
    """
    inert, quoted = [False] * len(text), [False] * len(text)
    quote, i = "", 0
    while i < len(text):
        ch = text[i]
        if quote == "'":                       # 单引号里什么都没有转义
            inert[i] = quoted[i] = True
            if ch == "'":
                quote = ""                     # 闭引号这一位已经标过，内容到它为止
            i += 1
            continue
        if quote == '"':
            quoted[i] = True
            if ch == "\\" and i + 1 < len(text):
                quoted[i + 1] = True           # `\"` 不闭引号
                i += 2
                continue
            if ch == '"':
                quote = ""
            i += 1
            continue
        if ch in "\"'":
            quote = ch                         # 开引号：这一位算外面，里面从下一位开始
            i += 1
            continue
        if ch == "\\" and i + 1 < len(text):
            i += 2                             # 外面的转义：下一个字符不能当引号起点
            continue
        i += 1
    return inert, quoted


def _py_interp_vars(text: str) -> set:
    """被赋成「含 python 的解释器」的变量名：`PY=.venv/bin/python` 与
    `PY=$(command -v python3 || true)` 都算。不认这一路，这条门就只看得见字面 python3，
    而交付链里真正跑构建与批跑的那几处走的正是 `"$PY"`/`$PY`（20b 认 mktemp 变量是同一道理：
    判据要跟着脚本自己实际的写法走，不是跟着我以为的写法走）。
    """
    return {m.group(1) for m in re.finditer(
        r"^\s*([A-Za-z_]\w*)=[^\n]*python[^\n]*$", text, re.M)}


def _python_command_at(line: str, at: int, inert: list, quoted: list):
    """返回 (这个 token 是不是「被执行的那条命令」, 它是否由单管道喂 stdin)。"""
    if inert[at]:
        return False, False
    # 往回找本段起点：引号内的分隔符不算分隔符
    seg_start, fed = 0, False
    for i in range(at - 1, -1, -1):
        if quoted[i]:
            continue
        if line[i] in PY_SEG_BREAK:
            if (line[i] == "|"
                    and (i == 0 or line[i - 1] != "|")
                    and (i + 1 >= len(line) or line[i + 1] != "|")):
                fed = True               # 单管道右侧：stdin 由上一条命令的 stdout 给出
            seg_start = i + 1
            break
    pre = line[seg_start:at]
    # `PY="/path/python"` 里那个路径是**赋值右值**：词表上一个词的尾巴是 `=` 就算（引号只是
    # 包着右值，不算“前面有别的东西”）。判成调用会得到一条指向赋值行的假红，而这条门的读者
    # 正是照着行号去改脚本的人。
    if pre.rstrip("\"'").endswith("="):
        return False, False              # `PY=.venv/bin/python` 是赋值右值，不是执行
    words = [w for w in re.split(r"\s+", pre) if w]
    # 词上的引号是**分词的一部分**而不是内容：`"$PY" build.py` 的第一个词就是变量展开，
    # 带着引号去比对会把它当成「别的东西在前面」，于是这一路只剩假绿。
    words = [w for w in (x.strip("\"'") for x in words) if w]
    # 命令位由这一段的**第一个实义动词**决定，不是由「python 前面有没有别的东西」决定：
    #   `timeout -s ABRT -k 10 python3 -u x.py`（批跑看门狗就是这个形状）里 python 确实被执行，
    #   只是隔了一层包装器；`echo "… python3 …"` 里它只是文案。按后者那种「前面有东西就不算」
    #   一刀切，得到的不是假红就是假绿——两种都见过，所以按动词分类而不是按位置数。
    while words and (words[0] in PY_PREFIX_WORDS
                     or re.match(r"^[A-Za-z_]\w*=\S*$", words[0])):
        words.pop(0)                     # 控制词与环境前缀都在命令位之前，不占用命令位
    if not words:
        return True, fed                 # `python3 …` / `FOO=bar python3 …`：就在命令位上
    head = words[0]
    if head in PY_QUERY_ONLY:
        return False, False              # `which python3`：动词本身就只是在问路径，不启动解释器
    if head in PY_LOOKUP_COMMANDS:
        if any(w in PY_LOOKUP_WORDS for w in words[1:]):
            return False, False          # `command -v python3`：查询不是执行
        return True, fed                 # `command python3 x.py`：还是被执行
    if head in PY_WRAPPERS or PY_WORD_VAR_RE.match(head):
        return True, fed                 # 包装器或「命令装在变量里」：后面那些参数不改这个事实
    return False, False                  # 别的动词在前面：这个 token 只是某个参数


def check_python_stdin(texts=None) -> list:
    """手写交付脚本里每一次独立起 python 都必须自己带 fd 0（第 20 条 20f）。"""
    problems = []
    if texts is None:
        # 只取手写脚本那一侧：workflow 的 bash 步由 GitHub Actions 自己关 stdin（见上面的说明）
        all_texts, problems = shell_gate_inputs(("python",))
        texts = {k: v for k, v in all_texts.items() if k in set(SHELL_SOURCES)}
    if not texts:
        problems.append("第 20 条 20f 一份含 python 的手写脚本都没读到：报「stdin 都独立了」没有"
                        "担保（要么登记表漂了，要么这条真的不再需要——那也要写一句话确认）")
        return problems
    total = 0
    for label, text in sorted(texts.items()):
        # 解释器变量表读**原文**（含注释）：这里保守的方向和调用点相反——注释里出现过
        # `PY=…python…` 就说明这份脚本确实用变量装解释器，多判一句真调用远比漏判一句便宜。
        interp = _py_interp_vars(text)
        for logical, lineno in _logical_lines(_drop_comment_lines(text)):
            inert, quoted = _quote_masks(logical)
            for m in PY_INVOKE_RE.finditer(logical):
                tok = m.group(1)
                if tok.startswith("$"):
                    if tok.lstrip("$").strip("{}") not in interp:
                        continue         # 通用 `$VAR`：不是解释器变量就不硬猜它是什么
                command, fed = _python_command_at(logical, m.start(1), inert, quoted)
                if not command:
                    continue
                total += 1
                if fed:
                    continue             # 管道右侧：stdin 由上一条命令给出
                tail = logical[m.end(1):]
                if any(k in tail for k in PY_STDIN_MARKS):
                    continue             # 显式喂了 stdin（heredoc 或重定向）
                problems.append(
                    f"{label} 第 {lineno} 行的 `{tok}` 既不在管道右侧、也没带任何 stdin 重定向："
                    "启动它的终端一旦被回收，fd 0 就是一个已关闭的描述符，解释器在跑第一行之前就崩"
                    "（init_sys_streams / Bad file descriptor），日志里连门自己的 print 都不会出现；"
                    "加 `< /dev/null` 让它自己带 fd 0")
    if texts and total == 0:
        problems.append(f"第 20 条 20f 在 {len(texts)} 份手写脚本里一处 python 调用都没找到：口径已经"
                        "和交付链脱节（这一条判的是「每一次独立起 python」，一次都没有等于没判）")
    return problems


# ── 第 21 条：Qt 工具的退出顺序（2026-11-14 本机自审抓到）─────────────────────
# 现象：一份离线套件连跑 12 次崩 1 次（rc=139），且崩在**打印汇总之后**——批跑只看退出码，
#       127/127 全绿照样被读成一次失败。生产侧同一条栈 b2bcollect 早就抓到过（main 的
#       finally 里那段「窗口 → QApplication」就是那次留下的），另外四个工具的入口却仍是
#       `sys.exit(app.exec_())`：那句后面一个字都放不进去。app 比窗口**先**定义，解释器拆帧
#       按定义顺序清局部变量 → QApplication 先析构、窗口 wrapper 后析构，PyQt 拆剩下那些
#       wrapper 时撞上的是一具已经随 app 没掉的 C++ 对象。用户侧表现是「关掉工具弹一次
#       系统崩溃报告」，顺带 closeEvent 里那最后一次保存可能根本没跑。
# 判据是结构、不是文案（第 19 条那条教训：注释里写了指路自己不构成判据）：
#   ① `sys.exit(app.exec_())` 这种把返回值直接吞掉的写法一律红；
#   ② 跑事件循环的入口必须先 `rc = app.exec_()`，再在 sys.exit 之前把主窗口交出去
#      （close()/hide() + 置 None）；
#   ③ 造过主窗口的离线套件同一条纪律——它们只调 processEvents、从不进 exec_，天然绕过
#      生产那段收尾，不钉住就会一直在退出码上抖。
# 扫描范围按目录发现（*_build 顶层 .py），不是一张登记表：以后再多一个工具，它自动在判据里。
#
# 三处口径细节（都是本条初版在本机跑出来的假阳性，写下来免得被「简化」回去）：
#   · 只判**代码行**：我修 riskbird 时把旧写法 `sys.exit(app.exec_())` 原样抄进了注释里，
#     结果门把自己的注释当成现场代码判红——门读注释就等于门在判文案。
#   · 「谁跑事件循环」看**它自己建的那个 QApplication 变量**有没有被调 exec_()：套件里
#     `dlg.exec_()` / `QMessageBox.exec_` 这类字样到处都是，拿子串 .exec_() 当判据会把
#     每一份套件都拖进入口那一路。同一条绑定也用在这两行本身（`rc = app.exec_()` 与
#     `sys.exit(app.exec_())`）：右侧变量不绑 appvar 的话，`n = dlg.exec_()` 会被当成入口循环，
#     于是「主窗口取 exec_ 上方最近一次赋值」找的是弹窗那一行——判的是两段毫不相干的代码。
#   · 主窗口取 exec_ **上方最近**的那次 `x = MainWindow(`：b2bcollect 的 ui_smoke 里也有一个
#     `window = MainWindow()`，但它是函数内局部、返回时 app 还活着，那条路本来顺序就对。
#   · QApplication 的两种创建句式都算（`QApplication(...)` 与 `QApplication.instance() or
#     QApplication(...)`）：本仓库两种都在用——b2bcollect_gui.py 一份文件里就各有一处。只认后
#     一种的话，那种写法的**入口**会掉进「套件」那一路：要求的是 hide/None 而不是「先接住返回值」，
#     报的错与实际要改的行不符；更糟的是若文件里别处恰好有 hide，入口这一路就假绿了。
_QT_APP_ASSIGN_RE = re.compile(
    r"^\s*(\w+)\s*=\s*(?:QApplication\s*\.\s*instance\(\)\s*or\s*)?QApplication\s*\(", re.M)
_QT_MAINWIN_RE = re.compile(r"^\s*(\w+)\s*=\s*(?:\w+\.)?MainWindow\(", re.M)


def _qt_exec_res(appvar: str):
    """把「谁在跑事件循环」绑到这个文件自己建的那个 QApplication 变量上。

    通用写法（`x = y.exec_()` / `sys.exit(y.exec_())` 里不限变量名）会认错行：套件里
    `n = dlg.exec_()`、`QMessageBox.exec_` 这类字样到处都是，拿它当入口循环，主窗口就按
    exec_ 那一行的位置去找——找到的是弹窗那一行，判的是完全无关的两行之间有什么。
    """
    a = re.escape(appvar)
    return (re.compile(rf"^\s*(\w+)\s*=\s*{a}\s*\.\s*exec_\(\)\s*$", re.M),
            re.compile(rf"^\s*sys\.exit\(\s*{a}\s*\.\s*exec_\(\)\s*\)\s*(?:#.*)?$", re.M))


def _qt_code_lines(text: str) -> str:
    """去掉整行注释后的源码：判据只该看真会执行的那些行。"""
    out = []
    for line in text.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            out.append("")
        else:
            out.append(line.split("  # ")[0].rstrip())     # 行尾注释也不算代码
    return "\n".join(out)


def _qt_build_py_files() -> dict:
    out = {}
    for d in sorted(ROOT.iterdir()):
        if not (d.is_dir() and d.name.endswith("_build")):
            continue
        for p in sorted(d.glob("*.py")):
            out[p] = p.read_text(encoding="utf-8", errors="replace")
    return out


def check_qt_shutdown_teardown(files=None) -> list:
    """自己跑 Qt 事件循环 / 造过主窗口的文件，必须赶在解释器之前把窗口交出去（第 21 条）。"""
    problems = []
    files = _qt_build_py_files() if files is None else dict(files)
    judged = 0
    for path, text in files.items():
        name = getattr(path, "name", str(path))
        code = _qt_code_lines(text)
        wins = list(_QT_MAINWIN_RE.finditer(code))
        app_m = _QT_APP_ASSIGN_RE.search(code)
        if not wins and app_m is None:
            continue
        appvar = app_m.group(1) if app_m else None
        # 这条正则对 `rc = app.exec_()` 和 `sys.exit(app.exec_())` 两种写法都成立（后者也确实在跑
        # 循环），所以「是不是入口循环」一次判掉，具体哪一种形态留给下面分别报。
        loop = (re.search(rf"\b{re.escape(appvar)}\s*\.\s*exec_\(\)", code)
                if appvar else None)
        if appvar and not wins:
            if loop:
                judged += 1
                problems.append(f"{name}：跑着 Qt 事件循环却认不出主窗口变量（判据要求 `x = MainWindow(`）"
                                "——要么把窗口变量写清楚，要么连这条判据一起改，不许安静跳过")
            continue
        if not loop:
            # 离线套件 / 一次性脚本那一路：不进 exec_，所以只要求「每个主窗口建出来之后
            # 在文件收尾时被交出去」。
            for win in wins:
                judged += 1
                var = win.group(1)
                after = code[win.end():]
                if not re.search(rf"^\s*{re.escape(var)}\s*\.\s*(?:close|hide)\s*\(", after, re.M) \
                        or not re.search(rf"^\s*{re.escape(var)}\s*=\s*None\s*$", after, re.M):
                    problems.append(
                        f"{name}：造了主窗口（`{var} = MainWindow(`）却没在收尾时把它交出去"
                        f"（要 `{var}.hide()`/`{var}.close()` 加 `{var} = None`）——套件只调 "
                        "processEvents，绕过了生产 main 里那段有序收尾，退出码就会随机 139："
                        "全绿的套件被批跑读成一次失败")
            continue
        _assign_re, _in_exit_re = _qt_exec_res(appvar)
        if _in_exit_re.search(code):
            judged += 1
            problems.append(f"{name}：`sys.exit({appvar}.exec_())` 把返回值直接吞了，那句后面一个字都"
                            "放不进去，主窗口只能等解释器去拆——而先析构的是先定义的 QApplication")
            continue
        m = _assign_re.search(code)
        if m is None:
            judged += 1
            problems.append(f"{name}：没找到 `rc = {appvar}.exec_()` 这种「先接住返回值」的写法"
                            "（不先接住，收尾就没有能落脚的地方）")
            continue
        win = [x for x in wins if x.start() < m.start()]
        if not win:
            judged += 1
            problems.append(f"{name}：`{appvar}.exec_()` 上方找不到主窗口赋值（判据范围没覆盖到，请连判据一起改）")
            continue
        judged += 1
        var = win[-1].group(1)
        tail = code[m.end():]
        ex = re.search(r"\bsys\.exit\(", tail)
        if ex is None:
            problems.append(f"{name}：exec_() 之后没有 sys.exit，这条判据的范围没覆盖到（请连判据一起改）")
            continue
        zone = tail[:ex.start()]
        if not re.search(rf"^\s*{re.escape(var)}\s*\.\s*(?:close|hide)\s*\(", zone, re.M):
            problems.append(f"{name}：sys.exit 之前没把 `{var}` close/hide 掉——窗口 wrapper 留到"
                            "解释器收尾时才拆，那时 QApplication 已经先没了（退出 SIGSEGV）")
        if not re.search(rf"^\s*{re.escape(var)}\s*=\s*None\s*$", zone, re.M):
            problems.append(f"{name}：sys.exit 之前没把 `{var}` 置 None——引用还在，refcount 就不会"
                            "当场拆掉 C++ 对象，上面那次 close 也不算收尾完成")
    if judged == 0:
        # 这一条本来写的是 `if files and judged == 0`：喂进来的源码是空的（扫描目录没读到东西）
        # 就安静报绿。本轮反向对照当场抓到——一个文件都没读到比读到 191 个里全是无关文件更该响。
        problems.append(f"第 21 条只读到 {len(files)} 份源码，一个 Qt 入口/主窗口都没判到：口径已经和交付链"
                        "脱节（读 0 份＝扫描目录落空，读到的全不含 Qt＝判据追不上现实写法，"
                        "两种都等于这条门从没跑过）")
    return problems


# ── 第 22 条：凭证与登录态不许进历史，也不许等着被下一次 add 扫进去 ─────────────
# 由来（2026-11-14 本轮提交时自审抓到）：那一次是一句**常规**命令 `git add b2bcollect_build
# riskbird_build`——不带 -A、不是危险命令、当时还专门为了"只加该加的文件"而列了目录名——
# 索引里却当场多出 `_云展账号.json`（绑定手机号）、`acl/.acl_claim.json`（三个账号各自的
# 32 位 session，键是手机号）、`acl/.acl_device.json`（设备指纹）、chromedriver 二进制和一个
# 10 MB 的 zip。它们能被扫进来只因为一件事：**工具跑几天后会在包目录里自己写出这些文件**。
# 靠人当场看出来不是防：历史里的凭证删不掉，只能作废重发，而 push 一次就永久公开。
# 这条门判两件事，两件都不许安静通过：
#  ①名单从代码里抽（`*_NAME = "xxx.json"` 那组常量），不在门里抄一遍文件名——抄一遍就会
#    在某天改名时变成「门还在、名单已过期」那种假绿；改名后新写的名字自动进来，同时下面
#    的 fail-closed 会因为抽不到任何东西而响。
#  ②「被挡住」这件事问 git 自己的解析器（`check-ignore`），不在 Python 里重抄一遍 gitignore
#    匹配。重抄就会分叉，而分叉的方向永远是「你以为挡住了」。
STATE_NAME_RE = re.compile(r'^([A-Z][A-Z0-9_]*_NAME)\s*=\s*"([^"/\\]+\.json)"\s*$', re.M)
# 名单从**被跟踪的源码**里抽，不维护一份文件清单：手写清单正是这条门要防的那种东西——
# 今天新增一个模块、在里面声明一份新的状态文件，手写清单不会跟着长，门就安静地漏掉整类文件。
STATE_SCAN_DIRS = ("b2bcollect_build/", "riskbird_build/", "ali1688_build/", ".github/scripts/")
# 允许「是状态文件但不必被 ignore」的例外：必须写理由，且理由只能讲「这文件本来就应当入库」。
# 空表是今天的实情；留这张表是为了让「这条门红得没道理」有一个正式出口，而不是逼人回去放宽
# 正则或删判据（那样漏的就是整类文件）。
STATE_NAME_EXEMPT = {}
# 不来自常量、但同样会在开发机工作区里长出来的形状：Chrome/CDP 配置目录内部文件 + 驱动二进制。
# 每一项 = (理由, 给 check-ignore 探的路径, 精确 basename, (basename 前缀, basename 后缀))
DEFAULT_SHAPES = (
    ("Chrome/CDP 配置目录里的登录态 Cookie 库", ("probe/Default/Cookies",),
     ("Cookies",), ("", "")),
    ("Chrome/CDP 配置目录里的账号登录数据", ("probe/Default/Login Data",),
     ("Login Data",), ("", "")),
    ("Chrome/CDP 配置目录里的浏览历史", ("probe/Default/History",),
     ("History",), ("", "")),
    ("内置 chromedriver 二进制（由 .github/scripts/fetch_chromedrivers.py 现取）",
     ("probe/chromedriver", "chromedriver.exe"),
     ("chromedriver", "chromedriver.exe"), ("", "")),
    ("chromedriver 压缩包", ("probe/chromedriver.zip",), (), ("chromedriver", ".zip")),
    # 2026-11-15 本机自审补的三条：这三类当时**都在工作区里、都没被挡住**，而上一格
    # （check_profile_dirs_ignored）只在目录真实存在时才判得到——CI 的干净检出里没有配置目录，
    # 那里需要一条与磁盘无关的探针来保证 .gitignore 这几行还在。两侧都要有，缺任何一侧
    # 得到的都是半条门：只有探针会漏「探针没列到的形状」，只有实扫会漏「CI 上被改坏的规则」。
    ("Chrome 配置目录顶层的 Local State（Cookie 主密钥就在这一份里）",
     ("probe/Local State", "a/b/probe/Local State"), ("Local State",), ("", "")),
    ("Chrome 配置目录 Default/ 里的自动填充与账号数据",
     ("probe/Default/Account Web Data", "probe/Default/Web Data"),
     ("Account Web Data", "Web Data"), ("", "")),
    ("Chrome 配置目录 Default/ 里的会话与标签状态",
     ("probe/Default/Current Session", "probe/Default/Session Storage"),
     ("Current Session", "Session Storage"), ("", "")),
)


# 第三格判据（2026-11-15 本机自审抓到）：**认「这是一份 Chrome user-data-dir」这件事**，
# 不认我们自己起的目录名。上面那些按内部文件形状的探针列不全——本机四个开发用 profile
# 里，除 Cookies/Login Data/History 之外还有 1528 个文件是 git 看得见的（`Local State`、
# `Default/Account Web Data`、`Default/Web Data`、`Default/Session Storage/`、各种缓存与
# .crx）。也就是说「下一次 `git add <目录>` 会不会把它们扫进索引」的真实答案是会，而门
# 当时报的是绿：形状清单式判据的失效方式从来不是报错，是**列漏一类就安静漏一类**。
# 所以这里判性质：目录里出现 Chrome 自己写的标志文件/子目录（`Local State`、`Default/`）
# 就认定它是配置目录，然后要求 git 对它下面的**每一个**文件都说"看不见"。
PROFILE_DIR_MARKERS = ("Local State", "Default")
# 扫描范围：交付目录 + 开发脚本目录（A/B 探针在那里长 profile）+ 门禁自己的目录。
PROFILE_SCAN_DIRS = ("b2bcollect_build", "riskbird_build", "ali1688_build", "debug", ".github")
# 深度上限：配置目录只会是「某个项目目录里的一层」，更深就不是我们的落点；限深同时
# 避免 walk 进 dist/ 这类几万文件的构建产物里空转。
PROFILE_DIR_MAX_DEPTH = 3


def find_profile_dirs(base=None, max_depth=PROFILE_DIR_MAX_DEPTH):
    """工作区里现存的 Chrome 配置目录：返回 (仓库相对路径列表, 扫描本身的问题列表)。

    只认标志、不认名字：``Local State``（文件）或 ``Default``（子目录）是 Chrome 自己写的，
    我们的目录名随便改都逃不掉；反过来，一个恰好叫 ``xxx_profile`` 的源码目录不会被误判，
    因为里面不会有那两个标志。
    """
    base = ROOT if base is None else Path(str(base))
    found, misses = [], []
    for top in PROFILE_SCAN_DIRS:
        d = base / top
        if not d.is_dir():
            misses.append(f"第 22 条扫不到 {top}/：配置目录防护的扫描范围靠这几个目录，"
                          "读不到不等于「没有配置目录」，按不合格算")
            continue
        base_depth = len(d.parts)
        for cur, subdirs, files in os.walk(str(d)):
            p = Path(cur)
            if len(p.parts) - base_depth > max_depth:
                del subdirs[:]
                continue
            subdirs[:] = [s for s in sorted(subdirs) if s not in (".git", "__pycache__")]
            if "Local State" in files or "Default" in subdirs:
                found.append(_rel_posix(p.relative_to(base)))
                del subdirs[:]          # 配置目录内部不再往下找：整目录一起判
    return sorted(set(found)), misses


def check_profile_dirs_ignored(dirs=None, base=None) -> list:
    """每份 Chrome 配置目录：git 必须对它下面的每个文件都说"看不见"（第 22 条第三格）。"""
    problems = []
    if dirs is None:
        dirs, scan_misses = find_profile_dirs(base=base)
        problems += scan_misses
    else:
        dirs = sorted(str(x) for x in dirs)
    if not dirs:
        return problems
    for rel in dirs:
        rc, out = _git("ls-files", "--", rel)
        if rc != 0:
            problems.append(f"配置目录 {rel} 问不出「有没有已进历史」：git ls-files 退出码 {rc}"
                            "（0 才是结论，非 0 一律按不合格算）")
        else:
            inside = [x for x in out.splitlines() if x.strip()]
            if inside:
                problems.append(
                    f"Chrome 配置目录 {rel} 里有 {len(inside)} 个文件已经被 git 跟踪"
                    f"（{', '.join(inside[:3])}{' 等' if len(inside) > 3 else ''}）："
                    "user-data-dir 里没有任何一个字节是源码，登录态进历史只能作废重发。"
                    "先 git rm --cached 整个目录，再确认 .gitignore 挡住了目录本身")
        rc, out = _git("status", "--porcelain", "--untracked-files=all", "--", rel)
        if rc != 0:
            problems.append(f"配置目录 {rel} 问不出「add 那一刻会不会被扫进来」："
                            f"git status 退出码 {rc}（读不到结论按不合格算）")
            continue
        seen = [ln[3:] for ln in out.splitlines() if ln.startswith("?? ")]
        if seen:
            problems.append(
                f"Chrome 配置目录 {rel} 没被 .gitignore 整目录挡住：git 现在看得见里面 "
                f"{len(seen)} 个文件（{', '.join(x.rsplit('/', 1)[-1] for x in seen[:3])}"
                f"{' 等' if len(seen) > 3 else ''}）——下一次 `git add` 到这一层就把它们连同"
                "登录态一起扫进索引。按内部文件名一条条列永远会漏（本机实测漏了 1528 个），"
                "要挡的是目录本身")
    return problems


def state_names_from_code(texts=None) -> dict:
    """从代码常量里抽出「工具会写在目录里的 .json 状态文件名」：name -> 出处。"""
    if texts is None:
        index, _head = _tracked_sets()
        texts = {}
        for rel in sorted(index):
            if not rel.endswith(".py") or not rel.startswith(STATE_SCAN_DIRS):
                continue
            p = ROOT / rel
            if p.is_file():
                texts[rel] = p.read_text(encoding="utf-8", errors="replace")
    out = {}
    for rel, text in texts.items():
        for const, name in STATE_NAME_RE.findall(text):
            out.setdefault(name, f"{rel}:{const}")
    return out


def _check_state_shape(problems, index, label, origin, probes, exact=(), pfx="", sfx=""):
    """一个形状的两条判据：没被跟踪 + 被 git 挡住。两条各自报，不合并成一条。"""
    hit = []
    for path in index:
        base = path.rsplit("/", 1)[-1]
        if base in exact or (pfx and sfx and base.startswith(pfx) and base.endswith(sfx)):
            hit.append(path)
    if hit:
        problems.append(f"{label} 已经被 git 跟踪（{', '.join(sorted(hit)[:3])}"
                        f"{' 等' if len(hit) > 3 else ''}）：{origin}。历史里的凭证删不掉，"
                        "只能 git rm --cached 之后重发；先确认这份内容的唯一归宿是 Secrets")
    misses = []
    for probe in probes:
        rc, _out = _git("check-ignore", "--no-index", "-q", probe)
        if rc == 0:
            continue
        if rc == 1:
            misses.append(probe)
        else:
            problems.append(f"{label} 问不出结论：git check-ignore 对 {probe} 退出码 {rc}"
                            f"（0=被忽略，1=没被忽略，其它=命令本身失败）：{origin}。"
                            "读不到结论按不合格算，不按合格算")
    if misses:
        # 两个探法各报一条会刷成噪音（同一条规则漏了就是漏了），所以合起来说；报的
        # 是**探的路径**而不是「建议加哪一行」：gitignore 能写对的形状有好几种，
        # 门替人写死一行，遇到只想按目录拦的那一种就成了一条必须照抄的错误指示。
        problems.append(f"{label} 没被 .gitignore 挡住（探的是 {'、'.join(misses)}）：{origin}。"
                        "下一次 `git add <目录>` 就会把它扫进索引——工作区里已经有一份"
                        "不等于挡住，判的是 add 那一刻 git 怎么说")


def check_state_files_not_tracked(names=None, shapes=None, profile_dirs=None) -> list:
    """凭证/登录态/运行时状态那些文件：既不许已经在历史里，也不许等着被下一次 add 扫进去
    （第 22 条）。第三格（profile_dirs）判的是**性质**：工作区里每一份 Chrome 配置目录，
    git 都必须看不见它下面的文件——按内部文件名一条条列永远会漏，本机实测漏了 1528 个。"""
    problems = []
    if not in_work_tree():
        return ["第 22 条读不到 git 工作区（不在 work tree 里）：这条门的两个判据都问的是 git，"
                "问不到就不许报绿——「git 不在」和「文件没问题」在两句话里长得一样"]
    names = state_names_from_code() if names is None else dict(names)
    shapes = DEFAULT_SHAPES if shapes is None else tuple(shapes)
    if not names:
        problems.append("第 22 条一个状态文件名都没抽到：常量写法换了口径就会空转，"
                        "空名单下的「全部合格」是假绿（见 STATE_NAME_RE 的说明）")
    index, _head = _tracked_sets()
    if not index:
        problems.append(f"第 22 条从 git 只拿到 {len(index)} 个被跟踪文件：清单读空时下面两个"
                        "判据全都「通过」，那种通过没有意义")
    for name, origin in sorted(names.items()):
        if name in STATE_NAME_EXEMPT:
            continue
        _check_state_shape(problems, index, name, origin, (name, f"a/b/{name}"),
                           exact=(name,))
    for why, probes, exact, (pfx, sfx) in shapes:
        _check_state_shape(problems, index, probes[0].rsplit("/", 1)[-1], why,
                           probes, exact, pfx, sfx)
    problems += check_profile_dirs_ignored(profile_dirs)
    return problems


# ── 第 23/24/25 条：给「离线回归这一层守卫」自己立门（2026-11-14 本机自审抓到三格）──────
# 这三条判的都不是采集逻辑，而是那 54 份离线套件**还在不在、在不在跑、跑出来的结论机器能不能
# 读对**。三格全是本轮人工翻日志才定位到的，而它们共同的形状是「一切正常」：
#   ①退出码口径（23）：test_keyword_resume_offline.py 打印完「全部通过：88 项断言」仍以
#     rc=139 收场（Qt/线程在解释器收尾时析构），批跑把一条全绿的套件读成一次匿名失败；同一族
#     的另一格在批跑入口自己身上——它把「挂死」配成 134/137，而本机实测（coreutils 9.7 的
#     `timeout -s ABRT -k`）看门狗返回的是它自己的 124。
#   ②套件没提交（24）：5 份套件只活在这台开发机上，git 里从来没有它们。CI 检出的仓库因此少
#     5/54 路覆盖，而批跑照跑、计数照对，没有任何一句会说「你少跑了」。
#   ③开发机绝对路径（25）：批跑入口曾经第 20 行写死某个绝对路径再 `|| exit 1`——换一台机器
#     （含 CI 打包机）第一步就退；同族的写法散在 19 份套件与 5 份 shell 里。
# 三条的范围都取自同一个来源：**批跑清单**（`run_offline_regression.sh --list`），现在有两条
# 通道读它：脚本写进 B2B_REGRESSION_LIST_FILE 的那份文件，和它的 stdout（见下面 regression_suites）。
# 不在门里重实现一遍发现逻辑（DELIVERY_DIRS + FILES + EXCLUSIONS）——重实现就会和批跑分叉，
# 而分叉的方向永远是「门比批跑宽或窄」：窄了漏判，宽了红在门自己的口径上，人第一件事是把
# 这一步跳过。第 24 条尤其依赖这条纪律：它要问的正是「批跑要跑的那些文件在不在提交里」。
REGRESSION_LIST_CMD = ("bash", "debug/run_offline_regression.sh", "--list")
REGRESSION_LIST_FILE_ENV = "B2B_REGRESSION_LIST_FILE"
REGRESSION_LIST_SCRIPT = "debug/run_offline_regression.sh"
# 红话里一条流最多带出这么多字节原文。要原文是因为本轮吃过只报字节数的亏：云端那句
# 「stdout 504 字节 / 11 行」把「那 11 行到底是什么」丢掉了，人只能靠算术反推清单在哪一步
# 断的，反推不出来；而全量贴又有几 KB，会把真线索挤出屏幕。300 字节足够认出「这是不是路径、
# 有没有 CR、有没有 ANSI 转义、是不是整份清单」。
_SCOPE_EVIDENCE_CAP = 300
# 「碰过 Qt」的口径：源码里出现 QApplication / QtWidgets 字样才判。为什么不是「造过主窗口」：
# 本轮点过一遍批跑清单，29 份不提 Qt 的套件里只有 3 份出现 MainWindow 字样，而那 3 份都是
# `W = gui.MainWindow`（取类引用）或 `MainWindow.__new__`（不建 C++ 对象），既没进事件循环也
# 没有在收尾时拆的 wrapper，构不成退出段错误。边界写在案上，下一人才知道这一格管到哪。
_QT_SUITE_HINT = re.compile(r"\bQApplication\b|\bQtWidgets\b")
_OS_EXIT_RE = re.compile(r"^\s*os\._exit\(")
# 只认 **stdout** 的 flush：缓冲区里那段「全部通过」是打在 stdout 上的，而 CPython 的
# stderr 从 3.9 起就是行缓冲/write_through，不存在同样的丢失窗口。上一版把 stderr 也算过，
# 于是「只 flush 了 stderr」的套件会躲过这一判，而报出来的那句话说的却是「没有
# sys.stdout.flush()」——判据与文案不一致的门，红起来没人能照着修（今天 25 份里 25 份都
# 显式 flush 了 stdout，收紧不会误伤，只会挡住将来那份漏写的一侧）。
_FLUSH_RE = re.compile(r"^\s*sys\.stdout\.flush\(\)\s*$")
# 交完退出码之后不该再有会执行的代码，所以判的是「最后一行代码」；往前 4 行内要看到 flush，
# 否则缓冲区里那段汇总还没落盘就把结论交出去了——屏幕上绿、退出码红，正是第 23 条要拦的形状。
_EXIT_FLUSH_WINDOW = 4


def _silent_unlink(path: str) -> None:
    """删一份本次即弃的证据文件，删不掉也不影响判定。

    这份文件是**证据**而不是产物：判定已经把它读完，留着只是在这台机器上多一份没人认领的
    临时文件（离线回归那侧「批跑不许把假数据写进开发机」盯的就是这类遗留物）。而它删失败
    绝不能变成一条红——那会让人以为清单取不到是文件系统的问题。
    """
    try:
        os.unlink(path)
    except OSError:
        pass


def _stream_evidence(raw: str) -> str:
    """一条流的取证：多少字节 / 多少非空行 + 原文片段。

    原文必须以 repr 交出，不能直接贴：\\r、ANSI 转义这类字节一旦原样写进日志，终端会拿
    后写的字**覆盖**先写的字，人看到的就不是那批字节了——本轮读云端那份日志恰好吃过这一
    手（看到「stderr 0 字节」和「stdout 504 字节 / 11 行」这两句时无法判断它们是渲染结果
    还是真实形状）。取证要交给下一个人的是可核对的形状，不是视觉效果。
    """
    data = raw.encode("utf-8", errors="replace")
    lines = len([ln for ln in raw.splitlines() if ln.strip()])
    body = repr(data[:_SCOPE_EVIDENCE_CAP].decode("utf-8", errors="replace"))
    return f"{len(data)} 字节 / {lines} 行｜原文 {body}" + ("…" if len(data) > _SCOPE_EVIDENCE_CAP else "")


def _list_channel_facts() -> str:
    """解释这份签名需要的现场事实：哪个 bash、什么版本、那份脚本在磁盘上是 LF 还是 CRLF。

    行尾这一格为什么必须由门来说：Windows runner 镜像的 git 全局 `core.autocrlf=true`，
    `actions/checkout` 不覆盖它，所以**同一份提交在 Windows 工作区里可能是 CRLF**，而
    `bash` 读到 CRLF 的 shell 会死在第一句（本机实测整份脚本转 CRLF：rc=2、stdout 0 字节、
    stderr 360 字节，签名是 `set: +: invalid option` 与 `syntax error near 'do'`）。门里其它
    shell 判据走的是 Python 读字节 + `bash -n`，`-n` 只解析不执行，恰好看不见这一类差别。
    """
    facts = [f"平台 {sys.platform}"]
    bash = shutil.which("bash")
    if not bash:
        facts.append("PATH 里没有 bash")
    else:
        facts.append(f"bash={Path(bash).name}")
        try:
            proc = subprocess.run([bash, "--version"], capture_output=True, timeout=20,
                                  stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                                  errors="replace")
            first = (proc.stdout or "").splitlines()
            facts.append(f"版本 {first[0][:60] if proc.returncode == 0 and first else f'取不到（退出码 {proc.returncode}）'}")
        except (OSError, subprocess.TimeoutExpired) as exc:
            facts.append(f"版本取不到（{type(exc).__name__}）")
    try:
        raw = (ROOT / REGRESSION_LIST_SCRIPT).read_bytes()
        facts.append(f"{REGRESSION_LIST_SCRIPT} 在磁盘上是"
                     + ("**CRLF**" if b"\r\n" in raw else ("含孤立 CR" if b"\r" in raw else "纯 LF")))
    except OSError as exc:
        facts.append(f"{REGRESSION_LIST_SCRIPT} 读不到（{type(exc).__name__}）")
    return "｜".join(facts)


def regression_suites() -> tuple:
    """(退出码, 套件相对路径清单, stderr 摘要, stdout 原文, 清单出自哪条通道)。

    第 4、5 格是本轮（2026-11-15 Windows 云端实发）加的，加的原因是**这两格互相矛盾**：
    云端报回来的签名是「退出码 1｜stdout 504 字节 / 11 行｜stderr 0 字节」，而这一份脚本
    在 `--list` 之前一个字都不往 stdout 写、每一条 `exit 1` 都必写 stderr。要么脚本没走
    它声称的那些分支，要么**取证据的那条管道自己在换平台后不可信**。光靠一条管道没法把
    这两种情形分开，所以：

      · 清单同时经文件通道取回（脚本侧契约就写在 --list 旁边），文件不经受 stdout/stderr
        的句柄归属、伪终端、参数转换这些平台差异；两条通道都读到并且对得上，范围才算有据；
      · 红话里带出两条流的**原文**和平台事实，让下一个读日志的人拿到的是字节而不是猜测。

    stdout 的格式依据就地写明，不留给下一个人在这里凭想象：那批字节由
    `run_offline_regression.sh` 里 `printf '%s\\n' $FILES` 打印（同一份展开结果也会写进
    B2B_REGRESSION_LIST_FILE），约定是「每行一个仓库相对的 .py 路径，进度与诊断一律走
    stderr」。这句约定被离线对拍与下面三条门共同依赖，改那一句的人必须同时改这里。
    """
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    list_file = ""
    facts = _list_channel_facts()
    try:
        fd, list_file = tempfile.mkstemp(prefix="b2b_regression_list_", suffix=".txt")
        os.close(fd)
        env[REGRESSION_LIST_FILE_ENV] = list_file
    except OSError as exc:
        list_file = ""
        facts += f"｜文件通道建不起来（{type(exc).__name__}），本轮只有管道一条证据"
    try:
        proc = subprocess.run(list(REGRESSION_LIST_CMD), cwd=str(ROOT), capture_output=True,
                              stdin=subprocess.DEVNULL, timeout=120, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        if list_file:
            _silent_unlink(list_file)
        return -1, [], f"起不来：{type(exc).__name__}: {exc}", "", facts
    out = proc.stdout.decode("utf-8", errors="replace")
    files = [ln.strip() for ln in out.splitlines() if ln.strip()]
    # err_raw 留着**没被 strip** 的那一份：strip 会把首尾的 CR/LF 一起吃掉，而本轮要判的
    # 恰恰是「这批字节里有没有行尾差异」。摘要给人读，原文给判据读，两者不能是同一份。
    err_raw = proc.stderr.decode("utf-8", errors="replace")
    err = err_raw.strip().replace("\n", " | ")
    chan = (f"{facts}｜管道通道：{_stream_evidence(out)}"
            f"｜stderr 通道：{_stream_evidence(err_raw)}")
    if list_file:
        try:
            raw = Path(list_file).read_text(encoding="utf-8", errors="replace")
            file_files = [ln.strip() for ln in raw.splitlines() if ln.strip()]
            chan += f"｜文件通道：{_stream_evidence(raw)}"
        except OSError as exc:
            file_files = []
            chan += f"｜文件通道读不到（{type(exc).__name__}）"
        _silent_unlink(list_file)
        if file_files and file_files != files:
            # 两条通道不一致时按文件通道为准，但把差分说出去：差分本身就是那条平台缺陷的
            # 形状（管道少读了哪几行、多读了什么），藏起来就等于把证据又丢回给人猜。
            only_pipe = [p for p in files if p not in file_files][:3]
            only_file = [p for p in file_files if p not in files][:3]
            chan += (f"｜**两条通道不一致**：管道 {len(files)} 项、文件 {len(file_files)} 项，"
                     f"管道独有 {only_pipe}、文件独有 {only_file}（判定按文件通道）")
            files = file_files
        elif file_files:
            chan += f"｜两条通道一致（各 {len(file_files)} 项）"
            files = file_files
        else:
            chan += "｜文件通道空（清单仍按管道，两条证据一起交给判据）"
    return proc.returncode, files, err, out, chan


def _regression_scope_problem(rc: int, files: list, err: str, out: str = "",
                              chan: str = "") -> list:
    """清单读不到＝这三条门一条都没跑。这时候必须响，不能按「没问题」返回空表。

    证据要把**两条通道的原文、退出码和平台事实**一起摊开（2026-11-15 云端实发）：上一版只
    报「stdout 几个字节、stderr 几个字节」，并在文案里断言「两条流都是空的就说明脚本是被静默
    结束的」。可云端那一次 stdout 有 504 字节 11 行——**文案说的和证据给的不是同一件事**，
    人照着那句去查 errexit，而真方向在通道上。所以现在的红话按实际签名分支措辞：空就说不空，
    非空就把原文交出来。
    """
    if rc != 0:
        shape = []
        if out.strip() and not err:
            shape.append("stdout 有原文而 stderr 一个字都没有：这一份脚本每一条 exit 1 都必写 "
                         "stderr，所以这个组合不该出现——先怀疑取证据的通道，而不是脚本在撒谎")
        elif not out.strip() and not err:
            shape.append("两条流都是空的：脚本被静默结束（继承来的 errexit 撞上一条正常也会非零"
                         "的命令是最常见的一种），先照第 15/28 条的口径查那一份 shell")
        if err:
            shape.append(f"stderr 原文：{err[:200]}")
        return [f"批跑清单取不到（`{' '.join(REGRESSION_LIST_CMD)}` 退出码 {rc}｜"
                f"{_stream_evidence(out)}）：第 23/24/25 条的范围全长在这一份清单上，读不到就一条"
                f"都没判，不能报绿。{('；'.join(shape)) if shape else ''}"
                + (f"｜现场：{chan}" if chan else "")]
    if not files:
        return ["批跑清单是空的（退出码 0，可两条通道都没读出一行路径）：三条门会在空集合上各自"
                "「通过」，那种通过等于这三条门从没跑过"
                + (f"｜现场：{chan}" if chan else "")]
    return []


def check_suite_exit_code_guard(suites=None) -> list:
    """跑过 Qt 的离线套件必须自己在收尾把退出码交给操作系统（第 23 条）。"""
    problems = []
    if suites is None:
        rc, files, err, out, chan = regression_suites()
        problems += _regression_scope_problem(rc, files, err, out, chan)
        suites = files if rc == 0 else []
    judged = 0
    for rel in suites:
        path = ROOT / rel
        if not path.is_file():
            problems.append(f"{rel}：在批跑清单里却读不到源码，这条门判不了它，批跑也会在同一份"
                            "文件上少跑一路（先把文件补齐，再谈退出码）")
            continue
        code = _qt_code_lines(path.read_text(encoding="utf-8", errors="replace"))
        if not _QT_SUITE_HINT.search(code):
            continue
        judged += 1
        lines = [ln for ln in code.split("\n") if ln.strip()]
        exits = [i for i, ln in enumerate(lines) if _OS_EXIT_RE.match(ln)]
        if not exits:
            problems.append(
                f"{rel}：建过 QApplication/QtWidgets 却没有 os._exit 收尾（最后一行代码是"
                f" `{lines[-1].strip()[:56]}`）——Qt 与线程的析构留在解释器收尾里，段错误和判定"
                "毫无关系却把退出码改成非零，一条全绿的套件就这样被批跑读成一次失败。"
                "修法：把结论打完（flush）之后自己 os._exit(结论)")
            continue
        if not _OS_EXIT_RE.match(lines[-1]):
            problems.append(
                f"{rel}：os._exit 之后还有会执行的代码（最后一行 `{lines[-1].strip()[:56]}`）——"
                "收尾写在交退出码之后就是死代码，最终退出码仍由解释器那一段决定")
        for i in exits:
            near = lines[max(0, i - _EXIT_FLUSH_WINDOW):i]
            if not any(_FLUSH_RE.match(x) for x in near):
                problems.append(
                    f"{rel}：`{lines[i].strip()[:40]}` 往前 {_EXIT_FLUSH_WINDOW} 行里没有 "
                    "sys.stdout.flush()——缓冲区里那段汇总还没落盘就把退出码交了出去，"
                    "屏幕上是一套话、批跑读到的是另一套")
    if judged == 0:
        problems.append(f"第 23 条在 {len(suites)} 份套件里一份「碰过 Qt」的都没判到：口径已经和"
                        "批跑脱节（清单读空、或 Qt 字样换了写法，两种都等于这条门从没跑过）")
    return problems


def check_regression_suites_tracked(suites=None) -> list:
    """批跑清单里的每一份套件都必须已经在提交里（第 24 条）。"""
    if not in_work_tree():
        return ["第 24 条读不到 git 工作区（不在 work tree 里）：这条门问的就是「git 里有没有"
                "这一份」，问不到不许按合格返回"]
    problems = []
    if suites is None:
        rc, files, err, out, chan = regression_suites()
        problems += _regression_scope_problem(rc, files, err, out, chan)
        suites = files if rc == 0 else []
    judged = 0
    for rel in suites:
        path = ROOT / rel
        if not path.is_file():
            problems.append(f"{rel}：在清单里但磁盘上没有——批跑入口会在开跑前把它拦下来，"
                            "这条门同步点名，别让「少一路覆盖」只留在 shell 的一句报错里")
            continue
        judged += 1
        state = tracked_state(path)
        if state != "ok":
            problems.append(f"{_state_hint(rel, state)}——它是批跑清单里的一路回归：本机跑绿的"
                            "那份断言，构建机和检出仓库里压根没有")
    if judged == 0:
        problems.append(f"第 24 条一份套件都没判（清单 {len(suites)} 项全读不到）：范围来源断了，"
                        "空集合上的「合格」不算回归覆盖过了")
    return problems


# 开发机绝对路径的两种形状：类 unix 的用户目录、Windows 的用户目录。前面那串否定断言是为了
# 不把 `https://site/home/xxx` 这类 URL 当成文件系统路径（词字符与 `/`、`:`、`.`、`~` 之后
# 的 `/home/` 不是路径开头）；家目录一律要求「问环境」（Path.home()），它自己会跟着用户走。
DEV_PATH_PAT = re.compile(r"(?<![A-Za-z0-9_/:.~])/(?:Users|home)/[A-Za-z0-9_.-]+/"
                          r"|[A-Za-z]:[\\/]{1,2}Users[\\/]{1,2}")
# 带理由的豁免：键是文件、值是「这一处允许的理由 + 认出它需要的字样」。
# 登记成「文件 + 字样」而不是只登记文件，是因为整份文件免检会让下一条同类写法顺着这张表混进来；
# 而每一处豁免都要求**今天仍然命中**，否则算名单过期（和 DIVERGENT_VENDORED、GENERATED_AT_BUILD
# 同一套做法：登记表只管当下这一处，不管永远）。
DEV_PATH_EXEMPT = {
    # 门读到自己：豁免表要起作用，就必须把被豁免的那串字节原样写在这里。这不是漏网，是这条
    # 判据必然的形状——所以也照样登记成「字样 + 理由」，把这一行删掉时名单会跟着响。
    ".github/scripts/check_packaging_manifest.py": [
        ("/Users/jhnoplan/Desktop/AI云展",
         "本表里那条 oss_pool_client 豁免登记的原样字样（判据要能认出被豁免的行，就必须在案）"),
    ],
    # 说明文字里的 Windows 8.3 短路径示例：没有任何代码会去 open 它。让门去猜「这一句是不是
    # 文案」迟早误报（误报的代价是把门跳过），所以这种走豁免表，理由写在表上而不是靠人记住。
    "b2bcollect_build/b2bcollect_gui.py": [
        ("ADMINI~1", "docstring 里举的 8.3 短路径例子（讲的是引号套引号的坑），不是被读取的路径"),
    ],
    "b2bcollect_build/test_frozen_delivery_offline.py": [
        ("ADMINI~1", "断言文案里引的实机日志形状，不是被读取的路径"),
    ],
    "b2bcollect_build/oss_pool_client.py": [
        ("/Users/jhnoplan/Desktop/AI云展",
         "开发态兜底读 iOS 工程里的 OSS 密钥（_M_FILE_CANDIDATES）：打包态走 frozen 分支、"
         "绝不读它，删掉只会让每台开发机手工往 config.json 粘密钥——这条豁免的是那一行本身，"
         "不是整份文件"),
    ],
}


def _dev_path_inputs() -> dict:
    """第 25 条的视野：进包与交付目录源码 + 批跑清单 + 交付 shell + 门禁自己 + workflow。"""
    out = {}

    def add(path):
        if path.is_file():
            out[path] = path.read_text(encoding="utf-8", errors="replace")

    for d in sorted(ROOT.iterdir()):
        if d.is_dir() and d.name.endswith("_build"):
            for p in sorted(d.glob("*.py")):
                add(p)
    rc, files = regression_suites()[:2]
    if rc == 0:
        for rel in files:
            add(ROOT / rel)
    for rel in tuple(SHELL_SOURCES) + tuple(WORKFLOWS):
        add(ROOT / rel)
    for p in sorted((ROOT / ".github" / "scripts").glob("*.py")):
        add(p)
    return out


def check_no_dev_machine_paths(files=None) -> list:
    """交付链上不许出现只对一台机器成立的绝对路径（第 25 条）。"""
    problems = []
    full_scan = files is None
    files = _dev_path_inputs() if full_scan else dict(files)
    if not files:
        return ["第 25 条一份源码都没读到（*_build 目录、批跑清单、交付 shell 全落空）："
                "扫描范围断了的时候，「没有开发机路径」这句话没有任何含义"]
    hits = {}
    scanned = set()
    for path, text in files.items():
        rel = _rel_posix(path) if not isinstance(path, str) else path
        scanned.add(rel)
        code = _qt_code_lines(text)          # 注释里的举例路径不算现场代码：判的是会被执行的那行
        bad = [ln for ln in code.split("\n") if DEV_PATH_PAT.search(ln)]
        if bad:
            hits[rel] = bad
    for rel, bad in sorted(hits.items()):
        exempt = DEV_PATH_EXEMPT.get(rel, [])
        left = [ln for ln in bad if not any(token in ln for token, _why in exempt)]
        for ln in left:
            problems.append(
                f"{rel}：`{ln.strip()[:88]}` 是只对一台机器成立的绝对路径——换一台机器或换一个"
                "用户，它指向的就是不存在的文件，而症状长得像「代码坏了」。改成按脚本自己的位置推"
                "（os.path.dirname(os.path.abspath(__file__))）或问环境（Path.home()）；"
                "确实要在某台机器上读某一处的，就在 DEV_PATH_EXEMPT 登记字样和理由")
    # 豁免表的「过期即响」：**这一份被扫到了**才谈得上判它过期。调用方喂进来的可以是任意子集
    # （离线对拍就是这么喂夹具的），拿整张表去要求一个只有一份文件的子集，会得到一串与本次
    # 判定无关的「过期」——那是门在自己造的噪音上把真信号盖掉（本轮对拍第一跑就红在这一格）。
    # 整张表的完整性由默认那一次全量扫描负责：main() 走的就是 files=None，而它同时也是 CI 走
    # 的那一条路。全量扫描下登记文件压根没进视野，同样按过期算（改名、挪目录、被排除掉三种
    # 形状的症状都是「豁免还在、字节已经不在了」）。
    for rel, entries in sorted(DEV_PATH_EXEMPT.items()):
        if rel not in scanned:
            if full_scan:
                problems.append(f"DEV_PATH_EXEMPT 登记给 {rel}，可这一份压根不在第 25 条的视野里"
                                "（改名、挪目录或换了交付目录）：豁免说的还是当年那一行，今天连读"
                                "都读不到它了，请把这条登记一起删掉")
            continue
        got = hits.get(rel, [])
        for token, why in entries:
            if not any(token in ln for ln in got):
                problems.append(f"DEV_PATH_EXEMPT 登记给 {rel} 的「{token}」今天已经不命中："
                                f"豁免说的是这一处（{why[:40]}…），路径删了或改名了就该把这条一起"
                                "删掉——挂着过期理由，下一条同类写法就会照着这张表混进来")
    return problems



# ── 第 26 条：临时目录的取值只许一处口径（2026-11-15 本机自审抓到）────────────────
# 由来：交付链里九处写成 ``Path(os.environ.get("TEMP", r"C:\\temp"))``。Mac/Linux 上根本没有
# ``TEMP`` 这个环境变量，于是兜底串 ``C:\\temp`` 变成一个**相对路径**，本机仓库里真长出过两个
# 名为 ``C:\\temp`` 的目录（仓库根一个、b2bcollect_build/ 一个）。难看只是表面：这些目录是当
# 「老登录态在哪」传给 :func:`browser_profile_dir` 的，而那条链上的 ``choose_writable`` 会先
# mkdir 写探针、再挑**第一个真能写的**目录——持久位置一旦写不进去（只读挂载、权限策略），付费
# 账号的 Cookie 就落到工作区里，等着下一次 ``git add <目录>``（第 22 条判的正是那一刻）。
# 判法刻意不判「这一行外面有没有 win32 守卫」：静态判守卫迟早误报，而误报的代价是人开始跳过这
# 一步；换成「TEMP 只许在 legacy_temp_root 那一处被读」之后，判据是**位置**而不是**上下文**，
# 既不会红在文案上，也不给「再抄一遍兜底串」留口子。两侧实现（tool_config / runtime_paths）
# 还要求结构一致：这两份是同名镜像实现，分叉的方向永远是「只有一侧被改对」。
TEMP_HELPER_FILES = {"b2bcollect_build/tool_config.py": "tool_config",
                     "riskbird_build/runtime_paths.py": "runtime_paths"}
TEMP_HELPER_NAME = "legacy_temp_root"


# 什么算「读了 TEMP」：AST 里的三个形状 —— ``os.environ.get("TEMP"…)``、
# ``os.environ["TEMP"]``、``os.getenv("TEMP"…)``。按 AST 而不是按正则有两条硬理由：
# ①注释与 docstring 里的举例不算判据（这条门的其它几格都是这个口径，红在文案上只会逼人跳过）；
# ②源码解析不过要**响**，而不是安静地当作「没读到」——坏源码上的「没有第二处取值」是假绿。
TEMP_ENV_READS = ("get", "getenv")


def _temp_read_sites(text):
    """代码里真实读取 TEMP 的位置：返回 (行号列表, 行号->最外层函数名, 解析错误)。

    认三种写法：``os.environ.get("TEMP"…)`` / ``os.getenv("TEMP"…)`` / ``os.environ["TEMP"]``。
    ``from os import environ`` 那种不认——今天交付链里没有这个形状，写一条判据去猜「将来可能
    这么写」得到的多是假红；真出现了，补一行成本很低，而漏一条的成本是又长出一次 ``C:\\temp``。
    """
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        return [], {}, f"第 {e.lineno} 行 {e.msg}"
    spans = {}          # 行号 -> 所在最外层函数名（顶层代码不进这张表）
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for ln in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                spans[ln] = node.name

    def _is_os_environ(z):
        return (isinstance(z, ast.Attribute) and z.attr == "environ"
                and isinstance(z.value, ast.Name) and z.value.id == "os")

    def _is_plain_os_getenv(z):
        # ``os.getenv("TEMP")`` 与 ``os.environ.get("TEMP")`` 是同一个坑的两种写法：
        # 本机自审第一版只认了后一种，反向对照格（AJ 第 5 节）当场把这条门咬穿——
        # 判据写全三种写法是靠夹具钉住的，不是靠 docstring 里那句声明。
        return (isinstance(z, ast.Attribute) and z.attr == "getenv"
                and isinstance(z.value, ast.Name) and z.value.id == "os")

    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in TEMP_ENV_READS and _is_os_environ(node.func.value):
            first = node.args[0] if node.args else None
        elif isinstance(node, ast.Call) and _is_plain_os_getenv(node.func):
            first = node.args[0] if node.args else None
        elif isinstance(node, ast.Subscript) and _is_os_environ(node.value):
            first = node.slice
        else:
            continue
        if isinstance(first, ast.Constant) and first.value == "TEMP":
            hits.append(node.lineno)
    return sorted(set(hits)), spans, None


def _helper_body(text, name):
    """顶层 def name 的函数体（去掉 docstring）的结构指纹：(指纹或 None, 错误说明或 None)。"""
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        return None, f"解析不过（第 {e.lineno} 行 {e.msg}）"
    found = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name]
    if len(found) != 1:
        return None, f"应当**恰好有一个**顶层定义，实际 {len(found)} 个"
    body = [n for n in found[0].body
            if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                    and isinstance(n.value.value, str))]
    return ast.dump(ast.Module(type_ignores=(), body=body)), None


def check_temp_dir_single_source(files=None) -> list:
    """TEMP 只许在 :func:`legacy_temp_root` 那一处被读，且两侧实现结构一致（第 26 条）。"""
    problems = []
    full_scan = files is None
    files = _dev_path_inputs() if full_scan else dict(files)
    if not files:
        return ["第 26 条一份源码都没读到（交付目录、批跑清单、交付 shell 全落空）："
                "扫描范围断了的时候，「没有第二处 TEMP 取值」这句话没有任何含义"]

    def _rel(path):
        return path if isinstance(path, str) else (_rel_posix(path) or str(path))

    read_sites = 0
    # 只判 .py：这一条管的是「代码里谁去读了 TEMP 这个环境变量」，shell 与 workflow 里
    # 的 ``$TEMP`` 是另一种语言、另一种取值形状（那边由第 6/14 条的 shell 判据看着）。
    # 不筛掉非 .py 的后果是本机实测那样：把 .yml 和 .sh 送去 ast.parse，得到十几条
    # 「解析不过」——门在自己的视野边界上造噪音，而噪音的代价是整条门被跳过。
    for path, text in sorted(files.items(), key=lambda kv: _rel(kv[0])):
        rel = _rel(path)
        if not rel.endswith(".py"):
            continue
        hits, spans, err = _temp_read_sites(text)
        if err:
            problems.append(f"{rel} 解析不过（{err}）：第 26 条读不到这一份里的取值点，"
                            "按不合格算而不是按「这处没问题」算")
            continue
        for lineno in hits:
            read_sites += 1
            # 唯一允许的一处：两侧 helper 自己的函数体内
            if rel in TEMP_HELPER_FILES and spans.get(lineno) == TEMP_HELPER_NAME:
                continue
            line = text.splitlines()[lineno - 1].strip()[:100]
            # 该走哪一侧的 helper：按文件所在交付目录判，两镜像同口径（判据只有一处实现）
            mod = ("tool_config" if rel.startswith("b2bcollect_build/") else "runtime_paths")
            problems.append(
                f"{rel} 第 {lineno} 行自己读 TEMP：{line}\n"
                f"   → 非 Windows 上 ``TEMP`` 压根不存在，兜底串会变成一个**相对**目录，而这个"
                f"目录是当「老登录态在哪」传给择路链的：写探针会把它在当前工作目录里 mkdir 出来"
                f"（本机实证：仓库根与 b2bcollect_build/ 各长出过一个 ``C:\\temp`` 目录）。"
                f"改走 {mod}.{TEMP_HELPER_NAME}()")

    # 只在**全量扫描**下判空转：子集扫描（离线对拍喂进来的夹具）本来就只带一两份文件，
    # 拿整条链的读取点总数去要求一个子集，得到的是一串与本次判定无关的红（第 25 条同一课）。
    if full_scan and read_sites == 0:
        problems.append("第 26 条一处 TEMP 读取都没看到，连 legacy_temp_root() 里那一处都没有："
                        "取值口径被人整段删掉时这条门必须响，不许在空集合上报绿")

    # 两侧实现：必须在、必须恰好一个顶层 def、结构必须一致（同名镜像实现最容易分叉）
    bodies = {}
    for rel, mod in sorted(TEMP_HELPER_FILES.items()):
        text = None
        for path, t in files.items():
            if _rel(path) == rel:
                text = t
                break
        if text is None:
            try:
                pth = ROOT / rel
                text = pth.read_text(encoding="utf-8", errors="replace") if pth.is_file() else None
            except OSError:
                text = None
        if text is None:
            problems.append(f"第 26 条读不到 {rel}：那一份 {mod}.{TEMP_HELPER_NAME}() 是唯一"
                            "允许读 TEMP 的地方，读不到就等于这条门在这一侧从没跑过")
            continue
        dump, err = _helper_body(text, TEMP_HELPER_NAME)
        if err is not None:
            problems.append(f"{rel} 里的 {TEMP_HELPER_NAME}() 判不出结构：{err}。"
                            "被覆盖、被改名或搬进类里，取值口径就又回到「各处各写一遍」")
            continue
        bodies[rel] = dump
    if len(bodies) == len(TEMP_HELPER_FILES) and len(set(bodies.values())) > 1:
        _a, _b = sorted(TEMP_HELPER_FILES)
        problems.append(f"两份 {TEMP_HELPER_NAME}() 的实现结构不一致（{_a} 与 {_b}）："
                        "同名镜像实现分叉的方向永远是「只有一侧被改对」，而调用点各引一侧，"
                        "换一台机器就换一种落点")
    return problems


# ── 第 27 条：模块级不许取用「当场还没绑定的名字」（2026-11-15 本机自审抓到）──────
# 由来：按第 25 条把批跑套件引导段里写死的 /Users/... 换成按 __file__ 推时，五份套件的**新代码**
# 用了当场还没绑定的名字——四份用了没导入的 Path、一份用了从未定义的 _BUILD。这五处在同一批回归
# 里一起崩，症状是五条**没有任何理由**的 FAIL：引导段一行判定都没打，而批跑入口的判定行只认
# 「套件汇总的最后一行」，取不到就留空（那一半由 debug/run_offline_regression.sh 的 _pick_reason
# 补上）。修完五份再把全仓扫了一遍（同一类缺陷要一次查全），还剩一处恒假死分支里的 io.open：
# 它今天不炸，谁把那个条件翻回来就当场复现同一症状。
# 判的形状两种，都限定在「顶层语句会被当场执行的部分」：
#   (a) 名字在后面某行才 import —— 取用早于绑定；
#   (b) 名字全文件没有任何绑定 —— gui_ux 的 _BUILD 是这种。
# 函数体与 lambda 体不进视野：它们晚于 import 才执行，那种前向引用是合法的。类体要进：建类那一刻
# 就执行。装饰器、参数默认值、注解也进：def 语句执行时就求值（``from __future__ import annotations``
# 在的文件里注解不求值，就不判注解）。
# 判不到的边界写在这里，免得这条门给人「名字全都被管住了」的错觉：有 ``from x import *`` 的文件
# 整份不判（名字来源判不准，猜出来的多是假红）；``globals()[...]=``、``exec`` 造的绑定看不见——
# 但 (a) 那一半不受影响，它只问「这行 import 之前有没有人用过这个名字」。
# 视野取 _dev_path_inputs()：与第 25/26 条同一份，三条一起响应「扫描范围断了」这一件事。
_NAME_EXEMPT = frozenset(dir(builtins)) | {
    "__file__", "__name__", "__doc__", "__package__", "__spec__",
    "__loader__", "__builtins__", "__debug__", "__class__",
}


def _import_first_line(tree) -> dict:
    """import 绑定的名字 -> 它最早出现在第几行（同名字被 import 两次时取最早那处）。"""
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            pairs = [((a.asname or a.name).split(".")[0], node.lineno) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            pairs = [("*", 0) if a.name == "*" else (a.asname or a.name, node.lineno)
                     for a in node.names]
        else:
            continue
        for nm, ln in pairs:
            out[nm] = min(out.get(nm, ln), ln)
    return out


def _any_binding_name(tree) -> set:
    """文件里任何一处可能绑定名字的地方（含函数体内）：宁可放过也不误报。

    为什么放过函数体内的赋值：这一格判的是「模块级取用时名字在不在」，而一个在函数里被当过
    参数/目标的名字，仍可能在模块级是另一个含义（本轮五例里的 ``Path`` 若在全仓某处以同名参数
    出现，按严格口径就会放掉真信号——所以这里反过来：**只用来排除遮蔽**，不用来要求绑定）。
    """
    out = set()

    def add_target(node):
        for x in ast.walk(node):
            if isinstance(x, ast.Name):
                out.add(x.id)

    def add_args(a):
        for arg in list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs):
            out.add(arg.arg)
        if a.vararg:
            out.add(a.vararg.arg)
        if a.kwarg:
            out.add(a.kwarg.arg)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
            if not isinstance(node, ast.ClassDef):
                add_args(node.args)
        elif isinstance(node, ast.Lambda):
            add_args(node.args)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for tg in targets:
                add_target(tg)
        elif isinstance(node, (ast.For, ast.comprehension)):
            add_target(node.target)
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            add_target(node.optional_vars)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            out.add(node.name)
        elif isinstance(node, ast.Import):
            out.update((a.asname or a.name).split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            out.update(a.asname or a.name for a in node.names)
        elif isinstance(node, ast.Global):
            out.update(node.names)
    return out


def _module_level_uses(tree, future_annotations: bool) -> list:
    """顶层会被当场执行的部分里对裸名字的取用：[(名字, 行号)]。"""
    hits = []

    def rec(node):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                rec(dec)
            a = node.args
            for d in list(a.defaults) + [x for x in a.kw_defaults if x]:
                rec(d)
            if not future_annotations:
                for arg in list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs):
                    if arg.annotation:
                        rec(arg.annotation)
                if node.returns:
                    rec(node.returns)
            return                          # 函数体晚于 import 才执行：前向引用合法
        if isinstance(node, ast.ClassDef):
            for dec in node.decorator_list:
                rec(dec)
            for base in node.bases:
                rec(base)
            for kw in node.keywords:
                rec(kw.value)
            for st in node.body:            # 类体在建类那一刻执行
                rec(st)
            return
        if isinstance(node, ast.Lambda):
            for d in list(node.args.defaults) + [x for x in node.args.kw_defaults if x]:
                rec(d)
            return                          # lambda 体在被调用时才执行
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            hits.append((node.id, node.lineno))
            return
        if isinstance(node, ast.Attribute):
            rec(node.value)                 # X.attr：只问 X 在不在
            return
        for child in ast.iter_child_nodes(node):
            rec(child)

    for st in tree.body:
        rec(st)
    return hits


def _has_future_annotations(tree) -> bool:
    return any(isinstance(n, ast.ImportFrom) and n.module == "__future__"
               and any(a.name == "annotations" for a in n.names) for n in tree.body)


def check_module_name_binding(files=None) -> list:
    """交付链的 .py 里不许在模块级取用当场还没绑定的名字（第 27 条）。"""
    problems = []
    full_scan = files is None
    files = _dev_path_inputs() if full_scan else dict(files)
    if not files:
        return ["第 27 条一份源码都没读到（交付目录、批跑清单、交付 shell 全落空）："
                "扫描范围断了的时候，「没有取用早于绑定的名字」这句话没有任何含义"]

    def _rel(path):
        return path if isinstance(path, str) else (_rel_posix(path) or str(path))

    judged = star_imports = 0
    for path, text in sorted(files.items(), key=lambda kv: _rel(kv[0])):
        rel = _rel(path)
        if not rel.endswith(".py"):
            continue                        # shell 与 workflow 是另一种语言、另一种名字规则
        try:
            tree = ast.parse(text)
        except SyntaxError as e:
            problems.append(f"{rel} 解析不过（第 {e.lineno} 行 {e.msg}）：第 27 条读不到这一份的"
                            "取用与绑定，按不合格算而不是按「这处没问题」算")
            continue
        judged += 1
        imports = _import_first_line(tree)
        if "*" in imports:
            star_imports += 1
            continue                        # 名字来源判不准：宁可不判，也不猜一条假红出来
        binds = _any_binding_name(tree)
        reported = set()
        for nm, lineno in _module_level_uses(tree, _has_future_annotations(tree)):
            if nm in _NAME_EXEMPT or nm in reported:
                continue
            first_import = imports.get(nm)
            if first_import is not None and first_import > lineno:
                line = text.splitlines()[lineno - 1].strip()[:90]
                problems.append(
                    f"{rel} 第 {lineno} 行取用 {nm}，而 {nm} 要到第 {first_import} 行才 import：{line}\n"
                    f"   → 模块级语句是**导入这一刻**执行的，名字还没绑上就当场 NameError；"
                    f"发生在批跑套件的引导段时，套件一条判定都没打就退出，屏幕上只剩一条没有理由的"
                    f" FAIL。把 import 提到使用点之前（引导段整段照这个顺序写）")
                reported.add(nm)
            elif nm not in binds:
                line = text.splitlines()[lineno - 1].strip()[:90]
                problems.append(
                    f"{rel} 第 {lineno} 行取用 {nm}，可这一份里没有任何地方绑定它：{line}\n"
                    f"   → 同上一条一样的死法，只是连「往后挪一行 import」都救不了：这个名字压根"
                    f"不存在（本轮实况是引导段引用了一个从未定义的 _BUILD）。要么补上定义，"
                    f"要么改成这一行自己能算出来的值")
                reported.add(nm)
    if full_scan and judged - star_imports == 0:
        problems.append(f"第 27 条一份都没判到（视野里 {judged} 份 .py 全是 ``from x import *``"
                        " 或非 .py）：这一条门等于没跑，不许在空集合上报绿")
    return problems


# ── 第 30 条：失败原因收成一行，只准一个把手 ─────────────────────────────────
# 由来（2026-11-16 第八轮实机体检报告 → 第九轮同类查全）：报告里出现过
# `【连不上·无应答】b2b.11467.com:443｜Connection to b2b.11467.com timed out. (con）`
# ——英文词被切掉半边、右括号还是分型器补的。上一轮把「已分型的原因」收进 short_reason 后
# 自审发现：当时那条判据（离线 11p）扫的是 `*_crawler.py`，而交付链里另有 **41 处**各自写的
# `str(e).splitlines()[0][:N]`（界面告警、会话日志、名单通道翻译层、权限自检、健康看板）、
# `err[:70]`（回执进台账）、`resp.text[:200]`（OSS 的 XML 错误体）。**判据的视野比缺陷小，
# 就等于没修**——半截词照旧从那些路上出来，而它们恰好是用户最常看到的那几行。
#
# 口径（三种合格写法，别的都算缺陷）：
#   ① 已定性的原因 → `short_reason(原因, 上限)`；
#   ② 异常/多行文本 → `first_line(异常, 上限)`（先取首行=自然边界，再只在标点/词边界断）；
#   ③ 接不到把手的地方（另两个独立包、三目录同名拷贝）→ **整行带出**，不做任何按字符数的切。
# 真源是 b2bcollect_build/text_shape.py；1688 独立包装不进它，在 ali1688_crawler 内联一份
# 逐字节相同的实现。这一条判六件事，全是结构判据：
#   30a 交付链的运行时代码里不许再有 `….splitlines()[0][:N]`（①②③ 都不长这样）；
#       30a-2 也不许「调用完把手又补一刀 `[:N]`」——本轮同类查全抓到的正是这一种：
#       `riskbird_build/chrome_driver.py` 里 `_short_err(e)` 已经是整行带出，外面却还挂着
#       `[:90]`，看着像走了口径，实际把边界又劈开。只认把手的名字，不认变量名（避免误报）；
#   30a-3 不许 `str(异常)[:N]`（**同一族的第三种形状，也是最大的一族：本轮实测 28 处**）。
#       30a 之所以看不见它，是因为它没有 `splitlines()[0]` 那一跳：`local_scan.py` 八处、
#       `access_guard.py` 四处、riskbird 那几份采集器都写成 `f"…失败：{str(e)[:60]}"`。
#       异常里最长的那一段恰恰是尾巴上的 `Session info`/路径，所以这一刀切下来留的是
#       「看起来完整」的半截词。b2bcollect 那份走 `first_line`（口径②），另两个独立包与
#       三目录同名拷贝就整行带出（口径③）；
#   30a-4 不许「读一本已经收过口的原因账，又按字符数切一刀」。现场（2026-11-16 第十一轮
#       实机报告）：`深链被拒: china.makepolo.com 42 次（… ·仅这一条深链，通道入）`——
#       记账处写的是 `short_reason(reason, 120)`，渲染处写的是 `str(v[1])[:46]`。30a/30a-2/
#       30a-3 都认不出这两刀之间的牵连：句子先落进字典，再被循环变量接走。所以这一判要跟着
#       那一跳走（**按函数作用域**做名字传播：整份文件共用一张名字表会报出 127 处噪声，
#       噪声到这个量级就等于没有判据），并且给「切的是列表元素个数／非原因的字段」留一张
#       钉死原文的豁免表——原文一改豁免即失效，免得豁免表长成藏缺陷的抽屉；
#   30b 把手在位且两份实现（真源 / 1688 内联）**逐字节相同**——比的是**整段**（断点表、
#       连接符表、`_is_break`、两个函数），只比函数体的话分叉会落在两边都比不到的那几行；
#   30c 把手的**性质**：任何一句原因、任何一档上限，收口之后 ①切点必须在真正的词边界上
#       （判据自己带一份分隔符表/连接符表重算，不读把手的常量，也不问「最后一个字是不是
#       边界」——那会被 rstrip 与不 rstrip 两种写法轮流骗过去）②`【类别】主机:端口｜` 前缀
#       整段还在 ③括号必成对；两份实现同答；
#   30d 视野为空不许报绿（和 25/26/27 条同一句：扫描范围断了，"没有不合格"没有含义）。
#       视野另加 `REASON_RENDER_EXTRA`：实机验收报告的渲染层本体（住在 debug/，却正是
#       交付前给用户看的那份报告），按目录名把它排除掉就等于「报告那一行永远没人判」。
REASON_HANDLE = "b2bcollect_build/text_shape.py"
REASON_MIRRORS = ("ali1688_build/ali1688_crawler.py",)
# 把手整段在源码里的边界（从断点表那行注释到 `first_line` 的最后一行）。取**整段**而不是
# 逐个函数：这一族的常量（`_REASON_BREAKS`／`_REASON_JOINERS`）和共用小函数（`_is_break`）
# 也会被单独改掉，只比两个函数体的话，分叉恰好落在「两边都比不到」的那几行上。
REASON_BLOCK = ("# 可断的位置", "    return short_reason(s.splitlines()[0], limit)")
# 全仓「把一句话收成一行」的那几把把手（含第三支口径里各包自带的整行把手）：
# 它们的返回值**已经**收过口了，再被 `[:N]` 切一刀等于把选好的边界劈开——30a-2 判这个形状。
REASON_HANDLES = frozenset({"short_reason", "first_line", "_oss_brief", "_short_err",
                            "_why_short", "_request_error_detail", "request_error_shape",
                            "probe_block_reason", "http_block_reason"})
# 30a-3：异常变量的常见名字（`e`/`err`/`exc`/`ex`/`error`/`exception`，带数字后缀也算）。
# 判的是 `str(这个_name)[:N]` 这个**形状**：把一句异常按字符数切。名字表只这么宽，
# 是因为 `str(x)[:N]` 用在异常之外的写法在交付链里是 0 处（本轮全量扫过），一旦哪天出现
# 非异常的 `str(e)`，那条会被豁免表接住并要写明理由，而不是让判据整体改名换姓。
EXC_NAME_RE = re.compile(r"^(?:e|err|exc|ex|error|exception)\d*$")
# 30a-4：原因账的键与属性。这些格子里存的**本来就是收口过的一句话**（记账处都走把手），
# 渲染处再切一刀就是把选好的边界劈开。登记口径：只有「值是一句失败原因」的格子才进来，
# 数字/名单/计数那些一律不算（算进去就又变成一把按名字乱切的尺）。
REASON_LEDGER_KEYS = frozenset({
    "last_page_error", "last_goto_error", "block_reason", "dead_hosts", "deep_block",
    "landing", "page_error", "nav_error", "load_error", "reason", "_ai_reason",
    "error", "errs", "read_err", "imp_err", "last_err"})
REASON_LEDGER_ATTRS = frozenset({"_last_page_error", "_last_goto_error", "_block_reason",
                                 "_page_error"})
# 视野里额外必须读到的渲染层：实机验收报告就是交付前用户读的那份报告本体。
REASON_RENDER_EXTRA = ("debug/platform_live_check.py",)
# 30a-4 的豁免表：键 = (相对路径, 那一行原文)，值 = 为什么这不是「切一句原因」。
# 行原文一变，这一条豁免就落空（判据会报「豁免不在原位」）：它不能长成
# 「把缺陷登记成合法」的抽屉——要么这行还在、理由成立，要么就得重新解释一次。
REASON_SLICE_EXEMPT = {
    ("b2bcollect_build/local_scan.py",
     'on_progress(f"正在识别 {it[\'display\'][:30]}…", len(items), len(files))'):
        "切的是压缩包里的**文件显示名**（进度条一格的宽度，行尾自带「…」），不是失败原因；"
        "这一格里 `it` 之所以带原因色，是因为同一个函数里别处从 `reason` 格子取值传给了它",
    ("b2bcollect_build/parallel_session.py",
     '+ "、".join(dry[:6]) + ("…" if len(dry) > 6 else "")'):
        "切的是「一家没出的关键词」这个**列表的元素个数**（取前 6 个词），不是句子的字符数",
}


def _runtime_reason_sources() -> dict:
    """第 30 条的视野：会被打进包、且会把失败原因念给用户听的那些 .py。

    取三个交付目录的全部 .py，剔掉测试套件（``test_*``）、一次性调试脚本（``debug_*``、
    ``probe_*``）——它们的话术不进用户眼睛；离线套件里那一族自有对拍（第 ㉓ 节）。
    例外只有 `REASON_RENDER_EXTRA` 那一组：实机验收报告的渲染层住在 debug/，可它就是
    交付前给用户看的那份报告本体（第十一轮那行半截词正是从它那里念出来的），
    所以它必须进视野；读不到就响，不许默默少一份。
    """
    out = {}
    for d in sorted(ROOT.iterdir()):
        if not (d.is_dir() and d.name.endswith("_build")):
            continue
        for p in sorted(d.glob("*.py")):
            stem = p.stem
            if stem.startswith("test_") or stem.startswith("debug_") or stem.startswith("probe_"):
                continue
            if p.is_file():
                out[p] = p.read_text(encoding="utf-8", errors="replace")
    for rel in REASON_RENDER_EXTRA:
        p = ROOT / rel
        if p.is_file():
            out[p] = p.read_text(encoding="utf-8", errors="replace")
        else:
            out[p] = ""
    return out


def _is_zero_index(node) -> bool:
    """这个 Subscript 是不是 `[0]`：**两种解析树都要认**（Python 3.9 起 `[0]` 直接是 Constant，
    旧的 `ast.Index(value=Constant(0))` 那层壳只剩下历史文档里还有）。

    由来（2026-11-16 本轮夹具的反向对照当场抓到）：判据原来只写 `isinstance(base.slice,
    ast.Index)`，在 3.13 的解析树上**一个节点都不匹配**，于是 30a 扫完全仓报绿——绿得毫无
    根据。判据必须能被「旧写法」这条反向对照咬住，否则「没有不合格」只说明判据看不见。
    """
    if not isinstance(node, ast.Subscript):
        return False
    sl = node.slice
    if isinstance(sl, ast.Index):                      # ≤3.8 的写法（兼容旧解析树）
        sl = sl.value
    return isinstance(sl, ast.Constant) and sl.value == 0


def _rel_label(path) -> str:
    """给报告用的相对路径：夹具喂进来的**仓外/相对**路径也要能印出来。

    原来这句写的是 `path.relative_to(ROOT) if hasattr(path, "relative_to") else path`——
    `hasattr` 永远为真（Path 一直都有这个方法），所以离线夹具一喂相对路径就当场 ValueError，
    「判据可被反向对照」这句话在第一次真去喂的时候自己崩了。护栏要挡的是**语义**上的不在仓内。
    """
    text = str(path)
    root = str(ROOT)
    return text[len(root) + 1:] if text.startswith(root + "/") else text


def _slice_of_first_line(tree) -> list:
    """AST 找出「取首行之后又按字符数切」的那一行：`….splitlines()[0][:N]`。

    为什么盯这个形状而不是变量名：名字里带 err/msg/reason 的切片可能是页面正文、公司名、
    链接文案（本轮扫到的 10 处就全是），按名字判会把它们报成缺陷；而 `splitlines()[0]`
    后面紧跟一个数字切片，**只可能**是在收一句失败原因——这正是这一族的形状。
    """
    hits = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice)):
            continue
        up = node.slice.upper
        if not (isinstance(up, ast.Constant) and isinstance(up.value, int)):
            continue
        base = node.value
        if (_is_zero_index(base) and isinstance(base.value, ast.Call)
                and isinstance(base.value.func, ast.Attribute)
                and base.value.func.attr == "splitlines"):
            hits.append(node.lineno)
    return hits


def _gate_slice_of_handle(tree) -> list:
    """第二种形状：**已经收口的一句话又被按字符数切了一刀**（`_short_err(e)[:90]`）。

    30a 那种形状是「自己写截断」，这一种更隐蔽：调用点看起来已经在走把手，紧接着又补
    `[:90]`——于是把手精心挑出来的那个边界被后面那一刀重新劈开，症状一模一样，而 30a 读不出来。
    判据只认「被切的那一段是一次对这些名字的调用」，不认变量名：变量名判据在 22g 那一族
    误报过（名字里带 text/body 的切片大多是正文与公司名），误报的代价是人们把整条门关掉。
    """
    hits = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice)
                and node.slice.lower is None
                and isinstance(node.slice.upper, ast.Constant)
                and isinstance(node.slice.upper.value, int)):
            continue
        base = node.value
        if isinstance(base, ast.Call):
            name = base.func.id if isinstance(base.func, ast.Name) else \
                getattr(base.func, "attr", "")
            if name in REASON_HANDLES:
                hits.append(node.lineno)
    return hits


def _int_slice(node) -> bool:
    """这个 Subscript 是不是一个纯上界的字符切片 `x[:N]`（带 lower/step 的是“取一段”，不是收口）。"""
    return (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice)
            and node.slice.lower is None
            and isinstance(node.slice.upper, ast.Constant)
            and isinstance(node.slice.upper.value, int))


def _reads_reason(node, names) -> bool:
    """这一段表达式是不是在「读一本原因账」：碰到登记的账键字面量、账属性、或已经
    被认定为原因的名字。只认这三个来源，不认变量名里带 err/msg/reason 的写法（本轮
    扫到的 10 处那种误报根源就在按名字切）。"""
    for sub in ast.walk(node):
        if (isinstance(sub, ast.Constant) and isinstance(sub.value, str)
                and sub.value in REASON_LEDGER_KEYS):
            return True
        if isinstance(sub, ast.Attribute) and sub.attr in REASON_LEDGER_ATTRS:
            return True
        if isinstance(sub, ast.Name) and sub.id in names:
            return True
    return False


def _scope_nodes(scope) -> list:
    """本作用域的节点，**不下钻**嵌套的函数/lambda（那是另一张名字表）。

    为什么按作用域而不是整份文件共用一张：本轮先试过后一种，`r`/`e`/`rec` 这些在某个
    函数里确实接过原因的短名，会把原因色传染到全仓同一个短名——第一次实跑就报出 127 处，
    其中真正是缺陷的只 7 处。误报到这个量级，下一条门就会被人关掉。"""
    out, stack = [], [scope]
    while stack:
        n = stack.pop()
        out.append(n)
        if (isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
                and n is not scope):
            continue
        stack.extend(ast.iter_child_nodes(n))
    return out


def _reason_names_of(scope, seed=None) -> set:
    """本作用域里「装的是一句原因」的名字：从登记的账键/账属性出发，跟着赋值与
    for/推导式的绑定走（不动点，最多八轮）。`seed` 是从调用点跨进来的形参（见
    `_reason_param_taint`）。"""
    names = set(seed or ())
    binders = [n for n in _scope_nodes(scope)
               if isinstance(n, (ast.Assign, ast.For, ast.comprehension))]
    for _ in range(8):
        grew = False
        for node in binders:
            if isinstance(node, ast.Assign):
                src, dsts = node.value, list(node.targets)
            else:
                src, dsts = node.iter, [node.target]
            if not _reads_reason(src, names):
                continue
            for d in dsts:
                if d is None:
                    continue
                for x in ast.walk(d):
                    if isinstance(x, ast.Name) and x.id not in names:
                        names.add(x.id)
                        grew = True
        if not grew:
            break
    return names


def _reason_param_taint(tree) -> dict:
    """把「这是一句原因」的颜色跨过函数边界递一次：{函数名: 被污染的形参名}。

    为什么必须有这一跳：本仓立项那一行里，账在**调用点**（`lines.append(_host_reason_line(
    "深链被拒", _deep))`），刀在**被调函数**里（`f"…次（{str(v[1])[:46]}）"`）。只按单个
    作用域算名字的话，被调那个函数的形参永远接不到原因色，于是把渲染处退回旧写法也不会响—
    —「修好了但门判不出回归」与「没修」对下一个人来说是同一件事。只认本文件里定义过的函数名，
    不认跨文件调用（那需要全局调用图，而且这一族把把手就住在本文件）。不动点最多八轮。"""
    fns = {}
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fns.setdefault(n.name, n)
    taint = {name: set() for name in fns}
    for _ in range(8):
        grew = False
        for caller in fns.values():
            names = _reason_names_of(caller, taint.get(caller.name))
            for node in _scope_nodes(caller):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id in fns):
                    continue
                callee = fns[node.func.id]
                args = [a for a in node.args if not isinstance(a, ast.Starred)]
                aarg = [a.arg for a in callee.args.posonlyargs + callee.args.args
                        if a.arg != "self"]
                for i, a in enumerate(args):
                    if i < len(aarg) and _reads_reason(a, names) and aarg[i] not in taint[callee.name]:
                        taint[callee.name].add(aarg[i])
                        grew = True
                for kw in node.keywords:
                    if kw.arg is None or not _reads_reason(kw.value, names):
                        continue
                    for i, an in enumerate(aarg):
                        if an == kw.arg and an not in taint[callee.name]:
                            taint[callee.name].add(an)
                            grew = True
        if not grew:
            break
    return taint


def _slice_of_exception_str(tree) -> list:
    """30a-3：`str(异常)[:N]`。没有 `splitlines()[0]` 那一跳的同一件事。"""
    hits = []
    for node in ast.walk(tree):
        if not _int_slice(node):
            continue
        base = node.value
        if (isinstance(base, ast.Call) and isinstance(base.func, ast.Name)
                and base.func.id == "str" and len(base.args) == 1
                and isinstance(base.args[0], ast.Name)
                and EXC_NAME_RE.match(base.args[0].id)):
            hits.append((node.lineno, f"把一句异常按字符数切了一刀（str({base.args[0].id})）"))
    return hits


def _slice_of_reason_ledger(tree) -> list:
    """30a-4：读原因账的那一刀（含隔着循环变量与隔着函数形参那两跳）。返回 (行号, 命中的格子)。"""
    scopes = [tree] + [n for n in ast.walk(tree)
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))]
    taint = _reason_param_taint(tree)
    hits = []
    for sc in scopes:
        names = _reason_names_of(sc, taint.get(getattr(sc, "name", "")))
        for node in _scope_nodes(sc):
            if not _int_slice(node):
                continue
            bad = set()
            for sub in ast.walk(node.value):
                if (isinstance(sub, ast.Constant) and isinstance(sub.value, str)
                        and sub.value in REASON_LEDGER_KEYS):
                    bad.add(sub.value)
                elif isinstance(sub, ast.Attribute) and sub.attr in REASON_LEDGER_ATTRS:
                    bad.add(sub.attr)
                elif isinstance(sub, ast.Name) and sub.id in names:
                    bad.add(sub.id)
            if bad:
                hits.append((node.lineno, "读一本已经收过口的原因账又按字符数切了一刀（"
                                          f"碰了 {sorted(bad)}）"))
    return hits


def _extract_reason_block(src: str) -> str:
    """源码里把手那一段（含首尾标记行）；任一边缺标记就返回哨兵，让对拍必然响。"""
    start, end = REASON_BLOCK
    if start not in src or end not in src:
        return f"（缺标记：start={start in src} end={end in src}）"
    i = src.index(start)
    j = src.index(end, i) + len(end)
    return src[i:j]


# 性质判据用的分隔符表／连接符表：判据**自己带一份**，不去读把手里的常量。
# 不然「把手把断点表改宽成任意字符」这种最坏的改法会连判据一起改掉（判据跟着认为什么都
# 是边界），这一条门就退化成「代码说自己没问题」。30c 问的是行为，口径必须站在被审代码之外。
GATE_SEPARATORS = "｜.：，、；)） ·"
GATE_JOINERS = ".:：０．·"


def _wordish(ch: str) -> bool:
    return bool(ch) and (ch.isalnum() or ch in "-_")


def _gate_cut_is_boundary(text: str, p: int) -> bool:
    """在下标 p 处把 text 切成两段：这个切点算不算真正的词边界（判据独立重算）。

    两种写法都要认得：切点**左边**那个字是分隔符（留下 `…timed out.`，下一个字是正文），
    或切点**右边**那个字是分隔符（`…冷却）｜下一句` 把分隔符让给后半句）。只问其中一边都会
    误报——把手会 rstrip 掉行尾分隔符，那时最后一个字当然是词字符；它不 rstrip `.` 时，
    下一个字又当然是正文。真正要问的是「这里本来就该断吗」：分隔符夹在两个词字符中间
    （`b2b.11467`、`com:443`、`哈利·波特`）劈开的是一个词，不算。
    """
    def sep(ch, left, right):
        if ch not in GATE_SEPARATORS:
            return False
        if ch in GATE_JOINERS and _wordish(left) and _wordish(right):
            return False
        return True

    left_ch = text[p - 1] if p else ""
    here = text[p] if p < len(text) else ""
    right_ch = text[p + 1] if p + 1 < len(text) else ""
    return sep(left_ch, text[p - 2] if p >= 2 else "", here) or sep(here, left_ch, right_ch)


def _reason_property_cases():
    """把手的性质用例：句子取自本机实机日志与分型器的真实输出形状，不是编的。"""
    return (
        "【连不上·无应答】b2b.11467.com:443｜Connection to b2b.11467.com timed out. "
        "(connect timeout=10)",
        "【被拦停】防采页没等到放行（等人过验证或等 IP 冷却）·仅这一条深链（见上一行）",
        "HTTPSConnectionPool(host='www.makepolo.com', port=443): Max retries exceeded "
        "with url: /search/%E9%98%80.html (Caused by SSLError(SSLCertVerificationError(1, "
        "'[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed'))) ",
        "上传失败 HTTP 403: Code=AccessDenied Message=You have no right to access this "
        "object because of acl RequestId=652F0A9B1A EC=0003-00000001",
        "列表页导航失败（【页面未就绪】等不到 https://b2b.11467.com/search 的完整正文）",
        "Message: unknown error: net::ERR_CONNECTION_TIMED_OUT\n  (Session info: "
        "chrome=131.0.6778.205); For documentation on this error, please visit: https://",
        "旧版 .xls 读不了：客户库.xls（XLRDError: Cannot find workbook in OLE2 stream）",
        "这一个词特别特别特别特别特别特别长中间没有任何标点也没有空格所以任何一档上限都会把它切断",
    )


def check_reason_one_handle(files=None, handle_src=None, mirrors=None) -> list:
    """失败原因收成一行只准一个把手（第 30 条）。

    `handle_src`／`mirrors` 是给离线夹具留的注入口：30c 那条**性质判据**如果只能读盘上的
    真源，就没法证明「把断点表改回按字符切一刀」会响——判据不能被反向对照，就等于还没修。
    """
    problems = []
    full_scan = files is None
    files = _runtime_reason_sources() if full_scan else dict(files)
    # 「一份都没读到」按内容算而不是按键算：额外登记的渲染层就算读不到也会留一个空字符串键，
    # 只看 len(files) 的话，三个 *_build 全落空时会反过来撞上「真源不在位」那条——同一次事故，
    # 但后一句话把「视野断了」说成了「代码丢了」，排查的人会去找回文件而不是看 ROOT。
    if not any(str(t).strip() for t in files.values()):
        return ["第 30 条一份源码都没读到（三个 *_build 目录全落空）：视野断了的时候，"
                "「没有一处自己切原因」这句话没有任何含义"]

    # 30a 形状判据：`….splitlines()[0][:N]`，加上同一件事的另外两种写法（30a-3/30a-4）
    judged = 0
    used_exempt = set()
    for path, text in sorted(files.items(), key=lambda kv: str(kv[0])):
        try:
            tree = ast.parse(text)
        except SyntaxError as e:
            problems.append(f"{path.name} 解析不过（第 {e.lineno} 行 {e.msg}）：第 30 条读不到"
                            "这一份的收口写法，按不合格算而不是按「这处没问题」算")
            continue
        judged += 1
        rel = _rel_label(path)
        for lineno, kind in [(n, "取完首行又按字符数切了一刀") for n in _slice_of_first_line(tree)] + \
                            [(n, "调完把手又把那句收口结果按字符数切了一刀")
                             for n in _gate_slice_of_handle(tree)] + \
                            _slice_of_exception_str(tree) + _slice_of_reason_ledger(tree):
            raw = text.splitlines()[lineno - 1].strip()
            # 豁免只对 30a-4 开（那一判是唯一会碰到「切的是列表元素/非原因字段」的），
            # 且必须连那一行原文一起对上；其余三种形状一刀不豁免。
            if kind.startswith("读一本") and (rel, raw) in REASON_SLICE_EXEMPT:
                used_exempt.add((rel, raw))
                continue
            line = raw[:120]
            problems.append(
                f"{_rel_label(path)} 第 {lineno} 行："
                f"{kind}：{line}\n"
                f"   → 半截词比不说更误导（实机读出过 `timed out. (con）`）。三种合格写法：已定性的"
                f"原因走 `short_reason(原因, 上限)`；异常文本走 `first_line(异常, 上限)`；接不到"
                f"把手的独立包/同名拷贝就整行带出（把 `[:N]` 删掉，别换一种切法）")

    # 30b 把手在位 + 两份实现同字节
    if handle_src is None:
        handle_path = ROOT / REASON_HANDLE
        if not handle_path.is_file():
            problems.append(f"真源不在位：{REASON_HANDLE} 读不到——这一族又退回各写各的截断了")
            return problems
        handle_src = handle_path.read_text(encoding="utf-8", errors="replace")
    if mirrors is None:
        mirrors = {}
        for rel in REASON_MIRRORS:
            mirror = ROOT / rel
            if not mirror.is_file():
                problems.append(f"内联那份把手的 {rel} 读不到：两份实现无从对拍")
                continue
            mirrors[rel] = mirror.read_text(encoding="utf-8", errors="replace")
    handle_block = _extract_reason_block(handle_src)
    if handle_block.startswith("（缺标记"):
        problems.append(f"{REASON_HANDLE} 里取不出把手那一段（边界 {list(REASON_BLOCK)}）："
                        "判据的取段边界与代码分叉了，改函数名或注释要连 REASON_BLOCK 一起改")
    ns_handle: dict = {"re": re}
    try:
        exec(compile(handle_src, REASON_HANDLE, "exec"), ns_handle)
    except Exception as e:
        problems.append(f"{REASON_HANDLE} 加载失败（{type(e).__name__}: {e}）："
                        "30c 的性质判据没有对照面，这一条等于没跑")
    # 30c 把手的性质：任何一句原因、任何一档上限，收口后都不许留半截词／丢前缀／孤括号
    handles = [("真源 " + REASON_HANDLE, ns_handle)]
    for rel, msrc in mirrors.items():
        mblock = _extract_reason_block(msrc)
        if mblock != handle_block:
            where = ""
            if not mblock.startswith("（缺标记") and not handle_block.startswith("（缺标记"):
                for n, (x, y) in enumerate(zip(handle_block.splitlines(),
                                               mblock.splitlines())):
                    if x != y:
                        where = f"（第一段差异：{REASON_HANDLE} 第 {n + 1} 行 ↔ {rel} 第 {n + 1} 行）"
                        break
            problems.append(
                f"{rel} 里内联的那一段与 {REASON_HANDLE} **不是逐字节相同**{where}：\n"
                f"   → 这一族的纪律是「一句原因只有一种收口」。分叉之后，同一条网络故障在 1688 "
                f"那条路上念得出完整句子、其它站念半截，而这种差异只有换一种异常文本才会暴露")
        ns_mirror: dict = {"re": re}
        # 整段带常量与小函数一起 exec：只取函数体的话，`_is_break` 这类共用助手会当场 NameError
        try:
            exec(compile(mblock, rel, "exec"), ns_mirror)
            handles.append((rel, ns_mirror))
        except Exception as e:
            problems.append(f"{rel} 里那份内联把手加载失败（{type(e).__name__}: {e}）："
                            "它一旦被 import 就带着整条 1688 通道一起崩")

    for text in _reason_property_cases():
        for limit in (8, 16, 24, 40, 48, 60, 70, 80, 120, 200):
            outs = []
            for label, ns in handles:
                fn = ns.get("short_reason")
                if not callable(fn):
                    continue
                got = fn(text, limit)
                outs.append(got)
                # ① 不以半截词收尾：收口留下的那一截必须是原句的**整词前缀**——判据问的是
                #    「紧跟着下一个字是不是边界」，而不是「最后一个字是不是边界」（把手会把行尾
                #    那个分隔符 rstrip 掉，那时最后一个字当然是词字符，而词本身完好）。
                whole = re.sub(r"\s+", " ", str(text)).strip()
                if got.endswith("…"):
                    stem = got[:-1]
                    for op, cl in (("（", "）"), ("(", ")")):
                        added = max(0, stem.count(cl) - stem.count(op))   # 把手补上的右括号
                        stem = stem[:len(stem) - added] if added else stem
                    if not whole.startswith(stem):
                        problems.append(f"{label} 在 limit={limit} 处改写了原句（收口只会少留，"
                                        f"不该换字）：{got!r}")
                    elif 0 < len(stem) < len(whole) and not _gate_cut_is_boundary(whole, len(stem)):
                        problems.append(f"{label} 在 limit={limit} 处切在词中间："
                                        f"{got[-24:]!r}｜下一字={whole[len(stem)]!r}")
                # ② 分过型的句子：`【类别】…｜` 前缀必须整段还在（处方就写在句首）
                if text.startswith("【") and "｜" in text:
                    head = text[:text.index("｜") + 1]
                    if not got.startswith(head):
                        problems.append(f"{label} 在 limit={limit} 处把前缀 {head!r} 切掉了：{got!r}")
                # ③ 括号必成对：日志里读到一个孤零零的 `(` 会让人以为是格式而不是内容
                for op, cl in (("（", "）"), ("(", ")")):
                    if got.count(op) != got.count(cl):
                        problems.append(f"{label} 在 limit={limit} 处留下不成对的 {op}：{got!r}")
            if len(set(outs)) > 1:
                problems.append(f"两份把手对同一句给出不同收口（limit={limit}）："
                                f"{[(l, o[:40]) for l, o in zip([h[0] for h in handles], outs)]}")

    if full_scan and judged == 0:
        problems.append(f"第 30 条一份都没判到（视野里 {len(files)} 份全解析不过）："
                        "这一条门等于没跑，不许在空集合上报绿")
    # 视野里额外登记的那几份渲染层，读不到就是少一份判据（少了不会有人注意到，所以要说）
    if full_scan:
        for rel in REASON_RENDER_EXTRA:
            for p, text in files.items():
                if _rel_label(p) == rel and not text.strip():
                    problems.append(f"视野里登记的渲染层 {_rel_label(p)} 读不到内容：第 30 条从此判不"
                                    "到实机报告那一行（上一轮那行半截词就念在这里）")
        # 豁免表不能只在「命中时」被读：一行的写法被改掉、那条缺陷换了衣服从门里走出去，
        # 靠这里响一句（否则豁免表会逐年长成一个没人敢删的抽屉）
        for key, why in sorted(REASON_SLICE_EXEMPT.items()):
            if key not in used_exempt:
                problems.append(f"豁免表里这一条已经不在原位：{key[0]} 里找不到整行等于 "
                                f"{key[1]!r} 的那一行（当时写的理由：{why}）——那一行要么换了"
                                "写法（得重新解释一次为什么不算切原因），要么已经修成走把手"
                                "（那这条豁免就该从表里删掉）")
    return problems


# ── 第 31 条：从浏览器标签交出去的那一份正文，必须先证明「这一页就是我要的那一页」 ──
# 由来（2026-11-16 第九轮实机马可波罗）：借标签跑完 JS 质询壳之后，旧实现只问「那页还是不是壳」，
# 壳一退掉就把 `_html()` 当成**这一家**的正文交出去。可那枚标签是各家用来的，站方在挑战之后常把
# 地址落到首页或上一家的商铺——那种页里有公司名、有电话，**只是不是这一家**。字段填到别人头上比
# 采不到更难被发现：Excel 里每一行都看起来是满的，电话打过去才知道是别家。
# 随后的同类查全证明这不是马可波罗一处的事，同一个坑在四个站各长了一遍、各防了一半：
#   · maimaib2b `_fetch_via_tab`：只在「保底重载回哪一页」防了，取回正文那一步没防；
#   · qihuo11467 `_fetch_via_tab`：只看主机名，同主机下首页与某一家的深链一样算「本站」；
#   · mic `_fetch_contact_page`：只拒 errordocs/404，被 302 领到别人的 showroom 照样解析；
#   · gys `_fetch_raw`：`_goto` 只断域名不断路径；
#   · mic `_fallback_en`：请求的主机名是**猜出来的**子域，落点换了主机就没人认得它是别家。
# 靠人记是记不住的——第五个站一定会漏，所以这一条上构建前硬门。判据是**数据流**而不是关键字
# （关键字版会把 `_goto`、能力探针、翻页循环三类合法写法一起误伤，误伤一次的判据下一次就被人
# 当噪音划掉）：先找出被 `self._html()` 赋过名的变量，再看这个函数的出口有没有把「正文本身」
# 交出去——`return` 那个变量／裸 `self._html()`，或把它当参数喂给 `_parse*`/`parse_*`/`_extract*`。
TAB_ID_SOURCE = "b2bcollect_build/cdp_base.py"
# 三种被认可的「问过身份」的写法：两道门都在基类（`_tab_body_for` 内含 `_tab_page_is`），
# 以及 `_goto`——它自己导航并回读 location 断言域名，是浏览器原生那条路的身份步。
TAB_ID_GATES = ("_tab_body_for", "_tab_page_is", "_goto")
# 身份门真源里必须**只有一个定义**的三个把手（各站再抄一份＝五处五种口径，等于没有门）
TAB_ID_HANDLES = ("_page_key", "_tab_page_is", "_tab_body_for")


def _tab_html_call(node) -> bool:
    return any(isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
               and c.func.attr == "_html" for c in ast.walk(node))


def _tab_body_out(value, tainted) -> bool:
    """这个 `return` 交出的是**正文本身**吗？裸变量或裸 `self._html()` 才算。
    包了一层判断（`return bool(self._tab_real_page(self._html()))`）交出去的是真/假，
    不是某一家的正文——那种能力探针不该被这条判据点名。
    元组/列表逐个元素看：`return rendered, "browser"` 是这一族最常见的形状（正文 + 来源通道），
    只看整体形状的判据会把每一站的浏览器腿全部漏掉（本轮自查抓到的第三个盲区）。"""
    if isinstance(value, (ast.Tuple, ast.List)):
        return any(_tab_body_out(e, tainted) for e in value.elts)
    if isinstance(value, ast.Name):
        return value.id in tainted
    return (isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)
            and value.func.attr == "_html")


def _ast_safe_parse(text):
    """给 31b 用：坏语法的那一份已经由 31a 单独报过了，这里不能再抛异常把整条门带走。"""
    try:
        return ast.parse(text)
    except SyntaxError:
        return ast.parse("")


def _tab_tainted_names(fn) -> set:
    """这个函数里有哪些变量**装着浏览器标签正文**（含传播）：
      ① 直接被 `self._html()` 赋值；
      ② 被「右边式子里出现过已污染的名字」的那个赋值语句赋值——`body, why = self._tab_body_for(url,
         body=html)`、`body, why = html, ""`、`x = html[:200]` 都是这一形。
      不算②的话，把那道门换成 `body = html` 就既去掉了门、又不被清成出口（自查时先发现「判据
      只认直接从 `_html()` 取值」这一形，等于告诉后来人「想绕过我就多包一层」；再把门摘成
      `body, why = html, ""` 时出口整条消失、只剩一句过期登记——右边是元组/切片的普通赋值
      同样是传播，判据不能只认 Call）。
    迭代到不动点：②可以链式传播（`a = _html()` → `b = f(a)` → `c = g(b)`）。
    """
    names = set()
    for _ in range(4):
        before = set(names)
        for n in ast.walk(fn):
            if not isinstance(n, ast.Assign):
                continue
            # ① 直接：`html = self._html()`、`html = (self._html() or "")[:N]` 这类混合表达式
            # ② 间接：右边任何位置出现已污染的名字（Call 的参数、元组元素、切片都算）
            hit = _tab_html_call(n.value) or any(
                isinstance(x, ast.Name) and x.id in names for x in ast.walk(n.value))
            if not hit:
                continue
            for t in n.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
                elif isinstance(t, (ast.Tuple, ast.List)):
                    names |= {e.id for e in t.elts if isinstance(e, ast.Name)}
        if names == before:
            break
    return names


def _tab_body_exits(tree) -> list:
    """列出这个模块里所有「把浏览器标签正文交出去」的函数：`[(名字, 行号, 出口方式, 用过的门)]`。

    这里**不做任何豁免**——豁免是登记表的事（见 `TAB_BODY_EXITS`）。判据只负责把出口清出来，
    于是新增一条通路的人会撞上一句「不在案」，而不是让判据替他猜「这一处大概不需要门」。
    前一版在这里直接豁免 `_goto(`，自查时被发现两个盲区：① 深链的身份比域名细，`_goto` 那道
    域名门**不够**（gys/mic 正是这样漏掉的）；② 只看关键字在不在，把门改成 `if False:` 死代码
    仍然算有门。所以改成「列全部 + 逐项验声明」。
    """
    out = []
    for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
        if not _tab_html_call(fn):
            continue
        body_src = ast.unparse(fn)
        tainted = _tab_tainted_names(fn)
        if not tainted and not any(isinstance(n, ast.Return) and _tab_html_call(n)
                                   for n in ast.walk(fn)):
            continue
        how = []
        for n in ast.walk(fn):
            if isinstance(n, ast.Return) and n.value is not None and _tab_body_out(n.value, tainted):
                how.append("return")
            if (isinstance(n, ast.Call)
                    and any(k in (getattr(n.func, "attr", "") or getattr(n.func, "id", ""))
                            for k in ("_parse", "parse_", "_extract"))
                    and any(isinstance(x, ast.Name) and x.id in tainted
                            for a in n.args for x in ast.walk(a))):
                how.append("parser")
        if how:
            gates = [k for k in TAB_ID_GATES if k + "(" in body_src]
            out.append((fn.name, fn.lineno, "+".join(sorted(set(how))), gates))
    return out


# 在案的出口登记表：每一项写明「这一路的身份门是哪一步」。**多加一项必须同时写下它靠什么认页**，
# 这就是这一条门的用处——把「这里要不要门」从每次评审现场决定，变成改门脚本时必须写下的在案声明。
#   _tab_body_for  域名＋路径，再问体积/是不是壳（把正文交出去的标准那道门）；
#   _tab_page_is   只问地址（正文另有各站判据时用这一档）；
#   _goto          域名级回读断言——只对**列表/搜索页**够用：那一页的身份就是这次搜索请求的地址，
#                  落点不符另有基类观测账（`_note_landing_mismatch`），等证据决定是否收紧。
#                  深链/详情页**不许**登记这一档：同主机下首页与某一家的深链都算「本站」，防不住。
TAB_BODY_EXITS = {
    "b2bcollect_build/cdp_base.py": {
        # 门自己也是「把标签正文交出去」的一处出口：它交出去的那一份靠 `_tab_page_is` 认页。
        # 把它登记进来而不是豁免掉，是为了让「把 `_tab_page_is` 那一行删掉」这件事在 31a 就响，
        # 而不是等到某一站撞上串页才发现门已经空转了三轮。
        "_tab_body_for": "_tab_page_is",
        "_iter_keyword": "_goto",
    },
    "b2bcollect_build/gys_crawler.py": {"_fetch_raw": "_tab_body_for", "_iter_keyword": "_goto"},
    # 麦麦B2B：它的注释里早就写着「别把别的页当这一页」，但只在保底重载那一步防了；
    # 现在取正文这一步也过门，所以它是这一族里第一个「声明与实现都在案」的样本。
    "b2bcollect_build/maimaib2b_crawler.py": {"_fetch_via_tab": "_tab_body_for"},
    "b2bcollect_build/mic_crawler.py": {"_fetch_contact_page": "_tab_page_is",
                                        "_fallback_en": "_tab_page_is"},
    # 顺企网：深链那一路过 `_tab_body_for`；列表页那一路**故意**不过——`search.php` 靠重定向
    # 拿 SEO 基址，调用方要的就是最终地址（见该函数注释）。两条都在同一个函数里，登记按函数计，
    # 所以登记的是那道更严的门；列表页那条的豁免理由写在源码里，不靠判据猜。
    "b2bcollect_build/qihuo11467_crawler.py": {"_fetch_via_tab": "_tab_body_for"},
}
# 「这一家」的把手：签名里出现这些参数名，说明这个函数取的是**单家**的页而不是列表页，
# 域名级门就不够（判据与理由见 _tab_is_per_company）
TAB_PER_COMPANY_ARGS = frozenset({"rec", "record", "user", "company", "supplier", "deep"})


def _text_of(files, rel: str):
    """按 `_rel_label` 的口径在视野里找一份源码（表里的键是相对路径，视野里的键是 Path）。"""
    for path, text in files.items():
        if _rel_label(path) == rel:
            return text
    return None


# 31c 反向对照：九份夹具都取自真实源码形状，判据必须只对「表与源码对不上」的那些响。
TAB_ID_CASES = (
    # (说明, 源码, 在案登记 {函数名: 门}, 期望是否不合格)
    ("没登记：壳一退掉就把 _html() 当这一家的正文交出去（马可波罗借标签跑壳修前原样）",
     'class A:\n    def _solve(self, url):\n        if not self._looks_like_challenge_shell'
     '(self._html()):\n            return self._html()\n        return ""\n', {}, True),
    ("没登记：把正文交给解析器（顺企网/中国制造网修前原样）",
     'class A:\n    def _from_tab(self, url, kw):\n        html = self._html()\n'
     '        self._parse_contact(html, rec)\n        return self.parse_list(html, kw)\n',
     {}, True),
    ("登记了元组出口那一形（`return rendered, "
     "\"browser\"` 也是把正文交出去，只看整体形状的判据会把每一站的浏览器腿全漏掉）",
     'class A:\n    def _fetch_raw(self, url, deep=False):\n        rendered = self._html()\n'
     '        return rendered, "browser"\n', {"_fetch_raw": "_tab_body_for"}, True),
    ("登记的门被删掉了：表说这一步过 _tab_body_for，源码里已经没有了（改代码忘改表不算，"
     "删了门忘改表更不算）",
     'class A:\n    def _fetch_via_tab(self, url):\n        body = self._html()\n'
     '        return body\n', {"_fetch_via_tab": "_tab_body_for"}, True),
    ("摘门时把正文经元组赋值转手（`body, why = html, \"\"`）：传播只认 Call 的判据会把这条"
     "出口整条漏掉，表里那一行反过来变成『过期登记』把删门这件事糊过去",
     'class A:\n    def _fetch_via_tab(self, url, rec):\n        html = self._html()\n'
     '        body, why = html, ""\n        return body\n', {"_fetch_via_tab": "_tab_body_for"},
     True),
    ("摘门时把正文切一刀再交（`part = html[:200]`）：同上一形，转手不等于换了来源",
     'class A:\n    def _fetch_via_tab(self, url, rec):\n        html = self._html()\n'
     '        part = html[:200]\n        self._parse_contact(part, rec)\n',
     {"_fetch_via_tab": "_tab_body_for"}, True),
    ("深链只登记域名级 _goto：同主机下首页与某一家的深链都算「本站」，这道门防不住串页",
     'class A:\n    def _fetch_contact_page(self, user, rec):\n        self._goto(url)\n'
     '        html = self._html()\n        self._parse_contact(html, rec)\n',
     {"_fetch_contact_page": "_goto"}, True),
    ("在案且门在位：先过 _tab_body_for 再交正文",
     'class A:\n    def _fetch_via_tab(self, url):\n        body, why = self._tab_body_for(url)\n'
     '        return body\n', {"_fetch_via_tab": "_tab_body_for"}, False),
    ("在案且门在位：深链用 _tab_page_is 只问地址（正文另有各站判据）",
     'class A:\n    def _fallback_en(self, user, rec):\n        ok, why = self._tab_page_is'
     '(alt, token=user)\n        html = self._html()\n        self._parse_contact(html, rec)\n',
     {"_fallback_en": "_tab_page_is"}, False),
    ("列表页登记 _goto 是合法的：那一页的身份就是这次搜索的地址",
     'class A:\n    def _iter_keyword(self, kw):\n        self._goto(url, expect_domain="x.com")\n'
     '        html = self._html()\n        return self.parse_list(html, kw)\n',
     {"_iter_keyword": "_goto"}, False),
    ("能力探针不进出口：交出去的是判断不是正文，不该出现在清单里（误伤一次，判据下一次就被人"
     "当噪音划掉）",
     'class A:\n    def _tab_page_ok(self, url):\n        return bool(self._tab_real_page'
     '(self._html() or ""))\n', {}, False),
)

# 31d 门本身的**行为**夹具：拿真源里那两段重算，地址语义由这里独立列一遍（不读实现的常量，
# 否则就是拿实现验实现）。`same_host` 那一档是 mic 猜子域那条路专用的，必须正反都在。
TAB_ID_BEHAVIOUR = (
    # (当前地址, 要的那一页, token, same_host, 期望, 期望理由片段)
    ("http://a.example.com/contact_us.html", "http://a.example.com/contact_us.html", "", True,
     True, ""),
    ("http://a.example.com/contact_us.html?__jsl_clearance=s%3Dx", "http://a.example.com/"
     "contact_us.html", "", True, True, ""),           # 站方补的质询令牌不算换页
    ("http://a.example.com/contact_us.html#tel", "http://a.example.com/contact_us.html", "",
     True, True, ""),                                    # 锚点不算换页
    ("http://a.example.com/shop", "http://a.example.com/shop/", "", True, True, ""),
    ("http://a.example.com/index.html", "http://a.example.com/contact_us.html", "", True,
     False, "标签落的不是这一页"),                        # 同主机别的路径＝串页
    ("http://b.example.com/contact_us.html", "http://a.example.com/contact_us.html", "", True,
     False, "标签不在本站"),                              # 马可波罗：每家一个子域
    ("", "http://a.example.com/contact_us.html", "", True, False, "标签不在本站"),
    ("https://www.example.com/shop/ab123-contact-us.html",
     "https://www.example.com/shop/ab123-contact.html", "ab123", True, True, ""),
    ("https://www.example.com/shop/zz999-contact.html",
     "https://www.example.com/shop/ab123-contact.html", "ab123", True, False, "凭据"),
    ("https://www.example.com/showroom/ab123/", "https://ab123.example.com/", "ab123", False,
     True, ""),                                          # 猜的子域被领到统一路径：凭据还在
    ("https://www.example.com/showroom/zz999/", "https://ab123.example.com/", "ab123", False,
     False, "凭据"),                                     # 换主机≠免检
    ("https://www.example.com/showroom/ab123/", "https://ab123.example.com/", "", False,
     False, "标签不在本站"),                              # 没有凭据就不许白放跨主机
    # 31d 的反向对照：普通串页**不许**被说成「这一跳没加载出来」。那一档的说法是「浏览器根本没
    # 发出去」，把两种处方（换页 vs 改启动参数）混成一句话，比原来那句更误导。
    ("https://a.example.com/shop/other.html", "https://a.example.com/shop/mine.html", "", True,
     False, "标签落的不是这一页"),
)

# 31e 「这一跳根本没加载出来」那一支的行为夹具：(当前地址, 要的那一页, Chrome 原话,
# 必须出现, 绝不允许出现)。由来（2026-11-14 实机马可波罗借标签那条腿 0/3）：`Page.navigate`
# 被拦时**不抛异常**，页面落在 `chrome-error://chromewebdata/`，旧写法只看到「地址不在本站」，
# 于是「浏览器没把这一跳发出去」在下游一律被说成「站点在拦／这条腿帮不上忙」——方向整个说反，
# 照着那句话去查的人会去改站点配置，而该改的是共享 Chrome 的启动参数。
# 每一档的 Chrome 原话都取自本机差分实测（同机两个 Chrome，只差 `--disable-features`）：
# A 组连 example.com 的明文 http 都被 BLOCKED_BY_CLIENT，B 组全部正常。
TAB_ID_NAVFAIL = (
    ("chrome-error://chromewebdata/", "http://a.example.com/contact_us.html",
     "net::ERR_BLOCKED_BY_CLIENT", "浏览器拦下这一跳", "标签不在本站"),
    ("chrome-error://chromewebdata/", "http://a.example.com/contact_us.html",
     "net::ERR_CERT_COMMON_NAME_INVALID", "TLS握手失败", "标签不在本站"),
    ("chrome-error://chromewebdata/", "http://a.example.com/contact_us.html",
     "net::ERR_HTTP_RESPONSE_CODE_FAILURE", "站点回了错误状态码", "浏览器拦下这一跳"),
    ("chrome-error://chromewebdata/", "http://a.example.com/contact_us.html",
     "net::ERR_TIMED_OUT", "连不上·无应答", "浏览器拦下这一跳"),
    # 拿不到错误码时**只许陈述事实**：一句「页里没写着错误码」，不许挑一个方向下结论
    ("chrome-error://chromewebdata/", "http://a.example.com/contact_us.html",
     "", "页里没写着错误码", "【"),
    # 认不出的错误码同理：落到【未分型】就照实说未分型，绝不顺手安一个已知方向
    ("chrome-error://chromewebdata/", "http://a.example.com/contact_us.html",
     "net::ERR_SOMETHING_NEW", "未分型", "浏览器拦下这一跳"),
)

# 31f 「导航证据」的结构判据：这一族的每一句话术都必须有**它自己拿得到的证据**撑着。
# (函数名, 必须出现在源码里的东西, 少了会说什么谎)
TAB_NAV_RULES = (
    ("_cdp_navigate", ("Page.navigate", "errorText", "_last_nav_error"),
     "把 `Page.navigate` 的响应丢掉，「这一跳被浏览器拦下」就再也没有留痕，下游只能猜"),
    ("_tab_load_failed", ("CHROME_ERROR_URL_MARKS", "tab_load_failed_line"),
     "错误页的判据与话术各写一份，将来加一档错误页就会有一侧先聋"),
    ("_solve_js_shell_via_tab", ("_tab_load_failed", "request_error_kind"),
     "等挑战那段循环不先问「这一跳到底加载出来没有」，就会把没发出去的导航说成挑战跑完了"),
)
# 31f-2 结论句的口径：`_solve_js_shell_via_tab` 里所有 `self._log(...)` 都不许出现「跑完了」。
# 「挑战跑完」这件事**没有任何证据**能证明（能证明的只有：这一页已经不是壳／正文过了身份门／
# 这一跳根本没加载出来）。写「跑完了」就是把推测当观察念给用户听。
TAB_NAV_LIE_TOKEN = "跑完了"
# 名单：哪些函数的**结论句**在这条口径的管辖之内（都是「借标签这条腿」向用户交代结果的地方）。
# 新增一条同类通路就要把它加进来——加进来的代价是那句话必须改成只陈述观察。
TAB_NAV_LIE_FNS = ("_solve_js_shell_via_tab", "_deep_shell_retry")

# 31f 的正反夹具：坏形状必须响、现网写法不许响（判据不能被反证就等于还没修）。
# 第四格是 `strict_names`：只有真源那一遍用严格档（理由见 _tab_nav_evidence_problems 的文档）。
TAB_NAV_CASES = (
    ("旧写法：导航响应整个丢掉，errorText 没人接",
     'class A:\n    def _cdp_navigate(self, url):\n'
     '        self._send("Page.navigate", {"url": url})\n        return True\n'
     '    def _tab_load_failed(self):\n        return tab_load_failed_line('
     'self._current_url(), self._last_nav_error, CHROME_ERROR_URL_MARKS)\n'
     '    def _solve_js_shell_via_tab(self, url):\n        if self._tab_load_failed():\n'
     '            return False\n        return bool(request_error_kind(""))\n', True),
    ("错误页判据被就地重抄一份字面量（把手先聋那一档）",
     'class A:\n    def _cdp_navigate(self, url):\n'
     '        r = self._send("Page.navigate", {"url": url})\n'
     '        self._last_nav_error = r.get("errorText")\n        return True\n'
     '    def _tab_load_failed(self):\n        if "chrome-error://" not in self._current_url():\n'
     '            return ""\n        return ""\n'
     '    def _solve_js_shell_via_tab(self, url):\n        if self._tab_load_failed():\n'
     '            return False\n        return bool(request_error_kind(""))\n', True),
    ("等挑战的循环不问「加载出来没有」，直接宣布这条腿的结果",
     'class A:\n    def _cdp_navigate(self, url):\n'
     '        r = self._send("Page.navigate", {"url": url})\n'
     '        self._last_nav_error = r.get("errorText")\n        return True\n'
     '    def _tab_load_failed(self):\n        return tab_load_failed_line('
     'self._current_url(), self._last_nav_error, CHROME_ERROR_URL_MARKS)\n'
     '    def _solve_js_shell_via_tab(self, url):\n        return self._harvest() > 0\n', True),
    ("结论句把推测当观察：`_log` 里写下「挑战跑完了」",
     'class A:\n    def _cdp_navigate(self, url):\n'
     '        r = self._send("Page.navigate", {"url": url})\n'
     '        self._last_nav_error = r.get("errorText")\n        return True\n'
     '    def _tab_load_failed(self):\n        return tab_load_failed_line('
     'self._current_url(), self._last_nav_error, CHROME_ERROR_URL_MARKS)\n'
     '    def _solve_js_shell_via_tab(self, url):\n'
     '        if self._tab_load_failed():\n            return False\n'
     '        self._log("   ⚠ 挑战跑完了，这条腿还是帮不上")\n'
     '        return bool(request_error_kind(""))\n', True),
    ("合格形状：三处证据都在、结论句只陈述观察",
     'class A:\n    def _cdp_navigate(self, url):\n'
     '        r = self._send("Page.navigate", {"url": url})\n'
     '        self._last_nav_error = r.get("errorText")\n        return True\n'
     '    def _tab_load_failed(self):\n        return tab_load_failed_line('
     'self._current_url(), self._last_nav_error, CHROME_ERROR_URL_MARKS)\n'
     '    def _solve_js_shell_via_tab(self, url):\n'
     '        if self._tab_load_failed():\n            return False\n'
     '        self._log("   ⚠ 标签里那页已经不是质询壳了，这条腿还是帮不上")\n'
     '        return bool(request_error_kind(""))\n', False),
)


# ── 31g 「这一跳被浏览器自己拦掉」的**环境侧**证据 ────────────────────────────────
# 31e/31f 管的是「这句话有没有说谎」，管不到「这句谎还有没有必要说」：真正让马可波罗那条借
# 标签的腿三次全废的是共享 Chrome 少了 `--disable-features=HttpsUpgrades,…`。归因写得再准，
# 启动参数丢了，用户看到的仍然是「这个站采不到」。所以这一节把**环境**也钉住：
#   31g-1 feature 清单必须三档齐全——只关 `HttpsUpgrades` 时 `HttpsFirstModeV2`／
#         `HttpsFirstBalancedMode` 那两条路径仍会升级，症状与「完全没关」一模一样，却更难查
#         （那枚开关看起来是带上了的）；
#   31g-2 启动参数必须由**那份常量**拼出来，不许就地重抄字面量：重抄的那份将来加一档 feature
#         只补到一侧，而两侧分叉恰好就是 31g-1 要防的那件事；
#   31g-3 那份常量必须真的进启动参数列表（赋给一个没人读的变量＝这一条已经作废）；
#   31g-4 复用「先前就开着的、没带开关的」旧实例时必须提示一次：不提示，用户看到的就是
#         「站点像是不给过」，而该做的只是重开一次本工具；
#   31g-5 内联副本（1688／riskbird 各自是独立包，import 不到真源）与真源**逐字节相同**，
#         并且各自真的用在自己的启动列表里；
#   31g-6 被明确否决的那枚 `--ignore-certificate-errors` 不许以**参数**的形态回到交付链：
#         它把「不强制升级」做成「不校验证书」，等于拿 1688／riskbird 的登录态换一次取页。
#         只看 AST 里那些会被当参数用的字符串，所以注释里讲「绝不采用」不受这一条管辖——
#         否则判据会逼人删掉那条最该留下的说明；
#   31g-7 覆盖面：凡在启动参数列表／Selenium `add_argument` 里配过 Chrome 开关的文件，要么带上
#         这份开关，要么在豁免表里写下「这个浏览器为什么不必带」；表里每一项都必须仍对应一处
#         真实启动点（过期豁免顶着名次，下一条同类漏网就会被当成合法的）。
TAB_ENV_SOURCE = "b2bcollect_build/shared_chrome.py"
TAB_ENV_MIRRORS = ("ali1688_build/ali1688_crawler.py", "riskbird_build/dual_session.py")
TAB_ENV_FEATURE_CONST = "NO_HTTPS_UPGRADE_FEATURES"
TAB_ENV_FLAG_CONST = "CHROME_NO_UPGRADE_FLAGS"
TAB_ENV_FEATURES = ("HttpsUpgrades", "HttpsFirstModeV2", "HttpsFirstBalancedMode")
TAB_ENV_REUSE_FN = "ensure_shared_chrome"
TAB_ENV_PROBE_FN = "missing_no_upgrade_flag"
TAB_ENV_FORBIDDEN = "--ignore-certificate-errors"
TAB_ENV_BROWSER_MARKS = ("--remote-debugging-port", "--user-data-dir")
# 进程管理类的 argv 首元素：这些列表里出现浏览器开关是在**认领旧实例**或**关旧实例**，
# 不是在配置启动参数（见 _tab_env_launch_lists 的文档）。
_TAB_ENV_PROC_TOOLS = ("pkill", "kill", "ps", "pgrep", "taskkill", "powershell", "wmic")
# 豁免表：这些启动点确实不带这份开关。理由写在这里，而不是写在沉默里。
TAB_ENV_EXEMPT = {
    "riskbird_build/qizhidao_crawler.py":
        "Selenium 临时 profile 的兜底会话，只逛企知道（站方强制 https），不按采集数据里的任意"
        "链接开页：带上它的收益是 0，代价是第四份内联副本",
    "riskbird_build/riskbird_crawler_v2.py":
        "同上：只逛 riskbird 本站，https",
    "riskbird_build/aiqicha_crawler_v2.py":
        "同上：只逛爱企查，https",
    "riskbird_build/launch_chrome_manual.py":
        "一次性手工登录助手，不在采集链路里（要登的平台也是 https）",
}


def _tab_env_assign(text: str, name: str):
    """模块级 `name = …` 那一行的**原文**（逐字节对拍用）；没有或解析不过则 None。"""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    for st in tree.body:
        if isinstance(st, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in st.targets):
            lines = text.splitlines()
            return "\n".join(lines[st.lineno - 1:st.end_lineno])
    return None


def _tab_env_assign_node(tree, name: str):
    for st in getattr(tree, "body", []):
        if isinstance(st, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in st.targets):
            return st
    return None


def _tab_env_arg_strings(tree) -> list:
    """会被**当成参数用**的字符串常量：列表/元组成员、调用实参（含 f-string 的固定前缀）。"""
    out = []
    for sub in ast.walk(tree):
        if isinstance(sub, (ast.List, ast.Tuple)):
            cand = [e.value if isinstance(e, ast.Starred) else e for e in sub.elts]
        elif isinstance(sub, ast.Call):
            cand = list(sub.args) + [k.value for k in sub.keywords]
        else:
            continue
        for c in cand:
            if isinstance(c, ast.Constant) and isinstance(c.value, str):
                out.append(c.value)
            elif isinstance(c, ast.JoinedStr):
                out.append("".join(v.value for v in c.values
                                   if isinstance(v, ast.Constant)
                                   and isinstance(v.value, str)))
    return out


def _tab_env_launch_lists(tree) -> list:
    """这个文件里所有「给 Chrome 配启动参数」的地方。

    两种形状都算：命令行列表/元组（`cmd = [chrome, "--user-data-dir=…", …]`）与 Selenium 的
    `opts.add_argument("--user-data-dir=…")`。只认第一种的话，Selenium 那条路径对整个覆盖面
    是隐形的——「同一枚开关只在一条路径上生效」这句正是这一族事故的原话，不能让它靠运气。

    刻意**排掉**进程管理类命令（`pkill -f user-data-dir=…`、`ps`、`taskkill`）：那些列表里
    出现 `user-data-dir` 只是在**认领已经在跑的实例**，不是配置启动参数。不排掉的话，这一条
    会把「按目录名匹配旧进程」那些正当写法一路报成缺陷，而报多了就没人再理会这一条。
    """
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):        # Selenium 那一路
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr == "add_argument":
                vals = []
                for a in node.args:
                    if isinstance(a, ast.Constant) and isinstance(a.value, str):
                        vals.append(a.value)
                    elif isinstance(a, ast.JoinedStr):
                        vals.append("".join(v.value for v in a.values
                                            if isinstance(v, ast.Constant)
                                            and isinstance(v.value, str)))
                if any(str(v).startswith(TAB_ENV_BROWSER_MARKS) for v in vals):
                    hits.append(node)
            continue
        if not isinstance(node, (ast.List, ast.Tuple)):
            continue
        first = node.elts[0] if node.elts else None
        head = (first.value if isinstance(first, ast.Constant) else "") if first is not None else ""
        if isinstance(head, str) and (head in _TAB_ENV_PROC_TOOLS
                                      or (head.startswith("/") and head.endswith(("/ps", "/kill",
                                                                                   "/pkill")))):
            continue
        for elt in node.elts:
            item = elt.value if isinstance(elt, ast.Starred) else elt
            if isinstance(item, ast.JoinedStr):
                vals = [v.value for v in item.values
                        if isinstance(v, ast.Constant) and isinstance(v.value, str)]
            elif isinstance(item, ast.Constant) and isinstance(item.value, str):
                vals = [item.value]
            else:
                vals = []
            if any(str(v).startswith(TAB_ENV_BROWSER_MARKS) for v in vals):
                hits.append(node)
                break
    return hits


def _tab_env_node_has_flag(node) -> bool:
    """这一处启动参数里带了那份开关吗（`*NAME,` 与 `NAME,` 两种成员写法都认）。"""
    if not isinstance(node, (ast.List, ast.Tuple)):
        return False
    for elt in node.elts:
        tgt = elt.value if isinstance(elt, ast.Starred) else elt
        if isinstance(tgt, ast.Name) and tgt.id == TAB_ENV_FLAG_CONST:
            return True
    return False


def _tab_calls_name(node, name: str) -> bool:
    return any(isinstance(x, ast.Call) and isinstance(x.func, ast.Name)
               and x.func.id == name for x in ast.walk(node))


def _tab_env_gate_problems(files: dict) -> list:
    """31g 的整轮：真源四步 + 副本逐字节 + 被否决的开关 + 覆盖面与过期豁免。"""
    problems = []
    src = _text_of(files, TAB_ENV_SOURCE)
    if src is None:
        return [f"{TAB_ENV_SOURCE} 不在视野里：第 31 条这一节没有对照面，"
                "「环境开关带上了」这句话等于没跑"]
    src_tree = _ast_safe_parse(src)
    problems += _tab_chrome_env_problems(TAB_ENV_SOURCE, src, src_tree)

    # 31g-5 内联副本：两行赋值与真源逐字节相同，并且各自真的进自己的启动列表
    for rel in TAB_ENV_MIRRORS:
        text = _text_of(files, rel)
        if text is None:
            problems.append(f"{rel} 不在视野里：登记着的内联副本读不到，31g-5 这一条等于跳过")
            continue
        tree = _ast_safe_parse(text)
        for name in (TAB_ENV_FEATURE_CONST, TAB_ENV_FLAG_CONST):
            a, b = _tab_env_assign(src, name), _tab_env_assign(text, name)
            if a is None or b is None:
                continue                     # 缺哪一行由 _tab_chrome_env_problems 报，别报两遍
            if a != b:
                problems.append(
                    f"{rel}::{name} 与真源不再逐字节相同：{b[:90]!r} ≠ {a[:90]!r}\n"
                    "   → 这一族的事故本来就是「同一个环境开关只在一条路径上生效」。副本可以因为"
                    "打包而存在，但内容必须与真源同一份字节；只比返回值比不出「一侧少一档 feature」")
        problems += _tab_chrome_env_problems(rel, text, tree, mirrors=True)

    # 31g-6 被明确否决的那枚开关（只看会被当参数用的字符串；注释里讲来历不算）
    for path, text in sorted(files.items(), key=lambda kv: str(kv[0])):
        tree = _ast_safe_parse(text)
        if tree is None:
            continue
        for val in _tab_env_arg_strings(tree):
            if TAB_ENV_FORBIDDEN in val:
                problems.append(
                    f"{_rel_label(path)} 把 {TAB_ENV_FORBIDDEN} 当参数交出去了：这一枚把「不强制"
                    "升级 https」做成「不校验任何证书」，一台带着 1688／riskbird 登录态的浏览器"
                    "从此对所有站点失去中间人防护——是拿账号安全换一次取页，本轮已明确否决")

    # 31g-7 覆盖面：配过 Chrome 启动参数的地方要么带开关，要么在豁免表里写下理由
    seen = set()
    for path, text in sorted(files.items(), key=lambda kv: str(kv[0])):
        rel = _rel_label(path)
        tree = _ast_safe_parse(text)
        if tree is None:
            continue
        sites = _tab_env_launch_lists(tree)
        if not sites:
            continue
        seen.add(rel)
        if any(_tab_env_node_has_flag(n) for n in sites):
            continue
        if rel in TAB_ENV_EXEMPT:
            continue
        problems.append(
            f"{rel} 拉起/配置了 Chrome（{TAB_ENV_BROWSER_MARKS}）却没带 "
            f"*{TAB_ENV_FLAG_CONST}，也不在豁免表里：这条路径上的明文 http 会被浏览器自己拦掉，"
            "而日志会把它说成「站点不给过」。要么带上，要么在 TAB_ENV_EXEMPT 写清这个浏览器"
            "为什么不必带——写在表里的代价是必须写下一个理由，这正是这一条门的全部用处")
    for rel in TAB_ENV_EXEMPT:
        if rel not in seen and _text_of(files, rel) is not None:
            problems.append(f"豁免表里的 {rel} 现在已经找不到 Chrome 启动参数列表：这一行作废，"
                            "请删掉——留着它，下一条同类漏网会顶着它的名次被当成合法的")
    return problems


def _tab_is_log(call) -> bool:
    """这个调用是不是「往用户眼睛里写一句话」：`log(...)` 或 `self._log(...)`。"""
    f = call.func
    return ((isinstance(f, ast.Name) and f.id == "log")
            or (isinstance(f, ast.Attribute) and f.attr in ("log", "_log")))


def _tab_chrome_env_problems(rel: str, text: str, tree, mirrors=False) -> list:
    """31g：一枚环境开关的四步结构证据（清单齐／由常量拼／真进列表／复用旧实例会说话）。"""
    problems = []
    feat = _tab_env_assign_node(tree, TAB_ENV_FEATURE_CONST)
    flag = _tab_env_assign_node(tree, TAB_ENV_FLAG_CONST)
    if feat is None:
        problems.append(f"{rel} 没有模块级 {TAB_ENV_FEATURE_CONST}：这一族的启动参数再也没有"
                        "单一真源，31g-2 的「由常量拼出来」无从谈起")
        return problems
    if flag is None:
        problems.append(f"{rel} 没有模块级 {TAB_ENV_FLAG_CONST}：清单有了，却没人把它拼成启动"
                        "参数（下一步必然长成就地重抄字面量）")
        return problems
    # 31g-1 三档齐全：按解析出来的字符串自己重算，不读实现里的任何判据
    got = ({p.strip() for p in feat.value.value.split(",") if p.strip()}
           if isinstance(feat.value, ast.Constant) and isinstance(feat.value.value, str)
           else set())
    if not got:
        problems.append(f"{rel}::{TAB_ENV_FEATURE_CONST} 不再是字面字符串：31g-1 无法确认三档"
                        " feature 都在（算出来的值在各份内联副本之间更没法对拍）")
    elif missing := [f for f in TAB_ENV_FEATURES if f not in got]:
        problems.append(f"{rel}::{TAB_ENV_FEATURE_CONST} 少了 {missing}：Chrome 有多条把明文"
                        " http 升成 https 的路径，只关一档时症状与「完全没关」一模一样，"
                        "而那枚开关看起来是带上了的——比整枚缺失更难查")
    # 31g-2 由那份常量拼出来
    if TAB_ENV_FEATURE_CONST not in _mirror_load_names(flag):
        problems.append(f"{rel}::{TAB_ENV_FLAG_CONST} 没读 {TAB_ENV_FEATURE_CONST}：清单被重抄"
                        "了一份，将来加一档 feature 只会补到一侧（同一枚开关在两条路径上"
                        "行为不同）")
    # 31g-3 真进启动参数列表：**每一处**给 Chrome 配启动参数的地方都要带上
    sites = _tab_env_launch_lists(tree)
    if not sites:
        problems.append(f"{rel} 已经找不到任何 Chrome 启动参数列表：31g-3 没有对照面——常量还在，"
                        "拉起浏览器的那一处被改名／搬走／删掉了（那条路径再也不会带这份开关）")
    else:
        miss = sorted(n.lineno for n in sites if isinstance(n, (ast.List, ast.Tuple))
                      and not _tab_env_node_has_flag(n))
        if miss:
            problems.append(f"{rel} 第 {miss} 行的启动参数列表里没有 *{TAB_ENV_FLAG_CONST}："
                            "常量在、没人用它，浏览器仍按默认策略把明文 http 升级后拦掉——"
                            "日志里那句「浏览器拦下这一跳」会变回真的，但那是我们自己的环境在拦，"
                            "不是站点（一处列表带、另一处不带＝同一个工具两种环境，正是这一族事故）")
    if mirrors:
        return problems        # 副本没有「复用旧实例」这一支，问它等于问一段不存在的代码
    # 31g-4 复用旧实例时那句提示要接线，而且要真的落在提示分支里
    reuse = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                  and n.name == TAB_ENV_REUSE_FN), None)
    if reuse is None:
        problems.append(f"{rel} 没有 {TAB_ENV_REUSE_FN}：「先前就开着的 Chrome 没带这个开关」"
                        "这一种最常见的现场再也没有人提醒")
    elif not _tab_calls_name(reuse, TAB_ENV_PROBE_FN):
        problems.append(f"{rel}::{TAB_ENV_REUSE_FN} 没问 {TAB_ENV_PROBE_FN}()：复用一个没带开关"
                        "的旧实例时，用户看到的仍是「这个站采不到」，而该做的只是重开一次本工具")
    else:
        spoken = any(isinstance(n, ast.If)
                     and _tab_calls_name(n.test, TAB_ENV_PROBE_FN)
                     and any(isinstance(x, ast.Call) and _tab_is_log(x)
                             for st in list(n.body) + list(n.orelse) for x in ast.walk(st))
                     for n in ast.walk(reuse))
        if not spoken:
            problems.append(f"{rel}::{TAB_ENV_REUSE_FN} 问到了却没说：那句话要落在提示分支里"
                            "（`if …: log(…)`）——只调用一次是给自己记账，不给用户交代")
    return problems


# 31g-8 正反夹具：坏形状必须响、现网写法不许响（这一节审判的是**环境**，判据自己也得被反证）。
# `_TAB_ENV_GOOD` 就是现网 shared_chrome 的形状缩到最小：四步证据一步不缺。
_TAB_ENV_GOOD = (
    'import subprocess\n'
    'NO_HTTPS_UPGRADE_FEATURES = "HttpsUpgrades,HttpsFirstModeV2,HttpsFirstBalancedMode"\n'
    'CHROME_NO_UPGRADE_FLAGS = (f"--disable-features={NO_HTTPS_UPGRADE_FEATURES}",)\n'
    '_flag_warned = []\n'
    'def missing_no_upgrade_flag():\n    return False\n'
    'class A:\n'
    '    def _launch_shared_chrome(self, log, port, profile):\n'
    '        cmd = [self.chrome, f"--remote-debugging-port={port}",\n'
    '               f"--user-data-dir={profile}", *CHROME_NO_UPGRADE_FLAGS]\n'
    '        subprocess.Popen(cmd)\n'
    '    def ensure_shared_chrome(self, log):\n'
    '        if self.is_alive():\n'
    '            if missing_no_upgrade_flag() and not _flag_warned:\n'
    '                _flag_warned.append(1)\n'
    '                log("⚠ 共享浏览器是先前就开着的，没带这个开关")\n'
    '            return True\n'
    '        return self._launch_shared_chrome(log, 9255, self.profile)\n')


def _tab_env_kill(s: str, old: str, new: str) -> str:
    return s.replace(old, new, 1)


TAB_ENV_CASES = (
    # (说明, 源码, 期望出现的缺陷关键词；""＝合格形状，一条都不许响)
    ("合格形状：清单齐、由常量拼、真进列表、复用旧实例会说话", _TAB_ENV_GOOD, ""),
    ("三档 feature 只剩一档（另外两条升级路径还在）",
     _tab_env_kill(_TAB_ENV_GOOD, '"HttpsUpgrades,HttpsFirstModeV2,HttpsFirstBalancedMode"',
                   '"HttpsUpgrades"'), "少了 ['HttpsFirstModeV2', 'HttpsFirstBalancedMode']"),
    ("启动参数就地重抄字面量（将来加一档只补到一侧）",
     _tab_env_kill(_TAB_ENV_GOOD, 'CHROME_NO_UPGRADE_FLAGS = '
                   '(f"--disable-features={NO_HTTPS_UPGRADE_FEATURES}",)',
                   'CHROME_NO_UPGRADE_FLAGS = ("--disable-features=HttpsUpgrades,HttpsFirst'
                   'ModeV2,HttpsFirstBalancedMode",)'), "没读 NO_HTTPS_UPGRADE_FEATURES"),
    ("常量在、启动列表里没有它（这一条已经作废）",
     _tab_env_kill(_TAB_ENV_GOOD, ' *CHROME_NO_UPGRADE_FLAGS]', ']'),
     "启动参数列表里没有"),
    ("复用旧实例时根本没问",
     _tab_env_kill(_TAB_ENV_GOOD, 'if missing_no_upgrade_flag() and not _flag_warned:',
                   'if not _flag_warned:'), "没问 missing_no_upgrade_flag()"),
    ("问到了却没说（只给自己记账，不给用户交代）",
     _tab_env_kill(_TAB_ENV_GOOD, '                _flag_warned.append(1)\n'
                   '                log("⚠ 共享浏览器是先前就开着的，没带这个开关")\n',
                   '                _flag_warned.append(1)\n'), "问到了却没说"),
)


def _mirror_load_names(node) -> set:
    """节点里以**读**的方式出现的名字（含嵌套表达式、f-string、推导式里的）。"""
    return {n.id for n in ast.walk(node)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


def _mirror_local_names(node) -> set:
    """节点里**自己绑定**的名字：形参、赋值目标、except as、函数内 import、推导式目标、
    海象、global/nonlocal。自由名字必须把它们减掉，否则每个局部变量都会被当成「模块级依赖」，
    闭包就把整份源码拖进镜像了（那样还不如直接 import）。"""
    bound = set()
    for n in ast.walk(node):
        if isinstance(n, ast.arg):
            bound.add(n.arg)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            bound.add(n.id)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            bound.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            bound |= {(a.asname or a.name.split(".")[0]) for a in n.names}
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            bound |= set(n.names)
    return bound


def _mirror_self_attrs(node) -> set:
    """这个函数读过的 `self.X`（只算读：写不改变镜像要不要补一份默认值）。"""
    return {n.attr for n in ast.walk(node)
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
            and n.value.id == "self" and isinstance(n.ctx, ast.Load)}


# 镜像作用域里**只准**出现标准库和零依赖把手：身份门之所以能在任何一台构建机上跑，靠的就是
# 它不碰 requests／PyQt6／websocket 那一整套。真源哪天把这些拖进 `_tab_page_is` 的依赖闭包，
# 这里必须当场响，而不是让构建前检查变成一条 ImportError。
MIRROR_SAFE_IMPORTS = frozenset({
    "re", "json", "time", "math", "os", "sys", "random", "string", "base64", "hashlib",
    "hmac", "uuid", "socket", "struct", "threading", "collections", "datetime", "functools",
    "itertools", "tempfile", "traceback", "shutil", "subprocess", "pathlib", "urllib",
    "gzip", "zlib", "binascii", "codecs", "copy", "enum", "typing", "warnings", "bisect",
})
# 零依赖把手（第 30 条的真源）：镜像按**真源码**跑一遍，不在这里抄一份断句口径。
# 名字从 REASON_HANDLE 推出来，不另写一遍——写死一次，将来挪文件就是镜像悄悄少一份依赖。
MIRROR_HANDLE_MODULE = PurePosixPath(REASON_HANDLE).stem
# 身份门读的那格「本轮导航为什么没成」在真源里的名字（31e 按这个名字把 Chrome 原话注入镜像；
# 真源要是改了名，31e 的每一档都会退回「标签不在本站」而当场响）
TAB_ID_NAV_ATTR = "_last_nav_error"
# 31e 的镜像里由夹具自己充当的成员：地址那一格。真源用 `_current_url()` 取地址，镜像不能
# 带着它去读 CDP，所以这一档在 `__init__` 里给死；其余 `self.X` 一律要求真源里能静态取到。
MIRROR_SYNTHED_MEMBERS = frozenset({"_cur", "_current_url"})


def _tab_identity_gate_class(src: str, reason_src: str = ""):
    """把真源里的身份门（`_page_key` + `_tab_page_is`）连同它**在模块级需要的每一样东西**
    抠出来，装成一个可独立调用的类。

    为什么抠而不是 import：`cdp_base` 顶部要拉 Qt 与 requests，构建前机器上未必装齐，而这一条
    判据要**在任何一台跑构建的机器上都能跑**才叫门。为什么必须抠**真源**而不是在这里重写一遍
    判据：重写的那份永远绿，改坏实现的人不会在这里撞到东西。

    为什么要按**依赖闭包**抠，而不是写死两个函数名（2026-11-16 本轮修归因时撞到的 NameError）：
    原先只抠 `_page_key`/`_tab_page_is` 两段，实现里新增一个模块级依赖（`tab_load_failed_line`）
    就让镜像在 exec 出来的第 55 行当场撞死——那既不是门坏了、也不是实现坏了，是**判据跟实现分家**。
    分家有两种坏法：撞死在构建前（本轮这种，还算响得早），或者更坏的那一种——镜像少一份依赖，
    行为与真源不同却刚好通过期望表，从此「门改了口径、判据没跟上」再没人知晓。
    """
    tree = _ast_safe_parse(src)
    fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    if not set(TAB_ID_HANDLES[:2]) <= set(fns):
        return None, f"{TAB_ID_SOURCE} 里找不到 {TAB_ID_HANDLES[:2]}（门被改名或搬走，判据与代码分叉了）"
    cls = next((c for c in ast.walk(tree) if isinstance(c, ast.ClassDef)
                and any(isinstance(m, ast.FunctionDef) and m.name == "_tab_page_is"
                        for m in c.body)), None)
    if cls is None:
        return None, "`_tab_page_is` 已经不在任何类里：身份门被搬出基类，31d 的镜像不知道按什么调它"
    methods = {m.name: m for m in cls.body if isinstance(m, ast.FunctionDef)}
    class_attrs = {}
    for st in cls.body:
        if isinstance(st, ast.Assign):
            for t in st.targets:
                if isinstance(t, ast.Name):
                    class_attrs.setdefault(t.id, st)

    # ① 类里这一层：从两个把手出发，把读到的 `self.X` 逐个问出处
    picked, seen, need, stranded = [], set(), list(TAB_ID_HANDLES[:2]), []
    while need:
        name = need.pop(0)
        if name in seen or name in MIRROR_SYNTHED_MEMBERS:
            continue
        seen.add(name)
        fn = methods.get(name)
        if fn is None:
            stranded.append(name)
            continue
        picked.append(fn)
        for attr in _mirror_self_attrs(fn):
            if attr in MIRROR_SYNTHED_MEMBERS:
                continue
            if attr in methods:
                need.append(attr)
            elif attr not in class_attrs:
                stranded.append(attr)
    if stranded:
        return None, (f"身份门镜像补不出 {sorted(set(stranded))}：`self.X` 既不是 {TAB_ID_SOURCE} "
                      "类里的方法、也没有**类体里的默认值**。默认值只准写在类上（账目重置有整整"
                      "三处：切站/续采/新轮各一份，只写进 `__init__` 就会漏掉一处，界面于是读到"
                      "上一轮留下的旧原因）；要是一段解释只需要地址和错误码，就照 "
                      "`tab_load_failed_line` 做成模块级函数")

    # ② 模块级这一层：按依赖闭包把真源里用到的每一样东西都带上（谁先谁后按源码行号，
    #    常量在函数之前定义这件事由源码本身保证，不在这里重排）
    defs, imports = {}, []
    for st in tree.body:
        if isinstance(st, (ast.FunctionDef, ast.ClassDef)):
            defs.setdefault(st.name, st)
        elif isinstance(st, ast.Assign):
            for t in st.targets:
                if isinstance(t, ast.Name):
                    defs.setdefault(t.id, st)
        elif isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
            defs.setdefault(st.target.id, st)
        elif isinstance(st, (ast.Import, ast.ImportFrom)):
            imports.append(st)

    attr_nodes = {}
    for fn in picked:
        for attr in _mirror_self_attrs(fn):
            if attr in class_attrs and attr not in MIRROR_SYNTHED_MEMBERS:
                attr_nodes[attr] = class_attrs[attr]

    def _free(node):
        return _mirror_load_names(node) - _mirror_local_names(node) - {"self"}

    mod_nodes, unbound, done_names = [], set(), set()
    queue = sorted(set(x for node in list(picked) + list(attr_nodes.values())
                       for x in _free(node)))
    while queue:
        name = queue.pop(0)
        if name in done_names:
            continue
        done_names.add(name)
        node = defs.get(name)
        if node is None:
            unbound.add(name)
            continue
        mod_nodes.append(node)
        queue += sorted(x for x in _free(node) if x not in done_names)

    ns = {"urlparse": urlparse}
    for st in imports:
        root = ({a.name.split(".")[0] for a in st.names} if isinstance(st, ast.Import)
                else {(st.module or "").split(".")[0]})
        if not root <= MIRROR_SAFE_IMPORTS or MIRROR_HANDLE_MODULE in root:
            continue          # 第三方/包内专属模块**不许**进镜像：那正是这一条要能独立跑的前提
        try:
            exec(compile(ast.Module(body=[st], type_ignores=[]),
                         f"<{TAB_ID_SOURCE} import>", "exec"), ns)
        except Exception as e:
            return None, f"镜像里 import {ast.unparse(st)} 失败（{e}）：标准库白名单与真源不同步了"
    if MIRROR_HANDLE_MODULE in {r for st in imports
                                for r in ({(st.module or "").split(".")[0]}
                                          if isinstance(st, ast.ImportFrom) else set())}:
        if not reason_src:
            return None, (f"身份门用到了 {REASON_HANDLE} 的把手，但镜像没拿到它的源码："
                          "断句口径会在 exec 里撞成 unbound name，宁可红也不许拿一份手抄的代替")
        try:
            exec(compile(reason_src, f"<{REASON_HANDLE}>", "exec"), ns)
        except Exception as e:
            return None, f"把手 {REASON_HANDLE} 的源码在镜像里跑不起来（{e}）"

    still = sorted(x for x in unbound
                   if x not in ns and x not in MIRROR_SYNTHED_MEMBERS
                   and not hasattr(builtins, x))
    if still:
        return None, (f"身份门的依赖里有镜像取不到的名字 {still}：要么它新拉了一个第三方库"
                      "（这一条判据必须在不装 requests/Qt 的机器上也能跑，别把重依赖带进 "
                      "`_tab_page_is` 的闭包），要么它改从别处取值却没留默认值——两种都要在 "
                      f"{TAB_ID_SOURCE} 里收口，不许在判据这边补一份假的")

    body = ["class _Gate:", "    def __init__(self, cur, **extra):", "        self._cur = cur",
            "        self.__dict__.update(extra)",
            "    def _current_url(self):", "        return self._cur"]
    for node in attr_nodes.values():          # 类里的默认值：从真源带出，不在这儿手写
        body.append(textwrap.indent(ast.unparse(node), "    "))
    for fn in sorted(picked, key=lambda n: (n.lineno, n.col_offset)):
        body.append(textwrap.indent(ast.unparse(fn), "    "))
    program = [ast.unparse(node) for node in
               sorted(mod_nodes, key=lambda n: (n.lineno, n.col_offset))] + ["\n".join(body)]
    exec("\n".join(program), ns)               # noqa: S102 —— 跑的是本仓库自己的源码
    return ns["_Gate"], ""


def _tab_log_texts(fn) -> list:
    """这个函数里每一句 `self._log(...)` 的**文本片段**（f-string 的常量段也算）。
    只取 `_log` 的参数：注释与 docstring 里允许出现「挑战跑完了」这种叙述（那是在讲来历），
    但念给用户听的那一句不允许——它必须只陈述拿得到的证据。"""
    out = []
    for node in ast.walk(fn):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute) and node.func.attr == "_log"):
            continue
        for arg in list(node.args) + [k.value for k in node.keywords]:
            out += [c.value for c in ast.walk(arg)
                    if isinstance(c, ast.Constant) and isinstance(c.value, str)]
    return out


def _tab_nav_evidence_problems(rel: str, tree, strict_names=False) -> list:
    """31f/31f-2：导航证据的三处结构判据 + 结论句口径。只做「东西在不在」，不猜行为。

    `strict_names=True` 只用在**基类那份真源**上：那张名单就是照着它写的，缺一枚说明登记作废。
    夹具与其它模块走 False——「这一档函数在这个文件里不存在」不该被算成不合格，否则每一枚
    夹具都得把整份基类抄一遍，而抄出来的那份永远绿。
    """
    problems = []
    by_name = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef):
            by_name.setdefault(n.name, n)
    for fname, marks, why in TAB_NAV_RULES:
        fn = by_name.get(fname)
        if fn is None:
            problems.append(f"{rel}::{fname} 不在了：{why}——这句话现在没人替它兜着了")
            continue
        src = ast.unparse(fn)
        for m in marks:
            if m not in src:
                problems.append(f"{rel}::{fname} 源码里没有 {m}：{why}")
    for fname in TAB_NAV_LIE_FNS:
        fn = by_name.get(fname)
        if fn is None:
            if strict_names:
                problems.append(f"{rel}::{fname} 不在了：31f-2 的结论句名单作废，"
                                "请连这张表一起改（留着它，将来同名的新句子会白拿这一档信任）")
            continue
        for text in _tab_log_texts(fn):
            if TAB_NAV_LIE_TOKEN in text:
                problems.append(
                    f"{rel}::{fname} 有一句 `_log` 写着「{TAB_NAV_LIE_TOKEN}」：{text[:60]!r}\n"
                    "   → 「挑战跑完」这件事没有任何证据能证明；能证明的只有「这一页已经不是壳」"
                    "「正文过了身份门」「这一跳根本没加载出来」三种观察。把推测当观察念给用户，"
                    "下一次真被浏览器拦下导航时，这句话会把方向指反")
    return problems


def _tab_is_per_company(fn) -> bool:
    """这个函数是不是在取**某一家**的页：签名里出现了「这一家」的把手（记录字典/供应商串/
    `deep` 旗标）就算。这是判「域名级门够不够」的唯一机械依据——列表页的函数只拿得到关键词
    （`kw`），它那一页的身份就是这次搜索的地址；而拿着 `rec`/`user`/`deep` 的函数，同主机下
    首页与上一家的商铺在它眼里必须是**不同的页**（2026-11-16 同类查全：五处漏的都是这一形）。
    """
    names = {a.arg for a in list(fn.args.args) + list(fn.args.kwonlyargs)}
    return bool(names & TAB_PER_COMPANY_ARGS)


def _tab_exit_problems(rel: str, tree, row: dict) -> list:
    """一个模块的「出口清单 ↔ 在案登记」差分。"""
    problems = []
    for fn_name, lineno, how, gates in _tab_body_exits(tree):
        fn = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                   and n.name == fn_name and n.lineno == lineno), None)
        if fn_name not in row:
            problems.append(
                f"{rel} 第 {lineno} 行 {fn_name}（出口方式 {how}）把浏览器标签里的正文交了出去，"
                "却不在 TAB_BODY_EXITS 登记里\n"
                "   → 站方在挑战或 302 之后常把地址落到首页或**上一家**的商铺：那种页里有公司名、"
                "有电话，只是不是这一家。字段填到别人头上比采不到更难被发现（电话打过去才知道）。"
                "要放行就在表里写明它靠哪一步认页——加一项必须同时写下门在哪，这一条门的全部用处"
                "就在这个摩擦上")
            continue
        want = row[fn_name]
        if want not in gates:
            problems.append(
                f"{rel}::{fn_name} 登记的身份门是 {want}，源码里却已经找不到这一步"
                f"（实际用过的：{gates or '没有'}）\n"
                "   → 交出去的那一份正文已经不再证明过「这一页就是我要的那一页」了。"
                "改代码要连表一起改；把门删了留着表，比从来没登记过更坏——后面的检查会以为有门")
        elif want == "_goto" and _tab_is_per_company(fn):
            problems.append(
                f"{rel}::{fn_name} 只登记了域名级 `_goto`，但它的签名里带着「这一家」的把手"
                f"（{sorted(set(a.arg for a in fn.args.args) & TAB_PER_COMPANY_ARGS)}）："
                "同主机下的首页、上一家的商铺在 `_goto` 眼里都算「本站」，这道门防不住串页\n"
                "   → 深链/详情页要登记 `_tab_page_is`（只问地址）或 `_tab_body_for`"
                "（地址＋不是壳＋体积）")
    return problems


def check_tab_body_identity(files=None) -> list:
    """从浏览器标签交出去的正文必须过页面身份门（第 31 条）。"""
    problems = []
    full_scan = files is None
    files = _runtime_reason_sources() if full_scan else dict(files)
    if not files:
        return ["第 31 条一份源码都没读到（三个 *_build 目录全落空）：视野断了的时候，"
                "「没有一处把标签正文直接交出去」这句话没有任何含义"]

    # 31a 全交付链：把标签正文交出去的出口必须**逐条在案**，且登记的门必须真在源码里
    judged = 0
    for path, text in sorted(files.items(), key=lambda kv: str(kv[0])):
        try:
            tree = ast.parse(text)
        except SyntaxError as e:
            problems.append(f"{path.name} 解析不过（第 {e.lineno} 行 {e.msg}）：第 31 条读不到"
                            "这一份的取页写法，按不合格算而不是按「这处没问题」算")
            continue
        judged += 1
        problems += _tab_exit_problems(_rel_label(path), tree,
                                       TAB_BODY_EXITS.get(_rel_label(path), {}))

    # 31a-2 反方向：表里写了、源码里却已经没有的项。两种「已经没有」都要报，因为过期登记会
    # 顶着它的名次让下一条同类缺陷看起来是合法的：
    #   · 函数整个不见了（改名/删掉）；
    #   · 函数还在，但它不再把标签正文交出去了（那条通路被改回裸 HTTP 或换成别的取法，
    #     门跟着作废——留着一行作废的登记，比没有登记更容易骗过后面的检查）。
    for rel, row in TAB_BODY_EXITS.items():
        text = _text_of(files, rel)
        if text is None:
            problems.append(f"登记表里的 {rel} 不在视野里：这一条门对它已经失明")
            continue
        tree = _ast_safe_parse(text)
        names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        exits = {e[0] for e in _tab_body_exits(tree)}
        for fn_name in row:
            if fn_name not in names:
                problems.append(f"{rel} 登记着出口 {fn_name}，源码里已经没有这个函数："
                                "要么它被改名/删掉（表要跟着改，否则下一条同类缺陷会顶用它的名次）")
            elif fn_name not in exits:
                problems.append(f"{rel}::{fn_name} 登记了出口（门 {row[fn_name]}），但它现在已经"
                                "不把浏览器标签里的正文交出去了：这一行登记作废，请删掉——"
                                "留着它，将来同名的新通路会白拿这一档合法身份")

    # 31b 门的真源只有一份：三个把手在整个视野里各只被定义一次，且都在基类
    for name in TAB_ID_HANDLES:
        defs = [str(_rel_label(p)) for p, t in files.items()
                if any(isinstance(n, ast.FunctionDef) and n.name == name
                       for n in ast.walk(_ast_safe_parse(t)))]
        if not defs:
            problems.append(f"全仓没有 {name}：身份门的真源被删了——各站那些「取回正文」的路此后再"
                            "没有任何东西拦着串页")
        elif len(defs) > 1 or defs[0] != TAB_ID_SOURCE:
            problems.append(f"{name} 有 {len(defs)} 处定义（{defs}），身份门只准在 {TAB_ID_SOURCE} "
                            "有一份：各站再抄一份就会长成五种口径，那等于没有门")

    # 31c 反向对照：五份不合格形状必须响、四份合法写法不许响（判据不能被反证，就等于还没修）
    for label, frag, table, want_bad in TAB_ID_CASES:
        try:
            got = _tab_exit_problems("夹具", ast.parse(frag), dict(table))
        except SyntaxError as e:
            problems.append(f"31c 夹具「{label}」自身解析不过（{e.msg}）：夹具坏了不算判据通过")
            continue
        if bool(got) != want_bad:
            problems.append(
                f"31c 判据与夹具「{label}」对不上：期望"
                f"{'响' if want_bad else '不响'}，实得 {got or '不响'}"
                + ("" if want_bad else "（误伤一次，下一个真漏的人就会被当成噪音放过去）"))
    # 31c-2 在案的每一项都必须真的在响的那一侧被验过一遍：表里挂着一条没有任何夹具走到的
    # 门型，等于那道门从没被对拍过。
    _covered = {g for _l, _f, _t, _w in TAB_ID_CASES for g in _t.values()}
    _declared = {g for row in TAB_BODY_EXITS.values() for g in row.values()}
    if _declared - _covered:
        problems.append(f"登记表用了 {sorted(_declared - _covered)} 这一档门，31c 却没有它的夹具："
                        "这一档门从未被对拍过，等于没验")

    # 31d 门本身的地址语义：拿真源重算一遍（期望表在这里独立写死，不读实现的常量）
    src = _text_of(files, TAB_ID_SOURCE)
    if src is None:
        problems.append(f"{TAB_ID_SOURCE} 不在视野里：31d 没有对照面，这一条等于没跑")
    else:
        gate, err = _tab_identity_gate_class(src, _text_of(files, REASON_HANDLE) or "")
        if err:
            problems.append(err)
        else:
            for cur, want, token, same_host, exp_ok, exp_why in TAB_ID_BEHAVIOUR:
                try:
                    ok, why = gate(cur)._tab_page_is(want, token=token, same_host=same_host)
                except TypeError as e:
                    problems.append(f"31d 身份门签名变了（{e}）：调用点与各站夹具都会跟着崩")
                    break
                if ok is not exp_ok:
                    problems.append(f"31d 地址语义不符：标签在 {cur!r}、要 {want!r}"
                                    f"（token={token!r}, same_host={same_host}）→ 实得 "
                                    f"{ok}｜{why}，期望 {exp_ok}")
                elif not exp_ok and exp_why not in (why or ""):
                    problems.append(f"31d 拒收必须说得出为什么：{cur!r} vs {want!r} 实得 {why!r}，"
                                    f"期望含 {exp_why!r}（只说「不行」等于把归因推回读日志）")
            # 31e 「这一跳根本没加载出来」那一支：Chrome 的原话必须被念出来，而且**方向由类别
            # 给**——同一句「没加载出来」背后是站点回错误码／连不上／浏览器拦三种完全不同的处方。
            for cur, want, nav, exp_in, exp_not in TAB_ID_NAVFAIL:
                ok, why = gate(cur, **{TAB_ID_NAV_ATTR: nav})._tab_page_is(want)
                if ok is not False:
                    problems.append(f"31e 标签停在 Chrome 错误页却放行了：{cur!r} vs {want!r} → "
                                    f"{ok}｜{why}（那一页连站点的一个字节都没有，当正文交出就是编造）")
                    continue
                if exp_in not in (why or ""):
                    problems.append(f"31e 归因不符：{cur!r} 带 {nav!r} 实得 {why!r}，期望含 "
                                    f"{exp_in!r}（Chrome 的原话没被念出来＝这一族的根因又丢了）")
                elif exp_not and exp_not in (why or ""):
                    problems.append(f"31e 说反了方向：{nav!r} 那一句里出现了 {exp_not!r}——{why!r}")

        # 31f 导航证据的三处结构判据 + 31f-2 结论句口径（只在基类那份源码上判，且按严格名单）
        problems += _tab_nav_evidence_problems(TAB_ID_SOURCE, _ast_safe_parse(src),
                                               strict_names=True)
        # 31f-3 反向对照：坏形状必须响、现网写法不许响。判据自己也要被反证一次，
        # 否则「三处证据都在」这句话只是我对源码的印象。
        for label, frag, want_bad in TAB_NAV_CASES:
            try:
                got = _tab_nav_evidence_problems("夹具", ast.parse(frag))
            except SyntaxError as e:
                problems.append(f"31f 夹具「{label}」自身解析不过（{e.msg}）：夹具坏了不算判据通过")
                continue
            if bool(got) != want_bad:
                problems.append(
                    f"31f 判据与夹具「{label}」对不上：期望"
                    f"{'响' if want_bad else '不响'}，实得 {got or '不响'}"
                    + ("" if want_bad else "（误伤一次，下一个真漏的人就会被当成噪音放过去）"))

    # 31g 环境侧：这一节管的是「这句谎还有没有必要说」——启动参数没了，归因写得再准，
    # 用户看到的仍然是「这个站采不到」。整轮含两份内联副本、被否决的那枚开关与覆盖面豁免表。
    problems += _tab_env_gate_problems(files)
    for label, frag, want in TAB_ENV_CASES:
        try:
            got = _tab_chrome_env_problems("夹具", frag, ast.parse(frag))
        except SyntaxError as e:
            problems.append(f"31g 夹具「{label}」自身解析不过（{e.msg}）：夹具坏了不算判据通过")
            continue
        if want == "":
            if got:
                problems.append(f"31g 判据误伤合格形状「{label}」：{got[:1]}\n"
                                "   → 误伤一次，下一个真丢开关的人就会被当成噪音放过去")
        elif not any(want in p for p in got):
            problems.append(f"31g 判据与夹具「{label}」对不上：期望响出含 {want!r} 的那一条，"
                            f"实得 {got or '不响'}")

    if full_scan and judged == 0:
        problems.append(f"第 31 条一份都没判到（视野里 {len(files)} 份全解析不过）："
                        "这一条门等于没跑，不许在空集合上报绿")
    return problems


# ── 第 32 条：三本「只累加、从不外读」的账，必须各自有唯一把手并被读出来 ──────────────
# 由来（2026-11-16 第十轮实机，同一个下午现形的三件事）：
#   ① 世界工厂网「电梯导轨」抓到 10 家、四项口径只剩 4 家，报告按旧口径写「大头连四项联系
#      方式都没有，先修取号通路再谈换词」。真翻页面，`联系电话` 那一栏写着 `-`（站方已改成
#      「联系客服登记」才给号）：通路没坏，这句谎把人支使去改本来正确的解析代码。
#      → 「无联系方式」必须按**是谁的责任**分档（档位与定序只由 cdp_base.NC_KIND_ORDER 定义，
#        现共六档；「详情不取页」与「漏接把手」必须分两档，否则处方会被开给一个执行不了的站）。
#   ② 三个 ❌ 全站都只说「该复核的是判定口径」，可复核的人手上没有名单——一句无法执行的处方。
#      → 砍单必须**记名**（每词每阶段留样本）。
#   ③ 六份纯 HTTP 站把 `close_browser` 覆写成空壳 `return None`，降级取页时借来的标签一路
#      留到收工之后（用户的共享 Chrome 里挂着一枚 maimaib2b 的标签没人认领）。
#      → 借来的标签必须还，空壳覆写必须当场响。
# 三条都是同一族病：**账在内存里累加，却从不外读**（与通路账 legs、落点账 landing 同族）。
# 所以本条判的全是结构与同源，不判任何一次实机的运气。
NC_BLOCK_MARKS = ("CONTACT_SLOT_LABEL_RE", "# ── [NC-BLOCK-END]")
NC_MIRROR_FILES = ("b2bcollect_build/cdp_base.py", "ali1688_build/ali1688_crawler.py")
# 32c 分档账在 stats() 里的三格：计数、证据、以及给三处渲染共用的那一句人话
NC_STATS_KEYS = ("nc_kinds", "nc_evidence", "nc_note")
# 32c/32d 必须外读这一本账的三处渲染（少一处，那一处就又回到「只看见总数」的老说法）
NC_READERS = ("b2bcollect_build/cdp_base.py", "ali1688_build/ali1688_crawler.py",
              "b2bcollect_build/channel_health.py", "debug/platform_live_check.py")
# 每一处读家的合格形状不一样：两份镜像必须真调把手并把档位念进日志；GUI 红牌那一行
# 只取现成的 `nc_note`（它不该自己算档位）；体检报告这一处是这本账最大的读家，必须自己
# 走把手（它在 25i 里还要按档给处方，就地拼一句就与日志长成两种说词）。
NC_READ_RULES = {"b2bcollect_build/cdp_base.py": ("handle", "无联系方式分档"),
                 "ali1688_build/ali1688_crawler.py": ("handle", "无联系方式分档"),
                 "b2bcollect_build/channel_health.py": ("key_or_handle", ""),
                 "debug/platform_live_check.py": ("handle", "")}
# 32d 说词只许一个把手：除两份镜像外，任何文件都不许自己拼标签表
NC_LABEL_OWNERS = NC_MIRROR_FILES
# 32a「详情页取页」这一层的函数名登记表：b2bcollect 十路都叫 crawl_detail，1688 那一路历史上
# 叫 `_crawl_company`/`_crawl_shop_company`。名字不同不影响判据，但**漏登记就等于没扫**。
DETAIL_FETCH_FNS = ("crawl_detail", "_crawl_company", "_crawl_shop_company")
# 32h 两份镜像里必须同行为的记账把手本体（docstring 可以不一样，行为不许）
NC_ACCOUNT_FNS = ("_keep_detail", "_note_no_contact")
# 32b 砍单记账的两个把手，与它们各自必须写入的那本名单
CUT_NOTE_HANDLES = ("_note_early_skip", "_note_detail_filter")
CUT_NOTE_BUCKETS = {"_note_early_skip": "kw_early_names",
                    "_note_detail_filter": "kw_filtered_names"}
# 32e 标签归还的凭据词：只认**真调用**（自己关会话的 quit/close 也认，只判「什么都没关」的空壳）。
# 为什么不能拿子串比：`close_browser` 这个名字自己就带着 "close"，而六站覆写的 docstring 里
# 都写了「…见 cdp_base.CdpSiteCrawler.release_tab」——拿子串判时，把归还那一行摘掉也照样能绿
# （25h 反向对照抓到的第三个盲区：这一族连方法名与注释都在替它说话）。
TAB_RELEASE_CALLS = ("release_tab", "close_tab", "_release_verify_tab", "_close_own_tab",
                     "_close_tab",
                     "release", "quit", "close", "shutdown", "destroy")
# 32g 这些函数自己就是「关/归还」的动作：里面 `except …: pass` 会把「没关掉」抹平成
# 「已经收工」（与 32e 同一族，只是那一头看的是「有没有关」，这一头看的是「关失败说不说」）。
TAB_RELEASE_FNS = ("close_browser", "release_tab", "_close_tab", "_close_own_tab",
                   "_release_verify_tab", "close_tab", "quit_browser")
# 但只按**函数名**发判就等于允许改名绕过（25h 已在 32e 上学过一次：名字与注释都会替它说话）。
# 所以另立一张「动作表」：函数体里只要出现下面任何一个调用，它就在干「关/归还」这件事，
# 不管它叫什么（site_adapters 的 `_trim_extra_tabs` 就是这样一枚：真在 close_tab，名字不在表里）。
TAB_ACTION_CALLS = ("close_tab", "release_tabs", "release_tab", "_close_tab",
                    "_close_own_tab", "_release_verify_tab", "quit_browser")


def _nc_block_of(text: str) -> str:
    """按标记取镜像段本体（从判据第一行到 [NC-BLOCK-END]）。取不到就交回标记串，让调用方响。"""
    i = text.find(NC_BLOCK_MARKS[0])
    j = text.find(NC_BLOCK_MARKS[1])
    if i < 0 or j < 0:
        return "（缺标记）"
    return text[i:j]


def _is_abstract_stub(fn) -> bool:
    """这个方法是基类的模板桩吗（除去 docstring 只剩 raise/pass/...）。

    桩子一页都不取，拿它发罚单就是误伤；而误伤一次，下一个真漏接的人就会被当成噪音放过去
    （同族教训见 31f 夹具注释）。"""
    body = [st for st in fn.body
            if not (isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant)
                    and isinstance(st.value.value, str))]
    if not body:
        return True
    if len(body) > 1:
        return False
    st = body[0]
    if isinstance(st, ast.Raise):
        return True
    return (isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant)
            and st.value.value is Ellipsis) or isinstance(st, ast.Pass)


def _nc_read_hits(tree):
    """这一份到底把分档账读出去了几次：只算**真调用把手**与**真取 stats 键**。

    为什么不能拿子串充数（25h 反向对照当场抓到的第二个盲区）：「无联系方式分档」这六个字
    在注释与 docstring 里到处都是，拿子串判时把渲染那句改回「无联系方式合计」也照样过关——
    而用户看到的那一行从此又只剩总数。AST 里没有注释；docstring 倒真的是常量，所以这里
    把它们一个个标出来排除掉（否则「说词在文档里」依然能冒充「词在日志里」）。
    """
    skip = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = list(getattr(node, "body", []))
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                skip.add(id(body[0].value))
    calls, keys, words = set(), set(), set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            calls.add(f.attr if isinstance(f, ast.Attribute)
                      else (f.id if isinstance(f, ast.Name) else ""))
        elif isinstance(n, ast.Attribute) and n.attr in NC_STATS_KEYS:
            keys.add(n.attr)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            if n.value in NC_STATS_KEYS:
                keys.add(n.value)
            if n.value and id(n) not in skip:
                words.add(n.value)
    return calls, keys, words


def _nc_detail_evidence_problems(label: str, tree):
    """32a 每一路的详情取页函数必须**要么留证据、要么声明没有证据可留**。

    静默漏接的形态正是本轮实机读到的那个 unknown：站点对着一百家商铺取过页，账上却说
    「本站没留正文证据」。声明 `NO_DETAIL_EVIDENCE` 也是合格答案（企知道那样详情不取页的站
    本来就没有单店正文可判），但必须写理由——悄悄不接与诚实声明，在报告里必须长得不一样。

    证据往往不在 `crawl_detail` 自己手里，而在它调下去的那条取页腿（mic 的
    `_fetch_contact_http`、makepolo 的 `_fetch`）：所以沿**同一个类里的调用闭包**找，不限层数；
    不跨类猜，偶然同名不算通路。抽象桩不归这一条管（它一页都不取）。
    """
    problems = []
    for cd in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
        fns = {}
        for n in cd.body:
            if isinstance(n, ast.FunctionDef):
                fns.setdefault(n.name, n)
        targets = [fns[name] for name in DETAIL_FETCH_FNS
                   if name in fns and not _is_abstract_stub(fns[name])]
        if not targets:
            continue
        declared = any(isinstance(st, ast.Assign)
                       and any(isinstance(t, ast.Name) and t.id == "NO_DETAIL_EVIDENCE"
                               for t in st.targets)
                       for st in cd.body)
        for fn in targets:
            if declared:
                continue
            seen, queue, hit = set(), [fn.name], False
            while queue:
                cur = queue.pop()
                if cur in seen:
                    continue
                seen.add(cur)
                for c in ast.walk(fns[cur]):
                    if not isinstance(c, ast.Call):
                        continue
                    f = c.func
                    if not isinstance(f, ast.Attribute):
                        continue
                    if f.attr == "_keep_detail":
                        hit = True
                    elif f.attr in fns and f.attr not in seen:
                        queue.append(f.attr)
            if hit:
                continue
            problems.append(
                f"{label} 的 {cd.name}.{fn.name}（第 {fn.lineno} 行）取了详情页却没留证据（"
                f"沿调用闭包查了 {len(seen)} 个方法：{'、'.join(sorted(seen))}）："
                f"收尾那一行只能说「本站没留正文证据（判不了）」，站方没给与我们没解析到分不开"
                f"→ 取到正文处调 `self._keep_detail(html)`；确实没有单店正文可判就在类里声明"
                f" `NO_DETAIL_EVIDENCE = \"理由\"`")
    return problems


def _nc_cut_arg_problems(label: str, tree):
    """32b 砍单记账的调用点必须把公司名传进去（把手本体除外，它就在那里写名单）。"""
    problems = []
    for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
        f = call.func
        if not (isinstance(f, ast.Attribute) and f.attr in CUT_NOTE_HANDLES):
            continue
        if len(call.args) >= 2 or any(k.arg == "name" for k in call.keywords):
            continue
        problems.append(
            f"{label} 第 {call.lineno} 行：`{f.attr}(kw)` 没把公司名传进去——只有一本计数时，"
            f"报告只能说「该复核判定口径」，而复核的人手上没有名单（2026-11-16 三个 ❌ 全站都"
            f"是这句话，谁都没去看砍掉的究竟是谁）→ 加第二个实参，样本进 {CUT_NOTE_BUCKETS[f.attr]}")
    return problems


def _nc_call_names(node) -> list:
    """这个节点里出现过的**被调用者名字**（属性调用取尾名，裸调用取名）。"""
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            out.append(f.attr if isinstance(f, ast.Attribute)
                       else (f.id if isinstance(f, ast.Name) else ""))
    return out


def _release_actions_in(node) -> list:
    """这个节点里出现过的「关/归还」动作（**按动作认，不按名字认**）。

    只按函数名登记表发判，改个名（或把归还动作抽到一个新 helper 里）就从判据视野里消失了，
    那与 32e 曾经「方法名与 docstring 替它说话」是同一个盲区。两个形状算数：
      · 直接调 TAB_ACTION_CALLS 里那一个名字（`sc.close_tab(...)`/`self.release_tab(...)`）；
      · 对 driver 型对象调 `close()`/`quit()`（Selenium 那一侧的关标签就是这个写法）。
    反过来，`f.close()`（文件）、`resp.close()`（响应）不会被算进来：这一条误伤一次，
    下一个真静默的人就会被当成噪音放过去。"""
    out = []
    stmts = node if isinstance(node, (list, tuple)) else [node]
    for n in [c for st in stmts for c in ast.walk(st)]:
        if not isinstance(n, ast.Call) or not isinstance(n.func, ast.Attribute):
            continue
        attr = n.func.attr
        if attr in TAB_ACTION_CALLS:
            out.append(attr)
            continue
        if attr in ("close", "quit"):
            base = n.func.value
            names = []
            while isinstance(base, ast.Attribute):
                names.append(base.attr)
                base = base.value
            if isinstance(base, ast.Name):
                names.append(base.id)
            if any(x and ("driver" in x or x == "chrome") for x in names):
                out.append(attr)
                continue
            if isinstance(base, ast.Attribute) and "driver" in (base.attr or ""):
                out.append(attr)
    return out


def _does_tab_release(fn) -> bool:
    """这个函数整体在不在干「关/归还」这件事（32g 用它选扫描范围）。"""
    return bool(_release_actions_in(fn))


def _silent_release_excepts(fn, whole_fn_is_release: bool = False) -> list:
    """这个函数里那些**把归还动作包在 try 本体、except 却只 pass** 的行号
    （32e/32g 共用同一份判据本体）。

    为什么要钉在「这一格 try 负责了什么」上（同一类误伤在本轮现过一次形）：
    `_goto_homepage_verified` 这类函数什么都做一点，只在收尾 finally 里还标签；
    若只看「函数里有没有 pass-only 的 except」，就会把与归还无关的那几格也开罚单，
    而罚单一旦混进无关项，真正该修的静默就被当成噪音放过去了。

    `whole_fn_is_release=True` 给的是「这个函数自己就叫关/归还」（close_browser /
    close_tab / _close_own_tab 这一族）：它整个职责就是这件事，每一格 pass-only
    都在抹平它自己的失败——拿「try 本体得含归还调用」去要求它，会漏掉**真正的
    最底层自己吞异常**那一形（25h 反向对照当场抓到了这一形：shared_chrome.close_tab
    被改回 `except: pass` 后判据一声不响，而上层整条链的「关失败必须开口」全成死分支）。

    除了 pass 与孤零零一行字符串，什么语句都算「干了点事」：这一条故意写得保守，只拦
    「静默把失败抹平」那一种形状，不把重试、赋值一并当成漏报。"""
    out = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Try):
            continue
        if not whole_fn_is_release and not _release_actions_in(node.body):
            continue          # 这一格负责的不是「关/归还」，不归本条管
        for h in node.handlers:
            if all(isinstance(s, ast.Pass)
                   or (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
                   for s in h.body):
                out.append(h.lineno)
    return out


def _tab_release_problems(label: str, tree):
    """32e 覆写了 close_browser 就必须真的关点东西（空壳＝借来的标签没人认领）。"""
    problems = []
    for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
               and n.name == "close_browser"):
        body = [st for st in fn.body
                if not (isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant)
                        and isinstance(st.value.value, str))]
        shell_only = all(isinstance(st, (ast.Pass, ast.Return))
                         and (not isinstance(st, ast.Return)
                              or st.value is None
                              or (isinstance(st.value, ast.Constant) and st.value.value is None))
                         for st in body)
        if shell_only:
            problems.append(
                f"{label} 第 {fn.lineno} 行的 `close_browser` 是空壳：纯 HTTP 站进入降级取页"
                f"模式后会一路握着借来的标签（2026-11-16 实机：用户的共享 Chrome 里挂着一枚 "
                f"maimaib2b 的商铺页没人认领）→ 收工前调 `self.release_tab(\"收工\")`")
            continue
        hit = [w for w in _nc_call_names(fn) if w in TAB_RELEASE_CALLS]
        if not hit:
            problems.append(
                f"{label} 第 {fn.lineno} 行的 `close_browser` 没调用任何归还动作"
                f"（{'/'.join(TAB_RELEASE_CALLS[:4])} 之一）：这一族的『关』必须落在硬事实上，"
                f"不能只改一个标志位；docstring 里提一句把手名也不算还")
            continue
        # 32g 关失败不许被 `except …: pass` 抹平：那一格里必须有一句人话（连类名也好）
        # close_browser 自己就是收工本体，按整函数算（不要求每一格 try 都直接含归还调用）
        for _ln25 in _silent_release_excepts(fn, True):
            problems.append(
                f"{label} 第 {_ln25} 行的 `except` 里只有 pass：这一族是「关/归还」本体，"
                f"抹平失败就是把「共享 Chrome 里还挂着这一枚标签」说成「已经收工」"
                f"→ 至少打一句「{fn.name} 没成功（类名）」并告诉用户在哪里手动收")
    return problems


def _tab_silent_release_problems(label: str, tree):
    """32g 不覆写 close_browser 的站也得过这一问：只要函数在干「关/归还」，
    里面 `except …: pass` 都算静默失败（判据本体在 _silent_release_excepts 里同一段）。"""
    problems = []
    for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
               and n.name != "close_browser"      # 那一枚由 _tab_release_problems 统一判，不报两遍
               and (n.name in TAB_RELEASE_FNS or _does_tab_release(n))):
        # 名字在登记表里的＝它自己就是归还本体，整函数算；只被动作认出来的
        # 那种（顺带干别的活的），只算包着归还调用的那几格 try，否则就是误伤
        for _ln25 in _silent_release_excepts(fn, fn.name in TAB_RELEASE_FNS):
            tag = "" if fn.name in TAB_RELEASE_FNS else "（按动作认出它在关标签，不看名字）"
            problems.append(
                f"{label} 第 {_ln25} 行的 `{fn.name}` 里 except 只有 pass{tag}：归还失败被"
                f"抹平成「已经收工」（与 32e 同一族：那一头看「有没有关」，这一头看「关失败说不说」）")
    return problems


def _nc_fn_core(fn):
    """把手本体的**行为指纹**：去掉 docstring 后把函数体还原成文本比对。
    不拿整段字面文本对拍是因为两份镜像的注释本来就该写得不一样（一份在基类、一份在
    独立交付包，读者不同）；要钉的是语句序列，不是注释。"""
    body = list(fn.body)
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]
    return ast.unparse(ast.Module(body=body, type_ignores=[]))


def check_no_contact_accountability(files=None, texts=None) -> list:
    """无联系方式责任分档 + 砍单记名 + 借标签归还（第 32 条）。

    `files`／`texts` 是给离线夹具留的注入口：每一条判据里凡是「性质判据」都必须能被反向对照
    （把真源改回修复前的样子，判据要响），否则这一条只是今天对了。
    """
    problems = []
    files = _runtime_reason_sources() if files is None else dict(files)
    if not files:
        return ["第 32 条一份源码都没读到（三个 *_build 目录全落空）：视野断了的时候，"
                "「每一路都留了证据」这句话没有任何含义"]
    # 渲染层不在 *_build 下（debug/ 里的体检器是这一本账最大的读家），单独收一份视野
    if texts is None:
        texts = {}
        for rel in NC_READERS:
            fp = ROOT / rel
            texts[rel] = (fp.read_text(encoding="utf-8", errors="replace")
                          if fp.is_file() else "")
    else:
        texts = dict(texts)

    # 32a 每一路的详情取页必须留证据（或诚实声明没有证据）
    judged = 0
    for path, text in sorted(files.items(), key=lambda kv: str(kv[0])):
        try:
            tree = ast.parse(text)
        except SyntaxError as e:
            problems.append(f"{_rel_label(path)} 解析不过（第 {e.lineno} 行 {e.msg}）：第 32 条"
                            "读不到这一份的写法，按不合格算而不是按「这处没问题」算")
            continue
        judged += 1
        problems += _nc_detail_evidence_problems(_rel_label(path), tree)
        problems += _nc_cut_arg_problems(_rel_label(path), tree)
        problems += _tab_release_problems(_rel_label(path), tree)
        problems += _tab_silent_release_problems(_rel_label(path), tree)
    if judged == 0:
        problems.append(f"第 32 条扫了 0 份源码（视野里 {len(files)} 项全解析失败）：这一条等于没跑")

    # 32b 把手本体必须真的写名单：只 += 计数不记名的把手，等于把这一族重新变成一句话
    base = texts.get(NC_MIRROR_FILES[0]) or _text_of(files, NC_MIRROR_FILES[0]) or ""
    for handle, bucket in CUT_NOTE_BUCKETS.items():
        if f"self._cut_sample(self.{bucket}" not in base:
            problems.append(f"cdp_base.py 的 `{handle}` 没把名单写进 `{bucket}`："
                            "调用点传了名字也没用，样本在半路被丢掉")

    # 32c 分档账必须被外读：stats() 三格 + 收尾那一句 + 四处渲染
    # 查的是 `"nc_note":` 这一格**字典键的写法**而不是子串：只比名字时把键改叫
    # `nc_note_x` 也能过关（25h 的反向对照当场抓到这个盲区——改名就是丢一格，而丢的那一格
    # 正是界面与体检器共用的那一句人话）。
    for key in NC_STATS_KEYS:
        if f'"{key}":' not in base:
            problems.append(f"cdp_base.py 的 stats() 没透出 `{key}`：这本账进不了界面与体检器"
                            "（「只累加、从不外读」正是通路账那一族的原病）")
    for rel in NC_READERS:
        t = texts.get(rel) or ""
        mode, phrase = NC_READ_RULES[rel]
        try:
            tree = ast.parse(t)
        except SyntaxError as e:
            problems.append(f"{rel} 解析不过（第 {e.lineno} 行 {e.msg}）：这一处读不到，"
                            "按不合格算而不是按「这一处没问题」算")
            continue
        calls, keys, words = _nc_read_hits(tree)
        if "nc_kinds_text" not in calls and (mode == "handle" or not keys):
            problems.append(
                f"{rel} 没把分档账读出去（既没真调 `nc_kinds_text`"
                + ("" if mode == "handle" else "、也没取 stats() 里那三格键")
                + f"）：这一处看见的还是那句没有证据的「先修取号通路」（注释里写了不算："
                  f"注释进不了用户的眼睛，却能把子串判据哄成绿）")
        if phrase and not any(phrase in w for w in words):
            problems.append(f"{rel} 的收尾账目行没念「{phrase}」：用户那一行日志还是只有一个"
                            "「无联系方式 N」的总数，各档处方又合回一句模棱两可的话")

    # 32d 说词只许一个把手：除两份镜像外不许出现标签表本体
    for path, text in sorted(files.items(), key=lambda kv: str(kv[0])):
        rel = _rel_label(path)
        if rel in NC_LABEL_OWNERS:
            continue
        if "NC_KIND_LABEL" in text or "NC_KIND_ORDER" in text:
            problems.append(f"{rel} 自己抄了一份分档标签表：同一笔账两种说词时，"
                            f"「谁的责任」这句话就没有唯一答案（唯一真源在 {NC_MIRROR_FILES[0]}）")

    # 32e 借来的标签必须还（见上 _tab_release_problems，已逐份扫过）
    # 这一问要的是「把手函数真的存在」，所以按解析树取函数名**精确**比对，不拿子串充数：
    # `def release_tab` 这一子串会被 `def release_tab_x` 那种改名后缀哄成绿（AR 反向对照抓到）——
    # 改名之后每一处 `self.release_tab(...)` 都会 AttributeError，而判据还在说「唯一入口在」。
    try:
        _base_tree = ast.parse(base)
    except SyntaxError as e:
        _base_tree = None
        problems.append(f"{NC_MIRROR_FILES[0]} 解析不过（第 {e.lineno} 行 {e.msg}）：这一条读不"
                        "到把手本体，按不合格算而不是按「入口还在」算")
    if _base_tree is not None and "release_tab" not in {
            n.name for n in ast.walk(_base_tree) if isinstance(n, ast.FunctionDef)}:
        problems.append(f"{NC_MIRROR_FILES[0]} 里没有 `release_tab` 把手：归还动作没有唯一入口，"
                        "各站会各写一份关标签（而「关失败」最容易在各份里被 except: pass 抹平）"
                        "；把手改名要同时改这一条，否则上层每一处调用都是死的而判据看不见")

    # 32f 两份镜像逐字节对拍：1688 是独立交付包、不能 import，所以内联一份拷贝
    blocks = {}
    for rel in NC_MIRROR_FILES:
        t = texts.get(rel) or _text_of(files, rel) or ""
        blk = _nc_block_of(t)
        if blk.startswith("（缺标记"):
            problems.append(f"{rel} 取不出分档判据那一段（标记 {NC_BLOCK_MARKS}）："
                            "改判据要连标记一起改，否则这一条对不上任何一边")
        blocks[rel] = blk
    if not any(b.startswith("（缺标记") for b in blocks.values()):
        a, b = (blocks[NC_MIRROR_FILES[0]], blocks[NC_MIRROR_FILES[1]])
        if a != b:
            for n, (x, y) in enumerate(zip(a.splitlines(), b.splitlines())):
                if x != y:
                    problems.append(
                        f"两份分档判据从第 {n + 1} 行开始分叉（{NC_MIRROR_FILES[0]} ↔ "
                        f"{NC_MIRROR_FILES[1]}）：{x.strip()[:60]} ↔ {y.strip()[:60]}")
                    break
            else:
                problems.append(f"两份分档判据长度不同（{len(a)} ↔ {len(b)}）：一边多了几档判断")

    # 32h 两份镜像的**记账把手本体**必须同行为：NC-BLOCK 只圈住判据（常量与纯函数），
    # 而「声明过详情不取页的站记 no_page、没声明的记 unknown」这一分叉住在方法里。
    # 方法不在对拍范围内时，只改 cdp_base 一侧不会有人响，1688 那一路就悄悄退回
    # 「把已声明的设计边界说成忘了接把手」（本轮刚消灭的那张假处方）。
    _mh = {}
    for rel in NC_MIRROR_FILES:
        t = texts.get(rel) or _text_of(files, rel) or ""
        try:
            _tree = ast.parse(t)
        except SyntaxError as e:
            problems.append(f"{rel} 解析不过（第 {e.lineno} 行 {e.msg}）：这一处读不到记账把手，"
                            "按不合格算而不是按「两边都读不到就算一致」算")
            _mh[rel] = {}
            continue
        got = {}
        for n in ast.walk(_tree):
            if not (isinstance(n, ast.FunctionDef) and n.name in NC_ACCOUNT_FNS):
                continue
            if n.name in got:
                problems.append(f"{rel} 里 {n.name} 出现了两次（第 {got[n.name].lineno} 行与第 "
                                f"{n.lineno} 行）：两份把手谁生效没人说得上（与 NC_READ_RULES "
                                "那一处同一个道理）")
            got.setdefault(n.name, n)
        _miss = [m for m in NC_ACCOUNT_FNS if m not in got]
        if _miss:
            problems.append(f"{rel} 没有记账把手 {'、'.join(_miss)}：分档账在这一路没人记，"
                            "这一份的 stats() 就只能永远交白卷")
        _mh[rel] = got
    if all(_mh.get(r) for r in NC_MIRROR_FILES) and len(NC_MIRROR_FILES) == 2:
        for m in NC_ACCOUNT_FNS:
            fa, fb = _mh[NC_MIRROR_FILES[0]].get(m), _mh[NC_MIRROR_FILES[1]].get(m)
            if not (fa and fb):
                continue
            ca, cb = _nc_fn_core(fa), _nc_fn_core(fb)
            if ca != cb:
                problems.append(
                    f"记账把手 {m} 在两份镜像里行为不同（{NC_MIRROR_FILES[0]} 第 {fa.lineno} 行 ↔ "
                    f"{NC_MIRROR_FILES[1]} 第 {fb.lineno} 行）：同一笔账在 1688 包里会给出"
                    f"另一种归因。修前一边={ca.splitlines()[:2]} 修后一边={cb.splitlines()[:2]}")
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
        print("❌ 交付链 shell 脚本不合格（这一条判三件事：文案里 `$VAR` 紧跟中文会把报错那行"
              "本身弄坏、登记表里的脚本在不在位、工作区那份是不是提交里那份）：")
        for p in hygiene:
            print(f"   · {p}")
            # 类目跟着问题走：annotation 的标签是从这条问题自己的类目标签上来的，
            # 不是在这里再猜一次「这一批大概都是文案问题」——猜的那一次就是今天满屏
            # 【未提交】却写着「shell 文案」的那一次。
            _tag, _sep, _body = p.partition("】")
            print(f"::error::shell {(_tag[1:] if _sep else '卫生')}：{_body}")
    else:
        print(f"✅ 交付链 shell 脚本：{len(HYGIENE_SCRIPTS)} 份脚本的"
              f"文案写法／在位／已提交三类都合格（{'、'.join(HYGIENE_TAGS)}）")

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

    # errexit 立场（第 28 条）：第 15 条管「这一段在 -e 下有没有会静默死的取值」，
    # 这一条管「这一段到底归谁决定活法」。2026-11-14 Windows 云端红的那一步，本机怎么跑都是
    # 绿的——差的不是代码，是拉起脚本的人带了 `-e -o pipefail`。一份不表态的 shell 有两种生死，
    # 而第 15 条原来那句「脚本里没写 set -e 就一路不设」的假设正是被这件事证伪的。
    posture = check_shell_errexit_posture()
    if posture:
        bad += 1
        print("❌ 手写 shell 没在第一条真命令之前对 errexit 表态（生死交给调用方的选项，"
              "同一份脚本会在一个平台静默死、另一个平台正常跑）：")
        for p in posture:
            print(f"   · {p}")
            print(f"::error::shell errexit 立场：{p}")
    else:
        print("✅ shell errexit 立场："
              f"{len(SHELL_SOURCES)} 份手写 shell 都在第一条真命令之前自己声明了 errexit 的活法"
              "（要么 `set -Eeuo pipefail` 并且逐条接管非零，要么 `set +e` 明说走显式分支）")

    # 行尾立场（第 29 条）：交付链的 shell/脚本/workflow 不许带 CR，且检出时必须是 LF，
    # 而这个「必须是 LF」要由仓库里的一份 .gitattributes 来说，不能由某台机器的全局配置来说。
    line_ending = check_line_ending_stance()
    if line_ending:
        bad += 1
        print("❌ 交付链的行尾不合格，或行尾立场没写在仓库里（Windows 检出成 CRLF 之后 shell"
              "当场死在第一句，而 bash -n 查不出来）：")
        for p in line_ending:
            print(f"   · {p}")
            print(f"::error::行尾立场：{p}")
    else:
        print("✅ 行尾立场：交付链的 shell、脚本、workflow 都不带 CR，且 eol=lf 由仓库里的 "
              ".gitattributes 定死（换一台 autocrlf=true 的机器也一样检出成 LF）")

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

    # 交付链上每一次 curl 都必须自己会停、写进文件的字节必须跨次能用（第 20 条）：
    # 本机取制品连着两趟 rc=124——外层超时把脚本杀了，日志一句原因都没有。判的是结构，
    # 不是这一分钟的网络（判据与由来见 check_curl_bounds 上方注释块）。
    curl_bounds = check_curl_bounds()
    if curl_bounds:
        bad += 1
        print("❌ 交付链 shell 里的 curl 没有自己会停 / 写进本次即弃的落点（慢可以接受，"
              "「永远完不成」不行）：")
        for p in curl_bounds:
            print(f"   · {p}")
            print(f"::error::curl 有界与可续传：{p}")
    else:
        print("✅ curl 有界与可续传：交付链每一次 curl 都自己会停，写进文件的字节都不落在 "
              "mktemp 那种本次即弃的落点上")

    # 同一族的另外两格（判据与由来见各自函数上方注释块）：会开口问人的外部命令要显式关掉
    # 交互；交付链目录里新写一份 shell 忘了登记进 SHELL_SOURCES，等于那道门从来不存在。
    prompt_hangs = check_no_prompt_hang()
    if prompt_hangs:
        bad += 1
        print("❌ 交付链 shell 里有会「开口问人」的外部命令没关掉交互（它不读 stdin、也不看"
              "超时，挂住之后日志一个字都不留）：")
        for p in prompt_hangs:
            print(f"   · {p}")
            print(f"::error::外部命令交互收口：{p}")
    else:
        print("✅ 外部命令交互收口：每一处 credential fill 都带 GIT_TERMINAL_PROMPT=0")

    shell_reg = check_shell_registration()
    if shell_reg:
        bad += 1
        print("❌ 交付链目录里有 shell 没登记进判据清单（那几道门在它身上是空的）：")
        for p in shell_reg:
            print(f"   · {p}")
            print(f"::error::交付链 shell 登记：{p}")
    else:
        print(f"✅ 交付链 shell 登记：{len(SHELL_SOURCES)} 份脚本全部在案"
              f"（扫描目录 {' / '.join(SHELL_SCAN_DIRS)}）")

    # 独立起来的 python 不许继承调用方的 stdin（第 20 条 20f）：本机实发过一次——后台终端被回收
    # 之后 fd 0 是个关闭的描述符，解释器在跑第一行之前就崩，门自己的 print 一句都不出现。
    stdin_gaps = check_python_stdin()
    if stdin_gaps:
        bad += 1
        print("❌ 交付链 shell 里有独立起的 python 没带自己的 fd 0（启动它的终端被回收就在初始化阶段崩）：")
        for p in stdin_gaps:
            print(f"   · {p}")
            print(f"::error::解释器 stdin 独立：{p}")
    else:
        print("✅ 解释器 stdin 独立：手写脚本里每一次独立起 python 都在管道右侧或自带 < /dev/null")

    # 每个跑 Qt 事件循环的入口、每个造过主窗口的离线套件，都要赶在解释器之前把窗口交出去
    # （第 21 条）：先定义的 QApplication 先析构，这条顺序不对就是退出 SIGSEGV。
    qt_teardown = check_qt_shutdown_teardown()
    if qt_teardown:
        bad += 1
        print("❌ Qt 退出顺序不合格（窗口没在 QApplication 之前交出去，关工具会弹系统崩溃报告；"
              "离线套件的退出码还会随机 139，把全绿读成失败）：")
        for p in qt_teardown:
            print(f"   · {p}")
            print(f"::error::Qt 退出顺序：{p}")
    else:
        print("✅ Qt 退出顺序：跑事件循环的入口都先接住返回值、再在 sys.exit 之前把主窗口交出去，"
              "造过主窗口的离线套件也都带着同一条收尾")

    # 凭证与登录态：既不许已经在历史里，也不许等着被下一次 `git add <目录>` 扫进来（第 22 条）。
    state_gaps = check_state_files_not_tracked()
    if state_gaps:
        bad += 1
        print("❌ 状态/凭证文件防护不合格（历史里的凭证删不掉，只能作废重发）：")
        for p in state_gaps:
            print(f"   · {p}")
            print(f"::error::状态文件防护：{p}")
    else:
        print("✅ 状态文件防护：代码里声明的每份 .json 状态文件与 Chrome/驱动形状都没被跟踪，"
              "逐条问过 git「add 那一刻你会不会挡」，工作区里每份 Chrome 配置目录也都整目录被挡")

    # 离线回归这一层守卫自己的三格门（第 23/24/25 条，判据与由来见各自函数上方的注释块）。
    # 范围都取自批跑清单，所以三条会一起响应同一件事：清单读不到 = 三条都没跑。
    exit_guard = check_suite_exit_code_guard()
    if exit_guard:
        bad += 1
        print("❌ 套件的判定与退出码不同源（跑过 Qt 的套件没在收尾自己 os._exit，"
              "全绿也会被批跑读成一次失败）：")
        for p in exit_guard:
            print(f"   · {p}")
            print(f"::error::套件退出码口径：{p}")
    else:
        print("✅ 套件退出码口径：批跑清单里每一份碰过 Qt 的套件都以 flush + os._exit 收尾")

    suite_track = check_regression_suites_tracked()
    if suite_track:
        bad += 1
        print("❌ 批跑清单里有套件不在提交里（本机跑绿的那一路回归，CI 检出的仓库里没有）：")
        for p in suite_track:
            print(f"   · {p}")
            print(f"::error::离线套件入库跟踪：{p}")
    else:
        print("✅ 离线套件入库跟踪：批跑清单展开出来的每一路套件都已在提交里")

    dev_paths = check_no_dev_machine_paths()
    if dev_paths:
        bad += 1
        print("❌ 交付链里有只对一台机器成立的绝对路径（换一台机器就指向不存在的文件，"
              "症状却长得像代码坏了）：")
        for p in dev_paths:
            print(f"   · {p}")
            print(f"::error::开发机绝对路径：{p}")
    else:
        print("✅ 开发机绝对路径：交付链的源码、批跑套件、shell 与 workflow 里没有写死某台"
              "机器的路径（豁免的每一处都在 DEV_PATH_EXEMPT 登记了字样和理由）")

    temp_dir_gaps = check_temp_dir_single_source()
    if temp_dir_gaps:
        bad += 1
        print("❌ 临时目录取值不止一处口径（非 Windows 上 ``C:\\temp`` 兜底会变成相对目录，"
              "择路链会把登录态写进仓库工作区）：")
        for p in temp_dir_gaps:
            print(f"   · {p}")
            print(f"::error::临时目录口径：{p}")
    else:
        print("✅ 临时目录口径：交付链里 TEMP 只在两侧同名的 legacy_temp_root() 里被读一次，"
              "两份实现结构一致")

    name_binding = check_module_name_binding()
    if name_binding:
        bad += 1
        print("❌ 模块级取用了当场还没绑定的名字（引导段一崩，整套回归就只剩一条没有理由的 FAIL）：")
        for p in name_binding:
            print(f"   · {p}")
            print(f"::error::名字取用早于绑定：{p}")
    else:
        print("✅ 名字取用早于绑定：交付链每份 .py 的模块级都只取用当场已绑定的名字"
              "（import 都在使用点之前，没有从未定义过的名字）")

    reason_handle = check_reason_one_handle()
    if reason_handle:
        bad += 1
        print("❌ 失败原因被各处自己按字符数切了一刀（半截词比不说更误导：读到一半的人会照着"
              "那半截去查，而「该怎么处置」写在句首）：")
        for p in reason_handle:
            print(f"   · {p}")
            print(f"::error::原因收口把手：{p}")
    else:
        print("✅ 原因收口把手：交付链的运行时代码没有一处再自己 `splitlines()[0][:N]`，"
              "也没有一处调完把手又对那句收口结果补一刀 `[:N]`，"
              f"真源 {REASON_HANDLE} 与 {'、'.join(REASON_MIRRORS)} 那份逐字节相同，"
              "任何一句原因在任何一档上限下都不留半截词／不丢【类别】前缀／不带孤括号")

    tab_gate = check_tab_body_identity()
    if tab_gate:
        bad += 1
        print("❌ 从浏览器标签交出去的正文没问过「这一页是不是我要的那一页」（串页假数据：字段"
              "填到别人头上，比采不到更难被发现）：")
        for p in tab_gate:
            print(f"   · {p}")
            print(f"::error::标签正文身份门：{p}")
    else:
        print("✅ 标签正文身份门：交付链把浏览器标签正文交出去的每一处出口都在案并验到了它登记的"
              f"那道门（门只有一份真源 {TAB_ID_SOURCE}；{len(TAB_ID_CASES)} 份门型夹具、"
              f"{len(TAB_ID_BEHAVIOUR)} 档地址语义、{len(TAB_ID_NAVFAIL)} 档「这一跳没加载出来」"
              f"的归因、{len(TAB_NAV_CASES)} 份导航证据夹具、{len(TAB_ENV_CASES)} 份环境开关夹具"
              f"（另含 {len(TAB_ENV_MIRRORS)} 份内联副本逐字节对拍、{len(TAB_ENV_EXEMPT)} 条带理由"
              f"的 Chrome 启动点豁免），在 31c~31g 反向对照里对拍过）")

    nc_gate = check_no_contact_accountability()
    if nc_gate:
        bad += 1
        print("❌ 「无联系方式」这本账又变回只有一个总数（分不清是站方没给、站方藏着、还是我们"
              "一条也没抽走），或砍掉的名单、借来的标签没人记账：")
        for p in nc_gate:
            print(f"   · {p}")
            print(f"::error::无联系方式责任分档：{p}")
    else:
        print("✅ 无联系方式责任分档：每一路详情取页都留了正文证据（或带理由声明没有可留的证据），"
              "砍单两个把手都记名、分档三格都被 stats() 透出并被四处渲染读到（说词只有"
              f" `nc_kinds_text` 一个把手），借来的标签收工前都走 `release_tab` 归还，"
              f"两份镜像的判据段逐字节相同、记账把手（{'、'.join(NC_ACCOUNT_FNS)}）行为指纹相同"
              f"（{NC_MIRROR_FILES[0]} ↔ {NC_MIRROR_FILES[1]}）")

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
          "每一份手写 shell 都在第一条真命令之前对 errexit 表了态、"
          "交付链对行尾有写在仓库里的立场（检出必是 LF，不靠任何一台机器的全局配置）、"
          "入库路径在大小写不敏感文件系统上也不撞车、"
          "交付链脚本的编码都写死在脚本里、Windows 装配清单与构建命令互相钉死、"
          "交付 zip 的门两侧都在链路上、"
          "Qt 入口与离屏套件都按「窗口先于 QApplication」收尾、"
          "离线套件的判定与退出码同源、批跑清单里的套件都在提交里、交付链没有开发机绝对路径、"
          "临时目录只在同口径的那一处被读、模块级没有取用早于绑定的名字、"
          "失败原因只按一个把手收口（半截词／丢前缀／孤括号都算不合格）、"
          "从浏览器标签交出去的正文都先过了页面身份门（串页假数据算不合格）、"
          "代码声明的状态文件都没被跟踪也没漏出 ignore、"
          "注释里每一句指路节号都走得通")
    return 0


if __name__ == "__main__":
    sys.exit(main())
