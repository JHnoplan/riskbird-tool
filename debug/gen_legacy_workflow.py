#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从主通道 yml 逐字节切出共享步骤，生成 Win7 兼容通道 yml（需求 10B 的施工工具）。

为什么要有这份生成器、而不是手写第二份 yml：要复用的是 200 多行**每一行都写着翻车
教训**的 bash（sha256 得从标准输入喂、MSYS 会把 /VERSION 当路径改写、PIPESTATUS 只为
归因、makensis 横幅两种写法都收……）。手抄一定会在某个字上漂，而这个缺陷的形状是
「主通道修好了、兼容包还带着旧 bug，两条流水线全绿」——正是这一路要根治的东西。
生成器保证共享步骤的 run 体逐字节相同；此后任何一边被改动，第 42 条的字节对拍当场红。

用法（改完主通道之后重跑，再 git diff 看一眼）：
    python3 debug/gen_legacy_workflow.py
"""
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
MAIN = REPO / ".github/workflows/build-b2bcollect.yml"
OUT = REPO / ".github/workflows/build-b2bcollect-legacy.yml"

HEADER = '''name: Build B2bCollectTool EXE (Windows 7 兼容通道)

# ── 这条流水是干什么的（需求 10B）────────────────────────────────────────────
# 由来：2026-11-19 一台纯小白的电脑装完主通道安装包，双击只得到两句系统错误
# （「丢失 api-ms-win-core-path-l1-1-0.dll」/「Failed to load Python DLL python311.dll」）。
# 需求 10 那一轮已经把「能自动补的当场补」做完了（VC++ 运行库随包带、缺就静默装），
# 剩下的是**补不了的那一半**：api set 是操作系统自己的组件，安装步骤造不出来。
# 所以「任何电脑都无需额外解决问题」这句话要成立，就必须真有一个为老系统打的包。
#
# 唯一的机械出路：CPython 官方二进制从 3.9 起不再支持 Windows 7，所以**目标解释器**
# 必须降到 3.8。这条根据决定了本文件与主通道之间**全部**差异，只有三处：
#   ① 目标解释器 3.8（工具链解释器仍留 3.11，理由见那一步的注释）；
#   ② 随包 ChromeDriver 取 109 那一档（Chrome 110 起不再有 Win7 版）；
#   ③ makensis 传地板 6.1 + 另一个包名（同一份 .nsi 编两种地板，不复制脚本）。
# 其余步骤与主通道**逐字节相同**（第 42 条按步骤名对拍 run 体字节）。为什么这么较真：
# 两条通道若差在打包参数上，将来主通道加一个 --hidden-import 而兼容包没加，Win7 用户
# 就会在一个早已修好的功能上再崩一次，而两条流水都是绿的——本项目在依赖清单、
# hidden-import、自报对拍上各平等过一次这个教训。
#
# 交付边界（这一片**不发** OSS 固定直链、不生成发布清单，是有意的、不是没做完）：
# 直链登记表 upload_release_to_oss.ALLOWED_NAMES 与更新器 tool_update.ALLOWED_ARTIFACTS
# 逐项对齐，多一条固定链接必须与更新器的读侧一起改（那是需求 10C：一个链接自动选包）。
# 宁可这一版只出 GitHub 制品，也不在这里悄悄改白名单让主通道的对拍门冒红。
#
# 本文件由 debug/gen_legacy_workflow.py 从主通道切片生成；共享步骤请**改主通道**
# 再重跑生成器，不要在这里单独改（单独改会被第 42 条按成红）。
on:
  push:
    branches: [ main ]
    paths:
      # 与主通道同一批源码目录：这些目录改了不重打包，发出去的兼容包就带着旧代码，
      # 症状是「修好了但没生效」。判据取自 --add-data 自己（第 6 条触发目录检查），
      # 不在这里另抄一张表——另立一张迟早和清单分叉。
      - 'b2bcollect_build/**'
      - 'enf_clean/**'
      - 'ali1688_build/**'
      - 'riskbird_build/**'
      # 这条通道自己的定义，以及两份**共同**影响产物字节的输入：依赖清单（不钉版本，
      # 由 pip 按 Requires-Python 给 3.8 挑最新版——它一漂，兼容包就跟着漂）与驱动脚本。
      - '.github/workflows/build-b2bcollect-legacy.yml'
      - '.github/requirements/b2bcollect.txt'
      - '.github/scripts/fetch_chromedrivers.py'
      # 主通道改了打包参数也要跟着重打：第 42 条会当场比对两份 yml 的字节，比对一红
      # 这里就必须同步——所以不靠人记得来改这一格。
      - '.github/workflows/build-b2bcollect.yml'
      # 注意：**不含** .github/release/** 与 make_release_manifest.py。那两处只影响
      # 发布清单里的 notes 文案，而本通道不发清单、不改产物字节；把它们放进来只会让
      # 「改一行说明」白烧一次 15 分钟的老系统打包。
  workflow_dispatch:  # 允许在 Actions 页面手动只打这一条通道

jobs:
  build:
    runs-on: windows-latest
    # 单独一个并发组：与主通道互不取消。两条通道打的是两个不同的包，谁红了都不该把
    # 对方正在跑的那一份掐掉——那只会让排障时两条日志同时缺一段。
    concurrency:
      group: build-b2bcollect-legacy-${{ github.ref }}
      cancel-in-progress: true

    steps:
'''

# ── 本通道独有的步骤（与主通道**有意**不同，每一条都写清为什么不同）──────────
SETUP_TARGET = '''    - name: Set up Python 3.8 (target interpreter：进包的那一个)
      # 【整条兼容通道的地基】CPython 官方二进制从 3.9 起不再支持 Windows 7，所以想让
      # 产物在 Win7 上起得来，唯一机械可行的出路是把**目标解释器**降到 3.8。
      # architecture 显式写 x64、不吃默认值：默认值哪天变成 arm64，我们得到的是一个连
      # x86_64 都跑不了的包，而 PE 体检那一步只会说「地板不对」，不会说「架构不对」。
      uses: actions/setup-python@v5
      id: targetpy
      with:
        python-version: '3.8'
        architecture: x64

'''

SETUP_TOOL = '''    - name: Set up Python 3.11 (toolchain interpreter：跑门禁的那一个)
      # 为什么要**两个**解释器，而不是整条流水一起降到 3.8：本仓库的构建前硬门自己用了
      # `ast.unparse`（Python 3.9+ 才有的标准库函数，实测 14 处）。只留 3.8 的话，兼容
      # 通道第一秒红的是那把尺子而不是被测的东西——这是「判据在错的解释器上瞎掉」那一族
      # 缺陷，代价比少一个功能大。所以：门禁跑 3.11、产物用 3.8，两者在下一步分别钉死。
      # 顺序也是判据的一部分：setup-python 每次把所选解释器**前置**进 PATH，后装的 3.11
      # 因此成为裸 `python`。所有与主通道逐字节相同的步骤写的都是裸 `python`，于是它们
      # 天然落在工具链解释器上，共享步骤里不必塞任何分支。
      uses: actions/setup-python@v5
      id: toolpy
      with:
        python-version: '3.11'

'''

PIN_INTERP = '''    - name: Pin both interpreters (两个解释器各是谁，在这里说死)
      # 这一格是整条通道唯一能回答「这个包到底是不是 3.8 打的」的地方（为什么只有这里
      # 判得掉：setup-python 拿不到指定版本时会退而求其次装一个别的版本，而**构建照样一路
      # 绿**——3.9 与 3.8 的产物在 PE 地板、窗口冒烟、自报对拍这些格子上长得一模一样）。
      # 四档判定住在 .github/scripts/check_legacy_toolchain.py 里，不写在 yml 的内联
      # `python -c` 上：第 34 条（语法地板）只看 .py，内联那一份是它的盲区（它自己的红话就
      # 写着「本条款只看 .py，这一处它看不到」）；提成脚本后这段逻辑才真进判据视野，
      # 也才能拿假版本元组跑离线夹具——不必真有两台解释器才测得了一把门。
      # 路径为空的两个守卫留在**这里**：那时连进程都起不来，让 bash 先说清是哪一步没给出。
      shell: bash
      run: |
        set -u
        TARGET_PY="${{ steps.targetpy.outputs.python-path }}"
        TOOL_PY="${{ steps.toolpy.outputs.python-path }}"
        [ -n "$TARGET_PY" ] || { echo "::error::setup-python 没给出目标解释器路径（3.8 那一步没成功，后面的构建全都不算数）"; exit 1; }
        [ -n "$TOOL_PY" ] || { echo "::error::setup-python 没给出工具链解释器路径（3.11 那一步没成功，门禁会跑在错误的解释器上）"; exit 1; }
        # 下面这一行跑在裸 `python` 上——它自己是谁，脚本第一档就要回答（PATH 上若落到
        # 3.8，共享步骤里那些门禁会炸成一句 SyntaxError，听起来像仓库坏了）。
        python .github/scripts/check_legacy_toolchain.py --pin --target-py "$TARGET_PY" --tool-py "$TOOL_PY" \\
          || { echo "::error::双解释器分工不成立：上面已经点出是哪一档不对，别打这个包"; exit 1; }
        echo "TARGET_PY=$TARGET_PY" >> "$GITHUB_ENV"
        echo "TOOL_PY=$TOOL_PY" >> "$GITHUB_ENV"

'''

INSTALL_DEPS = '''    - name: Install dependencies (装进**目标** 3.8 环境)
      # 与主通道同一份清单、同一个安装器（多源回退那套逻辑不抄第二遍），差别只有一个：
      # 用 "$TARGET_PY" 跑。pip_install.py 按 `sys.executable -m pip` 装包——谁跑它就装进
      # 谁的环境。这里必须是 3.8：装错解释器的形状是「3.11 有 PyQt5、3.8 没有，
      # PyInstaller 收不到 Qt，产物开不了界面」，而窗口冒烟会红得看不出根因。
      # 由此产生一条**前置事实**：pip_install.py 自己必须能在 3.8 下解析。它属于
      # .github/scripts/（默认按工具链 3.11 写），所以第 42 条把它单独点名判语法——
      # 不判的话，这条通道会在未来某天 pip_install 用上 3.9 写法之后，红成一句
      # SyntaxError，而不是红成「依赖没装上」。
      shell: bash
      run: |
        set -u
        "$TARGET_PY" .github/scripts/pip_install.py --upgrade pip
        "$TARGET_PY" .github/scripts/pip_install.py -r .github/requirements/b2bcollect.txt

'''

RUNTIME_INVENTORY = '''    - name: Target runtime inventory (进包的运行时逐一对版)
      # 装完立刻问**目标环境自己**装到了什么。清单不钉版本是设计（pip 按 Requires-Python
      # 给 3.8 自动挑还能装的最新版，省掉一份必然漂移的兼容清单），但设计也要落在日志里：
      # 三个月后有人问「那个 Win7 包带的哪版 selenium / 哪版 Qt」，重跑一次云打包是问不
      # 出来的，只有这一格留了字。
      # Qt 主版本在这里被钉死成 5——是判定，不是记录：Qt 6 官方最低支持 Windows 10，而带
      # Qt6 的「Win7 包」能在所有门禁里全绿（runner 是 Win11）。判定为什么不住在这一段的
      # heredoc 里（它第一版就住在这里）：第 34 条的内联 python 正则只认字面量 `python` 打头，
      # `"$TARGET_PY" - <<PYEOF` 是它的**盲区**——提成脚本才真进判据视野，也才喂得进离线夹具。
      shell: bash
      run: |
        set -u
        python .github/scripts/check_legacy_toolchain.py --target-py "$TARGET_PY" --inventory

'''

CHROMEDRIVERS = '''    - name: Download chromedrivers (Win7 那一档：最高 109)
      # 与主通道同一个脚本，只多一个 --max-major：Google 从 Chrome 110 起不再发
      # Win7/8/8.1 版，那类机器上能装到的最新浏览器就是 109。装一套 141 的驱动去驱动一台
      # 装不出来的浏览器没有意义，而症状是「采集 1688/企知道时报 cannot connect」——
      # 一个和「驱动版本」八竿子打不着的话术。旧桶（109/108/107）的取法、命名、落点全部
      # 在脚本内部，判据在 fetch_chromedrivers.py 与 DE 节离线门里，不在这里抄一份。
      run: python .github/scripts/fetch_chromedrivers.py --max-major 109

'''

STAMP = '''    - name: Write build stamp (版本单一真源，用**目标**解释器跑)
      # 与主通道同一份 tool_version.py、同一组参数，差别是用 "$TARGET_PY"：这个模块本身
      # 要进包（--hidden-import tool_version），在 3.8 上跑这一步顺带证明了「随包那份代码
      # 在老解释器上真能执行」——比只判语法多走了一步。算不出号就红在这一格，理由与主
      # 通道那条注释相同（红要红在「生成」那一步，不和一堆打包日志混在一起）。
      shell: bash
      run: |
        set -u
        "$TARGET_PY" b2bcollect_build/tool_version.py --write-stamp b2bcollect_build/version.json --build "${{ github.run_number }}" --commit "${{ github.sha }}"

'''

UCRT_SWAP = '''    - name: Swap in Win7-compatible UCRT (装配期换掉 Win11 版运行库)
      # 由来（2026-11-21 取证，legacy run#4）：42 项存根红只是第一层——包内那份 Win11 版
      # ucrtbase.dll 自己就静态依赖 api-ms-win-core-synch-l1-2-0.dll（Win8 才有的契约
      # 模块）：存根在位时 PE 门按「随包桩导出表实测」放行，存根一剪它立刻变红；真 Win7
      # 上没装 KB2999226 的机器连这个模块名都解析不到。所以光剪存根不够，必须把
      # ucrtbase + api-ms-win-crt-* 整套换成 Win7 兼容版（SDK 的 app-local redist）。
      # 为什么不在安装器里补 KB2999226 而要在装配期换：绿色版 zip 不经过安装器，
      # app-local 替换让两种交付形态都不依赖用户机器上装没装过那个补丁。
      # 替换件自己先过 check_pe_floor 同一把尺子（地板 6.1、最严口径），不合格就
      # fail-closed 红在这一步——不凭记忆写 Windows 行为，用字节说话。
      # 为什么不走构建期排除：PyInstaller 那一步是共享步骤（第 42 条对拍），而 UCRT
      # 是它从 runner 的 System32 扫依赖带进来的，排除参数拦不住；这一整步只在兼容
      # 通道存在，主通道根本不跑（Win10 不需要）。
      shell: bash
      run: |
        set -u
        python .github/scripts/swap_win7_ucrt.py --dist-dir b2bcollect_build/dist/B2bCollectTool

'''

PE_FLOOR = '''    - name: PE floor gate (产物字节里写着的最低 Windows 版本)
      # 需求 10B 里唯一能在**没有 Win7 实机**的情况下机械量出来的一格。为什么量这个：
      # Windows 加载器映射镜像时会把可选头里的 MajorSubsystemVersion/MinorSubsystemVersion
      # 与自身版本比对，镜像要得比系统新就当众拒载——用户看到的就是需求 10 那两句英文
      # 错误，而 PyInstaller、makensis、CI 全都只关心「我有没有写出这个文件」，**构建一句
      # 错都不报**。也就是说「能不能在老系统上加载」早就写在字节里了，只是没人去读。
      # 扫整个 dist 目录而不是只扫主 exe：真实地板由它加载的每一个二进制里最高的那一格
      # 决定（python38.dll / Qt5Core.dll / 随包 chromedriver / selenium-manager.exe），
      # 只测入口的结论等于没测。
      # 诚实边界（脚本里也是这么写的，这里不许拔高）：这一格只证「加载器那一关不拦」，
      # **不**证「Win7 双击一定可用」——运行库是否装齐、驱动能否起来、字体/显卡回退都不在
      # 这张表里。前者由安装器体检补装（需求 10 已交付），后者必须回 Win7 实机验收。
      # 【实测剪 selenium-manager.exe】run#2 实测：它静态导入
      # kernel32!GetSystemTimePreciseAsFileTime（Win8 起）——Win7 的加载器会当场拒掉它。
      # 为什么剪掉而不是把门调松：这个 helper 只在「随包驱动都不配套、要联网现下」那一支
      # 用到，而 Win7 上 Chrome 最高 109、本通道已随包 107/108/109 三档；即使落到那一支，
      # 它在 Win7 上也跑不起来——留着一个开不了的二进制只会多一处 0xc0000139。
      # 为什么不走构建期排除：PyInstaller 那一步是**共享步骤**（第 42 条只允解释器那一
      # 个 token 不同），在它那里加排除会连带改掉正在出货的主通道；而这一整步只在
      # 兼容通道存在，主通道根本不跑 PE 地板门（Win10 不需要）。
      # --prune-keep 是「哪怕通配命中也不许剪」的那道框：入口 exe 被剪掉的话，剩下的
      # 就是一堆开不了的零件。
      # 【实测剪 api-ms-win-core-* 存根】run#4 实测：30 个 core 存根全是 PyInstaller 从
      # Win11 System32 扫依赖带进来的（地板 10.0，Win7 加载器当场拒载）。上一步把 UCRT
      # 换成 Win7 版后它们失去了唯一的依赖方，剪掉不影响任何镜像；crt 转发器不在剪名单
      # 里——它们已被换成 Win7 版，要留在包里给 python38.dll 用。--max-prune 抬到 40：
      # 实测 30 个 core 存根 + selenium-manager 共 31 件，默认 4 会当场停下来问人；
      # 四道安全框不动（只剪实测不合格且命中通配的、剪后必复检、超限先问人）。
      shell: bash
      run: |
        set -u
        cd b2bcollect_build
        python ../.github/scripts/check_pe_floor.py --scan-dir dist/B2bCollectTool --floor 6.1 \\
          --prune-match selenium-manager.exe --prune-match api-ms-win-core-*.dll \\
          --prune-keep B2bCollectTool.exe --max-prune 40

'''

PE_FLOOR_SETUP = '''    - name: PE floor gate on the installer (安装包自己的地板)
      # 为什么不和上面那一步一起检：安装包是**这一步才**由 makensis 造出来的。生成器
      # 早期版本把这两条写在了一起（第 14 步去检一个还不存在的文件），那只会得到一句
      # 「文件不在位」——那是顺序错，不是产物错，而且它被 run#2 那 49 项假红盖住了：
      # 不先修假红，这一条还要再藏一轮。
      # NSIS 写的子系统版本比 6.1 还老（这一格脚本对「早于登记表」的形状不误伤，判据在
      # test_frozen_delivery_offline.py 的 DH 节）；真高起来时该问的是编译器参数而不是产物。
      shell: bash
      run: |
        set -u
        cd b2bcollect_build
        python ../.github/scripts/check_pe_floor.py B2bCollectToolSetup_Win7.exe --floor 6.1

'''

UPLOAD = '''    - name: Upload artifacts (Win7 兼容包)
      # 两个产物：安装器（纯小白路径，带运行环境体检与运行库补装）+ 绿色版目录 zip
      # （不让装软件 / 放共享盘的兜底）。名字一律带 _Win7 后缀，与主通道逐字区分——
      # 同名的两个包会在下载侧互相覆盖，而覆盖这件事是静默的。
      # 交付口径：这一版只到 GitHub 制品。固定直链与「一个链接自动选包」是需求 10C，
      # 它要同时改 upload_release_to_oss.ALLOWED_NAMES 与 tool_update.ALLOWED_ARTIFACTS
      # /MANIFEST_BY_KEY 两处登记表，那一刀必须一次切完，不能在这里先漏一条链接。
      uses: actions/upload-artifact@v4
      with:
        name: B2bCollectTool_Win7
        path: |
          b2bcollect_build/B2bCollectToolSetup_Win7.exe
          b2bcollect_build/B2bCollectTool_Win7_portable.zip
        if-no-files-found: error
'''

STEP_HEAD = re.compile(r"^    - (?:name|uses):\s*(.*?)\s*$")
SHARED_NOTE = ("      # 【需求 10B】本步骤与主通道**逐字节相同**（第 42 条按步骤名对拍 run 体字节）。\n"
               "      # 要改这段就先去改主通道，再重跑 debug/gen_legacy_workflow.py；在这里单独改\n"
               "      # 会被那条门按成红——而红话会直接说出「两条通道已经不同源」这件事。\n")


def slice_steps(text: str) -> dict:
    """把主通道的每个步骤切成「含注释与 env 的整块原文」，键是步骤名。"""
    lines = text.splitlines(keepends=True)
    out, keys, start = [], [], None
    for i, ln in enumerate(lines):
        m = STEP_HEAD.match(ln)
        if not m:
            continue
        if start is not None:
            out.append((keys[-1], "".join(lines[start:i])))
        keys.append(m.group(1))
        start = i
    if start is not None:
        out.append((keys[-1], "".join(lines[start:])))
    got = {}
    for k, v in out:
        # **原字节保留**，包括步骤之间那一行排版空行。上一版在这里写了 `v.rstrip("\n") + "\n"`，
        # 理由是「顺手对齐排版」——结果切出来的 run 体比主通道少一个尾部换行，第 42 条当场
        # 报出 10 条「不再逐字节相同」（而它的红话说不清真因，只写了「行数都不同（14 vs 14）」）。
        # 教训：生成器的职责是**忠实复制**，任何“顺手归一”都在制造两个文件之间真实的字节差异。
        got[k] = v
    return got


def swap_env(block: str, mapping: dict, who: str) -> str:
    """只替换步骤内 env 的**值**；run 体一个字节都不许动（第 42 条只比 run 体）。"""
    for old, new in mapping.items():
        if old not in block:
            raise SystemExit(f"{who}：共享步骤里没找到要替换的 env 钉子 {old}\n"
                             "  → 主通道那一行改过了。先确认「换值」这件事还成不成立，再改这里。")
        block = block.replace(old, new)
    return block


def main() -> int:
    steps = slice_steps(MAIN.read_text(encoding="utf-8"))

    def shared(title, env_map=None, note=None, py_target=False):
        if title not in steps:
            raise SystemExit(f"主通道里没有名为「{title}」的步骤（步骤名被改了？第 42 条也钉这个）")
        block = steps[title]
        if py_target:
            old = "        python -m PyInstaller"
            if old not in block:
                raise SystemExit(f"{title}：没找到主通道那行 PyInstaller 的固定前缀，切片失败")
            block = block.replace(old, '        "$TARGET_PY" -m PyInstaller')
        if env_map:
            block = swap_env(block, env_map, title)
        if note:
            head, _, rest = block.partition("\n")
            block = head + "\n" + note + rest
        return block

    parts = [
        HEADER,
        steps["Checkout code"],
        SETUP_TARGET, SETUP_TOOL, PIN_INTERP,
        shared("Packaging manifest gate"),
        shared("Write bundled config.json from Secrets"),
        INSTALL_DEPS, RUNTIME_INVENTORY, CHROMEDRIVERS, STAMP,
        shared("Build EXE with PyInstaller", py_target=True, note=SHARED_NOTE),
        shared("Prune Qt hook leftovers + Artifact weight gate (剪裁 + 成分账)"),
        UCRT_SWAP,
        PE_FLOOR,
        shared("Artifact doctor gate (B2bCollectTool.exe --acl-doctor)"),
        shared("Window smoke gate (B2bCollectTool.exe --ui-smoke)"),
        shared("Artifact self-report gate (产物必须自报是哪一版)"),
        shared("Package Windows artifacts",
               env_map={'PORTABLE_NAME: "B2bCollectTool_Win_portable.zip"':
                        'PORTABLE_NAME: "B2bCollectTool_Win7_portable.zip"'}),
        shared("Provision NSIS (钉版本 + 校验 sha256)"),
        shared("Provision VC++ redist (钉版本 + 校验 sha256)"),
        shared("Build Windows installer (NSIS, per-user)",
               env_map={'OUT_FILE: "B2bCollectToolSetup.exe"':
                        'OUT_FILE: "B2bCollectToolSetup_Win7.exe"',
                        'PORTABLE_NAME: "B2bCollectTool_Win_portable.zip"':
                        'PORTABLE_NAME: "B2bCollectTool_Win7_portable.zip"',
                        'MIN_WIN_MAJOR: "10"': 'MIN_WIN_MAJOR: "6"',
                        'MIN_WIN_MINOR: "0"': 'MIN_WIN_MINOR: "1"'}),
        PE_FLOOR_SETUP,
        UPLOAD,
    ]
    text = "".join(parts)
    OUT.write_text(text, encoding="utf-8")
    print(f"已生成 {OUT.relative_to(REPO)}：{len(text.splitlines())} 行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
