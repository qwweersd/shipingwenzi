@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo   Push this project to GitHub
echo ============================================
echo.
echo  Before running: create an EMPTY repo on github.com.
echo  Do NOT add README, license or .gitignore there.
echo.

echo  Current remotes:
git remote -v
echo.

echo  Example: https://github.com/USER/REPO.git
echo.
set "REPO="
set /p "REPO=Repo URL: "
if not defined REPO (
    echo  Cancelled.
    pause
    exit /b 1
)

echo.
git remote remove origin >nul 2>nul
git remote add origin "%REPO%"
if errorlevel 1 (
    echo  [ERROR] Failed to set remote.
    pause
    exit /b 1
)

git branch -M main
echo  Pushing to %REPO% ...
echo  A browser sign-in window may appear. Complete it to continue.
echo.
git push -u origin main
if errorlevel 1 (
    echo.
    echo  [ERROR] Push failed. Common causes:
    echo    - Not signed in, or no write permission
    echo    - Remote repo is not empty, e.g. created with a README
    echo    - Wrong URL
    pause
    exit /b 1
)

echo.
echo  [OK] Code pushed.
echo.
echo  To build and publish the Windows exe, run:
echo      git tag v1.0.0
echo      git push origin v1.0.0
echo.
echo  Then check the Actions tab and the Releases page.
echo.
pause
