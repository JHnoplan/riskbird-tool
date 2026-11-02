# 离线自测：索比光伏网(company.solarbe.com) 解析链路（真实页面缓存，不触网、不开浏览器）
#
# 本站已按用户指令从工具的关键词并行通道下线，solarbe_crawler.py 保留作
# 独立全量采集脚本（debug/scrape_solarbe_full.py）的解析库，本测试守护那条链路。
#
# 覆盖 2026-11-02 实机探测确认的几个关键点：
#   · 详情必须用手机版 UA 才拿得到明文联系方式（桌面版被 Destoon 图片化）；
#   · 号码只取正文块 + tel: 链接，meta description 里灌的赌博号绝不能入库；
#   · 传真行不参与电话列；86- 前缀按形态归一；
#   · 每个切片最多 6 页（120 家）→ 靠「行业×地区」细分展开；
#   · 200 + 空正文＝可重采的导航失败（不写种子库锁死），「公司不存在」才是终态。
import sys, pathlib, re
sys.path.insert(0, "/Users/jhnoplan/Desktop/PythonDemo/b2bcollect_build")

from solarbe_crawler import (SolarbeCrawler, _UA, _UA_MOBILE, CATIDS, AREAIDS,   # noqa: E402
                             _NAME_PLACEHOLDER)
from parallel_session import SITE_ORDER                                       # noqa: E402
from cdp_base import strip_country_code                          # noqa: E402

CACHE = pathlib.Path(__file__).parent / "probe_solarbe_cache"
def cached(name):
    return (CACHE / name).read_text(encoding="utf-8", errors="ignore")

ok = 0
def check(name, cond):
    global ok
    assert cond, f"FAIL: {name}"
    ok += 1
    print(f"  ✓ {name}")

c = SolarbeCrawler()
c.incremental = False                 # 自测绝不写种子库

# ── 1. 旁路浏览器（纯 HTTP 不占标签）────────────────────────────
check("NEEDS_BROWSER=False（不占浏览器标签）", c.NEEDS_BROWSER is False)
check("BLOCK_OPEN_BROWSER=False（本站只限流、没有可点的滑块）", c.BLOCK_OPEN_BROWSER is False)
check("start_browser/ensure_tab 旁路返回 True", c.start_browser() is True and c.ensure_tab() is True)
check("close_browser 旁路不报错", c.close_browser() is None)
check("声明 2s 采集间隔安全下限（界面拉小会被钳住，免得把 IP 打进空白页）",
      SolarbeCrawler.MIN_DELAY >= 2.0 and SolarbeCrawler().delay_between >= SolarbeCrawler.MIN_DELAY)

# ── 2. 列表：qyk_sublistleft 卡片（真实缓存：逆变器行业页）──────
lst_html = cached("list_inverter.html")
entries = c.parse_list(lst_html, "逆变器")
check(f"列表解析出 {len(entries)} 家/页", len(entries) == 20)
check("公司ID=子域 slug 且唯一", len({e["公司ID"] for e in entries}) == len(entries))
check("详情URL 指向 /<slug>/contact/",
      all(re.fullmatch(r"http://company\.solarbe\.com/[a-z0-9_-]+/contact/", e["详情URL"]) for e in entries))
check("主营产品/所在区域都来自卡片字段",
      all(e["主营产品"] and e["所在区域"] for e in entries))
check("页头「共找到 N 条」解析（本片标称 575 家）", c.slice_total(lst_html) == 575)
check("导航位链接不混进卡片（公司名都含中文且不为空）",
      all(e["公司名称"] and re.search(r"[\u4e00-\u9fa5]", e["公司名称"]) for e in entries))
check("卡片名不会被按钮文字顶替（不是「企业商铺」这类占位词）",
      all(e["公司名称"] not in _NAME_PLACEHOLDER for e in entries))

# ── 2b. 公司名只有一两个人名的卡片（真实缓存：行业＝组件 页）─────
# 旧正则把链接文本限在 4 字以上，于是 h3 里的「刘鑫」被跳过，落到了同一张卡片里
# href 相同的「企业商铺」按钮上，64 家因此被存成公司名＝企业商铺。
comp_html = cached("c_com_search_php_keyword_E7_BB_84_E4_BB_B6")
comp = {e["公司ID"]: e["公司名称"] for e in c.parse_list(comp_html, "组件")}
check("两字人名公司名不再拿成「企业商铺」（fhdgkfj235＝刘鑫，实际 %s）"
      % comp.get("fhdgkfj235"), comp.get("fhdgkfj235") == "刘鑫")
check("整页 20 家一家不丢、一家不成占位名",
      len(comp) == 20 and not [v for v in comp.values() if v in _NAME_PLACEHOLDER])

# ── 3. 翻页：6 页硬上限 → 切片细分 ────────────────────────────
check("搜索 URL 带 catid/areaid/page 三维度",
      c.build_search_url("逆变器", 3, 1438, 119)
      == "https://company.solarbe.com/search.php?kw=%E9%80%86%E5%8F%98%E5%99%A8&catid=1438&areaid=119&page=3")
check("整片被截断→先按行业细分（15 个行业切片）", len(c._expand(0, 0)) == len(CATIDS) == 15)
check("行业片仍被截断→再按地区细分（31 省市 + 港澳台 + 海外）", len(c._expand(1438, 0)) == len(AREAIDS) == 35)
check("已细到行业×地区则接受截断（不再无限展开）", c._expand(1438, 119) == [])

# 翻页调度：被 120 上限截断时，“本片能拿的先拿完”再细分（回归 2026-11-02：
# 旧逻辑一在第 1 页看到标称 575 家就丢下本片去细分，而行业片/地区片层层都超上限，
# 于是一片都不入库，一个关键词跑完一家都不产出）
asked = []
c._sleep = lambda: None
def fake_fetch(url, tries=4, ua=None, guarded=True):
    asked.append(url)
    return lst_html if "catid=0&areaid=0&page=1" in url else ""
c._fetch = fake_fetch
got = list(c._iter_keyword("逆变器"))
check(f"本片 20 家先拿到手（{len(got)} 家待进详情，其余由相关性早过滤）", len(got) >= 5)
check("列表记账按卡片总数走（本片统计到 20 家）", c.stat_listed >= 20)
check("先翻完本片、再按行业细分（细分片一定排在整片第 1 页之后）",
      asked[0].endswith("catid=0&areaid=0&page=1") and any("catid=4846" in u for u in asked[1:]))
check("去重生效：同一公司在细分片里不会重复入库", len({e["公司ID"] for e in got}) == len(got))
c._fetch = SolarbeCrawler._fetch.__get__(c)
c._sleep = SolarbeCrawler._sleep.__get__(c)

# ── 4. 详情：手机版联系页明文（真实缓存 4 家）──────────────────
recs = {}
for name in ("lucky24", "whqtxny", "renjiangpv", "citicbohai"):
    html = cached(f"contact_{name}_mobile.html")
    stub = {"公司名称": "", "公司ID": name, "所在区域": "", "主营产品": ""}
    c._fetch = lambda u, tries=4, ua=None, guarded=True, _h=html: _h
    recs[name] = c.crawl_detail(stub)
    c._fetch = SolarbeCrawler._fetch.__get__(c)

r = recs["lucky24"]
check(f"手机版明文固话（{r.get('电话')}）", r.get("电话") == "021-50790323")
check(f"公司名取正文块（{r.get('公司名称')}）", r.get("公司名称") == "上海臻苏新能源科技有限公司")
check(f"联系人取「联 系 人」行（{r.get('联系人')}）", r.get("联系人") == "赵福兴")
check("纯性别称谓不立岗位列（实测括号里只写「先生」）", not r.get("岗位"))
check("绝不把页面里的 QQ 聊天按钮当客户官网（实测扫 href 兜底会拿到 wpa.qq.com）",
      all("qq.com" not in (x.get("公司网址") or "") for x in recs.values()))
check("状态=成功（有电话）", r.get("状态") == "成功")
check("meta 描述里灌的赌博号 190-38688883 绝不入库",
      "190" not in (r.get("电话", "") + r.get("手机", "")))

r = recs["whqtxny"]
check(f"邮箱走 mailto: 明文（{r.get('邮箱')}）", r.get("邮箱") == "zhongbuqingtian@chintiyan.com")
check(f"区号无连字符的固话也认出（{r.get('电话')}）", r.get("电话") == "02781937088")
check("平台自身邮箱不会当公司邮箱", "solarbe.com" not in r.get("邮箱", ""))

r = recs["renjiangpv"]
check(f"手机明文（{r.get('手机')}）", r.get("手机") == "18974945968")
check("手机不会误写进固话列", not r.get("电话"))

r = recs["citicbohai"]
check(f"手机+邮箱一次拿齐（{r.get('手机')}／{r.get('邮箱')}）",
      r.get("手机") == "13930380021" and r.get("邮箱") == "tenglipeng@163.com")
check(f"部门(职位)拼出完整岗位（{r.get('岗位')}）", r.get("岗位") == "销售部（销售经理）")
check("传真号 0335-3850621 不进电话列（实测有人把传真当号码）",
      "0335-3850621" not in (r.get("电话", "") + r.get("手机", "")))

# ── 4b. 正文公司名与占位邮箱（页面结构照真实缓存 contact_*_mobile.html）───
# 两个实测坑：① 站点允许公司名就是一两个人名（fhdgkfj235＝「刘鑫」），旧门槛「满 4 字」
# 会把它当没填；② 16 家不同公司共用同一个占位邮箱 1@qq.com，当可用联系方式入库就是脏数据。
body_page = ('<html><body><div class="content bd-t">\n公司名称：刘鑫<br/>\n'
             '公司地址：安康市高新区<br/>\n公司电话：021-50790323<br/>\n'
             '</div><a href="mailto:1@qq.com">发邮件</a>'
             '<a href="tel:021-50790323">拨打</a></body></html>')
c._fetch = lambda u, tries=4, ua=None, guarded=True, _h=body_page: _h
r = c.crawl_detail({"公司名称": "企业商铺", "公司ID": "fhdgkfj235",
                    "所在区域": "陕西安康", "主营产品": ""})
c._fetch = SolarbeCrawler._fetch.__get__(c)
check("列表把「企业商铺」当名字带回来时，以正文真名为准（实际 %s）" % r.get("公司名称"),
      r.get("公司名称") == "刘鑫")
check("两字人名不会被当作「没填名字」丢掉", bool(r.get("公司名称")))
check("占位邮箱 1@qq.com 作废（16 家共用同一个号，不是联系方式）", not r.get("邮箱"))
check("仅因占位邮箱不算数据：有正文固话仍判成功", r.get("状态") == "成功" and r.get("电话"))
normal = c._parse_contact(body_page.replace("1@qq.com", "liuxin2020@qq.com"), {})
check("正常 QQ 邮箱（多位数账号）不误杀", normal.get("邮箱") == "liuxin2020@qq.com")
c._fetch = lambda u, tries=4, ua=None, guarded=True, _h=body_page: _h
r2 = c.crawl_detail({"公司名称": "刘鑫光伏设备", "公司ID": "fhdgkfj235",
                     "所在区域": "", "主营产品": ""})
c._fetch = SolarbeCrawler._fetch.__get__(c)
check("列表已是真名时不被正文随便改写", r2.get("公司名称") == "刘鑫光伏设备")

# ── 5. 86 国际前缀归一（实测 86-0755-… 直接拨不通）─────────────
check("86- 前缀固话归回 0 区号", strip_country_code("86-0755-89810939") == "0755-89810939")
check("+86 手机号去前缀", strip_country_code("+8613930380021") == "13930380021")
check("裸区号固话补 0", strip_country_code("86-755-89810939") == "0755-89810939")

# ── 6. 异常页与终态 ───────────────────────────────────────────
stub = {"公司名称": "广东朗嘉光伏", "公司ID": "grandglow24", "所在区域": "", "主营产品": ""}
c._fetch = lambda u, tries=4, ua=None, guarded=True: ""
r = c.crawl_detail(dict(stub))
check("空正文判「详情页导航失败」（可重采）", r.get("状态") == "详情页导航失败")
check("导航失败属瞬时状态、不写种子库锁死",
      any(m in r["状态"] for m in c.TRANSIENT_MARKS))
missing = cached("contact_bomo_mobile.html")     # 真实 4212 字节「公司不存在」页
c._fetch = lambda u, tries=4, ua=None, guarded=True, _h=missing: _h
r = c.crawl_detail({**stub, "公司ID": "bomo"})
check("名录已删除的公司判「无联系方式」终态（不占下一轮量）", r.get("状态") == "无联系方式")
c._fetch = SolarbeCrawler._fetch.__get__(c)

# ── 7. 双 UA 路由：列表桌面、详情手机 ─────────────────────────
seen = []
class _Resp:
    status_code, headers = 200, {"Content-Type": "text/html"}
    def __init__(self, text): self.text = text
class _Session:
    headers = {"User-Agent": _UA}          # 会话默认 UA（桌面版），单请求 headers 才能覆盖它
    def get(self, url, timeout=None, headers=None):
        seen.append((url, (headers or {}).get("User-Agent") or self.headers["User-Agent"]))
        return _Resp("x" * 4000)
    def close(self): pass
c._session = _Session()
c._pace_before_request = lambda: None
c._new_session = lambda: c._session
_ = c._fetch("https://company.solarbe.com/search.php?kw=a", guarded=False)
_ = c._fetch("http://company.solarbe.com/a/contact/", ua=_UA_MOBILE, guarded=False)
check("列表请求用桌面 UA", seen[0][1] and "iPhone" not in seen[0][1])
check("详情请求带手机版 UA（只有手机版给明文）", "iPhone" in seen[1][1])

# 200 + 空正文：重试后仍空 → 累计空页次数，连续 3 次升格 IP 限流交看门狗
class _EmptySession(_Session):
    def get(self, url, timeout=None, headers=None):
        seen.append((url, ""))
        return _Resp("")

c2 = SolarbeCrawler(); c2.incremental = False
c2._session = _EmptySession(); c2._pace_before_request = lambda: None
c2._new_session = lambda: c2._session
c2.handle_http_block = lambda url: False
check("空正文返回空串（绝不当空数据入库）", c2._fetch("http://company.solarbe.com/x/", tries=1, guarded=False) == "")
for _ in range(3):
    c2._fetch("http://company.solarbe.com/x/", tries=1, guarded=False)
check("连续 3 页空白判 IP 限流（交看门狗等冷却）", c2._block_kind == "ip" and "空白页" in c2._block_reason)

# ── 8. 已从工具下线（防误接回），解析库本身仍归全量脚本在用 ────────
check("solarbe 不在 SITE_ORDER（已按用户指令下线）", "solarbe" not in SITE_ORDER)
check("种子库路径仍按本站 slug 隔离（全量脚本在用）",
      c._seen_store_path.name == "seen_store_solarbe.json")

print(f"\nALL PASS ({ok} checks)")
