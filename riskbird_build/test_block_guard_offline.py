"""离线验证平台拦截识别、联想被抑制的处置、空白窗口清理与界面提示。

覆盖（都不启动浏览器）：
1. 判据只认「页面自己的 URL / 标题 / 极短正文」，正常长页面里内嵌"安全验证"文案不能误判；
2. wait_unblock：先通知界面、期间可被停止打断、恢复后返回 True；
3. 被拦时不推进名单、不写空记录，解除后从卡住的那一家继续；
4. 爱企查"联想被抑制"与"真查无此公司"要能分开（前者等人工，后者记未找到）；
5. 界面收到 blocked_signal 后红条提示"请在浏览器完成验证"，解除后自动收掉；
6. 空白窗口清理只关 about:blank / chrome://newtab，带内容页面与 keep_ids 不碰。
"""
import json
import os
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

# ── 6. 空白窗口清理 ──────────────────────────────────
TABS = [
    {"type": "page", "id": "t1", "url": "chrome://newtab/", "title": "新标签页"},
    {"type": "page", "id": "t2", "url": "about:blank", "title": ""},
    {"type": "page", "id": "t3", "url": "https://www.qizhidao.com/companies/1", "title": "企知道"},
    {"type": "page", "id": "t4", "url": "https://aiqicha.baidu.com/", "title": "爱企查"},
    {"type": "page", "id": "t5", "url": "chrome://new-tab-page/", "title": ""},
    {"type": "service_worker", "id": "s1", "url": "https://x/y", "title": ""},
]
closed = []
real_get = ds.requests.get


class FakeResp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


def fake_get(url, timeout=None):
    if url.endswith("/json/list"):
        return FakeResp(list(TABS))
    if "/json/close/" in url:
        closed.append(url.rsplit("/", 1)[-1])
        return FakeResp("Target is being attached")
    return FakeResp({})


ds.requests.get = fake_get
try:
    msgs = []
    n = ds.cleanup_blank_tabs(msgs.append, keep_ids=["t5"])
    assert closed == ["t1", "t2"], f"只应关空白页且尊重 keep：{closed}"
    assert n == 2 and any("已清理 2 个空白窗口" in m for m in msgs)
    # 没有空白页时不应该有多余动作
    closed.clear()
    TABS[:] = [t for t in TABS if t["id"] in ("t3", "t4")]
    assert ds.cleanup_blank_tabs(msgs.append) == 0 and closed == []
finally:
    ds.requests.get = real_get
print("⑥ 空白窗口清理：只关空白、keep 生效、不误伤 ✓")

print("BLOCK_OFFLINE_OK")
