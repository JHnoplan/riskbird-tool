#!/usr/bin/env python3
r"""secret_gate_offline_test.py —— 给「第 43 条密钥泄漏防线」做判据（含反向对照）。

为什么需要（2026-11-20 交接段）：第 43 条是在我**已经**把「docs/07 这 5 份不含密钥」写成
错话、并且扫描器真抓出一枚 DeepSeek Key 之后加的。一条只跑过一次、抓过一只虫的门，
如果不带判据，下一轮改动就会悄悄失去牙——本仓库反复付的就是这笔学费。

四类坏形状，每条都要有夹具：
  甲 空视野冒充绿：git 挂了 / 扩展名筛歪 → 「没发现密钥」其实是「什么都没读」。
  乙 形态缺牙：六种凭据形态里任一种正则写坏，那一型就永久漏过。
  丙 反向误伤：脱敏标记、占位符、`os.environ.get(...)` 这类合法写法不许响；
     一旦门禁乱咬，人就会去关它——**门禁的可用性本身就是安全性**。
  丁 门禁自身泄密：问题清单里若带密钥原文，CI 日志就成了第二个泄漏点。

夹具一律**程序生成**（见 _tok），不在本文件里落下任何形如 `sk-xxxx...` 的字面量：
否则本判据自己就会被第 43 条顶红（壬 组正面钉住这条自指关系）。

跑法：`.venv/bin/python debug/secret_gate_offline_test.py`（在批跑清单 FILES 里，每轮批跑都跑）。
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / ".github" / "scripts" / "check_packaging_manifest.py"

_ok, _failed = 0, []


def check(label, cond, extra=""):
    global _ok
    if cond:
        _ok += 1
        print(f"  ok {label}")
    else:
        _failed.append(label)
        print(f"FAIL {label} {extra}")


def load_gate():
    spec = importlib.util.spec_from_file_location("_pkg_manifest_gate", str(GATE))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tok(prefix, n=24, step=7):
    """生成一把「形状合法、内容是假的」凭据串：本文件里因此不存在任何字面密钥。"""
    return prefix + "".join(chr(65 + (i * step + 3) % 26) for i in range(n))


g = load_gate()
check("甲0 门禁模块加载成功且第 43 条的把手都在（加载失败时后面全是空闸）",
      all(hasattr(g, n) for n in ("check_history_secrets", "SECRET_PATTERNS", "_secret_scope", "_mask")))

# ── 甲) 真实视野非空 ───────────────────────────────────────────────────────────
scope = g._secret_scope()
check(f"甲1 真实扫描范围非空（本次 {len(scope)} 份「能进 git 历史」的文本）", len(scope) >= 100,
      f"只有 {len(scope)} 份——范围断了的话「没有密钥」这句话没有含义")
check("甲2 扫描范围按扩展名筛过（图片/可执行不许进文本清单）",
      all(p.endswith(g._SECRET_TEXT_EXT) for p in scope))
# 真值落点这一格要三个条件同时成立才叫"两层各管一段"：文件在、确实被 ignore、且确实没进本条范围。
# 上一版这里写成 `not any(p.endswith("config.json"))`，实测被 `screen_translate_config.json`
# （29 字符、不含任何凭据）顶红——那是判据自己筛歪，不是门禁漏了：断言要钉住**那一个**落点。
LANDING = "b2bcollect_build/config.json"
ignored = subprocess.run(["git", "check-ignore", "-q", LANDING], cwd=str(ROOT)).returncode == 0
exists = (ROOT / LANDING).is_file()
# 本条曾多写了一个「文件必须在位」，它在干净 clone 里当场假红：那个前提说的是
# 「这台开发机配过密钥」，不是「交付链正确」——新机器 clone 下来还没录密钥完全合法
# （运行时会 fail-closed 点名缺哪个，那是另一格在守）。而 git check-ignore 对不存在的
# 路径同样能答规则，所以「在不在」对这一格本就是多余信息：只钉【被 ignore + 不进门禁范围】
# 这两件——它们在任何一台机器上都必须成立。
check(f"甲3 真值落点 {LANDING}：确实被 ignore 且不在本条范围内（在位与否只记录不判定："
      f"新机器 clone 后未录密钥是合法态）",
      ignored and LANDING not in scope,
      {"当前状态": "已配置（在位）" if exists else "未配置（干净克隆/新机器）",
       "被ignore": ignored, "在范围内": LANDING in scope})

# ── 乙) 六种形态逐个必须响 ─────────────────────────────────────────────────────
BODY = _tok("", 40)
PRIVATE_KEY_LINE = "-----BEGIN " + "RSA PRIVATE KEY" + "-----"  # 拼接落下，别在本文件里成字面量
CASES = [
    ("DeepSeek / OpenAI 风格", f'KEY = "sk-{BODY}"'),
    ("GitHub 令牌", f'token = "ghp_{BODY}"'),
    ("阿里云 AccessKey ID", f"AK_ID = 'LTAI{BODY[:16]}'"),
    ("AWS 风格 AccessKeyId", f"ak = 'AKIA{BODY[:16].upper()}'"),
    ("私钥文件体", PRIVATE_KEY_LINE),
    ("口令写成字面量", 'password = "' + ("pw" * 12) + '"'),
]
for name, line in CASES:
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "fixture.txt"
        f.write_text("# 这行是判据夹具，不是真凭据\n" + line + "\n", encoding="utf-8")
        got = g.check_history_secrets([f])
        check(f"乙 「{name}」必须响（这一型正则写坏就永久漏过）", len(got) >= 1,
              f"夹具 {line[:28]}… 被判成合格 → 该型没牙")

# 乙8/乙9：`_TAIL` 右边界成对夹具。2026-11-20 实测——抓来的网页里 `$f_lsk-cem…_U-El41…`
# 这种 webpack 标识符会被 sk- 正则整段咬住；解决办法是要求 token「到此为止」。
# 光有「碎片不响」这一条不够：把右边界写坏成 `(?=.)` 也能让它变绿，所以必须同时钉住
# 「同样长度、以引号收尾的真凭据形态依然要响」。
with tempfile.TemporaryDirectory() as td:
    frag = Path(td) / "webpack.js"
    frag.write_text('var x={"$f_lsk-' + BODY + '_U-El41mizDklYzuQo":3694};\n', encoding="utf-8")
    check("乙8 标识符碎片（sk- 后面还接着单词字符）不许当凭据——否则第三方页面会把门禁淹掉",
          g.check_history_secrets([frag]) == [], g.check_history_secrets([frag])[:3])
    real = Path(td) / "quoted.txt"
    real.write_text('KEY = "sk-' + BODY + '"\n', encoding="utf-8")
    check("乙9 成对正向：同样长度、以引号收尾的真凭据形态**必须**响（右边界不许写成恒真）",
          len(g.check_history_secrets([real])) >= 1)

# 乙10：第三方页面层的 NUL 分格——不硬红，但必须被点名计数（悄悄略过就是第 43 条自己的假绿）
with tempfile.TemporaryDirectory() as td:
    page = Path(td) / "saved_page.html"
    page.write_bytes(b"<!DOCTYPE html><p>ok</p>\n" + b"\x00" * 46)
    rep_dict = {}
    got = g.check_history_secrets([page], report=rep_dict)
    check("乙10 第三方页面含 NUL：不硬红（那是用户要保存的采集数据，红它等于逼下一个人关门禁）",
          got == [], got)
    check("乙11 但必须进「扫不动」计数并点名——「没扫」绝不写成「确认没有」",
          rep_dict.get("第三方页面扫不动", []) == [str(page)], rep_dict)
    check("乙12 不传 report 时也不许抛（调用方没兴趣领计数是合法用法）",
          g.check_history_secrets([page]) == [])
    mine = Path(td) / "authored.py"
    mine.write_bytes(b"import os\n" + b"\x00" * 4)
    check("乙13 成对反向：**自家写的**文件含 NUL 必须硬红（分格的分界要两头都有夹具钉着）",
          len(g.check_history_secrets([mine])) >= 1)

check("乙7 形态条数与判据份数自洽：SECRET_PATTERNS 加一型就必须同时补夹具",
      len(CASES) == len(g.SECRET_PATTERNS), f"夹具 {len(CASES)} 条 / 形态 {len(g.SECRET_PATTERNS)} 条")

# ── 丙) 反向对照：合法写法不许响（门禁乱咬 = 人会去关它）──────────────────────
CLEAN = [
    "AK_ID = os.environ.get('OSS_ACCESS_KEY_ID', '')",
    "AK_SECRET = os.environ.get('OSS_ACCESS_KEY_SECRET', '')",
    "# 密钥形如 sk-‹已脱敏：真值只在本机 config.json›，这里只是格式说明",
    'cfg = {"api_key": "your_api_key_here_placeholder"}',
    "print('请 export OSS_ACCESS_KEY_ID / OSS_ACCESS_KEY_SECRET 后重试')",
]
with tempfile.TemporaryDirectory() as td:
    f = Path(td) / "clean.txt"
    f.write_text("\n".join(CLEAN) + "\n", encoding="utf-8")
    got = g.check_history_secrets([f])
    check("丙1 环境变量 / 占位符 / 已脱敏写法不许响（否则文档里提一句格式都红）", got == [], got)

# 丙2/丙3：豁免只认「这串是假的」的强标记；行内出现 < > $ … 之类结构字符**不算**豁免理由。
# 这是本段自查时抓到的门禁自身缺陷：.html 在扫描范围内，而 HTML 里的值必然被尖括号包着，
# 拿整行含 `<` 当豁免 = 整个 HTML 类文件永久免检。
with tempfile.TemporaryDirectory() as td:
    f = Path(td) / "in_html.html"
    f.write_text('<span class="k">sk-' + BODY + "</span>\n", encoding="utf-8")
    got = g.check_history_secrets([f])
    check("丙2 密钥写在尖括号里（HTML/模板的常态）也必须响——整行含 `<` 不是豁免理由",
          len(got) >= 1, got)
with tempfile.TemporaryDirectory() as td:
    f = Path(td) / "tail.txt"
    f.write_text('KEY = "sk-' + BODY + '"  # 后面省略...\n', encoding="utf-8")
    got = g.check_history_secrets([f])
    check("丙3 真值行尾带省略号/变量引用也必须响——「同一行别处有别的东西」不是豁免理由",
          len(got) >= 1, got)

# ── 丁) 门禁自己绝不泄密 ───────────────────────────────────────────────────────
with tempfile.TemporaryDirectory() as td:
    f = Path(td) / "leak.txt"
    secret = "sk-" + BODY
    f.write_text('KEY = "' + secret + '"\n', encoding="utf-8")
    got = "\n".join(g.check_history_secrets([f]))
    check("丁1 问题清单里不许出现密钥原文（CI 日志就是第二个泄漏点）", secret not in got, got[:120])
    check("丁2 但要能定位：留下前缀 + 总长度（够我认出是哪把，不够别人拿去用）",
          secret[:6] in got and str(len(secret)) in got, got[:160])
    check("丁3 报告要带行号（1688 份文件里没行号等于让人全文翻）", ":1 " in got or ":1\t" in got, got[:120])

# ── 戊) 空范围与坏文件都不许静默通过 ──────────────────────────────────────────
check("戊1 显式传空清单必须自红（空视野=空闸，不能报绿）", len(g.check_history_secrets([])) >= 1)
with tempfile.TemporaryDirectory() as td:
    bad = Path(td) / "sub"          # 目录当文件传进去：read_text 抛 OSError
    got = g.check_history_secrets([bad])
    check("戊2 读不出的那份必须点名（读不到 ≠ 没有）", len(got) >= 1, got)
    nul = Path(td) / "nul.txt"
    # NUL 必须和密钥落在**同一行**才摸得到原来那个分支。上一版把 `\x00` 放到第二行，
    # 于是门禁还在"逐行 continue"时这一格就是绿的——判据自己在假绿，比漏检更危险，
    # 因为它让人以为这个口子被钉住了（2026-11-20 自查抓到）。
    nul.write_bytes(b'KEY = "sk-' + BODY.encode() + b'"\x00binary tail\n')
    got = g.check_history_secrets([nul])
    check("戊3 密钥与 NUL 同行也必须响（原先逐行静默跳过＝一个绕过口子；"
          "实测已跟踪文本零 NUL，所以 fail-closed 不会误伤）", len(got) >= 1, got)

# ── 己) 真实仓库当前必须是绿的（这一格红了就是真凭据进来了，立刻作废重发）────────
_real_report = {}
real = g.check_history_secrets(report=_real_report)
check("己1 本仓库「可进历史的」全部文本当前零密钥形态", real == [], real[:6])
_unscanned = _real_report.get("第三方页面扫不动", [])
check(f"己2 真实范围里扫不动的第三方页面共 {len(_unscanned)} 份，全部落在页面扩展名内"
      "（这一格红了说明有**自家写的**东西被当成扫不动悄悄放过）",
      all(p.endswith(g._SECRET_PAGE_EXT) for p in _unscanned), _unscanned)

# ── 壬) 自指：本判据自己不许被门禁顶红 ────────────────────────────────────────
here = Path(__file__).resolve()
check("壬1 本判据文件自己过第 43 条（夹具全靠生成，没落下字面量——否则每轮批跑都被自己顶红）",
      g.check_history_secrets([here]) == [], g.check_history_secrets([here])[:4])
check("壬2 且夹具确实有内容可扫（壬1 不能靠「本文件没被读到」变绿）",
      len(here.read_text(encoding="utf-8").splitlines()) > 80)

print(f"\n═══ 密钥门（第 43 条）判据：{_ok} 通过 / {len(_failed)} 失败 ═══")
if _failed:
    print("失败项：")
    for f in _failed:
        print(f"  - {f}")
    sys.stdout.flush()
    os._exit(1)
print(f"全部通过 ✅ ({_ok}/{_ok})")
sys.stdout.flush()
os._exit(0)
