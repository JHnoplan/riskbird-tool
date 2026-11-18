# -*- coding: utf-8 -*-
"""test_site_discovery_offline.py —— 「按公司名找官网」发现层的离线回归。

用户诉求原话：「补采质量也太差了……不可达 327、HTTP403 204、页面未见可用号码/邮箱 191，
990 行只净新增 17 个联系方式」，以及「参考 v16 那套方法集成进工具，只为更高效、数据质量
更高」。发现层是那批方法里唯一能同时改善两者的部分（先把"该访问哪个域"问对，才谈得上
抽值），所以本套件钉的是**候选生产与通道健康**，不是抽值（抽值判据在
test_site_enrich_offline.py，瀑布顺序判据在 test_waterfall_leads_offline.py）。

全程零网络：socket.getaddrinfo / create_connection 换成一碰就抛，requests.get 装硬闸，
`site_discovery` 的两个取页出口（fetch_html / fetch_html_post）由本表驱动。
时间也换成可控假时钟——否则「退避放大」「熔断到点恢复」这两类判据只能靠真睡 120 秒来测，
那种用例在批跑里迟早被人注释掉，判据就等于没有。

  A. 零网络硬保证：本套件整轮从未发出真请求；模块导入本身不碰网
  B. 通道 ①（商铺子域反推）：v16 实证 hapstar.cn.b2b168.com → hapstar.cn；三种尾巴写法
     （`.cn` / `_com` / `-cn`）；三类**不该**反推（平台裸域 www、编号形租户、自有域名前缀）
  C. 通道 ②（拼音候选）：三族齐全且按撞车率排序；生僻字整条放弃；非中文名不产拼音
  D. 形状闸 `_candidate_ok`：编号形（**10086.cn 是中国移动**）、下划线拼接产物、纯数字
     一律拦；两个字符的短字号不误杀；噪声域/平台域一律拦
  E. 可信序与封顶：确证域名排第一，同段另一 TLD 只占一个位且紧随其后，全拼族必须活到
     下游（封顶截断的是低可信那族，不是高可信）
  F. 通道 ③④（现网反查）：三型记账（limited/shell/err）分得开；**JS 壳不得记成"正常无
     结果"**（这条是 Bing 在本机实测返回 JS 壳逼出来的）；退避放大、连续三回熔断、熔断
     期间跳过要留痕、到点恢复、配额用完留痕、reset_state 清账
  G. 降级留痕：词表拿不到时 stats()["degraded"] 非空且 summary_lines 打印警告
     （静默返回空 = 报表上"品牌域名推导 0 家"与"代码坏了"同形，是本项目记过账的形态）
  H. 口径同源：发现的层级 slug 必须在 field_evidence 注册表里；报表数字与 stats 一致

运行：python test_site_discovery_offline.py
"""
import os
import re
import socket
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# 状态落点钉进沙箱：DNS 磁盘缓存与审计日志都问 tool_config.state_file，不打进来
# 就会把测试造的域名写进开发机的真实索引目录（下次真跑读到的是假缓存）。
os.environ["B2B_STATE_DIR"] = str(Path(tempfile.mkdtemp(prefix="sd_state_")))

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


# ── A. 网络硬闸：本套件的"离线"必须是可证伪的 ───────────────────
_net_trips = []
_real_getaddrinfo = socket.getaddrinfo
_real_create_conn = socket.create_connection
try:
    import requests as _requests
    _real_requests_get = _requests.get
except Exception:                                     # 打包环境没装 requests 也要能跑
    _requests, _real_requests_get = None, None


def _bomb_getaddrinfo(*a, **k):
    _net_trips.append(("getaddrinfo", a[:1]))
    raise AssertionError("发现层发出了 DNS 查询")


def _bomb_create_connection(*a, **k):
    _net_trips.append(("create_connection", a[:1]))
    raise AssertionError("发现层建立了真连接")


socket.getaddrinfo = _bomb_getaddrinfo
socket.create_connection = _bomb_create_connection


def _bomb_requests(url=None, *a, **k):
    _net_trips.append(("requests.get", url))
    raise AssertionError("发现层绕过 website_contact 直接发请求")


if _requests is not None:
    _requests.get = _bomb_requests

import field_evidence as fe                           # noqa: E402
import pinyin_lite as py                              # noqa: E402
import site_discovery as sd                           # noqa: E402
import website_contact as wc                          # noqa: E402

_real_fetch = wc.fetch_html
_real_fetch_post = wc.fetch_html_post

print("A. 零网络硬保证")
_assert(not _net_trips, "模块导入过程本身没碰网（socket 已换成会抛异常的桩）")
_assert(sd.local_candidates({"公司名": "杭州怡星机电设备有限公司"}),
        "零网络通道在 DNS 全炸的环境里照样产候选（预览不再卡 GUI 线程）")


# ── 假时钟：退避/熔断/恢复这类判据不能靠真睡觉 ──────────────────
_NOW = [1_700_000_000.0]
_sleeps = []


class _Clock:
    @staticmethod
    def time():
        return _NOW[0]

    @staticmethod
    def sleep(sec):
        _sleeps.append(float(sec))
        _NOW[0] += float(sec)          # 睡觉推进时钟：熔断到点恢复才可测


def _advance(sec):
    _NOW[0] += float(sec)


sd.time = _Clock
wc.time = _Clock                        # 预检/节流同一套时钟，别让两个模块各走各的


# ── 现网通道的表驱动桩 ──────────────────────────────────────────
_GET_PLAN = []                          # [(文本, why)] 按调用顺序消费
_POST_PLAN = []
_calls = {"get": 0, "post": 0}


def _fake_fetch(url, timeout=None, **kw):
    _calls["get"] += 1
    plan = _GET_PLAN or [("", "计划外调用")]
    item = plan.pop(0) if len(plan) > 1 else plan[0]
    text, why = item if isinstance(item, tuple) else (item, "")
    return (text, url, why) if text else ("", "", why or "空响应")


def _fake_fetch_post(url, data=None, timeout=None, **kw):
    _calls["post"] += 1
    plan = _POST_PLAN or [("", "计划外调用")]
    item = plan.pop(0) if len(plan) > 1 else plan[0]
    text, why = item if isinstance(item, tuple) else (item, "")
    return (text, url, why) if text else ("", "", why or "空响应")


wc.fetch_html = _fake_fetch
wc.fetch_html_post = _fake_fetch_post


def _reset(**_ignored):
    """每个用例前清场：通道账、节流状态、假时钟、调用计划。"""
    sd.reset_state()
    _GET_PLAN.clear()
    _POST_PLAN.clear()
    _calls["get"] = _calls["post"] = 0
    _sleeps.clear()
    _NOW[0] = 1_700_000_000.0


# ── B. 通道 ①：商铺子域反推（v16 的主发现）──────────────────────
print("B. 商铺子域反推（零网络）")
_rec = lambda **kw: dict(kw)
_hap = sd.subdomain_candidates(_rec(详情URL="https://hapstar.cn.b2b168.com/company.html"))
_assert(_hap == ["hapstar.cn"], f"v16 实证：hapstar.cn.b2b168.com → {_hap}（平台登记的自有域名）")
_assert(sd.subdomain_candidates(_rec(公司网址="yixingjd_com.b2b168.com")) == ["yixingjd.com"],
        "下划线尾巴写法 yixingjd_com → yixingjd.com")
_assert(sd.subdomain_candidates(_rec(公司网址="acef_comcn.huangye88.com")) == ["acef.com.cn"],
        "comcn 尾巴 → acef.com.cn（三截尾巴也要认得）")
_assert(sd.subdomain_candidates(_rec(详情URL="https://www.b2b168.com/search?q=x")) == [],
        "平台裸域（前缀只有 www）不反推：那是搜索页不是店铺")
_assert(sd.subdomain_candidates(_rec(详情URL="https://x12345.b2b168.com/")) == [],
        "编号形租户 x12345 不反推（拼出来的域名必然指向别人家）")
_assert(sd.subdomain_candidates(_rec(公司网址="https://www.hapstar.cn/")) == [],
        "自有域名不按前缀切：www 不是租户标签，切法只许落在平台域上")
_assert(sd.subdomain_candidates(_rec(公司网址="https://img.a.com/x.png")) == [],
        "非平台域一律不反推（img.a.com 切出 a.com 那是 CDN 不是官网）")
_assert(sd.subdomain_candidates({}) == [] and sd.subdomain_candidates(None) == [],
        "空行/None 不抛异常（发现层跑在批量循环里，一家炸不能带崩整轮）")


# ── C. 通道 ②：拼音候选 ────────────────────────────────────────
print("C. 拼音候选（零网络生成 + 排序判据）")
_assert(py.lazy_pinyin("怡星机电设备有限公司") == ["yi", "xing", "ji", "dian",
                                                   "she", "bei", "you", "xian", "gong", "si"],
        "读音钉死：pinyin_lite 与生成它的那份 pypinyin 逐字一致（跨机可复算的前提）")
_yx = sd.pinyin_hosts("杭州怡星机电设备有限公司")
_assert(_yx[:2] == ["yixing.cn", "yixing.com"], f"全拼族排最前（撞车率最低）：{_yx}")
_assert("hzyx.cn" in _yx, "城市缩写+首字母族仍在（v16 实测拼音通道 52% 命中的组成）")
_assert("yx.cn" not in _yx, "纯首字母 yx.cn 被形状闸拦掉：两个字母的组合早被占光")
_gm = sd.pinyin_hosts("杭州光明照明工程有限公司")
# 断的是实现承诺的**族序**而不是「有候选就行」：全拼 guangmingzhaoming 长 18 字符，
# 被长度闸判死（超长全拼域名几乎没人注册），剩下两族按撞车率排：城市缩写+首字母
# （6 段）比纯首字母（4 段）难撞，所以 hzgmzm 必须排在 gmzm 前面。
_assert(_gm[:2] == ["hzgmzm.cn", "hzgmzm.com"],
        f"超长全拼被弃 → 城市缩写族排在纯首字母族前（族序即撞车率序）：{_gm}")
_assert(sd.pinyin_hosts("上海A镠科技有限公司") == [],
        "有字读不出（GB2312 之外的扩展字）整条放弃，不拼半截域名")
_assert(sd.pinyin_hosts("Krones AG") == [], "纯英文名不产拼音候选")
_assert(sd.pinyin_hosts("") == [] and sd.pinyin_hosts(None) == [], "空名不产候选")
_assert(all("cctv.com" not in d and "gov.cn" not in d for d in _yx),
        "拼音族不会拼出噪声域（过的是同一道闸）")


# ── D. 形状闸：四条通道共用的一道 ──────────────────────────────
print("D. 候选形状闸")
_assert(sd._candidate_ok("hapstar.cn"), "合法自有域名放行")
_assert(sd._candidate_ok("loco-china.cn"), "带连字符的自有域名放行（历史上裸子串误杀过它）")
_assert(not sd._candidate_ok("10086.cn"), "**10086.cn（中国移动）必须拦**：抓到的是运营商客服号")
_assert(not sd._candidate_ok("x12345.cn"), "字母+长编号拦")
_assert(not sd._candidate_ok("yixingjdcom.cn"), "下划线被吃掉后的拼接产物拦")
_assert(not sd._candidate_ok("www.b2b168.com"), "平台域拦（走 is_platform_host 那一份口径）")
_assert(not sd._candidate_ok("gd.gov.cn"), "政务域拦")
_assert(not sd._candidate_ok("bing.com"), "搜索引擎自己拦（否则会把结果页当官网抓）")
_assert(not sd._candidate_ok(""), "空串拦")
_assert(not sd._candidate_ok("http://a.com"), "带协议的整串 URL 不当域名放行")
_assert(not sd._candidate_ok("ab.cn"), "两字段的域名拦（BRAND_MIN_LEN 同源口径）")
_assert(sd._candidate_ok("scom.cn"), "scom 这种本来就是完整字号的段不被 TLD 尾巴规则误杀")
_assert(sd._noise_strip("  HapStar.CN. ") == "hapstar.cn", "现网通道收口做大小写/点清洗")
_assert(sd._noise_strip("bad host") == "" and sd._noise_strip("a@b.com") == "",
        "现网通道收口拦空格与带凭据的 host")
_assert(sd._noise_strip("10086.cn") == "", "同一道闸对现网反查同样生效（不是只管本地通道）")


# ── E. 可信序与封顶 ────────────────────────────────────────────
print("E. 候选顺序与预算")
_order = sd.local_candidates(_rec(详情URL="https://hapstar.cn.b2b168.com/company.html",
                                 公司名称="上海怡星机电设备有限公司"))
_urls = [u for u, _t in _order]
_assert(_urls[0] == "https://hapstar.cn/", f"确证域名占第一位（{_urls}）")
_assert(_urls[1] == "https://hapstar.com/",
        f"同段另一 TLD 紧随其后而不是排到末尾被截掉（{_urls}）")
_assert(len(_order) <= sd.LOCAL_MAX_CANDIDATES,
        f"发现层自己封顶（{len(_order)} ≤ {sd.LOCAL_MAX_CANDIDATES}）：不靠下游截断")
_assert("https://hapstar.com.cn/" not in _urls,
        f"同一商号段不重复猜第三个 TLD（{[u for u in _urls if 'hapstar' in u]}）")
_assert("https://shyx.cn/" in _urls or "https://yixing.cn/" in _urls,
        "确证域名之外仍留给推导通道一个位（不能因为有店铺子域就完全不做兜底）")
_junk = sd.local_candidates(_rec(详情URL="https://10086.b2b168.com/", 公司名="数字铺公司"))
_assert(_junk == [], f"纯数字租户 + 无有效商号 → 一个候选都不产（{_junk}）")
_assert(sd.local_candidates({}) == [], "空行零候选")
_assert(all(u.startswith("https://") for u, _t in _order), "候选一律是 https 绝对地址")


# ── F. 现网通道：三型记账与熔断 ────────────────────────────────
print("F. 现网反查的通道健康账")
_BIG = "x" * (sd.SERP_MIN_BYTES + 500)
_serp_ok = ('<ol class="b_results"><li class="b_algo"><h2><a href="https://hapstar.cn/">'
            '上海怡星机电设备有限公司官网</a></h2></li></ol>' + "y" * sd.SERP_MIN_BYTES)
_serp_shell = '<html><body><div id="shell">正在载入…</div>' + "z" * (sd.SERP_MIN_BYTES + 200)
_serp_empty_but_real = '<ol class="b_results">' + "w" * (sd.SERP_MIN_BYTES + 200)

# F1：JS 壳必须记成 shell，并且连续三回把这一层熔断
_reset()
_GET_PLAN[:] = [(_serp_shell, "")] * 6
got = [sd.serp_candidates("上海怡星机电设备有限公司") for _ in range(3)]
st = sd.stats()["serp"]
_assert(got == [[], [], []], f"JS 壳不产候选（{got}）")
_assert(st["shell"] == 3 and st["limited"] == 0,
        f"整页没有结果结构记 shell 三笔，不混进「被限流」（实得 shell={st['shell']} limited={st['limited']}）")
_assert(st["tripped"] == 1, f"连续三回熔断一回（tripped={st['tripped']}）")
_calls_before = _calls["get"]
_assert(sd.serp_candidates("另一家公司") == [], "熔断期间直接放弃这一家，不睡在这")
_assert(_calls["get"] == _calls_before, "熔断期间真的一个请求都没发（省下的就是墙钟）")
_assert(sd.stats()["serp"]["skipped_cold"] == 1, "熔断期间跳过的家数留痕（报表要说得出几家没问）")
_advance(sd.DEAD_S + 1)
_GET_PLAN[:] = [(_serp_ok, "")]
_assert(sd.serp_candidates("上海怡星机电设备有限公司") == ["hapstar.cn"], "到点恢复并能反出候选")
_assert(sd.stats()["serp"]["hit"] == 1, "命中计数只记真反出候选的那些家")
_assert("熔断" in " ".join(sd.summary_lines()) and "跳过" in " ".join(sd.summary_lines()),
        f"收尾话术里熔断与跳过都说得出口：{sd.summary_lines()}")

# F2：真结果页但这家确实没结果 ≠ 通道坏了（这是最容易误判的一刀）
_reset()
_GET_PLAN[:] = [(_serp_empty_but_real, "")] * 5
outs = [sd.serp_candidates(f"查无此家{i}") for i in range(5)]
st = sd.stats()["serp"]
_assert(outs == [[]] * 5, "结果页里没有匹配项 → 返回空候选")
_assert(st["tripped"] == 0 and st["shell"] == 0 and st["limited"] == 0,
        f"「正常无结果」不计故障、不熔断（否则 100 家空手会把整层锁死）：{st}")
_assert(st["asked"] == 5 and st["hit"] == 0, "问了 5 家、反出 0 家：这两个数必须分开记")

# F3：失败分型（不可达=err、403=limited）
_reset()
_GET_PLAN[:] = [("", "不可达(ConnectionError)"), ("", "HTTP403"), ("", "超时")]
for _ in range(3):
    sd.serp_candidates("某公司")
st = sd.stats()["serp"]
_assert(st["err"] == 2 and st["limited"] == 1,
        f"不可达/超时记 err、HTTP403 记 limited（{st['err']}/{st['limited']}）：两类退避理由不同")
_assert(st["tripped"] == 1, "连续三回不管哪一型都该歇一会儿")

# F4：退避放大与慢慢收回
_reset()
gap0 = sd.stats().get("serp", {}).get("gap") or sd._BASE["serp"]["gap"]
_GET_PLAN[:] = [(_serp_shell, "")]
sd.serp_candidates("某公司")
gap1 = sd._st("serp")["gap"]
_advance(60)                                             # 出熔断
_GET_PLAN[:] = [(_serp_shell, "")]
sd.serp_candidates("某公司")
gap2 = sd._st("serp")["gap"]
_assert(gap1 > gap0 and gap2 > gap1, f"被限流两次间隔递增 {gap0} → {gap1} → {gap2}")
_advance(60)
_GET_PLAN[:] = [(_serp_ok, "")]
sd.serp_candidates("上海怡星机电设备有限公司")
_assert(sd._st("serp")["gap"] < gap2, f"顺利一回间隔往回收（{gap2} → {sd._st('serp')['gap']}）")
_advance(sd.GAP_MAX * 2)
_GET_PLAN[:] = [(_serp_shell, "")] * 3
for _ in range(3):
    sd.serp_candidates("某公司")
    _advance(60)
_assert(sd._st("serp")["gap"] <= sd.GAP_MAX, f"退避有上限，不会指数跑飞（{sd._st('serp')['gap']}）")

# F5：配额用完要说得出几家没问
_reset()
sd._st("serp")["round_cap"] = 2
_GET_PLAN[:] = [(_serp_ok, "")]
outs = [sd.serp_candidates("上海怡星机电设备有限公司") for _ in range(4)]
st = sd.stats()["serp"]
_assert(st["asked"] == 2 and st["skipped_cap"] == 2,
        f"配额 2 用完后两家没问且计入 skipped_cap（{st['asked']}/{st['skipped_cap']}）")
_assert("配额用完" in " ".join(sd.summary_lines()), "收尾话术里这一句必须出现")

# F6：八方 POST 通道
_reset()
_shop_page = ('<div class="cInfo"><a href="https://hapstar.cn.b2b168.com/">上海怡星机电设备'
              '有限公司</a></div>' + "p" * 3000)
_POST_PLAN[:] = [(_shop_page, "")]
got = sd.shop_candidates("上海怡星机电设备有限公司")
_assert(got == ["hapstar.cn"], f"八方搜索页 → 租户子域 → 候选域名（{got}）")
_assert(_calls["post"] == 1, "一家只发一次 POST")
_POST_PLAN[:] = [("p" * 3000, "")]
sd.reset_state()
_assert(sd.shop_candidates("某公司") == [] and sd.stats()["shop"]["shell"] == 1,
        "整页没有租户子域 → 记 shell（不能读成「这家在八方没有店」）")
_POST_PLAN[:] = [("", "HTTP403")]
sd.reset_state()
_assert(sd.shop_candidates("某公司") == [] and sd.stats()["shop"]["limited"] == 1,
        "POST 通道同样分型记账")
# 归属预筛：标题里见不到这家的字样就不给候选
_POST_PLAN[:] = [('<a href="https://otherco.b2b168.com/">完全无关的同行公司</a>' + "q" * 3000, "")]
sd.reset_state()
_assert(sd.shop_candidates("上海怡星机电设备有限公司") == [], "同屏文字里没有主体字样 → 预筛拦下")

# F7：discover_expensive 的先后与封顶
_reset()
_POST_PLAN[:] = [(_shop_page, "")]
_GET_PLAN[:] = [(_serp_ok, "")]
out = sd.discover_expensive(_rec(公司名称="上海怡星机电设备有限公司"))
_assert(out and out[0] == ("https://hapstar.cn/", sd.SHOP_TIER), f"先商铺后 SERP（{out}）")
_assert(len(out) <= sd.EXPENSIVE_MAX_PER_ROW, f"一家最多 {sd.EXPENSIVE_MAX_PER_ROW} 个候选（{out}）")
_reset()
_POST_PLAN[:] = [(_shop_page, "")]
_GET_PLAN[:] = [(_serp_ok, "")]
sd.discover_expensive(_rec(公司名称="上海怡星机电设备有限公司"), use=("shop",))
_assert(_calls["get"] == 0, "只开商铺通道时一个 SERP 请求都不发（配额是省出来的）")
_assert(sd.discover_expensive({}) == [], "没有公司名就不问现网（问了也是白问）")


# ── G. 降级留痕：词表拿不到时必须说得出这一层废了 ───────────────
print("G. 降级留痕")
_reset()
import enrich_waterfall as wf                         # noqa: E402
_real_tokens = wf.brand_tokens
try:
    del wf.brand_tokens
    _ = sd.local_candidates(_rec(公司网址="https://www.mingliang-led.com/",
                                公司名称="照明公司"))
    st = sd.stats()
    _assert("brand_tokens" in (st.get("degraded") or {}),
            f"词表被改名 → degraded 里留下原因（{st.get('degraded')}）")
    _assert("降级" in " ".join(sd.summary_lines()), "收尾话术打印降级警告（不是静默少功能）")
finally:
    wf.brand_tokens = _real_tokens
sd.reset_state()
_assert(not (sd.stats().get("degraded") or {}), "reset_state 同时清降级标记（否则永久性假故障）")
_reset()
# 拔词表又插回来之后，商号通道必须真的活过来：这里断的是**具体候选**而不是
# 「返回列表非空」——子域反推那一腿根本不依赖词表，只看非空的话，词表这条路没恢复
# 也照样绿（上一版这里就是一条 `or True`，等于自己写了个永远绿的装饰）。
_ml = sd.local_candidates(_rec(公司网址="https://www.mingliang-led.com/"))
_assert(("https://mingliang-led.cn/", "brand") in _ml,
        f"词表恢复后自有域名的商号段重新进候选：{_ml}")
_assert(("https://krones.cn/", "brand") in sd.local_candidates(_rec(公司名称="Krones AG")),
        "英文名走商号通道（纯 ASCII 名没有拼音可拼，这是它唯一的一路）")


# ── H. 口径同源 ───────────────────────────────────────────────
print("H. 口径与注册表同源")
_tiers = {sd.SUBDOMAIN_TIER, sd.PINYIN_TIER, sd.SHOP_TIER, sd.SERP_TIER}
_assert(_tiers <= set(fe.TIER_LABEL),
        f"发现层报出去的层级都在 field_evidence 注册表里（{_tiers}）：宁可复用档位，不造野 slug")
_assert(sd.SERP_TIER == "search", "SERP 单独归「搜索反查」档：结果页本身不算证据，采的是抓回来的正文")
_assert(sd.SHOP_TIER == "brand" and sd.SUBDOMAIN_TIER == "brand",
        "商铺子域反查与本地推导同档：层级记的是可信度不是成本（成本另有 TIER_COST）")
_assert(fe.TIER_COST["search"] >= fe.TIER_COST["brand"],
        "搜索反查的成本档不低于品牌推导：多花一次查询才拿到域名")
sd.reset_state()
_GET_PLAN[:] = [(_serp_shell, "")]
for _ in range(1):
    sd.serp_candidates("某公司")
_summary = " ".join(sd.summary_lines())
_assert(_summary and "问 1 家" in _summary and "反出候选 0 家" in _summary,
        f"话术里的数字直接来自 stats（{_summary}）：报表与台账不同源就会两张皮")
_assert(not sd.summary_lines() or isinstance(sd.summary_lines(), list), "summary_lines 返回话术列表")
# 一条都没问时不该编出行来
sd.reset_state()
_assert(sd.summary_lines() == [], "整轮没问现网 → 一行都不打印（不写「搜索层：问 0 家」这种废话）")
# 噪声名单自检：不许出现拼错到永远命中不了的条目
_assert("b2b168.com" in wc.PLATFORM_HOSTS,
        "八方平台域在 PLATFORM_HOSTS 里（is_noise_host 走那一份口径，不是另列一遍）")
_bad = [d for d in sd.NOISE_HOSTS if not re.fullmatch(r"[a-z0-9][a-z0-9.\-]*\.[a-z]{2,}", d)]
_assert(not _bad, f"NOISE_HOSTS 里没有病态/拼错条目（{_bad}）：这种项永远命中不了，只会给人「覆盖已经够了」的错觉")


# ── 收口：把网络桩全部还原，证明本套件没有留下后门 ─────────────
wc.fetch_html = _real_fetch
wc.fetch_html_post = _real_fetch_post
wc.time = sys.modules["time"]
sd.time = sys.modules["time"]
socket.getaddrinfo = _real_getaddrinfo
socket.create_connection = _real_create_conn
if _requests is not None and _real_requests_get is not None:
    _requests.get = _real_requests_get
_assert(wc.fetch_html is _real_fetch and wc.fetch_html_post is _real_fetch_post
        and wc.socket.getaddrinfo is _real_getaddrinfo,
        "桩已还原（本套件跑完不影响后续套件的真实性）")
_assert(not _net_trips, "整轮从未发出真请求（所有现网行为都由表驱动夹具产生）")

print("\n" + "=" * 60)
total_n = _passed + _failed
print((f"全部通过 ✅ ({_passed}/{total_n})") if _failed == 0
      else f"通过 {_passed}/{total_n}，失败 {_failed} ❌")
print("=" * 60)
# 退出码交给操作系统：本套件不建 QApplication，但假时钟/桩的收尾同样不该改写判定。
sys.stdout.flush()
sys.stderr.flush()
os._exit(1 if _failed else 0)
