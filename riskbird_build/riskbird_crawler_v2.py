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


# ── Qt 信号类 ──
if HAS_PYQT:
    class CrawlerSignals(QObject):
        log_signal = pyqtSignal(str)
        progress_signal = pyqtSignal(int, int)
        crawl_finished_signal = pyqtSignal()
        login_status_signal = pyqtSignal(bool, str)
        manual_check_signal = pyqtSignal(str)
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
    SEARCH_URL = f"{BASE_URL}/search/company"

    def __init__(self, gui_callback=None):
        self.driver = None
        self.wait = None
        self._logged_in = False
        self._crawling = False
        self._stop_requested = False
        self._pause_requested = False
        self.results = []
        self.gui_callback = gui_callback
        self._browser_lock = threading.Lock()

        if HAS_PYQT:
            self.signals = CrawlerSignals()
        else:
            self.signals = None

        # Cookie 持久化
        self._data_dir = Path(__file__).parent / "riskbird_data"
        self._data_dir.mkdir(exist_ok=True)
        self._cookie_file = self._data_dir / "cookies.json"

        # 速率控制
        self.delay_between = 0.5
        self.max_retries = 2

    # ── 日志 ──
    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        if self.signals:
            self.signals.log_signal.emit(line)
        if self.gui_callback:
            self.gui_callback(line)
        print(line)

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
            self._log("Chrome 浏览器已启动")

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

    # ── 搜索公司（直接构造详情页URL，不浪费查询次数） ──
    def search_company(self, name: str) -> dict:
        """搜索公司：直接构造详情页URL → 进入详情采集（不浪费查询次数）
        
        实机验证：风鸟详情页URL格式为 /ent/公司名.html，无需entid参数，
        直接导航即可进入详情页，完全跳过搜索列表，不消耗平台查询次数。
        """
        result = {
            "公司名": name,
            "状态": "", "法定代表人": "", "注册资本": "",
            "成立日期": "", "统一社会信用代码": "",
            "电话": "", "邮箱": "", "官网": "", "地址": "",
            "数据来源": "风鸟", "搜索结果URL": "",
        }
        
        # 最多重试2次（第1次失败可能因会话崩溃，自动重启后重试）
        for attempt in range(2):
            try:
                if self.driver is None or not self._is_session_alive():
                    if attempt == 0:
                        self._log("  ⚠️ 会话不可用，尝试重启...")
                        self._restart_browser()
                        time.sleep(2)
                    else:
                        result["状态"] = "失败: 浏览器会话无法恢复"
                        return result

                # 直接构造详情页URL（不浪费查询次数）
                from urllib.parse import quote
                detail_url = f"{self.BASE_URL}/ent/{quote(name)}.html"
                self._log(f"  直接访问详情页: {detail_url}")
                self.driver.get(detail_url)
                time.sleep(3)

                result["搜索结果URL"] = self.driver.current_url

                # 检查是否成功进入详情页
                page_text = self.driver.find_element(By.TAG_NAME, "body").text
                info_wrap = self.driver.find_elements(By.XPATH, "//div[contains(@class,'info-basic-wrap')]")
                
                if info_wrap or name in page_text[:500]:
                    # 在详情页，提取数据
                    result = self._extract_detail_data(result)
                elif "没有找到" in page_text or "未找到" in page_text or "404" in self.driver.current_url:
                    result["状态"] = "未找到"
                    self._log(f"  ❌ 公司未找到: {name}")
                else:
                    # 可能在其他页面，尝试从页面中查找公司名
                    self._log(f"  页面URL: {self.driver.current_url}")
                    result = self._extract_detail_data(result)
                
                # 成功则跳出重试
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

        return result

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
        """批量采集，支持从指定位置恢复，自动处理浏览器崩溃"""
        self._crawling = True
        self._stop_requested = False
        self._pause_requested = False
        total = len(companies)
        consecutive_failures = 0  # 连续失败计数
        max_consecutive_failures = 5  # 连续失败超过此数则停止

        self._log(f"开始采集，共 {total} 家公司，从第 {start_index + 1} 条开始")

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
                self.results.append(result)
                consecutive_failures = 0  # 成功则重置计数
                
                # 检查返回结果是否表示会话问题
                if result.get("状态", "").startswith("失败"):
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
        self._log(f"采集完成！共 {len(self.results)} 条数据")
        if self.signals:
            self.signals.crawl_finished_signal.emit()

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
