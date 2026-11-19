#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Win7 兼容通道的双解释器体检：进包的那一个是不是 3.8、跑门禁的那一个是不是 3.11，
以及进包运行时到底装到了什么（Qt 必须是 5）。

两种用法对应流水里的两步：--pin 在构建最开头查分工，--inventory 在装完依赖后对版。

需求 10B 里**唯一**能回答「这个包到底是不是 3.8 打的」的一格。为什么只有这里判得掉：
CPython 官方二进制从 3.9 起不再支持 Windows 7，所以「降到 3.8」是这条通道存在的全部理由；
而 `actions/setup-python` 拿不到指定版本时会**退而求其次**装一个别的版本，构建照样一路绿——
PE 地板、窗口冒烟、自报对拍看的都是产物自己，3.9 与 3.8 打出来的东西在这些格子上一模一样
（子系统版本都是 6.00；`api-ms-win-core-path` 是运行期延迟加载，不在导入表里）。也就是说
这一条根据一旦静默失效，除了直接问解释器自己，没有任何下游判据会响。

为什么不写在 workflow 的 `python -c "..."` 里（本脚本第一版就是那么写的）：
第 34 条（语法地板）只看 `.py`，workflow 里的内联 python 是它的**盲区**——它自己的红话就写着
「本条款只看 .py，这一处它看不到」。提成一份脚本之后：①这段逻辑真的进了判据视野；②四档结论
都能被离线夹具喂假数据验（见 ``evaluate``），不必真有两台解释器才能测一把门。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

# 控制台编码必须由脚本自己写定，不能交给环境代码页（与 .github/scripts 里另外几份同源）：
# 本脚本每一行结论都带中文，而它跑在云端 Windows——英文镜像是 cp1252，写出的当下就抛
# UnicodeEncodeError，「双解释器分工没成立」这句唯一的证据会被那条异常盖掉。顺序也有门
# （第 41 条），所以这一句必须在**第一处中文之前**。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 目标解释器必须是这一档：Windows 7 上还能用的最后一个 CPython 官方二进制。
TARGET_FLOOR = (3, 8)
# 工具链解释器的下限（不是精确值）：构建前硬门用了 3.9+ 才有的 `ast.unparse`，
# 低于这一档时那道门会炸成一句 SyntaxError，听起来像仓库坏了而不是解释器不对。
TOOL_MIN = (3, 11)

# 问另一个解释器「你是谁」的那段源码。它是**本文件里的一个常量**，所以第 34 条看得见它；
# 三档信息一次问全：版本、位数、以及它自己看到的可执行文件路径（用于确认没指错人）。
# 写法保持在 3.8 也能解析——它会被目标解释器执行。
PROBE_SRC = (
    "import json,sys;"
    "print(json.dumps({'version': list(sys.version_info[:3]),"
    " 'bits': 64 if sys.maxsize > 2**32 else 32,"
    " 'executable': sys.executable,"
    " 'banner': sys.version.splitlines()[0]}))"
)


def probe(exe: str) -> dict:
    """问一个解释器自己是谁。拿不到答案就是**红**，不许当「这格没问题」放过。"""
    return _run_probe(exe, PROBE_SRC)


def _run_probe(exe: str, src: str) -> dict:
    """在另一个解释器里跑一段源码、拿回它打的 JSON。失败一律折成 `{"error": …}`。

    为什么用 `-c` 而不是临时文件：临时文件要选落点（老系统上 TEMP 不可写就是另一种红），
    而这段源码是本文件的常量——它跟着判据一起被审、一起被测，不会与判断分离到两个地方。
    """
    try:
        # stdin 关掉：本脚本被 bash 调用，继承来的 stdin 若被子进程吃掉，父子会互相等
        # （交付链里独立起 python 的既有纪律：要么在管道右侧，要么自带 < /dev/null）。
        out = subprocess.run([exe, "-c", src], capture_output=True, text=True,
                             encoding="utf-8", errors="replace",
                             timeout=120, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    if out.returncode != 0:
        return {"error": f"退出码 {out.returncode}：{(out.stderr or out.stdout).strip()[:200]}"}
    try:
        return json.loads(out.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        return {"error": f"它答的话读不懂（{type(exc).__name__}）：{out.stdout[:120]!r}"}


# ── 第二件事：问目标环境「你装到了什么」，并把 Qt 主版本这一格判掉──────────────
# 本函数原来住在 workflow 的 `"$TARGET_PY" - <<'PYEOF'` heredoc 里。那一形状是第 34 条的
# **盲区**：它的正则要求字面量 `python` 打头，带引号的变量名它看不到，连「这一处它看不到」
# 那句自报都会漏——所以本轮顺手把那把尺子也补宽了（见 _INLINE_PY_PAT）。
# 只让目标环境**报数**、判定留在工具链这一侧（见 ``evaluate_inventory``）：判定住在纯函数里
# 才喂得进假数据，不必真装一份 3.8 + PyQt5 才能测这把门。
INVENTORY_MODULES = ("PyInstaller", "selenium", "requests", "openpyxl",
                     "phonenumbers", "PIL", "xlrd")
INVENTORY_SRC = (
    "import json, sys\n"
    "out = {'python': sys.version.split()[0], 'version': list(sys.version_info[:3])}\n"
    "for _m in " + repr(INVENTORY_MODULES) + ":\n"
    "    try:\n"
    "        out[_m] = str(getattr(__import__(_m), '__version__', '?'))\n"
    "    except Exception as _e:\n"
    "        out[_m] = 'IMPORT-FAILED ' + type(_e).__name__\n"
    "try:\n"
    "    from PyQt5.QtCore import QT_VERSION_STR, PYQT_VERSION_STR\n"
    "    out['Qt'] = QT_VERSION_STR\n"
    "    out['PyQt5'] = PYQT_VERSION_STR\n"
    "except Exception as _e:\n"
    "    out['Qt'] = 'IMPORT-FAILED ' + type(_e).__name__\n"
    "print(json.dumps(out))\n"
)


def evaluate_inventory(got: dict) -> list:
    """进包运行时的判定（纯函数）。返空 = 这一格过。

    Qt 主版本钉在 5 是**判定而不是记录**：Qt 6 官方支持的最低系统是 Windows 10，一条带着
    Qt6 的「Win7 兼容包」从名字到内容都是假的，而它**能在所有门禁里全绿**（runner 是
    Win11：窗口建得出、版本自报得上）。这种「只有目标系统能发现」的缺陷，能在工具链上
    判掉的就必须在这里判掉。
    """
    if "error" in got:
        return [f"目标环境答不出自己装了什么（{got['error']}）：装依赖那一步实际上没成功，"
                "而后面每一格都会拿一个缺东西的产物去测"]
    problems = []
    # 纵深防御：这一步自己再核一次「报回来的 Python 确实是 3.8」。它不是重复劳——
    # --pin 那一步被删/改名时，只有这里还会响；而拿 3.9 环境装出来的依赖去打 Win7 包，
    # 症状与「驱动不对」「权限不对」都长得不一样，只会拖到用户双击那一刻。
    tv = tuple(got.get("version") or ())[:2]
    if tv != TARGET_FLOOR:
        problems.append(f"装依赖的那个环境是 {'.'.join(str(x) for x in tv)}（{got.get('python')}），"
                        f"不是 {TARGET_FLOOR[0]}.{TARGET_FLOOR[1]}：PyInstaller 会把它的运行时带进包，"
                        "而 3.9+ 的官方二进制在 Windows 7 上连解释器都加载不起来")
    qt = str(got.get("Qt", ""))
    if not qt.split(".")[0].isdigit() or not qt.startswith("5."):
        problems.append(f"进包的是 Qt「{qt}」，不是 Qt 5：Qt 6 官方最低支持 Windows 10，"
                        "这一版不可能在 Win7 上跑起来，而门禁会全绿（runner 是 Win11）")
    missing = [m for m in INVENTORY_MODULES if str(got.get(m, "")).startswith("IMPORT-FAILED")]
    if missing:
        problems.append("目标环境里这些包 import 失败：" + " / ".join(missing)
                        + "——pip 按 Requires-Python 挑不到还能装的版本时不会报错，只会少装，"
                        "而症状是产物里缺一个模块（启动即 ModuleNotFoundError）")
    return problems


def inventory(target_py: str) -> list:
    """把清单打在日志里（将来问「那个 Win7 包带的哪版 selenium」只有这一格留了字），再跑判定。"""
    got = _run_probe(target_py, INVENTORY_SRC)
    if "error" not in got:
        print(f"· Python      ：{got.get('python')}")
        print(f"· Qt / PyQt5  ：{got.get('Qt')} / {got.get('PyQt5')}")
        for m in INVENTORY_MODULES:
            print(f"· {m:<12}：{got.get(m, '(没报)')}")
    else:
        print(f"· 目标环境答话失败：{got['error']}")
    return evaluate_inventory(got)


def evaluate(own: tuple, target: dict, tool: dict, same_path: bool) -> list:
    """四档判定，纯函数（不碰进程、不碰文件）——离线夹具喂假元组就能逐档验。

    返回问题列表；空列表 = 分工成立。每句话都要能直接当处方读：只说「不对」的红话，
    下一轮排查还是要从 HEAD 开始找。
    """
    problems = []
    # ① 本脚本自己跑在谁身上：这就是「PATH 上的裸 python 是不是工具链」那一格。
    #    共享步骤（清单门、装配、自报对拍）写的都是裸 `python ...`，它指向谁决定那些门
    #    是「真跑过」还是在 3.8 上崩给你看。
    if tuple(own) < TOOL_MIN:
        problems.append(
            f"跑本脚本的裸 python 是 {'.'.join(str(x) for x in own)}，低于工具链要求的 "
            f"{'.'.join(str(x) for x in TOOL_MIN)}：与主通道逐字节相同的那些步骤会跑在"
            "目标解释器上，构建前硬门里的 3.9+ 写法（ast.unparse）会当场炸")
    # ② 目标解释器：整条通道存在的全部理由。
    if "error" in target:
        problems.append(f"目标解释器问不出自己是谁（{target['error']}）：3.8 那一步"
                        "实际上没成功，后面的构建全都不算数")
    else:
        tv = tuple(target.get("version") or ())[:2]
        if tv != TARGET_FLOOR:
            problems.append(
                f"目标解释器是 {'.'.join(str(x) for x in tv)}，不是 {TARGET_FLOOR[0]}.{TARGET_FLOOR[1]}"
                f"（{target.get('banner')}）：CPython 官方二进制从 3.9 起不再支持 Windows 7，"
                "这一路打出来的就是主通道那个包，Win7 依旧打不开——而 PE 地板、窗口冒烟、"
                "自报对拍全都不会响（3.8 与 3.9 的产物在这些格子上长得一样）")
        if target.get("bits") != 64:
            problems.append(f"目标解释器是 {target.get('bits')} 位：随包 PyQt5 与 ChromeDriver "
                            "都是 win64，装进 32 位运行时是启动即崩")
    # ③ 工具链解释器：判据自己得站得住。
    if "error" in tool:
        problems.append(f"工具链解释器问不出自己是谁（{tool['error']}）：3.11 那一步没成功，"
                        "门禁会跑在错误的解释器上")
    elif tuple(tool.get("version") or ())[:2] < TOOL_MIN:
        tv = tuple(tool.get("version") or ())[:2]
        problems.append(
            f"工具链解释器是 {'.'.join(str(x) for x in tv)}，低于 "
            f"{TOOL_MIN[0]}.{TOOL_MIN[1]}：构建前硬门用的是 3.9+ 的标准库，它跑不起来"
            "等于那道门没跑，而不是那道门过了")
    # ④ 两个名字必须是两个人：同一个路径意味着分工根本没成立。
    if same_path:
        problems.append("目标解释器与工具链解释器是同一个可执行文件：双解释器的分工不存在，"
                        "产物与门禁跑在同一版本上（那这条通道与主通道就是同一个东西）")
    return problems


def check_pin(target_py: str, tool_py: str) -> list:
    """问两个解释器各自是谁，再交给 ``evaluate`` 判定（采集与判定分开，后者才能离线喂假数据）。"""
    target, tool = probe(target_py), probe(tool_py)
    for label, got in (("进包的解释器", target), ("门禁的解释器", tool)):
        if "error" not in got:
            print(f"· {label}：{got['banner']}｜{got['bits']} 位｜{got['executable']}")
    same_path = False
    try:
        same_path = pathlib.Path(target_py).resolve() == pathlib.Path(tool_py).resolve()
    except OSError:
        pass                                    # 解析不了就交给上面两档去报，不在这里造第二个错
    return evaluate(sys.version_info[:3], target, tool, same_path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Win7 兼容通道：双解释器分工体检与进包运行时对版")
    ap.add_argument("--target-py", required=True, help="进包的解释器（3.8）可执行文件路径")
    ap.add_argument("--tool-py", default="", help="跑门禁的解释器（3.11）可执行文件路径；--pin 时必填")
    ap.add_argument("--pin", action="store_true", help="查四档解释器分工（构建最开头那一步）")
    ap.add_argument("--inventory", action="store_true", help="问目标环境装到了什么并钉住 Qt 5")
    args = ap.parse_args(argv)

    if not args.pin and not args.inventory:
        print("::error::既没要 --pin 也没要 --inventory：这个调用什么也没判，"
              "而「跑了一步却什么都没判」就是门禁最危险的形状")
        return 1

    problems = []
    if args.pin:
        if not args.tool_py:
            print("::error::--pin 需要 --tool-py：两个解释器只问一个，分工这一格判不了")
            return 1
        problems.extend(check_pin(args.target_py, args.tool_py))
    if args.inventory:
        problems.extend(inventory(args.target_py))

    for why in problems:
        print(f"::error::{why}")
    if problems:
        print(f"❌ 兼容通道体检不合格：{len(problems)} 处。这一格是它唯一能回答"
              "「这个包到底是不是 3.8 + Qt5 打的」的地方，不放行就不许开建")
        return 1
    if args.pin:
        print(f"✅ 双解释器分工成立：产物用 {'.'.join(str(x) for x in TARGET_FLOOR)}（Win7 还能用的"
              f"最后一档），门禁跑在 ≥{'.'.join(str(x) for x in TOOL_MIN)} 上（裸 python 已确认）")
    if args.inventory:
        print("✅ 进包运行时已对版，Qt 主版本为 5")
    return 0


if __name__ == "__main__":
    sys.exit(main())
