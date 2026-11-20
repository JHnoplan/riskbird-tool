"""逐条件探针：令牌已经合法落地，为什么 try_adopt_cached_view 仍然返回假。

背景（2026-11-18 实测）：debug/warmup_master_fp.py 阶段 1 成功写出 master_fp.json，
并且当场验证 master_fingerprint() == 视图 meta.fingerprint、is_current() == True；
但阶段 2 用一个全新 manager 调 get_merged_master() 却没有走秒开，而是掉回
_load_local_state 被内存闸门拦下抛 MemoryError —— 说明采纳路径上还有第二处断点，
而它跟令牌本身无关。

方法：把 try_adopt_cached_view 的每个前置条件按源码顺序逐个打印并就地判定，
**任何一步为假就停在那里**，不做整体物化（本探针内存必须保持有界，否则测不出
秒开的真实结论）。全部为真却仍返回假，则说明是采纳之后的动作把状态弄坏了。
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "b2bcollect_build"))

from data_pool import (  # noqa: E402
    MASTER_FP_FILE, DataPoolManager, _silver_rule_version, _tok_num,
)

CACHE_DIR = os.path.expanduser("~/Desktop/数据池缓存")


def main() -> int:
    print(f"令牌文件存在: {os.path.exists(os.path.join(CACHE_DIR, MASTER_FP_FILE))}")
    mgr = DataPoolManager()
    print(f"cache_dir = {mgr._cache_dir}")

    print(f"[1] _master_cache 起手 = {mgr._master_cache!r}（必须是 None 才会去采纳）")
    print(f"[2] _store_off = {mgr._store_off}")
    st = mgr.store()
    print(f"[3] store() = {type(st).__name__ if st is not None else None}")
    if st is None:
        print("    ❌ 停在 [3]：视图句柄拿不到")
        return 1
    print(f"[4] readable() = {st.readable()}  path={st._path}  exists={st._path.exists()}")
    print(f"[5] _cache_rules_current() = {mgr._cache_rules_current()}  "
          f"(文件里的 silver={json.loads((mgr._cache_dir / 'rule_version.json').read_text()).get('silver')!r}"
          f"  当前算出的={_silver_rule_version()!r})")
    tok = mgr._read_master_fp_token()
    print(f"[6] 令牌 = {json.dumps(tok, ensure_ascii=False) if tok else None}")
    if not tok:
        print("    ❌ 停在 [6]：令牌读不出来")
        return 1
    print(f"[7] 令牌 silver 匹配 = "
          f"{str(tok.get('silver') or '') == _silver_rule_version()}")
    ok_file = mgr._token_cache_file_matches(tok)
    print(f"[8] _token_cache_file_matches = {ok_file}")
    if not ok_file:
        from data_pool import _file_identity
        name = str((tok.get("cache") or {}).get("file") or "")
        now = _file_identity(mgr._cache_dir / name)
        print(f"    令牌记的 = {json.dumps(tok.get('cache'), ensure_ascii=False)}")
        print(f"    现在算的 = {json.dumps(now, ensure_ascii=False)}")
    mgr._load_tombstone_state()
    tcount, tcrc = mgr._tomb_fingerprint()
    # 这里必须用产品同一个 _tok_num 取数：上一版探针照抄了 `int(x or -1)`，
    # 于是产品代码已经修对了、探针却仍然印「匹配=False」（0 or -1 = -1）——
    # 一个自己写了 bug 的探针会把人引向完全相反的结论。
    tb = tok.get("tomb") or {}
    tomb_match = (_tok_num(tb, "count") == tcount
                  and _tok_num(tb, "crc") == tcrc)
    print(f"[9] 删除向量身份 now=({tcount},{tcrc})  令牌记的={tb}  匹配={tomb_match}")
    fp = (int(tok["count"]), int(tok["crc"]))
    print(f"[10] fp={fp} count>0={fp[0] > 0}  is_current={st.is_current(fp)}")
    print(f"     meta 里的 fingerprint={st.meta().get('fingerprint')} "
          f"record_count={st.meta().get('record_count')} "
          f"schema_version={st.meta().get('schema_version')}")

    print("\n── 直接调用采纳本身（看它到底认不认）──")
    t0 = time.time()
    ok = mgr.try_adopt_cached_view(progress_cb=lambda s: print(f"     · {s}"))
    print(f"try_adopt_cached_view() = {ok}   耗时 {(time.time() - t0) * 1000:.0f}ms")
    print(f"_master_cache = {type(mgr._master_cache).__name__}   "
          f"_view_fp={mgr._view_fp}   _master_fp={mgr._master_fp}")
    if ok:
        import resource
        print(f"峰值 RSS={resource.getrusage(resource.RUSAGE_SELF).ru_maxrss >> 20}MB"
              f"（秒开应当只有几十 MB）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
