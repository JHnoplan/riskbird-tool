"""chrome_driver.py - 本地 chromedriver 定位与创建（尽量不走 Selenium Manager）

为什么必须有这一层：
打包成 exe 后，代码里 `Path(__file__).parent / "chromedriver-win64"` 指向的是每次启动
都变、退出即删的 _MEIxxxx 临时目录，判据永远不成立 → 每一次附加/启动浏览器都退给
Selenium Manager。它会先联网向 googlechromelabs、googleapis 查驱动版本再下载，国内网络
这两个端点经常几十秒不通，界面上的表现就是：
    「Chrome 已经打开了，但企知道/风鸟的页面迟迟不出来，要等很久很久」
（Ali1688Tool 早前就是同一个坑，见 ali1688_crawler._driver_path 的注释。）

这里的约束很硬：先把「手头已有的驱动」全试一遍，只有本地一个都不行时才允许退给
Selenium Manager，并且明确打出这一次慢在哪一步、用的是哪个驱动、花了几秒。
"""
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests

# 本机装了哪些调试端口在跑（共享 Chrome 的端口）——能问出 Chrome 真实版本，优先挑同主版本的驱动
DEFAULT_PROBE_PORTS = (9233, 9234)

_major_cache = None       # 同一个进程里只探测一次 Chrome 主版本


def _exe_name() -> str:
    return "chromedriver.exe" if sys.platform == "win32" else "chromedriver"


def _version_key(path: Path):
    """按版本号排序用：取路径里出现的第一段数字（.../chromedriver/142.0.7444.162/chromedriver.exe）"""
    for part in reversed(path.parts):
        nums = re.findall(r"\d+", part)
        if nums and len(nums) >= 2:
            return tuple(int(x) for x in nums[:4])
    return ()


def _cache_root() -> Path:
    """Selenium Manager 以往成功联网拉取后固化的驱动目录（在用户目录，跨运行保留）"""
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or str(Path.home())
    return Path(home) / ".cache" / "selenium" / "chromedriver"


def _port_chrome_major(port: int):
    """从调试端口问 Chrome 主版本（共享浏览器已在跑时最准，也不用额外起进程）"""
    try:
        browser = str((requests.get(f"http://127.0.0.1:{port}/json/version",
                                    timeout=1.5).json() or {}).get("browser") or "")
    except Exception:
        return None
    m = re.search(r"/(\d+)\.", browser)
    return m.group(1) if m else None


def chrome_major(ports=None) -> str:
    """当前 Chrome 的主版本（拿不到返回 None，不因此放弃本地驱动）"""
    global _major_cache
    if _major_cache:
        return _major_cache
    for p in (ports or DEFAULT_PROBE_PORTS):
        got = _port_chrome_major(p)
        if got:
            _major_cache = got
            return got
    # 没有可问的浏览器：读安装信息（Windows 读文件版本，其他系统问 --version）
    try:
        if sys.platform == "win32":
            chrome = next((c for c in (
                os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
                os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
                os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
            ) if c and os.path.exists(c)), None)
            if not chrome:
                return ""
            ps = f"(Get-Item -Path '{chrome}').VersionInfo.ProductVersion"
            out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                                 capture_output=True, text=True, timeout=8).stdout
        else:
            chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
                if sys.platform == "darwin" else (
                    shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser") or "")
            if not chrome:
                return ""
            out = subprocess.run([chrome, "--version"], capture_output=True,
                                 text=True, timeout=8).stdout
        m = re.search(r"\b(\d+)\.", out or "")
        _major_cache = m.group(1) if m else ""
    except Exception:
        _major_cache = ""
    return _major_cache


def candidates(ports=None) -> list:
    """本机能直接用的 chromedriver，按“最可能一次成功”排好序

    排序原则：与当前 Chrome 主版本一致的缓存驱动排最前（版本不符 chromedriver 会直接拒绝启动，
    白等一轮），其次才是随 exe 打包的、用户放在 exe 同目录的、PATH 上的、开发仓库里的。
    """
    exe = _exe_name()
    frozen = getattr(sys, "frozen", False)
    cands = []
    meipass = getattr(sys, "_MEIPASS", "") if frozen else ""
    if meipass:
        cands.append(Path(meipass) / exe)
    if frozen:
        # 用户可以手动把一个匹配自己 Chrome 的驱动放在 exe 旁边，优先级高于打包里那份
        cands.append(Path(sys.executable).resolve().parent / exe)
    root = _cache_root()
    if root.is_dir():
        cands.extend(sorted(root.glob("**/" + exe), key=_version_key, reverse=True))
    if not frozen:
        dev_sub = "chromedriver-win64/chromedriver.exe" if sys.platform == "win32" \
            else "chromedriver-mac-x64/chromedriver"
        cands.append(Path(__file__).resolve().parent / dev_sub)
    which = shutil.which(exe)
    if which:
        cands.append(Path(which))

    major = chrome_major(ports) or ""
    uniq, seen = [], set()
    for c in cands:
        try:
            if not c.is_file():
                continue
            key = str(c)
            if key in seen:
                continue
            seen.add(key)
            uniq.append(c)
        except OSError:
            continue
    if major:
        # 主版本对得上的挪到最前面（缓存目录里往往躺着好几个历史版本）
        uniq.sort(key=lambda c: 0 if re.search(r"(^|[\\/])" + re.escape(major) + r"\.", str(c)) else 1)
    return [str(c) for c in uniq]


def _short_err(e) -> str:
    """chromedriver 的报错动辄几十行堆栈，只留第一行给人看"""
    s = str(e) or e.__class__.__name__
    return s.splitlines()[0][:180]


def create(options, log=None, ports=None, retries: int = 3):
    """用本地驱动建会话；本地全落空才退 Selenium Manager。返回 WebDriver。

    每一次尝试都打耗时，方便“打开网页很慢”这类问题事后归因到具体步骤。
    """
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service

    log = log or (lambda m: None)
    tried = []
    paths = candidates(ports)[:retries]
    for p in paths:
        service = None
        t0 = time.monotonic()
        try:
            service = Service(executable_path=p)
            driver = webdriver.Chrome(service=service, options=options)
            log(f"🔧 已用本地 ChromeDriver（{Path(p).parent.name[:16]}）"
                f"，耗时 {time.monotonic() - t0:.1f} 秒")
            return driver
        except Exception as e:
            if service is not None:
                try:
                    service.stop()
                except Exception:
                    pass
            msg = _short_err(e)
            tried.append(msg)
            log(f"  本地驱动不可用（{Path(p).name}：{msg[:100]}），换下一个…")
    if paths:
        log("⚠ 本机可用的 ChromeDriver 都没能建会话，回退 Selenium Manager（可能要联网查版本，会慢）")
    else:
        log("⚠ 本机没有 ChromeDriver，交给 Selenium Manager 现场获取（首次要联网下载，可能等几十秒）")
    t0 = time.monotonic()
    try:
        driver = webdriver.Chrome(options=options)
        log(f"🔧 Selenium Manager 已备好驱动，耗时 {time.monotonic() - t0:.1f} 秒")
        return driver
    except Exception as e:
        if tried:
            log(f"   （本地驱动失败原因：{tried[0]}）")
        raise RuntimeError(f"创建浏览器会话失败：{_short_err(e)}") from e
