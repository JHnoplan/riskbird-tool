; ══════════════════════════════════════════════════════════════════════════
;  B2B 多平台采集工具 —— Windows 安装包（NSIS）
;
;  编译方式（CI 里由 .github/workflows/build-b2bcollect.yml 执行，本机手跑同理）：
;    cd b2bcollect_build
;    makensis /DB2B_ROOT="<b2bcollect_build 的绝对路径>" installer/b2bcollect_setup.nsi
;
;  ══ 为什么是「每用户安装」而不是「装进 Program Files」═══════════════════════
;  这不是口味问题，是本轮 Windows 实机故障的直接结论：
;    · 本工具的运行期数据（登录 Cookie、30 天列表缓存、自检报告、任务进度）按
;      riskbird_build/runtime_paths.py 与 tool_config.state_dir 的口径**优先落在程序
;      同级目录**。装进 Program Files 时普通用户写不进去，全部数据会静默退到
;      %APPDATA% 之外没人知道的角落，症状是「登录状态没保住 / 每天白烧查询额度」；
;    · 装进 Program Files 还要 UAC。用户只是更新一版工具，却每次都要管理员密码，
;      在企业里等于让 IT 部门当人肉部署器。
;  所以走 VS Code / Slack / Discord 同一路线：$LOCALAPPDATA\Programs\、
;  RequestExecutionLevel user、卸载信息写 HKCU。目录可写、不弹 UAC、不动系统注册表。
;
;  ══ 为什么不再是「一个 exe 文件」════════════════════════════════════════════
;  旧产物是 PyInstaller --onefile：每次启动把整个运行时解到 %TEMP%\_MEI<pid>，退出即删。
;  2026-11-12 实机崩溃「切换账号后点重启就报 FileNotFoundError: \_MEI…\base_library.zip」
;  就是这条链路——新旧实例在同一个临时目录上抢着解包/删除，谁先到谁把对方的归档删了。
;  现在改 onedir（程序 + _internal 运行时目录），运行时是磁盘上稳定的真实文件，
;  不解包、不回收、没有赛跑；代价是产物从「一个文件」变成「一个目录」，
;  所以必须靠本安装包落地，另留一份目录 zip 给「不让装软件」的场景兜底。
;
;  ══ 编码 ════════════════════════════════════════════════════════════════════
;  本文件必须是 **UTF-8 with BOM**：makensis 只在看到 BOM 时才按 UTF-8 读脚本，
;  否则按系统代码页解释——英文 Windows 的 runner 是 cp1252，中文界面文字会全部
;  变成乱码，而且构建照样「成功」。这是一个只在产物里才暴露的缺陷，所以 BOM 由
;  .github/scripts/package_win_artifacts.py 在打包前显式校验，不靠人眼。
; ══════════════════════════════════════════════════════════════════════════

Unicode true
!include "MUI2.nsh"
!include "FileFunc.nsh"      ; ${GetSize}：控制面板里那个「预计大小」

; ── 名称与常量（改这里就够了，正文一律引用宏）────────────────────────────
!define APP_NAME    "B2B 多平台采集工具"
!define APP_DIRNAME "B2bCollectTool"
!define APP_EXE     "B2bCollectTool.exe"
!define UNINST_KEY  "Software\Microsoft\Windows\CurrentVersion\Uninstall\B2bCollectTool"

; B2B_ROOT：b2bcollect_build 的绝对路径。!cd 把它固定下来之后，下面所有 File /
; OutFile 的相对路径都以它为基准——不再依赖「makensis 是从哪个目录启动的」这种
; 谁都可能顺手改一下的东西。
!ifdef B2B_ROOT
  !cd "${B2B_ROOT}"
!endif

; BUILD_DIR：onedir 产物所在目录（相对 B2B_ROOT）。CI 不需要改这里。
!ifndef BUILD_DIR
  !define BUILD_DIR "dist"
!endif
!ifndef OUT_FILE
  !define OUT_FILE "B2bCollectToolSetup.exe"
!endif
!ifndef APP_VERSION
  !define APP_VERSION "0.0"
!endif
!ifndef VER4
  !define VER4 "1.0.0.0"
!endif

Name "${APP_NAME} ${APP_VERSION}"
OutFile "${OUT_FILE}"
InstallDir "$LOCALAPPDATA\Programs\${APP_DIRNAME}"
InstallDirRegKey HKCU "${UNINST_KEY}" "InstallLocation"
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetOverwrite try

; 文件属性里的版本信息：任务管理器、控制面板「程序和功能」、以及用户随手截图
; 给技术支持时能看到的那个版本号。没有它，出问题只能靠「你那个包是哪天的」来对。
VIProductVersion "${VER4}"
VIAddVersionKey /LANG=2052 "ProductName" "${APP_NAME}"
VIAddVersionKey /LANG=2052 "FileDescription" "${APP_NAME} 安装程序"
VIAddVersionKey /LANG=2052 "FileVersion" "${APP_VERSION}"
VIAddVersionKey /LANG=2052 "ProductVersion" "${APP_VERSION}"
VIAddVersionKey /LANG=2052 "LegalCopyright" "内部工具"

; ── 界面 ──────────────────────────────────────────────────────────────────
!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "立即启动 ${APP_NAME}"
!define MUI_FINISHPAGE_SHOWREADME "$INSTDIR\使用说明.txt"
!define MUI_FINISHPAGE_SHOWREADME_TEXT "查看使用说明（安装位置、数据落在哪、报错怎么查）"
!define MUI_FINISHPAGE_SHOWREADME_NOTCHECKED

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "SimpChinese"
; ── 运行环境体检与自动补齐（需求 10）─────────────────────────────────────
; 由来：2026-11-19 给一台纯小白的电脑装工具，装完双击只得到两句系统错误：
;   · 「无法启动此程序，因为计算机中丢失 api-ms-win-core-path-l1-1-0.dll」
;   · 「Failed to load Python DLL …\_internal\python311.dll。LoadLibrary: 找不到指定的模块」
; 两句是同一个根因：那个系统组件从 Windows 10 才存在，而产物是 Python 3.11 打的
; （Python 3.9 起就要求 Win10+）。这条界限决定了本体检的两半：
;   · **能自动补的就当场补**，不让用户去找东西装——微软 VC++ 运行库就是这一类，
;     包内自带、只在系统里确实没有时才静默装（用户拍板的口径）；
;   · **补不了的要在装之前就说清楚**：操作系统没有的 api set 不是安装步骤能补的，
;     硬装上去只会得到一个双击就报英文错误的桌面图标。这里宁可 Abort，也不留那样一个图标。
; 以前这一格是空的：本文件从头到尾没查过系统版本、没查过运行库、没留过一行日志，
; 所以远程排障只能靠用户拍照——本轮那两张照片才认出是 Win7，代价是一整台机器白装。
; 现在不管合格不合格都先写 install_check.log：下一次问一句「你装的那台是什么系统」
; 就能直接读出来，不必再猜。
!define MIN_WIN_MAJOR 10
!define WIN_NT_KEY "SOFTWARE\Microsoft\Windows NT\CurrentVersion"
; 体检日志落在**包外**（与安装目录同级但不由安装程序删）：卸载工具时这段历史还得留着，
; 而「下一次也报过同样的错」这条线索只有历史能给。
!define CHECK_LOG "$LOCALAPPDATA\B2bCollectTool\install_check.log"
; 包内自带的微软运行库。CI 把它下到 installer\deps\ 里；本机手工打包没下也不拦，
; 走下面 !if /FileExists 那条降级分支（工具自己带 app-local 运行库，缺它通常照样能跑）。
!define VCREDIST_FILE "installer\deps\VC_redist.x64.exe"
!if /FileExists "${VCREDIST_FILE}"
  !define HAS_VCREDIST
!endif

Function EnvLog
    ; 调用约定：$R9 是要记的那一句话。追加写，不覆盖（理由见上面 CHECK_LOG 的注释）。
    ; 写不进去不拦安装：日志是给人看的证据，不是功能开关，为它中止安装是本末倒置。
    CreateDirectory "$LOCALAPPDATA\B2bCollectTool"
    ClearErrors
    FileOpen $9 "${CHECK_LOG}" a
    IfErrors envlog_done
    FileSeek $9 0 END
    FileWrite $9 "v${APP_VERSION} | $R9$\r$\n"
    FileClose $9
envlog_done:
FunctionEnd

Function .onInit
    ; 64 位注册表视图：安装包是 32 位的，默认读到的是 WOW6432Node 那一套，而
    ; Windows NT\CurrentVersion 的原生数据在 64 位视图里。读完在下面 envchk_ok 里
    ; 还原——安装段里还有 HKCU 的卸载登记，别让这里的视图选择连坐到它们。
    SetRegView 64
    ClearErrors
    ReadRegDWORD $R0 HKLM "${WIN_NT_KEY}" "CurrentMajorVersionNumber"
    ; 这个值从 Windows 10 才存在：Win7/8/8.1 上根本没有它，**读不到本身就是系统版本
    ; 信号**。判据走 error flag 而不是比对某个失败字面量——flag 是唯一稳定的表示法。
    ; IfErrors 必须紧跟在这一句后面：中间再夹任何一句注册表读取，flag 就被顶掉了。
    IfErrors envchk_old 0
    ReadRegStr $R1 HKLM "${WIN_NT_KEY}" "CurrentBuildNumber"
    ReadRegStr $R2 HKLM "${WIN_NT_KEY}" "ProductName"
    ReadRegStr $R3 HKLM "${WIN_NT_KEY}" "CurrentVersion"
    ReadRegStr $R5 HKLM "SYSTEM\CurrentControlSet\Control\Session Manager\Environment" "PROCESSOR_ARCHITECTURE"
    StrCpy $R4 "图形"
    IfSilent 0 +2
    StrCpy $R4 "静默"
    ; 这一行不能直接写 ${HAS_VCREDIST}：那个宏只在包内真带了 redist 时才 !define，
    ; 没带的时候 makensis 会因「未定义的宏」不给编——一个日志字段不该有这种权力。
    StrCpy $R8 "没带"
!ifdef HAS_VCREDIST
    StrCpy $R8 "带了"
!endif

    StrCpy $R9 "体检：major=$R0 build=$R1 cur=$R3 product=$R2 arch=$R5 方式=$R4 运行库=$R8"
    Call EnvLog

    ; 先记日志、再下结论——这个顺序本身就是判据（离线门会钉）：日志里没有这台机器的
    ; 底细，「下次装不动再问一句是什么系统」就问不出来。这里再兜一层 flag 之外的失败
    ; 表示法（空串不是任何真实 major，同样按老系统拦）。
    StrCmp $R0 "" envchk_old 0
    ; IntCmp 的三个跳转依次是「相等 / 小于 / 大于」：major=10 的 Win10 是**相等**，
    ; 必须落到 envchk_ok。参数顺序由 test_installer_env_gate 逐字钉住——写反一次的
    ; 代价是全量 Win10 用户被当成老系统拦下，而本机怎么试都试不出来（本机是 Mac，
    ; makensis 只在云端跑）。
    IntCmp $R0 ${MIN_WIN_MAJOR} envchk_ok envchk_old envchk_ok

envchk_old:
    ; 老系统：把「为什么装了也打不开」讲完再中止。文案逐句 StrCpy 拼进寄存器，
    ; 不用行尾续行（makensis 会把续行后的引号串当成额外参数，弹窗就只剩第一句）。
    StrCpy $R6 "这台电脑的系统版本低于 Windows 10（$R2，版本 $R3，内部版 $R1）。$\n$\n"
    StrCpy $R6 "$R6本工具的运行环境要求 Windows 10 或更高：程序用的 Python 运行时从 Windows 10 起才有那个系统组件，$\n"
    StrCpy $R6 "$R6缺的那几个文件不是安装步骤能补上的，所以这里没有继续安装——装完也只会得到一个双击就报错的图标。$\n$\n"
    StrCpy $R6 "$R6两条出路：$\n"
    StrCpy $R6 "$R6   · 换一台 Windows 10/11 的电脑运行这个安装包；$\n"
    StrCpy $R6 "$R6   · 向管理员索取「兼容版安装包」（文件名 B2bCollectToolSetup_Win7.exe），它是专为老系统打的那一版。$\n$\n"
    StrCpy $R6 "$R6本次没有写入任何文件，你的登录状态与采集数据都没有被动过。$\n"
    StrCpy $R6 "$R6详细信息已记进：${CHECK_LOG}"
    StrCpy $R9 "结论：环境不合格（$R2 / $R3 build $R1），本次安装已拦下"
    Call EnvLog
    ; 静默安装（运维推送 /S）里 MessageBox 照样会弹并等人点：没人看得见那个窗口，
    ; 安装进程就一直挂到被超时杀掉，日志里只剩一句「安装没结束」。所以静默只写日志。
    IfSilent envchk_old_abort 0
    MessageBox MB_OK|MB_ICONSTOP "$R6"
envchk_old_abort:
    Abort "这台电脑的系统低于 Windows 10，本工具需要 Windows 10 及以上；本次安装已拦下（详情见 ${CHECK_LOG}）"

envchk_ok:
    ; 系统版本合格。VC++ 运行库的补装在主程序安装段里（「整批拷贝之后、拷完校验之前」
    ; 那一段）：.onInit 跑在欢迎页**之前**，把一个可能要半分钟的静默子安装器放在那里，
    ; 用户面对的就是一个「双击之后毫无动静」的窗口——足够让小白认定卡死然后杀进程。
    ; 安装段里有进度条与 DetailPrint，看得见正在做什么。
    StrCpy $R9 "体检通过（major=$R0 build=$R1），继续安装"
    Call EnvLog
    ; 视图还原：开头为读系统版本切到 64 位，这里交还给安装包位数的默认视图，
    ; 不把这次切换带进后面的安装段（安装段里还有 HKCU 的卸载登记）。
    SetRegView default
FunctionEnd

; ── 安装 ──────────────────────────────────────────────────────────────────
Section "主程序" SEC_APP
    SectionIn RO

    ; 旧实例还在跑就别换文件：onedir 的产物是「exe + _internal 一整批」，只换成
    ; 一半（新 exe 配旧运行时，或反过来）比不装更糟——报出来的错会比以前更难读，
    ; 而用户完全看不出自己拿到的是个拼起来的版本。
    ; 探测手法：以「追加写」方式打开那个正在运行的 exe。Windows 把正在执行的镜像
    ; 以「禁止写」的方式占住，所以打开失败＝有实例在跑。刻意不用 Delete 试探（那会
    ; 真的删掉文件），也不用 tasklist / 第三方插件（多一个依赖就多一类
    ; 「这台机器上没有」的失败）。
    IfFileExists "$INSTDIR\${APP_EXE}" check_lock copy_files
    check_lock:
        ClearErrors
        FileOpen $9 "$INSTDIR\${APP_EXE}" a
        IfErrors ask_close
        FileClose $9
        Goto copy_files
    ask_close:
        ; 静默安装（/S）里这句问句没人能回答：MessageBox 照样会弹、进程就一直等到被超时
        ; 杀掉，日志里只剩一句「安装没结束」。所以静默时直接按「取消」处理——这个分支还
        ; 排在任何 File 之前，中止得干干净净，一个文件都没动过。
        IfSilent abort_locked
        MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "${APP_NAME} 正在运行，安装目录里的程序文件被占用。$\n$\n请先关掉正在运行的工具窗口（必要时在任务管理器里结束 ${APP_EXE}），然后点「重试」。$\n点「取消」本次安装立即中止——不会留下装了一半的版本。" IDRETRY check_lock IDCANCEL abort_locked
    abort_locked:
        Abort "已取消：工具正在运行，本次没有替换任何文件。"

    copy_files:
    ; 整批复制 onedir 产物：程序本体 + _internal 运行时 + 随包凭证 config.json +
    ; 跨目录采集器源码（riskbird_build / ali1688_build）+ 内置 chromedriver。
    ; **复制**这一步刻意不逐条列文件：列一条就少一处与打包清单对拍的机会，
    ; 「少一个文件不报错、只少一个功能」正是本项目被咬过最多次的形状。
    ; 下面**校验**那一段则要逐条列——那里对拍的正是那张打包清单。
    SetOutPath "$INSTDIR"
    ; ── 升级先把旧运行时清掉，再铺新的 ────────────────────────────────────────
    ; File /r 的语义是「同名覆盖、不同名保留」：它只覆盖这一版里有的那些文件，上一版有、
    ; 这一版改名或删掉的模块会**原样留着**。onedir 的 _internal 是一整批互相咬合的文件
    ; （base_library.zip、Python 的 .dll/.pyd、各个包目录），混着两批的版本只在「装过旧版
    ; 再装新版」的机器上出现——本机每次都是全新安装，怎么试都是好的，用户那边却会报出读
    ; 不懂的错（本轮修的「base_library.zip 找不到」形状上正是新旧两批互相咬不住）。PyInstaller
    ; 官方对升级的口径同样是「先删掉旧的 _internal 再放新的」。
    ;
    ; 递归删除在安装段里**只允许**指向运行时目录这一处：安装目录里还躺着用户的登录状态、
    ; 缓存与采集进度（b2bcollect_data / b2bcollect_state / riskbird_data），安装步骤没有
    ; 资格删它们——卸载段都要问一次、默认保留，这里连问都不必问。这一句由
    ; .github/scripts/package_win_artifacts.py 的 check_installer_payload 在 makensis 之前
    ; 判：少这一句、排到 File 之后、或删到别处去，都在装配那一步拦下而不是等用户发现。
    RMDir /r "$INSTDIR\_internal"
    File /r "${BUILD_DIR}\${APP_DIRNAME}\*.*"

    ; 使用说明进安装目录（完成页有勾选框直接打开；产物 zip 里也带同一份，
    ; 由 .github/scripts/package_win_artifacts.py 放进去，两处只有一份源文件）。
    File /oname=使用说明.txt "installer\usage_guide.txt"

    ; ── 微软 VC++ 运行库：缺了才装（需求 10，用户拍板「包内自带、缺了才装」）────
    ; 判据问的是「这台机器上有没有 vcruntime140.dll」而不是「我们带没带」：产物里本来
    ; 就有 app-local 那份（PyInstaller 放进 _internal），这一步是给「系统里干净、又恰好
    ; 需要系统级那份」的机器补位。$SYSDIR 在 32 位安装包里指 SysWOW64，所以 64 位那份
    ; 要另问 $WINDIR\Sysnative（这个虚拟目录只在 64 位 Windows 上存在；32 位系统上
    ; 两问合一——32 位系统的系统目录本来就只有一份）。
    ; 位置有讲究，不是随手放的：package_win_artifacts 的安装段判据要求所有 File 都排在
    ; 逐条校验之前、而运行时目录的递归删除又必须排在所有 File 之前——所以这一段只能
    ; 插在「整批拷贝之后、拷完校验之前」这唯一允许的窗口里。语义上有次序代价，说清楚
    ; 不算亏：运行库装完之后万一校验拦下（关键文件没装全），系统里已经多了一份 VC++
    ; 运行库——它是微软的标准系统组件，多装不伤任何软件；为次序好看把解压挪出安装段
    ; 的方案反而更绕，不值。
    IfFileExists "$SYSDIR\vcruntime140.dll" redist_done 0
    IfFileExists "$WINDIR\Sysnative\vcruntime140.dll" redist_done 0
    DetailPrint "系统里没有 VC++ 运行库，正在补装（微软官方安装包，静默进行，不会重启电脑）……"
!ifdef HAS_VCREDIST
    ; 解到 $PLUGINSDIR（安装结束由 NSIS 回收），不进安装目录：它是安装器的工具，不是
    ; 交付物；留在安装目录里下一次升级还得跟它斗一句「同名覆盖」。InitPluginsDir 必须
    ; 先于 $PLUGINSDIR 的第一次使用——没有它这个目录还不存在，25MB 的运行库会被 File
    ; 解到一个名字就叫「$PLUGINSDIR」的真实子目录里去，而 nsExec 找不到 exe。
    InitPluginsDir
    SetOutPath "$PLUGINSDIR"
    File "${VCREDIST_FILE}"
    ; /quiet /norestart：全程不问人、不自己重启机器。ExecToLog 把它的输出接进安装日志，
    ; 退出码 Pop 出来记进体检日志：0=刚装好、1638=已有同版或更新版（等于已装好）、
    ; 3010=装好了要重启才生效——其余才是真失败；失败也不拦安装（app-local 兜底）。
    nsExec::ExecToLog '"$PLUGINSDIR\VC_redist.x64.exe" /install /quiet /norestart'
    Pop $7
    StrCpy $R9 "VC++ 运行库补装退出码 $7（0=刚装好 1638=已有同版或更新版 3010=装好待重启）"
    Call EnvLog
!else
    StrCpy $R9 "安装包内没带 VC_redist.x64.exe（手工打包常见）：程序自带 app-local 运行库照常能用，真缺再让用户联网装"
    Call EnvLog
!endif
    redist_done:

    ; ── 拷完回头验一遍：装进机器的那份，必须真是打包时那份关键文件 ──────────
    ; 为什么这一段不能省（两种失败都不报错，而且都只有用户那边看得见）：
    ;   1) SetOverwrite try 的语义是「目标文件被占用就**跳过**，不算失败」。跳过之后
    ;      装出来的是新 exe 配旧运行时（或反过来）这种拼起来的版本，安装却照样报成功。
    ;      上面那段占用探测只探得到 exe 这一个文件——_internal 里的某个 .py、.dll 被
    ;      安全软件或索引服务按住时，探测是绿的，文件却是旧的；
    ;   2) makensis 的 File /r 是「匹配到几份就打几份」：dist 布局改了名、或者装配那
    ;      一步少剔/少放一样东西，安装包照样编得出来、装得上、跑得起来，只少那一路功能。
    ;      CI 上验的是 **dist 树**（package_win_artifacts.check_layout），没有任何一处
    ;      验过「makensis 真的把这些文件放进了用户机器」——这一段补的就是这一格。
    ; 关键文件清单只有一个真源：.github/scripts/package_win_artifacts.py 的
    ; REQUIRED_INTERNAL。下面每一对（StrCpy + IfFileExists）都要与它逐个对上，由
    ; check_installer_payload 在 makensis 之前判：清单加了一条、这里忘了一行，
    ; 或者反过来这里多验了一个清单里没有的路径，构建当场拦下并点名是哪一条。
    ;
    ; 手法刻意用 NSIS 自带的 IfFileExists，而不是 ${IfNot} ${FileExists}：
    ;   1) 不引入 LogicLib.nsh——多一个 !include 就多一类「这台机器的 NSIS 里没有这个
    ;      库版本」的失败，而本文件通篇已经是 IfFileExists/StrCmp/Goto 这一套；
    ;   2) 判据要按「这一行的假分支跳到哪个标号」把每条校验钉回打包清单，平铺的
    ;      「真→下一行 / 假→copy_missing」形状判得最直接，LogicLib 的块结构还得先做
    ;      一遍括号配对。配对错了不会有人报错，只会让这条门数错校验条数。
    ; 每一条前面那句 StrCpy $7 是给失败文案用的：报的是**哪一个**文件没落地，
    ; 而不是「装不全」——后者让用户只能重装一次试试看。
    StrCpy $7 "${APP_EXE}（主程序本体）"
    IfFileExists "$INSTDIR\${APP_EXE}" 0 copy_missing
    StrCpy $7 "_internal\base_library.zip"
    IfFileExists "$INSTDIR\_internal\base_library.zip" 0 copy_missing
    StrCpy $7 "_internal\config.json"
    IfFileExists "$INSTDIR\_internal\config.json" 0 copy_missing
    ; 构建戳单独占一对，理由与其余每一条都不同：其余那些没落地会**崩**或少一路功能，
    ; 而这一份没落地时程序照开、采集照跑、界面照用——只有「自己是哪一版」这件事从此说不清：
    ; 更新提示永远闭嘴（它拿不到本机基准），远程支持也只能猜。一个安静失效的版本号比一个
    ; 报错的文件更难被发现，所以这一格只能在装完的那一刻验一次，别的地方没有第二次机会。
    StrCpy $7 "_internal\version.json（随包构建戳）"
    IfFileExists "$INSTDIR\_internal\version.json" 0 copy_missing
    StrCpy $7 "_internal\mk_phone_font.json"
    IfFileExists "$INSTDIR\_internal\mk_phone_font.json" 0 copy_missing
    StrCpy $7 "_internal\riskbird_build\runtime_paths.py"
    IfFileExists "$INSTDIR\_internal\riskbird_build\runtime_paths.py" 0 copy_missing
    StrCpy $7 "_internal\riskbird_build\riskbird_crawler_v2.py"
    IfFileExists "$INSTDIR\_internal\riskbird_build\riskbird_crawler_v2.py" 0 copy_missing
    StrCpy $7 "_internal\riskbird_build\aiqicha_crawler_v2.py"
    IfFileExists "$INSTDIR\_internal\riskbird_build\aiqicha_crawler_v2.py" 0 copy_missing
    StrCpy $7 "_internal\riskbird_build\qizhidao_crawler.py"
    IfFileExists "$INSTDIR\_internal\riskbird_build\qizhidao_crawler.py" 0 copy_missing
    StrCpy $7 "_internal\riskbird_build\block_guard.py"
    IfFileExists "$INSTDIR\_internal\riskbird_build\block_guard.py" 0 copy_missing
    StrCpy $7 "_internal\riskbird_build\chrome_driver.py"
    IfFileExists "$INSTDIR\_internal\riskbird_build\chrome_driver.py" 0 copy_missing
    StrCpy $7 "_internal\ali1688_build\ali1688_crawler.py"
    IfFileExists "$INSTDIR\_internal\ali1688_build\ali1688_crawler.py" 0 copy_missing
    ; 目录型条目只能验「目录里有文件」：*.* 匹配到任意一个文件才算落地。
    ; 空目录在这里算失败是对的——drivers/ 空着等于一个 chromedriver 都没打进去。
    StrCpy $7 "_internal\drivers（内置 chromedriver 多版本目录）"
    IfFileExists "$INSTDIR\_internal\drivers\*.*" 0 copy_missing
    StrCpy $7 "_internal\enf_clean（清洗子包目录）"
    IfFileExists "$INSTDIR\_internal\enf_clean\*.*" 0 copy_missing
    StrCpy $7 "使用说明.txt"
    IfFileExists "$INSTDIR\使用说明.txt" 0 copy_missing
    Goto copy_ok

copy_missing:
    ; 这里必须在写快捷方式与注册表**之前**中止：装了一半却在控制面板里留下一个能点开
    ; 却跑不起来的条目，比什么都不留更难收拾（用户会以为是程序坏了，而不会想到是没装全）。
    ; 文案逐句 StrCpy 拼接，不用行尾续行 `\`：makensis 把续行后的引号串当成**额外的
    ; 参数**而不是同一段文本，MessageBox 拿到的还是第一句，后面几句变成无人认领的参数
    ; ——本机没有 makensis，这种差异只在产物里现形，所以整份脚本一律只用单行写法
    ; （这条纪律由 check_installer_payload 判：非注释行不许以 \ 结尾）。
    StrCpy $6 "${APP_NAME} 没能把程序文件装到位：$7$\n$\n"
    StrCpy $6 "$6安装目录里的程序文件被占用时，SetOverwrite try 会跳过它继续往下装，所以这里主动停下。$\n"
    StrCpy $6 "$6继续装下去会得到一个新旧文件混在一起的版本，启动时报的错反而更难读。$\n"
    StrCpy $6 "$6本次没有创建快捷方式与卸载入口；你的登录状态和采集数据都没有被动过。$\n$\n"
    StrCpy $6 "$6请按下面的顺序处理，然后重新运行这个安装包（会覆盖补齐）：$\n"
    StrCpy $6 "$6   · 关掉正在运行的工具窗口（必要时在任务管理器里结束 ${APP_EXE}）；$\n"
    StrCpy $6 "$6   · 如果装了安全软件，允许本次安装写入安装目录；$\n"
    StrCpy $6 "$6安装位置：$INSTDIR"
    ; 静默安装（运维推送时的 /S）里 MessageBox **照样会弹并且等人点**：没有人看得见那
    ; 个窗口，安装进程就一直挂在那里直到被超时杀掉，日志里只留一句「安装没结束」。
    ; 所以静默分支只写日志并中止，图形安装才弹窗——这是 NSIS 文档给的标准手法。
    ; IfSilent 的第一个参数是「条件成立时跳去的标号」（与上面 IfFileExists 同一套写法）。
    IfSilent copy_abort
    MessageBox MB_OK|MB_ICONSTOP "$6"
copy_abort:
    DetailPrint "安装未完成：$7 没有落到安装目录，没有创建快捷方式与卸载入口。"
    Abort "安装未完成：$7 没有落到安装目录，本次没有创建快捷方式与卸载入口。"

copy_ok:

    ; 快捷方式：开始菜单一份、桌面一份。每用户安装下 $SMPROGRAMS/$DESKTOP 就是
    ; 当前用户的目录，不需要管理员权限，也不会给别人多出一个图标。
    CreateDirectory "$SMPROGRAMS\${APP_NAME}"
    CreateShortCut "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}"
    CreateShortCut "$SMPROGRAMS\${APP_NAME}\使用说明.lnk" "$INSTDIR\使用说明.txt"
    CreateShortCut "$SMPROGRAMS\${APP_NAME}\卸载 ${APP_NAME}.lnk" "$INSTDIR\uninstall.exe"
    CreateShortCut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}"

    WriteUninstaller "$INSTDIR\uninstall.exe"

    ; 卸载信息写 HKCU（不是 HKLM）：与「每用户安装」成套——控制面板能正常卸载，
    ; 机器级注册表里不会留下一个指向别人用户目录的死条目。
    WriteRegStr HKCU "${UNINST_KEY}" "DisplayName" "${APP_NAME}"
    WriteRegStr HKCU "${UNINST_KEY}" "DisplayVersion" "${APP_VERSION}"
    WriteRegStr HKCU "${UNINST_KEY}" "Publisher" "内部工具"
    WriteRegStr HKCU "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\${APP_EXE}"
    WriteRegStr HKCU "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
    WriteRegStr HKCU "${UNINST_KEY}" "UninstallString" '"$INSTDIR\uninstall.exe"'
    WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
    WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1
    ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
    IntFmt $0 "0x%08X" $0
    WriteRegDWORD HKCU "${UNINST_KEY}" "EstimatedSize" $0

    ; 装完顺手清一次「旧版单文件 exe 留在 %TEMP% 的解包目录」的提示：那些目录是
    ; 系统临时目录，Windows 自己会回收，但用户会因为「刚装的软件在临时目录里有
    ; 文件夹」而误以为新版还是老形态，所以把落点写清楚比清几个文件更有用。
    DetailPrint "安装位置：$INSTDIR"
    DetailPrint "登录状态与缓存：$INSTDIR\b2bcollect_data / $INSTDIR\riskbird_data"
SectionEnd

; ── 卸载 ──────────────────────────────────────────────────────────────────
Section "Uninstall"
    ; 同安装：正在运行时不删文件，否则留下一半目录，下次安装还会撞上占用。
    IfFileExists "$INSTDIR\${APP_EXE}" 0 uninstall_go
        ClearErrors
        FileOpen $9 "$INSTDIR\${APP_EXE}" a
        IfErrors uninstall_locked
        FileClose $9
        Goto uninstall_go
    uninstall_locked:
        ; 静默卸载同样没人能点这个「确定」：先判 IfSilent，静默时直接中止（结论与图形
        ; 分支一样——正在运行时不删文件，否则留下一半目录，下次安装还会撞上占用）。
        IfSilent uninstall_abort
        MessageBox MB_OK|MB_ICONSTOP "${APP_NAME} 正在运行。请先关掉工具窗口，再重新执行卸载。"
    uninstall_abort:
        Abort
    uninstall_go:
        Delete "$INSTDIR\uninstall.exe"
        Delete "$INSTDIR\${APP_EXE}"
        Delete "$INSTDIR\使用说明.txt"
        RMDir /r "$INSTDIR\_internal"

        ; 运行期数据（登录 Cookie、列表缓存、任务进度、自检报告）就在安装目录里，
        ; 但它是用户这几个月的劳动成果：**绝不允许静默删掉**。问一次，默认保留。
        ; MB_DEFBUTTON2 不是装饰：NSIS 的 MessageBox 不写这一位时，默认选中的是**第一个
        ; 按钮**（也就是「是，连数据一起删」），一路回车下去就把数据删了——跟这句
        ; 「默认保留」正好相反。把焦点放到「否」上，才让「不选就等于同意」这件事成立。
        ;
        ; IfSilent keep_data 是同一件事的另一半：运维推送 /S 卸载时没人回答这句问句，
        ; 必须由脚本替用户选**保守**那一支（保留数据）。少了这一行，静默卸载要么挂在
        ; 那个看不见的弹窗上，要么（更坏）按默认走进「连数据一起删」。
        IfSilent keep_data
        MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON2 "是否同时删除登录状态、缓存与采集进度数据？$\n$\n· 选「否」（推荐）：保留 $INSTDIR 下的 b2bcollect_data / b2bcollect_state / riskbird_data，以后重装会自动接着用，登录状态不用重新过一遍；$\n· 选「是」：连同这些目录一起清除，无法恢复。" IDNO keep_data
        ; ↓ 只有这一条分支（用户明确选了「是」）才允许递归删除。
        RMDir /r "$INSTDIR\b2bcollect_data"
        RMDir /r "$INSTDIR\b2bcollect_state"
        RMDir /r "$INSTDIR\riskbird_data"
        RMDir /r "$INSTDIR\aiqicha_data"
        ; 下面这一行今天大概率删不掉任何东西（1688 通道在包里走 b2bcollect_data），
        ; 留着它是因为「用户选了全删、结果磁盘上还剩一个数据目录」这种漏法没有症状，
        ; 只有下次重装时才会被发现——而那时没人会想起是卸载脚本少写一行。
        RMDir /r "$INSTDIR\ali1688_data"
        RMDir /r "$INSTDIR"
        Goto uninstall_tail
    keep_data:
        ; 这一分支里**绝不出现 RMDir /r "$INSTDIR"**。旧写法是两个选项走到同一句递归
        ; 删除上：选「否」也照删，上面那句「保留 b2bcollect_data / b2bcollect_state /
        ; riskbird_data」当场变成空话。这类缺陷最坏的地方是没有症状——卸载照样「成功」、
        ; 卸载日志照样漂亮，用户要到下一次重装登录状态全没了才知道，而那时没人会去想
        ; 是卸载脚本少写了一个分支跳转。
        ;
        ; 不带 /r 的 RMDir 只在目录**已经空了**的时候才删：程序文件（exe、_internal、
        ; 使用说明、uninstall.exe）在上面已经删净，目录真空了就顺手移除，还剩用户数据
        ; 就原样留着。行为跟着用户的选择不跑，也不会因为保守一点而留下垃圾目录。
        DetailPrint "已保留登录状态与采集数据：$INSTDIR"
        RMDir "$INSTDIR"
    uninstall_tail:
        Delete "$DESKTOP\${APP_NAME}.lnk"
        Delete "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk"
        Delete "$SMPROGRAMS\${APP_NAME}\使用说明.lnk"
        Delete "$SMPROGRAMS\${APP_NAME}\卸载 ${APP_NAME}.lnk"
        RMDir "$SMPROGRAMS\${APP_NAME}"

        DeleteRegKey HKCU "${UNINST_KEY}"

        ; 数据有可能落在个人目录里的这一级（安装目录被安全软件挡着写时会自动退到
        ; 这里）。卸载程序**不碰**它：那里可能有别的工具的数据，删错的代价远大于
        ; 多留一个目录；只把位置说清楚，用户想清自己清。
        ;
        ; 个人目录在 NSIS 里叫 $PROFILE —— NSIS 没有 $HOME：写成 $HOME 时 makensis 只
        ; 给一句 warning 6000「unknown variable」然后当空串处理，安装包照样出得来、
        ; 卸载照样成功，但那句「数据还留在个人目录」永远不打印（2026-11-12 云端日志
        ; 里就是这么一条静默的 warning）。$PROFILE 万一为空（服务上下文、老系统）就
        ; 退回注册表里的 USERPROFILE，两句都是同一个口径：运行期用的是 Path.home()。
        StrCpy $8 "$PROFILE"
        StrCmp $8 "" use_reg_profile check_personal_data
        use_reg_profile:
            ReadRegStr $8 HKCU "Volatile Environment" "USERPROFILE"
        check_personal_data:
        IfFileExists "$8\.b2bcollect\*.*" 0 no_personal_data
            DetailPrint "个人目录里还留着 $8\.b2bcollect（本工具与它共用，卸载未删除）"
        no_personal_data:
        DetailPrint "卸载完成"
SectionEnd
