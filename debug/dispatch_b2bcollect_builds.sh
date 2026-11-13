#!/bin/bash
# 触发 b2bcollect 云端打包（Windows exe + Mac arm64），并轮询本次 run 状态。
set -u
cd /Users/jhnoplan/Desktop/PythonDemo
# GIT_TERMINAL_PROMPT=0：helper 里没凭证时 `git credential fill` 会去开 /dev/tty 问密码，
# 那一刻它既不读 stdin 也不看超时——整趟派发就这么挂住，而屏幕上一句原因都没有。口径与
# 判据见 debug/fetch_b2bcollect_artifacts.sh 开头那段，以及
# b2bcollect_build/test_frozen_delivery_offline.py 的 AD 节。
CRED=$(printf 'protocol=https\nhost=github.com\n\n' | GIT_TERMINAL_PROMPT=0 git credential fill)
TOK=$(printf '%s\n' "$CRED" | sed -n 's/^password=//p')
USR=$(printf '%s\n' "$CRED" | sed -n 's/^username=//p')
REPO="JHnoplan/riskbird-tool"
API="https://api.github.com/repos/$REPO"
REF="main"

for WF in build-b2bcollect.yml build-b2bcollect-mac.yml; do
  # 每一次 curl 都得自己会停（硬门第 20 条）：这里原本两条都是裸 curl——本机 api.github.com
  # 今天的实测连接时间是 2.1 秒、偶尔 15 秒都连不上（同一台机器同一时刻取制品就是这么挂
  # 到外层超时的）。派发这一头挂了就不会有任何 run 起来，而脚本看起来还在跑。
  # -o /dev/null -w 只取状态码：响应体扔进 /dev/null，没有需要续传的东西。
  CODE=$(curl -s --connect-timeout 30 -m 60 --retry 2 --retry-all-errors -o /dev/null -w '%{http_code}' -X POST \
    -H "Authorization: Bearer $TOK" -H "Accept: application/vnd.github+json" \
    "$API/actions/workflows/$WF/dispatches" \
    -d "{\"ref\":\"$REF\",\"inputs\":{}}")
  # 状态码必须是「看得懂的 2xx」，其它一律点名：旧写法把空串和 404/401 都照样印一句
  # 「dispatch xxx -> …」然后往下走，于是派发失败看起来像派发成功，人开始等一个不存在的 run。
  case "$CODE" in
    2*) echo "dispatch $WF -> $CODE ✅" ;;
    *) echo "dispatch $WF -> ${CODE:-curl 没拿到状态码（网络或令牌）} ❌ 这一步没有触发构建"; EXIT_CODE=${EXIT_CODE:-0}; EXIT_CODE=1 ;;
  esac
done

sleep 20
for WF in build-b2bcollect.yml build-b2bcollect-mac.yml; do
  echo "--- $WF ---"
  # /usr/bin/python3（Apple 平台签名）而不是 PATH 里第一个 python：非平台签名的解释器
  # 每次 exec 都要过 syspolicyd 的评估队列，队列一堵，这条命令就挂在那儿不出结果
  # （2026-11-13 凌晨那两个 0 字节的取日志文件就是这么来的）。
  curl -sS --connect-timeout 30 -m 60 --retry 2 --retry-all-errors \
    -H "Authorization: Bearer $TOK" \
    "$API/actions/workflows/$WF/runs?per_page=1" \
    | /usr/bin/python3 -c 'import sys,json
raw = sys.stdin.read()
try:
    runs = json.loads(raw).get("workflow_runs") or []
except Exception:
    runs = []
if not runs:
    # 空列表在这里不是「没有 run」：令牌失效、限流、网络断都会走到这里，而它们和「workflow
    # 没配好」的修法完全不同，所以把 raw 的头几个字节原样印出来，让下一个看见日志的人有依据。
    print("查不到最新 run（API 回来的不是预期 JSON；前 120 字节：", raw[:120].replace("\n", " "), "）")
else:
    r = runs[0]
    print(r.get("id"), r.get("name"), r.get("status"), r.get("conclusion"), r.get("html_url"))'
done
echo "USER=$USR"
[ "${EXIT_CODE:-0}" = 0 ] || echo "· 至少一条 dispatch 没成功：poll 那一步如果等到的是旧 run，别拿它当本次交付"
exit "${EXIT_CODE:-0}"
