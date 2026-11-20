#!/usr/bin/env python3
r"""qoder_rules_offline_test.py —— 给「IDE 项目资产到底会不会被加载」做判据。

为什么要这一套（2026-11-20 交接段的自审结论）：`AGENTS.md` 与 `.qoder/rules/*.md` 是
新机器上 Qoder 能不能接手开发的**唯一上下文载体**（会话历史只存本机、不随账号同步）。
而这类资产的失败形态恰好是本项目最恨的那一族——**文件在、格式不对、IDE 静默不加载，
谁都不报错**。肉眼 review 抓不住：frontmatter 少一行、trigger 拼错、glob 打不中任何文件，
看起来都"写得挺好"。

判据口径不是猜的，是从 Qoder 自己的实现里逐条抄出来的（只读取证，未改动应用包）：

    /Applications/Qoder IDE.app/Contents/Resources/app/out/vs/workbench/workbench.desktop.main.js
      · frontmatter 正则：/^---\r?\n([\s\S]*?)\r?\n---\r?/        （必须从文件第 1 行开始）
      · 逐行 `key: value`，用第一个冒号切；去掉包裹的一对引号    （**不支持多行值**）
      · trigger 合法值枚举：glob / manual / model_decision / always_on
      · 优先级：trigger 合法 → 用它；否则有 glob(s) → glob；否则 alwaysApply=="true" → always_on；
                否则有 description → model_decision；否则 alwaysApply=="false" → manual
      · **没有 frontmatter 时默认 always_on**（默认值就是最贵的那一档：每次都注入）
      · 正文为空 → isEmpty；监听 **/**.qoder/rules/**/*.{md,mdc}（目录枚举，递归子目录）
    product.json: dataFolderName = ".qoder"（国际版实测；别的构建可能不同名 → 见丙组最后一条）

官方口径（docs.qoder.com/zh/user-guide/rules，2026-11-20 读全文）：项目规则存放于
`.qoder/rules`、随 Git 共享；`AGENTS.md` 自动识别、**与 rules 冲突时 rules 优先**；
所有生效规则合计上限 100,000 字符，超出截断；仅支持自然语言。

跑法：`.venv/bin/python debug/qoder_rules_offline_test.py`（在批跑清单 FILES 里，每轮批跑都跑）。
"""
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RULES_DIR = ROOT / ".qoder" / "rules"
AGENTS = ROOT / "AGENTS.md"

TRIGGERS = ("glob", "manual", "model_decision", "always_on")
TOTAL_CHAR_BUDGET = 100_000          # 官方上限：所有生效规则合计，超出截断

_ok, _failed = 0, []


def check(label, cond, extra=""):
    global _ok
    if cond:
        _ok += 1
        print(f"  ok {label}")
    else:
        _failed.append(label)
        print(f"FAIL {label} {extra}")


def parse_rule(text):
    """复刻 Qoder 的 frontmatter 解析（口径见文件头）。返回 kv + 判定出的 trigger。"""
    m = re.compile(r"^---\r?\n([\s\S]*?)\r?\n---\r?").match(text)
    trigger, glob_, desc, kv, has_fm = "always_on", "", "", {}, False
    if m:
        has_fm = True
        for line in re.split(r"\r?\n", m.group(1)):
            i = line.find(":")
            if i > 0:
                v = line[i + 1:].strip()
                if len(v) >= 2 and v[0] in "'\"" and v[-1] == v[0]:
                    v = v[1:-1]
                kv[line[:i].strip()] = v
        if kv.get("trigger") in TRIGGERS:
            trigger = kv["trigger"]
        elif kv.get("globs") or kv.get("glob"):
            trigger, glob_ = "glob", kv.get("globs") or kv.get("glob")
        elif kv.get("alwaysApply") == "true":
            trigger = "always_on"
        elif kv.get("description"):
            trigger, desc = "model_decision", kv["description"]
        elif kv.get("alwaysApply") == "false":
            trigger = "manual"
        if trigger == "model_decision" and kv.get("description"):
            desc = kv["description"]
        if trigger == "glob":
            glob_ = kv.get("glob") or kv.get("globs") or glob_
        body = text[m.end():]
    else:
        body = text
    stray = [l for l in (m.group(1).splitlines() if has_fm else [])
             if l.strip() and l.find(":") <= 0]
    return {"has_fm": has_fm, "trigger": trigger, "glob": glob_, "description": desc,
            "kv": kv, "empty": not body.strip(), "stray": stray}


def glob_hits(pattern):
    """这条 glob 在**本仓库**能命中几个真文件。打不中就是规则永不生效（静默失效）。"""
    p = pattern.strip()
    if not p:
        return []
    try:
        return [q for q in ROOT.glob(p) if q.is_file()]
    except Exception:
        return []


def validate(rel, text, kind="rule"):
    """一份 IDE 资产的合格性；返回问题清单（空=合格）。

    kind 必须分档，因为它们**不是同一个读法**：`.qoder/rules/*.md` 走上面那套 frontmatter
    解析器，而 `AGENTS.md` 是官方兼容通路（整篇当上下文注入，没有“默认 always_on”这一说）。
    把规则档的 schema 套到 AGENTS.md 上，就是本仓库反复挨打的那个形状——
    **判据的前提超出了自己的适用范围**（第一轮就红了它：要求 AGENTS.md 写 frontmatter）。
    """
    r, bad = parse_rule(text), []
    if kind != "rule":
        if r["empty"]:
            bad.append(f"{rel}：正文为空（注入进去什么也没有）")
        if "\n# " not in text and not text.startswith("# "):
            bad.append(f"{rel}：没有任何一级标题——整篇注入时模型抓不到结构，人也不好在 IDE 外读")
    else:
        if not r["has_fm"]:
            bad.append(f"{rel}：没有 frontmatter——会被默认成 always_on（最贵的一档），必须显式写")
        if r["kv"].get("trigger") and r["trigger"] != r["kv"]["trigger"]:
            bad.append(f"{rel}：trigger={r['kv']['trigger']!r} 不在合法枚举 {TRIGGERS} 里，会被回落到默认档")
        if r["empty"]:
            bad.append(f"{rel}：正文为空（规则内容为空 = 一条都不注入）")
        if r["stray"]:
            bad.append(f"{rel}：frontmatter 里有 {len(r['stray'])} 行不是 `key: value`，"
                       f"解析器会**逐行丢弃**（多行值/折行在这里不支持）：{r['stray'][0][:40]!r}")
        if r["trigger"] == "model_decision" and not r["description"]:
            bad.append(f"{rel}：model_decision 没有 description——模型无从判断何时套用，等于 manual")
        if r["trigger"] == "glob" and not r["glob"]:
            bad.append(f"{rel}：glob 类型没写 glob——一条文件都匹配不上")
    if "\n\n\n\n" in text:
        bad.append(f"{rel}：连续 4 个空行，通常是段落塌了/复制错位")
    return bad


# ── 甲) 正向：资产在位、格式合格、预算内 ──────────────────────────────────────
check("甲1 仓库根有 AGENTS.md（Qoder 官方：自动识别，无需配置）", AGENTS.is_file())
rule_files = sorted(list(RULES_DIR.glob("*.md")) + list(RULES_DIR.glob("*.mdc"))) if RULES_DIR.is_dir() else []
check("甲2 .qoder/rules 目录存在且有规则文件（新机器 clone 后要有上下文载体）",
      len(rule_files) >= 1, f"实际 {len(rule_files)} 份：{[p.name for p in rule_files]}")

problems = []
if AGENTS.is_file():
    problems += validate("AGENTS.md", AGENTS.read_text(encoding="utf-8"), kind="agents")
for p in rule_files:
    problems += validate(str(p.relative_to(ROOT)), p.read_text(encoding="utf-8"))
check("甲3 全部 IDE 资产格式合格（含反向对照同款判据）", problems == [], problems)

total = sum(len(p.read_text(encoding="utf-8")) for p in ([AGENTS] if AGENTS.is_file() else []) + rule_files)
check(f"甲4 生效规则合计 {total} 字符，在官方 100,000 上限内（超出会被**截断**，"
      f"截掉的是尾部内容而前面照常生效——这种半生效最难查）", total <= TOTAL_CHAR_BUDGET,
      f"实际 {total}")
check("甲5 规则档数符合设计：1 份 always_on + 2 份 model_decision + 1 份 glob",
      [parse_rule(p.read_text(encoding="utf-8"))["trigger"] for p in rule_files].count("always_on") == 1,
      [(p.name, parse_rule(p.read_text(encoding="utf-8"))["trigger"]) for p in rule_files])

# ── 乙) 反向对照：坏格式必须响（否则本判据就是一条空闸）───────────────────────
_FIXTURES = {
    "缺 frontmatter": "# 规则\n正文有内容但没有任何头部\n",
    "trigger 拼错": "---\ntrigger: always\n---\n\n正文\n",
    "model_decision 没描述": "---\ntrigger: model_decision\n---\n\n正文\n",
    "glob 没写模式": "---\ntrigger: glob\n---\n\n正文\n",
    "正文为空": "---\ntrigger: always_on\nalwaysApply: true\n---\n\n",
    "多行值折行": "---\ntrigger: always_on\ndescription: 第一行\n  第二行被折下来\n---\n\n正文\n",
}
for name, text in _FIXTURES.items():
    got = validate("夹具/" + name, text, kind="rule")
    check(f"乙 反向对照「{name}」必须响", len(got) >= 1, "这份坏格式被判据读成了合格 → 甲3 是空闸")
check("乙6 正向对照：不带 frontmatter 的 AGENTS.md **不该**响（它是兼容通路，不由规则解析器读）——"
      "这一格是第一轮自己踩的坑：把规则档的 schema 套到 AGENTS.md 上，判据就成了一条假红",
      validate("夹具/AGENTS 无头部", "# 标题\n\n正文内容\n", kind="agents") == [])
check("乙7 反向对照的份数自洽：夹具份数就是应响份数，少一份就是有的分支没牙",
      len(_FIXTURES) == 6, sorted(_FIXTURES))
# 缺省档这一格要单独钉：它不是"我们的文件坏了"，而是"格式缺失时 IDE 会怎么选"
_d = parse_rule("# 只有正文\n")
check("乙8 无 frontmatter 时 IDE 默认落 always_on（本判据据此要求「必须显式写」）",
      _d["has_fm"] is False and _d["trigger"] == "always_on", _d)

# ── 丙) 交叉核对：glob 打不打得中真文件、指路走不走得通、资产进没进仓库 ────────
for p in rule_files:
    r = parse_rule(p.read_text(encoding="utf-8"))
    if r["trigger"] != "glob":
        continue
    for pat in [x.strip() for x in r["glob"].split(",") if x.strip()]:
        n = len(glob_hits(pat))
        check(f"丙1 {p.name} 的 glob {pat!r} 在本仓库命中真文件（打不中=这条规则永不生效）",
              n >= 1, f"命中 {n} 个")

assets_text = (AGENTS.read_text(encoding="utf-8") if AGENTS.is_file() else "") + \
    "".join(p.read_text(encoding="utf-8") for p in rule_files)
refs = sorted(set(re.findall(r"docs/[0-9A-Za-z\u4e00-\u9fff_\-.\u3000-\u303f]+\.md", assets_text)))
missing = [x for x in refs if not (ROOT / x).is_file()]
check(f"丙2 资产里点名的 {len(refs)} 份 docs 文档都在位（指路走不通时人会跳过文档）",
      refs and not missing, {"读到": len(refs), "缺": missing})
for doc in refs:
    body = (ROOT / doc).read_text(encoding="utf-8")
    for sec in set(re.findall(re.escape(doc) + r"[^\n]{0,24}?第\s*(\d+)\s*节", assets_text)):
        check(f"丙3 {doc} 里确有「第 {sec} 节」（资产正文里指向了它）",
              bool(re.search(rf"^##\s*{sec}\.", body, re.M)), "该节不存在＝指路指向空处")


def _git(*args):
    return subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=str(ROOT),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=60, stdin=subprocess.DEVNULL)


tracked = set(_git("ls-files").stdout.splitlines())
# 交接文档也必须进 git 索引：本段真实踩过「文档写完了、判据全绿、clone 下来一份都没有」——
# 因为旧清单只点名 AGENTS.md 与规则文件，docs/ 整目录不在视野内。
docs_dir = ROOT / "docs"
docs_files = {f"docs/{p.name}" for p in sorted(docs_dir.glob("*.md"))}
check("丙4a 交接文档目录非空（清单为空时下面那条『都已在索引里』会恒真＝假绿）",
      len(docs_files) >= 8, f"docs/ 下只读到 {len(docs_files)} 份 .md")
want = ({"AGENTS.md"} if AGENTS.is_file() else set()) | {f".qoder/rules/{p.name}" for p in rule_files} | docs_files
untracked = sorted(w for w in want if w not in tracked)
check(f"丙4 换机要带的资产（{len(want)} 份：AGENTS.md + 规则文件 + 全部交接文档）都已在 git 索引里"
      f"——**换机器只靠 git 带**，没提交的那份在新机器上等于不存在", untracked == [], untracked)
ign = _git("check-ignore", *sorted(want))
check("丙5 这些资产没有被 .gitignore 拦着（被拦着时 clone 拿不到，且不会有任何提示）",
      ign.returncode != 0, ign.stdout.strip())
check("丙6 统计中文文件名一律带 core.quotepath=off（本仓库真实踩过：不带时中文路径被转义成"
      "以引号结尾，grep '\\.md$' 一条都匹配不上，据此下的结论全是错的）",
      "core.quotepath=off" in subprocess.list2cmdline(["git", "-c", "core.quotepath=off", "ls-files"]))

print(f"\n═══ IDE 项目资产：{_ok} 通过 / {len(_failed)} 失败 ═══")
if _failed:
    print("失败项：")
    for f in _failed:
        print(f"  - {f}")
    sys.stdout.flush()
    os._exit(1)
print(f"全部通过 ✅ ({_ok}/{_ok})")
sys.stdout.flush()
os._exit(0)
