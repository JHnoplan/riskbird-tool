# -*- coding: utf-8 -*-
"""test_gui_store_offline.py —— GUI 检索链路（pool_dialogs）接 Gold 层的离线回归。

为什么单开一份：pool_dialogs 里的搜索/筛选/概览/同步是**用户真正点到的地方**。
逻辑正确但接错线（视图没就绪就去查、统计跨口径混用、上传数字按提交数报）
在纯逻辑回归里一律看不出来。这里用 offscreen Qt 把对话框真跑起来，
断言的是「界面显示的那句话、那张表、那个数字」是否与底层一致。

覆盖：
  1. LoadMasterWorker：加载完就把物化视图对齐好，并如实播报就绪/回退；另把
     「两万条以上才触发的进度回调」压小常量真走一遍（旧接法会让整段重建回滚）
  2. SearchDialog：搜索走视图下压（分区 + 筛选），文案里的「粗筛数 / 当前范围数 /
     返回数」三个数各自等于库内 count，混用即骗人
  3. 地区下拉：选项来自视图 province/country 列，不再是 UI 线程全表扫地址
  4. 筛选：视图可用时重新下压检索（不是「页内挑一遍」），回退时筛选仍生效
  5. 概览：视图口径与本页口径分别标注，绝不把本页 100 家与池内 N 家的数字混着显示
  6. SyncDialog：入库前清洗报告可见；上传成功数按清洗后 kept 报，不按提交数报
全程临时目录 + 假 OSS + 空审计日志，不联网、不写 ~/Desktop/数据池缓存。
"""
import json
import os
import re
import shutil
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

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


def _assert_raises(fn, label):
    """断言某段接法必然抛异常：用于把「测试前提」本身也验一遍。

    前提不成立时，主断言就成了永远为真的空断言（例如旧接法其实没报错，
    那「改成新接法」这条回归就什么都没证明）。
    """
    try:
        fn()
    except Exception:
        _assert(True, label)
        return
    _assert(False, f"{label}  (实际没有抛异常，前提不成立)")


def _new_tmp_dir():
    d = tempfile.mkdtemp(prefix="gui_store_test_")
    _tmp_roots.append(d)
    return d


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtWidgets import QApplication

import pool_dialogs
import pool_store
from data_pool import DataPoolManager, record_region
from ingest_cleaning import clean_records

app = QApplication.instance() or QApplication(sys.argv[:1])


NUM_RE = re.compile(r"(\d+)")
# 单位随场景变（搜索报「条」、推荐报「家」），正则不锁死单位才能两处共用同一套断言
_RECALL_RE = re.compile(r"关键词先粗筛出 (\d+) [条家]")
_SCOPE_RE = re.compile(r"当前范围共 (\d+) [条家]")


def _note_numbers(msg: str):
    """从结果文案里取出 (粗筛数, 当前范围数)，缺失的一项为 None。

    这两个数在百万级里本来就该不同（粗筛宽于精排、范围与关键词无关），
    文案只在两者相等时合并成一个数——那是去重，不是漏报。
    """
    k = _RECALL_RE.search(msg or "")
    t = _SCOPE_RE.search(msg or "")
    return (int(k.group(1)) if k else None, int(t.group(1)) if t else None)


def _assert_note(msg, matched, scope_total, label):
    """断言文案里的粗筛数/范围数与库内 count 一致（含相等时合并报数的规则）。"""
    k, t = _note_numbers(msg)
    if matched == scope_total:
        _assert(k is None and t == matched,
                f"{label}：粗筛数与范围数相等时只报一次  (粗筛={matched}, "
                f"范围={scope_total}, 实际={msg!r})")
    else:
        _assert(k == matched and t == scope_total,
                f"{label}：粗筛 {matched} 与当前范围 {scope_total} 各自对得上库内 "
                f"count  (实际={msg!r})")
    _assert(matched <= scope_total,
            f"{label}：关键词粗筛数不可能超过当前范围总数  "
            f"({matched} <= {scope_total})")


def _drain(seconds: float = 5.0, until=None):
    """跑事件循环等后台线程；until 为真即提前返回。"""
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.005)
    return until is None or bool(until())


def _rec(company, **kw):
    rec = {
        "company": company, "contacts": [], "mobiles": [], "landlines": [],
        "phones": [], "emails": [], "wechats": [], "address": "",
        "products": "", "website": "", "source": "测试来源.xlsx",
        "source_time": "2026-11-05T10:00:00", "source_user": "tester",
    }
    rec.update(kw)
    return rec


class FakeOSS:
    """内存版 OSS：只实现数据池用到的四个方法。"""

    def __init__(self):
        self.objects = {}

    def upload_json_atomic(self, key, payload, progress_callback=None):
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


class NoopAudit:
    def log(self, *a, **k):
        pass


class _BrokenStore:
    """视图彻底不可用（FTS5 缺失 / 目录只读 / 建库失败）的形态。"""

    def usable(self):
        return False

    def readable(self):
        return False

    def is_current(self, fp):
        return False

    def rebuild(self, records, fp, progress_cb=None):
        return False


# ── 测试语料：30 浙江光伏 + 30 广东储能 + 40 国外 solar ──────────────────────
oss = FakeOSS()
_dom = ([_rec(f"宁波光伏公司{i}有限公司", products="光伏逆变器 储能电池",
              address="浙江省宁波市", mobiles=[f"1380000{i:04d}"],
              source="测试光伏展.xlsx", source_user="alice") for i in range(30)]
        + [_rec(f"深圳储能公司{i}有限公司", products="锂电池 储能系统",
                address="广东省深圳市", emails=[f"c{i}@cn.com"],
                source="测试电池展.xlsx", source_user="alice") for i in range(30)])
_intl = [_rec(f"Solar Mounting Co {i} Ltd", products="solar racking",
              country="Spain", address="Madrid", emails=[f"b{i}@x.com"],
              source="测试海外站.xlsx", source_user="bob") for i in range(40)]
oss.objects["users/alice/upload_1.json"] = {"username": "alice", "records": _dom}
oss.objects["users/bob/upload_2.json"] = {"username": "bob", "records": _intl}

mgr = DataPoolManager(oss_client=oss, cache_dir=_new_tmp_dir())
MASTER = mgr.get_merged_master()
assert len(MASTER) == 100, len(MASTER)

# 真实审计日志一律换成空实现（回归不写用户机器上的日志）
pool_dialogs.get_audit_logger = lambda: NoopAudit()


# ════════════════════════════════════════════════════════════════════════════
print("\n═══ 1. LoadMasterWorker：加载即把视图对齐，并如实播报 ═══")

_msgs = []
_done = {}
w = pool_dialogs.LoadMasterWorker(force_refresh=True, mgr=mgr)
w.progress.connect(_msgs.append)
w.finished.connect(lambda ok, m, msg: _done.update(ok=ok, master=m, msg=msg))
w.run()                    # 同线程直接 run，不起真线程（去掉事件循环依赖）
_assert(_done.get("ok") is True, "加载成功")
_assert_eq(len(_done.get("master") or []), 100, "总表条数")
_assert(mgr.store_ready(), "加载完成后物化视图已对齐（搜索路径不必再等构建）")
_assert("搜索加速已就绪" in _done.get("msg", ""),
        f"就绪状态如实写进用户可见文案  (实际={_done.get('msg', '')!r})")
_assert(any("整理数据" in m for m in _msgs), "加载过程播报过加速数据准备阶段")

# 视图建不起来 → 文案必须改成「回退」，不能一边回退一边报「就绪」
mgr_broken = DataPoolManager(oss_client=oss, cache_dir=_new_tmp_dir())
mgr_broken.get_merged_master()
mgr_broken._store = _BrokenStore()
mgr_broken._store_off = False
_done2 = {}
w2 = pool_dialogs.LoadMasterWorker(force_refresh=True, mgr=mgr_broken)
w2.finished.connect(lambda ok, m, msg: _done2.update(ok=ok, msg=msg))
w2.run()
_assert(not mgr_broken.store_ready(), "视图不可用时 store_ready 为假")
_assert("较慢的方式" in _done2.get("msg", "")
        and "搜索加速已就绪" not in _done2.get("msg", ""),
        f"视图不可用时文案说明改用慢办法，不谎报就绪  (实际={_done2.get('msg', '')!r})")


# ── 大池子才会走到的进度回调（真实缺陷回归：8.6 万条池首次重建直接回滚）──────
# rebuild 的回调契约是 progress_cb(done, total) 两个整数，而 progress 是
# pyqtSignal(str)：把 signal.emit 当回调传进去，回调一响就 TypeError，异常发生在
# 重建事务里 → 整段回滚 → 视图永远建不起来，搜索静默走慢路径。
# 更麻烦的是这条只在「条数 ≥ _PROGRESS_MIN_RECORDS 且攒满一批」时才触发，
# 默认门槛两万条、默认批次两千条，小语料回归永远踩不到——所以这里把两个常量
# 临时压小，把这条路径真的走一遍。
_assert_raises(lambda: w.progress.emit(1, 2),
               "前提成立：progress 是 str 信号，收到两个 int 必抛（旧接法就是错的）")
_batch_bak, _min_bak = pool_store._BATCH, pool_store._PROGRESS_MIN_RECORDS
try:
    pool_store._BATCH = 5
    pool_store._PROGRESS_MIN_RECORDS = 0
    _mgr_big = DataPoolManager(oss_client=oss, cache_dir=_new_tmp_dir())
    _done3 = {}
    _msgs3 = []
    w4 = pool_dialogs.LoadMasterWorker(force_refresh=True, mgr=_mgr_big)
    w4.progress.connect(_msgs3.append)
    w4.finished.connect(lambda ok, m, msg: _done3.update(ok=ok, msg=msg))
    _raised = None
    try:
        w4.run()
    except Exception as e:                       # run() 内部应吞掉并如实报失败
        _raised = e
    _assert(_raised is None, f"两万条以上路径不抛异常  ({_raised!r})")
    _assert(_done3.get("ok") is True, "加载照样成功收尾")
    _assert(_mgr_big.store_ready(),
            "回调触发后视图仍然建成（旧接法在这里必然失败：整段重建被回滚）")
    _pcts = [m for m in _msgs3 if "已整理" in m and "%" in m]
    _assert(len(_pcts) >= 2, f"重建过程按批次播报进度  (实际={_msgs3[-3:]!r})")
    _assert(_pcts and _pcts[-1].endswith("（100%）"),
            f"最后一条进度到 100%  (实际={_pcts[-1:]!r})")
    _assert_eq(int(_mgr_big.store().meta().get("record_count", "-1")), 100,
               "带回调的重建写入条数与总表一致（没有被回滚）")
    _assert_eq(_mgr_big.store().count(""), 100,
               "带回调重建后视图行数=总表条数（数据真的提交了）")
    _assert_eq(_mgr_big.store().count("光伏"), mgr.store_count("光伏"),
               "带回调与不带回调两条重建路径，同一查询命中完全一致")
finally:
    pool_store._BATCH, pool_store._PROGRESS_MIN_RECORDS = _batch_bak, _min_bak


# ════════════════════════════════════════════════════════════════════════════
print("\n═══ 2. SearchDialog：搜索下压到视图，数字与结果同源 ═══")

pool_dialogs.DataPoolManager = lambda *a, **k: mgr


class _StubLoadWorker(QObject):
    """对话框构造时的加载线程换成「立刻返回已备好的总表」，避免真联网。"""
    progress = pyqtSignal(str)
    finished = pyqtSignal(bool, list, str)
    refreshed = pyqtSignal(list, str)

    def __init__(self, force_refresh=False, mgr=None):
        super().__init__()
        self.mgr = mgr

    def start(self):
        self.finished.emit(True, MASTER, f"总表 {len(MASTER)} 家公司")


pool_dialogs.LoadMasterWorker = _StubLoadWorker

dlg = pool_dialogs.SearchDialog()
_drain(0.2)
_assert_eq(len(dlg._master), 100, "对话框拿到总表")
_assert(dlg._pool_mgr is mgr, "对话框与总表共用同一个管理器（视图/索引同源）")


def _run_search(dlg, query, use_deepseek=False):
    """点搜索 → 等后台线程收尾 → 返回 (results, message)。

    信号跨线程是「排队投递」，必须等事件循环才送达；所以 _do_search 返回后
    立刻 connect 是安全的（这中间我们没有 processEvents）。
    """
    box = {}
    dlg._do_search(query, use_deepseek=use_deepseek)
    dlg._search_worker.finished.connect(
        lambda ok, res, msg: box.update(ok=ok, res=res, msg=msg))
    ok = _drain(20.0, until=lambda: bool(box))
    return box.get("res") or [], box.get("msg", ""), ok


_results, _msg, _ok = _run_search(dlg, "光伏")
_assert(_ok and dlg._search_worker.isFinished(), f"搜索线程正常收尾  ({_msg})")
assert _results, "「光伏」在国内应有命中"
_assert(all(record_region(r) == "dom" for r in _results),
        "勾选国内后结果全是国内（分区裁剪在召回阶段就下压）")
_matched = mgr.store_count("光伏", ["dom"], {})
_scope_total = mgr.store_count("", ["dom"], {})
_assert_note(_msg, _matched, _scope_total, "界面文案")
_assert(_matched < _scope_total,
        f"本语料里召回数与池内范围数确实不同（召回 {_matched} ＜ 范围 {_scope_total}）"
        f"，才看得出旧文案把两个数当成一个")
_assert(f"池内共 {_matched} 条" not in _msg,
        f"老缺陷指纹不复现：召回数绝不再被显示成「池内共 N 条」  (实际={_msg!r})")
_assert(dlg.table.rowCount() == len(_results),
        f"表格行数与结果条数一致  ({dlg.table.rowCount()} vs {len(_results)})")

# 真实缺陷回归（8.6 万条真实池实测）：搜一整家公司名时关键词粗筛会有上万条命中，
# 旧文案把那个数写成「池内共 18153 条」，用户会以为池里有一万家同样的公司。
# 本语料只有 60 家国内，公司名粗筛恰好等于整个范围，所以这里验的是「两个数相等时
# 只报一次」；粗筛严格宽于返回数的场景用「最多显示」压出来（见下一段）。
_exact = _results[0]["company"]
_ex_res, _ex_msg, _ex_ok = _run_search(dlg, _exact)
_ex_matched = mgr.store_count(_exact, ["dom"], {})
_ex_scope = mgr.store_count("", ["dom"], {})
_assert(_ex_ok, f"按公司名搜索正常收尾  ({_ex_msg})")
_assert_note(_ex_msg, _ex_matched, _ex_scope, "整家公司名的文案")
_assert(len(_ex_res) <= _ex_matched,
        f"精排返回的每家都必须来自粗筛命中的那一批："
        f"{len(_ex_res)} ≤ {_ex_matched}")
_assert(any(r.get("company") == _exact for r in _ex_res),
        "按公司名搜索时目标公司必须出现在结果里")

# 第三个数（这次给你看多少）：把「最多显示」调到 10，粗筛 60 家只返回 10 家时
# 必须补一句「按匹配度取前 N 条」——只报粗筛数会让用户以为相关的就那么多，
# 只报返回数则看不出范围有多大
dlg.spin_max_results.setValue(10)
_cut_res, _cut_msg, _cut_ok = _run_search(dlg, _exact)
_assert(_cut_ok, f"限制返回条数后搜索正常收尾  ({_cut_msg})")
_assert(0 < len(_cut_res) <= 10,
        f"「最多显示」在视图路径同样生效  (实际返回 {len(_cut_res)} 条)")
_assert(f"按匹配度取前 {len(_cut_res)} 条" in _cut_msg,
        f"返回数小于粗筛数时必须说明只取了前 N 条  (实际={_cut_msg!r})")
_assert(f"池内共 {_ex_matched} 条" not in _cut_msg,
        f"限制返回后老缺陷指纹仍不复现  (实际={_cut_msg!r})")
dlg.spin_max_results.setValue(100)

# 封顶计数（百万级做法，对标 Elasticsearch track_total_hits）：界面那两个总数
# 只要数到上限就停，超过上限必须说成「N 条以上」——把封顶值当精确值报出去
# 就是报假数。这里把上限压到 10（本语料国内 60 条、关键词命中 30 条都超它），
# 专门走一遍「两个数都封顶」的文案分支。
_cap_bak = pool_store.COUNT_CAP
try:
    pool_store.COUNT_CAP = 10
    _cap_res, _cap_msg, _cap_ok = _run_search(dlg, "光伏")
    _assert(_cap_ok and bool(_cap_res), f"封顶计数下搜索照常出结果  ({_cap_msg})")
    _assert("关键词先粗筛出 10 条以上" in _cap_msg and
            "当前范围共 10 条以上" in _cap_msg,
            f"两个总数都到上限时都改说「以上」  (实际={_cap_msg!r})")
    _assert(f"关键词先粗筛出 {_matched} 条）" not in _cap_msg and
            f"当前范围共 {_scope_total} 条）" not in _cap_msg,
            f"封顶后不许把没封顶的精确值 {_matched}/{_scope_total} 报出去  "
            f"(实际={_cap_msg!r})")
    _c10 = pool_dialogs._counts_of(mgr, "光伏", ["dom"], {})
    _assert_eq((_c10.matched, _c10.scope_total, _c10.matched_capped,
                _c10.scope_capped), (10, 10, True, True),
               f"封顶值与「有没有封顶」的标记一起带出  (实际={_c10!r})")
    _assert(_c10.merged is False,
            "两个封顶值数字相同也不是「同一个数」，必须分开说")
    _assert(len(_cap_res) > 10,
            f"前提成立：精确命中确实超过上限 10（实际 {_matched} 条）")
finally:
    pool_store.COUNT_CAP = _cap_bak
_c_full = pool_dialogs._counts_of(mgr, "光伏", ["dom"], {})
_assert_eq((_c_full.matched, _c_full.scope_total, _c_full.matched_capped,
            _c_full.scope_capped), (_matched, _scope_total, False, False),
           f"恢复真实上限后回到精确值（封顶只影响取数上限，不影响口径）  "
           f"(实际={_c_full!r})")

# 双选（国内+国外）= 不限：国外 solar 也能被召回
dlg.chk_region_dom.setChecked(True)
dlg.chk_region_intl.setChecked(True)
_res2, _msg2, _ok2 = _run_search(dlg, "solar")
_assert(_ok2 and any(record_region(r) == "intl" for r in _res2),
        "双选时国外记录可召回")
# 单选国外：结果里一条国内都不该有
dlg.chk_region_dom.setChecked(False)
_res3, _msg3, _ok3 = _run_search(dlg, "solar")
_assert(_ok3 and bool(_res3),
        f"单选国外仍有命中  ({_msg3})")
_assert(all(record_region(r) == "intl" for r in _res3), "单选国外结果全是国外")

# 地区下拉：选项来自视图分区列
dlg.chk_region_dom.setChecked(True)
dlg.chk_region_intl.setChecked(False)
_regions = [dlg.combo_region.itemText(i) for i in range(dlg.combo_region.count())]
_assert_eq(_regions[0], "全部", "下拉首项是「全部」")
_assert("浙江" in _regions and "广东" in _regions,
        f"下拉含视图里的真实省份  (实际={_regions})")
_assert("西班牙" in _regions,
        "国外国家同样来自分区列，且必须是归一后的中文名（旧实现按地址子串猜，"
        "给出的是原始写法 Spain，选了它按分区键一条也筛不到）")
_assert(not any(p in _regions for p in ("江苏", "山东", "北京", "西藏", "新疆")),
        "下拉只列真实存在的分区值，不再硬编码 26 省清单")


# ════════════════════════════════════════════════════════════════════════════
print("\n═══ 3. 筛选：视图可用时重新下压检索，而不是页内再挑一遍 ═══")

_page_res, _page_msg, _page_ok = _run_search(dlg, "光伏")
assert _page_ok and _page_res
_page_n = len(_page_res)

# 勾「必须有邮箱」：下压后池内命中是「含邮箱的光伏公司」
dlg.chk_f_email.setChecked(True)
_researched = []
_orig_do_search = dlg._do_search
dlg._do_search = lambda q, use_deepseek=False: (
    _researched.append(q), _orig_do_search(q, use_deepseek))[1]
_email_box = {}
dlg._on_apply_filter()
if dlg._search_worker is not None:
    dlg._search_worker.finished.connect(
        lambda ok, res, msg: _email_box.update(ok=ok, res=res, msg=msg))
_drain(20.0, until=lambda: bool(_email_box))
_assert(_researched == ["光伏"], "视图可用时点「应用筛选」= 重新下压检索")
_mail_res = _email_box.get("res") or []
_assert(bool(_mail_res) and all(r.get("emails") for r in _mail_res),
        "筛选后每条结果都真的有邮箱")
_mail_pool = mgr.store_count("光伏", ["dom"], {"has_email": True})
_mail_scope = mgr.store_count("", ["dom"], {"has_email": True})
_assert_note(_email_box.get("msg", ""), _mail_pool, _mail_scope, "筛选后的文案")
dlg._do_search = _orig_do_search
dlg.chk_f_email.setChecked(False)

# ── 「只看有联系方式」：界面开关 → 下压参数 → 文案，三段都必须接上 ──
# 池子从 6 万涨到 8 万这一轮带进来 1.3 万条只有官网/地址的线索行；不区分的话
# 「8 万家」会被当成「8 万个能打的电话」，用户导出后才发现一半是空的。
_assert(hasattr(dlg, "chk_f_any"), "筛选面板挂了「只看有联系方式」开关")
_assert_eq(dlg.chk_f_any.text(), "只看有联系方式", "开关文案按用户说得出口的说法写")
_assert("has_contact" not in dlg._get_filter_params(),
        "不勾选时不下压这个条件（一条都不该被排除）")
dlg.chk_f_any.setChecked(True)
_assert(dlg._get_filter_params().get("has_contact") is True,
        f"勾选后转成 has_contact 参数  (实际={dlg._get_filter_params()})")
dlg._on_reset_filter()
_assert(not dlg.chk_f_any.isChecked(), "「重置」把开关复位（残留勾选会让人以为池子变小了）")


class _ReachStub:
    """只替 reach_summary 一个方法：文案函数不该依赖整个管理器。"""

    def __init__(self, rs):
        self._rs = rs

    def reach_summary(self):
        return self._rs


_note_both = pool_dialogs._reach_note(
    _ReachStub({"total": 3, "reachable": 1, "lead_only": 2}))
_assert("1 家" in _note_both and "2 家" in _note_both and "只看有联系方式" in _note_both,
        f"有线索行时同时给出两个数并提示怎么排除  (实际={_note_both!r})")
_note_pure = pool_dialogs._reach_note(
    _ReachStub({"total": 100, "reachable": 100, "lead_only": 0}))
_assert("100 家" in _note_pure and "只看有联系方式" not in _note_pure,
        f"全是可外呼时不提排除开关（不误导用户去找一个用不上的勾选）  (实际={_note_pure!r})")
_assert_eq(pool_dialogs._reach_note(_ReachStub(None)), "",
           "拿不到覆盖度就整句不说——绝不显示成「0 家能打电话」")
_assert_eq(pool_dialogs._reach_note(_ReachStub({"total": 0, "reachable": 0,
                                                "lead_only": 0})), "",
           "空池同样不报（0 总数时这句话没意义）")
_assert_eq(pool_dialogs._reach_note(_ReachStub({"total": 5, "reachable": 5})),
           "其中 5 家有手机号/固话/邮箱/微信（可直接外呼），0 家暂时只有官网或地址",
           "缺 lead_only 键时按互补口径算，不把缺键当成隐藏信息")
_rs_real = mgr.reach_summary()
_note_real = pool_dialogs._reach_note(mgr)
if _rs_real:
    _lead_real = int(_rs_real.get("lead_only",
                                 _rs_real["total"] - _rs_real["reachable"]))
    _assert(f"{_rs_real['reachable']} 家" in _note_real and f"{_lead_real} 家" in _note_real,
            f"界面上的两个数与 mgr.reach_summary 完全一致  (实际={_note_real!r})")
else:
    _assert_eq(_note_real, "", "拿不到覆盖度时界面这句话整句不说（不硬编 0）")

# 回退路径：视图不可用时筛选照样生效，且不谎报池内总数
mgr_store, mgr_view_fp = mgr._store, mgr._view_fp
mgr._store = None
mgr._view_fp = None
_fb_res, _fb_msg, _fb_ok = _run_search(dlg, "光伏")
_assert(_fb_ok and bool(_fb_res), "视图不可用时搜索照样出结果（回退老链路）")
_fb_k, _fb_t = _note_numbers(_fb_msg)
_assert(_fb_k is None and _fb_t is None,
        f"回退时不谎报池内总数（拿不到就不写，也绝不写 0）  (实际={_fb_msg!r})")
dlg._results = list(_fb_res)
dlg._all_results = list(_fb_res)
_researched.clear()
dlg.chk_f_email.setChecked(True)
dlg._on_apply_filter()          # 邮箱筛选：视图不可用 → 页内筛
_drain(0.2)
_assert(_researched == [], "回退路径不触发重检（视图本来就没就绪）")
# 语料里「光伏」国内命中的 30 家只有手机号、没有邮箱 → 页内筛后必须是 0 条
_expect_email = sum(1 for r in _fb_res if r.get("emails"))
_assert_eq(_expect_email, 0, "回退候选里本来就没有含邮箱的")
_assert_eq(len(dlg._results), _expect_email,
           "回退时页内筛选照常生效（与下压同一 apply_filters 口径）")
_assert_eq(dlg.table.rowCount(), _expect_email, "页内筛后表格同步更新")
mgr._store, mgr._view_fp = mgr_store, mgr_view_fp
dlg.chk_f_email.setChecked(False)


# ════════════════════════════════════════════════════════════════════════════
print("\n═══ 4. 概览：视图口径与本页口径绝不混用 ═══")

_ov_res, _ov_msg, _ov_ok = _run_search(dlg, "光伏")
dlg.analytics_frame.setVisible(True)
dlg._update_analytics(dlg._results)
_txt = dlg.analytics_text.toPlainText()
_ov_matched = mgr.store_count("光伏", ["dom"], {})
_ov_scope = mgr.store_count("", ["dom"], {})
_assert(_txt.startswith("统计口径：地区/行业分布（关键词先粗筛出 "),
        f"概览首行如实标注分布是按关键词粗筛的那一批算的，不是本页也不是全池  "
        f"(实际={_txt.splitlines()[0]!r})")
_assert(f"关键词先粗筛出 {_ov_matched} 条" in _txt and
        f"当前范围共 {_ov_scope} 条" in _txt,
        f"概览同时给出粗筛数与当前范围总数，各自等于库内 count  "
        f"(粗筛={_ov_matched}, 范围={_ov_scope})")
# 卡片三个数字：本页给你看多少 / 关键词粗筛多少 / 当前范围总量（历史缺陷是把第二个
# 当成「池内共 N 条」显示，搜一家公司也会报「池内共 18153 条」）
_card = NUM_RE.findall(dlg.card_total.text())
_assert_eq([int(x) for x in _card[-3:]],
           [len(dlg._results), _ov_matched, _ov_scope],
           f"卡片三个数字依次为 本页/粗筛/当前范围  (实际={dlg.card_total.text()!r})")
_v_regions, _v_inds, _scope, _cnt = dlg._analytics_counters(
    dlg._results, dlg._get_filter_params())
_assert(_cnt.matched and "浙江" in _v_regions,
        f"视图聚合按 province 分区列给出地区分布  (实际={dict(_v_regions)})")
_assert_eq(_cnt.scope_total, mgr.store_count("", ["dom"], {}),
           "当前范围总数按当前分区+筛选统计，与关键词无关")
_master_inds = {r.get("industry") for r in MASTER}
_assert(set(_v_inds) <= _master_inds,
        "概览行业与入库派生行业同源（不再另抄一份关键词表）")

# 哨兵桶两个口径：分布里必须出现（它本身就是一张可筛选的补录清单），但绝不能
# 计进「覆盖地区 N 个」——把「未知地区」说成一个被覆盖的地区是假宣称
from collections import Counter as _Counter
_bak_counters = dlg._analytics_counters
dlg._analytics_counters = lambda res, f: (
    _Counter({"浙江": 5, pool_dialogs.PROVINCE_UNKNOWN: 3}),
    _Counter({"光伏太阳能": 4, pool_dialogs.INDUSTRY_UNKNOWN: 2}),
    "测试口径", pool_dialogs.RecallCounts())
dlg._update_analytics(dlg._results)
_assert_eq(int(NUM_RE.findall(dlg.card_region.text())[0]), 1,
           f"覆盖地区不计「未知地区」哨兵桶  (实际={dlg.card_region.text()!r})")
_assert_eq(int(NUM_RE.findall(dlg.card_industry.text())[0]), 1,
           f"涉及行业不计「未分类」哨兵桶  (实际={dlg.card_industry.text()!r})")
_an = dlg.analytics_text.toPlainText()
_assert("未知地区" in _an and "未分类" in _an,
        "两个哨兵桶仍如实出现在分布里（可筛出来补录）")
dlg._analytics_counters = _bak_counters
dlg._update_analytics(dlg._results)

# 换一个横跨两省的品类词：分布必须是两个省，证明聚合下压带了查询条件
_run_search(dlg, "储能")
_two, _two_inds, _two_scope, _two_cnt = dlg._analytics_counters(
    dlg._results, dlg._get_filter_params())
_assert_eq(sorted(_two), ["广东", "浙江"],
           f"「储能」在浙江+广东两省都有命中  (实际={dict(_two)})")
_assert_eq(_two_cnt.matched, mgr.store_count("储能", ["dom"], {}),
           "多省分布与库内 count 同一批召回候选")
_assert(sum(_two.values()) >= _two_cnt.matched,
        "各分区计数之和覆盖召回候选（空分区键的记录本来就不该进分布）")

# 分布必须与旁边的总数带同一批筛选条件（历史缺陷 D9：store_facets 不透传
# filters，勾了「有邮箱」之后总数变小、地区分布却仍是全量，同一屏两套口径）
_mail_all = mgr.store_count("储能", ["dom"], {})
_mail_only = mgr.store_count("储能", ["dom"], {"has_email": True})
_assert(0 < _mail_only < _mail_all,
        f"前提成立：「储能」国内 {_mail_all} 家里只有部分含邮箱，"
        f"筛与不筛必须是两个数（{_mail_only}）")
dlg.chk_f_email.setChecked(True)
_f_regions, _f_inds, _f_scope, _f_cnt = dlg._analytics_counters(
    dlg._results, dlg._get_filter_params())
_assert_eq(sum(_f_regions.values()), _mail_only,
           f"勾邮箱筛选后地区分布只覆盖含邮箱的那批  (实际={dict(_f_regions)})")
_assert_eq(_f_cnt.scope_total, mgr.store_count("", ["dom"], {"has_email": True}),
           "分布与总数同一批记录：范围总数也随筛选一起变小")
_assert_eq(sorted(_f_regions), ["广东"],
           "含邮箱的「储能」只在广东，分布不再混进未筛选的浙江记录")
dlg.chk_f_email.setChecked(False)

# 视图不可用 → 退回本页聚合，且文案必须改成「本页」
mgr._view_fp = None
_v2, _i2, _s2, _c2 = dlg._analytics_counters(dlg._results, {})
_assert(_c2.matched is None and _c2.scope_total is None and "本页" in _s2,
        f"视图不可用时如实标注本页口径、两个总数都不报  (实际={_s2!r})")
_assert(len(_v2) >= 1, "本地聚合仍算得出地区（derive_province 与清洗层同源）")
dlg._update_analytics(dlg._results)
_assert(dlg.analytics_text.toPlainText().startswith("统计口径：本页"),
        "回退时概览首行同样改成本页口径")
mgr._view_fp = mgr_view_fp
dlg._update_analytics(dlg._results)

# 条长按最大值归一：池内数字上千时固定格数会全部顶满看不出差异
from collections import Counter
_uneven = Counter({"广东": 15053, "浙江": 3000, "江苏": 7})
_bars = dlg._bars(_uneven)
_assert(all("█" in b for b in _bars), "条形图每行都有条")
_assert_eq(_bars[0].count("█"), 20, "最大项满格")
_assert(1 <= _bars[1].count("█") < 20 and _bars[-1].count("█") == 1,
        f"小项按比例缩短（最小项至少 1 格不消失）  (实际={_bars})")
_assert(_bars[0].startswith("  广东") and "15053" in _bars[0],
        f"条形行按计数从大到小排  (实际={_bars[0]!r})")
_empty = dlg._bars(Counter())
_assert_eq(_empty, [], "空分布不渲染（不显示半截标题）")


# ════════════════════════════════════════════════════════════════════════════
print("\n═══ 5. SyncDialog：入库前清洗报告 + 上传数字按 kept 报 ═══")

_dirty = [
    _rec("深圳同步测试有限公司", products="光伏组件", address="广东省深圳市",
         mobiles=["13800007777"]),
    _rec("无联系方式的同步公司", products="阀门"),          # 门禁剔除
    _rec("   ", mobiles=["13800008888"]),                  # 无公司名剔除
    _rec("2. 带序号前缀同步有限公司", products="水泵",
         address="浙江省台州市", mobiles=["13800009999"]),
]
_k, _r = clean_records([dict(x) for x in _dirty])
_assert_eq(_r["dropped"], 2, "语料确有 2 条不该入库")

sync_dlg = pool_dialogs.SyncDialog(_dirty, cleaning_report=_r)
sync_dlg.show()
_drain(0.3)
_ct = sync_dlg.clean_text.toPlainText()
_assert("2 条不会入库" in _ct, f"有剔除时报告醒目提示  (实际={_ct[:60]!r})")
_assert("属地分布" in _ct, "报告含属地分布（「上传时就分开吗」的界面证据）")
_assert("被剔记录样例" in _ct, "报告含被剔样本，可回溯核对列映射")
_assert("#c62828" in sync_dlg.clean_title.styleSheet(),
        "有剔除时标题转红（一眼看出这批数据不干净）")
# ── 未入库清单：门禁拦下来的行在界面上必须有去处（不入库 ≠ 消失）──
_assert(hasattr(sync_dlg, "btn_export_drops"), "对话框挂了「导出未入库清单」按钮")
_assert(sync_dlg.btn_export_drops.isVisible(),
        "有剔除时按钮可见（没剔除就不该出现，别把用户引到空文件上）")
_assert("2 条" in sync_dlg.btn_export_drops.text(),
        f"按钮直接写清这次拦了几条  (实际={sync_dlg.btn_export_drops.text()!r})")
_drop_p = os.path.join(_new_tmp_dir(), "gui_未入库清单.xlsx")
_n, _saved = sync_dlg._export_drops_to(_drop_p)
_assert_eq(_n, 2, "导出的清单条数 == 报告剔除条数")
_assert(os.path.exists(_saved), "清单真的落了盘")
from openpyxl import load_workbook as _load_wb2
_ws2 = _load_wb2(_saved)["未入库清单"]
_h2 = [c.value for c in next(_ws2.iter_rows(min_row=1, max_row=1))]
_r2 = [dict(zip(_h2, [c.value for c in r])) for r in _ws2.iter_rows(min_row=2)]
_assert_eq(len(_r2), 2, "清单行数与剔除数一致（逐行可核对）")
_assert({r["剔除原因"] for r in _r2} == {"无联系方式", "缺公司名"},
        f"每行都带具体剔除原因  (实际={[r['剔除原因'] for r in _r2]})")
_clean_rep = clean_records([_rec("全部合规测试有限公司", mobiles=["13800001111"],
                                 address="浙江省杭州市",
                                 products="光伏逆变器")])[1]
sync_dlg4 = pool_dialogs.SyncDialog(
    [_rec("全部合规测试有限公司", mobiles=["13800001111"], address="浙江省杭州市",
          products="光伏逆变器")], cleaning_report=_clean_rep)
sync_dlg4.show()
_drain(0.2)
_assert(not sync_dlg4.btn_export_drops.isVisible(),
        "一条都没被拦时按钮不出现（报告已经写了「全部符合入库口径」）")
sync_dlg4.done(0)
sync_dlg.done(0)

# 不传报告时对话框自己预演一遍（采集后同步链路走的正是这条路）
sync_dlg2 = pool_dialogs.SyncDialog([dict(x) for x in _dirty])
sync_dlg2.show()
_ready = _drain(10.0, until=lambda: bool(sync_dlg2._cleaning_report))
_assert(_ready and bool(sync_dlg2._cleaning_report), "对话框自动完成清洗预演")
_assert_eq(int(sync_dlg2._cleaning_report["dropped"]), 2,
           "自动预演的剔除数与同一入口直算一致")
assert "2 条不会入库" in sync_dlg2.clean_text.toPlainText(), \
    sync_dlg2.clean_text.toPlainText()
sync_dlg2.done(0)

# SyncWorker：提示条数按清洗后 kept，不按提交数
w3 = pool_dialogs.SyncWorker([dict(x) for x in _dirty])
_out = {}
w3.finished.connect(lambda ok, msg: _out.update(ok=ok, msg=msg))
w3.run()
_assert(_out.get("ok") is True, f"上传成功  ({_out.get('msg', '')})")
_assert(f"{len(_k)} 条" in _out.get("msg", ""),
        f"提示条数 = 清洗后 kept（不是提交数 {len(_dirty)}）  "
        f"(实际={_out.get('msg', '')!r})")
_assert_eq(int(w3.report.get("kept", -1)), len(_k),
           "worker 报告里的 kept 与清洗结果一致")

# 上传后对话框把结果面板/审计都按 kept 走
sync_dlg3 = pool_dialogs.SyncDialog([dict(x) for x in _dirty],
                                    cleaning_report=_r)
sync_dlg3.show()
_drain(0.2)
sync_dlg3._worker = w3
sync_dlg3._on_sync_finished(True, _out.get("msg", ""))
_assert_eq(sync_dlg3._uploaded_count(), len(_k),
           "_uploaded_count 取报告 kept（Toast 与审计都用它）")
sync_dlg3.done(0)


# ════════════════════════════════════════════════════════════════════════════
print("\n═══ 6. 相关推荐：候选来自视图召回，结果不回写总表 ═══")

_seed = next(r for r in MASTER if "光伏" in r.get("products", ""))
RW = pool_dialogs.RecommendWorker

# 召回词的选择（推荐锚定的是目标公司自己，不能拿整表当候选）
_q, _f, _c = RW("similar", _seed, MASTER, mgr=mgr)._plan()
_assert(_q == _seed["products"], f"相似推荐以目标公司主营为召回词  (实际={_q!r})")
_assert(_c == "", "主营齐全时不该有冲突提示")
_seed_bare = dict(_seed, products="")
_assert(RW("similar", _seed_bare, MASTER, mgr=mgr)._plan()[0] == _seed_bare["company"],
        "无主营时退回公司名做召回")

# 同地区：下压到派生省份，且尊重用户已选地区
_qr, _fr, _cr = RW("regional", _seed, MASTER, mgr=mgr)._plan()
_assert(_qr == "", "同地区不按关键词召回（按分区下压）")
_assert(_fr.get("region") == "浙江", f"按派生省份下压  (实际={_fr})")
_assert(_cr == "", "与当前筛选不冲突时不提示")
_conf_q, _conf_f, _conf_c = RW("regional", _seed, MASTER, mgr=mgr,
                               filters={"region": "广东"})._plan()
_assert("不一致" in _conf_c, f"与用户地区筛选冲突时如实说明  (实际={_conf_c!r})")
_assert(_conf_f.get("region") == "广东", "冲突时不拿目标公司省份覆盖用户筛选")
_no_prov = RW("regional", dict(_seed, address="某市某区", mobiles=[],
                              province=""), MASTER, mgr=mgr)._plan()[2]
_assert("判不出所在地区" in _no_prov,
        "地址/号码/邮箱里都没有地区线索时如实说明原因，而不是报「没有同地区公司」")
# 哨兵值绝不能当同地区筛选键：拿「未知地区」下压等于把全池判不出地区的公司
# 都算成「同地区」，那是一种会误导用户的假推荐
_sent = dict(_seed, address="某市某区", mobiles=[], province="未知地区")
_q_s, _f_s, _c_s = RW("regional", _sent, MASTER, mgr=mgr)._plan()
_assert("判不出所在地区" in _c_s and not _f_s.get("region"),
        f"地区分区键已是哨兵时也当「无证据」处理  (实际={_f_s!r}/{_c_s!r})")

# 国外公司的同地区：分区键走归一后的中文国名（与视图 province 列同口径），
# 旧的 country 原始写法 Spain 既筛不到视图，也没法并进 España/西班牙 同一个桶
_seed_intl = next(r for r in MASTER if r.get("country") == "Spain")
_si_q, _si_f, _si_c = RW("regional", _seed_intl, MASTER, mgr=mgr)._plan()
_assert(_si_f.get("region") == "西班牙", f"国外公司按归一国名下压  (实际={_si_f})")
_assert(_si_c == "", "国外同地区同样不冲突")

# 三级降级：视图就绪 / 视图不可用 / 完全没有管理器
_got, _src, _cnt = RW("similar", _seed, MASTER, mgr=mgr)._recall(_q, _f)
_assert(_src == "检索视图", "视图就绪时候选来自检索视图")
_assert(0 < len(_got) <= RW.RECALL_CAP, f"候选规模有界  (实际={len(_got)})")
_assert(any("光伏" in (r.get("products") or "") for r in _got), "候选确实命中召回词")
_assert_eq(_cnt.matched, mgr.store_count(_q, [], _f),
           "视图路径的粗筛数与库内 count 一致")
_assert_eq(_cnt.scope_total, mgr.store_count("", [], _f),
           "视图路径的当前范围数与关键词无关")
_assert(_cnt.by_keyword is True, "带关键词召回时 by_keyword 必须为真")
_got2, _src2, _cnt2 = RW("similar", _seed, MASTER, mgr=mgr_broken)._recall(_q, _f)
_assert(_src2 != "检索视图", f"视图不可用不得声称走视图  (实际={_src2})")
_assert(_cnt2.matched is None and _cnt2.scope_total is None,
        "回退路径两个总数都如实为空（宁可少报，绝不猜）")
_got3, _src3, _cnt3 = RW("similar", _seed, MASTER, mgr=None)._recall(_q, _f)
_assert(_src3 == "全表扫描" and len(_got3) == len(MASTER),
        "没有管理器时如实回退全表，候选就是总表本身")
_assert(_cnt3.matched is None and _cnt3.scope_total is None, "无管理器时同样不报总数")

# 同地区推荐是「空关键词 + 筛选下压」：不许说成「关键词粗筛」
_rq, _rf, _rc = RW("regional", _seed, MASTER, mgr=mgr)._plan()
_rgot, _rsrc, _rcnt = RW("regional", _seed, MASTER, mgr=mgr)._recall(_rq, _rf)
_assert(_rq == "" and _rf.get("region") == "浙江", f"同地区按省份下压  ({_rq!r} {_rf})")
_assert(_rcnt.by_keyword is False, "空关键词召回不能声称按关键词粗筛过")
_assert_eq(_rcnt.scope_total, mgr.store_count("", [], {"region": "浙江"}),
           "空关键词时仍报得出当前范围总数")
_assert("关键词" not in pool_dialogs._recall_note(
        _rsrc, len(_rgot), _rcnt, cap=RW.RECALL_CAP),
        f"空关键词的取数说明里不出现「关键词」  "
        f"(实际={pool_dialogs._recall_note(_rsrc, len(_rgot), _rcnt, cap=RW.RECALL_CAP)!r})")
_assert("关键词" not in pool_dialogs._scope_note(_rcnt, len(_rgot), unit="家"),
        "空关键词的文案里同样不出现「关键词」")

# 端到端跑一遍：结果带标注，但总表对象绝不带脏字段
_box = {}
rw = RW("similar", _seed, MASTER, mgr=mgr)
rw.finished.connect(lambda ok, res, msg: _box.update(ok=ok, res=res, msg=msg))
rw.run()
_assert(_box.get("ok") is True, f"推荐成功  ({_box.get('msg', '')})")
_assert(bool(_box.get("res")), "相似推荐拿到结果")
_assert("_recommend_type" in _box["res"][0], "推荐结果带 _recommend_type 标注")
_assert(all("_recommend_type" not in r for r in MASTER),
        "总表对象未被污染（旧实现就地写 rec[...]，脏字段会一路带进导出与上传）")
_assert_note(_box.get("msg", ""), mgr.store_count(_q, [], {}),
             mgr.store_count("", [], {}), "推荐文案")

# 冲突路径：run 直接给结论，不做无谓的全表打分
_cb = {}
rw_conf = RW("regional", _seed, MASTER, mgr=mgr, filters={"region": "广东"})
rw_conf.finished.connect(lambda ok, res, msg: _cb.update(ok=ok, msg=msg))
rw_conf.run()
_assert(_cb.get("ok") is False and "不一致" in _cb.get("msg", ""),
        f"冲突时把原因回给用户  ({_cb.get('msg', '')})")

# GUI 入口：表格换成推荐结果，文案走同一个 _scope_note
dlg._recommend_worker = None
dlg._on_recommend(0, "similar")
_drain(10.0, until=lambda: dlg._recommend_worker is None
        or not dlg._recommend_worker.isRunning())
_assert(dlg._recommend_worker is not None and dlg._recommend_worker.isFinished(),
        "推荐线程正常收尾")
_assert(bool(dlg._results) and dlg.table.rowCount() == len(dlg._results),
        f"推荐结果铺到表格  (rows={dlg.table.rowCount()}, results={len(dlg._results)})")
_assert("_recommend_type" in (dlg._results[0] if dlg._results else {}),
        "表格里的推荐结果带标注")
_assert(all("_recommend_type" not in r for r in MASTER), "GUI 路径同样不污染总表")
_assert("相似的公司" in dlg.status_lbl.text(),
        f"状态栏说明这是推荐视图  (实际={dlg.status_lbl.text()!r})")

# 「是否同一家」的判据守卫：空标识绝不参与相等判定（本次 0 家推荐的根因）
from recommend_engine import _is_anchor
_assert(_is_anchor({"company_norm": ""}, {"company_norm": ""}) is False,
        "两个空 norm 不算同一家（旧写法恒真，会把全部候选判成自身）")
_assert(_is_anchor({"company_norm": "abc"}, {"company_norm": "abc"}) is True,
        "同一 norm 仍正确判为同一家")
_assert(_is_anchor({"company_norm": "abc"}, {"company_norm": "abd"}) is False,
        "不同 norm 不误判")
_self_rec = {"company_norm": ""}
_assert(_is_anchor(_self_rec, _self_rec) is True, "同一对象始终是锚点本身")

# 关闭收尾：加载/搜索/推荐三个线程都在管辖范围内（跑着线程销毁对话框必崩）
_assert("closeEvent" in vars(pool_dialogs.SearchDialog)
        and "reject" in vars(pool_dialogs.SearchDialog),
        "SearchDialog 自己接管关闭收尾")
_assert("RecommendWorker" in vars(pool_dialogs), "RecommendWorker 定义在本模块")


# ════════════════════════════════════════════════════════════════════════════
print("\n═══ 7. 清理 ═══")

dlg.done(0)
_drain(0.2)
for d in _tmp_roots:
    shutil.rmtree(d, ignore_errors=True)
_assert(all(not os.path.exists(d) for d in _tmp_roots), "临时目录全部清理")

print("\n" + "=" * 60)
print(f"全部通过 ✅ ({_passed}/{_passed + _failed})" if _failed == 0
      else f"完成：{_passed} 通过 / {_failed} 失败 ❌")
print("=" * 60)
sys.exit(1 if _failed else 0)
