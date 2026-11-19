#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_release_manifest.py —— 发布那一头：生成 `latest_<平台>.json` 清单（需求 2 的最后一块）。

工具那头（`b2bcollect_build/tool_update.py`）只会**读**清单并照它决定「有没有新版、能不能
自动装、装之前说哪几步」。这一头负责把那份清单**写出来**，而且必须与读的那头共用同一份
口径——两头各拼一次字符串，就是两个真源，症状是「CI 绿着发出去，客户那边永远读不出结论」。

三条硬规矩（每条都在 `b2bcollect_build/test_tool_update_offline.py` 的 ⑰ 组里有判据）：

1. **字段名、平台键、档名、直链、schema 全部从 tool_update 取**，本文件不写任何一份字面量。
   写完立刻用 `tool_update.normalize_manifest()` 回读一遍自己：自己生成的清单被自己
   的读侧拒收，就在这一步红掉，而不是等用户机器上来读。
2. **指纹与字节数由真制品算**（`tool_update.sha256_file` + `os.path.getsize`），不让人抄进
   命令行。手抄的指纹对不上制品，用户那边就是「下载完成却校验失败」，而这是最像"网络坏了"
   的一类事故。
3. **更新说明必须是人写的那一份**。`--draft-from-git` 只产出一段草稿给管理员过目
   （按提交记录归纳，标注来源），它**不能**直接发布：界面上出现的每一句「本次更新了什么」
   都必须是有人对它负责的那一句。这也是主流做法的共同点——发布说明由人审核后发出，
   工具负责起草与校验，不负责代言。

版本号只有一个来源（需求 2 第一块）：`--build` 缺省取 `GITHUB_RUN_NUMBER`，导出显示版
`1.<build>` 走 `tool_version.version_text`，与随包构建戳、NSIS 文件属性同源。构建号为 0
（没注入）时直接拒绝发布：一沓「1.0」的清单从此再也提不到更新，是最难发现的一形。

用法（CI 里在上传制品之前跑）：
    python .github/scripts/make_release_manifest.py --platform win \
        --artifact dist/B2bCollectToolSetup.exe \
        --artifact dist/B2bCollectTool_Win_portable.zip \
        --notes release_notes.md --out dist/
    python .github/scripts/make_release_manifest.py --platform mac_arm64 \
        --artifact dist/B2bCollectTool_Mac_arm64.zip \
        --notes release_notes.md --out dist/

起草（不发布，只给管理员看）：
    python .github/scripts/make_release_manifest.py --platform win --draft-from-git 20
"""
import os
import re
import sys
import json
import argparse
import subprocess
from datetime import datetime, timezone
from pathlib import Path

BUILD_DIR = Path(__file__).resolve().parent.parent.parent / "b2bcollect_build"
if str(BUILD_DIR) not in sys.path:
    sys.path.insert(0, str(BUILD_DIR))

import tool_update as tu                                            # noqa: E402
import tool_version as tv                                           # noqa: E402

# 编码必须在**第一次往控制台写中文之前**定好：Windows runner 的英文镜像是 cp1252，
# 写不下的那个字会当场抛 UnicodeEncodeError，而这条脚本的退出码本来是用来告诉发布人
# 「清单为什么不能发」的——异常一抛，结论被盖掉，红的看起来像脚本自己坏了。放在模块级
# 而不是 main() 里：`_die()` 这类函数在 argparse 之前就可能被调到，而且 import 期写日志
# 也是同一条路径（交付链其余脚本都是这个写法，第 12 条硬门按行号判的就是这件事）。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _die(msg: str) -> int:
    print(f"❌ {msg}")
    return 1


def current_build(raw) -> int:
    """构建号：命令行给了就得是个数；没给才取 CI 的 run_number；再没有就 0（=未能确认，不许发布）。

    只问 `tool_version.parse_version` 那一个口径（不拿 `_clean_build` 这种内部函数）：
    「一个字符串能不能算成一个构建号」这件事在仓库里只能有一份答案，否则发布那头
    认下来的号与工具那头比出来的号迟早对不上。

    还有一条顺序上的条件：**显式给了但不像个版本号时，不得静默退回 CI 计数器**。
    那等于人点名要发 300、发出去却是 317——两个数各自都能凑出一份自洽的清单，没有人会
    发现客户端实际拿的是哪一版。这一格与 `parse_version` 对「认不出」返回 None
    （而不是 0）是同一个纪律：错格式要红，不能降级折成另一个数。
    """
    if str(raw or "").strip():
        n = tv.parse_version(raw)
        return int(n) if n else tv.UNKNOWN_BUILD
    n = tv.parse_version(os.environ.get("GITHUB_RUN_NUMBER", ""))
    return int(n) if n else tv.UNKNOWN_BUILD


def git_subjects(count: int) -> list:
    """最近 N 条提交的标题（起草用的原始材料）。git 不可用就返回空列表，由调用方说清。"""
    try:
        r = subprocess.run(["git", "log", f"-{int(count)}", "--pretty=%s"],
                           capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    if r.returncode != 0:
        return []
    out = (r.stdout or b"").decode("utf-8", "replace")
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def draft_text(platform: str, build: int, subjects: list) -> str:
    """给管理员过目的草稿：只归纳提交标题，不替谁下结论，也不写「我们修好了什么」。

    刻意保留两个提醒：①这份草稿**不是**发布说明，没人过目就不许发；②提交标题里混着
    内部术语与 [skip ci] 这类对用户无意义的东西，草稿的作用是把它们摊开让人删。
    """
    head = (f"# 更新说明草稿 · {platform} 1.{build}\n\n"
            "下面是按最近提交记录自动归纳的草稿，**请管理员删掉看不懂的内部术语、"
            "补上用户能感知的变化，再另存为正式说明发布**。\n"
            "（工具不会把这份草稿直接发出去：`--draft-from-git` 只起草，发布必须走 "
            "`--notes <人写的那一份>`）\n\n")
    if not subjects:
        return head + "（这一条都没读到：仓库不是 git、或者没有提交历史。请手工写一份。）\n"
    body = "\n".join(f"· {s}" for s in subjects)
    return head + body + f"\n\n（共 {len(subjects)} 条提交，逐条来自 git log --pretty=%s）\n"


def artifact_entry(path: Path) -> tuple:
    """按**真制品**算出清单里的一档：属于哪个平台键、档名、直链、指纹、字节数。

    这一档是谁的是以**制品自己的文件名**为凭（反查 `ARTIFACT_BY_KEY`），不是拿发布通道
    去要：Windows 那一份清单里安装包与解压版是两档并存，用通道名逐一要求会把第二份顶掉
    （症状是「构建明明成功，清单里就是少了那份包」）。而通道缺不缺自己要的那一档，
    是另一件事，由调用方在全部制品扫完之后再判一次。

    认不出的文件名一律拒收：清单里多一档没登记过的包，读侧只会把它当不认识（而不是
    报错），而发版的人以为已经发出去了。
    """
    key = next((k for k, name in tu.ARTIFACT_BY_KEY.items() if name == path.name), "")
    if not key:
        raise ValueError(f"这个文件名不在发布制品表里，拒绝写进清单：{path.name}")
    if not path.is_file():
        raise ValueError(f"制品文件不在位：{path}")
    size = path.stat().st_size
    if size <= 0:
        raise ValueError(f"制品是个空文件（0 字节），不发这种包：{path}")
    digest = tu.sha256_file(path)
    if not re.match(r"^[0-9a-f]{64}$", digest or ""):
        raise ValueError(f"算不出制品指纹（读不到还是读坏了）：{path}")
    return key, {"file": path.name,
                 "url": f"https://{tu.BUCKET_HOST}/{tu.RELEASE_PREFIX}/{path.name}",
                 "sha256": digest,
                 "size": size}


def build_manifest(platform: str, build: int, notes: str, artifacts: dict,
                   published_at: str = "") -> dict:
    """拼清单。字段名与顺序都按读侧的要求来，导出值一律由 tool_version 算。"""
    return {"schema": tu.SCHEMA,
            "version": tv.version_text(build),
            "build": int(build),
            "published_at": published_at or datetime.now(timezone.utc).astimezone().isoformat(),
            "notes": notes,
            "artifacts": artifacts}


def load_notes(args) -> str:
    """说明文字的唯一来源：管理员写的那份文件，或命令行直接给的一段话。"""
    if args.notes and args.notes_text:
        raise ValueError("--notes 与 --notes-text 只能给一个（两个都给就不知道哪份算数）")
    if args.notes:
        p = Path(args.notes)
        if not p.is_file():
            raise ValueError(f"更新说明文件不在位：{p}")
        return p.read_text(encoding="utf-8").strip()
    if args.notes_text:
        return args.notes_text.strip()
    return ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成发布清单 latest_<平台>.json")
    ap.add_argument("--platform", required=True, choices=sorted(tu.MANIFEST_BY_KEY),
                    help="这一条发布通道属于哪个平台")
    ap.add_argument("--build", default="", help="构建号；缺省取 GITHUB_RUN_NUMBER")
    ap.add_argument("--artifact", action="append", default=[],
                    help="真制品路径，可重复（档名必须与该平台对应，指纹与字节数就地算）")
    ap.add_argument("--notes", default="", help="更新说明文件（管理员过目的那一份）")
    ap.add_argument("--notes-text", default="", help="更新说明（直接给一段话，与 --notes 互斥）")
    ap.add_argument("--published-at", default="", help="发布时间 ISO 串；缺省取现在")
    ap.add_argument("--out", default="dist", help="清单写到哪个目录")
    ap.add_argument("--draft-from-git", type=int, default=0, dest="draft",
                    help="只起草说明给管理员过目（写出草稿就退出，不发布）")
    ap.add_argument("--allow-empty-notes", action="store_true", dest="allow_empty_notes",
                    help="确实要发空说明时显式声明（默认拒绝）")
    args = ap.parse_args(list(argv) if argv is not None else sys.argv[1:])

    build = current_build(args.build)
    if build == tv.UNKNOWN_BUILD:
        return _die("构建号没能确认（没给、给的是 0，或给了个不像版本号的东西）："
                    "发出去的清单会是「1.0」那一档，从此再也提不到更新。"
                    "这一类必须停在这里，不许带着一个假号继续打包")
    if not tv.in_range(build):
        return _die(f"构建号 {build} 超出可表达范围（≤ {tv.MAX_BUILD}），不发")

    if args.draft:
        subjects = git_subjects(args.draft)
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        draft = out_dir / f"release_notes_draft_{args.platform}.md"
        draft.write_text(draft_text(args.platform, build, subjects), encoding="utf-8")
        print(f"ℹ 草稿已写到 {draft}（{len(subjects)} 条提交）：请管理员过目改定后，"
              f"用 --notes 那个文件发布；本命令没有生成清单")
        return 0

    if not args.artifact:
        return _die(f"{args.platform} 这一档至少要给出自动安装用的制品："
                    f"--artifact <{tu.ARTIFACT_BY_KEY[args.platform]} 的路径>")
    try:
        notes = load_notes(args)
    except ValueError as e:
        return _die(str(e))
    if not notes and not args.allow_empty_notes:
        return _die("更新说明是空的：界面上那句「本次更新了什么」必须有人对它负责。"
                    "确实想发空说明就显式加 --allow-empty-notes")

    artifacts = {}
    for raw in args.artifact:
        try:
            key, entry = artifact_entry(Path(raw))
        except ValueError as e:
            return _die(str(e))
        artifacts[key] = entry
    if args.platform not in artifacts:
        return _die(f"没有给出 {args.platform} 自动安装所需的制品（只给了 "
                    f"{'、'.join(sorted(artifacts)) or '空'}）：用户点「现在就装」会拿不到包")

    doc = build_manifest(args.platform, build, notes, artifacts, args.published_at)
    # 自己写的清单先过一遍自己这头的读口径：字段少一个、地址拼歪一档、指纹算错，
    # 都在这一步红，而不是等用户机器上读不出来（那时已经发出去了）
    back = tu.normalize_manifest(doc)
    if not back.get("ok"):
        return _die(f"生成的清单被读侧拒收：{back.get('why')}")
    data = back["data"]
    if data["build"] != build:
        return _die(f"读回来的构建号是 {data['build']}，与要发的 {build} 不是同一个数")
    art = data["artifacts"].get(args.platform)
    if not art or not art.get("url") or not art.get("sha256"):
        return _die(f"清单里 {args.platform} 这一档制品不齐（地址或指纹缺失）："
                    "用户点「现在就装」会拿不到包")
    # 这里**不**拿本机的构建号去跑一次 evaluate：发布机上有没有随包戳与用户能不能
    # 看到新版本是两件不相干的事，把它们绑在一起只会多一条「CI 因为自己的环境红」的假红。
    # 「旧一版的客户端能看到 update-available」那条性质交给离线判据去钉（⑰ 组）。

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = tu.MANIFEST_BY_KEY[args.platform]
    dest = out_dir / name
    text = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    try:
        dest.write_text(text, encoding="utf-8")
    except OSError as e:
        return _die(f"清单写不下去：{type(e).__name__}: {e}")
    print(f"✅ 已生成 {dest}：{doc['version']}（build {build}），"
          f"制品 {len(artifacts)} 档（{args.platform} 自动安装可用）")
    print(f"   下载直链：{artifacts[args.platform]['url']}")
    print(f"   指纹：{artifacts[args.platform]['sha256'][:16]}…  "
          f"字节：{artifacts[args.platform]['size']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
