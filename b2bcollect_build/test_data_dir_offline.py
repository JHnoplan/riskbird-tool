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
每一路「有没有去问它」——行为比对抓得住写错的实现，抓不住同一形状各写四遍的实现。
运行：python test_data_dir_offline.py
"""
import os
import importlib
import shutil
import stat
import sys
import tempfile
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


def run_resolver(fn, frozen, platform, exe=None, here=None):
    """在指定假环境下调用被测解析函数，返回 (结果路径, 异常)"""
    old = (getattr(sys, "frozen", None), sys.platform, getattr(sys, "executable", None))
    old_here = site_adapters._HERE
    try:
        # 换环境之前必须清掉进程内择路缓存：四节用例问的是同一个目录名，缓存不清的话
        # 第 2 节测的其实是第 1 节的环境（结果一路都是 FAKE_HOME，「包旁优先」就假绿了）。
        # 这也是 runtime_paths.reset_cache 存在的唯一理由，测试必须用它，不许另造。
        runtime_paths.reset_cache()
        if frozen:
            sys.frozen = True
        else:
            sys.frozen = False
            sys.__dict__.pop("frozen", None)
            if hasattr(sys, "frozen"):
                sys.frozen = False
        sys.platform = platform
        if exe is not None:
            sys.executable = str(exe)
        if here is not None:
            site_adapters._HERE = here
        return fn(), None
    except Exception as exc:                      # noqa: BLE001
        return None, exc
    finally:
        sys.frozen, sys.platform, sys.executable = old[0], old[1], old[2]
        site_adapters._HERE = old_here
        if old[0] is None:
            sys.__dict__.pop("frozen", None)


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
    sys.frozen, sys.platform, sys.executable = True, "darwin", str(exe7)
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
        sys.frozen, sys.platform, sys.executable = True, "darwin", str(exe7d)
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
        sys.frozen, sys.platform, sys.executable = True, "darwin", str(exe7d)
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

Path.home = _real_home
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n全部通过：{len(PASS)} 组断言")