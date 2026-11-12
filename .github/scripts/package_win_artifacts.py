#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Windows 交付物装配：验 onedir 布局 → 打绿色版 zip → 顺手验安装包脚本的编码。

为什么单独一个脚本，而不是把这几句抄在 workflow 的 run 里（2026-11-12 Windows 实机）：
  旧产物是 ``--onefile``：整个运行时每次启动解到 ``%TEMP%\\_MEI<pid>``，退出即删。
  用户「切换账号后点重启」就撞在这条链路上——旧实例正在删自己的临时目录，新实例
  同时在（PID 复用时会撞名的）同一个目录上开始解包，读到一半的归档被对方删掉：
      FileNotFoundError: ...\\_MEI000025982\\base_library.zip
  栈里全是 importlib / zipimport，看起来却像「找不到文件」，排查方向极容易被带去
  查网络和权限。第二重代价更隐蔽：onefile 的 ``__file__`` 指向那个退出即删的目录，
  所有按「模块所在目录」写的 Cookie 与缓存每次归零，症状是「每天白烧一遍查询额度」。
  现在改 onedir，交付物从「一个 exe」变成「一个目录」，于是多了三件必须有人验的事：
    1) 目录布局真的对（exe 在、``_internal`` 在、随包凭证与跨目录采集器都在）——
       onefile 残留混进制品是最坏形状：用户装了目录版却又跑了那个单文件 exe，
       本轮修好的问题会原样复发，而且这次没人能看出来为什么；
    2) 绿色版 zip 的**内部结构**（顶层必须是一个目录名，解压出来不能是一堆散文件，
       否则用户会把散文件解压到桌面，安装目录和数据目录直接被摊进用户可见区域）；
    3) NSIS 脚本与使用说明必须是 **UTF-8 with BOM**。makensis 只在看到 BOM 时才按
       UTF-8 读脚本，否则按系统代码页解释——英文 Windows 的 runner 是 cp1252，
       中文界面文字全部变乱码，而构建照样「成功」。这种「只在产物里暴露的成功」
       必须由脚本而不是人眼来拦。
    4) 卸载段那条「保留数据」的分支必须**真的只删程序文件**（见 check_installer_uninstall）。
       2026-11-12 读这份脚本时发现：两个选项最后都走到同一句 ``RMDir /r "$INSTDIR"``，
       于是「选『否』保留 b2bcollect_data」这句承诺是空的。编译器与卸载日志都无从反映，
       只有下次重装时用户才发现登录状态没了。
    5) 安装段必须**逐条验拷进去的关键文件**（见 check_installer_payload）。CI 上验的一直
       只是 dist 树，没有任何一处验过「makensis 真的把这些文件放进了用户机器」；而
       ``SetOverwrite try`` 的语义是「目标被占用就跳过、不算失败」，于是新 exe 配旧运行时
       这种拼起来的版本照样报安装成功。这一条同时把 .nsi 里那份手抄的关键文件清单钉回
       REQUIRED_INTERNAL。

用法（CI 在 b2bcollect_build 目录下调用，也可从仓库根调用，路径按参数给）：
    python .github/scripts/package_win_artifacts.py --root b2bcollect_build
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

APP_DIR_NAME = "B2bCollectTool"
EXE_NAME = "B2bCollectTool.exe"
SETUP_NAME = "B2bCollectToolSetup.exe"
PORTABLE_NAME = "B2bCollectTool_Win_portable.zip"
GUIDE_DEST = "使用说明.txt"          # 用户看到的名字（源文件用 ASCII 名，见下）
GUIDE_SRC = "installer/usage_guide.txt"
NSI_PATH = "installer/b2bcollect_setup.nsi"
# onedir 的运行时目录名。**只许有一处定义**：装配校验按它找文件、安装脚本按它清目录，
# 写成两个字面量就等于「PyInstaller 改了布局名而我们的判据还盯着旧名字」——那正是
# 第 13 条要防的「两处各抄一份」，只不过这次抄的是目录名。
INTERNAL_DIR = "_internal"
BOM = b"\xef\xbb\xbf"

# 程序**自己会在安装目录里造**的那几个运行期目录。它们绝不能进交付物：
# 产物自检门禁（--acl-doctor）刚在 runner 上跑过一轮，这些目录里装着的是
# 「那台 CI 机器」的路径、任务状态与缓存。把它们发给用户，轻则界面上冒出一条
# 与本机无关的历史任务，重则带着一份别人机器的自检报告被当成自己机器的结论。
# 同一条纪律由两处执行：这里在装配前删掉（安装器与 zip 共用同一棵 dist 树，
# 删一次两边都干净），walker 里再兜一层（有人手工跑这一步时也不会漏）。
# 名单里的 aiqicha_data 今天**还没有人生成它**（爱企查与风鸟共用 riskbird_data）：
# 它是留着的那道闸——哪天真把两路的缓存拆开，拆开的那一路会自动被剔出去，不用
# 再回来改这里。ali1688_data 同属这道闸：1688 通道在包内一律走 b2bcollect_data
# （见 site_adapters.Ali1688Site._stable_data_dir），只有脱离本工具单跑原 1688 爬
# 虫时才会出现这个名字，留着它是为了防止哪天它在安装目录里复活又被一起发出去。
# 使用说明里则只写真实存在的那三个目录，不跟着这份名单照念。
RUNTIME_DIRS = ("b2bcollect_state", "b2bcollect_data", "riskbird_data",
                "aiqicha_data", "ali1688_data")

# onedir 产物里必须存在的项，按「_internal 里的相对路径」写。
# 每一项都对应一次真实事故，没有一项是凑数：
REQUIRED_INTERNAL = {
    "base_library.zip": "onedir 的铁证：onefile 时代它就是被 _MEI 回收 race 掉的那个文件"
                        "（缺它 = 标准库没冻结进来，启动即崩）",
    "config.json": "随包凭证（缺它 = 外部机器一律精简版）",
    "mk_phone_font.json": "马可波罗图片码字形字典（缺它 = 手机号整列空白）",
    "riskbird_build/runtime_paths.py": "三个采集器的可写落点口径（缺它 = 开机即崩）",
    "riskbird_build/riskbird_crawler_v2.py": "风鸟名单通道（缺它 = 静默少一个平台）",
    "riskbird_build/aiqicha_crawler_v2.py": "爱企查名单通道（缺它 = 静默少一个平台）",
    "riskbird_build/qizhidao_crawler.py": "企知道名单通道（缺它 = 静默少一个平台）",
    # 下面两项与三个采集器同源同批：它们是 v2 采集器**运行时按路径 import** 的模块
    # （site_adapters.EXTERNAL_MODULES 已登记，构建前硬门第 1 条也盯着 --add-data），
    # 但装配清单此前抄漏了它们——「清单三处一致」验的是打包，这里验的是出包之后。
    "riskbird_build/block_guard.py": "名单通道的域名与频控护栏（缺它 = 采集链路整段裸奔）",
    "riskbird_build/chrome_driver.py": "本地 chromedriver 定位（缺它 = 浏览器通道起不来）",
    "ali1688_build/ali1688_crawler.py": "1688 关键词通道（缺它 = 静默少一个平台）",
    # CI 现场下载的驱动目录（见 fetch_chromedrivers.py）。它不在 EXTERNAL_MODULES 里，
    # 所以第 1 条硬门管不到它，只有这里能管：漏了它，1688 与风鸟的浏览器通道在用户机器
    # 上只能退回 Selenium Manager 现下载——公司网/无网机器上表现为「点了没反应」。
    "drivers": "内置最近三个大版本的 chromedriver（缺它 = 浏览器通道看运气）",
    "enf_clean": "清洗子包源码目录（缺它 = 清洗链路降级）",
}


def is_dir_entry(rel: str) -> bool:
    """清单里这一项是**目录**还是单个文件：最后一段没有扩展名分隔点就是目录。

    判据不另立一张表：目录型条目（drivers / enf_clean）本来就只能按「目录里有文件」
    来验，写第二张登记表迟早和 REQUIRED_INTERNAL 对不上——那正是本项目被咬过的形状。
    """
    return "." not in rel.rstrip("/").split("/")[-1]


# REQUIRED_INTERNAL 里**不由任何 --add-data 产生**的那几项：构建系统自己写进 _internal
# 的。每一项都必须写清理由——打包清单硬门第 13 条拿它当放行名单（见
# check_packaging_manifest.check_win_payload_manifest），所以「这张表里多一项」这件事
# 本身就是一次改动，而不是一个没人注意的洞。
BY_BUILDER = {
    "base_library.zip": "PyInstaller 自动冻结的标准库归档，没有哪条 --add-data 产它"
                        "（onedir 的铁证，也是本轮那个 FileNotFoundError 的主角）",
}


def _fail(lines: list, msg: str) -> None:
    lines.append(msg)
    print(f"❌ {msg}")


def check_layout(root: Path, problems: list) -> tuple:
    """验 onedir 目录布局，返回 (exe 路径, _internal 路径)；不合格时元素为 None。"""
    dist = root / "dist"
    app = dist / APP_DIR_NAME
    if not app.is_dir():
        _fail(problems, f"没找到 onedir 产物目录：{app}"
                        "（PyInstaller 的 --onefile 又回来了？onedir 才会产出这个目录）")
        return None, None
    exe = app / EXE_NAME
    if not exe.is_file():
        _fail(problems, f"产物目录里没有 {EXE_NAME}：{exe}")
        return None, None
    internal = app / INTERNAL_DIR
    if not internal.is_dir():
        _fail(problems, f"产物目录里没有 {INTERNAL_DIR}：{internal}"
                        "（onedir 的运行时就在这一层，缺它等于产物只剩一个空壳 exe）")
        return exe, None
    # onefile 残留是最危险的形状：制品里同时躺着单文件 exe 与目录版，用户随手双击
    # 哪个都可能，而那个单文件 exe 会**原样复发**本轮修好的两个故障。
    stale = dist / EXE_NAME
    if stale.is_file():
        _fail(problems, f"dist 里还有一个旧的单文件 {EXE_NAME}：{stale}"
                        "（它会跟着一起进制品；用户双击它就走回 onefile 那条老链路，"
                        "「重启报 base_library.zip 找不到」会原样复发）")
    for rel, why in REQUIRED_INTERNAL.items():
        target = internal / rel
        if is_dir_entry(rel):
            # 目录型条目要求**里面有文件**：drivers/ 空着 = 一个 chromedriver 都没下载
            # 成功（fetch_chromedrivers.py 自己的上限判据只数它下载了几次，数得着 zip
            # 解压失败这种「目录建出来了但没落文件」的半途失败），空目录与缺目录在
            # 用户机器上的症状一模一样——浏览器通道点了没反应，而产物自检照样绿。
            if not target.is_dir() or not any(target.iterdir()):
                _fail(problems, f"_internal 缺 {rel}：{why}"
                                f"（目录型条目空着也算缺：{target}）")
        elif not target.is_file():
            _fail(problems, f"_internal 缺 {rel}：{why}")
    return exe, internal


def check_bom(path: Path, label: str, problems: list) -> None:
    """UTF-8 with BOM 必须成文可验：没有 BOM 的中文脚本会在英文 runner 上「构建成功」。"""
    if not path.is_file():
        _fail(problems, f"{label} 不存在：{path}")
        return
    head = path.read_bytes()[:3]
    if head != BOM:
        _fail(problems, f"{label} 不是 UTF-8 with BOM（头三字节 {head!r}）："
                        "makensis / 记事本会按系统代码页解释这份文件，"
                        "英文 Windows 上中文全部变乱码，而构建照样报成功")


# ── 卸载脚本的结构判据（正则在下面几条常量里，判定逻辑全在函数内）──────────
# NSIS 的注释行（整行以 ; 或 # 开头）。必须剔掉再判：解释这个 bug 的那行注释里
# 就写着旧的错误写法，不剔掉的话「说明为什么不能这样写」自己被当成「这样写了」。
NSIS_COMMENT = re.compile(r"^\s*[;#]")
NSIS_SECTION_END = re.compile(r"^\s*SectionEnd\b")
# 卸载段里唯一那条「问用户要不要连数据一起删」的问句：IDNO 跳到保留分支的标号。
NSIS_ASK = re.compile(r'MessageBox\s+(?P<flags>[^\s"]+)\s+"(?P<msg>[^"]*)"'
                      r'\s+IDNO\s+(?P<label>\w+)')
NSIS_LABEL = re.compile(r"^\s*(?P<label>\w+):\s*$")
NSIS_GOTO = re.compile(r"^\s*Goto\s+(?P<target>\w+)")
# 递归删除：/r 会把整棵树连带里面的用户数据一起带走，只允许出现在「全删」分支。
NSIS_RECURSIVE = re.compile(r'(?:RMDir|Delete)\s+/r\s+"(?P<path>[^"]+)"')
NSIS_DETAILPRINT = re.compile(r"^\s*DetailPrint\b")

# ── 安装段「拷完逐条验」的结构判据（下面这几条常量就是这个形状）───────────
#     StrCpy $7 "_internal\config.json"
#     IfFileExists "$INSTDIR\_internal\config.json" 0 copy_missing
#     ……最后一条校验之后…… Goto copy_ok / copy_missing: … Abort … / copy_ok:
NSIS_SECTION_ANY = re.compile(r'^\s*Section\b(?P<rest>[^\n]*)$')
NSIS_SECTION_TITLE = re.compile(r'"(?P<title>[^"]*)"')
NSIS_IF_EXISTS = re.compile(r'^\s*IfFileExists\s+"(?P<path>[^"]*)"'
                            r'\s+(?P<yes>\S+)\s+(?P<no>\w+)\s*$')
NSIS_STRCPY = re.compile(r'^\s*StrCpy\s+\$\d+\s+"(?P<msg>[^"]*)"\s*$')
NSIS_ABORT = re.compile(r"^\s*Abort\b")
NSIS_GOTO_PLAIN = re.compile(r"^\s*Goto\s+(?P<target>\w+)")
# 会改变执行顺序的两句：Goto 是无条件的，IfSilent 只在**静默**安装/卸载时跳。
# 分支归属必须两句都顺着走，否则会把「弹窗 → IfSilent → 下一个标号里才 Abort」误判成
# 「失败分支没有 Abort」。
NSIS_JUMP_ANY = re.compile(r"^\s*(?:Goto|IfSilent)\s+(?P<target>\w+)")
# 拷贝动作本身（FileOpen 不算：它是探测占用，不往安装目录写文件）
NSIS_FILE_COPY = re.compile(r"^\s*File\s")
# 把安装包「登记成已安装」的那几句：校验必须排在它们之前，否则装不全的包会在
# 控制面板里留下一个点开就跑不起来的条目，比什么都不留更难收拾。
NSIS_ENTRY_WRITE = re.compile(r"^\s*(?:CreateShortCut|WriteRegStr|WriteRegDWORD|WriteUninstaller)\b")
NSIS_DEFINE = re.compile(r'!define\s+(?P<name>\w+)\s+"(?P<val>[^"]*)"')
NSIS_MACRO_REF = re.compile(r"\$\{(\w+)\}")
# 失败分支里的引号串（MessageBox / StrCpy / DetailPrint / Abort 的文案都靠它捞出来）
NSIS_QUOTED = re.compile(r'"([^"]*)"')
# 中止安装的那一刻，用户手里只剩这个弹窗：只说「装失败了」等于让他猜下一步。
NSIS_NEXT_STEP = re.compile(r"重新|重试")
# 行尾续行：makensis 把续行后的引号串当成**额外的参数**而不是同一段文本，
# 只有真编译才看得见（本机没有 makensis，所以这条纪律也交给判据）。
NSIS_CONTINUATION = re.compile(r"\\\s*$")
NSI_SEP = "\\"


def _nsis_label_of(line: str):
    """这一行如果是标号（``keep_data:``）就返回它的名字，否则返回 None。"""
    m = NSIS_LABEL.match(line)
    return m.group("label") if m else None


def _nsis_section_bodies(lines: list) -> list:
    """切出所有顶层 Section：[(标题, 正文下标列表, 起始行号)]。

    卸载段与安装段共用这一把切法——两段各写一遍「往下找到 SectionEnd」迟早一份漂
    一份（本项目已经在「三处入口各抄一份 --add-data」上栽过整整一节）。
    """
    out = []
    i = 0
    while i < len(lines):
        m = NSIS_SECTION_ANY.match(lines[i])
        if not m:
            i += 1
            continue
        t = NSIS_SECTION_TITLE.search(m.group("rest") or "")
        body = []
        for j in range(i + 1, len(lines)):
            if NSIS_SECTION_END.match(lines[j]):
                break
            body.append(j)
        out.append((t.group("title") if t else "", body, i + 1))
        i = body[-1] + 1 if body else i + 1
    return out


def nsi_payload_checks() -> tuple:
    """安装段必须逐条验的那批路径：``(相对 $INSTDIR 的 NSIS 写法, 一句为什么)``。

    这份清单**不是手抄的第二遍**，它就是 REQUIRED_INTERNAL 加上安装目录根上的两项
    （主程序 exe 与使用说明）。目录型条目只能验「目录里有文件」，所以尾巴带 ``*.*``。
    """
    out = [(EXE_NAME, "主程序本体（缺它 = 装完点不开）")]
    for rel, why in REQUIRED_INTERNAL.items():
        path = NSI_SEP.join([INTERNAL_DIR] + rel.rstrip("/").split("/"))
        if is_dir_entry(rel):
            path += NSI_SEP + "*.*"
        out.append((path, why))
    out.append((GUIDE_DEST, "安装目录根上的使用说明（完成页那个勾选框指着的就是它）"))
    return tuple(out)


def _payload_item(path: str) -> str:
    """校验路径里用来点名文件的那一段：目录型取目录名，文件型取文件名。"""
    parts = path.split(NSI_SEP)
    return parts[-2] if parts[-1] == "*.*" else parts[-1]


def _nsi_expander(text: str):
    """把 ${MACRO} 换成 !define 里的值：判据比的是**展开后**的字节，不是宏名。

    不展开会得到两种坏结果：把 ``"$INSTDIR\\${APP_EXE}"`` 判成「校验了清单里没有的路径」
    （假红），或者反过来要求 .nsi 把文件名硬写一遍而绕开 !define（真漂移）。
    """
    defs = {m.group("name"): m.group("val") for m in NSIS_DEFINE.finditer(text)}

    def expand(value: str) -> str:
        for _ in range(3):                      # 允许 !define 互相引用，够了
            new = NSIS_MACRO_REF.sub(
                lambda m: defs.get(m.group(1), m.group(0)), value)
            if new == value:
                break
            value = new
        return value
    return expand


def check_installer_payload(text: str, problems: list) -> None:
    """安装段必须逐条验「关键文件真的落进了安装目录」，验不过就在登记之前 Abort。

    这一类缺陷的两种失败都不报错，而且都只在用户机器上看得见：
      1) ``SetOverwrite try`` 遇到被占用的目标是「跳过，不算失败」——装出来的是新 exe
         配旧运行时这种**拼起来的**版本，安装向导却照样走到「完成」；
      2) makensis 的 ``File /r`` 是「匹配到几份就打几份」：dist 布局改了名、或装配那步
         少放一样，安装包照样编得出来、装得上、跑得起来，只少那一路功能。
    CI 上 ``check_layout`` 验的是 **dist 树**，从来没有一处验过「makensis 把这些文件放
    进了用户机器」——补的就是这一格。判据只看结构，七种漂移都必须在编译前响：清单里有
    而脚本没验、脚本验了而清单里没有、同一项验两遍、校验排在快捷方式/注册表之后、
    失败分支没有 Abort 或忘了 Goto 跳过它（最后一种最坏：每次都装失败）、失败分支把
    用户留在「然后呢？」那里（没有下一步动作的中止文案）、运行时目录没先清或清错了对象
    （升级后新旧混着的一棵树，只在装过旧版的机器上现形）。
    """
    lines = text.splitlines()

    # 0) 行尾续行：先判这一条，因为它坏的是「文案只剩第一句」这种没人能从产物看出来的形状
    for ln, raw in enumerate(lines, 1):
        if not NSIS_COMMENT.match(raw) and NSIS_CONTINUATION.search(raw):
            _fail(problems, f"第 {ln} 行用反斜杠续行：makensis 把续行后的引号串当成**额外的"
                            "参数**而不是同一段文本，长文案请逐句 StrCpy 拼进寄存器再一次性用")

    sections = _nsis_section_bodies(lines)
    install = [(t, b, s) for t, b, s in sections if t != "Uninstall"]
    if not install:
        _fail(problems, '安装脚本里除了 Section "Uninstall" 没有安装段：那批关键文件'
                        "根本没有谁往安装目录里放，安装包不能发")
        return
    if len(install) > 1:
        _fail(problems, f"卸载段之外还有 {len(install)} 个 Section（标题 "
                        f"{[t for t, _b, _s in install]}）：关键文件校验只能有一个归属，"
                        "分段之后这条门数不出校验条数，也就等于没在判")
        return
    _title, body, start = install[0]
    code = [k for k in body if not NSIS_COMMENT.match(lines[k])]
    pos = {k: n for n, k in enumerate(code)}
    expand = _nsi_expander(text)

    # 标号 → (所在行, 这一段到下一个标号为止的代码)。带 Abort 的那一段就是「校验失败分支」。
    labels = [(k, _nsis_label_of(lines[k])) for k in code]
    labels = [(k, n) for k, n in labels if n]
    blocks = {}
    for idx, (k, name) in enumerate(labels):
        end = labels[idx + 1][0] if idx + 1 < len(labels) else body[-1] + 1
        blocks[name] = (k, [j for j in code if k < j < end])
    abort_labels = {n for n, (_k, blk) in blocks.items()
                    if any(NSIS_ABORT.match(lines[j]) for j in blk)}

    checks = []                       # (行号, 展开后的路径, 假分支标号)
    for k in code:
        m = NSIS_IF_EXISTS.match(lines[k])
        if not m or m.group("yes") != "0":
            continue                  # 真分支不是「落到下一行」，就不是这个形状（占用探测那一类）
        checks.append((k, expand(m.group("path")), m.group("no")))
    if not checks:
        _fail(problems, f"安装段（第 {start} 行起）一条「拷完验一遍」的 IfFileExists 都没有："
                        f"{len(nsi_payload_checks())} 项关键文件里少任何一项，安装包照样出、"
                        "照样报成功，用户那边只是静默少一路功能")
        return
    if not abort_labels:
        _fail(problems, f"安装段没有任何带 Abort 的失败分支：文件没落地时下面那些校验"
                        "只能打一行 DetailPrint，安装照样走完并登记进控制面板")
        return

    # 失败分支的归属要按 NSIS 的**执行顺序**算，不能只看被跳进去的那一段：本项目的写法
    # 是「弹窗 → IfSilent 跳过弹窗 → 下一个标号里才 Abort」，只认显式跳转的判据会把这种
    # 正确形状误报成「失败分支没有 Abort」（而误报的门会被当噪音关掉，比没有门更糟）。
    abort_targets = {t for _k, _p, t in checks if t in blocks}
    ordered = [n for _k, n in labels]                # 标号按源码顺序

    def branch_labels(head: str) -> set:
        """从失败标号 head 出发、按执行顺序真会走到的所有标号（防环）。

        两条边：块里出现的 ``Goto``/``IfSilent`` 跳转；以及「这一段没有 Abort、末尾也不
        是 unconditional Goto」时的**顺序落空**——NSIS 会继续往下一个标号那一段跑。
        """
        seen, queue = set(), [head]
        while queue:
            n = queue.pop()
            if n in seen or n not in blocks:
                continue
            seen.add(n)
            _k, blk = blocks[n]
            for j in blk:
                m = NSIS_JUMP_ANY.match(lines[j])
                if m:
                    queue.append(m.group("target"))
            ends_jump = bool(blk and NSIS_GOTO_PLAIN.match(lines[blk[-1]]))
            if not any(NSIS_ABORT.match(lines[j]) for j in blk) and not ends_jump:
                i = ordered.index(n) + 1
                if i < len(ordered):
                    queue.append(ordered[i])
        return seen

    # 校验全过时走的那条路必须**跳开**失败分支：先找出那个成功标号。
    last_check = max(k for k, _p, _t in checks)
    cut = min(blocks[t][0] for t in abort_targets) if abort_targets else None
    success = None
    if cut is not None and cut > last_check:
        skip = [m.group("target") for k in code if last_check < k < cut
                for m in [NSIS_GOTO_PLAIN.match(lines[k])] if m]
        success = next((t for t in skip if t in blocks and t not in abort_targets), None)

    grouped = {}
    used_abort = set()                # 校验真的跳进去、且这条路确实会中止的那些标号
    gone_labels = {}                  # 标号 → 跳到它的行号（标号在安装段里没有）
    weak_labels = {}                  # 标号 → 跳到它的行号（一路走到黑也没有 Abort）
    mixed_labels = {}                 # 标号 → 跳到它的行号（绕进成功分支去了）
    for k, path, target in checks:
        if target not in blocks:
            gone_labels.setdefault(target, []).append(k)
            continue
        reach = branch_labels(target)
        if success and success in reach:
            mixed_labels.setdefault(target, []).append(k)
            continue
        if not any(NSIS_ABORT.match(lines[j]) for n in reach for j in blocks[n][1]):
            weak_labels.setdefault(target, []).append(k)
            continue
        grouped.setdefault(path, []).append(k)
        used_abort.add(target)
    # 这三种漏法都是**成批**出现的（同一段失败分支被所有校验共用），所以按标号各报一句
    # 而不是每条校验报一句：十四行同样的话会把真正的那一条淹没掉。
    for t, ks in sorted(gone_labels.items()):
        _fail(problems, f"第 {ks[0] + 1} 行起有 {len(ks)} 条校验的假分支跳进标号 {t}，"
                        "安装段里没有这个标号（makensis 会报 unknown label，安装包编不出来）")
    for t, ks in sorted(weak_labels.items()):
        _fail(problems, f"第 {ks[0] + 1} 行起有 {len(ks)} 条校验没落地时跳进标号 {t}，"
                        "沿着这条路一路走下去都没有 Abort：只弹个窗或打一行日志就继续装完，"
                        "正是「装了一半却报成功」")
    for t, ks in sorted(mixed_labels.items()):
        _fail(problems, f"第 {ks[0] + 1} 行起有 {len(ks)} 条校验跳进标号 {t}，"
                        f"可这条路最后掉进了成功标号 {success}：文件没落地也照样创建快捷方式、"
                        "照样登记进控制面板")
    if not grouped:
        _fail(problems, "安装段没有任何一条校验把失败分支接到 Abort 上：现在关键文件缺一整批"
                        "也照样装完、照样登记进控制面板（上面几条点名的就是缺的那一段）")
        return

    want = {}
    for rel_path, why in nsi_payload_checks():
        want["$INSTDIR" + NSI_SEP + rel_path] = why
    for path, why in want.items():
        ks = grouped.get(path)
        if not ks:
            _fail(problems, f"安装段没有校验 {path}：{why}。装配清单（REQUIRED_INTERNAL）里"
                            "有的东西，安装脚本必须逐条验它在安装目录里落了地")
            continue
        if len(ks) > 1:
            _fail(problems, f"{path} 被校验了 {len(ks)} 次（第 {[k + 1 for k in ks]} 行）："
                            "同一项验两遍说明有一段校验是从别处抄来的，清单改动时只会改到一处")
        prev = code[pos[ks[0]] - 1] if pos[ks[0]] else None
        m = NSIS_STRCPY.match(lines[prev]) if prev is not None else None
        item = _payload_item(path)
        if not m or item not in expand(m.group("msg")):
            _fail(problems, f"第 {ks[0] + 1} 行校验 {path}，前一句却不是一句点名「{item}」的 "
                            "StrCpy：失败弹窗要报的是**哪一个**文件没落地，"
                            "只说「装不全」等于让用户重装一次试试看")
    for path, ks in grouped.items():
        if path not in want:
            _fail(problems, f"安装段校验了装配清单里没有的路径 {path}（第 {ks[0] + 1} 行）："
                            "要么把它补进 REQUIRED_INTERNAL（那它也会进 dist 校验），"
                            "要么删掉这一行——两处各抄一份迟早对不上")

    # 失败分支的两条硬要求：走进去之后 ① 绝不能再写快捷方式/注册表（那正是「装不全却
    # 在控制面板里留一个点开就跑不起来的条目」），② 文案必须给出下一步动作。②不是文案
    # 洁癖：校验排在 File **之后**，走到这里时安装目录里可能已经躺着一批新文件，而安装
    # 向导已经中止——用户手里只剩这一个弹窗。只说「装不全」等于让他自己猜该做什么，实际
    # 结果就是反复双击安装包（而只要旧进程还在跑，SetOverwrite try 会一直跳过同一个文件，
    # 永远装不全）。
    for t in sorted(used_abort):
        reach = branch_labels(t)
        bad_entry = [j for n in reach for j in blocks[n][1]
                     if NSIS_ENTRY_WRITE.match(lines[j])]
        if bad_entry:
            _fail(problems, f"失败分支（标号 {t}，第 {bad_entry[0] + 1} 行）里还在写快捷方式/"
                            "注册表：走进这条路说明文件没装全，控制面板里留下一个点开就跑不"
                            "起来的条目，比什么都不留更难收拾")
        said = " ".join(txt for n in reach for j in blocks[n][1]
                        for txt in NSIS_QUOTED.findall(lines[j]))
        if not NSIS_NEXT_STEP.search(said):
            _fail(problems, f"失败分支（标号 {t}，第 {blocks[t][0] + 1} 行起）的文案没给出下一步动作"
                            "（要出现「重新」或「重试」）：安装在这里中止，用户只看得见这一段话")

    span = [k for ks in grouped.values() for k in ks]
    first, last = min(span), max(span)
    copies = [k for k in code if NSIS_FILE_COPY.match(lines[k])]
    late = [k for k in copies if k > first]
    if late:
        _fail(problems, f"第 {late[0] + 1} 行的 File 排在拷贝后校验之后：校验读的是**安装目录里"
                        "已有的文件**，排在它后面的那一行根本没被验过（要嘛挪到校验之前，"
                        "要嘛给它补一条清单与一条校验）")
    entries = [k for k in code if NSIS_ENTRY_WRITE.match(lines[k])]
    early = [k for k in entries if k < last]
    if early:
        _fail(problems, f"第 {early[0] + 1} 行的快捷方式/注册表写入排在校验之前：装不全的包"
                        "会在控制面板里留下一个点开就跑不起来的条目，"
                        "比什么都不留更难收拾（用户只会以为是程序坏了）")
    # 7) 升级时旧运行时必须先清掉。File /r 的语义是「同名覆盖、不同名保留」：上一版有、
    #    这一版改名或删掉的那几个模块会原样留在运行时目录里，装出来是一棵**新旧混着**的树。
    #    这一类比「少一个文件」更阴：本机怎么试都是好的（本机每次都是全新安装），只有升级过
    #    的机器上才会出现，而报出来的错和「压根没装对」一样难读——本轮修的那个
    #    base_library.zip 找不到，形状上正是「新旧两批文件互相咬不住」。
    #    递归删除的对象**只允许**是运行时目录：安装目录里还躺着用户这几个月的登录状态与
    #    采集数据（RUNTIME_DIRS 那三个），安装步骤没有资格删它们。卸载段那条「问一次、默认
    #    保留」的纪律在这里同样成立，只不过这里连问都不该问。
    runtime = NSI_SEP.join(["$INSTDIR", INTERNAL_DIR])
    cleans = [(k, m.group("path")) for k in code
              for m in [NSIS_RECURSIVE.search(lines[k])] if m]
    cleans = [(k, expand(p)) for k, p in cleans]
    mine = [(k, p) for k, p in cleans if p.lower() == runtime.lower()]
    if not mine:
        _fail(problems, f'安装段没有 RMDir /r "{runtime}"：File /r 只覆盖同名的那些，'
                        "上一版有而这一版改名或删掉的模块会原样留在运行时目录里，"
                        "升级得到的是新旧混着的一棵树（本机全新安装永远试不出来，"
                        "只有装过旧版的机器上才现形）")
    else:
        if len(mine) > 1:
            _fail(problems, f"运行时目录被清了 {len(mine)} 遍（第 {[k + 1 for k, _p in mine]} 行）："
                            "同一件事做两遍说明有一段是从别处抄来的，改动时只会改到一处")
        first_copy = min((k for k in code if NSIS_FILE_COPY.match(lines[k])), default=None)
        if first_copy is not None and mine[0][0] > first_copy:
            _fail(problems, f"第 {mine[0][0] + 1} 行的递归删除排在 File 之后：刚铺好的运行时又被删掉，"
                            "后面那段逐条校验会红在「文件没落地」上，而真原因是这一行的次序——"
                            "报错报在校验上，下个人就会去查 dist 树")
    for k, p in cleans:
        if p.lower() == runtime.lower():
            continue
        hit = [d for d in RUNTIME_DIRS
               if p.lower().startswith(NSI_SEP.join(["$INSTDIR", d]).lower())]
        if hit:
            _fail(problems, f'第 {k + 1} 行在安装段里递归删除 {p}：{hit[0]} 里是用户的登录状态、'
                            "缓存与采集进度，安装步骤没有资格删它（卸载段都要问一次，这里连问都没问）")
        elif p.lower() in ("$instdir", "$instdir" + NSI_SEP):
            _fail(problems, f'第 {k + 1} 行递归删除的是整个安装目录 {p}：那里面除了运行时还有'
                            "用户的数据目录，一次升级就把几个月的劳动成果一起带走；"
                            f"要清的只有运行时目录 {runtime}")
        else:
            _fail(problems, f'第 {k + 1} 行递归删除 {p}：安装段里唯一的递归删除对象是运行时目录 '
                            f"{runtime}，删到别处去的行为没有人核对过")

    if cut is not None and cut > last and success is None:
        # 失败分支排在校验之后（本项目就是这么写的）：校验全过的那条路必须**跳出去**，
        # 否则文件一个都不缺也会走进 Abort——那种「修法」比不校验更坏，因为它让每次
        # 安装都失败。刻意只看校验真的跳进去的那些标号：安装段里还带着一句「工具正在
        # 运行」的 Abort（abort_locked），拿它当分界会切出一段空区间，然后报一条指错方向的假红。
        _fail(problems, f"最后一条校验（第 {last + 1} 行）与失败分支（第 {cut + 1} 行）"
                        "之间没有 Goto 跳到成功分支：文件全都在位也会一路走进失败标号，"
                        "每一次安装都弹窗中止——这种修法比不校验更坏")


def _nsis_uninstall_body(lines: list) -> tuple:
    """返回 (卸载段正文下标列表, 起始行号)。找不到时返回 (None, 0)。"""
    for title, body, start in _nsis_section_bodies(lines):
        if title == "Uninstall":
            return body, start
    return None, 0


def check_installer_uninstall(text: str, problems: list) -> None:
    """卸载段的「保留数据」分支必须真的保留数据——这是本轮读脚本读出来的真缺陷。

    旧写法长这样（两条分支汇到同一句递归删除）：

        MessageBox MB_YESNO "……选「否」保留 b2bcollect_data / ……" IDNO keep_data
            RMDir /r "$INSTDIR\\b2bcollect_data"
            ……
        keep_data:
            RMDir /r "$INSTDIR"          ← 选「否」也走到这里，刚承诺保留的目录被整体删掉

    编译器不会报（语法完全合法），卸载不会报（退出码 0），DetailPrint 里那句
    「已保留……」也照样打印——它承诺的东西在它自己后面就被删掉了。用户只在下次
    重装时才发现这几个月白干，而那时没人会怀疑到卸载脚本。所以这一条必须在 makensis
    之前拦住：判据只看**分支归属**（一行代码在哪条分支里），不看它写得多像样。
    """
    lines = text.splitlines()
    body, start = _nsis_uninstall_body(lines)
    if body is None:
        _fail(problems, '安装脚本里没有 Section "Uninstall"：控制面板那句「卸载」'
                        "就没有卸载逻辑可执行，安装包不能发")
        return
    code = [k for k in body if not NSIS_COMMENT.match(lines[k])]

    asks = [(k, NSIS_ASK.search(lines[k])) for k in code]
    asks = [(k, m) for k, m in asks if m]
    if not asks:
        _fail(problems, f"卸载段（第 {start} 行起）没有任何「要不要连登录状态和缓存一起删」"
                        "的问句：运行期数据是用户几个月的劳动成果，绝不允许静默删除，"
                        "必须先问、再按用户的选择分支")
        return
    if len(asks) > 1:
        _fail(problems, f"卸载段有 {len(asks)} 条带 IDNO 跳转的问句（第 "
                        f"{[k + 1 for k, _ in asks]} 行）：分支归属变得不确定，"
                        "一条问句配一条保留分支就够")
        return
    ask_k, ask = asks[0]
    if "MB_DEFBUTTON2" not in ask.group("flags"):
        _fail(problems, f"第 {ask_k + 1} 行那条问句没带 MB_DEFBUTTON2：NSIS 的 MessageBox "
                        "默认焦点在**第一个按钮**（=「是，连数据一起删」），一路回车下去就"
                        "把用户数据删了，与问句里那句「选『否』（推荐）」互相矛盾")
    label = ask.group("label")

    label_k = next((k for k in code if k > ask_k and _nsis_label_of(lines[k]) == label), None)
    if label_k is None:
        _fail(problems, f"第 {ask_k + 1} 行 IDNO 跳到标号 {label}，卸载段里没有这个标号 "
                        f"{label}（makensis 会报 unknown label，安装包编不出来）")
        return
    del_branch = [k for k in code if ask_k < k < label_k]        # 用户选「是」：连数据全删
    # 保留分支到下一个标号为止（后面是两条分支共用的收尾清理，不属于任何一条分支）
    keep_end = next((k for k in code if k > label_k and _nsis_label_of(lines[k])),
                    body[-1] + 1)
    keep_branch = [k for k in code if label_k < k < keep_end]

    for name in RUNTIME_DIRS:
        want = f'$INSTDIR\\{name}'
        if not any(m.group("path") == want for k in del_branch
                   for m in NSIS_RECURSIVE.finditer(lines[k])):
            _fail(problems, f"「连数据一起删」那条分支里没有 RMDir /r \"{want}\"："
                            "用户明确选了全删，磁盘上却会留下这个数据目录"
                            "（这种漏法没有症状，只有下次重装时才被发现）")

    if not any(m.group("path") == "$INSTDIR" for k in del_branch
               for m in NSIS_RECURSIVE.finditer(lines[k])):
        _fail(problems, '「连数据一起删」那条分支最后没有 RMDir /r "$INSTDIR"：'
                        "程序文件已删净但空目录留着，下次安装还会撞在那个残骸上")
    gotos = [m for m in (NSIS_GOTO.search(lines[k]) for k in del_branch) if m]
    if not gotos:
        _fail(problems, "「连数据一起删」那条分支末尾没有 Goto：它会直接落进保留分支，"
                        "两条分支的执行内容变成同一段，用户在问句里选了什么都不算数")
    elif len(gotos) > 1:
        _fail(problems, f"全删分支里有 {len(gotos)} 条 Goto：分支归属不再唯一，"
                        "收尾只留一条跳转就够了")
    else:
        target = gotos[0].group("target")
        # 在**整段**里找这个标号，而不是只在 label 之后找：`Goto keep_data` 那种往回跳
        # 在 NSIS 里语法完全合法（makensis 不会报），但它的语义正是「跳回保留分支内部」
        # ——把全删分支又接回它刚跳开的那一段。只往后找会把它当成「标号不存在」，
        # 报一条让人去补标号的错词，根因就被指错了。
        tk = next((k for k in body if _nsis_label_of(lines[k]) == target), None)
        if tk is None:
            _fail(problems, f"全删分支 Goto 的标号 {target} 在卸载段里不存在"
                            "（makensis 会报 unknown label，安装包编不出来）")
        elif tk < keep_end:
            _fail(problems, f"全删分支 Goto 到第 {tk + 1} 行的 {target}：跳转目标必须落在两条"
                            "分支**之外**的收尾处，落进保留分支（或往回跳进它）等于又绕回删除逻辑")

    for k in keep_branch:
        for m in NSIS_RECURSIVE.finditer(lines[k]):
            if m.group("path").startswith("$INSTDIR"):
                _fail(problems, f"第 {k + 1} 行在「保留数据」分支里递归删除 {m.group('path')}"
                                "：用户选了「否」，这句会把上面刚承诺保留的登录状态、"
                                "缓存与采集进度一起带走（本轮实测到的正是这一条）")
    if not any(NSIS_DETAILPRINT.search(lines[k]) and "$INSTDIR" in lines[k]
               for k in keep_branch):
        _fail(problems, "「保留数据」分支没有 DetailPrint 打印 $INSTDIR：卸载日志里必须"
                        "写明数据留在哪，否则用户找日志只能靠猜，界面这句话也无人可对")


def prune_runtime(app: Path, problems: list) -> int:
    """删掉 dist 里由产物自检**运行出来**的那几个数据目录，返回删掉的个数。"""
    removed = 0
    for name in RUNTIME_DIRS:
        d = app / name
        if not d.is_dir():
            continue
        try:
            shutil.rmtree(d)
            removed += 1
        except Exception as e:
            _fail(problems, f"运行期目录删不掉：{d}（{type(e).__name__}: {e}）"
                            "——带着 CI 机器状态的安装包不能发")
    if removed:
        print(f"✅ 已剔除 {removed} 个运行期目录（产物自检在 runner 上跑出来的，不该进交付物）")
    return removed


def build_portable_zip(root: Path, app: Path, guide: Path, out: Path, problems: list) -> None:
    """绿色版 zip：顶层一个目录 + 根上一份使用说明。"""
    if app is None or not app.is_dir():
        _fail(problems, "没有 onedir 目录，绿色版 zip 无从生成")
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    n = 0
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for dirpath, dirnames, filenames in os.walk(app):
            base = Path(dirpath)
            # 原地剔掉运行期目录（见 RUNTIME_DIRS）：即便这一步之前没执行 prune，
            # 也不许把别人机器的登录状态和任务历史打进用户拿到的 zip。
            dirnames[:] = [d for d in dirnames
                           if not (base == app and d in RUNTIME_DIRS)]
            for name in sorted(filenames):
                full = base / name
                arc = f"{APP_DIR_NAME}/" + full.relative_to(app).as_posix()
                z.write(full, arc)
                n += 1
            # 空目录也要留痕：PyInstaller 会建一些空目录，丢了会让运行时少一层
            if not filenames and not dirnames and base != app:
                z.writestr(zipfile.ZipInfo(
                    f"{APP_DIR_NAME}/" + base.relative_to(app).as_posix() + "/"), "")
        if guide.is_file():
            z.write(guide, GUIDE_DEST)      # zip 根上的使用说明，用户解压第一眼就看到
    size_mb = out.stat().st_size / 1048576.0
    print(f"✅ 绿色版：{out.name}（{n} 个文件 + 1 份使用说明，{size_mb:.1f} MB）"
          f"  顶层＝{APP_DIR_NAME}/ + {GUIDE_DEST}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Windows 交付物装配与校验（onedir + 绿色版 zip）")
    ap.add_argument("--root", default="b2bcollect_build",
                    help="b2bcollect_build 目录（默认相对当前目录）")
    ap.add_argument("--skip-zip", action="store_true", help="只校验，不生成 zip")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"❌ 找不到 b2bcollect_build 目录：{root}")
        return 1

    problems: list = []
    exe, internal = check_layout(root, problems)
    check_bom(root / NSI_PATH, "NSIS 安装脚本", problems)
    guide = root / GUIDE_SRC
    check_bom(guide, "使用说明", problems)

    # 卸载分支与安装校验的结构性检查紧随编码检查之后：这一步排在 makensis 之前（见
    # workflow 「Package Windows artifacts」），所以「保留数据那一支其实会删数据」「少打
    # 一个文件却报安装成功」这类缺陷到不了编译，更到不了用户机器。文件本身缺席时不重复
    # 报（上面 check_bom 已经点了名）。
    nsi_path = root / NSI_PATH
    if nsi_path.is_file():
        nsi_text = nsi_path.read_text(encoding="utf-8-sig")
        check_installer_uninstall(nsi_text, problems)
        check_installer_payload(nsi_text, problems)

    # 校验不过就到此为止：绝不在「产物本身有问题」之后还继续生成 zip——那种情况下
    # 生成出来的正是一个「CI 全绿、用户拿到坏包」的包（本轮两个根因共同的样子）。
    if problems:
        print(f"\n❌ Windows 交付物校验未通过（{len(problems)} 条）：")
        for p in problems:
            print(f"   · {p}")
        return 1

    # 装配前先把运行期目录剔干净：安装器与绿色版 zip 共用同一棵 dist 树，
    # 这一步跑完 dist 就是「干净的可交付布局」，NSIS 的 File /r 才不会把
    # runner 上 --acl-doctor 跑出来的状态目录一起装进用户机器。
    app = root / "dist" / APP_DIR_NAME
    if app.is_dir():
        prune_runtime(app, problems)

    if not args.skip_zip:
        build_portable_zip(root, app, guide, root / PORTABLE_NAME, problems)

    setup = root / SETUP_NAME
    if setup.is_file():
        print(f"✅ 安装包：{setup.name}（{setup.stat().st_size / 1048576.0:.1f} MB）")
    else:
        # 这一条**不**计入 problems：安装包由下一步的 makensis 生成，本脚本先于它跑。
        print(f"ℹ 还没看到 {SETUP_NAME}（makensis 那一步还没跑，正常）")

    if problems:
        print(f"\n❌ Windows 交付物装配未通过（{len(problems)} 条）：")
        for p in problems:
            print(f"   · {p}")
        return 1
    print(f"\n✅ Windows 交付物校验通过：exe={exe}  运行时={internal}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
