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
# 仓库根从脚本自己的位置推：写死某台机器的绝对路径，换一台（含 CI 打包机）第一行就 exit，
# 而这几个脚本正是「交付链可复现」唯一的机器口径。
# 调用方的 shell 选项不许决定本脚本的生死（2026-11-14 Windows 云端实发，本机已复现同签名）：
# Actions 的 bash 步是 `bash --noprofile --norc -e -o pipefail` 起 python，python 再拉一个
# bash 跑这一份——继承下来的 errexit 让脚本在第 78 行 ``hits=$(ls "$d"/test_*_offline.py
# 2>/dev/null)`` 那一句**一声不响地退出**（ali1688_build 天生没有 *_offline.py，ls 返回非零，
# 而它的报错被 2>/dev/null 吃掉了）：退出码 1、stdout 0 字节、stderr 0 字节。本机
# ``bash -e -o pipefail debug/run_offline_regression.sh --list`` 复现出的签名与云端逐字节一致，
# 而 mac CI 同一条命令是绿的（/bin/bash 3.2 不把 -e 传给子 bash，Git Bash 会）——**同一份脚本、
# 一个平台红一个平台绿**，差的不是代码，是拉起它的那个人带了什么选项。
# 这份脚本的契约是「拒绝开跑必须说清为什么」，静默退出正好把契约反着写，所以第一句先把继承来
# 的选项关掉；下面的失败处理一律走显式 `|| { echo 为什么; exit N; }`，不拿 errexit 当错误处理。
set +e
set +o pipefail
cd "$(dirname "$0")/.." || { echo "❌ 进不去仓库根（$(dirname "$0")/..）：下面每一步都按仓库根的相对路径找 .venv 和 debug/"; exit 2; }
export QT_QPA_PLATFORM=offscreen
PY=${PY:-.venv/bin/python}
# 单套件看门狗超时（秒）：gui_smoke_test 曾因为收尾一个模态弹窗 offscreen 永久等待，
# 把整批回归挂住 20 多分钟。挂死与代码坏是两回事，但都必须当场暴露、不能拖走全批。
PER_TEST_TIMEOUT=${PER_TEST_TIMEOUT:-600}
# 看门狗预算按套件给一份（2026-11-20 本机实测）：`test_frozen_delivery_offline.py` 是全部
# 66 路里最重的一份，单跑就要 529 秒（3176+ 条判定，内含全仓 ast.parse 与多次子进程实跑），
# 600 秒的统一预算只剩 13% 余量：只要机器上还有第二个活（我这次是并发了几个取证探针），
# 它就越线被 124 打断，报成 HANG、栈停在 `ast.parse`/GC——那是「跑得慢」被读成「挂死」，
# 照着那句去找不存在的死循环会白费一整轮。只给这一路加预算，其余一律仍按 600 秒：
# 这个上限的存在意义就是抓真挂死（2026-11-12 整批回归被一次模态弹窗拖死正是这么抓到的），
# 全都放宽等于把报警器关掉。
HEAVY_TEST_TIMEOUT=${HEAVY_TEST_TIMEOUT:-1500}

# 按命名约定自动发现的目录（新增交付目录就在这里加一行）
DELIVERY_DIRS="b2bcollect_build riskbird_build ali1688_build"
# 一个离线套件都没有的交付目录必须写理由：不写就是「约定漏了这个目录」，
# 而漏掉的目录不会报错，只会永远不红（和打包清单 NOT_PACKAGED 同一套做法）。
DIR_WITHOUT_SUITES="ali1688_build|1688 的采集逻辑由 b2bcollect_build/test_integration_offline.py"
DIR_WITHOUT_SUITES="$DIR_WITHOUT_SUITES 与 riskbird_build/test_*_offline.py 覆盖，本目录不另立套件"

# 手写清单：debug/ 下命名不规则的套件 + 不符合 *_offline.py 约定的那一个
FILES="
debug/aipage_offline_test.py
debug/acl_note_panel_offline_test.py
debug/b2b_offline_test.py
debug/block_guard_offline_test.py
debug/chrome_env_offline_test.py
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
debug/update_offline_test.py
debug/test_stats_status_flags.py
debug/verify_gys_offline.py
debug/gi_mp_offline_check.py
debug/gi_pace_offline_test.py
debug/keyword_junk_offline_test.py
debug/local_scan_offline_test.py
debug/qzd_channel_offline_test.py
debug/batch_runner_offline_test.py
debug/check_gui_fit.py
debug/qoder_rules_offline_test.py
debug/doc_hygiene_offline_test.py
debug/secret_gate_offline_test.py
b2bcollect_build/test_optimizations.py
"

# 命名匹配但不进批跑的文件：每一项必须带理由。「忘了登记」和「有意不登记」症状完全
# 一样，只有理由能把它们分开——没有理由的排除等于把判据改成沉默。
EXCLUSIONS="
debug/append_offline_14.py|一次性源码补丁脚本：跑一次就往 gi_mp_offline_check.py 里再插一段
debug/append_offline_15.py|一次性源码补丁脚本：同上一族，往 gi_mp_offline_check.py 追加 ⑮ 段
debug/fix_offline_14_5.py|一次性源码补丁脚本：同上三族，改写 gi_mp_offline_check.py 里已有的 ⑭-5 段
debug/offline_env_probe.py|不是断言套件而是现场探针本体（「这份解释器跑得动吗」那一问的唯一实现）；它的判据在 debug/batch_runner_offline_test.py 里，而它自己已被开跑前那一步真跑过一次
"

# ── 自动发现 + 完整性判据 ────────────────────────────────────────────────
discovered=""
for d in $DELIVERY_DIRS; do
  if [ ! -d "$d" ]; then
    echo "❌ 交付目录 $d/ 不在了：它的离线套件将整体从批跑里消失（静默少一档回归）" >&2
    exit 1
  fi
  # 用 bash 自己的 glob 发现套件，不 `ls`：①ls 在「没有匹配」时返回非零，把整份脚本的生死
  # 交给一条外部命令的退出码（本轮云端就死在这上面）；②解析 ls 的输出是反模式——真有个文件名
  # 带空格，这里就会把它劈成两项，报成「清单里有套件在磁盘上不存在」。
  # 不开 nullglob：没匹配时 bash 会留下字面量本身，下面用 [ -f ] 当场判掉，改完全局 shopt
  # 会跟着影响同一进程里后面的每一段（脚本被 source 时尤其脏）。
  hits=""
  for p in "$d"/test_*_offline.py; do
    [ -f "$p" ] || continue
    hits="$hits $p"
  done
  if [ -z "$hits" ]; then
    case "$DIR_WITHOUT_SUITES" in
      *"$d"*) echo "   ℹ ${d}：没有 *_offline.py 套件（已登记理由）" >&2;;
      *) echo "❌ $d/ 里一个 test_*_offline.py 都没有，且没登记理由：" >&2
         echo "   是目录改名了，还是套件被删了？要么补套件，要么在 DIR_WITHOUT_SUITES 写清理由" >&2
         exit 1;;
    esac
    continue
  fi
  for p in $hits; do discovered="$discovered $p"; done
done

# 手写清单里若有 *_offline.py 约定命名的项，与自动发现会撞车（同一套跑两遍，
# 断言数翻倍看着像「更严了」，其实只是慢）。这里去重，并让约定命名的项以自动发现为准。
_hand_count=$(printf '%s\n' $FILES | grep -c . || true)
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
  echo "❌ 回归清单不完整：下面这些离线套件在磁盘上，却既不在 FILES 也不在 EXCLUSIONS" >&2
  for p in $missing; do echo "   · $p" >&2; done
  echo "   加进 FILES（进批跑）或在 EXCLUSIONS 写清为什么不进——「写完只跑一次」等于没有回归" >&2
  exit 1
fi
# 清单里有、磁盘上没有 → 不开跑，并点名下一步。「少跑一路」不会让任何计数变红：
# 那一路的缺陷从此没有守卫，日志里也连一行缺失都不留——本轮 5 份套件只活在这台机器上
# （CI 检出的仓库里压根没有），批跑照跑、总数照对，症状和「跑全了」完全一样。
absent=""
for p in $FILES; do
  [ -f "$p" ] || absent="$absent $p"
done
if [ -n "$absent" ]; then
  _absent_n=$(printf '%s\n' $absent | grep -c . || true)
  echo "❌ 批跑清单里有 ${_absent_n} 份套件在磁盘上不存在：" >&2
  for p in $absent; do echo "   · $p" >&2; done
  echo "   要么文件被删/改名了，要么这一路本来就登记错了。缺失的那一路永远不会红，" >&2
  echo "   只会让这一类缺陷从此没有守卫，所以这里拒绝开跑，不当它不存在。" >&2
  exit 1
fi

# 三处 grep -c 都带 `|| true`：`discovered` 在「三个交付目录一个套件都没有」时是空的，
# 那种情形这一行正是唯一能说清「范围为什么空了」的一句话，不能被 grep 的退出码提前带走。
echo >&2 "清单完整性：约定发现 $(printf '%s\n' $discovered | grep -c . || true) 个（${DELIVERY_DIRS}）" \
     "+ 手写 ${_hand_count} 项 = 合计 $(printf '%s\n' $FILES | grep -c . || true) 项，" \
     "另有 $(printf '%s\n' "$EXCLUSIONS" | grep -c . || true) 项按登记排除（都带理由）" >&2

# 单跑几套（排查时不必等全批）：给了 B2B_REGRESSION_ONLY 就以它为准，其余判据
# （清单完整性、计数自洽、总判定进退出码）一条都不少。为什么要开这个口子：批跑入口
# 的退出码是「全量跑绿」唯一的机器口径，而它必须能被用例注入一份**故意会红**的夹具来
# 验活——不能验的判据和没有判据只差一句注释。
if [ -n "${B2B_REGRESSION_ONLY:-}" ]; then
  FILES=$(printf '%s\n' $B2B_REGRESSION_ONLY)
  echo "   ℹ 本轮只跑指定的 $(printf '%s\n' $B2B_REGRESSION_ONLY | grep -c . || true) 项（B2B_REGRESSION_ONLY）" >&2
fi

# --list：只把解析完的批跑清单打出来（给回归入口自己的离线断言用：判据写在脚本里，
# 核对写在用例里，两边共用同一份展开结果，不重复实现一遍发现逻辑）。
# --list 的契约：stdout 每行一个**仓库相对的套件路径**，别的什么都没有（进度与诊断走
# stderr）。硬门第 23/24/25 条与离线对拍读的就是这批字节，格式由下面这一行 printf 决定——
# 改了它，那两处的判据必须一起改，这也是把契约写在出口旁边而不是别处的原因。
#
# 第二条通道（B2B_REGRESSION_LIST_FILE）为什么必须有（2026-11-15 Windows 云端实发）：
# 门禁拿这份清单**只靠一条管道**。Windows runner 上报回来的签名是「退出码 1｜stdout
# 504 字节 / 11 行｜stderr 0 字节」，而这一份脚本在 --list 之前一个字都不往 stdout 写、
# 每一条 exit 1 都必写 stderr——两个事实互相排斥，说明**取证据的那条通道自己不可信**，
# 而不是脚本活着却撒了谎（本机把整份脚本改成 CRLF 复现过：签名是 rc=2/stdout 0 字节/
# stderr 360 字节，对不上，行尾不是这一次的凶手）。拿一条管道的行为去推断另一个平台的
# 代码，推断出来的结论没有一条能复跑。
# 所以清单同时写进调用方指定的文件：文件通道不经受 stdout/stderr 的归属、控制台伪终端、
# 参数转换这些平台差异，写成功就是这一份展开结果本身。写不进去必须当场响并退出——静默
# 降级就等于「只有那条可疑的管道能说明范围」，那正是本轮要摆脱的状态。
if [ "${1:-}" = "--list" ]; then
  if [ -n "${B2B_REGRESSION_LIST_FILE:-}" ]; then
    if ! printf '%s\n' $FILES > "$B2B_REGRESSION_LIST_FILE"; then
      echo "❌ 清单写不进 B2B_REGRESSION_LIST_FILE=${B2B_REGRESSION_LIST_FILE}：调用方要的是这份" \
           "展开结果，写不进文件就不能只把 stdout 当唯一凭据交出去" >&2
      exit 1
    fi
  fi
  printf '%s\n' $FILES
  exit 0
fi

# ── 空清单的早退：排在 --list 之后、探针之前（2026-11-19 本轮实发）────────────
# 顺序错了会把诊断换掉：往下走的话探针读到的还是这同一份空清单，于是回「范围断了、问不出
# 依赖」那一格（退出码 2），屏幕上只剩「现场不齐」的话术——而真正要查的是 FILES 手写表 /
# DELIVERY_DIRS 自动发现 / 本轮传入的 B2B_REGRESSION_ONLY。这一格红过一次就是这么被读坏的：
# 症状和「环境坏了」逐字节一样，修的人却去补包。
# 放在 --list 之后是契约：--list 只如实交出展开结果（空就空着，不替调用方下判定），
# 「空范围不许开跑」这条决定留给真跑批的这一层，与上面注释里「判定和证据分开两个通道」一致。
if [ "$(printf '%s\n' $FILES | grep -c . || true)" -eq 0 ]; then
  echo "❌ 批跑清单是空的：没有任何套件被执行，这份「全绿」没有任何含义"
  echo "   查 FILES 手写表与 DELIVERY_DIRS 自动发现两段"
  if [ -n "${B2B_REGRESSION_ONLY:-}" ]; then
    # %q 而不是直接打原值：本轮那次注入的就是一个**空格**，直接打出来与没打一样，
    # 看屏幕的人会以为变量是空的。
    echo "   本轮走了 B2B_REGRESSION_ONLY 口子（$(printf '%q' "${B2B_REGRESSION_ONLY}")）：它以它为准，其余判据一条不少"
  fi
  exit 1
fi

# 环境检查为什么必须排在 --list 之后：--list 不跑任何套件，它只是把清单声明读出来，
# 而硬门第 23/24 条与离线对拍正是靠这一句拿「批跑到底覆盖哪几路」的权威答案。把它挡在
# 解释器检查后面，等于让门禁的结论取决于跑不跑得动——那是两种完全不同的红。

# 解释器必须先在场，并且**跑得动本轮全部离线套件**：批跑入口退到系统 python3 的话，几十套会红成
# ModuleNotFoundError，看着像代码坏了，其实是环境不对——「证据先在场，判定才有意义」这条也管环境。
# 这一问从 2026-11-19 起交给探针（debug/offline_env_probe.py），不再在这里写死 `import PyQt5`：
# 写死的那一版漏掉了 PIL——它正是「平行会话 → makepolo_crawler → mk_phone_ocr」这条链上会执行的
# 一句 `from PIL import Image`，结果那一轮满屏假红被当成业务缺陷去查根因。探针的范围取自**本脚本
# 自己的批跑清单**（--list），依赖取自**那份清单里每一份套件的 import 闭包**：两处都不再抄第二份名单。
if [ ! -x "$PY" ]; then
  echo "❌ 找不到可执行的 ${PY}（仓库根 $(pwd)）：先 python3 -m venv .venv && .venv/bin/pip install -r .github/requirements/b2bcollect.txt"
  exit 2
fi
# 探针文件本身不在位必须单独报（与下面四种结论都不同）：`python <不存在的文件>` 的退出码
# 也是 2，而 2 在本脚本里表示「批跑清单取不到」。少这一句，缺文件会红成「范围断了」，修的人
# 去查 FILES 手写表——而真正缺的是 debug/offline_env_probe.py 没跟着提交（CI 检出正是这一形：
# 它不在这轮新加的批跑清单里，而在排除表里，第 24 条那份“套件都要已提交”的门照不到它）。
if [ ! -f debug/offline_env_probe.py ]; then
  echo "❌ 现场探针不在位：debug/offline_env_probe.py 读不到"
  echo "   本机有、提交里没有的话，先 git add debug/offline_env_probe.py && git commit；"
  echo "   它不在批跑清单里（是前置工具不是断言套件），但批跑入口第一句就要拿它问现场"
  exit 2
fi
# `|| probe_rc=$?`：退出码必须在接管它的那一句里取（第 15 条），本脚本随时可能被带着
# errexit 的调用方拉起来。`< /dev/null`：同第 20 条 20f，后台起批跑时父终端被回收会让
# fd 0 是个已关闭的描述符，python 在跑第一行之前就崩——那会被读成「现场不齐」。
probe_rc=0
"$PY" debug/offline_env_probe.py --check < /dev/null || probe_rc=$?
# 四种结论分开接，不退成一个词（本轮要的正是这一格）：1 缺依赖 / 2 清单取不到 /
# 3 探针自己起不来。后两种尤其不许按「没报缺 = 现场齐」继续开跑：那一路的证据全没了。
if [ $probe_rc -eq 2 ]; then
  echo "❌ 批跑清单取不到，「现场齐不齐」这一问答不了："
  echo "   先修 debug/run_offline_regression.sh --list 那一步（上面已经给了两条通道的原文），别去看套件红不红"
  exit 2
fi
if [ $probe_rc -eq 3 ]; then
  echo "❌ 现场探针自己没跑成（退出码 3）：连「缺不缺」都没问出口，不许按现场齐继续开跑"
  exit 2
fi
if [ $probe_rc -ne 0 ]; then
  echo "❌ ${PY} 跑不动本轮离线套件：把上面点名的那几只补齐，或 PY=/path/to/python 换一份解释器"
  exit 2
fi

pass=0; fail=0; skip=0; failed=""; hung=""; total=0
# 有 timeout/gtimeout 就上真实看门狗（SIGABRT + faulthandler 能把挂死点栈打出来），
# 没有也别裸跑：宁可用例慢，也不要一个模态弹窗把整批回归拖死。
# `|| true`：这台机器上两个名字都没有时，这一行的状态就是 1——而本脚本随时可能被一个带着
# errexit 的调用方拉起来（本轮 Windows 云端就是这么死的）。取不到看门狗下面有明确的降级路，
# 不该由这一句的退出码决定整批跑不跑。
TMO=$(command -v timeout || command -v gtimeout || true)
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
import hashlib, os, sys
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
visited = 0
for d in WATCHED_DIRS:
    base = root / d
    if not base.is_dir():
        continue
    # 扫树筛名字，而不是拿声明的裸名去拼路径。旧写法只看得到目录第一层
    # （`root / d / 裸名`），于是**住在子目录里的状态文件**从来没进过快照：
    # b2bcollect_build/acl/.acl_cache.json 就是这么躲过对拍的——2026-11-20 批跑
    # 中途 check_gui_fit.py 用本机真凭据拉回真名单写脏了它，屏幕上却写着
    # 「没改动 70 份」——一条只会说「没事」的门比没有门更糟，因为它就是用来
    # 让人安心跳过这一问的。
    for dirpath, _dirnames, filenames in os.walk(base):
        visited += len(filenames)
        for fn in filenames:
            f = Path(dirpath) / fn
            rel = f.relative_to(base).as_posix()
            if rel in names or fn in names:
                try:
                    digest = hashlib.sha256(f.read_bytes()).hexdigest()
                except OSError as e:        # 读不动也要进表：宁可报成「变了」也不许静默溜过
                    digest = f"读不到:{type(e).__name__}"
                rows.append(f"{f.relative_to(root).as_posix()}\t{digest}")
rows.sort()
# 三种「扫不到东西」必须分开说，它们是完全不同的事：
#   · 名单空 / 走了三个目录却一个文件都没碰到 → 门的口径断了，这一轮不算做过对拍；
#   · 口径没断、只是本机确实还没有任何运行态文件（干净克隆的第一轮就是这样：
#     acl/ 与 *_data/ 都是跑起来才建的）→ 合法现场，红它等于把新机器第一次批跑
#     拦在门外。但这句话必须上屏：只默默往表里写 0 行，下一个人分不清
#     「还没有」与「没在看」，那与旧写法的空视野假绿只差一层窗户纸。
if not names:
    sys.stderr.write("❌ 状态文件声明名单是空的：监视范围从哪来这一问断了\n")
    sys.exit(3)
if visited == 0:
    sys.stderr.write("❌ 三个交付目录都在位，却一个文件都没走到：遍历口径断了\n")
    sys.exit(3)
if not rows:
    print("⚠️ 状态对拍：0 份运行态文件在位（本机还没跑过这些东西）——"
          "本轮只能靠「跑完新冒出来的文件」说话")
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

# ── 判定行的「理由」从哪来（汇总行 → 异常行 → 最后一行输出）──────────────
# 未捕获异常行：行首是标识符、以 Error/Exception/Exit/Interrupt 结尾、紧跟冒号。
# 判据不列具体异常类型清单，将来出现一种没见过的异常也照样报得出理由（本轮是 NameError，
# 上一轮是 OSError；按类型枚举的写法每来一种就要改一次批跑入口）。
EXC_RE='^[A-Za-z_][A-Za-z0-9_.]*(Error|Exception|Exit|Interrupt):'
# 红判定行：套件自己按约定缩进两格打的断言行 + 栈起始行 + 异常行。三处 grep 共用这一份
# 模式，免得「计数用的」和「打印用的」各自漂移——本轮就是打印用的那份漏了 ^Traceback，
# 才让一条崩在引导段的 FAIL 在屏幕上连一行线索都没留下。
RED_RE="^  (FAIL|BAD|❌|✗)|^Traceback|^${EXC_RE%:}"

_pick_reason() {
  # $1 = 带计数的汇总行；$2 = 任何含判定字样的行（可能是小节标题）；$3 = 摘栈后的正文；
  # $4 = 日志原文。
  local summary="$1" loose="$2" body="$3" out="$4" exc tail
  # `|| true`：grep 没匹配返回 1。这一句在 _pick_reason 里，也就是**正在给一条 FAIL 找理由**
  # 的路上——找理由的路上自己静默退出，屏幕上就又是那条没有理由的 FAIL（本轮修的就是它）。
  exc=$(printf '%s\n' "$out" | grep -E "$EXC_RE" | tail -1 || true)
  if [ -n "$exc" ] && [ -n "$summary" ]; then
    # 两条事实都在场就两条都给：套件跑完了并打上「失败 N」，收尾又抛了一条没接住的异常，
    # 只报其中一条都会让人往错的方向查（按位置猜谁是主因不如把两条都摊开）。
    printf '%s（日志里另有一条未捕获异常：%s）' "$summary" "$exc"
    return 0
  fi
  if [ -n "$exc" ]; then
    printf '一条判定都没打就异常退出（栈见下方证据）：%s' "$exc"
    return 0
  fi
  if [ -n "$summary" ]; then
    printf '%s' "$summary"
    return 0
  fi
  if [ -n "$loose" ]; then
    # 明说它不是汇总行：否则读批跑的人会以为套件跑完了并数出了这个数，实际它半步没走。
    printf '没有带计数的汇总行，最后一条含判定字样的输出是：%s' "$loose"
    return 0
  fi
  tail=$(printf '%s\n' "$body" | grep -v '^[[:space:]]*$' | tail -1 || true)
  if [ -n "$tail" ]; then
    printf '没有汇总行、也没有异常行；日志末尾那一行是：%s' "$tail"
    return 0
  fi
  printf '没有汇总行、没有异常行、没有任何非空输出：这条判定只能靠日志文件本身'
}

for f in $FILES; do
  total=$((total+1))
  log="$LOG_DIR/$(basename "${f%.py}").log"
  suite_timeout=$PER_TEST_TIMEOUT
  case "$f" in
    *test_frozen_delivery_offline.py) suite_timeout=$HEAVY_TEST_TIMEOUT ;;
  esac
  code=0
  if [ -n "$TMO" ]; then
    # < /dev/null：解释器必须自己带 fd 0。批跑常在后台起、起它的终端随后被回收，那时
    # fd 0 是个已关闭的描述符，python 在初始化阶段就崩（init_sys_streams / Bad file
    # descriptor），日志里只剩上一行标题——硬门第 20 条 20f，夹具见
    # b2bcollect_build/test_frozen_delivery_offline.py 的 AD 节。
    #
    # `|| code=$?`（第 15 条给的写法）：退出码必须在**接管它的那一句里**取。两种坏写法都实测过：
    # ①`cmd` 单独一行、下一行 `code=$?`——在继承来的 errexit 下走不到第二行，套件一红脚本先没；
    # ②中间夹一句 `if [ $? -eq 0 ]` 再在 else 里取 `$?`——那时 `$?` 已经是 `[` 自己的 1，
    #   139（SIGSEGV）会被读成普通断言红，按信号分类那一整支就此失效。
    "$TMO" -s ABRT -k 10 "$suite_timeout" $PY -u -X faulthandler "$f" >"$log" 2>&1 < /dev/null || code=$?
  else
    $PY -u -X faulthandler "$f" >"$log" 2>&1 < /dev/null || code=$?
  fi
  # `|| true` 且把 2>/dev/null 留着：日志不见了的处置就在下面 `if [ ! -f "$log" ]` 那一支，
  # 让 cat 的非零先把脚本带走，就等于「有证据说它为什么没跑成」的那一支永远不会被执行。
  out=$(cat "$log" 2>/dev/null || true)
  # 找汇总行之前要先把**栈自身**按形状摘掉：Python 会把出错那行的源码原文缩进四格打出来，
  # 套件的断言标题又满是中国字（含「失败」二字），拿它当汇总行就等于报一个假理由。
  # 按形状摘而不是按区域摘：中途被 except 接住、print_exc 之后还会接着打判定，按「最后一处
  # 栈起始行之后的全裁」会把那些真判定一起裁掉。摘到栈体结束为止（异常行本身要留着当理由）。
  body=$(printf '%s\n' "$out" | awk '
    /^(Traceback \(most recent call last\):|Fatal Python error: |Current thread )/ { tb = 1; next }
    tb && (/^  File "/ || /^    / || /^[[:space:]]*$/) { next }
    { tb = 0; print }
  ' || true)
  last=$(printf '%s\n' "$body" | grep -E "PASS|FAIL|通过|失败|_OK" | tail -1 || true)
  # 「汇总行」还要再窄一档：真汇总一律带计数（通过 N / 失败 M、ALL PASS (41 checks)、
  # 全部 122 项断言通过），而小节标题只有中国字没有数字。把两者混为一谈，套件死在
  # 小节开头时判定行就会报成「[③ 采集失败重试]」——看着像结论，其实只说明它走到过这里。
  verdict=$(printf '%s\n' "$body" | grep -E "PASS|FAIL|通过|失败|_OK" | grep -E '[0-9]' | tail -1 || true)
  # FAIL 判定行必须自带理由：本轮 5 份套件崩在引导段（用了没导入的名字），退出码 1、
  # 日志里明明写着 NameError，屏幕上却是一条空的 FAIL——因为判定行只认「套件汇总的
  # 最后一行」，而引导段一行汇总都没打。理由取不到汇总行时退到异常行，再退到末尾输出。
  reason=$(_pick_reason "$verdict" "$last" "$body" "$out")
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
  nfail=$(printf '%s\n' "$out" | grep -oE "[0-9]+ 失败|失败[：: ]*[0-9]+" | tail -1 | grep -oE "[0-9]+" | tail -1 || true)
  if [ $code -eq 0 ] && [ -n "$nfail" ] && [ "$nfail" -gt 0 ]; then
    fail=$((fail+1)); failed="$failed $f"
    printf 'FAIL  %-52s 退出码 0，可套件自己数出了 %s 条失败（判定与退出码不一致，这种绿不算绿）\n' "$f" "$nfail"
    printf '%s\n' "$out" | grep -nE "$RED_RE" | head -20 | sed 's/^/        /'
    printf '        （完整输出：%s）\n' "$log"
  elif [ $code -eq 0 ] && printf '%s\n' "$out" | grep -q "SKIP-FIXTURE"; then
    skip=$((skip+1))
    printf 'SKIP  %-52s %s\n' "$f" "$(printf '%s\n' "$out" | grep 'SKIP-FIXTURE' | head -1)"
  elif [ $code -eq 0 ]; then
    pass=$((pass+1)); printf 'PASS  %-52s %s\n' "$f" "$last"
  elif [ $code -eq 124 ]; then
    # 看门狗到点用 -s ABRT 打断，可退出码是 **timeout 自己的 124**。本机实测 coreutils 9.7
    # 的三种死法：被 ABRT 打断→124、ABRT 叫不应升级 KILL→137、进程自己 abort→134。旧写法拿
    # 134/137 当挂死，两支配对恰好贴反了：真挂死掉进「普通断言红」那一支，报出来的是最后一行
    # 汇总而不是「超时」；进程自己 abort 反倒被喊成超时。两个结论都指向错的地方，而日志里不会
    # 有任何一句话承认自己配错了——判外部工具的返回值就必须当场实测过它的格式（同第 14 道硬门）。
    fail=$((fail+1)); failed="$failed $f"; hung="$hung $f"
    # 「挂死点见下」这句承诺得当场上过证据才能给：本机实测 12 次里 9 次有 faulthandler 的栈
    # ——SIGABRT 到达时主线程往往已经把 GIL 放掉，handler 有时来不及写。旧文案不分情形一律
    # 说「见下」，没栈的那几次就等于又造一句「提示与实际行为不一致」的话，而读日志的人照着
    # 那句去找行号，找不到就会开始跳过这一步的 output。
    if printf '%s\n' "$out" | grep -q '^Fatal Python error: '; then
      printf 'HANG  %-52s 超过 %ss 没跑完（看门狗 124 打断，挂死点栈见下）\n' "$f" "$suite_timeout"
    else
      printf 'HANG  %-52s 超过 %ss 没跑完（看门狗 124 打断；faulthandler 这次没来得及打栈，' "$f" "$suite_timeout"
      printf '只能看日志末尾）\n'
    fi
    printf '%s\n' "$out" | tail -22 | sed 's/^/        /'
  elif [ $code -eq 137 ]; then
    # SIGKILL：只有「SIGABRT 都叫不应」的时候 -k 才会升级到这里。仍然是挂死，而且是最硬的一种
    # ——连 faulthandler 的栈都来不及打，所以把已有的日志原样留给人看。
    fail=$((fail+1)); failed="$failed $f"; hung="$hung $f"
    printf 'HANG  %-52s 看门狗 SIGABRT 叫不应、升级 KILL 才停（137）：最后的输出见下\n' "$f"
    printf '%s\n' "$out" | tail -22 | sed 's/^/        /'
  elif [ $code -gt 128 ]; then
    # 剩下所有 >128 都是「被信号打死」。这一支不再猜某一句中文汇总的措辞：旧写法只认
    # 「全部 N 项断言通过」那一种，而 keyword_resume 打的是「全部通过：88 项断言」——于是
    # 全绿的套件崩在收尾时被读成一条**没有名字的** FAIL，根因只能人工翻日志找（本轮实况）。
    # 现在读两件当场就在的证据：①信号号（139=SIGSEGV、134=SIGABRT）判「怎么死的」；
    # ②faulthandler 那句 `Fatal Python error:` 连同它的栈，判「死在哪一帧」。
    # 「判定与退出码是否同源」用反证，同样不依赖措辞：日志里有没有任何一条红断言。一条红都没有
    # 却被信号打死 = 崩在收尾，套件必须自己 flush 后 os._exit，否则这种绿永远会被读成失败。
    sig=$((code-128))
    redlines=$(printf '%s\n' "$out" | grep -cE "$RED_RE" || true)
    fail=$((fail+1)); failed="$failed $f"
    if [ "$redlines" -eq 0 ]; then
      printf 'CRASH %-52s 被信号 %s 打死（退出码 %s），日志里一条红断言都没有：崩在收尾阶段，与判定无关，' "$f" "$sig" "$code"
      printf '但退出码把整套读成失败——判定要与退出码同源（收尾 flush 后 os._exit）\n'
    else
      printf 'CRASH %-52s 被信号 %s 打死（退出码 %s），日志里同时有 %s 处红：先按栈修判定，再谈收尾\n' "$f" "$sig" "$code" "$redlines"
    fi
    printf '%s\n' "$out" | sed -n '/^Fatal Python error: /,/^$/p' | head -14 | sed 's/^/        /'
    printf '%s\n' "$out" | grep -nE "$RED_RE" | head -12 | sed 's/^/        /'
    printf '        （完整输出：%s）\n' "$log"
  else
    fail=$((fail+1)); failed="$failed $f"
    printf 'FAIL  %-52s %s\n' "$f" "$reason"
    # 打红的那几条断言 + 异常行 + 栈起始行，完整日志见 LOG_DIR
    # BAD 也要打：check_gui_fit 这类几何体检报的是「BAD 某控件被压扁」，按 FAIL 找
    # 就等于失败了却看不到失败在哪——留档了却不去读，跟没留一样。
    printf '%s\n' "$out" | grep -nE "$RED_RE" | head -20 | sed 's/^/        /'
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
    cp "$CUR" "$PREV" 2>/dev/null || echo "   ⚠ 快照没推进成功：下一路套件会拿**上一张**快照比对，" \
                                       "归属可能指到隔壁那一路（污染本身仍由收工对拍兜住）"
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
# 上面开跑前已经拦过一次，这里留作第二个证人：它判的是**展开并计数后**的 total，
# 与 FILES 之间还隔着 DELIVERY_DIRS 发现、排除表、B2B_REGRESSION_ONLY 三步加工——那三步
# 里任何一步把清单吃掉，都只有这一句能看见（删掉上面那句不会让这里变绿，反之也一样）。
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
  if [ -n "$failed" ]; then echo "FAILED:$failed"; fi
fi
# 同上：`[ -n "$hung" ] && echo` 在没有挂死时整行返回 1（本轮全批就是没有挂死）。
if [ -n "$hung" ]; then echo "HANG:$hung"; fi
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
