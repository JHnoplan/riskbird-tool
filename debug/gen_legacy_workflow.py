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

UCRT_REDIST = '''    - name: Provision VS2015U3 redist (Win7 UCRT 替换源，钉版本 + 校验 sha256)
      # 由来（2026-11-21 台账第 11 项第二轮取证）：第一版修法取 runner 上的 Windows SDK
      # Redist\\ucrt——云端 run#5 当场证伪：runner 只有 26100 一档，那份 ucrtbase 是 Win10
      # 构建，照样依赖 Win8 契约模块。三条「完整 Win7 UCRT」的路逐一实测后只剩这一条：
      # VS2015U3 redist（14.0.24215.1）的附加容器 a10 = 完整 app-local UCRT 套件 47 件
      #（本机从官方原件纯标准库解出、47/47 逐字节对拍过）。KB2999226 独立 MSU 内层全是
      # PA delta（确认没有完整字节），旧档 SDK runner 上没有（确认没有）。
      # 与主通道 Provision VC++ redist 同一套纪律：版本化 URL + sha256 钉死（download.microsoft.com
      # 的历史版本长期不删）；哈希从标准输入喂 sha256sum，输出里没有文件名可转义。
      # 注意这份与安装器 deps 里那份**不是同一个文件**：那份是最新版（给 Win10 补装用），
      # 这份钉在 2015U3（只有它的 a10 才是 Win7 完整 UCRT）——两份各钉各的，不共用。
      shell: bash
      env:
        WIN7_UCRT_REDIST_URL: "https://download.microsoft.com/download/6/A/A/6AA4EDFF-645B-48C5-81CC-ED5963AEAD48/vc_redist.x64.exe"
        WIN7_UCRT_REDIST_SHA256: "da66717784c192f1004e856bbcf7b3e13b7bf3ea45932c48e4c9b9a50ca80965"
      run: |
        set -u
        # 哈希取法的格式依据（出处＝SHA-256 的定义本身，不依赖 sha256sum 的行文话术，
        # 与主通道 Provision VC++ redist 那一步同源）：摘要就是 64 位十六进制，所以只收
        # 64 位十六进制；从标准输入喂 sha256sum 是因为输出里没有文件名可转义——GNU 工具
        # 在带反斜杠的文件名上会给整行加转义前缀，awk '{print $1}' 会拿到 \\fcdce…。
        # （这里必须双写反斜杠：本常量是普通三引号串，单反斜杠紧跟 f 会被 Python
        # 解成换页符 0x0C 写进 yml——YAML 拒收控制字符，整条流水 jobs=0 静默失败，
        # legacy run#6 实发；连这句注释本身都不能写出那个转义序列，否则它自己就是雷。）
        REDIST="$GITHUB_WORKSPACE/vc2015u3_x64.exe"
        curl -sSLf --retry 3 --connect-timeout 20 --max-time 300 -o "$REDIST" "$WIN7_UCRT_REDIST_URL" \\
          || { echo "::error::VS2015U3 vc_redist.x64.exe 下载失败（微软官方 CDN 抖动，重跑一次；连红就是钉住的 URL 失效了，找新的版本化地址重钉）"; exit 1; }
        got=$(sha256sum < "$REDIST" | grep -oE "[0-9a-fA-F]{64}" | head -1 || true)
        [ "$got" = "$WIN7_UCRT_REDIST_SHA256" ] || {
          echo "::error::VS2015U3 redist 的 sha256 与钉住的值不一致：期望 $WIN7_UCRT_REDIST_SHA256 实际 ${got:-无}。这份钉的是 2015U3 历史版（字节不该变）——对不上多半是下错了文件或 URL 被重定向，本机实拉一份重算再重新钉，绝不带着旧哈希放过新字节"; exit 1; }
        echo "· Win7 UCRT 替换源：$(wc -c < "$REDIST") 字节，sha256 已校验（VS2015U3 14.0.24215.1）"

'''

UCRT_SWAP = '''    - name: Swap in Win7-compatible UCRT (装配期换掉 Win11 版运行库)
      # 由来（2026-11-21 取证，legacy run#4）：42 项存根红只是第一层——包内那份 Win11 版
      # ucrtbase.dll 自己就静态依赖 api-ms-win-core-synch-l1-2-0.dll（Win8 才有的契约
      # 模块）：存根在位时 PE 门按「随包桩导出表实测」放行，存根一剪它立刻变红。所以光剪
      # 存根不够，必须把 ucrtbase + api-ms-win-crt-* 整套换成 Win7 兼容版。
      # 第二轮修法（run#5 证伪 SDK 来源后，用户拍板）：替换源改上一步钉死的 VS2015U3
      # redist——脚本用纯标准库从 burn 容器里抽含 ucrtbase 的那套（实测=a10，47 件），
      # 不依赖 expand/cabextract（「存在」不等于「本平台可执行」）。替换件先过
      # check_pe_floor 同一把尺子（随包桩表给 a10 自己的桩集：VS2015 版 ucrtbase 也依赖
      # synch，但 a10 自带那个桩且实测导出 Sleep），替换后再做前后差分覆盖对拍，
      # 新增缺口即 fail-closed——不凭记忆写 Windows 行为，用字节说话。
      # 为什么不在安装器里补 KB2999226 而要在装配期换：绿色版 zip 不经过安装器，
      # app-local 替换让两种交付形态都不依赖用户机器上装没装过那个补丁。
      # 为什么不走构建期排除：PyInstaller 那一步是共享步骤（第 42 条对拍），而 UCRT
      # 是它从 runner 的 System32 扫依赖带进来的，排除参数拦不住；这一整步只在兼容
      # 通道存在，主通道根本不跑（Win10 不需要）。
      shell: bash
      run: |
        set -u
        python .github/scripts/swap_win7_ucrt.py --dist-dir b2bcollect_build/dist/B2bCollectTool \\
          --redist "$GITHUB_WORKSPACE/vc2015u3_x64.exe"

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
      # 【api-ms-win-core-* 的剪枝已退役（第三轮，run#8 取证）】run#4 时代 dist 里那
      # 30 个 core 存根是 PyInstaller 扫依赖带进来的 Win11 孤儿（地板 10.0），当时 UCRT
      # 替换只换 crt+ucrtbase，剪掉它们是唯一的去留手段；现在替换步从 a10 redist 整套
      # 拷贝 core+crt+ucrtbase 覆盖同名 Win11 镜像——剪枝对象已被换成合格镜像，其中
      # api-ms-win-core-synch 转发器还是替换后 ucrtbase 的加载依赖，同一条 glob 会反
      # 手把刚带进包里的支撑桩剪掉。run#8 正是门绿而产物自检 0xC0000409 fastfail，
      # 首嫌就是那条剪枝把 UCRT 掏空（取证步在下一步单独量）。--max-prune 40 维持：
      # 现测只剪 selenium-manager 一件，上限是给「新桩来源先停下来问人」留的余量。
      shell: bash
      run: |
        set -u
        cd b2bcollect_build
        python ../.github/scripts/check_pe_floor.py --scan-dir dist/B2bCollectTool --floor 6.1 \\
          --prune-match selenium-manager.exe \\
          --prune-keep B2bCollectTool.exe --max-prune 40

'''

DIAG_BEFORE_DOCTOR = '''    - name: Legacy crash forensics (doctor 前的取证：只在兼容通道跑)
      # 由来（run#8，2026-11-21）：legacy 产物第一次活到产物自检步，B2bCollectTool.exe
      # --acl-doctor 以 0xC0000409（fastfail/无效参数那一族）退出且没留下
      # acl_doctor.txt——共享步只报退出码，分不出「bootloader 就炸」还是「doctor 逻辑炸」。
      # 这一步用三发取证把范围夹出来：甲 `--version` 对照（最轻入口，也炸就是启动段）；
      # 乙 把 stdout/stderr 带回来（python 层异常会留字，fastfail 不会）；丙 列替换现场
      # （ucrtbase/synch 桩在不在、多大、哈希前 8 位——剪子是不是把 a10 支撑桩剪了，在这里见分晓）。
      # 这一步的取证形状在 run#9 后升过一级：甲/乙改 Start-Process -Wait -PassThru 的
      # 可靠调用（run#9 实发 & 直接调用对 fastfail 进程拿到 rc=空——读不出，不许当成
      # 「没炸」；同一步里共享自检步的 Start-Process 形状拿到了 -1073740791，证明形状可信）。
      # 它不在共享十步里，不会破第 42 条的逐字节对拍；主通道不跑这一步。
      shell: pwsh
      run: |
        $exe = "b2bcollect_build\\dist\\B2bCollectTool\\B2bCollectTool.exe"
        Write-Host "── 甲：最轻入口对照 ──"
        $o1 = "$env:RUNNER_TEMP\\diag_version.out"; $e1 = "$env:RUNNER_TEMP\\diag_version.err"
        $pv = Start-Process -FilePath $exe -ArgumentList "--version" -NoNewWindow -Wait -PassThru -RedirectStandardOutput $o1 -RedirectStandardError $e1
        Write-Host "--version rc=$($pv.ExitCode)"
        Get-Content $o1 -TotalCount 5 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "out: $_" }
        Get-Content $e1 -TotalCount 5 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "err: $_" }
        Write-Host "── 乙：doctor 带 stdout/stderr ──"
        $o2 = "$env:RUNNER_TEMP\\diag_doctor.out"; $e2 = "$env:RUNNER_TEMP\\diag_doctor.err"
        $pd = Start-Process -FilePath $exe -ArgumentList "--acl-doctor" -NoNewWindow -Wait -PassThru -RedirectStandardOutput $o2 -RedirectStandardError $e2
        $drc = $pd.ExitCode
        Get-Content $o2 -TotalCount 30 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "out: $_" }
        Get-Content $e2 -TotalCount 10 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "err: $_" }
        Write-Host "── 丙：替换现场清点 ──"
        $d = "b2bcollect_build\\dist\\B2bCollectTool\\_internal"
        foreach ($n in @("ucrtbase.dll", "api-ms-win-core-synch-l1-2-0.dll", "api-ms-win-crt-runtime-l1-1-0.dll")) {
          $f = Get-ChildItem -Path "b2bcollect_build\\dist\\B2bCollectTool" -Recurse -Filter $n -ErrorAction SilentlyContinue | Select-Object -First 1
          if ($f) { $h = (Get-FileHash $f.FullName -Algorithm SHA256).Hash.Substring(0,8); Write-Host "在位 $n size=$((Get-Item $f.FullName).Length) sha256前8=$h" } else { Write-Host "不在包 $n" }
        }
        $core = @(Get-ChildItem -Path "b2bcollect_build\\dist\\B2bCollectTool" -Recurse -Filter "api-ms-win-core-*.dll" -ErrorAction SilentlyContinue).Count
        Write-Host "包里 api-ms-win-core-* 总数=$core"
        Write-Host "── 丁：分层对照实验（run#9 取证升级；剪枝假设已证伪，桩全在仍 fastfail）──"
        # run#9 实测三件：PE 门绿、a10 全套在位（ucrtbase B2C22623 / synch / crt-runtime）、
        # doctor 仍 0xC0000409——「剪枝掏空」首嫌证伪。差异面只剩：3.8 构建本身 vs swap 引入的
        # app-local a10 UCRT。机制假设：Win10+ 上 app-local ucrtbase 是不受支持的覆盖（官方
        # UCRT 文档：它是系统组件；main 通道 app-local 是 Win11 同版本所以从不炸），一旦经
        # 基名解析/显式加载进来，与系统 CRT 串扰 → invoke_watson → fastfail。丁三档把它夹出来：
        # A 只移 ucrtbase.dll／B 只移 api-ms-* 转发器／C 整套移走；每档跑 --version 记 rc、跑
        # doctor 记 rc，**立刻还原**并核对计数，还原不彻底就地拦停（不许留半还原状态出包）。
        # 读数表：C 绿且 A 绿 → app-local ucrtbase 是炸点；C 绿而 A 红 → 炸在转发器组合；
        # C 也红 → 与 UCRT 无关，转 3.8 构建启动段排查。
        function Run-Diag([string]$tag) {
          $ov = "$env:RUNNER_TEMP\\dv_$tag.out"; $ev = "$env:RUNNER_TEMP\\dv_$tag.err"
          $p1 = Start-Process -FilePath $exe -ArgumentList "--version" -NoNewWindow -Wait -PassThru -RedirectStandardOutput $ov -RedirectStandardError $ev
          Write-Host "[$tag] --version rc=$($p1.ExitCode)"
          Get-Content $ev -TotalCount 3 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "[$tag] err: $_" }
          $od = "$env:RUNNER_TEMP\\dd_$tag.out"; $ed = "$env:RUNNER_TEMP\\dd_$tag.err"
          $p2 = Start-Process -FilePath $exe -ArgumentList "--acl-doctor" -NoNewWindow -Wait -PassThru -RedirectStandardOutput $od -RedirectStandardError $ed
          Write-Host "[$tag] acl-doctor rc=$($p2.ExitCode)"
          Get-Content $ed -TotalCount 3 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "[$tag] err: $_" }
        }
        function Move-Ucrt([string]$tag, [string[]]$patterns) {
          $stg = Join-Path $env:RUNNER_TEMP ("ucrt_" + $tag)
          if (Test-Path $stg) { Remove-Item $stg -Recurse -Force }
          New-Item -ItemType Directory -Path $stg | Out-Null
          $moved = @()
          foreach ($pat in $patterns) {
            @(Get-ChildItem -Path $d -Recurse -Filter $pat -ErrorAction SilentlyContinue) | ForEach-Object {
              Move-Item -LiteralPath $_.FullName -Destination (Join-Path $stg $_.Name)
              $moved += $_.FullName
            }
          }
          return $moved
        }
        function Restore-Ucrt([string]$tag, [string[]]$moved) {
          $stg = Join-Path $env:RUNNER_TEMP ("ucrt_" + $tag)
          foreach ($full in $moved) {
            Move-Item -LiteralPath (Join-Path $stg (Split-Path $full -Leaf)) -Destination $full
          }
          $ubu = @(Get-ChildItem -Path $d -Recurse -Filter "ucrtbase.dll" -ErrorAction SilentlyContinue).Count
          if ($ubu -lt 1) { Write-Host "::error::丁档 $tag 还原后 ucrtbase 不在位（实验污染了产物，禁止带着半还原状态继续出包）"; exit 1 }
        }
        Run-Diag "S0"
        $mva = Move-Ucrt "A" @("ucrtbase.dll")
        Run-Diag "A"; Restore-Ucrt "A" $mva
        $mvb = Move-Ucrt "B" @("api-ms-win-core-*.dll", "api-ms-win-crt-*.dll")
        Run-Diag "B"; Restore-Ucrt "B" $mvb
        $mvc = Move-Ucrt "C" @("ucrtbase.dll", "api-ms-win-core-*.dll", "api-ms-win-crt-*.dll")
        Run-Diag "C"; Restore-Ucrt "C" $mvc
        if ($drc -ne 0) { Write-Host "::error::doctor 仍未通过（rc=${drc}）：上面甲乙丙丁的取证与丁三档读数就是下一步的分工依据，不许用重跑掩盖" ; exit $drc }

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
        UCRT_REDIST,
        UCRT_SWAP,
        PE_FLOOR,
        DIAG_BEFORE_DOCTOR,
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
