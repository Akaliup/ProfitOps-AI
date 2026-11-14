import importlib.util
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DATABASE = ROOT / "data" / "profitops.db"


def _load_dev():
    spec = importlib.util.spec_from_file_location("profitops_dev", ROOT / "scripts" / "dev.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stamp(path: Path):
    if not path.exists():
        return None
    stat = path.stat()
    return (stat.st_size, stat.st_mtime_ns)


def test_demo_db_flag_points_at_isolated_copy(monkeypatch):
    before = _stamp(RUNTIME_DATABASE)
    dev = _load_dev()
    url = dev.demo_database_url()
    assert url.endswith("/data/demo-copy.db")
    assert not url.endswith("/data/profitops.db")
    monkeypatch.setenv("PROFITOPS_DATABASE_URL", "sqlite:////tmp/profitops-custom.db")
    unchanged = dev.service_environment(False)
    isolated = dev.service_environment(True)
    assert unchanged["PROFITOPS_DATABASE_URL"] == "sqlite:////tmp/profitops-custom.db"
    assert isolated["PROFITOPS_DATABASE_URL"] == url
    assert os.environ["PROFITOPS_DATABASE_URL"] == "sqlite:////tmp/profitops-custom.db"
    assert _stamp(RUNTIME_DATABASE) == before
    assert "--demo-db" in __import__("subprocess").run(
        [__import__("sys").executable, str(ROOT / "scripts" / "dev.py"), "--help"],
        check=True, capture_output=True, text=True,
    ).stdout
