# -*- coding: utf-8 -*-
"""chrome_doctor.py —— 「这台机器到底有没有 Chrome、没有的话怎么办」的体检与安装通路。

为什么会有这个模块（2026-11-18 的实录）
------------------------------------
给新用户装工具时，对方机器上没有 Chrome。工具那边只说了一句「请确认已安装 Google
Chrome」，于是这半小时全部由人工代劳：先要判断到底有没有、再找下载地址、再看着他装完、
最后还要重开一次。这句话对开发者是提示，对使用者是**把一个必须由程序完成的判断推给了
人**：他既不知道去哪儿下、也不知道装完该怎么让工具发现。

三条设计决定
----------
1. **「有没有装」只认启动器那一份真源**。工具能不能拉起浏览器，取决于
   ``shared_chrome._find_chrome()`` 走到哪一条候选路径；体检若另开一份候选清单，就会
   出现「体检说装了、启动说没装」这种两个人互相说不通的局面。所以本模块的
   ``installed`` 直接问启动器，注册表 / plist / 版本目录只作为**补充证据**用于排障，
   不参与结论。
2. **成功与否以实测为准，不以退出码为准**。winget 在「已经装好了」的时候返回的是非零
   码，brew 在「拷进去了但你启动的那条路径还不是它」的时候返回 0。每个安装动作做完都
   重新 ``inspect()`` 一次：能找到了就叫成功。退出码只进日志，不进判定。
3. **能直接装就装，装不了就实话实说并留一个出口**。三层顺序：系统自带的包管理器
   （Windows 的 winget / macOS 的 brew，签名与来源由管理器验）→ 官方直链自动下载并安装
   → 打开官网让人手动下。每一层失败都要留下**为什么**（网络不通 / 需要管理员授权 /
   该命令不在这台机器上），而不是笼统一句「安装失败」。国内网络对 dl.google.com 常常
   不通，所以官网那一层不许省——它是唯一在所有网络条件下都能到达的出口。
4. **「读不到」时不推销安装**。探测本身失败（启动器模块导入不了等）与「这台机器确实
   没装」在结论位上长成一个样子，但处置完全相反：前者先要重新确认一次。这种情况下
   ``install_plan`` 只给「重新确认 + 官网」两条，绝不给「装一个浏览器」——那会让一台
   已经装好 Chrome 的机器白白被重装一次，而且用户会照做，因为这个话是工具自己说的。

这一层不碰界面也不自己发起安装
------------------------------
装软件是**必须在用户明确同意之后**才发生的动作：本模块只提供「现在什么情况 / 有哪几条
路 / 这条路会做什么」，由界面层拿去问用户。这样离线判据可以真跑判定逻辑，而不用担心
「跑一遍测试就装了一个浏览器」。
"""
from __future__ import annotations

import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

import download_verify

# 官方页面：Google 自己的下载页是唯一值得推荐的来源（第三方「Chrome 高速下载器」
# 会连带装一堆无关软件，那是今天这类投诉的主要来源）。两个地址都给：国内网络下
# www.google.com 有时不可达，而 www.google.cn 可达且同样是 Google 官方页。
PAGE_OFFICIAL = "https://www.google.com/chrome/"
PAGE_CN = "https://www.google.cn/chrome/"

# Google 官方发布的独立安装包直链（企业版不参与自动更新，适合「装一次就用」的场景；
# 引导器体积最小，但后续仍要从 google 取包，国内更容易卡在第二步）。
WIN_MSI_URL = ("https://dl.google.com/dl/chrome/install/"
               "googlechromestandaloneenterprise64.msi")
WIN_INSTALLER_URL = "https://dl.google.com/chrome/install/latest/chrome_installer.exe"
MAC_DMG_URL = "https://dl.google.com/chrome/mac/universal/stable/GGRO/googlechrome.dmg"

# winget 的 Google Chrome 包标识（Windows Package Manager 客户端自带在 19041+，
# 由微软 CDN 分发安装包——国内可达性明显好于 dl.google.com，所以排在第一层）。
WINGET_ID = "Google.Chrome"
BREW_CASK = "google-chrome"

# Windows 上从 --windowed 的产物里起子进程，不带这个标志就会弹一个黑控制台窗，
# 盖在界面上像个报错窗（用户会以为工具崩了）。CREATE_NO_WINDOW。
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# 安装动作的耐心上限：下载 + 安装在国内网络下一步很慢是常态，给到 15 分钟。
# 不设上限的话，卡住的那一路会把界面里的「正在安装」永远挂在那里。
RUN_TIMEOUT = 900.0

_VER_RE = re.compile(r"\b(\d{2,4}\.\d+\.\d+\.\d+)\b")


# ── 可被测试顶掉的三个接缝：磁盘、注册表、外部命令 ─────────────────────────
def _exists(path: str) -> bool:
    try:
        return bool(path) and os.path.exists(path)
    except OSError:
        return False


def _which(cmd: str) -> str:
    """找可执行文件。

    winget 住在 ``%LOCALAPPDATA%\\Microsoft\\WindowsApps``，那是个「逻辑上在 PATH 里、
    实际可能不在某些 shell 的 PATH 里」的目录（从服务会话、从某些终端起就取不到）。
    所以 Windows 上额外查一次已知位置：``shutil.which`` 找不到不等于这台机器没有 winget。
    """
    found = shutil.which(cmd)
    if found:
        return found
    if os.name == "nt" and cmd == "winget":
        local = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps\winget.exe")
        if _exists(local):
            return local
    return ""


def _spawn(argv, timeout: float = RUN_TIMEOUT, cwd=None):
    """跑一条外部命令，返回 (退出码或 None, 合并后的输出)。"""
    try:
        res = subprocess.run(list(argv), capture_output=True, text=True,
                             timeout=timeout, cwd=str(cwd) if cwd else None,
                             creationflags=_NO_WINDOW)
        text = ((res.stdout or "") + "\n" + (res.stderr or "")).strip()
        return int(res.returncode), text
    except FileNotFoundError as e:
        return None, f"这台机器上没有这个命令：{e}"
    except subprocess.TimeoutExpired:
        return None, f"超过 {int(timeout)} 秒没有结束（已中止等待，可以稍后再试）"
    except OSError as e:
        return None, f"{type(e).__name__}: {e}"


# ── 证据采集：每条都要说清「在哪儿看到的」 ────────────────────────────────
def _launch_path() -> tuple:
    """启动器眼里的 Chrome 路径：全工具唯一那份「有没有装」的判据。

    返回 ``(路径, 探测自身的失败原因)``，两个维度必须分开：「确实没找到」与「没能力去找」
    症状一样、处置完全不同。把后者当后者用，就会对一台装了 Chrome 的机器推销一次
    不必要的安装（而用户会照做，因为这是工具自己说的）。
    """
    try:
        import shared_chrome
        return (shared_chrome._find_chrome() or ""), ""
    except Exception as e:
        return "", f"{type(e).__name__}: {e}"


def _candidate_paths() -> list:
    if os.name == "nt":
        return [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe")]
    if sys.platform == "darwin":
        return ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                os.path.expanduser("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")]
    return ["/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium-browser", "/usr/bin/chromium"]


def _registry_chrome() -> str:
    """Windows 注册表里 Chrome 自报的位置（只做证据，不参与结论）。

    为什么不拿它当结论：Chrome 可以装在非默认位置、可以由别人以别的账号安装、
    也可能是便携版根本不在注册表里——以它为准就会和启动器的判断分叉。
    """
    if os.name != "nt":
        return ""
    try:
        import winreg
    except ImportError:
        return ""
    keys = (r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe")
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for key in keys:
            try:
                with winreg.OpenKey(hive, key) as h:
                    value, _ = winreg.QueryValueEx(h, "")
                    if value and _exists(str(value)):
                        return str(value)
            except OSError:
                continue
    return ""


def _version_of(path: str) -> str:
    """从已确认存在的路径读出版本号；读不到返回空串（体检报告里空串就是「不知道」）。"""
    if not path:
        return ""
    p = Path(path)
    if sys.platform == "darwin":
        # Mac 的 Info.plist 是版本号的正式来源，用标准库读，不起子进程（subprocess
        # 在 --windowed 产物里偶发被安全软件拦，而读 plist 不可能被拦）。
        # 路径只算一层：可执行文件在 ``<app>/Contents/MacOS/``，所以 bundle 是上三层，
        # 而 plist 又回到 ``Contents`` 下——上一版把这两件事当成一层，本机装了 Chrome
        # 却报「版本读不到」（探钉实测抓出来的）。
        try:
            bundle = p.parents[2]
            with (bundle / "Contents" / "Info.plist").open("rb") as fh:
                data = plistlib.load(fh)
            return str(data.get("CFBundleShortVersionString") or "").strip()
        except (OSError, ValueError, IndexError):
            return ""
    if os.name == "nt":
        # Chrome for Windows 的安装结构是 Application\\<版本>\\chrome.exe，
        # 版本就写在目录名里；不在这层结构上（便携版）就留空。
        m = _VER_RE.search(p.parent.name)
        if m:
            return m.group(1)
        return ""
    code, text = _spawn([path, "--version"], timeout=10.0)
    if code == 0:
        m = _VER_RE.search(text or "")
        return m.group(1) if m else ""
    return ""


def inspect() -> dict:
    """本机 Chrome 的体检结果。

    返回字段（**全部为事实，不含建议**；建议走 :func:`install_plan`）：
      installed   启动器能不能找到 Chrome（唯一的结论位）
      certain     这个结论能不能信：False 代表「没读到」而不是「没装」
      path        找到的可执行文件
      version     读得到就写，读不到就空（绝不猜一个）
      platform    系统标识，供上层选处置
      evidence    逐条取证：候选路径命中情况、注册表、包管理器可用性
      manager     这台机器上可用的系统包管理器（winget / brew / 空）
    """
    path, probe_error = _launch_path()
    reg = _registry_chrome()
    evidence = []
    for cand in _candidate_paths():
        mark = "在" if _exists(cand) else "不在"
        evidence.append(f"候选路径{mark}：{cand}")
    if reg:
        evidence.append(f"注册表 App Paths 指向：{reg}")
    elif os.name == "nt":
        evidence.append("注册表 App Paths 没登记 Chrome")
    manager = ""
    if os.name == "nt":
        manager = _which("winget")
        evidence.append(("winget 可用：" + os.path.basename(manager)) if manager
                        else "winget 不可用（这台机器没有系统包管理器）")
    elif sys.platform == "darwin":
        manager = _which("brew")
        evidence.append(("brew 可用：" + manager) if manager else "brew 不可用")
    if probe_error:
        evidence.append(f"启动器没读到（{probe_error}）——这是「读不到」，不是「没装」")
    elif not path:
        evidence.append("启动器（shared_chrome._find_chrome）没找到 Chrome")
    return {
        "installed": bool(path),
        "certain": not probe_error,
        "probe_error": probe_error,
        "path": path or reg,
        "launch_path": path,
        "version": _version_of(path or reg),
        "platform": f"{platform.system()} {platform.release()}",
        "evidence": evidence,
        "manager": manager,
    }


# ── 处置方案：按「侵入性从低到高、可达性从高到低」排 ──────────────────────
def install_plan(info: Optional[dict] = None) -> list:
    """返回这台机器上**真的能走**的几条路，按顺序试。

    每一项是 {kind, label, detail, how}：``label`` 是给按钮/日志看的一句话，
    ``detail`` 是「点了会发生什么」（会不会弹授权、要不要自己等下载），
    ``how`` 是 :func:`run_step` 认的动作名。``page`` 永远在最后且永远存在——
    自动通路可能因为网络/权限全都走不通，那时必须有一个「你自己下」的出口，
    否则这个功能在最需要它的机器上反而什么都不做。
    """
    info = info or inspect()
    steps = []
    if not info.get("certain", True):
        # 结论不可信时连「装不装」都还没定，先把确认这一步递出去，别越过它去动用户的机器
        steps.append({
            "kind": "recheck", "how": "recheck",
            "label": "先重新确认一次这台机器有没有 Chrome",
            "detail": f"这次体检没读成功：{info.get('probe_error', '')}\n"
                      "确认之前不会替你下载安装——万一其实装了，重装一次纯属折腾。\n"
                      "如果重新确认还是读不到，可以关掉工具再打开一次（多数读不到是启动时的"
                      "一次意外）。"})
        steps.append(_page_step())
        return steps
    if info.get("installed"):
        return [_page_step()]
    if os.name == "nt":
        if info.get("manager"):
            steps.append({
                "kind": "winget", "how": "winget",
                "label": "用 Windows 自带的「应用安装程序」装（推荐）",
                "detail": "命令：winget install -e --id Google.Chrome --silent\n"
                          "微软可能弹一次「允许此应用对你的设备进行更改」，点「是」即可；"
                          "全程约 1-3 分钟，装完工具会自己再确认一次。"})
        steps.append({
            "kind": "installer", "how": "google_installer",
            "label": "从 Google 官方地址下载并安装",
            "detail": "取官方安装包到临时目录后运行（同样需要一次管理员授权）。\n"
                      "国内网络常连不上 dl.google.com，这一条不通时会自动改走下一步。"})
    elif sys.platform == "darwin":
        if info.get("manager"):
            steps.append({
                "kind": "brew", "how": "brew",
                "label": "用 Homebrew 装（brew install --cask google-chrome）",
                "detail": "由 Homebrew 校验来源与签名；若需要管理员密码，终端会提示你输入。"})
        steps.append({
            "kind": "dmg", "how": "google_dmg",
            "label": "从 Google 官方地址下载镜像并放好",
            "detail": "取官方 dmg 后挂载、把 Google Chrome.app 复制进「应用程序」"
                      "（不可写时放个人目录下的 Applications，工具同样能找到）。\n"
                      "国内网络连不上 dl.google.com 时会自动改走下一步。"})
    steps.append(_page_step())
    return steps


def _page_step() -> dict:
    """那条永远存在的兜底出口（不许因为前面有自动通路就省掉它）。"""
    return {
        "kind": "page", "how": "page",
        "label": "打开 Chrome 官网自己下载",
        "detail": f"官方页面：{PAGE_OFFICIAL}\n"
                  f"国内可试：{PAGE_CN}\n"
                  "装完不用重启工具——回到这里点一次「我已经装好了」即可。"}


def _download_to(name: str, url: str, max_bytes: int, log) -> dict:
    """取到包外的临时目录（绝不落在包内：onedir 的产物旁边可能是只读的，而
    安装完这两个文件就是垃圾，留在包旁没人会清）。"""
    import tempfile
    dest = Path(tempfile.gettempdir()) / "b2bcollect" / name
    return download_verify.fetch(url, dest, log=log, max_bytes=max_bytes)


def run_step(step: dict, log: Optional[Callable[[str], None]] = None) -> dict:
    """走**一条**安装通路，返回 {ok, why}。

    ``ok`` 只由重新体检决定（见模块开头第 2 条）：命令退出码在这里只用于写清楚「它自己
    说了什么」，不参与结论——「已经装好了」与「装砸了」在退出码上常常长成一个样子。
    """
    say = log or (lambda _m: None)
    how = str(step.get("how") or "")
    why = ""
    code = None
    text = ""
    if how == "recheck":
        say("正在重新确认这台机器有没有 Chrome…")
        fresh = inspect()
        if fresh["installed"]:
            say(f"✅ 确认到了：{fresh['launch_path']}"
                + (f"（版本 {fresh['version']}）" if fresh["version"] else ""))
            return {"ok": True, "why": "", "info": fresh}
        if not fresh["certain"]:
            return {"ok": False, "why": f"还是没读成功：{fresh['probe_error']}",
                    "info": fresh}
        return {"ok": False, "why": "重新确认过了：这台机器确实没有 Chrome，"
                                    "可以走下载安装那一步", "info": fresh}
    if how == "winget":
        argv = [_which("winget"), "install", "-e", "--id", WINGET_ID,
                "--accept-package-agreements", "--accept-source-agreements",
                "--silent"]
        say("正在用 Windows 应用安装程序装 Chrome（可能弹一次授权窗，约 1-3 分钟）…")
        code, text = _spawn([a for a in argv if a])
    elif how == "brew":
        say("正在用 Homebrew 装 Chrome（约 1-3 分钟）…")
        code, text = _spawn(["brew", "install", "--cask", BREW_CASK])
    elif how == "google_installer":
        got = _download_to("chrome_installer.exe", WIN_INSTALLER_URL,
                           64 * 1024 * 1024, say)
        if not got["ok"]:
            return {"ok": False, "why": f"官方安装包没取回来：{got['why']}"}
        say("安装包已取回，正在运行（会弹一次管理员授权窗）…")
        code, text = _spawn([got["path"], "/silent", "/install"])
    elif how == "google_dmg":
        got = _download_to("googlechrome.dmg", MAC_DMG_URL,
                           640 * 1024 * 1024, say)
        if not got["ok"]:
            return {"ok": False, "why": f"官方镜像没取回来：{got['why']}"}
        code, text, ok, why = _install_from_dmg(got["path"], say)
        if not ok:
            return {"ok": False, "why": why}
    else:
        return {"ok": False, "why": f"不认识这个动作：{how}"}

    info = inspect()
    if info["installed"]:
        say(f"✅ Chrome 已就位：{info['launch_path']}"
            + (f"（版本 {info['version']}）" if info["version"] else ""))
        return {"ok": True, "why": "", "info": info}
    tail = (text or "").replace("\n", " ")[-240:]
    if not why:
        why = (f"这一步走完仍没在本机找到 Chrome（退出码={code}）。"
               f"命令原话：{tail or '（没有输出）'}")
    return {"ok": False, "why": why}


def _install_from_dmg(dmg: str, say, targets: Optional[list] = None) -> tuple:
    """挂载官方 dmg 并把 app 放到「应用程序」；不可写就退到个人目录。

    全程用标准命令（hdiutil/ditto），不用第三方解压工具；挂载点固定在自己建的空目录，
    避免与别人的卷同名导致 detach 时误弹用户的盘。

    ``targets`` 是给离线判据留的接缝（真跑时不能拿临时目录当「应用程序」，但那条退个人
    目录的路必须被真实走过）：不传就是现行那两级。
    """
    import tempfile
    mount = Path(tempfile.mkdtemp(prefix="chrome_mount_"))
    app = mount / "Google Chrome.app"
    if targets is None:
        targets = [Path("/Applications"), Path.home() / "Applications"]
    try:
        code, text = _spawn(["hdiutil", "attach", "-nobrowse", "-quiet",
                             "-mountpoint", str(mount), dmg], timeout=180.0)
        if code != 0:
            return code, text, False, f"挂载官方镜像失败：{(text or '').replace(chr(10), ' ')[-200:]}"
        if not app.exists():
            found = sorted(mount.glob("Google Chrome*.app"))
            if not found:
                return code, text, False, "镜像里没有 Google Chrome.app（不是预期那份官方镜像）"
            app = found[0]
        for dest_dir in targets:
            dest = dest_dir / app.name
            try:
                dest_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                continue
            if not os.access(str(dest_dir), os.W_OK):
                continue
            say(f"正在把 Google Chrome 放到 {dest_dir}…")
            if dest.exists():
                # 只在「那一份是个坏安装」时才删它：启动器没找到 Chrome 却在这里有个同名
                # bundle，要么是被人工删过一半、要么是别人装在其他位置。拿 rmtree 碰一个
                # 可能正在运行的 bundle，会把用户开着的标签页一并带走——那已经不是装软件。
                exe = dest / "Contents" / "MacOS" / "Google Chrome"
                if not exe.exists():
                    shutil.rmtree(dest, ignore_errors=True)
                else:
                    return code, text, False, (f"{dest} 已经有一份 Chrome，但启动器没认它"
                                               "（可能是别的安装位置）：请先用官网安装器覆盖安装，"
                                               "工具不替你删一个可能在跑着的浏览器")
            code, text = _spawn(["ditto", str(app), str(dest)], timeout=600.0)
            if code != 0:
                return code, text, False, f"复制失败：{(text or '').replace(chr(10), ' ')[-200:]}"
            return code, text, True, ""
        return code, text, False, ("「应用程序」与个人 Applications 都不可写，"
                                   "请手动把 Chrome 装进去（安装器会自己完成）")
    finally:
        _spawn(["hdiutil", "detach", "-quiet", str(mount)], timeout=60.0)
        try:
            mount.rmdir()
        except OSError:
            pass


def install(log: Optional[Callable[[str], None]] = None,
            only: Optional[list] = None) -> dict:
    """按顺序试完所有自动通路，返回 {ok, tried, info, page}。

    ``tried`` 是逐条台账（哪条路、成功与否、为什么）。留着它是因为远程排障时
    「用户点了确定」之后到底发生了什么，只有这份台账能回答；``page`` 是那条永远存在的
    兜底出口，供界面在同一次交互里直接递给用户。
    """
    say = log or (lambda _m: None)
    info = inspect()
    if info["installed"]:
        return {"ok": True, "tried": [], "info": info, "page": PAGE_OFFICIAL,
                "note": "本机已经有 Chrome，不需要安装"}
    steps = install_plan(info)
    if not info.get("certain", True):
        # 先把“确认”这一步走完再说：下面那些通路都会下载并运行安装程序，
        # 拿一个自己都不确定的结论去动用户的机器，比少做一次安装严重得多
        res = run_step({"how": "recheck"}, say)
        if res.get("ok"):
            return {"ok": True, "tried": [{"how": "recheck", "label": "重新确认",
                                           "ok": True, "why": ""}],
                    "info": res["info"], "page": PAGE_OFFICIAL,
                    "note": "重新确认后发现本机已装 Chrome，没有执行安装"}
        if not res["info"].get("certain", True):
            return {"ok": False,
                    "tried": [{"how": "recheck", "label": "重新确认",
                               "ok": False, "why": res["why"]}],
                    "info": res["info"], "page": PAGE_OFFICIAL,
                    "note": res["why"]}
        info, steps = res["info"], install_plan(res["info"])
    wanted = {str(s.get("how")) for s in steps
              if s.get("how") not in ("page", "recheck")}
    if only is not None:
        wanted &= {str(x) for x in only}
    tried = []
    for step in steps:
        how = str(step.get("how") or "")
        if how not in wanted:
            continue
        res = run_step(step, say)
        tried.append({"how": how, "label": step.get("label"),
                      "ok": res.get("ok"), "why": res.get("why", "")})
        if res.get("ok"):
            return {"ok": True, "tried": tried, "info": res.get("info") or inspect(),
                    "page": PAGE_OFFICIAL}
        say(f"⚠ 这一条没走通：{res.get('why', '')}")
    return {"ok": False, "tried": tried, "info": inspect(), "page": PAGE_OFFICIAL}


def open_official_page() -> dict:
    """打开 Chrome 官网。两个官方地址依次试：第一个在有些网络下不可达。"""
    import webbrowser
    for url in (PAGE_OFFICIAL, PAGE_CN):
        try:
            webbrowser.open(url)
            return {"ok": True, "url": url}
        except Exception:
            continue
    return {"ok": False, "url": PAGE_OFFICIAL,
            "why": "这台机器的默认浏览器打不开（可以用系统浏览器直接访问 "
                   f"{PAGE_OFFICIAL} 或 {PAGE_CN}）"}


def summary_text(info: Optional[dict] = None) -> str:
    """给人看的体检结论：先说结论，再一行一条证据。

    结论只有三种说法，而且不能串档：已装 / 确实没装 / 没读到。把第三种说成第二种，
    就等于用工具自己的名义建议用户重装一个已经装好的浏览器。
    """
    info = info or inspect()
    if info["installed"]:
        head = (f"已安装：{info['launch_path']}"
                + (f"（版本 {info['version']}）" if info["version"] else
                   "（版本号没读到，不影响使用）"))
    elif not info.get("certain", True):
        head = ("没读到 Chrome（这次体检本身失败了），所以不能断定「没装」："
                f"{info.get('probe_error', '')}")
    else:
        head = "没有找到 Google Chrome：本工具的采集要用它，这台机器现在跑不了浏览器采集那几路。"
    return head + "\n" + "\n".join(f"· {e}" for e in info["evidence"])


def main(argv=None) -> int:
    """命令行体检：``python -m chrome_doctor``。

    只报告、不动手：与界面层同一个划分（装软件必须由用户当场同意）。
    退出码供脚本用：0=已装、10=确实没装、20=没读到（结论不可信，不能当成没装）。
    与 access_guard 的 doctor 同一族口径：能用退出码表的结论不靠人读屏。
    20 单独一个码是因为「没读到」与「没装」在脚本里必须分叉：前者该去修环境，
    后者才该去装浏览器；把两者归成一档，就等于鼓动脚本去重装。
    """
    del argv                                          # 无参数：这一句只要一个结论
    info = inspect()
    print(summary_text(info))
    if not info["installed"]:
        print("\n可走的安装通路（界面层会逐项问过用户再动手）：")
        for step in install_plan(info):
            print(f"  · [{step['kind']}] {step['label']}")
            print("      " + step["detail"].replace("\n", "\n      "))
    if info["installed"]:
        return 0
    return 10 if info.get("certain", True) else 20


if __name__ == "__main__":
    sys.exit(main())
