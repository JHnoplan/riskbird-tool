#!/bin/bash
# 查三条发版流水最近一轮的状态（主通道 Win / Mac / Win7 兼容通道）。
#
# 为什么要这么一个小东西：轮询脚本 poll_b2bci.sh 的 WINS 数组里只有主通道那两条，
# 兼容通道（build-b2bcollect-legacy.yml）不在它认的名单里——于是「Win7 包跑到第几步」
# 这一问只能靠人手工敲 API，手工敲出来的结论不可复跑。这里先把「能看见」补上。
#
# 响应体为什么**不落地**（2026-11-20 被第 20 条 20c 逼出来后想通的）：这一份 JSON 的
# 全部意义是「最近一轮」，落盘等于留一份注定过期的缓存；更坏的是 curl 断在半路时，
# 截断的那份文件会原地留在 debug/ 里，下一次 cat 它就是垃圾，而现场看着像成功过。
# 判据侧对这一格早有在案的说法：`-o -` 与 /dev/null 不算落点，因为「体没落地就没有
# 要续传的东西」。续传在这儿反而是错的——续上一个上一轮的半截响应，比整段重下更骗人。
#
# stdin 的分工（第 20 条 20f 那一族）：heredoc 占着 fd 0 传 python 代码，所以响应体
# 只能走环境变量，不能再占管道。
set -u
set +e
set +o pipefail
cd "$(dirname "$0")/.." || exit 2

TOK=$(printf 'protocol=https\nhost=github.com\n\n' | GIT_TERMINAL_PROMPT=0 git credential fill \
      | sed -n 's/^password=//p')
[ -n "$TOK" ] || { echo "NO_TOKEN：git credential helper 里没有 github.com 凭证"; exit 2; }

# -f：HTTP 4xx/5xx 直接当失败（否则 404 的错误体也会原样进解析，看着像「没有跑过的记录」）
# -o -：体走 stdout，不落盘（见上面那段）
BODY=$(curl -fsS -m 25 -o - -H "Authorization: Bearer $TOK" -H "Accept: application/vnd.github+json" \
        "https://api.github.com/repos/JHnoplan/riskbird-tool/actions/runs?per_page=15")
rc=$?
if [ $rc -ne 0 ]; then
    echo "❌ 取状态失败（curl 退出码 $rc）：这一格没有可结论的数据，别把「没拿到」读成「没跑过」。"
    exit 1
fi
# 单个环境变量传不动就直说，不要让 python 收到一个被截断的 JSON（那正是上面落盘那一版留下的形态）
if [ "${#BODY}" -gt 900000 ]; then
    echo "❌ 响应 ${#BODY} 字节，超出环境变量安全上限（900000）：请调小 per_page 或改分页取。"
    exit 1
fi

CI_BODY="$BODY" /usr/bin/python3 - <<'PY'
import json, os
raw = os.environ.get("CI_BODY", "")
try:
    d = json.loads(raw)
except Exception as e:                                    # 取不到状态就说取不到，不打空表
    print(f"解析失败（{e}）：这一格没有可结论的数据（拿到 {len(raw)} 字节）")
    raise SystemExit(1)
runs = d.get("workflow_runs", [])
if not runs:
    print("这一轮仓库里没有任何 workflow_runs 记录（head 分支不对？还是真的没触发过？）")
    raise SystemExit(0)
for r in runs:
    print(f"{r['id']}  {r['name'][:46]:<46}  #{r['run_number']:<4} {r['status']:<12}"
          f" {str(r.get('conclusion')):<10} head={r['head_commit']['id'][:7]}"
          f" 触发={r['event']}")
PY
