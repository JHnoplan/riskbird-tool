"""离线验证平台拦截识别、联想被抑制的处置、空白窗口清理与界面提示。

覆盖（都不启动浏览器）：
1. 判据只认「页面自己的 URL / 标题 / 极短正文」，正常长页面里内嵌"安全验证"文案不能误判；
2. wait_unblock：先通知界面、期间可被停止打断、恢复后返回 True；
3. 被拦时不推进名单、不写空记录，解除后从卡住的那一家继续；
4. 爱企查"联想被抑制"与"真查无此公司"要能分开（前者等人工，后者记未找到）；
5. 界面收到 blocked_signal 后红条提示"请在浏览器完成验证"，解除后自动收掉；
6. 空白窗口清理只关 about:blank / chrome://newtab，带内容页面与 keep_ids 不碰；
7. 站点拒绝浏览器：判据命中与防误杀、跳板真的换来源、拒绝页现场留证；
8. 永久判据：产品不许调本类没有的方法、夹具不许比产品更富（两条各自带反向对照）；
9. 归还标签：必须带 targetId 发命令，并且拿标签列表复验，关不掉就开口。
"""
import json
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication  # noqa: E402

app = QApplication([])

import block_guard as bg  # noqa: E402
import dual_session as ds  # noqa: E402
import aiqicha_crawler_v2 as A  # noqa: E402
from riskbird_gui import MainWindow  # noqa: E402

# ── 1. 判据：命中与不误杀 ────────────────────────────
def cdp_state(url, title, body_len, head):
    def _js(_expr):
        return json.dumps({"u": url, "t": title, "l": body_len, "h": head})
    return _js


assert bg.detect_cdp(cdp_state("https://wappass.baidu.com/static/captcha/tuxing_v2.html",
                               "百度安全验证", 120, "请完成验证")) , "wappass 页必须判为拦截"
assert bg.detect_cdp(cdp_state("https://aiqicha.baidu.com/company_detail_123",
                               "环境异常", 9000, "x")) , "标题为环境异常应判为拦截"
assert bg.detect_cdp(cdp_state("https://www.qizhidao.com/companies/1",
                               "某企业 - 企知道", 300, "请关闭浏览器的调试窗口后继续访问")) , "短正文风控插页应判为拦截"
# 反向：正常企业详情页里内嵌"安全验证/滑动验证"前端文案，正文很长，绝不能误判
normal = bg.detect_cdp(cdp_state("https://www.qizhidao.com/companies/1",
                                "某某机械有限公司-企知道", 18000,
                                "登录 安全验证 滑动验证 联系我们 主营产品 经营范围 " * 20))
assert normal == "", f"正常长页面被误判成拦截：{normal}"
assert bg.detect_cdp(cdp_state("https://aiqicha.baidu.com/", "爱企查", 20000, "搜索")) == ""
print("① 拦截判据：命中 3 类、正常页面不误杀 ✓")

# ── 2. wait_unblock：通知界面 / 停止打断 / 恢复继续 ────
real_sleep = time.sleep
bg.time.sleep = lambda s: None          # 测试里不让它真的等
try:
    logs, emits = [], []
    # 2a 一直不解除 → 超时返回 False，且只通知界面一次
    ok = bg.wait_unblock("页面被跳转到安全验证页", logs.append, emits.append,
                         lambda: False, lambda: "still blocked", max_wait=5, interval=1, first_wait=2)
    assert ok is False and len(emits) == 1, f"超时应返回 False 且只通知一次：{emits}"
    assert any("已暂停推进名单" in m for m in logs), "日志要说明不会写空记录"

    # 2b 用户停止 → 立刻退出
    stop_flag = {"v": False}
    calls = {"n": 0}

    def stop_soon():
        calls["n"] += 1
        if calls["n"] > 2:
            stop_flag["v"] = True
        return stop_flag["v"]

    ok = bg.wait_unblock("拦截", logs.append, lambda r: None, stop_soon,
                         lambda: "blocked", max_wait=1000, interval=1, first_wait=1)
    assert ok is False, "停止应结束等待"

    # 2c 人工完成验证 → 返回 True 继续
    seq = {"n": 0}

    def probe_recover():
        seq["n"] += 1
        return "" if seq["n"] >= 3 else "blocked"

    ok = bg.wait_unblock("拦截", logs.append, lambda r: None, lambda: False,
                         probe_recover, max_wait=1000, interval=1, first_wait=1)
    assert ok is True, "解除后应返回 True 继续采集"
finally:
    bg.time.sleep = real_sleep
print("② 等待人工验证：超时/停止/解除三条路径都对 ✓")

# ── 3. 被拦时不推进名单、不写空记录 ────────────────────
c = A.AiQiChaCrawlerV2()
c.signals = None
# crawl() 开头有一道登录闸门，会真的去请求共享浏览器的 9233 调试端口；
# 离线测试不能依赖“本机刚好开着那个浏览器”，直接把登录判据桩成已登录
c.check_login = lambda: ("ok", "测试桩：视为已登录")
c.delay_between = 0
c.max_retries = 0
seen = []


def fake_search(name):
    seen.append(name)
    # 带齐关键字段：只有状态“成功”、详情字段全空的记录会被当成未登录空壳，
    # 那是另一套管线的职责（见 test_login_gate_offline），这里只测拦截行为
    return {"公司名": name, "状态": "成功", "法定代表人": "张三",
            "注册资本": "100万", "地址": "某市某路1号", "数据来源": "爱企查"}


c.search_company = fake_search
names = ["甲公司", "乙公司", "丙公司"]

# 3a 第 2 家时被拦且未解除：只应该真的采到 1 家，被拦那家不写入结果
gate = {"n": 0}


def detect_when_second():
    gate["n"] += 1
    return "页面被跳转到安全验证页（百度验证）" if gate["n"] == 2 else ""


c._detect_block = detect_when_second
c._wait_unblock = lambda reason, max_wait=1800: False
c.crawl(list(names), 0)
assert [r["公司名"] for r in c.results] == ["甲公司"], f"被拦那家不能写进结果：{c.results}"
assert seen == ["甲公司"], f"名单不能往下烧：{seen}"
print("③ 被拦时：空记录不写入、名单不推进 ✓")

# 3b 人工完成后从卡住那一家继续（续采起点 = 已写入条数）
gate2 = {"n": 0}


def detect_clear_soon():
    gate2["n"] += 1
    return "拦截" if gate2["n"] == 1 else ""


c2 = A.AiQiChaCrawlerV2()
c2.signals = None
c2.check_login = lambda: ("ok", "测试桩：视为已登录")
c2.delay_between = 0
c2.max_retries = 0
seen2 = []
c2.search_company = lambda n: seen2.append(n) or {
    "公司名": n, "状态": "成功", "法定代表人": "张三", "注册资本": "100万", "地址": "某路1号"}
c2._detect_block = detect_clear_soon
waited = []
c2._wait_unblock = lambda reason, max_wait=1800: waited.append(reason) or True
c2._suggest_suppressed = lambda ws, n: False
c2.crawl(list(names), 1)      # 续采：从卡住的"乙公司"开始
assert seen2 == ["乙公司", "丙公司"], f"续采应包含卡住那一家：{seen2}"
assert waited, "应走过一次等待人工验证"
print("③b 解除后从卡住的那一家继续 ✓")

# ── 4. 联想被抑制 vs 真查无此公司 ─────────────────────
d = A.AiQiChaCrawlerV2()


def js_with(value, n_items):
    def _js(url, expr):
        return json.dumps({"v": value, "n": n_items})
    return _js


d._execute_js = js_with("上海新时达电气股份有限公司", 0)
assert d._suggest_suppressed("ws", "上海新时达电气股份有限公司") is True, "输入框还是我们那家但联想为空 → 被抑制"
d._execute_js = js_with("上海新时达电气股份有限公司", 3)
assert d._suggest_suppressed("ws", "上海新时达电气股份有限公司") is False, "有联想项就不是抑制"
# 真查无此公司：前端会把输入框换成它推荐的内容
d._execute_js = js_with("上海新时达电气有限公司", 0)
assert d._suggest_suppressed("ws", "上海不存在的一家集团公司") is False, "输入框被换掉说明是真查无此公司"
print("④ 联想抑制与“真无此公司”可区分 ✓")

# ── 5. 界面提示 ──────────────────────────────────────
w = MainWindow()
w.aiqicha_crawler.signals.blocked_signal.emit("页面被跳转到安全验证页（百度验证）")
app.processEvents()
assert [i[0] for i in w._blocked_info] == ["爱企查"], f"应记下被拦平台：{w._blocked_info}"
assert not w.blocked_lbl.isHidden(), "红条应显示"
assert "请在浏览器窗口完成验证" in w.blocked_lbl.text(), w.blocked_lbl.text()
assert "百度验证" in w.blocked_lbl.text(), "提示条应带上具体原因"
assert w._blocked_box is not None, "应给出提示框"
# 双标签下两个平台分别计数，一个解除后提示不能提前消失
w.qizhidao_crawler.signals.blocked_signal.emit("搜索联想被平台抑制（多为请求过快触发风控）")
app.processEvents()
assert [i[0] for i in w._blocked_info] == ["爱企查", "企知道"]
assert "没有验证页时稍候会自动恢复" in w.blocked_lbl.text(), "抑制类原因要说明会自动恢复"
w.aiqicha_crawler.signals.block_cleared_signal.emit("x")
app.processEvents()
assert not w.blocked_lbl.isHidden(), "还有一个平台在被拦，提示不能收掉"
assert [i[0] for i in w._blocked_info] == ["企知道"]
w.qizhidao_crawler.signals.block_cleared_signal.emit("x")
app.processEvents()
assert w.blocked_lbl.isHidden(), "全部解除后提示应收起"
assert w._blocked_box is None, "提示框应关闭"
# 同一平台重复上报只更新原因，不应把提示列表越堆越长
w.qizhidao_crawler.signals.blocked_signal.emit("拦截一")
w.qizhidao_crawler.signals.blocked_signal.emit("拦截二")
app.processEvents()
assert len(w._blocked_info) == 1 and w._blocked_info[0][1] == "拦截二", w._blocked_info
# 停止/结束时清理，避免红条残留
w.aiqicha_crawler.signals.blocked_signal.emit("拦截")
app.processEvents()
w._clear_blocked_state()
assert w._blocked_info == [] and w.blocked_lbl.isHidden()
print("⑤ 界面提示：显示/区分平台/解除/收尾 ✓")

# ── 6. 空白窗口清理：只关空白、keep 生效、不误伤，而且「关掉」只能拿重查当证据 ──
# 与 b2bcollect 第 32j 条同一族（构建前硬门会扫 riskbird_build）：旧写法把「请求没抛异常」
# 当成「已关掉」（closed += 1），而 Chrome 的 /json/close 是异步的、对不存在的 target 也回
# HTTP 200；列表查询失败又吞成 []，于是「剩 0 个页面」这句在控制面不通时照样念得出口。
ALL_TABS = [
    {"type": "page", "id": "t1", "url": "chrome://newtab/", "title": "新标签页"},
    {"type": "page", "id": "t2", "url": "about:blank", "title": ""},
    {"type": "page", "id": "t3", "url": "https://www.qizhidao.com/companies/1", "title": "企知道"},
    {"type": "page", "id": "t4", "url": "https://aiqicha.baidu.com/", "title": "爱企查"},
    {"type": "page", "id": "t5", "url": "chrome://new-tab-page/", "title": ""},
    {"type": "service_worker", "id": "s1", "url": "https://x/y", "title": ""},
]
GONE, SHOWN_ONCE, closed = set(), set(), []
# MODE：lag＝关掉后下一次列表查询里仍看得见（异步落地）；resist＝回执漂亮但永远不掉下去；
# boom_at＝第 N 次列表查询直接抛挂（控制面不通）。
MODE = {"lag": False, "resist": (), "boom_at": 0}
LIST_QUERIES = [0]
real_get = ds.requests.get


class FakeResp:
    def __init__(self, payload, text=""):
        self._p, self.text = payload, text

    def json(self):
        return self._p


def fake_get(url, timeout=None):
    if url.endswith("/json/list"):
        LIST_QUERIES[0] += 1
        if MODE["boom_at"] and LIST_QUERIES[0] >= MODE["boom_at"]:
            raise ConnectionError("127.0.0.1 拒绝连接")
        shown = [dict(t) for t in ALL_TABS
                 if t["id"] not in GONE or t["id"] in SHOWN_ONCE]
        SHOWN_ONCE.clear()
        return FakeResp(shown)
    if "/json/close/" in url:
        tid = url.rsplit("/", 1)[-1]
        closed.append(tid)
        if tid in MODE["resist"]:
            return FakeResp("", text="Target is closing")   # 回执漂亮，这一枚就是不掉下去
        if MODE["lag"]:
            SHOWN_ONCE.add(tid)                             # 本次重查还看得见
        GONE.add(tid)
        return FakeResp("", text="Target is closing")
    return FakeResp({})


ds.requests.get = fake_get
try:
    def _reset():
        GONE.clear(); SHOWN_ONCE.clear(); closed.clear()
        LIST_QUERIES[0] = 0

    msgs = []
    n = ds.cleanup_blank_tabs(msgs.append, keep_ids=["t5"])
    assert closed == ["t1", "t2"], f"只应关空白页且尊重 keep：{closed}"
    assert n == 2 and any("已清理 2 个空白窗口" in m for m in msgs), msgs
    assert not any("没做成" in m for m in msgs), msgs
    # 没有空白页时不应该有多余动作（t1/t2/t5 这三枚空白全从列表里拿掉，只剩两个会话页）
    _reset()
    GONE.update(["t1", "t2", "t5"])
    assert ds.cleanup_blank_tabs(msgs.append) == 0 and closed == []

    # 6b 「只信回执」那一族：回执 200 但这一枚就是不掉下去 → 不许记成已清理，必须开口
    _reset(); MODE["resist"] = ("t1", "t2")
    msgs = []
    n = ds.cleanup_blank_tabs(msgs.append, keep_ids=["t5"])
    assert n == 0, f"回执 200 不等于关掉，两枚都还在列表里却报了战绩：{n}"
    assert any("没做成" in m and "仍在列表里" in m for m in msgs), msgs
    MODE["resist"] = ()

    # 6c 我们动手前它就不在了（Chrome 对不存在的 target 也回 200）→ 不计进本次战绩
    _reset()

    def _fake_get_nosuch(url, timeout=None):
        if "/json/close/" in url:
            closed.append(url.rsplit("/", 1)[-1])
            return FakeResp("", text="No such target id: xxx")
        return fake_get(url, timeout)

    _keep = ds.requests.get
    ds.requests.get = _fake_get_nosuch
    msgs = []
    n = ds.cleanup_blank_tabs(msgs.append)
    ds.requests.get = _keep
    assert n == 0 and not any("已清理" in m for m in msgs), f"本来就不在的算到我们头上了：{n} {msgs}"

    # 6d 开头那份枚举就读不到 → 不许一声不响地回 0（那听起来像「已经干净」）
    _reset(); MODE["boom_at"] = 1
    msgs = []
    assert ds.cleanup_blank_tabs(msgs.append) == 0
    assert any("没读到标签列表" in m and "ConnectionError" in m for m in msgs), msgs

    # 6e 关闭发了、复查那一步读不到 → 不许说「已清理 N」，要说没核对上
    _reset(); MODE["boom_at"] = 2
    msgs = []
    n = ds.cleanup_blank_tabs(msgs.append)
    assert n == 0 and any("没送达" in m and "不确定还在不在" in m for m in msgs), f"{n} {msgs}"
    assert not any("已清理" in m for m in msgs), msgs
    MODE["boom_at"] = 0

    # 6f 异步落地（关掉后第一次重查仍看得见、下一次才消失）：这一枚确实关掉了，
    # 只查一次的写法会在这一形上响假警（第十二轮实机中国制造网那句就长这样）；
    # 而旧写法根本不复查，连这个输入都接不住
    _reset(); MODE["lag"] = True
    msgs = []
    n = ds.cleanup_blank_tabs(msgs.append, keep_ids=["t5"])
    MODE["lag"] = False
    assert n == 2 and any("已清理 2 个空白窗口" in m for m in msgs), f"异步落地被判成没关掉：{n} {msgs}"
    assert not any("没做成" in m for m in msgs), msgs
finally:
    ds.requests.get = real_get
print("⑥ 空白窗口清理：只关空白、keep 生效、回执不算数（重查才算）、读不到要说没读到 ✓")

# ── 7. 站点拒绝浏览器：判据命中/防误杀/与风控类分开/退路真生效 ───────
# 2026-11-18 用户实机：点「打开爱企查」，Chrome 能开页面但一瞬间提示不能使用
# 此浏览器。旧判据里一个字都没登记 → 被当成「页面正常但没数据」，名单会被
# 静默烧成一片「查不到」。新判据必须同时交出反向探针，证明它不是空跑。
import ast       # noqa: E402（7c 要现算产品与夹具里的 self.X 调用，不拿写死的名单冒充）
import tempfile  # noqa: E402
import shutil   # noqa: E402（上面那几段自己不清临时目录，本段收尾要用）
from pathlib import Path  # noqa: E402（留证文件要读回来看内容，只看文件名判不了质）

_r = bg.detect_cdp(cdp_state("https://aiqicha.baidu.com/", "", 137,
                            "不能使用此浏览器，请更换其他浏览器访问"))
assert bg.is_browser_reject(_r), f"拒绝页文案必须判成「浏览器被拒」这一类：{_r}"
# 反向 1：同样的文案出现在正文很长的正常页面里不能判（企业站页脚常写浏览器建议）
assert not bg.detect_cdp(cdp_state("https://aiqicha.baidu.com/company_detail_1",
                                  "某公司 - 爱企查", 9000,
                                  "不支持此浏览器 建议使用 Chrome 浏览器")), \
    "长页面里的浏览器建议文案不能误判成拦截"
# 反向 2：短页面但属于风控插页 → 判风控类，两类不能混（混了就等错东西）
_r2 = bg.detect_cdp(cdp_state("https://aiqicha.baidu.com/s?q=x", "", 200,
                             "请关闭浏览器的调试窗口后继续访问"))
assert _r2 and not bg.is_browser_reject(_r2), f"风控插页不能被归成拒绝类：{_r2}"
# 反向 3：去掉那句文案就什么都不判（判据不是靠「短页面一律拦」充数的）
assert not bg.detect_cdp(cdp_state("https://aiqicha.baidu.com/", "", 137,
                                  "正在加载")), "没有拒绝文案的短页不能被判拦截"
print("⑦a 拒绝类判据：命中/长页不误杀/与风控类分开/去掉文案即不判 ✓")

# ── 7b. _confirm_open：正常页不白跳、拒绝页跳一次、跳不动也要给人下一步 ──
_tmp7 = tempfile.mkdtemp(prefix="bg_reject_")
# 假标签按「真 CDP 边界」建模：Page.navigate 会改地址，页面自己写 location.href 也会改
# 地址，读地址就读这一格。上一版是给假对象挂了一支产品当时并不存在的 c._navigate，
# 于是「百度跳板」离线走得通、真机第一步就 AttributeError——夹具比产品更富就是
# 假绿的来源，下面 7c 把这件事钉成永久判据。
_tab7 = {"url": "https://aiqicha.baidu.com/", "rej": True, "flip": False}
_cmds7 = []                       # 产品真发出去的 CDP 命令（断言要看方法名与参数）


def _js7(expr):
    # 分支顺序不能改：取现场信息那句 JS 里就含 `u:location.href`，一旦把「读地址」排在
    # 它前面，这句会被误答成一个 URL 字符串，detect_cdp 解析不到就进不了拒绝分支。
    if "readyState" in expr:
        return "complete"
    if "JSON.stringify" in expr:
        return json.dumps({"u": _tab7["url"], "t": "", "ua": "Chrome", "r": "",
                           "l": 137, "h": "不能使用此浏览器，请更换其他浏览器访问"})
    if "outerHTML" in expr:
        return "<html><body>不能使用此浏览器</body></html>"
    if "location.href=" in expr.replace(" ", ""):      # 页面自己发起的跳转（跳板第二跳）
        _tab7["url"] = expr.split("=", 1)[1].split(")")[0].strip("'\"; ")
        if "aiqicha" in _tab7["url"] and _tab7["flip"]:
            _tab7["rej"] = False          # 从百度跳进来之后站点不再拒
        return None
    if "location.href" in expr:
        return _tab7["url"]
    return ""


def _cmd7(method, params=None):
    _cmds7.append((method, dict(params or {})))
    if method == "Page.navigate":
        _tab7["url"] = str((params or {}).get("url") or "")
    return {"result": {"data": ""}}


def _make_c7(flip_after_hop):
    c = A.AiQiChaCrawlerV2()
    c.signals = None
    c.diag_dir = _tmp7
    c.logs = []
    c._log = lambda m: c.logs.append(m)
    c._wait_page_hydrated = lambda ws, n: True
    # 签名与产品一致：_execute_js 是 (ws_url, script) 两参数，而 detect_cdp
    # 拿到的执行器只有一个 script 参数。上一版这里混用一个 1 参数函数，结果留证
    # 和跳板都抛 TypeError（被产品自己接住了，但假场景就测不成真路径）。
    c._execute_js = lambda ws, expr: _js7(expr)
    c._send_command = lambda ws, method, params=None: _cmd7(method, params)
    c._detect_block = lambda ws=None: (bg.detect_cdp(_js7) if _tab7["rej"] else "")
    _tab7["flip"] = flip_after_hop
    _tab7["url"] = "https://aiqicha.baidu.com/"
    return c


def _hops():
    """产品真发出去的「换页面」命令（Page.navigate 的目标地址），按顺序。"""
    return [p.get("url") for m, p in _cmds7 if m == "Page.navigate"]


_sleep7 = time.sleep
time.sleep = lambda s: None          # 跳板与导航复验里有真实等待，测试里不让它真等
try:
    _tab7["rej"] = False
    _cmds7.clear()
    c7 = _make_c7(False)
    assert c7._confirm_open("WS") is True, "正常页面必须报打开成功"
    assert not _hops(), f"没被拒时绝不能多跳一次百度（白等还闪页面）：{_hops()}"
    assert any("爱企查页面已打开" in m for m in c7.logs), c7.logs

    _tab7["rej"] = True
    _cmds7.clear()
    c7b = _make_c7(True)
    assert c7b._confirm_open("WS") is True
    assert _hops() and "baidu.com" in _hops()[0], f"被拒后必须真的从百度跳板走一次：{_hops()}"
    assert any("跳进来后页面正常" in m for m in c7b.logs), c7b.logs
    # 跳板的全部意义在「换来源」：只有第一跳可以用 CDP 导航，第二跳必须由百度
    # 页面自己改 location.href。否则等于地址栏直接敲，Referer 依旧为空，这一跳白做。
    assert len(_hops()) == 1, f"第二跳不该再用 CDP 导航（来源就丢了）：{_hops()}"
    _dumped = [x for x in os.listdir(_tmp7) if x.startswith("爱企查_浏览器被拒证据")]
    assert _dumped, f"拒绝页现场必须留证（下次排查不能靠猜）：{os.listdir(_tmp7)}"
    _ev = (Path(_tmp7) / _dumped[0]).with_suffix(".txt").read_text(encoding="utf-8")
    assert "不能使用此浏览器" in _ev and "无 Referer" in _ev, \
        f"留证要能看出结论与来源，否则下次还是靠猜：{_ev[:120]}"
    assert any("已留现场证据" in m for m in c7b.logs), c7b.logs
    # 同一轮只存一份：再来一次不得堆文件
    _tab7["rej"] = True
    c7b._confirm_open("WS")
    assert len([x for x in os.listdir(_tmp7) if x.startswith("爱企查_浏览器被拒证据")]) == 1, \
        "一次运行只存一份证据（堆文件就是在淹现场）"

    _tab7["rej"] = True
    _cmds7.clear()
    c7c = _make_c7(False)            # 跳板也不能翻页的硬场景
    assert c7c._confirm_open("WS") is True
    assert _hops(), "硬场景也要先试过退路"
    assert any("跳板退路无效" in m for m in c7c.logs), c7c.logs
    assert any("手动刷新" in m for m in c7c.logs), \
        f"退路无效时必须给出能动手的下一步，不能只说失败：{c7c.logs}"
    assert not any("页面已打开" in m for m in c7c.logs), \
        "被拒的页面绝不能报「已打开」（结论与现场不同源就是误导）"
finally:
    time.sleep = _sleep7
shutil.rmtree(_tmp7, ignore_errors=True)
print("⑦b _confirm_open：正常不白跳/被拒跳一次并留证/退路无效也说清下一步 ✓")

# ── 7c. 永久判据：产品不许调本类没有的方法，夹具也不许比产品更富 ───────
# 7b 那一族缺陷（产品调不存在的 _navigate，而夹具正好自己补了一支把它盖住）离线全绿、
# 真机才炸，根因是「没人要求被测的那个类真的有这个方法」。两条判据方向相反，
# 合起来才把这一族堵死。两条都各自带反向对照，不允许自己是空跑。
def _phantom_self_calls(src_text, cls):
    """现算 src_text 里被当成 self.X(...) 调用、而 cls 上既无此方法又无实例属性赋值的名字。"""
    tree = ast.parse(src_text)
    called, assigned = set(), set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and isinstance(n.func.value, ast.Name) and n.func.value.id == "self":
            called.add(n.func.attr)
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) \
                and n.value.id == "self" and isinstance(n.ctx, ast.Store):
            assigned.add(n.attr)
    return sorted(x for x in called if not hasattr(cls, x) and x not in assigned)


def _fixture_legs(obj, cls):
    """假对象上挂的私有可调用里，真类没有的那几支（= 夹具自己造的新腿）。"""
    return sorted(k for k, v in vars(obj).items()
                  if k.startswith("_") and callable(v) and not hasattr(cls, k))


_phantom = _phantom_self_calls(open(A.__file__, encoding="utf-8").read(), A.AiQiChaCrawlerV2)
assert not _phantom, \
    f"产品在调本类根本没有的方法（走到就是 AttributeError，不许再靠离线放过）：{_phantom}"
_richer = sorted({k for _fx in (c7, c7b, c7c) for k in _fixture_legs(_fx, A.AiQiChaCrawlerV2)})
assert not _richer, \
    f"夹具比产品更富（挂了真类没有的方法，离线假绿、真机必炸）：{_richer}"
# 反向对照 A：判据自己不能是空跑——造一段调不存在方法的源码，必须被点名
_probe_a = _phantom_self_calls("class X:\n    def f(self):\n        self._no_such_leg()\n",
                               A.AiQiChaCrawlerV2)
assert _probe_a == ["_no_such_leg"], f"幻影调用判据失效（造出来的假方法却没抓到）：{_probe_a}"
# 反向对照 B：临时给假对象挂一支真类确实没有的腿，必须被点名
c7._probe_fake_leg = lambda ws: None
_probe_b = _fixture_legs(c7, A.AiQiChaCrawlerV2)
del c7._probe_fake_leg
assert _probe_b == ["_probe_fake_leg"], f"夹具富化判据失效（挂了产品没有的腿却没抓到）：{_probe_b}"
print("⑦c 永久判据：产品幻影调用为零、夹具不富过产品，两条各自拿反向对照自证 ✓")

# ── 7d. _close_tab：必须带 targetId 发命令，并且拿标签列表复验 ───────
# 与跳板同一轮真机查出的第二处：旧写法 params 传空、又无条件 return True，而
# `_send_command` 只递原始响应不对 error 抛异常，于是「没报错」被当成「关掉了」，
# 实测在共享 Chrome 里攒下两枚没人认领的标签。这一处之能藏住，是因为离线零覆盖。
_tid7 = "aabbccddeeff00112233445566778899aabbccdd"
_tabs7 = [{"id": _tid7, "url": "https://aiqicha.baidu.com/"}]
_sent7 = []
c7d = A.AiQiChaCrawlerV2()
c7d.signals = None
c7d.logs = []
c7d.pin_tab_id = ""          # 故意复位：与 close_browser 调它之前的状态一致（旧写法在这拿到空 id）
c7d._log = lambda m: c7d.logs.append(m)
c7d._send_command = lambda ws, method, params=None: (
    _sent7.append((method, dict(params or {}))), {"result": {}})[1]
c7d._get_tabs = lambda: list(_tabs7)
_sleep7d = time.sleep
time.sleep = lambda s: None          # 复验要等三轮，测试里不让它真等
try:
    assert c7d._close_tab("ws://127.0.0.1:9255/devtools/page/" + _tid7) is False, \
        "标签还在列表里就必须说没关掉（拿没报错冒充做成过就是这个形状）"
    assert _sent7 and _sent7[0][0] == "Target.closeTarget" \
        and _sent7[0][1].get("targetId") == _tid7, f"closeTarget 必须带上那一枚的 targetId：{_sent7}"
    assert any("没关掉" in m for m in c7d.logs), f"没关掉要开口说，不能静默：{c7d.logs}"
    _tabs7.clear()                   # Chrome 真的收走了
    assert c7d._close_tab("ws://127.0.0.1:9255/devtools/page/" + _tid7) is True, \
        "列表里已经没有它了，就该报关掉"

    # 新分支也得有人管：复验三次全抛时，既不许报关掉，也不许说「确实还挂着」
    def _boom7():
        raise RuntimeError("标签列表读不到")
    c7d._get_tabs = _boom7
    c7d.logs.clear()
    assert c7d._close_tab("ws://127.0.0.1:9255/devtools/page/" + _tid7) is False, \
        "读不到标签列表时不许报关掉（无从确认就是无从确认）"
    assert any("读不到标签列表" in m and "没法确认" in m for m in c7d.logs), \
        f"这一格要说清是「无从确认」而不是「确实还挂着」：{c7d.logs}"
finally:
    time.sleep = _sleep7d
print("⑦d _close_tab：带 targetId + 标签列表复验，关不掉就开口 ✓")

# ── 收尾：趁 QApplication 还活着把窗口拆掉（顺序与 b2bcollect_gui.main 的 finally 同一条）──
# 套件从不调 app.exec_()，也就绕过了生产那段有序收尾；而生产那段存在的理由正是「让解释器
# 自己拆 Qt 对象会撞上退出 SIGSEGV」（线上实测抓到过栈）。这里实测同样 6 次崩 1 次，且崩在
# 打印汇总**之后**——批跑只看退出码，全绿也会被判成一次失败。
# 走 hide() 而不是 close()：close 会进生产 closeEvent（等线程、弹确认框），本套件测的不是
# 退出那条路；hide 之后把引用清掉，refcount 当场让 sip 删掉 C++ 对象，此时 app 仍在。
w.hide()
w = None
app.processEvents()

print("BLOCK_OFFLINE_OK")

# 判定到此为止：屏幕上这句和批跑读到的退出码必须是同一个来源。这套件自己建过
# QApplication，而 Qt/线程在解释器收尾时的析构会随机段错误（本机实测：keyword_resume
# 全绿 88 项之后 rc=139，被批跑读成一次失败，单跑又绿——真红就是这么被当成抖动的）。
# flush 之后直接把退出码交给操作系统，不让任何收尾动作再改写判定。
sys.stdout.flush()
sys.stderr.flush()
os._exit(0)
