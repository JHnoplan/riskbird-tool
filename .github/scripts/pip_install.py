#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交付链的 pip 安装：一份依赖清单 + 多个索引源，全挂才算挂。

为什么要新增这一份脚本（2026-11-13 云端两个 run 同时红在同一个地方）：
四个 workflow 的 `Install dependencies` 都写成

    pip install <一长串包名> -i https://pypi.tuna.tsinghua.edu.cn/simple

日志里的事实是：清华源连续 5 次 ReadTimeout（pip 把重试耗尽），然后
`ERROR: No matching distribution found for PyQt5 (from versions: none)`，Mac 与
Windows 两个制品同时没有——**构建脚本没写错，代码没改坏，交付链挂在了一个外部门口**。
这条链上有两类东西我们控制得了，一类控制不了：

  · 控制得了：源清单里有没有第二、第三条路；一条路最多占用多少时间；失败时留下
    的是「哪个源、什么错」还是只有一句 from versions: none；
  · 控制不了：任何一个镜像今天的可用性。

所以这里把「可用性」做成冗余而不是做成祈祷：按顺序试多个源，第一个成功就收工，
每个源都有有界的超时（挂住的源不许把整个 job 拖到 6 小时），全挂才非零退出，并且
逐源点名「试了谁、它说了什么」。这和交付链上其它门的思路是同一件事：**任何单点外部
依赖，都必须在它失效的那一次就把后果说清楚**，而不是留下一句与根因无关的报错。

顺带解决第二类老毛病：mac 与 Windows 两份 workflow 的包名清单此前靠注释「逐行对齐」
（人肉同步）。清单挪进 `.github/requirements/*.txt`，两个平台读同一个文件，对齐这件事
从此不需要人记得。

用法（workflow 里）：
    python .github/scripts/pip_install.py -r .github/requirements/b2bcollect.txt
退出码：0 某个源装成功；1 所有源都失败或清单本身不合格；2 用法错误。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

# 编码由脚本自己写定，不等环境（第 12 条；Windows runner 的控制台代码页是 cp1252）
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 索引源，顺序即优先级。空字符串 = 不加 -i，用 pip 自己的默认源（官方 PyPI）。
# 官方放第一位不是因为它一定快，而是因为镜像的可变性最大（同步延迟、区域策略、
# 今天这种整段超时）；镜像留给海外 runner 网络受限时的退路。
INDEX_SOURCES = (
    ("pypi（pip 默认源）", ""),
    ("清华", "https://pypi.tuna.tsinghua.edu.cn/simple"),
    ("阿里云", "https://mirrors.aliyun.com/pypi/simple/"),
)

# 单个源的总时长上限（秒）与 pip 自己的网络参数。三个数字都要有界：没有 timeout 的
# pip 会挂在镜像上直到 job 超时（默认 6 小时），日志里一句原因都留不下；而没有总时长
# 的 --retries 会把「有界的失败」又变回无界。取 900 秒是因为 PyQt5+pandas+PyQt 全家
# 在慢源上真取过十几分钟量级，宁给余量也不许无界。
PER_SOURCE_TIMEOUT = 900
PIP_NET_FLAGS = ("--timeout", "30", "--retries", "2")


def _pkg_lines(requirements: tuple) -> list:
    """清单里的包名（去掉注释与空行）；文件不在位或一份都没写时返回 (行, 问题)。"""
    lines, problems = [], []
    for req in requirements:
        if not req.is_file():
            problems.append(f"依赖清单 {req} 不在位：没有清单就没有交付，这一步不能安静跳过")
            continue
        got = [ln.strip() for ln in req.read_text(encoding="utf-8", errors="replace").splitlines()
               if ln.strip() and not ln.strip().startswith("#")]
        if not got:
            problems.append(f"依赖清单 {req} 里一个包都没有（空清单=产物缺依赖但构建全绿）")
        lines += got
    return lines, problems


def install(requirements: tuple = (), packages: tuple = (), upgrade: bool = False,
            sources: tuple = INDEX_SOURCES, pip: tuple = None) -> list:
    """按顺序试各源装依赖；返回失败原因清单（空列表 = 某个源装上了）。

    ``sources`` / ``pip`` / ``PER_SOURCE_TIMEOUT`` 是留给离线用例的接缝：判据要能被喂
    一个「只有一个源」或「没有 --timeout」的假安装现场，才谈得上反向对照。
    """
    problems: list = []
    lines, bad = _pkg_lines(tuple(Path(r) for r in requirements))
    if bad:
        return bad
    if packages:
        lines += list(packages)
    if not lines:
        return ["既没有 -r 清单也没有包名：这一步不知道该装什么，宁可红也不装个寂寞"]
    pip = (sys.executable, "-m", "pip") if pip is None else tuple(pip)
    attempted = 0
    for label, url in sources:
        attempted += 1
        cmd = list(pip) + ["install", "--disable-pip-version-check", "--no-input",
                           *PIP_NET_FLAGS]
        if upgrade:
            cmd += ["--upgrade"]
        for req in requirements:
            cmd += ["-r", str(req)]
        cmd += list(packages)
        if url:
            cmd += ["-i", url]
        try:
            r = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, encoding="utf-8",
                               errors="replace", timeout=PER_SOURCE_TIMEOUT)
        except subprocess.TimeoutExpired:
            problems.append(f"{label}：超过 {PER_SOURCE_TIMEOUT} 秒没装完——"
                            f"这个源今天不响应，换下一个（挂住的源不许把 job 拖到超时）")
            continue
        except OSError as exc:
            problems.append(f"{label}：pip 根本没能启动：{exc}")
            continue
        if r.returncode == 0:
            print(f"✅ 依赖装上了（源：{label}，共 {len(lines)} 项）")
            return []
        last = (r.stdout or "").strip().splitlines()
        problems.append(f"{label}：退出码 {r.returncode}，最后一句："
                        f"{(last[-1] if last else '(没有输出)')[:160]}")
    if not attempted:
        # fail-closed：源清单为空时，上面那个循环一次都不跑，problems 也就是空的。
        # 空 problems 在调用方等于「装上了」——于是一次 pip 都没执行却报绿，是这条链上
        # 最坏的失败样子（和取制品那一路「没验到被当成验过」是同一族）。
        return ["索引源清单是空的：一次 pip 都没跑，却说不出任何失败原因"]
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="交付链 pip 安装：一份清单，多个源，全挂才算挂")
    ap.add_argument("-r", "--requirements", action="append", default=[],
                    help="依赖清单路径（相对仓库根亦可，可重复）")
    ap.add_argument("packages", nargs="*", help="直接写在命令行上的包名（如 pip 自身升级）")
    ap.add_argument("--upgrade", action="store_true", help="加 --upgrade（用于 pip 自身）")
    args = ap.parse_args(argv)
    if len(INDEX_SOURCES) < 2:
        print("::error::索引源少于两条：这一条链又变成了单点，任何一个镜像出问题就没有交付")
        return 1
    root = Path(__file__).resolve().parent.parent.parent
    reqs = tuple(Path(r) if Path(r).is_absolute() else root / r for r in args.requirements)
    problems = install(requirements=reqs, packages=tuple(args.packages),
                       upgrade=args.upgrade)
    if problems:
        print(f"❌ 所有索引源都没装上这份依赖（试了 {len(problems)} 个源）：")
        for p in problems:
            print(f"   · {p}")
            print(f"::error::pip 安装：{p}")
        print("   · 一个源不通不是构建错误，全部不通才是不交付：先确认 runner 出口，"
              "再考虑在 INDEX_SOURCES 里加一条源")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
