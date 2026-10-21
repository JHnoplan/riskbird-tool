"""
riskbird_crawler.py - 美亚风鸟(riskbird.com) 企业数据爬虫核心模块
使用 Selenium 控制 Chrome 浏览器，实现登录、搜索、详情采集
"""

import json
import os
import sys
import time
import re
import subprocess
import threading
import traceback
from datetime import datetime
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException,
    ElementClickInterceptedException, StaleElementReferenceException
)

try:
    from PyQt5.QtCore import QObject, pyqtSignal
    HAS_PYQT = True
except ImportError:
    HAS_PYQT = False


# ─────────────────────────────────────────────
#  Qt 信号类（仅在 PyQt5 可用时使用）
# ──────────────────────────────────────────────
if HAS_PYQT:
    class CrawlerSignals(QObject):
        log_signal = pyqtSignal(str)
        progress_signal = pyqtSignal(int, int)          # current, total
        login_status_signal = pyqtSignal(bool, str)      # success, message
        manual_check_signal = pyqtSignal(str)            # message
        crawl_finished_signal = pyqtSignal()


# ── Chrome 检测与自动安装 ──
CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]

def is_chrome_installed() -> bool:
    for path in CHROME_PATHS:
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
    def _log(msg):
        if callback: callback(msg)
        print(msg)
    _log("正在下载 Google Chrome...")
    temp_dir = Path(os.environ.get("TEMP", "/tmp"))
    installer_path = temp_dir / "ChromeSetup.exe"
    try:
        url = "https://dl.google.com/chrome/install/latest/chrome_installer.exe"
        ps_cmd = f"[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12;(New-Object Net.WebClient).DownloadFile('{url}','{installer_path}')"
        subprocess.run(["powershell", "-Command", ps_cmd], check=True, capture_output=True, timeout=180)
        if not installer_path.exists():
            _log("Chrome 下载失败"); return False
        _log("下载完成，正在安装 Chrome（请勿关闭）...")
        subprocess.run([str(installer_path), "/silent", "/install"], check=True, capture_output=True, timeout=120)
        time.sleep(5)
        if is_chrome_installed():
            _log("Chrome 安装成功！"); return True
        else:
            _log("Chrome 安装可能未完成，请手动安装"); return False
    except subprocess.TimeoutExpired:
        _log("下载/安装超时，请检查网络"); return False
    except Exception as e:
        _log(f"Chrome 安装失败: {e}"); return False


# ──────────────────────────────────────────────
#  爬虫主类
# ─────────────────────────────────────────────
class RiskBirdCrawler:
    BASE_URL = "https://www.riskbird.com"
    SEARCH_URL = f"{BASE_URL}/search"
    LOGIN_URL = f"{BASE_URL}/login"

    def __init__(self, gui_callback=None):
        """
        gui_callback: callable(msg) — 用于非 Qt 环境下的日志回调
        """
        self.driver = None
        self.wait = None
        self._logged_in = False
        self._crawling = False
        self._stop_requested = False
        self.results = []
        self.gui_callback = gui_callback
        self._browser_lock = threading.Lock()  # 防止重复启动浏览器

        # Qt 信号
        if HAS_PYQT:
            self.signals = CrawlerSignals()
        else:
            self.signals = None

        # Cookie / 用户数据目录
        self._data_dir = Path(__file__).parent / "riskbird_data"
        self._data_dir.mkdir(exist_ok=True)
        self._cookie_file = self._data_dir / "cookies.json"

        # 速率控制
        self.delay_between = 3       # 每次请求间隔（秒）
        self.max_retries = 2

    # ─ 日志 ──────────────────────────────────
    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        if self.signals:
            self.signals.log_signal.emit(line)
        if self.gui_callback:
            self.gui_callback(line)
        print(line)

    # ── Cookie 持久化 ─────────────────────────
    def _save_cookies(self, phone: str = ""):
        cookies = self.driver.get_cookies()
        data = {
            "cookies": cookies,
            "saved_at": datetime.now().isoformat(),
            "phone": phone,
        }
        with open(self._cookie_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        self._log("✅ Cookies 已保存到本地")

    def _load_cookies(self):
        if self._cookie_file.exists():
            with open(self._cookie_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            for c in data.get("cookies", []):
                try:
                    self.driver.add_cookie(c)
                except Exception:
                    pass
            self._log(f" 已加载本地 Cookies（保存于 {data.get('saved_at', '未知')}）")
            return True
        return False

    def _check_login_status(self) -> bool:
        """通过 Cookie 和页面元素判断是否已登录"""
        try:
            self.driver.get(self.BASE_URL)
            time.sleep(2)
            
            # 方法1：检查登录相关的 Cookie
            cookies = self.driver.get_cookies()
            cookie_names = [c['name'] for c in cookies]
            # 常见的登录 Cookie 名称
            login_cookie_keywords = ['token', 'session', 'login', 'auth', 'user', 'uid', 'sid']
            for kw in login_cookie_keywords:
                for name in cookie_names:
                    if kw in name.lower():
                        self._log(f"  检测到登录 Cookie: {name}")
                        return True
            
            # 方法2：检查页面元素
            indicators = [
                "//div[contains(@class,'user')]",
                "//a[contains(@class,'user')]",
                "//span[contains(@class,'phone')]",
                "//div[contains(@class,'avatar')]",
                "//*[contains(text(),'SVIP')]",
                "//*[contains(text(),'****')]",
                "//span[contains(text(),'****')]",
                "//a[contains(text(),'****')]",
                "//*[contains(@class,'nickname')]",
                "//*[contains(@class,'username')]",
            ]
            for xpath in indicators:
                try:
                    el = self.driver.find_element(By.XPATH, xpath)
                    if el and el.is_displayed():
                        self._log(f"  检测到登录标识: {xpath}")
                        return True
                except NoSuchElementException:
                    continue
            
            # 方法3：用 JavaScript 检查页面中是否有用户信息
            js_check = """
                var body = document.body.innerText;
                return body.includes('SVIP') || /1[3-9]\\d{1}\\*{4}\\d{4}/.test(body);
            """
            if self.driver.execute_script(js_check):
                self._log("  JavaScript 检测到登录状态")
                return True
            
            # 方法4：检查是否没有"登录"按钮
            try:
                login_btn = self.driver.find_element(
                    By.XPATH, "//a[contains(text(),'登录') or contains(text(),'登 录')]"
                )
                return not login_btn.is_displayed()
            except NoSuchElementException:
                return True  # 找不到登录按钮 = 已登录
        except Exception as e:
            self._log(f"⚠️ 登录状态检测异常: {e}")
        return False

    # ── 浏览器启动 / 关闭 ─────────────────────
    def start_browser(self, headed: bool = True):
        with self._browser_lock:
            # 双重检查：锁内再判断一次
            if self.driver is not None:
                return
            opts = Options()
            if not headed:
                opts.add_argument("--headless=new")
            opts.add_argument("--window-size=1400,900")
            opts.add_argument("--disable-blink-features=AutomationControlled")
            opts.add_argument("--disable-notifications")
            opts.add_argument("--no-first-run")
            opts.add_argument("--disable-infobars")
            opts.add_experimental_option("excludeSwitches", ["enable-automation"])
            opts.add_experimental_option("useAutomationExtension", False)

            self.driver = webdriver.Chrome(options=opts)
            self.driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"},
            )
            self.wait = WebDriverWait(self.driver, 15)
            self._log("🌐 Chrome 浏览器已启动")

    def close_browser(self):
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None
            self._log("🔒 浏览器已关闭")

    # ── 登录流程 ──────────────────────────────
    def login_with_credentials(self, phone: str, password: str) -> bool:
        """使用手机号+密码登录"""
        try:
            # 先访问首页，再点击登录按钮
            self.driver.get(self.BASE_URL)
            time.sleep(2)
            # 尝试点击登录按钮
            login_triggers = self.driver.find_elements(
                By.XPATH, "//a[contains(text(),'登录')] | //button[contains(text(),'登录')] | //a[contains(@href,'login')]"
            )
            for btn in login_triggers:
                if btn.is_displayed():
                    btn.click()
                    break
            time.sleep(2)

            # 尝试填写手机号
            phone_inputs = self.driver.find_elements(
                By.XPATH, "//input[@type='tel' or @placeholder[contains(.,'手机') or contains(.,'phone') or contains(.,'账号')]]"
            )
            if phone_inputs:
                phone_inputs[0].clear()
                phone_inputs[0].send_keys(phone)

            # 尝试填写密码
            pwd_inputs = self.driver.find_elements(
                By.XPATH, "//input[@type='password']"
            )
            if pwd_inputs:
                pwd_inputs[0].clear()
                pwd_inputs[0].send_keys(password)

            # 点击登录按钮
            login_btns = self.driver.find_elements(
                By.XPATH,
                "//button[contains(text(),'登录')] | //a[contains(text(),'登录')] | //input[@type='submit']"
            )
            if login_btns:
                login_btns[0].click()
            else:
                if pwd_inputs:
                    pwd_inputs[0].submit()

            time.sleep(3)

            if self._check_login_status():
                self._logged_in = True
                self._save_cookies(phone)
                self._log("✅ 登录成功！")
                if self.signals:
                    self.signals.login_status_signal.emit(True, "登录成功")
                return True
            else:
                self._log(" 登录失败，请检查账号密码")
                if self.signals:
                    self.signals.login_status_signal.emit(False, "登录失败，请检查账号密码")
                return False
        except Exception as e:
            self._log(f"❌ 登录异常: {e}")
            return False

    def login_manual(self) -> bool:
        """打开登录页让用户手动登录"""
        try:
            # 访问首页，点击登录按钮
            self.driver.get(self.BASE_URL)
            time.sleep(2)
            login_triggers = self.driver.find_elements(
                By.XPATH, "//a[contains(text(),'登录')] | //button[contains(text(),'登录')] | //a[contains(@href,'login')]"
            )
            for btn in login_triggers:
                if btn.is_displayed():
                    btn.click()
                    break
            time.sleep(1)
            self._log(" 请在浏览器中手动完成登录，完成后点击'我已登录'按钮")
            if self.signals:
                self.signals.manual_check_signal.emit("请在浏览器中手动完成登录")
            return True
        except Exception as e:
            self._log(f"❌ 打开登录页失败: {e}")
            return False

    def verify_manual_login(self) -> bool:
        """用户手动登录后点击验证"""
        if self._check_login_status():
            self._logged_in = True
            self._save_cookies()
            self._log("✅ 手动登录验证成功！")
            if self.signals:
                self.signals.login_status_signal.emit(True, "登录成功")
            return True
        else:
            self._log("️ 尚未检测到登录状态，请确认已在浏览器中完成登录")
            if self.signals:
                self.signals.login_status_signal.emit(False, "尚未登录")
            return False

    def try_restore_session(self) -> bool:
        """尝试从本地 Cookie 恢复登录"""
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
                self._log("✅ 从本地 Cookie 恢复登录成功")
                return True
            else:
                self._log("⚠️ 本地 Cookie 已过期，需要重新登录")
                return False
        except Exception as e:
            self._log(f"⚠️ Cookie 恢复失败: {e}")
            return False

    # ── 搜索公司 ──────────────────────────────
    def search_company(self, name: str) -> dict:
        """搜索公司并返回第一条结果的基本信息"""
        result = {
            "公司名": name,
            "状态": "", "法定代表人": "", "注册资本": "",
            "成立日期": "", "统一社会信用代码": "",
            "电话": "", "邮箱": "", "官网": "", "地址": "",
            "搜索结果URL": "",
        }
        try:
            # 确保浏览器已启动
            if self.driver is None:
                self.start_browser(headed=True)

            self.driver.get(self.SEARCH_URL)
            time.sleep(2)

            # 输入公司名 - 多种选择器尝试
            search_input = None
            selectors = [
                "//input[@placeholder[contains(.,'企业') or contains(.,'公司') or contains(.,'搜索') or contains(.,'请输入') or contains(.,'人名')]]",
                "//input[contains(@class,'search')]",
                "//input[contains(@class,'input')]",
                "//div[contains(@class,'search')]//input",
                "//header//input",
            ]
            for sel in selectors:
                try:
                    search_input = self.driver.find_element(By.XPATH, sel)
                    if search_input.is_displayed():
                        break
                    search_input = None
                except NoSuchElementException:
                    continue

            if search_input is None:
                # 最后尝试：找到第一个可见的 input
                inputs = self.driver.find_elements(By.TAG_NAME, "input")
                for inp in inputs:
                    if inp.is_displayed() and inp.get_attribute("type") != "hidden":
                        search_input = inp
                        break

            if search_input is None:
                self._log(f"   未找到搜索输入框")
                result["状态"] = "页面异常"
                return result

            search_input.clear()
            search_input.send_keys(name)
            time.sleep(0.8)

            # 点击搜索 - 多种选择器尝试
            search_btn = None
            btn_selectors = [
                "//button[contains(text(),'搜索') or contains(text(),'查询')]",
                "//a[contains(text(),'搜索') or contains(text(),'查询')]",
                "//button[contains(@class,'search')]",
                "//div[contains(@class,'search')]//button",
                "//header//button",
            ]
            for sel in btn_selectors:
                try:
                    search_btn = self.driver.find_element(By.XPATH, sel)
                    if search_btn.is_displayed():
                        break
                    search_btn = None
                except NoSuchElementException:
                    continue

            if search_btn:
                self.driver.execute_script("arguments[0].click();", search_btn)
            else:
                # 尝试回车
                search_input.submit()

            time.sleep(3)

            # 获取第一条结果 - 多种选择器尝试
            cards = []
            card_selectors = [
                "//div[contains(@class,'company-card')]",
                "//div[contains(@class,'result-item')]",
                "//div[contains(@class,'search-item')]",
                "//div[contains(@class,'company-item')]",
                "//li[contains(@class,'company')]",
                "//div[contains(@class,'card')]//a[contains(@href,'/company')]",
                "//div[contains(@class,'list')]//div[contains(@class,'item')]",
            ]
            for sel in card_selectors:
                cards = self.driver.find_elements(By.XPATH, sel)
                if cards:
                    break

            if not cards:
                # 最后尝试：获取页面中第一个包含公司名的链接
                self._log(f"   未找到标准结果卡片，尝试通用匹配...")
                result["状态"] = "未找到"
                return result

            card = cards[0]
            card_text = card.text

            # 公司名 & 链接
            try:
                name_el = card.find_element(
                    By.XPATH, ".//a[contains(@class,'name')] | .//h3 | .//h4 | .//div[contains(@class,'title')]//a | .//a[1]"
                )
                result["公司名"] = name_el.text.strip()
                href = name_el.get_attribute("href") or ""
                if href:
                    result["搜索结果URL"] = href
            except NoSuchElementException:
                pass

            # 状态标签
            status_patterns = ["在营", "存续", "在业", "注销", "吊销", "迁出"]
            for s in status_patterns:
                if s in card_text:
                    result["状态"] = s
                    break

            # 法定代表人
            m = re.search(r"法定代表人[：:\s]*(\S+)", card_text)
            if m:
                result["法定代表人"] = m.group(1).strip()

            # 注册资本
            m = re.search(r"注册资本[：:\s]*([\d.]+\s*万\S*)", card_text)
            if m:
                result["注册资本"] = m.group(1).strip()

            # 成立日期
            m = re.search(r"成立日期[：:\s]*(\d{4}[-/]\d{1,2}[-/]\d{1,2})", card_text)
            if m:
                result["成立日期"] = m.group(1).strip()

            # 信用代码
            m = re.search(r"(?:统一社会信用代码|信用代码)[：:\s]*([A-Z0-9]{18})", card_text)
            if m:
                result["统一社会信用代码"] = m.group(1).strip()

            # 电话
            m = re.search(r"电话[：:\s]*([\d\-]+)", card_text)
            if m:
                result["电话"] = m.group(1).strip()

            # 邮箱
            m = re.search(r"邮箱[：:\s]*([\w.+-]+@[\w.-]+)", card_text)
            if m:
                result["邮箱"] = m.group(1).strip()

            # 官网
            m = re.search(r"官网[：:\s]*(https?://\S+)", card_text)
            if m:
                result["官网"] = m.group(1).strip().rstrip("，。,.")

            # 地址
            m = re.search(r"(?:通信地址|地址)[：:\s]*(.+?)(?:电话|邮箱|官网|$)", card_text, re.DOTALL)
            if m:
                result["地址"] = m.group(1).strip()[:100]

            self._log(f"  ✅ 搜索到: {result['公司名']} ({result['状态']})")

        except TimeoutException:
            self._log(f"   搜索 '{name}' 超时")
            result["状态"] = "搜索超时"
        except Exception as e:
            self._log(f"   搜索 '{name}' 异常: {e}")
            result["状态"] = f"异常: {str(e)[:50]}"

        return result

    # ── 采集详情 ──────────────────────────────
    def fetch_detail(self, result: dict) -> dict:
        """进入详情页采集更完整的数据"""
        url = result.get("搜索结果URL", "")
        if not url:
            return result

        try:
            self.driver.get(url)
            time.sleep(3)

            page_text = self.driver.find_element(By.TAG_NAME, "body").text

            # 曾用名
            m = re.search(r"曾用名[：:\s]*(\S+)", page_text)
            if m:
                result["曾用名"] = m.group(1).strip()[:100]

            # 简介
            intro_patterns = [
                r"简介[：:。\s]*([\u4e00-\u9fff].{20,500}?)(?=风险扫描|基本信息|法律诉讼|经营风险|$)",
                r"公司简介[：:。\s]*([\u4e00-\u9fff].{20,500}?)(?=风险扫描|基本信息|法律诉讼|$)",
            ]
            for pat in intro_patterns:
                m = re.search(pat, page_text, re.DOTALL)
                if m:
                    result["简介"] = m.group(1).strip()[:500]
                    break

            # 风险扫描
            risk_patterns = [
                r"自身风险\s*(\d+)",
                r"关联风险\s*(\d+)",
                r"提示信息\s*(\d+)",
                r"负面舆情\s*(\d+)",
            ]
            for pat in risk_patterns:
                m = re.search(pat, page_text)
                if m:
                    key = pat.split(r"\s*")[0]
                    result[key] = m.group(1)

            # 采集所有电话（点击"更多"弹窗）
            result["全部电话"] = self._extract_modal_data("电话")

            # 采集所有邮箱
            result["全部邮箱"] = self._extract_modal_data("邮箱")

            self._log(f"  📋 详情采集完成: {result['公司名']}")

        except Exception as e:
            self._log(f"  ️ 详情采集异常: {e}")

        return result

    # ── 弹窗数据提取（电话/邮箱） ────────────
    def _extract_modal_data(self, data_type: str) -> str:
        """
        点击"更多 N"打开弹窗，提取所有数据（支持分页）
        data_type: "电话" 或 "邮箱"
        """
        items = []
        try:
            # 定位"更多 N"链接 - 多种选择器
            more_xpaths = [
                f"//a[contains(text(),'{data_type}') and contains(text(),'更多')]",
                f"//span[contains(text(),'{data_type}')]/following-sibling::a[contains(text(),'更多')]",
                f"//*[contains(text(),'{data_type}')]//following::a[contains(text(),'更多')][1]",
                f"//a[contains(text(),'更多')][preceding-sibling::*[contains(text(),'{data_type}')]]",
            ]

            clicked = False
            for xpath in more_xpaths:
                try:
                    els = self.driver.find_elements(By.XPATH, xpath)
                    for el in els:
                        if el.is_displayed() and "更多" in el.text:
                            self.driver.execute_script("arguments[0].click();", el)
                            clicked = True
                            break
                    if clicked:
                        break
                except Exception:
                    continue

            if not clicked:
                return ""

            # 等待弹窗内容加载
            time.sleep(2)

            # 等待弹窗表格行出现
            modal_row_selectors = [
                "//div[contains(@class,'modal') or contains(@class,'dialog') or contains(@class,'popup')]//table//tr",
                "//div[contains(@class,'phone-list') or contains(@class,'email-list')]//tr",
                "//div[@role='dialog']//table//tr",
                "//div[contains(@class,'modal')]//div[contains(@class,'item')]",
                "//div[contains(@class,'dialog')]//li",
            ]
            rows = []
            for sel in modal_row_selectors:
                try:
                    rows = self.driver.find_elements(By.XPATH, sel)
                    # 过滤掉表头行
                    data_rows = [r for r in rows if r.text.strip() and "序号" not in r.text and "电话号码" not in r.text and "邮箱地址" not in r.text]
                    if data_rows:
                        rows = data_rows
                        break
                    rows = []
                except Exception:
                    continue

            if rows:
                for row in rows:
                    try:
                        cells = row.find_elements(By.TAG_NAME, "td")
                        if len(cells) >= 2:
                            val = cells[1].text.strip()
                            if val and val not in items:
                                items.append(val)
                        else:
                            # 可能是 div/li 结构
                            text = row.text.strip()
                            # 提取数字/邮箱
                            if data_type == "电话":
                                phones = re.findall(r'[\d\-]{7,}', text)
                                for p in phones:
                                    if p not in items:
                                        items.append(p)
                            elif data_type == "邮箱":
                                emails = re.findall(r'[\w.+-]+@[\w.-]+', text)
                                for e in emails:
                                    if e not in items:
                                        items.append(e)
                    except StaleElementReferenceException:
                        continue

                # 处理分页 - 点击"下一页"直到没有更多
                for page in range(20):  # 最多翻20页
                    try:
                        next_clicked = False
                        next_selectors = [
                            "//div[contains(@class,'modal') or contains(@class,'dialog') or contains(@class,'popup')]//button[contains(text(),'下一页')]",
                            "//div[contains(@class,'modal') or contains(@class,'dialog') or contains(@class,'popup')]//a[contains(text(),'下一页')]",
                            "//div[contains(@class,'pagination')]//button[contains(text(),'下一页')]",
                            "//div[contains(@class,'pagination')]//a[contains(text(),'下一页')]",
                            "//div[contains(@class,'modal') or contains(@class,'dialog') or contains(@class,'popup')]//li[contains(@class,'next')]//a",
                            "//div[contains(@class,'pagination')]//li[contains(@class,'next')]//a",
                        ]
                        for sel in next_selectors:
                            try:
                                btns = self.driver.find_elements(By.XPATH, sel)
                                for btn in btns:
                                    if btn.is_displayed() and btn.is_enabled():
                                        # 检查是否被禁用
                                        cls = btn.get_attribute("class") or ""
                                        if "disabled" in cls or "inactive" in cls:
                                            continue
                                        btn.click()
                                        next_clicked = True
                                        break
                                if next_clicked:
                                    break
                            except Exception:
                                continue

                        if not next_clicked:
                            break

                        time.sleep(1.5)

                        # 重新获取行
                        new_items_found = False
                        for sel in modal_row_selectors:
                            try:
                                new_rows = self.driver.find_elements(By.XPATH, sel)
                                data_rows = [r for r in new_rows if r.text.strip() and "序号" not in r.text and "电话号码" not in r.text and "邮箱地址" not in r.text]
                                if data_rows:
                                    new_rows = data_rows
                                else:
                                    continue
                            except Exception:
                                continue

                            for row in new_rows:
                                try:
                                    cells = row.find_elements(By.TAG_NAME, "td")
                                    if len(cells) >= 2:
                                        val = cells[1].text.strip()
                                        if val and val not in items:
                                            items.append(val)
                                            new_items_found = True
                                    else:
                                        text = row.text.strip()
                                        if data_type == "电话":
                                            phones = re.findall(r'[\d\-]{7,}', text)
                                            for p in phones:
                                                if p not in items:
                                                    items.append(p)
                                                    new_items_found = True
                                        elif data_type == "邮箱":
                                            emails = re.findall(r'[\w.+-]+@[\w.-]+', text)
                                            for e in emails:
                                                if e not in items:
                                                    items.append(e)
                                                    new_items_found = True
                                except StaleElementReferenceException:
                                    continue

                            if new_items_found:
                                break  # 找到新数据，继续下一页
                            else:
                                break  # 没有新数据，停止

                    except Exception:
                        break

            # 关闭弹窗
            self._close_modal()

        except Exception as e:
            self._log(f"  ⚠️ 提取{data_type}弹窗数据异常: {e}")
            try:
                self._close_modal()
            except Exception:
                pass

        return "；".join(items)

    def _close_modal(self):
        """关闭弹窗"""
        try:
            close_selectors = [
                "//div[contains(@class,'modal') or contains(@class,'dialog')]//button[contains(@class,'close')]",
                "//div[contains(@class,'modal') or contains(@class,'dialog')]//span[contains(text(),'×')]",
                "//div[contains(@class,'modal') or contains(@class,'dialog')]//span[contains(text(),'X')]",
                "//div[contains(@class,'modal') or contains(@class,'dialog')]//a[contains(@class,'close')]",
            ]
            for sel in close_selectors:
                try:
                    btns = self.driver.find_elements(By.XPATH, sel)
                    for btn in btns:
                        if btn.is_displayed():
                            btn.click()
                            time.sleep(0.3)
                            return
                except Exception:
                    continue

            # ESC 关闭
            from selenium.webdriver.common.keys import Keys
            self.driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            time.sleep(0.3)
        except Exception:
            pass

    # ── 批量采集 ──────────────────────────────
    def crawl(self, companies: list):
        """批量采集公司数据"""
        self._crawling = True
        self._stop_requested = False
        self.results = []
        total = len(companies)

        if not self._logged_in:
            self._log("❌ 未登录，请先登录")
            if self.signals:
                self.signals.log_signal.emit("❌ 未登录，请先登录")
            return

        self._log(f"🚀 开始采集，共 {total} 家公司")

        for idx, name in enumerate(companies, 1):
            if self._stop_requested:
                self._log("⏹ 用户停止采集")
                break

            name = str(name).strip()
            if not name:
                continue

            self._log(f"[{idx}/{total}] 正在查询: {name}")

            if self.signals:
                self.signals.progress_signal.emit(idx, total)

            # 检查登录是否过期
            if idx % 20 == 0:
                if not self._check_login_status():
                    self._logged_in = False
                    self._log("⚠️ 登录已过期，请重新登录！")
                    if self.signals:
                        self.signals.login_status_signal.emit(False, "登录已过期")
                        self.signals.manual_check_signal.emit("登录已过期，请重新登录")
                    break

            # 搜索
            result = self.search_company(name)

            # 详情
            if result.get("搜索结果URL"):
                time.sleep(self.delay_between)
                result = self.fetch_detail(result)

            self.results.append(result)
            time.sleep(self.delay_between)

        self._crawling = False
        self._log(f" 采集完成！共 {len(self.results)} 条数据")
        if self.signals:
            self.signals.crawl_finished_signal.emit()

    def stop_crawl(self):
        self._stop_requested = True
        self._log("⏳ 正在停止...")

    # ── 导出 Excel ────────────────────────────
    def export_excel(self, filepath: str):
        """将采集结果导出为 Excel"""
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

            wb = Workbook()
            ws = wb.active
            ws.title = "企业数据"

            if not self.results:
                self._log("️ 没有数据可导出")
                return

            # 表头
            headers = [
                "序号", "公司名", "状态", "法定代表人", "注册资本",
                "成立日期", "统一社会信用代码", "电话", "全部电话",
                "邮箱", "全部邮箱", "官网", "地址", "曾用名",
                "简介", "自身风险", "关联风险", "提示信息", "负面舆情",
                "搜索结果URL",
            ]
            header_font = Font(bold=True, size=11, color="FFFFFF")
            header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
            header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
            thin_border = Border(
                left=Side(style="thin"), right=Side(style="thin"),
                top=Side(style="thin"), bottom=Side(style="thin"),
            )

            for col, h in enumerate(headers, 1):
                cell = ws.cell(row=1, column=col, value=h)
                cell.font = header_font
                cell.fill = header_fill
                cell.alignment = header_align
                cell.border = thin_border

            # 数据行
            data_align = Alignment(vertical="top", wrap_text=True)
            for row_idx, r in enumerate(self.results, 2):
                ws.cell(row=row_idx, column=1, value=row_idx - 1).alignment = Alignment(horizontal="center")
                col_idx = 2
                for h in headers[1:]:
                    val = r.get(h, "")
                    cell = ws.cell(row=row_idx, column=col_idx, value=val)
                    cell.alignment = data_align
                    cell.border = thin_border
                    col_idx += 1

            # 自动列宽
            for col in ws.columns:
                max_len = 0
                col_letter = col[0].column_letter
                for cell in col:
                    if cell.value:
                        max_len = max(max_len, len(str(cell.value)))
                ws.column_dimensions[col_letter].width = min(max_len + 4, 50)

            # 冻结首行 + 自动筛选
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions

            wb.save(filepath)
            self._log(f"📊 数据已导出到: {filepath}")

        except Exception as e:
            self._log(f"❌ 导出 Excel 失败: {e}")
            traceback.print_exc()
