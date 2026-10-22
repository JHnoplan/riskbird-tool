"""
aiqicha_crawler_v2.py - 爱企查爬虫 v2（基于 CDP 直接通信）
使用 Chrome DevTools Protocol 直接通信，无需 Selenium
"""

import json
import time
import re
import threading
import subprocess
import sys
import os
import requests
import shutil
from datetime import datetime
from pathlib import Path

try:
    import websocket
    HAS_WEBSOCKET = True
except ImportError:
    HAS_WEBSOCKET = False

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


class AiQiChaCrawlerV2:
    """爱企查爬虫 - 基于 CDP 直接通信"""
    
    BASE_URL = "https://aiqicha.baidu.com"
    SEARCH_URL = "https://aiqicha.baidu.com/s?q={}"
    DETAIL_URL = "https://aiqicha.baidu.com/company_detail_{}"
    
    def __init__(self, gui_callback=None, debug_port=9233):
        self.gui_callback = gui_callback
        self.debug_port = debug_port
        self.base_url = f"http://127.0.0.1:{debug_port}"
        
        self._logged_in = False
        self._crawling = False
        self._stop_requested = False
        self._pause_requested = False
        self.results = []
        self._browser_lock = threading.Lock()
        
        # 兼容性属性（GUI 期望这些属性存在）
        self.driver = None  # CDP 模式不使用 Selenium WebDriver
        
        if HAS_PYQT:
            self.signals = CrawlerSignals()
        else:
            self.signals = None
        
        # Cookie 持久化
        self._data_dir = Path(__file__).parent / "riskbird_data"
        self._data_dir.mkdir(exist_ok=True)
        self._cookie_file = self._data_dir / "aiqicha_cookies.json"
        
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
    
    # ── CDP 通信 ──
    def _get_tabs(self):
        """获取所有标签页"""
        resp = requests.get(f"{self.base_url}/json/list", timeout=5)
        return resp.json()
    
    def _get_ws_url(self, url_pattern=None):
        """获取 WebSocket URL"""
        tabs = self._get_tabs()
        if url_pattern:
            for tab in tabs:
                if url_pattern in tab.get("url", ""):
                    return tab.get("webSocketDebuggerUrl")
        if tabs:
            return tabs[0].get("webSocketDebuggerUrl")
        return None
    
    def _send_command(self, ws_url, method, params=None):
        """发送 CDP 命令"""
        ws = websocket.create_connection(ws_url, timeout=15)
        cmd = {"id": 1, "method": method, "params": params or {}}
        ws.send(json.dumps(cmd))
        result = json.loads(ws.recv())
        ws.close()
        return result
    
    def _execute_js(self, ws_url, script):
        """执行 JavaScript（必须使用 IIFE 模式）"""
        result = self._send_command(ws_url, "Runtime.evaluate", {
            "expression": script,
            "returnByValue": True,
            "awaitPromise": True
        })
        if "error" in result:
            return None
        return result.get("result", {}).get("result", {}).get("value")
    
    def _create_tab(self, url):
        """创建新标签页并导航（或复用现有标签页）"""
        # 检查是否是详情页 URL
        is_detail = "company_detail" in url
            
        if not is_detail:
            # 首页：复用已有的爱企查首页标签页
            tabs = self._get_tabs()
            for tab in tabs:
                tab_url = tab.get("url", "")
                if "aiqicha.baidu.com" in tab_url and "/s?" not in tab_url and "company_detail" not in tab_url:
                    self._log(f"复用现有爱企查标签页")
                    return tab.get("webSocketDebuggerUrl")
                
            # 没有现有标签页，创建新标签页并导航到爱企查
            self._log("正在打开爱企查...")
            result = self._send_command(
                self._get_ws_url(),
                "Target.createTarget",
                {"url": self.BASE_URL}
            )
            target_id = result.get("result", {}).get("targetId")
            if not target_id:
                return None
                
            time.sleep(5)
            tabs = self._get_tabs()
            for tab in tabs:
                if target_id in tab.get("id", ""):
                    return tab.get("webSocketDebuggerUrl")
            return None
            
        # 详情页：创建新标签页
        result = self._send_command(
            self._get_ws_url(),
            "Target.createTarget",
            {"url": url}
        )
        target_id = result.get("result", {}).get("targetId")
        if not target_id:
            return None
            
        # 获取新标签页的 WebSocket URL
        time.sleep(3)
        tabs = self._get_tabs()
        for tab in tabs:
            if target_id in tab.get("id", ""):
                return tab.get("webSocketDebuggerUrl")
        return None
    
    def _close_tab(self, ws_url):
        """关闭标签页"""
        try:
            self._send_command(ws_url, "Target.closeTarget", {})
        except Exception:
            pass
    
    def _input_text(self, ws_url, text):
        """输入文本（模拟键盘输入以触发 Vue 事件）"""
        for char in text:
            # 发送 keyDown
            self._send_command(ws_url, "Input.dispatchKeyEvent", {
                "type": "keyDown",
                "text": char,
                "key": char,
                "code": "Key" + char.upper() if char.isalpha() else char,
                "windowsVirtualKeyCode": ord(char.upper()) if char.isalpha() else ord(char),
                "nativeVirtualKeyCode": ord(char.upper()) if char.isalpha() else ord(char),
            })
            # 发送 keyUp
            self._send_command(ws_url, "Input.dispatchKeyEvent", {
                "type": "keyUp",
                "key": char,
                "code": "Key" + char.upper() if char.isalpha() else char,
                "windowsVirtualKeyCode": ord(char.upper()) if char.isalpha() else ord(char),
                "nativeVirtualKeyCode": ord(char.upper()) if char.isalpha() else ord(char),
            })
            time.sleep(0.05)  # 每个字符间短暂延迟
    
    def _press_enter(self, ws_url):
        """按回车"""
        self._send_command(ws_url, "Input.dispatchKeyEvent", {
            "type": "keyDown", "key": "Enter", "code": "Enter",
            "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13
        })
        self._send_command(ws_url, "Input.dispatchKeyEvent", {
            "type": "keyUp", "key": "Enter", "code": "Enter",
            "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13
        })
    
    def _wait_for_navigation(self, ws_url, timeout=15):
        """等待页面导航完成"""
        start = time.time()
        while time.time() - start < timeout:
            try:
                # 检查页面是否加载完成
                ready = self._execute_js(ws_url, "(function() { return document.readyState; })()")
                if ready == "complete":
                    # 再等一会让 SPA 渲染
                    time.sleep(3)
                    return True
            except Exception:
                # WebSocket 可能已断开，尝试重新连接
                pass
            time.sleep(1)
        return False
    
    def _get_page_content(self, ws_url):
        """获取页面 body HTML"""
        return self._execute_js(ws_url, "(function() { return document.body.innerHTML; })()")
    
    # ── 浏览器管理 ──
    def _find_chrome(self):
        """查找 Chrome 可执行文件路径"""
        candidates = []
        if sys.platform == "win32":
            candidates = [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
            ]
        elif sys.platform == "darwin":
            candidates = [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            ]
        for path in candidates:
            if os.path.exists(path):
                return path
        return None
    
    def _kill_chrome(self):
        """关闭所有 Chrome 进程"""
        try:
            if sys.platform == "win32":
                subprocess.run(
                    [r"C:\Windows\System32\taskkill.exe", "/F", "/IM", "chrome.exe"],
                    capture_output=True, timeout=10
                )
            elif sys.platform == "darwin":
                subprocess.run(["pkill", "-9", "Google Chrome"], capture_output=True, timeout=10)
            time.sleep(2)  # 等待进程完全退出
        except Exception:
            pass
    
    def _get_temp_profile(self):
        """获取临时 Chrome 配置目录（只复制必要文件，跳过缓存加速）"""
        if sys.platform == "win32":
            real_profile = os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data")
            temp_profile = Path(os.environ.get("TEMP", r"C:\temp")) / "aiqicha_chrome_profile"
        else:
            real_profile = os.path.expanduser("~/Library/Application Support/Google/Chrome")
            temp_profile = Path("/tmp/aiqicha_chrome_profile")
        
        # 如果临时配置不存在或损坏，从真实配置复制
        cookies_file = temp_profile / "Default" / "Cookies"
        if not temp_profile.exists() or (cookies_file.exists() and cookies_file.stat().st_size < 1000):
            if os.path.exists(real_profile):
                self._log("正在准备 Chrome 配置（仅复制必要文件，跳过缓存）...")
                if temp_profile.exists():
                    shutil.rmtree(str(temp_profile), ignore_errors=True)
                
                # 只复制必要文件，跳过巨大的缓存目录
                skip_patterns = [
                    "SingletonLock", "SingletonSocket", "SingletonCookie",
                    "Cache", "Code Cache", "GPUCache", "Service Worker",
                    "DawnCache", "GrShaderCache", "ShaderCache",
                    "IndexedDB", "File System", "Platform Notifications",
                    "Network", "Origin Trials", "Trust Tokens",
                    "optimization_guide_model_store",
                ]
                shutil.copytree(
                    real_profile, str(temp_profile),
                    ignore=shutil.ignore_patterns(*skip_patterns)
                )
                self._log("Chrome 配置准备完成")
        
        return str(temp_profile)
    
    def start_browser(self, headed: bool = True):
        """启动浏览器（自动管理 Chrome 进程）"""
        with self._browser_lock:
            # 1. 先检查是否已有可用的调试端口
            try:
                tabs = self._get_tabs()
                if tabs:
                    self._log("Chrome 调试端口已连接")
                    return True
            except Exception:
                pass
            
            # 2. 关闭所有现有 Chrome 进程
            self._log("正在关闭已有 Chrome...")
            self._kill_chrome()
            
            # 3. 查找 Chrome
            chrome_path = self._find_chrome()
            if not chrome_path:
                self._log("❌ 未找到 Chrome，请先安装 Google Chrome")
                return False
            
            # 4. 准备临时配置目录
            temp_profile = self._get_temp_profile()
            
            # 5. 启动 Chrome（带调试端口）
            self._log("正在启动 Chrome...")
            cmd = [
                chrome_path,
                f"--remote-debugging-port={self.debug_port}",
                "--remote-allow-origins=*",
                f"--user-data-dir={temp_profile}",
                "--profile-directory=Default",
                "--no-first-run",
                "--no-default-browser-check",
            ]
            
            try:
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as e:
                self._log(f"启动 Chrome 失败：{e}")
                return False
            
            # 6. 等待调试端口就绪
            self._log("等待 Chrome 启动...")
            for i in range(15):
                time.sleep(1)
                try:
                    tabs = self._get_tabs()
                    if tabs:
                        self._log("Chrome 启动成功")
                        # 自动打开爱企查首页
                        self._log("正在打开爱企查...")
                        first_tab_ws = tabs[0].get("webSocketDebuggerUrl")
                        if first_tab_ws:
                            # 尝试导航到爱企查（最多重试 3 次）
                            nav_success = False
                            for retry in range(3):
                                try:
                                    self._execute_js(first_tab_ws, "(function() { window.location.href = 'https://aiqicha.baidu.com/'; })()")
                                    time.sleep(3)
                                    # 验证是否真的打开了爱企查
                                    current_url = self._execute_js(first_tab_ws, "(function() { return window.location.href; })()")
                                    if current_url and "aiqicha" in current_url:
                                        nav_success = True
                                        self._log("爱企查页面已打开")
                                        break
                                    else:
                                        self._log(f"导航未生效（第{retry+1}次），重试...")
                                except Exception:
                                    time.sleep(2)
                            
                            if not nav_success:
                                self._log("⚠ 爱企查页面未自动打开，请手动在浏览器中访问 aiqicha.baidu.com")
                        return True
                except Exception:
                    pass
            
            self._log("❌ Chrome 启动超时，请检查是否已安装 Google Chrome")
            return False
    
    def close_browser(self):
        """关闭浏览器（CDP 模式下不关闭，由用户手动关闭）"""
        pass
    
    def _is_session_alive(self) -> bool:
        """检查会话是否存活"""
        try:
            tabs = self._get_tabs()
            return len(tabs) > 0
        except Exception:
            return False
    
    def try_restore_session(self) -> bool:
        """恢复会话"""
        return self._is_session_alive()
    
    # ─ GUI 兼容性方法 ──
    def login_with_credentials(self, phone: str, password: str) -> bool:
        """使用账号密码登录（爱企查不支持，返回 False）"""
        self._log("爱企查请使用手动登录方式")
        return False
    
    def login_manual(self) -> bool:
        """手动登录（提示用户在浏览器中完成）"""
        ws_url = self._get_ws_url("aiqicha.baidu.com")
        if not ws_url:
            self._log("请先在 Chrome 中打开 aiqicha.baidu.com")
            return False
        
        self._log("请在浏览器中完成登录...")
        # 等待用户登录
        for i in range(60):  # 最多等待 60 秒
            time.sleep(1)
            if self._check_login_status():
                self._logged_in = True
                self._log("登录成功！")
                return True
        self._log("登录超时")
        return False
    
    def verify_manual_login(self) -> bool:
        """验证手动登录状态"""
        return self._check_login_status()
    
    # ── 登录 ──
    def _check_login_status(self) -> bool:
        """检查登录状态"""
        ws_url = self._get_ws_url("aiqicha.baidu.com")
        if not ws_url:
            return False
        
        # 检查是否有用户名显示
        result = self._execute_js(ws_url, """
            (function() {
                var el = document.querySelector('.user-name, .nickname, [class*="username"]');
                return el ? el.textContent.trim() : null;
            })()
        """)
        return result is not None and len(result) > 0
    
    def login_manual(self) -> bool:
        """手动登录"""
        ws_url = self._get_ws_url("aiqicha.baidu.com")
        if not ws_url:
            self._navigate_to_aiqicha()
            ws_url = self._get_ws_url("aiqicha.baidu.com")
        
        if ws_url:
            self._navigate(ws_url, "https://aiqicha.baidu.com")
            self._log("请在浏览器中完成登录")
            time.sleep(5)
            return self._check_login_status()
        return False
    
    def _navigate_to_aiqicha(self):
        """导航到爱企查"""
        tabs = self._get_tabs()
        if not tabs:
            self._log("没有可用的标签页")
            return
        
        ws_url = tabs[0].get("webSocketDebuggerUrl")
        self._navigate(ws_url, self.BASE_URL)
        time.sleep(5)
    
    # ── 搜索公司 ──
    def search_company(self, name: str) -> dict:
        """搜索公司并采集数据（全程只用 1 个标签页）"""
        result = {
            "公司名": name,
            "状态": "", "法定代表人": "", "注册资本": "",
            "成立日期": "", "统一社会信用代码": "",
            "电话": "", "邮箱": "", "官网": "", "地址": "",
            "简介": "", "经营范围": "", "所属行业": "",
            "企业类型": "", "营业期限": "", "登记机关": "",
            "核准日期": "", "参保人数": "", "数据来源": "爱企查",
            "搜索结果 URL": "",
        }
        
        for attempt in range(self.max_retries):
            try:
                # 1. 复用或创建爱企查首页标签页（全程只用这 1 个标签页）
                self._log(f"搜索：{name}")
                ws_url = self._get_or_create_homepage_tab()
                if not ws_url:
                    self._log("无法获取首页标签页")
                    result["状态"] = "浏览器错误"
                    return result
                
                # 2. 等待页面加载（智能等待：检查搜索框和 Vue 组件是否就绪）
                for i in range(8):  # 最多等 8 秒
                    page_ready = self._execute_js(ws_url, """
                        (function() {
                            var input = document.querySelector('#aqc-search-input') || document.querySelector('#aqc-header-search-input');
                            if (!input) return 'loading';
                            // 检查 Vue 是否已挂载（检查是否有 iView 组件）
                            var vueApp = document.querySelector('#app') || document.querySelector('[data-v-]');
                            return (input && vueApp) ? 'ready' : 'loading';
                        })()
                    """)
                    if page_ready == 'ready':
                        break
                    time.sleep(1)
                
                # 检查页面状态
                page_check = self._check_page_status(ws_url)
                if page_check == 'blocked':
                    self._log("️ 检测到反爬虫，尝试刷新...")
                    self._execute_js(ws_url, "(function() { location.reload(); })()")
                    time.sleep(5)
                
                # 3. 清空并输入公司名
                self._clear_and_input_search(ws_url, name)
                
                # 4. 等待自动补全出现（智能等待：最多 3 秒）
                autocomplete_found = False
                for i in range(6):  # 6 * 0.5 = 3 秒
                    time.sleep(0.5)
                    check = self._execute_js(ws_url, """
                        (function() {
                            var items = document.querySelectorAll('.ivu-select-item, .sug-company-line');
                            return items.length > 0 ? 'found' : 'waiting';
                        })()
                    """)
                    if check == 'found':
                        autocomplete_found = True
                        break
                
                if not autocomplete_found:
                    self._log(f"未找到自动补全：{name}")
                    result["状态"] = "未找到"
                    return result
                
                # 5. 点击自动补全第一项（会直接跳转到详情页）
                self._click_first_autocomplete(ws_url)
                
                # 6. 等待跳转到详情页（智能等待：检查 URL 变化）
                self._log("等待跳转到详情页...")
                detail_loaded = False
                for i in range(10):  # 10 * 0.5 = 5 秒
                    time.sleep(0.5)
                    current_url = self._execute_js(ws_url, "(function() { return window.location.href; })()")
                    if current_url and 'company_detail' in current_url:
                        detail_loaded = True
                        company_id_match = re.search(r'company_detail_(\d+)', current_url)
                        if company_id_match:
                            result["搜索结果 URL"] = f"company_detail_{company_id_match.group(1)}"
                        break
                
                if not detail_loaded:
                    self._log(f"未跳转到详情页")
                    result["状态"] = "跳转失败"
                    return result
                
                self._log(f"已跳转到详情页")
                
                # 7. 等待详情页数据渲染（智能等待：检查关键元素）
                for i in range(8):  # 8 * 0.5 = 4 秒
                    time.sleep(0.5)
                    data_check = self._execute_js(ws_url, """
                        (function() {
                            var items = document.querySelectorAll('.info-item');
                            return items.length > 0 ? 'ready' : 'loading';
                        })()
                    """)
                    if data_check == 'ready':
                        break
                
                # 额外等待 2 秒确保所有动态数据加载完成
                time.sleep(2)
                
                # 8. 提取详情页全量数据
                detail_data = self._extract_detail_data_full(ws_url, name)
                result.update(detail_data)
                result["状态"] = "成功"
                
                # 9. 导航回首页（关键！让下一个公司能复用这个标签页）
                self._log("返回首页...")
                self._navigate_to_homepage(ws_url)
                time.sleep(5)  # 增加等待时间，确保页面完全重新加载
                
                self._log(f"采集完成：{name}")
                return result
                
            except Exception as e:
                self._log(f"采集失败（第{attempt+1}次）：{e}")
                if attempt < self.max_retries - 1:
                    time.sleep(2)
        
        result["状态"] = "失败"
        return result
    
    def _get_or_create_homepage_tab(self):
        """获取或创建爱企查首页标签页（复用现有，避免重复打开）"""
        tabs = self._get_tabs()
        
        # 查找已有的爱企查首页标签页
        for tab in tabs:
            tab_url = tab.get("url", "")
            if "aiqicha.baidu.com" in tab_url and "/s?" not in tab_url and "company_detail" not in tab_url:
                self._log("复用现有爱企查首页标签页")
                return tab.get("webSocketDebuggerUrl")
        
        # 没有现有标签页，创建新标签页
        self._log("创建新的爱企查首页标签页")
        result = self._send_command(
            self._get_ws_url(),
            "Target.createTarget",
            {"url": self.BASE_URL}
        )
        target_id = result.get("result", {}).get("targetId")
        if not target_id:
            return None
        
        time.sleep(5)
        tabs = self._get_tabs()
        for tab in tabs:
            if target_id in tab.get("id", ""):
                return tab.get("webSocketDebuggerUrl")
        return None
    
    def _check_page_status(self, ws_url):
        """检查页面状态"""
        page_body = self._execute_js(ws_url, "(function() { return document.body ? document.body.innerText : ''; })()")
        if page_body and '请关闭浏览器的调试窗口' in page_body:
            return 'blocked'
        return 'ok'
    
    def _clear_and_input_search(self, ws_url, name):
        """清空搜索框并输入公司名"""
        # 点击聚焦并清空
        self._execute_js(ws_url, """
            (function() {
                var input = document.querySelector('#aqc-search-input') || document.querySelector('#aqc-header-search-input');
                if (input) {
                    input.click();
                    input.focus();
                    input.value = '';
                }
            })()
        """)
        time.sleep(0.5)
        
        # 输入公司名（触发 Vue 事件）
        self._execute_js(ws_url, """
            (function() {
                var input = document.querySelector('#aqc-search-input') || document.querySelector('#aqc-header-search-input');
                if (input) {
                    var nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                    nativeInputValueSetter.call(input, '""" + name + """');
                    input.dispatchEvent(new InputEvent('input', {
                        bubbles: true, cancelable: true,
                        inputType: 'insertText', data: '""" + name + """'
                    }));
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                }
            })()
        """)
    
    def _click_first_autocomplete(self, ws_url):
        """点击自动补全第一项，返回是否成功"""
        result = self._execute_js(ws_url, """
            (function() {
                var items = document.querySelectorAll('.ivu-select-item, .sug-company-line');
                if (items.length > 0) {
                    items[0].click();
                    return 'clicked';
                }
                return null;
            })()
        """)
        return result is not None
    
    def _navigate_to_homepage(self, ws_url):
        """导航回爱企查首页"""
        self._execute_js(ws_url, "(function() { window.location.href = 'https://aiqicha.baidu.com/'; })()")
    
    def _extract_detail_data_full(self, ws_url, company_name: str) -> dict:
        """从详情页提取全量数据"""
        data = {
            "法定代表人": "", "注册资本": "", "成立日期": "",
            "统一社会信用代码": "", "电话": "", "邮箱": "",
            "官网": "", "地址": "", "简介": "", "经营范围": "",
            "所属行业": "", "企业类型": "", "营业期限": "",
            "登记机关": "", "核准日期": "", "参保人数": "",
        }
            
        # 提取基本信息（使用实际 DOM 结构）
        info_json = self._execute_js(ws_url, """
            (function() {
                var result = {
                    legalPerson: '', registeredCapital: '', establishDate: '',
                    creditCode: '', phone: '', email: '', website: '', address: '',
                    introduction: '', businessScope: '', industry: '',
                    companyType: '', businessTerm: '', registrationAuthority: '',
                    approvalDate: '', insuredCount: ''
                };
                
                // 尝试从页面的 JSON 数据中提取（更完整）
                try {
                    if (window.__INITIAL_STATE__ || window.__NUXT__ || window.__NEXT_DATA__) {
                        var pageData = window.__INITIAL_STATE__ || window.__NUXT__ || window.__NEXT_DATA__;
                        var dataStr = JSON.stringify(pageData);
                        
                        // 从 JSON 数据中提取
                        var match;
                        if (match = dataStr.match(/"legalPerson"[\\s:]*"([^"]+)"/)) result.legalPerson = match[1];
                        if (match = dataStr.match(/"registeredCapital"[\\s:]*"([^"]+)"/)) result.registeredCapital = match[1];
                        if (match = dataStr.match(/"establishDate"[\\s:]*"([^"]+)"/)) result.establishDate = match[1];
                        if (match = dataStr.match(/"creditCode"[\\s:]*"([^"]+)"/)) result.creditCode = match[1];
                        if (match = dataStr.match(/"phone"[\\s:]*"([^"]+)"/)) result.phone = match[1];
                        if (match = dataStr.match(/"email"[\\s:]*"([^"]+)"/)) result.email = match[1];
                        if (match = dataStr.match(/"website"[\\s:]*"([^"]+)"/)) result.website = match[1];
                        if (match = dataStr.match(/"address"[\\s:]*"([^"]+)"/)) result.address = match[1];
                        if (match = dataStr.match(/"introduction"[\\s:]*"([^"]+)"/)) result.introduction = match[1];
                        if (match = dataStr.match(/"businessScope"[\\s:]*"([^"]+)"/)) result.businessScope = match[1];
                        if (match = dataStr.match(/"industry"[\\s:]*"([^"]+)"/)) result.industry = match[1];
                        if (match = dataStr.match(/"companyType"[\\s:]*"([^"]+)"/)) result.companyType = match[1];
                        if (match = dataStr.match(/"businessTerm"[\\s:]*"([^"]+)"/)) result.businessTerm = match[1];
                        if (match = dataStr.match(/"registrationAuthority"[\\s:]*"([^"]+)"/)) result.registrationAuthority = match[1];
                        if (match = dataStr.match(/"approvalDate"[\\s:]*"([^"]+)"/)) result.approvalDate = match[1];
                        if (match = dataStr.match(/"insuredCount"[\\s:]*"([^"]+)"/)) result.insuredCount = match[1];
                    }
                } catch(e) {}
                
                // 获取整个页面文本
                var allText = document.body.innerText;
                var lines = allText.split('\\n');
                
                // 从 .info-item 元素中提取基础字段
                var items = document.querySelectorAll('.info-item');
                for (var i = 0; i < items.length; i++) {
                    var text = items[i].textContent.trim();
                        
                    // 统一社会信用代码
                    if (!result.creditCode && text.indexOf('统一社会信用代码') >= 0) {
                        var match = text.match(/统一社会信用代码[：:]\\s*(\\S+)/);
                        if (match) result.creditCode = match[1];
                    }
                    // 电话
                    if (!result.phone && (text.indexOf('电话') >= 0 || text.indexOf('手机') >= 0)) {
                        var match = text.match(/(?:电话|手机|联系方式)[：:]\\s*(\\d[\\d\\-,\\s]+)/);
                        if (match) {
                            result.phone = match[1].trim();
                        } else {
                            var match2 = text.match(/(\\d{3,4}[-]?\\d{7,8}|\\d{11})/);
                            if (match2) result.phone = match2[1];
                        }
                    }
                    // 法定代表人
                    if (!result.legalPerson && text.indexOf('法定代表人') >= 0) {
                        var match = text.match(/法定代表人[：:]\\s*[\\u4e00-\\u9fa5][\\s\\n]+([\\u4e00-\\u9fa5]{2,4})/);
                        if (match) {
                            result.legalPerson = match[1];
                        } else {
                            var match2 = text.match(/法定代表人[：:]\\s*([\\u4e00-\\u9fa5]{2,4})/);
                            if (match2) result.legalPerson = match2[1];
                        }
                    }
                    // 邮箱
                    if (!result.email && text.indexOf('邮箱') >= 0) {
                        var match = text.match(/邮箱[：:]\\s*([\\w@.]+)/);
                        if (match) result.email = match[1];
                    }
                    // 注册资本
                    if (!result.registeredCapital && text.indexOf('注册资本') >= 0) {
                        var match = text.match(/注册资本[：:]\\s*(.+?)(?:\\s|$)/);
                        if (match) result.registeredCapital = match[1].trim();
                    }
                    // 网址
                    if (!result.website && text.indexOf('网址') >= 0) {
                        var match = text.match(/网址[：:]\\s*(http\\S+)/);
                        if (match) result.website = match[1];
                    }
                    // 地址
                    if (!result.address && text.indexOf('地址') >= 0 && text.length > 10) {
                        var match = text.match(/地址[：:]\\s*([\\u4e00-\\u9fa5].+)/);
                        if (match) result.address = match[1].trim().substring(0, 100);
                    }
                    // 成立日期/注册时间
                    if (!result.establishDate && (text.indexOf('注册时间') >= 0 || text.indexOf('成立日期') >= 0)) {
                        var match = text.match(/(\\d{4}-\\d{2}-\\d{2})/);
                        if (match) result.establishDate = match[1];
                    }
                }
                
                // 从整个页面文本中提取新字段（处理 tab 分隔格式）
                for (var i = 0; i < lines.length; i++) {
                    var line = lines[i].trim();
                    
                    // 按 tab 分割处理（爱企查用 tab 分隔字段）
                    if (line.indexOf('\\t') >= 0) {
                        var parts = line.split('\\t');
                        for (var j = 0; j < parts.length; j++) {
                            var part = parts[j].trim();
                            
                            // 企业类型
                            if (!result.companyType && part.indexOf('企业类型') >= 0) {
                                var match = part.match(/企业类型[\\s：:]*(.+)/);
                                if (match) result.companyType = match[1].trim().substring(0, 50);
                            }
                            // 所属行业
                            if (!result.industry && part.indexOf('所属行业') >= 0) {
                                var match = part.match(/所属行业[\\s：:]*(.+)/);
                                if (match) result.industry = match[1].trim().substring(0, 50);
                            }
                            // 营业期限
                            if (!result.businessTerm && part.indexOf('营业期限') >= 0) {
                                var match = part.match(/营业期限[\\s：:]*(.+)/);
                                if (match) result.businessTerm = match[1].trim().substring(0, 50);
                            }
                            // 核准日期
                            if (!result.approvalDate && part.indexOf('核准日期') >= 0) {
                                var match = part.match(/核准日期[\\s：:]*(\\d{4}-\\d{2}-\\d{2}|\\*{4}-\\*{2}-\\*{2})/);
                                if (match) result.approvalDate = match[1];
                            }
                            // 参保人数
                            if (!result.insuredCount && part.indexOf('参保人数') >= 0) {
                                var match = part.match(/参保人数[\\s：:]*(\\d+)/);
                                if (match) result.insuredCount = match[1];
                            }
                            // 登记机关
                            if (!result.registrationAuthority && part.indexOf('登记机关') >= 0) {
                                var match = part.match(/登记机关[\\s：:]*(.+)/);
                                if (match) result.registrationAuthority = match[1].trim().substring(0, 50);
                            }
                        }
                    }
                    
                    // 处理非 tab 分隔的行
                    if (!result.industry && line.indexOf('所属行业') >= 0) {
                        var match = line.match(/所属行业[\\s：:]*(.+)/);
                        if (match) result.industry = match[1].trim().substring(0, 50);
                    }
                    if (!result.companyType && (line.indexOf('企业类型') >= 0 || line.indexOf('公司类型') >= 0)) {
                        var match = line.match(/(?:企业类型|公司类型)[\\s：:]*(.+)/);
                        if (match) result.companyType = match[1].trim().substring(0, 50);
                    }
                    if (!result.businessTerm && line.indexOf('营业期限') >= 0) {
                        var match = line.match(/营业期限[\\s：:]*(.+)/);
                        if (match) result.businessTerm = match[1].trim().substring(0, 50);
                    }
                    if (!result.registrationAuthority && line.indexOf('登记机关') >= 0) {
                        var match = line.match(/登记机关[\\s：:]*(.+)/);
                        if (match) result.registrationAuthority = match[1].trim().substring(0, 50);
                    }
                    if (!result.approvalDate && line.indexOf('核准日期') >= 0) {
                        var match = line.match(/核准日期[\\s：:]*(\\d{4}-\\d{2}-\\d{2})/);
                        if (match) result.approvalDate = match[1];
                    }
                    if (!result.insuredCount && line.indexOf('参保人数') >= 0) {
                        var match = line.match(/参保人数[\\s：:]*(\\d+)/);
                        if (match) result.insuredCount = match[1];
                    }
                }
                
                // 提取简介和经营范围（从"简介："开始的内容）
                if (!result.introduction || !result.businessScope) {
                    var introMatch = allText.match(/简介 [：:]([\\s\\S]{50,2000}?)(?=\\n\\s*(?:所属|企业|营业|登记|核准|参保|联系|动态|风险)|$)/);
                    if (introMatch) {
                        var introText = introMatch[1].trim();
                        // 分离简介和经营范围
                        var scopeMatch = introText.match(/^(.+?)(?=\\s*经营范围 [：:])/);
                        if (scopeMatch) {
                            result.introduction = scopeMatch[1].trim().substring(0, 500);
                            var scopeText = introText.substring(scopeMatch[1].length);
                            var scopeOnly = scopeText.match(/经营范围 [：:]([\\s\\S]+)/);
                            if (scopeOnly) {
                                result.businessScope = scopeOnly[1].trim().substring(0, 500);
                            }
                        } else {
                            // 没有明确的经营范围分隔
                            result.introduction = introText.substring(0, 500);
                        }
                    }
                }
                    
                return JSON.stringify(result);
            })()
        """)
            
        if info_json:
            try:
                info = json.loads(info_json)
                data["法定代表人"] = info.get("legalPerson", "")
                data["注册资本"] = info.get("registeredCapital", "")
                data["成立日期"] = info.get("establishDate", "")
                data["统一社会信用代码"] = info.get("creditCode", "")
                data["地址"] = info.get("address", "")
                data["电话"] = info.get("phone", "")
                data["邮箱"] = info.get("email", "")
                data["官网"] = info.get("website", "")
                data["简介"] = info.get("introduction", "")
                data["经营范围"] = info.get("businessScope", "")
                data["所属行业"] = info.get("industry", "")
                data["企业类型"] = info.get("companyType", "")
                data["营业期限"] = info.get("businessTerm", "")
                data["登记机关"] = info.get("registrationAuthority", "")
                data["核准日期"] = info.get("approvalDate", "")
                data["参保人数"] = info.get("insuredCount", "")
            except json.JSONDecodeError:
                pass
            
        return data
    
    # ─ 采集控制 ──
    def crawl(self, companies: list, delay: float = 3.0):
        """批量采集（默认 3 秒间隔，避免触发验证码）"""
        self._crawling = True
        self._stop_requested = False
        self.results = []
        total = len(companies)
        
        self._log(f"开始采集 {total} 家公司")
        
        for i, company in enumerate(companies):
            if self._stop_requested:
                self._log("采集已停止")
                break
            
            while self._pause_requested:
                time.sleep(1)
            
            try:
                result = self.search_company(company)
                self.results.append(result)
                
                if self.signals:
                    self.signals.progress_signal.emit(i + 1, total)
                
                if i < total - 1:
                    time.sleep(delay)
                    
            except Exception as e:
                self._log(f"采集异常：{e}")
        
        self._crawling = False
        self._log(f"采集完成，共 {len(self.results)} 家")
        
        if self.signals:
            self.signals.crawl_finished_signal.emit()
    
    def pause_crawl(self):
        self._pause_requested = True
    
    def resume_crawl(self):
        self._pause_requested = False
    
    def stop_crawl(self):
        self._stop_requested = True
    
    # ── 导出 ─
    def export_excel(self, filepath: str):
        """导出 Excel"""
        try:
            from openpyxl import Workbook
            wb = Workbook()
            ws = wb.active
            ws.title = "爱企查数据"
            
            headers = ["序号", "公司名", "法定代表人", "注册资本", "成立日期",
                      "统一社会信用代码", "电话", "邮箱", "官网", "地址",
                      "简介", "经营范围", "所属行业", "企业类型", "营业期限",
                      "登记机关", "核准日期", "参保人数", "状态", "数据来源"]
            ws.append(headers)
            
            for i, r in enumerate(self.results, 1):
                ws.append([
                    i, r.get("公司名", ""), r.get("法定代表人", ""),
                    r.get("注册资本", ""), r.get("成立日期", ""),
                    r.get("统一社会信用代码", ""), r.get("电话", ""),
                    r.get("邮箱", ""), r.get("官网", ""), r.get("地址", ""),
                    r.get("简介", ""), r.get("经营范围", ""),
                    r.get("所属行业", ""), r.get("企业类型", ""),
                    r.get("营业期限", ""), r.get("登记机关", ""),
                    r.get("核准日期", ""), r.get("参保人数", ""),
                    r.get("状态", ""), r.get("数据来源", "")
                ])
            
            wb.save(filepath)
            self._log(f"已导出 {len(self.results)} 条数据到 {filepath}")
            return True
        except Exception as e:
            self._log(f"导出失败：{e}")
            return False
