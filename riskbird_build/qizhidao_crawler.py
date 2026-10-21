"""
qizhidao_crawler.py - 企知道 (qizhidao.com) 企业数据爬虫模块
使用 Selenium 控制 Chrome 浏览器，实现搜索、数据采集
"""

import json
import os
import sys
import time
import re
import subprocess
import threading
import traceback
import urllib.parse
from datetime import datetime
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    NoSuchElementException, TimeoutException, WebDriverException
)

try:
    from PyQt5.QtCore import QObject, pyqtSignal
    HAS_PYQT = True
except ImportError:
    HAS_PYQT = False


# ──────────────────────────────────────────────
#  Chrome 检测与自动安装
# ─────────────────────────────────────────────
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
    try:
        url = "https://dl.google.com/chrome/install/latest/chrome_installer.exe"
        installer = os.path.join(os.environ.get("TEMP", "/tmp"), "chrome_installer.exe")
        if callback:
            callback("正在下载 Google Chrome...")
        ps_script = f"""
$ProgressPreference = 'SilentlyContinue'
Invoke-WebRequest -Uri '{url}' -OutFile '{installer}'
Start-Process -FilePath '{installer}' -Args '/silent /install' -Wait
Remove-Item '{installer}' -Force
"""
        subprocess.run(["powershell", "-Command", ps_script], check=True, timeout=120)
        if callback:
            callback("Chrome 安装成功！")
        return True
    except Exception as e:
        if callback:
            callback(f"Chrome 安装失败：{e}")
        return False


# ─────────────────────────────────────────────
#  信号类
# ─────────────────────────────────────────────
if HAS_PYQT:
    class CrawlerSignals(QObject):
        log_signal = pyqtSignal(str)
        progress_signal = pyqtSignal(int, int)
        crawl_finished_signal = pyqtSignal()
else:
    class CrawlerSignals:
        pass


# ─────────────────────────────────────────────
#  爬虫主类
# ─────────────────────────────────────────────
class QiZhiDaoCrawler:
    BASE_URL = "https://www.qizhidao.com"
    SEARCH_URL = f"{BASE_URL}/check"

    def __init__(self, gui_callback=None):
        self.driver = None
        self.wait = None
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

        self.delay_between = 0.5
        self.max_retries = 2

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        full_msg = f"[{ts}] {msg}"
        if self.gui_callback:
            self.gui_callback(full_msg)
        if self.signals:
            self.signals.log_signal.emit(full_msg)
        print(full_msg)

    def start_browser(self, headed: bool = True):
        with self._browser_lock:
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

    def search_company(self, name: str) -> dict:
        """搜索公司并返回第一条结果的基本信息"""
        result = {
            "公司名": name,
            "法定代表人": "",
            "注册资本": "",
            "成立日期": "",
            "统一社会信用代码": "",
            "电话": "",
            "邮箱": "",
            "官网": "",
            "地址": "",
            "经营状态": "",
            "搜索结果 URL": "",
        }
        try:
            if self.driver is None:
                self.start_browser(headed=True)

            # 使用企知道搜索 URL
            search_url = f"{self.SEARCH_URL}?searchKey={urllib.parse.quote(name)}&tagNum=1&scPageTitle=PC 站首页&searchModeType=8&fromRoutePage=index&businessSourceDzlQuery="
            self.driver.get(search_url)
            time.sleep(2)

            # 等待搜索结果加载
            time.sleep(1)

            # 获取第一条结果
            cards = []
            card_selectors = [
                "//div[contains(@class,'company-item')]",
                "//div[contains(@class,'result-item')]",
                "//div[contains(@class,'search-result')]",
                "//li[contains(@class,'company')]",
                "//div[contains(@class,'card')]",
            ]
            for sel in card_selectors:
                try:
                    cards = self.driver.find_elements(By.XPATH, sel)
                    if cards:
                        break
                except Exception:
                    continue

            if not cards:
                self._log(f"   未找到 '{name}' 的搜索结果")
                result["经营状态"] = "未找到"
                return result

            card = cards[0]
            card_text = card.text

            # 提取公司名
            try:
                name_el = card.find_element(
                    By.XPATH, ".//a[contains(@class,'name')] | .//h3 | .//h4 | .//div[contains(@class,'title')]//a | .//a[1]"
                )
                result["公司名"] = name_el.text.strip()
                href = name_el.get_attribute("href") or ""
                if href:
                    result["搜索结果 URL"] = href
            except NoSuchElementException:
                pass

            # 提取各项信息
            # 法定代表人
            m = re.search(r"法定代表人 [：:\s]*(\S+)", card_text)
            if m:
                result["法定代表人"] = m.group(1).strip()

            # 注册资本
            m = re.search(r"注册资本 [：:\s]*([\d.]+\s*万\S*)", card_text)
            if m:
                result["注册资本"] = m.group(1).strip()

            # 成立日期
            m = re.search(r"成立日期 [：:\s]*(\d{4}[-/]\d{1,2}[-/]\d{1,2})", card_text)
            if m:
                result["成立日期"] = m.group(1).strip()

            # 信用代码
            m = re.search(r"(?:统一社会信用代码 | 信用代码)[：:\s]*([A-Z0-9]{18})", card_text)
            if m:
                result["统一社会信用代码"] = m.group(1).strip()

            # 电话
            m = re.search(r"电话 [：:\s]*([\d\-]+)", card_text)
            if m:
                phone = m.group(1).strip()
                phone = re.sub(r'[更多\s]+$', '', phone)
                result["电话"] = phone

            # 邮箱
            m = re.search(r"邮箱 [：:\s]*([\w.+-]+@[\w.-]+)", card_text)
            if m:
                email = m.group(1).strip()
                email = re.sub(r'[更多\s]+$', '', email)
                result["邮箱"] = email

            # 官网
            m = re.search(r"官网 [：:\s]*(https?://\S+)", card_text)
            if m:
                result["官网"] = m.group(1).strip().rstrip("，。,.")

            # 地址
            m = re.search(r"(?:通信地址 | 地址)[：:\s]*(.+?)(?:电话 | 邮箱 | 官网|$)", card_text, re.DOTALL)
            if m:
                result["地址"] = m.group(1).strip()[:100]

            # 经营状态
            status_patterns = ["在营", "存续", "在业", "注销", "吊销", "迁出"]
            for s in status_patterns:
                if s in card_text:
                    result["经营状态"] = s
                    break

            self._log(f"  ✅ 搜索到：{result['公司名']}")

        except TimeoutException:
            self._log(f"   搜索 '{name}' 超时")
            result["经营状态"] = "搜索超时"
        except Exception as e:
            self._log(f"   搜索 '{name}' 异常：{e}")
            result["经营状态"] = f"异常：{str(e)[:50]}"

        return result

    def crawl(self, companies: list, start_index: int = 0):
        """批量采集公司数据，支持从指定位置恢复"""
        self._crawling = True
        self._stop_requested = False
        self._pause_requested = False
        total = len(companies)

        self._log(f"🚀 开始采集，共 {total} 家公司，从第 {start_index + 1} 条开始")

        for idx in range(start_index, total):
            if self._stop_requested:
                self._log(f" 用户停止采集，已采集 {len(self.results)} 条")
                break

            # 暂停检查
            while self._pause_requested:
                time.sleep(0.5)
                if self._stop_requested:
                    break

            name = str(companies[idx]).strip()
            if not name:
                continue

            self._log(f"[{idx + 1}/{total}] 正在查询：{name}")

            if self.signals:
                self.signals.progress_signal.emit(idx + 1, total)

            # 搜索
            result = self.search_company(name)
            self.results.append(result)
            time.sleep(self.delay_between)

        self._crawling = False
        self._log(f"✅ 采集完成！共 {len(self.results)} 条数据")
        if self.signals:
            self.signals.crawl_finished_signal.emit()

    def stop_crawl(self):
        self._stop_requested = True
        self._log(" 正在停止...")

    def pause_crawl(self):
        """暂停采集"""
        self._pause_requested = True
        self._log(" 采集已暂停")

    def resume_crawl(self):
        """恢复采集"""
        self._pause_requested = False
        self._log("▶ 采集已恢复")

    def export_excel(self, filepath: str):
        """将采集结果导出为 Excel"""
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

            wb = Workbook()
            ws = wb.active
            ws.title = "企业数据"

            if not self.results:
                self._log("⚠️ 没有数据可导出")
                return

            # 表头
            headers = ["公司名", "法定代表人", "注册资本", "成立日期", "统一社会信用代码",
                       "电话", "邮箱", "官网", "地址", "经营状态", "搜索结果 URL"]
            header_font = Font(bold=True, color="FFFFFF", size=11)
            header_fill = PatternFill(start_color="1a3c6e", end_color="1a3c6e", fill_type="solid")
            header_align = Alignment(horizontal="center", vertical="center")

            for col, header in enumerate(headers, 1):
                cell = ws.cell(row=1, column=col, value=header)
                cell.font = header_font
                cell.fill = header_fill
                cell.alignment = header_align

            # 数据
            for row_idx, result in enumerate(self.results, 2):
                for col_idx, key in enumerate(headers, 1):
                    value = result.get(key, "")
                    cell = ws.cell(row=row_idx, column=col_idx, value=value)
                    cell.alignment = Alignment(vertical="center", wrap_text=True)

            # 列宽
            col_widths = [30, 12, 15, 12, 22, 15, 25, 25, 40, 10, 30]
            for i, width in enumerate(col_widths, 1):
                ws.column_dimensions[chr(64 + i) if i <= 26 else 'A'].width = width

            wb.save(filepath)
            self._log(f"✅ 已导出 {len(self.results)} 条数据到：{filepath}")

        except Exception as e:
            self._log(f"❌ 导出失败：{e}")
