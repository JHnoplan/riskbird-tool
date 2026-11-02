# scrape_solarbe_full.py —— 索比光伏网企业库全量采集（独立脚本，不进工具）
#
# 站点实测（2026-11-02，全部有 debug/probe_solarbe_*.py 的真实请求输出为证）：
#   · 全库 13285 家：search.php?kw=&catid=0&areaid=0 页头「共找到 13285 条」。
#   · **任意切片最多只让翻 6 页（120 家）**：第 7 页起与第 6 页逐字相同（走过 8/20/100/300/665 页）。
#   · 可用筛选维（改一个就换一批结果，标称数实测）：
#       地区树 areaid   省 35 个求和 13232（缺口 53）；省下还有市（广东 21 市求和 6683/6913），
#                      市下偶有区 → 覆盖最好的维度，所以细分优先级第一
#       注册资本        mincapital/maxcapital：**站点是「min < 注册资本 < max」严格开区间，
#                      且 min/max 传 0 等于那一侧不限**（debug/probe_solarbe_capfix.py 实测）。
#                      注册资本都记整数（万元），没填的记 0（全库 5187 家，所以 0 下界天然
#                      把它们接住）。档位分界必须用 x.5：用整数分界（0/50/100/300/500/1000/5000）
#                      会把「正好 50 万 / 100 万 / 1000 万」这些最扎堆的整数从所有档位里漏掉——
#                      实测整数档求和只有 10823 家，x.5 档求和 13280 家（全库 13285）。
#       行业树 catid    一级 15 个求和 9949（有 25% 的公司没挂分类）；分类还有二三级子项
#                      （深圳更极端：15 个一级分类只求和 501 家，92% 没挂行业）
#       企业类型 type   1-6 求和 7674      经营模式 mode 1-4 求和 5116      企业规模 size 1-5 求和 3382
#       关键词 kw       全站是分词索引（kw=有限 只有 440），只能当兜底维度
#     排序参数 order/by 实测被站点忽略；vip=N、day=N、grade、is_login、letter、thumb(只有=1
#     有效)、offset/start/itemid 也不改变结果。手机 UA 模板同受 6 页限制（page=6.5/74.5 与
#     第 6 页逐字相同，debug/probe_solarbe_pagefloat.py），整站也没 sitemap（robots.txt 挂的
#     4 条 Sitemap 全是资讯页），所以「一片最多 120 家」没有任何绕法。
#
# 采集策略＝「按维度覆盖率的自适应切片」：每一片老老实实翻满 6 页；标称数还是被
# 120 截断，就顺着「地区树 → 注册资本值段 → 行业树 → 企业类型 → 经营模式 → 企业规模
# → 关键词」的顺序再切一刀；一轮队列跑完后再补漏——凡是「名下到手（子树并集）< 标称」的片，
# 换下一个还没用过的维度继续切（不同维度各有盲区：没挂分类的公司能靠地区/资本捞回来，
# 没填注册资本的靠分类捞回来），直到没有增量或触到请求量上限。
#
# 数据链路完全复用 b2bcollect_build/solarbe_crawler.py 的解析与清洗（手机版 UA
# 拿明文电话/邮箱、赌博号过滤、假号作废等，全部有 debug/solarbe_offline_test.py
# 的真实页面断言守护），本脚本只负责「枚举全库 + 断点续采 + 落盘导出」。
#
# 用法（务必后台跑）：
#   nohup .venv/bin/python -u debug/scrape_solarbe_full.py > debug/solarbe_full_run.log 2>&1 &
#   中断随时再跑同一条命令，进度从 debug/solarbe_full_state.json 自动续（每片都落盘）。
#   带一个 areaid 参数＝只试采那个地区片（小流量验证切片策略，台账/产出另外存）：
#   .venv/bin/python -u debug/scrape_solarbe_full.py 10414
# 产出（都在 采集的数据汇总/，文件名带首次启动日期）：
#   · 索比光伏网全量_日期.json        —— 逐家明细（含状态列，原始全量不删行）
#   · 索比光伏网全量_日期.xlsx        —— 每 10 分钟覆盖式导出（原始表 + 清洗入库表两个 sheet）
import sys, os, json, re, time, pathlib
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

PAGE_CAP = 6                  # 站点硬上限：第 7 页起与第 6 页逐字相同
STATE_VERSION = 4
MAX_DEPTH = 9                 # 切片嵌套层数上限：全库路径「根→省→市→区」已经吃掉 3 层，
                              # 深圳这种单城 6152 家的片还要靠资本二分+行业细分好几层，
                              # 留太浅会在还有维度可切的时候就被层数卡住（收口报告里
                              # 剩下的缺口全是这种片）；真正的失控防线是 MAX_LIST_REQ。
MAX_ROUNDS = 6                # 补漏轮数：每轮把「到手 < 标称」的片换个维度再切
MAX_LIST_REQ = 30000          # 列表请求总量兜底（防维度组合失控把一轮拖没完）
MAX_CHILDREN = 40             # 单片单次细分出的子片数上限
# 细分维度优先级：覆盖率高、能连续二分的最优先。地区树 13232/13285，注册资本 x.5 档
# 13280/13285（还能源源不断二分），两者加起来几乎无盲区；一级行业只求和 9949（深圳这种
# 城市只求和 501），所以排第三；企业类型/经营模式/规模只当兜底。
DIMS = ("area", "cap", "cat", "type", "mode", "size", "kw")
FLAT_VALUES = {"type": (1, 2, 3, 4, 5, 6), "mode": (1, 2, 3, 4), "size": (1, 2, 3, 4, 5)}
# 注册资本档位：上界都用 x.5（站点开区间，整数分界会漏掉「正好这个整数」那一坨）。
# 实测各档家数：0.5 以下（没填）5187、(0.5,50.5) 1896、(50.5,100.5) 1947、
#   (100.5,300.5) 1137、(300.5,500.5) 1080、(500.5,1000.5) 888、(1000.5,5000.5) 761、
#   5000 万以上 384，求和 13280。下界 0 在站点嘴里＝不限，正好把「没填＝0」那批接住。
CAP_INF = 1000000.5           # maxcapital 传 0 等于不限，所以「上不封顶」要填个大数
CAP_SYM = {CAP_INF: "∞"}
CAP_LADDER = [(0, 0.5), (0.5, 50.5), (50.5, 100.5), (100.5, 300.5), (300.5, 500.5),
              (500.5, 1000.5), (1000.5, 2000.5), (2000.5, 5000.5), (5000.5, 10000.5),
              (10000.5, 50000.5), (50000.5, CAP_INF)]
MAX_SUB_KW = 8
KW_MIN_COVER = 10             # 高频子词至少要出现在这么多家才值得切一刀
# 实测教训：滑窗分词会被「有限公司/科技有限」这类后缀碎片主导（kw=有限 440 家几乎
# 没压小）。子词只从「主营产品」取业务实词，公司名只取品牌段（剔掉常见后缀后），
# 且入队前要真探一刀验证能压小，探不动就不切。
_STOP_WORDS = {"有限", "公司", "集团", "股份", "科技", "技术", "贸易", "实业",
               "设备", "工程", "产品", "太阳能", "光伏", "新能源", "有限公司",
               "专业", "生产", "加工", "销售", "定制", "系列", "主要", "经营"}

c = SolarbeCrawler()
c.incremental = False          # 台账走自己的 state 文件，不用工具的种子库

# 小流量试跑：`... scrape_solarbe_full.py 10414` 只采「深圳」这一片（台账/产出另外存，
# 不污染全量台账），用来验切片策略在最大城片上能不能把 6152 家接近拿全。
SEED_AREAID = None


def init_paths():
    global STATE_FILE, DETAIL_JSON, EXPORT_XLSX
    if SEED_AREAID is None:
        return
    STATE_FILE = ROOT / "debug" / f"solarbe_full_state_{SEED_AREAID}.json"
    DETAIL_JSON = OUT_DIR / f"索比光伏网试采{SEED_AREAID}_{DAY}.json"
    EXPORT_XLSX = OUT_DIR / f"索比光伏网试采{SEED_AREAID}_{DAY}.xlsx"


# ── 切片：一个片 = 关键词 + 已固定的筛选维（没固定的用 0/"" 表示「不限」）──
def new_slice(kw="", catid=0, areaid=0, mincap=None, maxcap=None,
              type_=0, mode=0, size=0):
    return {"kw": kw, "catid": int(catid), "areaid": int(areaid),
            "mincapital": mincap, "maxcapital": maxcap,
            "type": int(type_), "mode": int(mode), "size": int(size)}


def cap_mid(lo, hi):
    """资本区间二分点：切点必须落在 x.5 上（站点是开区间，切点若是整数，「正好这个整数」
    那一坨会被两侧同时排掉——这正是原来整数档位漏掉 2457 家的同一个坑）。取最靠近中点的
    .5 档；落不进开区间＝这一档只装得下一个整数，切不动了，返回 None。"""
    mid = int((lo + hi) / 2) + 0.5
    return mid if lo < mid < hi else None


def skey(f):
    return json.dumps(f, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def surl(f, page):
    q = f"kw={quote(f['kw'] or '')}&catid={f['catid']}&areaid={f['areaid']}"
    if f["mincapital"] is not None:
        q += f"&mincapital={f['mincapital']}&maxcapital={f['maxcapital']}"
    for d in ("type", "mode", "size"):
        if f[d]:
            q += f"&{d}={f[d]}"
    return f"{SEARCH_URL}?{q}&page={page}"


def sdesc(st, f):
    cat = (CATIDS.get(f["catid"]) or _tree_name(st["tree"]["cat"], f["catid"])
           or ("全行业" if not f["catid"] else f"行业{f['catid']}"))
    area = (AREAIDS.get(f["areaid"]) or _tree_name(st["tree"]["area"], f["areaid"])
            or ("全地区" if not f["areaid"] else f"地区{f['areaid']}"))
    extra = []
    if f["mincapital"] is not None:
        hi = f["maxcapital"]
        extra.append(f"资本{f['mincapital'] or 0}-{CAP_SYM.get(hi, hi)}")
    for d, names in (("type", {1: "私营", 2: "国营", 3: "民营", 4: "外商独资", 5: "三资", 6: "其他"}),
                     ("mode", {1: "生产型", 2: "贸易型", 3: "服务型", 4: "其他机构"}),
                     ("size", {1: "20人以下", 2: "20-100人", 3: "100-500人", 4: "500-1000人", 5: "1000人以上"})):
        if f[d]:
            extra.append(f"{names.get(f[d], d)}")
    return (f"kw={f['kw'] or '(全)'} {cat} {area}" + (" " + " ".join(extra) if extra else "")).strip()


_OPT_RE = re.compile(r'<option value="(\d+)"[^>]*>\s*([^<]{0,24}?)\s*</option>', re.S)


def _tree_name(mapping, vid):
    """日志里把市/区、二三级行业的 id 译成中文（这些子项是从筛选栏现场抄回来的）。"""
    for kids in mapping.values():
        for k, name in kids:
            if int(k) == int(vid):
                return name
    return ""


def harvest_tree(html):
    """从筛选栏的下拉里顺手把「地区树/行业树」的子项抄回来（第一项＝当前父级，其余是子级）。"""
    area, cat = {}, {}
    for m in re.finditer(r'<select\b([^>]*)>(.*?)</select>', html or "", re.S):
        opts = _OPT_RE.findall(m.group(2))
        if len(opts) < 2:
            continue
        attrs = m.group(1)
        if "load_area" in attrs:
            area[opts[0][0]] = [[v, t.strip()] for v, t in opts[1:]]
        elif "load_category" in attrs:
            cat[opts[0][0]] = [[v, t.strip()] for v, t in opts[1:]]
    return area, cat


# ── 断点台账 ──────────────────────────────────────────────────
def load_state():
    if STATE_FILE.exists():
        st = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if st.get("v") != STATE_VERSION:
            print(f"台账版本不符（{st.get('v')}≠{STATE_VERSION}），本次按全新开跑；"
                  f"旧台账已另存 {STATE_FILE.name}.old")
            os.replace(STATE_FILE, pathlib.Path(str(STATE_FILE) + ".old"))
        else:
            print(f"续跑：台账 {len(st['records'])} 家明细，队列剩 {len(st['queue'])} 片，"
                  f"切片记录 {len(st['slices'])} 条")
            return st
    root = new_slice(areaid=SEED_AREAID or 0)
    return {"v": STATE_VERSION, "started": time.strftime("%F %T"),
            "queue": [skey(root)], "seen_slugs": [], "records": {},
            "slices": {skey(root): {"f": root, "parent": None, "depth": 0, "used": [],
                                    "total": None, "own": 0}},
            "tree": {"area": {}, "cat": {}},
            "list_req": 0, "detail_req": 0, "rounds": 0}


def save_state(st):
    tmp = STATE_FILE.with_suffix(".tmp")
    # seen_set 是运行期的内存去重集（load_state 时从 seen_slugs 重建），不进台账
    payload = {k: v for k, v in st.items() if k != "seen_set"}
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, STATE_FILE)            # 原子替换：强杀不会写坏台账


# 已细分过的片，名下到手要按「子树并集」算（不同维度的子片会重叠：同一个公司既在
# 深圳×行业 里、又在深圳×资本里），所以给这些片留一个 slug 集。没细分过的片 own 就是
# 本片列出的家数，不需要集，台账也不会因它膨胀。
LISTED = {}


def rebuild_listed(st):
    n = 0
    for key, meta in st["slices"].items():
        if meta.get("listed"):
            LISTED[key] = set(meta["listed"])
            n += 1
    return n


def record_listing(st, key, slugs):
    """本片列出 slugs → 先给自己记账，再沿父链把已细分过的父片的并集补上。"""
    chain, k, hops = [], key, 0
    while k and hops <= MAX_DEPTH + 2:
        m = st["slices"].get(k)
        if not m:
            break
        chain.append(m)
        k, hops = m.get("parent"), hops + 1
    if not chain:
        return
    if not chain[0].get("used"):
        chain[0]["own"] = len(slugs)          # 没细分的片：own 就是本片列出的家数
    for m in chain:
        if not m.get("used"):
            continue
        kk = skey(m["f"])
        s = LISTED.setdefault(kk, set())
        new = [x for x in slugs if x not in s]
        if new:
            s.update(new)
            m.setdefault("listed", []).extend(new)
        m["own"] = len(s)


# ── 关键词细分：只在别的维度都切不动时才用（兜底维）─────
# 实测（probe_solarbe_kw.py）：深圳「注册资本正好 200 万」那 403 家里，地区只剩一个市、
# 行业只挂上 8%、type/mode/size 只填了 3~4%，只能靠 kw 的全文索引偷一点：10 个词探下来
# 只多带回 18 家。所以 kw 只给「切不动的大片」兼底，且子词先从本片条目身上抽，
# 抽不出来（这些公司大多连主营产品都没填）再用下面这批工商常用词。
_VOCAB = ("科技", "电子", "新能源", "太阳能", "光伏", "设备", "电池", "组件",
          "逆变器", "照明", "电气", "电源", "控制器", "材料", "仪器", "系统",
          "工程", "贸易", "技术", "节能")


def sub_keywords(entries):
    """子词候选：主营产品按分隔符切成业务词（硅料/银浆/EVA 胶膜…）；
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
    words = [w for w, k in cnt.most_common(60) if k >= KW_MIN_COVER][:MAX_SUB_KW]
    for w in _VOCAB:                       # 补上工商常用词（本片抽不出词时也有东西可试）
        if w not in words:
            words.append(w)
    return words[:MAX_SUB_KW * 2]


def probe_total(st, f):
    """只探第 1 页拿标称数（细分入队前验证这刀切得动，不白切）。"""
    html = c._fetch(surl(f, 1))
    st["list_req"] += 1
    return c.slice_total(html) if html else None


# ── 一片翻满（最多 6 页）───────────────────────────────────────
def crawl_slice(st, f, entries_out):
    """返回本片标称总数；整片第 1 页都拿不到（看门狗也没等回放行）时返回 None，调用方重排一次。"""
    html0 = c._fetch(surl(f, 1))
    st["list_req"] += 1
    if not html0:
        return None
    a, cat = harvest_tree(html0)
    for k, v in a.items():
        st["tree"]["area"].setdefault(k, v)
    for k, v in cat.items():
        st["tree"]["cat"].setdefault(k, v)
    total, prev = max(c.slice_total(html0), 0), None
    sig_seen = set()
    for e in c.parse_list(html0, f["kw"]):
        sig_seen.add(e["公司ID"])
        entries_out.append(e)
    # 每页固定 20 家：不满一页就是这一片的最后一页，后面几页根本不必发请求。
    # 实测全库后半程全是几家用的小片（深圳试采里「标称 0」的片也照翻 2、3 页，
    # 3 个请求换 0 家），不这一条每片要白发 1~2 个请求。
    # 刻意不用「到手 >= 标称」当停止条件：标称数是站点报的，少报就会漏家。
    if len(entries_out) < 20:
        return total
    for page in range(2, PAGE_CAP + 1):
        html = c._fetch(surl(f, page))        # guarded：空白页限流自动交看门狗等放行
        st["list_req"] += 1
        if not html:
            break
        got = c.parse_list(html, f["kw"])
        a2, c2 = harvest_tree(html)
        for k, v in a2.items():
            st["tree"]["area"].setdefault(k, v)
        for k, v in c2.items():
            st["tree"]["cat"].setdefault(k, v)
        sig = tuple(e["公司ID"] for e in got)
        if sig and sig == prev:               # 内容与前页逐字相同＝触顶假翻页，停
            break
        prev = sig
        total = max(total, c.slice_total(html))
        fresh = [e for e in got if e["公司ID"] not in sig_seen]
        for e in fresh:
            sig_seen.add(e["公司ID"])
        entries_out.extend(fresh)
        if len(got) < 20:                      # 不满一页＝已经翻到最后一页
            break
        if not fresh and page >= 3:           # 翻页不再产出新条目＝到底
            break
    # 翻页间不另加睡：本站已开 PER_REQUEST_PACE，_fetch 每请求前自动对齐 2s 实测安全节奏
    return total


# ── 细分：顺着维度再切一刀 ────────────────────────────────────
def children_of(st, f, dim, entries, meta=None):
    """返回该维度切出来的子片列表：[]=这个维度确实切不动；None=这一维的子项还没探到
    （页面还没翻到过），调用方本轮先不当它用过，留给补漏轮再试。"""
    out = []
    if dim == "area":
        key = str(f["areaid"])
        if key not in st["tree"]["area"]:
            return None
        for vid, _name in st["tree"]["area"][key][:MAX_CHILDREN]:
            if int(vid) == f["areaid"]:        # 站点偶尔把自己列进子项，跳过
                continue
            out.append(dict(f, areaid=int(vid)))
    elif dim == "cat":
        key = str(f["catid"])
        if key not in st["tree"]["cat"]:
            return None
        for vid, _name in st["tree"]["cat"][key][:MAX_CHILDREN]:
            if int(vid) == f["catid"]:
                continue
            out.append(dict(f, catid=int(vid)))
    elif dim == "cap":
        if f["mincapital"] is None:
            for lo, hi in CAP_LADDER:
                out.append(dict(f, mincapital=lo, maxcapital=hi))
        else:
            lo, hi = float(f["mincapital"]), float(f["maxcapital"])
            if hi - lo < 1.0:                  # 注册资本都是整万，区间已经只装得下一个整数档
                return []
            mid = cap_mid(lo, hi)
            if mid is None:
                return []
            out = [dict(f, mincapital=lo, maxcapital=mid),
                   dict(f, mincapital=mid, maxcapital=hi)]
    elif dim in FLAT_VALUES:
        for v in FLAT_VALUES[dim]:
            if not f[dim]:
                out.append(dict(f, **{dim: v}))
    elif dim == "kw":
        # 入队判据：这一刀能把片子压到 120 家以内（或至少压掉三分之二）才算有用。
        # 补漏轮传进来的 entries 是空的，所以拿本片的标称数当基准，不能用 entries 长度。
        base = (meta or {}).get("total") or len(entries)
        for w in sub_keywords(entries):
            if w == f["kw"]:
                continue
            pt = probe_total(st, dict(f, kw=w))
            if pt is not None and (pt <= PAGE_CAP * 20 or pt * 3 < base):
                out.append(dict(f, kw=w))
            if len(out) >= 6:
                break
    return out


def enqueue_children(st, queue, parent_key, dim, kids, depth):
    for kf in kids:
        k = skey(kf)
        if k in st["slices"]:                  # 这片已经跑过/排过队，不重复
            continue
        st["slices"][k] = {"f": kf, "parent": parent_key, "depth": depth + 1,
                           "used": [], "total": None, "own": 0}
        queue.append(k)
    st["slices"][parent_key]["used"].append(dim)


def try_split(st, queue, key, meta, entries):
    """超限就顺着维度优先级找第一个切得动的维度，切完把子片排进队列。
    返回开出的子片数（0 表示这一片暂时没法再细分）。"""
    f, depth = meta["f"], meta.get("depth", 0)
    for dim in DIMS:
        if dim in meta["used"]:
            continue
        kids = children_of(st, f, dim, entries, meta)
        if not kids:                 # [] 切不动 / None 子项还没探到：都留到后面再说
            if kids is not None:
                meta["used"].append(dim)
            continue
        enqueue_children(st, queue, key, dim, kids, depth)
        seed_union(st, key, entries)              # 父片从此按子树并集记账，先把本片自己垫进去
        print(f"  ↳ 超限细分 {dim}：{len(kids)} 个子片", flush=True)
        return len(kids)
    return 0


def seed_union(st, key, entries):
    s = LISTED.setdefault(key, set())
    new = [e["公司ID"] for e in entries if e["公司ID"] not in s]
    if new:
        s.update(new)
        st["slices"][key].setdefault("listed", []).extend(new)
    st["slices"][key]["own"] = len(s)


# ── 详情采集 ──────────────────────────────────────────────────
def enqueue_details(st, entries, key):
    """把列表条目存进台账（按 slug 全局去重，重复主体绝不访问第二次）。
    名下到手数走 record_listing：按「本片列出的家数」记，不按「全局新增」记——
    别的路径采过的公司也算在这一片名下，不然覆盖率一直假性偏低，补漏轮会拿着
    同一个片反复重切。"""
    added = 0
    for e in entries:
        slug = e["公司ID"]
        if slug in st["seen_set"]:
            continue
        st["seen_set"].add(slug)
        st["seen_slugs"].append(slug)
        st["records"][slug] = {"stub": e}      # 先存列表字段，详情采完覆盖
        added += 1
    record_listing(st, key, [e["公司ID"] for e in entries])
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
def drain(st, queue, t0):
    """把当前队列跑干净：每一片翻满，被 120 截断就顺着优先级再切一刀。"""
    last_export = time.time()
    done = 0
    while queue:
        key = queue.popleft()
        meta = st["slices"].get(key) or {"f": json.loads(key), "parent": None,
                                         "depth": 0, "used": [], "total": None, "own": 0}
        st["slices"].setdefault(key, meta)
        f = meta["f"]
        entries = []
        total = crawl_slice(st, f, entries)
        if total is None:
            if meta.get("retried"):
                print(f"  ⚠ 拿不到页，放弃本片：{sdesc(st, f)}", flush=True)
                meta["total"] = 0
            else:
                meta["retried"] = 1
                queue.append(key)
                print(f"[枚举] 整片拿不到页，稍后重试一次：{sdesc(st, f)}", flush=True)
            st["queue"] = list(queue)
            save_state(st)
            continue
        added = enqueue_details(st, entries, key)
        meta["total"] = total
        got = len(entries)
        # 零产出防滑坡：一个片一家新公司都没列出（added=0），可标称还被 120 截断，说明
        # 它只是把同一批公司换个档位重排；它的子片几乎必然还是这批熟面孔。实测（深圳市
        # 试采）前期 244 个请求换 2156 家，之后 208 个请求只换 70 家——全在「正好 300 万/
        # 100 万」这种上千家扎堆档里反复二分。规则：本片零产出、且父片也是零产出，就不
        # 再往下切（给「再切一刀可能露出新面孔」留一层机会，不是一刀冷就收）。
        pk = meta.get("parent")
        meta["dead"] = 1 if (added == 0 and pk and st["slices"].get(pk, {}).get("dead")) else 0
        done += 1
        print(f"[枚举] {sdesc(st, f)} 标称{total} 到手{got} 新增{added} 队列剩{len(queue)} "
              f"累计{len(st['seen_slugs'])}家 列表请求{st['list_req']}", flush=True)
        if (total > got and not meta["dead"] and meta["depth"] < MAX_DEPTH
                and st["list_req"] < MAX_LIST_REQ):
            try_split(st, queue, key, meta, entries)
        st["queue"] = list(queue)
        save_state(st)                          # 每片落盘：中途被杀也不丢进度
        if time.time() - last_export > EXPORT_EVERY:
            n, k = export_excel(st)
            print(f"[导出] 明细 {n} 行（入库 {k}）→ {EXPORT_XLSX.name}"
                  f"，已跑 {(time.time()-t0)/60:.1f} 分钟", flush=True)
            last_export = time.time()
    return done


def gap_rounds(st, queue):
    """补漏轮：凡是「名下到手（子树并集）< 标称」的片，换个还没用过的维度再切，
    直到没有增量、跑不动新片、或触到请求量上限。"""
    rnd = 0
    while rnd < MAX_ROUNDS:
        if st["list_req"] >= MAX_LIST_REQ:
            print(f"  列表请求量已到上限 {MAX_LIST_REQ}，补漏停止", flush=True)
            return
        rnd += 1
        opened = 0
        for key, meta in list(st["slices"].items()):
            total, own = meta.get("total"), meta.get("own", 0)
            if not total or total <= own or meta.get("dead") or meta.get("depth", 0) >= MAX_DEPTH:
                continue
            if try_split(st, queue, key, meta, []):
                opened += 1
        if not opened:
            print(f"[补漏] 第 {rnd} 轮没有可再切的片，枚举到此收口", flush=True)
            return
        print(f"\n[补漏] 第 {rnd} 轮：又开出 {opened} 个父片的新维度，共 {len(queue)} 个子片", flush=True)
        st["queue"] = list(queue)
        st["rounds"] = rnd
        save_state(st)
        n = drain(st, queue, time.time())
        st["queue"] = list(queue)
        save_state(st)
        if not n:
            return


def main():
    global SEED_AREAID
    if len(sys.argv) > 1:
        SEED_AREAID = int(sys.argv[1])
    init_paths()
    st = load_state()
    st["seen_set"] = set(st["seen_slugs"])
    print(f"台账并集账本：已细分片 {rebuild_listed(st)} 个", flush=True)
    queue = deque(st["queue"])
    OUT_DIR.mkdir(exist_ok=True)
    t0 = time.time()
    print(f"开跑：{'只试采 areaid=' + str(SEED_AREAID) if SEED_AREAID else '目标全库 13285 家'}；"
          f"台账已见 {len(st['seen_slugs'])} 家", flush=True)

    # 阶段一：枚举全库（切片翻满→超限按维度优先级细分→跑完再补漏换维度切）
    drain(st, queue, t0)
    gap_rounds(st, queue)
    st["queue"] = []
    save_state(st)
    print(f"\n枚举完成：共 {len(st['seen_slugs'])} 家（列表请求 {st['list_req']} 个，"
          f"切片 {len(st['slices'])} 片，耗时 {(time.time()-t0)/60:.1f} 分钟）\n", flush=True)
    # 覆盖率：根片名下并集就是「整库被我们列出过多少家」，这是最硬的指标
    root_key = skey(new_slice(areaid=SEED_AREAID or 0))
    root = st["slices"].get(root_key) or {}
    if root.get("total"):
        cov = root["own"] * 100.0 / root["total"]
        print(f"覆盖率：根片标称 {root['total']} 家，列出过 {root['own']} 家（{cov:.1f}%）", flush=True)
    # 收口报告：哪些片还是没拿全（不同维度盲区导致的），如实打出来不粉饰
    short = [(m["total"] - m["own"], m) for m in st["slices"].values()
             if m.get("total") and m["total"] > m.get("own", 0)]
    short.sort(reverse=True, key=lambda x: x[0])
    if short:
        miss = sum(x[0] for x in short)
        print(f"⚠ 仍有 {len(short)} 个切片没拿全，估算缺口 {miss} 家（Top5）：", flush=True)
        for gap, m in short[:5]:
            print(f"    {sdesc(st, m['f'])} 标称{m['total']} 名下{m['own']} 差{gap} "
                  f"已用维度{m['used']}", flush=True)

    # 阶段二：逐家进详情（slug 全局去重；瞬时失败的不标 done）
    if SEED_AREAID:
        n, k = export_excel(st)
        print(f"\n试采只跑枚举：列出 {n} 家（详情略），台账 {STATE_FILE.name}", flush=True)
        return
    todo = [s for s in st["seen_slugs"] if not st["records"][s].get("done")]
    print(f"\n详情采集：待采 {len(todo)} 家", flush=True)
    t1 = last_export = time.time()
    for i, slug in enumerate(todo, 1):
        process_one(st, slug)
        if i % 20 == 0:
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
