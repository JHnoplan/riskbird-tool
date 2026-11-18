"""gi_pace_offline_test.py —— 跨国采购网「恢复探测节奏」离线断言（零真实请求）。

要钉住的两件事（都是 2026-11-04 实机量出来的账）：
  A. 放行窗口的产能并不小：窗口一 08:38:14–08:47:28 九分钟入库 99 家（翻 14 页），
     窗口二 10:17–10:22 五分钟入库 289 家（≈58 家/分钟），所以「本站一天只能采
     几十条」不成立。冷却实测约 90 分钟（08:47 被拦→10:17 放行），期间旧代码每
     60 秒探测一次共约 40 次并没有把放行时刻推后——「探测会续期冷却」是误判。
     由此定节奏：等待上限必须跨过实测冷却（≥7200s），而探测静默只要「远低于人工
     速率」即可，**不许为了猜想的续期效应定到几十分钟**（放行后感知延迟＝白丢产能）。
  B. 「本机网络不通」不能再说成「站点在拦」（09:36 那条 HTTPSConnectionPool 事故）。

手法：按 URL 谓词回放的假 Session 记录请求序列并计数——这类缺陷看日志发现不了，
必须数请求次数才能锁死「退避期间到底有没有真的少发请求」。
"""
import io
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 同一条纪律：本套件验 b2bcollect 这一份产品，而 `cdp_base` 开局就 `import
# customer_library_cleaner`——那是三个目录各有一份的同名模块。两份都 insert(0) 时
# 后插的 riskbird 抢到最前，cdp_base 里的 `_cleaner` 就成了隔壁产物的副本
# （两份已经差开几百行）。隔壁目录只用来借独有模块，必须排在后面。
sys.path.insert(0, os.path.join(BASE, "b2bcollect_build"))
sys.path.append(os.path.join(BASE, "riskbird_build"))

import requests  # noqa: E402
import gi_crawler  # noqa: E402
import cdp_base  # noqa: E402

# 导对了哪一份产品当场自证（拿实现验实现看不出“又被副本遮蔽”）
for _nm, _mod in (("cdp_base", cdp_base), ("gi_crawler", gi_crawler),
                  ("customer_library_cleaner", cdp_base._cleaner)):
    assert os.sep + "b2bcollect_build" + os.sep in _mod.__file__, \
        f"导错产品的 {_nm}（被另一份同名副本遮蔽）：{_mod.__file__}"

PASS = FAIL = 0


def check(tag, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  ok   {tag}")
    else:
        FAIL += 1
        print(f"  FAIL {tag}\n        实际={got!r}\n        期望={want!r}")


def check_true(tag, got):
    check(tag, bool(got), True)


# ── 三种真实页面形状（内联构造，不依赖 /tmp 留档）──────────────────────────
_SHELL = ('<html><script>function JumpSelf(){self.location="/?WebShieldSessionVerify'
          '=ABC123";}</script><body>setTimeout("JumpSelf()",700);</body></html>')

# 安全狗拦截页：HTTP 200 + 正文五千字（比基类 3000 字门槛大），话术是「请求过于频繁」
_SAFEDOG = ('<html><head><title>拦截页面</title></head><body>'
            '<h1>您的请求过于频繁，已被网站管理员设置拦截！</h1>'
            '<p>请登录安全狗查看。' + ('填充正文，确保这一页五千字，绝不会被'
                                      '「体积小于 3000 才算插页」的门槛放过。' * 120)
            + '</p></body></html>')

_LIST = ('<html><body><table>'
         '<tr><td>1 <a href="http://weficoil.cn.globalimporter.net">山东威飞技术发展'
         '有限公司</a></td></tr>'
         '<tr><td colspan=2>深海石油钻探设备、水下系统<br><font size=-1 color=9999FF>'
         '联系人:Celia Lee<br>  电话: 15266008063</font></td></tr>'
         '<tr><td>2 <a href="http://saieryi.cn.globalimporter.net">上海赛尔意电气有限'
         '公司</a></td></tr>'
         '<tr><td colspan=2>海洋工程关键配套系统<br><font size=-1 color=9999FF>'
         '联系人:胡晶<br>  电话: 021-61492366</font></td></tr>'
         '</table></body></html>')


class _Resp:
    def __init__(self, text, status=200):
        self.content = text.encode("gb18030", "ignore")
        self.text = text
        self.status_code = status
        self.headers = {}


class _ScriptSession:
    """按脚本回放：script 是 url→响应 的函数；同时记下每一次请求的 URL。"""

    def __init__(self, script):
        self.script = script
        self.calls = []
        self.closed = 0

    def get(self, url, timeout=None, headers=None):
        self.calls.append(url)
        r = self.script(url, len(self.calls))
        if isinstance(r, Exception):
            raise r
        return r

    def close(self):
        self.closed += 1


def _mk(script, port=9401):
    g = gi_crawler.GiCrawler(port=port)
    g.incremental = False
    g._log = lambda *a, **k: None
    g._pace_before_request = lambda *a, **k: None
    g._session = _ScriptSession(script)
    return g


print("\n=== A1 恢复探测的节奏常量：静默要够快感知、等待要跨过实测冷却 ===")
check("首次探测前静默 3 分钟", gi_crawler.GiCrawler.BLOCK_POLL_IP, 180)
check("探测间隔上限 10 分钟", gi_crawler.GiCrawler.BLOCK_POLL_IP_MAX, 600)
check("单次总等待上限 3 小时", gi_crawler.GiCrawler.BLOCK_MAX_WAIT_IP, 10800)
check_true("等待上限必须跨过站方原话的 2 小时（7200 秒）",
           gi_crawler.GiCrawler.BLOCK_MAX_WAIT_IP >= 7200)
check_true("等待上限必须大于实测冷却 90 分钟（5400 秒）",
           gi_crawler.GiCrawler.BLOCK_MAX_WAIT_IP > 5400)
check_true("静默落在 60~600 秒之间（高于 600 就是放行后白等：实测 58 家/分钟）",
           60 <= gi_crawler.GiCrawler.BLOCK_POLL_IP <= 600)

print("\n=== A2 安全狗拦截页：一次探测只发 1 个请求（旧实现每探发 2~4 个）===")
_boot_calls = []


def _sd_script(url, n):
    # 列表页与首页都给安全狗拦截页：站方在拦这个出口，跟质询跳转没有任何意义
    return _Resp(_SAFEDOG)


g = _mk(_sd_script, port=9402)
g._bootstrap = lambda: _boot_calls.append(1) or False
why = g.probe_block()
check("探测请求数恰好 1 个", len(g._session.calls), 1)
check("冷却期里一次都不做会话引导（不额外烧请求）", len(_boot_calls), 0)
check_true("仍判定被拦：报出防采插页", "防采插页" in (why or ""))
check_true("文案把站方口径带出来（等 2 小时），不说成缺跳转",
           "2 小时" in (why or ""))
check_true("归类成 ip 处置（页面上没有可点的验证）", g._block_kind == "ip")

print("\n=== A3 WAF 薄壳：会话质询没走完才值得再花请求跟一跳 ===")
_shell_boot = []


def _shell_script(url, n):
    # 第 1 次（探测列表页）给薄壳；引导之后（第 3 次）给真列表
    if n == 1:
        return _Resp(_SHELL)
    return _Resp(_LIST)


g = _mk(_shell_script, port=9403)
g._bootstrap = lambda: _shell_boot.append(1) or True
why = g.probe_block()
check("薄壳判定解除成功", why, "")
check("确实补跟了一次质询引导", len(_shell_boot), 1)
check("引导后回头重取，总请求 2 个", len(g._session.calls), 2)
check("会话标记为可用（上层不用再引导一遍）", g._booted, True)
check("解析出 2 家公司（真列表页）", len(g.parse_list(_LIST, "公司")), 2)

print("\n=== A4 真列表页：一发就判定解除 ===")
g = _mk(lambda u, n: _Resp(_LIST), port=9404)
g._bootstrap = lambda: None or True
check("解除返回空串", g.probe_block(), "")
check("只发 1 个请求", len(g._session.calls), 1)
check("不调会话引导", g._booted, True)

print("\n=== A5 列表页正常但真没公司条目：不算解除（不能把空页当放行）===")
g = _mk(lambda u, n: _Resp("<html><body><table></table></body></html>"), port=9405)
why = g.probe_block()
check_true("仍判未解除", bool(why))
check_true("说明里点出「没有公司条目」", "没有公司条目" in (why or ""))

print("\n=== B1 连接层异常＝本机网络不通，绝不说成站点在拦 ===")
g = _mk(lambda u, n: requests.exceptions.ConnectionError("HTTPSConnectionPool"),
        port=9406)
why = g.probe_block()
check_true("带 NET_FAIL_TAG 前缀（界面/日志按这个分叉文案）",
           (why or "").startswith(cdp_base.NET_FAIL_TAG))
check_true("不再报「防采插页」", "防采插页" not in (why or ""))
check_true("HTTP 会话被重建（连接被重置后旧连接池不能用）",
           isinstance(g._session, requests.Session)
           and not isinstance(g._session, _ScriptSession))
check_true("说明里带异常类型（方便区分断网/DNS/连接被重置）",
           "ConnectionError" in (why or ""))
check("会话标记为不可用（下次自己重新引导）", g._booted, False)

print("\n=== B2 看门狗等待循环：网络类探测要说「等网络恢复」而不是「IP 冷却」===")
_logs = []
g = _mk(lambda u, n: _Resp(_SAFEDOG), port=9407)
g._log = lambda *a, **k: _logs.append(str(a[0]) if a else "")
g._block_kind = "ip"
_real_sleep = time.sleep
time.sleep = lambda s: None          # 长静默在测试里不真等
try:
    g._retry_after = 0
    g._stop_requested = False
    g._block_wake.clear()
    ok = g._wait_manual_unblock("防采插页（拦截页面）·站方要求约 2 小时后再浏览",
                                lambda: cdp_base.NET_FAIL_TAG + "ConnectionError")
finally:
    time.sleep = _real_sleep
check("探测始终不通→本轮优雅收（返回 False，已采数据不动）", ok, False)
check_true("等待日志说「本机网络没通」",
           any("本机网络没通（不是站点在拦）" in x for x in _logs))
check_true("等待日志不再说「IP 冷却，无需操作」",
           not any("IP 冷却，无需操作" in x for x in _logs))
check_true("收本轮时不再说「等 IP 冷却超时」（那是站点账，不是网络账）",
           not any("等 IP 冷却超时" in x for x in _logs))
check_true("收本轮时说网络仍未恢复",
           any("本机网络仍未恢复" in x for x in _logs))

print("\n=== B3 站点真在拦时，等待文案照旧说 IP 冷却（两条归因不互相覆盖）===")
_logs = []
g = _mk(lambda u, n: _Resp(_SAFEDOG), port=9408)
g._log = lambda *a, **k: _logs.append(str(a[0]) if a else "")
g._block_kind = "ip"
time.sleep = lambda s: None
try:
    g._retry_after = 0
    g._stop_requested = False
    g._block_wake.clear()
    g._wait_manual_unblock("防采插页（拦截页面）", lambda: "防采插页（拦截页面）")
finally:
    time.sleep = _real_sleep
check_true("站点拦截仍按 IP 冷却文案",
           any("IP 冷却，无需操作" in x for x in _logs))
check_true("不会误报成网络不通",
           not any("本机网络没通" in x for x in _logs))

print("\n=== C1 拦截判定本身不变（回归上一轮的取证：不看体积也扫）===")
g = _mk(lambda u, n: _Resp(_SAFEDOG), port=9409)
reason = g.http_block_reason(200, _SAFEDOG, "https://x/y", {})
check_true("五千字安全狗页仍识别为插页", bool(reason))
check_true("文案带出站方 2 小时口径", "2 小时" in (reason or ""))
check("真列表页不误判成插页", g.http_block_reason(200, _LIST, "https://x/y", {}), "")

print("\n=== C2 翻页硬上限仍是 3 页（非会员「每个行业前 3 页」是站方策略）===")
check("max_pages_default", gi_crawler.GiCrawler.max_pages_default, 3)
check("MAX_PAGES_HARD_CAP", gi_crawler.GiCrawler.MAX_PAGES_HARD_CAP, 3)
check("一轮请求预算 45（钉在站方「不超过 50 个内容页」之下）",
      gi_crawler.GiCrawler.REQ_BUDGET, 45)
check_true("预算必须 ≤ 站方配额 50（旧值 90 永远到不了，每次都是先撞墙再赔 90 分钟冷却）",
           gi_crawler.GiCrawler.REQ_BUDGET <= 50)

print("\n=== C3 配额页（非会员「不超过 50 个内容页」）分诊：等冷却，不许喊人工 ===")
# 10:22 实机拦到的就是这一页（与安全狗「请求过于频繁」不同层），实测 275 字
_QUOTA = ('<html><head><title>提示</title></head><body>'
          '网站已启用防采集功能，非会员可浏览每个行业的前3页信息(或不超过50个内容页信息)。'
          'Your IP has too many visits in a short time. '
          'Please continue browsing after 2 hours!'
          '<br>如需查看更多信息请<a href="/reg.html">会员注册</a></body></html>')
_gq = gi_crawler.GiCrawler(port=9410)
_gq._log = lambda *a, **k: None
_r = _gq.http_block_reason(200, _QUOTA,
                           "https://www.globalimporter.net/search_company.asp?keyword=x&curpage=1", {})
check_true("配额页判成插页（旧版会当正文返回→解析 0 家→关键词被记成已跑完）", bool(_r))
check("配额页分层归 ip（等冷却自动恢复，不是等人工）", _gq._block_kind, "ip")
check_true("配额页话术要带等待预期（只写「防采插页」用户不知道还要不要等）",
           "2 小时" in (_r or ""))
check("配额页 parse_list 是 0 家（这正是旧版误当空结果的形状）",
      len(_gq.parse_list(_QUOTA, "制冷设备")), 0)

print("\n=== C4 翻页参数没生效：每页取回同一批条目，就说「同一批」不说「池子枯竭」 ===")
# 站方配额 3 页，但 curpage 这一形实机也撞到过「换个页码地址、正文一个字没变」。
# 旧代码要连撞两页 0 新增才停下，还写「公司池已枯竭」：那把处方指到「换词」上，
# 而真凶是翻页参数（处方应是修 URL）。1688 那一轮实机就是被这句引偏了两轮。
import re as _re_c4
_LIST_NEXT = _LIST.replace("</table>",
                           '<tr><td><a href="/search_company.asp?keyword=x&amp;curpage=2">下一页</a>'
                           "</td></tr></table>")
_LIST_NEXT2 = (_LIST_NEXT.replace("weficoil", "bexample").replace("saieryi", "cexample")
                .replace("山东威飞技术发展", "山东乙示例机械").replace("上海赛尔意电气", "上海丙示例电子"))


def _by_page(pages):
    """按 URL 里的 curpage 回放。不能拿「第几次请求」当页码：_fetch 内部有重试与
    质询引导，请求次数≠页数，那样喂会把下一页发成上一页（本段第一版就是这么错的）。"""
    def _f(url, n):
        _m4 = _re_c4.search(r"curpage=(\d+)", url)
        _i4 = (int(_m4.group(1)) if _m4 else 1) - 1
        return _Resp(pages[_i4] if _i4 < len(pages) else pages[-1])
    return _f


_logs_c4 = []
g = _mk(_by_page([_LIST_NEXT]), port=9411)
g._log = lambda *a, **k: _logs_c4.append(str(a[0]) if a else "")
_got_c4 = list(g._iter_keyword("海洋工程"))
_new1_c4 = int(_re_c4.search(r"第 1 页：\d+ 条，新增 (\d+) 家", "\n".join(_logs_c4)).group(1))
check_true("第一页真有出货（不是一上来就空页）", _new1_c4 > 0)
check("两页合计出货＝第一页那一页的出货（第二页同一批，一家都不该再多）",
      len(_got_c4), _new1_c4)
check_true("第二页就点名「同一批条目／翻页参数没生效」",
           any("第 2 页与上一页是「同一批条目」" in m and "翻页参数没生效" in m for m in _logs_c4))
check_true("不必再多烧一页去凑「连续两页 0 新增」", not any("第 3 页" in m for m in _logs_c4))
check_true("不许把这一形写成「无新增所以池子枯竭」（只允许否定句里提到枯竭）",
           not any("枯竭" in m and "不是" not in m for m in _logs_c4))

print("\n=== C5 各页内容不同、只是都在增量库里：那一形要说「不再翻页」而不是「同一批」 ===")
_logs_c5 = []
g = _mk(_by_page([_LIST_NEXT, _LIST_NEXT2]), port=9412)
g._log = lambda *a, **k: _logs_c5.append(str(a[0]) if a else "")
# 先把两页的公司都标成「已经采过」：这一形的真相是增量库已含，不是翻页失效
for _h5 in (_LIST_NEXT, _LIST_NEXT2):
    for _e5 in g.parse_list(_h5, "海洋工程"):
        g._seen_members.add(f"{g.PLATFORM_SLUG}:{_e5['公司ID']}")
        g._seen_members.add(f"{g.PLATFORM_SLUG}:name:{g._norm_name(_e5['公司名称'])}")
_got_c5 = list(g._iter_keyword("海洋工程"))    # 生成器不跑完就没有请求、也没有日志
check("一家都不重复入库", len(_got_c5), 0)
check_true("两页都翻过了（内容不同就不会提前收轮）",
           any("第 2 页" in m for m in _logs_c5))
check_true("说的是「各页内容不同、都已在增量库里」",
           any("各页条目内容不同" in m for m in _logs_c5))
check_true("这一形不许报「同一批条目」（两页内容真的不同）",
           not any("同一批条目" in m for m in _logs_c5))

total = PASS + FAIL
print("\n" + "=" * 62)
print(f"跨国采购网恢复节奏离线断言：{PASS} 项通过 / {FAIL} 项失败（共 {total}）")
sys.exit(1 if FAIL else 0)
