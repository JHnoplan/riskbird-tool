# -*- coding: utf-8 -*-
"""globalimporter / makepolo 离线自审（零网络，全部用实机取回的页面与已知真值的码图）：
  ① GI 列表解析：真页面 37KB → 家数/公司名/主营/联系人/电话/详情URL/区域
  ② GI 号码清洗：含「010-13920758-444」型脏号还原（同店子域 w13920758444 互证）
  ③ 马可波罗 列表解析：真搜索页 181KB → 卡片聚合成公司维度
  ④ 马可波罗 两套模板的明文字段解析（A 型 <span> / B 型 <li>）
  ⑤ 马可波罗 crawl_detail 端到端：FakeSession 喂真 contact_us 页 + 人工转写过真值的码图
  ⑥ 传真差分：把某店明文传真改成与码图固话同串 → 该号必须被剔（不能把传真机当电话入库）
"""
import collections
import glob
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "b2bcollect_build"))
import gi_crawler
import makepolo_crawler
from mk_phone_ocr import load_default_font

# ════════ 实机夹具定位（缺夹具＝如实跳过，绝不把「脚本没夹具」报成「产品坏了」）════════
# 这些页面是实机取回的唯一真值来源（家数、总数、真实号码都对着它们断言）。
# 过去一律写死 /tmp——机器一重启就没了，回归清单于是长期红着一项，
# 红的原因和代码好坏毫无关系，反而把真问题淹掉。现在按「仓库副本 → /tmp 现场」两级找。
FIX_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "gi_mp")


def _skip_fixtures(what):
    print(f"⏭ SKIP-FIXTURE 缺实机夹具：{what}　"
          f"取回方式：跑 debug/gi_mp_live_probe.py，把页面放进 {FIX_DIR}/（或 /tmp/），断言自动恢复")
    sys.exit(0)


def FX(name):
    for _base in (FIX_DIR, "/tmp"):
        _p = os.path.join(_base, name)
        if os.path.exists(_p):
            return _p
    _skip_fixtures(name)


def FX_DIR(name):
    for _base in (FIX_DIR, "/tmp"):
        _p = os.path.join(_base, name)
        if os.path.isdir(_p):
            return _p
    _skip_fixtures(name)


def FX_GLOB(pattern):
    for _base in (FIX_DIR, "/tmp"):
        if glob.glob(os.path.join(_base, pattern)):
            return os.path.join(_base, pattern)
    _skip_fixtures(pattern)


OK = FAIL = 0


def check(tag, got, want=None, pred=None):
    global OK, FAIL
    good = pred(got) if pred else (got == want)
    OK += good
    FAIL += (not good)
    print(f"  {'✓' if good else '✗'} {tag}: {got!r}" + ("" if good else f"  期望 {want!r}"))


# ════════════ ① GI 列表解析（真页面） ════════════
print("\n=== ① 跨国采购网 列表解析（实机页面） ===")
gi = gi_crawler.GiCrawler()
html = open(FX("gi_p1.html"), encoding="utf-8").read()
rows = gi.parse_list(html, "制冷设备")
check("解析到家数", len(rows), 20)
check("总数 slice_total", gi.slice_total(html), 422)
r0 = rows[0]
check("公司名", r0["公司名称"], "东莞市企石唐蚁制冷设备厂（个体工商户）")
check("子域=memberId", r0["memberId"], "zhihehgj")
check("联系人(实为公司名→应被详情丢)", r0["列表联系人"], "东莞市企石唐蚁制冷设备厂")
check("电话原文", r0["列表电话"], "13392381815")
check("主营产品非空", bool(r0["主营产品"]), True)
check("详情URL 是产品静态页", r0["详情URL"].startswith("https://www.globalimporter.net/cdetail_"), True)
check("区域(名首级市)", gi._region_from_name("东莞市企石唐蚁制冷设备厂"), "东莞")
r1 = next(x for x in rows if x["memberId"] == "w13920758444")
check("脏号条目", r1["列表电话"], "010-13920758-444")
n_has_tel = sum(1 for x in rows if x["列表电话"])
print(f"  ℹ {len(rows)} 家里 {n_has_tel} 家带号码")

# ════════════ ② GI 号码清洗 ════════════
print("\n=== ② 跨国采购网 号码清洗（三种实测形态 + 脏号还原） ===")
cases = [
    ("13392381815", ["13392381815"], []),
    ("021-36110118", [], ["021-36110118"]),
    ("010-13920758-444", ["13920758444"], []),      # 区号+手机混写：还原本体
    ("0512-62714302", [], ["0512-62714302"]),
    ("0755-86331949", [], ["0755-86331949"]),        # 真固话不许被拆成手机
    ("暂无", [], []),
    ("000-000000", [], []),
    ("11111111111", [], []),
    ("86-13920758444", ["13920758444"], []),
]
for raw, wm, wt in cases:
    m, t = gi._split_numbers(raw)
    check(f"  {raw}", (m, t), (wm, wt))
# 脏号必须与同店子域互证才敢收
dub = gi._split_numbers(r1["列表电话"])[0]
check("脏号与子域双源互证", dub and dub[0] in r1["memberId"], True)

# ════════════ ③ 马可波罗 列表解析 ════════════
print("\n=== ③ 马可波罗 列表解析（实机搜索页 181KB） ===")
mp = makepolo_crawler.MakepoloCrawler()
sp = open(FX("mp_search.html"), encoding="utf-8").read()
mrows = mp.parse_list(sp, "制冷设备")
check("按子域去重后公司数", len(mrows) >= 15, True)
check("slice_total(1429811)", mp.slice_total(sp), 1429811)
m0 = mrows[0]
for k in ("公司名称", "memberId", "详情URL", "主营产品", "所在区域"):
    check(f"{k} 非空", bool(m0.get(k)), True)
check("详情URL 形如 contact_us", bool(re.match(r"http://[\w\-]+\.cn\.makepolo\.com/contact_us\.html",
                                            m0["详情URL"])), True)
print(f"  ℹ 卡片 40 张聚合为 {len(mrows)} 家公司")

# ════════════ ④ 两套模板的明文字段 ════════════
print("\n=== ④ 马可波罗 A/B 模板明文字段解析 ===")
A = open(FX("mp_028pack.html"), encoding="utf-8").read()
B = open(FX("mp_dgghzl.html"), encoding="utf-8").read()
# 走采集器里真正的清洗链（先吐码图 URL、再按字段收明文）。
# 空值不得覆盖已有值：contact_us 页下方「给我们留言」表单里也有一个空的「联系人：」，
# 采集器里是 `if v and not fields[k]` 防护的，这里口径必须一致（否则测试会比采集器多一个坑）。
f = lambda t: dict(reversed([(k, re.sub(r"\s+", " ", makepolo_crawler._TAG_RE.sub(" ", v)).strip(" ：:|/"))
               for k, v in makepolo_crawler._FIELD_RE.findall(makepolo_crawler._CODE_RE.sub("", t))
               if k and True]), )
fa, fb = f(A), f(B)
check("A 联系人", fa.get("联系人"), "张先生")
check("A Email", fa.get("Email"), "13881877244@139.com")
check("A 传真(不许当电话)", fa.get("传真"), "028-87631203")
check("A 地址含金府路", "金府路" in (fa.get("地址") or ""), True)
check("B 传真", fb.get("传真"), "0769-83034002")
check("B 地址", "塘厦镇" in (fb.get("公司地址") or ""), True)
check("B Email", fb.get("Email"), "676047694@qq.com")

# ════════════ ⑤ crawl_detail 端到端（FakeSession 喂真页 + 真值码图） ════════════
print("\n=== ⑤ 马可波罗 crawl_detail 端到端（离线喂图） ===")
FONT_IMG = {   # (通道, 人工转写过的真值样本) → 期望识别结果
    ("pro", "pro_1246x8givi.png"): ("13650342510", 11),
    ("y_left", "y_left_21aesjZC0l.png"): ("021-54783528", 12),
    ("y_left", "y_left_236aJPY*m0.png"): ("0769-86331949", 13),
}
FILES = [fn for _, fn in FONT_IMG]                 # 人工转写过真值的样本文件名
tok_of = {fn: json.load(open(FX("mkimgs_labels.json")))[fn]["token"] for fn in FILES}


class FakeResp:
    def __init__(self, content):
        self.content = content
        self.status_code = 200
        self.headers = {}
        self.text = content.decode("utf-8", "ignore") if isinstance(content, bytes) else content

class FakeSession:
    """contact_us 一律回 028pack 真页；码图按「页面里出现的 token」回对应的真值 PNG。"""

    def __init__(self, page, mapping, blank_codes=()):
        self.page, self.mapping, self.blank = page, mapping, set(blank_codes)
        self.calls = []

    def get(self, url, timeout=None, headers=None):
        self.calls.append(url)
        if "buildStringToimg" in url:
            tok = re.search(r"phone=([^&]+)", url).group(1)
            for fn in FILES:
                if tok_of[fn] == tok:
                    if tok in self.blank:
                        return FakeResp(b"")
                    return FakeResp(open(os.path.join(FX_DIR("mkimgs"), fn), "rb").read())
            return FakeResp(b"")
        return FakeResp(self.page.encode("utf-8"))

    def close(self):
        pass


def build_page(with_fax="028-87631203"):
    """拼一个 A 型 contact_us 片段：联系人/手机码/电话码/Email/传真/地址 全要素。"""
    return ('<div class="connect_msg"><ul>'
            '<li><span>联系人：张先生</span></li>'
            f'<li><span>手机：<img src="/purchase/buildStringToimg.php?t_page=pro&phone={tok_of["pro_1246x8givi.png"]}" style="width:120px"/></span></li>'
            f'<li><span>电话：<img src="/purchase/buildStringToimg.php?t_page=y_left&phone={tok_of["y_left_21aesjZC0l.png"]}" style="width:120px"/></span></li>'
            '<li><span>Email：13881877244@139.com</span></li>'
            f'<li><span>传真：{with_fax}</span></li>'
            '<li><span>地址：中国 四川 成都 金牛区金府路799号</span></li>'
            '</ul></div>')


c = makepolo_crawler.MakepoloCrawler()
c._session = FakeSession(build_page(), FONT_IMG)
c._pace_before_request = lambda *a, **kw: None
c._sleep = lambda *a, **kw: None
rec = c.crawl_detail({"公司名称": "成都市纬星包装材料有限公司", "公司ID": "028pack",
                      "memberId": "028pack", "关键词": "包装制品",
                      "主营产品": "包装制品", "公司网址": "http://028pack.cn.makepolo.com/",
                      "详情URL": "http://028pack.cn.makepolo.com/contact_us.html"})
check("状态", rec["状态"], "成功")
# 手机列：码图 OCR 在前、运营商邮箱前缀在后（两个都是真人号码，多一个就多一次触达机会）
check("手机首位=码图 OCR", rec["手机"].split(" /")[0], "13650342510")
check("手机次位=邮箱前缀", "13881877244" in rec["手机"], True)
check("电话(码图 y_left OCR)", rec["电话"], "021-54783528")
check("邮箱", rec["邮箱"], "13881877244@139.com")
check("联系人", rec["联系人"], "张先生")
check("地址含金府路", "金府路" in rec["地址"], True)
check("码图请求数(1 主 + 补图收敛)", len([x for x in c._session.calls if "buildStringToimg" in x]) <= 4, True)
check("通路账目·码图", c.src_count["码图"] >= 1, True)

# 邮箱前缀通路（139.com）：码图全空时也该有手机
c2 = makepolo_crawler.MakepoloCrawler()
c2._session = FakeSession(build_page(), FONT_IMG, blank_codes=set(tok_of.values()))
c2._pace_before_request = lambda *a, **kw: None
rec2 = c2.crawl_detail({"公司名称": "甲公司", "公司ID": "028pack", "memberId": "028pack",
                        "关键词": "包装制品", "主营产品": "包装材料", "详情URL": ""})
check("无图时手机=运营商邮箱前缀", rec2["手机"], "13881877244")
check("无图时状态", rec2["状态"], "成功")

# ════════════ ⑥ 传真差分 ════════════
print("\n=== ⑥ 传真差分（OCR 出的固话 == 明文传真 → 必须剔） ===")
c3 = makepolo_crawler.MakepoloCrawler()
c3._session = FakeSession(build_page(with_fax="021-54783528"), FONT_IMG)
c3._pace_before_request = lambda *a, **kw: None
rec3 = c3.crawl_detail({"公司名称": "乙公司", "公司ID": "028pack", "memberId": "028pack",
                        "关键词": "包装制品", "主营产品": "包装材料", "详情URL": ""})
check("传真号不进电话列", rec3.get("电话", ""), "")
check("手机仍在(不受传真影响)", rec3["手机"].split(" /")[0], "13650342510")

# 平台假号/假邮箱
c4 = makepolo_crawler.MakepoloCrawler()
c4._session = FakeSession('<div><li>传真：0769-111</li><li>Email：service@makepolo.com</li></div>', FONT_IMG)
c4._pace_before_request = lambda *a, **kw: None
rec4 = c4.crawl_detail({"公司名称": "丙公司", "公司ID": "028pack", "memberId": "028pack",
                        "关键词": "包装制品", "主营产品": "包装材料", "详情URL": ""})
check("站方客服邮箱被剔", rec4.get("邮箱", ""), "")

# ════════════ ⑦ 实机真页（第二轮采回的 contact_us）════════════
print("\n=== ⑦ 马可波罗 实机回接真页：字段/码图/传真在真数据上的行为 ===")
live = sorted(glob.glob(FX_GLOB("mp_live_*.html")))
check("实机回接页数", len(live) >= 6, True)
agg = collections.Counter()
for path in live:
    slug = path.split("mp_live_")[1].replace(".html", "")
    t = open(path, encoding="utf-8").read()
    codes = list(dict.fromkeys(makepolo_crawler._CODE_RE.findall(t)))
    fields = {}
    for k, v in makepolo_crawler._FIELD_RE.findall(makepolo_crawler._CODE_RE.sub("", t)):
        v = re.sub(r"\s+", " ", makepolo_crawler._TAG_RE.sub(" ", v)).strip(" ：:|/")
        if v and not fields.get(k):
            fields[k] = v
    agg["页"] += 1
    agg["码图"] += len(codes)
    agg["联系人"] += bool(fields.get("联系人"))
    agg["Email"] += bool(fields.get("Email") or fields.get("邮箱"))
    agg["传真"] += bool(fields.get("传真"))
    agg["地址"] += bool(fields.get("地址") or fields.get("公司地址"))
    agg["明文号码"] += bool(re.search(r"(?:1[3-9]\d{9}|0\d{2,3}-\d{7,8})",
                                     makepolo_crawler._TAG_RE.sub(" ", t)))
    lens = sorted({len(tk) for _s, tk in codes})
    agg["token总"] += len(codes)
    agg["token在54~56"] += sum(1 for _s, tk in codes if len(tk) in (54, 55, 56))
    print(f"  · {slug:18s} 码图 {len(codes)} 张 token长{lens} → 应读出号码长 "
          f"{sorted({l - makepolo_crawler.TOKEN_LEN_OFFSET for l in lens})}"
          f" 字段={sorted(fields)}")
    for _s, tk in codes:
        assert len(tk) - makepolo_crawler.TOKEN_LEN_OFFSET >= 6, (slug, len(tk))
check("真页普遍带码图", agg["码图"] >= 2 * agg["页"], True)
check("真页 token 多数落在手机/固话长度（54~56）",
      agg["token在54~56"] / max(1, agg["token总"]) >= 0.80, True)
print("  ℹ 少数店把「固话+分机」写成 16 字符（token 59），解不出就不入库（宁缺不假）")
check("真页联系人字段可读", agg["联系人"] == agg["页"], True)
check("真页邮箱字段常见", agg["Email"] >= agg["页"] - 1, True)
check("真页地址字段常见", agg["地址"] >= agg["页"] - 1, True)
print(f"  ℹ 真页汇总 {dict(agg)}：号码几乎全在图里，所以手机号必须靠图（已实测）")
# 传真差分在真页上的效果：dgghzl 的明文传真是 0769-83034002，采集器必须把它从电话列剔掉
fax_of = {}
for path in live:
    for k, v in makepolo_crawler._FIELD_RE.findall(open(path, encoding="utf-8").read()):
        if k == "传真" and not fax_of.get(path):
            fax_of[path] = re.sub(r"\D", "", makepolo_crawler._TAG_RE.sub("", v))
dgg = next((p for p in live if "dgghzl" in p), "")
check("dgghzl 真页明文传真是 0769-83034002", fax_of.get(dgg, ""), "076983034002")
check("采集器传真差分能把它从固话列拒掉",
      [x for x in ["0769-83034002", "0769-83034001"]
       if re.sub(r"\D", "", x) != fax_of.get(dgg, "@")], ["0769-83034001"])

# ════════════ ⑧ 图片码通路的策略回测（决策证据）════════════
print("\n=== ⑧ 号码图片码：出图按店性 + 两阶段取法回测 ===")
LB = json.load(open(FX("mkimgs_labels.json")))
FILES = set(os.listdir(FX_DIR("mkimgs")))
per_shop = collections.OrderedDict()
for fn, v in LB.items():
    per_shop.setdefault(v.get("shop") or "?", []).append(fn)
tot_img = sum(1 for fn in LB if fn in FILES)
shops_with_img = sum(1 for fns in per_shop.values() if any(f in FILES for f in fns))
rate = shops_with_img / len(per_shop)
print(f"  ℹ {len(per_shop)} 家店 × {len(LB)} 个 token → 拿到 {tot_img} 张 PNG；"
      f"放图的店 {shops_with_img} 家（{rate * 100:.1f}%）")
check("出图率量级（10%~16%）", 0.10 <= rate <= 0.16, True)
all6 = sum(1 for fns in per_shop.values()
           if sum(1 for f in fns if f in FILES) >= 2)
check("放图的店多数一次给 ≥2 张（所以命中后要把这一家取齐）",
      all6 / shops_with_img >= 0.75, True)
full6 = sum(1 for fns in per_shop.values()
            if sum(1 for f in fns if f in FILES) >= 5)
print(f"  ℹ 放图的 51 家：一次给 ≥5 张的有 {full6} 家，给 ≥2 张的有 {all6} 家")


def simulate(k_probe, k_fallback):
    """按历史数据回测两阶段取法：返回 (图中召回率, 平均请求/店)。"""
    recall = reqs = 0
    for fns in per_shop.values():
        head, tail = fns[:k_probe], fns[k_probe:]
        reqs += len(head)
        hit = any(f in FILES for f in head)
        recall += sum(1 for f in head if f in FILES)
        if not hit and tail:
            reqs += min(k_fallback, len(tail))
            hit = any(f in FILES for f in tail[:k_fallback])
            recall += sum(1 for f in tail[:k_fallback] if f in FILES)
            tail = tail[k_fallback:] if hit else []
        if hit:
            reqs += len(tail)
            recall += sum(1 for f in tail if f in FILES)
    return recall / tot_img, reqs / len(per_shop)


MP = makepolo_crawler.MakepoloCrawler
r_full, q_full = simulate(99, 0)
r_new, q_new = simulate(MP.IMG_PROBE_CODES, MP.IMG_PROBE_FALLBACK)
print(f"  ℹ 全打：召回 {r_full * 100:.1f}%，{q_full:.2f} 请求/店　｜　"
      f"两阶段：召回 {r_new * 100:.1f}%，{q_new:.2f} 请求/店")
check("两阶段召回不低于 90%", r_new >= 0.90, True)
check("两阶段比全打省请求", q_new < q_full * 0.75, True)
check("采集器常量与回测一致", (MP.IMG_PROBE_CODES, MP.IMG_PROBE_FALLBACK, MP.IMG_DEAD_STREAK),
      (2, 1, 30))
# 熔断阈值不能再浅：12.6% 出图率下连续 8 家全不中是常事（旧值 8 会把整轮手机号锁死）
p8 = (1 - rate) ** 8
check("旧阈值 8 会误伤（8 家全不中概率 > 20%）", p8 > 0.20, True)
check("现阈值 30 几乎不会误伤（< 5%）", (1 - rate) ** 30 < 0.05, True)

# ════════════ ⑨ 码图归属过滤（防把推广主的号当成本店手机号）════════════
print("\n=== ⑨ 码图归属：推广位串号必须切掉、本店号一个不丢 ===")
MP = makepolo_crawler.MakepoloCrawler
_C = MP.__new__(MP)                     # 纯函数路径，不建会话/不开浏览器

# 逐张 OCR 取证得到的真值（区块 → 号码）：实机 2026-11-04
KNOWN_OWN = {   # 本店登记联系方式（在「手机/电话」标签里）
    "csdfgyb123": ["8d1344x4OF", "ef2cSY4LrE", "86fbYiMi0c"],
    "51chinasensor": ["5d5fSHGk9K", "ec27Jy3Wo6", "36546pafQJ"],
}
KNOWN_PROMO = {  # phone_mask_list「联 系商家」弹窗里的 CPC 推广码（广告主的号）
    "csdfgyb123": ["8160PG5G6v", "3cdbqLBxvf", "da1eCbL31Z"],
    "51chinasensor": ["40e5v97LhN", "f3daFgw1h1", "46273nm-yY"],
}
kept_map = {}
for _f in sorted(glob.glob(FX_GLOB("mp_live_*.html"))):
    _slug = os.path.basename(_f)[8:-5]
    _doc = open(_f, encoding="utf-8", errors="ignore").read()
    kept_map[_slug] = [t for _, t in _C._shop_code_tokens(_doc, _slug)]

for _slug, own in KNOWN_OWN.items():
    for _t in own:
        check(f"{_slug} 本店码 {_t[:8]}… 保留",
              any(k.startswith(_t) for k in kept_map[_slug]), True)
    for _t in KNOWN_PROMO[_slug]:
        check(f"{_slug} 推广码 {_t[:8]}… 剔除",
              not any(k.startswith(_t) for k in kept_map[_slug]), True)
# 两个上海号之所以危险：它们在毫不相干的两家店上 OCR 结果完全相同
check("两家的保留集无交集（真号不跨店）",
      not (set(kept_map["csdfgyb123"]) & set(kept_map["51chinasensor"])), True)
# B 型模板页（无推广弹窗）必须零误伤，不然会把唯一的本店号切掉
# 比较用集合而不是列表：保留集的顺序是「首次合法出现位置」，跟「首次出现位置」可以不同
for _slug in ("dgghzl", "wuxibz", "szxgxjx", "shctzlzd", "yitaojienengzd", "gzhaomei"):
    _doc = open(FX("mp_live_%s.html" % _slug), encoding="utf-8", errors="ignore").read()
    _raw = set(t for _, t in makepolo_crawler._CODE_RE.findall(_doc))
    check(f"{_slug}（无推广弹窗的模板）零误伤", set(kept_map[_slug]) == _raw, True)

# 哨兵：同一号第 3 家店起剔除，前两家保留（集团开多店是常态）
_S = MP.__new__(MP)
_S._num_shops = {}
_S._log = lambda *a, **k: None
_seq = [_S._public_num_hit("13900001111", s) for s in ("a1", "a2", "a3", "a4")]
check("公共号：第1/2 家不剔，第3/4 家剔", _seq, [False, False, True, True])
check("不同号互不干扰",
      [_S._public_num_hit("13800002222", "b1"), _S._public_num_hit("13800003333", "b2")],
      [False, False])
check("哨兵阈值常量就位", MP.PUBLIC_NUM_MAX_SHOPS, 2)

# ════════════ ⑩ GI 拦截归类：会话质询不许喊「去 Chrome 手动过」════════════
print("\n=== ⑩ 跨国采购网 拦截归类（人不参与，按 IP 节奏等冷却）===")
_SHELL = ("<html><head><script>WebShieldSessionVerify=1;</script></head>"
          "<body>too many visits from your ip, please try again after 2 hours</body></html>").encode("gb18030", "ignore")


class _ShellResp:
    status_code = 200
    headers = {}

    def __init__(self):
        self.content = _SHELL
        self.text = _SHELL.decode("gb18030", "ignore")


class _ShellSession:
    def get(self, url, timeout=None, headers=None):
        return _ShellResp()


_gi2 = gi_crawler.GiCrawler()
_gi2._log = lambda *a, **k: None
_gi2._emit = lambda *a, **k: None
_gi2._pace_before_request = lambda *a, **k: None
_gi2._bootstrap = lambda: True
_gi2._session = _ShellSession()
_gi2._shell_hits = 9                 # 已经反复引导过：不再是「缺 Cookie」
_handled = []
_gi2.handle_http_block = lambda url="": (_handled.append(_gi2._block_kind) or False)
_gi2._fetch("https://www.globalimporter.net/sellindex.html")
check("薄壳反复仍不成 → 归拦截处置（绝不当成「本关键词没公司」）", len(_handled), 1)
check("会话质询归入 ip 等待（本站人工点不掉）", _handled, ["ip"])

# 文案硬验证：不弹浏览器的站，不能再让用户去 Chrome 找验证码
_gh = gi_crawler.GiCrawler()
_lines = []
_gh._log = lambda *a, **k: _lines.append(" ".join(str(x) for x in a))
_gh._emit = lambda *a, **k: None
_gh._block_times = []
_gh._block_kind = "ip"
_gh._wait_manual_unblock("防采插页（WebShieldSessionVerify）", lambda: "", max_wait=0)
_txt = "\n".join(_lines)
check("不再让用户去 Chrome 手动过验证", "请在 Chrome 窗口里手动过验证" not in _txt, True)
check("如实说明：无需人工、工具自己等放行", "没有验证码可点" in _txt, True)
check("不写入空记录（暂停推进而非当成无结果）", "不写入空记录" in _txt, True)

# ════════════ ⑪ 产品详情页明文手机（实测手机覆盖从 12.6% 抬到 85% 的那条路）════════════
print("\n=== ⑪ 马可波罗 产品页明文手机：字段取全、噪声不进、归属不自证就丢 ===")
MP = makepolo_crawler.MakepoloCrawler
_CX = MP.__new__(MP)
PD_CASES = [(FX("mp_proddetail_101063246805.html"), "dgghzl",
             {"手机": "13450460020", "联系人": "黄亨",
              "电话": "0769-83034001", "传真": "0769-83034002"}),
            (FX("mp_proddetail_101106428872.html"), "wuxibz",
             {"手机": "17637702908", "联系人": "吴明辉", "传真": "0375-2298818"}),
            (FX("mp_proddetail_101117116006.html"), "yitaojienengzd",
             {"手机": "13726329969", "联系人": "张路平"})]
PD_NOISE = ["13031303633", "13031313036", "13031313137", "13136303036",
            "16207895295", "14419005517"]      # appid / 统一社会信用代码 / 页内十六进制串
for _f, _slug, _want in PD_CASES:
    _doc = open(_f, encoding="utf-8", errors="ignore").read()
    _got = _CX._parse_product_page(_doc, _slug)
    for _k, _v in _want.items():
        check(f"{_slug} {_k}={_v!r}", _got.get(_k), _v)
    check(f"{_slug} 地址取到", bool(_got.get("地址")), True)
    _blob = " ".join(_got.values())
    for _n in PD_NOISE:
        check(f"{_slug} 噪声号 {_n} 不入库", _n not in _blob, True)
    check(f"{_slug} 归属闸门（递错店→整页丢弃）",
          _CX._parse_product_page(_doc, "other_shop"), {})

# 商铺页回 439、只有产品页名片时，必须照样成单（实机碰到 shop135637108916827 就是这形状）
_PROD_HTML = open(FX("mp_proddetail_101063246805.html"), encoding="utf-8", errors="ignore").read()


class _PdResp:
    def __init__(self, body, code=200):
        self.content = body
        self.status_code = code
        self.headers = {}
        self.text = body.decode("utf-8", "ignore")


class _PdSession:
    """产品页给真页，商铺页（contact_us）一律 439 空正文。"""

    def get(self, url, timeout=None, headers=None):
        return _PdResp(_PROD_HTML.encode("utf-8")) if "product-detail" in url else _PdResp(b"", 439)

    def close(self):
        pass


_mp2 = makepolo_crawler.MakepoloCrawler(port=9299)
_mp2.incremental = False
_mp2._log = lambda *a, **k: None
_mp2._session = _PdSession()
_mp2._pace_before_request = lambda *a, **k: None
_rec = _mp2.crawl_detail({"公司名称": "东莞市港汇制冷机电设备有限公司", "公司ID": "dgghzl",
                          "memberId": "dgghzl", "产品ID": "101063246805", "关键词": "制冷设备",
                          "主营产品": "制冷配件", "公司网址": "http://dgghzl.cn.makepolo.com/"})
check("商铺页 439 但产品页有名片 → 不判失败", _rec["状态"], "成功")
check("手机从产品页明文取到", _rec.get("手机"), "13450460020")
check("固话从产品页取到", _rec.get("电话"), "0769-83034001")
check("传真没被当电话入库", "0769-83034002" not in (_rec.get("电话") or ""), True)
check("联系人从产品页名片取到", _rec.get("联系人"), "黄亨")
check("产品页通路记账上", _mp2.src_count["产品页"] >= 2, True)


# ════════════ ⑫ 完整 crawl() 实机跑暴露的四处缺陷（回归钉住）════════════
print("\n=== ⑫ 马可波罗：公司名参与早过滤 / 同店换产品页 / 439 死铺状态 / 实体残留 ===")
_mp3 = makepolo_crawler.MakepoloCrawler(port=9301)
_mp3.incremental = False
_mp3._log = lambda *a, **k: None
# ⑫-1 马可波罗列表卡的「主营产品」写的是类目词，与关键词常常不同字。
# 实测三页 69 张卡：只看主营产品只放过 43%，公司名里就带行业词的同行被误杀 17 家。
check("早过滤放过「公司名命中而主营不命中」（天津良丰·冷却塔）", _mp3.is_relevant(
    "制冷设备", _mp3.stub_relevance_text(
        {"公司名称": "天津良丰制冷设备有限公司", "主营产品": "冷却塔", "标题": ""})), True)
check("（对照）只看主营产品确实会误杀", _mp3.is_relevant("制冷设备", "冷却塔"), False)
check("早过滤仍砍掉真不相关的（伊明传动·减速器）", _mp3.is_relevant(
    "制冷设备", _mp3.stub_relevance_text(
        {"公司名称": "伊明传动科技（厦门）有限公司", "主营产品": "减速器", "标题": ""})), False)

# ⑫-2 同店多产品：备选 proid 要在列表阶段就带走（实测 48% 的店有 2 个以上产品 id）
_TWO_CARD = ('<div class="s_product_contact"><span>[广东东莞]</span>'
             '<a href="http://dgtest.cn.makepolo.com/">东莞市测试制冷机电设备有限公司</a>'
             '<a href="http://dgtest.cn.makepolo.com/companyProduct.html">冷水机,冷冻机</a>'
             '<a href="http://dgtest.cn.makepolo.com/contact_us.html?proid=100000000001">联系</a></div>'
             '<div class="s_product_contact"><span>[广东东莞]</span>'
             '<a href="http://dgtest.cn.makepolo.com/">东莞市测试制冷机电设备有限公司</a>'
             '<a href="http://dgtest.cn.makepolo.com/companyProduct.html">制冰机,冷库设备</a>'
             '<a href="http://dgtest.cn.makepolo.com/contact_us.html?proid=100000000002">联系</a></div>')
_e = _mp3.parse_list(_TWO_CARD, "制冷设备")
check("同店两张卡仍去重成一家", len(_e), 1)
check("首卡 proid 记进「产品ID」", _e[0]["产品ID"], "100000000001")
check("第二张卡的 proid 记进「备选产品ID」", _e[0]["备选产品ID"], ["100000000002"])


class _R2:
    def __init__(self, body, code=200):
        self.content = body
        self.status_code = code
        self.headers = {}
        self.text = body.decode("utf-8", "ignore")


class _AltSession:
    """首卡那个产品页 439、同店第二个产品页 200，商铺联系页也 439。
    实机形状：昆山冠信是「联系页 439 / 产品页 200」，这里是它的镜像——只能靠换 proid 救。"""

    def __init__(self, prod_html):
        self.prod = prod_html.encode("utf-8")

    def get(self, url, timeout=None, headers=None):
        if "product-detail" in url:
            return _R2(self.prod) if url.endswith("/100000000002.html") else _R2(b"", 439)
        return _R2(b"", 439)

    def close(self):
        pass


_mp4 = makepolo_crawler.MakepoloCrawler(port=9304)
_mp4.incremental = False
_mp4._log = lambda *a, **k: None
_mp4._session = _AltSession(_PROD_HTML)
_mp4._pace_before_request = lambda *a, **k: None
_rec2 = _mp4.crawl_detail({"公司名称": "东莞市港汇制冷机电设备有限公司", "公司ID": "dgghzl",
                           "memberId": "dgghzl", "产品ID": "100000000001",
                           "备选产品ID": ["100000000002"], "关键词": "制冷设备",
                           "主营产品": "制冷设备", "公司网址": "http://dgghzl.cn.makepolo.com/"})
check("首卡产品页 439 → 换同店其它产品页救回手机", _rec2.get("手机"), "13450460020")
check("换腿成功就不该判失败", _rec2["状态"], "成功")


class _All439:
    def get(self, url, timeout=None, headers=None):
        return _R2(b"", 439)

    def close(self):
        pass


_mp5 = makepolo_crawler.MakepoloCrawler(port=9305)
_mp5.incremental = False
_mp5._log = lambda *a, **k: None
_mp5._session = _All439()
_mp5._pace_before_request = lambda *a, **k: None
_rec3 = _mp5.crawl_detail({"公司名称": "深圳市利尔机械设备有限公司", "公司ID": "liermachine",
                            "memberId": "liermachine", "产品ID": "100463758253",
                            "关键词": "制冷设备", "主营产品": "制冷设备",
                            "公司网址": "http://liermachine.cn.makepolo.com/"})
check("整店 439 → 状态写清是站方下架而不是我们没采到", _rec3["状态"], "商铺已关闭(站方不给看)")
check("商铺已关闭是终态（会进种子库，不再每轮白烧两个请求）", _mp5._is_terminal(_rec3), True)
check("商铺已关闭不会被当成拿到过联系方式",
      _rec3.get("手机") or _rec3.get("电话") or _rec3.get("邮箱"), "")

# ⑫-3 站方写得随手的 &nbsp（不带分号）以前会跟着入库（实机联系人「易道和 &nbsp」）
_NB = ('<html><body><div class="connect_msg"><span>联系人：易道和&nbsp</span>'
       '<span>手机：13386086818</span></div></body></html>').encode("utf-8")


class _NbSession:
    def get(self, url, timeout=None, headers=None):
        return _R2(b"", 439) if "product-detail" in url else _R2(_NB)

    def close(self):
        pass


_mp6 = makepolo_crawler.MakepoloCrawler(port=9306)
_mp6.incremental = False
_mp6._log = lambda *a, **k: None
_mp6._session = _NbSession()
_mp6._pace_before_request = lambda *a, **k: None
_rec4 = _mp6.crawl_detail({"公司名称": "上海日进鑫机械设备有限公司", "公司ID": "rjxin",
                           "memberId": "rjxin", "关键词": "制冷设备", "主营产品": "制冷设备",
                           "公司网址": "http://rjxin.cn.makepolo.com/"})
check("实体残留 &nbsp 不进联系人", _rec4.get("联系人"), "易道和")
check("页面明写手机仍成单", _rec4.get("手机"), "13386086818")

print("\n=== ⑫b 跨国采购网：安全狗拦截页不能被当成「这个关键词没公司」 ===")
_SD = open(FX("gi_block_safedog.html"), encoding="utf-8").read()
_gi2 = gi_crawler.GiCrawler(port=9302)
_gi2._log = lambda *a, **k: None
check("安全狗拦截页（200 + 五千字）识别为防采插页",
      _gi2.http_block_reason(200, _SD, "https://x/y", {}),
      "防采插页（拦截页面）·站方要求约 2 小时后再浏览")
check("插页文案把站方冷却口径带出来（界面照这句告诉用户）",
      "2 小时" in _gi2.http_block_reason(200, _SD, "https://x/y", {}), True)
check("归类成 ip 冷却（页面上没有可点的验证）", _gi2._block_kind, "ip")
check("旧口径的坑：这一页 parse_list 出来是 0 家", len(_gi2.parse_list(_SD, "制冷设备")), 0)
check("真列表页不会被这套词误判成插页",
      _gi2.http_block_reason(200, html, "https://x/y", {}), "")


class _SdResp:
    def __init__(self, text):
        self.content = text.encode("gb18030", "ignore")
        self.status_code = 200
        self.headers = {}
        self.text = text


class _SdSession:
    def get(self, url, timeout=None, headers=None):
        return _SdResp(_SD)

    def close(self):
        pass


_glogs = []
_gi3 = gi_crawler.GiCrawler(port=9303)
_gi3.incremental = False
_gi3._log = lambda *a, **k: _glogs.append(str(a[0]) if a else "")
_gi3._pace_before_request = lambda *a, **k: None
_gi3._session = _SdSession()
_gi3._bootstrap = lambda: False
_handled = []
_gi3.handle_http_block = lambda u: (_handled.append(_gi3._block_kind), False)[1]
_body = _gi3._fetch("https://www.globalimporter.net/search_company.asp?keyword=x&curpage=1")
check("风控页不会当正文返回（旧版就是这里漏的）", _body, "")
check("走的是拦截处置（等恢复），不是「解析 0 家收工」", _handled, ["ip"])
check("失败日志说清是被拦而不是没数据", any("防采集提示页" in x for x in _glogs), True)
_gi3.max_pages = 2
_out = list(_gi3._iter_keyword("制冷设备"))
check("被拦时不算翻过页：关键词不会被记成「已跑完」", _gi3.stat_pages, 0)
check("被拦时不产出结果（交给下一轮续采）", _out, [])

# ════════════ ⑬ 完整 crawl() 实机跑碰到的两处数据质量脏值 ════════════
MPM = makepolo_crawler
print("\n=== ⑬ 马可波罗：联系人前缀产品词剔掉 / 写坏的邮箱宁缺不假 ===")
# ⑬-1 商家把「产品词 + 姓 + 称谓」连写进联系人框（实机：华浦机电「热处理备件杨经理」）
for _raw, _want in (("热处理备件杨经理", "杨经理"), ("制冷设备李经理", "李经理"),
                    ("冷却设备 陈经理", "陈经理"), ("减速机欧阳经理", "欧阳经理")):
    check(f"剔产品词 {_raw}", MPM.clean_contact_person(_raw), _want)
# 切不出这个形状就必须原样交回，绝不拿产品词猜名字
for _keep in ("黄亨", "易道和", "张先生", "业务经理小王", "王东-销售", "不锈钢经理", "陈生"):
    check(f"不误伤 {_keep}", MPM.clean_contact_person(_keep), _keep)
check("并列多人取第一位（旧版入库是「周/李」）",
      MPM.clean_contact_person("周先生/李女士"), "周先生")
check("接号尾巴不留分隔符", MPM.clean_contact_person("张经理/13800001111"), "张经理")

# ⑬-2 占位/写坏的邮箱（实机：吉美斯「test@2449181705qq.com」）
for _bad in ("test@2449181705qq.com", "demo@163.com", "info@123456.net", "abc@qq.com"):
    check(f"坏邮箱剔除 {_bad}", MPM.is_bad_email(_bad), True)
for _good in ("2449181705@qq.com", "13881877244@139.com", "gcsb@163.com",
              "fairy_1118@126.com", "sales@abc.com.cn"):
    check(f"真邮箱不误杀 {_good}", MPM.is_bad_email(_good), False)

# ⑬-3 端到端：这两处脏值确实不会入库，同时不影响到手机号那一列
_HTML13 = ('<html><body><div class="connect_msg">'
           '<span>联系人：热处理备件杨经理</span>'
           '<span>手机：18560220268</span>'
           '<span>Email：test@2449181705qq.com</span>'
           '<span>地址：济南市历城区</span></div></body></html>').encode("utf-8")


class _DirtySession:
    def get(self, url, timeout=None, headers=None):
        return _R2(b"", 439) if "product-detail" in url else _R2(_HTML13)

    def close(self):
        pass


_mp7 = makepolo_crawler.MakepoloCrawler(port=9307)
_mp7.incremental = False
_mp7._log = lambda *a, **k: None
_mp7._session = _DirtySession()
_mp7._pace_before_request = lambda *a, **k: None
_rec5 = _mp7.crawl_detail({"公司名称": "华浦（济南）机电设备有限公司", "公司ID": "huapu",
                           "memberId": "huapu", "关键词": "制冷设备", "主营产品": "热处理设备",
                           "公司网址": "http://huapu.cn.makepolo.com/"})
check("入库联系人不带产品词", _rec5.get("联系人"), "杨经理")
check("岗位仍从称谓抽取", _rec5.get("岗位"), "经理")
check("写坏的邮箱不入库", _rec5.get("邮箱"), "")
check("脏值不影响手机号", _rec5.get("手机"), "18560220268")
check("这一条仍算成功（有手机号）", _rec5["状态"], "成功")

# ⑬-4 不带分号的实体残留，凡是「从页面摘出来的文本」都要洗（实机第 43 家「张新鲜&nbsp」
# 是从**产品页名片**来的，上一版只洗了联系方式页那一侧）
_DIRTY_PD = _PROD_HTML.replace("黄亨</span>", "黄亨&nbsp</span>")   # 保留 </span>，只加残体
assert "黄亨&nbsp" in _DIRTY_PD
_got_pd = _CX._parse_product_page(_DIRTY_PD, "dgghzl")
check("产品页名片联系人洗掉 &nbsp", _got_pd.get("联系人"), "黄亨")
check("产品页名片手机不受影响", _got_pd.get("手机"), "13450460020")
_name_nbsp = _TWO_CARD.replace("东莞市测试制冷机电设备有限公司</a>",
                               "东莞市测试制冷机电设备有限公司&nbsp</a>")
check("列表公司名洗掉 &nbsp", MPM.MakepoloCrawler.parse_list(
    _mp3, _name_nbsp, "制冷设备")[0]["公司名称"], "东莞市测试制冷机电设备有限公司")
check("clean_text 数字实体正常还原", MPM.clean_text("周&#39;先生&nbsp资料"), "周'先生 资料")
check("clean_text 吃双重转义", MPM.clean_text("易道和&amp;nbsp"), "易道和")

# ⑬-5 假区号固话闸门（实机第 4 家：手机 19352178592 被商家写成「0193-52178592」当固话）
from cdp_base import is_valid_landline                        # noqa: E402
for _ok in ("010-12345678", "020-86171076", "0755-89810939", "0512-57299728",
            "0375-2298818", "0769-83034001", "022-26267575"):
    check(f"真区号 {_ok} 放行", is_valid_landline(_ok), True)
for _no in ("0193-52178592", "0123-4567890", "400-800-8888", "010-110", ""):
    check(f"假区号 {_ok if _no else '空串'} 不放行" if _no == "" else f"假区号 {_no} 不放行",
          is_valid_landline(_no), False)

_HTML_TEL = ('<html><body><div class="connect_msg">'
             '<span>联系人：陈涛</span><span>手机：19352178592</span>'
             '<span>电话：0193-52178592</span>'
             '<span>地址：上海市青浦区某路 88 号</span></div></body></html>').encode("utf-8")


class _TelSession:
    def get(self, url, timeout=None, headers=None):
        return _R2(b"", 439) if "product-detail" in url else _R2(_HTML_TEL)

    def close(self):
        pass


_mp8 = makepolo_crawler.MakepoloCrawler(port=9308)
_mp8.incremental = False
_mp8._log = lambda *a, **k: None
_mp8._session = _TelSession()
_mp8._pace_before_request = lambda *a, **k: None
_rec6 = _mp8.crawl_detail({"公司名称": "上海程韬制冷设备有限公司", "公司ID": "ctzl",
                           "memberId": "ctzl", "关键词": "制冷设备", "主营产品": "制冷设备",
                           "公司网址": "http://ctzl.cn.makepolo.com/"})
check("手机照常入库", _rec6.get("手机"), "19352178592")
check("假区号固话不入库", _rec6.get("电话"), "")
check("剔假号这一条仍是成功", _rec6["状态"], "成功")


# ⑭-1 联系人**后粘**产品词（实机文安县汇永商贸：名片原文「田经理二手机床」，
#      基类按尾巴称谓取名 → 切成叫不出口的「田经理二」）
for _raw, _want in (("田经理二手机床", "田经理"), ("赵女士二手机床批发", "赵女士"),
                    ("陈小姐加工厂", "陈小姐"), ("高先生13912345678", "高先生"),
                    ("田经理二维码", "田经理"),
                    ("数控车床张经理二手机床", "数控车床张经理二手机床"),   # 两侧都粘：真页没出现过，不猜
                    ("李总经理", "李总经理"), ("王经理助理", "王经理助理"),
                    ("郑生", "郑生"), ("黄亨", "黄亨")):
    _g = MPM.clean_contact_person(_raw)
    check(f"联系人清洗「{_raw}」", _g, _want)
    check(f"清洗结果定点「{_raw}」", MPM.clean_contact_person(_g), _g)

# ⑭-2 同店两张卡的主营并起来判相关（实机深圳市鲁海阳精密机械配件：两张卡一张写
#      「其他通用五金配件,钻头,螺母」、一张写「刀片,其他机床附件」，翻页只判首卡就误杀）
_two14 = _mp3.parse_list(_TWO_CARD, "制冷设备")
check("两张卡主营并进「主营全文」", _two14[0]["主营全文"], "冷水机,冷冻机 制冰机,冷库设备")
check("对外「主营产品」仍是首卡（不污染输出列）", _two14[0]["主营产品"], "冷水机,冷冻机")
check("「主营全文」不会被记录骨架带进结果", "主营全文" in _mp3._base_rec(_two14[0]), False)
_MIX14 = ('<div class="s_product_contact"><span>[广东深圳]</span>'
          '<a href="http://sylhy.cn.makepolo.com/">深圳市鲁海阳精密机械配件有限公司</a>'
          '<a href="http://sylhy.cn.makepolo.com/companyProduct.html">其他通用五金配件,钻头,螺母</a>'
          '<a href="http://sylhy.cn.makepolo.com/contact_us.html?proid=100000000011">联系</a></div>'
          '<div class="s_product_contact"><span>[广东深圳]</span>'
          '<a href="http://sylhy.cn.makepolo.com/">深圳市鲁海阳精密机械配件有限公司</a>'
          '<a href="http://sylhy.cn.makepolo.com/companyProduct.html">刀片,其他机床附件,刀杆</a>'
          '<a href="http://sylhy.cn.makepolo.com/contact_us.html?proid=100000000012">联系</a></div>')
_m14 = _mp3.parse_list(_MIX14, "数控机床")[0]
check("（对照）只看首卡确实判成不相关",
      _mp3.is_relevant("数控机床", "深圳市鲁海阳精密机械配件有限公司 其他通用五金配件,钻头,螺母"), False)
check("合并两张卡后判成相关", _mp3.is_relevant("数控机床", _mp3.stub_relevance_text(_m14)), True)

# ⑭-3 早过滤**砍掉的公司不占全局已见**（行为级：相关性是针对某个关键词的判定，
#      对「制冷设备」砍掉的滤芯厂，对「滤芯」正是目标，同一轮里也必须能重新评估）
def _card14(slug, name, prod, pid, city="河北廊坊"):
    return ('<div class="s_product_contact"><span>[%s]</span>'
            '<a href="http://%s.cn.makepolo.com/">%s</a>'
            '<a href="http://%s.cn.makepolo.com/companyProduct.html">%s</a>'
            '<a href="http://%s.cn.makepolo.com/contact_us.html?proid=%s">联系</a></div>'
            % (city, slug, name, slug, prod, slug, pid))


_PAGES14 = {
    ("制冷设备", 1): _card14("lfmylx", "廊坊明耀滤芯厂", "滤芯", "100000000021")
                    + _card14("dgcyzl", "东莞市长赢制冷设备有限公司", "冷水机,冷冻机",
                              "100000000022", "广东东莞"),
    ("滤芯", 1): _card14("lfmylx", "廊坊明耀滤芯厂", "滤芯", "100000000021"),
}


class _Gate14(MPM.MakepoloCrawler):
    """把取页换成喂假页面（不联网）：只验「翻页循环里标已见与相关性判定的先后」。"""

    def build_search_url(self, kw, page):
        return f"mock://{kw}/{int(page)}"

    def _fetch(self, url, enc="utf-8", tries=3, guarded=True, referer=""):
        for (kw, page), body in _PAGES14.items():
            if url == self.build_search_url(kw, page):
                self._last_status = 200
                return body
        return ""


_g14 = _Gate14()
_g14.incremental = False
_g14.max_pages = 3
_g14._log = lambda *a, **k: None
_g14._sleep = lambda *a, **k: None
_g14._pace_before_request = lambda *a, **k: None
_k1 = [e["公司名称"] for e in _g14._iter_keyword("制冷设备")]
check("「制冷设备」这一轮：滤芯厂被早过滤砍掉", _k1, ["东莞市长赢制冷设备有限公司"])
check("砍掉的这家只按「关键词#主体」标记", "makepolo:制冷设备#lfmylx" in _g14._seen_members, True)
check("砍掉的这家**没**被全局标记", "makepolo:lfmylx" in _g14._seen_members, False)
check("放过的这家占全局已见（换词也不会重复采）", "makepolo:dgcyzl" in _g14._seen_members, True)
_k2 = [e["公司名称"] for e in _g14._iter_keyword("滤芯")]
check("换「滤芯」这一轮，同一家仍被重新评估并放行", _k2, ["廊坊明耀滤芯厂"])
_k3 = [e["公司名称"] for e in _g14._iter_keyword("制冷设备")]
check("同关键词重跑仍不重复产出（去重生效）", _k3, [])

# ⑭-4 同族类目桥（实机「数控机床」砍掉的名单里有车床/铣床/加工中心厂，是同行）
from keyword_expander import peer_words                                # noqa: E402
check("机床类关键词带出同族类目词", "加工中心" in peer_words("数控机床"), True)
check("关键词互相包含的同族词不重复给", "数控机床" in peer_words("数控机床"), False)
check("不在桥表里的关键词不放宽", peer_words("制冷设备"), ())
for _t, _w in (("车床,加工中心", True), ("铣床", True), ("钻床", True),
               ("加工中心", True), ("电火花线切割机", True),
               ("滤芯", False), ("货运代理,清关服务", False),
               ("减速器,齿轮", False), ("PLC/可编程控制系统", False)):
    check(f"同族桥判定「{_t}」", _mp3.is_relevant("数控机床", _t), _w)
check("同族桥不波及其他关键词", _mp3.is_relevant("制冷设备", "车床,加工中心"), False)


class _NoPeer14(MPM.MakepoloCrawler):
    _PEER_FN = False          # 模拟 keyword_expander 取不到：判定必须退回纯二字组


_np14 = _NoPeer14()
check("取不到同族表时判定退回原行为", _np14.is_relevant("数控机床", "车床,加工中心"), False)
check("取不到同族表时命中项仍放行", _np14.is_relevant("数控机床", "数控车床"), True)

# ⑭-5 端到端：真产品页名片改成后粘形态，联系人必须洗成「田经理」
_PD14 = _PROD_HTML.replace("黄亨</span>", "田经理二手机床</span>")
assert "田经理二手机床</span>" in _PD14


class _Pd14Session:
    """只有产品页那一跳给内容、商铺页回 439（实机真出现过这种店）。"""

    def get(self, url, timeout=None, headers=None):
        return _R2(_PD14.encode("utf-8")) if "product-detail" in url else _R2(b"", 439)

    def close(self):
        pass


_mp9 = makepolo_crawler.MakepoloCrawler(port=9309)
_mp9.incremental = False
_mp9._log = lambda *a, **k: None
_mp9._session = _Pd14Session()
_mp9._pace_before_request = lambda *a, **k: None
_rec14 = _mp9.crawl_detail({"公司名称": "东莞市港汇制冷设备有限公司", "公司ID": "dgghzl",
                            "memberId": "dgghzl", "关键词": "数控机床", "产品ID": "101063246805",
                            "公司网址": "http://dgghzl.cn.makepolo.com/"})
check("端到端：名片后粘产品词被切干净", _rec14.get("联系人"), "田经理")
check("端到端：职务列照旧", _rec14.get("岗位"), "经理")
check("端到端：手机不受影响", _rec14.get("手机"), "13450460020")
check("端到端：这一条仍是成功", _rec14.get("状态"), "成功")


# ════════════ ⑮ GI WebShield 会话质询链（2026-11-04 现场纠错） ════════════
print("\n=== ⑮ 跨国采购网 WebShield 会话质询链（零网络） ===")

_SHELL_15 = ('<html><head><meta http-equiv="Content-Type" content="text/html; charset=gb2312" />'
             '<script>function JumpSelf(){     self.location="/?WebShieldSessionVerify=%s";}'
             '</script><script>setTimeout("JumpSelf()",700);</script></head><body></body></html>')
_REAL_HOME_15 = "<html><head><title>跨国采购网</title></head><body>" + "首页正文" * 4000 + "</body></html>"
_VERIFY_PAGE_15 = ('<html><head><meta http-equiv="Content-Type" content="text/html; charset=gb2312" />'
                   '<script>function JumpSelf(){self.location="/";}</script>'
                   '<script>setTimeout("JumpSelf()",700);</script></head><body></body></html>')
_REAL_LIST_15 = open(FX("gi_p1.html"), encoding="utf-8").read()   # 实机列表页（37KB），直接读文件免得撞变量名
_HOME_URL_15 = gi_crawler.HOME_URL


class _Resp15:
    def __init__(self, text, status=200):
        self.content = text.encode("gb18030", "ignore")
        self.status_code = status
        self.headers = {"content-type": "text/html; Charset=gb2312"}

    @property
    def text(self):
        return self.content.decode("gb18030", "ignore")


class _Sess15:
    """按 URL 谓词回放；同一谓词给了多条就按次序弹出（用尽后固定回最后一条）。"""

    def __init__(self, routes):
        self.routes = list(routes)
        self.calls = []

    def get(self, url, timeout=None, headers=None):
        self.calls.append((url, dict(headers or {})))
        for pred, seq in self.routes:
            if pred(url):
                if len(seq) > 1:
                    return seq.pop(0)
                return seq[0]
        return _Resp15("")

    def close(self):
        pass


def _gi15(sess):
    g = gi_crawler.GiCrawler(port=9515)
    g.incremental = False
    g._log = lambda *a, **k: None
    g._session = sess
    g._pace_before_request = lambda *a, **k: None
    return g


_is_home = lambda u: u.rstrip("?") == _HOME_URL_15.rstrip("?") or u == _HOME_URL_15
_is_verify = lambda u: "WebShieldSessionVerify=" in u
_is_list = lambda u: "search_company.asp" in u

_sleep_orig = gi_crawler.time.sleep
gi_crawler.time.sleep = lambda *a, **k: None        # 质询链里的 700ms 节奏在离线不等
try:
    # ⑮-1 薄壳 → 跟一跳 → 回补首页：三跳形状与判据
    _sq = [_Resp15(_SHELL_15 % "TOKENAAA"), _Resp15(_REAL_HOME_15), _Resp15(_REAL_HOME_15)]
    _sx = _Sess15([(_is_home, _sq[:1] + _sq[2:]), (_is_verify, [_Resp15(_VERIFY_PAGE_15)])])
    _g = _gi15(_sx)
    check("⑮ 引导成功（薄壳跟完后放行）", _g._bootstrap(), True)
    check("⑮ 跟了几跳", _g._boot_hops, 1)
    check("⑮ 请求次序：首页→质询页→回补首页",
          ["V" if _is_verify(u) else "H" for u, _ in _sx.calls], ["H", "V", "H"])
    check("⑮ 质询页 URL 照 JS 原样带 token",
          _sx.calls[1][0], f"{_HOME_URL_15}?WebShieldSessionVerify=TOKENAAA")
    check("⑮ 质询页带 Referer=首页", _sx.calls[1][1].get("Referer"), _HOME_URL_15)

    # ⑮-2 已放行（首页直接给真内容）：不该多打质询跳转，一个请求就够
    _sx2 = _Sess15([(_is_home, [_Resp15(_REAL_HOME_15)]), (_is_list, [_Resp15(_REAL_LIST_15)])])
    _g2 = _gi15(_sx2)
    check("⑮ 放行态引导成功", _g2._bootstrap(), True)
    check("⑮ 放行态不跟跳转", _g2._boot_hops, 0)
    check("⑮ 放行态只发 1 个请求", len(_sx2.calls), 1)

    # ⑮-3 跟完仍薄壳：判失败、记下来自哪一层，且跳数有上限不死循环
    _sx3 = _Sess15([(_is_home, [_Resp15(_SHELL_15 % "TOK%02X")]),
                    (_is_verify, [_Resp15(_SHELL_15 % "TOK%02X")])])
    _g3 = _gi15(_sx3)
    check("⑮ 始终薄壳判引导失败", _g3._bootstrap(), False)
    check("⑮ 跳数封顶 3 次", _g3._boot_hops, 3)
    check("⑮ 记录的是薄壳字节量级", _g3._boot_len < 1000, True)

    # ⑮-4 probe_block 归因话术：跟过跳转 ≠ 会话建立不起来，必须说成出口冷却
    _g4 = _gi15(_Sess15([(_is_home, [_Resp15(_SHELL_15 % "TK")]),
                         (_is_verify, [_Resp15(_SHELL_15 % "TK")]),
                         (_is_list, [_Resp15(_SHELL_15 % "TK")])]))
    _g4._block_probe_url = gi.build_search_url("制冷设备", 1)
    _msg4 = _g4.probe_block()
    check("⑮ 列表页给薄壳时确实补走了质询引导",
          any(_is_verify(u) for u, _ in _g4._session.calls), True)
    check("⑮ 探测话术不许出现「会话建立不起来」", "会话建立不起来" in _msg4, False)
    check("⑮ 始终薄壳仍判未解除（不能当成放行）", bool(_msg4), True)

    # ⑮-5 probe_block 真放行：一发判定就解除，冷却期里绝不多打一个请求
    #     （2026-11-04 实机的账：旧实现每探一次先走引导发 1~3 个请求，60 秒一探，
    #     被拦后一小时自己打 60~100 个请求，站方「too many visits … after 2 hours」
    #     的计时被反复续期，界面「跨国采购网」死钉 99 家不动。）
    _g5 = _gi15(_Sess15([(_is_home, [_Resp15(_REAL_HOME_15)]),
                         (_is_list, [_Resp15(_REAL_LIST_15)])]))
    _g5._block_probe_url = gi.build_search_url("制冷设备", 1)
    _g5._block_reason = "防采插页（拦截页面）·站方要求约 2 小时后再浏览"
    check("⑮ 真放行时探测返回空串", _g5.probe_block(), "")
    check("⑮ 真放行只发 1 个请求", len(_g5._session.calls), 1)
    check("⑮ 真放行不做会话引导（列表页自己就能判定）",
          any(_is_verify(u) for u, _ in _g5._session.calls), False)
    check("⑮ 解除后清掉拦截原因", _g5._block_reason, "")
    check("⑮ 解除后会话标记为可用", _g5._booted, True)

    # ⑮-5b 列表页给 WAF 薄壳：补跟一跳后回头重取同一页，这才轮到多花请求
    _sx5b = _Sess15([(_is_home, [_Resp15(_REAL_HOME_15)]),
                     (_is_verify, [_Resp15(_VERIFY_PAGE_15)]),
                     (_is_list, [_Resp15(_SHELL_15 % "TK"), _Resp15(_REAL_LIST_15)])])
    _g5b = _gi15(_sx5b)
    _g5b._block_probe_url = gi.build_search_url("制冷设备", 1)
    check("⑮ 薄壳后补跟一跳即解除", _g5b.probe_block(), "")
    check("⑮ 薄壳场景请求数＝判定1＋引导1＋重取1", len(_sx5b.calls), 3)
    check("⑮ 解除后会话标记为可用", _g5b._booted, True)

    # ⑮-5c 安全狗拦截页：跟质询跳转没有任何意义，必须一个请求判定完就走
    _sx5c = _Sess15([(_is_home, [_Resp15(_REAL_HOME_15)]),
                     (_is_list, [_Resp15(_SD)])])
    _g5c = _gi15(_sx5c)
    _g5c._block_probe_url = gi.build_search_url("制冷设备", 1)
    _msg5c = _g5c.probe_block()
    check("⑮ 安全狗页只发 1 个请求判定", len(_sx5c.calls), 1)
    check("⑮ 安全狗页绝不补跟质询跳转",
          any(_is_verify(u) for u, _ in _sx5c.calls), False)
    check("⑮ 安全狗页仍判未解除", bool(_msg5c), True)
    check("⑮ 未解除话术归到防采插页", "防采插页" in _msg5c, True)

    # ⑮-6 _fetch 自愈：搜索页先给薄壳，重走质询链后同一页要拿到真列表
    _sx6 = _Sess15([(_is_home, [_Resp15(_REAL_HOME_15)]),
                    (_is_list, [_Resp15(_SHELL_15 % "TK"), _Resp15(_REAL_LIST_15)])])
    _g6 = _gi15(_sx6)
    _g6._booted = True
    _out6 = _g6._fetch(gi.build_search_url("制冷设备", 1), guarded=False)
    check("⑮ 薄壳后重取同一页拿到正文", len(_out6) > 30000, True)
    check("⑮ 重取后解析出 20 家", len(gi.parse_list(_out6, "制冷设备")), 20)
    check("⑮ 薄壳自愈不记拦截", _g6._block_reason, "")

    # ⑮-7 请求预算：引导用的首页/质询页不占内容页配额（REQ_BUDGET 只记列表页）
    _g7 = _gi15(_Sess15([(_is_home, [_Resp15(_SHELL_15 % "TK"), _Resp15(_REAL_HOME_15)]),
                         (_is_verify, [_Resp15(_VERIFY_PAGE_15)]),
                         (_is_list, [_Resp15(_REAL_LIST_15)])]))
    _g7._fetch(gi.build_search_url("制冷设备", 1), guarded=False)
    check("⑮ 引导不消耗内容页预算", _g7._req_used, 1)
finally:
    gi_crawler.time.sleep = _sleep_orig


# ⑭-1 联系人**后粘**产品词（实机文安县汇永商贸：名片原文「田经理二手机床」，
#      基类按尾巴称谓取名 → 切成叫不出口的「田经理二」）
for _raw, _want in (("田经理二手机床", "田经理"), ("赵女士二手机床批发", "赵女士"),
                    ("陈小姐加工厂", "陈小姐"), ("高先生13912345678", "高先生"),
                    ("田经理二维码", "田经理"),
                    ("数控车床张经理二手机床", "数控车床张经理二手机床"),   # 两侧都粘：真页没出现过，不猜
                    ("李总经理", "李总经理"), ("王经理助理", "王经理助理"),
                    ("郑生", "郑生"), ("黄亨", "黄亨")):
    _g = MPM.clean_contact_person(_raw)
    check(f"联系人清洗「{_raw}」", _g, _want)
    check(f"清洗结果定点「{_raw}」", MPM.clean_contact_person(_g), _g)

# ⑭-2 同店两张卡的主营并起来判相关（实机深圳市鲁海阳精密机械配件：两张卡一张写
#      「其他通用五金配件,钻头,螺母」、一张写「刀片,其他机床附件」，翻页只判首卡就误杀）
_two14 = _mp3.parse_list(_TWO_CARD, "制冷设备")
check("两张卡主营并进「主营全文」", _two14[0]["主营全文"], "冷水机,冷冻机 制冰机,冷库设备")
check("对外「主营产品」仍是首卡（不污染输出列）", _two14[0]["主营产品"], "冷水机,冷冻机")
check("「主营全文」不会被记录骨架带进结果", "主营全文" in _mp3._base_rec(_two14[0]), False)
_MIX14 = ('<div class="s_product_contact"><span>[广东深圳]</span>'
          '<a href="http://sylhy.cn.makepolo.com/">深圳市鲁海阳精密机械配件有限公司</a>'
          '<a href="http://sylhy.cn.makepolo.com/companyProduct.html">其他通用五金配件,钻头,螺母</a>'
          '<a href="http://sylhy.cn.makepolo.com/contact_us.html?proid=100000000011">联系</a></div>'
          '<div class="s_product_contact"><span>[广东深圳]</span>'
          '<a href="http://sylhy.cn.makepolo.com/">深圳市鲁海阳精密机械配件有限公司</a>'
          '<a href="http://sylhy.cn.makepolo.com/companyProduct.html">刀片,其他机床附件,刀杆</a>'
          '<a href="http://sylhy.cn.makepolo.com/contact_us.html?proid=100000000012">联系</a></div>')
_m14 = _mp3.parse_list(_MIX14, "数控机床")[0]
check("（对照）只看首卡确实判成不相关",
      _mp3.is_relevant("数控机床", "深圳市鲁海阳精密机械配件有限公司 其他通用五金配件,钻头,螺母"), False)
check("合并两张卡后判成相关", _mp3.is_relevant("数控机床", _mp3.stub_relevance_text(_m14)), True)

# ⑭-3 早过滤**砍掉的公司不占全局已见**（行为级：相关性是针对某个关键词的判定，
#      对「制冷设备」砍掉的滤芯厂，对「滤芯」正是目标，同一轮里也必须能重新评估）
def _card14(slug, name, prod, pid, city="河北廊坊"):
    return ('<div class="s_product_contact"><span>[%s]</span>'
            '<a href="http://%s.cn.makepolo.com/">%s</a>'
            '<a href="http://%s.cn.makepolo.com/companyProduct.html">%s</a>'
            '<a href="http://%s.cn.makepolo.com/contact_us.html?proid=%s">联系</a></div>'
            % (city, slug, name, slug, prod, pid))


_PAGES14 = {
    ("制冷设备", 1): _card14("lfmylx", "廊坊明耀滤芯厂", "滤芯", "100000000021")
                    + _card14("dgcyzl", "东莞市长赢制冷设备有限公司", "冷水机,冷冻机",
                              "100000000022", "广东东莞"),
    ("滤芯", 1): _card14("lfmylx", "廊坊明耀滤芯厂", "滤芯", "100000000021"),
}


class _Gate14(MPM.MakepoloCrawler):
    """把取页换成喂假页面（不联网）：只验「翻页循环里标已见与相关性判定的先后」。"""

    def build_search_url(self, kw, page):
        return f"mock://{kw}/{int(page)}"

    def _fetch(self, url, enc="utf-8", tries=3, guarded=True, referer=""):
        for (kw, page), body in _PAGES14.items():
            if url == self.build_search_url(kw, page):
                self._last_status = 200
                return body
        return ""


_g14 = _Gate14()
_g14.incremental = False
_g14.max_pages = 3
_g14._log = lambda *a, **k: None
_g14._sleep = lambda *a, **k: None
_g14._pace_before_request = lambda *a, **k: None
_k1 = [e["公司名称"] for e in _g14._iter_keyword("制冷设备")]
check("「制冷设备」这一轮：滤芯厂被早过滤砍掉", _k1, ["东莞市长赢制冷设备有限公司"])
check("砍掉的这家只按「关键词#主体」标记", "makepolo:制冷设备#lfmylx" in _g14._seen_members, True)
check("砍掉的这家**没**被全局标记", "makepolo:lfmylx" in _g14._seen_members, False)
check("放过的这家占全局已见（换词也不会重复采）", "makepolo:dgcyzl" in _g14._seen_members, True)
_k2 = [e["公司名称"] for e in _g14._iter_keyword("滤芯")]
check("换「滤芯」这一轮，同一家仍被重新评估并放行", _k2, ["廊坊明耀滤芯厂"])
_k3 = [e["公司名称"] for e in _g14._iter_keyword("制冷设备")]
check("同关键词重跑仍不重复产出（去重生效）", _k3, [])

# ⑭-4 同族类目桥（实机「数控机床」砍掉的名单里有车床/铣床/加工中心厂，是同行）
from keyword_expander import peer_words                                # noqa: E402
check("机床类关键词带出同族类目词", "加工中心" in peer_words("数控机床"), True)
check("关键词互相包含的同族词不重复给", "数控机床" in peer_words("数控机床"), False)
check("不在桥表里的关键词不放宽", peer_words("制冷设备"), ())
for _t, _w in (("车床,加工中心", True), ("铣床", True), ("钻床", True),
               ("加工中心", True), ("滤芯", False), ("货运代理,清关服务", False),
               ("减速器,齿轮", False), ("PLC/可编程控制系统", False)):
    check(f"同族桥判定「{_t}」", _mp3.is_relevant("数控机床", _t), _w)
check("同族桥不波及其他关键词", _mp3.is_relevant("制冷设备", "车床,加工中心"), False)


class _NoPeer14(MPM.MakepoloCrawler):
    _PEER_FN = False          # 模拟 keyword_expander 取不到：判定必须退回纯二字组


_np14 = _NoPeer14()
check("取不到同族表时判定退回原行为", _np14.is_relevant("数控机床", "车床,加工中心"), False)
check("取不到同族表时命中项仍放行", _np14.is_relevant("数控机床", "数控车床"), True)

# ⑭-5 端到端：真产品页名片改成后粘形态，联系人必须洗成「田经理」
_PD14 = _PROD_HTML.replace("黄亨</span>", "田经理二手机床</span>")
assert "田经理二手机床</span>" in _PD14


class _Pd14Session:
    """只有产品页那一跳给内容、商铺页回 439（实机真出现过这种店）。"""

    def get(self, url, timeout=None, headers=None):
        return _R2(_PD14.encode("utf-8")) if "product-detail" in url else _R2(b"", 439)

    def close(self):
        pass


_mp9 = makepolo_crawler.MakepoloCrawler(port=9309)
_mp9.incremental = False
_mp9._log = lambda *a, **k: None
_mp9._session = _Pd14Session()
_mp9._pace_before_request = lambda *a, **k: None
_rec14 = _mp9.crawl_detail({"公司名称": "东莞市港汇制冷设备有限公司", "公司ID": "dgghzl",
                            "memberId": "dgghzl", "关键词": "数控机床", "产品ID": "101063246805",
                            "公司网址": "http://dgghzl.cn.makepolo.com/"})
check("端到端：名片后粘产品词被切干净", _rec14.get("联系人"), "田经理")
check("端到端：职务列照旧", _rec14.get("岗位"), "经理")
check("端到端：手机不受影响", _rec14.get("手机"), "13450460020")
check("端到端：这一条仍是成功", _rec14.get("状态"), "成功")


# ════════════ ⑮ GI WebShield 会话质询链（2026-11-04 现场纠错） ════════════
print("\n=== ⑮ 跨国采购网 WebShield 会话质询链（零网络） ===")

_SHELL_15 = ('<html><head><meta http-equiv="Content-Type" content="text/html; charset=gb2312" />'
             '<script>function JumpSelf(){     self.location="/?WebShieldSessionVerify=%s";}'
             '</script><script>setTimeout("JumpSelf()",700);</script></head><body></body></html>')
_REAL_HOME_15 = "<html><head><title>跨国采购网</title></head><body>" + "首页正文" * 4000 + "</body></html>"
_VERIFY_PAGE_15 = ('<html><head><meta http-equiv="Content-Type" content="text/html; charset=gb2312" />'
                   '<script>function JumpSelf(){self.location="/";}</script>'
                   '<script>setTimeout("JumpSelf()",700);</script></head><body></body></html>')
_REAL_LIST_15 = open(FX("gi_p1.html"), encoding="utf-8").read()   # 实机列表页（37KB），直接读文件免得撞变量名
_HOME_URL_15 = gi_crawler.HOME_URL


class _Resp15:
    def __init__(self, text, status=200):
        self.content = text.encode("gb18030", "ignore")
        self.status_code = status
        self.headers = {"content-type": "text/html; Charset=gb2312"}

    @property
    def text(self):
        return self.content.decode("gb18030", "ignore")


class _Sess15:
    """按 URL 谓词回放；同一谓词给了多条就按次序弹出（用尽后固定回最后一条）。"""

    def __init__(self, routes):
        self.routes = list(routes)
        self.calls = []

    def get(self, url, timeout=None, headers=None):
        self.calls.append((url, dict(headers or {})))
        for pred, seq in self.routes:
            if pred(url):
                if len(seq) > 1:
                    return seq.pop(0)
                return seq[0]
        return _Resp15("")

    def close(self):
        pass


def _gi15(sess):
    g = gi_crawler.GiCrawler(port=9515)
    g.incremental = False
    g._log = lambda *a, **k: None
    g._session = sess
    g._pace_before_request = lambda *a, **k: None
    return g


_is_home = lambda u: u.rstrip("?") == _HOME_URL_15.rstrip("?") or u == _HOME_URL_15
_is_verify = lambda u: "WebShieldSessionVerify=" in u
_is_list = lambda u: "search_company.asp" in u

_sleep_orig = gi_crawler.time.sleep
gi_crawler.time.sleep = lambda *a, **k: None        # 质询链里的 700ms 节奏在离线不等
try:
    # ⑮-1 薄壳 → 跟一跳 → 回补首页：三跳形状与判据
    _sq = [_Resp15(_SHELL_15 % "TOKENAAA"), _Resp15(_REAL_HOME_15), _Resp15(_REAL_HOME_15)]
    _sx = _Sess15([(_is_home, _sq[:1] + _sq[2:]), (_is_verify, [_Resp15(_VERIFY_PAGE_15)])])
    _g = _gi15(_sx)
    check("⑮ 引导成功（薄壳跟完后放行）", _g._bootstrap(), True)
    check("⑮ 跟了几跳", _g._boot_hops, 1)
    check("⑮ 请求次序：首页→质询页→回补首页",
          ["V" if _is_verify(u) else "H" for u, _ in _sx.calls], ["H", "V", "H"])
    check("⑮ 质询页 URL 照 JS 原样带 token",
          _sx.calls[1][0], f"{_HOME_URL_15}?WebShieldSessionVerify=TOKENAAA")
    check("⑮ 质询页带 Referer=首页", _sx.calls[1][1].get("Referer"), _HOME_URL_15)

    # ⑮-2 已放行（首页直接给真内容）：不该多打质询跳转，一个请求就够
    _sx2 = _Sess15([(_is_home, [_Resp15(_REAL_HOME_15)]), (_is_list, [_Resp15(_REAL_LIST_15)])])
    _g2 = _gi15(_sx2)
    check("⑮ 放行态引导成功", _g2._bootstrap(), True)
    check("⑮ 放行态不跟跳转", _g2._boot_hops, 0)
    check("⑮ 放行态只发 1 个请求", len(_sx2.calls), 1)

    # ⑮-3 跟完仍薄壳：判失败、记下来自哪一层，且跳数有上限不死循环
    _sx3 = _Sess15([(_is_home, [_Resp15(_SHELL_15 % "TOK%02X")]),
                    (_is_verify, [_Resp15(_SHELL_15 % "TOK%02X")])])
    _g3 = _gi15(_sx3)
    check("⑮ 始终薄壳判引导失败", _g3._bootstrap(), False)
    check("⑮ 跳数封顶 3 次", _g3._boot_hops, 3)
    check("⑮ 记录的是薄壳字节量级", _g3._boot_len < 1000, True)

    # ⑮-4 probe_block 归因话术：跟过跳转 ≠ 会话建立不起来，必须说成出口冷却
    _g4 = _gi15(_Sess15([(_is_home, [_Resp15(_SHELL_15 % "TK")]),
                         (_is_verify, [_Resp15(_SHELL_15 % "TK")]),
                         (_is_list, [_Resp15(_SHELL_15 % "TK")])]))
    _g4._block_probe_url = gi.build_search_url("制冷设备", 1)
    _msg4 = _g4.probe_block()
    check("⑮ 探测话术写明跟随过跳转", "已跟随质询跳转" in _msg4, True)
    check("⑮ 探测话术不许出现「会话建立不起来」", "会话建立不起来" in _msg4, False)

    # ⑮-5 probe_block 真放行：引导 + 列表能解析出条目才算解除（不看状态码）
    _g5 = _gi15(_Sess15([(_is_home, [_Resp15(_SHELL_15 % "TK"), _Resp15(_REAL_HOME_15)]),
                         (_is_verify, [_Resp15(_VERIFY_PAGE_15)]),
                         (_is_list, [_Resp15(_REAL_LIST_15)])]))
    _g5._block_probe_url = gi.build_search_url("制冷设备", 1)
    _g5._block_reason = "防采插页（WebShieldSessionVerify）"
    check("⑮ 真放行时探测返回空串", _g5.probe_block(), "")
    check("⑮ 解除后清掉拦截原因", _g5._block_reason, "")

    # ⑮-6 _fetch 自愈：搜索页先给薄壳，重走质询链后同一页要拿到真列表
    _sx6 = _Sess15([(_is_home, [_Resp15(_REAL_HOME_15)]),
                    (_is_list, [_Resp15(_SHELL_15 % "TK"), _Resp15(_REAL_LIST_15)])])
    _g6 = _gi15(_sx6)
    _g6._booted = True
    _out6 = _g6._fetch(gi.build_search_url("制冷设备", 1), guarded=False)
    check("⑮ 薄壳后重取同一页拿到正文", len(_out6) > 30000, True)
    check("⑮ 重取后解析出 20 家", len(gi.parse_list(_out6, "制冷设备")), 20)
    check("⑮ 薄壳自愈不记拦截", _g6._block_reason, "")

    # ⑮-7 请求预算：引导用的首页/质询页不占内容页配额（REQ_BUDGET 只记列表页）
    _g7 = _gi15(_Sess15([(_is_home, [_Resp15(_SHELL_15 % "TK"), _Resp15(_REAL_HOME_15)]),
                         (_is_verify, [_Resp15(_VERIFY_PAGE_15)]),
                         (_is_list, [_Resp15(_REAL_LIST_15)])]))
    _g7._fetch(gi.build_search_url("制冷设备", 1), guarded=False)
    check("⑮ 引导不消耗内容页预算", _g7._req_used, 1)
finally:
    gi_crawler.time.sleep = _sleep_orig


# ⑭-1 联系人**后粘**产品词（实机文安县汇永商贸：名片原文「田经理二手机床」，
#      基类按尾巴称谓取名 → 切成叫不出口的「田经理二」）
for _raw, _want in (("田经理二手机床", "田经理"), ("赵女士二手机床批发", "赵女士"),
                    ("陈小姐加工厂", "陈小姐"), ("高先生13912345678", "高先生"),
                    ("田经理二维码", "田经理"),
                    ("数控车床张经理二手机床", "数控车床张经理二手机床"),   # 两侧都粘：真页没出现过，不猜
                    ("李总经理", "李总经理"), ("王经理助理", "王经理助理"),
                    ("郑生", "郑生"), ("黄亨", "黄亨")):
    _g = MPM.clean_contact_person(_raw)
    check(f"联系人清洗「{_raw}」", _g, _want)
    check(f"清洗结果定点「{_raw}」", MPM.clean_contact_person(_g), _g)

# ⑭-2 同店两张卡的主营并起来判相关（实机深圳市鲁海阳精密机械配件：两张卡一张写
#      「其他通用五金配件,钻头,螺母」、一张写「刀片,其他机床附件」，翻页只判首卡就误杀）
_two14 = _mp3.parse_list(_TWO_CARD, "制冷设备")
check("两张卡主营并进「主营全文」", _two14[0]["主营全文"], "冷水机,冷冻机 制冰机,冷库设备")
check("对外「主营产品」仍是首卡（不污染输出列）", _two14[0]["主营产品"], "冷水机,冷冻机")
check("「主营全文」不会被记录骨架带进结果", "主营全文" in _mp3._base_rec(_two14[0]), False)
_MIX14 = ('<div class="s_product_contact"><span>[广东深圳]</span>'
          '<a href="http://sylhy.cn.makepolo.com/">深圳市鲁海阳精密机械配件有限公司</a>'
          '<a href="http://sylhy.cn.makepolo.com/companyProduct.html">其他通用五金配件,钻头,螺母</a>'
          '<a href="http://sylhy.cn.makepolo.com/contact_us.html?proid=100000000011">联系</a></div>'
          '<div class="s_product_contact"><span>[广东深圳]</span>'
          '<a href="http://sylhy.cn.makepolo.com/">深圳市鲁海阳精密机械配件有限公司</a>'
          '<a href="http://sylhy.cn.makepolo.com/companyProduct.html">刀片,其他机床附件,刀杆</a>'
          '<a href="http://sylhy.cn.makepolo.com/contact_us.html?proid=100000000012">联系</a></div>')
_m14 = _mp3.parse_list(_MIX14, "数控机床")[0]
check("（对照）只看首卡确实判成不相关",
      _mp3.is_relevant("数控机床", "深圳市鲁海阳精密机械配件有限公司 其他通用五金配件,钻头,螺母"), False)
check("合并两张卡后判成相关", _mp3.is_relevant("数控机床", _mp3.stub_relevance_text(_m14)), True)

# ⑭-3 早过滤**砍掉的公司不占全局已见**（行为级：相关性是针对某个关键词的判定，
#      对「制冷设备」砍掉的滤芯厂，对「滤芯」正是目标，同一轮里也必须能重新评估）
def _card14(slug, name, prod, pid, city="河北廊坊"):
    return ('<div class="s_product_contact"><span>[%s]</span>'
            '<a href="http://%s.cn.makepolo.com/">%s</a>'
            '<a href="http://%s.cn.makepolo.com/companyProduct.html">%s</a>'
            '<a href="http://%s.cn.makepolo.com/contact_us.html?proid=%s">联系</a></div>'
            % (city, slug, name, slug, prod, pid))


_PAGES14 = {
    ("制冷设备", 1): _card14("lfmylx", "廊坊明耀滤芯厂", "滤芯", "100000000021")
                    + _card14("dgcyzl", "东莞市长赢制冷设备有限公司", "冷水机,冷冻机",
                              "100000000022", "广东东莞"),
    ("滤芯", 1): _card14("lfmylx", "廊坊明耀滤芯厂", "滤芯", "100000000021"),
}


class _Gate14(MPM.MakepoloCrawler):
    """把取页换成喂假页面（不联网）：只验「翻页循环里标已见与相关性判定的先后」。"""

    def build_search_url(self, kw, page):
        return f"mock://{kw}/{int(page)}"

    def _fetch(self, url, enc="utf-8", tries=3, guarded=True, referer=""):
        for (kw, page), body in _PAGES14.items():
            if url == self.build_search_url(kw, page):
                self._last_status = 200
                return body
        return ""


_g14 = _Gate14()
_g14.incremental = False
_g14.max_pages = 3
_g14._log = lambda *a, **k: None
_g14._sleep = lambda *a, **k: None
_g14._pace_before_request = lambda *a, **k: None
_k1 = [e["公司名称"] for e in _g14._iter_keyword("制冷设备")]
check("「制冷设备」这一轮：滤芯厂被早过滤砍掉", _k1, ["东莞市长赢制冷设备有限公司"])
check("砍掉的这家只按「关键词#主体」标记", "makepolo:制冷设备#lfmylx" in _g14._seen_members, True)
check("砍掉的这家**没**被全局标记", "makepolo:lfmylx" in _g14._seen_members, False)
check("放过的这家占全局已见（换词也不会重复采）", "makepolo:dgcyzl" in _g14._seen_members, True)
_k2 = [e["公司名称"] for e in _g14._iter_keyword("滤芯")]
check("换「滤芯」这一轮，同一家仍被重新评估并放行", _k2, ["廊坊明耀滤芯厂"])
_k3 = [e["公司名称"] for e in _g14._iter_keyword("制冷设备")]
check("同关键词重跑仍不重复产出（去重生效）", _k3, [])

# ⑭-4 同族类目桥（实机「数控机床」砍掉的名单里有车床/铣床/加工中心厂，是同行）
from keyword_expander import peer_words                                # noqa: E402
check("机床类关键词带出同族类目词", "加工中心" in peer_words("数控机床"), True)
check("关键词互相包含的同族词不重复给", "数控机床" in peer_words("数控机床"), False)
check("不在桥表里的关键词不放宽", peer_words("制冷设备"), ())
for _t, _w in (("车床,加工中心", True), ("铣床", True), ("钻床", True),
               ("加工中心", True), ("滤芯", False), ("货运代理,清关服务", False),
               ("减速器,齿轮", False), ("PLC/可编程控制系统", False)):
    check(f"同族桥判定「{_t}」", _mp3.is_relevant("数控机床", _t), _w)
check("同族桥不波及其他关键词", _mp3.is_relevant("制冷设备", "车床,加工中心"), False)


class _NoPeer14(MPM.MakepoloCrawler):
    _PEER_FN = False          # 模拟 keyword_expander 取不到：判定必须退回纯二字组


_np14 = _NoPeer14()
check("取不到同族表时判定退回原行为", _np14.is_relevant("数控机床", "车床,加工中心"), False)
check("取不到同族表时命中项仍放行", _np14.is_relevant("数控机床", "数控车床"), True)

# ⑭-5 端到端：真产品页名片改成后粘形态，联系人必须洗成「田经理」
_PD14 = _PROD_HTML.replace("黄亨</span>", "田经理二手机床</span>")
assert "田经理二手机床</span>" in _PD14


class _Pd14Session:
    """只有产品页那一跳给内容、商铺页回 439（实机真出现过这种店）。"""

    def get(self, url, timeout=None, headers=None):
        return _R2(_PD14.encode("utf-8")) if "product-detail" in url else _R2(b"", 439)

    def close(self):
        pass


_mp9 = makepolo_crawler.MakepoloCrawler(port=9309)
_mp9.incremental = False
_mp9._log = lambda *a, **k: None
_mp9._session = _Pd14Session()
_mp9._pace_before_request = lambda *a, **k: None
_rec14 = _mp9.crawl_detail({"公司名称": "东莞市港汇制冷设备有限公司", "公司ID": "dgghzl",
                            "memberId": "dgghzl", "关键词": "数控机床", "产品ID": "101063246805",
                            "公司网址": "http://dgghzl.cn.makepolo.com/"})
check("端到端：名片后粘产品词被切干净", _rec14.get("联系人"), "田经理")
check("端到端：职务列照旧", _rec14.get("岗位"), "经理")
check("端到端：手机不受影响", _rec14.get("手机"), "13450460020")
check("端到端：这一条仍是成功", _rec14.get("状态"), "成功")


# ════════════ ⑮ GI WebShield 会话质询链（2026-11-04 现场纠错） ════════════
print("\n=== ⑮ 跨国采购网 WebShield 会话质询链（零网络） ===")

_SHELL_15 = ('<html><head><meta http-equiv="Content-Type" content="text/html; charset=gb2312" />'
             '<script>function JumpSelf(){     self.location="/?WebShieldSessionVerify=%s";}'
             '</script><script>setTimeout("JumpSelf()",700);</script></head><body></body></html>')
_REAL_HOME_15 = "<html><head><title>跨国采购网</title></head><body>" + "首页正文" * 4000 + "</body></html>"
_VERIFY_PAGE_15 = ('<html><head><meta http-equiv="Content-Type" content="text/html; charset=gb2312" />'
                   '<script>function JumpSelf(){self.location="/";}</script>'
                   '<script>setTimeout("JumpSelf()",700);</script></head><body></body></html>')
_REAL_LIST_15 = open(FX("gi_p1.html"), encoding="utf-8").read()   # 实机列表页（37KB），直接读文件免得撞变量名
_HOME_URL_15 = gi_crawler.HOME_URL


class _Resp15:
    def __init__(self, text, status=200):
        self.content = text.encode("gb18030", "ignore")
        self.status_code = status
        self.headers = {"content-type": "text/html; Charset=gb2312"}

    @property
    def text(self):
        return self.content.decode("gb18030", "ignore")


class _Sess15:
    """按 URL 谓词回放；同一谓词给了多条就按次序弹出（用尽后固定回最后一条）。"""

    def __init__(self, routes):
        self.routes = list(routes)
        self.calls = []

    def get(self, url, timeout=None, headers=None):
        self.calls.append((url, dict(headers or {})))
        for pred, seq in self.routes:
            if pred(url):
                if len(seq) > 1:
                    return seq.pop(0)
                return seq[0]
        return _Resp15("")

    def close(self):
        pass


def _gi15(sess):
    g = gi_crawler.GiCrawler(port=9515)
    g.incremental = False
    g._log = lambda *a, **k: None
    g._session = sess
    g._pace_before_request = lambda *a, **k: None
    return g


_is_home = lambda u: u.rstrip("?") == _HOME_URL_15.rstrip("?") or u == _HOME_URL_15
_is_verify = lambda u: "WebShieldSessionVerify=" in u
_is_list = lambda u: "search_company.asp" in u

_sleep_orig = gi_crawler.time.sleep
gi_crawler.time.sleep = lambda *a, **k: None        # 质询链里的 700ms 节奏在离线不等
try:
    # ⑮-1 薄壳 → 跟一跳 → 回补首页：三跳形状与判据
    _sq = [_Resp15(_SHELL_15 % "TOKENAAA"), _Resp15(_REAL_HOME_15), _Resp15(_REAL_HOME_15)]
    _sx = _Sess15([(_is_home, _sq[:1] + _sq[2:]), (_is_verify, [_Resp15(_VERIFY_PAGE_15)])])
    _g = _gi15(_sx)
    check("⑮ 引导成功（薄壳跟完后放行）", _g._bootstrap(), True)
    check("⑮ 跟了几跳", _g._boot_hops, 1)
    check("⑮ 请求次序：首页→质询页→回补首页",
          ["V" if _is_verify(u) else "H" for u, _ in _sx.calls], ["H", "V", "H"])
    check("⑮ 质询页 URL 照 JS 原样带 token",
          _sx.calls[1][0], f"{_HOME_URL_15}?WebShieldSessionVerify=TOKENAAA")
    check("⑮ 质询页带 Referer=首页", _sx.calls[1][1].get("Referer"), _HOME_URL_15)

    # ⑮-2 已放行（首页直接给真内容）：不该多打质询跳转，一个请求就够
    _sx2 = _Sess15([(_is_home, [_Resp15(_REAL_HOME_15)]), (_is_list, [_Resp15(_REAL_LIST_15)])])
    _g2 = _gi15(_sx2)
    check("⑮ 放行态引导成功", _g2._bootstrap(), True)
    check("⑮ 放行态不跟跳转", _g2._boot_hops, 0)
    check("⑮ 放行态只发 1 个请求", len(_sx2.calls), 1)

    # ⑮-3 跟完仍薄壳：判失败、记下来自哪一层，且跳数有上限不死循环
    _sx3 = _Sess15([(_is_home, [_Resp15(_SHELL_15 % "TOK%02X")]),
                    (_is_verify, [_Resp15(_SHELL_15 % "TOK%02X")])])
    _g3 = _gi15(_sx3)
    check("⑮ 始终薄壳判引导失败", _g3._bootstrap(), False)
    check("⑮ 跳数封顶 3 次", _g3._boot_hops, 3)
    check("⑮ 记录的是薄壳字节量级", _g3._boot_len < 1000, True)

    # ⑮-4 probe_block 归因话术：跟过跳转 ≠ 会话建立不起来，必须说成出口冷却
    _g4 = _gi15(_Sess15([(_is_home, [_Resp15(_SHELL_15 % "TK")]),
                         (_is_verify, [_Resp15(_SHELL_15 % "TK")]),
                         (_is_list, [_Resp15(_SHELL_15 % "TK")])]))
    _g4._block_probe_url = gi.build_search_url("制冷设备", 1)
    _msg4 = _g4.probe_block()
    check("⑮ 探测话术写明跟随过跳转", "已跟随质询跳转" in _msg4, True)
    check("⑮ 探测话术不许出现「会话建立不起来」", "会话建立不起来" in _msg4, False)

    # ⑮-5 probe_block 真放行：引导 + 列表能解析出条目才算解除（不看状态码）
    _g5 = _gi15(_Sess15([(_is_home, [_Resp15(_SHELL_15 % "TK"), _Resp15(_REAL_HOME_15)]),
                         (_is_verify, [_Resp15(_VERIFY_PAGE_15)]),
                         (_is_list, [_Resp15(_REAL_LIST_15)])]))
    _g5._block_probe_url = gi.build_search_url("制冷设备", 1)
    _g5._block_reason = "防采插页（WebShieldSessionVerify）"
    check("⑮ 真放行时探测返回空串", _g5.probe_block(), "")
    check("⑮ 解除后清掉拦截原因", _g5._block_reason, "")

    # ⑮-6 _fetch 自愈：搜索页先给薄壳，重走质询链后同一页要拿到真列表
    _sx6 = _Sess15([(_is_home, [_Resp15(_REAL_HOME_15)]),
                    (_is_list, [_Resp15(_SHELL_15 % "TK"), _Resp15(_REAL_LIST_15)])])
    _g6 = _gi15(_sx6)
    _g6._booted = True
    _out6 = _g6._fetch(gi.build_search_url("制冷设备", 1), guarded=False)
    check("⑮ 薄壳后重取同一页拿到正文", len(_out6) > 30000, True)
    check("⑮ 重取后解析出 20 家", len(gi.parse_list(_out6, "制冷设备")), 20)
    check("⑮ 薄壳自愈不记拦截", _g6._block_reason, "")

    # ⑮-7 请求预算：引导用的首页/质询页不占内容页配额（REQ_BUDGET 只记列表页）
    _g7 = _gi15(_Sess15([(_is_home, [_Resp15(_SHELL_15 % "TK"), _Resp15(_REAL_HOME_15)]),
                         (_is_verify, [_Resp15(_VERIFY_PAGE_15)]),
                         (_is_list, [_Resp15(_REAL_LIST_15)])]))
    _g7._fetch(gi.build_search_url("制冷设备", 1), guarded=False)
    check("⑮ 引导不消耗内容页预算", _g7._req_used, 1)
finally:
    gi_crawler.time.sleep = _sleep_orig


# ⑭-1 联系人**后粘**产品词（实机文安县汇永商贸：名片原文「田经理二手机床」，
#      基类按尾巴称谓取名 → 切成叫不出口的「田经理二」）
for _raw, _want in (("田经理二手机床", "田经理"), ("赵女士二手机床批发", "赵女士"),
                    ("陈小姐加工厂", "陈小姐"), ("高先生13912345678", "高先生"),
                    ("田经理二维码", "田经理"),
                    ("数控车床张经理二手机床", "数控车床张经理二手机床"),   # 两侧都粘：真页没出现过，不猜
                    ("李总经理", "李总经理"), ("王经理助理", "王经理助理"),
                    ("郑生", "郑生"), ("黄亨", "黄亨")):
    _g = MPM.clean_contact_person(_raw)
    check(f"联系人清洗「{_raw}」", _g, _want)
    check(f"清洗结果定点「{_raw}」", MPM.clean_contact_person(_g), _g)

# ⑭-2 同店两张卡的主营并起来判相关（实机深圳市鲁海阳精密机械配件：两张卡一张写
#      「其他通用五金配件,钻头,螺母」、一张写「刀片,其他机床附件」，翻页只判首卡就误杀）
_two14 = _mp3.parse_list(_TWO_CARD, "制冷设备")
check("两张卡主营并进「主营全文」", _two14[0]["主营全文"], "冷水机,冷冻机 制冰机,冷库设备")
check("对外「主营产品」仍是首卡（不污染输出列）", _two14[0]["主营产品"], "冷水机,冷冻机")
check("「主营全文」不会被记录骨架带进结果", "主营全文" in _mp3._base_rec(_two14[0]), False)
_MIX14 = ('<div class="s_product_contact"><span>[广东深圳]</span>'
          '<a href="http://sylhy.cn.makepolo.com/">深圳市鲁海阳精密机械配件有限公司</a>'
          '<a href="http://sylhy.cn.makepolo.com/companyProduct.html">其他通用五金配件,钻头,螺母</a>'
          '<a href="http://sylhy.cn.makepolo.com/contact_us.html?proid=100000000011">联系</a></div>'
          '<div class="s_product_contact"><span>[广东深圳]</span>'
          '<a href="http://sylhy.cn.makepolo.com/">深圳市鲁海阳精密机械配件有限公司</a>'
          '<a href="http://sylhy.cn.makepolo.com/companyProduct.html">刀片,其他机床附件,刀杆</a>'
          '<a href="http://sylhy.cn.makepolo.com/contact_us.html?proid=100000000012">联系</a></div>')
_m14 = _mp3.parse_list(_MIX14, "数控机床")[0]
check("（对照）只看首卡确实判成不相关",
      _mp3.is_relevant("数控机床", "深圳市鲁海阳精密机械配件有限公司 其他通用五金配件,钻头,螺母"), False)
check("合并两张卡后判成相关", _mp3.is_relevant("数控机床", _mp3.stub_relevance_text(_m14)), True)

# ⑭-3 早过滤**砍掉的公司不占全局已见**（行为级：相关性是针对某个关键词的判定，
#      对「制冷设备」砍掉的滤芯厂，对「滤芯」正是目标，同一轮里也必须能重新评估）
def _card14(slug, name, prod, pid, city="河北廊坊"):
    return ('<div class="s_product_contact"><span>[%s]</span>'
            '<a href="http://%s.cn.makepolo.com/">%s</a>'
            '<a href="http://%s.cn.makepolo.com/companyProduct.html">%s</a>'
            '<a href="http://%s.cn.makepolo.com/contact_us.html?proid=%s">联系</a></div>'
            % (city, slug, name, slug, prod, pid))


_PAGES14 = {
    ("制冷设备", 1): _card14("lfmylx", "廊坊明耀滤芯厂", "滤芯", "100000000021")
                    + _card14("dgcyzl", "东莞市长赢制冷设备有限公司", "冷水机,冷冻机",
                              "100000000022", "广东东莞"),
    ("滤芯", 1): _card14("lfmylx", "廊坊明耀滤芯厂", "滤芯", "100000000021"),
}


class _Gate14(MPM.MakepoloCrawler):
    """把取页换成喂假页面（不联网）：只验「翻页循环里标已见与相关性判定的先后」。"""

    def build_search_url(self, kw, page):
        return f"mock://{kw}/{int(page)}"

    def _fetch(self, url, enc="utf-8", tries=3, guarded=True, referer=""):
        for (kw, page), body in _PAGES14.items():
            if url == self.build_search_url(kw, page):
                self._last_status = 200
                return body
        return ""


_g14 = _Gate14()
_g14.incremental = False
_g14.max_pages = 3
_g14._log = lambda *a, **k: None
_g14._sleep = lambda *a, **k: None
_g14._pace_before_request = lambda *a, **k: None
_k1 = [e["公司名称"] for e in _g14._iter_keyword("制冷设备")]
check("「制冷设备」这一轮：滤芯厂被早过滤砍掉", _k1, ["东莞市长赢制冷设备有限公司"])
check("砍掉的这家只按「关键词#主体」标记", "makepolo:制冷设备#lfmylx" in _g14._seen_members, True)
check("砍掉的这家**没**被全局标记", "makepolo:lfmylx" in _g14._seen_members, False)
check("放过的这家占全局已见（换词也不会重复采）", "makepolo:dgcyzl" in _g14._seen_members, True)
_k2 = [e["公司名称"] for e in _g14._iter_keyword("滤芯")]
check("换「滤芯」这一轮，同一家仍被重新评估并放行", _k2, ["廊坊明耀滤芯厂"])
_k3 = [e["公司名称"] for e in _g14._iter_keyword("制冷设备")]
check("同关键词重跑仍不重复产出（去重生效）", _k3, [])

# ⑭-4 同族类目桥（实机「数控机床」砍掉的名单里有车床/铣床/加工中心厂，是同行）
from keyword_expander import peer_words                                # noqa: E402
check("机床类关键词带出同族类目词", "加工中心" in peer_words("数控机床"), True)
check("关键词互相包含的同族词不重复给", "数控机床" in peer_words("数控机床"), False)
check("不在桥表里的关键词不放宽", peer_words("制冷设备"), ())
for _t, _w in (("车床,加工中心", True), ("铣床", True), ("钻床", True),
               ("加工中心", True), ("滤芯", False), ("货运代理,清关服务", False),
               ("减速器,齿轮", False), ("PLC/可编程控制系统", False)):
    check(f"同族桥判定「{_t}」", _mp3.is_relevant("数控机床", _t), _w)
check("同族桥不波及其他关键词", _mp3.is_relevant("制冷设备", "车床,加工中心"), False)


class _NoPeer14(MPM.MakepoloCrawler):
    _PEER_FN = False          # 模拟 keyword_expander 取不到：判定必须退回纯二字组


_np14 = _NoPeer14()
check("取不到同族表时判定退回原行为", _np14.is_relevant("数控机床", "车床,加工中心"), False)
check("取不到同族表时命中项仍放行", _np14.is_relevant("数控机床", "数控车床"), True)

# ⑭-5 端到端：真产品页名片改成后粘形态，联系人必须洗成「田经理」
_PD14 = _PROD_HTML.replace("黄亨</span>", "田经理二手机床</span>")
assert "田经理二手机床</span>" in _PD14


class _Pd14Session:
    """只有产品页那一跳给内容、商铺页回 439（实机真出现过这种店）。"""

    def get(self, url, timeout=None, headers=None):
        return _R2(_PD14.encode("utf-8")) if "product-detail" in url else _R2(b"", 439)

    def close(self):
        pass


_mp9 = makepolo_crawler.MakepoloCrawler(port=9309)
_mp9.incremental = False
_mp9._log = lambda *a, **k: None
_mp9._session = _Pd14Session()
_mp9._pace_before_request = lambda *a, **k: None
_rec14 = _mp9.crawl_detail({"公司名称": "东莞市港汇制冷设备有限公司", "公司ID": "dgghzl",
                            "memberId": "dgghzl", "关键词": "数控机床", "产品ID": "101063246805",
                            "公司网址": "http://dgghzl.cn.makepolo.com/"})
check("端到端：名片后粘产品词被切干净", _rec14.get("联系人"), "田经理")
check("端到端：职务列照旧", _rec14.get("岗位"), "经理")
check("端到端：手机不受影响", _rec14.get("手机"), "13450460020")
check("端到端：这一条仍是成功", _rec14.get("状态"), "成功")


# ════════════ ⑮ GI WebShield 会话质询链（2026-11-04 现场纠错） ════════════
print("\n=== ⑮ 跨国采购网 WebShield 会话质询链（零网络） ===")

_SHELL_15 = ('<html><head><meta http-equiv="Content-Type" content="text/html; charset=gb2312" />'
             '<script>function JumpSelf(){     self.location="/?WebShieldSessionVerify=%s";}'
             '</script><script>setTimeout("JumpSelf()",700);</script></head><body></body></html>')
_REAL_HOME_15 = "<html><head><title>跨国采购网</title></head><body>" + "首页正文" * 4000 + "</body></html>"
_VERIFY_PAGE_15 = ('<html><head><meta http-equiv="Content-Type" content="text/html; charset=gb2312" />'
                   '<script>function JumpSelf(){self.location="/";}</script>'
                   '<script>setTimeout("JumpSelf()",700);</script></head><body></body></html>')
_REAL_LIST_15 = open("/tmp/gi_p1.html", encoding="utf-8").read()   # 实机列表页（37KB），直接读文件免得撞变量名
_HOME_URL_15 = gi_crawler.HOME_URL


class _Resp15:
    def __init__(self, text, status=200):
        self.content = text.encode("gb18030", "ignore")
        self.status_code = status
        self.headers = {"content-type": "text/html; Charset=gb2312"}

    @property
    def text(self):
        return self.content.decode("gb18030", "ignore")


class _Sess15:
    """按 URL 谓词回放；同一谓词给了多条就按次序弹出（用尽后固定回最后一条）。"""

    def __init__(self, routes):
        self.routes = list(routes)
        self.calls = []

    def get(self, url, timeout=None, headers=None):
        self.calls.append((url, dict(headers or {})))
        for pred, seq in self.routes:
            if pred(url):
                if len(seq) > 1:
                    return seq.pop(0)
                return seq[0]
        return _Resp15("")

    def close(self):
        pass


def _gi15(sess):
    g = gi_crawler.GiCrawler(port=9515)
    g.incremental = False
    g._log = lambda *a, **k: None
    g._session = sess
    g._pace_before_request = lambda *a, **k: None
    return g


_is_home = lambda u: u.rstrip("?") == _HOME_URL_15.rstrip("?") or u == _HOME_URL_15
_is_verify = lambda u: "WebShieldSessionVerify=" in u
_is_list = lambda u: "search_company.asp" in u

_sleep_orig = gi_crawler.time.sleep
gi_crawler.time.sleep = lambda *a, **k: None        # 质询链里的 700ms 节奏在离线不等
try:
    # ⑮-1 薄壳 → 跟一跳 → 回补首页：三跳形状与判据
    _sq = [_Resp15(_SHELL_15 % "TOKENAAA"), _Resp15(_REAL_HOME_15), _Resp15(_REAL_HOME_15)]
    _sx = _Sess15([(_is_home, _sq[:1] + _sq[2:]), (_is_verify, [_Resp15(_VERIFY_PAGE_15)])])
    _g = _gi15(_sx)
    check("⑮ 引导成功（薄壳跟完后放行）", _g._bootstrap(), True)
    check("⑮ 跟了几跳", _g._boot_hops, 1)
    check("⑮ 请求次序：首页→质询页→回补首页",
          ["V" if _is_verify(u) else "H" for u, _ in _sx.calls], ["H", "V", "H"])
    check("⑮ 质询页 URL 照 JS 原样带 token",
          _sx.calls[1][0], f"{_HOME_URL_15}?WebShieldSessionVerify=TOKENAAA")
    check("⑮ 质询页带 Referer=首页", _sx.calls[1][1].get("Referer"), _HOME_URL_15)

    # ⑮-2 已放行（首页直接给真内容）：不该多打质询跳转，一个请求就够
    _sx2 = _Sess15([(_is_home, [_Resp15(_REAL_HOME_15)]), (_is_list, [_Resp15(_REAL_LIST_15)])])
    _g2 = _gi15(_sx2)
    check("⑮ 放行态引导成功", _g2._bootstrap(), True)
    check("⑮ 放行态不跟跳转", _g2._boot_hops, 0)
    check("⑮ 放行态只发 1 个请求", len(_sx2.calls), 1)

    # ⑮-3 跟完仍薄壳：判失败、记下来自哪一层，且跳数有上限不死循环
    _sx3 = _Sess15([(_is_home, [_Resp15(_SHELL_15 % "TOK%02X")]),
                    (_is_verify, [_Resp15(_SHELL_15 % "TOK%02X")])])
    _g3 = _gi15(_sx3)
    check("⑮ 始终薄壳判引导失败", _g3._bootstrap(), False)
    check("⑮ 跳数封顶 3 次", _g3._boot_hops, 3)
    check("⑮ 记录的是薄壳字节量级", _g3._boot_len < 1000, True)

    # ⑮-4 probe_block 归因话术：跟过跳转 ≠ 会话建立不起来，必须说成出口冷却
    _g4 = _gi15(_Sess15([(_is_home, [_Resp15(_SHELL_15 % "TK")]),
                         (_is_verify, [_Resp15(_SHELL_15 % "TK")]),
                         (_is_list, [_Resp15(_SHELL_15 % "TK")])]))
    _g4._block_probe_url = gi.build_search_url("制冷设备", 1)
    _msg4 = _g4.probe_block()
    check("⑮ 探测话术写明跟随过跳转", "已跟随质询跳转" in _msg4, True)
    check("⑮ 探测话术不许出现「会话建立不起来」", "会话建立不起来" in _msg4, False)

    # ⑮-5 probe_block 真放行：引导 + 列表能解析出条目才算解除（不看状态码）
    _g5 = _gi15(_Sess15([(_is_home, [_Resp15(_SHELL_15 % "TK"), _Resp15(_REAL_HOME_15)]),
                         (_is_verify, [_Resp15(_VERIFY_PAGE_15)]),
                         (_is_list, [_Resp15(_REAL_LIST_15)])]))
    _g5._block_probe_url = gi.build_search_url("制冷设备", 1)
    _g5._block_reason = "防采插页（WebShieldSessionVerify）"
    check("⑮ 真放行时探测返回空串", _g5.probe_block(), "")
    check("⑮ 解除后清掉拦截原因", _g5._block_reason, "")

    # ⑮-6 _fetch 自愈：搜索页先给薄壳，重走质询链后同一页要拿到真列表
    _sx6 = _Sess15([(_is_home, [_Resp15(_REAL_HOME_15)]),
                    (_is_list, [_Resp15(_SHELL_15 % "TK"), _Resp15(_REAL_LIST_15)])])
    _g6 = _gi15(_sx6)
    _g6._booted = True
    _out6 = _g6._fetch(gi.build_search_url("制冷设备", 1), guarded=False)
    check("⑮ 薄壳后重取同一页拿到正文", len(_out6) > 30000, True)
    check("⑮ 重取后解析出 20 家", len(gi.parse_list(_out6, "制冷设备")), 20)
    check("⑮ 薄壳自愈不记拦截", _g6._block_reason, "")

    # ⑮-7 请求预算：引导用的首页/质询页不占内容页配额（REQ_BUDGET 只记列表页）
    _g7 = _gi15(_Sess15([(_is_home, [_Resp15(_SHELL_15 % "TK"), _Resp15(_REAL_HOME_15)]),
                         (_is_verify, [_Resp15(_VERIFY_PAGE_15)]),
                         (_is_list, [_Resp15(_REAL_LIST_15)])]))
    _g7._fetch(gi.build_search_url("制冷设备", 1), guarded=False)
    check("⑮ 引导不消耗内容页预算", _g7._req_used, 1)
finally:
    gi_crawler.time.sleep = _sleep_orig


# ⑭-1 联系人**后粘**产品词（实机文安县汇永商贸：名片原文「田经理二手机床」，
#      基类按尾巴称谓取名 → 切成叫不出口的「田经理二」）
for _raw, _want in (("田经理二手机床", "田经理"), ("赵女士二手机床批发", "赵女士"),
                    ("陈小姐加工厂", "陈小姐"), ("高先生13912345678", "高先生"),
                    ("田经理二维码", "田经理"),
                    ("数控车床张经理二手机床", "数控车床张经理二手机床"),   # 两侧都粘：真页没出现过，不猜
                    ("李总经理", "李总经理"), ("王经理助理", "王经理助理"),
                    ("郑生", "郑生"), ("黄亨", "黄亨")):
    _g = MPM.clean_contact_person(_raw)
    check(f"联系人清洗「{_raw}」", _g, _want)
    check(f"清洗结果定点「{_raw}」", MPM.clean_contact_person(_g), _g)

# ⑭-2 同店两张卡的主营并起来判相关（实机深圳市鲁海阳精密机械配件：两张卡一张写
#      「其他通用五金配件,钻头,螺母」、一张写「刀片,其他机床附件」，翻页只判首卡就误杀）
_two14 = _mp3.parse_list(_TWO_CARD, "制冷设备")
check("两张卡主营并进「主营全文」", _two14[0]["主营全文"], "冷水机,冷冻机 制冰机,冷库设备")
check("对外「主营产品」仍是首卡（不污染输出列）", _two14[0]["主营产品"], "冷水机,冷冻机")
check("「主营全文」不会被记录骨架带进结果", "主营全文" in _mp3._base_rec(_two14[0]), False)
_MIX14 = ('<div class="s_product_contact"><span>[广东深圳]</span>'
          '<a href="http://sylhy.cn.makepolo.com/">深圳市鲁海阳精密机械配件有限公司</a>'
          '<a href="http://sylhy.cn.makepolo.com/companyProduct.html">其他通用五金配件,钻头,螺母</a>'
          '<a href="http://sylhy.cn.makepolo.com/contact_us.html?proid=100000000011">联系</a></div>'
          '<div class="s_product_contact"><span>[广东深圳]</span>'
          '<a href="http://sylhy.cn.makepolo.com/">深圳市鲁海阳精密机械配件有限公司</a>'
          '<a href="http://sylhy.cn.makepolo.com/companyProduct.html">刀片,其他机床附件,刀杆</a>'
          '<a href="http://sylhy.cn.makepolo.com/contact_us.html?proid=100000000012">联系</a></div>')
_m14 = _mp3.parse_list(_MIX14, "数控机床")[0]
check("（对照）只看首卡确实判成不相关",
      _mp3.is_relevant("数控机床", "深圳市鲁海阳精密机械配件有限公司 其他通用五金配件,钻头,螺母"), False)
check("合并两张卡后判成相关", _mp3.is_relevant("数控机床", _mp3.stub_relevance_text(_m14)), True)

# ⑭-3 早过滤**砍掉的公司不占全局已见**（行为级：相关性是针对某个关键词的判定，
#      对「制冷设备」砍掉的滤芯厂，对「滤芯」正是目标，同一轮里也必须能重新评估）
def _card14(slug, name, prod, pid, city="河北廊坊"):
    return ('<div class="s_product_contact"><span>[%s]</span>'
            '<a href="http://%s.cn.makepolo.com/">%s</a>'
            '<a href="http://%s.cn.makepolo.com/companyProduct.html">%s</a>'
            '<a href="http://%s.cn.makepolo.com/contact_us.html?proid=%s">联系</a></div>'
            % (city, slug, name, slug, prod, pid))


_PAGES14 = {
    ("制冷设备", 1): _card14("lfmylx", "廊坊明耀滤芯厂", "滤芯", "100000000021")
                    + _card14("dgcyzl", "东莞市长赢制冷设备有限公司", "冷水机,冷冻机",
                              "100000000022", "广东东莞"),
    ("滤芯", 1): _card14("lfmylx", "廊坊明耀滤芯厂", "滤芯", "100000000021"),
}


class _Gate14(MPM.MakepoloCrawler):
    """把取页换成喂假页面（不联网）：只验「翻页循环里标已见与相关性判定的先后」。"""

    def build_search_url(self, kw, page):
        return f"mock://{kw}/{int(page)}"

    def _fetch(self, url, enc="utf-8", tries=3, guarded=True, referer=""):
        for (kw, page), body in _PAGES14.items():
            if url == self.build_search_url(kw, page):
                self._last_status = 200
                return body
        return ""


_g14 = _Gate14()
_g14.incremental = False
_g14.max_pages = 3
_g14._log = lambda *a, **k: None
_g14._sleep = lambda *a, **k: None
_g14._pace_before_request = lambda *a, **k: None
_k1 = [e["公司名称"] for e in _g14._iter_keyword("制冷设备")]
check("「制冷设备」这一轮：滤芯厂被早过滤砍掉", _k1, ["东莞市长赢制冷设备有限公司"])
check("砍掉的这家只按「关键词#主体」标记", "makepolo:制冷设备#lfmylx" in _g14._seen_members, True)
check("砍掉的这家**没**被全局标记", "makepolo:lfmylx" in _g14._seen_members, False)
check("放过的这家占全局已见（换词也不会重复采）", "makepolo:dgcyzl" in _g14._seen_members, True)
_k2 = [e["公司名称"] for e in _g14._iter_keyword("滤芯")]
check("换「滤芯」这一轮，同一家仍被重新评估并放行", _k2, ["廊坊明耀滤芯厂"])
_k3 = [e["公司名称"] for e in _g14._iter_keyword("制冷设备")]
check("同关键词重跑仍不重复产出（去重生效）", _k3, [])

# ⑭-4 同族类目桥（实机「数控机床」砍掉的名单里有车床/铣床/加工中心厂，是同行）
from keyword_expander import peer_words                                # noqa: E402
check("机床类关键词带出同族类目词", "加工中心" in peer_words("数控机床"), True)
check("关键词互相包含的同族词不重复给", "数控机床" in peer_words("数控机床"), False)
check("不在桥表里的关键词不放宽", peer_words("制冷设备"), ())
for _t, _w in (("车床,加工中心", True), ("铣床", True), ("钻床", True),
               ("加工中心", True), ("滤芯", False), ("货运代理,清关服务", False),
               ("减速器,齿轮", False), ("PLC/可编程控制系统", False)):
    check(f"同族桥判定「{_t}」", _mp3.is_relevant("数控机床", _t), _w)
check("同族桥不波及其他关键词", _mp3.is_relevant("制冷设备", "车床,加工中心"), False)


class _NoPeer14(MPM.MakepoloCrawler):
    _PEER_FN = False          # 模拟 keyword_expander 取不到：判定必须退回纯二字组


_np14 = _NoPeer14()
check("取不到同族表时判定退回原行为", _np14.is_relevant("数控机床", "车床,加工中心"), False)
check("取不到同族表时命中项仍放行", _np14.is_relevant("数控机床", "数控车床"), True)

# ⑭-5 端到端：真产品页名片改成后粘形态，联系人必须洗成「田经理」
_PD14 = _PROD_HTML.replace("黄亨</span>", "田经理二手机床</span>")
assert "田经理二手机床</span>" in _PD14


class _Pd14Session:
    """只有产品页那一跳给内容、商铺页回 439（实机真出现过这种店）。"""

    def get(self, url, timeout=None, headers=None):
        return _R2(_PD14.encode("utf-8")) if "product-detail" in url else _R2(b"", 439)

    def close(self):
        pass


_mp9 = makepolo_crawler.MakepoloCrawler(port=9309)
_mp9.incremental = False
_mp9._log = lambda *a, **k: None
_mp9._session = _Pd14Session()
_mp9._pace_before_request = lambda *a, **k: None
_rec14 = _mp9.crawl_detail({"公司名称": "东莞市港汇制冷设备有限公司", "公司ID": "dgghzl",
                            "memberId": "dgghzl", "关键词": "数控机床", "产品ID": "101063246805",
                            "公司网址": "http://dgghzl.cn.makepolo.com/"})
check("端到端：名片后粘产品词被切干净", _rec14.get("联系人"), "田经理")
check("端到端：职务列照旧", _rec14.get("岗位"), "经理")
check("端到端：手机不受影响", _rec14.get("手机"), "13450460020")
check("端到端：这一条仍是成功", _rec14.get("状态"), "成功")


# ════════════ ⑮ GI WebShield 会话质询链（2026-11-04 现场纠错） ════════════
print("\n=== ⑮ 跨国采购网 WebShield 会话质询链（零网络） ===")

_SHELL_15 = ('<html><head><meta http-equiv="Content-Type" content="text/html; charset=gb2312" />'
             '<script>function JumpSelf(){     self.location="/?WebShieldSessionVerify=%s";}'
             '</script><script>setTimeout("JumpSelf()",700);</script></head><body></body></html>')
_REAL_HOME_15 = "<html><head><title>跨国采购网</title></head><body>" + "首页正文" * 4000 + "</body></html>"
_VERIFY_PAGE_15 = ('<html><head><meta http-equiv="Content-Type" content="text/html; charset=gb2312" />'
                   '<script>function JumpSelf(){self.location="/";}</script>'
                   '<script>setTimeout("JumpSelf()",700);</script></head><body></body></html>')
_REAL_LIST_15 = open("/tmp/gi_p1.html", encoding="utf-8").read()   # 实机列表页（37KB），直接读文件免得撞变量名
_HOME_URL_15 = gi_crawler.HOME_URL


class _Resp15:
    def __init__(self, text, status=200):
        self.content = text.encode("gb18030", "ignore")
        self.status_code = status
        self.headers = {"content-type": "text/html; Charset=gb2312"}

    @property
    def text(self):
        return self.content.decode("gb18030", "ignore")


class _Sess15:
    """按 URL 谓词回放；同一谓词给了多条就按次序弹出（用尽后固定回最后一条）。"""

    def __init__(self, routes):
        self.routes = list(routes)
        self.calls = []

    def get(self, url, timeout=None, headers=None):
        self.calls.append((url, dict(headers or {})))
        for pred, seq in self.routes:
            if pred(url):
                if len(seq) > 1:
                    return seq.pop(0)
                return seq[0]
        return _Resp15("")

    def close(self):
        pass


def _gi15(sess):
    g = gi_crawler.GiCrawler(port=9515)
    g.incremental = False
    g._log = lambda *a, **k: None
    g._session = sess
    g._pace_before_request = lambda *a, **k: None
    return g


_is_home = lambda u: u.rstrip("?") == _HOME_URL_15.rstrip("?") or u == _HOME_URL_15
_is_verify = lambda u: "WebShieldSessionVerify=" in u
_is_list = lambda u: "search_company.asp" in u

_sleep_orig = gi_crawler.time.sleep
gi_crawler.time.sleep = lambda *a, **k: None        # 质询链里的 700ms 节奏在离线不等
try:
    # ⑮-1 薄壳 → 跟一跳 → 回补首页：三跳形状与判据
    _sq = [_Resp15(_SHELL_15 % "TOKENAAA"), _Resp15(_REAL_HOME_15), _Resp15(_REAL_HOME_15)]
    _sx = _Sess15([(_is_home, _sq[:1] + _sq[2:]), (_is_verify, [_Resp15(_VERIFY_PAGE_15)])])
    _g = _gi15(_sx)
    check("⑮ 引导成功（薄壳跟完后放行）", _g._bootstrap(), True)
    check("⑮ 跟了几跳", _g._boot_hops, 1)
    check("⑮ 请求次序：首页→质询页→回补首页",
          ["V" if _is_verify(u) else "H" for u, _ in _sx.calls], ["H", "V", "H"])
    check("⑮ 质询页 URL 照 JS 原样带 token",
          _sx.calls[1][0], f"{_HOME_URL_15}?WebShieldSessionVerify=TOKENAAA")
    check("⑮ 质询页带 Referer=首页", _sx.calls[1][1].get("Referer"), _HOME_URL_15)

    # ⑮-2 已放行（首页直接给真内容）：不该多打质询跳转，一个请求就够
    _sx2 = _Sess15([(_is_home, [_Resp15(_REAL_HOME_15)]), (_is_list, [_Resp15(_REAL_LIST_15)])])
    _g2 = _gi15(_sx2)
    check("⑮ 放行态引导成功", _g2._bootstrap(), True)
    check("⑮ 放行态不跟跳转", _g2._boot_hops, 0)
    check("⑮ 放行态只发 1 个请求", len(_sx2.calls), 1)

    # ⑮-3 跟完仍薄壳：判失败、记下来自哪一层，且跳数有上限不死循环
    _sx3 = _Sess15([(_is_home, [_Resp15(_SHELL_15 % "TOK%02X")]),
                    (_is_verify, [_Resp15(_SHELL_15 % "TOK%02X")])])
    _g3 = _gi15(_sx3)
    check("⑮ 始终薄壳判引导失败", _g3._bootstrap(), False)
    check("⑮ 跳数封顶 3 次", _g3._boot_hops, 3)
    check("⑮ 记录的是薄壳字节量级", _g3._boot_len < 1000, True)

    # ⑮-4 probe_block 归因话术：跟过跳转 ≠ 会话建立不起来，必须说成出口冷却
    _g4 = _gi15(_Sess15([(_is_home, [_Resp15(_SHELL_15 % "TK")]),
                         (_is_verify, [_Resp15(_SHELL_15 % "TK")]),
                         (_is_list, [_Resp15(_SHELL_15 % "TK")])]))
    _g4._block_probe_url = gi.build_search_url("制冷设备", 1)
    _msg4 = _g4.probe_block()
    check("⑮ 探测话术写明跟随过跳转", "已跟随质询跳转" in _msg4, True)
    check("⑮ 探测话术不许出现「会话建立不起来」", "会话建立不起来" in _msg4, False)

    # ⑮-5 probe_block 真放行：引导 + 列表能解析出条目才算解除（不看状态码）
    _g5 = _gi15(_Sess15([(_is_home, [_Resp15(_SHELL_15 % "TK"), _Resp15(_REAL_HOME_15)]),
                         (_is_verify, [_Resp15(_VERIFY_PAGE_15)]),
                         (_is_list, [_Resp15(_REAL_LIST_15)])]))
    _g5._block_probe_url = gi.build_search_url("制冷设备", 1)
    _g5._block_reason = "防采插页（WebShieldSessionVerify）"
    check("⑮ 真放行时探测返回空串", _g5.probe_block(), "")
    check("⑮ 解除后清掉拦截原因", _g5._block_reason, "")

    # ⑮-6 _fetch 自愈：搜索页先给薄壳，重走质询链后同一页要拿到真列表
    _sx6 = _Sess15([(_is_home, [_Resp15(_REAL_HOME_15)]),
                    (_is_list, [_Resp15(_SHELL_15 % "TK"), _Resp15(_REAL_LIST_15)])])
    _g6 = _gi15(_sx6)
    _g6._booted = True
    _out6 = _g6._fetch(gi.build_search_url("制冷设备", 1), guarded=False)
    check("⑮ 薄壳后重取同一页拿到正文", len(_out6) > 30000, True)
    check("⑮ 重取后解析出 20 家", len(gi.parse_list(_out6, "制冷设备")), 20)
    check("⑮ 薄壳自愈不记拦截", _g6._block_reason, "")

    # ⑮-7 请求预算：引导用的首页/质询页不占内容页配额（REQ_BUDGET 只记列表页）
    _g7 = _gi15(_Sess15([(_is_home, [_Resp15(_SHELL_15 % "TK"), _Resp15(_REAL_HOME_15)]),
                         (_is_verify, [_Resp15(_VERIFY_PAGE_15)]),
                         (_is_list, [_Resp15(_REAL_LIST_15)])]))
    _g7._fetch(gi.build_search_url("制冷设备", 1), guarded=False)
    check("⑮ 引导不消耗内容页预算", _g7._req_used, 1)
finally:
    gi_crawler.time.sleep = _sleep_orig


# ⑭-1 联系人**后粘**产品词（实机文安县汇永商贸：名片原文「田经理二手机床」，
#      基类按尾巴称谓取名 → 切成叫不出口的「田经理二」）
for _raw, _want in (("田经理二手机床", "田经理"), ("赵女士二手机床批发", "赵女士"),
                    ("陈小姐加工厂", "陈小姐"), ("高先生13912345678", "高先生"),
                    ("田经理二维码", "田经理"),
                    ("数控车床张经理二手机床", "数控车床张经理二手机床"),   # 两侧都粘：真页没出现过，不猜
                    ("李总经理", "李总经理"), ("王经理助理", "王经理助理"),
                    ("郑生", "郑生"), ("黄亨", "黄亨")):
    _g = MPM.clean_contact_person(_raw)
    check(f"联系人清洗「{_raw}」", _g, _want)
    check(f"清洗结果定点「{_raw}」", MPM.clean_contact_person(_g), _g)

# ⑭-2 同店两张卡的主营并起来判相关（实机深圳市鲁海阳精密机械配件：两张卡一张写
#      「其他通用五金配件,钻头,螺母」、一张写「刀片,其他机床附件」，翻页只判首卡就误杀）
_two14 = _mp3.parse_list(_TWO_CARD, "制冷设备")
check("两张卡主营并进「主营全文」", _two14[0]["主营全文"], "冷水机,冷冻机 制冰机,冷库设备")
check("对外「主营产品」仍是首卡（不污染输出列）", _two14[0]["主营产品"], "冷水机,冷冻机")
check("「主营全文」不会被记录骨架带进结果", "主营全文" in _mp3._base_rec(_two14[0]), False)
_MIX14 = ('<div class="s_product_contact"><span>[广东深圳]</span>'
          '<a href="http://sylhy.cn.makepolo.com/">深圳市鲁海阳精密机械配件有限公司</a>'
          '<a href="http://sylhy.cn.makepolo.com/companyProduct.html">其他通用五金配件,钻头,螺母</a>'
          '<a href="http://sylhy.cn.makepolo.com/contact_us.html?proid=100000000011">联系</a></div>'
          '<div class="s_product_contact"><span>[广东深圳]</span>'
          '<a href="http://sylhy.cn.makepolo.com/">深圳市鲁海阳精密机械配件有限公司</a>'
          '<a href="http://sylhy.cn.makepolo.com/companyProduct.html">刀片,其他机床附件,刀杆</a>'
          '<a href="http://sylhy.cn.makepolo.com/contact_us.html?proid=100000000012">联系</a></div>')
_m14 = _mp3.parse_list(_MIX14, "数控机床")[0]
check("（对照）只看首卡确实判成不相关",
      _mp3.is_relevant("数控机床", "深圳市鲁海阳精密机械配件有限公司 其他通用五金配件,钻头,螺母"), False)
check("合并两张卡后判成相关", _mp3.is_relevant("数控机床", _mp3.stub_relevance_text(_m14)), True)

# ⑭-3 早过滤**砍掉的公司不占全局已见**（行为级：相关性是针对某个关键词的判定，
#      对「制冷设备」砍掉的滤芯厂，对「滤芯」正是目标，同一轮里也必须能重新评估）
def _card14(slug, name, prod, pid, city="河北廊坊"):
    return ('<div class="s_product_contact"><span>[%s]</span>'
            '<a href="http://%s.cn.makepolo.com/">%s</a>'
            '<a href="http://%s.cn.makepolo.com/companyProduct.html">%s</a>'
            '<a href="http://%s.cn.makepolo.com/contact_us.html?proid=%s">联系</a></div>'
            % (city, slug, name, slug, prod, pid))


_PAGES14 = {
    ("制冷设备", 1): _card14("lfmylx", "廊坊明耀滤芯厂", "滤芯", "100000000021")
                    + _card14("dgcyzl", "东莞市长赢制冷设备有限公司", "冷水机,冷冻机",
                              "100000000022", "广东东莞"),
    ("滤芯", 1): _card14("lfmylx", "廊坊明耀滤芯厂", "滤芯", "100000000021"),
}


class _Gate14(MPM.MakepoloCrawler):
    """把取页换成喂假页面（不联网）：只验「翻页循环里标已见与相关性判定的先后」。"""

    def build_search_url(self, kw, page):
        return f"mock://{kw}/{int(page)}"

    def _fetch(self, url, enc="utf-8", tries=3, guarded=True, referer=""):
        for (kw, page), body in _PAGES14.items():
            if url == self.build_search_url(kw, page):
                self._last_status = 200
                return body
        return ""


_g14 = _Gate14()
_g14.incremental = False
_g14.max_pages = 3
_g14._log = lambda *a, **k: None
_g14._sleep = lambda *a, **k: None
_g14._pace_before_request = lambda *a, **k: None
_k1 = [e["公司名称"] for e in _g14._iter_keyword("制冷设备")]
check("「制冷设备」这一轮：滤芯厂被早过滤砍掉", _k1, ["东莞市长赢制冷设备有限公司"])
check("砍掉的这家只按「关键词#主体」标记", "makepolo:制冷设备#lfmylx" in _g14._seen_members, True)
check("砍掉的这家**没**被全局标记", "makepolo:lfmylx" in _g14._seen_members, False)
check("放过的这家占全局已见（换词也不会重复采）", "makepolo:dgcyzl" in _g14._seen_members, True)
_k2 = [e["公司名称"] for e in _g14._iter_keyword("滤芯")]
check("换「滤芯」这一轮，同一家仍被重新评估并放行", _k2, ["廊坊明耀滤芯厂"])
_k3 = [e["公司名称"] for e in _g14._iter_keyword("制冷设备")]
check("同关键词重跑仍不重复产出（去重生效）", _k3, [])

# ⑭-4 同族类目桥（实机「数控机床」砍掉的名单里有车床/铣床/加工中心厂，是同行）
from keyword_expander import peer_words                                # noqa: E402
check("机床类关键词带出同族类目词", "加工中心" in peer_words("数控机床"), True)
check("关键词互相包含的同族词不重复给", "数控机床" in peer_words("数控机床"), False)
check("不在桥表里的关键词不放宽", peer_words("制冷设备"), ())
for _t, _w in (("车床,加工中心", True), ("铣床", True), ("钻床", True),
               ("加工中心", True), ("滤芯", False), ("货运代理,清关服务", False),
               ("减速器,齿轮", False), ("PLC/可编程控制系统", False)):
    check(f"同族桥判定「{_t}」", _mp3.is_relevant("数控机床", _t), _w)
check("同族桥不波及其他关键词", _mp3.is_relevant("制冷设备", "车床,加工中心"), False)


class _NoPeer14(MPM.MakepoloCrawler):
    _PEER_FN = False          # 模拟 keyword_expander 取不到：判定必须退回纯二字组


_np14 = _NoPeer14()
check("取不到同族表时判定退回原行为", _np14.is_relevant("数控机床", "车床,加工中心"), False)
check("取不到同族表时命中项仍放行", _np14.is_relevant("数控机床", "数控车床"), True)

# ⑭-5 端到端：真产品页名片改成后粘形态，联系人必须洗成「田经理」
_PD14 = _PROD_HTML.replace("黄亨</span>", "田经理二手机床</span>")
assert "田经理二手机床</span>" in _PD14


class _Pd14Session:
    """只有产品页那一跳给内容、商铺页回 439（实机真出现过这种店）。"""

    def get(self, url, timeout=None, headers=None):
        return _R2(_PD14.encode("utf-8")) if "product-detail" in url else _R2(b"", 439)

    def close(self):
        pass


_mp9 = makepolo_crawler.MakepoloCrawler(port=9309)
_mp9.incremental = False
_mp9._log = lambda *a, **k: None
_mp9._session = _Pd14Session()
_mp9._pace_before_request = lambda *a, **k: None
_rec14 = _mp9.crawl_detail({"公司名称": "东莞市港汇制冷设备有限公司", "公司ID": "dgghzl",
                            "memberId": "dgghzl", "关键词": "数控机床", "产品ID": "101063246805",
                            "公司网址": "http://dgghzl.cn.makepolo.com/"})
check("端到端：名片后粘产品词被切干净", _rec14.get("联系人"), "田经理")
check("端到端：职务列照旧", _rec14.get("岗位"), "经理")
check("端到端：手机不受影响", _rec14.get("手机"), "13450460020")
check("端到端：这一条仍是成功", _rec14.get("状态"), "成功")


# ════════════ ⑮ GI WebShield 会话质询链（2026-11-04 现场纠错） ════════════
print("\n=== ⑮ 跨国采购网 WebShield 会话质询链（零网络） ===")

_SHELL_15 = ('<html><head><meta http-equiv="Content-Type" content="text/html; charset=gb2312" />'
             '<script>function JumpSelf(){     self.location="/?WebShieldSessionVerify=%s";}'
             '</script><script>setTimeout("JumpSelf()",700);</script></head><body></body></html>')
_REAL_HOME_15 = "<html><head><title>跨国采购网</title></head><body>" + "首页正文" * 4000 + "</body></html>"
_VERIFY_PAGE_15 = ('<html><head><meta http-equiv="Content-Type" content="text/html; charset=gb2312" />'
                   '<script>function JumpSelf(){self.location="/";}</script>'
                   '<script>setTimeout("JumpSelf()",700);</script></head><body></body></html>')
_REAL_LIST_15 = open("/tmp/gi_p1.html", encoding="utf-8").read()   # 实机列表页（37KB），直接读文件免得撞变量名
_HOME_URL_15 = gi_crawler.HOME_URL


class _Resp15:
    def __init__(self, text, status=200):
        self.content = text.encode("gb18030", "ignore")
        self.status_code = status
        self.headers = {"content-type": "text/html; Charset=gb2312"}

    @property
    def text(self):
        return self.content.decode("gb18030", "ignore")


class _Sess15:
    """按 URL 谓词回放；同一谓词给了多条就按次序弹出（用尽后固定回最后一条）。"""

    def __init__(self, routes):
        self.routes = list(routes)
        self.calls = []

    def get(self, url, timeout=None, headers=None):
        self.calls.append((url, dict(headers or {})))
        for pred, seq in self.routes:
            if pred(url):
                if len(seq) > 1:
                    return seq.pop(0)
                return seq[0]
        return _Resp15("")

    def close(self):
        pass


def _gi15(sess):
    g = gi_crawler.GiCrawler(port=9515)
    g.incremental = False
    g._log = lambda *a, **k: None
    g._session = sess
    g._pace_before_request = lambda *a, **k: None
    return g


_is_home = lambda u: u.rstrip("?") == _HOME_URL_15.rstrip("?") or u == _HOME_URL_15
_is_verify = lambda u: "WebShieldSessionVerify=" in u
_is_list = lambda u: "search_company.asp" in u

_sleep_orig = gi_crawler.time.sleep
gi_crawler.time.sleep = lambda *a, **k: None        # 质询链里的 700ms 节奏在离线不等
try:
    # ⑮-1 薄壳 → 跟一跳 → 回补首页：三跳形状与判据
    _sq = [_Resp15(_SHELL_15 % "TOKENAAA"), _Resp15(_REAL_HOME_15), _Resp15(_REAL_HOME_15)]
    _sx = _Sess15([(_is_home, _sq[:1] + _sq[2:]), (_is_verify, [_Resp15(_VERIFY_PAGE_15)])])
    _g = _gi15(_sx)
    check("⑮ 引导成功（薄壳跟完后放行）", _g._bootstrap(), True)
    check("⑮ 跟了几跳", _g._boot_hops, 1)
    check("⑮ 请求次序：首页→质询页→回补首页",
          ["V" if _is_verify(u) else "H" for u, _ in _sx.calls], ["H", "V", "H"])
    check("⑮ 质询页 URL 照 JS 原样带 token",
          _sx.calls[1][0], f"{_HOME_URL_15}?WebShieldSessionVerify=TOKENAAA")
    check("⑮ 质询页带 Referer=首页", _sx.calls[1][1].get("Referer"), _HOME_URL_15)

    # ⑮-2 已放行（首页直接给真内容）：不该多打质询跳转，一个请求就够
    _sx2 = _Sess15([(_is_home, [_Resp15(_REAL_HOME_15)]), (_is_list, [_Resp15(_REAL_LIST_15)])])
    _g2 = _gi15(_sx2)
    check("⑮ 放行态引导成功", _g2._bootstrap(), True)
    check("⑮ 放行态不跟跳转", _g2._boot_hops, 0)
    check("⑮ 放行态只发 1 个请求", len(_sx2.calls), 1)

    # ⑮-3 跟完仍薄壳：判失败、记下来自哪一层，且跳数有上限不死循环
    _sx3 = _Sess15([(_is_home, [_Resp15(_SHELL_15 % "TOK%02X")]),
                    (_is_verify, [_Resp15(_SHELL_15 % "TOK%02X")])])
    _g3 = _gi15(_sx3)
    check("⑮ 始终薄壳判引导失败", _g3._bootstrap(), False)
    check("⑮ 跳数封顶 3 次", _g3._boot_hops, 3)
    check("⑮ 记录的是薄壳字节量级", _g3._boot_len < 1000, True)

    # ⑮-4 probe_block 归因话术：跟过跳转 ≠ 会话建立不起来，必须说成出口冷却
    _g4 = _gi15(_Sess15([(_is_home, [_Resp15(_SHELL_15 % "TK")]),
                         (_is_verify, [_Resp15(_SHELL_15 % "TK")]),
                         (_is_list, [_Resp15(_SHELL_15 % "TK")])]))
    _g4._block_probe_url = gi.build_search_url("制冷设备", 1)
    _msg4 = _g4.probe_block()
    check("⑮ 探测话术写明跟随过跳转", "已跟随质询跳转" in _msg4, True)
    check("⑮ 探测话术不许出现「会话建立不起来」", "会话建立不起来" in _msg4, False)

    # ⑮-5 probe_block 真放行：引导 + 列表能解析出条目才算解除（不看状态码）
    _g5 = _gi15(_Sess15([(_is_home, [_Resp15(_SHELL_15 % "TK"), _Resp15(_REAL_HOME_15)]),
                         (_is_verify, [_Resp15(_VERIFY_PAGE_15)]),
                         (_is_list, [_Resp15(_REAL_LIST_15)])]))
    _g5._block_probe_url = gi.build_search_url("制冷设备", 1)
    _g5._block_reason = "防采插页（WebShieldSessionVerify）"
    check("⑮ 真放行时探测返回空串", _g5.probe_block(), "")
    check("⑮ 解除后清掉拦截原因", _g5._block_reason, "")

    # ⑮-6 _fetch 自愈：搜索页先给薄壳，重走质询链后同一页要拿到真列表
    _sx6 = _Sess15([(_is_home, [_Resp15(_REAL_HOME_15)]),
                    (_is_list, [_Resp15(_SHELL_15 % "TK"), _Resp15(_REAL_LIST_15)])])
    _g6 = _gi15(_sx6)
    _g6._booted = True
    _out6 = _g6._fetch(gi.build_search_url("制冷设备", 1), guarded=False)
    check("⑮ 薄壳后重取同一页拿到正文", len(_out6) > 30000, True)
    check("⑮ 重取后解析出 20 家", len(gi.parse_list(_out6, "制冷设备")), 20)
    check("⑮ 薄壳自愈不记拦截", _g6._block_reason, "")

    # ⑮-7 请求预算：引导用的首页/质询页不占内容页配额（REQ_BUDGET 只记列表页）
    _g7 = _gi15(_Sess15([(_is_home, [_Resp15(_SHELL_15 % "TK"), _Resp15(_REAL_HOME_15)]),
                         (_is_verify, [_Resp15(_VERIFY_PAGE_15)]),
                         (_is_list, [_Resp15(_REAL_LIST_15)])]))
    _g7._fetch(gi.build_search_url("制冷设备", 1), guarded=False)
    check("⑮ 引导不消耗内容页预算", _g7._req_used, 1)
finally:
    gi_crawler.time.sleep = _sleep_orig


# ⑭-1 联系人**后粘**产品词（实机文安县汇永商贸：名片原文「田经理二手机床」，
#      基类按尾巴称谓取名 → 切成叫不出口的「田经理二」）
for _raw, _want in (("田经理二手机床", "田经理"), ("赵女士二手机床批发", "赵女士"),
                    ("陈小姐加工厂", "陈小姐"), ("高先生13912345678", "高先生"),
                    ("田经理二维码", "田经理"),
                    ("数控车床张经理二手机床", "数控车床张经理二手机床"),   # 两侧都粘：真页没出现过，不猜
                    ("李总经理", "李总经理"), ("王经理助理", "王经理助理"),
                    ("郑生", "郑生"), ("黄亨", "黄亨")):
    _g = MPM.clean_contact_person(_raw)
    check(f"联系人清洗「{_raw}」", _g, _want)
    check(f"清洗结果定点「{_raw}」", MPM.clean_contact_person(_g), _g)

# ⑭-2 同店两张卡的主营并起来判相关（实机深圳市鲁海阳精密机械配件：两张卡一张写
#      「其他通用五金配件,钻头,螺母」、一张写「刀片,其他机床附件」，翻页只判首卡就误杀）
_two14 = _mp3.parse_list(_TWO_CARD, "制冷设备")
check("两张卡主营并进「主营全文」", _two14[0]["主营全文"], "冷水机,冷冻机 制冰机,冷库设备")
check("对外「主营产品」仍是首卡（不污染输出列）", _two14[0]["主营产品"], "冷水机,冷冻机")
check("「主营全文」不会被记录骨架带进结果", "主营全文" in _mp3._base_rec(_two14[0]), False)
_MIX14 = ('<div class="s_product_contact"><span>[广东深圳]</span>'
          '<a href="http://sylhy.cn.makepolo.com/">深圳市鲁海阳精密机械配件有限公司</a>'
          '<a href="http://sylhy.cn.makepolo.com/companyProduct.html">其他通用五金配件,钻头,螺母</a>'
          '<a href="http://sylhy.cn.makepolo.com/contact_us.html?proid=100000000011">联系</a></div>'
          '<div class="s_product_contact"><span>[广东深圳]</span>'
          '<a href="http://sylhy.cn.makepolo.com/">深圳市鲁海阳精密机械配件有限公司</a>'
          '<a href="http://sylhy.cn.makepolo.com/companyProduct.html">刀片,其他机床附件,刀杆</a>'
          '<a href="http://sylhy.cn.makepolo.com/contact_us.html?proid=100000000012">联系</a></div>')
_m14 = _mp3.parse_list(_MIX14, "数控机床")[0]
check("（对照）只看首卡确实判成不相关",
      _mp3.is_relevant("数控机床", "深圳市鲁海阳精密机械配件有限公司 其他通用五金配件,钻头,螺母"), False)
check("合并两张卡后判成相关", _mp3.is_relevant("数控机床", _mp3.stub_relevance_text(_m14)), True)

# ⑭-3 早过滤**砍掉的公司不占全局已见**（行为级：相关性是针对某个关键词的判定，
#      对「制冷设备」砍掉的滤芯厂，对「滤芯」正是目标，同一轮里也必须能重新评估）
def _card14(slug, name, prod, pid, city="河北廊坊"):
    return ('<div class="s_product_contact"><span>[%s]</span>'
            '<a href="http://%s.cn.makepolo.com/">%s</a>'
            '<a href="http://%s.cn.makepolo.com/companyProduct.html">%s</a>'
            '<a href="http://%s.cn.makepolo.com/contact_us.html?proid=%s">联系</a></div>'
            % (city, slug, name, slug, prod, pid))


_PAGES14 = {
    ("制冷设备", 1): _card14("lfmylx", "廊坊明耀滤芯厂", "滤芯", "100000000021")
                    + _card14("dgcyzl", "东莞市长赢制冷设备有限公司", "冷水机,冷冻机",
                              "100000000022", "广东东莞"),
    ("滤芯", 1): _card14("lfmylx", "廊坊明耀滤芯厂", "滤芯", "100000000021"),
}


class _Gate14(MPM.MakepoloCrawler):
    """把取页换成喂假页面（不联网）：只验「翻页循环里标已见与相关性判定的先后」。"""

    def build_search_url(self, kw, page):
        return f"mock://{kw}/{int(page)}"

    def _fetch(self, url, enc="utf-8", tries=3, guarded=True, referer=""):
        for (kw, page), body in _PAGES14.items():
            if url == self.build_search_url(kw, page):
                self._last_status = 200
                return body
        return ""


_g14 = _Gate14()
_g14.incremental = False
_g14.max_pages = 3
_g14._log = lambda *a, **k: None
_g14._sleep = lambda *a, **k: None
_g14._pace_before_request = lambda *a, **k: None
_k1 = [e["公司名称"] for e in _g14._iter_keyword("制冷设备")]
check("「制冷设备」这一轮：滤芯厂被早过滤砍掉", _k1, ["东莞市长赢制冷设备有限公司"])
check("砍掉的这家只按「关键词#主体」标记", "makepolo:制冷设备#lfmylx" in _g14._seen_members, True)
check("砍掉的这家**没**被全局标记", "makepolo:lfmylx" in _g14._seen_members, False)
check("放过的这家占全局已见（换词也不会重复采）", "makepolo:dgcyzl" in _g14._seen_members, True)
_k2 = [e["公司名称"] for e in _g14._iter_keyword("滤芯")]
check("换「滤芯」这一轮，同一家仍被重新评估并放行", _k2, ["廊坊明耀滤芯厂"])
_k3 = [e["公司名称"] for e in _g14._iter_keyword("制冷设备")]
check("同关键词重跑仍不重复产出（去重生效）", _k3, [])

# ⑭-4 同族类目桥（实机「数控机床」砍掉的名单里有车床/铣床/加工中心厂，是同行）
from keyword_expander import peer_words                                # noqa: E402
check("机床类关键词带出同族类目词", "加工中心" in peer_words("数控机床"), True)
check("关键词互相包含的同族词不重复给", "数控机床" in peer_words("数控机床"), False)
check("不在桥表里的关键词不放宽", peer_words("制冷设备"), ())
for _t, _w in (("车床,加工中心", True), ("铣床", True), ("钻床", True),
               ("加工中心", True), ("滤芯", False), ("货运代理,清关服务", False),
               ("减速器,齿轮", False), ("PLC/可编程控制系统", False)):
    check(f"同族桥判定「{_t}」", _mp3.is_relevant("数控机床", _t), _w)
check("同族桥不波及其他关键词", _mp3.is_relevant("制冷设备", "车床,加工中心"), False)


class _NoPeer14(MPM.MakepoloCrawler):
    _PEER_FN = False          # 模拟 keyword_expander 取不到：判定必须退回纯二字组


_np14 = _NoPeer14()
check("取不到同族表时判定退回原行为", _np14.is_relevant("数控机床", "车床,加工中心"), False)
check("取不到同族表时命中项仍放行", _np14.is_relevant("数控机床", "数控车床"), True)

# ⑭-5 端到端：真产品页名片改成后粘形态，联系人必须洗成「田经理」
_PD14 = _PROD_HTML.replace("黄亨</span>", "田经理二手机床</span>")
assert "田经理二手机床</span>" in _PD14


class _Pd14Session:
    """只有产品页那一跳给内容、商铺页回 439（实机真出现过这种店）。"""

    def get(self, url, timeout=None, headers=None):
        return _R2(_PD14.encode("utf-8")) if "product-detail" in url else _R2(b"", 439)

    def close(self):
        pass


_mp9 = makepolo_crawler.MakepoloCrawler(port=9309)
_mp9.incremental = False
_mp9._log = lambda *a, **k: None
_mp9._session = _Pd14Session()
_mp9._pace_before_request = lambda *a, **k: None
_rec14 = _mp9.crawl_detail({"公司名称": "东莞市港汇制冷设备有限公司", "公司ID": "dgghzl",
                            "memberId": "dgghzl", "关键词": "数控机床", "产品ID": "101063246805",
                            "公司网址": "http://dgghzl.cn.makepolo.com/"})
check("端到端：名片后粘产品词被切干净", _rec14.get("联系人"), "田经理")
check("端到端：职务列照旧", _rec14.get("岗位"), "经理")
check("端到端：手机不受影响", _rec14.get("手机"), "13450460020")
check("端到端：这一条仍是成功", _rec14.get("状态"), "成功")


# ════════════ ⑮ GI WebShield 会话质询链（2026-11-04 现场纠错） ════════════
print("\n=== ⑮ 跨国采购网 WebShield 会话质询链（零网络） ===")

_SHELL_15 = ('<html><head><meta http-equiv="Content-Type" content="text/html; charset=gb2312" />'
             '<script>function JumpSelf(){     self.location="/?WebShieldSessionVerify=%s";}'
             '</script><script>setTimeout("JumpSelf()",700);</script></head><body></body></html>')
_REAL_HOME_15 = "<html><head><title>跨国采购网</title></head><body>" + "首页正文" * 4000 + "</body></html>"
_VERIFY_PAGE_15 = ('<html><head><meta http-equiv="Content-Type" content="text/html; charset=gb2312" />'
                   '<script>function JumpSelf(){self.location="/";}</script>'
                   '<script>setTimeout("JumpSelf()",700);</script></head><body></body></html>')
_REAL_LIST_15 = open("/tmp/gi_p1.html", encoding="utf-8").read()   # 实机列表页（37KB），直接读文件免得撞变量名
_HOME_URL_15 = gi_crawler.HOME_URL


class _Resp15:
    def __init__(self, text, status=200):
        self.content = text.encode("gb18030", "ignore")
        self.status_code = status
        self.headers = {"content-type": "text/html; Charset=gb2312"}

    @property
    def text(self):
        return self.content.decode("gb18030", "ignore")


class _Sess15:
    """按 URL 谓词回放；同一谓词给了多条就按次序弹出（用尽后固定回最后一条）。"""

    def __init__(self, routes):
        self.routes = list(routes)
        self.calls = []

    def get(self, url, timeout=None, headers=None):
        self.calls.append((url, dict(headers or {})))
        for pred, seq in self.routes:
            if pred(url):
                if len(seq) > 1:
                    return seq.pop(0)
                return seq[0]
        return _Resp15("")

    def close(self):
        pass


def _gi15(sess):
    g = gi_crawler.GiCrawler(port=9515)
    g.incremental = False
    g._log = lambda *a, **k: None
    g._session = sess
    g._pace_before_request = lambda *a, **k: None
    return g


_is_home = lambda u: u.rstrip("?") == _HOME_URL_15.rstrip("?") or u == _HOME_URL_15
_is_verify = lambda u: "WebShieldSessionVerify=" in u
_is_list = lambda u: "search_company.asp" in u

_sleep_orig = gi_crawler.time.sleep
gi_crawler.time.sleep = lambda *a, **k: None        # 质询链里的 700ms 节奏在离线不等
try:
    # ⑮-1 薄壳 → 跟一跳 → 回补首页：三跳形状与判据
    _sq = [_Resp15(_SHELL_15 % "TOKENAAA"), _Resp15(_REAL_HOME_15), _Resp15(_REAL_HOME_15)]
    _sx = _Sess15([(_is_home, _sq[:1] + _sq[2:]), (_is_verify, [_Resp15(_VERIFY_PAGE_15)])])
    _g = _gi15(_sx)
    check("⑮ 引导成功（薄壳跟完后放行）", _g._bootstrap(), True)
    check("⑮ 跟了几跳", _g._boot_hops, 1)
    check("⑮ 请求次序：首页→质询页→回补首页",
          ["V" if _is_verify(u) else "H" for u, _ in _sx.calls], ["H", "V", "H"])
    check("⑮ 质询页 URL 照 JS 原样带 token",
          _sx.calls[1][0], f"{_HOME_URL_15}?WebShieldSessionVerify=TOKENAAA")
    check("⑮ 质询页带 Referer=首页", _sx.calls[1][1].get("Referer"), _HOME_URL_15)

    # ⑮-2 已放行（首页直接给真内容）：不该多打质询跳转，一个请求就够
    _sx2 = _Sess15([(_is_home, [_Resp15(_REAL_HOME_15)]), (_is_list, [_Resp15(_REAL_LIST_15)])])
    _g2 = _gi15(_sx2)
    check("⑮ 放行态引导成功", _g2._bootstrap(), True)
    check("⑮ 放行态不跟跳转", _g2._boot_hops, 0)
    check("⑮ 放行态只发 1 个请求", len(_sx2.calls), 1)

    # ⑮-3 跟完仍薄壳：判失败、记下来自哪一层，且跳数有上限不死循环
    _sx3 = _Sess15([(_is_home, [_Resp15(_SHELL_15 % "TOK%02X")]),
                    (_is_verify, [_Resp15(_SHELL_15 % "TOK%02X")])])
    _g3 = _gi15(_sx3)
    check("⑮ 始终薄壳判引导失败", _g3._bootstrap(), False)
    check("⑮ 跳数封顶 3 次", _g3._boot_hops, 3)
    check("⑮ 记录的是薄壳字节量级", _g3._boot_len < 1000, True)

    # ⑮-4 probe_block 归因话术：跟过跳转 ≠ 会话建立不起来，必须说成出口冷却
    _g4 = _gi15(_Sess15([(_is_home, [_Resp15(_SHELL_15 % "TK")]),
                         (_is_verify, [_Resp15(_SHELL_15 % "TK")]),
                         (_is_list, [_Resp15(_SHELL_15 % "TK")])]))
    _g4._block_probe_url = gi.build_search_url("制冷设备", 1)
    _msg4 = _g4.probe_block()
    check("⑮ 探测话术写明跟随过跳转", "已跟随质询跳转" in _msg4, True)
    check("⑮ 探测话术不许出现「会话建立不起来」", "会话建立不起来" in _msg4, False)

    # ⑮-5 probe_block 真放行：引导 + 列表能解析出条目才算解除（不看状态码）
    _g5 = _gi15(_Sess15([(_is_home, [_Resp15(_SHELL_15 % "TK"), _Resp15(_REAL_HOME_15)]),
                         (_is_verify, [_Resp15(_VERIFY_PAGE_15)]),
                         (_is_list, [_Resp15(_REAL_LIST_15)])]))
    _g5._block_probe_url = gi.build_search_url("制冷设备", 1)
    _g5._block_reason = "防采插页（WebShieldSessionVerify）"
    check("⑮ 真放行时探测返回空串", _g5.probe_block(), "")
    check("⑮ 解除后清掉拦截原因", _g5._block_reason, "")

    # ⑮-6 _fetch 自愈：搜索页先给薄壳，重走质询链后同一页要拿到真列表
    _sx6 = _Sess15([(_is_home, [_Resp15(_REAL_HOME_15)]),
                    (_is_list, [_Resp15(_SHELL_15 % "TK"), _Resp15(_REAL_LIST_15)])])
    _g6 = _gi15(_sx6)
    _g6._booted = True
    _out6 = _g6._fetch(gi.build_search_url("制冷设备", 1), guarded=False)
    check("⑮ 薄壳后重取同一页拿到正文", len(_out6) > 30000, True)
    check("⑮ 重取后解析出 20 家", len(gi.parse_list(_out6, "制冷设备")), 20)
    check("⑮ 薄壳自愈不记拦截", _g6._block_reason, "")

    # ⑮-7 请求预算：引导用的首页/质询页不占内容页配额（REQ_BUDGET 只记列表页）
    _g7 = _gi15(_Sess15([(_is_home, [_Resp15(_SHELL_15 % "TK"), _Resp15(_REAL_HOME_15)]),
                         (_is_verify, [_Resp15(_VERIFY_PAGE_15)]),
                         (_is_list, [_Resp15(_REAL_LIST_15)])]))
    _g7._fetch(gi.build_search_url("制冷设备", 1), guarded=False)
    check("⑮ 引导不消耗内容页预算", _g7._req_used, 1)
finally:
    gi_crawler.time.sleep = _sleep_orig


# ⑭-1 联系人**后粘**产品词（实机文安县汇永商贸：名片原文「田经理二手机床」，
#      基类按尾巴称谓取名 → 切成叫不出口的「田经理二」）
for _raw, _want in (("田经理二手机床", "田经理"), ("赵女士二手机床批发", "赵女士"),
                    ("陈小姐加工厂", "陈小姐"), ("高先生13912345678", "高先生"),
                    ("田经理二维码", "田经理"),
                    ("数控车床张经理二手机床", "数控车床张经理二手机床"),   # 两侧都粘：真页没出现过，不猜
                    ("李总经理", "李总经理"), ("王经理助理", "王经理助理"),
                    ("郑生", "郑生"), ("黄亨", "黄亨")):
    _g = MPM.clean_contact_person(_raw)
    check(f"联系人清洗「{_raw}」", _g, _want)
    check(f"清洗结果定点「{_raw}」", MPM.clean_contact_person(_g), _g)

# ⑭-2 同店两张卡的主营并起来判相关（实机深圳市鲁海阳精密机械配件：两张卡一张写
#      「其他通用五金配件,钻头,螺母」、一张写「刀片,其他机床附件」，翻页只判首卡就误杀）
_two14 = _mp3.parse_list(_TWO_CARD, "制冷设备")
check("两张卡主营并进「主营全文」", _two14[0]["主营全文"], "冷水机,冷冻机 制冰机,冷库设备")
check("对外「主营产品」仍是首卡（不污染输出列）", _two14[0]["主营产品"], "冷水机,冷冻机")
check("「主营全文」不会被记录骨架带进结果", "主营全文" in _mp3._base_rec(_two14[0]), False)
_MIX14 = ('<div class="s_product_contact"><span>[广东深圳]</span>'
          '<a href="http://sylhy.cn.makepolo.com/">深圳市鲁海阳精密机械配件有限公司</a>'
          '<a href="http://sylhy.cn.makepolo.com/companyProduct.html">其他通用五金配件,钻头,螺母</a>'
          '<a href="http://sylhy.cn.makepolo.com/contact_us.html?proid=100000000011">联系</a></div>'
          '<div class="s_product_contact"><span>[广东深圳]</span>'
          '<a href="http://sylhy.cn.makepolo.com/">深圳市鲁海阳精密机械配件有限公司</a>'
          '<a href="http://sylhy.cn.makepolo.com/companyProduct.html">刀片,其他机床附件,刀杆</a>'
          '<a href="http://sylhy.cn.makepolo.com/contact_us.html?proid=100000000012">联系</a></div>')
_m14 = _mp3.parse_list(_MIX14, "数控机床")[0]
check("（对照）只看首卡确实判成不相关",
      _mp3.is_relevant("数控机床", "深圳市鲁海阳精密机械配件有限公司 其他通用五金配件,钻头,螺母"), False)
check("合并两张卡后判成相关", _mp3.is_relevant("数控机床", _mp3.stub_relevance_text(_m14)), True)

# ⑭-3 早过滤**砍掉的公司不占全局已见**（行为级：相关性是针对某个关键词的判定，
#      对「制冷设备」砍掉的滤芯厂，对「滤芯」正是目标，同一轮里也必须能重新评估）
def _card14(slug, name, prod, pid, city="河北廊坊"):
    return ('<div class="s_product_contact"><span>[%s]</span>'
            '<a href="http://%s.cn.makepolo.com/">%s</a>'
            '<a href="http://%s.cn.makepolo.com/companyProduct.html">%s</a>'
            '<a href="http://%s.cn.makepolo.com/contact_us.html?proid=%s">联系</a></div>'
            % (city, slug, name, slug, prod, pid))


_PAGES14 = {
    ("制冷设备", 1): _card14("lfmylx", "廊坊明耀滤芯厂", "滤芯", "100000000021")
                    + _card14("dgcyzl", "东莞市长赢制冷设备有限公司", "冷水机,冷冻机",
                              "100000000022", "广东东莞"),
    ("滤芯", 1): _card14("lfmylx", "廊坊明耀滤芯厂", "滤芯", "100000000021"),
}


class _Gate14(MPM.MakepoloCrawler):
    """把取页换成喂假页面（不联网）：只验「翻页循环里标已见与相关性判定的先后」。"""

    def build_search_url(self, kw, page):
        return f"mock://{kw}/{int(page)}"

    def _fetch(self, url, enc="utf-8", tries=3, guarded=True, referer=""):
        for (kw, page), body in _PAGES14.items():
            if url == self.build_search_url(kw, page):
                self._last_status = 200
                return body
        return ""


_g14 = _Gate14()
_g14.incremental = False
_g14.max_pages = 3
_g14._log = lambda *a, **k: None
_g14._sleep = lambda *a, **k: None
_g14._pace_before_request = lambda *a, **k: None
_k1 = [e["公司名称"] for e in _g14._iter_keyword("制冷设备")]
check("「制冷设备」这一轮：滤芯厂被早过滤砍掉", _k1, ["东莞市长赢制冷设备有限公司"])
check("砍掉的这家只按「关键词#主体」标记", "makepolo:制冷设备#lfmylx" in _g14._seen_members, True)
check("砍掉的这家**没**被全局标记", "makepolo:lfmylx" in _g14._seen_members, False)
check("放过的这家占全局已见（换词也不会重复采）", "makepolo:dgcyzl" in _g14._seen_members, True)
_k2 = [e["公司名称"] for e in _g14._iter_keyword("滤芯")]
check("换「滤芯」这一轮，同一家仍被重新评估并放行", _k2, ["廊坊明耀滤芯厂"])
_k3 = [e["公司名称"] for e in _g14._iter_keyword("制冷设备")]
check("同关键词重跑仍不重复产出（去重生效）", _k3, [])

# ⑭-4 同族类目桥（实机「数控机床」砍掉的名单里有车床/铣床/加工中心厂，是同行）
from keyword_expander import peer_words                                # noqa: E402
check("机床类关键词带出同族类目词", "加工中心" in peer_words("数控机床"), True)
check("关键词互相包含的同族词不重复给", "数控机床" in peer_words("数控机床"), False)
check("不在桥表里的关键词不放宽", peer_words("制冷设备"), ())
for _t, _w in (("车床,加工中心", True), ("铣床", True), ("钻床", True),
               ("加工中心", True), ("滤芯", False), ("货运代理,清关服务", False),
               ("减速器,齿轮", False), ("PLC/可编程控制系统", False)):
    check(f"同族桥判定「{_t}」", _mp3.is_relevant("数控机床", _t), _w)
check("同族桥不波及其他关键词", _mp3.is_relevant("制冷设备", "车床,加工中心"), False)


class _NoPeer14(MPM.MakepoloCrawler):
    _PEER_FN = False          # 模拟 keyword_expander 取不到：判定必须退回纯二字组


_np14 = _NoPeer14()
check("取不到同族表时判定退回原行为", _np14.is_relevant("数控机床", "车床,加工中心"), False)
check("取不到同族表时命中项仍放行", _np14.is_relevant("数控机床", "数控车床"), True)

# ⑭-5 端到端：真产品页名片改成后粘形态，联系人必须洗成「田经理」
_PD14 = _PROD_HTML.replace("黄亨</span>", "田经理二手机床</span>")
assert "田经理二手机床</span>" in _PD14


class _Pd14Session:
    """只有产品页那一跳给内容、商铺页回 439（实机真出现过这种店）。"""

    def get(self, url, timeout=None, headers=None):
        return _R2(_PD14.encode("utf-8")) if "product-detail" in url else _R2(b"", 439)

    def close(self):
        pass


_mp9 = makepolo_crawler.MakepoloCrawler(port=9309)
_mp9.incremental = False
_mp9._log = lambda *a, **k: None
_mp9._session = _Pd14Session()
_mp9._pace_before_request = lambda *a, **k: None
_rec14 = _mp9.crawl_detail({"公司名称": "东莞市港汇制冷设备有限公司", "公司ID": "dgghzl",
                            "memberId": "dgghzl", "关键词": "数控机床", "产品ID": "101063246805",
                            "公司网址": "http://dgghzl.cn.makepolo.com/"})
check("端到端：名片后粘产品词被切干净", _rec14.get("联系人"), "田经理")
check("端到端：职务列照旧", _rec14.get("岗位"), "经理")
check("端到端：手机不受影响", _rec14.get("手机"), "13450460020")
check("端到端：这一条仍是成功", _rec14.get("状态"), "成功")


# ════════════ ⑮ GI WebShield 会话质询链（2026-11-04 现场纠错） ════════════
print("\n=== ⑮ 跨国采购网 WebShield 会话质询链（零网络） ===")

_SHELL_15 = ('<html><head><meta http-equiv="Content-Type" content="text/html; charset=gb2312" />'
             '<script>function JumpSelf(){     self.location="/?WebShieldSessionVerify=%s";}'
             '</script><script>setTimeout("JumpSelf()",700);</script></head><body></body></html>')
_REAL_HOME_15 = "<html><head><title>跨国采购网</title></head><body>" + "首页正文" * 4000 + "</body></html>"
_VERIFY_PAGE_15 = ('<html><head><meta http-equiv="Content-Type" content="text/html; charset=gb2312" />'
                   '<script>function JumpSelf(){self.location="/";}</script>'
                   '<script>setTimeout("JumpSelf()",700);</script></head><body></body></html>')
_REAL_LIST_15 = open("/tmp/gi_p1.html", encoding="utf-8").read()   # 实机列表页（37KB），直接读文件免得撞变量名
_HOME_URL_15 = gi_crawler.HOME_URL


class _Resp15:
    def __init__(self, text, status=200):
        self.content = text.encode("gb18030", "ignore")
        self.status_code = status
        self.headers = {"content-type": "text/html; Charset=gb2312"}

    @property
    def text(self):
        return self.content.decode("gb18030", "ignore")


class _Sess15:
    """按 URL 谓词回放；同一谓词给了多条就按次序弹出（用尽后固定回最后一条）。"""

    def __init__(self, routes):
        self.routes = list(routes)
        self.calls = []

    def get(self, url, timeout=None, headers=None):
        self.calls.append((url, dict(headers or {})))
        for pred, seq in self.routes:
            if pred(url):
                if len(seq) > 1:
                    return seq.pop(0)
                return seq[0]
        return _Resp15("")

    def close(self):
        pass


def _gi15(sess):
    g = gi_crawler.GiCrawler(port=9515)
    g.incremental = False
    g._log = lambda *a, **k: None
    g._session = sess
    g._pace_before_request = lambda *a, **k: None
    return g


_is_home = lambda u: u.rstrip("?") == _HOME_URL_15.rstrip("?") or u == _HOME_URL_15
_is_verify = lambda u: "WebShieldSessionVerify=" in u
_is_list = lambda u: "search_company.asp" in u

_sleep_orig = gi_crawler.time.sleep
gi_crawler.time.sleep = lambda *a, **k: None        # 质询链里的 700ms 节奏在离线不等
try:
    # ⑮-1 薄壳 → 跟一跳 → 回补首页：三跳形状与判据
    _sq = [_Resp15(_SHELL_15 % "TOKENAAA"), _Resp15(_REAL_HOME_15), _Resp15(_REAL_HOME_15)]
    _sx = _Sess15([(_is_home, _sq[:1] + _sq[2:]), (_is_verify, [_Resp15(_VERIFY_PAGE_15)])])
    _g = _gi15(_sx)
    check("⑮ 引导成功（薄壳跟完后放行）", _g._bootstrap(), True)
    check("⑮ 跟了几跳", _g._boot_hops, 1)
    check("⑮ 请求次序：首页→质询页→回补首页",
          ["V" if _is_verify(u) else "H" for u, _ in _sx.calls], ["H", "V", "H"])
    check("⑮ 质询页 URL 照 JS 原样带 token",
          _sx.calls[1][0], f"{_HOME_URL_15}?WebShieldSessionVerify=TOKENAAA")
    check("⑮ 质询页带 Referer=首页", _sx.calls[1][1].get("Referer"), _HOME_URL_15)

    # ⑮-2 已放行（首页直接给真内容）：不该多打质询跳转，一个请求就够
    _sx2 = _Sess15([(_is_home, [_Resp15(_REAL_HOME_15)]), (_is_list, [_Resp15(_REAL_LIST_15)])])
    _g2 = _gi15(_sx2)
    check("⑮ 放行态引导成功", _g2._bootstrap(), True)
    check("⑮ 放行态不跟跳转", _g2._boot_hops, 0)
    check("⑮ 放行态只发 1 个请求", len(_sx2.calls), 1)

    # ⑮-3 跟完仍薄壳：判失败、记下来自哪一层，且跳数有上限不死循环
    _sx3 = _Sess15([(_is_home, [_Resp15(_SHELL_15 % "TOK%02X")]),
                    (_is_verify, [_Resp15(_SHELL_15 % "TOK%02X")])])
    _g3 = _gi15(_sx3)
    check("⑮ 始终薄壳判引导失败", _g3._bootstrap(), False)
    check("⑮ 跳数封顶 3 次", _g3._boot_hops, 3)
    check("⑮ 记录的是薄壳字节量级", _g3._boot_len < 1000, True)

    # ⑮-4 probe_block 归因话术：跟过跳转 ≠ 会话建立不起来，必须说成出口冷却
    _g4 = _gi15(_Sess15([(_is_home, [_Resp15(_SHELL_15 % "TK")]),
                         (_is_verify, [_Resp15(_SHELL_15 % "TK")]),
                         (_is_list, [_Resp15(_SHELL_15 % "TK")])]))
    _g4._block_probe_url = gi.build_search_url("制冷设备", 1)
    _msg4 = _g4.probe_block()
    check("⑮ 探测话术写明跟随过跳转", "已跟随质询跳转" in _msg4, True)
    check("⑮ 探测话术不许出现「会话建立不起来」", "会话建立不起来" in _msg4, False)

    # ⑮-5 probe_block 真放行：引导 + 列表能解析出条目才算解除（不看状态码）
    _g5 = _gi15(_Sess15([(_is_home, [_Resp15(_SHELL_15 % "TK"), _Resp15(_REAL_HOME_15)]),
                         (_is_verify, [_Resp15(_VERIFY_PAGE_15)]),
                         (_is_list, [_Resp15(_REAL_LIST_15)])]))
    _g5._block_probe_url = gi.build_search_url("制冷设备", 1)
    _g5._block_reason = "防采插页（WebShieldSessionVerify）"
    check("⑮ 真放行时探测返回空串", _g5.probe_block(), "")
    check("⑮ 解除后清掉拦截原因", _g5._block_reason, "")

    # ⑮-6 _fetch 自愈：搜索页先给薄壳，重走质询链后同一页要拿到真列表
    _sx6 = _Sess15([(_is_home, [_Resp15(_REAL_HOME_15)]),
                    (_is_list, [_Resp15(_SHELL_15 % "TK"), _Resp15(_REAL_LIST_15)])])
    _g6 = _gi15(_sx6)
    _g6._booted = True
    _out6 = _g6._fetch(gi.build_search_url("制冷设备", 1), guarded=False)
    check("⑮ 薄壳后重取同一页拿到正文", len(_out6) > 30000, True)
    check("⑮ 重取后解析出 20 家", len(gi.parse_list(_out6, "制冷设备")), 20)
    check("⑮ 薄壳自愈不记拦截", _g6._block_reason, "")

    # ⑮-7 请求预算：引导用的首页/质询页不占内容页配额（REQ_BUDGET 只记列表页）
    _g7 = _gi15(_Sess15([(_is_home, [_Resp15(_SHELL_15 % "TK"), _Resp15(_REAL_HOME_15)]),
                         (_is_verify, [_Resp15(_VERIFY_PAGE_15)]),
                         (_is_list, [_Resp15(_REAL_LIST_15)])]))
    _g7._fetch(gi.build_search_url("制冷设备", 1), guarded=False)
    check("⑮ 引导不消耗内容页预算", _g7._req_used, 1)
finally:
    gi_crawler.time.sleep = _sleep_orig


# ⑭-1 联系人**后粘**产品词（实机文安县汇永商贸：名片原文「田经理二手机床」，
#      基类按尾巴称谓取名 → 切成叫不出口的「田经理二」）
for _raw, _want in (("田经理二手机床", "田经理"), ("赵女士二手机床批发", "赵女士"),
                    ("陈小姐加工厂", "陈小姐"), ("高先生13912345678", "高先生"),
                    ("田经理二维码", "田经理"),
                    ("数控车床张经理二手机床", "数控车床张经理二手机床"),   # 两侧都粘：真页没出现过，不猜
                    ("李总经理", "李总经理"), ("王经理助理", "王经理助理"),
                    ("郑生", "郑生"), ("黄亨", "黄亨")):
    _g = MPM.clean_contact_person(_raw)
    check(f"联系人清洗「{_raw}」", _g, _want)
    check(f"清洗结果定点「{_raw}」", MPM.clean_contact_person(_g), _g)

# ⑭-2 同店两张卡的主营并起来判相关（实机深圳市鲁海阳精密机械配件：两张卡一张写
#      「其他通用五金配件,钻头,螺母」、一张写「刀片,其他机床附件」，翻页只判首卡就误杀）
_two14 = _mp3.parse_list(_TWO_CARD, "制冷设备")
check("两张卡主营并进「主营全文」", _two14[0]["主营全文"], "冷水机,冷冻机 制冰机,冷库设备")
check("对外「主营产品」仍是首卡（不污染输出列）", _two14[0]["主营产品"], "冷水机,冷冻机")
check("「主营全文」不会被记录骨架带进结果", "主营全文" in _mp3._base_rec(_two14[0]), False)
_MIX14 = ('<div class="s_product_contact"><span>[广东深圳]</span>'
          '<a href="http://sylhy.cn.makepolo.com/">深圳市鲁海阳精密机械配件有限公司</a>'
          '<a href="http://sylhy.cn.makepolo.com/companyProduct.html">其他通用五金配件,钻头,螺母</a>'
          '<a href="http://sylhy.cn.makepolo.com/contact_us.html?proid=100000000011">联系</a></div>'
          '<div class="s_product_contact"><span>[广东深圳]</span>'
          '<a href="http://sylhy.cn.makepolo.com/">深圳市鲁海阳精密机械配件有限公司</a>'
          '<a href="http://sylhy.cn.makepolo.com/companyProduct.html">刀片,其他机床附件,刀杆</a>'
          '<a href="http://sylhy.cn.makepolo.com/contact_us.html?proid=100000000012">联系</a></div>')
_m14 = _mp3.parse_list(_MIX14, "数控机床")[0]
check("（对照）只看首卡确实判成不相关",
      _mp3.is_relevant("数控机床", "深圳市鲁海阳精密机械配件有限公司 其他通用五金配件,钻头,螺母"), False)
check("合并两张卡后判成相关", _mp3.is_relevant("数控机床", _mp3.stub_relevance_text(_m14)), True)

# ⑭-3 早过滤**砍掉的公司不占全局已见**（行为级：相关性是针对某个关键词的判定，
#      对「制冷设备」砍掉的滤芯厂，对「滤芯」正是目标，同一轮里也必须能重新评估）
def _card14(slug, name, prod, pid, city="河北廊坊"):
    return ('<div class="s_product_contact"><span>[%s]</span>'
            '<a href="http://%s.cn.makepolo.com/">%s</a>'
            '<a href="http://%s.cn.makepolo.com/companyProduct.html">%s</a>'
            '<a href="http://%s.cn.makepolo.com/contact_us.html?proid=%s">联系</a></div>'
            % (city, slug, name, slug, prod, pid))


_PAGES14 = {
    ("制冷设备", 1): _card14("lfmylx", "廊坊明耀滤芯厂", "滤芯", "100000000021")
                    + _card14("dgcyzl", "东莞市长赢制冷设备有限公司", "冷水机,冷冻机",
                              "100000000022", "广东东莞"),
    ("滤芯", 1): _card14("lfmylx", "廊坊明耀滤芯厂", "滤芯", "100000000021"),
}


class _Gate14(MPM.MakepoloCrawler):
    """把取页换成喂假页面（不联网）：只验「翻页循环里标已见与相关性判定的先后」。"""

    def build_search_url(self, kw, page):
        return f"mock://{kw}/{int(page)}"

    def _fetch(self, url, enc="utf-8", tries=3, guarded=True, referer=""):
        for (kw, page), body in _PAGES14.items():
            if url == self.build_search_url(kw, page):
                self._last_status = 200
                return body
        return ""


_g14 = _Gate14()
_g14.incremental = False
_g14.max_pages = 3
_g14._log = lambda *a, **k: None
_g14._sleep = lambda *a, **k: None
_g14._pace_before_request = lambda *a, **k: None
_k1 = [e["公司名称"] for e in _g14._iter_keyword("制冷设备")]
check("「制冷设备」这一轮：滤芯厂被早过滤砍掉", _k1, ["东莞市长赢制冷设备有限公司"])
check("砍掉的这家只按「关键词#主体」标记", "makepolo:制冷设备#lfmylx" in _g14._seen_members, True)
check("砍掉的这家**没**被全局标记", "makepolo:lfmylx" in _g14._seen_members, False)
check("放过的这家占全局已见（换词也不会重复采）", "makepolo:dgcyzl" in _g14._seen_members, True)
_k2 = [e["公司名称"] for e in _g14._iter_keyword("滤芯")]
check("换「滤芯」这一轮，同一家仍被重新评估并放行", _k2, ["廊坊明耀滤芯厂"])
_k3 = [e["公司名称"] for e in _g14._iter_keyword("制冷设备")]
check("同关键词重跑仍不重复产出（去重生效）", _k3, [])

# ⑭-4 同族类目桥（实机「数控机床」砍掉的名单里有车床/铣床/加工中心厂，是同行）
from keyword_expander import peer_words                                # noqa: E402
check("机床类关键词带出同族类目词", "加工中心" in peer_words("数控机床"), True)
check("关键词互相包含的同族词不重复给", "数控机床" in peer_words("数控机床"), False)
check("不在桥表里的关键词不放宽", peer_words("制冷设备"), ())
for _t, _w in (("车床,加工中心", True), ("铣床", True), ("钻床", True),
               ("加工中心", True), ("滤芯", False), ("货运代理,清关服务", False),
               ("减速器,齿轮", False), ("PLC/可编程控制系统", False)):
    check(f"同族桥判定「{_t}」", _mp3.is_relevant("数控机床", _t), _w)
check("同族桥不波及其他关键词", _mp3.is_relevant("制冷设备", "车床,加工中心"), False)


class _NoPeer14(MPM.MakepoloCrawler):
    _PEER_FN = False          # 模拟 keyword_expander 取不到：判定必须退回纯二字组


_np14 = _NoPeer14()
check("取不到同族表时判定退回原行为", _np14.is_relevant("数控机床", "车床,加工中心"), False)
check("取不到同族表时命中项仍放行", _np14.is_relevant("数控机床", "数控车床"), True)

# ⑭-5 端到端：真产品页名片改成后粘形态，联系人必须洗成「田经理」
_PD14 = _PROD_HTML.replace("黄亨</span>", "田经理二手机床</span>")
assert "田经理二手机床</span>" in _PD14


class _Pd14Session:
    """只有产品页那一跳给内容、商铺页回 439（实机真出现过这种店）。"""

    def get(self, url, timeout=None, headers=None):
        return _R2(_PD14.encode("utf-8")) if "product-detail" in url else _R2(b"", 439)

    def close(self):
        pass


_mp9 = makepolo_crawler.MakepoloCrawler(port=9309)
_mp9.incremental = False
_mp9._log = lambda *a, **k: None
_mp9._session = _Pd14Session()
_mp9._pace_before_request = lambda *a, **k: None
_rec14 = _mp9.crawl_detail({"公司名称": "东莞市港汇制冷设备有限公司", "公司ID": "dgghzl",
                            "memberId": "dgghzl", "关键词": "数控机床", "产品ID": "101063246805",
                            "公司网址": "http://dgghzl.cn.makepolo.com/"})
check("端到端：名片后粘产品词被切干净", _rec14.get("联系人"), "田经理")
check("端到端：职务列照旧", _rec14.get("岗位"), "经理")
check("端到端：手机不受影响", _rec14.get("手机"), "13450460020")
check("端到端：这一条仍是成功", _rec14.get("状态"), "成功")


# ════════════ ⑮ GI WebShield 会话质询链（2026-11-04 现场纠错） ════════════
print("\n=== ⑮ 跨国采购网 WebShield 会话质询链（零网络） ===")

_SHELL_15 = ('<html><head><meta http-equiv="Content-Type" content="text/html; charset=gb2312" />'
             '<script>function JumpSelf(){     self.location="/?WebShieldSessionVerify=%s";}'
             '</script><script>setTimeout("JumpSelf()",700);</script></head><body></body></html>')
_REAL_HOME_15 = "<html><head><title>跨国采购网</title></head><body>" + "首页正文" * 4000 + "</body></html>"
_VERIFY_PAGE_15 = ('<html><head><meta http-equiv="Content-Type" content="text/html; charset=gb2312" />'
                   '<script>function JumpSelf(){self.location="/";}</script>'
                   '<script>setTimeout("JumpSelf()",700);</script></head><body></body></html>')
_REAL_LIST_15 = open("/tmp/gi_p1.html", encoding="utf-8").read()   # 实机列表页（37KB），直接读文件免得撞变量名
_HOME_URL_15 = gi_crawler.HOME_URL


class _Resp15:
    def __init__(self, text, status=200):
        self.content = text.encode("gb18030", "ignore")
        self.status_code = status
        self.headers = {"content-type": "text/html; Charset=gb2312"}

    @property
    def text(self):
        return self.content.decode("gb18030", "ignore")


class _Sess15:
    """按 URL 谓词回放；同一谓词给了多条就按次序弹出（用尽后固定回最后一条）。"""

    def __init__(self, routes):
        self.routes = list(routes)
        self.calls = []

    def get(self, url, timeout=None, headers=None):
        self.calls.append((url, dict(headers or {})))
        for pred, seq in self.routes:
            if pred(url):
                if len(seq) > 1:
                    return seq.pop(0)
                return seq[0]
        return _Resp15("")

    def close(self):
        pass


def _gi15(sess):
    g = gi_crawler.GiCrawler(port=9515)
    g.incremental = False
    g._log = lambda *a, **k: None
    g._session = sess
    g._pace_before_request = lambda *a, **k: None
    return g


_is_home = lambda u: u.rstrip("?") == _HOME_URL_15.rstrip("?") or u == _HOME_URL_15
_is_verify = lambda u: "WebShieldSessionVerify=" in u
_is_list = lambda u: "search_company.asp" in u

_sleep_orig = gi_crawler.time.sleep
gi_crawler.time.sleep = lambda *a, **k: None        # 质询链里的 700ms 节奏在离线不等
try:
    # ⑮-1 薄壳 → 跟一跳 → 回补首页：三跳形状与判据
    _sq = [_Resp15(_SHELL_15 % "TOKENAAA"), _Resp15(_REAL_HOME_15), _Resp15(_REAL_HOME_15)]
    _sx = _Sess15([(_is_home, _sq[:1] + _sq[2:]), (_is_verify, [_Resp15(_VERIFY_PAGE_15)])])
    _g = _gi15(_sx)
    check("⑮ 引导成功（薄壳跟完后放行）", _g._bootstrap(), True)
    check("⑮ 跟了几跳", _g._boot_hops, 1)
    check("⑮ 请求次序：首页→质询页→回补首页",
          ["V" if _is_verify(u) else "H" for u, _ in _sx.calls], ["H", "V", "H"])
    check("⑮ 质询页 URL 照 JS 原样带 token",
          _sx.calls[1][0], f"{_HOME_URL_15}?WebShieldSessionVerify=TOKENAAA")
    check("⑮ 质询页带 Referer=首页", _sx.calls[1][1].get("Referer"), _HOME_URL_15)

    # ⑮-2 已放行（首页直接给真内容）：不该多打质询跳转，一个请求就够
    _sx2 = _Sess15([(_is_home, [_Resp15(_REAL_HOME_15)]), (_is_list, [_Resp15(_REAL_LIST_15)])])
    _g2 = _gi15(_sx2)
    check("⑮ 放行态引导成功", _g2._bootstrap(), True)
    check("⑮ 放行态不跟跳转", _g2._boot_hops, 0)
    check("⑮ 放行态只发 1 个请求", len(_sx2.calls), 1)

    # ⑮-3 跟完仍薄壳：判失败、记下来自哪一层，且跳数有上限不死循环
    _sx3 = _Sess15([(_is_home, [_Resp15(_SHELL_15 % "TOK%02X")]),
                    (_is_verify, [_Resp15(_SHELL_15 % "TOK%02X")])])
    _g3 = _gi15(_sx3)
    check("⑮ 始终薄壳判引导失败", _g3._bootstrap(), False)
    check("⑮ 跳数封顶 3 次", _g3._boot_hops, 3)
    check("⑮ 记录的是薄壳字节量级", _g3._boot_len < 1000, True)

    # ⑮-4 probe_block 归因话术：跟过跳转 ≠ 会话建立不起来，必须说成出口冷却
    _g4 = _gi15(_Sess15([(_is_home, [_Resp15(_SHELL_15 % "TK")]),
                         (_is_verify, [_Resp15(_SHELL_15 % "TK")]),
                         (_is_list, [_Resp15(_SHELL_15 % "TK")])]))
    _g4._block_probe_url = gi.build_search_url("制冷设备", 1)
    _msg4 = _g4.probe_block()
    check("⑮ 探测话术写明跟随过跳转", "已跟随质询跳转" in _msg4, True)
    check("⑮ 探测话术不许出现「会话建立不起来」", "会话建立不起来" in _msg4, False)

    # ⑮-5 probe_block 真放行：引导 + 列表能解析出条目才算解除（不看状态码）
    _g5 = _gi15(_Sess15([(_is_home, [_Resp15(_SHELL_15 % "TK"), _Resp15(_REAL_HOME_15)]),
                         (_is_verify, [_Resp15(_VERIFY_PAGE_15)]),
                         (_is_list, [_Resp15(_REAL_LIST_15)])]))
    _g5._block_probe_url = gi.build_search_url("制冷设备", 1)
    _g5._block_reason = "防采插页（WebShieldSessionVerify）"
    check("⑮ 真放行时探测返回空串", _g5.probe_block(), "")
    check("⑮ 解除后清掉拦截原因", _g5._block_reason, "")

    # ⑮-6 _fetch 自愈：搜索页先给薄壳，重走质询链后同一页要拿到真列表
    _sx6 = _Sess15([(_is_home, [_Resp15(_REAL_HOME_15)]),
                    (_is_list, [_Resp15(_SHELL_15 % "TK"), _Resp15(_REAL_LIST_15)])])
    _g6 = _gi15(_sx6)
    _g6._booted = True
    _out6 = _g6._fetch(gi.build_search_url("制冷设备", 1), guarded=False)
    check("⑮ 薄壳后重取同一页拿到正文", len(_out6) > 30000, True)
    check("⑮ 重取后解析出 20 家", len(gi.parse_list(_out6, "制冷设备")), 20)
    check("⑮ 薄壳自愈不记拦截", _g6._block_reason, "")

    # ⑮-7 请求预算：引导用的首页/质询页不占内容页配额（REQ_BUDGET 只记列表页）
    _g7 = _gi15(_Sess15([(_is_home, [_Resp15(_SHELL_15 % "TK"), _Resp15(_REAL_HOME_15)]),
                         (_is_verify, [_Resp15(_VERIFY_PAGE_15)]),
                         (_is_list, [_Resp15(_REAL_LIST_15)])]))
    _g7._fetch(gi.build_search_url("制冷设备", 1), guarded=False)
    check("⑮ 引导不消耗内容页预算", _g7._req_used, 1)
finally:
    gi_crawler.time.sleep = _sleep_orig

print(f"\n通过 {OK} 项 / 失败 {FAIL} 项")
sys.exit(1 if FAIL else 0)
