"""断点续采台账离线自测：状态机分流、待采集合、原子写、损坏容错、奇偶归属稳定。

不启动浏览器、不碰网络：全部用临时目录与假记录跑，本机/CI 都能直接执行。
"""
import json
import os
import shutil
import tempfile
import threading

import crawl_checkpoint as CP
import dual_session as _dual
import block_guard
import task_registry as _tasks
from openpyxl import Workbook

ROOT = tempfile.mkdtemp(prefix="checkpoint_test_")
COMPANIES = ["A公司", "B公司", "C公司", "D公司", "E公司"]


def folder(tag):
    """每个用例单独一个会话目录，免得前一个用例写的进度文件干扰后一个"""
    path = os.path.join(ROOT, tag)
    os.makedirs(path, exist_ok=True)
    return path


def rec(name, status="存续", source="企知道"):
    return {"公司名": name, "状态": status, "数据来源": source, "电话": "13800001111"}


# ── 1. 建台账：名单顺序快照 + 全 pending + 立刻落盘 ──
cp = CP.CrawlCheckpoint.create("t1", folder("t1"), COMPANIES, log=print)
assert cp.snapshot == COMPANIES, cp.snapshot
assert os.path.exists(cp.path), "建台账就要落一次盘，崩之前也有进度"
assert cp.summary()["todo"] == 5 and cp.summary()[CP.STATUS_DONE] == 0, cp.summary()
print("1 ✅ 建台账:", cp.text_summary())

# ── 2. 状态机分流：有效 / 站点确认查不到（永久）/ 临时失败（重试） ──
assert CP.classify_record(rec("A公司")) == CP.STATUS_DONE
assert CP.classify_record(rec("C公司", "未找到该公司")) == CP.STATUS_NO_RESULT
assert CP.classify_record(rec("D公司", "模糊查询额度用尽待重试")) == CP.STATUS_FAILED
assert CP.classify_record(rec("E公司", "失败: 会话异常")) == CP.STATUS_FAILED
assert CP.classify_record({"状态": "存续"}) == CP.STATUS_FAILED, "没有公司名不能算采到"
cp.mark_record(rec("A公司"), "企知道")
cp.mark_record(rec("C公司", "未找到该公司"), "爱企查")
cp.mark_record(rec("D公司", "模糊查询额度用尽待重试"), "爱企查")
cp.mark_record(rec("E公司", "失败: 会话异常"), "企知道")
cp.flush(force=True)

# ── 3. 待采集合 = 名单 − done − no_result（failed 计入待采重试） ──
todo = cp.pending()
assert todo == ["B公司", "D公司", "E公司"], todo
s = cp.summary()
assert (s[CP.STATUS_DONE], s[CP.STATUS_NO_RESULT], s[CP.STATUS_FAILED], s["todo"]) == (1, 1, 2, 3), s
print("3 ✅", cp.text_summary())

# ── 4. 幂等键：同一家的空格/全半角括号写法不能算两条；临时失败不打回已采数据 ──
assert CP.norm_key(" 深圳A公司 ") == CP.norm_key("深圳A　公司") == CP.norm_key("深圳A公司")
assert CP.norm_key("腾讯(北京)") == CP.norm_key("腾讯（北京）")
# 幂等键必须与双标签“已采跳过”的判据同一套口径，否则两边算出来的待采不一致
assert CP.norm_key(" 腾讯（北京） ") == block_guard.norm_company("腾讯(北京)")
cp.mark("  A公司  ", CP.STATUS_DONE, "企知道")      # 同一家换个写法
assert len([k for k in cp.items if k == CP.norm_key("A公司")]) == 1, cp.items
assert cp.status_of("A公司") == CP.STATUS_DONE
cp.mark("A公司", CP.STATUS_FAILED)
assert cp.status_of("A公司") == CP.STATUS_DONE, "临时失败不该抹掉已到手的记录"
print("4 ✅ 幂等键与状态保护")

# ── 5. 落盘频率：每采满 5 条自动 flush；状态没变不重复写盘 ──
cp2 = CP.CrawlCheckpoint.create("t5", folder("t5"), COMPANIES, log=print)
writes = []
_real_replace = os.replace


def spy_replace(src, dst):
    writes.append(dst)
    return _real_replace(src, dst)


os.replace = spy_replace
try:
    for i in range(4):
        cp2.mark(COMPANIES[i], CP.STATUS_DONE)
    assert len(writes) == 0, f"未满 5 条不该落盘：{writes}"
    cp2.mark("E公司", CP.STATUS_DONE)
    assert len(writes) == 1, f"第 5 条应触发一次落盘：{writes}"
    cp2.mark("E公司", CP.STATUS_DONE)
    assert len(writes) == 1, f"状态没变不该重复写盘：{writes}"
finally:
    os.replace = _real_replace
print("5 ✅ 每采满 5 条落盘一次，且无变更不写")

# ── 6. 读回 + 名单指纹（同一份名单才劝续采） ──
cp6 = CP.CrawlCheckpoint.create("t6", folder("t6"), COMPANIES, log=print)
same = os.path.join(ROOT, "名单.xlsx")
with open(same, "w", encoding="utf-8") as fh:
    fh.write("公司名\nA公司\n")
cp6.files = CP.files_fingerprint([same])
cp6.mark("A公司", CP.STATUS_DONE)
cp6.flush(force=True)
cp6b, msg = CP.CrawlCheckpoint.open_folder(cp6.folder)
assert cp6b is not None, msg
assert cp6b.snapshot == COMPANIES and cp6b.status_of("A公司") == CP.STATUS_DONE
assert cp6b.matches_files([same]), "同一份名单必须能续采"
other = os.path.join(ROOT, "另一份名单.xlsx")
with open(other, "w", encoding="utf-8") as fh:
    fh.write("公司名\nX公司\n")
assert not cp6b.matches_files([other]), "换了名单不能弹续采"
assert CP.inspect_folder(cp6.folder, COMPANIES, [same])[0] is not None, "指纹对上应可续采"
assert CP.inspect_folder(cp6.folder, COMPANIES, [other])[0] is None, "指纹不对不该续采"
print("6 ✅ 读回与指纹比对")

# ── 7. 损坏进度文件容错：不崩、给出可恢复提示 ──
broken = folder("broken")
with open(os.path.join(broken, CP.CHECKPOINT_FILE_NAME), "w", encoding="utf-8") as fh:
    fh.write('{"version": 1, "snapshot": ["A公司"')      # 半截写
cp7, msg7 = CP.CrawlCheckpoint.open_folder(broken)
assert cp7 is None and "损坏" in msg7, (cp7, msg7)
print("7 ✅ 容错提示:", msg7)

# ── 8. 用原数据 Excel 重建状态（损坏时的兜底路径） ──
seed = folder("seed")
wb = Workbook()
ws = wb.active
ws.append(["公司名", "状态", "数据来源", "电话"])
ws.append(["A公司", "存续", "企知道", "13800001111"])
ws.append(["B公司", "未找到", "爱企查", ""])
wb.save(os.path.join(seed, _tasks.RAW_FILE_NAME))
wb.close()
records = CP.load_records_from_raw_excel(seed)
assert len(records) == 2, records
rebuilt = CP.CrawlCheckpoint.create("t8", seed, COMPANIES, log=print)
assert rebuilt.rebuild_done(records) == 2
assert rebuilt.status_of("A公司") == CP.STATUS_DONE
assert rebuilt.status_of("B公司") == CP.STATUS_NO_RESULT
assert rebuilt.pending() == ["C公司", "D公司", "E公司"], rebuilt.pending()
print("8 ✅ 从原数据 Excel 重建:", rebuilt.text_summary())

# ── 9. 双标签奇偶在续采前后归属稳定（同一家永远归同一个平台、不重不漏） ──
big = [f"第{i}家公司" for i in range(1, 21)]
qzd_before, aqc_before = _dual.split_parity(big)
cp9 = CP.CrawlCheckpoint.create("t9", folder("t9"), big, log=print)
cp9.mark(big[3], CP.STATUS_DONE)          # 第 4 家（双数 → 爱企查）已采到
cp9.mark(big[4], CP.STATUS_NO_RESULT)     # 第 5 家（单数 → 企知道）确认无结果
cp9.mark(big[5], CP.STATUS_FAILED)        # 第 6 家（双数 → 爱企查）临时失败待重试
todo9 = set(cp9.pending())
qzd_after, aqc_after = _dual.split_parity(cp9.snapshot)     # 奇偶仍按原始快照取
qzd_todo = [n for n in qzd_after if n in todo9]
aqc_todo = [n for n in aqc_after if n in todo9]
for name in todo9:
    assert (name in qzd_todo) == (name in qzd_before), ("续采时这家公司换了平台", name)
    assert (name in aqc_todo) == (name in aqc_before), ("续采时这家公司换了平台", name)
assert big[3] not in todo9 and big[4] not in todo9, "已完成/确认无结果的不该再采"
assert big[5] in aqc_todo and big[5] not in qzd_todo, "临时失败要回到原平台重试"
assert set(qzd_todo) | set(aqc_todo) == todo9, "待采名单要不重不漏"
assert not (set(qzd_todo) & set(aqc_todo)), "两个平台不能同时采同一家"
print(f"9 ✅ 奇偶归属稳定：企知道 {len(qzd_todo)} 家 / 爱企查 {len(aqc_todo)} 家")

# ── 10. 采集线程在跑、主线程定时落盘：文件不会被写成半个 ──
cp10 = CP.CrawlCheckpoint.create("t10", folder("t10"), COMPANIES, log=print)


def worker():
    for name in COMPANIES:
        cp10.mark_record(rec(name), "企知道")


t = threading.Thread(target=worker)
t.start()
for _ in range(20):
    cp10.flush()
t.join()
cp10.flush(force=True)
with open(cp10.path, "r", encoding="utf-8") as fh:
    on_disk = json.load(fh)
assert len(on_disk["items"]) == 5, on_disk["items"]
assert all(v["status"] == CP.STATUS_DONE for v in on_disk["items"].values())
assert not os.path.exists(cp10.path + ".tmp"), "临时文件必须被原子替换掉"
print("10 ✅ 原子写与并发落盘")

# ── 11. GUI 接线层：不建窗口，直接跑续采相关的几个方法 ──
from pathlib import Path

import riskbird_gui as G

DESK = Path(folder("desk")) / "企业数据自动采集工具"
DESK.mkdir(parents=True, exist_ok=True)
_tasks.set_desktop_dir(str(DESK.parent))      # 任务索引也落在临时目录，不碰真桌面

SNAMES = ["A公司", "B公司", "C公司", "D公司", "E公司", "F公司"]


class FakeCrawler:
    def __init__(self):
        self.results = []


def make_gui(session=None):
    """绕过 PyQt5 构造：只装续采逻辑需要的字段，不创窗口、不启动浏览器"""
    win = G.MainWindow.__new__(G.MainWindow)
    win._companies = list(SNAMES)
    win._excel_paths = []
    win._companies_snapshot = []
    win._checkpoint = None
    win._cp_cursor = {}
    win._session_folder = Path(session) if session else None
    win._task_id = win._session_folder.name if session else ""
    win._by_key = {k: FakeCrawler() for k in G.MainWindow.SOURCE_KEYS}
    win.logs = []
    win._append_log = lambda m: win.logs.append(str(m))
    win._get_desktop_dir = lambda: DESK.parent
    # 没走 PyQt5 构造，刷新控件文本的方法一律拦掉（hasattr 在这种对象上会报 RuntimeError）
    win._update_platform_counts = lambda: None

    class FakeLabel:
        text = ""

        def setText(self, t):
            self.text = t

    win.company_count_lbl = FakeLabel()      # 状态行用假控件，照样能跑文本拼接
    return win


gui = make_gui()
sess = DESK / "2026-01-01_10-00-00"
sess.mkdir(parents=True, exist_ok=True)
cp11 = gui._ensure_checkpoint(sess)
assert cp11 is not None and os.path.exists(cp11.path), "开始采集就要建台账"
assert cp11.snapshot == SNAMES, cp11.snapshot
assert gui._todo_companies() == SNAMES, gui._todo_companies()
# 逐条落状态：done / no_result（永久）/ failed（重试）
for nm, st in (("A公司", CP.STATUS_DONE), ("B公司", CP.STATUS_NO_RESULT),
               ("C公司", CP.STATUS_FAILED)):
    cp11.mark(nm, st, "企知道")
cp11.mark_record(rec("A公司"), "企知道")
assert gui._todo_companies() == ["C公司", "D公司", "E公司", "F公司"], gui._todo_companies()
# 内存里已采到的（本轮切平台的情形）也不能再交给爬虫
gui._by_key["riskbird"].results.append(rec("D公司", source="风鸟"))
assert gui._todo_companies() == ["C公司", "E公司", "F公司"], gui._todo_companies()
# 双标签的待采不计风鸟的结果（风鸟不参与单双数分工）
assert "D公司" in gui._todo_companies(("qizhidao", "aiqicha")), gui._todo_companies(("qizhidao", "aiqicha"))
print("11 ✅ GUI 待采集合：名单 6 家 / 待采", gui._todo_companies())

# ── 12. 强杀后重启：自动发现断点 → 回读原数据 Excel → 待采 = 名单 − 已完成 − 无结果 ──
cp11.flush(force=True)
raw = os.path.join(str(sess), _tasks.RAW_FILE_NAME)
wb = Workbook()
ws = wb.active
ws.append(["序号", "公司名", "状态", "数据来源", "电话"])
for nm in ("A公司", "B公司", "D公司"):
    ws.append([1, nm, "存续", "企知道", "13800001111"])
ws.append([2, "E公司", "未找到", "爱企查", ""])
wb.save(raw)

boot = make_gui()                       # 相当于重启进程：内存里没有任何结果
found = boot._find_resumable()
assert len(found) == 1, f"应只发现一个未完成会话：{[f[0].name for f in found]}"
assert found[0][0].name == sess.name, found[0][0].name
boot._excel_paths = []                  # 没选文件时不比对指纹
boot._adopt_checkpoint(found[0][1], found[0][0], found[0][3])
assert boot._session_folder == sess and boot._task_id == sess.name, boot._session_folder
assert len(boot._by_key["qizhidao"].results) == 3, boot._by_key["qizhidao"].results
assert boot._todo_companies() == ["C公司", "F公司"], boot._todo_companies()
assert any("从断点继续" in m for m in boot.logs), boot.logs
# 确认无结果的公司不再请求（E 公司已回读成 no_result）
assert "E公司" not in boot._todo_companies(), boot._todo_companies()
boot._refresh_company_count()
assert boot.company_count_lbl.text.startswith("本次待采 2 家"), boot.company_count_lbl.text
gui._refresh_company_count()
print("12 ✅ 重启续采：待采", boot._todo_companies(), "/", boot._checkpoint.text_summary())

# 台账进度文件坏了也要能靠原数据 Excel 重建（GUI 不弹窗卡住）
broken = DESK / "2026-01-02_11-00-00"
broken.mkdir(parents=True, exist_ok=True)
shutil.copy(raw, os.path.join(str(broken), _tasks.RAW_FILE_NAME))
with open(os.path.join(str(broken), CP.CHECKPOINT_FILE_NAME), "w", encoding="utf-8") as fh:
    fh.write('{"version": 1, "snapshot": ["A公司"')
broke = make_gui(session=broken)
cp13, note = CP.CrawlCheckpoint.open_folder(broken, log=lambda m: None)
assert cp13 is None and "损坏" in note, note
rebuilt = broke._ensure_checkpoint(broken) if cp13 is None else cp13
# 损坏时 _ensure_checkpoint 会新建一份空台账，再由原数据 Excel 把已采的补回 done
broke._restore_results(CP.load_records_from_raw_excel(broken))
assert rebuilt.status_of("A公司") == CP.STATUS_DONE, rebuilt.status_of("A公司")
assert rebuilt.status_of("E公司") == CP.STATUS_NO_RESULT, rebuilt.status_of("E公司")
print("13 ✅ 损坏台账降级重建:", rebuilt.text_summary())

# ── 14. 重新开始：清进度文件 + 名单回到全量；快照追加新名单不乱奇偶归属 ──
boot._restart_session(sess)
assert not os.path.exists(os.path.join(str(sess), CP.CHECKPOINT_FILE_NAME))
assert boot._checkpoint is None and boot._companies_snapshot == []
boot._companies = list(SNAMES) + ["G公司"]
snap = boot._merge_snapshot()
assert snap == SNAMES + ["G公司"], snap
q_before, a_before = _dual.split_parity(SNAMES)
boot._companies_snapshot = snap
q_after, a_after = _dual.split_parity(snap)
for name in SNAMES:
    assert (name in q_after) == (name in q_before), ("追加名单后这家公司换了平台", name)
print("14 ✅ 重新开始与快照追加不影响平台归属")

# ── 15. 采完一条 just-in-time 落台账：_cp_mark_new 按游标增量扫 ──
cp15 = CP.CrawlCheckpoint.create("t15", folder("t15"), SNAMES, log=print)
gui15 = make_gui(session=folder("t15"))
gui15._checkpoint = cp15
gui15._companies_snapshot = list(SNAMES)
gui15._by_key["qizhidao"].results.append(rec("A公司"))
gui15._by_key["aiqicha"].results.append(rec("B公司", status="未找到", source="爱企查"))
gui15._cp_mark_new()
assert cp15.status_of("A公司") == CP.STATUS_DONE, cp15.status_of("A公司")
assert cp15.status_of("B公司") == CP.STATUS_NO_RESULT, cp15.status_of("B公司")
assert gui15._cp_cursor["qizhidao"] == 1 and gui15._cp_cursor["aiqicha"] == 1, gui15._cp_cursor
gui15._cp_mark_new()                     # 没新增时不能反复写盘
gui15._by_key["qizhidao"].results = []    # 清空结果：游标要退回，不能永久错位
gui15._cp_cursor["qizhidao"] = 5
gui15._cp_mark_new()
assert gui15._cp_cursor["qizhidao"] == 0, gui15._cp_cursor
gui15._cp_flush("测试")
assert any("采集进度已落盘" in m for m in gui15.logs), gui15.logs
print("15 ✅ 逐条写台账与游标退回")

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL ASSERTS PASSED")
