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

本文件后半的 `己` 节管的是另一件事：**文档抄代码的那一面**（门禁清单、批跑口径）有没有
与代码逐字对拍。甲/乙/丙 管中文用词，丁/戊/己 管文档与现实是否一致。
"""
import ast
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
    # 判据不许把自己的反例吃成语料：本文件里写着错字示例与造出来的假英文词，
    # 它一旦入库，不排除就是「自己发的坏样本把全局语料污染」——
    # 本轮 乙2 不再响直接就是这个原因（zebraquuxfizz 只存在于本文件）。
    here = str(Path(__file__).resolve().relative_to(ROOT))
    files = [x for x in files if x != here]
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
# 语料侧必须与扫描侧**同一个口径**（都过 strip_code）：
# 反引号里的内容在扫描侧永远不报，那它就不该在语料侧发放豁免；
# 否则「把错字示例用反引号括起来」等于给它发一张全球通行证，
# 而 docs/06 正是这么写反例的（本轮 乙1 不再响的直接原因）。
corpus = strip_code(corpus)
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
# 先验反例本身还有效：它们一旦进了语料，下面两条就会「因为语料里已有而不报」——
# 那不是没测出问题，而是**自检已经停转却仍然显示在跑**，比一条 FAIL 更误导人。
check("乙0 两个反例 token 仍在语料之外（否则乙1/乙2 属于假绿，排查时先看本条）",
      "貁" not in cjk_corpus and "zebraquuxfizz" not in latin_corpus,
      {"貁 进了语料": "貁" in cjk_corpus, "zebraquuxfizz 进了语料": "zebraquuxfizz" in latin_corpus,
       "处置": "语料口径被改宽了（查 strip_code 与 tracked_text 里的自排除），先修口径再读乙1/乙2"})
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
# 2026-11-20：5 份已全部入库，表按约定清空。保留空表与下面那段口径，
# 是为了让「以后再点名」必须重新写理由，而不是往现成表里添一行。
LEGACY_NO_LATIN: dict[str, str] = {}
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
      all(k in {p.relative_to(ROOT).as_posix() for p in targets}
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

# ── 戊) 文档不许把「易变计数」抄进正文 ──────────────────────────────────
# 本轮真实踩过：三份文档里抄着「仓库里 223 份跟踪文件」，交接这一轮补入库 28 份
# 之后全过期——而过期那一刻没有任何东西会报红：**散文里的数字没有对账对象**。
# 口径：可以写命令让读者当场跑，不可以替命令回答。
# 为什么是「行级合取」而不是一个相邻搭配的宽正则：旧 docs/07 写的是
#   「层次一：仓库里已跟踪 ──── 223 份」
# 数字与「跟踪」隔着一串破折号，相邻型正则根本抽不到——那正是需要它抽到的那条。
_NFUN = re.compile(r"[0-9][0-9,]{1,8}\s*[份个]")   # 量词一并管：只管「份」会漏掉「png 7,132 个」那一类
_SCOPE_WORDS = ("跟踪", "git clone", "层次一", "层次二", "层次三", "危险区", "可再生")
stale_counts = []
for p in targets:
    for i, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        if _NFUN.search(ln) and any(w in ln for w in _SCOPE_WORDS):
            stale_counts.append(f"{p.relative_to(ROOT)}:第{i}行 {ln.strip()[:64]}")
check("戊1 文档里不许出现「跟踪/层次/危险区 + N 份」这种抄死的计数（要口径就给命令）",
      stale_counts == [], stale_counts)
# 正控：上面那条如果因为正则改坏而恒不匹配，会变成最隐蔽的假绿——它自己必须先能抓到样本。
_prose = "仓库里 223 份跟踪文件才是项目"
_diagram = "层次一：仓库里已跟踪 ──────────── 223 份  ← 这才是「项目」"
_unit = "危险区里 png 7,132 个 + gif 1,216 个"
_hits = [s for s in (_prose, _diagram, _unit) if _NFUN.search(s) and any(w in s for w in _SCOPE_WORDS)]
check("戊2 戊1 的规则对三种真实写法都能开火（叙事体 + 层次图体 + 按量词「个」计数体）；"
      "抽不到＝那条判据是假的", _hits == [_prose, _diagram, _unit],
      {"抓到": len(_hits), "漏了": [s for s in (_prose, _diagram, _unit) if s not in _hits]})

# ── 己) 文档抄代码的那一面：清单不许手抄、口径不许抄死 ───────────────────────────
# 这一节是被本轮的真实缺陷逼出来的：docs/03 §1 那张门名表是**手抄**的，抄的对象是
# `main()` 里的 `check_*` 调用序——每加一道门就漂一次。重生成时实测到已经漂走两处：
# 表里写着 `check_section_pointer_refs`（真名是 `check_section_pointer_references`，
# 照旧表去 grep 会找不到文件），且整表序号与真序错位。`戊` 节管的是散文里的**数字**
# 没有对账对象，清单是同一个病的另一种形状（**名字**没有对账对象），所以分开判。
GATE_DOC = "docs/03-测试门禁与回归批跑.md"
GATE_SCRIPT = ".github/scripts/check_packaging_manifest.py"
_GATE_ROW = re.compile(r"^\s*(\d+)\s+(check_\w+)$")


def _fence_blocks(text):
    """按**行**取代码围栏内容，返回每块的行列表。

    故意不用 ```…``` 一整段匹配的正则：docs/03 第一个块带 `bash` 语言标注，那种正则
    匹配不上它的开栅栏，栅栏奇偶整个错位——实测它对本文命中 0 块。而「0 块」往下走只有
    两种结局，两种都比一条 FAIL 更隐蔽：取 cand[0] 是崩在判据里，对空列表跑 all() 是恒真。
    """
    blocks, cur = [], None
    for line in text.splitlines():
        if re.fullmatch(r"```(\w*)\s*", line):
            if cur is None:
                cur = []
            else:
                blocks.append(cur)
                cur = None
        elif cur is not None:
            cur.append(line)
    return blocks


def _gates_in_main():
    """门禁本体 `main()` 里 `check_*` 的真实调用序（按书写顺序去重）；找不到 main 返回 None。"""
    tree = ast.parse((ROOT / GATE_SCRIPT).read_text(encoding="utf-8"))
    entry = next((n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "main"), None)
    if entry is None:
        return None
    seen = []
    for node in ast.walk(entry):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id.startswith("check_") and node.func.id not in seen):
            seen.append(node.func.id)
    return seen


_gate_lists = [b for b in _fence_blocks((ROOT / GATE_DOC).read_text(encoding="utf-8"))
               if b and all(_GATE_ROW.match(ln) for ln in b)]
check(f"己1 门禁清单能被读出来（{GATE_DOC} 的围栏里恰好 1 个纯 `check_` 清单，实读 {len(_gate_lists)} 个）",
      len(_gate_lists) == 1,
      {"处置": "读不到输入必须响，不许静默按空清单判绿：查表格是否被改成非 `N check_xxx` 形状"
              "、栅栏是否闭合、语言标注行是否顶格"})
_real_gates = _gates_in_main()
check("己2 门禁本体里能取到 `main()` 的调用序（取不到＝本节全部对拍都是空闸）",
      bool(_real_gates), {"实际": None if _real_gates is None else len(_real_gates),
                          "文件": GATE_SCRIPT})
if len(_gate_lists) == 1 and _real_gates:
    _rows = [(_m.group(1), _m.group(2)) for _m in map(_GATE_ROW.match, _gate_lists[0])]
    _doc_names = [n for _, n in _rows]
    _first_diff = next((f"第 {i + 1} 行：文档 `{a}` ≠ 代码 `{b}`"
                        for i, (a, b) in enumerate(zip(_doc_names, _real_gates)) if a != b),
                       f"长度不同：文档 {len(_doc_names)} 条 / 代码 {len(_real_gates)} 条")
    check(f"己3 清单门名序列与 `main()` 调用序逐字相同（共 {len(_real_gates)} 道，顺序也算）",
          _doc_names == _real_gates, {"首个不同": _first_diff,
                                     "只在文档里有": sorted(set(_doc_names) - set(_real_gates)),
                                     "只在代码里有": sorted(set(_real_gates) - set(_doc_names)),
                                     "处置": "重跑 docs/03 §1 那条取数命令，不要手改表"})
    check("己4 清单序号从 1 连续（序号错位＝名字与号各说各话，按号找门会找到隔壁那条）",
          [int(i) for i, _ in _rows] == list(range(1, len(_real_gates) + 1)),
          {"文档行数": len(_rows), "代码门数": len(_real_gates)})

# 己5：禁「替命令回答」的库存口径。为什么不用一个相邻搭配的宽正则：同一句里
# 「第 2 道硬门 `check_shared_source`」是**编号引用**（指向文件头那张编号表的某一条），
# 不是总数；把它一起判红只会逼人把编号引用删掉，那是门禁把文档推向更难读的形状。
# 历史取证里的规模（「DH 节 74 条判据」「9 路变异全开火」）也不管：量词「条/路」后面
# 跟的是「判据/变异」而不是「硬门/批跑」，事件快照本身不需要与今天的机器对账。
_INVENTORY = re.compile(r"\d[\d,]*\s*(道|路|项)\s*(构建前硬门|硬门|门禁|检查|离线回归|回归批跑|批跑)")
_ORDINAL_TAIL = re.compile(r"第[\d\s/、,，\-~—和或及至]*$")
inventory = []
for p in targets:
    for i, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        for m in _INVENTORY.finditer(ln):
            if not _ORDINAL_TAIL.search(ln[:m.start()]):
                inventory.append(f"{p.relative_to(ROOT)}:第{i}行 「{m.group(0)}」 {ln.strip()[:52]}")
check("己5 文档里不许抄死库存口径（「N 道硬门 / N 路批跑 / N 项检查」这类总数要写成取数命令）",
      inventory == [], inventory)
# 正控与豁免正控：两条都必要。少了正控，己5 只要正则改坏就恒不匹配（最隐蔽的假绿）；
# 少了豁免正控，下一轮为了「改好一点」把序数引用一并关掉，视野就静默空了一半。
_bad_writings = ["硬门：42 项检查全部通过才算绿",
                 "跑一遍 66 路离线回归",
                 "三条 workflow 第一步都是 44 道构建前硬门"]
_fired = [s for s in _bad_writings
          if any(not _ORDINAL_TAIL.search(s[:m.start()]) for m in _INVENTORY.finditer(s))]
check("己6 己5 对三种真实坏写法都开火（叙事体 / 命令体 / 总量体）；抽不到＝那条判据是假的",
      _fired == _bad_writings, {"抓到": len(_fired),
                                "漏了": [s for s in _bad_writings if s not in _fired]})
_ordinal_writings = ["——第 2 道硬门 check_shared_source 与尾门",
                     "三处对拍由第 1/2 道硬门负责",
                     "| ④ 第 42 条硬门 + CALLERS/WORKFLOWS 登记 | ✅ |"]
_wrongly = [s for s in _ordinal_writings
            if any(not _ORDINAL_TAIL.search(s[:m.start()]) for m in _INVENTORY.finditer(s))]
check("己7 编号引用不许误伤（「第 N 道」「第 1/2 道」指的是那一条，不是总数）",
      _wrongly == [], _wrongly)

# ── 庚) 「§N」级指路必须落到真实标题 ──────────────────────────────────────
# 丁1 只判「那份文档在不在」，判不到「那一节在不在」。本轮的真实空头支票：docs/02 §5.1
# 写「开启步骤见 `04-…md`」，可 docs/04 里根本没有 required status check 那一节——
# 文件在、内容不在，丁1 结构上全绿。补实那一节之后把指路升级成带章节号的形式，
# 再让下面这条盯着它：以后谁写一句走不到的 §，当场红。
_SECTION_PTR = re.compile(r"§\s*([0-9]+(?:\.[0-9]+)*)")
_PREV_DOC = re.compile(r"(?:docs/)?`?(\d{2})(?:-[0-9A-Za-z\u4e00-\u9fff_.\-]+)?`?|`(\d{2})`")


def _has_heading(text, num):
    """目标文档里有没有编号为 num 的章节标题。

    后面那个字符集是故意列出来的：允许 `## 1. x`（点号分隔）与 `### 3.1 x`，
    但不允许拿 `§1` 匹配到 `## 10`（下一位是数字就否掉）。
    """
    return re.search(r"^#{2,4}\s*" + re.escape(num) + r"(?=$|[\s.、:：,，\-—])", text, re.M) is not None


def _section_pointers(path, text):
    """把这份文档里的 `§N` 解析成 (编号, 目标文档, 是否点名了别的文档)。

    目标文档靠「§ 前 60 字符内出现过的最后一个 NN 号」推断，这是启发式：一句长话里
    先提了 `03` 再写一个属于本文档的 §2，会被算到 03 那边去。它可能让个别指路**查错了
    文档**（两文档章节号大量重合，误判概率低但不为零），所以视野计数交给庚2 单独兜底：
    解析口径一旦整体失效，红的是「读到 0 处」，而不是「全部走得通」。
    """
    out = []
    for m in _SECTION_PTR.finditer(text):
        target, resolved = path, False
        hits = list(_PREV_DOC.finditer(text[max(0, m.start() - 60):m.start()]))
        if hits:
            key = hits[-1].group(1) or hits[-1].group(2)
            found = sorted((ROOT / "docs").glob(f"{key}-*.md"))
            if found:
                target, resolved = found[0], True
        out.append((m.group(1), target, resolved))
    return out


_ptrs, dangling = [], []
for p in targets:
    text = p.read_text(encoding="utf-8")
    for num, tgt, resolved in _section_pointers(p, text):
        _ptrs.append((p, num, tgt, resolved))
        if not _has_heading(tgt.read_text(encoding="utf-8"), num):
            dangling.append(f"{p.relative_to(ROOT)} 的「§{num}」→ {tgt.relative_to(ROOT)} 里没有这一级标题")
check(f"庚1 全部 § 级指路都落到真实存在的章节标题（读到 {len(_ptrs)} 处）",
      dangling == [], {"断链": dangling})
_named = [x for x in _ptrs if x[3]]
check(f"庚2 扫描视野非空（{len(_ptrs)} 处指路、其中点名跨文档 {len(_named)} 处）："
      "读不到输入不许判绿——§ 写法一改这条就会先红，而不是默默变成恒真",
      len(_ptrs) >= 8 and len(_named) >= 3,
      {"读到": len(_ptrs), "跨文档": len(_named)})
# 正控：拿一句真不存在的指路验这台机器会开火（少了它，庚1 只要解析坏就永远绿）
_probe = "本节内指路见 §7。\n开启步骤另见 `04` §99。\n"
_found = _section_pointers(ROOT / "docs/04-打包发布链路与云端配置.md", _probe)
check("庚3 正控：不存在的章节（`04` §99）必须被判为断链，未点名文档的 §7 必须按本文档判为存在",
      [(n, _has_heading(t.read_text(encoding="utf-8"), n)) for n, t, _ in _found] ==
      [("7", True), ("99", False)], _found)
check("庚4 正控的证人本身在位（docs/04 确实有 §7；否则庚3 的「True」是碰运气）",
      _has_heading((ROOT / "docs/04-打包发布链路与云端配置.md").read_text(encoding="utf-8"), "7"),
      "docs/04 的章节结构变了，庚3 的样本要跟着换")

# 庚5：交接文档自己必须在仓库里。这不是凑数的一条：本次交接的对象是**另一个 AI 客户端**，
# 而两个客户端的会话历史与 IDE 记忆实测互相隔离（~/.qoder 与 ~/.qoder-cn）——唯一的桥就是
# 仓库里的文件。桥没入库，交接就不是「信息少一点」，而是**整个通道不存在**。
# 只管 docs/ 与 .qoder/rules/：根目录那批零散 .md（如 `打包说明.md`）有各自的入库判定与
# 台账（docs/05 §3 第 4 项、docs/07 第 2 节），在这里一并判会把已知待办变成常亮故障灯。
untracked_docs = [str(p.relative_to(ROOT)) for p in targets
                  if (p.relative_to(ROOT).as_posix().startswith("docs/")
                      or p.relative_to(ROOT).as_posix().startswith(".qoder/rules/"))
                  and p.relative_to(ROOT).as_posix() not in tracked_set]
check(f"庚5 交接文档与 IDE 规则全部已入 git（读到未跟踪 {len(untracked_docs)} 份）——"
      "接手方看不到会话历史，只看得见仓库；桥没入库等于交接通道不存在",
      untracked_docs == [], {"未跟踪": untracked_docs,
                            "处置": "git add <那份文档>（点名 add，不许 -A）后提交"})
_bridge_scope = [p for p in targets
                 if p.relative_to(ROOT).as_posix().startswith(("docs/", ".qoder/rules/"))]
check(f"庚6 庚5 的视野非空（本次扫到桥上的文档 {len(_bridge_scope)} 份：docs/ + .qoder/rules/）——"
      "这一份为庚5 兜底：目标口径一旦改坏（比如 glob 失效），未跟踪列表会变成空的而看起来像全绿",
      len(_bridge_scope) >= 12, {"读到": [str(p.relative_to(ROOT)) for p in _bridge_scope]})

# ── 辛) 已知错字样本黑名单：每一轮人工抓到的形近错替都固化成不可回退的一条 ──────
# 为什么丙2 不够：它只报「仓库从没用过的字」。2026-11-20 本轮我在 docs/08 里写了
# 「拒绍放行」（绍→绝），而「绍」在两份 customer_library_cleaner.py 里早就在用——
# 于是丙2 当场 29/0 全绿放行。这类错字的字本身合法，错的是位置，**只能靠样本名单**。
# 同一形状已经在本仓库反复出现，不先把它们钉住，下一轮还会从手里漏过去。
KNOWN_TYPOS: dict[str, str] = {
    "拒绍": "拒绝（本轮 docs/08；丙2 实测放过的那一条）",
    "跑断据": "跑判据（本轮 docs/06 §11）",
    "兵底": "兜底（本轮 docs/06 §11）",
    "一声不呐": "一声不响（本轮 docs/06 §11）",
    "只取不拄": "只取不抄（本轮 docs/06 §11）",
    "噪咅": "噪音（本轮；上一版先误改成「噪响」才改对——连改错字都能再错一个）",
    "貁免": "豁免（docs/06 第 5 节记录的三次之一）",
    "进住": "接住（同上）",
    "讫不": "绝不（同上）",
    "只抱一把": "只靠一把（本轮写 `06` 第 12 节标题时犯的）",
    "拓不到": "抓不到（同上；形近且都是常用字）",
    # 下面三条是“入库之后又推到现实里才发现”的：它们都不是生僻字，所以丙2 看不见；
    # 实测语料里「自名单」「捧」各 0 处、「扇生面孔」这个短语从未存在——进这里才不会静默复发。
    "自名单": "白名单（本轮 docs/04 那个 ❗ 注；形近且全是常用字，丙2 天然放过）",
    "捧住": "拦住／逮住（本轮 docs/05 §3 第 11 项；「字合法、位置错」正是丙2 看不见的形状）",
    "扇生面孔": "扫生面孔（本轮写 docs/06 §13 时由编辑工具变形引入——说明每一笔新写入都要重跑本判据）",
    "拄住": "拦住／抓出来（同上：同一个“接→进／拦→拄”家族，写下一句时当场又错一次）",
}


def strip_real_code(text):
    """只剥「真的是代码」的那部分：```bash / ```python 等带语言标的围栏 + 行内反引号。

    为什么辛 不能用上面那个 strip_code：变异台实测（本轮 M1）——docs/08 的开场话术整块写在
    ```text 围栏里，而 `strip_code` 不分语言一律剥掉，于是「拒绍」注进话术本体后辛1 一声不响。
    那份块是**给人（和接手会话）读的正文**，只是形式上套了围栏；拿处理代码的口径去处理
    它，等于给交接话术本身开了一个免疫区。口径：`text` 与空语言标的围栏算正文，其余算代码。
    """
    out, fence = [], None
    for line in text.splitlines(keepends=True):
        m = re.match(r"\s*```\s*([A-Za-z0-9_+\-]*)\s*$", line)
        if m and fence is None:
            fence = m.group(1).lower()
            continue
        if m and fence is not None:
            fence = None
            continue
        if fence is not None and fence not in ("", "text"):
            continue
        out.append(line)
    return re.sub(r"`[^`\n]*`", " ", "".join(out))


def typo_hits(text):
    """正文里命中的已知错字（口径见 strip_real_code：话术块算正文，真代码块与行内反引号不算）。

    行内反引号仍豁免：文档里拿反引号引着的「错字示例」（如 docs/06 那句「豁→`貁`」）是
    **被讨论的字符**，不是正文写错，不该把自己的事故记录判成故障。
    """
    stripped = strip_real_code(text)
    return [bad for bad in KNOWN_TYPOS if bad in stripped]


_typo_hits = {str(p.relative_to(ROOT)): v for p in targets
              if (v := typo_hits(p.read_text(encoding="utf-8")))}
check(f"辛1 中文资产里没有已知错字样本黑名单里的字（收录 {len(KNOWN_TYPOS)} 条，每条都带本轮出处）",
      _typo_hits == {}, {"文件:命中": _typo_hits,
                        "处置": "按字典里的正确写法改文档；改完再跑本判据"})
# 正控：逐条把样本喂给判定本身——不生下来命中一条的黑名单就是装饰（本项目反复出现的假绿形状）
_fake_hits = {bad: typo_hits(f"这段中文里把词写坏了：{bad}，后面还有正文。")
              for bad in KNOWN_TYPOS}
check("辛2 正控：黑名单里每一条字面都必须能把自己判出来（抽不到＝那条规则永不命中）",
      all(v == [bad] for bad, v in _fake_hits.items()),
      {k: v for k, v in _fake_hits.items() if v != [k]})
check("辛3 正控的夹具不污染真判定：那些假句子只存在于内存里，一份真文档都没被写进样本句",
      not any("这段中文里把词写坏了" in strip_real_code(p.read_text(encoding="utf-8")) for p in targets),
      "假样本泄进了真文档，两条判据从此互相掩盖")
# 辛5 负控（与辛2 方向相反）：辛2 管「条目永不命中」（空名单也能过），辛5 管「条目过宽」。
# 底文是一句**不含任何错字**的真中文，它必须一个都不命中；命中了就是名单里有合法词当错字
# （那种名单会把人直接淹没，然后被关掉——噪音过多的门禁比没有门禁更糟）。
_CLEAN_PROSE = "这段中文写得完全正确：打包链路拒绝放行，判据也必须真的跑过一遍。"
_wide = typo_hits(_CLEAN_PROSE)
check("辛5 负控：一段干净的中文正文必须一个都不命中（命中＝那条名单把合法词当成了错字）",
      _wide == [], {"误杀": _wide, "底文": _CLEAN_PROSE})
# 辛6 辛5 的底文本身要能承托它判的那两件事：它既得含「拒绝」（防有人把合法词列进名单），
# 又得真的一句都不命中（否则上面那条是在判空气）。没有这一格，辛5 的「空」可能是底文写空了。
check("辛6 辛5 的底文真在判事：里面确实出现了「拒绝／判据」这类高频词（底文一旦写空，辛5 就变成装饰）",
      "拒绝" in _CLEAN_PROSE and "判据" in _CLEAN_PROSE and len(_CLEAN_PROSE) >= 20, _CLEAN_PROSE)
# 辛4 把「为什么需要第二把把手」钉成可执行事实：拿本轮那条真实事故串去问丙2 的语料，
# 如果哪天「绍」变成了生面孔（语料口径改了、或仓库重组了），丙2 就能自己管住它，
# 那时这条辛4 会红——提醒重新读一遍两把把手的分工，而不是默默多一条无用规则。
check("辛4 本轮那条被丙2 放过的错字，其错用字确实在既有语料里（这就是辛存在的理由）",
      "绍" in cjk_corpus,
      "「绍」已不在语料里：丙2 现在能自己抓住它了，请重新评估辛4 要不要跟着换样本")
# 辛7 辛1 的视野非空：`strip_real_code` 一旦碰上不配平的围栏，fence 会一直开着并把后面全文
# 当代码剥掉——那时辛1 读到的是空气而看起来全绿。拿剥完还剩多少字符当证人，口径同庚2。
_in_scope = sum(len(strip_real_code(p.read_text(encoding="utf-8"))) for p in targets)
_raw = sum(len(p.read_text(encoding="utf-8")) for p in targets)
check(f"辛7 辛1 真读到东西（剥完代码后还剩 {_in_scope} / {_raw} 字符）：视野塌了不会假装全绿",
      _in_scope >= 4000 and _in_scope * 4 >= _raw,
      {"读到": _in_scope, "原文": _raw,
       "处置": "检查是否有不配平的 ``` 围栏，或语言标口径被改坏"})
# 辛8 把豁免边界本身写成可执行判据（本轮变异台实测：拿真文档当锚点靠不住——
# docs/08 §2 那个 ```bash 块**嵌在 ```text 话术块里面**，按 Markdown 真实语义它本来就还是
# 话术文字，于是「不该报」的期望从一开始就错了）。这里改用自造样本逐块判，不依赖仓库写法。
check("辛8 豁免边界：顶层代码块不判、正文与 ```text 话术块都判（三种形状各自对拍）",
      [typo_hits("```bash\n这里面的拒绍不该报\n```"),
       typo_hits("正文里写了一个拒绍。"),
       typo_hits("```text\n话术块里的拒绍要报\n```")]
      == [[], ["拒绍"], ["拒绍"]],
      {"代码块": typo_hits("```bash\n拒绍\n```"),
       "正文": typo_hits("正文里写了一个拒绍。"),
       "话术块": typo_hits("```text\n拒绍\n```")})

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
