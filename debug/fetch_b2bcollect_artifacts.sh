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
REPO_DIR=$(pwd)
# GIT_TERMINAL_PROMPT=0 不是装饰：helper 里没有 github.com 凭证时，`git credential fill`
# 会去开 /dev/tty 问用户名密码——它读的不是上面那条 printf 的 stdin，所以「管道里已经喂了
# 东西」并不能挡住它。这一刻和第 20 条管的是同一件事：进程不会自己停，日志一个字都不留，
# 而人只能对着「取制品」那行标题等。关掉终端交互后它当场失败，落到下面那句 NO_TOKEN 上。
TOK=$(printf 'protocol=https\nhost=github.com\n\n' | GIT_TERMINAL_PROMPT=0 git credential fill | sed -n 's/^password=//p')
[ -n "$TOK" ] || { echo "NO_TOKEN：git credential helper 里没有 github.com 凭证"; exit 2; }
API="https://api.github.com/repos/JHnoplan/riskbird-tool"

# ── 每一次 curl 都必须自己会停（硬门第 20 条 20a）──
# 事故形状（2026-11-13 本机，连着两次）：外层 timeout 把取制品杀掉，rc=124，日志停在
# 「[$key] 下载中」之后一字节解释都没有。原因不是网络坏，是 curl 的默认口径：它只在
# 连不上的时候自己退，连上以后 26 KB/s 它认为完全正常，于是「慢」在外面看就成了「挂」。
# API 这类小响应给硬上限（40 秒足够），体下载那一处走的是另一套（停顿判定 + 续传），
# 因为几百 MB 的传输里，任何「够用」的总时限都同时在「弄死正常的慢链路」。
CACHE_DIR=${B2B_FETCH_CACHE:-$REPO_DIR/ci_dl/artifacts}
CHUNK_BYTES=${B2B_FETCH_CHUNK:-8388608}     # 8 MiB：够大不让请求数失控，够小让一次重置只丢一块
PARALLEL=${B2B_FETCH_PARALLEL:-6}           # 同时开几条连接（实测 4 条已把聚合速度乘到 4 倍以上）
CHUNK_ATTEMPTS=${B2B_FETCH_ATTEMPTS:-12}    # 单块最多重试几轮；连续无进展就停下来说话，不空转
ART_ZIP=""                                   # fetch_artifact 成功后把 zip 路径放这里给调用方

# 制品字节的落点为什么必须是缓存（硬门第 20 条 20b/20c 的由来，本机实测在下面）：
#   ① curl 默认不在「慢」上自己停——它只在连不上的时候退，26 KB/s 它认为完全正常；
#   ② 限速挂在单条连接上，不在链路上，所以一条连接慢就永远慢；
#   ③ zip 落在 mktemp 出来的 WORK 里，trap EXIT 顺手删掉——每次重跑都从第 0 字节开始。
# 本机实测（2026-11-13 21:48–21:51，同一个 artifact 14034890976，同一台机器）：
#   · 单连接 Range 取 2 MiB：182.98 秒 = 11.5 KB/s；照这个速度 261 MB 要 6.3 小时；
#   · 四连接各取 2 MiB：总 166 秒拿到 8 MiB ≈ 50 KB/s——并发确实乘得动；
#   · 那四路里有一路中途 `curl: (56) Recv failure: Connection reset by peer`，只落了
#     1899440/2097152 字节。这一条正是分块的真正理由：连接随时会被重置，重置发生在第
#     250 块和发生在第 1 块的代价完全不同——旧写法里它等于整包从头再来。
# 缓存目录在仓库里、且已在 .gitignore（ci_dl/）：里面全是一次请求就能重下的可再生字节，
# 绝不该入库，但也绝不该每次被删。
api_get() {  # $1=URL；带界、带重试，取不回印空串（调用方必须自己判空，不许把空当「没有」）
  local i out
  for i in 1 2 3; do
    out=$(curl -sS --connect-timeout 15 -m 40 --retry 2 --retry-all-errors \
               -H "Authorization: Bearer $TOK" -H "Accept: application/vnd.github+json" \
               -H "X-GitHub-Api-Version: 2022-11-28" "$1" 2>/dev/null) && { printf '%s' "$out"; return 0; }
    sleep 3
  done
  printf ''
  return 1
}
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
WORK_AUTO=0
[ -n "${B2B_FETCH_WORK:-}" ] || WORK_AUTO=1
# 一份 EXIT 处理，两件事都要做：收后台下载进程、收工作目录。为什么写成同一个函数而不是
# 两条 trap——bash 每个信号只有一个处理器，后来那条 `trap 'bg_reap' EXIT` 会把这条
# `rm -rf "$WORK"` **顶掉**，于是本小节专门修的「崩一次留一份 883MB」原地复活，而且日志
# 里一行痕迹都没有。TERM/INT 只负责退出，清理统一走 EXIT（幂等）。
on_exit() {
  bg_reap
  [ "$WORK_AUTO" = 1 ] && rm -rf -- "$WORK" 2>/dev/null
  return 0
}
trap 'on_exit' EXIT
trap 'exit 143' TERM INT
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
  SHA=$(api_get "$API/git/ref/heads/main" | python3 -c 'import json,sys
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
# 第三格是「为什么没有」：API 一个字节没回来（令牌失效、404、超时）与「这一轮真的没起
# 来」在旧写法里长得一样——都是空 id + 一句「推送没触发？」，于是人会去查推送，而毛病
# 在凭证或网络上（poll 脚本 2026-11-12 修的第一条就是同一族：空令牌静默空跑 40 轮）。
find_run() {
  api_get "$API/actions/workflows/$2/runs?head_sha=$SHA&per_page=1" \
  | python3 -c 'import sys,json
raw = sys.stdin.read()
if not raw.strip():
    print("", "none", "api_unreachable")
else:
    try:
        runs = json.loads(raw).get("workflow_runs") or []
    except Exception:
        runs = None
    if runs is None:
        print("", "none", "api_unparsable")
    else:
        r = runs[0] if runs else {}
        print(r.get("id", ""), r.get("status", "none"), r.get("conclusion") or "")'
}

declare -a IDS
for pair in "win:build-b2bcollect.yml" "mac:build-b2bcollect-mac.yml"; do
  key=${pair%%:*}; wf=${pair##*:}
  read -r id status concl <<<"$(find_run "$key" "$wf")"
  [ -n "$id" ] || {
    case "$concl" in
      api_unreachable) echo "[$key] API 一个字节都没回来（令牌失效 / 网络 / 限流），判不了这一轮在不在——这不是「云端没打包」" ;;
      api_unparsable)  echo "[$key] API 回来的不是预期 JSON（改网关了？被登录页挡了？），判不了这一轮在不在" ;;
      *)               echo "[$key] 本次 commit 没有 $wf 的 run（推送没触发？先确认 commit 已推上 main）" ;;
    esac
    exit 3
  }
  echo "[$key] run=$id $status $concl"
  IDS+=("$key:$id")
done

# 字节级等价证明（放在等结果**之前**：内容都不等价就别浪费这二十分钟轮询）。
# 号不同、tree 相同 = 用 Git Data API 快进推送的正常结果，取回来的就是这个内容；
# tree 不同 = 本地还有没推上去的改动（或远端多了别的），这一份制品不代表刚在本机跑绿的
# 那份代码——必须停下来说清楚。旧写法是「按本地 HEAD 查 run，查不到才停」，既报错了
# 原因（说的是「推送没触发」），又挡不住真正的风险：拿一个内容不同的旧包交付。
REMOTE_TREE=$(api_get "$API/git/commits/$SHA" | python3 -c 'import json,sys
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

# 等两条 run 出结论。这里刻意不写成「一次 curl 到底」：每一轮都是独立的一次有界请求，
# 网络断了就报本轮取不到，下一轮接着问（旧写法把 json.load 直接接在无界 curl 后面，
# API 一旦回 404/限流，python 抛 KeyError 把 traceback 混进状态行，case 两路都不命中，
# 于是 90 轮里一行有效判断都不做，静静走到「下载前状态」那一步再报一句不相干的话）。
for kv in "${IDS[@]}"; do
  key=${kv%%:*}; id=${kv##*:}
  for i in $(seq 1 90); do
    ST=$(api_get "$API/actions/runs/$id" \
      | python3 -c 'import sys,json
raw = sys.stdin.read()
try:
    r = json.loads(raw)
except Exception:
    r = {}
print(r.get("status", "unreachable"), r.get("conclusion") or "", str(r.get("head_sha") or "")[:7])')
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
  FIN=$(api_get "$API/actions/runs/$id" \
    | python3 -c 'import sys,json
raw = sys.stdin.read()
try:
    r = json.loads(raw)
except Exception:
    r = {}
print(r.get("status", "unreachable"), r.get("conclusion") or "", str(r.get("head_sha") or "")[:7])')
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
# 注意那句 "disk full?" 只是它**写不下去时顺口说的猜测**，不是结论：同一批字节腾出空间后
# 照样红，真因见下面解包口径那段（中央目录 create_system 那一字节 + 非 ASCII 条目名），
# 生产者早已改成 ASCII 条目名；unzip_probe 只留在这里记账，谁再怀疑磁盘就跑一次。
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

# 缓存也会攒：一次 commit 一对 zip（各几百 MB），攒七天才收——太短会把「今天重跑还要用」
# 的字节扔掉（那正是本轮加缓存要救的东西），太长就又回到「失败会自己攒成下一次的失败」。
# 带活锁的那一份绝不碰：并发跑着的下载，文件 mtime 只在一块落地时更新，光看时间判不出
# 「正在写」，所以锁才是依据。
CACHE_KEEP_DAYS=${B2B_FETCH_KEEP_DAYS:-7}
if [ -d "$CACHE_DIR" ]; then
  freed=0
  for d in "$CACHE_DIR"/*; do
    [ -e "$d" ] || continue
    case "$d" in *.lock) continue ;; esac
    [ -d "${d%.parts}.lock" ] && continue          # 这一份正被别的进程写着
    [ -n "$(find "$d" -maxdepth 0 -mtime -"$CACHE_KEEP_DAYS" 2>/dev/null)" ] && continue
    freed=$(( freed + $(du -k "$d" 2>/dev/null | awk 'NR==1 {print $1}') ))
    rm -rf -- "$d"
  done
  [ "$freed" -gt 0 ] && echo "· 收掉 $CACHE_KEEP_DAYS 天没再碰过的旧制品缓存 $(( freed / 1024 )) MB（${CACHE_DIR}）"
fi

NEED=0
UNKNOWN=0
for kv in "${IDS[@]}"; do
  key=${kv%%:*}; id=${kv##*:}
  SZ=$(api_get "$API/actions/runs/$id/artifacts" | python3 -c 'import sys,json
try:
    a = json.load(sys.stdin).get("artifacts") or []
except Exception:
    a = None
print(sum(int(x.get("size_in_bytes") or 0) for x in a) if a else -1)')
  case "$SZ" in
    ""|"-1") UNKNOWN=$((UNKNOWN + 1)); echo "· [$key] API 没答出制品大小（这一路按保守下限判，不当「不需要空间」）" ;;
    *) NEED=$((NEED + SZ))
       echo "· [$key] 制品内容约 $(( SZ / 1048576 )) MB（这是**解压后**的口径，zip 通常比它小）" ;;
  esac
done
# 空间口径（两个系数都得说清凭什么）：
#   ×3 解压后比 zip 大得多（.app 与 onedir 里全是压不动的可执行文件）；
#   +1 缓存目录里还留着 zip 本身——这是本轮新加的落点（下面 fetch_artifact），它跨次存活，
#     不再随 WORK 一起被 trap 删掉，所以它占的那份也必须算进「这次装不装得下」。
# 外加 500MB 余量。问不到数字时不许退化成「不判」：给一个下限，宁可停下来说话。
NEED_KB=$(( (NEED * 4 + 524288000) / 1024 ))
if [ "$UNKNOWN" -gt 0 ] && [ "$NEED_KB" -lt 2097152 ]; then
  NEED_KB=2097152
fi
AVAIL_KB=$(df -kP "$WORK" 2>/dev/null | awk 'NR==2 {print $4}')
if [ -z "$AVAIL_KB" ]; then
  echo "❌ 问不出 ${WORK} 所在卷的可用空间（df 没给数）：空间不够时 unzip 不会干净地报错退出——写不下去它会回头问「Continue? (y/n)」，后台那条路没人答就整条挂住（本机实测：问句拿不到 stdin 就 8 秒不结束）。判不了就不下载。"
  exit 7
fi
echo "· 磁盘：可用 $((AVAIL_KB / 1024)) MB，本次至少要 $((NEED_KB / 1024)) MB（解压×3 + 缓存×1 + 500MB 余量${UNKNOWN:+，含 $UNKNOWN 路 API 没答出大小}）"
if [ "$AVAIL_KB" -lt "$NEED_KB" ]; then
  echo "❌ 磁盘不够：${WORK} 所在卷只剩 $((AVAIL_KB / 1024)) MB，取这一批制品至少要有 $((NEED_KB / 1024)) MB。不在这里停下来的话，坏法不是「报一条错」，而是下到一半解包回头问人、整条交付链挂住，或在半截字节上做完校验还报 ✅。"
  echo "   · 可以先收的（都是可再生的）：TMPDIR 里往次的 b2b_dl.*（本脚本上面已按规则收过一轮）、b2bcollect_build/build/（PyInstaller 中间产物）、$CACHE_DIR 里的旧制品缓存（B2B_FETCH_KEEP_DAYS=0 可强制全收）、本机 dist 里不需要的旧包。"
  exit 7
fi

mkdir -p "$WORK" || { echo "❌ 工作目录建不出来：${WORK}"; exit 2; }
if [ -n "${B2B_FETCH_WORK:-}" ]; then
  find "$WORK" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
fi
echo "· 工作目录：$WORK"
# 解包口径：优先 ditto，unzip 只作兜底。要钉的门是「不交互 + 接住退出码」这两条。
# 三条实测（2026-11-13 本机，同一份 zip，脚本 unzip_probe 里可复跑）：
#   · unzip 6.00（Apple 改过的那份）会**回头问人**：凡是它觉得需要答复的场合（同名
#     文件已存在、写不下去、路径不对），它把问题打到 stderr 然后等 stdin。把 stdin 接
#     到一个没人写的管道上，8 秒它还没结束——整条取制品就挂在那里，看起来像「还在下载」。
#   · 同一条命令把 stdin 接到空设备：0.2 秒按默认答复「[N]one」走人，rc=1。不挂，
#     退出码还接得住，这才是后台（nohup / CI）能用的形状。
#   · ditto 同一场景 0.018 秒解完、rc=0：它压根没有「问句」这个概念。
# 这两条与文件名无关，所以口径不变。变的是第三句我先前写错的话，现在按字节改完：
#   ✗ 先前写「Apple unzip 不认中文条目名的 UTF-8 标志位」——错，它认（标志位置着时名字
#     解出来原样正确）。
#   △ 上一轮又写成「macOS 的 unzip 读中文名是对的，真因至今没有定论」——只对一半：这句
#     只在**中央目录声明这包是 Unix 打的**时成立。
# 定论（2026-11-13 本机字节级对拍，同一批字节来回改一个字节）：决定成败的是中央目录记录
# 里 version made by 的高字节 create_system——3(Unix) 时 unzip 按本地编码直取名字字节；
# 0(DOS/FAT) 时它把名字再过一遍 OEM→Native 转换，得到一个写不下去的名字，于是报
# `write error (disk full?). Continue? (y/n)`（那句只是它顺口说的猜测，那块卷还剩 9 GB）。
# CPython 的 zipfile 在 win32 上把这个字节写死成 0，所以「Windows runner 打的包 + 非 ASCII
# 条目名」迟早红在用户手里，而构建日志全绿。修复在生产者那一头（绿色版条目名一律 ASCII，
# 中文名由 NSIS 的 /oname 给），并由 .github/scripts/check_release_zip.py 在两个平台的
# 交付链上各拦一次；这一头的差分探针退化成纯记账：下次谁再怀疑磁盘，跑一次就看清事实。
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
  # 日志里混着解包工具按本地编码打出来的名字字节（不一定是合法 UTF-8）。mac 的 tr 在
  # UTF-8 locale 下遇到非法字节会报 `Illegal byte sequence` 并把这一行丢掉——取证的那一句
  # 恰恰在最需要的时候消失。tr 只按字节做事就够了，所以这里显式给它 C locale。
  echo "[$2] 差分探针：unzip rc=${rc}、落地 ${mine} 个文件${ref}；它自己说的那句：$(tail -2 "$lg" | LC_ALL=C tr '\n' ' ')"
  [ -s "$lg" ] || echo "   （unzip 一字节没抱怨：条目名全 ASCII 时就是这样，creator 那一字节影响不到它）"
  rm -rf "$p" "$lg"
  return 0
}
FAIL=0

# ── 后台块下载的进程账：本脚本自己生的每一个子进程都要能收掉 ─────────────────────
# 为什么这一小节必须存在（2026-11-13 本机实跑撞到的）：把取制品脚本杀掉之后，它 spawn
# 的 curl **还活着**，而且继续往同一批块文件里追加字节（父进程没了，子进程被 init 收养，
# 一条 SIGTERM 只管到脚本本身）。这时候重跑脚本，新的 curl 和活的旧 curl 一起 append 同
# 一个文件——交错写出来的 zip 大小照样对得上，内容却是花的。锁只管得住「脚本级」并发，
# 管不住这一层，所以：①退出前按 pid 收掉自己开的那一批（先收 curl，再收子壳）；
# ②接手一把死锁之前，先看还有没有活进程手里拿着同一个下载 URL。
BGS=""
bg_reap() {  # 收掉本轮开出去的后台块下载（幂等：账清完再叫一次什么也不做）
  local p
  for p in $BGS; do
    pkill -P "$p" 2>/dev/null          # 子壳里的 curl 才是真正在写块文件的那一个
    kill -TERM "$p" 2>/dev/null
  done
  BGS=""
}
# 调用点在上面的 on_exit（EXIT/TERM/INT 都走那一条，见 WORK 那一段的说明）。

total_of_zip() {  # $1=artifact id；问出这个制品 zip 的字节数，问不到印空
  local hdr
  hdr=$(curl -sS -fL --connect-timeout 30 -m 90 --retry 3 --retry-all-errors \
             -H "Authorization: Bearer $TOK" -r 0-0 -o /dev/null -D - \
             "$API/actions/artifacts/$1/zip" 2>/dev/null | tr -d '\r')
  # 格式依据：这里不依赖任何工具的话术，只依赖 HTTP 标准里 Content-Range 那一条的定义
  # （`bytes <首>-<尾>/<完整长度>`，RFC 9110 §14.2）。-D - 会把 302 与 206 两段头一起打
  # 出来，所以扫的是**所有**出现过的 Content-Range，取带完整长度那一条；一条都没有就是
  # 服务端没接 Range（印空，调用方按「问不到」处理，绝不退化成「当成 0 字节」）。
  printf '%s\n' "$hdr" | python3 -c 'import re,sys
m = re.findall(r"(?im)^content-range:\s*bytes\s+\d+-\d+/(\d+|\*)\s*$", sys.stdin.read())
good = [x for x in m if x.isdigit()]
print(good[-1] if good else "")'
}

get_chunk() {  # $1=起始 $2=终止 $3=块文件 $4=下载 URL $5=错误日志；0=这块字节齐了
  local s=$1 e=$2 p=$3 u=$4 errlog=$5 have want att=0 prev="" idle=0 rc
  want=$(( e - s + 1 ))
  while :; do
    # 块文件不存在就是「这块还没下到一个字节」，这是一条正常的路，不是错误：直接 `< "$p"`
    # 会让 bash 在 exec 之前就把「No such file or directory」打到 stderr（2>/dev/null 拦
    # 不住它，那是给命令的，不是给 shell 的），日志里每一块都冒一行假错误——真出错的那
    # 几种反而混在里面看不出来。所以先看文件在不在。
    if [ -f "$p" ]; then have=$(wc -c < "$p" | tr -dc 0-9); else have=0; fi
    have=${have:-0}
    if [ "$have" -ge "$want" ]; then
      # 比预期长只可能是上一次崩在多写了一截：按块边界切掉，别让拼出来的 zip 错位
      [ "$have" -gt "$want" ] && truncate -s "$want" "$p" 2>>"$errlog"
      return 0
    fi
    att=$(( att + 1 ))
    if [ "$have" = "$prev" ]; then idle=$(( idle + 1 )); else idle=0; fi
    prev=$have
    # 连续 3 轮一字节没涨＝这条路已经不动了；继续重试只会把日志写满而字节不会多一个，
    # 所以停下来点名（旧写法是 --retry 8 之后再无判定：八次全挂在同一个不动的 socket 上，
    # 每次重跑都一样，谁也看不出它其实没在下载）。
    # 「放弃」这个前缀是失败信号本身，不是给人读的修饰：curl 被 -S 放行的那些真报错（28/56
    # 这些**被容忍的**慢与断）也写同一份日志，所以「日志非空」不能当失败判据——第一次实跑
    # 就是这个形状：一路下全，照样报红。判据只认这一句是谁写的。
    [ "$idle" -ge 3 ] && { echo "放弃：块 $s-$e 连续 $idle 轮零进展（已 $have/$want 字节）" >>"$errlog"; return 1; }
    [ "$att" -gt "$CHUNK_ATTEMPTS" ] && { echo "放弃：块 $s-$e 已试 $att 轮，仍差 $(( want - have )) 字节" >>"$errlog"; return 1; }
    curl -sSfL --connect-timeout 30 --speed-limit 2048 --speed-time 180 -m 1800 \
         -H "Authorization: Bearer $TOK" -r $(( s + have ))-$e -o - "$u" \
         < /dev/null >> "$p" 2>>"$errlog"
    rc=$?
    # `-s` 不是可省的装饰：curl 的进度表是往 stderr 写的，而这里 stdout 已经被 body 占了
    # （-o -），stderr 又正好是 errors.log。第一次实跑就在 errors.log 里攒了 40 KB 进度条
    # 花屏，而下面「errors.log 非空即失败」的判据把它当成了失败——一次下载全成、报红。
    # 留 `-S`（silent-but-show-errors）：真正的报错仍然进得到账。
    # 0 下完 / 28 本轮总时限到（接着要下一段 Range）/ 56 连接被重置（同上，字节留在块文件里）
    # / 22 HTTP 错误——每一次重试都是重新走一遍 302，签名链接因此每次都是新铸的，所以
    # 「上一轮的链接过期了」正好靠重发解决；真要是令牌被吊销这种一直 403，上面那条零进展
    # 判定三轮就会把这一块停掉，不会 12 轮空转。除此之外才是真出事，点名退出码。
    # 这里只保证不把坏字节写进块里：-f 已经让错误响应体不进 stdout。
    [ "$rc" = 0 ] || [ "$rc" = 22 ] || [ "$rc" = 28 ] || [ "$rc" = 56 ] || {
      echo "放弃：块 $s-$e curl 退出码 ${rc}（不是「慢/断/链接过期」那几种）" >>"$errlog"; return 1; }
  done
}

fetch_artifact() {  # $1=平台 key $2=run id；成功时设 ART_ZIP
  local key=$1 id=$2 aid url zip parts lock err total n i j idx s e k want
  local p pp f nmax cur orphans running gaveup
  aid=$(api_get "$API/actions/runs/$id/artifacts" | python3 -c 'import sys,json
try:
    a = json.load(sys.stdin).get("artifacts") or []
except Exception:
    a = []
print(a[0].get("id", "") if a else "")')
  [ -n "$aid" ] || { echo "[$key] ❌ 问不出 artifact id（API 没答或这一轮压根没有制品）——不拿上一次缓存冒充本轮字节"; return 1; }
  url="$API/actions/artifacts/$aid/zip"
  zip="$CACHE_DIR/$key-$aid.zip"
  parts="$CACHE_DIR/$key-$aid.parts"
  lock="$CACHE_DIR/$key-$aid.lock"
  mkdir -p "$CACHE_DIR" || { echo "[$key] ❌ 缓存目录建不出来：$CACHE_DIR"; return 1; }

  total=$(total_of_zip "$aid")
  if [ -z "$total" ]; then
    echo "[$key] ❌ 问不出制品 zip 的总字节数（服务端没给 Content-Range？）：不知道终点就没法判「下全了没」，"
    echo "       而「没下全却解得开」正是最贵的那种红——这一趟停在这里，不动用旧缓存。"
    return 1
  fi
  if [ -f "$zip" ] && [ "$(wc -c < "$zip" | tr -dc 0-9)" = "$total" ]; then
    ART_ZIP="$zip"
    echo "[$key] 缓存里已有完整的 $(( total / 1048576 )) MB（本轮一个字节没下）：$(basename "$zip")"
    return 0
  fi

  # 切块计划先算出来：下面「收孤儿之后按块边界裁剪」要知道每一块的合法长度，而它必须
  # 发生在任何下载之前。8 MiB 一块，最后一块按 total 收边。块文件名就是它的块号，所以
  # 「哪几块齐了」不看台账、只看文件——台账会撒谎（崩在写台账之前），文件不会。
  n=$(( (total + CHUNK_BYTES - 1) / CHUNK_BYTES ))

  # 锁之前先问「有没有活着的写者」，这一步不能只放在抢锁那一段里：被 SIGKILL 杀掉的那一次
  # 连锁都来不及留下，但它 spawn 的 curl 还活着、还在往同一批块文件里追加字节。只看锁会
  # 放这一种过去，而它的后果是两批进程交错 append 同一个块——拼出来的 zip 字节数正好对上
  # total，内容却是花的（zip 解包与 check_release_zip 真解一次是最后接住它的两道，但不能
  # 靠下游兜：把坏字节拼出来再指望它解不开，等于把交付的判定权交给运气）。
  # 按父进程分两类处理：ppid=1 是「父进程已经没了」的孤儿 → 收掉；ppid 还活着 → 那是
  # 隔壁一趟正在跑的取制品，绝不与它抢同一批字节（也不去杀它）。
  orphans=""
  running=""
  for p in $(pgrep -f -- "$url" 2>/dev/null); do
    pp=$(ps -o ppid= -p "$p" 2>/dev/null | tr -dc 0-9)
    if [ "${pp:-0}" = "1" ]; then orphans="$orphans $p"; else running="$running $p"; fi
  done
  if [ -n "$running" ]; then
    echo "[$key] ❌ 另一个还活着的取制品进程正在下这一份（pid ${running}）：不并发写同一批 zip，这一趟就此打住。"
    return 1
  fi
  if [ -n "$orphans" ]; then
    echo "[$key] · 上一趟被杀掉时没跟着退的下载进程（pid ${orphans}）还握着同一批块文件：按 pid 收掉再继续"
    kill -TERM $orphans 2>/dev/null
    sleep 3
    for p in $orphans; do
      kill -0 "$p" 2>/dev/null && { echo "[$key] ❌ 收不掉 pid ${p}（TERM 之后还活着）：这一趟不停下来等它，就是两批进程往同一块里交错 append"; return 1; }
    done
    # 孤儿在被收掉之前可能已经多写了一截：把超过块边界的那一段切回去（不删整块——删了
    # 就又回到「从头再来」，那正是本轮要治的病）。块号就是文件名，最后一块按 total 收边。
    for f in "$parts"/[0-9]*; do
      [ -f "$f" ] || continue
      nmax=$CHUNK_BYTES
      [ "$(basename "$f")" = "$(( n - 1 ))" ] && nmax=$(( total - (n - 1) * CHUNK_BYTES ))
      cur=$(wc -c < "$f" | tr -dc 0-9)
      [ "${cur:-0}" -gt "$nmax" ] && truncate -s "$nmax" "$f" 2>/dev/null
    done
  fi

  # 同一份制品只许一个进程在写：锁按 artifact id 建，mkdir 是原子的。
  # 持有者已经不在了（被杀掉的那次留了锁）就按 pid 收掉——否则「崩一次锁一把」会和
  # 当年「崩一次留一份 883MB 垃圾」一样，让这个脚本每次失败后都更难跑成功一次。
  if ! mkdir "$lock" 2>/dev/null; then
    local holder
    holder=$(cat "$lock/pid" 2>/dev/null | tr -dc 0-9)
    if [ -n "$holder" ] && kill -0 "$holder" 2>/dev/null; then
      echo "[$key] ❌ 另一个取制品进程（pid ${holder}）正在写同一份字节；不并发写同一批 zip。"
      return 1
    fi
    echo "[$key] · 收掉死进程 ${holder:-未知} 留下的锁：$(basename "$lock")"
    rm -rf -- "$lock"; mkdir "$lock" || { echo "[$key] ❌ 锁建不出来：$lock"; return 1; }
  fi
  printf '%s\n' "$$" > "$lock/pid"
  err="$parts/errors.log"
  mkdir -p "$parts"
  # 每跑一次先撕掉上一次的错误记录：块文件是留着续传的（那正是进度），但「上一轮失败过」
  # 不能跟着留下来——否则下一次明明一路下全，也会在这一行被自己的旧字节判成失败。
  rm -f -- "$err"

  echo "[$key] 下载 $(( total / 1048576 )) MB：$n 块 × $(( CHUNK_BYTES / 1048576 )) MiB，同时 $PARALLEL 条（实测本机单连接只有 11.5 KB/s，四并发能到 50 KB/s）"
  i=0
  while [ "$i" -lt "$n" ]; do
    for j in $(seq 0 $(( PARALLEL - 1 ))); do
      idx=$(( i + j )); [ "$idx" -ge "$n" ] && continue
      s=$(( idx * CHUNK_BYTES ))
      e=$(( s + CHUNK_BYTES - 1 )); [ "$e" -ge "$total" ] && e=$(( total - 1 ))
      want=$(( e - s + 1 ))
      # 已到位的块直接跳过：这一条就是「跨次存活」的落点，字节留在盘上，重跑不重下。
      if [ -f "$parts/$idx" ] && [ "$(wc -c < "$parts/$idx" | tr -dc 0-9)" = "$want" ]; then
        continue
      fi
      # 进度只在这一批开始时印一次：一条连接一行，六条并发一起往日志里刷会互相打断，
      # 最后谁也读不出「现在到第几块」。
      echo "[$key]   第 $(( idx + 1 ))/$n 块（$(( s / 1048576 ))–$(( e / 1048576 )) MB）"
      get_chunk "$s" "$e" "$parts/$idx" "$url" "$err" & BGS="$BGS $!"
    done
    wait
    BGS=""                       # 这一批都回来了，账要清：下一批的 pid 不能混进上一批里
    i=$(( i + PARALLEL ))
  done

  # 失败判据只认「放弃」那一句（见 get_chunk 里的注释）：errors.log 里同时还有 curl 自己
  # 被 -S 放行的真报错，那些是**被容忍的**慢与断，拿「文件非空」当失败会把一路下全的那一趟
  # 判成红。反过来，一句放弃都没有也不许当成功——所以下面拼装前还要逐块核对字节数。
  # 这两处 grep 的字节出处（硬门第 14 条）：
  #   · `^放弃` 那一路的**格式依据**是本文件自己写的两行 echo——「放弃：块 $s-$e 连续
  #     $idle 轮零进展（已 $have/$want 字节）」与「放弃：块 $s-$e curl 退出码 ${rc}（…）」，
  #     写它和读它都在同一份脚本里，不依赖任何外部工具的话术；
  #   · 其余那些是 curl 打到 stderr 的字节，本机 2026-11-13 21:48–21:51 那趟四并发实测到的
  #     原字节是 `curl: (56) Recv failure: Connection reset by peer`（同一趟那一路只落了
  #     1899440/2097152 字节）。这里只把它当证据印出来给人看，一个字段都不从它身上取。
  gaveup=$(LC_ALL=C grep -c '^放弃' "$err" 2>/dev/null)
  if [ "${gaveup:-0}" != 0 ]; then
    echo "[$key] ❌ 有 ${gaveup} 块被判定放弃（curl 与判定自己说的是什么，原样贴出）："
    LC_ALL=C grep '^放弃' "$err" | sed 's/^/     /'
    echo "   · 其余诊断（curl 的慢/断/过期，被容忍的那些）："
    LC_ALL=C grep -v '^放弃' "$err" | tail -6 | sed 's/^/     /'
    echo "   · 已下到的块留在 $parts 里：下一次重跑接着这些块继续，不从头再来。"
    rm -rf -- "$lock"
    return 1
  fi

  # 全部就位才拼装：按块号升序一块一块追进 .building，字节数对上 total 才改名交付——
  # 半截的 zip 永远不可能被当成「完整的那一份」。这里不用 `cat $(…)`：路径带空格时它会
  # 散成好几个参数（本仓路径今天没空格，明天不一定），而少拼一块的 zip 依然解得开前几个
  # 文件，是最难看出来的一种坏。
  : > "$zip.building" || { echo "[$key] ❌ 拼装落点建不出来：$zip.building"; rm -rf -- "$lock"; return 1; }
  k=0
  while [ "$k" -lt "$n" ]; do
    if ! cat "$parts/$k" >> "$zip.building"; then
      echo "[$key] ❌ 第 $(( k + 1 ))/$n 块不在位（下载段没报错却没落地＝最阴的一种）：逐块字节自己看"
      ls -l "$parts" | sed 's/^/     /'
      rm -f -- "$zip.building"; rm -rf -- "$lock"
      return 1
    fi
    k=$(( k + 1 ))
  done
  if [ "$(wc -c < "$zip.building" | tr -dc 0-9)" != "$total" ]; then
    echo "[$key] ❌ 拼出来 $(( $(wc -c < "$zip.building") / 1048576 )) MB ≠ 服务端说的 $(( total / 1048576 )) MB：这一批不换本地 dist"
    rm -f -- "$zip.building"; rm -rf -- "$lock"
    return 1
  fi
  mv -f "$zip.building" "$zip"
  rm -rf -- "$parts" "$lock"
  ART_ZIP="$zip"
  echo "[$key] 制品字节已完整落在缓存：$(basename "$zip")（$(( total / 1048576 )) MB）"
  return 0
}

for kv in "${IDS[@]}"; do
  key=${kv%%:*}; id=${kv##*:}
  fetch_artifact "$key" "$id" || { echo "❌ [$key] 制品字节没拿全，本地 dist 保持原样"; exit 6; }
  if ! extract_zip "$ART_ZIP" "$WORK/$key"; then
    NOW_KB=$(df -kP "$WORK" 2>/dev/null | awk 'NR==2 {print $4}')
    echo "[$key] ❌ 外层制品 zip 解不开（此刻可用 $(( ${NOW_KB:-0} / 1024 )) MB）：字节数已经和服务端对过，所以先怀疑 zip 本身"
    unzip_probe "$ART_ZIP" "$key.outer" "$WORK/$key"
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
    echo "[$key] 已解开内层：$(basename "$INNER") → $(ls "$WORK/$key/root/dist" | LC_ALL=C tr '\n' ' ')"
  fi
  # 交付 zip 的门在「即将换进本地 dist 的这批字节」上再过一次。判据只有一处实现
  # （.github/scripts/check_release_zip.py）：云端装配那一步跑的是它，这里跑的也是它。
  # 为什么两处都要跑，而不是「云端验过就够了」：从 runner 到这台机器之间还隔着一次归档
  # 换形（Actions 把产物重新打包）与一次网络传输，而用户拿到的正是这一头的字节。本轮
  # 那次非 ASCII 条目名红在用户手里、构建日志全绿——缺的就是这一头的一道真解一次。
  if [ "$key" = "mac" ]; then
    ZGATE_ARGS=(--top B2bCollectTool.app)
  else
    ZGATE_ARGS=(--top B2bCollectTool --root-file usage_guide.txt)
  fi
  # < /dev/null 不是装饰（硬门第 20 条 20f）：这一趟本机实发过——后台终端被回收后 fd 0 是个
  # 已关闭的描述符，python 在跑第一行之前就崩 `init_sys_streams / Bad file descriptor`，
  # 于是「门不合格」那句成了唯一线索，而真正的失败是门根本没跑起来。
  if ! python3 .github/scripts/check_release_zip.py "$INNER" "${ZGATE_ARGS[@]}" --os-extract < /dev/null; then
    echo "[$key] ❌ 交付 zip 的门不合格（条目名字节或本机真解一次对拍）：这一批不换本地 dist"
    exit 6
  fi
  echo "[$key] artifact 内容：$(find "$WORK/$key" -maxdepth 2 -type f | head -3 | LC_ALL=C tr '\n' ' ')"
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
