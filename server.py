"""Serve the chat UI and a key-protected Ollama endpoint on the local machine."""

import argparse
import hmac
import http.client
import json
import os
import secrets
import socket
import stat
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


MODEL = "qwen2.5:1.5b"
CHAT_PATH = "/v1/chat/completions"
MAX_BODY = 32 * 1024
MAX_RESPONSE = 1024 * 1024
INDEX = Path(__file__).resolve().parent / "web" / "index.html"


def posix_key(path):
    if not path.exists():
        # Publish a complete mode-0600 file, even if two servers start at once.
        with tempfile.NamedTemporaryFile(dir=path.parent) as tmp:
            tmp.write((secrets.token_urlsafe(32) + "\n").encode("ascii"))
            tmp.flush()
            try:
                os.link(tmp.name, path)
            except FileExistsError:
                pass
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("Key file must be a private regular file owned by you (chmod 600)")
        if info.st_size > 258:
            raise ValueError("API key file is too large")
        return source.read(258)


def windows_key(path):
    # Windows PowerShell 5.1 provides .NET Framework's ACL-aware FileStream.
    # Paths and secret bytes are supplied separately from the command line.
    command = r"""
$ErrorActionPreference = 'Stop'
$path = $env:LOCAL_QWEN_KEY_PATH
$user = [Security.Principal.WindowsIdentity]::GetCurrent().User
function Assert-PrivateKey($stream) {
    $acl = $stream.GetAccessControl()
    $sid = [Security.Principal.SecurityIdentifier]
    $rules = $acl.GetAccessRules($true, $true, $sid)
    $trusted = @($user.Value, 'S-1-5-18', 'S-1-5-32-544')  # user, SYSTEM, Administrators
    $other = $rules | Where-Object { $_.AccessControlType -eq 'Allow' -and $_.IdentityReference.Value -notin $trusted }
    if ($acl.GetOwner($sid).Value -ne $user.Value -or $rules.Count -eq 0 -or $other) {
        throw 'API key must be owned by the current user and have a private Windows ACL.'
    }
}
if (-not [IO.File]::Exists($path)) {
    $temp = [IO.Path]::Combine([IO.Path]::GetDirectoryName($path), [IO.Path]::GetRandomFileName())
    $acl = [Security.AccessControl.FileSecurity]::new()
    $acl.SetOwner($user)
    $acl.SetAccessRuleProtection($true, $false)
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($user, 'FullControl', 'Allow'))
    $outFile = [IO.FileStream]::new($temp, 'CreateNew', [Security.AccessControl.FileSystemRights]::FullControl,
                                    'None', 4096, 'None', $acl)
    try {
        try {
            Assert-PrivateKey $outFile
            $key = [Text.Encoding]::ASCII.GetBytes([Console]::ReadLine() + [Environment]::NewLine)
            $outFile.Write($key, 0, $key.Length)
        } finally { $outFile.Dispose() }
        try { [IO.File]::Move($temp, $path) }
        catch [IO.IOException] { if (-not [IO.File]::Exists($path)) { throw } }
    } finally { [IO.File]::Delete($temp) }
}
if ([IO.File]::GetAttributes($path) -band [IO.FileAttributes]::ReparsePoint) { throw 'Key cannot be a reparse point.' }
$source = [IO.File]::OpenRead($path)
try {
    Assert-PrivateKey $source
    if ($source.Length -gt 258) { throw 'API key file is too large.' }
    $source.CopyTo([Console]::OpenStandardOutput())
} finally { $source.Dispose() }
"""
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run(
        [str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
        env=dict(os.environ, LOCAL_QWEN_KEY_PATH=str(path.absolute())),
        input=(secrets.token_urlsafe(32) + "\n").encode("ascii"),
        capture_output=True,
    )
    if result.returncode:
        raise ValueError("Cannot open a private Windows key file. Use your own LocalAppData on an ACL-capable volume.")
    return result.stdout


def load_key(path):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    data = windows_key(path) if os.name == "nt" else posix_key(path)
    key = data.removesuffix(b"\n").removesuffix(b"\r")
    if not 32 <= len(key) <= 256 or not all(33 <= char <= 126 for char in key):
        raise ValueError("Key must contain 32 to 256 printable non-space ASCII characters")
    return key


def chat_request(body):
    if not isinstance(body, dict) or body.get("model") != MODEL:
        raise ValueError(f"Use model {MODEL}")
    if body.get("stream", False) is not False:
        raise ValueError("Use stream=false")
    messages = body.get("messages")
    if not isinstance(messages, list) or not 1 <= len(messages) <= 40:
        raise ValueError("Send 1 to 40 messages")
    clean = []
    for item in messages:
        if not isinstance(item, dict) or item.get("role") not in ("system", "user", "assistant"):
            raise ValueError("Each message needs a system, user, or assistant role")
        content = item.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Each message needs non-empty text content")
        clean.append({"role": item["role"], "content": content})
    limit = body.get("max_completion_tokens", body.get("max_tokens", 256))
    if type(limit) is not int or not 1 <= limit <= 512:
        raise ValueError("Use an output limit between 1 and 512 tokens")
    return {"model": MODEL, "messages": clean, "stream": False, "max_tokens": limit}


class Gateway(ThreadingHTTPServer):
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def __init__(self, address, bearer, index):
        super().__init__(address, ChatHandler)
        self.bearer = bearer
        self.index = index
        self.workers = threading.BoundedSemaphore(4)
        self.generation = threading.BoundedSemaphore(1)

    def process_request(self, request, address):
        self.workers.acquire()
        try:
            request.settimeout(10)
            super().process_request(request, address)
        except BaseException:
            self.workers.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.workers.release()


class ChatHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Do not log request targets, credentials or prompts.

    def reply(self, status, data, content_type="application/json; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def fail(self, status, message):
        self.reply(status, json.dumps({"error": {"message": message}}).encode())

    def do_GET(self):
        if self.path != "/":
            return self.fail(404, "Unknown route")
        self.reply(200, self.server.index, "text/html; charset=utf-8")

    def do_POST(self):
        if self.path != CHAT_PATH:
            return self.fail(404, "Unknown route")
        if not hmac.compare_digest(self.headers.get("Authorization", "").encode(), self.server.bearer):
            return self.fail(401, "Invalid API key")
        if self.headers.get_content_type() != "application/json":
            return self.fail(415, "Use application/json")
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") or len(lengths) != 1:
            return self.fail(400, "Provide one Content-Length and no Transfer-Encoding")
        length = lengths[0]
        if not length.isascii() or not length.isdigit():
            return self.fail(400, "Invalid Content-Length")
        if len(length) > 5 or int(length) > MAX_BODY:
            return self.fail(413, "Request exceeds 32 KiB")
        try:
            body = json.loads(self.rfile.read(int(length)).decode("utf-8"))
        except (ValueError, RecursionError):
            return self.fail(400, "Invalid JSON")
        try:
            outgoing = chat_request(body)
        except ValueError as error:
            return self.fail(400, str(error))
        if not self.server.generation.acquire(blocking=False):
            return self.fail(429, "Model busy. Retry shortly")
        upstream = http.client.HTTPConnection("127.0.0.1", 11434, timeout=90)
        try:
            upstream.request("POST", CHAT_PATH, json.dumps(outgoing).encode(), {"Content-Type": "application/json"})
            response = upstream.getresponse()
            if response.status != 200:
                return self.fail(502, "Ollama rejected the request. Check ollama list")
            data = response.read(MAX_RESPONSE + 1)
        except socket.timeout:
            return self.fail(504, "Local model timed out")
        except (OSError, http.client.HTTPException):
            return self.fail(502, "Cannot reach local Ollama")
        finally:
            upstream.close()
            self.server.generation.release()
        if len(data) > MAX_RESPONSE:
            return self.fail(502, "Local response too large")
        self.reply(200, data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default=18435, type=int)
    if os.name == "nt":
        appdata = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData/Local"))
        default_key = appdata / "LocalQwenApi/api-key"
    else:
        default_key = Path.home() / ".config/local-qwen-api/api-key"
    parser.add_argument("--key-file", type=Path, default=os.environ.get("QWEN_API_KEY_FILE") or default_key)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535")
    key_file = args.key_file.expanduser()
    try:
        index = INDEX.read_bytes()
        key = load_key(key_file)
        with Gateway(("127.0.0.1", args.port), b"Bearer " + key, index) as server:
            print(f"Chat UI: http://127.0.0.1:{args.port}/\nAPI key file: {key_file}", flush=True)
            server.serve_forever()
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    if os.name == "nt" and sys.stdout is not None and not sys.stdout.isatty():
        sys.stdout.reconfigure(encoding="utf-8")
    main()
