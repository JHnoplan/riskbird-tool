#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐平台实机采集体检（不做离线替身，真的联网、真的开爬）。

为什么要有这个入口：离线套件判的是「代码按我的理解在动」，只有实机能回答
「这个平台今天还能不能采到数据」。上一轮用户报「1688 一直 0 家」「企知道爱企查都有问题」
时，能给出结论的只能是这种探针——离线用例当时全绿，而线上真的采不到。

每一站的判据不是「跑完了没报错」，而是四条能证伪的硬账：
  1) 有产出：入库条数 > 0；
  2) 关键词真的生效：两个语义无关关键词的公司名集合必须显著不同（假命中陷阱，
     goepe/b2b818/qympw 三站就是靠这条判据事后定性、当场下线的）；
  3) 有联系方式：入库行里电话/手机/邮箱/网址至少有一项的比例；
  4) 来源戳齐全：每条都带「数据来源」，缺了会在跨站去重时被整批静默丢弃。
最后再走一遍生产漏斗 clean_records，比对入库前后条数——某站被整批吞掉就在这一步现形。

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

CONTACT_KEYS = ("电话", "手机", "邮箱", "公司网址")
# 拦截/异常类日志的关键字：体检报告里要把这些行原样带出来，归因靠它们
BLOCK_WORDS = re.compile(r"(拦截|验证码|滑块|限流|冷却|额度|未登录|登录|失败|异常|超时|403|429|502|blocked)")


def _attrib(st, n, blocked, reason):
    """把「这一路 0 家」拆成四种来路，每种给的处方都不一样（2026-11-15 实机体检的教训）：
    上一轮 9 站有 4 站被写成「采不到」，逐一定性后没有一站属于「代码采不到」——3 站是增量库
    全跳过、1 站是 www.11467.com 整站 TCP 连不上，外加 1688 把整轮预算烧在等登录上。
    读数只问 stats() 的机器可读键，不许去猜日志文案（文案会自相矛盾正是这次的病根）。

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
            return "❌", (f"链路未通：{page_fail} 页一页都没取到"
                          f"（最后一次：{last_err[:70] or '未记录'}）——先查出口网络/域名可达，"
                          "这与解析代码无关，别照着它改爬虫")
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
                          "是名单/词与该平台不对口）")
        return "❌", "0 家（采不到）"
    return "", ""


def _fmt_site(slug, crawler, kws):
    """一站的体检账（纯函数式拼装，方便单测与人工复核对齐同一把尺子）。"""
    st = crawler.stats() if hasattr(crawler, "stats") else {}
    rows = list(getattr(crawler, "results", []) or [])
    by_kw = defaultdict(set)
    miss_stamp = 0
    for r in rows:
        by_kw[str(r.get("关键词") or "").strip()].add(str(r.get("公司名称") or "").strip())
        if not str(r.get("数据来源") or "").strip():
            miss_stamp += 1
    contact_n = sum(1 for r in rows if any(str(r.get(k) or "").strip() for k in CONTACT_KEYS))
    lines = []
    blocked = bool(getattr(crawler, "_blocked", False))
    reason = str(getattr(crawler, "_block_reason", "") or "")
    lines.append(f"  产出: 入库 {len(rows)} 家｜翻页 {st.get('pages', '-')}｜"
                 f"列表条目 {st.get('listed', '-')}｜详情成功 {st.get('ok', '-')}｜"
                 f"无联系方式 {st.get('no_contact', '-')}｜详情失败 {st.get('failed', '-')}｜"
                 f"早过滤 {st.get('early', '-')}｜详情不相关 {st.get('filtered', '-')}")
    # 链路这一笔平时全是 0，出了事却是唯一能定性的数：单独占一行，别混进产出那长句里
    lines.append(f"  链路: 取页失败 {st.get('page_fail', 0) or 0} 页｜"
                 f"最后一次={str(st.get('last_page_error') or '')[:70] or '（无）'}｜"
                 f"增量跳过 {st.get('incr_skip', 0) or 0} 家｜"
                 f"等人={str(st.get('waiting') or '') or '（无）'}")
    lines.append(f"  逐词净新增: {st.get('kw_yield', {})}")
    a, b = (kws + ["", ""])[:2]
    sa, sb = by_kw.get(a, set()), by_kw.get(b, set())
    if sa and sb:
        inter = sa & sb
        ratio = len(inter) / max(1, min(len(sa), len(sb)))
        verdict = "OK（服务端真按关键词过滤）" if ratio < 0.5 else "疑似假命中（两词结果高度重合）"
        lines.append(f"  差分: 「{a}」{len(sa)} 家 vs 「{b}」{len(sb)} 家｜交集 {len(inter)} 家"
                     f"（重合 {ratio:.0%}）→ {verdict}")
    else:
        lines.append(f"  差分: 无法判定（词 A 有 {len(sa)} 家、词 B 有 {len(sb)} 家，至少一边为空）")
    if rows:
        pct = contact_n / len(rows)
        lines.append(f"  联系方式: 任一 {contact_n}/{len(rows)} = {pct:.0%}")
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
    return lines, len(rows), contact_n, miss_stamp, blocked, reason, st


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
    verdicts = []
    for c in crawlers:
        slug = getattr(c, "PLATFORM_SLUG", "?")
        name = STATS_LABELS.get(slug, SITE_NAMES.get(slug, slug))
        print(f"\n── {slug} {name} ── 状态={status.get(slug, '未启动')}")
        try:
            lines, n, contact_n, miss, blocked, reason, st = _fmt_site(slug, c, run_kws or kws)
        except Exception as e:
            print(f"  ❌ 读数失败：{e}")
            traceback.print_exc()
            verdicts.append((name, "读数失败", "❌"))
            continue
        for ln in lines:
            print(ln)
        per_site = [ln for ln in all_logs if name.split("补采")[0] in ln or slug in ln]
        bad = [ln for ln in per_site if BLOCK_WORDS.search(ln)][-6:]
        if bad:
            print("  拦截/异常日志：")
            for ln in bad:
                print(f"    · {ln[:160]}")
        # 判定顺序：先分「被平台拦／在等人／链路没通／增量库全跳过」与「代码采不到」，
        # 这四类的处方完全不同——把拦停或等登录算成采不到，会让人去改本来没毛病的代码
        # （上一轮 4 站「采不到」就是这么绕的远路，逐一定性后没有一站属于代码问题）。
        mark, verdict = _attrib(st, n, blocked, reason)
        if not mark:
            if miss:
                # 缺来源戳的记录会在跨站去重时被整批静默丢弃，这一条是缺陷不是风格问题
                mark, verdict = "❌", f"{n} 家但有 {miss} 条缺来源戳"
            elif contact_n * 2 < n:
                mark, verdict = "⚠", f"{n} 家但联系方式命中率仅 {contact_n}/{n}"
            else:
                mark, verdict = "✅", f"{n} 家 / 命中 {contact_n} / 差分与来源戳 OK"
        verdicts.append((name, verdict, mark))

    raw_n = sum(len(getattr(c, "results", []) or []) for c in crawlers)
    try:
        clean = sess.clean_records()
        clean_n = len(clean or [])
    except Exception as e:
        print(f"\n❌ 清洗漏斗跑不起来：{e}")
        traceback.print_exc()
        clean_n = -1
    print("\n════════════════ 汇总 ════════════════")
    print(f"入库原始合计 {raw_n} 条 → 清洗后 {clean_n} 条"
          + (f"（漏斗吞掉 {raw_n - clean_n} 条）" if clean_n >= 0 and raw_n > clean_n else ""))
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
