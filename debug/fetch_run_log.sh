#!/bin/bash
# 取某条 GitHub Actions run 的失败日志尾部（定位云端打包真因）。
#
# 三条刻意的写法：
#   ① 一律用 /usr/bin/python3（Apple 平台签名的解释器）：这台机器上非平台签名的二进制
#      要走 syspolicyd 的 exec 评估，评估排队时整条链路会挂死（2026-11-13 凌晨就是这样
#      把取证脚本拖成两个 0 字节文件的），取证脚本不能再依赖会挂的那一类。
#   ② 每个 curl 都带 -m，输出**落在工作区内**（沙箱只允许写工作区，写 /tmp 会得到一个
#      看起来成功的空文件）。
#   ③ 失败原因不做「大概是这样」的猜测：把带 ::error:: / Traceback / ❌ 的行原样打出来。
set -u
cd /Users/jhnoplan/Desktop/PythonDemo || exit 2
RUN=${1:?用法: fetch_run_log.sh <run_id> [关键字]}
KEY=${2:-}
# GIT_TERMINAL_PROMPT=0：没凭证时 `git credential fill` 会去开 /dev/tty 问密码，既不读
# stdin 也不看超时——取证脚本挂住就等于什么都没取证。口径见
# debug/fetch_b2bcollect_artifacts.sh 开头那段与
# b2bcollect_build/test_frozen_delivery_offline.py 的 AD 节。
TOK=$(printf 'protocol=https\nhost=github.com\n\n' | GIT_TERMINAL_PROMPT=0 git credential fill | sed -n 's/^password=//p')
[ -n "$TOK" ] || { echo "NO_TOKEN：git credential helper 里没有 github.com 凭证"; exit 2; }
API="https://api.github.com/repos/JHnoplan/riskbird-tool"
OUT="debug/runlog_${RUN}.txt"

echo "· run $RUN 的 job 结论"
curl -sS -m 60 -H "Authorization: Bearer $TOK" -H "Accept: application/vnd.github+json" \
  "$API/actions/runs/$RUN/jobs?per_page=50" \
| /usr/bin/python3 -c 'import json,sys
for j in json.load(sys.stdin).get("jobs", []):
    print("   %-28s %-10s %s" % (j["name"], j.get("conclusion"), j.get("html_url")))
    for s in j.get("steps", []):
        if s.get("conclusion") not in (None, "success", "skipped", "neutral"):
            print("      ✗ 步骤: %s -> %s" % (s["name"], s.get("conclusion")))'

curl -sS -m 60 -H "Authorization: Bearer $TOK" -H "Accept: application/vnd.github+json" \
  "$API/actions/runs/$RUN/jobs?per_page=50" \
| /usr/bin/python3 -c 'import json,sys
for j in json.load(sys.stdin).get("jobs", []):
    if j.get("conclusion") != "success":
        print(j["run_id"], j["id"])' | while read -r _rid jid; do
  [ -n "$jid" ] || continue
  LOG_ZIP="debug/joblog_${jid}.zip"
  echo "· job $jid 日志 → $OUT"
  curl -sSL -m 180 -H "Authorization: Bearer $TOK" -o "$LOG_ZIP" \
    "$API/actions/jobs/$jid/logs" || { echo "  下载失败"; continue; }
  # 完整性收口（硬门第 20 条 20c）：这里**不**加 -C 续传是刻意的——日志包整段重下比
  # 拼半个便宜，续传在这个尺寸上只增加「拼坏了没人说」的机会。真正的风险就是那个：
  # 截断的 zip 前两字节照样是 PK，而非 zip 的响应体（401/404/限流的 JSON）会走下面
  # 那条纯文本分支，被当成「这份日志里没有错误行」——取证的人从此拿着一个错结论找 bug。
  # 所以先 unzip -t 真解一遍，验不过就整段重下一次，再不过当场说清楚并跳过这个 job。
  if [ "$(head -c 2 "$LOG_ZIP" 2>/dev/null)" = "PK" ]; then
    if ! unzip -tqq "$LOG_ZIP" >/dev/null 2>&1; then
      echo "  日志包解不开（下坏了），整段重下一次"
      rm -f "$LOG_ZIP"
      curl -sSL -m 180 -H "Authorization: Bearer $TOK" -o "$LOG_ZIP" \
        "$API/actions/jobs/$jid/logs" || { echo "  重下失败"; continue; }
      unzip -tqq "$LOG_ZIP" >/dev/null 2>&1 \
        || { echo "  放弃：$LOG_ZIP 始终不是合法 zip，这个 job 的结论不采信"; continue; }
    fi
  else
    echo "  放弃：$LOG_ZIP 前两字节不是 PK——拿到的是错误响应而不是日志，这个 job 的结论不采信"
    continue
  fi
  /usr/bin/python3 - "$jid" "$OUT" "$KEY" <<'PY'
import sys, zipfile, io, re
jid, out, key = sys.argv[1], sys.argv[2], sys.argv[3]
raw = open(f"debug/joblog_{jid}.zip", "rb").read()
if raw[:2] == b"PK":                      # 归档接口给的是 zip；直接重定向到 blob 时是纯文本
    buf = io.BytesIO(raw)
    text = []
    with zipfile.ZipFile(buf) as z:
        for n in z.namelist():
            text.append(z.read(n).decode("utf-8", "replace"))
else:
    text = [raw.decode("utf-8", "replace")]
lines = "\n".join(text).splitlines()
pat = re.compile(r"::error::|Traceback|错误|失败|❌|Error|error CS|npm ERR")
keep = [ln for ln in lines if pat.search(ln) or (key and key in ln)]
with open(out, "w", encoding="utf-8") as fh:
    fh.write("\n".join(keep[-80:]))
    fh.write("\n\n=== 末尾 120 行原文 ===\n")
    fh.write("\n".join(lines[-120:]))
print(f"  命中 {len(keep)} 行错误线索，全文 {len(lines)} 行")
for ln in keep[-25:]:
    print("   " + ln.strip()[:220])
PY
done
