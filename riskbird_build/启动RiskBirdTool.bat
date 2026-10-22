@echo off
chcp 65001 >nul
title RiskBirdTool 启动器

echo ============================================
echo   企业数据采集工具 - 启动清理
echo ============================================
echo.

REM 1. 杀掉旧的 RiskBirdTool 进程
echo [1/3] 检查旧进程...
tasklist /FI "IMAGENAME eq RiskBirdTool.exe" /FO CSV /NH 2>nul | findstr /I "RiskBirdTool" >nul
if %errorlevel%==0 (
    echo   发现旧进程，正在关闭...
    taskkill /F /IM RiskBirdTool.exe 2>nul
    timeout /t 2 /nobreak >nul
    echo   旧进程已关闭
) else (
    echo   没有旧进程
)
echo.

REM 2. 清理临时目录
echo [2/3] 清理临时文件...
for /d %%D in ("%TEMP%\_MEI*") do (
    echo   清理: %%D
    rd /s /q "%%D" 2>nul
    if exist "%%D" (
        ren "%%D" "_MEI_old_%%~nxD" 2>nul
        rd /s /q "_MEI_old_%%~nxD" 2>nul
    )
)
echo   清理完成
echo.

REM 3. 启动主程序
echo [3/3] 启动程序...
echo.
start "" "%~dp0RiskBirdTool.exe"
