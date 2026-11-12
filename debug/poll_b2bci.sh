#!/bin/bash
T=$(cat .gh_token)
for i in $(seq 1 40); do
  R=$(curl -sS -m 40 -H "Authorization: Bearer $T" -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/JHnoplan/riskbird-tool/actions/runs?per_page=4")
  echo "[$(date +%H:%M:%S)] $(echo "$R" | /Users/jhnoplan/Desktop/PythonDemo/.venv/bin/python -c "
import json,sys
d=json.load(sys.stdin)
for r in d['workflow_runs']:
    if r['head_sha'].startswith('e7e1253'):
        print(r['name'],'=',r['status'],'/',r.get('conclusion'), end='  ')
")"
  DONE=$(echo "$R" | /Users/jhnoplan/Desktop/PythonDemo/.venv/bin/python -c "
import json,sys
d=json.load(sys.stdin)
m=[r for r in d['workflow_runs'] if r['head_sha'].startswith('e7e1253')]
print(1 if len(m)==2 and all(r['status']=='completed' for r in m) else 0)")
  [ "$DONE" = "1" ] && echo "ALL_COMPLETED" && break
  sleep 30
done
