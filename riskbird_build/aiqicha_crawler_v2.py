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

import block_guard

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
        blocked_signal = pyqtSignal(str)   # 被安全验证/风控拦截，需人工在浏览器完成验证
        block_cleared_signal = pyqtSignal(str)  # 人工完成验证，采集已自动继续
        login_required_signal = pyqtSignal(str)  # 未登录，需界面上确认去登录还是继续
else:
    class CrawlerSignals:
        pass


class AiQiChaCrawlerV2:
    """爱企查爬虫 - 基于 CDP 直接通信"""
    
    BASE_URL = "https://aiqicha.baidu.com"
    SEARCH_URL = "https://aiqicha.baidu.com/s?q={}"
    DETAIL_URL = "https://aiqicha.baidu.com/company_detail_{}"
    PLATFORM_LABEL = "爱企查"
    
    def __init__(self, gui_callback=None, debug_port=9233):
        self.gui_callback = gui_callback
        self.debug_port = debug_port
        self.base_url = f"http://127.0.0.1:{debug_port}"
        # 双标签并行采集：本会话独占的标签页 id，所有 CDP 命令只发往它
        self.pin_tab_id = None
        self.attach_mode = False
        
        self._logged_in = False
        self._crawling = False
        self._stop_requested = False
        self._pause_requested = False
        self.results = []
        # 采集线程在写、GUI 线程在读（双标签并行时两个线程同时写），靠锁交接快照
        self.results_lock = threading.RLock()
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

        # 登录态闸门与空壳计数
        self._login_gate_obj = None
        self._shell_streak = 0
        self._shell_gate_passed = False
    
    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        full_msg = f"[{ts}] {msg}"
        if self.gui_callback:
            self.gui_callback(full_msg)
        if self.signals:
            self.signals.log_signal.emit(full_msg)
        print(full_msg)

    # ── 结果交接 / 拦截处理 ──
    def _add_result(self, result: dict):
        """线程安全地追加一条采集结果"""
        with self.results_lock:
            self.results.append(result)

    def snapshot_results(self) -> list:
        """给 GUI 用的快照：连每条记录的 dict 一起复制。

        只拷列表不够：单条记录是逐步填写的（search_company 边采边往 dict 里写），
        写 Excel 时遍历 r.keys() 如果 dict 同时变大就会 RuntimeError。
        """
        with self.results_lock:
            return [dict(r) for r in self.results]

    def _emit_blocked(self, reason: str):
        if self.signals:
            try:
                self.signals.blocked_signal.emit(reason or "页面被平台拦截")
            except Exception:
                pass

    def _detect_block(self, ws_url=None):
        """只看本会话自己那个标签，当前页面是不是验证页/风控插页"""
        ws = ws_url or (self._get_ws_url() if self.pin_tab_id else None)
        if not ws:
            return ""
        try:
            return block_guard.detect_cdp(lambda e: self._execute_js(ws, e))
        except Exception:
            return ""

    def _wait_unblock(self, reason: str, max_wait: int = 1800) -> bool:
        """被拦截时原地等人工验证，不推进名单、不写入空记录"""
        ok = block_guard.wait_unblock(
            reason, self._log, self._emit_blocked,
            lambda: self._stop_requested, self._detect_block,
            max_wait=max_wait,
        )
        if ok:
            self._emit_cleared(reason)
        return ok

    def _emit_cleared(self, reason: str):
        if self.signals:
            try:
                self.signals.block_cleared_signal.emit(reason)
            except Exception:
                pass

    # ── 登录态确认 ──
    def check_login(self):
        """用浏览器 Cookie 判登录态。

        爱企查的登录凭证 BDUSS 是 HttpOnly，document.cookie 读不到，必须走
        CDP Network.getCookies；拿不到 Cookie 时返回 unknown 而不是 missing，
        免得把“读不到”误判成“未登录”而拦住正常采集。
        """
        ws = self._get_ws_url()
        if not ws:
            return "unknown", "爱企查标签页还没打开"
        try:
            resp = self._send_command(ws, "Network.getCookies",
                                      {"urls": ["https://aiqicha.baidu.com"]})
        except Exception as e:
            return "unknown", f"读取 Cookie 失败：{str(e)[:60]}"
        cookies = (resp.get("result") or {}).get("cookies")
        if cookies is None:
            return "unknown", "读取 Cookie 无返回"
        return block_guard.login_state([c.get("name") for c in cookies],
                                       self.PLATFORM_LABEL)

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

    def _shell_gate(self) -> bool:
        """连续采到空壳：多半是登录失效或权限不足。不写空记录，走登录确认流程"""
        if self._shell_gate_passed:
            return True
        self._shell_gate_passed = True
        state, detail = self.check_login()
        head = f"连续 {self._shell_streak} 家详情字段全空"
        reason = (head + f"：{detail}" if state == "missing"
                  else head + "（登录凭证仍在，可能是平台权限或页面结构变了）")
        return self._ask_login(reason)

    def _suggest_suppressed(self, ws_url, name: str) -> bool:
        """区分“真没这家公司”与“联想被风控抑制”。

        实测：正常时注入后 1 秒内必出联想项；被抑制时输入框里仍保留我们注入的公司名
        但联想恒为空（而真查无此公司时，前端会把输入框换成自己推荐的内容）。
        误判代价不对称：把真没这家公司当拦截只会多等几十秒，反过来会把整份名单烧成空数据。
        """
        state = self._execute_js(ws_url, """
            (function() {
                var i = document.querySelector('#aqc-search-input') || document.querySelector('#aqc-header-search-input');
                if (!i) return JSON.stringify({v: '', n: -1});
                var n = document.querySelectorAll('.ivu-select-item, .sug-company-line').length;
                return JSON.stringify({v: i.value || '', n: n});
            })()
        """)
        if not state:
            return False
        try:
            info = json.loads(state)
        except Exception:
            return False
        val = str(info.get("v") or "").strip()
        if int(info.get("n") or 0) > 0:
            return False
        key = str(name)[:6]
        return bool(key) and (key in val or val in str(name))
    
    # ── CDP 通信 ──
    def _get_tabs(self):
        """获取所有标签页"""
        resp = requests.get(f"{self.base_url}/json/list", timeout=5)
        return resp.json()
    
    def _get_ws_url(self, url_pattern=None):
        """获取 WebSocket URL（双标签并行时固定返回本会话独占的标签，不会被其他会话的页面干扰）"""
        tabs = self._get_tabs()
        if self.pin_tab_id:
            for tab in tabs:
                if tab.get("id") == self.pin_tab_id:
                    return tab.get("webSocketDebuggerUrl")
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
    
    def start_browser(self, headed: bool = True, attach: bool = False):
        """启动浏览器（自动管理 Chrome 进程）

        attach=True 时不启动也不关闭任何 Chrome，只附加到已运行的共享浏览器并开一个独占标签。
        """
        with self._browser_lock:
            if attach:
                self.attach_mode = True
                try:
                    if not self._get_tabs():
                        self._log("❌ 共享浏览器调试端口未就绪，无法附加")
                        return False
                except Exception:
                    self._log("❌ 共享浏览器调试端口连不上，无法附加")
                    return False
                return self.open_own_tab()

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
        if self.attach_mode and self.pin_tab_id:
            # 双标签并行：只关自己独占的标签，共享浏览器留给另一个会话
            try:
                ws = self._get_ws_url()
                if ws:
                    self._close_tab(ws)
            except Exception:
                pass
            self.pin_tab_id = None
    
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
            "状态": "", "法定代表人": "", "注册资本": "", "实缴资本": "",
            "成立日期": "", "统一社会信用代码": "", "工商注册号": "",
            "组织机构代码": "", "纳税人识别号": "", "纳税人资质": "",
            "电话": "", "邮箱": "", "官网": "", "地址": "", "行政区划": "",
            "简介": "", "经营范围": "", "所属行业": "",
            "企业类型": "", "营业期限": "", "登记机关": "",
            "核准日期": "", "参保人数": "", "曾用名": "", "数据来源": "爱企查",
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
                
                # 并行采集时两个会话抢资源，首页水合明显变慢；上面的判据（#app）在本站永远不成立，
                # 必须额外等到页面真正可交互，否则注入的公司名会被未挂载的组件丢掉
                if self.attach_mode:
                    self._wait_page_hydrated(ws_url, 15)
                
                # 检查页面状态
                page_check = self._check_page_status(ws_url)
                if page_check == 'blocked':
                    self._log("️ 检测到反爬虫，尝试刷新...")
                    self._execute_js(ws_url, "(function() { location.reload(); })()")
                    time.sleep(5)

                # 本标签已经停在验证页上：原地等人工完成验证，不拿这一家去凑数
                block = self._detect_block(ws_url)
                if block:
                    if not self._wait_unblock(block):
                        result["状态"] = "拦截未解除"
                        return result
                    ws_url = self._get_or_create_homepage_tab()
                
                # 3. 清空并输入公司名
                self._clear_and_input_search(ws_url, name)
                
                # 4. 等待自动补全出现（智能等待：最多 3 秒）
                autocomplete_found = self._wait_autocomplete(ws_url, 3.0)
                
                # 并行模式下首页可能刚好在注入时才挂载完，重试两次（单平台模式保持原有时序不变）
                if not autocomplete_found and self.attach_mode:
                    for _retry in range(2):
                        self._wait_page_hydrated(ws_url, 10)
                        self._log(f"  未出现联想，第{_retry + 1}次重试输入：{name}")
                        self._clear_and_input_search(ws_url, name)
                        autocomplete_found = self._wait_autocomplete(ws_url, 3.0)
                        if autocomplete_found:
                            break

                if not autocomplete_found:
                    # 先分清是“真没这家公司”还是“联想被风控抑制”，
                    # 被抑制时绝不能把这一家记成未找到并往下烧名单（否则整份名单会瞬间变成空数据）
                    block = self._detect_block(ws_url)
                    suppressed = bool(block) or self._suggest_suppressed(ws_url, name)
                    if not suppressed:
                        self._log(f"未找到自动补全：{name}")
                        result["状态"] = "未找到"
                        return result
                    if not block:
                        block = "搜索联想被平台抑制（多为请求过快触发风控）"
                    self._log(f"⚠ {block}：{name}")
                    if not self._wait_unblock(block):
                        result["状态"] = "拦截未解除"
                        return result
                    # 恢复后重新归位首页并重试这一家
                    self._navigate_to_homepage(ws_url)
                    time.sleep(4)
                    self._wait_page_hydrated(ws_url, 15)
                    self._clear_and_input_search(ws_url, name)
                    autocomplete_found = self._wait_autocomplete(ws_url, 5.0)
                    if not autocomplete_found:
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
    
    def open_own_tab(self) -> bool:
        """在共享浏览器里开一个本会话独占的页面并打开爱企查首页。

        用独立窗口（newWindow）而不是同窗口的新标签：同窗口内只有一个标签是“可见”的，
        后台标签会被 Chrome 节流（定时器降频、requestAnimationFrame 暂停），
        并行采集时两边都保持可见才不会互相拖慢。
        """
        target_id = None
        browser_ws = None
        try:
            ver = requests.get(f"{self.base_url}/json/version", timeout=5).json()
            browser_ws = ver.get("webSocketDebuggerUrl")
        except Exception:
            browser_ws = None
        if browser_ws:
            for params in ({"url": self.BASE_URL, "newWindow": True}, {"url": self.BASE_URL}):
                try:
                    r = self._send_command(browser_ws, "Target.createTarget", params)
                    target_id = (r.get("result") or {}).get("targetId")
                    if target_id:
                        break
                except Exception:
                    target_id = None
        if not target_id:
            try:
                resp = requests.put(f"{self.base_url}/json/new?url={self.BASE_URL}", timeout=5)
                target_id = (resp.json() or {}).get("id")
            except Exception:
                target_id = None
        if not target_id:
            self._log("❌ 无法在共享浏览器中创建爱企查标签")
            return False
        self.pin_tab_id = target_id
        self._log(f"已占用爱企查独占标签 {target_id[:8]}")
        for _ in range(8):
            time.sleep(1)
            try:
                ws = self._get_ws_url()
                cur = self._execute_js(ws, "(function() { return window.location.href; })()") if ws else ""
                if cur and "aiqicha" in str(cur):
                    # 光看 URL 不够：组件未挂载就采集会直接失败，等它真正可交互
                    self._wait_page_hydrated(ws, 15)
                    self._log("爱企查页面已打开")
                    return True
            except Exception:
                pass
        self._log("⚠ 爱企查页面未确认打开，将继续尝试")
        return True

    def _get_or_create_homepage_tab(self):
        """获取或创建爱企查首页标签页（复用现有，避免重复打开）"""
        # 双标签并行：永远只用本会话独占的那个标签，不去扫全局标签（否则会拿到另一个会话的页面）
        if self.pin_tab_id:
            ws = self._get_ws_url()
            if ws:
                cur = ""
                try:
                    cur = self._execute_js(ws, "(function() { return window.location.href; })()") or ""
                except Exception:
                    cur = ""
                if "aiqicha.baidu.com" not in cur or "/s?" in cur or "company_detail" in cur:
                    self._navigate_to_homepage(ws)
                    time.sleep(4)
                    if self.attach_mode:
                        self._wait_page_hydrated(ws, 15)
                elif self.attach_mode and not self._page_hydrated(ws):
                    self._wait_page_hydrated(ws, 15)
                return ws
            self.pin_tab_id = None   # 标签被关掉了，下面重建

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
    
    def _page_hydrated(self, ws_url) -> bool:
        """页面是否已真正可交互（组件已挂载、正文已渲染）。

        不能用 #app 判断：爱企查首页根本没有 #app（实测 hasApp 永远 False），
        也不能只看 readyState：interactive 阶段正文只有 300 多字符，此时注入公司名
        会被还未挂载的组件直接丢掉（表现为“未找到自动补全”）。
        """
        try:
            st = self._execute_js(ws_url, """
                (function() {
                    var input = document.querySelector('#aqc-search-input') || document.querySelector('#aqc-header-search-input');
                    return {
                        ready: document.readyState === 'complete',
                        hasInput: !!input,
                        len: (document.body.innerText || '').length
                    };
                })()
            """)
            return bool(st and st.get("ready") and st.get("hasInput") and int(st.get("len") or 0) >= 2000)
        except Exception:
            return False

    def _wait_page_hydrated(self, ws_url, seconds: int = 15) -> bool:
        """等页面水合完成（并行采集时两个会话抢资源，首页明显变慢）"""
        for _ in range(seconds):
            if self._page_hydrated(ws_url):
                return True
            time.sleep(1)
        return False

    def _wait_autocomplete(self, ws_url, seconds: float = 3.0) -> bool:
        """等搜索联想项出现"""
        for _ in range(int(seconds * 2)):
            time.sleep(0.5)
            try:
                if self._execute_js(ws_url, """
                    (function() {
                        var items = document.querySelectorAll('.ivu-select-item, .sug-company-line');
                        return items.length > 0 ? 'found' : 'waiting';
                    })()
                """) == 'found':
                    return True
            except Exception:
                pass
        return False

    def _navigate_to_homepage(self, ws_url):
        """导航回爱企查首页"""
        self._execute_js(ws_url, "(function() { window.location.href = 'https://aiqicha.baidu.com/'; })()")
    
    def _extract_detail_data_full(self, ws_url, company_name: str) -> dict:
        """从详情页提取全量数据（window.pageData + 工商信息表结构化解析，避免 innerText 正则错位）"""
        data = {
            "法定代表人": "", "注册资本": "", "实缴资本": "", "成立日期": "",
            "统一社会信用代码": "", "工商注册号": "", "组织机构代码": "",
            "纳税人识别号": "", "纳税人资质": "",
            "电话": "", "邮箱": "", "官网": "", "地址": "", "行政区划": "",
            "简介": "", "经营范围": "", "所属行业": "", "企业类型": "",
            "营业期限": "", "登记机关": "", "核准日期": "", "参保人数": "",
            "曾用名": "",
        }

        # 一次 JS 调用取回三路数据：pageData JSON / 工商信息表键值对 / 头部信息
        info_json = self._execute_js(ws_url, """
            (function() {
                var out = {pd: null, table: {}, head: {}};
                try {
                    if (window.pageData && window.pageData.result) {
                        out.pd = JSON.stringify(window.pageData.result);
                    }
                } catch(e) {}
                function cellText(td) {
                    var c = td.cloneNode(true);
                    var sels = ['.ivu-poptip-popper', '.fold-btn', 'a', 'button', 'img', 'svg', 'i'];
                    for (var s = 0; s < sels.length; s++) {
                        var nodes = c.querySelectorAll(sels[s]);
                        for (var n = 0; n < nodes.length; n++) {
                            if (nodes[n].parentNode) nodes[n].parentNode.removeChild(nodes[n]);
                        }
                    }
                    return (c.textContent || '').replace(/\\s+/g, ' ').trim();
                }
                var KNOWN = ['企业名称', '法定代表人', '经营状态', '成立日期', '行政区划',
                    '注册资本', '实缴资本', '企业类型', '所属行业', '工商注册号',
                    '组织机构代码', '统一社会信用代码', '纳税人识别号', '纳税人资质',
                    '营业期限', '核准日期', '参保人数', '登记机关', '曾用名',
                    '注册地址', '经营范围', '英文名'];
                function normLabel(raw) {
                    for (var k = 0; k < KNOWN.length; k++) {
                        if (raw.indexOf(KNOWN[k]) === 0) return KNOWN[k];
                    }
                    return raw.split(' ')[0].replace(/[：:]\\s*$/, '');
                }
                try {
                    var tbl = document.querySelector('table.zx-detail-basic-table');
                    if (tbl) {
                        var trs = tbl.querySelectorAll('tr');
                        for (var r = 0; r < trs.length; r++) {
                            var tds = trs[r].querySelectorAll('td');
                            for (var i = 0; i + 1 < tds.length; i += 2) {
                                var label = normLabel(cellText(tds[i]));
                                var value = cellText(tds[i + 1]);
                                if (label && value && !out.table[label]) out.table[label] = value;
                            }
                        }
                    }
                } catch(e) {}
                try {
                    var items = document.querySelectorAll('.info-item');
                    for (var k = 0; k < items.length; k++) {
                        var it = items[k];
                        if (it.querySelector('.telphone-lists-wrap') || it.querySelector('.email-lists-wrap')) continue;
                        var t = (it.textContent || '').replace(/\\s+/g, ' ').trim();
                        if (t.indexOf('注册时间') >= 0 && !out.head.regTime) {
                            var m = t.match(/\\d{4}-\\d{2}-\\d{2}/);
                            if (m) out.head.regTime = m[0];
                        }
                        if (t.indexOf('网址') === 0 && !out.head.website) {
                            var cd = it.querySelector('.child-data');
                            var w = (cd ? cd.textContent : t).replace(/\\s+/g, ' ').trim();
                            if (w && w.indexOf('暂无') < 0) out.head.website = w;
                        }
                    }
                    var brief = document.querySelector('.item-brief .child-data');
                    if (brief) {
                        var txt = '';
                        for (var nd = 0; nd < brief.childNodes.length; nd++) {
                            if (brief.childNodes[nd].nodeType === 3) txt += brief.childNodes[nd].textContent;
                        }
                        out.head.brief = txt.replace(/\\s+/g, ' ').replace(/^简介[：:]\\s*/, '').trim();
                    }
                } catch(e) {}
                return JSON.stringify(out);
            })()
        """)

        if not info_json:
            return data
        try:
            info = json.loads(info_json)
        except json.JSONDecodeError:
            return data

        table = info.get("table") or {}
        head = info.get("head") or {}
        pd = {}
        pd_str = info.get("pd")
        if pd_str:
            try:
                pd = json.loads(pd_str)
            except json.JSONDecodeError:
                pd = {}

        def pick(*vals):
            for v in vals:
                if v and str(v).strip() and str(v).strip() not in ("-", "--", "暂无"):
                    return str(v).strip()
            return ""

        # 全量电话/邮箱（pageData 内含历史年报联系方式）
        phones, emails = [], []
        if pd.get("telephone"):
            phones.append(str(pd["telephone"]).strip())
        for p in (pd.get("phoneinfo") or []):
            num = str((p or {}).get("phone", "")).strip()
            if num and num not in phones:
                phones.append(num)
        if pd.get("email"):
            emails.append(str(pd["email"]).strip())
        for e in (pd.get("emailinfo") or []):
            addr = str((e or {}).get("email", "")).strip()
            if addr and addr.lower() not in [x.lower() for x in emails]:
                emails.append(addr)

        website = pick(pd.get("website"), head.get("website"))
        if website in ("-", "暂无网址"):
            website = ""
        insured = pick(table.get("参保人数"))
        if insured:
            insured = re.sub(r"[人\s]", "", insured)
        approval = pick(table.get("核准日期"))
        if approval:
            m = re.search(r"\d{4}-\d{2}-\d{2}", approval)
            approval = m.group(0) if m else approval

        data["法定代表人"] = pick(pd.get("legalPerson"), table.get("法定代表人"))
        data["注册资本"] = pick(table.get("注册资本"), pd.get("regCapital"))
        data["实缴资本"] = pick(table.get("实缴资本"))
        data["成立日期"] = pick(table.get("成立日期"), pd.get("startDate"), head.get("regTime"))
        data["统一社会信用代码"] = pick(pd.get("unifiedCode"), table.get("统一社会信用代码"))
        data["工商注册号"] = pick(pd.get("licenseNumber"), table.get("工商注册号"))
        data["组织机构代码"] = pick(table.get("组织机构代码"))
        data["纳税人识别号"] = pick(pd.get("taxNo"), table.get("纳税人识别号"))
        data["纳税人资质"] = pick(table.get("纳税人资质"))
        data["电话"] = ";".join(phones)
        data["邮箱"] = ";".join(emails)
        data["官网"] = website
        data["地址"] = pick(table.get("注册地址"), pd.get("regAddr"), pd.get("addr"))
        data["行政区划"] = pick(table.get("行政区划"))
        data["简介"] = pick(pd.get("describe"), head.get("brief"))
        data["经营范围"] = pick(table.get("经营范围"), pd.get("scope"))
        data["所属行业"] = pick(pd.get("industry"), table.get("所属行业"))
        data["企业类型"] = pick(table.get("企业类型"))
        data["营业期限"] = pick(table.get("营业期限"))
        data["登记机关"] = pick(table.get("登记机关"))
        data["核准日期"] = approval
        data["参保人数"] = insured
        data["曾用名"] = pick(table.get("曾用名"))
        return data
    
    # ─ 采集控制 ──
    def crawl(self, companies: list, start_index: int = 0):
        """批量采集，支持从指定位置续采（签名与风鸟/企知道一致，不重置已有结果）"""
        self._crawling = True
        self._stop_requested = False
        self._pause_requested = False
        total = len(companies)

        self._log(f"开始采集，共 {total} 家公司，从第 {start_index + 1} 条开始")

        # 未登录时详情页字段是空的，先确认登录态再开跑，不白烧名单
        self._shell_streak = 0
        self._shell_gate_passed = False
        if not self._login_gate():
            self._crawling = False
            if self.signals:
                self.signals.crawl_finished_signal.emit()
            return

        for idx in range(start_index, total):
            if self._stop_requested:
                self._log(f"用户停止采集，已采集 {len(self.results)} 条")
                break

            name = str(companies[idx]).strip()
            if not name:
                continue

            while self._pause_requested:
                time.sleep(0.5)
                if self._stop_requested:
                    break

            # 拦截门放在暂停之后：用户点"暂停"时优先听用户的，不抢着去等人工验证。
            # 被拦时不推进名单、不写空记录，等人工在浏览器完成验证后从这一家继续
            block = self._detect_block()
            if block and not self._wait_unblock(block):
                break

            try:
                result = self.search_company(name)
                if result.get("状态") == "拦截未解除":
                    self._log("⏹ 拦截未解除，本轮采集到此结束（这一家未写入结果，续采会从这里开始）")
                    break
                if block_guard.is_shell_record(result):
                    # 不写入空壳，也不推进名单：登录补上后从这一家重新采
                    self._shell_streak += 1
                    self._log(f"  ⚠ 详情字段全空（连续 {self._shell_streak} 家），这一家先不写入结果")
                    if self._shell_streak >= block_guard.SHELL_STREAK_LIMIT \
                            and not self._shell_gate():
                        self._log("⏹ 未登录问题未解决，本轮采集到此结束")
                        break
                    if idx < total - 1:
                        time.sleep(self.delay_between)
                    continue
                self._shell_streak = 0
                self._add_result(result)

                if self.signals:
                    self.signals.progress_signal.emit(idx + 1, total)

                if idx < total - 1:
                    time.sleep(self.delay_between)

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
            
            headers = ["序号", "公司名", "法定代表人", "注册资本", "实缴资本",
                      "成立日期", "统一社会信用代码", "工商注册号",
                      "组织机构代码", "纳税人识别号", "纳税人资质",
                      "电话", "邮箱", "官网", "地址", "行政区划",
                      "简介", "经营范围", "所属行业", "企业类型", "营业期限",
                      "登记机关", "核准日期", "参保人数", "曾用名",
                      "状态", "数据来源", "搜索结果 URL"]
            ws.append(headers)
            
            for i, r in enumerate(self.results, 1):
                ws.append([i] + [r.get(h, "") for h in headers[1:]])
            
            wb.save(filepath)
            self._log(f"已导出 {len(self.results)} 条数据到 {filepath}")
            return True
        except Exception as e:
            self._log(f"导出失败：{e}")
            return False
