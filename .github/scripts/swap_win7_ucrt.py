#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""装配期把包里的 UCRT 换成 Win7 兼容版（需求 10B / 台账第 11 项，兼容通道独有步骤）。

由来（2026-11-21 取证，legacy run#4 @870a8a9）：
  PE 地板门报 42 项红，全部是 PyInstaller 从 Win11 runner 的 System32 扫依赖带进来的
  api-ms-win-* 存根（30 个 core + 12 个 crt，子系统版本全是 10.0）。但**光剪存根不够**：
  包内那份 Win11 版 ucrtbase.dll 自己就静态依赖 api-ms-win-core-synch-l1-2-0.dll
  （Win8 才有的契约模块，见 check_pe_floor.POST_WIN7_DLL）——存根在位时门按「随包桩
  导出表实测」放行，存根一剪它立刻变红；而在真 Win7 上，没有 KB2999226 的机器连这个
  模块名都解析不到，加载器当场拒载。所以必须把 ucrtbase 与 api-ms-win-crt-* 转发器
  **整套换成 Win7 兼容版**，再让 PE 门实测剪掉失去依赖方的 core 存根。

为什么取 Windows SDK 的 Redist\\<ver>\\ucrt\\DLLs\\x64：
  那一目录就是微软给老系统做 app-local 部署用的 UCRT（KB2999226 的同源二进制）。
  另一条路——解 VC_redist.x64.exe 里的 KB2999226 cab——已实测走不通：内层 48 个编号
  文件**全部是 PA delta 补丁**（0 个 MZ 头），ApplyDelta 是 Windows API，macOS 与
  离线判据都拿不到完整字节。SDK redist 是完整镜像，且 runner（windows-latest）自带。

自证式设计（不凭记忆写 Windows 行为）：
  替换前先用 check_pe_floor.check_one 按同一把尺子（地板 6.1、无随包桩的最严口径）
  量候选 ucrtbase 的真实字节——地板高了、或仍依赖 Win8+ 契约模块，就 fail-closed
  红在这里并点名是哪一格，绝不把一份没量过的 DLL 塞进包里。云端 PE 门随后还会对
  替换后的整个 dist 复检一次，两层量的是同一份字节。

诚实边界：本脚本只保证「换进去的 UCRT 自身过加载器那一关」。core 存根剪掉后其余
镜像是否还有暗依赖，由紧随其后的 PE 地板门全量复检说了算——那也是云端实测的一部分。
"""
import argparse
import pathlib
import shutil
import sys

# 控制台编码写死（第 10 条）：Windows runner 英文镜像的 cp1252 会在写中文的当下抛
# UnicodeEncodeError，把本步的结论盖掉——不靠 import check_pe_floor 的副作用兜底。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import check_pe_floor as pe  # noqa: E402  同一目录的判据脚本，复用它的尺子不抄第二份

FLOOR = (6, 1)
# SDK redist 的固定布局（Windows Kits 10 起从未变过）；版本目录取最新一档。
SDK_GLOB = "Redist/*/ucrt/DLLs/x64"
SDK_ROOTS = (
    pathlib.Path(r"C:\Program Files (x86)\Windows Kits\10"),
    pathlib.Path(r"C:\Program Files\Windows Kits\10"),
)


def find_sdk_ucrt() -> pathlib.Path:
    """在 runner 上找 SDK 的 UCRT redist 目录；找不到就 raise（fail-closed）。"""
    hits = []
    for root in SDK_ROOTS:
        if root.is_dir():
            hits.extend(p for p in root.glob(SDK_GLOB) if (p / "ucrtbase.dll").is_file())
    if not hits:
        raise FileNotFoundError(
            "在 runner 上找不到 Windows SDK 的 UCRT redist（找过 "
            + " / ".join(str(r / SDK_GLOB) for r in SDK_ROOTS)
            + "）。下一步：在 workflow 里显式装 Windows SDK，或用 --ucrt-src 指一份"
              "已验证过地板的 UCRT 目录——不许跳过这一步直接出包")
    # 版本目录名形如 10.0.26100.0：按元组比大小，不吃字符串排序的亏。
    def ver(p):
        try:
            return tuple(int(x) for x in p.parent.parent.name.split("."))
        except ValueError:
            return (0,)
    return max(hits, key=ver)


def validate_ucrtbase(src: pathlib.Path) -> None:
    """替换件必须先过与产物同一把尺子；不合格就 raise，点名是哪一格。"""
    raw = (src / "ucrtbase.dll").read_bytes()
    problems = pe.check_one(pathlib.Path("ucrtbase.dll（SDK 替换件）"), raw, FLOOR)
    if problems:
        raise ValueError("SDK 的 ucrtbase.dll 自己就过不了 Win7 地板门，不许换进包：\n  "
                         + "\n  ".join(problems)
                         + "\n下一步：换一档 SDK redist 再试，或把这份红话贴进台账第 11 项")


def swap(dist: pathlib.Path, src: pathlib.Path) -> int:
    """把 src 里的 ucrtbase + api-ms-win-crt-* 换进 dist/_internal，逐件报账。"""
    internal = dist / "_internal"
    if not internal.is_dir():
        print(f"::error::{internal} 不在位：PyInstaller 布局变了？先确认产物目录再跑这一步")
        return 1
    try:
        validate_ucrtbase(src)
    except ValueError as e:
        # 体检不合格要在这一层就落成红话 + 退出码：直接调 swap() 的调用方（离线判据）
        # 不该接住一个裸栈——判据自己抛栈比误判更难查（DH 节同一课）。
        print(f"::error::{e}")
        return 1
    # crt 转发器必须**整套**换：dist 里有、src 里没有的那几个会留下 Win11 版存根
    # （地板 10.0），PE 门照样红——所以缺一个就停，不做半套替换。
    want = sorted(p.name for p in internal.glob("api-ms-win-crt-*.dll"))
    missing = [n for n in want if not (src / n).is_file()]
    if missing:
        print(f"::error::SDK redist 里缺这几个 crt 转发器，半套替换会留下 Win11 存根："
              f"{', '.join(missing)}（src={src}）")
        return 1
    targets = ["ucrtbase.dll"] + want
    for name in targets:
        old = internal / name
        if not old.is_file():
            print(f"::error::{old} 不在位：包里没有这一件，替换无从对拍（布局变了先查 PyInstaller）")
            return 1
        new = src / name
        old_size = old.stat().st_size      # 先记账再覆盖：覆盖后 stat 拿到的就是新字节了
        shutil.copyfile(new, old)
        print(f"· 已替换 _internal/{name}：{old_size} 字节 ← {new}（{new.stat().st_size} 字节）")
    print(f"结论：UCRT 已整套换成 Win7 兼容版（ucrtbase + {len(want)} 个 crt 转发器，src={src}）；"
          "core 存根交给下一步 PE 地板门实测剪")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="装配期替换 Win7 兼容 UCRT（兼容通道独有）")
    ap.add_argument("--dist-dir", required=True, help="PyInstaller 产物目录（含 _internal）")
    ap.add_argument("--ucrt-src", default="", help="显式指定 UCRT 目录（缺省自动找 runner 上的 SDK redist）")
    args = ap.parse_args(argv)
    dist = pathlib.Path(args.dist_dir)
    try:
        src = pathlib.Path(args.ucrt_src) if args.ucrt_src else find_sdk_ucrt()
        if not (src / "ucrtbase.dll").is_file():
            print(f"::error::{src} 里没有 ucrtbase.dll：这不是一个 UCRT redist 目录")
            return 1
        return swap(dist, src)
    except (FileNotFoundError, ValueError) as e:
        print(f"::error::{e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())