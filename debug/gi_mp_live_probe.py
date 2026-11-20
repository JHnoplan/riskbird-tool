# -*- coding: utf-8 -*-
"""马可波罗 / 跨国采购网 小流量实机探针（低请求量，只验通路是否真能出数据）：
  ① makepolo：1 词 1 页 → 前 N 家进详情，看四条手机通路各自出多少、码图 OCR 实际识别率；
  ② globalimporter：1 词 2 页 → 详情零请求本地组装，看明文号码与脏号还原在真实数据里的比例。
不写种子库、不导 Excel（incremental=False，也不调 save_seen_store），免得污染工具的真实断点。
"""
import sys
import time
from pathlib import Path

# 仓库根从 __file__ 当场推导：写死某台机器的绝对路径，换一台机器就 ImportError。
# 本文件又是 debug/gi_mp_offline_check.py 「缺夹具时跑本脚本取回」那条指引的落点，
# 指一个在新机器上跑不起来的路径等于没有指引。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "b2bcollect_build"))
from gi_crawler import GiCrawler                    # noqa: E402
from makepolo_crawler import MakepoloCrawler        # noqa: E402

KW = sys.argv[1] if len(sys.argv) > 1 else "制冷设备"
N_MP = int(sys.argv[2]) if len(sys.argv) > 2 else 10


def stat(recs, title, extra=""):
    n = len(recs)
    mob = sum(1 for r in recs if r.get("手机"))
    tel = sum(1 for r in recs if r.get("电话"))
    mail = sum(1 for r in recs if r.get("邮箱"))
    person = sum(1 for r in recs if r.get("联系人"))
    addr = sum(1 for r in recs if r.get("地址"))
    anyc = sum(1 for r in recs if r.get("手机") or r.get("电话") or r.get("邮箱"))
    print(f"\n── {title}：{n} 家")
    print(f"   手机 {mob}/{n}　固话 {tel}/{n}　任一联系方式 {anyc}/{n}"
          f"　邮箱 {mail}/{n}　联系人 {person}/{n}　地址 {addr}/{n}{extra}")
    for r in recs:
        print(f"   · {r['公司名称'][:26]:26s} 手机={r.get('手机','-') or '-':<28s}"
              f" 电话={r.get('电话') or '-':<16s} 邮箱={r.get('邮箱') or '-':<24s}"
              f" 状态={r.get('状态')}")


# ══════════ ① 马可波罗 ══════════
print(f"\n===== 马可波罗 实机（关键词「{KW}」1 页 + {N_MP} 家详情）=====")
mp = MakepoloCrawler()
mp.incremental = False
mp.max_pages = 1
mp.BLOCK_MAX_WAIT_IP = 30          # 探针不真等 40 分钟，验证分诊链路走通即可
t0 = time.time()
stubs = []
for e in mp._iter_keyword(KW):
    stubs.append(e)
    if len(stubs) >= N_MP:
        break
print(f"列表取到 {len(stubs)} 家公司（用时 {time.time() - t0:.0f}s）")
recs = []
for s in stubs:
    recs.append(mp.crawl_detail(s))
stat(recs, "马可波罗 详情",
     f"　通路账目={mp.src_count}  图片通路停用={mp._img_off}")
print(f"   详情用时 {time.time() - t0:.0f}s，请求总数见日志")

# ══════════ ② 跨国采购网 ══════════
print(f"\n===== 跨国采购网 实机（关键词「{KW}」2 页，详情零请求）=====")
gi = GiCrawler()
gi.incremental = False
gi.max_pages = 2
gi.BLOCK_MAX_WAIT_IP = 40         # 站方真冷却是 2 小时，探针只看「是否归对了处置」
gi.BLOCK_POLL_IP = 15
t1 = time.time()
gest = []
for e in gi._iter_keyword(KW):
    gest.append(e)
grecs = [gi.crawl_detail(s) for s in gest]
stat(grecs, "跨国采购网 详情（本地组装，未发一个详情请求）",
     f"  用时 {time.time() - t1:.0f}s")
print(f"   请求页数={gi.stat_pages} 列表条目={gi.stat_listed} 早过滤={gi._early_skipped}")
