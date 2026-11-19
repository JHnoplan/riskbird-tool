# -*- coding: utf-8 -*-
"""test_chrome_doctor_offline.py —— Chrome 体检与安装通路（2026-11-19 需求 3）。

用户原话：「今天给新用户安装工具的时候，发现对方没有 Chrome……能否做到检测电脑没有安装
Chrome，自动提示安装（能直接安装最好，不能的话跳到官网手动下载）」。

这一路最怕的不是装不上，而是**说不清**：说「没装」其实装了（于是让人重装一次）、说「装好了」
其实没找到（于是下一次启动还是失败）、说「安装失败」其实是代理要认证（于是人去查网络）。
所以判据全部围绕这三句话展开，并且一律离线顶掉接缝：真装一个浏览器到测试机上不可接受，
真去 dl.google.com 下载也不能进批跑（CI 机器上网络口径不定，红了没人知道是代码还是网络）。

顶掉的是 chrome_doctor **模块命名空间里**的那几个名字（``cd.os`` / ``cd.sys`` / ``cd._spawn``
……），不去动全局 ``os.name``：改全局会把同一进程里其他套件的判断一起带偏，那种红查不清。

十节各自盯一个具体事故
--------------------
① 「有没有装」只认启动器那一份真源（注册表/候选路径在也不算已装，否则体检与启动各说一套）；
② 「读不到」≠「没装」：探测本身失败时 certain=False，既不给安装通路、也不下那句结论；
③ 通路清单按这台机器**真的有的能力**给：winget 不在就不承诺 winget，page 永远在最后；
④ 成败以重新体检为准，不以退出码为准（winget「已经装好了」返回非零）；
⑤ 下载没成功就绝不运行安装包（拿一个坏包/半包去跑，比不装严重得多）；
⑥ dmg 安装：不可写退个人目录、镜像不对就认出来、可能在跑的 bundle 不许 rmtree、
   无论成败必 detach（留下挂载的盘下次开机还在那儿）；
⑦ 版本读取跨平台（mac 的 plist 分层上一版算错了一层，本机装了 Chrome 却报读不到）；
⑧ winget 那种「逻辑上在 PATH、实际取不到」的命令要按已知位置再找一次；
⑨ 官网兜底两个地址依次试，都打不开时给一句仍然能行动的话；
⑩ 下载原语：https-only、原子改名、校验不符即删、失败不留残件、六种成因给六句话。

运行：.venv/bin/python b2bcollect_build/test_chrome_doctor_offline.py
"""
import hashlib
import os
import plistlib
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

import chrome_doctor as cd                                     # noqa: E402
import download_verify as dv                                   # noqa: E402

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


# ── 顶装工具：把 chrome_doctor 看到的「这台机器」换成用例想要的样子 ─────────
class Rig:
    """一次用例的现场：启动器说什么、有哪些命令、跑命令的结果、下载的结果。"""

    def __init__(self, launch=("", ""), nt=False, mac=False, which=None,
                 spawns=None, exists=None, fetched=None):
        self.launch = list(launch)      # _launch_path() 的返回：(路径, 探测失败原因)
        self.launch_calls = 0
        self.spawns = []                # 真实跑过的命令台账
        self.spawn_results = spawns or {}
        self.exists = set(exists or ())
        self.fetch_calls = []
        self.fetched = fetched
        self._saved = {}
        self._enter(nt, "win32" if nt else ("darwin" if mac else "linux"), which)
        if fetched is not None:
            self._save("download_verify")
            cd.download_verify = types.SimpleNamespace(fetch=self._fetch)

    def _save(self, attr):
        self._saved[attr] = getattr(cd, attr)

    def _enter(self, nt, plat, which):
        self._save("_launch_path")
        cd._launch_path = self._launch
        self._save("_exists")
        cd._exists = lambda p: bool(p) and str(p) in self.exists
        self._save("_spawn")
        cd._spawn = self._spawn
        if which is not None:
            self._save("_which")
            cd._which = lambda c: which.get(c, "")
        self._save("os")
        self._save("sys")
        # 顶掉的是 chrome_doctor 自己看到的那一份 os/sys：真全局改 os.name 会把同一进程里
        # 其他套件的判断一起带偏，那种红查不清是谁干的
        cd.os = types.SimpleNamespace(name="nt" if nt else "posix", path=os.path,
                                      access=lambda p, m: True, W_OK=os.W_OK)
        cd.sys = types.SimpleNamespace(platform=plat)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def close(self):
        for attr, value in self._saved.items():
            setattr(cd, attr, value)
        self._saved.clear()

    # ── 四个接缝的替身 ──
    def _launch(self):
        self.launch_calls += 1
        return tuple(self.launch)

    def _spawn(self, argv, timeout=None, cwd=None):
        argv = [str(a) for a in argv]
        self.spawns.append(argv)
        for key, res in self.spawn_results.items():
            if key in " ".join(argv):
                return res if isinstance(res, tuple) else (res, "")
        return 0, ""

    def _fetch(self, url, dest, log=None, expected_sha256="", max_bytes=None,
               timeout=None, cancel=None, session=None):
        self.fetch_calls.append((url, str(dest)))
        return dict(self.fetched)


# ── 下载原语的替身：只喂字节，不碰网络 ──────────────────────────────────
class FakeResp:
    def __init__(self, chunks=(), status=200, length=None):
        self.chunks = [c if isinstance(c, bytes) else str(c).encode() for c in chunks]
        self.status_code = status
        self.closed = 0
        head = {}
        if length is not None:
            head["Content-Length"] = str(length)
        elif self.chunks:
            head["Content-Length"] = str(sum(len(c) for c in self.chunks))
        self.headers = head

    def iter_content(self, chunk_size=1):
        for c in self.chunks:
            yield c

    def close(self):
        self.closed += 1


class FakeSession:
    def __init__(self, resp=None, exc=None):
        self.resp, self.exc = resp, exc
        self.urls = []
        self.closed = 0

    def get(self, url, stream=True, timeout=None, allow_redirects=True):
        self.urls.append(url)
        if self.exc:
            raise self.exc
        return self.resp

    def close(self):
        self.closed += 1


def main():
    print("═══ Chrome 体检/安装 + 下载校验 离线测试 ═══")
    print(f"   （解释器 {sys.version.split()[0]}，本套件只跑本地判定，不碰网也不装任何软件）")
    _test_inspect()
    _test_probe_uncertain()
    _test_plan()
    _test_run_step()
    _test_install()
    _test_dmg()
    _test_version()
    _test_which_winget()
    _test_page()
    _test_cli()
    _test_download_verify()
    print(f"\n═══ 结果：{_PASS} 通过 / {_FAIL} 失败 ═══")
    return 0 if _FAIL == 0 else 1


# ── ① 体检结论只认启动器 ────────────────────────────────────────────────
def _test_inspect():
    print("\n── ① 「有没有装」只认启动器那一份真源 ──")
    with Rig(launch=("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", ""),
             mac=True, exists={"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"},
             which={}) as rig:
        info = cd.inspect()
        check("启动器找到了就是已装，结论位与路径同源",
              info["installed"] and info["certain"] and info["launch_path"].endswith("Google Chrome"),
              info)
        check("体检真的去问过启动器（只查候选路径的体检会与启动器各说一套）",
              rig.launch_calls == 1, rig.launch_calls)

    # 候选路径在、注册表在，但启动器没找到：仍然不许宣布已装
    with Rig(launch=("", ""), nt=True, which={"winget": "C:\\x\\winget.exe"},
             exists={"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"}) as rig:
        rig_reg = cd._registry_chrome
        cd._registry_chrome = lambda: r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        try:
            info = cd.inspect()
            check("注册表和候选路径都「在」但启动器没找到时，结论仍是没装——否则体检说装了、"
                  "启动说没装，两个人互相说不通",
                  not info["installed"] and info["certain"], info)
            check("证据行仍然留下注册表那条（不参与结论，但要能排障）",
                  any("注册表" in e for e in info["evidence"]), info["evidence"])
        finally:
            cd._registry_chrome = rig_reg


def _test_probe_uncertain():
    print("\n── ② 「读不到」不是「没装」 ──")
    with Rig(launch=("", "ImportError: no module named selenium"), mac=True, which={}) as rig:
        info = cd.inspect()
        check("探测本身失败时 certain=False，installed 仍为 False（不猜结论）",
              (not info["installed"]) and info["certain"] is False and
              "ImportError" in info["probe_error"], info)
        check("证据行把这句话说明白（不许留成一句「没找到」）",
              any("读不到" in e and "没装" in e for e in info["evidence"]), info["evidence"])
        plan = cd.install_plan(info)
        kinds = [s["kind"] for s in plan]
        check("读不到时不给任何「装一个浏览器」的通路：只给重新确认 + 官网",
              kinds == ["recheck", "page"], kinds)
        # needle 必须跟 summary_text 真文案里的引号形状一致（那句写的是「没装」带书名引号，
        # 因为它是被引用的说法、不是本工具自己的结论）。引号掉了这句仍然是在说「不能断定」，
        # 语义没变，但 needle 按不带引号的字面串去找就会恒红——那是判据自己漂移，不是实现坏了。
        check("结论话术不许把「读不到」说成「没装」（那会让装了 Chrome 的人白装一次）",
              "不能断定「没装」" in cd.summary_text(info), cd.summary_text(info))
        res = cd.install(log=lambda _m: None)
        check("这种情况下去 install 也不会下载/运行任何东西",
              not rig.fetch_calls and not rig.spawns, (rig.fetch_calls, rig.spawns))


def _test_plan():
    print("\n── ③ 通路清单按这台机器真的有的能力给 ──")
    with Rig(launch=("", ""), nt=True, which={"winget": "C:\\w\\winget.exe"}) as rig:
        kinds = [s["how"] for s in cd.install_plan()]
        check("Windows + 有 winget：winget → 官方安装包 → 官网",
              kinds == ["winget", "google_installer", "page"], kinds)
    with Rig(launch=("", ""), nt=True, which={}) as rig:
        kinds = [s["how"] for s in cd.install_plan()]
        check("Windows 但没有 winget 时**不承诺**这条能力（说了就会在下一步报一个谁也"
              "看不懂的「不是内部或外部命令」）",
              kinds == ["google_installer", "page"], kinds)
    with Rig(launch=("", ""), mac=True, which={"brew": "/usr/local/bin/brew"}) as rig:
        kinds = [s["how"] for s in cd.install_plan()]
        check("macOS + 有 brew：brew → 官方 dmg → 官网",
              kinds == ["brew", "google_dmg", "page"], kinds)
    with Rig(launch=("", ""), mac=True, which={}) as rig:
        kinds = [s["how"] for s in cd.install_plan()]
        check("macOS 没有 brew 也仍有两条出口", kinds == ["google_dmg", "page"], kinds)
    with Rig(launch=("", ""), mac=True, which={}) as rig:
        plan = cd.install_plan()
        check("page 永远在最后且永远存在：自动通路可能因网络/权限全走不通，"
              "那时必须有一个「你自己下」的出口",
              plan[-1]["how"] == "page" and len(plan) >= 2)
        check("官网那一条把国内可达地址也给了（dl.google.com 常不通是事实）",
              cd.PAGE_OFFICIAL in plan[-1]["detail"] and cd.PAGE_CN in plan[-1]["detail"],
              plan[-1]["detail"])
        check("每条都带「点了会发生什么」（会不会弹授权、要等多久）",
              all(s.get("label") and s.get("detail") for s in plan), plan)
    with Rig(launch=("/c/chrome.exe", ""), nt=True, which={}) as rig:
        check("已经装了就不给安装通路（只留官网那一条兜底）",
              [s["kind"] for s in cd.install_plan()] == ["page"], cd.install_plan())


def _test_run_step():
    print("\n── ④ 成败以重新体检为准，不以退出码为准 ──")
    with Rig(launch=("", ""), nt=True, which={"winget": "C:\\w\\winget.exe"},
             spawns={"winget": (0, "已安装。")}) as rig:
        res = cd.run_step({"how": "winget"})
        check("命令跑了", any("winget" in " ".join(a) for a in rig.spawns), rig.spawns)
        check("退出码 0 但重新体检仍没找到 Chrome ⇒ 判失败（不能把「命令成功」当「装好了」）",
              not res["ok"], res)
        check("失败理由带上退出码与命令原话（远程排障只有这两件能对齐）",
              "退出码" in res["why"] and "已安装" in res["why"], res["why"])

    with Rig(launch=("", ""), nt=True, which={"winget": "C:\\w\\winget.exe"},
             spawns={"winget": (1, "No available package.")}) as rig:
        cd._launch_path = lambda: (r"C:\Program Files\Google\Chrome\Application\chrome.exe", "")
        res = cd.run_step({"how": "winget"})
        check("winget 返回非零但机器上确实找到了 Chrome ⇒ 判成功（"
              "「已经装好了」在 winget 这里就是非零码）", res["ok"], res)

    with Rig(launch=("", ""), nt=True, which={"winget": "C:\\w\\winget.exe"},
             spawns={"winget": (None, "这台机器上没有这个命令：winget")}) as rig:
        res = cd.run_step({"how": "winget"})
        check("命令压根不在 ⇒ 失败且理由是那句能行动的话，不是 traceback",
              (not res["ok"]) and "这台机器上没有这个命令" in res["why"], res)

    print("\n── ⑤ 下载没成功就绝不运行安装包 ──")
    with Rig(launch=("", ""), nt=True, which={},
             fetched={"ok": False, "path": "", "bytes": 0, "sha256": "",
                      "why": "连不上对方：没有网络"}) as rig:
        res = cd.run_step({"how": "google_installer"})
        check("安装包没取回来时报失败并把下载理由原样递上来",
              not res["ok"] and "连不上对方" in res["why"], res)
        check("一条安装命令都没跑（拿半包/坏包去 run 比不装严重得多）",
              not rig.spawns, rig.spawns)
    with Rig(launch=("", ""), nt=True, which={},
             fetched={"ok": True, "path": "/tmp/chrome_installer.exe", "bytes": 10,
                      "sha256": "ab", "why": ""}, spawns={"chrome_installer": (0, "")}) as rig:
        res = cd.run_step({"how": "google_installer"})
        check("取回来了才运行安装包，且带静默参数",
              any("chrome_installer.exe" in " ".join(a) and "/silent" in a
                  for a in rig.spawns), rig.spawns)
        check("跑完仍然要重新体检", not res["ok"], res)
    with Rig(launch=("", ""), mac=True, which={},
             fetched={"ok": False, "path": "", "bytes": 0, "sha256": "",
                      "why": "对方拒绝提供这个文件（HTTP 403）"}) as rig:
        res = cd.run_step({"how": "google_dmg"})
        check("dmg 那一路同样：没取回来就不挂载", (not res["ok"]) and not rig.spawns, res)

    print("\n── ⑥ recheck 那一步的三种结论 ──")
    with Rig(launch=("", "ImportError: x"), mac=True, which={}) as rig:
        res = cd.run_step({"how": "recheck"})
        check("重新确认还是读不到 ⇒ ok=False 并如实说「还是没读成功」",
              not res["ok"] and "还是没读成功" in res["why"], res)
        rig.launch = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "")
        res = cd.run_step({"how": "recheck"})
        check("重新确认读到了 ⇒ ok=True 并把新体检结果带回去", res["ok"] and
              res["info"]["installed"], res)
        rig.launch = ("", "")
        res = cd.run_step({"how": "recheck"})
        check("确认过了确实没有 ⇒ 明说可以走下载安装那一步",
              not res["ok"] and "确实没有" in res["why"], res)


def _test_install():
    print("\n── ⑦ install：逐条试、留台账、永远带出口 ──")
    with Rig(launch=("/c/chrome.exe", ""), nt=True, which={"winget": "C:\\w\\winget.exe"}) as rig:
        res = cd.install()
        check("本机已装就直接返回，一条命令都不跑", res["ok"] and not rig.spawns, res)
        check("已装那一条要说「不需要安装」（不能让人以为刚才装了东西）",
              "不需要安装" in res.get("note", ""), res)

    with Rig(launch=("", ""), nt=True, which={"winget": "C:\\w\\winget.exe"},
             spawns={"winget": (0, "ok")},
             fetched={"ok": False, "path": "", "bytes": 0, "sha256": "", "why": "网络不通"}) as rig:
        res = cd.install()
        kinds = [t["how"] for t in res["tried"]]
        check("第一条不通就接着试第二条，并把每条的结果记进台账",
              kinds == ["winget", "google_installer"], res["tried"])
        check("全失败时 ok=False，但 page 那条出口一定在返回值里",
              (not res["ok"]) and res["page"] == cd.PAGE_OFFICIAL, res)

    with Rig(launch=("", ""), nt=True, which={"winget": "C:\\w\\winget.exe"}) as rig:
        # 装之前确实没装，装完就有：这才叫「因为装成了才停」，而不是
        # 「一开头就以为已装」被当成成功（那种绿只证明代码跑得下去，不证明它会装）
        def spawn_then_found(argv, timeout=None, cwd=None):
            argv = [str(a) for a in argv]
            rig.spawns.append(argv)
            if "winget" in " ".join(argv):
                rig.launch = (r"C:\Program Files\Google\Chrome\Application\chrome.exe", "")
                return 0, "Successfully installed"
            return 0, ""
        cd._spawn = spawn_then_found
        res = cd.install()
        check("第一条就装成了就不再往下走（不留两条重复安装）",
              res["ok"] and [t["how"] for t in res["tried"]] == ["winget"], res["tried"])
        check("只有 winget 那一条真跑过（装成功之后不应再去下载一遍官方包）",
              len(rig.spawns) == 1 and "winget" in " ".join(rig.spawns[0]), rig.spawns)

    with Rig(launch=("", ""), nt=True, which={"winget": "C:\\w\\winget.exe"},
             spawns={"winget": (0, "x")},
             fetched={"ok": True, "path": "/tmp/i.exe", "bytes": 1, "sha256": "y", "why": ""}) as rig:
        res = cd.install(only=["google_installer"])
        check("only 过滤是真的过滤：winget 那一条压根没跑",
              all("winget" not in " ".join(a) for a in rig.spawns), rig.spawns)
        check("only 也没成功时仍是 ok=False（不许因为「试过了」就报成功）",
              not res["ok"], res)


def _test_dmg():
    print("\n── ⑧ dmg 安装：退个人目录、坏包认出、绝不留挂载、不删在跑的浏览器 ──")
    tmp = Path(tempfile.mkdtemp(prefix="cd_dmg_"))
    apps = tmp / "Applications"
    personal = tmp / "personal" / "Applications"

    def mount_ok(mountpoint):
        """伪造一次成功的 hdiutil attach：在挂载点里放一个 app。"""
        app = Path(mountpoint) / "Google Chrome.app"
        (app / "Contents" / "MacOS").mkdir(parents=True, exist_ok=True)
        (app / "Contents" / "MacOS" / "Google Chrome").write_text("x")
        return 0, ""

    def attach_fails(argv, timeout=None, cwd=None):
        rig.spawns.append([str(a) for a in argv])
        if "attach" in argv:
            return 1, "hdiutil: attach failed - 无此类挂载"
        return 0, ""

    with Rig(launch=("", ""), mac=True, which={}) as rig:
        rig.spawn_results = {}
        calls = []

        def spawn(argv, timeout=None, cwd=None):
            argv = [str(a) for a in argv]
            calls.append(argv)
            if "attach" in argv:
                return mount_ok(argv[argv.index("-mountpoint") + 1])
            if "ditto" in argv:
                dest = Path(argv[-1])
                dest.mkdir(parents=True, exist_ok=True)
                (dest / "Contents" / "MacOS").mkdir(parents=True, exist_ok=True)
                (dest / "Contents" / "MacOS" / "Google Chrome").write_text("x")
                return 0, ""
            return 0, ""
        cd._spawn = spawn
        rig.spawns = calls
        code, text, ok, why = cd._install_from_dmg("/tmp/x.dmg", lambda _m: None,
                                                   targets=[apps, personal])
        check("「应用程序」可写就装进应用程序", ok and (apps / "Google Chrome.app").exists(), why)
        check("不管成败都要 detach（留下挂载的盘下次开机还在那儿）",
              any("detach" in " ".join(c) for c in calls), calls)

        # 系统目录不可写：退到个人 Applications
        rig2_calls = []
        saved_access = cd.os.access

        def spawn2(argv, timeout=None, cwd=None):
            argv = [str(a) for a in argv]
            rig2_calls.append(argv)
            if "attach" in argv:
                return mount_ok(argv[argv.index("-mountpoint") + 1])
            if "ditto" in argv:
                dest = Path(argv[-1])
                dest.mkdir(parents=True, exist_ok=True)
                (dest / "Contents" / "MacOS").mkdir(parents=True, exist_ok=True)
                (dest / "Contents" / "MacOS" / "Google Chrome").write_text("x")
                return 0, ""
            return 0, ""
        cd._spawn = spawn2
        cd.os = types.SimpleNamespace(**{**cd.os.__dict__,
                                         "access": lambda p, m: str(p) != str(apps)})
        code, text, ok, why = cd._install_from_dmg("/tmp/x.dmg", lambda _m: None,
                                                   targets=[apps, personal])
        check("系统「应用程序」不可写时自动退到个人 Applications（这一台照样装得上）",
              ok and (personal / "Google Chrome.app").exists(), why)
        cd.os = types.SimpleNamespace(**{**cd.os.__dict__, "access": lambda p, m: False})
        code, text, ok, why = cd._install_from_dmg("/tmp/x.dmg", lambda _m: None,
                                                   targets=[apps, personal])
        check("两级都不可写时给的是「请手动装」，不是「安装失败」",
              (not ok) and "不可写" in why, why)
        check("失败那一路也 detach 了", any("detach" in " ".join(c) for c in rig2_calls), rig2_calls)
        cd.os = types.SimpleNamespace(**{**cd.os.__dict__, "access": saved_access})

        # 镜像里没有 app：认出来，不说「装好了」
        def spawn3(argv, timeout=None, cwd=None):
            argv = [str(a) for a in argv]
            if "attach" in argv:
                return 0, ""
            return 0, ""
        cd._spawn = spawn3
        code, text, ok, why = cd._install_from_dmg("/tmp/x.dmg", lambda _m: None,
                                                   targets=[apps, personal])
        check("镜像里没有 Google Chrome.app 时明说「不是预期那份官方镜像」",
              (not ok) and "不是预期" in why, why)

        # 已存在且可执行：不许 rmtree 一个可能在跑的浏览器
        def spawn4(argv, timeout=None, cwd=None):
            argv = [str(a) for a in argv]
            calls.append(argv)
            if "attach" in argv:
                return mount_ok(argv[argv.index("-mountpoint") + 1])
            return 0, ""
        cd._spawn = spawn4
        live = personal / "Google Chrome.app"
        check("前面那步已经把 app 放进个人目录（这步拿它当「已存在的一份」）", live.exists())
        code, text, ok, why = cd._install_from_dmg("/tmp/x.dmg", lambda _m: None,
                                                   targets=[apps, personal])
        check("目标位置已有一份**能启动**的 Chrome 时不 rmtree 它，而是叫用户走覆盖安装"
              "（删掉一个正在跑的浏览器会把开着的标签页一起带走）",
              (not ok) and "不替你删" in why and live.exists(), why)


def _test_version():
    print("\n── ⑨ 版本读取：三种平台都跑一遍（本机不是那个平台也要验） ──")
    tmp = Path(tempfile.mkdtemp(prefix="cd_ver_"))
    bundle = tmp / "Google Chrome.app"
    exe = bundle / "Contents" / "MacOS" / "Google Chrome"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("x")
    with (bundle / "Contents" / "Info.plist").open("wb") as fh:
        plistlib.dump({"CFBundleShortVersionString": "131.0.6778.85"}, fh)
    with Rig(launch=("", ""), mac=True, which={}) as rig:
        check("mac：从 bundle 的 Info.plist 读出版本（上一版把路径算成 Contents/Contents，"
              "本机装了 Chrome 却报读不到）",
              cd._version_of(str(exe)) == "131.0.6778.85", cd._version_of(str(exe)))
        check("mac：plist 不在时留空串，绝不猜一个",
              cd._version_of(str(tmp / "Nope.app" / "Contents" / "MacOS" / "Nope")) == "")

    win_exe = tmp / "Application" / "131.0.6778.85" / "chrome.exe"
    win_exe.parent.mkdir(parents=True, exist_ok=True)
    win_exe.write_text("x")
    with Rig(launch=("", ""), nt=True, which={}) as rig:
        check("win：版本写在安装目录名里，读目录名即可（不起进程问，问一次可能被安全软件拦）",
              cd._version_of(str(win_exe)) == "131.0.6778.85", cd._version_of(str(win_exe)))
        check("win：便携版不在那套目录结构上时留空（不编一个版本）",
              cd._version_of(str(tmp / "chrome.exe")) == "")

    with Rig(launch=("", ""), mac=False, nt=False, which={},
             spawns={"--version": (0, "Google Chrome 130.0.6723.58 ")}) as rig:
        check("linux：用 --version 问一次并抽出号码",
              cd._version_of("/usr/bin/google-chrome") == "130.0.6723.58",
              cd._version_of("/usr/bin/google-chrome"))
    with Rig(launch=("", ""), mac=False, nt=False, which={},
             spawns={"--version": (1, "Permission denied")}) as rig:
        check("linux：问不到就留空，并把「问不到」与「版本 0」分开",
              cd._version_of("/usr/bin/google-chrome") == "")


def _test_which_winget():
    print("\n── ⑩ winget 那种「逻辑上在 PATH、实际取不到」的命令 ──")
    found = {}
    saved_shutil = cd.shutil
    with Rig(launch=("", ""), nt=True) as rig:
        try:
            cd.shutil = types.SimpleNamespace(which=lambda c: found.get(c, ""))
            target = r"%LOCALAPPDATA%\Microsoft\WindowsApps\winget.exe"
            expanded = os.path.expandvars(target)
            rig.exists = {expanded}
            check("shutil.which 找不到时再查已知位置：取不到不等于这台机器没有 winget",
                  cd._which("winget") == expanded, cd._which("winget"))
            rig.exists = set()
            check("已知位置也不在 ⇒ 真的是没有（不能把「PATH 里没」说成「有」）",
                  cd._which("winget") == "", cd._which("winget"))
            rig.exists = {expanded}
            check("这个兜底只给 winget，不给随便一个名字（不然会把「没有」说成「有」）",
                  cd._which("notepad") == "", cd._which("notepad"))
        finally:
            cd.shutil = saved_shutil


def _test_page():
    print("\n── ⑪ 官网兜底：两个地址依次试 ──")
    saved = sys.modules.get("webbrowser")
    calls = []
    try:
        sys.modules["webbrowser"] = types.SimpleNamespace(
            open=lambda url: calls.append(url) or (_ for _ in ()).throw(OSError("没有桌面")))
        res = cd.open_official_page()
        check("两个官方地址都试过（www.google.com 在有些网络下不可达）",
              len(calls) == 2 and cd.PAGE_OFFICIAL in calls[0] and cd.PAGE_CN in calls[1], calls)
        check("都打不开时给一句仍然能行动的话（把地址原样念出来）",
              (not res["ok"]) and cd.PAGE_CN in res["why"], res)
    finally:
        if saved is not None:
            sys.modules["webbrowser"] = saved
        else:
            sys.modules.pop("webbrowser", None)
    calls2 = []
    try:
        sys.modules["webbrowser"] = types.SimpleNamespace(open=lambda url: calls2.append(url))
        res = cd.open_official_page()
        check("第一个能打开就不再开第二个（开两个标签页是打扰）",
              res["ok"] and len(calls2) == 1, calls2)
    finally:
        if saved is not None:
            sys.modules["webbrowser"] = saved
        else:
            sys.modules.pop("webbrowser", None)


def _test_cli():
    print("\n── ⑫ 命令行体检：结论进退出码，三档不许并档 ──")
    import io
    import contextlib
    with Rig(launch=("/c/chrome.exe", ""), nt=True, which={}) as rig:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cd.main()
        check("已装 ⇒ 退出码 0", rc == 0, rc)
    with Rig(launch=("", ""), nt=True, which={"winget": "C:\\w\\winget.exe"}) as rig:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cd.main()
        out = buf.getvalue()
        check("确实没装 ⇒ 退出码 10，并把可走的通路打出来",
              rc == 10 and "可走的安装通路" in out, (rc, out[:200]))
    with Rig(launch=("", "ImportError: x"), nt=True, which={}) as rig:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cd.main()
        check("没读到 ⇒ 单独一个退出码 20：把它并进 10 就等于鼓动脚本去重装",
              rc == 20, rc)


# ── ⑬ 下载原语 ─────────────────────────────────────────────────────────
def _test_download_verify():
    print("\n── ⑬ 下载：要么完整且是自己声明的那个文件，要么干脆没有 ──")
    base = Path(tempfile.mkdtemp(prefix="cd_dl_"))

    def run(url, name, resp=None, exc=None, **kw):
        dest = base / name
        sess = FakeSession(resp=resp, exc=exc)
        out = dv.fetch(url, dest, session=sess, **kw)
        return out, dest, sess

    out, dest, sess = run("http://dl.google.com/chrome/x.exe", "d1.exe",
                          resp=FakeResp([b"abc"]))
    check("http 一律拒绝，且一个字节都不落盘（能通融的判据等于没有判据）",
          (not out["ok"]) and "只允许 https" in out["why"] and not dest.exists()
          and not sess.urls, (out, sess.urls))

    body = b"ChromeInstallerBytes"
    want = hashlib.sha256(body).hexdigest()
    out, dest, sess = run("https://dl.google.com/chrome/d2.exe", "d2.exe",
                          resp=FakeResp([body[:6], body[6:]]), expected_sha256=want)
    check("校验对上才算成功，且落名就是目标名（先 .part 再原子改名）",
          out["ok"] and dest.read_bytes() == body and not (base / "d2.exe.part").exists(), out)
    check("成功时把指纹与字节数带回去（远程排障要拿它对包）",
          out["sha256"] == want and out["bytes"] == len(body), out)

    out, dest, sess = run("https://dl.google.com/chrome/d3.exe", "d3.exe",
                          resp=FakeResp([b"tampered"]), expected_sha256=want)
    check("校验不符 ⇒ 失败并**删掉**这份下载结果（留着下次就有人当「已下好」用）",
          (not out["ok"]) and "不一致" in out["why"] and not dest.exists()
          and not (base / "d3.exe.part").exists(), out)

    out, dest, sess = run("https://dl.google.com/chrome/d4.exe", "d4.exe",
                          resp=FakeResp([b"short"], length=10 ** 6))
    check("Content-Length 与实到字节不符 ⇒ 判「下载不完整」，不当成功",
          (not out["ok"]) and "下载不完整" in out["why"] and not dest.exists(), out)

    out, dest, sess = run("https://dl.google.com/chrome/d5.exe", "d5.exe",
                          resp=FakeResp([b"x" * 40], length=40), max_bytes=32)
    check("超过硬上限就中止，并且不留半包",
          (not out["ok"]) and "上限" in out["why"] and not (base / "d5.exe.part").exists(), out)

    out, dest, sess = run("https://dl.google.com/chrome/d6.exe", "d6.exe",
                          resp=FakeResp([], length=0))
    check("对方给空文件不算成功（否则下一步就拿 0 字节的 exe 去运行）",
          (not out["ok"]) and "空文件" in out["why"] and not dest.exists(), out)

    out, dest, sess = run("https://dl.google.com/chrome/d7.exe", "d7.exe",
                          resp=FakeResp([b"abc"]),
                          cancel=lambda: True)
    check("取消是真的取消：临时文件不留",
          (not out["ok"]) and "取消" in out["why"] and not (base / "d7.exe.part").exists(), out)

    out, dest, sess = run("https://dl.google.com/chrome/d8.exe", "d8.exe",
                          resp=FakeResp([b"abc"]), timeout=(1, 1),
                          cancel=lambda: False)
    check("cancel 返回 False 时正常走完（顶掉的接缝不许反过来把成功路弄断）",
          out["ok"], out)

    out, dest, sess = run("https://x/y.exe", "d9.exe", exc=OSError(28, "No space left on device"))
    check("磁盘满单独点名「剩余空间不够」（说成网络问题会让人白折腾）",
          (not out["ok"]) and "磁盘" in out["why"], out["why"])

    out, dest, sess = run("https://x/z.exe", "d10.exe", exc=OSError("SSL: CERTIFICATE_VERIFY_FAILED"))
    check("TLS 失败点出「加密连接建立失败」并提示网关/系统时间",
          "加密连接" in out["why"], out["why"])

    out, dest, sess = run("https://x/w.exe", "d11.exe", exc=OSError("connect timeout"))
    check("超时与「连不上」分两种说法", "超时" in out["why"], out["why"])

    out, dest, sess = run("https://x/v.exe", "d12.exe", resp=FakeResp([b"a"], status=404))
    check("404 说「对方没有这个文件（链接可能已过期）」，不是万金油「请检查网络」",
          (not out["ok"]) and "404" in out["why"] and "没有这个文件" in out["why"], out["why"])

    out, dest, sess = run("https://x/v2.exe", "d12b.exe", resp=FakeResp([b"a"], status=418))
    check("没见过的状态码也要把码带出来（一句「失败」没法行动）",
          "418" in out["why"], out["why"])

    out, dest, sess = run("https://x/u.exe", "d13.exe", resp=FakeResp([b"a" * 8], length=8),
                          max_bytes=10 ** 9)
    check("上限给得宽时不误伤正常包（只卡「来路不明的超大文件」）", out["ok"], out)
    big = FakeResp([b"a"], length=10 ** 12)
    out, dest, sess = run("https://x/big.exe", "big.exe", resp=big)
    check("声明 1 TB 时中止在开始写字之前，并说清是「超过允许的上限」",
          (not out["ok"]) and "超过允许的上限" in out["why"]
          and not (base / "big.exe.part").exists(), out["why"])

    check("sha256_of 读不到文件返回空串（调用方按「校验不了」处理，不抛异常打断安装流程）",
          dv.sha256_of(base / "压根不在的文件.bin") == "")
    p = base / "known.bin"
    p.write_bytes(body)
    check("sha256_of 与 fetch 用同一套算法（两份指纹能对齐）",
          dv.sha256_of(p) == want, dv.sha256_of(p))

    msgs = []
    sess = FakeSession(resp=FakeResp([b"y" * dv.PROGRESS_EVERY,
                                      b"y" * dv.PROGRESS_EVERY]))
    dv.fetch("https://x/progress.exe", base / "progress.exe",
             log=msgs.append, session=sess)
    check("大文件按固定间隔报进度（一次都不报=界面像卡死；每块都报=日志刷满）",
          sum("已下载" in m for m in msgs) >= 1, msgs)
    check("响应一定关掉（不关连接会漏描述符，一个流程里下载几次就会撞到上限）",
          sess.resp.closed == 1, sess.resp.closed)
    check("别人借给你的 session 不替人关（所有权在调用方：它可能还要复用连接池）",
          sess.closed == 0, sess.closed)
    # 自己建的 session 必须自己关：这一路不碰网，只把 requests 模块本身顶掉
    saved_requests = dv.requests
    owned = FakeSession(resp=FakeResp([b"z"]))
    try:
        dv.requests = types.SimpleNamespace(Session=lambda: owned,
                                            RequestException=OSError)
        dv.fetch("https://x/own.exe", base / "own.exe")
        check("fetch 自己建的 session 用完一定关掉（上面那条「不替人关」的另一半）",
              owned.closed == 1, owned.closed)
    finally:
        dv.requests = saved_requests

    # 本轮真实撞到的一个坑：`os.errno` 在 3.10 就被删了，3.13 上访问它是 AttributeError。
    # 它当时在「磁盘满」那一分支里：磁盘满不算大事故，一个未接住的异常却是。
    # 拿源码扫而不是拿行为测扫：行为测只能覆盖本轮跑到的那几个异常类型。
    # 只扫非注释行：修完把名字写进注释是给下一个人看的，判据不能因此判自己红。
    code = "\n".join(l for l in Path(dv.__file__).read_text(encoding="utf-8").splitlines()
                     if not l.strip().startswith("#"))
    check("download_verify 不再用 os.errno（写这条是因为本轮真撞到了：3.10 起被删，"
          "磁盘满那一路会抛成未接住的异常）", "os.errno" not in code and "import errno" in code)


if __name__ == "__main__":
    sys.exit(main())
