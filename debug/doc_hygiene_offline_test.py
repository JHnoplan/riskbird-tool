#!/usr/bin/env python3
r"""doc_hygiene_offline_test.py —— 给「交接文档的中文」做判据（两类静默缺陷）。

为什么需要（2026-11-20 交接段实测）：这一轮我写了 8 份文档 + 4 份 IDE 规则，自审时抓到
**四次中文形近错替**（豁免→貁、绝不→讫/绞、接住→进住）和**三处中英夹杂**
（"总有一天 someone 会"、"本 project"、注释里的 "pointing"）。
其中形近错字**肉眼 review 没抓全**，是被"以既有语料为合法字表、扫新文档里的生面孔"
这个口径抓净的。既然换一个人（或下一轮会话）不会再有这套口径，就把它固化成判据。

两类判定：
  甲) **CJK 生面孔**：目标文档里出现了「仓库既有语料从没用过」的汉字 → 高概率是形近错替。
      语料只取 **git 跟踪文件**（不是工作区）——用工作区当语料会造出一条本机绿、
      干净克隆红的判据，那正是本仓库 `docs/06` A 族反复付学费的形状。
  乙) **中英夹杂**：中文正文里出现裸英文词，而它既不在语料里、也不在允许名单里。
      `` `反引号` `` 与代码围栏内的内容豁免（标识符、命令、包名本来就该保留原文）。

已知判不到的形状（写在案上，免得这条门给人「所有夹杂都被管住了」的错觉）：
  · 语料里已经出现过的英文词（`project`、`data`）夹进中文句子不会响——所以乙只保证
    「新引入的夹杂」被抓到；老夹杂要靠 review。
  · 合法但全新的中文用字（第一次写进仓库的术语）会被甲报出来，需要**带理由**进白名单，
    而不是关掉这条判据。

跑法：`.venv/bin/python debug/doc_hygiene_offline_test.py`（在批跑清单 FILES 里，每轮批跑都跑）。
"""
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 目标：所有「给人读的中文资产」。新增同类文档自动进视野（命名约定，不靠人记得登记）。
TARGET_GLOBS = ("AGENTS.md", "docs/*.md", ".qoder/rules/*.md", "*.md")

# 白名单：确属本项目要用、且**已人工确认不是错字**的生面孔汉字（每个都带理由，别批量灌）。
# 2026-11-20 逐条人工查过上下文，以下 11 字全部是合法用字，不是形近错替。
# 反过来，docs/06 里作为「错字示例」被引用的字（豁→貁、绝→绞）**不进这里**——它们是"被讨论的
# 字符"，正确写法是加反引号进 code span 由 strip_code 豁免；进白名单等于把示例当正文放行。
CJK_ALLOWED = set(
    "瘦"      # 瘦身：包体积裁剪（需求 5 的口径）
    "呆"      # 防呆层：根 .gitignore 的定位
    "债"      # 架构债：docs/01 第 6 节
    "帝"      # 上帝对象：gui 主文件的既有说法
    "簇"      # 对话框簇：拆分方向
    "惕"      # 警惕：断言数为 0 的 PASS 那一格
    "浓"      # 浓缩：八条铁律是故障的浓缩
    "祝"      # 祝你采集顺利：旧文档结尾
    "碳"      # 碳酸锂：电池材料名（AI 解析样例）
    "拳"      # 组合拳：两份复盘文档里的固定说法
    "嫁"      # 转嫁：把不确定性转嫁给用户
    "刺"      # 讽刺：「取证工具把程序带崩是最讽刺的失败形态」
    "讽"      # 同上，与「刺」成词
)

# 允许名单：中文正文里允许裸写的英文（不在语料里也不报）。只放「术语且不便加反引号」的词。
# 2026-11-20 逐条人工判定后加入（产品名/协议名/无中文惯用叫法的行业术语）。
# 反过来，**同句里已经有中文说法的**一律不进这里：`rules` 与「规则」并存、`TODO` 与
# 「未完成项」并存、`flaky` 与本项目的「抖动」并存——那是要改的夹杂，不是要供着的术语。
LATIN_ALLOWED = {"macOS", "Windows", "Linux", "Qoder", "PyPI", "arm64",
                 "qoder-cn",      # 国内版构建的产品名（数据目录名可能与国际版不同）
                 "RiskBird",      # 同仓库里另一个可独立打包的工具名
                 "MCP",           # Model Context Protocol
                 "SSOT",          # 已写作「单一真源（SSOT）」，括注缩写便于检索
                 "lint",          # 静态检查的行业叫法
                 "shift-left"}    # 已写作「构建前门禁（shift-left）」

_ok, _failed = 0, []


def check(label, cond, extra=""):
    global _ok
    if cond:
        _ok += 1
        print(f"  ok {label}")
    else:
        _failed.append(label)
        print(f"FAIL {label} {extra}")


def tracked_text():
    """git 跟踪的 .py/.md 全文（语料）。带 core.quotepath=off，否则中文路径被转义后全漏。"""
    out = subprocess.run(["git", "-c", "core.quotepath=off", "ls-files", "-z"], cwd=str(ROOT),
                         capture_output=True, text=True, encoding="utf-8", errors="replace",
                         timeout=120, stdin=subprocess.DEVNULL).stdout
    files = [x for x in out.split("\0") if x.endswith((".py", ".md"))]
    text = []
    for rel in files:
        p = ROOT / rel
        try:
            if p.is_file() and p.stat().st_size <= 3_000_000:
                text.append(p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return files, "\n".join(text)


FRONTMATTER = re.compile(r"^---\r?\n[\s\S]*?\r?\n---\r?")


def strip_meta(text):
    """去掉 frontmatter：那是**给 IDE 读的元数据**，不是给人读的中文正文。

    不剥就会把 `trigger:` / `alwaysApply:` 判成"中英夹杂"，可这两个字段是规则文件必须写的
    东西（口径见 debug/qoder_rules_offline_test.py 复刻的解析器）。属于「判据的前提超出自己
    的适用范围」这一族——本段已经为它付过一次学费（把规则 schema 套到 AGENTS.md 上）。
    """
    return FRONTMATTER.sub("", text, count=1)


def strip_code(text):
    """去掉代码围栏与行内反引号——那里的英文本来就该是英文。"""
    text = re.sub(r"```[\s\S]*?```", " ", text)
    return re.sub(r"`[^`\n]*`", " ", text)


CJK = re.compile(r"[\u4e00-\u9fff]")
LATIN = re.compile(r"[A-Za-z][A-Za-z0-9_.\-]{2,}")
# 命中后还要过这一道：形如 `v1.73`、`build77`、`f53b7b1`、`AGENTS.md`、`PyQt5-sip`、`gui_store`
# 的 token 是**版本号/提交号/文件名/标识符**——那是代码与数据的形状，中文句子里本来就该留原文。
# 本条判据要抓的是"该说中文的地方说了英文单词"（`本 project`、`总有一天 someone 会`）。
# 少了这一道，门禁会淹没在版本号噪音里，而**噪音过多的门禁最终会被人关掉**——比漏检更糟。
# 注意先剥句尾标点：`project.` 整体被 LATIN 吞下后含点就会被放过，那是给夹杂开门。
_TRAILING_PUNCT = ".,;:!?、。：，"
_DATAISH = re.compile(r"[0-9_./" + chr(92) * 2 + "]")  # 两个反斜杠：一个都别多，字符类才闭合


def scan(text, cjk_corpus, latin_corpus):
    prose = strip_code(strip_meta(text))
    new_cjk = {c for c in set(CJK.findall(prose)) if c not in cjk_corpus and c not in CJK_ALLOWED}
    new_latin = {w for w in set(LATIN.findall(prose))
                 if w not in latin_corpus and w not in LATIN_ALLOWED
                 and not _DATAISH.search(w.rstrip(_TRAILING_PUNCT))}
    return sorted(new_cjk), sorted(new_latin)


corpus_files, corpus = tracked_text()
cjk_corpus = set(CJK.findall(corpus))
latin_corpus = set(LATIN.findall(corpus))

# ── 甲) 语料本身不许是空的（空视野必须自己红，否则「没发现错字」=「语料没读到」）────
check("甲1 语料来自 git 跟踪文件且份数可观（用工作区当语料会造成本机绿、干净克隆红）",
      len(corpus_files) >= 150, f"读到 {len(corpus_files)} 份")
check("甲2 语料里的汉字够多（少于 2000 个就说明扫歪了，此时「没生面孔」毫无含义）",
      len(cjk_corpus) >= 2000, f"实际 {len(cjk_corpus)} 个不同汉字")
check("甲3 语料里的英文词够多（同上：拉丁侧空视野同样是空闸）",
      len(latin_corpus) >= 1000, f"实际 {len(latin_corpus)} 个不同词形")

# ── 乙) 反向对照：两类坏样本都必须响 ────────────────────────────────────────────
_fake = "这句话里出现了一个仓库从没见过的貁字，还有裸英文 zebraquuxfizz 混在中文里。\n"
c, l = scan(_fake, cjk_corpus, latin_corpus)
check("乙1 形近错字（貁）必须被抓出来", "貁" in c, c)
check("乙2 中英夹杂（语料外裸英文词）必须被抓出来", "zebraquuxfizz" in l, l)
c2, l2 = scan("这句用了 `git commit` 与 Python，没有该报的东西。\n", cjk_corpus, latin_corpus)
check("乙3 反引号里的命令不许报（否则全文没法写命令）", l2 == [], l2)

# ── 丙) 正向：全部中文资产过扫 ─────────────────────────────────────────────────
targets = []
for pat in TARGET_GLOBS:
    for p in (sorted(ROOT.glob(pat)) if "*" in pat else ([ROOT / pat] if (ROOT / pat).is_file() else [])):
        if p.is_file() and p not in targets:
            targets.append(p)
check(f"丙1 目标文档在视野里（读到 {len(targets)} 份，含 AGENTS.md 与 docs/、.qoder/rules/）",
      len(targets) >= 10, [str(p) for p in targets])

# 语料只取 **git 跟踪文件**（这是本判据的立命之本：用工作区当语料会造出本机绿、干净克隆红）。
# 于是有一个过渡态：交接前写的复盘文档还没入库，它们里的每个英文产品名都成了"生面孔"。
# 对它们判夹杂＝逼我给几十个专名开白名单（正是本文件注释里禁止的"批量灌"）。
# 但绝不能写成「未入库的都免检」——那等于给以后每份新文档自动发免检证。所以用**点名表**：
# 只有列在这里、且带理由的份数享受只判错字不判夹杂；不在表里的未入库文档照样全判。
# 这批文档入库之后，点名表就该空掉（丙5 会盯着它别变成常驻豁免）。
LEGACY_NO_LATIN = {
    "打包说明.md": "交接前写的打包复盘，含 Visual Studio/UPX/UTM 等专名；入库后从本表删",
    "DeepSeek_API使用指南.md": "交接前写的 AI 功能说明，含 ChatGLM/Prompt 等专名",
    "DeepSeek_API智能解析优化总结.md": "交接前写的优化复盘，引用了行业英文关键词表",
    "企知道搜索失败问题修复总结_20261105.md": "交接前写的故障复盘，含 XPath 等术语",
    "验证码处理优化总结_20261105.md": "交接前写的优化复盘，含 Captcha/Prompt 等术语",
}
tracked_set = set(corpus_files)
offenders, latin_offenders, legacy_hits = {}, {}, {}
for p in targets:
    rel = str(p.relative_to(ROOT))
    c, l = scan(p.read_text(encoding="utf-8"), cjk_corpus, latin_corpus)
    if c:
        offenders[rel] = c          # 形近错替与入库与否**无关**，一律判
    if not l:
        continue
    if rel in tracked_set or rel not in LEGACY_NO_LATIN:
        latin_offenders[rel] = l    # 已入库、或未点名 → 照判
    else:
        legacy_hits[rel] = l
check("丙2 中文资产里没有「语料未见」的生面孔汉字（形近错替的唯一机械把手）",
      offenders == {}, {"文件:字": offenders,
                       "处置": "确认是新术语就带理由进 CJK_ALLOWED，是错字就改文档"})
check("丙3 中文资产里没有语料外的裸英文词（夹杂；点名表外的未入库文档同样判）",
      latin_offenders == {}, {"文件:词": latin_offenders,
                             "处置": "该加反引号的加反引号，是术语的进 LATIN_ALLOWED 并写理由"})
check(f"丙4 点名表确实在承托（本次放过 {len(legacy_hits)} 份）：表里每一份都真在扫描范围内，"
      "写错名字的豁免等于无效豁免",
      all(str(ROOT / k).replace(str(ROOT) + "/", "") in {str(p.relative_to(ROOT)) for p in targets}
          for k in LEGACY_NO_LATIN), sorted(LEGACY_NO_LATIN))
check("丙5 点名表不许变成常驻豁免：已经入库的那份必须从表里删掉（留着就是骗人的安全垫）",
      not (set(LEGACY_NO_LATIN) & tracked_set), sorted(set(LEGACY_NO_LATIN) & tracked_set))
for k, why in LEGACY_NO_LATIN.items():
    check(f"丙6 {k} 的豁免带理由（没理由的豁免条目＝下一个人不敢删的沉积物）", bool(why.strip()))

# ── 丁) 结构卫生：文档不许留空段、不许指到不存在的 docs 文件 ────────────────────
broken = []
for p in targets:
    t = p.read_text(encoding="utf-8")
    for m in re.finditer(r"[（(]见\s*`?(docs/[0-9A-Za-z\u4e00-\u9fff_\-]+\.md)`?", t):
        if not (ROOT / m.group(1)).is_file():
            broken.append(f"{p.name} → {m.group(1)}")
check(f"丁1 「见 docs/xxx.md」这类指路全部走得到（读到 {len(targets)} 份里所有指向）",
      broken == [], broken)

# 丁2 的真实目标不是"不许出现 TODO 这个词"。`docs/05` 整份就是**未完成项台账**，
# 它不提"待办"反而不诚实；按禁词判红只会逼人把台账改写成"都完成了"——那是门禁把文档推向谎报。
# 所以改成判那件真正可验证的事：**提到待办的地方必须有内容**（命中行往下到下一个标题之间
# 实质行数 >= 2；表格分隔行与空行不算内容）。
empty_spots = []
for p in targets:
    lines = strip_code(p.read_text(encoding="utf-8")).splitlines()
    for i, ln in enumerate(lines):
        if not re.search(r"(待补|待确认|TODO|FIXME|占位)", ln):
            continue
        body = []
        for nxt in lines[i + 1:]:
            if re.match(r"\s*#{1,4}\s", nxt):
                break
            s = nxt.strip()
            if s and set(s) - set("|-: "):
                body.append(s)
        if len(body) < 2:
            empty_spots.append(f"{p.relative_to(ROOT)}:第{i + 1}行「{ln.strip()[:26]}」"
                               f"往下只有 {len(body)} 行实质内容")
check("丁2 提到待办的地方必须有实质内容（禁的是空段，不是这个词）",
      empty_spots == [], empty_spots)

print(f"\n═══ 文档中文卫生：{_ok} 通过 / {len(_failed)} 失败 ═══")
if _failed:
    print("失败项：")
    for f in _failed:
        print(f"  - {f}")
    sys.stdout.flush()
    os._exit(1)
print(f"全部通过 ✅ ({_ok}/{_ok})")
sys.stdout.flush()
os._exit(0)
