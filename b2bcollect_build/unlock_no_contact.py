# -*- coding: utf-8 -*-
"""unlock_no_contact.py —— 按「企业数据_原始.xlsx」解锁增量种子库里缺联系人的历史记录。

为什么需要它：种子库把「成功」的记录当终态锁死（命中即跳过、永不再访详情页），
而早期版本的解析器取不到联系人（gys 逐行正则匹配不到拆节点的 <dt>联系人</dt><dd>，
b2b168 只访商铺首页没访 contact.aspx），于是那批「有号码没联系人」的公司被永久锁成
空联系人——重跑再多次也不会补。基类后来加了 ct/rt 自愈（新写入的条目缺联系人只允许
再采 2 次），但历史条目没这个标记，只能按最新原始表定位后一次性解锁。

用法（务必先停止采集，否则运行中的进程结束时会把整份种子库覆写回去）：
    python3 unlock_no_contact.py                     # 自动取任务索引里最新的原始表
    python3 unlock_no_contact.py --raw <原始xlsx路径> --sites gys,b2b168 --apply

默认干跑（只报告会解锁多少，不改文件），加 --apply 才真正写入，并先备份 .bak_unlock。
"""
import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "b2bcollect_data"
TASK_ROOT = Path("/Users/jhnoplan/Desktop/B2B三站企业数据自动采集工具")
INDEX_FILE = TASK_ROOT / "_采集任务索引.json"

# 原始表「数据来源」→ 站点 PLATFORM_SLUG（种子库文件名 seen_store_<slug>.json）
SITE_BY_SOURCE = {
    "供应商网": "gys",
    "八方资源网": "b2b168",
    "顺企网": "11467",
    "爱采购": "aipage",
    "中国制造网": "mic",
}
EMPTY = {"", "nan", "none", "null", "-", "无", "暂无", "未提供", "未公开", "不详"}


def norm_name(name):
    """与 cdp_base._norm_name 完全一致，否则算不出种子库里的 name 键。"""
    return re.sub(r"[\s（）()・.、，,]+", "", name or "")


def latest_raw_file():
    """任务索引里 finished_at 为空/最新的那条 raw_file；退而求其次按目录名排序取最新。"""
    cand = []
    try:
        tasks = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        for t in tasks:
            f = t.get("raw_file")
            if f and Path(f).exists():
                cand.append((t.get("started_at", ""), Path(f)))
    except Exception:
        pass
    if cand:
        return sorted(cand)[-1][1]
    dirs = sorted(p for p in TASK_ROOT.glob("*/企业数据_原始.xlsx"))
    return dirs[-1] if dirs else None


def keys_for(site_slug, row):
    """一家公司在种子库里的全部去重键（与 cdp_base._rec_seen_keys 同口径）。"""
    keys = []
    mid = str(row.get("memberId") or "").strip()
    if mid and mid.lower() != "nan":
        keys.append(f"{site_slug}:{mid}")
    name = norm_name(str(row.get("公司名称") or ""))
    if name:
        keys.append(f"{site_slug}:name:{name}")
    for col in ("详情URL", "公司网址"):
        url = str(row.get(col) or "").strip()
        if url and url.lower() != "nan":
            keys.append(f"{site_slug}:url:{url}")
    return keys


def match_drop_keys(store, site_slug, rows):
    """算出该站哪些种子库条目对应「缺联系人」的公司，返回键列表。
    除精确键外还做名称包包含匹配：库里可能存「公司全名 + 主营尾巴」（gys 列表
    锚文本就带尾巴），也可能只有详情页标题里的规范名；漏解 name 键时列表页仍会
    命中它，这家公司就永远不会被重采。"""
    exact, names = set(), []
    for _, row in rows.iterrows():
        exact.update(keys_for(site_slug, row))
        n = norm_name(str(row.get("公司名称") or ""))
        if len(n) >= 6:
            names.append(n)
    hit = [k for k in exact if k in store]
    prefix = f"{site_slug}:name:"
    for k in store:
        if k in hit or not k.startswith(prefix):
            continue
        stored = k[len(prefix):]
        if len(stored) < 6:
            continue
        if any(stored in n or n in stored for n in names):
            hit.append(k)
    return hit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="", help="企业数据_原始.xlsx 路径（默认取最新任务）")
    ap.add_argument("--sites", default=",".join(sorted(set(SITE_BY_SOURCE.values()))),
                    help="只处理这些站点，逗号分隔（如 gys,b2b168）")
    ap.add_argument("--apply", action="store_true", help="真正写入种子库（默认只干跑报告）")
    args = ap.parse_args()

    raw_path = Path(args.raw) if args.raw else latest_raw_file()
    if not raw_path or not raw_path.exists():
        print("找不到原始表（企业数据_原始.xlsx），请用 --raw 指定")
        return 1
    raw = pd.read_excel(raw_path)
    raw.columns = [str(c).strip() for c in raw.columns]
    if "联系人" not in raw.columns or "数据来源" not in raw.columns:
        print(f"原始表缺必要列：{list(raw.columns)}")
        return 1
    print(f"原始表：{raw_path}（{len(raw)} 行）")

    want = {s.strip() for s in args.sites.split(",") if s.strip()}
    blank = raw["联系人"].isna() | raw["联系人"].astype(str).str.strip().str.lower().isin(EMPTY)
    todo = {}
    for site, slug in SITE_BY_SOURCE.items():
        if slug not in want:
            continue
        g = raw[(raw["数据来源"].astype(str).str.strip() == site) & blank]
        if len(g):
            todo[slug] = g
    if not todo:
        print("没有需要解锁的记录")
        return 0

    total_removed = 0
    for slug, g in todo.items():
        path = DATA_DIR / f"seen_store_{slug}.json"
        if not path.exists():
            print(f"[{slug}] 种子库不存在：{path}")
            continue
        store = json.loads(path.read_text(encoding="utf-8"))
        hit = match_drop_keys(store, slug, g)
        locked_ct = sum(1 for v in store.values() if isinstance(v, dict) and "ct" in v)
        print(f"[{slug}] 缺联系人 {len(g)} 家 → 命中种子库键 {len(hit)} 个"
              f"（库内共 {len(store)} 键，其中 {locked_ct} 个带自愈标记）")
        if not hit or not args.apply:
            continue
        shutil.copy2(path, path.with_name(path.name + f".bak_unlock.{time.strftime('%Y%m%d_%H%M%S')}"))
        for k in hit:
            store.pop(k)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        total_removed += len(hit)
    print(("已解锁 %d 个键，下次采集会重新访问这些公司详情页补联系人" % total_removed)
          if args.apply else "（干跑：加 --apply 才会真正写入）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
