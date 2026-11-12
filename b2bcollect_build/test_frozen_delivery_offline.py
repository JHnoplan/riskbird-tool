# -*- coding: utf-8 -*-
"""test_frozen_delivery_offline.py —— 打包态交付链离线测试（凭证链 / 故障分类 / 发包门禁）。

覆盖：
  A. tool_config 解析链：优先级、合并语义、空值不遮蔽、frozen 与源码两种形态
  B. OSS 凭证：环境变量 > 统一链 > 开发机 .m（**打包态必须禁用**），失败必须是
     OSSCredentialError 而不是被压成网络故障
  C. access_guard 故障分类：探测分类、文案分类、classify_reason 反查唯一口径、
     _must_client 管理入口分类
  D. 界面口径：凭证类失败不得说成「还在确认中」，被拒类失败不得指向白名单，
     认领失败不得承诺「网络恢复后自动补齐」
  E. DeepSeek 与缓存：打包态密钥读得到、缓存与配置写包外、pool_matcher 同源
  F. --acl-doctor：退出码分类，且不通的原因说清楚
  G. 交付链：Secret 注入 → 随包 config.json → 产物门禁；三个打包出口（Windows
     workflow / Mac workflow / 本机打包脚本）的 PyInstaller 清单逐项一致，
     .gitignore 里 config.json 仍在（密钥绝不入库）
  H. 本地状态落盘：首选目录写不进去要有备用落点，备用也没有就把后果说清楚；
     写失败不许静默（它是断网时唯一能用的身份凭据），且失败后能自愈回首选
  I. 时钟偏差与出网收口：签名时刻偏出容忍带时按服务端时间自愈重签（只重试一次）；
     加密连接被中途拦下单独一类；出网失败一律在唯一通道里转成可分类的错误
  J. 时间基准：进程内新鲜度与有界等待走单调时钟，落盘时间戳落在将来按不可信处理
     ——改系统时间不许让「被删的账号还能用」，也不许把 3 秒等待变成几小时卡死

为什么单独成套：本仓库其它离线测试都在**源码态**跑，而这一轮外部机器全员精简版
的根因恰恰是「源码态通、打包态不通」——开发机恰好能从本机 iOS 工程读到密钥，
所以从第一次打包到现在都测不出来。这一套的职责就是**在离线机器上把打包态模拟
出来**：sys.frozen / _MEIPASS / sys.executable 三个开关一拨，凭证链就必须按
产物的规矩说话。

运行：QT_QPA_PLATFORM=offscreen .venv/bin/python b2bcollect_build/test_frozen_delivery_offline.py
"""
import contextlib
import inspect
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from email.utils import formatdate, parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_REPO = Path(__file__).resolve().parent.parent

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.pop("B2B_ACL_LEVEL", None)
os.environ.pop("B2B_DEVICE_ID", None)
os.environ["B2B_EDITION"] = "pro"

import edition as _ed                                          # noqa: E402
import access_guard as ag                                      # noqa: E402
import tool_config as tc                                       # noqa: E402
import oss_pool_client as oss                                  # noqa: E402
import deepseek_expander as dse                                # noqa: E402
from access_guard import AccessGuard                           # noqa: E402
from oss_pool_client import (OSSPoolError, OSSCredentialError,    # noqa: E402
                             OSSTransportError)

# 源码文本：有些约束是「只许有一处」型的（出网只许走 _send、路径策略只许问
# edition），运行时看不出漏了第二处，只有在文本上钉住才不会日后悄悄多出一份。
_OSS_SRC = (_REPO / "b2bcollect_build" / "oss_pool_client.py").read_text(encoding="utf-8")
_AG_SRC = (_REPO / "b2bcollect_build" / "access_guard.py").read_text(encoding="utf-8")

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


def _write(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


# ── 打包态模拟器 ────────────────────────────────────────────────────────────

@contextlib.contextmanager
def _workspace(with_exe_config=None, with_bundle_config=None, env_config=None):
    """搭一套「 exe 同级 / 随包 / 环境变量指定」三级都可控的临时目录。

    刻意同时支持 frozen 与非 frozen：同三级路径在两种形态下的**语义**不同
    （源码态没有「程序同级」这一级），必须两种都摆出来才测得出优先级差异。

    同时把 access_guard 的本地状态目录（名单副本 / 设备指纹 / 认领记录）整体
    迁到临时目录：那三份文件在开发机上是**真实存在**的，`_load_disk_once()` 会
    从副本里拿到 accounts 从而绕开「包里没带凭证」这一支——不隔离的话，本套最
    关键的几条断言在开发机上会假绿，换一台机器又变成随机红。
    """
    root = Path(tempfile.mkdtemp(prefix="frozen_ws_"))
    exe_dir = root / "ToolDir"
    bundle = root / "MEIPASS"
    local_state = root / "local_state"
    exe_dir.mkdir(parents=True, exist_ok=True)
    bundle.mkdir(parents=True, exist_ok=True)
    local_state.mkdir(parents=True, exist_ok=True)
    if with_exe_config is not None:
        _write(exe_dir / "config.json", with_exe_config)
    if with_bundle_config is not None:
        _write(bundle / "config.json", with_bundle_config)
    env_file = None
    saved = {k: os.environ.get(k) for k in
             ("B2B_CONFIG_FILE", "B2B_STATE_DIR",
              "OSS_ACCESS_KEY_ID", "OSS_ACCESS_KEY_SECRET", "DEEPSEEK_API_KEY")}
    if env_config is not None:
        env_file = root / "given" / "config.json"
        _write(env_file, env_config)
        os.environ["B2B_CONFIG_FILE"] = str(env_file)
    for k in ("OSS_ACCESS_KEY_ID", "OSS_ACCESS_KEY_SECRET", "DEEPSEEK_API_KEY"):
        os.environ.pop(k, None)
    stack = contextlib.ExitStack()
    try:
        stack.enter_context(_patch(ag, "_state_dir", lambda: local_state))
        yield {"root": root, "exe_dir": exe_dir, "bundle": bundle,
               "env_file": env_file, "local_state": local_state}
    finally:
        stack.close()
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@contextlib.contextmanager
def _frozen(ws, state_dir=None):
    """把当前进程伪装成 PyInstaller 打包态。

    三个开关缺一不可：``sys.frozen`` 是本仓库所有 frozen 判定的唯一依据，
    ``sys._MEIPASS`` 决定「随包」这一级指向哪，``sys.executable`` 决定「程序同级」
    与「包旁」指向哪。少拨一个，测的就不是打包态了。
    """
    old = (getattr(sys, "frozen", None), getattr(sys, "_MEIPASS", None),
           sys.executable)
    saved_env = os.environ.get("B2B_STATE_DIR")
    sys.frozen = True
    sys._MEIPASS = str(ws["bundle"])
    sys.executable = str(ws["exe_dir"] / "B2bCollectTool.exe")
    if state_dir is not None:
        os.environ["B2B_STATE_DIR"] = str(state_dir)
    try:
        yield ws
    finally:
        (f, m, e) = old
        if f is None:
            if hasattr(sys, "frozen"):
                del sys.frozen
        else:
            sys.frozen = f
        if m is None:
            if hasattr(sys, "_MEIPASS"):
                del sys._MEIPASS
        else:
            sys._MEIPASS = m
        sys.executable = e
        if saved_env is None:
            os.environ.pop("B2B_STATE_DIR", None)
        else:
            os.environ["B2B_STATE_DIR"] = saved_env


@contextlib.contextmanager
def _patch(obj, name, value):
    """显式补丁 + 保证收回：漏收会污染后面所有用例（本仓库栽过一次）。"""
    old = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, old)


AKV = {"oss_access_key_id": "AKID_BUNDLE", "oss_access_key_secret": "SK_BUNDLE"}


# ══════════════════════════════════════════════ A. 配置解析链
def test_config_chain():
    print("\n═══ A. tool_config 解析链（打包态必须与开发态同权）═══")

    with _workspace(with_bundle_config=AKV) as ws, _frozen(ws):
        _assert(tc.is_frozen(), "frozen 模拟生效（sys.frozen + _MEIPASS）")
        _assert_eq(tc.bundle_dir(), ws["bundle"],
                   "随包目录取 sys._MEIPASS（不是模块目录）")
        _assert_eq(tc.exe_dir(), ws["exe_dir"].resolve(),
                   "程序同级目录取 sys.executable 的父目录")
        _assert_eq(tc.get("oss_access_key_id"), "AKID_BUNDLE",
                   "只随包带配置时也读得到（这就是 CI 注入后的真实形态）")
        _assert_eq(tc.source_for("oss_access_key_id"), "随包内置",
                   "来源标签如实报「随包内置」，排障时能一眼看出命中的是哪一级")

    # 同级覆盖随包：不发新版也能就地修好一台机器
    with _workspace(with_exe_config={"oss_access_key_id": "AKID_SIDE",
                                     "oss_access_key_secret": "SK_SIDE"},
                    with_bundle_config=AKV) as ws, _frozen(ws):
        _assert_eq(tc.get("oss_access_key_id"), "AKID_SIDE",
                   "exe 同级配置覆盖随包配置（side-by-side 优先）")
        _assert_eq(tc.source_for("oss_access_key_id"), "程序同级",
                   "覆盖命中后来源标签也跟着改")

    # 非成组的键可以跨级补：排障时在 exe 同级只放一个键，其余键仍从随包取到
    with _workspace(with_exe_config={"some_future_toggle": "off"},
                    with_bundle_config=dict(AKV, some_future_toggle="on")) as ws, \
            _frozen(ws):
        merged = tc.load()
        _assert_eq(merged.get("some_future_toggle"), "off",
                   "同级给的那个普通键以同级为准")
        _assert_eq(merged.get("oss_access_key_id"), "AKID_BUNDLE",
                   "同级没给的键继续从随包补齐（合并语义，不是第一个文件说了算）")

    # 成组的密钥**绝不允许**跨级拼合：半份新密钥 + 半份旧密钥 = 必然签名不符，
    # 症状与「没配密钥」不同却更难查（会被归成 denied，两边互相指）
    with _workspace(with_exe_config={"oss_access_key_secret": "SK_SIDE"},
                    with_bundle_config=AKV) as ws, _frozen(ws):
        merged = tc.load()
        _assert_eq((merged.get("oss_access_key_id"), merged.get("oss_access_key_secret")),
                   ("AKID_BUNDLE", "SK_BUNDLE"),
                   "同级只给 secret 时整级跳过：不许把同级的半把密钥与随包的半把拼一起")
        _assert_eq(tc.source_for("oss_access_key_secret"), "随包内置",
                   "来源报告说的是真正生效的那一级")
        warns = tc.warnings()
        _assert(any("OSS 密钥对" in w and "程序同级" in w for w in warns),
                f"配不全这件事必须写进 warnings（否则就是静默失败）：{warns}")

    # 空值不遮蔽
    with _workspace(with_exe_config={"oss_access_key_id": "  "},
                    with_bundle_config=AKV) as ws, _frozen(ws):
        _assert_eq(tc.get("oss_access_key_id"), "AKID_BUNDLE",
                   "同级写了个空串不算「已提供」：随包的值不许被空值遮掉")

    # 环境变量指定的文件优先级最高
    with _workspace(with_exe_config=AKV, with_bundle_config=AKV,
                    env_config={"oss_access_key_id": "AKID_ENVFILE",
                                "oss_access_key_secret": "SK_ENVFILE"}) as ws, _frozen(ws):
        _assert_eq(tc.get("oss_access_key_id"), "AKID_ENVFILE",
                   "B2B_CONFIG_FILE 指定的文件压过一切（临时排障专用口子）")
        _assert_eq(tc.source_for("oss_access_key_id"), "环境变量指定",
                   "来源标签如实报「环境变量指定」")

    # 源码态：没有「程序同级」这一级，且 describe 只报文件名不报内容
    with _workspace(with_bundle_config=AKV) as ws:
        _assert(not tc.is_frozen(), "未模拟时是源码态")
        _assert_eq(tc.exe_dir(), None,
                   "源码态刻意不给「程序同级」，避免与随包候选混成同一目录")
        desc = tc.describe()
        _assert("AKID" not in desc and "SK_" not in desc,
                "describe 绝不带出配置内容（密钥不能进日志）")

    # 坏 JSON 不许把整条链炸掉
    with _workspace(with_bundle_config=AKV) as ws, _frozen(ws):
        (ws["exe_dir"] / "config.json").write_text("{不是合法 JSON", encoding="utf-8")
        _assert_eq(tc.get("oss_access_key_id"), "AKID_BUNDLE",
                   "同级配置是坏 JSON 时退到随包配置（一处脏数据不许废掉整条链）")

    # 环境变量与文件同级参与解析：来源报告必须跟着改成「环境变量」，
    # 否则自检说「随包内置」、实际用的是别人塞进进程的变量，远程排障两头对不上
    with _workspace(with_bundle_config=AKV) as ws, _frozen(ws):
        os.environ["OSS_ACCESS_KEY_ID"] = "AKID_ENV"
        os.environ["OSS_ACCESS_KEY_SECRET"] = "SK_ENV"
        _assert_eq(tc.source_for("oss_access_key_id"), "环境变量",
                   "命中环境变量时来源标签如实说「环境变量」")
        _assert("环境变量=有" in tc.describe(),
                "describe 把环境变量这一级也列出来（优先级最高的一级不能漏报）")
        # 只给半个密钥变量的常见误配：整级跳过，退到随包的完整一对（绝不拼合）
        os.environ.pop("OSS_ACCESS_KEY_SECRET", None)
        _assert_eq((tc.get("oss_access_key_id"), tc.get("oss_access_key_secret")),
                   ("AKID_BUNDLE", "SK_BUNDLE"),
                   "只设了 OSS_ACCESS_KEY_ID 时不许拼出「env 的 id + 文件的 secret」")

    # 来源不许缓存上一次解析：先取值、后出现的环境变量也必须被来源认出来。
    # （CI 与 GUI 的调用顺序本来就不同，缓存过一次就会说出一个没被用上的级）
    with _workspace(with_bundle_config=AKV) as ws, _frozen(ws):
        _assert_eq(tc.get("oss_access_key_id"), "AKID_BUNDLE",
                   "先按文件链取一次值（让「上一次的结论」真实存在）")
        os.environ["OSS_ACCESS_KEY_ID"] = "AKID_ENV"
        os.environ["OSS_ACCESS_KEY_SECRET"] = "SK_ENV"
        _assert_eq(tc.source_for("oss_access_key_id"), "环境变量",
                   "取值之后才出现的环境变量，来源跟着改（不许返回缓存）")
        _assert_eq(tc.get("oss_access_key_id"), "AKID_ENV",
                   "来源与值出自同一次解析：两边说法必须一致")

    # 环境变量键名表只有一份口径：oss_pool_client 的退路必须与主表逐项相等
    _assert_eq(dict(oss.ENV_VAR_OF_KEY), dict(tc.ENV_KEY_MAP),
               "oss_pool_client 的退路表与 tool_config 主表逐项相等（改一边忘另一边就红）")


# ══════════════════════════════════════════════ B. OSS 凭证解析
def test_oss_credentials():
    print("\n═══ B. OSS 凭证：打包态禁用机器专属兜底 ═══")

    _assert(issubclass(OSSCredentialError, OSSPoolError),
            "OSSCredentialError 是 OSSPoolError 的子类（上层现有 except 全部继续成立）")

    # 1) 环境变量最高优先级
    with _workspace(with_bundle_config=AKV) as ws, _frozen(ws):
        os.environ["OSS_ACCESS_KEY_ID"] = "AKID_ENV"
        os.environ["OSS_ACCESS_KEY_SECRET"] = "SK_ENV"
        _assert_eq(oss.resolve_credentials(), ("AKID_ENV", "SK_ENV"),
                   "环境变量压过随包配置")
        _assert_eq(oss.credential_origin(), "环境变量",
                   "credential_origin 报出「环境变量」")

    # 2) 随包配置（CI 注入后的真实形态）
    with _workspace(with_bundle_config=AKV) as ws, _frozen(ws):
        _assert_eq(oss.resolve_credentials(), ("AKID_BUNDLE", "SK_BUNDLE"),
                   "打包态只靠随包 config.json 就能拿到密钥（本轮修复的正身）")
        _assert_eq(oss.credential_origin(), "随包内置",
                   "credential_origin 报出「随包内置」")

    # 3) 打包态**绝不**回落到开发机 .m 文件——哪怕这台机器上真有一份
    with _workspace() as ws, _frozen(ws):
        real_m_exists = any(p.is_file() for p in oss._M_FILE_CANDIDATES)
        with _patch(oss, "_read_ak_from_m_file",
                    lambda: ("AKID_FROM_M", "SK_FROM_M")):
            err = None
            try:
                oss.resolve_credentials()
            except OSSCredentialError as e:
                err = e
            _assert(err is not None,
                    "打包态无任何配置时必须抛 OSSCredentialError"
                    f"（本机 .m {'存在' if real_m_exists else '不存在'}，"
                    "这条断言正是靠它才不是空转）")
            _assert(err is not None and "打包工作流" in str(err),
                    "打包态的失败文案指向打包工作流（处理人是发包方，不是终端用户）")
            _assert(err is not None and "Secret" in str(err),
                    "文案直接给出该配哪两个 Secret，发包方不用再去翻代码")
            _assert(err is not None and "检查网络" not in str(err),
                    "缺密钥的文案绝不混进「请检查网络」")

    # 4) 源码态保留 .m 兜底（开发便利不能被这次修复打掉）
    with _workspace():
        with _patch(oss, "_read_ak_from_m_file",
                    lambda: ("AKID_FROM_M", "SK_FROM_M")):
            _assert_eq(oss.resolve_credentials(), ("AKID_FROM_M", "SK_FROM_M"),
                       "源码态仍然读得到开发机 .m（老开发流程不受影响）")
            _assert_eq(oss.credential_origin(), "开发机 iOS 工程文件",
                       "来源如实标注「开发机 iOS 工程文件」")

    # 5) 源码态彻底没配时的文案要指向本地可操作的动作
    with _workspace() as ws:
        with _patch(oss, "_read_ak_from_m_file", lambda: (None, None)):
            try:
                oss.resolve_credentials()
                _assert(False, "源码态没配密钥时应当抛错")
            except OSSCredentialError as e:
                _assert("config.json" in str(e) and "打包工作流" not in str(e),
                        "源码态文案指向本机 config.json/环境变量，不叫人改 workflow")


# ══════════════════════════════════════════════ C. 故障分类
def test_failure_taxonomy():
    print("\n═══ C. 权限服务故障分类（不再压成「网络问题」）═══")

    # 1) 探测分类：打包态没密钥 = credential，而不是 None
    with _workspace() as ws, _frozen(ws):
        client, kind, detail = AccessGuard._client_probe()
        _assert(client is None and kind == "credential",
                f"没凭证的打包态被分类为 credential（实际 {kind}）")
        _assert("未找到 OSS 密钥" in (detail or ""),
                "detail 带上底层原因，日志里能直接看到证据")

    # 2) check() 的 reason 不能再是「检查网络」
    with _workspace() as ws, _frozen(ws):
        _ed.reset_for_tests()
        g = AccessGuard(ttl=60.0)
        g._account_reader = lambda: "13417047813"
        ok, reason = g.check(wait=False)
        _assert(not ok, "没凭证时不放行（fail-closed 的安全边界必须守住）")
        _assert("不是你的网络问题" in reason,
                f"reason 明说这不是网络问题（实际：{reason[:70]}）")
        _assert("请检查网络后重试" not in reason,
                "reason 不再把发包缺陷伪装成用户侧网络故障")
        _assert_eq(g.service_status()[0], "credential",
                   "service_status 记下类别，界面/自检读同一个口径")

    # 3) 网络类仍然要说成网络（别把修复做成「什么都是发包的错」）
    _assert("检查网络" in AccessGuard._unreachable_text("network"),
            "network 类文案仍然让人查网络")
    _assert_eq(AccessGuard._unreachable_text("whatever"),
               AccessGuard._unreachable_text("network"),
               "认不出的类别退到网络文案兜底，绝不返回空话")

    # 4) OSS 错误归类
    cases = [
        ("OSSPoolError: HTTP 403 Forbidden", "denied"),
        ("SignatureDoesNotMatch 签名不符", "denied"),
        ("InvalidAccessKeyId 密钥不存在", "denied"),
        ("HTTPSConnectionPool: Read timed out", "network"),
        ("Failed to establish a new connection", "network"),
        ("NameResolutionError getaddrinfo failed", "network"),
        # 加密连接被中途拦下（安全软件网页过滤 / 公司代理 / 包里缺根证书）：
        # 报文里同时带着 HTTPSConnectionPool 与证书错误，判据必须**先认证书**，
        # 否则就退化成「请检查网络」——而这类机器网络好得很，用户会一直查下去。
        ("下载失败（加密连接）: SSLError: [SSL: CERTIFICATE_VERIFY_FAILED] "
         "certificate verify failed: unable to get local issuer certificate", "tls"),
        ("请求失败（加密连接）: SSLError: [SSL: WRONG_VERSION_NUMBER] wrong version number",
         "tls"),
        ("某种没见过的服务端错误", "other"),
    ]
    for text, want in cases:
        _assert_eq(AccessGuard._classify_oss_error(OSSPoolError(text))[0], want,
                   f"「{text[:28]}…」归为 {want}")
    # 「确定性失败」的判据只许有一个主人：判据写在两个地方，迟早一处认为该重试、
    # 另一处认为不该重试，而表现出来就是「同一台机器有时秒回、有时卡十几秒」。
    _assert_eq(len(re.findall(r"_is_permanent_failure\(", _OSS_SRC)), 2,
               "永久失败判据只有定义 + _send 一处调用（重试与否只在一个地方决定）")

    # 5) reason ↔ 类别 的反查表与文案表同源（界面靠它决定下一步）
    #    类别清单**从表里取**而不是写死：写死的话，以后新增一类故障（这次的时间
    #    偏差就是新增的）只要忘了在这里加一行，就会带着空文案上线——而这正是本轮
    #    要消灭的「某一类故障没被单独说话」。
    #    四张表的关系也一次说清（差集必须是确定的那一类，不能靠记忆）：
    #      · 文案表 ↔ 退出码表 严格同一批类别：每类故障都既有说法又有专属退出码；
    #      · 反查表 / 下一步表 少一个 network：网络类是**兜底**类，它不靠关键词认出
    #        来（认不出来就是它），文案本身已经写了「请检查网络后重试」。
    text_kinds = set(AccessGuard._UNREACHABLE_TEXT)
    _assert_eq(text_kinds, set(ag._DOCTOR_KIND_CODE),
               "每类故障都有文案，也都有 --acl-doctor 专属退出码（门禁红得能看出修哪层）")
    _assert_eq(set(AccessGuard._REASON_MARKERS), text_kinds - {"network"},
               "除兜底的网络类以外，每类都有可反查的关键词")
    _assert_eq(set(AccessGuard.SERVICE_NEXT_STEP), text_kinds - {"network"},
               "除兜底的网络类以外，每类都有「下一步该谁做什么」")
    for kind in sorted(text_kinds):
        _assert_eq(AccessGuard.classify_reason(AccessGuard._unreachable_text(kind)),
                   kind if kind != "network" else "",
                   f"{kind} 类文案归类唯一（网络类是兜底，不该被关键词反查命中）")
    _assert_eq(AccessGuard._unreachable_text("whatever"),
               AccessGuard._unreachable_text("network"),
               "认不出的类别退到网络文案兜底，绝不返回空话")
    _assert_eq(AccessGuard.classify_reason(
        AccessGuard._admin_client_failure("credential", "")), "credential",
        "管理入口的失败文案也在同一张反查表里")
    _assert_eq(AccessGuard.classify_reason("不在白名单，按丐版收口"), "",
               "非服务类 reason 不误伤成服务类")

    # 6) 管理入口分类：_must_client 抛的必须是分类过的 RuntimeError
    with _workspace() as ws, _frozen(ws):
        _assert_raises(RuntimeError, lambda: AccessGuard._must_client(),
                       "打包态没凭证时管理动作当场抛错（不静默返回 None）")
        try:
            AccessGuard._must_client()
        except RuntimeError as e:
            _assert_eq(AccessGuard.classify_reason(str(e)), "credential",
                       "管理动作的报错也能反查出类别，管理员一眼知道该修哪一层")

    # 7) 顶号/认领路径同样分类（旧写法一律「无法连接权限服务器，设备登记失败」）
    with _workspace() as ws, _frozen(ws):
        g2 = AccessGuard(ttl=60.0)
        ok2, msg2 = g2.claim_device("13417047813")
        _assert(not ok2 and "不是你的网络问题" in msg2,
                 f"设备登记失败也说清类别（实际：{msg2[:70]}）")

    # 8) 客户端能建起来时才谈得上网络问题
    class _FakeClient:
        bucket = "b"
        endpoint = "e"

        def download_json(self, key):
            raise OSSPoolError("HTTP 403 Forbidden")

    with _workspace(), _patch(AccessGuard, "_build_client",
                              staticmethod(lambda: _FakeClient())):
        g3 = AccessGuard(ttl=60.0)
        g3._account_reader = lambda: "13417047813"
        _assert(g3._fetch() is False, "被拒的刷新返回失败")
        _assert_eq(g3.service_status()[0], "denied",
                   "403 被记成 denied 而不是 network（管理员不必再去翻名单）")
        _assert("拒绝了本次访问" in g3._unreachable_reason(),
                "真被拒过之后给的是「密钥/RAM 权限」口径，不是「检查网络」")
        # 没试过的时候不能瞎指：客户端建得起来、又没有任何失败证据，只能诚实说网络
        g4 = AccessGuard(ttl=60.0)
        _assert("检查网络" in g4._unreachable_reason(),
                "客户端可构造但尚未试过 → 退到网络口径，不把猜测写成发包方的错")


# ══════════════════════════════════════════════ D. 界面口径
def _misleads_to_whitelist(text: str) -> bool:
    """这段文案是否在**指示**用户去动白名单（而不是告诉他动了也没用）。

    「加白名单都不起作用」「改白名单没有用」这两句是正确口径里必须出现的否定句，
    所以不能简单用「白名单」子串判对错——那样等于把最有用的那句排除掉，剩下的
    断言只会放过真正危险的写法。判据改成：提到白名单的地方必须紧邻否定词。
    """
    t = text or ""
    i = t.find("白名单")
    while i >= 0:
        seg = t[max(0, i - 16):i + 16]
        if not any(neg in seg for neg in ("不起作用", "没有用", "不用", "无关", "不必")):
            return True
        i = t.find("白名单", i + 1)
    return False


def test_gui_wording():
    print("\n═══ D. 界面下一步口径与故障类别同源 ═══")
    import b2bcollect_gui as gui

    cred = AccessGuard._unreachable_text("credential")
    mod = AccessGuard._unreachable_text("module")
    denied = AccessGuard._unreachable_text("denied")

    # 判据本身先自证：漏判（永远 False）比断言红更危险
    _assert(_misleads_to_whitelist("请联系管理员把你的手机号加入白名单"),
            "判据能认出真正的误导写法")
    _assert(not _misleads_to_whitelist("本地改网络、重新绑定、加白名单都不起作用"),
            "判据不会把否定句误判成误导")

    t, adv = gui.MainWindow._acl_denial(cred)
    _assert("凭证" in t and "自动" not in adv,
            f"缺凭证的拒绝标题点名凭证，且不承诺会自动补齐（{t}）")
    _assert(not _misleads_to_whitelist(adv), "缺凭证不叫人去找管理员加白名单")

    t2, adv2 = gui.MainWindow._acl_denial(mod)
    _assert("模块" in t2, f"缺模块的拒绝标题点名模块（{t2}）")

    # 这条是本轮最容易漏的顺序缺陷：被拒文案里带着「不用改白名单」，
    # 而白名单分支在它前面就会把人引去改白名单
    t3, adv3 = gui.MainWindow._acl_denial(denied)
    _assert("拒绝" in t3 and not _misleads_to_whitelist(adv3),
            f"被拒（403）不再被当成「没进白名单」（{t3}｜{adv3[:24]}）")

    # 网络类维持原口径：仍然承诺下一次刷新自动确认
    t4, adv4 = gui.MainWindow._acl_denial(
        "无法连接权限服务器，请检查网络后重试；仍无法使用请联系管理员")
    _assert("确认中" in t4 and "自动" in adv4,
            "网络类仍然是「权限还在确认中」，这次修复没把它改坏")

    # 每一类**可归类**的故障都要在界面上有自己的一句标题。遍历现表而不是写死清单：
    # 这次新增的时间偏差、加密连接就是这样被漏掉的——分类表加一行，界面那张 map
    # 不会自动跟着长，用户看到的还是那句含糊的「权限服务不可用」。
    for kind in sorted(AccessGuard._REASON_MARKERS):
        tk, ak = gui.MainWindow._acl_denial(AccessGuard._unreachable_text(kind))
        _assert(tk != "账号已绑定，但权限服务不可用",
                f"{kind} 类在界面上有专属标题，不掉回通用兜底话术（{tk}）")
        _assert(not _misleads_to_whitelist(tk) and not _misleads_to_whitelist(ak),
                f"{kind} 类的标题与下一步都不把用户指向白名单")
        _assert_eq(ak, AccessGuard.SERVICE_NEXT_STEP[kind],
                   f"{kind} 类的下一步就是分类表里那一条（界面不另写一份）")

    # 认领失败：凭证类不许落在「网络恢复后自动补齐登记」那一支
    tc_, bc = gui.MainWindow._claim_failure("13417047813", f"设备登记失败：{cred}")
    _assert("凭证" in tc_ and "自动补齐" not in bc,
            f"登记失败也如实说不会自动补齐（{tc_}）")
    _assert("13417047813" in bc, "认领失败的正文仍带上刚绑定的账号")

    # 真正该自动补齐的两类保持原样（防止把修复做成一刀切）
    tc2, bc2 = gui.MainWindow._claim_failure("13417047813", "无法读取该账号的绑定状态，请稍后重试")
    _assert("自动补齐" in bc2, "网络型登记失败仍然指向真实存在的自动补登记路径")


# ══════════════════════════════════════════════ E. DeepSeek 与缓存
def test_deepseek_and_cache():
    print("\n═══ E. DeepSeek 密钥与缓存（打包态读得到、写包外）═══")

    key = {"deepseek_api_key": "DS_FROM_BUNDLE"}
    with _workspace(with_bundle_config=dict(AKV, **key)) as ws, _frozen(ws):
        _assert_eq(dse.load_api_key(), "DS_FROM_BUNDLE",
                   "打包态随包 config.json 里的 DeepSeek 密钥读得到")
        from pool_matcher import CompanyMatcher
        _assert_eq(CompanyMatcher._load_api_key(), "DS_FROM_BUNDLE",
                   "pool_matcher 与 deepseek_expander 同一个口径（不再各读各的）")
        cf = dse.cache_file()
        _assert(str(ws["bundle"]) not in str(cf),
                f"缓存落在包外（实际 {cf}）——onefile 的临时目录每次退出就删")
        _assert(str(ws["exe_dir"]) in str(cf),
                "打包态缓存优先落在程序同级（用户看得见、也拷得走）")

    # 环境变量优先
    with _workspace(with_bundle_config=key) as ws, _frozen(ws):
        os.environ["DEEPSEEK_API_KEY"] = "DS_FROM_ENV"
        _assert_eq(dse.load_api_key(), "DS_FROM_ENV", "环境变量压过随包配置")

    # 缓存读写真的落到包外并且能读回来
    with _workspace(with_bundle_config=key) as ws, _frozen(ws):
        state = ws["root"] / "state"
        with _patch(dse, "cache_file", lambda: state / "deepseek_cache.json"):
            dse._cache.clear()
            dse._cache["k"] = ["a", "b"]
            dse._save_cache()
            _assert((state / "deepseek_cache.json").is_file(),
                    "缓存写到包外状态目录（不是 _MEIPASS 临时目录）")
            dse._cache.clear()
            _assert_eq(dse._load_cache().get("k"), ["a", "b"],
                       "缓存能读回来：否则「避免重复调用 API」这句话是空的")
            dse.clear_cache()
            _assert(not (state / "deepseek_cache.json").exists(),
                    "clear_cache 清的是同一个落点")

    # 坏缓存不许把功能炸掉
    with _workspace(with_bundle_config=key) as ws, _frozen(ws):
        bad = ws["root"] / "bad.json"
        bad.write_text("[\"不是对象\"]", encoding="utf-8")
        with _patch(dse, "cache_file", lambda: bad):
            dse._cache.clear()
            _assert_eq(dse._load_cache(), {},
                       "顶层是列表的脏缓存退回空表（旧写法会留下 AttributeError 隐患）")

    # save_api_key 的落点必须与读取链同源
    with _workspace() as ws:
        target = ws["root"] / "given" / "config.json"
        _write(target, {"placeholder": "1"})
        os.environ["B2B_CONFIG_FILE"] = str(target)
        try:
            _assert(dse.save_api_key("DS_SAVED"), "保存 DeepSeek 密钥返回成功")
            _assert_eq(dse.load_api_key(), "DS_SAVED",
                       "保存完立刻能从同一口径读回（界面提示「已保存」才是真话）")
            _assert("DS_SAVED" in target.read_text(encoding="utf-8"),
                    "写到的是那一份配置文件，没有另起一处影子配置")
        finally:
            os.environ.pop("B2B_CONFIG_FILE", None)

    # 源码态缓存仍在模块目录（历史缓存不搬家）
    _assert(str(Path(dse.cache_file())) .startswith(str(_REPO)),
            "源码态缓存留在仓库内，不因为这次改造把历史缓存搬丢")


# ══════════════════════════════════════════════ F. --acl-doctor
class _DoctorOSS:
    """自检用的假客户端：读可以，**任何写/删/改都必须当场炸**。

    自检会在 CI 与用户机器上反复跑，只要它写过一次线上数据（比如顺手播种
    名单），就等于「跑一次自检改一次生产配置」。这条边界光靠注释不算数，
    必须让方法本身不存在可用实现。
    """

    bucket = "images6396289"
    endpoint = "oss-cn-shenzhen.aliyuncs.com"
    prefix = "0817899624/company_pool"

    def __init__(self, doc=None, err=None):
        self._doc = doc
        self._err = err
        self.keys = []
        self.writes = []

    def download_json(self, key):
        self.keys.append(key)
        if self._err:
            raise self._err
        return self._doc, "etag"

    def _no_write(self, key):
        self.writes.append(key)
        raise AssertionError(f"自检不许写线上数据：{key}")

    def upload_json(self, key, *a, **kw):
        return self._no_write(key)

    def upload_json_atomic(self, key, *a, **kw):
        return self._no_write(key)

    def delete_key(self, key):
        return self._no_write(key)

    def copy_key(self, source_key, target_key, *a, **kw):
        return self._no_write(f"{source_key}->{target_key}")


def test_doctor():
    print("\n═══ F. 产物自检 --acl-doctor（发包门禁）═══")

    # 1) 通：拿到名单 = 退出码 0
    #    套进 _workspace：本机真实绑定号会多读一个设备分片，断言就跟着机器变。
    #    自检必须与「本机绑没绑」无关地在任何机器上给出同一结论。
    doc = {"accounts": ["13417047813"], "grants": {"13417047813": 2},
           "version": 7, "schema": "acl-v3"}
    fake = _DoctorOSS(doc=doc)
    with _workspace():
        # 绑定账号读的是 task_registry（不在 _workspace 的隔离范围内），本机真实
        # 绑定会让「读几个键」这条断言随机器变，所以显式钉成未绑定
        with _patch(AccessGuard, "_load_bound_account", staticmethod(lambda: "")):
            with _patch(AccessGuard, "_client_probe",
                        staticmethod(lambda: (fake, "ok", ""))):
                code, lines = ag.doctor_lines()
                _assert_eq(code, ag.DOCTOR_EXIT_OK, "名单可读 → 自检通过")
                _assert(any("名单读到 1 个账号" in ln for ln in lines), "报告里带名单规模")
                _assert_eq(fake.keys, [ag.ACL_KEY],
                           "未绑定的机器上自检只读名单这一个键")
                _assert_eq(fake.writes, [], "自检一个字节都没往线上写（只读边界）")
                _assert(any("未绑定" in ln for ln in lines),
                        "未绑定也如实报出来（CI runner 上这是正常形态，不能被当成失败）")

        # 1b) 本机已绑定时多读一个自己的分片键，仍然只读
        fake_b = _DoctorOSS(doc=doc)
        with _patch(AccessGuard, "_load_bound_account",
                    staticmethod(lambda: "13417047813")):
            with _patch(AccessGuard, "_client_probe",
                        staticmethod(lambda: (fake_b, "ok", ""))):
                code_b, lines_b = ag.doctor_lines()
                _assert_eq(code_b, ag.DOCTOR_EXIT_OK, "已绑定机器上自检同样通过")
                _assert_eq(fake_b.keys,
                           [ag.ACL_KEY, AccessGuard._bind_key("13417047813")],
                           "已绑定时只多读自己那一个设备分片（键名按账号定位，不扫全量）")
                _assert_eq(fake_b.writes, [], "已绑定路径也不写线上")
                _assert(any("该号等级：2" in ln for ln in lines_b),
                        "报告里能看出这个号的等级（发包方自查一眼对得上）")

    # 2) 不通：没凭证 = 退出码 2，且报告直接给出该改哪里
    with _workspace():
        with _patch(AccessGuard, "_client_probe",
                    staticmethod(lambda: (None, "credential", "未找到 OSS 密钥（配置来源：未配置）"))):
            code2, lines2 = ag.doctor_lines()
            _assert_eq(code2, ag.DOCTOR_EXIT_CREDENTIAL,
                       "缺凭证的自检退 2（CI 据此禁止上传制品）")
            _assert(any("不许发货" in ln for ln in lines2),
                    "报告把「这个包发出去就是精简版」说明白")

        # 3) 被拒 = 4，网络 = 5：两类必须分得开，否则门禁红得没有信息量
        for err, want in ((OSSPoolError("HTTP 403 AccessDenied"), ag.DOCTOR_EXIT_DENIED),
                          (OSSPoolError("connection timeout"), ag.DOCTOR_EXIT_NETWORK)):
            with _patch(AccessGuard, "_client_probe",
                        staticmethod(lambda e=_DoctorOSS(err=err): (e, "ok", ""))):
                code3, lines3 = ag.doctor_lines()
                _assert_eq(code3, want, f"「{str(err)[:24]}」→ 退出码 {want}")
                _assert(any("类别=" in ln for ln in lines3), "失败报告带类别")

        # 3b) 名单还没播种过 ≠ 发包失败：首次使用会自动建，门禁不能长期红着
        missing = _DoctorOSS(err=OSSPoolError("NoSuchKey 对象不存在"))
        with _patch(AccessGuard, "_client_probe",
                    staticmethod(lambda: (missing, "ok", ""))):
            code4, lines4 = ag.doctor_lines()
            _assert_eq(code4, ag.DOCTOR_EXIT_OK, "名单不存在时判通过（别把门禁做成永久红灯）")
            _assert(any("还没播种" in ln for ln in lines4), "报告说明白为什么不拦")

        # 3c) 连 tool_config 都没打进包 = 模块类退出码
        saved_tc = sys.modules.get("tool_config")
        try:
            sys.modules["tool_config"] = None          # import 语句命中 None 会抛 ImportError
            code5, lines5 = ag.doctor_lines()
            _assert_eq(code5, ag.DOCTOR_EXIT_MODULE, "tool_config 缺失 → 模块类退出码")
            _assert(any("打包清单" in ln for ln in lines5),
                    "报告指向 --hidden-import（这就是本轮 CI 新加的那一项）")
        finally:
            sys.modules["tool_config"] = saved_tc

    # 4) GUI 必须在 QApplication 之前分派 --acl-doctor（否则 CI 在无图形会话里跑不动）
    import b2bcollect_gui as gui
    src = inspect.getsource(gui.main)
    body = [ln for ln in src.splitlines() if not ln.strip().startswith("#")]
    idx = next(i for i, ln in enumerate(body) if "--acl-doctor" in ln)
    app_idx = next(i for i, ln in enumerate(body) if "QApplication(sys.argv)" in ln)
    _assert(idx < app_idx,
            "main() 里 --acl-doctor 的分派排在 QApplication 之前（纯 python 可跑）")
    _assert("_acl_doctor_entry" in src, "GUI 入口走的是统一收口，不各写一份")


# ══════════════════════════════════════════════ G. 交付链
@contextlib.contextmanager
def _chdir(path: Path):
    old = os.getcwd()
    os.chdir(str(path))
    try:
        yield
    finally:
        os.chdir(old)


# ── PyInstaller 命令行清单提取（yml 里是一整行、脚本里是折行，正则都吃得下）──
_HIDDEN_RE = r'--hidden-import\s+([\w.]+)'
_COLLECT_RE = r'--collect-(?:all|submodules)\s+([\w.]+)'
_ADD_DATA_RE = r'--add-data\s+"([^"]+)"'


def _cli_values(text: str, pattern: str) -> set:
    return set(re.findall(pattern, text))


def test_delivery_chain():
    print("\n═══ G. 交付链：Secret 注入 → 随包配置 → 产物门禁 ═══")
    wf = _REPO / ".github" / "workflows"
    win = (wf / "build-b2bcollect.yml").read_text(encoding="utf-8")
    mac = (wf / "build-b2bcollect-mac.yml").read_text(encoding="utf-8")

    # 分隔符本平台化：Windows 用 `;`、macOS/Linux 用 `:`，写反了 PyInstaller
    # 不报错、只是静默不打包（= 又一次「源码态通、打包态不通」）
    for name, text, sep in (("Windows", win, ";"), ("Mac", mac, ":")):
        for needle, why in (
            ("secrets.OSS_ACCESS_KEY_ID", "AK 必须来自 Secrets（不入库）"),
            ("secrets.OSS_ACCESS_KEY_SECRET", "SK 同上"),
            ("inject_build_config.py", "构建前必须现写随包配置"),
            (f'--add-data "config.json{sep}."', "随包清单里有 config.json，且分隔符是本平台写法"),
            ('[ -f config.json ]', "构建前有硬门：注入没成功就不许进 PyInstaller"),
            ("--hidden-import tool_config", "新模块必须显式收录（依赖分析口径会变）"),
            ("--acl-doctor", "产物自检门禁"),
        ):
            _assert(needle in text, f"{name} workflow {why}")
        # 顺序即语义：门禁排在上传之前才有意义
        gate = text.index("--acl-doctor")
        upload = text.index("upload-artifact")
        _assert(gate < upload, f"{name} workflow 自检门禁在上传制品之前（不通就没有包）")
        _assert("paths:" in text and "build-b2bcollect" in text,
                f"{name} workflow 改动 yml 自身也会触发（改完就能验证）")

    # 交付物的第三个入口是「本机手跑打包」。注意 `b2bcollect_build/*.spec` 被
    # .gitignore 忽略（本机可再生产物，不入库），所以**受版本控制**的那个入口是
    # debug/build_b2bcollect_mac_local.sh。断言必须压在这个文件上：压到 spec 的
    # 存在性上，就等于又写了一条「只在作者那台机器上成立的检查」——正是本轮要
    # 根治的形状（只在开发机成立的东西不会失败，只会静默地把坑留给下一台机器）。
    local_sh = _REPO / "debug" / "build_b2bcollect_mac_local.sh"
    _assert(local_sh.is_file(), "本机打包入口受版本控制（换台机器 clone 也打得出同一个包）")
    local = local_sh.read_text(encoding="utf-8")
    for needle, why in (
        ("set -euo pipefail", "任一步失败就整体失败，不会安静地留个半成品"),
        ("inject_build_config.py --from-local",
         "随包凭证与 CI 同一个脚本、同一份键名契约（不靠人肉复制密钥）"),
        ('--add-data "config.json:."', "本机包也把 config.json 打进包"),
        ('if [ ! -f config.json ]', "构建前硬门：注入没成功就不进 PyInstaller"),
        ("--hidden-import tool_config", "新模块显式收录（与两个 workflow 同口径）"),
        ("--acl-doctor", "产物自检门禁：跑的是产物自己，不是源码"),
        ("RC=$?", "自检退出码被抓出来，而不是只看它跑没跑"),
        ('exit "$RC"', "自检不通就非零退出，末尾那句成功话术骗不了人"),
    ):
        _assert(needle in local, f"本机打包脚本 {why}")

    # 清单一致性：本机脚本与 Mac workflow 的 PyInstaller 收集清单逐项必须相同。
    # 这是三个出口最容易漂移的地方——往 yml 补一个 hidden-import 忘了同步脚本，
    # 本机包就永久少一个模块，而 CI 包一切正常，症状又是「某些机器上不对劲」。
    for label, pattern in (("--hidden-import", _HIDDEN_RE), ("--collect-*", _COLLECT_RE)):
        _assert_eq(_cli_values(local, pattern), _cli_values(mac, pattern),
                   f"本机脚本与 Mac workflow 的 {label} 清单一致")
    ci_data = {v for v in _cli_values(mac, _ADD_DATA_RE) if "chromedriver" not in v}
    local_data = {v for v in _cli_values(local, _ADD_DATA_RE)
                  if "chromedriver" not in v and "$DRIVER" not in v}
    _assert_eq(local_data, ci_data,
               "本机脚本与 Mac workflow 的 --add-data 清单一致（驱动按架构各取所需）")
    _assert(any("chromedriver" in v for v in _cli_values(mac, _ADD_DATA_RE))
            and "$DRIVER" in local,
            "两边都带内置驱动：CI 写死 arm64 路径，本机按架构探测")

    # spec 只存在于开发机：在位就顺手比对，不在位绝不算失败。
    spec_file = _REPO / "b2bcollect_build" / "B2bCollectTool.spec"
    if spec_file.is_file():
        spec = spec_file.read_text(encoding="utf-8")
        _assert("'config.json'" in spec, "本地 spec 也把 config.json 打进包")
        _assert("B2B_BUILD_ALLOW_NO_CONFIG" in spec,
                "本地 spec 缺配置时默认中止，不许安静地出一个没凭证的包")
        _assert("'tool_config'" in spec, "本地 spec 收了 tool_config（与两个 workflow 同口径）")

    # 注入脚本与运行时的键名契约：不一致 = 注入了等于没注入
    import oss_pool_client as _oss
    script = (_REPO / ".github" / "scripts" / "inject_build_config.py").read_text(
        encoding="utf-8")
    _assert(_oss.AK_ID_KEY in script and _oss.AK_SECRET_KEY in script,
            "注入脚本写的键名与 oss_pool_client 的常量完全一致")
    _assert("deepseek_api_key" in script, "DeepSeek 密钥也走同一次注入")

    tmp = Path(tempfile.mkdtemp(prefix="inject_"))
    script_src = _REPO / ".github" / "scripts" / "inject_build_config.py"
    with _chdir(tmp):
        # 缺 Secret：必须红，且不落任何配置文件
        env = dict(os.environ)
        for k in ("OSS_AK_ID", "OSS_AK_SECRET", "DEEPSEEK_API_KEY"):
            env.pop(k, None)
        r = subprocess.run([sys.executable, str(script_src)], env=env,
                           capture_output=True, text=True)
        _assert(r.returncode != 0, "缺 OSS Secret 时注入脚本失败退出（宁可不发包）")
        _assert("::error::" in r.stdout, "失败信息走 Actions 的 ::error:: 注解")
        _assert(not (tmp / "b2bcollect_build" / "config.json").exists(),
                "失败时不写出半份配置")

        # 齐了：写出的文件必须能被 tool_config 读到同一个值
        env2 = dict(env)
        env2.update({"OSS_AK_ID": "AKID_CI", "OSS_AK_SECRET": "SK_CI",
                     "DEEPSEEK_API_KEY": "DS_CI"})
        r2 = subprocess.run([sys.executable, str(script_src)], env=env2,
                            capture_output=True, text=True)
        _assert_eq(r2.returncode, 0, "Secrets 齐全时注入成功")
        _assert("AKID_CI" not in r2.stdout and "SK_CI" not in r2.stdout,
                "注入脚本的日志绝不带出密钥值")
        written = json.loads((tmp / "b2bcollect_build" / "config.json")
                             .read_text(encoding="utf-8"))
        _assert_eq(written.get(_oss.AK_ID_KEY), "AKID_CI",
                   "写出的键名运行时读得到（这份配置就是产物的随包配置）")
        with _workspace() as ws:
            ws["bundle"].mkdir(parents=True, exist_ok=True)
            (ws["bundle"] / "config.json").write_text(
                (tmp / "b2bcollect_build" / "config.json").read_text(encoding="utf-8"),
                encoding="utf-8")
            with _frozen(ws):
                _assert_eq(_oss.resolve_credentials(), ("AKID_CI", "SK_CI"),
                           "端到端：CI 写出的文件放进包里就读得到")

    # 密钥绝不入库这条边界不能被这次改动冲掉。判定要看**真实生效**的那份 ignore
    # 规则（本仓库的 config.json 规则在 b2bcollect_build/.gitignore，不在仓库根），
    # 再加一道 git 层面的硬证据：这个文件从来没被跟踪过。
    ignored = False
    for gitignore in (_REPO / ".gitignore", _REPO / "b2bcollect_build" / ".gitignore"):
        if gitignore.is_file() and "config.json" in gitignore.read_text(encoding="utf-8"):
            ignored = True
    _assert(ignored, "生效的 .gitignore 里仍然忽略 config.json（密钥不入库）")
    tracked = subprocess.run(["git", "-C", str(_REPO), "ls-files",
                              "b2bcollect_build/config.json"],
                             capture_output=True, text=True)
    _assert(tracked.returncode == 0 and not tracked.stdout.strip(),
            "git 确认 b2bcollect_build/config.json 未被跟踪（历史里也没进去）")


# ══════════════════════════════════════════════ H. 本地状态落盘
@contextlib.contextmanager
def _clean_state_dir():
    """清空并还原进程级的「落点缓存 + 问题清单」。

    这两样是全进程共享的，测试之间不隔离就会互相带味道（上一例发现的问题会
    出现在下一例的报告里，红得莫名其妙）。
    """
    saved = dict(ag._STATE_DIR_CACHE), list(ag._STATE_PROBLEMS)
    ag._STATE_DIR_CACHE.clear()
    del ag._STATE_PROBLEMS[:]
    try:
        yield
    finally:
        ag._STATE_DIR_CACHE.clear()
        ag._STATE_DIR_CACHE.update(saved[0])
        ag._STATE_PROBLEMS[:] = saved[1]


def _blocked_dir(root: Path, name: str = "数据池缓存") -> Path:
    """造一个「必定写不进去」的目录路径：父级是普通文件，mkdir 一定失败。

    刻意不用 chmod 做不可写目录：以管理员/root 跑的机器上 chmod 拦不住写入，
    这种测试换台机器就假绿——正是本轮要根治的形状。
    """
    root.mkdir(parents=True, exist_ok=True)
    blocker = root / "blocker"
    blocker.write_text("x", encoding="utf-8")
    return blocker / name


def test_state_dir():
    print("\n═══ H. 本地状态：写不进去要看得见，也要有退路 ═══")
    root = Path(tempfile.mkdtemp(prefix="state_"))
    app = root / "appdata"
    good = root / "good_state"
    bad = _blocked_dir(root)

    # 0) 路径策略仍只在 edition 里判：权限模块只问投影函数，不许自己 if lite
    src = inspect.getsource(ag._preferred_state_dir)
    _assert_eq(re.findall(r"_ed\.(\w+)", src), ["pool_cache_dir"],
               "首选落点只问 edition 的档位投影（路径策略只许有一份）")

    def _fresh() -> None:
        """按「新起一个进程」的姿态重测一次：落点在一次运行内只定一次。

        这是刻意的（每次读写都去探盘会在漫游目录/网络盘上拖慢工具），所以逐例复用
        同一条首选路径时必须显式重来，否则测的是上一例留下的结论。
        """
        ag._forget_state_dir()
        del ag._STATE_PROBLEMS[:]

    with _clean_state_dir(), _patch(ag._ed, "app_data_dir", lambda: app):
        # 1) 首选写得进去：原位不动。内部同事的历史缓存都在桌面目录，搬家 = 重下整池
        with _patch(ag._ed, "pool_cache_dir", lambda default: str(good)):
            _fresh()
            _assert_eq(ag._state_dir(), good, "首选可写时不搬家（老缓存继续可用）")
            _assert_eq(ag.state_problems(), [], "没故障就不制造故障")
            _assert_eq(ag._state_dir(), good, "同一进程内落点稳定（不会一会儿东一会儿西）")
            parents = {ag._cache_file().parent, ag._device_file().parent,
                       ag._claim_file().parent}
            _assert_eq(parents, {good}, "三份状态文件同源一个目录（不许散在两地）")

        # 2) 首选写不进去：退到应用数据目录下的备用落点，并把问题如实记下来
        with _patch(ag._ed, "pool_cache_dir", lambda default: str(bad)):
            _fresh()
            chosen = ag._state_dir()
            _assert_eq(chosen, app / ag.BACKUP_STATE_SUBDIR,
                       "桌面落不下去时用备用目录，而不是静默放弃落盘")
            _assert(tc.probe_writable(chosen), "备用目录真的写得进去")
            problems = ag.state_problems()
            _assert(any("不可写" in p for p in problems),
                    f"首选不可写这件事必须现形（实际：{problems}）")
            _assert(any(str(bad) in p for p in problems),
                    "问题里带上是哪一处，远程排障才不用猜")
            # 磁盘副本真的能落地：断网时沿用上一次身份等级靠它
            _assert(ag._write_json(ag._cache_file(), {"level": 1}),
                    "退到备用目录后名单副本写成功")
            _assert(ag._cache_file().is_file(), "副本确实落在磁盘上（不是只返回成功）")

        # 3) 两处都写不进去：不瞎搬家（保持首选），但一定把后果说清楚
        with _patch(ag._ed, "pool_cache_dir", lambda default: str(bad)), \
             _patch(ag._ed, "app_data_dir", lambda: _blocked_dir(root / "b2", "appdata")):
            _fresh()
            _assert_eq(ag._state_dir(), bad, "无路可退时留在首选目录（不拿猜测定落点）")
            _assert(any("无法沿用上一次的身份等级" in p for p in ag.state_problems()),
                    "最坏情况把代价说明白：断网时会被判成丐版")
            _assert(ag._write_json(ag._cache_file(), {"level": 1}) is False,
                    "写失败返回 False（调用方要能判断）")

        # 4) 自愈：写失败 = 落点判定的凭据失效，之后封锁解除要能自己回到首选目录。
        #    少了这一步，「桌面被安全软件临时拦一下」的机器整个进程都会留在备用目录。
        c4 = _blocked_dir(root / "c4")               # root/c4/blocker/数据池缓存
        with _patch(ag._ed, "pool_cache_dir", lambda default: str(c4)):
            _fresh()
            backup = ag._state_dir()
            _assert_eq(backup, app / ag.BACKUP_STATE_SUBDIR, "先退到备用目录")
            (root / "c4" / "blocker").unlink()       # 封锁解除：同一条首选路径又可写了
            _assert_eq(ag._state_dir(), backup,
                       "没有新的失败证据就不搬家（一次运行内落点保持稳定）")
            for stale in backup.glob("*"):
                stale.unlink()
            backup.rmdir()
            backup.write_text("占位：让备用目录变成写不进去的地方", encoding="utf-8")
            _assert(ag._write_json(ag._cache_file(), {"level": 1}) is False,
                    "备用目录也写不进去时如实返回失败")
            _assert_eq(ag._state_dir(), c4,
                       "写失败之后重新判定：封锁已解除的首选目录回来了")
            _assert(tc.probe_writable(c4), "重新判定选中的目录真的写得进去")
            backup.unlink()                          # 还原备用目录，别把脏状态留给下一例

        # 5) 落盘失败不许静默：写不进去必须进问题清单
        _fresh()
        _assert(ag._write_json(bad / ".acl_cache.json", {"a": 1}) is False,
                "写到不可用路径返回失败")
        _assert(any(".acl_cache.json" in p and "失败" in p for p in ag.state_problems()),
                "具体是哪份文件写失败也要看得见")

    # 6) 探针模块自己没了（打包清单漏收 tool_config）：不拿假结论搬家、也不凭空报故障
    with _clean_state_dir():
        saved_tc = sys.modules.get("tool_config")
        try:
            sys.modules["tool_config"] = None
            for case_dir in (good, bad):
                _fresh()
                with _patch(ag._ed, "pool_cache_dir", lambda default, d=case_dir: str(d)):
                    _assert_eq(ag._state_dir(), case_dir,
                               f"探针不可用时保持首选路径（{case_dir.name}）")
            _assert_eq(ag.state_problems(), [],
                       "探针不可用不是一条故障：产物缺模块由 --acl-doctor 的模块类退出码说")
        finally:
            sys.modules["tool_config"] = saved_tc

    # 7) 测试可替换性不变量：_state_dir 是本地状态**唯一**的替换点
    with _patch(ag, "_state_dir", lambda: good), _clean_state_dir():
        _assert_eq(ag._cache_file().parent, good,
                   "替换 _state_dir 就能整体改道（离线夹具依赖这一点）")
        _assert(ag._device_file().parent == good and ag._claim_file().parent == good,
                "三份文件都经过同一个替换点，漏一份就会有人写到真实桌面")


# ══════════════════════════════════════════════ I. 时钟偏差自愈
class _Resp:
    def __init__(self, status=200, body="", headers=None, payload=None):
        self.status_code = status
        self.text = body
        self.headers = headers or {}
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class _FakeSession:
    """记下每次请求的**头**：时钟校正必须体现在重新签名的 Date 上。

    只重发旧头等于把同一个 403 再收一遍——那是「重试了」而不是「修好了」，
    两者在日志里长得一模一样，只有 Date 能区分。
    """

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.headers = {}

    def request(self, method, url, headers=None, **kw):
        self.calls.append((method, url, dict(headers or {}), kw))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _client_with(session) -> oss.OSSPoolClient:
    c = oss.OSSPoolClient(ak_id="AK_TEST", ak_secret="SK_TEST")
    c._session = session
    return c


@contextlib.contextmanager
def _no_skew():
    """校正量是进程级状态，测试必须自己收尾（否则会污染同进程的其它用例）。"""
    saved = oss._CLOCK_OFFSET_S
    oss._CLOCK_OFFSET_S = 0.0
    try:
        yield
    finally:
        oss._CLOCK_OFFSET_S = saved


def _skew_response(seconds_ahead: float = 7200.0) -> _Resp:
    return _Resp(
        403,
        "<Error><Code>RequestTimeTooSkewed</Code><Message>The difference between "
        "the request time and the current time is too large.</Message></Error>",
        {"Date": formatdate(time.time() + seconds_ahead, usegmt=True)})


def test_clock_skew():
    print("\n═══ I. 时钟偏差：签名时刻被服务端拒掉时自己校正 ═══")
    import b2bcollect_gui as gui
    with _no_skew():
        # 1) 判据本身：只有「服务端明说时刻偏太远」才值得自己修
        _assert(oss._is_clock_skew(403, "<Code>RequestTimeTooSkewed</Code>"),
                "RequestTimeTooSkewed 认得出来")
        _assert(oss._is_clock_skew(403, "The difference between the request time "
                                        "and the current time is too large."),
                "只有英文原文时也认得出来")
        _assert(not oss._is_clock_skew(403, "<Code>AccessDenied</Code>"),
                "普通 403（密钥/权限）不算时间问题")
        _assert(not oss._is_clock_skew(500, "RequestTimeTooSkewed"),
                "非 403 不碰（不是这一类故障的形态）")

        # 2) 校正来源只有服务端时间：解析不了、或偏差不大，就一律不瞎改
        _assert_eq(oss._apply_server_date(""), None, "没有 Date 头就不校正")
        _assert_eq(oss._apply_server_date("昨天天气不错"), None, "读不懂的时间不校正")
        _assert_eq(oss._apply_server_date(formatdate(time.time() + 5, usegmt=True)),
                   None, "五秒钟的抖动不值得为此改状态")
        with _no_skew():
            _assert(abs(oss._apply_server_date(
                formatdate(time.time() + 7200, usegmt=True)) - 7200) < 120,
                "两小时的偏差按服务端时间校正")
            _assert(abs(oss.clock_offset() - 7200) < 120, "校正量可被外部读到（自检报告用）")
            signed = parsedate_to_datetime(oss._gmt_date())
            skew = abs((signed - datetime.now(timezone.utc)).total_seconds())
            _assert(skew > 7080, "签名用的 Date 跟着校正量走（不是只记了个数）")
            _assert("7200" not in oss.clock_offset_text() and "慢" in oss.clock_offset_text(),
                    f"人话版说明不含原始秒数（{oss.clock_offset_text()}）")

        # 3) 端到端：先 403 后 200，重试必须带**新** Date
        sess = _FakeSession([_skew_response(), _Resp(200, payload={"ok": 1},
                                                     headers={"ETag": '"e1"'})])
        data, etag = _client_with(sess).download_json("acl/whitelist.json")
        _assert_eq(data, {"ok": 1}, "校正后这一次读到了名单")
        _assert_eq(etag, '"e1"', "ETag 照常返回")
        _assert_eq(len(sess.calls), 2, "时间偏差只重试一次（不做无限重试）")
        _assert(sess.calls[0][2]["Date"] != sess.calls[1][2]["Date"],
                "重试用的是重新签名的 Date，不是把旧头重发一遍")
        _assert(oss.clock_offset() > 6000, "自愈过程确实改了校正量")

        # 4) 其它 403 一次都不重试：那是密钥/权限问题，重试只会把启动拖慢
        sess2 = _FakeSession([_Resp(403, "<Code>AccessDenied</Code>",
                                    {"Date": formatdate(usegmt=True)})])
        _assert_raises(OSSPoolError,
                       lambda: _client_with(sess2).download_json("acl/whitelist.json"),
                       "被拒的下载照旧抛错")
        _assert_eq(len(sess2.calls), 1, "AccessDenied 不重试（不把 403 当时间问题硬试）")

        # 5) 拿不到服务端时间就不猜：错误原样抛出，交给分类与文案说话
        with _no_skew():
            sess3 = _FakeSession([_Resp(403, "<Code>RequestTimeTooSkewed</Code>")])
            _assert_raises(OSSPoolError,
                           lambda: _client_with(sess3).download_json("acl/x.json"),
                           "没有时间源时如实失败（不拿猜测定时刻）")
            _assert_eq(len(sess3.calls), 1, "没校正成功就不重试")
            _assert_eq(oss.clock_offset(), 0.0, "校正量没被瞎改")

        # 6) 出网只有一处：六个方法全部走 _send，组合方法不自己碰 session。
        #    漏掉哪个方法，哪个方法就在一台时钟不准的机器上稳定失败，而症状看起来
        #    又像密钥问题——所以这条「只许有一处」的约束必须在文本上钉住。
        for name in ("download_json", "upload_json", "head_etag",
                     "delete_key", "list_keys", "copy_key"):
            _assert("_send" in inspect.getsource(getattr(oss.OSSPoolClient, name)),
                    f"{name} 走 _send（时钟校正在这条路径上也生效）")
        for name in ("upload_json_atomic", "key_exists"):
            _assert("self._session" not in inspect.getsource(
                getattr(oss.OSSPoolClient, name)),
                f"{name} 不自己出网（复用上面的方法，自愈自动跟着来）")
        outlets = [ln.strip() for ln in _OSS_SRC.splitlines()
                   if "self._session." in ln and 'headers["User-Agent"]' not in ln]
        _assert_eq(len(outlets), 1, "整个客户端只有一个真正出网的语句")
        _assert_eq([ln for ln in outlets if ".request(" not in ln], [],
                   "唯一出网点是 _send 里的 .request（绕过它 = 那条路径没有自愈）")

        # 7) 分类：自愈没成之后，别再把它说成「密钥失效」
        kind, _detail = AccessGuard._classify_oss_error(
            OSSPoolError("下载失败 HTTP 403: <Code>RequestTimeTooSkewed</Code>"))
        _assert_eq(kind, "clock", "时间偏差单独一类，不混进 denied（否则两边互相指）")
        text = AccessGuard._unreachable_text("clock")
        _assert("时间" in text and "检查网络" not in text,
                f"这类故障既不叫人查网络也不叫人换包（{text[:40]}）")
        _assert(not _misleads_to_whitelist(text), "时间问题不叫人去找白名单")
        _assert("自动设置时间" in AccessGuard.SERVICE_NEXT_STEP["clock"],
                "下一步就给到校准时间这一步操作")
        _assert_eq(ag._DOCTOR_KIND_CODE["clock"], ag.DOCTOR_EXIT_CLOCK,
                   "--acl-doctor 有专属退出码（CI 红在这里时不用猜）")
        t, adv = gui.MainWindow._acl_denial(text)
        _assert("时间" in t and not _misleads_to_whitelist(adv),
                f"界面标题也说是时间问题（{t}）")

        # 8) 自愈成功后用户就不该看到故障：doctor 里这一行只是把做过的事写出来
        with _no_skew():
            _assert("未校正" in oss.clock_offset_text(), "没校过时不写出一句像故障的话")
        _assert("时钟校正" in inspect.getsource(ag.doctor_lines),
                "--acl-doctor 报告里带上时钟校正情况")

        # 9) 唯一出网点还负责把裸 requests 异常转成**可分类**的错误（它只认
        #    OSSPoolError 家族）。六个方法各写一份 try/except 迟早漏一个，漏掉的那
        #    个会把异常原样抛上去，结果又是「一句人话都没有」。
        import requests as _rq
        ssl_err = _rq.exceptions.SSLError(
            "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
            "unable to get local issuer certificate")
        conn_err = _rq.exceptions.ConnectionError("Connection to host lost")

        # 退避睡眠在测试里必须换成计数：真睡就是每个方法 1.8 秒，而「睡了几次」恰好
        # 就是这条路径上最该被钉住的东西（重试次数）。
        sleeps = []

        def _raised(call, exc):
            try:
                call(_client_with(_FakeSession([exc] * 4)))
            except BaseException as got:          # noqa: BLE001 正是要抓出来看类型
                return got
            return None

        cases9 = (
            ("head_etag", lambda c: c.head_etag("acl/whitelist.json")),
            ("delete_key", lambda c: c.delete_key("acl/x.json")),
            ("copy_key", lambda c: c.copy_key("a.json", "b.json")),
            ("list_keys", lambda c: c.list_keys("1699_users")),
            ("upload_json", lambda c: c.upload_json("acl/x.json", {"a": 1})),
            ("download_json", lambda c: c.download_json("acl/whitelist.json")),
        )
        with _patch(oss, "_retry_sleep", lambda attempt: sleeps.append(attempt)):
            for name, call in cases9:
                err = _raised(call, ssl_err)
                _assert(isinstance(err, OSSTransportError),
                        f"{name} 的 TLS 失败转成了 OSSTransportError（不是裸 requests 异常）")
                _assert(err is not None and err.permanent and "加密连接" in str(err),
                        f"{name} 的报错说清是加密连接（{str(err)[:52]}）")
                _assert_eq(AccessGuard._classify_oss_error(err)[0] if err else "", "tls",
                           f"{name} 的失败能被权限模块归到加密连接一类")
                del sleeps[:]
                err2 = _raised(call, conn_err)
                _assert(err2 is not None and not err2.permanent and "网络" in str(err2),
                        f"{name} 的瞬时连接失败仍标成网络（该重试的那类没被一并禁掉）")
                _assert_eq(len(sleeps), oss._MAX_RETRIES - 1,
                           f"{name} 的瞬时失败跑满重试并逐次退避（韧性还在）")
            _assert_eq(AccessGuard._classify_oss_error(
                _raised(lambda c: c.head_etag("k"), conn_err))[0], "network",
                "连接类失败还是网络类（这次新增类别没有把旧类别吃掉）")

            # 10) 重试语义的**反面**：确定性失败一次都不重试，也一次都不该睡。
            #     只测「重试还在」会把「顺手永久禁掉重试」也判成通过，两边都要钉。
            del sleeps[:]
            sessP = _FakeSession([ssl_err] * 4)
            _assert_raises(OSSTransportError,
                           lambda: _client_with(sessP).download_json("acl/whitelist.json"),
                           "证书类失败直接上报")
            _assert_eq(len(sessP.calls), 1,
                       "确定性失败不重试（冷启动那 3 秒等不起三轮退避）")
            _assert_eq(sleeps, [], "确定性失败连一次退避都不睡")

            del sleeps[:]
            sessT = _FakeSession([conn_err] * 4)
            _assert_raises(OSSPoolError,
                           lambda: _client_with(sessT).download_json("acl/whitelist.json"),
                           "瞬时失败跑满重试后如实报错")
            _assert_eq(len(sessT.calls), oss._MAX_RETRIES,
                       "瞬时失败仍然重试（这次改动没把网络韧性一起删掉）")
            _assert_eq(len(sleeps), oss._MAX_RETRIES - 1,
                       "最后一次失败之后不再多睡一觉（等待预算要花在刀刃上）")


# ══════════════════════════════════════════════ J. 时间基准
@contextlib.contextmanager
def _shifted_wall_clock(seconds: float):
    """把墙上时钟整体挪一段（NTP 纠正、用户手动改时间、虚拟机恢复后回拨）。

    只挪 ``time.time``：单调时钟本来就不该被这种动作影响，能被影响到的都是真缺陷。
    """
    real = time.time
    with _patch(time, "time", lambda: real() + seconds):
        yield


class _StubClient:
    """桩权限服务：名单与设备分片都读得到（不出网，只喂判定逻辑）。"""

    bucket = "b"
    endpoint = "e"
    phone = "13417047813"

    def download_json(self, key, **_kw):
        if key == ag.ACL_KEY:
            return ({"schema": ag.ACL_SCHEMA, "accounts": [self.phone],
                     "grants": {self.phone: ag.LEVEL_FULL}}, '"etag"')
        return ({"phone": self.phone, "state": "active", "session": "sess-1",
                 "generation": 1, "device_id": ag.device_id(),
                 "device_label": "stub"}, '"etag"')


class _SilentClient:
    """一次都给不出结论的服务（用来走冷启动那段有界等待）。"""

    bucket = "b"
    endpoint = "e"

    def download_json(self, key, **_kw):
        raise OSSPoolError("下载失败（网络）: ConnectTimeout: timed out")


def test_time_basis():
    print("\n═══ J. 时间基准：改系统时间不许让权限判定失忆 ═══")
    root = Path(tempfile.mkdtemp(prefix="clock_"))

    # 1) 判据本身先自证：不可信的时间戳一律按最旧，容差以内按「刚写的」
    now = time.time()
    _assert(abs(ag._stamp_age(now - 10) - 10) < 2, "十秒前的时间戳算得出年龄")
    for junk in (0, None, "", "abc", -1, {}):
        _assert_eq(ag._stamp_age(junk), ag._STALE, f"{junk!r} 这种时间戳按不可信处理")
    _assert_eq(ag._stamp_age(now + 3600), ag._STALE,
               "落在将来一小时 = 不可信（这台机器的系统时间被改过）")
    _assert_eq(ag._stamp_age(now + 30), 0.0,
               "几秒的抖动按刚写的处理（不因为抖动误杀一份好副本）")

    with _workspace():
        # 2) 进程内新鲜度与墙上时钟跳变无关：这条正是「被删的账号还能用」的入口
        with _patch(AccessGuard, "_build_client", staticmethod(lambda: _StubClient())):
            g = AccessGuard(ttl=0.05)
            g._account_reader = lambda: _StubClient.phone
            _assert(g._fetch() is True, "桩服务下这次刷新算成功")
            calls = []
            with _patch(g, "_start_refresh", lambda: calls.append(1)):
                with _shifted_wall_clock(-7200.0):
                    time.sleep(0.08)          # 真实过了 80ms > ttl
                    g.tick()
                    _assert_eq(calls, [1],
                               "时钟被往回调两小时后仍然按 ttl 过期刷新（缓存不会「永远新鲜」）")
                    g._fetched_at = time.monotonic()
                    g._bind_at = time.monotonic()
                    del calls[:]
                    g.tick()
                    _assert_eq(calls, [], "没到 ttl 就不多跑一趟（自愈不等于无脑重试）")

        # 3) 冷启动那段有界等待：等待途中时间被纠正也不许提前结束，更不许变成卡死
        #    必须换一间全新的状态目录：上一例的刷新刚把名单副本写到盘上，紧接着
        #    这一例要模拟的却是「本地什么都没有」的冷启动——复用同一间屋子的话，
        #    _load_disk_once 会拿上一例的副本当自己的，测试测的就不再是冷启动，
        #    而是「有副本但没绑定」那条完全不同的分支（症状：期望网络文案，
        #    实际拿到设备绑定文案，而且只在用例顺序变动时才复现）。
        with _workspace(), \
             _patch(AccessGuard, "_build_client", staticmethod(lambda: _SilentClient())), \
             _patch(ag, "FIRST_FETCH_WAIT_SECONDS", 0.4):
            g2 = AccessGuard(ttl=60.0)
            g2._account_reader = lambda: _StubClient.phone
            real = time.time

            def _jump_midway():
                """0.15 秒之后系统时间被往前纠两小时（等待途中跳变）。"""
                return real() + (7200.0 if real() - t0 > 0.15 else 0.0)

            t0 = real()
            with _patch(time, "time", _jump_midway):
                started = time.monotonic()
                ok3, reason3 = g2.check(wait=True)
                waited = time.monotonic() - started
            _assert(not ok3, "拿不到名单就是拒绝（fail-closed）")
            _assert(0.35 <= waited < 1.2,
                    f"等待按预算走完就收口：实际 {waited:.2f}s（墙上时钟跳变不参与计时）")
            _assert_eq(reason3, AccessGuard._unreachable_text("network"),
                       "确实连不上的那一类仍然给网络文案（新增类别没把旧类别吃掉）")

        # 4) 磁盘副本：不可信的时间戳不再能撑起离线可用，而且这件事要看得见
        del ag._STATE_PROBLEMS[:]
        future = {"schema": ag.ACL_SCHEMA, "accounts": [_StubClient.phone],
                  "grants": {_StubClient.phone: ag.LEVEL_FULL},
                  "grants_declared": True, "fetched_at": time.time() + 7200}
        _write(ag._cache_file(), future)
        g4 = AccessGuard(ttl=60.0)
        g4._load_disk_once()
        _assert_eq(g4._accounts, None, "时间戳落在将来的副本被丢弃（未来时间戳不是通行证）")
        _assert(any("时间戳不可信" in p for p in ag.state_problems()),
                f"这份副本为什么被丢掉要写进自检清单（{ag.state_problems()}）")
        del ag._STATE_PROBLEMS[:]
        _write(ag._cache_file(), dict(future, fetched_at=time.time() - 3600))
        g5 = AccessGuard(ttl=60.0)
        g5._load_disk_once()
        _assert_eq(g5._accounts, [_StubClient.phone], "一小时前的正常副本照旧可用")
        _assert_eq(ag.state_problems(), [], "正常路径不制造故障")

        # 5) 认领记录里的同一套判据（断网时它顶替远端分片）
        _write(ag._claim_file(), {_StubClient.phone: {
            "session": "sess-1", "generation": 1, "device_id": ag.device_id(),
            "claimed_ts": time.time() + 7200}})
        _assert_eq(g4._local_binding_fallback(_StubClient.phone), None,
                   "认领记录时间戳落在将来 → 不用它顶替远端（宁严勿宽）")

        # 6) 被顶号后的冷却：不可信时间戳按「冷却刚开始」算，但边界必须封住
        claim = {_StubClient.phone: {"session": "sess-1", "generation": 1,
                                     "device_id": ag.device_id()}}
        for raw, want in ((time.time() + 7200, float(ag.RECLAIM_COOLDOWN_SECONDS)),
                          (time.time() - 30, ag.RECLAIM_COOLDOWN_SECONDS - 30),
                          (time.time() - 99999, 0.0),
                          (0, 0.0),
                          ("不是数字", float(ag.RECLAIM_COOLDOWN_SECONDS))):
            _write(ag._claim_file(), dict(claim, **{
                _StubClient.phone: dict(claim[_StubClient.phone], kicked_ts=raw)}))
            got = ag._reclaim_blocked(_StubClient.phone)
            _assert(abs(got - want) <= 1.0, f"kicked_ts={raw!r} → 还要等 {got:.0f}s")
            _assert(0.0 <= got <= ag.RECLAIM_COOLDOWN_SECONDS,
                    f"冷却永远在一整段以内（{raw!r} 算出 {got:.0f}s 就是荒唐数字）")

    # 7) 文本级不变量：时间基准只许两套，写墙上时钟的地方只许是要落盘的那几个
    deadlines = re.findall(r"deadline = ([\w.]+\()", _AG_SRC)
    _assert_eq(deadlines, ["time.monotonic(", "time.monotonic("],
               "两处有界等待都用单调时钟（墙上时钟跳变不许把 3 秒变成几小时）")
    _assert_eq(re.findall(r"time\.time\(\) - self\._\w*at", _AG_SRC), [],
               "不许再用墙上时钟算进程内新鲜度")
    wall = [ln.strip() for ln in _AG_SRC.splitlines()
            if "time.time()" in ln and not ln.strip().startswith("#")]
    _assert_eq(len(wall), 4, f"整个模块只剩 4 处墙上时钟（实际 {len(wall)} 处：{wall}）")
    for ln in wall:
        _assert(any(k in ln for k in ("claimed_ts", "kicked_ts", "fetched_at", "delta =")),
                f"这一处墙上时钟必须是**要落盘**的时间戳或其判定：{ln}")


def main():
    print("═══ 打包态交付链 离线测试 ═══")
    test_config_chain()
    test_oss_credentials()
    test_failure_taxonomy()
    test_gui_wording()
    test_deepseek_and_cache()
    test_doctor()
    test_delivery_chain()
    test_state_dir()
    test_clock_skew()
    test_time_basis()
    print(f"\n═══ 结果：{_PASS} 通过 / {_FAIL} 失败 ═══")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
