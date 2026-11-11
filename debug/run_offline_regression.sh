#!/bin/bash
# 离线回归批跑：逐个跑全部离线测试，报 PASS/FAIL/SKIP 与断言数，最后给总判定。
# 清单必须与 b2bcollect_build/test_*_offline.py 保持同步：新写的套件当天就要登记进来，
# 否则它只跑一次就再也无人回归，改动踩到契约也不会红。
# SKIP 单独计数：部分套件依赖实机取回的夹具（页面/码图），夹具不在仓库里时它必须
# 如实报 SKIP 而不是 FAIL——把「缺夹具」混进 FAIL，长期红着的一项会淹掉真问题。
cd /Users/jhnoplan/Desktop/PythonDemo || exit 1
export QT_QPA_PLATFORM=offscreen
PY=.venv/bin/python
# 单套件看门狗超时（秒）：gui_smoke_test 曾因为收尾一个模态弹窗 offscreen 永久等待，
# 把整批回归挂住 20 多分钟。挂死与代码坏是两回事，但都必须当场暴露、不能拖走全批。
PER_TEST_TIMEOUT=${PER_TEST_TIMEOUT:-600}
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
debug/quality_fix_offline_test.py
debug/test_stats_status_flags.py
debug/verify_gys_offline.py
debug/gi_mp_offline_check.py
debug/gi_pace_offline_test.py
debug/keyword_junk_offline_test.py
debug/local_scan_offline_test.py
debug/qzd_channel_offline_test.py
b2bcollect_build/test_block_triage_offline.py
b2bcollect_build/test_data_dir_offline.py
b2bcollect_build/test_external_clean_offline.py
b2bcollect_build/test_integration_offline.py
b2bcollect_build/test_keyword_resume_offline.py
b2bcollect_build/test_access_guard_offline.py
b2bcollect_build/test_acl_levels_offline.py
b2bcollect_build/test_export_scope_offline.py
b2bcollect_build/test_gui_store_offline.py
b2bcollect_build/test_ingest_store_offline.py
b2bcollect_build/test_list_channels_offline.py
b2bcollect_build/test_optimizations.py
b2bcollect_build/test_pool_offline.py
"
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
    printf '%s\n' "$out" | grep -nE "^  (FAIL|❌|✗)" | head -20 | sed 's/^/        /'
    printf '        （完整输出：%s）\n' "$log"
  fi
done
echo "----------------------------------------------------------------"
echo "套件：$pass 通过 / $fail 失败 / $skip 跳过（缺实机夹具）"
[ $fail -eq 0 ] && echo "ALL SUITES PASS" || echo "FAILED:$failed"
[ -n "$hung" ] && echo "HANG:$hung"
exit 0
