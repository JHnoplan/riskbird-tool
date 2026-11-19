# -*- coding: utf-8 -*-
"""download_verify.py —— 从 https 取一个文件回来，并说清「到底取到了什么」。

这个模块为什么存在
------------------
工具里有两件事都要「从网上拿一个文件下来再用」：① 本机没有 Chrome 时替用户装
（需求 3），② 有新版本时把新包取下来自举替换（需求 2）。两条路对"取回来的东西"的
要求一模一样：**要么完整且是自己声明的那个文件，要么干脆没有**。中间态（下到一半、
被代理改写成一张登录页、磁盘满只写了一半）如果被当成成功往下走，代价是拿一个坏包去
覆盖用户正在用的工具，或者运行一个来源不明的 exe。

所以这里把四条纪律钉在一个地方，两条业务路共用：

1. **只收 https**。http 上的 .exe 任何人都能在路由中间换掉。这一层不做「内网调试
   时通融一下」：能通融的判据就等于没有判据。
2. **先写 .part，落齐了才改名**。断在第 60% 时磁盘上不会留下一个「看起来已经下完」
   的 exe——文件名的存在性在很多调用点就是"可用"的判据，不能让它说谎。
3. **边下边算 sha256**。给了期望值就必须对上；对不上是**删除**而不是报告完留着。
   校验失败的原件留在盘上，下一步就有人会顺手把它当"已经下好了"用。
4. **失败原因必须是那句能行动的话**。同一个「下载失败」背后可能是没网、DNS 被污染、
   代理要认证、对方 403、磁盘满、用户按了取消——六种成因给的是六种不同处置。这一层
   把异常种类原样带出来（附 HTTP 状态码与已写字节数），上层只负责转述，不负责猜。

代理这件事这里**故意**与本机 CDP 那条路相反：`shared_chrome.local_session()` 必须
绕开系统代理（回环控制面走代理是纯粹的错误），而下载 Chrome/新包走的是公网，公司机
器上「只有代理能出网」是常态，所以这里保留 ``trust_env=True``。两处口径相反不是飘
移，是两件事本身相反——注释写在这里，免得后来人"顺手统一"把其中一个弄坏。
"""
from __future__ import annotations

import errno
import hashlib
import os
import re
from pathlib import Path
from typing import Callable, Optional

import requests

# 单次下载的硬上限：Chrome 官方安装包 ~1.1 MB（引导器）、企业版 MSI ~110 MB、
# Mac 的 dmg ~200 MB，本工具的新包 zip ~120 MB。上限按最大那个再留一倍余量：
# 超过它就不是「文件大」，而是对端在往我们这里灌东西（或被中间设备换成了视频页）。
MAX_BYTES_DEFAULT = 400 * 1024 * 1024
CHUNK = 256 * 1024
PROGRESS_EVERY = 4 * 1024 * 1024      # 每 4 MB 报一次：够及时，又不会把日志刷满

CancelFn = Callable[[], bool]

# 一句人话映射表：把异常种类转成「用户/管理员下一步能做什么」。
# 这里刻意不写「请检查网络」这种万金油——它对所有成因都成立，因此对任何成因都没用。
_HINTS = {
    "proxy": "这台机器的代理需要认证或不允许访问该地址",
    "403": "对方拒绝提供这个文件（链接可能已过期）",
    "404": "对方没有这个文件（链接可能已过期或文件名写错）",
    "ssl": "加密连接建立失败（常见于公司网关拦截或系统时间不对）",
    "timeout": "连接超时：网络不通、太慢，或对端被网关截断",
    "conn": "连不上对方：没有网络、DNS 解析失败，或该域名在此网络内不可达",
    "enospc": "磁盘剩余空间不够，写不下去了",
    "size": "对端给的文件超出预期大小，已中止（不能拿一个来路不明的超大文件继续）",
    "hash": "文件内容与发布方声明的校验值不一致，已删除这份下载结果",
    "cancel": "按你的操作取消了，临时文件已清掉",
}


def _why(exc: BaseException) -> str:
    """把异常翻成一句带成因的话：宁可长，也不许糊成「失败」两个字。"""
    text = f"{type(exc).__name__}: {exc}"
    low = text.lower()
    for key, hint in _HINTS.items():
        # 状态码用边界匹配：否则 "404" 会撞上 "0.404s" 这种无关数字
        if key.isdigit():
            if re.search(r"(?<!\d)" + key + r"(?!\d)", text):
                return f"{hint}（HTTP {key}）｜原始：{text[:160]}"
            continue
        if key in low:
            return f"{hint}｜原始：{text[:160]}"
    return text[:200]


def _status_hint(code) -> str:
    """把 HTTP 状态码翻成能行动的一句话；没见过的码至少把码本身留住。"""
    try:
        key = str(int(code))
    except (TypeError, ValueError):
        return f"对方没把文件给出来（状态码（{code}）本身读不懂）"
    if key in _HINTS:
        return f"{_HINTS[key]}（HTTP {key}）"
    return f"对方没把文件给出来（HTTP {key}）"


def human_size(n: float) -> str:
    """字节数说成 MB 给人看（日志里 118239232 这种数字没人读得出量级）。"""
    return f"{n / 1024 / 1024:.1f} MB" if n >= 1024 * 1024 else f"{int(n)} KB"


def fetch(url: str, dest, log: Optional[Callable[[str], None]] = None,
          expected_sha256: str = "", max_bytes: int = MAX_BYTES_DEFAULT,
          timeout: tuple = (15, 60), cancel: Optional[CancelFn] = None,
          session=None) -> dict:
    """把 ``url`` 下到 ``dest``，返回 {ok, path, bytes, sha256, why}。

    ``expected_sha256`` 为空时**不做校验**（此时 ``sha256`` 仍然算出来并返回：调用方
    可以把它写进日志，远程排障时"用户机器上那个文件的指纹"是唯一能对齐两份包的证据）。
    ``cancel`` 每轮检查一次：界面层的「取消」必须真的能停，不能只会话说一半。
    """
    say = log or (lambda _m: None)
    dest = Path(dest)
    tmp = dest.with_name(dest.name + ".part")
    out = {"ok": False, "path": "", "bytes": 0, "sha256": "", "why": ""}

    if not str(url).lower().startswith("https://"):
        # 这一条不许被"我自己知道这个 http 是安全的"说服：判据一开先例，下次就是
        # 「大概是安全的」，而 http 上的 exe 是任何人都能在路由中间换掉的。
        out["why"] = f"只允许 https 下载（当前是 {url[:80]}）"
        return out
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        out["why"] = f"落点目录建不出来：{dest.parent}｜{_why(e)}"
        return out

    own = session is None
    sess = session or requests.Session()
    sha = hashlib.sha256()
    written = 0
    next_mark = PROGRESS_EVERY
    resp = None
    try:
        # 不关 trust_env：这一条走的是公网，公司机器上「只有代理出得去」是常态
        resp = sess.get(url, stream=True, timeout=timeout, allow_redirects=True)
        if resp.status_code != 200:
            # 不写「请检查网络」：403 / 404 / 5xx 背后是三种不同处置（换链接、查文件名、
            # 等对端恢复），那一句万金油对所有成因都成立，因此对任何成因都没用
            out["why"] = f"{_status_hint(resp.status_code)}｜{url[:100]}"
            return out
        declared = 0
        try:
            declared = int(resp.headers.get("Content-Length") or 0)
        except ValueError:
            declared = 0
        if declared and declared > max_bytes:
            out["why"] = (f"声明大小 {human_size(declared)} 超过允许的上限 "
                          f"{human_size(max_bytes)}，没有开始下载")
            return out
        say(f"正在下载 {url.rsplit('/', 1)[-1]}（{human_size(declared) if declared else '大小未知'}）…")
        with tmp.open("wb") as fh:
            for chunk in resp.iter_content(chunk_size=CHUNK):
                if cancel is not None and cancel():
                    out["why"] = _HINTS["cancel"]
                    return out
                if not chunk:
                    continue
                written += len(chunk)
                if written > max_bytes:
                    out["why"] = _HINTS["size"] + f"（已写 {human_size(written)}）"
                    return out
                fh.write(chunk)
                sha.update(chunk)
                if written >= next_mark:
                    say(f"   … 已下载 {human_size(written)}")
                    next_mark += PROGRESS_EVERY
        if not written:
            out["why"] = "对方给的是一个空文件，没有采用"
            return out
        digest = sha.hexdigest()
        if declared and written != declared:
            # Content-Length 与实际字节不符：连接被中途掐断（网关、代理、对端重启）
            out["why"] = (f"下载不完整：应有 {human_size(declared)}，实到 "
                          f"{human_size(written)}")
            return out
        if expected_sha256:
            want = str(expected_sha256).strip().lower()
            if digest != want:
                out["why"] = (f"{_HINTS['hash']}（期望 {want[:16]}…，实际 {digest[:16]}…）")
                return out
        tmp.replace(dest)          # 原子换名：这一刻之前 dest 不存在，之后就是完整的
        out.update(ok=True, path=str(dest), bytes=written, sha256=digest)
        say(f"✅ 下载完成：{human_size(written)}（sha256 {digest[:16]}…）")
        return out
    except (requests.RequestException, OSError, ValueError) as e:
        # 磁盘满（ENOSPC）单独点名：它是「这台机器放不下新包」，与其他所有成因的
        # 处置都不同——清空间是用户自己的事，说成「网络问题」会让人白折腾半天。
        # 用 `errno` 模块而不是 `os.errno`：后者自 3.10 起就不再公开导出，3.13 上
        # `os.errno` 直接 AttributeError——一个「磁盘满」的分支自己把整次下载抛成未接住的
        # 异常，正是本模块要消除的那一类「说了句没人能行动的话」。
        eno = getattr(e, "errno", None)
        if eno == errno.ENOSPC:
            out["why"] = f"{_HINTS['enospc']}｜目标位置 {dest.parent}"
        else:
            out["why"] = _why(e)
        return out
    finally:
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass
        if own:
            try:
                sess.close()
            except Exception:
                pass
        if not out["ok"]:
            # 失败就一定不留临时文件：留在盘上的 .part 既没人会清（下次同名覆盖算运气好），
            # 又会让人以为「下到一半，再点一次就能续上」——本实现不支持续传，别留错觉。
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass


def sha256_of(path, log: Optional[Callable[[str], None]] = None) -> str:
    """算一个已存在文件的 sha256（读不动就返回空串，调用方按「校验不了」处理）。"""
    sha = hashlib.sha256()
    try:
        with Path(path).open("rb") as fh:
            for chunk in iter(lambda: fh.read(CHUNK), b""):
                sha.update(chunk)
    except OSError as e:
        (log or (lambda _m: None))(f"⚠ 读不出文件指纹：{path}｜{_why(e)}")
        return ""
    return sha.hexdigest()
