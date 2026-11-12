#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打包清单硬门：构建开始前先证明「产物该有的东西，清单里真的写了」。

为什么必须有这一步（2026-11-12 的实测教训）：
  b2bcollect 的名单补采有三个平台（企知道 / 风鸟 / 爱企查），它们的采集器源码放在
  ali1688_build、riskbird_build 这两个**别的工具的项目目录**里，运行时由
  b2bcollect_build/site_adapters.py 去 sys._MEIPASS 对应子目录 import。PyInstaller
  的依赖分析只看入口可达的 import 图，而那几个 import 是运行时按路径动态发的，
  **分析器永远看不见**，只能靠 --add-data 一条条手抄。手抄的副本一共有三处
  （Windows workflow、Mac workflow、本机打包脚本），三处都漏了同一对文件
  （riskbird_crawler_v2.py / aiqicha_crawler_v2.py），于是：
    · 构建成功、产物自检通过、CI 全绿；
    · 用户拿到的包在「② 公司名单」里只有企知道一路，等级 0 的管理员也一样；
    · 症状和「档位没给这个功能」长得一模一样，第一轮排查方向直接被带去查权限。
  这类缺陷的共同点是：**少一个文件不会报错，只会少一个功能**，所以在构建前用一张
  机器可读的清单把它拦住，比事后靠用户截图发现便宜得多。

口径（谁是真源）：
  · 跨目录模块清单 = b2bcollect_build/site_adapters.py 里的 EXTERNAL_MODULES
    ——由**使用者**声明自己运行时需要什么，而不是由打包脚本各自记一遍；
  · 本目录模块清单 = b2bcollect_build/*.py 扫出来的实际文件（新增模块忘了登记
    --hidden-import 正是历史上反复出现的漏项，workflow 注释里那句「维护约定」
    从今天起由这个脚本执行，不再靠人逐行比对）；
  · 三方消费本脚本：两个 workflow 的构建前步骤、离线回归测试
    b2bcollect_build/test_frozen_delivery_offline.py。

用法：
  python .github/scripts/check_packaging_manifest.py        # 校验，失败退出码 1
  python .github/scripts/check_packaging_manifest.py --list  # 只打印当前清单
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[2]
ADAPTERS = ROOT / "b2bcollect_build" / "site_adapters.py"
BUILD_DIR = ROOT / "b2bcollect_build"

# 三处打包入口必须同口径：任何一处漏抄，那个平台的产物就少一块功能。
CALLERS = {
    "Windows workflow": ROOT / ".github" / "workflows" / "build-b2bcollect.yml",
    "Mac workflow": ROOT / ".github" / "workflows" / "build-b2bcollect-mac.yml",
    "本机 Mac 打包脚本": ROOT / "debug" / "build_b2bcollect_mac_local.sh",
}

# 不进包的本地模块：入口脚本自己（PyInstaller 以文件为入口，不需要 hidden-import）、
# 一次性命令行工具、已下线但源码还留着的采集站。每一项都必须写清理由——
# 「忘了登记」和「有意不登记」在症状上完全一样，只有理由能把它们分开。
NOT_PACKAGED = {
    "b2bcollect_gui": "打包入口脚本（作为位置参数传给 PyInstaller）",
    "unlock_no_contact": "一次性命令行修复工具，界面不引用",
    "gys_crawler": "已下线，不再打包（见 workflow 注释）",
    "solarbe_crawler": "已下线，不再打包（见 workflow 注释）",
}
TEST_PREFIX = "test_"

# 第三方包名（--hidden-import 里合法、但本目录没有同名 .py 的那些）。
# 这份表只用于「反向检查」：登记了一个既不是本地模块、又不在这里的名字，就说明
# 它要么是打错的模块名、要么是已删除模块的残留——两种都该在构建前说清楚。
# pip 名≠import 名的两个写法（websocket-client→websocket/websocket_client、
# beautifulsoup4→bs4、pillow→PIL）都收进来，那是刻意双写，不是笔误。
PIP_NAMES = {
    "requests", "websocket", "websocket_client", "openpyxl", "phonenumbers",
    "pandas", "xlrd", "bs4", "lxml", "PIL", "selenium", "PyQt5", "yaml",
}

# 随包必须存在的运行期数据：漏了不崩，但功能会静默降级或全员丐版。
REQUIRED_DATA = {
    "config.json": "随包凭证（缺它 = 外部机器一律精简版）",
    "mk_phone_font.json": "马可波罗图片码字形字典（缺它 = 手机号整列空白）",
    "enf_clean": "清洗子包源码目录（缺它 = enhanced_cleaner 整条链路降级）",
}


def _literal(node):
    """取 ast 字面量：只认字符串与元组/列表，不接受任何求值。"""
    try:
        return ast.literal_eval(node)
    except Exception:
        return None


def module_manifest() -> tuple:
    """从 site_adapters.py 的源码里读出两张表（不 import，免得把 selenium 拖进来）。"""
    tree = ast.parse(ADAPTERS.read_text(encoding="utf-8"), filename=str(ADAPTERS))
    found = {}
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name) and t.id in ("EXTERNAL_MODULES",
                                                        "EXPECTED_LIST_CHANNELS"):
                    val = _literal(node.value)
                    if isinstance(val, (list, tuple)):
                        found[t.id] = tuple(str(v) for v in val)
    missing_keys = [k for k in ("EXTERNAL_MODULES", "EXPECTED_LIST_CHANNELS")
                    if k not in found]
    if missing_keys:
        raise SystemExit(f"❌ {ADAPTERS.name} 里读不到 {missing_keys}："
                         "打包清单的判据本身没了，这一步必须红")
    return found["EXTERNAL_MODULES"], found["EXPECTED_LIST_CHANNELS"]


def local_modules() -> list:
    """b2bcollect_build 下应当进包的模块名（排除入口、测试与有意不打包的）。"""
    out = []
    for p in sorted(BUILD_DIR.glob("*.py")):
        name = p.stem
        if name.startswith(TEST_PREFIX) or name in NOT_PACKAGED:
            continue
        out.append(name)
    return out


def _strip_comments(text: str) -> str:
    """剔掉整行注释，只留真正会执行的参数文本。

    这一步不是洁癖：workflow 的注释里就写着「两个名字都写」「--hidden-import 覆盖
    全部本地模块」这类话，不剔掉的话扫描器会把注释里的字当成参数，报出
    「--hidden-import 里登记了『两边都显式写上。』」这种荒唐告警——而一条荒唐的告警
    会让人整条门禁都不信，那比没有门禁更糟。
    """
    kept = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        kept.append(line)
    return "\n".join(kept)


def _flag_values(text: str, flag: str) -> list:
    """扫出 ``--add-data "a;b"`` / ``--hidden-import name`` 后面的取值。

    刻意不依赖 YAML 或 shell 解析器：三处入口的写法各不相同（yml 里是一整行 bash、
    本机脚本是反斜杠续行），能稳定取到「flag 后面那个 token」就够了。
    """
    values = []
    for m in re.finditer(re.escape(flag) + r"[\s=]+", text):
        rest = text[m.end():].lstrip()
        if rest[:1] in ("\"", "'"):
            quote = rest[0]
            end = rest.find(quote, 1)
            token = rest[1:end if end > 0 else len(rest)]
        else:
            token = re.split(r"[\s\\]+", rest, maxsplit=1)[0]
        if token:
            values.append(token.strip())
    return values


def _split_add_data(token: str) -> tuple:
    """拆 --add-data 取值：Windows 用 ``;``、类 Unix 用 ``:`` 分隔源与目标。"""
    src = token.replace("\\", "/")
    for sep in (";", ":"):
        if sep in src:
            a, b = src.split(sep, 1)
            return a.strip(), b.strip()
    return src.strip(), ""


def check_caller(label: str, text: str, externals: tuple, modules: list) -> list:
    """单个打包入口的体检，返回问题清单（空 = 合格）。"""
    problems = []
    text = _strip_comments(text)
    add_data = [_split_add_data(t) for t in _flag_values(text, "--add-data")]
    hidden = set(_flag_values(text, "--hidden-import"))

    # 1) 跨目录模块：site_adapters 运行时要找的每一个 .py 都必须在清单里，
    #    而且**必须落到它被 import 的那个目录**。路径写法允许 "../riskbird_build/x.py"
    #    或 "riskbird_build/x.py"，判据是尾巴对上；但只比对 src 是不够的——目标目录
    #    写错（比如整包平铺进 "."）文件确实在包里，运行时按 _MEIPASS/<子目录>/x.py
    #    照样找不到，症状与「压根没打」一字不差，同样是静默少功能。
    covered = {}
    for src, dst in add_data:
        covered.setdefault(src, set()).add(dst)
    for rel in externals:
        hits = [(s, d) for s, ds in covered.items()
                if (s == rel or s.endswith("/" + rel)) for d in ds]
        if not hits:
            problems.append(f"--add-data 缺跨目录模块 {rel}"
                            f"（site_adapters 运行时要按这个路径 import 它）")
            continue
        want = Path(rel).parent.as_posix()      # ali1688_build/x.py → ali1688_build
        if not any(d == want or d.endswith("/" + want) for _s, d in hits):
            problems.append(
                f"--add-data 里 {rel} 的目标目录不是 {want}"
                f"（实际 {' / '.join(sorted({d for _s, d in hits}))}）："
                f"文件进了包但落错地方，运行时按 _MEIPASS/{want}/ 仍然 import 不到")

    # 2) 本目录模块：新增一个 .py 却没登记 --hidden-import 是最常见的一类漏项
    for name in modules:
        base = name.split(".")[0]
        if name not in hidden and base not in hidden:
            problems.append(f"--hidden-import 缺本地模块 {name}")

    # 3) 反向检查：登记了源码里已经不存在的**本地**模块（下线站残留也算）。
    #    只判本地名：pip 包名与 enf_clean.* 这类子模块不在本目录，不该被误报。
    stale = sorted(n for n in hidden
                   if "." not in n
                   and n not in PIP_NAMES
                   and not (BUILD_DIR / f"{n}.py").exists())
    for name in stale:
        problems.append(f"--hidden-import 里登记了 {name}，但 b2bcollect_build/ 没有这个文件")

    # 4) 运行期数据文件
    dst_names = {Path(src).name for src, _ in add_data} | {src for src, _ in add_data}
    for key, why in REQUIRED_DATA.items():
        if not any(d == key or d.endswith("/" + key) or Path(d).name == key
                   for d in dst_names):
            problems.append(f"--add-data 缺运行期数据 {key}：{why}")

    # 5) 跨目录的 .py 源码在仓库里得真的存在（改名/删除会在这里现形，而不是等到
    #    用户发现界面上少一个平台）。驱动目录那类由 CI 现场下载，本地没有属正常，
    #    把它们混进来只会得到一条永远说不清的假告警。
    for src, _dst in add_data:
        if not src.startswith("../") or not src.endswith(".py"):
            continue
        if not (ROOT / src[3:]).exists():
            problems.append(f"--add-data 指向的源码不存在：{src}")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="b2bcollect 打包清单硬门（构建前）")
    ap.add_argument("--list", action="store_true", help="只打印当前清单与判定，不改退出码语义")
    args = ap.parse_args(argv)

    externals, expected_channels = module_manifest()
    modules = local_modules()

    print(f"跨目录模块（site_adapters.EXTERNAL_MODULES，{len(externals)} 项）：")
    for rel in externals:
        print(f"   · {rel}")
    print(f"名单通道应有路数（EXPECTED_LIST_CHANNELS）：{len(expected_channels)} 路 "
          f"＝ {' / '.join(expected_channels)}")
    print(f"本目录应进包模块：{len(modules)} 个"
          f"（另排除 {len(NOT_PACKAGED)} 个入口/一次性/已下线模块）")

    bad = 0
    for label, path in CALLERS.items():
        if not path.exists():
            print(f"❌ {label}：找不到文件 {path.relative_to(ROOT)}")
            bad += 1
            continue
        problems = check_caller(label, path.read_text(encoding="utf-8", errors="replace"),
                                 externals, modules)
        if problems:
            bad += 1
            print(f"❌ {label}（{path.relative_to(ROOT)}）打包清单不合格：")
            for p in problems:
                print(f"   · {p}")
                if path.name.endswith(".yml"):
                    print(f"::error::{label} 打包清单：{p}")
        else:
            print(f"✅ {label}：跨目录模块、本地模块、运行期数据三项齐全")

    if bad:
        print(f"\n❌ 打包清单硬门：{bad} 处入口不合格。少一个文件不会报错、只会少一个"
              f"功能，所以这一步不放行就不许开建。")
        return 1
    print("\n✅ 打包清单硬门：三处入口与 site_adapters 的清单一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
