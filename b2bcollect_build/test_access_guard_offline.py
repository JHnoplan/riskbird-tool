# -*- coding: utf-8 -*-
"""test_access_guard_offline.py —— 双重账号验证 + 国内外归属链路的离线测试。

覆盖：
  1. _parse_accounts / _normalize_account（名单解析与账号归一口径）
  2. AccessGuard.check（未绑定/在名单/不在名单/+86 归一/TTL 缓存/出网预算/
     网络失败走磁盘副本+本机认领记录/从未成功严格阻断/首次播种/后台刷新去重）
  3. eventFilter 拦截语义（受拒拦截、放行透传、豁免控件、键盘触发）
  4. record_region / _infer_is_domestic（多信号加权推导：强弱证据、
     噪声地址不得一票否决、零证据按公司名语言收尾、派生值须带判据版本才短路）
  5. _merge_records 属地合并（并集证据优先、显式值投票只在零证据时兜底）
     + _normalize_is_domestic 落盘口径
  6. apply_filters regions 多选过滤（单选/双选/空选兜底/存量缺失推导）

等级判定与单点登录顶号的用例不在本文件，见 test_acl_levels_offline.py：
那边盯的是「等级怎么算、设备怎么顶、踢掉之后怎么收」这条新链路，
本文件盯的是「门禁本身别把自己锁死」这条老契约——两边分开才看得出是谁改坏的。

运行：QT_QPA_PLATFORM=offscreen .venv/bin/python b2bcollect_build/test_access_guard_offline.py
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

import access_guard as ag
from access_guard import AccessGuard, _parse_accounts, _normalize_account
from oss_pool_client import OSSPoolError
from data_pool import (
    DataPoolManager, record_region, _infer_is_domestic, _domestic_signals,
    REGION_RULE_VERSION,
)

_PASS = 0
_FAIL = 0


def _assert(cond, msg):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ✅ {msg}")
    else:
        _FAIL += 1
        print(f"  ❌ {msg}")


def _assert_eq(actual, expected, msg):
    _assert(actual == expected, f"{msg}（期望 {expected!r}，实际 {actual!r}）")


# ── 测试替身 ────────────────────────────────────────────────────────────────

class FakeClient:
    """OSS 客户端替身：名单 + 设备分片都可编程，统计调用次数与写过的对象。

    设备分片必须一起替身化：门禁从「只看名单」变成「名单 + 本机绑定」后，
    只喂名单的替身会让 check() 永远停在「正在校验设备绑定」，
    测试看着像覆盖了、其实一条判定都没跑到。
    """

    def __init__(self, accounts=None, fail=False, missing=False, grants=None,
                 fail_msg=None):
        self.calls = 0
        self.accounts = accounts if accounts is not None else ["15088131371"]
        self.grants = grants            # None = 名单里根本没有等级字段（旧版）
        self.fail = fail
        # 故障文案可指定：5xx（服务端异常）与超时（网络）在分类上是两类，
        # 都写成同一句话就等于放弃了这次修复要守住的区分
        self.fail_msg = fail_msg
        self.missing = missing       # 首次 download 抛"不存在"→ 触发播种分支
        self.uploaded = None
        self.shards: dict = {}          # phone → 设备分片（后写覆盖 = 顶号）
        self.writes: list = []

    def _doc(self):
        doc = {"accounts": list(self.accounts)}
        if self.grants is not None:
            doc["grants"] = dict(self.grants)
        return doc

    def download_json(self, key):
        self.calls += 1
        # 用真实 OSSPoolError：走生产代码的 except 分支（错误口径同源）
        if self.fail:
            raise OSSPoolError(self.fail_msg or "下载失败 HTTP 500")
        if key == ag.ACL_KEY:
            if self.missing:
                self.missing = False    # 播种后重读可成功
                raise OSSPoolError("对象不存在: acl/whitelist.json")
            return self._doc(), '"etag"'
        if key.startswith("acl/devices/"):
            phone = key.split("/")[-1][:-5]
            if phone in self.shards:
                return json.loads(json.dumps(self.shards[phone])), '"etag"'
            raise OSSPoolError(f"对象不存在: {key}")
        raise OSSPoolError(f"对象不存在: {key}")

    def upload_json_atomic(self, key, data, progress_callback=None):
        self.uploaded = data
        self.writes.append((key, json.loads(json.dumps(data))))
        if key == ag.ACL_KEY:
            self.accounts = list(data.get("accounts") or [])
            self.grants = dict(data.get("grants") or {})
        elif key.startswith("acl/devices/"):
            self.shards[key.split("/")[-1][:-5]] = data
        return '"etag"'

    def delete_key(self, key):
        self.shards.pop(key.split("/")[-1][:-5], None)


def _make_guard(account="15088131371", client=None, ttl=60.0, state_dir=None):
    """构造隔离守卫：注入账号读取、OSS 替身、临时状态目录。

    重定向的是 ``_state_dir`` 而不是单个缓存文件名：权限本地状态是三份文件
    （名单副本 / 设备指纹 / 认领记录），只挪其中一份会让另外两份写到真实桌面目录，
    用例之间还会通过「同一台机器、同一份认领记录」互相污染。
    """
    g = AccessGuard(ttl=ttl)
    g._account_reader = (lambda: account) if account is not None else None
    if account is None:
        g._load_bound_account = lambda: ""
    if client is not None:
        g._build_client = staticmethod(lambda: client)
    tmp = Path(state_dir or tempfile.mkdtemp(prefix="acl_test_"))
    ag._state_dir = lambda: tmp   # 重定向全部本地状态，绝不碰真实桌面目录
    return g


# ── 1. 名单解析与账号归一 ───────────────────────────────────────────────────

def test_parsing():
    print("\n═══ 1. 名单解析与账号归一 ═══")
    _assert_eq(_parse_accounts({"accounts": ["15088131371", " 13800138000 "]}),
               ["15088131371", "13800138000"], "dict 形态解析并去空格")
    _assert_eq(_parse_accounts(["+8613900139000"]), ["13900139000"], "list 形态 + 86 归一")
    _assert_eq(_parse_accounts(None), [], "None 输入返回空列表")
    _assert_eq(_parse_accounts({"accounts": ["", None, "abc"]}), [], "脏条目全部剔除")
    _assert_eq(_parse_accounts("非法结构"), [], "非 list/dict 返回空")
    _assert_eq(_normalize_account(" +86 150-8813-1371 "), "15088131371", "+86/空格/横线归一")
    _assert_eq(_normalize_account(""), "", "空账号归一为空")


# ── 2. check 双重验证核心语义 ───────────────────────────────────────────────

def test_check():
    print("\n═══ 2. check 双重验证 ═══")

    # 第一重：未绑定
    g = _make_guard(account=None, client=FakeClient())
    ok, reason = g.check()
    _assert(not ok and "请先绑定云展动力账号" in reason, "未绑定 → 拒绝并提示先绑定")

    # 第一重：格式错误
    g = _make_guard(account="12345", client=FakeClient())
    ok, reason = g.check()
    _assert(not ok and "格式有误" in reason, "绑定号格式错 → 拒绝")

    # 双重通过
    client = FakeClient(accounts=["15088131371"])
    g = _make_guard(account="15088131371", client=client)
    ok, reason = g.check()
    _assert(ok and reason == "", "已绑定 + 在名单 + 本机已登记 → 放行")
    # 首次判定的出网预算：名单 1 次 + 分片不存在 1 次 + 登记后回读确认 1 次 = 3。
    # 写死数字是为了让「多一次往返」变成会被抓住的改动：点击路径的等待时间
    # 全靠这个预算兜着，回归里没人盯就会一次次悄悄加上去。
    _assert_eq(client.calls, 3, "首次 check 出网 3 次（名单+查分片+回读确认）")

    # TTL 内不重复出网（连续点击 100 次不再增加请求）
    for _ in range(100):
        g.check()
    _assert_eq(client.calls, 3, "TTL 60s 内 100 次点击不再发 OSS 请求")

    # 自动登记的落盘形态（顶号的凭据本体，缺字段等于没有单点登录）
    shard = client.shards.get("15088131371") or {}
    _assert(shard.get("device_id") == ag.device_id()
            and bool(shard.get("session")) and shard.get("generation") == 1,
            "自动登记写入 device_id/session/generation")

    # 不在名单
    g = _make_guard(account="13700137000", client=FakeClient(accounts=["15088131371"]))
    ok, reason = g.check()
    _assert(not ok and "联系管理员获得使用权限" in reason, "不在白名单 → 拒绝并提示联系管理员")

    # +86 归一后仍可命中
    g = _make_guard(account="+8615088131371", client=FakeClient(accounts=["15088131371"]))
    ok, _ = g.check()
    _assert(ok, "绑定号带 +86 前缀归一后命中名单")

    # 网络失败 + 有磁盘副本 → 用副本判定
    g1 = _make_guard(account="15088131371", client=FakeClient(accounts=["15088131371"]))
    g1.check()   # 先成功一次，写磁盘副本与认领记录
    tmpfile = ag._cache_file()
    _assert(tmpfile.exists(), "成功拉取后磁盘副本已落盘")
    g2 = _make_guard(account="15088131371", client=FakeClient(fail=True),
                     state_dir=tmpfile.parent)
    ok, _ = g2.check()
    _assert(ok, "OSS 故障 + 名单副本 + 本机认领记录 → 已授权用户不受影响")
    g3 = _make_guard(account="13700137000", client=FakeClient(fail=True),
                     state_dir=tmpfile.parent)
    ok, _ = g3.check()
    _assert(not ok, "OSS 故障 + 副本里无此号 → 仍按名单拒绝（副本不放宽边界）")

    # 远端故障 + 从未成功 → 严格阻断（fail-closed），且文案按故障类别分开说
    # 1) 真·网络故障：还是那句「检查网络」，并承诺下次自动确认
    g = _make_guard(account="15088131371", client=FakeClient(
        fail=True, fail_msg="HTTPSConnectionPool: Read timed out"))
    ok, reason = g.check()
    _assert(not ok and "权限服务器" in reason and "检查网络" in reason,
            "从未成功拉取 + 网络超时 → 严格阻断，并如实叫人查网络")
    # 2) 服务端 5xx：不是用户的网络，也不该被说成网络
    g = _make_guard(account="15088131371", client=FakeClient(fail=True))
    ok, reason = g.check()
    _assert(not ok, "从未成功拉取 + 服务端 500 → 同样严格阻断（安全边界不分类别）")
    _assert("检查网络" not in reason,
            f"服务端异常不许被说成用户的网络问题（实际：{reason[:60]}）")
    _assert(AccessGuard.classify_reason(reason) == "other",
            "5xx 归到 other 类，界面按这一类决定下一步")

    # 名单文件不存在 → 自动播种初始名单
    client = FakeClient(missing=True, accounts=["15088131371"])
    g = _make_guard(account="15088131371", client=client)
    ok, _ = g.check()
    _assert(ok, "名单不存在 → 自动播种后放行")
    # uploaded 只记最后一次写（设备分片在名单播种之后写），断言播种内容必须按 key 取
    seeded = [d for k, d in client.writes if k == ag.ACL_KEY]
    _assert(bool(seeded) and "15088131371" in seeded[0]["accounts"], "播种内容含初始账号")
    _assert(bool(seeded) and seeded[0].get("schema") == ag.ACL_SCHEMA
            and seeded[0].get("grants"), "播种即 v3 形态（accounts + grants 双写）")
    _assert(bool(seeded) and seeded[0]["grants"].get("15088131371") == ag.LEVEL_ADMIN,
            "播种名单里初始账号是等级 0（唯一的管理入口持有者）")

    # TTL 过期 → 旧结果立即可用 + 后台刷新（UI 线程零卡顿语义）
    client = FakeClient(accounts=["15088131371"])
    g = _make_guard(account="15088131371", client=client, ttl=0.2)
    ok, _ = g.check()
    _assert(ok, "TTL 过期前放行")
    # 把「上次成功」推回 1 秒前制造过期。必须和守卫用同一个时钟基准：写
    # time.time() 的话这行在单调时钟下等于「远在将来」= 永远新鲜，测试会绿着
    # 什么都不测（这类假绿正是本轮在产物上反复撞到的形状）。
    g._fetched_at = time.monotonic() - 1.0
    t0 = time.time()
    ok, _ = g.check()
    elapsed = time.time() - t0
    _assert(ok and elapsed < 0.15, "TTL 过期 → 立即用旧名单放行且不出网阻塞（后台刷新）")


# ── 3. eventFilter 拦截语义 ─────────────────────────────────────────────────

def test_event_filter():
    print("\n═══ 3. eventFilter 全局拦截 ═══")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtCore import QEvent, Qt
    from PyQt5.QtGui import QKeyEvent
    from PyQt5.QtWidgets import QApplication, QPushButton

    app = QApplication.instance() or QApplication(sys.argv)

    class FakeBtn:
        def __init__(self, text):
            self._text = text
        def metaObject(self):
            class MO:
                def className(self_): return "QPushButton"
            return MO()
        def text(self): return self._text
        def isEnabled(self): return True
        def isVisible(self): return True
        def property(self, k): return None
        def window(self): return None

    space = QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.NoModifier)
    press_type = QEvent.MouseButtonPress

    blocked = AccessGuard()
    blocked.check = lambda: (False, "当前账号无使用权限，请联系管理员获得使用权限")
    blocked._notify = lambda *a: None

    passed = AccessGuard()
    passed.check = lambda: (True, "")
    hits = []
    passed._notify = lambda *a: hits.append(a)

    # 受拒：鼠标与键盘触发都被吞掉
    _assert(blocked.eventFilter(FakeBtn("搜索采购商"), space) is True, "无权限 + 键盘空格 → 拦截")
    ev = _FakeMouseEvent(press_type)
    _assert(blocked.eventFilter(FakeBtn("同步数据池"), ev) is True, "无权限 + 鼠标点击 → 拦截")

    # 放行：事件透传
    _assert(passed.eventFilter(FakeBtn("搜索采购商"), space) is False, "有权限 + 键盘 → 透传")
    _assert(passed.eventFilter(FakeBtn("搜索采购商"), _FakeMouseEvent(press_type)) is False,
            "有权限 + 鼠标 → 透传")

    # 豁免：绑定/停止/取消等永不拦截（被拒用户须能完成绑定与停任务）
    for text in ("绑定云展动力账号", "更换绑定账号", "停止", "暂停", "取消", "← 返回", "关闭"):
        _assert(blocked.eventFilter(FakeBtn(text), space) is False, f"豁免控件不拦截：{text}")

    # 窗口层豁免（实机死锁教训）：绑定对话框的 OK 按钮文字不在豁免词表，
    # 若按按钮层判定，不在名单的账号切不了账号——必须按窗口层放行
    class FakeTop:
        def __init__(self, cls, title):
            self._cls, self._title = cls, title
        def metaObject(self):
            cls = self._cls
            class MO:
                def className(self_): return cls
            return MO()
        def windowTitle(self): return self._title

    class BtnIn(FakeBtn):
        def __init__(self, text, top):
            FakeBtn.__init__(self, text)
            self._top = top
        def window(self):
            return self._top

    _assert(blocked.eventFilter(BtnIn("OK", FakeTop("QInputDialog", "绑定云展动力账号")),
                                _FakeMouseEvent(press_type)) is False,
            "绑定对话框内确认按钮 → 透传（防切号死锁）")
    _assert(blocked.eventFilter(BtnIn("OK", FakeTop("QMessageBox", "提示")), space) is False,
            "消息弹窗按钮 → 透传（只关弹窗不提权）")
    _assert(blocked.eventFilter(BtnIn("确定", FakeTop("QDialog", "选择导入分组")), space) is True,
            "普通业务对话框的确定按钮仍拦截")

    # 非按键事件不处理
    _assert(blocked.eventFilter(FakeBtn("搜索"), _FakeMouseEvent(QEvent.Paint)) is False,
            "非点击/按键事件不拦截")


class _FakeMouseEvent:
    def __init__(self, ev_type):
        self._t = ev_type
    def type(self): return self._t
    def key(self): return 0


# ── 4. 国内外推导（多信号加权） ────────────────────────────────────────────

def test_region_infer():
    print("\n═══ 4. 国内外归属推导 ═══")

    dom = {"company": "常州天合光能有限公司", "country": "中国",
           "address": "江苏省常州市新北区", "mobiles": ["13800138000"], "is_domestic": None}
    _assert(_infer_is_domestic(dom), "country=中国 + 江苏地址 + 中国手机 → 国内")

    intl = {"company": "ACME SOLAR INC", "country": "United States",
            "address": "California USA", "mobiles": ["+1 415 555 0132"],
            "emails": ["buy@acme.com"], "is_domestic": None}
    _assert(not _infer_is_domestic(intl), "国家US + 国外手机 → 国外")

    # 弱信号不得一票否决（记忆中清洗侧教训的同口径校验）
    noisy = {"company": "广州华美出口有限公司", "country": "",
             "address": "广州市天河区体育西路 伦敦贸易大厦 601",  # 音译噪声
             "mobiles": ["13800138000"], "emails": ["hua@huamei.cn"],
             "is_domestic": None}
    _assert(_infer_is_domestic(noisy), "中国公司 + 地址含英文音译噪声 → 仍判国内")

    landline_only = {"company": "Apple China 办事处", "country": "",
                     "address": "上海市", "landlines": ["021-63360000"],
                     "is_domestic": None}
    dom_s, intl_s = _domestic_signals(landline_only)
    _assert(dom_s >= intl_s, "外企中国座机：固话只给弱分不被误为强证据（打平默认国内）")

    zero_latin = {"company": "ABC", "is_domestic": None}
    _assert(not _infer_is_domestic(zero_latin),
            "零证据 + 纯拉丁公司名 → 判国外（旧口径在这里无条件默认国内，"
            "是存量 35,699 条国际展商被标成国内的直接原因）")
    zero_cn = {"company": "ABC 机电设备有限公司", "is_domestic": None}
    _assert(_infer_is_domestic(zero_cn), "零证据 + 中文公司名 → 判国内")
    _assert(_infer_is_domestic({"company": "", "is_domestic": None}),
            "连公司名都没有 → 保守回落国内")

    # 显式属地值只在「带当前判据版本标记」时才凌驾于推导之上：旧判据写下的
    # 派生值不带标记，必须允许重算，否则判据升级永远落不到存量数据上（自锁）
    stale = {"company": "任意", "is_domestic": False}
    _assert(_infer_is_domestic(stale),
            "无版本标记的裸 is_domestic 不再凌驾证据（按中文名重算判国内）")
    fresh = {"company": "任意", "is_domestic": False,
             "region_rule": REGION_RULE_VERSION}
    _assert(not _infer_is_domestic(fresh),
            "带当前版本标记的显式值仍被尊重（快捷路径与合并投票结论靠它保住）")

    # 北美电话不是中国手机号：1 + 区号 形态曾拿到 dom+2 强证据
    usa_toll = {"company": "Bureau Veritas", "phones": ["18883577020"],
                "address": "1412 Main Street, Dallas, TX, United States"}
    _assert(not _infer_is_domestic(usa_toll),
            "北美免费电话（1+区号888）不再被当成中国手机号判国内")
    cn_mobile = {"company": "Dyness", "phones": ["13143093303"],
                 "address": "No.688 Liupu Road, Wuxi, China"}
    _assert(_infer_is_domestic(cn_mobile),
            "中国手机 + 英文书写的中国地址 → 判国内（英文地址不等于国外）")

    # record_region 键与缓存
    r = {"company": "深圳某某科技有限公司", "address": "广东省深圳市",
         "mobiles": ["13900139000"], "is_domestic": None}
    _assert_eq(record_region(r), "dom", "record_region 国内键")
    r["is_domestic"] = None
    r.pop("_region_key", None)
    _assert_eq(r.get("_region_key"), None, "推导缓存键可被清理（写 OSS 前剥离）")

    gmail_foreign = {"company": "Green Valley Trading", "country": "India",
                     "emails": ["purchase@gmail.com"], "is_domestic": None}
    _assert(not _infer_is_domestic(gmail_foreign), "国家India + gmail → 国外")


# ── 5. 合并属地口径与 normalize ────────────────────────────────────────────

def test_merge_and_normalize():
    print("\n═══ 5. 合并属地口径（并集证据优先）+ normalize ═══")
    mgr = DataPoolManager.__new__(DataPoolManager)   # 不走 __init__，免 OSS 依赖
    base = dict(company="同源公司", company_norm="同源公司", phones=["13800000000"],
                emails=[], mobiles=["13800000000"], landlines=[], wechats=[],
                contacts=[], address="", products="", website="", industry="",
                source="t", source_time="", source_user="", country="中国")

    a = dict(base, is_domestic=True)
    b = dict(base, is_domestic=False)
    merged = mgr._merge_records([a, b, b])
    _assert_eq(len(merged), 1, "同名公司合并为 1 组")
    # 三条记录业务证据完全相同（country=中国 + 中国手机），只是来源自带的
    # is_domestic 标记不一致：属地只有一个口径——并集证据。拿显式值投票会把
    # 旧判据写下的结论当成事实，与 record_region/province/筛选口径分叉。
    _assert(merged[0]["is_domestic"] is True,
            "并集证据（中国手机+country=中国）压倒来源标记的票数，判国内")
    _assert_eq(merged[0]["region"], "dom", "region 与 is_domestic 同步（同一口径）")

    merged2 = mgr._merge_records([a, b, a])
    _assert(merged2[0]["is_domestic"] is True, "2 国内 1 国外 + 证据一致 → 判国内")

    # 零证据才退回投票：没有地址/号码/国名/域名后缀时，组内已有结论比瞎猜可靠
    # （website 用通用 .com：既过有效性门禁，又不产生任何属地信号）
    blank = dict(base, country="", address="", phones=[], mobiles=[],
                 landlines=[], emails=[], website="https://www.example.com")
    _assert_eq(_domestic_signals(dict(blank, company="Acme Trading")), (0, 0),
               "这组并集后确实零证据（境内境外得分都是 0）")
    zb = dict(blank, company="Acme Trading", is_domestic=False)
    merged_vote = mgr._merge_records([dict(blank, company="Acme Trading",
                                          is_domestic=True), zb, zb])
    _assert_eq(len(merged_vote), 1, "同名零证据公司合并为 1 组")
    _assert(merged_vote[0]["is_domestic"] is False,
            "并集后仍零证据 → 退回组内显式值多数投票（1 国内 2 国外判国外）")

    legacy = {k: v for k, v in base.items() if k != "is_domestic"}
    legacy.pop("is_domestic", None)
    legacy["country"] = ""
    legacy["address"] = "德国汉堡"
    legacy["is_domestic"] = None
    del legacy["is_domestic"]
    merged3 = mgr._merge_records([legacy])
    _assert("is_domestic" in merged3[0], "合并输出始终显式带 is_domestic 字段")

    # _normalize_is_domestic：补字段 + 剥运行时缓存键
    rec = {"company": "苏州工业园区精密制造厂", "address": "江苏省苏州市",
           "mobiles": ["13612345678"]}
    out = DataPoolManager._normalize_is_domestic([rec])
    _assert(out[0]["is_domestic"] is True, "normalize 推导国内并落显式字段")
    _assert("_region_key" not in out[0] and "_region_ver" not in out[0],
            "normalize 剥离下划线运行时键（不得写进 OSS/磁盘）")


# ── 6. apply_filters regions 多选 ──────────────────────────────────────────

def test_apply_filters_regions():
    print("\n═══ 6. apply_filters 国内外多选 ═══")
    from pool_matcher import CompanyMatcher
    recs = [
        {"company": "甲是国内公司", "is_domestic": True, "country": "中国",
         "address": "广东省深圳市", "mobiles": ["13800000001"]},
        {"company": "Beta Overseas Ltd", "is_domestic": False, "country": "Germany",
         "address": "Berlin", "mobiles": ["+49 30 123456"]},
        {"company": "丙是legacy缺字段", "country": "中国", "address": "浙江杭州",
         "mobiles": ["13800000003"]},
    ]
    r_dom = CompanyMatcher.apply_filters(recs, {"regions": ["dom"]})
    _assert_eq(len(r_dom), 2, "只勾国内 → 显式国内 + legacy 推导国内各 1 条")
    r_intl = CompanyMatcher.apply_filters(recs, {"regions": ["intl"]})
    _assert_eq(len(r_intl), 1, "只勾国外 → 仅国外记录")
    _assert_eq(r_intl[0]["company"], "Beta Overseas Ltd", "国外记录命中正确公司")
    r_both = CompanyMatcher.apply_filters(recs, {"regions": ["dom", "intl"]})
    _assert_eq(len(r_both), 3, "双选（全选）→ 不过滤")
    r_none = CompanyMatcher.apply_filters(recs, {})
    _assert_eq(len(r_none), 3, "空 filters → 行为不变（向后兼容）")
    r_empty_sel = CompanyMatcher.apply_filters(recs, {"regions": []})
    _assert_eq(len(r_empty_sel), 3, "regions 为空列表 → 视为不限")
    # regions 与其他条件叠加
    r_mix = CompanyMatcher.apply_filters(recs, {"regions": ["dom"], "has_email": True})
    _assert_eq(len(r_mix), 0, "国内 + 必须有邮箱 → 叠加生效")


def test_cli_show():
    """--show 的「生效档位」必须如实反映本机绑定账号的等级。

    曾经 --show 在「本机已绑定、等级也在名单里」时仍打印「等级未判定」——
    与 --whoami 自相矛盾，管理员据此核对线上权限分配会被误导。这条把 CLI 接线钉住：
    受限号（等级 2）在 pro 构建上应投影成精简版 lite，而不是停留在 pro。
    """
    print("\n═══ 7. --show 生效档位与绑定账号一致 ═══")
    import edition as _ed
    import io
    import contextlib

    def _run_show(account, client):
        # 隔离守卫单例与状态目录：_cli 用 get_guard()._bound_phone() 与
        # AccessGuard.load_document()（走 _build_client），两处都要注入替身。
        g = _make_guard(account=account, client=client)
        old_guard, ag._guard = ag._guard, g
        old_bc = AccessGuard.__dict__.get("_build_client")
        AccessGuard._build_client = staticmethod(lambda: client)
        old_acct = dict(_ed._ACCOUNT)
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                ag._cli(["--show"])
        finally:
            ag._guard = old_guard
            if old_bc is None:
                try:
                    del AccessGuard._build_client
                except Exception:
                    pass
            else:
                AccessGuard._build_client = old_bc
            _ed.set_account_level(old_acct.get("level"), old_acct.get("account", ""),
                                  reason=old_acct.get("reason", ""))
        return buf.getvalue()

    _bound = "13417047813"                       # 名单里的受限号（等级 2）
    cli = FakeClient(accounts=["15088131371", _bound, "19224997664"],
                     grants={"15088131371": 0, _bound: 2, "19224997664": 1})
    out = _run_show(_bound, cli)
    _assert("等级未判定" not in out,
            f"绑定受限号时 --show 不再谎报未判定  ({out.splitlines()[-1] if out.splitlines() else ''!r})")
    _assert("lite" in out, "等级 2 在 pro 构建上投影成精简版 lite（生效档位随等级变）")
    _assert(_bound in out, "输出点名是哪个绑定账号定的档位（可核对）")

    # 未绑定：必须诚实地停在「隐含」，不能凭空报某个具体等级
    cli2 = FakeClient(accounts=["15088131371"], grants={"15088131371": 0})
    out2 = _run_show(None, cli2)
    _assert(("本机未绑定" in out2) or ("等级未判定" in out2),
            "未绑定时如实说明按构建档位隐含，不假装已判定")


def main():
    print("═══ access_guard 双重验证 + 国内外链路 离线测试 ═══")
    test_parsing()
    test_check()
    test_event_filter()
    test_region_infer()
    test_merge_and_normalize()
    test_apply_filters_regions()
    test_cli_show()
    print(f"\n═══ 结果：{_PASS} 通过 / {_FAIL} 失败 ═══")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
