# EXE Build Guide

This project can be packaged as a standalone Windows desktop app with PyInstaller.

## Recommended first build mode

Use **onedir** first. It is easier to debug and tends to be more reliable for tray apps.

## Build steps

1. Install project requirements:

```powershell
python -m pip install -r requirements.txt
```

2. Run the build script:

```powershell
build_exe.bat
```

3. After a successful build, check:

```text
dist\CivitAITracker\
```

## Notes

- The build uses `--windowed`, so the app starts without a console window.
- If Windows warns about unknown apps, that is expected for unsigned local builds.
- If tray behavior looks different between Python and EXE mode, test the EXE separately because `pystray` can behave a little differently once bundled.
