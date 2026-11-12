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


def _app_sibling_dir() -> Optional[Path]:
    """Mac .app 形态下的「包旁」目录：X.app 同级，用户看得见也写得进去。"""
    ed = exe_dir()
    if ed is None or not sys.platform.startswith("darwin"):
        return None
    try:
        if ed.parent.parent.name.endswith(".app"):
            return ed.parent.parent.parent
    except Exception:
        return None
    return None


def state_dir(subdir: str = "") -> Path:
    """持久可写状态目录（缓存、自检报告这类非会话产物）。

    候选链：环境变量 > 包旁（Mac .app）> exe 同级 > 用户目录 ~/.b2bcollect >
    系统临时目录；开发态直接用模块目录，避免历史缓存搬家。每个候选都过写探针。
    """
    name = "b2bcollect_state"
    if subdir:
        name = os.path.join(name, subdir)
    env_dir = str(os.environ.get(_STATE_DIR_ENV_VAR) or "").strip()
    if env_dir:
        return Path(env_dir) / (subdir or "")

    cands: List[Path] = []
    if is_frozen():
        sib = _app_sibling_dir()
        if sib is not None:
            cands.append(sib / name)
        ed = exe_dir()
        if ed is not None:
            cands.append(ed / name)
        cands.append(Path.home() / USER_CONFIG_DIR_NAME / name)
        import tempfile
        cands.append(Path(tempfile.gettempdir()) / name)
    else:
        return _MODULE_DIR / (subdir or "") if subdir else _MODULE_DIR

    for d in cands:
        if probe_writable(d):
            return d
    fallback = Path.home() / USER_CONFIG_DIR_NAME / name
    return fallback


def save_config(values: dict) -> Tuple[bool, str]:
    """把若干键写进**一个可写**的配置文件，返回 (是否成功, 落点说明)。

    落点选择顺序 = 读取链里「存在且可写」优先、其次用户目录：在 exe 同级已经
    有配置文件的机器上改配置，就应该改那一份（不然会凭空多出第二份互相矛盾的
    配置）；否则写到用户目录这一级。绝不允许「界面点保存 → 静默失败」。
    """
    target: Optional[Path] = None
    for label, path in config_candidates():
        try:
            # 打包态的「随包内置」是临时解压目录（Windows onefile 每次退出即删，
            # Mac 转置后只读）：往它里面写等于「保存成功但下次开机没了」，
            # 这一级只许读、不许作为保存落点。
            if label == "随包内置" and is_frozen():
                continue
            if path.is_file() and probe_writable(path.parent):
                target = path
                break
        except Exception:
            continue
    if target is None:
        user = user_config_file()
        if not probe_writable(user.parent):
            return False, "用户目录不可写，配置未保存"
        target = user
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
