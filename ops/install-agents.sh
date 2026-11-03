#!/bin/sh
# Install the gateway and an existing named tunnel for the current macOS user.
set -eu
if [ "$#" -ne 1 ]; then
    echo 'Usage: sh ops/install-agents.sh "/absolute/path/to/config.yml"' >&2
    exit 2
fi
if [ "$(uname -s)" != Darwin ] || [ "$(id -u)" -eq 0 ]; then
    echo "Run as your regular user on macOS." >&2
    exit 1
fi
project=$(CDPATH= cd -P "$(dirname "$0")/.." && pwd)
exec python3 - "$project" "$1" <<'PY'
import fcntl
import os
from pathlib import Path
import plistlib
import shutil
import socket
import subprocess
import sys
import tempfile
import time

root = Path(sys.argv[1])
config = Path(sys.argv[2])
if not config.is_absolute() or not config.is_file():
    sys.exit("Supply the absolute path of an existing named tunnel configuration.")
if sys.version_info < (3, 9):
    sys.exit("Python 3.9 or later is required.")
cloudflared = shutil.which("cloudflared")
if not cloudflared:
    sys.exit("Install cloudflared and make it available on PATH.")
subprocess.run([cloudflared, "tunnel", "--config", str(config), "ingress", "validate"], check=True)

home = Path.home()
app = home / "Library/Application Support/local-qwen-api"
agents = home / "Library/LaunchAgents"
logs = home / "Library/Logs/local-qwen-api"
os.umask(0o077)
for folder in (app / "web", agents, logs):
    folder.mkdir(parents=True, exist_ok=True)

# Serialize installers and prepare all managed files before reloading jobs.
lock = (app / ".install.lock").open("a")
fcntl.flock(lock, fcntl.LOCK_EX)
labels = ("com.localqwen.gateway", "com.localqwen.tunnel")
paths = {
    "python3": sys.executable, "cloudflared": os.path.abspath(cloudflared),
    "server": str(app / "server.py"), "config": str(config),
    "key_file": str(home / ".config/local-qwen-api/api-key"),
    "gateway_log": str(logs / "gateway.log"), "tunnel_log": str(logs / "tunnel.log"),
}
files = {app / name: (root / name).read_bytes() for name in ("server.py", "web/index.html")}
for label in labels:
    job = plistlib.loads((root / "ops" / (label + ".plist.example")).read_bytes())
    job["ProgramArguments"] = [arg.format_map(paths) for arg in job["ProgramArguments"]]
    for field in ("StandardOutPath", "StandardErrorPath"):
        job[field] = job[field].format_map(paths)
    files[agents / (label + ".plist")] = plistlib.dumps(job)
for path, data in files.items():
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as staged:
        staged.write(data)
    os.replace(staged.name, path)

domain = "gui/" + str(os.getuid())
for label in reversed(labels):
    service = domain + "/" + label
    if subprocess.run(["launchctl", "print", service], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        subprocess.run(["launchctl", "bootout", service], check=True)

# Do not create an Internet route to another service occupying our gateway port.
for _ in range(20):
    with socket.socket() as client:
        client.settimeout(0.25)
        if client.connect_ex(("127.0.0.1", 18435)) != 0:
            break
    time.sleep(0.25)
else:
    sys.exit("Port 18435 is in use. Stop your manually started gateway and rerun the installer.")

for label in labels:
    subprocess.run(["launchctl", "enable", domain + "/" + label], check=True)
    subprocess.run(["launchctl", "bootstrap", domain, str(agents / (label + ".plist"))], check=True)
print("Installed both login agents. Check their logs in", logs)
PY
