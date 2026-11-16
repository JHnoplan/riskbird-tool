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
- 夜间无人值守：爱企查被拦超过约定时长没有人工处理时，走 NameHandoff 把剩余名单
  交给企知道接着采，避免一整晚只采到一半。
"""
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests

import runtime_paths

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


# 明文 http 不能被浏览器自己升级成 https 再拦掉：升级失败时 Chrome 不抛异常，把这一跳直接取消
# 并落在 `chrome-error://chromewebdata/`，于是「环境自己拦掉了」在日志里长成「站点不给过」。
# 由来与同机差分实测见 b2bcollect_build/shared_chrome 那段注释。riskbird 是独立包，import 不到
# 那一份，所以这里内联**逐字节相同**的副本，由构建前硬门第 31 条（31g）整段对拍三份。
NO_HTTPS_UPGRADE_FEATURES = "HttpsUpgrades,HttpsFirstModeV2,HttpsFirstBalancedMode"
CHROME_NO_UPGRADE_FLAGS = (f"--disable-features={NO_HTTPS_UPGRADE_FEATURES}",)


def shared_profile_dir() -> Path:
    """共享 Chrome 使用的配置目录（含用户真实登录态的副本）"""
    return runtime_paths.legacy_temp_root() / "chrome_debug_profile"


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
    alt = runtime_paths.legacy_temp_root() / "aiqicha_chrome_profile"
    if has_login_state(alt):
        log(f"复用已有 Chrome 登录态副本：{alt}")
        return alt
    real = _real_profile_dir()
    if os.path.exists(real):
        log("首次使用双标签：正在复制 Chrome 登录态（跳过缓存目录，只此一次）…")
        if profile.exists():
            shutil.rmtree(str(profile), ignore_errors=True)
        t0 = time.monotonic()
        try:
            shutil.copytree(real, str(profile),
                            ignore=shutil.ignore_patterns(*_SKIP_PATTERNS), dirs_exist_ok=True)
            # 这一步是“每次打开都要等很久”里最慢的一环（用户 Chrome 配置可能几个 G），
            # 打出秒数就能分清是复制慢还是驱动慢
            log(f"登录态副本已就绪，用时 {time.monotonic() - t0:.1f} 秒")
        except Exception as e:
            log(f"⚠ 复制 Chrome 配置失败（用时 {time.monotonic() - t0:.1f} 秒）：{e}")
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
        # 与 b2bcollect 共享 Chrome 同一枚开关：本工具拉起的浏览器对明文 http 一视同仁，
        # 不许「某一条路径的浏览器带、另一条不带」——那种差异正是这一族事故的形状（见上面副本）
        *CHROME_NO_UPGRADE_FLAGS,
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


# Chrome 的 /json/close 是**异步**的：回执 200（body 常写 Target is closing）后，这一枚还能在
# /json/list 里挂上几百毫秒。只查一次就报「关不掉」会让用户去手动收一枚早就关掉的窗口
# （2026-11-16 第十二轮实机在 b2bcollect 那一侧刚吃过这个假警）；本目录是独立交付包，
# 拿不到 `shared_chrome.TAB_CLOSE_SETTLE`，所以内联一份同节奏。
TAB_CLOSE_SETTLE = (0.2, 0.4, 0.8)


class TabListUnavailable(RuntimeError):
    """标签列表**压根没读到**：这和「读到了、里面是空的」是两句话。

    与 `b2bcollect_build/shared_chrome.py` 同一口径；本目录是独立交付包（装不进那一份），
    所以内联一份同名同义的把手。为什么要分这一档：旧写法把查询失败吞成 []，于是
    「已清理 N 个窗口（现在剩 0 个页面）」这句话在控制面不通时照样念得出来——把
    「我不知道」念成「没事」，用户就不会去查那个其实还挂着的窗口。"""


def _first_line(e) -> str:
    """失败原因只取整行、**不按字符数切**（切在中间就长成半截词，见第 30 条）。"""
    s = str(e).strip()
    return s.splitlines()[0] if s else e.__class__.__name__


def list_page_targets(port: int = DUAL_PORT, strict: bool = False) -> list:
    """共享浏览器里所有可见页面（type=page）。

    `strict=False`（默认）查询失败回 []：调用方只把它当「现在挂着哪些窗口」的提示。
    `strict=True` 把失败原因抛成 `TabListUnavailable`：凡是**拿这份列表当证据下结论**的
    地方（「这一枚真的关掉了吗」「现在还剩几个」）都必须走这一档。
    """
    try:
        data = requests.get(f"http://127.0.0.1:{port}/json/list", timeout=3).json()
    except Exception as e:
        if strict:
            raise TabListUnavailable(f"{type(e).__name__}：{_first_line(e)}") from e
        return []
    if not isinstance(data, list):
        if strict:
            raise TabListUnavailable(f"回执不是一页列表（读到的是 {type(data).__name__}）")
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
    try:
        pages = list_page_targets(port, strict=True)
    except TabListUnavailable as e:
        # 读不到列表就当「没有空白窗口要洗」然后一声不响地返回 0，与上面那一族同形：
        # 下一轮开窗占内存的真相就成了没人知道的一件事。这里不猜，只把没核对说出来。
        log(f"⚠ 清理空白窗口这一步没读到标签列表（{e}）：没核对成，不代表已经干净")
        return 0
    targets = [t for t in pages
               if _is_blank_url(t.get("url")) and str(t.get("id")) not in keep]
    # 绝不把最后一个窗口也关掉：0 窗口的 Chrome 会让接下来的附加拿不到句柄，
    # 刚启动的共享浏览器只有一个空白窗口，那个窗口就是给会话用的
    targets = targets[:max(0, len(pages) - 1)]
    if not targets:
        return 0
    # 回执不是事实：Chrome 的 `/json/close` 是**异步**的（回执 200 后这一枚还能在列表里
    # 挂上几百毫秒），而对已经不存在的 target 它也回 200（body 写 No such target）。
    # 旧写法“请求没抛异常”就 `closed += 1`，把两个方向都记错了：没关掉的计成了战绩，
    # 本来就不在的也算到我们头上。现在先收集“待确认”，统一复查一次列表才下结论。
    # 而 `except …: pass` 那一格吞掉的正是「这一枚还挂在用户屏幕上」（第 32g 同族）。
    pending, reasons = [], []
    for t in targets:
        tid = t.get("id")
        if not tid:
            continue
        try:
            r = requests.get(f"http://127.0.0.1:{port}/json/close/{tid}", timeout=3)
        except Exception as e:
            reasons.append(f"关闭请求没送达（{type(e).__name__}：{_first_line(e)}）")
            pending.append(tid)
            continue
        if "no such target" in (getattr(r, "text", "") or "").lower():
            continue          # 我们动手前它就不在了，不计进「本次清理了几个」
        pending.append(tid)
    closed = 0
    if pending:
        # 逐次复查（异步关闭要给它落地时间）：任何一次查不到就算关掉，每次都还在才算没关掉
        still, alive = list(pending), None
        for gap in TAB_CLOSE_SETTLE:
            time.sleep(gap)
            try:
                alive = {str(x.get("id")) for x in list_page_targets(port, strict=True)}
            except TabListUnavailable as e:
                reasons.append(f"复查标签列表没送达（{e}）：{len(still)} 枚不确定还在不在")
                break
            still = [tid for tid in still if str(tid) in alive]   # 还在列表里的才算没关掉
            if not still:
                break
        closed = len(pending) - len(still)
        if still and alive is not None:
            reasons.append(f"{len(still)} 枚回执 200 但逐次复查仍在列表里")
    if closed:
        try:
            tail = f"（共享浏览器现在剩 {len(list_page_targets(port, strict=True))} 个页面）"
        except TabListUnavailable as e:
            tail = f"（现在剩几个页面没读到：{e}）"
        log(f"🧹 已清理 {closed} 个空白窗口{tail}")
    if reasons:
        log(f"⚠ 清理空白窗口这一步有 {len(reasons)} 处没做成（{'；'.join(reasons[:3])}）——"
            f"这里只关空白页、不碰带内容的窗口，看不惯可以手动收")
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


class NameHandoff:
    """爱企查撑不住时把剩余名单交给企知道接着采（夜间挂机无人值守场景）。

    交接面很窄，只靠一个线程安全队列：
      · 爱企查只在“放弃”时放名字（give_up），收工前必须 finish（不管是否正常采完），
        否则企知道自己名单跑完后不知道还要不要等下去；
      · 企知道每采完一家取一次（pending），自己名单空了则先等对面落定（wait_for），
        否则会漏掉刚好在收尾时才移交的那几家。

    队列自己已经按名字去重（同一家不会被移交两次），取方还会再按“已采结果 +
    已排名单”过滤一层，所以原名单里重复出现的公司也不会被重复采集。
    """

    def __init__(self, log=None):
        self._cond = threading.Condition()
        self._pending = []
        self._handed = set()
        self._settled = False     # 爱企查已经收工（放弃或采完），不会再有移交
        self._gave_up = False
        self._log = log or (lambda m: None)

    def give_up(self, names, reason: str = "") -> int:
        """爱企查调用：把还没采的名单交出去，返回真正新增的条数"""
        fresh = []
        with self._cond:
            for raw in (names or []):
                n = str(raw or "").strip()
                if n and n not in self._handed:
                    self._handed.add(n)
                    self._pending.append(n)
                    fresh.append(n)
            self._gave_up = True
            self._cond.notify_all()
        if fresh:
            self._log(f"🔁 已把 {len(fresh)} 家未采名单移交给企知道"
                      + (f"：{reason}" if reason else ""))
        return len(fresh)

    def finish(self):
        """爱企查收工：企知道那边等的就是这个信号"""
        with self._cond:
            self._settled = True
            self._cond.notify_all()

    def pending(self) -> list:
        """企知道调用：取出并清空队列"""
        with self._cond:
            out, self._pending = self._pending, []
            return out

    def wait_for(self, seconds: float, should_stop=None) -> bool:
        """等到“有移交内容”或“爱企查落定”。返回是否拿到了名字。

        用条件变量而不是 sleep 轮询：爱企查在等人工验证时企知道本来就是空转，
        睡死在这里比提前退出把名单漏掉好。
        """
        deadline = time.time() + max(0.0, seconds)
        with self._cond:
            while not self._settled and not self._pending:
                left = deadline - time.time()
                if left <= 0:
                    break
                if should_stop and should_stop():
                    return False
                self._cond.wait(min(left, 0.5))
            return bool(self._pending)

    @property
    def gave_up(self) -> bool:
        with self._cond:
            return self._gave_up

    @property
    def settled(self) -> bool:
        with self._cond:
            return self._settled


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
    """开启双标签：启动共享浏览器 + 两个会话各自附加。返回 (是否成功, 提示信息)

    每一步都打秒数：“打开网页很慢”可能是复制登录态慢、共享 Chrome 起得慢、
    chromedriver 联网解析慢或首页导航慢，没读数的话只能靠猜。
    """
    t_all = t = time.monotonic()
    if not ensure_shared_chrome(log, port):
        return False, "共享浏览器启动失败"
    t = _stage(log, "启动/复用共享浏览器", t)
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
    t = _stage(log, "企知道会话附加", t)
    if not attach_aiqicha(aqc_crawler, log, port):
        return False, "爱企查会话附加失败"
    t = _stage(log, "爱企查会话附加", t)
    # Chrome 自己启动时可能带一个 chrome://newtab 空窗口，附加前那次清理为了保底窗口
    # 没法关它；现在两个会话都落到真实页面上了，再洗一次把这种空窗口收掉
    cleanup_blank_tabs(log, keep_ids=_session_tab_ids(qzd_crawler, aqc_crawler), port=port)
    _stage(log, "双标签就绪总计", t_all)
    return True, "双标签已就绪"


def _stage(log, name: str, t0: float) -> float:
    """打一段耗时。太快（<3 秒）的不写，并且不重设起点：让时间累积到真正慢的那一步"""
    now = time.monotonic()
    if now - t0 >= 3:
        log(f"⏱ {name}：{now - t0:.1f} 秒")
        return now
    return t0


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
