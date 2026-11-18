# -*- coding: utf-8 -*-
"""test_site_enrich_offline.py —— 功能④「采集的数据同步去官网拿联系方式」离线回归。

用户诉求原话：「如果数据有官网，同步去它的官网把电话和邮箱采集下来，如果数据本来就有
电话或邮箱，可以合并去重用英文逗号隔开」。这句话的正确性全在判据里，不在抓取动作里，
所以本套件按**判据 → 出口 → 会话 → 界面**四层钉住，全程不联网（fetch_html 与
socket.getaddrinfo 都换成本表驱动的实现，requests.get 上装硬闸，真发一次请求就判红）：

  A. 口径同源：留痕列名只许有一份写法（RAW_HEADERS/EXPORT_HEADERS/_base_rec/SITE_MARK 对齐）
  B. 「什么算官网」：norm_site 规整与拒绝形态、平台页边界口径、SSRF 闸（字面 IP 与 DNS 两分支）
  C. 证据分级：tel:/mailto:=3、标签语境=2、孤立=1、**只出现在 <script> 里=0**、
     负向语境（专利号/备案号/邮编/版权年）=0、建站商署名的 400 热线=0
  D. 排版盲区：号码被切成两个 span、区号后空格、4-3-3 的 400、带分机、86 字冠
  E. 号码/邮箱形态闸与显示归一（不改写出另一个号）
  F. 停放/被抢注/挂马、归属证据缺失 → 整站不采信（fail-closed，200 也不算）
  G. 合并出口：英文逗号去重、只追加不覆盖、手机/固话/邮箱各归各列、跨行净新增按值去重
  H. enrich_records 的账：同域名只请求一次、终态不再花预算、待重试累加、到上限转终态
     （且留痕与 needs_retry 自洽）、halt 后一律不落笔、单条异常不冒泡、就地写回原字典
  I. 会话层：site_enrich 默认关（引擎侧防「自称离线的用例真联网」）、手动 force 放行、
     preview 与真跑同一份判据、reset_results 清进程内缓存
  J. 界面层：手动按钮恒定存在、四条前置各回一句话、跑在后台线程、回来恢复按钮并落盘、
     退出时等这一轮、与采集/复位不并发、话术与代码不两张皮（docstring 承诺的入口必须真存在）

运行：python test_site_enrich_offline.py
"""
import os
import socket
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))

# 状态落点钉进沙箱：本套件会真造 MainWindow，绝不能把假任务写进开发机的真实索引
_STATE_SANDBOX = Path(tempfile.mkdtemp(prefix="site_state_"))
os.environ["B2B_STATE_DIR"] = str(_STATE_SANDBOX)
_DESK_SANDBOX = Path(tempfile.mkdtemp(prefix="site_desk_"))

_passed = 0
_failed = 0


def _assert(condition, label):
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  ok   {label}")
    else:
        _failed += 1
        print(f"  FAIL {label}")


import website_contact as wc                                        # noqa: E402
import field_evidence as fe                                          # noqa: E402
import enrich_waterfall as _wf                                       # noqa: E402
import task_registry as _tasks                                      # noqa: E402
from cdp_base import CdpSiteCrawler                                 # noqa: E402
from parallel_session import RAW_HEADERS, ParallelSession            # noqa: E402

_assert(str(_tasks.index_path()).startswith(str(_STATE_SANDBOX)), "隔离生效：任务索引落在沙箱")

# ── 离线硬闸：本表之外的域名一律报错，requests 真被调用就判红 ──────────
# 假 IP 一律用**真公网段**：203.0.113.0/24、198.51.100.0/24、192.0.2.0/24 这三段
# 虽然叫「文档用例段」，在 Python 的 ipaddress 里却归 is_private——拿它们当公网会让
# SSRF 闸按设计拦下夹具，红的是判据本身而不是被测代码。
_DNS_TABLE = {
    "www.mingliang-led.com": ["58.87.101.1"],
    "mingliang-led.com": ["58.87.101.1"],
    "www.a-b.com": ["58.87.101.2"],
    "www.c-d.com": ["58.87.101.3"],
    "www.e-f.com": ["58.87.101.4"],
    "www.slow-1.com": ["58.87.101.5"],
    "www.slow-2.com": ["58.87.101.6"],
    "www.slow-3.com": ["58.87.101.7"],
    "www.slow-4.com": ["58.87.101.8"],
    "intranet.corp": ["10.0.0.8"],
    "metadata.example": ["169.254.169.254"],
}
_dns_misses = []
_net_trips = []
# 对照域探针的开关：预检要写「这个域不存在」前会先问一次对照域，判本机解析器通不通。
# 这里只能由**夹具**提供答案：去桩掉 _resolver_alive 就是拿桩验桩，测不到真链路。
_ctl = {"alive": True}
_ctl_probes = []          # 对照域被真查过几次（不记这一笔，“探针没开火”就是一条永不会红的判据）


def _fake_getaddrinfo(host, *a, **k):
    h = str(host).lower().rstrip(".")
    ips = _DNS_TABLE.get(h)
    if ips:
        return [(2, 1, 6, "", (ip, 0)) for ip in ips]
    if h in wc.DNS_CONTROL_HOSTS:
        # 对照域是「本机解析器此刻通不通」的唯一探针，所以它必须**不查表**、只看开关：
        # 表里存不存这三个域是夹具的事，开关才是被测代码能观察到的那件事。
        _ctl_probes.append(h)
        if _ctl["alive"]:
            return [(2, 1, 6, "", ("58.87.101.9", 0))]
        raise OSError("对照域也解不出来（模拟本机解析器废了）")
    if h.endswith(".invalid"):                # RFC 6761 保留：真机就回 errno=8（查无此名）
        raise socket.gaierror(8, "nodename nor servname provided")
    _dns_misses.append(h)
    raise OSError(f"离线用例查了表外域名：{host}")


_real_getaddrinfo = wc.socket.getaddrinfo
wc.socket.getaddrinfo = _fake_getaddrinfo

try:
    import requests as _requests
    _real_requests_get = _requests.get

    def _tripwire_get(*a, **k):
        _net_trips.append(a[:1] and str(a[0]) or "")
        raise AssertionError("离线用例真发出了 HTTP 请求")

    _requests.get = _tripwire_get
except Exception:                                     # requests 不在环境里也照常跑
    _requests = None
    _real_requests_get = None

_PAGES = {}           # host -> 首页 HTML（按 host 存，避免 norm_site 补出的斜杠差异坑夹具）
_calls = []           # 真被"访问"过的 URL（含重复，用于数请求次数）


def _fake_fetch(url, timeout=wc.TIMEOUT):
    _calls.append(url)
    html = _PAGES.get(wc.host_of(url))
    if html is None:
        return None, url, "不可达(ConnectionError)"
    return html, url, ""


_real_fetch = wc.fetch_html
wc.fetch_html = _fake_fetch


def _reset_net():
    _calls.clear()
    _net_trips.clear()
    _dns_misses.clear()
    _ctl_probes.clear()
    _ctl["alive"] = True
    wc.reset_caches()


# ── A. 口径同源 ─────────────────────────────────────────────
print("A. 留痕列口径同源")
_assert(wc.SITE_MARK == "官网回采", f"留痕列名固定（{wc.SITE_MARK}）")
# 瀑布补全（功能 A/I）在留痕列之后又接了三列证据，所以「官网回采是最后一列」这句
# 旧判据已经过期。这里按**新的表尾契约**钉，钉得比原来更死：四列的顺序与名字全部
# 从两个模块的常量派生（不写第二份字面量），任何一列被改名、被换序、被漏进某一张表，
# 都当场红——原来那句只验最后一列，恰恰管不到「后面又加了三列而某张表只加了两列」。
_evidence_tail = [wc.SITE_MARK] + list(fe.evidence_columns())
_assert(RAW_HEADERS[-len(_evidence_tail):] == _evidence_tail,
        f"原始表表尾四列＝留痕+证据三列且顺序固定（{('｜'.join(_evidence_tail))}）")
_assert(CdpSiteCrawler.EXPORT_HEADERS[-len(_evidence_tail):] == _evidence_tail,
        "清洗导出表表尾同序同名（两处不许各写一份）")
_rec = CdpSiteCrawler.__new__(CdpSiteCrawler)
CdpSiteCrawler.PLATFORM_NAME = "测试站"
_base = _rec._base_rec({"公司名称": "测试公司", "公司网址": "www.mingliang-led.com"})
_assert(wc.SITE_MARK in _base and _base[wc.SITE_MARK] == "",
        "_base_rec 建的每一行都带空的留痕列（空=本轮没对它跑过回采）")
_assert(all(c in _base and str(_base[c]) == "" for c in fe.evidence_columns()),
        "_base_rec 同样带齐三列证据且初始为空（空=这行还没被补全动过）")
_assert(list(RAW_HEADERS).count(wc.SITE_MARK) == 1, "留痕列只出现一次（重复列会让导出错位）")
_assert([c for c in fe.evidence_columns() if list(RAW_HEADERS).count(c) != 1] == [],
        "证据三列各只出现一次（缺一列＝那一级写的值落进虚空，多一列＝导出错位）")

# ── B. 什么算官网 / SSRF 边界 ───────────────────────────────
print("B. norm_site / 平台页边界 / SSRF")
_assert(wc.norm_site("www.mingliang-led.com") == "https://www.mingliang-led.com", "裸域名补 https")
_assert(wc.norm_site("  http://a-b.com/contact/  ") == "http://a-b.com/contact/", "去空白保留原协议")
_assert(wc.norm_site("“www.a-b.com”") == "https://www.a-b.com", "中文引号也剥掉")
_assert(wc.norm_site("暂无") == "" and wc.norm_site("-") == "", "中文占位符不当官网")
_assert(wc.norm_site("http://127.0.0.1:8080/x") == "", "非 web 端口不是官网（是扫描目标）")
_assert(wc.norm_site("javascript:void(0)") == "", "js 伪协议不发请求")
_assert(wc.norm_site("user:pass@a-b.com") == "", "带凭据的 URL 一律不发请求")
_assert(wc.norm_site("ftp://a-b.com") == "", "只走 http(s)")
_assert(wc.is_platform_host("qizhidao.com") and wc.is_platform_host("xxx.jdzj.com"),
      "平台页命中（含租户子域）")
_assert(not wc.is_platform_host("loco-china.cn"),
      "边界口径：裸子串会把 loco-china.cn 这类企业自有域名误杀（实测踩过的坑）")
_assert(wc.is_platform_host(""), "空 host 不当官网")
_assert(wc.is_public_host("58.87.101.1") is True, "公网 IP 放行")
_assert(wc.is_public_host("192.0.2.6") is False,
        "文档用例段在 ipaddress 里归私网：夹具不能拿它当公网（这条同时钉住闸的真实口径）")
_assert(wc.is_public_host("127.0.0.1") is False, "回环拦截")
_assert(wc.is_public_host("10.0.0.8") is False, "私网段拦截")
_assert(wc.is_public_host("169.254.169.254") is False, "元数据地址拦截")
_assert(wc.is_public_host("localhost") is False and wc.is_public_host("nas.local") is False,
      ".local / localhost 拦截")
_assert(wc.is_public_host("intranet.corp") is False, "域名解析到私网 → 字面看无害也拦（DNS 分支）")
_assert(wc.is_public_host("www.mingliang-led.com") is True, "解析到公网 → 放行")
_assert(wc.is_public_host("not-in-table.example") is True,
      "查不到不等于危险：判不出时不拦，让请求自己失败（不许把没网说成不安全）")
_assert(wc.pick_site({"公司网址": "https://xxx.jdzj.com/shop/1", "官网": "www.mingliang-led.com"})
        == "https://www.mingliang-led.com", "平台页跳过、按优先级取下一个像官网的字段")

# ── B2. 域名可达性预检（DNS 缓存的三条闸）───────────────────────
# 这一节本来一条判据都没有：预检能把一轮 990 行里那 327 家「不可达」从「每家付 10 秒
# 连接超时」降到「一次并发解析」，但它同时是唯一被允许往缓存里写「这个域名不存在」的
# 地方——写坏了就是两天内把好好域名一家家拦掉。**能提速的通道同时是能投毒的通道**，
# 所以三道闸都要钉，而且每道都要钉正反两个方向：只钉「该拦时拦住了」，闸焊死也算通过。
print("B2. 域名可达性预检（负结论只能由预检写，三道闸各管一种写坏缓存）")
_pf_logs = []


def _pf(hosts, **kw):
    kw.setdefault("respect_persist", False)      # 闸的行为不靠磁盘也能验，先拿快的
    _pf_logs.clear()
    return wc.preflight_hosts(hosts, log=lambda m: _pf_logs.append(str(m)), **kw)


_reset_net()
_assert(wc.host_reachable("case-a.invalid") is None, "没跑过预检＝「没查过」：不拦也不跳")
_assert(wc.is_public_host("case-a.invalid") is True,
        "SSRF 闸碰到「查无此名」按设计放行（解不出 ≠ 危险，让请求自己失败）")
_assert(wc.host_reachable("case-a.invalid") is None,
        "但 SSRF 那一路**不许**顺手把负结论写进可达性缓存：它分不清「域名真没了」与"
        "「本机此刻没网」，后者一固化下次联网预检就会把整批好域名当不存在拦掉")

_reset_net()
st = _pf(["www.mingliang-led.com", "case-b.invalid"])
_assert(st["live"] == 1 and st["dead"] == 1,
        f"批内同时有在网与查无此名时分档正确（{st['live']}/{st['dead']}）")
_assert(wc.host_reachable("case-b.invalid") is False,
        "预检把「查无此名」落成负结论：下一轮这一家不再付连接超时")
_assert(not _ctl_probes,
        "批内只要有一个域解出来了就**不再问对照域**：那一个成功已当场证明本机解析器是通的。"
        "旧写法每轮开局先打三个对照域，那三个是真外网域名——离线用例当场变成「悄悄联网」，"
        "并把「本机 DNS 不可用」这句**假结论**印进日志，后面的预检不再是生产那条路")

_reset_net()
_ctl["alive"] = False
st = _pf(["case-c.invalid", "case-d.invalid"])
_assert(st["skipped"] and st["dead"] == 0,
        f"本机解析器判废时一条负结论都不写（dead={st['dead']}）")
_assert(wc.host_reachable("case-c.invalid") is None
        and wc.host_reachable("case-d.invalid") is None,
        "「坏掉的递归 DNS 能把全网判成不存在」那种负结论一写就是两天，比不缓存糟糕得多")
_assert(bool(_ctl_probes),
        "但探针不许因为「挪到写盘前」就不开火：批内零成功又有负结论要写时它必须问")
_assert(st["unknown"] == 2,
        f"被丢弃的负结论退回「没查过」并把家数记进 unknown（{st['unknown']}）："
        "不记这一笔，后面 enrich_site 的账就凑不齐")
_assert(any("不记任何「不存在」结论" in m and "2 个域照旧去抓" in m for m in _pf_logs),
        "话术里那个数必须是**丢弃的家数**：拿清零后的 dead 拼句子就恒印「0 个域」，"
        "日志说「本轮没拦任何一家」而实际上整整一批都是被拦掉的——最坏的那种假账")

_reset_net()
st = _pf(["case-e.invalid"])          # 对照域默认通（夹具开关已复位）
_assert(st["dead"] == 1 and wc.host_reachable("case-e.invalid") is False,
        "反向对照：解析器是通的时，全批零成功**不能**拦下合法负结论——"
        "一轮里真的只有一两家小域名不存在是完全可能的，把探针当永真闸就等于废掉整个缓存")

_reset_net()
_many = [f"case-f{i}.invalid" for i in range(wc.DNS_DEAD_MIN_PROBED)]
st = _pf(_many + ["www.mingliang-led.com"])
_assert(st["dead"] == 0 and not [h for h in _many if wc.host_reachable(h) is False],
        f"{len(_many)}/{len(_many) + 1} 判死超过阈值 → 占比闸丢弃全部负结论（dead={st['dead']}）")
_assert(not _ctl_probes,
        "占比闸靠批内自己的分布就能判，不需要再问对照域（两道闸不重叠也不互等）")

_reset_net()
st = _pf([f"case-g{i}.invalid" for i in range(wc.DNS_DEAD_MIN_PROBED - 1)])
_assert(st["dead"] == wc.DNS_DEAD_MIN_PROBED - 1,
        "样本没到 DNS_DEAD_MIN_PROBED 就不下「解析器不可信」的结论："
        "10 家全是死域在小表里完全正常，拿占比当唯一依据就会把少量死域名当成机器坏了")

_reset_net()
st = _pf(["not-in-table.example"])
_assert(st["unknown"] == 1 and wc.host_reachable("not-in-table.example") is None,
        "超时/临时失败算「没查过」（不是「不存在」）：跟上面的查无此名必须是两个结论")

_reset_net()
wc._dns_disk_note("case-h.invalid", False, when=time.time() - wc.DNS_TTL_DEAD - 1)
_assert(wc.host_reachable("case-h.invalid") is None,
        "负结论过期就当没查过（新注册的域名要能复活），而不是把过期读成永久")
wc._dns_disk_note("case-h.invalid", False)
_assert(wc.host_reachable("case-h.invalid") is False,
        "同一结论重记一次就复活：TTL 是「重新问一次」不是「判死」")

_reset_net()
st = _pf(["www.mingliang-led.com", "case-i.invalid"], respect_persist=True)
_assert(st["dead"] == 1 and wc.dns_disk_flush() is True, "预检结论落盘")
with wc._LOCK:
    wc._DNS_DISK.clear()
wc._DNS_DISK_LOADED = False
_assert(wc.host_reachable("case-i.invalid") is None,
        "内存镜像清空且未加载时＝没查过（「没加载过就不写盘」与「没加载过就不读」是同一口径）")
wc._dns_disk_load()
_assert(wc.host_reachable("case-i.invalid") is False,
        "跨任务复用：结论从盘里回来，一次都不再问（这就是那 327 家不再各付 10 秒的原因）")
_reset_net()

# ── C. 证据分级 ─────────────────────────────────────────────
print("C. 证据分级（页面哪里出现的）")
PHONE = "021-12345678"
PAGE_TEL = ('<html><body><div>上海明亮照明</div>'
            f'<a href="tel:{PHONE}">{PHONE}</a></body></html>')
ph, em = wc.extract(PAGE_TEL)
gp, ge = wc.score_contacts(PAGE_TEL, ph, em)
_assert(gp and gp[0][1] == 3, f"tel: 链接=3 分（作者明确标注这是联系方式）：{gp}")
PAGE_MAIL = ('<html><body><div>明亮照明</div>'
             '<a href="mailto:info@mingliang-led.com">info@mingliang-led.com</a></body></html>')
ph2, em2 = wc.extract(PAGE_MAIL)
_assert(wc.score_contacts(PAGE_MAIL, ph2, em2)[1][0][1] == 3, "mailto: 链接=3 分")
PAGE_LABEL = ('<html><body><div>明亮照明 联系我们</div>'
              f'<p>电话：{PHONE}</p></body></html>')
ph3, em3 = wc.extract(PAGE_LABEL)
_assert(wc.score_contacts(PAGE_LABEL, ph3, em3)[0][0][1] == 2, "命中处 ±40 字有标签词=2 分")
PAGE_ALONE = ('<html><body><div>明亮照明</div>'
              '<p>厂区沿河路 88 号，西侧二期在建，欢迎参观。</p>'
              f'<p style="color:#999">{PHONE}</p></body></html>')
ph4, em4 = wc.extract(PAGE_ALONE)
_assert(wc.score_contacts(PAGE_ALONE, ph4, em4)[0][0][1] == 1, "可见但无标签语境=1 分（仍留但降级）")
PAGE_JS = ('<html><body><div>明亮照明</div><p>版权所有，翻录必究，页面正在加载。</p>'
           '<script>var dates = ["05172743000", "01041426525"];</script></body></html>')
ph5, em5 = wc.extract(PAGE_JS)
_assert(bool(ph5), "抽取器确实扫到了脚本里的数字串（不放宽判据，靠打分剔除）")
_assert(not wc.score_contacts(PAGE_JS, ph5, em5)[0],
      "只出现在 <script> 里=0 分直接丢弃（实测 05172743000 就是这样混进来的）")
PAGE_PATENT = ('<html><body><div>明亮照明</div>'
               '<p>专利号：ZL201310628718.0</p><p>更多专利详见国家知识产权局。</p></body></html>')
ph6, em6 = wc.extract(PAGE_PATENT)
_assert(not wc.score_contacts(PAGE_PATENT, ph6, em6)[0],
      "专利号里的数字串不算电话（NEG_CTX 命中降为 0）")
PAGE_AGENCY = ('<html><body><div>明亮照明</div>'
               '<p>技术支持：某某网络 400-888-1234</p></body></html>')
ph7, em7 = wc.extract(PAGE_AGENCY)
_assert(not wc.score_contacts(PAGE_AGENCY, ph7, em7)[0],
      "建站商署名的 400 热线不算厂家电话（有标签语境也不给分）")
PAGE_SPAM_MAIL = ('<html><body><div>明亮照明</div><p>联系邮箱 '
                  'test@example.com、sentry.wixpress.com、logo@2x.png</p></body></html>')
ph8, em8 = wc.extract(PAGE_SPAM_MAIL)
_assert(not wc.score_contacts(PAGE_SPAM_MAIL, ph8, em8)[1], "邮箱垃圾闸：示例域/图片名/sentry 都不算")

# ── D. 排版盲区 ─────────────────────────────────────────────
print("D. 排版盲区（判据没放宽，只是看得全了）")
# 切断位置按**真实排版**来：企业站把号码拆进两个标签时，分界只有「区号 | 本地号」和
# 「4 位 | 4 位」两处。刻意从中间任意切一刀（021-123 | 45678 这种 3-5 分组）去断言，
# 等于要求抽取器承认「任意空格都能拼成号码」——那判据就真放宽了，日期和编号会一起被拼进来。
_ph_ac = wc.extract('<p><span>021-</span><span>12345678</span></p>')[0]
_assert(_ph_ac == ["021- 12345678"], f"区号后被切成两个标签也能拼回来：{_ph_ac}")
_assert(_ph_ac and wc.normalize_display(_ph_ac[0]) == PHONE,
        "拼回来的带空格写法，出口前归一成可直接外呼的号")
_assert(wc.extract('<p><span>021-1234</span><span>5678</span></p>')[0] == [PHONE],
        "本地号 4-4 分段被切成两个标签也能拼回来")
_assert(wc.extract('<p>电话：010- 64229311</p>')[0], "区号后隔一格的排法能命中")
_assert(wc.extract('<p>服务热线 4008-123-456</p>')[0] == ["4008-123-456"],
        "400 的 4-3-3 分段写法能命中")
_assert(wc.extract('<p>总机：021-12345678-转123</p>')[0] == [PHONE],
        "带分机的总机收主号：不把「-转123」整串当号（外呼拨不通），也不拆出个假号")
_assert(len(wc.extract(f'<p>{PHONE}</p><p>{PHONE[:4]} {PHONE[4:]}</p>')[0]) == 1,
        "同一号码两种排版只留第一次出现（去重按数字串）")

# ── E. 形态闸与显示归一 ─────────────────────────────────────
print("E. plausible_* / normalize_display")
_assert(wc.plausible_phone("13800138138") is True, "正常手机号放行")
_assert(wc.plausible_phone("13800138000") is False, "演示号段拉黑")
_assert(wc.plausible_phone("11111111111") is False, "叠字/同字符不算号")
_assert(wc.plausible_phone("0211234567") is False, "区号+7 位且无分隔一律拒（已基本退役）")
_assert(wc.plausible_phone("021-12345678") is True, "区号带分隔的 8 位本地号放行")
_assert(wc.plausible_phone("021-32563256") is False,
        "本地号前后叠词（3256 重复两遍）是页面数字，不是电话")
_assert(wc.plausible_email("sale@mingliang-led.com") is True, "企业邮箱放行")
_assert(wc.plausible_email("noreply@mingliang-led.com") is False, "noreply 不算联系人邮箱")
_assert(wc.normalize_display("010- 64229311") == "010-64229311", "排版归一成可直接外呼的写法")
_assert(wc.normalize_display("+86 21 36129977") == "021-36129977", "86 字冠归一")
_assert(wc.normalize_display("400-888-1234") == "400-888-1234", "400 不乱改（改了就是另一个号）")

# ── F. 停放 / 归属：fail-closed ─────────────────────────────
print("F. 停放与被劫持的官网整站不采信")
PARKED = ('<html><body><title>buy this domain</title><div>明亮照明</div>'
          f'<p>电话：{PHONE}</p></body></html>')
_assert(wc._grade_pages([(PARKED, "http://www.a-b.com/", "首页")], "上海明亮照明有限公司")[3] is True,
        "停放页判 dead（200 可达 + 页上写着号码也不采信）")
ENTITY_PARKED = ('<html><body><div>&#39;域名&#x5df2;&#x6ce8;&#x518c;&#39;</div>'
                 f'<p>电话：{PHONE}</p></body></html>')
_assert(wc.PARKED_RE.search(wc.strip_tags(ENTITY_PARKED)),
        "先解 HTML 实体再判：灰产标题写成实体形式时正则也要看得见")
BODY_LOST = ('<html><body><div>某家电俱乐部 电子竞技 投注</div>'
             f'<p>电话：{PHONE}</p></body></html>')
_assert(wc._grade_pages([(BODY_LOST, "http://www.a-b.com/", "首页")],
                        "上海明亮照明有限公司")[2], "被抢注挂灰产内容 → 给出人话原因")
NO_ATTR = ('<html><body><div>某某食品加工厂</div>'
           f'<p>电话：{PHONE}</p></body></html>')
gp_n, _ge_n, why_n, dead_n = wc._grade_pages(
    [(NO_ATTR, "http://www.mingliang-led.com/", "首页")], "广东旭日智能装备有限公司")
_assert(not gp_n and "认不出主体" in why_n and dead_n is False,
        f"归属证据一条都没有 → 整站不采信（fail-closed）：{why_n}")
gp_y, _ge_y, why_y, _d = wc._grade_pages(
    [('<html><body><div>明亮照明工程有限公司官网</div>'
      f'<a href="tel:{PHONE}">{PHONE}</a></body></html>',
      "http://www.mingliang-led.com/", "首页")], "上海明亮照明有限公司")
_assert(bool(gp_y) and not why_y, "页面认得出这家公司（品牌段见于正文）才采信")
_assert(wc.brand_grams("上海明亮照明有限公司") == {"明亮", "亮照", "照明"},
        "区划与组织形式不进品牌段（否则整页正文都能「对上」，归属判据形同没有）")
_assert(wc.has_attr("", "任意正文", "unknown-host.cn") is True,
        "名字规整后一个可判段都不剩时不强卡（fail-open：那是行本身缺名字，不是官网易主）")
_assert(wc.has_attr("某某贸易有限公司", "任意正文里没这个词", "unknown-host.cn") is False,
        "有可判段但页面对不上 → 不采信（fail-closed 才是默认，fail-open 只留给无可判段）")

# ── G. 合并出口 ─────────────────────────────────────────────
print("G. 合并去重（用户要的就是这句：英文逗号隔开）")
_assert(wc.merge_values("021-12345678", ["021-1234 5678", "021-87654321"])
        == "021-12345678,021-87654321", "同值的另一种排版不再追加，异值用英文逗号合并")
_assert(wc.merge_values("", ["a@b-c.com"]) == "a@b-c.com", "原本没有也能追加")
_assert(wc.merge_values("021-12345678／021-87654321", []) == "021-12345678,021-87654321",
      "原值里的全角斜杠写法拆开重排（分隔符集合刻意不含空格）")
_assert(wc.merge_values("13800138138 ", ["13800138138"]) == "13800138138",
      "已有值不会被覆盖成别的号（只追加、不覆盖）")
_assert(wc.split_mobile("13800138138") and not wc.split_mobile(PHONE), "手机/固话分列判据")

# ── H. enrich_records 的账 ─────────────────────────────────
print("H. 批量回采的账（真线程池 + 假网络）")
GOOD = ('<html><body><div>明亮照明 联系我们</div>'
        f'<a href="tel:{PHONE}">{PHONE}</a>'
        '<a href="mailto:sale@mingliang-led.com">sale@mingliang-led.com</a>'
        '<p>手机 13800138138</p></body></html>')
NOWHERE = ('<html><body><div>明亮照明</div>'
           '<p>本公司专注道路照明，欢迎参观指导。</p></body></html>')
UNREACH_HTML = None


def _rec(name, site, **kw):
    r = {"公司名称": name, "公司网址": site, "电话": "", "手机": "", "邮箱": "", wc.SITE_MARK: ""}
    r.update(kw)
    return r


_reset_net()
_PAGES.clear()
_PAGES["www.mingliang-led.com"] = GOOD
_PAGES["www.a-b.com"] = NOWHERE
rows = [_rec("上海明亮照明有限公司", "www.mingliang-led.com"),
        _rec("上海明亮照明有限公司（第二家门店）", "www.mingliang-led.com"),
        _rec("上海明亮照明有限公司", "https://www.a-b.com/")]
_ref0 = rows[0]
st = wc.enrich_records(rows, workers=1, budget=30, limit=50)
_assert(_calls and len([u for u in _calls if wc.host_of(u) == "www.mingliang-led.com"]) == 1,
        f"同一官网只请求一次（本页访问 "
        f"{len([u for u in _calls if wc.host_of(u) == 'www.mingliang-led.com'])} 次）")
_assert(_ref0["电话"] == PHONE and "sale@mingliang-led.com" in _ref0["邮箱"],
        f"就地写回原字典：电话={_ref0['电话']} 邮箱={_ref0['邮箱']}")
_assert(_ref0["手机"] == "13800138138", "手机号进「手机」列、固话进「电话」列（不串列）")
_assert(wc.SITE_MARK in _ref0 and "净新增" in _ref0[wc.SITE_MARK],
        "每行留下可复核的账：" + _ref0[wc.SITE_MARK])
_assert(st["ok"] == 2 and st["new_values"] == 3,
        f"跨行去重后的净新增按「值」算：{st['new_values']}（同一家两条记录不重复计数）")
_assert(st["total"] == 3 and st["marked"] == 3, "三条都落了笔（含没出值那条，它也有一条账）")
_assert(rows[2][wc.SITE_MARK].startswith(wc.SITE_MARK) and
        not rows[2][wc.SITE_MARK].startswith(wc.RETRY_MARK),
        "「页面没号码」是终态、不进重试队列：" + rows[2][wc.SITE_MARK])

# 终态留痕不再花第二次预算
_reset_net()
row_done = _rec("上海明亮照明有限公司", "www.mingliang-led.com")
row_done[wc.SITE_MARK] = f"{wc.SITE_MARK}：官网疑似停放/被劫持（整站不采信）"
st2 = wc.enrich_records([row_done], workers=1, budget=30, limit=50)
_assert(_calls == [], "已判终态的行不再请求（预算留给没试过的站）")
_assert(st2["cached"] == 1 and st2["eligible"] == 0, f"账上记成「沿用上次结论」：cached={st2['cached']}")

# 待重试累加 → 到上限转终态（留痕与 needs_retry 必须自洽）
_reset_net()
row_transient = _rec("上海明亮照明有限公司", "www.c-d.com")      # 表内没页面 → 不可达
st3 = wc.enrich_records([row_transient], workers=1, budget=30, limit=50)
_assert(row_transient[wc.SITE_MARK].startswith(wc.RETRY_MARK)
        and "(1次)" in row_transient[wc.SITE_MARK],
        "暂时失败留「待重试(1次)」：" + row_transient[wc.SITE_MARK])
_assert(wc.needs_retry(row_transient[wc.SITE_MARK]) is True, "待重试仍排队（企业站停机是常态）")
_reset_net()
row_transient[wc.SITE_MARK] = f"{wc.RETRY_MARK}(2次)：不可达(ConnectionError)"
wc.enrich_records([row_transient], workers=1, budget=30, limit=50)
_assert(not wc.needs_retry(row_transient[wc.SITE_MARK]),
        f"到上限转终态：{row_transient[wc.SITE_MARK]}")
_assert("清空这一列" in row_transient[wc.SITE_MARK],
        "终态留痕写明怎么重开（不许留一句永远兑不到的承诺）")
_assert(not row_transient[wc.SITE_MARK].startswith(wc.RETRY_MARK),
        "转终态后不再挂「待重试」字样（文案与排队行为一致）")

# halt 之后一律不落笔：慢站 + 极小预算，让「已认领但没写完」真的发生
_reset_net()


def _slow_fetch(url, timeout=wc.TIMEOUT):
    import time as _t
    _t.sleep(0.25)
    return _fake_fetch(url, timeout)


_slow_real = wc.fetch_html
wc.fetch_html = _slow_fetch
try:
    slow_rows = [_rec(f"上海明亮照明有限公司{i}", f"www.slow-{i + 1}.com") for i in range(4)]
    for i in range(1, 5):
        _PAGES[f"www.slow-{i}.com"] = GOOD
    st4 = wc.enrich_records(slow_rows, workers=1, budget=0.35, limit=50)
finally:
    wc.fetch_html = _slow_real
_assert(st4["budget_hit"] >= 1 and st4["skipped"] >= 1,
        f"预算到点如实报剩余量：budget_hit={st4['budget_hit']} skipped={st4['skipped']}")
_dirty = [r for r in slow_rows if r[wc.SITE_MARK] == "" and (r["电话"] or r["手机"] or r["邮箱"])]
_assert(not _dirty,
        "被 halt 拦在半路的行一个字都不写（不留「已认领未写完」的脏值）：" + str(len(_dirty)) + " 行")

# 单条内部异常不冒泡
_reset_net()


def _boom_fetch(url, timeout=wc.TIMEOUT):
    raise RuntimeError("假实现故意炸")


_orig = wc.fetch_html
wc.fetch_html = _boom_fetch
try:
    st5 = wc.enrich_records([_rec("上海明亮照明有限公司", "www.mingliang-led.com")],
                            workers=1, budget=30, limit=50)
finally:
    wc.fetch_html = _orig
_assert(st5["marked"] == 0 and any(k.startswith("内部异常") for k in st5["reasons"]),
        f"工作线程里的异常被收口成一条原因，不冒泡：{st5['reasons']}")
_assert(_net_trips == [], "全程没有一次真 HTTP 请求（requests.get 硬闸从未被触发）")

# ── H2. 追加候选的惰性问法（escalate）──────────────────────
# 这一段的每一条都对着一种**曾经真实写错过**的形状，不是装饰：
#   · 预支（在排队阶段就把要花钱的候选拼上去）→ 抽到值的行也在买单；
#   · 共用单行名额（按剩余名额追加）→ 零网络候选本来就交 4 个、单行只取前 3 个，
#     于是那一问永远轮不到，层级表多一级而实际一个包都不发（定义了没接线）；
#   · 不去重／每轮都问 → 同一个域名访问两遍，白烧一倍配额。
# 所以下面六条分别钉住：命中即停不问、占满名额仍要接得上、只问一次、不重复访问、
# 层级记对、没传时与老行为逐字相同。
print("H2. 追加候选的惰性问法（真线程池 + 假网络 + 假 provider）")
_reset_net()
_PAGES.clear()
_PAGES["www.first-hit.com"] = GOOD          # 行自带官网，第一个候选就出值
_PAGES["www.free-1.com"] = NOWHERE          # 免费候选：页面上没号码
_PAGES["www.free-2.com"] = NOWHERE
_PAGES["www.free-3.com"] = NOWHERE
_PAGES["www.rescued.com"] = GOOD            # 只有反查回来的那一个站有值
_FREE3 = [("https://www.free-1.com/", "brand"),
          ("https://www.free-2.com/", "brand"),
          ("https://www.free-3.com/", "brand")]
assert len(_FREE3) == wc.MAX_CANDIDATES_PER_ROW, \
    "夹具前提：这一串正好占满单行名额（占不满就测不到「名额挤占」那种形状）"
_esc_calls = []


def _esc(got):
    def _fn(rec):
        _esc_calls.append(str(rec.get("公司名称") or ""))
        return list(got)
    return _fn


# ① 命中即停：第一个候选就抽到值的行，绝不许为它多问一次
_reset_net()
_esc_calls.clear()
_r1 = [_rec("甲公司", "www.first-hit.com")]
_s1 = wc.enrich_records(_r1, workers=1, budget=30, limit=50,
                        escalate=_esc([("https://www.rescued.com/", "search")]))
assert _s1["ok"] == 1
_assert(_esc_calls == [],
        f"第一个候选就出值的行，一次都不问追加那一级（实际问了 {len(_esc_calls)} 次）——"
        "预支配额就是在这里烧掉的")
_assert([wc.host_of(u) for u in _calls] == ["www.first-hit.com"],
        f"问都没问，自然一个额外页都不抓：{_calls}")

# ② 免费候选占满单行名额时，追加那一级照样接得上（这一条钉的是「共用名额」那种写法）
_reset_net()
_esc_calls.clear()
_r2 = [_rec("乙公司", "")]
_s2 = wc.enrich_records(_r2, workers=1, budget=60, limit=50,
                        resolver=lambda rec: list(_FREE3),
                        escalate=_esc([("https://www.rescued.com/", "search")]))
_assert(_esc_calls == ["乙公司"],
        f"免费候选占满单行名额（{wc.MAX_CANDIDATES_PER_ROW} 个）时仍要问一次："
        f"实际 {_esc_calls}——共用名额的写法会在这里变成永轮不到")
_assert("www.rescued.com" in [wc.host_of(u) for u in _calls],
        f"问回来的候选真的去抓了（不是只拼进名单就算）：{_calls}")
_assert(_r2[0]["电话"] == PHONE and _s2["ok"] == 1,
        "反查回来的站抽到值并就地写回：" + str(_r2[0]["电话"]))
_ev2 = fe.parse_evidence(_r2[0])
_assert(_ev2.get("电话", ("", ""))[1] == "search",
        f"这个值的「补全层级」记的是搜索反查那一层（实际：{_ev2.get('电话')}）——"
        "记错层就会把外网反查的号算成官网直采的可信分")

# ③ 单行只许问一次（反查没结果也不能每试一个候选就重问一遍）
_reset_net()
_esc_calls.clear()
_r3 = [_rec("丙公司", "")]
wc.enrich_records(_r3, workers=1, budget=60, limit=50,
                  resolver=lambda rec: list(_FREE3), escalate=_esc([]))
_assert(_esc_calls == ["丙公司"],
        f"反查一无所获时也只问一次（三个免费候选都失败 ≠ 问三次）：实际 {len(_esc_calls)} 次")
# 数的是**访问过的域名集合**而不是页数：一个站最坏要翻好几个页（联系页 + 常见路径），
# 拿页数当候选数会把「一个候选五个页」读成「问了五次」。
_h3 = {wc.host_of(u) for u in _calls}
_assert(_h3 == {"www.free-1.com", "www.free-2.com", "www.free-3.com"},
        f"反查没拿到候选时，访问的域名集合不增（白抓的站就在这里多出来）：{sorted(_h3)}")

# ④ 去重必须在封顶**之前**：反查回来的四个候选里三个刚被免费候选试过（都已判过
# 「页面未见可用号码」），而追加名额只有 ESCALATE_MAX_PER_ROW 个。不去重的后果不是
# 「多访问一遍」——进程内同一域名只请求一次的缓存会把重复访问吞掉，屏幕上根本看不见；
# 后果是**重复站把唯一那个新站挤出门槛**，那一行就永远补不上。钉前者会得到一条
# 无论实现对错都通过的装饰判据（上一版就是这么写的），这里钉的是可观察的那个代价。
_reset_net()
_esc_calls.clear()
_r4 = [_rec("丁公司", "")]
_ESC4_DUP = [("https://www.free-1.com/", "search"),
             ("https://www.free-2.com/", "search"),
             ("https://www.free-3.com/", "search"),
             ("https://www.rescued.com/", "search")]
assert len(_ESC4_DUP) > wc.ESCALATE_MAX_PER_ROW, \
    "夹具前提：重复项要多到能挤掉新站，否则这条判据测不到「去重与封顶的先后」"
wc.enrich_records(_r4, workers=1, budget=60, limit=50,
                  resolver=lambda rec: list(_FREE3), escalate=_esc(_ESC4_DUP))
_h4 = [wc.host_of(u) for u in _calls]
_assert("www.rescued.com" in _h4,
        f"反查回来的重复站不许把唯一那个新站挤掉（去重必须在封顶之前）："
        f"实际访问过 {sorted(set(_h4))}")
_assert(_r4[0]["电话"] == PHONE,
        f"被挤掉新站时这一行就补不到值（后果必须落在值上，不只是名单形状）："
        f"电话=「{_r4[0]['电话']}」")
_assert(_esc_calls == ["丁公司"], f"单行只问一次这一条不受去重写法影响：{_esc_calls}")

# ⑤ 没传 escalate（默认）＝老行为，一个额外问题都不发
_reset_net()
_esc_calls.clear()
_r5 = [_rec("戊公司", "")]
_s5 = wc.enrich_records(_r5, workers=1, budget=60, limit=50,
                        resolver=lambda rec: list(_FREE3))
_h5 = {wc.host_of(u) for u in _calls}
_assert(_h5 == {"www.free-1.com", "www.free-2.com", "www.free-3.com"},
        f"没接 provider 时只试免费候选，访问的域名集合不增：{sorted(_h5)}")
# 不只要「没多访」，还要**连一次函数调用都没发生过**：没开时下游拿到的是 None
# 而不是空函数——把 None 代成空函数，「没开」与「开了但没结果」在账上就同形了。
_assert(not _esc_calls and "escalate_ask" not in _s5,
        "没传 escalate 时追加那一级的计数器一个都不涨（账上分不清没开与开了没结果）")

# ⑥ provider 抛异常等同没找到：绝不把已经跑了一半的回采轮带崩
_reset_net()


def _boom_esc(rec):
    raise RuntimeError("假 provider 故意炸")


try:
    _s6 = wc.enrich_records([_rec("己公司", "")], workers=1, budget=60, limit=50,
                            resolver=lambda rec: list(_FREE3), escalate=_boom_esc)
except Exception as _e6:                          # noqa: BLE001 - 这条判据判的就是“没冒泡”
    _s6 = {"error": type(_e6).__name__}
_assert(_s6.get("error") is None and _s6.get("escalate_err") == 1,
        f"provider 抛异常被收口成一条账（不冒泡、不中断）：{_s6}")
_assert(_net_trips == [], "H2 全程没有一次真 HTTP 请求（requests.get 硬闸从未被触发）")
# 夹具收口：H2 把 _PAGES 换成自己的那一批域名，下一段（I 会话层）仍拿明亮照明的页当
# 唯一出值页。不留这一手就是「I 段红是因为 H2 把页表清了」这种假回归。
_PAGES.clear()
_PAGES["www.mingliang-led.com"] = GOOD
_PAGES["www.a-b.com"] = NOWHERE
_reset_net()
_esc_calls.clear()

# ── I. 会话层 ───────────────────────────────────────────────
print("I. 会话层接线（ParallelSession）")
sess = ParallelSession()
logs = []
sess._log = lambda m: logs.append(str(m))
sess._user_stop = False
_assert(sess.site_enrich is False, "引擎默认关：防「自称离线的用例真联网」，界面上的勾选才是默认开")
sess_rows = [_rec("上海明亮照明有限公司", "www.mingliang-led.com"),
             _rec("某某食品公司", "")]
sess._all_live_records = lambda: list(sess_rows)
_reset_net()
_assert(sess.enrich_websites(reason="离线用例") is None and _calls == [],
        "未勾选时早退，一次请求都不发")
_assert(any("未勾选" in m and "不回采" in m for m in logs),
        "没做也要说清为什么没做：" + next((m for m in logs if "未勾选" in m), ""))
_reset_net()
sess.site_enrich = True
sess.site_enrich_workers = 1
sess.site_enrich_budget = 30
sess.site_enrich_limit = 50
s_stats = sess.enrich_websites(reason="离线用例")
_assert(s_stats and s_stats["ok"] >= 1, f"勾选后自动收尾轮真跑：{s_stats and s_stats['ok']} 行补到值")
_assert(any("净新增" in m for m in logs), "收尾账目里写着净新增几个联系方式")
_reset_net()
sess.site_enrich = False
# 上面那一轮已经把 sess_rows 标成终态了（终态不再花第二次预算，那是 H 节钉住的判据），
# 所以手动这一轮必须换一批没试过的行——否则 _calls 为空是「按设计跳过」，红的是夹具。
sess._all_live_records = lambda: [_rec("上海明亮照明有限公司", "www.mingliang-led.com")]
sess.enrich_websites(reason="手动补采", force=True)
_assert(bool(_calls), "force=True（界面手动点的那一次）不被链路开关拦住")
_assert(any("手动补采" in m and "照跑" in m for m in logs),
        "自动回采没勾选但手动点了：日志当场说清这是手动放行")
sess_rows2 = [_rec("上海明亮照明有限公司", "www.mingliang-led.com")]
sess._all_live_records = lambda: list(sess_rows2)
total, with_site, missing = sess.site_enrich_preview()
_assert((total, with_site, missing) == (1, 1, 1),
        f"preview 与真跑同一份判据：{(total, with_site, missing)}")
sess._all_live_records = lambda: [_rec("某某食品公司", "https://xxx.jdzj.com/shop/1")]
_assert(sess.site_enrich_preview()[1] == 0, "preview 不把平台页算成可回采官网")
# 同一行在两个开关下必须得出两个不同的数，而且差值得有解释：只钉单跳那一档，
# 「勾了瀑布也按 pick_site 数」这种退化成旧口径的写法就没人拦得住。
sess.waterfall = True
_assert(sess.site_enrich_preview()[1] == 1,
        "同一行勾了瀑布就算得起来（行自带的那一个官网是平台页，但还有别的途径）")
sess.waterfall = False

# ── J. 界面手动入口 ─────────────────────────────────────────
print("J. 界面手动入口（offscreen 真 MainWindow）")
import b2bcollect_gui as G                                        # noqa: E402
from PyQt5.QtWidgets import QApplication, QMessageBox              # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
boxes = []
QMessageBox.information = staticmethod(
    lambda *a, **k: boxes.append(" | ".join(str(x) for x in a[1:3])) or QMessageBox.Ok)
QMessageBox.warning = staticmethod(
    lambda *a, **k: boxes.append(" | ".join(str(x) for x in a[1:3])) or QMessageBox.Ok)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)
G.MainWindow._get_desktop_dir = lambda self: str(_DESK_SANDBOX)
w = G.MainWindow()
w.show()
app.processEvents()
ui_logs = []
w._on_log = lambda m: ui_logs.append(str(m))
saved = []
w.request_autosave = lambda: saved.append(1)

_assert(hasattr(w, "btn_site_now") and w.btn_site_now is not None, "手动补采按钮已建出")
_assert(w.btn_site_now.text() == w.BTN_SITE_ENRICH,
        f"按钮文本取自单一常量（{w.BTN_SITE_ENRICH}），恢复时不会写成另一句")
_assert(w.btn_site_now.isVisibleTo(w), "按钮在任何档位都可见（不是藏起来的功能）")
_assert(w.btn_site_now.toolTip() and "英文逗号" in w.btn_site_now.toolTip(),
        "按钮自带说明写清合并口径")
_assert("不用重跑采集" in w.chk_site.toolTip(),
        "勾选框文案指向手动按钮（不再教用户重跑一轮采集去触发收尾）")

w.session._all_live_records = lambda: []
boxes.clear()
w.btn_site_now.click()
app.processEvents()
_assert(any("没有数据" in b for b in boxes), "没数据时点它：说清没东西可补，而不是点了没反应")
_assert(w._site_worker is None, "没数据时不起线程")

w.session._all_live_records = lambda: [_rec("某", "")]
# 这一档默认开着瀑布，所以 0 行的解释必须是「所有途径一级都不接得上」那一套；只讲
# 「公司网址那列没有可用官网」是单跳时代的说法——勾了新功能却听旧解释，
# 用户会以为是老问题，而真原因可能是邮箱列也空、同主体也没有值。
# 夹具这一行要**真的一个途径都没有**：拼音通道接进来后，双字以上的中文商号都会算出候选
# （那一档的判据在 test_waterfall_leads_offline 的 H 条，不在这里重述），拿旧夹具跑就变成
# 「有途径却断言没途径」，红的不是界面而是这句话本身。单字名是当下最短的真零途径形状。
boxes.clear()
w.btn_site_now.click()
app.processEvents()
_assert(any("没有可补采的行" in b for b in boxes),
        "有数据但一行途径都没有：单独一句话说明白（标题按口径改名，不再只说官网）")
# 断言从**层级表本体**拉：个数与每一个名字都必须在话术里出现。旧断言只钉「五种途径」这
# 四个字的字面，而那句话是手抄在界面里的——阶段表多一级时旧断言会跟着旧文案一起错。
# 2026-11-18 再收紧一层：要额外花外网配额才会走的那一级（现网反查）没开时**不许算进话里**：
# 「六种途径都试过」在只跑了五种的时候是一句看不出来的谎，而它偏偏长得像合规。
# 所以两个口径各验一遍：开关关→个数只算本轮真会走的、且不得点名那一级；
# 开关开→个数与名字都要把那一级补齐。
_act = _wf.active_stages(bool(w.session.expensive_lookup))
_assert(any(f"{len(_act)} 种途径都试过" in b for b in boxes),
        f"勾了瀑布、没开现网反查时 0 行的解释跟着口径换：途径**个数**由本轮真会走的层级算（{len(_act)}）")
_bad_tiers = [s for s in _act if _wf.stage_name(s) not in " ".join(boxes)]
_assert(not _bad_tiers,
        f"话术里逐级点名到本轮真会走的每一级，且用的就是「补全层级」列落库的那个名字"
        f"（缺这些级：{_bad_tiers}）：白话描述与层级名分家最难发现")
_gated_said = [s for s in _wf.STAGES if s not in _act
               and _wf.stage_name(s) in " ".join(boxes)]
_assert(not _gated_said,
        f"没开的现网反查不许被说成「试过的途径」（话术里出现了：{_gated_said}）——"
        "把没花的配额报成花过，用户下次就不会信这一列的账")
w._set_expensive_lookup(True)      # 走唯一写入口，顺带验依赖（此时瀑布是开的）
boxes.clear()
w.btn_site_now.click()
app.processEvents()
_act2 = _wf.active_stages(True)
_assert(len(_act2) == len(_wf.STAGES),
        "夹具前提：开了现网反查就是全层级都走（否则上面那两条对照不是同一个口径）")
_assert(any(f"{len(_act2)} 种途径都试过" in b for b in boxes),
        f"开了现网反查后个数要把那一问也算进去（{len(_act2)}）")
_missing2 = [s for s in _act2 if _wf.stage_name(s) not in " ".join(boxes)]
_assert(not _missing2,
        f"开了现网反查后那句要把「{_wf.stage_name('search')}」也点名出来（缺：{_missing2}）")
w._set_expensive_lookup(False)     # 还原默认，别把开关状态留给后面的章节
_assert(w._site_worker is None, "无途径可补时也不起线程")
w.session.waterfall = False
boxes.clear()
w.btn_site_now.click()
app.processEvents()
_assert(any("平台页" in b and "种途径" not in b for b in boxes),
        "反向对照：没勾瀑布时只讲单跳口径（公司网址里的平台页那几条），不塞瀑布的话术")
w.session.waterfall = True

w.session._all_live_records = lambda: [_rec("上海明亮照明有限公司", "www.mingliang-led.com")]


class _BusyWorker:
    def isRunning(self):
        return True


w.worker = _BusyWorker()
ui_logs.clear()
w.btn_site_now.click()
app.processEvents()
_assert(any("采集还在跑" in m for m in ui_logs), "采集进行中点它：说明白为什么这次不动")
_assert(w._site_worker is None, "采集进行中不起并发的一轮")
w.worker = None

_calls.clear()
w.chk_site.setChecked(False)
ui_logs.clear()


class _Sig:
    """替 pyqtSignal：connect 记下槽、emit 当场调用——回调链路照原样验，不必真起线程。"""

    def __init__(self):
        self.cb = None

    def connect(self, fn):
        self.cb = fn

    def emit(self, value):
        if self.cb is not None:
            self.cb(value)


class _FakeThread:
    """替掉真线程：跑起来时不发结果，由测试在验完「在途形态」后手动 emit。"""

    def __init__(self, session):
        self.session = session
        self.done = _Sig()
        self._running = True
        _threads.append(self)

    def isRunning(self):
        return self._running

    def start(self):
        pass        # 真线程在这里才会去联网；离线用例把结果交给测试手动发回


_threads = []
_orig_thread = G.SiteEnrichWorker
G.SiteEnrichWorker = _FakeThread
w.btn_site_now.click()
app.processEvents()
G.SiteEnrichWorker = _orig_thread
_assert(bool(_threads), "前置都过了：起了一轮补采（走的是后台线程，不在 GUI 线程里联网）")
_assert(w.btn_site_now.isEnabled() is False and w.btn_site_now.text() == "补采中…",
        "跑起来时按钮自己表态（灰着 + 改字），不用用户猜有没有点上")
_assert(w.chk_site.isChecked() is False,
        "手动点这一次不改用户的勾选状态（那条开关管的是自动跑）")
_assert(any("手动补采开始" in m for m in ui_logs),
        "点下去当场报账：" + next((m for m in ui_logs if "手动补采开始" in m), ""))
ui_logs.clear()
w.btn_site_now.click()          # 故意在途再点一次
app.processEvents()
_assert(len(_threads) == 1 and not ui_logs, "上一轮还在跑时不叠第二轮（也不留一句噪音日志）")
ui_logs.clear()
saved.clear()
_threads[0].done.emit({"ok": 1, "sites": 1, "new_values": 3, "marked": 1})
app.processEvents()
_assert(w.btn_site_now.isEnabled() and w.btn_site_now.text() == w.BTN_SITE_ENRICH,
        "回来恢复按钮（漏恢复就等于要用户重启工具）")
_assert(any("手动补采结束" in m for m in ui_logs), "回来报一次账")
_assert(saved, "补完自动落盘（用户要的是表格里的值，不是内存里的值）")
saved.clear()
ui_logs.clear()
_threads[0].done.emit(None)
app.processEvents()
_assert(not saved and any("没有实际访问官网" in m for m in ui_logs),
        "没跑成的那一轮不假装落盘，只说明原因")
_assert(w.btn_site_now.isEnabled(), "异常收口后按钮同样恢复")

# 真线程类本身：异常也必须发 done（卡在「补采中…」是最坏的失败形态）
_run_done = []
w.session.enrich_websites = lambda **kw: (_ for _ in ()).throw(RuntimeError("故意炸"))
w._site_worker = None
t = G.SiteEnrichWorker(w.session)
t.done.connect(lambda v: _run_done.append(v))
_orig_log = w.session._log
w.session._log = lambda m: None
t.run()
w.session._log = _orig_log
_assert(_run_done == [None], "线程里炸了也照发 done：界面绝不会停在「补采中…」")
w.session.enrich_websites = ParallelSession.enrich_websites.__get__(w.session)

# 并发拒止与退出收口（源码级判据：这些路径点到界面上验不全）
_gsrc = Path(G.__file__).read_text(encoding="utf-8")
_ci = _gsrc.index("def closeEvent", _gsrc.index("class MainWindow"))
_close = _gsrc[_ci:_ci + 3000]
_assert("_site_worker" in _close and "_final_save_sync()" in _close,
        "退出时等这一轮并把已补到的值同步落盘（排队信号在退出路径上不会跑）")
_start = _gsrc[_gsrc.index("def _on_start"):_gsrc.index("def _on_start") + 6000]
_assert("_site_worker" in _start, "补采在跑时「开始采集」拒并发（同一份官网缓存与预算）")
_reset = _gsrc[_gsrc.index("def _on_reset_task"):_gsrc.index("def _on_reset_task") + 4000]
_assert("_site_worker" in _reset, "补采在跑时「开始新任务」拒并发（否则补到的值被复位丢掉）")
_ps = Path(G.__file__).with_name("parallel_session.py").read_text(encoding="utf-8")
_doc = _ps[_ps.index("def enrich_websites"):_ps.index("def site_enrich_preview")]
_assert("手动" in _doc and "force" in _doc, "会话 docstring 说的手动入口真存在")
_assert('enrich_websites(reason="手动补采", force=True)' in _gsrc,
        "界面那一处确实用 force 走通了 docstring 承诺的那条路（说到必须做到）")

# ── 收口：把网络桩全部还原，证明本套件没有留下后门 ─────────────
wc.fetch_html = _real_fetch
wc.socket.getaddrinfo = _real_getaddrinfo
if _requests is not None and _real_requests_get is not None:
    _requests.get = _real_requests_get
_assert(wc.fetch_html is not _fake_fetch and wc.socket.getaddrinfo is _real_getaddrinfo,
        "桩已还原（本套件跑完不影响后续套件的真实性）")
_assert(_net_trips == [], "整轮从未发出真请求")

# ── 收尾：趁 QApplication 还活着把窗口拆掉（顺序与 b2bcollect_gui.main 的 finally 同一条）──
# 套件从不调 app.exec_()，也就绕过了生产那段有序收尾；而生产那段存在的理由正是「让解释器
# 自己拆 Qt 对象会撞上退出 SIGSEGV」（线上实测抓到过栈）。这里实测同样 6 次崩 1 次，且崩在
# 打印汇总**之后**——批跑只看退出码，全绿也会被判成一次失败。
# 走 hide() 而不是 close()：close 会进生产 closeEvent（等线程、弹确认框），本套件测的不是
# 退出那条路；hide 之后把引用清掉，refcount 当场让 sip 删掉 C++ 对象，此时 app 仍在。
w.hide()
w = None
app.processEvents()

print("\n" + "=" * 60)
total_n = _passed + _failed
print((f"全部通过 ✅ ({_passed}/{total_n})") if _failed == 0
      else f"通过 {_passed}/{total_n}，失败 {_failed} ❌")
print("=" * 60)
# 判定到此为止：屏幕上这句和批跑读到的退出码必须是同一个来源。这套件自己建过
# QApplication，而 Qt/线程在解释器收尾时的析构会随机段错误（本机实测：keyword_resume
# 全绿 88 项之后 rc=139，被批跑读成一次失败，单跑又绿——真红就是这么被当成抖动的）。
# flush 之后直接把退出码交给操作系统，不让任何收尾动作再改写判定。
sys.stdout.flush()
sys.stderr.flush()
os._exit(1 if _failed else 0)
