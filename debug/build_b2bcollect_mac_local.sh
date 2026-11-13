#!/usr/bin/env bash
# b2bcollect 工具的 macOS 本地打包（一键、带产物自检门禁）。
#
# 为什么要这么个脚本（2026-11 外部机器全员精简版的教训）：
#   云端 workflow 是主打包路径，但「本机现在就要一个能跑的包」这件事一直没人管：
#   以前靠手敲 pyinstaller，参数散在 yml 里，敲漏一项就少一层，而少的那一项
#   （随包 config.json）在症状上表现为「别人电脑上全是精简版」，本机却一切正常。
#   本脚本与 .github/workflows/build-b2bcollect-mac.yml **同口径**：同样一份
#   hidden-import/datas 清单，跑完必须产物自己通过 --acl-doctor 与 --ui-smoke，
#   最后还要真双击一次、由产物自己记账证明界面交出去了（判据不看屏幕，见第 6 步）。
#
# 步骤（编号与正文一一对应；标「门」的那几步任何一道不过就没有包，不会有半成品）：
#   0) 打包清单硬门【门】：三处入口的 --add-data/--hidden-import 与 site_adapters 的
#      清单逐项对拍，缺一项就中止（少一个文件不会报错、只会少一个平台）；
#   1) 随包凭证【门】：用运行时同一条解析链现写 b2bcollect_build/config.json，
#      不在位就中止（防止把「没带凭证」的包发出去 = 别人电脑上全是精简版）；
#   2) chromedriver：打进包根（chrome_driver.candidates 的 frozen 首选位）——
#      这一步找不到只警告不拦停，理由见那一段；
#   3) 构建：PyInstaller 一次成型；
#   4) 产物自检门禁【门】：跑的是 .app 里的可执行文件、不是源码，--acl-doctor 真连
#      权限服务并验采集通道完整性，不通就报错退出；
#   5) 只读包窗口冒烟【门】：把 .app 拷一份改成只读再跑 --ui-smoke，复现 Gatekeeper
#      转置（App Translocation）那一类「双击没界面」的现场——doctor 刻意不碰 Qt，
#      这一段不补上就永远是「CI 全绿、用户开不了」；
#   6) 真双击 + 启动台账【门】：走 open（与用户双击同源）起一次，只认产物自己写进
#      launch_history.log 的那一行 shown。数窗口在锁屏/远程会话下对所有进程都报 0，
#      它只能当附加信息、不能当交付判据（离线套件 W 节把这一道门的每条判据都跑了正反）。
#
# 用法：bash debug/build_b2bcollect_mac_local.sh
#   可选环境变量：PY=python 解释器（默认 .venv/bin/python）
#                 B2B_LOCAL_KEEP_OLD=1 保留旧 dist/B2bCollectTool.app（默认覆盖）
set -Eeuo pipefail
# ── ERR 陷阱：把「脚本静默结束」变成「带行号的一句话」（2026-11-13 本机实发）──────
# 上一次跑这个脚本，第 5 步印完 ✅ 之后整个脚本一声不响地结束了：第 6 道门（真双击验窗）
# 压根没执行，日志里连一行报错都没有。根因是 set -e 撞上一句**正常情况下也会返回非零**的命令：
#   BEFORE_PIDS=$(pgrep -x B2bCollectTool 2>/dev/null | tr '\n' ' ')
# 机器上没有旧实例时（干净机器 = 最常见的那种）pgrep 返回 1，pipefail 让整条管道非零，
# 裸赋值又把这个非零状态原样交给 set -e，于是当场退出、零解释。
# 更难看的是它此前一直「绿」：只因那台机器上恰好留着一枚我自己起的旧实例，pgrep 才返回 0
# ——判据依赖机器上有没有碰巧的残留进程，就是拿运气当判据。
# `|| true` 修的是那一句；这一条陷阱修的是**这一整类**：以后再有任何一句命令把脚本带走，
# 至少留下一行「第几行、退出码多少」，而不是让交付链最后一道门安静地不存在。
# 注：`exit 1` 不触发 ERR（实测），所以各处主动止损的报错照旧只打自己那句；
#     需要「先跑完再看退出码」的地方一律写成 `cmd || RC=$?`——它在 || 列表里，
#     set -e 与 ERR 陷阱都不插手，不再需要 set +e 那种整段关掉保护的做法。
trap 'rc=$?; printf "❌ 第 %s 行的命令非零退出（rc=%s）：set -e 会在这里结束脚本，后面的门不会再跑\n" "$LINENO" "$rc" >&2' ERR

cd "$(dirname "$0")/.." || exit 1
ROOT="$PWD"
PY=${PY:-.venv/bin/python}
# 解释器一律收敛成绝对路径：第 3 步要 cd 进 b2bcollect_build，那时相对路径
# .venv/bin/python 已经不在位——症状是「前三道门全绿、构建那一步 No such file or
# directory」，门都过了却没出包，比一开始就报错更难归因（2026-11-12 本机实踩）。
case "$PY" in
  /*) : ;;
  */*) PY="$ROOT/$PY" ;;
  *) PY=$(command -v "$PY" || true) ;;
esac
[ -n "${PY:-}" ] && [ -x "$PY" ] || PY=$(command -v python3 || true)
if [ -z "${PY:-}" ] || [ ! -x "$PY" ]; then
  echo "❌ 找不到可用的 Python 解释器（可用 PY=/path/to/python 指定）"
  exit 1
fi
echo "· 解释器：$PY"

# 有边界地跑一个子进程：$1=秒上限，其余是命令；超时按 124 收（与 GNU timeout 同口径）。
# 为什么连「取个路径」这种秒回的事也要包一层：本机已实证过两次，被堵死的 exec 评估队列
# 能让任何一次新 exec 永远排不上队，那时整条交付链跟着挂住、日志里一行结论都留不下
# （2026-11-13 凌晨那两个 0 字节的取日志文件就是这么来的）。
# 放在文件顶部而不是第 6 步原地：第 4 步取「自检报告落点」也要用它——同一个「有边界地
# 跑外部程序」的规矩只写一份，两处各抄一遍迟早会抄出两种超时语义。
_run_bounded() {
  local _limit=$1 _pid="" _i=0
  shift
  "$@" & _pid=$!
  while [ $_i -lt $((_limit * 2)) ]; do
    _i=$((_i + 1))
    sleep 0.5
    if ! kill -0 "$_pid" 2>/dev/null; then
      wait "$_pid" && return 0
      return $?
    fi
  done
  kill -TERM "$_pid" 2>/dev/null || true
  sleep 1
  kill -KILL "$_pid" 2>/dev/null || true
  wait "$_pid" 2>/dev/null || true
  return 124
}

# ── 0) 打包清单硬门：该有的模块与数据在清单里一项不缺，才许开建 ──
#     判据来自 site_adapters.EXTERNAL_MODULES（模块的使用者自己声明需要什么），
#     本脚本、两个 workflow 三处入口逐个对拍。这一道门是 2026-11-12 换来的教训：
#     风鸟/爱企查的 .py 没进包，构建照样成功、自检照样绿，用户那边只是「② 公司名单
#     少了两个平台」——少一个文件不报错、只少功能，必须在开建前拦住。
# < /dev/null 由硬门第 20 条 20f 要求：本机后台跑这份脚本时，启动它的终端一被回收，
# fd 0 就成了已关闭的描述符，python 在跑第一行之前就崩 init_sys_streams/Bad file
# descriptor——「门没跑起来」会被读成「门判它不合格」（取制品那趟实发的就是这个形状）。
"$PY" .github/scripts/check_packaging_manifest.py < /dev/null || exit 1

# ── 1) 随包凭证：与 CI 的 inject_build_config.py 同一个脚本、同一份键名契约 ──
"$PY" .github/scripts/inject_build_config.py --from-local < /dev/null

# ── 2) chromedriver：打进包根（chrome_driver.candidates 的 frozen 首选位）──
#     找不到只警告不拦停：驱动缺失运行时会用 Selenium Manager 现下，症状是「首次
#     启动慢一点」，与「没带凭证 = 全员精简版」不是一个量级，不该混成同一道门。
DRIVER=""
for c in "$ROOT/mac_driver/chromedriver" \
         "$ROOT/riskbird_build/chromedriver-mac-x64/chromedriver"; do
  if [ -f "$c" ]; then DRIVER="$c"; break; fi
done
DRIVER_ARG=()
if [ -n "$DRIVER" ]; then
  echo "· 内置驱动：$DRIVER"
  DRIVER_ARG=(--add-data "$DRIVER:.")
else
  echo "⚠️  没找到本机 chromedriver（mac_driver/ 或 riskbird_build/chromedriver-mac-x64/），"
  echo "    本次包不含内置驱动，首次使用浏览器功能时会自动下载。"
fi

# ── 3) 构建 ──
cd b2bcollect_build
# 这里再验一次：上面已经绝对化过，这一句是给「以后有人改回相对路径」留的哨兵——
# cd 之后解释器不可用会在这里当场停下，而不是拖到 PyInstaller 那行报一句难读的错。
[ -x "$PY" ] || { echo "❌ cd 进 b2bcollect_build 后解释器不可用：$PY"; exit 1; }
if [ ! -f config.json ]; then
  echo "❌ b2bcollect_build/config.json 不存在（第 1 步没成功），不许出包"
  exit 1
fi

EXE_ARGS=()
if [ "${B2B_LOCAL_KEEP_OLD:-}" != "1" ]; then
  EXE_ARGS=(--noconfirm)
fi

# 参数与 build-b2bcollect-mac.yml 逐条对齐（pip 名≠import 名的两个都写）。
# 差异只有一处：CI 用 arm64 runner 下载 arm64 驱动，本机用本机架构的驱动。
"$PY" -m PyInstaller --clean "${EXE_ARGS[@]+"${EXE_ARGS[@]}"}" --windowed \
  --name B2bCollectTool \
  --collect-all selenium --collect-all PyQt5 --collect-all lxml --collect-all bs4 \
  --collect-submodules PIL \
  --hidden-import requests --hidden-import websocket --hidden-import websocket_client \
  --hidden-import openpyxl --hidden-import cdp_base --hidden-import shared_chrome \
  --hidden-import parallel_session --hidden-import mic_crawler --hidden-import b2b168_crawler \
  --hidden-import qihuo11467_crawler --hidden-import aipage_crawler --hidden-import maimaib2b_crawler \
  --hidden-import gi_crawler --hidden-import makepolo_crawler --hidden-import mk_phone_ocr \
  --hidden-import province_filter --hidden-import site_adapters --hidden-import pending_companies \
  --hidden-import keyword_expander --hidden-import deepseek_expander --hidden-import gongchang_crawler \
  --hidden-import customer_library_cleaner --hidden-import enhanced_cleaner \
  --hidden-import enf_clean.normalize --hidden-import enf_clean.phone \
  --hidden-import phonenumbers --hidden-import pandas --hidden-import xlrd \
  --hidden-import local_scan --hidden-import yzd_client --hidden-import task_registry \
  --hidden-import oss_pool_client --hidden-import data_pool --hidden-import pool_matcher \
  --hidden-import pool_dialogs --hidden-import access_guard --hidden-import edition \
  --hidden-import acl_admin_dialog --hidden-import feedback_store --hidden-import search_suggest \
  --hidden-import recommend_engine --hidden-import dedup_engine --hidden-import mapping_recommender \
  --hidden-import audit_logger --hidden-import search_optimizer --hidden-import pool_merge \
  --hidden-import ingest_cleaning --hidden-import pool_store --hidden-import tool_config \
  --hidden-import website_contact \
  --add-data "config.json:." --add-data "mk_phone_font.json:." \
  --add-data "../enf_clean:enf_clean" \
  --add-data "../ali1688_build/ali1688_crawler.py:ali1688_build" \
  --add-data "../riskbird_build/qizhidao_crawler.py:riskbird_build" \
  --add-data "../riskbird_build/block_guard.py:riskbird_build" \
  --add-data "../riskbird_build/chrome_driver.py:riskbird_build" \
  --add-data "../riskbird_build/riskbird_crawler_v2.py:riskbird_build" \
  --add-data "../riskbird_build/aiqicha_crawler_v2.py:riskbird_build" \
  --add-data "../riskbird_build/runtime_paths.py:riskbird_build" \
  "${DRIVER_ARG[@]+"${DRIVER_ARG[@]}"}" \
  b2bcollect_gui.py < /dev/null

# ── 4) 产物自检门禁：跑的是 .app 里的可执行文件，不是源码 ──
#     只有它才同时具备「打包态 frozen 判定」与「随包 config.json」这两个真实条件。
APP="dist/B2bCollectTool.app/Contents/MacOS/B2bCollectTool"
if [ ! -x "$APP" ]; then
  echo "❌ 产物没生成到位：$APP"
  exit 1
fi
echo "· 产物自检：$APP --acl-doctor"
# 落点先问产物自己（--state-dir 只印一行绝对路径），后面第 4、5、6 步都用它：
# 2026-11-13 受保护目录那条改动之后，本机这份 .app 建在 ~/Desktop 下，候选链按语义把
# 受保护目录整条排除，自检报告因此不再落在 dist/ 里——原来那句 `find dist` 找不到任何东西，
# 门禁仍然对，但「把报告原文发给发包方」这条排障用途静默失效（脚本只会说一句没找到）。
STATE_OUT="dist/.state_dir.out"
_rc=0
_run_bounded 20 "$APP" --state-dir > "$STATE_OUT" 2>&1 || _rc=$?
if [ "$_rc" = 124 ]; then
  echo "❌ 产物取台账落点 20 秒没返回（--state-dir）：连一个路径都给不出，这份包别外发"
  exit 1
fi
if [ "$_rc" -ne 0 ]; then
  echo "❌ 产物不认 --state-dir（退出码 ${_rc}）：$(cat "$STATE_OUT" 2>/dev/null || true)"
  exit 1
fi
# 格式依据：这个开关只 print 一行绝对路径（_state_dir_entry 里唯一那次 print），取末行
# 既容得下它前面可能多出来的告警行，也不会因为话术改动而拿错字段。
STATE_DIR=$(tail -1 "$STATE_OUT")
rm -f "$STATE_OUT"
if [ -z "$STATE_DIR" ]; then
  echo "❌ --state-dir 给了空路径：台账与自检报告的落点问不出来，第 4、6 两步就没有判据"
  exit 1
fi
echo "· 状态目录（产物自己报的）：$STATE_DIR"
RC=0
"$APP" --acl-doctor || RC=$?
# 报告文件按「刚被写过」来 find，落点用上面问来的那一份，绝不写死 dist/b2bcollect_state：
# 口径或布局一变，写死的那条路径就静默 cat 不到任何东西——门禁还是对的，但远程排障
# 时少了一份可以直接发给发包方的原文。Windows 那一步早就是查找而不是写死，两边同规矩。
LOG=$(find "$STATE_DIR" -name acl_doctor.txt -mmin -10 2>/dev/null | head -1 || true)
if [ -n "${LOG:-}" ]; then
  echo "· 自检报告原文：$LOG"
  cat "$LOG"
else
  echo "· （没找到本次的 acl_doctor.txt，以上面的标准输出为准）"
fi
if [ "$RC" -ne 0 ]; then
  echo "❌ 产物自检未通过（退出码 ${RC}，报告见上）：这个包发出去只会是精简版，别外发"
  exit "$RC"
fi

# ── 5) 只读包窗口冒烟：把 Gatekeeper 转置（App Translocation）的现场在本机复现一遍 ──
#     与云端 Mac workflow 里那一步同一条判据、同一段脚本口径，为什么本机还要再来一次：
#     外部机器「双击没界面」时，第一手排查不该等到云端跑一轮（十几分钟 + 只能看日志），
#     在这里 30 秒就能复现。三条判据各挡一种「绿得没意义」：
#       1) 退出码 0                  —— 窗口真建出来了（Mac 那份崩溃日志停在建通道那一步）；
#       2) 临时 HOME 里没有崩溃日志   —— 挡住「异常被上层吞掉、界面勉强开出来」的半坏；
#       3) 副本树文件数没多           —— 挡住「没被转置的机器上写进应用包」那种不报错的写法。
#     最后那句是给仿真自己立的判据，判据必须是**真写入**：2026-11-12 本机与云端同时红在
#     「这份副本没被改成只读」，而它其实已经只读——macOS 上文件属主改时间戳（touch 走的
#     utimensat 只要拥有者一致就允许）不需要写权限，拿 touch 当探针等于用一个永远成功的
#     检查去证明一件事成立。改成往里追加一个字节：能写就是仿真没做成，此时这份副本已经
#     被污染，但下一步就是退出，不影响任何交付物。
echo "· 只读包窗口冒烟（App Translocation 现场）"
STAGE=$(mktemp -d)
# 退出前先把写权限还回来再删：只读树里 rm 连自己父目录的子项都删不掉（解链要目录的写权限）
trap 'chmod -R u+w "$STAGE" 2>/dev/null || true; rm -rf "$STAGE"' EXIT
cp -R dist/B2bCollectTool.app "$STAGE/"
SAPP="$STAGE/B2bCollectTool.app"
SBIN="$SAPP/Contents/MacOS/B2bCollectTool"
# ACL 与扩展属性会盖在模式位之上（PyInstaller 产物的每个文件都带 com.apple.provenance，
# cp 还可能带上继承的 ACL），只 chmod 就可能「看着只读、实际可写」。这里先把两者清掉，
# 再收模式位；清不动不算失败（本来就没有），真写探针会替我们把结论收口。
chmod -R -N "$SAPP" 2>/dev/null || true
xattr -cr "$SAPP" 2>/dev/null || true
chmod -R a-w "$SAPP"
[ -x "$SBIN" ] || { echo "❌ 只读副本里没有可执行文件：$SBIN"; exit 1; }
READONLY_PROBE="$SAPP/Contents/Info.plist"
if (printf 'x' >> "$READONLY_PROBE") 2>/dev/null; then
  echo "❌ 这份副本真的还能写（${READONLY_PROBE}）：仿真没做成，这一步绿了也不代表产物没问题"
  exit 1
fi
# 数文件数是判据 3)「包内一字节没多」的基准：基准取不到就停下并说清为什么，别把
# 「find 失败」留给 set -e 当成一次没有解释的退出（同一类事故见文件开头的 ERR 陷阱）。
BEFORE=$(find "$SAPP" | wc -l) || { echo "❌ 数不到只读副本的文件数（${SAPP}）：判据没有基准，不放行"; exit 1; }
mkdir -p "$STAGE/home"
RC=0
HOME="$STAGE/home" QT_QPA_PLATFORM=offscreen "$SBIN" --ui-smoke || RC=$?
STRAY=$(find "$STAGE/home" -name "*.log" 2>/dev/null || true)
AFTER=$(find "$SAPP" | wc -l) || { echo "❌ 跑完之后数不到副本文件数（${SAPP}）：比对没有基准，不放行"; exit 1; }
if [ -n "${STRAY:-}" ]; then
  while read -r f; do
    [ -n "$f" ] || continue
    echo "—— 崩溃日志 $f ——"
    cat "$f"
  done <<<"$STRAY"
fi
if [ "$RC" -ne 0 ]; then
  echo "❌ 只读包上窗口没建出来（退出码 ${RC}）：用户双击打开就是这个样子，别外发"
  exit "$RC"
fi
if [ -n "${STRAY:-}" ]; then
  echo "❌ 窗口建成了却留下崩溃日志：启动路径上有异常被吞掉了，两者不能同时成立"
  exit 1
fi
if [ "$BEFORE" != "$AFTER" ]; then
  echo "❌ 只读副本里多出了文件（${BEFORE} → ${AFTER}）：启动路径上有代码往应用包里写东西"
  exit 1
fi
echo "✅ 只读包窗口冒烟通过：界面建得出、崩溃日志一份都没有、包内一字节没多"

# ── 6) 最后一步：真按用户那一次双击验一遍（走 launchd，不走 shell 直接 exec）────
# 上面所有环节都是 QT_QPA_PLATFORM=offscreen 的仿真，它能证明「代码路径不写包」，
# 却证明不了「这台机器今天给用户摆得出界面」。这一步的判据换过两次，两次都撞在
# 「观测手段自己不可靠」上（2026-11-12、11-13 本机实测）：
#   · 第一坑：连着两次「0 窗口、CPU 冻结」看着像产物挂了，真原因是我自己攒下的那批
#     僵死子进程把 syspolicyd 的 exec 评估队列堵死——**观测手段污染了被测对象**；
#   · 第二坑：锁屏与远程会话下 System Events 对**每一个**进程都报 0 窗口（同一时刻连
#     Qoder、Safari 也数不出），产物明明活着、窗口明明在，门禁却只能判红。
# 结论：窗口有没有交出去，只有进程自己说得出真话。判据因此换成产物写的启动台账
# （b2bcollect_gui._launch_record，行格式 时间|pid|阶段|细节）；数窗口降级成附加信息：
# 数得出就一并印出来给人核对，数不出不判红。
# 五条纪律：
#   ① 用 open（launchd 那条路），与用户双击同源；shell 直接 exec 走的是另一套评估。
#   ② 台账落点问产物自己（--state-dir），绝不在 shell 里重抄候选链——重抄就是第二份
#      真源，改了 tool_config 忘了改这里，门禁会去读一个不存在的路径再判一场莫名其妙的红。
#      文件名与阶段名这两个字面量由离线套件 W 节对着 Python 常量逐字对拍。
#   ③ 只认**本次新出现**的 shown 记录：open 对已在跑的旧实例只是把它唤到前台，那份字节
#      不是刚打出来的产物，把它当通过就是「为了绿灯而少验」（旧写法正是这么写的）。
#   ④ 有边界地等（macOS 没有 timeout 命令，用轮次 + sleep 自己数），子进程一律带超时。
#   ⑤ 收尾把这个实例连同它带出来的子进程一起收干净，绝不留孤儿：今天这批孤儿就是把
#      机器堵死的那批孤儿。
APP_LIVE="dist/B2bCollectTool.app"
LEDGER_NAME="launch_history.log"      # 与 b2bcollect_gui.LAUNCH_LOG_NAME 同一份名字
LEDGER_STAGE="shown"                  # 与 b2bcollect_gui.LAUNCH_STAGE_SHOWN 同一个阶段
# 「没有旧实例」是**最常见**的情形（干净机器上就该一个都没有），而 pgrep 在这种情况下返回 1：
# 裸赋值 + pipefail + set -e 会让整条脚本在这里静默结束，最后一道门根本不跑
# （2026-11-13 本机实发，症状是「第 5 步 ✅ 之后什么也没有」）。这里必须 `|| true`。
BEFORE_PIDS=$(pgrep -x B2bCollectTool 2>/dev/null | tr '\n' ' ' || true)
# 时间戳落在 dist/（工作区内，沙箱允许写；不在 .app 里面，免得自己污染自己写的探针）。
STAMP_FILE="dist/.open_gate.stamp"
: > "$STAMP_FILE"

# 台账落点：第 4 步已经向产物问过一次的 STATE_DIR（纪律②：全脚本只问一次，
# 问两次就会有「两步各自拿到不同落点」这种查不出来的分裂）。
LEDGER="$STATE_DIR/$LEDGER_NAME"
echo "· 启动台账：$LEDGER"

# 台账里「已经报过 shown」的 pid 集合（本轮 open 之前先拍一份，之后只认新出现的）
_shown_pids() {
  [ -f "$LEDGER" ] || return 0
  # 格式依据：台账行由 _launch_record 写死成「时间|pid|阶段|细节」，四个字段以 | 分隔。
  # 这里按第 3 列比阶段、取第 2 列当 pid，不数空格也不按行号——改了行格式必须同时改
  # 这里（离线套件 W 节把两边逐字对拍，防止「门禁读的是想象中的字段」）。
  awk -F'|' -v s="$LEDGER_STAGE" '$3 == s { print $2 }' "$LEDGER" | tr '\n' ' '
}
BEFORE_SHOWN=$(_shown_pids)
/usr/bin/open "$APP_LIVE" || { echo "❌ open 都没发出去（这一步连双击都模拟不了）"; exit 1; }
# 台账里找「本次新出现的 shown pid」：快照之前就有的一律不算，报过 shown 却已经退出的
# 也不算（记完就崩的进程，用户看到的仍然是没界面）。
_find_shown() {
  for _p in $(_shown_pids); do
    case " ${BEFORE_SHOWN:-} " in *" $_p "*) continue ;; esac
    kill -0 "$_p" 2>/dev/null || continue
    LIVE_PID=$_p
    return 0
  done
  return 1
}
LIVE_PID=""
_i=0
while [ $_i -lt 10 ]; do
  _i=$((_i + 1))
  sleep 2
  _find_shown || true
  [ -n "$LIVE_PID" ] && break
done
if [ -z "$LIVE_PID" ] && [ -n "${BEFORE_PIDS:-}" ]; then
  # 桌面上已经有一份在跑的旧实例：`open` 只把它唤到前台，新字节压根起不来。这一步要验的
  # 正是刚打出来的这个包，所以换 `open -n` 强制起一份；验完只收自己起的这一个，绝不动
  # 旧实例（别人可能正在采集）。两种起法都在台账里留下自己的 pid，判据不受影响。
  echo "· 已有实例在跑（pid ${BEFORE_PIDS}）：open 唤不到新字节，改用 open -n 单独起一份刚打出来的产物"
  /usr/bin/open -n "$APP_LIVE" || { echo "❌ open -n 都没发出去"; exit 1; }
  _i=0
  while [ $_i -lt 40 ]; do
    _i=$((_i + 1))
    sleep 2
    _find_shown || true
    [ -n "$LIVE_PID" ] && break
  done
fi
# 收尾：只收本次新起的实例和它带出来的子进程，绝不动交付前就在跑的那些（别人可能正在用）。
# 这一步**成败都要做**：旧写法只在通过那条路上收，失败那条直接 exit——于是一次判红就在
# 机器上留下一枚没人知道的实例（本轮实测留下的那枚一直卡在 open() 里，四分钟不放界面）。
_harvest_live() {
  for _p in $(pgrep -x B2bCollectTool 2>/dev/null || true); do
    case " ${BEFORE_PIDS:-} " in *" $_p "*) continue ;; esac
    for _c in $(pgrep -P "$_p" 2>/dev/null || true); do kill -TERM "$_c" 2>/dev/null || true; done
    kill -TERM "$_p" 2>/dev/null || true
  done
  sleep 3
  for _p in $(pgrep -x B2bCollectTool 2>/dev/null || true); do
    case " ${BEFORE_PIDS:-} " in *" $_p "*) continue ;; esac
    for _c in $(pgrep -P "$_p" 2>/dev/null || true); do kill -KILL "$_c" 2>/dev/null || true; done
    kill -KILL "$_p" 2>/dev/null || true
  done
}

if [ -n "$LIVE_PID" ]; then
  echo "✅ 真双击路径验过：产物自己记到 ${LEDGER_STAGE}（pid ${LIVE_PID}，第 ${_i} 次轮询）"
  # 数窗口这一段现在只是附加信息：数得出最好（人眼能核对），数不出说明的是这台机器的
  # 会话状态（锁屏、远程），不是产物的状态——拿它当判据就是让交付结论跟着屏幕走。
  _w=$(/usr/bin/osascript -e "tell application \"System Events\" to tell (first process whose unix id is $LIVE_PID) to get count of windows" 2>/dev/null || true)
  case "$_w" in
    '' | *[!0-9]*) echo "   （窗口数这次数不出：锁屏或远程会话下 System Events 对所有进程都报空，不作判据）" ;;
    *) echo "   窗口数：${_w} 个" ;;
  esac
fi
if [ -z "$LIVE_PID" ]; then
  # 数不出窗口不再等于失败（那是这台机器的会话状态），但**台账里没有本次的 shown**
  # 一定是失败：台账是产物自己写的，它说没走到那一步，就是没走到。成因分开说，
  # 因为修法完全相反：
  echo "❌ 轮询 ${_i} 次（每次 2 秒）都没等到本次的 ${LEDGER_STAGE} 记录。判据不看屏幕，只看这份台账——"
  echo "   台账（${LEDGER}）最近 6 条："
  if [ -f "$LEDGER" ]; then
    tail -6 "$LEDGER" | sed 's/^/     /'
  else
    echo "     （文件不存在：这台机器上一次台账都没落过盘，产物连第一步都没走到）"
  fi
  # 产物自己被卡住时写下的那一份栈：比这里任何推测都直接，读到哪一行就是卡在哪一行
  STALL_FILE="$(dirname "$LEDGER")/startup_stall.txt"
  if [ -f "$STALL_FILE" ]; then
    echo "   本次启动的卡点栈（产物自己写的，${STALL_FILE}）前 24 行："
    head -24 "$STALL_FILE" | sed 's/^/     /'
  fi
  echo "   ①有 begin 没 shown：走到建界面就断了，堆栈在崩溃日志里（_fatal_log_dirs 那两处）；"
  echo "   ②连 begin 都没有，两种现实长得一样，先看有没有那份崩溃日志："
  echo "     · 有日志＝崩在 import（台账这一段还没轮到它写），按日志里的栈修；"
  echo "     · 没日志＝本次根本没有新进程起来（上面有旧实例时脚本已经自己换过 open -n"
  echo "       强制起新字节，仍然起不来就是产物或系统这一侧的问题；旧写法会把「唤到前台"
  echo "       的旧实例」当通过，那是「为了绿灯而少验」：验的是上一次那个包）；"
  echo "   ③进程在但状态是 U（不可中断等待）：这台机器的 exec 评估/磁盘被堵住，不是包坏了；"
  echo "   ④台账里有 stalled：界面没交出去不是因为报错，是因为**没人回答系统弹窗**。"
  echo "      macOS 把桌面/文稿/下载里的文件访问扣在内核里等用户点「允许」，而双击起的"
  echo "      进程由 launchd 拉起（与从终端跑是同一条命令、却不是同一个 TCC 主体）——"
  echo "      这正是「终端里全绿、用户双击没界面」那一族。核对哪一次访问被扣住："
  echo "        log show --last 10m --predicate 'eventMessage CONTAINS \"System Policy: B2bCollectTool\"'"
  echo "      修法不是在这里加等待，而是**启动路径不碰受保护目录**（机器状态一律落状态目录，"
  echo "      老位置的读一律有界；判据见离线套件 X 节）。"
  pgrep -ax B2bCollectTool 2>/dev/null | head -5 || true
  # 格式依据（本机 2026-11-13 实测）：`ps -axo pid,stat,command` 的第二列就是 STAT，僵死
  # 进程在那里印 U（那次把这台机器拖死的一整排进程就是 U 态）。老写法是 `grep -c "U"`，
  # 数的是「整行里出现过一个大写 U」——路径里随便一个 U 都算，念出来的数字毫无意义。
  # 所以按字段取，并且只报条数与几个 pid：这一步是给人分成因的，不是判据。
  /bin/ps -axo pid,stat,command 2>/dev/null | awk '$2 ~ /^U/ { n++; if (n <= 3) print "     U 态：" $1 " " $3 }
    END { print "   STAT 首字母为 U（不可中断等待）的进程：" n + 0 " 个" }' || true
  _harvest_live
  exit 1
fi
GREW=$(find "$APP_LIVE" -newer "$STAMP_FILE" 2>/dev/null | head -3 || true)
if [ -n "${GREW:-}" ]; then
  echo "❌ 真跑起来的那一会儿往应用包里写了东西（用户机器上这就是 App Translocation"
  echo "   只读卷里的 OSError 30）：$(printf '%s\n' "$GREW")"
  _harvest_live
  exit 1
fi
echo "✅ 包内一字节没多（${APP_LIVE}）：台账落在包外的 ${STATE_DIR}"
# 收尾：只收这次新起的实例和它的子进程，绝不动交付前就在跑的那些（别人可能正在用）。
_harvest_live
echo "✅ 验证用的实例已收干净（不留孤儿进程——本轮把机器堵死的就是孤儿）"
echo "✅ 打包完成并通过产物自检：$(pwd)/dist/B2bCollectTool.app"
