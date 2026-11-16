"""chrome_driver.py - 本地 chromedriver 定位与创建（尽量不走 Selenium Manager）

为什么必须有这一层：
打包成 exe 后，代码里 `Path(__file__).parent / "chromedriver-win64"` 指向的是每次启动
都变、退出即删的 _MEIxxxx 临时目录，判据永远不成立 → 每一次附加/启动浏览器都退给
Selenium Manager。它会先联网向 googlechromelabs、googleapis 查驱动版本再下载，国内网络
这两个端点经常几十秒不通，界面上的表现就是：
    「Chrome 已经打开了，但企知道/风鸟的页面迟迟不出来，要等很久很久」
（Ali1688Tool 早前就是同一个坑；现在 1688 也直接调这里的 create()，两个工具一份判据。）

这里的约束很硬：先把「手头已有的驱动」全试一遍，只有本地一个都不行时才允许退给
Selenium Manager，并且明确打出这一次慢在哪一步、用的是哪个驱动、花了几秒。
"""
import atexit
import contextlib
import os
import re
import shutil
import subprocess
import sys
import threading
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


# 驱动与浏览器大版本差多少还算「值得一试」：2026-11-14 实机用 chromedriver 156 附加
# Chrome 157.0.8103.78 **成功**，用 135 附加同一台 Chrome **失败**（报的还是那句最容易被
# 误读的 session not created: cannot connect to chrome）。旧代码因此写成了「大版本不等就
# 立刻弃掉本地驱动、交给 Selenium Manager 联网下载」，结果 SM 按 PATH 挑中那份 135，
# 1688 通道从此恒 0 家。留 2 个大版本的余量：差一点先试，真不行由建会话的结果说话。
DRIVER_MAJOR_SLACK = 2


def _path_major(path) -> int:
    """从路径里读驱动大版本：文件名注版 chromedriver.156.exe（CI 随包那三个），
    或缓存目录 <版本>/chromedriver。两处都读不出返回 0 —— 0 只表示「看不出来」，
    不表示「不配套」，所以调用方要把它排到「已知配套」之后、而不是直接淘汰。"""
    p = Path(str(path))
    m = re.search(r"chromedriver[._](\d+)", p.name, re.I)
    if m:
        return int(m.group(1))
    for part in reversed(p.parts[:-1]):
        m = re.match(r"(\d+)\.", part)
        if m:
            return int(m.group(1))
    return 0


def _driver_dirs() -> list:
    """放着「好几个注版驱动」的目录：Windows 随包三个大版本在 _MEIPASS/drivers，
    源码态在 ali1688_build/drivers（CI 的 fetch_chromedrivers.py 就写在那儿）。

    为什么 chrome_driver 也要认这个目录：打包命令把 drivers/ 打进包里是给 1688 用的，
    但企知道/风鸟/爱企查与 1688 用的是同一台 Chrome。以前只有 ali1688 自己的候选链看
    得到它，名单通道一个字没提 —— 于是 Windows 上「包里有配套驱动、这两个通道却偏要
    联网下载」的怪事。判据只留一份，两个工具都从这一处挑驱动。"""
    out = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            out.append(Path(meipass) / "drivers")
        out.append(Path(sys.executable).resolve().parent / "drivers")
    else:
        here = Path(__file__).resolve().parent
        out.append(here.parent / "ali1688_build" / "drivers")
    return [d for d in out if d.is_dir()]


def _local_session():
    """问本机调试端口专用的连接：不吃系统代理、不吃 .netrc。

    为什么专门写这一句：requests 默认照抄 HTTP_PROXY/ALL_PROXY，用户挂了代理时
    「访问 127.0.0.1」也会被送到代理那儿去，回来的是 502 或超时 —— 于是本地判据
    在代理机器上集体失明（问不到 Chrome 版本、误判端口没起，再把好端端的驱动
    当不配套弃掉）。这些都是同机回环，没有任何理由经过代理。

    必须走 Session 而不是给 requests.get(...) 塞参数：trust_env 是 Session 的属性，
    当参数传进去直接 TypeError，而调用点那句 except 会把它吞成「端口问不到」——
    判据全瞎，表现和「浏览器没起」一模一样，比不修更坏。离线用例第 ⑬ 节用真
    requests 跑本机 HTTP 服务，就是为了钉住这一类「桩函数挡不住 API 用错」的坑。
    """
    s = requests.Session()
    s.trust_env = False
    s.proxies = {"http": None, "https": None}
    return s


def _local_get(url: str, timeout: float = 1.5):
    with _local_session() as s:
        return s.get(url, timeout=timeout)


def _local_put(url: str, timeout: float = 1.5):
    with _local_session() as s:
        return s.put(url, timeout=timeout)


def _port_chrome_major(port: int):
    """从调试端口问 Chrome 主版本（共享浏览器已在跑时最准，也不用额外起进程）

    键名大小写踩过坑：/json/version 给的是 "Browser"，而 JSON 的键**区分大小写**
    （HTTP 头才不分），旧写法取 "browser" 永远拿到空 → 这条快探路实际一直在走
    后面的「问本地 Chrome 二进制版本」那条慢路。两个键名都认，取到哪个算哪个。
    """
    try:
        data = _local_get(f"http://127.0.0.1:{port}/json/version").json() or {}
        browser = str(data.get("Browser") or data.get("browser") or "")
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
    """本机能直接用的 chromedriver，按「最可能一次建会话成功」排好序

    排序原则：与当前 Chrome 主版本精确对上的排最前；其次随包/exe 旁那份没注版本的
    （CI 打包时与 Stable 配对，Chrome 与打包通道同源时它就是配套的）；再后是大版本差
    在 DRIVER_MAJOR_SLACK 以内的按新旧排（实测差 1 个大版本可用，见那里注释）；然后是
    版本看不出来的散装驱动（PATH/手工放置，宁试勿垫底）；明知差出 SLACK 的排最后——
    版本不符 chromedriver 会直接拒绝建会话，白等一轮。
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
    for d in _driver_dirs():
        # 注版驱动（chromedriver.156.exe）与不带版本的都收，交给下面的排序按浏览器版本挑
        cands.extend(sorted(d.glob("chromedriver*"), reverse=True))
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
    if major.isdigit():
        want = int(major)
        cache_root = str(_cache_root())

        def _rank(c):
            # (档位, 版本距离, 来源) —— 稳定排序，同键保持列出次序：
            # 档位 0：大版本对得上或差在 SLACK 以内；其中随包/exe 旁那份虽然文件名
            # 没注版本，却是 CI 打包时与当时 Stable 配对下载的（实测本机 Chrome 与
            # 打包通道同源时它就是配套的），排在「精确命中」之后、其余相近版本之前；
            # 档位 1：版本号看不出来的散装驱动（PATH/手工放置），宁试勿垫底；
            # 档位 2：明知差出 SLACK 以上的，只给最后一线机会。
            m = _path_major(c)
            if not m:
                in_cache = str(c).startswith(cache_root)
                return (0, 0.5, 1 if in_cache else 0)
            d = abs(m - want)
            return (0, d, 1) if d <= DRIVER_MAJOR_SLACK else (2, d, 1)

        uniq.sort(key=_rank)
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


def _major_gap(drv_ver, chrome_ver):
    """驱动与浏览器的**主版本差**；任何一边问不到就返回 None（未知≠不符）。

    分类和「本机到底有没有配套驱动」两件事都必须用这一个算式，否则两处会分叉：
    一边判成 version 去联网，另一边认不出其实手里就有一份配套驱动。"""
    try:
        if drv_ver and chrome_ver:
            return abs(int(str(drv_ver).split(".")[0]) - int(str(chrome_ver).split(".")[0]))
    except Exception:
        pass
    return None


def has_matching_driver(drv_vers, chrome_ver):
    """手底下是否真有一份「版本差在容差内」的驱动。未知一律不算配套。"""
    return any(_major_gap(v, chrome_ver) is not None
               and _major_gap(v, chrome_ver) <= DRIVER_MAJOR_SLACK for v in drv_vers)


def classify_driver_error(msg, drv_ver="", chrome_ver="", pages=None) -> str:
    """建会话失败分两类，日志与上层重试判据都按这个分流：

    version —— 驱动与 Chrome 主版本差得够远，chromedriver 明确拒绝，只有换驱动才有用；
    connect —— 端口在但浏览器那一刻不接驱动的连接（或浏览器压根没在跑），同一驱动
               稍后重试即可（2026-11-04 08:33 企知道通道撞的就是这一类，同一份驱动
               随后 0.2 秒附加成功）。

    【判据顺序 2026-11-14 实测纠正】旧写法先看报错文案，于是「session not created:
    cannot connect to chrome at …」这一句被无条件归成 connect。但它恰恰也是**驱动太旧**
    时报的那句（本机实测：chromedriver 135 连 Chrome 157 就是这句，而 156 连 157 成功）。
    归成 connect 的后果是「原地退避重试同一个旧驱动」——重试一百次也不会好，日志却写着
    「稍后重试通常就好」，这就是 1688 恒 0 家还查不出原因的那条链。所以两个版本都问得到时
    先按主版本判，问不到（返回空串）才退回文案判。

    【判据范围 2026-11-14 第二次实测纠正】上一条写成「主版本不相等就算 version」，于是
    156 驱动连 157 浏览器（实测 0.3 秒成功）也被记成版本不符。同一轮里真正的原因是
    「调试端口上一个标签都没开」（见 ensure_attachable_pages），记错账的后果是日志
    指着一份好驱动说「换下一个」，最后整条链退给 Selenium Manager 联网 —— 而本机联网
    不通，任务就挂在那里。所以差在 DRIVER_MAJOR_SLACK 以内**不构成换驱动的理由**，
    只有差出余量才算 version。端口证据排在版本推断前面：/json/list 都问不到时，
    浏览器根本不在跑，这时候报「版本不符、换驱动」是让人往完全错的方向修。
    """
    s = (msg or "").lower()
    if "only supports chrome version" in s or "this version of chromedriver" in s:
        return "version"
    if pages is not None and pages < 0:
        return "connect"          # 调试端口 HTTP 都问不到：浏览器没在跑，换驱动无用
    if _major_gap(drv_ver, chrome_ver) is not None \
            and _major_gap(drv_ver, chrome_ver) > DRIVER_MAJOR_SLACK:
        return "version"
    if ("cannot connect to chrome" in s or "session not created" in s
            or "websocket" in s or "failed to parse" in s):
        return "connect"
    return "connect"


def _short_err(e) -> str:
    """chromedriver 的报错动辄几十行堆栈，只留第一行给人看"""
    s = str(e) or e.__class__.__name__
    # 只取首行、**不按字符数切**：这一句会被拼进「哪个驱动为什么起不来」的说明里，
    # 切在中间就长成一串半截字符。本目录是独立包（装不进 b2bcollect_build/text_shape.py），
    # 所以这一族在这里的合格写法就是「整行带出」，见判据第 30 条的第三支口径。
    return s.splitlines()[0]


# Selenium Manager 是按父进程的 PATH 找现成驱动的，而改 PATH 是**进程级**动作：
# 同一时刻只能有一路在改，所以补采三路与 1688 并行时要用这把锁排队。
_sm_lock = threading.Lock()


def stale_driver_path_dirs(want_major: str) -> list:
    """PATH 上「装着 chromedriver、但大版本和浏览器不配套」的那些目录。

    为什么专门找它们：Selenium Manager 的查找顺序是「PATH 上有没有现成驱动 → 有就用」，
    它自己都会打一句「PATH 里那份 135 可能不兼容，建议删掉再试」，**却照样拿那份去连**
    （2026-11-14 实机撞上的：135 连 Chrome 157 → session not created: cannot connect to
    chrome，1688 因此恒 0 家）。删用户机器上的文件是越界，但只在本次子进程里把这些目录
    藏起来，SM 找不到现成的就会自己去拉配套版本——这正是它设计上该走的那条路。
    """
    if not want_major:
        return []
    exe, bad, seen = _exe_name(), [], set()
    for d in (os.environ.get("PATH") or "").split(os.pathsep):
        # PATH 里同一个目录被重复挂几十上百次是常态（conda/venv 每次激活都插一遍，
        # 本机实测 /usr/local/bin 出现 500+ 次）：去重后再查版本，既不让「绕过了 N 个
        # 目录」的日志虚报成几百，也不白走几百次 is_file
        if not d or d in seen:
            continue
        seen.add(d)
        try:
            if not (Path(d) / exe).is_file():
                continue
            ver = driver_version(str(Path(d) / exe))
        except OSError:
            continue
        if ver and ver.split(".")[0] != str(want_major):
            bad.append(d)
    return bad


@contextlib.contextmanager
def sm_clean_path(ports=None, log=None):
    """临时把 PATH 里那些不配套的驱动目录摘掉，只管这一次调用，绝不落盘改用户环境。"""
    log = log or (lambda m: None)
    want = chrome_major(ports) or ""
    bad = stale_driver_path_dirs(want)
    if not bad:
        yield
        return
    with _sm_lock:
        old = os.environ.get("PATH", "")
        kept = os.pathsep.join(x for x in old.split(os.pathsep) if x and x not in bad)
        os.environ["PATH"] = kept
        try:
            log(f"   联网准备驱动时先绕过 PATH 上 {len(bad)} 个不配套的旧驱动目录"
                f"（Chrome {want}，那些目录里的驱动大版本对不上；只绕本次调用，不改你的系统设置）")
            yield
        finally:
            os.environ["PATH"] = old


def _is_attach(options) -> bool:
    """这次是「附加到已经在跑的 Chrome」还是「让驱动自己起一个浏览器」。

    为什么要分清：附加模式下 Selenium Manager 联网换来的新驱动连的还是同一个
    debuggerAddress。浏览器那一刻不接连接（端口在、进程半死、单实例锁没释放）时，
    换驱动治不了，联网等几分钟更治不了 —— 该做的是把错误尽快抛给上层，由上层去清残留
    Chrome、重启浏览器再附加。自己起浏览器的模式相反：SM 会连浏览器一起管，联网兜底
    是真兜底，不能省。"""
    try:
        if getattr(options, "debugger_address", None):
            return True
        exp = getattr(options, "experimental_options", None) or {}
        if exp.get("debuggerAddress"):
            return True
        return any(str(a).startswith("--remote-debugging-address")
                   or str(a).startswith("--remote-debugging-port")
                   for a in (getattr(options, "arguments", None) or []))
    except Exception:
        return False


def _attach_addr(options, ports=None) -> str:
    """这次要附加的调试地址（形如 127.0.0.1:9255）；不是附加模式返回空串。

    三种写法都得认：Selenium 的 options.debugger_address、experimental_options
    里的 debuggerAddress、以及命令行参数 --remote-debugging-port（1688 与企知道
    用的就是不同的两种）。都没写时按调用方给的探测端口问一遍，问得到算数。
    """
    try:
        a = str(getattr(options, "debugger_address", None) or "")
        if not a:
            exp = getattr(options, "experimental_options", None) or {}
            a = str(exp.get("debuggerAddress") or "")
        if not a:
            for arg in (getattr(options, "arguments", None) or []):
                s = str(arg)
                if s.startswith("--remote-debugging-port"):
                    p = s.split("=", 1)[1].strip() if "=" in s else ""
                    if p.isdigit():
                        a = f"127.0.0.1:{p}"
                    break
        if a:
            return a
        for p in (ports or []):
            if cdp_pages(f"127.0.0.1:{p}", timeout=0.8) >= 0:
                return f"127.0.0.1:{p}"
    except Exception:
        return ""
    return ""


def cdp_pages(addr, timeout: float = 1.5) -> int:
    """调试端口上现在开着几个页面标签；−1 = 端口问不到（浏览器没起/没开调试端口）。

    只走本机 HTTP，不联网，所以拿它当判据是零成本的。0 是个要命的数字，见
    ensure_attachable_pages 的实测记录。
    """
    if not addr:
        return -1
    try:
        data = _local_get(f"http://{addr}/json/list", timeout=timeout).json()
    except Exception:
        return -1
    if not isinstance(data, list):
        return -1
    return sum(1 for t in data if str((t or {}).get("type") or "").lower() == "page")


def ensure_attachable_pages(addr, log=None, timeout: float = 1.5) -> int:
    """附加前保证至少开着一个标签；一个都没有就补一个 about:blank。返回补完的标签数。

    【2026-11-14 实机定位的根因】同一份 chromedriver 156 附加同一个 Chrome 157：
        · /json/list 里有 1 个 page → 0.3 秒建会话成功；
        · page 数 = 0（用户手动关掉了那个 Chrome 窗口的最后一个标签，或上一轮收尾
          把标签全 close 掉）→ 报「session not created: cannot connect to chrome」，
          另一路干脆挂住 45 秒不返回。
    报错文案把矛头指向驱动版本，实际浏览器活得好好的 —— 差别只在有没有标签。
    补一个空白标签是本机操作：不动用户已开的标签、不联网、失败也只是回到原样。
    """
    log = log or (lambda m: None)
    n = cdp_pages(addr, timeout)
    if n != 0:
        return n
    log("   共享 Chrome 一个标签都没开（0 page target）：附加会失败或直接挂住，"
        "先补开一个空白标签…")
    try:
        # 新版 Chrome 只认 PUT（GET 会被回一句 "Using unsafe HTTP verb GET"）
        _local_put(f"http://{addr}/json/new?url=about:blank", timeout)
    except Exception as e:
        log(f"   补开标签没成功（{_short_err(e)}），继续按原样试一次")
        return 0
    for _ in range(3):
        time.sleep(0.3)
        n = cdp_pages(addr, timeout)
        if n > 0:
            log(f"   已补开标签（现在 {n} 个），继续附加")
            return n
    return 0


def sm_source_reachable(timeout: float = 4.0) -> bool:
    """驱动下载源此刻够不够得着（内网/被墙/拔线时这里就该说「够不着」）。

    为什么要单独问一次：Selenium Manager 的联网过程是**阻塞**的，本机 googlechromelabs
    不可达时它不是报错，而是一直等到自己的超时 —— 上层看到的就是「日志停在 21:17 之后
    再不动，任务挂着 6 分钟还没结束」。先花 4 秒问一句，够不着就不去，换成一句人话。
    """
    for url in ("https://googlechromelabs.github.io/chrome-for-testing/",
                "https://storage.googleapis.com/chrome-for-testing-public/"):
        try:
            requests.head(url, timeout=timeout)
            return True
        except Exception:
            continue
    return False


# ── 子进程回收：quit() 只删会话，chromedriver 得我们自己收 ──────────────────
# Selenium 4.51 的 `driver.quit()` 里已经没有 `service.stop()` 了：整包里搜下来，
# 只有「建会话失败」那一条异常路径会 stop（remote/webdriver.py new_session 的 except
# 分支），正常收工那一路从头到尾没人管子进程；`stop_client()` 是个空函数。
# 唯一还会替我们收进程的是 `Service.__del__` —— 它要等引用断了、GC 真跑一次才算，
# 而采集器把 driver 挂在 self 上、会话自愈又是「换新一个、旧的丢掉」，什么时候断引用
# 没人能承诺；进程被强杀/Ctrl-C 时更是永远不会跑（2026-11-15 实机取证：本机挂着两个
# 父进程已经是 launchd 的孤儿 chromedriver，一个空跑了 5 小时）。
# 单次代价看着不大（几十 MB + 一个端口），但 Windows 上它还攥着 chromedriver.exe 的
# 文件句柄，且「会话自愈/换驱动重试」每发生一次就多一个，跑一下午就是几十个。
# 口径统一在这里：谁建会话谁登记（create），谁收会话谁回收（destroy），
# 进程退出时再兜一层（atexit），三条路都指向同一个 stop_service。
# 注意 destroy()/stop_service() 收的都只是**我们自己拉起的 chromedriver**，
# 与被附加的 Chrome 无关 —— 共享浏览器的会话该不该 quit 由调用方按标签策略决定。
_started_services = []
_services_lock = threading.Lock()


def _track_service(service) -> None:
    if service is None:
        return
    with _services_lock:
        _started_services.append(service)


def _untrack_service(service) -> None:
    if service is None:
        return
    with _services_lock:
        for i, s in enumerate(_started_services):
            if s is service:
                del _started_services[i]
                return


def stop_service(service) -> bool:
    """收掉一个 chromedriver 子进程。重复调用只生效一次；停不掉也不抛
    （收工路径上绝不能因为回收失败把整轮采集的收尾带崩）。返回是否真收了一次。"""
    if service is None or getattr(service, "_cd_reaped", False):
        _untrack_service(service)
        return False
    try:
        service._cd_reaped = True            # 先立牌：下面无论走到哪一步都不再收第二遍
    except Exception:
        pass                                 # 立不上牌也不能挡回收（顶多被多收一次）
    try:
        service.stop()
        return True
    except Exception:
        return False
    finally:
        _untrack_service(service)


def _arm_quit(driver, service) -> None:
    """把这个实例的 quit() 绑成「结束会话 + 收子进程」。
    为什么绑在实例上而不是要求每个调用方改用 destroy()：仓库里有十来个 `driver.quit()`
    （含异常自愈路径），漏一个就漏一份进程；绑在这里，老写法自动就是对的。"""
    raw_quit = getattr(driver, "quit", None)
    if not callable(raw_quit):
        # 形状不对（不是我们认识的 WebDriver）就别绑：绑这一步绝不能反过来
        # 把「会话已经建好了」的成功流程带崩。登记照旧，收尾由 destroy()/退出兜底负责。
        return

    def quit():                                  # noqa: A001 - 与 Selenium 同名，就是要在原地补收
        try:
            raw_quit()
        finally:
            stop_service(service)

    driver.quit = quit


def destroy(driver, log=None) -> None:
    """结束会话并把 chromedriver 子进程一起收掉；新写的收尾请用它，别再用裸 quit()。"""
    if driver is None:
        return
    log = log or (lambda m: None)
    service = getattr(driver, "service", None)
    t0 = time.monotonic()
    try:
        driver.quit()                            # 被 _arm_quit 绑过 → 顺带收进程（幂等）
    except Exception as e:
        log(f"   结束浏览器会话时报错（不影响收工，子进程照常回收）：{_short_err(e)}")
    stop_service(service)
    cost = time.monotonic() - t0
    if cost > 2:
        # 正常一秒内就完事；慢说明 chromedriver 对 /shutdown 没立刻退，值得在日志里留一行
        log(f"   chromedriver 子进程回收用了 {cost:.1f} 秒（会话结束 + 进程退出）")


def release(driver, log=None) -> None:
    """只收自己这份 chromedriver 子进程，不发 quit。
    给「共享浏览器、会话归别人用/标签已收回」这类收尾用：那里最忌讳的就是
    替整个浏览器发 quit（见 qizhidao_crawler.close_browser 的分叉），但子进程
    照样该收 —— 否则丢掉的 driver 只能等 GC 替我们收拾（见上面那段注释）。"""
    if driver is None:
        return
    stop_service(getattr(driver, "service", None))


def reap_started_services() -> int:
    """把本进程拉起、还没收掉的 chromedriver 全部收掉，返回收掉几个。
    给 atexit 用，也给「停止任务」这类收尾当兜底：中途被 Ctrl-C、窗口被直接关掉时，
    close_browser 来不及跑，这一层保证不把孤儿进程留给下一轮。"""
    with _services_lock:
        pending = list(_started_services)
        _started_services.clear()
    n = 0
    for s in pending:
        if stop_service(s):
            n += 1
    return n


atexit.register(reap_started_services)


def create(options, log=None, ports=None, retries: int = 3):
    """用本地驱动建会话；本地全落空才退 Selenium Manager。返回 WebDriver。

    每一次尝试都打耗时，方便“打开网页很慢”这类问题事后归因到具体步骤。
    """
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service

    log = log or (lambda m: None)
    attach = _is_attach(options)
    # 附加模式的第一个动作是看浏览器此刻有没有标签：没有就先补一个，
    # 否则接下来每一次建会话都会撞那句误导人的 "cannot connect to chrome"
    pages = None
    if attach:
        addr = _attach_addr(options, ports)
        pages = cdp_pages(addr)
        if pages == 0:
            pages = ensure_attachable_pages(addr, log)
    tried = []
    kinds = []
    drv_vers = []          # 每次尝试所用驱动的版本，配合 kinds 才能问出「有没有配套的那一份」
    paths = candidates(ports)[:retries]

    def _attempt(p):
        """用一个本地驱动建会话；成功返回 driver，失败返回异常消息（并记归因）。"""
        service = None
        t0 = time.monotonic()
        try:
            service = Service(executable_path=p)
            driver = webdriver.Chrome(service=service, options=options)
            _track_service(service)
            _arm_quit(driver, service)
            log(f"🔧 已用本地 ChromeDriver（{Path(p).parent.name[:16]}）"
                f"，耗时 {time.monotonic() - t0:.1f} 秒")
            return driver
        except Exception as e:
            if service is not None:
                stop_service(service)          # 建会话失败也要收子进程（幂等，失败不抛）
            msg = _short_err(e)
            dver = driver_version(p)
            cver = chrome_major(ports)
            kind = classify_driver_error(msg, dver, cver, pages)
            tried.append(msg)
            kinds.append(kind)
            drv_vers.append(dver)
            where = f"{Path(p).parent.name}/{Path(p).name}"
            if kind == "version":
                log(f"  本地驱动版本不符（{where} {dver or '?'}，Chrome {cver or '?'}）："
                    f"{msg.splitlines()[0]}，换下一个…")
            else:
                log(f"  本地驱动连不上浏览器（{where} {dver or '?'}：端口在但这一刻没接受"
                    f"驱动的连接，稍后重试通常就好）：{msg.splitlines()[0]}，换下一个…")
            return None

    for p in paths:
        driver = _attempt(p)
        if driver is not None:
            return driver

    # 浏览器压根问不到（端口没起）：附加不必再等，直接报给上层去重启 Chrome
    if attach and pages is not None and pages < 0:
        raise RuntimeError("附加到已运行的 Chrome 失败：调试端口没有响应"
                           "（浏览器可能已退出或不是带调试端口启动的）")

    # 本地全败且全是「浏览器不接连接」：联网换驱动治不了这个，先原地退避再试一遍本地
    # 旧判据要求「全部失败都是 connect」才原地退避。可同一轮里常常还躺着一份早就
    # 过期的旧驱动（本机实测 135 连 156 报的正是 cannot connect 那句），它把 kinds
    # 掺进一个 version，于是整轮被判去联网 —— 而真正配套的那一份只是被浏览器临时
    # 拒绝，退避重试 0.3 秒就好。联网下载源不可达时任务就永久挂在那儿。
    connect_blocked = ("connect" in kinds
                       and has_matching_driver(drv_vers, chrome_major(ports)))
    if paths and kinds and (all(k == "connect" for k in kinds) or connect_blocked):
        for wait in (3, 6):
            log(f"   本地驱动都是「浏览器没接连接」这一类失败，{wait} 秒后原地重试一遍"
                f"（不联网下载）…")
            time.sleep(wait)
            if attach:
                # 上一轮可能是「浏览器还没起来/没标签」，重试前按同样的顺序再核一遍
                addr = _attach_addr(options, ports)
                if cdp_pages(addr) == 0:
                    ensure_attachable_pages(addr, log)
            for p in paths:
                driver = _attempt(p)
                if driver is not None:
                    return driver
        if attach:
            # 附加模式 + 全是连接类失败：交给上层走「清残留 Chrome → 重启 → 再附加」。
            # 这里去联网只会让那次重试凭空慢几分钟，日志还看着像卡死。
            head = tried[0] if tried else "无报错详情"
            raise RuntimeError("附加到已运行的 Chrome 失败：浏览器端口在但没接受驱动的连接"
                               f"（多为浏览器正忙/刚启动）。最近一次报错：{head}")
    # 联网兜底前先问一句下载源够不够得着：够不着就别去 —— 那里的等待是阻塞的，
    # 本机实测「不可达」不是报错而是一直挂着，界面上看就是任务卡死（2026-11-14 企知道）
    if paths and not sm_source_reachable():
        cver = chrome_major(ports) or "?"
        mine = ", ".join(x for x in (driver_version(p) for p in paths) if x) or "?"
        raise RuntimeError(
            "创建浏览器会话失败：本机 ChromeDriver 都不配套，而驱动下载源此刻连不上"
            f"（本机 Chrome {cver}，能用到的驱动 {mine}）。"
            f"两条路：把 Chrome 升到最新；或放一份配套驱动进 {_cache_root()} "
            "（下次开机就直接可用，不用联网）")
    if paths:
        log("⚠ 本机可用的 ChromeDriver 都没能建会话，回退 Selenium Manager（可能要联网查版本，会慢）")
    else:
        log("⚠ 本机没有 ChromeDriver，交给 Selenium Manager 现场获取"
            "（首次要联网下载配套驱动，本机实测约 6 分钟；下完进本地缓存，以后每次都是秒级）")
    t0 = time.monotonic()
    try:
        # 联网这一路必须绕过 PATH 上那些不配套的旧驱动，否则 SM 会拿它们去连浏览器，
        # 报的还是那句最容易被误读的「cannot connect to chrome」（见 stale_driver_path_dirs）
        with sm_clean_path(ports, log):
            driver = webdriver.Chrome(options=options)
        # 联网这一路的 Service 是 Selenium 自己建的，挂在 driver.service 上：
        # 不登记不绑回收，这份进程照样会漏（口径与本地驱动那几条完全一致）
        _track_service(getattr(driver, "service", None))
        _arm_quit(driver, getattr(driver, "service", None))
        log(f"🔧 Selenium Manager 已备好驱动，耗时 {time.monotonic() - t0:.1f} 秒")
        return driver
    except Exception as e:
        if tried:
            log(f"   （本地驱动失败原因：{tried[0]}）")
        tail = ""
        if kinds and (all(k == "connect" for k in kinds) or connect_blocked):
            tail = "（浏览器端口在但没接受驱动的连接，多为浏览器正忙/刚启动，稍后重试即可）"
        elif "version" in kinds:
            tail = (f"（驱动与 Chrome 版本不匹配：本机 Chrome {chrome_major(ports) or '?'}，"
                    f"能找到的驱动是 {', '.join(x for x in (driver_version(p) for p in paths) if x) or '?'}；"
                    "把 Chrome 升到最新，或放一份配套驱动进 "
                    f"{_cache_root()} 就立刻可用）")
        raise RuntimeError(f"创建浏览器会话失败：{_short_err(e)}{tail}") from e
