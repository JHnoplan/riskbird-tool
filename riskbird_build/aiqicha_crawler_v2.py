"""
aiqicha_crawler_v2.py - 爱企查爬虫 v2（基于 CDP 直接通信）
使用 Chrome DevTools Protocol 直接通信，无需 Selenium
"""

import json
import time
import re
import threading
import requests
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
                    self._log(f"复用现有标签页：{tab.get('title', 'N/A')[:30]}")
                    return tab.get("webSocketDebuggerUrl")
        
        # 创建新标签页（详情页或没有可用首页标签）
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
    def start_browser(self, headed: bool = True):
        """启动浏览器（检查 Chrome 是否已运行）"""
        with self._browser_lock:
            try:
                tabs = self._get_tabs()
                if tabs:
                    self._log("Chrome 已运行")
                    return True
            except Exception:
                pass
            self._log("请先手动启动 Chrome（带 --remote-debugging-port=9233）")
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
        """搜索公司并采集数据（通过自动补全直跳详情页）"""
        result = {
            "公司名": name,
            "状态": "", "法定代表人": "", "注册资本": "",
            "成立日期": "", "统一社会信用代码": "",
            "电话": "", "邮箱": "", "官网": "", "地址": "",
            "数据来源": "爱企查", "搜索结果 URL": "",
        }
        
        for attempt in range(self.max_retries):
            try:
                # 1. 创建新标签页到爱企查首页
                self._log(f"搜索：{name}")
                ws_url = self._create_tab(self.BASE_URL)
                if not ws_url:
                    self._log("无法创建标签页")
                    result["状态"] = "浏览器错误"
                    return result
                
                self._log(f"标签页 WebSocket: {ws_url[:60]}...")
                
                # 2. 等待页面完全加载
                time.sleep(8)
                
                # 3. 点击搜索框聚焦并清空
                self._execute_js(ws_url, """
                    (function() {
                        var input = document.querySelector('#aqc-search-input');
                        if (!input) input = document.querySelector('#aqc-header-search-input');
                        if (input) {
                            input.click();
                            input.focus();
                            input.value = '';
                        }
                    })()
                """)
                time.sleep(1)
                
                # 4. 输入公司名（使用 JavaScript 触发 Vue 事件）
                self._execute_js(ws_url, """
                    (function() {
                        var input = document.querySelector('#aqc-search-input');
                        if (!input) input = document.querySelector('#aqc-header-search-input');
                        if (input) {
                            // 使用原生方式设置值
                            var nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                            nativeInputValueSetter.call(input, '""" + name + """');
                            // 触发 InputEvent（Vue 3 监听这个）
                            input.dispatchEvent(new InputEvent('input', {
                                bubbles: true,
                                cancelable: true,
                                inputType: 'insertText',
                                data: '""" + name + """'
                            }));
                            input.dispatchEvent(new Event('change', { bubbles: true }));
                        }
                    })()
                """)
                time.sleep(5)  # 等待自动补全出现
                
                # 5. 查找自动补全结果并点击第一个（iView 组件）
                company_id = self._execute_js(ws_url, """
                    (function() {
                        // 查找 iView 自动补全下拉项
                        var items = document.querySelectorAll('.ivu-select-item, .sug-company-line');
                        if (items.length > 0) {
                            var first = items[0];
                            // 点击下拉项
                            first.click();
                            
                            // 尝试从父元素获取 href
                            var parent = first;
                            while (parent) {
                                if (parent.tagName === 'A') {
                                    var href = parent.getAttribute('href') || '';
                                    var match = href.match(/company_detail_(\\d+)/);
                                    if (match) return match[1];
                                }
                                parent = parent.parentElement;
                            }
                            return 'clicked:' + first.textContent.trim().substring(0, 50);
                        }
                        return null;
                    })()
                """)
                
                if not company_id:
                    self._log(f"未找到自动补全结果：{name}")
                    result["状态"] = "未找到"
                    self._close_tab(ws_url)
                    return result
                
                # 处理点击后的情况
                if company_id.startswith("clicked:"):
                    # 已点击，等待页面导航
                    self._log("已点击下拉项，等待跳转...")
                    time.sleep(5)
                    
                    # 从 URL 获取 company_id
                    current_url = self._execute_js(ws_url, "(function() { return window.location.href; })()")
                    match = re.search(r'company_detail_(\d+)', current_url or '')
                    if match:
                        company_id = match.group(1)
                    else:
                        self._log(f"无法从 URL 获取公司 ID: {current_url}")
                        result["状态"] = "跳转失败"
                        self._close_tab(ws_url)
                        return result
                
                self._log(f"找到公司 ID: {company_id}")
                result["搜索结果 URL"] = f"company_detail_{company_id}"
                
                # 6. 关闭当前标签，创建详情页标签
                self._close_tab(ws_url)
                time.sleep(1)
                
                detail_url = self.DETAIL_URL.format(company_id)
                ws_url = self._create_tab(detail_url)
                if not ws_url:
                    self._log("无法创建详情页标签")
                    continue
                
                # 7. 等待详情页加载
                time.sleep(8)
                
                # 8. 采集详情页数据
                detail_data = self._extract_detail_data(ws_url, name)
                result.update(detail_data)
                result["状态"] = "成功"
                
                self._close_tab(ws_url)
                self._log(f"采集完成：{name}")
                return result
                
            except Exception as e:
                self._log(f"采集失败（第{attempt+1}次）：{e}")
                if attempt < self.max_retries - 1:
                    time.sleep(2)
        
        result["状态"] = "失败"
        return result
    
    def _extract_detail_data(self, ws_url, company_name: str) -> dict:
        """从详情页提取数据"""
        data = {
            "法定代表人": "", "注册资本": "", "成立日期": "",
            "统一社会信用代码": "", "电话": "", "邮箱": "",
            "官网": "", "地址": "",
        }
        
        # 提取基本信息（使用实际 DOM 结构）
        info_json = self._execute_js(ws_url, """
            (function() {
                var result = {
                    legalPerson: '', registeredCapital: '', establishDate: '',
                    creditCode: '', phone: '', email: '', website: '', address: ''
                };
                
                // 从 .info-item 元素中提取
                var items = document.querySelectorAll('.info-item');
                for (var i = 0; i < items.length; i++) {
                    var text = items[i].textContent.trim();
                    
                    // 统一社会信用代码
                    if (text.indexOf('统一社会信用代码') >= 0) {
                        var match = text.match(/统一社会信用代码[：:]\\s*(\\S+)/);
                        if (match) result.creditCode = match[1];
                    }
                    // 电话
                    if (text.indexOf('电话') >= 0 && text.indexOf('手机') >= 0) {
                        var match = text.match(/手机[号]?[：:]?\\s*(\\d+)/);
                        if (match) result.phone = match[1];
                    }
                    // 法定代表人
                    if (text.indexOf('法定代表人') >= 0) {
                        // 匹配 "法定代表人：秦\\n 秦双玉" - 取换行后的完整姓名
                        var match = text.match(/法定代表人[：:]\\s*[\\u4e00-\\u9fa5][\\s\\n]+([\\u4e00-\\u9fa5]{2,4})/);
                        if (match) {
                            result.legalPerson = match[1];
                        } else {
                            // 简单匹配 "法定代表人：秦双玉"
                            var match2 = text.match(/法定代表人[：:]\\s*([\\u4e00-\\u9fa5]{2,4})/);
                            if (match2) result.legalPerson = match2[1];
                        }
                    }
                    // 邮箱
                    if (text.indexOf('邮箱') >= 0) {
                        var match = text.match(/邮箱[：:]\\s*([\\w@.]+)/);
                        if (match) result.email = match[1];
                    }
                    // 注册资本
                    if (text.indexOf('注册资本') >= 0) {
                        var match = text.match(/注册资本[：:]\\s*(.+?)(?:\\s|$)/);
                        if (match) result.registeredCapital = match[1].trim();
                    }
                    // 网址
                    if (text.indexOf('网址') >= 0) {
                        var match = text.match(/网址[：:]\\s*(http\\S+)/);
                        if (match) result.website = match[1];
                    }
                    // 地址
                    if (text.indexOf('地址') >= 0 && text.length > 10) {
                        var match = text.match(/地址[：:]\\s*([\\u4e00-\\u9fa5].+)/);
                        if (match) result.address = match[1].trim().substring(0, 100);
                    }
                }
                
                // 从 .info-item 获取注册时间
                var allItems = document.querySelectorAll('.info-item');
                for (var i = 0; i < allItems.length; i++) {
                    var text = allItems[i].textContent.trim();
                    if (text.indexOf('注册时间') >= 0 || text.indexOf('成立日期') >= 0) {
                        var match = text.match(/(\\d{4}-\\d{2}-\\d{2})/);
                        if (match) result.establishDate = match[1];
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
            except json.JSONDecodeError:
                pass
        
        return data
    
    # ─ 采集控制 ──
    def crawl(self, companies: list, delay: float = 0.5):
        """批量采集"""
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
                      "统一社会信用代码", "电话", "邮箱", "官网", "地址", "状态", "数据来源"]
            ws.append(headers)
            
            for i, r in enumerate(self.results, 1):
                ws.append([
                    i, r.get("公司名", ""), r.get("法定代表人", ""),
                    r.get("注册资本", ""), r.get("成立日期", ""),
                    r.get("统一社会信用代码", ""), r.get("电话", ""),
                    r.get("邮箱", ""), r.get("官网", ""), r.get("地址", ""),
                    r.get("状态", ""), r.get("数据来源", "")
                ])
            
            wb.save(filepath)
            self._log(f"已导出 {len(self.results)} 条数据到 {filepath}")
            return True
        except Exception as e:
            self._log(f"导出失败：{e}")
            return False
