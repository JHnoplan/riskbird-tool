# -*- coding: utf-8 -*-
"""test_ingest_store_offline.py —— Silver 清洗派生 + Gold 物化视图 离线回归。

覆盖三层数据湖在本机落地时**最容易静默失效**的地方（每条都对应一个真实缺陷
或一次真实修复，不是为覆盖率编的断言）：

  1. 分区键派生（ingest_cleaning.enrich_partition_fields）
     存量记录补齐 industry/province/region/quality，只填空缺、绝不覆盖已有值。
  2. 判据口径唯一（pool_matcher ↔ pool_store）
     SQL 下压的每一位（联系方式/规模/新鲜度/属地）必须与 Python apply_filters
     在同一时刻给出**完全相同的记录集合**——两侧规则一旦漂移，界面会出现
     「勾了筛选却少一批、不勾又多一些」这种永远说不清的问题。
  3. FTS5 真实表名（MATCH / bm25 都不接受表别名）
     用别名时 SQLite 报 no such column，被 except 吞掉后表现为「搜索全零命中、
     但统计有几万条」，是本项目最危险的一次静默失败，必须有回归断言。
  4. 视图可用性三态（不可用 / 未对齐 / 可用）
     空库、半成品库、无 FTS5、目录只读都必须走「回退老链路」，绝不能被上层
     读成「库里没有数据」。
  5. 写侧前移与增量对齐（DataPoolManager + 假 OSS）
     全量重建 → 视图对齐；OSS 增量 → upsert 跟上；总表变化 → 视图作废。
  6. 检索字段口径（search_fields）
     倒排索引、FTS 列、文本相关性判据必须来自同一份字段清单。
  7. 派生列在合并之后重算（属地 ⇄ 地区必须原子一致）
     合并是同一实体多来源取并集，证据变了判据输出必须跟着重算，否则总表里
     会留下「勾国外筛不到、勾广东也筛不到」这种永远查不出来的矛盾记录。
  8. 重放确定性（Bronze 装配序 + 分区键挑法与记录顺序无关）
     同一批 Bronze 数据两次全量重建、以及「增量合并 vs 全量重建」必须给出逐位
     相同的总表；否则用户每次同步都会看到同一公司的行业/地区悄悄换值。
  9. 名称门禁的读侧重放（判据升级要能回刷存量）
     Bronze 是 append-only 原始层，历史对象不会被回头改写：只在写侧加判据会让
     修复对存量永远不可见。读取端重放同一份判据 + 判据版本号落盘作废旧缓存，
     同时保证断网时仍用旧缓存兜底（可用性优先）。
全程临时目录，不联网、不写 ~/Desktop/数据池缓存、不动真实审计日志。
"""
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

_passed = 0
_failed = 0
_tmp_roots = []


def _assert(condition, label):
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  ok   {label}")
    else:
        _failed += 1
        print(f"  FAIL {label}")


def _assert_eq(actual, expected, label):
    _assert(actual == expected,
            f"{label}  (期望={expected!r}, 实际={actual!r})")


def _new_tmp_dir():
    d = tempfile.mkdtemp(prefix="ingest_store_test_")
    _tmp_roots.append(d)
    return d


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_pool import DataPoolManager, record_region
from ingest_cleaning import (classify_industry, clean_records, clean_one,
                             enrich_partition_fields, export_dropped_rows,
                             format_report, merge_reports)
import ingest_cleaning as ic
from pool_matcher import (CompanyMatcher, contact_flags, parse_source_time,
                          search_fields, source_ts, tokenize)
from pool_store import PoolStore, fingerprint_records, match_expr

apply_filters = CompanyMatcher.apply_filters


# ── 测试数据（覆盖国内/国外、有/无联系方式、脏/净字段）────────────────────────

def _rec(company, **kw):
    rec = {
        "company": company,
        "company_norm": company.strip().lower(),
        "contacts": [], "mobiles": [], "landlines": [], "phones": [],
        "emails": [], "wechats": [], "address": "", "products": "",
        "website": "", "source": "测试来源.xlsx",
        "source_time": "2026-11-01T10:00:00", "source_user": "tester",
    }
    rec.update(kw)
    return rec


CORPUS = [
    _rec("宁波锦浪新能源技术有限公司",
         products="光伏逆变器 储能电池", address="浙江省宁波市",
         mobiles=["13800000001"], emails=["sales@ginlong.com"],
         source_time="2026-11-05T09:00:00"),
    _rec("中山市小榄镇照明电器厂",
         products="LED 灯具", address="广东省中山市小榄镇",
         phones=["0760-88886666"], source_time="2026-06-01T08:00:00"),
    _rec("SolarEdge Technologies Ltd",
         products="solar inverter", country="Israel", address="Haifa",
         emails=["info@solaredge.com"], source_time="2026-10-20T08:00:00"),
    _rec("上海大周信息科技有限公司",
         products="MES 系统", address="上海市浦东新区",
         mobiles=["13900002222"], wechats=["dazhou2026"],
         source_time="2020-01-01 00:00:00"),
    _rec("某某国际贸易集团", products="进出口贸易",
         address="江苏省苏州市", phones=["0512-12345678"],
         emails=["a@b.com"], source_time="2026-11-09T18:30:00"),
    _rec("无联系方式的公司", products="阀门"),          # 门禁应剔除
    _rec("1. 带序号前缀的有限公司", products="水泵",
         address="浙江省台州市", mobiles=["13700003333"]),
]


# ════════════════════════════════════════════════════════════════════════════
print("\n═══ 1. Silver：入库前清洗与分区派生 ═══")

kept, report = clean_records([dict(r) for r in CORPUS])
_assert_eq(report["in"], len(CORPUS), "清洗输入条数")
_assert_eq(report["kept"], len(kept), "保留条数与 kept 一致")
_assert_eq(len(kept), 6, "无联系方式的那条被门禁剔除")
_assert("无联系方式" in report["drop_reasons"], "剔除原因进报告（不静默丢数据）")
_assert(report["region"]["dom"] == 5 and report["region"]["intl"] == 1,
         "属地分区键在入库时就派生（5 国内 + 1 国外）")
_assert(kept[0].get("industry") == "光伏太阳能",
       f"行业由主营派生并落进记录本身  (实际={kept[0].get('industry')})")
_assert(kept[0].get("province") == "浙江", "省份分区键落进记录本身")
_assert(kept[0].get("quality", 0) > 0, "质量分入库时算好（不在查询里现算）")
_assert("带序号前缀的有限公司" in [r["company"] for r in kept],
         "公司名的序号前缀被修复（'1. ' 去掉）")
_samples = report["dropped_samples"]
_assert(isinstance(_samples, list) and len(_samples) == 1
         and _samples[0][0] == "无联系方式的公司" and _samples[0][1],
         f"被剔记录带样本（公司名 + 原因）可回溯  (实际={_samples})")

# ── 未入库清单（reject-file）：不入库 ≠ 消失 ────────────────────────────────
# 门禁拦下来的行必须整份有去处。只给 20 条样例的报告能帮人判断口径，但补数据
# 的人要的是每一行——所以 dropped_rows 与 dropped 计数必须始终对得上。
_rows = report["dropped_rows"]
_assert_eq(len(_rows), report["dropped"],
           "被剔记录整份进报告（条数与剔除计数一致）")
_assert(_rows[0]["company"] == "无联系方式的公司"
        and _rows[0]["reason"] == "无联系方式" and _rows[0]["products"] == "阀门",
        f"清单行带原始字段：一眼分清「列映射选错」还是「文件本来没号」  (实际={_rows[0]})")
_drop_p = os.path.join(_new_tmp_dir(), "未入库清单_测试.xlsx")
_n, _saved = export_dropped_rows(report, _drop_p)
_assert_eq(_n, report["dropped"], "导出的清单条数 == 报告剔除条数")
_assert(os.path.exists(_saved), "清单真的落了盘（不是只返回一个路径）")
from openpyxl import load_workbook as _load_wb
_wb = _load_wb(_saved)
_assert_eq(_wb.sheetnames, ["未入库清单", "汇总"],
           "两张表：清单给人动手补，汇总让「为什么少了这些」能自证")
_ws = _wb["未入库清单"]
_hdr = [c.value for c in next(_ws.iter_rows(min_row=1, max_row=1))]
_rows2 = [dict(zip(_hdr, [c.value for c in r])) for r in _ws.iter_rows(min_row=2)]
_assert_eq(len(_rows2), 1, "清单里逐行落盘（回读条数一致）")
_assert(_rows2[0]["公司名"] == "无联系方式的公司"
        and _rows2[0]["剔除原因"] == "无联系方式"
        and _rows2[0]["主营产品"] == "阀门",
        f"清单表头与内容对齐  (实际={_rows2[0]})")
_sum = {r[0].value: r[1].value for r in _wb["汇总"].iter_rows(min_row=2)}
_assert_eq(int(_sum.get("被剔除行数", -1)), 1, "汇总表如实写剔除数")
_assert_eq(int(_sum.get("清单已列出行数", -1)), 1, "汇总表写清列出了多少行")

# 超上限：计数如实、清单截断、截断量单独记账（绝不假装全给了）
_old_cap = ic.DROP_ROW_CAP
ic.DROP_ROW_CAP = 5
try:
    _cap_rep = clean_records([_rec(f"无联系方式公司{i}", products="阀门")
                              for i in range(8)])[1]
finally:
    ic.DROP_ROW_CAP = _old_cap
_assert_eq(_cap_rep["dropped"], 8, "超上限时剔除计数不受影响")
_assert_eq(len(_cap_rep["dropped_rows"]), 5, "清单最多留 DROP_ROW_CAP 条（内存上限）")
_assert_eq(_cap_rep["dropped_overflow"], 3, "超出部分单独记账，不静默吞掉")
_assert("共 8 条" in format_report(_cap_rep), "报告文案按总数说，不按清单条数说")
_agg = {}
merge_reports(_agg, {"dropped": 2, "kept": 3, "in": 5,
                    "dropped_rows": _cap_rep["dropped_rows"][:2],
                    "dropped_overflow": 0, "drop_reasons": {"无联系方式": 2}})
merge_reports(_agg, {"dropped": 6, "kept": 4, "in": 10,
                    "dropped_rows": _cap_rep["dropped_rows"][2:],
                    "dropped_overflow": 3, "drop_reasons": {"无联系方式": 6}})
_assert_eq(_agg["dropped"], 8, "多文件汇总：剔除数求和")
_assert_eq(len(_agg["dropped_rows"]), 5, "多文件汇总：清单行数拼接（未超上限）")
_assert_eq(_agg["dropped_overflow"], 3, "多文件汇总：溢出计数求和")

# 只填空缺，不覆盖已有值
legacy = _rec("既已有行业标签有限公司", industry="既有标签",
              address="四川省成都市")
counts = enrich_partition_fields([legacy])
_assert_eq(legacy["industry"], "既有标签", "已有行业值不被派生结果覆盖")
_assert_eq(legacy["province"], "四川", "缺失省份补齐")
_assert_eq(legacy["region"], record_region(legacy), "属地键与 record_region 同口径")
_assert_eq(legacy["is_domestic"], True, "属地显式字段落库")
_assert(counts.get("省份补派生") == 1, "补齐计数返回给调用方展示")
before = dict(legacy)
_assert_eq(enrich_partition_fields([legacy]), {},
           "第二次跑没有任何改动（幂等，可反复执行）")
_assert_eq({k: legacy[k] for k in before}, before, "幂等：记录内容一字不改")

# classify_industry 的大小写改写必须与「输入原样 + IGNORECASE」同结果
_ref_re = re.compile(
    "|".join(re.escape(k) for k in sorted(
        __import__("ingest_cleaning")._PATTERN_ALIASES, key=len, reverse=True)),
    re.IGNORECASE)


def _classify_reference(*texts):
    """改写前的实现：原样输入 + IGNORECASE（用于等价性对照）。"""
    blob = " ".join(str(t or "") for t in texts)[:600]
    if not blob.strip():
        return ""
    hits = {}
    for m in _ref_re.finditer(blob):
        ind = __import__("ingest_cleaning")._PATTERN_ALIASES.get(
            m.group(0).lower(), "")
        if ind:
            hits[ind] = hits.get(ind, 0) + len(m.group(0))
    return max(hits.items(), key=lambda kv: (kv[1], len(kv[0])))[0] if hits else ""


CASE_SAMPLES = [
    ("SOLAR PANEL 光伏组件", "", "", ""),
    ("Solar Inverter / Storage Battery", "", "", ""),
    ("LED 照明灯具", "", "", ""),
    ("BATTERY 锂电池 储能", "", "", "德国展会"),
    ("WIND POWER 风电设备", "", "", ""),
    ("小写混排 solar panel 与 SolarEdge", "", "", ""),
    ("PV MODULE 组件 制造商", "", "", ""),
    ("EV CHARGING PILE 充电桩", "", "", ""),
    ("", "", "", ""),
    ("没有行业证据的一家公司", "", "", ""),
]
_mismatch = [(s, classify_industry(*s), _classify_reference(*s))
             for s in CASE_SAMPLES
             if classify_industry(*s) != _classify_reference(*s)]
_assert_eq(_mismatch, [], "行业分类改为「输入转小写 + 敏感匹配」后结果完全一致")

# 时间解析：缓存前后语义一致
_assert_eq(parse_source_time("2026-11-05T09:00:00"),
           datetime(2026, 11, 5, 9, 0, 0), "ISO 格式解析")
_assert_eq(parse_source_time("2026-11-05 09:00:00"),
           datetime(2026, 11, 5, 9, 0, 0), "空格分隔格式解析")
_assert_eq(parse_source_time("2026-11-05"),
           datetime(2026, 11, 5, 0, 0, 0), "纯日期格式解析")
_assert(parse_source_time("不是时间") is None, "解析不出返回 None（不当 0 点用）")
_assert(parse_source_time("") is None, "空串返回 None")
_assert(source_ts("2026-11-05T09:00:00") > 0, "source_ts 正常给正数")
_assert_eq(source_ts("乱七八糟"), 0.0, "source_ts 解析失败给 0（新鲜度过滤必剔除）")
_assert_eq(source_ts("2026-11-05T09:00:00"),
           source_ts("2026-11-05T09:00:00"), "缓存命中后同值（可重复调用）")


# ════════════════════════════════════════════════════════════════════════════
print("\n═══ 2. Gold：物化视图构建 / 三态可用性 ═══")

VIEW_DIR = _new_tmp_dir()
store = PoolStore(VIEW_DIR)
_assert(store.fts_available(), "当前 SQLite 编译期带 FTS5")
_assert(store.usable(), "视图可用（FTS5 + 目录可写）")
_assert(not store.readable(), "还没构建时 readable() 为假（不是「零命中」）")
_assert_eq(store.search("光伏"), [], "未构建：检索空且不报错")
_assert_eq(store.count("光伏"), None, "未构建：计数 None（调用方须回退老链路）")
_assert_eq(store.stats(), {}, "未构建：统计空 dict")

fp = fingerprint_records(kept)
_assert_eq(fp[0], len(kept), "指纹含条数")
_assert(fp[1] != fingerprint_records(kept[:-1])[1], "内容变化即指纹变化")
_assert_eq(fingerprint_records(kept), fp, "指纹可重复计算（crc32 不受随机化影响）")

_assert(store.rebuild(kept, fp), "全量重建成功")
_assert(store.readable(), "构建后可读")
_assert(store.is_current(fp), "视图与总表同源（指纹对齐）")
_assert(not store.is_current(fingerprint_records(kept + kept)),
       "换了总表就不再是当前视图")
_assert_eq(store.count(""), len(kept), "无条件计数 = 全库条数")

# 空白库（文件存在但没有 meta）必须判为不可读，而不是「一条都没有」
EMPTY_DIR = _new_tmp_dir()
empty = PoolStore(EMPTY_DIR)
import sqlite3
sqlite3.connect(os.path.join(EMPTY_DIR, "pool_store.db")).execute(
    "CREATE TABLE IF NOT EXISTS other(x)").close()
_assert(os.path.exists(empty.path), "空白库文件确实存在")
_assert(not empty.readable(), "空白库判为不可读（避免被当成零命中）")
_assert_eq(empty.count(""), None, "空白库计数 None → 上层回退老链路")

# 只读目录：usable() 判假 → 整条 Gold 链路自动降级
RO_DIR = _new_tmp_dir()
ro_db = os.path.join(RO_DIR, "sub")
os.mkdir(ro_db)
os.chmod(ro_db, 0o500)
try:
    ro = PoolStore(ro_db)
    _assert(not ro.usable(), "缓存目录不可写 → usable() 为假")
    _assert(not ro.rebuild(kept, fp), "目录只读时重建返回 False（不抛异常）")
finally:
    os.chmod(ro_db, 0o700)


# ════════════════════════════════════════════════════════════════════════════
print("\n═══ 3. FTS5 真实表名回归（曾经的静默全零命中）═══")

store2 = PoolStore(_new_tmp_dir())
BIG = []
for i in range(400):
    if i % 4 == 0:
        BIG.append(_rec(f"光伏{i}号有限公司", products="光伏组件 逆变器",
                        address="浙江省杭州市", mobiles=[f"1380000{i:04d}"]))
    elif i % 4 == 1:
        BIG.append(_rec(f"储能电池{i}号有限公司", products="磷酸铁锂电池",
                        address="广东省深圳市", emails=[f"s{i}@bat.com"]))
    elif i % 4 == 2:
        BIG.append(_rec(f"SolarWorks {i} Ltd", products="solar panel",
                        country="Germany", address="Berlin",
                        phones=[f"+49 30 {i:06d}"]))
    else:
        BIG.append(_rec(f"机床装备{i}厂", products="数控机床",
                        address="江苏省南京市", landlines=["025-88888888"]))
_assert(store2.rebuild(BIG, fingerprint_records(BIG)), "400 条视图重建")
_last_err_before = store2.last_error
hits = store2.search("光伏", limit=500)
_assert_eq(store2.last_error, _last_err_before,
           f"检索没有把 SQL 错误吞成静默零命中  (last_error={store2.last_error})")
_assert(len(hits) > 0, "FTS 检索有命中（别名写法会让这里恒为 0）")
_assert_eq(len(hits), store2.count("光伏"),
           "search 与 count 用同一套 SQL，命中数必须一致")
# 语料里 100 条中文「光伏」+ 100 条 SolarWorks（写入端把派生行业「光伏太阳能」
# 回写进记录，search_fields 含 industry，所以原词层就能召回它们）；
# 储能/机床各 100 条不得混进来——同义词层只在原词层零命中时才用
_assert_eq(store2.count("光伏"), 200, "「光伏」召回字面 100 + 归类光伏太阳能 100")
_assert_eq(store2.count("储能"), 100, "「储能」只召回储能家族，不被同义词淹掉")
_assert_eq(store2.count("solar"), 100, "英文原词只召回英文那批")
_assert_eq(store2.count("光"), 200, "单字查询照样可用（切不出多字 token 时退回单字）")
_assert_eq(store2.count("不存在的东西xyzqw"), 0,
           "真无命中返回 0 条（不是 None）：单字万能词不参与召回")
_assert("光伏" in store2.search("光伏", limit=1)[0]["company"],
       "相关记录排在前面")
_assert_eq(match_expr(""), "", "空查询不构造 MATCH 表达式")
_assert(match_expr("光伏").startswith('"'), "MATCH token 全部带引号（防表达式注入）")
_assert('""' not in match_expr('光伏" OR 储能').replace('""', ''),
       "查询里的引号被转义，不会改变布尔结构")
_assert_eq(match_expr("光伏", expanded=False), '"光伏"',
           "原词层只用查询自己切出的词（同义词留给兜底层）")
_assert("solar" in match_expr("光伏"),
       "同义词层带扩展（与评分侧 expand_query 同源，原词层空了才用它）")
_long = "光伏储能电池逆变器组件太阳能支架控制系统"
_assert_eq(len(match_expr(_long, max_tokens=8).split(" OR ")), 8,
           "token 数量有上限（防超长查询把表达式撑爆）")

# ── 两级召回（原词层优先，零命中才用同义词层）：淹结果和漏召回都是线上事故 ──
tier_store = PoolStore(_new_tmp_dir())
_assert(tier_store.rebuild([BIG[0], BIG[1]], fingerprint_records([BIG[0], BIG[1]])),
        "光伏 + 储能各一条的小库重建")
_assert_eq(tier_store.count("储能电池"), 1,
           "原词层已有命中就到此为止：同义词里的「光伏/组件」记录不掺进来")
tier_store.rebuild([BIG[0]], fingerprint_records([BIG[0]]))
# 这条记录字面上没有 "solar"，但它的派生行业是「光伏太阳能」，同义词层能召回
_assert_eq(tier_store.count("solar"), 1,
           "原词层零命中 → 同义词层兜底，英文查询也能找到中文同品类记录")
_assert_eq(tier_store.count("储能", regions=["intl"]), 0,
           "兜底层也一样受分区裁剪约束：放宽召回绝不许越过国内外边界")

# payload 与分区列必须同源：按列能裁剪到的记录，返回体里就带着同一套分区键
# （写入端回写副本，不改调用方传入的原记录），否则界面按 industry 分组筛出的
# 公司在明细里显示成「未知行业」
_sample = store2.search("光伏", limit=5)
_assert(all(r.get("region") and r.get("quality") is not None for r in _sample),
        "返回记录带 region/quality（与视图列同一份派生结果）")
_assert(all(r.get("industry") == "光伏太阳能" for r in _sample),
        f"返回记录的 industry 就是聚合用的那一列  (实际={sorted({r.get('industry') for r in _sample})})")
_src = dict(BIG[0])
_store_probe2 = PoolStore(_new_tmp_dir())
_assert(_store_probe2.rebuild([_src], fingerprint_records([_src])), "不改原记录的小库重建")
_assert("industry" not in _src,
        "写入端只改副本：调用方传进来的记录对象不被就地补上派生键")


# ════════════════════════════════════════════════════════════════════════════
print("\n═══ 4. 判据口径：SQL 下压 == Python apply_filters ═══")

NOW = datetime.now()
FILTERS = [
    {}, {"has_mobile": True}, {"has_email": True}, {"has_phone": True},
    {"has_wechat": True}, {"scale": "large"}, {"scale": "medium"},
    {"scale": "small"}, {"freshness_days": 30}, {"freshness_days": 3650},
    {"region": "浙江"}, {"region": "广东"}, {"region": "Germany"},
    {"has_mobile": True, "has_email": True},
    {"has_contact": True},
    {"has_contact": True, "has_email": True},
    {"region": "江苏", "has_phone": True},
    {"scale": "medium", "freshness_days": 3650},
]
for f in FILTERS:
    f2 = dict(f)
    f2["now"] = NOW
    py = apply_filters(BIG, f2)
    sql = store2.search("", filters=f2, limit=len(BIG) + 50)
    py_norms = {r["company_norm"] for r in py}
    sql_norms = {r["company_norm"] for r in sql}
    _assert_eq(sql_norms, py_norms,
               f"筛选下压与 Python 同集合  ({json.dumps(f, ensure_ascii=False)})")
    _assert_eq(store2.count("", filters=f2), len(py),
               f"count 与 Python 条数一致  ({json.dumps(f, ensure_ascii=False)})")

# 「只看有联系方式」单独取证：真实池里有一成多只有官网/地址的展商线索行，
# 上面 400 条 BIG 全都有联系方式，那个筛选项在这里等于没被检验过。
LEAD = [_rec("线索甲实业有限公司", address="浙江省杭州市", website="www.jia.com"),
        _rec("线索乙贸易有限公司", address="广东省深圳市", products="光伏组件"),
        _rec("能打电话的丙有限公司", address="江苏省南京市", mobiles=["13800009999"]),
        _rec("只有邮箱的丁有限公司", emails=["ding@d.com"]),
        _rec("只有微信的戊有限公司", wechats=["wx_wu"])]
lead_store = PoolStore(_new_tmp_dir())
_assert(lead_store.rebuild(LEAD, fingerprint_records(LEAD)), "线索/联系方式混合小库重建")
_lf = {"has_contact": True, "now": NOW}
_assert_eq({r["company_norm"] for r in lead_store.search("", filters=_lf, limit=50)},
           {r["company_norm"] for r in apply_filters(LEAD, _lf)},
           "只看有联系方式：SQL 下压与 Python 判据取到同一批公司")
_keep = {r["company_norm"] for r in lead_store.search("", filters=_lf, limit=50)}
_assert("线索甲实业有限公司" not in _keep and "线索乙贸易有限公司" not in _keep,
        "只有官网/地址的线索行被排除在本次结果外")
_assert({"能打电话的丙有限公司", "只有邮箱的丁有限公司", "只有微信的戊有限公司"} <= _keep,
        "手机/邮箱/微信任一种都算「有联系方式」，绝不因筛选口径太窄漏掉能联系上的公司")
_assert_eq(lead_store.count("", filters=_lf), 3, "count 也按同一条判据给数")
# 不勾 = 全量：筛选是视图开关，不是把数据藏起来（用户要求「不要有任何遗漏」）
_assert_eq(lead_store.count(""), len(LEAD), "不勾选时一条都不少（全量 5 家都在）")

# 浏览态排序：没有关键词时，能外呼的必须排在只有官网/地址的前面
_br = lead_store.search("", limit=50)
_flags_seq = [1 if contact_flags(r) else 0 for r in _br]
_assert_eq(_flags_seq, sorted(_flags_seq, reverse=True),
           f"浏览态第一页先给有联系方式的公司  (实际={_flags_seq})")

# 概览覆盖度：reachable + lead_only == total，且与 flags 位逐个一致
_ls = lead_store.stats()
_assert_eq(_ls.get("total"), len(LEAD), "概览总数就是全量（覆盖度统计不改变总数口径）")
_assert_eq(_ls.get("reachable"), sum(1 for r in LEAD if contact_flags(r)),
           "概览「可外呼」数与 contact_flags 逐个核对一致")
_assert_eq(_ls.get("reachable") + _ls.get("lead_only"), _ls.get("total"),
           "可外呼 + 仅线索 == 总数（两个数必须互补，不能各说各的）")

# 分区裁剪：只勾国内/只勾国外
dom = store2.search("光伏", regions=["dom"], limit=500)
intl = store2.search("solar", regions=["intl"], limit=500)
_assert(len(dom) > 0 and all(record_region(r) == "dom" for r in dom),
       "分区裁剪只返回国内记录")
_assert(len(intl) > 0 and all(record_region(r) == "intl" for r in intl),
       "分区裁剪只返回国外记录")
_assert_eq(store2.count("数控机床", regions=["intl"]), 0,
           "分区裁剪真的生效：国外分区里没有机床记录")
_assert_eq(store2.count("光伏", regions=["intl"]), 100,
           "裁剪后的集合里只剩国外那 100 条光伏归类")


# ════════════════════════════════════════════════════════════════════════════
print("\n═══ 5. 增量：upsert / delete_norms / 流式遍历 / 聚合 ═══")

changed = [dict(BIG[0])]
changed[0]["emails"] = ["new@ginlong.com"]
added = [_rec("新增测试光能有限公司", products="光伏支架",
              address="安徽省合肥市", mobiles=["13611112222"])]
removed_norms = {BIG[5]["company_norm"]}
_assert(store2.upsert(changed + added, fingerprint_records(BIG)),
       "增量 upsert 成功（改 1 条 + 增 1 条）")
got = store2.get(changed[0]["company_norm"])
_assert("new@ginlong.com" in got["emails"], "改动后的内容立刻可见")
_assert(store2.get(added[0]["company_norm"]) is not None, "新增记录可按主键取到")
# 语料里每条记录的 source 都含「测试」，条数断言没有意义，直接查集合归属
_assert(added[0]["company_norm"] in
        {r["company_norm"] for r in store2.search("新增测试光能", limit=len(BIG) + 50)},
        "新增记录全文可检索")
_assert_eq(store2.count(""), len(BIG) + 1, "总数只 +1（没有重复插入）")
_assert(store2.upsert(changed + added, None), "重复 upsert 幂等")
_assert_eq(store2.count(""), len(BIG) + 1, "重复 upsert 不产生第二条")
_assert(store2.delete_norms(removed_norms), "按主键删除成功")
_assert_eq(store2.count(""), len(BIG), "删除后总数回到 400")
_assert_eq(store2.get(list(removed_norms)[0]), None, "删除后点查取不到")
_assert(list(removed_norms)[0] not in
        {r["company_norm"] for r in store2.search(list(removed_norms)[0],
                                                  limit=len(BIG) + 50)},
        "删除后全文检索也不再返回这条（FTS 行同步删掉）")

many_added = [_rec(f"批量增删验证{i}有限公司", products="阀门",
                   address="河北省石家庄市", mobiles=[f"1350000{i:04d}"])
              for i in range(1000)]
_assert(store2.upsert(many_added, None), "批量新增 1000 条（超过单批上限）")
_assert_eq(store2.count("批量增删验证"), 1000, "批量新增全部可检索")
_assert(store2.delete_norms({r["company_norm"] for r in many_added}),
        "批量删除 1000 条（旧实现只删前 900）")
_assert_eq(store2.count("批量增删验证"), 0, "批量删除一条不剩")

cur = list(store2.iter_records("光伏", regions=["dom"], page=37))
_assert_eq(len(cur), store2.count("光伏", regions=["dom"]),
           "流式遍历条数 == SQL 计数（分页游标不丢不重）")
fac_ind = dict(store2.facets("industry", limit=20))
# 200 条（光伏字面 100 + SolarWorks 归类 100）+ 上面 upsert 进来的「新增测试光能」
# （products=光伏支架 → 派生行业同为光伏太阳能）= 201
_assert_eq(fac_ind.get("光伏太阳能"), 201,
           "行业聚合下压：按视图列统计（含写入端补齐的派生行业）")
_assert_eq(store2.count("光伏"), fac_ind.get("光伏太阳能"),
           "聚合与检索的行业口径一致（同一批记录，两套算法不吵架）")
_st = store2.stats()
_assert_eq(_st["total"], store2.count(""), "统计总数与计数一致")
_assert_eq(_st["region"]["dom"] + _st["region"]["intl"], store2.count(""),
           "属地分布加总 = 全库")
_assert(_st["users"] >= 1 and _st["source_users"] == ["tester"],
       "上传者按 source_user 去重（不是把来源当用户数）")
_assert(_st["sources_count"] == 1, "来源类数单独统计")
_assert_eq(store2.find_company(BIG[7]["company_norm"], BIG[7]["company_norm"])
           ["company"], BIG[7]["company"], "公司名点查（精确）")
_assert(store2.find_company("", "机床装备7厂")["company"].startswith("机床装备"),
       "公司名点查（包含，空 norm 不做精确匹配）")
_assert_eq(store2.find_company("", "zzzz不存在"), None, "点查无结果返回 None")
try:
    store2.facets("payload", limit=5)
    _assert(False, "非白名单聚合列必须报错")
except ValueError:
    _assert(True, "聚合列白名单：非分区列直接拒绝（不拼进 SQL）")

# ── 封顶计数（对标 Elasticsearch track_total_hits）──────────────────────────
# 百万级里精确 count 宽词是秒级（实测 100 万行 / 22.4 万命中 4.18s，
# 数到 10 万就停只 174ms），而界面那两个总数只是给用户判断「范围大不大」，
# 不需要精确值——但「不需要精确」绝不等于可以报假数：触顶时必须带 exact=False，
# 让调用方说成「N 条以上」。
_full_pv = store2.count("光伏")
_assert(_full_pv > 20, f"前提成立：语料里「光伏」命中 {_full_pv} 条，够压出触顶分支")
_assert_eq(store2.count_capped("光伏", cap=_full_pv), (_full_pv, True),
           "命中数刚好等于上限：仍是精确值")
_assert_eq(store2.count_capped("光伏", cap=_full_pv - 1), (_full_pv - 1, False),
           "超上限一条：返回上限值 + exact=False（界面必须改说「以上」）")
_assert_eq(store2.count_capped("光伏", cap=10 ** 9), (_full_pv, True),
           "上限远高于命中数：退化成精确计数，与 count() 完全一致")
for _f in ({}, {"has_email": True}, {"freshness_days": 90}, {"region": "浙江"}):
    _f2 = dict(_f)
    _f2["now"] = NOW
    _exact_n = store2.count("光伏", regions=["dom"], filters=_f2)
    _assert_eq(store2.count_capped("光伏", regions=["dom"], filters=_f2,
                                   cap=10 ** 9), (_exact_n, True),
               f"封顶计数与精确计数逐条件一致（同一份下压 SQL，未触顶时不该有差）"
               f"  ({json.dumps(_f, ensure_ascii=False)})")
    _capped = store2.count_capped("光伏", regions=["dom"], filters=_f2, cap=1)
    _assert_eq(_capped, (1, _exact_n <= 1),
               f"cap=1 时命中多于 1 条即触顶  ({json.dumps(_f, ensure_ascii=False)})")
_assert_eq(empty.count_capped("光伏"), None,
           "库不可读时封顶计数同样 None（调用方须回退老链路）")

# 聚合也必须带筛选：概览的分布条形图与旁边的总数同屏出现，一个带筛选一个不带
# 就是同一屏两套口径（当年 data_pool.store_facets 根本没把 filters 传下来）
_all_dom = store2.count("", regions=["dom"])
_mail_dom = store2.count("", regions=["dom"], filters={"has_email": True, "now": NOW})
_assert(0 < _mail_dom < _all_dom,
        f"前提成立：国内记录里含邮箱的只是一部分（{_mail_dom}/{_all_dom}）")
_fac_all = dict(store2.facets("province", regions=["dom"]))
_fac_mail = dict(store2.facets("province", regions=["dom"],
                               filters={"has_email": True, "now": NOW}))
_assert_eq(sum(_fac_all.values()), _all_dom,
           f"不加筛选时分布加总 = 全量计数  (实际={_fac_all})")
_assert_eq(sum(_fac_mail.values()), _mail_dom,
           f"带筛选时分布加总 = 同条件计数（filters 真下压进聚合）  (实际={_fac_mail})")
_assert(_fac_mail != _fac_all,
        "带筛选的分布与不带筛选确实不同（filters 没下压时两者会一模一样）")


# ════════════════════════════════════════════════════════════════════════════
print("\n═══ 6. 大库等价性抽样（真实总表规模 2 万条）═══")

SCALE_N = 20000
BIG2 = []
for i in range(SCALE_N):
    base = BIG[i % len(BIG)]
    r = dict(base)
    r["company"] = f"{base['company'][:6]}{i}号"
    r["company_norm"] = r["company"].lower()
    r["source_time"] = (NOW - timedelta(days=i % 900)).isoformat(timespec="seconds")
    if i % 3 == 0:
        r["emails"] = [f"u{i}@x.com"]
    if i % 5 == 0:
        r["phones"] = [f"0{i}12345678"]
    BIG2.append(r)
store3 = PoolStore(_new_tmp_dir())
_assert(store3.rebuild(BIG2, fingerprint_records(BIG2)), "2 万条重建")
for f in ({}, {"has_email": True}, {"freshness_days": 30},
          {"freshness_days": 180}, {"region": "浙江"}, {"scale": "medium"},
          {"has_phone": True, "freshness_days": 90}):
    f2 = dict(f)
    f2["now"] = NOW
    py = apply_filters(BIG2, f2)
    n_sql = store3.count("", filters=f2)
    _assert_eq(n_sql, len(py),
               f"2 万级筛选条数一致  ({json.dumps(f, ensure_ascii=False)})")
t0 = __import__("time").time()
_ = store3.search("光伏", regions=["dom"], limit=100)
dt = __import__("time").time() - t0
_assert(dt < 1.0, f"2 万级检索延迟 {dt * 1000:.0f}ms（应远小于 1s）")


# ════════════════════════════════════════════════════════════════════════════
print("\n═══ 7. DataPoolManager 集成（假 OSS + 临时缓存目录）═══")

POOL_DIR = _new_tmp_dir()


class FakeOSS:
    """本地内存版 OSS：list_keys / download_json / upload_json*。"""

    def __init__(self):
        self.objects = {}
        self.fail_next = False

    def upload_json_atomic(self, key, payload, progress_callback=None):
        if self.fail_next:
            raise RuntimeError("模拟瞬时网络故障")
        self.objects[key] = json.loads(json.dumps(payload))
        return key

    def upload_json(self, key, payload):
        self.objects[key] = json.loads(json.dumps(payload))
        return key

    def download_json(self, key):
        from oss_pool_client import OSSPoolError
        if key not in self.objects:
            raise OSSPoolError(f"对象不存在: {key}")
        return self.objects[key], "etag"

    def list_keys(self, prefix=""):
        return sorted(k for k in self.objects if k.startswith(prefix))


def _mk_mgr(oss):
    return DataPoolManager(oss_client=oss, cache_dir=_new_tmp_dir())


oss = FakeOSS()
up = [_rec(f"池内公司{i}有限公司", products="光伏逆变器",
           address="浙江省宁波市", mobiles=[f"1380000{i:04d}"],
           source="测试光伏展.xlsx", source_user="alice")
      for i in range(60)]
up += [_rec(f"Pool Buyer {i} Ltd", products="solar mounting",
            country="Spain", address="Madrid", emails=[f"b{i}@x.com"],
            source="测试海外站.xlsx", source_user="bob") for i in range(40)]
oss.objects["users/alice/upload_1.json"] = {
    "username": "alice", "records": up[:60]}
oss.objects["users/bob/upload_2.json"] = {
    "username": "bob", "records": up[60:]}

mgr = _mk_mgr(oss)
master = mgr.get_merged_master()
_assert_eq(len(master), 100, "全量重建合并 100 条")
_assert(all(r.get("region") in ("dom", "intl") for r in master),
       "入库即派生属地键（读侧不再猜）")
_assert(all(r.get("industry") for r in master),
       "存量无行业标签的记录在加载入口补齐行业")
_assert(mgr._master_fp is None, "总表刚换，视图状态是作废的（不敢用旧视图）")
_assert(not mgr.store_ready(), "视图未对齐时 store_ready 为假")
_assert_eq(mgr.search_candidates("光伏"), None,
           "视图未对齐 → 候选返回 None（必须回退老链路，不能当零命中）")
_assert(mgr.sync_store(), "后台构建视图成功")
_assert(mgr.store_ready(), "构建后就绪")
_assert(mgr.sync_store(), "再次同步零成本返回真")

cand = mgr.search_candidates("光伏", limit=200)
# 60 条中文「光伏逆变器」+ 40 条 Pool Buyer（products=solar mounting → 写入端把
# 派生行业「光伏太阳能」回写进记录，原词层即可召回），机床类一条都不该有
_assert_eq(len(cand), 100, "候选来自视图：光伏 100 条")
_assert_eq(len([r for r in cand if record_region(r) == "dom"]), 60,
           "候选里国内 60 条（分区键与记录本身同源）")
intl_cand = mgr.search_candidates("solar", regions=["intl"], limit=200)
_assert_eq(len(intl_cand), 40, "分区裁剪 + 全文：国外 solar 40 条")
_stats = mgr.get_master_stats()
_assert_eq(_stats["users"], 2, "上传者数按 source_user 统计（alice + bob）")
_assert_eq(_stats["total_uploads"], 100, "总条数来自视图统计")
_assert_eq(_stats["sources"], 2, "来源类数单独口径")
_assert(_stats.get("from_view") is True, "统计走了视图（百万级不再全表遍历）")

# 联系方式覆盖度（回答「8 万里有多少是能直接打电话的」）：视图路径与内存回退
# 路径必须是同一个判据，否则概览写的数和勾了筛选筛出的数会互相打脸。
_assert_eq(mgr.reach_summary(), {"total": 100, "reachable": 100, "lead_only": 0},
           "覆盖度走视图：这 100 条全都有联系方式")
_oss_lead = FakeOSS()
_oss_lead.objects["users/carol/upload_1.json"] = {"username": "carol", "records": [
    # 四条公司名彼此字面距离足够远：这一段测的是「覆盖度口径」，不能被
    # 模糊归并（阈值 0.85）吃掉一条，否则总数口径就失去意义。
    # 线索行的形态取自真实数据：本次上传带来的 13,254 条无号码行**全部**带官网，
    # 它们按 2026-11-09 门禁口径（手机/固话/邮箱/官网五者取一）允许入库。
    _rec("宁波光伏组件示例有限公司", website="www.lead.com", address="浙江省杭州市",
         source="展商名录.xlsx", source_user="carol"),
    _rec("青岛风电塔筒示例有限公司", website="www.lead2.com",
         address="广东省深圳市", products="光伏支架",
         source="展商名录.xlsx", source_user="carol"),
    _rec("深圳储能逆变器示例有限公司", mobiles=["13711112222"], address="江苏省苏州市",
         source="展商名录.xlsx", source_user="carol"),
    # 联系方式与官网全空：按门禁就是不入库（不许悄悄放宽），但它必须出现在
    # 未入库清单里可核对——「不入库」和「消失」是两件事。
    _rec("苏州声呐示例有限公司", address="江苏省南京市", products="声呐探头",
         source="展商名录.xlsx", source_user="carol")]}
_mgr_lead = _mk_mgr(_oss_lead)
_mgr_lead.get_merged_master()
_assert(_mgr_lead.sync_store(), "线索混合小库视图构建成功")
_rs_view = _mgr_lead.reach_summary()
_assert_eq(_rs_view, {"total": 3, "reachable": 1, "lead_only": 2},
           "可外呼 1 家、仅线索 2 家（总数仍是 3：只做区分，一条都不删）")
_assert("苏州声呐示例有限公司" not in
        {r["company_norm"] for r in (_mgr_lead.search_candidates("", limit=50) or [])},
        "五类联系方式与官网全空的行按门禁不进池（口径未放宽）")
_assert_eq({r["company_norm"] for r in _mgr_lead.search_candidates(
               "", filters={"has_contact": True}, limit=50)},
           {"深圳储能逆变器示例有限公司"},
           "勾「只看有联系方式」后只剩可外呼那 1 家")
_no_contact = [r for r in (_mgr_lead.search_candidates("", limit=50) or [])
               if not contact_flags(r)]
_assert_eq(len(_no_contact), 2,
           "未删数据：只有官网的 2 条线索行仍然留在池里（只是排序靠后）")
_assert(any(str(r.get("website") or "") == "www.lead.com" for r in _no_contact),
        "官网线索行的字段原样保留（区分口径不等于丢弃线索）")
_mgr_lead._store = None            # 视图不可用 → 必须退回内存总表按同一条判据数
_assert_eq(_mgr_lead.reach_summary(), _rs_view,
           "覆盖度两条路径给同一个数（视图在不在都不改口径）")
_assert_eq(_mgr_lead.search_candidates("", filters={"has_contact": True}, limit=50),
           None, "视图不可用时候选返回 None（调用方必须回退，绝不能当成「池里没有了」）")

# 增量：新上传一个文件 → 同步 → 视图必须跟上（upsert 或作废）
oss.objects["users/alice/upload_3.json"] = {
    "username": "alice",
    "records": [_rec("增量新增光能有限公司", products="光伏支架",
                     address="山东省青岛市", mobiles=["13500009999"],
                     source="测试光伏展.xlsx", source_user="alice")]}
mgr._oss_keys_cache.clear()
mgr._master_cache_time = 0
mgr._master_cache = master              # 模拟上一轮已加载
refreshed = mgr.refresh_from_oss()
_assert(refreshed is not None and len(refreshed) == 101, "增量合并 1 条")
# 视图本来就在线 → 增量按 company_norm 走 upsert 推齐：既不整表重建，
# 也不作废等后台（作废会让下一次搜索直接回退全表扫描，百万级就是几秒卡顿）
_assert(mgr.store_ready() and mgr._view_fp == mgr._master_fp,
        "增量后视图与总表同源（upsert 推齐，不需要整表重建）")
_assert_eq(mgr.store_stats()["total"], 101, "统计跟着增量走")
_new_norms = {r["company_norm"]
              for r in (mgr.search_candidates("光伏支架", limit=500) or [])}
_assert("增量新增光能有限公司" in _new_norms, "增量数据立刻可检索")

# 视图在线时的增量：应走 upsert 而不是整表重建
mgr2 = _mk_mgr(oss)
mgr2.get_merged_master()
mgr2.sync_store()
_assert(mgr2.store_ready(), "第二个实例视图就绪")
view_fp_before = mgr2._view_fp
oss.objects["users/bob/upload_4.json"] = {
    "username": "bob",
    "records": [_rec("在线增量测试有限公司", products="数控机床",
                     address="辽宁省沈阳市", phones=["024-88880000"],
                     source="测试海外站.xlsx", source_user="bob")]}
mgr2._oss_keys_cache.clear()
mgr2._master_cache_time = 0
mgr2.refresh_from_oss()
_assert(mgr2._view_fp is not None and mgr2._view_fp != view_fp_before,
       "视图在线时增量走 upsert 推齐（不整表重建）")
_assert(mgr2.store_ready(), "upsert 后视图仍然就绪（没有被打回全表扫描）")
_assert("在线增量测试有限公司" in
        {r["company_norm"] for r in (mgr2.search_candidates("在线增量测试",
                                                            limit=500) or [])},
        "upsert 后新记录立即可检索")
_assert_eq(mgr2.store_stats()["total"], 102, "统计随 upsert 更新")

# 老链路一致性：视图给出的候选必须与全表扫描同口径
mgr3 = _mk_mgr(oss)
m3 = mgr3.get_merged_master()
mgr3.sync_store()
v_cand = mgr3.search_candidates("光伏", limit=500)
py_cand = [r for r in m3 if "光伏" in " ".join(search_fields(r))]
_assert_eq({r["company_norm"] for r in v_cand},
           {r["company_norm"] for r in py_cand},
           "视图候选集合 == 按 search_fields 全表扫描（口径唯一）")

# 倒排索引字段口径：只有邮箱能命中的记录也必须被索引覆盖
mgr4 = _mk_mgr(FakeOSS())
mgr4._master_cache = [_rec("邮箱only公司", products="", address="",
                           emails=["only@mail.com"])]
idx = DataPoolManager._build_inverted_index(mgr4._master_cache)
mgr4._save_local_state(mgr4._master_cache, idx, set())
_hits = mgr4.search_by_index("only@mail.com")
_assert(_hits == [0], "倒排索引覆盖邮箱等全部可检索字段（与 FTS 列同源）")
_assert(mgr4.search_by_index("邮箱only公司") == [0],
       "公司名 token 同样命中（三段文本都进了同一份索引）")


# ════════════════════════════════════════════════════════════════════════════
print("\n═══ 8. 降级：没有 Gold 层也要一切照旧 ═══")


class BrokenStore(PoolStore):
    def usable(self):
        return False

    def readable(self):
        return False


mgr5 = _mk_mgr(oss)
mgr5._store = BrokenStore(_new_tmp_dir())
mgr5._store_off = False
master5 = mgr5.get_merged_master()
_assert(not mgr5.store_ready(), "视图不可用时 store_ready 为假")
_assert_eq(mgr5.search_candidates("光伏"), None, "候选退回 None（老链路继续）")
_st5 = mgr5.get_master_stats()
_assert_eq(_st5["users"], 2, "统计退回内存遍历，数字仍按 source_user 口径")
_assert_eq(_st5["total_uploads"], 102, "内存统计条数正确")
_assert(_st5.get("from_view") is False, "统计明确标注来源是内存遍历")
_assert(mgr5.sync_store() is False, "构建失败只返回 False，不抛给调用方")

# 记录里没有 source_user 时（早期数据）：上传者至少算 1，别报「0 位用户」
mgr6 = _mk_mgr(FakeOSS())
mgr6._master_cache = [{"company": "无上传者公司", "company_norm": "无上传者公司",
                       "mobiles": ["13800000000"], "source": "x.xlsx"}]
_assert(mgr6._compute_local_stats()["users"] >= 1, "缺 source_user 时不报 0 位用户")


# ════════════════════════════════════════════════════════════════════════════
print("\n═══ 9. 清洗报告：合并、渲染、幂等（界面上的数字必须对得上）═══")

from ingest_cleaning import format_report, merge_reports

# 两份独立报告：多文件导入时 GUI 要把它们合并成一份用户可见的汇总
_k1, _r1 = clean_records([
    _rec("报告甲有限公司", products="光伏组件", address="广东省深圳市",
         mobiles=["13600001111"]),
    _rec("无联系方式甲", products="阀门"),                    # 门禁剔除
])
_k2, _r2 = clean_records([
    _rec("报告乙有限公司", products="储能电池", address="浙江省杭州市",
         mobiles=["13500004444"], emails=["sol@b.com"]),
    _rec("   ", mobiles=["13500002222"]),                    # 无名剔除
])
_m = merge_reports({}, _r1)
merge_reports(_m, _r2)
_assert_eq(_m["in"], 4, "合并后读取条数 = 各文件之和")
_assert_eq(_m["kept"], 2, "合并后可入库条数")
_assert_eq(_m["dropped"], 2, "合并后剔除条数")
_assert_eq(sum(_m["drop_reasons"].values()), 2, "剔除原因计数合并后总量守恒")
_assert_eq(_m["region"]["dom"], 2, "属地分布合并")
_assert_eq(_m["province"].get("广东"), 1, "省份分布合并（甲）")
_assert("浙江" in _m["province"], "省份分布合并（乙）")
_assert_eq(_m["with_phone"] + _m["with_email"], _m["kept"] + 1,
           "联系方式覆盖合并：一条既有电话又有邮箱，按字段各自计数")
_assert_eq(merge_reports({}, _r1)["kept"], _r1["kept"],
           "合并空目标 = 原报告（不丢字段）")
_identity = dict(_r1)
before = (_identity["kept"], _identity["dropped"])
merge_reports(_identity, {})
_assert_eq((_identity["kept"], _identity["dropped"]), before,
           "合并空报告不改变目标（幂等）")

# 样本上限：几十个文件的被剔样本不能无限堆积到界面上
_many_in = [_rec(f"无联系方式公司{i}", products="阀门") for i in range(30)]
_many_in.append(_rec("样本上限测试有限公司", products="光伏逆变器",
                     address="广东省深圳市", mobiles=["13800007777"]))
_kx, _rx = clean_records(_many_in)
_cap = merge_reports({}, _rx)
_assert_eq(_cap["kept"], 1, "无联系方式的记录被门禁剔除")
_assert_eq(_cap["dropped"], 30, "剔除条数如实统计（不是只统计样本的 20 条）")
_assert_eq(len(_cap["dropped_samples"]), 20, "被剔样本最多留 20 条（可回溯够用即可）")
_merge_cap = merge_reports(dict(_rx), dict(_rx))
_assert_eq(len(_merge_cap["dropped_samples"]), 20,
           "多文件合并后样本仍封顶 20（30 个文件也不会撑爆界面）")

# 整表跳过信息随报告一起渲染（说明表被拦在入库前，用户要看得见）
_sk = dict(_r1)
_sk["skipped_sheets"] = [{"sheet": "采集进度", "entity_ratio": 0.05}]
_txt = format_report(_sk)
_assert("整表跳过" in _txt and "采集进度" in _txt, "报告含整表跳过说明")
_assert("被剔记录样例" in _txt, "报告含被剔样本")
_assert("属地分布：国内" in _txt, "报告含属地分布")

# 幂等：预览跑一遍、真正上传再跑一遍，第二遍必须零剔除——
# 否则「预览说能入库 N 条、实际入库 M 条」，界面上的数字就是骗人
_k_once, _r_once = clean_records([dict(r) for r in CORPUS])
_k_twice, _r_twice = clean_records(_k_once)
_assert_eq(_r_twice["dropped"], 0, "清洗过的记录再过一次清洗零剔除（幂等）")
_assert_eq(_r_twice["kept"], _r_once["kept"], "第二遍条数不变")
_assert_eq(_r_twice["region"], _r_once["region"], "第二遍属地分布不变")
_assert_eq(_r_twice["industry"], _r_once["industry"], "第二遍行业分布不变")

# 上传口的 kept 就是真正入库的条数（GUI 提示与审计日志都用这个数）
mgr7 = _mk_mgr(FakeOSS())
_rep_up = {}
_up_name = mgr7.upload_session([dict(r) for r in _k_once],
                               report_out=_rep_up)
_assert_eq(_rep_up["kept"], len(_k_once), "上传报告 kept = 实际入库条数")
_assert(_up_name.endswith(".json"), "上传返回文件名")
_assert(len(mgr7.oss.objects) >= 1, "上传确实落了对象")


# ════════════════════════════════════════════════════════════════════════════
print("\n═══ 10. 地区分区键：证据链、国名归一、与属地判据的一致性 ═══")

import data_pool as _dp
import ingest_cleaning as _ic
from ingest_cleaning import (INDUSTRY_UNKNOWN, PARTITION_RULE_VERSION,
                             PROVINCE_UNKNOWN, derive_province,
                             partition_industry, partition_province)

# 10.1 维度字典双向同步。判据（data_pool）与分区键（ingest_cleaning）各维护一份
#      词表时，「国内外」与「地区」两个筛选条件迟早互相矛盾——实测存量池 55,219 条
#      里有 111 条「属地=国外 但 地区=甘肃/台湾」，就是这个漂移的形状。
_assert_eq(sorted(set(_ic._LATIN_PROVINCES) - set(_dp._CN_LATIN_GEO_WORDS)), [],
           "省级拉丁写法全部进了属地判据词表")
_hk_tw = set(_ic._LATIN_CITY_GROUPS["台湾"]) | set(_ic._LATIN_CITY_GROUPS["香港"])
_assert_eq(sorted(_hk_tw - set(_dp._CN_LATIN_GEO_WORDS)), [],
           "港澳台城市写法进了判据词表（英文地址只写 Tainan 的那类公司）")
_assert(set(_ic._COUNTRY_TO_CN) == set(_dp._FOREIGN_COUNTRY_WORDS),
        "国名词表两侧完全一致（判据认得的写法分区能归一，反过来也是）")
_assert_eq(sorted(set(_dp._CN_LATIN_GEO_WORDS) - set(_ic._LATIN_CITIES)
                  - set(_ic._LATIN_PROVINCES) - {"china", "prc"}), [],
           "判据里的中国地名都能归一进分区表（不留「国内+未知地区」的死角）")
_assert(_dp._FOREIGN_COUNTRY_RE.search("Contrada Blocco Torrazze, Catania, Italy"),
        "词表改动确实重编译进了正则（Italy 这一类此前两侧都漏）")

# 10.2 同形歧义词两边都不认：宁可分区落哨兵，也不猜一个省份（猜错会让筛选给错结果）
_assert_eq(sorted(_ic._LATIN_AMBIGUOUS_CITIES), ["fengxian", "yulin"],
           "构建期自动识别的同形歧义词清单")
_assert_eq(partition_province({"address": "Yulin", "is_domestic": True,
                               "region": "dom", "region_rule": _dp.REGION_RULE_VERSION}),
           PROVINCE_UNKNOWN, "广西玉林/陕西榆林同形 → 不猜，落哨兵桶")

# 10.3 号码写法归一：判据与分区必须用同一条归一规则，否则「国外+未知地区」
_rec_sp = {"company": "Solar Mounting S.L.", "address": "C/ Balmes 297, Barcelona",
           "phones": ["00 34-93-123 45 67"]}
_assert_eq(record_region(_rec_sp), "intl", "00 国际冠码在属地判据里算境外")
_assert_eq(derive_province(_rec_sp, "intl"), "西班牙", "同一号码派生出国家（两侧同结论）")

# 10.4 港澳台：地址/号码/域名三种写法都落省份，且属地为国内（口径里港澳台属国内）
for _gc, _want in (({"address": "No. 406, Xinhu 1st Road, Neihu District, Taipei"}, "台湾"),
                   ({"address": "12 F, World-Wide Building, 19 Lockhart Road",
                     "phones": ["+852 2861 8222"]}, "香港"),
                   ({"address": "1A Rua de Sao Paulo",
                     "emails": ["info@empresa.mo"]}, "澳门")):
    _assert_eq(record_region(_gc), "dom", f"{_want}记录属地为国内  ({_gc.get('address')})")
    _assert_eq(partition_province(_gc), _want, f"{_want}按省份归类，不落未知地区")

# 10.5 一致性不变量：属地=国外的记录，地区分区键绝不许是某个中国省份
for _adv in ({"address": "Hauptstrasse 1, 8001 Zürich, Switzerland",
              "phones": ["+86 755 1234 5678"]},
             {"address": "No. 88 Dali Street, Nicosia, Cyprus"},
             {"address": "No.1 Xinjing Road, Tianshan Zone, Gaoyou, Yangzhou",
              "country": "Germany"},
             {"address": "Política Communications, Madrid", "website": "http://p.co.uk"}):
    _p = partition_province(_adv)
    if record_region(_adv) == "intl":
        _assert(_p not in _ic._PROVINCES,
                f"国外记录不给中国省份  (地区={_p!r} / {_adv.get('address')[:38]})")
    else:
        _assert(bool(_p), f"国内记录也不给国家名  (地区={_p!r})")

# 10.6 分区键规则版本：旧口径写死的值必须能被新证据链升级，同版本内保持幂等
_legacy = _rec("Solar Mounting Co Ltd", products="solar racking",
               address="C/ Balmes 297, 8006, Barcelona, Spain", country="Spain",
               emails=["b1@x.com"])
_legacy["province"] = "未知地区"
_legacy["province_rule"] = "1"
_bumps = enrich_partition_fields([_legacy])
_assert_eq(_legacy["province"], "西班牙", "旧版本分区键按当前证据链重算（不被冻住）")
_assert_eq(_legacy["province_rule"], PARTITION_RULE_VERSION, "重算后打上当前版本标记")
_assert_eq(_bumps.get("省份补派生"), 1, "补算计数如实报给调用方")
_manual = _rec("用户自己填了地区的有限公司", products="锂电池",
               address="江苏省南京市", emails=["u@v.com"])
enrich_partition_fields([_manual])
_manual["province"] = "用户填的"
_assert_eq(enrich_partition_fields([_manual]), {},
           "同版本内已有地区不重算（幂等，也不覆盖用户填的值）")
_assert_eq(_manual["province"], "用户填的", "用户填的地区不被派生结果覆盖")

# 10.7 哨兵桶让分布自洽：地区分布加总 == 保留条数（报告与视图都必须成立）
_k2, _r2 = clean_records([dict(x) for x in CORPUS])
_assert_eq(sum((_r2.get("province") or {}).values()), _r2["kept"],
           "清洗报告地区分布加总 == kept（哨兵兜住每一条，不再出现两套数）")
from ingest_cleaning import format_report as _fmt2
_assert("地区分布：" in _fmt2(_r2), "清洗报告新增地区分布行（用户看得见分区结果）")

_fac = store.facets("province", limit=500)
_assert_eq(sum(c for name, c in _fac if name), store.count(""),
           "视图地区分布加总 == 全库计数（province 列非空是硬约束）")
_view = sorted(r["company"] for r in store.search("", filters={"region": "以色列"},
                                                  limit=500))
_local = sorted(r["company"] for r in apply_filters(kept, {"region": "以色列"}))
_assert_eq(_view, _local, "地区等值筛选：视图下压 == Python 侧（口径唯一实现）")
_assert_eq(partition_industry({"products": "光伏组件"}), "光伏太阳能",
           "行业分区键与清洗层同一实现")
_assert_eq(partition_industry({"products": "什么都不是"}), _ic.INDUSTRY_UNKNOWN,
           "判不出行业落未分类哨兵，不返回空串")

# 「行业」列填的是经济类型（私营企业/个体经营/外资企业）时等于没填：归一表把它
# 映射成空之后必须继续走关键词派生。旧实现在这里直接返回哨兵，导致同一条记录
# 第一次清洗落「未分类」、第二次才落「环保与水处理」——导入预览与真正入库两套数
_DIRTY = {"company": "海南君若排水服务有限公司",
          "products": "管道疏通,排水应急抢修,污水池运维清理",
          "industry": "私营企业", "address": "海南省海口市",
          "source": "深海装备.xlsx", "mobiles": ["13006076383"]}
_assert_eq(partition_industry(dict(_DIRTY)), "环保与水处理",
           "经济类型脏值不占用行业分区键，回落到关键词派生")
_p1 = clean_records([dict(_DIRTY)])[0][0]
_p2 = clean_records([dict(_p1)])[0][0]
_assert_eq(_p2["industry"], _p1["industry"],
           f"连洗两遍行业不变  (一次={_p1['industry']}, 二次={_p2['industry']})")
_assert_eq(_p2["province"], _p1["province"], "连洗两遍地区分区键不变")
_assert_eq(_p2["region"], _p1["region"], "连洗两遍属地不变")
for _junk in ("个体经营", "外资企业", "合资企业", "生产企业", "有限责任公司"):
    _assert_eq(partition_industry({"company": "宁波某光伏元件厂", "industry": _junk}),
               "光伏太阳能", f"「{_junk}」不充当行业结论")
_assert_eq(partition_industry({"company": "某公司", "industry": "光伏",
                               "products": "储能电池"}), "光伏",
           "有意义的显式行业原样保留，不派生覆盖用户填的值")

# ════════════════════════════════════════════════════════════════════════════
print("\n═══ 11. 说明表拦截：结尾句标点判据 + 尾缀修复（能修的绝不扔）═══")

from ingest_cleaning import is_valid_company_name as _valid, sheet_usability as _su

# 历史缺陷：海洋展「采集进度」说明表被当企业名单入库。旧散文判据要求
# 「长度>40 且无组织尾缀」，而这些说明行都带「。」、句子里又恰好出现
# 「企业/公司」字样，两个条件同时不成立 → 整表畅通无阻。
_PROSE = [
    "无手机号、掩码手机号、地区不明或非沿海、业务不匹配、重复企业及重复号码不计入。",
    "号码可在保存的公开来源页检出;检查11位格式,未拨测,未确认现任归属。",
    "包含前次采集缓存与本轮新读取页面。每条保留查阅日期,缓存不冒充本轮在线复核。",
    "深海开发、海底作业、商业潜水、海洋能源、水域救援与水上救生及明确相关的海工/船舶配套。",
    "按企业名称去重计数;同一企业多个手机号仍计1家;不同企业共用号码不重复计入。",
]
for _p in _PROSE:
    _assert(not _valid(_p), f"以句标点收尾的说明文字判为散文  ({_p[:18]}…)")
# 表级门禁因此能认出这是说明表（此前实体占比 0.86 被当数据表放行，全靠记录级
# 门禁兜底；一旦说明行里带上号码就会直接入库）
_usable, _ratio = _su([_rec(p, mobiles=["13800000009"]) for p in _PROSE])
_assert(not _usable and _ratio == 0.0,
        f"整表判定为说明表（实体占比 0）  (usable={_usable}, ratio={_ratio})")
# 同一批行即便补上联系方式，也仍在记录级被拦下（两道防线各自成立）
_pk, _pr = clean_records([_rec(p, mobiles=["13800000009"]) for p in _PROSE])
_assert_eq(len(_pk), 0, "说明行带号码也不入库（记录级门禁独立成立）")
_assert_eq(_pr["drop_reasons"].get("公司名不是企业实体"), len(_PROSE),
           "剔除原因记为「公司名不是企业实体」而不是含糊的无联系方式")

# 反向：合法企业名带收尾标点属于抓取残留，必须修好保留而不是丢弃
_fixed, _rsn, _fx = clean_one({"company": "深圳市某科技有限公司。",
                               "phones": ["13800001111"]})
_assert_eq(_rsn, "", "带句号的中国公司名不被剔除")
_assert_eq(_fixed["company"], "深圳市某科技有限公司", "收尾句号被修复掉")
_assert_eq(_fx.get("公司名结尾标点修正"), 1, "修复计入报告（用户看得见改了什么）")
_paren, _r2, _ = clean_one({"company": "上海某工程有限公司（筹）。",
                            "phones": ["13800002222"]})
_assert_eq(_paren["company"], "上海某工程有限公司（筹）",
           "括注结尾的合法实体名（…（筹））同样修复保留")
_ltd, _r3, _ = clean_one({"company": "3M Company.", "phones": ["13800003333"]})
_assert_eq(_ltd["company"], "3M Company", "英文缩写式句点也清掉，实体保留")

# 全量取证：以 . ! ? 收尾的原始名称里，捷克 s.r.o.、欧洲品牌 "Ecrowd!"
# 这类是真名——判据只看全角句标点与分号，绝不用 ASCII 标点判散文
for _keep in ["Ecrowd!", "Nabach Wir machen Was!", "ABCO expert s.r.o.",
              "Luoyang Sanwu Cable Co., Ltd.,", "Solar Energy Ltd."]:
    _assert(_valid(_keep), f"含 ASCII 标点/句点的真实名称保持有效  ({_keep!r})")
_assert(_valid("Blackwell Energy"), "无组织尾缀的海外名称照旧有效")
_assert(not _valid("www.solarportal24.de"), "URL 型名称仍被拦下")

# 端到端：说明表 + 名单表混在一个文件里 → 只入库名单，并在报告里点名跳过
try:
    from openpyxl import Workbook

    _xlsx = os.path.join(_new_tmp_dir(), "混合文件.xlsx")
    _wb = Workbook()
    _ws = _wb.active
    _ws.title = "采集进度"
    _ws.append(["项目", "说明"])
    for _row_i, _p in enumerate(_PROSE, start=1):
        _ws.append([f"条目{_row_i}", _p])
    _ws2 = _wb.create_sheet("企业名单")
    _ws2.append(["公司名", "电话", "地址"])
    _ws2.append(["宁波某光伏有限公司", "13800004444", "浙江省宁波市"])
    _ws2.append(["深圳某储能有限公司", "13800005555", "广东省深圳市"])
    _wb.save(_xlsx)
    _rep = {}
    _out = DataPoolManager.read_records_from_excel(_xlsx, report_out=_rep)
    _assert_eq(len(_out), 2, "混表文件只入库企业名单那 2 条")
    _sk = [s.get("sheet") for s in (_rep.get("skipped_sheets") or [])]
    _assert("采集进度" in _sk,
            f"说明表在表级就被跳过并写进报告  (skipped={_sk})")
    _assert_eq(_rep.get("dropped"), 0,
               "表级拦截后不再靠记录级剔行（原因归位到正确的一层）")
except ImportError:  # 没有 openpyxl 时跳过端到端，单元判据仍然生效
    _assert(True, "缺 openpyxl，端到端混表用例跳过")

# ════════════════════════════════════════════════════════════════════════════
print("\n═══ 12. 合并层派生列重算（属地与地区必须原子一致）═══")
# D13 取证：合并层的 pool_merge 曾**在同一个函数里用两套属地口径**——
# is_domestic/region 取「组内显式值多数投票」，province 却按「证据判据」派生，
# 于是合并结果落成「region=dom 而 province=德国」这种自相矛盾的记录（实测
# 上传后对账抓到 2 条、按并集证据重判后放大到 24 条）。现在只留一套口径：
# **并集证据判属地**（record_region 的多信号加权，与搜索过滤/清洗分区键/导出
# 范围完全同源），投票只在并集后仍然零证据时兜底。
from data_pool import REGION_RULE_VERSION, _domestic_signals
from pool_merge import dedup_exact as _dedup_exact, merge_group as _merge_group


def _mk(company, **kw):
    return _rec(company, **kw)


# 派生列 = 判据的输出，不是业务事实
_DERIVED_KEYS = ("region", "is_domestic", "province", "industry", "quality",
                 "region_rule", "province_rule", "_region_key", "_region_ver")

# 12.1 并集证据优先于「来源显式值的多数投票」：三条里两条自称国内，但它们的
# 业务证据是空的，唯一的实质证据（country=Germany + .de 官网 + .de 邮箱）在第三条
_x1 = _mk("投票与证据验证有限公司", products="solar",
          region="dom", is_domestic=True, province="广东",
          region_rule=REGION_RULE_VERSION, province_rule=PARTITION_RULE_VERSION)
_x2 = _mk("投票与证据验证有限公司", products="solar",
          region="dom", is_domestic=True, province="广东",
          region_rule=REGION_RULE_VERSION, province_rule=PARTITION_RULE_VERSION)
_y = _mk("投票与证据验证有限公司", country="Germany", website="https://www.probe.de",
         emails=["z@probe.de"],
         region="intl", is_domestic=False, province="德国",
         region_rule=REGION_RULE_VERSION, province_rule=PARTITION_RULE_VERSION)
_votes = (sum(1 for r in (_x1, _x2, _y) if r["is_domestic"] is True),
          sum(1 for r in (_x1, _x2, _y) if r["is_domestic"] is False))
_assert_eq(_votes, (2, 1), "按旧口径（显式值投票）这里会判国内")
_g = _merge_group([dict(_x1), dict(_x2), dict(_y)])
_ev = {k: v for k, v in _g.items() if k not in _DERIVED_KEYS}
_assert_eq(_domestic_signals(_ev), (2, 5),
           "并集证据：中文名+有限公司 2 分境内，country/官网/邮箱 5 分境外")
_assert_eq(_g["region"], "intl", "合并按并集证据判属地，投票不再凌驾证据")
_assert_eq(_g["province"], "德国", "地区分区键用同一个属地派生（不再各算一半）")
_assert_eq(_g["is_domestic"], False, "is_domestic 与 region 同步落")
_assert_eq(_g.get("region_rule"), REGION_RULE_VERSION, "合并结果打上当前属地判据版本")
_assert_eq(_g.get("province_rule"), PARTITION_RULE_VERSION, "合并结果打上当前分区键版本")
_assert(not [k for k in _g if k.startswith("_")], "合并结果不带运行时缓存键")

# 12.2 零证据才退回投票：组内谁都没有地址/号码/国名/域名后缀时，
# 多个来源已有的结论比「按公司名语言瞎猜」信息量大
_z1 = _mk("Acme Trading", region="dom", is_domestic=True,
          region_rule=REGION_RULE_VERSION)
_z2 = _mk("Acme Trading", region="dom", is_domestic=True,
          region_rule=REGION_RULE_VERSION)
_z3 = _mk("Acme Trading", region="intl", is_domestic=False,
          region_rule=REGION_RULE_VERSION)
_gz = _merge_group([dict(_z1), dict(_z2), dict(_z3)])
_z_ev = {k: v for k, v in _gz.items() if k not in _DERIVED_KEYS}
_assert_eq(_domestic_signals(_z_ev), (0, 0), "这一组并集后仍然零证据")
_assert_eq(_gz["is_domestic"], True, "零证据时按组内显式值多数投票兜底")
_assert_eq(_gz["region"], "dom", "region 与投票结论一致")

# 12.3 _merge_records 出口（清洗层 + 合并层 + 兜底核齐）必须整体自洽
_a = _mk("属地并集验证有限公司", products="solar inverter",
         address="深圳市南山区科技园", mobiles=["13800008888"],
         region="dom", is_domestic=True, province="广东",
         region_rule=REGION_RULE_VERSION, province_rule=PARTITION_RULE_VERSION)
_b = _mk("属地并集验证有限公司", country="Germany", website="https://www.mergeprobe.de",
         emails=["a@mergeprobe.de"], phones=["+49-151-2345678"],
         region="intl", is_domestic=False, province="德国",
         region_rule=REGION_RULE_VERSION, province_rule=PARTITION_RULE_VERSION)
_assert(_a["region"] != _b["region"], "两个来源的属地标记本就相反（合并必须重算的前提）")
_evidence = {k: v for k, v in _dedup_exact([dict(_a), dict(_b)])[0].items()
             if k not in _DERIVED_KEYS}
_assert_eq(record_region(_evidence), "intl",
           "并集证据（country=Germany + .de 官网邮箱 + 德国号码）判国外")

_m = DataPoolManager._merge_records([dict(_a), dict(_b)])
_assert_eq(len(_m), 1, "同一 company_norm 归并成一条")
_m0 = _m[0]
_fresh = {k: v for k, v in _m0.items() if k not in _DERIVED_KEYS}
_assert_eq(_m0["region"], record_region(_fresh),
           f"合并出口属地 = 对并集证据现算  (region={_m0['region']!r})")
_assert_eq(_m0["region"], "intl", "合并出口不沿用任何来源的旧属地标记")
_assert_eq(_m0["is_domestic"], _m0["region"] == "dom",
           "合并出口 is_domestic 与 region 一致")
_assert_eq(_m0["province"], "德国",
           f"地区分区键随属地原子重算  (province={_m0['province']!r})")
_assert(not _ic._province_conflicts(_m0["province"], _m0["region"]),
        "合并出口不存在「属地与地区互相矛盾」的记录")
_assert_eq(_m0.get("region_rule"), REGION_RULE_VERSION, "合并出口补上属地判据版本")
_assert_eq(_m0.get("province_rule"), PARTITION_RULE_VERSION, "合并出口补上分区键版本")
_assert(not [k for k in _m0 if k.startswith("_")],
        "下划线运行时缓存键不得写进总表/OSS")
_assert("13800008888" in _m0["mobiles"] and "a@mergeprobe.de" in _m0["emails"],
        "重算派生列不牺牲并集：两个来源的联系方式都还在")
_assert_eq(DataPoolManager._merge_records([dict(_m0)])[0]["region"], "intl",
           "对已核齐的总表再走一次合并：结果不变（幂等，同步可反复执行）")


# 12.4 存量补齐：属地本轮翻侧 → 地区必须跟着原子重算（旧值来自旧属地，留着就是错的）
_flip = _mk("Renova Systems S.r.l.", products="solar mounting",
            address="Via Roma 1, Milano, Italy", country="Italy",
            website="renovasystems.it", emails=["info@renovasystems.it"])
_flip.update(region="dom", is_domestic=True, province="广东")   # 旧口径写下的值，无版本标记
_c = enrich_partition_fields([_flip])
_assert_eq(_flip["region"], "intl", f"属地按当前判据核齐  ({_c})")
_assert_eq(_flip["province"], "意大利", "属地翻侧后地区跟着重算，不再留「广东」")
_assert_eq(_c.get("地区随属地重算"), 1, "随属地重算单独计数，报告里看得出口径")
_assert_eq(enrich_partition_fields([_flip]), {}, "重算后幂等（第二遍一字不改）")

# 12.5 存量补齐：属地没变、但已存地区与新属地定义上矛盾 → 也要修（一致性闸门补到「已存值」上）
_stale = _mk("Renova S.r.l.", products="solar mounting",
             address="Hauptstrasse 1, 8001 Zurich, Switzerland",
             emails=["info@renova.it"], region="intl", is_domestic=False,
             province="广东", region_rule=REGION_RULE_VERSION,
             province_rule=PARTITION_RULE_VERSION)
_c2 = enrich_partition_fields([_stale])
_assert_eq(_c2.get("属地补派生"), None, "属地三件套齐备，本轮不动属地")
_assert_eq(_stale["province"], "瑞士", f"国外记录挂广东被判据重算  ({_c2})")
_assert_eq(_c2.get("地区矛盾修正"), 1, "矛盾修正单独计数")
_assert_eq(enrich_partition_fields([_stale]), {}, "修正后幂等")

# 12.6 自由文本不算矛盾：不宣称省份的地区与属地不冲突，删掉才是丢用户数据
_free = _mk("只填了大区的公司", products="锂电池", industry="光伏太阳能",
            address="工业区", mobiles=["13800009999"], province="华南",
            region="dom", is_domestic=True, region_rule=REGION_RULE_VERSION,
            province_rule=PARTITION_RULE_VERSION)
_assert_eq(enrich_partition_fields([_free]), {}, "「华南」这种自由文本不动")
_assert_eq(_free["province"], "华南", "自由文本地区原样保留")
_assert(not _ic._province_conflicts("华南", "dom"), "自由文本不构成矛盾")
_assert(_ic._province_conflicts("广东", "intl"), "国外 + 中国省份 = 矛盾")
_assert(_ic._province_conflicts("美国", "dom"), "国内 + 国家名 = 矛盾")
_assert(not _ic._province_conflicts(PROVINCE_UNKNOWN, "intl"), "哨兵不算矛盾（走补算路径）")
_kept = _mk("分区键原语义验证公司", region="intl", province="广东")
_assert_eq(_ic.partition_province(_kept, "intl"), "广东",
           "不 force 时已存值原样返回（保持 partition_province 原语义）")
_assert_eq(_ic.partition_province(_kept, "intl", force=True), "未知地区",
           "force 时按当前证据链重算，判不出就落哨兵")

# 12.7 上传主链路端到端：清洗 + 归一后绝不留矛盾记录（写侧兜底）
_e2e, _rep12 = clean_records([
    _mk("端到端矛盾一号", products="solar", address="深圳市南山区",
        mobiles=["13800001234"], country="Germany", website="e2e1.de"),
    _mk("端到端矛盾二号", products="solar", address="Hauptstrasse 1, Berlin",
        phones=["+49-30-112233"], country="Germany"),
])
_conflict = [r["company"] for r in _e2e
             if r["region"] == "intl" and r["province"] in _ic._PROVINCES]
_assert_eq(_conflict, [], f"上传口径下零矛盾记录  (n={len(_e2e)})")

# ════════════════════════════════════════════════════════════════════════════
print("\n═══ 13. 确定性：Bronze 重放与增量收敛必须给出同一张总表 ═══")
# D14 取证：_download_records 用 as_completed 装配并行下载结果，记录顺序 = 网络
# 完成时序。而合并层是**顺序敏感**的（组内 base 取首现、_first_nonempty 取首个
# 非空值），于是同一批 Bronze 数据两次全量重建给出不同的派生列（真实 8.7 万条
# 实测漂移 128 组），增量合并与全量重建也不收敛（87,546 vs 87,545）。
# 要求的是数据工程的硬不变量：同一快照重放逐条等价，compaction ≡ full compaction
# ——否则用户每次同步都可能看到同一公司的行业/地区悄悄变了。
import time as _time


class SlowOSS(FakeOSS):
    """越靠前的 key 下载越慢：完成序与 key 序完全相反，专治 as_completed 回归。"""

    DELAY = 0.02

    def download_json(self, key):
        keys = self.list_keys("")
        _time.sleep(self.DELAY * max(1, len(keys) - keys.index(key)))
        return super().download_json(key)


_sow = SlowOSS()
for _i in range(4):
    _sow.objects[f"users/alice/upload_{_i}.json"] = {
        "username": "alice",
        "records": [_rec(f"装配序{_i}有限公司", products="光伏组件",
                         address="江苏省南京市", mobiles=[f"138{_i}0000000"],
                         source=f"装配{_i}.xlsx", source_user="alice")]}
_mgr_sow = _mk_mgr(_sow)
_got_recs, _got_durable = _mgr_sow._download_records(_mgr_sow._list_upload_keys())
_assert_eq([r["company"] for r in _got_recs],
           [f"装配序{i}有限公司" for i in range(4)],
           "并行下载仍按 key 规范序装配（完成序与 key 序相反也不受影响）")
_assert_eq(_got_durable, {f"users/alice/upload_{i}.json" for i in range(4)},
           "装配序改造不影响失败分级：durable 集合照旧")

# —— 增量收敛 ≡ 全量重建（真实规模上差的正是这 1 条）——
_cvg = FakeOSS()
_A = [_rec("收敛验证甲有限公司", products="光伏逆变器", address="浙江省杭州市",
           mobiles=["13811110001"], industry="光伏太阳能",
           source="展会A.xlsx", source_user="alice"),
      _rec("收敛验证乙有限公司", products="储能电池", address="广东省深圳市",
           mobiles=["13811110002"], industry="光伏太阳能",
           source="展会A.xlsx", source_user="alice")]
# 同一公司来自另一个展会，行业/地址与 A 冲突：分区键的胜负由多数票 + 新鲜度决定
_B = [_rec("收敛验证甲有限公司", products="数控机床", address="辽宁省沈阳市",
           mobiles=["13811110003"], industry="机械与机床",
           source="展会B.xlsx", source_user="bob",
           source_time="2026-10-20T10:00:00"),
      _rec("收敛验证丙有限公司", products="水处理设备", address="山东省青岛市",
           mobiles=["13811110004"], industry="环保与水处理",
           source="展会B.xlsx", source_user="bob",
           source_time="2026-10-20T10:00:00")]
_cvg.objects["users/alice/upload_a.json"] = {"username": "alice", "records": _A}
mgr_full = _mk_mgr(_cvg)
mgr_inc = _mk_mgr(_cvg)
m_inc = mgr_inc.get_merged_master()          # 此刻 OSS 里只有 A
_assert_eq(len(m_inc), 2, "增量侧基线：只有 A 的两个文件时总表 2 条")
_cvg.objects["users/bob/upload_b.json"] = {"username": "bob", "records": _B}
mgr_inc._oss_keys_cache.clear()
mgr_inc._master_cache_time = 0
m_inc = mgr_inc.refresh_from_oss() or mgr_inc.get_merged_master()

m_full = mgr_full.get_merged_master()        # 同一批 Bronze，一次全量重建
_der = ("region", "province", "industry", "quality", "is_domestic")
_key = lambda rs: {str(r.get("company_norm")): tuple(str(r.get(k) or "") for k in _der)
                   for r in rs}
_ki, _kf = _key(m_inc), _key(m_full)
_assert_eq(len(m_inc), len(m_full), "增量合并与全量重建条数相等（收敛）")
_assert_eq(set(_ki), set(_kf), "两条路径覆盖的实体集合相同")
_diverge = {n: (_ki[n], _kf[n]) for n in set(_ki) & set(_kf) if _ki[n] != _kf[n]}
_assert_eq(_diverge, {}, "增量结果 ≡ 全量重放：同一实体的派生列逐位相同")
_jia = next(r for r in m_full if r["company_norm"] == "收敛验证甲有限公司")
_assert_eq(_jia["industry"], "光伏太阳能",
           "同数冲突按来源新鲜度定胜（A 展 11-01 比 B 展 10-20 新），与记录顺序无关")
_assert("13811110001" in _jia["mobiles"] and "13811110003" in _jia["mobiles"],
        "确定性不牺牲并集：两个来源的手机号都在")

# —— 同一批 Bronze 重放两次必须逐条等价（幂等不动点）——
mgr_re = _mk_mgr(_cvg)
_r1 = mgr_re.get_merged_master()
_r2 = mgr_re._full_rebuild()
_assert_eq(_key(_r1), _key(_r2), "同一快照两次全量重建：派生列零漂移")
_assert_eq(len(_r1), len(_r2), "两次全量重建条数相同")
# 上传文件名带 uuid 后缀，同一秒内的两次上传必须靠 key 字典序定序（规范序的前提）
_uuid_keys = ["users/alice/upload_20261110_101010_b2c1.json",
              "users/alice/upload_20261110_101010_a9f3.json"]
_uo = FakeOSS()
for _k in _uuid_keys:
    _uo.objects[_k] = {"username": "alice",
                       "records": [_rec("同秒上传定序有限公司", products="光伏组件",
                                        address="福建省厦门市",
                                        mobiles=["13822220000"])]}
_uo.objects["users/alice/meta.json"] = {"username": "alice"}   # 非 upload 对象要排除
_mgr_uo = _mk_mgr(_uo)
_assert_eq(_mgr_uo._list_upload_keys(), [_uuid_keys[1], _uuid_keys[0]],
           "同秒并发上传按 key 字典序定序，meta 分片不进合并输入")

# —— 分区键的挑法本身必须与记录顺序无关（多数票 → 新鲜度 → 字面序）——
from pool_merge import _majority_value as _mv, merge_group as _mg2

_i1 = _rec("行业投票有限公司", products="光伏组件", address="浙江省宁波市",
           mobiles=["13833330001"], industry="光伏太阳能", source_time="2026-11-05T10:00:00")
_i2 = _rec("行业投票有限公司", products="光伏支架", address="浙江省宁波市",
           mobiles=["13833330002"], industry="光伏太阳能", source_time="2026-11-05T10:00:00")
_i3 = _rec("行业投票有限公司", products="数控机床", address="浙江省宁波市",
           mobiles=["13833330003"], industry="机械与机床", source_time="2026-11-09T10:00:00")
_orders = ([_i1, _i2, _i3], [_i3, _i1, _i2], [_i2, _i3, _i1])
_votes = [_mv([dict(x) for x in g], "industry") for g in _orders]
_assert_eq(set(_votes), {"光伏太阳能"},
           "2 票光伏 vs 1 票机械（更新鲜）：多数票压倒新鲜度，三种顺序同结论")
_t1 = _rec("新鲜度定胜有限公司", products="a", mobiles=["13844440001"],
           industry="泵阀管道", source_time="2026-09-01T10:00:00")
_t2 = _rec("新鲜度定胜有限公司", products="b", mobiles=["13844440002"],
           industry="仪器仪表", source_time="2026-11-01T10:00:00")
_assert_eq({_mv([_t1, _t2], "industry"), _mv([_t2, _t1], "industry")}, {"仪器仪表"},
           "1:1 平票按来源新鲜度定胜，且与记录顺序无关")
_u1 = _rec("哨兵不投票有限公司", products="水处理设备", mobiles=["13855550001"],
           industry=INDUSTRY_UNKNOWN)
_u2 = _rec("哨兵不投票有限公司", products="水处理设备", mobiles=["13855550002"],
           industry="环保与水处理")
_assert_eq(_mv([_u1, _u1, _u2], "industry"), "环保与水处理",
           "「未分类」等同缺失：两条哨兵不给 未分类 凑票（否则哨兵永远赢）")
_assert_eq(_mv([_u1, dict(_u1)], "industry"), "",
           "全是哨兵时留空，让 enrich 按并集证据重新派生（不锁死 Unknown）")
_g1 = _mg2([dict(_i1), dict(_i2), dict(_i3)])
_g2 = _mg2([dict(_i3), dict(_i1), dict(_i2)])
_assert_eq(_g1["industry"], _g2["industry"],
           "merge_group 出口行业与组内顺序无关（真实漂移 11 组的收口）")
_assert_eq(_g1["industry"], "光伏太阳能", "出口取多数票那一档")
_assert_eq(len(_g1["mobiles"]), 3, "投票只作用于分区键，联系方式仍然并集")

# —— 增量倒排索引 ≡ 全量重建索引（D14b：追加路径曾是 O(n²) 逐项查重）——
# 热 token（所有公司都含「光伏」）会让 postings 长到几万项，`if i not in lst`
# 对它是线性开销；一片 68,935 条的存量对象能把一轮增量同步拖到 5 分钟以上。
_ix = FakeOSS()
_hot = [_rec(f"光伏热词{i}有限公司", products="光伏逆变器",
             address="浙江省宁波市", mobiles=[f"1390000{i:04d}"],
             source="热词展.xlsx", source_user="alice") for i in range(120)]
_ix.objects["users/alice/upload_hot.json"] = {"username": "alice", "records": _hot}
mgr_ix = _mk_mgr(_ix)
mgr_ix.get_merged_master()
mgr_ix.sync_store()
# 第二批里 60 家与已有组同名：并集后会往**旧位置**追加新 token，正好踩在
# 「旧 postings 升序 + 新 i 更小」的插入点上（顺序错了就会留下重复或错位）
_cold = [_rec(f"光伏热词{i}有限公司", products="储能电池 光伏逆变器",
              address="浙江省宁波市", mobiles=[f"1391111{i:04d}"],
              source="热词展B.xlsx", source_user="bob") for i in range(60)]
_ix.objects["users/bob/upload_cold.json"] = {"username": "bob", "records": _cold}
_tokens_before = set((mgr_ix._load_inverted_index() or {"tokens": {}})["tokens"])
mgr_ix._oss_keys_cache.clear()
mgr_ix._master_cache_time = 0
merged_ix = mgr_ix.refresh_from_oss()
inc_index = mgr_ix._load_inverted_index()
full_index = DataPoolManager._build_inverted_index(merged_ix)
_assert(inc_index is not None, "增量后索引仍然可加载（与总表同源）")
inc_index = inc_index or {"tokens": {}}   # 加载失败也要给出清晰 FAIL，而不是 KeyError
_assert(set(inc_index["tokens"]) == set(full_index["tokens"]),
        f"增量索引的 token 集合 == 全量重建（{len(inc_index['tokens'])} 个 token）")
_mismatch = [t for t, postings in inc_index["tokens"].items()
             if postings != full_index["tokens"].get(t)]
_assert_eq(_mismatch[:5], [], "每个 token 的 postings 逐个相同（顺序+去重都对）")
_hot_postings = inc_index["tokens"]["光伏"]
_assert_eq(len(_hot_postings), len(set(_hot_postings)),
           "热 token 的 postings 没有重复位置（去重排序收口）")
_assert_eq(_hot_postings, sorted(_hot_postings), "postings 保持升序（下标语义不变）")
_assert_eq(len(_hot_postings), 120, "热 token 覆盖全部 120 家含「光伏」的记录")
_new_tokens = set(inc_index["tokens"]) - _tokens_before
_assert(len(_new_tokens) > 0, f"增量确实扩充了可召回 token（新增 {len(_new_tokens)} 个）")
# 用「整串号码」这种不会再被切分的 token 做召回校验（n-gram 子 token 会被重新
# 分词，拿它断言召回位置等于自找假阳性）
_probe = next((t for t in sorted(_new_tokens) if t.isdigit() and len(t) == 11), "")
_assert(bool(_probe), "第二批的新号码本身成了可检索 token")
if _probe:
    _assert_eq(sorted(mgr_ix.search_by_index(_probe)),
               sorted(inc_index["tokens"][_probe]),
               "新 token 立即参与召回，且召回位置就是 postings")


# ════════════════════════════════════════════════════════════════════════════
print("\n═══ 14. 名称门禁读侧重放：判据升级必须回刷到存量（D15）═══")
# 取证（存量池 87,545 条全量原始名称）：以「频道」结尾 90 条、以「板块」结尾 1 条，
# **零条真实企业**；而「商城」结尾的「昌邑市北孟镇富丽华商城」是真实个体户。
# 豁免通道（含组织尾缀的长名放行）只放过 1 条「酒店频道」——它显然也是栏目名，
# 所以短名不豁免。这一节的断言就是钉住这三个边界，别让后来人一句话改回单边。
from ingest_cleaning import NAME_RULE_VERSION, gate_valid_names, is_valid_company_name

_NAVI_BAD = ["安防频道", "酒店频道", "IT频道", "暖通空调频道", "深圳市节博会智慧板块",
             "www.solarportal24.de", "/uploads/medium_Logo_x.png"]
_NAVI_OK = ["昌邑市北孟镇富丽华商城",              # 「商城」结尾的真个体户：必须放行
            "ABCO expert s.r.o.",                  # 海外名带组织尾缀
            "宁波锦浪新能源技术有限公司",
            "深圳市湾畔酒店管理有限公司频道"]        # 长名 + 组织尾缀：走豁免通道
for _n in _NAVI_BAD:
    _assert(not is_valid_company_name(_n), f"非实体名判无效：{_n}")
for _n in _NAVI_OK:
    _assert(is_valid_company_name(_n), f"真实体名不被尾缀判据误伤：{_n}")

# ── 读侧门禁函数本身：只留实体、可计数、幂等 ──
_gated, _dropped_n = gate_valid_names([
    _rec("宁波锦浪新能源技术有限公司", mobiles=["13800000001"]),
    _rec("安防频道", mobiles=["13800000002"]),
    _rec("www.solarportal24.de", mobiles=["13800000003"]),
    _rec("", mobiles=["13800000004"]),
    "不是一条记录",
])
_assert_eq([r["company"] for r in _gated], ["宁波锦浪新能源技术有限公司"],
           "读侧门禁只保留企业实体名")
_assert_eq(_dropped_n, 4, "剔除条数可播报（含非 dict 脏元素）")
_assert_eq(gate_valid_names(_gated)[1], 0, "门禁幂等：洗过的数据再跑零剔除")
_assert_eq(gate_valid_names([])[0], [], "空输入不炸")

# ── 接线到真实加载链路：Bronze 不可改写，Silver 必须可重放 ──
# 只在写侧加判据 = 修复对存量永远不可见（历史自锁事故的同源形状）：
# 上传层是 append-only 原始层，历史对象不会被回头改写，规则升级后老脏数据
# 还在池里被搜到，界面上就是「新导入被拦、老数据还能搜到」的口径分叉。
oss_gate = FakeOSS()
oss_gate.objects["users/alice/upload_legacy.json"] = {
    "username": "alice",
    "records": [
        _rec("存量脏数据频道", products="安防设备", address="广东省深圳市",
             mobiles=["13811110000"]),
        _rec("www.legacy-shop.com", products="安防设备", address="广东省深圳市",
             mobiles=["13811110001"]),
        _rec("存量干净安防有限公司", products="安防设备", address="广东省深圳市",
             mobiles=["13811110002"]),
    ]}
mgr_gate = _mk_mgr(oss_gate)
_master_gate = mgr_gate.get_merged_master()
_assert_eq([r["company"] for r in _master_gate], ["存量干净安防有限公司"],
           "历史 Bronze 里的栏目名在读取端被重放剔除")

# ── 判据版本落盘 + 过期缓存作废（否则回刷只在新机器上生效）──
_ver_file = os.path.join(mgr_gate._cache_dir, "rule_version.json")
_assert(os.path.exists(_ver_file), "判据版本与总表成对落盘")
import data_pool as _dp
_assert_eq(json.loads(open(_ver_file, encoding="utf-8").read())["silver"],
           _dp._silver_rule_version(),
           f"版本内容含名称门禁版本（NAME_RULE_VERSION={NAME_RULE_VERSION}）")
_assert(NAME_RULE_VERSION in _dp._silver_rule_version(),
        "名称门禁版本升级会改变版本指纹")
# 判据升级后（模拟：缓存里记的是旧版本）→ 本地总表作废，走全量重建自动回刷
json.dump({"silver": "0|0|0"}, open(_ver_file, "w", encoding="utf-8"))
_mgr_stale = DataPoolManager(oss_client=oss_gate, cache_dir=mgr_gate._cache_dir)
_assert(not _mgr_stale._load_local_state(),
        "判据版本不符 → 本地缓存判过期（不拿旧规则的结果糊弄用户）")
_assert_eq([r["company"] for r in _mgr_stale.get_merged_master()],
           ["存量干净安防有限公司"], "版本升级后第一次加载即重建回刷")
_assert_eq(json.loads(open(_ver_file, encoding="utf-8").read())["silver"],
           _dp._silver_rule_version(), "重建后重新盖上当前判据版本")


# 离线兜底：版本过期但网络已断 → 仍用旧缓存（可用性优先，不许把断网报成"没数据"）
class _NoListOSS(FakeOSS):
    def list_keys(self, prefix=""):
        from oss_pool_client import OSSPoolError
        raise OSSPoolError("模拟断网")


json.dump({"silver": "0|0|0"}, open(_ver_file, "w", encoding="utf-8"))
_mgr_offline = DataPoolManager(oss_client=_NoListOSS(), cache_dir=mgr_gate._cache_dir)
_offline_master = _mgr_offline.get_merged_master()
_assert_eq(len(_offline_master), 1,
           "断网 + 判据过期：仍然给出本地缓存那 1 条，而不是报错清空")


# ════════════════════════════════════════════════════════════════════════════
print("\n═══ 15. 清理 ═══")
for st in (store, store2, store3):
    st.close()
for d in _tmp_roots:
    shutil.rmtree(d, ignore_errors=True)
_assert(all(not os.path.exists(d) for d in _tmp_roots), "临时目录全部清理")
_assert(not os.path.exists(os.path.join(POOL_DIR, "master.json")),
       "本回归不写真实数据池缓存目录")

print("\n" + "=" * 60)
print(f"全部通过 ✅ ({_passed}/{_passed + _failed})" if _failed == 0
      else f"完成：{_passed} 通过 / {_failed} 失败 ❌")
print("=" * 60)
sys.exit(1 if _failed else 0)
