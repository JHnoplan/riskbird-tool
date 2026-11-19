#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_tool_update_offline.py —— 新版本提示与自动更新的**引擎层**离线判据（需求 2 第二块）。

界面层那份判据（`debug/update_offline_test.py`）管的是「这句话有没有说、按钮点下去走哪条路」；
这一份管的是那句话所依据的**结论**是怎么来的。两层分开写的理由与 Chrome 那块一样：界面可以
把一个错结论渲染得很得体，引擎也可以把一个对结论算错。

钉住的十六组性质，每条都对应一个已经付过学费或想过最坏形状的事故：

① 平台分派：Rosetta 下的 Python 报 x86_64，直接归到 `other` 就是「一台能自动升级的机器被当成
   不能」；`other` 必须拼不出清单地址（空串），否则会把「不适配」问成「对方 404」；
② 清单严校验：下载的是可执行程序，「清单说下哪就下哪」必须是硬约束——档名、直链、指纹、
   字节数四项任一不像话就整体拒收，而不是「少几个字段就当没有更新」；
③ 五种 state 互斥且形状一致：`unknown`（可重试）与 `bad-manifest`（CI 要修）混成一个，远程支持
   就会拿着没网的机器去查发布流程；本模块**不返回** ok/has_update 那种布尔位；
④ `check` 的分派：本机不适配时根本不发请求；清单拿不回来是 unknown 不是 bad-manifest；
⑤ 「怎么装」与「装之前说的那几步」必须问**制品自己**，不拿运行这台机器的解释器反推；
⑥ 下载：缺地址或指纹不开始；声明字节数与实到不符就删件并停下；上限按 size 的两倍与 1GB 取小；
⑦ 安装包必须**等它跑完**并取退出码，超时说「这边不等了」而不是「装坏了」；
⑧ 装完再对一次新包里的构建戳：找不到戳是「没能确认」不是「没装上」，两者给的话完全不同；
⑨ 交接的分岔看**包自己的形状**；认不出的形状不动任何东西；旧实例没真退出就不去替换正在跑的东西；
⑩ macOS 自替换的每个失败分支都必须「没有替换任何东西」，装不回去就当场把旧的挪回来，
   回滚也失败时留下那句最难听但最必要的话；
⑪ 旁边那份旧副本只在「版本也对上了」之后才清；拉不起来时不许清；清不掉必须说话；
⑫ 报告：交接进程 stdio 进黑洞，成败都要落一份包外文件；写不下去只说一句，不改结论；
⑬ 读报告：没有/读坏/太老/时间戳在未来都返回空字典——界面因此闭嘴，而不是播一句上周的旧话；
⑭ 免打扰按**版本号**记，不按时间记；只有 update-available 才可能弹；
⑮ 命令行与界面共用同一份解析与同一个 `apply_update`，退出码三档；两处各实现一份交接的话，
   线上永远只会跑到其中一份；
⑯ 与发布那头（上传脚本的制品白名单、桶与前缀）和界面那头（四个开关、免打扰键、state 口径）
   逐字对拍：版本号算法只允许存在于 tool_version，本模块不自己 parse/compare。

不联网、不碰真实 HOME（`staging_dir` 一律指到 tempdir）、不真跑 ditto/rm（换成如实复制/删除的
替身，语义与真命令一致，否则「成功才清副本」这一条只能靠嘴上说）。

运行：.venv/bin/python b2bcollect_build/test_tool_update_offline.py
"""
import io
import json
import os
import re
import sys
import time
import shutil
import zipfile
import subprocess
import contextlib
import tempfile
import importlib.util
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tool_update as tu                                          # noqa: E402
import tool_version as tv                                         # noqa: E402

_PASS = 0
_FAIL = 0


def check(name, cond, detail=None):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ✅ {name}")
    else:
        _FAIL += 1
        print(f"  ❌ {name}" + (f"  -> {detail}" if detail is not None else ""))


def tmp_dir(prefix="upd_"):
    d = Path(tempfile.mkdtemp(prefix=prefix))
    return d


class Recorder:
    """把 log 回调收集起来：本模块每一句「说给人听的话」都是被断言的对象。"""

    def __init__(self):
        self.lines = []

    def __call__(self, msg):
        self.lines.append(str(msg))

    def text(self):
        return "\n".join(self.lines)

    def has(self, *needles):
        return all(n in self.text() for n in needles)


def mk_entry(name, url=None, sha=None, size=118_000_000, file=None):
    """造一份**清单里的一档**：默认全对，缺省项由调用方故意写坏。"""
    real = tu.ARTIFACT_BY_KEY[name]
    return {"file": file if file is not None else real,
            "url": url if url is not None else f"https://{tu.BUCKET_HOST}/{tu.RELEASE_PREFIX}/{real}",
            "sha256": sha if sha is not None else "a" * 64,
            "size": size}


def mk_manifest(version="1.234", schema=None, artifacts=None, notes="修了三个 bug",
                published_at="2026-11-19T09:00:00+08:00"):
    raw = {"schema": schema if schema is not None else tu.SCHEMA,
           "version": version,
           "published_at": published_at,
           "notes": notes,
           "artifacts": ({"win": mk_entry("win"), "mac_arm64": mk_entry("mac_arm64"),
                          "win_portable": mk_entry("win_portable")}
                         if artifacts is None else artifacts)}
    return raw


class use_build:
    """把「本机是哪一版」钉成给定值：写一份 tempdir 里的构建戳。

    走 tool_version 的真算法而不是替 `build_number` 撒谎——本模块与版本模块之间那条
    「比较只用整数 build，不拿字符串比大小」的契约，只有两边都真跑才验得到。
    """

    def __init__(self, build=None):
        self.dir = tmp_dir("upd_stamp_")
        self.saved_bundle = tv.bundle_dir
        self.saved_env = os.environ.get(tv.BUILD_ENV_VAR)
        tv.bundle_dir = lambda: self.dir
        os.environ.pop(tv.BUILD_ENV_VAR, None)
        if build is not None:
            tv.write_stamp(self.dir / tv.BUILD_STAMP_NAME, build)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        tv.bundle_dir = self.saved_bundle
        os.environ.pop(tv.BUILD_ENV_VAR, None)
        if self.saved_env is not None:
            os.environ[tv.BUILD_ENV_VAR] = self.saved_env
        shutil.rmtree(self.dir, ignore_errors=True)
        return False


class use_staging:
    """把下载与报告的落点指到 tempdir：判据绝不允许写进用户真实的 ~/.b2bcollect。"""

    def __init__(self):
        self.dir = tmp_dir("upd_stage_")
        self.saved = tu.staging_dir

    def __enter__(self):
        tu.staging_dir = lambda: self.dir
        return self

    def __exit__(self, *exc):
        tu.staging_dir = self.saved
        shutil.rmtree(self.dir, ignore_errors=True)
        return False


class _Passthrough:
    """包一层真模块，只改我要冒充的那一格，其余原样透出去。

    写这一层是为了「只把 os.name 说成 nt」这种局部伪装：整体换成假 os 会把
    `os.access`、`os.kill` 一起换掉，判据就测不到真行为。
    """

    def __init__(self, real, **overrides):
        object.__setattr__(self, "_real", real)
        for k, v in overrides.items():
            object.__setattr__(self, k, v)

    def __getattr__(self, item):
        return getattr(object.__getattribute__(self, "_real"), item)


class patch_attr:
    """临时替换 `tu` 上的一个名字（模块、函数、常量都用它），退出时原样还回去。"""

    def __init__(self, holder, name, value):
        self.holder = holder
        self.name = name
        self.value = value

    def __enter__(self):
        self.saved = getattr(self.holder, self.name)
        setattr(self.holder, self.name, self.value)
        return self

    def __exit__(self, *exc):
        setattr(self.holder, self.name, self.saved)
        return False


class FakeSubprocess:
    """替掉 `tu.subprocess`：ditto/rm/Popen 都按真语义落地，异常类保持真身。

    这一层不是「让测试好过」，而是让 macOS 自替换那条**真机上只能跑一次**的链路
    （换错了就把用户的工具弄没了）能在离线判据里被逐分支走完。
    """

    def __init__(self, copy_ditto=True, fail_ditto=None, rm_ok=True, rm_raises=False,
                 popen_fail=None):
        self.cmds = []
        self.copy_ditto = copy_ditto
        self.fail_ditto = fail_ditto          # OSError 实例 or "create_then_fail"
        self.rm_ok = rm_ok                    # False = 命令跑了但没删掉（退出码非 0）
        self.rm_raises = rm_raises            # True = rm 自己就抱（权限/被拦）
        self.popen_fail = popen_fail
        self.TimeoutExpired = __import__("subprocess").TimeoutExpired
        self.SubprocessError = __import__("subprocess").SubprocessError
        self.CalledProcessError = __import__("subprocess").CalledProcessError

    def run(self, cmd, *a, **kw):
        argv = [str(x) for x in cmd]
        self.cmds.append(argv)
        if argv[0] == "/usr/bin/ditto" and self.fail_ditto is not None:
            if self.fail_ditto == "create_then_fail":
                # 新版本没放进去、原位却被占住：回滚也挪不回来的最难看的一形
                Path(argv[2]).mkdir(parents=True, exist_ok=True)
                (Path(argv[2]) / "占位.txt").write_text("x", encoding="utf-8")
            raise OSError("ditto 挂了")
        if argv[0] == "/usr/bin/ditto" and self.copy_ditto:
            shutil.copytree(argv[1], argv[2], symlinks=True, dirs_exist_ok=True)
        if argv[0] == "/bin/rm":
            if self.rm_raises:
                raise OSError("rm 没权限")
            if self.rm_ok:
                shutil.rmtree(argv[-1], ignore_errors=True)
            return _NS(returncode=0 if self.rm_ok else 1, stderr=b"", stdout=b"")
        return _NS(returncode=0, stderr=b"", stdout=b"")

    def Popen(self, cmd, *a, **kw):
        argv = [str(x) for x in cmd]
        self.cmds.append(argv)
        if self.popen_fail is not None:
            raise self.popen_fail
        return _NS(pid=4242)


class _NS:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def make_mac_zip(dest: Path, app_name="B2bCollectTool.app", extra_top=None) -> Path:
    """造一个「顶层是一个 .app」的 zip（`zip_app_entries` 只看目录项，不解压）。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(str(dest), "w") as zf:
        zf.writestr(f"{app_name}/Contents/MacOS/{app_name[:-4]}", b"binary")
        zf.writestr(f"{app_name}/Contents/Info.plist", b"<plist/>")
        for extra in (extra_top or []):
            zf.writestr(extra, b"x")
    return dest


def fake_unpack(app_name="B2bCollectTool.app", build=234, with_exec=True, with_macos_dir=True):
    """替掉真 ditto 解包：在 stage 里落一棵**形状与真包一致**的应用树。

    顶层叫什么名、带不带 `Contents/MacOS`、构建戳是哪一版都由调用方指定：「包里的应用
    名不一样」与「解出来的包少了可执行那一层」是两个不同的分支，必须各自拿到真的形状，
    而不是靠话术凑一句。（上一版把这两个分支都只改 unpack_fn，而包属于哪个平台是以
    zip 自己解出来的条目为凭的，于是两个判据一起变成了假绿。）
    """
    def _fn(zip_path, into):
        stem = app_name[:-4]
        app = Path(into) / app_name
        (app / "Contents").mkdir(parents=True, exist_ok=True)
        if with_macos_dir:
            (app / "Contents" / "MacOS").mkdir(parents=True, exist_ok=True)
            if with_exec:
                (app / "Contents" / "MacOS" / stem).write_bytes(b"binary")
        if build is not None:
            res = app / "Contents" / "Resources"
            res.mkdir(parents=True, exist_ok=True)
            (res / tv.BUILD_STAMP_NAME).write_text(
                json.dumps({"build": build}), encoding="utf-8")
        return {"ok": True, "why": ""}
    return _fn


@contextlib.contextmanager
def sr_case(name):
    """每个分支一份全新的「本机应用 + 下载好的包」。

    自替换是真改磁盘的操作：共用一棵树的话，前一个分支留下的状态（旧包已被挑走、
    原位已被新版本盖掉）会直接决定后一个分支的结论——测出来的就不是分支而是顺序。
    """
    d = tmp_dir(f"upd_sr_{name}_")
    pkg = make_mac_zip(d / "B2bCollectTool_Mac_arm64.zip")
    app_root = d / "Applications"
    app_root.mkdir()
    target = app_root / "B2bCollectTool.app"
    (target / "Contents" / "MacOS").mkdir(parents=True)
    (target / "Contents" / "MacOS" / "B2bCollectTool").write_bytes(b"old")
    try:
        yield _NS(d=d, pkg=pkg, app_root=app_root, target=target)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _sr_out():
    return {"ok": False, "kind": "", "why": "", "relaunched": False,
            "verified": {}, "aside": ""}


# ══════════════════════════════════════════════════ ① 平台与通道
def test_platform():
    print("\n═══ ① 平台分派与发布通道 ═══")
    check("Windows 归到 win", tu.platform_key(win=True, darwin=False) == "win")
    check("arm64 Mac 归到 mac_arm64",
          tu.platform_key(win=False, darwin=True, machine="arm64") == "mac_arm64")
    check("Rosetta 下的 x86_64 进程仍归 mac_arm64（不能当成不能自动升级的机器）",
          tu.platform_key(win=False, darwin=True, machine="x86_64", translated=True) == "mac_arm64")
    check("真 Intel Mac 归 other（宁可少给一条自动通路）",
          tu.platform_key(win=False, darwin=True, machine="x86_64", translated=False) == "other")
    check("Linux 源码运行归 other", tu.platform_key(win=False, darwin=False) == "other")
    check("darwin_is_arm64 认 aarch64 写法", tu.darwin_is_arm64("aarch64") is True)
    check("架构字符串拿不到时不硬猜成 arm", tu.darwin_is_arm64("ppc64") is False)
    check("问不出 sysctl 时按 Intel 处理（不是「默认能装」）",
          tu.darwin_is_arm64("x86_64", translated=False) is False)
    check("两个平台各自一份清单，不塞进同一个 version 字段",
          set(tu.MANIFEST_BY_KEY) == {"win", "mac_arm64"})
    check("清单文件名逐字对",
          (tu.MANIFEST_BY_KEY["win"], tu.MANIFEST_BY_KEY["mac_arm64"])
          == ("latest_windows.json", "latest_mac_arm64.json"))
    check("win 的直链锚定发布桶", tu.manifest_url("win") ==
          f"https://{tu.BUCKET_HOST}/{tu.RELEASE_PREFIX}/latest_windows.json")
    check("other 拼不出清单地址（空串，不是猜一个 key 去问 404）",
          tu.manifest_url("other") == "" and tu.manifest_name("other") == "")
    check("Windows 解压版地址固定", tu.portable_url().endswith(tu.ARTIFACT_WINDOWS_PORTABLE))
    check("解压版不参与自动安装（没有自己的发布清单，platform_key 也永不返回它）",
          "win_portable" not in tu.MANIFEST_BY_KEY)
    # 这一条必须在**没被顶掉**的 staging_dir 上量：顶掉之后再问「在不在包外」是假问题
    check("下载落点在包外的用户目录（不在应用包里）",
          str(tu.staging_dir()).startswith(str(Path.home()))
          and str(tu.staging_dir()).endswith("updates")
          and str(tu.staging_dir()) not in str(tu.__file__))
    check("平台键与档名双向钉死（win 不能对到 Mac 的 zip）",
          set(tu.ARTIFACT_BY_KEY) == set(tu.MANIFEST_BY_KEY) | {"win_portable"}
          and tu.ARTIFACT_BY_KEY["win"].endswith(".exe")
          and tu.ARTIFACT_BY_KEY["mac_arm64"].endswith(".zip"))
    check("制品白名单就是值域本身，没有第二份清单",
          set(tu.ALLOWED_ARTIFACTS) == set(tu.ARTIFACT_BY_KEY.values()))


# ══════════════════════════════════════════════ ② 清单严校验
def test_manifest():
    print("\n═══ ② 清单严校验（下载的是可执行程序）═══")
    good = mk_manifest()
    ok = tu.normalize_manifest(good)
    check("全对的清单收得下", ok["ok"] is True, ok.get("why"))
    check("原文字符串与已解析对象两条路都从这里过",
          tu.normalize_manifest(json.dumps(good))["ok"] is True)
    check("三档制品都清洗出来", set(ok["data"]["artifacts"]) == {"win", "mac_arm64", "win_portable"})
    check("清洗后的制品带平台键（后面「怎么装」要问制品自己）",
          ok["data"]["artifacts"]["win"]["key"] == "win")
    check("版本号按整数 build 存下来", ok["data"]["build"] == 234)

    bad_cases = [
        ("不是合法 JSON", "{不是 json", "不是合法的 JSON"),
        ("顶层不是对象", ["a"], "顶层不是对象"),
        ("schema 不认识", mk_manifest(schema="release-v2"), "只认"),
        ("schema 为空", mk_manifest(schema=""), "（空）"),
        ("版本号读不出来", mk_manifest(version="1.234.5"), "读不出来"),
        ("版本号写成 1.0 = 未能确认，不敢当最新版", mk_manifest(version="1.0"), "未能确认"),
        ("没有 artifacts 这一档", {"schema": tu.SCHEMA, "version": "1.234"}, "没有 artifacts"),
    ]
    for name, raw, needle in bad_cases:
        got = tu.normalize_manifest(raw)
        check(f"拒收：{name}", got["ok"] is False and needle in got["why"], got.get("why"))

    art_cases = [
        ("这一档不是对象", {"win": "字符串"}, "不是对象"),
        ("平台键对到别的档名", {"win": mk_entry("win", file=tu.ARTIFACT_MAC_ARM)}, "不敢照着下"),
        ("档名为空", {"win": mk_entry("win", file="")}, "空"),
        ("外部地址", {"win": mk_entry("win", url="https://evil.example.com/x.exe")}, "不是发布直链"),
        ("http 不是 https", {"win": mk_entry("win", url=(
            f"http://{tu.BUCKET_HOST}/{tu.RELEASE_PREFIX}/{tu.ARTIFACT_WINDOWS_SETUP}"))},
         "不是发布直链"),
        ("同桶里多一层前缀", {"win": mk_entry("win", url=(
            f"https://{tu.BUCKET_HOST}/{tu.RELEASE_PREFIX}/sub/{tu.ARTIFACT_WINDOWS_SETUP}"))},
         "不是发布直链"),
        ("没有指纹", {"win": mk_entry("win", sha="")}, "缺少可用的文件指纹"),
        ("指纹不是 64 位十六进制", {"win": mk_entry("win", sha="abc123")}, "sha256"),
        ("字节数不像真的", {"win": mk_entry("win", size=0)}, "字节数不像是真的"),
        ("字节数是字符串垃圾", {"win": mk_entry("win", size="很多")}, "字节数不像是真的"),
    ]
    for name, arts, needle in art_cases:
        got = tu.normalize_manifest(mk_manifest(artifacts=arts))
        check(f"拒收：{name}", got["ok"] is False and needle in got["why"], got.get("why"))

    # 认不出的平台键只该不认识，不该报错：CI 将来多挂一个平台时老工具还得能用
    got = tu.normalize_manifest(mk_manifest(artifacts={"win": mk_entry("win"),
                                                       "symbian": mk_entry("win")}))
    check("不认识的平台键跳过而不是整体失败",
          got["ok"] is True and set(got["data"]["artifacts"]) == {"win"})

    notes_cases = [
        (["第一条", "第二条"], "第一条\n第二条"),
        ("就一句话", "就一句话"),
        (None, ""),
        ([], ""),
    ]
    for raw, want in notes_cases:
        got = tu.normalize_manifest(mk_manifest(artifacts={"win": mk_entry("win")}, notes=raw))
        check(f"说明列表/字符串都收成一段话：{raw!r}"[:40], got["data"]["notes"] == want,
              got["data"]["notes"])


# ══════════════════════════════════════════ ③④ 五种结论与 check 的分派
VERDICT_KEYS = {"state", "why", "current", "latest", "build", "notes",
                "published_at", "artifacts", "artifact", "distance"}


def test_states():
    print("\n═══ ③ 五种互斥结论 ═══")
    with use_build(100):
        ev = tu.evaluate(mk_manifest(version="1.234"), key="win")
        check("本机旧 → update-available", ev["state"] == "update-available", ev)
        check("update-available 带上本机那一档制品",
              ev["artifact"] and ev["artifact"]["key"] == "win")
        check("差距按整数算（distance=134）", ev["distance"] == 134, ev["distance"])
        check("本机版本如实带上", ev["current"] == "1.100", ev["current"])
        check("清单版本原样带上，不重新格式化", ev["latest"] == "1.234")

        ev = tu.evaluate(mk_manifest(version="1.100"), key="win")
        check("同一版不弹（1.100 vs 1.100）", ev["state"] == "up-to-date")
        check("同版 distance=0", ev["distance"] == 0)
        ev = tu.evaluate(mk_manifest(version="1.90"), key="win")
        check("清单比本机旧也是 up-to-date，不是「有更新」", ev["state"] == "up-to-date")
        check("比本机旧时 distance 是负数（真实差距，不是 ±1 的方向）", ev["distance"] == -10,
              ev["distance"])

        with use_build(9):
            check("本机 1.9、清单 1.10 → 有更新（字符串比较会在这里说谎：\"1.9\" > \"1.10\"）",
                  tu.evaluate(mk_manifest(version="1.10"), key="win")["state"] == "update-available")
            check("反过来本机 1.10、清单 1.9 → 已是最新",
                  tu.evaluate(mk_manifest(version="1.9"), key="win")["state"] == "up-to-date")

        ev = tu.evaluate(mk_manifest(artifacts={"mac_arm64": mk_entry("mac_arm64")}), key="win")
        check("有新版但本机这档没有制品 → unsupported", ev["state"] == "unsupported", ev)
        check("unsupported 仍然把新版本与说明告诉人",
              ev["latest"] == "1.234" and ev["notes"] == "修了三个 bug")
        check("unsupported 不给制品（界面因此摆不出「现在就装」）", ev["artifact"] is None)

    with use_build(None):
        ev = tu.evaluate(mk_manifest(version="1.234"), key="win")
        check("本机说不出自己是哪版 → unknown，不是 update-available", ev["state"] == "unknown", ev)
        check("unknown 的 current 如实说「没能确认本机版本」，不印 1.0 冒充",
              ev["current"] == "（没能确认本机版本）", ev["current"])
        check("unknown 指向可执行的排障动作", "print-version" in ev["why"], ev["why"])

    shapes = [("bad-manifest", tu.evaluate("{坏 json", key="win")),
              ("bad-manifest2", tu.evaluate({"ok": False, "why": "清单读不出"}, key="win")),
              ("up-to-date", None), ("unknown", None), ("unsupported", None),
              ("update-available", None)]
    with use_build(100):
        shapes[2] = ("up-to-date", tu.evaluate(mk_manifest(version="1.100"), key="win"))
        shapes[3] = ("unknown", tu.evaluate({"ok": False, "why": "没网"}, key="win"))
        shapes[4] = ("unsupported", tu.evaluate(mk_manifest(
            artifacts={"mac_arm64": mk_entry("mac_arm64")}), key="win"))
        shapes[5] = ("update-available", tu.evaluate(mk_manifest(version="1.234"), key="win"))
    for name, ev in shapes:
        check(f"结论形状完整：{name}（少一个键在界面上就是一个 KeyError）",
              set(ev) == VERDICT_KEYS, sorted(set(VERDICT_KEYS) ^ set(ev)))
    check("结论里没有 ok/has_update 那种第二个口径（分派只看 state）",
          "ok" not in shapes[1][1] and "has_update" not in shapes[5][1])
    with use_build(100):
        check("三种来路都在这一处收口：原文 / normalize 过的 / 拿不回东西的",
              tu.evaluate(tu.normalize_manifest(mk_manifest(version="1.234")), key="win")["state"]
              == "update-available"
              and tu.evaluate({"ok": False, "why": "没网"}, key="win")["state"] == "bad-manifest")
        check("拿不回东西时那句 why 不丢",
              "没网" in tu.evaluate({"ok": False, "why": "没网"}, key="win")["why"])
    check("说明为空时也有话说（界面不会摆一个空框）",
          "没有留下更新说明" in tu.notes_view({"notes": ""}))
    check("notes_view 不替管理员编内容", tu.notes_view({"notes": "真写的那句"}) == "真写的那句")


def test_check_dispatch():
    print("\n═══ ④ check 的分派 ═══")
    def boom(*a, **kw):
        raise AssertionError("不适配本机时不该发任何请求")

    with patch_attr(tu, "fetch_manifest", boom):
        ev = tu.check(key="other")
        check("本机不适配 → 根本不发请求（问了只会把「不适配」说成「对方没文件」）",
              ev["state"] == "unsupported", ev)
    with use_build(100), use_staging():
        ev = tu.check(manifest_fetch={"ok": False, "why": "连不上"}, key="win")
        check("清单拿不回来是 unknown（可重试），不是 bad-manifest（CI 要修）",
              ev["state"] == "unknown", ev)
        check("unknown 的原话不丢", "连不上" in ev["why"])
        ev = tu.check(manifest_fetch={"ok": True, "raw": json.dumps(mk_manifest(version="1.234"))},
                      key="win")
        check("正常通路走到 update-available", ev["state"] == "update-available", ev)
        got = tu.fetch_manifest(key="other")
        check("fetch_manifest 自己被问到没有通道时也如实说不发请求",
              got["ok"] is False and "没有对应的自动更新通道" in got["why"], got["why"])
    with patch_attr(tu, "HAS_DOWNLOAD_VERIFY", False), use_staging():
        got = tu.fetch_manifest(key="win")
        check("下载校验模块没加载时不硬下（清单同样走那条纪律）",
              got["ok"] is False and "download_verify" in got["why"], got["why"])


# ══════════════════════════════ ⑤⑥ 制品自己决定怎么装 + 下载纪律
def test_steps_and_download():
    print("\n═══ ⑤ 「怎么装」问制品自己 ═══")
    win_art = tu.normalize_manifest(mk_manifest(artifacts={"win": mk_entry("win")}),
                                    )["data"]["artifacts"]["win"]
    mac_art = tu.normalize_manifest(mk_manifest(artifacts={"mac_arm64": mk_entry("mac_arm64")})
                                    )["data"]["artifacts"]["mac_arm64"]
    check("Windows 制品 → installer 这条路", tu.handoff_kind(win_art) == "installer")
    check("Mac 制品 → self_replace 这条路", tu.handoff_kind(mac_art) == "self_replace")
    check("认不出的制品 → none", tu.handoff_kind({"key": "symbian"}) == "none")
    steps_win = tu.install_steps(win_art)
    check("Windows 那几步说的是安装包", any("官方安装包" in s["label"] for s in steps_win))
    check("Windows 那几步不承诺 Mac 的说法（不硬来 / 挪到旁边）",
          not any("挪到旁边" in s["detail"] for s in steps_win))
    steps_mac = tu.install_steps(mac_art)
    check("Mac 那几步说的是「放回原来的位置」", any("放回原来的位置" in s["label"] for s in steps_mac))
    check("Mac 那几步如实说目录不可写时不硬来",
          any("不会硬来" in s["detail"] for s in steps_mac))
    steps_none = tu.install_steps({"key": "symbian"})
    check("没有适配的平台就说不做，不猜一个别的平台的包",
          any("不做" in s["detail"] for s in steps_none))
    check("大小未知时说「大小未知」，不说「约 0 MB」",
          any("大小未知" in s["detail"] for s in tu.install_steps({"key": "win", "size": 0})))

    print("\n═══ ⑥ 下载纪律 ═══")
    calls = []

    class FakeDV:
        def __init__(self, res):
            self.res = res

        def fetch(self, url, dest, **kw):
            calls.append({"url": url, "dest": Path(dest), **kw})
            return dict(self.res)

    with use_staging() as st:
        art = dict(mac_art, size=200)
        dv = FakeDV({"ok": True, "path": str(st.dir / art["name"]), "bytes": 200,
                     "sha256": "a" * 64})
        with patch_attr(tu, "_dv", dv):
            say = Recorder()
            got = tu.download(art, log=say, dest_dir=st.dir)
            check("下载成功给出路径与指纹", got["ok"] is True and got["sha256"] == "a" * 64, got)
            check("落点在包外 staging", str(calls[-1]["dest"]).startswith(str(st.dir)))
            check("把清单给的指纹交给校验层（不是下完再看一眼）",
                  calls[-1]["expected_sha256"] == "a" * 64)
            check("上限取 size 的两倍但不超 1GB", calls[-1]["max_bytes"] == 400,
                  calls[-1]["max_bytes"])
            check("指纹核对通过这句话要说出来", say.has("指纹核对通过"))
        big = dict(mac_art, size=900_000_000)
        calls.clear()
        with patch_attr(tu, "_dv", FakeDV({"ok": True, "path": str(st.dir / "x"),
                                           "bytes": 1, "sha256": "a" * 64})):
            got = tu.download(big, dest_dir=st.dir)
            check("上限封顶在 1GB（不被当成自由下载地址）",
                  calls[-1]["max_bytes"] == tu.ARTIFACT_MAX_BYTES, calls[-1]["max_bytes"])
        for name, art_, needle in [
            ("缺下载地址", {"key": "win", "url": "", "sha256": "a" * 64}, "缺少可用的下载地址"),
            ("缺指纹", {"key": "win", "url": "https://x/y", "sha256": ""}, "缺少可用的下载地址"),
            ("制品为空", {}, "缺少可用的下载地址"),
        ]:
            calls.clear()
            with patch_attr(tu, "_dv", FakeDV({"ok": True, "path": "z", "bytes": 1})):
                got = tu.download(art_, dest_dir=st.dir)
                check(f"{name}：没有开始下载", got["ok"] is False and needle in got["why"], got)
                check(f"{name}：确实一次都没下", not calls)
        # 字节数复核：指纹相同必然相同，但「声明 200、实到 60」要在这一格抓住
        calls.clear()
        pkg = st.dir / mac_art["name"]
        pkg.write_bytes(b"half-package")

        class FakeDVPartial:
            def fetch(self, url, dest, **kw):
                calls.append(kw)
                Path(dest).write_bytes(b"half-package")
                return {"ok": True, "path": str(dest), "bytes": 6, "sha256": "a" * 64}

        with patch_attr(tu, "_dv", FakeDVPartial()):
            got = tu.download(dict(mac_art, size=200), dest_dir=st.dir)
            check("声明字节数与实到不符 → 判「包不完整」",
                  got["ok"] is False and "包不完整" in got["why"], got)
            check("不完整的包当场删掉，不留残件", not pkg.exists())
        with patch_attr(tu, "HAS_DOWNLOAD_VERIFY", False):
            got = tu.download(mac_art, dest_dir=st.dir)
            check("校验模块缺失时不做自动更新", got["ok"] is False and "没能加载" in got["why"])
        with patch_attr(tu, "_dv", FakeDV({"ok": False, "path": "", "why": "HTTP 404"})):
            got = tu.download(mac_art, dest_dir=st.dir)
            check("下载层失败的原话透出去", got["ok"] is False and "404" in got["why"])


# ══════════════════════════════════════ ⑦⑧ 安装包与装完再对版本
def test_installer_and_verify():
    print("\n═══ ⑦ Windows 安装包：必须等它跑完 ═══")
    d = tmp_dir("upd_setup_")
    try:
        exe = d / "B2bCollectToolSetup.exe"
        seen = []

        def ok_runner(cmd, **kw):
            seen.append((cmd, kw))
            return _NS(returncode=0, stderr=b"")

        got = tu.run_installer(str(d / "not_here.exe"), run_fn=ok_runner)
        check("安装包不在位就不跑", got["ok"] is False and "不在了" in got["why"], got["why"])
        check("不存在的包一次都没跑安装", not seen)
        (d / "wrong.zip").write_bytes(b"x")
        got = tu.run_installer(str(d / "wrong.zip"), run_fn=ok_runner)
        check("拿到 .zip 判「不是可执行的安装包」（清单写错或人手工传错）",
              got["ok"] is False and "可执行的安装包" in got["why"], got["why"])
        exe.write_bytes(b"")
        got = tu.run_installer(str(exe), run_fn=ok_runner)
        check("空文件不当成安装包", got["ok"] is False and "空文件" in got["why"], got["why"])
        exe.write_bytes(b"MZ")
        say = Recorder()
        got = tu.run_installer(str(exe), log=say, run_fn=ok_runner)
        check("退出码 0 → 成功", got["ok"] is True, got)
        check("静默参数是 /S", seen[-1][0][1] == "/S", seen[-1][0])
        check("话术里说清下一步是再对一次版本", say.has("再对一次"))
        got = tu.run_installer(str(exe), run_fn=lambda cmd, **kw: _NS(returncode=5,
                                                                      stderr=b"abort_locked"))
        check("退出码非 0 → 失败并带上原因",
              got["ok"] is False and got["code"] == 5 and "abort_locked" in got["why"], got)
        check("失败时明说旧版本还在（工具仍能打开）", "旧版本还在原地" in got["why"])
        got = tu.run_installer(str(exe), run_fn=lambda cmd, **kw: _NS(returncode=0, stderr=None))
        check("退出码 0 但 stderr 为空时不拼出 None 字样", got["ok"] is True)

        def timeout_runner(cmd, **kw):
            raise __import__("subprocess").TimeoutExpired(cmd, 1)

        got = tu.run_installer(str(exe), run_fn=timeout_runner)
        check("超时不是「装坏了」而是「这边不能再等」",
              got["ok"] is False and "不等了" in got["why"], got["why"])
        check("超时的话里给出人可做的两步", "让它跑完" in got["why"] and "手动双击" in got["why"])

        def oserror_runner(cmd, **kw):
            raise PermissionError(13, "denied")

        got = tu.run_installer(str(exe), run_fn=oserror_runner)
        check("安装包没能启动（被安全软件拦这类）单独成话",
              got["ok"] is False and "没能启动" in got["why"], got["why"])
    finally:
        shutil.rmtree(d, ignore_errors=True)

    print("\n═══ ⑧ 装完再对一次新包里的构建戳 ═══")
    d = tmp_dir("upd_verify_")
    try:
        nt_os = _Passthrough(os, name="nt")
        bin_dir = d / "win" / "B2bCollectTool"
        (bin_dir / "_internal").mkdir(parents=True)
        exe = bin_dir / "B2bCollectTool.exe"
        exe.write_bytes(b"MZ")
        with patch_attr(tu, "os", nt_os):
            cands = tu.stamp_candidates_for(exe)
            check("onedir 布局：先认 _internal 再认同级",
                  cands[0].name == tv.BUILD_STAMP_NAME and cands[0].parent.name == "_internal",
                  cands)
            (cands[0]).write_text(json.dumps({"build": 234}), encoding="utf-8")
            got = tu.verify_installed_build(exe, 234)
            check("戳与清单对得上 → 确认成功", got["ok"] is True and got["build"] == 234, got)
            got = tu.verify_installed_build(exe, 999)
            check("戳与清单对不上 → 红，并让人去确认界面版本号",
                  got["ok"] is False and "对不上" in got["why"] and "版本号" in got["why"], got)
            got = tu.verify_installed_build(exe, 0)
            check("没给期望值时只报读到的那一份（不假装核对过）", got["ok"] is True)
        mac_dir = d / "Mac" / "B2bCollectTool.app" / "Contents" / "MacOS"
        mac_dir.mkdir(parents=True)
        (mac_dir / "B2bCollectTool").write_bytes(b"x")
        (mac_dir.parent / "Resources").mkdir()
        cands = tu.stamp_candidates_for(mac_dir / "B2bCollectTool")
        check(".app 布局：Resources 在 Contents 下",
              cands[0] == mac_dir.parent / "Resources" / tv.BUILD_STAMP_NAME, cands)
        (cands[0]).write_text(json.dumps({"build": "abc"}), encoding="utf-8")
        got = tu.verify_installed_build(mac_dir / "B2bCollectTool")
        check("戳里是垃圾构建号 → 判「没能确认」而不是「没装上」",
              got["ok"] is False and got["certain"] is False
              and "没能确认" in got["why"] and "没装上" in got["why"], got)
        (cands[0]).write_text("[" , encoding="utf-8")
        got = tu.verify_installed_build(mac_dir / "B2bCollectTool")
        check("戳读成坏 JSON 也只是没能确认（不抛）", got["ok"] is False and got["path"] == "")
        got = tu.verify_installed_build(d / "没有这个文件.exe")
        check("路径根本不是文件时不列候选（不去猜一个目录）",
              tu.stamp_candidates_for(d / "没有这个文件.exe") == [] and got["ok"] is False)
        check("找不到戳时把「随包戳漏收是打包清单的事」说清",
                "随包戳漏收" in got["why"], got["why"])
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ══════════════════════════════════════ ⑨⑩⑪ 交接与自替换
def test_apply_update():
    print("\n═══ ⑨ 交接的分岔看包自己的形状 ═══")
    d = tmp_dir("upd_apply_")
    try:
        empty = d / "empty.zip"
        empty.write_bytes(b"")
        got = tu.apply_update(str(empty))
        check("空文件不动任何东西", got["ok"] is False and "空文件" in got["why"], got["why"])

        weird = d / "readme.txt"
        weird.write_text("不是包", encoding="utf-8")
        got = tu.apply_update(str(weird))
        check("认不出的形状 → kind=none，不「当安装包跑一下看看」",
              got["ok"] is False and got["kind"] == "none" and "没有动任何东西" in got["why"], got)

        # 残件与垃圾文件走的是同一个出口，而不是一个栈：交接进程 stdio 进黑洞，
        # 崩在这里就正是本模块要避免的那句「工具关了、什么都没装、屏幕上一个字都没有」
        broken = d / "B2bCollectTool_Mac_arm64.zip"
        broken.write_bytes(b"PK\x03\x04" + b"truncated-half-of-a-zip")
        try:
            got = tu.apply_update(str(broken))
            crashed = None
        except Exception as e:                       # noqa: BLE001
            got, crashed = None, e
        check("不是合法 zip 的包判「形状认不出」而不是抛栈", crashed is None and got
              and got["kind"] == "none", crashed)
        check("zip_app_entries 对垃圾输入返回空列表（不是 Mac 包的形状）",
              tu.zip_app_entries(broken) == [])

        half = make_mac_zip(d / "half.zip", extra_top=["随便一个文件.txt"])
        # 顶层只有一个 .app 时仍然算 self_replace（多一个散文件不是第二个应用包）
        check("顶层 .app 个数为 1 才算自替换包", tu._kind_for(half) == "self_replace")
        two = make_mac_zip(d / "two.zip", extra_top=["Other.app/Contents/MacOS/Other"])
        check("顶层两个 .app 判「不像可用的包」（半个包与错平台的 zip 都停在这里）",
              tu._kind_for(two) == "none")
        noapp = d / "noapp.zip"
        with zipfile.ZipFile(str(noapp), "w") as zf:
            zf.writestr("a.txt", b"x")
        got = tu.apply_update(str(noapp))
        check("没有 .app 的 zip 不自动安装", got["kind"] == "none", got)

        exe = d / "B2bCollectToolSetup.exe"
        exe.write_bytes(b"MZ")
        waited = []

        def wait_never(pid, timeout, **kw):
            waited.append((pid, timeout))
            return False

        got = tu.apply_update(str(exe), wait_pid=12345, wait_fn=wait_never)
        check("旧实例没真退出就不去替换正在运行的东西",
              got["ok"] is False and "还没退出" in got["why"] and waited, got["why"])
        check("等不到时把「手动再跑一次」说清", "手动跑一次升级" in got["why"])
        check("等待有界（用交接默认上限，不是无限等）", waited[0][1] == tu.INSTALLER_LOCK_WAIT_SECONDS)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_self_replace():
    print("\n═══ ⑩⑪ macOS 自替换：每个失败分支都不许留下半个状态 ═══")
    spawns = []
    spawn_ok = lambda cmd: spawns.append(list(cmd))          # noqa: E731

    with sr_case("missing") as c:
        got = tu._self_replace(str(c.d / "没了.zip"), Recorder(), _sr_out())
        check("下载好的包不在了：一句话停下，没碰本机",
              got["ok"] is False and "不在了" in got["why"], got["why"])

    with sr_case("twoapps") as c:
        two = make_mac_zip(c.d / "two.zip", extra_top=["Other.app/Contents/MacOS/Other"])
        got = tu._self_replace(str(two), Recorder(), _sr_out(), bundle=c.target)
        check("顶层不是一个应用包：没有替换任何东西",
              got["ok"] is False and "没有替换任何东西" in got["why"], got["why"])
        check("顶层两个 .app 时不猜哪一个才是工具",
              (c.target / "Contents" / "MacOS" / "B2bCollectTool").read_bytes() == b"old")

    with sr_case("nobundle") as c:
        got = tu._self_replace(str(c.pkg), Recorder(), _sr_out(), bundle=None)
        check("认不出应用本体位置时不猜目录，留包 + 给人工步骤",
              got["ok"] is False and "拖进" in got["why"] and c.target.exists(), got["why"])

    with sr_case("readonly") as c:
        ro_root = c.d / "ReadOnly"
        ro_root.mkdir()
        (ro_root / "B2bCollectTool.app").mkdir()
        ro_root.chmod(0o500)
        try:
            got = tu._self_replace(str(c.pkg), Recorder(), _sr_out(),
                                   bundle=ro_root / "B2bCollectTool.app")
            check("那一层不可写时不硬来（只读卷/权限）",
                  got["ok"] is False and "不可写" in got["why"], got["why"])
        finally:
            ro_root.chmod(0o700)

    with sr_case("unpackfail") as c:
        def unpack_fail(zip_path, into):
            return {"ok": False, "why": "没能解包：ditto 退出码 1"}

        got = tu._self_replace(str(c.pkg), Recorder(), _sr_out(), bundle=c.target,
                               unpack_fn=unpack_fail)
        check("解包失败原话透出，且旧包还在",
              got["ok"] is False and "没能解包" in got["why"] and c.target.exists(), got["why"])

    with sr_case("nameother") as c:
        # 应用名是从 zip 自己解出来的条目读的，所以这一形必须真的递一个别的名字的包
        other = make_mac_zip(c.d / "other.zip", app_name="RenamedTool.app")
        got = tu._self_replace(str(other), Recorder(), _sr_out(), bundle=c.target,
                               unpack_fn=fake_unpack("RenamedTool.app"))
        check("包里的应用名与本机正在跑的不一样时不盖（不是同一个工具的上下版）",
              got["ok"] is False and "名字不一样" in got["why"], got["why"])
        check("名字不符时原位一个字节都没动",
              (c.target / "Contents" / "MacOS" / "B2bCollectTool").read_bytes() == b"old")

    with sr_case("noexecdir") as c:
        got = tu._self_replace(str(c.pkg), Recorder(), _sr_out(), bundle=c.target,
                               unpack_fn=fake_unpack(with_macos_dir=False))
        check("解出来的包少了可执行那一层 → 不替换",
              got["ok"] is False and "少了可执行那一层" in got["why"], got["why"])
        check("缺层的半成品不会被放到原位",
              (c.target / "Contents" / "MacOS" / "B2bCollectTool").read_bytes() == b"old")

    with sr_case("renamefail") as c:
        # 旧版本挪不动：用固定时钟把 aside 的名字算出来，先占一个非空目录
        T = 1_800_000_000
        aside = c.target.with_name(f"{c.target.stem}.old-{T}.app")
        aside.mkdir()
        (aside / "占位.txt").write_text("x", encoding="utf-8")
        clock = _Passthrough(time, time=lambda: T, sleep=lambda s: None)
        with patch_attr(tu, "time", clock):
            got = tu._self_replace(str(c.pkg), Recorder(), _sr_out(), bundle=c.target,
                                   unpack_fn=fake_unpack(), spawn_fn=spawn_ok)
        check("旧版本挪不动 → 当场停下，什么都不改",
              got["ok"] is False and "挪不动" in got["why"], got["why"])
        check("挪不动时旧包还在原位", c.target.exists())

    with sr_case("rollback") as c:
        sub = FakeSubprocess(fail_ditto=True)
        say = Recorder()
        with patch_attr(tu, "subprocess", sub):
            got = tu._self_replace(str(c.pkg), say, _sr_out(), bundle=c.target,
                                   unpack_fn=fake_unpack(), spawn_fn=spawn_ok)
        check("新版本放不进去 → 已退回原来的版本",
              got["ok"] is False and "已退回原来的版本" in got["why"], got["why"])
        check("回滚后旧工具仍在原位（宁可版本不变，也不留下没有工具的机器）",
              (c.target / "Contents" / "MacOS" / "B2bCollectTool").read_bytes() == b"old")
        check("回滚成功时不把「挪不回来」那句假话也一起说", "回滚也没挪回来" not in say.text())

    with sr_case("rollbackfail") as c:
        # 回滚也失败：最难看的一形，必须留下那句话与旁注
        sub2 = FakeSubprocess(fail_ditto="create_then_fail")
        say2 = Recorder()
        with patch_attr(tu, "subprocess", sub2):
            got = tu._self_replace(str(c.pkg), say2, _sr_out(), bundle=c.target,
                                   unpack_fn=fake_unpack(), spawn_fn=spawn_ok)
        check("回滚也没挪回来时把旁边那份的路径说清",
              "回滚也没挪回来" in say2.text() and got["ok"] is False, say2.text())
        check("这种最坏形状不谎报成功", got["ok"] is False and not got["relaunched"], got)

    with sr_case("success") as c:
        # 成功：版本对上才清旁边那份
        sub3 = FakeSubprocess()
        say3 = Recorder()
        spawns.clear()
        with patch_attr(tu, "subprocess", sub3):
            got = tu._self_replace(str(c.pkg), say3, _sr_out(), bundle=c.target, expect_build=234,
                                   unpack_fn=fake_unpack(build=234), spawn_fn=spawn_ok)
        check("装好且版本对上 → ok", got["ok"] is True and got["verified"]["ok"] is True, got)
        check("原位确实是新包（不是嘴上说装好了）",
              (c.target / "Contents" / "MacOS" / "B2bCollectTool").read_bytes() == b"binary")
        check("装好后拉起的是刚装的那个应用本体",
              spawns and spawns[-1][-1].endswith("B2bCollectTool.app"), spawns)
        leftovers = [p.name for p in c.app_root.glob("B2bCollectTool.old-*.app")]
        check("一切就绪（版本也对上）之后才清旁边那份旧副本",
              any(x[0] == "/bin/rm" for x in sub3.cmds) and not leftovers, leftovers)
        check("版本对上时不留 aside", got["aside"] == "", got["aside"])

    with sr_case("buildmismatch") as c:
        # 版本没对上：旧副本必须留着并说可以换回来
        sub4 = FakeSubprocess()
        say4 = Recorder()
        with patch_attr(tu, "subprocess", sub4):
            got = tu._self_replace(str(c.pkg), say4, _sr_out(), bundle=c.target, expect_build=999,
                                   unpack_fn=fake_unpack(build=234), spawn_fn=spawn_ok)
        check("版本对不上时不谎报：aside 留着", got["aside"] and Path(got["aside"]).exists(),
              got["aside"])
        check("并把「可以换回来」这句真话说出来", "对不上" in say4.text(), say4.text())
        check("版本没对上时不清旧副本（那是唯一的退路）",
              not any(x[0] == "/bin/rm" for x in sub4.cmds), sub4.cmds)

    with sr_case("launchfail") as c:
        # 拉不起来：装这一步确实成了，坏的只是「顺手打开」
        sub5 = FakeSubprocess()
        say5 = Recorder()

        def no_launch(cmd):
            raise OSError("拉不起来")

        with patch_attr(tu, "subprocess", sub5):
            got = tu._self_replace(str(c.pkg), say5, _sr_out(), bundle=c.target, expect_build=234,
                                   unpack_fn=fake_unpack(build=234), spawn_fn=no_launch)
        check("没能自动打开时仍判装好，并给双击打开那一步",
              got["ok"] is True and got["relaunched"] is False and "双击" in got["why"], got)
        check("拉不起来时不许清掉旁边那份旧副本",
              not any(x[0] == "/bin/rm" for x in sub5.cmds), sub5.cmds)

    with sr_case("rmfail_rc") as c:
        # 清不掉旧副本要说话（它占几百 MB）：rm 跑了但返非 0 也是清不掉
        sub6 = FakeSubprocess(rm_ok=False)
        say6 = Recorder()
        with patch_attr(tu, "subprocess", sub6):
            got = tu._self_replace(str(c.pkg), say6, _sr_out(), bundle=c.target, expect_build=234,
                                   unpack_fn=fake_unpack(build=234), spawn_fn=spawn_ok)
        check("rm 返非 0 时留下一句「可以手工删」（不能只挡住崩的那一种）",
              got["ok"] is True and "没清掉" in say6.text(), say6.text())
        leftovers = [p.name for p in c.app_root.glob("B2bCollectTool.old-*.app")]
        check("那句话里带上还在原地的旧副本路径（人能照着删）",
              leftovers and str(leftovers[0]) in say6.text(), (leftovers, say6.text()))

    with sr_case("rmfail_raise") as c:
        sub7 = FakeSubprocess(rm_raises=True)
        say7 = Recorder()
        with patch_attr(tu, "subprocess", sub7):
            got = tu._self_replace(str(c.pkg), say7, _sr_out(), bundle=c.target, expect_build=234,
                                   unpack_fn=fake_unpack(build=234), spawn_fn=spawn_ok)
        check("rm 自己抱了也只说一句，不改升级结论",
              got["ok"] is True and "没清掉" in say7.text(), (got, say7.text()))



def test_installer_handoff_chain():
    print("\n═══ ⑨b Windows 那条合流：跑完 → 对版本 → 拉起 ═══")
    d = tmp_dir("upd_win_")
    try:
        exe = d / "B2bCollectToolSetup.exe"
        exe.write_bytes(b"MZ")
        nt_os = _Passthrough(os, name="nt")
        bin_dir = d / "installed"
        (bin_dir / "_internal").mkdir(parents=True)
        target = bin_dir / "B2bCollectTool.exe"
        target.write_bytes(b"MZ")
        (bin_dir / "_internal" / tv.BUILD_STAMP_NAME).write_text(
            json.dumps({"build": 234}), encoding="utf-8")
        spawns = []
        with patch_attr(tu, "os", nt_os), \
                patch_attr(tu, "subprocess", FakeSubprocess()):
            got = tu.apply_update(str(exe), expect_build=234, relaunch_target=str(target),
                                  run_fn=lambda cmd, **kw: _NS(returncode=0, stderr=b""),
                                  spawn_fn=lambda cmd: spawns.append(list(cmd)))
        check("Windows 交接走 installer 这条路", got["kind"] == "installer", got)
        check("装好且版本对上 → ok + relaunched",
              got["ok"] is True and got["relaunched"] is True, got)
        check("拉起的是 relaunch_target，不是当前解释器",
              spawns and spawns[0][0] == str(target), spawns)
        check("对版本这一步读的是新包同级 _internal 里的戳",
              got["verified"]["path"].endswith("_internal/version.json"), got["verified"])

        spawns.clear()
        say_order = Recorder()
        with patch_attr(tu, "os", nt_os), \
                patch_attr(tu, "subprocess", FakeSubprocess()):
            got = tu.apply_update(str(exe), expect_build=999, relaunch_target=str(target),
                                  log=say_order,
                                  run_fn=lambda cmd, **kw: _NS(returncode=0, stderr=b""),
                                  spawn_fn=lambda cmd: spawns.append(list(cmd)))
        check("版本对不上不拦拉起（新版工具仍然该能用）",
              got["ok"] is True and got["verified"]["ok"] is False, got)
        check("但那句话排在拉起之前（窗口一开没人再读日志）",
              say_order.text().index("对不上") < say_order.text().index("正在重新打开"),
              say_order.text())

        spawns.clear()
        got = tu.apply_update(str(exe), relaunch_target=str(target),
                              run_fn=lambda cmd, **kw: _NS(returncode=2, stderr=b"locked"),
                              spawn_fn=lambda cmd: spawns.append(list(cmd)))
        check("安装包失败就不拉起（旧版本还在）",
              got["ok"] is False and got["relaunched"] is False and not spawns, got)

        spawns.clear()
        got = tu.apply_update(str(exe), relaunch_target=str(target),
                              run_fn=lambda cmd, **kw: _NS(returncode=0, stderr=b""),
                              spawn_fn=lambda cmd: (_ for _ in ()).throw(OSError("起不来")))
        check("「装好了但没打开」与「没装上」是两句话",
              got["ok"] is True and got["relaunched"] is False and "双击" in got["why"], got)
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ══════════════════════════════════════ ⑫⑬ 报告
def test_report():
    print("\n═══ ⑫ 交接报告：stdio 进黑洞也要留下一句话 ═══")
    with use_staging() as st:
        res = {"ok": False, "kind": "self_replace", "why": "放不进去，已退回原来的版本",
               "relaunched": False, "fell_back": True, "expect_build": 234,
               "verified": {"ok": False, "build": 0}, "aside": "/tmp/x.old.app"}
        got = tu.write_report(res)
        check("报告落在包外 staging", Path(got["path"]).parent == st.dir, got)
        raw = json.loads(Path(got["path"]).read_text(encoding="utf-8"))
        for key in ("ts", "ok", "kind", "why", "relaunched", "fell_back", "expect_build",
                    "installed_build", "verified", "aside", "handoff_pid"):
            check(f"报告里有 {key} 这一格", key in raw, sorted(raw))
        check("fell_back 与 verified 分开说（兜底开了旧版 ≠ 版本已确认）",
              raw["fell_back"] is True and raw["verified"] is False, raw)
        check("installed_build 取的是新包自报那一份", raw["installed_build"] == 0)
        check("handoff_pid 是写报告这个进程（取证能对上号）", raw["handoff_pid"] == os.getpid())

        say = Recorder()
        bad_dir = st.dir / "占位目录"
        bad_dir.mkdir(exist_ok=True)          # 是个目录：往它写必然 OSError
        bad = tu.write_report(res, log=say, dest=bad_dir)
        check("报告写不下去时如实返回失败", bad["ok"] is False, bad)
        check("并且只说一句，不改升级结论", say.has("没能落到报告"), say.text())

        print("\n═══ ⑬ 读报告：读不出就闭嘴，不编一句 ═══")
        p = st.dir / "r.json"
        check("没有报告文件 → 空字典（界面不播）", tu.read_report(p) == {})
        p.write_text("{坏 json", encoding="utf-8")
        check("报告读坏 → 空字典", tu.read_report(p) == {})
        p.write_text("[1, 2]", encoding="utf-8")
        check("顶层不是对象 → 空字典", tu.read_report(p) == {})
        now = 1_800_000_000.0
        p.write_text(json.dumps({"ts": now - 10, "ok": False}), encoding="utf-8")
        check("新鲜报告读得出来", tu.read_report(p, now=now)["ok"] is False)
        p.write_text(json.dumps({"ts": now - tu.REPORT_MAX_AGE_SECONDS - 10, "ok": True}),
                     encoding="utf-8")
        check("太老的报告不播（上周的失败不该让人查一个不存在的问题）", tu.read_report(p, now=now) == {})
        p.write_text(json.dumps({"ts": now + 3600}), encoding="utf-8")
        check("时间戳在未来（本机时钟被改过）不播", tu.read_report(p, now=now) == {})
        p.write_text(json.dumps({"ts": 0}), encoding="utf-8")
        check("ts 缺失或为 0 不播", tu.read_report(p) == {})
        p.write_text(json.dumps({"ts": "很久以前"}), encoding="utf-8")
        check("ts 不是数不播", tu.read_report(p) == {})


# ══════════════════════════════════════ ⑭⑮ 免打扰与命令行
def test_snooze_and_cli():
    print("\n═══ ⑭ 免打扰按版本号记 ═══")
    with use_build(100):
        ev = tu.evaluate(mk_manifest(version="1.234"), key="win")
    check("update-available 且没记过 → 该弹", tu.should_prompt(ev, {}) is True)
    check("同一版本已经说过不要提醒 → 安静",
          tu.should_prompt(ev, {tu.SNOOZE_KEY: "1.234"}) is False)
    check("出了更新的版本还会正常提醒（不是全局关掉）",
          tu.should_prompt(ev, {tu.SNOOZE_KEY: "1.233"}) is True)
    for state in ("up-to-date", "unknown", "unsupported", "bad-manifest"):
        check(f"{state} 不弹窗（「没能确认」不是「有新版本」）",
              tu.should_prompt({"state": state, "latest": "1.234"}, {}) is False)
    check("latest 为空时不弹（免得记住一个空版本号）",
          tu.should_prompt({"state": "update-available", "latest": ""}, {}) is False)
    check("prefs 不是字典也不崩", tu.should_prompt(ev, None) is True
          and tu.dismissed_version(None) == "")
    saved = {"theme": "dark"}
    out = tu.with_dismissed("1.234", saved)
    check("写回的偏好不丢原有键（纯函数，落盘交给界面）",
          out["theme"] == "dark" and out[tu.SNOOZE_KEY] == "1.234")
    check("with_dismissed 不改调用方那份字典（否则界面内存里的偏好被悄悄写过一次）",
          saved == {"theme": "dark"}, saved)
    check("with_dismissed 对非字典输入也不崩", tu.with_dismissed("1.234", None)[tu.SNOOZE_KEY]
          == "1.234")

    print("\n═══ ⑮ 命令行与界面共用一份解析 ═══")
    p = tu.parse_argv([tu.APPLY_FLAG, "/tmp/a.zip", tu.WAIT_PID_FLAG, "123",
                       tu.EXPECT_BUILD_FLAG, "234", tu.RELAUNCH_FLAG, "/tmp/B.app"])
    check("四个开关一次收齐",
          p == {"apply": "/tmp/a.zip", "wait_pid": 123, "expect_build": 234,
                "relaunch": "/tmp/B.app", "requested": True}, p)
    check("--apply-update 没跟路径时如实报「请求了但没有输入」",
          tu.parse_argv([tu.APPLY_FLAG])["requested"] is True
          and tu.parse_argv([tu.APPLY_FLAG])["apply"] == "")
    check("负数与非数字当没给（0），不拿它去比大小",
          tu.parse_argv([tu.WAIT_PID_FLAG, "-1", tu.EXPECT_BUILD_FLAG, "很多"])["expect_build"] == 0)
    check("认不出的参数原样留给界面（不在这里吞掉）",
          tu.parse_argv(["--some-gui-flag", "x"])["requested"] is False)
    check("开关名就是界面拼命令用的那四个",
          (tu.APPLY_FLAG, tu.WAIT_PID_FLAG, tu.EXPECT_BUILD_FLAG, tu.RELAUNCH_FLAG)
          == ("--apply-update", "--wait-pid", "--expect-build", "--relaunch"))

    def run_cli(argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = tu.main(list(argv))
        return int(rc or 0), buf.getvalue()

    fake = {"state": "up-to-date", "why": "", "current": "1.100", "latest": "1.100",
            "build": 100, "notes": "就一句", "published_at": "", "artifacts": {}, "artifact": None,
            "distance": 0}
    with patch_attr(tu, "check", lambda *a, **kw: dict(fake)):
        rc, text = run_cli(["--check"])
        check("已是最新 → 退出码 0", rc == 0, rc)
    fake2 = dict(fake, state="update-available", latest="1.234", distance=134,
                 artifact=tu.normalize_manifest(mk_manifest())["data"]["artifacts"]["win"])
    with patch_attr(tu, "check", lambda *a, **kw: dict(fake2)):
        rc, text = run_cli(["--check"])
        check("有可装的新版本 → 退出码 10", rc == 10, (rc, text))
        check("给人看的输出里有本机、最新与说明",
              "本机：" in text and "最新：" in text and "就一句" in text, text)
        check("并指了下一步（--download）", "--download" in text, text)
        rc, text = run_cli(["--check-json"])
        check("--check-json 输出合法 JSON 且不夹带 artifacts",
              json.loads(text.strip())["state"] == "update-available" and "artifacts" not in text,
              text[:120])
        check("--check-json 不打进度话术（脚本读的是 stdout）", "本机：" not in text)
    fake3 = dict(fake, state="unknown", why="没网", latest="", current="")
    with patch_attr(tu, "check", lambda *a, **kw: dict(fake3)):
        rc, text = run_cli(["--check"])
        check("问不出结论 → 退出码 20", rc == 20, rc)
        check("「没能确认」这类原话照样打出来", "没网" in text, text)
        rc, text = run_cli(["--download"])
        check("没有可下载的更新时不下（且按状态给码）", rc == 20 and "没有可以下载的更新" in text,
              (rc, text))
    with patch_attr(tu, "check", lambda *a, **kw: dict(fake)):
        rc, text = run_cli(["--download"])
        check("up-to-date 时 --download 退 0（不是失败）", rc == 0, (rc, text))
    with patch_attr(tu, "check", lambda *a, **kw: dict(fake2)), \
            patch_attr(tu, "download", lambda art, log=None: {"ok": False, "path": "",
                                                              "why": "包不完整"}):
        rc, text = run_cli(["--download"])
        check("下载失败 → 20 并带上原因", rc == 20 and "包不完整" in text, (rc, text))
    with use_staging():
        rc, text = run_cli([tu.APPLY_FLAG])
        check("--apply-update 后面没包 → 20 并说清用法", rc == 20 and "要跟一个下载好的包路径" in text,
              (rc, text))
        rc, text = run_cli([tu.APPLY_FLAG, "/tmp/根本不存在.zip"])
        check("交接走的是同一个 apply_update（不另实现一份）",
              rc == 20 and "不在了" in text, (rc, text))
        rc, text = run_cli([])
        check("不带参数打帮助并退 0", rc == 0 and "用法" in text, (rc, text))
        rc, text = run_cli(["-h"])
        check("-h 同一条路", rc == 0)


# ══════════════════════════════════════ ⑯ 跨文件单一口径
def test_single_source():
    print("\n═══ ⑯ 与发布那头、界面那头的单一口径 ═══")
    script = Path(__file__).resolve().parent.parent / ".github" / "scripts" / "upload_release_to_oss.py"
    check("发布上传脚本在位（白名单对拍的前提）", script.is_file(), str(script))
    text = script.read_text(encoding="utf-8") if script.is_file() else ""
    m = re.search(r"ALLOWED_NAMES\s*=\s*\(([^)]*)\)", text, re.S)
    names = tuple(re.findall(r'"([^"]+)"', m.group(1))) if m else ()
    want_names = set(tu.ALLOWED_ARTIFACTS) | set(tu.MANIFEST_BY_KEY.values())
    check("制品白名单与上传那头逐字一致（少一个名字=有新版本却装不上）",
          set(names) == want_names,
          sorted(set(names) ^ want_names) or names)
    check("发布通道需要的两份清单全在那个白名单里（清单上不了链=永远提不到更新）",
          set(tu.MANIFEST_BY_KEY.values()) <= set(names),
          (sorted(tu.MANIFEST_BY_KEY.values()), names))
    host = re.search(r'ENDPOINT\s*=\s*"([^"]+)"', text)
    bucket = re.search(r'BUCKET\s*=\s*"([^"]+)"', text)
    prefix = re.search(r'RELEASE_PREFIX\s*=\s*"([^"]+)"', text)
    check("桶与发布前缀两处同源（本模块拼的直链就是那头放的位置）",
          f"{bucket.group(1)}.{host.group(1)}" == tu.BUCKET_HOST
          and prefix.group(1) == tu.RELEASE_PREFIX,
          (bucket and host and prefix.group(0))[:80] if bucket and host and prefix else "读不到")
    check("清单直链的拼法与上传那头的 key 拼法能遇到同一个位置（两头各拼一半就遇不上）",
          tu.manifest_url("win") ==
          f"https://{bucket.group(1)}.{host.group(1)}/{prefix.group(1)}/{tu.MANIFEST_BY_KEY['win']}",
          tu.manifest_url("win"))
    check("清单按 JSON 的 Content-Type 上传（当普通文件缓存=用户读到上周那份）",
          '".json"' in text and "application/json" in text)

    # schema、档名、版本算法这三个常量只能从 tool_update / tool_version 取。
    # 向这个对拍提问的对象是**生成清单那一头**（不是上传那头：上传根本不需要认识
    # schema，把它逼到那里去只会得到一份为了不报错而写的假绿）。
    gen = Path(__file__).resolve().parent.parent / ".github" / "scripts" / "make_release_manifest.py"
    check("发布清单生成脚本在位（没有这一头，读侧的校验只不过是一段没人写的字段）",
          gen.is_file(), str(gen))
    gensrc = gen.read_text(encoding="utf-8") if gen.is_file() else ""
    check("生成那头用的是读那头的 schema 常量，不自己写一串 'release-v1'",
          "tu.SCHEMA" in gensrc and f'"{tu.SCHEMA}"' not in gensrc, tu.SCHEMA)
    check("档名/平台键/清单文件名全从 tool_update 取（写两份迟早只改一处）",
          all(n in gensrc for n in ("tu.ARTIFACT_BY_KEY", "tu.MANIFEST_BY_KEY",
                                  "tu.BUCKET_HOST", "tu.RELEASE_PREFIX")))
    check("生成那头不自己拼版本号与桶名（无第二份字面量）",
          not re.search(r'"(images6396289|0817899624|B2bCollectTool[M_W])', gensrc)
          and "tv.version_text" in gensrc,
          [ln.strip()[:70] for ln in gensrc.splitlines()
           if re.search(r'"(images6396289|0817899624|B2bCollectTool[M_W])', ln)][:3])
    check("生成完立刻用读侧口径回读自己（normalize_manifest）",
          "tu.normalize_manifest(" in gensrc)
    check("发布那头不拿自己机器的构建号跑 evaluate（那是「CI 因自身环境红」的假红）",
          "tu.evaluate(" not in gensrc and "_current_build" not in gensrc)

    gui = Path(__file__).resolve().parent / "b2bcollect_gui.py"
    gsrc = gui.read_text(encoding="utf-8") if gui.is_file() else ""
    check("界面用的是本模块的四个开关常量，不自己拼一份字符串",
          "_tool_update.APPLY_FLAG" in gsrc and "_tool_update.RELAUNCH_FLAG" in gsrc)
    check("界面不自己实现交接（不出现第二个 apply_update/run_handoff 的实现）",
          "def run_handoff" not in gsrc and "def apply_update" not in gsrc)
    check("界面问「能不能自动装」用的是 handoff_kind", "handoff_kind(" in gsrc)
    check("界面按 state 分派，不再自己判 has_update",
          "has_update" not in gsrc, [ln for ln in gsrc.splitlines() if "has_update" in ln][:3])
    check("免打扰键只有一份（界面与引擎读写同一个 key）",
          gsrc.count("_tool_update.SNOOZE_KEY") >= 1 and tu.SNOOZE_KEY == "update_snoozed_version")
    tusrc = Path(tu.__file__).read_text(encoding="utf-8")
    check("版本号算法只有一份：本模块不自己 parse/compare，一律转给 tool_version",
          not re.search(r"^def (parse_version|compare|is_newer)\b", tusrc, re.M))
    check("比较用的是整数 build（不拿 version 字符串比大小）",
          'data["build"] > cur_build' in tusrc and '"1." +' not in tusrc)


# ══════════════════════════════════════ ⑫b 兜底：屏幕上不能只剩「工具不见了」
def test_handoff_fallback():
    print("\n═══ ⑫b run_handoff：失败也要把旧版本开回来，且报告先落 ═══")
    with use_staging() as st:
        rep = st.dir / "r.json"
        junk = st.dir / "readme.txt"
        junk.write_text("不是包", encoding="utf-8")
        seen_at_launch = []

        def peek(cmd):
            # 拉起那一刻报告里已经写了什么：反序的话新实例会比报告早一步读到旧内容
            try:
                seen_at_launch.append(json.loads(rep.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                seen_at_launch.append(None)

        say = Recorder()
        got = tu.run_handoff(str(junk), relaunch_target=str(st.dir / "B2bCollectTool.app"),
                             log=say, spawn_fn=peek, report_dest=rep)
        check("形状认不出 → 不装也不兜底报错，结论如实是失败", got["ok"] is False, got)
        check("兜底把原来的版本重新打开了", got["fell_back"] is True, got)
        check("兜底那一刻报告已经在场（顺序不能反）", seen_at_launch and seen_at_launch[0]
              is not None, seen_at_launch)
        check("兜底之前那一份里 fell_back 还是 False（写的是升级本身的结果）",
              seen_at_launch and seen_at_launch[0]["fell_back"] is False, seen_at_launch)
        final = json.loads(rep.read_text(encoding="utf-8"))
        check("兜底完成后同一份报告被补全（不是另开一份）", final["fell_back"] is True, final)
        check("兜底成功这句话给人看", "先把原来的版本重新打开了" in say.text(), say.text())
        check("expect_build 也落进报告（下次开窗能说清要对哪一版）",
              final["expect_build"] == 0, final)

        seen_at_launch.clear()
        got = tu.run_handoff(str(junk), relaunch_target=str(st.dir / "B2bCollectTool.app"),
                             log=Recorder(), spawn_fn=lambda cmd: seen_at_launch.append(1),
                             report_dest=rep)
        check("兜底只拉一次（不因为重试把两个实例起来）", len(seen_at_launch) == 1)

        got = tu.run_handoff(str(junk), relaunch_target="",
                             log=Recorder(), spawn_fn=lambda cmd: None, report_dest=rep)
        check("没给 relaunch_target 时不猜一个东西去起", got.get("fell_back", False) is False)

        def boom(cmd):
            raise OSError("开不起来")

        say2 = Recorder()
        got = tu.run_handoff(str(junk), relaunch_target=str(st.dir / "X.app"),
                             log=say2, spawn_fn=boom, report_dest=rep)
        check("连原来的版本也没能自动打开时说清要手动双击", "手动双击工具图标" in say2.text(),
              say2.text())

        exe = st.dir / "B2bCollectToolSetup.exe"
        exe.write_bytes(b"MZ")
        launched = []
        with patch_attr(tu, "run_installer", lambda *a, **kw: {"ok": True, "code": 0, "why": ""}), \
                patch_attr(tu, "verify_installed_build",
                           lambda *a, **kw: {"ok": True, "build": 234, "certain": True,
                                             "path": "", "why": ""}):
            got = tu.run_handoff(str(exe), expect_build=234, relaunch_target=str(exe),
                                 log=Recorder(),
                                 spawn_fn=lambda cmd: launched.append(list(cmd)),
                                 report_dest=rep)
        check("装成功并拉起后不再兜底（不会开两个窗口）",
              len(launched) == 1 and got["relaunched"] is True and not got.get("fell_back"),
              (launched, got.get("fell_back")))
        final = json.loads(rep.read_text(encoding="utf-8"))
        check("成功这一次的报告里 verified 为真（与「兜底开了旧版」分开说）",
              final["verified"] is True and final["installed_build"] == 234, final)

        bad_dest = st.dir / "占位目录"      # 是个目录：写报告必然 OSError
        bad_dest.mkdir(exist_ok=True)
        say3 = Recorder()
        got = tu.run_handoff(str(junk), relaunch_target=str(st.dir / "X.app"), log=say3,
                             spawn_fn=lambda cmd: None, report_dest=bad_dest)
        check("报告写不下去不改升级结论（它是取证不是成功条件）",
              got["ok"] is False and got["report"]["ok"] is False, got)
        check("并且把「没能落到报告里」说出来", "没能落到报告" in say3.text(), say3.text())


# ══════════════════════════ ⑰ 发布那一头：清单生成的端到端判据
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / ".github" / "scripts"
_ARTIFACT_NAMES = set(tu.ARTIFACT_BY_KEY.values())


def load_gen():
    """按路径把发布那头那个脚本加载成模块，**真跑它的 `main()`**。

    判据不复制它的逻辑：CI 那一步最容易出的错是「判据里模拟的那份生成逻辑与 CI 真跑的
    并不是同一件东西」。它 import 的 tool_update / tool_version 与本判据是同一批模块对象，
    所以 `use_build`、`patch_attr` 对两头同时生效——端到端这才算真的端到端。
    """
    path = SCRIPTS_DIR / "make_release_manifest.py"
    spec = importlib.util.spec_from_file_location("mrm_under_test", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class gen_case:
    """一次发布现场：dist 目录 + 能算出真指纹的制品 + 人写的说明文件 + CI 计数器。"""

    def __init__(self, notes="修了三个 bug", run_number=None):
        self.dir = tmp_dir("upd_gen_")
        self.dist = self.dir / "dist"
        self.dist.mkdir()
        self.notes_file = self.dir / "release_notes.md"
        if notes is not None:
            self.notes_file.write_text(notes, encoding="utf-8")
        self.saved_rn = os.environ.get("GITHUB_RUN_NUMBER")
        if run_number is None:
            os.environ.pop("GITHUB_RUN_NUMBER", None)
        else:
            os.environ["GITHUB_RUN_NUMBER"] = str(run_number)

    def artifact(self, name, size=2048):
        """在 dist 里落一份**真制品**：档名与字节数都由调用方定，判据要能故意写坏。"""
        p = self.dist / name
        p.write_bytes(os.urandom(size))
        return p

    def emit(self, mod, argv, platform="win"):
        """跑一次发布。`@notes` 展开成 `--notes <说明文件>`；argv 里一个**裸档名**（在制品表里）
        展开成 `--artifact <同名真制品>`（制品就地生成）。其余原样传：要指向一份不在表里的
        路径（空文件、缺文件）就自己把 `--artifact` 写全。

        返回 (退出码, 控制台输出, 这个平台的清单是否落盘)。“落盘与否”必须由调用方一起断：
        只断退出码的话，「先把清单写出去再报错」这种形状在 CI 里同样是红——但那一刻
        已经发出去了，收不回来。
        """
        real = []
        for a in argv:
            if a == "@notes":
                real += ["--notes", str(self.notes_file)]
            elif a in _ARTIFACT_NAMES:
                real += ["--artifact", str(self.artifact(a))]
            else:
                real.append(a)
        real += ["--out", str(self.dist)]
        buf = io.StringIO()
        try:
            # argparse 拦下来的（未知平台、参数不对）也是一种拒：退出码从 SystemExit 里拿，
            # 而它的话是往 stderr 说的，两个流一并收集才能断「它把原因说对了」
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                rc = mod.main(real)
        except SystemExit as e:
            rc = int(e.code or 0)
        return rc, buf.getvalue(), self.written(platform)

    def written(self, platform="win"):
        return (self.dist / tu.MANIFEST_BY_KEY[platform]).is_file()

    def raw(self, platform="win"):
        """清单落盘后的原文；没落盘就返回空串（很多条判据要的正是「根本没写」）。"""
        try:
            return (self.dist / tu.MANIFEST_BY_KEY[platform]).read_text(encoding="utf-8")
        except OSError:
            return ""

    def draft(self, platform="win"):
        try:
            return (self.dist / f"release_notes_draft_{platform}.md").read_text(encoding="utf-8")
        except OSError:
            return ""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        os.environ.pop("GITHUB_RUN_NUMBER", None)
        if self.saved_rn is not None:
            os.environ["GITHUB_RUN_NUMBER"] = self.saved_rn
        shutil.rmtree(self.dir, ignore_errors=True)
        return False


def test_release_manifest():
    print("\n═══ ⑰ 发布那一头：清单生成的端到端判据 ═══")
    gen_path = SCRIPTS_DIR / "make_release_manifest.py"
    check("清单生成脚本在位且可加载（没有这一头，读侧的校验只是一段没人写的字段）",
          gen_path.is_file(), str(gen_path))
    if not gen_path.is_file():
        return
    mod = load_gen()
    up = (SCRIPTS_DIR / "upload_release_to_oss.py").read_text(encoding="utf-8")

    def pick(pattern):
        m = re.search(pattern, up)
        return m.group(1) if m else ""

    ub, ue, upx = (pick(r'BUCKET\s*=\s*"([^"]+)"'), pick(r'ENDPOINT\s*=\s*"([^"]+)"'),
                   pick(r'RELEASE_PREFIX\s*=\s*"([^"]+)"'))

    def rejected(label, g, argv, needle, platform="win", code=1):
        """一条「该拒的发布」：退出码就是那一个、话里点明原因、清单一个字都没落盘——三样一起断。

        `code` 缺省是 1（脚本自己拒的）；argparse 那一类是 2——两者都是「没发出去」，
        但不是同一种红：前者是发布口径不过，后者是命令根本没跑对。
        """
        rc, out, wrote = g.emit(mod, argv, platform=platform)
        check(label, rc == code and needle in out and not wrote,
              (rc, needle, out.strip()[:180], wrote))
        return out

    # ── 正常发版：生成的清单必须能被读侧收下，而且里面每一个地址都是真的 ──
    with gen_case() as g:
        setup = g.artifact(tu.ARTIFACT_WINDOWS_SETUP)
        rc, out, wrote = g.emit(mod, ["--platform", "win", "--build", "300", "@notes",
                                      tu.ARTIFACT_WINDOWS_SETUP,
                                      tu.ARTIFACT_WINDOWS_PORTABLE])
        check("win 通道正常发版：退出码 0 且清单落盘", rc == 0 and wrote, out.strip()[:200])
        check("清单文件名就是读侧期望的那一个", g.written("win"), tu.MANIFEST_BY_KEY["win"])
        check("win 通道不会顺手写出 Mac 那份清单（两个计数器不能互瞊）",
              not g.written("mac_arm64"))
        norm = tu.normalize_manifest(g.raw("win"))
        check("生成那头写下的清单，读这头收得下（同一次发布自洽）",
              norm.get("ok") is True, norm.get("why"))
        data = norm.get("data") or {}
        check("schema 就是读侧那一个常量", data.get("schema") == tu.SCHEMA, data.get("schema"))
        check("版本号由 tool_version 算，与构建号同源（不手拄一段字符串）",
              data.get("version") == tv.version_text(300) and data.get("build") == 300,
              (data.get("version"), data.get("build")))
        check("说明原样进清单（生成那头不改写、不代言）",
              data.get("notes") == "修了三个 bug", data.get("notes"))
        art = (data.get("artifacts") or {}).get("win") or {}
        check("指纹与字节数是从**真制品**算的（不是命令行列上来的那一句）",
              art.get("sha256") == tu.sha256_file(setup)
              and art.get("size") == setup.stat().st_size, art)
        check("清单里的下载地址逐字等于读侧拼出来的那条",
              art.get("url") == f"https://{tu.BUCKET_HOST}/{tu.RELEASE_PREFIX}/{tu.ARTIFACT_WINDOWS_SETUP}",
              art.get("url"))
        check("下载地址也逐字等于上传那头真会放的位置（两头各拼一半就遇不上）",
              art.get("url") == f"https://{ub}.{ue}/{upx}/{tu.ARTIFACT_WINDOWS_SETUP}",
              (art.get("url"), ub, ue, upx))
        check("win 自动安装拿的是安装包那一档，不是解压版",
              art.get("name") == tu.ARTIFACT_WINDOWS_SETUP
              and tu.handoff_kind(art) == "installer", art.get("name"))
        check("解压版那一档也在清单里（靠 U 盘干活的人也要拿到地址）",
              (data.get("artifacts") or {}).get("win_portable", {}).get("url") == tu.portable_url(),
              data.get("artifacts"))

        # 发布那头真正要保的性质：旧一版客户端在这份清单上能得到什么结论。
        # CI 脚本不拿发布机自己的环境跑 evaluate（那是假红），这条性质在这里钉。
        raw = g.raw("win")
        with use_build(299):
            got = tu.evaluate(raw, key="win")
        check("旧一版客户端在这份清单上得到 update-available（自动提示的源头就在这）",
              got["state"] == "update-available", (got["state"], got["why"][:120]))
        check("差距是真实差距（差 1 版就说差 1 版）", got["distance"] == 1, got["distance"])
        check("弹窗里那句「本次更新了什么」拿到的正是发布时写的那一句",
              got["notes"] == "修了三个 bug" and "修了三个 bug" in tu.notes_view(got),
              tu.notes_view(got)[:120])
        check("点「现在就装」要动的那个包就是 win 这一档",
              (got["artifact"] or {}).get("key") == "win", got["artifact"])
        with use_build(300):
            same = tu.evaluate(raw, key="win")
        check("本机已经是这一版：不重复弹，差距归零",
              same["state"] == "up-to-date" and same["distance"] == 0,
              (same["state"], same["distance"]))
        with use_build(305):
            ahead = tu.evaluate(raw, key="win")
        check("本机比清单还新：仍说已是最新，差距如实是负数",
              ahead["state"] == "up-to-date" and ahead["distance"] == -5,
              (ahead["state"], ahead["distance"]))
        with use_build(None):
            blind = tu.evaluate(raw, key="win")
        check("本机说不清自己是哪版时不给「有新版」这个结论（unknown，可重试）",
              blind["state"] == "unknown", blind["state"])

    # ── 拒收：每一条都同时证明「清单一个字都没落盘」 ──
    base = ["--platform", "win", "--build", "300", "@notes", tu.ARTIFACT_WINDOWS_SETUP]
    with gen_case() as g:
        rejected("构建号是 0（没注入）→ 不发：发出去就是一沓再也提不到更新的清单",
                 g, ["--platform", "win", "--build", "0", "@notes",
                     tu.ARTIFACT_WINDOWS_SETUP], "没能确认")
    with gen_case() as g:
        rejected("没给 --build、CI 计数器也是空的 → 不发（不带着一个假号继续）",
                 g, ["--platform", "win", "@notes", tu.ARTIFACT_WINDOWS_SETUP], "没能确认")
    with gen_case() as g:
        rejected("--build 不像一个版本号 → 不发（不静默折成 0）",
                 g, ["--platform", "win", "--build", "abc", "@notes",
                     tu.ARTIFACT_WINDOWS_SETUP], "没能确认")
    with gen_case(run_number="317") as g:
        rejected("--build 写错了但 CI 计数器在场 → 仍不发（错格式不退回另一个数："
                 "人点名要 300、静默发成 317 是最难发现的一形）",
                 g, ["--platform", "win", "--build", "v300", "@notes",
                     tu.ARTIFACT_WINDOWS_SETUP], "没能确认")
    with gen_case(run_number="317") as g:
        rc, out, wrote = g.emit(mod, ["--platform", "win", "@notes",
                                      tu.ARTIFACT_WINDOWS_SETUP])
        check("没给 --build 时取 CI 计数器（每条通道只用自己那个计数器）",
              rc == 0 and wrote
              and tu.normalize_manifest(g.raw("win"))["data"]["build"] == 317,
              (rc, out.strip()[:160]))
    with gen_case() as g:
        rejected("构建号超出四段可表达范围 → 不发（折段会得到一个对应另一版的假号）",
                 g, ["--platform", "win", "--build", str(tv.MAX_BUILD + 1), "@notes",
                     tu.ARTIFACT_WINDOWS_SETUP], "超出可表达范围")
    with gen_case(notes="") as g:
        rejected("说明是空的 → 默认不发：界面上那句「更新了什么」必须有人负责",
                 g, base, "--allow-empty-notes")
    with gen_case(notes="") as g:
        rc, out, wrote = g.emit(mod, base + ["--allow-empty-notes"])
        data = tu.normalize_manifest(g.raw("win")).get("data") or {}
        check("显式声明后发得出去，且清单里说明就是空的（不替谁编一句）",
              rc == 0 and wrote and data.get("notes") == "", (rc, data.get("notes")))
    with gen_case(notes="上一份没人看的说明") as g:
        rc, out, wrote = g.emit(mod, ["--platform", "win", "--build", "300",
                                      "--notes-text", "只改了导出表格这一件事",
                                      tu.ARTIFACT_WINDOWS_SETUP])
        data = tu.normalize_manifest(g.raw("win")).get("data") or {}
        check("说明也可以直接给一段话，且用的就是这一份（不是文件里那一份）",
              rc == 0 and data.get("notes") == "只改了导出表格这一件事", (rc, data.get("notes")))
    with gen_case() as g:
        rejected("--notes 与 --notes-text 都给 → 不发（两个都给就不知道哪份算数）",
                 g, ["--platform", "win", "--build", "300", "@notes",
                     "--notes-text", "另一段", tu.ARTIFACT_WINDOWS_SETUP], "只能给一个")
    with gen_case() as g:
        rejected("说明文件不在位 → 不发（不拿一份没人写过的空文件当发布说明）",
                 g, ["--platform", "win", "--build", "300",
                     "--notes", str(g.dir / "没这个文件.md"),
                     tu.ARTIFACT_WINDOWS_SETUP], "不在位")
    with gen_case() as g:
        rejected("一份制品都不给 → 不发，并把该给哪个档名说清",
                 g, ["--platform", "win", "--build", "300", "@notes"], "至少要给出")
    with gen_case() as g:
        rejected("拿 Mac 包发 win 通道 → 不发（win 这一档自己没制品，点「现在就装」拿不到包）",
                 g, ["--platform", "win", "--build", "300", "@notes",
                     tu.ARTIFACT_MAC_ARM], "只给了 mac_arm64")
    with gen_case() as g:
        rejected("只给解压版不给安装包 → 不发（解压版不参与自动安装）",
                 g, ["--platform", "win", "--build", "300", "@notes",
                     tu.ARTIFACT_WINDOWS_PORTABLE], "只给了 win_portable")
    with gen_case() as g:
        rejected("制品文件名不在发布表里 → 不发（多一档没登记过的包，读侧只会当不认识）",
                 g, ["--platform", "win", "--build", "300", "@notes",
                     "--artifact", str(g.dist / "顺手扔进来的.zip")], "不在发布制品表里")
    with gen_case() as g:
        empty = g.artifact(tu.ARTIFACT_WINDOWS_SETUP, size=0)
        rejected("制品是个空文件 → 不发（指纹算得出，但那不是包）",
                 g, ["--platform", "win", "--build", "300", "@notes",
                     "--artifact", str(empty)], "空文件")
    with gen_case() as g:
        rejected("制品路径合法但文件不存在 → 不发（不写一个下不到的地址）",
                 g, ["--platform", "win", "--build", "300", "@notes",
                     "--artifact", str(g.dist / tu.ARTIFACT_WINDOWS_SETUP)], "不在位")
    with gen_case() as g:
        rejected("平台键不在发布通道表里 → argparse 当场拦住（不发一条没人读的通道）",
                 g, ["--platform", "linux", "--build", "300", "@notes",
                     tu.ARTIFACT_WINDOWS_SETUP], "invalid choice", code=2)

    # ── 每条通道各写各的清单：两个计数器不能拄到一份文件里 ──
    with gen_case() as g:
        rc, out, wrote = g.emit(mod, ["--platform", "mac_arm64", "--build", "77", "@notes",
                                      tu.ARTIFACT_MAC_ARM], platform="mac_arm64")
        check("mac_arm64 通道正常发版（退出码 0，写的是自己那份清单）",
              rc == 0 and wrote, out.strip()[:180])
        check("mac 清单不会顺手写出 win 那份", not g.written("win"))
        norm = tu.normalize_manifest(g.raw("mac_arm64"))
        check("mac 那份清单也被读侧收下，且只有 mac_arm64 一档",
              norm.get("ok") is True
              and set((norm.get("data") or {}).get("artifacts") or {}) == {"mac_arm64"},
              norm.get("why") or norm.get("data"))
        with use_build(76):
            got = tu.evaluate(g.raw("mac_arm64"), key="mac_arm64")
        check("旧一版 Mac 客户端在这份清单上得到 update-available，装法是就地自替换",
              got["state"] == "update-available"
              and tu.handoff_kind(got["artifact"]) == "self_replace",
              (got["state"], tu.handoff_kind(got["artifact"])))
        check("published_at 缺省也不是空（界面要说清这是哪天发的）",
              bool((norm.get("data") or {}).get("published_at")), norm.get("data"))
    with gen_case() as g:
        rc, out, wrote = g.emit(mod, base + ["--published-at", "2026-11-19T09:00:00+08:00"])
        check("发布时间可以指定并原样进清单",
              rc == 0 and wrote
              and tu.normalize_manifest(g.raw("win"))["data"]["published_at"]
              == "2026-11-19T09:00:00+08:00", out.strip()[:160])

    # ── 起草只能起草：说明必须有人负责才能发布 ──
    with gen_case() as g:
        real_subjects = mod.git_subjects
        try:
            mod.git_subjects = lambda n: ["换绑后点重启现在真的会重开", "权限面板可以改备注"]
            rc, out, wrote = g.emit(mod, ["--platform", "win", "--build", "300",
                                          "--draft-from-git", "20"])
            draft = g.draft("win")
            check("起草退出码 0（它只是个草稿机，不管能不能发）", rc == 0, (rc, out[:160]))
            check("草稿写出来了，而且逐条列出提交标题（让人能删）",
                  "换绑后点重启现在真的会重开" in draft and "权限面板可以改备注" in draft,
                  draft[:200])
            check("草稿里点明了这不是发布说明、发布要走 --notes",
                  "草稿" in draft and "--notes" in draft, draft[:200])
            check("起草这一步**没有**产出清单（没人过目就发不出去）", not wrote)
            mod.git_subjects = lambda n: []
            rc2, _, _ = g.emit(mod, ["--platform", "win", "--build", "301",
                                     "--draft-from-git", "5"])
            check("一条提交都没读到也说清了（不空着一段让人以为这次没改动）",
                  rc2 == 0 and "手工写" in g.draft("win"), g.draft("win")[:200])
        finally:
            mod.git_subjects = real_subjects

    # ── 自校验不是装饰：它真的门在写盘之前 ──
    real_norm = tu.normalize_manifest
    with gen_case() as g:
        def deny(_raw):
            return {"ok": False, "data": {}, "why": "读侧就是说不合法"}
        with patch_attr(tu, "normalize_manifest", deny):
            rejected("读侧拒收自己生成的清单 → 当场红且不落盘（不等用户机器上来读）",
                     g, base, "被读侧拒收")
    with gen_case() as g:
        def lie_build(_raw):
            r = real_norm(_raw)
            r["data"]["build"] = r["data"]["build"] - 1
            return r
        with patch_attr(tu, "normalize_manifest", lie_build):
            rejected("读回来的构建号与要发的那个不是同一个数 → 不发", g, base, "不是同一个数")
    with gen_case() as g:
        def lie_art(_raw):
            r = real_norm(_raw)
            r["data"]["artifacts"] = {}
            return r
        with patch_attr(tu, "normalize_manifest", lie_art):
            rejected("清单里本档制品不齐 → 不发（地址或指纹缺一都算不齐）", g, base, "制品不齐")
    with gen_case() as g:
        rc, out, wrote = g.emit(mod, base)
        check("把那个假读侧还回去后同一命令仍能发出去（上面的红是门，不是默认状态）",
              rc == 0 and wrote and tu.normalize_manifest(g.raw("win")).get("ok") is True,
              (rc, out.strip()[:160]))


# ═════════════════⑱ CI 那一头：产物自报与本次注入值的对拍
def load_script(fname: str):
    """按文件名把 .github/scripts 下的脚本加载成模块（⑰ 的 load_gen 同一条路子）。"""
    spec = importlib.util.spec_from_file_location(f"ci_{fname[:-3]}", str(SCRIPTS_DIR / fname))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_ROOT = SCRIPTS_DIR.parent.parent
_COMMIT_OK = "0123456789abcdef0123456789abcdef01234567"


def mk_selfreport(build="234", commit=_COMMIT_OK, frozen=True, **patch):
    """做一份「产物自己写出来的」自报 JSON：字段与两格版本都由 tool_version 真算。

    这里刻意不手抄一份报告格式：report() 哪天多一个键、改名一个键，抄的那份还会绿，
    而被判的对象已经换了——那就是本轮在版本号上刚拆掉的「第二份实现」。
    frozen 由调用方拨：源码态跑 report() 恒为 False，而这一格记录的正是「写报告的那个
    进程是不是打包后的产物」，仿真必须在包外把它说成真值。
    """
    with use_build(None) as u:                      # 只借它换 bundle_dir，不预先写戳
        if build is not None:
            tv.write_stamp(u.dir / tv.BUILD_STAMP_NAME, build, commit)
        data = tv.report()
    data["frozen"] = frozen
    data.update(patch)
    return json.dumps(data, ensure_ascii=False)


def run_selfreport(report_text, expect_build="234", expect_commit=_COMMIT_OK,
                   label="B2bCollectTool.exe"):
    """真起一个子进程跑 CI 那一步调的脚本。

    不在本进程 import 它再调 main()：CI 消费的是**退出码与屏幕上那几句话**，而脚本
    第一件事就是把标准流设成 UTF-8——在本进程调会把判据自己的输出也一并拨走。
    """
    d = tmp_dir("upd_sr_ci_")
    try:
        p = d / "version_selfreport.json"
        if report_text is not None:
            p.write_text(report_text, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "check_artifact_selfreport.py"),
             "--report", str(p), "--expect-build", str(expect_build),
             "--expect-commit", str(expect_commit), "--label", label],
            capture_output=True, text=True, errors="replace", stdin=subprocess.DEVNULL)
        return proc.returncode, proc.stdout + proc.stderr
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_ci_selfreport_gate():
    print("\n═══ ⑱ CI 那一头：产物自报对拍与「戳只由 CI 注入」 ═══")
    # 先问「这份脚本到底有没有人跑」：脚本自己全绿而两条流水都没调它，就是一件死代码
    # 穿着判据的外衣。两个 --expect-* 也必须绑到 CI 计数器与 sha 本身，不能绑任何
    # 「重新算一遍」的东西——本轮拆第二份实现拆的就是这个动作。
    win = (_ROOT / ".github" / "workflows" / "build-b2bcollect.yml").read_text(encoding="utf-8")
    mac = (_ROOT / ".github" / "workflows" / "build-b2bcollect-mac.yml").read_text(encoding="utf-8")
    local = (_ROOT / "debug" / "build_b2bcollect_mac_local.sh").read_text(encoding="utf-8")

    def sr_step(text):
        """切出「调对拍脚本」那一个 step 的原文（接线判据只看这一步，不看整份文件）。

        这一步不能省：整份文件里 `github.run_number` 不止一处（写戳那一步也用），拿全文
        判会给出假绿——本步的 env 被改绑到别的变量时，别处那句还在，文本上照样「找得到」。
        切不到（没调这份脚本）就返回空串，下面那体检会把它报成「样样都缺」。
        """
        i = text.find("check_artifact_selfreport.py")
        if i < 0:
            return ""
        starts = [n for n in (text.rfind("\n    - name:", 0, i),
                              text.rfind("\n- name:", 0, i)) if n >= 0]
        ends = [n for n in (text.find("\n    - name:", i), text.find("\n- name:", i)) if n >= 0]
        return text[max(starts or [0]):min(ends or [len(text)])]

    def wiring(step_text, tail=""):
        """一个 step 的接线体检，返回缺的那几样（空 = 接上了）。

        判据按**递进来的文本**判，不自己读盘：这样下面的反例能拿现场弄坏的那份字节喂它
        （与 check_caller(label, text, …)、_nsi_var_problems(text) 同一形状），而不必为
        了变异台在交付链上开一条「读副本」的环境变量口子。
        """
        need = ["check_artifact_selfreport.py", "--expect-build", "--expect-commit",
                "github.run_number", "github.sha", "--print-version", "--out"]
        if tail:
            need.append(tail)
        return [n for n in need if n not in step_text]

    for tag, text, tail in (("Windows", win, "$LASTEXITCODE"), ("Mac", mac, "")):
        check(f"{tag} 流水真调了这一份对拍脚本，且两个期望值来自 run_number / sha"
              + ("（pwsh 还要把退出码传出去）" if tail else ""),
              not wiring(sr_step(text), tail), wiring(sr_step(text), tail))
    # 两条反例：接线判据不能只会对真文件说绿。第一例把脚本调用抽掉（死代码那一形），
    # 第二例只把**这一步**的期望值改绑到另一个看着同源的变量（两份实现那一形）——
    # 后者止于一个 step，才区分得开「这一步没接」与「整份文件里没提过」。
    _gone = wiring(sr_step(win.replace("check_artifact_selfreport.py", "忘了调这一步")),
                   "$LASTEXITCODE")
    check("反向对照·把对拍脚本那一句从流水里抽掉：接线体检必须报名字（而不是报「切不到步」以外的健）",
          "check_artifact_selfreport.py" in _gone, _gone)
    _step_win = sr_step(win)
    _drift = wiring(_step_win.replace("github.run_number", "github.run_id"), "$LASTEXITCODE")
    check("反向对照·期望值改绑到不是计数器的变量：必须只报「拿不到 run_number」这一样",
          _drift == ["github.run_number"], _drift)

    good = mk_selfreport()
    rc, out = run_selfreport(good)
    check("正例：自报与本次注入逐条对上就放行", rc == 0 and "✅" in out, (rc, out.strip()[-160:]))
    check("放行前先把自报的原文念在屏幕上（红了不必重新下一次制品）",
          "自报：1.234" in out and "它读的戳" in out, out[-200:])
    check("念出来的四段号就是 tool_version 算的那一格（不在脚本里重拼一遍）",
          f"四段={tv.product_version(234)}" in out, out[-200:])

    # 三条红线的第一族：取证没拿到。拿不到证据时不许开口谈版本，否则同一次红会被
    # 读成两种不同的毛病。
    for tag, text, needle in (("报告不在位", None, "自报报告不在位"),
                              ("报告是空的", "", "自报报告是空的"),
                              ("报告不是合法 JSON", "{这不是JSON", "不是合法 JSON"),
                              ("顶层不是对象", "[1,2]", "顶层不是对象")):
        rc, out = run_selfreport(text)
        check(f"反例·{tag}：非零退出、说清是取证没拿到、不印成功话术",
              rc == 1 and needle in out and "✅" not in out, (rc, out[-160:]))
        check(f"反例·{tag}：拿不到证据就不许说「产物对不上」也不许说「说不出自己是哪一版」",
              "本次注入的是" not in out and "产物说不出自己是哪一版" not in out
              and "自报：" not in out, out[-160:])

    # 三条红线的第二族：产物自己有证据，但证据说「我没被注入戳」。
    rc, out = run_selfreport(mk_selfreport(build=None))
    check("反例·随包戳缺失（certain=false）：要说成「说不出自己是哪一版」并点名进包那一格",
          rc == 1 and "产物说不出自己是哪一版" in out and "version.json" in out
          and "✅" not in out, (rc, out[-170:]))
    rc, out = run_selfreport(mk_selfreport(frozen=False))
    check("反例·frozen=false：跑起来的不是打包那一份，这份取证不算数",
          rc == 1 and "跑起来的不是打包后的那一份" in out, (rc, out[-170:]))

    # 三条红线的第三族：产物说的与 CI 记的不是同一件事。
    rc, out = run_selfreport(good, expect_build="235")
    check("反例·上一轮的戳被带过来：两个号不相等要说清谁对不上谁",
          rc == 1 and "产物自报的构建号是 234，本次注入的是 235" in out, (rc, out[-170:]))
    for tag, eb in (("空串", ""), ("被引号包住", '"234"'), ("还是个未展开的表达式", "${{ github.run_number }}")):
        rc, out = run_selfreport(good, expect_build=eb)
        check(f"反例·注入的构建号{tag}：先修注入，不许把包红成「产物对不上」",
              rc == 1 and "本次注入的构建号不像一个数" in out
              and "产物自报的构建号是" not in out, (rc, out[-170:]))
    rc, out = run_selfreport(mk_selfreport(commit=""))
    check("反例·戳里没有 commit：宁可不发一个说不清出自哪一次提交的包",
          rc == 1 and "戳里没有 commit" in out, (rc, out[-170:]))
    rc, out = run_selfreport(good, expect_commit="")
    check("反例·本次注入的 commit 是空的：红要指向 workflow 里那个表达式而不是产物",
          rc == 1 and "本次注入的 commit 是空的" in out, (rc, out[-170:]))
    rc, out = run_selfreport(good, expect_commit="f" * 40)
    check("反例·戳里的 commit 与本次构建不是同一件事",
          rc == 1 and "戳里的 commit 是" in out and "不是同一件事" in out, (rc, out[-170:]))
    rc, out = run_selfreport(mk_selfreport(commit=_COMMIT_OK.upper()))
    check("commit 比的是十六进制本身：大小写不同不算不同（git 两种写法都给）",
          rc == 0 and "✅" in out, (rc, out[-160:]))

    # “戳只由 CI 注入”那一半：豁免不能只活在注释里。拿真文本喂给真门，两边都要拿到
    # 自己的结论——只验「本机不红」的话，把整张 REQUIRED_DATA 停用也能绿。
    cpm = load_script("check_packaging_manifest.py")

    def vproblems(label, text):
        return [p for p in cpm.check_caller(label, text, (), []) if "version.json" in p]

    stripped = mac.replace('--add-data "version.json:."', "")
    check("反向对照用的文本真把那一格删掉了（没删成功=拿同一份字节自己跟自己比）",
          stripped != mac and "version.json:." not in stripped, "")
    _vp = vproblems("Mac workflow", stripped)
    check("CI 入口少注 version.json 必须红，并把理由念出来（缺它 = 版本未能确认…）",
          bool(_vp) and "版本未能确认" in " ".join(_vp), _vp)
    check("本机的真文本不许因为没注这一格而红（CI_ONLY_DATA 的豁免正在生效）",
          vproblems("本机 Mac 打包脚本", local) == [], vproblems("本机 Mac 打包脚本", local))
    check("豁免按登记表里的文件类型派生：同一份缺格文本喂给不在 CALLERS 里的合成标，按「不是 CI」算",
          vproblems("假装的第三个入口", stripped) == [], "")
    _cf = [p for p in cpm.check_caller("本机 Mac 打包脚本",
                         local.replace('--add-data "config.json:."', ""), (), [])
           if "config.json" in p]
    check("豁免只覆盖登记了理由的那一格：把 config.json 删掉，本机入口也照样红",
          bool(_cf), "把 REQUIRED_DATA 整表都豁免了，那种绿等于这道门没有")

    # 上面三条豁免判据自己的证人。变异打在**刚加载的那份门**上（不是磁盘文件），
    # 因为 cpm 是按路径现加载的，变异台在跑套件之前拿不到这个对象。
    # 为什么不能只跑「真文本不红」那一面：门被整表关掉时它同样不红——那三条绿
    # 得没有任何含义。三种放宽写法（清空、归类改成一律算 CI、豁免扩到别的文件）
    # 必须各被抓住一次。
    with patch_attr(cpm, "CI_ONLY_DATA", {}):
        _v1 = vproblems("本机 Mac 打包脚本", local)
        check("反向对照·把 CI_ONLY_DATA 清空：本机那份立刻红成「漏注 version.json」",
              bool(_v1), _v1)
    with patch_attr(cpm, "_is_ci_caller", lambda _label: True):
        _v2 = vproblems("本机 Mac 打包脚本", local)
        check("反向对照·把归类改成「一律按 CI」：本机也照样红（归类不能是摆设）",
              bool(_v2), _v2)
    with patch_attr(cpm, "CI_ONLY_DATA", dict(cpm.CI_ONLY_DATA, **{"config.json": "测试用"})):
        _v3 = [p for p in cpm.check_caller("本机 Mac 打包脚本",
                             local.replace('--add-data "config.json:."', ""), (), [])
               if "config.json" in p]
        check("反向对照·把豁免扩到 config.json：上一条「本机也照样红」立刻变得不红",
              not _v3, "豁免只能逐格点名；能随手扩表的那张表就是把门改成沉默")


def main():
    print("═══ tool_update 引擎层离线判据 ═══")
    test_platform()
    test_manifest()
    test_states()
    test_check_dispatch()
    test_steps_and_download()
    test_installer_and_verify()
    test_apply_update()
    test_self_replace()
    test_installer_handoff_chain()
    test_report()
    test_handoff_fallback()
    test_snooze_and_cli()
    test_single_source()
    test_release_manifest()
    test_ci_selfreport_gate()
    print(f"\n═══ 结果：{_PASS} 通过 / {_FAIL} 失败 ═══")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
