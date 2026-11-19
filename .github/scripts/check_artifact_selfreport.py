#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「产物自报的版本」与「本次 CI 注入的版本」逐条对拍（两个平台的打包链共用这一份）。

为什么不各自内联在 workflow 里：需求 2 之后，「这个包到底是不是 1.<run_number>」是交付的
第一判据，而 Windows 的 run 步是 pwsh、Mac 的是 bash。同一套判定抄两遍，漂移的后果是
「一个平台拦得住、另一个平台放行」——那比没有判据更危险，因为屏幕上两个 run 都是绿的。
本仓库已经在 --hidden-import 清单、pip 源、runtime_paths 落点这三件事上各吃过一次「两份
实现」的亏。抽成脚本还有第二件实打实的好处：这一段的对错从此能被离线判据直接调用
（喂一份假报告，要求它必须红、且红在点上），而内联在 pwsh 里的话本机连解释器都没有。

判据的顺序就是取证的顺序，三条红线各说一件事、不许互相冒充：
  · 报告读不到 / 不是 JSON / 不是对象   —— **取证没拿到**：自报那条路压根没走通，
    这时谈「版本对不对」没有意义，话也必须说成「拿不到证据」而不是「版本不对」；
  · certain=false                      —— 产物说不出自己是哪一版（随包构建戳缺失或读坏）；
  · build / commit 与注入值不相等       —— 产物说的与 CI 记的不是同一件事（上一轮的戳
    被带过来了，或者注入根本没生效）。
把这三种混成一句「自检失败」，等于让下一个人在六种可能里猜。

退出码：0 = 逐条对上；1 = 任一条不对。这一步在打包链里的目标就是「不给一个说不清自己
是哪一版的包开绿」，与 tool_version.main() 里「取证恒返 0、判定另做」的分工正好对接：
自报那一步负责如实说，这里负责判，两次的红才各有含义。

用法（两个平台同一条命令，只是 --report 的落点不同）：
    python .github/scripts/check_artifact_selfreport.py \
        --report "$RUNNER_TEMP/version_selfreport.json" \
        --expect-build "${{ github.run_number }}" \
        --expect-commit "${{ github.sha }}" \
        --label B2bCollectTool.exe
"""
import sys
import json
import argparse
from pathlib import Path

# 编码必须在第一次往控制台写中文之前定好：Windows runner 的英文镜像是 cp1252，写不下去
# 的那个字会当场抛 UnicodeEncodeError，而这条脚本的退出码本来就是要告诉构建者「哪一条
# 不对」——异常一抛，结论被盖掉，红的看起来像脚本自己坏了。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _red(label: str, why: str, hint: str) -> int:
    """统一的红法：先说要停、再说谁不对、最后给下一步查哪里。"""
    print(f"::error::{label}：{why}")
    if hint:
        print(f"· 下一步查这里：{hint}")
    return 1


def load_report(path: str, label: str) -> tuple:
    """读那份自报 JSON；读不到/读坏一律按「取证没拿到」返回 (None, 退出码)。

    这里刻意不区分「文件不存在」与「JSON 语法坏」之后的处理——两者对构建者是同一件事：
    没有证据可判。但**文件在而内容为空**要单独说一句，因为那通常是 --out 被两个进程
    同时写、或者产物在写之前就退出了。
    """
    p = Path(path)
    if not p.is_file():
        return None, _red(label, f"自报报告不在位：{p}",
                          "产物那一步有没有真的跑起来？--out 传的是不是同一个路径？")
    text = p.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        return None, _red(label, f"自报报告是空的：{p}",
                          "产物可能在写报告之前就退了（看那一步的退出码），或 --out 被别处覆盖过")
    try:
        data = json.loads(text)
    except ValueError as e:
        return None, _red(label, f"自报报告不是合法 JSON（{type(e).__name__}: {e}）：{p}",
                          "报告格式变了就得同时改这里与 tool_version.report()，两份不一致就是假绿")
    if not isinstance(data, dict):
        return None, _red(label, f"自报报告顶层不是对象（拿到的是 {type(data).__name__}）：{p}",
                          "报告格式变了就得同时改这里与 tool_version.report()，两份不一致就是假绿")
    return data, 0


def check(args) -> int:
    label = args.label or "产物"
    data, rc = load_report(args.report, label)
    if data is None:
        return rc
    # 先把自报的原文念出来：判定在下面，但取证要在屏幕上——红了以后第一眼看的是这几行，
    # 而不是让人再去下载一次制品、本地跑一遍 --print-version。
    print(f"· {label} 自报：{data.get('version')}  四段={data.get('product_version')}  "
          f"frozen={str(data.get('frozen')).lower()}  commit={data.get('commit') or '-'}")
    print(f"· 它读的戳：{data.get('stamp_file')}")

    if not data.get("certain"):
        return _red(label, "产物说不出自己是哪一版（certain=false，随包构建戳读不到或为空）",
                    "打包命令有没有把 version.json 注进包、它是否落在 tool_version.stamp_file() 那个位置。"
                    "这种包发出去只会永远提不到更新，而远程排障也问不出真相")
    # 期望值自己也要判：上一步的表达式没生效时这里会拿到空串，而空串与任何构建号都
    # 「不相等」——报成「产物对不上」就冤枉了产物。
    expect_build = str(args.expect_build or "").strip()
    if not expect_build.isdigit():
        return _red(label, f"本次注入的构建号不像一个数（拿到的是「{expect_build or '（空）'}」）",
                    "workflow 里 run_number 那个表达式有没有生效；先修注入，再来比产物")
    if str(data.get("build")) != expect_build:
        return _red(label, f"产物自报的构建号是 {data.get('build')}，本次注入的是 {expect_build}",
                    "两个号各自都能凑出一份自洽的交付，但用户拿到的是哪一个没人说得清——"
                    "必须停在这里，而不是发出去后靠客户报版本号对上号")
    if not data.get("frozen"):
        return _red(label, "自报说 frozen=false：跑起来的不是打包后的那一份",
                    "布局或入口变了，这份取证不算数（源码态读的是仓库里的戳，与包无关）")
    commit = str(data.get("commit") or "").strip()
    if not commit:
        return _red(label, "戳里没有 commit：这一版号对不上任何一次提交",
                    "宁可不发，也不发一个说不清出自哪一次代码的包（远程排障时点不出名）")
    expect_commit = str(args.expect_commit or "").strip()
    if not expect_commit:
        return _red(label, "本次注入的 commit 是空的", "workflow 里 github.sha 那个表达式有没有生效")
    if commit.lower() != expect_commit.lower():
        return _red(label, f"戳里的 commit 是 {commit[:8]}，本次构建是 {expect_commit[:8]}",
                    "随包说的那个号与本次代码不是同一件事（上一轮的戳被带过来了，"
                    "或是构建戳那一步写到了另一个目录、被缓存复用）")
    print(f"✅ {label} 自报与本次注入逐条对上：{data.get('version')}（commit {commit[:8]}…）")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="产物自报版本与本次注入值的对拍")
    ap.add_argument("--report", required=True, help="产物用 --print-version --json --out 写出的那份 JSON")
    ap.add_argument("--expect-build", default="", help="本次注入的构建号（CI 的 run_number）")
    ap.add_argument("--expect-commit", default="", help="本次注入的 commit（CI 的 sha）")
    ap.add_argument("--label", default="", help="报错里怎么称呼这个产物（B2bCollectTool.exe / .app）")
    args = ap.parse_args(argv)
    try:
        return check(args)
    except OSError as e:
        # 读报告本身炸了（权限、路径被中途删掉）也是「取证没拿到」，不能留一条 traceback
        # 冒充判定结论：那会把「这一步为什么红」又推回给人猜。
        return _red(args.label or "产物", f"自报报告读不下去：{type(e).__name__}: {e}",
                    "报告落点是不是在本次构建的可写目录里")


if __name__ == "__main__":
    sys.exit(main())
