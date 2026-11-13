# -*- coding: utf-8 -*-
"""持久数据目录离线回归：只读应用包（App Translocation）下绝不崩、绝不写进包内。

复现的实机故障：Mac 上 .app 被 Gatekeeper 从只读随机路径启动，企知道 Cookie
目录用包内裸 mkdir → OSError [Errno 30] Read-only file system，启动即弹错误框。
八节各管一件事：1 只读整卷（五种落点实现逐条实测，退路只许有一个家 ~/.b2bcollect）、
2 包可写时也不许写进包里（并逐字比对包旁布局）、3 Windows 打包（含 1688 适配器与
基类必须同名一个目录）、4 开发态不搬家、5 崩溃现场直测、6 运行期**文件**（审计日志与列
映射历史）的默认落点绝不相对当前工作目录、7 定口径的那一侧（tool_config 的状态目录与
配置保存、deepseek 的退路）按**形态**排除包内——第 7 节还专门立了「候选链」这条与权限
无关的判据：1~3 节那种「让写探针替我选」的断言，在整包可写的机器上会替缺陷打掩护；
8 兜住口径的最后一档与「一个形状只许一份实现」：包旁与用户目录都写不进去时必须退到
系统临时目录且**真写得进去**（旧写法把不可写的目录原样返回，调用方一句 mkdir 就崩）、
不赌 .app 内部布局深度、环境变量指的是容器而不是唯一目录，最后把口径本身换成哨兵问
每一路「有没有去问它」——行为比对抓得住写错的实现，抓不住同一形状各写四遍的实现；
9 管 macOS 那三个受系统隐私保护的目录：app 放在桌面时「包旁」那一级就是 ~/Desktop/…，
而定落点靠写探针，等于让内核把启动扣住等用户点「允许」（本轮实机四分钟以上那次）。
候选链按语义摘掉这类目录，判据在两个独立打包的树里各有一份，于是第 9 节立三条对拍：
名单同字、同一批样例逐例答案一致（含 Windows 上两份都不降级、同一个目录的两种写法
两份都得认——只按字面比的实现会漏判，而漏判的方向恰好是「不加超时」）、以及把判据摘
成恒 False 之后桌面那一级必须回到候选链（不然「链里干净」可能只是本来就没有）。
10 管「落点只由形态决定」这条新口径：可写的候选里挑「已经存着数据的那个」那一版被改掉
了，因为它把本机历史当成了落点的一部分——同一份包在两台电脑上数据住在两个地方、用户拷
走工具目录拷不走数据，而判据在开发机红、在干净机器绿；这里既钉择路确定性（旧口径原样
请回来做差分，夹具分辨不了新旧口径就算白测），也钉「全都写不进去时给 None、退路由调用
方说」与指定落点那一档（容器语义、指了就是唯一答案、不过受保护滤网，两侧同能力——以前
只有 riskbird 那一侧有开关，12 路 CDP 的种子库压根指不动，「指了不生效」和「没有这个
开关」在日志里是同一句话）。
11 钉受保护那道滤在**三处**接线的两个方向：少滤一处就把启动交给内核的授权弹窗（本轮实
机扣住四分钟那次），多滤一处则把开发机上攒了几年的 Cookie 悄悄搬家（症状是「几个平台都
要重新登录、1688 种子库读不到」）；接线用「摘掉判据答案就变」证明，不靠链里恰好没有。
12/13 钉两份镜像实现不再分叉：常量、有数据判据、择路、接管账目逐例同答（并把数据偏好
注回来验证对拍真能抓红），接管三条硬规矩（新落点在用就不动手、同名条目不覆盖、只搬顶
层）加两个上限加 ADOPTED 台账，配置目录接不过来时**退回老位置**——登录态比落点统一重要。
14/15 钉用户报「以前能采到、现在一直是 0」那条链上的两句口径：页尾「早过滤 N 家/增量库
已含 N 家」与收尾「还欠多少家补联系人」在 cdp_base 与 ali1688 两边逐字同构、同表同答，
种子库解锁判据 ct/rt 两边一致（旧口径「进过库就永不重采」在这张表上确实锁死待补采的公
司）；results_live 五路同构且真跑一次——官网回采联系方式必须写回真身，写成副本的话那句
「已补采」就是空话。
运行：python test_data_dir_offline.py
"""
import ast
import inspect
import os
import contextlib
import importlib
import shutil
import stat
import sys
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
for p in (str(HERE), str(HERE.parent / "riskbird_build"), str(HERE.parent / "ali1688_build")):
    sys.path.insert(0, p)

import cdp_base                # noqa: E402
import site_adapters           # noqa: E402
import qizhidao_crawler        # noqa: E402
import ali1688_crawler         # noqa: E402
import runtime_paths           # noqa: E402  (这一棵树唯一的落点口径，用例切环境要清它的缓存)
import audit_logger            # noqa: E402  (第 6 节：运行期文件的默认落点)
import mapping_recommender     # noqa: E402  (第 6 节：同上，两个消费者共用一份口径)
import tool_config             # noqa: E402  (第 6 节：落点口径本体，按名字改它来演故障)
import deepseek_expander       # noqa: E402  (第 7 节：口径模块没加载时的退路落点)

TMP = Path(tempfile.mkdtemp(prefix="b2bdir_"))
FAKE_HOME = TMP / "home"
FAKE_HOME.mkdir()
_real_home = Path.home
Path.home = classmethod(lambda cls: FAKE_HOME)   # 兜底位置也指到临时目录，不污染真用户目录

# 开跑前的运行形态快照：必须在任何用例拨开关之前记下（写在模块顶部就是这个意思），
# 它是脚本末尾那道「谁拨走没收回」判据的比对基准——本机以源码态跑，`frozen` 本来就不
# 存在，所以快照里那一项是 None，恢复时必须**删掉**而不是置 False。
_SNAPSHOT = {k: getattr(sys, k, None) for k in ("frozen", "_MEIPASS", "platform", "executable")}

PASS = []


def check(name, cond, detail=""):
    assert cond, f"{name} 断言失败：{detail}"
    PASS.append(name)
    print(f"  ✅ {name}")


def _ro(path):
    """把目录设为只读（root 例外，测试进程非 root 时 mkdir 必失败）"""
    path.chmod(stat.S_IRUSR | stat.S_IXUSR)


def _rw(path):
    path.chmod(stat.S_IRWXU)


def _walk_dirs(path):
    """自顶向下列出所有子目录。父目录保持 r-x（可穿越），所以只读状态下也能走完。"""
    for child in sorted(path.iterdir()):
        if child.is_dir():
            yield child
            yield from _walk_dirs(child)


def _ro_tree(app):
    """整棵 .app 都设为只读——这才是转置卷的实况。

    只 chmod ``Fake.app`` 顶层等于给被测代码留了个后门：``Contents/MacOS`` 仍然可写，
    于是「exe 同级」这一路在夹具里 mkdir 成功，测试看着像通过，而真机上整卷只读时崩的
    就是这一路。**夹具比真机宽松**是离线测试最坏的一种假绿（本轮 qizhidao 这条正是这么
    露出来的：候选表里那条包内路径本该按形态排除，却靠写探针的运气混过了第一版用例）。
    """
    for d in [app, *_walk_dirs(app)]:
        _ro(d)


def _rw_tree(app):
    for d in [app, *_walk_dirs(app)]:
        _rw(d)


def _under(child, parent):
    """macOS 下 /var 是 /private/var 的软链，路径比较统一 resolve。"""
    return Path(parent).resolve() in Path(child).resolve().parents


def _same(actual, expect) -> bool:
    """逐字比对落点前两边都要 resolve：被测函数对 sys.executable 做过 resolve()，
    夹具里的 TMP 却是 /var/... 这条软链名——不统一就会红在「同一个目录的两种写法」上，
    而这条用例要抓的是「目录选错了」，不是「字符串不一样」。"""
    try:
        return Path(actual).resolve() == Path(expect).resolve()
    except (OSError, TypeError):
        return False


def make_app(read_only=False):
    """造一个 Fake.app/Contents/MacOS/<exe> 结构，返回 (exe 路径, 包外父目录)

    每次用独立子目录：只读目录里再 mkdir 会先撞 EACCES，复用同名会假失败。
    """
    make_app.n += 1
    outer = TMP / f"case{make_app.n}" / "Fake.app"
    macos = outer / "Contents" / "MacOS"
    macos.mkdir(parents=True, exist_ok=True)
    (macos / "B2bCollectTool").write_text("exe", encoding="utf-8")
    holder = outer.parent                       # .app 所在目录
    if read_only:
        _ro(holder)
        _ro_tree(outer)                         # 整卷只读，见 _ro_tree
    return macos / "B2bCollectTool", holder


make_app.n = 0


_ABSENT = object()


@contextlib.contextmanager
def _switches(**values):
    """按**名字**拨 sys 上的运行形态开关，退出时按名字还原（传 None = 恢复成没拨过的样子）。

    为什么不用 ``old = (sys.frozen, sys.platform, sys.executable)`` 这种位置元组：存取
    两边一旦错开一位，``sys.frozen`` 就成了真值字符串、``sys.executable`` 就成了 None，
    而红的是**隔了好几段**的下一条用例，报的还是「NoneType」这种和落点毫无关系的栈。
    本仓库另一套离线测试（test_frozen_delivery_offline 的 T 节）就是这么把 U 节带红的，
    判据形状完全一样，所以这边也走同一条纪律：按名字存取，末尾再核对一次快照。
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


def run_resolver(fn, frozen, platform, exe=None, here=None):
    """在指定假环境下调用被测解析函数，返回 (结果路径, 异常)"""
    old_here = site_adapters._HERE
    switches = {"platform": platform, "frozen": True if frozen else None}
    if exe is not None:                       # 没给就不动这一项（动了就回不到本机真实值）
        switches["executable"] = str(exe)
    try:
        # 换环境之前必须清掉进程内择路缓存：四节用例问的是同一个目录名，缓存不清的话
        # 第 2 节测的其实是第 1 节的环境（结果一路都是 FAKE_HOME，「包旁优先」就假绿了）。
        # 这也是 runtime_paths.reset_cache 存在的唯一理由，测试必须用它，不许另造。
        runtime_paths.reset_cache()
        with _switches(**switches):
            if here is not None:
                site_adapters._HERE = here
            return fn(), None
    except Exception as exc:                      # noqa: BLE001
        return None, exc
    finally:
        site_adapters._HERE = old_here


# ── 1. 核心场景：只读 .app（App Translocation）→ 不抛异常、结果在包外且真可写 ──
# 每一路都带上「这个实现认为自己的目录叫什么名字」。名字不是注释里的说法，而是
# 下面三节逐字比对的期望值：退路只许有**一个家**（~/.b2bcollect）+ 各自的目录名，
# 谁多造一个容器名（历史上 1688 的退路是 ~/.ali1688），这里就会红。
CASES = [
    # (标签, 被测函数, 是否依赖 site_adapters._HERE, 目录名)
    ("cdp_base._stable_data_dir",
     cdp_base.CdpSiteCrawler._stable_data_dir, False, "b2bcollect_data"),
    ("site_adapters._writable_data_dir",
     site_adapters._writable_data_dir, True, "b2bcollect_data"),
    ("Ali1688Site._stable_data_dir",
     site_adapters.Ali1688Site._stable_data_dir, True, "b2bcollect_data"),
    ("qizhidao_crawler._stable_data_dir",
     qizhidao_crawler._stable_data_dir, False, "riskbird_data"),
    ("ali1688_crawler._stable_data_dir",
     ali1688_crawler.Ali1688Crawler._stable_data_dir, False, "ali1688_data"),
]


def _expect_home(dir_name: str) -> Path:
    """只读卷退路：``~/.b2bcollect/<目录名>``（全体实现共用这一个家）。

    容器名取自 tool_config 的常量而不是写死字面量：riskbird_build 那份口径里也有
    一个同名常量，哪天真要改名，这套用例要红在「两边不一致」上，而不是绿着放过。
    """
    return FAKE_HOME / tool_config.USER_CONFIG_DIR_NAME / dir_name


def _expect_sibling(holder: Path, dir_name: str) -> Path:
    """Mac 包旁：统一进 ``<.app 旁边>/b2bcollect_data``，非本工具主目录的再套一层。"""
    base = holder / "b2bcollect_data"
    return base if dir_name == "b2bcollect_data" else base / dir_name


exe, holder = make_app(read_only=True)
app_dir = Path(exe).parent.parent.parent          # Fake.app
for name, fn, uses_here, dir_name in CASES:
    _ro_tree(app_dir)                             # 每一路之前重新把整卷设只读：上一路可能在
                                                  # 包里建出目录（那是被测代码的错，不能让夹具
                                                  # 替它把后门留着）
    here = exe.parent if uses_here else None
    result, exc = run_resolver(fn, True, "darwin", exe=exe, here=here)
    check(f"{name}：只读 .app 不抛异常", exc is None, repr(exc))
    check(f"{name}：结果不在 .app 包内", result is not None and not _under(result, app_dir),
          f"{result} 仍在 {app_dir} 内")
    probe = result / ".probe"
    probe.write_text("1", encoding="utf-8")       # 真写一次，验证返回的就是可用目录
    probe.unlink()
    # 原来只判「在 FAKE_HOME 底下」，于是 ~/.ali1688 与 ~/.b2bcollect 同时成立——
    # 「运维只需记一个目录」这句写在各模块 docstring 里的话就没人管了。逐字比路径。
    check(f"{name}：只读卷退到唯一的用户目录", _same(result, _expect_home(dir_name)),
          f"期望 {_expect_home(dir_name)}，实际 {result}")
    _rw_tree(app_dir)

# ── 2. 可写的 .app 父目录 → 数据放包旁可见目录（不塞进 .app 里） ──
exe2, holder2 = make_app()
app_dir2 = Path(exe2).parent.parent.parent
for name, fn, uses_here, dir_name in CASES:
    here = exe2.parent if uses_here else None
    result, exc = run_resolver(fn, True, "darwin", exe=exe2, here=here)
    check(f"{name}：包旁目录可写时优先用它", exc is None and _under(result, holder2),
          f"exc={exc!r} result={result} 期望在 {holder2} 下")
    check(f"{name}：包旁目录仍不写进 .app 内部", ".app" not in str(result), str(result))
    check(f"{name}：包旁布局与说明书一致", _same(result, _expect_sibling(holder2, dir_name)),
          f"期望 {_expect_sibling(holder2, dir_name)}，实际 {result}")
    # 这一条是第 1 条的另一半：包**恰好可写**时（用户从下载目录双击、没被 Gatekeeper 隔离）
    # 探针不会替我们拦住包内路径，只有「按形态排除」才守得住「数据不进包」这条纪律。
    check(f"{name}：包内可写时也绝不写进包里", not _under(result, app_dir2),
          f"{result} 在 {app_dir2} 内")

# ── 3. Windows 打包（onedir/onefile 同级目录）→ exe 旁，不落 _MEIPASS ──
win_dir = TMP / "win" / "dist"
win_dir.mkdir(parents=True, exist_ok=True)
for name, fn, uses_here, dir_name in CASES:
    here = win_dir if uses_here else None
    result, exc = run_resolver(fn, True, "win32", exe=win_dir / "Tool.exe", here=here)
    check(f"{name}：Windows 打包放 exe 同级", exc is None and _under(result, win_dir),
          f"exc={exc!r} result={result}")
    check(f"{name}：Windows 目录名就是那一个（不另造同级目录）",
          _same(result, win_dir / dir_name), f"期望 {win_dir / dir_name}，实际 {result}")
# 1688 适配器的落点必须与基类**同一个目录**：以前是父类构造时先按自己的口径建一个
# ali1688_data（可写探针会真的 mkdir），适配器再把 _data_dir 改指到 b2bcollect_data，
# 于是安装目录里凭空多出一个空目录，而说明书对用户只承诺三个数据目录。
# 用**新目录**验：上面那条 Windows 用例已经把 win_dir/ali1688_data 建出来了（那是
# 被测代码在测父类时的正常行为），复用同一个目录只会把自己造的目录当成缺陷。
_pair = TMP / "win_pair"
_pair.mkdir(parents=True, exist_ok=True)
_site_dir, _e2 = run_resolver(site_adapters.Ali1688Site._stable_data_dir,
                              True, "win32", exe=_pair / "Tool.exe", here=_pair)
check("Ali1688Site 的落点与基类同名（安装目录里只有 b2bcollect_data）",
      _e2 is None and _same(_site_dir, _pair / "b2bcollect_data")
      and not (_pair / "ali1688_data").exists(),
      f"exc={_e2!r} result={_site_dir} 越界目录={sorted(p.name for p in _pair.iterdir())}")
# 覆写要在**父类调用点**生效才算修好：只在适配器 __init__ 里改属性的话，
# 上面这条断言照样绿，而父类那一遍 mkdir 仍然会留下空目录。
check("覆写发生在 _stable_data_dir 上（父类构造时就会走到适配器这一版）",
      "_stable_data_dir" in vars(site_adapters.Ali1688Site)
      and site_adapters.Ali1688Site._stable_data_dir
      is not ali1688_crawler.Ali1688Crawler._stable_data_dir,
      f"MRO={site_adapters.Ali1688Site.__mro__[:3]}")

# ── 4. 开发环境（未打包）→ 仍用各自模块目录，老数据/老 Cookie 不搬家 ──
dev_targets = {
    "cdp_base": (cdp_base.CdpSiteCrawler._stable_data_dir, HERE / "b2bcollect_data"),
    "site_adapters": (site_adapters._writable_data_dir, HERE / "b2bcollect_data"),
    "Ali1688Site": (site_adapters.Ali1688Site._stable_data_dir, HERE / "b2bcollect_data"),
    "qizhidao": (qizhidao_crawler._stable_data_dir, HERE.parent / "riskbird_build" / "riskbird_data"),
    "ali1688": (ali1688_crawler.Ali1688Crawler._stable_data_dir,
                HERE.parent / "ali1688_build" / "ali1688_data"),
}
for label, (fn, expect) in dev_targets.items():
    result, exc = run_resolver(fn, False, sys.platform)
    check(f"{label}：开发环境仍用模块目录", exc is None and result == expect,
          f"exc={exc!r} result={result} 期望 {expect}")

# ── 5. 崩溃现场直测：企知道 Cookie 路径在只读包内不再抛 OSError 30 ──
exe3, holder3 = make_app(read_only=True)
app3 = Path(exe3).parent.parent.parent
_ro_tree(app3)
result3, exc = run_resolver(qizhidao_crawler._stable_data_dir, True, "darwin", exe=exe3)
check("只读包内实例化 Cookie 路径不再 Errno 30", exc is None, repr(exc))
check("Cookie 目录退到包外兜底位置", result3 == FAKE_HOME / ".b2bcollect" / "riskbird_data",
      str(result3))
_rw_tree(app3)

# ── 6. 运行期**文件**的默认落点绝不相对当前工作目录（审计日志 / 列映射历史）──
# 第 1～5 节管的是目录（数据、Cookie），这一节管文件：同一类缺陷的两个面。这两个默认值
# 历史上写成裸文件名 "audit.log" / "mapping_history.json"，注释还写着「与程序同目录」，
# 实际含义却是**相对当前工作目录**——而打包态的 CWD 由「谁把进程拉起来」决定：访达双击
# .app 时是 /，重启链路里是 exe 同级，用户手敲时是他当时站着的目录。后果不是报错而是
# 静默失效：Mac 只读副本下写 / 被 except 吞掉＝审计日志根本没有；onefile 下写进退出即删
# 的 _MEI＝列映射推荐永远停在首次使用的空状态，用户只觉得「这功能没用」。
CWD_PROBE = TMP / "cwd_probe"
CWD_PROBE.mkdir(parents=True)
TARGETS = (("审计日志", audit_logger._default_log_file, audit_logger.LOG_FILE_NAME),
           ("映射历史", mapping_recommender._default_cache_file,
            mapping_recommender.HISTORY_FILE_NAME))
USER_FALLBACK = FAKE_HOME / tool_config.USER_CONFIG_DIR_NAME / tool_config.STATE_DIR_NAME
_old_cwd = os.getcwd()
_old_state_env = os.environ.get(tool_config._STATE_DIR_ENV_VAR)
_old_expanduser = os.path.expanduser
try:
    os.chdir(str(CWD_PROBE))

    # 6a 默认口径：绝对路径、目录由口径给、文件名由调用方给
    for label, getter, fname in TARGETS:
        got = getter()
        p = Path(got)
        check(f"{label}：默认落点是绝对路径（裸文件名等于跟着 CWD 跑）", p.is_absolute(), got)
        check(f"{label}：不落在当前工作目录里", CWD_PROBE.resolve() not in p.parents, got)
        check(f"{label}：调用方只报文件名、目录只由一份口径决定", p.name == fname, got)

    # 6b 状态目录被指到别处时，两个消费者一起搬（口径只有一份，不是各写一份默认值）
    env_dir = TMP / "state_from_env"
    os.environ[tool_config._STATE_DIR_ENV_VAR] = str(env_dir)
    for label, getter, fname in TARGETS:
        got = getter()
        check(f"{label}：状态目录指到哪就落到哪（两个消费者同一份口径）",
              Path(got).parent == env_dir, got)

    # 6c 真写得进去：CWD 站在别处，落点与落盘都得照常成立（这一条只有跑起来才知道）
    audit_logger.AuditLogger().log("import", "tester", {"n": 1})
    mapping_recommender.MappingRecommender().update(["公司名称"], {0: "company_name"})
    check("审计日志在 CWD 别处时真写得进去",
          (env_dir / audit_logger.LOG_FILE_NAME).is_file(), str(env_dir))
    check("列映射历史在 CWD 别处时真写得进去",
          (env_dir / mapping_recommender.HISTORY_FILE_NAME).is_file(), str(env_dir))
    check("CWD 那个目录里一个文件都没多（旧写法就是往这里丢文件）",
          not any(CWD_PROBE.iterdir()), str(sorted(CWD_PROBE.iterdir())))

    # 6d 三级退路逐条实测：首选口径崩 → 用户目录固定落点；tool_config 整块加载不出来 →
    #     仍然只认用户目录。任何一级都不许偷偷退成「相对当前目录」，那正是缺陷本体。
    _real_state_file = tool_config.state_file
    try:
        def _boom(name):
            raise RuntimeError("状态目录口径整体崩掉")

        tool_config.state_file = _boom
        for label, getter, fname in TARGETS:
            got = getter()
            check(f"{label}：首选口径崩掉时退到用户目录（第二退路也不认 CWD）",
                  Path(got) == USER_FALLBACK / fname, got)

        sys.modules["tool_config"] = None          # 第三退路：连口径模块都拿不到
        os.path.expanduser = lambda _p: str(FAKE_HOME)
        for label, getter, fname in TARGETS:
            got = getter()
            check(f"{label}：tool_config 没加载时仍落用户目录（最后退路）",
                  Path(got) == USER_FALLBACK / fname, got)
    finally:
        sys.modules["tool_config"] = tool_config
        tool_config.state_file = _real_state_file
        os.path.expanduser = _old_expanduser
finally:
    os.chdir(_old_cwd)
    if _old_state_env is None:
        os.environ.pop(tool_config._STATE_DIR_ENV_VAR, None)
    else:
        os.environ[tool_config._STATE_DIR_ENV_VAR] = _old_state_env

# ── 7. 口径模块自己（tool_config / deepseek 退路）也不许把数据落在包内 ──
# 第 1~2 节测的是「采集器」那一侧的落点。这一节测的是**给它们定口径的那一侧**：
# 2026-11-12 全局自审发现三处漏法，形状与本轮 Mac 崩溃同族、却更隐蔽（不崩、只悄悄写进包里）：
#   · tool_config.state_dir 的候选里有「exe 同级」那一级，而 Mac 的 .app 下它正好是
#     Contents/MacOS＝**包内**。它只排除了「随包内置」一级，没按形态排除这一级——
#     于是没被 Gatekeeper 隔离的 app（整包可写）会把审计日志、自检报告、列映射历史
#     全塞进应用包；覆盖升级或拖废纸篓卸载时，这些历史跟着一起没了；
#   · save_config 同一条纪律只写了「跳过随包内置」，Mac 下照样能把用户填的密钥写进
#     Contents/MacOS（读得到、下次升级被覆盖，用户只觉得「密钥又掉了」）；
#   · deepseek_expander 在「tool_config 没加载成功」时的退路直接写模块目录——打包态
#     模块目录就是包内，只读卷上抛 OSError，可写包里就是「保存成功但下次开机没了」。
# 前两处都必须在**包可写**的夹具下测：只读卷下写探针会替被测代码挡住包内路径，那种绿
# 是假绿（教训见 _ro_tree 的注释）。第三处（deepseek）测的是口径模块缺席时的落点。
# 而 7a0 比这三条都硬：只要「包旁可写」，候选 1 就先命中，永远走不到包内那一级，
# 靠返回值断言的 1~3 节对这种形态错误是瞎的——所以判据必须直接摊开**候选链**按形态断言。
# 这条是反向对照实测出来的：把两处实现都退回旧写法跑一遍，1~3 节全绿、只有 7a0 变红，
# 才确认它抓的是真缺陷而不是夹具。
exe7, holder7 = make_app()                     # 不设只读：整包可写
app_dir7 = Path(exe7).parent.parent.parent
macos7 = Path(exe7).parent

# 7a0 候选链必须按**形态**里没有包内这一级——与权限无关，整包可写时也一样成立。
# 为什么单独立一条：7a/7b 那种「让写探针替我选」的判据，只要包旁那一级可写就永远
# 命中候选 1，走不到包内那一级，于是旧实现在这条夹具下也是绿的（本轮反向对照实测
# 到的假绿就是这么来的）。把候选链摊开直接断言，缺陷形态本身才算被钉住。
CANDIDATE_CASES = (
    ("tool_config.state_dir_candidates", lambda: tool_config.state_dir_candidates(),
     holder7 / tool_config.STATE_DIR_NAME,
     FAKE_HOME / tool_config.USER_CONFIG_DIR_NAME / tool_config.STATE_DIR_NAME),
    ("runtime_paths.candidate_dirs", lambda: runtime_paths.candidate_dirs("riskbird_data"),
     holder7 / "b2bcollect_data" / "riskbird_data",
     FAKE_HOME / runtime_paths.USER_DIR_NAME / "riskbird_data"),
)
for label, getter, expect_first, expect_home in CANDIDATE_CASES:
    cands, exc7a0 = run_resolver(getter, True, "darwin", exe=exe7)
    check(f"{label}：列候选不抛异常", exc7a0 is None, repr(exc7a0))
    inside = [str(p) for p in (cands or []) if _under(p, app_dir7)]
    check(f"{label}：候选链里一处都不在 .app 内部", not inside,
          f"{inside} 落在 {app_dir7} 内（按形态排除，不能等写探针碰运气）")
    check(f"{label}：候选链第一条就是包旁那一级", cands and _same(cands[0], expect_first),
          f"首候选={cands[0] if cands else None} 期望 {expect_first}")
    check(f"{label}：候选链里备着一个包外的家（包旁与包内都写不进时有的退）",
          bool(cands) and any(_same(p, expect_home) for p in cands),
          f"期望含 {expect_home}，实际 {cands}")

# 7a state_dir / state_file：包可写时也必须落在包旁，不进 .app
for label, getter, expect_parent in (
        ("状态目录", lambda: tool_config.state_dir(), holder7),
        ("状态文件", lambda: tool_config.state_file("audit.log").parent, holder7)):
    result, exc = run_resolver(getter, True, "darwin", exe=exe7)
    check(f"{label}：只读与否都不抛异常", exc is None, repr(exc))
    check(f"{label}：绝不落在 .app 内部", not _under(result, app_dir7),
          f"{result} 在 {app_dir7} 内")
    check(f"{label}：优先落在包旁那一级", _under(result, expect_parent),
          f"{result} 不在 {expect_parent} 下")
    check(f"{label}：目录名就是口径里那一个",
          Path(str(result)).name == tool_config.STATE_DIR_NAME, str(result))

# 7b 包旁写不进、**但包内可写**——这才是旧实现真正会翻车的那一档权限组合，
# 所以夹具要精确做成「外锁内开」：整棵 holder（含 7a 那一路已经被探针真建出来的包旁
# 状态目录，只 chmod holder 本身锁不住它——mkdir(exist_ok=True) 对已存在目录不报错，
# 写文件也照样成功）全部只读，再单独把 Contents/MacOS 放开。反过来说，整棵全锁也不对：
# 那样写探针会替被测代码在包内那一级先失败，绿的是权限而不是实现（教训同 _ro_tree）。
_ro_tree(holder7)                             # 整棵锁死，含 7a 那一路已建出来的包旁目录
_rw(macos7)                                   # 再单独放开包内那一级：外锁内开
try:
    result7b, exc7b = run_resolver(lambda: tool_config.state_dir(), True, "darwin", exe=exe7)
    check("状态目录：包旁写不进、包内可写时退到用户目录", exc7b is None
          and _same(result7b, FAKE_HOME / tool_config.USER_CONFIG_DIR_NAME / tool_config.STATE_DIR_NAME),
          f"exc={exc7b!r} result={result7b}")
    check("状态目录：退路也不落在 .app 内部", not _under(result7b, app_dir7), str(result7b))
    # 直接验现场：包内那一级连目录都没被建出来（旧写法在这一步会真 mkdir 成功）
    check("状态目录：包内没有留下任何被建出来的状态目录",
          not (macos7 / tool_config.STATE_DIR_NAME).exists(),
          f"{macos7 / tool_config.STATE_DIR_NAME} 被建出来了")
finally:
    _rw_tree(holder7)

# 7c save_config：包内那一份 config.json 只许读、不许成为保存落点
in_bundle_cfg = macos7 / tool_config.CONFIG_FILE_NAME
in_bundle_cfg.parent.mkdir(parents=True, exist_ok=True)
in_bundle_cfg.write_text('{"deepseek_api_key": "出厂随包密钥"}', encoding="utf-8")
before_bytes = in_bundle_cfg.read_bytes()
exc7c = None
ok7c, where7c = (None, None)
try:
    with _switches(frozen=True, platform="darwin", executable=str(exe7)):
        ok7c, where7c = tool_config.save_config({"deepseek_api_key": "用户自己填的"})
except Exception as e:                          # noqa: BLE001
    exc7c = e
check("保存配置：不抛异常", exc7c is None, repr(exc7c))
check("保存配置：返回成功并说明落点", ok7c is True and str(where7c), f"{ok7c} {where7c}")
check("保存配置：落点不在 .app 内部", not _under(where7c, app_dir7), str(where7c))
check("保存配置：包内那份出厂配置一个字节都没被改（升级时它会被覆盖，改它就是改产物）",
      in_bundle_cfg.read_bytes() == before_bytes, in_bundle_cfg.read_text(encoding="utf-8"))
in_bundle_cfg.unlink()

# 7d deepseek 退路：tool_config 整块拿不到时，缓存与密钥都只许落用户目录
exe7d, holder7d = make_app()
app_dir7d = Path(exe7d).parent.parent.parent
_real_tc_module = sys.modules.get("tool_config")
try:
    def _fallback_probe():
        importlib.reload(deepseek_expander)
        return (deepseek_expander.fallback_dir(),
                deepseek_expander.cache_file())

    sys.modules["tool_config"] = None           # 让 import tool_config 当场失败
    (fb, cf), exc7d = run_resolver(_fallback_probe, True, "darwin", exe=exe7d)
    check("deepseek 退路：不抛异常", exc7d is None, repr(exc7d))
    check("deepseek 退路：fallback_dir 不在 .app 内", not _under(fb, app_dir7d), str(fb))
    check("deepseek 退路：缓存落点不在 .app 内", not _under(cf, app_dir7d), str(cf))
    check("deepseek 退路：落点是唯一的用户目录",
          _same(fb, FAKE_HOME / tool_config.USER_CONFIG_DIR_NAME), str(fb))
    saved_ok = None
    try:
        with _switches(frozen=True, platform="darwin", executable=str(exe7d)):
            saved_ok = deepseek_expander.save_api_key("sk-退路写入的密钥")
    except Exception as e:                      # noqa: BLE001
        saved_ok = f"抛出 {type(e).__name__}: {e}"
    check("deepseek 退路：保存密钥成功（旧写法在这一步会把包带崩或骗用户「已保存」）",
          saved_ok is True, repr(saved_ok))
    written = FAKE_HOME / tool_config.USER_CONFIG_DIR_NAME / "config.json"
    check("deepseek 退路：密钥真的落在用户目录里", written.is_file(), str(written))
    check("deepseek 退路：包内没有被写出任何 config.json",
          not (app_dir7d / "Contents" / "MacOS" / "config.json").exists(), "")
    read_back = None
    try:
        with _switches(frozen=True, platform="darwin", executable=str(exe7d)):
            read_back = deepseek_expander._module_dir_key()
    except Exception as e:                      # noqa: BLE001
        read_back = f"抛出 {type(e).__name__}: {e}"
    check("deepseek 退路：写过的密钥下次还能读出来（写的地方比读的地方窄就是「已保存」变空）",
          read_back == "sk-退路写入的密钥", repr(read_back))
finally:
    sys.modules["tool_config"] = _real_tc_module
    importlib.reload(deepseek_expander)         # 恢复成真实口径，后面的用例与导入都按它跑

# ── 8. 最后一级退路 + 不赌包布局深度 + 一个形状只许一份实现 ──
# 五路采集器与状态目录一起过。这一节是 2026-11-12 第二轮全局自审补的：第 1~3 节把
# 每个实现都按「只读整卷 / 包可写 / Windows / 开发态」跑了一遍，唯独没问过四件事——
# 全部落点都写不进去时怎么办、包内布局比 Contents/MacOS 更深时还认不认得 .app、
# 排障用的环境变量指的是容器还是唯一目录、以及同一形状是不是真的只有一份实现。
# 8a 「包旁与用户目录**都**写不进去」的机器是真实存在的：主目录被安全软件的文件夹保护
#    接管、被同步盘换成占位符、配额满、域控漫游档案只读。旧写法在这种机器上把
#    primary 原样返回，调用方一句 mkdir 就 OSError 崩在启动那一下——与本轮 Mac 故障
#    同一种收场，只是触发条件更苛刻，所以也最容易没人管。现在必须退到系统临时目录，
#    并且返回的这一个要真写得进去（宁可重启后丢，也不许把窗口带没）。
def _expect_temp(dir_name: str) -> Path:
    """数据目录那一路的最后一级：``<系统临时目录>/b2bcollect_<目录名>``（全体同一规则，
    与 runtime_paths.candidate_dirs 逐字一致）。"""
    return Path(tempfile.gettempdir()) / f"b2bcollect_{dir_name}"


LAST_RESORT_CASES = [(n, fn, uh, _expect_temp(d)) for n, fn, uh, d in CASES]
# 状态目录那一路用的是自己的目录名（b2bcollect_state），不加 b2bcollect_ 前缀：两边都是
# 「无路可走时的一次性目录」，语义相同而名字各按各的常量走。这里逐字写出来而不是强行
# 统一，是因为统一会让 STATE_DIR_NAME 这个常量在临时目录里换个说法——下次排障对不上号。
LAST_RESORT_CASES.append(("tool_config.state_dir",
                          lambda: tool_config.state_dir(), False,
                          Path(tempfile.gettempdir()) / tool_config.STATE_DIR_NAME))

exe8, holder8 = make_app(read_only=True)        # 包旁与整包都只读
app_dir8 = Path(exe8).parent.parent.parent
# 用户目录这一级也要写不进去，而且必须 _ro_tree 整棵：前几节已经真建出过
# ``~/.b2bcollect/<各目录>``，只 chmod 家目录本身锁不住已存在的子目录（mkdir
# exist_ok 不报错、写文件照旧成功）——那样退到用户目录是**正当行为**，测不到最后一级。
_ro_tree(FAKE_HOME)
_tmp_made = []
try:
    for name, fn, uses_here, expect_temp in LAST_RESORT_CASES:
        here = exe8.parent if uses_here else None
        result, exc = run_resolver(fn, True, "darwin", exe=exe8, here=here)
        check(f"{name}：包旁与用户目录都写不进时不抛异常", exc is None, repr(exc))
        check(f"{name}：退到最后一级系统临时目录", _same(result, expect_temp),
              f"期望 {expect_temp}，实际 {result}")
        check(f"{name}：最后一级仍在应用包外", not _under(result, app_dir8), str(result))
        probe8 = result / ".probe"
        probe8.write_text("1", encoding="utf-8")   # 真写一次：返回的必须是可用目录
        probe8.unlink()
        _tmp_made.append(result)
finally:
    _rw_tree(FAKE_HOME)
    _rw_tree(holder8)
    for d in _tmp_made:                            # 不在这次机器上留下临时目录垃圾
        shutil.rmtree(d, ignore_errors=True)

# 8b 形态判定不许把 Contents/MacOS 的深度写死：把执行程序放到 .app 里更深一层。
# 写死了的那一路会认不出 .app，于是「exe 同级」= 包内又成了首选落点，而且整包可写时
# 写探针还会替它 succeed（第 7 节 7a0 说的就是这个形状）。口径本体两处都是向上找
# .app，被对齐的 cdp_base / site_adapters 以前是按深度 2 判——这条就是它们的老账。
deep_app = TMP / "deep" / "Deep.app"
deep_macos = deep_app / "Contents" / "Frameworks" / "MacOS"
deep_macos.mkdir(parents=True)
(deep_macos / "B2bCollectTool").write_text("exe", encoding="utf-8")
holder_deep = deep_app.parent
for name, fn, uses_here, dir_name in CASES + [("tool_config.state_dir",
                                               lambda: tool_config.state_dir(), False,
                                               tool_config.STATE_DIR_NAME)]:
    here = deep_macos if uses_here else None
    result, exc = run_resolver(fn, True, "darwin",
                               exe=deep_macos / "B2bCollectTool", here=here)
    check(f"{name}：执行程序在包里更深一层时仍认出 .app（不赌布局深度）",
          exc is None and not _under(result, deep_app), f"exc={exc!r} result={result}")
    if name == "tool_config.state_dir":
        expect_deep = holder_deep / tool_config.STATE_DIR_NAME
    else:
        expect_deep = _expect_sibling(holder_deep, dir_name)
    check(f"{name}：更深一层时数据照旧落包旁", _same(result, expect_deep),
          f"期望 {expect_deep}，实际 {result}")

# 8c 指定落点的环境变量是**容器**，不是「那一个目录」：问同一处口径的四路采集器（风鸟/
# 爱企查/企知道/1688）共用这一个开关时，各家占自己那个 <name> 子目录。这条管的是开关的
# 语义——远程排障让人把那个目录整个发回来，收到一堆混在一起、分不清是谁写的文件，等于
# 白要一次；「指了哪就只落哪」那半句则与第 7 节 7a0 同一条纪律：候选链要说真话。
env_base = TMP / "env_container"
os.environ[runtime_paths.DATA_DIR_ENV_VAR] = str(env_base)
try:
    runtime_paths.reset_cache()
    landing = {}
    for dir_name in ("riskbird_data", "ali1688_data"):
        landing[dir_name] = runtime_paths.stable_data_dir(dir_name)
        check(f"环境变量容器：{dir_name} 落在容器下的同名子目录",
              _same(landing[dir_name], env_base / dir_name),
              f"期望 {env_base / dir_name}，实际 {landing[dir_name]}")
    check("环境变量容器：两路采集器不共用同一个目录（混在一起就分不清谁写的）",
          landing["riskbird_data"] != landing["ali1688_data"], str(landing))
    probe8c = landing["ali1688_data"] / ".probe"
    probe8c.write_text("1", encoding="utf-8")
    probe8c.unlink()
    cands8c = runtime_paths.candidate_dirs("riskbird_data")
    check("环境变量容器：候选链只剩这一条（不再往下探，说法与实际一致）",
          len(cands8c) == 1 and _same(cands8c[0], env_base / "riskbird_data"), str(cands8c))
finally:
    os.environ.pop(runtime_paths.DATA_DIR_ENV_VAR, None)
    runtime_paths.reset_cache()
    shutil.rmtree(env_base, ignore_errors=True)

# 8d 落点实现只许有一份：把口径本身换成哨兵，看每一路**是不是真的去问它**。
# 为什么行为比对（8a/8b）不够：那些用例只在「这一份实现恰好也写对了」的时候绿。谁在
# 本地再抄一份候选链——本轮 ali1688 留下的就是第四份，缺最后一级退路、还把包内深度写死
# ——行为可以照样对上好几天，而下次改口径的人只会改到自己那一处。打桩问得出「你有没有
# 去问」，这一问才把「一个形状一份实现」这条纪律变成判据而不是注释里的愿望。
_real_cdp_sdd = cdp_base.CdpSiteCrawler._stable_data_dir
_real_paths_sdd = ali1688_crawler._paths.stable_data_dir
try:
    sentinel = TMP / "sentinel_from口径"
    cdp_base.CdpSiteCrawler._stable_data_dir = staticmethod(lambda *a, **k: sentinel)
    check("site_adapters._writable_data_dir 只是 cdp_base 那一处的门面（不再自带链）",
          _same(site_adapters._writable_data_dir(), sentinel),
          str(site_adapters._writable_data_dir()))
    check("Ali1688Site 覆写的 _stable_data_dir 也走同一处",
          _same(site_adapters.Ali1688Site._stable_data_dir(), sentinel),
          str(site_adapters.Ali1688Site._stable_data_dir()))

    seen = {}

    def _spy(name=None, dev_dir=None):
        seen["name"], seen["dev"] = name, dev_dir
        return sentinel

    ali1688_crawler._paths.stable_data_dir = _spy
    got8d = ali1688_crawler.Ali1688Crawler._stable_data_dir()
    check("1688 这一路问的是 runtime_paths（第四份候选链不许回来）",
          _same(got8d, sentinel), str(got8d))
    check("1688 报出的目录名与开发态锚点（老种子库不许被口径搬家）",
          seen.get("name") == "ali1688_data"
          and _same(Path(seen.get("dev") or "."), HERE.parent / "ali1688_build"), str(seen))
finally:
    cdp_base.CdpSiteCrawler._stable_data_dir = _real_cdp_sdd
    ali1688_crawler._paths.stable_data_dir = _real_paths_sdd

# ── 9. 「受系统隐私保护的目录」这一档口径：两份实现必须长成同一种样子 ──
# 动机是本轮 Mac 实机那次「双击图标，什么也没发生，也没有报错」：app 放在桌面时，
# 候选链的「包旁」那一级就是 ~/Desktop/…，而定落点靠**写探针**——在桌面上创建一个文件
# 正是被内核扣住等用户点「允许」的那类动作（实测扣住四分钟以上）。修法是在两条候选链
# 上各加一道语义滤网，但这份判据必须写两遍：riskbird_build 与 b2bcollect_build 是两个
# 独立打包的树，反向 import 就要在「谁先 import」上赌运气（同 8d 那套纪律，只是这里
# 重复是被接受的形状）。既然接受重复，就必须有对拍：谁改出自己的说法就红在这里。
print("\n═══ 9. 受保护目录判据：名单同字、答案同口径、滤网真接线 ═══")

check("9a 两份实现的受保护名单逐字一致（改名要一起改，不许一边先跑）",
      tuple(runtime_paths.PROTECTED_USER_DIRS) == tuple(tool_config.PROTECTED_USER_DIRS),
      f"runtime_paths={runtime_paths.PROTECTED_USER_DIRS} tool_config={tool_config.PROTECTED_USER_DIRS}")

_desktop = FAKE_HOME / "Desktop"
_samples = [
    # (样例路径, 期望：True=受保护，False=不受保护)
    (_desktop, True),
    (_desktop / "Fake.app", True),                      # 子目录同样受保护
    (_desktop / "b2bcollect_data" / "riskbird_data", True),
    (FAKE_HOME / "desktop", True),                      # 大小写不敏感（卷可能不区分）
    (FAKE_HOME / "Documents", True),
    (FAKE_HOME / "Downloads" / "x", True),
    (FAKE_HOME, False),                                  # 家目录本身不受保护
    (FAKE_HOME / "other_dir", False),                    # 不是那三个语义目录
    (Path(tempfile.gettempdir()) / "b2bcollect_riskbird_data", False),
    (Path("/Applications/Fake.app/Contents/MacOS"), False),
]
for _sp, _want in _samples:
    with _switches(platform="darwin"):
        _a = runtime_paths.is_protected_user_dir(_sp)
        _b = tool_config.is_protected_user_dir(_sp)
    check(f"9b 样例判据两份一致且符合语义：{_sp} → {_want}",
          _a == _b == _want, f"runtime_paths={_a} tool_config={_b} 期望={_want}")

with _switches(platform="win32"):
    _w = [runtime_paths.is_protected_user_dir(_desktop),
          tool_config.is_protected_user_dir(_desktop)]
check("9c Windows 上两份都判不受保护（把桌面也降级会让首选落点白白废掉）",
      _w == [False, False], str(_w))

# 9d 口径一致性：候选链里 exe 那一级是 **resolve 过**的，而 Path.home() 未必。Mac 上
# /var 与 /private/var 是同一个地方的两个名字，只按一边比会得到「两边都明明是桌面、
# 就是对不上」的漏判，而漏判的方向恰好是「不加超时」——正是要防的那次挂死。
# 这个前提不靠本机 temp 目录碰巧有软链：夹具自己给假家目录挂一个别名链接，两种写法
# 就是同目录的两个名字（造不出来时如实报跳过，不拿恒真的断言冒充验过）。
_link_home = TMP / "home_link"
try:
    os.symlink(FAKE_HOME, _link_home)
except OSError:                                    # Windows 默认不给普通用户建符号链接
    _link_home = None
if _link_home is None:
    print("  ⏭  9d 跳过：本机建不出符号链接，「同一个目录的两种写法」这个前提造不出来"
          "（判的是 macOS 那一档，缺夹具时不拿假绿代替）")
else:
    _old_home_fn = Path.home
    Path.home = classmethod(lambda cls: _link_home)      # 家目录用**未解析**的别名写法
    _literal_desktop = str(_link_home / "Desktop" / "b2bcollect_data")
    _resolved_desktop = str((_link_home / "Desktop" / "b2bcollect_data").resolve())
    try:
        check("9d 夹具前提成立：同一个桌面目录确实有两种写法（不然下面白测）",
              _literal_desktop != _resolved_desktop,
              f"字面={_literal_desktop} 解析={_resolved_desktop}")
        with _switches(platform="darwin"):
            _both = [runtime_paths.is_protected_user_dir(_literal_desktop),
                     runtime_paths.is_protected_user_dir(_resolved_desktop),
                     tool_config.is_protected_user_dir(_literal_desktop),
                     tool_config.is_protected_user_dir(_resolved_desktop)]
        check("9d 两种写法都判受保护（只按一边比的实现会漏判，而漏判＝不加超时）",
              _both == [True] * 4, str(_both))
    finally:
        Path.home = _old_home_fn

# ── 9e/9f/9g：app 真放在桌面上时，两条链都必须躲开受保护目录 ──
# 这里不新建第 9 条候选链，只把第 1~8 节那套 Fake.app 挪到假家目录的桌面上：形态判定
# （_app_bundle_dir / app_bundle_dir）认的是「向上找 .app」，与它在哪无关，所以这条用例
# 验的只有滤网一件事，正合「一个形状一份实现」里「滤网是真接在链上的」这一问。
desk_app = _desktop / "Fake.app"
desk_macos = desk_app / "Contents" / "MacOS"
desk_macos.mkdir(parents=True, exist_ok=True)
desk_exe = desk_macos / "B2bCollectTool"
desk_exe.write_text("exe", encoding="utf-8")

_saved_env = {k: os.environ.get(k) for k in
              (runtime_paths.DATA_DIR_ENV_VAR, tool_config._STATE_DIR_ENV_VAR)}
for _k in _saved_env:
    os.environ.pop(_k, None)
try:
    runtime_paths.reset_cache()
    with _switches(frozen=True, platform="darwin", executable=str(desk_exe)):
        _rp_chain = runtime_paths.candidate_dirs("riskbird_data")
        _tc_chain = tool_config.state_dir_candidates()
        check("9e1 数据侧候选链一条都不在受保护目录（app 就在桌面上）",
              not any(runtime_paths.is_protected_user_dir(d) for d in _rp_chain), str(_rp_chain))
        check("9e2 状态侧候选链一条都不在受保护目录（同一句话的另一个消费者）",
              not any(tool_config.is_protected_user_dir(d) for d in _tc_chain), str(_tc_chain))

        # 9f 落到实处：定落点那两个函数在「桌面 app」形态下给出的目录不在受保护目录，
        # 而且**真写得进去**（只说「换个目录」不说「换的那个能用」等于把故障挪个位置）。
        _rp_got = runtime_paths.stable_data_dir("riskbird_data")
        _tc_got = tool_config.state_dir()
        for _lbl, _got, _fn in (("9f1 数据落点", _rp_got, runtime_paths.is_protected_user_dir),
                                ("9f2 状态落点", _tc_got, tool_config.is_protected_user_dir)):
            check(f"{_lbl}：避开受保护目录且不落在包内",
                  not _fn(_got) and not _under(_got, desk_app), str(_got))
            _probe = Path(_got) / ".probe9f"
            _probe.write_text("1", encoding="utf-8")
            _probe.unlink()
            check(f"{_lbl}：换过去的落点真的写得进去（不是拿猜测当退路）", _probe.parent.is_dir(),
                  str(_got))

    # 9g 反向对照：把两份判据都摘成「永远不受保护」，桌面那一级必须回到候选链里。
    # 少了这一条，9e 就只是「链里恰好没有桌面目录」的绿灯——滤网写没接线、接在哪一级、
    # 名单有没有漂，三件事全都看不出来（8d 说的正是这一类：行为比对抓不住形状走样）。
    _real_rp = runtime_paths.is_protected_user_dir
    _real_tc = tool_config.is_protected_user_dir
    runtime_paths.is_protected_user_dir = lambda p: False
    tool_config.is_protected_user_dir = lambda p: False
    try:
        runtime_paths.reset_cache()
        with _switches(frozen=True, platform="darwin", executable=str(desk_exe)):
            _back = [d for d in runtime_paths.candidate_dirs("riskbird_data")
                     if _real_rp(d)]
            _back_tc = [d for d in tool_config.state_dir_candidates() if _real_tc(d)]
        check("9g1 摘掉数据侧判据后桌面候选回来了（滤网确实接在这一级）", _back, str(_back))
        check("9g2 摘掉状态侧判据后桌面候选回来了（两处接线都要真存在）", _back_tc, str(_back_tc))
    finally:
        runtime_paths.is_protected_user_dir = _real_rp
        tool_config.is_protected_user_dir = _real_tc
finally:
    for _k, _v in _saved_env.items():
        if _v is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _v
    runtime_paths.reset_cache()
    shutil.rmtree(_desktop, ignore_errors=True)

# ── 10. 落点只由形态决定：本机历史不参与择路，指定落点就是唯一答案 ──────────
# 由来（2026-11-13 自审纠正）：上一版的 tool_config.choose_writable 与
# runtime_paths._choose 里写着「可写的候选里挑已经存着数据的那个」，动机是好心——别让
# 升级把攒了几年的 Cookie 留在原地。但它把「这台机器以前发生过什么」变成了落点的一部分：
# 开发机上 ~/.b2bcollect 只要有过往留下的任何一个文件，第 2 节那条「打包态数据优先落在
# 程序同级（看得见、也拷得走）」就当场红，换一台干净机器又绿。同一份包在两台电脑上数据
# 住在两个地方、用户拷走工具目录拷不走数据，正是本项目立过无数次判据要防的那件事。
# 不可再生数据因此挪到**显式接管**（第 13 节），这一节钉的是「择路本身确定性」，并且
# 把旧口径原样请回来做一次差分：夹具输入要是分辨不了新旧两种口径，10b/10c 就是装饰。
print("\n═══ 10. 择路确定性：顺序 + 真能写，本机历史不参与 ═══")

_c10 = TMP / "c10"
_a10, _b10 = _c10 / "a", _c10 / "b"
_a10.mkdir(parents=True), _b10.mkdir(parents=True)
(_b10 / "seen_store_1688.json").write_text("老数据", encoding="utf-8")
check("10a 夹具前提成立：两个候选都可写，只有第二个存着数据（不然下面白测）",
      tool_config.probe_writable(_a10) and tool_config.probe_writable(_b10)
      and not tool_config.holds_tool_data(_a10) and tool_config.holds_tool_data(_b10))
check("10b tool_config 择路按候选序，不跟着「哪一级已经有数据」走",
      _same(tool_config.choose_writable([_a10, _b10]), _a10),
      f"期望 {_a10}，实际 {tool_config.choose_writable([_a10, _b10])}")
check("10c runtime_paths 那一镜像实现同口径",
      _same(runtime_paths._choose([_a10, _b10]), _a10),
      f"期望 {_a10}，实际 {runtime_paths._choose([_a10, _b10])}")


def _choose_old_rule(cands, markers=()):
    """旧口径**原样**请回来当反向对照：可写的候选里挑已经存着数据的那个。"""
    writable = [d for d in cands if tool_config.probe_writable(d)]
    return next((d for d in writable if tool_config.holds_tool_data(d, markers)),
                writable[0] if writable else None)


check("10d 反向对照：这条输入真的分辨得了新旧口径（旧口径会选 b）",
      _same(_choose_old_rule([_a10, _b10]), _b10),
      "旧口径也选了 a——那 10b/10c 什么都没钉，只是恰好两个答案一样")

# 10e 「有数据」不等于「能用」：写不进去的候选必须被跳过，哪怕它是唯一存着数据的那个
_c10h = TMP / "c10hist"
_old_place, _new_place = _c10h / "old", _c10h / "new"
_old_place.mkdir(parents=True), _new_place.mkdir(parents=True)
(_old_place / "cookies.json").write_text("攒了很久的登录态", encoding="utf-8")
_ro(_old_place)
try:
    check("10e 夹具前提：这个候选里真有数据但写不进去（顺序之外还要真能写）",
          tool_config.holds_tool_data(_old_place)
          and not tool_config.probe_writable(_old_place))
    check("10f 有数据但写不进去的候选照样被跳过（两份实现同口径）",
          _same(tool_config.choose_writable([_old_place, _new_place]), _new_place)
          and _same(runtime_paths._choose([_old_place, _new_place]), _new_place),
          f"tool_config={tool_config.choose_writable([_old_place, _new_place])} "
          f"runtime_paths={runtime_paths._choose([_old_place, _new_place])}")
finally:
    _rw(_old_place)

# 10g 一个都写不进去时给 None，而不是替调用方挑一个写不进去的目录：上一轮 Mac 崩在启动
# 那一下的同一种收场就是「择路函数把不可写的目录原样返回，调用方一句 mkdir 就炸」。
# 各调用方的退路不同（状态目录退用户目录、数据目录退第一候选、配置目录退持久位置），
# 所以「退到哪一级」由调用方说，这里只钉「不许在这里替它决定」。
_dead1 = TMP / "c10dead"
_dead1.mkdir()
_ro(_dead1)
try:
    _d1 = _dead1 / "x"
    _d2 = _dead1 / "y"
    check("10g 全都写不进去时两份实现都给 None（退路由调用方决定）",
          tool_config.choose_writable([_d1, _d2]) is None
          and runtime_paths._choose([_d1, _d2]) is None,
          f"tool_config={tool_config.choose_writable([_d1, _d2])} "
          f"runtime_paths={runtime_paths._choose([_d1, _d2])}")
finally:
    _rw(_dead1)

# 10h 指定落点（环境变量）这一档：两侧都是「唯一答案 + 容器语义 + 不过受保护滤网」。
# 以前只有 riskbird 那一侧有开关，CDP 那 12 路的种子库/Cookie 压根指不动——远程排障说
# 「指个目录看看」时，「指了不生效」和「压根没有这个开关」在日志里是同一句话。
_env_saved = {k: os.environ.get(k) for k in (tool_config.DATA_DIR_ENV_VAR,
                                             runtime_paths.DATA_DIR_ENV_VAR)}
try:
    _pin_root = FAKE_HOME / "Desktop" / "pinned"          # 故意指到受保护目录
    os.environ[tool_config.DATA_DIR_ENV_VAR] = str(_pin_root)
    _got_cdp = cdp_base.CdpSiteCrawler._stable_data_dir()
    check("10h1 指了就是唯一答案：即便指的是受保护目录也照办（这是显式说出来的排障意图）",
          _same(_got_cdp, _pin_root / "b2bcollect_data"), str(_got_cdp))
    check("10h2 指定落点不跟着形态漂：源码态与打包态给的是同一个答案",
          all(_same(run_resolver(cdp_base.CdpSiteCrawler._stable_data_dir,
                                 frozen, "darwin", exe=make_app()[0])[0],
                    _pin_root / "b2bcollect_data")
              for frozen in (False, True)),
          "某一形态下候选链赢了环境变量——「我明明指了目录」这句话又不可信了")
    os.environ[tool_config.DATA_DIR_ENV_VAR] = "   "     # 空白值必须当成没指
    check("10h3 空白值算「没指」，不算「指到当前工作目录」（否则数据会写进 CWD）",
          tool_config.env_data_dir("b2bcollect_data") is None
          and not _same(cdp_base.CdpSiteCrawler._stable_data_dir(), Path.cwd()))
    os.environ[runtime_paths.DATA_DIR_ENV_VAR] = str(_pin_root)
    _g1 = runtime_paths.stable_data_dir("riskbird_data")
    _g2 = runtime_paths.stable_data_dir("ali1688_data")
    check("10h4 容器语义两侧一致：一个开关，下面每个消费者各占一个子目录",
          _same(_g1, _pin_root / "riskbird_data") and _same(_g2, _pin_root / "ali1688_data")
          and not _same(_g1, _g2), f"{_g1} / {_g2}")
    check("10h5 两个开关各管各的：错指另一侧的变量不许把这一路的落点带走",
          tool_config.env_data_dir("b2bcollect_data") is None,
          "B2B_RISKBIRD_DATA_DIR 被 tool_config 读了——两个开关其实是一个")
finally:
    for _k, _v in _env_saved.items():
        if _v is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _v
    runtime_paths.reset_cache()

# ── 11. 受保护目录那道滤的接线方向：少滤会挂死启动，多滤会搬家 ──────────────
# 第 9 节钉的是「判据本身」与另外两条链；这一节钉**三处接线**的两个方向：
#   · 少滤（本处以前就没滤）：app 放在桌面时「包旁」那一级就是 ~/Desktop/…，而定落点靠
#     写探针——在桌面上建文件正是实测被内核扣住的那类动作，一次扣住就把「双击打不开工具」
#     换来换去地复现在启动那一下；
#   · 多滤（riskbird 那一侧的第一版）：源码态锚点也被滤掉，而本仓库就放在
#     ~/Desktop/PythonDemo 下面 → 四个采集器的数据目录一起挪去 ~/.b2bcollect，开发机上
#     攒了几年的 Cookie 留在原地没人读，症状是「企知道/风鸟/爱企查都要重新登录、1688
#     种子库读不到」。锚点是代码自己所在的目录，进程刚从那里 import 成功，访问权已成立。
print("\n═══ 11. 滤网三处接线的两个方向：少滤挂启动、多滤搬数据 ═══")

_desk2 = FAKE_HOME / "Desktop"
_desk2.mkdir(parents=True, exist_ok=True)
_d2app = _desk2 / "Fake2.app"
_d2macos = _d2app / "Contents" / "MacOS"
_d2macos.mkdir(parents=True)
_d2exe = _d2macos / "B2bCollectTool"
_d2exe.write_text("exe", encoding="utf-8")
_env11 = {k: os.environ.get(k) for k in (tool_config.DATA_DIR_ENV_VAR,
                                         runtime_paths.DATA_DIR_ENV_VAR,
                                         tool_config._STATE_DIR_ENV_VAR)}
for _k in _env11:
    os.environ.pop(_k, None)


def _cdp_answer(exe, filter_fn=None, frozen=True):
    """在指定形态下问一次 cdp_base 的落点；filter_fn=None 表示「把滤网摘掉」."""
    real_fn = tool_config.is_protected_user_dir
    if filter_fn is not None:
        tool_config.is_protected_user_dir = filter_fn
    try:
        with _switches(frozen=frozen, platform="darwin",
                       **({"executable": str(exe)} if exe is not None else {})):
            return cdp_base.CdpSiteCrawler._stable_data_dir()
    finally:
        tool_config.is_protected_user_dir = real_fn


try:
    _got11 = _cdp_answer(_d2exe)
    check("11a cdp_base（12 路的种子库）在「桌面 app」形态下给出的落点不在受保护目录",
          tool_config.is_protected_user_dir(_got11) is False and not _under(_got11, _d2app),
          str(_got11))
    _p11 = Path(_got11) / ".probe11"
    _p11.write_text("1", encoding="utf-8")
    _p11.unlink()
    check("11b 换过去的落点真写得进去（只说「换个目录」不说「那个能用」＝把故障挪个位置）",
          _p11.parent.is_dir(), str(_got11))

    # 11c 反向对照：这一处的滤网要真接在链上，不是「这台机器恰好没把 app 放桌面」。
    # 手法与第 8 节同一条纪律——把判据换成哨兵问被测代码「有没有去问它」：摘掉判据之后
    # 答案必须**变成**受保护的那一级，否则上面两条断言抓不到任何故障。
    _off11 = _cdp_answer(_d2exe, filter_fn=lambda p: False)
    _real_fn11 = tool_config.is_protected_user_dir
    check("11c 摘掉判据之后 cdp_base 落在桌面（滤网确实接在这一级）",
          _real_fn11(_off11), f"摘掉判据后仍然是 {_off11}：这一处滤网可能压根不存在，"
                              f"或者接在了一级永远轮不到的候选上")
    check("11d 摘掉判据前后的答案不一样（同一形态只有一个变量的话，上面两条就是在比运气）",
          not _same(_off11, _got11), f"{_off11} vs {_got11}")

    # 11e 多滤这一方向：源码态锚点在桌面上也必须照原样用。第一版 riskbird 的滤网通吃了
    # 这一级，把四个采集器的数据目录一起挪去 ~/.b2bcollect，开发机上攒了几年的 Cookie
    # 留在原地没人读——「企知道/风鸟/爱企查都要重新登录、1688 种子库读不到」。
    _proj_on_desktop = _desk2 / "proj"
    (_proj_on_desktop / "b2bcollect_data").mkdir(parents=True)
    _old_cdp_file = cdp_base.__file__
    cdp_base.__file__ = str(_proj_on_desktop / "cdp_base.py")   # 函数体读模块全局 __file__
    try:
        _dev11 = _cdp_answer(None, frozen=False)
        check("11e1 cdp_base 源码态锚点在桌面上也照用（多滤=悄悄搬家，四年 Cookie 没人读）",
              _same(_dev11, _proj_on_desktop / "b2bcollect_data"), str(_dev11))
        with _switches(frozen=False, platform="darwin"):
            runtime_paths.reset_cache()
            _dev_rp = runtime_paths.candidate_dirs("riskbird_data", dev_dir=str(_proj_on_desktop))
        check("11e2 runtime_paths 同口径：锚点留在链里而且是第一候选",
              _dev_rp and _same(_dev_rp[0], _proj_on_desktop / "riskbird_data"), str(_dev_rp[:2]))
        check("11e3 这条豁免的方向性：上面那两级在判据眼里确实算受保护目录（不然 11e1 白测）",
              all(_real_fn11(d) for d in (_proj_on_desktop, _proj_on_desktop / "riskbird_data")),
              f"夹具前提不成立：{_proj_on_desktop} 没被判为受保护")
    finally:
        cdp_base.__file__ = _old_cdp_file
        runtime_paths.reset_cache()

    # 11f 指定落点不过这道滤：三个消费者同一条说法（10h1 钉的是 cdp_base 与 riskbird 数据
    # 目录，这里补状态目录那一条——审计日志与自检报告住的地方也是排障要能钉的）。
    os.environ[tool_config._STATE_DIR_ENV_VAR] = str(_desk2 / "pinned")
    _st11 = tool_config.state_dir()
    check("11f 指定的状态目录即便是受保护位置也照办（指了不生效＝两句互相矛盾的说法）",
          _same(_st11, _desk2 / "pinned"), str(_st11))
finally:
    for _k, _v in _env11.items():
        if _v is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _v
    runtime_paths.reset_cache()
    shutil.rmtree(_desk2, ignore_errors=True)

# ── 12. 两份镜像实现同口径对拍：改出自己的说法就红在这里 ────────────────────
# riskbird_build 与 b2bcollect_build 是两个独立打包的树，反向 import 就要在「谁先
# import」上赌运气，所以这份重复是明知并接受的形状（理由在 runtime_paths 的模块
# docstring）。既然接受重复，就必须有对拍：只有文字说明的「同口径」会在第一次单边修改
# 之后长期失效，而那正是本轮「择路单边加了数据偏好」的收场。
print("\n═══ 12. 两份实现对拍：择路 / 有数据判据 / 接管 / 常量 / 真实 Chrome ═══")

check("12a 接管相关常量逐字一致（改名与调上限要两边一起动）",
      (set(tool_config.HOUSEKEEPING_FILES) == set(runtime_paths.HOUSEKEEPING_FILES)
       and tool_config.ADOPT_MAX_FILES == runtime_paths.ADOPT_MAX_FILES
       and tool_config.ADOPT_MAX_BYTES == runtime_paths.ADOPT_MAX_BYTES
       and tuple(tool_config.PROFILE_LOGIN_MARKER) == tuple(runtime_paths.PROFILE_LOGIN_MARKER)
       and tuple(tool_config.PROFILE_SKIP_PATTERNS) == tuple(runtime_paths.PROFILE_SKIP_PATTERNS)
       and tool_config.PROFILE_ENV_VAR == runtime_paths.PROFILE_ENV_VAR),
      f"tool_config={sorted(tool_config.HOUSEKEEPING_FILES)} "
      f"runtime_paths={sorted(runtime_paths.HOUSEKEEPING_FILES)} / 上限 "
      f"{tool_config.ADOPT_MAX_FILES},{tool_config.ADOPT_MAX_BYTES} vs "
      f"{runtime_paths.ADOPT_MAX_FILES},{runtime_paths.ADOPT_MAX_BYTES}")
check("12b 用户目录容器名同一个（多一个家就等于远程排障时多问一句「你那个是哪年的」）",
      tool_config.USER_CONFIG_DIR_NAME == runtime_paths.USER_DIR_NAME,
      f"{tool_config.USER_CONFIG_DIR_NAME} vs {runtime_paths.USER_DIR_NAME}")


def _holds_samples():
    """一批目录形状，两份实现必须给出一样的答案。"""
    root = TMP / "c12holds"
    shapes = {}
    for tag, files, marker in (("空目录", [], False),
                               ("只有写探针", [".write_test"], False),
                               ("只有 .DS_Store", [".DS_Store"], False),
                               ("有一份种子库", ["seen_store_1688.json"], False),
                               ("登录过的配置目录", [], True),
                               ("半截配置目录（有文件没 Cookies）", ["Preferences"], False)):
        d = root / tag
        d.mkdir(parents=True, exist_ok=True)
        for f in files:
            (d / f).write_text("x", encoding="utf-8")
        if marker:
            (d / "Default").mkdir(exist_ok=True)
            (d / "Default" / "Cookies").write_text("登录态", encoding="utf-8")
        shapes[tag] = d
    return shapes


_c12shapes = _holds_samples()
_c12bad = []
for _tag, _d in _c12shapes.items():
    for _mk in ((), tuple(tool_config.PROFILE_LOGIN_MARKER)):
        _x = tool_config.holds_tool_data(_d, _mk)
        _y = runtime_paths._holds_tool_data(_d, _mk)
        if _x != _y:
            _c12bad.append(f"{_tag}{_mk}: tool_config={_x} runtime_paths={_y}")
check("12c 「这个目录里已经有本工具的数据」两份实现逐例同答", not _c12bad, str(_c12bad))
check("12d 夹具前提：半截 profile 有文件但没有 Default/Cookies（只看「有没有文件」会误判登录过）",
      tool_config.holds_tool_data(_c12shapes["半截配置目录（有文件没 Cookies）"])
      and not tool_config.holds_tool_data(_c12shapes["半截配置目录（有文件没 Cookies）"],
                                          tuple(tool_config.PROFILE_LOGIN_MARKER)))


def _choose_parity():
    """三种候选形状喂给两份择路实现，返回「答案不一样」的条目。

    夹具一律 exist_ok：这个函数会被调用三次（正例、变异前后各一次），每次重造一遍
    「File exists」就当场炸——判据跑不成不等于判据抓到红，那种收场在本仓库已经点过名。
    """
    bad = []
    _c12 = TMP / "c12choose"
    w_empty, w_data = _c12 / "e", _c12 / "d"
    w_empty.mkdir(parents=True, exist_ok=True)
    w_data.mkdir(parents=True, exist_ok=True)
    (w_data / "seed.json").write_text("x", encoding="utf-8")
    ro_root = _c12 / "ro"
    ro_root.mkdir(parents=True, exist_ok=True)
    ro_a = ro_root / "a"
    ro_a.mkdir(exist_ok=True)
    _ro(ro_root)
    try:
        for tag, cands in (("第一个可写但空/第二个存着数据", [w_empty, w_data]),
                           ("顺序颠倒", [w_data, w_empty]),
                           ("全都写不进去", [ro_a, ro_root / "b"])):
            x = tool_config.choose_writable(list(cands))
            y = runtime_paths._choose(list(cands))
            if not _same(x, y):
                bad.append(f"{tag}: tool_config={x} runtime_paths={y}")
    finally:
        _rw(ro_root)
    return bad


check("12e 两份择路实现三种候选形状同答", not _choose_parity(), str(_choose_parity()))
# 反向对照：对拍工具自己得能抓红。把 runtime_paths 那份换成旧口径（数据优先），12e 必须
# 立刻报出差异。「回想出来的结论没有一条能复跑」——这句对拍夹具同样适用。
_rp_choose_real = runtime_paths._choose
runtime_paths._choose = lambda cands, markers=(): _choose_old_rule(cands, markers)
try:
    _neg12 = _choose_parity()
finally:
    runtime_paths._choose = _rp_choose_real
check("12f 反向对照：把数据偏好注回来，对拍真的报出差异（不然 12e 是装饰）", _neg12,
      "换成旧口径之后对拍仍然为空——这份对拍抓不到单边修改")


def _adopt_pair(tag):
    """造一对「老位置有三份文件（含一个子目录与一个家务文件）、新位置是空的」的夹具。"""
    root = TMP / f"c12adopt_{tag}"
    src, dst = root / "old", root / "new"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "nested.json").write_text("子目录里的一份", encoding="utf-8")
    (src / ".DS_Store").write_text("家务", encoding="utf-8")
    for n in ("seen_store_1688.json", "cookies.json", "state.json"):
        (src / n).write_text(f"{n} 的内容", encoding="utf-8")
    dst.mkdir(parents=True)
    return src, dst


src_tc, dst_tc = _adopt_pair("tc")
src_rp, dst_rp = _adopt_pair("rp")
_out_tc = tool_config.adopt_missing_files([src_tc], dst_tc)
_out_rp = runtime_paths._adopt_missing_files([src_rp], dst_rp)
_norm = lambda items, s: sorted(x.replace(str(s), "<老位置>") for x in items)
check("12g 两份接管实现返回的账目一致（按 <老位置> 归一后逐字比）",
      _norm(_out_tc, src_tc) == _norm(_out_rp, src_rp),
      f"tool_config={_norm(_out_tc, src_tc)} runtime_paths={_norm(_out_rp, src_rp)}")
check("12h 两份接管实现落到新位置的文件集合一致",
      sorted(p.name for p in dst_tc.iterdir()) == sorted(p.name for p in dst_rp.iterdir()),
      f"{sorted(p.name for p in dst_tc.iterdir())} vs {sorted(p.name for p in dst_rp.iterdir())}")
check("12i 两份实现都只接顶层三份数据文件（子目录与家务文件不算）",
      sorted(p.name for p in dst_tc.iterdir())
      == ["cookies.json", "seen_store_1688.json", "state.json"],
      str(sorted(p.name for p in dst_tc.iterdir())))

# 12j real_chrome_profile：两份都用 Default/Cookies 当判据，装了 Chrome 从没开过的机器
# 那个目录也存在，拿目录在不在当「有真实配置」会让刷新登录态每次启动白跑一遍。
_chrome_dir = FAKE_HOME / "Library" / "Application Support" / "Google" / "Chrome"
(_chrome_dir / "Default").mkdir(parents=True, exist_ok=True)
_home_saved = os.environ.get("HOME")
os.environ["HOME"] = str(FAKE_HOME)          # expanduser 读的是 HOME，不是 Path.home()
try:
    with _switches(platform="darwin"):
        _ck = _chrome_dir / "Default" / "Cookies"
        _ck.write_text("登录态", encoding="utf-8")
        _a12, _b12 = tool_config.real_chrome_profile(), runtime_paths.real_chrome_profile()
        check("12j1 有 Default/Cookies 时两份都认出这个目录",
              _a12 is not None and _same(_a12, _b12), f"{_a12} / {_b12}")
        _ck.unlink()
        _a12b, _b12b = tool_config.real_chrome_profile(), runtime_paths.real_chrome_profile()
        check("12j2 目录在但没有 Cookies 时两份都说不算（白跑一轮刷新比不刷新更糟）",
              _a12b is None and _b12b is None, f"{_a12b} / {_b12b}")
finally:
    if _home_saved is None:
        os.environ.pop("HOME", None)
    else:
        os.environ["HOME"] = _home_saved

# ── 13. 显式接管：三条硬规矩、上限、台账，以及「接不过来就用老位置」 ────────
# 第 10 节把「本机历史」从择路里赶出去之后，用户攒下的登录态/Cookie/种子库靠什么活下来？
# 靠这一步。它必须同时守住两头：接了不许盖掉现役数据（新落点已经在用就说明现役在那儿），
# 也不许顺手变成搬家（老位置很可能还躺着另一个版本的工具正在用）。第三头是台账：
# 「悄悄接完不吭声」和「接没接上」是同一类故障的两种收场——远程排障时两句都问不出来。
print("\n═══ 13. 接管三条硬规矩 + 上限 + 台账 + 接不过来退老位置 ═══")


def _pair13(tag):
    """老位置有数据（含一个子目录与一份家务文件）、新位置是空的。"""
    root = TMP / f"c13_{tag}"
    src, dst = root / "old", root / "new"
    src.mkdir(parents=True)
    for n in ("seen_store_1688.json", "cookies.json"):
        (src / n).write_text(f"{n} 的内容", encoding="utf-8")
    (src / "sub").mkdir()
    (src / "sub" / "deep.json").write_text("子目录里的一份", encoding="utf-8")
    (src / ".DS_Store").write_text("家务", encoding="utf-8")
    dst.mkdir(parents=True)
    return src, dst


_s13, _d13 = _pair13("busy")
(_d13 / "seen_store_1688.json").write_text("现役的最新数据", encoding="utf-8")
check("13a 新落点已经在用：一份都不接、一个字都不改（往现役里倒旧数据=把最新的盖掉）",
      tool_config.adopt_missing_files([_s13], _d13) == []
      and (_d13 / "seen_store_1688.json").read_text(encoding="utf-8") == "现役的最新数据"
      and not (_d13 / "cookies.json").exists())

# 13b 「绝不覆盖同名」这一条走的是公开函数能到的那条路：新落点里有一个**目录**叫
# cookies.json（一次中断留下的），holds_tool_data 只看普通文件所以照样动手，而守卫必须
# 拦住——用 shutil.rmtree + copy 的写法在这里会红，那是把用户目录当自己的临时目录。
_s13b, _d13b = _pair13("same")
(_d13b / "cookies.json").mkdir()
(_d13b / "cookies.json" / "留着.json").write_text("原来这里面有东西", encoding="utf-8")
_out13b = tool_config.adopt_missing_files([_s13b], _d13b)
check("13b 同名条目原样保留（接管的另一头是不许顺手「清理」新落点里的东西）",
      (_d13b / "cookies.json").is_dir()
      and (_d13b / "cookies.json" / "留着.json").is_file()
      and "cookies.json" not in "".join(_out13b), str(_out13b))
check("13c 该接的照样接进来了（上面那条守卫不许把整次接管一起拦死）",
      (_d13b / "seen_store_1688.json").is_file(), str(sorted(p.name for p in _d13b.iterdir())))

_s13d, _d13d = _pair13("keep")
_out13d = runtime_paths._adopt_missing_files([_s13d], _d13d)
check("13d 只复制、绝不删源（老位置很可能正被另一个版本用着，删源=替用户做决定）",
      (_s13d / "seen_store_1688.json").is_file() and (_s13d / "cookies.json").is_file()
      and (_s13d / "sub" / "deep.json").is_file(), str(_s13d))
check("13e 只搬顶层：子目录不递归合并（「接了三份 Cookie」与「搬了一次家」必须讲得清）",
      sorted(p.name for p in _d13d.iterdir()) == ["cookies.json", "seen_store_1688.json"],
      str(sorted(p.name for p in _d13d.iterdir())))
check("13f 台账记了账且格式可原样报给用户（<老目录> -> <文件名>）",
      len(_out13d) == 2 and all("-> " in x for x in _out13d)
      and all(str(_s13d) in x for x in _out13d)
      and runtime_paths.ADOPTED[-2:] == _out13d,
      f"返回值={_out13d} 台账尾部={runtime_paths.ADOPTED[-2:]}")
check("13g tool_config 那一侧同样进台账（两份镜像实现都要留得下账）",
      any(str(_s13b) in x for x in tool_config.ADOPTED), str(tool_config.ADOPTED[-3:]))

# 13h 上限：撞到 ADOPT_MAX_FILES 说明这个「老落点」根本不是本工具的目录（用户把环境变量
# 指到了一堆无关文件上），那种时候宁可少接也别把别人的文件夹复制进落点。
_s13i, _d13i = _pair13("cap")
for _i in range(6):
    (_s13i / f"extra{_i}.tmp.json").write_text("x", encoding="utf-8")
_tc_files, _rp_bytes = tool_config.ADOPT_MAX_FILES, tool_config.ADOPT_MAX_BYTES
try:
    tool_config.ADOPT_MAX_FILES = 2
    _cap13 = tool_config.adopt_missing_files([_s13i], _d13i)
    check("13h1 文件数上限真的生效（撞上限=这个目录不是本工具的）", len(_cap13) == 2,
          f"接了 {len(_cap13)} 份：{_cap13}")
    tool_config.ADOPT_MAX_FILES = 99
    tool_config.ADOPT_MAX_BYTES = 4                       # 单文件超过 4 字节就不接
    _s13j, _d13j = _pair13("bytes")
    _cap13j = tool_config.adopt_missing_files([_s13j], _d13j)
    check("13h2 单文件体积上限真的生效（一个 500MB 的「种子库」不是本工具的数据）",
          _cap13j == [], f"接了：{_cap13j}")
finally:
    tool_config.ADOPT_MAX_FILES, tool_config.ADOPT_MAX_BYTES = _tc_files, _rp_bytes

# 13i/13j 浏览器配置目录这一路：接管不改变落点，但**接不过来就继续用老位置**。
# 后半句比前半句重要：静默换成一个空 profile 的表现就是「今天怎么又要扫码登录」——
# 与用户报的那条一模一样，而日志里一个字都不会提落点。
_env13 = {k: os.environ.get(k) for k in (tool_config.PROFILE_ENV_VAR,
                                         runtime_paths.PROFILE_ENV_VAR)}
for _k in _env13:
    os.environ.pop(_k, None)
try:
    _legacy = TMP / "c13_legacy_profile"
    (_legacy / "Default").mkdir(parents=True)
    (_legacy / "Default" / "Cookies").write_text("登录态", encoding="utf-8")
    (_legacy / "Cache").mkdir()
    (_legacy / "Cache" / "big.bin").write_text("不该搬的东西", encoding="utf-8")
    _pname = "prof_c13"
    _pnew = tool_config.durable_profile_root() / _pname
    shutil.rmtree(_pnew, ignore_errors=True)
    _before13 = len(tool_config.ADOPTED)
    _gp13 = tool_config.browser_profile_dir(_pname, legacy_dirs=[_legacy])
    check("13i1 老位置登录过 → 接过来之后落点仍然是持久位置那一个（接管不改变落点）",
          _same(_gp13, _pnew), f"期望 {_pnew}，实际 {_gp13}")
    check("13i2 登录态真的到了新位置", (_pnew / "Default" / "Cookies").is_file(), str(_pnew))
    check("13i3 老位置原样还在（只复制、绝不删源）",
          (_legacy / "Default" / "Cookies").is_file(), str(_legacy))
    check("13i4 该跳过的重目录没被搬过来（Cache 那一支几百 MB，搬它=每次启动都搬家）",
          not (_pnew / "Cache").exists(), str(sorted(p.name for p in _pnew.iterdir())))
    check("13i5 配置目录这一路也进了台账", len(tool_config.ADOPTED) > _before13
          and any(str(_legacy) in x for x in tool_config.ADOPTED[_before13:]),
          str(tool_config.ADOPTED[_before13:]))
    (_pnew / "Default" / "Cookies").write_text("新位置自己攒的登录态", encoding="utf-8")
    tool_config.browser_profile_dir(_pname, legacy_dirs=[_legacy])
    check("13i6 新位置已经有登录态时不动老位置、也不被老副本覆盖（老副本永不刷新）",
          (_pnew / "Default" / "Cookies").read_text(encoding="utf-8") == "新位置自己攒的登录态"
          and (_legacy / "Default" / "Cookies").read_text(encoding="utf-8") == "登录态")

    # 13j 反向对照：新位置接不进去（持久位置那一级的父目录只读）时必须退回老位置。
    shutil.rmtree(_pnew, ignore_errors=True)
    _proot = tool_config.durable_profile_root()
    _ro(_proot)
    try:
        _gj13 = tool_config.browser_profile_dir(_pname, legacy_dirs=[_legacy])
        _gr13 = runtime_paths.browser_profile_dir(_pname, legacy_dirs=[_legacy])
        check("13j1 接不过来时继续用老位置（tool_config）：登录态比落点统一重要",
              _same(_gj13, _legacy), str(_gj13))
        check("13j2 接不过来时继续用老位置（runtime_paths 同口径）",
              _same(_gr13, _legacy), str(_gr13))
    finally:
        _rw(_proot)
finally:
    for _k, _v in _env13.items():
        if _v is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _v

# ── 14. 「59 条 offer，新增 0 家」那句话：两个口径必须逐字同答 ──────────────
# 由来（2026-11-13 用户报「以前 1688 还能采到，现在一直是 0」）：1688 这一路把**所有**
# 种子库键都当去重依据，等于「进过种子库就永不重采」，早期被反爬空页判成「无联系方式」的
# 公司被永久盖章；而 cdp_base 那 12 路有 ct/rt 解锁与「跳过多少家、还欠多少家」的账目。
# 同一个问题在两套采集器里各写一遍，只剩一份是对的——所以这里既比代码指纹，也比答案表，
# 还把两边的旧口径请回来做差分：表要是分辨不了新旧口径，下面几条就是装饰。
print("\n═══ 14. 页尾与账目口径：cdp_base 与 ali1688 同答（含旧口径差分）═══")


def _body_dump(source, func_name):
    """函数体去掉 docstring 的 AST 指纹：比的是代码，不是 prose。

    两处 docstring 各自解释自己那一路的字段名（1688 说「网站」，基类说「公司网址」），
    拿文字作对照只会红在无关的地方；而代码结构一比就露出「少判一个字段」「尾巴少一段」。
    """
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(getattr(body[0], "value", None), ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body = body[1:]
            return ast.dump(ast.Module(body=body, type_ignores=[]))
    return None


def _fn_body_dump(fn):
    import textwrap
    return _body_dump(textwrap.dedent(inspect.getsource(fn)), fn.__name__)


_cdp_cls = cdp_base.CdpSiteCrawler
_a16_cls = ali1688_crawler.Ali1688Crawler
for _fname in ("_page_tail", "_has_any_contact", "_entry_locked", "_seen_store_pending"):
    check(f"14a {_fname} 两边的函数体逐字同构（改一处的说法必须两边一起改）",
          _fn_body_dump(getattr(_cdp_cls, _fname)) is not None
          and _fn_body_dump(getattr(_cdp_cls, _fname))
          == _fn_body_dump(getattr(_a16_cls, _fname)),
          f"{_fname}: 基类={_fn_body_dump(getattr(_cdp_cls, _fname))} "
          f"1688={_fn_body_dump(getattr(_a16_cls, _fname))}")
check("14b 重试上限同一个数（同一个「补几次算完」的判断不许有两个答案）",
      _cdp_cls.CONTACT_RETRY_MAX == _a16_cls.CONTACT_RETRY_MAX == 2,
      f"{_cdp_cls.CONTACT_RETRY_MAX} vs {_a16_cls.CONTACT_RETRY_MAX}")

_tail14 = _cdp_cls._page_tail(3, 5)
check("14c 页尾拼法同答（早过滤与增量库跳过两种原因都要说出来，只报一种就是「新增 0 家」那句）",
      _tail14 == _a16_cls._page_tail(3, 5) == "，早过滤 3 家(未进详情)，增量库已含 5 家(跳过)",
      repr(_tail14))
check("14d 两个原因都没有时页尾是空的（不许把「0 家」硬凑成一句看起来有原因的话）",
      _cdp_cls._page_tail(0, 0) == _a16_cls._page_tail(0, 0) == "", repr(_cdp_cls._page_tail(0, 0)))

_CONTACT_CASES = [
    # (记录, 期望：有没有联系方式)
    ({"联系人": "张三", "电话": "", "手机": "", "邮箱": ""}, True),
    ({"联系人": "", "电话": "020-12345678", "手机": "", "邮箱": ""}, True),
    ({"联系人": "", "电话": "", "手机": "13800000000", "邮箱": ""}, True),
    ({"联系人": "", "电话": "", "手机": "", "邮箱": "a@b.com"}, True),
    ({"联系人": "  ", "电话": "", "手机": "", "邮箱": ""}, False),
    ({"联系人": "", "电话": "", "手机": "", "邮箱": "", "网站": "x.com"}, False),
    ({"联系人": "", "电话": "", "手机": "", "邮箱": "", "公司网址": "x.com"}, False),
    ({}, False),
]
_bad14 = []
for _rec, _want in _CONTACT_CASES:
    _x, _y = _cdp_cls._has_any_contact(dict(_rec)), _a16_cls._has_any_contact(dict(_rec))
    if not (_x == _y == _want):
        _bad14.append(f"{_rec}: 基类={_x} 1688={_y} 期望={_want}")
check("14e 「有没有联系方式」判据两边同答，且刻意不看网址", not _bad14, str(_bad14))


def _has_contact_old_rule(rec):
    """旧口径原样请回来：只看「联系人」那一列（人名）。"""
    return bool(str(rec.get("联系人") or "").strip())


_old_hits = [f"{_rec}" for _rec, _want in _CONTACT_CASES
             if _has_contact_old_rule(dict(_rec)) != _want]
check("14f 反向对照：旧口径（只看人名列）在这张表上确实判错（不然 14e 是装饰）",
      _old_hits, "旧口径也全对——这张表分辨不了两种说法")

_ENTRY_CASES = [
    ({"ct": 1}, True),                                   # 活干完了：永久跳过
    ({"ct": 0, "rt": 0}, False),                         # 空联系人、还没补过：下轮重采
    ({"ct": 0, "rt": _cdp_cls.CONTACT_RETRY_MAX - 1}, False),
    ({"ct": 0, "rt": _cdp_cls.CONTACT_RETRY_MAX}, True),  # 补够了才盖章
    ({}, True),                                          # 老版本没写 ct：按已处理，别把上千家重访
    ("不是字典", True),
]
_bad14e = []
for _e, _want in _ENTRY_CASES:
    _x, _y = _cdp_cls._entry_locked(_e), _a16_cls._entry_locked(_e)
    if not (_x == _y == _want):
        _bad14e.append(f"{_e}: 基类={_x} 1688={_y} 期望={_want}")
check("14g 种子库「命中即跳过」的解锁口径两边同答（1688 恒 0 家就是这一条没接上）",
      not _bad14e, str(_bad14e))
check("14h 反向对照：旧口径（进过种子库就永不重采）在这张表上确实锁死待补采的条目",
      _cdp_cls._entry_locked({"ct": 0, "rt": 0}) is False,
      "ct=0/rt=0 也被锁住——那正是「以前能采、现在一直是 0」的那一步")

# 14i 收尾那两句账目：两边都必须说「跳过多少家」和「还欠多少家补联系人」，逐字同一段话。
# 只比函数指纹比不到这里：这两句分散在各自的收尾方法里，长在不同上下文中。
_FRAG14 = ("增量库跳过 {self._incremental_skipped} 家(已采过或重试已用尽，未重复访问)",
           "另有 {pend} 家欠补联系人，下轮自动重采")
_src_cdp14 = (HERE / "cdp_base.py").read_text(encoding="utf-8")
_src_a1614 = (HERE.parent / "ali1688_build" / "ali1688_crawler.py").read_text(encoding="utf-8")
for _frag in _FRAG14:
    check(f"14i 收尾账目片段两边逐字同在：「{_frag[:16]}…」",
          _frag in _src_cdp14 and _frag in _src_a1614,
          f"基类有={_frag in _src_cdp14} 1688 有={_frag in _src_a1614}")
check("14j 欠账计数真的能数出来（台账里一半待补采就不能报「已经采全」）",
      _cdp_cls._seen_store_pending.__name__ == _a16_cls._seen_store_pending.__name__
      and _a16_cls._seen_store_pending(
          type("_S", (), {"_seen_store": {"k1": {"ct": 1}, "k2": {"ct": 0, "rt": 0}},
                          "_entry_locked": staticmethod(_a16_cls._entry_locked)})()) == 1,
      "数不出「还欠几家」的账目只是把 0 家说成两句好听的话")

# ── 15. results_live：五路采集器同一个「就地补字段的实时视图」 ──────────────
# 官网回采联系方式那一轮（功能④）必须写回真身：整体替换 results 会把并发的 append 盖掉，
# 症状是「采到了一半又不见了」。这个视图在五路采集器里各有一份（两个独立打包的树），
# 谁写成 snapshot_results 那种副本，回采就永远落不了地——所以既比代码指纹，也真跑一次。
print("\n═══ 15. results_live 五路一致：同一个锁、同一份真身 ═══")

_LIVE_FILES = [HERE / "cdp_base.py",
               HERE.parent / "riskbird_build" / "aiqicha_crawler_v2.py",
               HERE.parent / "riskbird_build" / "qizhidao_crawler.py",
               HERE.parent / "riskbird_build" / "riskbird_crawler_v2.py",
               HERE.parent / "ali1688_build" / "ali1688_crawler.py"]
_dumps15 = {}
for _f in _LIVE_FILES:
    _dumps15[_f.name] = _body_dump(_f.read_text(encoding="utf-8"), "results_live")
check("15a 五路都定义了 results_live（少一路=那一路的回采写了个没人读的副本）",
      all(v for v in _dumps15.values()),
      f"没有该方法的路={sorted(k for k, v in _dumps15.items() if not v)}")
check("15b 五路函数体逐字同构（都在锁内返回 list(self.results)）",
      len(set(_dumps15.values())) == 1, str({k: v[:40] for k, v in _dumps15.items()}))


class _Stub15:
    def __init__(self):
        self.results_lock = threading.Lock()
        self.results = [{"公司名称": "甲"}, {"公司名称": "乙"}]


for _lbl, _cls in (("cdp_base", _cdp_cls), ("ali1688", _a16_cls)):
    _st = _Stub15()
    _view = _cls.results_live(_st)
    check(f"15c {_lbl}：返回的是原字典（就地补字段才落得下去）",
          all(any(r is _st.results[i] for r in _view) for i in range(len(_st.results))),
          str(_view))
    check(f"15d {_lbl}：列表本身是新的一份（收尾时并发 append 不许让遍历炸掉）",
          _view is not _st.results)
    _st.results.append({"公司名称": "丙"})
    check(f"15e {_lbl}：取完视图之后再 append，视图不会跟着变（否则「写回真身」是句空话）",
          len(_view) == 2, str(_view))
    _snap = _cls.snapshot_results(_st)
    check(f"15f {_lbl}：snapshot 与 live 是两个口径（副本给界面、真身给收尾）",
          all(r is not _st.results[i] for i, r in enumerate(_snap)), str(_snap))

check("15g 反向对照：把 live 写成副本的那种做法在这张夹具上确实落不回真身（15c 不是装饰）",
      not any(r is _st.results[0] for r in [dict(x) for x in _st.results]))

Path.home = _real_home
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n全部通过：{len(PASS)} 组断言")