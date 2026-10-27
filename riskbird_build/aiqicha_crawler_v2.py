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
from urllib.parse import quote, unquote

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
        handoff_signal = pyqtSignal(str)  # 本路已放弃等人工，剩余名单转交另一平台（界面要撤掉红条）
else:
    class CrawlerSignals:
        pass


# 搜索结果页“渲染完了但确实一家都没有”的正文长度下限：
# 结果页空壳时正文只有几百字，拿它当“查无此公司”会把限流误判成未找到、白烧名单
SEARCH_PAGE_READY_LEN = 1200
# 要连续这么多次“正文已渲染但结果区空”才敢定“查无此公司”（每轮约 0.7 秒）
SEARCH_PAGE_NONE_POLLS = 4
# 夜间挂机无人值守：拦截持续超过这个秒数就放弃爱企查、把剩余名单移交企知道
BLOCK_WAIT_LIMIT = 600
# 退避期间重新发起一次独立查询的间隔（秒）。原来每 15 秒刷一轮，一轮里又按
# 0.7 秒抽十几次同一个页面：十分钟近千条日志、几百次抽取，用户看不出重点，
# 平台只看到我们在刷，限流更难解除
REPROBE_INTERVAL = 45
# 连续抽不出结果条目到这个次数，就把页面实况存成证据文件（区分“改版”与“平台静默拒绝”）
SEARCH_PAGE_UNKNOWN_LIMIT = 6
# 算“页面已经渲染过”的正文下限：死页/验证回跳页的地址栏也停在 /s?q= 上，
# 拿它当“已经在这一页”会省掉一次导航，于是同一个旧 DOM 重抽十几轮都拿不到数据
SEARCH_PAGE_MIN_BODY = 300
# 刚被拦过之后本路的降速秒数：过码后立刻恢复原速，往往三五分钟又弹一次验证
COOLDOWN_AFTER_BLOCK = 90
# 按页面上下文追加的拦截插页文案（只在正文极短时参与判断，沿用 block_guard 的防误伤约束）
PAGE_INTERSTITIAL_MARKERS = ("当前暂时无法访问", "暂不支持访问", "环境异常", "accessrestriction")


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

        # 夜间无人值守：被拦（验证码/限流）累计超过这个时长就当作没人会来处理，
        # 停止爱企查这一路，把剩余名单交给企知道接着采。handoff 由双标签工作线程注入；
        # 单平台采集时 handoff 为 None，沿用原来的长等待（停下来只会让名单越积越多）。
        self.block_wait_limit = BLOCK_WAIT_LIMIT
        self.handoff = None
        self._blocked_at = 0.0   # 本轮拦截开始时刻（0 表示当前没被拦）

        # 登录态闸门与空壳计数
        self._login_gate_obj = None
        self._shell_streak = 0
        self._shell_gate_passed = False

        # 会话取证与降速状态：diag_dir 由界面设为本次采集的会话目录
        self.diag_dir = None
        self._unknown_streak = 0        # 连续抽不出结果条目的次数
        self._evidence_dumped = False   # 一次运行只存一份证据，别堆文件
        self._cooldown_until = 0.0      # 被拦过之后的降速截止时刻
    
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

    def _detect_block(self, ws_url=None, extra_markers=None):
        """只看本会话自己那个标签，当前页面是不是验证页/风控插页

        extra_markers 默认带上爱企查自己的插页文案：这些页面地址栏仍是百度域名内
        的正常路径、标题也常是空的，只看 URL/标题会整页漏判（实测“当前暂时无法访问”
        插页正文只有 134 字），而正文长度上限已经把正常页面挡在外面。
        """
        ws = ws_url or (self._get_ws_url() if self.pin_tab_id else None)
        if not ws:
            return ""
        try:
            return block_guard.detect_cdp(
                lambda e: self._execute_js(ws, e),
                extra_markers=PAGE_INTERSTITIAL_MARKERS
                if extra_markers is None else extra_markers)
        except Exception:
            return ""

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
        """没有界面时（命令行直接跑）不等人，直接结束本轮。

        双标签并行（有移交对象）时按 block_wait_limit 限时等：夜间没人点“继续采集”，
        干等 30 分钟等于整晚白跑，超时就把名单交给企知道。验证码与登录共用同一个
        “等人工”计时，因为它们对夜间挂机来说是同一回事：没人在现场。
        """
        if self.signals is None:
            self._log(f"🛑 {reason}；没有界面可确认，本轮采集结束")
            return False
        if self.handoff is None:
            return self._gate().ensure(reason)
        now = time.time()
        if not self._blocked_at:
            self._blocked_at = now
        left = int(self.block_wait_limit - (now - self._blocked_at))
        if left <= 0:
            self._log(f"⏱ 等人工处理已超过 {int(self.block_wait_limit) // 60} 分钟，爱企查这一路先停")
            return False
        if self._gate().ensure(reason, max_wait=left, rounds=1):
            self._blocked_at = 0.0   # 人已经确认过了，下一段重新计时
            return True
        return False

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
    
    def _wait_unblock(self, reason: str, max_wait: int = 1800, interval: int = 5,
                      ready=None, heal=None, heal_every: int = 30) -> bool:
        """被拦截/被限流时原地等恢复，不推进名单、不写入空记录

        ready 传能力探针时，“验证页没了”不再算恢复，必须实测能拿到数据才放行；
        heal 默认给上 _heal_block，验证页挂在另一个标签时不至于死等到超时。
        """
        ok = block_guard.wait_unblock(
            reason, self._log, self._emit_blocked,
            lambda: self._stop_requested, self._detect_block,
            max_wait=max_wait, interval=interval,
            ready=ready, heal=heal or self._heal_block, heal_every=heal_every,
        )
        if ok:
            self._emit_cleared(reason)
            # 拦刚解除时平台是“盯得最紧”的，马上按原速接着跑几乎一定会再弹一次
            # 验证（实测就是三五分钟一轮），所以这里强制降速一段
            self._cooldown_until = time.time() + COOLDOWN_AFTER_BLOCK
            self._unknown_streak = 0
        return ok

    def _wait_unblock_limited(self, reason: str, **kw) -> bool:
        """带“没人处理就放弃”上限的等待。返回 False = 该停止爱企查并移交名单。

        上限按整段拦截累计计算（验证码反复出现也只算一段），不是每家重新给 10 分钟，
        否则夜间挂机会变成“每家等 10 分钟、整晚都在等”。
        没有移交对象（单平台采集）时沿用原来的长等待：这时候停下来只会把名单越积越多。
        """
        if self.handoff is None:
            return self._wait_unblock(reason, **kw)
        now = time.time()
        if not self._blocked_at:
            self._blocked_at = now
        left = int(self.block_wait_limit - (now - self._blocked_at))
        if left <= 0:
            self._log(f"⏱ 拦截已持续 {int(self.block_wait_limit) // 60} 分钟以上无人处理，爱企查这一路先停")
            return False
        kw["max_wait"] = min(int(kw.get("max_wait") or left), left)
        if int(kw.get("interval") or 1) > kw["max_wait"]:
            kw["interval"] = max(1, kw["max_wait"])
        if self._wait_unblock(reason, **kw):
            self._blocked_at = 0.0   # 这一段拦截过去了，下一段重新计时
            return True
        return False

    def _give_up_left(self, companies: list, idx: int, why: str = ""):
        """停止本路采集，并把从这一家开始的剩余名单交给企知道接着采。

        只在“不是用户主动停止”时调用：用户点停止代表全盘打算，这时候把名单
        塞给企知道反而违背用户意图。没有移交对象（单平台采集）时直接不做。
        """
        if self.handoff is None:
            return
        left = [str(n or "").strip() for n in companies[idx:] if str(n or "").strip()]
        mins = max(1, int(self.block_wait_limit) // 60)
        why = why or f"验证码/限流持续 {mins} 分钟以上无人处理"
        self._log(f"🛑 爱企查这一路到此结束（{why}），剩下 {len(left)} 家不再在这里死等")
        if left:
            try:
                self.handoff.give_up(left, why)
            except Exception as e:
                self._log(f"  ⚠ 名单移交失败：{e}")
        if self.signals:
            try:
                # 采集已经不等人了，界面上的“等待人工处理”红条要撤掉，
                # 否则企知道还在跑、用户却以为整个工具卡在验证上
                self.signals.handoff_signal.emit(why)
            except Exception:
                pass

    def _heal_block(self) -> bool:
        """卡在验证页时的自愈，返回是否做了动作（调用方会再判一次拦截）。

        过验证码最常见的两种“假死”：
        ① 平台另开了一个标签做验证，用户在那个标签里验过了，但本会话的标签
          还停在 wappass 验证页上——只看本标签就会一直判拦截，直到 30 分钟超时；
        ② 本标签已经飘到验证页/空白页，而验证已写进全局 Cookie，导回首页就能直接用。
        """
        ws = self._get_ws_url()
        if not ws:
            return False
        # ① 双标签并行时，共享浏览器里若有另一个没被拦的爱企查标签，把会话切过去
        if self.pin_tab_id:
            for t in (self._get_tabs() or []):
                if t.get("type") != "page" or t.get("id") == self.pin_tab_id:
                    continue
                u = str(t.get("url") or "")
                tws = t.get("webSocketDebuggerUrl")
                if "aiqicha.baidu.com" not in u or "/login" in u or not tws:
                    continue
                if self._detect_block(tws):
                    continue
                self.pin_tab_id = t.get("id")
                self._log(f"  改用已恢复的爱企查标签 {str(self.pin_tab_id)[:8]}")
                return True
        # ② 本标签不在爱企查域名上（停在验证页/空白页）：导回首页
        try:
            cur = str(self._execute_js(ws, "(function(){return location.href;})()") or "")
        except Exception:
            cur = ""
        if "aiqicha.baidu.com" not in cur:
            self._navigate_to_homepage(ws)
            time.sleep(4)
            return True
        # ③ 地址在爱企查里、验证页也没了，但页面其实是个死页（组件没挂载/停在验证回跳页）：
        #    这正是“手工搜能进详情、脚本却什么都抽不到”的样子，整页重刷一次才能拿到新 DOM
        if not self._page_hydrated(ws):
            self._log("  页面已不可用（组件未挂载/停在验证回跳页），整页重刷…")
            self._soft_reset_tab(ws)
            return True
        return False

    def _soft_reset_tab(self, ws_url) -> bool:
        """把本标签洗回“刚进首页”的干净状态，并保证真的发生了一次重新加载

        两个地方必须有：① 过完验证码后手工搜同一家能进详情、工具不行；② 退避重试
        时 `_goto_search_page` 发现地址栏已经是这一家的 /s?q= 就直接返回 True 不再导航，
        于是一口气在同一个死页面上重抽十几轮，抽到的还是那份旧 DOM。
        正在过验证时不做（不然会把用户正在点的验证码刷掉）。
        """
        if self._detect_block(ws_url):
            return False
        try:
            self._execute_js(ws_url, "(function(){location.reload();})()")
            time.sleep(3)
            self._wait_page_hydrated(ws_url, 15)
            self._ensure_request_observer(ws_url)   # 重刷会把观测器一起刷掉，装回来
            return True
        except Exception:
            return False

    def _cooldown_wait(self):
        """被拦过之后的降速：不给平台“刚恢复又被密集请求”的理由，停止要能立即打断"""
        left = int(self._cooldown_until - time.time())
        if left <= 0:
            return
        self._log(f"  ☕ 刚被平台拦过，先降速 {left} 秒再继续（免得马上又弹验证）")
        end = time.time() + left
        while time.time() < end:
            if self._stop_requested:
                return
            time.sleep(1)

    # ── 搜索结果页（联想挂了时的兜底路线，与手工搜公司名同一条路） ──
    @staticmethod
    def _norm_name(text: str) -> str:
        """比公司名用：去空白与括号装饰、转小写"""
        s = re.sub(r"[\s\u3000（）()【】\[】\]《》·、,，.。_-]", "", str(text or ""))
        return s.lower()

    def _current_url(self, ws_url) -> str:
        try:
            return str(self._execute_js(ws_url, "(function(){return location.href;})()") or "")
        except Exception:
            return ""

    def _wait_detail_url(self, ws_url, seconds: float = 10.0, expect: str = "company_detail") -> str:
        """等本标签跳到详情页，返回详情页 URL（超时尚未跳转返回空串）"""
        for _ in range(int(seconds * 2)):
            time.sleep(0.5)
            cur = self._current_url(ws_url)
            if expect in cur:
                return cur
            if not cur:
                break
        return ""

    def _search_page_rendered(self, ws_url) -> bool:
        """本标签是不是一个已经渲染出内容的页面（用来决定要不要省掉一次导航）"""
        try:
            n = self._execute_js(ws_url,
                                 "(function(){return document.body?(document.body.innerText||'').length:0;})()")
            return int(n or 0) >= SEARCH_PAGE_MIN_BODY
        except Exception:
            return False

    def _goto_search_page(self, ws_url, name: str, reload: bool = False) -> bool:
        """把本标签导航到这家公司的搜索结果页，并等到地址栏真的落在 /s? 上

        reload=True 时无论是否已在本页都重新加载（用来做“第二次独立查询”的确认）。
        不等导航落地就读页面会读到上一个页面（首页/详情页），把状态判错。
        同一家公司“已经在本页”也不能直接复用：页面可能是个死页，此时必须重走一次。
        """
        url = self.SEARCH_URL.format(quote(name))
        cur = self._current_url(ws_url)
        if not reload and "/s?" in cur and self._norm_name(name) in self._norm_name(unquote(cur)) \
                and self._search_page_rendered(ws_url):
            return True
        self._execute_js(ws_url, "(function(){window.location.href=%s;})()" % json.dumps(url))
        for _ in range(20):        # 最多等 10 秒：被弹去登录页也算落地
            time.sleep(0.5)
            cur = self._current_url(ws_url)
            if "/s?" in cur or "/login" in cur:
                return True
        return False

    def _read_search_page(self, ws_url, name: str, wait: float = 10.0,
                         reload: bool = False, quiet: bool = False) -> dict:
        """读当前标签的搜索结果页，判断这次查询到底给了什么。

        返回 {"state": ..., "pid": "", "text": ""}
          found   结果区里有公司（pid 已按公司名匹配挑好）
          none    页面明确“无搜索结果”且结果区确实没有这家——这才是真查无此公司
          pending 还在渲染/正文太短/页面结构不认，不能下结论（既不写未找到也不当成限流）
          login   被弹到登录页（登录态失效）
        没单独返 blocked：调用方自己用 _detect_block 看验证页，避免两套判据打架。

        结果区的取法刻意做得宽：href 里的 company_detail 优先，没命中再扫全页带公司名的
        文字链（从 href/属性里抽 pid，抽不到就记 href），避免爱企查改版或后台标签被节流时，
        只靠一个选择器把“没认出来”误判成“查无此公司”——那样整份名单会被烧成未找到。
        """
        self._goto_search_page(ws_url, name, reload=reload)
        want = json.dumps([str(name)[:8], self._norm_name(name)[:8]], ensure_ascii=False)
        probe = """
            (function() {
                var b = document.body ? (document.body.innerText || '') : '';
                var out = {u: location.href, len: b.length, c: [], t: 0, none: 0};
                var want = %s;
                var as = document.querySelectorAll('a[href*="company_detail"]');
                for (var i = 0; i < as.length && i < 12; i++) {
                    var h = as[i].getAttribute('href') || '';
                    var m = h.match(/company_detail_(\\d+)/);
                    if (m) out.c.push([m[1], (as[i].innerText || '').replace(/\\s+/g, ' ').slice(0, 60)]);
                }
                if (!out.c.length) {
                    var all = document.querySelectorAll('a');
                    out.t = all.length;
                    var re1 = /company_detail_(\\d+)/;
                    var re3 = /^https?:\\/\\//;
                    for (var j = 0; j < all.length && out.c.length < 12; j++) {
                        var a = all[j];
                        var tx = (a.innerText || '').replace(/\\s+/g, ' ').trim();
                        if (!tx || tx.length < 4 || !/(公司|集团|企业|厂|院|所|社)/.test(tx)) continue;
                        var ok = false;
                        for (var k = 0; k < want.length; k++) {
                            if (want[k] && tx.indexOf(want[k]) >= 0) { ok = true; break; }
                        }
                        if (!ok) continue;
                        var pid = '';
                        var href = a.getAttribute('href') || '';
                        var abs = a.href || '';
                        var mm = abs.match(re1) || href.match(re1);
                        if (mm) pid = mm[1];
                        else {
                            var attrs = ['data-pid','data-id','data-comid','baseurl','data-href'];
                            for (var q = 0; q < attrs.length; q++) {
                                var mv = (a.getAttribute(attrs[q]) || '').match(re1);
                                if (mv) { pid = mv[1]; break; }
                            }
                        }
                        // 抽不到 id 就把整条链接当地址用（绝对地址去掉域名前缀，统一走 "@" 分支）；
                        // 什么链接都没有的条目不能当结果——它会导到一个拼出来的假地址
                        if (!pid) {
                            if (!href || href.indexOf('javascript') === 0 || href === '#') continue;
                            pid = '@' + (re3.test(abs) ? abs.slice(abs.indexOf('/', 8)) : href);
                        }
                        out.c.push([pid, tx.slice(0, 60)]);
                    }
                }
                if (/没有找到相关|无搜索结果|未找到相关|暂无匹配|相关的公司信息/.test(b)) out.none = 1;
                return JSON.stringify(out);
            })()
        """ % want
        polls = max(4, int(wait / 0.7) + 1)      # 按轮数而不是挂钟，避开后台标签被节流时的慢渲染
        empty_polls = 0
        last = {"state": "pending", "pid": "", "text": ""}
        for _ in range(polls):
            raw = self._execute_js(ws_url, probe)
            try:
                info = json.loads(raw) or {}
            except Exception:
                info = {}
            url = str(info.get("u") or "")
            cands = [(str(p), str(x)) for p, x in (info.get("c") or [])]
            if "/login" in url:
                return {"state": "login", "pid": "", "text": ""}
            if cands:
                self._unknown_streak = 0
                pid, text, exact = self._pick_pid(name, cands)
                if not exact:
                    self._log(f"  ⚠ 结果页没找到名字完全对得上的公司，用第一条：{text[:30]}")
                return {"state": "found", "pid": pid, "text": text}
            # 结果区一个都没抽到：只有页面自己说了“无搜索结果”且正文已渲染完整才算查无
            rendered = "/s?" in url and int(info.get("len") or 0) >= SEARCH_PAGE_READY_LEN
            if rendered and info.get("none"):
                empty_polls += 1
                last = {"state": "none", "pid": "", "text": ""}
                if empty_polls >= SEARCH_PAGE_NONE_POLLS:
                    return last
            elif rendered and int(info.get("t") or 0) >= 20:
                # 页面上有二十多个链接却一个公司结果都抽不到：是我们不认这个页面，
                # 不是平台查无——绝不能拿它写未找到
                self._unknown_streak += 1
                if not quiet and self._unknown_streak <= 2:
                    # 同一个死页按 0.7 秒一轮刷十分钟会堆出近千条同样的日志，
                    # 只报头两次，剩下的靠证据文件说话
                    self._log("  ⚠ 搜索结果页认不出结果条目（改版或平台静默拒绝），"
                              "先按限流退避而不是写未找到")
                if self._unknown_streak >= SEARCH_PAGE_UNKNOWN_LIMIT:
                    self._dump_search_page_evidence(ws_url, name, info)
                last = {"state": "pending", "pid": "", "text": ""}
            else:
                empty_polls = 0
            time.sleep(0.7)
        return last

    def _ensure_request_observer(self, ws_url):
        """在本标签装一个只记录不干预的请求观测（每个页面装一次）

        拿不到数据时，“联想接口压根没发请求”与“请求发了但平台回了空”是完全不同的问题，
        前者是页面/组件坏了（重刷就好），后者是限流（只能等）。光看 DOM 分不出来，
        这里把本标签发过的接口地址与状态码留若干条，随证据一起落盘。
        """
        try:
            self._execute_js(ws_url, """
                (function() {
                    if (window.__aqcObs) return 'already';
                    window.__aqcObs = [];
                    var push = function(m, u, s) {
                        try {
                            window.__aqcObs.push({m: m, u: String(u || '').slice(0, 200), s: s, t: Date.now()});
                            if (window.__aqcObs.length > 14) window.__aqcObs.shift();
                        } catch (e) {}
                    };
                    var O = window.XMLHttpRequest;
                    if (O) {
                        var oo = O.prototype.open, os = O.prototype.send;
                        O.prototype.open = function(m, u) { this.__m = m; this.__u = u; return oo.apply(this, arguments); };
                        O.prototype.send = function() {
                            var self = this;
                            this.addEventListener('loadend', function () { push(self.__m, self.__u, self.status); });
                            return os.apply(this, arguments);
                        };
                    }
                    if (window.fetch) {
                        var of = window.fetch;
                        window.fetch = function(input, init) {
                            var u = (input && input.url) || input;
                            var m = (init && init.method) || 'GET';
                            return of.apply(this, arguments).then(function (r) { push(m, u, r && r.status); return r; },
                                function (e) { push(m, u, -1); throw e; });
                        };
                    }
                    return 'on';
                })()
            """)
        except Exception:
            pass

    def _observed_requests(self, ws_url) -> list:
        """读回本标签发过的接口请求（观测器没装上也只是空列表，不影响采集）"""
        try:
            raw = self._execute_js(ws_url,
                                   "(function(){return JSON.stringify(window.__aqcObs || []);})()")
            data = json.loads(raw or "[]")
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _dump_search_page_evidence(self, ws_url, name: str, info: dict):
        """把“认不出结果条目”当时的页面实况存进会话目录，一次运行只存一份

        这种状态只有两种可能：平台改版（我们的选择器要跟着改）或静默拒绝（限流，
        页面正常但不给数据）。两者的处置完全不一样，而从日志上分不出来，
        所以这里落一份能直接看的现场：没证据时只能靠猜，有证据就能一次改对。
        """
        if self._evidence_dumped:
            return
        self._evidence_dumped = True
        base = Path(self.diag_dir) if self.diag_dir else self._data_dir
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = base / f"爱企查_结果页证据_{stamp}.txt"
        try:
            base.mkdir(parents=True, exist_ok=True)
            snap = self._execute_js(ws_url, """
                (function() {
                    var b = document.body ? (document.body.innerText || '') : '';
                    var as = document.querySelectorAll('a');
                    var pref = {}, cd = 0;
                    for (var i = 0; i < as.length; i++) {
                        var h = (as[i].getAttribute('href') || '').slice(0, 40);
                        pref[h] = (pref[h] || 0) + 1;
                        if (h.indexOf('company_detail') >= 0) cd++;
                    }
                    var tops = Object.keys(pref).sort(function (x, y) { return pref[y] - pref[x]; })
                        .slice(0, 15).map(function (k) { return k + ' ×' + pref[k]; });
                    return JSON.stringify({
                        u: location.href, t: document.title, len: b.length, links: as.length,
                        company_detail: cd, iframes: document.querySelectorAll('iframe').length,
                        body: b.slice(0, 6000), tops: tops,
                        html: (document.documentElement.outerHTML || '').slice(0, 300000)
                    });
                })()
            """) or "{}"
            s = json.loads(snap) if isinstance(snap, str) else {}
            text = "\n".join([
                f"爱企查搜索结果页抽不到条目 — {datetime.now():%Y-%m-%d %H:%M:%S}",
                f"公司名：{name}",
                f"页面地址：{s.get('u')}",
                f"页面标题：{s.get('t')}",
                f"正文长度：{s.get('len')}  链接总数：{s.get('links')}  "
                f"company_detail 链接：{s.get('company_detail')}  iframe：{s.get('iframes')}",
                f"探针读数：{json.dumps(info, ensure_ascii=False)}",
                "",
                "【链接前缀 TOP15】",
                "\n".join(str(x) for x in (s.get("tops") or [])),
                "",
                "【正文前 6000 字】",
                str(s.get("body") or ""),
                "",
                "【本标签最近的接口请求（状态码）】",
                "\n".join(f"{r.get('m', 'GET')} {r.get('u')} → {r.get('s')}"
                          for r in self._observed_requests(ws_url)) or "（没装观测器/无请求）",
                "",
                "【页面 HTML 前 300000 字】",
                str(s.get("html") or ""),
            ])
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            self._log(f"🔎 已存下当时的搜索结果页证据：{path}")
            self._log("   如果在浏览器里手工搜同一家能进详情、工具却抽不到，"
                      "请把这个文件发回来，就能定出是改版还是平台静默拒绝")
        except Exception as e:
            self._log(f"  ⚠ 结果页证据写入失败：{e}")

    def _pick_pid(self, name: str, cands: list):
        """从结果条目里挑一家：公司名互包含的优先，否则退而取第一条

        反向包含（条目名是公司名的前缀）只在条目名够长时才信，免得“某某公司”这种
        短名字匹配到另一家不相干的公司。
        """
        want = self._norm_name(name)
        for pid, text in cands:
            got = self._norm_name(text)
            if want and got and (want in got or (len(got) >= 8 and got in want)):
                return pid, text, True
        pid, text = cands[0]
        return pid, text, False

    def _goto_detail(self, ws_url, pid: str) -> str:
        """直接把本标签导到详情页（点联想之外第二条路），等到数据块出现再返回 URL

        pid 以 "@" 开头时是结果条目自己的链接（抽不到 company_detail_<id> 时的退路），
        相对地址补成绝对地址；导航失败（href 不可用）就返回空串交上层换路。
        """
        pid = str(pid or "")
        expect = "company_detail"
        if pid.startswith("@"):
            url = pid[1:]
            if url.startswith("/"):
                url = self.BASE_URL + url
            if "aiqicha.baidu.com" not in url:
                return ""
            expect = url.rstrip("/").rsplit("/", 1)[-1][:16] or "company"
        else:
            url = self.DETAIL_URL.format(pid)
        try:
            self._execute_js(ws_url, "(function(){window.location.href=%s;})()" % json.dumps(url))
        except Exception:
            return ""
        got = self._wait_detail_url(ws_url, 12.0, expect)
        if not got:
            return ""
        for _ in range(12):        # 详情数据比 URL 渲染得晚，直接取会拿到空字段
            time.sleep(0.5)
            try:
                if self._execute_js(ws_url,
                                    "(function(){return document.querySelectorAll('.info-item').length>0;})()"):
                    break
            except Exception:
                break
        return got

    def _detail_via_search(self, ws_url, name: str):
        """走搜索结果页把本标签带到详情页（验证码过后联想常被抑制但搜索仍可用）

        返回 ("detail"/"empty"/"stop", 当前应用的爱企查标签 ws)。等待恢复期间
        _heal_block 可能把会话换到另一个标签，所以句柄必须回传给调用方，否则会
        拿着已作废的标签继续读一个死页面。
        含义："detail" 已在详情页 / "empty" 确认查无此公司 / "stop" 仍在限流，
        这一家不能写入结果，否则整份名单会被烧成一片“未找到”。
        """
        for _ in range(2):
            ws_url = self._get_ws_url() or ws_url
            st = self._read_search_page(ws_url, name)
            if st["state"] == "found":
                if self._goto_detail(ws_url, st["pid"]):
                    return "detail", ws_url
                self._log("  详情页没打开，再试一次搜索结果页")
                continue
            if st["state"] == "login":
                self._log("  ⚠ 页面被弹到爱企查登录页：登录态已失效")
                if not self._ask_login("爱企查登录态已失效（页面被跳转到登录页）"):
                    return "stop", ws_url
                continue
            if st["state"] == "none":
                # 一次空结果不能直接定“未找到”：先确认再来一次独立查询还是空
                if self._read_search_page(ws_url, name, 6.0, reload=True)["state"] == "none":
                    return "empty", ws_url
                continue
            reason = self._detect_block(ws_url) or \
                "⏳ 平台限流中：搜索联想与搜索结果页都没给出公司"
            hit = {}

            def ready():
                # 必须 reload：地址栏已经停在这一家的 /s?q= 时不重走一次，
                # 抽到的永远是同一个旧 DOM——看着在退避重试，其实一个请求都没发
                s = self._read_search_page(ws_url, name, 8.0, reload=True, quiet=True)
                if s["state"] == "found":
                    hit["pid"] = s["pid"]
                    return True
                return s["state"] in ("none", "login")

            # 先洗一次标签再开始长等：过码后停在回跳页/组件已死时，这一步就能直接解决，
            # 不必让名单在死页面上挂满十分钟
            self._soft_reset_tab(ws_url)
            if not self._wait_unblock_limited(reason, max_wait=900,
                                              interval=REPROBE_INTERVAL, ready=ready):
                return "stop", self._get_ws_url() or ws_url
            if hit.get("pid"):
                ws_url = self._get_ws_url() or ws_url
                if self._goto_detail(ws_url, hit["pid"]):
                    return "detail", ws_url
                continue
            # ready 认为“能判定了”（none/login）：回到循环开头重新读一次，
            # 连续两轮都空才会写未找到
        return "stop", ws_url

    def _reach_detail(self, ws_url, name: str, suggest_ready: bool):
        """把本标签带到这家公司详情页。返回 (是否到位, 详情页 URL, 结束状态, 当前标签 ws)"""
        if suggest_ready and self._click_first_autocomplete(ws_url):
            url = self._wait_detail_url(ws_url, 5.0)
            if url:
                return True, url, "", ws_url
            self._log("  点了联想但没跳详情，改用搜索结果页")
        outcome, ws_url = self._detail_via_search(ws_url, name)
        if outcome == "detail":
            return True, self._current_url(ws_url), "", ws_url
        if outcome == "empty":
            return False, "", "未找到", ws_url
        if outcome == "stop":
            return False, "", "拦截未解除", ws_url
        return False, "", "跳转失败", ws_url

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
                    # 并行模式才装：单平台时序一个字都不能改，而观测器只能随
                    # 页面加载重装，装在水合之后刚好覆盖后续几家公司的请求
                    self._ensure_request_observer(ws_url)
                
                # 检查页面状态
                page_check = self._check_page_status(ws_url)
                if page_check == 'blocked':
                    self._log("️ 检测到反爬虫，尝试刷新...")
                    self._execute_js(ws_url, "(function() { location.reload(); })()")
                    time.sleep(5)

                # 本标签已经停在验证页上：原地等人工完成验证，不拿这一家去凑数
                block = self._detect_block(ws_url)
                if block:
                    if not self._wait_unblock_limited(block):
                        result["状态"] = "拦截未解除"
                        return result
                    # 验证完了不等于页面能用：本标签往往还停在验证回跳页上，
                    # 不先洗回干净首页，这一家还是“卡在搜索公司名”
                    self._soft_reset_tab(ws_url)
                    ws_url = self._get_or_create_homepage_tab()
                
                # 3. 清空并输入公司名
                self._clear_and_input_search(ws_url, name)
                
                # 4. 等待自动补全出现（智能等待：最多 3 秒）
                autocomplete_found = self._wait_autocomplete(ws_url, 3.0)
                
                # 并行模式下首页可能刚好在注入时才挂载完，重试两次（单平台模式保持原有时序不变）
                if not autocomplete_found and self.attach_mode:
                    for _retry in range(2):
                        # 页面不可用就先整页重刷：过码后本标签常停在“地址正常但组件已死”的
                        # 首页上，原地重注几次公司名都无效（手工刷新后一搜就好就是这个道理）
                        if not self._page_hydrated(ws_url):
                            self._soft_reset_tab(ws_url)
                        self._wait_page_hydrated(ws_url, 10)
                        self._log(f"  未出现联想，第{_retry + 1}次重试输入：{name}")
                        self._clear_and_input_search(ws_url, name)
                        autocomplete_found = self._wait_autocomplete(ws_url, 3.0)
                        if autocomplete_found:
                            break

                if not autocomplete_found:
                    # 联想没出来不能直接定“未找到”：验证码刚过时页面正常、输入框里还是我们注的
                    # 公司名但联想恒为空是常态（此时手工搜公司名仍能进详情），所以走搜索结果页兜底
                    why = "联想被平台抑制" if self._suggest_suppressed(ws_url, name) else "联想未出（可能是查无此公司）"
                    self._log(f"  {why}：{name}，改走搜索结果页")

                # 5-6. 进详情页：优先点联想，进不去就走搜索结果页（两条路都不给结果时绝不写未找到）
                self._log("等待跳转到详情页...")
                reached, detail_url, status, ws_url = self._reach_detail(ws_url, name, autocomplete_found)
                if not reached:
                    if status == "未找到":
                        self._log(f"搜索结果页确认查无此公司：{name}")
                    elif status == "跳转失败":
                        self._log("未跳转到详情页")
                    result["状态"] = status
                    return result
                company_id_match = re.search(r'company_detail_(\d+)', detail_url or "")
                if company_id_match:
                    result["搜索结果 URL"] = f"company_detail_{company_id_match.group(1)}"
                
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
            # 夜间没人点“去登录/继续采集”，在这一家死等不如把名单交给企知道
            if not self._stop_requested:
                self._give_up_left(companies, start_index, "爱企查未登录且长时间无人确认")
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
            # 被拦时不推进名单、不写入空记录，等人工在浏览器完成验证后从这一家继续；
            # 但夜间没人点验证，所以超过 block_wait_limit 就收掉这一路、把名单交给企知道
            block = self._detect_block()
            if block:
                if not self._wait_unblock_limited(block):
                    if not self._stop_requested:
                        self._give_up_left(companies, idx)
                    break
            else:
                # 能正常开工就说明这一段拦截过去了，别拿旧时间戳去裁下一段
                self._blocked_at = 0.0

            # 刚过完验证就按原速接着跑，几乎一定会再弹一次验证（实测三五分钟一轮）
            self._cooldown_wait()

            try:
                result = self.search_company(name)
                if result.get("状态") == "拦截未解除":
                    # 搜索途中被拦且等不到恢复：夜间无人处理时收掉这一路、名单交企知道
                    if not self._stop_requested:
                        self._give_up_left(companies, idx)
                    if self.handoff is None:
                        self._log("⏹ 拦截未解除，本轮采集到此结束（这一家未写入结果，续采会从这里开始）")
                    break
                if block_guard.is_shell_record(result):
                    # 不写入空壳，也不推进名单：登录补上后从这一家重新采
                    self._shell_streak += 1
                    self._log(f"  ⚠ 详情字段全空（连续 {self._shell_streak} 家），这一家先不写入结果")
                    if self._shell_streak >= block_guard.SHELL_STREAK_LIMIT \
                            and not self._shell_gate():
                        self._log("⏹ 未登录问题未解决，本轮采集到此结束")
                        if not self._stop_requested:
                            self._give_up_left(
                                companies, idx,
                                f"连续 {self._shell_streak} 家详情字段全空且长时间无人确认")
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
