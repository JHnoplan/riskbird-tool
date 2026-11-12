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
SHA=$(git rev-parse HEAD)
echo "· 目标 commit：${SHA:0:7}"

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
check_win() {
  [ -n "$1" ] || { echo "[win] ❌ 没找到 B2bCollectTool.exe，无法校验"; return 1; }
  .venv/bin/python - "$1" <<'PYEOF'
import sys
from PyInstaller.archive import readers as R
try:
    arc = R.CArchiveReader(sys.argv[1])
    # --add-data 的条目在 CArchive 的 TOC 里按**目标相对路径**登记（onedir 落成文件、
    # onefile 解到 _MEI 临时目录），所以判据是「有一张名为 config.json 的表项」。
    has_cfg = "config.json" in set(arc.toc)
    mods = set(arc.open_embedded_archive("PYZ.pyz").toc)
except Exception as e:
    print(f"[win] ❌ 归档解析失败：{type(e).__name__}: {e}")
    sys.exit(1)
need = [m for m in ("tool_config", "access_guard", "oss_pool_client", "edition") if m not in mods]
ok = has_cfg and not need
print(f"[win] {'✅' if ok else '❌'} 随包 config.json={'在位' if has_cfg else '缺失'}"
      f"  缺失模块={need or '无'}")
if not ok:
    print("   · config.json 缺失 → 改 workflow 的 --add-data；模块缺失 → 改 --hidden-import。"
          " 这两项任一缺失，外部机器都会读不到权限服务凭证（全员精简版），本机却一切正常。")
sys.exit(0 if ok else 1)
PYEOF
}
check_mac() {
  local app=$1
  [ -n "$app" ] || { echo "[mac] ❌ 没找到 .app，无法校验"; return 1; }
  .venv/bin/python - "$app" <<'PYEOF'
import json, sys
from pathlib import Path
app = Path(sys.argv[1])
cfg = app / "Contents" / "Resources" / "config.json"
ok = cfg.is_file()
detail = "config.json 不在 Resources 下"
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
# 模块收没收（tool_config 这类）不在这里查：onedir 的模块藏在归档里，按文件名找
# 是靠猜布局。这一层由 CI 里跑在 arm64 runner 上的 --acl-doctor 产物门禁负责，
# 它跑的是真产物，判据比任何结构猜测都硬。
print(f"[mac] {'✅' if ok else '❌'} {detail}")
sys.exit(0 if ok else 1)
PYEOF
}

FAIL=0
WIN_EXE=$(find /tmp/b2b_dl/win -maxdepth 3 -name "B2bCollectTool.exe" -type f | head -1)
MAC_APP=$(find /tmp/b2b_dl/mac -maxdepth 4 -name "B2bCollectTool.app" -type d | head -1)
check_win "$WIN_EXE" || FAIL=1
check_mac "$MAC_APP" || FAIL=1
[ "$FAIL" = 0 ] || { echo "❌ 随包凭证校验未通过，本地 dist 保持原样（不覆盖成坏包）"; exit 5; }

find /tmp/b2b_dl/win -name "B2bCollectTool.exe" -exec cp -f {} b2bcollect_build/dist/B2bCollectTool.exe \;
find /tmp/b2b_dl/mac -name "B2bCollectTool_Mac_arm64.zip" -exec cp -f {} b2bcollect_build/dist/B2bCollectTool_Mac_arm64.zip \;
ls -l b2bcollect_build/dist/B2bCollectTool.exe b2bcollect_build/dist/B2bCollectTool_Mac_arm64.zip
echo DONE
