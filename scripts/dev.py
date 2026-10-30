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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-install", action="store_true", help="启动已安装的环境")
    parser.add_argument("--api-port", type=int, default=8010)
    parser.add_argument("--web-port", type=int, default=5180)
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
        backend = subprocess.Popen([str(executable), "-m", "uvicorn", "app.main:app", "--app-dir", "backend", "--host", "127.0.0.1", "--port", str(args.api_port)], cwd=ROOT)
        processes.append(backend)
        web_environment = {**os.environ, "PROFITOPS_WEB_PORT": str(args.web_port), "PROFITOPS_API_TARGET": f"http://127.0.0.1:{args.api_port}"}
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
