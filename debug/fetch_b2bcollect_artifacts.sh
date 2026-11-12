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
# 目标 commit 默认就是 HEAD。只有在「推送不是走常规 git push」时才需要显式传 sha：
# 本机到 github.com:443 的路由偶发不可达（api.github.com 一直通），此时提交通过
# Git Data API 快进上去，云端那个 sha 与本地 HEAD 号不同、但 tree 完全一致。
# 允许显式指定是为了不把这种情况变成「脚本本身 unusable」；查不到 run 仍然当场停，
# 「绝不拿旧产物交差」这条不会因为多了一个入参就松掉。
SHA=${1:-}
[ -n "$SHA" ] || SHA=$(git rev-parse HEAD)
case "$SHA" in
  *[!0-9a-fA-F]*|'') echo "SHA 不合法（要 40 位十六进制，或留空表示用 HEAD）：$SHA"; exit 2 ;;
esac
[ ${#SHA} -eq 40 ] || { echo "SHA 长度 ${#SHA} 不是 40 位：$SHA"; exit 2; }
echo "· 目标 commit：${SHA:0:7}$([ "$SHA" = "$(git rev-parse HEAD)" ] || echo '（显式指定，非本地 HEAD）')"

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

mkdir -p /tmp/b2b_dl && rm -rf /tmp/b2b_dl/*
FAIL=0
for kv in "${IDS[@]}"; do
  key=${kv%%:*}; id=${kv##*:}
  AID=$(curl -s -H "Authorization: Bearer $TOK" "$API/actions/runs/$id/artifacts" \
    | python3 -c 'import sys,json; print(json.load(sys.stdin)["artifacts"][0]["id"])')
  curl -sL --retry 8 --retry-all-errors -H "Authorization: Bearer $TOK" -o "/tmp/b2b_dl/$key.zip" \
    "$API/actions/artifacts/$AID/zip"
  mkdir -p "/tmp/b2b_dl/$key" && unzip -oq "/tmp/b2b_dl/$key.zip" -d "/tmp/b2b_dl/$key"
  # 制品是「zip 里再套一层真实产物包」，两个平台都要解到内层才能验：
  #   · Mac：外层 Actions zip 里是 ditto 打的 B2bCollectTool_Mac_arm64.zip，.app 在里面；
  #   · Windows：外层 Actions zip 里是 B2bCollectToolSetup.exe（安装包）+
  #     B2bCollectTool_Win_portable.zip（绿色版），**布局判据在绿色版那个 zip 里**，
  #     所以解到内层、把 B2bCollectTool/ 归位成 dist/B2bCollectTool 再交给校验脚本。
  # 解不开必须当场停：静默跳过校验等于把「随包凭证」这道门拆了，而它正是外部机器全员
  # 精简版那次事故的守门人。
  if [ "$key" = "mac" ]; then
    INNER=$(find "/tmp/b2b_dl/$key" -maxdepth 2 -name "*.zip" -type f | head -1)
    [ -n "$INNER" ] || { echo "[$key] ❌ 制品里没有内层产物 zip（workflow 的上传步骤变了？先修校验器再谈交付）"; exit 6; }
    ditto -x -k "$INNER" "/tmp/b2b_dl/$key/unpacked" \
      || { echo "[$key] ❌ 内层产物 zip 解不开：$INNER"; exit 6; }
    echo "[$key] 已解开内层：$(basename "$INNER") → $(find "/tmp/b2b_dl/$key/unpacked" -maxdepth 1 -name "*.app" -type d | head -1)"
  else
    INNER=$(find "/tmp/b2b_dl/$key" -maxdepth 2 -name "B2bCollectTool_Win_portable.zip" -type f | head -1)
    [ -n "$INNER" ] || { echo "[$key] ❌ 制品里没有 B2bCollectTool_Win_portable.zip（onedir 交付的两个产物少了一个，先修 workflow 再谈交付）"; exit 6; }
    mkdir -p "/tmp/b2b_dl/$key/root/dist"
    unzip -oq "$INNER" -d "/tmp/b2b_dl/$key/root/dist" \
      || { echo "[$key] ❌ 内层绿色版 zip 解不开：$INNER"; exit 6; }
    echo "[$key] 已解开内层：$(basename "$INNER") → $(ls "/tmp/b2b_dl/$key/root/dist" | tr '\n' ' ')"
  fi
  echo "[$key] artifact 内容：$(find /tmp/b2b_dl/$key -maxdepth 2 -type f | head -3 | tr '\n' ' ')"
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
WIN_ROOT="/tmp/b2b_dl/win/root"
WIN_SETUP=$(find /tmp/b2b_dl/win -maxdepth 2 -name "B2bCollectToolSetup.exe" -type f | head -1)
MAC_APP=$(find /tmp/b2b_dl/mac -maxdepth 4 -name "B2bCollectTool.app" -type d | head -1)
check_win "$WIN_ROOT" "$WIN_SETUP" || FAIL=1
check_mac "$MAC_APP" || FAIL=1
[ "$FAIL" = 0 ] || { echo "❌ 产物校验未通过，本地 dist 保持原样（不覆盖成坏包）"; exit 5; }

# 交付物落到本地 dist：安装包与绿色版是给用户的两份产物，使用说明随绿色版 zip 一起。
mkdir -p b2bcollect_build/dist
cp -f "$WIN_SETUP" b2bcollect_build/dist/B2bCollectToolSetup.exe
cp -f "$(find /tmp/b2b_dl/win -maxdepth 2 -name B2bCollectTool_Win_portable.zip | head -1)" \
  b2bcollect_build/dist/B2bCollectTool_Win_portable.zip
find /tmp/b2b_dl/mac -name "B2bCollectTool_Mac_arm64.zip" -exec cp -f {} b2bcollect_build/dist/B2bCollectTool_Mac_arm64.zip \;
ls -l b2bcollect_build/dist/B2bCollectToolSetup.exe \
      b2bcollect_build/dist/B2bCollectTool_Win_portable.zip \
      b2bcollect_build/dist/B2bCollectTool_Mac_arm64.zip
echo DONE
