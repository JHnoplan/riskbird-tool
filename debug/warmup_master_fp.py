"""一次性预热：把身份令牌 master_fp.json 落地，并对这条判据做双向探针。

为什么必须跑这一次（2026-11-18 本机实测）：
  缓存目录里只有 master.pkl(359MB) + pool_store.db(1.7GB)，**没有 master_fp.json**。
  三段身份链的第二段（令牌）缺失 → try_adopt_cached_view 恒假 → 每次开「搜索
  采购商」都要把 73.8 万条整体解析进内存（本机约 6GB，而机器只有 8GB）→ 用户
  反馈的「加载很久很久」真身就是这个。令牌缺失的原因是：09:22 那次写盘是今早
  06:46 启动的**旧进程**干的，旧代码里还没有写令牌的代码。

为什么不走「拿视图 meta 里的指纹直接拼一个令牌」这条捷径：
  debug/view_fp_probe.py 实测证明视图 payload 里回写了派生列（region/province/
  industry/quality），流式重算得到 (738734, 4160093755)，而 meta 存的是
  (738734, 2406017331) —— 两种表示不同源。拿其中一个去填另一个就是造一个
  「看上去对」的假身份：错了不会报错，只会让用户静默读到错池。代价不对称，
  所以宁可付一次几分钟的高内存运行，让产品代码自己读盘、自己算、自己写。

三个阶段（第三阶段是反向探针，缺它则前两个阶段的通过没有意义）：
  1 预热：force_full_load=True 越过内存闸门，完整加载一次并落令牌
  2 正向：全新 manager 走秒开路径，必须采纳成功、必须是 LazyMaster、
          必须**没有物化**（materialized 为假），并给出端到端毫秒数
  3 反向：把令牌改名后全新 manager 必须**采纳失败**（证明判据不是空跑），
          随后恢复令牌
"""
import gc
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "b2bcollect_build"))

from data_pool import (  # noqa: E402
    MASTER_FP_FILE, DataPoolManager, LazyMaster, _approx_free_memory_bytes,
)

CACHE_DIR = os.path.expanduser("~/Desktop/数据池缓存")
TOKEN = os.path.join(CACHE_DIR, MASTER_FP_FILE)


def rss_mb() -> int:
    import resource
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss >> 20)


def say(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def phase1_warmup() -> None:
    say("── 阶段 1：一次性预热（完整加载 + 落令牌）──")
    avail, why = _approx_free_memory_bytes()
    say(f"闸门视角的可用内存：{avail >> 30}GB（依据 {why}）")
    mgr = DataPoolManager()
    say(f"预热前闸门判定：{mgr.master_memory_room()}")
    say(f"令牌是否存在（预热前）：{os.path.exists(TOKEN)}")

    t0 = time.time()
    master = mgr.get_merged_master(force_full_load=True,
                                   progress_cb=lambda s: say(f"  · {s}"))
    cost = time.time() - t0
    say(f"完整加载耗时 {cost:.1f}s   峰值 RSS={rss_mb()}MB   "
        f"条数={len(master):,}   类型={type(master).__name__}")

    fp = mgr.master_fingerprint()
    say(f"总表指纹 master_fingerprint() = {fp}")
    st = mgr.store()
    meta = st.meta() if st is not None else {}
    say(f"视图 meta = {json.dumps(meta, ensure_ascii=False)}")
    say(f"视图是否已与本批总表同源 is_current(fp) = "
        f"{st.is_current(fp) if st is not None else 'NO-STORE'}")
    if not os.path.exists(TOKEN):
        raise SystemExit("❌ 预热跑完仍没有令牌：写令牌的代码路径没被执行到，"
                         "后面的秒开验证全部无效")
    say(f"令牌内容 = {json.dumps(json.loads(open(TOKEN).read()), ensure_ascii=False)}")

    # 6GB 必须在这一阶段就还给系统，否则阶段 2 测出来的「秒开」是假象
    mgr._master_cache = None
    del master, mgr
    gc.collect()
    say(f"阶段 1 结束，已释放总表（RSS={rss_mb()}MB）")


def phase2_hit() -> None:
    say("── 阶段 2：正向探针（令牌命中 → 秒开且不物化）──")
    mgr = DataPoolManager()
    t0 = time.time()
    master = mgr.get_merged_master()
    ms = (time.time() - t0) * 1000
    lazy = isinstance(master, LazyMaster)
    say(f"get_merged_master() 端到端 {ms:.0f}ms   类型={type(master).__name__}   "
        f"条数={len(master):,}")
    assert lazy, f"❌ 采纳的不是 LazyMaster，说明仍然物化了总表：{type(master)}"
    assert not master.materialized, "❌ LazyMaster 已被物化，秒开没生效"
    assert mgr.master_is_lazy(), "❌ master_is_lazy() 判定与类型不一致"
    say(f"len() 与 bool 未触发物化（materialized={master.materialized}）✅")
    # 界面第一帧要用的三样东西都必须在不物化的前提下拿得到
    say(f"store_view_count() = {mgr.store_view_count()}   "
        f"materialized={master.materialized}")
    t1 = time.time()
    sug = mgr.store_suggest("华为", 8)
    say(f"store_suggest('华为') 耗时 {(time.time() - t1) * 1000:.1f}ms → "
        f"{json.dumps(sug, ensure_ascii=False)}")
    assert not master.materialized, "❌ 视图建议把总表物化了"
    say(f"阶段 2 结束 RSS={rss_mb()}MB（全程没有搬 6GB）")


def phase3_miss() -> None:
    say("── 阶段 3：反向探针（令牌缺失必须采纳失败）──")
    bak = TOKEN + ".bak"
    os.rename(TOKEN, bak)
    try:
        mgr = DataPoolManager()
        ok = mgr._adopt_lazy_master()
        say(f"没有令牌时 _adopt_lazy_master() = {ok}   "
            f"_master_cache={type(mgr._master_cache).__name__}")
        assert not ok, "❌ 令牌被改名后仍然采纳成功：这条判据是空跑的"
        del mgr
        gc.collect()
    finally:
        os.rename(bak, TOKEN)
    say(f"令牌已恢复：{os.path.exists(TOKEN)} ✅")


def main() -> int:
    if not os.path.exists(os.path.join(CACHE_DIR, "master.pkl")):
        raise SystemExit("❌ 本机没有 master.pkl，预热前提不成立")
    phase1_warmup()
    phase2_hit()
    phase3_miss()
    say("全部通过：令牌已落地，秒开路径已接通，且判据经反向验证非空跑")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
