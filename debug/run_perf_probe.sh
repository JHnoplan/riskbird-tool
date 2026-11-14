#!/usr/bin/env bash
# 性能探针统一入口：优化前 / 优化后各跑一次，只比 PROBE_JSON 那几行。
# 为什么要这么个脚本：前后必须用**同一把尺子**（同样的标签数、同样的空转路数、
# 同样的行数），否则「快了 3 倍」这句话里没有事实。
#   用法：  bash debug/run_perf_probe.sh [输出文件]
#          PROXY=1 bash debug/run_perf_probe.sh   # 顺带量代理污染（每条要等 24s，很慢）
#
# 退出码取「两段里第一个非零的」：探针自己跑挂了绝不能被打印成 0——上一版这里写的是
# 紧跟在 { } 块后面的 RC=$?，而块里最后一句是 echo，于是 PROBE_RC 恒等于 0，
# 「门自己绿了」这件事根本没被判据看过（同一类缺陷在交付链硬门里记过账）。
set -u
cd "$(dirname "$0")/.." || exit 2
OUT="${1:-/tmp/perf_probe_$(date +%m%d_%H%M%S).txt}"
HEAD="${GIT_HEAD:-$(git rev-parse --short HEAD 2>/dev/null || echo unknown)}"
PROXY_ARG=""
[ "${PROXY:-0}" = "1" ] && PROXY_ARG="--proxy"
# 这里用**一个标量**而不是数组装这个开关：macOS 自带的 bash 是 3.2，在 set -u 下
# 展开空数组 "${ARR[@]}" 直接报 unbound variable，脚本在第一句命令就退出（本轮就是这么
# 撞上的：探针压根没跑，输出文件里只剩两行头）。标量不加引号是**故意**按空格拆词的，
# 它只可能是空串或 "--proxy" 这一个无空格参数，两种形态都安全。
CDP_RC=0
GUI_RC=0

{
  echo "# perf probe  HEAD=$HEAD  机器=$(sysctl -n hw.model 2>/dev/null || echo pc)" \
       "cpu=$(sysctl -n hw.ncpu 2>/dev/null || echo ?)" \
       "mem_gb=$(($(sysctl -n hw.memsize 2>/dev/null || echo 0) / 1073741824))"
  echo "════ 1/2 CDP 往返（headless Chrome，生产代码路径）════"
  QT_QPA_PLATFORM=offscreen .venv/bin/python debug/perf_cdp_probe.py \
      -n 40 --tabs 8 --busy 4 --big --cycle 6 $PROXY_ARG < /dev/null
  CDP_RC=$?
  echo "CDP_RC=$CDP_RC"
  echo "════ 2/2 界面与导出（offscreen Qt，真实 MainWindow / AutoSaveWorker）════"
  QT_QPA_PLATFORM=offscreen .venv/bin/python debug/perf_gui_probe.py \
      --lines 1200 --rows 5000 < /dev/null
  GUI_RC=$?
  echo "GUI_RC=$GUI_RC"
} >"$OUT" 2>&1
RC=$CDP_RC
[ "$RC" = "0" ] && RC=$GUI_RC
echo "PROBE_RC=$RC OUT=$OUT"
tail -n 6 "$OUT"
exit "$RC"
