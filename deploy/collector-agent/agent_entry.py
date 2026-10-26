"""PyInstaller-Entry: baut eine Python-freie `automato-collector.exe` (ADR 0008).

Alternative zum venv+Wheel, wenn auf der Windows-Station kein Python installiert werden
soll. Build (auf einer Windows-Build-Maschine mit Python + PyInstaller):

    pip install . pyinstaller
    pyinstaller --onefile -n automato-collector deploy/collector-agent/agent_entry.py

Die erzeugte `dist/automato-collector.exe` verhaelt sich wie das Konsolen-Script
(`--poll` / `--run-id` / `--check`).
"""

import sys

from collectors.run import main

if __name__ == "__main__":
    sys.exit(main())
