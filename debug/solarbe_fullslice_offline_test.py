# solarbe_fullslice_offline_test.py —— 全量采集脚本「切片策略」离线回归（不触网）
#
# 为什么要有这一套：debug/scrape_solarbe_full.py 的命根子是那套自适应切片
# （地区树→注册资本→行业树→企业类型/模式/规模→关键词，跑完再补漏换维度切）。
# 真实站点要跑几小时才能验出策略写错了，所以这里造一个假站点模型，把
# 「每片只给 120 家、每个维度各有盲区」这两条站点脾气照搬，断言：
#   1) 维度优先级按覆盖率排（地区→资本→行业）；
#   2) 超限片一定会被继续细分，补漏轮把「没填资本/没挂行业」盲区里的公司捞回来；
#   3) 台账每片落盘 + 中断续跑结果一致（丢进度是这条链路上最不能接受的失败）；
#   4) 同一家公司在多个切片里重复出现时，只按 slug 进一次详情队列；
#   5) 翻页触顶（第 7 页复制第 6 页）不白发请求；
#   6) 注册资本是「min < 值 < max」严格开区间、传 0＝那一侧不限（probe_solarbe_capfix.py
#      实测语义），二分切点与档位分界都必须落在 x.5，否则「正好 50/100/500 万」这些
#      最扎堆的整数档会被两侧同时排掉（真实站点因此少采过 2457 家）；
#   7) 零产出防滑坡：连续两层都列不出新公司就不再细分（不然上千家的扎堆档会把请求
#      预算全花在同样的 120 家重排上）；
#   8) 流水线：每片枚举完当场把这一片新公司的详情采回来（客户不能干等几个小时看空表格）；
#   9) 限流刹车：看门狗判定“持续限流”后一个请求都不再发（实测真跑时这里空转过
#      4.5 请求/秒），且“整片拿不到页”不许被当成“这一片 0 家”写进台账（那样名下
#      几百上千家会被补漏轮永久跳过、静默丢数据）。
import sys, json, tempfile, pathlib, time
from collections import deque

sys.path.insert(0, "/Users/jhnoplan/Desktop/PythonDemo/debug")
sys.path.insert(0, "/Users/jhnoplan/Desktop/PythonDemo/b2bcollect_build")
import scrape_solarbe_full as M      # noqa: E402

checks, n = [], 0


def check(label, cond, detail=""):
    global n
    n += 1
    if cond:
        checks.append(f"  ✓ {label}")
    else:
        checks.append(f"  ✗ {label} {detail}")
        print("\n".join(checks))
        sys.exit(f"FAILED at check {n}: {label} {detail}")


# ── 假站点：300 家。地区树 华东→江苏/浙江；行业树 组件→晶硅/薄膜 ──
# 故意留盲区：1 成公司没填注册资本、1 成没挂行业分类（真实站点实测 25% 没挂分类），
# 但保证每家至少有一个可用维度，这样「全库一家不丢」才是可验证的硬指标。
# 注册资本照真实分布取「扎堆的整数档」（0＝没填，50/100/300/500/1000 万是最大几坨），
# 这样才能验出档位分界与二分切点会不会把正好整数的公司漏掉。
_CAPS = (0, 30, 50, 80, 100, 200, 300, 500, 1000, 2000)
ITEMS = []
for i in range(300):
    r = i % 10
    sub = "101" if (i % 20) < 10 else "102"      # 两个地区各 150 家，且与资本档解耦
    ITEMS.append({
        "公司ID": f"c{i:04d}", "公司名称": f"测试公司{i:04d}有限公司",
        "主营产品": f"逆变器 组件{i % 7}", "经营模式": "生产型",
        "所在区域": "江苏" if sub == "101" else "浙江",
        "地址": "", "公司网址": "", "详情URL": f"http://x/{i}/contact/", "标题": "t",
        "memberId": f"c{i:04d}", "是否核实": "已核实",
        "_area": ("1", sub),
        "_cat": None if r == 3 else ("9" if i % 2 else "91"),
        "_cap": _CAPS[r],
        "_type": (i % 6) + 1,
    })
PUBLIC = [k for k in ITEMS[0] if not k.startswith("_")]


def cat_hit(it, catid):
    if it["_cat"] is None:
        return False
    if catid == 9:                                   # 一级分类含子分类（Destoon 实测行为）
        return it["_cat"] in ("9", "91", "92")
    return it["_cat"] == str(catid)


class FakeCrawler:
    """照搬站点脾气：命中结果按 page 切片，最多 6 页（第 7 页起复制第 6 页）。"""

    incremental = False

    def __init__(self):
        self.calls = []
        self.blank = False             # ＝站点对我们 IP 返回空白页（限流）

    def _match(self, it, q):
        if int(q.get("catid", 0)) and not cat_hit(it, int(q["catid"])):
            return False
        aid = int(q.get("areaid", 0))
        if aid and aid not in (int(it["_area"][0]), int(it["_area"][1])):
            return False
        if q.get("mincapital") is not None:
            cap = it["_cap"]
            lo = float(q["mincapital"] or 0)
            hi = float(q.get("maxcapital") or 0)
            # 站点实测语义：min < 注册资本 < max 严格开区间，min/max 传 0＝那一侧不限
            if lo and cap <= lo:
                return False
            if hi and cap >= hi:
                return False
        if int(q.get("type", 0)) and it["_type"] != int(q["type"]):
            return False
        return True

    def _fetch(self, url, tries=1, ua=None, guarded=True):
        self.calls.append(url)
        if self.blank:                     # 限流：整个站点对这个 IP 返回空正文
            return ""
        q = dict(p.split("=", 1) for p in url.split("?", 1)[1].split("&"))
        hit = [it["公司ID"] for it in ITEMS if self._match(it, q)]
        page = min(int(q.get("page", 1)), 6)          # 触顶：第 7 页往后都返回第 6 页
        lo = (page - 1) * 20
        return json.dumps({"total": len(hit), "ids": hit[lo:lo + 20]}, ensure_ascii=False)

    def slice_total(self, html):
        return json.loads(html)["total"] if html else -1

    def parse_list(self, html, kw):
        ids = json.loads(html)["ids"]
        return [{k: it[k] for k in PUBLIC} for it in ITEMS if it["公司ID"] in ids]

    def crawl_detail(self, stub):
        rec = dict(stub)
        rec["状态"] = "成功"
        rec["电话"] = "010-12345678"
        return rec


M.c = FakeCrawler()
# 筛选栏下拉里抄树的子项：这一层用假数据直接喂（真实页面解析由 solarbe_offline_test 守护）
M.harvest_tree = lambda html: (
    {"0": [("1", "华东")], "1": [("101", "江苏"), ("102", "浙江")], "101": [], "102": []},
    {"0": [("9", "组件")], "9": [("91", "晶硅组件"), ("92", "薄膜组件")], "91": [], "92": []})

TMP = pathlib.Path(tempfile.mkdtemp(prefix="solarbe_slice_"))
M.STATE_FILE = TMP / "state.json"
M.OUT_DIR = TMP
M.DETAIL_JSON = TMP / "out.json"
M.EXPORT_XLSX = TMP / "out.xlsx"
M.EXPORT_EVERY = 10 ** 9          # 本测试只验枚举与台账，不导 Excel
M.LISTED.clear()
M.SEED_AREAID = None
ROOT_KEY = M.skey(M.new_slice())

# ── 1) 维度优先级 + 超限必切 ────────────────────────────────
st = M.load_state()
check("初始台账只有根片", len(st["slices"]) == 1 and len(st["queue"]) == 1)
st["seen_set"] = set(st["seen_slugs"])
queue = deque(st["queue"])
M.drain(st, queue, 0)
root = st["slices"][ROOT_KEY]
check("根片被 120 截断后先切地区树", root["used"][0] == "area", root["used"])
js = st["slices"][M.skey(M.new_slice(areaid=101))]
caps, cats = [i for i, d in enumerate(js["used"]) if d == "cap"], \
             [i for i, d in enumerate(js["used"]) if d == "cat"]
check("地区叶子片接着切资本，资本排在行业之前",
      js["used"][0] == "area" and caps and (not cats or caps[0] < cats[0]), js["used"])
check("根片已细分过（名下走并集）", isinstance(root.get("listed"), list))
over = [k for k, m in st["slices"].items() if m.get("total") and m["total"] > 120 and not m["used"]]
check("跑完一轮没有「超限却没细分」的漏片", not over, [st["slices"][k]["total"] for k in over][:5])

# ── 2) 补漏轮把盲区里的公司捞回来 ───────────────────────────
M.gap_rounds(st, queue)
listed = set(st["slices"][ROOT_KEY]["listed"])
missing = [it["公司ID"] for it in ITEMS if it["公司ID"] not in listed]
check("假站点 300 家全部被列出过", not missing, f"缺 {len(missing)} 家：{missing[:6]}")
check("根片名下并集＝全库家数", st["slices"][ROOT_KEY]["own"] == 300, st["slices"][ROOT_KEY]["own"])
check("详情队列按 slug 全局去重", len(st["seen_slugs"]) == 300
      and len(set(st["seen_slugs"])) == 300, len(st["seen_slugs"]))
check("列表请求量没有被维度组合炸开", len(M.c.calls) < 900, len(M.c.calls))

# ── 2b) 流水线：边枚举边采详情（用户反馈「Excel 都没有联系方式」的根因）──
check("枚举当片就顺手把详情采回来，不再等到收尾",
      st["detail_req"] == 300 and all(v.get("done") for v in st["records"].values()),
      st["detail_req"])
rows = M.flatten(st)
check("导出行已带上联系方式列", all(r["电话"] for r in rows), rows[0].get("电话"))

# ── 3) 台账每片落盘 + 断点续跑结果一致 ──────────────────────
check("台账文件已落盘", M.STATE_FILE.exists())
saved = json.loads(M.STATE_FILE.read_text(encoding="utf-8"))
check("运行期内存去重集不写进台账", "seen_set" not in saved)
check("台账存了完整切片账本", len(saved["slices"]) == len(st["slices"]))
st2 = M.load_state()
st2["seen_set"] = set(st2["seen_slugs"])
M.LISTED.clear()
M.rebuild_listed(st2)
q2 = deque(st2["queue"])
M.drain(st2, q2, 0)
M.gap_rounds(st2, q2)
check("续跑不重复入队公司已采的详情", len(st2["seen_slugs"]) == 300, len(st2["seen_slugs"]))
check("续跑后台账覆盖率保持 300", st2["slices"][ROOT_KEY]["own"] == 300, st2["slices"][ROOT_KEY]["own"])

# ── 4) 中途打断：只跑 20 片就停，再从台账续 ─────────────────
M.LISTED.clear()
M.STATE_FILE = pathlib.Path(tempfile.mkdtemp(prefix="solarbe_slice_mid_")) / "state.json"
st3 = M.load_state()
st3["seen_set"] = set()
q3 = deque(st3["queue"])
done = 0
while q3 and done < 20:
    key = q3.popleft()
    meta = st3["slices"][key]
    ents = []
    total = M.crawl_slice(st3, meta["f"], ents)
    M.enqueue_details(st3, ents, key)
    meta["total"] = total or 0
    if (total or 0) > len(ents):
        M.try_split(st3, q3, key, meta, ents)
    st3["queue"] = list(q3)
    M.save_state(st3)
    done += 1
half = len(st3["seen_slugs"])
check("打断 20 片已有进度且台账已落盘", 0 < half < 300 and M.STATE_FILE.exists(), half)
st4 = M.load_state()
check("续跑读到的家数与打断时一致", len(st4["seen_slugs"]) == half, (len(st4["seen_slugs"]), half))
check("续跑读到的队列与打断时一致", len(st4["queue"]) == len(q3), (len(st4["queue"]), len(q3)))

# ── 5) 翻页触顶不白发请求 ───────────────────────────────────
before = len(M.c.calls)
ents = []
M.crawl_slice(st4, M.new_slice(areaid=101), ents)          # 江苏：150 家
check("一片最多发 6 页请求", len(M.c.calls) - before <= 6, len(M.c.calls) - before)
check("到手条目被钳在 120 家", len(ents) == 120, len(ents))
before = len(M.c.calls)
ents_small = []
M.crawl_slice(st4, M.new_slice(areaid=101, mincap=1999.5, maxcap=2000.5), ents_small)  # 15 家
check("不满一页的小片只发 1 个请求", len(M.c.calls) - before == 1 and 0 < len(ents_small) < 20,
      (len(M.c.calls) - before, len(ents_small)))
before = len(M.c.calls)
ents_empty = []
M.crawl_slice(st4, M.new_slice(areaid=101, mincap=1.5, maxcap=10.5), ents_empty)   # 0 家
check("空片不白发翻页请求", len(M.c.calls) - before == 1 and not ents_empty,
      (len(M.c.calls) - before, len(ents_empty)))

# ── 6) 资本开区间：档位与二分切点都不能漏掉「正好整数」那坨 ─────
f_open = M.new_slice(mincap=5000.5, maxcap=M.CAP_INF)
kids = M.children_of(st4, f_open, "cap", [])
check("上不封顶值段二分出两段", len(kids) == 2, kids)
check("二分后最后一段仍上不封顶", kids[-1]["maxcapital"] == M.CAP_INF
      and kids[-1]["mincapital"] == 502500.5, kids)
check("值段只剩一个整数档时不再切",
      M.children_of(st4, M.new_slice(mincap=100.5, maxcap=101.5), "cap", []) == [])


def band_hits(cap):
    return [(lo, hi) for lo, hi in M.CAP_LADDER
            if (not lo or cap > lo) and (not hi or cap < hi)]


VALS = list(range(0, 120)) + [200, 300, 500, 1000, 2000, 5000, 50000, 100000, 1000000]
check("每个整数注册资本都被恰好一档接住（无盲区、无重叠）",
      all(len(band_hits(v)) == 1 for v in VALS),
      [v for v in VALS if len(band_hits(v)) != 1][:8])
mids, stack = [], list(M.CAP_LADDER)
while stack:
    lo, hi = stack.pop()
    m = M.cap_mid(lo, hi)
    if m is None:
        continue
    mids.append(m)
    stack += [(lo, m), (m, hi)]
    if len(mids) > 400:
        break
check("资本二分切点全落在 .5 上", len(mids) > 50 and all(v % 1 == 0.5 for v in mids),
      [v for v in mids if v % 1 != 0.5][:5])
check("没填注册资本（记 0）被最低一档接住", band_hits(0) == [(0, 0.5)], band_hits(0))

check("地区树叶子节点不再切地区",
      M.children_of(st4, M.new_slice(areaid=999), "area", []) is None)
check("URL 带齐已固定维度", "mincapital=100.5&maxcapital=300.5&type=2" in
      M.surl(M.new_slice(areaid=10403, mincap=100.5, maxcap=300.5, type_=2), 3),
      M.surl(M.new_slice(areaid=10403, mincap=100.5, maxcap=300.5, type_=2), 3))

# ── 7) 零产出防滑坡：连续两层都列不出新公司就不再往下细分 ──────
# 深圳市试采实测：前期 244 个请求换 2156 家，之后 208 个请求只换 70 家——全在「正好
# 100 万/300 万」这种上千家的扎堆档里反复二分。防滑坡只能一刀切掉这类重排，又不能
# 一冷却就收（再切一刀确实偶尔会露出新面孔），所以给“零产出一层”留着机会。
def fresh_state(parent_dead):
    s = {"v": M.STATE_VERSION, "started": "",
         "seen_slugs": [it["公司ID"] for it in ITEMS],
         "records": {it["公司ID"]: {"stub": it} for it in ITEMS},
         "slices": {}, "tree": {"area": {}, "cat": {}},
         "list_req": 0, "detail_req": 0, "rounds": 0, "stalled": []}
    s["seen_set"] = set(s["seen_slugs"])
    pk = M.skey(M.new_slice())
    s["slices"][pk] = {"f": json.loads(pk), "parent": None, "depth": 0, "used": [],
                       "total": 300, "own": 300, "dead": parent_dead}
    k = M.skey(M.new_slice(areaid=101))          # 150 家但全已采过＝零产出，且被 120 截断
    s["slices"][k] = {"f": json.loads(k), "parent": pk, "depth": 1, "used": [],
                      "total": None, "own": 0}
    s["queue"] = [k]
    return s


for dead, expect, label in ((1, 2, "父片也零产出时不再细分"), (0, 13, "只零产出一层还给一次机会")):
    M.STATE_FILE = pathlib.Path(tempfile.mkdtemp(prefix="solarbe_slice_dead_")) / "state.json"
    M.LISTED.clear()
    sx = fresh_state(dead)
    qx = deque(sx["queue"])
    M.drain(sx, qx, 0)
    # drain 会把开出来的子片一并跑干，所以看切片总数而不是队列长度
    check("零产出防滑坡：" + label, len(sx["slices"]) == expect and sx["slices"][
        M.skey(M.new_slice(areaid=101))]["dead"] == (1 if dead else 0),
        (len(sx["slices"]), expect))

# ── 8) 限流刹车：该收工就收工，且拿不到页不许错记成 0 家 ──────────
# 真实运行踩到的两个坑（2026-11-02 日志）：17:34 看门狗已判「持续限流、本轮到此
# 为止」，但 _sleep 看置位就不睡，脚本拿着空白页以 4.5 请求/秒空转了几千次；同时
# 「整片拿不到页」被写成 meta["total"]=0，那片名下的公司从此被补漏轮跳过。
M.STATE_FILE = pathlib.Path(tempfile.mkdtemp(prefix="solarbe_slice_stop_")) / "state.json"
M.LISTED.clear()
SL_KEY = M.skey(M.new_slice(areaid=101))
s8 = fresh_state(0)
before = len(M.c.calls)
M.c._stop_requested = True
check("看门狗置位后 drain 一个请求都不发、立刻收工",
      M.drain(s8, deque(s8["queue"]), 0) == 0 and len(M.c.calls) == before,
      len(M.c.calls) - before)
check("收工时当前片仍完整留在队列里（不丢片）", s8["queue"] == [SL_KEY], s8["queue"])
check("收工时台账已落盘", M.STATE_FILE.exists())
M.gap_rounds(s8, deque(s8["queue"]))
check("补漏轮看到置位同样不重切、不发请求", len(M.c.calls) == before and s8["rounds"] == 0,
      (len(M.c.calls) - before, s8["rounds"]))
M.c._stop_requested = False

s9 = fresh_state(0)
M.c.blank = True
M.drain(s9, deque(s9["queue"]), 0)
M.c.blank = False
check("整片拿不到页：两次重试用完进 stalled，标称数保持未知（不写 0）",
      s9["slices"][SL_KEY]["total"] is None and s9["stalled"] == [SL_KEY],
      (s9["slices"][SL_KEY]["total"], s9["stalled"]))
check("补漏轮开头把 stalled 片重新排回队列重给机会",
      (lambda q=deque(s9["queue"]): (M.gap_rounds(s9, q), SL_KEY in q and not s9["stalled"])[1])(),
      (s9["stalled"], SL_KEY))

# 导出计时器：必须是模块级的墙上时钟。原先它是 drain 的局部变量，开新一轮就归零，
# 真实运行里 17:21 导完那份「只有 35 家有联系方式」的表之后到 17:39 一次没刷。
M.EXPORT_EVERY = 0
M._LAST_EXPORT = 0.0
check("到点就导，跑下轮不会把计时器归零", M.maybe_export(s9, 0) is not None)
M.EXPORT_EVERY = 10 ** 9
check("没到点不重复导（不然每片都在写盘）", M.maybe_export(s9, 0) is None)

print("\n".join(checks))
print(f"ALL PASS ({n} checks)")
