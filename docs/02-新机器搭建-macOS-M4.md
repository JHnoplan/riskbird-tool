# 02 · 新机器搭建（macOS · Apple M4 / arm64）

> 目标：在**另一台 M4 Mac** 上，从零把项目跑到"能开发 + 能出 Mac 包 + 能跑全部离线回归"。
> 本文所有可装性结论都在 2026-11-20 向 PyPI 实测过（第 4 节附实测数据与复跑命令）。
>
> **平台边界先说清**：这台 Mac 能做开发、能出 **Mac .app**、能跑全部离线判据；
> **不能**在本地打 Windows 的 `.exe`——PyInstaller 不做交叉编译，
> Windows 包（含 Win7 兼容通道）只能靠云端 GitHub Actions 的 `windows-latest` runner。
> 这不是缺陷，是工具链事实；改 Windows 交付物 = 推到 main 让云端跑（见 `04`）。

---

## 1. 系统前置（3 项）

| 需要 | 为什么 | 检查 / 安装 |
| --- | --- | --- |
| `Xcode Command Line Tools` | `git`、编译兜底（本项目实测**不需要**编译任何包，但 venv/pip 兜底要） | `xcode-select -p` ；缺则 `xcode-select --install` |
| **Google Chrome** | 1688 与"企知道补采"两条通道走真实浏览器（其余 6 站裸 HTTP 不需要） | 装官方包；不装的话这两条通道不可用，其他功能照常 |
| Git + 仓库访问 | 交付链的门会反复问 git（"进包输入是否都在提交里"） | `git --version` |

---

## 2. 拿代码（**别只 clone**）

```bash
# 方式 A（推荐）：clone + 手工补带未入库资料
git clone <你的仓库地址> PythonDemo && cd PythonDemo

# 方式 B：整目录拷贝——必须排除这几类，否则会把垃圾带过去
rsync -a --exclude '.venv/' --exclude '__pycache__/' --exclude '.pytest_cache/' \
      --exclude 'backups/' --exclude 'b2bcollect_build_backup_*/' \
      --exclude '*_run.log' --exclude '*_progress.json' --exclude 'debug/batch_*.log' \
      旧机:/Users/xxx/Desktop/PythonDemo/ ./
```

**关键**：仓库里 223 份跟踪文件是"项目"，但旧机工作区里还有**少量该入库却没入库**的
资料（`打包说明.md` 等）。逐条清单与处置见 `07-文件边界与打包卫生.md`。
只 clone 会漏掉它们；整目录全拷又会带上 741MB 的 `.venv` 和过期日志。

---

## 3. 建环境（两条命令）

```bash
python3 -m venv .venv
.venv/bin/pip install -r .github/requirements/b2bcollect.txt
```

装完核对（应与本机一致或更新）：

```bash
.venv/bin/python -V     # 本机 3.13.7；≥3.11 均可
.venv/bin/pip list | grep -iE "PyQt5|selenium|openpyxl|pyinstaller|lxml|pillow|xlrd|phonenumbers"
```

**为什么用 `.github/requirements/b2bcollect.txt` 而不是 `b2bcollect_build/requirements.txt`**：
前者是交付链**单一真源**（多平台 workflow 与本机都读它），后者是人手装的子集，
少了 `pyinstaller` / `phonenumbers` / `xlrd`——缺 `xlrd` 会直接让"读取老 .xls"功能报错，
缺 `pyinstaller` 会让本机打包那一步炸。差异表见 `07` 第 3 节。

---

## 4. M4 (arm64) 可装性 —— 实测数据

2026-11-20 向 PyPI 逐个核对交付链 13 个依赖。**结论：全部有预编译轮子，零源码编译。**

| 包 | 最新版 | M4 能直接装的轮子 |
| --- | --- | --- |
| PyQt5 | 5.15.11 | `cp38-abi3-macosx_11_0_arm64`（abi3，3.13 可用） |
| PyQt5-Qt5 | 5.15.19 | `py3-none-macosx_11_0_arm64` |
| PyQt5-sip | 12.20.1 | `cp313-cp313-macosx_10_13_universal2` |
| lxml | 6.1.3 | `cp313-cp313-macosx_10_13_universal2` |
| pillow | 13.0.0 | `cp313-cp313-macosx_11_0_arm64` |
| pyinstaller | 6.22.3 | `py3-none-macosx_10_13_universal2` |
| selenium / openpyxl / requests / websocket-client / beautifulsoup4 / phonenumbers / xlrd | — | 纯 Python `*-none-any.whl` |

复跑这段核对（换机器、或怀疑轮子变了）：

```bash
.venv/bin/python - <<'PY'
import json, urllib.request
for p in ("PyQt5","PyQt5-sip","lxml","pillow","pyinstaller"):
    d = json.load(urllib.request.urlopen(f"https://pypi.org/pypi/{p}/json", timeout=25))
    v = d["info"]["version"]
    ok = [f["filename"] for f in d["releases"][v] if f["filename"].endswith(".whl")
          and ("arm64" in f["filename"] or "universal2" in f["filename"]
               or "abi3" in f["filename"] or "none-any" in f["filename"])]
    print(f"{p} {v}: {len(ok)} 个可用轮子，首个 {ok[0] if ok else '❌ 需源码编译'}")
PY
```

> 自审记录：这条结论第一版是错的——我最初按"文件名含 arm64"筛，把
> `universal2`（同时含 x86_64 与 arm64）和 `abi3` 都误判成"无 mac 轮子"。
> 上一表是修正后的筛法。**凡"装不上"的结论，都要先怀疑筛法。**

**Python 版本要求**：本机开发只需一个现代 CPython（3.13 实测全绿）。
**不要在 M4 上装 Python 3.8**：Win7 兼容通道的 3.8/3.11 双解释器只存在于云端
Windows runner；本机的 frozen 判据是拿 `sys.executable` 同时当两个参数、
故意验"分工不成立必须退 1"（`test_frozen_delivery_offline.py` DG 组 4）。

---

## 5. 首次验收（四条，全绿才开始改代码）

```bash
cd PythonDemo

# ① 构建前硬门 42 项（秒级）
.venv/bin/python .github/scripts/check_packaging_manifest.py | tail -3

# ② 界面冒烟（offscreen，不弹你桌面）
QT_QPA_PLATFORM=offscreen .venv/bin/python debug/gui_smoke_test.py | tail -3

# ③ 全量离线回归 66 路（约 10 分钟，frozen 单跑约 9 分钟）
bash debug/run_offline_regression.sh 2>&1 | tail -6

# ④ 双击真开窗口
open b2bcollect_build/启动B2B多平台采集工具.command
```

③ 的期望尾部：

```
✅ 状态文件对拍：批跑全程没改动开发机的 N 份状态文件（用例的假数据都留在临时目录）
套件：65 通过 / 0 失败 / 1 跳过（缺实机夹具），本轮共展开 66 项
```

`1 跳过`是**正常**的：那一路依赖实机取回的夹具（页面/码图，没入库）。
它必须报 `SKIP-FIXTURE` 而不是 FAIL——把"缺夹具"混进 FAIL，长期红着的一项会淹掉真问题。

---

## 6. 配密钥（打包和权限功能的开关）

工具需要三项凭证：`oss_access_key_id`、`oss_access_key_secret`、`deepseek_api_key`。

**最省事**：打开工具 → 权限管理/设置里保存密钥 → 落到 `~/.b2bcollect/config.json`
（打包态五级链里唯一保证可写的一级）。

三种途径按优先级（详见 `tool_config.py` docstring 第 17-26 行）：
环境变量 → `B2B_CONFIG_FILE` → exe 同级 `config.json` → 随包 `config.json` → `~/.b2bcollect/config.json`。

两条铁律：
- **`config.json` 绝不入库**（根 `.gitignore` 已拦）。密钥的唯一合法归宿是
  Actions Secrets + 用户目录。
- **OSS 的 id 与 secret 必须在同一级凑齐**，"新 id + 旧 secret"会被整级跳过并记 warning。

没有密钥会怎样：开发态照常跑；**打包态**会连不上权限服务 → 一律按丐版起界面
（这正是 2026-11 那批"别人电脑上全是精简版"缺陷的由来，已由 `tool_config` + 打包脚本
第 1 道门收口）。

---

## 7. 出 Mac 包（本机就能做）

```bash
bash debug/build_b2bcollect_mac_local.sh          # 产出 dist/B2bCollectTool.app
B2B_LOCAL_KEEP_OLD=1 bash debug/build_b2bcollect_mac_local.sh   # 保留旧包
```

六步里**四道是门**（任何一道不过就没有包，不留半成品）：
0) 打包清单硬门 → 1) **随包凭证在位**（没配密钥会在这里中止，见第 6 节）
→ 2) chromedriver 打进包根 → 3) PyInstaller 一次成型（`--exclude-module numpy`）
→ 3.5) 产物剪裁 + 重量门 → 4) `--acl-doctor` 真连权限服务 →
5) **只读包**窗口冒烟（复现 Gatekeeper App Translocation"双击没界面"）→
6) **真双击 + 启动台账**（只认产物自己写进 `launch_history.log` 的那行 `shown`）。

第 6 步的由来值得知道：数窗口在锁屏/远程会话下对所有进程都报 0，
它只能当附加信息、不能当交付判据。

---

## 8. Qoder IDE（另一台机器、另一个账号、`qoder-cn-ide`）

**要让 IDE 接手就有项目上下文，靠的是随 Git 走的资产，不是账号。**

本仓库已备好：

| 资产 | 位置 | 作用 |
| --- | --- | --- |
| `AGENTS.md` | 仓库根 | Qoder 自动识别的项目说明（入口，指向 docs） |
| 项目规则 | `.qoder/rules/*.md` | 四类触发：`always_on`（铁律）/ `model_decision`（按任务）/ `glob`（按文件）/ `manual` |
| 本套文档 | `docs/0x-*.md` | 事实与现状（规则文件讲"不许做什么"，本套文档讲"是什么"） |

要点（官方口径，已在上一台机器核实）：
1. `.qoder/rules/` 与 `AGENTS.md` **随 Git 共享即生效**，与登录哪个账号无关——
   换账号换机器都不影响项目上下文。
2. `AGENTS.md` 自动识别；**与规则文件冲突时，规则文件优先**。所以 AGENTS.md 里只放索引与
   最少量约定，细则留在规则文件里，避免两处漂移。
3. 规则总量上限 100,000 字符，只支持自然语言（不放图片、不放链接锚点当内容）。
4. 首次打开仓库后：设置 → 规则（`Rules`）里应能看到这 4 条；看不到就先确认
   `.qoder/rules/*.md` 真的随代码过来了（`git ls-files .qoder`）。
5. 规则文件头部（`frontmatter`）的写法口径——**逐条取自 IDE 自己的解析实现**，不是文档转述，
   所以照着写不会被静默忽略：

   | 事项 | 口径 | 写错的后果 |
   | --- | --- | --- |
   | 起始位置 | 必须从**文件第 1 行**开始 `---`，到下一个 `---` 结束 | 前面有空行＝整段被当正文，规则降级 |
   | 每行格式 | `key: value`，按**第一个冒号**切分 | 折行/多行值**不支持**，续行被逐行丢弃 |
   | `trigger` 合法值 | `glob` / `manual` / `model_decision` / `always_on` | 拼错不报错，回落到默认档 |
   | 判定优先级 | `trigger` 合法 → 用它；否则有 `glob(s)` → `glob`；否则 `alwaysApply: true` → `always_on`；否则有 `description` → `model_decision`；否则 `alwaysApply: false` → `manual` | 想按文件生效却写了 `description`，就变成按模型判断 |
   | **没有头部时** | 默认 `always_on` | 最贵的一档：每轮对话都注入，且没人提醒你 |
   | 正文为空 | 视为空规则 | 文件在、体积在、内容零 |

   要复核这套口径或换版本后重验，跑 `.venv/bin/python debug/qoder_rules_offline_test.py`：
   它内置了一份同样的解析器，会用坏格式夹具反向验证（缺 `frontmatter`、`trigger` 拼错、
   `glob` 没写模式、多行值折行等六份夹具都必须被咬住）。
6. **目录名随构建走，别假设另一台机器也叫 `.qoder`。** 规则目录名取自该 IDE 安装包里的
   `product.json` 的 `dataFolderName` 字段（本机国际版实测为 `.qoder`；实现里另有一条
   `lingma/rules` 的兼容分支）。`qoder-cn` 那台机器上先跑一次：

   ```bash
   # 在装好 Qoder 的那台机器上执行；只看目录名字段，不看任何账号信息
   find /Applications -maxdepth 5 -name product.json 2>/dev/null \
     | xargs grep -h dataFolderName
   ```

   若输出不是 `.qoder`，就把本仓库的 `.qoder/rules/` 整个改名成它认的那个名字，
   否则规则会**一条都不加载**，而且不报错。

**必须在新机器上手工做的三件事**（都不随 Git 走，`git clone` 之后一定缺）：
- **`b2bcollect_build/config.json`：真凭据的唯一落点，被 `.gitignore` 拦着，所以只能带外传**
  （U 盘 / 加密网盘 / 当场录入，别走聊天工具）。里面是 DeepSeek 与 OSS 两组密钥。
  不放这一份的后果很明确也很安静：AI 智能解析与图片上传功能直接不可用——
  程序会 fail-closed 报"缺少密钥"并告诉你去哪配（第 43 条密钥门就是为守住这条而加的：
  密钥**只允许**待在这个文件里，写进任何文档或脚本都会让构建红）。
  另注：`qcollect/`、`qrcode_landing/` 两份小工具还有一条兜底路径指向工作区外的 iOS 工程
  常量文件（`~/Desktop/AI云展/…/AliyunOSSUpload.m`），新机器上没有那个工程，
  所以那两份必须靠 `export OSS_ACCESS_KEY_ID` / `OSS_ACCESS_KEY_SECRET` 才跑得起来。
- MCP / 用户级设置：属于账号与本机，项目不携带。
- 若要 IDE 能跑测试：确认右下角解释器选的是 `.venv/bin/python`（不是系统 python3）。
  这一点和 `.command` 脚本同源——**"解释器在位"和"这个包能跑"是两格**，
  系统 python3 里没有 PyQt5，症状是"窗口一闪就没"。

---

## 9. 常见踩坑（都在本项目真实发生过）

| 症状 | 真相 | 出处 |
| --- | --- | --- |
| 双击 `.command` 一闪就没 | 环境没建（旧版脚本写死了开发机绝对路径，已修） | 第 8 节末、`06` 第 3 类 |
| 本机全绿、CI 红 | 判据跑的是工作区，CI 检出的是提交——**改了没 commit** | 第 20 条那一路硬门会点名 |
| 别人电脑上全是精简版 | 打包时没带 `config.json` | 打包脚本第 1 道门 |
| 双击没界面但 CI 全绿 | Gatekeeper App Translocation 把包搬到只读位置 | 打包脚本第 5 道门 |
| Windows 包在老系统装不上 | VC 运行库 / OpenSSL 1.1 命名 / PE 子系统地板 | 需求 10A/10B，见 `04` |
| 批跑挂住 20 分钟不动 | 某套件在 offscreen 等一个模态弹窗（已加 600s 看门狗） | `run_offline_regression.sh` 头部 |
