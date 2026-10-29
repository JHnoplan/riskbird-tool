#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""b2bcollect Mac 云打包专用：下载 Chrome Stable 最新构建的 mac-arm64 chromedriver，
放到运行器工作区根目录 mac_driver/chromedriver（PyInstaller --add-data 打进 _MEIPASS，
chrome_driver.candidates 在 frozen 时优先尝试；版本失配时自动回退 Selenium Manager）。

与 fetch_chromedrivers.py（win64 三个大版本）同源，但 Mac 云端 runner 与目标机同为
Apple Silicon，只需 Stable 最新一份 arm64 驱动即可。
"""
import io
import json
import os
import stat
import urllib.request
import zipfile

BASE = ("https://googlechromelabs.github.io/chrome-for-testing/"
        "last-known-good-versions-with-downloads.json")


def main():
    data = json.load(urllib.request.urlopen(BASE, timeout=60))
    stable = next(c for c in data["channels"].values() if c["channel"] == "Stable")
    urls = [d["url"] for d in stable["downloads"].get("chromedriver", [])
            if d.get("platform") == "mac-arm64"]
    if not urls:
        raise SystemExit("Stable 通道没有 mac-arm64 chromedriver，检查网络或改日期版本")
    print(f"Stable {stable['version']} → {urls[-1]}")
    raw = urllib.request.urlopen(urls[-1], timeout=180).read()
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        member = next(n for n in z.namelist()
                      if n.endswith("chromedriver-mac-arm64/chromedriver"))
        blob = z.read(member)
    out_dir = os.path.join(os.getcwd(), "mac_driver")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "chromedriver")
    with open(out, "wb") as fh:
        fh.write(blob)
    os.chmod(out, os.stat(out).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"saved {out} ({len(blob)} bytes, +x)")


if __name__ == "__main__":
    main()
