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

# --list：只把解析完的批跑清单打出来（给回归入口自己的离线断言用：判据写在脚本里，
# 核对写在用例里，两边共用同一份展开结果，不重复实现一遍发现逻辑）。
if [ "${1:-}" = "--list" ]; then
  printf '%s\n' $FILES
  exit 0
fi

pass=0; fail=0; skip=0; failed=""; hung=""
# 有 timeout/gtimeout 就上真实看门狗（SIGABRT + faulthandler 能把挂死点栈打出来），
# 没有也别裸跑：宁可用例慢，也不要一个模态弹窗把整批回归拖死。
TMO=$(command -v timeout || command -v gtimeout)
# 每套件的完整输出都落盘：失败时「tail -N」只能看到最后几行，而断言失败的那一行
# （`  FAIL xxx`）通常埋在中间——上次 gui_store 批跑红 2 条、单跑全绿，就是因为
# 看不到具体哪两条，只能靠猜。留档 + 只打 FAIL 行，才能定位抖动。
LOG_DIR="$(dirname "$0")/regression_logs"
rm -rf "$LOG_DIR" 2>/dev/null
mkdir -p "$LOG_DIR" 2>/dev/null
for f in $FILES; do
  log="$LOG_DIR/$(basename "${f%.py}").log"
  if [ -n "$TMO" ]; then
    "$TMO" -s ABRT -k 10 "$PER_TEST_TIMEOUT" $PY -u -X faulthandler "$f" >"$log" 2>&1
  else
    $PY -u -X faulthandler "$f" >"$log" 2>&1
  fi
  code=$?
  out=$(cat "$log")
  last=$(printf '%s\n' "$out" | grep -E "PASS|FAIL|通过|失败" | tail -1)
  if [ $code -eq 0 ] && printf '%s\n' "$out" | grep -q "SKIP-FIXTURE"; then
    skip=$((skip+1))
    printf 'SKIP  %-52s %s\n' "$f" "$(printf '%s\n' "$out" | grep 'SKIP-FIXTURE' | head -1)"
  elif [ $code -eq 0 ]; then
    pass=$((pass+1)); printf 'PASS  %-52s %s\n' "$f" "$last"
  elif [ $code -eq 134 ] || [ $code -eq 137 ]; then
    fail=$((fail+1)); failed="$failed $f"; hung="$hung $f"
    printf 'HANG  %-52s 超过 %ss 没跑完（已 SIGABRT，挂死栈见下）\n' "$f" "$PER_TEST_TIMEOUT"
    printf '%s\n' "$out" | tail -22 | sed 's/^/        /'
  else
    fail=$((fail+1)); failed="$failed $f"; printf 'FAIL  %-52s %s\n' "$f" "$last"
    # 只打红的那几条断言（含前后各一行上下文），完整日志见 LOG_DIR
    # BAD 也要打：check_gui_fit 这类几何体检报的是「BAD 某控件被压扁」，按 FAIL 找
    # 就等于失败了却看不到失败在哪——留档了却不去读，跟没留一样。
    printf '%s\n' "$out" | grep -nE "^  (FAIL|BAD|❌|✗)|^AssertionError|^OSError|^FileNotFoundError" | head -20 | sed 's/^/        /'
    printf '        （完整输出：%s）\n' "$log"
  fi
done
echo "----------------------------------------------------------------"
echo "套件：$pass 通过 / $fail 失败 / $skip 跳过（缺实机夹具）"
[ $fail -eq 0 ] && echo "ALL SUITES PASS" || echo "FAILED:$failed"
[ -n "$hung" ] && echo "HANG:$hung"
exit 0
