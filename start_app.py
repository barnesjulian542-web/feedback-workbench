"""Start the local workbench independently of an editor terminal, with logs."""
import argparse
from datetime import datetime
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import ProxyHandler, build_opener


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8501)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    root = Path(__file__).resolve().parent
    python = root / ".venv" / "Scripts" / "python.exe"
    if not python.is_file():
        raise SystemExit("Project .venv is missing")
    url = f"http://127.0.0.1:{args.port}"
    with socket.socket() as sock:
        if sock.connect_ex(("127.0.0.1", args.port)) == 0:
            raise SystemExit(f"Port {args.port} is already in use. Reuse the running app or choose --port 8502.")
    logs = root / "runtime_logs"
    logs.mkdir(exist_ok=True)
    path = logs / (datetime.now().strftime("streamlit-%Y%m%d-%H%M%S") + ".log")
    env = os.environ.copy()
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(name, None)
    env["NO_PROXY"] = env["no_proxy"] = "localhost,127.0.0.1,::1,.aliyuncs.com"
    env["PYTHONIOENCODING"] = "utf-8"
    flags = (subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS) if os.name == "nt" else 0
    with path.open("ab", buffering=0) as log:
        child = subprocess.Popen([str(python), "-m", "streamlit", "run", str(root / "app.py"),
                                  f"--server.port={args.port}"], cwd=root, env=env,
                                 stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                 creationflags=flags, start_new_session=os.name != "nt")
    opener = build_opener(ProxyHandler({}))
    for _ in range(30):
        if child.poll() is not None:
            raise SystemExit(f"Server exited; see {path}")
        try:
            with opener.open(url + "/_stcore/health", timeout=1) as response:
                if response.read() == b"ok":
                    print(f"Ready: {url}\nPID: {child.pid}\nLog: {path}")
                    return
        except OSError:
            pass
        time.sleep(0.5)
    child.terminate()
    raise SystemExit(f"Startup health check failed; see {path}")


if __name__ == "__main__":
    main()
