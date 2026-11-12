# -*- coding: utf-8 -*-
"""access_guard.py —— 账号权限：云展绑定 + OSS 白名单（分等级）+ 单点登录设备绑定。

设计（对标国内外大厂成熟实践）：
  · 零信任（Google BeyondCorp）「持续校验」：每次操作都要过权限判定，
    不做「启动查一次、终身有效」的弱模型
  · 读路径 stale-while-revalidate（CDN/Chrome 惯例）：按钮点击只读
    内存/本地磁盘缓存，绝不在 UI 线程同步等 OSS；缓存过期时由后台
    守护线程静默刷新，下一次点击自然用上新结果
  · fail-closed（OAuth/支付网关惯例）：名单、等级、设备绑定**任何一项没确认
    就严格阻断**——权限系统故障绝不退化为「人人可用」
  · 权限分层（AWS IAM policy ∩ SCP）：名单里的 level 决定这个账号能用哪一档
    能力，构建档位决定这个包里有哪些入口，两者取交集（能力矩阵见 edition.py）
  · 单点登录（会话令牌 + 代际号 generation）：每个账号一个绑定分片对象，
    后写覆盖即顶号；本机持有自己的 session/generation，读到不属于自己的
    分片就判定掉线，走「优雅停止 + 落盘 + 阻断」而不是硬杀进程
  · 心跳（移动端 push/长轮询的等价物）：采集是长任务，不能只在点按钮时校验，
    GUI 每 60 秒 tick() 一次，被顶号最迟一分钟内被发现
  · 本地持久化副本：最近一次成功拉取的名单、以及本机自己的认领记录都落盘，
    OSS 抖动或断网时已授权用户不受影响（可用性优先，安全边界仍是名单与
    绑定分片本身；重新联网后最迟 60 秒补上顶号判定）
  · 豁免表：绑定账号/停止/暂停/返回/关闭/取消等控件永不拦截——
    被拒用户仍能完成绑定动作、退出正在跑的任务，工具不把自己锁死

存放位置（OSS {prefix}/ 下）：
    acl/whitelist.json          名单与等级
        {"version": N, "updated_at": "...", "schema": "acl-v3",
         "accounts": ["15088131371", ...],          # 旧版客户端只读这个字段
         "grants":  {"15088131371": 0, ...}}        # 新版客户端读它拿等级
    acl/devices/{phone}.json    该账号当前绑定的设备（后写覆盖 = 顶号）
        {"phone": "...", "device_id": "...", "device_label": "...",
         "session": "uuid4", "generation": 3, "bound_at": "...",
         "history": [最多 10 条换绑轨迹]}

为什么**一个账号一个分片对象**而不是所有人挤在一个 devices.json 里：
OSS 的条件写在本站客户端（v1 头签名）里没有实现，多个客户端对同一个共享文件做
「读-改-写」会互相覆盖别人的记录——A 顶掉 B 的同时把 C 的绑定一起抹掉。按账号
分片之后每次写只有一个写者，天然消除跨账号竞态；这与数据池按
``users/{name}/meta.json`` 分片是同一套做法。

管理员入口（在 OSS 控制台直接改 json 也可以，字段格式见上）：
    python -m access_guard --show                      看名单/等级/本机设备
    python -m access_guard --add 13800138000=1         加账号并指定等级
    python -m access_guard --set-level 13800138000=2   改等级
    python -m access_guard --remove 13800138000        移出白名单（连设备绑定一起删）
    python -m access_guard --devices                   看每个号绑了哪台机器
    python -m access_guard --kick 13800138000          强制解绑（所有设备掉线）
    python -m access_guard --claim [号码]              把本机登记为该号当前设备
（改完本机立即生效；其他设备的工具在下一次刷新落地的那一刻就被唤醒，最迟 60 秒）

只有一台电脑怎么验顶号（源码运行专用开关，打包态一律忽略）：
    B2B_DEVICE_ID=任何字符串 python -m access_guard --claim
    ↑ 这一条命令用「另一台设备」的身份接管账号，正在运行的工具会在几秒内收到
      顶号事件、优雅停止并落盘；撤掉环境变量，本机还是原来那台设备。
      模拟身份不写盘、不进缓存，也不会污染这台机器的真实指纹与换绑轨迹。

图形界面版：主界面「权限管理」（仅构建 pro + 等级 0 的账号能看到该入口）

能力等级怎么落到界面上：见 ``edition.py`` 的 ``_LEVEL_CAPS``——本模块只负责
「算出等级并回填」，界面只负责「问能力有没有」，两边都不写对方的规则。
"""
import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from PyQt5.QtCore import QEvent, QObject, Qt, pyqtSignal
except ImportError:  # 离线测试（无 PyQt5）仍可 import 本模块
    QObject = object  # type: ignore[misc,assignment]
    QEvent = None     # type: ignore[assignment]
    Qt = None         # type: ignore[assignment]

    def pyqtSignal(*_a, **_k):     # type: ignore[misc]
        """无 Qt 时的占位符：类定义必须照样能过，发射点各自判空跳过。"""
        return None

# 能力矩阵与等级定义的唯一真源在 edition.py：权限模型不允许在这里另写一份，
# 否则「等级 2 = 丐版功能集」这句话会在两个模块里慢慢长成两个意思。
import edition as _ed

# ── 常量口径 ────────────────────────────────────────────────────────────────
ACL_KEY = "acl/whitelist.json"
BIND_KEY_TPL = "acl/devices/{phone}.json"
ACL_SCHEMA = "acl-v3"
# 播种名单：只有初始管理员。等级 0 = 唯一持有「权限管理」入口的账号。
DEFAULT_GRANTS: Dict[str, int] = {"15088131371": _ed.LEVEL_ADMIN}
DEFAULT_ACCOUNTS = sorted(DEFAULT_GRANTS)       # 兼容旧调用方的只读别名

CACHE_TTL_SECONDS = 60                  # 内存缓存有效期（过期即触发后台刷新）
DISK_MAX_AGE_SECONDS = 7 * 86400        # 磁盘副本 / 本地认领记录最长可接受年龄
FIRST_FETCH_WAIT_SECONDS = 3            # 冷启动首次拉取的有界等待（超时先拒绝）
STARTUP_AUTH_WAIT_SECONDS = 3.0         # 开机「查身份→定档」建界面前的有界权威等待（见 prefetch_authoritative）
RECLAIM_COOLDOWN_SECONDS = 60           # 被顶掉后重新认领的最短间隔（防互相顶号）
_TOAST_INTERVAL = 4.0                   # 拒绝提示最短间隔（防连点刷屏）
_HISTORY_MAX = 10                       # 换绑轨迹最多留几条（管理员取证用）
# 合成状态标记：远端分片不存在、但本机曾经认领过 = 被解绑，必须走显式重新绑定
RELEASED_STATE = "released"

# ── 时间基准：进程内用单调时钟，落盘时间戳要防「落在将来」 ──────────────────
# 为什么这件事值得单独立一节：本轮出问题的机器恰恰是**时钟不靠谱**的那批（系统
# 「自动设置时间」被关掉、主板电池没电、虚拟机长时间挂起后恢复）。这类机器上，用
# 墙上时钟算「距上次刷新过了 60 秒没有」会长出三种只在它们身上出现的病症：
#   · 时间被往回调（NTP 纠正 / 用户手动改）之后，上一次刷新的时刻跑到将来去了，
#     差值永远是负数 → 缓存被判定成**永远新鲜**，后台再也不刷新，被管理员删掉的
#     账号在这台机器上能一路用到进程结束；
#   · 有界等待 while time.time() < deadline 同理会从 3 秒变成**几小时的界面卡死**；
#   · 磁盘副本的「最长 7 天」也一样失效，断网时照信一份不知道多老的名单。
# 收口办法：进程内的新鲜度与等待一律用 time.monotonic()（它的语义本来就是「过了
# 多久」，对改系统时间免疫）；必须跨进程比较的落盘时间戳仍用墙上时钟，但落在将来
# 的时间戳按**不可信**处理（等同于最旧）——宁严勿宽。口径对齐 JWT 的 iat/nbf 容忍
# 窗口与主流云 SDK 的时钟偏差处理：允许几秒抖动，不允许拿一个未来越过所有上限。
CLOCK_FUTURE_TOLERANCE_SECONDS = 120.0    # 落盘时间戳最多允许超前这么久
_MONO_NEVER = float("-inf")               # 「一次都没成功过」的单调时钟哨兵
_STALE = float("inf")                     # 「时间戳不可信」= 按最旧处理


def _mono_age(since: float) -> float:
    """进程内距上次成功过去了多久（单调时钟，不受改系统时间影响）。"""
    return time.monotonic() - since


def _stamp_age(ts) -> float:
    """落盘时间戳距今多久（秒）；不可信（缺失 / 非数 / 落在将来）时返回 _STALE。

    返回 _STALE 而不是抛错：调用方的判据一律是「比上限还旧就丢掉」，不可信的时间戳
    应当走同一条 fail-closed 分支，而不是把一次权限刷新带崩。
    """
    try:
        value = float(ts or 0)
    except (TypeError, ValueError):
        return _STALE
    if value <= 0:
        return _STALE
    delta = time.time() - value
    if delta < -CLOCK_FUTURE_TOLERANCE_SECONDS:
        return _STALE
    return max(0.0, delta)


LEVEL_ADMIN = _ed.LEVEL_ADMIN
LEVEL_FULL = _ed.LEVEL_FULL
LEVEL_LITE = _ed.LEVEL_LITE
LEVEL_NAMES = _ed.LEVEL_NAMES

_MOBILE_RE = re.compile(r"^1[3-9]\d{9}$")
_PHONE_RE = re.compile(r"^\+?86[-\s]?")


def _normalize_account(phone: str) -> str:
    """账号归一：去空格/去 +86 前缀，保证 OSS 名单与本地绑定号同口径比较。"""
    s = str(phone or "").strip()
    s = _PHONE_RE.sub("", s).replace(" ", "").replace("-", "")
    return s


def _now_text() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ── 本地状态目录与三份小文件 ────────────────────────────────────────────────
# 三份文件各自只有一个写者（名单副本 / 设备指纹 / 认领记录），所以都不需要
# 读-改-写合并；合成一份大文件反而会因为「刷新名单时把认领记录覆盖掉」出事故。

# 首选落点写不进去时的备用落点：与丐版同一个应用数据目录，但不和数据池混在一起
BACKUP_STATE_SUBDIR = "acl"

# 本地状态读写中如实记下的问题（只有路径与异常类型，绝不带文件内容）：断网时
# 「沿用上一次的身份等级」全靠磁盘副本，它落不下去必须让自检看得见。
_STATE_PROBLEMS: List[str] = []
# 一次运行只认一个落点（键 = 首选路径原文）：三份文件散在两个目录，比统一退到
# 备用目录更糟——那会出现「名单副本在桌面、认领记录在备用目录」的拼接状态。
_STATE_DIR_CACHE: Dict[str, Path] = {}


def state_problems() -> List[str]:
    """本地状态读写发现的问题（给 --acl-doctor 与日志读，界面无需再判一次）。"""
    return list(_STATE_PROBLEMS)


def _note_state_problem(text: str) -> None:
    if text and text not in _STATE_PROBLEMS:
        _STATE_PROBLEMS.append(text)


def _note_untrusted_stamp(what: str) -> None:
    """把「本地状态里的时间戳不可信」写进自检可见的问题清单。

    丢弃一份不可信的副本是 fail-closed 的正确方向，但**安静地**丢弃就是又一轮
    「用户被降级、界面上一个字都不提」：管理员照着提示去查网络、查白名单，而真正的
    原因是这台电脑的系统时间被改过（先快后正、或者手动调过）。写日志不如写清单——
    清单会被 --acl-doctor 原样打出来，远程排障只要那一份输出就够。
    """
    _note_state_problem(
        f"{what}里的时间戳不可信（缺失、非数字或落在将来 = 这台电脑的系统时间被改过），"
        f"已按最旧处理：断网期间这台机器会退回丐版")


def _forget_state_dir() -> None:
    """忘掉已解析的落点，下一次重新判定。

    落点在**正常情况**下一次进程只定一次：`_state_file` 会在能力检查、认领、刷新
    这些路径上被反复调用，每次都去桌面上写删一个探针文件，在漫游用户目录/网络盘的
    机器上就是实打实的拖慢。但「真写失败」是这条判定唯一的凭据失效的信号——那一刻
    起缓存的结论已经不被事实支持了，必须重新判一次：这样桌面被安全软件临时拦住、
    稍后又放开的机器能自己回到首选目录，而不是整个进程都留在备用目录里。
    """
    _STATE_DIR_CACHE.clear()


def _writable(path: Path) -> Optional[bool]:
    """True/False = 探针结论；None = 探针模块本身没了（此时不下任何结论）。

    判据复用 tool_config.probe_writable：「目录在不在」与「写不写得进去」是两件
    事，两处各写一份迟早对不上，而对不上时看到假阳性的永远是远程排障的人。
    """
    try:
        from tool_config import probe_writable
    except Exception:
        return None
    return probe_writable(path)


def _preferred_state_dir() -> Path:
    """档位投影后的首选落点（pro 在桌面数据池缓存目录，lite 在应用数据目录）。"""
    default = Path.home() / "Desktop" / "数据池缓存"
    return Path(_ed.pool_cache_dir(str(default)))


def _backup_state_dir() -> Path:
    return _ed.app_data_dir() / BACKUP_STATE_SUBDIR


def _state_dir() -> Path:
    """权限本地状态目录：与数据池缓存同目录、按构建档位投影，且必须**真写得进去**。

    外部用户的桌面上不该出现一个「数据池缓存」文件夹，里面还躺着
    .acl_cache.json 这种一眼就能看出「有白名单机制」的文件——路径判定同样
    只放在 edition 模块里，不在这里写 if lite。

    为什么还要多走一次写探针：首选落点在**用户桌面**，而「桌面上写不进东西」在真实
    机器上很常见——桌面被同步盘重定向、被安全软件的文件夹保护拦着、公司策略把用户目录
    挂成只读。开发机一切正常，外部机器上三份文件一份也落不下去：断网时读不到上一次的
    身份等级（只能退回丐版）、被顶号后的冷却时间归零，而界面上一个字都不提。
    「静默失败 + 只在开发机成立」正是本轮缺陷的形状，所以这里宁可多探一次，也要把问题
    记进 :func:`state_problems` 让自检看得见。
    """
    want = _preferred_state_dir()
    key = str(want)
    hit = _STATE_DIR_CACHE.get(key)
    if hit is not None:
        return hit

    ok_want = _writable(want)
    if ok_want:
        chosen = want
    else:
        backup = _backup_state_dir()
        ok_backup = _writable(backup)
        if ok_want is None:
            # 探针模块自己都没了：不拿猜测定落点，也不凭空报一个故障（产物本来就是
            # 坏的，这一点由 --acl-doctor 的模块类退出码说，比这里准确得多）
            chosen = want
        elif ok_backup:
            chosen = backup
            _note_state_problem(f"权限本地状态首选目录不可写（{want}），改用备用目录：{backup}")
        else:
            # 两处都落不下去：留在首选路径（与修之前一致），但把后果写在问题清单里，
            # 具体哪份文件没落地由 _write_json 的失败记录补上。
            chosen = want
            _note_state_problem(f"权限本地状态目录不可写（{want}），备用目录同样不可写："
                                f"断网时无法沿用上一次的身份等级")
    _STATE_DIR_CACHE[key] = chosen
    return chosen


def _state_file(name: str) -> Path:
    return _state_dir() / name


def _cache_file() -> Path:
    """最近一次成功拉取的名单与等级副本（OSS 抖动时用它的旧值判定）。"""
    return _state_file(".acl_cache.json")


def _device_file() -> Path:
    return _state_file(".acl_device.json")


def _claim_file() -> Path:
    return _state_file(".acl_claim.json")


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, data) -> bool:
    """临时文件 + 原子替换：断电也不会留下半个坏 JSON。

    失败绝不静默：返回值给调用方判断，同时记一条进 :func:`state_problems`。这三份
    文件是断网时唯一能用的身份凭据，「写失败但没人知道」和这次的「读不到密钥但说成
    网络问题」是同一类缺陷——症状轻，查起来最费时间。
    """
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        return True
    except Exception as e:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        _note_state_problem(f"写入 {path.name} 失败（{type(e).__name__}: "
                            f"{str(e)[:120]}），目录 {path.parent}")
        _forget_state_dir()      # 写失败 = 落点判定的凭据没了，下一次重新判定
        return False


# ── 设备指纹 ────────────────────────────────────────────────────────────────
# 取各平台出厂就固定、且不随用户改名/换网络变化的机器标识，散列后使用：
#   macOS   IOPlatformUUID（system_profiler 同款，重装系统也不变）
#   Windows MachineGuid（HKLM\\SOFTWARE\\Microsoft\\Cryptography，装机时生成）
#   Linux   /etc/machine-id（systemd 装机生成）
# 原文不外传：只把 sha256 的前 16 位送到 OSS，管理员看到的是可读的主机名。
# 计算结果**落盘持久**，之后不再重复取硬件 ID：一来快，二来万一某台机器的
# machine-id 因为克隆镜像/重装而漂移，持久化能让它不至于自己把自己顶掉。

_DEVICE_ID: Dict[str, str] = {}      # 进程内缓存（也保证测试里可预期）

# 只给源码运行用的「扮演另一台设备」开关：一台机器复现顶号全过程（管理员与离线测试
# 都靠它），冻结态**一律忽略**——否则外面的人设一个环境变量就能把设备指纹换成任意值，
# 多处使用想开几台开几台，单点登录当场归零，而这正是它存在的唯一理由。
# 与 edition 的 B2B_EDITION/B2B_ACL_LEVEL 同一口径：源码可用、打包态失效。
DEVICE_ID_ENV_VAR = "B2B_DEVICE_ID"


def _simulated_identity() -> Optional[Dict[str, str]]:
    """环境变量指定的模拟设备身份；不适用时返回 None（走真实指纹）。"""
    if getattr(sys, "frozen", False):
        return None
    fake = str(os.environ.get(DEVICE_ID_ENV_VAR) or "").strip()
    if not fake:
        return None
    return {
        "device_id": hashlib.sha256(
            f"B2BCollect|env|{fake}".encode("utf-8")).hexdigest()[:16],
        # 标签里明写「模拟设备」：管理员在 --devices 面板看到的必须是真话，
        # 不然排查线上问题时会被一条看起来像正常机器的记录带偏。
        "label": f"{_device_label()} · 模拟设备:{fake[:24]}",
        "source": "env",
    }


def _device_label() -> str:
    """给人看的设备名：主机名 + 系统，不含任何硬件标识原文。"""
    try:
        return f"{platform.node() or 'unknown-host'} · {platform.system()}"
    except Exception:
        return "unknown-host"


def _raw_machine_id() -> Tuple[str, str]:
    """返回 (来源标记, 原文)；取不到返回 ("", "")。"""
    try:
        if sys.platform == "darwin":
            out = subprocess.run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                                 capture_output=True, text=True, timeout=5).stdout
            m = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', out or "")
            if m:
                return "IOPlatformUUID", m.group(1)
        elif sys.platform.startswith("win"):
            import winreg
            # 64 位视图优先：32 位进程读的是 WOW6432Node 里那份复制值
            for flag in (winreg.KEY_WOW64_64KEY | winreg.KEY_READ, winreg.KEY_READ):
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                        r"SOFTWARE\Microsoft\Cryptography", 0, flag) as k:
                        val, _ = winreg.QueryValueEx(k, "MachineGuid")
                        if val:
                            return "MachineGuid", str(val)
                except OSError:
                    continue
        else:
            for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
                if Path(p).is_file():
                    v = Path(p).read_text(encoding="utf-8").strip()
                    if v:
                        return "machine-id", v
    except Exception:
        pass
    return "", ""


def device_identity() -> Dict[str, str]:
    """本机设备身份：{"device_id": 16 位散列, "label": 主机名 · 系统, "source": 来源}。

    任何一步失败都必须能给出一个**稳定**的 device_id：返回空串会让所有取不到
    指纹的机器看起来是同一台设备，等于把「一个人多处使用」变成「谁都互相顶号」。
    所以最后一级兜底是随机 UUID 落盘——它不再绑定硬件，但仍然一台机器一个值。
    """
    # 模拟身份排在缓存与磁盘之前：环境变量是进程启动那一刻就定下的，放在后面
    # 会因为先缓存了真实指纹而「设了不生效」——最难查的一类假开关就是这么来的。
    # 它不写盘、不进进程缓存，撤掉变量就回到真实指纹，绝不让一次调试把这台机器的
    # 永久身份改掉（那会连带 history 与冷却记录一起失真）。
    sim = _simulated_identity()
    if sim:
        return sim

    if _DEVICE_ID.get("device_id"):
        return dict(_DEVICE_ID)

    cached = _read_json(_device_file(), None)
    if isinstance(cached, dict) and cached.get("device_id"):
        _DEVICE_ID.update({"device_id": str(cached["device_id"]),
                           "label": str(cached.get("label") or _device_label()),
                           "source": str(cached.get("source") or "cached")})
        return dict(_DEVICE_ID)

    source, raw = _raw_machine_id()
    if not raw:
        source, raw = "random", uuid.uuid4().hex
    did = hashlib.sha256(f"B2BCollect|{source}|{raw}".encode("utf-8")).hexdigest()[:16]
    label = _device_label()
    _write_json(_device_file(), {"device_id": did, "label": label,
                                 "source": source, "created_at": _now_text()})
    _DEVICE_ID.update({"device_id": did, "label": label, "source": source})
    return dict(_DEVICE_ID)


def device_id() -> str:
    return device_identity()["device_id"]


def device_label() -> str:
    """给人看的设备名，与 ``device_id()`` 出自**同一次** identity 快照。

    分开取最容易长出自相矛盾的记录：指纹是模拟的那台、标签却还是真实主机名，
    管理员照着标签去找机器，那台机器却说「我没绑过这个号」。身份要么整体真、
    要么整体假，不许一半一半。
    """
    return device_identity()["label"]


def reset_device_identity_for_tests() -> None:
    """清进程内指纹缓存（测试重定向状态目录后必须重新算一次）。"""
    _DEVICE_ID.clear()


def _load_claims() -> Dict[str, dict]:
    data = _read_json(_claim_file(), {})
    return data if isinstance(data, dict) else {}


def _claim_of(phone: str) -> dict:
    rec = _load_claims().get(phone)
    return rec if isinstance(rec, dict) else {}


def _save_claim(phone: str, generation, session: str) -> None:
    """记下落笔时的代际号：这是「我还在当前会话上吗」的本地凭据。"""
    claims = _load_claims()
    claims[phone] = {"device_id": device_id(), "generation": int(generation or 0),
                     "session": str(session), "claimed_at": _now_text(),
                     "claimed_ts": time.time()}
    _write_json(_claim_file(), claims)


def _note_kicked(phone: str) -> None:
    claims = _load_claims()
    rec = dict(claims.get(phone) or {})
    rec["kicked_at"] = _now_text()
    rec["kicked_ts"] = time.time()
    claims[phone] = rec
    _write_json(_claim_file(), claims)


def _reclaim_blocked(phone: str) -> float:
    """被顶掉之后重新认领还要等多久（秒）。0 = 现在就能认领。

    没有这一步，两台机器（或两个人）可以各点一次绑定把会话抢来抢去，谁都在
    「刚能用又被踢」的抖动里，管理员也查不出到底是谁在用。从没被顶过的机器
    （新设备第一次认领）不受这个限制，正常换机不额外等待。
    """
    raw = _claim_of(phone).get("kicked_ts")
    if not raw:
        return 0.0
    age = _stamp_age(raw)     # 脏值（非数字、缺失）也在里面按不可信处理，不许抛错
    if age == _STALE:
        # 时间戳落在将来 = 这台机器的时钟被动过。照走一整段冷却：既不给人「把钟
        # 拨回去就能立刻抢回来」的空子，也不会算出几小时几天的荒唐等待（那种数字
        # 写在界面上，用户只会当成工具坏了）。
        return float(RECLAIM_COOLDOWN_SECONDS)
    return max(0.0, min(RECLAIM_COOLDOWN_SECONDS,
                        RECLAIM_COOLDOWN_SECONDS - age))


# ── 名单解析 ────────────────────────────────────────────────────────────────

def _parse_accounts(data) -> List[str]:
    """解析白名单 JSON：兼容 list / {"accounts":[...]} / 只有 grants 三种形态。

    只保留手机号形态的合法条目（编辑 OSS 文件难免手滑，脏条目直接丢弃，
    宁严勿宽：非法条目永远匹配不上任何账号，留着只会掩盖配置错误）。
    """
    if isinstance(data, dict):
        raw = data.get("accounts", [])
        if not isinstance(raw, list) or not raw:
            # 有人只在 grants 里加了号（accounts 忘了同步）：也认下来，
            # 但等级字段照旧缺位，读侧按最保守处理
            g = data.get("grants")
            raw = list(g.keys()) if isinstance(g, dict) else []
    elif isinstance(data, list):
        raw = data
    else:
        raw = []
    out = []
    for x in raw:
        if isinstance(x, dict):          # 兼容 [{"phone":...,"level":...}] 写法
            a = _normalize_account(x.get("phone") or x.get("account"))
        else:
            a = _normalize_account(x)
        if a and _MOBILE_RE.match(a):
            out.append(a)
    return out


def _parse_grants(data) -> Tuple[Dict[str, int], bool]:
    """解析等级字段，返回 (号码→等级, 名单里是否声明过等级)。

    「没声明」与「声明了但漏了这个号」是两件事，必须分开：
      · 整份文件没有 grants 字段 = 旧版名单，还没有等级概念 → 沿用旧行为
        （LEGACY_ACCOUNT_LEVEL：全功能，但不给管理入口）
      · 有 grants 但查不到这个号 = 有人只往 accounts 加号、忘了给等级
        → 按最保守那一档（UNKNOWN_ACCOUNT_LEVEL），绝不默认放行
    判据取「字段在不在」而不是「条目空不空」：管理员把等级清空是一次**配错**，
    不是「退回旧版」，配错必须往最保守那一边走（同 edition.resolve 的口径）。
    """
    if not isinstance(data, dict):
        return {}, False
    items: List[Tuple[object, object]] = []
    raw = data.get("grants")
    declared = isinstance(raw, (dict, list))
    if isinstance(raw, dict):
        items += list(raw.items())
    elif isinstance(raw, list):
        for x in raw:
            if isinstance(x, dict):
                items.append((x.get("phone") or x.get("account"), x.get("level")))
    accounts = data.get("accounts")
    if isinstance(accounts, list):
        for x in accounts:
            if isinstance(x, dict) and x.get("level") is not None:
                declared = True
                items.append((x.get("phone") or x.get("account"), x.get("level")))

    grants: Dict[str, int] = {}
    for k, v in items:
        a = _normalize_account(k)
        if not a or not _MOBILE_RE.match(a):
            continue
        try:
            lv = int(v)
        except (TypeError, ValueError):
            continue
        if lv in _ed.LEVELS:
            grants[a] = lv
    return grants, declared


def resolve_level(phone: str, grants: Dict[str, int], declared: bool) -> int:
    """某个账号的权限等级（带两条兜底口径，见 _parse_grants 注释）。"""
    if not declared:
        return _ed.LEGACY_ACCOUNT_LEVEL
    return grants.get(phone, _ed.UNKNOWN_ACCOUNT_LEVEL)


def _new_document(grants: Dict[str, int], version: int = 1) -> dict:
    """生成 accounts / grants 两份都齐的名单：accounts 给旧客户端读，grants 给新的。

    只发一份字段会在灰度期出事故——旧 exe 拿到只含 dict 的 accounts 会把所有条目
    当脏数据丢掉，全公司瞬间没人能用工具。加字段不改老字段，是这份名单从 v1
    升到 v3 仍然要求向后兼容的全部原因。
    """
    return {"accounts": sorted(set(grants)), "grants": dict(grants),
            "schema": ACL_SCHEMA, "updated_at": _now_text(), "version": int(version)}


def _device_phrase(binding: dict) -> str:
    """把「哪台机器、什么时候」压缩成一句可读证据（两个文案共用同一份口径）。"""
    label = str(binding.get("device_label") or "另一台设备")
    when = str(binding.get("bound_at") or "")[:19].replace("T", " ")
    return f"{label}" + (f"，{when}" if when else "")


def _revoked_reason(binding: dict, ever_claimed: bool) -> str:
    """设备不是本机时的文案：必须区分「被顶掉」和「这台机器从来没抢到」。

    两种情况的下一步动作完全不同：前者是「我的会话被人接管了」，后者是
    「这个号还挂在别人机器上，我要不要把它拿过来」。写成同一句话，用户
    会以为自己刚才还在用、其实是第一次点——提示与实际不符就会找错方向。
    """
    who = _device_phrase(binding)
    if ever_claimed:
        return (f"当前云展账号已在新设备使用（{who}），本机使用权限已中止。\n"
                "要继续在本机使用，请点「绑定云展动力账号」重新登记本机；"
                "若非本人操作，请联系管理员核实账号归属。")
    return (f"该云展账号当前绑定在另一台设备上（{who}），本机还没有使用权限。\n"
            "点「绑定云展动力账号」即可把使用权限登记到本机；登记后原设备会同时失去权限。")


def _released_reason() -> str:
    return ("该云展账号的设备绑定已被解除（管理员解绑或此前在本机放弃过认领）。\n"
            "请点「绑定云展动力账号」重新登记本机后继续使用。")


# ── 守卫主体 ────────────────────────────────────────────────────────────────

class AccessGuard(QObject):
    """权限守卫：check() 判定 + eventFilter() 全局按钮拦截 + 设备认领与顶号。"""

    # 「判定结果可能已经变了」的唤醒信号，只在一次真实刷新结束时发射。
    # 为什么界面不能只靠 60 秒心跳轮询：心跳只能看到**上一次**刷新留下的结果，
    # 于是被顶号的机器最坏要等两拍（120 秒）才停手；而刷新明明是后台线程刚跑完的，
    # 那一刻界面完全有条件立刻收口。信号只做唤醒、不带结论——结论仍然由
    # take_revoked() 这一份一次性信箱交出，避免两条路径各拿一份重复弹窗。
    refreshed = pyqtSignal()

    def __init__(self, ttl: float = CACHE_TTL_SECONDS):
        super().__init__()
        self._ttl = float(ttl)
        self._lock = threading.Lock()
        self._accounts: Optional[List[str]] = None   # 最近一次成功拉取的名单
        self._grants: Dict[str, int] = {}            # 号码 → 等级
        self._grants_declared = False                # 名单里有没有等级字段
        self._binding: Optional[dict] = None         # 该账号当前的设备分片
        self._binding_phone = ""                     # 这份分片是为哪个号读的
        self._acl_at = _MONO_NEVER                   # 名单最近成功时刻
        self._bind_at = _MONO_NEVER                  # 绑定状态最近成功时刻
        self._fetched_at = _MONO_NEVER               # =min(_acl_at,_bind_at)
        self._disk_loaded = False                    # 磁盘副本只尝试加载一次
        # 权限服务可达性结论：kind ∈ unknown/ok/credential/module/other/network。
        # 默认 unknown（还没试过），check() 在没试过时会先做一次廉价探测，
        # 这样「包里没带密钥」第一次点击就能说清是哪一类，而不是永远套网络文案。
        self._service_kind = "unknown"
        self._service_detail = ""
        self._inflight = False                       # 后台刷新去重（含冷启动窗口）
        self._enrolling: set = set()                 # 自动登记在途的账号（去重）
        self._last_toast = _MONO_NEVER
        self._revoked = ""                           # 待交出去的顶号事件（一次性）
        self._revoked_latched = False                # 本轮掉线是否已经报过
        self._account_reader = None                  # 测试注入点：绑定账号读取函数

    # ── 依赖懒加载（缺模块/缺密钥都不许在 import 期炸） ──

    @staticmethod
    def _load_bound_account() -> str:
        try:
            import task_registry as _tasks
            return _tasks.load_account() or ""
        except Exception:
            return ""

    @staticmethod
    def _build_client():
        client, _kind, _detail = AccessGuard._client_probe()
        return client

    def _acquire_client(self, client=None):
        """``(client, kind, detail)``：先走可被替换的 ``_build_client``，失败才补分类。

        为什么不是直接用 ``_client_probe()``：``_build_client`` 是本类**唯一**的客户端
        构造入口，离线测试与内部夹具都顶掉它（实例级与类级两种）。分类探测绕开它，
        「跑一遍测试」就会变成「拿真实密钥连一遍生产 OSS」。成功路径不多花任何一次
        凭证解析；失败路径多出的那一次只读配置、不出网。
        """
        if client is not None:
            return client, "ok", ""
        built = self._build_client()
        if built is not None:
            return built, "ok", ""
        _probe, kind, detail = self._client_probe()
        return None, kind, detail

    @staticmethod
    def _client_probe():
        """构造 OSS 客户端，并把失败原因**分类**交出去。

        返回 ``(client 或 None, kind, detail)``，kind ∈ ok / module / credential / other。

        为什么必须分类（2026-11 外部机器全员丐版）：过去这里是一个
        ``except Exception: return None``，把「这个包压根没带密钥」「包里缺依赖」
        「网络不通」三种完全不同的故障压成同一个 None，上层只能统一说成
        「无法连接权限服务器，请检查网络后重试」。于是发包侧漏配凭证这种**构建期
        就能查出来**的缺陷，被文案一路推给终端用户的网络和管理员的白名单，
        谁都不会去查那个真正的地方（打包工作流）。分类是把这个缺陷从
        「不可诊断」变成「一句话可诊断」的前提。
        """
        try:
            from oss_pool_client import OSSPoolClient, OSSCredentialError
        except Exception as e:
            return None, "module", (
                f"权限模块加载失败（{type(e).__name__}: "
                f"{str(e).splitlines()[0][:120]}）")
        try:
            return OSSPoolClient(), "ok", ""
        except OSSCredentialError as e:
            return None, "credential", str(e)
        except Exception as e:
            return None, "other", (
                f"{type(e).__name__}: {str(e).splitlines()[0][:120]}")

    @classmethod
    def _must_client(cls, client=None):
        """管理入口专用：拿到可用的 OSS 客户端，拿不到就抛**分类过**的异常。

        校验路径（check/claim）失败可以退成「暂时不给用」，管理路径不行：
        操作的人就是管理员本人，一句「初始化失败（缺少密钥或依赖模块）」把
        三种故障写成一件事，等于让人对着猜。分类后的信息直接指向该改的地方。
        """
        if client is not None:
            return client
        own = cls._build_client()
        if own is not None:
            return own
        _probe, kind, detail = cls._client_probe()
        raise RuntimeError(cls._admin_client_failure(kind, detail))

    @staticmethod
    def _admin_client_failure(kind: str, detail: str = "") -> str:
        head = {
            "credential": ("权限服务凭证不可用：本机没有 OSS 密钥"
                           "（安装包应由 CI 注入 config.json，开发态应配置 "
                           "config.json 或 OSS_ACCESS_KEY_ID/OSS_ACCESS_KEY_SECRET）"),
            "module": "权限服务模块加载失败：安装包缺依赖或文件损坏",
            "other": "权限服务客户端构造失败",
        }.get(kind, "权限服务客户端不可用")
        return f"{head}｜{detail}".strip("｜") if detail else head

    # 各类故障对应的用户口径：判据与下一步必须同源，否则就是把人往错的方向引。
    _UNREACHABLE_TEXT = {
        "credential": (
            "这个版本的工具没有带上权限服务凭证，这不是你的网络问题，不用检查网络。"
            "属发包环节漏配：请向开发者索取最新版本的安装包（开发者处理办法见 "
            "workflow 的 OSS Secret 注入步骤）。"),
        "module": (
            "权限服务模块没能加载（安装包缺依赖或文件损坏），不是你的网络问题。"
            "请重新下载最新安装包，或向开发者反馈。"),
        "other": (
            "权限服务返回异常，暂时无法确认本机使用权限。请稍后重试；"
            "持续如此请把本条提示反馈给开发者。"),
        "denied": (
            "权限服务拒绝了本次访问：密钥已失效，或这把密钥的权限范围里没有"
            "权限名单这条路径。这不是你的网络问题，也不用改白名单——需要开发者"
            "检查随包密钥与 RAM 权限策略。"),
        "clock": (
            "这台电脑的系统时间与标准时间相差太大，权限服务按防盗用规则拒绝了"
            "本次请求。这不是网络问题，也不是账号问题：把系统的「自动设置时间」"
            "打开就能恢复。"),
        "tls": (
            "这台电脑到权限服务之间的加密连接被中途拦下了（常见于安全软件的网页"
            "过滤、公司代理或不受信任的根证书）。这不是网络不通，跟账号也没有"
            "关系：浏览器能打开网页，工具照样连不上。"),
        "network": (
            "无法连接权限服务器，请检查网络后重试；仍无法使用请联系管理员"),
    }

    # 「这条 reason 属于哪一类故障」的反查表。界面拿到的是文案字符串，要按类别
    # 决定「能不能承诺自动补齐」就必须先把它归一次类。
    # 为什么表放在这里而不是界面里：文案和归类同源才不会脱节——归类关键词写在
    # 界面里，改一句文案就会悄悄改掉一条分支（这是上一轮「提示与实际不符」的
    # 同款结构问题）。
    _REASON_MARKERS = {
        "credential": ("没有带上权限服务凭证", "权限服务凭证不可用"),
        "module": ("权限服务模块没能加载", "权限服务模块加载失败"),
        "denied": ("权限服务拒绝了本次访问",),
        "clock": ("系统时间与标准时间相差太大",),
        "tls": ("加密连接被中途拦下",),
        "other": ("权限服务返回异常", "权限服务客户端构造失败"),
    }

    # 每一类故障「下一步该做什么」。判据与下一步必须成对出现：只说「连不上」
    # 而不说该谁去做什么，用户就只能试网络、管理员就只能翻名单。
    SERVICE_NEXT_STEP = {
        "credential": (
            "这一类不会自己变好：凭证是打包时注入的，本地改网络、重新绑定、"
            "加白名单都不起作用。请换用最新版本的安装包；开发者处理办法见 "
            "workflow 的 OSS Secret 注入步骤与 --acl-doctor 产物自检。"),
        "module": (
            "这一类也不会自己变好：请重新下载完整安装包（文件损坏或被安全软件"
            "拦截都会导致模块加载失败），仍不行就把本条提示反馈给开发者。"),
        "denied": (
            "这一类要找开发者：随包密钥已失效，或它的 RAM 权限范围里没有权限"
            "名单那条路径。改白名单没有用——请求根本没被服务接受。"),
        "clock": (
            "这一类在这台电脑上就能修好，不用换包也不用找开发者：打开系统的"
            "「自动设置时间」（Windows：设置 → 时间和语言 → 立即同步；"
            "macOS：系统设置 → 通用 → 日期与时间）后重新打开工具。"),
        "tls": (
            "这一类也在电脑上解决，账号侧什么都用不着做：\n"
            "· 装了带「网页过滤 / HTTPS 扫描」的安全软件，或走公司代理：临时关掉"
            "该扫描，或请 IT 给 *.aliyuncs.com 放行（代理例外 + 受信任证书）；\n"
            "· 上面都不是：请把这条提示原样反馈给开发者——安装包缺可信根证书也是"
            "这个形态，需要开发者重新发包。"),
        "other": (
            "请先稍后重试一次；持续出现请把本条提示的原文反馈给开发者，"
            "这类故障需要在服务端排查，本地不需要改动。"),
    }

    @classmethod
    def classify_reason(cls, reason: str) -> str:
        """把一条 reason 文案归回故障类别，认不出来返回空串。"""
        r = reason or ""
        for kind, marks in cls._REASON_MARKERS.items():
            if any(mark in r for mark in marks):
                return kind
        return ""

    def service_status(self) -> Tuple[str, str]:
        """最近一次权限服务可达性结论：``(kind, detail)``。

        界面、自检、CLI 都读这一个口径，不许各自再猜一次。kind 为 ``ok`` 表示
        最近一次刷新真的从远端拿到了名单。
        """
        with self._lock:
            return self._service_kind, self._service_detail

    @classmethod
    def _unreachable_text(cls, kind: str) -> str:
        """类别 → 用户口径的一句话（认不出的类别按网络类兜底，绝不空手而归）。"""
        return cls._UNREACHABLE_TEXT.get(kind or "network",
                                         cls._UNREACHABLE_TEXT["network"])

    def _unreachable_reason(self) -> str:
        """把「拉不到名单」翻译成按故障分类的一句话。

        还没试过（unknown）时先做一次**不出网**的本地探测：构造客户端只做
        「模块能不能 import、密钥能不能解析」两件事，微秒级。这一步是整条链路
        的关键——没有它，「包里没带凭证」这种必然失败的情况会永远显示成
        「请检查网络」，因为网络确实没坏，只是根本没资格出网。
        """
        with self._lock:
            kind = self._service_kind
        if kind == "unknown":
            _c, kind, detail = self._acquire_client()
            if _c is not None:
                kind = "network"      # 客户端能建起来，才谈得上是网络问题
                detail = ""
            self._set_service(kind, detail)
        # credential 这一类在开发态与打包态的处理人不同（本机没配 / 发包漏注入），
        # 但两边都不该被说成网络问题；具体是谁的活儿由文案里那句「处理办法见
        # workflow」自己交代，不在这里再分叉一次
        return self._unreachable_text(kind)

    def _bound_phone(self) -> str:
        reader = self._account_reader
        phone = reader() if reader else self._load_bound_account()
        return _normalize_account(phone or "")

    # ── 名单 + 绑定状态获取与缓存 ──

    def _set_service(self, kind: str, detail: str = ""):
        with self._lock:
            self._service_kind = kind
            self._service_detail = detail or ""

    @staticmethod
    def _classify_oss_error(err) -> Tuple[str, str]:
        """把一次 OSS 读失败归到「用户该往哪查」的类别上。

        网络类（超时/连不上/DNS）与凭证类（403、签名不符、密钥失效）在协议层
        完全不同，却常常被一起写成「连不上服务器」。前者该让用户看网络，后者
        该让开发者看密钥与 RAM 权限策略——归错类的代价是两边互相指。

        时间偏差单独一类（而且必须排在 403 之前判）：OSS 把请求时刻算进签名，
        本机时钟偏出 15 分钟会被回一个**和密钥失效同样形态的 403**。客户端平时
        会按服务端时间自愈重签（见 oss_pool_client._send），走到这里说明自愈没成
        功。这种故障用户自己就能修（校准时间），把它说成「找开发者换密钥」会让
        两边各自空跑一轮。
        """
        m = str(err or "")
        low = m.lower()
        if any(k in low for k in ("requesttimetooskewed", "difference between the request time",
                                  "时间偏差", "系统时间")):
            return "clock", m
        # 加密连接被中途拦下（安全软件网页过滤 / 公司代理 / 不受信任的根证书 / 打包
        # 态缺可信证书）：请求根本没到达 OSS，却常被写成「连不上服务器，请检查网络」
        # ——而它的修法与网络通不通毫无关系（浏览器能打开网页，工具照样连不上）。
        # 排在 denied 之前判：这类失败一个 HTTP 状态码都没有，绝不该被认成密钥问题。
        if any(k in low for k in ("ssl", "tls", "certificate", "handshake",
                                  "cert_verify_failed", "unable to get local issuer",
                                  "self signed", "wrong version number")):
            return "tls", m
        if any(k in low for k in ("signaturedoesnotmatch", "invalidaccesskeyid",
                                  "accessdenied", "invalidauthorization")) \
                or "403" in m or "签名" in m or "密钥" in m:
            return "denied", m
        if any(k in low for k in ("nosuchkey", "not found", "不存在")):
            return "network", m
        if any(k in low for k in ("timeout", "timed out", "connection",
                                  "unreachable", "getaddrinfo",
                                  "temporary failure", "name resolution")):
            return "network", m
        return "other", m

    def _fetch(self) -> bool:
        """一次刷新同时取回「名单+等级」和「本机绑定状态」。

        两者必须绑在同一次刷新里：只更新名单不更新绑定，会出现「等级已经变成
        受限版、设备判定还停留在上一个账号」的错配。两份结果各自记录成功时刻，
        任一处失败只让那一半保持待刷新状态，不把另一半的成功一起丢掉。
        """
        try:
            from oss_pool_client import OSSPoolError
            client, kind, detail = self._acquire_client()
            if client is None:
                # 客户端压根构造不出来：这从来不是网络抖动，先于任何出网动作把
                # 类别记下来，第一次点击就能把「包里没带密钥」和「网络不通」分开
                self._set_service(kind, detail)
                return False
            # 构造成功但还没出网：此刻的默认结论是「网络类」，一旦真拿到名单就改 ok
            self._set_service("network", "最近一次出网未取回远端名单")

            phone = self._bound_phone()

            ok_acl = False
            accounts: Optional[List[str]] = None
            grants: Optional[Dict[str, int]] = None
            declared = False
            try:
                doc, _ = client.download_json(ACL_KEY)
            except OSSPoolError as e:
                if "不存在" in str(e):
                    # 名单文件还没建过：播种初始名单（含等级）再重读一次
                    client.upload_json_atomic(ACL_KEY, _new_document(DEFAULT_GRANTS))
                    doc, _ = client.download_json(ACL_KEY)
                else:
                    k, d = self._classify_oss_error(e)
                    self._set_service(k, d)
                    doc = None
            if doc is not None:
                accounts = _parse_accounts(doc)
                grants, declared = _parse_grants(doc)
                ok_acl = True
                self._set_service("ok", "")
                with self._lock:
                    self._accounts = accounts
                    self._grants = grants or {}
                    self._grants_declared = declared
                    self._acl_at = time.monotonic()
                self._write_disk({"accounts": accounts, "grants": grants or {},
                                  "grants_declared": declared, "phone": phone})

            ok_bind = False
            binding: Optional[dict] = None
            if not phone:
                # 没绑定号：设备维度无从判定，也不阻塞名单刷新
                with self._lock:
                    self._binding = None
                    self._binding_phone = ""
                ok_bind = True
            else:
                try:
                    data, _ = client.download_json(self._bind_key(phone))
                    binding = data if isinstance(data, dict) else {}
                    ok_bind = True
                except OSSPoolError as e:
                    if "不存在" in str(e):
                        binding, ok_bind = self._on_shard_absent(client, phone)
                    else:
                        ok_bind = False        # 读不到就当不知道，绝不猜成「是本机」
                except Exception:
                    ok_bind = False
                if binding is not None:
                    with self._lock:
                        self._binding = binding
                        self._binding_phone = phone
                        self._bind_at = time.monotonic()

            with self._lock:
                self._fetched_at = min(self._acl_at, self._bind_at)
            self._evaluate(phone)
            self._notify_refreshed()
            return bool(ok_acl and ok_bind)
        except Exception:
            return False

    def _on_shard_absent(self, client, phone: str) -> Tuple[Optional[dict], bool]:
        """远端没有这个号的设备分片：区分「升级前绑定的存量机器」与「被解绑」。

        · 本机从来没有认领记录 → 替它自动登记一次（存量用户不该因为这次升级
          莫名其妙用不了工具），登记结果就是分片本身。
        · 本机认领过、分片却没了 → 只可能是管理员 ``--kick`` 或换机时被清掉。
          此时**绝不自动重抢**：自动登记会把「解绑」变成一次空操作，账号又能
          在旧机器上跑起来，管理动作就白做了。记为 released，让用户走显式绑定。
        """
        if _claim_of(phone).get("session"):
            return {"state": RELEASED_STATE, "phone": phone}, True
        doc = self._auto_enroll(client, phone)
        return (doc, True) if doc is not None else (None, False)

    def _auto_enroll(self, client, phone: str) -> Optional[dict]:
        """名单里已有、但 OSS 上还没有设备分片：登记本机（一次性）。

        ``prev_hint={}`` 与「不重复回读」都是必需的：调用方刚刚确认过分片不存在，
        再读一次只是白等一个网络往返——首次点击的等待时间就是靠这类重复请求堆出来的。
        """
        if phone in self._enrolling:
            return None        # 同一账号不并发登记；下次刷新还会再来
        self._enrolling.add(phone)
        try:
            ok, _, doc = self._claim(phone, client=client, reason="首次自动登记",
                                     prev_hint={})
            return doc if ok else None
        finally:
            self._enrolling.discard(phone)

    def _start_refresh(self):
        """后台守护线程刷新（同一时刻至多一个在途请求）。"""
        with self._lock:
            if self._inflight:
                return
            self._inflight = True

        def _run():
            try:
                self._fetch()
            finally:
                with self._lock:
                    self._inflight = False
        threading.Thread(target=_run, daemon=True).start()

    def _load_disk_once(self):
        """内存无名单时加载磁盘副本（过期副本丢弃，保持 fail-closed）。"""
        if self._disk_loaded:
            return
        self._disk_loaded = True
        obj = _read_json(_cache_file(), None)
        if not isinstance(obj, dict):
            return
        # 时间戳不可信（缺失/非数/落在将来）时 _stamp_age 给 _STALE，同样走这条
        # return：一份「不知道什么时候写的」副本不足以在断网时撑起完整功能。
        age = _stamp_age(obj.get("fetched_at", 0))
        if age > DISK_MAX_AGE_SECONDS:
            if age == _STALE:
                _note_untrusted_stamp("本地权限名单副本")
            return
        accounts = [a for a in _parse_accounts(obj) if a]
        if not accounts:
            return
        grants, declared = _parse_grants(obj)
        # 磁盘副本里存了当时的「有没有等级字段」标记，照原样读回来：
        # 旧版名单解析出的 grants 是空 dict，重读时会被当成「声明过等级」，
        # 于是所有人都从全功能掉到受限版——标记落盘才是唯一的解法。
        if "grants_declared" in obj:
            declared = bool(obj.get("grants_declared"))
        with self._lock:
            if self._accounts is None:
                self._accounts = accounts
                self._grants = grants
                self._grants_declared = declared

    @staticmethod
    def _write_disk(payload: dict):
        data = dict(payload)
        data["fetched_at"] = time.time()
        _write_json(_cache_file(), data)

    # ── 等级与设备状态的回填（唯一写 edition 的地方） ──

    def _evaluate(self, phone: str):
        """把「等级 → edition 能力上限」和「本机是否在当前会话」算出来。

        等级回填必须发生在这里，而不是界面上：界面只问 ``edition.has(cap)``，
        让它自己去查名单就把权限模型拆成了两半。
        """
        with self._lock:
            accounts = self._accounts
            grants = dict(self._grants)
            declared = self._grants_declared
        phone = phone or self._bound_phone()
        if accounts and phone in accounts:
            level = resolve_level(phone, grants, declared)
            _ed.set_account_level(level, phone,
                                  reason=f"白名单等级 {level}（{LEVEL_NAMES.get(level)}）")
        else:
            # 不在名单 / 未绑定：显式钉到最保守的丐版等级，**不能留在「未判定」**。
            # 未判定在 pro 构建下会被 effective_level() 默认成管理员 = 全套入口，
            # 于是「管理员运行中把某号删掉 → 下一次心跳拉回名单该号消失 → 界面与
            # 后台静默能力（不看按钮点击的同步上传）反而被打开」——正是本功能要堵的
            # 「被删除的账号还能使用」。运行中被移出名单，就地收到丐版。
            _ed.set_account_level(LEVEL_LITE, phone,
                                  reason="已移出白名单或未绑定，按丐版收口")

        binding = self._binding_for(phone, use_fallback=False)
        if binding is None:
            return                      # 设备状态还没确认：不报顶号，也不撤销已报状态
        state = str(binding.get("state") or "")
        if state == RELEASED_STATE:
            # 被解绑不是「被顶号」：不写 kicked_ts（否则用户连重新绑定都要干等
            # 一分钟），也不发顶号事件，只让 check() 把话说明白
            with self._lock:
                self._revoked_latched = False
            return
        if str(binding.get("device_id") or "") != device_id():
            with self._lock:
                already = self._revoked_latched
                self._revoked_latched = True
            if not already and _claim_of(phone).get("session"):
                # 只有「本机曾经拥有过这个会话又被别人接管」才记成被顶号并进入
                # 重新认领冷却；第一次在新设备上点绑定的正常用户不该被冷却卡住
                _note_kicked(phone)
                with self._lock:
                    self._revoked = _revoked_reason(binding, True)
        else:
            with self._lock:
                self._revoked_latched = False
                self._revoked = ""

    def _binding_for(self, phone: str, use_fallback: bool = True) -> Optional[dict]:
        """该账号的设备判定结果：内存里的远端分片优先，本地认领记录只兜底。

        ``use_fallback=False`` 给 ``_evaluate`` 用：它要知道「远端到底怎么说」，
        拿本地记录冒充自己会永远看不见被顶号。
        """
        with self._lock:
            binding, binding_phone = self._binding, self._binding_phone
        if binding is not None and binding_phone == phone:
            # 远端说过的话（包括「分片不存在」= released）一律优先，
            # 绝不让本地记录把管理动作冲掉
            return binding
        if not use_fallback:
            return None
        return self._local_binding_fallback(phone)

    def _local_binding_fallback(self, phone: str) -> Optional[dict]:
        """OSS 读不到绑定分片时，用本机认领记录顶一阵（与名单副本同一套可用性思路）。

        只在**网络失败**时生效，且限定 7 天内的认领记录：被顶掉的设备一旦能联网，
        最迟 60 秒就会被判掉线；断网期间它继续干活，与「断网期间已授权用户不受影响」
        这条既有口径一致。
        """
        rec = _claim_of(phone)
        if not rec.get("session"):
            return None
        age = _stamp_age(rec.get("claimed_ts"))
        if age > DISK_MAX_AGE_SECONDS:
            if age == _STALE:
                _note_untrusted_stamp("本机认领记录")
            return None
        if str(rec.get("device_id") or "") != device_id():
            return None
        return {"phone": phone, "device_id": rec["device_id"],
                "session": rec["session"], "generation": rec.get("generation"),
                "bound_at": rec.get("claimed_at"), "device_label": device_label(),
                "_local": True}

    # ── 判定入口 ──

    def check(self, wait: bool = True) -> Tuple[bool, str]:
        """三重验证：①已绑定云展账号 ②账号在白名单内（并按等级收敛能力）
        ③本机是该账号当前的绑定设备。

        返回 (是否放行, 拒绝原因文案)。本函数只在 UI 线程被点击路径调用，
        内部绝不长时间阻塞（唯一例外是需要出网确认名单/设备状态时的有界等待 3 秒，
        与冷启动同一套口径）。

        ``wait=False`` 给**徽章与心跳**这类"顺便看一眼"的调用方：判定结果一样，
        但没查到就立刻返回拒绝文案，绝不代替用户把 UI 线程按住 3 秒等网络。
        把这类只读展示接到阻塞路径上，代价是「网络一抖，界面跟着卡」——
        而它展示的还不是用户点出来的东西。
        """
        try:
            return self._check(wait=wait)
        except Exception:
            # 守卫自身异常绝不退化为「放行」：判不出来就是不允许
            return False, "权限校验异常，请重试；仍失败请联系管理员"

    def _check(self, wait: bool = True) -> Tuple[bool, str]:
        phone = self._bound_phone()
        if not phone:
            return False, "请先绑定云展动力账号"
        if not _MOBILE_RE.match(phone):
            return False, "绑定的云展账号格式有误，请重新绑定"

        with self._lock:
            accounts = self._accounts
            fresh = _mono_age(self._fetched_at) < self._ttl
        if accounts is None:
            self._load_disk_once()
            with self._lock:
                accounts = self._accounts
        if accounts is None:
            # 冷启动：有界等待一次拉取结果，超时先拒绝（不弹窗报错，下次点击重试）
            self._start_refresh()
            if wait:
                deadline = time.monotonic() + FIRST_FETCH_WAIT_SECONDS
                while time.monotonic() < deadline:
                    time.sleep(0.05)
                    with self._lock:
                        accounts = self._accounts
                    if accounts is not None:
                        break
        if accounts is None:
            # 按故障分类给一句话：过去这里写死「请检查网络」，于是「安装包没带
            # 密钥」这种必然失败的情况也会让用户去查路由器（2026-11 外部机器
            # 全员丐版就是这样被误诊成网络问题的）。
            return False, self._unreachable_reason()
        if not fresh:
            # ``fresh`` 是函数开头取的旧值：冷启动那一次有界等待**本身就是这次刷新**，
            # 不再重读就会紧接着多起一轮「名单+分片」——每次开机第一下点击都要跑两遍，
            # 更麻烦的是两轮并发写同一份判定结果，后收尾的那轮会拿刚读到的旧结论把
            # 前一轮刚落下的顶号事件清掉（离线测试实机复现过）。
            with self._lock:
                fresh = _mono_age(self._fetched_at) < self._ttl
        if not fresh:
            self._start_refresh()
        if phone not in {a for a in accounts}:
            # 与 _evaluate 同一口径：被移出白名单就显式收到丐版，不留「未判定」
            # （未判定在 pro 构建下会被默认成管理员 = 全套入口，等于放行）。
            _ed.set_account_level(LEVEL_LITE, phone,
                                  reason="不在白名单，按丐版收口")
            return False, "当前账号无使用权限，请联系管理员获得使用权限"

        # 等级 → edition 能力上限。放在设备判定**之前**回填：被顶掉的机器、
        # 或者还没联网确认设备的机器，界面也应该按它自己的等级投影，
        # 而不是停在全功能形态。
        self._evaluate(phone)

        # 第三重：本机必须是该账号当前的绑定设备
        with self._lock:
            bind_fresh = _mono_age(self._bind_at) < self._ttl
        binding = self._binding_for(phone)
        if binding is None and not bind_fresh:
            # 设备状态一次都没出网确认过：有界等一次刷新，等不到就下轮再来
            self._start_refresh()
            if wait:
                deadline = time.monotonic() + FIRST_FETCH_WAIT_SECONDS
                while time.monotonic() < deadline:
                    time.sleep(0.05)
                    binding = self._binding_for(phone)
                    if binding is not None:
                        break
        if binding is None:
            return False, "正在校验本机设备绑定，请稍候重试"
        if str(binding.get("state") or "") == RELEASED_STATE:
            return False, _released_reason()
        if str(binding.get("device_id") or "") != device_id():
            claimed = bool(_claim_of(phone).get("session"))
            with self._lock:
                msg = self._revoked or _revoked_reason(binding, claimed)
            return False, msg

        return True, ""

    def level(self) -> Optional[int]:
        """当前账号权限等级（未判定返回 None）。界面用它，不自己解析名单。"""
        return _ed.account_level()

    def account(self) -> str:
        """当前绑定的云展账号（归一后）。界面只读这个口，不自己碰本地账号文件。"""
        return self._bound_phone()

    def tick(self) -> str:
        """心跳（GUI 每 60 秒调一次）：过期就后台刷新，并交出顶号事件。

        长任务期间用户不会去点按钮，只靠点击路径校验的话，被顶号的机器可以
        继续跑几十分钟——心跳是**保底**，不是全部：它自己只能取到上一轮刷新已经
        落下的事件，本轮刷新的结论要等下一拍才看得到。这段延迟由 ``refreshed``
        信号补掉（刷新一结束就叫醒界面），心跳则负责信号没接上/界面在别的线程
        没能处理时的第二次机会。返回值非空即「本机刚被顶掉」。
        """
        with self._lock:
            fresh = _mono_age(self._fetched_at) < self._ttl
        if not fresh:
            self._start_refresh()
        return self.take_revoked()

    def take_revoked(self) -> str:
        """取出并清空顶号事件（一次性消费，避免反复弹窗）。"""
        with self._lock:
            msg = self._revoked
            self._revoked = ""
        return msg

    def _notify_refreshed(self) -> None:
        """刷新结束时叫醒界面。

        离线运行（无 PyQt5）时 ``refreshed`` 是占位返回的 None，这里就必须安静——
        它是通知，不是判定，绝不许把一次刷新带崩。
        """
        sig = getattr(self, "refreshed", None)
        if sig is None:
            return
        try:
            sig.emit()
        except Exception:
            pass

    def invalidate(self):
        """强制下次 check 重新出网拉取（绑定成功、管理员改名单后调用）。"""
        with self._lock:
            self._fetched_at = _MONO_NEVER
            self._acl_at = _MONO_NEVER
            self._bind_at = _MONO_NEVER

    def refresh_blocking(self, timeout: float = 5.0) -> bool:
        """同步刷新一次（限时）：绑定流程的成功收尾用，确保点完绑定
        立刻得到准确的名单与设备判定结果，而不是等后台线程慢慢刷。"""
        done: List[bool] = []
        lock = threading.Lock()

        def _run():
            ok = self._fetch()
            with lock:
                done.append(ok)
        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join(timeout)
        return bool(done and done[0])

    # ── 设备绑定（单点登录核心） ──

    @staticmethod
    def _bind_key(phone: str) -> str:
        return BIND_KEY_TPL.format(phone=_normalize_account(phone))

    def claim_device(self, phone: str, client=None, force: bool = False,
                     reason: str = "绑定") -> Tuple[bool, str]:
        ok, msg, _ = self._claim(phone, client=client, force=force, reason=reason)
        return ok, msg

    def _claim(self, phone: str, client=None, force: bool = False,
               reason: str = "绑定",
               prev_hint: Optional[dict] = None) -> Tuple[bool, str, Optional[dict]]:
        """把「这个账号 = 这台机器」写到 OSS 上：后写覆盖 = 顶掉前面的设备。

        流程：读远端分片 → 同机则原代续期、异机则代际 +1 → 原子上传 → **写后回读确认**。
        回读不能省：两台机器同时抢注时，谁都想当然认为自己赢了；只有回读才能告诉
        其中一台「你没抢到」，否则两台都会以为自己是在用的那一台。

        ``prev_hint`` 给「调用方已经读过分片」的路径用（自动登记时刚确认过它不存在），
        省掉一次纯重复的网络往返；返回第三个值是回读到的分片本体，让调用方不必
        自己再下载一次。
        """
        phone = _normalize_account(phone)
        if not phone or not _MOBILE_RE.match(phone):
            return False, "手机号格式有误，无法登记设备", None
        own, kind, detail = self._acquire_client(client)
        if own is None:
            # 设备登记失败也必须分类：包里没带密钥时，「请检查网络」会把用户
            # 引去查路由器，而真正要修的是打包工作流（2026-11 外部机器全员丐版）
            self._set_service(kind, detail)
            return False, f"设备登记失败：{self._unreachable_reason()}", None

        from oss_pool_client import OSSPoolError
        if prev_hint is None:
            prev: dict = {}
            try:
                data, _ = own.download_json(self._bind_key(phone))
                if isinstance(data, dict):
                    prev = data
            except OSSPoolError as e:
                if "不存在" not in str(e):
                    return False, "无法读取该账号的绑定状态，请稍后重试", None
            except Exception:
                return False, "无法读取该账号的绑定状态，请稍后重试", None
        else:
            prev = prev_hint if isinstance(prev_hint, dict) else {}

        # device_id()/device_label() 都转调 device_identity()，所以这两个值天然同一快照
        # （模拟设备时一起变模拟，绝不会「指纹是假的、标签还是真实主机名」）
        mine = device_id()
        generation = int(prev.get("generation") or 0)
        if str(prev.get("device_id") or "") == mine and prev.get("session"):
            # 同一台机器重复认领：续期但不涨代际，否则每次启动都把自己刚写的
            # 记录「顶掉」一次，历史里全是自己踩自己
            session = str(prev["session"])
        else:
            left = _reclaim_blocked(phone)
            if left > 0 and not force:
                wait = int(left) + 1
                return False, (f"该账号刚在另一台设备上绑定过；为防止两台设备互相顶号，"
                               f"请 {wait} 秒后再认领本机，或联系管理员确认设备归属"), None
            generation += 1
            session = uuid.uuid4().hex

        history = [h for h in (prev.get("history") or []) if isinstance(h, dict)]
        if prev.get("device_id"):
            history.append({"device_id": str(prev.get("device_id")),
                            "label": str(prev.get("device_label") or ""),
                            "generation": prev.get("generation"),
                            "bound_at": prev.get("bound_at"),
                            "released_at": _now_text()})
        doc = {"phone": phone, "device_id": mine, "device_label": device_label(),
               "session": session, "generation": generation,
               "bound_at": _now_text(), "claim_reason": reason,
               "history": history[-_HISTORY_MAX:]}
        try:
            own.upload_json_atomic(self._bind_key(phone), doc)
        except Exception as e:
            return False, f"设备登记写入失败：{type(e).__name__}", None

        try:                      # 写后回读确认
            back, _ = own.download_json(self._bind_key(phone))
        except Exception:
            back = None
        if not isinstance(back, dict) or str(back.get("session")) != session:
            got = back if isinstance(back, dict) else {}
            if str(got.get("device_id") or "") != mine:
                who = got.get("device_label") or "未知设备"
                return False, f"本机设备登记被另一台设备抢先（{who}），请稍后重试", None
            return False, "设备登记结果校验失败，请稍后重试", None

        _save_claim(phone, back.get("generation", generation), session)
        with self._lock:
            self._binding = back
            self._binding_phone = phone
            self._bind_at = time.monotonic()
            self._fetched_at = min(self._acl_at, self._bind_at)
            self._revoked = ""
            self._revoked_latched = False
        return True, "本机已登记为该账号的当前使用设备", back

    def unbind_self(self, phone: str) -> None:
        """本机主动放弃认领（换机前的礼貌动作，不做也不影响安全边界）。"""
        claims = _load_claims()
        rec = dict(claims.get(phone) or {})
        rec["released_at"] = _now_text()
        claims[phone] = rec
        _write_json(_claim_file(), claims)

    # ── 管理入口（写 OSS） ──

    @classmethod
    def load_document(cls, client=None) -> Tuple[dict, int]:
        """读名单文档（不存在则播种），返回 (规范化后的文档, version)。

        规范化 = accounts 与 grants 都算好、并附带 ``_grants_declared`` 标记，
        调用方拿到的一定是可以直接读写的结构，不必各自再解析一遍原始 JSON。
        """
        from oss_pool_client import OSSPoolError
        own = cls._must_client(client)
        try:
            doc, _ = own.download_json(ACL_KEY)
        except OSSPoolError as e:
            if "不存在" not in str(e):
                raise
            doc = _new_document(DEFAULT_GRANTS)
            own.upload_json_atomic(ACL_KEY, doc)
        if not isinstance(doc, dict):
            doc = _new_document(DEFAULT_GRANTS)
        grants, declared = _parse_grants(doc)
        accounts = _parse_accounts(doc)
        for a in grants:                   # grants 里写了但 accounts 漏了的号也要认
            if a not in accounts:
                accounts.append(a)
        return ({"accounts": accounts, "grants": grants, "schema": ACL_SCHEMA,
                 "updated_at": doc.get("updated_at") or "", "_grants_declared": declared,
                 "version": int(doc.get("version") or 0)},
                int(doc.get("version") or 0))

    @classmethod
    def _put_document(cls, doc: dict, grants: Dict[str, int],
                      extra_accounts: Optional[List[str]] = None,
                      drop_accounts: Optional[List[str]] = None, client=None) -> dict:
        """合并写回：version 单调 +1，accounts 与 grants 同步生成。

        历史实现把 version 硬编码成 2，改多少次都是 2——那样这份文件就没有
        「第几版」的意义，出了问题也说不清是谁在哪一次编辑上改坏的。

        ``drop_accounts`` 是删除账号用的：只从 accounts 里减不够，因为 grants 也会被
        并进来，「删掉的号还留在 grants 里」等于没删。所以最终集合一律再减一次。
        """
        own = cls._must_client(client)
        version = int(doc.get("version") or 0)
        drop = {_normalize_account(a) for a in (drop_accounts or [])}
        accounts = sorted((set(doc.get("accounts") or []) | set(grants)
                           | set(extra_accounts or [])) - drop)
        new_doc = {"accounts": accounts, "grants": {k: v for k, v in grants.items()
                                                   if k not in drop},
                   "schema": ACL_SCHEMA, "updated_at": _now_text(),
                   "version": max(version, 1) + 1}
        own.upload_json_atomic(ACL_KEY, new_doc)
        return new_doc

    @classmethod
    def add_accounts(cls, entries: List[str], client=None) -> dict:
        """把账号加入白名单，支持 ``手机号`` 或 ``手机号=等级`` 两种写法。

        只给号码不给等级时：已有等级沿用，新号取最保守那一档——管理员忘了写
        等级，后果是「这个号先按受限版用」，而不是「这个号把内部功能全看到了」。
        """
        own = cls._must_client(client)
        doc, version = cls.load_document(own)
        grants = dict(doc["grants"])
        wanted: Dict[str, int] = {}
        for raw in entries:
            phone, sep, lvl = str(raw or "").partition("=")
            phone = _normalize_account(phone)
            if not _MOBILE_RE.match(phone):
                raise ValueError(f"手机号格式有误: {phone}")
            if sep and lvl.strip():
                lv = _parse_level_text(lvl, raw)
            else:
                lv = grants.get(phone, _ed.UNKNOWN_ACCOUNT_LEVEL)
            wanted[phone] = lv
        grants.update(wanted)
        new_doc = cls._put_document(doc, grants, client=own)
        cls._sync_local(own)
        return {"doc": new_doc, "added": sorted(wanted), "grants": grants,
                "version": new_doc["version"]}

    @classmethod
    def set_levels(cls, entries: List[str], client=None) -> dict:
        """批量改等级：``手机号=等级``。号码必须已在名单里（不在就先 --add）。"""
        own = cls._must_client(client)
        doc, _ = cls.load_document(own)
        grants = dict(doc["grants"])
        changed: Dict[str, int] = {}
        for raw in entries:
            phone, sep, lvl = str(raw or "").partition("=")
            phone = _normalize_account(phone)
            if not _MOBILE_RE.match(phone) or not sep:
                raise ValueError(f"写法应为 手机号=等级：{raw}")
            lv = _parse_level_text(lvl, raw)
            if phone not in doc["accounts"] and phone not in grants:
                raise ValueError(f"该号码不在白名单里，请先 --add {phone}={lv}")
            grants[phone] = lv
            changed[phone] = lv
        new_doc = cls._put_document(doc, grants, client=own)
        cls._sync_local(own)
        return {"doc": new_doc, "changed": changed, "grants": grants,
                "version": new_doc["version"]}

    @classmethod
    def remove_accounts(cls, phones: List[str], force: bool = False,
                        client=None) -> dict:
        """把账号从名单里移除（等级记录与设备绑定一起删）。

        没有这一步，「收回权限」就只剩改等级这一种说法：等级 2 仍然能用精简版功能，
        离职的人照样可以拿号干活。

        两道防自我锁死的闸门（不给 ``force`` 就拒绝，因为这两件事的后果操作者
        当场就要吃，而且没有第二次入口可以补救）：
        ① 移除的号码正是**本机当前绑定的号** —— 移除后本机立刻失去权限，
           之后即使把号加回来，本机也仍是「被解绑」状态，得手动重新绑定；
        ② 移完之后名单里**一个管理员都不剩** —— 界面上的权限管理入口从此没人能开，
           只能回 OSS 控制台手工改文件。
        """
        from oss_pool_client import OSSPoolError
        own = cls._must_client(client)
        doc, _ = cls.load_document(own)
        targets: List[str] = []
        for raw in phones:
            phone = _normalize_account(raw)
            if not _MOBILE_RE.match(phone):
                raise ValueError(f"手机号格式有误: {phone}")
            if phone not in doc["accounts"]:
                raise ValueError(f"该号码不在白名单里，无需移除: {phone}")
            if phone not in targets:
                targets.append(phone)
        if not targets:
            raise ValueError("没有给出要移除的号码")
        self_phone = get_guard()._bound_phone()
        if not force:
            if self_phone in targets:
                raise ValueError(
                    f"{self_phone} 是本机当前绑定的账号，移除后这台机器会立刻失去权限"
                    "（重新加回来也要手动重新绑定）。确认无误请加 --force")
            rest_admins = [a for a in doc["accounts"] if a not in targets
                           and resolve_level(a, doc["grants"], doc["_grants_declared"])
                           == LEVEL_ADMIN]
            if not rest_admins:
                raise ValueError(
                    "移除后名单里不再有任何管理员账号，界面上的「权限管理」将没人能打开。"
                    "确认无误请加 --force")
        notes: List[str] = []
        for phone in targets:
            try:
                own.delete_key(cls._bind_key(phone))
            except OSSPoolError as e:
                if "不存在" not in str(e):
                    notes.append(f"{phone} 的设备记录没删掉（{str(e)[:60]}）")
            except Exception as e:
                notes.append(f"{phone} 的设备记录没删掉（{type(e).__name__}）")
        new_doc = cls._put_document(doc, dict(doc["grants"]), drop_accounts=targets,
                                    client=own)
        cls._sync_local(own)
        return {"doc": new_doc, "removed": sorted(targets),
                "grants": new_doc["grants"], "version": new_doc["version"],
                "notes": notes}

    @staticmethod
    def _sync_local(client=None) -> "AccessGuard":
        """写完 OSS 立刻让本机看到新值（回读刷新单例内存与磁盘副本）。"""
        guard = get_guard()
        guard.invalidate()
        guard.refresh_blocking(timeout=8.0)
        return guard

    @classmethod
    def describe_levels(cls, doc: Optional[dict] = None,
                        phones: Optional[List[str]] = None,
                        client=None) -> Tuple[Dict[str, int], bool]:
        """按**唯一口径**算出每个号的等级，并带上「名单有没有声明过等级字段」。

        界面（权限面板）要显示等级列、命令行要打印等级、判定链路要生效等级——三处
        都必须走这里。否则「旧名单没等级字段取 1、有字段但缺这个号取 2」这句口径就会
        在三个地方各写一遍，而三遍的结局一定是哪天不一致。
        """
        if doc is None:
            doc, _ = cls.load_document(client)
        declared = bool(doc.get("_grants_declared", True))
        grants = doc.get("grants") or {}
        wanted = phones if phones is not None else (doc.get("accounts") or [])
        return ({str(p): resolve_level(_normalize_account(p), grants, declared)
                 for p in wanted}, declared)

    @classmethod
    def ensure_whitelist(cls) -> List[str]:
        """确保 OSS 白名单存在并返回号码列表。

        保留这个名字与返回类型是给旧调用方与既有测试的兼容层。
        """
        doc, _ = cls.load_document()
        return doc["accounts"]

    @classmethod
    def list_devices(cls, phones: Optional[List[str]] = None, client=None) -> List[dict]:
        """列出各账号当前绑定的设备（管理员查「谁在多处用同一个号」）。"""
        from oss_pool_client import OSSPoolError
        own = cls._must_client(client)
        if phones is None:
            phones = cls.load_document(own)[0]["accounts"]
        out: List[dict] = []
        for phone in phones:
            phone = _normalize_account(phone)
            item = {"phone": phone, "bound": False, "device_id": "",
                    "device_label": "", "generation": 0, "bound_at": "",
                    "history": [], "is_self": False, "error": ""}
            try:
                data, _ = own.download_json(cls._bind_key(phone))
            except OSSPoolError as e:
                if "不存在" not in str(e):
                    item["error"] = str(e)[:120]
                out.append(item)
                continue
            except Exception as e:
                item["error"] = f"{type(e).__name__}"
                out.append(item)
                continue
            if isinstance(data, dict):
                item.update({"bound": True,
                             "device_id": str(data.get("device_id") or ""),
                             "device_label": str(data.get("device_label") or ""),
                             "generation": int(data.get("generation") or 0),
                             "bound_at": str(data.get("bound_at") or ""),
                             "history": [h for h in (data.get("history") or [])
                                         if isinstance(h, dict)]})
                item["is_self"] = item["device_id"] == device_id()
            out.append(item)
        return out

    @classmethod
    def kick(cls, phone: str, client=None) -> bool:
        """强制解绑：删掉绑定分片，该账号**所有**设备立刻掉线，需重新登记认领。

        删掉而不是改写，是为了让被踢的机器不会把「分片还在、只是换了机器」
        误读成自己的会话；分片不存在会走自动登记分支，语义最干净。
        """
        own = cls._must_client(client)
        own.delete_key(cls._bind_key(phone))
        get_guard().invalidate()
        return True

    @classmethod
    def whoami(cls, client=None) -> dict:
        """本机视角：我是谁、绑了哪个号、远端认为这个号在谁手上。"""
        guard = get_guard()
        phone = guard._bound_phone()
        ident = device_identity()
        info = {"phone": phone, "device_id": ident["device_id"], "label": ident["label"],
                "edition_build": _ed.build_edition(),
                "edition_effective": _ed.edition()}
        info["claim"] = _claim_of(phone) if phone else {}
        try:
            doc, version = cls.load_document(client)
            info["version"] = version
            info["declared"] = doc["_grants_declared"]
            if phone:
                info["level"] = resolve_level(phone, doc["grants"], doc["_grants_declared"])
                info["level_name"] = LEVEL_NAMES.get(info["level"], "")
                info["in_whitelist"] = phone in doc["accounts"]
        except Exception as e:
            info["acl_error"] = str(e)[:120]
        if phone:
            guard.refresh_blocking(timeout=8.0)
            b = guard._binding or {}
            info["remote_device"] = str(b.get("device_label") or "")
            info["remote_generation"] = int(b.get("generation") or 0)
            info["remote_is_self"] = str(b.get("device_id") or "") == info["device_id"]
        return info


def _parse_level_text(text: str, raw: str) -> int:
    """把「等级」文本解析成等级值，非法就报错——绝不在这里默认给高等级。"""
    try:
        lv = int(str(text).strip())
    except (TypeError, ValueError):
        raise ValueError(f"等级必须是 0/1/2（0管理员 1标准 2受限）：{raw}")
    if lv not in _ed.LEVELS:
        raise ValueError(f"等级必须是 0/1/2（0管理员 1标准 2受限）：{raw}")
    return lv


# ── 模块级单例与安装入口 ──

_guard: Optional[AccessGuard] = None


def get_guard() -> AccessGuard:
    global _guard
    if _guard is None:
        _guard = AccessGuard()
    return _guard


def prefetch_local() -> Optional[int]:
    """建界面**之前**用本地副本把等级定下来（绝不出网）。

    没有这一步，主窗口就会在「等级未知」的状态下按构建档位摆出全部入口，
    等后台拉完名单才发现这个号只能看受限版——已经建好的按钮收不回去，
    只能提示重启。开机先用本地副本定档，绝大多数情况一次就对。
    """
    guard = get_guard()
    guard._load_disk_once()
    with guard._lock:
        accounts = guard._accounts
        grants = dict(guard._grants)
        declared = guard._grants_declared
    phone = guard._bound_phone()
    if not phone or not accounts or phone not in accounts:
        return None
    level = resolve_level(phone, grants, declared)
    _ed.set_account_level(level, phone, reason=f"本地名单副本等级 {level}")
    return level


def prefetch_authoritative(timeout: float = STARTUP_AUTH_WAIT_SECONDS) -> int:
    """开机建界面**之前**做一次**权威**身份判定：每次都联网查 OSS，据此决定
    第一屏是完整版还是丐版。与 ``prefetch_local``（只读副本、绝不出网）相对。

    为什么要它（对齐用户诉求「打开工具→查身份→显示对应 GUI」）：
    · 副本可能是**上一个还没被删除/降级时的旧等级**。只信副本，被管理员删掉的号
      下次开机照样按副本里的旧等级摆出全套入口——正是「被删除的账号还能使用」。
    · 界面入口在 ``_build_ui`` 一次性按 ``edition`` 建出来，pro 专属入口（数据池四键、
      企知道/风鸟/爱企查补采、权限管理）建的时候就决定有没有，运行时只能隐不能长。
      所以**必须在建界面前**把等级定成权威值，才能让高权限号一次看到完整版。

    判定口径（宁严勿宽，与 ``check()`` 的 fail-closed 同源）：
    · 名单这次**确实从 OSS 拿到了**且绑定号**在里面** → 用真实等级（0/1→完整版，2→丐版）。
      看「名单有没有拿到 + 号在不在」，不看设备判定成功与否：设备没确认只是暂时不能用，
      界面仍应按这个号自己的等级显示（否则管理员一断设备校验就被降成丐版，属于误伤）。
    · 其余一切情况——OSS 连不上/超时、根本没配 OSS、未绑定、号不在名单里——一律按
      **丐版**起界面（显式钉 ``LEVEL_LITE``，而不是留在「未判定」：pro 构建下未判定会被
      ``effective_level()`` 默认成管理员 = 全套入口，那正是要堵的漏）。
    · 因断网先起成丐版的高权限号，联网后由后台心跳/刷新确认真实等级，再走
      ``_acl_sync_level`` 的「横幅 + 一键重启」补齐完整版入口（建好的界面长不出新按钮）。

    返回最终钉到 edition 上的等级（一定是一个具体值，绝不返回 None）。
    """
    guard = get_guard()
    # 权威来源只有这次 refresh_blocking 从 OSS 取回的内存名单：**不读磁盘副本**，
    # 因为副本正是「被删除的号还能开机看到全套入口」的来源。拿不到 = accounts 仍为
    # None = 未确认 = 丐版，这条判据不能掺任何缓存进来的东西。
    try:
        guard.refresh_blocking(timeout=timeout)
    except Exception:
        pass
    with guard._lock:
        accounts = guard._accounts          # None = 这次没能从 OSS 拿到名单
        grants = dict(guard._grants)
        declared = guard._grants_declared
    phone = guard._bound_phone()
    if accounts is None or not phone or phone not in accounts:
        # 定档口径不变（一律丐版，fail-closed 是安全边界），但**原因**必须落到
        # 分类口径上：这条 reason 会进 edition，被横幅/日志原样带出来。过去它写成
        # 「未确认到有效名单」，用户看到的第一屏就是丐版，却没有任何一处告诉他
        # 「这个包压根没带权限凭证」——于是整批外部机器只能靠截图互相猜。
        if accounts is None:
            why = guard._unreachable_reason()
        elif not phone:
            why = "本机还没有绑定云展动力账号"
        else:
            why = f"账号 {phone} 不在本次拉取的名单里"
        _ed.set_account_level(LEVEL_LITE, phone, reason=f"开机按丐版起界面：{why}")
        return LEVEL_LITE
    level = resolve_level(phone, grants, declared)
    _ed.set_account_level(level, phone, reason=f"开机 OSS 权威等级 {level}")
    return level


def install(app) -> AccessGuard:
    """把守卫装到 QApplication 上：全局覆盖主窗口与所有对话框的按钮。"""
    guard = get_guard()
    app.installEventFilter(guard)
    return guard


def shutdown(app=None) -> bool:
    """退出前显式拆掉守卫：摘事件过滤器、断唤醒信号、销毁 QObject。拆过返回 True。

    为什么这一步必须由界面在 ``app.exec_()`` 返回之后、QApplication 销毁之前调用：
    守卫是**模块级全局**持有的 QObject，而模块全局比 main 的帧活得久。解释器收尾时
    QApplication 的 wrapper 先析构，PyQt 在里面会遍历一遍所有还活着的 wrapper 做清理，
    走到这个 C++ 侧已随 app 销毁的对象上就是**退出 SIGSEGV**（线上实测抓到的栈：
    ``dealloc_QApplication → cleanup_qobject → sip_api_get_address``）。用户看到的
    就是「关掉工具弹一次系统崩溃报告」，而且最后一次自动保存可能没落盘。

    顺序只有一个地方能保证：app 还活着的时候把守卫删掉。所以这里不只是放引用
    （别的引用可能还在窗口/信号连接上），而是用 ``sip.delete`` 当场销毁 C++ 对象——
    之后谁再碰这个 Python 对象都会得到明确的 RuntimeError，而不是一片没有堆栈的野内存。
    """
    global _guard
    guard = _guard
    if guard is None:
        return False
    _guard = None
    try:
        target = app
        if target is None:
            try:
                from PyQt5.QtWidgets import QApplication as _QApp
                target = _QApp.instance()
            except Exception:
                target = None
        if target is not None:
            target.removeEventFilter(guard)
    except Exception:
        pass
    try:
        guard.refreshed.disconnect()      # 窗口可能已经先拆了，连接留着就是往死对象排队
    except Exception:
        pass
    try:
        try:
            from PyQt5 import sip      # PyQt5 ≥5.12 把 sip 装在这里；裸 import sip 在
        except Exception:              # 没先导入 PyQt5 的进程里会直接 ImportError
            import sip                 # 老包装/某些发行版只有这一种写法
        sip.delete(guard)
    except Exception:
        pass
    return True


# 挂在类上而不是写在类体里：离线测试要按「窗口层」逐条构造假控件复用这套判定
_EXEMPT_TEXTS = ("绑定", "更换绑定", "停止", "暂停", "继续",
                 "返回", "关闭", "取消", "收起", "展开")


def _is_exempt(self, widget) -> bool:
    text = (widget.text() or "").strip()
    if any(k in text for k in _EXEMPT_TEXTS):
        return True
    # Toast 等自绘小控件上的按钮事件不应触发权限提示
    if widget.property("acl_exempt"):
        return True
    # 【实机死锁教训】豁免必须看到「按钮所在窗口」这一层：绑定对话框的
    # 确认按钮文字是 OK/确定，不在豁免词表里——不在名单的账号点 OK 被拦
    # → 切不了账号、绑不上白名单账号，唯一出路被自己锁死。认证入口与
    # 纯消息弹窗按窗口层整体放行：业务动作的触发点（功能按钮）仍被拦，
    # 放行这两类窗口不构成提权路径。
    try:
        top = widget.window()
        if top is None:
            return False
        if top.metaObject().className() == "QMessageBox":
            # 消息弹窗只负责关闭自己，不执行任何业务操作
            return True
        # 绑定类对话框（含 QInputDialog）是第一重认证入口，内部按钮全可用
        return "绑定" in (top.windowTitle() or "")
    except Exception:
        return False


def eventFilter(self, obj, event):
    try:
        cls_name = obj.metaObject().className()
        if cls_name not in ("QPushButton", "QToolButton"):
            return False
        if event.type() not in (QEvent.MouseButtonPress, QEvent.KeyPress):
            return False
        if event.type() == QEvent.KeyPress and event.key() not in (
                Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            return False
        if not obj.isEnabled() or not obj.isVisible():
            return False
        if self._is_exempt(obj):
            return False
        try:
            allowed, reason = self.check()
        except Exception:
            allowed, reason = False, "权限校验异常，请重试；仍失败请联系管理员"
        if allowed:
            return False
        self._notify(reason, obj)
        return True  # 吞掉这次点击
    except Exception:
        # 守卫自身故障绝不崩宿主；判定环节异常已在内层按拒绝处理
        return False


def _notify(self, reason: str, widget):
    """拒绝提示：非阻塞 Toast 优先，4 秒防抖；无 Toast 能力时降级弹窗。"""
    now = time.monotonic()        # 防抖是「隔了多久」，用单调时钟才不会被改时间卡住
    if now - self._last_toast < _TOAST_INTERVAL:
        return
    self._last_toast = now
    try:
        top = widget.window()
    except Exception:
        top = None
    try:
        from pool_dialogs import _show_toast
        _show_toast(top if top is not None else widget, reason, 'warning')
        return
    except Exception:
        pass
    try:
        from PyQt5.QtWidgets import QMessageBox
        QMessageBox.warning(top, "使用权限", reason)
    except Exception:
        pass


AccessGuard._EXEMPT_TEXTS = _EXEMPT_TEXTS
AccessGuard._is_exempt = _is_exempt
AccessGuard.eventFilter = eventFilter
AccessGuard._notify = _notify


# ── 产物自检（发包门禁）────────────────────────────────────────────────────
# 为什么必须有这一步：本轮「外部机器全员丐版」的根因不是某行代码写错，而是
# 「产物到底能不能连上权限服务」这件事**从来没有被验证过一次**。开发机上恰好
# 能从本机 iOS 工程读到密钥，源码怎么跑都通；打包机上没有那份文件，于是发出去
# 的每个包都缺凭证。在开发机测一百遍也测不出这种缺陷。
# 做法对齐容器/桌面软件交付里的 smoke test：构建完立刻**拿产物自己**做一次真连
# 自检，不通就不许发货。退出码分类，让 CI 与人都能一眼看出该修哪一层。

DOCTOR_EXIT_OK = 0
DOCTOR_EXIT_CREDENTIAL = 2
DOCTOR_EXIT_MODULE = 3
DOCTOR_EXIT_DENIED = 4
DOCTOR_EXIT_NETWORK = 5
DOCTOR_EXIT_OTHER = 6
DOCTOR_EXIT_CLOCK = 7
DOCTOR_EXIT_TLS = 8
DOCTOR_EXIT_PATH = 9

# 一张表同时给出「类别 → 退出码 + 短标签」，退出码表和 CLI 帮助都由它派生：
# 上一轮就吃过「帮助里列了 6 个码、实际有 8 个码」的亏——同一条口径写两处，
# 加一类故障时必然漏一处。现在加一类只需要在这一张表里加一行。
_DOCTOR_KINDS = {
    "credential": (DOCTOR_EXIT_CREDENTIAL, "缺凭证"),
    "module": (DOCTOR_EXIT_MODULE, "缺模块"),
    "denied": (DOCTOR_EXIT_DENIED, "被拒"),
    "network": (DOCTOR_EXIT_NETWORK, "网络"),
    "other": (DOCTOR_EXIT_OTHER, "其它"),
    # 时钟偏差单独一个码：CI 的 runner 时间是同步好的，红在这里基本只可能是
    # 「产物里的自愈逻辑没生效」，与「密钥不对」是两个完全不同的排查方向
    "clock": (DOCTOR_EXIT_CLOCK, "时钟"),
    # 加密连接被拦也单独一个码：CI 上出现它 = 构建环境的证书链/代理与 OSS 不通，
    # 该修的是构建机网络策略，而不是发包用的密钥
    "tls": (DOCTOR_EXIT_TLS, "加密被拦"),
}

_DOCTOR_KIND_CODE = {k: v[0] for k, v in _DOCTOR_KINDS.items()}

# 非服务类（发包级）故障：自检里还有一类门禁问的不是「权限服务连不连得上」，而是
# 「这个包把自己的数据写在会丢的地方没有」。**故意不并进 _DOCTOR_KINDS**：那三张表
# （_UNREACHABLE_TEXT / _REASON_MARKERS / SERVICE_NEXT_STEP）说的是服务为什么连不上，
# 把发包缺陷混进去，界面就会把「包打错了」解释成「请检查网络」——本轮丐版事故的形状
# 来源正是这种张冠李戴的归类。但退出码全表必须由两张表**一起**派生：分开列的话就会
# 有人只加常量忘了进表，那种故障在 --doctor 帮助里就没名字（上一轮「帮助只列 6 个码、
# 实际 8 个码」吃的就是这个亏）。
_DOCTOR_NONSERVICE_KINDS = {
    "path": (DOCTOR_EXIT_PATH, "落点违规"),
}

DOCTOR_LOG_NAME = "acl_doctor.txt"

# 自检开关的两个写法必须是**同一份**：CI 工作流里写的是 --acl-doctor（历史命名，
# 报告话术也叫 acl-doctor），管理员手敲源码 CLI 时惯用 --doctor。谁认不出谁的
# 写法都会出事——打包态更狠：exe 认不出 --doctor 就当普通启动去建界面，CI 的
# Start-Process -Wait 会一路干等到 job 超时。
DOCTOR_FLAGS = ("--doctor", "--acl-doctor")

# 退出码全表（含 0）：CLI 帮助、测试与排查手册都读它，避免「文档只列了 6 个码」。
# 两张分类表一起派生（理由见 :data:`_DOCTOR_NONSERVICE_KINDS` 的注释）。
_DOCTOR_EXIT_TABLE = ([(DOCTOR_EXIT_OK, "通过")]
                      + sorted({**_DOCTOR_KINDS, **_DOCTOR_NONSERVICE_KINDS}.values(),
                               key=lambda v: v[0]))
DOCTOR_EXIT_HELP = ("产物自检：只读地验证凭证链、权限服务真连、采集通道完整性与落点纪律，"
                    "退出码按故障分类（"
                    + " / ".join(f"{code} {label}"
                                 for code, label in _DOCTOR_EXIT_TABLE) + "）")


def _channel_integrity(frozen: bool) -> Tuple[int, List[str]]:
    """采集通道完整性：包里少了风鸟/爱企查的采集器，界面上就永远只有企知道一路。

    这一条与权限无关，却和「外部机器一律精简版」是同一类发包缺陷，而且是它的
    **更难查的兄弟**：2026-11-12 实测，等级 0 的管理员账号在产物里只看到企知道，
    而当时的产物自检全绿——因为它只验凭证链与名单可读，没人验「功能模块在不在包里」。
    少一路的症状在界面上和「档位没给这个功能」长得一模一样，用户只会来问权限。

    判据取自 ``site_adapters`` 那张表：模块的**使用者**自己声明运行时要去哪几个路径
    找这些 .py，自检与打包硬门都读它，不在这里另抄一份清单（抄一份就会漏改一处）。

    口径：打包态缺一路就是发包失败（退出码 3 = 缺模块，与「access_guard 没进包」同类）；
    源码态只警告不判红——开发机可能故意没装 selenium，那既不是发包缺陷，也不该把
    本地回归和 CI 前置步一起拖成红灯。
    """
    lines: List[str] = []
    try:
        import site_adapters as _sa
    except Exception as e:
        why = f"{type(e).__name__}: {str(e).splitlines()[0][:160]}"
        lines.append(f"⚠️ acl-doctor：采集通道模块（site_adapters）加载失败 —— {why}")
        if frozen:
            return DOCTOR_EXIT_MODULE, lines
        return DOCTOR_EXIT_OK, lines
    try:
        missing = tuple(_sa.missing_list_channels())
        errors = dict(getattr(_sa, "ADAPTER_ERRORS", {}) or {})
        expected = tuple(getattr(_sa, "EXPECTED_LIST_CHANNELS", ()))
        kw_missing = tuple(_sa.missing_keyword_channels())
        kw_expected = tuple(getattr(_sa, "EXPECTED_KEYWORD_CHANNELS", ()))
    except Exception as e:
        lines.append(f"⚠️ acl-doctor：采集通道完整性判据取不到（{type(e).__name__}: {e}）")
        return DOCTOR_EXIT_OK, lines
    # 按 slug 取中文名（不是按位置）：位置映射在 EXPECTED_LIST_CHANNELS 增删一次就会
    # 全体错位，而这张表只是给人看的报告文案，绝不该把「风鸟」说成「爱企查」。
    # 新加的通道没登记名字时退回 slug 本身，宁可生僻也不张冠李戴。
    names = {"qzd": "企知道", "riskbird": "风鸟", "aiqicha": "爱企查"}
    kw_names = dict(getattr(_sa, "KEYWORD_CHANNEL_NAMES", {}) or {})
    if not missing and not kw_missing:
        lines.append(f"   · 采集通道：{len(expected)} 路齐全（"
                     + " / ".join(names.get(s, s) for s in expected) + "）")
        # 关键词那几路也一并打印：外部目录装载的模块，「在不在包里」只有自检说得出话。
        lines.append(f"   · 关键词通道（外部目录）：{len(kw_expected)} 路齐全（"
                     + " / ".join(kw_names.get(s, s) for s in kw_expected) + "）")
        return DOCTOR_EXIT_OK, lines
    lost = " / ".join(names.get(s, s) for s in missing)
    lost_kw = " / ".join(kw_names.get(s, s) for s in kw_missing)
    head = ("❌ acl-doctor：产物里缺采集通道" if frozen
            else "⚠️ acl-doctor：本机缺采集通道（源码态不判红）")
    if lost:
        lines.append(f"{head}：{lost}")
    if lost_kw:
        lines.append(f"{head}（关键词）：{lost_kw}")
    for s in tuple(missing) + tuple(kw_missing):
        why = errors.get(s)
        lines.append(f"   · {s}：{why or '加载结果不可用（原因未登记）'}")
    lines.append("   · 症状：界面上少一路来源（名单只剩企知道、关键词只剩内置站），"
                 "等级 0/1 也一样——这不是权限问题，是那几处的 .py 没进包或没装载起来。")
    lines.append("   · 修法：在打包工作流与 debug/build_b2bcollect_mac_local.sh 的 "
                 "--add-data 里补齐 site_adapters.EXTERNAL_MODULES 列出的文件"
                 "（跑 .github/scripts/check_packaging_manifest.py 会点名缺哪一条；"
                 "它现在还顺 import 走一遍传递依赖，并校验每个进包源码都已提交）。")
    return (DOCTOR_EXIT_MODULE if frozen else DOCTOR_EXIT_OK), lines


def _path_discipline(frozen: bool) -> Tuple[int, List[str]]:
    """落点纪律：打包产物里任何一处落点（候选与实际选中的）都不许在应用包内部。

    为什么这一条要进自检（2026-11-12 全局自审）：Mac 崩溃日志里那句
    ``OSError: [Errno 30] Read-only file system`` 只是「数据写进包里」最响亮的一种
    下场，同一条纪律还有三种不吭气的写法——整包可写时数据被塞进 ``.app``（覆盖升级
    或拖废纸篓就把 Cookie、审计日志、自检报告一起带走）；Windows 写进退出即删的
    ``_MEI`` 目录（＝每次重新登录、每天白烧一遍查询额度）；界面报「已保存」而文件
    下次开机不见了。共同症状是「用户只觉得功能没了」，界面上却查不出任何痕迹。

    判据直接取两份口径模块自己列出的候选链（``state_dir_candidates`` /
    ``candidate_dirs`` / ``config_save_target``），不在这里另抄一份路径清单——抄一份
    就会漏改一处，本轮缺的正是「同一纪律在三处各有实现」里的那一处。

    口径：打包态违规就是发包失败（退出码 9）；源码态只报现状不判红（开发机的数据
    本来就该待在仓库目录里，那是历史 Cookie 与调试产物的正常位置）。
    """
    lines: List[str] = []
    try:
        import tool_config as _tc
        groups: List[Tuple[str, List]] = [
            ("状态目录候选", list(_tc.state_dir_candidates())),
            ("实际状态目录", [_tc.state_dir()]),
        ]
        save_target = _tc.config_save_target()
        if save_target is not None:
            groups.append(("配置保存落点", [save_target]))
    except Exception as e:                      # 口径模块自己读不到：这条门禁弃权
        lines.append(f"   · 落点纪律：判定失败（{type(e).__name__}: {e}）")
        return DOCTOR_EXIT_OK, lines
    try:
        import runtime_paths as _rp
        groups.append(("通道数据候选", list(_rp.candidate_dirs())))
    except Exception:
        pass                                    # 上面 0.6 那段已经如实报过读不到
    bad = [(label, p) for label, paths in groups for p in paths
           if _tc.inside_app_bundle(p)]
    if not bad:
        lines.append("   · 落点纪律：候选与实际落点全在应用包外"
                     + ("（开发态按模块目录，不判红）" if not frozen else ""))
        return DOCTOR_EXIT_OK, lines
    lines.append(("❌ acl-doctor：产物把数据落在应用包内部" if frozen
                  else "⚠️ acl-doctor：数据落在应用包内部（源码态不判红）"))
    for label, p in bad:
        lines.append(f"   · {label}：{p}")
    lines.append("   · 症状：只读卷上启动即崩（Errno 30）；整包可写时不崩，但升级或卸载"
                 "会把 Cookie、审计日志、自检报告一起带走；Windows 写进退出即删的临时目录"
                 "＝登录态与缓存每次归零，界面却照样提示「已保存」。")
    lines.append("   · 修法：按**形态**排除包内（tool_config.inside_app_bundle 与 "
                 "runtime_paths.candidate_dirs），不许等写探针碰运气；离线判据见 "
                 "b2bcollect_build/test_data_dir_offline.py 第 7 节。")
    return (DOCTOR_EXIT_PATH if frozen else DOCTOR_EXIT_OK), lines


def doctor_lines() -> Tuple[int, List[str]]:
    """执行一次自检，返回 ``(退出码, 报告行)``。

    只读不写：绝不在自检里播种名单或改绑定状态——CI 里跑一次自检不应该改变线上
    数据。输出全部走返回值，由调用方决定往哪儿落。
    """
    out: List[str] = []
    code = DOCTOR_EXIT_OK

    def say(line: str = ""):
        out.append(str(line))

    try:
        import tool_config
        frozen = tool_config.is_frozen()
    except Exception as e:                      # 自检模块自己没了也是发包级问题
        return DOCTOR_EXIT_MODULE, [
            "❌ acl-doctor：tool_config 模块缺失",
            f"   · {type(e).__name__}: {e}",
            "   · 说明打包清单没收到这个模块，产物读不到任何配置。",
        ]
    say(f"acl-doctor 形态={'打包' if frozen else '源码'}  "
        f"python={sys.version.split()[0]}  platform={sys.platform}")
    say(f"   · 配置候选：{tool_config.describe()}")

    # 0) 「配了但配不全」这一类：半份密钥比没配更难查——它会让某一级被整级跳过，
    #    而界面上看到的症状和「没配」一模一样。这一行就是为了让它在报告里现形。
    try:
        for w in tool_config.warnings():
            say(f"⚠️ acl-doctor：{w}")
    except Exception:
        pass

    # 0.5) 采集通道完整性：放在**任何网络步骤之前**，因为报告里每一处提前返回都
    #      会跳过后面的段落。这一项不联网、只问模块自己「那几路加载上了没有」，
    #      而它恰恰是本轮真正坏掉的那一层——凭证全通、名单全通、包里却少两个平台。
    ch_code, ch_lines = _channel_integrity(frozen)
    for line in ch_lines:
        say(line)

    # 0.6) 运行形态与落点（2026-11-12 两份实机故障都该由这两行提前说清）：
    #      · Mac 被 Gatekeeper 转置的包整棵树只读，日志里那句 Errno 30 背后就是它；
    #      · 风鸟/爱企查的 Cookie 与缓存落在包外哪一处，决定用户明天还需不需要重新登录、
    #        当天要不要白烧一遍查询额度——「在包内」和「在包外」在界面上完全看不出来。
    #      排在通道完整性之后：那一步已经把 site_adapters（顺带 riskbird_build 路径）
    #      拉进来了，这里才谈得上 import 到口径模块；拉不进来时如实说「读不到」。
    try:
        shape = ("macOS 只读转置包（App Translocation）"
                 if tool_config.app_translocated() else "正常安装位置")
        say(f"   · 运行形态：{shape}  可执行文件={getattr(sys, 'executable', '?')}")
    except Exception as e:
        say(f"   · 运行形态：判定失败（{type(e).__name__}: {e}）")
    try:
        import runtime_paths as _rp
        say(f"   · 名单通道数据目录：{_rp.describe()}")
    except Exception as e:
        say(f"   · 名单通道数据目录：读不到（{type(e).__name__}: {e}）")

    # 0.7) 落点纪律（2026-11-12 全局自审新增的门禁）：上面两行只说「现在落在哪」，
    #      这一条说的是「候选里有没有包内那一级」。两者必须分开报：整包可写的机器上
    #      实际落点看起来正常（候选 1 命中），而包内那一处仍在链上，哪天包旁写不进
    #      （装进 /Applications、被只读挂载、被同步盘接管）就立刻掉进包里。
    path_code, path_lines = _path_discipline(frozen)
    for line in path_lines:
        say(line)

    # 1) 凭证：能不能拿到、从哪一级拿到（只报来源，绝不报值）
    try:
        from oss_pool_client import credential_origin
        origin = credential_origin()
    except Exception as e:
        say(f"   · OSS 凭证来源：无法判定（{type(e).__name__}: {e}）")
        origin = "未知"
    say(f"   · OSS 凭证来源：{origin}")

    client, kind, detail = AccessGuard._client_probe()
    if client is None:
        code = _DOCTOR_KIND_CODE.get(kind, DOCTOR_EXIT_OTHER)
        say(f"❌ acl-doctor：权限服务客户端构造失败（类别={kind}）")
        say(f"   · {detail}")
        say(f"   · 用户侧口径：{AccessGuard._unreachable_text(kind)}")
        say("   · 这一步不通，外部机器就会永远按丐版起界面——不许发货。")
        return code, out
    say(f"   · 客户端可构造：bucket={client.bucket} endpoint={client.endpoint}")

    # 2) 真连一次：读权限名单（只读，绝不播种、绝不改写）
    try:
        doc, _etag = client.download_json(ACL_KEY)
    except Exception as e:
        # 凭证类异常在这里也可能二次出现（构造时没暴露、请求时才炸），统一走
        # _classify_oss_error：它认得出「密钥失效/没权限」与「连不上」的区别
        k, d = AccessGuard._classify_oss_error(e)
        code = _DOCTOR_KIND_CODE.get(k, DOCTOR_EXIT_OTHER)
        say(f"❌ acl-doctor：权限名单读取失败（类别={k}）")
        say(f"   · {type(e).__name__}: {str(e).splitlines()[0][:200]}")
        say(f"   · 用户侧口径：{AccessGuard._unreachable_text(k)}")
        if "不存在" in str(e):
            say("   · 名单文件还没播种过：首次使用会自动建，本次判为通过。")
            # 这是全流程里唯一一个「返回码还没定就提前退出」的成功分支：通道完整性
            # 判红时必须照样退 3，否则「名单还没建 + 包少两个平台」这两件事叠在一起，
            # CI 门禁会拿着 0 直接发一个功能不全的包出去。
            if ch_code != DOCTOR_EXIT_OK:
                say(f"❌ acl-doctor：权限链本项通过，但采集通道完整性不通（退出码 {ch_code}）")
                return ch_code, out
            return DOCTOR_EXIT_OK, out
        return code, out
    accounts = _parse_accounts(doc)
    grants, declared = _parse_grants(doc)
    say(f"   · 名单读到 {len(accounts)} 个账号"
        f"  version={doc.get('version') if isinstance(doc, dict) else '?'}"
        f"  等级字段={'已声明' if declared else '未声明'}")
    if not accounts:
        say("⚠️ acl-doctor：名单是空的，任何人都会被判成丐版（不算发包失败）。")

    # 3) 本机身份链路：绑定号 → 等级 → 设备分片（打包机上没有绑定记录，属正常）
    #    直接读磁盘上的绑定账号，不建守卫单例：自检可能在 QApplication 之前跑
    phone = _normalize_account(AccessGuard._load_bound_account() or "")
    say(f"   · 本机绑定账号：{phone or '（未绑定）'}")
    if phone:
        if phone in accounts:
            lv = resolve_level(phone, grants, declared)
            say(f"   · 该号等级：{lv}（{LEVEL_NAMES.get(lv)}）")
        else:
            say("   · 该号不在名单里 → 本机开机就是丐版（名单问题，不是发包问题）")
        try:
            shard, _ = client.download_json(AccessGuard._bind_key(phone))
            dev = (shard or {}).get("device_id") if isinstance(shard, dict) else None
            same = str(dev or "") == device_id()
            say(f"   · 远端设备：{dev or '（无记录）'}  与本机一致={same}")
        except Exception as e:
            say(f"   · 远端设备记录读不到：{type(e).__name__}: "
                f"{str(e).splitlines()[0][:120]}")

    # 4) 其它同类密钥与可写性：这两项不影响门禁结论，但都是本轮缺陷的同族
    try:
        ds_src = tool_config.source_for("deepseek_api_key")
        say(f"   · DeepSeek 密钥：{ds_src}"
            + ("（未配置 → 扩展词功能会静默降级为纯离线）" if ds_src == "未配置"
               else ""))
    except Exception:
        pass
    try:
        # 时钟校正：OSS 签名对请求时刻敏感，本机时间偏太远会被回 403（形态与密钥
        # 失效一模一样）。客户端会按服务端时间自愈重签，这一行就是把它已经做过的事
        # 如实写出来——没有这行，「校过时差的机器上通、其它机器不通」又是一次盲查。
        from oss_pool_client import clock_offset_text
        say(f"   · 时钟校正：{clock_offset_text()}")
    except Exception:
        pass
    try:
        # 两个目录都要报：三份 .acl_*.json 落在 _state_dir()（按档位投影的缓存目录），
        # 本报告自己落在 tool_config.state_dir()。以前只报后者，等于自检指着
        # 「一个没被用上的地方」说这里没问题——报告说的必须就是实际用的那一个。
        say(f"   · 权限状态目录：{_state_dir()}")
    except Exception as e:
        say(f"⚠️ 权限状态目录不可用：{type(e).__name__}: {e}")
    try:
        say(f"   · 自检报告目录：{tool_config.state_dir()}")
    except Exception as e:
        say(f"⚠️ 自检报告目录不可用：{type(e).__name__}: {e}")
    # 本地状态读写的问题（目录不可写、落盘失败）：不影响门禁结论——凭证与网络都通
    # 的包发出去是能用的，但这些问题必须在这一份报告里看得见，否则下次远程排障
    # 又是「用户说不好用、开发者说我这头全绿」。
    try:
        for p in state_problems():
            say(f"⚠️ acl-doctor：{p}")
    except Exception:
        pass

    # 权限链每一层都通了，功能模块却少了两路——同样是「发出去就不能用」，退出码
    # 必须跟着改。反过来若前面已经因为凭证/网络判红，那个码更该先看，通道缺失的
    # 结论已经写在上面那几行里，不会被淹没。（落点纪律同理：它是第三类「前面全通、
    # 发出去照样出事」的缺陷，报告里已经逐条点名，这里只补退出码。）
    if code == DOCTOR_EXIT_OK and ch_code != DOCTOR_EXIT_OK:
        code = ch_code
    if code == DOCTOR_EXIT_OK and path_code != DOCTOR_EXIT_OK:
        code = path_code

    say("✅ acl-doctor：权限服务连通且名单可读" if code == DOCTOR_EXIT_OK
        else f"❌ acl-doctor：未通过（退出码 {code}）")
    return code, out


def _force_utf8_stdout() -> None:
    """命令行入口的第一件事：把控制台输出切到 UTF-8（口径见 tool_config）。

    这一步必须排在**任何打印之前**。Windows 英文系统上 Python 打印中文会抛
    UnicodeEncodeError，而这一层恰恰是**发包门禁与远程排障**用的：报错的是打印，
    被误判的却是权限服务——「工具说自己连不上」和「工具没法把自己说的话写到屏幕上」
    在日志里长得一模一样。本轮 CI 就是这样：config.json 已经写好了，进程却死在
    打印 ✅ 的那一句上。

    安静失败：tool_config 没被收进打包清单时（这才是门禁要抓的故障），绝不能让
    「修编码」这件事把它自己的诊断也一起弄丢，所以这里不抛、也不打印。
    """
    try:
        import tool_config
        tool_config.force_utf8_stdout()
    except Exception:
        pass


def run_doctor(write_log: bool = True) -> int:
    """打印自检报告并返回退出码。

    ``write_log``：打包成 ``--windowed`` 的产物在 Windows 上**没有控制台**
    （``sys.stdout`` 是 None，``print`` 会静默什么也不做），所以报告除了打屏还
    必须落一份到包外的状态目录，CI 与远程排障才有东西可看。
    """
    _force_utf8_stdout()
    code, lines = doctor_lines()
    text = "\n".join(lines)
    printed = False
    try:
        if sys.stdout is not None:
            print(text)
            printed = True
    except Exception:
        printed = False
    if write_log:
        # 无控制台（Windows --windowed 产物里 sys.stdout 是 None，print 静默失效）
        # 时必须另有一处能拿到报告，否则 CI 的门禁只剩一个数字，失败原因永远查不到。
        try:
            import tool_config
            path = tool_config.state_dir() / DOCTOR_LOG_NAME
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text + "\n", encoding="utf-8")
            if not printed:
                try:
                    sys.__stderr__.write(text + "\n")
                except Exception:
                    pass
                print(f"acl-doctor 报告已写入：{path}")
        except Exception as e:
            try:
                print(f"⚠️ acl-doctor 报告落盘失败：{type(e).__name__}: {e}")
            except Exception:
                pass
    return int(code)


# ── 管理员命令行 ──

def _fmt_level(grants: Dict[str, int], declared: bool, phone: str) -> str:
    if not declared:
        return "旧名单未声明等级 → 全功能（无管理入口）"
    lv = grants.get(phone)
    if lv is None:
        return f"{_ed.UNKNOWN_ACCOUNT_LEVEL}（{LEVEL_NAMES[_ed.UNKNOWN_ACCOUNT_LEVEL]}·未声明按最保守）"
    return f"{lv}（{LEVEL_NAMES.get(lv, '?')}）"


def _print_grants(doc: dict):
    grants = doc["grants"]
    declared = doc["_grants_declared"]
    for p in doc["accounts"]:
        print(f"   · {p}  等级 {_fmt_level(grants, declared, p)}")


def _cli(argv: Optional[List[str]] = None):
    """管理员命令行入口：名单 / 等级 / 设备绑定 一体管理。"""
    ap = argparse.ArgumentParser(
        description="账号权限管理（OSS acl/whitelist.json + acl/devices/）")
    ap.add_argument("--add", nargs="*", default=None,
                    help="加账号：手机号 或 手机号=等级（0管理员 1标准 2受限）")
    ap.add_argument("--set-level", nargs="*", default=None,
                    help="改等级：写法 手机号=等级")
    ap.add_argument("--remove", nargs="*", default=None,
                    help="把账号移出白名单（连等级记录和设备绑定一起删）")
    ap.add_argument("--force", action="store_true",
                    help="配合 --remove：明知会移除本机账号/清空管理员也照做")
    ap.add_argument("--show", action="store_true", help="查看名单与等级")
    ap.add_argument("--devices", nargs="*", default=None,
                    help="查看各账号绑定的设备（不带参数=全部）")
    ap.add_argument("--kick", nargs="*", default=None,
                    help="强制解绑（该号所有设备掉线）")
    ap.add_argument("--whoami", action="store_true", help="看本机设备身份与认领状态")
    ap.add_argument("--claim", nargs="?", default=None, const="",
                    help="把本机登记为该账号的当前设备（不带号码=用已绑定账号）")
    ap.add_argument(*DOCTOR_FLAGS, dest="doctor", action="store_true",
                    help=DOCTOR_EXIT_HELP + "（--acl-doctor 是同义写法，与打包工作流、"
                                            "界面产物入口保持一致）")
    args = ap.parse_args(argv)

    # 参数组合校验：--force 只在移除时有意义，别让它默默被忽略（管理员以为生效了）
    if args.force and not args.remove:
        ap.error("--force 需要配合 --remove 一起用")
    # nargs="*" 不带值时 argparse 给的是**空列表**，而下面全按真值分派：
    # "python -m access_guard --add" 会一路掉到 else，什么都没改却打印一份名单，
    # 管理员看到名单就以为加成功了。带旗标必须同时带参数，当场报错才算说清楚。
    # （--devices 是唯一例外：不带号码=看全部，是有意设计的默认。）
    for _flag, _val in (("--add", args.add), ("--set-level", args.set_level),
                        ("--remove", args.remove), ("--kick", args.kick)):
        if _val is not None and not _val:
            ap.error(f"{_flag} 至少要带一个参数")

    if args.doctor:
        # 自检是**只读**动作，和任何写操作混在一起时结论就不可信了（比如同一次
        # 调用里先改了名单，自检读到的就是刚写下去的东西），所以必须单独跑。
        if (args.add or args.set_level or args.remove or args.kick
                or args.claim is not None):
            ap.error("、".join(DOCTOR_FLAGS) + " 只读，不能与 "
                     "--add/--set-level/--remove/--kick/--claim 同时使用")
        code = run_doctor()
        if code != DOCTOR_EXIT_OK:
            sys.exit(code)
        return

    if args.add:
        res = AccessGuard.add_accounts(args.add)
        print(f"✅ 已写入白名单（version {res['version']}），本次加入/更新："
              f"{'、'.join(res['added'])}")
        for p in res["added"]:
            lv = res["grants"][p]
            print(f"   · {p} → {lv}（{LEVEL_NAMES.get(lv)}）")
    elif args.set_level:
        res = AccessGuard.set_levels(args.set_level)
        print(f"✅ 等级已更新（version {res['version']}）")
        for p, lv in sorted(res["changed"].items()):
            print(f"   · {p} → {lv}（{LEVEL_NAMES.get(lv)}）")
    elif args.remove:
        res = AccessGuard.remove_accounts(args.remove, force=args.force)
        print(f"✅ 已移出白名单（version {res['version']}）："
              f"{'、'.join(res['removed'])}（等级记录与设备绑定一并删除）")
        for n in res["notes"]:
            print(f"   ⚠ {n}")
        print(f"   剩余 {len(res['doc']['accounts'])} 个账号")
    elif args.devices is not None:
        phones = args.devices or None      # 空列表=没带号码=看全部
        rows = AccessGuard.list_devices(phones)
        print(f"绑定设备（共 {len(rows)} 个账号）：")
        for r in rows:
            if not r["bound"]:
                print(f"   · {r['phone']}  未登记设备"
                      + (f"（{r['error']}）" if r["error"] else ""))
                continue
            tag = "  ← 本机" if r["is_self"] else ""
            print(f"   · {r['phone']}  {r['device_label']}"
                  f"  gen={r['generation']}  {r['bound_at']}{tag}")
            for h in r["history"][-3:]:
                who = h.get("label") or h.get("device_id")
                print(f"        历史：{who}  gen={h.get('generation')}"
                      f"  解于 {h.get('released_at') or h.get('bound_at')}")
    elif args.kick:
        for p in args.kick:
            AccessGuard.kick(p)
            print(f"✅ 已解绑 {p}（该号所有设备下次校验时掉线，需重新登记认领）")
    elif args.whoami:
        info = AccessGuard.whoami()
        print(f"本机设备：{info['label']}  device_id={info['device_id']}")
        print(f"绑定账号：{info.get('phone') or '（未绑定）'}")
        if info.get("level") is not None:
            print(f"权限等级：{info['level']}（{info.get('level_name')}）"
                  f"  名单 version={info.get('version')}"
                  f"  在名单={'是' if info.get('in_whitelist') else '否'}")
        if info.get("claim"):
            c = info["claim"]
            print(f"本机认领：gen={c.get('generation')}  at={c.get('claimed_at')}")
        if info.get("phone"):
            remote = info.get("remote_device") or "（远端无记录）"
            state = "本机正是当前绑定设备" if info.get("remote_is_self") else "已被其他设备顶掉"
            print(f"远端认定：{remote}  gen={info.get('remote_generation')}  → {state}")
        print(f"生效档位：{info.get('edition_effective')}"
              f"（构建 {info.get('edition_build')}）")
    elif args.claim is not None:
        phone = _normalize_account(args.claim) or get_guard()._bound_phone()
        ok, msg = get_guard().claim_device(phone)
        print(("✅ " if ok else "❌ ") + msg)
        if not ok:
            sys.exit(1)
    else:
        doc, version = AccessGuard.load_document()
        declared = doc["_grants_declared"]
        print(f"白名单 version={version} 共 {len(doc['accounts'])} 个账号"
              f"（等级字段：{'已声明' if declared else '未声明，全部按旧版全功能'}）")
        _print_grants(doc)
        print(f"本机设备：{device_label()}  device_id={device_id()}")
        # 生效档位要如实反映**本机绑定账号**的等级：这份 doc 已经在手，直接本地解析、
        # 不再出网。缺这一步时 --show 会在「本机其实已绑定、等级也拿得到」的情况下仍
        # 打印「等级未判定」——与 --whoami 自相矛盾，管理员据此判断线上状态会被误导。
        _phone = get_guard()._bound_phone()
        if _phone and _phone in doc["accounts"]:
            _lv = resolve_level(_phone, doc["grants"], declared)
            _ed.set_account_level(_lv, _phone, reason=f"--show 本地解析绑定账号等级 {_lv}")
            _tail = f"（绑定账号 {_phone}）"
        else:
            _tail = "（本机未绑定，按构建档位隐含）" if _phone == "" else "（绑定账号不在名单）"
        print(f"生效档位：{_ed.edition()}（构建 {_ed.build_edition()}，"
              f"{_ed.account_level_reason()}）{_tail}")


def _cli_guarded(argv: Optional[List[str]] = None) -> int:
    """CLI 的错误收口：管理员要看到的是人话 + 非零退出码，不是裸 traceback。

    管理动作全是「读名单→改→写回」，中途任何一步失败（号码格式、闸口拒绝、
    网络抖动、OSS 报错）都必须**明确没有生效**：打一句错误并退 1，让脚本和人都
    能据此停手，而不是看到半屏异常后猜「到底写进去没有」。
    """
    _force_utf8_stdout()
    try:
        from oss_pool_client import OSSPoolError as _oss_err
    except Exception:                    # 没装 oss2/依赖时也不该让 CLI 先崩
        _oss_err = ()
    try:
        _cli(argv)
    except (ValueError, RuntimeError, _oss_err) as e:
        print(f"❌ 操作未完成（未写入任何变更）：{type(e).__name__}: {e}",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_cli_guarded())
