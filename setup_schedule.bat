@echo off
REM 注册"午盘+尾盘"每交易日定时任务到 Windows 计划任务
REM 需以管理员身份运行（仅首次）
setlocal
set TASK_NOON=StockMonitor_Noon
set TASK_TAIL=StockMonitor_Tail
set BAT=D:\zcodexiangm\feishu-stock-monitor\run_intraday.bat

echo [1/2] 注册午盘任务（交易日 11:35）...
schtasks /create /tn "%TASK_NOON%" /tr "\"%BAT%\" noon" /sc weekly /d MON,TUE,WED,THU,FRI /st 11:35 /f
if %errorlevel% neq 0 echo   失败，请确认以管理员身份运行

echo [2/2] 注册尾盘任务（交易日 14:50）...
schtasks /create /tn "%TASK_TAIL%" /tr "\"%BAT%\" close" /sc weekly /d MON,TUE,WED,THU,FRI /st 14:50 /f
if %errorlevel% neq 0 echo   失败，请确认以管理员身份运行

echo.
echo 已注册。查看：
schtasks /query /tn "%TASK_NOON%" /fo LIST | findstr /i "任务名 TaskName 下次运行 Next"
schtasks /query /tn "%TASK_TAIL%" /fo LIST | findstr /i "任务名 TaskName 下次运行 Next"
echo.
echo 删除任务：schtasks /delete /tn "%TASK_NOON%" /f  ^&  schtasks /delete /tn "%TASK_TAIL%" /f
endlocal
pause
