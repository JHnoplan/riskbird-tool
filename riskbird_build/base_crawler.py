"""
base_crawler.py - 爬虫基类
所有平台爬虫的公共逻辑
"""

import time
import threading
from pathlib import Path
from abc import ABC, abstractmethod

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException

try:
    from PyQt5.QtCore import QObject, pyqtSignal
    HAS_PYQT = True
except ImportError:
    HAS_PYQT = False


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


class BaseCrawler(ABC):
    """爬虫基类，定义通用接口"""
    
    BASE_URL = ""
    PLATFORM_NAME = ""
    
    def __init__(self):
        self.driver = None
        self.wait = None
        self._logged_in = False
        self._crawling = False
        self._stop_requested = False
        self._pause_requested = False
        self.results = []
        self.signals = CrawlerSignals() if HAS_PYQT else None
        self.delay_between = 0.5
        self.max_retries = 2
        
        # 数据目录
        self._data_dir = Path(__file__).parent / "riskbird_data"
        self._data_dir.mkdir(exist_ok=True)
        self._cookie_file = self._data_dir / f"{self.PLATFORM_NAME}_cookies.json"
    
    def _log(self, msg: str):
        """输出日志"""
        print(msg)
        if self.signals:
            self.signals.log_signal.emit(msg)
    
    def start_browser(self, headed: bool = True):
        """启动 Chrome 浏览器"""
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
            {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"}
        )
        self.wait = WebDriverWait(self.driver, 10)
        self._log(f"[{self.PLATFORM_NAME}] 浏览器已启动")
    
    def close_browser(self):
        """关闭浏览器"""
        if self.driver:
            self.driver.quit()
            self.driver = None
            self._log(f"[{self.PLATFORM_NAME}] 浏览器已关闭")
    
    @abstractmethod
    def login_manual(self) -> bool:
        """手动登录（子类实现）"""
        pass
    
    @abstractmethod
    def search_company(self, name: str) -> dict:
        """搜索公司（子类实现）"""
        pass
    
    def crawl(self, companies: list, start_index: int = 0):
        """批量采集"""
        self._crawling = True
        self._stop_requested = False
        self._pause_requested = False
        total = len(companies)
        
        self._log(f"[{self.PLATFORM_NAME}] 🚀 开始采集，共 {total} 家公司，从第 {start_index + 1} 条开始")
        
        for idx in range(start_index, total):
            if self._stop_requested:
                self._log(f"[{self.PLATFORM_NAME}] ⏹ 用户停止采集，已采集 {len(self.results)} 条")
                break
            
            # 暂停检查
            while self._pause_requested:
                time.sleep(0.5)
                if self._stop_requested:
                    break
            
            name = str(companies[idx]).strip()
            if not name:
                continue
            
            self._log(f"[{self.PLATFORM_NAME}] [{idx + 1}/{total}] 正在查询：{name}")
            
            if self.signals:
                self.signals.progress_signal.emit(idx + 1, total)
            
            result = self.search_company(name)
            self.results.append(result)
            time.sleep(self.delay_between)
        
        self._crawling = False
        self._log(f"[{self.PLATFORM_NAME}] ✅ 采集完成！共 {len(self.results)} 条")
        
        if self.signals:
            self.signals.crawl_finished_signal.emit()
    
    def export_excel(self, output_path: str) -> bool:
        """导出 Excel"""
        if not self.results:
            self._log(f"[{self.PLATFORM_NAME}] 没有数据可导出")
            return False
        
        try:
            from openpyxl import Workbook
            wb = Workbook()
            ws = wb.active
            ws.title = "采集结果"
            
            # 表头
            headers = list(self.results[0].keys())
            ws.append(headers)
            
            # 数据
            for row in self.results:
                ws.append([row.get(h, "") for h in headers])
            
            wb.save(output_path)
            self._log(f"[{self.PLATFORM_NAME}] ✅ 已导出到：{output_path}")
            return True
        except Exception as e:
            self._log(f"[{self.PLATFORM_NAME}] ❌ 导出失败：{e}")
            return False
    
    def stop_crawl(self):
        """停止采集"""
        self._stop_requested = True
        self._log(f"[{self.PLATFORM_NAME}] 正在停止...")
    
    def pause_crawl(self):
        """暂停采集"""
        self._pause_requested = True
        self._log(f"[{self.PLATFORM_NAME}] 已暂停")
    
    def resume_crawl(self):
        """恢复采集"""
        self._pause_requested = False
        self._log(f"[{self.PLATFORM_NAME}] 已恢复")
    
    def safe_find_element(self, xpath: str, timeout: int = 5):
        """安全查找元素（找不到返回 None）"""
        try:
            element = WebDriverWait(self.driver, timeout).until(
                EC.presence_of_element_located((By.XPATH, xpath))
            )
            if element.is_displayed():
                return element
        except:
            pass
        return None
    
    def safe_find_elements(self, xpath: str, timeout: int = 5) -> list:
        """安全查找多个元素"""
        try:
            WebDriverWait(self.driver, timeout).until(
                EC.presence_of_element_located((By.XPATH, xpath))
            )
            elements = self.driver.find_elements(By.XPATH, xpath)
            return [el for el in elements if el.is_displayed()]
        except:
            return []
    
    def get_text(self, xpath: str, default: str = "") -> str:
        """获取元素文本"""
        el = self.safe_find_element(xpath)
        return el.text.strip() if el else default
    
    def click_element(self, xpath: str, timeout: int = 5) -> bool:
        """点击元素"""
        el = self.safe_find_element(xpath, timeout)
        if el:
            el.click()
            return True
        return False
