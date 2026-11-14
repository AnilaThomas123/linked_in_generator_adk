"""Best-effort local notifications; publication remains manual and explicit."""

from __future__ import annotations

import platform
import subprocess


def notify(title: str, message: str) -> bool:
    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.run(["osascript", "-e", f'display notification "{message}" with title "{title}"'], check=True)
        elif system == "Linux":
            subprocess.run(["notify-send", title, message], check=True)
        elif system == "Windows":
            # Uses the built-in Windows PowerShell message fallback; no credentials or network.
            command = f"Add-Type -AssemblyName PresentationFramework; [System.Windows.MessageBox]::Show('{message}','{title}')"
            subprocess.Popen(["powershell", "-NoProfile", "-Command", command])
        else:
            return False
        return True
    except (OSError, subprocess.SubprocessError):
        return False
