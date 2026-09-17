@echo off
REM 自选股盘中扫描（午盘/尾盘定时任务的执行入口）
REM 用法: run_intraday.bat noon   |   run_intraday.bat close
setlocal
cd /d "D:\zcodexiangm\feishu-stock-monitor"
set PY=C:\Users\Administrator\miniconda3\python.exe
if "%~1"=="" (set SESSION=noon) else (set SESSION=%~1)
"%PY%" tools\intraday_scan.py --session %SESSION% >> "data\intraday_%SESSION%.log" 2>&1
endlocal
