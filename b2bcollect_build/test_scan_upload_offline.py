# -*- coding: utf-8 -*-
"""test_scan_upload_offline.py —— 扫描上传四项改造的离线夹具（不需要网络/OSS）。

覆盖（与 2026-11-17 改造一一对应）：
  1. 行业词表同源（pool_matcher 查询端派生自 ingest_cleaning 写入端唯一真源）：
     键集合一致、行业名在位、无空格死词、口语词识别抽查
  2. 搜索主链路行业裁剪 + 不足回退（SearchWorker._recall）：
     裁剪生效/切片太小回退并明说/公司名豁免/手动选择不覆盖/泛词不裁剪/
     视图挂了不发行业文案
  3. 分片并发上传（upload_session）：
     worker 数收缩、client 独占、按分片号装配、单片路径不变、失败聚合
  4. 微信扫描根（default_roots）：
     三处探测、被默认根覆盖不重复加、显示名「微信聊天文件」
  5. 复制源 header 纯 ASCII（copy_key，2026-11-17 全盘扫描实机缺陷）：
     中文 tag 的 key 进 x-oss-copy-source 必须 percent-encode，
     签名串与发送的 header 逐字节一致

运行：cd b2bcollect_build && python test_scan_upload_offline.py
"""
import os
import pathlib
import shutil
import sys
import tempfile
import threading
import time
import types

sys.path.insert(0, os.path.dirname(__file__))

from pool_matcher import INDUSTRY_SYNONYMS, detect_industry
import ingest_cleaning
import local_scan as ls
import data_pool as dp
import pool_dialogs as pd

# ── 测试辅助 ──────────────────────────────────────────────────────────────────

_passed = 0
_failed = 0


def _assert(condition: bool, label: str, extra: str = ""):
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  ✅ {label}")
    else:
        _failed += 1
        print(f"  ❌ {label}  {extra}")


# ── 1. 行业词表同源 ──────────────────────────────────────────────────────────

def test_industry_vocab_single_source():
    print("\n═══ 1. 行业词表同源 ═══")
    write_side = ingest_cleaning.INDUSTRY_KEYWORDS
    _assert(len(write_side) == 30, "写入端 30 个行业",
            f"实际 {len(write_side)}")
    _assert(set(INDUSTRY_SYNONYMS.keys()) == set(write_side.keys()),
            "查询端键集合与写入端完全一致",
            f"查询端独有 {sorted(set(INDUSTRY_SYNONYMS) - set(write_side))} "
            f"写入端独有 {sorted(set(write_side) - set(INDUSTRY_SYNONYMS))}")
    # 行业名本身必须在候选词首位（识别结果就是落库值，裁剪才打得中）
    bad_first = [k for k, v in INDUSTRY_SYNONYMS.items()
                 if not v or v[0] != k]
    _assert(not bad_first, "每个行业名本身都在候选词首位", str(bad_first))
    # 无空格死词（历史上真有过 " 电动车"：带前导空格永远匹配不到）
    dead = [(k, w) for k, ws in INDUSTRY_SYNONYMS.items() for w in ws
            if w != w.strip()]
    _assert(not dead, "无前后空格死词", str(dead))
    # 口语扩展表的键不许越界（越界键永远被识别表忽略，等于白写）
    from pool_matcher import _INDUSTRY_QUERY_EXTRAS, _WRITE_SIDE_INDUSTRIES
    overflow = set(_INDUSTRY_QUERY_EXTRAS) - set(_WRITE_SIDE_INDUSTRIES)
    _assert(not overflow, "口语扩展表键全部在写入端词表内", str(overflow))

    # 识别行为抽查（钉死最长匹配与口语词映射）
    cases = [
        ("光伏组件出口", "光伏太阳能"),
        ("充电宝", "储能与电池"),
        ("马桶", "建材与装饰"),
        ("液压泵", "泵阀管道"),
        ("不锈钢管", "金属与冶金"),
        ("5G基站", "电子电气"),
        ("知名制造企业", None),      # 泛词不进识别表
        ("", None),
    ]
    for q, want in cases:
        got = detect_industry(q)
        _assert(got == want, f"detect_industry({q!r}) == {want!r}",
                f"实际 {got!r}")


# ── 2. 搜索主链路行业裁剪 + 回退 ─────────────────────────────────────────────

class _FakeMgr:
    """假 mgr：记录每次 search_candidates 的 filters，按脚本返回结果。"""

    def __init__(self, script):
        self.script = list(script)      # 依次弹出的返回值（list 或 Exception）
        self.calls = []                 # 每次 (filters, limit)
        self.count_calls = []           # 每次 (query, filters)

    def search_candidates(self, query, regions, filters, limit):
        self.calls.append((dict(filters or {}), limit))
        r = self.script.pop(0)
        if isinstance(r, Exception):
            raise r
        return list(r)

    def store_count_capped(self, query, regions, filters, cap=None):
        self.count_calls.append((query, dict(filters or {})))
        return (123, False)

    def search_by_index(self, query):
        return {"k1", "k2"}


def _worker_self_attrs():
    """产品侧 `SearchWorker._recall` + `_scope_records` 真正触碰的 self 属性（AST 现算）。

    这一格以前只测「视图能用」那条路，回退到整张总表那条路一碰就炸在产品代码深处的
    AttributeError，排查的人只能从堆栈往回猜「夹具少给了什么」。把属性清单从产品源码
    里当场取出来对拍，漂移就变成在这里点名「缺哪个」，而且清单不由本文件维护——
    产品加一个 self.xxx，这里自动跟着要求。
    """
    import ast
    import inspect
    import textwrap
    names = set()
    for fn in (pd.SearchWorker._recall, pd.SearchWorker._scope_records):
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        for n in ast.walk(tree):
            if (isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                    and n.value.id == "self"):
                names.add(n.attr)
    return names


def _fake_worker(query, mgr=None, filters=None, max_results=100):
    """不实例化 QThread：鸭子类型直接喂未绑定方法 _recall。

    `_scope_records` 挂的是**产品自己的方法**（`MethodType` 绑上去），不在这里另写一份：
    「懒采纳下回退全表要先过内存闸门，不够就就地失败并把原因说清」这条纪律就住在那
    个方法里，夹具复刻一份 stub 就等于把它复制成第二个真源——它会漂。
    """
    w = types.SimpleNamespace(
        query=query, master=["M1", "M2"], mgr=mgr, regions=[],
        filters=filters or {}, candidate_indices=None,
        max_results=max_results,
        RECALL_CAP=pd.SearchWorker.RECALL_CAP,
        PRUNE_MIN_FLOOR=pd.SearchWorker.PRUNE_MIN_FLOOR,
    )
    w._scope_records = types.MethodType(pd.SearchWorker._scope_records, w)
    missing = sorted(_worker_self_attrs() - set(vars(w)))
    _assert(not missing, "夹具必须跟上 SearchWorker 的真实形状（缺哪个属性就在这一格点名）",
            str(missing))
    return w


def test_search_industry_pruning():
    print("\n═══ 2. 搜索行业裁剪 + 回退 ═══")
    recall = pd.SearchWorker._recall

    # 裁剪生效：识别出行业、切片够大 → SQL 带 industry 谓词
    m = _FakeMgr([list(range(500))])
    records, src, counts, note = recall(_fake_worker("光伏组件出口", mgr=m))
    _assert(m.calls and m.calls[0][0].get("industry") == "光伏太阳能",
            "首次查询带 industry 谓词（下压 SQL）", str(m.calls[:1]))
    _assert(src == "检索视图", "召回来源=检索视图", src)
    _assert("行业池" in note and "光伏太阳能" in note, "文案说明行业池", note)
    _assert(bool(m.count_calls)
            and m.count_calls[0][1].get("industry") == "光伏太阳能",
            "计数同口径带 industry", str(m.count_calls[:1]))
    _assert(len(m.calls) == 1, "切片够大只查一次", str(len(m.calls)))

    # 切片太小 → 回退全库并如实说明
    m = _FakeMgr([list(range(3)), list(range(2000))])
    records, src, counts, note = recall(_fake_worker("光伏组件出口", mgr=m))
    _assert(len(m.calls) == 2, "回退查了两次", str(len(m.calls)))
    _assert("industry" not in m.calls[1][0], "第二次不带 industry（全库）",
            str(m.calls[1][0]))
    _assert(len(records) == 2000, "返回全库结果", str(len(records)))
    _assert("光伏太阳能" in note and "3 条" in note and "回退全库" in note,
            "回退文案含行业名与切片数", note)

    # 公司名查询豁免（产业链上下游跨行业）
    m = _FakeMgr([list(range(500))])
    records, src, counts, note = recall(
        _fake_worker("深圳市比亚迪锂电池有限公司", mgr=m))
    _assert(len(m.calls) == 1 and "industry" not in m.calls[0][0],
            "公司名查询不裁剪", str(m.calls))
    _assert(note == "", "公司名查询无行业文案", repr(note))

    # 用户手动选行业 → 尊重显式选择
    m = _FakeMgr([list(range(500))])
    records, src, counts, note = recall(
        _fake_worker("光伏组件出口", mgr=m, filters={"industry": "电子电气"}))
    _assert(len(m.calls) == 1 and m.calls[0][0].get("industry") == "电子电气",
            "手动选的行业不被覆盖", str(m.calls))
    _assert(note == "", "手动选择不追加行业文案", repr(note))

    # 视图全挂 → 落索引路径，不发行业文案（没做成的动作不说）
    m = _FakeMgr([RuntimeError("down"), RuntimeError("down")])
    records, src, counts, note = recall(_fake_worker("光伏组件出口", mgr=m))
    _assert(src == "关键词索引", "视图挂了走关键词索引", src)
    _assert(note == "", "索引路径不发行业文案", repr(note))

    # mgr=None → 安全四元
    records, src, counts, note = recall(_fake_worker("光伏组件", mgr=None))
    _assert(records == ["M1", "M2"] and src == "" and note == "",
            "mgr=None 安全返回", f"{src!r} {note!r}")


# ── 3. 分片并发上传 ──────────────────────────────────────────────────────────

class _FakeWorkerClient:
    def __init__(self, oss, fail_shards, latency):
        self.oss = oss
        self.fail_shards = fail_shards
        self.latency = latency
        self.uploaded = {}
        self.got_cb = False

    def upload_json_atomic(self, key, payload, progress_callback=None):
        self.got_cb = progress_callback is not None
        shard = payload["shard"]
        with self.oss.lock:
            self.oss.active += 1
            self.oss.max_active = max(self.oss.max_active, self.oss.active)
        try:
            time.sleep(self.latency)     # 制造并发窗口
            if shard in self.fail_shards:
                raise dp.OSSPoolError(f"模拟第 {shard} 批失败")
            self.uploaded[key] = payload
        finally:
            with self.oss.lock:
                self.oss.active -= 1


class _FakeOSS:
    def __init__(self, fail_shards=(), latency=0.02):
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.clones = 0
        self.worker_clients = []
        self.fail_shards = list(fail_shards)
        self.latency = latency
        self.single_key = None
        self.single_payload = None
        self.single_cb_passed = False

    def clone_for_thread(self):
        self.clones += 1
        c = _FakeWorkerClient(self, self.fail_shards, self.latency)
        self.worker_clients.append(c)
        return c

    def upload_json_atomic(self, key, payload, progress_callback=None):
        self.single_key = key
        self.single_payload = payload
        self.single_cb_passed = progress_callback is not None


def _valid_records(n):
    return [{"company": f"深圳市测试电子有限公司{i}", "phone": f"1380013{i:04d}"}
            for i in range(n)]


def _upload(fake, records, cb=None, rep=None):
    return dp.DataPoolManager.upload_session(
        fake, records, tag="excel",
        progress_callback=cb or (lambda m: None),
        report_out=rep)


def test_sharded_concurrent_upload():
    print("\n═══ 3. 分片并发上传 ═══")

    # 25 条 / 单片 10 条 → 3 片、2 worker：并发真实发生且不超上限
    oss = _FakeOSS()
    fake = types.SimpleNamespace(
        SHARD_RECORD_LIMIT=10, UPLOAD_WORKERS=2, username="tester",
        oss=oss, _bg_update_meta=lambda *a: None)
    msgs = []
    rep = {}
    label = _upload(fake, _valid_records(25), cb=msgs.append, rep=rep)
    _assert(oss.clones == 2, "clone 恰好 2 次（=worker 数）", str(oss.clones))
    total = sum(len(c.uploaded) for c in oss.worker_clients)
    _assert(total == 3, "3 片全部上传", str(total))
    _assert("_p1of3" in label and "等 3 个文件" in label,
            "label 指向第 1 片（按分片号装配，不随完成序漂）", label)
    _assert(any("3 批" in m and "并发上传" in m for m in msgs),
            "开播文案说明并发", str(msgs[:2]))
    _assert(any("已上传 3/3 批" in m for m in msgs), "完成计数到 3/3",
            str(msgs[-1]))
    _assert(oss.max_active == 2, "实测并发=worker 数且不超上限",
            f"max_active={oss.max_active}")
    _assert(rep.get("in") == 25, "清洗报告进 report_out", str(rep.get("in")))
    all_payloads = [p for c in oss.worker_clients for p in c.uploaded.values()]
    _assert(all(p["shard_count"] == 3 and 1 <= p["shard"] <= 3
                for p in all_payloads), "payload 分片号正确")

    # 单片路径行为不变：不 clone、无 _p1of1、回调直传
    oss = _FakeOSS()
    fake = types.SimpleNamespace(
        SHARD_RECORD_LIMIT=100, UPLOAD_WORKERS=4, username="tester",
        oss=oss, _bg_update_meta=lambda *a: None)
    _upload(fake, _valid_records(5))
    _assert(oss.clones == 0, "单片不 clone", str(oss.clones))
    _assert(oss.single_key and "_p" not in oss.single_key,
            "单片文件名无分片后缀", str(oss.single_key))
    _assert(oss.single_cb_passed, "单片回调直传 client")
    _assert(oss.single_payload["shard"] == 1
            and oss.single_payload["shard_count"] == 1,
            "单片 payload shard=1/1")

    # 第 2 片失败 → 聚合报错含分片号，其余片完成
    oss = _FakeOSS(fail_shards=(2,))
    fake = types.SimpleNamespace(
        SHARD_RECORD_LIMIT=10, UPLOAD_WORKERS=2, username="tester",
        oss=oss, _bg_update_meta=lambda *a: None)
    try:
        _upload(fake, _valid_records(30))
        raised = None
    except dp.OSSPoolError as e:
        raised = str(e)
    _assert(raised and "第2批" in raised and "1 批上传失败" in raised,
            "失败聚合报分片号", raised or "没有抛错")
    ok = sorted(p["shard"] for c in oss.worker_clients
                for p in c.uploaded.values())
    _assert(ok == [1, 3], "失败外的片都已上传", str(ok))

    # 50 片规模：worker 数恒为 4，绝不超
    oss = _FakeOSS(latency=0.005)
    fake = types.SimpleNamespace(
        SHARD_RECORD_LIMIT=2, UPLOAD_WORKERS=4, username="tester",
        oss=oss, _bg_update_meta=lambda *a: None)
    _upload(fake, _valid_records(100))
    _assert(oss.clones == 4, "50 片只开 4 worker", str(oss.clones))
    total = sum(len(c.uploaded) for c in oss.worker_clients)
    _assert(total == 50, "50 片全上传", str(total))
    _assert(oss.max_active <= 4, "并发不超 4", f"max_active={oss.max_active}")


# ── 4. 微信扫描根 ────────────────────────────────────────────────────────────

def test_wechat_scan_root():
    print("\n═══ 4. 微信扫描根 ═══")
    orig_home = pathlib.Path.home
    cur = None

    def use_home(mapping):
        nonlocal cur
        if cur:
            shutil.rmtree(cur, ignore_errors=True)
        cur = tempfile.mkdtemp(prefix="scanup_home_")
        for rel, make in mapping.items():
            p = pathlib.Path(cur) / rel
            if make:
                p.mkdir(parents=True, exist_ok=True)
        pathlib.Path.home = classmethod(lambda cls: pathlib.Path(cur))

    def has_root(roots, rel):
        return any(r == os.path.join(cur, rel) for r in roots)

    wechat_lib = ("Library/Containers/com.tencent.xinWeChat/"
                  "Data/Documents/xwechat_files")
    try:
        # 三处候选都在 → 只追加 Library 版（Documents 下两处已被覆盖）
        use_home({"Desktop": True, "Documents": True, "Downloads": True,
                  wechat_lib: True, "Documents/WeChat Files": True,
                  "Documents/xwechat_files": True})
        roots = ls.default_roots()
        _assert(len(roots) == 4, "三处候选只加不被覆盖的 Library 版",
                str(roots))
        _assert(has_root(roots, wechat_lib), "微信 Library 根在列")
        _assert(not has_root(roots, "Documents/WeChat Files")
                and not has_root(roots, "Documents/xwechat_files"),
                "Documents 下两处不重复加")

        # 没有微信目录 → 行为与旧版一致（三个默认根）
        use_home({"Desktop": True, "Documents": True, "Downloads": True})
        roots = ls.default_roots()
        _assert(len(roots) == 3, "无微信目录不追加", str(roots))

        # 显示名
        use_home({"Desktop": True, wechat_lib: True})
        wp = os.path.join(cur, wechat_lib)
        _assert(ls.root_display_name(wp) == "微信聊天文件",
                "微信根显示「微信聊天文件」", ls.root_display_name(wp))
        _assert(ls.root_display_name(os.path.join(cur, "Desktop")) == "Desktop",
                "普通根显示目录名")
    finally:
        pathlib.Path.home = orig_home
        if cur:
            shutil.rmtree(cur, ignore_errors=True)

    # 真实环境（信息性，不做硬断言：CI 机器可能没有微信）
    roots = ls.default_roots()
    wechat_roots = [r for r in roots if "com.tencent.xinWeChat" in r]
    print(f"  ℹ️ 本机默认根 {len(roots)} 个：{[os.path.basename(r) for r in roots]}")
    print(f"  ℹ️ 本机微信根：{wechat_roots or '（无）'}")
    _assert(all(os.path.isdir(r) for r in roots), "返回的每个根都真实存在")


# ── 5. 复制源 header 纯 ASCII（2026-11-17 全盘扫描实机缺陷）────────────────────

def test_copy_source_header_ascii():
    print("\n═══ 5. 复制源 header 纯 ASCII（中文 tag 不炸） ═══")
    from urllib.parse import unquote

    from oss_pool_client import OSSPoolClient
    c = OSSPoolClient(bucket="bkt", endpoint="example.aliyuncs.com",
                      prefix="0817/pool", ak_id="id", ak_secret="sec")
    captured = {}

    def fake_send(method, url, headers_factory, what="", timeout=None):
        captured["headers"] = dict(headers_factory())
        return types.SimpleNamespace(status_code=200,
                                     headers={"ETag": "T"},
                                     text="", json=lambda: {})

    orig_send = c._send
    c._send = fake_send
    try:
        c.copy_key("u/upload_1_p1of87_全盘扫描.json", "u/x.json")
    finally:
        c._send = orig_send

    src = captured["headers"]["x-oss-copy-source"]
    try:
        src.encode("latin-1")
        ok = True
    except UnicodeEncodeError:
        ok = False
    _assert(ok, "复制源 header 可 latin-1 编码（HTTP 头硬约束）", repr(src))
    _assert("全盘扫描" not in src and "%" in src,
            "中文 key 已 percent-encode", repr(src))
    _assert(unquote(src) == "/bkt/0817/pool/u/upload_1_p1of87_全盘扫描.json",
            "解码还原等于真实源对象路径", unquote(src))

    # 签名串用与发送 header 完全相同的值（OSS 对 header 原文验签）
    import base64 as _b64
    import hashlib as _h
    import hmac as _hmac
    date = captured["headers"]["Date"]
    sts = (f"PUT\n\n\n{date}\n"
           f"x-oss-copy-source:{src}\n/bkt/0817/pool/u/x.json")
    expect = _b64.b64encode(_hmac.new(
        b"sec", sts.encode("utf-8"), _h.sha1).digest()).decode()
    _assert(captured["headers"]["Authorization"] == f"OSS id:{expect}",
            "签名串与发送的 header 逐字节一致（验签口径）",
            captured["headers"]["Authorization"])


# ── main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    test_industry_vocab_single_source()
    test_search_industry_pruning()
    test_sharded_concurrent_upload()
    test_wechat_scan_root()
    test_copy_source_header_ascii()

    print("\n" + "=" * 60)
    total = _passed + _failed
    if _failed == 0:
        print(f"全部通过 ✅ ({_passed}/{total})")
    else:
        print(f"通过 {_passed}/{total}，失败 {_failed} ❌")
    print("=" * 60)

    sys.exit(1 if _failed else 0)
