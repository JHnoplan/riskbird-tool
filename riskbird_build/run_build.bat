@echo off
echo Starting build...
python build.py
if errorlevel 1 (
    echo Build failed. Please check the error messages above.
    pause
)
