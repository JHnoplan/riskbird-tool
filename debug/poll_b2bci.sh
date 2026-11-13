#!/bin/bash
# 轮询本次云端打包（Windows + Mac 两条 workflow）的状态，直到两条都出结论。
#
# 退出码：0 两条都成功（可以取制品）/ 1 有判定不了的异常（点名说清）/ 2 有打包失败 / 3 超时
#
# 为什么改掉旧写法（2026-11-12 实跑撞上的三处）：
#   1. 旧脚本从 `.gh_token` 读令牌，而这个文件早就不在磁盘上（它在 .gitignore 里，
#      换机器/重新 clone 必然没有）。后果不是报错，是**静默空跑**：curl 拿空令牌得 401，
#      workflow_runs 解析成空列表，40 轮里一行状态都不印，最后退 0——调用方看到的是
#      「脚本跑完了」。凭证与派发/取制品那两份脚本统一走 `git credential fill`。
#   2. 旧脚本把 e7e1253 写死在源码里，下一次跑就永远在等上一次那个包。
#   3. 基准提交与「认哪一轮」这两件事（今天下午又撞上一处）：
#      · 基准必须是**远端 main 当前那条**，不是本地记的 origin/main——只通 api.github.com
#        的那几天 fetch 会失败，本地引用是旧的，照旧那条查只得到「这一轮没起来」这种假结论。
#      · 「只认 workflow_dispatch」也作废了：用 Git Data API 快进推 main 同样会触发 push
#        事件那一轮，而 concurrency 开着 cancel-in-progress，新触发的 push 轮会把 dispatch 轮
#        **顶掉**（实跑记录：43242964898 dispatch=cancelled，43243427272 push=in_progress）。
#        判据只有一条站得住：看**当前远端 main 那条提交上最新的那一轮**，不分触发方式——
#        那正是 GitHub 正在为交付做的事。被顶掉不当失败、也不当成功：换到新提交接着看，
#        若顶掉它的新提交一直没起来，就点名报错（静默等 40 轮不是答案）。
set -u
cd "$(dirname "$0")/.."
PY="/Users/jhnoplan/Desktop/PythonDemo/.venv/bin/python"
[ -x "$PY" ] || PY="python3"
REPO="JHnoplan/riskbird-tool"
API="https://api.github.com/repos/$REPO"
WINS=("build-b2bcollect.yml|Windows EXE" "build-b2bcollect-mac.yml|Mac App")
ROUNDS="${ROUNDS:-40}"

# GIT_TERMINAL_PROMPT=0：没凭证时 `git credential fill` 会去开 /dev/tty 问密码，既不读
# stdin 也不看超时——整趟轮询就这么挂住。口径见 debug/fetch_b2bcollect_artifacts.sh 开头
# 那段与 b2bcollect_build/test_frozen_delivery_offline.py 的 AD 节。
TOK=$(printf 'protocol=https\nhost=github.com\n\n' | GIT_TERMINAL_PROMPT=0 git credential fill \
      | sed -n 's/^password=//p')
[ -n "$TOK" ] || { echo "ABORT：git credential helper 里没有 github.com 凭证（旧脚本在这里退 0，等于没验）"; exit 1; }

api_json() {  # $1=路径；带重试，取不回就印空串
  local i
  for i in 1 2 3; do
    local out
    out=$(curl -sS -m 40 -H "Authorization: Bearer $TOK" -H "Accept: application/vnd.github+json" \
          -H "X-GitHub-Api-Version: 2022-11-28" "$1" 2>/dev/null) && { printf '%s' "$out"; return 0; }
    sleep 5
  done
  printf ''
  return 1
}

resolve_main() {  # 远端 main 当前那条提交（与 Actions 检出同源）；取不回印空
  api_json "$API/git/ref/heads/main" | "$PY" -c 'import json,sys
try:
    b = json.load(sys.stdin)
except Exception:
    b = {}
print((b.get("object") or {}).get("sha") or "")'
}

SHA="${1:-$(resolve_main)}"
[ -n "${SHA:-}" ] || { echo "ABORT：拿不到远端 main（API 没通？或显式把 commit 作为第一个参数传进来）"; exit 1; }

judge_one() {  # $1=workflow 文件名 $2=人话名字 $3=基准 sha；退出码 0=还在跑 1=成功 2=失败 3=这一轮没起来 4=暂时取不回 5=被顶掉
  local out
  out=$(api_json "$API/actions/workflows/$1/runs?per_page=20" | "$PY" -c '
import json, sys
try:
    body = json.load(sys.stdin)
except Exception as exc:
    print("4|这一轮取不回来（%s），下一轮重试" % exc); sys.exit(0)
runs = body.get("workflow_runs")
if runs is None:
    print("4|返回里没有 workflow_runs（%s），下一轮重试" % str(body)[:80]); sys.exit(0)
mine = [r for r in runs if r["head_sha"] == sys.argv[1]]
if not mine:
    others = [r for r in runs if r["head_sha"].startswith(sys.argv[1][:7])]
    print("3|这条提交上还没有任何一轮（已列 %d 条，同前缀 %d 条）" % (len(runs), len(others)))
    sys.exit(0)
r = mine[0]                      # 列表本身按时间倒序：这一条就是 GitHub 当前为交付跑的那一轮
st, cc, ev = r["status"], r.get("conclusion"), r.get("event")
if st != "completed":
    print("0|正在跑（%s，触发方式 %s）" % (st, ev)); sys.exit(0)
if cc == "success":
    print("1|成功（%s，run %s）" % (ev, r["html_url"])); sys.exit(0)
if cc == "cancelled":
    print("5|被更新的提交顶掉了（%s，run %s）" % (ev, r["id"])); sys.exit(0)
print("2|%s（%s，run %s）" % (cc, ev, r["html_url"]))
' "$3")
  local code="${out%%|*}"
  echo "  ${2}: ${out#*|}"
  return "$((code))"
}

MISSING=0; CANCELLED_SAME=0
for i in $(seq 1 "$ROUNDS"); do
  CUR=$(resolve_main)
  if [ -z "$CUR" ]; then
    echo "[$(date +%H:%M:%S)] 第 ${i}/${ROUNDS} 轮：取不回远端 main，下一轮重试"
    sleep 30; continue
  fi
  if [ "$CUR" != "$SHA" ]; then
    echo "[$(date +%H:%M:%S)] 远端 main 从 ${SHA:0:7} 前进到 ${CUR:0:7}，改按新的这条判（交付看的就是最新提交）"
    SHA="$CUR"; MISSING=0; CANCELLED_SAME=0
  fi
  echo "[$(date +%H:%M:%S)] 第 ${i}/${ROUNDS} 轮，基准 ${SHA:0:7}"
  pending=0; failed=0; aborted=0; missing_now=0; cancelled_now=0
  for w in "${WINS[@]}"; do
    f="${w%%|*}"; label="${w##*|}"
    judge_one "$f" "$label" "$SHA"
    case "$?" in
      0|4) pending=1;;
      1) : ;;
      2) failed=1;;
      5) pending=1; cancelled_now=1;;
      *) pending=1; missing_now=1;;
    esac
  done
  if [ "$cancelled_now" = "1" ]; then
    CANCELLED_SAME=$((CANCELLED_SAME + 1))
    # 顶掉这一轮的一定是更新的提交；下一轮 resolve 会换基准。若两轮都没换，说明
    # 交付链上那条提交被取消后没人接手——那就等于没有产物，必须点名，不能空等。
    [ "$CANCELLED_SAME" -ge 2 ] && { echo "ABORT：最新提交的打包轮被取消且没有更新的提交接手（重跑一次派发：bash debug/dispatch_b2bcollect_builds.sh）"; exit 1; }
  else
    CANCELLED_SAME=0
  fi
  if [ "$missing_now" = "1" ]; then
    MISSING=$((MISSING + 1))
    # 刚推完的那一瞬间 run 可能还没登记，给两轮宽限；再没有就是派发/触发真没生效。
    [ "$MISSING" -ge 3 ] && { echo "ABORT：基准提交上迟迟没有打包轮（先确认推送与派发都生效：bash debug/dispatch_b2bcollect_builds.sh）"; exit 1; }
    pending=1
  else
    MISSING=0
  fi
  if [ "$pending" = "0" ]; then
    if [ "$failed" = "0" ]; then
      echo "ALL_COMPLETED 两条打包都成功，可以取制品"
      exit 0
    fi
    echo "ALL_COMPLETED 但有打包失败（见上面点名那行），产物不要取"
    exit 2
  fi
  sleep 30
done
echo "TIMEOUT：${ROUNDS} 轮还没跑完，最后一次状态见上面（别急着下结论，先打开 run 页面看卡在哪一步）"
exit 3
