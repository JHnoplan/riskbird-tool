# 离线自测：自动扩词 + 关键词级续采 —— 2026-10-30 客户「填了关键词挂一天才几百条」
# 客户原话：关键词是「暖通空调与制冷设备、空气过滤与净化设备」，挂一天只采到几百条；
# 要求「自动扩大关键词（相关行业）」「暂停/停止后下次打开自动显示上次关键词和进度」
# 「做任务列表，选中历史任务→恢复进度→点开始采集即续采」。
# 取证结论：不是平台只给推荐位，而是①类别名不是站内搜索认的产品词（实测八方整句只出
# 20 家且混无关商家）②总量≈词数×每词池子③跨词/跨运行去重把重复扣掉→净新增个位数被
# 判「公司池枯竭」切词。所以本次改的是「扩词」和「可恢复的任务」两件事。
# 全程不联网、不启 Chrome：翻页/详情全部注入假实现，Qt 走 offscreen。
import io
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")   # 无头跑 Qt，不打扰用户桌面
sys.path.insert(0, "/Users/jhnoplan/Desktop/PythonDemo/b2bcollect_build")

import keyword_expander as kwexp
from parallel_session import ParallelSession, SITE_NAMES
from b2b168_crawler import B2b168Crawler
import task_registry as _tasks

ok = 0


def check(name, cond):
    global ok
    assert cond, f"FAIL: {name}"
    ok += 1
    print(f"  ✓ {name}")


CUSTOMER_KW = "暖通空调与制冷设备、空气过滤与净化设备"

# ── A. 自动扩词：类别名 → 产品词 ────────────────────────────────
print("A. 关键词展开（客户那两个词）")
rep = kwexp.expand_keywords(CUSTOMER_KW)
kws = rep["keywords"]
check("两个类别名展开成一批产品词（≥60 个）", len(kws) >= 60)
check("展开出同产业链高产词（工业冷水机/制冷压缩机/冷凝器）",
      {"工业冷水机", "制冷压缩机", "冷凝器"} <= set(kws))
check("净化侧展开出高效过滤器/层流罩", {"高效过滤器", "层流罩"} <= set(kws))
check("知识库已覆盖 40+ 个行业类别", len(kwexp.KNOWLEDGE_BASE) >= 40)
check("展开报告带明细可核对（expanded/kept/dropped 三段）",
      bool(rep["expanded"]) and isinstance(rep["kept"], list) and isinstance(rep["dropped"], list))
# 幂等：展开结果再展开一次不该无限膨胀（否则用户多点几次词表爆炸）
rep2 = kwexp.expand_keywords("\n".join(kws))
rep3b = kwexp.expand_keywords("\n".join(rep2["keywords"]))
check("二次展开有界收敛（第二轮最多加几十个，第三轮不再变）",
      len(rep2["keywords"]) <= len(kws) + 20 and len(rep3b["keywords"]) == len(rep2["keywords"]))
check("词表有单次上限配置", kwexp.MAX_KEYWORDS == 200 and rep["truncated"] is False)
# 纯服务/流程词该忽略（站内没有对应商品，采了也是空）
rep3 = kwexp.expand_keywords("系统解决方案\n招投标\n冷链物流配送\n工业冷水机")
check("纯服务/流程词被忽略、产品词保留",
      "工业冷水机" in rep3["keywords"] and "系统解决方案" not in rep3["keywords"]
      and len(rep3["dropped"]) >= 2)

# ── B. 高产词同族派生 ───────────────────────────────────────────
print("B. 高产词同族近义词派生")
sib = kwexp.derive_siblings("工业冷水机")
check("工业冷水机派生出同族完整词（螺杆式/风冷式/水冷式…）",
      len(sib) >= 3 and any("冷水机" in w for w in sib))
check("派生数量有上限（不一次灌几十个词）", len(sib) <= kwexp.SIBLING_CAP)
res = kwexp.boost_high_yield(["工业冷水机", "新风机", "某种窄词"],
                             {"工业冷水机": 723, "新风机": 3, "某种窄词": 0})
check("只有高产词才派生（723 家的派生、3 家/0 家的不派生）",
      len(res["added"]) == 1 and res["added"][0][0] == "工业冷水机")
check("派生词追加在词表末尾且不与原词重复",
      res["keywords"][:3] == ["工业冷水机", "新风机", "某种窄词"]
      and len(set(res["keywords"])) == len(res["keywords"]))
check("已在词表里的同族词不重复派生",
      "螺杆式冷水机" not in kwexp.derive_siblings("工业冷水机", {"螺杆式冷水机"}))

# ── C. 爬虫逐词台账 + 按游标跳过（假详情，绝不联网）───────────────
print("C. 爬虫每词台账与续采跳过")


def _wire_fake(crawler, sink, stop_at=None):
    """把翻页/详情换成假实现：每词固定 2 家；stop_at 命中的词在第 1 家后置停止位。"""
    crawler.incremental = False
    crawler.max_pages = 0
    crawler._is_session_alive = lambda: True
    crawler._sleep = lambda: None

    def _iter_keyword(kw):
        for i in (0, 1):
            name = f"{kw}有限公司{i}"
            # 模拟增量种子库命中即跳过（真实链路就是这个效果：被打断的词重跑时，
            # 上次已采到的那家不会重复入库，净新增只算新出现的那家）
            if any(r.get("公司名称") == name for r in crawler.results):
                continue
            yield {"关键词": kw, "公司名称": name,
                   "公司网址": f"http://example.com/{kw}/{i}",
                   "详情URL": f"http://example.com/{kw}/{i}.html",
                   "memberId": f"{kw}-{i}"}

    def _detail(stub):
        kw = stub["关键词"]
        sink.append(kw)
        rec = crawler._base_rec(stub)
        rec["状态"] = "成功"
        rec["手机"] = "13800000000"
        if stop_at and kw == stop_at and sink.count(kw) == 1:
            crawler._stop_requested = True      # 模拟用户点了「停止」
        return rec

    crawler._iter_keyword = _iter_keyword
    crawler.crawl_detail = _detail
    return crawler


calls1 = []
c1 = _wire_fake(B2b168Crawler(), calls1, stop_at="B词")
c1.crawl(["A词", "B词", "C词"])
check("跑完的词进游标、被中途打断的词不进（A 进、B/C 不进）", c1._kw_done == {"A词"})
check("每词净新增按关键词记账", c1.kw_yield.get("A词") == 2 and c1.kw_yield.get("B词") == 1)
snap1 = c1.progress_snapshot()
check("进度快照含 yield 与 done 两段",
      set(snap1) >= {"yield", "done"} and snap1["done"] == ["A词"])

# 续采：新实例（等价于重启工具）→ 恢复游标 + 装回上次数据 → 只跑没跑完的
calls2 = []
c2 = _wire_fake(B2b168Crawler(), calls2)
c2.seed_results(c1.results)          # 装回上次已采（不装就会被自动保存盖掉）
check("未恢复进度前 resume=False（全新任务）", c2.resume is False)
c2.restore_progress(snap1)
check("恢复进度即置 resume=True（crawl 不再清台账）", c2.resume is True)
check("恢复后已跑完游标与每词账目都在", c2._kw_done == {"A词"} and c2.kw_yield["A词"] == 2)
c2.crawl(["A词", "B词", "C词"])
check("续采跳过已跑完的 A 词（只请求 B/C，被打断的 B 重来）",
      calls2 == ["B词", "C词", "C词"])
check("续采后三个词全部跑完", c2._kw_done == {"A词", "B词", "C词"})
check("续采保留上轮账目（A 词仍是 2 家，不清零也不翻倍）", c2.kw_yield["A词"] == 2)
check("被打断的 B 词重跑只补新增那家（1+1，不是 +2）", c2.kw_yield["B词"] == 2)
check("累计净新增 = 6 家（与实采条数一致）", sum(c2.kw_yield.values()) == 6)
check("续采后内存共 6 家（老 3 家 + 新 3 家）", len(c2.results) == 6)

# 种子库续采：把已采记录装回内存（不装就会被自动保存全量覆写时盖掉）
c3 = _wire_fake(B2b168Crawler(), [])
rows = [{"关键词": "A词", "公司名称": "老数据公司", "数据来源": SITE_NAMES["b2b168"],
         "手机": "13900000000", "状态": "成功"}]
check("seed_results 返回装回条数", c3.seed_results(rows) == 1)
check("装回的记录进内存 results", any(r["公司名称"] == "老数据公司" for r in c3.results))


class StubNoLedger:
    """没有逐词台账的通道（1688/企知道适配器就是这类）：靠会话层过滤词表续采。"""
    PLATFORM_SLUG = "aipage"
    PLATFORM_NAME = SITE_NAMES["aipage"]
    signals = None

    def __init__(self):
        self.results = []
        self._stop_requested = False
        self.crawled = None

    def _log(self, msg):
        pass

    def crawl(self, keywords, start_index=0):
        self.crawled = list(keywords)


# ── D. 会话台账 / 进度文件 / 续采过滤 ───────────────────────────
print("D. 会话进度台账与续采过滤")
pf = Path(tempfile.mkdtemp(prefix="kw_resume_")) / "20261030_120000"
s = ParallelSession()
stub = s.crawlers["aipage"] = StubNoLedger()
s.set_enabled(["b2b168", "aipage"])
s.set_task_folder(str(pf))
s._last_keywords = ["A词", "B词", "C词"]
s._kw_ledger = {"b2b168": {"yield": {"A词": 2}, "done": ["A词"]}}

check("绑定任务文件夹后进度文件与数据同层",
      s.progress_path == str(pf / _tasks.PROGRESS_FILENAME))
check("有台账的通道按自己（爬虫）的游标过滤词表",
      s.crawlers["b2b168"].restore_progress({"yield": {"A词": 2}, "done": ["A词"]})
      and s._pending_keywords(s.crawlers["b2b168"], ["A词", "B词"]) == ["B词"])
check("爬虫台账能同回会话台账（落盘前不再靠手改）",
      "A词" in s.snapshot_site_progress()["b2b168"]["done"])
check("无台账通道按会话账目过滤",
      s._pending_keywords(stub, ["A词", "B词", "C词"]) == ["A词", "B词", "C词"])
s._kw_ledger["aipage"] = {"yield": {}, "done": ["A词", "B词"]}
check("无台账通道续采只跑剩下的词",
      s._pending_keywords(stub, ["A词", "B词", "C词"]) == ["C词"])
s._merge_site_progress(stub, ["C词"])
check("无台账通道跑完后按子集推 done", "C词" in s._kw_ledger["aipage"]["done"])
stub.results = [{"关键词": "C词", "公司名称": "X"}]
s._merge_site_progress(stub, ["C词"])
check("无台账通道每词条数按 results 回算", s._kw_ledger["aipage"]["yield"].get("C词") == 1)

check("save_progress 原子写盘成功", s.save_progress() is True)
check("无 .tmp 残留", not (pf / (_tasks.PROGRESS_FILENAME + ".tmp")).exists())
data = json.loads((pf / _tasks.PROGRESS_FILENAME).read_text(encoding="utf-8"))
check("进度文件含 updated_at/keywords/sites/ledger 四段",
      {"updated_at", "keywords", "sites", "ledger"} <= set(data)
      and data["keywords"] == ["A词", "B词", "C词"] and "aipage" in data["ledger"])

# 坏文件降级：进度 JSON 被截断时必须读回 None（当全新任务），不能抛异常带崩启动
with io.open(pf / _tasks.PROGRESS_FILENAME, "w", encoding="utf-8") as f:
    f.write('{"ledger": {"b2b168"')
check("进度 JSON 损坏时读回 None 不抛异常", ParallelSession().load_progress(str(pf)) is None)
with io.open(pf / _tasks.PROGRESS_FILENAME, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False)

s2 = ParallelSession()
s2.crawlers["aipage"] = StubNoLedger()
got = s2.load_progress(str(pf))
check("新会话读回上次的台账", bool(got) and s2._kw_ledger["b2b168"]["done"] == ["A词"])
applied = s2.apply_progress()
check("apply_progress 把游标装回有台账的通道",
      s2.crawlers["b2b168"].resume is True and s2.crawlers["b2b168"]._kw_done == {"A词"})
check("apply_progress 返回生效通道数（无台账通道不算）", applied == 1)
s2._kw_ledger = {"b2b168": {"yield": {"工业冷水机": 723, "冷凝器": 556}, "done": []}}
tops = s2.top_keywords(2)
check("产出榜跨站合并并按净新增从多到少排",
      tops[0][0] == "工业冷水机" and tops[1][0] == "冷凝器")
check("台账累计净新增可读", s2.total_yield() == 1279)
s2.reset_results()
check("「开始新任务」连逐词游标账目一起清（否则老关键词会被当已采跳过）",
      s2._kw_ledger == {} and s2.progress_path == "")

# ── E. 任务索引：配置快照 + 进度对账 ───────────────────────────
print("E. 任务索引与进度对账")
desk = Path(tempfile.mkdtemp(prefix="kw_resume_desk_"))
_tasks.set_desktop_dir(desk)
base = desk / "B2B三站企业数据自动采集工具"
base.mkdir(parents=True, exist_ok=True)
tid = "20261030_120000"
folder = base / tid
folder.mkdir(parents=True, exist_ok=True)
with io.open(folder / _tasks.PROGRESS_FILENAME, "w", encoding="utf-8") as f:
    json.dump({"updated_at": "2026-10-30 12:30:00", "keywords": ["A词", "B词", "C词"],
               "sites": ["b2b168"], "ledger": {"b2b168": {"yield": {"A词": 2}, "done": ["A词"]}}},
              f, ensure_ascii=False)
task = _tasks.new_task(tid, ["制冷.txt"], str(folder), count=6,
                       keywords=["A词", "B词", "C词"], kw_raw=CUSTOMER_KW,
                       sites=["b2b168", "aipage"],
                       params={"delay_between": 5.0, "incremental": True})
_tasks.upsert_task(task)
back = next(t for t in _tasks.load_tasks() if t.get("id") == tid)
check("任务里存下了关键词/原始输入/站点/参数（关掉工具不丢）",
      back["keywords"] == ["A词", "B词", "C词"] and back["kw_raw"] == CUSTOMER_KW
      and back["sites"] == ["b2b168", "aipage"] and back["params"]["delay_between"] == 5.0)
check("已暂停属于可续采状态", _tasks.TASK_STATUS_PAUSED in _tasks.RESUMABLE_STATUS)
check("能读回「跑到哪儿」（已跑完词数 + 累计条数）",
      _tasks.done_keywords(back) == 1 and _tasks.task_yield_total(back) == 2)
# 这条老任务标成已完成，避免后面的「启动自动回填」把它和 F 里的新任务搞混
_tasks.finish_task(tid, _tasks.TASK_STATUS_DONE, 6)

# ── F. 界面：自动扩词 / 落盘 / 启动回填 / 历史任务续采 ──────────
print("F. 界面接线（offscreen 真 MainWindow）")
import b2bcollect_gui as gui
from PyQt5.QtWidgets import QApplication, QDialog, QListWidget
from PyQt5.QtCore import Qt

app = QApplication.instance() or QApplication(sys.argv)   # 先有 QApplication 才能造控件
gui.MainWindow._get_desktop_dir = lambda self: desk
w = gui.MainWindow()
check("多了「历史任务·接着上次采」按钮", hasattr(w, "btn_history"))
check("多了「自动扩词」开关且默认开", w.chk_auto_kw.isChecked())
w.kw_text.setPlainText(CUSTOMER_KW)
w._on_parse()
before = len(w.keywords)
w._auto_expand_on_start()
check("点开始采集前自动把类别名换成产品词",
      len(w.keywords) > before + 30 and "工业冷水机" in w.keywords)
check("展开后的词写回输入框（用户看得见、能删改）",
      "工业冷水机" in w.kw_text.toPlainText()
      and "暖通空调与制冷设备" not in w.kw_text.toPlainText())
check("原始输入留了底（任务里能回溯用户本来填什么）", w._kw_raw == CUSTOMER_KW)
n2 = len(w.keywords)
w._auto_expand_on_start()
check("再点一次开始采集不会把词表越扩越长（已展开的内容认得出来）", len(w.keywords) == n2)
w.chk_auto_kw.setChecked(False)
w.kw_text.setPlainText(CUSTOMER_KW)
w._on_parse()
w._auto_expand_on_start()
check("取消勾选就完全不换词", w.keywords == ["暖通空调与制冷设备", "空气过滤与净化设备"])
w.chk_auto_kw.setChecked(True)

# 模拟一次采集后的落盘：绑定文件夹 → 台账 → 持久化
sess_folder = base / "20261030_130000"
sess_folder.mkdir(parents=True, exist_ok=True)
w.session_dir = str(sess_folder)
w._first_start = "20261030_130000"
w._kw_raw = CUSTOMER_KW
w.keywords = ["A词", "B词", "C词"]
w.kw_text.setPlainText("\n".join(w.keywords))
w.site_checks["b2b168"].setChecked(True)
for _slug, _chk in w.site_checks.items():      # 只留八方，验证站点快照能存能恢复
    _chk.setChecked(_slug == "b2b168")
w.session.set_enabled(["b2b168"])              # 等价于 _on_start 里那行 set_enabled
w.spin_delay.setValue(8.0)
w.session.set_task_folder(str(sess_folder))
# 台账由爬虫自己记（会话落盘前会从各爬虫同步，手改会话台账会被盖回）
w.session.crawlers["b2b168"].restore_progress({"yield": {"A词": 3}, "done": ["A词"]})
w._persist_progress(_tasks.TASK_STATUS_STOPPED)
check("停止/退出前把进度写进任务文件夹", (sess_folder / _tasks.PROGRESS_FILENAME).exists())
saved = next(t for t in _tasks.load_tasks() if t.get("id") == "20261030_130000")
check("任务索引记下关键词与参数，状态停在「已停止」",
      saved["keywords"] == ["A词", "B词", "C词"]
      and saved["status"] == _tasks.TASK_STATUS_STOPPED
      and saved["params"]["delay_between"] == 8.0
      and saved["sites"] == ["b2b168"])

# 上次数据装成 Excel：续采必须把它装回内存，否则自动保存全量覆写会盖掉
from openpyxl import Workbook

wb = Workbook()
ws = wb.active
ws.append(["关键词", "公司名称", "手机", "数据来源", "状态"])
ws.append(["A词", "上次采到的公司", "13700000000", SITE_NAMES["b2b168"], "成功"])
ws.append(["B词", "上次采到的公司2", "13700000001", SITE_NAMES["b2b168"], "成功"])
wb.save(str(sess_folder / _tasks.RAW_FILE_NAME))

w.site_checks["11467"].setChecked(True)        # 故意改乱，验证恢复会照上次设回去
ok_restore = w._restore_task(saved)
check("恢复任务成功", ok_restore is True)
check("关键词框回填成上次的词", w.kw_text.toPlainText().splitlines() == ["A词", "B词", "C词"])
check("平台勾选按上次恢复", w.site_checks["b2b168"].isChecked()
      and not w.site_checks["11467"].isChecked())
check("数据仍指向同一个任务文件夹", w.session_dir == str(sess_folder))
check("游标接回爬虫（A 词已跑完）",
      w.session.crawlers["b2b168"]._kw_done == {"A词"}
      and w.session.crawlers["b2b168"].resume is True)
check("上次已采的 2 条装回内存（自动保存不会盖掉它们）",
      any(r.get("公司名称") == "上次采到的公司" for r in w.session.snapshot_results()))
# 高产词派生：把台账换成真实产出，验证只给高产词派生同族词
w.keywords = ["工业冷水机", "新风机"]
w.session._kw_ledger = {"b2b168": {"yield": {"工业冷水机": 723, "新风机": 3}, "done": []}}
w._boost_high_yield()
check("结束后高产词派生的同族词进了关键词框（等用户点头再采）",
      "螺杆式冷水机" in w.keywords and "新风机" in w.keywords
      and "螺杆式冷水机" in w.kw_text.toPlainText())

# 再开一个窗口：启动自动回填上次任务（客户要的「下次打开自动显示上次的关键词和进度」）
w2 = gui.MainWindow()
w2._restore_last_task()
check("重启工具自动显示上次的关键词",
      w2.kw_text.toPlainText().splitlines()[:3] == ["A词", "B词", "C词"])
check("重启工具自动接上次的进度与数据",
      w2.session.crawlers["b2b168"]._kw_done == {"A词"}
      and any(r.get("公司名称") == "上次采到的公司" for r in w2.session.snapshot_results()))

# 历史任务弹窗：列出全部任务，选中一条 → 接着采

_exec_orig = QDialog.exec_
picked = {}


def _fake_exec(self):
    lw = self.findChild(QListWidget)
    picked["count"] = lw.count()
    for i in range(lw.count()):
        it = lw.item(i)
        if (it.data(Qt.UserRole) or {}).get("id") == "20261030_130000":
            lw.setCurrentItem(it)
    return QDialog.Accepted


QDialog.exec_ = _fake_exec
try:
    w2._on_history_tasks()
finally:
    QDialog.exec_ = _exec_orig
check("历史任务列表把以前的任务都列出来了", picked.get("count", 0) >= 2)
check("选中历史任务后现场恢复（关键词/游标/数据都在）",
      w2.keywords[:3] == ["A词", "B词", "C词"] and w2.session_dir == str(sess_folder))

# 复位后不该还留着上一任务的游标（防「点开始新任务却什么都没采」）
w2.session.reset_results()
check("「开始新任务」后不再有上一任务的跳过位",
      w2.session._kw_ledger == {} and w2.session.crawlers["b2b168"].resume is False)

print(f"\n全部通过：{ok} 项断言")
