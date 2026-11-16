#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐平台实机采集体检（不做离线替身，真的联网、真的开爬）。

为什么要有这个入口：离线套件判的是「代码按我的理解在动」，只有实机能回答
「这个平台今天还能不能采到数据」。上一轮用户报「1688 一直 0 家」「企知道爱企查都有问题」
时，能给出结论的只能是这种探针——离线用例当时全绿，而线上真的采不到。

每一站的判据不是「跑完了没报错」，而是七条能证伪的硬账：
  1) 有产出：入库条数 > 0；
  2) 关键词真的生效：两个语义无关关键词的公司名集合必须显著不同（假命中陷阱，
     goepe/b2b818/qympw 三站就是靠这条判据事后定性、当场下线的）；差分跑不成（某一边
     0 家）时必须显式说「没被证明」并**点名那个零产出的词**，不许算成通过、也不许
     照着写死的「第二个关键词」去换词（2026-11-15 实机就把好词当坏词点名了）；
  3) 出的货能用：按**生产入库口径**（手机号/固话/邮箱/微信号四项至少一项，走 make_row +
     filter_import_rows，与真导出完全同一把尺子）复算可入库行数。旧版这里用的是
     「电话/手机/邮箱/公司网址任一」，把「只有官网」也算命中——马可波罗那轮报 ✅ 的
     51 家，漏斗按真口径只留 2 家，两把尺子就是这条假绿的全部来源；
  4) 来源戳齐全：每条都带「数据来源」，缺了会在跨站去重时被整批静默丢弃；
  5) 产出与同轮参照相当：同词同页数下可入库数显著低于其它站的中位数就是异常，报告要带着
     证据（深链被拒/取页失败/详情失败/四项全空/通路账）说清少在哪——「4 家」也是 ❌，
     旧版只看 >0 时它被打过 ✅；
  6) 归因分档：0 家要分清「链路未通／增量库全跳过／链路回了货但没落地／平台确实没这
     些公司」，四类的处方不同，混成一类会让人去改本来没毛病的代码；
  7) 砍单归因：某一词零净新增时，必须分得开「平台真没货」与「站方回了货、被本机
     相关性闸门判成类目不符」（kw_filtered/kw_early 两本账）——后者的处方是改判定
     口径，写成「差分没跑成、建议换词复跑」会把人支使去查本来没毛病的链路
     （2026-11-15 实机：世界工厂网「橡胶手套」7 条全被砍）；而「砍了几家」还必须配上
     「站方一共回了几条」（kw_items/kw_claimed 两本账）：砍掉 1 家可能是这一词只回 1 条
     （换词换索引），也可能是 30 条里的 1 条（改相关性口径），两种念法写成同一句时
     前一种的读者会去改一个改了也没用的东西（2026-11-16 实机：中国制造网「窗帘导轨」
     站方自报「共找到 1 个」，报告却写「该复核的是相关性口径」）。
最后再走一遍生产漏斗 clean_records，并按档打印（喂进 → 严格档剔 → 剔无效 → 跨平台合并 →
四项全空剔（分来源点名）→ 入库）：某站被整批吞掉要能当场说出吞在哪一档、属于哪一路。
通路账（stats()["legs"]）单独成行——站方改版会掐死某一条取号通路，而只累加、从不外读的
计数会让它在账上像从来没存在过（2026-11-16 实机：马可波罗产品 id 挪位，最硬的一条腿
整轮 0 次请求，报告照样是一片绿）。

用法：
    .venv/bin/python debug/platform_live_check.py                      # 全部关键词站
    .venv/bin/python debug/platform_live_check.py --sites b2b168,11467 # 只查指定站
    .venv/bin/python debug/platform_live_check.py --kws 泵,生物质锅炉 --pages 1
    .venv/bin/python debug/platform_live_check.py --list riskbird,qzd,aiqicha --names 3
所有落盘都进 debug/_live_sandbox/，绝不写用户桌面的工具目录与任务索引。
"""
import argparse
import os
import re
import sys
import threading
import time
import traceback
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(REPO / "b2bcollect_build"))

import parallel_session as ps_mod                      # noqa: E402
from parallel_session import ParallelSession, SITE_ORDER, SITE_NAMES, STATS_LABELS  # noqa: E402
# 失败原因的**收口**只准用一个把手：探针自己写一份 [:70] 就会与采集器那份长得不一样，
# 同一件故障在日志里是一个词被截断、在报告里是另一个词被截断（判据与由来见 cdp_base.short_reason）
from cdp_base import short_reason, CdpSiteCrawler               # noqa: E402
# 分档的说词只许取采集器那一个把手（nc_kinds_text）：探针自己写一份就会出现同一笔账两种处方
from cdp_base import nc_kinds_text              # noqa: E402

# 「任一联系方式」的**宽口径**：只出现在报表那一行对比数字里，不参与判定。
# 判定一律用生产入库那把尺子（见 _importable：make_row + filter_import_rows，
# 手机号/固话/邮箱/微信号四项）。由来（2026-11-15 实机马可波罗 51 家）：宽口径把
# 「只有官网」也算命中，于是分站报告打 ✅、汇总漏斗按四项口径吞掉 49 条——
# 同一个工具里两把「这条数据能不能用」的尺子，弱的那把永远会把坏消息洗成好消息。
CONTACT_KEYS = ("电话", "手机", "邮箱", "公司网址")
# 拦截/异常类日志的关键字：体检报告里要把这些行原样带出来，归因靠它们
# 「深链被拒」必须在列：这一族的原因里可能只有「防采插页/访问过于频繁」而没有 403，
# 漏掉就等于把本轮唯一能定性产出为什么低的证据筛没了（2026-11-15 马可波罗）
BLOCK_WORDS = re.compile(r"(拦截|深链被拒|验证码|滑块|限流|冷却|额度|未登录|登录|失败|异常|超时|403|429|502|blocked)")


def _diff_judge(by_kw, kws):
    """两个语义无关关键词的结果集差分。返回 (状态, 说明行)。

    状态 ∈ {"ok", "suspect", "unknown"}。这一格是**数据质量唯一能证伪「服务端真按
    关键词过滤」的判据**：goepe/b2b818/qympw 三站当年每词都能出 10~57 家、纯看产出
    全是绿的，靠的就是这条差分才定性下线。

    "unknown"（第二词一家都没有）必须与 "ok" 分开：差分**没跑成**不等于差分通过。
    上一版把这两种混为一谈，于是「第二个关键词 0 家」的站照样打着 ✅，结论行里那句
    关于差分的「OK」当时就是假的。
    """
    a, b = (kws + ["", ""])[:2]
    sa, sb = by_kw.get(a, set()), by_kw.get(b, set())
    if sa and sb:
        inter = sa & sb
        ratio = len(inter) / max(1, min(len(sa), len(sb)))
        if ratio < 0.5:
            return "ok", (f"  差分: 「{a}」{len(sa)} 家 vs 「{b}」{len(sb)} 家｜交集 {len(inter)} 家"
                          f"（重合 {ratio:.0%}）→ OK（服务端真按关键词过滤）")
        return "suspect", (f"  差分: 「{a}」{len(sa)} 家 vs 「{b}」{len(sb)} 家｜交集 {len(inter)} 家"
                           f"（重合 {ratio:.0%}）→ 疑似假命中（两词结果高度重合）")
    return "unknown", (f"  差分: 无法判定（词 A「{a}」{len(sa)} 家、词 B「{b}」{len(sb)} 家，"
                        "至少一边为空 → 关键词是否生效这一轮**没被证明**）")


def _peer_baseline(counts):
    """同轮参照产出：其它站本轮入库数的中位数（只看真的有产出的站）。

    为什么要中位数而不是最大值：某一站恰好爆量会把基线抬到谁都过不了；为什么必须
    带这一格：产出「>0」从来不是质量标准——4 家与 62 家在旧判据里都是 ✅，而马可波罗
    那一轮正是靠这个把「整条腿不通」放过去的。样本不足两站时返回 0（不装懂）。
    """
    xs = sorted(c for c in counts if c > 0)
    if len(xs) < 2:
        return 0
    mid = len(xs) // 2
    return xs[mid] if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2.0


def _peer_for(reports, slug):
    """这一路的同轮参照产出（剔掉不该当参照的那几路）。

    剔除名单与理由：
      · 自己——不然产出异常低的那一路会亲手把中位数拉下来，判据就永远响不了；
      · 拦停的/等人的——它们的 0 家不是「这个平台今天货少」，拿来当基线会把满轮正常
        的站全判成异常（上一轮的教训正是把等登录算成采不到）；
      · 读数失败的——那是探针自己的故障，不是站点的产出（见 main 里的 read_err）。
    参照基数用**可入库数**（下标 11）而不是原始家数：旧版用原始家数，于是「抓到 51 家、
    能入库 2 家」那一路既拿 51 把自己算进基线，又因原始数够高躲过产出异常判定——
    同一个错数字替坏消息打了两次掩护。这一路复算不了（None）时才退回原始家数。
    reports 元组下标：0=slug、2=入库数、5=blocked、7=stats、9=读数失败原因、11=可入库数。
    """
    return _peer_baseline([(r[11] if r[11] is not None else r[2]) for r in reports
                           if r[0] != slug and not r[9] and not r[5]
                           and not str((r[7] or {}).get("waiting") or "")])


def _kw_supply(st, kw):
    """这一词站方回了多少条：(我们解析到的, 站方自报的)，没记账的那一本交回 None。

    为什么不能只拿解析数当货量：解析数只说明我们抓到了几条。站方自报的比它多时，
    约束项在**我们的列表解析**（该修卡片选择器）；两者相当且都是个位数时，约束项在
    **货源**（该换词或换索引）。只有砍单数时这两种局在报告里长得一模一样。
    """
    it = (st.get("kw_items") or {}).get(kw)
    cl = (st.get("kw_claimed") or {}).get(kw)
    return (int(it) if it is not None else None, int(cl) if cl is not None else None)


def _kw_thin(st, kw):
    """这一词的约束项是不是「货源薄」（站方回得只有一两条）：账上没条目数时交回 False。

    说不出就不算薄：宁可退回旧那句「该复核判定口径」，也不许拿一个没记账的数去把一个
    真该修解析的站判成「平台没货」（那一形是反向的谎：把我们的锅说成站方的锅）。
    """
    it, cl = _kw_supply(st, kw)
    if it is None and cl is None:
        return False
    got = it if it is not None else cl
    if cl is not None and it is not None and cl > it:
        return False        # 站方有货而我们没抓到：这是解析漏，不是货源薄
    return got <= CdpSiteCrawler.KW_THIN_SUPPLY_ITEMS


def _kw_starve(st, kw):
    """这一词零净新增时，问一句「货是不是被本机自己的相关性闸门砍掉的」。

    返回说明句，空串表示账上没有砍单记录。这一格是 2026-11-15 实机补上的第五类归因：
    世界工厂网「橡胶手套」站方回了 7 条、7 条全被判「类目不符」，旧报告把它写成
    「差分没跑成，建议换词复跑」——处方完全指错方向（该动的是相关性口径/同族类目桥）。
    """
    det = int((st.get("kw_filtered") or {}).get(kw, 0) or 0)
    ear = int((st.get("kw_early") or {}).get(kw, 0) or 0)
    seg = []
    if det:
        seg.append(f"详情页复核砍 {det} 家" + _cut_names(st.get("kw_filtered_names"), kw, "复核"))
    if ear:
        seg.append(f"列表页早过滤砍 {ear} 家" + _cut_names(st.get("kw_early_names"), kw, "早过滤"))
    if not seg:
        # 没砍单就一个字都不说：调用方（starved 那一路）拿这一句当「被自己砍过」的唯一凭据，
        # 货量那一笔虽然也想露出来，但把它接在空句后面等于把「没人砍」的词递成「砍过」的证据。
        return ""
    # 货量跟着砍单数一起报：只说砍了几家时，读的人分不清「只回 1 条砍 1 家」与「回 30 条砍 30 家」
    it, cl = _kw_supply(st, kw)
    if it is not None or cl is not None:
        both = (f"自报 {cl} 条、解析到 {it} 条" if cl is not None and it is not None
                else f"自报 {cl} 条" if cl is not None else f"解析到 {it} 条")
        seg.append(f"站方回货：{both}")
    else:
        seg.append("站方回货：条目账没记上（说不出这一词站方回了几条）")
    return "、".join(seg)


def _cut_names(bucket, kw, stage):
    """砍单名单样本：报告写「该复核判定口径」时，复核的人手上必须有名字可查。
    只给计数的处方是执行不了的（2026-11-16 第十轮实机：三个 ❌ 全站都只说「该复核」，
    而谁都没去看砍掉的究竟是谁）。没有名单就只留计数，不许编。"""
    got = (bucket or {}).get(kw) or []
    return f"（{stage}砍掉：{'、'.join(got)}）" if got else ""


def _nc_line(st):
    """「无联系方式 N 家」按**谁的责任**分档渲染成一行。

    措词本体只在 `cdp_base.nc_kinds_text` 那一处（日志/GUI/体检报告共用一句），
    探针这一层只负责取账；由来见 cdp_base._note_no_contact（2026-11-16 第十轮实机
    世界工厂网：旧句「先修取号通路」把人的动作支使到本来正确的解析代码上）。
    """
    return nc_kinds_text(st.get("nc_kinds") or {}, st.get("nc_evidence") or {})


def _nc_advice(st):
    """分档账 → 一句能执行的处方。只许 miss 那一档说「修代码」，其余几档要说清不是我们的锅。

    旧句「先修取号通路再谈换词」在任何一档上都不对：站方藏着要的是换通路，站方没登要的是
    换词换站，我们自己漏解析要的才是修代码。分档拿不到时就只报「不知道」并说出为什么
    不知道，绝不回到那句人人都熟的假话。
    （说词本体在 nc_kinds_text；这一函数只给**处方**，所以两边不重叠。）
    """
    k = st.get("nc_kinds") or {}
    ev = st.get("nc_evidence") or {}
    if k.get("miss"):
        return (f"——四项全空里有 {k['miss']} 家**栏位里本有号码**：先修我们的解析"
                f"（证据：{short_reason(ev.get('miss', ''), 46)}）")
    if k.get("unknown"):
        return (f"——分档拿不到证据（{k['unknown']} 家未留详情页正文）：先给这站的 "
                "crawl_detail 接上 _keep_detail，再定性是站方没给还是我们没取到")
    if k.get("no_page"):
        # 这一档与上一档长相很像、处方完全相反：本站已声明「详情不取页」（列表明文组装/
        # 详情吃 IP 配额），把手没接不是漏接。把这里也写成「先接 _keep_detail」就是
        # 给一个已经声明过的站开了一张执行不了的处方（2026-11-16 第十轮实机跨国采购网）。
        return (f"——{k['no_page']} 家本站详情不取页（{short_reason(ev.get('no_page', ''), 46)}）："
                "判不了是谁的责任，这不是漏接把手；要判得先加一条详情腿（代价见那站注释），"
                "或按列表页口径接受——卡里没号码就是站方没登")
    if k.get("hidden"):
        return (f"——大头是**站方藏着**（{k['hidden']} 家要点查看/登录/联系客服才给）："
                "取号通路没坏，该换通路或换站；照着这句改解析只会引入假数据")
    if k.get("blank"):
        return (f"——大头是**站方自己没登**（{k['blank']} 家那一栏写 -／暂无）："
                "这不是通路缺陷，要新数据得换词或换站")
    if k.get("no_slot"):
        return (f"——{k['no_slot']} 家取回的那一页根本没有联系方式栏："
                "先查取的是不是这一家的页（差分与落点不符就在上面两行）")
    return ("——四项全空的大头不在「无联系方式」这一档："
            "看上面详情失败/取页失败/通路账哪一格非零，再决定改什么")


def _deep_group(deep):
    """深链账按**主域**聚合后用于渲染：主键取自 cdp_base._deep_log_key（唯一真源，
    不在这里另算一份，否则两处的聚合口径会各自漂移）。"""
    from cdp_base import CdpSiteCrawler
    out = {}
    for host, val in (deep or {}).items():
        key = CdpSiteCrawler._deep_log_key(str(host))
        n = int((val or [0, ""])[0])
        rec = out.setdefault(key, [0, str((val or [0, ""])[1])])
        rec[0] += n
    return dict(sorted(out.items()))


def _host_reason_line(title, mapping, limit=46):
    """`{标题}: {主机} {次数} 次（{原因}）` 这一行只在这里写一次（落点不符 / 深链被拒共用）。

    为什么要收成一支把手：这两行的原因是**账上已经收过口的那一句**（记账处走的是
    `short_reason(reason, 120)`），而渲染处当年各写了一份 `str(v[1])[:46]`——第十一轮实机
    报告里 `china.makepolo.com 42 次（… 仅这一条深链，通道入）` 那半截词就是这么来的：
    两刀之间隔了一层字典与一层循环变量，第 30 条当时那一判看不见。现在两处走同一支把手，
    夹具也能直接喂一句长原因对拍「切点落在哪」。次数降序：读报告的人第一眼要看到的是
    堵得最久的那条腿，不是字典序第一条。
    """
    parts = [f"{h} {v[0]} 次（{short_reason(v[1], limit)}）"
             for h, v in sorted((mapping or {}).items(), key=lambda kv: -int(kv[1][0]))]
    return f"  {title}: " + "｜".join(parts)


def _importable(crawler, rows):
    """这一路的原始结果按**生产入库口径**能留下几行，返回 (行数或 None, 复算失败原因)。
    走的是 make_row + CdpSiteCrawler.filter_import_rows——与真导出、真上传完全同一把
    尺子，绝不在探针里另写一份「这条有没有联系方式」的判断（那份判断本轮就是把
    马可波罗的 51 家判成「命中 51/51」的那份）。
    行数为 None 只在两种情况：一批都没有（没什么可判）、或这一路复算不成（探针自己的
    故障）。它必须与 0 分清楚：0 是**站点的事实**（全是死数据，判红），None 是**我们没测到**
    （只许说「未判」，不许顺着打绿——探针替站点打包票是这一族假绿的另一半）。"""
    if not rows:
        return None, ""
    if not hasattr(crawler, "_to_cleaner_record"):
        return None, "这一路没有 _to_cleaner_record（清洗映射缺失，入库口径无从复算）"
    try:
        from customer_library_cleaner import make_row
        from cdp_base import CdpSiteCrawler
        recs = []
        for r in rows:
            f = crawler._to_cleaner_record(r)
            recs.append(make_row(f, [str(f.get("数据来源") or crawler.PLATFORM_NAME)]))
        return len(CdpSiteCrawler.filter_import_rows(recs)), ""
    except Exception as e:
        return None, f"{type(e).__name__}：{short_reason(str(e).splitlines()[0], 60)}"


def _legs_line(st):
    """通路账渲染：每条取号通路各发过几次请求、列表卡带产品 id 的比例。
    由来（2026-11-16 实机取证）：站方把产品 id 从 contact_us.html?proid= 挪到产品标题链接，
    解析没跟上 →「产品页明文手机」这条最硬的腿整轮 0 次请求，而 src_count 只累加从不外读，
    账上没有一行说过这件事。通路静默死亡必须在这一行里看得见。
    `cp_leg`（马可波罗的第三条取号腿：走了几家 / 出货几家）跟着同一行渲染——它是抓取路径
    而不是号码来源，并进 legs 会让一条号在账上出现两次，但静默不跑同样必须看得见。"""
    legs = st.get("legs") or {}
    seg = "、".join(f"{k} {v} 次" for k, v in sorted(legs.items(), key=lambda kv: -kv[1]))
    raw = str(st.get("leg_pid_raw") or "")
    if raw:
        seg += f"｜列表卡带产品 id {raw}"
    cp = str(st.get("cp_leg") or "")
    if cp:
        seg += f"｜{cp}"
    # 借标签跑 JS 质询壳这条腿（尝试/跑完）：它不开就是「等冷却」，开了有没有用必须看得见，
    # 否则下一轮判「这条腿值不值得留」时又只剩注释里的好话（口径见 cdp_base._solve_js_shell_via_tab）
    jt = str(st.get("js_tab") or "")
    if jt:
        seg += f"｜{jt}"
    return seg


def _attrib(st, n, blocked, reason, diff="ok", peer=0, killed="", zero=(), imp=None,
            imp_err=""):
    """把「这一路到底行不行」拆成能证伪的几档，每种给的处方都不一样（2026-11-15 实机体检的教训）：
    上一轮 9 站有 4 站被写成「采不到」，逐一定性后没有一站属于「代码采不到」——3 站是增量库
    全跳过、1 站是 www.11467.com 整站 TCP 连不上，外加 1688 把整轮预算烧在等登录上。
    读数只问 stats() 的机器可读键，不许去猜日志文案（文案会自相矛盾正是这次的病根）。

    `diff`/`peer` 是**有产出那一侧**的两格新判据（旧版只在 0 家时才开口，于是 4 家也能打 ✅）：
      · diff=="suspect" 两词结果集重合 → 数据根本不能用，比 0 家更坏（0 家至少不撒谎）；
      · diff=="unknown" 某个关键词没出货 → 关键词生效与否这轮没被证明，不许记成"通过"；
        `zero` 是**真的**零产出的那几个词名，结论要点名它（硬写「第二个关键词」会把好词当坏词）；
      · peer>0 且本路产出显著低于同轮中位数 → 「>0」不是质量标准，得问为什么少了 4/25。
    `imp` 是这一路按**生产入库口径**（make_row + filter_import_rows，手机号/固话/邮箱/微信号
    四项）能留下的行数：产出高低与同轮参照一律按它比，不按「抓了多少家」比。None 表示
    这一路没法复算（不判），0 表示「全是死数据」（比低产出更坏，单独一档判红）。
    `imp_err` 是复算不成的原因（只在 imp=None 时有话要说）。这一格必须单独成档：
    「探针没测出来」和「站点没货」是两件事，把前者顺着算成 ✅ 是本族假绿的最后一块——
    它只许说「未判」，并写出为什么没测到（缺映射/映射抛异常），下一轮人才有的可查。
    深链被拒（stats()["deep_block"]）与通路账（stats()["legs"]）在这些档里是决定性证据：
    前者说明少的那些家卡在**某一条腿**上而不是没货，后者说明某条取号通路**一次都没发过请求**，
    两者的处方都是修通路而不是换关键词。

    返回 (标记, 结论句)；标记 ∈ {"", "⚠", "❌"}，空串表示不归这一管（交给调用方判产出质量）。
    """
    page_fail = int(st.get("page_fail") or 0)
    listed = int(st.get("listed") or 0)
    last_err = str(st.get("last_page_error") or "")
    waiting = str(st.get("waiting") or "")
    incr_skip = int(st.get("incr_skip") or 0)
    if blocked:
        return "⚠", f"拦停（{reason or '需人工过验证/换网络'}）｜本轮 {n} 家"
    if waiting:
        # 等人 ≠ 采不到：配额没烧、代码没错，处置是「去登录/去点验证」，不是改解析
        return "⚠", (f"仍在等{waiting}｜本轮 {n} 家（不是采不到：人处理完会自动续采，"
                     "先登录/过验证再谈这一路）")
    if not n:
        if page_fail and not listed:
            dead = "、".join((st.get("dead_hosts") or {}).keys())
            return "❌", (f"链路未通：{page_fail} 页一页都没取到"
                          f"（最后一次：{short_reason(last_err, 70) or '未记录'}）——先查出口网络/域名可达，"
                          f"这与解析代码无关，别照着它改爬虫"
                          # 熔断账是这一路「我们已经不再试了」的凭据：没有它，读报告的人
                          # 会以为再等等就有数据（由来见 cdp_base._skip_dead_host）
                          + (f"；已按主机熔断：{dead}" if dead else ""))
        if incr_skip:
            return "⚠", (f"0 家但增量库全跳过 {incr_skip} 家（链路是通的，种子库已含这些公司；"
                          "要新数据得换关键词或放开省份）")
        failed = int(st.get("failed") or 0)
        missing = int(st.get("no_contact") or 0)
        if listed and failed:
            # 「链路上明明回了 M 条、一条都没落地」≠「这个站没有你要的公司」：
            # 前者必须修（详情取不到/解析失效），后者该换词换站，改了代码反而引入假数据
            return "❌", (f"链路回了 {listed} 条但 {failed} 次都没落地"
                          "（详情/查询失败，不是平台没数据）——这一路必须修")
        if listed and missing:
            return "⚠", (f"{listed} 次查询里 {missing} 次平台确实查不到（0 家不是链路问题，"
                          "是名单/词与该平台不对口）"
                          # 「确实查不到」这句也只许分档后才能说：miss 那一档是我们的锅，
                          # 拿它去说「平台没货」会把可修的解析缺陷说成不可修的事实
                          + (f"｜分档 {_nc_line(st)}" if st.get("nc_kinds") else ""))
        return "❌", "0 家（采不到）"
    # ── 下面全是「有产出」那一侧：出了货不等于出对货，更不等于出全货，也不等于出的货能用
    if diff == "suspect":
        return "❌", (f"{n} 家但两个关键词的结果集高度重合——服务端没真按关键词过滤，"
                      "这批数据不能用（goepe/b2b818/qympw 三站就是这一族下线的）")
    # 「没测到」这件事要跟着每一条结论走，不能只在最后一条结论里说：某一路同时
    # 「产出异常 + 入库口径复算不成」时，只念前者就还是把探针自己的故障藏住了。
    _impnote = ("" if imp is not None
                else f"｜入库口径未判（{imp_err or '原因未记录'}）——这一条不是绿，是没测")
    if imp is not None and not imp:
        # 「抓到 N 家、能入库 0 家」是 2026-11-15 马可波罗的真实形状（51 家 → 49 家四项全空）：
        # 宽口径把它判成「命中 51/51 ✅」，漏斗随后整批吞掉。这一档比产出高低、比差分
        # 都要先判红——「全不能用」比「少」更坏，而旧报告里连一句怀疑都没有。
        _legs0 = _legs_line(st)
        # 标题句只说测到的事实（一行都进不了库），不再代分档下结论：旧句把「取号通路没出货」
        # 写在最前面，后面却可能接上一句「这不是通路缺陷」（世界工厂网 6 家全是站方没登），
        # 同一行里两句自相矛盾——读的人只会当成又一句「先修通路」，那正是本族最后一句谎。
        return "❌", (f"{n} 家但按生产入库口径（手机号/固话/邮箱/微信号四项）一条都不能用"
                      "——这一批一行都进不了客户库（是谁的责任只看下面那一行）"
                      + (f"｜通路账：{_legs0}" if _legs0 else "")
                      # 而「一条都不能用」到底是哪一方的责任，同样只有分档账能回答
                      + f"｜{_nc_advice(st)}")
    deep = _deep_group(st.get("deep_block") or {})
    out = n if imp is None else imp   # 产出高低一律按「能用多少」比，不按「抓了多少」比
    if peer and out < min(peer * 0.25, peer - 5):
        # 证据**逐条列全**而不是只挑第一条：旧写法用 if/elif 链，于是「深链被拒 + 四项全空」
        # 这种同时坏两处的形状只会念出第一处，读报告的人以为修一条腿就够了。
        ev = []
        if deep:
            ev.append("深链被拒 " + "、".join(f"{h}({v[0]} 次)" for h, v in deep.items()))
        if killed:
            ev.append(f"相关性砍单 {killed}")
        if st.get("page_fail"):
            ev.append(f"取页失败 {st.get('page_fail')} 页")
        if st.get("failed"):
            ev.append(f"详情失败 {st.get('failed')} 次")
        # 增量库已含多少家要念出来：它直接决定了本轮「最多能出多少家新的」，
        # 少了这一句，一个词跑过多轮后的低产出会被当成链路坏了（2026-11-16 第十轮：
        # 八方资源网 76 条里 73 家已含，只报「2 家 vs 参照 9 家」会把人引向改解析）
        if incr_skip and listed:
            ev.append(f"列表 {listed} 条里 {incr_skip} 家种子库已含"
                      f"（本轮最多只能出 {max(listed - incr_skip, 0)} 家新的）")
        if imp is not None and imp < n:
            ev.append(f"四项全空 {n - imp} 家（可入库仅 {imp}/{n}）")
        _legs = _legs_line(st)
        if _legs:
            ev.append(f"通路账 {_legs}")
        # 分档账与落点账也是这一档的决定性证据：少了的那些家到底是站方没给、藏着，
        # 还是我们没解析到，处方完全不同（旧报告只会说「先修取号通路」）
        _nc = _nc_line(st)
        if _nc:
            ev.append(f"无联系方式分档 {_nc}")
        _land = st.get("landing") or {}
        if _land:
            ev.append("落点不符 " + "、".join(
                f"{h}({v[0]} 次)" for h, v in sorted(_land.items(), key=lambda kv: -int(kv[1][0]))))
        broken = bool(deep or killed or st.get("failed") or st.get("page_fail")
                      or (imp is not None and imp * 2 < n))
        # 有证据＝这一路确实坏了（该修）；没证据只是词/站薄（该换词换站），
        # 两者都给红会把「平台本来货少」也判成缺陷，下一轮就没人信这份报告了
        # 结论里的「可入库」只念真的测出来的那个数：复算不成时拿抓到的家数顶上去，
        # 读报告的人会以为这一路测过且全都能用——参照仍然按原始家数比（不装懂不等于不算）
        _outtxt = (f"{imp} 家" if imp is not None
                   else f"未测（参照是拿抓到的 {n} 家比的）")
        return ("❌" if broken else "⚠"), (
            f"产出异常：抓到 {n} 家、可入库 {_outtxt} vs 同轮参照 {peer:.0f} 家"
            f"（同词同页数，参照同样按入库口径）｜{'｜'.join(ev) or '账上无异常证据'}"
            + ("——少的那些家卡在这条腿上，不是平台没货" if deep else "")
            + ("——货是本站的闸门砍的，不是平台没货" if killed else "")
            + (_nc_advice(st) if imp is not None and imp * 2 < n else "")
            + _impnote)
    if diff == "unknown":
        # 点名**真正**零产出的那个词：旧文案硬写「第二个关键词」，可实机那轮零产出的是
        # 第一个词（八方资源网红话「泵」），照着错名字去换词会把好词当坏词扔掉。
        who = "、".join(f"「{k}」" for k in (zero or ())) or "其中一个关键词"
        if killed:
            # 「差分没跑成」与「差分被自己的相关性判定砍成没跑成」是两回事：后者不需要
            # 换词、不需要复跑，需要的是复核判定口径（同族类目桥），所以处方必须改写
            starved = [k for k in (zero or ()) if _kw_starve(st, k)]
            thin = [k for k in starved if _kw_thin(st, k)]
            if thin and len(thin) == len(starved):
                # 全是「站方只回个位数条」的词：约束项在货源，不在本机闸门。把这一形打成 ❌
                # 会把读者支使去改一个改了也没用的口径（2026-11-16 实机「窗帘导轨」：
                # 站方自报「共找到 1 个」，旧报告给的却是「该改的是判定口径」）
                return "⚠", (f"{n} 家但{who}零产出：站方对这些词只回个位数条（{killed}）"
                              "——不是链路问题，改本机相关性口径也变不出货；"
                              "该换的是关键词或这一路的搜索索引" + _impnote)
            return "❌", (f"{n} 家但{who}零产出：站方回了货、被本机相关性闸门砍掉"
                          f"（{killed}）——不是平台没货、也不是链路问题，该改的是判定口径"
                          + (f"（其中 {'、'.join('「%s」' % k for k in thin)} 是站方只回几条，"
                             "改口径救不回来）" if thin else "")
                          + _impnote)
        return "⚠", (f"{n} 家但{who} 0 家：差分没跑成，"
                      "这一轮的产出不能证明关键词真的生效（换词复跑一遍再看）" + _impnote)
    if imp is None:
        # 走到这里意味着账上没有任何站点侧异常——旧写法会 return "", "" 让调用方打 ✅。
        # 但「能不能入库」这一格我们根本没测出来（缺映射/映射抛异常），绿必须换成未判：
        # 红是站点的事实，绿是站点的事实，灰才是探针自己的故障，不许冒充前两者任何一种。
        return "⚠", (f"{n} 家，但入库口径复算没跑成（{imp_err or '原因未记录'}）："
                      "探针说不出「这批能不能用」，不许把它当绿——先修复算再谈这一路")
    return "", ""


def _fmt_site(slug, crawler, kws):
    """一站的体检账（纯函数式拼装，方便单测与人工复核对齐同一把尺子）。

    返回的第 8 个值是差分状态、第 9 个是「零产出关键词被本机相关性闸门砍掉」的说明句；
    第 10/11/12/13 个是「按生产入库口径可入库几行 / 真正零产出的词名 / 通路账 /
    复算不成的原因」——判定要读这几格，不许再回头猜日志文案。新字段一律**追加在末尾**：
    夹具 _rep 与 _peer_for 按下标读数，插中间会静默错位。
    """
    st = crawler.stats() if hasattr(crawler, "stats") else {}
    rows = list(getattr(crawler, "results", []) or [])
    by_kw = defaultdict(set)
    miss_stamp = 0
    for r in rows:
        by_kw[str(r.get("关键词") or "").strip()].add(str(r.get("公司名称") or "").strip())
        if not str(r.get("数据来源") or "").strip():
            miss_stamp += 1
    contact_n = sum(1 for r in rows if any(str(r.get(k) or "").strip() for k in CONTACT_KEYS))
    # 同一批行再用生产入库口径复算一次：contact_n 是宽口径（含只有官网），只配当对照
    imp_n, imp_err = _importable(crawler, rows)
    lines = []
    blocked = bool(getattr(crawler, "_blocked", False))
    reason = str(getattr(crawler, "_block_reason", "") or "")
    lines.append(f"  产出: 入库 {len(rows)} 家｜翻页 {st.get('pages', '-')}｜"
                 f"列表条目 {st.get('listed', '-')}｜详情成功 {st.get('ok', '-')}｜"
                 f"无联系方式 {st.get('no_contact', '-')}｜详情失败 {st.get('failed', '-')}｜"
                 f"早过滤 {st.get('early', '-')}｜详情不相关 {st.get('filtered', '-')}")
    # 链路这一笔平时全是 0，出了事却是唯一能定性的数：单独占一行，别混进产出那长句里
    _dead = "、".join((st.get("dead_hosts") or {}).keys()) or "（无）"
    lines.append(f"  链路: 取页失败 {st.get('page_fail', 0) or 0} 页｜"
                 f"最后一次={short_reason(st.get('last_page_error'), 70) or '（无）'}｜"
                 f"增量跳过 {st.get('incr_skip', 0) or 0} 家｜"
                 f"熔断主机 {_dead}｜"
                 f"等人={str(st.get('waiting') or '') or '（无）'}")
    lines.append(f"  逐词净新增: {st.get('kw_yield', {})}")
    _legs = _legs_line(st)
    if _legs:
        # 通路账单独占一行：它是「哪一条腿今天一次都没发过请求」的唯一可见证据。
        # 马可波罗那轮最硬的一条腿（产品页明文手机）整轮 0 次请求，账上却没有一行说过
        lines.append(f"  通路账: {_legs}")
    _nc = _nc_line(st)
    if _nc:
        # 「无联系方式 N 家」后面的这一行才是能执行的结论：六档的处方互相矛盾，只报总数
        # 就只会得到一句「先修取号通路」（世界工厂网那一轮就是被这句支使错的）
        lines.append(f"  无联系方式分档: {_nc}")
    _land = st.get("landing") or {}
    if _land:
        # 落点不符（导航的那一页与浏览器留下的那一页不是同一页）只进过 stats()、从没进过人眼：
        # 它是「把别人的页当这一家」那一类事故的先兆，不念出来就等于没记
        lines.append(_host_reason_line("落点不符", _land))
    diff, diff_line = _diff_judge(by_kw, kws)
    lines.append(diff_line)
    _deep = _deep_group(st.get("deep_block") or {})
    if _deep:
        # 深链账单独占一行：它是「某条腿今天不通」的唯一可见证据，混进链路那长句里
        # 就没人会去换通路（马可波罗 contact_us 与商铺首页两条腿的差别正是在这里现形的）
        # 按**主域**聚合渲染：马可波罗每家商铺一个子域，按主机念会念出 50 段同一句话
        # （2026-11-15 实机那一行 3000 字，没人读得下去），聚合后只剩一条腿一句话
        lines.append(_host_reason_line("深链被拒", _deep))
    # 零产出的关键词：先点名是哪几个词（判定文案要说得出真名字），再问货是不是被自己的
    # 相关性闸门砍掉的（账在 kw_filtered/kw_early 两本）
    zero = [k for k in kws[:2] if k and not by_kw.get(k)]
    killed = "；".join(f"「{_k}」{_kw_starve(st, _k)}" for _k in zero if _kw_starve(st, _k))
    if killed:
        lines.append(f"  相关性砍单（该词零净新增）: {killed}")
    if rows:
        pct = contact_n / len(rows)
        lines.append(f"  联系方式: 任一 {contact_n}/{len(rows)} = {pct:.0%}（宽口径，含只有官网）")
        if imp_n is None:
            # 复算不成必须写出**为什么**不成：旧文案硬写「没有清洗映射」，可异常那一支的原因
            # 被吞掉了，读报告的人照着这句话去找映射，其实那边抛的是 KeyError
            lines.append(f"  可入库数: —（复算不成：{imp_err or '原因未记录'}）"
                         "判定不按它开口，但这一格不许算绿")
        else:
            lines.append(f"  可入库数: {imp_n}/{len(rows)}"
                         f"（生产口径：手机号/固话/邮箱/微信号四项至少一项，与真导出同一把尺子）")
        lines.append(f"  来源戳: 缺「数据来源」{miss_stamp} 条"
                     + ("" if miss_stamp else "（跨站去重不会整批丢）"))
        for r in rows[:2]:
            lines.append(f"  样例: {r.get('公司名称', '')!s:<34.34}"
                         f" | 手机={r.get('手机', '')!s:<18.18}"
                         f" | 邮箱={r.get('邮箱', '')!s:<24.24}"
                         f" | 网址={str(r.get('公司网址') or '')[:34]}")
    else:
        lines.append(f"  联系方式: —（一条都没有，来源戳缺 {miss_stamp} 条）")
    if blocked or reason:
        lines.append(f"  拦停归因: blocked={blocked}｜原因={reason or '（无记录）'}")
    return (lines, len(rows), contact_n, miss_stamp, blocked, reason, st, diff, killed,
            imp_n, zero, _legs, imp_err)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sites", default="", help="逗号分隔 slug，默认全部关键词站")
    ap.add_argument("--kws", default="泵,生物质锅炉", help="两个语义无关关键词")
    ap.add_argument("--pages", type=int, default=1)
    ap.add_argument("--delay", type=float, default=3.0)
    ap.add_argument("--max-rec", type=int, default=120,
                    help="每站入库上限（体检用，别把配额烧光）：设太小会让第二个关键词"
                         "跑不到，差分那条判据就变成「无法判定」（上一轮 25 就是这么自削的）")
    ap.add_argument("--budget", type=int, default=900, help="整轮墙钟预算（秒），到点停止并出报告")
    ap.add_argument("--list", default="", help="名单通道 slug（qzd/riskbird/aiqicha），与关键词站分轮跑")
    ap.add_argument("--names", default="", help="名单通道用的公司名（逗号分隔），为空则取内置样例")
    args = ap.parse_args()

    kws = [x.strip() for x in args.kws.split(",") if x.strip()]
    slugs = [x.strip() for x in args.sites.split(",") if x.strip()] or list(SITE_ORDER)
    slugs = [s for s in SITE_ORDER if s in slugs]
    list_slugs = [x.strip() for x in args.list.split(",") if x.strip()]

    sandbox = REPO / "debug" / "_live_sandbox" / time.strftime("%m%d_%H%M%S")
    sandbox.mkdir(parents=True, exist_ok=True)

    sess = ParallelSession(enabled=slugs if not list_slugs else [])
    sess.set_task_folder(sandbox)
    sess.configure(max_pages=args.pages, delay_between=args.delay, max_records=args.max_rec)

    all_logs = []
    lock = threading.Lock()

    def _on_log(msg):
        with lock:
            all_logs.append(str(msg))

    if sess.signals is not None:
        try:
            sess.signals.log_signal.connect(_on_log)
        except Exception:
            pass
    else:                                       # 无 PyQt 时的兜底：直接换 _log
        sess._log = lambda m: _on_log(m)

    if list_slugs:
        names = [x.strip() for x in (args.names or "格力电器股份有限公司,宁德时代新能源科技股份有限公司,徐工集团工程机械股份有限公司").split(",") if x.strip()]
        name_file = sandbox / "体检名单.txt"
        name_file.write_text("\n".join(names), encoding="utf-8")
        on, added = sess.bind_pending(str(sandbox), list_file=str(name_file), want_slugs=list_slugs)
        print(f"名单通道: 启用={on} 入列={added} 名单={names}")
        run_kws = []
    else:
        run_kws = kws

    print(f"═══ 逐平台实机体检  {time.strftime('%Y-%m-%d %H:%M:%S')} ═══")
    print(f"沙箱落点: {sandbox}")
    print(f"参与: {'名单通道 ' + ','.join(list_slugs) if list_slugs else ','.join(slugs)}"
          f"｜关键词={run_kws}｜每站最多 {args.max_rec} 条 / {args.pages} 页｜预算 {args.budget}s")

    t = threading.Thread(target=lambda: _safe_crawl(sess, run_kws), daemon=True)
    t.start()
    deadline = time.time() + args.budget
    while t.is_alive() and time.time() < deadline:
        t.join(5)
        with lock:
            tail = all_logs[-1:] or [""]
        print(f"  … 已用 {int(args.budget - max(0, deadline - time.time()))}s｜{tail[0][:110]}")
    if t.is_alive():
        print("!! 超出预算，按「停止」口径收尾")
        try:
            sess.stop_crawl()
        except Exception:
            pass
        t.join(120)

    # 只报**本轮真的参与**的那几路：ParallelSession.__init__ 会把 SITE_ORDER 全部
    # 实例化（界面上没勾的站也在字典里），拿字典全量读数就会把「压根没启动」报成
    # 「采不到」——探针自己造的假红，上一版就是这么把 mic 和 1688 写成 ❌ 的。
    crawlers = ([sess.crawlers[s] for s in slugs] if not list_slugs else
                [sess.channels[s] for s in list_slugs if s in sess.channels])
    status = sess.site_status()
    print("\n════════════════ 分站报告 ════════════════")
    # 先读数、后判定：判定要用到「同轮其它站的产出」作参照，一遍循环里拿不到这个数
    # （旧版就是一遍循环里边读边判，于是产出判据只能看「>0」——4 家与 62 家同样 ✅）
    reports = []
    for c in crawlers:
        slug = getattr(c, "PLATFORM_SLUG", "?")
        name = STATS_LABELS.get(slug, SITE_NAMES.get(slug, slug))
        print(f"\n── {slug} {name} ── 状态={status.get(slug, '未启动')}")
        try:
            (lines, n, contact_n, miss, blocked, reason, st, diff, killed,
             imp_n, zero, legs, imp_err) = _fmt_site(slug, c, run_kws or kws)
        except Exception as e:
            print(f"  ❌ 读数失败：{e}")
            traceback.print_exc()
            reports.append((slug, name, 0, 0, 0, False, "", {}, "unknown", str(e),
                            "", None, [], "", ""))
            continue
        for ln in lines:
            print(ln)
        per_site = [ln for ln in all_logs if name.split("补采")[0] in ln or slug in ln]
        bad = [ln for ln in per_site if BLOCK_WORDS.search(ln)][-6:]
        if bad:
            print("  拦截/异常日志：")
            for ln in bad:
                print(f"    · {ln[:160]}")
        reports.append((slug, name, n, contact_n, miss, blocked, reason, st, diff, "",
                        killed, imp_n, zero, legs, imp_err))

    verdicts = []
    for (slug, name, n, contact_n, miss, blocked, reason, st, diff, read_err,
         killed, imp_n, zero, legs, imp_err) in reports:
        if read_err:
            # 读数失败不是「采不到」：把它算成产出 0 会同时污染同轮参照与结论（探针自己的
            # 故障必须由探针说清楚，否则下一轮人会去改爬虫）
            verdicts.append((name, f"读数失败：{short_reason(read_err, 80)}", "❌"))
            continue
        # 参照里剔掉自己/拦停/等人/读数失败（判据与理由见 _peer_for）
        peer = _peer_for(reports, slug)
        # 判定顺序：先分「被平台拦／在等人／链路没通／增量库全跳过」与「代码采不到」，
        # 这四类的处方完全不同——把拦停或等登录算成采不到，会让人去改本来没毛病的代码
        # （上一轮 4 站「采不到」就是这么绕的远路，逐一定性后没有一站属于代码问题）。
        # 有产出那一侧再问「能不能入库」、差分与同轮参照：出了货不等于出对货（假命中），
        # 也不等于出全货，更不等于出的货能用（入库口径复算就是问这一件事）。
        mark, verdict = _attrib(st, n, blocked, reason, diff=diff, peer=peer, killed=killed,
                                zero=zero, imp=imp_n, imp_err=imp_err)
        if not mark:
            if miss:
                # 缺来源戳的记录会在跨站去重时被整批静默丢弃，这一条是缺陷不是风格问题
                mark, verdict = "❌", f"{n} 家但有 {miss} 条缺来源戳"
            elif imp_n is not None and imp_n * 2 < n:
                mark, verdict = "⚠", (f"{n} 家但可入库仅 {imp_n}/{n}"
                                      "（超过一半连手机/固话/邮箱/微信四项都没有）"
                                      + (f"｜通路账：{legs}" if legs else ""))
            elif imp_n is None:
                # 这一支是上面「未判不许打绿」的第二道锁：_attrib 的判档顺序哪天被改动、
                # 漏掉了 None 那一档，这里必须响，而不是让 ✅ 重新长出来（假绿最难查的
                # 形状就是「绿本身没变，只是通往绿的那条路上多了一格没测」）
                mark, verdict = "⚠", (f"{n} 家，但入库口径复算没跑成（{imp_err or '原因未记录'}）"
                                      "——探针说不出这批能不能用，不许打绿")
            else:
                # ✅ 那行只念**真的判过**的格子：差分没跑成/没有参照时不许写「OK」，
                # 入库口径没复算时也不许拿宽口径的命中数充当「数据能用」的证据
                mark, verdict = "✅", (f"{n} 家 / 可入库 {imp_n}"
                                       f"（宽口径命中 {contact_n}）/ 差分 OK / "
                                       + (f"产出与同轮参照 {peer:.0f} 家相当" if peer
                                          else "产出高低未判（同轮参照不足）")
                                       + " / 来源戳齐")
        elif diff == "suspect" or (peer and (n if imp_n is None else imp_n) < peer * 0.25):
            verdict = f"{verdict}（差分={diff}，同轮参照 {peer:.0f} 家）"
        verdicts.append((name, verdict, mark))

    raw_n = sum(len(getattr(c, "results", []) or []) for c in crawlers)
    funnel = {}
    try:
        clean = sess.clean_records(funnel_out=funnel)
        clean_n = len(clean or [])
    except Exception as e:
        print(f"\n❌ 清洗漏斗跑不起来：{e}")
        traceback.print_exc()
        clean_n = -1
    print("\n════════════════ 汇总 ════════════════")
    print(f"入库原始合计 {raw_n} 条 → 清洗后 {clean_n} 条"
          + (f"（漏斗吞掉 {raw_n - clean_n} 条）" if clean_n >= 0 and raw_n > clean_n else ""))
    if funnel:
        # 每一档的数必须念出来：只报「吞掉 50 条」的报告等于没查（2026-11-15 实机就是这样，
        # 50 条到底死在严格档、合并还是四项全空，当时谁也答不上，通路断了一整轮没人去看）
        _g = funnel.get("gate") or {}
        _by = funnel.get("no_contact_by_source") or {}
        print(f"  漏斗分解: 喂进 {funnel.get('fed', '-')} 条 →"
              f" 严格档剔 {_g.get('dropped', 0)} 行（无依据未判 {_g.get('unknown', 0)}）→"
              f" 剔无效 {funnel.get('invalid', 0)} 条 →"
              f" 跨平台合并掉 {funnel.get('merged', 0)} 条 → 剩 {funnel.get('rows', '-')} 行 →"
              f" 四项全空剔 {funnel.get('import_dropped', 0)} 条 → 入库 {funnel.get('import_kept', 0)} 条"
              + (f"（裸号补区号 {funnel['area_filled']} 个）" if funnel.get("area_filled") else ""))
        if _by:
            print("  ↳ 四项全空按来源: " + "、".join(
                f"{s} {v} 条" for s, v in sorted(_by.items(), key=lambda kv: -kv[1])))
        # 自审：两道判据必须是同一把尺子。这里对不上就说明有人又写了第二份「有没有联系方式」
        # 的判断（本轮缺陷的根形），当场响，不等下一轮人去猜。
        if funnel.get("import_dropped", 0) != funnel.get("no_contact", 0):
            print(f"  ⚠ 口径分叉：build_clean_rows 判四项全空 {funnel.get('no_contact')} 条，"
                  f"filter_import_rows 实际剔掉 {funnel.get('import_dropped')} 条"
                  "——同一件事两把尺子，必有一把是错的")
    for name, v, m in verdicts:
        print(f"  {m} {name}: {v}")
    print(f"\n沙箱产物: {sandbox}")
    # 退出码按归因走，不看总数：只要还有一路是「链路未通／采不到／缺来源戳」就是红，
    # 哪怕别的站出了几千条——上一版正是这么把「11467 整站连不上」混在一堆绿的产出里放行的。
    hard = [name for name, _v, m in verdicts if m == "❌"]
    if hard:
        print(f"❌ 需要处置的这几路：{'、'.join(hard)}")
    # 收尾：只关本站标签/自己的会话，共享 Chrome 留着（与生产 close_browser 口径一致）
    for c in getattr(sess, "_all_result_crawlers", lambda: list(crawlers))():
        try:
            c.close_browser()
        except Exception:
            pass
    return 1 if hard or not raw_n else 0


def _safe_crawl(sess, kws):
    try:
        sess.crawl(kws)
    except Exception as e:
        print(f"\n❌ 会话线程异常：{e}")
        traceback.print_exc()


if __name__ == "__main__":
    sys.exit(main())
