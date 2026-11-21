#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""装配期把包里的 UCRT 换成 Win7 兼容版（需求 10B / 台账第 11 项，兼容通道独有步骤）。

由来（两轮取证，都在 2026-11-21）：
  第一轮（legacy run#4 @870a8a9）：PE 地板门报 42 项红，全是 PyInstaller 从 Win11
  runner 的 System32 扫依赖带进来的 api-ms-win-* 存根。但光剪存根不够——包内那份
  Win11 版 ucrtbase.dll 自己就静态依赖 api-ms-win-core-synch-l1-2-0.dll（Win8 才有
  的契约模块），存根一剪它立刻变红。所以必须整套换 Win7 版 UCRT。
  第一版修法取 Windows SDK 的 Redist\ucrt——云端 run#5 当场证伪：runner 只有
  26100 一档，那份 ucrtbase 是 Win10 构建，照样依赖 synch-l1-2-0。第二轮把三条
  「完整 Win7 UCRT」的路逐一实测：KB2999226 独立 MSU 内层全是 PA delta（确认没有
  完整字节）；旧档 SDK（确认没有）；**VS2015U3 redist（14.0.24215.1）的附加容器
  a10 = 完整 app-local UCRT 套件 47 件**（ucrtbase 子系统 5.02 + 46 个转发器，
  全 x64）——本机从官方原件逐字节解出并与微软部署形态对拍过。

为什么从 redist exe 里抽而不是装 SDK：
  redist 是 burn 容器：顶层两个 MSCF（c0=UX、c1=附加容器），c1 里的 a10 就是
  UCRT 套件。本脚本用**纯标准库**（struct+zlib）解 MSCF/MSZIP，不依赖 expand /
  cabextract / 7z——「存在」不等于「本平台可执行」（macOS 的 /usr/bin/expand 是
  GNU 文本工具，同名不同物；云端 Windows 的 expand.exe 又是另一回事），判据与
  交付走同一份代码，离线可复现。a10 实测是单 folder MSZIP；跨块字典用 zlib 的
  FDICT+zdict 语义还原（本机 47/47 逐字节对拍过）。只支持 none/MSZIP，遇到
  LZX/Quantum 明说解不出并 fail-closed——不猜。

自证式设计（不凭记忆写 Windows 行为）：
  ① 替换前用 check_pe_floor.check_one 按同一把尺子量候选 ucrtbase，**随包桩表
     给 a10 自己的桩集**：VS2015 版 ucrtbase 也依赖 synch-l1-2-0，但 a10 自带那个
     桩且实测导出 Sleep——不给桩表就会把微软官方件误判成不合格（run#5 的教训）。
  ② 替换前后各做一次覆盖对拍（dist 里每个 PE 的 api-ms-win-* 具名导入都要能在
     包内同名桩的导出表里实测到），**只对替换新增的缺口判红**：本机正控实测，
     原始 dist 本来就有 21 条存量缺口（Qt5Bluetooth/qtaudio_wasapi 的 winrt 桩、
     python311 的 path 桩不在包内）——那些是 PE 门黑名单口径外的另一族问题，
     不是这一步的责任；把存量算进替换责任就是把判据前提超出适用范围（docs/06
     那一族）。这一步的担保是：**替换不新增任何缺口**，存量如实报数不吞。
  云端 PE 门随后还会对整个 dist 复检一次，两层量的是同一份字节。

诚实边界：本脚本只保证「换进去的 UCRT 自身过加载器那一关、替换不新增契约导入
缺口」。存量缺口（winrt/path 桩不在包）与 DLL 子系统地板对 DLL 不适用（2026-11-21
用户拍板，见 check_pe_floor 文件头）是另外两格；core 存根剪掉后其余镜像是否还有
暗依赖，由紧随其后的 PE 地板门说了算。
"""
import argparse
import pathlib
import shutil
import struct
import sys
import tempfile
import zlib

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


class CabError(ValueError):
    """cab/redist 读不出：调用方一律落成红话+退出码，不许当成「跳过」。"""


def _mszip_block(hist: bytes, comp: bytes) -> bytes:
    """解一个 MSZIP 块（CK 签名后面的 deflate 数据）。

    MSZIP 的跨块引用＝zlib 预设字典语义：把前块明文尾 32KB 当 zdict。Python 的
    raw inflate（-15）不认字典，所以这里补一个 FDICT 置位的 zlib 头（DICTID＝
    字典的 adler32），交给 decompressobj(zdict=…)。本机拿 a10 的 125 个块逐字节
    对拍过；第一版想拿「历史伪装 stored 块」绕，被 adler 校验当场打回。
    """
    if not hist:
        return zlib.decompress(comp, -15)
    stream = b"\x78\xbb" + struct.pack(">I", zlib.adler32(hist)) + comp
    try:
        return zlib.decompressobj(zdict=hist).decompress(stream)
    except zlib.error as e:
        raise CabError(f"MSZIP 块解不出（{e}）：容器字节被改过，或压缩形态超出本脚本口径")


def cab_files(d: bytes) -> dict:
    """解一个 MSCF 容器，返回 {文件名: 字节}。只支持 none/MSZIP，其余明说解不出。

    布局按**顺序**走（头→reserve→folder 表→文件表→数据块），不信头里的 coffFiles：
    a10 实测该字段是 0（burn 造的容器就这样），照它读会把签名当文件表。这不是猜的，
    是拿 47 件参照逐字节对出来的口径。多卷容器（iCabinet≠0）也明说不支持——
    redist 的 a10 实测是单卷，遇到别的形态宁可红也不猜。
    """
    if len(d) < 36 or d[:4] != b"MSCF":
        raise CabError("开头不是 MSCF：这不是一个 cabinet 容器")
    c_folders, c_files, flags = struct.unpack_from("<HHH", d, 26)
    set_id, i_cabinet = struct.unpack_from("<HH", d, 32)
    if flags & 1 or i_cabinet != 0:
        raise CabError(f"多卷容器（flags={flags:#x} iCabinet={i_cabinet}）不在本脚本口径内："
                       "redist 的 UCRT 套件实测是单卷，遇到别的形态先取证再扩口径")
    off, cb_folder, cb_data = 36, 0, 0
    if flags & 4:                                   # CFHDR_FLAG_RESERVE_PRESENT
        cb_header, cb_folder, cb_data = struct.unpack_from("<HBB", d, off)
        off += 4 + cb_header
    folders = []
    for i in range(c_folders):
        if off + 8 > len(d):
            raise CabError(f"第 {i} 个 folder 头越界（容器截断）")
        start, n_data, comp = struct.unpack_from("<IHH", d, off)
        folders.append((start, n_data, comp & 0xF))
        off += 8 + cb_folder
    files = []
    for i in range(c_files):
        if off + 16 > len(d):
            raise CabError(f"第 {i} 个文件头越界（容器截断）")
        cb_file, u_off, i_folder = struct.unpack_from("<IIH", d, off)
        end = d.find(b"\x00", off + 16)
        if end < 0:
            raise CabError(f"第 {i} 个文件名没有 NUL 结束符（容器被改过）")
        files.append((d[off + 16:end].decode("latin-1"), cb_file, u_off, i_folder))
        off = end + 1
    out = {}
    for fi, (start, n_data, comp) in enumerate(folders):
        if comp not in (0, 1):
            raise CabError(f"folder{fi} 的压缩类型是 {comp}（2=Quantum 3=LZX）："
                           "本脚本只实现了 none/MSZIP（a10 实测是 MSZIP）——解不出就红，不猜")
        pos, hist, parts = start, b"", []
        for bi in range(n_data):
            if pos + 8 > len(d):
                raise CabError(f"folder{fi} 第 {bi} 个数据块头越界")
            _csum, cb_comp, cb_uncomp = struct.unpack_from("<IHH", d, pos)
            pos += 8 + cb_data
            if pos + cb_comp > len(d):
                raise CabError(f"folder{fi} 第 {bi} 个数据块越界（容器截断）")
            if comp == 0:
                raw = d[pos:pos + cb_comp]
            else:
                if d[pos:pos + 2] != b"CK":
                    raise CabError(f"folder{fi} 第 {bi} 块缺 CK 签名：MSZIP 形态被改过")
                raw = _mszip_block(hist, d[pos + 2:pos + cb_comp])
            if len(raw) != cb_uncomp:
                raise CabError(f"folder{fi} 第 {bi} 块明文 {len(raw)} 字节 ≠ 声明的 {cb_uncomp}")
            hist = (hist + raw)[-32768:]
            parts.append(raw)
            pos += cb_comp
        blob = b"".join(parts)
        for name, cb_file, u_off, i_folder in files:
            if i_folder == fi:
                if u_off + cb_file > len(blob):
                    raise CabError(f"{name} 声明的字节范围超出 folder{fi} 的明文（容器被改过）")
                out[name] = blob[u_off:u_off + cb_file]
    return out


def _norm_name(name: str) -> str:
    """a10 里的文件名是下划线形（api_ms_win_crt_runtime_l1_1_0.dll）——burn 容器
    不收点号分隔的长名。落盘前归一化成包里的点号形；只动 api_ms_ 前缀那一族，
    vcruntime140.dll 这类正常名字一个字符都不碰。"""
    return name.replace("_", "-") if name.lower().startswith("api_ms_") else name


def extract_ucrt_from_redist(redist: pathlib.Path) -> pathlib.Path:
    """从 VS2015U3 redist exe 里抽出含 ucrtbase.dll 的那套容器（实测=a10），
    归一化文件名后写进临时目录，返回目录路径。找不到/解不出就 raise CabError。

    扫法：按 MSCF 魔数找候选、按头里的 cbCabinet 切块再试解——压缩数据里碰巧
    出现 MSCF 四个字节时候选会解不出，跳过继续找（真容器解得出，假候选解不出，
    用「解得出且含 ucrtbase」认领，不靠偏移记忆）。顶层 c1 里的 a0-a11 本身又是
    MSCF，逐个试解，认领含 ucrtbase.dll 的那一个。
    """
    data = redist.read_bytes()
    hits, i = [], 0
    while True:
        j = data.find(b"MSCF", i)
        if j < 0:
            break
        if j + 12 <= len(data):
            cb_cab = struct.unpack_from("<I", data, j + 8)[0]
            if 36 < cb_cab <= len(data) - j:
                hits.append((j, cb_cab))
                i = j + cb_cab
                continue
        i = j + 4
    if not hits:
        raise CabError(f"{redist.name} 里找不到任何 MSCF 容器：这不是 VS2015U3 redist 原件"
                       "（burn 容器才有两个 MSCF）。下一步：核对下载 URL 与 sha256 钉")
    found = []
    for off, size in hits:
        try:
            top = cab_files(data[off:off + size])
        except CabError:
            continue                                # 假候选（压缩数据里的巧合魔数）
        for name, blob in top.items():
            if blob[:4] != b"MSCF":
                continue
            try:
                inner = cab_files(blob)
            except CabError:
                continue
            if "ucrtbase.dll" in inner:
                found.append((name, inner))
    if not found:
        raise CabError(f"{redist.name} 的 {len(hits)} 个 MSCF 容器里没有一套含 ucrtbase.dll："
                       "版本不对（要 VS2015U3 14.0.24215.1，它的附加容器 a10 才是完整 Win7 UCRT）"
                       "——下一步：核对 workflow 钉的 URL/sha256，别拿新版 redist 凑数")
    if len(found) > 1:
        raise CabError(f"{redist.name} 里有多套含 ucrtbase 的容器（{[n for n, _ in found]}）："
                       "口径外的形态，先取证再动手，不猜哪一套是对的")
    name, inner = found[0]
    dst = pathlib.Path(tempfile.mkdtemp(prefix="win7_ucrt_"))
    for fn, blob in sorted(inner.items()):
        (dst / _norm_name(fn)).write_bytes(blob)
    print(f"· 已从 {redist.name} 抽出 UCRT 套件（容器 {name}，{len(inner)} 件，"
          f"文件名已归一化）→ {dst}")
    return dst


def _bundle_of(d: pathlib.Path) -> "pe.BundleIndex":
    """把目录里的 DLL 编成随包桩表（check_one 交叉核对用），不抄第二份尺子。"""
    return pe.BundleIndex({p.name.lower(): p for p in d.glob("*.dll")})


def validate_ucrtbase(src: pathlib.Path) -> None:
    """替换件必须先过与产物同一把尺子；不合格就 raise，点名是哪一格。

    桩表必须给 src 自己的桩集：VS2015 版 ucrtbase 也依赖 synch-l1-2-0（Win8 契约），
    但 a10 自带那个桩且实测导出它要的那一格（本机解包实测：ucrtbase 导入 Sleep，
    桩导出表里 sleep/waitonaddress 都在）——无桩表的最严口径会把微软官方 Win7 件
    误判成不合格（云端 run#5 就是拿 SDK 件撞了这一格；给对桩表后实测放行）。
    """
    raw = (src / "ucrtbase.dll").read_bytes()
    problems = pe.check_one(pathlib.Path("ucrtbase.dll（Win7 替换件）"), raw, FLOOR,
                            bundle=_bundle_of(src))
    if problems:
        raise ValueError("替换件 ucrtbase.dll 自己就过不了 Win7 地板门，不许换进包：\n  "
                         + "\n  ".join(problems)
                         + "\n下一步：核对 redist 的 URL/sha256 钉（要 VS2015U3 14.0.24215.1），"
                           "或把这份红话贴进台账第 11 项")


def _coverage_problems(internal: pathlib.Path) -> list:
    """覆盖对拍：dist 里每个 PE 的 api-ms-win-* 具名导入，都要能在包内同名桩的
    导出表里实测到。返回缺口清单（调用方自己决定拿它做什么：替换前后各跑一次，
    差分才是替换的责任）。读不出的 PE 也记红（读不出≠没有，第 2 条铁律）。
    """
    bundle = _bundle_of(internal)
    probs = []
    for p in sorted(internal.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in (".dll", ".exe", ".pyd"):
            continue
        try:
            info = pe.parse_pe(p.read_bytes())
        except (pe.PeError, struct.error, OSError, UnicodeDecodeError) as e:
            probs.append(f"{p.name}: 替换后复检读不出（{type(e).__name__}: {str(e).strip()}）")
            continue
        for dll, fn in sorted(info["imports"], key=lambda x: (x[0], x[1] or "")):
            if not dll.startswith("api-ms-"):
                continue                            # 非契约模块交给 PE 门的黑名单那一格
            if fn is None:
                probs.append(f"{p.name}: 对 {dll} 按序号导入，名字读不出——覆盖对拍对它瞎，按最严口径判")
                continue
            _in, exps = bundle.exports_of(dll)
            if exps is None or fn.lower() not in exps:
                tail = ("包里没有同名桩" if not _in else
                        "桩的导出表读不出" if exps is None else "桩的导出表里没有这一格")
                probs.append(f"{p.name}: 导入 {dll}!{fn}，{tail}")
    return probs


def swap(dist: pathlib.Path, src: pathlib.Path) -> int:
    """把 src 里的 ucrtbase + api-ms-win-crt-*（以及 src 备有的 core 桩）换进
    dist/_internal，逐件报账，最后做全量覆盖对拍。"""
    internal = dist / "_internal"
    if not internal.is_dir():
        print(f"::error::{internal} 不在位：PyInstaller 布局变了？先确认产物目录再跑这一步")
        return 1
    # 存量缺口先记账（替换前）：替换只对**新增**缺口负责，存量如实报数不吞。
    before = set(_coverage_problems(internal))
    try:
        validate_ucrtbase(src)
    except ValueError as e:
        # 体检不合格要在这一层就落成红话 + 退出码：直接调 swap() 的调用方（离线判据）
        # 不该接住一个裸栈——判据自己抛栈比误判更难查（DH 节同一课）。
        print(f"::error::{e}")
        return 1
    # crt 转发器必须**整套**换：dist 里有、src 里没有的那几个会留下 Win11 版存根，
    # PE 门照样红——所以缺一个就停，不做半套替换。
    want = sorted(p.name for p in internal.glob("api-ms-win-crt-*.dll"))
    missing = [n for n in want if not (src / n).is_file()]
    if missing:
        print(f"::error::替换源里缺这几个 crt 转发器，半套替换会留下 Win11 存根："
              f"{', '.join(missing)}（src={src}）")
        return 1
    # core 桩：src 备有的那些一并换成 Win7 版（新 ucrtbase 的 synch 依赖就靠它喂）；
    # src 没有的留给下一步 PE 门实测剪——失去依赖方的 Win11 存根剪掉才是正解。
    core = sorted(p.name for p in internal.glob("api-ms-win-core-*.dll")
                  if (src / p.name).is_file())
    targets = ["ucrtbase.dll"] + want + core
    for name in targets:
        old = internal / name
        if not old.is_file():
            print(f"::error::{old} 不在位：包里没有这一件，替换无从对拍（布局变了先查 PyInstaller）")
            return 1
        new = src / name
        old_size = old.stat().st_size      # 先记账再覆盖：覆盖后 stat 拿到的就是新字节了
        shutil.copyfile(new, old)
        print(f"· 已替换 _internal/{name}：{old_size} 字节 ← {new.name}（{new.stat().st_size} 字节）")
    probs = _coverage_problems(internal)
    new = sorted(set(probs) - before)
    if new:
        print("::error::替换新增了覆盖缺口——换进去的 UCRT 喂不饱包里的契约导入，"
              "不许出包：\n  " + "\n  ".join(new[:20])
              + (f"\n  …共 {len(new)} 条" if len(new) > 20 else "")
              + "\n下一步：把这份红话贴进台账第 11 项，先取证是哪一套容器/哪一个桩缺了格")
        return 1
    kept = sorted(set(probs) & before)
    if kept:
        print(f"· 覆盖对拍：存量缺口 {len(kept)} 条替换前后一模一样（不是这一步的责任，"
              "如实报数不吞；它们属于 PE 门黑名单口径外的另一族，见台账第 11 项）：")
        for x in kept[:5]:
            print(f"    {x}")
        if len(kept) > 5:
            print(f"    …共 {len(kept)} 条")
    print(f"结论：UCRT 已整套换成 Win7 兼容版（ucrtbase + {len(want)} 个 crt 转发器"
          f" + {len(core)} 个 core 桩，src={src}）；覆盖对拍无新增缺口，"
          "其余 core 存根交给下一步 PE 地板门实测剪")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="装配期替换 Win7 兼容 UCRT（兼容通道独有）")
    ap.add_argument("--dist-dir", required=True, help="PyInstaller 产物目录（含 _internal）")
    ap.add_argument("--redist", default="", help="VS2015U3 vc_redist.x64.exe 原件（sha256 由 workflow 钉）")
    ap.add_argument("--ucrt-src", default="", help="显式指定已解出的 UCRT 目录（与 --redist 二选一）")
    args = ap.parse_args(argv)
    if bool(args.redist) == bool(args.ucrt_src):
        print("::error::--redist 与 --ucrt-src 必须给且只给一个：前者从官方原件现抽（交付走这条），"
              "后者给已解出的目录（取证/正控用）。下一步：看 workflow 的 UCRT 替换那一步怎么写的")
        return 1
    dist = pathlib.Path(args.dist_dir)
    try:
        if args.redist:
            src = extract_ucrt_from_redist(pathlib.Path(args.redist))
        else:
            src = pathlib.Path(args.ucrt_src)
            if not (src / "ucrtbase.dll").is_file():
                print(f"::error::{src} 里没有 ucrtbase.dll：这不是一个 UCRT 套件目录")
                return 1
        return swap(dist, src)
    except (FileNotFoundError, ValueError) as e:
        print(f"::error::{e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())