; ═══════════════════════════════════════════════════════
;  美亚风鸟企业数据采集工具 - NSIS 安装程序脚本
;  需要先安装 NSIS: https://nsis.sourceforge.io/Download
; ═══════════════════════════════════════════════════════

!include "MUI2.nsh"
!include "FileFunc.nsh"
!include "WordFunc.nsh"

; ── 基本信息 ──
!define APP_NAME "美亚风鸟企业数据采集工具"
!define APP_VERSION "1.0.0"
!define APP_PUBLISHER "RiskBird Tool"
!define APP_EXE "美亚风鸟采集工具.exe"

Name "${APP_NAME} v${APP_VERSION}"
OutFile "setup_美亚风鸟采集工具.exe"
InstallDir "$PROGRAMFILES64\美亚风鸟采集工具"
InstallDirRegKey HKLM "Software\${APP_NAME}" ""
RequestExecutionLevel admin

; ── 界面设置 ──
!define MUI_ABORTWARNING
!define MUI_ICON ""
!define MUI_UNICON ""
!define MUI_WELCOMEFINISHPAGE_BITMAP ""
!define MUI_HEADERIMAGE
!define MUI_HEADERIMAGE_BITMAP ""
!define MUI_HEADERIMAGE_UNBITMAP ""

; 安装页面
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "license.txt"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

; 卸载页面
!insertmacro MUI_UNPAGE_WELCOME
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_UNPAGE_FINISH

!insertmacro MUI_LANGUAGE "SimpChinese"

; ── 安装 ──
Section "主程序" SEC_MAIN
    SectionIn RO

    ; 设置输出路径
    SetOutPath "$INSTDIR"

    ; 复制主程序
    File "release\${APP_EXE}"

    ; 复制使用说明
    File "release\使用说明.txt"

    ; 创建桌面快捷方式
    CreateShortCut "$DESKTOP\美亚风鸟采集工具.lnk" "$INSTDIR\${APP_EXE}"
    CreateShortCut "$SMPROGRAMS\美亚风鸟采集工具.lnk" "$INSTDIR\${APP_EXE}"

    ; 创建开始菜单文件夹
    CreateDirectory "$SMPROGRAMS\美亚风鸟采集工具"
    CreateShortCut "$SMPROGRAMS\美亚风鸟采集工具\美亚风鸟采集工具.lnk" "$INSTDIR\${APP_EXE}"
    CreateShortCut "$SMPROGRAMS\美亚风鸟采集工具\卸载.lnk" "$INSTDIR\uninst.exe"
    CreateShortCut "$SMPROGRAMS\美亚风鸟采集工具\使用说明.lnk" "$INSTDIR\使用说明.txt"

    ; 写入注册表（用于卸载）
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" \
                   "DisplayName" "${APP_NAME}"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" \
                   "UninstallString" "$INSTDIR\uninst.exe"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" \
                   "DisplayIcon" "$INSTDIR\${APP_EXE}"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" \
                   "DisplayVersion" "${APP_VERSION}"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" \
                   "Publisher" "${APP_PUBLISHER}"

    ; 写入安装路径到注册表
    WriteRegStr HKLM "Software\${APP_NAME}" "" $INSTDIR

    ; 创建卸载程序
    WriteUninstaller "$INSTDIR\uninst.exe"

SectionEnd

; ── 可选：创建 Chrome 快捷方式 ──
Section "检查 Chrome 浏览器" SEC_CHROME
    ; 检查 Chrome 是否已安装
    IfFileExists "$PROGRAMFILES64\Google\Chrome\Application\chrome.exe" chrome_found chrome_not_found

    chrome_found:
        DetailPrint "检测到 Google Chrome 已安装"
        Goto chrome_done

    chrome_not_found:
        DetailPrint "未检测到 Google Chrome"
        MessageBox MB_YESNO "未检测到 Google Chrome 浏览器！$\n$\n本工具需要 Chrome 浏览器才能运行。$\n是否现在打开 Chrome 下载页面？" \
                          IDNO chrome_done
        ExecShell "open" "https://www.google.com/chrome/"

    chrome_done:
SectionEnd

; ── 卸载 ──
Section "Uninstall"
    ; 删除文件
    Delete "$INSTDIR\${APP_EXE}"
    Delete "$INSTDIR\使用说明.txt"
    Delete "$INSTDIR\uninst.exe"

    ; 删除快捷方式
    Delete "$DESKTOP\美亚风鸟采集工具.lnk"
    Delete "$SMPROGRAMS\美亚风鸟采集工具.lnk"
    Delete "$SMPROGRAMS\美亚风鸟采集工具\美亚风鸟采集工具.lnk"
    Delete "$SMPROGRAMS\美亚风鸟采集工具\卸载.lnk"
    Delete "$SMPROGRAMS\美亚风鸟采集工具\使用说明.lnk"
    RMDir "$SMPROGRAMS\美亚风鸟采集工具"

    ; 删除注册表
    DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}"
    DeleteRegKey HKLM "Software\${APP_NAME}"

    ; 询问是否删除用户数据
    MessageBox MB_YESNO "是否同时删除登录数据和采集记录？$\n$\n（选择"是"将清除所有登录信息）" IDNO skip_data
        RMDir /r "$INSTDIR\riskbird_data"
    skip_data:

    ; 删除安装目录（如果为空）
    RMDir "$INSTDIR"

    DetailPrint "卸载完成！"
SectionEnd
