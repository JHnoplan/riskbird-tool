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

用法（CI 在 b2bcollect_build 目录下调用，也可从仓库根调用，路径按参数给）：
    python .github/scripts/package_win_artifacts.py --root b2bcollect_build
"""
from __future__ import annotations

import argparse
import os
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
    "base_library.zip": "onedir 的铁证：onefile 时代它就是被 _MEI 回收 race 掉的那个文件",
    "config.json": "随包凭证（缺它 = 外部机器一律精简版）",
    "mk_phone_font.json": "马可波罗图片码字形字典（缺它 = 手机号整列空白）",
    "riskbird_build/runtime_paths.py": "三个采集器的可写落点口径（缺它 = 开机即崩）",
    "riskbird_build/riskbird_crawler_v2.py": "风鸟名单通道（缺它 = 静默少一个平台）",
    "riskbird_build/aiqicha_crawler_v2.py": "爱企查名单通道（缺它 = 静默少一个平台）",
    "riskbird_build/qizhidao_crawler.py": "企知道名单通道（缺它 = 静默少一个平台）",
    "ali1688_build/ali1688_crawler.py": "1688 关键词通道（缺它 = 静默少一个平台）",
    "enf_clean": "清洗子包源码目录（缺它 = 清洗链路降级）",
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
    internal = app / "_internal"
    if not internal.is_dir():
        _fail(problems, f"产物目录里没有 _internal：{internal}"
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
        if not (internal / rel).exists():
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
