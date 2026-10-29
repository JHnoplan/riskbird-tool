# -*- coding: utf-8 -*-
"""持久数据目录离线回归：只读应用包（App Translocation）下绝不崩、绝不写进包内。

复现的实机故障：Mac 上 .app 被 Gatekeeper 从只读随机路径启动，企知道 Cookie
目录用包内裸 mkdir → OSError [Errno 30] Read-only file system，启动即弹错误框。
运行：python test_data_dir_offline.py
"""
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


def _under(child, parent):
    """macOS 下 /var 是 /private/var 的软链，路径比较统一 resolve。"""
    return Path(parent).resolve() in Path(child).resolve().parents


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
        _ro(outer)
    return macos / "B2bCollectTool", holder


make_app.n = 0


def run_resolver(fn, frozen, platform, exe=None, here=None):
    """在指定假环境下调用被测解析函数，返回 (结果路径, 异常)"""
    old = (getattr(sys, "frozen", None), sys.platform, getattr(sys, "executable", None))
    old_here = site_adapters._HERE
    try:
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
CASES = [
    ("cdp_base._stable_data_dir", cdp_base.CdpSiteCrawler._stable_data_dir),
    ("site_adapters._writable_data_dir", site_adapters._writable_data_dir),
    ("qizhidao_crawler._stable_data_dir", qizhidao_crawler._stable_data_dir),
    ("ali1688_crawler._stable_data_dir", ali1688_crawler.Ali1688Crawler._stable_data_dir),
]

exe, holder = make_app(read_only=True)
app_dir = Path(exe).parent.parent.parent          # Fake.app
for name, fn in CASES:
    _ro(app_dir)                                  # 包内也设为只读，模拟转置卷
    here = exe.parent if "site_adapters" in name else None
    result, exc = run_resolver(fn, True, "darwin", exe=exe, here=here)
    check(f"{name}：只读 .app 不抛异常", exc is None, repr(exc))
    check(f"{name}：结果不在 .app 包内", result is not None and not _under(result, app_dir),
          f"{result} 仍在 {app_dir} 内")
    probe = result / ".probe"
    probe.write_text("1", encoding="utf-8")       # 真写一次，验证返回的就是可用目录
    probe.unlink()
    check(f"{name}：结果目录确实可写", str(result).startswith(str(FAKE_HOME)),
          f"期望退到 {FAKE_HOME}/.b2bcollect，实际 {result}")
    _rw(app_dir)

# ── 2. 可写的 .app 父目录 → 数据放包旁可见目录（不塞进 .app 里） ──
exe2, holder2 = make_app()
for name, fn in CASES:
    here = exe2.parent if "site_adapters" in name else None
    result, exc = run_resolver(fn, True, "darwin", exe=exe2, here=here)
    check(f"{name}：包旁目录可写时优先用它", exc is None and _under(result, holder2),
          f"exc={exc!r} result={result} 期望在 {holder2} 下")
    check(f"{name}：包旁目录仍不写进 .app 内部", ".app" not in str(result), str(result))

# ── 3. Windows 打包（onedir/onefile 同级目录）→ exe 旁，不落 _MEIPASS ──
win_dir = TMP / "win" / "dist"
win_dir.mkdir(parents=True, exist_ok=True)
for name, fn in CASES:
    here = win_dir if "site_adapters" in name else None
    result, exc = run_resolver(fn, True, "win32", exe=win_dir / "Tool.exe", here=here)
    check(f"{name}：Windows 打包放 exe 同级", exc is None and _under(result, win_dir),
          f"exc={exc!r} result={result}")

# ── 4. 开发环境（未打包）→ 仍用各自模块目录，老数据/老 Cookie 不搬家 ──
dev_targets = {
    "cdp_base": (cdp_base.CdpSiteCrawler._stable_data_dir, HERE / "b2bcollect_data"),
    "site_adapters": (site_adapters._writable_data_dir, HERE / "b2bcollect_data"),
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
_ro(app3)
result3, exc = run_resolver(qizhidao_crawler._stable_data_dir, True, "darwin", exe=exe3)
check("只读包内实例化 Cookie 路径不再 Errno 30", exc is None, repr(exc))
check("Cookie 目录退到包外兜底位置", result3 == FAKE_HOME / ".b2bcollect" / "riskbird_data",
      str(result3))
_rw(app3)

Path.home = _real_home
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n全部通过：{len(PASS)} 组断言")
