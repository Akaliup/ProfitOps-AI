import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_synthetic_replay_eval_passes_offline():
    result = subprocess.run(
        [sys.executable, str(ROOT / "backend" / "evals" / "replay.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"passed": true' in result.stdout
    assert '"network_calls": 0' in result.stdout
