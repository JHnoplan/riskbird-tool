#!/bin/bash
# 离线回归批跑：逐个跑全部离线测试，报 PASS/FAIL/SKIP 与断言数，最后给总判定。
#
# ── 清单从哪来（2026-11-12 改的）────────────────────────────────────────────
# 交付目录（b2bcollect_build / riskbird_build / ali1688_build）里的离线套件按
# **命名约定**自动发现：`test_*_offline.py` 一律进批跑，不需要谁记得登记。
# 为什么不再手写这一栏：riskbird_build 的 13 个套件此前压根不在表里——写完当天跑绿，
# 之后随包代码怎么改都不会红（本轮就是靠手工补跑才发现 4 条已经和现实脱节，其中一条
# 是跨目录同名模块分叉导致的真实上传缺陷）。「新增套件要记得登记」这条要求本身就是
# 缺陷来源：约定 + 一份**带理由的排除表**才能既自动又不漏。
# debug/ 下命名不齐（*_offline_test.py、gui_smoke_test.py、check_gui_fit.py……），
# 仍然手写，但下面的完整性判据会把「磁盘上有、表里没有」当场报红并拒绝开跑。
#
# SKIP 单独计数：部分套件依赖实机取回的夹具（页面/码图，没入库），夹具不在时它必须
# 如实报 SKIP-FIXTURE 而不是 FAIL——把「缺夹具」混进 FAIL，长期红着的一项会淹掉真问题。
# 非断言型的门禁也进这张表（debug/check_gui_fit.py：布局几何体检，退出码就是判定）：
# 「一屏放不下主按钮」这类问题只有量几何才发现得了，而量几何的工具不登记，就会退化成
# 只有改布局那天才会想起来跑一次的私人脚本。
cd /Users/jhnoplan/Desktop/PythonDemo || exit 1
export QT_QPA_PLATFORM=offscreen
PY=.venv/bin/python
# 单套件看门狗超时（秒）：gui_smoke_test 曾因为收尾一个模态弹窗 offscreen 永久等待，
# 把整批回归挂住 20 多分钟。挂死与代码坏是两回事，但都必须当场暴露、不能拖走全批。
PER_TEST_TIMEOUT=${PER_TEST_TIMEOUT:-600}

# 按命名约定自动发现的目录（新增交付目录就在这里加一行）
DELIVERY_DIRS="b2bcollect_build riskbird_build ali1688_build"
# 一个离线套件都没有的交付目录必须写理由：不写就是「约定漏了这个目录」，
# 而漏掉的目录不会报错，只会永远不红（和打包清单 NOT_PACKAGED 同一套做法）。
DIR_WITHOUT_SUITES="ali1688_build|1688 的采集逻辑由 b2bcollect_build/test_integration_offline.py"
DIR_WITHOUT_SUITES="$DIR_WITHOUT_SUITES 与 riskbird_build/test_*_offline.py 覆盖，本目录不另立套件"

# 手写清单：debug/ 下命名不规则的套件 + 不符合 *_offline.py 约定的那一个
FILES="
debug/aipage_offline_test.py
debug/b2b_offline_test.py
debug/block_guard_offline_test.py
debug/contact_field_offline_test.py
debug/contact_selfheal_offline_test.py
debug/edition_offline_test.py
debug/maimaib2b_offline_test.py
debug/solarbe_offline_test.py
debug/solarbe_fullslice_offline_test.py
debug/gui_smoke_test.py
debug/gui_ux_offline_test.py
debug/position_smart_offline_test.py
debug/qihuo_offline_test.py
debug/province_offline_test.py
debug/quality_fix_offline_test.py
debug/test_stats_status_flags.py
debug/verify_gys_offline.py
debug/gi_mp_offline_check.py
debug/gi_pace_offline_test.py
debug/keyword_junk_offline_test.py
debug/local_scan_offline_test.py
debug/qzd_channel_offline_test.py
debug/check_gui_fit.py
b2bcollect_build/test_optimizations.py
"

# 命名匹配但不进批跑的文件：每一项必须带理由。「忘了登记」和「有意不登记」症状完全
# 一样，只有理由能把它们分开——没有理由的排除等于把判据改成沉默。
EXCLUSIONS="
debug/append_offline_14.py|一次性源码补丁脚本：跑一次就往 gi_mp_offline_check.py 里再插一段
debug/append_offline_15.py|同上（它自己不是断言，是被断言的那份套件的写入口）
debug/fix_offline_14_5.py|同上
"

# ── 自动发现 + 完整性判据 ────────────────────────────────────────────────
discovered=""
for d in $DELIVERY_DIRS; do
  if [ ! -d "$d" ]; then
    echo "❌ 交付目录 $d/ 不在了：它的离线套件将整体从批跑里消失（静默少一档回归）"
    exit 1
  fi
  hits=$(ls "$d"/test_*_offline.py 2>/dev/null)
  if [ -z "$hits" ]; then
    case "$DIR_WITHOUT_SUITES" in
      *"$d"*) echo "   ℹ ${d}：没有 *_offline.py 套件（已登记理由）";;
      *) echo "❌ $d/ 里一个 test_*_offline.py 都没有，且没登记理由："
         echo "   是目录改名了，还是套件被删了？要么补套件，要么在 DIR_WITHOUT_SUITES 写清理由"
         exit 1;;
    esac
    continue
  fi
  for p in $hits; do discovered="$discovered $p"; done
done

# 手写清单里若有 *_offline.py 约定命名的项，与自动发现会撞车（同一套跑两遍，
# 断言数翻倍看着像「更严了」，其实只是慢）。这里去重，并让约定命名的项以自动发现为准。
_hand_count=$(printf '%s\n' $FILES | grep -c .)
FILES=$(printf '%s\n' $FILES $discovered | awk 'NF && !seen[$0]++')

# debug/ 那侧仍靠手写，所以补一道完整性判据：磁盘上有、清单里没有 → 不开跑
excluded_paths=$(printf '%s\n' "$EXCLUSIONS" | cut -d'|' -f1)
all_list=" $(printf '%s\n' $FILES $excluded_paths | tr '\n' ' ') "
missing=""
for p in debug/*offline*.py; do
  [ -f "$p" ] || continue
  case "$all_list" in *" $p "*) continue;; esac
  missing="$missing $p"
done
if [ -n "$missing" ]; then
  echo "❌ 回归清单不完整：下面这些离线套件在磁盘上，却既不在 FILES 也不在 EXCLUSIONS"
  for p in $missing; do echo "   · $p"; done
  echo "   加进 FILES（进批跑）或在 EXCLUSIONS 写清为什么不进——「写完只跑一次」等于没有回归"
  exit 1
fi
echo "清单完整性：约定发现 $(printf '%s\n' $discovered | grep -c .) 个（${DELIVERY_DIRS}）" \
     "+ 手写 ${_hand_count} 项 = 合计 $(printf '%s\n' $FILES | grep -c .) 项，" \
     "另有 $(printf '%s\n' "$EXCLUSIONS" | grep -c .) 项按登记排除（都带理由）"

# 单跑几套（排查时不必等全批）：给了 B2B_REGRESSION_ONLY 就以它为准，其余判据
# （清单完整性、计数自洽、总判定进退出码）一条都不少。为什么要开这个口子：批跑入口
# 的退出码是「全量跑绿」唯一的机器口径，而它必须能被用例注入一份**故意会红**的夹具来
# 验活——不能验的判据和没有判据只差一句注释。
if [ -n "${B2B_REGRESSION_ONLY:-}" ]; then
  FILES=$(printf '%s\n' $B2B_REGRESSION_ONLY)
  echo "   ℹ 本轮只跑指定的 $(printf '%s\n' $B2B_REGRESSION_ONLY | grep -c .) 项（B2B_REGRESSION_ONLY）"
fi

# --list：只把解析完的批跑清单打出来（给回归入口自己的离线断言用：判据写在脚本里，
# 核对写在用例里，两边共用同一份展开结果，不重复实现一遍发现逻辑）。
if [ "${1:-}" = "--list" ]; then
  printf '%s\n' $FILES
  exit 0
fi

pass=0; fail=0; skip=0; failed=""; hung=""; total=0
# 有 timeout/gtimeout 就上真实看门狗（SIGABRT + faulthandler 能把挂死点栈打出来），
# 没有也别裸跑：宁可用例慢，也不要一个模态弹窗把整批回归拖死。
TMO=$(command -v timeout || command -v gtimeout)
# 每套件的完整输出都落盘：失败时「tail -N」只能看到最后几行，而断言失败的那一行
# （`  FAIL xxx`）通常埋在中间——上次 gui_store 批跑红 2 条、单跑全绿，就是因为
# 看不到具体哪两条，只能靠猜。留档 + 只打 FAIL 行，才能定位抖动。
#
# 落点可以是私有目录（B2B_REGRESSION_SANDBOX），这一条是本轮自己抓出来的事故：批跑入口
# 一开头就 rm -rf 共享日志目录，而离线用例 5c-II 会**在批跑里面**再调一次本脚本注入
# 夹具——内层那一次把外层已经写好的 31 份日志连同外层正开着的自己的日志一起删了。
# 后果有两层：①排障证据凭空消失（报的是「FAIL」，可完整输出那一行 cat 都 cat 不出来）；
# ②外层那些套件真实是绿的，却因为日志被删被判成红。并发/嵌套互删日志的症状和「代码坏了」
# 完全一样，人就会去改代码——所以每次批跑都有自己的落点，并且用一把锁把「同时两批」堵住。
BASE=${B2B_REGRESSION_SANDBOX:-}
if [ -n "$BASE" ]; then
  CLEAN_BASE=0          # 别人给的路径不做整目录删除：里面可能还有调用方自己的东西
else
  BASE="$(dirname "$0")/regression_logs"
  CLEAN_BASE=1          # 本脚本自己的落点，整目录重建（旧版平铺的 *.log 也一并收掉）
fi
LOG_DIR="$BASE/logs"
LOCK_DIR="$BASE/.lock"
mkdir -p "$BASE" 2>/dev/null || { echo "❌ 落点建不出来：$BASE"; exit 2; }
# 锁：**先抢锁再动目录**，顺序反了就没用了——旧写法是先 rm -rf 再判，嵌套调用只要忘了
# 指定私有落点，就会在被告知「你被挡住了」之前把外层已经写好的日志全删干净。
# 同一落点上同时跑两批 = 互相删对方的证据。取不到锁就当场停，并且说清下一步
# （等它跑完 / 给自己指定私有落点），绝不静默等待——「等一下」在无人值守的批跑里
# 就是「永远卡住」。内层夹具走私有落点，因此不会被外层的锁挡住。
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "❌ 这个落点上已经有一批回归在跑（锁：${LOCK_DIR}）"
  echo "   两批并发会互相删对方的日志：本轮就是这么丢掉了 31 份证据，还把 2 个其实是绿的"
  echo "   套件判成了红。等前一批跑完再重跑；若是离线用例要在批跑里注入夹具，"
  echo "   就给它一个私有落点：B2B_REGRESSION_SANDBOX=\$(mktemp -d)"
  exit 8
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null' EXIT
# 锁在手之后才清日志。注意清的是**日志**，不是落点本身：整目录 rm -rf 会把自己刚建的
# 锁一起删掉，那一瞬间第二个批跑能乘虚拿到锁，两批又开始互删对方的证据——本轮要的正是
# 堵住这一类。默认落点下顺手收掉旧版平铺在 BASE 根的 *.log（换了目录层级，别留遗物）。
if [ "$CLEAN_BASE" -eq 1 ]; then
  rm -f "$BASE"/*.log 2>/dev/null
fi
rm -rf "$LOG_DIR" 2>/dev/null
mkdir -p "$LOG_DIR" 2>/dev/null || { echo "❌ 日志目录建不出来：${LOG_DIR}（判定看不到证据就没法诚实）"; exit 2; }

# ── 开发态状态文件的前后快照对拍（2026-11-13：红的那条不在犯错的套件里）────────────
# 症状是隔壁套件断言 `len(load_tasks()) == 2` 而磁盘上有 8 条。查下去发现：某一路用例
# 的隔离钩子挂在**旧落点**上（`set_desktop_dir` / `tool_dir` 早已不决定任务索引在哪，
# 现行落点问的是 tool_config.state_file），于是它把假任务一条条 upsert 进开发机真实的
# `b2bcollect_build/_采集任务索引.json`。犯错的套件自己一直是绿的——它按 id 找回自己那条
# 就行；被顶红的是下一个，而用户打开源码版工具时会在任务列表里看到一批指向 /tmp 的假会话。
# 这类缺陷不能靠「每个套件都记得钉落点」防：记得一次不算防，忘一次就污染环境且几周看不出来。
# 判据取「代码自己声明的状态文件名」——各 .py 里形如 `X_NAME = "*.json|log|txt"` 的常量，
# 新加一份状态文件就自动进监视范围，不必再维护一份人工清单（清单式的守卫第一次改名就失效）。
# 批跑期间改动过这些文件的，不论出自哪一路套件，都在这里当场判红并点名是哪一份——
# 「谁的用例干的」要能从证据读出来，不许靠人回想这一轮跑了什么。
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# 「代码自己声明的那一半」名单整批取一次就够（离线用例不会改源码），数据目录里现有的
# 文件每次重新走一遍：新增一份必须在下一张快照里现身，否则「多出来的文件」这一类污染
# 正好看不见——本轮那两份「爱企查_结果页证据_*.txt」就是这么冒出来的。
_state_declared_names() {   # $1 = 输出文件
  $PY - "$ROOT" "$1" <<'PY'
import re, sys
from pathlib import Path
root, out = Path(sys.argv[1]), Path(sys.argv[2])
NAME_PAT = re.compile(r'^[A-Z_]*(?:NAME|FILENAME)\s*=\s*[\'"]([^\'"]+\.(?:json|log|txt))[\'"]', re.M)
WATCHED_DIRS = ("b2bcollect_build", "riskbird_build", "ali1688_build")
names = set()
for d in WATCHED_DIRS:
    for py in sorted((root / d).glob("*.py")):
        names.update(NAME_PAT.findall(py.read_text(encoding="utf-8", errors="replace")))
out.write_text("\n".join(sorted(names)) + "\n", encoding="utf-8")
PY
}
_state_snapshot() {   # $1 = 输出文件，$2 = 上面那份声明名单
  $PY - "$ROOT" "$1" "$2" <<'PY'
import hashlib, sys
from pathlib import Path
root, out, declared = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
WATCHED_DIRS = ("b2bcollect_build", "riskbird_build", "ali1688_build")
names = {n for n in declared.read_text(encoding="utf-8").splitlines() if n}
for d in WATCHED_DIRS:                          # 开发态数据目录（Cookie、种子库）
    for sub in sorted((root / d).glob("*_data")):
        for f in sorted(sub.rglob("*")):
            if f.is_file():
                names.add(str(f.relative_to(root / d)))
rows = []
for d in WATCHED_DIRS:
    for name in sorted(names):
        f = root / d / name
        if f.is_file():
            rows.append(f"{f.relative_to(root)}\t{hashlib.sha256(f.read_bytes()).hexdigest()}")
out.write_text("\n".join(rows) + "\n", encoding="utf-8")
PY
}
# 点名哪一份状态文件变了。格式由这份脚本自己定义（上面写出去的 TSV），bash 只把结果
# 原样转给人看：不解析 diff/grep 的行首符号，也就不拿外部工具的输出去猜字节长什么样
# （同一类坑在第 14 道硬门里已经立过判据：按外部工具的输出取值就必须写明格式出处）。
_state_leak_report() {   # $1 = 前一份快照，$2 = 后一份快照
  $PY - "$1" "$2" <<'PY'
import sys
from pathlib import Path


def load(path):
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        name, _, digest = line.partition("\t")
        if name and digest:
            out[name] = digest
    return out


before, after = load(sys.argv[1]), load(sys.argv[2])
for name in sorted(set(before) | set(after)):
    b = before.get(name, "原来没有这份")[:12]
    a = after.get(name, "跑完不见了")[:12]
    if before.get(name) != after.get(name):
        print(f"   · {name}：开工前 {b} → 收工后 {a}")
PY
}
STATE_BEFORE="$BASE/state_before.tsv"
STATE_AFTER="$BASE/state_after.tsv"
STATE_NAMES="$BASE/state_names.txt"
leak=0
leak_suites=""
if ! _state_declared_names "$STATE_NAMES"; then
  echo "❌ 状态文件名单取不了：批跑失去「监视范围从哪来」这一问，这一步不放行"
  exit 2
fi
if ! _state_snapshot "$STATE_BEFORE" "$STATE_NAMES"; then
  echo "❌ 状态文件快照取不了：批跑失去「有没有污染开发机」这一问，这一步不放行"
  exit 2
fi
nwatch=$(wc -l < "$STATE_BEFORE" | tr -d ' ')
echo "状态文件监视：开工前先记下 ${nwatch} 份（每跑完一路套件就对拍一次，改一份就点名是谁干的）"

# 逐套件对拍用的两张快照轮流当「上一张」和「这一张」：只保留一份文件会在下一轮被覆盖，
# 于是「上一张」其实就是这一路套件跑完之后的样子——对拍的就不是这一路干的，而是隔壁。
SNAP_A="$BASE/state_a.tsv"
SNAP_B="$BASE/state_b.tsv"
PREV="$SNAP_A"
CUR="$SNAP_B"
cp "$STATE_BEFORE" "$PREV" 2>/dev/null || { echo "❌ 快照复制不到 ${PREV}：逐套件归属这一步不放行"; exit 2; }

for f in $FILES; do
  total=$((total+1))
  log="$LOG_DIR/$(basename "${f%.py}").log"
  if [ -n "$TMO" ]; then
    # < /dev/null：解释器必须自己带 fd 0。批跑常在后台起、起它的终端随后被回收，那时
    # fd 0 是个已关闭的描述符，python 在初始化阶段就崩（init_sys_streams / Bad file
    # descriptor），日志里只剩上一行标题——硬门第 20 条 20f，夹具见
    # b2bcollect_build/test_frozen_delivery_offline.py 的 AD 节。
    "$TMO" -s ABRT -k 10 "$PER_TEST_TIMEOUT" $PY -u -X faulthandler "$f" >"$log" 2>&1 < /dev/null
  else
    $PY -u -X faulthandler "$f" >"$log" 2>&1 < /dev/null
  fi
  code=$?
  out=$(cat "$log" 2>/dev/null)
  last=$(printf '%s\n' "$out" | grep -E "PASS|FAIL|通过|失败|_OK" | tail -1)
  # 证据必须先在场，判定才有意义。日志不见了（被并发的另一批删了）或压根没写一行
  # （套件静默 exit 0，一句断言都没报）都属于「看不到」，看不到的东西不许算绿。
  if [ ! -f "$log" ]; then
    fail=$((fail+1)); failed="$failed $f"
    printf 'FAIL  %-52s 退出码 %s，但套件日志不在（%s）：没有证据就无法判断它真的跑过\n' "$f" "$code" "$log"
    continue
  fi
  if [ -z "$out" ]; then
    fail=$((fail+1)); failed="$failed $f"
    printf 'FAIL  %-52s 退出码 %s，可日志一个字都没写：一条断言都没报的「绿」没有任何含义\n' "$f" "$code"
    continue
  fi
  # 判定交叉核对：套件自己在汇总里数出来的失败条数，必须和它的退出码一致。
  # 这一条是本轮补的：批跑入口原先无论红多少都 `exit 0`，「全量跑绿」这件事从来没有
  # 机器可判的口径，只有人眼盯着屏幕那一刻算数——而同一套逻辑的坑在同一个套件里已经
  # 抓过一次（poll 脚本静默空跑）。这里只认能明确解析出「N 失败 / 失败 N」的那种汇总，
  # 认不出来的仍按退出码判（宁可少判也不误判：误判的红会让人去改判据，而不是改代码）。
  nfail=$(printf '%s\n' "$out" | grep -oE "[0-9]+ 失败|失败[：: ]*[0-9]+" | tail -1 | grep -oE "[0-9]+" | tail -1)
  if [ $code -eq 0 ] && [ -n "$nfail" ] && [ "$nfail" -gt 0 ]; then
    fail=$((fail+1)); failed="$failed $f"
    printf 'FAIL  %-52s 退出码 0，可套件自己数出了 %s 条失败（判定与退出码不一致，这种绿不算绿）\n' "$f" "$nfail"
    printf '%s\n' "$out" | grep -nE "^  (FAIL|BAD|❌|✗)" | head -20 | sed 's/^/        /'
    printf '        （完整输出：%s）\n' "$log"
  elif [ $code -eq 0 ] && printf '%s\n' "$out" | grep -q "SKIP-FIXTURE"; then
    skip=$((skip+1))
    printf 'SKIP  %-52s %s\n' "$f" "$(printf '%s\n' "$out" | grep 'SKIP-FIXTURE' | head -1)"
  elif [ $code -eq 0 ]; then
    pass=$((pass+1)); printf 'PASS  %-52s %s\n' "$f" "$last"
  elif [ $code -eq 134 ] || [ $code -eq 137 ]; then
    fail=$((fail+1)); failed="$failed $f"; hung="$hung $f"
    printf 'HANG  %-52s 超过 %ss 没跑完（已 SIGABRT，挂死栈见下）\n' "$f" "$PER_TEST_TIMEOUT"
    printf '%s\n' "$out" | tail -22 | sed 's/^/        /'
  elif [ -z "$nfail" ] && printf '%s\n' "$out" | grep -qE "全部 [0-9]+ 项断言通过|ALL ASSERTS PASSED" \
       && ! printf '%s\n' "$out" | grep -qE "^Traceback|^AssertionError"; then
    # 「矛盾」这一类是本轮 gui_smoke_test 的实况：屏幕最后一行写着「全部 54 项断言
    # 通过 ✅」，退出码却不是 0（崩在解释器收尾阶段，Qt/线程的析构与判定毫无关系）。
    # 旧写法把这一行照原样打出来，人看见的是「✅ 却 FAIL」，只能靠猜和重跑——
    # 重跑又是绿的，于是这条真红被当成抖动放过去了。现在直接说清：断言全过，
    # 崩的是收尾，并提醒「判定要与退出码同源」（套件自己收尾 os._exit）。
    fail=$((fail+1)); failed="$failed $f"
    printf 'FAIL  %-52s 断言全过但退出码 %s：崩在收尾阶段，不是判定阶段（套件的退出码必须和断言结果同源）\n' "$f" "$code"
    printf '%s\n' "$out" | tail -6 | sed 's/^/        /'
    printf '        （完整输出：%s）\n' "$log"
  else
    fail=$((fail+1)); failed="$failed $f"; printf 'FAIL  %-52s %s\n' "$f" "$last"
    # 只打红的那几条断言（含前后各一行上下文），完整日志见 LOG_DIR
    # BAD 也要打：check_gui_fit 这类几何体检报的是「BAD 某控件被压扁」，按 FAIL 找
    # 就等于失败了却看不到失败在哪——留档了却不去读，跟没留一样。
    printf '%s\n' "$out" | grep -nE "^  (FAIL|BAD|❌|✗)|^AssertionError|^OSError|^FileNotFoundError" | head -20 | sed 's/^/        /'
    printf '        （完整输出：%s）\n' "$log"
  fi

  # ── 这一路跑完立刻对拍状态文件：污染要报在犯错的套件名下 ──────────────────
  # 只在批跑收尾对拍整批的写法抓得到「有没有」，抓不到「是谁」——上一轮那两份
  # 「爱企查_结果页证据_*.txt」就是这样：屏幕上是「改动了 2 份」，接下来要靠人回想
  # 这一批跑过什么。回想出来的结论没有一条能复跑，所以判据必须自己带名字。
  if ! _state_snapshot "$CUR" "$STATE_NAMES"; then
    echo "   ⚠ $f 跑完之后快照取不了：这一路及其后的归属判断失去证据（总判定仍按收工对拍）"
  else
    if ! cmp -s "$PREV" "$CUR"; then
      leak=1
      leak_suites="$leak_suites $f"
      echo "   ⚑ $f 改动了开发机的状态文件（收工对拍仍按总判定处理这一问）："
      _state_leak_report "$PREV" "$CUR" | sed 's/^/        /' | head -12
    fi
    # 不论有没有改动都要推进「上一张」：不推进的话第一处污染之后的每一路都拿它比对，
    # 同一个元凶会被点名几十次，真正的新增污染反而埋没在重复里。
    cp "$CUR" "$PREV" 2>/dev/null
  fi
done
echo "----------------------------------------------------------------"
# 收工后再取一次快照对拍：这一问与「哪一路套件红了」是两条独立的判据——上一轮两条都绿
# 着跑了几十次，污染却在攒着，直到下一个断言条数的用例被顶红。判据要盯的是环境本身。
if ! _state_snapshot "$STATE_AFTER" "$STATE_NAMES"; then
  echo "❌ 收工后的状态文件快照取不了：这一轮无法回答「有没有污染开发机」"
  leak=1
elif ! cmp -s "$STATE_BEFORE" "$STATE_AFTER"; then
  leak=1
  echo "❌ 批跑改动了开发机的状态文件（离线用例的隔离没接在现行落点上）："
  _state_leak_report "$STATE_BEFORE" "$STATE_AFTER"
  echo "   逐套件归属（⚑ 那些行）已经点名是谁写的：${leak_suites:-未定位到具体套件}"
  echo "   修法：让该套件把落点钉到临时目录（B2B_STATE_DIR），并当场自证 index_path 在它下面；"
  echo "   只改注释或把文件删掉不算修——下一次批跑还会写回来。"
else
  echo "✅ 状态文件对拍：批跑全程没改动开发机的 ${nwatch} 份状态文件（用例的假数据都留在临时目录）"
fi
echo "套件：$pass 通过 / $fail 失败 / $skip 跳过（缺实机夹具），本轮共展开 $total 项"
# 一项都没跑就等于「全绿」：清单解析空了、目录改名了，症状都和「真的全过」一模一样。
if [ "$total" -eq 0 ]; then
  echo "❌ 批跑清单是空的：没有任何套件被执行，这份「全绿」没有任何含义（查 FILES/自动发现两段）"
  exit 1
fi
if [ $((pass + fail + skip)) -ne "$total" ]; then
  echo "❌ 计数不自洽（$pass + $fail + $skip ≠ ${total}）：有一项没被归类，判定不可信"
  exit 1
fi
if [ $fail -eq 0 ] && [ $leak -eq 0 ]; then
  echo "ALL SUITES PASS"
else
  [ -n "$failed" ] && echo "FAILED:$failed"
fi
[ -n "$hung" ] && echo "HANG:$hung"
# 总判定必须进退出码：这一句之前，「跑绿」只有人眼盯屏幕那一次算数，CI 与任何脚本
# 拿到的都是 0——红着 6 条也一样绿。SKIP 不算失败（缺的是实机夹具，不是代码）。
[ $fail -eq 0 ] || exit 1
# 污染开发机单独是一条不放行的红线：套件全绿也拦。上一轮就是「全绿 + 真实索引被写脏」
# 跑了很久，症状推迟到隔壁套件才出现，所以这里不能让它换一种形式继续存在。
if [ $leak -ne 0 ]; then
  echo "❌ 状态文件被改动：本轮即使套件全绿也不放行（写脏开发机的用例迟早红在隔壁套件）"
  exit 1
fi
exit 0
