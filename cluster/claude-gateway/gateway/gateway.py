import hashlib
import hmac
import json
import os
import secrets
import subprocess
import time
import urllib.request
import urllib.parse
import urllib.error
from collections import defaultdict
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from threading import Lock

from anthropic import Anthropic, beta_tool


SANDBOX_WORKDIR = Path(os.environ.get("SANDBOX_WORKDIR", "/tmp/sandbox"))
EXEC_TIMEOUT = int(os.environ.get("EXEC_TIMEOUT_SECONDS", "30"))
MAX_OUTPUT_BYTES = 64 * 1024
TOKEN_TTL_SECONDS = int(os.environ.get("TOKEN_TTL_SECONDS", "14400"))  # 4 hours
SIGNING_KEY = os.environ.get("TOKEN_SIGNING_KEY", secrets.token_hex(32))
MAX_REQUESTS_PER_HOUR = int(os.environ.get("MAX_REQUESTS_PER_HOUR", "30"))
GITLAB_URL = os.environ.get("GITLAB_URL", "").rstrip("/")
GITLAB_TOKEN = os.environ.get("GITLAB_TOKEN", "")
GITLAB_AUTH_USER = os.environ.get("GITLAB_AUTH_USER", "root")

SANDBOX_WORKDIR.mkdir(parents=True, exist_ok=True)

client = Anthropic()

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


# --- Sandbox helpers ---

def _user_workdir(username: str) -> Path:
    d = SANDBOX_WORKDIR / username
    d.mkdir(parents=True, exist_ok=True)
    return d


def _git_env(workdir: Path, username: str) -> dict:
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(workdir),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": username,
        "GIT_AUTHOR_EMAIL": f"{username}@lab.local",
        "GIT_COMMITTER_NAME": username,
        "GIT_COMMITTER_EMAIL": f"{username}@lab.local",
        "LANG": "C.UTF-8",
    }


def _run_git(args: list, cwd: Path, workdir: Path, username: str) -> tuple:
    try:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True, text=True, timeout=60,
            cwd=str(cwd), env=_git_env(workdir, username),
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return 1, "", "git operation timed out"


def _active_repo_path(workdir: Path) -> Path | None:
    meta = workdir / ".active_repo"
    if not meta.exists():
        return None
    repo_name = meta.read_text().strip()
    repo_path = workdir / repo_name
    return repo_path if repo_path.is_dir() else None


def _active_branch(workdir: Path) -> str | None:
    meta = workdir / ".active_branch"
    return meta.read_text().strip() if meta.exists() else None


def _auth_url(repo_path: str) -> str:
    return f"{GITLAB_URL}/{repo_path}.git".replace(
        "https://", f"https://{GITLAB_AUTH_USER}:{GITLAB_TOKEN}@"
    )


def _gitlab_api(method: str, path: str, body: dict | None = None) -> dict:
    url = f"{GITLAB_URL}/api/v4{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "PRIVATE-TOKEN": GITLAB_TOKEN,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode()[:500]}"}
    except Exception as e:
        return {"error": str(e)}


# --- Tool factory (per-request, closures over username/workdir) ---

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
        active = _active_repo_path(workdir)
        cwd = active if active else workdir
        cmd = runners[language] + [code]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=EXEC_TIMEOUT,
                cwd=str(cwd),
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

    @beta_tool
    def git_clone(repo_name: str) -> str:
        """Clone a GitLab repository into the sandbox and create a session branch.

        Must be called before any other git_ tools. Creates a unique session branch
        (ai/<username>/<timestamp>) so your work is isolated from main.

        Args:
            repo_name: Repository name in the user's GitLab namespace,
                       e.g. "workshop-python-microservices".
        """
        if not GITLAB_URL or not GITLAB_TOKEN:
            return "Error: GitLab not configured on this gateway."

        repo_path = workdir / repo_name
        if repo_path.exists():
            branch = _active_branch(workdir) or "unknown"
            return f"Already cloned at {repo_name}/  (session branch: {branch})"

        try:
            result = subprocess.run(
                ["git", "clone", _auth_url(f"{username}/{repo_name}"), str(repo_path)],
                capture_output=True, text=True, timeout=120,
                cwd=str(workdir), env=_git_env(workdir, username),
            )
        except subprocess.TimeoutExpired:
            return "Error: git clone timed out after 120s"

        if result.returncode != 0:
            return f"Error: git clone failed\n{result.stderr.replace(GITLAB_TOKEN, '***')}"

        branch = f"ai/{username}/{int(time.time())}"
        rc, _, err = _run_git(["checkout", "-b", branch], repo_path, workdir, username)
        if rc != 0:
            return f"Cloned but failed to create session branch: {err}"

        (workdir / ".active_repo").write_text(repo_name)
        (workdir / ".active_branch").write_text(branch)

        return f"Cloned {repo_name}/\nSession branch: {branch}\nReady — use read_file/write_file to explore and edit."

    @beta_tool
    def git_status() -> str:
        """Show uncommitted changes in the active repository."""
        repo = _active_repo_path(workdir)
        if not repo:
            return "Error: no repo cloned. Use git_clone first."
        rc, out, err = _run_git(["status", "--short"], repo, workdir, username)
        if rc != 0:
            return f"Error: {err}"
        return out.strip() or "(working tree clean)"

    @beta_tool
    def git_commit(message: str) -> str:
        """Stage all changes and create a commit on the session branch.

        Args:
            message: Commit message describing what changed and why.
        """
        repo = _active_repo_path(workdir)
        if not repo:
            return "Error: no repo cloned. Use git_clone first."
        rc, _, err = _run_git(["add", "-A"], repo, workdir, username)
        if rc != 0:
            return f"Error: git add failed\n{err}"
        rc, out, err = _run_git(["commit", "-m", message], repo, workdir, username)
        if rc != 0:
            return f"Error: git commit failed\n{err}"
        return out.strip() or "Committed."

    @beta_tool
    def git_push() -> str:
        """Push the session branch to GitLab."""
        if not GITLAB_TOKEN:
            return "Error: GitLab not configured on this gateway."
        repo = _active_repo_path(workdir)
        branch = _active_branch(workdir)
        if not repo or not branch:
            return "Error: no repo cloned. Use git_clone first."

        _run_git(
            ["remote", "set-url", "origin", _auth_url(f"{username}/{repo.name}")],
            repo, workdir, username,
        )
        rc, _, err = _run_git(["push", "-u", "origin", branch], repo, workdir, username)
        if rc != 0:
            return f"Error: git push failed\n{err.replace(GITLAB_TOKEN, '***')}"
        return f"Pushed to origin/{branch}"

    @beta_tool
    def git_create_mr(title: str, description: str, target_branch: str = "main") -> str:
        """Create a GitLab Merge Request from the session branch.

        Only call this when the user explicitly asks to open an MR or pull request.
        The MR is created as a draft. Returns the MR URL.

        Args:
            title: MR title.
            description: MR description explaining what changed and why.
            target_branch: Branch to merge into (default: "main").
        """
        if not GITLAB_TOKEN:
            return "Error: GitLab not configured on this gateway."
        branch = _active_branch(workdir)
        repo = _active_repo_path(workdir)
        if not branch or not repo:
            return "Error: no active session. Clone and push changes first."

        project_path = urllib.parse.quote(f"{username}/{repo.name}", safe="")
        result = _gitlab_api(
            "POST",
            f"/projects/{project_path}/merge_requests",
            {
                "source_branch": branch,
                "target_branch": target_branch,
                "title": title,
                "description": description,
                "remove_source_branch": False,
                "draft": True,
            },
        )

        if "error" in result:
            return f"Error creating MR: {result['error']}"

        mr_url = result.get("web_url", "")
        mr_iid = result.get("iid", "?")
        return f"Draft MR !{mr_iid} opened: {mr_url}"

    return [execute_code, read_file, write_file, list_files,
            git_clone, git_status, git_commit, git_push, git_create_mr]


SYSTEM_PROMPT = """\
You are a code agent running inside an isolated Kata Containers sandbox on OpenShift. \
You have tools to execute code, read/write files, and interact with GitLab repositories.

Workflow:
1. Use git_clone to clone the user's GitLab repo into your sandbox.
2. Explore the repo with list_files and read_file. Edit with write_file.
3. Run code with execute_code to test changes (runs from the repo root when a repo is active).
4. Use git_commit to save progress — commit often with clear messages.
5. Use git_push to push the session branch to GitLab when the user wants to share work.
6. Only use git_create_mr when the user explicitly asks to open a Merge Request.

Constraints:
- Each session gets its own branch (ai/<username>/<timestamp>). Never push to main/master.
- No network access except to the Anthropic API and the configured GitLab instance.
- All file paths are relative to the sandbox working directory.
- Code execution has a 30-second timeout.
- The sandbox is ephemeral — data does not persist between sessions.

Write clear commit messages. Prefer writing files and executing them over inline code \
for anything longer than a few lines."""


# --- HTTP handler ---

class AgentHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

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
        try:
            if self.path == "/healthz":
                self._send_json(200, {"status": "ok"})
            else:
                self._send_json(404, {"error": "not found"})
        except Exception as e:
            print(f"[error] GET {self.path}: {e}", flush=True)
            self._send_json(500, {"error": str(e)})

    def do_POST(self):
        try:
            if self.path == "/token":
                self._handle_token()
            elif self.path == "/run":
                self._handle_run()
            else:
                self._send_json(404, {"error": "not found"})
        except Exception as e:
            print(f"[error] POST {self.path}: {e}", flush=True)
            self._send_json(500, {"error": str(e)})

    def _handle_token(self):
        username = self.headers.get("X-Forwarded-User")
        if not username:
            body = json.loads(self._read_body() or b"{}")
            username = body.get("username")
        if not username:
            self._send_json(400, {"error": "username required (X-Forwarded-User header or JSON body)"})
            return
        token_data = create_token(username)
        print(f"[token] issued for user={username} expires={token_data['expires_at']}", flush=True)
        self._send_json(200, token_data)

    def _handle_run(self):
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

        print(f"[run] user={username} prompt={prompt[:80]}...", flush=True)

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
            print(f"[error] user={username} error={e}", flush=True)
            self._send_json(500, {"error": str(e)})

    def log_message(self, format, *args):
        print(f"[http] {self.client_address[0]} {format % args}", flush=True)


def main():
    port = int(os.environ.get("PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), AgentHandler)
    print(f"Gateway listening on :{port}")
    print(f"  GitLab: {GITLAB_URL or '(not configured)'}")
    print(f"  GitLab auth user: {GITLAB_AUTH_USER}")
    print(f"  GitLab token configured: {'yes' if GITLAB_TOKEN else 'no'}")
    print(f"  Token TTL: {TOKEN_TTL_SECONDS}s")
    print(f"  Rate limit: {MAX_REQUESTS_PER_HOUR} req/hour/user", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
