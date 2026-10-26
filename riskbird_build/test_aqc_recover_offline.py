"""离线验证「爱企查过了验证码却采不动」这条链路的修复。

覆盖（都不启动浏览器，JS 与标签页都用假模型顶掉）：
1. 限流不空转：验证页消失 ≠ 恢复，必须能力探针实测能拿到数据才放行；
2. 验证页挂在别的标签：_heal_block 能把会话切到已恢复的标签/把飘走的标签导回首页；
3. 联想被抑制时改走搜索结果页仍能进详情并正常出数据（不再原地卡住）；
4. 只有搜索结果页连续几次都确认没有，才允许写「未找到」；
5. 拿不到结果又判不准时记「拦截未解除」：名单不推进、不写空记录；
6. 结果页被弹去登录页时走登录闸门，而不是把这家烧成「未找到」；
7. 结果条目按公司名挑，名字对不上时用第一条并留可核对日志；
8. 界面区分「要人去验证」与「平台限流自动退避」两种提示。
"""
import json
import os
import re
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication  # noqa: E402

app = QApplication([])

import block_guard as bg  # noqa: E402
import aiqicha_crawler_v2 as A  # noqa: E402

HOME = "https://aiqicha.baidu.com/"
NAME = "深圳市腾讯计算机系统有限公司"
DETAIL = "https://aiqicha.baidu.com/company_detail_777"

real_sleep = time.sleep
time.sleep = lambda s: None           # 测试里不让它真的等（模块级，测完还原）


class Page:
    def __init__(self, url=HOME, **kw):
        self.url = url
        # land：导航后实际停下的地址（用来模拟“搜 /s?q= 却被弹去登录页”）
        self.land = kw.get("land")
        self.title = kw.get("title", "爱企查")
        self.length = kw.get("length", 20000)
        self.head = kw.get("head", "正常页面正文")
        self.suggest = kw.get("suggest", [])
        self.links = kw.get("links", [])
        self.none = kw.get("none", 0)          # 页面自己写了“无搜索结果”
        self.anchors = kw.get("anchors", 0)    # 全页 <a> 个数（抽不到条目时的分流依据）
        self.info = kw.get("info", 5)
        self.input = kw.get("input", "")


class Browser:
    """假 CDP：按脚本内容分派，导航按队列换页"""

    def __init__(self, page=None):
        self.page = page or Page()
        self.queue = []            # 每次导航消费一个 Page（模型化"这一次加载给什么"）
        self.navs = []

    def goto(self, url):
        self.navs.append(url)
        nxt = self.queue.pop(0) if self.queue else None
        if nxt is None:
            if "aiqicha" not in url:
                nxt = Page(url, title="百度安全验证", length=120, head="请完成验证")
            elif "/login" in url:
                nxt = Page(url, title="请登录-爱企查", length=58, head="扫码登录账号登录")
            elif "/s?" in url:
                nxt = Page(url, links=[["777", NAME + "- 爱企查"]])
            elif "company_detail" in url:
                nxt = Page(url, length=9000, info=5)
            else:
                nxt = Page(url)
        nxt.url = url if not getattr(nxt, "land", None) else nxt.land
        self.page = nxt

    def js(self, expr):
        p = self.page
        if "out.c.push" in expr:                       # 搜索结果页探针
            return json.dumps({"u": p.url, "len": p.length, "c": p.links,
                               "t": p.anchors, "none": p.none})
        if "b.slice(0,200)" in expr:                   # block_guard.detect_cdp
            return json.dumps({"u": p.url, "t": p.title, "l": p.length, "h": p.head})
        if "JSON.stringify({v:" in expr:               # 联想是否被抑制
            return json.dumps({"v": p.input, "n": len(p.suggest)})
        if "return document.body ?" in expr:           # 反爬文案检查
            return p.head
        if "nativeInputValueSetter.call" in expr:
            m = re.search(r"nativeInputValueSetter\.call\(input, '([^']*)'", expr)
            if m:
                p.input = m.group(1)
            return None
        if "input.value = ''" in expr:
            p.input = ""
            return None
        if "items[0].click()" in expr:                 # 点联想第一项
            if not p.suggest:
                return None
            self.goto(p.suggest[0])
            return "clicked"
        if "items.length > 0 ? 'found'" in expr:
            return "found" if p.suggest else "waiting"
        if "items.length > 0 ? 'ready'" in expr:
            return "ready" if p.info else "loading"
        if "('.info-item').length>0" in expr:
            return bool(p.info)
        if "hasInput" in expr:                         # 页面水合判据
            return {"ready": True, "hasInput": True, "len": p.length}
        if "vueApp" in expr:
            return "ready"
        if "location.reload()" in expr:
            self.goto(p.url)
            return None
        # 导航脚本两种写法都要认：爬虫里 _heal_block 走的是带空格的 `href = '...'`
        m = re.search(r"window\.location\.href\s*=\s*['\"](https?://[^'\"]+)", expr)
        if m:
            self.goto(m.group(1))
            return None
        if "return location.href" in expr or "return window.location.href" in expr:
            return p.url
        return None


def make_crawler(browser, pin=None):
    c = A.AiQiChaCrawlerV2()
    c.signals = None
    c.attach_mode = False
    c.max_retries = 1              # search_company 的循环体只在重试次数 ≥1 时才跑
    c.delay_between = 0
    c.pin_tab_id = pin
    c.check_login = lambda: ("ok", "测试桩：视为已登录")
    c._get_tabs = lambda: [{"type": "page", "id": "T1", "url": browser.page.url,
                            "webSocketDebuggerUrl": "WS1"}]
    c._get_ws_url = lambda pattern=None: "WS1"
    c._get_or_create_homepage_tab = lambda: "WS1"   # 不碰真的 Target.createTarget
    c._execute_js = lambda ws, expr: browser.js(expr)
    c._extract_detail_data_full = lambda ws, name: {
        "法定代表人": "张三", "注册资本": "100万", "地址": "某市某路1号", "电话": "13800138000"}
    logs = []
    c._log = lambda msg: logs.append(str(msg))
    c.logs = logs
    return c


# ── 1. 限流不能"1 秒即算解除" ────────────────────────
logs, emits = [], []
tries = {"n": 0}


def ready_slow():
    tries["n"] += 1
    return tries["n"] >= 4


ok = bg.wait_unblock("⏳ 平台限流中", logs.append, emits.append, lambda: False,
                     lambda: "", max_wait=1000, interval=1, first_wait=2, ready=ready_slow)
assert ok is True, "能力探针最终给数据时应判定恢复"
assert any("验证页已经没了" in m for m in logs), f"限流期要说清是限流不是验证：{logs}"
assert any("能正常拿到数据" in m for m in logs), logs

logs.clear()
ok = bg.wait_unblock("⏳ 平台限流中", logs.append, lambda r: None, lambda: False,
                     lambda: "", max_wait=40, interval=1, first_wait=2,
                     ready=lambda: False)
assert ok is False, "页面签名干净但一直拿不到数据，绝不能当成已恢复放行"
print("① 恢复判据：只看验证页消失不再算恢复 ✓")

# ── 2/7. 自愈：验证页挂在别的标签 ────────────────────
b2 = Browser(Page(HOME))
c2 = make_crawler(b2, pin="T1")
other = {"type": "page", "id": "T2", "url": HOME, "webSocketDebuggerUrl": "WS2"}
c2._get_tabs = lambda: [{"type": "page", "id": "T1", "url": b2.page.url,
                         "webSocketDebuggerUrl": "WS1"}, other]
c2._get_ws_url = lambda pattern=None: "WS1" if c2.pin_tab_id == "T1" else "WS2"
c2._execute_js = lambda ws, expr: (b2.js(expr) if ws == "WS1"
                                   else json.dumps({"u": HOME, "t": "爱企查", "l": 20000, "h": "正常"}))
# 本标签停在 wappass 验证页：另一个标签是好的爱企查页 → 应该把会话切过去
b2.goto("https://wappass.baidu.com/static/captcha/tuxing_v2.html")
assert c2._detect_block("WS1"), "wappass 页要判为拦截"
assert c2._heal_block() is True, "有已恢复的标签就该切过去"
assert c2.pin_tab_id == "T2", c2.pin_tab_id
assert any("改用已恢复的爱企查标签" in m for m in c2.logs), c2.logs

# 单平台（没有别的标签）时：本标签飘在验证页上，导回首页也算自愈
b3 = Browser(Page("https://wappass.baidu.com/static/captcha/tuxing_v2.html",
                  title="百度安全验证", length=120))
c3 = make_crawler(b3)
assert c3._heal_block() is True and b3.page.url == HOME, f"应把飘走的标签导回首页：{b3.page.url}"
print("② 自愈：验证页在别的标签/本标签飘走都能把自己捞回来 ✓")

# ── 3. 联想被抑制 → 搜索结果页兜底照样采到 ───────────
b = Browser(Page(HOME, suggest=[], input=NAME))     # 注入后联想恒为空
c = make_crawler(b)
b.queue = [Page(HOME + "s?q=x", links=[["777", NAME + "-爱企查"]]),
           Page(DETAIL, length=9000, info=5)]
r = c.search_company(NAME)
assert r["状态"] == "成功", f"联想挂了也该靠搜索结果页采到：{r['状态']} / {c.logs[-6:]}"
assert r["搜索结果 URL"] == "company_detail_777", r
assert r["法定代表人"] == "张三", r
assert any("改走搜索结果页" in m for m in c.logs), c.logs
assert any("/s?q=" in n for n in b.navs), f"应真的走了搜索结果页：{b.navs}"
print("③ 联想被抑制：搜索结果页兜底进详情并取到数据 ✓")

# ── 3b. 结果条目里没 company_detail_ 链接：跟着条目自己的 href 走 ──
ALT = HOME + "/company/201104120034567890.html"
b = Browser(Page(HOME, suggest=[], input=NAME))
b.queue = [Page(HOME + "s?q=x", links=[["@" + ALT, NAME + "-爱企查"]]),
           Page(ALT, length=9000, info=5)]
c = make_crawler(b)
r = c.search_company(NAME)
assert r["状态"] == "成功", f"没 company_detail_ 链接也应跟着条目 href 走：{r['状态']} / {c.logs[-6:]}"
assert r["法定代表人"] == "张三", r
assert any(ALT in n for n in b.navs), b.navs
print("③b 结果条目无 company_detail_ 链接：退到条目 href 仍能进详情 ✓")

# ── 4. 只有连续确认查无，才写未找到 ──────────────────
# 联想直接不可用（组件没挂载），走搜索结果页：两次独立查询都空才算查无
b = Browser(Page(HOME, suggest=[], info=0, input=NAME))
c = make_crawler(b)
empty = Page(HOME + "s?q=x", links=[], length=6000, none=1)   # 页面自己写了“无搜索结果”
b.queue = [empty, empty, empty, empty, empty, empty]
r = c.search_company(NAME)
assert r["状态"] == "未找到", f"结果页两次都确认没有才允许记未找到：{r['状态']}"
assert any("确认查无此公司" in m for m in c.logs), c.logs

# 结果页仍在渲染（正文太短）→ 判不出查无，也不能写未找到
b = Browser(Page(HOME, suggest=[], info=0, input=NAME))
c = make_crawler(b)
stuck = Page(HOME + "s?q=x", links=[], length=200)
b.queue = [stuck] * 40
r = c.search_company(NAME)
assert r["状态"] == "拦截未解除", f"拿不准时必须停下来而不是烧名单：{r['状态']}"
assert any("平台限流中" in m for m in c.logs), c.logs[-8:]

# 页面渲染完整、链接一堆，但条目一个都抽不到（改版了）：也不能写未找到
b = Browser(Page(HOME, suggest=[], info=0, input=NAME))
c = make_crawler(b)
mystery = Page(HOME + "s?q=x", links=[], length=9000, anchors=80, none=0)
b.queue = [mystery] * 40
r = c.search_company(NAME)
assert r["状态"] == "拦截未解除", f"认不出页面结构时绝不能定未找到：{r['状态']}"
assert any("认不出结果条目" in m for m in c.logs), c.logs[-8:]
print("④ 查无与限流分得开：判不准一律不写未找到 ✓")

# ── 5. 名单不推进、不写入（走 crawl 主循环） ──────────
c = make_crawler(Browser(Page(HOME)))
seen = []
c.search_company = lambda nm: seen.append(nm) or {"公司名": nm, "状态": "拦截未解除"}
c._detect_block = lambda ws=None: ""
names = ["甲公司", "乙公司", "丙公司"]
c.crawl(list(names), 0)
assert seen == ["甲公司"], f"卡住那家之后不能继续烧名单：{seen}"
assert c.results == [], f"拦截未解除不应写入结果：{c.results}"
print("⑤ 拦截未解除：不写入、不推进，续采从这一家重新开始 ✓")

# ── 6. 被弹去登录页：走登录闸门，不记未找到 ───────────
b = Browser(Page(HOME, suggest=[]))
c = make_crawler(b)
asked = []
c._ask_login = lambda reason: asked.append(reason) or False
b.queue = [Page("https://aiqicha.baidu.com/login?u=x", title="请登录-爱企查", length=58,
                land="https://aiqicha.baidu.com/login?u=x")] * 20
r = c.search_company(NAME)
assert r["状态"] == "拦截未解除", f"登录态失效要交给闸门：{r['状态']}"
assert asked and "登录" in asked[0], asked
assert not any("查无" in m for m in c.logs), "登录页不能被记成查无此公司"

# 用户登录好了（闸门放行）→ 下一轮结果页正常出详情
b = Browser(Page(HOME, suggest=[]))
c = make_crawler(b)
c._ask_login = lambda reason: True
b.queue = [Page("https://aiqicha.baidu.com/login?u=x", length=58,
                land="https://aiqicha.baidu.com/login?u=x"),
           Page(HOME + "s?q=x", links=[["888", NAME]]),
           Page(DETAIL, length=9000, info=5)]
r = c.search_company(NAME)
assert r["状态"] == "成功", f"登录后应重新拿到这家：{r['状态']} / {c.logs[-5:]}"
print("⑥ 登录态失效走闸门：不烧名单，登录后从这一家接着采 ✓")

# ── 7. 结果条目挑选 ─────────────────────────────────
c = make_crawler(Browser())
pid, text, exact = c._pick_pid(NAME, [["1", "某某管理有限公司"], ["2", NAME + "-爱企查"]])
assert (pid, exact) == ("2", True), (pid, text, exact)
# 短名字是公司名前缀时不能乱认（避免匹配到不相干的公司）
pid, text, exact = c._pick_pid("北京字节跳动科技有限公司",
                               [["9", "字节"], ["7", "北京字节跳动科技有限公司"]])
assert pid == "7" and exact, (pid, text)
pid, text, exact = c._pick_pid("上海某公司", [["5", "完全不同的一家公司股份有限公司"]])
assert pid == "5" and not exact, (pid, text)
assert c._norm_name("深圳市腾讯 计算机系统（有限）公司") == "深圳市腾讯计算机系统有限公司"
print("⑦ 结果条目按公司名挑，名字对不上取第一条 ✓")

# ── 8. 界面：限流提示不该叫用户去找验证码 ─────────────
from riskbird_gui import MainWindow  # noqa: E402

w = MainWindow()
# 文案用纯函数校验：offscreen 平台下窗口标题不归后端管，拿窗体读会得空串
title, body = MainWindow._blocked_dialog_message(True)
assert title == "平台正在限流" and "并没有要你填的验证码" in body, (title, body)
assert "已退避实测" in MainWindow._blocked_dialog_message(False)[1] or \
    "实测能不能拿到数据" in MainWindow._blocked_dialog_message(False)[1]
w.aiqicha_crawler.signals.blocked_signal.emit("⏳ 平台限流中：搜索联想与搜索结果页都没给出公司")
app.processEvents()
assert w._blocked_box is not None
assert w._blocked_info[0][1].startswith("⏳")
assert "平台限流中，工具已退避实测" in w.blocked_lbl.text(), w.blocked_lbl.text()
assert "完成验证，" not in w.blocked_lbl.text(), "限流红条不该叫用户去完验证"
w._hide_blocked_dialog()
w.qizhidao_crawler.signals.blocked_signal.emit("页面被跳转到安全验证页（百度验证）")
app.processEvents()
assert "请在浏览器窗口完成验证" in w.blocked_lbl.text(), w.blocked_lbl.text()
t2, b2txt = MainWindow._blocked_dialog_message(False)
assert t2 == "需要人工验证" and "手动完成验证" in b2txt, (t2, b2txt)
w._clear_blocked_state()
assert w._blocked_info == [] and w.blocked_lbl.isHidden()
print("⑧ 界面区分「要人工验证」与「平台限流自动退避」 ✓")

time.sleep = real_sleep
print("AQC_RECOVER_OFFLINE_OK")
