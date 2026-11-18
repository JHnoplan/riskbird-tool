#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 b2bcollect_build/pinyin_lite.py（拼音候选通道的**离线建表**脚本）。

为什么要把表生成出来而不是运行时依赖 pypinyin：
  · 交付包多一个第三方依赖，就多一处「这台机器上装没装」的分支。本工具的离线判据
    套件、PyInstaller spec、requirements 三处都得跟着改，而漏掉任何一处的表现是
    「开发机上有拼音候选、用户机器上没有」——那种只在别人机器上失效的功能最难归因。
  · 更关键的是**跨机一致**：pypinyin 升级会改多音字的默认读音，同一个公司名在两个
    版本下拼出不同域名，于是「这轮为什么补到/没补到」失去可复现性。表进了代码，
    读音就是钉死的那一份，判据可复算。
  · 表本身只有 6.7 千个 GB2312 汉字的无声调音节（约 35KB 源码），换来的是零依赖。

用法（在项目根目录）：
    .venv/bin/python debug/gen_pinyin_lite.py            # 重新生成
    .venv/bin/python debug/gen_pinyin_lite.py --check    # 只比对：现表与生成结果是否一致

覆盖范围：GB2312 全部 6763 个汉字（一级字按拼音序、二级字按部首序，两者都逐字取音）。
范围外的字（繁体、生僻扩展区）返回空串——调用方**必须**按「拼不出来」处理而不是
硬凑一个音，否则域名候选就从「可能不对」变成「凭空造」。
"""
from __future__ import annotations

import io
import os
import sys

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "b2bcollect_build", "pinyin_lite.py")

HEADER = '''# -*- coding: utf-8 -*-
"""pinyin_lite.py —— 纯标准库的「汉字 → 无声调拼音音节」查表（官网拼音候选的唯一用表）。

【这个文件是生成的，不要手改】改它请改 ``debug/gen_pinyin_lite.py`` 再重新生成；
``bash debug/run_offline_regression.sh`` 里有对拍判据，手改或表与生成器分家会当场判红。
生成时用的是 pypinyin 0.55.0 的默认读音（多音字取最常见一读），版本换代会改变
候选域名，所以读音必须钉死在这一份表上——理由见生成脚本的文件头。

运行时只依赖标准库：表是两段按 GB2312 码位顺序排列的音节串，字符本身由码位反解
（不需要把 6.7 千字也写进源码，源码因此只有音节那一份）。
"""
from __future__ import annotations

__all__ = ["syllable", "lazy_pinyin", "is_han", "is_syllable", "COVERAGE"]

# 覆盖范围（由生成器按实表字数写入，不在这里手写第二份数字）
COVERAGE = %d
'''

FOOTER = '''
# 表按码位分两段：一级字 0xB0A1–0xD7F9（3755 个，恰好排满），二级字 0xD8A1–0xF7FE
# （3008 个）。段内**逐槽**存，空槽是 GB2312 里没用到的位置（一级尾部有三个），
# 用空串占位而不是删掉——删掉会让后面所有字的码位偏移一格，那种错不会报错，
# 只会让整张表从某一位起全部对错字（本模块最坏的一类静默失效）。
_L1 = _decoder(_ROWS[0], 0xB0, 0xD7)
_L2 = _decoder(_ROWS[1], 0xD8, 0xF7)
_BY_CHAR = {}
_BY_CHAR.update(_L1)
_BY_CHAR.update(_L2)
# 音节全集：由表本身反推，不另列一份清单（另列就会与表分家）。
_SYLLABLE_SET = frozenset(v for v in _BY_CHAR.values() if v)


def syllable(ch):
    """单个汉字 → 无声调拼音音节；不是 GB2312 里的汉字返回空串（调用方按拼不出处理）。"""
    return _BY_CHAR.get(str(ch or ""), "")


def is_han(ch):
    return bool(syllable(ch))


def lazy_pinyin(text):
    """把一串文字转成音节列表：汉字→音节，ASCII 字母数字→小写原样，其余（含繁体、
    生僻扩展字、标点）→ **原样保留成一个非音节段**，由调用方判断这段能不能进候选域名。

    为什么不悄悄丢掉读不出来的字：丢掉会把「上海A镠科技」拼成 `shanghaiakexin`，
    一个看着像那么回事、实际根本不存在的域名——比返回一个明显的异常形状更坏。
    调用方用 :func:`is_syllable` 就能分辨哪些段是真音节。
    """
    out = []
    for ch in str(text or ""):
        s = _BY_CHAR.get(ch)
        if s:
            out.append(s)
        elif ch.isascii() and ch.isalnum():
            out.append(ch.lower())
        else:
            out.append("")              # 读不出来：留空槽而不是丢字（长度即账）
    return out


def is_syllable(seg):
    """这一段是不是**表里真有的**拼音音节（名字即判据，不是「长得像字母就行」）。

    为什么必须按表校：上一版写的是「非空且全小写字母数字」，那是「安全 ASCII 段」判定，
    不是音节判定。`lazy_pinyin("Krones AG")` 会把拉丁字母逐字原样返回，于是守卫把
    ['k','r','o',...] 全当成合法读音，拼出 `kronesag.cn` 这种纯字母序假域名——守卫恰好
    在本该拦住它的地方放了行（离线套件实测抓到）。
    """
    return str(seg or "") in _SYLLABLE_SET
'''


def build_rows():
    from pypinyin import lazy_pinyin
    rows = []
    total = 0
    for hi_lo in ((0xB0, 0xD7), (0xD8, 0xF7)):
        lo_i, hi_i = hi_lo
        cells = []
        for hi in range(lo_i, hi_i + 1):
            for lo in range(0xA1, 0xFF):
                try:
                    ch = bytes((hi, lo)).decode("gb2312")
                except UnicodeDecodeError:
                    cells.append("")
                    continue
                if not ("\u4e00" <= ch <= "\u9fff"):
                    cells.append("")
                    continue
                py = lazy_pinyin(ch)
                cells.append(str(py[0]).lower() if py else "")
                if cells[-1]:
                    total += 1
        rows.append(cells)
    return rows, total


def render(rows, total):
    body = [HEADER % int(total)]
    body.append("_ROWS = (\n")
    for cells in rows:
        blob = ",".join(cells)
        chunks = [blob[i:i + 90] for i in range(0, len(blob), 90)]
        # 加号只能放在分片**之间**：尾随一个加号会让生成的文件当场语法错。
        body.append("    (\n")
        for i, seg in enumerate(chunks):
            tail = "" if i == len(chunks) - 1 else " +"
            body.append("        %r%s\n" % (seg, tail))
        body.append("    ),\n")
    body.append(")\n\n")
    body.append(
        "def _decoder(chunks, hi_from, hi_to):\n"
        "    \"\"\"按码位反解字符并配平音节：表里只存读音，字符由 GB2312 码位自己算出来。\"\"\"\n"
        "    text = \"\".join(chunks)\n"
        "    cells = text.split(\",\")\n"
        "    out = {}\n"
        "    n = 0\n"
        "    for hi in range(hi_from, hi_to + 1):\n"
        "        for lo in range(0xA1, 0xFF):\n"
        "            if n >= len(cells):\n"
        "                return out\n"
        "            seg = cells[n]\n"
        "            n += 1\n"
        "            if not seg:\n"
        "                continue\n"
        "            try:\n"
        "                out[bytes((hi, lo)).decode(\"gb2312\")] = seg\n"
        "            except UnicodeDecodeError:\n"
        "                pass\n"
        "    return out\n\n\n")
    body.append(FOOTER.strip("\n") + "\n")
    return "".join(body)


def main():
    check = "--check" in sys.argv
    rows, total = build_rows()
    text = render(rows, total)
    # 生成完立刻自己跑一遍：表是不是真能解析、字数是不是真对得上、守卫是不是真拦得住拉丁字母。
    # 不这么做的后果是这些缺陷要到用户点「补采」时才发现，而那时它们表现为「候选很少」。
    ns = {"__name__": "pinyin_lite_selfcheck"}
    exec(compile(text, "pinyin_lite.py", "exec"), ns)
    if len(ns["_BY_CHAR"]) != int(total):
        print(f"❌ 表内字数与生成计数不一致：{len(ns['_BY_CHAR'])} != {total}")
        return 1
    if ns["COVERAGE"] != int(total):
        print("❌ COVERAGE 没写成实表字数（文档与表分家）")
        return 1
    if any(ns["is_syllable"](c) for c in "kqxbz"):
        print("❌ is_syllable 又把拉丁字母当音节了（守卫失效）")
        return 1
    if not ns["is_syllable"](ns["syllable"]("怡")):
        print("❌ is_syllable 连表内音节都不认（收得太紧）")
        return 1
    if check:
        cur = io.open(OUT, encoding="utf-8").read() if os.path.exists(OUT) else ""
        if cur != text:
            print("❌ pinyin_lite.py 与生成器输出不一致（手改过，或 pypinyin 版本换了）")
            return 1
        print(f"✅ 表与生成器一致（{total} 个汉字）")
        return 0
    io.open(OUT, "w", encoding="utf-8").write(text)
    print(f"✅ 已生成 {OUT}（{total} 个汉字、{len(text)} 字节）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
