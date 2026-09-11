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
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from threading import Lock

from anthropic import Anthropic


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
rate_counters = defaultdict(list)

history_lock = Lock()
conversation_histories = {}
MAX_HISTORY_TURNS = 10


# --- Token management ---

def create_token(username):
    issued_at = int(time.time())
    expires_at = issued_at + TOKEN_TTL_SECONDS
    payload = json.dumps({"sub": username, "iat": issued_at, "exp": expires_at})
    sig = hmac.new(SIGNING_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return {"token": f"{payload}|{sig}", "username": username, "expires_at": expires_at}


def validate_token(token):
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


def check_rate_limit(username):
    now = time.time()
    cutoff = now - 3600
    with rate_lock:
        rate_counters[username] = [t for t in rate_counters[username] if t > cutoff]
        if len(rate_counters[username]) >= MAX_REQUESTS_PER_HOUR:
            return False
        rate_counters[username].append(now)
        return True


# --- Sandbox helpers ---

def _user_workdir(username):
    d = SANDBOX_WORKDIR / username
    d.mkdir(parents=True, exist_ok=True)
    return d


def _git_env(workdir, username):
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


def _run_git(args, cwd, workdir, username):
    try:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True, text=True, timeout=60,
            cwd=str(cwd), env=_git_env(workdir, username),
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return 1, "", "git operation timed out"


def _active_repo_path(workdir):
    meta = workdir / ".active_repo"
    if not meta.exists():
        return None
    repo_name = meta.read_text().strip()
    repo_path = workdir / repo_name
    return repo_path if repo_path.is_dir() else None


def _active_branch(workdir):
    meta = workdir / ".active_branch"
    return meta.read_text().strip() if meta.exists() else None


def _auth_url(repo_path):
    return f"{GITLAB_URL}/{repo_path}.git".replace(
        "https://", f"https://{GITLAB_AUTH_USER}:{GITLAB_TOKEN}@"
    )


def _gitlab_api(method, path, body=None):
    url = f"{GITLAB_URL}/api/v4{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"PRIVATE-TOKEN": GITLAB_TOKEN, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode()[:500]}"}
    except Exception as e:
        return {"error": str(e)}


# --- Tool factory ---

def make_tools(username):
    workdir = _user_workdir(username)

    def execute_code(language, code):
        runners = {"python": ["python3", "-c"], "bash": ["bash", "-c"], "javascript": ["node", "-e"]}
        if language not in runners:
            return f"Error: unsupported language '{language}'. Use python, bash, or javascript."
        active = _active_repo_path(workdir)
        cwd = active if active else workdir
        try:
            result = subprocess.run(
                runners[language] + [code], capture_output=True, text=True,
                timeout=EXEC_TIMEOUT, cwd=str(cwd),
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

    def read_file(path):
        target = (workdir / path).resolve()
        if not str(target).startswith(str(workdir.resolve())):
            return "Error: path traversal denied"
        if not target.is_file():
            return f"Error: file not found: {path}"
        content = target.read_text(errors="replace")
        return content[:MAX_OUTPUT_BYTES] + ("\n... (truncated)" if len(content) > MAX_OUTPUT_BYTES else "")

    def write_file(path, content):
        target = (workdir / path).resolve()
        if not str(target).startswith(str(workdir.resolve())):
            return "Error: path traversal denied"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"

    def list_files(directory="."):
        target = (workdir / directory).resolve()
        if not str(target).startswith(str(workdir.resolve())):
            return "Error: path traversal denied"
        if not target.is_dir():
            return f"Error: not a directory: {directory}"
        entries = sorted(target.iterdir())
        lines = []
        for e in entries[:200]:
            lines.append(("d " if e.is_dir() else "f ") + str(e.relative_to(workdir)))
        return "\n".join(lines) if lines else "(empty)"

    def git_clone(repo_name):
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

    def git_status():
        repo = _active_repo_path(workdir)
        if not repo:
            return "Error: no repo cloned. Use git_clone first."
        rc, out, err = _run_git(["status", "--short"], repo, workdir, username)
        return out.strip() or "(working tree clean)"

    def git_commit(message):
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

    def git_push():
        if not GITLAB_TOKEN:
            return "Error: GitLab not configured on this gateway."
        repo = _active_repo_path(workdir)
        branch = _active_branch(workdir)
        if not repo or not branch:
            return "Error: no repo cloned. Use git_clone first."
        _run_git(["remote", "set-url", "origin", _auth_url(f"{username}/{repo.name}")],
                 repo, workdir, username)
        rc, _, err = _run_git(["push", "-u", "origin", branch], repo, workdir, username)
        if rc != 0:
            return f"Error: git push failed\n{err.replace(GITLAB_TOKEN, '***')}"
        return f"Pushed to origin/{branch}"

    def git_create_mr(title, description, target_branch="main"):
        if not GITLAB_TOKEN:
            return "Error: GitLab not configured on this gateway."
        branch = _active_branch(workdir)
        repo = _active_repo_path(workdir)
        if not branch or not repo:
            return "Error: no active session. Clone and push changes first."
        project_path = urllib.parse.quote(f"{username}/{repo.name}", safe="")
        result = _gitlab_api("POST", f"/projects/{project_path}/merge_requests", {
            "source_branch": branch, "target_branch": target_branch,
            "title": title, "description": description,
            "remove_source_branch": False, "draft": True,
        })
        if "error" in result:
            return f"Error creating MR: {result['error']}"
        return f"Draft MR !{result.get('iid','?')} opened: {result.get('web_url','')}"

    schemas = [
        {
            "name": "execute_code",
            "description": "Execute code in the sandboxed environment and return the output.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "language": {"type": "string", "description": "Programming language: python, bash, or javascript."},
                    "code": {"type": "string", "description": "The source code to execute."},
                },
                "required": ["language", "code"],
            },
        },
        {
            "name": "read_file",
            "description": "Read a file from the sandbox working directory.",
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Relative path within the sandbox."}},
                "required": ["path"],
            },
        },
        {
            "name": "write_file",
            "description": "Write content to a file in the sandbox working directory.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path within the sandbox."},
                    "content": {"type": "string", "description": "The file content to write."},
                },
                "required": ["path", "content"],
            },
        },
        {
            "name": "list_files",
            "description": "List files in a directory within the sandbox.",
            "input_schema": {
                "type": "object",
                "properties": {"directory": {"type": "string", "description": "Relative path within the sandbox (default: .)"}},
                "required": [],
            },
        },
        {
            "name": "git_clone",
            "description": "Clone a GitLab repository into the sandbox and create a session branch. Call before any other git_ tools.",
            "input_schema": {
                "type": "object",
                "properties": {"repo_name": {"type": "string", "description": "Repository name in the user's GitLab namespace, e.g. python-microservices."}},
                "required": ["repo_name"],
            },
        },
        {
            "name": "git_status",
            "description": "Show uncommitted changes in the active repository.",
            "input_schema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "git_commit",
            "description": "Stage all changes and create a commit on the session branch.",
            "input_schema": {
                "type": "object",
                "properties": {"message": {"type": "string", "description": "Commit message."}},
                "required": ["message"],
            },
        },
        {
            "name": "git_push",
            "description": "Push the session branch to GitLab.",
            "input_schema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "git_create_mr",
            "description": "Create a GitLab Merge Request. Only call when the user explicitly asks for an MR.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "MR title."},
                    "description": {"type": "string", "description": "MR description."},
                    "target_branch": {"type": "string", "description": "Branch to merge into (default: main)."},
                },
                "required": ["title", "description"],
            },
        },
    ]

    callables = {
        "execute_code": execute_code,
        "read_file": read_file,
        "write_file": write_file,
        "list_files": list_files,
        "git_clone": git_clone,
        "git_status": git_status,
        "git_commit": git_commit,
        "git_push": git_push,
        "git_create_mr": git_create_mr,
    }

    return schemas, callables


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

    def _send_json(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length)

    def _authenticate(self):
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

    def do_POST(self):
        try:
            if self.path == "/token":
                self._handle_token()
            elif self.path == "/run":
                self._handle_run()
            elif self.path == "/reset":
                self._handle_reset()
            else:
                self._send_json(404, {"error": "not found"})
        except Exception as e:
            print(f"[error] POST {self.path}: {e}", flush=True)

    def _handle_token(self):
        username = self.headers.get("X-Forwarded-User")
        if not username:
            body = json.loads(self._read_body() or b"{}")
            username = body.get("username")
        if not username:
            self._send_json(400, {"error": "username required"})
            return
        token_data = create_token(username)
        print(f"[token] issued for user={username}", flush=True)
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

        schemas, callables = make_tools(username)

        with history_lock:
            history = list(conversation_histories.get(username, []))
        history.append({"role": "user", "content": prompt})
        messages = history[-(MAX_HISTORY_TURNS * 2):]

        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        def write_chunk(text):
            data = text.encode("utf-8")
            try:
                self.wfile.write(f"{len(data):x}\r\n".encode())
                self.wfile.write(data)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
            except (BrokenPipeError, OSError):
                pass

        output_parts = []
        current_messages = list(messages)

        try:
            while True:
                with client.messages.stream(
                    model="claude-opus-5",
                    max_tokens=16000,
                    system=SYSTEM_PROMPT,
                    tools=schemas,
                    messages=current_messages,
                ) as stream:
                    for text in stream.text_stream:
                        write_chunk(text)
                        output_parts.append(text)
                    final_message = stream.get_final_message()

                if final_message.stop_reason != "tool_use":
                    break

                tool_results = []
                for block in final_message.content:
                    if block.type == "tool_use":
                        write_chunk(f"\n[{block.name}...]\n")
                        fn = callables.get(block.name)
                        try:
                            result = fn(**block.input) if fn else f"Unknown tool: {block.name}"
                        except Exception as e:
                            result = f"Error: {e}"
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": str(result),
                        })

                current_messages = current_messages + [
                    {"role": "assistant", "content": final_message.content},
                    {"role": "user", "content": tool_results},
                ]

        except Exception as e:
            write_chunk(f"\nError: {e}")
            print(f"[error] user={username} error={e}", flush=True)
        finally:
            try:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except (BrokenPipeError, OSError):
                pass

        response_text = "".join(output_parts)
        with history_lock:
            h = conversation_histories.get(username, [])
            h.append({"role": "user", "content": prompt})
            h.append({"role": "assistant", "content": response_text})
            conversation_histories[username] = h[-(MAX_HISTORY_TURNS * 2):]

    def _handle_reset(self):
        username = self._authenticate()
        if not username:
            self._send_json(401, {"error": "invalid or expired token"})
            return
        with history_lock:
            conversation_histories.pop(username, None)
        print(f"[reset] cleared history for user={username}", flush=True)
        self._send_json(200, {"user": username, "message": "conversation history cleared"})

    def log_message(self, format, *args):
        print(f"[http] {self.client_address[0]} {format % args}", flush=True)


def main():
    port = int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), AgentHandler)
    print(f"Gateway listening on :{port}")
    print(f"  GitLab: {GITLAB_URL or '(not configured)'}")
    print(f"  GitLab auth user: {GITLAB_AUTH_USER}")
    print(f"  GitLab token configured: {'yes' if GITLAB_TOKEN else 'no'}")
    print(f"  Token TTL: {TOKEN_TTL_SECONDS}s")
    print(f"  Rate limit: {MAX_REQUESTS_PER_HOUR} req/hour/user", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
