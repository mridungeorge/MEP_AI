"""Make the synthetic/real status of the golden projects impossible to miss in test output."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import release_gate


def pytest_report_header(config):
    synthetic, real = release_gate.counts()
    return [f"golden projects: {synthetic} SYNTHETIC, {real} REAL"
            + ("  (all golden passes below prove the engine only; no release claim is allowed)" if real == 0 else "")]


def pytest_terminal_summary(terminalreporter):
    synthetic, real = release_gate.counts()
    if real == 0:
        terminalreporter.write_sep(
            "=", f"GOLDEN: {synthetic} projects, ALL SYNTHETIC, 0 real: release claims are blocked", yellow=True)
