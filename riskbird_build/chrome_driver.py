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


_drv_ver_cache = {}      # 驱动自身版本号很稳定：同一路径只问一次


def driver_version(path) -> str:
    """问某个 chromedriver 文件的版本（形如 156.0.8078.47）；问不到返回空串。

    只在失败分支调用 —— 建会话成功时不该为了写日志多起一次进程。
    """
    key = str(path)
    if key in _drv_ver_cache:
        return _drv_ver_cache[key]
    ver = ""
    try:
        out = subprocess.run([key, "--version"], capture_output=True,
                             text=True, timeout=6).stdout
        m = re.search(r"\b(\d+\.\d+\.\d+\.\d+)", out or "")
        ver = m.group(1) if m else ""
    except Exception:
        ver = ""
    _drv_ver_cache[key] = ver
    return ver


def classify_driver_error(msg, drv_ver="", chrome_ver="") -> str:
    """建会话失败分两类，日志与上层重试判据都按这个分流：

    version —— 驱动与 Chrome 主版本对不上，chromedriver 明确拒绝，只有换驱动才有用；
    connect —— 端口在但浏览器那一刻不接驱动的连接，同一驱动稍后重试即可（2026-11-04
               08:33 企知道通道撞的就是这一类，同一份驱动随后 0.2 秒附加成功）。
    """
    s = (msg or "").lower()
    if "only supports chrome version" in s or "this version of chromedriver" in s:
        return "version"
    if ("cannot connect to chrome" in s or "session not created" in s
            or "websocket" in s or "failed to parse" in s):
        return "connect"
    try:    # 老驱动有时只给一句含糊报错：主版本对不上仍按版本型处理
        if drv_ver and chrome_ver and str(drv_ver).split(".")[0] != str(chrome_ver).split(".")[0]:
            return "version"
    except Exception:
        pass
    return "connect"


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
    kinds = []
    paths = candidates(ports)[:retries]

    def _attempt(p):
        """用一个本地驱动建会话；成功返回 driver，失败返回异常消息（并记归因）。"""
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
            dver = driver_version(p)
            cver = chrome_major(ports)
            kind = classify_driver_error(msg, dver, cver)
            tried.append(msg)
            kinds.append(kind)
            where = f"{Path(p).parent.name}/{Path(p).name}"
            if kind == "version":
                log(f"  本地驱动版本不符（{where} {dver or '?'}，Chrome {cver or '?'}）："
                    f"{msg[:100]}，换下一个…")
            else:
                log(f"  本地驱动连不上浏览器（{where} {dver or '?'}：端口在但这一刻没接受"
                    f"驱动的连接，稍后重试通常就好）：{msg[:100]}，换下一个…")
            return None

    for p in paths:
        driver = _attempt(p)
        if driver is not None:
            return driver

    # 本地全败且全是「浏览器不接连接」：联网换驱动治不了这个，先原地退避再试一遍本地
    if paths and kinds and all(k == "connect" for k in kinds):
        for wait in (3, 6):
            log(f"   本地驱动都是「浏览器没接连接」这一类失败，{wait} 秒后原地重试一遍"
                f"（不联网下载）…")
            time.sleep(wait)
            for p in paths:
                driver = _attempt(p)
                if driver is not None:
                    return driver
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
        tail = ""
        if kinds and all(k == "connect" for k in kinds):
            tail = "（浏览器端口在但没接受驱动的连接，多为浏览器正忙/刚启动，稍后重试即可）"
        elif "version" in kinds:
            tail = "（驱动与 Chrome 版本不匹配，需要换成配套的 ChromeDriver）"
        raise RuntimeError(f"创建浏览器会话失败：{_short_err(e)}{tail}") from e
