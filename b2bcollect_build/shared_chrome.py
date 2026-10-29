"""三站并行采集的共享浏览器管理：一个 Chrome 进程 + 每站固定独占一个标签。

设计要点（沿用 riskbird dual_session 已实机验证的并行模型，按三站扩展）：
- 三个站点会话共用同一个带调试端口的 Chrome，但**各自独占一个标签**：每个采集器
  用裸 CDP，每条命令都显式带上自己那个标签的 webSocketDebuggerUrl，命令通道彼此
  独立 → 三个线程可真并行、互不抢占活动标签。
- 「每个网站只用一个标签，不创建新的标签」：acquire_tab 优先复用已属于该站点的标签，
  没有才新建一个并记住 target id（pin）；之后所有导航都在这个标签里 Page.navigate，
  绝不再开新标签，避免抢焦点/后台节流/标签堆积。
- 这三个站点都**无需登录**，故不必复制用户真实 Chrome 配置，直接用独立配置目录，
  启动更快、也不干扰用户日常浏览器。
"""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests

PORT = 9255                     # 本工具专用调试端口（与 riskbird 9233、1688 9244 错开）
CHROME_WAIT_SECONDS = 25
BLANK_TAB_URLS = ("about:blank", "chrome://newtab/", "chrome://new-tab-page/")

# 并发安全：三站线程可能同时冷启动 / 同时取标签。
_LAUNCH_LOCK = threading.RLock()      # 保证只拉起一个共享 Chrome
_ALLOC_LOCK = threading.RLock()       # 保证标签分配互斥
# 标签认领表 port -> {tab_id: domain}：记录每个标签已被哪个站点独占，
# 避免冷启动时三站的 acquire_tab 都复用同一个空白标签（每站必须独占一个标签）。
_CLAIMED = {}


def shared_profile_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("TEMP", r"C:\temp")) / "chrome_b2bcollect_profile"
    return Path("/tmp/chrome_b2bcollect_profile")


def _find_chrome() -> str:
    if sys.platform == "win32":
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ]
    elif sys.platform == "darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            os.path.expanduser("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        ]
    else:
        candidates = ["/usr/bin/google-chrome", "/usr/bin/chromium-browser", "/usr/bin/chromium"]
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return ""


def is_alive(port: int = PORT) -> bool:
    """只探调试端口、不看页面数：Chrome 在跑但 0 窗口时也不能当成没在跑，
    否则会再拉一个同配置目录的进程，新进程只会把请求转交老进程后自己退出。"""
    try:
        return requests.get(f"http://127.0.0.1:{port}/json/version", timeout=3).status_code == 200
    except Exception:
        return False


def _kill_profile_chrome(profile: Path):
    """只关闭使用本工具配置目录的 Chrome（绝不动用户自己的浏览器窗口）。"""
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


def ensure_shared_chrome(log, port: int = PORT, headed: bool = True) -> bool:
    """确保有一个带调试端口的共享 Chrome 在跑（已就绪则直接复用，不重启）。
    并发安全：三站线程可能同时冷启动，用启动锁 + double-check 保证只拉起一个 Chrome，
    否则后起的进程会 pkill 掉先起的、或抢同一 profile 单实例锁导致全部失败。"""
    if is_alive(port):
        return True
    with _LAUNCH_LOCK:
        if is_alive(port):
            return True
        return _launch_shared_chrome(log, port, headed)


def _launch_shared_chrome(log, port: int = PORT, headed: bool = True) -> bool:
    chrome = _find_chrome()
    if not chrome:
        log("❌ 未找到 Chrome，请先安装 Google Chrome")
        return False
    profile = shared_profile_dir()
    try:
        profile.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    _kill_profile_chrome(profile)   # 同配置目录旧实例会锁端口/配置，必须先退
    log("正在启动共享浏览器（三站并行，各占一个标签）…")
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
        # 裸 CDP 附加时无法再传开关，反自动化检测必须在启动时带上
        "--disable-blink-features=AutomationControlled",
        "--disable-infobars",
        # 后台化支持：窗口最小化/标签不可见时不降级定时器、不冻结渲染进程，
        # 否则最小化后 JS 渲染站点的水合/就绪等待会被拖慢甚至静默卡死。
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--window-size=1440,960",
    ]
    if not headed:
        cmd.append("--headless=new")
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        log(f"❌ 启动共享浏览器失败：{e}")
        return False
    for _ in range(CHROME_WAIT_SECONDS):
        time.sleep(1)
        if is_alive(port):
            log("共享浏览器已就绪")
            return True
    log("❌ 共享浏览器启动超时")
    return False


def list_page_targets(port: int = PORT) -> list:
    """共享浏览器里所有可见页面（type=page）。查询失败返回 []。"""
    try:
        data = requests.get(f"http://127.0.0.1:{port}/json/list", timeout=3).json()
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [t for t in data if isinstance(t, dict) and t.get("type") == "page"]


def open_tab(port: int = PORT, url: str = "about:blank"):
    """在共享 Chrome 里新开一个标签，返回 target dict（失败返回 None）。"""
    try:
        r = requests.put(f"http://127.0.0.1:{port}/json/new?url={url}", timeout=6).json()
        return r if isinstance(r, dict) and r.get("id") else None
    except Exception:
        # 个别 Chrome 版本 /json/new 只接受 GET
        try:
            r = requests.get(f"http://127.0.0.1:{port}/json/new?url={url}", timeout=6).json()
            return r if isinstance(r, dict) and r.get("id") else None
        except Exception:
            return None


def close_tab(port: int = PORT, target_id: str = ""):
    if not target_id:
        return
    try:
        requests.get(f"http://127.0.0.1:{port}/json/close/{target_id}", timeout=3)
    except Exception:
        pass


def _is_blank(url: str) -> bool:
    u = (url or "").strip().lower()
    return any(u.startswith(b) for b in BLANK_TAB_URLS)


def acquire_tab(port: int = PORT, url: str = "about:blank", domain: str = "",
                pin_id: str = "", log=None):
    """给某个站点取得它独占的那一个标签（并发安全 + 认领互斥）：
      1) pin_id 仍在 → 直接复用（跨重启/续采不重复建标签）；
      2) 否则找一个已停在该站点域名下、且未被别的站点认领的标签复用；
      3) 否则找一个**未被认领的**空白标签复用（不新建）；
      4) 都没有才新建一个标签。
    返回 (target_dict, pin_id)。绝不为一次导航多开标签；也绝不把已被别的站点
    认领的标签抢过来（否则冷启动时三站会挤到同一个空白标签，互相把对方页面导走）。"""
    log = log or (lambda m: None)
    with _ALLOC_LOCK:
        claimed = _CLAIMED.setdefault(port, {})
        tabs = list_page_targets(port)
        # 1) 已 pin 的标签还活着
        if pin_id:
            for t in tabs:
                if t.get("id") == pin_id:
                    claimed[pin_id] = domain
                    return t, pin_id
        # 2) 复用已停在该域名、且归属本站(或未认领)的标签
        if domain:
            for t in tabs:
                tid = t.get("id")
                if domain in (t.get("url") or "") and claimed.get(tid, domain) == domain:
                    claimed[tid] = domain
                    return t, tid
        # 3) 复用一个未被认领的空白标签
        for t in tabs:
            tid = t.get("id")
            if tid not in claimed and _is_blank(t.get("url")):
                claimed[tid] = domain
                return t, tid
        # 4) 新建一个（每个站点全程只会走到这里一次）
        t = open_tab(port, url)
        if t:
            claimed[t.get("id")] = domain
            return t, t.get("id")
        # 兜底：拿第一个标签
        if tabs:
            return tabs[0], tabs[0].get("id")
        return None, pin_id


def release_tabs(pinned_ids, port: int = PORT, log=None):
    """收工：只关掉本工具 pin 的标签，共享浏览器保留（下次直接复用）。
    同时解除这些标签的认领，避免认领表残留导致下次误判标签已被占用。"""
    log = log or (lambda m: None)
    claimed = _CLAIMED.setdefault(port, {})
    tabs = list_page_targets(port)
    keep_alive = len(tabs)
    for tid in pinned_ids or []:
        claimed.pop(tid, None)
        if tid and keep_alive > 1:
            close_tab(port, tid)
            keep_alive -= 1


def _browser_cmd(port, method, params, timeout=8):
    """向浏览器级 CDP 端点发一条命令（只取 id=1 的响应，忽略事件帧）。"""
    try:
        import websocket as _ws
        info = requests.get(f"http://127.0.0.1:{port}/json/version", timeout=4).json()
        url = info.get("webSocketDebuggerUrl") or ""
        if not url:
            return None
        ws = _ws.create_connection(url, timeout=timeout)
        try:
            ws.send(json.dumps({"id": 1, "method": method, "params": params or {}}))
            deadline = time.time() + timeout
            while time.time() < deadline:
                raw = ws.recv()
                if not raw:
                    continue
                msg = json.loads(raw)
                if msg.get("id") == 1:
                    return msg
        finally:
            try:
                ws.close()
            except Exception:
                pass
    except Exception:
        return None
    return None


def _set_window_state(window_ids, state, port):
    done = 0
    for wid in window_ids:
        ok = _browser_cmd(port, "Browser.setWindowBounds",
                          {"windowId": wid, "bounds": {"windowState": state}})
        if ok and "error" not in ok:
            done += 1
    return done


def set_tabs_window_state(tab_ids, state="minimized", port: int = PORT):
    """把本工具标签所在窗口最小化(minimized)/还原(normal)，按 windowId 去重。
    人在环兼容：命中验证码时由界面层调 state="normal" 还原窗口让用户过验证。
    返回操作成功的窗口数；拿不到窗口信息返回 0（不抛异常，后台化失败不影响采集）。"""
    seen, window_ids = set(), []
    for tid in tab_ids or []:
        if not tid or tid in seen:
            continue
        seen.add(tid)
        r = _browser_cmd(port, "Browser.getWindowForTarget", {"targetId": tid})
        window_id = ((r or {}).get("result") or {}).get("windowId")
        if window_id:
            window_ids.append(window_id)
    return _set_window_state(window_ids, state, port)


def set_all_windows_state(state="minimized", port: int = PORT):
    """把共享 Chrome 的**所有窗口**最小化/还原（后台化总开关）。
    与 set_tabs_window_state 的区别：不依赖本工具 CDP pin 的标签——Selenium 附加
    通道（1688/企知道）自己开的标签/窗口也一并覆盖。旧实现只按 pin_tab_id 最小化，
    而 Selenium 站 pin_tab_id 恒为空，导致企知道独占窗口永远游离在后台化之外、
    一直杵在屏幕最前面（用户反馈的“窗口不停弹出/收起”主因之一）。
    共享 Chrome 是本工具专属实例（独立 profile/端口 9255），整窗后台化不会误伤用户日常浏览器。"""
    seen, window_ids = set(), []
    for t in list_page_targets(port):
        tid = t.get("id")
        if not tid:
            continue
        r = _browser_cmd(port, "Browser.getWindowForTarget", {"targetId": tid})
        window_id = ((r or {}).get("result") or {}).get("windowId")
        if window_id and window_id not in seen:
            seen.add(window_id)
            window_ids.append(window_id)
    return _set_window_state(window_ids, state, port)
