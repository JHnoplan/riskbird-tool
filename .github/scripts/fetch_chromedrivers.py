# -*- coding: utf-8 -*-
"""build-1688 专用：下载最近 3 个大版本（Stable 及其前两个）的 win64 chromedriver，
按 chromedriver.<大版本>.exe 注版命名放进 ali1688_build/drivers/，随 exe 一并打包。

为什么不只打 Stable：Chrome 自动更新灰度慢，用户浏览器常比打包时的 Stable 落后
一个大版本；驱动大版本与浏览器不匹配时新版 chromedriver 报的是
'cannot connect to chrome'，小白用户无法自救。运行时 crawler._driver_path
会读浏览器实际版本来挑对应的注版文件。

用 Python 而非 PowerShell 做版本算术/JSON 挑选：pwsh 对 PSCustomObject 的
数组与减法语义在边界上不直观，已在 CI 实测踩过（op_Subtraction on Object[]）。

需求 10 的兼容通道（Win7/8）多一个参数 `--max-major`：Google 从 Chrome 110 起不再
支持 Win7/8/8.1，那类机器上能装到的最新浏览器是 109，拿 Chrome 141 的驱动去驱动
不存在的浏览器没有意义。而 Chrome for Testing 只发布 115+，109 及更早只能在旧的
`chromedriver.storage.googleapis.com` 存储桶里取（2026-11-19 本机实测：
LATEST_RELEASE_109 仍返回 109.0.5414.74、win32 包 HTTP 200），所以这一路要认两套货源：
≥115 走 CfT 的 known-good JSON，≤114 走旧存储桶的 LATEST_RELEASE_<大版本>。
旧包名是 chromedriver_win32.zip（那年代只有 32 位驱动，它在 64 位系统上驱动 64 位
Chrome 是官方支持的形态），但**包内布局与 CfT 不同**：chromedriver.exe 直接躺在顶层，
没有 chromedriver_win32/ 这一层目录（2026-11-19 本机拉真包验的：109.0.5414.74 里是
`chromedriver.exe` + `LICENSE.chromedriver`，108.0.5359.71 里只有 `chromedriver.exe`），
所以取哪一格、按什么名字取，两处都要按货源分支。
"""
import argparse
import io
import json
import os
import sys
import urllib.request
import zipfile

# 与 .github/scripts 里另外几份同源：控制台编码必须由脚本自己写定，不能交给环境代码页。
# 本轮把旧桶那一支（需求 10 兼容通道）加进来之后，这里新增了三处中文写控制台——第 12 条
# 硬门就是这么抓到它的：英文 Windows 镜像（cp1252）上写出的当下就抛 UnicodeEncodeError，
# 而「驱动没拿到」这句唯一的结论会被那条异常盖掉，构建日志只剩一片乱码加一个非零退出码。
# 本机（UTF-8）永远复现不出来，所以必须在**第一处中文之前**写好这一句（顺序本身也有门）。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

LKG_URL = ("https://googlechromelabs.github.io/chrome-for-testing/"
           "last-known-good-versions-with-downloads.json")
KG_URL = ("https://googlechromelabs.github.io/chrome-for-testing/"
          "known-good-versions-with-downloads.json")
# 旧存储桶：Chrome 114 及唯一有驱动的地方（Chrome for Testing 从 115 才开始）。
LEGACY_BASE = "https://chromedriver.storage.googleapis.com"
# 两套货源的分界：CfT 的第一个大版本就是 115，低于它只能去旧桶。
CFT_MIN_MAJOR = 115
OUT_DIR = os.path.join("ali1688_build", "drivers")


def fetch_json(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.load(r)


def extract_win64_driver(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        return _as_driver(z.read("chromedriver-win64/chromedriver.exe"), "CfT win64 包")


def _as_driver(data, whence):
    """拿出来的必须是一个能跑的 Windows 可执行文件，不能只看「名字对上了」。

    这一格是驱动类产物里最坏的失败形态：打包时全绿（文件大小对、路径对），
    到用户机器上才报「不是有效的 Win32 应用程序」——而这台机器正是需求 10 里那批
    小白电脑。两个货源都过这一道：PE 头必须是 MZ，而且不能小到一个「只拿到页头」
    的程度（实测最小的一个驱动 12,358,144 字节，1MB 是宽得多的一道 sanity）。
    """
    if data[:2] != b"MZ" or len(data) < 1024 * 1024:
        raise SystemExit("%s 取出来的不像一个 chromedriver（头 %r、共 %d 字节）："
                         "宁可不打包，也不把一个跑不起来的东西注进包里" %
                         (whence, data[:2], len(data)))
    return data


def extract_legacy_driver(raw):
    """旧桶的包：chromedriver.exe 直接在顶层（没有包名那一层目录，与 CfT 不同）。

    不写「取 z.namelist() 里第一个 .exe」那种讨巧写法：按实测那一格的名字取，
    包结构意外变了就红在这里（KeyError），而不是静默拿到一个不相干的文件。
    上面的出处不是背文档：本函数初版就是照包名猜了个 `chromedriver_win32/` 目录，
    本机拉真包才发现它压根不存在——同一个错如果发生在 CI 上，红话是「构建脚本
    挂了」，发生在用户机器上就是「装完用不了」。
    """
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        return _as_driver(z.read("chromedriver.exe"), "旧桶 win32 包")


def legacy_majors(max_major, count=3):
    """旧桶里从 max_major 往回数 count 个大版本（桶里没有版本列表，号是连续的）。"""
    return [max_major - i for i in range(count)]


def fetch_legacy(mj):
    """旧存储桶取一个大版本：LATEST_RELEASE_<mj> 给出四段号，再拼下载路径。"""
    ver = _text("%s/LATEST_RELEASE_%d" % (LEGACY_BASE, mj)).strip()
    if not ver.startswith("%d." % mj):
        raise SystemExit("旧桶给大版本 %d 返回的版本号不像话（%r）：宁可不打包，"
                         "也不拿一个来路不明的驱动充数" % (mj, ver))
    raw = _bytes("%s/%s/chromedriver_win32.zip" % (LEGACY_BASE, ver))
    return ver, extract_legacy_driver(raw)


def _text(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def _bytes(url):
    with urllib.request.urlopen(url, timeout=180) as r:
        return r.read()


def main(argv=None):
    ap = argparse.ArgumentParser(description="下载随包 chromedriver（默认最近 3 个大版本）")
    ap.add_argument("--max-major", type=int, default=0,
                    help="只取不大于这个值的大版本（兼容通道用：Win7/8 上 Chrome 最高 109）")
    args = ap.parse_args(argv)
    os.makedirs(OUT_DIR, exist_ok=True)
    if args.max_major and args.max_major < CFT_MIN_MAJOR:
        # 老系统那一档：旧桶里取三个大版本（109/108/107），与主链路同一命名、同一落点。
        got = 0
        for mj in legacy_majors(args.max_major):
            try:
                ver, data = fetch_legacy(mj)
            except SystemExit as e:
                print("skip legacy major %d: %s" % (mj, e))
                continue
            except Exception as e:
                print("skip legacy major %d: %s: %s" % (mj, type(e).__name__, e))
                continue
            dst = os.path.join(OUT_DIR, "chromedriver.%d.exe" % mj)
            with open(dst, "wb") as fh:
                fh.write(data)
            print("bundled %s (%s, 旧桶, %d MB)" % (dst, ver, len(data) // 1048576))
            got += 1
        if got < 2:
            raise SystemExit("only %d drivers bundled, abort" % got)
        print("DONE")
        return
    stable = fetch_json(LKG_URL)["channels"]["Stable"]["version"]
    stable_major = int(stable.split(".")[0])
    versions = fetch_json(KG_URL)["versions"]
    print("stable:", stable)
    if args.max_major and args.max_major < stable_major:
        # 封顶但仍走 CfT（比如哪天要一个只到 120 的通道）：从 known-good 里取
        # 不大于封顶的最近三个大版本，与旧桶那一路同一语义。
        allowed = sorted({int(v["version"].split(".")[0]) for v in versions
                          if int(v["version"].split(".")[0]) <= args.max_major}, reverse=True)
        targets = allowed[:3]
        print("capped by --max-major %d: %s" % (args.max_major, targets))
    else:
        targets = [stable_major, stable_major - 1, stable_major - 2]
    got = 0
    for mj in targets:
        cands = [v for v in versions if int(v["version"].split(".")[0]) == mj]
        if not cands:
            print(f"skip major {mj}: no known-good build")
            continue
        v = cands[-1]  # known-good 按发布顺序排列，取该大版本最新构建
        urls = [d["url"] for d in v["downloads"].get("chromedriver", [])
                if d.get("platform") == "win64"]
        if not urls:
            print(f"skip major {mj}: no win64 chromedriver")
            continue
        data = extract_win64_driver(_bytes(urls[0]))
        dst = os.path.join(OUT_DIR, f"chromedriver.{mj}.exe")
        with open(dst, "wb") as fh:
            fh.write(data)
        print(f"bundled {dst} ({v['version']}, {len(data) // 1048576} MB)")
        got += 1
    if got < 2:
        raise SystemExit(f"only {got} drivers bundled, abort")
    print("DONE")


if __name__ == "__main__":
    main()
