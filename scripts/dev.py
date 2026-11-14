"""One command local startup. Run from any directory; writes only under the project."""
import argparse
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]


def demo_database_url() -> str:
    """Isolated demo file. Never the runtime database in data/profitops.db."""
    return "sqlite:///" + (ROOT / "data" / "demo-copy.db").resolve().as_posix()


def service_environment(demo_db: bool) -> dict[str, str]:
    """Child-process environment. ``--demo-db`` overrides only the database URL."""
    env = os.environ.copy()
    if demo_db:
        env["PROFITOPS_DATABASE_URL"] = demo_database_url()
    return env


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-install", action="store_true", help="启动已安装的环境")
    parser.add_argument("--api-port", type=int, default=8010)
    parser.add_argument("--web-port", type=int, default=5180)
    parser.add_argument("--demo-db", action="store_true", help="使用 data/demo-copy.db，不写入 data/profitops.db")
    args = parser.parse_args()
    for port in (args.api_port, args.web_port):
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                raise SystemExit(f"端口 {port} 已占用，请使用 --api-port / --web-port 指定空闲端口")
    os.chdir(ROOT)
    environment = ROOT / ".venv"
    executable = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if npm is None:
        raise SystemExit("请先安装 Node.js 20.19+ 和 npm")
    if not executable.exists():
        if sys.version_info < (3, 11):
            raise SystemExit("需要 Python 3.11+")
        venv.create(environment, with_pip=True)
    if not args.skip_install:
        subprocess.run([str(executable), "-m", "pip", "install", "-r", "backend/requirements.lock.txt"], check=True)
        subprocess.run([npm, "ci", "--prefix", "frontend", "--registry=https://registry.npmjs.org"], check=True)
    processes = []
    try:
        if args.demo_db:
            (ROOT / "data").mkdir(exist_ok=True)
            print(f"演示数据库（独立文件，不会清空或迁移 data/profitops.db）: {demo_database_url()}", flush=True)
        else:
            print("数据库: 使用 Settings / .env 的 PROFITOPS_DATABASE_URL；未配置时为 data/profitops.db。五分钟演示请改用 --demo-db。", flush=True)
        child_env = service_environment(args.demo_db)
        backend = subprocess.Popen([str(executable), "-m", "uvicorn", "app.main:app", "--app-dir", "backend", "--host", "127.0.0.1", "--port", str(args.api_port)], cwd=ROOT, env=child_env)
        processes.append(backend)
        web_environment = {**child_env, "PROFITOPS_WEB_PORT": str(args.web_port), "PROFITOPS_API_TARGET": f"http://127.0.0.1:{args.api_port}"}
        frontend = subprocess.Popen([npm, "--prefix", "frontend", "run", "dev"], cwd=ROOT, env=web_environment)
        processes.append(frontend)
        print(f"\nProfitOps AI: http://127.0.0.1:{args.web_port} | API: http://127.0.0.1:{args.api_port}/docs\nCtrl+C 停止服务", flush=True)
        while all(process.poll() is None for process in processes):
            try:
                backend.wait(timeout=1)
            except subprocess.TimeoutExpired:
                continue
    except KeyboardInterrupt:
        print("\n正在停止 ProfitOps AI…", flush=True)
    finally:
        failed = any(process.poll() not in (None, 0) for process in processes)
        for process in reversed(processes):
            if process.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
                else:
                    process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        if failed:
            raise SystemExit("服务启动失败，详情见上方错误")


if __name__ == "__main__":
    main()
