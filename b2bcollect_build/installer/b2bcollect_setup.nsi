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
