#!/bin/bash
# 离线回归批跑：逐个跑全部离线测试，只报 PASS/FAIL 与断言数，最后给总判定。
cd /Users/jhnoplan/Desktop/PythonDemo || exit 1
export QT_QPA_PLATFORM=offscreen
PY=.venv/bin/python
FILES="
debug/aipage_offline_test.py
debug/b2b_offline_test.py
debug/block_guard_offline_test.py
debug/contact_field_offline_test.py
debug/contact_selfheal_offline_test.py
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
b2bcollect_build/test_block_triage_offline.py
b2bcollect_build/test_data_dir_offline.py
b2bcollect_build/test_external_clean_offline.py
b2bcollect_build/test_integration_offline.py
b2bcollect_build/test_keyword_resume_offline.py
"
pass=0; fail=0; failed=""
for f in $FILES; do
  out=$($PY -u "$f" 2>&1)
  code=$?
  last=$(printf '%s\n' "$out" | grep -E "PASS|FAIL|通过|失败" | tail -1)
  if [ $code -eq 0 ]; then
    pass=$((pass+1)); printf 'PASS  %-52s %s\n' "$f" "$last"
  else
    fail=$((fail+1)); failed="$failed $f"; printf 'FAIL  %-52s %s\n' "$f" "$last"
    printf '%s\n' "$out" | tail -6 | sed 's/^/        /'
  fi
done
echo "----------------------------------------------------------------"
echo "套件：$pass 通过 / $fail 失败"
[ $fail -eq 0 ] && echo "ALL SUITES PASS" || echo "FAILED:$failed"
