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
  K. 控制台编码：CI 脚本与 CLI/自检入口在**第一次打印之前**就把 stdout 切到
     UTF-8 ——Windows 英文系统用 cp1252 编码控制台，中文和 ✅ 都编不出来，本轮
     CI 就是「config.json 已写好、进程却死在打印成功话术」这种红法
  L. 自检开关口径：CI 的 --acl-doctor、源码 CLI 的 --doctor、产物界面的分派三者
     必须是同一份写法（拼错时不许退化成「安静地去建界面」，那会让 runner 挂到
     job 超时）；退出码全表由故障分类表派生，帮助文本不许再手写一份清单
  M. workflow 步骤的脚本语言与这一步真正用的 shell 必须配套：Windows 上 run 步
     默认是 pwsh，bash 的 `[ -f x ] || { …; exit 1; }` 会被解析成类型表达式，
     ParserError 之后 PyInstaller 根本没启动——本轮 CI 的第二种红法
  N. 打包清单硬门与采集通道完整性：site_adapters 声明的跨目录模块必须在三处入口
     逐条对上、且落对目录（缺一条或放错地方都在构建前红）；产物自检在打包态缺
     通道退 3，源码态只警告；界面只点名「该给却没给」的那几路（档位藏起来的
     功能不许被念出来）——本轮第三种红法：CI 全绿、产物却只剩企知道一路
  O. Windows 交付形态：三处入口都不许再出现 --onefile；装配校验器对合成产物树判得
     准（onedir 目录缺失/只有空壳 exe/混进旧单文件 exe/缺 runtime_paths 都要点名），
     校验不过就**不产出 zip**，运行期目录绝不进交付物，zip 顶层必须是一个目录，
     NSIS 脚本与使用说明必须 UTF-8 with BOM，CI 与制品校验器共用同一份判据；
     说明书里的每一条事实都要有代码或脚本撑着（每用户安装、安装目录、三个数据目录、
     崩溃日志名与界面同一个常量）
  P. 运行期文件落点：审计日志与列映射历史的默认值不再相对当前工作目录（切到别处
     也照样落在状态目录、且不落进随包目录），坏缓存/写不进去一律退化而不抛异常
  Q. 重启链路：给新实例的 cwd 绝不是包内目录（Windows 那句 base_library.zip
     FileNotFoundError 就是这么来的），重启参数不被误判成自检参数，不走 shell、
     不猜时长，由新实例观测旧 PID 真退出
  R. 一路通道失败的种类由「文件的主人」回答成因：模块已加载却在构造期抛
     FileNotFoundError 绝不能报成「安装包缺文件」（Mac 只读卷的实况就是这一类）
  S. Mac 只读副本：识别得出转置运行，并且能在界面上一次点完「移进应用程序文件夹」
     ——判定与执行分开，四种不许动手的形态（目标同名/版本不同/多份副本/读不到包
     标识）各有独立判据，执行只做同卷改名，其余一律交回访达
  T. 落点纪律门禁：--acl-doctor 必须在**发包前**认出「数据写进应用包里」——候选链
     和实际落点都查，打包态违规退 9（落点违规），源码态只报现状；这一类故意不进
     服务故障分类表（那三张表说的是服务为什么连不上），但必须进退出码全表和帮助
  U. 窗口冒烟门禁：--ui-smoke 让**产物自己**把界面真建一遍，三处打包入口都跑它，
     Mac 那两处还跑在只读副本上（App Translocation 现场）；正例退 0、反例退非零并
     留下崩溃日志——doctor 不碰 Qt，「CI 全绿、用户双击没界面」这一类只能由这条拦

为什么单独成套：本仓库其它离线测试都在**源码态**跑，而这一轮外部机器全员精简版
的根因恰恰是「源码态通、打包态不通」——开发机恰好能从本机 iOS 工程读到密钥，
所以从第一次打包到现在都测不出来。这一套的职责就是**在离线机器上把打包态模拟
出来**：sys.frozen / _MEIPASS / sys.executable 三个开关一拨，凭证链就必须按
产物的规矩说话。

运行：QT_QPA_PLATFORM=offscreen .venv/bin/python b2bcollect_build/test_frozen_delivery_offline.py
"""
import ast
import contextlib
import importlib.util
import inspect
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tokenize
import textwrap
import time
import zipfile
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
_TC_SRC = (_REPO / "b2bcollect_build" / "tool_config.py").read_text(encoding="utf-8")

_PASS = 0
_FAIL = 0

# 失败详情里不许带出密钥明文。这套用例的主角就是凭证解析链：只要某一级命中了开发机上
# 的真实密钥（本机打过包就一定会有），diff 就会把 AK/SK 原样打进 CI 日志和本地终端——
# 日志会被留存、会被别人看到，而「测试输出里有真密钥」这件事本身就是一个缺陷。
# 判据取「连续 24 位以上的纯字母数字」：AK/SK 这类正好命中；路径、URL、中文里都有
# / . 或非标点字符断串，不会被误伤成脱敏（把有用的定位信息抹掉也是一种坏法）。
_SECRET_RE = re.compile(r"[A-Za-z0-9]{24,}")


def _redact(text) -> str:
    return _SECRET_RE.sub(
        lambda m: m.group(0)[:4] + f"…（已脱敏，{len(m.group(0))} 位）", str(text))


def _assert(cond, msg):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ✅ {_redact(msg)}")
    else:
        _FAIL += 1
        print(f"  ❌ {_redact(msg)}")


def _assert_eq(actual, expected, msg):
    _assert(actual == expected, f"{msg}（期望 {expected!r}，实际 {actual!r}）")


def _assert_raises(exc_type, fn, msg):
    global _PASS, _FAIL
    try:
        fn()
    except exc_type:
        _PASS += 1
        print(f"  ✅ {_redact(msg)}")
        return
    except Exception as e:
        _FAIL += 1
        print(f"  ❌ {_redact(msg)}（抛的是 {type(e).__name__}: {_redact(e)}）")
        return
    _FAIL += 1
    print(f"  ❌ {_redact(msg)}（没抛异常）")


def _fmt_idx(i):
    """把「有没有找到某一行」说成人话。

    配合 ``next(gen, None)`` 用：裸 ``next(gen)`` 找不到就抛 StopIteration，那是
    测试框架自己的异常，一整棵用例树会被它带走，屏幕上连一条 ❌ 都看不到——
    本轮 K 组被自己的夹具打死就是这一类，查找/夹具的异常永远不该冒充用例结论。
    """
    return f"第 {i + 1} 行" if i is not None else "没找到"


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
        # ── 源码态的三级文件候选也一并搬进临时目录 ──────────────────────────
        # 不搬就会「跑过一次本地打包，回归就红四条」：打包脚本会现写
        # b2bcollect_build/config.json（真密钥），PyInstaller 还会在源码态把
        # state_dir 指回模块目录。测试读到的来源因此从「什么都没配」变成「随包内置
        # 命中」，.m 兜底那几条断言当场变红——而红法最坏的地方是它**看起来像真缺陷**，
        # 换一台没打过包的机器又全绿。测试的输入必须只由夹具决定（本套的职责就是把
        # 打包态模拟出来，结果被开发机磁盘上的真实产物影响，等于没模拟）。
        _orig_bundle = tc.bundle_dir
        _orig_repo = tc._repo_root

        def _bundle_isolated():
            return (root / "src_module") if not tc.is_frozen() else _orig_bundle()

        def _repo_isolated():
            return (root / "src_none") if not tc.is_frozen() else _orig_repo()

        stack.enter_context(_patch(tc, "bundle_dir", _bundle_isolated))
        stack.enter_context(_patch(tc, "_repo_root", _repo_isolated))
        stack.enter_context(_patch(tc, "user_config_file",
                                  lambda: root / "home_isolated" / "config.json"))
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
    与「包旁」指向哪。少拨一个，测的就不是打包态了；拨了不收回，测的就是邻居的
    环境——所以三项都交给 ``_sys`` 按名字存取，这一层不再自己记 old。
    """
    saved_env = os.environ.get("B2B_STATE_DIR")
    with _sys(frozen=True, _MEIPASS=str(ws["bundle"]),
              executable=str(ws["exe_dir"] / "B2bCollectTool.exe")):
        if state_dir is not None:
            os.environ["B2B_STATE_DIR"] = str(state_dir)
        try:
            yield ws
        finally:
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


_ABSENT = object()


@contextlib.contextmanager
def _sys(**values):
    """按**名字**拨 sys 上的运行形态开关，退出时按名字还原。

    为什么不让各节自己写 ``old = (sys.platform, sys.executable, ...)`` 这种位置元组：
    T 节原来就是这么写的，取值顺序是 (platform, executable, frozen)，还原却按
    (platform, frozen, executable) 赋回去——于是 ``sys.frozen`` 变成一个真值字符串、
    ``sys.executable`` 变成 ``None``。它当场没红，红的是后面一整节（U 节在进程内真建
    窗口，撞上 ``Path(None)``），报的还是「NoneType」这种和产品毫不相干的栈。这类缺陷
    最坏的地方不在红，而在**红的位置离真因隔了一整节**：排查的人会先去查产品代码。
    按名字存取让两边不可能错开；传 ``None`` 表示「这项要恢复成没拨过的样子」，直接
    从 sys.__dict__ 删掉——``tool_config.is_frozen()`` 读的就是「有没有这个属性」。
    """
    old = {k: getattr(sys, k, _ABSENT) for k in values}
    for k, v in values.items():
        if v is None:
            sys.__dict__.pop(k, None)
        else:
            setattr(sys, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            if v is _ABSENT:
                sys.__dict__.pop(k, None)
            else:
                setattr(sys, k, v)


# 开跑前的运行形态快照：必须在**任何小节拨开关之前**记下来（写在模块顶部就是这个意思），
# 它同时是「源码态」的标准答案和「谁没收拾」的判据来源。
_SYS_SNAPSHOT = {"platform": sys.platform, "executable": sys.executable,
                 "frozen": getattr(sys, "frozen", None),
                 "_MEIPASS": getattr(sys, "_MEIPASS", None)}


@contextlib.contextmanager
def _source_form():
    """把运行形态开关压回「本机源码直跑」这一档，退出时按现场恢复。

    为什么 U 节要自己接管：本套十几节都会拨 frozen/_MEIPASS/executable，而 U 节是在
    **同一个进程内**真建 Qt 窗口。邻居留一个半拨的状态（frozen 成了真值字符串、
    executable 成了 None）时，U 节红的是 ``Path(None)`` 那种 TypeError——看着像新加的
    门禁自己有 bug，其实是上一节没收拾。用例的输入只能由夹具决定，不能由「谁先跑」
    决定，这条与 P/Q 节自己接管 ``Path.home``、cwd 是同一条纪律。
    """
    with _sys(**_SYS_SNAPSHOT):
        yield


def _switches_left() -> list:
    """跑完全部小节后，与开跑前快照不一致的那几项（空 = 每一节都把自己拨走的收回了）。"""
    out = []
    for key, want in _SYS_SNAPSHOT.items():
        now = getattr(sys, key, None)
        if now != want:
            out.append(f"sys.{key}={now!r}（开跑前是 {want!r}）")
    return out


def _code_only(fn) -> str:
    """函数正文，只留**会执行的那几行**（docstring 与注释整体剔掉）。

    「不许再出现某种写法」这类判据必须压在这里：本轮修好的旧写法（``cmd /c start``、
    ``timeout /t 2``、``shell=True``、``Path(__file__).parent``）恰恰都要写进 docstring
    里说明「为什么不能用」——直接从 ``inspect.getsource`` 上判负，会得到两种坏法：判据
    永远红，或者有人为了变绿把解释删掉（那是抹掉下次排查最需要的上下文）。注释行整体
    剔掉同一个道理，与 ``_strip_comments``（shell 文本）配成一对。
    """
    src = textwrap.dedent(inspect.getsource(fn))
    lines = src.splitlines()
    start = 0
    body = ast.parse(src).body[0]
    if (body.body and isinstance(body.body[0], ast.Expr)
            and isinstance(body.body[0].value, ast.Constant)
            and isinstance(body.body[0].value.value, str)):
        start = body.body[0].end_lineno          # docstring 之后才是正文
    return "\n".join(ln for ln in lines[start:]
                     if ln.strip() and not ln.lstrip().startswith("#"))


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

    # 4) GUI 必须在 QApplication 之前分派自检（否则 CI 在无图形会话里跑不动）。
    #    这里只问**先后**，不问写法：写法已经收进 DOCTOR_FLAGS 一处，由 L 组逐字
    #    对拍；在文本里搜 "--acl-doctor" 字面量等于把「写法」和「顺序」两件事焊在
    #    一起，改一处命名就会把这条完全无关的顺序断言炸成整棵树崩溃。
    import b2bcollect_gui as gui
    src = inspect.getsource(gui.main)
    body = [ln for ln in src.splitlines() if not ln.strip().startswith("#")]
    idx = next((i for i, ln in enumerate(body) if "_doctor_request(" in ln), None)
    app_idx = next((i for i, ln in enumerate(body)
                    if "QApplication(sys.argv)" in ln), None)
    _assert(idx is not None and app_idx is not None,
            f"分派与建界面两段代码都在（{_fmt_idx(idx)}, {_fmt_idx(app_idx)}）")
    _assert(idx is not None and app_idx is not None and idx < app_idx,
            "main() 里自检的分派排在 QApplication 之前（纯 python 可跑）")
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

_GATE_MOD = None


def _manifest_gate():
    """把构建前的「打包清单硬门」脚本当模块载进来（它就是判据本体，不另抄一份）。

    这份用例既校验三处入口的清单，也校验那个脚本自己判得对不对：脚本和用例各写一套
    提取逻辑时，同一颗坑要踩两次（注释里的 --hidden-import 被当成参数就是这么回事）。
    """
    global _GATE_MOD
    if _GATE_MOD is None:
        path = _REPO / ".github" / "scripts" / "check_packaging_manifest.py"
        spec = importlib.util.spec_from_file_location("_pkg_manifest_gate", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _GATE_MOD = mod
    return _GATE_MOD


def _cli_values(text: str, pattern: str) -> set:
    # 注释必须先剔掉再匹配：workflow/脚本的注释里写着「--add-data/--hidden-import 与
    # site_adapters 的清单」这种话，不剔就会凭空多出一个叫「与」的幽灵清单项，
    # 于是「本机脚本与 CI 清单一致」这条永远红——而它红的时候没人怀疑是解析器的问题。
    # 解析器与构建前的硬门共用同一份（见 _manifest_gate），两边不再各写一遍正则。
    return set(re.findall(pattern, _manifest_gate()._strip_comments(text)))


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

    # 这里**不再**比对 b2bcollect_build/B2bCollectTool.spec：它由 PyInstaller 在
    # CLI 构建时自动生成（.gitignore 里 *.spec 就是为此），内容只是那串命令行参数的
    # 回显，既不是入口也不是人工维护的清单。以前「在位就顺手比对」的写法会让回归结果
    # 取决于「这台机器最近跑没跑过本地打包」——跑过就红三条，而且红的是一条根本不参与
    # 出包的旁路文件。真正要钉的门在下面这句：本机脚本自己必须在 config.json 不在位时中止。
    _assert("if [ ! -f config.json ]" in local and "exit 1" in local,
            "本机脚本在 config.json 不在位时中止（不许安静地出一个没凭证的包）")

    # 校验器自己也得懂产物结构：Actions 制品是「zip 里再套一层 ditto 打的产物 zip」，
    # 只解外层时 .app 永远找不到，脚本会报「没找到 .app，无法校验」并按坏包退出——
    # 症状和产物真坏了完全一样（本轮两个云 run 都 success，却被自己的校验器拦下）。
    # 判据钉在文本上：必须解内层 zip；解不出内层时必须非零退出，绝不许安静跳过校验。
    fetch = (_REPO / "debug" / "fetch_b2bcollect_artifacts.sh").read_text(encoding="utf-8")
    _assert("ditto -x -k" in fetch and "INNER" in fetch,
            "制品校验器解到内层产物 zip 再找 .app（少解一层=把校验器自己的毛病报成产物坏了）")
    _assert("没有内层产物 zip" in fetch and "解不开" in fetch
            and fetch.count("exit 6") >= 2,
            "内层 zip 不在位/解不开两处都非零退出（静默跳过校验等于拆掉随包凭证这道门）")

    # 自检报告的落点由产物自己的 state_dir 候选链决定，两个 Mac 入口都不许把它写死：
    # 写死之后的下一步一定是「口径或布局变了、门禁静默 cat 不到报告」——CI 里看着像
    # 「产物是好的，只是这一步没打印原文」，而这种红法没人会去修（本来就拦不住上传）。
    _assert("b2bcollect_state/acl_doctor.txt" not in mac
            and "b2bcollect_state/acl_doctor.txt" not in local,
            "两个 Mac 入口都不写死自检报告路径（Windows 那一步早就是查找，两边同规矩）")
    for label, text2, pat in (("Mac workflow", mac,
                               "find b2bcollect_build/dist -name acl_doctor.txt"),
                              ("本机打包脚本", local, "find dist -name acl_doctor.txt")):
        _assert(pat in text2 and "-mmin " in text2,
                f"{label} 按「刚被写过」查找自检报告（递归找旧文件会把上一次的报告当本次结论）")
    # Actions 的 bash 步默认 -e：不收住退出码，::error:: 那行永远打不出来，
    # 日志里只剩一句 exit code 1，分不清是产物坏了还是这一步自己坏了。
    _assert("set +e" in mac, "Mac workflow 显式收住自检退出码，失败注解才有机会打印")
    _assert("set +e" in local and "set -e" in local,
            "本机脚本同样收住退出码（两个入口的失败话术要一样齐全）")

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


# ══════════════════════════════════════════════ K. 控制台编码
def _cp1252_stdout():
    """造一个「英文 Windows 控制台」：缓冲区是内存，编码是 cp1252。

    为什么要在 macOS 上模拟它：Windows 的 Python 用**系统代码页**给 stdout 编码，
    非中文系统就是 cp1252 —— 中文和 ✅ 都编不出来，直接抛 UnicodeEncodeError。
    这个坑只在 CI 的那台机器上成立，本机跑一百遍也测不出来（本轮 CI 就是这么红的：
    config.json 已经写好了，进程却死在打印成功话术那一行）。所以这里把控制台
    本身做成夹具。
    """
    buf = io.BytesIO()
    return io.TextIOWrapper(buf, encoding="cp1252", errors="strict"), buf


def _print_on_cp1252(text: str, fix=None):
    """在「英文 Windows 控制台」上真打印一次，把异常和落盘字节**带回给调用方**。

    夹具的作用域只许包住被测语句本身：断言函数自己也要 print 结论，一旦它跑在
    cp1252 流上，结论还没打出来就先抛 UnicodeEncodeError——测试框架被自己的夹具
    打死，整棵用例树崩掉，反而看不到到底是哪一条红（本套第一版就是这么炸的）。
    所以这里在交出结论之前先把 sys.stdout 换回真的那个。
    """
    stream, buf = _cp1252_stdout()
    saved = sys.stdout
    err = None
    try:
        sys.stdout = stream
        if fix is not None:
            fix()
        print(text)
    except BaseException as e:
        err = e
    finally:
        try:
            stream.flush()
        except Exception:
            pass
        raw = buf.getvalue()
        sys.stdout = saved          # 先恢复，再谈结论
    return err, raw


def _force_with_stdout(value):
    """把 sys.stdout 换成 value 后调用 force_utf8_stdout，恢复之后才交出结果。"""
    saved = sys.stdout
    try:
        sys.stdout = value
        got = tc.force_utf8_stdout()
    except BaseException as e:
        got = e
    finally:
        sys.stdout = saved
    return got


def _prints_before(src: str, needle: str) -> bool:
    """``needle`` 是否排在这段代码**第一次真正打印**之前。

    顺序就是这里的全部语义：编码修在第一次打印之后，那一行就已经抛完了，后面
    再改也换不回结论。注释行先剔掉——文档字符串里出现「print」不算打印。
    """
    body = "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("#"))
    i = body.find(needle)
    if i < 0:
        return False
    outlets = [body.find(p) for p in ("print(", "say(") if body.find(p) >= 0]
    return not outlets or i < min(outlets)


def test_console_encoding():
    print("\n═══ K. 控制台编码：中文与 ✅ 在英文 Windows 上必须说得出来 ═══")
    import b2bcollect_gui as gui

    # 1) 先把「环境确实会坏」钉住：否则后面所有断言都是在测一个不会失败的场景
    err, _ = _print_on_cp1252("✅ 已生成随包配置")
    _assert(isinstance(err, UnicodeEncodeError),
            f"cp1252 控制台上打印带 ✅ 的成功话术当场抛错（实际 {type(err).__name__}）")
    err, _ = _print_on_cp1252("写入失败")
    _assert(isinstance(err, UnicodeEncodeError),
            f"纯中文一样抛错（所以只改 emoji 是假修法，下一句中文字又撞墙）："
            f"实际 {type(err).__name__}")

    # 2) 修法本身：换编码之后同一条打印能出来，且落的是 UTF-8 字节
    fixed = []
    err, raw = _print_on_cp1252("✅ 已生成随包配置 b2bcollect_build/config.json",
                               fix=lambda: fixed.append(tc.force_utf8_stdout()))
    _assert(err is None, f"换成 UTF-8 后同一条打印不再抛错（实际 {type(err).__name__}）")
    _assert_eq(fixed, [True], "能改的流改成功了（返回值如实说）")
    _assert("随包配置" in raw.decode("utf-8") and "✅" in raw.decode("utf-8"),
            f"中文与符号都落到了字节上（{raw[:40]!r}）")

    # 3) 改不动的流不许把调用方带崩：--windowed 产物里 stdout 是 None / 不落字的
    #    NullWriter，没有 reconfigure；自检报告的价值远高于「顺便修好编码」
    got = _force_with_stdout(io.StringIO())        # 有 write、没有 reconfigure
    _assert(got is True, f"无 reconfigure 的流安静跳过（不算失败）（实际 {got!r}）")
    got = _force_with_stdout(None)                 # 打包 --windowed 的真实形态
    _assert(got is True, f"stdout 是 None 也不抛（CI 的产物门禁就长这样）（实际 {got!r}）")

    # 4) 四个会打印的入口都必须**在第一次打印之前**修好编码。它们是各自独立的进程
    #    入口（CI 脚本 / 源码 CLI / 产物自检 / 打包后的 --acl-doctor），谁都不能假设
    #    另外几个跑过；判据统一成「排在第一次打印之前」，而不是「代码里提过一句」。
    inject = (_REPO / ".github" / "scripts" / "inject_build_config.py").read_text(encoding="utf-8")
    entries = (
        ("CI 注入脚本", inject, 'reconfigure(encoding="utf-8"'),
        ("源码 CLI 入口", inspect.getsource(ag._cli_guarded), "_force_utf8_stdout()"),
        ("自检主流程", inspect.getsource(ag.run_doctor), "_force_utf8_stdout()"),
        ("产物自检入口", inspect.getsource(gui._acl_doctor_entry),
         "_tool_config.force_utf8_stdout()"),
    )
    for name, src, needle in entries:
        _assert(_prints_before(src, needle), f"{name} 第一次打印之前就修好了编码")
    # 「本地取密钥」那一支确实要 import tool_config（开发机上它就在旁边），所以
    # 判据只许管它之前的顶层部分；找不到这个分界就得直说，不能让下面那条断言
    # 因为 split 退化成整份文件而报一个看不出根因的错。
    _assert("def _from_local" in inject, "CI 脚本仍保留 --from-local 分支（本机打包同一条链）")
    head = inject.split("def _from_local")[0]
    head_code = "\n".join(ln for ln in head.splitlines() if not ln.strip().startswith("#"))
    _assert(not re.search(r"^\s*(import|from)\s+tool_config", head_code, re.M),
            "CI 脚本顶层的编码修法不依赖产物模块（模块全坏时它还得能说话）")
    _assert("HAS_TOOL_CONFIG" in inspect.getsource(gui._acl_doctor_entry),
            "tool_config 自己没加载进来时也不能抛（那正是门禁要报的故障）")

    # 5) 只许有一处实现：编码口径写三份，迟早一份改了另两份没改
    _assert_eq(len(re.findall(r"def force_utf8_stdout", _TC_SRC)), 1,
               "force_utf8_stdout 只有 tool_config 一个定义")
    _assert_eq(len(re.findall(r'reconfigure\(encoding="utf-8"', _TC_SRC)), 1,
               "tool_config 里真正改编码的语句只有一处")


# ══════════════════════════════════════════ L. 自检开关的口径一致
def test_doctor_flag_parity():
    print("\n═══ L. 自检开关：CI 写法、CLI 写法、界面写法必须是同一件事 ═══")
    import b2bcollect_gui as gui

    # 1) 两种写法在三个地方都得认：CI 的 yml 写 --acl-doctor，管理员手敲源码 CLI
    #    看帮助是 --doctor，产物 exe 收哪个由界面分派决定。历史上这三处各写一份，
    #    拼错的一方不会报错，而是**安静地走另一条路**（见下面第 3 条）。
    _assert_eq(set(ag.DOCTOR_FLAGS), {"--doctor", "--acl-doctor"},
               "access_guard 声明的自检开关就这两种写法")
    _assert_eq(set(gui._DOCTOR_FLAGS_FALLBACK), set(ag.DOCTOR_FLAGS),
               "界面那份退路副本与 access_guard 逐字一致（漏收权限模块时仍要认得）")

    # 三个发包出口都要用同一个写法：两个 CI workflow + 本机 Mac 打包脚本。
    # 少查一个出口，就等于允许那个出口用一个产物认不出来的开关。
    wf_dir = _REPO / ".github" / "workflows"
    exits = [(wf_dir / "build-b2bcollect.yml", True),
             (wf_dir / "build-b2bcollect-mac.yml", True),
             (_REPO / "debug" / "build_b2bcollect_mac_local.sh", False)]
    for p, in_ci in exits:
        name = p.relative_to(_REPO).as_posix()
        if not p.exists():
            _assert(False, f"{name} 必须还在（发包门禁的定义处）")
            continue
        text = p.read_text(encoding="utf-8")
        used = set(re.findall(r"(--acl-doctor|--doctor)", text))
        _assert(used and used <= set(ag.DOCTOR_FLAGS),
                f"{name} 里调自检用的写法都在口径内（实际 {sorted(used)}）")
        _assert("acl_doctor.txt" in text or "--acl-doctor" not in text,
                f"{name} 自检失败时会把报告原文打出来（不只剩一个退出码）")
        if in_ci:
            # CI 里必须有超时：这一步最坏的失败不是非零退出，而是一声不响地卡住
            _assert("timeout-minutes" in text,
                    f"{name} 的自检门禁带超时（卡住比非零退出更难查）")

    # 2) 界面分派本身：两种写法都进自检；带 doctor 字样却拼错的**不许**掉去建界面
    for argv, want in ((["--acl-doctor"], (True, [])),
                       (["--doctor"], (True, [])),
                       (["--windowed", "--doctor"], (True, [])),
                       ([], (False, [])),
                       (["--doctors"], (False, ["--doctors"])),
                       (["--acl-doctor-x"], (False, ["--acl-doctor-x"]))):
        _assert_eq(gui._doctor_request(argv), want, f"界面分派 {argv}")

    # 3) 反向确认这条分支真的会「挂住 CI」：拼错时主流程走到的是建界面那一行，
    #    所以这里必须靠兜底把它拦下来——判据直接读源码里的先后顺序。
    src = inspect.getsource(gui.main)
    _assert(src.index("if wrong:") < src.index("QApplication(sys.argv)"),
            "认不出的自检写法在 QApplication 之前就被拦下（否则 runner 挂到超时）")

    # 4) 权限模块没加载时退回本地常量：那正是门禁要报的故障，开关必须先认得出来
    with _patch(gui, "HAS_ACL_GUARD", False):
        _assert_eq(set(gui._doctor_flags()), set(gui._DOCTOR_FLAGS_FALLBACK),
                   "access_guard 缺席时仍认得两种写法（用退路副本）")
    with _patch(ag, "DOCTOR_FLAGS", ("--only-one-way",)):
        _assert_eq(gui._doctor_flags(), ("--only-one-way",),
                   "口径只认 access_guard 那一份，界面不另立一套")

    # 5) 源码 CLI 侧：两种写法都要真的走到自检，且「只读」约束对两种写法同样成立
    for flag in ag.DOCTOR_FLAGS:
        seen = []
        with _patch(ag, "run_doctor", lambda *a, **k: (seen.append(1), 0)[1]):
            ag._cli([flag])
        _assert_eq(seen, [1], f"python -m access_guard {flag} 真的跑了自检")
        _assert_raises(SystemExit,
                       lambda: ag._cli([flag, "--add", "13417047813"]),
                       f"{flag} 与写操作混用时当场拒绝（自检读到的会是刚写下去的东西）")

    # 6) 退出码全表由分类表派生：加了新故障类别却忘了补标签，这里立刻红
    codes = [c for c, _ in ag._DOCTOR_EXIT_TABLE]
    _assert_eq(len(codes), len(set(codes)), "退出码不重复")
    declared = {v for k, v in vars(ag).items() if k.startswith("DOCTOR_EXIT_")
                and isinstance(v, int)}
    _assert_eq(declared, set(codes),
               "每一个 DOCTOR_EXIT_* 常量都进了全表（漏一个=那种故障在帮助里没名字）")
    _assert_eq(set(ag._DOCTOR_KINDS), set(ag._DOCTOR_KIND_CODE),
               "分类表与退出码表同源（派生出来的，不是又抄一份）")
    for kind, (code, label) in ag._DOCTOR_KINDS.items():
        _assert(label and code != ag.DOCTOR_EXIT_OK, f"{kind} 有码也有短标签")
    _assert("8" in ag.DOCTOR_EXIT_HELP and "加密被拦" in ag.DOCTOR_EXIT_HELP,
            f"帮助文本里能数出全部类别（本轮就吃过「帮助只列 6 个码」的亏）："
            f"{ag.DOCTOR_EXIT_HELP}")


# ════════════════════════════════ M. workflow 步骤的脚本与 shell 必须配套
_BASH_ONLY = ("[ -f ", "[ -n ", "] ||", "|| {", "rc=$?", "$((", "&& {")
_PS_ONLY = ("Test-Path", "Start-Process", "Write-Host", "$env:", "$($", "-PassThru")


def _step_region(text: str, step_name: str):
    """取出某个 step 的正文：从它的 ``- name:`` 到下一个 ``- name:`` 之前。

    不用 PyYAML：这套离线测试跑在任何一台开发机上，多一个依赖就多一类「本机绿、
    别人红」。所以只用最笨的定位，并让调用方先确认这一步真的存在。
    """
    i = text.find(f"- name: {step_name}")
    if i < 0:
        return None
    j = text.find("- name: ", i + 1)
    return text[i:(j if j > 0 else len(text))]


def _iter_steps(text: str):
    """按 ``- name:`` 把 workflow 切成一个个 step 正文。

    不用 PyYAML：这套离线测试要跑在任何一台开发机上，多一个依赖就多一类
    「本机绿、别人红」。定位只需最笨的切法，判据是「这一步的正文里出现了什么」。
    注释行整体剔掉：注释里引用另一种 shell 的写法是写给维护者看的，不是要执行的。
    """
    heads = [(m.start(), m.group(1).strip())
             for m in re.finditer(r"^\s*-\s+name:\s*(.+)$", text, re.M)]
    for i, (pos, name) in enumerate(heads):
        end = heads[i + 1][0] if i + 1 < len(heads) else len(text)
        body = "\n".join(ln for ln in text[pos:end].splitlines()
                         if not ln.strip().startswith("#"))
        yield name, body


def _step_shell(region: str, default: str) -> str:
    """这一步真正由哪个 shell 执行：显式声明优先，没声明就是 runner 的默认值。"""
    m = re.search(r"^\s*shell:\s*(\w+)", region, re.M)
    return (m.group(1).strip() if m else default)


def test_workflow_shell_syntax():
    print("\n═══ M. workflow：写的脚本必须和这一步真正用的 shell 对上 ═══")
    # 本轮 CI 的第二种红法：Windows 上 run 步**默认**是 pwsh，而构建前那道
    # 「config.json 不在位就不许出包」的硬门是 bash 写法，PowerShell 把 `[ -f ...`
    # 当类型表达式解析，ParserError 之后 PyInstaller 根本没启动。这类错误不在任何
    # 一行的逻辑里，只在「脚本语言」和「解释器」的接缝上，本机永远测不出来。
    wf = _REPO / ".github" / "workflows"
    win = (wf / "build-b2bcollect.yml").read_text(encoding="utf-8")
    mac = (wf / "build-b2bcollect-mac.yml").read_text(encoding="utf-8")
    _assert(len(list(_iter_steps(win))) >= 6 and len(list(_iter_steps(mac))) >= 6,
            "两个 workflow 都切得出至少 6 个 step（切不出来=定位失效）")

    # 1) 通用不变量：每一个 step 的脚本语法都得配上它的 shell。
    #    只在真的扫到某种写法时才下结论——每条都报一句会把 60 行无关的 ✅ 灌进
    #    输出里，而那些 ✅ 一条都没有回答「万一改坏了会怎样」。
    seen_bash = seen_ps = 0
    for text, runner, default in ((win, "Windows", "pwsh"), (mac, "macOS", "bash")):
        for name, region in _iter_steps(text):
            shell = _step_shell(region, default)
            bashy = [b for b in _BASH_ONLY if b in region]
            psky = [p for p in _PS_ONLY if p in region]
            is_bash = shell in ("bash", "sh")
            seen_bash += bool(bashy)
            seen_ps += bool(psky)
            if bashy:
                _assert(is_bash,
                        f"{runner}「{name}」里的 bash 写法 {bashy} 配的是 shell={shell}")
            if psky:
                _assert(not is_bash,
                        f"{runner}「{name}」里的 PowerShell 写法 {psky} 配的是 shell={shell}")
    _assert(seen_bash >= 3 and seen_ps >= 1,
            f"扫描确实找到了待配对的写法（bash 步骤 {seen_bash} 个 / PowerShell 步骤 {seen_ps} 个）")

    # 2) 关键步骤的存在性与各自的手法：这三样一旦被人顺手改回去，包就又变成
    #    「没凭证也照样出」「自检拿不到退出码」
    build = _step_region(win, "Build EXE with PyInstaller")
    _assert(build is not None and _step_shell(build, "pwsh") == "bash",
            "Windows 构建步骤显式 shell: bash（bash 硬门才生效，也不依赖默认值）")
    gate = _step_region(win, "Artifact doctor gate (B2bCollectTool.exe --acl-doctor)")
    _assert(gate is not None, "Windows 产物自检门禁步骤还在（发包前最后一道门）")
    if gate:
        _assert(_step_shell(gate, "bash") == "pwsh", "自检门禁显式声明 pwsh（不靠默认值）")
        for need in ("Start-Process", "-Wait", "-PassThru", "$p.ExitCode"):
            _assert(need in gate, f"自检门禁保留了 {need}（GUI 子系统程序靠它才拿得到退出码）")

    # 3) 两个平台的构建前硬门必须是**同一句**：少一边，那一边就是「没凭证也照出包」
    _assert("[ -f config.json ]" in win and "[ -f config.json ]" in mac,
            "两个 workflow 都在构建前检查随包 config.json")
    for text, path in ((win, "Windows"), (mac, "Mac")):
        _assert("不许出包" in text, f"{path} 的构建前硬门说清了后果（不是只 exit 1）")

    # 4) M 组的第三种接缝错：不是「语法配不上解释器」，而是**报错那一行自己配不上变量名**。
    #    `$MK（…）` 这种写法在 bash 里会把全角括号/全角逗号的字节一起读进变量名：开
    #    `set -u` 的步骤直接 unbound variable 炸掉（炸的是那句解释失败原因的 echo），
    #    没开的更坏——值被静默印成空，「退出码 ）」。本轮是给 NSIS 步骤干跑时当场撞上的，
    #    而同类写法在 Mac 那两个入口里已经躺了两处。判据用构建前硬门那一份，不另抄规则。
    g = _manifest_gate()
    local_sh = (_REPO / "debug" / "build_b2bcollect_mac_local.sh").read_text(encoding="utf-8")
    for label, text in (("本机 Mac 打包脚本", local_sh),
                        ("Windows workflow", win), ("Mac workflow", mac)):
        got = g.check_shell_hygiene(g._strip_comments(text))
        _assert(not got, f"{label} 里没有 $VAR 紧跟非 ASCII 的写法（{got[:1]}）")
    for script in sorted((_REPO / ".github" / "scripts").glob("*.sh")):
        got = g.check_shell_hygiene(script.read_text(encoding="utf-8", errors="replace"))
        _assert(not got, f"{script.name} 的报错文案不会自己把变量名读坏（{got[:1]}）")
    # 反向对照：判据必须真的抓得住，而不是「现在恰好没样本」。
    _assert(len(g.check_shell_hygiene('echo "退出码 $RC，见上"')) == 1,
            "反向对照：$RC 紧跟全角逗号必须判红")
    _assert(g.check_shell_hygiene('echo "退出码 ${RC}，见上"') == [],
            "反向对照：写成 ${RC} 就放行（判据抓的是写法，不是中文）")
    _assert(g.check_shell_hygiene('# 注释里的 $RC，不会被执行') == [],
            "反向对照：注释行不算（否则每条中文注释都要加花括号，门禁立刻沦为噪音）")
    # 本轮那三处实际坏掉的行，钉成「现在这个样子」：顺手改回去时用例不会跟着改。
    _assert("${RC}，报告见上" in local_sh,
            "本机脚本失败分支把退出码印得出来（${RC} 而不是 $RC）")
    _assert("退出码 ${rc}）" in mac, "Mac workflow 自检门禁的失败文案同理")

    # 5b) 同一份判据盖住交付链上另外几份手写 shell 脚本（派发云打包、取制品、轮询 CI、
    # 批跑回归）。它们坏的时候恰好都是「排障现场最该看见那个数字」的时刻。登记表里的
    # 脚本不在位或没提交也要红——一条静默不跑的判据比没有判据更危险（会让人以为验过）。
    _assert_eq(g.check_script_hygiene(), [], "交付链 shell 脚本：文案卫生 + 在位 + 已提交")
    for rel in g.HYGIENE_SCRIPTS:
        _assert((_REPO / rel).is_file(), f"登记的脚本在位：{rel}")
    _assert("debug/run_offline_regression.sh" in g.HYGIENE_SCRIPTS,
            "回归入口自己也在判据范围内（它坏掉的那一行正是批跑要报红的那一行）")
    # 反向对照：登记表读空了这条判据就永远绿，所以先证明它抓得住真问题
    _saved_scripts = g.HYGIENE_SCRIPTS
    try:
        g.HYGIENE_SCRIPTS = ("debug/这个脚本不存在.sh",)
        _gone = g.check_script_hygiene()
        _assert(any("这个脚本不存在.sh" in p and "不在位" in p for p in _gone),
                f"登记了却读不到必须红（判据不许静默空转）：{_gone}")
        g.HYGIENE_SCRIPTS = ("debug/b2bcollect_bad_hygiene_probe.sh",)
        probe = _REPO / "debug" / "b2bcollect_bad_hygiene_probe.sh"
        probe.write_text('echo "清单不完整：$missing（见上）"\n', encoding="utf-8")
        try:
            _assert(any("紧跟" in p for p in g.check_script_hygiene()),
                    "登记过的脚本里出现坏写法同样要红")
        finally:
            probe.unlink(missing_ok=True)
    finally:
        g.HYGIENE_SCRIPTS = _saved_scripts

    # 5c) 批跑清单的完整性：交付目录里靠命名约定**自动发现**，这条判据核对「磁盘上有、
    # 清单里没有」。riskbird_build 那 13 个套件就是这么整目录消失了两年的——写完当天
    # 跑绿，此后随包代码怎么改都不红（本轮手工补跑才发现一条真实上传缺陷）。
    proc = subprocess.run(["bash", str(_REPO / "debug" / "run_offline_regression.sh"), "--list"],
                          capture_output=True, text=True, cwd=str(_REPO))
    _assert_eq(proc.returncode, 0, f"回归入口能解析清单（stderr={proc.stderr[-200:]}）")
    listed = {ln.strip() for ln in proc.stdout.splitlines() if ln.strip().endswith(".py")}
    on_disk = {p.relative_to(_REPO).as_posix() for d in g.SHARED_SOURCE_DIRS
               for p in (_REPO / d).glob("test_*_offline.py")}
    _assert_eq(sorted(on_disk - listed), [],
               f"三个交付目录里每个 test_*_offline.py 都在批跑清单里（清单共 {len(listed)} 项）")
    _assert(len(on_disk) >= 20, f"自动发现真的跑起来了（发现 {len(on_disk)} 个，少于 20 就是约定没匹配上）")
    for junk in ("append_offline_14.py", "append_offline_15.py", "fix_offline_14_5.py"):
        _assert(not any(junk in x for x in listed),
                f"一次性源码补丁脚本不许进批跑（跑一次就往套件里再插一段）：{junk}")

    # 5) 安装包编译器不许「赌这台机器恰好装了什么」：本轮 Windows 就是红在 CI 全绿、
    #    产物与自检都过、最后一步找不到 makensis ——镜像里 NSIS 没了，而本机永远复现不了。
    #    判据只看这一步自己的手法：钉版本、钉 sha256、校验不过就停、解压后还要问一次
    #    版本；并且不许再留「去 Program Files 里找」的旧写法（那条路等于把交付形态
    #    交给别人的镜像决定）。
    prov = _step_region(win, "Provision NSIS")
    _assert(prov is not None, "Windows workflow 里 NSIS 的准备步骤还在（安装包不能靠镜像）")
    if prov:
        _assert(_step_shell(prov, "pwsh") == "bash", "NSIS 准备步骤显式 bash")
        m = re.search(r'NSIS_SHA256:\s*"([0-9a-f]{64})"', prov)
        _assert(m is not None, "NSIS_SHA256 钉的是 64 位十六进制（占位串或空值都算没钉）")
        _assert(re.search(r'NSIS_VERSION:\s*"(\d+\.\d+)"', prov) is not None,
                "NSIS_VERSION 钉了具体版本（不钉就会出现「今天能打包、明天打的不是同一个安装包」）")
        for need in ("sha256", "exit 1", "::error::", "-V"):
            _assert(need in prov, f"NSIS 准备步骤保留了 {need}（下载/校验/版本确认任一缺失都是拿坏编译器出包）")
        # 本轮那次 CI 红的不是 NSIS，是**校验哈希的那一行自己**：Git Bash 里
        # $RUNNER_TEMP 是带反斜杠的 Windows 路径，而 GNU coreutils 遇到「文件名里含反斜杠」
        # 会给整行输出加一个 `\` 前缀，`| awk '{print $1}'` 于是取出 `\fcdce…`——
        # 哈希本身一字不差，比对却永远不过。判据不许依赖字段位置，也不许把文件名喂进输出。
        # 只看**会被执行的行**：注释里正是写着那种错写法（写给维护者看的），拿原文判
        # 就是「解释了 bug 的行自己被当成 bug」。
        prov_code = g._strip_comments(prov)
        _assert("sha256sum < " in prov_code and 'sha256sum "$1"' not in prov_code,
                "哈希只从内容算（文件名参与输出时 GNU 会整行加转义前缀，Windows 路径必踩）")
        _assert('grep -oE "[0-9a-fA-F]{64}"' in prov_code
                and "| awk '{print $1}'" not in prov_code and "sed -n 2p" not in prov_code,
                "哈希值按 64 位十六进制取，不按行号/字段位置切（切歪了就是「值对、比对失败」）")
    inst = _step_region(win, "Build Windows installer (NSIS, per-user)")
    _assert(inst is not None and "Program Files (x86)/NSIS" not in inst
            and "这台 runner 上没有 makensis" not in win,
            "安装包步骤不再在 runner 上找 NSIS（找不找得到是镜像的事，不该决定用户有没有安装包）")
    _assert("MAKENSIS=$MK\" >> \"$GITHUB_ENV" in prov and "${MAKENSIS:-}" in inst,
            "编译器路径由准备步骤写进 GITHUB_ENV、安装步骤只认这一个来源")


# ══════════════════════════════════════════════ N. 打包清单硬门 + 采集通道完整性
class _NoSuchKeyClient:
    """凭证通、网络通、但名单还没播种过的 OSS（自检里唯一那条提前返回的分支）。"""

    bucket = "images6396289"
    endpoint = "oss-cn-shenzhen.aliyuncs.com"

    def download_json(self, key, *a, **kw):
        raise OSSPoolError("404 NoSuchKey: 指定的名单文件不存在")


def test_packaging_manifest_gate():
    """2026-11-12 实测：等级 0 的账号在产物里只看到企知道 —— 根因是三处入口的
    --add-data 漏收了 riskbird_crawler_v2.py / aiqicha_crawler_v2.py。

    「少一个文件不会报错、只会少一个功能」是这一类缺陷的共同形状，而且它在界面上和
    「档位没给这个功能」一模一样，第一轮排查必然被带去查权限。所以六个方向都要钉住：
      1) 清单真源（site_adapters.EXTERNAL_MODULES）与三处入口逐条对拍，含目标目录；
      2) 硬门脚本自己判得对不对（反例：删一条、放错目录，都必须红）；
      2b) 第 7 条——要进包的源码必须已在**提交**里（本机绿、CI 检出后没文件就是它）；
      2c) 第 8 条——清单顺着 import 走必须闭合，故意让位给本目录的那几笔要登记理由；
      2d) 第 10 条——三个交付目录里的同名拷贝要么字节一致、要么登记分叉理由（生效的那
          份由 import 顺序决定，「一致但都是旧的」还要按行为钉住）；
      3) 产物自检的通道完整性（打包态退 3、源码态只警告、那条提前返回不放过）；
      4) 界面说话只点名「该给却没给」的那几路，档位藏起来的路绝不念出来。
    """
    print("\n═══ N. 打包清单硬门 + 采集通道完整性（少一路=静默少功能）═══")
    gate = _manifest_gate()
    externals, channels = gate.module_manifest()
    _assert_eq(set(channels), {"qzd", "riskbird", "aiqicha"},
               "名单通道应有路数 = 企知道 / 风鸟 / 爱企查 三路")
    _assert(any("riskbird_crawler_v2.py" in r for r in externals)
            and any("aiqicha_crawler_v2.py" in r for r in externals),
            "两个 v2 采集器在清单真源里（正是这次漏掉的那两个文件）")

    # 1) 三处入口逐条对拍（正例）
    modules = gate.local_modules()
    _assert(len(modules) >= 40, f"本地模块清单扫出来了（{len(modules)} 个）")
    for label, path in gate.CALLERS.items():
        _assert(path.is_file(), f"{label} 入口在位：{path}")
        problems = gate.check_caller(label, path.read_text(encoding="utf-8", errors="replace"),
                                     externals, modules)
        _assert_eq(problems, [], f"{label} 过打包清单硬门（任何一条不合格都拦在开建之前）")

    # 2) 硬门自己判得对不对：两个反例必须红，否则「门禁全绿、用户少功能」会原样复发
    local = gate.CALLERS["本机 Mac 打包脚本"].read_text(encoding="utf-8")
    cut = "\n".join(ln for ln in local.splitlines() if "riskbird_crawler_v2.py" not in ln)
    _assert(cut != local, "反例①构造成功（删掉那条 --add-data；没删掉就是空跑）")
    got = gate.check_caller("反例①", cut, externals, modules)
    _assert(any("riskbird_crawler_v2.py" in p and "缺跨目录模块" in p for p in got),
            f"漏一条 --add-data 必须被点名（实际={got}）")
    moved = local.replace("riskbird_build/riskbird_crawler_v2.py:riskbird_build",
                          "riskbird_build/riskbird_crawler_v2.py:.")
    _assert(moved != local, "反例②构造成功（目标目录写成 .）")
    got2 = gate.check_caller("反例②", moved, externals, modules)
    _assert(any("riskbird_crawler_v2.py" in p and "目标目录不是 riskbird_build" in p for p in got2),
            f"文件进了包但落错目录同样要红（运行时按 _MEIPASS/riskbird_build/ 找不到）：{got2}")
    # 注释里的参数名不是参数：这颗坑在硬门和本用例里各踩过一次，解析器现在只有一份
    _assert_eq(gate._flag_values(gate._strip_comments(
                   "# --hidden-import 注释里的假项\n--hidden-import real_mod\n"),
                   "--hidden-import"), ["real_mod"],
               "整行注释被剔掉，只有真正会执行的参数进清单")

    # 2b) 第 7 条「进包源码必须已在提交里」。这是 2026-11-12 的第二刀：清单写得对、
    # 磁盘上文件也在、本机打包全绿，唯独没 git add —— CI 检出后文件压根不存在，两个
    # 平台的构建一起红在「--add-data 指向的源码不存在」。判据必须能区分三种状态：
    # 在提交里 / 只 add 没提交 / 压根没跟踪，后两种都要红，而且要点名下一步命令。
    probe = gate.ROOT / "_gate_untracked_probe.py"
    _assert(not probe.exists(), "反例③起点干净（探针文件不存在）")
    probe.write_text("# 硬门自审用探针，跑完即删\n", encoding="utf-8")
    try:
        _assert_eq(gate.tracked_state(probe), "missing", "新文件＝压根没被跟踪")
        _assert_eq(gate.tracked_state(gate.ADAPTERS), "ok", "已提交的源码＝在提交里")
        got3 = gate.check_tracked([("../_gate_untracked_probe.py", ".")])
        _assert(any("_gate_untracked_probe.py" in p and "从未被 git 跟踪" in p
                    for p in got3), f"未跟踪的进包源码必须红并给出 git add（实际={got3}）")
        # 分隔符两种写法都要能换算到仓库内路径：Windows 用 ; 、类 Unix 用 :
        _assert_eq(gate._split_add_data("../x.py;pkg")[1], "pkg", "Windows 分号拆得开")
        _assert_eq(gate._split_add_data("../x.py:pkg")[1], "pkg", "类 Unix 冒号拆得开")
        _assert_eq(gate._package_input("../riskbird_build/x.py").name, "x.py",
                   "跨目录 --add-data 换算到仓库内路径（第 7 条判的就是它）")
        # 有意不提交的那类（Secrets 现写 / CI 现下载）必须在表里写理由，不能靠沉默放行
        got3c = gate.check_tracked([("config.json", ".")])
        _assert_eq(got3c, [], "GENERATED_AT_BUILD 登记过的输入不误伤（否则门禁变噪音）")
    finally:
        probe.unlink(missing_ok=True)
    # 同一形状的另外两个入口：本目录新模块（靠 --hidden-import 进包）与入口命令引用的
    # 构建期脚本。少提交它们的下场分别是一句谁也看不懂的 "Hidden import not found" 和
    # 「门禁脚本自己没检出」，比少一条 --add-data 更难查，所以一并判。
    mod_probe = gate.BUILD_DIR / "zz_gate_probe_module.py"
    _assert(not mod_probe.exists(), "反例④起点干净（本地模块探针不存在）")
    mod_probe.write_text("# 硬门自审探针，跑完即删\n", encoding="utf-8")
    try:
        _assert("zz_gate_probe_module" in gate.local_modules(),
                "新增的本地模块会进 local_modules（说明 --hidden-import 那条判据也看得见它）")
        got7 = gate.check_tracked([], gate.CALLERS["Mac workflow"].read_text(encoding="utf-8"))
        _assert(any("zz_gate_probe_module.py" in p and "--hidden-import" in p for p in got7),
                f"本地模块没提交同样要红（实际={[p for p in got7 if 'zz_gate' in p]}）")
    finally:
        mod_probe.unlink(missing_ok=True)
    got8 = gate.check_tracked([], gate.CALLERS["Mac workflow"].read_text(encoding="utf-8"))
    _assert_eq([p for p in got8 if "zz_gate" in p], [], "探针删掉后立刻不再报（不是残留误报）")
    _assert(any(".github/scripts/" in str(p) for p in
                gate.referenced_inputs(gate.CALLERS["Mac workflow"].read_text(encoding="utf-8"))),
           "入口引用的构建期脚本被扫进第 7 条的范围（缺一个连门禁自己都跑不起来）")
    # 分隔符：git 在任何平台都输出正斜杠，而 Windows 上 str(Path(...)) 给的是反斜杠。
    # 两边直接比对的结果是 2026-11-12 那次 Windows 构建里**每一个**进包文件都被判成
    # 「从未被 git 跟踪」——门禁自己坏了，比没有门禁更糟。Mac 上复现不出来，所以这里
    # 用「源码文本 + 嵌套路径」两条一起钉住：前者防写法改回去，后者防判据只测过顶层。
    gate_src = (gate.ROOT / ".github" / "scripts" /
                "check_packaging_manifest.py").read_text(encoding="utf-8")
    _assert("path.relative_to(ROOT).as_posix()" in gate_src,
            "_rel_posix 用 as_posix 归一分隔符（不写归一，Windows runner 上整条清单全红）")
    _assert("rel = _rel_posix(path)" in gate_src,
            "tracked_state 只比归一后的路径，不拿 str(Path) 去撞 git 的输出")
    _assert_eq(gate._rel_posix(gate.BUILD_DIR / "site_adapters.py"),
               "b2bcollect_build/site_adapters.py",
               "比对用的说法与 git 一致（子目录也要是正斜杠）")
    _index, _head = gate._tracked_sets()
    _assert(all("\\" not in x for x in _head),
            "git 列出来的路径里没有反斜杠（这条就是上面那处归一的现实依据）")
    # 同一次实跑里抓出来的第二个坑：git 默认把非 ASCII 文件名八进制转义再套引号，
    # 那种字符串跟任何真实路径都对不上，于是每个中文名文件都被判成「没提交」。
    # 判据跨平台的另一半含义是**跨文件名**，所以这里直接拿仓库里真有的中文名文件验。
    cjk = next((x for x in sorted(_head) if not x.isascii()), "")
    _assert(cjk, f"仓库里确实有非 ASCII 路径，下面这条不是空跑（HEAD 清单 {len(_head)} 项）")
    _assert_eq(gate.tracked_state(gate.ROOT / cjk), "ok",
               f"中文名文件也认得（不加 -z 拆 NUL 就会被 git 的转义说法骗过去）：{cjk}")
    sub = gate.BUILD_DIR / "zz_gate_probe_pkg"
    sub.mkdir(exist_ok=True)
    nested = sub / "mod.py"
    nested.write_text("# 分隔符探针，跑完即删\n", encoding="utf-8")
    try:
        _assert_eq(gate.tracked_state(nested), "missing",
                   "子目录里未跟踪的文件也认得（分隔符判错的话这条在 Windows 上是唯一漏网）")
    finally:
        nested.unlink(missing_ok=True)
        sub.rmdir()
    # 真清单实跑：三处入口的进包输入此刻都必须已在提交里（这条红＝现在推上去 CI 必红）
    _assert_eq(gate.check_tracked(
        [gate._split_add_data(t) for t in gate._flag_values(
            gate._strip_comments(gate.CALLERS["Mac workflow"].read_text(encoding="utf-8")),
            "--add-data")]), [], "Mac workflow 的进包输入全部已在提交里")

    # 2c) 第 8 条「跨目录依赖链必须闭合」：清单是人手抄的，顺着 import 走一遍才敢收口。
    _assert_eq(gate.check_closure(externals, gate.shadowed_modules()), [],
               "EXTERNAL_MODULES 顺着 import 走是闭合的（含 SHADOWED 显式让位那几笔）")
    _assert("customer_library_cleaner" in gate.shadowed_modules(),
            "1688 清洗模块由本目录供给这件事是登记过的，不是碰巧 import 顺序对上")
    _assert((gate.BUILD_DIR / "customer_library_cleaner.py").exists(),
            "SHADOWED 说由本目录供给，本目录就得真有这个文件")
    no_shadow = {k: v for k, v in gate.shadowed_modules().items()
                 if k != "customer_library_cleaner"}
    got4 = gate.check_closure(externals, no_shadow)
    _assert(any("customer_library_cleaner" in p for p in got4),
            f"撤掉那笔让位登记，链就必须报红（否则第 8 条是空转）：{got4}")
    no_block = tuple(x for x in externals if not x.endswith("block_guard.py"))
    got5 = gate.check_closure(no_block, gate.shadowed_modules())
    _assert(any("block_guard.py" in p and "没进 EXTERNAL_MODULES" in p for p in got5),
            f"漏登记一个传递依赖同样要红（真漏，不是让位）：{got5}")
    got6 = gate.check_closure(externals + ("ali1688_build/customer_library_cleaner.py",),
                              gate.shadowed_modules())
    _assert(any("两份同名实现" in p for p in got6),
            f"让位登记与进包清单同时出现＝两份实现一起进包，必须红：{got6}")

    # 2d) 第 10 条「跨目录同名拷贝必须在案」。这条盯的不是「少文件」，是「多份文件里挑了
    # 一份旧的」：三个交付目录里有同名 .py，产物实际吃到哪一份由 sys.path 顺序决定
    # （riskbird_gui 把 ../b2bcollect_build 插到最前，b2bcollect 那份永远赢）。本轮实测的
    # 代价：两处上传侧修复只落在 riskbird 那份 yzd_client.py 里，进包生效的是 b2bcollect
    # 那份旧的——界面不报错、日志不报错、CI 全绿，客户库里却多了没有联系方式的行，超长
    # 手机号被从中间切成打不通的半截号。症状和「压根没写过这个修复」一模一样。
    _assert_eq(gate.check_shared_source(), [],
               "真实仓库现况：同名源码要么字节一致，要么在 DIVERGENT_VENDORED 登记了理由")
    copies = [gate.ROOT / d / "yzd_client.py" for d in gate.SHARED_SOURCE_DIRS]
    _assert(all(p.is_file() for p in copies), "yzd_client 三份拷贝都在位")
    _assert_eq(len({gate._file_digest(p) for p in copies}), 1,
               "yzd_client 三份字节一致（本轮同步的就是那两处上传侧修复）")
    # 一致 ≠ 对：三份一起停在旧实现，这条判据同样是绿的。所以还要按行为钉住生效的那一份
    import yzd_client as _yzd
    _numbers = ",".join(f"1380000000{i}" for i in range(9))     # 9 条 = 107 字符 > 上限 100
    _clamped = _yzd.clamp_fields({"手机号": _numbers})["手机号"]
    _assert(len(_clamped) <= _yzd.FIELD_LIMITS["手机号"], "超长号码列收进字段上限")
    _assert(all(_yzd.PHONE_RE.search(p) for p in _clamped.split(",")),
            f"收拢后每一条都是完整号码（旧实现 text[:100] 会把最后一条切成半截打不通）"
            f"（实际={_clamped!r}）")
    _assert(not _yzd.has_contact({"企业名称": "只有一家公司名", "手机号": "", "固话": "",
                                 "邮箱": "", "微信号": ""}),
            "三个联系方式全空的行判为「不可入库」（旧实现没有这道过滤）")

    # 反向对照：把判据放回到一个假仓库里跑四种现场，缺一种就说明它有一头是空转
    fake = Path(tempfile.mkdtemp(prefix="shared_gate_"))
    try:
        dirs = {}
        for _i, name in enumerate(gate.SHARED_SOURCE_DIRS):
            dirs[name] = fake / name
            dirs[name].mkdir(parents=True)
            # same.py 三份一致；fork.py 三份不同；test_only.py 只有一份且是测试文件
            # （分叉内容按下标生成，绝不能用 hash()：它是每进程加盐的，两台机器上可能
            #   撞成相同值，那下面几条「必须红」就全成了空跑）
            (dirs[name] / "same.py").write_text("A = 1\n", encoding="utf-8")
            (dirs[name] / "fork.py").write_text(f"B = {_i}\n", encoding="utf-8")
            # 测试文件三份故意不同：前缀判据一旦失效，这条就会把回归套件判成缺陷
            (dirs[name] / "test_only.py").write_text(f"# {_i}\n", encoding="utf-8")
        # 夹具检查：fork.py 必须真的分叉，否则下面几条「必须红」全是空跑
        _assert_eq(len({(d / "fork.py").read_bytes() for d in dirs.values()}),
                   len(gate.SHARED_SOURCE_DIRS), "反例起点：fork.py 三份内容互不相同")
        with _patch(gate, "ROOT", fake), _patch(gate, "DIVERGENT_VENDORED",
                                                {"fork.py": "两份按产品各留一份（假登记）"}):
            _assert_eq(gate.check_shared_source(), [],
                       "一致的不动、分叉的登记过 → 放行（test_ 前缀不参与）")
        with _patch(gate, "ROOT", fake), _patch(gate, "DIVERGENT_VENDORED", {}):
            got9 = gate.check_shared_source()
            _assert(any("fork.py" in p and "sys.path" in p for p in got9),
                    f"分叉又没登记必须红，还要说清「哪份生效由 import 顺序决定」：{got9}")
            _assert_eq([p for p in got9 if "same.py" in p or "test_only.py" in p], [],
                       "一致的文件与测试文件不被牵连（误报会把整条门禁消耗成噪音）")
        with _patch(gate, "ROOT", fake), _patch(gate, "DIVERGENT_VENDORED", {"fork.py": "  "}):
            got10 = gate.check_shared_source()
            _assert(any("fork.py" in p and "没写理由" in p for p in got10),
                    f"登记了却不写理由同样要红：{got10}")
        for d in dirs.values():                     # 后来同步了，登记却留着
            (d / "fork.py").write_text("B = 1\n", encoding="utf-8")
        with _patch(gate, "ROOT", fake), _patch(gate, "DIVERGENT_VENDORED",
                                                {"fork.py": "曾经分叉的理由"}):
            got11 = gate.check_shared_source()
            _assert(any("fork.py" in p and "该删了" in p for p in got11),
                    f"早已一致还挂着「允许不同」的登记要红（否则下次真分叉时这张表不值得信）"
                    f"：{got11}")
        with _patch(gate, "ROOT", fake), _patch(gate, "SHARED_SOURCE_DIRS",
                                                ("b2bcollect_build", "no_such_build")):
            got12 = gate.check_shared_source()
            _assert(any("no_such_build" in p for p in got12),
                    f"交付目录整个不见了也要红，不能当成「没有同名文件」悄悄放过：{got12}")
    finally:
        shutil.rmtree(fake, ignore_errors=True)

    # CLI 退出码语义：CI 靠它拦构建，返回 1 就等于门禁形同虚设
    proc = subprocess.run([sys.executable,
                           str(_REPO / ".github" / "scripts" / "check_packaging_manifest.py")],
                          capture_output=True, text=True, cwd=str(_REPO))
    _assert_eq(proc.returncode, 0, f"硬门脚本实跑通过（stderr={proc.stderr[-200:]}）")

    # 3) 产物自检的通道完整性
    import site_adapters as _sa
    import b2bcollect_gui as gui
    two_lost = {"qzd": True, "riskbird": False, "aiqicha": False}
    errs = {"riskbird": "FileNotFoundError: riskbird_crawler_v2.py 不存在",
            "aiqicha": "ImportError: selenium 没装"}
    with _patch(_sa, "LIST_CHANNEL_AVAILABLE", two_lost), _patch(_sa, "ADAPTER_ERRORS", dict(errs)):
        _assert_eq(_sa.missing_list_channels(), ("riskbird", "aiqicha"),
                   "缺路口径只看「加载上了没有」，与档位/等级无关")
        code_f, lines_f = ag._channel_integrity(frozen=True)
        _assert_eq(code_f, ag.DOCTOR_EXIT_MODULE, "打包态缺通道 → 自检退 3（不许发货）")
        code_s, lines_s = ag._channel_integrity(frozen=False)
        _assert_eq(code_s, ag.DOCTOR_EXIT_OK, "源码态只警告不判红（开发机没装 selenium 不该拖红 CI）")
        # 中文名按 slug 取，不是按位置：位置映射在增删通道时全体错位，会把风鸟说成爱企查
        _assert(any("产物里缺采集通道：风鸟 / 爱企查" in ln for ln in lines_f),
                f"报告按 slug 点名缺哪几路（实际={lines_f[:2]}）")
        _assert(any("不是权限问题" in ln for ln in lines_f),
                "报告直接把人从「查权限」这条错路上拦回来")
        _assert(any("check_packaging_manifest" in ln for ln in lines_f),
                "报告给出可执行的修法（发包方照着跑就能定位缺哪条）")

    # 唯一那条提前返回的成功分支（名单还没播种）不许放过通道判红
    with _patch(_sa, "LIST_CHANNEL_AVAILABLE", two_lost), _patch(_sa, "ADAPTER_ERRORS", dict(errs)):
        with _workspace() as _ws, _frozen(_ws):
            with _patch(AccessGuard, "_load_bound_account", staticmethod(lambda: "")), \
                 _patch(AccessGuard, "_client_probe", staticmethod(lambda: (_NoSuchKeyClient(), "ok", ""))):
                code_e, lines_e = ag.doctor_lines()
                _assert_eq(code_e, ag.DOCTOR_EXIT_MODULE,
                           "「名单没播种 + 包少平台」两件事叠在一起时，提前返回也必须带通道退出码")
                _assert(any("采集通道完整性不通" in ln for ln in lines_e),
                        f"报告说清为什么权限项过了还判红（实际={lines_e[-3:]}）")
    with _patch(_sa, "LIST_CHANNEL_AVAILABLE", {"qzd": True, "riskbird": True, "aiqicha": True}):
        with _workspace() as _ws, _frozen(_ws):
            with _patch(AccessGuard, "_load_bound_account", staticmethod(lambda: "")), \
                 _patch(AccessGuard, "_client_probe", staticmethod(lambda: (_NoSuchKeyClient(), "ok", ""))):
                code_ok, _ = ag.doctor_lines()
                _assert_eq(code_ok, ag.DOCTOR_EXIT_OK, "通道齐全时，名单没播种仍然判通过（不误伤首次使用）")

    # 3b) 关键词通道里那个**从外部目录装载**的 1688 也在同一道自检里。它与名单三路
    # 是同一个装载形状（运行时按路径 import 外部 .py），此前却只被名单判据覆盖：
    # 它依赖的 customer_library_cleaner 靠「本目录同名模块先进 sys.modules」这条隐式链，
    # 链一断就是「界面上少一个来源、自检全绿」——正是本轮要根治的形状，所以判据必须
    # 覆盖到它，报告也必须把这一路打印出来。
    _assert_eq(_sa.missing_keyword_channels(), (), "源码实跑：关键词外部通道装载成功")
    with _patch(_sa, "HAS_1688", False), _patch(_sa, "ADAPTER_ERRORS",
                                                {"1688": "ModuleNotFoundError: customer_library_cleaner"}):
        _assert_eq(_sa.missing_keyword_channels(), ("1688",),
                   "1688 没装载起来时判据认得它（读当前值，不是定义时的快照）")
        code_kw, lines_kw = ag._channel_integrity(frozen=True)
        _assert_eq(code_kw, ag.DOCTOR_EXIT_MODULE,
                   "打包态缺关键词外部通道同样退 3（与缺名单路同级，不许发货）")
        _assert(any("1688 阿里巴巴" in ln for ln in lines_kw),
                f"报告按登记名点名缺了哪一路（实际={lines_kw[:3]}）")
        _assert(any("不是权限问题" in ln for ln in lines_kw),
                "缺 1688 同样要把人从「查权限」这条错路上拦回来")
    with _patch(_sa, "HAS_1688", True):
        _c, _l = ag._channel_integrity(frozen=True)
        _assert_eq(_c, ag.DOCTOR_EXIT_OK, "1688 在位时不判红（不误伤正常包）")
        _assert(any("关键词通道（外部目录）：1 路齐全" in ln for ln in _l),
                f"齐全时也要打印出来——不打印就没人知道这一路被验过（实际={_l}）")

    # 4) 界面说话：只念「该给却没给」的那几路
    _old_ed = os.environ.get(_ed.ENV_VAR)
    _old_lv = os.environ.get(_ed.LEVEL_ENV_VAR)
    try:
        with _patch(_sa, "LIST_CHANNEL_AVAILABLE", two_lost), _patch(_sa, "ADAPTER_ERRORS", dict(errs)):
            os.environ[_ed.ENV_VAR] = "pro"
            os.environ[_ed.LEVEL_ENV_VAR] = "0"
            _ed.reset_for_tests()
            _assert_eq(gui._lost_list_channels(), ("riskbird", "aiqicha"),
                       "pro + 等级 0 该有三路：缺的两路必须报出来")
            os.environ[_ed.ENV_VAR] = "lite"
            os.environ[_ed.LEVEL_ENV_VAR] = "2"
            _ed.reset_for_tests()
            _assert_eq(gui._lost_list_channels(), (),
                       "丐版本来只给企知道：缺风鸟/爱企查不能在日志里点名内部平台（等于把藏起来的功能念出来）")
            # 档位藏起来的那几路即使真的缺失也不许出现在给外部用户看的文案里
            _lost_pro = gui.MainWindow._lost_channels_text(("riskbird", "aiqicha"), "风鸟 / 爱企查")
            _assert("不是账号权限" in _lost_pro and "找发包方" in _lost_pro,
                    "缺路文案三件事齐全：是什么、不是什么（权限）、怎么办（要新包）")
            _lines = [x for x in _lost_pro.strip().splitlines() if x]
            _assert(len(_lines) == 3 and _lines[-1].startswith("⚠ 处置"),
                    f"「怎么办」必须排在最后一条：收起态只看得见最后两三行（实际={_lines}）")
            # 「原因」那一行必须由实测到的加载错误决定：漏模块、漏依赖、初始化抛异常
            # 三种情况界面症状完全一样，说死其中一种就等于让发包方照着错的那句去查。
            _reason = gui.MainWindow._lost_channels_reason
            with _patch(_sa, "ADAPTER_ERRORS", {
                    "riskbird": "ModuleNotFoundError: No module named 'riskbird_crawler_v2'",
                    "aiqicha": "ImportError: No module named 'cv2'"}):
                _r = _reason(("riskbird", "aiqicha"))
                _assert("缺了它们运行所需的文件" in _r and "采集器本身或它的依赖" in _r,
                        f"全是「找不到文件」这一类时才说漏文件，且把依赖也算进去"
                        f"（selenium/cv2 不是采集器文件）（实际={_r}）")
            with _patch(_sa, "ADAPTER_ERRORS", {
                    "riskbird": "AttributeError: 'NoneType' object has no attribute 'x'",
                    "aiqicha": "AttributeError: 缺模板图"}):
                _assert("本机加载这些采集器时报错了" in _reason(("riskbird", "aiqicha")),
                        "文件都在、初始化抛异常时说「加载报错」，不说漏文件")
            with _patch(_sa, "ADAPTER_ERRORS", {
                    "riskbird": "ModuleNotFoundError: No module named 'riskbird_crawler_v2'",
                    "aiqicha": "AttributeError: 缺模板图"}):
                _r2 = _reason(("riskbird", "aiqicha"))
                _assert("缺了、或本机加载不出" in _r2,
                        f"证据不止一种时退回覆盖得下的那句，不替某一路下结论（实际={_r2}）")
            with _patch(_sa, "ADAPTER_ERRORS", {}):
                _r = _reason(("riskbird",))
                _assert("缺了、或本机加载不出" in _r and "--doctor" in _r,
                        f"没有任何加载错误记录时不编原因，只把人指向自检报告（实际={_r}）")
            _detail = gui.MainWindow._lost_channels_detail(("riskbird", "aiqicha"))
            _assert("风鸟" in _detail and "爱企查" in _detail and "selenium 没装" in _detail,
                    f"逐路的错误原文进悬停说明（长句不进日志）（实际={_detail[:120]}）")
            _assert("check_packaging_manifest" in gui.MainWindow._lost_channels_detail(("riskbird",)),
                    "发包方自查命令挂在悬停说明里（长句不进日志，否则会把结论顶出可见区）")
            _assert_eq(gui.MainWindow._lost_channels_detail(()), "",
                       "一路不缺时连细节也不产（正常机器上不多一个字）")
            _assert_eq(gui.MainWindow._lost_channels_text((), ""), "",
                       "一路不缺时不产文案（正常机器上日志里一条也不会多）")
    finally:
        # 档位是进程级状态：不收回去，后面每一条用例都在 lite 里跑
        for _k, _v in ((_ed.ENV_VAR, _old_ed), (_ed.LEVEL_ENV_VAR, _old_lv)):
            if _v is None:
                os.environ.pop(_k, None)
            else:
                os.environ[_k] = _v
        _ed.reset_for_tests()


# ══════════════════════════════════════════════ O. 两条实机根因的判据
_WIN_ASM_MOD = None


def _win_assembler():
    """把 Windows 交付物装配脚本当模块载进来：它就是判据本体，这里不抄第二份。

    与 N 组的 `_manifest_gate()` 同一个道理——校验器与用例各写一套提取逻辑时，同一颗
    坑要踩两次；而 CI 与本机的制品校验器**必须**共用同一份装配判据（否则 CI 绿了、
    本机校验器却按另一套规矩说话，用户拿到的包由谁说了算就变成了运气）。
    """
    global _WIN_ASM_MOD
    if _WIN_ASM_MOD is None:
        path = _REPO / ".github" / "scripts" / "package_win_artifacts.py"
        spec = importlib.util.spec_from_file_location("_win_artifact_asm", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _WIN_ASM_MOD = mod
    return _WIN_ASM_MOD


def _fake_dist(root: Path, drop=(), stale_onefile=False, runtime_dirs=()):
    """搭一棵**合成**的 onedir 产物树：指定项可以缺席，用来验校验器自己判得对不对。"""
    app = root / "dist" / _win_assembler().APP_DIR_NAME
    internal = app / "_internal"
    internal.mkdir(parents=True, exist_ok=True)
    (app / _win_assembler().EXE_NAME).write_text("MZ", encoding="utf-8")
    asm = _win_assembler()
    for rel in asm.REQUIRED_INTERNAL:
        if rel in drop:
            continue
        target = internal / rel
        if rel.endswith("/") or rel == "enf_clean":
            (target / "keep.txt").parent.mkdir(parents=True, exist_ok=True)
            (target / "keep.txt").write_text("x", encoding="utf-8")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("x", encoding="utf-8")
    # installer/ 那两份也必须铺上：装配脚本在目录布局之外还校验它们的编码，缺席时
    # 校验不过就**不产出 zip**，于是「合成树装配通过」会红成一条看着像产物坏了的结果
    # ——那其实是夹具自己没铺全。这里写带 BOM 的合成桩而不是复制仓库里的真文件：
    # 夹具的职责只是「搭一棵合格的树」，真文件合规与否由下面第 3 段单独判（同一件事
    # 不留两处判据，否则真文件哪天改了名，这里会跟着红成一条莫名其妙的装配失败）。
    for rel in (asm.NSI_PATH, asm.GUIDE_SRC):
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(asm.BOM + "合成桩\n".encode("utf-8"))
    if stale_onefile:
        (root / "dist" / asm.EXE_NAME).write_text("MZ", encoding="utf-8")
    for name in runtime_dirs:
        (app / name / "history.json").parent.mkdir(parents=True, exist_ok=True)
        (app / name / "history.json").write_text("{}", encoding="utf-8")
    return app


@contextlib.contextmanager
def _hush():
    """收下被检对象自己往屏幕上的播报。

    反例段落要验的是**判据**（problems 列表里有没有那一条），而不是把校验器已经说过
    的话再刷一遍：它的 ❌ 混在用例结果里，读日志的人会以为用例失败了，而真正失败的
    那一条反而被淹掉——这类「夹具的动静冒充结论」在 K 组已经红过一次。
    """
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        yield buf


def test_windows_onedir_delivery():
    print("\n═══ O. Windows 交付形态：onefile 绝迹、装配判据共用一份 ═══")
    asm = _win_assembler()
    gate = _manifest_gate()

    # 1) 三个入口都不许再出现 --onefile（注释里提它是写给维护者看的，不算）
    wf = _REPO / ".github" / "workflows"
    win = (wf / "build-b2bcollect.yml").read_text(encoding="utf-8")
    mac = (wf / "build-b2bcollect-mac.yml").read_text(encoding="utf-8")
    local = (_REPO / "debug" / "build_b2bcollect_mac_local.sh").read_text(encoding="utf-8")
    for label, text in (("Windows workflow", win), ("Mac workflow", mac), ("本机脚本", local)):
        _assert("--onefile" not in gate._strip_comments(text),
                f"{label} 的构建命令里没有 --onefile（onefile 会原样复发 "
                "base_library.zip 找不到 + Cookie 每次归零）")
    _assert("--name B2bCollectTool" in gate._strip_comments(win),
            "Windows 仍然产出同一个名字（改的是形态，不是品牌）")

    # 2) 装配判据自己判得对不对：正例全绿 + 四个反例各自点名
    root = Path(tempfile.mkdtemp(prefix="windist_"))
    _fake_dist(root)
    problems = []
    with _hush():
        exe, internal = asm.check_layout(root, problems)
    _assert_eq(problems, [], f"合格的 onedir 布局一条也不报（实际={problems}）")
    _assert(exe is not None and internal is not None, "正例返回 exe 与 _internal 两个路径")

    bad = Path(tempfile.mkdtemp(prefix="windist_bad_"))
    _fake_dist(bad, drop=("riskbird_build/runtime_paths.py",))
    got = []
    with _hush():
        asm.check_layout(bad, got)
    _assert(any("runtime_paths.py" in p for p in got),
            f"_internal 少了 runtime_paths.py 必须点名（缺它开机即崩）：{got}")

    stale = Path(tempfile.mkdtemp(prefix="windist_stale_"))
    _fake_dist(stale, stale_onefile=True)
    got = []
    with _hush():
        asm.check_layout(stale, got)
    _assert(any("单文件" in p and "原样复发" in p for p in got),
            f"制品里混着旧的单文件 exe 必须判红（用户双击它就回到本轮修好的故障）：{got}")

    noapp = Path(tempfile.mkdtemp(prefix="windist_none_"))
    got = []
    with _hush():
        asm.check_layout(noapp, got)
    _assert(any("--onefile 又回来了" in p for p in got),
            f"连 onedir 目录都没有时，直接把最可能的原因说成 onefile 复发：{got}")

    nointernal = Path(tempfile.mkdtemp(prefix="windist_shell_"))
    (nointernal / "dist" / asm.APP_DIR_NAME).mkdir(parents=True)
    (nointernal / "dist" / asm.APP_DIR_NAME / asm.EXE_NAME).write_text("MZ", encoding="utf-8")
    got = []
    with _hush():
        asm.check_layout(nointernal, got)
    _assert(any("空壳" in p for p in got), f"只有 exe、没有 _internal 时判红：{got}")

    # 3) UTF-8 BOM：仓库里那两份真实文件必须已经合规，缺 BOM 的反例必须红
    real = []
    with _hush():
        asm.check_bom(_REPO / "b2bcollect_build" / asm.NSI_PATH, "NSIS", real)
        asm.check_bom(_REPO / "b2bcollect_build" / asm.GUIDE_SRC, "说明", real)
    _assert_eq(real, [], f"仓库里的安装脚本与使用说明都是 UTF-8 with BOM（实际={real}）")
    nobom = Path(tempfile.mkdtemp(prefix="nobom_")) / "x.nsi"
    nobom.write_text("中文", encoding="utf-8")
    got = []
    with _hush():
        asm.check_bom(nobom, "反例", got)
    _assert(any("BOM" in p for p in got),
            "没有 BOM 时必须判红：makensis 会按系统代码页解释，中文全乱码而构建照样成功")

    # 4) 装配次序：校验不过就到此为止；运行期目录绝不进 zip；zip 顶层是一个目录
    dirty = Path(tempfile.mkdtemp(prefix="windist_dirty_"))
    _fake_dist(dirty, runtime_dirs=asm.RUNTIME_DIRS)
    _rc = subprocess.run([sys.executable, str(_REPO / ".github" / "scripts" /
                            "package_win_artifacts.py"), "--root", str(dirty)],
                         capture_output=True, text=True, cwd=str(_REPO))
    _assert_eq(_rc.returncode, 0, f"合成产物树装配通过（stderr={_rc.stderr[-200:]}）")
    _assert(not any((dirty / "dist" / asm.APP_DIR_NAME / n).exists()
                    for n in asm.RUNTIME_DIRS),
            "runner 上 --acl-doctor 跑出来的状态目录被剔掉了（带着别人机器的登录状态"
            "和任务历史安装包不能发）")
    zpath = dirty / asm.PORTABLE_NAME
    _assert(zpath.is_file(), "校验通过才产出绿色版 zip")
    with zipfile.ZipFile(zpath) as z:
        names = z.namelist()
    _assert(all(n.startswith(asm.APP_DIR_NAME + "/") or n == asm.GUIDE_DEST for n in names),
            f"zip 顶层只有 {asm.APP_DIR_NAME}/ 与根上的使用说明（解压出来是一堆散文件的话，"
            f"用户会把它摊到桌面上）（实际={names[:3]}）")
    _assert(asm.GUIDE_DEST in names, "绿色版 zip 根上有使用说明（第一眼就看到）")
    _assert(not any(f"/{n}/" in f"/{x}" for x in names for n in asm.RUNTIME_DIRS),
            "运行期目录连一个条目都没进 zip")

    broken = Path(tempfile.mkdtemp(prefix="windist_broken_"))
    _fake_dist(broken, drop=("config.json",))
    _rc2 = subprocess.run([sys.executable, str(_REPO / ".github" / "scripts" /
                             "package_win_artifacts.py"), "--root", str(broken)],
                          capture_output=True, text=True, cwd=str(_REPO))
    _assert(_rc2.returncode != 0 and not (broken / asm.PORTABLE_NAME).exists(),
            "随包凭证缺失时非零退出**且不产出 zip**（否则又是一个 CI 绿、用户拿坏包）")

    # 5) CI 与本机的制品校验器共用这份判据，不抄第二份
    fetch = (_REPO / "debug" / "fetch_b2bcollect_artifacts.sh").read_text(encoding="utf-8")
    _assert("package_win_artifacts" in fetch and "check_layout" in fetch,
            "制品校验器 import 装配脚本复用 check_layout（两套规矩迟早对不上）")
    _assert("B2bCollectToolSetup" in fetch,
            "制品校验器要求安装包存在（只有绿色版目录 zip 等于交付形态没落地）")
    _assert("makensis" in win and "Package Windows artifacts" in win,
            "Windows workflow 里既有装配步也有 NSIS 安装包步")
    _assert(asm.SETUP_NAME in win and asm.PORTABLE_NAME in win,
            "安装包与绿色版两份产物都进上传清单")

    # 6) 交付文档与安装脚本、界面话术必须说同一件事：说明书写下的承诺没有代码撑着时，
    #    漂移没有任何运行期症状，只有用户在第一次安装时被问住（本轮说明书改过三处，
    #    每处都对应一个真实存在/不存在的目录，逐条对拍比读一遍可靠）。
    nsi = (_REPO / "b2bcollect_build" / asm.NSI_PATH).read_text(encoding="utf-8-sig")
    guide = (_REPO / "b2bcollect_build" / asm.GUIDE_SRC).read_text(encoding="utf-8-sig")
    _assert("RequestExecutionLevel user" in nsi
            and "RequestExecutionLevel admin" not in nsi,
            "安装脚本是每用户安装：说明书那句「全程不会弹管理员权限窗口」全靠这一行撑着")
    _assert("$LOCALAPPDATA\\Programs" in nsi and "管理员权限" in guide,
            "默认安装目录与说明书第一节写的是同一个位置（装进 Program Files 的话 "
            "Cookie 与缓存写不进去，那节讲的正是这个因果）")
    for d in ("b2bcollect_data", "riskbird_data", "b2bcollect_state"):
        _assert(d in guide, f"说明书第三节逐个写明 {d} 存的是什么（用户找日志不必猜）")
        _assert(d in asm.RUNTIME_DIRS,
                f"{d} 同时在装配剔除名单里（说明书承诺的目录与发包前剔的目录必须是同一批，"
                "否则 CI 上跑出自检报告就会随包发出去）")
        _assert(f'"$INSTDIR\\{d}"' in nsi,
                f"卸载脚本的「连数据一起删」清单覆盖 {d}（用户选了全删却还剩一个空目录，"
                "这种漏法没有症状，只有下次重装时才被发现）")
    _assert("aiqicha_data" not in guide,
            "说明书不列 aiqicha_data：爱企查与风鸟共用 riskbird_data，"
            "文档不许列出程序根本不会创建的目录（RUNTIME_DIRS 里那一项是留着防身的）")
    import b2bcollect_gui as _gui
    _assert(_gui.FATAL_LOG_NAME in guide,
            f"说明书里的崩溃日志名与界面落盘用的是同一个名字（{_gui.FATAL_LOG_NAME}）")
    _assert("App Translocation" in guide and "应用程序" in guide,
            "说明书 Mac 那节仍在讲只读副本这件事（界面横幅的出口要有文字版可对）")


def test_state_file_never_relative_cwd():
    print("\n═══ P. 运行期文件的落点：绝不相对当前工作目录 ═══")
    import audit_logger as _al
    import mapping_recommender as _mr

    # 1) 源码层面：两个默认值都不许再是「裸文件名」（那等于相对 CWD）
    for label, path, name in (("audit_logger", "audit_logger.py", "log_file"),
                              ("mapping_recommender", "mapping_recommender.py", "cache_file")):
        src = (_REPO / "b2bcollect_build" / path).read_text(encoding="utf-8")
        _assert(not re.search(rf'{name}: str = "[^"/\\\\]+\.(json|log)"', src),
                f"{label} 的 {name} 默认值不再是相对 CWD 的裸文件名")
        _assert("state_file" in src, f"{label} 的默认落点问 tool_config.state_file（唯一口径）")

    # 2) 行为层面：CWD 切到一个根本写不出文件的地方，落点仍在状态目录里
    elsewhere = Path(tempfile.mkdtemp(prefix="cwd_elsewhere_"))
    old_cwd = os.getcwd()
    try:
        os.chdir(str(elsewhere))
        with _workspace() as ws, _frozen(ws):
            for label, got in (("审计日志", _al._default_log_file()),
                               ("映射历史", _mr._default_cache_file())):
                p = Path(got)
                _assert(p.is_absolute(), f"{label} 的落点是绝对路径（实际={got}）")
                _assert(str(ws["bundle"]) not in str(p),
                        f"{label} 不落在随包数据目录里（打包态那可能是退出即删的 _MEI，"
                        f"或 Mac 的只读转置卷）（实际={got}）")
                _assert(p.parent == tc.state_dir(),
                        f"{label} 与状态目录同源（口径只有一份）（实际={p.parent}）")
            # 真的写一次：状态目录可写就落得下，且读得回来
            _mr.MappingRecommender().update(["公司名称"], {0: "公司名"})
            _assert(Path(_mr._default_cache_file()).is_file(), "映射历史真写得进去")
            _al.AuditLogger().log("export", "u", {"n": 1})
            _assert(Path(_al._default_log_file()).is_file(), "审计日志真写得进去")
    finally:
        os.chdir(old_cwd)

    # 3) 坏缓存绝不能把对话框带崩：这一类形状就是本轮 Mac 崩溃的翻版
    bad_root = Path(tempfile.mkdtemp(prefix="badhist_"))
    for name, payload in (("半截", "{不是 JSON"), ("形状不对", "[1, 2, 3]"),
                          ("条目是字符串", json.dumps({"表头": "字段"})),
                          ("计数不是数字", json.dumps({"表头": [["字段", "abc"]]})),
                          ("空值", json.dumps({"表头": [None, [], ["", 1]]}))):
        f = bad_root / f"{name}.json"
        f.write_text(payload, encoding="utf-8")
        rec = _mr.MappingRecommender(cache_file=str(f))
        _assert_eq(rec.recommend(["表头"]), {}, f"坏缓存「{name}」退化成没有历史，不抛异常")
    _assert_eq(_mr.MappingRecommender(cache_file=str(bad_root / "不存在.json")).history, {},
               "文件不存在时按空历史起步")
    unwritable = bad_root / "blocker"
    unwritable.write_text("x", encoding="utf-8")
    raised = None
    try:
        _mr.MappingRecommender(cache_file=str(unwritable / "nope" / "h.json"))._save_history()
        _al.AuditLogger(log_file=str(unwritable / "nope" / "a.log")).log("a", "u", {})
    except Exception as e:
        raised = e
    _assert(raised is None,
            "落点写不进去时只报一句、不把导入动作带崩"
            + (f"（实际抛了 {type(raised).__name__}: {raised}）" if raised else ""))


def test_relaunch_chain_off_the_bundle():
    print("\n═══ Q. 重启链路：不拿包内目录当 cwd、旧进程真退出才起新实例 ═══")
    import b2bcollect_gui as gui

    # 1) 工作目录：打包态取 exe 同级（磁盘上那个真实文件），绝不取随包数据目录
    with _workspace() as ws, _frozen(ws):
        _assert(ws["exe_dir"].exists(),
                "夹具的 exe 同级目录在位（onedir 的意义就在这：可执行文件是真实文件）")
        workdir = Path(gui._relaunch_workdir())
        _assert(str(ws["bundle"]) not in str(workdir.resolve()),
                f"重启给新实例的 cwd 不是随包数据目录（实际={workdir}）——"
                "子进程占着那个目录，旧实例就删不掉自己的临时目录，"
                "base_library.zip 正是这么报 FileNotFound 的")
        _assert(workdir.is_dir(), f"给出去的 cwd 真存在：{workdir}")

    # 2) 参数写法：重启标记不许被当成自检参数（否则 CI/界面两条路互相劫持）
    for argv in ([gui.RELAUNCH_FLAG, "4242"], [gui.RELAUNCH_FLAG, "4242",
                                              gui.RELAUNCH_NOTE_MOVED]):
        want_doctor, wrong = gui._doctor_request(list(argv))
        _assert(not want_doctor and wrong == [],
                f"{argv} 被判成「不是自检、也没有认不出的写法」（必须走建界面这条路）")
    _assert_eq(gui._relaunch_source_pid([gui.RELAUNCH_FLAG, "4242"]), 4242,
               "取到前任 PID")
    _assert_eq(gui._relaunch_source_pid([gui.RELAUNCH_FLAG, "abc"]), 0,
               "PID 写坏时退回 0（当普通启动，绝不 wait 一个不存在的进程）")
    _assert_eq(gui._relaunch_source_pid([]), 0, "没有这个参数就不是重启拉起的")
    _assert_eq(gui._relaunch_note([gui.RELAUNCH_FLAG, "1", gui.RELAUNCH_NOTE_MOVED]),
               gui.RELAUNCH_NOTE_MOVED, "搬家那一次的重启原因读得出来")
    _assert_eq(gui._relaunch_note([gui.RELAUNCH_FLAG, "1"]), "",
               "权限变更那一次没有原因标记（话术各说各的，不互相冒充）")

    # 3) 三条纪律只能在文本上钉住（跑一次就会真的拉起新进程/真的退出）
    body = _code_only(gui.MainWindow._spawn_relaunch)
    _assert("shell=True" not in body,
            "自动重启不走 shell：命令行里有中文路径、空格、8.3 短名"
            "（实机日志里就是 C:\\Users\\ADMINI~1\\...），过一层 shell 就多一层引号歧义")
    _assert("_relaunch_workdir()" in body, "cwd 走唯一那个函数，不在这里各写一份")
    _assert("RELAUNCH_FLAG" in body and "os.getpid()" in body,
            "把「我是谁」交给新实例去观测（旧写法 timeout /t 2 是猜时长）")
    _assert("_relaunch_note" in body or gui.RELAUNCH_NOTE_MOVED in body,
            "重启原因跟着参数走，新实例才知道该说哪句话")
    _assert("timeout" not in body.lower() and "cmd /c" not in body.lower(),
            "不再用 cmd /c start + timeout 猜时长：真实判据是「前任进程没了」，"
            "由新实例去观测（见 :func:`_wait_for_previous_instance`）")
    cmd_src = _code_only(gui.MainWindow._relaunch_command)
    _assert("return [sys.executable]" in cmd_src,
            "打包态起的是自身可执行文件——onedir 下它是磁盘上的真实文件，"
            "不再指向退出即删的临时解包目录")
    _assert(0 <= cmd_src.index("frozen") < cmd_src.index("return [sys.executable]"),
            "frozen 分支先返回，源码分支才用 __file__（两种形态各有各的启动路径）")


def test_channel_failure_kind():
    print("\n═══ R. 一路通道失败的种类：成因由文件的主人回答 ═══")
    import site_adapters as _sa

    with _patch(_sa, "_LIST_CHANNEL_LOADED", {"riskbird": True, "aiqicha": False}), \
         _patch(_sa, "ADAPTER_ERRORS",
                {"riskbird": "FileNotFoundError: [Errno 30] Read-only file system",
                 "aiqicha": "ModuleNotFoundError: No module named 'aiqicha_crawler_v2'"}):
        _assert_eq(_sa.channel_failure_kind("riskbird"), "broken",
                   "模块已经加载成功却在构造期抛 FileNotFoundError：文件明明在包里，"
                   "说成「缺文件」就是把只读卷的环境问题报成发包缺陷")
        _assert_eq(_sa.channel_failure_kind("aiqicha"), "missing",
                   "模块压根没加载上、报的又是找不到模块：这是发包缺文件，两条证据同向")
        _assert_eq(_sa.channel_failure_kind("qzd"), "", "没失败就不下结论")
    with _patch(_sa, "_LIST_CHANNEL_LOADED", {"aiqicha": False}), \
         _patch(_sa, "ADAPTER_ERRORS", {"aiqicha": "FileNotFoundError: [Errno 2] "
                                                   "No such file or directory"}):
        _assert_eq(_sa.channel_failure_kind("aiqicha"), "missing",
                   "同一句 FileNotFoundError，模块没加载上时它就是「包里有依赖但那个文件"
                   "没进包」——判据必须看加载状态，只读异常文本会把两类故障说成同一句话")
    with _patch(_sa, "_LIST_CHANNEL_LOADED", {"aiqicha": False}), \
         _patch(_sa, "ADAPTER_ERRORS", {"aiqicha": "ImportError: No module named 'cv2'"}):
        _assert_eq(_sa.channel_failure_kind("aiqicha"), "missing",
                   "缺依赖也是「东西没进包」这一类（发包方要查的是清单，不是用户权限）")
    with _patch(_sa, "_LIST_CHANNEL_LOADED", {"aiqicha": False}), \
         _patch(_sa, "ADAPTER_ERRORS", {"aiqicha": "OSError: [Errno 13] Permission denied"}):
        _assert_eq(_sa.channel_failure_kind("aiqicha"), "broken",
                   "非 import 期的错误不算缺文件（本机加载不出）")
    import b2bcollect_gui as gui
    with _patch(_sa, "_LIST_CHANNEL_LOADED", {"riskbird": True, "aiqicha": True}), \
         _patch(_sa, "ADAPTER_ERRORS", {"riskbird": "FileNotFoundError: x",
                                        "aiqicha": "FileNotFoundError: y"}):
        _assert_eq(gui.MainWindow._lost_channels_kinds(("riskbird", "aiqicha")), {"broken"},
                   "两路同一成因只得到一个种类（界面据此只说一句结论）")
        _assert("磁盘只读" in gui.MainWindow._lost_channels_reason(("riskbird", "aiqicha")),
                "全是 broken 时把「只读/不可写」这一类摆在话术里，不让人去查网络")
    with _patch(_sa, "_LIST_CHANNEL_LOADED", {"riskbird": False, "aiqicha": True}), \
         _patch(_sa, "ADAPTER_ERRORS", {"riskbird": "ModuleNotFoundError: x",
                                        "aiqicha": "AttributeError: y"}):
        _assert("缺了、或本机加载不出" in gui.MainWindow._lost_channels_reason(
            ("riskbird", "aiqicha")),
            "两种成因并存时退回覆盖得下的那句，不替某一路下结论")
    # 判据本体出问题也不能把界面带崩
    with _patch(_sa, "channel_failure_kind", lambda s: (_ for _ in ()).throw(RuntimeError("x"))):
        _assert_eq(gui.MainWindow._lost_channels_kinds(("riskbird",)), {"unknown"},
                   "判据自身抛异常时退化成 unknown，界面照常起得来")


def test_app_translocation_relocation():
    print("\n═══ S. Mac 只读副本：识别得出，也能一次点完搬家 ═══")
    import plistlib

    root = Path(tempfile.mkdtemp(prefix="relocate_"))
    home = root / "home"
    apps = home / "Applications"
    apps.mkdir(parents=True)

    def mkapp(p, bid="com.b2bcollect.tool", ver="1.0"):
        (p / "Contents" / "MacOS").mkdir(parents=True, exist_ok=True)
        (p / "Contents" / "Info.plist").write_bytes(plistlib.dumps(
            {"CFBundleIdentifier": bid, "CFBundleShortVersionString": ver,
             "CFBundleExecutable": "B2bCollectTool"}))
        (p / "Contents" / "MacOS" / "B2bCollectTool").write_text("MZ", encoding="utf-8")
        return p

    running = mkapp(root / "AppTranslocation" / "UUID-XYZ" / "B2bCollectTool.app")
    source = mkapp(home / "Downloads" / "B2bCollectTool.app")
    K = dict(app=running, home=home, apps_dir=apps, platform="darwin")

    plan = tc.app_migration_plan(**K)
    _assert_eq(plan["status"], "ready", "只读副本 + 下载目录里有同一份 = 可以由工具代做")
    _assert_eq(plan["source"], source, "源就是实测到的那一份，不靠猜文件名")
    _assert_eq(Path(str(plan["dest_exe"])), apps / "B2bCollectTool.app" / "Contents" /
               "MacOS" / "B2bCollectTool", "新位置的可执行路径由 Info.plist 算出")

    # 四种「不许动手」的形态各有一条独立判据（每一种都是能损坏用户数据的方向）
    stash = [0]

    def _clear_dest():
        """把「应用程序」里那份挪走：每颗反例都从目标位置是空的起步。"""
        d = apps / "B2bCollectTool.app"
        if d.exists():
            stash[0] += 1
            d.rename(root / f"stash{stash[0]}.app")

    mkapp(apps / "B2bCollectTool.app", bid="com.someone.else")
    _assert_eq(tc.app_migration_plan(**K)["status"], "name_taken",
               "目标位置有同名但不同产品的包时绝不动手（同名不是证据）")
    _clear_dest()
    mkapp(apps / "B2bCollectTool.app", bid="com.b2bcollect.tool", ver="9.9")
    _assert_eq(tc.app_migration_plan(**K)["status"], "name_taken",
               "目标位置已有同产品但版本不同：不覆盖、不删除，交回给用户决定")
    _clear_dest()
    running_no_id = mkapp(root / "AppTranslocation" / "UUID-2" / "B2bCollectTool.app")
    (running_no_id / "Contents" / "Info.plist").write_bytes(plistlib.dumps(
        {"CFBundleExecutable": "B2bCollectTool"}))
    got = tc.app_migration_plan(app=running_no_id, home=home, apps_dir=apps,
                                platform="darwin")
    _assert_eq(got["status"], "unknown_identity", "读不到包标识就承认无法证明，不猜")

    mkapp(apps / "B2bCollectTool.app", bid="com.b2bcollect.tool")   # 同一份已在应用程序里
    _assert_eq(tc.app_migration_plan(**K)["status"], "already_installed",
               "已经装好了就说「从那里打开」，不再给一个会失败的搬家按钮")
    _clear_dest()
    source.rename(root / "moved-away.app")
    _assert_eq(tc.app_migration_plan(**K)["status"], "no_source",
               "在有限的几个用户目录里没找到同一份时实话实说（绝不做全盘搜索）")
    root_app = mkapp(home / "B2bCollectTool.app", bid="com.b2bcollect.tool")
    _assert_eq(tc.app_migration_plan(**K)["status"], "ready", "家目录根上也算候选（深度一层）")
    mkapp(home / "Desktop" / "B2bCollectTool.app", bid="com.b2bcollect.tool")
    got = tc.app_migration_plan(**K)
    _assert_eq(got["status"], "ambiguous", "多份副本时不替用户猜搬哪一个")
    _assert(got["detail"].count("B2bCollectTool.app") >= 2,
            f"两份都逐字列在细节里，用户才知道要去处理谁（实际={got['detail']}）")
    root_app.rename(home / "Desktop" / "extra.app")      # 只剩桌面那一份
    _assert_eq(tc.app_migration_plan(**K)["status"], "ready", "去掉一份就不再含糊")
    _assert_eq(tc.app_migration_plan(**{**K, "platform": "win32"})["status"], "not_mac",
               "非 macOS 直接判「没这回事」（搬家这件事是 Mac 专属）")
    _assert_eq(tc.app_migration_plan(app=home / "Desktop" / "B2bCollectTool.app", home=home,
                                     apps_dir=apps, platform="darwin")["status"],
               "not_translocated", "不是从只读副本启动的，就不给搬家入口")

    # 执行层：同卷改名真能搬成；源没了、目标已存在都不许判成功
    got = tc.app_migration_plan(app=running, home=home, apps_dir=apps, platform="darwin")
    src_now, dest_now = got["source"], got["dest"]
    ok, why = tc.move_app_bundle(src_now, dest_now)
    _assert(ok and Path(str(got["dest_exe"])).is_file() and not src_now.exists(),
            "同卷改名真搬得动，且源目录不残留半个副本"
            + ("" if ok else f"（失败原因：{why}）"))
    again = tc.move_app_bundle(src_now, dest_now)
    _assert(not again[0], "源已经不在了再搬一次必须判失败，绝不能把「不存在」当成功")
    other = mkapp(home / "Desktop" / "second.app")
    ok2, why2 = tc.move_app_bundle(other, dest_now)
    _assert(not ok2 and "不覆盖" in why2, f"目标已存在时拒绝动手并说清原因：{why2}")
    _assert(other.is_dir(), "拒绝的时候源目录一定还在（不许留下半个搬家现场）")

    # 界面侧：链接永远给，但话术跟着判据变；判据自身出问题也不能带崩界面
    import b2bcollect_gui as gui
    with _patch(tc, "app_migration_plan", lambda: {"status": "ready", "dest": apps}):
        _assert("帮我移进" in gui.MainWindow._app_relocate_link(),
                "能代做时链接就承诺代做，不写「请你自己拖」")
    with _patch(tc, "app_migration_plan", lambda: {"status": "no_source"}):
        _assert("在访达里打开" in gui.MainWindow._app_relocate_link(),
                "做不了时链接只承诺指路，不假装一键搞定")
    with _patch(tc, "app_migration_plan",
                lambda: (_ for _ in ()).throw(RuntimeError("boom"))):
        _assert("在访达里打开" in gui.MainWindow._app_relocate_link(),
                "判据抛异常时退回最保守的那句（一条提醒绝不能把界面带崩）")

    class _Fake:
        def __init__(self):
            self.called = []

        def _acl_prompt_restart(self):
            self.called.append("restart")

        def _offer_app_relocation(self):
            self.called.append("relocate")

    f = _Fake()
    gui.MainWindow._on_banner_link(f, "#app-relocate")
    gui.MainWindow._on_banner_link(f, "#acl-restart")
    gui.MainWindow._on_banner_link(f, "#什么都不知道")
    _assert_eq(f.called, ["relocate", "restart"],
               "两个动作各自分派、未知动作静默忽略（横幅加新链接不该炸界面）")
    # reveal_in_finder 只在 macOS 上真的去开访达：其它平台必须原样返回 False，
    # 离线机器上谁都不该因为一条提醒拉起一个外部进程（判据先于动作，不靠 except 兜）
    old_platform = sys.platform
    try:
        sys.platform = "win32"
        _assert_eq(tc.reveal_in_finder([apps]), False,
                   "非 macOS 不碰 /usr/bin/open")
    finally:
        sys.platform = old_platform
    _assert("shell=True" not in inspect.getsource(tc.reveal_in_finder)
            and "/usr/bin/open" in inspect.getsource(tc.reveal_in_finder),
            "开访达走绝对路径 + 参数列表，不过 shell（中文路径与引号歧义不值得再冒一次险）")
    _assert("app_relocate_link" in inspect.getsource(gui.MainWindow._warn_read_only_runtime),
            "播报只读副本的那一屏同时给出出口（只提示不给办法等于让人烦躁）")


def test_path_discipline_gate():
    print("\n═══ T. 落点纪律门禁：发包前就得认出「数据写进应用包里」 ═══")
    # 为什么这条门禁值得单独一类退出码：本轮 Mac 崩的那句 Errno 30 只是同一条纪律最
    # 响亮的下场，另外三种写法（整包可写时把数据塞进 .app、Windows 写进退出即删的
    # _MEI、界面报「已保存」而文件下次开机不见了）在界面上**一点痕迹都没有**。凭
    # 证链与网络全绿的产物照样能带病发货，所以它必须让 --acl-doctor 的退出码也变红。
    root = Path(tempfile.mkdtemp(prefix="pathgate_"))
    app = root / "B2bCollectTool.app"
    macos = app / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    (macos / "B2bCollectTool").write_text("MZ", encoding="utf-8")
    try:
        with _sys(platform="darwin", frozen=True,
                  executable=str(macos / "B2bCollectTool")):

            code0, lines0 = ag._path_discipline(frozen=True)
            _assert_eq(code0, ag.DOCTOR_EXIT_OK, "候选与实际落点都在包外时判绿（不误伤好包）")
            _assert(any("落点纪律" in l for l in lines0),
                    f"绿也要在报告里留一行，否则下次没人记得这道门在不在（实际={lines0}）")

            # 旧写法「exe 同级 = Contents/MacOS 也进候选链」：判据必须认出来并点名。
            # 这里**只**改候选链、不改 inside_app_bundle：后者既是排除规则又是这道门禁的
            # 判据，整体打桩会把两边一起打掉（自废武功式的假绿），那正是 7a0 立条要避免的。
            with _patch(tc, "state_dir_candidates", lambda subdir="": [macos / tc.STATE_DIR_NAME]):
                code1, lines1 = ag._path_discipline(frozen=True)
            _assert_eq(code1, ag.DOCTOR_EXIT_PATH, "候选链里出现包内那一级时直接判红（不许发货）")
            _assert(any(str(macos) in l or str(macos.resolve()) in l for l in lines1),
                    f"红的那一段必须点名是哪一处（实际={lines1}）")
            _assert(any("test_data_dir_offline" in l for l in lines1),
                    "报告要给下一步该做什么（离线判据在哪、按形态排除是什么意思）"
                    f"（实际={lines1}）")

            # 包内判定必须对同一个目录的两种拼写都成立：macOS 上 /var 是 /private/var 的
            # 软链，而 App Translocation 就发生在 /private/var/folders 这一棵上。只认其中
            # 一种拼写（一边 resolve 一边不 resolve），这条纪律恰好会在最需要它的那台机器
            # 上把包内判成包外——本轮给自检加这道门时就是这样实测出来的。
            _assert(tc.inside_app_bundle(macos / tc.STATE_DIR_NAME)
                    and tc.inside_app_bundle(macos.resolve() / tc.STATE_DIR_NAME),
                    "包内判定对软链两种拼写都成立（比较前统一 resolve）")
            _assert(not tc.inside_app_bundle(app.parent / tc.STATE_DIR_NAME),
                    "包旁那个目录不算包内（否则包旁这一级会被自己排除掉，退路全断）")

            # 只看候选链不够：真正选中的落点在包内同样要红（探针运气挑出包内路径那种形态）
            with _patch(tc, "state_dir", lambda subdir="": macos / tc.STATE_DIR_NAME):
                code2, lines2 = ag._path_discipline(frozen=True)
            _assert_eq(code2, ag.DOCTOR_EXIT_PATH, "实际落点跑进包里同样判红（不靠「候选看着干净」）")

            # 源码态不判红：开发机的数据本来就该待在仓库目录里（与 N 节通道口径同一纪律）
            with _patch(tc, "state_dir_candidates",
                        lambda subdir="": [macos / tc.STATE_DIR_NAME]):
                code3, _ = ag._path_discipline(frozen=False)
            _assert_eq(code3, ag.DOCTOR_EXIT_OK, "源码态只报现状不判红（别把开发机拖成红灯）")

            # 新类别的三件配套事：有码、进了全表、帮助里念得出名字
            _assert(ag.DOCTOR_EXIT_PATH not in {v[0] for v in ag._DOCTOR_KINDS.values()},
                    "落点违规不许混进「服务故障分类表」（那三张表说的是服务为什么连不上）")
            _assert(ag.DOCTOR_EXIT_PATH in {c for c, _ in ag._DOCTOR_EXIT_TABLE},
                    "退出码进了常量却没进全表 = --doctor 帮助里它没名字（上一轮就吃过这亏）")
            _assert("9" in ag.DOCTOR_EXIT_HELP and "落点违规" in ag.DOCTOR_EXIT_HELP,
                    f"帮助文本要数得出这一类（实际={ag.DOCTOR_EXIT_HELP}）")
            src = _code_only(ag.doctor_lines)
            _assert("_path_discipline" in src and "path_code" in src,
                    "自检必须真的调用这道门禁并把它的码并进结论（只写函数不调用＝门开着）")
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ── U. 窗口冒烟门禁 ────────────────────────────────────────────────────────
# 这一条管的是「CI 全绿、用户双击却没界面」那类盲区：--acl-doctor 验凭证、验通道、
# 验落点，唯独不碰 Qt，而本轮两份实机故障恰好都落在 Qt 那一段（Mac 崩在建名单通道，
# Windows 崩在建窗口之前的 import）。所以判据分三层，缺一层就有一个方向被放过：
#   界面侧：开关认得出、分派早于任何副作用、不与自检同族；
#   行为侧：正例真把窗口建出来退 0，反例（只读卷上的 OSError）退非零并留下崩溃日志；
#   入口侧：三处打包入口都跑了它，Mac 那两处还跑在**只读副本**上，且仿真自己也被验活。
SMOKE_STEP_NAME = "Window smoke gate"
SMOKE_MARK = "--ui-smoke"


def _smoke_region(text: str, is_workflow: bool) -> str:
    """三处入口里「窗口冒烟」那一段**会被执行**的行。

    workflow 按 step 切（注释整体剔掉，_iter_steps 已经做了），本机脚本没有 step
    概念，按整篇剔注释。两处都只留可执行行：这些入口的注释里正写着「--acl-doctor
    不建窗口」这种话，拿原文判就是「解释了盲区的行自己被当成盲区」。
    """
    if not is_workflow:
        return "\n".join(ln for ln in text.splitlines()
                         if not ln.strip().startswith("#"))
    for name, body in _iter_steps(text):
        if SMOKE_STEP_NAME in name:
            return body
    return ""


def _smoke_problems(region: str, readonly_expected: bool) -> list:
    """一段冒烟脚本够不够格当发包门禁。返回问题清单，空 = 合格。"""
    out = []
    if SMOKE_MARK not in region:
        out.append(f"没把 {SMOKE_MARK} 传进产物（跑 --acl-doctor 不算：它刻意不建窗口）")
    elif not any(k in region for k in ("ExitCode", "rc=$?", "RC=$?")):
        out.append("冒烟结果没进退出码（只看它跑没跑，红了也不知道为什么红）")
    if "*.log" not in region:
        out.append("没查「临时用户目录里有没有崩溃日志」：异常被吞掉、界面勉强开出来"
                   "那种半坏状态，退出码 0 是绿的")
    if readonly_expected:
        if "chmod -R a-w" not in region:
            out.append("没把副本改成只读：这一步验的就不是 Gatekeeper 转置那个现场")
        if "touch" not in region:
            out.append("没验「只读真的生效了」：chmod 失灵时这一步绿得毫无意义")
        if "wc -l" not in region:
            out.append("没比对副本前后的文件数：写进包内在没转置的机器上不报错，只会在"
                       "升级/卸载时把用户数据一起带走")
    return out


# ── V. 运行形态开关的存取纪律 ──────────────────────────────────────────────
# 本轮 3 条红（663 绿里有 3 条是隔了一整节才红的）根因就在这里：T 节把
# ``old = (sys.platform, sys.executable, getattr(sys, "frozen", None))`` 存成位置元组，
# 还原却按 ``sys.executable = old[2]`` / ``sys.frozen = old[1]`` 写——顺序错开一位。
# 当场不红，红的是下一节（U 节在进程内真建窗口，撞上 Path(None)），报的还是
# 「NoneType」这种和产品毫不相干的栈。这类缺陷最坏的地方不是红，是**红的位置离真因
# 隔了一整节**：排查的人会先去查产品代码。所以除了本套自己的 ``_sys``/``_run``，
# 这条写法要在全部离线套件上被机械地拦住。
_SWITCH_INDEX_RESTORE = re.compile(
    r"sys\.(?:frozen|_MEIPASS|executable|platform)\s*=\s*[A-Za-z_]\w*\[\d\]")
_SWITCH_TUPLE_SAVE = re.compile(
    r"(?:^|[\s(])(?:old|saved|before|_old|_saved)\s*=\s*\(\s*sys\.(?:frozen|_MEIPASS|executable|platform)\b")


_STR_TOKENS = {tokenize.COMMENT, tokenize.STRING}
for _name in ("FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END"):   # Python 3.12+ 拆开 f-string
    _tok = getattr(tokenize, _name, None)
    if _tok is not None:
        _STR_TOKENS.add(_tok)


def _switch_code_text(text: str):
    """把注释与字符串**就地**掩成空格，返回只剩代码骨架的文本；行号与列位一字不移。

    为什么是掩字符而不是跳过整行：本节自己的反例样本就写在代码行上
    （``_bad_restore = "…sys.frozen = old[1]…"``），跳过整行会把同一行上真会执行的代码
    一起放过；而 docstring/注释里那两句是**解释**这种写法为什么不行，不是要拦的行为。
    一条误报的代价比漏报还大：下一来的人会为了消红去改判据。
    读不懂（语法错、编码怪异）返回 None，调用方必须判红——「读不懂」不等于「没问题」。
    """
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except Exception:
        return None
    lines = text.splitlines(keepends=True)
    for tok in tokens:
        if tok.type not in _STR_TOKENS:
            continue
        sl, sc, el, ec = tok.start[0], tok.start[1], tok.end[0], tok.end[1]
        for i in range(sl, el + 1):
            ln = lines[i - 1]
            body = ln.rstrip("\r\n")
            a = max(0, min(sc if i == sl else 0, len(body)))
            b = max(a, min(ec if i == el else len(body), len(body)))
            lines[i - 1] = body[:a] + " " * (b - a) + ln[len(body):]
    return "".join(lines)


def _switch_discipline_hits(text: str, where: str = "<文本>"):
    """在这份源码上跑一次纪律，返回「位置:行号: 原文」清单；None 表示这份文本读不懂。"""
    skeleton = _switch_code_text(text)
    if skeleton is None:
        return None
    src = text.splitlines()
    out = []
    for lineno, line in enumerate(skeleton.splitlines(), 1):
        if _SWITCH_INDEX_RESTORE.search(line) or _SWITCH_TUPLE_SAVE.search(line):
            out.append(f"{where}:{lineno}: {src[lineno - 1].strip()[:80]}")
    return out


def _switch_scan_files():
    """纪律扫描范围：三个交付目录的 test_*.py + 交付链上全部 debug/*.py。"""
    g = _manifest_gate()
    dirs = [d for d in g.SHARED_SOURCE_DIRS if (_REPO / d).is_dir()]
    _assert_eq(len(dirs), len(g.SHARED_SOURCE_DIRS),
               f"交付目录都在位，范围没缩水（登记 {list(g.SHARED_SOURCE_DIRS)}，实到 {dirs}）")
    return sorted({p for d in dirs for p in (_REPO / d).glob("test_*.py")} |
                  set((_REPO / "debug").glob("*.py"))), dirs


def _switch_scan_hits(files):
    """对一批文件跑纪律，返回违规清单；读不出来/读不懂的文件也算违规（不是跳过）。"""
    hits = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            hits.append(f"{f.relative_to(_REPO)}: 读不出来（{exc}）")
            continue
        got = _switch_discipline_hits(text, str(f.relative_to(_REPO)))
        if got is None:
            hits.append(f"{f.relative_to(_REPO)}: 语法读不懂，这条判据在它上面没跑")
            continue
        hits.extend(got)
    return hits


def test_switch_discipline_gate():
    print("\n═══ V. 开关存取纪律：不许用位置元组存运行形态开关（还原错一位没人看得出来）═══")
    # 扫描范围按「三个交付目录 + 交付链脚本」来，不写 `*/test_*.py`：仓库里躺着本地备份
    # 目录（b2bcollect_build_backup_20261106 那种），它们既不进包也不参与交付，把里面
    # 特意保留的旧写法算成产品缺陷，这条判据就永久红——而永久红的判据等于没有判据。
    # 目录清单取自构建前硬门，两边共用一张表（各抄一份迟早会漂）。
    files, dirs = _switch_scan_files()
    _assert(len(files) >= 40, f"扫描范围不是空的（实扫 {len(files)} 个文件，少于 40 就是通配写错了）")
    _assert(__file__ in [str(f) for f in files], "本套自己也在扫描范围内（判据不许只量别人）")
    _assert(not any("backup" in p.as_posix() for p in files),
            "本地备份目录不在范围内（不进包的东西不该决定交付判据）")
    _assert_eq(_switch_scan_hits(files), [], "全部离线套件里没有人用位置元组存/还运行形态开关"
                       "（改回按名字存取：with _sys(...) / with _switches(...)）")
    # 反向对照：把本轮真出过事的那两句喂给判据，必须各抓一次（抓不住就是空转）
    _bad_restore = "        sys.frozen = old[1]\n"
    _bad_save = "    old = (sys.platform, sys.executable, getattr(sys, 'frozen', None))\n"
    _assert(_SWITCH_INDEX_RESTORE.search(_bad_restore), "反例①：按位置还原必须被抓住")
    _assert(_SWITCH_TUPLE_SAVE.search(_bad_save), "反例②：位置元组取值必须被抓住")
    _sample = ("# 注释里写 old = (sys.platform, sys.executable)\n"
               "def f():\n"
               '    """文档里写 old = (sys.platform, sys.executable) 这种位置元组\n'
               "    是本轮翻车的那句，这里只是在解释它为什么不行\"\"\"\n"
               "    sys.frozen = old[1]\n")
    # 掩码版判据的三条要害：行号不漂、同一行上的真代码一个字符不少、字符串里的反例不算
    _sample = _sample + '    print("上面那行才是真违规，这一句里的 sys.frozen = old[1] 只是话")'
    _masked = _switch_code_text(_sample)
    _assert(_masked is not None, "反例③起点：样例读得懂（读不懂时后面几条全是空跑）")
    _assert_eq(len(_masked.splitlines()), len(_sample.splitlines()),
               "反例③：掩码不合并行也不拆行（行号一漂，报出来的位置就不是人能跳过去的位置）")
    _assert_eq(_masked.splitlines()[0], " " * len(_sample.splitlines()[0]),
               "注释整行掩成等长空格：判据抓的是代码骨架，报出来的位置却还是原文的位置")
    _assert(not _masked.splitlines()[2].strip() and not _masked.splitlines()[3].strip(),
            "跨两行的 docstring 也掩干净，那两句示例说明都不算违规")
    _assert_eq(_switch_discipline_hits(_sample, "样例"), ["样例:5: sys.frozen = old[1]"],
               f"反例④：真违规那行照抓，同一份文本里注释/docstring/字符串上的同一句不抓"
               f"（实际={_switch_discipline_hits(_sample, '样例')}）")
    _assert("print(" in _masked.splitlines()[5] and "old[1]" not in _masked.splitlines()[5],
            "字符串只掩字面量本身，同一行上的真代码一个字符都没少（跳过整行的老写法做不到）")
    _fs = '    print(f"翻车那句是 sys.frozen = old[1]，值是 {old[1]}")'
    _assert_eq(_switch_discipline_hits(_fs, "f串"), [],
               "反例⑤：f-string 里的反例文字不算代码（3.12+ 把它拆成 FSTRING_* token，一样要掩）")
    _assert_eq(_switch_discipline_hits(_bad_restore, "坏"), ["坏:1: sys.frozen = old[1]"],
               "同样一份判据放在可执行行上照样抓得住（掩码不是把判据调松）")
    _assert_eq(_switch_discipline_hits(Path(__file__).read_text(encoding="utf-8"), "本套"), [],
               "本套不把自己这一节的反例样本判成违规（上一版「跳过整行」就是这么红的）")
    # 反向对照⑥：上面几条测的是「判据认不认得这种写法」，这一条测的是「真放进交付目录的
    # 文件会不会被扫到」。少了它，通配写错、目录名漂掉、helper 换成读常量表这些事故都会
    # 表现成「全绿」——而这条判据当初就是为了拦那个让 U 节隔节翻车的 T 节写法。
    probe = _REPO / "b2bcollect_build" / "zz_discipline_probe.py"
    _assert(not probe.exists(), "反例⑥起点干净（探针文件不在位，上次没留残留）")
    probe.write_text(os.linesep.join(("def g():", "    old = (sys.platform, sys.frozen)",
                                      "    sys.frozen = old[1]")), encoding="utf-8")
    try:
        got6 = _switch_scan_hits([probe])
        _assert_eq([h.split(":")[1] for h in got6], ["2", "3"],
                   f"反例⑥：交付目录里新放一个位置元组的文件，扫描当场报出存与还两行"
                   f"（实际={got6}）")
    finally:
        probe.unlink(missing_ok=True)
    _assert(not any(p.name == probe.name for p in (_REPO / "b2bcollect_build").glob("*.py")),
            "探针删掉后磁盘上立刻没有它（留一个「故意违规」的文件在交付目录里，下一次扫描就永久红）")
    _good = ("    with _sys(platform='win32', frozen=True):\n"
             "        sys.executable = str(exe)\n")
    _assert_eq(_switch_discipline_hits(_good, "正例"), [],
               "正例：就地拨、由 with 还——不误伤（否则这条纪律第一天就把所有套件拖红）")


def test_window_smoke_gate():
    print("\n═══ U. 窗口冒烟门禁：产物自己把界面建出来，Mac 跑在只读副本上 ═══")
    import b2bcollect_gui as _gui

    # 1) 界面侧：写法、分派位置、与自检那条边界
    _assert_eq(_gui.UI_SMOKE_FLAG, SMOKE_MARK, "冒烟开关的写法（CI 抄的是同一个字符串）")
    _assert_eq(_gui._doctor_request([SMOKE_MARK]), (False, []),
               f"{SMOKE_MARK} 既不被当成自检开关，也不进「像自检却认不出」那份清单"
               "（判错的方向很坏：CI 传冒烟参数却退出 3，红的是门禁不是包）")
    msrc = _code_only(_gui.main)
    _assert("UI_SMOKE_FLAG in sys.argv" in msrc and "_ui_smoke_entry()" in msrc,
            "main() 里真的分派到冒烟入口（只有函数没有分派＝门开着没人走）")
    _assert(0 < msrc.index("_acl_doctor_entry()") < msrc.index("_ui_smoke_entry()")
            < msrc.index("previous_pid"),
            "分派排在自检之后、重启交接与建界面之前——早于任何 Qt/Chrome 副作用")
    ssrc = _code_only(_gui._ui_smoke_entry)
    for need in ("MainWindow()", ".show()", "prefetch_authoritative", "install(app)",
                 "_fatal_log(", "QTimer"):
        _assert(need in ssrc, f"冒烟入口保留了 {need}（少一样，验的就不是用户那一次双击）")
    _assert(_gui.UI_SMOKE_EXIT_FAILED != _gui.UI_SMOKE_EXIT_TIMEOUT
            and _gui.UI_SMOKE_DEADLINE_MS > 0,
            "「没建出来」与「建到一半卡住」是两种成因，退出码必须分开；看门狗必须有时长")

    # 2) 行为侧：正例退 0、反例退非零并留下崩溃日志。崩溃日志的落点按 Path.home() 算，
    #    所以整段把家目录指进临时目录——顺手也保证门禁不往真实桌面写东西。运行形态开关
    #    也由这一段自己接管（_source_form）：建窗口这件事在两种形态下走的落点不同，
    #    不压回源码态的话，测出来的可能是上一节留下的那副假打包态。
    root = Path(tempfile.mkdtemp(prefix="uicheck_"))
    old_home, old_prefetch = Path.home, _gui._access_guard.prefetch_authoritative
    import site_adapters as _sa
    import runtime_paths as _rp       # 落点口径本体：它有进程内择路缓存，切环境必须清
    old_build = _sa.build_list_channels
    seen = []
    saved_env = {k: os.environ.get(k) for k in
                 ("B2B_STATE_DIR", _rp.DATA_DIR_ENV_VAR)}
    try:
        Path.home = lambda *a, **k: root
        _gui._access_guard.prefetch_authoritative = lambda *a, **k: seen.append("prefetch")
        # 状态目录与采集数据目录整体指进临时目录：建一次界面会读写 seen_store 那几份
        # 文件，不隔离的话这道门禁每跑一次就动一次开发机的真实数据——而「门禁改了用户的
        # 数据」会让下一次回归的结果不再由夹具决定（与 _workspace 隔离本地状态同理）。
        os.environ["B2B_STATE_DIR"] = str(root / "state")
        os.environ[_rp.DATA_DIR_ENV_VAR] = str(root / "data")
        _rp.reset_cache()
        with _source_form():
            _assert(not tc.is_frozen(), "行为侧跑在源码态（开关由这一段自己拨，不靠邻居收回）")
            rc = _gui._ui_smoke_entry()
            _assert_eq(rc, _gui.UI_SMOKE_EXIT_OK, f"正例：窗口建得出来就退 0（实际={rc}）")
            _assert_eq(seen, ["prefetch"], "冒烟走的是与真实启动同一条路径（含权威身份判定）")
            _assert(not list(root.rglob("*.log")),
                    f"正例不该留下崩溃日志（实际={list(root.rglob('*.log'))}）")

            def _boom(*a, **k):
                raise OSError(30, "Read-only file system")

            _sa.build_list_channels = _boom           # Mac 那份日志的第一个真实形态
            rc2 = _gui._ui_smoke_entry()
            _assert_eq(rc2, _gui.UI_SMOKE_EXIT_FAILED,
                       f"建名单通道撞上只读卷时必须退非零（实际={rc2}）")
            logs = sorted(root.rglob("*.log"))
            _assert(logs, "失败要留下一份崩溃日志：Finder 双击没有终端，日志是唯一取证入口")
            if logs:
                txt = logs[0].read_text(encoding="utf-8")
                _assert("Read-only file system" in txt and "运行形态" in txt,
                        "日志里既要有那句栈，也要有「打包态/转置」这段运行形态抬头")
                _assert(_gui.FATAL_LOG_NAME in str(logs[0]),
                        f"日志名与界面话术、说明书用的是同一个常量（{_gui.FATAL_LOG_NAME}）")
    finally:
        Path.home = old_home
        _gui._access_guard.prefetch_authoritative = old_prefetch
        _sa.build_list_channels = old_build
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _rp.reset_cache()                             # 缓存里还指着临时目录就得清掉
        shutil.rmtree(root, ignore_errors=True)

    # 3) 入口侧：三处打包入口都把这道门跑起来，Mac 那两处跑在只读副本上
    wf = _REPO / ".github" / "workflows"
    win = (wf / "build-b2bcollect.yml").read_text(encoding="utf-8")
    mac = (wf / "build-b2bcollect-mac.yml").read_text(encoding="utf-8")
    local_sh = (_REPO / "debug" / "build_b2bcollect_mac_local.sh").read_text(encoding="utf-8")
    entries = (("Windows workflow", _smoke_region(win, True), False),
               ("Mac workflow", _smoke_region(mac, True), True),
               ("本机 Mac 打包脚本", _smoke_region(local_sh, False), True))
    for label, region, ro in entries:
        _assert(bool(region), f"{label} 找得出冒烟那一段（找不到=这一步压根不存在）")
        _assert_eq(_smoke_problems(region, ro), [], f"{label} 的冒烟门禁判据齐全")
        _assert("--acl-doctor" in (win if label.startswith("Windows") else
                                   (mac if label.startswith("Mac") else local_sh)),
                f"{label} 里自检门禁仍在（冒烟补的是 Qt 那段盲区，不替凭证与落点让位）")
    # 反向对照：三种「顺手改回去」的形态必须各自点名，否则判据只是摆设
    n = _smoke_region(mac, True).replace(SMOKE_MARK, "--acl-doctor")
    _assert(any("没把 --ui-smoke 传进产物" in p for p in _smoke_problems(n, True)),
            "把冒烟退回成「再跑一遍自检」必须判红——那正是本轮的盲区本身")
    n = _smoke_region(mac, True).replace("chmod -R a-w", "echo ok")
    _assert(any("只读" in p for p in _smoke_problems(n, True)),
            "只读副本被摘掉必须判红（Mac 那两份崩溃日志全靠这个形态才复现得出来）")
    n = _smoke_region(mac, True).replace(' -name "*.log"', '-name "*.txt"')
    _assert(any("崩溃日志" in p for p in _smoke_problems(n, True)),
            "不查崩溃日志必须判红（异常被吞掉、界面勉强开出来那种半坏看不出来）")
    gone = "\n".join(ln for ln in mac.splitlines() if SMOKE_STEP_NAME not in ln)
    _assert(not _smoke_region(gone, True),
            "整步删掉时切出来的是空段（判据靠 step 名字定位，改名也要能被看见）")


def _switches_state() -> dict:
    """运行形态开关的当前值（None = 这一项没拨过）。"""
    return {k: getattr(sys, k, None) for k in _SYS_SNAPSHOT}


def _run(section):
    """跑一节，跑完立刻核对运行形态开关：谁拨走没收回就点谁的名。

    为什么不等到最后统一核对：本轮 T 节还原开关时按位置元组赋错了值（executable 变成
    None、frozen 变成一个真值字符串），红的是**隔了一整节**的 U 节，报的还是「NoneType」
    这种和产品毫不相干的栈——排查的人会先去查产品代码。按节点名把这件事的成本从「翻一
    整套用例」压回「看那一个函数」。最后一道总量核对仍然保留在 main() 末尾：它管的是
    「每节都恢复到了上一节留下的错值」这种逐节看不出、但结果与开跑前不一致的形态。
    """
    before = _switches_state()
    section()
    after = _switches_state()
    diff = [f"sys.{k}: {before[k]!r} → {after[k]!r}"
            for k in before if before[k] != after[k]]
    _assert_eq(diff, [], f"{section.__name__} 跑完后的运行形态开关要和进来时一致"
                         "（拨走不收回＝下一节测的是邻居的环境）")


SECTIONS = (
    test_config_chain, test_oss_credentials, test_failure_taxonomy, test_gui_wording,
    test_deepseek_and_cache, test_doctor, test_delivery_chain, test_state_dir,
    test_clock_skew, test_time_basis, test_console_encoding, test_doctor_flag_parity,
    test_workflow_shell_syntax, test_packaging_manifest_gate,
    test_windows_onedir_delivery, test_state_file_never_relative_cwd,
    test_relaunch_chain_off_the_bundle, test_channel_failure_kind,
    test_app_translocation_relocation, test_path_discipline_gate,
    test_switch_discipline_gate,
    test_window_smoke_gate,
)


def main():
    print("═══ 打包态交付链 离线测试 ═══")
    for section in SECTIONS:
        _run(section)
    # 收尾硬判据：每一节拨走的运行形态开关都必须自己收回去。不查这一条的话，「邻居
    # 没收拾」这种缺陷要等下一节撞出一个与产品毫不相干的栈才会被发现——本轮 T 节把
    # sys.executable 还原成 None，红的是隔了一整节的 U 节（Path(None) 的 TypeError），
    # 排查的人第一反应是「新加的门禁有 bug」。当场点名比隔节报错便宜得多。
    _assert_eq(_switches_left(), [],
               "跑完全部小节，运行形态开关要和开跑前一模一样（拨走不收回＝下一节的输入不再由夹具决定）")
    # 每一节都真的被跑到：新增一节却忘了登记进 SECTIONS，它就永远不会执行，而且从输出上
    # 看不出来——这正是 riskbird 那 13 个套件的形状（不是坏了，是压根没在跑）。
    _defined = {n for n, v in globals().items() if n.startswith("test_") and callable(v)}
    _missing = sorted(_defined - {s.__name__ for s in SECTIONS})
    _assert_eq(_missing, [], f"文件里每个 test_ 小节都在 SECTIONS 里（漏登记＝这一节从没跑过）")
    _assert_eq(len({s.__name__ for s in SECTIONS}), len(SECTIONS),
               "SECTIONS 里没有重名小节（重名会让一节被跑两遍、另一遍掩盖真红）")
    print(f"\n═══ 结果：{_PASS} 通过 / {_FAIL} 失败 ═══")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
