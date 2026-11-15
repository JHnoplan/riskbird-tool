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
# errexit／pipefail 显式关掉——**这份脚本的生死不许由调用方的 shell 选项决定**。
# Actions 的 bash 步是 `bash --noprofile --norc -e -o pipefail`，它拉起的 python 再拉起
# 子 bash 时，新版 bash 会把这套选项交给孩子；本轮 Windows 云端就是这么把批跑入口
# 一声不响杀掉的（退出码 1、stdout 0 字节、stderr 0 字节）。完整故事与复现见
# debug/run_offline_regression.sh 开头那段，判据是硬门第 15 条的「立场」那半。
set +e
set +o pipefail
# 仓库根从脚本自己的位置推：写死某台机器的绝对路径，换一台（含 CI 打包机）第一行就 exit，
# 而这几个脚本正是「交付链可复现」唯一的机器口径。
cd "$(dirname "$0")/.." || { echo "❌ 进不去仓库根（$(dirname "$0")/..）：下面每一步都按仓库根的相对路径找 .venv 和 debug/"; exit 2; }
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

wc_bytes() { [ -f "$1" ] && wc -c < "$1" | tr -d ' \n' || echo 0; }
is_zip() { [ "$(head -c 2 "$1" 2>/dev/null)" = "PK" ]; }

# 「一份能用来取证的日志」的完整定义都收在这一个函数里：首下与重下共用同一口径，
# 免得两条路各自演化出第二种「大概算拿到了」。四格逐一判，缺哪一格都会让人拿着
# 一个空结论去找 bug，而不是去找网络：
#   · HTTP 码：curl 的退出码只能发现「连都没连上」，发现不了连上了却回 401/404/限流
#     ——那种响应体是一段 JSON，看着像日志又不太像，正是最容易被读成「云端没报错」的一类。
#   · 字节数：2xx + 0 字节同样是什么都没取到，不能交给下面的分析段去猜。
#   · 格式：`/actions/jobs/{id}/logs` 返回的是 **text/plain**（zip 只属于
#     `/actions/runs/{id}/logs` 那个归档接口）。所以头两字节是不是 PK 只用来决定
#     「要不要真解一遍」，绝不用来决定「这是不是日志」。上一版拿 PK 当门禁，于是每一次
#     正常取证都从「不是 PK」那一支被丢掉，而下面读日志的 python 本来就写了纯文本分支——
#     它永远跑不到。这不报红，只是让取证的人对着一个空文件说「云端没失败行」。
#   · 完整性（硬门第 20 条 20c）：不加 -C 续传是刻意的——日志整段重下比拼半个便宜。
#     判 zip 用 unzip -t 真解一遍，解不开就整段重下一次，再不过当场说清楚并跳过这个 job。
# 返回非 0 表示「这个 job 没有可信日志」：调用方必须跳过，不许继续分析半个文件。
fetch_job_log() {   # fetch_job_log <job_id> <落点>
  local jid="$1" dst="$2" code _try
  for _try in 1 2; do
    code=$(curl -sSL -m 180 -o "$dst" -w '%{http_code}' -H "Authorization: Bearer $TOK" \
      "$API/actions/jobs/${jid}/logs")
    if [ "$code" = "000" ]; then
      echo "  放弃：curl 根本没连上接口（DNS/连接/超时），HTTP 码都没拿到"
      return 2
    fi
    case "$code" in
      2??) ;;
      *)   echo "  放弃：HTTP ${code} 取到的是错误响应而不是日志（体 $(wc_bytes "$dst") 字节，" \
                "开头：$(head -c 200 "$dst" | tr '\r\n' '  ')）"
           return 2 ;;
    esac
    if [ "$(wc_bytes "$dst")" = "0" ]; then
      echo "  放弃：HTTP ${code} 但响应体 0 字节——没有可取证的行"
      return 2
    fi
    if is_zip "$dst"; then
      if unzip -tqq "$dst" >/dev/null 2>&1; then return 0; fi
      if [ "$_try" = 1 ]; then
        echo "  日志包解不开（下坏了），整段重下一次"
        rm -f "$dst"
        continue
      fi
      echo "  放弃：$dst 始终不是合法 zip"
      return 2
    fi
    return 0                        # 纯文本日志：没有容器要解，2xx + 有字节就是拿到了
  done
}

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
  # 落点不叫 .zip：这个端点实测返回 text/plain（本机对着 run 43664465393 的 job
  # 132660669361 取过一次：HTTP 200、Content-Type: text/plain、53545 字节、
  # 开头是 UTF-8 BOM），名字骗人是上一版误判的一半原因，另一半见上面函数里的四条判据。
  LOG_RAW="debug/joblog_${jid}.log"
  echo "· job $jid 日志 → $OUT"
  fetch_job_log "$jid" "$LOG_RAW" || continue
  /usr/bin/python3 - "$jid" "$OUT" "$KEY" <<'PY'
import sys, zipfile, io, re
jid, out, key = sys.argv[1], sys.argv[2], sys.argv[3]
raw = open(f"debug/joblog_{jid}.log", "rb").read()
if raw[:2] == b"PK":                      # runs 归档接口给 zip；jobs 日志接口给纯文本
    buf = io.BytesIO(raw)
    text = []
    with zipfile.ZipFile(buf) as z:
        for n in z.namelist():
            text.append(z.read(n).decode("utf-8", "replace"))
else:
    # utf-8-sig：实测这份日志以 EF BB BF 开头，用 utf-8 解会把 BOM 留在第一行第一个字符上，
    # 于是第一行永远匹配不到任何以行首特征写的判据（`::error::`、`##[group]`…）。
    text = [raw.decode("utf-8-sig", "replace")]
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
