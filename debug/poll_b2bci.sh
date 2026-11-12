#!/bin/bash
# 轮询本次云端打包（Windows + Mac 两条 workflow）的状态，直到两条都出结论。
#
# 为什么改掉旧写法（2026-11-12 实跑撞上的两处）：
#   1. 旧脚本从 `.gh_token` 读令牌，而这个文件早就不在磁盘上（它在 .gitignore 里，
#      换机器/重新 clone 必然没有）。后果不是报错，是**静默空跑**：curl 拿空令牌得 401，
#      workflow_runs 解析成空列表，40 轮里一行状态都不印，最后退 0——调用方看到的是
#      「脚本跑完了」。凭证与派发/取制品那两份脚本统一走 `git credential fill`。
#   2. 旧脚本把 e7e1253 写死在源码里，下一次跑就永远在等上一次那个包。现在按参数取，
#      缺省用远端 main 当前那条提交。
#   3. 更隐蔽的一处：这条仓库的 workflow 同时挂着 `push` 触发。推 main 会自己起一对 run，
#      随后的 workflow_dispatch 又按并发组把那一对**取消**掉。若按「这条 sha 的全部 run」
#      判成败，结论里永远混着别人的 cancelled——第一次实跑就把 in_progress 判成 BAD=2。
#      所以按 workflow 文件逐个查，只认 event=workflow_dispatch 那一条（= 我们派发的本次）。
set -u
cd "$(dirname "$0")/.."
PY="/Users/jhnoplan/Desktop/PythonDemo/.venv/bin/python"
[ -x "$PY" ] || PY="python3"
REPO="JHnoplan/riskbird-tool"
API="https://api.github.com/repos/$REPO"
WINS=("build-b2bcollect.yml|Windows EXE" "build-b2bcollect-mac.yml|Mac App")

TOK=$(printf 'protocol=https\nhost=github.com\n\n' | git credential fill \
      | sed -n 's/^password=//p')
[ -n "$TOK" ] || { echo "ABORT：git credential helper 里没有 github.com 凭证（旧脚本在这里退 0，等于没验）"; exit 1; }

SHA="${1:-}"
if [ -n "$SHA" ]; then
  SRC="命令行参数"
else
  # 缺省要轮询「哪一条提交」：以**远端 main 当前那条**为准，而不是本地记的 origin/main。
  # 只通 api.github.com 的那几天，fetch 不成功、本地引用是旧的（本轮就是远端 a9b518d、
  # 本地还停在 2d17a45），照旧的那条去查只会得到「这一轮没起来」这种假结论——而它看起来
  # 和「派发没生效」一模一样，人就会去重派发。Actions 检出的就是远端 ref，判据同源才可信。
  SHA=$(curl -sS -m 30 -H "Authorization: Bearer $TOK" -H "Accept: application/vnd.github+json" \
        -H "X-GitHub-Api-Version: 2022-11-28" "$API/git/ref/heads/main" \
        | "$PY" -c 'import json,sys
try:
    b = json.load(sys.stdin)
except Exception:
    b = {}
print((b.get("object") or {}).get("sha") or "")')
  SRC="远端 main（API 现取）"
  if [ -z "$SHA" ]; then
    SHA=$(git rev-parse origin/main 2>/dev/null || true)
    SRC="本地 origin/main（API 没取到，可能已经过期）"
  fi
fi
[ -n "${SHA:-}" ] || { echo "ABORT：既没传 commit，也拿不到远端 main（显式传第一个参数，或先恢复网络）"; exit 1; }
P="${SHA:0:7}"
LOCAL=$(git rev-parse main 2>/dev/null || true)
if [ -n "$LOCAL" ] && [ "$LOCAL" != "$SHA" ]; then
  echo "   ℹ 本地 main(${LOCAL:0:7}) 与 ${SRC}(${P}) 号不同：打包以远端那条为准（用 API 快进推送后"
  echo "     号本来就不同，内容是否等价请看推送那一步的 tree 校验；真不一样就别发这一轮）"
fi
ROUNDS="${ROUNDS:-40}"
echo "· 轮询 ${P} 这条提交的本次云端打包（基准：${SRC}；只认 workflow_dispatch 那一轮），最多 ${ROUNDS} 轮"

judge_one() {  # $1=workflow 文件名  $2=人话名字；印一行状态，退出码 0=还在跑 1=成功 2=失败 3=这一轮没起来 4=暂时取不回（重试）
  local out
  out=$(curl -sS -m 40 -H "Authorization: Bearer $TOK" -H "Accept: application/vnd.github+json" \
        -H "X-GitHub-Api-Version: 2022-11-28" \
        "$API/actions/workflows/$1/runs?per_page=20" | "$PY" -c '
import json, sys
try:
    body = json.load(sys.stdin)
except Exception as exc:
    print("4|这一轮取不回来（%s），下一轮重试" % exc); sys.exit(0)
runs = body.get("workflow_runs")
if runs is None:
    print("4|返回里没有 workflow_runs（%s），下一轮重试" % str(body)[:80]); sys.exit(0)
mine = [r for r in runs if r["head_sha"].startswith(sys.argv[1])
        and r.get("event") == "workflow_dispatch"]
if not mine:
    print("3|这条提交上没有 dispatch 来的这一轮（派发没发出去？已有的 run：%s 条）"
          % len([r for r in runs if r["head_sha"].startswith(sys.argv[1])])); sys.exit(0)
r = mine[0]
st, cc = r["status"], r.get("conclusion")
if st != "completed":
    print("0|%s（%s）" % (st, cc)); sys.exit(0)
if cc == "success":
    print("1|成功（%s）" % r["html_url"]); sys.exit(0)
print("2|%s（%s）" % (cc, r["html_url"]))
' "$P")
  local code="${out%%|*}"
  echo "  ${2}: ${out#*|}"
  return "$((code))"
}

for i in $(seq 1 "$ROUNDS"); do
  echo "[$(date +%H:%M:%S)] 第 ${i}/${ROUNDS} 轮"
  pending=0; failed=0; aborted=0
  for w in "${WINS[@]}"; do
    f="${w%%|*}"; label="${w##*|}"
    judge_one "$f" "$label"
    case "$?" in
      0|4) pending=1;;
      1) : ;;
      2) failed=1;;
      *) aborted=1;;
    esac
  done
  if [ "$aborted" = "1" ]; then
    echo "ABORT：上面有点名的 workflow 这一轮根本没跑起来，静等 40 轮不是答案（先重跑派发）"
    exit 1
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
