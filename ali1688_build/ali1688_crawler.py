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

PHONE_RE = re.compile(r'(?:86[\s-]?)?(?:0\d{2,3}[\s-]?\d{7,8}|1[3-9]\d{9})')
EMAIL_RE = re.compile(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}')


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
        self.max_pages = 10          # 每个关键词最多翻几页（1688 上限约 50）

        self._data_dir = Path(__file__).parent / "ali1688_data"
        self._data_dir.mkdir(exist_ok=True)
        self._profile_dir = self._profile_path()
        self._seen_members = set()   # 本轮已处理公司（memberId）去重

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

    # ── Chrome 生命周期 ────────────────────────────────────────
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

    def _driver_path(self):
        p = Path(__file__).resolve().parent.parent / "riskbird_build"
        if sys.platform == "win32":
            f = p / "chromedriver-win64" / "chromedriver.exe"
        else:
            f = p / "chromedriver-mac-x64" / "chromedriver"
        return str(f) if f.exists() else None

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
        只会把请求转交给旧实例然后自己退出，调试端口永远起不来。"""
        if sys.platform == "win32":
            return
        try:
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
                os.kill(pid, 15)          # SIGTERM：优雅退出，释放单实例锁
            except Exception:
                pass
        time.sleep(1.8)

    def _attach(self):
        opts = Options()
        opts.debugger_address = f"127.0.0.1:{self.debug_port}"
        drv = self._driver_path()
        if drv:
            return webdriver.Chrome(service=Service(executable_path=drv), options=opts)
        return webdriver.Chrome(options=opts)

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
            self._ensure_tab()
            try:
                self.driver.get(url)
            except Exception as e:
                # 只记录首行，避免 chromedriver 巨长堆栈刷屏；含超时/断网等
                msg = str(e).splitlines()[0][:200] if str(e) else e.__class__.__name__
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

    def _wait_unblock(self, reason, max_wait=3600):
        """被拦截时原地等人工过验证；过验后自动继续，并把采集间隔自动放大。"""
        login = (reason == "__LOGIN__")
        if login:
            self._emit("login_required_signal", "1688 需要登录，请在弹出的 Chrome 里完成登录")
            self._log("🔑 检测到未登录，请在 Chrome 窗口里登录 1688（登录后自动继续）")
        else:
            self._emit("blocked_signal", reason)
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
        total_pages = 0
        # 分页信息（部分字段名随版本变化，取能取到的）
        return offers

    def _iter_keyword_offers(self, kw):
        """遍历一个关键词的多页列表，逐条 yield offer（已按 memberId 去重）。"""
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
                if not mid or mid in self._seen_members:
                    continue
                self._seen_members.add(mid)
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
            self._sleep()
            if len(offers) < 8:   # 尾页
                return

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

    # ── 主采集循环 ─────────────────────────────────────────────
    def crawl(self, keywords, start_index=0):
        self._crawling = True
        self._stop_requested = False
        self._pause_requested = False
        self._seen_members = set()
        self._current_delay = self.delay_between

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
        self._log(f"🚀 开始采集，共 {total} 个关键词，每关键词最多 {self.max_pages} 页")
        done = 0
        for ki in range(start_index, total):
            if self._stop_requested:
                break
            while self._pause_requested and not self._stop_requested:
                time.sleep(0.5)
            kw = str(keywords[ki]).strip()
            if not kw:
                continue
            self._log(f"▶ [{ki+1}/{total}] 关键词：{kw}")
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
                           f"关键词 {ki+1}/{total}｜已采公司 {done}｜间隔 {self._current_delay:.1f}s")
                self._sleep()
        self._crawling = False
        self._log(f"✅ 采集结束，共 {len(self.results)} 家公司")
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
