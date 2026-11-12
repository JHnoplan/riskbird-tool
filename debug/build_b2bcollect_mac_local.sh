#!/usr/bin/env bash
# b2bcollect 工具的 macOS 本地打包（一键、带产物自检门禁）。
#
# 为什么要这么个脚本（2026-11 外部机器全员精简版的教训）：
#   云端 workflow 是主打包路径，但「本机现在就要一个能跑的包」这件事一直没人管：
#   以前靠手敲 pyinstaller，参数散在 yml 里，敲漏一项就少一层，而少的那一项
#   （随包 config.json）在症状上表现为「别人电脑上全是精简版」，本机却一切正常。
#   本脚本与 .github/workflows/build-b2bcollect-mac.yml **同口径**：同样五道门，
#   同样一份 hidden-import/datas 清单，跑完必须产物自己通过 --acl-doctor 与 --ui-smoke。
#
# 五道门（任何一道不过就没有包，不会有半成品）：
#   0) 打包清单硬门：三处入口的 --add-data/--hidden-import 与 site_adapters 的清单
#      逐项对拍，缺一项就中止（少一个文件不会报错、只会少一个平台）；
#   1) 随包凭证：用运行时同一条解析链现写 b2bcollect_build/config.json；
#   2) 构建前硬门：config.json 不在位就中止（防止把「没带凭证」的包发出去）；
#   3) 构建后自检：拿 **产物自己** 跑 --acl-doctor 真连权限服务并验采集通道完整性，
#      不通就报错退出；
#   4) 只读包窗口冒烟：把 .app 拷一份改成只读再跑 --ui-smoke，复现 Gatekeeper 转置
#      （App Translocation）那一类「双击没界面」的现场——doctor 刻意不碰 Qt，这一段
#      不补上就永远是「CI 全绿、用户开不了」。
#
# 用法：bash debug/build_b2bcollect_mac_local.sh
#   可选环境变量：PY=python 解释器（默认 .venv/bin/python）
#                 B2B_LOCAL_KEEP_OLD=1 保留旧 dist/B2bCollectTool.app（默认覆盖）
set -euo pipefail

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

# ── 0) 打包清单硬门：该有的模块与数据在清单里一项不缺，才许开建 ──
#     判据来自 site_adapters.EXTERNAL_MODULES（模块的使用者自己声明需要什么），
#     本脚本、两个 workflow 三处入口逐个对拍。这一道门是 2026-11-12 换来的教训：
#     风鸟/爱企查的 .py 没进包，构建照样成功、自检照样绿，用户那边只是「② 公司名单
#     少了两个平台」——少一个文件不报错、只少功能，必须在开建前拦住。
"$PY" .github/scripts/check_packaging_manifest.py || exit 1

# ── 1) 随包凭证：与 CI 的 inject_build_config.py 同一个脚本、同一份键名契约 ──
"$PY" .github/scripts/inject_build_config.py --from-local

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
  b2bcollect_gui.py

# ── 4) 产物自检门禁：跑的是 .app 里的可执行文件，不是源码 ──
#     只有它才同时具备「打包态 frozen 判定」与「随包 config.json」这两个真实条件。
APP="dist/B2bCollectTool.app/Contents/MacOS/B2bCollectTool"
if [ ! -x "$APP" ]; then
  echo "❌ 产物没生成到位：$APP"
  exit 1
fi
echo "· 产物自检：$APP --acl-doctor"
set +e
"$APP" --acl-doctor
RC=$?
set -e
# 报告文件按「刚被写过」来find，而不是写死 dist/b2bcollect_state：自检报告的落点
# 由 tool_config.state_dir 的候选链算出来（环境变量、包旁、exe 同级、用户目录…），
# 口径或布局一变，写死的那条路径就静默 cat 不到任何东西——门禁还是对的，但远程排障
# 时少了一份可以直接发给发包方的原文。Windows 那一步早就是查找而不是写死，两边同规矩。
LOG=$(find dist -name acl_doctor.txt -mmin -10 2>/dev/null | head -1)
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
#     最后那句 touch 是给仿真自己立的判据：chmod 没生效时这一步会绿得毫无意义。
echo "· 只读包窗口冒烟（App Translocation 现场）"
STAGE=$(mktemp -d)
# 退出前先把写权限还回来再删：只读树里 rm 连自己父目录的子项都删不掉（解链要目录的写权限）
trap 'chmod -R u+w "$STAGE" 2>/dev/null || true; rm -rf "$STAGE"' EXIT
cp -R dist/B2bCollectTool.app "$STAGE/"
chmod -R a-w "$STAGE/B2bCollectTool.app"
SAPP="$STAGE/B2bCollectTool.app"
SBIN="$SAPP/Contents/MacOS/B2bCollectTool"
[ -x "$SBIN" ] || { echo "❌ 只读副本里没有可执行文件：$SBIN"; exit 1; }
if touch "$SAPP/Contents/Info.plist" 2>/dev/null; then
  echo "❌ 这份副本没被改成只读（Info.plist 仍可写）：仿真失效，这一步绿了也不代表产物没问题"
  exit 1
fi
BEFORE=$(find "$SAPP" | wc -l)
mkdir -p "$STAGE/home"
set +e
HOME="$STAGE/home" QT_QPA_PLATFORM=offscreen "$SBIN" --ui-smoke
RC=$?
set -e
STRAY=$(find "$STAGE/home" -name "*.log" 2>/dev/null || true)
AFTER=$(find "$SAPP" | wc -l)
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
echo "✅ 打包完成并通过产物自检：$(pwd)/dist/B2bCollectTool.app"
