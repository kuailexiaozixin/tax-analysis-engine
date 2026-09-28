@echo off
REM ============================================================
REM 本文件是 GBK(cp936) 编码。中文 Windows 的 cmd 默认代码页就是 936，
REM 用 GBK 存盘，cmd 解析与显示都正常；存成 UTF-8 反而会让中文变成乱码，
REM 甚至把注释行读成命令。改动时请保持 GBK 编码与 CRLF 换行。
REM ============================================================
cd /d "%~dp0"

echo ============================================
echo  规则的起点 — 财税政策搜索引擎（本地版）
echo ============================================
echo.

echo 检查 Python…
where python >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [错误] 未找到 Python，请先安装 Python 3
    pause
    exit /b 1
)

echo 安装 / 检查依赖…
python -m pip install -r requirements.txt -q

REM 端口跟随 tax_server.py：TAX_PORT 没设就是 5080
set PORT=5080
if not "%TAX_PORT%"=="" set PORT=%TAX_PORT%

echo.
echo 启动服务器（端口 %PORT%）…

REM 已经在跑就直接开浏览器，不再起第二个（否则两个进程会撞端口）。
REM ProgressPreference 是为了不让 Invoke-WebRequest 往屏幕上刷进度条。
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; try{ if((Invoke-WebRequest -Uri 'http://127.0.0.1:%PORT%/api/health' -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200){exit 0} }catch{}; exit 1"
if %ERRORLEVEL% equ 0 (
    echo 服务器已经在运行，直接打开浏览器。
    start "" http://127.0.0.1:%PORT%
    goto :done
)

REM 在独立窗口里起服务：日志留在那个窗口，关掉它即停服务。
REM 这里不必套 cmd /k "chcp 65001 && ..."：tax_server.py 启动时会自己把控制台
REM 代码页切成 65001，所以一个最简单的 start 就够，没有嵌套引号要操心。
REM 监听地址跟随 tax_server.py 的 TAX_BIND（默认 127.0.0.1，只本机可访问）。
start "tax-policy-search server" python scripts\tax_server.py

REM 等端口真的就绪再开浏览器。原来这里是先 start 浏览器再起服务，
REM 页面抢在 Flask 前面加载，只会看到"无法访问此网站"。
REM 端口没开时每探测一次约 1.5 秒（连接被拒要等超时），40 次约 1 分钟。
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; $ok=$false; for($i=0;$i -lt 40;$i++){ try{ if((Invoke-WebRequest -Uri 'http://127.0.0.1:%PORT%/api/health' -UseBasicParsing -TimeoutSec 1).StatusCode -eq 200){$ok=$true; break} }catch{}; Start-Sleep -Milliseconds 500 }; if($ok){exit 0}else{exit 1}"
if %ERRORLEVEL% neq 0 (
    echo.
    echo [警告] 服务器约 1 分钟内没有就绪，仍尝试打开浏览器。
    echo        请查看那个 "tax-policy-search server" 窗口里的报错。
    echo.
)

start "" http://127.0.0.1:%PORT%

:done
echo.
echo 服务器运行在独立的 "tax-policy-search server" 窗口里。
echo 停止服务：关闭那个窗口即可。
echo.
pause
