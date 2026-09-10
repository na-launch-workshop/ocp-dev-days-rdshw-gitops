import hashlib
import hmac
import json
import os
import secrets
import subprocess
import time
from collections import defaultdict
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from threading import Lock

from anthropic import AnthropicVertex, beta_tool


GCP_PROJECT_ID = os.environ["GCP_PROJECT_ID"]
GCP_REGION = os.environ.get("CLOUD_ML_REGION", "us-east5")
SANDBOX_WORKDIR = Path(os.environ.get("SANDBOX_WORKDIR", "/tmp/sandbox"))
EXEC_TIMEOUT = int(os.environ.get("EXEC_TIMEOUT_SECONDS", "30"))
MAX_OUTPUT_BYTES = 64 * 1024
TOKEN_TTL_SECONDS = int(os.environ.get("TOKEN_TTL_SECONDS", "14400"))  # 4 hours
SIGNING_KEY = os.environ.get("TOKEN_SIGNING_KEY", secrets.token_hex(32))
MAX_REQUESTS_PER_HOUR = int(os.environ.get("MAX_REQUESTS_PER_HOUR", "30"))

SANDBOX_WORKDIR.mkdir(parents=True, exist_ok=True)

client = AnthropicVertex(project_id=GCP_PROJECT_ID, region=GCP_REGION)

rate_lock = Lock()
rate_counters: dict[str, list[float]] = defaultdict(list)


# --- Token management ---

def create_token(username: str) -> dict:
    issued_at = int(time.time())
    expires_at = issued_at + TOKEN_TTL_SECONDS
    payload = json.dumps({"sub": username, "iat": issued_at, "exp": expires_at})
    sig = hmac.new(SIGNING_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return {
        "token": f"{payload}|{sig}",
        "username": username,
        "expires_at": expires_at,
    }


def validate_token(token: str) -> str | None:
    try:
        payload_str, sig = token.rsplit("|", 1)
        expected = hmac.new(
            SIGNING_KEY.encode(), payload_str.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(payload_str)
        if payload["exp"] < time.time():
            return None
        return payload["sub"]
    except Exception:
        return None


def check_rate_limit(username: str) -> bool:
    now = time.time()
    cutoff = now - 3600
    with rate_lock:
        rate_counters[username] = [
            t for t in rate_counters[username] if t > cutoff
        ]
        if len(rate_counters[username]) >= MAX_REQUESTS_PER_HOUR:
            return False
        rate_counters[username].append(now)
        return True


# --- Sandboxed tools (per-user workdir) ---

def _user_workdir(username: str) -> Path:
    d = SANDBOX_WORKDIR / username
    d.mkdir(parents=True, exist_ok=True)
    return d


def make_tools(username: str):
    workdir = _user_workdir(username)

    @beta_tool
    def execute_code(language: str, code: str) -> str:
        """Execute code in the sandboxed environment and return the output.

        Args:
            language: Programming language — "python", "bash", or "javascript".
            code: The source code to execute.
        """
        runners = {
            "python": ["python3", "-c"],
            "bash": ["bash", "-c"],
            "javascript": ["node", "-e"],
        }
        if language not in runners:
            return f"Error: unsupported language '{language}'. Use python, bash, or javascript."
        cmd = runners[language] + [code]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=EXEC_TIMEOUT,
                cwd=str(workdir),
                env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(workdir),
                     "TMPDIR": str(workdir), "LANG": "C.UTF-8"},
            )
        except subprocess.TimeoutExpired:
            return f"Error: execution timed out after {EXEC_TIMEOUT}s"
        parts = []
        if result.stdout:
            parts.append(f"stdout:\n{result.stdout[:MAX_OUTPUT_BYTES]}")
        if result.stderr:
            parts.append(f"stderr:\n{result.stderr[:MAX_OUTPUT_BYTES]}")
        parts.append(f"exit code: {result.returncode}")
        return "\n".join(parts)

    @beta_tool
    def read_file(path: str) -> str:
        """Read a file from the sandbox working directory.

        Args:
            path: Relative path within the sandbox working directory.
        """
        target = (workdir / path).resolve()
        if not str(target).startswith(str(workdir.resolve())):
            return "Error: path traversal denied"
        if not target.is_file():
            return f"Error: file not found: {path}"
        content = target.read_text(errors="replace")
        return content[:MAX_OUTPUT_BYTES] + ("\n... (truncated)" if len(content) > MAX_OUTPUT_BYTES else "")

    @beta_tool
    def write_file(path: str, content: str) -> str:
        """Write content to a file in the sandbox working directory.

        Args:
            path: Relative path within the sandbox working directory.
            content: The file content to write.
        """
        target = (workdir / path).resolve()
        if not str(target).startswith(str(workdir.resolve())):
            return "Error: path traversal denied"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"

    @beta_tool
    def list_files(directory: str = ".") -> str:
        """List files in a directory within the sandbox.

        Args:
            directory: Relative path within the sandbox working directory.
        """
        target = (workdir / directory).resolve()
        if not str(target).startswith(str(workdir.resolve())):
            return "Error: path traversal denied"
        if not target.is_dir():
            return f"Error: not a directory: {directory}"
        entries = sorted(target.iterdir())
        lines = []
        for e in entries[:200]:
            prefix = "d " if e.is_dir() else "f "
            lines.append(prefix + str(e.relative_to(workdir)))
        return "\n".join(lines) if lines else "(empty)"

    return [execute_code, read_file, write_file, list_files]


SYSTEM_PROMPT = """\
You are a code execution agent running inside an isolated Kata Containers \
sandbox on OpenShift. You can execute Python, Bash, and JavaScript code, \
and read/write files within the sandbox working directory.

Constraints:
- No network access except to the Vertex AI API.
- All file operations are confined to your sandbox working directory.
- Code execution has a timeout; long-running processes will be killed.
- The environment is ephemeral — files do not persist between sessions.

When the user asks you to run code, use the execute_code tool. Prefer \
writing files and then executing them for complex tasks."""


# --- HTTP handler ---

class AgentHandler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, body: dict):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length)

    def _authenticate(self) -> str | None:
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return None
        return validate_token(auth[7:])

    def do_GET(self):
        if self.path == "/healthz":
            self._send_json(200, {"status": "ok"})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/token":
            self._handle_token()
        elif self.path == "/run":
            self._handle_run()
        else:
            self._send_json(404, {"error": "not found"})

    def _handle_token(self):
        """Issue a per-user token. Caller must present the OpenShift user header."""
        username = self.headers.get("X-Forwarded-User")
        if not username:
            body = json.loads(self._read_body() or b"{}")
            username = body.get("username")
        if not username:
            self._send_json(400, {"error": "username required (X-Forwarded-User header or JSON body)"})
            return
        token_data = create_token(username)
        print(f"[token] issued for user={username} expires={token_data['expires_at']}")
        self._send_json(200, token_data)

    def _handle_run(self):
        """Execute an agent prompt. Requires a valid Bearer token."""
        username = self._authenticate()
        if not username:
            self._send_json(401, {"error": "invalid or expired token"})
            return
        if not check_rate_limit(username):
            self._send_json(429, {"error": f"rate limit exceeded ({MAX_REQUESTS_PER_HOUR}/hour)"})
            return
        try:
            body = json.loads(self._read_body())
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid JSON"})
            return
        prompt = body.get("prompt", "").strip()
        if not prompt:
            self._send_json(400, {"error": "prompt required"})
            return

        print(f"[run] user={username} prompt={prompt[:80]}...")

        tools = make_tools(username)
        messages = [{"role": "user", "content": prompt}]

        try:
            runner = client.beta.messages.tool_runner(
                model="claude-opus-5",
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                thinking={"type": "adaptive"},
                tools=tools,
                messages=messages,
            )
            output_parts = []
            for message in runner:
                for block in message.content:
                    if block.type == "text":
                        output_parts.append(block.text)
            self._send_json(200, {"user": username, "response": "\n".join(output_parts)})
        except Exception as e:
            print(f"[error] user={username} error={e}")
            self._send_json(500, {"error": str(e)})

    def log_message(self, format, *args):
        pass


def main():
    port = int(os.environ.get("PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), AgentHandler)
    print(f"Gateway listening on :{port}")
    print(f"  GCP project: {GCP_PROJECT_ID}")
    print(f"  Vertex region: {GCP_REGION}")
    print(f"  Token TTL: {TOKEN_TTL_SECONDS}s")
    print(f"  Rate limit: {MAX_REQUESTS_PER_HOUR} req/hour/user")
    server.serve_forever()


if __name__ == "__main__":
    main()
