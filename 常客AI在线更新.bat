@echo off
setlocal
set "UPDATER_DIR=%LOCALAPPDATA%\ChangkeAI"
set "UPDATER=%UPDATER_DIR%\常客AI在线更新器.exe"
if not exist "%UPDATER_DIR%" mkdir "%UPDATER_DIR%" >nul 2>&1

echo 正在获取常客AI在线更新器...
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -UseBasicParsing -Uri 'https://raw.githubusercontent.com/WenRan007/-aI-/main/%E5%B8%B8%E5%AE%A2AI%E5%9C%A8%E7%BA%BF%E6%9B%B4%E6%96%B0%E5%99%A8.exe' -OutFile ([Environment]::ExpandEnvironmentVariables('%UPDATER%'))"
if not exist "%UPDATER%" (
  echo 获取在线更新器失败，请检查网络连接。
  pause
  exit /b 1
)

if exist "%~dp0_internal" set "CHANGKE_APP_DIR=%~dp0"
start "常客AI在线更新器" "%UPDATER%" --auto
exit /b 0
