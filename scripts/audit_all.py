"""
scripts/audit_all.py
====================
System process & balance auditor: running Money Banque processes + configured wallet balances.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import subprocess

from scripts.deep_audit import print_wallet_balances

PROCESS_MARKERS = ("main.py", "run_prescope_sniper.py")


def running_processes() -> list[str]:
    if os.name == "nt":
        cmd = ["powershell", "-NoProfile", "-Command",
               "Get-CimInstance Win32_Process -Filter \"name = 'python.exe'\" | "
               "ForEach-Object { \"$($_.ProcessId) $($_.CommandLine)\" }"]
    else:
        cmd = ["ps", "-eo", "pid,args"]
    output = subprocess.run(cmd, capture_output=True, text=True).stdout
    return [line.strip() for line in output.splitlines() if any(m in line for m in PROCESS_MARKERS)]


def main() -> None:
    print("Running processes:")
    for line in running_processes() or ["   none"]:
        print(f"   {line}")
    print_wallet_balances()


if __name__ == "__main__":
    main()
