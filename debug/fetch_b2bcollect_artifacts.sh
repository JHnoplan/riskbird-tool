#!/bin/bash
# 等云端打包完成 → 下载 artifact → 替换本地 dist（exe + Mac arm64 zip）→ 校验随包凭证。
#
# 为什么按 HEAD 现取 run id（以前是手写死两个 id）：死 id 属于「上一次那个 commit」，
# 换一次提交就得记得改；忘了改就是**下载旧包冒充新包**——比构建失败更危险，因为
# 它看起来完全成功。现在 id 由本次 commit 现查，查不到就当场停，绝不拿旧产物交差。
#
# 最后一道「随包凭证」校验是这轮缺陷的直接门禁：外部机器全员精简版的根因就是包里
# 没有 config.json。构建期的 --acl-doctor 已经拦过一次，这里在**下载回来的字节**上
# 再验一次——万一哪天有人改了 workflow 的 --add-data，本机就能立刻看出来，而不是等
# 客户反馈。
set -u
cd /Users/jhnoplan/Desktop/PythonDemo
TOK=$(printf 'protocol=https\nhost=github.com\n\n' | git credential fill | sed -n 's/^password=//p')
[ -n "$TOK" ] || { echo "NO_TOKEN：git credential helper 里没有 github.com 凭证"; exit 2; }
API="https://api.github.com/repos/JHnoplan/riskbird-tool"
# 取制品的工作目录：每一次都有自己的落点（B2B_FETCH_WORK 可显式指定，指定时先清空它）。
# 旧写法把所有人挤在同一个 /tmp/b2b_dl 上：两次取制品并发（或者上一次崩了留下半个目录）
# 时，校验读到的可能是另一批的字节——「验证的字节和交付的字节不是同一份」在这一环最阴，
# 因为它报出来的仍然是 ✅。
WORK=${B2B_FETCH_WORK:-$(mktemp -d "${TMPDIR:-/tmp}/b2b_dl.XXXXXX")}
# 自己生的垃圾自己收：2026-11-13 本机就是在这里翻车的——盘只剩 2.0Gi，取制品下到
# 一半 unzip 报 "write error (disk full?) Continue? (y/n)"，它在**等一个不会来的回答**
# （这条路是后台 nohup，没人敲键盘），被杀掉之后 TMPDIR 里留下两个各 883MB 的半截
# 工作目录，把下一次取制品也一起堵死。崩一次留一份，等于这个脚本每失败一次就更可能
# 再失败一次。所以：自动建的工作目录一律在退出时收掉（显式指定 B2B_FETCH_WORK 时保留，
# 那是调用方要留着查的落点）。
if [ -z "${B2B_FETCH_WORK:-}" ]; then
  trap 'rm -rf "$WORK" 2>/dev/null || true' EXIT
fi
# 目标 commit 的取法（2026-11-12 改的，与 poll 那一份同口径）：
#   ①显式参数优先；②缺省取**远端 main 当前那条**（走 api.github.com）；③API 不通才退回
#   本地 HEAD 并明说。为什么不能一上来就用本地 HEAD：推送偶发要走 Git Data API 快进，
#   那种时候远端那个 sha 与本地 HEAD 号不同（内容等价），按本地号去查 run 只会得到
#   「本次 commit 没有 run」——而它看起来和「云端没打这个包」一模一样，人就去找旧制品，
#   那一步才是真正的风险：拿旧包冒充新包，症状是完全成功。
#   所以查到 run 之后还要再对一次 tree：远端那条提交的 tree 与本地 HEAD 的 tree 不一致时
#   点名告警（不静默），因为「取回来的字节不含本地这份改动」必须是能被看见的事实。
SHA=${1:-}
SHA_SRC="显式参数"
if [ -z "$SHA" ]; then
  SHA=$(curl -sS -m 30 -H "Authorization: Bearer $TOK" -H "Accept: application/vnd.github+json" \
        "$API/git/ref/heads/main" 2>/dev/null \
        | python3 -c 'import json,sys
try:
    b = json.load(sys.stdin)
except Exception:
    b = {}
print((b.get("object") or {}).get("sha") or "")')
  SHA_SRC="远端 main（API 现取）"
fi
if [ -z "$SHA" ]; then
  SHA=$(git rev-parse HEAD)
  SHA_SRC="本地 HEAD（API 没取到远端 ref）"
fi
case "$SHA" in
  *[!0-9a-fA-F]*|'') echo "SHA 不合法（要 40 位十六进制，或留空表示用远端 main）：$SHA"; exit 2 ;;
esac
[ ${#SHA} -eq 40 ] || { echo "SHA 长度 ${#SHA} 不是 40 位：$SHA"; exit 2; }
LOCAL_TREE=$(git rev-parse "HEAD^{tree}")
echo "· 目标 commit：${SHA:0:7}（基准：${SHA_SRC}）"

# 按 (平台 key, workflow 文件名) 查本次 commit 最新那条 run 的 id
find_run() {
  curl -s -H "Authorization: Bearer $TOK" \
    "$API/actions/workflows/$2/runs?head_sha=$SHA&per_page=1" \
  | python3 -c 'import sys,json
r=(json.load(sys.stdin).get("workflow_runs") or [{}])[0]
print(r.get("id",""), r.get("status","none"), r.get("conclusion") or "")'
}

declare -a IDS
for pair in "win:build-b2bcollect.yml" "mac:build-b2bcollect-mac.yml"; do
  key=${pair%%:*}; wf=${pair##*:}
  read -r id status concl <<<"$(find_run "$key" "$wf")"
  [ -n "$id" ] || { echo "[$key] 本次 commit 没有 $wf 的 run（推送没触发？先确认 commit 已推上 main）"; exit 3; }
  echo "[$key] run=$id $status $concl"
  IDS+=("$key:$id")
done

# 字节级等价证明（放在等结果**之前**：内容都不等价就别浪费这二十分钟轮询）。
# 号不同、tree 相同 = 用 Git Data API 快进推送的正常结果，取回来的就是这个内容；
# tree 不同 = 本地还有没推上去的改动（或远端多了别的），这一份制品不代表刚在本机跑绿的
# 那份代码——必须停下来说清楚。旧写法是「按本地 HEAD 查 run，查不到才停」，既报错了
# 原因（说的是「推送没触发」），又挡不住真正的风险：拿一个内容不同的旧包交付。
REMOTE_TREE=$(curl -s -H "Authorization: Bearer $TOK" -H "Accept: application/vnd.github+json" \
              "$API/git/commits/$SHA" | python3 -c 'import json,sys
try:
    b = json.load(sys.stdin)
except Exception:
    b = {}
print(((b.get("tree") or {}).get("sha")) or "")')
[ -n "$REMOTE_TREE" ] || { echo "❌ 取不回 ${SHA:0:7} 的 tree，无法证明制品与本地内容等价（这一步不靠猜）"; exit 7; }
if [ "$REMOTE_TREE" != "$LOCAL_TREE" ]; then
  echo "❌ 制品那条提交(${SHA:0:7}，基准：${SHA_SRC}) 的 tree ${REMOTE_TREE:0:8} 与本地 HEAD 的 tree ${LOCAL_TREE:0:8} 不是同一颗"
  echo "   先看清差异（git diff --name-only ${SHA:0:7} HEAD）；若确认只是不随包的文件（测试/工具脚本），"
  echo "   就把远端那条 sha 显式传给本脚本再取；否则先把要交付的改动推上去。"
  exit 7
fi
echo "· tree ${LOCAL_TREE:0:8} 远端与本地一致：取回来的制品与刚在本机验证的这份字节等价"

for kv in "${IDS[@]}"; do
  key=${kv%%:*}; id=${kv##*:}
  for i in $(seq 1 90); do
    ST=$(curl -s -H "Authorization: Bearer $TOK" "$API/actions/runs/$id" \
      | python3 -c 'import sys,json; r=json.load(sys.stdin); print(r["status"], r["conclusion"] or "", r["head_sha"][:7])')
    echo "[$key] $ST"
    case "$ST" in
      completed\ success*) break ;;
      completed\ *) echo "[$key] FAILED（见 $API/actions/runs/$id 的日志）"; exit 1 ;;
    esac
    sleep 20
  done
done

# 轮询窗口到期不等于打包完成：下制品前再核一次状态，没完成就明说，不拿旧产物冒充
for kv in "${IDS[@]}"; do
  key=${kv%%:*}; id=${kv##*:}
  FIN=$(curl -s -H "Authorization: Bearer $TOK" "$API/actions/runs/$id" \
    | python3 -c 'import sys,json; r=json.load(sys.stdin); print(r["status"], r["conclusion"] or "", r["head_sha"][:7])')
  echo "[$key] 下载前状态：$FIN"
  case "$FIN" in
    completed\ success\ ${SHA:0:7}*) ;;
    *) echo "[$key] 不是本次 commit 的成功 run，终止下载"; exit 4 ;;
  esac
done

# ── 开下载之前的两件事：先把往次崩留下的半截工作目录收掉，再问「这次装得下吗」──
# 顺序很重要：不先收垃圾，可用空间里有一大块正被上一次失败的残留占着，那道空间判据
# 就会指着一份根本不属于本次交付的字节说话。
# 事故现场（2026-11-13 本机）：盘只剩 2.0Gi，取制品下到一半 unzip 报
# "write error (disk full?) Continue? (y/n)"——它不是退出，是**回头问人**，而这条路是
# 后台 nohup 跑的，没人敲键盘，整个取制品就挂在那里，看起来像「还在干活」；强杀之后
# TMPDIR 里留下两个各 883MB 的 b2b_dl.* ，下一次取制品更放不下。失败会自己攒成
# 下一次的失败，这种复利必须在脚本里断掉，不能靠人记得去打扫。
# 注意那句 "disk full?" 只是它**写不下去时顺口说的猜测**，不是定论：这一批腾出空间后
# 照样红过，真因由下面的 unzip_probe 差分探针在同一份字节上问两个工具，跑一次就钉死。
reclaimed=0
for d in $(find "${TMPDIR:-/tmp}" -maxdepth 1 -type d -name 'b2b_dl.*' 2>/dev/null); do
  [ "$d" = "$WORK" ] && continue
  # 只收自己这一族，且 1 小时内没再被动过（可能是隔壁那次正在写），且没有进程还指着它
  [ -n "$(find "$d" -maxdepth 0 -mmin -60 2>/dev/null)" ] && continue
  pgrep -f -- "$d" >/dev/null 2>&1 && continue
  rm -rf -- "$d" && reclaimed=$((reclaimed + 1))
done
if [ "$reclaimed" -gt 0 ]; then
  echo "· 收掉往次崩留下的半截工作目录 $reclaimed 个（只限同前缀 b2b_dl.*，1 小时内动过的不碰）"
fi

NEED=0
UNKNOWN=0
for kv in "${IDS[@]}"; do
  key=${kv%%:*}; id=${kv##*:}
  SZ=$(curl -sS -m 40 -H "Authorization: Bearer $TOK" -H "Accept: application/vnd.github+json" \
       "$API/actions/runs/$id/artifacts" | python3 -c 'import sys,json
try:
    a = json.load(sys.stdin).get("artifacts") or []
except Exception:
    a = None
print(sum(int(x.get("size_in_bytes") or 0) for x in a) if a else -1)')
  case "$SZ" in
    ""|"-1") UNKNOWN=$((UNKNOWN + 1)); echo "· [$key] API 没答出制品大小（这一路按保守下限判，不当「不需要空间」）" ;;
    *) NEED=$((NEED + SZ)); echo "· [$key] 制品 $((SZ / 1048576)) MB" ;;
  esac
done
# 解压后要占的比制品数字大得多（.app 与 onedir 里全是压不动的可执行文件），按 3 倍
# 外加 500MB 余量要空间。问不到数字时不许退化成「不判」：给一个下限，宁可停下来说话。
NEED_KB=$(( (NEED * 3 + 524288000) / 1024 ))
if [ "$UNKNOWN" -gt 0 ] && [ "$NEED_KB" -lt 2097152 ]; then
  NEED_KB=2097152
fi
AVAIL_KB=$(df -kP "$WORK" 2>/dev/null | awk 'NR==2 {print $4}')
if [ -z "$AVAIL_KB" ]; then
  echo "❌ 问不出 ${WORK} 所在卷的可用空间（df 没给数）：空间不够时 unzip 不会干净地报错退出——写不下去它会回头问「Continue? (y/n)」，后台那条路没人答就整条挂住（本机实测：问句拿不到 stdin 就 8 秒不结束）。判不了就不下载。"
  exit 7
fi
echo "· 磁盘：可用 $((AVAIL_KB / 1024)) MB，本次至少要 $((NEED_KB / 1024)) MB（制品×3 + 500MB 余量${UNKNOWN:+，含 $UNKNOWN 路 API 没答出大小}）"
if [ "$AVAIL_KB" -lt "$NEED_KB" ]; then
  echo "❌ 磁盘不够：${WORK} 所在卷只剩 $((AVAIL_KB / 1024)) MB，取这一批制品至少要有 $((NEED_KB / 1024)) MB。不在这里停下来的话，坏法不是「报一条错」，而是下到一半解包回头问人、整条交付链挂住，或在半截字节上做完校验还报 ✅。"
  echo "   · 可以先收的（都是可再生的）：TMPDIR 里往次的 b2b_dl.*（本脚本上面已按规则收过一轮）、b2bcollect_build/build/（PyInstaller 中间产物）、本机 dist 里不需要的旧包。"
  exit 7
fi

mkdir -p "$WORK" || { echo "❌ 工作目录建不出来：${WORK}"; exit 2; }
if [ -n "${B2B_FETCH_WORK:-}" ]; then
  find "$WORK" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
fi
echo "· 工作目录：$WORK"
# 解包口径：优先 ditto，unzip 只作兜底。要钉的门是「不交互 + 接住退出码」这两条。
# 三条实测（2026-11-13 本机，同一份带中文条目名的 zip，脚本 unzip_probe 里可复跑）：
#   · unzip 6.00（Apple 改过的那份）会**回头问人**：凡是它觉得需要答复的场合（同名
#     文件已存在、写不下去、路径不对），它把问题打到 stderr 然后等 stdin。把 stdin 接
#     到一个没人写的管道上，8 秒它还没结束——整条取制品就挂在那里，看起来像「还在下载」。
#   · 同一条命令把 stdin 接到空设备：0.2 秒按默认答复「[N]one」走人，rc=1。不挂，
#     退出码还接得住，这才是后台（nohup / CI）能用的形状。
#   · ditto 同一场景 0.018 秒解完、rc=0：它压根没有「问句」这个概念。
# 曾经我把这里的理由写成「Apple unzip 不认中文名的 UTF-8 标志位」——**那句是错的**，
# 实测它认：带 0x0800 标志位的 `使用说明.txt` 解出来名字原样正确。而 11-13 那次内层
# 绿色版解不开、报 `write error (disk full?). Continue? (y/n)` 的真实原因到现在没有
# 定论（腾出空间后照样红，红法一模一样）。别再靠猜下结论：unzip_probe 是同一条 zip 上
# 让两个工具各解一次的差分探针，B2B_FETCH_DIFF=1 开一次，谁红谁绿、最后一行说了什么
# 就写进日志；它只记账，不参与交付判据（所以函数里没有 exit）。
extract_zip() {  # $1=zip $2=目标目录
  mkdir -p "$2" || return 2
  if command -v ditto >/dev/null 2>&1; then
    ditto -x -k "$1" "$2" < /dev/null
  else
    unzip -oq "$1" -d "$2" < /dev/null || return 1
  fi
}
unzip_probe() {  # 差分探针：$1=zip $2=标签 $3=口径解出来的参照目录；只印事实，不决定这一趟成不成
  [ "${B2B_FETCH_DIFF:-0}" = "1" ] || return 0
  command -v unzip >/dev/null 2>&1 || { echo "[$2] 差分探针：这台机器没有 unzip，跳过"; return 0; }
  local p="$WORK/probe_$2" lg="$WORK/probe_$2.log" rc mine ref=
  rm -rf "$p"; mkdir -p "$p" || return 0
  unzip -q "$1" -d "$p" < /dev/null > "$lg" 2>&1; rc=$?
  mine=$(find "$p" -type f 2>/dev/null | wc -l | tr -d ' ')
  if [ -n "${3:-}" ] && [ -d "$3" ]; then
    ref="，口径同一份解出 $(find "$3" -type f 2>/dev/null | wc -l | tr -d ' ') 个文件"
  elif [ -n "${3:-}" ]; then
    ref="，口径那份没落地（$3 不在）"
  fi
  echo "[$2] 差分探针：unzip rc=${rc}、落地 ${mine} 个文件${ref}；它自己说的那句：$(tail -2 "$lg" | tr '\n' ' ')"
  [ -s "$lg" ] || echo "   （无输出：这份字节 unzip 也解得动，当初那次红与工具无关）"
  rm -rf "$p" "$lg"
  return 0
}
FAIL=0
for kv in "${IDS[@]}"; do
  key=${kv%%:*}; id=${kv##*:}
  AID=$(curl -s -H "Authorization: Bearer $TOK" "$API/actions/runs/$id/artifacts" \
    | python3 -c 'import sys,json; print(json.load(sys.stdin)["artifacts"][0]["id"])')
  curl -sL --retry 8 --retry-all-errors -H "Authorization: Bearer $TOK" -o "$WORK/$key.zip" \
    "$API/actions/artifacts/$AID/zip"
  if ! extract_zip "$WORK/$key.zip" "$WORK/$key"; then
    NOW_KB=$(df -kP "$WORK" 2>/dev/null | awk 'NR==2 {print $4}')
    echo "[$key] ❌ 外层制品 zip 解不开（此刻可用 $(( ${NOW_KB:-0} / 1024 )) MB）：先看空间，再怀疑网络截断"
    unzip_probe "$WORK/$key.zip" "$key.outer" "$WORK/$key"
    exit 6
  fi
  # 制品是「zip 里再套一层真实产物包」，两个平台都要解到内层才能验：
  #   · Mac：外层 Actions zip 里是 ditto 打的 B2bCollectTool_Mac_arm64.zip，.app 在里面；
  #   · Windows：外层 Actions zip 里是 B2bCollectToolSetup.exe（安装包）+
  #     B2bCollectTool_Win_portable.zip（绿色版），**布局判据在绿色版那个 zip 里**，
  #     所以解到内层、把 B2bCollectTool/ 归位成 dist/B2bCollectTool 再交给校验脚本。
  # 解不开必须当场停：静默跳过校验等于把「随包凭证」这道门拆了，而它正是外部机器全员
  # 精简版那次事故的守门人。
  if [ "$key" = "mac" ]; then
    INNER=$(find "$WORK/$key" -maxdepth 2 -name "*.zip" -type f | head -1)
    [ -n "$INNER" ] || { echo "[$key] ❌ 制品里没有内层产物 zip（workflow 的上传步骤变了？先修校验器再谈交付）"; exit 6; }
    extract_zip "$INNER" "$WORK/$key/unpacked" \
      || { echo "[$key] ❌ 内层产物 zip 解不开：$INNER"; unzip_probe "$INNER" "$key.inner" "$WORK/$key/unpacked"; exit 6; }
    unzip_probe "$INNER" "$key.inner" "$WORK/$key/unpacked"
    echo "[$key] 已解开内层：$(basename "$INNER") → $(find "$WORK/$key/unpacked" -maxdepth 1 -name "*.app" -type d | head -1)"
  else
    INNER=$(find "$WORK/$key" -maxdepth 2 -name "B2bCollectTool_Win_portable.zip" -type f | head -1)
    [ -n "$INNER" ] || { echo "[$key] ❌ 制品里没有 B2bCollectTool_Win_portable.zip（onedir 交付的两个产物少了一个，先修 workflow 再谈交付）"; exit 6; }
    if ! extract_zip "$INNER" "$WORK/$key/root/dist"; then
      NOW_KB=$(df -kP "$WORK" 2>/dev/null | awk 'NR==2 {print $4}')
      echo "[$key] ❌ 内层绿色版 zip 解不开：${INNER}（此刻可用 $(( ${NOW_KB:-0} / 1024 )) MB）"
      unzip_probe "$INNER" "$key.inner" "$WORK/$key/root/dist"
      exit 6
    fi
    unzip_probe "$INNER" "$key.inner" "$WORK/$key/root/dist"
    echo "[$key] 已解开内层：$(basename "$INNER") → $(ls "$WORK/$key/root/dist" | tr '\n' ' ')"
  fi
  echo "[$key] artifact 内容：$(find "$WORK/$key" -maxdepth 2 -type f | head -3 | tr '\n' ' ')"
done

# ── 随包凭证校验：产物里必须真的带着可用的 config.json ──
#    为什么不能用「在 exe 字节里 grep config.json」：我们自己的源码里就写着这个
#    字符串，它必然存在，判据永远绿——等于没有判据。必须读**归档结构**：
#    · Windows onefile：解析 PyInstaller CArchive 的 TOC，要有一条名为 config.json
#      的数据项，还要有 tool_config 模块（凭证解析链就在这个模块里，漏收等于没密钥）；
#    · Mac onedir：数据项直接落在 Contents/Resources/ 下，按文件查，并且必须
#      真的读得出两把非空密钥（文件在但是空的，症状和没注入一模一样）。
#    Mac 产物是 arm64，本机是 Intel，跑不了 —— 所以这边只做结构校验，运行期校验
#    由 CI 的 --acl-doctor 产物门禁负责（它跑在 arm64 runner 上，形态才对得上）。
# ── 随包凭证与产物布局校验 ──
#    为什么不能用「在 exe 字节里 grep config.json」：我们自己的源码里就写着这个
#    字符串，它必然存在，判据永远绿——等于没有判据。必须读**归档结构**，并且
#    两个平台读的是同一份判据（.github/scripts/package_win_artifacts.py）：
#    · Windows onedir：直接按文件查 dist/B2bCollectTool/{exe, _internal/…}，
#      判据本体就是 CI 装配交付物时跑的同一个函数——本机与 CI 各写一套迟早对不上；
#      还要真读出两把非空 OSS 密钥（文件在但内容空，症状和没注入一模一样）；
#    · Mac onedir：数据项在 Contents/Frameworks 还是 Contents/Resources 由 PyInstaller
#      大版本决定（本机 6.x 实测两处都有），所以两处都认，命中哪处就用哪处判密钥。
#    Mac 产物是 arm64，本机是 Intel，跑不了 —— 所以这边只做结构校验，运行期校验
#    由 CI 的 --acl-doctor 产物门禁负责（它跑在 arm64 runner 上，形态才对得上）。
check_win() {
  local root=$1 setup=$2
  [ -n "$root" ] || { echo "[win] ❌ 没解开绿色版 zip，无法校验产物布局"; return 1; }
  if [ -z "$setup" ] || [ ! -f "$setup" ]; then
    echo "[win] ❌ 制品里没有 B2bCollectToolSetup.exe（安装包是主交付物，缺它等于没交付）"
    return 1
  fi
  .venv/bin/python - "$root" <<'PYEOF'
import importlib.util, json, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location(
    "package_win_artifacts", ".github/scripts/package_win_artifacts.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)                    # 判据与 CI 共用一份，不在这抄第二份
problems = []
mod.check_layout(Path(sys.argv[1]), problems)
internal = Path(sys.argv[1]) / "dist" / mod.APP_DIR_NAME / "_internal"
cfg = internal / "config.json"
if cfg.is_file():
    try:
        d = json.loads(cfg.read_text(encoding="utf-8"))
    except Exception as e:
        problems.append(f"config.json 读不出来：{type(e).__name__}")
        d = {}
    keys = ("oss_access_key_id", "oss_access_key_secret")
    missing = [k for k in keys if not str(d.get(k) or "").strip()]
    if missing:
        problems.append(f"随包凭证不全，缺 {'、'.join(missing)}（外部机器会一律精简版）")
elif not problems:
    problems.append("_internal/config.json 不在位")
if problems:
    print("[win] ❌ 产物布局 / 随包凭证不合格：")
    for p in problems:
        print("   · " + p)
    sys.exit(1)
print("[win] ✅ onedir 布局齐全（exe + _internal + 采集器 + 凭证），OSS 两把密钥齐")
sys.exit(0)
PYEOF
}
check_mac() {
  local app=$1
  [ -n "$app" ] || { echo "[mac] ❌ 没找到 .app，无法校验"; return 1; }
  .venv/bin/python - "$app" <<'PYEOF'
import json, sys
from pathlib import Path
app = Path(sys.argv[1])
# PyInstaller 6.x 把 --add-data 落在 Contents/Frameworks，旧版与个别参数落在
# Contents/Resources（本机 6.22 实测两处都有同一份 config.json）。写死一处就会
# 在升级 PyInstaller 那天报「随包凭证缺失」——产物明明是好的，报的却是校验器的毛病。
cfg = next((p for p in (app / "Contents" / "Frameworks" / "config.json",
                        app / "Contents" / "Resources" / "config.json") if p.is_file()),
           app / "Contents" / "Resources" / "config.json")
ok = cfg.is_file()
detail = f"config.json 不在 Frameworks/Resources 下（找的是 {cfg}）"
if ok:
    try:
        d = json.loads(cfg.read_text(encoding="utf-8"))
    except Exception as e:
        d = {}
        detail = f"config.json 读不出来：{type(e).__name__}"
    if d:
        keys = ("oss_access_key_id", "oss_access_key_secret")
        have = [k for k in keys if str(d.get(k) or "").strip()]
        ok = len(have) == 2
        detail = (f"OSS 两把密钥齐（deepseek={'有' if str(d.get('deepseek_api_key') or '').strip() else '无'}）"
                  if ok else f"密钥不全，只读到 {have or '无'}")
    # 本轮 Mac 实机崩在「包内只读还要 mkdir」，配套修好的口径文件必须真的进包：
    # 少它就是开机即崩，与「少一个平台」同属静默交付缺陷，只能在这里拦住。
    rp = cfg.parent / "riskbird_build" / "runtime_paths.py"
    if not rp.is_file():
        ok = False
        detail += "；但 riskbird_build/runtime_paths.py 没进包（会开机即崩）"
    else:
        detail += "，runtime_paths.py 在包内"
# 模块收没收（tool_config 这类）不在这里查：onedir 的模块藏在归档里，按文件名找
# 是靠猜布局。这一层由 CI 里跑在 arm64 runner 上的 --acl-doctor 产物门禁负责，
# 它跑的是真产物，判据比任何结构猜测都硬。
print(f"[mac] {'✅' if ok else '❌'} {detail}")
sys.exit(0 if ok else 1)
PYEOF
}

FAIL=0
WIN_ROOT="$WORK/win/root"
WIN_SETUP=$(find "$WORK/win" -maxdepth 2 -name "B2bCollectToolSetup.exe" -type f | head -1)
MAC_APP=$(find "$WORK/mac" -maxdepth 4 -name "B2bCollectTool.app" -type d | head -1)
check_win "$WIN_ROOT" "$WIN_SETUP" || FAIL=1
check_mac "$MAC_APP" || FAIL=1
[ "$FAIL" = 0 ] || { echo "❌ 产物校验未通过，本地 dist 保持原样（不覆盖成坏包）"; exit 5; }

# 交付物落到本地 dist：安装包与绿色版是给用户的两份产物，使用说明随绿色版 zip 一起。
mkdir -p b2bcollect_build/dist
cp -f "$WIN_SETUP" b2bcollect_build/dist/B2bCollectToolSetup.exe
cp -f "$(find "$WORK/win" -maxdepth 2 -name B2bCollectTool_Win_portable.zip | head -1)" \
  b2bcollect_build/dist/B2bCollectTool_Win_portable.zip
find "$WORK/mac" -name "B2bCollectTool_Mac_arm64.zip" -exec cp -f {} b2bcollect_build/dist/B2bCollectTool_Mac_arm64.zip \;
ls -l b2bcollect_build/dist/B2bCollectToolSetup.exe \
      b2bcollect_build/dist/B2bCollectTool_Win_portable.zip \
      b2bcollect_build/dist/B2bCollectTool_Mac_arm64.zip
echo DONE
