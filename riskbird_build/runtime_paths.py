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

落点口径（顺序即优先级，每一级都先写探针实测**真能写**再返回）：
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

两条后来加上的纪律（2026-11-13 实机换来的，都写在这里而不是写在某个调用方身上）：
  · **受保护目录那一道滤不许砍到源码态锚点**。上一版把「相对家目录第一段是
    Desktop/Documents/Downloads 就摘掉」整条链通吃，而本仓库恰好就在 ``~/Desktop/PythonDemo``
    下面——于是开发态第一候选被自己的判据摘掉，四个采集器的数据目录集体跳到
    ``~/.b2bcollect/``，``riskbird_build/riskbird_data`` 里那份 Cookie 从此没人读：症状就是
    「企知道/风鸟/爱企查 突然都要重新登录、1688 种子库读不到」。判据没错，错在适用范围：
    锚点是**代码自己所在的目录**，进程刚刚从那里 import 成功，访问权已经成立了；而那条滤
    要防的是「把数据的家安在用户桌面顶层」，两件事不是一回事。tool_config 那一侧的
    ``state_dir_candidates`` 开发态本来就是提前返回、不过这道滤的——同一个口径在两份实现
    里长出了两种适用范围，红的那份就是这一份。
  · **落点只由形态决定，不许由「这台机器上谁已经存着数据」决定**。上一版让「已经有
    文件的候选」盖过优先级，代价是同一份包在不同机器上落到不同目录：硬判据在开发机上红
    （``~/.b2bcollect`` 里只要有过往留下的文件就压过程序同级那一级），在干净机器上又绿，
    而用户真正要的那句话——「整个目录拷到另一台电脑还是这套数据」——反而不成立了。
    不可再生的数据（登录态、Cookie、种子库）改用**显式接管**来保：浏览器配置走
    :func:`adopt_profile`、数据目录走 :func:`adopt_data_from`，两者都只复制、不删源、
    不覆盖已有文件，接完之后落点仍然是那一个确定的目录。接管过的文件记在 ``ADOPTED``
    里供自检报告原样报出，「悄悄接完不吭声」与「接没接上」是同一类问题的两种收场。

**本模块的函数一律不抛异常**：一个缓存目录不值得把整个工具带走。真到了全链路都写
不进去的机器上，宁可这一路不缓存，也要让窗口开出来、让日志把原因说清楚。
"""
import os
import sys
import tempfile
import time
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

# 这些文件不能拿来当「这个目录里已经有本工具的数据」的证据：写探针留下的、系统留下的、
# 以及本模块自己每级候选都会碰一面的东西。少了这一层滤，一个刚被探针建出来的空目录会
# 被当成「老数据在这儿」，于是接管接了个寂寞。
HOUSEKEEPING_FILES = {".write_test", ".DS_Store", "desktop.ini", "Thumbs.db"}
# 一次接管的两个上限（口径与 tool_config 同名常量一致，对拍见 test_data_dir_offline.py
# 第 12、13 节）：撞上限说明这个「老落点」不是本工具的目录，宁可少接也别把别人的
# 文件夹复制进落点。
ADOPT_MAX_FILES = 200
ADOPT_MAX_BYTES = 512 * 1024 * 1024
# 本进程接管过哪些文件（``<老目录> -> <文件名>``）：GUI 自检报告与远程排障原样报出这一份，
# 「悄悄接完不吭声」和「接没接上」是同一类故障的两种收场。
ADOPTED: List[str] = []


def _holds_tool_data(d: Path, markers: tuple = ()) -> bool:
    """这个候选目录里是不是已经有本工具写过的东西（接管判据用它，落点本身不用它）。

    ``markers`` 非空时按「目录下的相对路径」逐个查——浏览器配置目录不能只看「有没有文件」：
    一次崩溃后的半截 profile 里也有几十个 json，真正说明「这里登录过」的是
    ``Default/Cookies`` 那一个 SQLite。不传 markers 时按「除家务文件之外还有任何普通文件」
    判，这对 Cookie/种子库这类数据目录已经足够，而且不必为每个采集器维护一张文件名表
    （文件名表一散开，加一个文件就得记得改两处，那正是本轮要根治的形状）。
    """
    try:
        if markers:
            return any((d / m).is_file() for m in markers)
        return any(p.is_file() and p.name not in HOUSEKEEPING_FILES
                   for p in d.iterdir())
    except Exception:
        return False


def _choose(cands: List[Path], markers: tuple = ()):
    """候选链里第一个**真能写**的目录；``markers`` 留给调用方做接管判据，不参与择路。

    为什么「谁已经存着数据」不再决定落点（2026-11-13 自审纠正，上一版就是这么写的）：
    那条规则把「这台机器以前发生过什么」变成了落点的一部分——开发机上
    ``~/.b2bcollect`` 里只要有过往留下的文件，「数据优先落在程序同级」这条硬判据就当场
    红，换一台干净机器又绿；而用户拿到的是「同一份包在两台电脑上数据住在两个地方」，
    整个目录拷走也拷不走数据。数据跟着工具走是产品口径，本机历史不是。
    不可再生数据（登录态、Cookie、种子库）改由**显式接管**保住：配置目录走
    :func:`_adopt_profile`，数据目录走 :func:`_adopt_missing_files`，都只复制、不删源、
    不覆盖已有文件，接完之后落点仍然是这里给的那一个。

    返回 None 而不是「把第一个不可写的目录交出去」：调用方各自知道自己该退到哪一级
    （数据目录退第一候选、配置目录退持久位置），在这里替它挑一个就是上一轮 Mac 崩在
    启动那一下的同一种收场。口径与 tool_config.choose_writable 一致，对拍见
    test_data_dir_offline.py 第 12 节。
    """
    for d in cands:
        if _probe_writable(d):
            return d
    return None


def _adopt_missing_files(srcs: List[Path], dst: Path) -> List[str]:
    """新落点还是空的、而某个老落点里已经有数据时，把那些文件**复制**过来。

    三条硬规矩（与 tool_config.adopt_missing_files 同口径，对拍见
    test_data_dir_offline.py 第 13 节）：只在新落点真的没有过像样文件时动手；只补缺、
    绝不覆盖同名文件、绝不删源（老位置可能还有另一个版本在用）；只搬顶层普通文件、
    不递归。返回 ``"源 -> 文件名"`` 列表并累计进模块级 ``ADOPTED``。
    """
    import shutil
    out: List[str] = []
    try:
        dst = Path(dst)
        dst.mkdir(parents=True, exist_ok=True)
        if _holds_tool_data(dst):
            return out
        have = {p.name for p in dst.iterdir()}
    except Exception:
        return out
    for src in srcs:
        try:
            src = Path(src)
            if src == dst or src.resolve() == dst.resolve():
                continue
            for p in sorted(src.iterdir()):
                try:
                    if not p.is_file() or p.name in have or p.name in HOUSEKEEPING_FILES:
                        continue
                    if p.stat().st_size > ADOPT_MAX_BYTES or len(out) >= ADOPT_MAX_FILES:
                        continue
                    shutil.copy2(str(p), str(dst / p.name))
                    have.add(p.name)
                    out.append(f"{src} -> {p.name}")
                except Exception:
                    continue
        except Exception:
            continue
    ADOPTED.extend(out)
    return out


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


# 与 b2bcollect_build/tool_config.py 同名字段同口径（那一份管 state，这一份管 data）。
# 两份实现是本项目明知并接受的形状：独立运行的风鸟/1688 看不到 b2bcollect_build，
# 反向 import 就要在「谁先 import」上赌运气。对拍判据在 test_data_dir_offline.py，
# 谁改出自己的说法就红在哪一路——没有这条判据，两份迟早长成两种口径。
PROTECTED_USER_DIRS = ("Desktop", "Documents", "Downloads")


def _protected_first_segment(path, home):
    """按同一种口径算出「相对家目录的第一段」；不在家目录里则返回 None。"""
    try:
        rel = Path(os.path.abspath(str(path))).relative_to(os.path.abspath(str(home)))
    except Exception:
        return None
    return rel.parts[0].lower() if rel.parts else ""


def is_protected_user_dir(path) -> bool:
    """这个目录在 macOS 上是否受「系统隐私保护」，访问它可能要等用户点一次「允许」。

    为什么落点口径里要有这一条（2026-11-13 实机）：工具被放在桌面上时，候选链的
    「包旁」那一级就是 ``~/Desktop/b2bcollect_data``，而定落点靠的是**写探针**——
    在桌面上创建一个文件正是被内核扣住不动的那类动作。扣住的时间没有上限（实测四分
    钟以上），而它排在界面出现之前，于是症状是「双击图标，什么也没发生，也没有报错」。
    从终端起却一切正常（那时授权主体是终端），所以 CI 与命令行永远复现不出来。

    与 tool_config 同名函数同口径，逐例对拍在 test_data_dir_offline.py：只管 darwin；
    按相对家目录的第一段比（子目录同样受保护）；大小写不敏感；字面口径与解析后口径
    两边各问一遍、任一命中即算受保护。最后这条是本处最容易写错的地方：候选链里
    ``sys.executable`` 那一级是 resolve 过的（Mac 上 `/var` 与 `/private/var` 是同
    一个地方的两个名字），家目录却不一定，只按一边比会得到「两边都明明是桌面、就是对
    不上」的漏判，而漏判的方向恰好是「不加超时」——正是本轮实测那次挂死。保守的一侧
    代价很小：最多是一个候选被跳过。
    """
    if sys.platform != "darwin":
        return False
    names = tuple(name.lower() for name in PROTECTED_USER_DIRS)
    home = Path.home()
    try:
        homes = (home, home.resolve())
    except Exception:
        homes = (home,)
    try:
        paths = (Path(str(path)), Path(str(path)).resolve())
    except Exception:
        paths = (Path(str(path)),)
    return any(_protected_first_segment(p, h) in names for h in homes for p in paths)


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
    dev_anchor: Optional[Path] = None
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
        dev_anchor = base / name
        out.append(dev_anchor)
    out.append(Path.home() / USER_DIR_NAME / name)
    out.append(Path(tempfile.gettempdir()) / f"b2bcollect_{name}")
    # 受系统隐私保护的目录一律摘掉（判据见 :func:`is_protected_user_dir`）：这一条链
    # 是靠写探针定落点的，而「在桌面上创建一个文件」正是本轮实测被内核扣住的那类动作。
    # 上面的环境变量分支**不**过这道滤——那是远程排障与回归测试显式指定的落点，
    # 指定就照办，否则「我明明指了目录」与「实际写在别处」又会成了两句矛盾的话。
    #
    # 源码态锚点同样**不过**这道滤，理由与环境变量那条同源但更硬：锚点就是「代码自己
    # 所在的目录」，进程刚刚从这里 import 了本模块，访问权已经成立；而本仓库就放在
    # ``~/Desktop/PythonDemo`` 下面，整条链通吃的第一版把四个采集器的数据目录一起挪到
    # ``~/.b2bcollect/``，把开发机上攒了几年的 Cookie 留在原地没人读——症状是「企知道/
    # 风鸟/爱企查 都要重新登录、1688 种子库读不到」。那道滤要防的是「把数据的家安在
    # 用户桌面顶层」，不是「这个项目本身在桌面上」。口径对拍见 test_data_dir_offline.py
    # 第 11 节（tool_config 那一侧开发态本来就是提前返回、不过这道滤的）。
    kept = [d for d in out if d == dev_anchor or not is_protected_user_dir(d)]
    if not kept:
        kept = [Path.home() / USER_DIR_NAME / name]
    return kept


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
    # 择路只认「顺序 + 真能写」，口径与它存在的理由都写在 :func:`_choose`：落点不许跟着
    # 本机历史漂。老落点里已有的 Cookie/种子库在新落点还是空目录时由
    # :func:`_adopt_missing_files` **复制**过来——两头都要，既不许同一份包在两台电脑上
    # 落到不同目录，也不许落点调一次就把用户攒下的登录态留在原地（2026-11-13 实机：
    # 界面上不报错，只是「这个平台今天一家都查不到」）。
    # 一个都写不进去时退回第一候选——本函数的契约是「永不抛异常且总给一个路径」，
    # 调用方那句 mkdir 因此永远不会拿到 None；真写不进去由采集器自己的写失败播报去说。
    chosen = _choose(cands)
    if chosen is None:
        chosen = cands[0] if cands else Path(tempfile.gettempdir())
    else:
        _adopt_missing_files([d for d in cands if d != chosen], chosen)
    _CHOSEN[key] = chosen
    return chosen


# ── 浏览器配置目录（登录态真正的家）─────────────────────────────────────────
# 2026-11-13 Windows 实机：用户换了一个 Windows 账号继续用，1688 一直是 0 家、企知道
# 补采 10 家、爱企查补采 0 家，而另外八个不需要登录的站照常出数。配置目录当时写在
# ``%TEMP%\chrome_1688_profile`` / ``%TEMP%\chrome_b2bcollect_profile`` 上，而 TEMP 有三条
# 与「登录态要活多久」完全相反的性质：**每个用户各一份**（换账号=换目录）、**清理程序
# 随时可删**（磁盘清理/存储感知默认就会动它）、**注销即弃**。把付费账号的会话放在这种
# 地方，等于把「今天能不能采」交给运维脚本决定。大厂的做法是把应用自管的浏览器 profile
# 放在用户级的持久目录里（Windows 用 %LOCALAPPDATA%，类 Unix 用 ~/.b2bcollect），并且
# 对老位置只做一件事：**接过来**，不删、不搬坏。
PROFILE_ENV_VAR = "B2B_CHROME_PROFILE_DIR"
# 一个 Chrome 配置「登录过」的最小证据：Chromium 系的 Cookie SQLite 就在这个相对路径下。
# 只看「目录里有没有文件」会被半截崩溃后的 profile 骗到（那种目录里也有几十个 json）。
PROFILE_LOGIN_MARKER = ("Default/Cookies",)
# 接老 profile / 复制真实 Chrome 时跳过的东西：缓存不是登录态，而它恰好是体积最大、最可能
# 被 Chrome 独占锁住的那部分——跳过它既省时间又避开锁。清单里刻意**没有** ``Network``：
# 部分 Chromium 版本把 Cookie 挪进过 ``Default/Network/``，跳掉它等于把登录态跳没了；
# 同理没有 ``Local Storage``（不少站点把登录标记写在那儿），体积也只有 MB 量级。
PROFILE_SKIP_PATTERNS = ("Cache", "Code Cache", "GPUCache", "Service Worker",
                         "DawnCache", "DawnGraphiteCache", "GrShaderCache", "ShaderCache",
                         "IndexedDB", "File System", "Platform Notifications",
                         "Origin Trials", "Trust Tokens", "blob_storage", "Download Service",
                         "optimization_guide_model_store", "AutofillAiModelCache",
                         "SingletonLock", "SingletonSocket", "SingletonCookie")


def _durable_profile_root() -> Path:
    """用户级持久配置的根：Windows 用 %LOCALAPPDATA%，其余用 ``~/.b2bcollect``。

    不用 ``%APPDATA%``（ roaming 剖面在域环境里跟着账号在网络盘上漂，一次登录要复制
    几百兆缓存），也不用 ``%PROGRAMDATA%``（机器级=同一台电脑上的别的账号能读到你的
    付费账号会话，这是合规问题不是便利问题）。
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / USER_DIR_NAME.lstrip(".")
    return Path.home() / USER_DIR_NAME


def _adopt_profile(src: Path, dst: Path) -> bool:
    """把老位置的登录态**复制**到新位置（只复制、绝不删源；失败就继续用老位置）。

    为什么不用 ``os.rename``：老位置很可能正被一个活着的 Chrome 占着（单实例锁），
    rename 跨卷也会退化成复制；复制失败一半比「还在用老位置」糟得多。
    """
    import shutil
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(str(src), str(dst),
                        ignore=shutil.ignore_patterns(*PROFILE_SKIP_PATTERNS),
                        dirs_exist_ok=True)
        return _holds_tool_data(dst, PROFILE_LOGIN_MARKER)
    except Exception:
        return False


def browser_profile_dir(name: str, legacy_dirs=()) -> Path:
    """浏览器配置目录的落点：持久位置优先，老位置手里有登录态就先接过来再走。

    ``legacy_dirs`` 由调用方给（各工具的老位置不一样：1688 在 ``%TEMP%\\chrome_1688_profile``，
    多平台工具在 ``%TEMP%\\chrome_b2bcollect_profile``），口径只有一条：**不许把登录态
    的寿命交给临时目录**。环境变量 ``B2B_CHROME_PROFILE_DIR`` 是容器目录，与数据目录
    同一个语义，回归测试与远程排障靠它把 profile 钉进临时目录。
    """
    name = str(name or "chrome_profile")
    env_dir = str(os.environ.get(PROFILE_ENV_VAR) or "").strip()
    if env_dir:
        return Path(env_dir) / name
    new = _durable_profile_root() / name
    legacy = [Path(str(x)) for x in legacy_dirs if x]
    if not _holds_tool_data(new, PROFILE_LOGIN_MARKER):
        # 老位置里登录过就先复制过来，然后仍然走持久位置：接管不改变落点（口径同
        # :func:`_choose`），只是把登录态搬进那个确定的落点。
        src = next((d for d in legacy if _holds_tool_data(d, PROFILE_LOGIN_MARKER)), None)
        if src is not None:
            if _adopt_profile(src, new):
                ADOPTED.append(f"{src} -> （整个 Chrome 配置目录）")
            elif _probe_writable(src):
                # 接不过来就继续用老位置：登录态比「落点统一」重要得多，静默换成一个空
                # profile 的表现就是「今天怎么又要扫码登录」——与用户报的那条一模一样。
                return src
    chosen = _choose([new] + legacy)
    if chosen is None:
        # 一个候选都写不进去：仍报持久位置那一级。这里不返回 None 是因为调用方在
        # 「启动 Chrome」那条链上，它要的是一个路径而不是一个结论；真写不进去时
        # Chrome 会在自己的日志里把原因说出来，比在这里静默换一个目录诚实。
        return new
    return chosen


def real_chrome_profile() -> Optional[Path]:
    """用户自己那个 Chrome 的「User Data」目录；没有就返回 None（没装 Chrome 的机器）。

    判据用 ``Default/Cookies`` 在不在，而不是目录在不在：装了 Chrome 从没开过、或者
    只开了访客窗口的机器，那个目录是存在的但里面没有登录态，拿它当「有真实配置」会
    让刷新逻辑每次启动都白跑一遍。
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        p = Path(base) / "Google" / "Chrome" / "User Data"
    elif sys.platform == "darwin":
        p = Path(os.path.expanduser("~/Library/Application Support/Google/Chrome"))
    else:
        p = Path(os.path.expanduser("~/.config/google-chrome"))
    try:
        return p if (p / PROFILE_LOGIN_MARKER[0]).is_file() else None
    except Exception:
        return None


def _cookies_mtime(d: Path) -> float:
    try:
        return (Path(d) / PROFILE_LOGIN_MARKER[0]).stat().st_mtime
    except Exception:
        return 0.0


def _safe_copy(src, dst, **kwargs):
    """copytree 的单文件复制器：读不动的文件跳过而不是把整次复制带走。

    用户的 Chrome 很可能正开着，``Default/Cookies`` 这类 SQLite 在 Windows 上是独占
    的——一次 PermissionError 不该让「刷新登录态」这件事整件失败，更不该把启动它的
    采集器一起带崩（旧写法在企知道那一路上就是裸 copytree，锁不住就整段抛）。
    """
    import shutil
    try:
        shutil.copy2(src, dst, **kwargs)
    except Exception:
        pass


def refresh_profile_from_real_chrome(dst: Path, src: Optional[Path] = None) -> bool:
    """真实 Chrome 里的登录态**比工具那份新**时，把它刷进工具那份；否则一行都不动。

    这一处存在的理由（2026-11-13 排查企知道/爱企查补采出数异常时实测到的形状）：老写法
    是「目标目录不存在才复制」，于是那份副本从此**永不刷新**——用户在浏览器里重新登录、
    续了期，工具还在用几周前那份过期的 Cookie，站点把它当未登录，界面表现是「这一路
    今天一家都没采到」，日志里连一句「我在用旧 Cookie」都没有。另一半风险是反向的：
    工具里人工登录出来的会话不能被一份更旧的真实配置盖掉，所以**只在源更新时才刷**。

    返回 True 表示这次真刷过（调用方据此说一句人话），False 表示没刷（源缺失或不更新）。
    """
    src = real_chrome_profile() if src is None else Path(str(src))
    try:
        if not src or not (src / PROFILE_LOGIN_MARKER[0]).is_file():
            return False
        import shutil
        dst.mkdir(parents=True, exist_ok=True)
        if _cookies_mtime(dst) >= _cookies_mtime(src):
            return False
        shutil.copytree(str(src), str(dst), dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(*PROFILE_SKIP_PATTERNS),
                        copy_function=_safe_copy)
        return _holds_tool_data(dst, PROFILE_LOGIN_MARKER)
    except Exception:
        return False


def login_chrome_profile(name: str, legacy_dirs=()):
    """「要继承用户真实 Chrome 登录态」那一类配置目录：定落点 + 按需刷副本。

    返回 ``(目录, 本次是否刚从真实 Chrome 刷过)``。调用方（风鸟/企知道/爱企查）拿这一句
    替掉以前那三段几乎一样、又各有各的漏的写法：三段都是「目录不存在才复制」，复制完
    就**再也不刷新**；两份还共用同一个 ``chrome_debug_profile``（同一时间只能被一个
    Chrome 实例占用，第二路上就撞单实例锁）；其中一份在 Windows 上是裸 ``copytree``，
    用户的 Chrome 正开着时那一句会整段抛。名字按平台分开、老目录作为 legacy 传进来接管，
    刷新交给 :func:`refresh_profile_from_real_chrome`。
    """
    d = browser_profile_dir(name, legacy_dirs=legacy_dirs)
    return d, refresh_profile_from_real_chrome(d)


def profile_login_state(d: Path) -> str:
    """一句人话说明这个配置目录里的登录态状态（体检日志用，绝不抛异常）。"""
    try:
        ck = Path(d) / PROFILE_LOGIN_MARKER[0]
        if not ck.is_file():
            return "无登录态（目录里没有 Cookies）"
        age = time.time() - ck.stat().st_mtime
        return f"有登录态（Cookies 距今 {int(age // 86400)} 天 / {int(age % 86400 // 3600)} 小时）"
    except Exception:
        return "登录态未知（目录读不到）"


def describe(name: str = DEFAULT_DATA_DIR_NAME, dev_dir: Optional[str] = None) -> str:
    """一行说明「现在落在哪、候选有哪几处」，只含路径不含数据。"""
    current = stable_data_dir(name, dev_dir)
    try:
        rel = str(current.relative_to(Path.home()))
        shown = "~/" + rel
    except Exception:
        shown = str(current)
    return (f"数据目录={shown}"
            f"（候选 {len(candidate_dirs(name, dev_dir))} 处，可写者里已有数据的优先）")


def reset_cache() -> None:
    """清掉进程内缓存（切换环境变量的测试用例之间必须调，否则测的是上一次的落点）。"""
    _CHOSEN.clear()
