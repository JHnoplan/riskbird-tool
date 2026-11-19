#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tool_version.py —— 工具版本号的**唯一真源**（需求 2 的第一块）。

在这一块之前，版本号在这个仓库里只存在于 CI 的一次 bash 变量里：安装包属性上有
（makensis 的 /DAPP_VERSION），**产物自己却不知道自己是哪一版**——于是
· 用户在界面里问不出「我这是哪版」，远程支持只能靠猜与让对方截图安装包属性；
· 有没有新版本这件事根本没有可比较的两个数，做不了更新提示；
· 而「装了旧包以为装了新包」这类事故（[skip ci] 那次）当时没有任何一句话能说清。

三条设计决定，每条都对齐业界做法并有本地判据：

1. **一个算法，两处消费**。显示版 `1.<build>`、文件属性四段版 `1.a.b.0`（build 拆成
   两个 16 位字段，因为 makensis 的 VIProductVersion 只认 X.X.X.X 且每段 16 位上限，
   run_number 直接塞进一段迟早被编译器拒掉）都由同一个 `build` 算出来。CI 那一步改读
   本模块，不再自己拼字符串——拼两遍就是两个真源，早晚对不上。
2. **构建号是构建输入，不是源码内容**。仓库里那份 `version.json` 是被 .gitignore 忽略的
   生成物（与 config.json 同一口径：密钥与每次构建都变化的东西不进仓库）。CI 打包前
   现生成并 `--add-data` 随包；源码运行读不到它时**如实报「没能确认」**（build=0），
   而不是假称自己是某一版。这也让「本地跑的源码」与「客户手里的包」天然可区分。
3. **产物必须自报**。`--print-version` 打印的是**这个包运行时真读到的**那一份，CI 拿
   注入值与它对拍，不一致就红。「清单上写着 1.234、包里的模块读出来是 1.233」这种
   错位（--add-data 漏收、旧包覆盖新包）只有让产物自己开口才查得出来。

比较一律走 `compare()`/`is_newer()`：按整数 build 比，不拿字符串比大小——
`"1.9" > "1.10"` 在字符串上是真话，在版本上是假话，而这条假话的表现是「永远提示有更新」。
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

# 显示版的主号：与 NSIS 的 Name / VIAddVersionKey、latest.json 里的 version 同源。
VERSION_MAJOR = 1
# 随包注入的构建戳文件名（CI 生成，仓库里不保留）。
BUILD_STAMP_NAME = "version.json"
# 源码运行/临时验证时的覆盖通道：与 B2B_EDITION 一类调试变量同一条思路，
# 但这里只接受纯数字，非数字一律当「没给」，免得把一个错值当成真实构建号去比大小。
BUILD_ENV_VAR = "B2B_BUILD_NUMBER"
# 未注入构建号时的取值：**0 代表「本机版本未能确认」**，不是「第 0 版」。
# 一切「有没有新版本」的判断都必须先看它是不是 0（见 certain）。
UNKNOWN_BUILD = 0
# 四段版本号能表达的上限：两段 16 位（见 `product_version`）。真正的拦截点在 CI 生成
# 戳的那一步（超了直接停），这里只负责「拼不出来就不拼」——把一个超出范围的构建号
# 降级折成两个字段，会得到一个看着合法、实际对应另一版的四段号，比 CI 红一天更难查。
MAX_BUILD = 2 ** 32 - 1

_DISPLAY_RE = re.compile(r"^(\d+)\.(\d+)$")


def bundle_dir() -> Path:
    """随包资源目录：冻结态取 PyInstaller 的解包根，源码态取本文件所在目录。

    这里**不**转过去调 tool_config.bundle_dir()：tool_config 自己是可选依赖，而版本
    模块要在「连 config 都读不到」的机器上也能说清自己是哪版——那一台最需要远程支持
    的机器，恰恰最需要这条答案。两处算法一致的判据交给离线套件来钉。
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent


def stamp_file() -> Path:
    return bundle_dir() / BUILD_STAMP_NAME


def _read_stamp() -> dict:
    """读那份注入的戳；读不到/读坏都返回空字典（由 `certain()` 如实说「没确认」）。"""
    try:
        raw = json.loads(stamp_file().read_text(encoding="utf-8"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _clean_build(value) -> int:
    """把任何来源的构建号收成非负整数；非数字一律回到 UNKNOWN_BUILD。"""
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return UNKNOWN_BUILD
    return n if n > UNKNOWN_BUILD else UNKNOWN_BUILD


def build_number() -> int:
    """本次运行的构建号：随包戳 > 环境变量 > 未能确认（0）。

    环境变量排在第二位而不是第一位：客户机器上不该有人设置它，但它必须能被覆盖，
    否则「这份包到底读的哪一份戳」这件事在远程支持时没法反证。
    """
    stamp = _read_stamp()
    n = _clean_build(stamp.get("build"))
    if n:
        return n
    return _clean_build(os.environ.get(BUILD_ENV_VAR, ""))


def commit_sha() -> str:
    """构建对应的 commit（只用于远程排障点名，不参与版本比较，也不上界面）。"""
    return str(_read_stamp().get("commit") or "").strip()[:40]


def certain() -> bool:
    """本机版本能不能确定地说出来。

    False 时界面与更新通路都必须闭嘴或如实说「没能确认」：拿 0 当版本号去比大小，
    结论永远是「对方更新」，于是每一台没注入戳的开发机都会天天弹更新提示。
    """
    return build_number() != UNKNOWN_BUILD


def version_text(build: Optional[int] = None) -> str:
    """给人看的那一句版本号：`1.234`；未能确认时说清是未能确认，不印「1.0」冒充。"""
    n = build_number() if build is None else _clean_build(build)
    if n == UNKNOWN_BUILD:
        return f"{VERSION_MAJOR}.0-dev（未注入构建号，本机版本未能确认）"
    return f"{VERSION_MAJOR}.{n}"


def in_range(build: Optional[int] = None) -> bool:
    """这个构建号还能不能被四段格式如实表达（CI 打包与离线判据共用这一个口径）。"""
    n = build_number() if build is None else _clean_build(build)
    return UNKNOWN_BUILD < n <= MAX_BUILD


def product_version(build: Optional[int] = None) -> str:
    """文件属性用的四段版本号 X.X.X.X（makensis 唯一能收的格式）。

    build 拆成两个 16 位字段：高位段 = build // 65536，低位段 = build % 65536。
    两种「拼不出来」都留空串（调用方是 CI 的注入那一步，拿到空串就该停下来）：
      · 超出可表达范围：降级折成两段会得到一个看着合法、实际对应另一版的四段号；
      · 未能确认（build=0）：这时拼出的 `1.0.0.0` 更阴——它完全合法、哪一版也不是，
        编进文件属性后远程支持会按「1.0」这个不存在的版本去判断，比 CI 红一天难查。
        （`--print-field` 早已按这一口径拦下，不能只挡 CLI 不挡函数：拿函数当接口的调用方
        同样多，而 `report()` 里那一格也会被对拍读到。）
    两段数字必须与 `version_text` 出自同一个 `build`，所以判据同时盯着这两处的算法。
    """
    n = build_number() if build is None else _clean_build(build)
    if n == UNKNOWN_BUILD or n > MAX_BUILD:
        return ""
    return f"{VERSION_MAJOR}.{n // 65536}.{n % 65536}.0"


def parse_version(text) -> Optional[int]:
    """`"1.234"` / `"234"` / `234` → 构建号；认不出来返回 None（不许当成 0）。

    返回 None 与返回 0 是两件事：None 是「这句话不像版本号」，0 是「版本号写成了
    未能确认那一档」。把错格式静默折成 0，等于把清单写错变成「用户已经是最新版」。
    """
    if text is None:
        return None
    if isinstance(text, bool):
        return None
    if isinstance(text, int):
        return text if text >= 0 else None
    s = str(text).strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    m = _DISPLAY_RE.match(s)
    if not m:
        return None
    if int(m.group(1)) < VERSION_MAJOR:
        # 主号更低：这是另一个产品线的号，不能被当成「比本机旧」而静默忽略
        return None
    return int(m.group(2))


def compare(a, b) -> Optional[int]:
    """两个版本号写法之间的先后：>0 表示 a 更新；任一侧认不出来返回 None。"""
    na, nb = parse_version(a), parse_version(b)
    if na is None or nb is None:
        return None
    return (na > nb) - (na < nb)


def is_newer(latest, current=None) -> bool:
    """远端那份是否**确实**比本机新；任何一侧说不清都返回 False。

    默认值取本机真实版本，而不是让人把本机版本传进来：调用点写错一个参数就把
    「和最新版的差距」比成了「和 0 的差距」，而表现是天天弹更新——这类错在用户那边
    只会看成弹窗烦人，不会有人怀疑是比较逻辑。
    """
    cur = version_text() if current is None else current
    if not certain() and current is None:
        return False
    return compare(latest, cur) == 1


def report() -> dict:
    """self-report 的全部内容：`--print-version` 打的就是这一份的 JSON。"""
    n = build_number()
    return {
        "version": version_text(n),
        "build": n,
        "product_version": product_version(n),
        "certain": n != UNKNOWN_BUILD,
        "commit": commit_sha(),
        "stamp_file": str(stamp_file()),
        "frozen": bool(getattr(sys, "frozen", False)),
    }


def write_stamp(path, build, commit: str = "") -> dict:
    """生成随包的构建戳（CI 打包前那一步的唯一出口）。返回 {ok, path, why, display, product_version}。

    两件事在这一步直接拦住，不留给后面：
      · 构建号不是正整数（run_number 为空、被引号包住了、拼错了）——拼出一个 1.0-dev 的包
        不会报错，只会让那个包永远说不出自己是谁，然后更新提示与远程排障一起失效；
      · 超出四段可表达范围——makensis 会收下一个它编不了的号，然后交付里根本没有安装包。
    戳里同时留一份 display/product_version 给人读：它们是同一算法的导出值，不是第二个真源；
    读的一侧（`build_number`）只认 `build`，所以就算有人手改了那两行也不会影响比较。
    """
    n = _clean_build(build)
    if n == UNKNOWN_BUILD:
        return {"ok": False, "path": str(path), "build": UNKNOWN_BUILD, "why":
                f"构建号不是可用的正整数（拿到的是「{build}」）：CI 的 run_number 没传、"
                "被引号吃了、或者写错了；不注入一个假号，也不许空着出包", "display": "", "product_version": ""}
    if n > MAX_BUILD:
        return {"ok": False, "path": str(path), "build": n, "why":
                f"构建号 {n} 已经超出四段版本号能表达的范围（≤ {MAX_BUILD}）："
                "拿它编安装包会直接 abort，宁可不出包也不能交一个假号", "display": "", "product_version": ""}
    payload = {
        "build": n,
        "commit": str(commit or "").strip()[:40],
        "display": version_text(n),
        "product_version": product_version(n),
        "generated_by": "tool_version.py --write-stamp",
    }
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as e:
        return {"ok": False, "path": str(p), "build": n, "why": f"构建戳写不下去：{type(e).__name__}: {e}",
                "display": "", "product_version": ""}
    return {"ok": True, "path": str(p), "why": "", "display": payload["display"],
            "product_version": payload["product_version"], "build": n}


def main(argv=None) -> int:
    """命令行出口：`--print-version` / `--write-stamp` / `--print-field`。

    退出码分两档：
      · 取证那两档（`--print-version`，包括未能确认）恒返 0：这一步在 CI 里是「问产物自己
        是哪一版」，判定由对拍那一步做（拿注入值与这里的输出比）。自报失败与自报不一致
        是两种不同的红，混在一个退出码里就查不出是哪种；
      · 生成那两档（`--write-stamp`、`--print-field product`）拼不出来就返 1：那一步的目标
        就是「不给一个假号开绿」，与上面不矛盾。
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        print("用法：tool_version.py [--print-version [--json] [--out 文件]] | "
              "[--print-field display|product|build] | "
              "[--write-stamp 文件 --build 构建号 [--commit sha]]")
        return 0
    if "--write-stamp" in argv:
        path = argv[argv.index("--write-stamp") + 1] if "--write-stamp" in argv[:-1] else ""
        if not path:
            print("❌ --write-stamp 后面要跟一个写入路径")
            return 1
        got = write_stamp(path, _opt(argv, "--build"), _opt(argv, "--commit"))
        if not got.get("ok"):
            print("❌ " + str(got.get("why") or "构建戳没能生成"))
            return 1
        print(f"✅ 构建戳已写入 {got['path']}：显示 {got['display']} / 四段 {got['product_version']}")
        return 0
    if "--print-field" in argv:
        field = argv[argv.index("--print-field") + 1] if "--print-field" in argv[:-1] else ""
        if field not in ("display", "product", "build"):
            print(f"❌ 不认这个字段：「{field}」（只能是 display / product / build）")
            return 1
        n = build_number()
        if n == UNKNOWN_BUILD:
            # 本机版本未能确认时**不给一个看起来合法的号**：源码目录里跑这一步会拼出
            # 1.0.0.0，CI 拿着它去编安装包只会得到一个「哪版都不是」的产物。真发生在这一步
            # 就是「上一步没写戳」，当场红比往下绿有用。
            print(f"❌ 本机没能确认构建号（{stamp_file()} 读不到或为空），"
                  "这一格拼不出来：先在打包前跑 --write-stamp")
            return 1
        value = {"display": version_text(n), "product": product_version(n),
                 "build": str(n)}[field]
        if not value:
            # 空串在四段这一格就是「拼不出来」，不能当成功打一个空行出去：
            # 后面那句 /DVER4="" 会被 makensis 回一句 invalid format，而日志里看不出错在哪。
            print(f"❌ 本机构建号 {n} 拼不出字段「{field}」（超出四段可表达范围？）")
            return 1
        print(value)
        return 0
    r = report()
    if "--json" in argv:
        text = json.dumps(r, ensure_ascii=False)
    else:
        text = (f"{r['version']}    四段={r['product_version']}    "
                f"frozen={str(r['frozen']).lower()}    commit={r['commit'] or '-'}    "
                f"戳={r['stamp_file']}")
    print(text)
    out = _opt(argv, "--out")
    if out:
        # 只给产物自报留这一个出口：Windows 的 --windowed exe 根本没有控制台，
        # Start-Process 拿不到 stdout（已有的 --acl-doctor 也是靠落在文件里的报告取证的）。
        # 写成一份 JSON 而不是拼一行：对拍那一步要的是 build/product_version/certain 三个字段。
        try:
            Path(out).write_text(json.dumps(r, ensure_ascii=False, indent=2) + "\n",
                                 encoding="utf-8")
        except OSError as e:
            print(f"⚠ 自报结果没能写到 {out}：{type(e).__name__}: {e}")
    return 0


def _opt(argv, name: str) -> str:
    """取 ``--name value`` 的取值；没给或跟到末尾就给空串（调用方按「没给」处理）。"""
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return str(argv[i + 1])
    return ""


if __name__ == "__main__":
    sys.exit(main())
