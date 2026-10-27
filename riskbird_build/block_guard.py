"""
block_guard.py - 平台拦截（安全验证/风控）识别与等待
三个爬虫共用：风鸟、企知道（Selenium）与爱企查（裸 CDP）都用同一套判据和等待策略。

设计要点：
1) 判据只用「当前页面自己的 URL / 标题」，绝不做整页正文关键词匹配。
   企业站正常页面里普遍内嵌“安全验证”“请输入验证码”之类前端表单文案，
   按全文匹配会把好页面误判成拦截，反而静默丢掉有效数据（本项目踩过这个坑）。
2) 被拦时原地等待人工完成验证，不推进名单、不写入空记录 —— 否则一份名单会被
   风控瞬间烧成一片“未找到”，数量在涨但字段全空。
3) 等待循环必须能被“停止”打断，且提醒用户：验证页反复出现通常是请求过快。
4) 登录态用「浏览器里登录之后才会下发的 Cookie」判断，不用页面文案：导航栏里
   “登录/注册”常驻，按文案会把已登录会话误判成未登录；而匿名会话同样有名为 token
   的 Cookie（企知道实测如此），所以只能看 accessToken/BDUSS 这类登录凭证。
"""

import json
import re
import threading
import time
import urllib.request

def norm_company(name) -> str:
    """公司名归一化：用于“这家是不是已经采过”的跨平台比对。

    采集结果里的公司名来自页面标题/表头，与名单里的写法可能有空格、全半角括号差异，
    不去一律就会把已采过的公司再采一遍。不抹括号内容，只抹无语义差异的字符。
    """
    s = str(name or "").strip()
    if not s:
        return ""
    for a, b in (("（", "("), ("）", ")"), ("\u3000", " "), ("　", " ")):
        s = s.replace(a, b)
    return re.sub(r"\s+", "", s)


# 命中即确定是拦截页（URL 特征最可靠）
BLOCK_URL_MARKERS = (
    "wappass.baidu.com",     # 百度安全验证
    "/static/captcha",
    "captcha/tuxing",
    "verifycloud",
    "/antispam",
    "seccode",
)
BLOCK_TITLE_MARKERS = ("百度安全验证", "安全验证", "身份核实", "环境异常", "验证中心")
# 正文特征只在「页面极短」时参与判断：正常详情页正文都是几 KB 起步
BLOCK_BODY_MARKERS = ("请关闭浏览器的调试窗口", "访问过于频繁", "滑动验证", "拖动滑块")
SHORT_BODY_LIMIT = 600


def _match(url: str, title: str, body_len: int, body_head: str, extra_markers=None):
    url = (url or "").lower()
    if any(m in url for m in BLOCK_URL_MARKERS):
        return "页面被跳转到安全验证页（百度验证）"
    title = title or ""
    if any(m in title for m in BLOCK_TITLE_MARKERS):
        return f"当前页面是验证页：{title[:24]}"
    head = body_head or ""
    markers = tuple(BLOCK_BODY_MARKERS) + tuple(extra_markers or ())
    if body_len is not None and body_len < SHORT_BODY_LIMIT and any(m in head for m in markers):
        return "页面被平台风控拦截（提示关闭调试窗口/访问频繁）"
    return ""


def detect_selenium(driver):
    """Selenium 会话：只读自己页面的 URL 与标题，返回拦截原因（无拦截返回空串）"""
    try:
        url = driver.current_url or ""
    except Exception:
        return ""
    try:
        title = driver.title or ""
    except Exception:
        title = ""
    reason = _match(url, title, None, "")
    if reason:
        return reason
    # 标题没命中时，再看正文是否短到像插页
    try:
        text = driver.find_element("tag name", "body").text or ""
    except Exception:
        return ""
    return _match(url, title, len(text), text[:200])


def detect_cdp(execute_js, extra_markers=None):
    """CDP 会话：execute_js 传本爬虫的 JS 执行方法，只看 URL/标题/正文长度

    extra_markers: 按页面上下文追加的插页文案。只有「正文极短」时才参与判断，
    沿用既有的防误伤约束（企业站正常页面里本来就写着“安全验证”之类的表单文案）。
    """
    state = execute_js(
        "(function(){var b=document.body?(document.body.innerText||''):'';"
        "return JSON.stringify({u:location.href,t:document.title,l:b.length,h:b.slice(0,200)});})()"
    )
    if not state:
        return ""
    try:
        info = json.loads(state)
    except Exception:
        return ""
    return _match(str(info.get("u") or ""), str(info.get("t") or ""),
                  info.get("l"), str(info.get("h") or ""), extra_markers)


def wait_unblock(reason, log, emit_blocked, is_stopped, probe,
                 max_wait: int = 1800, interval: int = 5, first_wait: int = 25,
                 ready=None, heal=None, heal_every: int = 30) -> bool:
    """被拦截（或平台在限流）时原地等恢复。返回 True 表示确实可以继续采，False 表示该结束本轮。

    log:            写日志的回调（str）
    emit_blocked:   通知界面的回调（str），由界面提示"请在浏览器完成验证"
    is_stopped:     返回 bool 的回调，用户点了停止要能立刻退出
    probe:          返回拦截原因字符串的回调，返回空串即代表验证页已消失
    ready:          能力探针（返回 bool）。给了就必须真正拿得动数据才算恢复：
                    平台限流常常不弹验证页，只看页面签名会「1 秒即判定已解除」，
                    于是同一家在死页面上反复空转，日志一直报「限制」却始终不前进。
    heal:           返回 bool 的自愈回调（如把会话切到已经验证通过的标签页 /
                    重刷当前标签）。验证页挂在别的标签时，本会话标签会一直被判
                    拦截而死等到超时，所以每隔 heal_every 秒主动试一次。
                    探针失败（页面签名正常但拿不到数据）时也照样试：过完验证码
                    最常见的是本标签停在验证回跳页/组件已死，不重刷就一直拿不到数据。
    """
    if emit_blocked:
        try:
            emit_blocked(reason or "页面被平台拦截")
        except Exception:
            pass
    log(f"🛑 {reason}")
    if ready is None:
        log("   已暂停推进名单（不会把空记录写进结果）。请在浏览器窗口里完成验证，验证通过后自动继续采集。")
    else:
        log("   已暂停推进名单（不会把空记录写进结果）。工具会一边退避一边实测能不能拿数据，"
            "确认恢复后自动继续；浏览器里如果有验证页请顺手完成。")
    waited = 0
    said_throttle = False
    while waited < max_wait:
        step = interval if waited >= first_wait else 1
        for _ in range(step):
            if is_stopped():
                log("⏹ 用户在等待验证期间停止采集")
                return False
            time.sleep(1)
            waited += 1
        if is_stopped():
            log("⏹ 用户在等待验证期间停止采集")
            return False
        still = probe()
        need_heal = bool(heal) and waited >= heal_every
        if still and need_heal:
            try:
                fixed = bool(heal())
            except Exception:
                fixed = False
            if fixed:
                log("   已尝试自愈（回到可用页面/换回正常标签），重新检查拦截状态…")
                still = probe()
        if not still:
            if ready is None:
                log(f"✅ 拦截已解除（等待约 {waited} 秒），继续采集")
                return True
            try:
                works = bool(ready())
            except Exception:
                works = False
            if works:
                log(f"✅ 平台已恢复，能正常拿到数据（等待约 {waited} 秒），继续采集")
                return True
            if need_heal:
                # 验证页没了但探针还是拿不到数据：把标签重刷回可用状态再试，
                # 而不是只当“还在限流”干等到超时
                try:
                    healed = bool(heal())
                except Exception:
                    healed = False
                if healed:
                    log("   页面正常但还拿不到数据，已重刷标签重新检查…")
            if not said_throttle:
                log("   验证页已经没了，但平台仍然不给数据（这是限流不是验证），继续退避等待…")
                said_throttle = True
        else:
            said_throttle = False
        if waited == first_wait:
            log("   ⚠ 验证页反复出现通常是请求过快：可先停止，调大“采集间隔”后再续采")
        if waited % 60 < interval:
            log(f"   仍在等待恢复…已等 {waited} 秒")
    log(f"⚠ 等待 {max_wait // 60} 分钟仍未解除拦截，本轮到此结束（已采集的数据不受影响）")
    return False


# ── 登录态：判据与人工确认闸门 ─────────────────────────────────
# 实测依据（同一账号登录前后各取一次浏览器 Cookie）：
#   企知道 登录后有 accessToken / creditNo / param_sign，未登录时这几个整体缺失，
#          只剩匿名的 token；此时详情页只回电话，状态/法人/注册资本/邮箱/地址全空。
#   爱企查 登录凭证是 HttpOnly 的 BDUSS，必须走 CDP Network.getCookies 才读得到。
#   风鸟   登录后有 userinfo / first-authorization。
# 注：风鸟“仅列表页”模式合法地产出只有状态的记录，所以空壳闸门只给企知道/爱企查用。
LOGIN_COOKIE_NAMES = {
    "企知道": ("accessToken", "creditNo"),
    "爱企查": ("BDUSS",),
    "风鸟": ("userinfo", "first-authorization"),
}

# 空壳判据用的硬字段：这三个全空 = 详情页只给了个名字/电话，没给任何工商信息。
# （不用“状态”参与判定：爱企查把状态填成“成功/未找到”，风鸟列表页模式下只有状态有值）
SHELL_KEY_FIELDS = ("法定代表人", "注册资本", "地址")
# 这些状态是“已知的查无/失败”，由各自的退避/计数分支处理，不当成空壳
SHELL_IGNORE_STATUSES = ("未找到", "拦截未解除", "失败", "浏览器错误", "跳转失败",
                         "额度用尽待重试")
# 连续这么多家全空才判定“不是个别公司的问题”，避免误伤本来就信息稀少的公司
SHELL_STREAK_LIMIT = 2


def login_state(cookie_names, platform: str):
    """返回 (状态, 说明)，状态为 'ok' / 'missing' / 'unknown'。

    cookie_names 为 None 表示压根读不到 Cookie（浏览器没开、会话断了），
    这时返回 'unknown'：宁可不拦，也不要因为读不到就误判未登录。
    """
    required = LOGIN_COOKIE_NAMES.get(platform)
    if not required or cookie_names is None:
        return "unknown", ""
    names = {str(n or "") for n in cookie_names}
    hit = [r for r in required if r in names]
    if hit:
        return "ok", f"已检测到登录凭证 {hit[0]}"
    return "missing", f"浏览器里没有 {required[0]} 登录凭证，当前是未登录状态"


def is_shell_record(result) -> bool:
    """关键字段全空的记录（不写入结果，否则名单会被烧成一片空数据）"""
    if not isinstance(result, dict):
        return True
    if result.get("错误") or str(result.get("状态") or "") in SHELL_IGNORE_STATUSES:
        return False      # 这些由各自分支处理，不参与空壳计数
    return not any(str(result.get(k) or "").strip() for k in SHELL_KEY_FIELDS)


# 双标签并行时共享调试 Chrome 的端口（与 dual_session.DUAL_PORT 一致）
SHARED_DEBUG_PORT = 9233


def debug_chrome_alive(port=None, timeout: float = 1.0) -> bool:
    """调试端口上是否已有 Chrome 在跑。

    同一份 user-data-dir 只能被一个 Chrome 实例占用，共享浏览器在跑时再开一个
    用同一目录的 Chrome 会立即退出，Selenium 报 “session not created: Chrome
    instance exited”，日志里看着像“没登录”，其实是自己把浏览器挤掉了。
    """
    try:
        url = "http://127.0.0.1:%d/json/version" % (port or SHARED_DEBUG_PORT)
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return bool(json.loads(resp.read().decode("utf-8", "ignore")).get("Browser"))
    except Exception:
        return False


def saved_cookie_names(path) -> list:
    """读已保存 Cookie 文件里的字段名（只看名字判登录凭证，不碰浏览器）

    文件早期只存了 list，后来存的是 {"cookies": [...]}，两种都要能读；
    读不到返回 None（当成 unknown，不拿它拦人）。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    items = data.get("cookies", []) if isinstance(data, dict) else data
    if not isinstance(items, list) or not items:
        return None
    return [c.get("name") for c in items if isinstance(c, dict)]


class LoginGate:
    """未登录时的“人工确认”闸门：采集线程原地等待，界面弹窗给出三条出路。

    界面弹窗用 login_required_signal 走自己的提示（不复用拦截红条那套文案，
    两者对用户的指引不一样：“验证”是去浏览器点几下、“登录”要重新登账号）。

    界面上的“继续采集”“停止”就是这个闸门的出口，所以不需要额外的按钮：
      emit_required 通知界面弹窗 → 用户选
        · 去浏览器登录：闸门继续原地等（不推进名单、不写空记录），
          等用户登录完点“继续采集”，resolve('login_done') 会重新验一次 Cookie；
        · 仍然继续采集：resolve('continue')；
        · 停止采集：resolve('stop')。
    """

    def __init__(self, platform, log, emit_required, is_stopped, recheck,
                 emit_cleared=None):
        self.platform = platform
        self._log = log
        self._emit_required = emit_required
        self._is_stopped = is_stopped
        self._recheck = recheck
        self._emit_cleared = emit_cleared
        self._lock = threading.Lock()
        self._ev = threading.Event()
        self._choice = ""

    def resolve(self, choice: str):
        """界面调用：把用户的选择交回采集线程"""
        with self._lock:
            self._choice = choice
            ev = self._ev
        ev.set()

    def pending(self) -> bool:
        with self._lock:
            return not self._ev.is_set() and self._choice == ""

    def ensure(self, reason: str, max_wait: int = 1800, rounds: int = 3) -> bool:
        """True 表示可以继续采集，False 表示本轮应就此结束（名单没有白烧）"""
        for _ in range(max(1, rounds)):
            if self._is_stopped():
                return False
            with self._lock:
                self._ev = threading.Event()
                self._choice = ""
                ev = self._ev
            try:
                self._emit_required(reason)
            except Exception:
                self._log(f"🛑 {reason}：无法通知界面，为避免白烧名单本轮采集结束")
                return False
            self._log(f"🛑 {reason}")
            self._log("   已暂停推进名单（不会把空记录写进结果）。请在浏览器窗口里登录该平台，"
                      "登录成功后点界面上的“继续采集”从这一家接着采")
            if not self._wait(ev, max_wait):
                return False
            with self._lock:
                choice = self._choice
            if choice == "continue":
                self._log("⚠ 按你的选择继续采集：未登录时详情字段可能仍然是空的")
                self._cleared(reason)
                return True
            if choice == "login_done":
                state, detail = self._recheck()
                if state != "missing":
                    self._log("✅ 已检测到登录凭证，继续采集")
                    self._cleared(reason)
                    return True
                reason = f"仍未登录：{detail or self.platform}"
                self._log("⚠ " + reason)
                continue
            return False
        self._log(f"⚠ 多次确认 {self.platform} 仍未登录，本轮采集到此结束（名单没有白烧）")
        return False

    def _cleared(self, reason):
        """闸门放行后通知界面收起提示（不管是“登录好了”还是“选择继续采集”）"""
        if self._emit_cleared:
            try:
                self._emit_cleared(reason)
            except Exception:
                pass

    def _wait(self, ev, max_wait) -> bool:
        waited = 0
        while waited < max_wait:
            if ev.wait(1):
                return True
            if self._is_stopped():
                self._log("⏹ 用户在等待登录期间停止了采集（已采集数据不受影响）")
                return False
            waited += 1
            if waited % 120 == 0:
                self._log(f"   仍在等待 {self.platform} 登录…已等 {waited // 60} 分钟")
        self._log(f"⚠ 等待 {max_wait // 60} 分钟仍未登录，本轮到此结束（已采集数据不受影响）")
        return False
