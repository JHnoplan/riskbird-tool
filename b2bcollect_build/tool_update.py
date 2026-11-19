#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tool_update.py —— 新版本提示与「点一下就装好」的自动更新（需求 2 第二块）。

发布通道就是制品已经在用的那一份固定 OSS 直链（见 `.github/scripts/upload_release_to_oss.py`）：
每个平台一份自己的清单（`0817899624/tool_releases/latest_windows.json`、
`latest_mac_arm64.json`）描述「这条通道上最新是哪一版、更新说明写了什么、这个平台
的包在哪个地址、指纹是多少」。不新开服务、不做第二套下载域——多一条通道就多一处
「哪条更新了哪条没更新」的对不齐；而 Windows 与 Mac 的构建号本来就是两个独立计数器，
写成一份全仓清单就是把两个计数器塑成同一个数（见 `MANIFEST_BY_KEY` 上的注释）。

四条设计决定，每条都有对应的本地判据：

1. **非强制，且记住用户的选择**。有新版本只说一次：弹一句能看懂的说明 + 「现在就装 /
   这版先不提示」。选了「先不提示」就按**版本号**记住（不是按时间、不是全局关掉）：
   下次出了更新的版本还会正常提醒，而同一版本不会天天来。这对齐主流桌面工具
   （VS Code / Chrome / Office 都是「这一版可以稍后」而不是「永远别再问」）。
2. **装之前必须先说清会发生什么**：谁在写你的硬盘、要不要授权、要等多久、装完会不会
   重开。同意发生在这些信息之后，不是之后补一句说明。
3. **只认白名单里的地址**。清单里哪怕出现一个外部 URL，也一律拒收（下载的是可执行程序，
   「清单说下哪就下哪」必须是硬约束，不能靠清单本身可信）：主机名与前缀都锚定到发布桶；
   文件名必须在制品白名单内；sha256 必须是 64 位十六进制；size 必须是正整数。
   校验不过就删件并停下来说话——绝不让一个「大概是对的」的包进入安装那一步。
4. **成败以重新核对为准**，不信命令退出码：装完之后要么能读到新包的构建戳，要么就不说
   「装好了」。安装这一步在两个平台上的可行做法并不一样，所以分开实现（见
   :func:`handoff_kind`），但共同遵守一条：本机正在跑的这个 exe **不去覆盖自己**。

平台差异（这是实现约束，不是风格偏好）：
  · Windows：正在运行的 exe 与它加载的 DLL 都拒绝被就地覆盖，所以本模块不尝试原地替换，
    而是交回制品自带的 NSIS 安装包（它本来就是交付物，且已经处理好安装目录、快捷方式、
    卸载信息、以及「有实例在跑」那把锁）。顺序是钉死的：交接进程先等旧实例的进程号真
    消失，再**同步跑完**安装包并取它的退出码，再读新包自己的构建戳对一次版本，最后才
    拉起新工具。这里刻意不用「把安装包丢出去就不管」那种写法：NSIS 在静默模式下碰到目
    录被占是直接 `Abort` 的（见 installer/b2bcollect_setup.nsi 的 check_lock/abort_locked，
    它不能弹一个没人能点的重试框），发完不管就等于把那次 Abort 变成一个「工具关了、什
    么都没装、屏幕上一个字都没有」的事故。等旧进程退出发生在跑安装包**之前**，所以那把
    锁根本不会被撞上，而不是靠重试碰运气。
  · macOS：可以 unlink/rename 一个正在运行的 bundle（POSIX 语义），所以旧实例退出后，
    由自己换自己：等新进程退出 → 解包 → 把旧 .app 挪到旁边 → 新的放进原位置 → 对一次
    新包的构建戳 → 拉起新实例 → 只在一切就绪后才清旁边那份（清不掉只说一句，不谎报成功；
    版本没对上就**留着**旁边那份旧版本并说清可以换回来）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Callable, Optional

try:
    import download_verify as _dv
    HAS_DOWNLOAD_VERIFY = True
except ImportError:                                    # pragma: no cover
    HAS_DOWNLOAD_VERIFY = False

try:
    import tool_version as _tv
    HAS_TOOL_VERSION = True
except ImportError:                                    # pragma: no cover
    HAS_TOOL_VERSION = False

# ── 发布通道（唯一口径，CI 生成清单时用的也是这几个名字）──────────
BUCKET_HOST = "images6396289.oss-cn-shenzhen.aliyuncs.com"
RELEASE_PREFIX = "0817899624/tool_releases"
SCHEMA = "release-v1"
# **每个平台一条自己的通道**，而不是全仓一份 latest.json：两个 workflow 各自的
# github.run_number 是两个独立计数器，Windows 的 1.234 与 Mac 的 1.234 根本不是同一件
# 事。把它们塞进同一个 version 字段，结论必然有一边是错的（而且错得很静：另一边的
# 机器会天天提「有新版」或永远不提）。各写各的 key 之后，一条通道只会说自己那个计数器。
MANIFEST_BY_KEY = {"win": "latest_windows.json",
                   "mac_arm64": "latest_mac_arm64.json"}
ARTIFACT_WINDOWS_SETUP = "B2bCollectToolSetup.exe"
ARTIFACT_MAC_ARM = "B2bCollectTool_Mac_arm64.zip"
ARTIFACT_WINDOWS_PORTABLE = "B2bCollectTool_Win_portable.zip"
# 清单里的平台键 → 该键**必须**对应的档名。只校「档名在白名单里」是不够的：那样
# `{"win": {"file": "B2bCollectTool_Mac_arm64.zip"}}` 也能过关（地址、校验和、字节数全都自
# 治），而 Windows 那边会拿着一个 .zip 去启动安装包。平台键与档名必须是双向钉死的
# 对应关系，而且两边写错了都要能在发版当场报出来（生成脚本与本模块共用这一份表）。
ARTIFACT_BY_KEY = {"win": ARTIFACT_WINDOWS_SETUP,
                   "mac_arm64": ARTIFACT_MAC_ARM,
                   # 解压版不参与自动安装（platform_key 永远不返回这个键），但它是 Windows
                   # 清单里可以并存的一档，见 :func:`portable_url`。
                   "win_portable": ARTIFACT_WINDOWS_PORTABLE}
# 制品白名单：就是 ARTIFACT_BY_KEY 的值域，不另列一份（列两份迟早只改一处：白名单
# 少一个→那一档被当成不认识；表少一个→白名单收下却拼不出地址）。
# 与 .github/scripts/upload_release_to_oss.py 的 ALLOWED_NAMES 逐字对拍：那边多一个名字、
# 这里少一个名字的症状都是「有新版本却装不上」，而只能到发版当天才发现。
ALLOWED_ARTIFACTS = tuple(ARTIFACT_BY_KEY.values())
# 清单文件的字节上限：它是给人读的一段话，不是制品。超上限一律按「清单坏了」处理，
# 而不是「下一个更大的也行」——被换大的 latest.json 和被换小的制品是同一类事故。
MANIFEST_MAX_BYTES = 256 * 1024
# 制品下载上限：Mac 包实测 200MB 量级，给到 1GB 是「比实际大一档的容错」，
# 同时仍然拒绝任何把这里当自由下载地址的清单（几十 GB 会把磁盘写满）。
ARTIFACT_MAX_BYTES = 1024 * 1024 * 1024
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
# 旧实例退出后，交接进程最多再等这么久（秒）：没退出就不硬装，如实停下。
INSTALLER_LOCK_WAIT_SECONDS = 30.0
# 静默安装自身的上限：onedir 整批复制在慢盘/共享盘上也要得了几分钟，给 15 分钟是
# 「比实测大一档的容错」；超上限不是失败而是「这边不能再等」，那句必须说清旧版本还在。
INSTALLER_TIMEOUT_SECONDS = 15 * 60.0
# 「这版先不提示」记在界面偏好同一个文件里，不新开一份状态（见 GUI 的 ui_prefs）。
SNOOZE_KEY = "update_snoozed_version"
APPLY_FLAG = "--apply-update"
WAIT_PID_FLAG = "--wait-pid"
EXPECT_BUILD_FLAG = "--expect-build"
RELAUNCH_FLAG = "--relaunch"


# ── 平台与落点 ────────────────────────────────────────────────────────
SYSCTL_PATH = "/usr/sbin/sysctl"


def _sysctl_flag(name: str) -> Optional[bool]:
    """问一个 sysctl 开关是不是 1。None = 问不出（系统命令跑不通/没输出），不是「不是 1」。"""
    try:
        r = subprocess.run([SYSCTL_PATH, "-n", name], capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    out = (r.stdout or b"").decode("utf-8", "replace").strip()
    if out == "":
        return None
    return out == "1"


def darwin_is_arm64(machine: Optional[str] = None,
                    translated: Optional[bool] = None) -> bool:
    """这台 Mac 该拿 arm64 的包吗。

    不能只看 `platform.machine()`：Apple Silicon 上用 Rosetta 跑起来的进程（打包机、
    x86_64 的 Python）报的是 **x86_64**，拿它直接归到 `other` 就是一台能自动更新的机器
    被当成不能（而它恰好是唯一有对应制品的 Mac 形状）。所以 x86_64 不直接下结论，
    先去问两个只有 Apple Silicon 才会回答 1 的开关：
      · `hw.optional.arm64`：硬件有 arm64 能力；
      · `sysctl.proc_translated`：当前进程正在 Rosetta 转译下跑。
    两者都问不出时才算 Intel（`other`）：宁可少给一条自动通路，也不拿一个不对症的包去装。
    """
    arch = (machine or "").strip().lower()
    if not arch:
        try:
            import platform as _p
            arch = (_p.machine() or "").strip().lower()
        except Exception:
            arch = ""
    if arch in ("arm64", "aarch64"):
        return True
    if arch != "x86_64":
        return False
    if translated is not None:
        return bool(translated)
    for _name in ("sysctl.proc_translated", "hw.optional.arm64"):
        if _sysctl_flag(_name) is True:
            return True
    return False


def platform_key(machine: Optional[str] = None, win: Optional[bool] = None,
                 darwin: Optional[bool] = None, translated: Optional[bool] = None) -> str:
    """这台机器该装哪个平台的包：`win` / `mac_arm64` / `other`。

    `other` 是有意的第三种答案（Intel Mac、Linux 源码运行）：这时**不许**猜一个平台
    去下载，而要如实说「这一版没有适配这台机器的自动更新」，仍然把新版本与说明告诉人。
    """
    if win is None:
        win = os.name == "nt"
    if darwin is None:
        darwin = sys.platform == "darwin"
    if win:
        return "win"
    if darwin:
        env_arch = (machine or os.environ.get("B2B_ARCH") or "").strip().lower()
        return "mac_arm64" if darwin_is_arm64(env_arch, translated=translated) else "other"
    return "other"


def staging_dir() -> Path:
    """下载落点：**包外**的用户目录。包内目录可能只读（App Translocation、Program Files），
    而且把几百 MB 的临时包留在交付目录里，等于给用户留一份没人会清垃圾。
    """
    return Path.home() / ".b2bcollect" / "updates"


# ── 清单：取回、验形、比较 ─────────────────────────────────────────────
def _why(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def _positive(value) -> int:
    """命令行上的一个数收成非负整数；不像数就当没给（0），由调用方按「缺省」处理。"""
    try:
        return max(0, int(str(value).strip()))
    except (TypeError, ValueError):
        return 0


def manifest_name(key: Optional[str] = None) -> str:
    """这台机器该问哪一份清单；没有对应通道时返回空串（调用方不许拿空串去拼 URL）。"""
    return MANIFEST_BY_KEY.get(key if key is not None else platform_key(), "")


def manifest_url(key: Optional[str] = None) -> str:
    """清单直链；没有对应通道时返回空串。"""
    name = manifest_name(key)
    return f"https://{BUCKET_HOST}/{RELEASE_PREFIX}/{name}" if name else ""


def portable_url() -> str:
    """Windows 解压版的固定地址。

    它不在自动通路里（正在跑的解压版 exe 依旧不能被就地覆盖），但那一台机器上的人
    确实需要这个选项：装了安装包就会在默认位置多出一份工具，对靠「拷在 U 盘里」干活
    的人来说不是升级。所以界面在那个时候能把地址递给人（开之/复制），而不是替他选一个。
    """
    return f"https://{BUCKET_HOST}/{RELEASE_PREFIX}/{ARTIFACT_WINDOWS_PORTABLE}"


def fetch_manifest(log: Optional[Callable[[str], None]] = None,
                   timeout: tuple = (10, 20), dest: Optional[Path] = None,
                   key: Optional[str] = None) -> dict:
    """取回本机那个平台的清单。返回 {ok, path, raw, why}。

    走 download_verify 而不是自己 requests.get：https-only、状态码分诊、声明大小与
    实到字节不符判「不完整」、失败不留残件、空文件不算成功——这些纪律在制品下载上
    已经付过学费，清单下载没有理由标准更低（清单坏了同样能把错误的包引到用户机器上）。
    """
    if not HAS_DOWNLOAD_VERIFY:
        return {"ok": False, "path": "", "raw": "",
                "why": "下载校验模块（download_verify）没能加载，这一版不做自动更新"}
    url = manifest_url(key)
    if not url:
        # 走到这里只可能是调用点把「本机没有对应通道」这台机器递了进来（正常路径在
        # :func:`check` 里就分了叉）。这里不能去拼一个不存在的 key、问一句再把 404 当
        # 结论：那会把「这台机器不适配」说成「对方没文件」，而两者处置完全不同。
        return {"ok": False, "path": "", "raw": "",
                "why": f"本机平台（{key or platform_key()}）没有对应的自动更新通道"}
    say = log or (lambda _m: None)
    name = manifest_name(key)
    path = Path(dest) if dest else (staging_dir() / name)
    res = _dv.fetch(url, path, log=say, max_bytes=MANIFEST_MAX_BYTES, timeout=timeout)
    if not res.get("ok"):
        return {"ok": False, "path": "", "raw": "", "why": str(res.get("why") or "")}
    try:
        raw = Path(res["path"]).read_text(encoding="utf-8")
    except OSError as e:
        return {"ok": False, "path": res["path"], "raw": "",
                "why": f"清单下来了但读不动：{_why(e)}"}
    say(f"   清单指纹 sha256 {res.get('sha256', '')[:16]}…")
    return {"ok": True, "path": res["path"], "raw": raw, "why": ""}


def _artifact_entry(entry, key: str) -> dict:
    """清洗一个平台的条目；任何一项不像话就返回 {ok:False, why}（不返回半成品）。"""
    if not isinstance(entry, dict):
        return {"ok": False, "why": f"{key}：这一档不是对象"}
    name = ARTIFACT_BY_KEY[key]
    file_name = str(entry.get("file") or "").strip()
    if file_name != name:
        # 平台键与档名不符：这不是「不认识的一档」，而是清单写错了，必须红
        return {"ok": False, "why": f"{key}：这一档对应的制品应该是 {name}，"
                                    f"清单里写的却是「{file_name or '空'}」，不敢照着下"}
    url = str(entry.get("url") or "").strip()
    want = f"https://{BUCKET_HOST}/{RELEASE_PREFIX}/{name}"
    if url != want:
        # 逐字等值比较，不做「同域就行」的宽松判断：同桶里多一个前缀就能把制品换成
        # 任意一份文件，而文件名与安装方式全都对得上，症状是「装了个来路不明的包」。
        return {"ok": False, "why": f"{name}：下载地址不是发布直链（应为 {want}）"}
    digest = str(entry.get("sha256") or "").strip().lower()
    if not _HEX64.match(digest):
        return {"ok": False, "why": f"{name}：缺少可用的文件指纹（sha256）"}
    try:
        size = int(entry.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    if size <= 0:
        return {"ok": False, "why": f"{name}：字节数不像是真的（{entry.get('size')!r}）"}
    return {"ok": True, "key": key, "name": name, "url": url, "sha256": digest, "size": size}


def normalize_manifest(raw) -> dict:
    """把清单收成可用形：{ok, data:{schema,version,build,published_at,notes,artifacts}, why}。

    `raw` 允许是字符串（原文）或已解析的对象：两条路都从这里过，界面与 CLI 就不必
    各写一份「先 json.loads 再说」。
    """
    if isinstance(raw, str):
        try:
            obj = json.loads(raw)
        except ValueError as e:
            return {"ok": False, "data": {}, "why": f"清单不是合法的 JSON：{_why(e)}"}
    else:
        obj = raw
    if not isinstance(obj, dict):
        return {"ok": False, "data": {}, "why": "清单顶层不是对象，没法按字段读"}
    schema = str(obj.get("schema") or "").strip()
    if schema != SCHEMA:
        # 认不出的格式一律不猜字段含义：这一版按「问不出结论」处理，而不是
        # 「少几个字段就当没有更新」——后者会让新格式的清单永远读成「已是最新版」。
        return {"ok": False, "data": {},
                "why": f"清单格式是 {schema or '（空）'}，这一版工具只认 {SCHEMA}"}
    build = _tv.parse_version(obj.get("version")) if HAS_TOOL_VERSION else None
    if build is None:
        return {"ok": False, "data": {},
                "why": f"清单里的版本号「{obj.get('version')!r}」读不出来"}
    if build == _tv.UNKNOWN_BUILD:
        # 远端写的是「1.0」这档：在本模块的口径里 0 不是「第 0 版」而是「没能确认」。
        # 拿它当最新版去比，结论永远是「本机更新」（up-to-date），而真正的表现是
        # 从此再也提不到更新——CI 忘注构建号时 silently 发布一沓空号清单是最难发现的一形。
        return {"ok": False, "data": {},
                "why": f"清单里的版本号是「{obj.get('version')}」（构建号 0 = 未能确认），"
                       "不敢拿它当最新版；这是发布那一步没注入构建号"}
    arts = {}
    raw_arts = obj.get("artifacts")
    if not isinstance(raw_arts, dict):
        return {"ok": False, "data": {}, "why": "清单里没有 artifacts 这一档"}
    for key, entry in raw_arts.items():
        if str(key) not in ARTIFACT_BY_KEY:
            # 认不出的平台键直接跳过而不是整体失败：CI 将来多挂一个平台时，
            # 老版本工具仍然该能用它认得的那几档（不认识的档只该不认识，不该报错）。
            continue
        got = _artifact_entry(entry, str(key))
        if not got.get("ok"):
            return {"ok": False, "data": {}, "why": str(got.get("why") or "")}
        arts[str(key)] = got
    notes = obj.get("notes")
    if isinstance(notes, list):
        notes = "\n".join(str(x).strip() for x in notes if str(x).strip())
    return {"ok": True, "data": {
        "schema": schema,
        "version": str(obj.get("version") or ""),
        "build": build,
        "published_at": str(obj.get("published_at") or "").strip(),
        "notes": str(notes or "").strip(),
        "artifacts": arts,
    }, "why": ""}


def _current_build() -> Optional[int]:
    """本机构建号；**说不清时返回 None**（不是 0）：拿 0 去比大小=永远「对方更新」。"""
    if not HAS_TOOL_VERSION:
        return None
    return _tv.build_number() or None


def _verdict(state: str, why: str = "", current: str = "", latest: str = "",
             build: int = 0, notes: str = "", published_at: str = "",
             artifacts: Optional[dict] = None, artifact: Optional[dict] = None,
             distance: Optional[int] = None) -> dict:
    """结论的唯一形状。

    写在一个构造器里而不是五个 return 旁边各拼一份：少一个键在界面上就是一个 KeyError
    或一个永远空白的小格，而两种都只在「走到那一态」时才暴露——离线判据能覆盖每一态，
    但覆盖不到的那个键迟早会在调用点上被当成「这一态永远不会有这个信息」。
    """
    return {"state": state, "why": why, "current": current, "latest": latest,
            "build": build, "notes": notes, "published_at": published_at,
            "artifacts": artifacts or {}, "artifact": artifact, "distance": distance}


def evaluate(manifest, current: Optional[str] = None, key: Optional[str] = None) -> dict:
    """这份清单对本机意味着什么：见 :func:`_verdict` 的那一份形状。

    state 五种，互相排斥，界面按它分派，绝不在调用点再判一次：
      update-available  有新版本（本机确实旧，且本机平台有自动包）
      up-to-date        已是最新
      unknown           说不清（本机版本未能确认 / 连不上）
      unsupported       本机平台不在这条自动通路里（有新版也只说不替装）
      bad-manifest      清单本身不合法（与 unknown 分开：后者可重试，前者要 CI 修）

    三种来路都在这一处收口（原文字符串 / 已 normalize 的 {ok,data,why} / 拿不回东西的
    {ok:False,why}）：界面与 CLI 不许各自再判一遍，否则「清单坏了」与「本机版本未能
    确认」这两种红迟早会在某一个调用点上被当成「已是最新版」。
    """
    if isinstance(manifest, dict) and "data" in manifest:
        norm = manifest
    elif isinstance(manifest, dict) and manifest.get("ok") is False:
        norm = {"ok": False, "data": {}, "why": str(manifest.get("why") or "清单读不出")}
    else:
        norm = normalize_manifest(manifest)
    if not norm.get("ok"):
        return _verdict("bad-manifest", str(norm.get("why") or "清单读不出"))
    data = norm["data"]
    cur_build = _current_build()
    cur_text = (current if current is not None
                else (_tv.version_text(cur_build) if HAS_TOOL_VERSION and cur_build else ""))
    if cur_build is None:
        # 本机说不清自己是哪版：这时候弹「有新版本」是把猜测当结论。CI 与打包硬门
        # 盯的就是「产物必须自报」，真读不到时界面该说的是「没能确认」。
        return _verdict("unknown",
                        "没能确认本机版本（随包构建戳读不到），所以不敢说有新的"
                        "；远程排障可跑 tool_version.py --print-version",
                        current=cur_text or "（没能确认本机版本）",
                        latest=data["version"], build=data["build"], notes=data["notes"],
                        published_at=data["published_at"], artifacts=data["artifacts"])
    cmp_result = 1 if data["build"] > cur_build else (0 if data["build"] == cur_build else -1)
    # distance 给的是**真实差距**（清单构建号 - 本机构建号），不是方向：
    # 叫 distance 却只放 ±1，下一个读它的人会把「差一个版本」当成「差 N 个版本」来写话术。
    gap = data["build"] - cur_build
    if cmp_result <= 0:
        return _verdict("up-to-date", current=cur_text, latest=data["version"],
                        build=data["build"], notes=data["notes"],
                        published_at=data["published_at"], artifacts=data["artifacts"],
                        distance=gap)
    key = platform_key() if key is None else key
    art = data["artifacts"].get(key)
    if art is None:
        return _verdict("unsupported",
                        f"有更新的版本，但这一版没准备适配本机（{key}）的自动安装包",
                        current=cur_text, latest=data["version"], build=data["build"],
                        notes=data["notes"], published_at=data["published_at"],
                        artifacts=data["artifacts"], distance=gap)
    return _verdict("update-available", current=cur_text, latest=data["version"],
                    build=data["build"], notes=data["notes"],
                    published_at=data["published_at"], artifacts=data["artifacts"],
                    artifact=art, distance=gap)


def check(log: Optional[Callable[[str], None]] = None,
          manifest_fetch: Optional[dict] = None,
          key: Optional[str] = None) -> dict:
    """一句话出口：取本机那个平台的清单 → 验形 → 对本机给结论。只返回 evaluate() 的形状。

    分派只看 `state` 那五个互斥的取值，本函数不再额外返回一个 ok/has_update 的布尔位：
    多一个口径就多一处漂移，而这里需要的正是「五种答案各说各的话」——unknown 可以
    重试（当时没网、这个包本来就没注戳），bad-manifest 要修发布（格式/地址/指纹/版本号
    不像真的），unsupported 是本机平台不在自动通路里。把前两种挤进一个布尔位，远程
    支持就会拿着「没网的机器」去查 CI。
    """
    key = platform_key() if key is None else key
    if key not in MANIFEST_BY_KEY:
        # 根本不发请求：这一台机器（Intel Mac、Linux 源码跑）没有对应的发布 key，问了
        # 只会拿到一个 404，然后把「不适配」说成「对方没文件/网络不行」。
        return _verdict("unsupported",
                        f"这一版本没有适配本机平台（{key}）的自动更新通道，"
                        "有新版也只把说明告诉你，不替你装")
    got = manifest_fetch if manifest_fetch is not None else fetch_manifest(log=log, key=key)
    if not got.get("ok"):
        # 连清单都没拿回来：这是 unknown（可重试），不是 bad-manifest（CI 要修）。
        # 两者混成一个，远程支持就会去查发布流程，而实际只是用户当时没网。
        return _verdict("unknown", str(got.get("why") or "没能连上发布地址"))
    return evaluate(got["raw"], key=key)


# ── 更新说明：给人读的那一段 ────────────────────────────────────────────
def notes_view(eval_res: dict) -> str:
    """把更新说明排成弹窗里的一段话（不解释、不加工内容，只保证空清单也有话说）。

    说明文字的来源在 CI 那一侧（管理员写的那份，或按提交记录生成的那份草稿），本模块
    不替管理员润色也不自己编「本次更新」：界面上出现的每一句「更新了什么」都必须是
    发布时真写过的那一句。
    """
    notes = str((eval_res or {}).get("notes") or "").strip()
    if not notes:
        return "这次发布没有留下更新说明。（工具不替你编一句，只把发布时真写的内容给你看）"
    return notes


def install_steps(art: Optional[dict] = None) -> list:
    """同意之前必须摊开说的几步：每步 {label, detail}。平台不同，步骤真的不同。

    按**这一份制品属于哪个平台**分派（_artifact_entry 已经把 key 带在制品里），不在这里
    重询一次本机：界面上正在说的是「你点确定之后会装这个包」，拿本机去推一个另一边的
    安装包长什么样，就是把一句假话写到同意之前（Windows 清单递 Mac 包时那句「不需要
    管理员授权」就错了）。
    """
    size = int((art or {}).get("size") or 0)
    size_text = (f"约 {size // 1048576} MB" if size else "大小未知")
    key = str((art or {}).get("key") or "") or platform_key()
    if key == "win":
        return [
            {"label": "先下载新安装包",
             "detail": f"存到你自己的文件夹里（{size_text}），下载完会核对文件指纹，"
                       "对不上就直接删掉并告诉你对不上，不会拿它去装。"},
            {"label": "关掉工具，用官方安装包装上",
             "detail": "就是你第一次安装时用的那种安装包，装到哪里、快捷方式、卸载项都"
                       "照原来的走；这一步不需要管理员授权。"},
            {"label": "装完自动重新打开",
             "detail": "打开的就是新版本。本轮数据与进度在关掉之前已经落盘，不会丢。"},
        ]
    if key == "mac_arm64":
        return [
            {"label": "先下载新的应用包",
             "detail": f"（{size_text}）下载完核对文件指纹，对不上就删掉并说清哪对不上。"},
            {"label": "关掉工具，把新版本放回原来的位置",
             "detail": "旧的一份会先挪到旁边，确认新的能打开之后再清掉；"
                       "如果这一台机器上工具所在目录不可写，这一步不会硬来，"
                       "而是把下载好的包留给你手动双击。"},
            {"label": "装完自动重新打开",
             "detail": "打开的就是新版本。本轮数据与进度在关掉之前已经落盘，不会丢。"},
        ]
    return [{"label": "这一版没有适配本机的自动安装",
             "detail": f"识别到的平台是「{key}」。有新版本与说明照样告诉你，"
                       "但自动安装这一步不做（不猜一个别的平台的包来装）。"}]


# ── 下载 ──────────────────────────────────────────────────────────────
def download(art: dict, log: Optional[Callable[[str], None]] = None,
             cancel: Optional[Callable[[], bool]] = None, dest_dir=None) -> dict:
    """按清单给的地址取制品，并**必须**按清单给的指纹校验。返回 {ok, path, sha256, why}。"""
    say = log or (lambda _m: None)
    if not HAS_DOWNLOAD_VERIFY:
        return {"ok": False, "path": "", "why": "下载校验模块没能加载，不做自动更新"}
    if not art or not art.get("url") or not art.get("sha256"):
        return {"ok": False, "path": "", "why": "缺少可用的下载地址或文件指纹，没有开始下载"}
    base = Path(dest_dir) if dest_dir else staging_dir()
    dest = base / str(art["name"])
    res = _dv.fetch(art["url"], dest, log=say, expected_sha256=str(art["sha256"]),
                    max_bytes=min(ARTIFACT_MAX_BYTES, max(int(art.get("size") or 0), 1)
                                  * 2), cancel=cancel)
    if not res.get("ok"):
        return {"ok": False, "path": "", "why": str(res.get("why") or "下载失败")}
    # 字节数也复核一遍：指纹相同则必然相同，但把「声明 200MB、实到 60MB」这种
    # 只在 size 上露馅的形状当场抓住，比留给用户去发现便宜得多。
    declared = int(art.get("size") or 0)
    if declared and int(res.get("bytes") or 0) != declared:
        try:
            Path(res["path"]).unlink()
        except OSError:
            pass
        return {"ok": False, "path": "",
                "why": f"包不完整：清单说 {declared} 字节，实到 {res.get('bytes')} 字节，已删掉"}
    say(f"✅ 指纹核对通过（sha256 {res.get('sha256', '')[:16]}…）")
    return {"ok": True, "path": str(res["path"]), "sha256": res.get("sha256", ""), "why": ""}


def handoff_kind(art: Optional[dict] = None) -> str:
    """本机走哪条安装路：`installer`（Windows）/ `self_replace`（macOS）/ `none`。

    同样优先按制品自己的平台键判（与 :func:`install_steps` 同一个口径），因为「怎么装」
    这句话必须与刚才说给人听的那几步对得上。
    """
    key = str((art or {}).get("key") or "") or platform_key()
    if key == "win":
        return "installer"
    if key == "mac_arm64":
        return "self_replace"
    return "none"


# ── 拉起：两个平台、打包/源码两种形态，只保留一份写法 ────────────────
def launch_command_for(target: str) -> list:
    """「拉起这个路径」要用的命令：冻结态直接起 exe，源码态起解释器 + 脚本。

    与界面的 :meth:`MainWindow._relaunch_command` 同一条口径，但目标由调用方给：升级时
    要拉起的是**刚装完的那个产物**，不是现在这个解释器（两者在源码态根本不是同一个东西，
    拿解释器当目标会弹出一个解释器而不是工具）。
    """
    t = str(target or "").strip()
    if not t:
        return []
    if getattr(sys, "frozen", False) or t.lower().endswith((".exe", ".app")):
        return [t]
    return [sys.executable, t]


def _spawn_default(cmd):
    """默认拉起方式：脱节于父进程，并且 macOS 上的 .app 必须走 `open`。

    直接 Popen 一个 bundle 路径在 POSIX 上是 ENOEXEC（那不是「没装好」，是拉法不对），
    而这一路恰好是唯一能把新工具升起来的链路，不能把「包放在那了」变成一个假的失败。
    """
    argv = [str(x) for x in (cmd or []) if str(x)]
    if not argv:
        raise OSError("没有可用的启动命令")
    if sys.platform == "darwin" and argv[0].lower().endswith(".app"):
        argv = ["/usr/bin/open", argv[0]]
    return subprocess.Popen(argv, close_fds=True, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ── Windows：交回官方安装包，并**等它跑完** ─────────────────────────
def run_installer(setup_path: str, log: Optional[Callable[[str], None]] = None,
                  timeout: float = INSTALLER_TIMEOUT_SECONDS,
                  run_fn=None) -> dict:
    """静默跑完官方安装包，等它结束并拿回退出码。返回 {ok, why, code}。

    这里刻意**等完成**而不是 Popen 一发不管：这一路的产物是用户接下来唯一能用的
    工具，「安装包被启动了」与「装好了」是两句不同的话。发完就不管的写法在旧实例
    退出与安装包开工之间留了一个没有人兜的时间差，而 NSIS 静默模式碰到目录被占时
    走的是 abort（它不能弹一个没人能点的「重试」框，这是脚本里已经写死的规矩）——
    那个时间差里的失败形状是「工具关了、什么都没装、屏幕上一个字都没有」。
    调用因此走交给交接进程（见 :func:`apply_update`）：它先等旧 PID 真消失，再跑这一步，
    时间差因此不存在，而不是靠重试碰运气。
    """
    say = log or (lambda _m: None)
    src = Path(setup_path)
    if src.suffix.lower() != ".exe":
        # 清单解析那一层已经把平台键钉到档名，这里拿到的却是 .zip 只可能是人手工传错：
        # Windows 会把它交给资源管理器，用户看到的是一场静悄悄的什都没有发生。
        return {"ok": False, "code": None,
                "why": f"这不是一个可执行的安装包（应为 .exe）：{setup_path}"}
    if not src.is_file() or src.stat().st_size <= 0:
        return {"ok": False, "code": None,
                "why": f"安装包不在了或是个空文件：{setup_path}"}
    say("ℹ 开始静默安装（就是第一次装工具那种安装包，不需要管理员密码）…")
    runner = run_fn or subprocess.run
    try:
        r = runner([str(src), "/S"], capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": None,
                "why": f"安装包跑了 {timeout / 60:.0f} 分钟还没结束，这边不等了："
                       "如果安装窗口还在，请让它跑完；否则手动双击那个安装包"}
    except OSError as e:
        return {"ok": False, "code": None, "why": f"安装包没能启动：{_why(e)}"}
    code = int(getattr(r, "returncode", 0) or 0)
    if code != 0:
        tail = (getattr(r, "stderr", b"") or b"").decode("utf-8", "replace").strip()[:200]
        return {"ok": False, "code": code,
                "why": f"安装包装到一半就停了（退出码 {code}）：{tail or '安装包没留下原因'}"
                       "——旧版本还在原地，工具仍然可以照常打开"}
    say("✅ 安装包报的成功（退出码 0）。下一步拿新产物自己报的版本再对一次。")
    return {"ok": True, "code": code, "why": ""}


# ── 装完之后：再对一次新包里的构建戳 ──────────────────────────
def stamp_candidates_for(exe_path) -> list:
    """从「正在跑的那个可执行文件」推到它同级应该躺着的构建戳（只列候选，不下结论）。"""
    exe = Path(exe_path or "")
    out = []
    if not exe.is_file():
        return out
    name = _tv.BUILD_STAMP_NAME if HAS_TOOL_VERSION else "version.json"
    base = exe.parent
    if os.name == "nt":
        # onedir：exe 同级是 _internal，--add-data 的目标 "." 就落在那里
        out.append(base / "_internal" / name)
        out.append(base / name)
    else:
        # .app：Resources 在 Contents 下，可执行在 Contents/MacOS 下
        out.append(base.parent / "Resources" / name)
        out.append(base / name)
    return out


def verify_installed_build(exe_path, expect_build: int = 0) -> dict:
    """安装/替换完之后，拿新包里的构建戳再对一次。返回 {ok, build, certain, path, why}。

    为什么不能拿「安装包退出码 0」当「装好了」：那个 0 只说「安装程序自己没报错」，
    它不说新包里的构建戳是不是那一份，也不说 `_internal` 里的数据复到位没有——而后者
    恰好是本项目付过学费的一形（--add-data 漏收不崩、只少功能）。

    这里读的是**文件而不是起一个新进程**：onedir 的布局是确定的（exe 同级 _internal），
    安装包同步写完才能返 0，所以不需要轮询；而起一个 GUI 子系统进程去拿它的 stdout 在
    Windows 上本来就拿不到（CI 那边为此用 Start-Process -Wait）。多一条取证链路就多一条
    会坏的链路，而这里要的答案就在磁盘上。真正「新包能不能跑」由下一步直接拉起新工具回答。
    """
    payload = {"ok": False, "build": 0, "certain": False, "path": "", "why": ""}
    for cand in stamp_candidates_for(exe_path):
        try:
            raw = json.loads(Path(cand).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(raw, dict):
            continue
        n = _tv.parse_version(raw.get("build")) if HAS_TOOL_VERSION else None
        payload["path"] = str(cand)
        payload["build"] = int(n or 0)
        if not n:
            payload["why"] = (f"新包里的构建戳读不出可用的构建号（{cand}）："
                              "这是「没能确认」，不是「没装上」——新工具已放在原位，可以先打开看看")
            return payload
        payload["certain"] = True
        if expect_build and n != int(expect_build):
            payload["why"] = (f"装完再对一次，新包里自报的构建号是 {n}，与清单说的 "
                              f"{expect_build} 对不上——请先手动打开工具确认界面左下角的版本号")
            return payload
        payload["ok"] = True
        return payload
    payload["why"] = ("装完之后在新工具同级找不到构建戳（version.json）："
                      "这是「没能确认版本」，不是「没装上」——新工具已放在原位，"
                      "先打开一次；若能用就不必重做，但请把这句话反馈给发布人"
                      "（随包戳漏收是打包清单的事）")
    return payload


# ── macOS：旧进程退出后自己换自己 ──────────────────────────────────────
def current_app_bundle() -> Optional[Path]:
    """正在跑的这个 .app 的根；读不出返回 None（然后就不硬替换，退回人工那一路）。"""
    try:
        exe = Path(sys.executable).resolve()
    except OSError:
        return None
    # .../B2bCollectTool.app/Contents/MacOS/B2bCollectTool → 往上三层
    for _ in range(3):
        exe = exe.parent
    return exe if exe.suffix == ".app" else None


def _wait_gone(pid: int, timeout: float, sleep: Callable[[float], None] = time.sleep,
               alive: Optional[Callable[[int], bool]] = None) -> bool:
    """等 pid 消失。返回 True=已退出；False=到点了还活着（不谎报）。"""
    if alive is None:
        def alive(p):                                    # noqa: ANN801
            if not p or p <= 0:
                return False
            try:
                os.kill(p, 0)
            except ProcessLookupError:
                return False
            except PermissionError:
                return True
            except OSError:
                return True
    deadline = time.time() + max(0.0, float(timeout))
    while alive(pid) and time.time() < deadline:
        sleep(0.2)
    return not alive(pid)


def _unpack_zip(zip_path: Path, into: Path) -> dict:
    """解 zip。macOS 用 ditto（它保权限与扩展属性，`unzip` 会把可执行位丢掉——
    解出来的 .app 不可执行是最难查的那种坏）。"""
    into.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run(["/usr/bin/ditto", "-x", "-k", str(zip_path), str(into)],
                           capture_output=True, timeout=1800)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "why": f"没能解包：{_why(e)}"}
    if r.returncode != 0:
        err = (r.stderr or b"").decode("utf-8", "replace").strip()[:200]
        return {"ok": False, "why": f"解包失败（ditto 退出码 {r.returncode}）：{err or '没留原因'}"}
    return {"ok": True, "why": ""}


def zip_app_entries(zip_path: Path) -> list:
    """列 zip 顶层的 .app（只读目录项，不解压）；不是 Mac 包的形状就返回空列表。

    `BadZipFile` 必须在这一格吃掉：它是 `Exception` 的直接子类，既不是 OSError 也不是
    ValueError。下载被打断留下的残件、被人换成任何别的文件、清单里挂了一个根本不是 zip
    的地址，都会走到这里；而调用点（`_kind_for` → `apply_update`）期望的答案是「这不像
    一个能自替换的包」，不是一个栈。崩在交接进程里等于 stdio 进黑洞——正是本模块从第一行
    起就在避免的那句「工具关了、什么都没装、屏幕上一个字都没有」。
    """
    try:
        with zipfile.ZipFile(str(zip_path)) as zf:
            names = zf.namelist()
    except (OSError, ValueError, zipfile.BadZipFile):
        return []
    tops = set()
    for n in names:
        head = n.split("/")[0]
        if head.lower().endswith(".app"):
            tops.add(head)
    return sorted(tops)


def _self_replace(package: str, say, out: dict, bundle: Optional[Path] = None,
                  unpack_fn=None, spawn_fn=None, expect_build: int = 0) -> dict:
    """macOS 那一条自替换通路：解包 → 把旧 .app 挪到旁边 → 新的放进原位置 → 拉起。

    三个接缝（unpack_fn/spawn_fn/bundle）不是为测试而测试：这一条链路在真机上「跑不了第
    二次」（换错了就把用户的工具弄没了），所以每一分支都必须能在离线判据里被单独走
    一遍，包括「解包失败」「目录不可写」「放不进去要回滚」「拉不起来」。

    包属于哪个平台以**包自己解出来的形状**为凭（zip 顶层只有一个 *.app），不拿运行这台
    机器的解释器去推：推出来的结论会错向最坏的一边——Rosetta 下的 Python 报 x86_64，
    本机推导会归到 other，于是一台本来能自动升级的机器被当成不能，只能退回人工。
    """
    src = Path(package)
    if not src.is_file() or src.stat().st_size <= 0:
        out["why"] = f"下载好的包不在了或是个空文件：{src}"
        return out
    apps = zip_app_entries(src)
    if len(apps) != 1:
        # 顶层不是一个应用包：半个包（下载被打断后留下的残件）、一个错平台的 zip、
        # 或者被人换成别的任何东西，在这一格就停住，不去解它也不碰本机。
        out["why"] = (f"这个包不像一个可用的应用包（顶层 .app 个数：{len(apps)}）：{src}，"
                      "没有替换任何东西")
        return out
    target = Path(bundle) if bundle else current_app_bundle()
    if target is None:
        # 源码运行、或 .app 位置认不出来：绝不能在这里「猜一个目录放进去」——
        # 猜错的代价是把用户没打算动的东西盖掉。留包 + 一句人工步骤才是这一路的正解。
        out["why"] = (f"没能确认这台机器上工具应用本体的位置（{sys.executable}），"
                      f"没有替换任何东西；下载好的包留在 {src}，双击解压后把 "
                      "B2bCollectTool.app 拖进「应用程序」覆盖即可")
        return out
    if not os.access(str(target.parent), os.W_OK | os.X_OK):
        out["why"] = (f"「{target.parent}」这一层不可写（可能被系统放在只读位置），"
                      f"没有替换任何东西；下载好的包留在 {src}")
        return out
    stage = src.parent / f"unpack_{int(time.time())}"
    unpack = unpack_fn or _unpack_zip
    got = unpack(src, stage)
    if not got.get("ok"):
        out["why"] = str(got.get("why") or "解包没成功")
        return out
    new_app = stage / apps[0]
    if apps[0].lower() != target.name.lower():
        # 包里的应用名与本机正在跑的那个不一样：这不是「同一个工具的上一版与下一版」，
        # 拿它盖到原位置会把用户手里那个应用变成另一个东西（名字、卸载项、登录项全不是一回事）。
        out["why"] = (f"包里的应用叫「{apps[0]}」，本机正在跑的却是「{target.name}」，"
                      "名字不一样，没有替换任何东西")
        return out
    if not (new_app / "Contents" / "MacOS").is_dir():
        out["why"] = (f"解出来的包不像一个可用的应用（少了可执行那一层）：{new_app}，"
                      "没有替换任何东西")
        return out
    aside = target.with_name(f"{target.stem}.old-{int(time.time())}.app")
    try:
        target.rename(aside)
    except OSError as e:
        out["why"] = f"旧版本挪不动（没替换任何东西）：{_why(e)}"
        return out
    try:
        subprocess.run(["/usr/bin/ditto", str(new_app), str(target)], check=True,
                       capture_output=True, timeout=1800)
    except (OSError, subprocess.SubprocessError) as e:
        # 装不回去就立刻把旧的挪回来：宁可版本不变，也不留下一个没有工具的机器。
        try:
            aside.rename(target)
        except OSError:
            say(f"⚠ 回滚也没挪回来，旧版本在旁边：{aside}")
        out["why"] = f"新版本放不进去，已退回原来的版本：{_why(e)}"
        return out
    say(f"✅ 新版本已放到：{target}")
    # 先对一次新包里的戳，再拉起：拉错了比没拉起难看得多（用户看到一个旧版本的新窗口）。
    check = verify_installed_build(target / "Contents" / "MacOS" / target.stem, expect_build)
    out["verified"] = check
    if not check.get("ok"):
        # 装不装得成与版本对不对得上是两件事：旧版本仍在旁边，所以这里不删它，
        # 并把「可以换回来」那句真话说出来。
        say("⚠ " + str(check.get("why") or "新版本已放到原位，但没能确认版本号"))
        out["aside"] = str(aside)
    launch = spawn_fn or _spawn_default
    try:
        launch(launch_command_for(str(target)))
        out["relaunched"] = True
    except (OSError, subprocess.SubprocessError) as e:
        out["why"] = (f"新版本已经装好了，但没能自动打开：{_why(e)}"
                      f"——请在「应用程序」里双击打开 {target.name}")
        out["ok"] = True
        return out
    # 旁边那份旧的只在一切就绪（版本也对上了）之后才清；清不掉不影响结论，但必须留一句话（它占几百 MB）
    if check.get("ok"):
        cleaned = False
        try:
            r = subprocess.run(["/bin/rm", "-rf", str(aside)], capture_output=True, timeout=600)
            cleaned = int(getattr(r, "returncode", 0) or 0) == 0
        except (OSError, subprocess.TimeoutExpired):
            cleaned = False
        if not cleaned:
            # 只看异常不看退出码是不够的：rm 返非 0（权限、只读卷、被安全软件拦）时旧副本
            # 还在原地占着几百 MB，而界面上一个字都没有——注释里那句「清不掉必须说话」
            # 说的就是这一格，不能只挡住崩的那一种。
            say(f"ℹ 旁边留了一份旧版本没清掉：{aside}（不影响使用，可以手工删）")
    out["ok"] = True
    return out


def apply_update(package: str, wait_pid: int = 0, expect_build: int = 0,
                 relaunch_target: str = "", log: Optional[Callable[[str], None]] = None,
                 bundle: Optional[Path] = None, timeout: float = INSTALLER_LOCK_WAIT_SECONDS,
                 wait_fn=None, unpack_fn=None, run_fn=None, spawn_fn=None) -> dict:
    """交接进程本体：等旧实例真退出 → 装上 → 再对一次版本 → 拉起新工具。

    返回 {ok, kind, why, relaunched, verified, aside}。两个平台在这里合流，但只在这里
    分叉（`_kind_for` 看包自己的形状），因为界面那边只想说一句话：「你点确定，接下来
    的事工具自己跑完」。调用点再判一次平台就会开始出现「一边测过一边没测」的缝隙。

    旧实例必须**先真退出**才动手，这件事由 `wait_fn` 等到 PID 消失为止（有界）：
    Windows 那边它占着安装目录里的镜像，macOS 那边它占着要改名的 bundle。等不到就停下
    并把「请稍后手动打开」说清，绝不拿一个「大概已经退了吧」去动正在跑的东西。
    """
    say = log or (lambda _m: None)
    out = {"ok": False, "kind": "", "why": "", "relaunched": False,
           "verified": {}, "aside": ""}
    src = Path(package)
    if not src.is_file() or src.stat().st_size <= 0:
        out["why"] = f"下载好的包不在了或是个空文件：{src}"
        return out
    kind = _kind_for(src)
    out["kind"] = kind
    if kind == "none":
        # 文件名与内容形状都不是本模块认得的两种包（.zip 顶层没有 .app，也不是 .exe）：
        # 这一格不能「那就当安装包跑一下看看」，拿一个认不出的东西去动用户的系统是最坏的一形。
        out["why"] = (f"这个包不是本模块能自动安装的形状（{src.name}）："
                      "没有动任何东西；可以在下载目录里双击它，或重开工具再提一次")
        return out
    if wait_pid:
        wait = wait_fn or _wait_gone
        if not wait(int(wait_pid), timeout):
            out["why"] = (f"等了 {timeout:.0f} 秒，上一个实例（pid {wait_pid}）还没退出，"
                          "没有去替换正在运行的东西。请关掉工具窗口后再手动跑一次升级")
            return out
    if kind == "installer":
        return _apply_with_installer(src, out, say, expect_build,
                                     relaunch_target, run_fn=run_fn, spawn_fn=spawn_fn)
    got = _self_replace(str(src), say, out, bundle=bundle, unpack_fn=unpack_fn,
                        spawn_fn=spawn_fn, expect_build=expect_build)
    return got


def _kind_for(src: Path) -> str:
    """这个包该走哪条安装路：看**包自己的形状**，不看现在是谁在问。"""
    if src.suffix.lower() == ".exe":
        return "installer"
    if len(zip_app_entries(src)) == 1:
        return "self_replace"
    return "none"


def _apply_with_installer(src: Path, out: dict, say, expect_build: int,
                          relaunch_target: str, run_fn=None, spawn_fn=None) -> dict:
    """Windows 那一条：等旧进程退出（上层已经做完）→ 跑完官方安装包 → 对版本 → 拉起。"""
    got = run_installer(str(src), log=say, run_fn=run_fn)
    if not got.get("ok"):
        out["why"] = str(got.get("why") or "安装没成功")
        return out
    exe = Path(relaunch_target or sys.executable)
    check = verify_installed_build(exe, expect_build)
    out["verified"] = check
    if not check.get("ok"):
        # 版本对不上不拦拉起（新版工具仍然该能用），但那句话必须跟在拉起之前说，
        # 不然窗口一开，没人会再读日志。
        say("⚠ " + str(check.get("why") or "没能确认新包的版本号"))
    say("ℹ 装好了，正在重新打开工具…")
    cmd = launch_command_for(str(exe))
    launch = spawn_fn or _spawn_default
    try:
        launch(cmd)
        out["relaunched"] = True
        out["ok"] = True
    except OSError as e:
        out["ok"] = True   # 装这一步确实成功了，坏的只是「顺手打开」
        out["why"] = (f"新版本已经装好了，但没能自动打开：{_why(e)}"
                      f"——双击桌面上的图标或 {exe.name} 就是新版本")
    return out


# ── 交接报告：stdout 进黑洞，成败都要留下一句下次开窗能读到的话 ────────
REPORT_NAME = "last_update.json"
# 只回播「这一次升级」而不是「磁盘上那份旧报告」：间隔太久的一句「上周升级没成功」
# 只会让人去查一个现在根本不存在的问题。上界取半天（从点确定到重开工具跳不过它）。
REPORT_MAX_AGE_SECONDS = 6 * 3600.0


def report_path() -> Path:
    return staging_dir() / REPORT_NAME


def write_report(res: dict, log: Optional[Callable[[str], None]] = None,
                 dest=None) -> dict:
    """把交接这一次的结果落到包外文件。返回 {ok, path, why}。

    为什么必须有这一份：交接进程是被 detach 起来的（stdio 全进 DEVNULL），它说的一切
    都落在没人看得到的地方；而这一刻界面上那个窗口**已经按「马上就有新版本」关掉了**。
    没有这份报告，失败形状就是「工具关了、什么都没装、屏幕上一个字都没有」——正是本模块
    从第一行起就在避免的那一句。报告本身写不下去只说一句，不改结论（它是取证，不是
    成功条件）。
    """
    say = log or (lambda _m: None)
    verified = res.get("verified") or {}
    payload = {
        "ts": time.time(),
        "ok": bool(res.get("ok")),
        "kind": str(res.get("kind") or ""),
        "why": str(res.get("why") or ""),
        "relaunched": bool(res.get("relaunched")),
        "fell_back": bool(res.get("fell_back")),
        "expect_build": int(res.get("expect_build") or 0),
        "installed_build": int(verified.get("build") or 0),
        "verified": bool(verified.get("ok")),
        "aside": str(res.get("aside") or ""),
        "handoff_pid": os.getpid(),
    }
    path = Path(dest) if dest else report_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    except OSError as e:
        say("⚠ 这次升级的结果没能落到报告里：" + _why(e))
        return {"ok": False, "path": str(path), "why": _why(e)}
    return {"ok": True, "path": str(path), "why": ""}


def read_report(path=None, now: Optional[float] = None,
                max_age: float = REPORT_MAX_AGE_SECONDS) -> dict:
    """读回上一次交接的报告；没有/读坏/太老都返回空字典（界面因此闭嘴，不编一句）。"""
    p = Path(path) if path else report_path()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    try:
        ts = float(raw.get("ts") or 0)
    except (TypeError, ValueError):
        return {}
    age = (time.time() if now is None else float(now)) - ts
    if ts <= 0 or age < 0 or age > max_age:
        return {}
    return raw


def run_handoff(package: str, wait_pid: int = 0, expect_build: int = 0,
                relaunch_target: str = "", log: Optional[Callable[[str], None]] = None,
                bundle=None, timeout: float = INSTALLER_LOCK_WAIT_SECONDS,
                wait_fn=None, unpack_fn=None, run_fn=None, spawn_fn=None,
                report_dest=None) -> dict:
    """交接的完整一步：装 → 没拉起来就把**原来那个**拉起来 → 落报告。

    界面的「点确定之后全自动」落到进程层面就是这里，CLI 走同一个入口（见 :func:`main`）：
    两处各写一份 choreography 的话，线上永远只会跑到其中一份。

    最后那一步是本函数存在的理由：升级失败时屏幕上不能只剩「工具不见了」。旧版本在两个
    平台上都还在磁盘上（Windows 那边安装包失败不会把一个没跑完的批次摆到位，macOS 那边
    放不进去就当场把旧的挪回来），所以「把旧的重新开起来 + 下次开窗读报告说话」是可达的
    承诺，不是一句安慰。这里**不看 ok**：ok=True 却「没能自动打开」的那些分支已经给了
    人工步骤，但人至少得先有工具可用。
    """
    say = log or (lambda _m: None)
    res = apply_update(package, wait_pid=wait_pid, expect_build=expect_build,
                       relaunch_target=relaunch_target, log=say, bundle=bundle,
                       timeout=timeout, wait_fn=wait_fn, unpack_fn=unpack_fn,
                       run_fn=run_fn, spawn_fn=spawn_fn)
    res["expect_build"] = int(expect_build or 0)
    # 报告先落一次，再尝试把旧的拉起来：反过来的话，新实例可能比报告早一步读到旧内容
    # （两个进程同时开头，取证不能假定为「一定比拉人慢」）。
    res["report"] = write_report(res, log=say, dest=report_dest)
    if not res.get("relaunched") and relaunch_target:
        launch = spawn_fn or _spawn_default
        try:
            launch(launch_command_for(relaunch_target))
            res["fell_back"] = True
            say("ℹ 先把原来的版本重新打开了；上面那句就是这次升级没换成的原因。")
        except (OSError, subprocess.SubprocessError) as e:
            say("⚠ 连原来的版本也没能自动打开，请手动双击工具图标：" + _why(e))
        # 把 fell_back 这一格补进同一份报告（重写一次的成本换来的是事实完整）
        res["report"] = write_report(res, log=say, dest=report_dest)
    return res


# ── 免打扰：按版本号记，不按时间记 ─────────────────────────────────────
def dismissed_version(prefs) -> str:
    """从界面偏好里读出「这一版已经说了不要提醒」的那个版本号。"""
    if not isinstance(prefs, dict):
        return ""
    return str(prefs.get(SNOOZE_KEY) or "").strip()


def should_prompt(eval_res: dict, prefs) -> bool:
    """该不该弹这一次提示（界面只问这一句，不许自己再判一遍）。

    只有 update-available 才可能弹；已弹过同一版本就安静；up-to-date/unknown 都不该
    拿一个框去打扰人——「没能确认」不是「有新版本」，这条与 Chrome 那边同规矩。
    """
    if not isinstance(eval_res, dict) or eval_res.get("state") != "update-available":
        return False
    latest = str(eval_res.get("latest") or "").strip()
    if not latest:
        return False
    return dismissed_version(prefs) != latest


def with_dismissed(latest: str, prefs) -> dict:
    """用户点了「这版先不提示」之后该写回的那份偏好（纯函数，落盘交给界面）。"""
    out = dict(prefs) if isinstance(prefs, dict) else {}
    out[SNOOZE_KEY] = str(latest or "").strip()
    return out


# ── 指纹工具（CI 生成清单与本模块校验共用同一份实现）────────────────────
def sha256_file(path) -> str:
    """算文件指纹；读不动返回空串（调用方按「校验不了」处理，不当成「一致」）。"""
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as fh:
            for chunk in iter(lambda: fh.read(256 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def parse_argv(argv) -> dict:
    """命令行解析（纯函数，好单独测）：只认本模块那几个开关，其余原样留给界面。

    这四个开关都是交接那一步的**必需输入**，少一个就只能得到一句更差的话，所以在这里
    一次收齐、由 :func:`main` 原样递给 :func:`apply_update`（不在这一步替调用方兜默认值，
    那会把「界面忘了传」变成「看起来正常」）：
      --apply-update <包>    要装的东西；没有它这一路根本不该开始
      --wait-pid <进程号>    要等谁退出；没有它就会在旧实例还占着目录时动手
      --expect-build <n>     装完要对上的构建号；没有它就只剩「安装包说自己成功了」
      --relaunch <路径>      装完拉起谁；源码态缺了它只会去启动解释器本身
    """
    argv = [str(a) for a in (argv or [])]
    out = {"apply": "", "wait_pid": 0, "expect_build": 0, "relaunch": "",
           "requested": False}
    for i, a in enumerate(argv):
        if a == APPLY_FLAG:
            out["requested"] = True
            if i + 1 < len(argv):
                out["apply"] = argv[i + 1]
        elif a == WAIT_PID_FLAG and i + 1 < len(argv):
            out["wait_pid"] = _positive(argv[i + 1])
        elif a == EXPECT_BUILD_FLAG and i + 1 < len(argv):
            out["expect_build"] = _positive(argv[i + 1])
        elif a == RELAUNCH_FLAG and i + 1 < len(argv):
            out["relaunch"] = argv[i + 1].strip()
    return out


def main(argv=None) -> int:
    """CLI：`--check` / `--check-json` / `--download` / `--apply-update <包> [交接参数]`。

    交接参数见 :func:`parse_argv`；界面那一条路（GUI 的 `main()` 在 QApplication 之前分派）
    用的是同一份解析与同一个 `apply_update`，所以「用户点确定之后发生的事」在命令行上
    可以被逐字复现——两处各实现一份交接是最大的风险来源，因为线上只会跑到其中一份。

    退出码三档，与「有没有新」而不是「跑没跑成」对齐，好让脚本与人都能照它分派：
      0  已是最新 / 动作成功
      10 有可装的新版本
      20 问不出结论（没网、清单坏了、本机版本未能确认、这台机器不适配）
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        print("用法：tool_update.py [--check | --check-json | --download]")
        print(f"      tool_update.py {APPLY_FLAG} <包路径> [{WAIT_PID_FLAG} <旧进程号>] "
              f"[{EXPECT_BUILD_FLAG} <构建号>] [{RELAUNCH_FLAG} <装完拉起谁>]")
        return 0
    hand = parse_argv(argv)
    if hand["requested"]:
        if not hand["apply"]:
            print(f"❌ {APPLY_FLAG} 后面要跟一个下载好的包路径")
            return 20
        res = run_handoff(hand["apply"], wait_pid=hand["wait_pid"],
                          expect_build=hand["expect_build"],
                          relaunch_target=hand["relaunch"],
                          log=lambda m: print(m))
        print(("✅ " if res["ok"] else "❌ ") + (res["why"] or "已完成"))
        return 0 if res["ok"] else 20
    res = check(log=(lambda m: print(m)) if argv[0] != "--check-json" else None)
    if argv[0] == "--download":
        if res.get("state") != "update-available":
            print("ℹ 没有可以下载的更新：" + (res.get("why") or res.get("state", "")))
            return 0 if res.get("state") == "up-to-date" else 20
        got = download(res.get("artifact") or {}, log=lambda m: print(m))
        print(("✅ 已下载并校验：" if got["ok"] else "❌ ") + (got.get("path") or got["why"]))
        return 0 if got["ok"] else 20
    if argv[0] == "--check-json":
        print(json.dumps({k: v for k, v in res.items() if k != "artifacts"},
                         ensure_ascii=False))
    else:
        print(f"本机：{res.get('current') or '（没能确认）'}")
        print(f"最新：{res.get('latest') or '（没拿到）'}")
        if res.get("state") == "update-available":
            print("说明：\n" + notes_view(res))
            print("可走的下一步：tool_update.py --download")
        elif res.get("why"):
            print("ℹ " + res["why"])
    return {"up-to-date": 0, "update-available": 10}.get(res.get("state"), 20)


if __name__ == "__main__":
    sys.exit(main())
