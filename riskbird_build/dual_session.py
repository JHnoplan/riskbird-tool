"""双标签并行采集：一个共享 Chrome + 两个独立会话（企知道 / 爱企查），单双数分公司。

为什么这样做能并行且互不影响（已实机验证，见 debug_dual_tab_probe.py）：
- 两个会话共用同一个 Chrome 进程与同一份登录态配置目录，但**各自独占一个窗口/标签**：
  企知道是 Selenium 会话（session 绑定自己的窗口，命令只发到自己的标签），
  爱企查是裸 CDP（每条命令显式带自己标签的 ws 地址），两条命令通道彼此独立 → 可真并行。
- 独占窗口而非同窗口的新标签：同窗口里只有一个标签是"可见"的，后台标签会被 Chrome
  节流（定时器降频、requestAnimationFrame 暂停），两边都保持可见才不会互相拖慢。
- 分工：第 1、3、5… 家给企知道，第 2、4、6… 家给爱企查；两个会话的结果由 GUI 合并后
  统一写两份文件（原数据 + 清洗后入库），不再按平台各存一个 xlsx。
- 风鸟不参与：它有每日模糊查询额度与详情额度，并行只会更快烧光额度。
"""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests

DUAL_PORT = 9233          # 共享 Chrome 的调试端口（与爱企查默认端口一致）
CHROME_WAIT_SECONDS = 25  # 启动共享 Chrome 的最长等待
# 会话结束后没人用的空白窗口：每个白占 30–50 MB，可以安全关掉
BLANK_TAB_URLS = ("about:blank", "chrome://newtab/", "chrome://new-tab-page/")

# 复制登录态配置时跳过的大目录（保留 Cookies/Network/Login Data/Local State 等登录相关文件）
_SKIP_PATTERNS = [
    "SingletonLock", "SingletonSocket", "SingletonCookie",
    "Cache", "Code Cache", "GPUCache", "DawnCache", "GrShaderCache", "ShaderCache",
    "component_crx_cache", "extensions_crx_cache", "Crashpad",
    "Service Worker", "Platform Notifications", "optimization_guide_model_store",
]


def shared_profile_dir() -> Path:
    """共享 Chrome 使用的配置目录（含用户真实登录态的副本）"""
    if sys.platform == "win32":
        return Path(os.environ.get("TEMP", r"C:\temp")) / "chrome_debug_profile"
    return Path("/tmp/chrome_debug_profile")


def _real_profile_dir() -> str:
    if sys.platform == "win32":
        return os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data")
    return os.path.expanduser("~/Library/Application Support/Google/Chrome")


def has_login_state(profile: Path) -> bool:
    """该配置目录里是否已有可用的 Cookie（新旧 Chrome 的 Cookie 路径不同，两个都认）"""
    return bool(profile.exists() and (
        (profile / "Default" / "Cookies").exists()
        or (profile / "Default" / "Network" / "Cookies").exists()
    ))


def prepare_shared_profile(log) -> Path:
    """准备共享配置目录：优先复用已有的登录态副本，没有才从真实 Chrome 复制一次。"""
    profile = shared_profile_dir()
    if has_login_state(profile):
        return profile
    # 爱企查单跑时用的是另一个副本目录，能复用就复用（省一次大文件复制）
    alt = Path("/tmp/aiqicha_chrome_profile") if sys.platform != "win32" else \
        Path(os.environ.get("TEMP", r"C:\temp")) / "aiqicha_chrome_profile"
    if has_login_state(alt):
        log(f"复用已有 Chrome 登录态副本：{alt}")
        return alt
    real = _real_profile_dir()
    if os.path.exists(real):
        log("首次使用双标签：正在准备 Chrome 登录态副本（跳过缓存，约十几秒）…")
        if profile.exists():
            shutil.rmtree(str(profile), ignore_errors=True)
        try:
            shutil.copytree(real, str(profile),
                            ignore=shutil.ignore_patterns(*_SKIP_PATTERNS), dirs_exist_ok=True)
        except Exception as e:
            log(f"⚠ 复制 Chrome 配置失败：{e}")
    return profile if profile.exists() else alt


def _find_chrome() -> str:
    if sys.platform == "win32":
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ]
    elif sys.platform == "darwin":
        candidates = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    else:
        candidates = ["/usr/bin/google-chrome", "/usr/bin/chromium-browser", "/usr/bin/chromium"]
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return ""


def is_shared_chrome_alive(port: int = DUAL_PORT) -> bool:
    """共享 Chrome 的调试端口是否可访问。

    只探端口、不看页面数：“Chrome 在跑但一个窗口都没了”不能当成没在跑——
    那样会再拉一个同配置目录的 Chrome，新进程只会把请求转交给老进程后自己退出，
    结果谁都连不上，界面表现就是一直“启动失败”。
    """
    try:
        return requests.get(f"http://127.0.0.1:{port}/json/version", timeout=3).status_code == 200
    except Exception:
        return False


def _kill_profile_chrome(profile: Path):
    """只关闭使用该配置目录的 Chrome（绝不动用户自己正在用的浏览器窗口）"""
    try:
        if sys.platform == "win32":
            ps = (
                "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                f"Where-Object {{ $_.CommandLine -like '*{profile.name}*' }} | "
                "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
            )
            subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, timeout=20)
        else:
            subprocess.run(["pkill", "-9", "-f", f"user-data-dir={profile}"],
                           capture_output=True, timeout=10)
        time.sleep(1.5)
    except Exception:
        pass


def ensure_shared_chrome(log, port: int = DUAL_PORT) -> bool:
    """确保有一个带调试端口的共享 Chrome 在跑（已就绪则直接复用，不重启）"""
    if is_shared_chrome_alive(port):
        return True
    chrome = _find_chrome()
    if not chrome:
        log("❌ 未找到 Chrome，请先安装 Google Chrome")
        return False
    profile = prepare_shared_profile(log)
    _kill_profile_chrome(profile)   # 同配置目录的旧实例会锁住端口/配置，必须先退
    log("正在启动共享浏览器（双标签并行）…")
    cmd = [
        chrome,
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",
        f"--user-data-dir={profile}",
        "--profile-directory=Default",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble",
        "--hide-crash-restore-bubble",
        # 附加模式下 Selenium 无法再传浏览器开关，反自动化检测必须在启动时带上
        "--disable-blink-features=AutomationControlled",
        "--disable-infobars",
    ]
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        log(f"❌ 启动共享浏览器失败：{e}")
        return False
    for _ in range(CHROME_WAIT_SECONDS):
        time.sleep(1)
        if is_shared_chrome_alive(port):
            log("共享浏览器已就绪")
            return True
    log("❌ 共享浏览器启动超时")
    return False


def _is_blank_url(url: str) -> bool:
    u = (url or "").strip().lower()
    return any(u.startswith(b) for b in BLANK_TAB_URLS)


def list_page_targets(port: int = DUAL_PORT) -> list:
    """共享浏览器里所有可见页面（type=page）"""
    try:
        data = requests.get(f"http://127.0.0.1:{port}/json/list", timeout=3).json()
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [t for t in data if isinstance(t, dict) and t.get("type") == "page"]


def open_window(port: int = DUAL_PORT, url: str = "about:blank"):
    """在共享 Chrome 里新开一个窗口，返回 target id（失败返回 None）。

    Selenium 附加时至少要有一个窗口能拿句柄，0 窗口的 Chrome 会让附加直接报错。
    """
    try:
        return (requests.put(f"http://127.0.0.1:{port}/json/new?url={url}", timeout=5).json() or {}).get("id")
    except Exception:
        return None


def cleanup_blank_tabs(log, keep_ids=(), port: int = DUAL_PORT) -> int:
    """关掉共享浏览器里遗留的空白窗口（about:blank / chrome://newtab）。

    这些窗口是之前双标签会话、切换数据源时留下的，开着不干活也要占内存。
    只按“这个页面本身就是空白页”关，带内容的页面（采集窗口、用户自己的页面）一律不碰；
    keep_ids 里指定的会话标签即使正好是空白也不关。返回实际关闭数。
    """
    keep = {str(i) for i in keep_ids if i}
    pages = list_page_targets(port)
    targets = [t for t in pages
               if _is_blank_url(t.get("url")) and str(t.get("id")) not in keep]
    # 绝不把最后一个窗口也关掉：0 窗口的 Chrome 会让接下来的附加拿不到句柄，
    # 刚启动的共享浏览器只有一个空白窗口，那个窗口就是给会话用的
    targets = targets[:max(0, len(pages) - 1)]
    if not targets:
        return 0
    closed = 0
    for t in targets:
        tid = t.get("id")
        if not tid:
            continue
        try:
            requests.get(f"http://127.0.0.1:{port}/json/close/{tid}", timeout=3)
            closed += 1
        except Exception:
            pass
    if closed:
        log(f"🧹 已清理 {closed} 个空白窗口（共享浏览器现在剩 {len(list_page_targets(port))} 个页面）")
    return closed


def _session_tab_ids(qzd_crawler, aqc_crawler) -> list:
    """两个会话自己正在用的标签 id，用于清理时避开（拿不到就返回空，不因此误关）"""
    ids = []
    try:
        if getattr(aqc_crawler, "pin_tab_id", None):
            ids.append(aqc_crawler.pin_tab_id)
    except Exception:
        pass
    try:
        drv = getattr(qzd_crawler, "driver", None)
        if drv is not None:
            ids.append(drv.current_window_handle)
    except Exception:
        pass
    return ids


def split_parity(companies: list):
    """按单双数分工：第 1、3、5… 家 → 企知道；第 2、4、6… 家 → 爱企查。

    返回 (企知道名单, 爱企查名单)。按原始位置切分，所以续采时同一家公司仍归同一个平台。
    """
    qzd = [name for i, name in enumerate(companies) if i % 2 == 0]
    aqc = [name for i, name in enumerate(companies) if i % 2 == 1]
    return qzd, aqc


def attach_qizhidao(crawler, log, port: int = DUAL_PORT) -> bool:
    """企知道会话附加到共享浏览器（已有会话则直接复用）"""
    try:
        if crawler.driver is not None and crawler.shared_session:
            return True
        if crawler.driver is not None:
            crawler.close_browser()
        if crawler.start_browser(headed=True, attach_port=port):
            return True
        # 最常见的卡点是共享浏览器一个窗口都没有，补开一个再试一次
        if not list_page_targets(port) and open_window(port):
            log("共享浏览器没有窗口，已补开一个，重新附加企知道会话…")
            return bool(crawler.start_browser(headed=True, attach_port=port))
        return False
    except Exception as e:
        log(f"❌ 企知道会话附加失败：{e}")
        return False


def attach_aiqicha(crawler, log, port: int = DUAL_PORT) -> bool:
    """爱企查会话附加到共享浏览器（已占用标签则直接复用）"""
    try:
        if crawler.pin_tab_id and is_shared_chrome_alive(port):
            return True
        return bool(crawler.start_browser(headed=True, attach=True))
    except Exception as e:
        log(f"❌ 爱企查会话附加失败：{e}")
        return False


def setup_dual(qzd_crawler, aqc_crawler, log, port: int = DUAL_PORT):
    """开启双标签：启动共享浏览器 + 两个会话各自附加。返回 (是否成功, 提示信息)"""
    if not ensure_shared_chrome(log, port):
        return False, "共享浏览器启动失败"
    # 上一轮可能把窗口全关完了（进程还在但 0 窗口），先补一个，否则下面附加必挂
    if not list_page_targets(port):
        if not open_window(port):
            return False, "共享浏览器无可用窗口（请手动结束 Chrome 进程后重试）"
        log("共享浏览器无窗口，已新开一个")
    # 在两个会话附加之前先洗一次：此刻还没有属于本会话的标签，任何空白页
    # 都是上一轮遗留的，关掉不会误伤正在采集的窗口（清理内部会保留至少一个窗口）
    cleanup_blank_tabs(log, keep_ids=_session_tab_ids(qzd_crawler, aqc_crawler), port=port)
    if not attach_qizhidao(qzd_crawler, log, port):
        return False, "企知道会话附加失败"
    if not attach_aiqicha(aqc_crawler, log, port):
        return False, "爱企查会话附加失败"
    # Chrome 自己启动时可能带一个 chrome://newtab 空窗口，附加前那次清理为了保底窗口
    # 没法关它；现在两个会话都落到真实页面上了，再洗一次把这种空窗口收掉
    cleanup_blank_tabs(log, keep_ids=_session_tab_ids(qzd_crawler, aqc_crawler), port=port)
    return True, "双标签已就绪"


def release_dual(qzd_crawler, aqc_crawler, log, port: int = DUAL_PORT):
    """收工：两个会话各自关掉自己的窗口/标签，共享浏览器保留（下次直接复用）。"""
    for crawler, name in ((qzd_crawler, "企知道"), (aqc_crawler, "爱企查")):
        try:
            crawler.close_browser()
        except Exception:
            pass
    # 两个会话已经收工，此刻洗一次能把整个过程的空白残留清掉
    cleanup_blank_tabs(log, port=port)
    if not is_shared_chrome_alive(port):
        log("ℹ 共享浏览器已退出，下次开启双标签时会自动重新启动")
