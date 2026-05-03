@echo off
setlocal

pushd "%~dp0"

if not exist "..\fonts" mkdir "..\fonts" || goto :error
if exist "..\fonts\ToneOZQSPinyinKaiTrad.ttf" del /f /q "..\fonts\ToneOZQSPinyinKaiTrad.ttf" || goto :error

python3 build_static_font.py || goto :error
python3 merge_reference_tables.py || goto :error
python3 validate_build.py || goto :error

set "EXIT_CODE=%ERRORLEVEL%"
popd
exit /b %EXIT_CODE%

:error
set "EXIT_CODE=%ERRORLEVEL%"
popd
exit /b %EXIT_CODE%
