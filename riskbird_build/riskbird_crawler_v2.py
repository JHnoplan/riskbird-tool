"""
riskbird_crawler_v2.py - 风鸟 (riskbird.com) 爬虫 v2
基于真实 HTML 结构，使用自动补全搜索 + 详情页全量采集
"""

import json
import os
import sys
import time
import re
import threading
import traceback
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlparse, parse_qs

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    NoSuchElementException, TimeoutException,
    StaleElementReferenceException
)

try:
    from PyQt5.QtCore import QObject, pyqtSignal
    HAS_PYQT = True
except ImportError:
    HAS_PYQT = False

import block_guard


# ── Qt 信号类 ──
if HAS_PYQT:
    class CrawlerSignals(QObject):
        log_signal = pyqtSignal(str)
        progress_signal = pyqtSignal(int, int)
        crawl_finished_signal = pyqtSignal()
        login_status_signal = pyqtSignal(bool, str)
        manual_check_signal = pyqtSignal(str)
        blocked_signal = pyqtSignal(str)          # 被安全验证/风控拦截，需人工在浏览器完成验证
        block_cleared_signal = pyqtSignal(str)    # 人工完成验证，采集已自动继续
        login_required_signal = pyqtSignal(str)   # 未登录，需界面上确认去登录还是继续
else:
    class CrawlerSignals:
        pass


# ── Chrome 检测与自动安装（跨平台） ──
CHROME_PATHS_WIN = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]
CHROME_PATHS_MAC = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    os.path.expanduser("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
]

def is_chrome_installed() -> bool:
    """检测 Chrome 是否已安装（跨平台）"""
    if sys.platform == "darwin":
        for path in CHROME_PATHS_MAC:
            if os.path.exists(path):
                return True
        return False
    else:
        for path in CHROME_PATHS_WIN:
            if os.path.isfile(path):
                return True
        try:
            import winreg
            for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                key = winreg.OpenKey(root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe")
                winreg.CloseKey(key)
                return True
        except Exception:
            pass
        return False

def download_and_install_chrome(callback=None) -> bool:
    """下载并安装 Chrome（目前仅支持 Windows，Mac 提示手动安装）"""
    def _log(msg):
        if callback: callback(msg)
        print(msg)
    if sys.platform == "darwin":
        _log("Mac 系统请手动安装 Chrome: https://www.google.com/chrome/")
        return False
    _log("正在下载 Google Chrome...")
    temp_dir = Path(os.environ.get("TEMP", "/tmp"))
    installer_path = temp_dir / "ChromeSetup.exe"
    try:
        import subprocess
        url = "https://dl.google.com/chrome/install/latest/chrome_installer.exe"
        ps_cmd = f"[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12;(New-Object Net.WebClient).DownloadFile('{url}','{installer_path}')"
        subprocess.run(["powershell", "-Command", ps_cmd], check=True, capture_output=True, timeout=180)
        if not installer_path.exists():
            _log("Chrome 下载失败"); return False
        _log("下载完成，正在安装 Chrome...")
        subprocess.run([str(installer_path), "/silent", "/install"], check=True, capture_output=True, timeout=120)
        time.sleep(5)
        if is_chrome_installed():
            _log("Chrome 安装成功！"); return True
        else:
            _log("Chrome 安装可能未完成"); return False
    except Exception as e:
        _log(f"Chrome 安装失败: {e}"); return False


class RiskBirdCrawlerV2:
    BASE_URL = "https://www.riskbird.com"
    PLATFORM_LABEL = "风鸟"
    SEARCH_URL = f"{BASE_URL}/search/company"
    # 补全接口（同源 JSON，不导航首页、不敲搜索框、不计次）
    FAST_SEARCH_API = "/riskbird-api/api/v1/companies/fast-search"

    # 额度墙判据（详情页每账号每日 50 次；列表页不消耗这项额度）
    QUOTA_WALL_KEYS = ("今日查询额度已用完", "明日0点自动恢复", "充值获取更多查询额度")
    # 模糊查询额度墙判据（关键词搜索本身也有每日上限，与详情页额度相互独立）
    # 实机验证：撞墙后列表页返回 0 卡片 + 弹窗“您今日模糊查询次数已达上限”，
    # 但同源补全接口 fast-search 仍正常返回 entName/ENTSTATUS/entid/uniscid。
    FUZZY_WALL_KEYS = ("模糊查询次数已达上限", "今日模糊查询次数", "访问已达上限")
    # 频率限制判据（平台临时限流，几分钟会自行恢复，应退避重试而非放弃）
    THROTTLE_KEYS = ("操作过于频繁", "请稍后再试", "访问过于频繁", "请求过于频繁",
                     "安全验证", "滑动验证", "网络异常，请稍后")
    EMPTY_RESULT_KEYS = ("为您找到 0", "没有找到", "未找到相关", "暂无数据", "无搜索结果")
    STATUS_NO_MATCH = "未找到"
    STATUS_QUOTA = "额度用尽待重试"
    STATUS_FUZZY_QUOTA = "模糊查询额度用尽待重试"
    STATUS_THROTTLE = "访问频繁待重试"

    # 列表页卡片解析选择器（按 class 词精确匹配，避免误命中 line-info/company-info）
    _CARD_XP = "//div[contains(concat(' ',normalize-space(@class),' '),' company-item ')]"
    _INFO_XP = ".//div[contains(concat(' ',normalize-space(@class),' '),' info ')]"
    _LABEL_XP = "./span[contains(concat(' ',normalize-space(@class),' '),' label ')]"
    # 顶部搜索框：用于回读页面当前回显的是哪一次搜索
    _SEARCH_INPUT_XP = ("//input[contains(@placeholder,'企业名')"
                        " or contains(@placeholder,'公司')"
                        " or contains(@class,'el-input__inner')]")

    def __init__(self, gui_callback=None):
        self.driver = None
        self.wait = None
        self._logged_in = False
        self._crawling = False
        self._stop_requested = False
        self._pause_requested = False
        self.results = []
        # 采集线程在写、GUI 线程在读（自动保存取快照），靠锁交接
        self.results_lock = threading.RLock()
        self.gui_callback = gui_callback
        self._browser_lock = threading.Lock()

        # ── 额度自适应：列表页(不计次)为主链路，详情页仅在必要时补采 ──
        self.detail_enrich = True   # 列表页缺联系方式时是否进详情页补齐
        self.force_detail = False   # 强制逐家进详情页抓全量联系方式（耗额度，受 detail_budget 限制）
        self.detail_budget = 40     # 单轮详情页访问上限（低于每日 50 次额度）
        self._detail_used = 0       # 本轮已访问详情页次数
        self._quota_blocked = False  # 撞过额度墙则本轮不再尝试详情页
        self._budget_warned = False
        self.pending_retry = []     # 因额度/限流未采到的公司名
        self.pending_detail = []    # 已有列表页数据、但没能进详情拿全量联系方式的公司名
        self.name_mismatch = []     # 搜索页只给出模糊匹配（结果里没有目标公司）的清单
        # ── 模糊查询额度自适应：撞墙后停止搜索请求，改用不计次的补全接口 ──
        self._fuzzy_blocked = False
        self._fuzzy_warned = False
        self.search_calls = 0       # 本轮真实发出的搜索请求次数（用于核对每日上限）
        # ── 本地卡片缓存：同一家公司重复跑不再消耗模糊查询次数 ──
        self.use_cache = True
        self.cache_ttl_days = 30    # 工商字段变化慢，30 天内直接复用
        self._card_cache = {}
        self._cache_dirty = False

        # ── 频率控量：平台对短时间连续搜索会临时限流（“操作过于频繁”） ──
        self.min_interval = 2.0     # 两次列表页导航的最小间隔（秒）
        self._last_nav_ts = 0.0
        self._backoff = 0           # 当前额外退避秒数（0=未限流）
        self._throttle_hits = 0     # 连续限流次数

        if HAS_PYQT:
            self.signals = CrawlerSignals()
        else:
            self.signals = None

        # Cookie 持久化
        self._data_dir = Path(__file__).parent / "riskbird_data"
        self._data_dir.mkdir(exist_ok=True)
        self._cookie_file = self._data_dir / "cookies.json"
        self._cache_file = self._data_dir / "风鸟列表缓存.json"

        # 速率控制
        self.delay_between = 0.5
        self.max_retries = 2
        self._login_gate_obj = None

    # ── 日志 ──
    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        if self.signals:
            self.signals.log_signal.emit(line)
        if self.gui_callback:
            self.gui_callback(line)
        print(line)

    # ── 结果交接 ──
    def _add_result(self, result: dict):
        """线程安全地追加一条采集结果"""
        with self.results_lock:
            self.results.append(result)

    def snapshot_results(self) -> list:
        """给 GUI 用的快照：连每条记录的 dict 一起复制，避免写 Excel 时撞上并发修改"""
        with self.results_lock:
            return [dict(r) for r in self.results]

    # ── 登录态确认 ──
    def _emit_blocked(self, reason: str):
        if self.signals:
            try:
                self.signals.blocked_signal.emit(reason or "需要人工处理")
            except Exception:
                pass

    def _emit_cleared(self, reason: str):
        if self.signals:
            try:
                self.signals.block_cleared_signal.emit(reason)
            except Exception:
                pass

    def check_login(self):
        """用浏览器 Cookie 判登录态（风鸟的 userinfo 只发给 www.riskbird.com，
        当前页不在那个域时要先回首页再读，否则会把“读不到”误判成未登录）"""
        if self.driver is None:
            return "unknown", "浏览器还没打开"
        try:
            if "www.riskbird.com" not in (self.driver.current_url or ""):
                self.driver.get(self.BASE_URL)
                time.sleep(2)
            names = [c.get("name") for c in (self.driver.get_cookies() or [])]
        except Exception as e:
            return "unknown", f"读取 Cookie 失败：{str(e)[:60]}"
        return block_guard.login_state(names, self.PLATFORM_LABEL)

    def _gate(self):
        """登录闸门（延迟创建，signals 可能在爬虫建好之后才接上）"""
        if self._login_gate_obj is None:
            self._login_gate_obj = block_guard.LoginGate(
                self.PLATFORM_LABEL, self._log,
                lambda r: self.signals.login_required_signal.emit(r) if self.signals else None,
                lambda: self._stop_requested, self.check_login,
                emit_cleared=self._emit_cleared,
            )
        return self._login_gate_obj

    def resolve_login_gate(self, choice: str):
        """界面把用户的选择（continue / login_done / stop）交回采集线程"""
        self._gate().resolve(choice)

    def _ask_login(self, reason: str) -> bool:
        """没有界面时（命令行直接跑）不等人，直接结束本轮"""
        if self.signals is None:
            self._log(f"🛑 {reason}；没有界面可确认，本轮采集结束")
            return False
        return self._gate().ensure(reason)

    def _login_gate(self) -> bool:
        """采集开始前确认登录态。True=继续，False=本轮结束"""
        state, detail = self.check_login()
        if state == "ok":
            self._log(f"🔑 {detail}")
            return True
        if state != "missing":
            return True
        return self._ask_login(detail)

    # ── Cookie 持久化 ──
    def _save_cookies(self, phone: str = ""):
        try:
            cookies = self.driver.get_cookies()
            data = {
                "cookies": cookies,
                "saved_at": datetime.now().isoformat(),
                "phone": phone,
            }
            with open(self._cookie_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self._log("Cookies 已保存")
        except Exception as e:
            self._log(f"Cookie 保存失败: {e}")

    def _load_cookies(self):
        if self._cookie_file.exists():
            try:
                with open(self._cookie_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for c in data.get("cookies", []):
                    try:
                        self.driver.add_cookie(c)
                    except Exception:
                        pass
                self._log(f"已加载 Cookies（保存于 {data.get('saved_at', '未知')}）")
                return True
            except Exception:
                pass
        return False

    def _check_login_status(self) -> bool:
        """检测是否已登录"""
        try:
            self.driver.get(self.BASE_URL)
            time.sleep(2)

            # 检查 Cookie
            cookies = self.driver.get_cookies()
            cookie_names = [c['name'] for c in cookies]
            for kw in ['token', 'session', 'login', 'auth', 'user', 'uid', 'sid']:
                for name in cookie_names:
                    if kw in name.lower():
                        self._log(f"检测到登录 Cookie: {name}")
                        return True

            # 检查页面元素
            indicators = [
                "//*[contains(text(),'SVIP')]",
                "//*[contains(text(),'****')]",
                "//span[contains(text(),'****')]",
                "//div[contains(@class,'user')]",
                "//a[contains(@class,'user')]",
                "//*[contains(@class,'nickname')]",
                "//*[contains(@class,'username')]",
                "//*[contains(@class,'phone')]",
                "//*[contains(@class,'avatar')]",
            ]
            for xpath in indicators:
                try:
                    el = self.driver.find_element(By.XPATH, xpath)
                    if el and el.is_displayed():
                        self._log(f"检测到登录标识")
                        return True
                except NoSuchElementException:
                    continue

            # JS 检测
            js_check = """
                var body = document.body.innerText;
                return body.includes('SVIP') || /1[3-9]\\d{1}\\*{4}\\d{4}/.test(body) || body.includes('积分');
            """
            if self.driver.execute_script(js_check):
                self._log("JS 检测到登录状态")
                return True

        except Exception as e:
            self._log(f"登录检测异常: {e}")
        return False

    # ── 浏览器启动 / 关闭 ──
    def start_browser(self, headed: bool = True):
        with self._browser_lock:
            if self.driver is not None:
                return
            
            # 启动前清理残留进程（避免端口冲突）
            import subprocess
            try:
                if sys.platform == "darwin":
                    subprocess.run(["pkill", "-9", "-f", "chromedriver"], 
                                 capture_output=True, timeout=5)
                    subprocess.run(["pkill", "-9", "-f", "Google Chrome"], 
                                 capture_output=True, timeout=5)
                else:
                    subprocess.run(["taskkill", "/F", "/IM", "chromedriver.exe"], 
                                 capture_output=True, timeout=5)
                    subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"], 
                                 capture_output=True, timeout=5)
                time.sleep(1)
            except Exception:
                pass  # 清理失败不影响后续启动
            
            opts = Options()
            if not headed:
                opts.add_argument("--headless=new")
            opts.add_argument("--window-size=1400,900")
            opts.add_argument("--disable-blink-features=AutomationControlled")
            opts.add_argument("--disable-notifications")
            opts.add_argument("--no-first-run")
            opts.add_argument("--disable-infobars")
            opts.add_argument("--no-sandbox")
            opts.add_argument("--disable-dev-shm-usage")  # 避免内存不足
            # 防止 Chrome 恢复上次会话/弹出"恢复页面"气泡（避免打开 hao123 等被劫持的启动页）
            opts.add_argument("--disable-session-crashed-bubble")
            opts.add_argument("--hide-crash-restore-bubble")
            opts.add_experimental_option("excludeSwitches", ["enable-automation"])
            opts.add_experimental_option("useAutomationExtension", False)

            # 使用 Chrome 配置文件保持登录状态
            # Mac 上直接用原始配置会锁定冲突，使用临时副本
            if sys.platform == "darwin":
                temp_profile = Path("/tmp/chrome_debug_profile")
                original_profile = os.path.expanduser("~/Library/Application Support/Google/Chrome")
                
                # 检查临时配置是否有效（存在且 Cookies 文件大小合理）
                profile_valid = False
                if temp_profile.exists() and (temp_profile / "Default" / "Cookies").exists():
                    cookies_size = (temp_profile / "Default" / "Cookies").stat().st_size
                    if cookies_size > 10000:  # 至少 10KB，避免空文件
                        profile_valid = True
                    else:
                        self._log(f"临时配置 Cookies 文件过小({cookies_size}字节)，重新复制...")
                
                if profile_valid:
                    opts.add_argument(f"--user-data-dir={temp_profile}")
                    opts.add_argument("--profile-directory=Default")
                    self._log("使用 Chrome 临时配置（登录态）")
                elif os.path.exists(original_profile):
                    # 重新复制配置
                    import shutil
                    if temp_profile.exists():
                        shutil.rmtree(temp_profile, ignore_errors=True)
                    self._log("正在复制 Chrome 配置...")
                    try:
                        shutil.copytree(original_profile, str(temp_profile), dirs_exist_ok=True)
                        opts.add_argument(f"--user-data-dir={temp_profile}")
                        opts.add_argument("--profile-directory=Default")
                        self._log("Chrome 配置已重新复制（登录态）")
                    except Exception as e:
                        self._log(f"复制配置失败: {e}，使用默认配置")
                else:
                    self._log("未找到 Chrome 配置，使用默认配置")
            else:
                # Windows: 复制 Chrome 配置到临时目录，避免单实例锁冲突
                chrome_profile = os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data")
                temp_profile = Path(os.environ.get("TEMP", r"C:\temp")) / "chrome_debug_profile"
                if os.path.exists(chrome_profile):
                    import shutil
                    if not temp_profile.exists():
                        self._log("正在复制 Chrome 配置...")
                        shutil.copytree(chrome_profile, str(temp_profile), dirs_exist_ok=True)
                    opts.add_argument(f"--user-data-dir={temp_profile}")
                    opts.add_argument("--profile-directory=Default")
                    self._log("使用 Chrome 配置（登录态）")

            # 使用本地 ChromeDriver（避免版本不匹配）
            from selenium.webdriver.chrome.service import Service
            if sys.platform == "darwin":
                chromedriver_path = Path(__file__).parent / "chromedriver-mac-x64" / "chromedriver"
            else:
                chromedriver_path = Path(__file__).parent / "chromedriver-win64" / "chromedriver.exe"
            
            if chromedriver_path.exists():
                service = Service(executable_path=str(chromedriver_path))
                self.driver = webdriver.Chrome(service=service, options=opts)
            else:
                self.driver = webdriver.Chrome(options=opts)
            
            self.driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"},
            )
            self.wait = WebDriverWait(self.driver, 15)
            try:
                self.driver.set_page_load_timeout(30)  # 避免首屏无限阻塞
                self.driver.set_script_timeout(20)
            except Exception:
                pass
            self._log("Chrome 浏览器已启动")
            # 显式导航到平台首页并验证（解决配置被 hao123 劫持、多标签页等问题）
            self._goto_homepage_verified()
            return True

    def _goto_homepage_verified(self) -> bool:
        """关闭多余标签页并导航到平台首页，验证成功（解决 hao123 劫持/多标签问题）"""
        try:
            # 1. 只保留第一个标签页，关闭其余（hao123 等启动页可能在多余标签）
            handles = self.driver.window_handles
            if len(handles) > 1:
                for h in handles[1:]:
                    try:
                        self.driver.switch_to.window(h)
                        self.driver.close()
                    except Exception:
                        pass
                try:
                    self.driver.switch_to.window(handles[0])
                except Exception:
                    pass

            # 2. 导航并验证（最多 3 次）
            domain = self.BASE_URL.split("//")[-1].split("/")[0]
            for attempt in range(3):
                self.driver.get(self.BASE_URL)
                time.sleep(2)
                cur = self.driver.current_url or ""
                if domain in cur:
                    return True
                self._log(f"  首页导航未生效（第{attempt+1}次），重试...")
            self._log(f"  ⚠ 未能自动打开 {domain}，请手动在浏览器访问")
            return False
        except Exception as e:
            self._log(f"  首页导航异常: {e}")
            return False

    def close_browser(self):
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None
            self._log("浏览器已关闭")

    # ── 登录流程 ──
    def login_with_credentials(self, phone: str, password: str) -> bool:
        """手机号+密码登录"""
        try:
            self.driver.get(self.BASE_URL)
            time.sleep(2)

            # 点击登录按钮
            login_triggers = self.driver.find_elements(
                By.XPATH, "//a[contains(text(),'登录')] | //button[contains(text(),'登录')] | //a[contains(@href,'login')]"
            )
            for btn in login_triggers:
                if btn.is_displayed():
                    btn.click()
                    break
            time.sleep(2)

            # 填写手机号
            phone_inputs = self.driver.find_elements(
                By.XPATH, "//input[@type='tel' or contains(@placeholder,'手机') or contains(@placeholder,'账号')]"
            )
            if phone_inputs:
                phone_inputs[0].clear()
                phone_inputs[0].send_keys(phone)

            # 填写密码
            pwd_inputs = self.driver.find_elements(By.XPATH, "//input[@type='password']")
            if pwd_inputs:
                pwd_inputs[0].clear()
                pwd_inputs[0].send_keys(password)

            # 点击登录
            login_btns = self.driver.find_elements(
                By.XPATH, "//button[contains(text(),'登录') or contains(text(),'登 录')] | //input[@type='submit']"
            )
            if login_btns:
                login_btns[0].click()
            elif pwd_inputs:
                pwd_inputs[0].submit()

            time.sleep(3)

            if self._check_login_status():
                self._logged_in = True
                self._save_cookies(phone)
                self._log("登录成功！")
                if self.signals:
                    self.signals.login_status_signal.emit(True, "登录成功")
                return True
            else:
                self._log("登录失败，请检查账号密码")
                if self.signals:
                    self.signals.login_status_signal.emit(False, "登录失败")
                return False
        except Exception as e:
            self._log(f"登录异常: {e}")
            return False

    def login_manual(self) -> bool:
        """打开登录页让用户手动登录"""
        try:
            self.driver.get(self.BASE_URL)
            time.sleep(2)
            login_triggers = self.driver.find_elements(
                By.XPATH, "//a[contains(text(),'登录')] | //button[contains(text(),'登录')]"
            )
            for btn in login_triggers:
                if btn.is_displayed():
                    btn.click()
                    break
            time.sleep(1)
            self._log("请在浏览器中完成登录")
            if self.signals:
                self.signals.manual_check_signal.emit("请在浏览器中完成登录")
            return True
        except Exception as e:
            self._log(f"打开登录页失败: {e}")
            return False

    def verify_manual_login(self) -> bool:
        """验证手动登录"""
        if self._check_login_status():
            self._logged_in = True
            self._save_cookies()
            self._log("手动登录验证成功！")
            if self.signals:
                self.signals.login_status_signal.emit(True, "登录成功")
            return True
        else:
            self._log("尚未检测到登录状态")
            if self.signals:
                self.signals.login_status_signal.emit(False, "尚未登录")
            return False

    def try_restore_session(self) -> bool:
        """从 Cookie 恢复登录"""
        if not self._cookie_file.exists():
            return False
        try:
            self.start_browser(headed=True)
            self.driver.get(self.BASE_URL)
            time.sleep(2)
            self._load_cookies()
            self.driver.refresh()
            time.sleep(3)
            if self._check_login_status():
                self._logged_in = True
                self._log("从 Cookie 恢复登录成功")
                return True
            else:
                self._log("Cookie 已过期，需重新登录")
                return False
        except Exception as e:
            self._log(f"Cookie 恢复失败: {e}")
            return False

    # ── 搜索公司（列表页不计次主链路 + 详情页额度自适应增强） ──
    def search_company(self, name: str) -> dict:
        """风鸟主链路：从搜索结果列表页取数据（不计次），详情页仅在必要时补采。

        实机穷举结论：
        1. 详情页受“每账号每日 50 次查询额度”限制，额度耗尽后新公司一律返回额度墙页；
        2. 列表页 /search/company?keyword=... 在额度耗尽时仍完整返回卡片，且不消耗额度；
        3. 列表卡片自带“基本信息”等价字段：经营状态/法定代表人/注册资本/成立日期/
           统一社会信用代码/电话/邮箱/官网/通信地址。
        因此以列表页为主数据出口，仅当卡片缺少联系方式且额度可用时才进详情页补齐。
        """
        result = self._blank_result(name)

        # 最多重试2次（第1次失败可能因会话崩溃，自动重启后重试）
        for attempt in range(2):
            try:
                if self.driver is None or not self._is_session_alive():
                    if attempt == 0:
                        self._log("  ⚠️ 会话不可用，尝试重启...")
                        self._restart_browser()
                        time.sleep(2)
                        continue
                    result["状态"] = "失败: 浏览器会话无法恢复"
                    return result

                # 1. 列表页：核心工商字段与联系方式都在这里（但受“每日模糊查询次数”限制）
                card, wall, throttled, fuzzy = self._lookup_list_card(name)

                if fuzzy:
                    # 搜索本身当日次数用完：先走不计次的补全接口核实主体，再尝试用独立的详情额度补齐
                    verified, entid, ename = self._collect_via_suggest(name, result)
                    if not verified:
                        result["状态"] = self.STATUS_FUZZY_QUOTA
                        self.pending_retry.append(name)
                        self._log(f"  ⤵ 补全接口也未命中，已记入待补采清单: {name}")
                        break
                    # 两层额度相互独立：详情额度还没用完时，直接进详情页把联系方式补齐
                    if entid and self._can_open_detail():
                        self._log("  → 搜索额度已用完，改用独立的详情额度补齐联系方式")
                        detail, dwall = self._collect_via_detail(
                            name, dict(result), entid=entid, matched=ename)
                        if detail is not None and (detail.get("电话") or detail.get("邮箱")):
                            detail["采集路径"] = "补全接口+详情页"
                            if "额度" in str(detail.get("状态") or ""):
                                detail["状态"] = ""  # 已拿到联系方式，不再是待补状态
                            result = detail
                            self._log(f"  详情额度补齐完成: {result['公司名']} | 电话{result['电话'] or '—'}"
                                      f" | 邮箱{result['邮箱'] or '—'}")
                    if not (result.get("电话") or result.get("邮箱")):
                        self.pending_retry.append(name)
                        self._log(f"  ⤵ 已核实企业主体+信用代码（缺联系方式），"
                                  f"已记入待补采清单: {name}")
                    break

                if wall:
                    # 列表页也出现额度提示 = 账号被整体限制，记入待补采清单
                    result["状态"] = self.STATUS_QUOTA
                    self._quota_blocked = True
                    self.pending_retry.append(name)
                    self._log(f"  ⛔ 列表页出现额度提示，已记入待补采清单: {name}")
                    break

                if throttled and card is None:
                    # 被频率限流：不能误判为“未找到”，记为待重试供后续重跑
                    result["状态"] = self.STATUS_THROTTLE
                    self.pending_retry.append(name)
                    self._log(f"  ⏸ 访问频率受限，已记入待补采清单: {name}")
                    break

                if card is None:
                    # 2. 列表页无匹配卡片 → 详情页兜底一次（会消耗额度）
                    if self._quota_blocked:
                        result["状态"] = self.STATUS_NO_MATCH
                        self._log(f"  ❌ 未找到该公司（额度已用尽，无法进一步核实）: {name}")
                        break
                    self._log(f"  列表页无匹配卡片，详情页兜底: {name}")
                    detail, dwall = self._collect_via_detail(name, result)
                    if detail is not None:
                        result = detail
                    else:
                        result["状态"] = self.STATUS_QUOTA if dwall else self.STATUS_NO_MATCH
                        if dwall:
                            self.pending_retry.append(name)
                        self._log(f"  ❌ 未采集到该公司: {name}"
                                  + ("（额度墙）" if dwall else "（平台无匹配结果）"))
                    break

                # 3. 列表卡片字段直接入结果
                self._note_success()
                self._merge_card(result, card)
                self._log(
                    f"  列表页采集完成: {result['公司名']} | {result['状态'] or '状态未标注'} | "
                    f"法人{result['法定代表人'] or '—'} | 电话{result['电话'] or '—'}"
                    f" | 邮箱{result['邮箱'] or '—'}")

                # 4. 额度自适应增强：默认仅当列表页未给联系方式时才进详情页补齐；
                #    勾选 force_detail 则逐家进详情页拿全量电话/邮箱（每家耗 1 次额度）
                need_enrich = self.force_detail or ((not result["电话"]) and (not result["邮箱"]))
                if need_enrich and self._can_open_detail():
                    if self.force_detail:
                        self._log(f"  → 按设置进详情页抽取全量联系方式（本日已用 {self._detail_used} 次额度）")
                    matched = card.get("匹配名") or result["公司名"]
                    enriched, _ = self._collect_via_detail(
                        name, dict(result), entid=card.get("entid", ""), matched=matched)
                    if enriched is not None:
                        enriched["采集路径"] = "列表页+详情页"
                        result = enriched
                    elif self.force_detail:
                        # 想进详情但被额度/预算挡住：记入待补全清单，额度恢复后只跑这份即可
                        self.pending_detail.append(name)
                break

            except Exception as e:
                err_str = str(e).lower()
                self._log(f"  搜索 {name} 失败(第{attempt+1}次): {e}")

                # 如果是会话问题，标记以便重试
                if "invalid session" in err_str or "session not created" in err_str or "not connected" in err_str:
                    if attempt == 0:
                        self._log("  ⚠️ 会话异常，尝试重启后重试...")
                        self.driver = None
                        time.sleep(1)
                        continue
                    else:
                        result["状态"] = f"失败: 会话异常且无法恢复"
                else:
                    result["状态"] = f"失败: {str(e)[:50]}"
                    break

        # 兜底：全字段为空且无状态 → 记为未找到，不得以空行混入采集结果
        if not result["状态"] and not any(
                result.get(k) for k in ("法定代表人", "注册资本", "统一社会信用代码",
                                        "电话", "邮箱", "地址", "官网")):
            result["状态"] = self.STATUS_NO_MATCH
        return result

    # ── 列表页（不计次）链路辅助方法 ──
    def _blank_result(self, name: str) -> dict:
        """统一的空结果模板（保证各平台记录字段一致）"""
        return {
            "公司名": name,
            "状态": "", "法定代表人": "", "注册资本": "",
            "成立日期": "", "统一社会信用代码": "",
            "电话": "", "邮箱": "", "官网": "", "地址": "",
            "数据来源": "风鸟", "搜索结果URL": "", "采集路径": "",
            "疑似匹配": "",
        }

    def _page_text(self) -> str:
        try:
            return self.driver.find_element(By.TAG_NAME, "body").text or ""
        except Exception:
            return ""

    def _is_quota_wall(self, text: str) -> bool:
        """额度墙判据：页面出现“今日查询额度已用完/明日0点自动恢复/充值获取更多额度”。"""
        t = text or ""
        if any(k in t for k in self.QUOTA_WALL_KEYS):
            return True
        return "额度已用完" in t or ("额度" in t and "充值获取" in t)

    def _is_throttled(self, text: str) -> bool:
        """频率限制判据：页面出现“您的操作过于频繁，请稍后再试”等。"""
        t = text or ""
        return any(k in t for k in self.THROTTLE_KEYS)

    def _is_fuzzy_wall(self, text: str) -> bool:
        """模糊查询额度墙判据：搜索本身当日次数用完（与详情页额度、临时限流都不同）。"""
        t = text or ""
        return any(k in t for k in self.FUZZY_WALL_KEYS)

    # ── 列表卡片本地缓存（撞墙前先把已采到的数据沉下来，重跑不再消耗次数） ──
    def _load_card_cache(self):
        """读（并清洗）本地卡片缓存：过期项与损坏文件一律丢弃。"""
        if not self.use_cache:
            return
        try:
            with open(self._cache_file, encoding="utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError:
            return
        except Exception as e:
            self._log(f"⚠ 列表缓存读取失败（忽略，不影响采集）: {e}")
            return
        if not isinstance(raw, dict):
            return
        deadline = time.time() - self.cache_ttl_days * 86400
        cache = {k: v for k, v in raw.items()
                 if isinstance(v, dict) and (v.get("ts") or 0) > deadline and v.get("card")}
        if len(cache) != len(raw):
            self._cache_dirty = True
        self._card_cache = cache

    def _cache_get(self, name: str):
        """命中缓存则返回卡片（并记上本次目标名），否则 None。"""
        if not self.use_cache:
            return None
        item = self._card_cache.get(self._norm_name(name))
        if not item:
            return None
        card = dict(item.get("card") or {})
        if not card:
            return None
        card.setdefault("匹配名", card.get("公司名") or name)
        return card

    def _cache_put(self, name: str, card: dict):
        """只缓存联系方式完整的卡片；缺电话邮箱的记下来反而会让下次跳过详情页补齐。"""
        if not self.use_cache or not card:
            return
        if not (card.get("电话") or card.get("邮箱")):
            return
        self._card_cache[self._norm_name(name)] = {"ts": time.time(), "card": dict(card)}
        self._cache_dirty = True

    def _save_card_cache(self):
        if not self.use_cache or not self._cache_dirty:
            return
        try:
            self._cache_file.parent.mkdir(exist_ok=True)
            with open(self._cache_file, "w", encoding="utf-8") as f:
                json.dump(self._card_cache, f, ensure_ascii=False, indent=1)
            self._cache_dirty = False
            self._log(f"💾 列表页数据已缓存 {len(self._card_cache)} 家（重跑这份名单不再消耗查询次数）")
        except Exception as e:
            self._log(f"⚠ 列表缓存写入失败: {e}")

    def _pace(self):
        """控制导航频率：保证两次列表页请求间隔不低于 min_interval + 当前退避值。"""
        need = self.min_interval + self._backoff
        gap = time.time() - self._last_nav_ts
        if gap < need:
            time.sleep(need - gap)
        self._last_nav_ts = time.time()

    def _note_throttle(self):
        """记录一次限流并递进退避（10/25/45/90 秒），避免越重试越深地被卡。"""
        self._throttle_hits += 1
        self._backoff = (10, 25, 45, 90)[min(self._throttle_hits, 4) - 1]
        self._log(f"⏳ 触发平台访问频率限制，退避 {self._backoff} 秒后继续（可适当调大‘请求间隔’）")

    def _note_success(self):
        """成功采集一家则重置限流计数，逐步回到正常节奏"""
        if self._throttle_hits:
            self._throttle_hits = 0
        if self._backoff:
            self._backoff = max(0, self._backoff - 10)

    def _can_open_detail(self) -> bool:
        """详情页增强是否可用（开关 + 未撞墙 + 未超本轮预算）"""
        if not self.detail_enrich or self._quota_blocked:
            return False
        if self._detail_used >= self.detail_budget:
            if not self._budget_warned:
                self._budget_warned = True
                self._log(f"  ℹ 本轮详情页预算({self.detail_budget})已用完，后续只用列表页数据")
            return False
        return True

    def _list_url(self, keyword) -> str:
        """搜索结果列表页 URL（timestamp 防缓存命中）"""
        return f"{self.SEARCH_URL}?keyword={quote(str(keyword))}&timestamp={int(time.time() * 1000)}"

    def _url_keyword_matches(self, name: str) -> bool:
        """地址栏的 keyword 是否已经是本次目标公司（导航是否真的切页了）。"""
        try:
            cur = self.driver.current_url or ""
        except Exception:
            return False
        if "/search/" not in cur:
            return False
        try:
            kw = (parse_qs(urlparse(cur).query).get("keyword") or [""])[0]
        except Exception:
            return False
        return self._norm_name(kw) == self._norm_name(name)

    def _page_keyword(self) -> str:
        """读页面顶部搜索框回显的关键词——证明当前渲染的文档确实是本次搜索。"""
        try:
            for el in self.driver.find_elements(By.XPATH, self._SEARCH_INPUT_XP):
                try:
                    if not el.is_displayed():
                        continue
                    v = (el.get_attribute("value") or "").strip()
                    if v:
                        return v
                except Exception:
                    continue
        except Exception:
            pass
        return ""

    def _nav_is_stale(self, name: str) -> bool:
        """当前文档是否还是上一次搜索（只在有正向证据时判脏，不误杀正常页）。

        背景：driver.get() 超时或被中断时，浏览器界面还停在上一家公司的结果页，
        此时 find_elements 读到的就是别家公司的卡片 → 表现为“日志在搜 A、画面显示 B”。
        我们自己拼的 URL 里 keyword 就是目标名，所以地址栏出现另一个 keyword 就是脏页的铁证。
        """
        if self._url_keyword_matches(name):
            return False
        try:
            cur = self.driver.current_url or ""
        except Exception:
            return True
        if "/search/" not in cur:
            return True
        try:
            kw = (parse_qs(urlparse(cur).query).get("keyword") or [""])[0]
        except Exception:
            kw = ""
        if kw:
            return True  # 地址栏明确是另一次搜索
        box = self._page_keyword()
        return bool(box) and self._norm_name(box) != self._norm_name(name)

    def _short_url(self) -> str:
        try:
            cur = self.driver.current_url or ""
        except Exception:
            return "<unknown>"
        try:
            kw = (parse_qs(urlparse(cur).query).get("keyword") or [""])[0]
        except Exception:
            kw = ""
        return f"keyword={kw}" if kw else cur[:80]

    @staticmethod
    def _norm_name(name) -> str:
        """公司名归一化：去空白、全角括号转半角、小写"""
        s = re.sub(r"[\s\u3000\u200b\u200c\ufeff]", "", str(name or ""))
        return s.replace("（", "(").replace("）", ")").replace("／", "/").lower()

    @classmethod
    def _clean_value(cls, text) -> str:
        """清理卡片字段值：压缩空白、去“更多 N”、去“暂无”占位"""
        s = re.sub(r"[\u200b\u200c\u200e\ufeff]", "", str(text or ""))
        s = re.sub(r"\s+", " ", s).strip()
        s = re.sub(r"更多\s*[（(]?\s*\d*\s*[)）]?", "", s).strip()
        s = s.strip("：: ").strip()
        if s in ("暂无", "无", "-", "--", "/", "未公开"):
            return ""
        return s

    def _parse_list_cards(self) -> list:
        """解析列表页公司卡片（实机验证 DOM）。

        div.company-item
          ├ div.company-name > a.xs-link-box.name[href=/ent/..?entid=..] + div.xs-statux-tag
          ├ div.company-type > div.tag × N
          └ div.company-info > div.line-info > div.info > span.label + 值
        电话/邮箱的值为 span + a「更多」，去掉尾部“更多”即可拿到首个联系方式。
        """
        cards = []
        try:
            elements = self.driver.find_elements(By.XPATH, self._CARD_XP)
        except Exception:
            return cards
        for el in elements:
            try:
                card = {"公司名": "", "状态": "", "法定代表人": "", "注册资本": "",
                        "成立日期": "", "统一社会信用代码": "", "电话": "", "邮箱": "",
                        "官网": "", "地址": "", "entid": "", "详情页URL": ""}
                for a in el.find_elements(
                        By.XPATH, ".//div[contains(@class,'company-name')]//a"):
                    nm = self._clean_value(a.text)
                    if not nm:
                        continue
                    card["公司名"] = nm
                    href = a.get_attribute("href") or ""
                    if "/ent/" in href:
                        card["详情页URL"] = href
                        m = re.search(r"[?&]entid=([^&#]+)", href)
                        if m:
                            card["entid"] = m.group(1)
                    break
                status = el.find_elements(
                    By.XPATH, ".//div[contains(@class,'xs-statux-tag')]")
                if status:
                    card["状态"] = self._clean_value(status[0].text)
                for info in el.find_elements(By.XPATH, self._INFO_XP):
                    labels = info.find_elements(By.XPATH, self._LABEL_XP)
                    if not labels:
                        continue
                    label_raw = labels[0].text or ""
                    label = self._clean_value(label_raw).rstrip("：:")
                    text = info.text or ""
                    value = text.split(label_raw, 1)[1] if label_raw and label_raw in text else ""
                    value = self._clean_value(value)
                    if not value or not label:
                        continue
                    if "法定代表人" in label:
                        card["法定代表人"] = value
                    elif "信用代码" in label:
                        card["统一社会信用代码"] = value
                    elif "注册资本" in label:
                        card["注册资本"] = value
                    elif "成立日期" in label or "开业日期" in label:
                        card["成立日期"] = value
                    elif "电话" in label:
                        card["电话"] = value
                    elif "邮箱" in label:
                        card["邮箱"] = value
                    elif "官网" in label or "网址" in label:
                        card["官网"] = value
                    elif "地址" in label:
                        if "通信" in label or not card["地址"]:
                            card["地址"] = value
                if card["公司名"]:
                    cards.append(card)
            except Exception:
                continue
        return cards

    @classmethod
    def _name_match(cls, requested: str, actual: str) -> bool:
        """判定平台返回名与目标名可否视为同一家公司（宁缺勿错）。

        接受：归一化完全一致 / 仅尾部括号备注不同 / 高度相似且行政区划与组织形式一致。
        相似度需≥0.95 且前 3 字与后 4 字均相同，避免“德兰”误为“德星云”这类不同主体。
        """
        a, b = cls._norm_name(requested), cls._norm_name(actual)
        if not a or not b:
            return False
        if a == b:
            return True
        if re.sub(r"\([^)]*\)$", "", a) == re.sub(r"\([^)]*\)$", "", b):
            return True
        if len(a) < 6 or len(b) < 6 or a[:3] != b[:3] or a[-4:] != b[-4:]:
            return False
        try:
            import difflib
            return difflib.SequenceMatcher(None, a, b).ratio() >= 0.95
        except Exception:
            return False

    def _match_card(self, cards: list, name: str):
        """按公司名挑出目标卡片（规则见 _name_match）。

        宁缺勿错：没把握认定是同一家公司就返回 None（判为未找到），
        不把近似名称的另一家公司写进客户库。
        """
        for c in cards:
            if self._name_match(name, c["公司名"]):
                if self._norm_name(c["公司名"]) != self._norm_name(name):
                    self._log(f"  ⚠ 近似名称采集（请人工核对）：{name} → {c['公司名']}")
                return dict(c, 匹配名=c["公司名"])
        return None

    def _lookup_list_card(self, name: str):
        """打开列表页并挑出目标卡片。

        返回 (card|None, 详情页额度墙, 频率限流, 模糊查询额度墙)。

        节流与容错要点（实机验证）：
        1. 先查本地缓存：同一家公司重跑不再消耗模糊查询次数；
        2. 卡片正常 1-2 秒内就渲染完，所以先用一次便宜的 find_elements 探存在，
           再整体解析（body.text 在这个站点很慢，不能每轮都读）；
        3. 解析前先确认文档确实是本次搜索（_nav_is_stale），否则会把上一家残留的
           卡片当成结果，出现“日志在搜 A、画面显示 B”的不一致；
        4. “操作过于频繁”是临时限流（几分钟自解）→ 退避重试；
           “今日模糊查询次数已达上限”是当日额度→ 立即停止搜索链路，重试只会白烧次数；
        5. “为您找到 0 条”可能是真无数据，也可能是限流/撞墙的伪装 → 退避确认一次再判。
        """
        cached = self._cache_get(name)
        if cached is not None:
            self._log(f"  命中本地缓存，跳过搜索（不消耗查询次数）: {cached.get('公司名') or name}")
            return cached, False, False, False

        for rnd in range(3):
            if self._fuzzy_blocked:
                # 当日搜索次数已用完：本次不再发请求，由调用方改走补全接口
                return None, False, False, True
            self._pace()
            timeout_hit = False
            try:
                self.driver.get(self._list_url(name))
                self.search_calls += 1
            except Exception as e:
                timeout_hit = True
                self._log(f"  列表页导航异常(第{rnd+1}次): {str(e)[:60]}")

            cards, wall, throttled, fuzzy, stale = [], False, False, False, False
            for i in range(16):  # 最多等约 10 秒
                time.sleep(0.65)
                if self._nav_is_stale(name):
                    stale = True
                    continue  # 还停在上一家的文档，继续等，绝不能读它的卡片
                stale = False
                if timeout_hit and i < 4:
                    continue  # 导航超时后先给渲染留时间，不急着读整页文本
                try:
                    present = self.driver.find_elements(By.XPATH, self._CARD_XP)
                except Exception:
                    present = []
                if present:
                    cards = self._parse_list_cards()
                    break
                if i >= 6:  # 前几秒不读整页文本，避免把正常渲染误判为受限
                    text = self._page_text()
                    if self._is_fuzzy_wall(text):
                        fuzzy = True
                        break
                    if self._is_quota_wall(text):
                        wall = True
                        break
                    if self._is_throttled(text):
                        throttled = True
                        break
            if stale:
                # 地址栏始终不是本次搜索：当导航未生效处理（重进），而不是误判“未找到”
                self._log(f"  导航未切换到本次搜索（当前页: {self._short_url()}），重新进入")
                continue
            if fuzzy:
                self._fuzzy_blocked = True
                self._log("  ⛔ 今日模糊查询次数已达上限（与详情页额度相互独立，明日 0 点恢复）")
                return None, False, False, True
            if wall:
                return None, True, False, False
            if cards:
                card = self._match_card(cards, name)
                if card is not None:
                    self._cache_put(name, card)
                    return card, False, False, False
                # 有结果但都不是目标：先排除“被限流/撞墙导致结果不准”再判未找到
                text = self._page_text()
                if self._is_fuzzy_wall(text):
                    self._fuzzy_blocked = True
                    return None, False, False, True
                if self._is_throttled(text):
                    throttled = True
                else:
                    self._log(f"  列表页 {len(cards)} 条结果均非目标公司（首条: {cards[0]['公司名']}）")
                    return None, False, False, False
            else:
                text = self._page_text()
                if self._is_fuzzy_wall(text):
                    self._fuzzy_blocked = True
                    self._log("  ⛔ 今日模糊查询次数已达上限（明日 0 点恢复）")
                    return None, False, False, True
                if self._is_quota_wall(text):
                    return None, True, False, False
                if self._is_throttled(text):
                    throttled = True
                elif any(k in text for k in self.EMPTY_RESULT_KEYS) and not timeout_hit:
                    # 页面明确提示无结果且未限流 → 先退避确认一次，防止限流伪装成 0 条
                    if rnd == 0:
                        self._note_throttle()
                        continue
                    return None, False, False, False
                else:
                    self._log(f"  列表页未解析到卡片(第{rnd+1}次)")

            if throttled:
                self._note_throttle()
                continue
            self._note_throttle()  # 无卡片且无明确提示，同样当限流处理
        self._log(f"  ⚠ 列表页多次尝试仍拿不到结果，页面片段: {' '.join(self._page_text().split())[:100]}")
        return None, False, True, False

    def _merge_card(self, result: dict, card: dict) -> dict:
        """将列表卡片字段合并进采集结果（只覆盖非空值）"""
        for key in ("公司名", "状态", "法定代表人", "注册资本", "成立日期",
                    "统一社会信用代码", "电话", "邮箱", "官网", "地址"):
            val = card.get(key) or ""
            if val:
                result[key] = val
        result["搜索结果URL"] = card.get("详情页URL") or self.SEARCH_URL
        result["采集路径"] = "列表页"
        return result

    def _collect_via_suggest(self, name: str, result: dict):
        """模糊查询额度用尽时的降级出口：同源补全接口（实机验证撞墙后仍可用）。

        它能给出 公司名/经营状态/统一社会信用代码/entid（进而拼出详情页链接），
        但不含联系方式，所以状态先记为 STATUS_FUZZY_QUOTA；调用方可再用独立的
        详情额度尝试补齐，都不行则留在待补采清单里等次日 0 点。返回 (是否命中, entid, 公司名)。
        """
        try:
            lst = self._extract_suggest_list(self._fast_search(name)) or []
        except Exception as e:
            self._log(f"  补全接口降级异常: {str(e)[:60]}")
            return False, "", ""
        for item in lst:
            if not isinstance(item, dict):
                continue
            ename = self._clean_value(item.get("entName") or item.get("ENTNAME")
                                      or item.get("name") or "")
            if not ename or not self._name_match(name, ename):
                continue  # 宁缺勿错：建议名不是目标公司就不写进结果
            ent_status = self._clean_value(item.get("ENTSTATUS") or item.get("entStatus") or "")
            result["公司名"] = ename
            result["状态"] = (f"{self.STATUS_FUZZY_QUOTA}（登记状态:{ent_status}）"
                          if ent_status else self.STATUS_FUZZY_QUOTA)
            result["统一社会信用代码"] = self._clean_value(item.get("uniscid") or "")
            entid = str(item.get("entid") or item.get("entId") or "")
            if entid:
                result["搜索结果URL"] = f"{self.BASE_URL}/ent/{quote(ename)}.html?entid={entid}"
            result["采集路径"] = "补全接口（无联系方式）"
            return True, entid, ename
        return False, "", ""

    def _detail_doc_ok(self, entid: str) -> bool:
        """当前文档是否确实是目标详情页（防止读到上一次搜索/别家公司的残留 DOM）。"""
        try:
            cur = self.driver.current_url or ""
        except Exception:
            return False
        if "/ent/" not in cur:
            return False
        if not entid:
            return True
        if entid in cur:
            return True
        try:
            got = (parse_qs(urlparse(cur).query).get("entid") or [""])[0]
        except Exception:
            return True
        if got:
            return got == entid
        return True  # 站点吃掉了 entid 参数：由后面的公司名称校验兜底

    def _collect_via_detail(self, name: str, base: dict, entid: str = "", matched: str = ""):
        """进详情页采集（会消耗每日额度）：返回 (result|None, 是否撞额度墙)。

        entid 为空时先用 fast-search 拦截取得（fast-search 不计次）。
        撞墙时不保留墙页空数据，由调用方回落至已有的列表页数据。
        """
        from urllib.parse import quote
        try:
            if not entid:
                entid, matched = self._get_entid_via_fastsearch(name)
                # 补全首条建议与目标名称不符时根本不进详情页：既省额度，也不会采到别家数据
                if entid and not self._name_match(name, matched):
                    self._log(f"  补全建议名称与目标不符，跳过详情页以保留额度: {name} ≠ {matched}")
                    return None, False
            if not entid:
                self._log("  详情页兜底未取得 entid")
                return None, False

            detail_url = f"{self.BASE_URL}/ent/{quote(matched or name)}.html?entid={entid}"
            self.driver.get(detail_url)
            self._detail_used += 1
            time.sleep(2)
            if not self._detail_doc_ok(entid):
                self._log(f"  直连被重定向或导航未落地，重试一次（当前页: {self._short_url()}）")
                self.driver.get(detail_url)
                self._detail_used += 1
                time.sleep(2)
            if not self._detail_doc_ok(entid):
                # 还停在列表页或别家详情页：解析它只会拿到不相干数据，宁可放弃这一次
                self._log("  ⛔ 详情页文档不是本次目标，弃用本次访问（避免采到别家公司数据）")
                return None, False

            page_text = self._page_text()
            if self._is_quota_wall(page_text):
                self._quota_blocked = True
                self._log(f"  ⛔ 命中详情额度墙（本轮详情访问 {self._detail_used} 次），后续不再访问详情页")
                return None, True

            result = dict(base)
            result["搜索结果URL"] = self.driver.current_url
            info_wrap = self.driver.find_elements(
                By.XPATH, "//div[contains(@class,'info-basic-wrap')]")
            if not info_wrap and name not in page_text[:500]:
                self._log(f"  详情页未渲染出公司主体（URL: {self.driver.current_url}）")
                return None, False
            result = self._extract_detail_data(result)
            # 详情页实际公司与目标名不符（补全首条建议可能是另一家）→ 弃用，宁缺勿错
            actual = self._clean_value(result.get("公司名") or "")
            if actual and not self._name_match(name, actual):
                self._log(f"  ⛔ 详情页实际公司与目标不符，弃用: {name} ≠ {actual}")
                return None, False
            # 详情页没给出的字段，回落使用列表页已有值，避免被空值覆盖
            for key in ("公司名", "状态", "电话", "邮箱", "地址", "官网",
                        "法定代表人", "注册资本", "成立日期", "统一社会信用代码"):
                if not result.get(key) and base.get(key):
                    result[key] = base[key]
            return result, False
        except Exception as e:
            self._log(f"  详情页采集异常: {e}")
            return None, False

    # ── 补全接口（同源 fetch，不碰首页搜索框） ──
    def _fast_search(self, name: str):
        """直接请求同源补全接口拿候选 JSON。

        旧做法要“回首页 → 注入 XHR 拦截器 → 逐字敲搜索框”才能接到一个响应，
        不仅让界面上出现“在首页搜 A 公司”的困惑画面，而且逐字输入会先触发
        短关键词的请求，拿到的可能是前缀对应的不相干公司。现在改为在已有
        风鸟页面里 fetch 同一个接口（保证同源、带 Cookie、不计次、不切页）。
        """
        js = """
        var api = arguments[0], key = arguments[1], done = arguments[2];
        fetch(api + '?limit=5&queryType=1&key=' + encodeURIComponent(key),
              {credentials: 'include', headers: {'Accept': 'application/json'}})
          .then(function(r) {
              return r.text().then(function(t) { return r.status + '|' + t; });
          })
          .then(function(s) { done(s); })
          .catch(function(e) { done('ERR|' + e); });
        """
        try:
            cur = self.driver.current_url or ""
        except Exception:
            return None
        if "riskbird.com" not in cur:
            # 接口靠 Cookie 鉴权，必须站在风鸟同源页上发；
            # 导航目标用本次公司的搜索页而不是首页，避免出现“在首页搜 A 公司”的困惑画面
            try:
                self.driver.get(self._list_url(name))
                time.sleep(2)
            except Exception:
                return None
        try:
            raw = self.driver.execute_async_script(js, self.FAST_SEARCH_API, name)
        except Exception as e:
            self._log(f"  补全接口调用异常: {str(e)[:70]}")
            return None
        if not isinstance(raw, str) or raw.startswith("ERR|") or "|" not in raw:
            self._log(f"  补全接口无响应: {str(raw)[:70]}")
            return None
        status, _, body = raw.partition("|")
        if status.strip() != "200":
            self._log(f"  补全接口 HTTP {status.strip()}")
            return None
        try:
            return json.loads(body)
        except Exception:
            return None

    def _get_entid_via_fastsearch(self, name: str):
        """补全接口取 entid（不计次、不导航首页、不碰搜索框）。返回 (entid, 公司名)。"""
        try:
            resp = self._fast_search(name)
            lst = self._extract_suggest_list(resp)
            if not lst:
                return "", ""

            def _pick(item):
                ename = (item.get("ENTNAME") or item.get("entName")
                         or item.get("name") or item.get("entname") or "")
                eid = (item.get("entid") or item.get("entId")
                       or item.get("id") or item.get("pid") or "")
                return str(eid) if eid else "", ename

            for item in lst:  # 同名优先
                if isinstance(item, dict):
                    eid, ename = _pick(item)
                    if eid and ename == name:
                        return eid, ename
            for item in lst:  # 否则首条（调用方会再做名称一致性校验）
                if isinstance(item, dict):
                    eid, ename = _pick(item)
                    if eid:
                        return eid, ename
            return "", ""
        except Exception as e:
            self._log(f"  兜底取 entid 异常: {e}")
            return "", ""

    @staticmethod
    def _extract_suggest_list(resp):
        """从补全响应中稳健取出建议列表"""
        if isinstance(resp, list):
            return resp
        if isinstance(resp, dict):
            for key in ("list", "rows", "records"):
                if isinstance(resp.get(key), list):
                    return resp[key]
            data = resp.get("data")
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                for key in ("list", "rows", "records"):
                    if isinstance(data.get(key), list):
                        return data[key]
        return []

    def _extract_detail_data(self, result: dict) -> dict:
        """从详情页提取所有字段（基于实机调试验证的选择器）"""
        try:
            page_text = self.driver.find_element(By.TAG_NAME, "body").text

            # ── 公司名 ──
            # 实机验证: div.info-basic-name 文本包含公司名+状态
            try:
                name_el = self.driver.find_element(By.XPATH, "//div[contains(@class,'info-basic-name')]")
                name_text = name_el.text.strip()
                # 去掉状态标签（如"在营"）
                for status in ["在营", "存续", "在业", "注销", "吊销", "迁出", "停业"]:
                    name_text = name_text.replace(status, "").strip()
                if name_text:
                    result["公司名"] = name_text
            except NoSuchElementException:
                pass

            # ── 经营状态 ──
            # 实机验证: div.popover-tag 下的 span，第一个含"在营"等
            try:
                tags = self.driver.find_elements(By.XPATH, "//div[contains(@class,'popover-tag')]//span")
                for tag in tags:
                    t = tag.text.strip()
                    if t in ["在营", "存续", "在业", "注销", "吊销", "迁出", "停业"]:
                        result["状态"] = t
                        break
            except Exception:
                for s in ["在营", "存续", "在业", "注销", "吊销"]:
                    if s in page_text[:500]:
                        result["状态"] = s
                        break

            # ── 基本信息表格 ──
            # 实机验证: table.xs-descriptions-box > tr > th + td
            try:
                table = self.driver.find_element(By.XPATH, "//table[contains(@class,'xs-descriptions-box')]")
                rows = table.find_elements(By.TAG_NAME, "tr")
                for row in rows:
                    ths = row.find_elements(By.TAG_NAME, "th")
                    tds = row.find_elements(By.TAG_NAME, "td")
                    for th, td in zip(ths, tds):
                        label = th.text.strip().rstrip("：:")
                        value = td.text.strip()
                        if not label or not value:
                            continue
                        if "法定代表人" in label:
                            # 法定代表人 td 内可能有 logo+人名+关联N家企业
                            # 策略：过滤掉"关联...企业"等非人名行，取第一个像人名的行
                            td_text = td.text.strip()
                            lines = [l.strip() for l in td_text.split('\n') if l.strip()]
                            # 过滤：跳过包含"关联"、"企业>"、"报告"等非人名文本
                            name_candidates = [
                                l for l in lines
                                if l and not any(kw in l for kw in ['关联', '企业>', '报告', '收藏', '发票', '监控', 'logo'])
                                and 1 < len(l) <= 10  # 人名通常2-10个字
                            ]
                            if name_candidates:
                                result["法定代表人"] = name_candidates[0]
                            elif lines:
                                # 兜底：取第一个非空行
                                result["法定代表人"] = lines[0]
                        elif "统一社会信用代码" in label:
                            result["统一社会信用代码"] = value
                        elif "注册资本" in label:
                            result["注册资本"] = value
                        elif "成立日期" in label:
                            result["成立日期"] = value
                        elif "企业类型" in label:
                            result["企业类型"] = value
                        elif "所属行业" in label:
                            result["所属行业"] = value
                        elif "登记机关" in label:
                            result["登记机关"] = value
                        elif "英文名" in label:
                            result["英文名"] = value
                        elif "注册地址" in label:
                            result["地址"] = value
                        elif "通信地址" in label and not result.get("地址"):
                            result["地址"] = value
                        elif "经营业务" in label or "业务范围" in label:
                            result["经营业务范围"] = value
                        elif "工商注册号" in label:
                            result["工商注册号"] = value
                        elif "组织机构代码" in label:
                            result["组织机构代码"] = value
                        elif "参保人数" in label:
                            result["参保人数"] = value
                        elif "实缴资本" in label:
                            result["实缴资本"] = value
                        elif "营业期限" in label:
                            result["营业期限"] = value
                        elif "核准日期" in label:
                            result["核准日期"] = value
            except NoSuchElementException:
                pass

            # ── 电话和邮箱 ──
            # 实机验证: div.contact-wrap（第一个=电话，第二个=邮箱）
            #   内部: span.content-box 显示首个, span.more-btn 显示"更多 N"
            phones = []
            emails = []
            try:
                contact_wraps = self.driver.find_elements(By.XPATH, "//div[contains(@class,'contact-wrap')]")
                for idx, wrap in enumerate(contact_wraps):
                    content_boxes = wrap.find_elements(By.XPATH, ".//span[contains(@class,'content-box')]")
                    more_btns = wrap.find_elements(By.XPATH, ".//span[contains(@class,'more-btn')]")

                    for box in content_boxes:
                        text = box.text.strip()
                        if not text:
                            continue
                        if '@' in text:
                            if text not in emails:
                                emails.append(text)
                        else:
                            if text not in phones:
                                phones.append(text)

                    # 点击"更多"按钮展开全部
                    for btn in more_btns:
                        btn_text = btn.text.strip()
                        if "更多" not in btn_text:
                            continue
                        try:
                            self.driver.execute_script("arguments[0].click();", btn)
                            time.sleep(2)
                            # idx==0 是电话的更多，idx==1 是邮箱的更多
                            if idx == 0:
                                modal_items = self._extract_modal_phones()
                                for p in modal_items:
                                    if p not in phones:
                                        phones.append(p)
                            else:
                                modal_items = self._extract_modal_emails()
                                for e in modal_items:
                                    if e not in emails:
                                        emails.append(e)
                            self._close_modal()
                        except Exception as e:
                            self._log(f"  点击'更多'异常: {e}")
                        break  # 每个 contact-wrap 只处理第一个 more-btn
            except Exception as e:
                self._log(f"  联系方式提取异常: {e}")

            result["电话"] = "；".join(phones) if phones else ""
            result["邮箱"] = "；".join(emails) if emails else ""

            # ── 官网 ──
            # 实机验证: info-basic-table 中 "官网：" 后面的文本
            try:
                table_div = self.driver.find_element(By.XPATH, "//div[contains(@class,'info-basic-table')]")
                table_text = table_div.text
                m = re.search(r'官网[：:]\s*(\S+)', table_text)
                if m:
                    val = m.group(1).strip()
                    if val and val != "暂无":
                        result["官网"] = val
            except NoSuchElementException:
                pass

            # ── 地址（从表格补充） ──
            if not result.get("地址"):
                try:
                    addr_rows = self.driver.find_elements(By.XPATH, "//table[contains(@class,'xs-descriptions-box')]//tr")
                    for row in addr_rows:
                        ths = row.find_elements(By.TAG_NAME, "th")
                        tds = row.find_elements(By.TAG_NAME, "td")
                        for th, td in zip(ths, tds):
                            if "地址" in th.text:
                                result["地址"] = td.text.strip()
                except Exception:
                    pass

            # ── 简介 ──
            # 实机验证: span.xs-clip-text 中包含公司简介长文本
            try:
                clip_texts = self.driver.find_elements(By.XPATH, "//span[contains(@class,'xs-clip-text')]")
                for ct in clip_texts:
                    text = ct.text.strip()
                    if len(text) > 20 and "主要从事" in text or "经营范围" in text or "成立于" in text:
                        result["简介"] = text[:500]
                        break
            except Exception:
                pass

            result["搜索结果URL"] = self.driver.current_url
            self._log(f"  详情采集完成: {result['公司名']} | {result['状态']} | 电话{len(phones)}个 | 邮箱{len(emails)}个")

        except Exception as e:
            self._log(f"  详情采集异常: {e}")
            traceback.print_exc()

        return result

    def _find_visible_dialog(self):
        """查找当前可见的弹窗"""
        dialogs = self.driver.find_elements(By.XPATH, "//div[contains(@class,'el-dialog')]")
        for dialog in dialogs:
            try:
                if dialog.is_displayed():
                    rect = dialog.rect
                    if rect['width'] > 0 and rect['height'] > 0:
                        return dialog
            except Exception:
                continue
        if dialogs:
            return dialogs[0]
        return None

    def _extract_modal_phones(self) -> list:
        """从弹窗中提取全部电话（Element Plus el-dialog 结构）"""
        phones = []
        try:
            dialog = self._find_visible_dialog()
            if not dialog:
                self._log("  未找到电话弹窗")
                return phones
            dialog_text = dialog.text
            
            # 提取所有电话号码（手机号11位，座机号带区号如 0635-8570758）
            found = re.findall(r'1[3-9]\d{9}|0\d{2,3}[-]?\d{7,8}', dialog_text)
            for p in found:
                if p not in phones:
                    phones.append(p)
            
            # 处理分页（用JS点击避免被遮挡）
            for _ in range(20):
                try:
                    next_btn = dialog.find_element(
                        By.XPATH, ".//button[contains(@class,'next') or contains(@class,'btn-next')] | .//li[contains(@class,'next')]//button"
                    )
                    if next_btn.is_displayed() and next_btn.is_enabled():
                        self.driver.execute_script("arguments[0].click();", next_btn)
                        time.sleep(1.5)
                        dialog_text = dialog.text
                        found = re.findall(r'1[3-9]\d{9}|0\d{2,3}[-]?\d{7,8}', dialog_text)
                        for p in found:
                            if p not in phones:
                                phones.append(p)
                    else:
                        break
                except NoSuchElementException:
                    break
        except Exception as e:
            self._log(f"  弹窗电话提取异常: {e}")
        return phones

    def _extract_modal_emails(self) -> list:
        """从弹窗中提取全部邮箱（Element Plus el-dialog 结构）"""
        emails = []
        try:
            dialog = self._find_visible_dialog()
            if not dialog:
                self._log("  未找到邮箱弹窗")
                return emails
            dialog_text = dialog.text
            
            found = re.findall(r'[\w.+-]+@[\w.-]+\.\w+', dialog_text)
            for e in found:
                if e not in emails:
                    emails.append(e)
            
            # 处理分页（用JS点击避免被遮挡）
            for _ in range(20):
                try:
                    next_btn = dialog.find_element(
                        By.XPATH, ".//button[contains(@class,'next') or contains(@class,'btn-next')] | .//li[contains(@class,'next')]//button"
                    )
                    if next_btn.is_displayed() and next_btn.is_enabled():
                        self.driver.execute_script("arguments[0].click();", next_btn)
                        time.sleep(1.5)
                        dialog_text = dialog.text
                        found = re.findall(r'[\w.+-]+@[\w.-]+\.\w+', dialog_text)
                        for e in found:
                            if e not in emails:
                                emails.append(e)
                    else:
                        break
                except NoSuchElementException:
                    break
        except Exception as e:
            self._log(f"  弹窗邮箱提取异常: {e}")
        return emails

    def _close_modal(self):
        """关闭弹窗（Element Plus el-dialog）"""
        try:
            close_selectors = [
                "//div[contains(@class,'el-dialog')]//button[contains(@class,'el-dialog__close') or contains(@class,'close')]",
                "//div[contains(@class,'el-dialog')]//span[contains(@class,'close')]",
                "//div[contains(@class,'el-overlay')]//button[contains(@class,'close')]",
            ]
            for sel in close_selectors:
                try:
                    btns = self.driver.find_elements(By.XPATH, sel)
                    for btn in btns:
                        if btn.is_displayed():
                            btn.click()
                            time.sleep(0.5)
                            return
                except Exception:
                    continue
            
            # 按 ESC 关闭
            from selenium.webdriver.common.keys import Keys
            self.driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            time.sleep(0.5)
        except Exception as e:
            self._log(f"  关闭弹窗异常: {e}")

    def _is_session_alive(self) -> bool:
        """检查浏览器会话是否存活"""
        if self.driver is None:
            return False
        try:
            # 尝试获取当前URL，如果会话已死会抛异常
            _ = self.driver.current_url
            # 尝试执行一个简单的JS
            self.driver.execute_script("return 1")
            return True
        except Exception:
            return False

    def _restart_browser(self):
        """重启浏览器（会话崩溃时自动调用）"""
        self._log("⚠️ 浏览器会话已断开，正在重启...")
        try:
            if self.driver:
                try:
                    self.driver.quit()
                except Exception:
                    pass
                self.driver = None
        except Exception:
            self.driver = None
        
        time.sleep(2)
        try:
            self.start_browser(headed=True)
            self._log("✅ 浏览器已重启")
            return True
        except Exception as e:
            self._log(f"❌ 浏览器重启失败: {e}")
            return False

    # ── 批量采集 ──
    def crawl(self, companies: list, start_index: int = 0):
        """批量采集，支持从指定位置恢复，自动处理浏览器崩溃

        列表页不消耗额度，因此额度用尽时仍能连续采集；仅当列表页也被限制时
        才中止本轮，并把未采到的公司写入待补采清单（次日 0 点额度恢复后可重跑）。
        """
        self._crawling = True
        self._stop_requested = False
        self._pause_requested = False
        # 重置额度/频率自适应状态
        self._detail_used = 0
        self._quota_blocked = False
        self._budget_warned = False
        self._fuzzy_blocked = False
        self._fuzzy_warned = False
        self.search_calls = 0
        self.pending_retry = []
        self.pending_detail = []
        self._throttle_hits = 0
        self._backoff = 0
        self._last_nav_ts = 0.0
        self._load_card_cache()
        cached_n = len(self._card_cache)
        total = len(companies)
        consecutive_failures = 0  # 连续失败计数
        max_consecutive_failures = 5  # 连续失败超过此数则停止
        consecutive_quota = 0     # 连续额度墙计数
        consecutive_throttle = 0  # 连续频率限流计数

        self._log(f"开始采集，共 {total} 家公司，从第 {start_index + 1} 条开始")
        self._log("ℹ 风鸟有两层每日额度：关键词搜索（模糊查询）与详情页查询，两者独立、均 0 点恢复")
        if cached_n:
            self._log(f"ℹ 本地已缓存 {cached_n} 家列表页数据，名单里重复的公司会直接复用、不消耗查询次数")

        # 未登录时采回来的记录字段是空的，先确认登录态再开跑（浏览器未就绪时不拦）
        if not self._login_gate():
            self._crawling = False
            self._log("⏹ 未登录问题未解决，本轮采集到此结束（已采集数据不受影响）")
            if self.signals:
                self.signals.crawl_finished_signal.emit()
            return

        for idx in range(start_index, total):
            if self._stop_requested:
                self._log(f"用户停止采集，已采集 {len(self.results)} 条")
                break

            while self._pause_requested:
                time.sleep(0.5)
                if self._stop_requested:
                    break

            name = str(companies[idx]).strip()
            if not name:
                continue

            self._log(f"[{idx + 1}/{total}] 正在查询: {name}")

            if self.signals:
                self.signals.progress_signal.emit(idx + 1, total)

            # 检查会话健康
            if not self._is_session_alive():
                self._log("⚠️ 检测到浏览器会话已断开")
                if not self._restart_browser():
                    self._log("❌ 浏览器重启失败，停止采集")
                    break
                time.sleep(2)

            try:
                result = self.search_company(name)
                self._add_result(result)
                consecutive_failures = 0  # 成功则重置计数

                # 额度墙/频率限流：连续多家公司都被限则优雅停止并留清单
                status = str(result.get("状态") or "")
                if status.startswith(self.STATUS_QUOTA):
                    consecutive_quota += 1
                    if consecutive_quota >= 2:
                        self._log("❌ 列表页连续出现额度限制，中止本轮采集（待补采清单已记录）")
                        break
                else:
                    consecutive_quota = 0

                if status.startswith(self.STATUS_FUZZY_QUOTA):
                    # 搜索额度当日已完：不中止，改走不计次的补全接口把整份名单的登记信息拿完
                    consecutive_throttle = 0
                    if not getattr(self, "_fuzzy_warned", False):
                        self._fuzzy_warned = True
                        self._log("▶ 已自动切换为‘仅补全接口’模式：不消耗查询次数，但拿不到联系方式；"
                                  "明日 0 点后把待补采清单当名单重跑即可补齐")
                        self._save_card_cache()   # 撞墙前采到的数据先沉下来
                elif status.startswith(self.STATUS_THROTTLE):
                    consecutive_throttle += 1
                    if consecutive_throttle >= 3:
                        self._log("❌ 平台频率限制连续未解除，已中止本轮（避免白等）")
                        self._log(f"   已采 {len(self.results)} 条可先导出；待补采清单会在下方给出，"
                                  f"建议过 10-20 分钟后把‘请求间隔’调到 3-5 秒再继续")
                        break
                else:
                    consecutive_throttle = 0
                
                # 检查返回结果是否表示会话问题
                if status.startswith("失败"):
                    consecutive_failures += 1
                    if consecutive_failures >= max_consecutive_failures:
                        self._log(f"❌ 连续 {max_consecutive_failures} 次失败，尝试重启浏览器...")
                        if self._restart_browser():
                            consecutive_failures = 0
                            time.sleep(2)
                        else:
                            self._log("❌ 重启失败，停止采集")
                            break
            except Exception as e:
                self._log(f"查询异常: {e}")
                consecutive_failures += 1
                # 如果是会话问题，尝试重启
                if "invalid session" in str(e).lower() or "session not created" in str(e).lower():
                    self._log("⚠️ 会话异常，重启浏览器...")
                    if self._restart_browser():
                        time.sleep(2)
                
                if consecutive_failures >= max_consecutive_failures:
                    self._log(f"❌ 连续 {max_consecutive_failures} 次异常，停止采集")
                    break
            
            time.sleep(self.delay_between)

        self._crawling = False
        self._save_card_cache()
        enriched_n = sum(1 for r in self.results if r.get("采集路径") == "列表页+详情页")
        suggest_n = sum(1 for r in self.results if str(r.get("采集路径") or "").startswith("补全接口"))
        self._log(f"采集完成！共 {len(self.results)} 条数据"
                  f"（本轮真实搜索 {self.search_calls} 次、详情页访问 {self._detail_used} 次，"
                  f"全量联系方式已补齐 {enriched_n} 家"
                  + (f"，仅登记信息 {suggest_n} 家" if suggest_n else "")
                  + ("，已命中模糊查询额度墙）" if self._fuzzy_blocked
                     else ("，已命中额度墙）" if self._quota_blocked else "）")))
        self._save_pending_retry()
        if self.signals:
            self.signals.crawl_finished_signal.emit()

    def _save_pending_retry(self):
        """把因额度/频率受限未采到的公司写入待补采清单（稍后或次日 0 点后可重跑）"""
        try:
            names = list(dict.fromkeys(self.pending_retry))
            if names:
                path = self._data_dir / "风鸟待补采清单.txt"
                with open(path, "w", encoding="utf-8") as f:
                    f.write("\n".join(names) + "\n")
                self._log(f"📝 待补采 {len(names)} 家已写入：{path}")
                self._log("   频率限制通常十几分钟解除；搜索额度与详情额度每日 0 点恢复，可直接把该清单当名单重跑")
                self._log("   重跑时已采过的公司会命中本地缓存而不再消耗次数，只有未采到的才真正请求")
            # 已有列表页数据、仅缺全量联系方式的公司：单独一份清单，下次只跑这些详情页
            detail_names = list(dict.fromkeys(self.pending_detail))
            if detail_names:
                dpath = self._data_dir / "风鸟待进详情补全清单.txt"
                with open(dpath, "w", encoding="utf-8") as f:
                    f.write("\n".join(detail_names) + "\n")
                self._log(f"📝 待进详情补全 {len(detail_names)} 家（已有基本数据，仅缺全量电话/邮箱）：{dpath}")
                self._log("   明日 0 点后额度恢复，勾选‘进详情页’开关后拿这份清单当名单跑即可")
        except Exception as e:
            self._log(f"⚠ 待补采清单写入失败: {e}")

    def stop_crawl(self):
        self._stop_requested = True
        self._log("正在停止...")

    def pause_crawl(self):
        self._pause_requested = True
        self._log("采集已暂停")

    def resume_crawl(self):
        self._pause_requested = False
        self._log("采集已恢复")

    # ── 导出 Excel ──
    def export_excel(self, filepath: str):
        """将采集结果导出为 Excel"""
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

            wb = Workbook()
            ws = wb.active
            ws.title = "企业数据"

            if not self.results:
                self._log("没有数据可导出")
                return

            headers = list(self.results[0].keys())
            header_font = Font(bold=True, size=11, color="FFFFFF")
            header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
            header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
            thin_border = Border(
                left=Side(style="thin"), right=Side(style="thin"),
                top=Side(style="thin"), bottom=Side(style="thin"),
            )

            # Column 1: 序号表头
            cell = ws.cell(row=1, column=1, value="序号")
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = header_align
            cell.border = thin_border

            # Column 2+: 数据表头
            for col, h in enumerate(headers, 2):
                cell = ws.cell(row=1, column=col, value=h)
                cell.font = header_font
                cell.fill = header_fill
                cell.alignment = header_align
                cell.border = thin_border

            data_align = Alignment(vertical="top", wrap_text=True)
            for row_idx, r in enumerate(self.results, 2):
                ws.cell(row=row_idx, column=1, value=row_idx - 1).alignment = Alignment(horizontal="center")
                for col_idx, h in enumerate(headers, 2):
                    val = r.get(h, "")
                    cell = ws.cell(row=row_idx, column=col_idx, value=val)
                    cell.alignment = data_align
                    cell.border = thin_border

            for col in ws.columns:
                max_len = 0
                col_letter = col[0].column_letter
                for cell in col:
                    if cell.value:
                        max_len = max(max_len, len(str(cell.value)))
                ws.column_dimensions[col_letter].width = min(max_len + 4, 50)

            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions

            wb.save(filepath)
            self._log(f"数据已导出到: {filepath}")

        except Exception as e:
            self._log(f"导出 Excel 失败: {e}")
            traceback.print_exc()
