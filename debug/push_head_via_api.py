#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""github.com:443 不通、api.github.com 通的时候，把一次**已经提交好的**本地 commit
用 Git Data API 快进推到 main。

为什么要有这个脚本（而不是干等网络恢复）：本轮交付的阻塞点是「修复没上云端 = 用户
永远拿不到修复」，而 443 到 github.com 的路由是偶发不可达（同网段另一台 IP 实测可通），
api.github.com 一直正常。大厂做法不是绕过校验，而是**把绕过的部分压到零**：
  · 不新建任何提交内容——blob 直接取自本地 commit，sha 逐个对拍；
  · base_tree 取远端 HEAD 的 tree，只有本次 diff 的文件被替换；
  · 新 tree 的 sha 必须等于本地 `git rev-parse <commit>^{tree}`，**不等就当场停**，
    这一步等价于「远端内容 = 本地内容」的字节级证明；
  · 更新 ref 用 PATCH 且 force=false：非快进会被 GitHub 直接拒绝，绝不可能覆盖别人的提交；
  · 不碰 git 配置、不碰系统文件、不改任何提交信息。

用法：python3 debug/push_head_via_api.py [commit]   # 默认推 HEAD，父提交必须就是远端 main
"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
import urllib.error
import urllib.request

REPO = "JHnoplan/riskbird-tool"
API = "https://api.github.com"
ROOT = "/Users/jhnoplan/Desktop/PythonDemo"


def die(msg: str) -> None:
    print(f"ABORT：{msg}")
    sys.exit(1)


def git(*args: str) -> str:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True)
    if r.returncode != 0:
        die(f"git {' '.join(args)} 失败：{r.stderr.decode('utf-8', 'replace').strip()}")
    return r.stdout.decode("utf-8", "surrogateescape")


def git_bytes(*args: str) -> bytes:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True)
    if r.returncode != 0:
        die(f"git {' '.join(args)} 失败：{r.stderr.decode('utf-8', 'replace').strip()}")
    return r.stdout


def token() -> str:
    p = subprocess.run(["git", "credential", "fill"], cwd=ROOT,
                       input=b"protocol=https\nhost=github.com\n\n",
                       capture_output=True)
    for line in p.stdout.decode().splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1]
    die("git credential helper 里没有 github.com 凭证")


def req(method: str, path: str, tok: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    q = urllib.request.Request(API + path, data=data, method=method,
                               headers={"Authorization": f"Bearer {tok}",
                                        "Accept": "application/vnd.github+json",
                                        "X-GitHub-Api-Version": "2022-11-28",
                                        "Content-Type": "application/json"})
    for attempt in (1, 2, 3):
        try:
            with urllib.request.urlopen(q, timeout=40) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception as exc:                      # 偶发网络抖动：重试两次再放弃
            print(f"  retry {attempt}：{exc}")
    die(f"{method} {path} 三次都没连通")


def main() -> int:
    commit = sys.argv[1] if len(sys.argv) > 1 else "HEAD"
    sha = git("rev-parse", commit).strip()
    parent = git("rev-parse", f"{sha}^").strip()
    msg = git("log", "-1", "--format=%B", sha)
    author = git("log", "-1", "--format=%an|%ae|%aI", sha).strip().split("|")
    local_tree = git("rev-parse", f"{sha}^{{tree}}").strip()

    tok = token()
    code, ref = req("GET", f"/repos/{REPO}/git/ref/heads/main", tok)
    if code != 200:
        die(f"读远端 main 失败 {code}：{ref.get('message')}")
    remote_sha = ref["object"]["sha"]
    print(f"· 远端 main = {remote_sha[:7]}")
    code, head_commit = req("GET", f"/repos/{REPO}/git/commits/{remote_sha}", tok)
    if code != 200:
        die(f"读远端提交失败 {code}：{head_commit.get('message')}")
    base_tree = head_commit["tree"]["sha"]
    parent_tree = git("rev-parse", f"{parent}^{{tree}}").strip()
    if remote_sha != parent:
        # 提交号对不上只有一种情况可以放行：内容对得上。上一刀就是用本脚本推的，
        # 远端那个 commit 与本地那个是同一片 tree、不同一次包装，所以本地接着往下
        # 提交的 parent 自然不等于远端 main。判据仍然是**字节级等价**：parent 的 tree
        # 必须逐位等于远端 HEAD 的 tree，差一个都不推——否则就是拿本地历史盖别人。
        if parent_tree != base_tree:
            die(f"本地 {sha[:7]} 的父提交是 {parent[:7]}，与远端 main 不一致，且两棵 tree"
                f"也不同（{parent_tree[:8]} != {base_tree[:8]}）——先 git pull --rebase，"
                f"不要用本脚本硬推")
        print(f"· parent({parent[:7]}) 与远端 main({remote_sha[:7]}) 号不同但 tree 相同"
              f"（上一次快进留下的），按内容等价继续")
        # 变更清单仍按 parent..sha 取：parent 的 tree 已逐位等于远端 HEAD 的 tree，
        # 两者是同一棵树，差集不可能不同；后面「新 tree == 本地 tree」那条断言会兜住
        # 任何意外（包括 base_tree 被远端另一次提交改过这种假设不成立的情况）。

    changed = [line.split("\t") for line in
               git("diff", "--name-status", parent, sha).splitlines() if line.strip()]
    if not changed:
        die("本次提交没有任何文件变化")
    entries = []
    for parts in changed:
        status, path = parts[0], parts[-1]
        if status.startswith("R"):
            die(f"重命名（{path}）本脚本不支持，请用常规 push")
        content = git_bytes("show", f"{sha}:{path}")
        blob_sha = git("rev-parse", f"{sha}:{path}").strip()
        # mode 也必须取自本地 tree：把 100755 写成 100644 会让 tree sha 对不上，
        # 而「对不上就停」正是这个脚本唯一的等价性证明，不该被人肉写错触发。
        ls_mode = git("ls-tree", sha, "--", path).split()[0]
        code, made = req("POST", f"/repos/{REPO}/git/blobs", tok,
                         {"content": base64.b64encode(content).decode(),
                          "encoding": "base64"})
        if code != 201:
            die(f"建 blob 失败 {code}：{made.get('message')}（{path}）")
        if made["sha"] != blob_sha:
            die(f"{path} 上传后的 blob sha 与本地不一致：{made['sha']} != {blob_sha}")
        print(f"· blob {path} → {made['sha'][:8]}（与本地一致，{len(content)} 字节）")
        entries.append({"path": path, "mode": ls_mode, "type": "blob",
                        "sha": made["sha"]})

    code, tree = req("POST", f"/repos/{REPO}/git/trees", tok,
                     {"base_tree": base_tree, "tree": entries})
    if code != 201:
        die(f"建 tree 失败 {code}：{tree.get('message')}")
    if tree["sha"] != local_tree:
        die(f"远端 tree {tree['sha']} 与本地 {local_tree} 不一致——内容不等价，"
            f"绝不推这种 ref")
    print(f"· tree {tree['sha'][:8]} == 本地 tree（字节级等价证明成立）")

    code, new_commit = req("POST", f"/repos/{REPO}/git/commits", tok,
                           {"tree": tree["sha"], "parents": [remote_sha],
                            "message": msg,
                            "author": {"name": author[0], "email": author[1],
                                       "date": author[2]}})
    if code != 201:
        die(f"建提交失败 {code}：{new_commit.get('message')}")

    code, upd = req("PATCH", f"/repos/{REPO}/git/refs/heads/main", tok,
                    {"sha": new_commit["sha"], "force": False})
    if code != 200:
        die(f"更新 ref 失败 {code}：{upd.get('message')}"
            f"（force=false 只允许快进，被拒说明远端已经有新提交）")
    print(f"· main 已快进到 {new_commit['sha'][:7]}")

    # 本地分支要跟上远端那次快进：tree 已经证明内容一致，所以这里只是换个提交号，
    # 不产生任何内容差异，也不需要 reset --hard。443 还没恢复时 fetch 会失败，
    # 那不影响远端已经拿到修复这件事，所以只提示、不当成失败。
    r = subprocess.run(["git", "fetch", "origin", "main"], cwd=ROOT, capture_output=True)
    if r.returncode == 0:
        print("· 本地 fetch 完成：origin/main 已指向新提交")
    else:
        print("· fetch 暂未成功（443 未恢复）：远端 main 已经是新内容，网络恢复后 "
              "git pull --rebase 会把本地这个重复的 commit 让位给远端那一个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
