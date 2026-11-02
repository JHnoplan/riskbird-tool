# 端到端复验（真实联网）：麦麦B2B 过盾 → 抄通行证 → 列表 1 页 + 详情 3 家
# 目的：验证「盾过了但通行证抄不回」不再整轮空等（旧实现死锁 300 秒 0 家）。
import sys, json, time
sys.path.insert(0, "/Users/jhnoplan/Desktop/PythonDemo/b2bcollect_build")
from maimaib2b_crawler import Maimaib2bCrawler

c = Maimaib2bCrawler()
c.incremental = False           # 复验绝不写种子库
t0 = time.time()
kw = "空调"
html = c._fetch(c.build_search_url(kw, 1))
entries = c.parse_list(html, kw) if html else []
print(f"列表：{len(entries)} 家（耗时 {time.time() - t0:.1f}s，降级={c._browser_fallback}）")
assert entries, "列表没拿到公司条目，复验失败"

rows = []
for e in entries[:3]:
    rec = c.crawl_detail(e)
    rows.append({k: rec.get(k, "") for k in
                 ("公司名称", "联系人", "岗位", "手机", "电话", "邮箱", "地址", "公司网址", "状态")})
    print(json.dumps(rows[-1], ensure_ascii=False))

good = sum(1 for r in rows if r["手机"] or r["电话"] or r["邮箱"])
print(f"\n详情：{len(rows)} 家，有联系方式 {good} 家，总耗时 {time.time() - t0:.1f}s，"
      f"降级模式={c._browser_fallback}")
