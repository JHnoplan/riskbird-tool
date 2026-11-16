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

# 失败原因收成一行只准一个把手（第 ㉓ 族）：真源在同目录 text_shape.py，零依赖。
from text_shape import first_line

PORT = 9255                     # 本工具专用调试端口（与 riskbird 9233、1688 9244 错开）
CHROME_WAIT_SECONDS = 25
BLANK_TAB_URLS = ("about:blank", "chrome://newtab/", "chrome://new-tab-page/")

# 并发安全：三站线程可能同时冷启动 / 同时取标签。
_LAUNCH_LOCK = threading.RLock()      # 保证只拉起一个共享 Chrome
_ALLOC_LOCK = threading.RLock()       # 保证标签分配互斥
# 标签认领表 port -> {tab_id: domain}：记录每个标签已被哪个站点独占，
# 避免冷启动时三站的 acquire_tab 都复用同一个空白标签（每站必须独占一个标签）。
_CLAIMED = {}


PROFILE_DIR_NAME = "chrome_b2bcollect_profile"


# ── 本机调试端口的 HTTP 通道（全工具唯一入口）──────────────────────────
# 为什么不能就地用 requests.get：requests 默认 trust_env=True，会读 HTTP_PROXY /
# HTTPS_PROXY / ALL_PROXY 环境变量。开着系统代理、VPN、Clash/抓包工具的电脑上，
# 连 http://127.0.0.1:9255/json/list 都要先丢给代理转一圈。2026-11-14 本机实测：
# 一旦 HTTP_PROXY 指向一个不应答的地址，单条 CDP 命令从中位 **4.8 ms 变成 24000 ms**，
# 并且列标签超时被 except 吞成 [] → 上层判成「页面是空的」→ 用户看到的是
# 「一家也没采到」而不是任何报错。控制面是本机回环，任何情况下都不该经过代理
# （Chrome 自家的 DevTools 客户端也是直连），所以这里显式把代理开关关掉。
# 顺手拿到第二笔收益：会话复用＝TCP 连接复用，今天每条命令都重新建一次连接。
# 线程本地：requests.Session 不是线程安全的，而采集是三~八路并行各自列标签。
_LOCAL_SESSIONS = threading.local()


def local_session() -> requests.Session:
    """本机回环控制面专用会话：绝不走代理、连接复用、每线程一条。"""
    s = getattr(_LOCAL_SESSIONS, "session", None)
    if s is None:
        s = requests.Session()
        s.trust_env = False     # 不读 *_PROXY 环境变量、不读 .netrc、不读 PAC
        s.proxies = {}          # 再上一道：即使有人在别处塞了代理也不生效
        _LOCAL_SESSIONS.session = s
    return s


def local_url(port: int, path: str) -> str:
    """调试端口的完整地址。集中一处拼，避免哪天换 0.0.0.1/IPv6 时漏掉调用点。"""
    return f"http://127.0.0.1:{port}{path}"



def shared_profile_dir() -> Path:
    """共享 Chrome 的配置目录落点：口径全交给 tool_config，本函数只声明「老位置在哪」。

    以前这里直接写 ``%TEMP%\\chrome_b2bcollect_profile``，而 TEMP 有三条与「登录态要活
    多久」完全相反的性质：**每个用户各一份**（换 Windows 账号=换目录）、**清理程序随时
    可删**（磁盘清理/存储感知默认就会动它）、**注销即弃**。2026-11-13 实机那次换账号后
    「需要登录的几路一家都采不到、不需要登录的八站照常出数」就是这么来的——界面没有
    报错，日志里也看不出和登录有关。持久位置（Windows ``%LOCALAPPDATA%\\b2bcollect``、
    其余 ``~/.b2bcollect``）由 tool_config 一处决定，老位置作为 legacy 传进去：那边要是
    还留着登录态就先**复制**接管过来，绝不删源、绝不让用户重做一遍登录。
    """
    import tool_config as _tc
    legacy = [_tc.legacy_temp_root() / PROFILE_DIR_NAME,
              Path("/tmp") / PROFILE_DIR_NAME]
    return _tc.browser_profile_dir(PROFILE_DIR_NAME, legacy_dirs=legacy)


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
        return local_session().get(local_url(port, "/json/version"), timeout=3).status_code == 200
    except Exception:
        return False


# ── 明文 http 那一跳必须真的发得出去（这台浏览器替我们做主的第 21 件事）──────────
# Chrome 从「始终使用更安全的连接」（HTTPS-First / HttpsUpgrades）开始，会把**顶层** http
# 导航先升级成 https；升级失败时它**不退回明文**，而是当场取消这次请求 —— CDP 侧收到的
# 就是 `net::ERR_BLOCKED_BY_CLIENT`，页面落在 `chrome-error://chromewebdata/`。
# 本机差分实测（2026-11-14，同机两个 headless Chrome，只差这一个开关，其余参数一致）：
#   不带参数：http://example.com、http://china.makepolo.com/…、http://xxx.cn.makepolo.com/
#             三个全部 ERR_BLOCKED_BY_CLIENT，正文 39 字符；
#   带上参数：三个全部正常落到请求的那一页（example.com 1974 字符、马可波罗拿到站方原文）。
# 为什么这一条对本工具是致命的：马可波罗的深链本来就是明文 http，而它的 https 证书连自己
# 的域名都不覆盖（curl 实测 `no alternative certificate subject name matches target host
# name 'china.makepolo.com'`）。于是「借浏览器标签把 JS 质询壳跑完」这条腿三次全废，
# 日志里只剩一句「这条腿帮不上忙」，把环境自己拦掉了说成站点不给过。
# 为什么只关自动升级、绝不关证书校验（`--ignore-certificate-errors`）：后者会让这台带着
# 1688／riskbird 登录态的浏览器对**所有站点**失去中间人防护，是拿账号安全换一次取页；
# 而「站点只给明文就按明文请求」与裸 HTTP 通道今天的行为等价，不新增任何风险面。
# 为什么是**一份元组**而不是一个字符串：1688 与 riskbird 各自是独立包，import 不到这里，
# 只能内联逐字节相同的副本（`ali1688_crawler._chrome_args`、`dual_session` 的启动列表）。
# 「同一个环境开关只在一处生效」正是这一族事故的形状，所以构建前硬门第 31 条（31g）按整段
# 对拍三份副本，并规定：任何在字符串常量里配 Chrome 启动参数的文件，要么带上这份开关，
# 要么在门的豁免表里写下「这个浏览器为什么不必带」。
NO_HTTPS_UPGRADE_FEATURES = "HttpsUpgrades,HttpsFirstModeV2,HttpsFirstBalancedMode"
CHROME_NO_UPGRADE_FLAGS = (f"--disable-features={NO_HTTPS_UPGRADE_FEATURES}",)
_flag_warned = []                      # 复用旧 Chrome 时只提示一次（每进程）


def _profile_cmdline() -> str:
    """本工具配置目录那个 Chrome 的启动命令行（读不到就回空串，绝不抛）。
    只用于判断「已在跑的实例带没带我们要的开关」，不做任何进程管理。
    按目录**名**而不是全路径匹配：登录态目录搬迁过（%TEMP% → 持久目录），旧实例可能还
    挂在老位置上，那一枚同样要能认出来。"""
    try:
        if sys.platform == "win32":
            ps = ("Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                  f"Where-Object {{ $_.CommandLine -like '*{PROFILE_DIR_NAME}*' }} | "
                  "Select-Object -First 1 -ExpandProperty CommandLine")
            out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                                 capture_output=True, text=True, timeout=10)
            return (out.stdout or "").strip()
        env = dict(os.environ, COLUMNS="20000")     # 不然 ps 会按终端宽度截断长命令行
        out = subprocess.run(["ps", "-Ao", "args="], capture_output=True, text=True,
                             timeout=10, env=env)
        return next((ln.strip() for ln in (out.stdout or "").splitlines()
                     if f"user-data-dir=" in ln and PROFILE_DIR_NAME in ln), "")
    except Exception:
        return ""


def missing_no_upgrade_flag() -> bool:
    """已在跑的共享 Chrome 是不是**没有**关闭自动升级 https（本工具自己拉起的都算带上）。
    读不到进程信息时按「不缺」处理：宁可少提示一次，也不要在证据不足时吓人。"""
    line = _profile_cmdline()
    return bool(line) and "HttpsUpgrades" not in line


def ensure_shared_chrome(log, port: int = PORT, headed: bool = True) -> bool:
    """确保有一个带调试端口的共享 Chrome 在跑（已就绪则直接复用，不重启）。
    并发安全：三站线程可能同时冷启动，用启动锁 + double-check 保证只拉起一个 Chrome，
    否则后起的进程会 pkill 掉先起的、或抢同一 profile 单实例锁导致全部失败。"""
    if is_alive(port):
        # 复用旧实例时那一跳的成败已经由启动参数决定了：早先（本工具升级前）起来的 Chrome
        # 没带这个开关，之后所有明文 http 深链都会被浏览器自己拦掉。这里必须说出来 ——
        # 不说，用户看到的就是「站点像是不给过」；重启一次本工具（或让它自己拉 Chrome）即好。
        if missing_no_upgrade_flag() and not _flag_warned:
            _flag_warned.append(1)
            log("⚠ 共享浏览器是先前就开着的，没带「不强制升级 https」的开关：明文 http 的"
                "商铺页/详情页会被浏览器拦在加载之前（不是站点在拦）。退出本工具再重新打开"
                "即可自动带上，之后无需再做别的")
        return True
    with _LAUNCH_LOCK:
        if is_alive(port):
            return True
        return _launch_shared_chrome(log, port, headed)


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
        # 明文 http 深链不能被浏览器自己升级成 https 再拦掉（由来与差分实测见上面
        # NO_HTTPS_UPGRADE_FEATURES 那段：马可波罗那条借标签的腿就是这么三次全废的）
        *CHROME_NO_UPGRADE_FLAGS,
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
        data = local_session().get(local_url(port, "/json/list"), timeout=3).json()
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [t for t in data if isinstance(t, dict) and t.get("type") == "page"]


def open_tab(port: int = PORT, url: str = "about:blank"):
    """在共享 Chrome 里新开一个标签，返回 target dict（失败返回 None）。"""
    try:
        r = local_session().put(local_url(port, f"/json/new?url={url}"), timeout=6).json()
        return r if isinstance(r, dict) and r.get("id") else None
    except Exception:
        # 个别 Chrome 版本 /json/new 只接受 GET
        try:
            r = local_session().get(local_url(port, f"/json/new?url={url}"), timeout=6).json()
            return r if isinstance(r, dict) and r.get("id") else None
        except Exception:
            return None


def close_tab(port: int = PORT, target_id: str = ""):
    """关掉一枚标签。**返回空串＝确认这一枚已经不在了；否则返回一句可读的失败原因。**

    为什么必须交回事实而不是 `except Exception: pass`（第 32g 条当场抓到）：
    上层 `cdp_base.release_tab` 写着「关失败必须开口」，可它靠的是**异常往上传**；
    这一层把异常吞掉，上面那句就成了永远走不到的死分支——工具会心平气和地告诉用户
    「已经收工」，而共享 Chrome 里其实还挂着那枚标签。最底层把事实抹平，上面整座账
    就退化成自证清白，所以「谁拿着标签谁还」「关失败必须开口」这两条不变量必须由
    这一层先把真相交出来。

    只信回执不信事实：Chrome 对不存在的 target 也回 HTTP 200（body 写 No such target），
    所以关完再查一次列表——**在列表里查不到才算关掉**，与 `list_page_targets` 同一口径。"""
    if not target_id:
        return "没给出 target_id，这一枚没关"
    said = ""
    try:
        r = local_session().get(local_url(port, f"/json/close/{target_id}"), timeout=3)
        said = f"HTTP {r.status_code} {first_line((r.text or '').strip(), 60)}"
    except Exception as e:
        return f"关闭请求没送达（{type(e).__name__}）：{first_line(e, 80)}"
    ids = {t.get("id") for t in list_page_targets(port)}
    if target_id not in ids:
        return ""
    return f"标签仍在列表里（关闭回执 {said or '无'}）"


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
            reason = close_tab(port, tid)
            if reason:
                # 收工路径上也一样不许静默：这一枚没关掉，用户需要知道去哪手动收
                log(f"⚠ 收工归还标签 {str(tid)[:12]} 没成功：{reason}——"
                    f"共享 Chrome 里可能还挂着它，请手动关掉（不影响已采数据）")
            else:
                keep_alive -= 1


def _browser_cmd(port, method, params, timeout=8):
    """向浏览器级 CDP 端点发一条命令（只取 id=1 的响应，忽略事件帧）。"""
    try:
        import websocket as _ws
        info = local_session().get(local_url(port, "/json/version"), timeout=4).json()
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
