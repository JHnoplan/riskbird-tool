#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PE 地板体检：这个 exe 拿给老 Windows，加载器会不会当场拒掉。

需求 10B（Win7 兼容通道）唯一能在**没有 Win7 实机**的情况下机械量出来的一格。
为什么量这个：Windows 加载器在映射镜像时会把可选头里的 `MajorSubsystemVersion /
MinorSubsystemVersion` 与自身版本比对，镜像要得比系统新就直接失败——用户看到的
正是需求 10 那句「装完了打不开 / 不是有效的 Win32 应用程序」，而**构建全程一句错
都不会报**：PyInstaller、makensis、CI 都只关心「我有没有写出这个文件」。也就是说
「能不能在老系统上跑」这件事，在字节里早就写好了答案，只是没人去读。

口径与边界（写在脸上，免得这一格被当成它做不到的证据用）：
  · 这一格证的是**加载器那一关**：子系统版本 + 导入表里有没有 Win7 之后才出现的
    API。两关都过，说明这个镜像不是被系统「凭什么都不让跑」地挡在门外。
  · 这一格**不**证明「在 Win7 上双击一定可用」：UCRT/VC++ 运行库是否装齐、驱动能否
    起来、显卡/字体回退，都不在这张表里。前者由安装器体检补装（需求 10 已交付，
    见 b2bcollect_setup.nsi 的 deps 段），后者必须回 Win7 实机验收。
  · 老系统上能装到的最新 Chrome 是 109，所以兼容通道的随包驱动由
    fetch_chromedrivers.py --max-major 109 从旧桶取——那是另一格，判据在 DE 节。

版本字段位置（PE32 与 PE32+ 同位，不必分支）：可选头 24 起 PE32 有 BaseOfData(4) +
ImageBase(4)，PE32+ 没有 BaseOfData 但 ImageBase 是 8 字节，两者相互抵消，所以
MajorSubsystemVersion 恒在可选头偏移 48。这一句是本脚本敢只按一个偏移读的全部依据。

用法：
  python .github/scripts/check_pe_floor.py <文件…> [--floor 6.1] [--quiet]
退出码：0 全部达标；1 有任一文件不达标或读不出（读不出=红，与第 17 条同一口径：
「这台机器没有该工具」必须是问题而不是打印）。
"""
from __future__ import annotations

import argparse
import fnmatch
import pathlib
import struct
import sys

# 控制台编码必须由脚本自己写定，不能交给环境代码页（与 .github/scripts 里另外几份同源）。
# 本脚本的每一行结论都带中文与箭头符号，而它跑在云端 Windows：英文镜像是 cp1252，写出的
# 当下就抛 UnicodeEncodeError——「这个镜像要的系统比 Win7 新」这句唯一的证据会被那条异常
# 盖掉，日志只剩一片乱码加一个非零退出码。本机（UTF-8）永远复现不出来，所以必须在
# **第一处中文之前**写好这一句（顺序本身由第 41 条硬门判）。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 子系统版本 → 这是「哪个 Windows 版本引入的」那一格，不是猜的：Microsoft 的
# PE 规范里 Major/MinorSubsystemVersion 就是按操作系统版本编的号。
WIN_NAMES = {
    (5, 0): "Windows 2000",
    (5, 1): "Windows XP",
    (5, 2): "Windows XP x64 / Server 2003",
    (6, 0): "Windows Vista",
    (6, 1): "Windows 7",
    (6, 2): "Windows 8",
    (6, 3): "Windows 8.1",
    (10, 0): "Windows 10 / 11",
}

# Win7 之后才出现的 API（函数名 → 引入版本）。只收**有官方出处、且我们真的可能连带
# 引入**的那几个：0xc0000139(ENTRYPOINT_NOT_FOUND) 在老系统上的成因就是导入表里有
# 它不认识的名字，而它在构建机上活得好好的——构建机是 Win11。
# 这张表是**地板筛查**，不是 Win7 API 全集：命中且**随包拿不出出处**才红，不命中≠证明齐全。
# 「随包出处」由 BundleIndex 实测（同一个目录里那个同名文件的导出表），不靠名字白名单。
POST_WIN7_API = {
    "GetSystemTimePreciseAsFileTime": "Win8",   # kernel32
    "WaitOnAddress": "Win8",                    # kernel32（Synchronization apiset）
    "WakeByAddressSingle": "Win8",
    "WakeByAddressAll": "Win8",
    "CreateFile2": "Win8",                      # kernel32（文件 API 的新入口）
    "SetThreadDescription": "Win10",            # kernel32（调试/性能工具最爱带）
    "GetThreadDescription": "Win10",
    "LoadLibraryExA": None,                     # 老函数，显式不收：占位防误删（见下 None 过滤）
    "RoInitialize": "Win8",                     # combase（WinRT）
    "WinRTGetActivationFactory": "Win8",
}
POST_WIN7_API = {k: v for k, v in POST_WIN7_API.items() if v}

# 这些 DLL 本身就是 Win8+ 才存在的 api-ms-win 契约，老系统上连模块都找不到。
POST_WIN7_DLL = {
    "api-ms-win-core-memoryalias-l1-1-0.dll": "Win8",
    "api-ms-win-core-realtime-l1-1-1.dll": "Win8",
    "api-ms-win-core-synch-l1-2-0.dll": "Win8",   # WaitOnAddress 那一族住这里
    "api-ms-win-ro-tyeresolver-l1-1-0.dll": "Win8",
    "mmdevapi.dll": "Vista",
}

FLOOR_DEFAULT = (6, 1)          # 6.1 = Windows 7（兼容通道的目标地板）


class PeError(Exception):
    """镜像读不出地板：调用方一律按红处理，不许当成「跳过」。"""


def _rva_to_off(rva, sections):
    for va, vs, raw_ptr, raw_size in sections:
        if va <= rva < va + max(vs, raw_size):
            return raw_ptr + (rva - va)
    raise PeError(f"RVA 0x{rva:X} 落在任何节区之外（镜像被改过或加了覆盖式数据）")


def _cstr(data: bytes, off: int, what: str) -> str:
    # 这一份必须是 raw docstring：正文里要照抄 `b"\x00"` 这段字面写法给读代码的人看，
    # 普通串会先把 \x00 解成一个真 NUL 塞进 __doc__——源码上看着是四个字符，运行时
    # 是零字节，读 __doc__ 的门（第 34 条转义那一族）与读源码的人是两份真相。
    r"""读一个 NUL 结尾的 ANSI 串；**没读到结束符之前不许返回**。

    直觉写法是 `data[off:].split(b"\x00")[0]` 或 `.index(b"\x00")`，但镜像被截断时
    结束符根本不在文件里：`.index()` 抛 ValueError 把整道门炸掉（2026-11-20 用合成
    PE 夹具探到，导入表的 DLL 名被切断时抛 `ValueError: subsection not found`）。
    门自己抛栈的时候，日志里既没有文件名也没有「哪一格不合格」——比误判更难查。
    所有读字节的出口统一落到 PeError，由 check_one 换成带文件名的红话。
    """
    end = data.find(b"\x00", off)
    if end < 0:
        raise PeError(f"{what}在偏移 0x{off:X} 处没有 NUL 结束符（镜像被截断或被改过）")
    return data[off:end].decode("latin-1")


def parse_pe(data: bytes) -> dict:
    """读出「加载器会不会要一台比地板更新的机器」所需的全部字段。"""
    if len(data) < 0x40 or data[:2] != b"MZ":
        raise PeError("开头不是 MZ：这不是一个 Windows 镜像")
    (pe_off,) = struct.unpack_from("<I", data, 0x3C)
    if pe_off + 24 > len(data) or data[pe_off:pe_off + 4] != b"PE\x00\x00":
        raise PeError(f"e_lfanew=0x{pe_off:X} 处找不到 PE 签名（老式 DOS 程序？本工具不发这种东西）")
    # COFF 文件头：Machine(2) NumberOfSections(2) TimeDateStamp(4) PointerToSymbolTable(4)
    # NumberOfSymbols(4) **SizeOfOptionalHeader(2)** Characteristics(2)。
    # 那三个 4 字节字段共 12 字节，不能按「两个 short」跳——本机首跑就是栽在这里：
    # 错读到的 size_opt=0 让判据在自己造的那道检查上拦下真产物（偏移类错误读代码读
    # 不出来，只能拿真字节对）。
    machine, nsec, _, _, _, size_opt, _chars = struct.unpack_from("<HHIIIHH", data, pe_off + 4)
    if size_opt < 70:
        raise PeError(f"可选头只有 {size_opt} 字节，读不到子系统版本字段（需要 ≥70）")
    opt = pe_off + 24
    (magic,) = struct.unpack_from("<H", data, opt)
    if magic not in (0x10B, 0x20B):
        raise PeError(f"可选头魔数 0x{magic:X} 不是 PE32/PE32+")
    # 恒在偏移 48（依据见文件头那一句），所以这里不区分 32/64 位。
    mj, mn = struct.unpack_from("<HH", data, opt + 48)
    subsys, = struct.unpack_from("<H", data, opt + 68)
    out = {
        "machine": {0x8664: "x64", 0x14C: "x86", 0xAA64: "ARM64"}.get(machine, f"0x{machine:X}"),
        "bits": 32 if magic == 0x10B else 64,
        "subsystem_major": mj,
        "subsystem_minor": mn,
        "subsystem": subsys,
        "imports": set(),          # (dll 小写, 函数名)；按名字导入才有意义，序号导入记 None
        "dlls": set(),             # 直接依赖的模块名（小写）
        "exports": set(),          # 这个镜像自己导出哪些名字（小写）
    }
    # 数据目录：PE32 从可选头 96 起、PE32+ 从 112 起，每项 8 字节；[1] 是导入表。
    dd_off = opt + (96 if magic == 0x10B else 112)
    # 节表紧跟在可选头之后，位置由**文件自己声明的 size_opt** 定，不是由我假定的
    # 224 定：上面那条偏移 bug 之所以能一路骗过抽样，就是因为我手算的 464 恰好等于
    # 「假定的标准 PE32 可选头」落点——真产物在这里是巧合，换个链接器就是静默错位。
    sec_off = pe_off + 24 + size_opt
    sections = []
    for i in range(nsec):
        base = sec_off + i * 40
        if base + 40 > len(data):
            raise PeError(f"第 {i} 个节区头越界（镜像截断）")
        vsize, va, rawsize, rawptr = struct.unpack_from("<IIII", data, base + 8)
        sections.append((va, vsize, rawptr, rawsize))
    imp_rva, imp_size = struct.unpack_from("<II", data, dd_off + 8)
    # 数据目录[0] = 导出表。**必须在这个函数返回之前读完**：下面要拿导出表去交叉核对的
    # 恰恰就是 api-ms-win-* 契约桩，而它们正是「有导出、没导入」那一形——把导出表放
    # 在导入解析之后读，就会在唯一需要它的那批文件上拿不到（本文件里那条早退）。
    out["exports"] = set()
    exp_rva, _exp_size = struct.unpack_from("<II", data, dd_off)
    if exp_rva:
        eoff = _rva_to_off(exp_rva, sections)
        # IMAGE_EXPORT_DIRECTORY：NumberOfNames(24) / AddressOfFunctions(28) / AddressOfNames(32)。
        # 按序号导出、名字表里没有的项进不了集合，交叉核对那一格于是对它**保守判红**——
        # 少放行一次比多放行一次便宜。
        nnames, _addr_fn, addr_nm = struct.unpack_from("<III", data, eoff + 24)
        nm_base = _rva_to_off(addr_nm, sections) if nnames else 0
        for i in range(min(nnames, 100000)):          # 硬上限：畸形计数不许把这里变成死循环
            (nrva,) = struct.unpack_from("<I", data, nm_base + i * 4)
            if nrva:
                out["exports"].add(
                    _cstr(data, _rva_to_off(nrva, sections), "导出名").lower())
    # 没导入表 = 这个镜像不声明任何 DLL 依赖。**这不是异常形状**：2026-11-20 兼容通道
    # 首运实测，`--scan-dir` 扫到的 123 个镜像里有 43 个是这样的——api-ms-win-crt-*.dll 这类
    # API-set 契约桩与 python3.dll 这类**只转发、不实现**的 DLL，结构上就没有导入表。
    # 旧写法在这里直接 raise（当时那句「真 PyInstaller 产物不可能这样」只对**主 exe** 成立，
    # 换成整目录扫描就超出适用范围了），后果是 43 条「读不出地板」的假红——而且它们与
    # 真缺陷（被壳压掉导入表）长一模一样，读日志的人分不出哪条要命。
    # 现在：地板照读（它只需要 OptionalHeader），导入集合置空，由调用方如实说
    # 「这一格没有 API 名单可筛」——宁可少判一格并把它说出来，不可把少判报成不合格。
    out["no_imports"] = not imp_rva
    if out["no_imports"]:
        return out                    # 早退而不是给下面整段循环加一层缩进：改动面要看得见
    off = _rva_to_off(imp_rva, sections)
    while True:
        ilt_rva, _, _, name_rva, iat_rva = struct.unpack_from("<IIIII", data, off)
        if not (ilt_rva or name_rva or iat_rva):
            break                                     # 全零 = 描述符数组结束
        dll = _cstr(data, _rva_to_off(name_rva, sections), "导入表 DLL 名").lower()
        out["dlls"].add(dll)
        thunk_rva = ilt_rva or iat_rva
        if thunk_rva:
            tsrc = _rva_to_off(thunk_rva, sections)
            fmt = "<Q" if out["bits"] == 64 else "<I"
            ordinal_flag = 1 << (63 if out["bits"] == 64 else 31)
            for k in range(4096):                     # 硬上限：畸形镜像不许把这里变成死循环
                (val,) = struct.unpack_from(fmt, data, tsrc + k * (8 if out["bits"] == 64 else 4))
                if val == 0:
                    break
                if val & ordinal_flag:
                    out["imports"].add((dll.lower(), None))   # 按序号导入：名字读不出，如实记 None
                    continue
                nmoff = _rva_to_off(val & ~ordinal_flag & 0x7FFFFFFF, sections)
                fname = _cstr(data, nmoff + 2, "导入函数名")   # +2 跳过 Hint 字段
                if fname:
                    out["imports"].add((dll.lower(), fname))
        off += 20
    return out


class BundleIndex:
    """随包清单：把「同一个产物目录里还有哪些二进制」变成可查的表。

    动机（2026-11-20 兼容通道 run#2）：门报了 `ucrtbase.dll 依赖
    api-ms-win-core-synch-l1-2-0.dll，老系统上连模块都加载不到`，而同一次扫描显示
    **这个桩自己就在包里**（那 43 个 api-ms-win-* 契约桩之一）。「加载不到」于是从
    一句要靠回忆微软 API-set 支持矩阵才敢说出的话，变成一个**可以拿包里的字节对拍**
    的事实。这张表就是那次对拍：宁可读实测，不拿名单换结论。

    读一个缓一个：123 个镜像里通常只有一个命中，不该为此把整包（149MB）读进内存。
    """

    def __init__(self, by_name: dict):
        self.by_name = dict(by_name)      # 小写文件名 -> Path
        self._cache = {}
        self.supplied = 0                 # 交叉核对后确认「随包桩实测提供」的那几格（给结论行）

    def exports_of(self, dll_lower: str):
        """(随包在位?, 导出名集合或 None)。集合为 None = 读不出，调用方按最严口径判。"""
        if dll_lower not in self.by_name:
            return False, None
        if dll_lower not in self._cache:
            try:
                raw = self.by_name[dll_lower].read_bytes()
                self._cache[dll_lower] = set(parse_pe(raw)["exports"])
            except (OSError, ValueError, struct.error, UnicodeDecodeError, PeError) as e:
                # 「读不出」不等于「没有」：保留在位这一格、集合给 None，让调用方把
                # 读不出说出来并退回红——把读不出当成「筛过了」就是这一族缺陷的原形。
                print(f"· 随包桩 {dll_lower} 的导出表读不出（{type(e).__name__}: "
                      f"{str(e).strip()}）：交叉核对这一格退回最严口径")
                self._cache[dll_lower] = None
        return True, self._cache[dll_lower]


def check_one(path, raw: bytes, floor, bundle=None) -> list:
    """返回问题列表（空 = 达标）。不给 bundle 就是空表：那一律按最严口径判红。"""
    problems = []
    try:
        pe = parse_pe(raw)
    except PeError as e:
        return [f"{path.name}: 读不出地板（{e}）"]
    except struct.error as e:
        return [f"{path.name}: 镜像结构异常（struct: {e}）"]
    got = (pe["subsystem_major"], pe["subsystem_minor"])
    label = WIN_NAMES.get(got)
    if label is None:
        # 不猜名字：本表没登记就直说没登记，并给出它与地板的大小关系。
        # （把 4.00 当成「不认识 => 有问题」是一种误伤：NSIS 写的就是 4.00，
        # 意思反而更宽——它比任何还在卖的 Windows 都老。）
        label = ("早于本表登记的最老一项" if got < min(WIN_NAMES)
                 else f"本表未登记（{got[0]}.{got[1]:02d}）")
    named = [1 for _, f in pe["imports"] if f]
    by_ordinal = len([1 for _, f in pe["imports"] if f is None])
    if pe.get("no_imports"):
        # 把「这一格筛不到」说出来，而不是静默给一个看起来像「筛过了、没问题」的 ✅。
        # 旧写法是把这一形状报成「读不出地板」的红（43 条假红）；反过来完全不提又是一个
        # 没人知道的盲区。两种都比「说出来」贵。
        print(f"· {path.name}: {pe['machine']} PE{pe['bits']}，子系统版本 "
              f"{got[0]}.{got[1]:02d}（≈最早 {label}），子系统号 {pe['subsystem']}，"
              f"不声明导入表（API-set 契约桩/转发 DLL 的正常形态）："
              f"地板这一格照判，API 名单筛查对这一格没有输入")
    else:
        print(f"· {path.name}: {pe['machine']} PE{pe['bits']}，子系统版本 "
              f"{got[0]}.{got[1]:02d}（≈最早 {label}），子系统号 {pe['subsystem']}，"
              f"导入 {len(pe['dlls'])} 个模块 / {len(named)} 个具名函数"
              + (f" / {by_ordinal} 项按序号导入（名字读不出，API 名单筛查对它们瞎）" if by_ordinal else ""))
    if got > tuple(floor):
        problems.append(
            f"{path.name}: 要求 Windows {got[0]}.{got[1]}，高于兼容通道地板 "
            f"{floor[0]}.{floor[1]}——老系统的加载器会**当场拒载**，而构建一句错都不报")
    if got == (0, 0):
        problems.append(f"{path.name}: 子系统版本是 0.00（被壳/被改过的常见形态），"
                        "这句话不能替它担保能在老系统上加载")
    # 排序键必须把 None 折成空串：真产物里就有按序号导入的项（2026-11-20 拿线上
    # 安装包首跑就炸在 sorted 上：None 与 str 不可比）。判据自己抛栈比误判更糟——
    # 读日志的人拿到一屏 traceback，看不出这一格到底判了什么。
    if bundle is None:
        bundle = BundleIndex({})
    for dll, fn in sorted(pe["imports"], key=lambda x: (x[0], x[1] or "")):
        if fn and fn in POST_WIN7_API:
            in_bundle, exps = bundle.exports_of(dll)
            if exps is not None and fn.lower() in exps:
                bundle.supplied += 1
                print(f"· {path.name}: {dll}!{fn}（{POST_WIN7_API[fn]} 起）——随包桩实测**导出了这一格**"
                      f"（{dll} 在包内，名字表里有它）：不按缺入口点判红")
                continue
            tail = ("随包桩在位，但它的导出表里没有这一格" if in_bundle and exps is not None
                    else "随包桩的导出表读不出" if in_bundle
                    else "包里没有同名模块可比对，按最严口径判")
            problems.append(f"{path.name}: 导入了 {dll}!{fn}，该函数是 {POST_WIN7_API[fn]} 才有的"
                            f"——老系统上会报 0xc0000139（找不到入口点）；{tail}")
    for dll in sorted(pe["dlls"]):
        if dll in POST_WIN7_DLL:
            in_bundle, exps = bundle.exports_of(dll)
            if in_bundle and exps is not None:
                print(f"· {path.name}: {dll}（{POST_WIN7_DLL[dll]} 起的契约模块）随包在位，"
                      f"实测导出 {len(exps)} 个名字：模块这一格不按缺失判红，函数名单独判")
                continue
            problems.append(f"{path.name}: 依赖 {dll}，这个模块本身是 {POST_WIN7_DLL[dll]} 才有的"
                            + ("——老系统上连模块都加载不到" if not in_bundle
                               else "；随包桩在位但导出表读不出，按最严口径判"))
    return problems


def _read_ranges(url: str, span: int = 1 << 16, grow: int = 4, hard_cap: int = 1 << 22) -> bytes:
    """远程读法（--url 用）：**递进式** Range 取头部，够 parse_pe 就停。

    为什么值得做，以及为什么必须递进：线上安装包 98 MB，而我要读的只是开头几 KB 的
    PE 头与导入表。第一版这里写的是「头部不够就整取」——那等于把一次取证写成一次
    全量下载（判据自己踩自己的坑，同第 20 条「每一次 curl 都必须自己会停」）。现在
    按 64 KiB → 256 KiB → 1 MiB → 4 MiB 涨，涨到硬上限还不够就**明说改本地文件**：
    CI 上产物本来就在手边，远程这一路只用来对拍线上已经发出去的字节。

    只用标准库（urllib），不引入 requests——这一格在 CI 上跑在**目标环境装好之前**，
    多一个依赖就多一处「装依赖」的前置条件。
    """
    import urllib.request

    def _get(start: int, end: int):
        req = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}",
                                                  "Accept-Encoding": "identity"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read(), r.headers.get("Content-Range", "")

    size, total = span, None
    while total is None or size <= min(total, hard_cap):
        head, cr = _get(0, size - 1)
        if "/" not in cr:
            # 服务端没理 Range（整取回来）：那就已经是全文，直接判。
            return head
        total = int(cr.rsplit("/", 1)[-1])
        try:
            parse_pe(head)
        except PeError:
            size *= grow
            continue
        print(f"· 远程 Range 取证：只取头部 {len(head)} 字节就读到了地板"
              f"（全文 {total:,} 字节，Content-Range: {cr}）")
        return head
    raise SystemExit(f"{url} 取到 {min(size, hard_cap):,} 字节仍读不出地板（全文 {total:,} 字节）："
                     "镜像把表放在更靠后的位置，这一格改成本地文件去量——"
                     "宁可不给结论，也不要为了出结论拖一整包下来")


def _collect(targets, scan_dirs, problems: list) -> list:
    """把「文件 / 目录」两种写法汇成一份待检清单；拿不到的直接记进 problems。

    为什么要扫整目录而不是只扫主 exe：产品的真实地板是**它加载的每一个二进制里最高的
    那一格**——python311.dll、Qt5Core.dll、随包 chromedriver、selenium-manager.exe 任一
    格要求高于地板，老系统上就是「主程序能开、点开某个功能才挂」，而那比开不了更难查。
    只测入口的结论等于没测。

    目录不在位必须**回传到退出码里**（2026-11-20 自检当场抓住的缺陷）：第一版只在
    这里 print 一句 ::error:: 就继续往下跑，结果是「喊了错、退了 0、还打了一句全部
    达标」——而 workflow 只看退出码。喊错不是判定，返回码才是。
    """
    out = []
    for t in targets:
        out.append(pathlib.Path(t))
    for d in scan_dirs:
        root = pathlib.Path(d)
        if not root.is_dir():
            problems.append(f"--scan-dir {d} 指向的目录不在位：一个没扫的产物不能叫「地板达标」")
            continue
        found = sorted(x for x in root.rglob("*")
                       if x.is_file() and x.suffix.lower() in (".exe", ".dll", ".pyd", ".ocx"))
        if not found:
            problems.append(f"--scan-dir {d} 里没有任何 PE（.exe/.dll/.pyd/.ocx）："
                            "路径写错或产物还没构建，都不是「扫过了」")
            continue
        print(f"· --scan-dir {d}：{len(found)} 个 PE 候选（.exe/.dll/.pyd/.ocx）")
        out.extend(found)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PE 地板体检（子系统版本 + 导入表 API）")
    ap.add_argument("targets", nargs="*", help="要检的 .exe / 镜像文件；配合 --url 时是 URL 路径后缀")
    ap.add_argument("--scan-dir", action="append", default=[],
                    help="递归扫该目录下每一个 PE（产物目录用这个：真实地板由全体决定）")
    ap.add_argument("--floor", default="%d.%d" % FLOOR_DEFAULT,
                    help="地板版本，默认 6.1（Windows 7）。高于它就红")
    ap.add_argument("--url", default="", help="从固定直链远程取证（Range 只取头部，用于对拍线上产物）")
    # 【实测剪】三个参数默认全空，因此**不传就一个字节也不会动**：门的本职是判定，删文件
    # 是操作者显式交给它的第二件事。为什么需要：2026-11-20 兼容通道实测到
    # selenium-manager.exe 静态导入 kernel32!GetSystemTimePreciseAsFileTime（Win8 起）——
    # 它在 Win7 上**开不起来**（而 Selenium 兜底那一步的文案还承诺给用户现场下驱动）。
    # 把门调松是拿一个用户换一次红，剪掉才是把那块加载不了的东西从包里拿出去。
    ap.add_argument("--prune-match", action="append", default=[],
                    help="名字匹配这个通配、且本趟实测为「这台地板加载不了」的随包文件，剪掉后复检；"
                         "不给这个参数就只报不剪")
    ap.add_argument("--prune-keep", action="append", default=[],
                    help="即便匹配通配也**绝不许剪**的名字（入口 exe 给在这里）")
    ap.add_argument("--max-prune", type=int, default=4,
                    help="一次最多剪几个：超出就是布局不是我以为的那样，停下来问人而不是接着删")
    ap.add_argument("--quiet", action="store_true", help="只打印结论行")
    args = ap.parse_args(argv)

    try:
        fmaj, fmin = (int(x) for x in args.floor.split("."))
    except ValueError:
        print(f"::error::--floor 写法不对（要 6.1 这样）：{args.floor!r}")
        return 1
    floor = (fmaj, fmin)

    if not args.targets and not args.scan_dir:
        print("::error::一个目标都没给：这一格没有输入就不该输出「达标」")
        return 1

    base_problems = []
    candidates = _collect(args.targets, args.scan_dir, base_problems)
    # 随包交叉核对表只在**本地文件**模式下有意义：--url 远程取证只 Range 取了每个镜像的
    # 头部，读不到邻居的导出表，那一格就只能按最严口径判红——差别必须说出来，否则
    # 「本地扫过 = 达标」与「远程扫过 = 达标」会变成两句不同的话。
    if args.url:
        print("· 随包交叉核对：关（--url 只取了镜像头部，没有随包清单可比对）——命中一律按最严口径判红")
    else:
        print(f"· 随包交叉核对：开（{len([p for p in candidates if p.is_file()])} 个随包模块在表里，"
              f"命中时去实测它的导出表）")

    def _bundle_of(paths):
        if args.url:
            return BundleIndex({})
        return BundleIndex({p.name.lower(): p for p in paths if p.is_file()})

    def _scan(paths):
        """跑一趟扫描，返回（每个文件的问题、检了几个、随包表）。

        随包表要交出来：它上面的 supplied 是「实测放行了几格」的唯一口径；
        剪完要复检，所以这一趟得能重跑（重跑就是重建一张表，剪掉的不再在里面）。
        """
        bundle = _bundle_of(paths)
        by_file, n = {}, 0
        for p in paths:
            if args.url:
                raw = _read_ranges(args.url.rstrip("/") + "/" + str(p))
                name = pathlib.PurePosixPath(str(p)).name
            else:
                if not p.is_file():
                    by_file[p] = [f"{p}: 文件不在位（不许把「读不到」说成「达标」）"]
                    continue
                raw = p.read_bytes()
                name = p.name
            n += 1
            by_file[p] = check_one(pathlib.Path(name), raw, floor, bundle)
        return by_file, n, bundle

    by_file, checked, bundle = _scan(candidates)
    problems = base_problems + [m for msgs in by_file.values() for m in msgs]

    # —— 实测剪：只在显式给了 --prune-match 时才动手，剪完必定复检一次 ——
    # 四道安全框：远程镜像不剪（剪不到还谎称剪过）、--prune-keep 不剪（入口 exe）、
    # 不在 --scan-dir 里的不剪（被点名的单个文件是要**判**的）、一次剪太多不剪（停下来问人）。
    if args.prune_match:
        if args.url:
            print("::error::--prune-match 与 --url 不能同时用：远程镜像剪不掉，"
                  "别把「报了错」当成「剪过了」")
            return 1

        def _under_scan(p) -> bool:
            for d in args.scan_dir:
                try:
                    p.resolve().relative_to(pathlib.Path(d).resolve())
                    return True
                except (ValueError, OSError):
                    continue
            return False

        keep = {x.lower() for x in args.prune_keep}
        hit = [p for p, msgs in by_file.items()
               if msgs and any(fnmatch.fnmatch(p.name.lower(), pat.lower())
                               for pat in args.prune_match)]
        blocked = sorted({p.name for p in hit if p.name.lower() in keep})
        if blocked:
            print(f"::error::--prune-keep 里的名字被通配命中了：{'、'.join(blocked)}——"
                  "入口 exe 被剪掉的话，这一包剩下的就是一堆开不了的零件")
            return 1
        hit = [p for p in hit if p.name.lower() not in keep]
        outside = sorted(str(p) for p in hit if not _under_scan(p))
        if outside:
            print(f"::error::这些命中文件不在任何 --scan-dir 里：{'、'.join(outside)}——"
                  "被明确点名的目标是要判的，不是要删的")
            return 1
        if len(hit) > args.max_prune:
            print(f"::error::实测剪要剪 {len(hit)} 个（上限 {args.max_prune}）："
                  "一次剪这么多说明布局不是我以为的那样，停下来问人——名单："
                  + "、".join(sorted(p.name for p in hit)))
            return 1
        if not hit:
            # 剪不到不算红：真正兜底的是本趟判定本身（还有问题就会退 1）。
            # 但这一格今天到底做没做事必须说出来，否则下一步 selenium 换了名字、
            # 或者这个 helper 干脆不随包了，日志里看不出区别。
            print(f"· --prune-match {'、'.join(args.prune_match)} 一个都没命中："
                  "本趟没有「实测加载不了又可剪」的随包文件")
        else:
            for p in sorted(hit):
                print(f"· 实测剪：{p.name} —— 依据：{by_file[p][0]}")
                try:
                    p.unlink()
                except OSError as e:
                    base_problems.append(f"{p.name}: 判定要剪但它没剪掉（{e}）："
                                         "留在包里的还是一个加载不了的二进制")
            rest = [p for p in candidates if p not in set(hit)]
            print(f"· 实测剪：剪掉 {len(hit)} 个，复检剩下 {len(rest)} 个镜像")
            by_file, checked, bundle = _scan(rest)
            problems = base_problems + [m for msgs in by_file.values() for m in msgs]

    # 结论行带上「实测放行多少格」：这一格与「不达标 N 项」同一个口径，读日志的人
    # 才能分辨「全部达标」是筛过了，还是根本筛不到。计数来自**最后一趟**扫描的那张表。
    extra = (f"，其中 {bundle.supplied} 格经随包桩导出表实测放行" if bundle.supplied else "")
    if problems:
        for msg in problems:
            print(f"::error::{msg}")
        print(f"结论：检了 {checked} 个镜像，{len(problems)} 项不达标"
              f"（地板 {floor[0]}.{floor[1]}{extra}）")
        return 1
    print(f"结论：检了 {checked} 个镜像，加载器这一关全部达标（地板 {floor[0]}.{floor[1]}{extra}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
