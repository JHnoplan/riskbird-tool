#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交付 zip 的门：条目名的字节 + 在目标系统上用系统自带的解包工具真解一次。

为什么要有这一份脚本（2026-11-13 本机字节级对拍定出的根因）：
云端打出来的 ``B2bCollectTool_Win_portable.zip`` 里只有一个条目落不下去——zip 根上的
``使用说明.txt``。本机把同一份字节交给两个工具，跑出来的事实是：

  · ditto：rc=0，3909 个文件全落地，中文名正确；
  · unzip 6.00（Apple 改过的那份）：rc=50，只落地 3908 个，它自己说
    ``write error (disk full?).  Continue? (y/n)``——**那块卷当时还剩 9 GB**，
    这句是它写不下去时顺口说的猜测，不是结论。

真正把成败定下来的字段，是把 zip 字节拆开才看见的：

  · 条目的名字节是 UTF-8，本地头与中央目录的 0x800（UTF-8 名字）标志位都规规矩矩置着
    ——这两样**都不是**决定项（只清中央目录那一位，unzip 照样解得开）；
  · 决定项是中央目录记录里「version made by」的高字节（create_system）：
      3 = Unix  → unzip 按本地编码直取名字字节，名字对、解得开；
      0 = DOS/FAT → 它把名字再过一遍 OEM→Native 转换，得到一个写不下去的名字。
    两个方向都实测过：把一份本来解得开的合成包那**一个字节**从 3 改成 0，当场变
    rc=50、文件不落地；把真制品那一个字节从 0 改成 3，rc=50/3908 变 rc=0/3909、
    名字正确。一条字节改出来的红与绿，比任何推理都硬。
  · CPython 的 zipfile 在 win32 上把 create_system 写死成 0
    （``zipfile/__init__.py``：``if sys.platform == 'win32': self.create_system = 0``），
    而它写进 external_attr 的却是 Unix 风格的 mode（高 16 位）——也就是说 Windows runner
    打出来的包，字节上本来就自相矛盾。这条我们改不动标准库，能改动的是**自己的纪律**。

于是这份脚本判两件事，都不靠推理、只靠字节与真解一次：
  1) ``check_zip_entries``：条目名必须 ASCII（不许依赖任何一个解包工具对非 ASCII 名的
     理解），不许有 ``\\``、绝对路径、``..``；顶层形状必须是 ``<top>/``（绿色版根上只
     额外允许那份说明）；条目名两两在「大小写不敏感 + Unicode 规范差异」下不许撞车——
     撞车时后解的那个会把先解的吃掉，用户看到的是「少一个文件」而工具报成功。
  2) ``extract_with_os_tools``：在这台机器上拿系统自带的工具真解一次（macOS：ditto 与
     unzip；Windows：bsdtar 与 .NET System.IO.Compression，也就是资源管理器/PowerShell
     走的那条），拿落地清单与条目清单**对拍**，少一个、名字不对、多一个都算不合格。
     判「解出来的字节」而不是「我以为工具会怎么读」，是这一整轮事故换来的做法。

用法：
    python .github/scripts/check_release_zip.py <zip 路径> [--top B2bCollectTool]
           [--root-file usage_guide.txt] [--require 相对路径 ...] [--os-extract]
退出码：0 合格；1 不合格（逐条点名）；2 用法/文件问题。
"""
from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 解一次大包的有界时间：绿色版 160 MB / 约 3900 个文件，正常几十秒；给到 20 分钟
# 是余量，但不许无界——取制品那一条链上「没人答的问句把整件事挂住」刚红过一次。
EXTRACT_TIMEOUT = 1200


def fs_key(name: str) -> str:
    """在大小写不敏感、且 Unicode 规范化形式由文件系统决定的那块盘上，这个名字归一成什么。

    Windows 的 NTFS 与 macOS 的 APFS 都按「大小写不敏感 + 保留首次写入的写法」处理，
    mac 上是 NFD、Windows 上是 NFC：两个只差大小写或只差组合形式的条目，在 Linux CI
    上是两个文件、在用户机器上就是同一个文件。
    """
    return unicodedata.normalize("NFC", name).casefold()


def check_zip_entries(path: Path, top: str = "", root_files: tuple = ()) -> list:
    """条目名与顶层形状的判据：返回不合格条目（空列表 = 合格）。

    ``root_files`` 同时承担两件事，这是有意的：**允许**躺在顶层目录之外，且**必须存在**。
    拆开成两个开关（一个白名单、一个必需清单）看着更通用，实际是把「只填了一个」做成
    可达状态——漏填必需清单的那一路照样报绿。我们的交付根上只有那一份使用说明，它既
    是白名单里的例外也是必需项，两个语义天然同源；调用方传的本来就是这个意思。
    """
    problems: list = []
    try:
        zf = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        return [f"{path.name} 不是一个能打开的 zip：{exc}"]
    with zf:
        infos = zf.infolist()
        if not infos:
            return [f"{path.name} 里一个条目都没有（空的交付包）"]
        non_ascii = [i.filename for i in infos if not i.filename.isascii()]
        if non_ascii:
            problems.append(
                f"{path.name} 里有 {len(non_ascii)} 个非 ASCII 条目名（例如 {non_ascii[0]!r}）："
                f"解包工具对非 ASCII 名字的处理取决于中央目录里「version made by」的高字节，"
                f"Windows runner 上 Python 打的包那一字节是 0（DOS），Apple 的 unzip 因此把名字"
                f"再过一遍 OEM→Native 转换，最后说的是 `write error (disk full?)`——交付包不许"
                f"把成败押在别人的实现上，条目名一律 ASCII，给用户看的中文名由安装脚本 /oname 给")
        bad_shape = []
        for i in infos:
            n = i.filename
            if "\\" in n or n.startswith("/") or n.startswith("~") or ".." in PurePosixPath(n).parts:
                bad_shape.append(n)
        if bad_shape:
            problems.append(f"{path.name} 里有 {len(bad_shape)} 个条目名带反斜杠/绝对路径/..："
                            f"{bad_shape[:3]}（这类条目在某些工具里写到包外，在另一些里直接落不下去）")
        seen: dict = {}
        collide = []
        for i in infos:
            k = fs_key(i.filename.rstrip("/"))
            if k in seen and seen[k] != i.filename:
                collide.append((seen[k], i.filename))
            else:
                seen[k] = i.filename
        if collide:
            a, b = collide[0]
            problems.append(f"{path.name} 里有 {len(collide)} 对名字只差大小写或 Unicode 组合形式，"
                            f"在用户那块盘上是同一个文件（例如 {a!r} 与 {b!r}）：后解的那个会吃掉"
                            f"先解的那个，工具报成功而用户少一个文件")
        if top:
            # 顶层这一格按**层级与身份**判，不按尾斜杠的拼写判（理由见 ``_is_under``：拼写那
            # 一路把「目录条目怎么拼」当成了交付内容，反向那一格还会放过根上同名的普通文件）。
            _norm = {n.rstrip("/") for n in root_files}
            stray = [i.filename for i in infos
                     if i.filename.rstrip("/") not in _norm
                     and not _is_under(i, top)]
            if stray:
                problems.append(f"{path.name} 的顶层不止 {top}/ 这一层（散出来的有 {stray[:3]}）："
                                f"用户会把它解压到桌面摊一桌，安装目录和数据目录被摊进用户可见区域")
        _present = {i.filename.rstrip("/") for i in infos if not _is_dir_entry(i)}
        absent = [n for n in root_files if n.rstrip("/") not in _present]
        if absent:
            problems.append(f"{path.name} 根上少了 {absent}：源文件在、条目却没写进包里，"
                            f"这就是打包那一步**静默少交付**一样东西——zip 的门只看了解不开、"
                            f"没看少没少，用户解压完第一眼找不到使用说明，只能凭记忆操作")
    return problems


def _tool_specs(zp: Path, platform: str = "", name: str = "") -> list:
    """这台机器上「用户会用的那几条解包路」：返回 (标签, 可执行名, 需要先建目录, argv 构造函数)。

    ``platform`` / ``name`` 是留给离线用例的接缝：默认按当前机器取值，用例可以按别的机器
    取值。为什么留这个口子而不是让用例去拨 ``sys.platform``/``os.name``：在 mac 上把
    ``os.name`` 拨成 nt，``pathlib`` 立刻改成实例化 ``WindowsPath`` 并抛
    ``UnsupportedOperation``，``sys.platform`` 拨成 win32 则 posix 的 ``shutil.which`` 会去摸
    在 mac 上为 None 的 ``_winapi``——两处红的是**仿真自己的环境**，跟交付物无关。仿的是行为，
    不是操作系统的名字，所以这一维由参数给，不由篡改全局给。

    argv 构造函数收的是落地目录，避免把目录字符串塞进命令里再替换——替换一次就有一种
    工具的命令被改坏（PowerShell 那条的目标本来在 -Command 里面）。

    ``需要先建目录`` 这一维不是装饰：``bsdtar -x -C dir`` 要求 dir **已存在**，而
    ``[ZipFile]::ExtractToDirectory(src, dst)`` 在 .NET Framework 上碰到已存在的 dst 直接抛
    IOException（文档写的就是 "The directory ... already exists"）。统一预建目录会让 Windows
    runner 上那一路**每次都假红**——红的是判据的写法，不是交付物。这类差异必须在写的时候就
    分清，不能等云端替我们发现（那时代价是一整个交付没有安装包）。
    """
    z = str(zp)
    platform = platform or sys.platform
    name = name or os.name
    if platform == "darwin":
        # Finder 双击走的就是 ditto；终端里手敲的是 unzip。两条都是真实路径。
        return [("ditto", "ditto", False, lambda d: ["ditto", "-x", "-k", z, d]),
                ("unzip", "unzip", False, lambda d: ["unzip", "-q", z, "-d", d])]
    if name == "nt":
        # Win10+ 自带的 bsdtar，与 PowerShell/资源管理器背后的 System.IO.Compression。
        return [("bsdtar", "tar", True, lambda d: ["tar", "-xf", z, "-C", d]),
                ("dotnet", "powershell.exe", False,
                 lambda d: ["powershell.exe", "-NoProfile", "-NonInteractive",
                            "-ExecutionPolicy", "Bypass", "-Command",
                            "Add-Type -AssemblyName System.IO.Compression.FileSystem;"
                            "[System.IO.Compression.ZipFile]::ExtractToDirectory("
                            f"'{z}','{d}')"])]
    return [("python-zipfile", sys.executable, False,
             lambda d: [sys.executable, "-c",
                        "import sys,zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])",
                        z, d])]


def _tool_names(zp: Path) -> tuple:
    return tuple(label for label, _, _, _ in _tool_specs(zp))


def _is_dir_entry(i: zipfile.ZipInfo) -> bool:
    """这一条在 zip 里是不是「目录」这个身份。

    ``ZipInfo.is_dir()`` 只看尾斜杠，而身份同时写在 ``external_attr`` 的高 16 位里（mode）。
    两个都认，是因为门只在这里读工具自己的字段，读到哪一格都要说得出实测过什么（下面
    ``_expected_files`` 的注释里记着本机对着真制品与真 ditto 数出来的结果）。
    """
    return i.filename.endswith("/") or stat.S_ISDIR(i.external_attr >> 16)


def _is_under(i: zipfile.ZipInfo, top: str) -> bool:
    """这个条目是不是落在 ``top/`` 这一层里面——按**层级与身份**判，不按尾斜杠的拼写判。

    按 ``startswith(top + "/")`` 判有两格会错：
    · ``top/`` 本身这一条（目录条目）——它的名字是 ``top/`` 还是 ``top`` 取决于打包邮的
      工具怎么拼，拼法不是交付内容；
    · 反过来，根上躺着一个**与顶层目录同名但身份是普通文件**的条目时，``startswith`` 判它
      不在 top 里、层级判它在里面——这时候必须报散出来，因为它根本不是那个目录，用户解压
      后拿到的是「一个文件 + 一个同名目录」，工具在大小写不敏感的盘上还会撞车。
    所以：拆层比第一段，第二段以后都算在里面；只剩第一段时问身份（目录才算顶层目录本身）。
    """
    parts = [p for p in i.filename.split("/") if p]
    if not parts or parts[0] != top:
        return False
    return len(parts) > 1 or _is_dir_entry(i)


def _ancestors(names) -> set:
    """这些路径的全部父目录（zip 里以 / 分隔，落地后也是同一层结构）。"""
    out: set = set()
    for n in names:
        parts = n.split("/")
        for i in range(1, len(parts)):
            out.add("/".join(parts[:i]))
    return out


def _missing_paths(want: set, got: set) -> list:
    """条目里哪些路径**真的**没到用户手上。

    AppleDouble 侧车（同层里那个 `._名字`）是唯一一种「本来就不该按字面落地」的条目：
    ditto 把它读回去变成基文件的扩展属性与 ACL，解完根本不留 `._X` 这个文件——这不是少
    交付，这正是用户想要的（Finder 里看到的包和打包时一模一样）；unzip 不认识它，于是老实
    落一个 `._X`。同一批字节在两个工具下给出两种落地清单，恰好是本轮反复踩的那条线：拿别人
    工具的实现差异当交付缺陷。所以这里只问「它描述的那个基文件到没到」，基文件也没到才报红。

    这一支在**当前这份交付上从不触发**：对着真制品数过，`._` 侧车条目 0 个、`__MACOSX`
    0 个（ditto 打包时那几个文件没有扩展属性可存）。留着是因为夹具里会触发（本机 ditto
    造的那份包有 10 条侧车），而且哪天交付带上 xattr 就是它挡在中间——但别把它记成
    「我们的包里有侧车，所以门要豁免」，那是没发生的事。
    """
    miss = []
    for n in sorted(want - got):
        d, _, b = n.rpartition("/")
        base = (d + "/" if d else "") + b[2:]
        if b.startswith("._") and base in got:
            continue
        miss.append(n)
    return miss


def _expected_files(zp: Path) -> tuple:
    """条目清单：(包里的所有路径, 重名条目数)。目录条目也算一条路径。

    云端那条假红（2026-11-13 Mac run：`ditto/unzip 解出来各少了 168 个路径`，第一例
    ``B2bCollectTool.app/Contents/Frameworks/PIL/.dylibs``）当初被我记成「ditto 写目录条目
    不带尾斜杠，门按尾斜杠分类」——**那句话是错的**，错在拿推理顶替实测。对着真制品的字节
    数过之后（本机同一份源码打的包，4943 个条目）：
      · 3297 条普通文件、1078 条符号链接、568 条目录条目，目录条目**全部带**尾斜杠，
        不带的一条都没有；本机 ``ditto -c -k --keepParent`` 造出来的包同样全带；
      · 那 168 条是**落地后是目录的符号链接**。PyInstaller 在 mac 上把隐藏目录 ``.dylibs``
        另存成 ``__dot__dylibs``，再留一条 ``.dylibs`` → ``__dot__dylibs`` 的链接；本机拿
        旧口径把这份真制品重跑一遍，条目侧 4375、落地侧 4207，差的 168 条 external_attr
        全是 ``S_IFLNK``，第一条正是 ``…/PIL/.dylibs``（解出来 islink 与 isdir 同时为真）；
      · 门旧版落地侧 ``for r, _, fs in os.walk(dst) for f in fs`` 只收 files，而 os.walk
        按跟随符号链接后的 isdir 分类——指向目录的链接被分到 dirs 那一格，于是条目里
        有、落地清单里「没有」。红的是**落地清单的收法**，交付物一个字节都没少。
    所以两侧都按「这条路径在不在」算：条目侧一律去掉尾斜杠计入（不靠拼法分类），落地侧
    files 与 dirs 一起收。某个关键路径是不是真的可执行/非空，由 require 那一路单独判
    （那里用 is_file 与字节数），不在这里混进同一条判据。
    顺带把「目录条目不带斜杠」这一格也钉死：本机试过用它造包（dir-mode 属性 + 不带斜杠，
    compress_type 与 DOS 目录位四种组合都试），ditto 与 unzip **两个都拒绝**（rc=1
    ``Unknown compression type``／rc=2 ``unable to process …``），所以那不是交付形状，
    不该拿它当正例去解——本轮第 5 次犯的错就是让夹具的形状冒充生产者的形状。
    """
    with zipfile.ZipFile(zp) as zf:
        names = [n for n in (i.filename.rstrip("/") for i in zf.infolist()) if n]
    uniq = set(names)
    return uniq, len(names) - len(uniq)


def extract_with_os_tools(zp: Path, require: tuple = (), platform: str = "",
                          name: str = "") -> list:
    """拿本机系统自带的解包工具真解一次，并把落地清单与条目清单对拍。

    只在这台机器可用的工具上跑，跑不动就明说「这一路没验」而不是安静跳过：判据必须
    说得出自己验了什么。``platform`` / ``name`` 与 ``_tool_specs`` 同一用途（离线用例
    按另一台机器的工具组合真跑一次），默认取当前机器。
    """
    problems: list = []
    want, dup = _expected_files(zp)
    if dup:
        problems.append(f"{zp.name} 条目清单里有 {dup} 个重名条目：对拍无从判起，先修生产者")
        return problems
    cmds = _tool_specs(zp, platform=platform, name=name)
    base = Path(tempfile.mkdtemp(prefix="release_zip_check."))
    try:
        for label, tool, need_dir, mkargv in cmds:
            if tool != sys.executable and shutil.which(tool) is None:
                problems.append(f"{zp.name}：这台机器没有 {label}（{tool}），这一路没验到——"
                                f"换一台带它的机器再跑，别把「没验」当成「过了」")
                continue
            dst = base / label
            if need_dir:                                    # bsdtar 那一路要现成的目录
                dst.mkdir(parents=True, exist_ok=True)
            cmd = mkargv(str(dst))
            try:
                r = subprocess.run(cmd, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   encoding="utf-8", errors="replace",
                                   timeout=EXTRACT_TIMEOUT)
            except subprocess.TimeoutExpired:
                problems.append(f"{zp.name}：{label} 解包超过 {EXTRACT_TIMEOUT} 秒没结束——"
                                f"工具在等一个不会来的回答，这一路本身就不适合后台跑")
                continue
            except OSError as exc:
                problems.append(f"{zp.name}：{label} 根本没能启动：{exc}")
                continue
            got = {os.path.relpath(os.path.join(r, x), dst).replace(os.sep, "/")
                   for r, ds, fs in os.walk(dst) for x in ds + fs}
            if r.returncode != 0:
                problems.append(f"{zp.name}：{label} 解包退出码 {r.returncode}，它的最后一句："
                                f"{(r.stdout or '').strip().splitlines()[-1][:120] if r.stdout else '(没有输出)'}")
            miss = _missing_paths(want, got)
            # 「落地比条目多」本身不是缺陷：包里有 a/b/c.txt 而没有 a/b/ 这一条时，任何工具
            # 都得把 a 与 a/b 现建出来。上一版我在这里把计数差异直接当成「用户手里多了东西」
            # ——那是我刚在下面那条注释里批评过的同一个错（拿计数差异推断包坏，没先问每一个
            # 名字的身份），离线夹具 ⑤ 立刻把它抓了出来。
            # 所以只有**既不是条目、也不是任何条目的父目录**的名字才算工具凭空造出来的东西。
            extra = sorted(got - want - _ancestors(want))
            if miss:
                problems.append(f"{zp.name}：{label} 解出来少了 {len(miss)} 个路径（例如 {miss[0]!r}）："
                                f"名字对不对是解包工具的事，少落地就是用户手里的包坏了")
            if extra:
                problems.append(f"{zp.name}：{label} 解出了条目清单里根本没有、也不是任何条目父目录的 "
                                f"{len(extra)} 个名字（例如 {extra[0]!r}）：多半是名字被工具改写后落的地")
            for rel in require:
                p = dst / rel
                if not p.is_file():
                    problems.append(f"{zp.name}：{label} 解完之后 {rel} 不在（这一项是交付判据点名的必需文件）")
                elif p.stat().st_size == 0:
                    problems.append(f"{zp.name}：{label} 解出来的 {rel} 是 0 字节")
            shutil.rmtree(dst, ignore_errors=True)
    finally:
        shutil.rmtree(base, ignore_errors=True)
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="交付 zip 的门：条目名字节 + 目标系统真解一次")
    ap.add_argument("zip", help="要验的 zip 路径")
    ap.add_argument("--top", default="", help="唯一允许的顶层目录名（绿色版传 B2bCollectTool）")
    ap.add_argument("--root-file", action="append", default=[],
                    help="允许躺在顶层目录之外的条目名（可重复；绿色版就是那份 ASCII 说明）")
    ap.add_argument("--require", action="append", default=[],
                    help="解出来必须存在且非空的相对路径（可重复）")
    ap.add_argument("--os-extract", action="store_true",
                    help="再用这台机器系统自带的解包工具真解一次并对拍")
    args = ap.parse_args(argv)

    zp = Path(args.zip).resolve()
    if not zp.is_file():
        print(f"❌ 要验的 zip 不在：{zp}")
        return 2
    problems = check_zip_entries(zp, top=args.top, root_files=tuple(args.root_file))
    if problems:
        print(f"❌ {zp.name} 条目名/形状不合格（{len(problems)} 条）：")
        for p in problems:
            print(f"   · {p}")
    else:
        with zipfile.ZipFile(zp) as zf:
            n = len(zf.infolist())
        print(f"✅ {zp.name} 条目名合格：{n} 个条目全 ASCII、顶层＝{args.top or '(不要求)'}/"
              f"{('，根上必需项 ' + str(args.root_file) + ' 都在') if args.root_file else ''}，"
              f"无重名撞车、无 .. 与反斜杠")
    if args.os_extract:
        ep = extract_with_os_tools(zp, require=tuple(args.require))
        if ep:
            print(f"❌ {zp.name} 用本机系统工具真解一次不合格（{len(ep)} 条）：")
            for p in ep:
                print(f"   · {p}")
        else:
            tools = " / ".join(_tool_names(zp))
            print(f"✅ {zp.name} 在 {sys.platform} 上用系统自带工具（{tools}）各解一次："
                  f"落地清单与条目清单一字节不差")
        # 真解一次的后果必须进退出码：上一版这里只看条目名的 problems，于是「unzip 少
        # 落地一个文件」在 CI 里打印了 ❌ 却退 0，上传照旧——一条只会说话不会拦人的门
        # 比没有门更糟，因为它让后面的人以为有人拦过了。
        problems += ep
    if problems:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
