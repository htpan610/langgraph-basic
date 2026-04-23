@echo off
setlocal
uv run python -m nuitka ^
  --onefile ^
  --enable-plugin=pyside6 ^
  --include-data-file=config.toml=config.toml ^
  --windows-console-mode=disable ^
  --output-filename=sewing-layout-agent.exe ^
  main.py
endlocal
