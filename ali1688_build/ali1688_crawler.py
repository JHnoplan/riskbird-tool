"""
ali1688_crawler.py - 1688 企业数据自动采集核心

链路（实机探测确认）：
  1) 列表页  https://www.1688.com/zw/page.html?hpageId=old-sem-pc-list&keywords=<kw>&beginPage=<n>
     页内内嵌 `var pageConfig = {...}`，其 listOffer[] 每条含 company / offerId /
     odUrl(干净详情URL) / loginId / memberId / price / subject，分页上限约 50 页。
  2) 详情页  odUrl（https://detail.1688.com/offer/<offerId>.html）
     页内 JSON 含 winportUrl(店铺域名) 与 sellerWinportUrlMap.contactinfoUrl。
  3) 联系方式页  contactinfoUrl（https://<shop>.1688.com/page/contactinfo.html）
     电话 / 手机 / 传真 / 地址 为纯文本，按标签提取。

按 memberId 去重后再进详情+联系方式，大幅降低请求量与封禁风险。

浏览器：独立调试端口 + 独立持久化配置目录（不抢其它工具的单实例锁），
登录一次后跨启动复用；被风控拦截时原地等人工过验证后自动继续。
"""

import os
import re
import sys
import time
import json
import random
import shutil
import threading
import subprocess
import urllib.request
from pathlib import Path
from urllib.parse import quote

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

# 复用企业数据采集工具成熟的《客户库数据导入模板》清洗模块（同目录副本）
try:
    import customer_library_cleaner as _cleaner
except ImportError:  # 兼容从上级目录运行
    sys.path.insert(0, str(Path(__file__).parent))
    import customer_library_cleaner as _cleaner

try:
    from PyQt5.QtCore import QObject, pyqtSignal
    HAS_PYQT = True
except ImportError:
    HAS_PYQT = False

if HAS_PYQT:
    class CrawlerSignals(QObject):
        log_signal = pyqtSignal(str)
        progress_signal = pyqtSignal(int, int)          # 当前, 总数
        crawl_finished_signal = pyqtSignal()
        blocked_signal = pyqtSignal(str)                # 需人工过验证
        block_cleared_signal = pyqtSignal(str)          # 已过验证继续
        login_required_signal = pyqtSignal(str)         # 需登录
        stats_signal = pyqtSignal(str)                  # 阶段/统计信息
else:
    class CrawlerSignals:
        pass


# ── 1688 风控 / 拦截判据（只用 URL 与标题，绝不整页正文关键词匹配）──────────
BLOCK_URL_MARKERS = (
    "x5secdata", "_____tmd_____", "/punish", "login.alibaba.com",
    "login.1688.com", "x5sec.", "/_____tmd____", "nocaptcha", "cap", "verify.1688",
)
BLOCK_TITLE_MARKERS = ("验证码", "安全验证", "滑块验证", "身份核实", "环境异常",
                       "访问验证", "请验证", "滑动验证")
# 正常列表/详情/联系页标题里含这些词，绝不能当拦截
SAFE_TITLE_HINTS = ("批发", "阿里巴巴", "联系方式页", "供应商", "制造商", "1688")

# 未登录特征：联系方式页要登录才完整展示，未登录会跳登录或空值
LOGIN_URL_MARKERS = ("login.1688.com", "login.alibaba.com", "/member2/login")

SEARCH_URL = ("https://www.1688.com/zw/page.html?hpageId=old-sem-pc-list"
              "&keywords={kw}&beginPage={page}&n=y")

# 供应商(公司)搜索渠道：每页约 20 家不同公司，产出远高于商品渠道
COMPANY_SEARCH_URL = ("https://s.1688.com/company/company_search.htm"
                      "&keywords={kw}&beginPage={page}")

# 主搜渠道（1688 站内正规商品搜索页）：每页约 60 条 offer、约 50 家不同公司，
# 实测同一垂直词池深 100+ 家（SEM 落地页仅 20 余条/页且大词也只有约 70 家池子）。
# 公司维度数据藏在 offer 卡片埋点属性 member_id@b2b-xxx^item_id=yyy 里。
MAIN_SEARCH_URL = ("https://s.1688.com/selloffer/offer_search.htm"
                   "&keywords={kw}&beginPage={page}")

PHONE_RE = re.compile(r'(?:86[\s-]?)?(?:0\d{2,3}[\s-]?\d{7,8}|1[3-9]\d{9})')
EMAIL_RE = re.compile(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}')
# 主搜页 offer 卡片埋点属性里的 (memberId, offerId) 对（2026-10-27 实机确认）
MAIN_MEMBER_ITEM_RE = re.compile(r'member_id@(b2b-[0-9a-z]{6,24})\^item_id@(\d+)')
# 主搜结果卡片 <a class="offerCard.." href="../offer/{id}.html">..</a>：列表页即可零成本
# 拿到 offerId + 公司名(卡片 title 属性) + 商品标题(class="titleText--hash" 内文)，
# 用于「进详情前」的早过滤（用平台自身的关键词预测类目+关键词字面做相关性打分，省大量详情导航）。
OFFER_CARD_RE = re.compile(
    r'<a class="offerCard[^"]*"[^>]*?href="https?://detail\.1688\.com/offer/(\d+)\.html[^"]*"[^>]*>(.*?)</a>',
    re.S)
CARD_TITLE_TEXT_RE = re.compile(r'class="titleText--[^"]*"[^>]*>(.*?)</div>', re.S)
CARD_COMPANY_SUFFIX_RE = re.compile(r"(公司|厂|商行|商店|中心|经营部|经销部|部|店|馆|集团|合作社|工作室)$")
# 主搜卡片埋点里的叶子类目：leafCategory@12345^。注意实测全页 60 条同值，
# 是「查询级预测类目」而非 offer 真实类目，逐条过滤不可用它——真实类目在详情页：
DETAIL_LEAF_ID_RE = re.compile(r'"leafCategoryId"\s*:\s*(\d{3,12})')
DETAIL_LEAF_NAME_RE = re.compile(r'"leafCategoryName"\s*:\s*"([^"]{1,20})"')
DETAIL_SHOP_CAT_RE = re.compile(r'shop-category-name">([^<]{2,40})<')   # 主营：xx / xx类目
DETAIL_TITLE_RE = re.compile(r'<title>([^<]{4,80}?)\s*[-–—]?\s*(?:阿里巴巴|1688)?\s*</title>')


class Ali1688Crawler:
    PLATFORM_NAME = "1688"
    BASE_URL = "https://www.1688.com/"

    def __init__(self, debug_port=9244):
        self.debug_port = debug_port
        self.driver = None
        self._browser_lock = threading.Lock()
        self._attach_only = False

        self.signals = CrawlerSignals() if HAS_PYQT else None
        self.results = []
        self.results_lock = threading.RLock()

        self._crawling = False
        self._stop_requested = False
        self._pause_requested = False

        # 采集速度：基础间隔（秒），会被随机抖动放大；被拦截时自动上调
        self.delay_between = 3.0
        self.jitter = 1.5
        self._current_delay = self.delay_between
        # 熔断（大厂 anti-abuse 的 circuit-breaker 思想）：短窗口内被反复拦截说明已进入
        # 平台限流区，继续逐页死磕只会把软限流熬成硬封号；此时强制长冷却、必要时收本轮。
        self._block_times = []       # 近期被拦时间戳
        self._block_window = 900     # 统计窗口（秒）
        self._max_blocks = 5         # 窗口内达此次数 → 判持续限流，优雅结束本轮
        self.max_pages = 10          # 每个关键词最多翻几页（1688 上限约 50）
        self.main_channel = True     # 主搜渠道（selloffer/offer_search，每页约50家，池子最深）
        self.company_channel = True  # 同时采集供应商(公司)渠道（实测产出为商品渠道数倍）
        self.relevance_guard = True  # 主搜渠道类目相关性过滤：类目不符的公司不进联系方式页
        self.max_records = 0         # 采集到 N 条自动收尾（0=不限，低频实测/限量用）
        self._filtered_count = 0     # 本轮类目过滤计数
        self.debug_collect = False   # 开发调参：记录类目过滤判定输入（不影响生产行为）
        self._guard_debug = []
        self.expand_related = False  # 自动扩展相关关键词（1688 relativeWords 多为全站热搜，保守过滤后追加）
        # 增量种子库（大厂爬虫的 frontier 去重思想）：跨运行记住「已处理过」的公司，
        # 再跑同一批关键词时命中即跳过，绝不重复戳同一家详情页/联系页。既省请求（防封），
        # 又能把每次新出现、之前没采到的公司增量补齐——重复挂过夜反而总数据量最大。
        self.incremental = True
        self._seen_store = {}        # {key: {"at": ts, "st": 状态}}，持久化到 seen_store.json

        self._data_dir = self._stable_data_dir()
        self._seen_store_path = self._data_dir / "seen_store.json"
        self._profile_dir = self._profile_path()
        self._seen_members = set()   # 本轮已处理公司（memberId）去重
        self._early_skipped = 0      # 本轮列表页早过滤计数（未进详情即判无关）
        self._guard_kw = ""          # 类目过滤：当前关键词
        self._guard_pred = None      # 类目过滤：关键词级 PRECISE 预测类目集（id+名）

    # ── 日志 / 结果 ────────────────────────────────────────────
    def _log(self, msg):
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        if self.signals:
            try:
                self.signals.log_signal.emit(line)
            except Exception:
                pass

    def _emit(self, name, *args):
        if self.signals:
            try:
                getattr(self.signals, name).emit(*args)
            except Exception:
                pass

    def snapshot_results(self):
        with self.results_lock:
            return [dict(r) for r in self.results]

    def _add_result(self, rec):
        with self.results_lock:
            self.results.append(rec)
        self._commit_to_store(rec)     # 到终态的公司写进种子库（临时失败不写，下轮重试）
        # 护栏条数：达到即自动收尾（低频实测/限量采集用）
        if self.max_records and len(self.results) >= self.max_records:
            self._log(f"🛡 已达限量 {self.max_records} 条，自动停止")
            self._stop_requested = True

    # ── 增量种子库（跨运行去重）───────────────────────────────
    # 只把「已到终态」的公司永久记住：成功/无电话/无联系方式（卖家确为暂无）、
    # 以及类目不符(已过滤)。凡带「拦截/未解除/导航失败/未登录/待重试」的临时失败
    # 一律不记，下一轮会重新尝试——这样既不重复戳已处理过的家（省请求防封），
    # 又不会把「这次没采成」的公司永久漏掉（保数据最全）。
    TRANSIENT_MARKS = ("拦截", "未解除", "导航失败", "未登录", "待重试")

    def _is_terminal(self, rec):
        st = rec.get("状态", "") or ""
        return st != "" and not any(m in st for m in self.TRANSIENT_MARKS)

    def _rec_seen_keys(self, rec):
        keys = []
        mid = str(rec.get("memberId") or "")
        if mid:
            keys.append(mid if mid.startswith("b2b-") else "shop:" + mid)
        name = self._norm_name(rec.get("公司名称") or "")
        if name:
            keys.append(name)
        return keys

    def _load_seen_store(self):
        try:
            with open(self._seen_store_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._seen_store = {str(k): v for k, v in data.items()}
        except Exception:
            self._seen_store = {}
        return len(self._seen_store)

    def _save_seen_store(self):
        try:
            tmp = self._seen_store_path.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._seen_store, f, ensure_ascii=False)
            tmp.replace(self._seen_store_path)
            return True
        except Exception as e:
            self._log(f"⚠ 种子库保存失败：{str(e).splitlines()[0]}")
            return False

    def _commit_to_store(self, rec):
        if not self.incremental or not self._is_terminal(rec):
            return
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        st = rec.get("状态", "")
        if st == "类目不符(已过滤)":
            # 类目不符是「关键词相关」的判定：同一家对 A 词不相关、对 B 词可能正是目标，
            # 故只按「关键词#公司」作用域记录，换关键词重跑时仍会重新评估，绝不全局误跳。
            mid = str(rec.get("memberId") or "")
            if mid:
                self._seen_store[f"{rec.get('关键词','')}#{mid}"] = {"at": ts, "st": st}
            return
        for k in self._rec_seen_keys(rec):
            self._seen_store[k] = {"at": ts, "st": st}

    def clear_seen_store(self):
        """清空跨运行种子库（用户想对同一批词从头重采时用）。"""
        self._seen_store = {}
        try:
            if self._seen_store_path.exists():
                self._seen_store_path.unlink()
        except Exception:
            pass
        return True

    # ── Chrome 生命周期 ────────────────────────────────────────
    @staticmethod
    def _stable_data_dir():
        """种子库/数据目录必须跨运行持久，且要可写。
        PyInstaller onefile 下 __file__ 指向每次启动都变、退出即删的 _MEIxxxx 临时目录，
        用它当数据目录会让 seen_store.json 既写不进也活不到下一轮（跨运行去重彻底失效）。
        故：打包运行时用「exe 同级目录」，开发时用模块目录；都不可写再回退用户主目录。"""
        if getattr(sys, "frozen", False):
            primary = Path(sys.executable).resolve().parent / "ali1688_data"
        else:
            primary = Path(__file__).resolve().parent / "ali1688_data"
        for d in (primary, Path.home() / ".ali1688" / "ali1688_data"):
            try:
                d.mkdir(parents=True, exist_ok=True)
                probe = d / ".write_test"
                probe.write_text("1", encoding="utf-8")
                probe.unlink()
                return d
            except Exception:
                continue
        return primary

    def _profile_path(self):
        if sys.platform == "win32":
            return Path(os.environ.get("TEMP", r"C:\temp")) / "chrome_1688_profile"
        return Path("/tmp/chrome_1688_profile")

    def _find_chrome(self):
        candidates = {
            "darwin": [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            ],
            "win32": [
                os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
                os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
                os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
            ],
        }.get(sys.platform, [])
        for c in candidates:
            c = os.path.expanduser(c)
            if os.path.exists(c):
                return c
        return None

    def _debug_alive(self):
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.debug_port}/json/version", timeout=1.5) as r:
                return bool(json.loads(r.read().decode("utf-8", "ignore")).get("Browser"))
        except Exception:
            return False

    def _driver_candidates(self):
        """列出所有「本地已有」的 chromedriver 候选（按默认优先级排序）。

        exe 里若只会命中一个固定版本驱动，一旦用户 Chrome 比它旧一个大版本
        （Chrome for Testing 打包时是 156、用户自动更新还没到），156 驱动连 155
        浏览器报的就是 'cannot connect to chrome'——重试/重启全都无效。
        所以候选要全列出来，由 _driver_path 按浏览器实际版本挑配套的那一个。"""
        exe_name = "chromedriver.exe" if sys.platform == "win32" else "chromedriver"
        cands = []
        if getattr(sys, "frozen", False):
            meipass = getattr(sys, "_MEIPASS", "")
            if meipass:
                cands.append(Path(meipass) / exe_name)
                # 新版打包：drivers/ 内置最近三个大版本，文件名注版 chromedriver.<N>.exe
                d_dir = Path(meipass) / "drivers"
                if d_dir.is_dir():
                    cands += sorted(d_dir.glob("chromedriver*"), reverse=True)
            exe_dir = Path(sys.executable).resolve().parent
            cands += sorted(exe_dir.glob("chromedriver*"), reverse=True)
        # Selenium Manager 缓存：~/.cache/selenium/chromedriver/<平台>/<版本>/chromedriver[.exe]
        cache_root = Path(os.environ.get("USERPROFILE") or str(Path.home())) \
            / ".cache" / "selenium" / "chromedriver"
        if cache_root.is_dir():
            def _ver_key(p):
                nums = re.findall(r"\d+", p.parent.name)
                return tuple(int(x) for x in nums[:4]) if nums else ()
            cands += sorted(cache_root.glob("**/" + exe_name), key=_ver_key, reverse=True)
        # 开发环境：与本仓库并列的风鸟驱动目录
        dev = Path(__file__).resolve().parent.parent / "riskbird_build"
        dev_sub = "chromedriver-win64/chromedriver.exe" if sys.platform == "win32" \
            else "chromedriver-mac-x64/chromedriver"
        cands.append(dev / dev_sub)
        out = []
        for f in cands:
            try:
                if f.is_file() and str(f) not in out:
                    out.append(str(f))
            except OSError:
                continue
        return out

    @staticmethod
    def _cand_major(path):
        """从路径推断驱动大版本：chromedriver.155.exe 文件名注版，或 SM 缓存的
        <版本目录>/chromedriver.exe；推断不出返回 0。"""
        name = Path(path).name
        m = re.match(r"chromedriver\.(\d+)", name)
        if m:
            return int(m.group(1))
        m = re.match(r"(\d+)\.", Path(path).parent.name)
        return int(m.group(1)) if m else 0

    def _driver_major(self, path):
        """跑 `chromedriver --version` 拿真实大版本（推断不出时的兜底，秒级本地调用）。"""
        try:
            out = subprocess.run([path, "--version"],
                                 capture_output=True, text=True, timeout=5).stdout
            m = re.search(r"ChromeDriver (\d+)", out or "")
            return int(m.group(1)) if m else 0
        except Exception:
            return 0

    def _browser_major(self):
        """调试端口 /json/version 的 Browser 字段（Chrome/155.0.x）取大版本。"""
        m = re.search(r"/(\d+)", self._devtools_browser())
        return int(m.group(1)) if m else 0

    def _driver_path(self, want_major=0):
        """挑一个能用的本地 chromedriver：优先与浏览器大版本(want_major)配套的；
        没有匹配项才退回默认优先级第一个（_attach 会据版本差决定是否在线兜底）。
        全部落空返回 None，由 _attach 回退 Selenium Manager（至多慢一次）。"""
        cands = self._driver_candidates()
        if not cands:
            return None
        if want_major:
            for f in cands:
                if self._cand_major(f) == want_major:
                    return f
            for f in cands:                       # 无名驱动实跑 --version 比对
                if self._driver_major(f) == want_major:
                    return f
        return cands[0]

    def _debug_pages(self):
        """列出调试端口上的 page 标签；查询失败返回 None，空列表表示「0 标签」。"""
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.debug_port}/json", timeout=1.5) as r:
                data = json.loads(r.read().decode("utf-8", "ignore"))
            return [t for t in data if isinstance(t, dict) and t.get("type") == "page"]
        except Exception:
            return None

    def _ensure_debug_tab(self):
        """附加前保证调试端口上至少有一个标签页。
        开着但 0 标签的 Chrome 会让 Selenium 附加直接报 'unable to discover open pages'，
        此时没有可接管的页面，通过 CDP /json/new 开一个空白页兜底。"""
        pages = self._debug_pages()
        if pages is None or pages:
            return
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{self.debug_port}/json/new?about:blank", method="PUT")
            urllib.request.urlopen(req, timeout=3).read()
            time.sleep(0.8)
        except Exception:
            pass

    def _wait_debug_ready(self, tries=20):
        for _ in range(tries):
            time.sleep(1)
            if self._debug_alive():
                return True
        return False

    def _kill_stale_profile_chrome(self):
        """关掉仍占用本工具独立配置目录、却没暴露调试端口的残留 Chrome 进程。
        Chrome 按 user-data-dir 单实例：只要旧实例还锁着配置目录，新起的带端口进程
        只会把请求转交给旧实例然后自己退出，调试端口永远起不来。
        Windows 也须能清（PowerShell 按命令行匹配本工具配置目录），否则重启重试全落空。"""
        try:
            if sys.platform == "win32":
                ps = ("Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                      "Where-Object { $_.CommandLine -like '*chrome_1688_profile*' } | "
                      "Select-Object -ExpandProperty ProcessId")
                out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                                     capture_output=True, text=True, timeout=25).stdout
            else:
                out = subprocess.run(["pgrep", "-f", str(self._profile_dir)],
                                     capture_output=True, text=True, timeout=5).stdout
            pids = [int(x) for x in out.split() if x.strip().isdigit()]
        except Exception:
            return
        if not pids:
            return
        self._log(f"清理 {len(pids)} 个占用配置目录的残留 Chrome 进程…")
        for pid in pids:
            try:
                if sys.platform == "win32":
                    # 不带 /F：优雅退出，释放单实例锁（强杀会留脏 session 文件）
                    subprocess.run(["taskkill", "/PID", str(pid)],
                                   capture_output=True, timeout=10)
                else:
                    os.kill(pid, 15)          # SIGTERM：优雅退出，释放单实例锁
            except Exception:
                pass
        time.sleep(1.8)

    # Selenium Manager 联网也连同一个调试端口，救不了「连不上 Chrome」类失败；
    # 只有「驱动与浏览器版本不匹配」才值得让它在线拉配套驱动。
    DRIVER_VERSION_MISMATCH_MARKS = ("only supports chrome version",
                                     "version mismatch",
                                     "unable to match browser version")

    def _devtools_browser(self):
        """读调试端口 /json/version 的 Browser 字段（如 Chrome/142.0.x），用于诊断日志。"""
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.debug_port}/json/version", timeout=2) as r:
                return json.loads(r.read().decode("utf-8", "ignore")).get("Browser", "")
        except Exception:
            return ""

    def _attach(self):
        opts = Options()
        opts.debugger_address = f"127.0.0.1:{self.debug_port}"
        browser = self._devtools_browser()
        want = self._browser_major()
        drv = self._driver_path(want)
        if drv is None:
            self._log("未检测到本地 chromedriver，改由 Selenium Manager 在线准备驱动"
                      "（首次可能较慢，请耐心等待）…")
            return webdriver.Chrome(options=opts)
        # 本地找不到与浏览器大版本配套的驱动（如驱动 156、浏览器 155）：这种失配
        # 报的是 'cannot connect to chrome'，重试/重启 Chrome 都无解，直接交
        # Selenium Manager 在线拉配套版本；同时提示用户升级 Chrome 这条更快的路。
        d_major = self._cand_major(drv) or self._driver_major(drv)
        if want and d_major and d_major != want:
            self._log(f"⚠ 本地驱动 {d_major} 与你的 Chrome {want} 不匹配，"
                      f"Selenium Manager 在线获取配套驱动（可能较慢；把 Chrome 升级到"
                      f"最新——设置→关于 Google Chrome→检查更新——是更快的解法）…")
            return webdriver.Chrome(options=opts)
        last_exc = None
        for i in range(3):
            try:
                return webdriver.Chrome(service=Service(executable_path=drv),
                                        options=opts)
            except Exception as e:
                last_exc = e
                msg = str(e).splitlines()[0][:140] if str(e) else e.__class__.__name__
                low = msg.lower()
                if any(k in low for k in self.DRIVER_VERSION_MISMATCH_MARKS):
                    self._log(f"⚠ 内置驱动与你的 Chrome（{browser or '版本未知'}）不匹配，"
                              f"改由 Selenium Manager 在线获取配套驱动（可能较慢，请等待）…")
                    return webdriver.Chrome(options=opts)
                time.sleep(1.5)          # 启动竞态：等 Chrome 完全就绪后重试
                browser = self._devtools_browser() or browser
        # 反复连不上：多半 Chrome 半死或残留实例没起端口，联网回退同样连不上还易卡死。
        # 抛出交给 start_browser：清残留进程 → 重启 Chrome → 再附加。
        self._log(f"⚠ 连续 3 次附加 Chrome 失败（端口 {self.debug_port}，"
                  f"{browser or '无响应'}）：{last_exc.__class__.__name__}，"
                  f"将清理残留 Chrome 后重启重试…")
        raise last_exc

    def _chrome_args(self, headed=True):
        return [
            self._find_chrome(),
            f"--remote-debugging-port={self.debug_port}",
            "--remote-allow-origins=*",
            f"--user-data-dir={self._profile_dir}",
            "--profile-directory=Default",
            "--no-first-run", "--no-default-browser-check",
            "--disable-blink-features=AutomationControlled",
            "--disable-session-crashed-bubble", "--hide-crash-restore-bubble",
            "--window-size=1440,960",
        ] + ([] if headed else ["--headless=new"])

    def start_browser(self, headed=True):
        """确保有一个带调试端口、且至少 1 个标签页的独立 Chrome，并用 Selenium 附加。幂等。"""
        with self._browser_lock:
            if self.driver is not None and self._is_session_alive():
                return True

            if not self._find_chrome():
                self._log("❌ 未找到 Google Chrome，请先安装 Chrome")
                return False
            self._profile_dir.mkdir(parents=True, exist_ok=True)

            def launch():
                subprocess.Popen(self._chrome_args(headed),
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            if not self._debug_alive():
                # 端口没起：多半是旧实例锁着同一配置目录却没带调试端口，先清掉再启动
                self._kill_stale_profile_chrome()
                self._log("正在启动 Chrome（独立配置，登录一次即长期复用）...")
                try:
                    launch()
                except Exception as e:
                    self._log(f"❌ 启动 Chrome 失败：{e}")
                    return False
                if not self._wait_debug_ready(20):
                    self._log("⚠ 调试端口未就绪，清理残留 Chrome 后重试…")
                    self._kill_stale_profile_chrome()
                    launch()
                    if not self._wait_debug_ready(15):
                        self._log("❌ Chrome 调试端口未就绪")
                        return False
                time.sleep(1.2)

            # 附加前保证有标签页，否则 0 标签会让 webdriver 附加报 unable to discover open pages
            self._ensure_debug_tab()
            try:
                self.driver = self._attach()
            except Exception as e:
                # 端口在但接不上：通常是崩溃/半死的旧实例，清掉重启一个干净的再接
                self._log(f"⚠ 连接现有 Chrome 失败（{str(e).splitlines()[0][:100] if str(e) else e}），重启后重试…")
                self.close_browser()
                self._kill_stale_profile_chrome()
                try:
                    launch()
                except Exception as e2:
                    self._log(f"❌ 重启 Chrome 失败：{e2}")
                    return False
                if not self._wait_debug_ready(20):
                    self._log("❌ Chrome 调试端口未就绪")
                    return False
                time.sleep(1.2)
                self._ensure_debug_tab()
                try:
                    self.driver = self._attach()
                except Exception as e3:
                    self._log(f"❌ 连接 Chrome 失败：{str(e3).splitlines()[0][:200] if str(e3) else e3}")
                    self._log("请手动关闭所有 Chrome 窗口后，再点一次「打开浏览器」；"
                              "若仍失败，重启一次电脑可释放被占用的独立配置目录。")
                    self.driver = None
                    return False
            self._log("✅ 已连接 Chrome")
            return True

    def close_browser(self):
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None

    def _is_session_alive(self):
        try:
            _ = self.driver.current_url
            return True
        except Exception:
            return False

    def try_restore_session(self):
        self.close_browser()
        return self.start_browser()

    def _restart_browser(self):
        return self.try_restore_session()

    # ── 标签管理（全程单标签，绝不新开标签，避免抢焦点/只能挂着）──────
    def _ensure_tab(self):
        """固定复用同一个标签；优先接管已有标签，不主动新建。"""
        handles = self.driver.window_handles
        if not handles:
            self.driver.switch_to.new_window("tab")
            self._tab = self.driver.current_window_handle
            return
        if getattr(self, "_tab", None) in handles:
            if self.driver.current_window_handle != self._tab:
                self.driver.switch_to.window(self._tab)
            return
        # 首次：优先挑一个已在 1688 的标签，否则用第一个，全程不再新建
        chosen = handles[0]
        for h in handles:
            try:
                self.driver.switch_to.window(h)
                if "1688.com" in (self.driver.current_url or ""):
                    chosen = h
                    break
            except Exception:
                continue
        self.driver.switch_to.window(chosen)
        self._tab = chosen

    def _prune_to_own_tab(self):
        """关掉 1688 页面自动弹出的多余标签，只留自己的工作标签并切回。"""
        try:
            handles = self.driver.window_handles
            if len(handles) <= 1:
                if handles:
                    self._tab = handles[0]
                return
            for h in handles:
                if h != self._tab:
                    try:
                        self.driver.switch_to.window(h)
                        self.driver.close()
                    except Exception:
                        pass
            if self._tab in self.driver.window_handles:
                self.driver.switch_to.window(self._tab)
            else:
                self._tab = self.driver.window_handles[0]
                self.driver.switch_to.window(self._tab)
        except Exception:
            pass

    def _goto(self, url, tries=4):
        # 惰性设置页面加载超时：避免个别资源卡住导致 driver.get 长时间挂起（夜间无人值守关键）
        if not getattr(self, "_timeout_set", False):
            try:
                self.driver.set_page_load_timeout(30)
            except Exception:
                pass
            self._timeout_set = True
        backoff = (2, 5, 10, 15)
        for i in range(tries):
            try:
                # _ensure_tab 也放进 try：浏览器被关/崩溃时它访问 window_handles 会抛
                # invalid session id，若留在 try 外会冒泡出 crawl 直接把采集线程打崩。
                self._ensure_tab()
                self.driver.get(url)
            except Exception as e:
                # 只记录首行，避免 chromedriver 巨长堆栈刷屏；含超时/断网等
                msg = str(e).splitlines()[0][:200] if str(e) else e.__class__.__name__
                if self._session_dead(msg):
                    # 会话/浏览器已死：原地重试同一个 driver 永远救不回来，必须重启并
                    # 重新附加 Chrome（独立 profile 登录态保留），从卡住处续采而非崩线程。
                    self._log(f"⚠ 浏览器会话已断开（{msg[:60]}），正在重启 Chrome 续采…（第{i + 1}/{tries}次）")
                    if not self.try_restore_session():
                        self._log("❌ 无法恢复浏览器会话，本轮到此结束（已采数据已保存，可稍后继续）")
                        self._stop_requested = True
                        return False
                    self._timeout_set = False   # 新 driver 需重设页面加载超时
                    time.sleep(1.5)
                    continue
                self._log(f"导航异常（第{i + 1}/{tries}次）：{msg} → 重试")
                time.sleep(backoff[min(i, len(backoff) - 1)])
                continue
            time.sleep(1.2)
            self._prune_to_own_tab()
            block = self._detect_block()
            if block:
                if not self._wait_unblock(block):
                    return False
                continue
            return True
        return False

    # 浏览器/会话已死的特征（Selenium WebDriverError 文案）：命中即重启续采，绝不崩线程。
    # 注意不能用裸 "disconnected"——断网 ERR_INTERNET_DISCONNECTED 也含该子串，
    # 而断网属正常可原地重试的瞬时错误，绝不该去重启 Chrome（会误杀正在加载的页面）。
    SESSION_DEAD_MARKS = (
        "invalid session id", "session deleted", "browser has closed",
        "chrome not running", "not connected to devtools",
        "no such window", "target window already closed",
        "unable to connect to renderer",
    )

    @classmethod
    def _session_dead(cls, msg):
        m = (msg or "").lower()
        return any(k in m for k in cls.SESSION_DEAD_MARKS)

    # ── 拦截 / 登录 判定 ───────────────────────────────────────
    def _detect_block(self):
        if self.driver is None:
            return ""
        try:
            url = (self.driver.current_url or "").lower()
            title = self.driver.title or ""
        except Exception:
            return ""
        if any(m in url for m in LOGIN_URL_MARKERS):
            return "__LOGIN__"
        if any(m in url for m in BLOCK_URL_MARKERS):
            return "页面被跳转到 1688 安全验证/风控页"
        if any(m in title for m in BLOCK_TITLE_MARKERS) and not any(h in title for h in SAFE_TITLE_HINTS):
            return f"出现验证页：{title[:24]}"
        # 正文极短且含验证字样才像插页
        try:
            body = self.driver.find_element("tag name", "body").text or ""
        except Exception:
            body = ""
        if len(body) < 300 and any(m in body for m in ("滑动验证", "拖动滑块", "验证码", "访问过于频繁")):
            return "页面被 1688 风控拦截（滑块/访问频繁）"
        return ""

    def _register_block(self):
        """记录一次拦截并返回统计窗口内的累计次数（熔断用）。"""
        now = time.time()
        self._block_times = [t for t in self._block_times if now - t < self._block_window]
        self._block_times.append(now)
        return len(self._block_times)

    def _interruptible_sleep(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            if self._stop_requested:
                return
            time.sleep(0.5)

    def _wait_unblock(self, reason, max_wait=3600):
        """被拦截时原地等人工过验证；过验后自动继续，并把采集间隔自动放大。
        非登录类拦截额外走熔断：短窗口内反复被拦＝已进限流区，
        每次都强制递增冷却（拿不到冷却窗口会把软限流熬成硬封号），
        达到阈值则直接优雅结束本轮，绝不死磕。"""
        login = (reason == "__LOGIN__")
        if login:
            self._emit("login_required_signal", "1688 需要登录，请在弹出的 Chrome 里完成登录")
            self._log("🔑 检测到未登录，请在 Chrome 窗口里登录 1688（登录后自动继续）")
        else:
            strikes = self._register_block()
            self._emit("blocked_signal", reason)
            if strikes >= self._max_blocks:
                self._log(f"🛑 {reason}")
                self._log(f"   ⛔ {self._block_window // 60} 分钟内第 {strikes} 次被拦，"
                          f"判定为持续限流。为保护账号，本轮到此为止（已采数据已保存），"
                          f"请稍后（建议 ≥30 分钟）或调大采集间隔后再跑。")
                self._stop_requested = True
                return False
            self._log(f"🛑 {reason}")
            self._log("   已暂停推进（不写入空记录）。请在 Chrome 窗口里手动过验证，通过后自动继续。")
            # 自动降速：每次被拦把间隔上调，最低 8s、最高 30s
            self._current_delay = min(30.0, max(8.0, self._current_delay * 1.6))
            self._log(f"   自动降速：采集间隔上调为约 {self._current_delay:.1f}s")

        waited = 0
        while waited < max_wait:
            if self._stop_requested:
                return False
            time.sleep(3)
            waited += 3
            state = self._detect_block()
            if state == "" or (login and state != "__LOGIN__") or (not login and state != "__LOGIN__" and not state):
                if login:
                    self._log("✅ 检测到已登录，继续采集")
                else:
                    self._log(f"✅ 验证已解除（等待约 {waited} 秒），继续采集")
                    self._emit("block_cleared_signal", reason)
                    # 冷却窗口：本轮第 2 次起，过验后先强制休息再恢复请求，
                    # 让客户端 IP 真正拿到冷却（立刻继续 = 最容易再次触发）
                    strikes = len([t for t in self._block_times
                                   if time.time() - t < self._block_window])
                    if strikes >= 2:
                        cool = min(1800, 120 * (2 ** (strikes - 1)))
                        self._log(f"   ❄ 已第 {strikes} 次被拦，强制冷却 {cool} 秒再继续"
                                  f"（给 IP 冷却窗口，避免软限流升级成硬封号）")
                        self._interruptible_sleep(cool)
                return True
            if waited % 60 == 0:
                self._log(f"   仍在等待{'登录' if login else '验证'}…已等 {waited} 秒")
        self._log("⚠ 等待超时，本轮结束（已采集数据不受影响）")
        return False

    def _sleep(self):
        d = self._current_delay + random.uniform(0, self.jitter)
        # 可被停止打断
        end = time.time() + d
        while time.time() < end:
            if self._stop_requested:
                return
            time.sleep(0.3)

    # ── 关键词解析 ─────────────────────────────────────────────
    @staticmethod
    def parse_keywords(text):
        """从上传文档/粘贴文字中识别多个关键词。按换行/常见分隔符切分并去重。"""
        if not text:
            return []
        parts = re.split(r"[\r\n,;，、；|/\t]+", text)
        seen, out = set(), []
        for p in parts:
            k = re.sub(r"\s+", " ", p).strip()
            # 去掉纯序号/Excel 列残留（如 "1. " "、产品："）
            k = re.sub(r"^\d+[.、]\s*", "", k)
            k = re.sub(r"^[^:：]{1,8}[:：]\s*", "", k) if (":" in k or "：" in k) and len(k) < 40 else k
            if k and k not in seen and len(k) <= 40:
                seen.add(k)
                out.append(k)
        return out

    # ── 列表页解析 ─────────────────────────────────────────────
    def _parse_list_page(self, html):
        """从 pageConfig 提取 listOffer 数组。返回 offer dict 列表。"""
        s = html.find("var pageConfig")
        if s < 0:
            return []
        s = html.find("{", s)
        depth = 0
        end = -1
        for i in range(s, min(len(html), s + 4_000_000)):
            c = html[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end < 0:
            return []
        try:
            pc = json.loads(html[s:end + 1])
        except Exception:
            return []
        offers = pc.get("listOffer") or []
        # 相关词（注意：1688 该字段多为全站热搜词，并非语义相关，使用时须保守过滤）
        self._last_relative = pc.get("relativeWords") or []
        return offers

    def _parse_company_page(self, html):
        """供应商(公司)搜索列表页：提取每家公司卡片的公司名与店铺子域URL。"""
        out = []
        for m in re.finditer(
                r'<a class="company-name" href="(https?://[^"/]+\.1688\.com)[^"]*"[^>]*>([^<]+)</a>',
                html):
            winport, name = m.group(1).rstrip("/"), m.group(2).strip()
            sub = winport.split("//", 1)[-1].split(".", 1)[0].lower()
            if sub in ("www", "s", "detail", "m", "login", "air", "page", "show"):
                continue
            seg = html[m.end():m.end() + 1500]
            pos = re.search(r'company-position">.*?<span[^>]*>([^<]{2,24})</span>', seg, re.S)
            out.append({"company": name, "winport": winport, "shop": sub,
                        "location": pos.group(1).strip() if pos else ""})
        return out

    def _iter_keyword_offers(self, kw):
        """遍历一个关键词的多页列表，逐条 yield offer（已按 memberId 去重）。"""
        stale_streak = 0
        for page in range(1, self.max_pages + 1):
            if self._stop_requested:
                return
            while self._pause_requested and not self._stop_requested:
                time.sleep(0.5)
            url = SEARCH_URL.format(kw=quote(kw), page=page)
            if not self._goto(url):
                return
            html = self.driver.page_source
            offers = self._parse_list_page(html)
            if not offers:
                self._log(f"  「{kw}」第 {page} 页无数据，停止翻页")
                return
            new = 0
            for o in offers:
                mid = str(o.get("memberId") or o.get("loginId") or o.get("company") or "")
                cname = self._norm_name(o.get("company") or "")
                if not mid or mid in self._seen_members or (cname and cname in self._seen_members):
                    continue
                self._seen_members.add(mid)
                if cname:
                    self._seen_members.add(cname)
                new += 1
                yield {"keyword": kw, "page": page, "raw": o,
                       "company": o.get("company") or "",
                       "offerId": o.get("offerId") or "",
                       "odUrl": o.get("odUrl") or "",
                       "memberId": mid,
                       "loginId": o.get("loginId") or "",
                       "price": o.get("price"),
                       "subject": o.get("subject") or o.get("simpleSubject") or ""}
            self._log(f"  「{kw}」第 {page} 页：{len(offers)} 条，新增公司 {new} 家")
            # 连续多页 0 新增即枯竭（主搜渠道跑过后尤其如此），别把页数预算耗在轮回的浅池上
            if new == 0:
                stale_streak += 1
                if stale_streak >= 3:
                    self._log(f"  「{kw}」商品渠道连续 {stale_streak} 页无新增，停止翻页")
                    return
            else:
                stale_streak = 0
            self._sleep()
            if len(offers) < 8:   # 尾页
                return

    @staticmethod
    def _norm_name(name):
        """公司名归一化：跨渠道去重用（去空白与常见括号差异）。"""
        return re.sub(r"[\s（）()・.、，,]+", "", name or "")

    def _iter_keyword_companies(self, kw):
        """遍历供应商(公司)搜索渠道的多页列表，逐家 yield 公司条目（跨渠道去重）。

        该渠道每页约 20 家公司卡片；实测大词单页新增公司数为商品渠道的 4 倍以上。
        早停：连续 2 页解析不到卡片视为无数据；连续 3 页 0 新增视为池子枯竭。"""
        empty_streak = stale_streak = 0
        for page in range(1, self.max_pages + 1):
            if self._stop_requested:
                return
            while self._pause_requested and not self._stop_requested:
                time.sleep(0.5)
            url = COMPANY_SEARCH_URL.format(kw=quote(kw), page=page)
            if not self._goto(url):
                return
            entries = self._parse_company_page(self.driver.page_source)
            if not entries:
                empty_streak += 1
                if empty_streak >= 2:
                    if page == 2:
                        self._log(f"  「{kw}」供应商渠道无公司卡片（1688 已改道/暂无该词公司），跳过该渠道")
                    else:
                        self._log(f"  「{kw}」供应商渠道第 {page - 1} 页起无数据，停止翻页")
                    return
                continue
            empty_streak = 0
            new = 0
            for e in entries:
                key = "shop:" + e["shop"]
                cname = self._norm_name(e["company"])
                if key in self._seen_members or (cname and cname in self._seen_members):
                    continue
                self._seen_members.add(key)
                if cname:
                    self._seen_members.add(cname)
                new += 1
                e["keyword"] = kw
                e["page"] = page
                yield e
            self._log(f"  「{kw}」供应商渠道第 {page} 页：{len(entries)} 家，新增公司 {new} 家")
            if new == 0:
                stale_streak += 1
                if stale_streak >= 3:
                    self._log(f"  「{kw}」供应商渠道连续 {stale_streak} 页无新增，公司池已枯竭")
                    return
            else:
                stale_streak = 0
            self._sleep()

    def _parse_main_search_page(self, html):
        """主搜商品搜索页：从 offer 卡片埋点属性提取 (memberId, offerId) 对，
        并从卡片 DOM 附带「公司名 + 商品标题」（列表页零成本），供进详情前早过滤用。

        每页约 60 条 offer、约 50 家不同公司；数据在 DOM 渲染属性里，
        不依赖 window.data（该对象在部分变体页里为空壳）。"""
        # offerId -> (公司名, 商品标题)：来自卡片 DOM，与埋点 (mid,oid) 按 offerId 关联
        meta = {}
        for oid, inner in OFFER_CARD_RE.findall(html):
            comp = ""
            for t in re.findall(r'title="([^"]{4,50})"', inner):
                t = t.strip()
                if t and CARD_COMPANY_SUFFIX_RE.search(t):
                    comp = t
                    break
            title = ""
            mt = CARD_TITLE_TEXT_RE.search(inner)
            if mt:
                title = re.sub(r"<[^>]+>", "", mt.group(1))
                title = re.sub(r"\s+", "", title)[:80]
            if comp or title:
                meta[oid] = (comp, title)
        out, seen = [], set()
        for mid, oid in MAIN_MEMBER_ITEM_RE.findall(html):
            key = (mid, oid)
            if key in seen:
                continue
            seen.add(key)
            comp, title = meta.get(oid, ("", ""))
            out.append({"memberId": mid, "offerId": oid, "company": comp, "title": title})
        return out

    def _parse_keywords_category(self, html):
        """主搜页内嵌 window.data.keywordsCategory：取该关键词的预测类目集合。

        allModels 里 grade 分 PRECISE（高置信）与 RELEVANT（仅相关）两档，
        过滤只认 PRECISE 档的叶子/二级类目 id 与其类目名，避免宽泛误放行。
        解析失败返回 None（后续判定 fail-open，绝不因取不到信号剔除商家）。"""
        i = html.find("keywordsCategory")
        if i < 0:
            return None
        s = html.find("{", i)
        if s < 0:
            return None
        depth = 0
        end = -1
        for j in range(s, min(len(html), s + 300_000)):
            c = html[j]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    end = j
                    break
        if end < 0:
            return None
        try:
            obj = json.loads(html[s:end + 1])
            qp = obj["data"]["qpPredictCateAllPlan"]
        except Exception:
            return None
        leaf_ids, names = set(), set()
        for m in qp.get("allModels") or []:
            if str(m.get("grade", "")).upper() != "PRECISE":
                continue
            for k in ("leafNode", "secondNode"):
                v = str(m.get(k) or "")
                if v.isdigit():
                    leaf_ids.add(v)
            n = str(m.get("cateName") or "").strip()
            if n:
                names.add(n)
        if not leaf_ids:  # 兜底：allModels 缺失时用查询级节点
            v = str(qp.get("postcateLeafNode") or "")
            if v.isdigit():
                leaf_ids.add(v)
            for k in ("postcateLeafNodes", "postcateSecondNodes"):
                for v in qp.get(k) or []:
                    v = str(v)
                    if v.isdigit():
                        leaf_ids.add(v)
        return {"ids": leaf_ids, "names": names}

    @staticmethod
    def _extract_offer_meta(html):
        """详情页零额外代价提取真实类目信号：leafCategoryId/Name、店铺主营类目、标题。"""
        meta = {"leafId": "", "leafName": "", "shopCats": [], "title": ""}
        m = DETAIL_LEAF_ID_RE.search(html)
        if m:
            meta["leafId"] = m.group(1)
        m = DETAIL_LEAF_NAME_RE.search(html)
        if m:
            meta["leafName"] = m.group(1).strip()
        meta["shopCats"] = [x.strip() for x in DETAIL_SHOP_CAT_RE.findall(html) if x.strip()]
        m = DETAIL_TITLE_RE.search(html)
        if m:
            meta["title"] = m.group(1).strip()
        return meta

    def _kw_relevant(self, meta, rec):
        """主搜商家类目相关性判定（双信号，宽松方向保数据量）：
        ① id 信号：详情页 leafCategoryId ∈ 关键词 PRECISE 预测集；
        ② 字面信号：关键词去「批发/厂家」等后缀后的二字组与 叶子类目名/店铺主营类目/
           商品标题/公司名 有重叠（覆盖「玻璃」⊂「光伏玻璃」这类父类目放行）。
        两信号都拿不到时 fail-open 放行——宁多采一家，不误杀。"""
        kw = self._guard_kw
        pred = self._guard_pred
        if pred and meta["leafId"] and meta["leafId"] in pred["ids"]:
            return True
        def grams(s):
            return {s[i:i + 2] for i in range(len(s) - 1)}
        base = re.sub(r"(批发|供应|厂家|定制|加工|工厂)", "", kw or "")
        kg = grams(base)
        # 字面信号仅在中文词时可靠（英文词与中文类目名之间二字组比对无意义）
        literal_ok = bool(kg) and bool(re.search(r"[\u4e00-\u9fa5]", base))
        if literal_ok:
            texts = ([meta["leafName"]] + meta["shopCats"][:3] +
                     [rec.get("公司名称") or ""])
            for t in texts:
                if not t:
                    continue
                segs = [x for x in re.split(r"[、，,;；/\s]+", t) if x] or [t]
                for seg in segs:
                    if kg & grams(seg) or base in seg:
                        return True
            # 标题营销词噪声大（「光伏鼠标垫」与「光伏玻璃」共享二字组即误放行），
            # 故只认「关键词完整词组原样出现在标题里」这一最强字面信号
            if base and base in meta["title"]:
                return True
        if pred and pred["names"] and meta["leafName"]:
            for n in pred["names"]:
                if grams(n) & grams(meta["leafName"]):
                    return True
        # fail-open：没有任何「可对比且可靠」的信号时不剔除。字面判定要有中文
        # 二字组才可靠（单字/纯英文词极易误杀）；字面不可用时只认 id 对比。
        comparable = bool((pred and pred["ids"] and meta["leafId"]) or
                          (literal_ok and (meta["leafName"] or meta["shopCats"] or
                                           meta["title"] or rec.get("公司名称"))))
        return not comparable

    def _list_relevant(self, company, title):
        """列表页「进详情前」的早过滤：用卡片自带的公司名+商品标题，与
        关键词字面 + 平台预测的 PRECISE 类目名做二字组比对，明显无关则连详情页都不必进
        （省一次详情导航=提速+防封）。这是大规模爬虫的通用手法：fetch 前先用廉价信号打分过滤。

        保守 fail-open——只有「有可靠中文信号且卡片文本中文、却零重叠」才判无关跳过；
        任何取不到信号/英文词/卡片文本缺失一律放行去详情，绝不误杀（详情页仍会用真实类目 id 兜底复核）。"""
        kw = self._guard_kw
        pred = self._guard_pred
        text = ((company or "") + " " + (title or "")).strip()
        if not text:
            return True   # 卡片没解析到公司名/标题 → 无法早判 → 进详情
        if not re.search(r"[\u4e00-\u9fa5]", text):
            return True   # 卡片文本非中文 → 字面判定不可靠 → 进详情

        def grams(s):
            return {s[i:i + 2] for i in range(len(s) - 1)}
        base = re.sub(r"(批发|供应|厂家|定制|加工|工厂)", "", kw or "")
        sig = grams(base)
        # 平台预测类目名并入可匹配信号（扩大安全网：如搜「制冷设备」命中「冷水机组」等预测名不误杀）
        for n in ((pred or {}).get("names") or set()):
            sig |= grams(n)
        if not sig:
            return True   # 无可靠中文信号（英文/单字词）→ 不早过滤，交详情页判定
        compact = re.sub(r"\s+", "", text)
        if sig & grams(compact) or (base and base in compact):
            return True
        return False

    def _iter_keyword_main_offers(self, kw):
        """遍历主搜渠道多页列表，逐条 yield 可进详情链路的伪 offer（按 memberId 去重）。

        早停：连续 2 页解析不到卡片视为翻页到底；连续 3 页 0 新增视为池子枯竭。"""
        empty_streak = stale_streak = 0
        self._guard_kw = kw
        self._guard_pred = None
        for page in range(1, self.max_pages + 1):
            if self._stop_requested:
                return
            while self._pause_requested and not self._stop_requested:
                time.sleep(0.5)
            url = MAIN_SEARCH_URL.format(kw=quote(kw), page=page)
            if not self._goto(url):
                return
            src = self.driver.page_source
            pairs = self._parse_main_search_page(src)
            if page == 1 and self.relevance_guard:
                # 顺带取该关键词的预测类目集（同一页源码，零额外请求）
                self._guard_pred = self._parse_keywords_category(src)
            if not pairs:
                empty_streak += 1
                if empty_streak >= 2:
                    self._log(f"  「{kw}」主搜渠道第 {page - 1} 页起无结果，停止翻页")
                    return
                continue
            empty_streak = 0
            new = 0
            page_skipped = 0
            for p in pairs:
                mid = p["memberId"]
                # 命中全局已采公司 或 本关键词下已判「类目不符」的公司 → 跳过，
                # 连详情页都不必再进（省请求=防封）；换关键词时后者不生效，仍会重评估。
                if mid in self._seen_members or f"{kw}#{mid}" in self._seen_members:
                    continue
                self._seen_members.add(mid)
                # 列表页早过滤：卡片自带的公司名+标题明显与关键词/预测类目无关 → 不进详情
                # （省一次详情导航=提速+防封）。保守 fail-open，只在强信号零重叠时才跳过。
                if self.relevance_guard and not self._list_relevant(p.get("company", ""), p.get("title", "")):
                    page_skipped += 1
                    self._early_skipped += 1
                    continue
                new += 1
                yield {"keyword": kw, "page": page, "raw": {},
                       "company": p.get("company", ""), "offerId": p["offerId"],
                       "odUrl": f"https://detail.1688.com/offer/{p['offerId']}.html",
                       "memberId": mid, "loginId": "", "price": "",
                       "subject": p.get("title", ""),
                       "guard": True}   # 主搜结果参差，进联系方式前做类目相关性过滤
            tail = f"，早过滤 {page_skipped} 家(未进详情)" if page_skipped else ""
            self._log(f"  「{kw}」主搜渠道第 {page} 页：{len(pairs)} 条offer，新增公司 {new} 家{tail}")
            if new == 0:
                stale_streak += 1
                if stale_streak >= 3:
                    self._log(f"  「{kw}」主搜渠道连续 {stale_streak} 页无新增，公司池已枯竭")
                    return
            else:
                stale_streak = 0
            self._sleep()

    def _related_candidates(self, kw):
        """从商品列表页 relativeWords 里保守筛选相关词。

        实测 1688 的 relativeWords 多为全站热搜词（搜「光伏玻璃」会给「t恤批发」），
        故只保留与主词去掉「批发/厂家」等后缀后、仍共享至少一个二字子串的候选词。
        多数情况下筛完所剩无几——宁缺毋滥，避免把无关行业词混进采集队列。"""
        def grams(s):
            return {s[i:i + 2] for i in range(len(s) - 1)}
        base = re.sub(r"(批发|供应|厂家|定制|加工|工厂)$", "", kw)
        kg = grams(base)
        if not kg:
            return []
        out = []
        for w in getattr(self, "_last_relative", []) or []:
            w = str(w).strip()
            if not w or w == kw or len(w) > 40 or w in out:
                continue
            core = re.sub(r"(批发|供应|厂家|定制|加工|工厂)$", "", w)
            if grams(core) & kg:
                out.append(w)
        return out[:5]

    # ── 详情 + 联系方式 ────────────────────────────────────────
    def _extract_winport(self, html):
        """详情页里取 winportUrl 与 contactinfoUrl。"""
        winport = contact = company = ""
        m = re.search(r'"winportUrl"\s*:\s*"(https?://[^"]+?\.1688\.com)"', html)
        if m:
            winport = m.group(1)
        m = re.search(r'"contactinfoUrl"\s*:\s*"(https?://[^"]+?contactinfo[^"]*)"', html)
        if m:
            contact = m.group(1).replace("\\/", "/")
        m = re.search(r'"companyName"\s*:\s*"([^"]+)"', html)
        if m:
            company = m.group(1)
        if not contact and winport:
            contact = winport.rstrip("/") + "/page/contactinfo.html"
        return winport, contact, company

    @staticmethod
    def _visible_text(html):
        """提取页面「可见文本」：剔除 <script>/<style> 块、所有 URL、再转纯文本。
        防止图片资源 ID / JS 里的数字串被误判成电话。"""
        t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.I | re.S)
        t = re.sub(r"https?://\S+", " ", t)
        t = re.sub(r"<[^>]+>", " ", t)
        t = t.replace("&nbsp;", " ")
        return re.sub(r"\s+", " ", t)

    def _parse_contact(self, html):
        """联系方式页：按标签提取电话/手机/传真/地址/联系人/邮箱。"""
        def after_label(lbl):
            i = html.find(lbl)
            if i < 0:
                return ""
            seg = html[i:i + 600]
            txt = re.sub(r"<[^>]+>", " ", seg)          # 去掉完整标签
            txt = txt.split(lbl, 1)[-1]                  # 去掉标签名本身
            txt = txt.split("<", 1)[0]                   # 去掉被截断的半个标签
            txt = re.sub(r"\s+", " ", txt)
            for stop in ("电话：", "手机：", "传真：", "地址：", "联系人", "邮编",
                         "主营", "经营模式", "公司：", "http"):
                if stop in txt:
                    txt = txt.split(stop)[0]
            txt = txt.strip(" ：:　")
            return "" if txt in ("暂无", "-", "无") else txt

        phone_raw = after_label("电话：")
        mobile_raw = after_label("手机：")
        fax_raw = after_label("传真：")
        addr_raw = after_label("地址：")

        # 联系方式页存在模板变体：卖家自定义块常写成「姓名：手机号」并与地址挤在一起，
        # 只用「电话：/手机：」标签会漏采。故除标签外，再对整页做号码兜底扫描。
        # 关键：兜底文本必须先剔除 <script>/<style> 块与所有 URL，否则 1688 图片资源 ID
        # （如 cbu01.alicdn.com/img/ibank/13907553770_...）会被当成手机号误采。
        plain = self._visible_text(html)
        mobiles = [x for x in PHONE_RE.findall(mobile_raw) if re.fullmatch(r"1[3-9]\d{9}", x)]
        mobiles += re.findall(r"(?<!\d)1[3-9]\d{9}(?!\d)", plain)
        mobiles = list(dict.fromkeys(mobiles))
        mset = set(mobiles)
        # 固话：标签区固话式号码（排除已被判为手机的）+ 整页带分隔符的固话，避免误抓长数字串
        tel_list = [p for p in PHONE_RE.findall(phone_raw) if p not in mset]
        tel_list += re.findall(r"(?<!\d)(?:\+?86[\s-]?)?(0\d{2,4}[-\s]\d{7,8})(?!\d)", plain)
        # 按纯数字（去国家码）去重，避免「86 0534 …」与「0534 …」重复
        _seen_tel, _tel = set(), []
        for t in tel_list:
            t = re.sub(r"\s+", " ", t).strip()
            digits = re.sub(r"\D", "", t)
            core = digits[2:] if digits.startswith("86") else digits
            if not core or core in _seen_tel:
                continue
            _seen_tel.add(core)
            _tel.append(t)
        tel_list = _tel
        # 丢弃「假固话」：1688 常把 区号+手机 连写（如 86 0512 18912740987），
        # 固话式正则只截到区号+手机前 8 位。去掉前导区号后若以 1[3-9] 开头即手机号，
        # 真实手机号已由整页扫描收入「手机」，这里把它从固话里剔除。
        def _glued_mobile(t):
            d = re.sub(r"\D", "", t)
            d = d[2:] if d.startswith("86") else d
            sub = re.sub(r"^0\d{2,3}", "", d)
            return bool(re.match(r"1[3-9]", sub))
        tel_list = [t for t in tel_list if not _glued_mobile(t)]

        # 联系人：优先「联系人：」标签，其次 1688 常见「X先生/X女士」呈现
        person = after_label("联系人：") or after_label("联系人:")
        if not person:
            mp = re.search(r'>([\u4e00-\u9fa5]{2,4}(?:先生|女士))<', html)
            if not mp:
                mp = re.search(r'([\u4e00-\u9fa5]{2,4}(?:先生|女士))',
                               re.sub(r"<[^>]+>", " ", html))
            if mp:
                person = mp.group(1).strip()

        # 邮箱：只在确有「邮箱：」标签的区域取，绝不整页扫描（整页会把 logo@2x.png
        # 这类图片文件名误判成邮箱）；再过滤掉任何图片/资源后缀。
        email_list = []
        i = html.find("邮箱")
        if i >= 0:
            region = re.sub(r"<[^>]+>", " ", html[i:i + 200])
            for cand in EMAIL_RE.findall(region):
                if not re.search(r"\.(png|jpe?g|gif|webp|svg|bmp|ico)$", cand, re.I):
                    email_list.append(cand)

        return {
            "联系人": person,
            "电话": " / ".join(tel_list),
            "手机": " / ".join(dict.fromkeys(mobiles)),
            "传真": "" if fax_raw in ("暂无", "-", "") else fax_raw,
            "地址": "" if addr_raw == "暂无" else addr_raw,
            "邮箱": " / ".join(dict.fromkeys(email_list)),
        }

    def _crawl_company(self, offer):
        rec = {
            "关键词": offer["keyword"],
            "公司名称": offer["company"],
            "联系人": "",
            "电话": "", "手机": "", "传真": "", "地址": "", "邮箱": "",
            "主营商品": offer["subject"],
            "价格": offer.get("price") or "",
            "店铺URL": "", "详情URL": "", "联系方式URL": "",
            "offerId": offer["offerId"], "memberId": offer["memberId"],
            "采集时间": time.strftime("%Y-%m-%d %H:%M:%S"),
            "状态": "",
        }
        od = offer["odUrl"] or f"https://detail.1688.com/offer/{offer['offerId']}.html"
        if not od.startswith("http"):
            od = "https:" + od
        if not self._goto(od):
            rec["状态"] = "详情页拦截未解除"
            return rec
        rec["详情URL"] = self.driver.current_url
        html = self.driver.page_source
        winport, contact, company = self._extract_winport(html)
        if company and not rec["公司名称"]:
            rec["公司名称"] = company
        rec["店铺URL"] = winport
        rec["联系方式URL"] = contact
        if offer.get("guard"):
            meta = self._extract_offer_meta(html)
            if self.debug_collect:      # 开发调参用：留痕判定输入
                self._guard_debug.append({"memberId": offer.get("memberId"),
                                          "company": rec["公司名称"],
                                          "kw": offer["keyword"], "meta": meta})
            if not rec["主营商品"]:
                rec["主营商品"] = meta["title"] or " / ".join(meta["shopCats"][:3])
            if self.relevance_guard and not self._kw_relevant(meta, rec):
                # 类目与关键词不符：到此为止，不再请求联系方式页（既保精准，
                # 又省下无效商家的后续请求——低频才是防封的关键）
                rec["状态"] = "类目不符(已过滤)"
                return rec
        if not contact:
            rec["状态"] = "无联系方式入口"
            return rec
        self._sleep()
        if not self._goto(contact):
            rec["状态"] = "联系方式页拦截未解除"
            return rec
        # 联系方式页可能仍需登录：跳登录则记录状态、不写空壳
        if self._detect_block() == "__LOGIN__":
            rec["状态"] = "未登录-联系方式不可见"
            return rec
        chtml = self.driver.page_source
        rec.update(self._parse_contact(chtml))
        has_contact = any(rec[k] for k in ("电话", "手机", "邮箱", "地址"))
        # 联系方式页有两套模板（contactinfo.htm / contactinfo.html）：若当前模板没解析到，
        # 换另一套 URL 再试一次（部分卖家页面用自定义块，只有 .html 模板能取到）
        if not has_contact and winport:
            alt = contact if contact and contact.rstrip("/").endswith(".html") \
                else winport.rstrip("/") + "/page/contactinfo.html"
            if alt and alt != contact:
                self._sleep()
                if self._goto(alt) and self._detect_block() != "__LOGIN__":
                    r2 = self._parse_contact(self.driver.page_source)
                    rec.update({k: v for k, v in r2.items() if v})
                    has_contact = any(rec[k] for k in ("电话", "手机", "邮箱", "地址"))
        rec["状态"] = ("成功" if any(rec[k] for k in ("电话", "手机", "邮箱"))
                  else ("无电话(1688未公开)" if rec["地址"] else "无联系方式"))
        return rec

    def _crawl_shop_company(self, entry):
        """供应商渠道采集：店铺URL直达联系方式页（跳过商品详情页，一家只需一次导航）。"""
        winport = entry["winport"]
        rec = {
            "关键词": entry.get("keyword", ""),
            "公司名称": entry["company"],
            "联系人": "", "电话": "", "手机": "", "传真": "", "地址": "", "邮箱": "",
            "主营商品": "", "价格": "",
            "店铺URL": winport, "详情URL": "", "联系方式URL": winport + "/page/contactinfo.html",
            "offerId": "", "memberId": entry["shop"],
            "采集时间": time.strftime("%Y-%m-%d %H:%M:%S"),
            "状态": "",
        }
        contact = winport + "/page/contactinfo.html"
        if not self._goto(contact):
            rec["状态"] = "联系方式页导航失败"
            return rec
        if self._detect_block() == "__LOGIN__":
            rec["状态"] = "未登录-联系方式不可见"
            return rec
        rec.update(self._parse_contact(self.driver.page_source))
        if not any(rec[k] for k in ("电话", "手机", "邮箱", "地址")):
            # 两套模板回退：.html 取不到就试 .htm
            alt = winport + "/page/contactinfo.htm"
            self._sleep()
            if self._goto(alt) and self._detect_block() != "__LOGIN__":
                rec["联系方式URL"] = alt
                rec.update({k: v for k, v in self._parse_contact(self.driver.page_source).items() if v})
        rec["状态"] = ("成功" if any(rec[k] for k in ("电话", "手机", "邮箱"))
                  else ("无电话(1688未公开)" if rec["地址"] else "无联系方式"))
        # 联系页没给地址时，用公司卡片上的产地做兜底（不影响上面的状态判定口径）
        if not rec["地址"] and entry.get("location"):
            rec["地址"] = entry["location"]
        return rec

    # ── 主采集循环 ─────────────────────────────────────────────
    def crawl(self, keywords, start_index=0):
        self._crawling = True
        self._stop_requested = False
        self._pause_requested = False
        self._seen_members = set()
        self._filtered_count = 0
        self._early_skipped = 0
        self._guard_debug = []
        self._block_times = []
        self._current_delay = self.delay_between
        seeded = 0
        if self.incremental:
            seeded = self._load_seen_store()
            self._seen_members |= set(self._seen_store.keys())

        if self.driver is None or not self._is_session_alive():
            if not self.start_browser():
                self._crawling = False
                self._emit("crawl_finished_signal")
                return

        # 先打开首页建立会话并检测登录
        self._goto(self.BASE_URL)
        if self._detect_block() == "__LOGIN__":
            if not self._wait_unblock("__LOGIN__"):
                self._crawling = False
                self._emit("crawl_finished_signal")
                return

        total = len(keywords)
        chans = []
        if self.main_channel:
            chans.append("主搜")
        chans.append("商品SEM")
        if self.company_channel:
            chans.append("供应商")
        skip_note = f"，增量模式已带 {seeded} 家历史公司（本轮命中即跳过、不再重复请求）" if seeded else ""
        self._log(f"🚀 开始采集，共 {total} 个关键词（{'＋'.join(chans)} 渠道），每关键词每渠道最多 {self.max_pages} 页{skip_note}")
        kws = [str(x).strip() for x in keywords]
        done = 0
        ki = start_index
        self._last_relative = []
        while ki < len(kws):
            if self._stop_requested:
                break
            while self._pause_requested and not self._stop_requested:
                time.sleep(0.5)
            kw = kws[ki]
            ki += 1
            if not kw:
                continue
            self._log(f"▶ [{ki}/{len(kws)}] 关键词：{kw}")
            self._last_relative = []
            try:
                if self.main_channel:
                    for offer in self._iter_keyword_main_offers(kw):
                        if self._stop_requested:
                            break
                        while self._pause_requested and not self._stop_requested:
                            time.sleep(0.5)
                        rec = self._crawl_company(offer)
                        if rec.get("状态") == "类目不符(已过滤)":
                            # 垃圾商家不进结果表，也省掉了联系方式页请求（防封关键）
                            self._filtered_count += 1
                            self._commit_to_store(rec)   # 记住它，下轮连详情页都不必再进
                            if self._filtered_count % 10 == 1:
                                self._log(f"  ⛔ 类目不符已过滤 {self._filtered_count} 家"
                                          f"（最近：{rec['公司名称'] or rec['主营商品'][:20]}）")
                            continue
                        self._add_result(rec)
                        done += 1
                        self._emit("progress_signal", done, max(done, 1))
                        self._emit("stats_signal",
                                   f"关键词 {ki}/{len(kws)}｜已采公司 {done}｜间隔 {self._current_delay:.1f}s")
                        self._sleep()
                for offer in self._iter_keyword_offers(kw):
                    if self._stop_requested:
                        break
                    while self._pause_requested and not self._stop_requested:
                        time.sleep(0.5)
                    rec = self._crawl_company(offer)
                    self._add_result(rec)
                    done += 1
                    self._emit("progress_signal", done, max(done, 1))
                    self._emit("stats_signal",
                               f"关键词 {ki}/{len(kws)}｜已采公司 {done}｜间隔 {self._current_delay:.1f}s")
                    self._sleep()
                if self.company_channel and not self._stop_requested:
                    for entry in self._iter_keyword_companies(kw):
                        if self._stop_requested:
                            break
                        while self._pause_requested and not self._stop_requested:
                            time.sleep(0.5)
                        rec = self._crawl_shop_company(entry)
                        self._add_result(rec)
                        done += 1
                        self._emit("progress_signal", done, max(done, 1))
                        self._emit("stats_signal",
                                   f"关键词 {ki}/{len(kws)}｜已采公司 {done}｜间隔 {self._current_delay:.1f}s")
                        self._sleep()
                if self.expand_related and not self._stop_requested:
                    added = 0
                    for w in self._related_candidates(kw):
                        if w not in kws:
                            kws.append(w)
                            added += 1
                    if added:
                        self._log(f"🔎 「{kw}」扩展相关词 {added} 个（现共 {len(kws)} 个关键词）")
            except Exception as e:
                # 兜底：迭代器内直接读 driver.page_source/current_url 等极端时序下仍可能
                # 抛会话失效。会话失效→重启续采；其它异常→跳过该关键词继续，绝不让线程崩。
                msg = str(e).splitlines()[0][:200] if str(e) else e.__class__.__name__
                if self._session_dead(msg):
                    self._log(f"⚠ 「{kw}」处理中浏览器会话断开（{msg[:60]}），重启后续采…")
                    if not self.try_restore_session():
                        self._log("❌ 无法恢复浏览器会话，本轮结束（已采数据已保存，可稍后继续）")
                        self._stop_requested = True
                    self._timeout_set = False
                else:
                    self._log(f"⚠ 「{kw}」处理异常：{msg} → 跳过该关键词继续")
            if self.incremental:
                self._save_seen_store()   # 每个关键词收尾即落盘，中途被拦/异常也不丢已学到的
        self._crawling = False
        if self.incremental:
            self._save_seen_store()
        tail = (f"，另有 {self._filtered_count} 家类目不相关已过滤"
                if self._filtered_count else "")
        early = (f"，列表页早过滤 {self._early_skipped} 家(未进详情即省请求)"
                 if self._early_skipped else "")
        saved = f"，种子库累计 {len(self._seen_store)} 家" if self.incremental else ""
        self._log(f"✅ 采集结束，共 {len(self.results)} 家公司{early}{tail}{saved}")
        self._emit("crawl_finished_signal")

    # 兼容旧契约（GUI 可能调用）
    def search_company(self, name):
        return self._crawl_company({"keyword": "", "company": name, "offerId": "",
                                    "odUrl": "", "memberId": name, "loginId": "",
                                    "price": "", "subject": ""})

    # ── 控制 ───────────────────────────────────────────────────
    def stop_crawl(self):
        self._stop_requested = True
        self._log("正在停止…")

    def pause_crawl(self):
        self._pause_requested = True
        self._log("已暂停")

    def resume_crawl(self):
        self._pause_requested = False
        self._log("已恢复")

    # ── 导出 ───────────────────────────────────────────────────
    EXPORT_HEADERS = ["序号", "关键词", "公司名称", "联系人", "电话", "手机", "传真",
                      "地址", "邮箱", "主营商品", "价格", "店铺URL", "详情URL",
                      "联系方式URL", "offerId", "memberId", "采集时间", "状态"]

    def export_excel(self, output_path):
        rows = self.snapshot_results()
        if not rows:
            self._log("没有数据可导出")
            return False
        try:
            from openpyxl import Workbook
            wb = Workbook()
            ws = wb.active
            ws.title = "1688企业数据"
            ws.append(self.EXPORT_HEADERS)
            for i, r in enumerate(rows, 1):
                ws.append([i] + [r.get(h, "") for h in self.EXPORT_HEADERS[1:]])
            wb.save(output_path)
            self._log(f"✅ 已导出 {len(rows)} 条：{output_path}")
            return True
        except Exception as e:
            self._log(f"❌ 导出失败：{e}")
            return False

    # ── 对接《客户库数据导入模板》清洗（复用成熟模块）───────────
    @staticmethod
    def _norm_landline(tok):
        """把 1688 的 '86 0534 5085777' 归一为清洗模块能识别的 '0534-5085777'。"""
        t = re.sub(r"^86[\s\-]?", "", (tok or "").strip())   # 去国家码
        t = re.sub(r"[\s]+", "-", t)                          # 区号与号码间空格转连字符
        return t

    def _to_cleaner_record(self, r):
        """把 1688 原始记录映射为清洗模块认识的字段。"""
        mobiles = [x for x in re.split(r"[/、,;]", r.get("手机") or "") if x.strip()]
        landlines = [self._norm_landline(x) for x in re.split(r"[/、,;]", r.get("电话") or "") if x.strip()]
        phones = " ; ".join([m.strip() for m in mobiles] + [l for l in landlines if l])
        status = r.get("状态") or ""
        return {
            "公司名": r.get("公司名称") or "",
            "联系人": r.get("联系人") or "",
            "电话": phones,
            "邮箱": r.get("邮箱") or "",
            "地址": r.get("地址") or "",
            "官网": r.get("店铺URL") or "",
            "数据来源": "1688",
            "状态": "" if status == "成功" else status,
            "备注": ("主营:" + r["主营商品"] if r.get("主营商品") else "") +
                    ("｜关键词:" + r["关键词"] if r.get("关键词") else ""),
        }

    @staticmethod
    def filter_import_rows(rows, log=None):
        """清洗后入库前的最终过滤：手机号/固话/邮箱 全为空的行直接删除。
        （原始数据不动，只影响清洗后 / 导入云展的数据。）"""
        kept = []
        dropped = 0
        for r in rows or []:
            if any(str(r.get(k) or "").strip() for k in ("手机号", "固话", "邮箱")):
                kept.append(r)
            else:
                dropped += 1
        if dropped and log:
            log(f"🧹 清洗过滤：剔除 {dropped} 条「无手机号/固话/邮箱」的记录（原数据保留）")
        return kept

    def clean_records(self, records, log=None):
        """把 1688 原始记录 → 映射 → 清洗成模板行 → 剔除无任何联系方式的行。
        AutoSaveWorker / 导出 / 云展导入三处统一走这里，保证口径一致。"""
        log = log or self._log
        recs = [self._to_cleaner_record(r) for r in records]
        rows = _cleaner.build_clean_rows(recs, log=log)
        return self.filter_import_rows(rows, log=log)

    def build_clean_rows(self, log=None):
        """返回按模板列结构清洗后的行（跨公司合并去重、拆手机/固话/邮箱、剔除无联系方式）。"""
        log = log or self._log
        return self.clean_records(self.snapshot_results(), log=log)

    def export_cleaned_excel(self, output_path, template_path="", log=None):
        """写出与《客户库数据导入模板.xlsx》完全一致格式的清洗后数据。"""
        log = log or self._log
        rows = self.build_clean_rows(log=log)
        if not rows:
            log("没有可清洗入库的数据")
            return False
        if not template_path:
            template_path = _cleaner.locate_template([str(Path(output_path).parent), str(self._data_dir)])
        return _cleaner.write_clean_excel(rows, output_path, template_path=template_path, log=log)



if __name__ == "__main__":
    # 命令行自测：python ali1688_crawler.py "AHU净化空调箱" [max_pages]
    kws = sys.argv[1].split(",") if len(sys.argv) > 1 else ["AHU净化空调箱"]
    c = Ali1688Crawler()
    if len(sys.argv) > 2:
        c.max_pages = int(sys.argv[2])
    c.delay_between = 2.0
    c.start_browser()
    c.crawl(kws)
    c.export_excel(str(Path(__file__).parent / "1688_测试导出.xlsx"))
