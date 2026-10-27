# -*- coding: utf-8 -*-
"""build-1688 专用：下载最近 3 个大版本（Stable 及其前两个）的 win64 chromedriver，
按 chromedriver.<大版本>.exe 注版命名放进 ali1688_build/drivers/，随 exe 一并打包。

为什么不只打 Stable：Chrome 自动更新灰度慢，用户浏览器常比打包时的 Stable 落后
一个大版本；驱动大版本与浏览器不匹配时新版 chromedriver 报的是
'cannot connect to chrome'，小白用户无法自救。运行时 crawler._driver_path
会读浏览器实际版本来挑对应的注版文件。

用 Python 而非 PowerShell 做版本算术/JSON 挑选：pwsh 对 PSCustomObject 的
数组与减法语义在边界上不直观，已在 CI 实测踩过（op_Subtraction on Object[]）。
"""
import io
import json
import os
import urllib.request
import zipfile

LKG_URL = ("https://googlechromelabs.github.io/chrome-for-testing/"
           "last-known-good-versions-with-downloads.json")
KG_URL = ("https://googlechromelabs.github.io/chrome-for-testing/"
          "known-good-versions-with-downloads.json")
OUT_DIR = os.path.join("ali1688_build", "drivers")


def fetch_json(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.load(r)


def extract_win64_driver(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        return z.read("chromedriver-win64/chromedriver.exe")


def main():
    stable = fetch_json(LKG_URL)["channels"]["Stable"]["version"]
    stable_major = int(stable.split(".")[0])
    versions = fetch_json(KG_URL)["versions"]
    os.makedirs(OUT_DIR, exist_ok=True)
    print("stable:", stable)
    got = 0
    for mj in (stable_major, stable_major - 1, stable_major - 2):
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
        data = extract_win64_driver(urllib.request.urlopen(urls[0], timeout=180).read())
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
