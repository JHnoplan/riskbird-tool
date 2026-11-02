# scrape_solarbe_full.py —— 索比光伏网企业库全量采集（独立脚本，不进工具）
#
# 背景：2026-11-02 实测空关键词全站共 13285 家（search.php?kw=&catid=0&areaid=0），
# 但本站任意切片最多只让翻 6 页（120 家），且省以下地区维度不存在、VIP/经营模式等
# 参数实测不改变结果——唯一可靠的三级细分手段是「关键词嵌套」(kw=电池 能把
# 光伏原材料×江苏 从 495 压到 122)。本脚本按下面的策略把全库捞干净：
#
#   切片队列：初始 = 行业catid(16) × 地区areaid(35)（小行业不再切地区）
#   每一片：kw 从空开始，翻满 6 页拿到手；若标称数 > 到手数 → 从本片已见条目的
#           「公司名称+主营产品」里提取高频词做子关键词，逐个入队再切一刀；
#           子片同样「翻满→超限再细分」（深度封顶，防无限递归）。
#
# 数据链路完全复用 b2bcollect_build/solarbe_crawler.py 的解析与清洗（手机版 UA
# 拿明文电话/邮箱、赌博号过滤、假号作废等，全部有 debug/solarbe_offline_test.py
# 的真实页面断言守护），本脚本只负责「枚举全库 + 断点续采 + 落盘导出」。
#
# 用法（务必后台跑，全程约 8~12 小时）：
#   nohup .venv/bin/python debug/scrape_solarbe_full.py > debug/solarbe_full_run.log 2>&1 &
#   中断随时可再跑同一条命令，进度从 solarbe_full_state.json 自动续。
# 产出（都在 采集的数据汇总/，文件名带首次启动日期）：
#   · 索比光伏网全量_日期.json        —— 逐家明细（含状态列，原始全量不删行）
#   · 索比光伏网全量_日期.xlsx        —— 每 10 分钟覆盖式导出（原始表 + 清洗入库表两个 sheet）
#   · solarbe_full_state.json         —— 断点台账（切片队列/已见slug/在采片）
import sys, os, json, re, time, random, pathlib
from collections import deque, Counter
from urllib.parse import quote

sys.path.insert(0, "/Users/jhnoplan/Desktop/PythonDemo/b2bcollect_build")
from solarbe_crawler import SolarbeCrawler, SEARCH_URL, CATIDS, AREAIDS   # noqa: E402

ROOT = pathlib.Path("/Users/jhnoplan/Desktop/PythonDemo")
OUT_DIR = ROOT / "采集的数据汇总"
DAY = time.strftime("%Y%m%d")
STATE_FILE = ROOT / "debug" / "solarbe_full_state.json"
DETAIL_JSON = OUT_DIR / f"索比光伏网全量_{DAY}.json"
EXPORT_XLSX = OUT_DIR / f"索比光伏网全量_{DAY}.xlsx"
EXPORT_EVERY = 600            # 每 10 分钟覆盖式导出一份 Excel
# 关键词细分：每片最多试几个高频子词、最深切几层（防长尾词无穷展开）
MAX_SUB_KW = 8
MAX_KW_DEPTH = 2
# 实测教训：滑窗分词会被「有限公司/科技有限」这类后缀碎片主导（kw=有限 440 家几乎
# 没压小）。子词只从「主营产品」取业务实词，公司名只取品牌段（剔掉常见后缀后），
# 且入队前要真探一刀验证能压小，探不动就不切。
_STOP_WORDS = {"有限", "公司", "集团", "股份", "科技", "技术", "贸易", "实业",
               "设备", "工程", "产品", "太阳能", "光伏", "新能源", "有限公司",
               "专业", "生产", "加工", "销售", "定制", "系列", "主要", "经营"}

c = SolarbeCrawler()
c.incremental = False          # 台账走自己的 state 文件，不用工具的种子库


# ── 断点台账 ──────────────────────────────────────────────────
def load_state():
    if STATE_FILE.exists():
        st = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        print(f"续跑：台账里有 {len(st['records'])} 家明细，队列剩 {len(st['queue'])} 片")
        return st
    q = deque()
    for cat in [0] + list(CATIDS):
        q.append(["", cat, 0, 0])           # 全库片 + 行业整片（能一片到底就不切）
    return {"queue": list(q), "seen_slugs": [], "records": {}, "splits": 0,
            "splits_probe": 0, "started": time.strftime("%F %T"), "list_req": 0, "detail_req": 0}


def save_state(st):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, STATE_FILE)            # 原子替换：强杀不会写坏台账


# ── 关键词细分：从本片条目身上提高频词 ────────────────────────
def sub_keywords(entries):
    """子词候选：主营产品按分隔符切成业务词（硅料/银浆/ EVA 胶膜…）；
    公司名只取品牌段（剪掉地名与后缀词）。按覆盖面降序出候选。"""
    cnt = Counter()
    for e in entries:
        prod = e.get("主营产品", "") or ""
        for seg in re.split(r"[^a-zA-Z\u4e00-\u9fa5]+", prod):
            if 2 <= len(seg) <= 8 and seg not in _STOP_WORDS:
                cnt[seg] += 1
        name = e.get("公司名称", "") or ""
        brand = re.sub(r"(有限责任公司|股份有限公司|有限公司|集团|公司|厂)$", "", name)
        brand = re.sub(r"^(北京|上海|天津|重庆|中国|国际)", "", brand)
        brand = re.sub(r"(科技|技术|贸易|实业|新能源|光伏|太阳能)+$", "", brand)
        if 2 <= len(brand) <= 8 and brand not in _STOP_WORDS:
            cnt[brand] += 1
    need = max(6, len(entries) // 8)
    return [w for w, k in cnt.most_common(40) if k >= min(need, 10)][:MAX_SUB_KW]


# ── 一片（kw×catid×areaid）翻满 6 页 ─────────────────────────
PAGE_CAP = 6                   # 站点硬上限：第 7 页起与第 6 页逐字相同


def crawl_slice(st, kw, catid, areaid, depth):
    """翻满本片并把每家排进详情采集。返回 (标称总数, 本片到手条目数, 本片条目列表)。
    整片拿不到页（看门狗也没能等回放行）时返回 total=None，调用方把本片重新入队。"""
    url0 = f"{SEARCH_URL}?kw={quote(kw)}&catid={catid}&areaid={areaid}&page=1"
    html0 = c._fetch(url0)
    st["list_req"] += 1
    if not html0:
        return None, 0, []
    entries, sig_seen = [], set()
    total, prev = c.slice_total(html0), None
    got0 = c.parse_list(html0, kw)
    for page in range(2, PAGE_CAP + 1):
        url = f"{SEARCH_URL}?kw={quote(kw)}&catid={catid}&areaid={areaid}&page={page}"
        html = c._fetch(url)                 # guarded：空白页限流自动交看门狗等放行
        st["list_req"] += 1
        got = c.parse_list(html, kw) if html else []
        sig = tuple(sorted(e["公司ID"] for e in got))
        if got and sig == prev:              # 内容与前页逐字相同＝触顶假翻页，停
            break
        prev = sig
        fresh = [e for e in got if e["公司ID"] not in sig_seen]
        for e in fresh:
            sig_seen.add(e["公司ID"])
        entries.extend(fresh)
        if html:
            total = max(total, c.slice_total(html))
        if len(fresh) == 0 and page >= 3:    # 翻页不再产出新条目＝到底
            break
    # 翻页间不另加睡：本站已开 PER_REQUEST_PACE，_fetch 每请求前自动对齐 2s 实测安全节奏
    # 第 1 页也并入 entries/sig_seen（上面只探了标称数）
    fresh0 = [e for e in got0 if e["公司ID"] not in sig_seen]
    for e in fresh0:
        sig_seen.add(e["公司ID"])
    entries = fresh0 + entries
    return total, len(entries), entries


def probe_total(kw, catid, areaid):
    """只探第 1 页拿标称数（细分入队前验证这刀切得动，不白切）。"""
    url = f"{SEARCH_URL}?kw={quote(kw)}&catid={catid}&areaid={areaid}&page=1"
    html = c._fetch(url)
    return c.slice_total(html) if html else None


def enqueue_details(st, entries):
    """把列表条目排队进详情采集（按 slug 全局去重，重复主体绝不访问第二次）。"""
    added = 0
    for e in entries:
        slug = e["公司ID"]
        if slug in st["seen_set"]:
            continue
        st["seen_set"].add(slug)
        st["seen_slugs"].append(slug)
        st["records"][slug] = {"stub": e}    # 先存列表字段，详情采完覆盖
        added += 1
    return added


def process_one(st, slug):
    rec_entry = st["records"][slug]
    if rec_entry.get("done"):
        return
    stub = rec_entry["stub"]
    try:
        rec = c.crawl_detail(stub)
    except Exception as e:
        rec = dict(stub)
        rec["状态"] = "详情页导航失败"
        print(f"  ! 详情异常 {slug}: {str(e).splitlines()[0][:60]}", flush=True)
    rec_entry["detail"] = rec
    # 空正文/导航失败属瞬时状态：不标 done，收尾还会重采一轮
    rec_entry["done"] = rec.get("状态") != "详情页导航失败"
    st["detail_req"] += 1
    # 详情间距同样交给 PER_REQUEST_PACE，不在这里叠睡


# ── 导出 ──────────────────────────────────────────────────────
COLS = ["公司名称", "联系人", "岗位", "手机", "电话", "邮箱", "QQ", "微信",
        "地址", "所在区域", "公司网址", "主营产品", "经营模式", "详情URL", "状态"]


def flatten(st):
    rows = []
    for slug in st["seen_slugs"]:
        v = st["records"][slug]
        rec = v.get("detail") or v["stub"]
        row = {k: rec.get(k, "") for k in COLS}
        row["数据来源"] = "索比光伏网"
        rows.append(row)
    return rows


def export_excel(st):
    rows = flatten(st)
    try:
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "原始全量"
        head = ["数据来源"] + COLS
        ws.append(head)
        for r in rows:
            ws.append([r[k] for k in head])
        # 清洗入库表：只剔「手机/固话/邮箱/微信」四项全空的行（口径与客户库一致），其余全保留
        ws2 = wb.create_sheet("清洗后入库")
        ws2.append(head)
        kept = 0
        for r in rows:
            if not (r["手机"] or r["电话"] or r["邮箱"] or r["微信"]):
                continue
            ws2.append([r[k] for k in head])
            kept += 1
        DETAIL_JSON.write_text(json.dumps(rows, ensure_ascii=False, indent=1),
                               encoding="utf-8")
        tmp = EXPORT_XLSX.with_suffix(".tmp")
        wb.save(tmp)
        os.replace(tmp, EXPORT_XLSX)
        return len(rows), kept
    except Exception as e:
        print(f"  ! 导出失败（下轮再试）：{e}", flush=True)
        return len(rows), -1


# ── 主流程 ────────────────────────────────────────────────────
def main():
    st = load_state()
    st["seen_set"] = set(st["seen_slugs"])
    queue = deque(st["queue"])
    OUT_DIR.mkdir(exist_ok=True)
    t0 = last_export = time.time()
    print(f"开跑：目标全库 13285 家；台账已见 {len(st['seen_slugs'])} 家", flush=True)

    # 阶段一：枚举全库（切片翻满→超限先探一刀验证切得动再按高频词细分）
    while queue:
        item = queue.popleft()
        kw, catid, areaid, depth = item[:4]
        retried = len(item) > 4              # 尾部带 1 ＝整片失败后唯一一次重排
        total, got, entries = crawl_slice(st, kw, catid, areaid, depth)
        if total is None:
            if not retried:
                queue.append([kw, catid, areaid, depth, 1])
                print(f"[枚举] 整片拿不到页，稍后重试一次：kw={kw} catid={catid} areaid={areaid}", flush=True)
            else:
                print(f"  ⚠ 重试仍失败，放弃本片：kw={kw} catid={catid} areaid={areaid}", flush=True)
            st["queue"] = list(queue)
            continue
        added = enqueue_details(st, entries)
        piece = f"kw={kw or '(全)'} catid={CATIDS.get(catid, '全')} areaid={AREAIDS.get(areaid, '全部')}"
        print(f"[枚举] {piece} 标称{total} 到手{got} 新增{added} 队列剩{len(queue)} "
              f"累计{len(st['seen_slugs'])}家", flush=True)
        if total > got and depth < MAX_KW_DEPTH:
            used = []
            for w in sub_keywords(entries)[:MAX_SUB_KW]:
                if w == kw:
                    continue
                pt = probe_total(w, catid, areaid)
                st["splits_probe"] = st.get("splits_probe", 0) + 1
                if pt is not None and pt < got:      # 探得动：这子词确实能切出一块
                    used.append(w)
                    if len(used) >= 6:
                        break
            if used:
                st["splits"] += 1
                for w in used:
                    queue.append([w, catid, areaid, depth + 1])
                print(f"  ↳ 超限细分（验切得动）：{used}", flush=True)
            else:
                print(f"  ⚠ {piece} 仍超 120 且切不动（深度{depth}），差 {total - got} 家", flush=True)
        st["queue"] = list(queue)
        if time.time() - last_export > EXPORT_EVERY:
            n, k = export_excel(st)
            save_state(st)
            print(f"[导出] 明细 {n} 行（入库 {k}）→ {EXPORT_XLSX.name}", flush=True)
            last_export = time.time()
    st["queue"] = []
    save_state(st)
    print(f"\n枚举完成：共 {len(st['seen_slugs'])} 家（列表请求 {st['list_req']} 个，"
          f"细分 {st['splits']} 刀，耗时 {(time.time()-t0)/60:.1f} 分钟）\n", flush=True)

    # 阶段二：逐家进详情（slug 全局去重；瞬时失败的不标 done）
    todo = [s for s in st["seen_slugs"] if not st["records"][s].get("done")]
    print(f"详情采集：待采 {len(todo)} 家", flush=True)
    t1 = last_export = time.time()
    for i, slug in enumerate(todo, 1):
        process_one(st, slug)
        if i % 50 == 0:
            save_state(st)
        if time.time() - last_export > EXPORT_EVERY:
            n, k = export_excel(st)
            save_state(st)
            print(f"[{i}/{len(todo)}] 导出 {n} 行（入库 {k}）", flush=True)
            last_export = time.time()
    # 收尾：把仍判“导航失败”的重采一轮（多为偶发空正文）
    retry = [s for s in st["seen_slugs"] if not st["records"][s].get("done")]
    if retry:
        print(f"\n收尾重采 {len(retry)} 家（上轮空正文/导航失败）", flush=True)
        for s in retry:
            process_one(st, s)
    n, k = export_excel(st)
    save_state(st)
    dt = (time.time() - t1) / 60
    print(f"\n全部完成：明细 {n} 行、入库口径 {k} 行，详情耗时 {dt:.1f} 分钟", flush=True)
    print(f"Excel: {EXPORT_XLSX}\nJSON: {DETAIL_JSON}", flush=True)


if __name__ == "__main__":
    main()
