"""Verify the production UI against isolated local API and database instances."""

import argparse
from datetime import datetime
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request


PROJECT = Path(__file__).resolve().parents[2]


def run_case(name, artifacts, browser_name):
    artifacts.mkdir(parents=True, exist_ok=True)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    env = dict(os.environ, PYTHON_DOTENV_DISABLED="1", PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    with (artifacts / "server.log").open("w", encoding="utf-8") as log:
        server = subprocess.Popen(
            [sys.executable, "-B", "-X", "utf8", str(Path(__file__).with_name("browser_server.py")),
             "--port", str(port), "--state-dir", str(artifacts / "state"),
             "--frontend-dist", str(PROJECT / "frontend/React/dist")],
            cwd=PROJECT, env=env, stdout=log, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            for _ in range(60):
                if server.poll() is not None:
                    raise RuntimeError(f"Local API exited; see {artifacts / 'server.log'}")
                try:
                    with opener.open(url + "/api/conversations", timeout=2) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(0.3)
            else:
                raise RuntimeError("Local API did not become ready")
            with (artifacts / "browser.log").open("w", encoding="utf-8") as output:
                result = subprocess.run(
                    [sys.executable, "-B", "-X", "utf8", str(Path(__file__).with_name(f"browser_{name}.py")),
                     "--url", url, "--artifacts", str(artifacts), "--browser", browser_name],
                    cwd=PROJECT, env=env, stdout=output, stderr=subprocess.STDOUT, timeout=240,
                )
            print((artifacts / "browser.log").read_text(encoding="utf-8"), flush=True)
            return result.returncode
        finally:
            if server.poll() is None:
                server.terminate()
            server.wait(timeout=20)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test", choices=("all", "smoke", "controls", "failures", "markup", "progress", "recovery"), default="all")
    parser.add_argument("--browser", choices=("chromium", "firefox", "webkit"), default="chromium")
    parser.add_argument("--artifacts", type=Path, default=Path(__file__).parent / "artifacts")
    args = parser.parse_args()
    run = args.artifacts.resolve() / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    print(f"Browser artifacts: {run}", flush=True)
    if not (PROJECT / "frontend/React/dist/index.html").is_file():
        parser.error("Run npm run build in frontend/React first")
    failures = []
    for name in (("smoke", "controls", "failures", "markup", "progress", "recovery") if args.test == "all" else (args.test,)):
        if run_case(name, run / name, args.browser):
            failures.append(name)
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
