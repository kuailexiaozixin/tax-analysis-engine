@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo  统一测试门禁 — 财税政策搜索引擎
echo ============================================
echo.
echo  默认只跑离线组（不联网，几秒出结果）
echo  加 --online 可带上联网 e2e 组
echo.

where python >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [错误] 未找到 Python，请先安装 Python 3
    pause
    exit /b 1
)

python "tests\run_all.py" %*
set RC=%ERRORLEVEL%

echo.
if %RC% equ 0 (
    echo [通过] 全部测试通过
) else (
    echo [失败] 有测试未通过，请回看上面的 FAIL 项
)
echo.
pause
exit /b %RC%
