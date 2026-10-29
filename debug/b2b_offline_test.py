# 离线自测：本轮 6 项改进的关键逻辑（不触网、不开浏览器）
import sys, time, re
sys.path.insert(0, "/Users/jhnoplan/Desktop/PythonDemo/b2bcollect_build")

from cdp_base import CdpSiteCrawler, clean_address
from b2b168_crawler import B2b168Crawler
from gys_crawler import GysCrawler
from mic_crawler import MicCrawler

ok = 0
def check(name, cond):
    global ok
    assert cond, f"FAIL: {name}"
    ok += 1
    print(f"  ✓ {name}")

# 1) _sleep 统一记账：连续两次 sleep 只付一轮间隔
class Pacer(CdpSiteCrawler):
    PLATFORM_NAME, PLATFORM_SLUG = "t", "t"
p = Pacer()
p.delay_between = 1.0; p.jitter = 0.0; p._current_delay = 1.0
t0 = time.time(); p._mark_activity(); p._sleep(); p._sleep()
dt = time.time() - t0
check(f"连续两次 _sleep 合并为一段间隔（实测 {dt:.2f}s ≈1.0s）", 0.9 < dt < 1.6)
p._mark_activity()          # 模拟一次请求完成
t0 = time.time(); p._sleep()
dt2 = time.time() - t0
check(f"请求后立即 sleep 才计满间隔（{dt2:.2f}s）", dt2 > 0.9)

# 2) 详情复核弱信号 fail-open
r = GysCrawler()
r.relevance_guard = True
rec_weak   = {"关键词": "光伏储能电池", "公司名称": "深圳市新能源科技有限公司", "主营产品": "", "标题": ""}
rec_strong = {"关键词": "光伏储能电池", "公司名称": "某某科技有限公司", "主营产品": "展览展台设计", "标题": ""}
rec_hit    = {"关键词": "光伏储能电池", "公司名称": "某某科技有限公司", "主营产品": "储能电池组装线", "标题": ""}
check("弱信号(无主营)→保留不误杀", r.detail_relevant(rec_weak))
check("强信号不符→仍过滤", not r.detail_relevant(rec_strong))
check("信号命中→保留", r.detail_relevant(rec_hit))

# 3) 地址尾部噪声剥离
addr = clean_address("深圳市宝安区西乡街道 姓名：王先生 技术支持：八方资源网")
check(f"地址噪声剥离（{addr}）", "西乡街道" in addr and "姓名" not in addr and "技术支持" not in addr)

# 4) b2b168：「姓名：」联系人 + 全国统一热线 + 主机互换
b = B2b168Crawler()
html = """<html><head><title>太阳能电池,逆变器,锂电池_长沙某某电子有限公司 - 八方资源网</title></head>
<body>主营产品：太阳能电池 逆变器<br>姓名： 王 公司地址：长沙高新开发区某路88号 联系人
手机：13974982872<br>全国统一热线：13974982872 sales@changsha-example.com.cn 更多</body></html>"""
d = b._parse_shop(html, "长沙某某电子有限公司", "https://cslianhuizd.b2b168.com/")
check(f"b2b168 联系人=「{d.get('联系人')}」", d.get("联系人") == "王")
check(f"b2b168 手机=「{d.get('手机')}」", "13974982872" in (d.get("手机") or ""))
check(f"b2b168 地址=「{d.get('地址')}」", "联系人" not in (d.get("地址") or "") and "长沙" in d.get("地址", ""))
check(f"b2b168 邮箱=「{d.get('邮箱')}」", (d.get("邮箱") or "").startswith("sales@"))
u1, h1 = b._alt_host_url("wxlianghuizd.cn", "https://wxlianghuizd.cn.b2b168.com/")
u2, h2 = b._alt_host_url("abcd1234", "https://abcd1234.b2b168.com/")
check("主机 .cn 互换双向", h1 == "wxlianghuizd" and h2 == "abcd1234.cn")
d2 = b._parse_shop(html, "x", "https://a.b2b168.com/", from_about=True)
check("from_about 不崩且字段一致", d2.get("联系人") == "王")

# 5) gys：「联 系 人」空格写法解析
g = GysCrawler()
ghtml = """<html><head><title>佛山市南区某某陶瓷有限公司 - 供应商网</title></head><body>
公司名称：佛山市南区某某陶瓷有限公司<br>联 系 人：廖生 先生<br>手 机：188 2658 6251<br>
地 址：佛山市南海区陶瓷城A区12号 姓名：廖生<br>主营产品：瓷砖,抛釉砖</body></html>"""
gd = g._parse_company_page(ghtml, "fsceramic")
check(f"gys 联系人=「{gd.get('联系人')}」", gd.get("联系人") in ("廖生", "廖生先生"))
check(f"gys 手机=「{gd.get('手机')}」", gd.get("手机") == "18826586251")
check(f"gys 地址=「{gd.get('地址')}」", "姓名" not in (gd.get("地址") or ""))

# 6) mic：裸 HTTP 拿到的原始 HTML 也能被 _parse_contact 解析出明文
m = MicCrawler()
mhtml = """<html><head><meta name="Description" content="中国制造网，深圳市希立仪器设备有限公司，联系人：廖生，联系电话：18826586251">
</head><body>公司地址：深圳市龙岗区某路1号 主营产品：密封圈</body></html>"""
rec = m._base_rec({"公司名称": "sealsins99", "关键词": "密封圈检测", "详情URL": "", "memberId": "sealsins99"})
m._parse_contact(mhtml, rec)
check(f"mic meta 明文解析 联系人=「{rec.get('联系人')}」手机=「{rec.get('手机')}」",
      rec.get("联系人") == "廖生" and rec.get("手机") == "18826586251"
      and "希立" in rec.get("公司名称", ""))

# 7) ParallelSession 关键词进度聚合 + site_stats + 信号存在性
from parallel_session import ParallelSession
sess = ParallelSession()
sent = []
sess.signals.kw_progress_signal.connect(lambda s, d, t: sent.append((d, t)))
c = sess.crawlers["11467"]; c.stat_ok = 3
sess._on_site_kw_progress("11467", 0, 4)
sess._on_site_kw_progress("mic", 1, 4)
sess._on_site_kw_progress("b2b168", 2, 4)
check(f"聚合进度 {sent[-1]}", sent[-1] == (3, 12))
st = sess.site_stats()
check("site_stats 在采站点齐全（gys 已下线，改成断言现存站）",
      {"中国制造网", "八方资源网", "顺企网", "爱采购"} <= set(st) and st["顺企网"]["ok"] == 3)

print(f"\nALL PASS ({ok} checks)")
