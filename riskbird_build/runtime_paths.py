# -*- coding: utf-8 -*-
"""runtime_paths.py —— riskbird_build / ali1688_build 两侧「可写数据落在哪」的唯一口径。

b2bcollect_build 那一侧（b2bcollect_data 在 cdp_base、b2bcollect_state 在 tool_config）
是同一形状的镜像实现：两侧不共用代码——独立运行的风鸟/1688 工具看不到 b2bcollect_build，
而 cdp_base 被导入的时刻早于 site_adapters 往 sys.path 上补 riskbird_build，反向依赖就会
在「谁先 import」上赌运气。所以纪律落在判据上：test_data_dir_offline.py 第 1、2、3、7、8
节让这几路逐字比对同一组期望，谁改出自己的说法就红在哪一路。

为什么要有这个模块（2026-11-12 两份实机故障，同一个根因的两个面）：
  · **Mac 打不开**：崩溃日志的最后一行是
      ``OSError: [Errno 30] Read-only file system:
       '/private/var/folders/.../AppTranslocation/<UUID>/d/B2bCollectTool.app/Contents/
       Frameworks/riskbird_build/riskbird_data'``
    ——风鸟采集器 ``__init__`` 里那句 ``Path(__file__).parent / "riskbird_data"`` 加裸
    ``mkdir``。被 Gatekeeper 隔离过的 .app 会从只读随机路径启动（App Translocation），
    包内整棵树不可写，于是「打开工具就报错」，界面根本长不出来。
  · **Windows 每次都得重新登录、每天白烧查询额度**：onefile 产物里 ``__file__`` 指向
    每次启动都解、退出就删的 ``%TEMP%\\_MEIxxxx``，Cookie 与 30 天卡片缓存落在那里
    等于从来没落过。症状不是报错，是「功能悄悄没了」——最难查的那一类。

所以「写哪儿」这件事必须由一处回答，而不是每个采集器各抄一遍：企知道早就有一份
``_stable_data_dir()``，风鸟与爱企查却还各自写着包内路径，同一条纪律在一个目录树里
存在两种实现，漏的那两路就正好是本轮崩掉的那两路。2026-11-12 之后又把
``ali1688_build/ali1688_crawler.py`` 那份也并进来——它自己那份少了最后一级临时目录
退路（全写不进去的机器上把不可写的目录原样返回，调用方一句 mkdir 就崩在启动那一下），
而且把 ``Contents/MacOS`` 的深度写死在判据里。同一个形状在一个仓库里存四份，就是
「每修一次都只修到自己那一处」的成因：口径只留这一份，各采集器只做一层旧函数名的门面。

落点口径（顺序即优先级，每一级都先写探针验**真能写**再返回）：
  0. 环境变量 ``B2B_RISKBIRD_DATA_DIR``：**容器目录**，每个消费者在其下各占一个
     ``<name>`` 子目录（远程排障与回归测试指定落点，绝不碰真实用户目录）。做成容器
     而不是直接指定目录，是因为现在四个采集器都问这一处——一个开关把四份 Cookie 和
     种子库堆进同一个目录，排障的人就分不清哪一份是谁写的了；
  1. Mac .app：**包旁** ``b2bcollect_data/<name>``（与 cdp_base / site_adapters 同一约定，
     用户看得见，卸载工具时不会被一起删）；
  2. 其它打包态：**exe 同级** ``<name>``（onedir 安装目录、绿色版解压目录都可写）。
     Mac 的 .app 形态下这一级正好是 ``Contents/MacOS``＝包内，按形态直接排除（见
     :func:`candidate_dirs`），不留给「恰好可写」这种运气来排除；
  3. 源码态：**调用方给的锚点** ``<dev_dir>/<name>``，没给就用本模块目录。锚点必须由
     调用方说：企知道的风鸟 Cookie 历史在 ``riskbird_build/riskbird_data``，1688 的种子库
     历史在 ``ali1688_build/ali1688_data``，口径替它选一个就等于悄悄把开发机上跑了几
     年的老数据搬到别处（「升级完 Cookie 全掉了」就是这么来的）；
  4. 退路：``~/.b2bcollect/<name>`` → 系统临时目录（最后一级只求「不崩、不写进只读卷」）。

**本模块的函数一律不抛异常**：一个缓存目录不值得把整个工具带走。真到了全链路都写
不进去的机器上，宁可这一路不缓存，也要让窗口开出来、让日志把原因说清楚。
"""
import os
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

# 默认目录名：与企知道/风鸟/爱企查历史落点同名，老机器上已有的 Cookie 直接接着用
DEFAULT_DATA_DIR_NAME = "riskbird_data"
# 用户目录里那一级的容器名（与 b2bcollect 的 tool_config 同一个家，运维只需记一个目录）
USER_DIR_NAME = ".b2bcollect"
# 指定落点的环境变量（优先级最高；回归测试靠它把落点钉进临时目录，不碰真实用户目录）
DATA_DIR_ENV_VAR = "B2B_RISKBIRD_DATA_DIR"

# 同一进程内不重复探针：采集器构造、Cookie 读写、证据落盘问的是同一个目录
_CHOSEN: Dict[str, Path] = {}


def _probe_writable(d: Path) -> bool:
    """目录「在不在」与「写不写得进去」是两件事：mkdir 之后真写一个文件再删掉。

    只判 mkdir 会得到假阳性——桌面上被 OneDrive 重定向、被安全软件的文件夹保护拦着、
    或者整个卷是只读挂载（App Translocation 就是这种）时，mkdir 可能成功而写文件失败。
    """
    probe = d / ".write_test"
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except Exception:
        return False


def _app_bundle_dir() -> Optional[Path]:
    """打包态 Mac 的 ``X.app`` 目录；非 Mac、非 .app、源码态返回 None。

    不写死 ``Contents`` 的深度：PyInstaller 在不同大版本里把数据放过
    ``Contents/Resources`` 也放过 ``Contents/Frameworks``，按「向上找第一个 .app」
    来判形态，将来布局变了也不会突然认不出自己的包。
    """
    if not getattr(sys, "frozen", False) or not sys.platform.startswith("darwin"):
        return None
    try:
        base = Path(sys.executable).resolve()
    except Exception:
        return None
    for parent in base.parents:
        if parent.name.endswith(".app"):
            return parent
    return None


def candidate_dirs(name: str = DEFAULT_DATA_DIR_NAME,
                   dev_dir: Optional[str] = None) -> List[Path]:
    """按优先级列出候选落点（不保证可写，也不保证存在）。

    公开出来是给产物自检与远程排障用的：一句「该落哪几处、现在落在哪一处」比让
    用户猜「为什么我的 Cookie 没存住」有用得多。

    ``dev_dir`` 只影响源码态那一级（见模块 docstring 第 3 条）：打包态根本不会走到
    那里，所以自检与排障读到的候选链与它无关，不存在「开发机看着对、包里另有一套」。
    """
    name = str(name or DEFAULT_DATA_DIR_NAME)
    env_dir = str(os.environ.get(DATA_DIR_ENV_VAR) or "").strip()
    if env_dir:
        # 指定落点就是唯一答案：不再往下探，否则「我明明指了目录」与「实际写在别处」
        # 又会变成两条互相矛盾的说法。按 <name> 分个子目录，多个采集器共用这一个开关
        # 时才不会把 Cookie 与种子库混在同一个文件夹里。
        return [Path(env_dir) / name]
    out: List[Path] = []
    if getattr(sys, "frozen", False):
        app = _app_bundle_dir()
        if app is not None:
            out.append(app.parent / "b2bcollect_data" / name)
        try:
            sib = Path(sys.executable).resolve().parent / name
            # .app 形态下「exe 同级」就是 Contents/MacOS——**那是包内**。本轮 Mac 崩掉的
            # 那个路径正是包内的 riskbird_data，如果只靠「写探针会不会失败」来排除它，
            # 就只在整卷只读时成立：用户从下载目录直接双击（没被隔离）时包内是可写的，
            # Cookie 又被塞回应用包里，而「数据不进包」这条纪律的意义（卸载不丢数据、
            # 只读卷不崩、升级不覆盖）当场没了。所以按**形态**排除，不按运气排除。
            if app is None or app not in sib.parents:
                out.append(sib)
        except Exception:
            pass
    else:
        base = Path(str(dev_dir)) if dev_dir else Path(__file__).resolve().parent
        out.append(base / name)
    out.append(Path.home() / USER_DIR_NAME / name)
    out.append(Path(tempfile.gettempdir()) / f"b2bcollect_{name}")
    return out


def stable_data_dir(name: str = DEFAULT_DATA_DIR_NAME,
                    dev_dir: Optional[str] = None) -> Path:
    """全树唯一的数据目录出口：返回**真能写**的那一个，永不抛异常。"""
    name = str(name or DEFAULT_DATA_DIR_NAME)
    # 缓存键带上源码态锚点：同一个目录名在两个调用方手里（现在 riskbird_data 只有
    # 一个，将来加采集器不一定）各自择一次路，谁也不替谁定。打包态两边候选链本来就
    # 相同，多这一截不改变行为，只是不把「第一个问的人」的结果当成全局答案。
    key = f"{name}|{Path(str(dev_dir)).resolve() if dev_dir else ''}"
    cached = _CHOSEN.get(key)
    if cached is not None:
        return cached
    cands = candidate_dirs(name, dev_dir)
    chosen = cands[0]
    for d in cands:
        if _probe_writable(d):
            chosen = d
            break
    _CHOSEN[key] = chosen
    return chosen


def describe(name: str = DEFAULT_DATA_DIR_NAME, dev_dir: Optional[str] = None) -> str:
    """一行说明「现在落在哪、候选有哪几处」，只含路径不含数据。"""
    current = stable_data_dir(name, dev_dir)
    try:
        rel = str(current.relative_to(Path.home()))
        shown = "~/" + rel
    except Exception:
        shown = str(current)
    return (f"数据目录={shown}"
            f"（候选 {len(candidate_dirs(name, dev_dir))} 处，按可写探针择第一）")


def reset_cache() -> None:
    """清掉进程内缓存（切换环境变量的测试用例之间必须调，否则测的是上一次的落点）。"""
    _CHOSEN.clear()
