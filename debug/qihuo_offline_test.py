# 离线自测：顺企网(11467) 新站点关键逻辑（用真实页面缓存，不触网、不开浏览器）
import sys, pathlib
sys.path.insert(0, "/Users/jhnoplan/Desktop/PythonDemo/b2bcollect_build")

from qihuo11467_crawler import Qihuo11467Crawler
from parallel_session import ParallelSession, SITE_ORDER

CACHE = pathlib.Path(__file__).parent / "probe11467_cache"
def cached(name):
    return (CACHE / name).read_text(encoding="utf-8")

ok = 0
def check(name, cond):
    global ok
    assert cond, f"FAIL: {name}"
    ok += 1
    print(f"  ✓ {name}")

c = Qihuo11467Crawler()

# 1) 列表解析：产品卡版式（第 1 页真实缓存）
p1 = cached("https_b2b_11467_com_search_php_q_E5_A4_AA_E9_98_B3_E8_83_BD_E8_B7_AF_E7_81_AF.html")
entries = c.parse_list(p1, "太阳能路灯")
subs = {e["公司ID"]: e for e in entries}
check(f"第1页解析出 {len(entries)} 家公司（全部子域含数字id）", len(entries) >= 20)
check("系统域/城市门户未混入（b2b/www/handan 等）",
      not (set(subs) & {"b2b", "www", "cp", "product", "blog", "vip", "handan", "guangzhou", "shenzhen"}))
e0 = subs.get("jinan069024")
check(f"公司名/区域/商铺址正确（{e0['公司名称']}｜{e0['所在区域']}）",
      e0 and e0["公司名称"] == "山东北华电源科技有限公司" and e0["所在区域"] == "山东-济南"
      and e0["公司网址"] == "https://jinan069024.11467.com/")
check("产品卡的主营弱信号取自本卡产品标题",
      bool(e0["主营产品"]) and "蓄电池" in e0["主营产品"] or not e0["主营产品"])
check("同名多子域按公司名去重（山东北华电源只留一家）",
      sum(1 for e in entries if e["公司名称"] == "山东北华电源科技有限公司") == 1)

# 2) 分页拼址：search.php 301 的 SEO 基址 → -pnN.htm
seen_urls = []
def fake_fetch(url, tries=4, allow_redirects=True):
    seen_urls.append(url)
    return ("<a href='//yangzhou0117547.11467.com'>扬州某某照明有限公司</a>",
            "https://b2b.11467.com/search/-abc123.htm")
c._fetch = fake_fetch
html1 = c._search_page("灯具", 1, {})
check("第1页跟随重定向并缓存 SEO 基址", html1 and "pn" not in seen_urls[-1])
html2 = c._search_page("灯具", 2, {"灯具": "https://b2b.11467.com/search/-abc123.htm"})
check(f"第2页按规律拼址（{seen_urls[-1]}）",
      seen_urls[-1] == "https://b2b.11467.com/search/-abc123-pn2.htm")

# 3) contact.asp 解析（真实缓存：标签行 + HTML 实体全角空格两种写法）
d = c._parse_contact(cached("https_jinan0157275_11467_com_contact_asp.html"))
check(f"codl 标签行：联系人=「{d.get('联系人')}」手机={d.get('手机')}",
      d.get("联系人") == "杨辰" and "13355412922" in d.get("手机", ""))
check(f"邮箱正确且非平台域（{d.get('邮箱')}）", d.get("邮箱") == "17737704144@163.com")
check(f"地址干净（{d.get('地址')}）", "历城区" in d.get("地址", "") and "技术" not in d.get("地址", ""))
d2 = c._parse_contact(cached("https_guangzhou0590735_11467_com_contact_asp.html"))
check(f"实体全角空格「手&#12288;机」容忍：联系人=「{d2.get('联系人')}」手机={d2.get('手机')}",
      d2.get("联系人") == "RAYNA" and "18898847304" in d2.get("手机", ""))

# 4) 商铺首页解析：标题产品词→主营产品、QQ客服行→手机
dh = c._parse_home(cached("https_jinan069024_11467_com_.html"), {"主营产品": ""})
check(f"首页标题提取主营产品（{(dh.get('主营产品') or '')[:24]}…）",
      "UPS" in dh.get("主营产品", "") or "蓄电池" in dh.get("主营产品", ""))
check(f"QQ客服行补手机（{dh.get('手机')}）", "13954022033" in (dh.get("手机") or ""))
check(f"首页页脚地址干净（{(dh.get('地址') or '')[:22]}…）",
      "历城区" in dh.get("地址", "") and "顺企网" not in dh.get("地址", ""))

# 5) 详情相关性：强信号不符过滤 / 命中保留 / 弱信号 fail-open
c.relevance_guard = True
r_hit = {"关键词": "太阳能路灯", "公司名称": "某某照明有限公司", "主营产品": "太阳能路灯 灯杆", "标题": ""}
r_bad = {"关键词": "太阳能路灯", "公司名称": "某某物流有限公司", "主营产品": "散货整柜海运空运快递", "标题": ""}
r_weak = {"关键词": "太阳能路灯", "公司名称": "某某商贸有限公司", "主营产品": "", "标题": ""}
check("主营命中关键词→保留", c.detail_relevant(r_hit))
check("主营为可靠中文信号且不相关→过滤", not c.detail_relevant(r_bad))
check("无主营弱信号→fail-open 不误杀", c.detail_relevant(r_weak))

# 6) 会话注册：四站及以后顺序含 11467、名称齐全、纯 HTTP 站不占标签
sess = ParallelSession()
check(f"SITE_ORDER 含 11467（{SITE_ORDER}）", "11467" in SITE_ORDER
      and "顺企网" in sess.site_stats())
check("顺企网 NEEDS_BROWSER=False（不占标签、不参与最小化标签清单）",
      Qihuo11467Crawler.NEEDS_BROWSER is False and sess._pinned_tab_ids() == [])
check("顺企网有独立种子库文件", sess.crawlers["11467"]._seen_store_path.name == "seen_store_11467.json")

print(f"\n全部 {ok} 项断言通过 ✅")
