# -*- coding: utf-8 -*-
"""马可波罗早过滤审计：把「被砍掉、没进详情」的列表卡片原样打出来，人工判断是不是误杀真同行。

用法：python debug/mp_filter_audit.py 数控机床 2
只看列表页（每页 1 个请求），不进任何详情，所以不烧配额也不会触发风控。
"""
import os
import sys

# 仓库根从 __file__ 当场推导，不写死某台机器的绝对路径（换机器就 ImportError）
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "b2bcollect_build"))
from makepolo_crawler import MakepoloCrawler        # noqa: E402

KW = sys.argv[1] if len(sys.argv) > 1 else "数控机床"
PAGES = int(sys.argv[2]) if len(sys.argv) > 2 else 2

mp = MakepoloCrawler()

kept, killed = [], []
for page in range(1, PAGES + 1):
    html = mp._fetch(mp.build_search_url(KW, page), enc="gb18030", guarded=False)
    if not html:
        print(f"第 {page} 页：没拿到内容（{mp._block_reason or mp._last_status}）")
        continue
    stubs = mp.parse_list(html, KW)
    for s in stubs:
        text = mp.stub_relevance_text(s)
        (kept if mp.is_relevant(KW, text) else killed).append(s)
    print(f"第 {page} 页解析出 {len(stubs)} 家")

print(f"\n关键词「{KW}」：放过 {len(kept)} 家 / 砍掉 {len(killed)} 家")
print("\n──── 砍掉的（逐条看是不是同行）────")
for s in killed:
    print(f"  · [{s.get('所在区域') or '?':<8s}] {s['公司名称'][:30]:<32s} 主营={s['主营产品'][:70]}")
print("\n──── 放过的（对照，看判定是否过松）────")
for s in kept:
    print(f"  · {s['公司名称'][:30]:<32s} 主营={s['主营产品'][:60]}")
