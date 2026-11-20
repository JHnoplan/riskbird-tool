"""反向探针：视图能不能作为「有界内存重建总表 + 身份令牌」的来源。

判据只有一条硬指标：按 rowid 顺序流式读视图 payload，用与产品代码**同一个**
FingerprintAccumulator 累加，得到的 (条数, crc) 必须逐位等于视图 meta 里存的
(source_count, fingerprint)。

为什么这一条就够定生死：
  · meta.fingerprint 是建库那次「合并后完整总表」的指纹（master 顺序）；
  · 若按 rowid 流式重算能对上，说明「视图行序 == 总表顺序」且「payload 逐条
    回读后字段值不变」（company_norm/source_time/quality/region 四个入指纹的
    字段都在 payload 里）—— 从视图流式重建出来的缓存文件，其指纹天然等于
    视图指纹，令牌可证地成立；
  · 若对不上，说明视图与总表顺序/内容有偏移，那就绝不能拿视图当总表来源，
    只能老实做一次全量物化。此时这个方向整体作废，改走「预热一次性落地」。

内存必须全程有界：逐行读、算完即丢，绝不做整体物化（本机空闲仅几十 MB）。
"""
import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "b2bcollect_build"))

from pool_store import FingerprintAccumulator  # noqa: E402  口径必须用产品实现

DB = os.path.expanduser("~/Desktop/数据池缓存/pool_store.db")


def rss_mb() -> int:
    """当前进程常驻内存（MB），用来证明整轮没有物化。"""
    try:
        import resource
        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss >> 20)
    except Exception:
        return -1


def main() -> int:
    t0 = time.time()
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    meta = {str(k): str(v) for k, v in con.execute("SELECT k, v FROM meta")}
    print(f"meta: {json.dumps(meta, ensure_ascii=False)}")

    want_count = int(meta.get("source_count", "-1"))
    want_fp = int(meta.get("fingerprint", "-1"))

    acc = FingerprintAccumulator()
    bad_payload = 0
    n = 0
    cur = con.execute("SELECT payload FROM company ORDER BY id")
    while True:
        rows = cur.fetchmany(2000)          # 分批取，绝不一把 fetchall
        if not rows:
            break
        for r in rows:
            n += 1
            try:
                rec = json.loads(r["payload"])
            except Exception:
                bad_payload += 1
                rec = None
            acc.feed(rec)                   # 非 dict 会被跳过（与产品同口径）
        if n % 100000 == 0:
            print(f"  已流式核对 {n:,} 条  RSS={rss_mb()}MB  "
                  f"{time.time() - t0:.1f}s", flush=True)

    count, crc = acc.finish()
    print(f"\n视图行数        : {n:,}")
    print(f"payload 解析失败 : {bad_payload}")
    print(f"流式重算指纹      : ({count:,}, {crc})")
    print(f"meta 记录指纹     : ({want_count:,}, {want_fp})")
    print(f"耗时              : {time.time() - t0:.1f}s   峰值 RSS={rss_mb()}MB")

    ok = (n == want_count == count and crc == want_fp)
    print("\n结论：", "✅ 视图可作总表来源（逐位相同）" if ok
          else "❌ 视图与总表指纹不同源，禁止用视图重建总表")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
