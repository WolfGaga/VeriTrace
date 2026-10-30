"""Build the static project page and a recorded replay of the production UI."""

import argparse
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".site"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-frontend",
        action="store_true",
        help="Reuse an existing local frontend build",
    )
    options = parser.parse_args()
    assert OUTPUT.resolve().is_relative_to(ROOT.resolve())
    shutil.copytree(ROOT / "website", OUTPUT, dirs_exist_ok=True)
    shutil.copytree(ROOT / "docs/assets", OUTPUT / "assets", dirs_exist_ok=True)
    app = OUTPUT / "demo/app"
    if not options.skip_frontend:
        npm = shutil.which("npm.cmd") or shutil.which("npm")
        if not npm:
            raise SystemExit("Install Node.js and npm first.")
        subprocess.run(
            [
                npm,
                "run",
                "build",
                "--",
                "--base=./",
                "--outDir",
                str(app),
                "--emptyOutDir",
            ],
            cwd=ROOT / "VERITRACE/frontend/React",
            check=True,
        )
    index = app / "index.html"
    html = index.read_text(encoding="utf-8")
    bridge = '<script src="../replay.js"></script>'
    if bridge not in html:
        html = html.replace("<head>", "<head>\n    " + bridge, 1)
    index.write_text(html, encoding="utf-8")
    (OUTPUT / ".nojekyll").touch()
    print(f"Site ready: {OUTPUT}")


if __name__ == "__main__":
    main()
