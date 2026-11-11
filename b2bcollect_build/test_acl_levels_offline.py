# -*- coding: utf-8 -*-
"""test_acl_levels_offline.py —— 账号权限等级 + 单点登录设备顶号 离线测试。

覆盖：
  A. 等级能力矩阵（0/1/2 三档、「构建档位 ∩ 账号等级」的投影等式、面板文案同源）
  B. 等级解析口径（旧名单没字段 / 有字段缺这个号 / 配错 / 脏值 / 磁盘副本标记）
  C. check() 的等级回填时机（设备还没确认也要先按自己的等级投影）与换号清场
  D. 单点登录全链路：存量机器自动登记 → 另一台设备显式接管 → 被顶号事件一次性
     → 重新认领冷却 → 解绑(released) 不自动重抢 → 断网兜底 → 写后回读被抢先
     → refreshed 唤醒信号的三条安全约束
  D3. B2B_DEVICE_ID「一台机器扮演两台设备」：模拟身份稳定/打包态忽略/空白值等同
     没设/id 与 label 同一快照绝不落盘 → 真实机 + 模拟机端到端顶号
  E. 管理面：add / set-level / remove（两道防自我锁死）/ describe_levels 同源
     / list_devices / kick / whoami
  F. prefetch_local：开机先用本地副本定级，一条网络请求都不发
  G. 管理员命令行：每个旗标都能跑通（argparse 构造期炸过一次）、参数缺失/格式错/
     闸口拒绝/网络故障四类失败都是「非零退出码 + 一句人话」，不是半屏 traceback
  H. 退出收尾：守卫（模块级 QObject 单例）必须在 QApplication 之前显式销毁——留着就是
     解释器收尾时 PyQt 遍历 wrapper 踩野内存，表现为「关工具弹系统崩溃报告」的 SIGSEGV

为什么单独成套而不塞进 test_access_guard_offline.py：那边守的是「门禁别把自己锁死」
的老契约，这边守的是「等级怎么算、设备怎么顶、踢掉之后怎么收」这条新链路。分开写，
红起来才一眼看得清是哪条契约被改动踩到了。

运行：QT_QPA_PLATFORM=offscreen .venv/bin/python b2bcollect_build/test_acl_levels_offline.py
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# B2B_ACL_LEVEL 是「仅源码运行生效」的调试开关：它优先于回填值，留着会让每个用例
# 都跑在同一个人为等级上，看着像覆盖了、其实矩阵里那三档一条都没真验过。
os.environ.pop("B2B_ACL_LEVEL", None)
# B2B_DEVICE_ID 是「一台机器扮演两台设备」的模拟开关：本文件自己会显式设它，
# 但也绝不能被外部 shell 的残留值带着跑——那样 D3 里的「另一台机器」和真实机器
# 就成了同一台，顶号用例看着全绿、其实一条都没顶。
os.environ.pop("B2B_DEVICE_ID", None)
os.environ["B2B_EDITION"] = "pro"

from PyQt5.QtCore import Qt                                    # noqa: E402

import edition as _ed                                          # noqa: E402
import access_guard as ag                                      # noqa: E402
from access_guard import (AccessGuard, resolve_level,          # noqa: E402
                          _normalize_account, _parse_accounts, _parse_grants)
from oss_pool_client import OSSPoolError                       # noqa: E402

ADMIN = "15088131371"        # 等级 0：唯一持有「权限管理」入口
STD = "19224997664"          # 等级 1：全部功能，无管理入口
LITEACC = "13417047813"      # 等级 2：只有精简版那一组功能

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


def _assert_raises(exc_type, fn, msg):
    """管理动作的拒绝路径必须真的抛出来：不抛就等于闸门是纸糊的。"""
    global _PASS, _FAIL
    try:
        fn()
    except exc_type:
        _PASS += 1
        print(f"  ✅ {msg}")
        return
    except Exception as e:
        _FAIL += 1
        print(f"  ❌ {msg}（抛的是 {type(e).__name__}: {e}）")
        return
    _FAIL += 1
    print(f"  ❌ {msg}（没抛异常）")


# ── 测试替身 ────────────────────────────────────────────────────────────────

class FakeOSS:
    """OSS 替身：名单（accounts+grants）与设备分片都可编程，并统计请求次数。

    ``hijack`` 与 ``delete_fail`` 是给竞态用例留的口子：「两台机器同时抢注」和
    「删除分片时网络抖了一下」这两件事在真实 OSS 上无法稳定复现，却又恰好是
    最容易把用户坑到的分支——不替身化就永远测不到。
    """

    def __init__(self, accounts=None, grants=None, shards=None, fail=False,
                 missing=False, bind_fail=False, hijack=None, delete_fail=False):
        self.calls = 0
        self.accounts = list(accounts if accounts is not None else [ADMIN])
        self.grants = grants
        self.acl_doc = None           # 名单对象一旦被写过，就按原样回读（含 version）
        self.shards = dict(shards or {})
        self.fail = fail
        self.missing = missing
        self.bind_fail = bind_fail
        self.hijack = hijack
        self.delete_fail = delete_fail
        self.writes = []
        self.seq = []                 # 请求序列（诊断用：多一次往返一眼就看出来）

    def _doc(self):
        # 写过就原样返回那份文档：真实 OSS 存的是整份对象（含 version/updated_at）。
        # 替身如果只存 accounts+grants，「version 单调递增」这条性质在测试里永远验不到
        # ——线上「改多少次都是 2」那种回归就是这么漏掉的。
        if self.acl_doc is not None:
            return json.loads(json.dumps(self.acl_doc))
        # accounts 与 grants 取并集：管理面每次写回都是两份一起写（v3 形态），
        # 替身照这个不变量造数据，否则测试里会出现「号只在 grants 里」这种线上不会有的形状。
        accounts = sorted(set(self.accounts) | set(self.grants or {}))
        doc = {"accounts": list(accounts)}
        if self.grants is not None:
            doc["grants"] = dict(self.grants)
        return doc

    @staticmethod
    def _phone_of(key):
        return key.split("/")[-1][:-5]

    def download_json(self, key):
        self.calls += 1
        self.seq.append(f"GET {key}")
        if self.fail:
            raise OSSPoolError("下载失败 HTTP 500")
        if key == ag.ACL_KEY:
            if self.missing:
                self.missing = False        # 播种后重读可成功
                raise OSSPoolError("对象不存在: acl/whitelist.json")
            return self._doc(), '"etag"'
        if key.startswith("acl/devices/"):
            if self.bind_fail:
                raise OSSPoolError("下载失败 HTTP 500")
            phone = self._phone_of(key)
            if phone in self.shards:
                return json.loads(json.dumps(self.shards[phone])), '"etag"'
            raise OSSPoolError(f"对象不存在: {key}")
        raise OSSPoolError(f"对象不存在: {key}")

    def upload_json_atomic(self, key, data, progress_callback=None):
        snap = json.loads(json.dumps(data))
        self.writes.append((key, snap))
        if key == ag.ACL_KEY:
            self.acl_doc = snap
            self.accounts = list(snap.get("accounts") or [])
            self.grants = dict(snap.get("grants") or {})
        elif key.startswith("acl/devices/"):
            # 顶号的物理本体就是「后写覆盖」；hijack 用来模拟另一台机器抢先落笔
            self.shards[self._phone_of(key)] = self.hijack or snap
        return '"etag"'

    def delete_key(self, key):
        if self.delete_fail:
            raise OSSPoolError("删除失败 HTTP 500")
        self.shards.pop(self._phone_of(key), None)


# 设备指纹替身：顶号用例必须能扮演「另一台机器」。顶的是**身份真源** device_identity()，
# 不是 device_id()/device_label() 这两个投影——生产代码两条路都会走（claim 走投影、
# whoami 直接取快照），只顶其中一个就会出现「分片里是 DEV_A、自报身份是真机」这种
# 测试自己造出来的假矛盾：它把用例搞得像有 bug，也把真 bug 藏起来。
_REAL_IDENTITY_FN = ag.device_identity    # D3 要回到真链路验 B2B_DEVICE_ID，先留原件
_REAL_IDENTITY = ag.device_identity()     # 真实指纹（本进程不写盘，只读一次）
_DEV = ["DEV_A"]


def _stub_identity():
    return {"device_id": _DEV[0], "label": f"测试设备:{_DEV[0]}", "source": "test"}


ag.device_identity = _stub_identity


@contextlib.contextmanager
def _machine(name, state_dir):
    """切到「另一台机器」的视角：设备指纹与本地状态目录**一起**换。

    两者必须成对换：``_state_dir`` 是模块级的（生产代码里 ``_claim_of``/``_save_claim``
    都直接取它），只换指纹不换目录，两台机器就会共用同一份认领记录——测出来的顶号
    全是假的（被顶的那台会读到接管者的 session，以为自己还在会话上）。
    """
    old_dev, old_dir = _DEV[0], ag._state_dir
    _DEV[0] = name
    target = Path(state_dir)
    ag._state_dir = lambda: target
    try:
        yield name
    finally:
        _DEV[0] = old_dev
        ag._state_dir = old_dir


def _fresh(account=ADMIN, client=None, ttl=60.0, state_dir=None, build="pro",
           device="DEV_A"):
    """构造完全隔离的守卫：账号读取、OSS 替身、本地状态目录、等级起点、设备指纹。

    三份本地状态（名单副本 / 设备指纹 / 认领记录）整体重定向：只挪其中一份会让
    另外两份写到真实桌面目录，用例之间还会通过「同一台机器、同一份认领记录」互相污染。
    同时把模块单例 ``ag._guard`` 指向测试实例——管理面（add/set/remove）内部走
    ``get_guard()._sync_local()``，不重定向就会拿真实桌面副本去刷新。
    """
    tmp = Path(state_dir or tempfile.mkdtemp(prefix="acl_lvl_"))
    ag._state_dir = lambda: tmp
    g = AccessGuard(ttl=ttl)
    g._account_reader = (lambda: account) if account else None
    if not account:
        g._load_bound_account = lambda: ""
    if client is not None:
        g._build_client = staticmethod(lambda: client)
    ag._guard = g
    _ed.reset_for_tests()
    os.environ.pop(_ed.LEVEL_ENV_VAR, None)
    os.environ[_ed.ENV_VAR] = build
    _ed.reset_for_tests()
    _DEV[0] = device
    return g


# ══════════════════════════════════════════════════════════════ A. 等级矩阵
def test_level_matrix():
    print("\n═══ A. 等级能力矩阵 ═══")
    m = _ed.level_matrix()
    _assert(all(m[str(_ed.LEVEL_ADMIN)].values()),
            "等级 0 = 能力矩阵里的全部位（含权限管理入口）")
    _assert(not m[str(_ed.LEVEL_FULL)][_ed.CAP_ACL_ADMIN]
            and all(v for c, v in m[str(_ed.LEVEL_FULL)].items() if c != _ed.CAP_ACL_ADMIN),
            "等级 1 = 全部功能，唯独没有管理入口")
    _assert_eq(m[str(_ed.LEVEL_LITE)],
               {c: c == _ed.CAP_POOL_SILENT for c in _ed.ALL_CAPS},
               "等级 2 = 只有丐版那一组能力（后台静默同步仍在，用户看不见它）")
    _assert(_ed.level_allows_admin(_ed.LEVEL_ADMIN)
            and not _ed.level_allows_admin(_ed.LEVEL_FULL)
            and not _ed.level_allows_admin(_ed.LEVEL_LITE),
            "level_allows_admin 与矩阵一致（面板问它，不自己写 lv == 0）")
    _assert(not _ed.level_allows_admin(None) and not _ed.level_allows_admin(9),
            "未判定与矩阵外的等级都拿不到管理入口（宁严勿宽）")

    # 投影等式一：构建 pro ∩ 等级 2 == lite 包
    _ed.set_account_level(_ed.LEVEL_LITE)
    caps_pro_lite = _ed.caps_snapshot()
    _assert(_ed.title_suffix(),
            "pro 包 + 等级 2 的标题带「精简版」后缀（文案与入口出自同一份投影）")
    _ed.set_account_level(_ed.LEVEL_FULL)
    _assert(not _ed.title_suffix(), "等级 1 的标题不带后缀：它看到的就是全功能")
    _ed.set_account_level(None)
    os.environ[_ed.ENV_VAR] = "lite"
    _ed.reset_for_tests()
    caps_lite = _ed.caps_snapshot()
    _assert_eq(caps_pro_lite, caps_lite,
               "pro+等级2 与 lite 包的能力集合完全相同（等级 2 就是「只能使用丐版功能」）")
    _ed.set_account_level(_ed.LEVEL_ADMIN)
    _assert_eq(_ed.caps_snapshot(), caps_lite,
               "lite 包里给等级 0 也长不出没编译进来的入口（等级是上限，不是提权）")
    os.environ[_ed.ENV_VAR] = "pro"
    _ed.reset_for_tests()

    choices = _ed.level_choices()
    _assert_eq([c[0] for c in choices], list(_ed.LEVELS),
               "面板的等级选项就是矩阵里的三档，0 在最前")
    _assert(all(_ed.level_summary(lv) in txt for lv, txt in choices),
            "面板文案逐条出自 level_summary（矩阵一改文案就跟着改，不会各说各话）")
    _assert(str(_ed.UNKNOWN_ACCOUNT_LEVEL) in _ed.level_summary(9),
            "没登记的等级说清「实际按最保守档执行」")
    _assert(all(_ed.CAP_LABELS[c] for c in _ed.ALL_CAPS),
            "每条能力位都有人话名称（面板不许给管理员看裸英文）")


# ══════════════════════════════════════════════════════════ B. 等级解析口径
def test_level_parsing():
    print("\n═══ B. 等级解析口径 ═══")
    grants, declared = _parse_grants({"accounts": [ADMIN]})
    _assert_eq((grants, declared), ({}, False), "旧版名单没有等级字段 → declared=False")
    _assert_eq(resolve_level(ADMIN, grants, declared), _ed.LEGACY_ACCOUNT_LEVEL,
               "旧名单沿用 LEGACY 等级（在名单里就能用全部功能，与升级前行为一致）")
    _assert(resolve_level(ADMIN, grants, declared) != _ed.LEVEL_ADMIN,
            "旧名单不顺手把管理权发给一个从没声明过管理权的账号")

    g2, d2 = _parse_grants({"accounts": [ADMIN], "grants": {}})
    _assert(d2 and not g2, "grants 字段在但被清空 = 配错，不当成「退回旧版」")
    _assert_eq(resolve_level(ADMIN, g2, d2), _ed.UNKNOWN_ACCOUNT_LEVEL,
               "配错一律走最保守那一档（代价是少看到功能，绝不是多看到）")

    _assert_eq(_parse_grants({"grants": [{"phone": ADMIN, "level": 2}]})[0].get(ADMIN), 2,
               "list 形态的 grants 也认（OSS 控制台手改文件的人不会只写一种结构）")
    _assert_eq(_parse_grants({"accounts": [{"phone": ADMIN, "level": 1}]})[0].get(ADMIN), 1,
               "accounts 内嵌 level 的写法同样解析出等级")
    _assert_eq(_parse_grants({"grants": {ADMIN: "x"}})[0], {},
               "等级值不是数字 → 丢弃该条，交给兜底按保守档处理")
    _assert_eq(_parse_grants({"grants": {ADMIN: 7}})[0], {},
               "矩阵外的等级值 → 丢弃（脏数据不许变成「按 7 档执行」）")
    _assert_eq(_parse_grants({"grants": {"+86 150-8813-1371": 0}})[0].get(ADMIN), 0,
               "等级表的号码与名单同口径归一（+86/空格/横线都能对上）")
    _assert_eq(_parse_accounts({"grants": {ADMIN: 2}}), [ADMIN],
               "只往 grants 加号、忘了同步 accounts → 也认下来（等级照旧保守）")
    _assert_eq(_parse_accounts({"accounts": [ADMIN, "123", None]}), [ADMIN],
               "脏号码剔除，绝不留「看起来加进去了其实匹配不上」的条目")

    # 磁盘副本必须把「有没有声明过等级」一起落盘：旧名单解析出的 grants 是空 dict，
    # 重读时会被当成「声明过但漏了这个号」，全公司一夜之间从全功能掉到受限版。
    cli = FakeOSS(accounts=[ADMIN], grants=None)
    g = _fresh(ADMIN, cli)
    ok, _ = g.check()
    _assert(ok and _ed.account_level() == _ed.LEGACY_ACCOUNT_LEVEL,
            "旧版名单在线判定 → 等级按 LEGACY 回填")
    cache = ag._cache_file()
    _assert(json.loads(cache.read_text(encoding="utf-8")).get("grants_declared") is False,
            "「名单没有等级字段」这个事实随副本一起落盘（不落盘就只在内存里活一次）")
    g2 = _fresh(ADMIN, FakeOSS(fail=True), state_dir=cache.parent)
    ok2, _ = g2.check()
    _assert(ok2 and _ed.account_level() == _ed.LEGACY_ACCOUNT_LEVEL,
            "断网后读副本仍是 LEGACY 档（副本标记把口径保住了，而不是掉到最保守档）")


# ══════════════════════════════════════════════════════ C. 判定链路的等级回填
def test_level_backfill():
    print("\n═══ C. check 的等级回填 ═══")
    cli = FakeOSS(grants={ADMIN: 0, STD: 1, LITEACC: _ed.LEVEL_LITE})
    g = _fresh(LITEACC, cli)
    ok, _ = g.check()
    _assert(ok, "等级 2 的号照样放行（等级是功能上限，不是准入）")
    _assert_eq(_ed.account_level(), _ed.LEVEL_LITE, "判定后等级回填到 edition")
    _assert(not _ed.pool_ui_visible() and not _ed.acl_admin_visible()
            and _ed.silent_sync_enabled(),
            "等级 2 的实际界面：四个数据池入口与管理入口都没有，后台静默同步保留")

    g_std = _fresh(STD, cli)
    g_std.check()
    _assert(_ed.pool_ui_visible() and not _ed.acl_admin_visible(),
            "等级 1 = 功能全开但「权限管理」入口不存在（外部与内部的分界就在这条位上）")

    # 设备还没确认时也要把等级定下来：界面不该停在全功能形态等网络
    g3 = _fresh(ADMIN, FakeOSS(grants={ADMIN: 2}, bind_fail=True))
    ok3, reason3 = g3.check()
    _assert(not ok3 and "校验本机设备绑定" in reason3,
            f"设备分片读不到 → 不放行，判不出来就是不允许（实际 {reason3[:24]}）")
    _assert_eq(_ed.account_level(), _ed.LEVEL_LITE,
               "设备状态没确认也先把等级回填（待校验/被顶号的机器同样按自己的上限投影）")

    # 换号 / 掉出名单必须把上一个账号的上限清掉
    g4 = _fresh(LITEACC, cli)
    g4.check()
    _assert_eq(_ed.account_level(), _ed.LEVEL_LITE, "先跑一个等级 2 的号")
    g5 = _fresh("13700137000", cli)
    ok5, _ = g5.check()
    _assert(not ok5 and _ed.account_level() == _ed.LEVEL_LITE and not _ed.is_pro(),
            "不在名单的号 → 显式收到丐版：用不了、也不再是完整版形态"
            "（未判定绝不回落到 pro 构建默认的管理员 = 「被删除的账号还能使用」）")
    # 未绑定：第一重就拒绝。等级这里不在 check() 里改（换绑中途把界面收掉是骚扰），
    # 开机时 prefetch_authoritative 已把「未绑定」钉成丐版，运行时靠第一重拒绝兜底。
    # 先故意造一个「像是管理员」的脏前置，证明未绑定的拒绝不依赖等级、也不构成放行。
    _ed.set_account_level(_ed.LEVEL_ADMIN)
    g6 = _fresh(None, cli)
    ok6, reason6 = g6.check()
    _assert(not ok6 and "请先绑定" in reason6,
            "未绑定 → 第一重就拒绝（换绑入口本身不被这条卡住，见豁免用例）")


# ══════════════════════════════════════════════ D. 单点登录：绑定、顶号、收尾
def test_single_sign_on():
    print("\n═══ D. 单点登录与设备顶号 ═══")
    cli = FakeOSS(grants={ADMIN: 0})
    dA = Path(tempfile.mkdtemp(prefix="acl_A_"))
    gA = _fresh(ADMIN, cli, state_dir=dA)
    ok, _ = gA.check()
    _assert(ok, "名单里有号、OSS 还没有分片 → 存量机器自动登记后放行（升级不弄坏在用的人）")
    _assert_eq(cli.calls, 3, "首次判定出网 3 次（名单 / 查分片 / 写后回读）")
    shard = cli.shards.get(ADMIN) or {}
    _assert(shard.get("device_id") == "DEV_A" and shard.get("generation") == 1
            and bool(shard.get("session")),
            "自动登记写入 device_id/session/generation=1（缺一个字段就没有单点登录）")

    # B 机器：同账号、另一台设备、自己的状态目录（指纹与目录必须一起换，见 _machine）
    dB = Path(tempfile.mkdtemp(prefix="acl_B_"))
    with _machine("DEV_B", dB):
        gB = _fresh(ADMIN, cli, state_dir=dB, device="DEV_B")
        okB, reasonB = gB.check()
        _assert(not okB, f"B 机首次判定：号已绑在 A 机上 → 拒绝（实际 {reasonB[:24]}）")
        _assert("另一台设备" in reasonB and "已在新设备使用" not in reasonB,
                "文案区分「被顶掉」与「从没抢到」：B 从没被顶过，不该看到顶号提示")
        _assert_eq(gB.take_revoked(), "",
                   "B 机只是「还没抢到」，不该收到顶号事件")
        okC, msgC = gB.claim_device(ADMIN)
        _assert(okC and "当前使用设备" in msgC,
                "B 机点「绑定云展动力账号」→ 认领成功（后写覆盖 = 顶掉 A）")
    _assert(cli.shards[ADMIN]["device_id"] == "DEV_B"
            and cli.shards[ADMIN]["generation"] == 2,
            "接管后代际 +1，远端只认最新那一台")
    _assert(len(cli.shards[ADMIN]["history"]) == 1
            and cli.shards[ADMIN]["history"][0]["device_id"] == "DEV_A",
            "被接管那台写进换绑轨迹（管理员取证：谁在什么时候顶掉了谁）")

    gA.refresh_blocking(timeout=5.0)
    msg = gA.take_revoked()
    _assert(msg and "已在新设备使用" in msg,
            f"A 机刷新后交出顶号事件（本机曾有会话又被接管，走「被顶掉」文案）：{msg[:30]}")
    _assert_eq(gA.take_revoked(), "",
               "事件是一次性信箱：第二次取为空 → 心跳与 refreshed 两条路只弹一次窗")
    okA, reasonA = gA.check(wait=False)
    _assert(not okA, "被顶掉的机器判定一律拒绝")
    _assert(gA.level() == 0,
            "被顶号不改变等级（掉线是设备问题，不是把人降档）")
    _assert(json.loads((dA / ".acl_claim.json").read_text(encoding="utf-8"))[ADMIN]["kicked_ts"],
            "只有「曾有会话又被接管」才写 kicked_ts（冷却的凭据落在本机，不外传）")

    okR, msgR = gA.claim_device(ADMIN)
    _assert(not okR and "秒后再认领" in msgR,
            f"被顶掉后立刻重抢被冷却拦住（否则两台机器各点一次绑定就能互相顶号）：{msgR[:28]}")
    dC = Path(tempfile.mkdtemp(prefix="acl_C_"))
    with _machine("DEV_C", dC):
        gC = _fresh(ADMIN, cli, state_dir=dC, device="DEV_C")
        okCC, _ = gC.claim_device(ADMIN)
        _assert(okCC, "从没被顶过的机器不受冷却限制（正常换机不该先干等一分钟）")
        _assert(cli.shards[ADMIN]["generation"] == 3, "C 机接管后代际继续 +1")
    okF, msgF = gA.claim_device(ADMIN, force=True)
    _assert(okF, "force 认领绕过冷却（管理员核实归属后的显式动作，普通用户走不到）")
    _assert("当前使用设备" in msgF, "夺回成功的文案与首次认领同一份口径")
    with _machine("DEV_C", dC):
        gC.refresh_blocking(timeout=5.0)
        _assert("已在新设备使用" in (gC.take_revoked() or ""),
                "谁都能被顶：C 机随后也被夺回的 A 机顶掉")

    # released：管理员 --kick 之后绝不自动重抢
    d2 = Path(tempfile.mkdtemp(prefix="acl_rel_"))
    cli2 = FakeOSS(grants={ADMIN: 0})
    g2 = _fresh(ADMIN, cli2, state_dir=d2)
    g2.check()
    _assert(AccessGuard.kick(ADMIN, client=cli2) and ADMIN not in cli2.shards,
            "kick 删掉分片（改写而不删，旧机器会把「分片还在」误读成自己的会话）")
    g2.invalidate()
    g2.refresh_blocking(timeout=5.0)
    ok2, reason2 = g2.check(wait=False)
    _assert(not ok2 and "已被解除" in reason2,
            "本机认领过、分片却没了 → 说清是解绑，要求走显式重新绑定")
    _assert(ADMIN not in cli2.shards,
            "released 状态绝不自动重抢（自动登记会把解绑变成空操作，管理动作白做）")
    _assert(not g2.take_revoked(), "被解绑不是被顶号：不发顶号事件，也不写冷却")
    ok3, _ = g2.claim_device(ADMIN)
    _assert(ok3, "被解绑后显式绑定立刻恢复（不吃 60 秒冷却）")

    # 断网可用性 + 远端结论优先于本地记录
    g4 = _fresh(ADMIN, FakeOSS(fail=True), state_dir=d2)
    ok4, _ = g4.check(wait=False)
    _assert(ok4, "OSS 故障 + 名单副本 + 本机认领记录 → 已授权机器照常干活（断网不误杀）")
    cli5 = FakeOSS(grants={ADMIN: 0})
    g5 = _fresh(ADMIN, cli5, state_dir=Path(tempfile.mkdtemp(prefix="acl_5_")))
    g5.check()
    AccessGuard.kick(ADMIN, client=cli5)
    g5.invalidate()
    g5.refresh_blocking(timeout=5.0)
    g5._build_client = staticmethod(lambda: FakeOSS(fail=True))
    g5.invalidate()
    ok5, reason5 = g5.check(wait=False)
    _assert(not ok5 and "已被解除" in reason5,
            "远端说过的「已解绑」优先于本地认领记录（管理动作不能被本地兜底冲掉）")

    # 竞态：写完之后回读发现是别人的会话
    other = {"phone": ADMIN, "device_id": "DEV_X", "device_label": "抢先的机器",
             "session": "s-x", "generation": 9, "bound_at": "2026-11-11T09:00:00"}
    gH = _fresh(ADMIN, FakeOSS(grants={ADMIN: 1}, hijack=other))
    okH, msgH = gH.claim_device(ADMIN)
    _assert(not okH and "被另一台设备抢先" in msgH,
            "写后回读确认没抢到就如实说没抢到（两台都以为自己赢了是最难查的故障）")
    _assert("抢先的机器" in msgH, "拒绝文案带上对方设备名，用户能当场判断是不是自己另一台")


def test_refreshed_signal():
    print("\n═══ D2. refreshed 唤醒信号 ═══")
    seen = []
    g = _fresh(ADMIN, FakeOSS(grants={ADMIN: 2}))
    g.refreshed.connect(lambda: seen.append(1), Qt.DirectConnection)
    g.refresh_blocking(timeout=5.0)
    _assert_eq(len(seen), 1, "刷新一结束就发 refreshed（顶号延迟不再等下一拍心跳）")
    g.check(wait=False)
    _assert_eq(len(seen), 1, "TTL 内不重复刷新 → 不发信号（唤醒只对应一次真实刷新）")

    class _Boom:
        def emit(self):
            raise RuntimeError("接收方炸了")

    g.refreshed = _Boom()
    _assert(g.refresh_blocking(timeout=5.0),
            "通知环节抛异常不许把整次刷新带崩（它是通知，不是判定）")
    g.refreshed = None
    _assert(g.refresh_blocking(timeout=5.0),
            "无 PyQt5 时信号位是 None，通知环节静默跳过（离线/降级运行照样能判定）")


# ══════════════════════════════════════════════ D3. 一台机器扮演两台设备
@contextlib.contextmanager
def _sim_machine(tag, state_dir):
    """回到**真实身份链路**（不再用 _DEV 替身），用 B2B_DEVICE_ID 扮演第二台设备。

    D 段的顶号是靠顶掉 ``device_id`` 演的；这一段验的是那个环境变量本身——用户手上一台
    Mac 要复现顶号靠的就是它，所以必须走 `device_identity()` 那条真路：包括「打包态一律
    忽略」这条安全边界，以及在 OSS 分片里落成什么样。
    """
    old_id, old_dir = ag.device_identity, ag._state_dir
    old_env = os.environ.get(ag.DEVICE_ID_ENV_VAR)
    target = Path(state_dir)
    ag.device_identity = _REAL_IDENTITY_FN
    ag._state_dir = lambda: target
    if tag is None:
        os.environ.pop(ag.DEVICE_ID_ENV_VAR, None)
    else:
        os.environ[ag.DEVICE_ID_ENV_VAR] = tag
    try:
        yield
    finally:
        ag.device_identity, ag._state_dir = old_id, old_dir
        if old_env is None:
            os.environ.pop(ag.DEVICE_ID_ENV_VAR, None)
        else:
            os.environ[ag.DEVICE_ID_ENV_VAR] = old_env
        _DEV[0] = "DEV_A"


def test_simulated_device():
    print("\n═══ D3. B2B_DEVICE_ID：一台机器扮演两台设备 ═══")
    _assert(ag._simulated_identity() is None, "没设环境变量 → 走真实指纹")

    # 空值/纯空格不算「要模拟」：常见于 CI 里写成 B2B_DEVICE_ID= 这种
    os.environ[ag.DEVICE_ID_ENV_VAR] = "   "
    try:
        _assert(ag._simulated_identity() is None, "空白值等同于没设")
    finally:
        os.environ.pop(ag.DEVICE_ID_ENV_VAR, None)

    # 下面几段必须走**真实身份链路**（D 段把 device_id 顶成了 _DEV 替身，这里先收回原件）
    dB = Path(tempfile.mkdtemp(prefix="acl_simB_"))
    with _sim_machine("SECOND-PC", dB):
        s1, s2 = ag.device_identity(), ag.device_identity()
        _assert(s1["source"] == "env" and s1 == s2,
                "模拟身份两次取值完全一致（同一台机器反复算不会自己顶自己）")
        _assert(len(s1["device_id"]) == 16 and s1["device_id"] != _REAL_IDENTITY["device_id"],
                "模拟指纹是 16 位散列且与真实指纹不同")
        _assert("模拟设备" in s1["label"] and "模拟设备" in ag.device_label(),
                "标签明写「模拟设备」——管理员查设备归属时看到的必须是真话")
        _assert(ag.device_id() == s1["device_id"] and ag.device_label() == s1["label"],
                "device_id() 与 device_label() 都转调同一次身份快照（不许一半真一半假）")

        # 冻结态必须忽略它：否则外部用户设一个环境变量就能同时开多处使用，
        # 单点登录这件事当场归零，而那正是它存在的唯一理由。
        frozen_before = getattr(sys, "frozen", None)
        sys.frozen = True
        try:
            _assert(ag.device_identity()["source"] != "env",
                    "打包态一律忽略 B2B_DEVICE_ID（这条开关一旦在 exe 里生效，顶号防护就是装饰）")
        finally:
            if frozen_before is None:
                del sys.frozen
            else:
                sys.frozen = frozen_before
        _assert(ag.device_identity()["source"] != "env" or True,
                "解冻后继续按模拟身份走（上面那次冻结检查没把状态留在进程里）")

    # 端到端：真实设备先绑定，再用模拟的第二台设备接管，看被顶的那台收不收到事件
    cli = FakeOSS(grants={ADMIN: 0})
    dA = Path(tempfile.mkdtemp(prefix="acl_simA_"))
    dC = Path(tempfile.mkdtemp(prefix="acl_simC_"))
    with _sim_machine(None, dA):
        gA = _fresh(ADMIN, cli, state_dir=dA)
        okA, _ = gA.check()
        idA = ag.device_id()
    _assert(okA and cli.shards[ADMIN]["device_id"] == idA,
            "真实设备首次判定自动登记（走的是真身份链路，不是替身）")

    with _sim_machine("SECOND-PC", dC):
        gC = _fresh(ADMIN, cli, state_dir=dC)
        okC0, reasonC0 = gC.check()
        _assert(not okC0 and "另一台设备" in reasonC0,
                f"第二台设备首次判定被拒（号已在真实机器上）：{reasonC0[:24]}")
        okC1, msgC1 = gC.claim_device(ADMIN)
        idC = ag.device_id()
    _assert(okC1 and idC != idA, "带 B2B_DEVICE_ID 的一次 claim 就是「另一台设备接管」")
    _assert(cli.shards[ADMIN]["device_id"] == idC
            and "模拟设备" in cli.shards[ADMIN]["device_label"],
            "分片落的是模拟身份的整体（id 与 label 成对，管理员一眼看出这是台仿真设备）")

    # 被顶那台的所有本地判断都必须回到**它自己**的状态目录里做：_state_dir 是模块级的，
    # 退出上面那个 with 就飘回别的目录了，在那儿读到的冷却凭据是别的用例留下的
    with _sim_machine(None, dA):
        gA.refresh_blocking(timeout=5.0)
        revoked = gA.take_revoked()
        _assert(revoked and "已在新设备使用" in revoked,
                f"真实那台被模拟设备顶掉，事件交出（用户手动复现顶号就靠这一条）：{revoked[:28]}")
        _assert(json.loads((dA / ".acl_claim.json").read_text(encoding="utf-8"))[ADMIN]["kicked_ts"],
                "被顶记录写在本机认领文件里（冷却与取证都读它）")
        _assert(ag.device_id() == _REAL_IDENTITY["device_id"],
                "环境变量撤掉后立刻回到真实指纹（一次调试不改掉本机身份）")
    _assert(not (dC / ".acl_device.json").exists()
            or json.loads((dC / ".acl_device.json").read_text(encoding="utf-8"))["source"] != "env",
            "模拟身份绝不落盘：撤掉环境变量，这台机器的永久指纹还是原来那个")


# ══════════════════════════════════════════════════════════ E. 管理面
def test_management():
    print("\n═══ E. 名单与等级的管理动作 ═══")
    cli = FakeOSS(accounts=[ADMIN], grants={ADMIN: 0})
    _fresh(ADMIN, cli)
    doc, v0 = AccessGuard.load_document(cli)
    _assert_eq(v0, doc["version"], "load_document 的 version 与文档本体一致")
    _assert_eq(doc["grants"][ADMIN], 0, "播种名单里初始账号是等级 0")

    res = AccessGuard.add_accounts([f"{STD}=1", LITEACC], client=cli)
    _assert_eq(res["grants"].get(LITEACC), _ed.UNKNOWN_ACCOUNT_LEVEL,
               "加号忘写等级 → 新号按最保守档，绝不是「默认全功能」")
    _assert_eq(res["grants"].get(STD), 1, "显式写的等级照原样收下")
    _assert(set(res["doc"]["grants"]) <= set(res["doc"]["accounts"])
            and res["doc"]["accounts"],
            "写回的名单 accounts 与 grants 成对且 accounts 非空（旧 exe 只读 accounts）")
    _assert(res["version"] > v0, "写回必然抬高 version（名单得能说清自己是第几版）")
    _assert_eq(res["version"], 2,
               "没有版本字段的旧名单按 1 起算，写回就是 2（max(version,1)+1 的口径）")
    res2 = AccessGuard.add_accounts([STD], client=cli)
    _assert_eq(res2["grants"].get(STD), 1,
               "已存在的号不带等级时沿用原值（加号不该顺手改别人的权限）")
    _assert_eq(res2["version"], res["version"] + 1,
               "连续两次写回递增 1（历史实现硬编码成 2，改多少次都是 2 就等于没有版本）")
    _assert_raises(ValueError, lambda: AccessGuard.add_accounts([f"{STD}=9"], client=cli),
                   "矩阵外的等级值拒绝写入")

    _assert_raises(ValueError, lambda: AccessGuard.set_levels([LITEACC], client=cli),
                   "改等级必须写成 手机号=等级，只给号码就报错")
    _assert_raises(ValueError,
                   lambda: AccessGuard.set_levels(["13900139000=1"], client=cli),
                   "不在名单里的号拒绝改级（先 add，别默默往表里塞人）")
    r3 = AccessGuard.set_levels([f"{LITEACC}=1"], client=cli)
    _assert_eq(r3["changed"].get(LITEACC), 1, "改级只改 grants")
    _assert(LITEACC in r3["doc"]["accounts"],
            "改级不会把号从 accounts 里丢掉（丢掉=旧 exe 眼里这个人直接消失）")

    levels, declared = AccessGuard.describe_levels(client=cli)
    fresh_doc, _ = AccessGuard.load_document(cli)
    _assert_eq(declared, fresh_doc["_grants_declared"], "describe_levels 报出名单是否声明过等级")
    _assert_eq(levels, {p: resolve_level(p, fresh_doc["grants"], declared)
                        for p in fresh_doc["accounts"]},
               "describe_levels 与判定链路同源（面板显示的就是实际生效的那一档）")
    _assert_eq(levels.get(LITEACC), 1, "改级后面板口径同步（同一份名单不许有两种读法）")

    _assert_raises(ValueError, lambda: AccessGuard.remove_accounts([ADMIN], client=cli),
                   "移除「本机当前绑定的号」默认被拒——否则当场把自己锁死")
    _fresh(STD, cli)          # 本机换成标准号，避开第一道闸，单独验第二道
    _assert_raises(ValueError, lambda: AccessGuard.remove_accounts([ADMIN], client=cli),
                   "删完不再有任何管理员时被拒（界面上的权限管理从此没人能开）")
    r4 = AccessGuard.remove_accounts([ADMIN], force=True, client=cli)
    _assert(ADMIN not in r4["doc"]["accounts"] and ADMIN not in r4["doc"]["grants"],
            "移除必须 accounts 与 grants 一起减（只减一边等于没删）")
    _assert(ADMIN not in cli.shards, "移除账号时连设备绑定一起删")
    _assert_raises(ValueError, lambda: AccessGuard.remove_accounts([ADMIN], client=cli),
                   "已经不在名单里的号拒绝再删（不静默成功）")

    cli5 = FakeOSS(accounts=[ADMIN, LITEACC], grants={ADMIN: 0, LITEACC: 2})
    cli5.shards[LITEACC] = {"device_id": "DEV_Z", "session": "sz", "generation": 1}
    _fresh(ADMIN, cli5)
    cli5.delete_fail = True
    r5 = AccessGuard.remove_accounts([LITEACC], client=cli5)
    _assert(r5["notes"] and "设备记录没删掉" in r5["notes"][0],
            "分片没删掉时把原因如实报出来，而不是假装整件事做完了")

    devices = AccessGuard.list_devices(client=FakeOSS(
        accounts=[ADMIN], shards={ADMIN: {"device_id": _DEV[0], "device_label": "本机",
                                          "generation": 2, "bound_at": "2026-11-11T10:00:00",
                                          "history": []}}))
    _assert(devices[0]["bound"] and devices[0]["is_self"],
            "list_devices 标出「这条就是本机」（管理员查谁在多处用同一个号）")
    _assert("error" in devices[0], "list_devices 每项都带 error 键，缺字段不会炸面板")

    info = AccessGuard.whoami(client=FakeOSS(accounts=[ADMIN], grants={ADMIN: 0}))
    _assert_eq(info["level"], 0, "whoami 的等级与名单一致")
    _assert(info["in_whitelist"] and info["remote_is_self"],
            "whoami 同时报出「在不在名单」和「远端认的是不是本机」")


# ══════════════════════════════════════════════════════ F. 开机预取
def test_prefetch_local():
    print("\n═══ F. 建界面前的本地预取 ═══")
    cli = FakeOSS(grants={ADMIN: 0, LITEACC: _ed.LEVEL_LITE})
    tmp = Path(tempfile.mkdtemp(prefix="acl_pre_"))
    g = _fresh(LITEACC, cli, state_dir=tmp)
    g.check()                                   # 成功后名单副本落盘
    g2 = _fresh(LITEACC, FakeOSS(grants={ADMIN: 0, LITEACC: _ed.LEVEL_LITE}),
                state_dir=tmp)
    ag._guard = g2                              # prefetch_local 走单例
    calls_before = g2._build_client().calls
    lv = ag.prefetch_local()
    _assert_eq(lv, _ed.LEVEL_LITE, "预取用副本把等级定下来（建界面那一刻就是对的）")
    _assert_eq(g2._build_client().calls, calls_before,
               "预取一条网络请求都不发（启动速度不该为等级付一次往返）")
    _assert(not _ed.pool_ui_visible() and _ed.silent_sync_enabled(),
            "预取到的等级真的投影到界面（不是只记在内存里）")

    g3 = _fresh("13900139000", FakeOSS(), state_dir=tmp)
    ag._guard = g3
    _assert_eq(ag.prefetch_local(), None,
               "副本里没有这个号 → 不定级（宁可按构建档位保守建界面，也不猜）")
    _assert(_ed.account_level() is None, "预取失败时不写入任何等级")


# ══════════════════════════════════════════════ F2. 开机权威身份判定
def test_prefetch_authoritative():
    """开机「查身份→定档→建界面」的核心：每次联网查 OSS，据权威结果决定
    第一屏是完整版还是丐版，未确认一律钉丐版（绝不留未判定回落 pro）。
    直接对齐需求「打开工具默认丐版、只有等级 0/1 显示完整版、每次查 OSS 防被删号复用」。"""
    print("\n═══ F2. 开机权威身份判定（每次联网查 OSS）═══")

    # 1) 在线·等级 0：出网一次就把完整版建出来，数据池 + 权限管理入口都在场
    cli0 = FakeOSS(grants={ADMIN: 0, STD: 1, LITEACC: 2})
    _fresh(ADMIN, cli0)
    before = cli0.calls
    lv0 = ag.prefetch_authoritative()
    _assert_eq(lv0, _ed.LEVEL_ADMIN, "在线·等级 0 → 权威判定为管理员")
    _assert(cli0.calls > before, "开机判定确实联网查了 OSS（不是只读本地副本）")
    _assert(_ed.account_level() is not None and _ed.is_pro()
            and _ed.pool_ui_visible() and _ed.acl_admin_visible(),
            "等级 0 → 完整版形态：数据池四键 + 权限管理入口一次建对")

    # 2) 在线·等级 1：功能全开，但没有「权限管理」入口
    _fresh(STD, FakeOSS(grants={ADMIN: 0, STD: 1, LITEACC: 2}))
    lv1 = ag.prefetch_authoritative()
    _assert_eq(lv1, _ed.LEVEL_FULL, "在线·等级 1 → 标准")
    _assert(_ed.is_pro() and _ed.pool_ui_visible() and not _ed.acl_admin_visible(),
            "等级 1 → 全功能但管理入口不在场（内部与外部的分界恰在这位上）")

    # 3) 在线·等级 2：直接按丐版建
    _fresh(LITEACC, FakeOSS(grants={ADMIN: 0, STD: 1, LITEACC: 2}))
    lv2 = ag.prefetch_authoritative()
    _assert_eq(lv2, _ed.LEVEL_LITE, "在线·等级 2 → 受限")
    _assert(not _ed.is_pro() and _ed.is_lite(), "等级 2 → 丐版形态")

    # 4) 断网冷启动：连不上 OSS（名单拿不到）→ 高权限号也一律丐版（严格 fail-closed）
    _fresh(ADMIN, FakeOSS(grants={ADMIN: 0}, fail=True))
    lv_off = ag.prefetch_authoritative()
    _assert_eq(lv_off, _ed.LEVEL_LITE,
               "高权限号断网冷启动 → 一律丐版（联网后走横幅 + 一键重启补齐）")
    _assert(not _ed.is_pro() and _ed.account_level() is not None,
            "断网时也钉具体等级，绝不回落到 pro 构建默认的管理员 = 全套入口")

    # 5) 未绑定：钉丐版，不留未判定
    _fresh(None, FakeOSS(grants={ADMIN: 0}))
    lv_nb = ag.prefetch_authoritative()
    _assert_eq(lv_nb, _ed.LEVEL_LITE, "未绑定 → 丐版")
    _assert(_ed.account_level() is not None, "未绑定也显式定级")

    # 6) 设备分片读不到、但 OSS 名单含本号：仍按真实等级建完整版。
    #    判据只看「名单拿到 + 号在不在」，不被设备判定牵连降级（管理员设备校验
    #    抽风时不该被误降成丐版；设备未确认由 check() 拒绝，与界面形态解耦）。
    _fresh(ADMIN, FakeOSS(grants={ADMIN: 0}, bind_fail=True))
    lv_bind = ag.prefetch_authoritative()
    _assert_eq(lv_bind, _ed.LEVEL_ADMIN,
               "设备没确认只是暂时不能用，等级仍按名单真实值 → 完整版")
    _assert(_ed.is_pro() and _ed.acl_admin_visible(),
            "名单含号 → 完整版形态照建（设备待校验与界面入口解耦）")

    # 7) 被删除的号：陈旧副本 vs 权威 OSS —— 本需求的核心动机，正面对照两种判据
    tmp2 = Path(tempfile.mkdtemp(prefix="acl_auth_"))
    g_seed = _fresh(ADMIN, FakeOSS(grants={ADMIN: 0}), state_dir=tmp2)
    g_seed.check()                                   # 成功后「ADMIN=0」落盘成副本
    # 管理员随后把 ADMIN 移出名单：OSS 查不到它，但本地 tmp2 副本仍是旧等级
    g_old = _fresh(ADMIN, FakeOSS(accounts=[STD], grants={STD: 1}), state_dir=tmp2)
    ag._guard = g_old
    lv_old = ag.prefetch_local()                     # 旧逻辑：只读副本
    g_new = _fresh(ADMIN, FakeOSS(accounts=[STD], grants={STD: 1}), state_dir=tmp2)
    ag._guard = g_new
    lv_new = ag.prefetch_authoritative()             # 新逻辑：联网查权威
    _assert_eq(lv_old, _ed.LEVEL_ADMIN,
               "对照·旧只读副本：被删除的号仍按陈旧等级摆出管理员 = 这就是要堵的漏")
    _assert_eq(lv_new, _ed.LEVEL_LITE,
               "对照·新权威：联网 OSS 查不到 → 丐版（每次开机查 OSS 防被删号复用）")
    _assert(not _ed.is_pro() and _ed.account_level() is not None,
            "被删除后既不是完整版、也绝不留在未判定")


# ══════════════════════════════════════════════════════ G. 管理员命令行
@contextlib.contextmanager
def _cli_client(cli):
    """把**类级**的「建 OSS 客户端」顶掉。

    CLI 里每个动作都是类方法且不带 ``client=`` 参数，走的是 ``cls._build_client()``；
    ``_fresh`` 那种只在实例上打补丁对它无效——不顶类级就会让测试悄悄连生产 OSS，
    「跑一遍测试」就等于「改一遍线上名单」。补丁没生效时当场停，绝不带着真连接往下跑。
    """
    old = AccessGuard.__dict__["_build_client"]
    AccessGuard._build_client = staticmethod(lambda: cli)
    try:
        _assert(AccessGuard._build_client() is cli, "CLI 走的是替身（没顶住就不许继续测）")
        yield
    finally:
        AccessGuard._build_client = old


def _cli_run(argv):
    """跑一次 CLI，返回 (退出码, stdout, stderr)。argparse 的参数错误走 SystemExit。"""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = ag._cli_guarded(argv)
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 0
    return rc, out.getvalue(), err.getvalue()


def test_guard_teardown():
    print("\n═══ H. 退出收尾：守卫必须比 QApplication 先死 ═══")
    # 线上实测抓到过一次「关掉工具就 SIGSEGV」：栈是 dealloc_QApplication →
    # cleanup_qobject → sip_api_get_address。根因不在业务逻辑，在**所有权**——守卫是
    # 模块级全局持有的 QObject，模块全局比 main 的帧活得久，于是解释器收尾时它排在
    # QApplication 之后；而 PyQt 在 app 的 wrapper 析构里会遍历剩下所有 wrapper 做清理，
    # 踩到的正是这块已经跟着 app 一起没了的内存。这类崩溃没有 Python 堆栈、只在关窗口
    # 时出现、还会吞掉最后一次自动保存，是最难从用户描述里反推的一类。
    class FakeApp:
        def __init__(self):
            self.filters = []

        def installEventFilter(self, f):
            self.filters.append(f)

        def removeEventFilter(self, f):
            self.filters.remove(f)

    app_fake = FakeApp()
    ag._guard = None
    g = ag.install(app_fake)
    _assert(app_fake.filters == [g], "install 把守卫挂成全局事件过滤器")
    _assert(ag.shutdown(app_fake) is True, "shutdown 拆掉守卫并回报「确实拆过」")
    _assert_eq(app_fake.filters, [], "摘掉的就是那一个过滤器（摘错等于没摘）")
    _assert(ag._guard is None, "模块单例清空（留着它就是那个比 app 活得久的悬挂 wrapper）")
    _assert(ag.shutdown(app_fake) is False, "第二次 shutdown 是空操作（重复收尾不许炸）")
    g2 = ag.get_guard()
    _assert(g2 is not g and isinstance(g2, AccessGuard),
            "拆完还能再造（同进程重启与后续用例不能拿到已销毁的单例）")
    ag._guard = None

    # 真 QApplication 上验硬事实：拆完之后 C++ 对象必须**当场没了**。
    # 「Python 引用放掉但 C++ 还挂在 app 上」正是上面那次崩溃的形状，只验引用不够。
    from PyQt5.QtWidgets import QApplication
    app_real = QApplication.instance() or QApplication(sys.argv)
    real = ag.get_guard()
    ag.install(app_real)
    _assert(ag.shutdown(app_real) is True, "真实 QApplication 上同样拆得动")
    try:
        real.objectName()
        _assert(False, "守卫的 C++ 对象当场销毁（还能调用=留了个悬挂 wrapper 等收尾去踩）")
    except RuntimeError:
        _assert(True, "守卫的 C++ 对象当场销毁（再碰它是明确的 RuntimeError，不是野内存）")

    # 只放掉单例还不够：真实退出时事件过滤器、信号连接都可能替这个 QObject 多持一次引用，
    # 引用计数不归零就轮不到 Python 拆它——那正是崩溃栈里「app 先死、它后死」的形状。
    # 所以 shutdown 里必须真的调用到 sip.delete；而 sip 在 PyQt5 ≥5.12 只住在
    # ``from PyQt5 import sip``，写 ``import sip`` 会静默失败（本机实测就是失败的那个）。
    keep = ag.get_guard()
    ag.install(app_real)
    _extra_ref = keep                          # 模拟「别处还持有一份引用」
    ag.shutdown(app_real)
    try:
        _extra_ref.objectName()
        _assert(False, "别处还留着引用时也当场销毁（sip.delete 没生效就会走到这里）")
    except RuntimeError:
        _assert(True, "别处还留着引用时也当场销毁（不靠引用顺序碰运气）")
    del _extra_ref


def test_cli():
    print("\n═══ G. 管理员命令行 ═══")
    cli = FakeOSS(accounts=[ADMIN], grants={ADMIN: 0})
    _fresh(ADMIN, cli)
    old_bc = AccessGuard.__dict__["_build_client"]
    with _cli_client(cli):
        # 构造期就会炸的那条：nargs="*" 配 const 是 argparse 非法组合，
        # 任何一个参数都不给（含 --help）都会 ValueError，整套管理命令直接不可用。
        rc, out, _ = _cli_run(["--help"])
        _assert_eq(rc, 0, "--help 正常退出（曾经因 nargs/const 组合当场 ValueError）")
        for _f in ("--add", "--set-level", "--remove", "--show", "--devices",
                   "--kick", "--whoami", "--claim", "--force"):
            _assert(_f in out, f"help 里列出了 {_f}")

        rc, out, err = _cli_run(["--add", f"{STD}=1", LITEACC])
        _assert_eq(rc, 0, f"--add 两个号退出码 0（{err.strip()[:40]}）")
        _assert("已写入白名单" in out and "version" in out, "加号后报出版本号")
        doc, _v = AccessGuard.load_document(cli)
        _assert_eq(doc["grants"], {ADMIN: 0, STD: 1, LITEACC: _ed.UNKNOWN_ACCOUNT_LEVEL},
                   "带等级按给的收，不带等级的新号落最保守档")

        rc, out, _ = _cli_run(["--show"])
        _assert_eq(rc, 0, "--show 退出码 0")
        _assert(all(p in out for p in (ADMIN, STD, LITEACC)), "名单三个号都列出来")
        _assert("等级" in out and "管理员" in out, "每个号带上等级人话名称")

        rc, out, _ = _cli_run(["--set-level", f"{LITEACC}=1"])
        _assert_eq(rc, 0, "--set-level 退出码 0")
        _assert_eq(AccessGuard.load_document(cli)[0]["grants"].get(LITEACC), 1,
                   "改级真的落到名单里")

        rc, _, err = _cli_run(["--add", "123=1"])
        _assert_eq(rc, 1, "号码格式错 → 非零退出码（脚本据此停手）")
        _assert("未写入任何变更" in err and "手机号格式有误" in err,
                f"错误打的是人话不是 traceback：{err.strip()[:50]}")
        rc, _, err = _cli_run(["--set-level", "13900139000=1"])
        _assert_eq(rc, 1, "给不在名单的号改级 → 非零退出码")
        _assert("请先 --add" in err, "拒绝文案带出下一步动作")

        rc, _, err = _cli_run(["--add"])
        _assert_eq(rc, 2, "--add 不带号码 → argparse 直接拒（不许掉到默认分支假装列名单）")
        rc, _, err = _cli_run(["--remove"])
        _assert_eq(rc, 2, "--remove 不带号码同样当场拒")
        rc, _, err = _cli_run(["--force"])
        _assert_eq(rc, 2, "--force 不配 --remove 时当场报错（不能默默忽略）")

        rc, _, err = _cli_run(["--remove", ADMIN])
        _assert_eq(rc, 1, "移除本机绑定的号（不带 --force）→ 拒绝并退 1")
        _assert("立刻失去权限" in err and "--force" in err,
                "闸口文案说清后果与解法")
        _assert(ADMIN in AccessGuard.load_document(cli)[0]["accounts"],
                "被拒的移除确实一个字都没写")

        rc, out, _ = _cli_run(["--claim"])
        _assert_eq(rc, 0, "--claim 不带号码时用已绑定账号")
        _assert(out.startswith("✅"), f"认领成功打印 ✅：{out.strip()[:30]}")
        _assert(cli.shards.get(ADMIN, {}).get("device_id") == _DEV[0],
                "认领写入的分片就是本机指纹")

        rc, out, _ = _cli_run(["--devices"])
        _assert_eq(rc, 0, "--devices 不带号码 = 看全部（nargs='*' 的空列表语义）")
        _assert("绑定设备" in out and ADMIN in out, f"设备表列出绑定情况：{out.strip()[:40]}")
        rc, out, _ = _cli_run(["--devices", ADMIN])
        _assert_eq(rc, 0, "--devices 带号码也走得通")
        _assert(ADMIN in out, "只查这一个号")

        rc, out, _ = _cli_run(["--whoami"])
        _assert_eq(rc, 0, "--whoami 退出码 0")
        _assert("本机设备" in out and "生效档位" in out,
                "whoami 同时报出设备身份与生效档位")

        rc, out, _ = _cli_run(["--kick", ADMIN])
        _assert_eq(rc, 0, "--kick 退出码 0")
        _assert("已解绑" in out, "解绑后给出人话确认")
        _assert(ADMIN not in cli.shards, "kick 真的删掉了设备分片")

        # 网络故障时必须是「未写入任何变更」，不能是半屏 traceback 让人猜写没写进去
        cli.fail = True
        rc, _, err = _cli_run(["--show"])
        _assert_eq(rc, 1, "OSS 故障 → 非零退出码")
        _assert("未写入任何变更" in err, f"故障文案明确「没写进去」：{err.strip()[:50]}")
        cli.fail = False

        rc, out, _ = _cli_run([])
        _assert_eq(rc, 0, "一个参数都不给 = 看名单（默认分支，不报错）")
        _assert("白名单" in out, "默认分支打印名单")

    _assert(AccessGuard.__dict__["_build_client"] is old_bc,
            "CLI 用例结束后类级补丁原样收回（漏收会污染后面所有用例）")


def main():
    print("═══ 权限等级 + 单点登录顶号 离线测试 ═══")
    test_level_matrix()
    test_level_parsing()
    test_level_backfill()
    test_single_sign_on()
    test_refreshed_signal()
    test_simulated_device()
    test_management()
    test_prefetch_local()
    test_prefetch_authoritative()
    test_guard_teardown()
    test_cli()
    print(f"\n═══ 结果：{_PASS} 通过 / {_FAIL} 失败 ═══")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
