# 离线自测：爱采购(b2b.aipage.com) 新站点关键逻辑（用真实页面缓存，不触网、不开浏览器）
import sys, pathlib, json, re
sys.path.insert(0, "/Users/jhnoplan/Desktop/PythonDemo/b2bcollect_build")

from aipage_crawler import AipageCrawler
from parallel_session import ParallelSession, SITE_ORDER, SITE_CLASSES, SITE_NAMES
from cdp_base import PHONE_MOBILE_RE

CACHE = pathlib.Path(__file__).parent / "probe_aipage_cache"
def cached(name):
    return (CACHE / name).read_text(encoding="utf-8")

ok = 0
def check(name, cond):
    global ok
    assert cond, f"FAIL: {name}"
    ok += 1
    print(f"  ✓ {name}")

c = AipageCrawler()

# 1) 首屏 window.data 注水解析（真实缓存：搜索第1页 HTML）
p1_html = cached("search_p1.html")
m = re.search(r'"csrf_token"\s*:\s*"([0-9a-f]{32})"', p1_html)
check("首屏 HTML 能提取 csrf_token", bool(m))
data1 = c._window_data(p1_html)
check("首屏 HTML 能解析 window.data", isinstance(data1, dict) and "productList" in data1)

entries = c.parse_list(data1, "太阳能路灯")
ids = {e["公司ID"] for e in entries}
check(f"第1页解析出 {len(entries)} 家公司（按 xzhid 去重）", len(entries) >= 20)
check("公司ID(memberId)=xzhid 且为纯数字", all(re.fullmatch(r"\d+", i) for i in ids))
check("公司名含中文且长度>=4", all(len(e["公司名称"]) >= 4 for e in entries))
check("详情URL 指向进店联系方式页 tpath=contact",
      all("tpath=contact" in e["详情URL"] for e in entries))
check("公司网址为 b2b.baidu.com/shop/<xzhid>",
      all(e["公司网址"].startswith("https://b2b.baidu.com/shop/") for e in entries))
check("主营产品来自 prodTag 拆分", any(e["主营产品"] for e in entries))
# 同名/同店去重：同一 xzhid 只出一条
check("同 xzhid 不重复", len(ids) == len(entries))
# advert 广告卡应被剔除
adv = [p for p in data1.get("productList", []) if p.get("advert")]
nonadv_ids = {str(p.get("xzhid")) for p in data1.get("productList", [])
              if not p.get("advert") and p.get("xzhid") and (p.get("fullProviderName") or (p.get("tpProvider") or {}).get("name"))}
check("广告卡(advert=1)不进入解析结果",
      all(e["公司ID"] in nonadv_ids for e in entries))

# 2) /s/a 接口 JSON 解析（真实缓存：第2页）
api2 = json.loads(cached("api_p2.json"))
check("/s/a 返回 status=0 且含 data.productList",
      api2.get("status") == 0 and isinstance(api2.get("data", {}).get("productList"), list))
e2 = c.parse_list(api2["data"], "太阳能路灯")
check(f"第2页接口解析出 {len(e2)} 家公司", len(e2) >= 15)
check("第2页与第1页公司集合不同（接口翻页生效）",
      bool({e["公司ID"] for e in e2} - ids))

# 3) 进店联系方式页：cardInfo 明文解析（真实缓存）
shop_html = cached("shop_contact.html")
sdata = c._window_data(shop_html)
check("进店页能解析 window.data", isinstance(sdata, dict) and "cardInfo" in sdata)
stub = {"公司名称": "", "公司ID": str(sdata.get("xzhId") or ""), "所在区域": "", "主营产品": ""}
rec = c._parse_shop(sdata, stub)
check(f"联系人解析（{rec.get('联系人')}）", bool(rec.get("联系人")))
check(f"手机为合法 11 位（{rec.get('手机')}）",
      bool(PHONE_MOBILE_RE.search(rec.get("手机", ""))))
check(f"邮箱解析（{rec.get('邮箱')}）", "@" in rec.get("邮箱", ""))
check(f"地址解析（{rec.get('地址')[:20]}）", len(rec.get("地址", "")) >= 6)
check(f"公司名解析（{rec.get('公司名称')}）", "公司" in rec.get("公司名称", "") or "厂" in rec.get("公司名称", ""))
check("主营产品补出（entInfo.mainProduct）", bool(rec.get("主营产品")))
check("公司网址=b2b.baidu.com/shop/<xzhid>", rec.get("公司网址", "").startswith("https://b2b.baidu.com/shop/"))

# 4) _parse_shop 固话/手机分列：phoneNumber 若是 0xx 区号形态归入电话
fake = {"cardInfo": {"contactName": "王经理", "phoneNumber": "0755-88886666",
                     "telNumber": "", "wechatNumber": "13800001111", "email": "a@b.com",
                     "addressV2": {"full": "广东省深圳市XX路1号"}},
        "entInfo": {"fullname": "深圳测试科技有限公司", "mainProduct": "LED灯;路灯"},
        "entName": "深圳测试科技有限公司", "xzhId": "123"}
r2 = c._parse_shop(fake, {"公司ID": "123"})
check("固话形态 phoneNumber 归入电话列", "0755-88886666" in r2.get("电话", ""))
check("手机形态 wechatNumber 归入手机列", r2.get("手机") == "13800001111")

# 5) 平台自有邮箱域剔除
junk = {"cardInfo": {"email": "support@baidu.com", "phoneNumber": "13800001111"},
        "entInfo": {"fullname": "某公司某公司"}, "xzhId": "9"}
check("baidu.com 等平台邮箱被剔除", not c._parse_shop(junk, {"公司ID": "9"}).get("邮箱"))

# 6) 旁路浏览器：NEEDS_BROWSER=False，start_browser 直接返回 True
check("NEEDS_BROWSER=False（纯HTTP不占标签）", c.NEEDS_BROWSER is False)
check("start_browser 旁路返回 True", c.start_browser() is True)
check("ensure_tab 旁路返回 True", c.ensure_tab() is True)

# 7) 注册到五站并行编排
sess = ParallelSession()
check("SITE_ORDER 含 aipage（末位留给后追加的 1688，不能再断言[-1]）",
      "aipage" in SITE_ORDER)
check("SITE_CLASSES/NAMES 均注册 aipage",
      SITE_CLASSES["aipage"] is AipageCrawler and SITE_NAMES["aipage"] == "爱采购")
check("ParallelSession 实例化出 aipage 采集器", "aipage" in sess.crawlers)
st = sess.site_stats()
check("site_stats 覆盖全部在采站点（gys 已下线、1688 后加，不再硬写五站）",
      set(st) == {SITE_NAMES[s] for s in SITE_ORDER})

# 8) 种子库文件名按 slug 隔离
check("种子库路径含 seen_store_aipage", c._seen_store_path.name == "seen_store_aipage.json")

print(f"\nALL PASS ({ok} checks)")
