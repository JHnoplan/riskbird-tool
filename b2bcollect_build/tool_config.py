# -*- coding: utf-8 -*-
"""tool_config.py —— 随包配置与凭证的唯一解析口径（打包态必须与开发态同权）。

为什么要有这个模块（2026-11 外部机器实机缺陷）：
  OSS 密钥此前按「环境变量 > 模块目录 config.json > 开发机上的 iOS .m 文件绝对路径」
  解析，DeepSeek 密钥按「环境变量 > 模块目录 config.json」解析。云端 PyInstaller
  打出来的包里既没有 config.json（它在 .gitignore 里，仓库里永远不该有真实密钥），
  更不可能有那个只存在于开发机磁盘上的 .m 文件。结果：所有外部机器上权限服务
  永远连不上 → check() 报「无法连接权限服务器，请检查网络后重试」→ 开机一律按丐版
  起界面，完整版功能一个都用不了；DeepSeek 同理静默降级成纯离线。而开发机恰好
  有那个 .m 文件，从第一次打包到现在都测不出来。

  这是同一类缺陷的三个面：①解析链里混进了「只对一台机器成立」的路径；②失败被
  压成「网络问题」，把配置错误伪装成用户侧故障；③没有任何一道闸在发包前证明
  「产物真的能连上权限服务」。本模块只解决①，②③分别在 access_guard 与 workflow 里收。

解析口径（顺序即优先级，越靠前越权威）：
  0. **环境变量**（``OSS_ACCESS_KEY_ID`` / ``OSS_ACCESS_KEY_SECRET`` /
     ``DEEPSEEK_API_KEY``）—— 与下面各级同级对待，见 :data:`ENV_KEY_MAP`；
  1. ``B2B_CONFIG_FILE`` 环境变量指定的文件 —— 排障时手工指定，优先级最高的文件；
  2. **exe 同级**的 config.json —— 不发新版也能就地修好一台机器（企业软件惯例：
     side-by-side 配置覆盖随包配置）；
  3. **随包** config.json（frozen 态取 ``sys._MEIPASS``，开发态取模块目录）；
  4. **用户目录** ``~/.b2bcollect/config.json`` —— 界面上「保存密钥」的落点，
     也是前三级全不可用时的唯一出路（打包态下它是链条里唯一保证可写的一级）；
  5. 开发态额外再看仓库根目录（历史习惯），frozen 态绝不越界去找任何机器专属路径。

**密钥必须成组**（:data:`KEY_GROUPS`）：一把 OSS 密钥的 id 与 secret 是一对，
按键逐级合并会把「新 id + 旧 secret」拼出来——看着两级都有值，实际必然签名不符，
报出来是 403 类的 denied，用户与发包方互相指（跟本轮缺陷同一个形状）。所以成组的
键只在**同一级里凑齐**才算这一级提供了它，缺一半的级整级跳过并记进 warnings。

安全边界：本模块只读键值、只报「命中了哪个文件」，任何日志与返回值都不带出密钥
明文。调用方需要展示来源时一律用 :func:`source_for`。
"""
import json
import os
import plistlib
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

CONFIG_FILE_NAME = "config.json"
CONFIG_FILE_ENV_VAR = "B2B_CONFIG_FILE"
# 用户目录里那一级的容器名（配置与状态共用同一个家，运维只需记一个目录）
USER_CONFIG_DIR_NAME = ".b2bcollect"

# 键 ↔ 环境变量：环境变量与文件各级**同级参与解析**，不是各自在调用方再判一次。
# 为什么要收进这里：散在各调用方的「先读环境变量」会写成两种形状——有的读了两个
# 密钥变量、有的只读一个，于是同一份产物在不同机器上来源不同，自检报告说的和实际
# 用的不是同一级。
ENV_KEY_MAP = {
    "oss_access_key_id": "OSS_ACCESS_KEY_ID",
    "oss_access_key_secret": "OSS_ACCESS_KEY_SECRET",
    "deepseek_api_key": "DEEPSEEK_API_KEY",
}

# 必须同级别凑齐的键组：只在一组里全部出现才算这一级提供了它
KEY_GROUPS = (
    ("OSS 密钥对", ("oss_access_key_id", "oss_access_key_secret")),
)

_ENV_LABEL = "环境变量"

_MODULE_DIR = Path(__file__).resolve().parent


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包态。"""
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path:
    """随包数据所在目录：onefile/onedir 都由 PyInstaller 写在 ``sys._MEIPASS``。

    开发态返回模块目录，这样同一份代码在两种形态下都有「随包配置」这一级候选，
    不必在调用方各写一遍 if frozen。
    """
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(str(base))
    return _MODULE_DIR


def exe_dir() -> Optional[Path]:
    """可执行文件所在目录（打包态）；开发态返回 None。

    刻意不在开发态退化成模块目录：那一级的候选已经由 :func:`bundle_dir` 提供，
    两处都指到同一个目录只会让「side-by-side 覆盖」这条语义变得含混。
    """
    if not is_frozen():
        return None
    try:
        return Path(sys.executable).resolve().parent
    except Exception:
        return None


def _repo_root() -> Optional[Path]:
    """开发态的仓库根（模块目录的上一级），仅为兼容历史上放在根里的配置。"""
    if is_frozen():
        return None
    return _MODULE_DIR.parent


def user_config_file() -> Path:
    """用户级配置文件：``~/.b2bcollect/config.json``。

    它同时是「保存配置」这个动作的落点——exe 同级在 Program Files / 只读卷里
    写不进去，随包目录在打包态根本不可写，只有用户目录这一级**永远可写**，
    所以它必须出现在读取候选链里，否则「界面里填了密钥」会变成填了个寂寞。
    """
    return Path.home() / USER_CONFIG_DIR_NAME / CONFIG_FILE_NAME


def config_candidates() -> List[Tuple[str, Path]]:
    """返回按优先级排好的 (来源标签, 文件路径) 候选，不保证存在。

    标签是给自检与日志看的「命中了哪一级」，绝不含内容。
    """
    out: List[Tuple[str, Path]] = []
    env_file = str(os.environ.get(CONFIG_FILE_ENV_VAR) or "").strip()
    if env_file:
        out.append(("环境变量指定", Path(env_file)))
    ed = exe_dir()
    if ed is not None:
        out.append(("程序同级", ed / CONFIG_FILE_NAME))
    out.append(("随包内置", bundle_dir() / CONFIG_FILE_NAME))
    out.append(("用户目录", user_config_file()))
    root = _repo_root()
    if root is not None:
        out.append(("仓库根目录", root / CONFIG_FILE_NAME))
    return out


def _read(path: Path) -> dict:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _clean(raw) -> dict:
    """把一级里读到原始 dict 收敛成 {键: 值}：空串/纯空白算「没提供」。

    这一条是本轮最难查的缺陷的形状来源——被空串遮掉的密钥看起来和「没配」完全
    一样，但排查方向相反（一个要去补，一个要去删）。
    """
    out: Dict[str, str] = {}
    for k, v in (raw or {}).items():
        if v is None:
            continue
        s = str(v).strip()
        if s:
            out[str(k)] = s
    return out


def _env_level() -> dict:
    out: Dict[str, str] = {}
    for key, var in ENV_KEY_MAP.items():
        v = str(os.environ.get(var) or "").strip()
        if v:
            out[key] = v
    return out


def levels() -> List[Tuple[str, dict]]:
    """按优先级返回 (来源标签, 该级提供的键值) —— 空值与坏文件在这一步就滤掉。

    环境变量排在所有文件之前：它与文件级是同一类东西（都是「这一级给了什么」），
    单独在调用方判一次就等于把同一条链写了两遍，两遍迟早会互相矛盾。
    """
    out: List[Tuple[str, dict]] = [(_ENV_LABEL, _env_level())]
    for label, path in config_candidates():
        if not path.is_file():
            continue
        out.append((label, _clean(_read(path))))
    return out


def _incomplete(label: str, keys: Tuple[str, ...], given: dict):
    """这一级给了组里的几个、缺哪几个（用于 warnings 与整级跳过判断）。"""
    have = [k for k in keys if k in given]
    missing = [k for k in keys if k not in given]
    if have and missing:
        return have, missing
    return None


def load() -> dict:
    """解析各级配置，返回合并结果：高优先级为准，成组的键绝不跨级拼合。

    合并而不是「第一个存在的文件说了算」，是为了让「exe 同级只放一个键来覆盖
    随包配置」这种排障手法可用——只放一个**非成组**键时其余键仍应从下一级取到；
    成组的键（:data:`KEY_GROUPS`）反过来：必须在同一级凑齐，否则这一级整级跳过，
    因为「新 id + 旧 secret」这种拼出来的对儿必然签名不符，比直接没配更难查。
    """
    lv = levels()
    merged: Dict[str, str] = {}
    sources: Dict[str, str] = {}
    warn: List[str] = []

    for group_name, keys in KEY_GROUPS:
        for label, given in lv:
            half = _incomplete(label, keys, given)
            if half:
                warn.append(f"{group_name}在「{label}」这一级只给了 {'、'.join(half[0])}，"
                            f"缺 {'、'.join(half[1])}：密钥必须成对，这一级整级跳过")
            if all(k in given for k in keys):
                for k in keys:
                    merged[k] = given[k]
                    sources[k] = label
                break

    for label, given in lv:
        for k, v in given.items():
            if k in merged:
                continue          # 高优先级已有值，低优先级不覆盖
            merged[k] = v
            sources[k] = label
    _SOURCES.clear()
    _SOURCES.update(sources)
    _WARNINGS[:] = warn
    return merged


_SOURCES: Dict[str, str] = {}
_WARNINGS: List[str] = []


def get(key: str, default: str = "") -> str:
    """取一个字符串配置项（去首尾空白）。缺失返回 ``default``。"""
    v = load().get(key)
    if v is None or str(v).strip() == "":
        return default
    return str(v).strip()


def warnings() -> List[str]:
    """解析发现的「配了但配不全」的级，供自检打印。

    只报键名与所在级，绝不报值。没有这一条，半份配置就会变成静默失败。
    """
    load()
    return list(_WARNINGS)


def source_for(key: str) -> str:
    """某个键命中在哪一级候选（``未配置`` / ``环境变量`` / 文件来源标签）。

    只报来源，不报值：自检与远程排障都要能回答「这台机器的密钥是从哪读到的」。
    每次都重新解析、不缓存来源：来源与值必须出自**同一次**解析。缓存过一次，
    「先问来源、后取值」和「先取值、后问来源」就会给出两套说法（CI 与 GUI 的调用
    顺序本来就不同），而报告说中一个没被用上的级，正是远程排障最费时间的形状。
    """
    load()
    if key in _SOURCES:
        return _SOURCES[key]
    return "未配置"


# ── 可写状态目录（缓存类文件绝不能落在包内）────────────────────────────────
# 打包态的包内目录要么只读（Mac .app 被 Gatekeeper 转置），要么是每次退出就删的
# 临时解压目录（Windows onefile 的 _MEIxxxx）。写在那里等于「缓存永远不生效」，
# 更糟的是只读时会直接抛 OSError。判据与 cdp_base/site_adapters 的持久目录同口径。

_STATE_DIR_ENV_VAR = "B2B_STATE_DIR"

# 持久状态容器的目录名：所有「按文件名落盘的运行期数据」共用这一级（缓存、审计日志、
# 列映射历史、自检报告）。写成常量而不是散落字面量，是为了让 state_file 的退路也能
# 引用同一个名字——退路和主路各写一份时，改一处就会让两条路落到不同目录。
STATE_DIR_NAME = "b2bcollect_state"

# Gatekeeper App Translocation 的路径标记：真实形态是
#   /private/var/folders/<xx>/<yy>/T/AppTranslocation/<UUID>/d/X.app/Contents/MacOS/...
# 这段是系统造的、只有只读挂载才会出现的路径，比「app 在不在 /Applications」硬得多。
_TRANSLOCATION_MARK = "/AppTranslocation/"


def probe_writable(d: Path) -> bool:
    """mkdir 后真写一个探针文件再删掉：目录「存在」与「可写」是两件事。

    对外提供而不是私有：access_guard 判「权限状态能不能落盘」用的是同一个判据，
    两处各写一份迟早对不上。刻意要真写一个文件而不是只看 mkdir 成不成功：桌面上
    被 OneDrive 重定向、被安全软件的文件夹保护拦着时，「目录看着在」和「写得进去」
    是两件事，只判前者会得到一个假阳性结论。
    """
    probe = d / ".write_test"
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except Exception:
        return False


def app_translocated() -> bool:
    """Mac 专属：本工具是不是正被 Gatekeeper 从只读随机路径转置运行。

    判据取自 ``AppTranslocation/<44位 UUID>/d/`` 这一段真实路径，不猜「app 在不在
    /Applications」——只有这一段能证明当前进程真的活在只读挂载上。

    为什么值得单独问一次（2026-11-12 实机崩溃日志）：被隔离过的 .app 双击打开时，
    macOS 把它挂到一个一次性随机目录上运行，包内整棵树**只读**。那时候任何「照着
    __file__ 往包里写东西」的代码都会抛 ``OSError: [Errno 30] Read-only file system``，
    而用户看到的只是「双击没反应」或一个启动错误框。落点策略（:func:`state_dir` 与
    riskbird_build/runtime_paths.py）已经保证自己不写包内，这里再把这个**运行形态**
    说出来：界面与自检报告都要能区分「装好了在用」和「还压在压缩里跑」。
    """
    if not sys.platform.startswith("darwin") or not is_frozen():
        return False
    return _TRANSLOCATION_MARK in str(getattr(sys, "_MEIPASS", "")) or \
        _TRANSLOCATION_MARK in str(getattr(sys, "executable", ""))


def app_bundle_dir() -> Optional[Path]:
    """当前运行中的 ``X.app`` 目录；非 .app 形态、非 Mac、源码态返回 None。"""
    if not sys.platform.startswith("darwin"):
        return None
    ed = exe_dir()
    if ed is None:
        return None
    try:
        for parent in ed.parents:
            if parent.name.endswith(".app"):
                return parent
    except Exception:
        return None
    return None


def _app_sibling_dir() -> Optional[Path]:
    """Mac .app 形态下的「包旁」目录：X.app 同级，用户看得见也写得进去。"""
    app = app_bundle_dir()
    return app.parent if app is not None else None


def inside_app_bundle(path) -> bool:
    """这个路径是不是落在应用包**内部**（Mac 的 ``X.app`` 这一棵树里）。

    落点口径必须按**形态**回答，不能按「写探针会不会碰巧成功」回答：.app 只有在被
    Gatekeeper 转置时整卷才只读；用户从下载目录双击一份**没被隔离**的 app 时，包内
    是写得进去的——那时候「exe 同级」（``Contents/MacOS``）这一级就会把审计日志、
    自检报告、列映射历史统统塞进应用包里。后果不是崩溃，而是三件更难查的事：
      · 覆盖升级 app 时，这些运行期数据跟着一起没了；
      · 拖进废纸篓卸载时，用户几个月的历史一起消失；
      · 哪天这份 app 被转置了，同一份数据会突然从原来的位置「消失」，用户看到的
        就是「登录状态没保住 / 报错日志找不到了」。
    ``riskbird_build/runtime_paths.candidate_dirs`` 早就按形态排除了这一级；本模块
    此前只排除了「随包内置」那一级，两条口径并不一致——同一个工具的两半数据因此
    会落在两个不同目录里（2026-11-12 全局自审发现并修齐）。
    """
    app = app_bundle_dir()
    if app is None:
        return False
    try:
        p = Path(str(path))
        # 两边都 resolve 之后再比：``app_bundle_dir`` 走过 ``resolve()``，而传进来的
        # 候选常常是没走过的那一份（``sys._MEIPASS``、``Path(__file__)`` 的历史值都是
        # 软链名）。macOS 上 ``/var`` 正是 ``/private/var`` 的软链——不统一就会在
        # **App Translocation 的真实现场**把包内判成包外，这条纪律恰好就在最需要它的
        # 那台机器上失效（本轮加产物自检门禁时实测到的，见离线测试 T 节）。
        try:
            p = p.resolve()
        except Exception:
            pass
        return app in p.parents or app == p
    except Exception:
        return False


# ── Mac：把「下载目录里双击打开」变成「从应用程序文件夹打开」────────────────
# Gatekeeper 的 App Translocation 只对「带隔离属性、又不在应用程序文件夹里」的 app
# 生效，处置办法历来只有一个：把 .app 移进「应用程序」再从那里打开。说明书里写这句
# 话，多数人不会去做；界面上给一次点击才做得完（Obsidian、Cursor 这类桌面工具都是
# 「替用户搬一次家」而不是「教用户搬一次家」）。但替人搬家必须有硬边界，越界就会变成
# 损坏用户数据的新缺陷，所以这里把边界写死成四条：
#   · 只在用户自己的几个目录里找（下载/桌面/文稿/家目录，深度一层），绝不全盘搜索；
#   · 只认 **CFBundleIdentifier 与运行中这份完全相同**的那份——名字相同不是证据，
#     同名不同产品在这台机器上太常见了；
#   · 目标位置已经有同名 .app 时绝不动手（无论是别人的软件还是旧版安装），
#     版本不一致也只报现状，不覆盖、不删除；
#   · 只有同卷 rename（瞬时）才由工具代做；跨卷复制、需要管理员授权这类必须有人
#     盯着的情形一律原样退回，交回给访达去做——它有进度条也会问密码，我们自己搬则
#     会把界面卡死十几秒，看起来像崩了。
_APPS_DIR_NAME = "Applications"
_APP_SOURCE_SUBDIRS = ("Downloads", "Desktop", "Documents")


def bundle_id_of(app_dir) -> str:
    """读 ``X.app/Contents/Info.plist`` 里的 CFBundleIdentifier；读不到返回空串。

    空串就是「没法证明」，调用方必须按「不能动手」处理——把它当「都一样」是拿猜测
    当证据，正是本轮要根治的那种「看起来合理、换台机器就错」的判定。
    """
    try:
        with (Path(app_dir) / "Contents" / "Info.plist").open("rb") as fh:
            return str(plistlib.load(fh).get("CFBundleIdentifier") or "")
    except Exception:
        return ""


def bundle_version_of(app_dir) -> str:
    """同一个 Info.plist 里的短版本号；读不到返回空串（用途见 :func:`bundle_id_of`）。"""
    try:
        with (Path(app_dir) / "Contents" / "Info.plist").open("rb") as fh:
            return str(plistlib.load(fh).get("CFBundleShortVersionString") or "")
    except Exception:
        return ""


def _bundle_executable_of(app_dir: Path, fallback: str = "") -> str:
    """可执行文件名：优先按 Info.plist 的 CFBundleExecutable，退回当前进程自己的名字。"""
    try:
        with (app_dir / "Contents" / "Info.plist").open("rb") as fh:
            value = str(plistlib.load(fh).get("CFBundleExecutable") or "")
        if value:
            return value
    except Exception:
        pass
    return fallback


def _applications_dir(home: Path) -> Path:
    """搬家目标：系统「应用程序」优先，写不进去退用户级 ``~/Applications``。

    两级都试过才承认「写不进去」：企业配发、非管理员的 Mac 上 ``/Applications``
    根本不给普通用户写，而 ``~/Applications`` 是 macOS 自己支持的应用程序位置。
    """
    for cand in (Path("/" + _APPS_DIR_NAME), home / _APPS_DIR_NAME):
        if probe_writable(cand):
            return cand
    return Path("/" + _APPS_DIR_NAME)


def _app_source_roots(home: Path) -> List[Path]:
    """搜寻源目录的有界清单：下载/桌面/文稿 + 家目录本身，深度一层。"""
    roots = [home / name for name in _APP_SOURCE_SUBDIRS]
    roots.append(home)
    return roots


def app_migration_plan(app=None, home=None, apps_dir=None,
                       platform=None) -> Dict[str, object]:
    """「把这份 .app 移进应用程序文件夹」这件事的可行性判定（只查，绝不动磁盘）。

    判定与执行分成两个函数，是为了让这条最容易出事的链路可以离线跑真目录验：搬家这个
    动作一旦判错，代价是用户机器上一个坏掉的 app，而它在开发机上永远测不出来（开发机
    早就装在 /Applications 里了，压根不转置）。

    返回 ``{"status", "source", "dest", "dest_exe", "detail"}``。status 取值即界面话术
    的分支，每一种都说得出一句人话；``dest_exe`` 只在 ready 时有值（新位置的可执行文件，
    重启链路直接用它，不需要再去猜 app 内部结构）。

    ``home`` / ``apps_dir`` / ``platform`` 三个入参是给离线测试的：默认取本机，测试传
    合成目录，于是同一条判定在 Linux/Windows 的 CI 上也能按 darwin 口径跑。
    """
    out: Dict[str, object] = {"status": "", "source": None, "dest": None,
                              "dest_exe": None, "detail": ""}
    plat = sys.platform if platform is None else str(platform)
    if not plat.startswith("darwin"):
        out.update(status="not_mac", detail="只有 macOS 才会被隔离成只读副本")
        return out
    app_path = Path(str(app)) if app is not None else app_bundle_dir()
    if app_path is None:
        out.update(status="no_bundle", detail="当前运行的不是一个 .app 包")
        return out
    home_path = Path(home) if home is not None else Path.home()
    apps = Path(apps_dir) if apps_dir is not None else _applications_dir(home_path)
    dest = apps / app_path.name
    out["dest"] = dest

    if _TRANSLOCATION_MARK not in str(app_path):
        # 已经在正常形态下运行：这一步无事可做（界面也不会给出这个按钮）。
        out.update(status="not_translocated", detail="本次不是从只读副本启动的")
        return out

    our_id = bundle_id_of(app_path)
    if dest.exists():
        same_id = bool(our_id) and bundle_id_of(dest) == our_id
        if same_id and bundle_version_of(dest) == bundle_version_of(app_path):
            out.update(status="already_installed",
                       detail=f"「应用程序」里已经有同一份：{dest}")
        else:
            # 同名不同物、或版本对不上：绝不覆盖、绝不删除，交回给访达让用户自己决定
            out.update(status="name_taken",
                       detail=f"「应用程序」里已有一个同名但不同版本/不同产品的包：{dest}")
        return out
    if not our_id:
        out.update(status="unknown_identity",
                   detail="读不到本程序的包标识，无法证明找到的那份就是它")
        return out

    found: List[Path] = []
    seen = set()
    for root in _app_source_roots(home_path):
        cand = root / app_path.name
        try:
            if not cand.is_dir():
                continue
            # 只排掉系统造的只读挂载（转置副本自己就在里面），不排 /Volumes：
            # 从 DMG 直接运行的场景很常见，那份也确实「就是它」——只是搬不动，
            # 由 mover 按跨卷退回，比在这里悄悄判成「找不到」更容易把人指到对的方向。
            if _TRANSLOCATION_MARK in str(cand):
                continue
            if bundle_id_of(cand) != our_id:
                continue
            key = str(cand.resolve())
            if key in seen:
                continue
            seen.add(key)
            found.append(cand)
        except OSError:
            # 家目录里可能挂着坏掉的软链/网络卷：一个候选读不到不影响其它候选，
            # 但绝不能让它在界面上变成「没找到」，也不能让整条判定抛异常。
            continue

    if not found:
        out.update(status="no_source",
                   detail=f"在 {'、'.join(r.name for r in _app_source_roots(home_path))} "
                          f"里没找到同一份 {app_path.name}")
        return out
    if len(found) > 1:
        out.update(status="ambiguous",
                   detail="找到多份同一程序的副本，不替你猜要搬哪一个："
                          + "、".join(str(p) for p in found))
        return out

    src = found[0]
    exe_name = _bundle_executable_of(
        src, fallback=Path(str(sys.executable)).name)
    out.update(status="ready", source=src,
               dest_exe=(dest / "Contents" / "MacOS" / exe_name) if exe_name else None,
               detail="")
    return out


def move_app_bundle(source, dest) -> Tuple[bool, str]:
    """执行 :func:`app_migration_plan` 判好的那一步：同卷 rename，瞬时完成。

    刻意只用 ``os.rename``：同卷改名不复制、不占空间、不留半个副本；跨卷会抛 EXDEV，
    权限不足会抛 EACCES/EPERM —— 这两种都**不该由工具闷头做**（复制几百 MB 会把界面
    卡死，要管理员授权则必须有人输密码），所以原样把原因退回，让界面把人交给访达。
    """
    source, dest = Path(str(source)), Path(str(dest))
    if dest.exists():
        return False, "目标位置已经有一个同名程序，不覆盖它"
    try:
        os.rename(str(source), str(dest))
        return True, ""
    except OSError as e:
        return False, f"{type(e).__name__}: {e}"


def reveal_in_finder(paths) -> bool:
    """在访达里打开这些目录（macOS 的 ``open``）。永不抛异常：这是提醒，不是流程。"""
    if not sys.platform.startswith("darwin"):
        return False
    import subprocess
    ok = False
    for p in [x for x in paths if x]:
        try:
            subprocess.run(["/usr/bin/open", str(p)], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=10)
            ok = True
        except Exception:
            continue
    return ok


def state_dir_candidates(subdir: str = "") -> List[Path]:
    """状态目录的候选链（按优先级，不保证可写、也不保证存在）。

    口径与 ``riskbird_build/runtime_paths.candidate_dirs`` 完全一致，两处必须一起改。
    把「候选链」单独暴露出来的理由：``数据不进应用包`` 这条纪律**不许靠权限碰运气**来
    验证——整包可写的机器上（用户从下载目录双击一份没被 Gatekeeper 隔离的 app），写探针
    在包内那一级会成功，旧实现照样把审计日志与自检报告塞进包里。候选链摊开之后，离线
    测试就能按**形态**逐条断言「一处都不在 .app 内部」，这句话在任何权限组合下都成立。
    """
    name = STATE_DIR_NAME
    if subdir:
        name = os.path.join(name, subdir)
    if not is_frozen():
        return [_MODULE_DIR / subdir] if subdir else [_MODULE_DIR]
    cands: List[Path] = []
    sib = _app_sibling_dir()
    if sib is not None:
        cands.append(sib / name)
    ed = exe_dir()
    # .app 形态下「exe 同级」就是 Contents/MacOS＝包内：按形态排除，
    # 不等写探针去碰运气（口径见 :func:`inside_app_bundle`）。
    if ed is not None and not inside_app_bundle(ed):
        cands.append(ed / name)
    cands.append(Path.home() / USER_CONFIG_DIR_NAME / name)
    import tempfile
    cands.append(Path(tempfile.gettempdir()) / name)
    return cands


def state_dir(subdir: str = "") -> Path:
    """持久可写状态目录（缓存、自检报告这类非会话产物）。

    候选链：环境变量 > 包旁（Mac .app）> exe 同级（.app 形态下这一级就是包内，已按
    形态排除）> 用户目录 ~/.b2bcollect > 系统临时目录；开发态直接用模块目录，避免历史
    缓存搬家。每个候选都过写探针，逐条口径见 :func:`state_dir_candidates`。
    """
    env_dir = str(os.environ.get(_STATE_DIR_ENV_VAR) or "").strip()
    if env_dir:
        return Path(env_dir) / (subdir or "")
    for d in state_dir_candidates(subdir):
        if probe_writable(d):
            return d
    # 全链路都写不进去（极端环境）：仍报用户目录那一级，由调用方的写失败播报去说这件事
    name = STATE_DIR_NAME
    if subdir:
        name = os.path.join(name, subdir)
    return Path.home() / USER_CONFIG_DIR_NAME / name


def user_state_dir() -> Path:
    """用户目录里那一级的状态目录（:func:`state_dir` 的最后一条退路）。

    单独提供是给「tool_config 自己都没加载成功」那种病态包用的退路：那里只能退回
    这个**与本机无关**的固定落点，绝不能退回当前工作目录（见 :func:`state_file`）。
    """
    return Path.home() / USER_CONFIG_DIR_NAME / STATE_DIR_NAME


def state_file(name: str) -> Path:
    """持久状态文件的唯一落点：``state_dir() / name``，并确保父目录存在。

    为什么值得专门有一个函数：历史上有两个默认值写成 ``"audit.log"`` /
    ``"mapping_history.json"``（注释还写着「与程序同目录」），实际是**相对当前工作
    目录**——而打包态的 CWD 由「谁把进程拉起来」决定：访达双击是 ``/``，重启链路是
    exe 同级，命令行则是用户当时站在哪。同一个文件因此可能落到四个地方：Mac 转置
    副本下写 ``/`` 直接 PermissionError（被 except 吞掉＝审计日志静默消失），
    Windows onefile 下写进退出即删的 _MEI 目录（＝每次归零）。落点只许问一处，
    调用方只报文件名——这条纪律由离线测试逐字钉住。
    """
    d = state_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        # 目录建不出来（首选写不进去时 state_dir 已经退到用户目录，走到这里
        # 说明连用户目录都不行）：仍然返回路径，由调用方的写失败播报去说这件事
        pass
    return d / name


def config_save_target() -> Optional[Path]:
    """「保存配置」这一步会落在哪一处（只判定、不落盘；自检与离线测试都问它）。

    单独公开出来有两个理由：
      · 判据只有一份。:func:`save_config` 里那串排除规则如果被自检或测试另抄一遍，
        抄的那一份就会漏改（本轮 Mac 崩溃的同族缺陷正是「一条纪律在三处各有实现」）；
      · 产物自检必须是**只读**的：它要能回答「用户现在点保存会写到哪」，却不能真的
        去改任何一份配置。返回 None 表示链条里没有一处可写落点。
    """
    for label, path in config_candidates():
        try:
            # 打包态的「随包内置」是临时解压目录（Windows onefile 每次退出即删，
            # Mac 转置后只读）：往它里面写等于「保存成功但下次开机没了」，
            # 这一级只许读、不许作为保存落点。
            if label == "随包内置" and is_frozen():
                continue
            # 同一条纪律的另一半：Mac .app 的「程序同级」＝Contents/MacOS，也在包内。
            # 读它可以（多一处兼容形态），写它不行——理由见 :func:`inside_app_bundle`。
            if is_frozen() and inside_app_bundle(path):
                continue
            if path.is_file() and probe_writable(path.parent):
                return path
        except Exception:
            continue
    user = user_config_file()
    return user if probe_writable(user.parent) else None


def save_config(values: dict) -> Tuple[bool, str]:
    """把若干键写进**一个可写**的配置文件，返回 (是否成功, 落点说明)。

    落点选择顺序 = 读取链里「存在且可写」优先、其次用户目录：在 exe 同级已经
    有配置文件的机器上改配置，就应该改那一份（不然会凭空多出第二份互相矛盾的
    配置）；否则写到用户目录这一级。绝不允许「界面点保存 → 静默失败」。
    """
    target = config_save_target()
    if target is None:
        return False, "用户目录不可写，配置未保存"
    cfg: dict = {}
    try:
        if target.is_file():
            cfg = json.loads(target.read_text(encoding="utf-8") or "{}")
            if not isinstance(cfg, dict):
                cfg = {}
    except Exception:
        cfg = {}
    cfg.update({str(k): v for k, v in (values or {}).items()})
    try:
        target.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    except Exception as e:
        return False, f"写入 {target} 失败（{type(e).__name__}）"
    return True, str(target)


def describe() -> str:
    """一行自检摘要：各级候选是否存在（只有文件名，没有内容）。

    运行形态（打包/源码）由调用方自己说明，这里不重复一遍——两处各写一次
    「形态」就会在自检报告里出现两遍，读的人反而怀疑它们不一致。
    环境变量这一级也要列出来：它优先级最高，漏报的话排障的人会去翻文件，
    而真正生效的是别人塞进进程环境的两个变量。
    """
    env = _env_level()
    parts = [f"{_ENV_LABEL}={'有' if env else '无'}({len(env)}项)"]
    parts += [f"{label}={'有' if path.is_file() else '无'}({path.name})"
              for label, path in config_candidates()]
    return " ".join(parts)


# ── 控制台输出编码 ───────────────────────────────────────────────────────────

def force_utf8_stdout() -> bool:
    """把 stdout/stderr 切成 UTF-8（编不出来的字符替换掉），绝不抛异常。

    为什么必须有这一步：Windows 的 Python 用**系统代码页**给控制台编码，英文系统
    就是 cp1252。这条链路上所有给用户看的字都是中文，还带着 ✅ ❌ ⚠ 这类符号，
    在 cp1252 上直接抛 UnicodeEncodeError —— 于是发生本轮 CI 那种最费解的失败：
    `inject_build_config.py` 已经把 config.json 写好了，却在打印成功话术那一刻
    非零退出，构建被自己写的门禁拦在门外（功能成功、进程失败，日志里只看得到
    "codec can't encode character '\u2705'"）。

    换编码是唯一的根治：把 emoji 改成文字只能治这一处，下一句中文字又会撞同一堵
    墙（cp1252 同样编不出「应」）。而 CI、远程排障、用户手跑 CLI 三条路都要打印，
    逐处包 try/except 迟早漏一处。

    只在**能改**的时候改：``--windowed`` 产物里 PyInstaller 给的是不落字的
    NullWriter，没有 reconfigure，这时安静跳过就是正确行为。
    """
    ok = True
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            ok = False      # 改不动就算了：自检报告的价值远高于「顺便修好编码」
    return ok
