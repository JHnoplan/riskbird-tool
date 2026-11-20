# -*- coding: utf-8 -*-
"""「扫描本地表格入数据池」离线回归（不联网、不碰真实缓存目录、不弹用户桌面）。

钉死四件事（每一件都对应本轮实测抓出过的真实缺陷）：

① 发现层剪枝必须**剪得掉噪声、也绝不剪掉数据**：虚拟环境/隐藏目录/包目录/
   Office 锁文件/工具自己的产物目录全部跳过，上限触顶要如实报「可能没扫全」。
② 台账认内容不认文件名：改名、复制、挪目录都是同一份数据；「不合格」绝不能
   被写成「已入库」（那等于永久跳过一个其实没进池的文件 = 丢数据）。
③ **扫描判定与权威入库链路必须同口径**（本轮修掉的真缺陷）：接口文档类表格
   「A 表有公司名、B 表有邮箱」按文件取并集会判成合格，而 read_records_from_excel
   是逐表转记录的，整份一条都留不下 → 界面出现「预估 454 条 → 上传报无可上传数据」。
   这里用同一批夹具把两个方向的等价性逐个断言。
④ 剔除标记（删除向量）：Bronze 只追加不可改写，标记在读取端重放；打标记后
   总表/缓存/新实例都必须不再返回它，网络坏了也必须保持上一次的正确行为。
"""
import errno
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
BASE = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, os.path.join(BASE, "b2bcollect_build"))
# 状态落点钉进临时目录：本套件真建 MainWindow，而 access_guard 会按现行落点写
# `.acl_cache.json`。2026-11-20 全量批跑实测：这一路把开发机那份**真缓存**改脏了
# （批跑收尾的状态对拍点名「开工前 X → 收工后 Y」），后果有两层——① acl_levels 下一轮
# 就在脏缓存上判，红得查不出理由；② 干净克隆上凭空多出这份文件，被同一道门压红总判定。
# 唯一的开关是 B2B_STATE_DIR（实现见 tool_config 的 _STATE_DIR_ENV_VAR 那一级，实测对
# 开发态同样生效）。**钉完必须当场自证落点真在沙箱里**：只设环境变量不证明，等于把
# 「哪天这个杠杆失效」再交给下一轮批跑去发现（那一路套件里就留着一句只删不证的假自证）。
_STATE_SANDBOX = tempfile.mkdtemp(prefix="locscan_state_")
os.environ["B2B_STATE_DIR"] = _STATE_SANDBOX


import local_scan as ls  # noqa: E402
import access_guard as _ag_boot                                   # noqa: E402
assert str(_ag_boot._state_dir()).startswith(str(_STATE_SANDBOX)), (
    f"隔离没生效：权限状态目录指向 {_ag_boot._state_dir()}（沙箱是 {_STATE_SANDBOX}）"
    "——上面那句 B2B_STATE_DIR 白设了，本套件仍会写脏开发机的 .acl_cache.json")

from openpyxl import Workbook  # noqa: E402

ok = 0


def check(name, cond, detail=None):
    global ok
    assert cond, f"FAIL: {name}" + (f"  -> {detail}" if detail is not None else "")
    ok += 1
    print(f"  ✓ {name}")


def make_xlsx(path, sheets):
    """sheets: [(表名, [表头, 行...]), ...]"""
    wb = Workbook()
    first = True
    for name, rows in sheets:
        ws = wb.active if first else wb.create_sheet()
        first = False
        ws.title = name
        for r in rows:
            ws.append(list(r))
    wb.save(path)
    return path


TMP = tempfile.mkdtemp(prefix="ls_test_")
FILES = os.path.join(TMP, "files")
CACHE = os.path.join(TMP, "cache")
os.makedirs(FILES, exist_ok=True)
os.makedirs(CACHE, exist_ok=True)

# ── 夹具：一份好表 + 四种「看着像数据其实不能入池」的表 ────────────────────
GOOD = make_xlsx(os.path.join(FILES, "good.xlsx"), [
    ("Sheet1", [["公司名", "电话", "邮箱"],
                ["苏州协浚光伏科技有限公司", "0573-83377200", "a@b.com"],
                ["中浚通(云南)光伏科技有限公司", "13800001111", "c@d.com"],
                ["海南君若排水服务有限公司", "13006076383", ""]]),
])
# 本轮修掉的那一类：公司名和联系方式分散在两张表里，逐表转记录一条都凑不齐
SPLIT = make_xlsx(os.path.join(FILES, "api_doc_like.xlsx"), [
    ("Overview", [["编号", "名称"], [1, "Error message design"], [2, "login"]]),
    ("names", [["公司名"], ["上海某某设备有限公司"], ["北京某某工程有限公司"],
               ["广州某某贸易有限公司"], ["深圳某某科技有限公司"],
               ["杭州某某信息有限公司"]]),
    ("mails", [["邮箱"], ["a@x.com"], ["b@x.com"], ["c@x.com"],
               ["d@x.com"], ["e@x.com"]]),
])
# 只有微信号：门禁里微信不算独立证据，扫描与入库必须给同一个结论
WECHAT = make_xlsx(os.path.join(FILES, "wechat_only.xlsx"), [
    ("Sheet1", [["公司名", "微信号"], ["天津某某机械公司", "wx_abc"],
                ["重庆某某电子公司", "wx_def"], ["南京某某仪表公司", "wx_ghi"],
                ["武汉某某材料公司", "wx_jkl"], ["成都某某器件公司", "wx_mno"]]),
])
# 说明表：整表是成句的文字，不是企业名
PROSE = make_xlsx(os.path.join(FILES, "progress_note.xlsx"), [
    ("采集进度", [["说明"],
                 ["本表用于记录采集进度，请勿删除。"],
                 ["目标企业数 20000 家，优先沿海地区。"],
                 ["重复企业及重复号码不计入统计。"],
                 ["业务不匹配的展商直接剔除。"],
                 ["联系方式缺失的进入待补名单。"]]),
])
# 只有表头
EMPTY = make_xlsx(os.path.join(FILES, "header_only.xlsx"), [
    ("Sheet1", [["公司名", "电话"]]),
])

# ── ① 发现层：剪枝与上限 ───────────────────────────────────────────────
print("\n[① 发现层剪枝与上限]")
tree = os.path.join(TMP, "tree")
os.makedirs(os.path.join(tree, "myenv", "Lib", "site-packages"), exist_ok=True)
open(os.path.join(tree, "myenv", "pyvenv.cfg"), "w").close()
os.makedirs(os.path.join(tree, "node_modules", "pkg"), exist_ok=True)
os.makedirs(os.path.join(tree, ".hidden"), exist_ok=True)
os.makedirs(os.path.join(tree, "keep"), exist_ok=True)
os.makedirs(os.path.join(tree, "own_output"), exist_ok=True)
for p in [os.path.join(tree, "myenv", "Lib", "site-packages", "t1.xlsx"),
          os.path.join(tree, "node_modules", "pkg", "t2.xlsx"),
          os.path.join(tree, ".hidden", "t3.xlsx"),
          os.path.join(tree, "own_output", "t4.xlsx"),
          os.path.join(tree, "keep", "~$good.xlsx"),
          os.path.join(tree, "keep", "readme.txt")]:
    with open(p, "wb") as f:
        f.write(b"x" * 4096)
# 空壳表格：体积不到 MIN_FILE_BYTES，多半是占位/写坏的文件
with open(os.path.join(tree, "keep", "tiny.xlsx"), "wb") as f:
    f.write(b"x" * 100)
shutil.copy(GOOD, os.path.join(tree, "keep", "real.xlsx"))

_orig_forbidden = ls._forbidden_dirs
ls._forbidden_dirs = lambda: [os.path.realpath(os.path.join(tree, "own_output"))]
try:
    found, archives, stats = ls.iter_tables([tree])
    names = sorted(os.path.basename(f["path"]) for f in found)
    check("① 只发现真实表格（虚拟环境/依赖包/隐藏目录/自身产物/锁文件全部剪掉）",
          names == ["real.xlsx"], str(names))
    check("① 非表格后缀不参与判定（csv 之外不算候选，txt 被跳过）",
          "readme.txt" not in names and stats["found"] == len(found))
    check("① 体积过小的占位文件被排除（MIN_FILE_BYTES）",
          "tiny.xlsx" not in names)
    check("① 统计逐项如实记账（dirs_walked/pruned/skipped_by_name 都非负且自洽）",
          stats["dirs_walked"] > 0 and stats["dirs_pruned"] >= 3
          and stats["skipped_by_name"] >= 1 and stats["truncated"] == "")
    # 这棵树里没有包：archives 必须是空列表而不是 None/缺键。发现层把散表和包分开
    # 交给下游（散表直接判、包先展开），混成一个列表会让「包里的表」和「包」被
    # 各算一次，判重与计数全乱。
    check("① 散表与压缩包分开记账（无包时 archives 为空且计数自洽）",
          archives == [] and stats["archives_found"] == 0, str(archives))

    saved = ls.MAX_CANDIDATE_FILES
    ls.MAX_CANDIDATE_FILES = 1
    try:
        found2, _a2, stats2 = ls.iter_tables([tree])
        check("① 文件数触顶时如实报「可能还有没扫到的」（不假装扫全了）",
              len(found2) == 1 and "触顶" in stats2["truncated"],
              stats2["truncated"])
    finally:
        ls.MAX_CANDIDATE_FILES = saved

    hit = {"n": 0}

    def _stop():
        hit["n"] += 1
        return hit["n"] > 1
    found3, _a3, stats3 = ls.iter_tables([tree], should_stop=_stop)
    check("① 取消在文件边界生效，且标注「已取消」",
          stats3["truncated"] == "已取消", stats3["truncated"])

    deep = os.path.join(TMP, "deep")
    p = deep
    for i in range(12):
        p = os.path.join(p, f"d{i}")
    os.makedirs(p, exist_ok=True)
    shutil.copy(GOOD, os.path.join(p, "deep.xlsx"))
    found4, _a4, _st4 = ls.iter_tables([deep])
    check("① 超过 MAX_DEPTH 的深层目录不追（防止拿 NAS/备份树把扫描拖死）",
          found4 == [], str([f["path"] for f in found4]))
finally:
    ls._forbidden_dirs = _orig_forbidden

# ── ② 指纹与台账 ────────────────────────────────────────────────────────
print("\n[② 内容指纹与台账]")
copied = os.path.join(FILES, "good_副本.xlsx")
shutil.copy(GOOD, copied)
fp_a = ls.fingerprint(GOOD)
fp_b = ls.fingerprint(copied)
check("② 另存副本 = 同一份内容（指纹一致，改名/复制不会导致重传）",
      fp_a == fp_b and fp_a.startswith("sha256:"))
with open(copied, "ab") as f:
    f.write(b"\x00" * 8)
os.utime(copied, (os.path.getatime(copied) + 5, os.path.getmtime(copied) + 5))
check("② 内容一改指纹就变（不会把改过的文件当传过的跳过）",
      ls.fingerprint(copied) != fp_a)

led = ls.Ledger(cache_dir=CACHE).load()
item_a = {"path": GOOD, "display": "good.xlsx", "fp": fp_a,
          "size": os.path.getsize(GOOD), "mtime": int(os.path.getmtime(GOOD)),
          "rows_kept": 3, "rows_in": 3}
item_bad = {"path": PROSE, "display": "progress_note.xlsx",
            "fp": ls.fingerprint(PROSE), "size": os.path.getsize(PROSE),
            "mtime": int(os.path.getmtime(PROSE))}
check("② 新台账初始为空（ingested 不接受任何未知指纹）",
      led.ingested(fp_a) is False)
led.mark_skipped([item_bad], ls.ST_INVALID, "说明表")
led.mark_ingested([item_a], batch="upload_20261111_abcd1234.json")
check("② 上传成功后才记 ingested", led.ingested(fp_a) is True)
check("② 不合格的结论绝不伪装成「已入库」",
      led.ingested(item_bad["fp"]) is False
      and led.entry(item_bad["fp"])["status"] == ls.ST_INVALID)
led2 = ls.Ledger(cache_dir=CACHE).load()
check("② 台账落盘可回读（重启后结论还在，不重复解析）",
      led2.ingested(fp_a) and led2.entry(item_bad["fp"])["status"] == ls.ST_INVALID)
check("② 记账带上批次文件名（出事能追到具体那次上传）",
      led2.entry(fp_a).get("batch") == "upload_20261111_abcd1234.json"
      and led2.entry(fp_a).get("rows_kept") == 3)
fp_cached, from_cache = led2.fp_for(GOOD, os.path.getsize(GOOD),
                                    int(os.path.getmtime(GOOD)))
check("② size+mtime 未变才复用哈希（改过必须重算）",
      fp_cached == fp_a and from_cache is True)
os.utime(GOOD, (os.path.getatime(GOOD), int(os.path.getmtime(GOOD)) + 7))
_, from_cache2 = led2.fp_for(GOOD, os.path.getsize(GOOD),
                             int(os.path.getmtime(GOOD)))
check("② mtime 变了就不给缓存（哈希复用不能靠猜）", from_cache2 is False)

broken = ls.Ledger(cache_dir=CACHE)
broken.file.write_text("{这不是 JSON", encoding="utf-8")
broken.load()
check("② 台账文件坏了当空台账重来（不阻断扫描），并留 .bad.json 档",
      broken.entries == {} and os.path.exists(
          os.path.join(CACHE, "local_scan_ledger.bad.json")))

# ── ③ 扫描判定 ⟺ 权威入库链路 ──────────────────────────────────────────
print("\n[③ 扫描判定与权威链路同口径]")
from data_pool import DataPoolManager, _is_valid_record  # noqa: E402

CASES = [
    ("good", GOOD, True),
    ("api_doc_like(公司名与联系方式分表)", SPLIT, False),
    ("wechat_only(只有微信号)", WECHAT, False),
    ("progress_note(说明表)", PROSE, False),
    ("header_only(只有表头)", EMPTY, False),
]
for label, path, expect_ok in CASES:
    pvs, src = ls.quick_preview(path)
    got_ok, reason, rows, fields = ls.judge_quality(pvs)
    check(f"③ {label}：扫描判定 = 期望（{expect_ok}）",
          got_ok == expect_ok, f"got={got_ok} reason={reason} src={src}")
    try:
        rep = {}
        kept = DataPoolManager.read_records_from_excel(path, report_out=rep)
    except Exception as e:
        # 只有表头的空表在权威链路里是「抛异常」而不是「返回空列表」：
        # 扫描层必须把它认成「没有数据」，不能认成「读不了」（两件事）
        kept = []
        check(f"③ {label}：权威链路以异常表达「没有数据」时，扫描层归到 NO_SHEET",
              ls.NO_SHEET in reason or "没有读到" in str(e),
              f"reason={reason} exc={e}")
    check(f"③ {label}：扫描判定与权威链路 kept>0 完全一致（不漏传也不多传）",
          got_ok == (len(kept) > 0),
          f"scan_ok={got_ok} kept={len(kept)} report={rep.get('drop_reasons') if isinstance(rep, dict) else None}")
    if not got_ok:
        check(f"③ {label}：不合格结论必须带可复述的原因", bool(reason), reason)

pvs, _ = ls.quick_preview(GOOD)
_ok, _r, rows, fields = ls.judge_quality(pvs)
check("③ 好表识别到公司名/电话/邮箱三个字段并给出采样行数",
      "公司名" in fields and "电话" in fields and rows >= 3, fields)

pv_split, _ = ls.quick_preview(SPLIT)
_ok2, reason_split, _r2, fields_split = ls.judge_quality(pv_split)
check("③ 分表文件的字段并集里「公司名/邮箱都有」，但按表配套判成不合格"
      "（本轮修复的核心：不再按文件取并集）",
      "公司名" in fields_split and "邮箱" in fields_split
      and not _ok2 and reason_split.startswith(ls.NO_CONTACT),
      f"fields={fields_split} reason={reason_split}")
check("③ 这类「取决于解析深度」的不合格必须交给权威整表复核翻案",
      ls.needs_full_confirm(reason_split) is True)
conf_ok, conf_reason, conf_kept = ls.confirm_by_full_read(SPLIT)
check("③ 权威复核也判它不合格（kept=0），扫描结论与真上传结果一致",
      conf_ok is False and conf_kept == 0, conf_reason)
pv_empty, _ = ls.quick_preview(EMPTY)
_ok3, reason_empty, _r3, _f3 = ls.judge_quality(pv_empty)
check("③ 「只有表头/没有数据行」不做整表复核（采样与整表看到的是同一件事）",
      not _ok3 and ls.needs_full_confirm(reason_empty) is False, reason_empty)

# 门禁字段集必须与 data_pool._is_valid_record 严格一致（微信/地址不算证据）
_pv, _s = ls.quick_preview(WECHAT)
_ok4, reason_wechat, _r4, f4 = ls.judge_quality(_pv)
rec_wechat = {"company": "天津某某机械公司", "wechats": ["wx_abc"], "address": "某地"}
check("③ 微信号在扫描层与入库门禁里同判「没有联系方式」",
      (not _ok4 and reason_wechat.startswith(ls.NO_CONTACT))
      and _is_valid_record(rec_wechat) is False,
      f"reason={reason_wechat}")
check("③ GATE_CONTACT_FIELDS 与 _is_valid_record 的证据字段逐项对齐",
      ls.GATE_CONTACT_FIELDS == ("电话", "邮箱", "官网")
      and _is_valid_record({"company": "x", "website": "http://x.com"}) is True
      and _is_valid_record({"company": "x", "emails": ["a@b.c"]}) is True)

# ── ④ 同族快照归组 ──────────────────────────────────────────────────────
print("\n[④ 同族快照归组]")
k1 = ls.family_key("/tmp/x/深海装备潜客_海洋装备46_客户1_20261015_120000.xlsx")
k2 = ls.family_key("/tmp/x/深海装备潜客_海洋装备46_客户1_20261101_0930.xlsx")
k3 = ls.family_key("/tmp/x/深海装备潜客_海洋装备46_客户1.xlsx")
check("④ 同一系列的时间戳快照归成一个同族", k1 == k2 == k3, f"{k1}\n{k2}\n{k3}")
k4 = ls.family_key("/tmp/x/新能源网企业采集_累积_20261102.xlsx")
k5 = ls.family_key("/tmp/y/新能源网企业采集_累积_20261103.xlsx")
check("④ 跨目录不并族（不同任务同名很可能不是同一批数据）", k4 != k5)
k6 = ls.family_key("/tmp/x/企业名单(副本).xlsx")
k7 = ls.family_key("/tmp/x/企业名单.xlsx")
k8 = ls.family_key("/tmp/x/企业名单 - Copy.xlsx")
check("④ 「副本/copy」后缀剥进同族键（另存一份不会被当成两份数据）",
      k6 == k7 == k8, f"{k6}｜{k7}｜{k8}")
k9 = ls.family_key("/tmp/x/企业数据_原始.xlsx")
k10 = ls.family_key("/tmp/x/企业数据_清洗后入库数据.xlsx")
check("④ 同一会话目录里「原始/清洗后」三个阶段归一族（都传等于传三遍）",
      k9 == k10)
# 真实命名探针：本机就有「…_20261111_1032.xlsx / …_20261111_1037.xlsx」这种
# 4 位（HHMM）时间戳的系列快照，只认 6 位会让同一批数据被勾两遍
h1 = ls.family_key("/tmp/x/CDSCA展商名单_补完_20261111_1032.xlsx")
h2 = ls.family_key("/tmp/x/CDSCA展商名单_补完_20261111_1037.xlsx")
h3 = ls.family_key("/tmp/x/CDSCA展商名单_补完.xlsx")
check("④ 4 位时分（HHMM）命名的系列快照也归同一族（本轮实测修掉的重复上传）",
      h1 == h2 == h3, f"{h1}\n{h2}\n{h3}")
d1 = ls.family_key("/tmp/x/深海装备潜客_海洋装备46_客户1.xlsx")
d2 = ls.family_key("/tmp/x/深海装备潜客_海洋装备46_客户2.xlsx")
check("④ 名字里的业务编号不能被当成时间戳吃掉（46 必须保留，否则不同系列会被误并）",
      d1 != d2 and "46" in d1, f"{d1}｜{d2}")
sep1 = ls.family_key("/tmp/x/名单_2026-11-11 1032.xlsx")
sep2 = ls.family_key("/tmp/x/名单.xlsx")
check("④ 带分隔符/空格的时间戳同样归族", sep1 == sep2, f"{sep1}｜{sep2}")

def _it(path, mtime, rows=10):
    return {"path": path, "family": ls.family_key(path), "mtime": mtime,
            "size": rows * 1000, "status": ls.ST_NEW, "est_rows": rows,
            "checked": True}

items = [_it("/d/快照_20261001.xlsx", 100), _it("/d/快照_20261101.xlsx", 300),
         _it("/d/快照_20261015.xlsx", 200), _it("/e/另一批.xlsx", 50)]
reps = ls.pick_family_representatives(items)
check("④ 同族只留 mtime 最新的那一份当代表",
      reps[ls.family_key("/d/快照_20261001.xlsx")] == "/d/快照_20261101.xlsx"
      and reps[ls.family_key("/e/另一批.xlsx")] == "/e/另一批.xlsx", str(reps))

# ── ⑤ scan_all 端到端（临时台账 + 临时根目录，零网络）───────────────────
print("\n[⑤ scan_all 端到端编排]")
scan_files = os.path.join(TMP, "scan")
os.makedirs(scan_files, exist_ok=True)
for src_p, name in [(GOOD, "客户名单.xlsx"),
                    (GOOD, "客户名单_20261001.xlsx"),   # 同内容同族 → 只该留一份
                    (SPLIT, "接口文档.xlsx"),
                    (EMPTY, "空表.xlsx")]:
    shutil.copy(src_p, os.path.join(scan_files, name))
os.utime(os.path.join(scan_files, "客户名单.xlsx"), (0, 400))
os.utime(os.path.join(scan_files, "客户名单_20261001.xlsx"), (0, 100))
# 另一个目录里放一份字节完全相同的副本（跨目录、文件名也不同族）：
# 内容寻址去重必须覆盖**单次扫描内部**，否则同一批数据会被当成两份一起上传
other_dir = os.path.join(TMP, "scan2")
os.makedirs(other_dir, exist_ok=True)
shutil.copy(GOOD, os.path.join(other_dir, "随手备份的一份.xlsx"))
led3 = ls.Ledger(cache_dir=os.path.join(TMP, "cache3")).load()
items5, stats5 = ls.scan_all(roots=[scan_files, other_dir], ledger=led3,
                             pool_index=None)
by = stats5["by_status"]
check("⑤ 待入库只勾「同内容/同族里的一份代表」，其余全部不勾",
      by.get(ls.ST_NEW, 0) == 1, json.dumps(by, ensure_ascii=False))
newest = [i for i in items5 if i["status"] == ls.ST_NEW]
check("⑤ 三份同内容（两份同目录 + 一份跨目录副本）里恰好勾中一份",
      len(newest) == 1 and os.path.getmtime(newest[0]["path"]) in (100, 400),
      str(newest))
check("⑤ 字节完全相同的副本被单列成「同内容副本」，且指明代表在哪",
      by.get(ls.ST_DUP, 0) == 2
      and all(i.get("dup_of") for i in items5 if i["status"] == ls.ST_DUP),
      json.dumps(by, ensure_ascii=False))
check("⑤ 副本的 reason 必须能复述「为什么不是我这一份被勾」",
      all("字节完全相同" in i["reason"] for i in items5
          if i["status"] == ls.ST_DUP),
      [i["reason"] for i in items5 if i["status"] == ls.ST_DUP])
check("⑤ 同内容副本绝不会被勾上（勾了就是把同一批数据传两遍）",
      not any(i["checked"] for i in items5 if i["status"] == ls.ST_DUP))
check("⑤ 分表文档/空表被拦在待入库之外（不合格），并写进台账",
      by.get(ls.ST_INVALID, 0) >= 2
      and led3.ingested(ls.fingerprint(os.path.join(scan_files, "接口文档.xlsx"))) is False)
check("⑤ 勾选项只来自 new/unconfirmed，不合格默认不勾",
      all(i["checked"] == (i["status"] in (ls.ST_NEW, ls.ST_UNCONFIRMED))
          for i in items5), [i["status"] for i in items5])
summary = ls.summary_line(stats5)
check("⑤ 一行汇总口径齐全（发现/待入库/传过/同系列旧版/同内容副本/不合格/读不了/用时）",
      all(k in summary for k in ("发现", "待入库", "传过", "同系列旧版",
                                 "同内容副本", "不合格", "读不了", "用时")), summary)
# 再扫一次：不合格/空表命中台账，不再重复解析（热扫描成本必须能掉下去）
items6, stats6 = ls.scan_all(roots=[scan_files, other_dir], ledger=led3,
                             pool_index=None)
check("⑤ 第二遍扫描沿用台账结论（同内容代表仍然只有一份待入库）",
      stats6["by_status"].get(ls.ST_NEW, 0) == 1,
      json.dumps(stats6["by_status"], ensure_ascii=False))
# 已入库的整族内容下次全部跳过
all_paths = ([os.path.join(scan_files, n) for n in os.listdir(scan_files)]
             + [os.path.join(other_dir, n) for n in os.listdir(other_dir)])
led3.mark_ingested([{"path": p, "fp": ls.fingerprint(p),
                     "size": os.path.getsize(p),
                     "mtime": int(os.path.getmtime(p)),
                     "rows_kept": 3, "rows_in": 3} for p in all_paths],
                   batch="upload_x.json")
items7, stats7 = ls.scan_all(roots=[scan_files, other_dir], ledger=led3,
                             pool_index=None)
check("⑤ 内容传过之后不再出现在待入库里（改名同内容也算传过）",
      stats7["by_status"].get(ls.ST_NEW, 0) == 0
      and all(not i["checked"] for i in items7),
      json.dumps(stats7["by_status"], ensure_ascii=False))

# ── ⑥ 剔除标记（删除向量）：不联网，用假 OSS 客户端跑通全链路 ─────────────
print("\n[⑥ 剔除标记与读取端重放]")


class FakeOSS:
    """最小可用的假对象存储：够跑 append-only + 原子上传 + 列表 + 下载。

    ``extra_keys`` 用来造「列表说有、下载说没有」这种幽灵对象（对象被生命周期
    规则清掉了但目录树还有残影），是删除向量链路必须扛住的真实场景。
    """

    def __init__(self):
        self.objs = {}
        self.extra_keys = []
        self.fail_list = False
        self.fail_get = set()

    def upload_json_atomic(self, key, data, **kw):
        self.objs[key] = data
        return key

    def upload_json(self, key, data, **kw):
        self.objs[key] = data
        return key

    def list_keys(self, sub_prefix=""):
        if self.fail_list:
            from oss_pool_client import OSSPoolError
            raise OSSPoolError("网络错误: forced")
        keys = [k for k in self.objs if k.startswith(sub_prefix)]
        keys += [k for k in self.extra_keys if k.startswith(sub_prefix)]
        return keys

    def download_json(self, key, timeout=None, **kw):
        from oss_pool_client import OSSPoolError
        if key in self.fail_get:
            raise OSSPoolError("网络错误: forced")
        if key not in self.objs:
            raise OSSPoolError("对象不存在: " + key)
        return self.objs[key], "etag"

    def head_etag(self, key):
        return "etag"


from data_pool import DataPoolManager, normalize_company_name  # noqa: E402

oss = FakeOSS()
cache6 = os.path.join(TMP, "cache6")
TEST_A = "测试联调公司A有限公司"
REAL = "苏州协浚光伏科技有限公司"
oss.objs["users/u1/upload_20261101_111111_aaaa1111.json"] = {
    "username": "u1", "record_count": 2,
    "records": [
        {"company": TEST_A, "company_norm": normalize_company_name(TEST_A),
         "phones": ["13900000000"], "source": "联调测试"},
        {"company": REAL, "company_norm": normalize_company_name(REAL),
         "phones": ["0573-83377200"], "source": "脉脉"},
    ]}
mgr = DataPoolManager(oss_client=oss, cache_dir=cache6)
master0 = mgr.get_merged_master(force_refresh=True)
check("⑥ 全量重建能看到原始数据（两条都在池里）",
      len(master0) == 2, [r["company"] for r in master0])
check("⑥ 未打标记时一条都不会被剔掉（默认零行为改变）",
      len(mgr._apply_tombstones(master0)) == 2)

res = mgr.tombstone_companies([TEST_A], reason="联调测试数据")
check("⑥ 剔除标记写成独立对象（users/ 原始层一个字节都没被改）",
      res["key"].startswith("tombstones/")
      and "users/u1/upload_20261101_111111_aaaa1111.json" in oss.objs
      and len(oss.objs["users/u1/upload_20261101_111111_aaaa1111.json"]["records"]) == 2,
      str(res))
check("⑥ 打完标记本机总表立即少掉那一条（不用等下一轮同步）",
      res["removed"] == 1 and len(mgr._master_cache) == 1
      and mgr._master_cache[0]["company"] == REAL, str(mgr._master_cache))

# 另一个「同事」的实例：只靠 OSS 上的标记对象恢复状态
oss2 = oss
mgr_b = DataPoolManager(oss_client=oss2, cache_dir=os.path.join(TMP, "cache_b"))
check("⑥ 新实例从对象存储重放标记（别的同事也搜不到它）",
      len(mgr_b.get_merged_master(force_refresh=True)) == 1
      and mgr_b._master_cache[0]["company"] == REAL)
check("⑥ 标记状态落盘可回读（第二次启动零网络也认得上次的标记）",
      os.path.exists(os.path.join(cache6, "tombstones.json"))
      and len(mgr.tombstone_list()) == 1
      and mgr.tombstone_list()[0]["reason"] == "联调测试数据")

# 负向探针：列表失败时，新实例仍要按**磁盘上已存的标记**重放（可用性优先，
# 绝不把「网络不通」表现成「脏数据又回来了」）
mgr_off = DataPoolManager(oss_client=oss, cache_dir=cache6)
oss.fail_list = True
added = mgr_off.refresh_tombstones()
check("⑥ 列表失败时 refresh 只报「没认到新标记」而不抛异常", added == 0)
master_offline = mgr_off.get_merged_master(force_refresh=True)
check("⑥ 列表失败仍按本地已存标记重放，脏数据不会因网络问题回潮",
      all(r["company"] != TEST_A for r in master_offline)
      and len(master_offline) == 1,
      [r["company"] for r in master_offline])
oss.fail_list = False
check("⑥ 网络恢复后不重复消费已知标记对象（游标已在本地状态里）",
      mgr_off.refresh_tombstones() == 0)
# 瞬时失败（单个标记对象读不动）不推进游标：下一轮必须重试，不能永久漏掉标记
mgr_new = DataPoolManager(oss_client=oss, cache_dir=os.path.join(TMP, "cache_new"))
tkey = res["key"]
oss.fail_get.add(tkey)
check("⑥ 标记对象瞬时读失败 → 本轮不记账、不误判成「没有标记」",
      mgr_new.refresh_tombstones() == 0 and tkey not in mgr_new._tomb_keys)
oss.fail_get.clear()
check("⑥ 下一轮自动补上（瞬时失败留在原地重试，不会永久漏标记）",
      mgr_new.refresh_tombstones() == 1 and tkey in mgr_new._tomb_keys)
# 永久坏文件（列表说有、下载说没有）要推进游标，否则每轮同步都被同一个坏标记拖住
oss.objs.pop(tkey)
oss.extra_keys.append(tkey)
mgr_new2 = DataPoolManager(oss_client=oss, cache_dir=os.path.join(TMP, "cache_new2"))
check("⑥ 永久坏文件（对象不存在）认掉并跳过，不反复重试拖慢同步",
      mgr_new2.refresh_tombstones() == 0 and tkey in mgr_new2._tomb_keys)
# 幂等：同一份标记重复消费不会把记录数越剔越少
before = len(mgr._apply_tombstones(master_offline))
check("⑥ 重放是幂等的（同一批标记过两次结果完全一致）",
      len(mgr._apply_tombstones(mgr._apply_tombstones(master_offline))) == before)
# 空关键字/无名字这两个误操作入口必须挡住
try:
    mgr.tombstone_companies(["", "   "])
    check("⑥ 没给企业名时拒绝执行（不能因为界面传了空列表就剔掉「所有」）", False)
except Exception as e:
    check("⑥ 没给企业名时拒绝执行（不能因为界面传了空列表就剔掉「所有」）",
          "没有可用的企业名" in str(e), str(e))

# 剔掉之后总表里已经没有这条记录，但「查找」必须还能看见它（带谁剔的、为什么）：
# 看不见会让用户以为标记没生效，再打一遍 = 往只追加的标记层里造第二条记录
hits_a = mgr.find_for_tombstone("联调")
check("⑥ 已剔除的企业在查找结果里仍可见，并标成已剔",
      len(hits_a) == 1 and hits_a[0]["tombstoned"] is True
      and hits_a[0]["company"] == TEST_A, str(hits_a))
check("⑥ 已剔行带出打标人与原因（界面据此提示，两周后还有人能查是谁剔的）",
      hits_a and hits_a[0]["reason"] == "联调测试数据"
      and hits_a[0]["source_user"] == mgr.username, str(hits_a[:1]))
hits_r = mgr.find_for_tombstone("协浚")
check("⑥ 正常企业查找结果标为未剔（不能被别家的标记连带灰掉）",
      len(hits_r) == 1 and hits_r[0]["tombstoned"] is False, str(hits_r))
check("⑥ 关键字命中数上限生效（ limit=1 时绝不返回两条）",
      len(mgr.find_for_tombstone("公司", limit=1)) <= 1)
# 同名企业既在标记里、又还在册（重放前的旧缓存）时只能出现一次，
# 出现两次会让用户在界面上把同一家勾两遍
mgr._master_cache.append({"company": REAL,
                          "company_norm": normalize_company_name(REAL)})
mgr.tombstone_companies([REAL], reason="重复上传的测试样本")
hits_dup = mgr.find_for_tombstone("协浚")
check("⑥ 同一家企业即使两处都能命中，查找结果也只出一条（不许重复勾两遍）",
      len(hits_dup) == 1 and hits_dup[0]["tombstoned"] is True, str(hits_dup))

# ── ⑦ GUI 台账回写的三条硬规矩（含「上传失败绝不记账」负向探针）───────────
print("\n[⑦ GUI 上传成功才记账]")
from PyQt5.QtWidgets import QApplication  # noqa: E402
import b2bcollect_gui as gui  # noqa: E402

app = QApplication.instance() or QApplication([])
win = gui.MainWindow()
win._on_log = lambda msg: None            # 测试期间不吃真实日志控件

cache7 = os.path.join(TMP, "cache7")
os.makedirs(cache7, exist_ok=True)
gui_files = os.path.join(TMP, "gui_files")
os.makedirs(gui_files, exist_ok=True)
p_good = os.path.join(gui_files, "客户名单A.xlsx")
p_doc = os.path.join(gui_files, "接口文档B.xlsx")
shutil.copy(GOOD, p_good)
shutil.copy(SPLIT, p_doc)
PAIRS = [(p_good, "客户名单A.xlsx"), (p_doc, "接口文档B.xlsx")]


class FakePreview:
    def __init__(self, pairs, parent=None):
        self._pairs = pairs

    def exec_(self):
        return 1                            # Accepted

    def get_mapping_override(self):
        return {}

    def get_skipped_files(self):
        return set()


class FakeSync:
    uploaded_ok = True
    uploaded_batch = "upload_20261111_111111_deadbeef.json"
    uploaded_kept = 3
    seen_records = None

    def __init__(self, records, parent=None, title_prefix="", cleaning_report=None):
        FakeSync.seen_records = records

    def exec_(self):
        return 1


_prev_orig, _sync_orig = gui.ImportPreviewDialog, gui.SyncDialog
gui.ImportPreviewDialog = FakePreview
gui.SyncDialog = FakeSync
try:
    led7 = ls.Ledger(cache_dir=cache7).load()
    win._ledger = lambda: led7
    # 规矩一：上传没成功 → 一个字节都不记
    FakeSync.uploaded_ok = False
    win._import_pairs_to_pool(PAIRS)
    check("⑦ 上传未成功时台账不记「已入库」（那等于永久跳过没进池的文件）",
          all(led7.ingested(ls.fingerprint(p)) is False for p, _ in PAIRS))
    # 规矩二：成功 → 只给真读出记录的 kept>0 文件记账
    FakeSync.uploaded_ok = True
    win._import_pairs_to_pool(PAIRS)
    check("⑦ 上传成功后，真有数据的文件记为已入库并带上批次文件名",
          led7.ingested(ls.fingerprint(p_good)) is True
          and led7.entry(ls.fingerprint(p_good))["batch"] == FakeSync.uploaded_batch,
          json.dumps({k: v.get("status") for k, v in led7.entries.items()},
                     ensure_ascii=False))
    check("⑦ 扫出来「像有数据」但权威链路 kept=0 的文件按不合格记账（不是已入库）",
          led7.ingested(ls.fingerprint(p_doc)) is False
          and led7.entry(ls.fingerprint(p_doc))["status"] == ls.ST_INVALID,
          str(led7.entry(ls.fingerprint(p_doc))))
    check("⑦ 真上传时送进同步对话框的记录只包含有效数据（分表文档没混进来）",
          FakeSync.seen_records is not None
          and all(r["company"] != TEST_A for r in FakeSync.seen_records)
          and len(FakeSync.seen_records) == 3,
          str(len(FakeSync.seen_records or [])))
    # 规矩三：记账后重扫，这批内容不再出现在待入库里
    items8, stats8 = ls.scan_all(roots=[gui_files], ledger=led7, pool_index=None)
    check("⑦ 记账后再扫：传过的内容不再被勾（端到端闭环）",
          all(not i["checked"] for i in items8)
          and stats8["by_status"].get(ls.ST_NEW, 0) == 0,
          json.dumps(stats8["by_status"], ensure_ascii=False))
finally:
    gui.ImportPreviewDialog = _prev_orig
    gui.SyncDialog = _sync_orig
    win.close()

# ── ⑧ GUI 入口与界面契约（源码级红线）──────────────────────────────────
print("\n[⑧ GUI 入口与界面契约]")
# 读源码只走 Path.read_text 一条路：原先写作 `io.open(...) if False else open(...)`，
# 死分支里那个 io 全文件从没 import 过——今天不炸是因为条件恒假，谁把它翻回来，
# 本节就当场 NameError 崩在引导段之后，而批跑只会留下一条没有理由的 FAIL。
src = Path(BASE, "b2bcollect_build", "b2bcollect_gui.py").read_text(encoding="utf-8")
check("⑧ 主界面新增「扫描本地表格」入口并连到扫描对话框",
      'QPushButton("扫描本地表格")' in src and "LocalScanDialog" in src
      and "btn_pool_scan.clicked.connect(self._on_pool_scan)" in src)
check("⑧ 手动导入与全盘扫描共用同一条导入链路（不允许两套判据）",
      src.count("_import_pairs_to_pool(") >= 3
      and "def _import_pairs_to_pool" in src)
check("⑧ 记账前置条件是 dlg.uploaded_ok（唯一成功证据点）",
      "if not dlg.uploaded_ok" in src)
check("⑧ 扫描对话框带「取消扫描」并在收尾时等线程结束（QThread 销毁规则）",
      "取消扫描" in open(os.path.join(BASE, "b2bcollect_build", "pool_dialogs.py"),
                        encoding="utf-8").read()
      and "w.cancel()" in src + open(
          os.path.join(BASE, "b2bcollect_build", "pool_dialogs.py"),
          encoding="utf-8").read())
check("⑧ 后缀白名单不写死第二份（与 data_pool.IMPORT_EXCEL_EXTS 同一真源）",
      "IMPORT_EXCEL_EXTS" in open(os.path.join(BASE, "b2bcollect_build",
                                               "local_scan.py"),
                                  encoding="utf-8").read())
check("⑧ 采样判定用的列匹配函数来自清洗模块（判据同源）",
      "from customer_library_cleaner import (detect_header_row, match_columns"
      in open(os.path.join(BASE, "b2bcollect_build", "local_scan.py"),
              encoding="utf-8").read())

# 剔除标记不可撤回 → 原因必须留痕，且不能被预填的默认文案顶替（写了就是假证据）
_pd_src = open(os.path.join(BASE, "b2bcollect_build", "pool_dialogs.py"),
               encoding="utf-8").read()
check("⑧ 剔除原因为空时拒绝执行（不可撤回的操作必须留可查的依据）",
      'reason = self.reason_input.text().strip()' in _pd_src
      and "if not reason:" in _pd_src
      and 'or "未填写原因"' not in _pd_src)
check("⑧ 剔除原因输入框不预填内容（预填会被原样写进团队可见的标记里）",
      'QLineEdit("联调测试数据")' not in _pd_src)

# ── ⑨ 点「扫描」不能被首次全量下载拖住（自审补的隐患）────────────────────
# PoolIndex 读的是本地物化总表；get_merged_master 在**无缓存**时会走 _full_rebuild
# （全量下载 OSS，几分钟且毫无反馈）。这条路径必须只在缓存存在时才走。
print("\n[⑨ 池内比对只在本地总表缓存存在时做]")
import pool_dialogs as pd  # noqa: E402
from data_pool import DataPoolManager  # noqa: E402

_orig_pool_index = ls.PoolIndex
_orig_cache_dir = DataPoolManager.__dict__["_default_cache_dir"]
_tried = []


class _FakeIdx:
    """替身：真 PoolIndex 会加载 8 万条总表（数秒），断言只关心「有没有被调」。"""
    ready = False

    def sample_diff(self, previews):
        return 0, 0


ls.PoolIndex = lambda *a, **k: (_tried.append(1), _FakeIdx())[1]
DataPoolManager._default_cache_dir = staticmethod(lambda: CACHE)


def _run_worker():
    got = {}
    w = pd.ScanWorker(roots=[FILES])
    w.finished.connect(lambda r, items, stats, note: got.update(
        ok=r, items=items, stats=stats, note=note))
    w.run()          # 同线程直接跑 run()，不起事件循环、不等线程
    return got


_res = _run_worker()
# 异常不能只变成一个空汇总：`ScanWorker.run()` 的兜底会把原因塞进 note（ok=False、stats={}），
# 而旧写法只看 ok——于是屏幕上只剩一句 `([], {})`，拿到原因的人只能重跑一次去猜（云端
# 那一红就是这么浪费掉一整轮的）。这里先把「有没有拿到结论」单独判一格，再把 note
# 带进 ⑨ 的详情：失败不可怕，失败不说为什么才可怕。
check("⑨ 扫描入口必须给出结论（失败也要带着原因回来，不许只交一个空汇总）",
      bool(_res) and "ok" in _res, _res)
check("⑨ 本机没有总表缓存时不构造池内比对（点扫描不会先等几分钟网络）",
      _res.get("ok") and not _tried, (_tried, _res.get("stats"), _res.get("note")))
check("⑨ 没比对必须在汇总里如实说明（不能把「没比对」装成「池里都有」）",
      bool(_res.get("stats", {}).get("pool_note"))
      and "池内比对" in ls.summary_line(_res["stats"]),
      _res.get("stats"))

os.makedirs(os.path.join(CACHE), exist_ok=True)
with open(os.path.join(CACHE, "master.json"), "w", encoding="utf-8") as f:
    json.dump([{"company": "苏州协浚光伏科技有限公司",
                "company_norm": "苏州协浚光伏科技"}], f, ensure_ascii=False)
_tried.clear()
_res2 = _run_worker()
check("⑨ 有本地总表缓存时才做池内比对", bool(_tried), _tried)
check("⑨ 有缓存时不再报「没比对」",
      not _res2.get("stats", {}).get("pool_note"), _res2.get("stats"))

ls.PoolIndex = _orig_pool_index
DataPoolManager._default_cache_dir = _orig_cache_dir

# ── ⑩ 旧版 .xls：格式判定看字节头，不看后缀 ────────────────────────────────
# 实机依据（本轮 scan_inventory 跑出来的最后一个「读不了」）：
#   /Users/jhnoplan/Downloads/未命名文件夹 3/2019年上海展客户整理.xlsx
#   file 显示 Composite Document File V2（WPS 存的旧版 OLE），后缀却写着 .xlsx，
#   里面是 1551 条真实客户名单。openpyxl 读它必抛 BadZipFile → 扫描层判「读不了」
#   ＝整份名单被丢掉。这一组钉死三件事：
#   a) 分派看字节头；b) .xls 走 xlrd 且单元格口径与 xlsx 完全一致（手机号绝不能
#      变成科学计数法）；c) 缺 xlrd/文件坏了都给「另存为 .xlsx」这条用户能自助的错，
#      并且后缀是 Excel 而内容不是时绝不硬当 CSV 读（读出来是一堆标签，列匹配全废）。
print("\n[⑩ 旧版 .xls 与字节头分派]")
import datetime  # noqa: E402
import types  # noqa: E402
import customer_library_cleaner as C  # noqa: E402
import data_pool as dp  # noqa: E402

_OLE_HEAD = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _bytes_file(name, data):
    p = os.path.join(FILES, name)
    with open(p, "wb") as f:
        f.write(data)
    return p


xls_real = _bytes_file("老名单.xls", _OLE_HEAD + b"\0" * 2000)
xls_lie = _bytes_file("2019年上海展客户整理.xlsx", _OLE_HEAD + b"\0" * 2000)
txt_lie = _bytes_file("其实是纯文本.xlsx", b"hello,world\n1,2\n3,4\n")

check("⑩ 内容是 OLE 就判 xls，后缀写 .xlsx 也骗不过去（本轮丢数据正因按后缀走）",
      C.excel_kind(xls_real) == C.XLS_KIND and C.excel_kind(xls_lie) == C.XLS_KIND,
      (C.excel_kind(xls_real), C.excel_kind(xls_lie)))
check("⑩ 真 xlsx（zip 容器）判 xlsx；纯文本判不出字节头就是 unknown（绝不猜成 Excel）",
      C.excel_kind(GOOD) == C.XLSX_KIND and C.excel_kind(txt_lie) == C.UNKNOWN_KIND,
      (C.excel_kind(GOOD), C.excel_kind(txt_lie)))

# 后缀是 Excel、字节头两头都不像：必须明确报错让用户去源头另存
err = None
try:
    C.read_tables(txt_lie)
except Exception as e:
    err = e
check("⑩ 后缀 Excel + 内容非 Excel → 报「扩展名与实际格式不符」，绝不按 CSV 硬读",
      isinstance(err, ValueError) and "扩展名与实际格式不符" in str(err)
      and "另存为" in str(err), str(err))

# ── 假 xlrd：够跑 read_xls_tables 的全部接口，零依赖真实 .xls 夹具 ──────────
F_EMPTY, F_TEXT, F_NUM, F_DATE, F_BLANK, F_ERR = 0, 1, 2, 3, 6, 16
_MAIN = [
    [(F_TEXT, "公司名"), (F_TEXT, "手机"), (F_TEXT, "参展日期"),
     (F_TEXT, "备注"), (F_TEXT, "公式")],
    [(F_TEXT, "广州速亿新能源有限公司"), (F_NUM, 15052473522.0),
     (F_DATE, 43640.0), (F_BLANK, ""), (F_ERR, 7)],
    [(F_TEXT, "四圳法能新能源有限公司"), (F_NUM, 1.3851871032e10),
     (F_DATE, 43640.5), (F_EMPTY, ""), (F_TEXT, "")],
]
_HDR_ONLY = [[(F_TEXT, "公司名")]]
_ALL_BLANK = [[(F_EMPTY, "")], [(F_EMPTY, "")]]


class _Sheet:
    def __init__(self, name, cells):
        self.name, self._c = name, cells
        self.nrows = len(cells)
        self.ncols = max((len(r) for r in cells), default=0)

    def cell_type(self, r, c):
        return self._c[r][c][0] if c < len(self._c[r]) else F_EMPTY

    def cell_value(self, r, c):
        return self._c[r][c][1] if c < len(self._c[r]) else ""


class _Book:
    datemode = 0
    released = False

    def __init__(self, sheets):
        self._s = sheets

    def sheets(self):
        return self._s

    def release_resources(self):
        _Book.released = True


_calls = []
_book = _Book([_Sheet("Contacts", _MAIN), _Sheet("说明", _HDR_ONLY),
               _Sheet("空页", _ALL_BLANK)])
_real_xlrd = sys.modules.get("xlrd")
_fake_xlrd = types.ModuleType("xlrd")
for _n, _v in [("XL_CELL_EMPTY", F_EMPTY), ("XL_CELL_TEXT", F_TEXT),
               ("XL_CELL_NUMBER", F_NUM), ("XL_CELL_DATE", F_DATE),
               ("XL_CELL_BLANK", F_BLANK), ("XL_CELL_ERROR", F_ERR)]:
    setattr(_fake_xlrd, _n, _v)
_fake_xlrd.xldate_as_datetime = (
    lambda v, dm: datetime.datetime(2019, 5, 20) + datetime.timedelta(days=v - 43640))
_fake_xlrd.open_workbook = lambda p, on_demand=False: (
    _calls.append((p, on_demand)), _book)[1]
sys.modules["xlrd"] = _fake_xlrd
try:
    t_xls = C.read_xls_tables(xls_real)
    check("⑩ .xls 交给 xlrd 且 on_demand=True（不把整本共享字符串表都灌进内存）",
          len(_calls) == 1 and _calls[0][1] is True, _calls)
    rows = t_xls[0][1]
    check("⑩ 数字单元格展开成完整号码（老 Excel 手机号的科学计数法坑）",
          rows[1][1] == "15052473522" and rows[2][1] == "13851871032",
          (rows[1][1], rows[2][1]))
    check("⑩ 日期单元格取年月日，带时刻的连时刻一起给",
          rows[1][2] == "2019-05-20" and rows[2][2] == "2019-05-20 12:00:00",
          (rows[1][2], rows[2][2]))
    check("⑩ 空白与公式错误码（#N/A/#VALUE!）当空串，不掺进数据",
          rows[1][3] == "" and rows[1][4] == "" and rows[2][3] == "",
          (rows[1][3], rows[1][4], rows[2][3]))
    check("⑩ 只有表头/整页空白的 sheet 不入选（与 openpyxl 分支同一道门槛）",
          [n for n, _ in t_xls] == ["Contacts"], [n for n, _ in t_xls])
    check("⑩ 读完释放文件句柄（on_demand 的本意，异常路径也必须释放）",
          _Book.released is True)

    _calls.clear()
    t_disp = C.read_tables(xls_lie)
    check("⑩ read_tables 把「后缀 .xlsx、内容 OLE」的老表送去 xlrd（以前是 BadZipFile）",
          len(_calls) == 1 and t_disp == t_xls, _calls)

    # 采样层必须主动让位：openpyxl 读不了 .xls，返回空正好触发权威版回落
    check("⑩ 扫描采样层对 .xls 直接让位（返回空 → 回落 preview_excel_mapping）",
          ls._quick_tables_raw(xls_real, 200) == [])
    xls_tree = os.path.join(TMP, "xls_tree")
    os.makedirs(xls_tree, exist_ok=True)
    shutil.copy(xls_lie, os.path.join(xls_tree, "2019年上海展客户整理.xlsx"))
    led10 = ls.Ledger(cache_dir=os.path.join(TMP, "cache10")).load()
    it10, st10 = ls.scan_all(roots=[xls_tree], ledger=led10, pool_index=None)
    check("⑩ 全盘扫描里这份老表不再是「读不了」（本轮实机丢的那份名单回来了）",
          it10 and all(i["status"] != ls.ST_UNREADABLE for i in it10),
          [(i["status"], i["reason"]) for i in it10])

    # 缺 xlrd：打包态的真实风险，必须给能自助的错，不能崩栈
    sys.modules["xlrd"] = None
    err2 = None
    try:
        C.read_xls_tables(xls_real)
    except Exception as e:
        err2 = e
    check("⑩ 环境缺 xlrd 时给「另存为 .xlsx」这条用户能自助的错（不是崩栈）",
          isinstance(err2, ValueError) and "xlrd" in str(err2), str(err2))
    check("⑩ 缺 xlrd 的错翻成界面文案时仍保留 xlrd 关键字（用户知道该装什么）",
          "xlrd" in ls._friendly_read_error(err2), ls._friendly_read_error(err2))

    # 文件真坏了：open_workbook 抛异常 → 明确报错，不是把栈甩给用户
    def _boom(p, on_demand=False):
        raise IOError("SST header records len mismatch")

    bad_mod = types.ModuleType("xlrd")
    for _n, _v in [("XL_CELL_EMPTY", F_EMPTY), ("XL_CELL_TEXT", F_TEXT),
                   ("XL_CELL_NUMBER", F_NUM), ("XL_CELL_DATE", F_DATE),
                   ("XL_CELL_BLANK", F_BLANK), ("XL_CELL_ERROR", F_ERR)]:
        setattr(bad_mod, _n, _v)
    bad_mod.open_workbook = _boom
    bad_mod.xldate_as_datetime = _fake_xlrd.xldate_as_datetime
    sys.modules["xlrd"] = bad_mod
    err3 = None
    try:
        C.read_xls_tables(xls_real)
    except Exception as e:
        err3 = e
    check("⑩ 老表本体坏了也报「另存为 .xlsx」，不是把栈甩给用户",
          isinstance(err3, ValueError) and "另存为" in str(err3), str(err3))
finally:
    if _real_xlrd is not None:
        sys.modules["xlrd"] = _real_xlrd
    else:
        sys.modules.pop("xlrd", None)

# ── 判据同源的三处红线（改一处必须同时改另两处，历史上就是这么漏的）────────
check("⑩ 后缀白名单含 .xls：扫描层/压缩包展开/导入入口共用这一份",
      ".xls" in dp.IMPORT_EXCEL_EXTS
      and ".xls" in ls.IMPORT_EXCEL_EXTS, dp.IMPORT_EXCEL_EXTS)
_pairs, _skips, _staging = dp.expand_import_files([xls_real])
check("⑩ 直接选 .xls 不再被「旧版格式」跳过（展开层与白名单同源）",
      [p for p, _ in _pairs] == [xls_real] and not _skips, (_pairs, _skips))
if _staging:
    shutil.rmtree(_staging, ignore_errors=True)
_dp_src = open(os.path.join(BASE, "b2bcollect_build", "data_pool.py"),
               encoding="utf-8").read()
check("⑩ 源码里不能再留「旧版 .xls 格式不支持」这种按后缀拒绝的分支",
      "旧版 .xls 格式不支持" not in _dp_src)
check("⑩ 界面「只认哪些后缀」从白名单变量拼出来（手写清单会说谎）",
      "accepted_types_text()" in open(
          os.path.join(BASE, "b2bcollect_build", "pool_dialogs.py"),
          encoding="utf-8").read()
      and '只认 .xlsx / .xlsm / .csv' not in _pd_src
      and ".xls" in ls.accepted_types_text(), ls.accepted_types_text())

# ── 数据来源溯源：中文文件名绝不能被抠成日期残片（本轮实测量：97 个里 32 个）──
clean = dp.DataPoolManager._clean_source_name
check("⑩ 中文名取有意义的首段，不是抠出来的日期",
      clean("深海装备潜客_客户1_20261109_171742.xlsx") == "深海装备潜客"
      and clean("索比光伏网试采10414_20261102.xlsx") == "索比光伏网试采10414"
      and clean("2019年上海展客户整理.xlsx") == "2019年上海展客户整理",
      (clean("深海装备潜客_客户1_20261109_171742.xlsx"),
       clean("索比光伏网试采10414_20261102.xlsx"),
       clean("2019年上海展客户整理.xlsx")))
check("⑩ 既有的域名型清洗一条不许回退（历史用例逐个钉住）",
      clean("ZHY20260721CJb2b.bjx.com.cn黄芷茵.xlsx") == "b2b.bjx.com.cn"
      and clean("20261109_1688.com_张三.xlsx") == "1688.com"
      and clean("Made-in-China") == "Made-in-China"
      and clean("b2b168_光伏储能电池相关公司.xlsx") == "b2b168",
      (clean("ZHY20260721CJb2b.bjx.com.cn黄芷茵.xlsx"),
       clean("20261109_1688.com_张三.xlsx"), clean("Made-in-China"),
       clean("b2b168_光伏储能电池相关公司.xlsx")))

# ── ⑪ 否定结论的有效期：台账缓存的是「解读」，不是事实 ─────────────────────
# 本轮 GUI 实机抓出的第二个真缺陷：加完 .xls 字节头分派之后，真实台账仍按内容
# 指纹复用升级前记下的「读不了」，把一份 1623 条的展会名单永久按在清单外（界面
# 显示 读不了 3、离线脚本显示 读不了 0，就是这个分叉暴露出来的）。
print("\n[⑪ 能力版本与否定结论的有效期]")

STALE_CAP = "1999-01-01.0"          # 一个绝对不存在的旧能力版
GOOD_P = os.path.join(TMP, "scan", "客户名单.xlsx")
_fp_good = ls.fingerprint(GOOD_P)
_sz_good, _mt_good = os.path.getsize(GOOD_P), int(os.path.getmtime(GOOD_P))
REAL_REASON = "文件损坏或不是真正的 Excel（扩展名与实际格式不符）"


def _ledger_file(cache_dir, capability, entries):
    os.makedirs(cache_dir, exist_ok=True)
    payload = {"version": 1, "updated": "2026-11-11T00:00:00", "files": entries}
    if capability is not None:
        payload["capability"] = capability
    with open(os.path.join(cache_dir, "local_scan_ledger.json"), "w",
              encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    return payload


_neg_entries = [
    {"fp": _fp_good, "size": _sz_good, "mtime": _mt_good,
     "paths": [GOOD_P], "status": ls.ST_UNREADABLE, "reason": REAL_REASON},
    {"fp": "sha256:别的文件", "size": 100, "mtime": 100,
     "paths": [os.path.join(TMP, "scan", "说明表.xlsx")],
     "status": ls.ST_INVALID, "reason": "没认出「公司名」列"},
    {"fp": "sha256:已经传过的内容", "size": 200, "mtime": 200,
     "paths": [os.path.join(TMP, "scan", "老名单.xlsx")],
     "status": ls.ST_INGESTED, "rows_kept": 88, "ts": "2026-11-01T00:00:00"},
]

_c1 = os.path.join(TMP, "cap_stale")
_ledger_file(_c1, STALE_CAP, _neg_entries)
_c2 = os.path.join(TMP, "cap_missing")
_ledger_file(_c2, None, _neg_entries)   # 升级前写下的老台账：完全没有这一项
led11 = ls.Ledger(cache_dir=_c1).load()
check("⑪ 能力版本变了：「读不了/不合格」这类解读全部作废重判",
      led11.reset_negatives == 2
      and led11.entry(_fp_good) is None
      and led11.entry("sha256:别的文件") is None, led11.reset_negatives)
check("⑪ 但 ingested 一条都不能丢：那是「真上传成功过」的事实，与读法无关"
      "（忘了它就会把同一批数据再灌一遍）",
      led11.ingested("sha256:已经传过的内容") is True
      and led11.entry("sha256:已经传过的内容")["rows_kept"] == 88)
check("⑪ 老台账压根没写 capability 这一项：同样按「不可信」处理（迁移场景）",
      ls.Ledger(cache_dir=_c2).load().reset_negatives == 2)
_ledger_file(os.path.join(TMP, "cap_now"), ls.CAPABILITY, _neg_entries)
led11b = ls.Ledger(cache_dir=os.path.join(TMP, "cap_now")).load()
check("⑪ 能力版本没变：沿用旧结论，绝不白白重读大文件（热扫描省 IO 的本意）",
      led11b.reset_negatives == 0 and led11b.entry(_fp_good)["status"]
      == ls.ST_UNREADABLE, led11b.reset_negatives)
check("⑪ 写盘必须把能力版本带上，否则下次加载无从判断结论出自哪一版读法",
      ls.Ledger(cache_dir=os.path.join(TMP, "cap_w")).save()
      and json.load(open(os.path.join(TMP, "cap_w",
                                      "local_scan_ledger.json"),
                         encoding="utf-8"))["capability"] == ls.CAPABILITY)

# 端到端：旧「读不了」结论挡不住一份其实能读的好表
items11, stats11 = ls.scan_all(roots=[os.path.join(TMP, "scan")],
                               ledger=ls.Ledger(cache_dir=_c1).load(),
                               pool_index=None)
hit11 = [i for i in items11 if i["path"] == GOOD_P]
check("⑪ 端到端：能力升级前记的「读不了」不能继续把一个能读的文件按在清单外",
      hit11 and hit11[0]["status"] in (ls.ST_NEW, ls.ST_FAMILY, ls.ST_DUP)
      and hit11[0]["status"] != ls.ST_UNREADABLE,
      hit11 and (hit11[0]["status"], hit11[0]["reason"]))
check("⑪ 统计里要如实报出「本次重判了多少个」，界面才答得出来",
      stats11.get("negatives_reset", 0) >= 1, stats11.get("negatives_reset"))

# 反向：能力版本一致时，缓存的读不了结论照旧生效（省 IO 的行为不能一起改掉）
items11c, _ = ls.scan_all(roots=[os.path.join(TMP, "scan")],
                          ledger=ls.Ledger(cache_dir=os.path.join(TMP, "cap_now")).load(),
                          pool_index=None)
hit11c = [i for i in items11c if i["path"] == GOOD_P]
check("⑪ 同一版读法下缓存结论照用（不能把省 IO 的机制顺手改成永远重算）",
      hit11c and hit11c[0]["status"] == ls.ST_UNREADABLE,
      hit11c and hit11c[0]["reason"])

# ── 环境抖动不等于文件的错：这类失败不许写进台账 ──────────────────────────
_transient = [PermissionError(13, "Permission denied"),
              FileNotFoundError(2, "No such file"),
              IsADirectoryError(21, "Is a directory"),
              OSError(errno.EMFILE, "Too many open files"),
              OSError(errno.ENFILE, "Process file table overflow")]
# 下面这几条是「文件本身就这样」：明天再扫还是同样的结论，才允许记进台账
_deterministic = [ValueError("扩展名与实际格式不符"),
                  __import__("zipfile").BadZipFile("File is not a zip file"),
                  RuntimeError("加密"),
                  Exception("This does not seem to be an XLS file")]
check("⑪ 临时性故障与确定性失败分得开（权限/文件没了/句柄吃紧＝临时）",
      all(ls._transient_io_error(e) for e in _transient)
      and not any(ls._transient_io_error(e) for e in _deterministic),
      [type(e).__name__ for e in _deterministic if ls._transient_io_error(e)])

_old_qp = ls.quick_preview
flaky_dir = os.path.join(TMP, "抖动目录")
os.makedirs(flaky_dir, exist_ok=True)
shutil.copy(GOOD_P, os.path.join(flaky_dir, "抖一下.xlsx"))
flaky_cache = os.path.join(TMP, "cap_io")
ls.quick_preview = lambda p: (_ for _ in ()).throw(
    PermissionError(13, "Permission denied: 沙箱这一刻不给读"))
try:
    items11d, _ = ls.scan_all(roots=[flaky_dir],
                              ledger=ls.Ledger(cache_dir=flaky_cache).load(),
                              pool_index=None)
finally:
    ls.quick_preview = _old_qp
_fl = [i for i in items11d if i["path"].endswith("抖一下.xlsx")]
check("⑪ 环境抖动当场如实标「读不了」，并明说没记账、下次重判",
      _fl and _fl[0]["status"] == ls.ST_UNREADABLE
      and _fl[0].get("cacheable") is False and "未记账" in _fl[0]["reason"],
      _fl and (_fl[0]["status"], _fl[0].get("cacheable"), _fl[0]["reason"]))
check("⑪ 抖动的结论绝不落台账（落了就等于把一次抖动固化成永久罪名）",
      not any(v.get("status") == ls.ST_UNREADABLE
              for v in ls.Ledger(cache_dir=flaky_cache).load().entries.values()),
      {k: v.get("status") for k, v in
       ls.Ledger(cache_dir=flaky_cache).load().entries.items()})
items11e, _ = ls.scan_all(roots=[flaky_dir],
                          ledger=ls.Ledger(cache_dir=flaky_cache).load(),
                          pool_index=None)
_he = [i for i in items11e if i["path"].endswith("抖一下.xlsx")]
check("⑪ 下一次扫描自愈：同一份文件、同一版读法，只是这次环境正常",
      _he and _he[0]["status"] in (ls.ST_NEW, ls.ST_FAMILY, ls.ST_DUP),
      _he and (_he[0]["status"], _he[0]["reason"]))

# 红线：判据只有一份，界面不许对手写文案
_ls_src = open(os.path.join(BASE, "b2bcollect_build", "local_scan.py"),
               encoding="utf-8").read()
_pd_src2 = open(os.path.join(BASE, "b2bcollect_build", "pool_dialogs.py"),
                encoding="utf-8").read()
check("⑪ 写台账这一处必须过滤掉不可缓存的项（漏掉就把抖动固化成罪名）",
      'f.get("cacheable", True) is False' in _ls_src)
check("⑪ 扫描对话框必须把「重判了多少个」显示出来（不显示＝用户觉得工具在偷偷改口）",
      "negatives_reset" in _pd_src2)
check("⑪ 否定结论的状态集合只定义一次，界面/台账/作废三处共用",
      ls.NEGATIVE_STATUSES == (ls.ST_INVALID, ls.ST_UNREADABLE))
check("⑪ 扫完之后界面不许还挂着「正在识别…」（进行时必须是真进行时）",
      _pd_src2.index("扫描已结束") < _pd_src2.index('if self._stats.get("denied")'),
      "结束文案要写在 denied 之前，让更该说的话覆盖它")

# ── ⑫ 压缩包进全盘扫描：发现 → 展开 → 包内判重 → 可直接上传 ────────────────
# 本轮需求本体（「很多压缩包里有 excel 文件」）。要点不止「进得去」：同一份内容
# 散着放也放、包里也放时**只该传一次**；包里的系列快照只勾最新一版；进不去的包
# 必须逐条说清楚为什么，不能把「没能力进去」说成「里面没数据」。
print("\n[⑫ 压缩包展开与包内判重]")
import datetime as _dt12  # noqa: E402
import stat  # noqa: E402
import tarfile  # noqa: E402
import zipfile  # noqa: E402
from pathlib import Path as _P12  # noqa: E402
from data_pool import DataPoolManager as _DPM12  # noqa: E402
import data_pool as _dp12  # noqa: E402

ARCH = os.path.join(TMP, "arch12")
ACACHE = os.path.join(TMP, "acache12")
ACDIR = str(_P12(ACACHE) / "archive_cache")
os.makedirs(ARCH, exist_ok=True)

A12 = make_xlsx(os.path.join(TMP, "a12.xlsx"), [
    ("Sheet1", [["公司名", "手机"], ["甲方机械有限公司", "13800001111"]])])
B12 = make_xlsx(os.path.join(TMP, "b12.xlsx"), [
    ("Sheet1", [["公司名", "邮箱"], ["乙电集团有限公司", "b@e.com"],
                ["乙电集团上海分公司", "c@e.com"]])])
C12 = make_xlsx(os.path.join(TMP, "c12.xlsx"), [
    ("Sheet1", [["企业名", "电话"], ["丙光能源股份有限公司", "020-12345678"],
                ["丙光能源设备部", "020-87654321"]])])
S_OLD = make_xlsx(os.path.join(TMP, "系列客户表_20261001.xlsx"), [
    ("Sheet1", [["公司名", "手机"], ["老甲有限公司", "13802115601"]])])
S_NEW = make_xlsx(os.path.join(TMP, "系列客户表_20261101.xlsx"), [
    ("Sheet1", [["公司名", "手机"], ["老甲有限公司", "13802115601"],
                ["新乙有限公司", "13802115602"]])])
os.utime(S_OLD, (_dt12.datetime(2026, 10, 1, 9, 0).timestamp(),) * 2)
os.utime(S_NEW, (_dt12.datetime(2026, 11, 1, 9, 0).timestamp(),) * 2)
F12 = make_xlsx(os.path.join(TMP, "f12.xlsx"), [
    ("Sheet1", [["公司名", "邮箱"], ["戊土建设工程有限公司", "f@e.com"]])])
# 真机那台机器上出现过的一层目录名（台球展会数据），故意带中文和点号
_UTF8_NAME = "11.10台球数据/表F.xlsx"


def _encrypt_flag(zip_path: str, member_name: str) -> None:
    """把某成员中央目录里的「加密」标志位打开（zipfile 写不出加密包，只能改字节）。

    不为难自己：验的不是解密能力，而是**这个成员取不出来时必须被记账**——真拿
    AES 包做夹具反而要靠第三方工具。
    """
    with zipfile.ZipFile(zip_path) as z:
        start = z.start_dir
    with open(zip_path, "rb") as f:
        f.seek(start)
        buf = bytearray(f.read())
    pos, hit = 0, 0
    while pos + 46 <= len(buf) and buf[pos:pos + 4] == b"PK\x01\x02":
        n_len = int.from_bytes(buf[pos + 28:pos + 30], "little")
        e_len = int.from_bytes(buf[pos + 30:pos + 32], "little")
        c_len = int.from_bytes(buf[pos + 32:pos + 34], "little")
        if bytes(buf[pos + 46:pos + 46 + n_len]).decode("utf-8", "ignore") == member_name:
            buf[pos + 8:pos + 10] = (1).to_bytes(2, "little")
            hit += 1
        pos += 46 + n_len + e_len + c_len
    assert hit, f"中央目录里没找到 {member_name}"
    with open(zip_path, "r+b") as f:
        f.seek(start)
        f.write(bytes(buf))


def _clear_utf8_flag(zip_path: str, member_name: str) -> None:
    """只把**一个成员**的「UTF-8 名字」标志位抹掉，复刻真机缺陷。

    真机那个包里的实际情况是两种名字并存：一部分成员名字节是 UTF-8 且打了 0x800，
    另一部分（macOS 自带压缩、老版 Java、部分 Go 库产出的）字节同样是 UTF-8，却不打
    标志位，读出来就是 ``11.10σÅ░τÉâµò░µì«``。全抹掉反而测不出「判据顺序」——只有两种
    名字同包并存，先试 UTF-8 还是先试 GBK 才会暴露。
    局部头和中央目录两处都得改：只改一处的话 zipfile 会因为「目录名与头名不一致」
    直接报错，测的就不再是这个场景了。
    """
    with zipfile.ZipFile(zip_path) as z:
        target = z.getinfo(member_name)
        assert target.flag_bits & 0x800, "夹具本身没写出 UTF-8 名字标志位"
        loff, start = target.header_offset, z.start_dir
    with open(zip_path, "r+b") as f:
        f.seek(loff + 6)                        # 局部头：标志位在偏移 6，2 字节
        flag = int.from_bytes(f.read(2), "little") & ~0x800
        f.seek(loff + 6)
        f.write(flag.to_bytes(2, "little"))
        f.seek(start)
        buf = bytearray(f.read())
        pos, hit = 0, 0
        while pos + 46 <= len(buf) and buf[pos:pos + 4] == b"PK\x01\x02":
            n_len = int.from_bytes(buf[pos + 28:pos + 30], "little")
            e_len = int.from_bytes(buf[pos + 30:pos + 32], "little")
            c_len = int.from_bytes(buf[pos + 32:pos + 34], "little")
            raw = bytes(buf[pos + 46:pos + 46 + n_len])
            if raw.decode("utf-8", "ignore") == member_name:
                flag = int.from_bytes(bytes(buf[pos + 8:pos + 10]), "little") & ~0x800
                buf[pos + 8:pos + 10] = flag.to_bytes(2, "little")
                hit += 1
            pos += 46 + n_len + e_len + c_len
        assert hit, f"中央目录里没找到 {member_name}"
        f.seek(start)
        f.write(bytes(buf))


def _link_info(name: str) -> "zipfile.ZipInfo":
    """造一个「名字看着像文件、内容其实是另一个路径」的 zip 符号链接成员。

    判据只有外置属性里的模式位（``S_IFLNK``），跟后缀无关：``base_library.zip``
    看着就是个包，本机实测那一条 29 字节的成员解出来是 ``../Resources/...``。
    """
    zi = zipfile.ZipInfo(name)
    zi.external_attr = (stat.S_IFLNK | 0o777) << 16
    zi.date_time = (2026, 11, 5, 9, 0, 0)
    return zi


NEST12 = os.path.join(TMP, "内层.zip")
with zipfile.ZipFile(NEST12, "w") as z:
    z.write(B12, "深层/表B.xlsx")
with zipfile.ZipFile(os.path.join(ARCH, "客户包.zip"), "w") as z:
    z.write(A12, "数据/表A.xlsx")               # 与散放的完全同一份内容
    z.write(S_OLD, "数据/系列客户表_20261001.xlsx")
    z.write(S_NEW, "数据/系列客户表_20261101.xlsx")
    z.write(A12, "数据/加密表.xlsx")            # 下面标成加密成员
    z.writestr("数据/说明.txt", "非表格成员不该进清单")
    z.write(F12, _UTF8_NAME)                    # 下面把 UTF-8 名字标志位抹掉
    z.writestr(_link_info("App/Frameworks/base_library.zip"),
              "../Resources/base_library.zip")  # 符号链接：不是包、也不是数据
_encrypt_flag(os.path.join(ARCH, "客户包.zip"), "数据/加密表.xlsx")
_clear_utf8_flag(os.path.join(ARCH, "客户包.zip"), _UTF8_NAME)
with tarfile.open(os.path.join(ARCH, "资料.tar.gz"), "w:gz") as t:
    t.add(C12, arcname="资料/表C.xlsx")
    t.add(NEST12, arcname="资料/内层.zip")       # 包中包：必须递归进去
    _li = tarfile.TarInfo("资料/旧链接.csv")
    _li.type, _li.linkname = tarfile.SYMTYPE, "表C.xlsx"
    t.addfile(_li)                              # tar 里的符号链接同样不许当数据
with zipfile.ZipFile(os.path.join(ARCH, "逃逸.zip"), "w") as z:
    z.write(A12, "../../../../escape12.xlsx")   # zip slip
with zipfile.ZipFile(os.path.join(ARCH, "空包.zip"), "w") as z:
    z.writestr("notes/readme.md", "这个包里真的没有表格")
with open(os.path.join(ARCH, "坏包.zip"), "wb") as f:
    f.write(b"PK\x03\x04" + b"\x00" * 300)
for _nm, _sig in (("老包.rar", b"Rar!\x1a\x07\x00"),
                  ("老包.7z", b"7z\xbc\xaf\x27\x1c")):
    with open(os.path.join(ARCH, _nm), "wb") as f:
        f.write(_sig + b"y" * 700)
shutil.copy(A12, os.path.join(ARCH, "散放的a12.xlsx"))

_ac12, _fb12 = ls.archive_cache_dir, ls._forbidden_dirs
ls.archive_cache_dir = lambda: _P12(ACACHE) / "archive_cache"
ls._forbidden_dirs = lambda: [os.path.realpath(ACACHE)]
try:
    _f12, _a12, _s12 = ls.iter_tables([ARCH])
    check("⑫ 发现层把压缩包单列一份，不与散表混成同一个清单（混了就等于各算一遍）",
          len(_a12) == 5 and len(_f12) == 1 and _s12["found"] == 1
          and _s12["archives_found"] == len(_a12),
          (len(_f12), len(_a12), _s12))
    check("⑫ rar/7z 如实记成「暂不支持解压」，不许说成「扫过了，里面没数据」",
          _s12["archives_unsupported"] == 2, _s12["archives_unsupported"])

    it12, st12 = ls.scan_all(roots=[ARCH], ledger=ls.Ledger(cache_dir=ACACHE).load(),
                             pool_index=None)
    by = {i["display"]: i for i in it12}
    mem = [i for i in it12 if i.get("logical")]
    check("⑫ 包内表格进了清单：zip、tar.gz、包中包都取到",
          len(mem) == 6 and {"表A.xlsx", "表C.xlsx", "表B.xlsx", "表F.xlsx",
                             "系列客户表_20261001.xlsx",
                             "系列客户表_20261101.xlsx"} <= set(by),
          sorted(i["logical"] for i in mem))
    check("⑫ 压缩包本身不作为数据条目（只报「这个包没取全」，不报「这个包是表格」）",
          not any(i["display"].lower().endswith((".zip", ".gz", ".rar", ".7z"))
                  for i in it12),
          [i["display"] for i in it12 if "." in i["display"][-4:]])
    check("⑫ 同一份内容散着放+包里放：只算一份，包内那份标成同内容副本且不勾",
          by["表A.xlsx"]["status"] == ls.ST_DUP
          and not by["表A.xlsx"]["checked"]
          and by["散放的a12.xlsx"]["status"] == ls.ST_NEW
          and by["散放的a12.xlsx"]["checked"],
          (by["表A.xlsx"]["status"], by["散放的a12.xlsx"]["status"]))
    check("⑫ 副本的判据要说清「勾那一份就行」，只写「重复」没人知道该动谁",
          "同一份内容只传一次" in by["表A.xlsx"]["reason"],
          by["表A.xlsx"]["reason"])
    check("⑫ 包内同系列快照只勾最新一版，旧版标「同系列旧版」",
          by["系列客户表_20261101.xlsx"]["status"] == ls.ST_NEW
          and by["系列客户表_20261001.xlsx"]["status"] == ls.ST_FAMILY
          and not by["系列客户表_20261001.xlsx"]["checked"],
          {k: by[k]["status"] for k in ("系列客户表_20261001.xlsx",
                                        "系列客户表_20261101.xlsx")})
    deep = by["表B.xlsx"]
    check("⑫ 包中包成员的 origin 指向**顶层包**（内层落盘名是哈希，端给用户等于说谎）",
          deep["logical"].startswith("资料.tar.gz/资料/内层.zip/")
          and deep["origin"].endswith("资料.tar.gz")
          and os.path.isfile(deep["origin"]),
          (deep["logical"], deep["origin"]))
    check("⑫ 包内成员的「所在目录」接上真实磁盘路径，用户能自己找回去",
          by["表A.xlsx"]["dir"] == os.path.join(ARCH, "客户包.zip", "数据"),
          by["表A.xlsx"]["dir"])
    notes = st12["archive_notes"]
    ntxt = "｜".join(f"{n}:{r}" for n, r in notes)
    check("⑫ zip slip 成员被拒收：不落地、不进清单、判据本身也拦得住",
          "包内路径不安全" in ntxt and "escape12.xlsx" not in by
          and not ls._arc_ok("../../x.xlsx")
          and not ls._arc_ok("/etc/passwd.xlsx") and ls._arc_ok("数据/a.xlsx"),
          ntxt)
    check("⑫ 空包 / 坏包 / 加密成员各有各的实话，不许混成一句「读不了」",
          any(r == ls.NO_ARCHIVE_TABLE for n, r in notes)
          and any("已损坏" in r for n, r in notes)
          and any("加密" in r for n, r in notes), notes)
    # 真机实测（2026-11-11）：同一个包里两种名字并存——macOS 压缩把 UTF-8 的字节
    # 写进去却不打 0x800 标志，读出来就是 ``11.10σÅ░τÉâµò░µì«``。名字是用户找回数据
    # 的唯一线索，端一串乱码上界面等于没给线索。
    _zi = zipfile.ZipFile(os.path.join(ARCH, "客户包.zip"))
    _mojibake = [i for i in _zi.infolist()
                 if i.filename.encode("cp437", "ignore") == _UTF8_NAME.encode("utf-8")]
    check("⑫ 夹具确实复刻了缺陷：字节是 UTF-8 而标志位没打，zipfile 读出来必是乱码",
          len(_mojibake) == 1 and _UTF8_NAME not in _mojibake[0].filename
          and not _mojibake[0].flag_bits & 0x800,
          [i.filename for i in _mojibake])
    check("⑫ 还原名字先试 UTF-8 再试 GBK：判反了会把好名字判成乱码",
          _dp12._fix_zip_filename(_mojibake[0]) == _UTF8_NAME,
          _dp12._fix_zip_filename(_mojibake[0]))
    ff = by["表F.xlsx"]
    check("⑫ 清单里出现的是还原后的中文名，「所在目录」也按原名接得回去",
          ff["logical"] == f"客户包.zip/{_UTF8_NAME}"
          and ff["dir"] == os.path.join(ARCH, "客户包.zip", "11.10台球数据")
          and "σ" not in ff["logical"] and ff["status"] == ls.ST_NEW,
          (ff["logical"], ff["dir"], ff["status"]))
    # 符号链接成员：内容是一串路径而不是数据。当包去开会给一个好文件扣上「已损坏」
    # 的罪名；跟着路径去开又等于让包外可控字符串当坐标（与 followlinks=False 同一条线）
    _li12 = _zi.getinfo("App/Frameworks/base_library.zip")
    check("⑫ 符号链接的判据看模式位而不是文件名（base_library.zip 看着就是个包）",
          ls.is_zip_symlink(_li12)
          and not ls.is_zip_symlink(_zi.getinfo("数据/表A.xlsx")),
          oct(_li12.external_attr >> 16))
    check("⑫ 链接成员（zip 与 tar 两条链路）：不打开、不落地、不进清单，但如实记账",
          "base_library.zip" not in by and "旧链接.csv" not in by
          and not any("base_library" in i["path"] for i in it12)
          and sum(1 for n, r in notes if "符号链接" in r) == 2
          and not any("已损坏" in r and "客户包" in n for n, r in notes),
          [(n, r) for n, r in notes if "符号链接" in r or "已损坏" in r])
    # notes 的展示层：同一句话在 15 个包上重复（本机实测）会把「加密取不出」「路径
    # 不安全」挤出屏幕，所以同原因合并；合并只许发生在展示，明细必须还是全量逐条
    _syn = ([(f"工具包{i}.zip", ls.NO_ARCHIVE_TABLE) for i in range(3)]
            + [("app.zip", ls.ZIP_LINK_NOTE.format(n=12)),
               ("app2.zip", ls.ZIP_LINK_NOTE.format(n=3)),
               ("坏包.zip", "压缩包已损坏，读不出来")])
    _st12txt = ls.archive_notes_text(_syn)
    _stdet = ls.archive_notes_detail(_syn)
    check("⑫ 正文按原因合并：同一句话重复 15 次会把故障挤出屏幕；"
          "但数字不同就不许合并——替用户改数字比多一行更糟",
          "3 个包：" in _st12txt and _st12txt.count(ls.NO_ARCHIVE_TABLE) == 1
          and "app.zip：包里 12 个" in _st12txt
          and "app2.zip：包里 3 个" in _st12txt
          and _st12txt.index("已损坏") < _st12txt.index("3 个包："), _st12txt)
    check("⑫ 排序按「要不要用户动手」而不是按条数：符号链接是打包的正常产物，"
          "不该压着真损坏的包排在第一行",
          _st12txt.index("已损坏") < _st12txt.index("app.zip："), _st12txt)
    check("⑫ 合并只改展示不改账：明细里每一条包名与原文都还在，族标题不替某条说话",
          all(n in _stdet for n, _r in _syn)
          and ls.ZIP_LINK_NOTE.format(n=12) in _stdet
          and "若干" in _stdet and "共 6 条" in _st12txt, _stdet[:200])
    check("⑫ 真夹具的 notes 也走同一套展示：正文比明细短，但条数报得一模一样",
          len(ls.archive_notes_text(notes)) < len(ls.archive_notes_detail(notes))
          and f"共 {len(notes)} 条" in ls.archive_notes_text(notes),
          ls.archive_notes_text(notes))
    _many = [(f"包{i}.zip", ls.NO_ARCHIVE_TABLE) for i in range(30)]
    check("⑫ 明细一条都不许截断：合并是为了好读，窗口存在的理由就是逐条核对，"
          "拿上限切短等于把承诺再收回一半",
          all(f"包{i}.zip" in ls.archive_notes_detail(_many) for i in range(30))
          and "未列出" not in ls.archive_notes_detail(_many)
          and "29 个包" not in ls.archive_notes_text(_many)
          and "30 个包：" in ls.archive_notes_text(_many),
          ls.archive_notes_detail(_many)[:120])
    got = {i["display"]: len(_DPM12.read_records_from_excel(i["path"])) for i in mem}
    check("⑫ 上传链路能直接读包内成员（path 是真身，不是只够展示的假路径）",
          all(v >= 1 for v in got.values()), got)
    # 跨入口去重的根：成员指纹 == 同一份字节散开放时的指纹。手动导入传过的表，
    # 之后全盘扫描从包里翻出来也必须认成「这份内容传过了」，否则就是传了两遍。
    check("⑫ 包内成员的指纹与散放同一份内容完全相等（两条入口共用一份台账）",
          all(ls.fingerprint(i["path"]) == i["fp"] for i in mem)
          and by["表A.xlsx"]["fp"] == by["散放的a12.xlsx"]["fp"],
          [(i["display"], i["fp"][:18]) for i in mem])
    line = ls.summary_line(st12)
    check("⑫ 汇总要如实交代压缩包：进了几个、取出几张表、有几个没取全",
          "压缩包" in line and "没有全部取出" in line and "暂不支持解压" in line,
          line)
    check("⑫ 认哪些格式只写一份判据，界面文案引用它而不是另抄一行",
          "zip" in ls.accepted_types_text() and "tar" in ls.accepted_types_text()
          and "压缩包" in ls.accepted_types_text(), ls.accepted_types_text())

    it12b, st12b = ls.scan_all(roots=[ARCH],
                               ledger=ls.Ledger(cache_dir=ACACHE).load(),
                               pool_index=None)
    check("⑫ 第二遍（热缓存 + 台账）结论一字不差：成员指纹不重算、判定不改口",
          sorted((i["display"], i["status"]) for i in it12b)
          == sorted((i["display"], i["status"]) for i in it12)
          and st12b["archive"]["members"] == 6,
          sorted((i["display"], i["status"]) for i in it12b))
    _k = by["表C.xlsx"]["path"]
    _before = sum(len(fs) for _d, _dd, fs in os.walk(ACDIR))
    freed = ls.prune_archive_cache(cache_dir=ACDIR, keep=[_k], max_bytes=0)
    check("⑫ 逐出缓存只动没人用的：本批清单正在引用的成员绝不删（删了点上传就读不到）",
          os.path.isfile(_k) and freed > 0
          and sum(len(fs) for _d, _dd, fs in os.walk(ACDIR)) < _before,
          (freed, _before))
finally:
    ls.archive_cache_dir, ls._forbidden_dirs = _ac12, _fb12

# 界面接线实测（offscreen）：源码里有那几个函数名不算接上了，得真把扫描结果喂进
# 对话框走一遍回调，才知道用户点得到点不到
_start12, _exec12 = pd.LocalScanDialog._start_scan, pd.QDialog.exec_
_seen12 = []
pd.LocalScanDialog._start_scan = lambda self, *a, **k: None
pd.QDialog.exec_ = lambda self, *a: (_seen12.append(self.windowTitle()), 0)[1]
try:
    dlg12 = pd.LocalScanDialog()
    dlg12._on_scan_finished(True, it12, st12, "")
    check("⑫ 扫完界面真的有「查看明细」这一段，而且条数与扫描记账一致（不是死文字）",
          not dlg12.notes_lbl.isHidden()
          and "压缩包没有全部取出" in dlg12.notes_lbl.text()
          and not dlg12.notes_detail_btn.isHidden()
          and f"查看明细（{len(st12['archive_notes'])} 条）"
          == dlg12.notes_detail_btn.text(),
          (dlg12.notes_lbl.text(), dlg12.notes_detail_btn.text()))
    dlg12._show_notes_detail()
    check("⑫ 点「查看明细」弹出的就是那份逐条明细，标题上的条数也是真的",
          _seen12 and _seen12[-1] == f"压缩包没有全部取出的明细"
                                    f"（{len(st12['archive_notes'])} 条）", _seen12)
    rows = {dlg12.table.item(r, 1).text(): r
            for r in range(dlg12.table.rowCount()) if dlg12.table.item(r, 1)}
    ra = rows.get("表A.xlsx")
    check("⑫ 包内成员在清单里显示成「包名/包内路径」的逻辑名字，而不是那串缓存哈希",
          ra is not None
          and "客户包.zip" in str(dlg12.table.item(ra, 2).text())
          and len(dlg12.table.item(ra, 2).text()) < 200
          and dlg12.table.item(ra, 5).text() == "同内容副本",
          (ra, dlg12.table.item(ra, 2).text(), dlg12.table.item(ra, 5).text()))
finally:
    pd.LocalScanDialog._start_scan, pd.QDialog.exec_ = _start12, _exec12

# 红线：判据同源 + 界面不许一次点击绕过扫描层去重
_ls12 = open(os.path.join(BASE, "b2bcollect_build", "local_scan.py"),
             encoding="utf-8").read()
_pd12 = open(os.path.join(BASE, "b2bcollect_build", "pool_dialogs.py"),
             encoding="utf-8").read()
check("⑫ 界面勾选资格引用扫描层同一组常量，不许在界面抄第二份判据",
      all(k in _pd12 for k in ("ELIGIBLE_STATUSES", "DUPLICATE_STATUSES",
                               "BAD_STATUSES")))
check("⑫ 三组状态互斥且覆盖全部判定结果（漏一个就会有文件既不能勾又没人解释）",
      not set(ls.ELIGIBLE_STATUSES) & set(ls.DUPLICATE_STATUSES)
      and not set(ls.DUPLICATE_STATUSES) & set(ls.BAD_STATUSES)
      and not set(ls.ELIGIBLE_STATUSES) & set(ls.BAD_STATUSES)
      and set(ls.ELIGIBLE_STATUSES) | set(ls.DUPLICATE_STATUSES) | set(ls.BAD_STATUSES)
      == {ls.ST_NEW, ls.ST_UNCONFIRMED, ls.ST_INGESTED, ls.ST_DUP, ls.ST_FAMILY,
          ls.ST_INVALID, ls.ST_UNREADABLE},
      (ls.ELIGIBLE_STATUSES, ls.DUPLICATE_STATUSES, ls.BAD_STATUSES))
_seg = _pd12[_pd12.index("def _on_select_all"):_pd12.index("def _invert_checks")]
check("⑫ 「全选」必须先把越界的行数摊开问一次（历史上一次点击就静默勾上 168 份快照）",
      "QMessageBox.question" in _seg and "QMessageBox.No)" in _seg, _seg[:120])
_i12 = _pd12.index("def _invert_checks")
_seg2 = _pd12[_i12:_i12 + 1200]
check("⑫ 「反选」不许把重复/旧版/不合格勾上（越界只允许走确认框那一条路）",
      "not in elig" in _seg2 and "_set_check(r, it, False)" in _seg2, _seg2[:120])
check("⑫ 汇总说了「详见清单下方」，界面就必须真有那一段（不许开空头支票）",
      "archive_notes_text" in _pd12 and "notes_lbl" in _pd12)
check("⑫ 汇总指的路必须和界面上的按钮同名：「点查看明细」而界面没有这四个字，"
      "就是让用户在屏幕上找不存在的东西",
      "查看明细" in ls.summary_line({"archive_notes": [("a.zip", "原因")],
                                     "archive": {"archives": 1}})
      and "查看明细" in _pd12)
check("⑫ 明细必须能点开逐条看：悬停提示既不能滚动也不能复制，"
      "把全量明细只托付给它等于没有明细",
      "archive_notes_detail" in _pd12 and "notes_detail_btn" in _pd12)
check("⑫ 正文合并只发生在展示层：归族函数逐条保留原文，"
      "「正常现象」也不许把故障挤出屏幕",
      "BENIGN_ARCHIVE_NOTES" in _ls12 and "archive_notes_grouped" in _ls12
      and "for name, reason in notes" in _ls12)
check("⑫ 上传前的重复提醒要按同一组状态分桶，含「同系列旧版」",
      "dup_statuses" in _pd12 and "DUPLICATE_STATUSES" in _pd12)
check("⑫ 范围文案不许再谎称「跳过压缩包」（那是能力边界，不是数据边界）",
      "跳过压缩包" not in _pd12)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n全部通过：{ok} 项")

# 判定到此为止：屏幕上这句和批跑读到的退出码必须是同一个来源。这套件自己建过
# QApplication，而 Qt/线程在解释器收尾时的析构会随机段错误（本机实测：keyword_resume
# 全绿 88 项之后 rc=139，被批跑读成一次失败，单跑又绿——真红就是这么被当成抖动的）。
# flush 之后直接把退出码交给操作系统，不让任何收尾动作再改写判定。
sys.stdout.flush()
sys.stderr.flush()
os._exit(0)
