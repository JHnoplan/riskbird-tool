#!/bin/bash
# 触发 b2bcollect 云端打包（Windows exe + Mac arm64），并轮询本次 run 状态。
set -u
cd /Users/jhnoplan/Desktop/PythonDemo
CRED=$(printf 'protocol=https\nhost=github.com\n\n' | git credential fill)
TOK=$(printf '%s\n' "$CRED" | sed -n 's/^password=//p')
USR=$(printf '%s\n' "$CRED" | sed -n 's/^username=//p')
REPO="JHnoplan/riskbird-tool"
API="https://api.github.com/repos/$REPO"
REF="main"

for WF in build-b2bcollect.yml build-b2bcollect-mac.yml; do
  CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST \
    -H "Authorization: Bearer $TOK" -H "Accept: application/vnd.github+json" \
    "$API/actions/workflows/$WF/dispatches" \
    -d "{\"ref\":\"$REF\",\"inputs\":{}}")
  echo "dispatch $WF -> $CODE"
done

sleep 20
for WF in build-b2bcollect.yml build-b2bcollect-mac.yml; do
  echo "--- $WF ---"
  # /usr/bin/python3（Apple 平台签名）而不是 PATH 里第一个 python：非平台签名的解释器
  # 每次 exec 都要过 syspolicyd 的评估队列，队列一堵，这条命令就挂在那儿不出结果
  # （2026-11-13 凌晨那两个 0 字节的取日志文件就是这么来的）。
  curl -s -H "Authorization: Bearer $TOK" \
    "$API/actions/workflows/$WF/runs?per_page=1" \
    | /usr/bin/python3 -c 'import sys,json; r=json.load(sys.stdin)["workflow_runs"][0]; print(r["id"], r["name"], r["status"], r["conclusion"], r["html_url"])'
done
echo "USER=$USR"
