import asyncio
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.parse
from collections import defaultdict
from pathlib import Path

import aiohttp as aiohttp_client
from aiohttp import web
from anthropic import AsyncAnthropic, beta_async_tool


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

client = AsyncAnthropic()

rate_counters: dict[str, list[float]] = defaultdict(list)
conversation_histories: dict[str, list] = {}
MAX_HISTORY_TURNS = 10


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


async def _run_git(args: list, cwd: Path, workdir: Path, username: str) -> tuple:
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd), env=_git_env(workdir, username),
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return 1, "", "git operation timed out"
        return proc.returncode, stdout.decode(), stderr.decode()
    except Exception as e:
        return 1, "", str(e)


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


async def _gitlab_api(method: str, path: str, body: dict | None = None) -> dict:
    url = f"{GITLAB_URL}/api/v4{path}"
    headers = {
        "PRIVATE-TOKEN": GITLAB_TOKEN,
        "Content-Type": "application/json",
    }
    try:
        async with aiohttp_client.ClientSession() as session:
            async with session.request(
                method, url, json=body, headers=headers, timeout=aiohttp_client.ClientTimeout(total=30)
            ) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    return {"error": f"HTTP {resp.status}: {text[:500]}"}
                return json.loads(text)
    except Exception as e:
        return {"error": str(e)}


# --- Tool factory (per-request, closures over username/workdir) ---

def make_tools(username: str):
    workdir = _user_workdir(username)

    @beta_async_tool
    async def execute_code(language: str, code: str) -> str:
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
        cmd_args = runners[language] + [code]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(cwd),
                env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(workdir),
                     "TMPDIR": str(workdir), "LANG": "C.UTF-8"},
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=EXEC_TIMEOUT)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return f"Error: execution timed out after {EXEC_TIMEOUT}s"
        except Exception as e:
            return f"Error: {e}"
        parts = []
        stdout_text = stdout.decode(errors="replace")
        stderr_text = stderr.decode(errors="replace")
        if stdout_text:
            parts.append(f"stdout:\n{stdout_text[:MAX_OUTPUT_BYTES]}")
        if stderr_text:
            parts.append(f"stderr:\n{stderr_text[:MAX_OUTPUT_BYTES]}")
        parts.append(f"exit code: {proc.returncode}")
        return "\n".join(parts)

    @beta_async_tool
    async def read_file(path: str) -> str:
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

    @beta_async_tool
    async def write_file(path: str, content: str) -> str:
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

    @beta_async_tool
    async def list_files(directory: str = ".") -> str:
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

    @beta_async_tool
    async def git_clone(repo_name: str) -> str:
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
            proc = await asyncio.create_subprocess_exec(
                "git", "clone", _auth_url(f"{username}/{repo_name}"), str(repo_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(workdir), env=_git_env(workdir, username),
            )
            try:
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return "Error: git clone timed out after 120s"
        except Exception as e:
            return f"Error: git clone failed\n{e}"

        if proc.returncode != 0:
            return f"Error: git clone failed\n{stderr.decode().replace(GITLAB_TOKEN, '***')}"

        branch = f"ai/{username}/{int(time.time())}"
        rc, _, err = await _run_git(["checkout", "-b", branch], repo_path, workdir, username)
        if rc != 0:
            return f"Cloned but failed to create session branch: {err}"

        (workdir / ".active_repo").write_text(repo_name)
        (workdir / ".active_branch").write_text(branch)

        return f"Cloned {repo_name}/\nSession branch: {branch}\nReady — use read_file/write_file to explore and edit."

    @beta_async_tool
    async def git_status() -> str:
        """Show uncommitted changes in the active repository."""
        repo = _active_repo_path(workdir)
        if not repo:
            return "Error: no repo cloned. Use git_clone first."
        rc, out, err = await _run_git(["status", "--short"], repo, workdir, username)
        if rc != 0:
            return f"Error: {err}"
        return out.strip() or "(working tree clean)"

    @beta_async_tool
    async def git_commit(message: str) -> str:
        """Stage all changes and create a commit on the session branch.

        Args:
            message: Commit message describing what changed and why.
        """
        repo = _active_repo_path(workdir)
        if not repo:
            return "Error: no repo cloned. Use git_clone first."
        rc, _, err = await _run_git(["add", "-A"], repo, workdir, username)
        if rc != 0:
            return f"Error: git add failed\n{err}"
        rc, out, err = await _run_git(["commit", "-m", message], repo, workdir, username)
        if rc != 0:
            return f"Error: git commit failed\n{err}"
        return out.strip() or "Committed."

    @beta_async_tool
    async def git_push() -> str:
        """Push the session branch to GitLab."""
        if not GITLAB_TOKEN:
            return "Error: GitLab not configured on this gateway."
        repo = _active_repo_path(workdir)
        branch = _active_branch(workdir)
        if not repo or not branch:
            return "Error: no repo cloned. Use git_clone first."

        await _run_git(
            ["remote", "set-url", "origin", _auth_url(f"{username}/{repo.name}")],
            repo, workdir, username,
        )
        rc, _, err = await _run_git(["push", "-u", "origin", branch], repo, workdir, username)
        if rc != 0:
            return f"Error: git push failed\n{err.replace(GITLAB_TOKEN, '***')}"
        return f"Pushed to origin/{branch}"

    @beta_async_tool
    async def git_create_mr(title: str, description: str, target_branch: str = "main") -> str:
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
        result = await _gitlab_api(
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


# --- HTTP handlers ---

def _authenticate(request: web.Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    return validate_token(auth[7:])


async def handle_healthz(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def handle_token(request: web.Request) -> web.Response:
    username = request.headers.get("X-Forwarded-User")
    if not username:
        try:
            body = await request.json()
        except Exception:
            body = {}
        username = body.get("username")
    if not username:
        return web.json_response(
            {"error": "username required (X-Forwarded-User header or JSON body)"},
            status=400,
        )
    token_data = create_token(username)
    print(f"[token] issued for user={username} expires={token_data['expires_at']}", flush=True)
    return web.json_response(token_data)


async def handle_run(request: web.Request) -> web.StreamResponse:
    username = _authenticate(request)
    if not username:
        return web.json_response({"error": "invalid or expired token"}, status=401)
    if not check_rate_limit(username):
        return web.json_response(
            {"error": f"rate limit exceeded ({MAX_REQUESTS_PER_HOUR}/hour)"},
            status=429,
        )
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    prompt = body.get("prompt", "").strip()
    if not prompt:
        return web.json_response({"error": "prompt required"}, status=400)

    print(f"[run] user={username} prompt={prompt[:80]}...", flush=True)

    tools = make_tools(username)

    history = list(conversation_histories.get(username, []))
    history.append({"role": "user", "content": prompt})
    messages = history[-(MAX_HISTORY_TURNS * 2):]

    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/plain; charset=utf-8",
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
        },
    )
    response.enable_chunked_encoding()
    await response.prepare(request)

    output_parts = []
    try:
        runner = client.beta.messages.tool_runner(
            model="claude-opus-5",
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            thinking={"type": "adaptive"},
            tools=tools,
            messages=messages,
        )
        async for message in runner:
            for block in message.content:
                if block.type == "text" and block.text:
                    try:
                        await response.write(block.text.encode("utf-8"))
                    except ConnectionResetError:
                        return response
                    output_parts.append(block.text)
    except Exception as e:
        try:
            await response.write(f"\nError: {e}".encode("utf-8"))
        except ConnectionResetError:
            pass
        print(f"[error] user={username} error={e}", flush=True)

    try:
        await response.write_eof()
    except (ConnectionResetError, OSError):
        pass

    response_text = "\n".join(output_parts)
    h = conversation_histories.get(username, [])
    h.append({"role": "user", "content": prompt})
    h.append({"role": "assistant", "content": response_text})
    conversation_histories[username] = h[-(MAX_HISTORY_TURNS * 2):]

    return response


async def handle_reset(request: web.Request) -> web.Response:
    username = _authenticate(request)
    if not username:
        return web.json_response({"error": "invalid or expired token"}, status=401)
    conversation_histories.pop(username, None)
    print(f"[reset] cleared history for user={username}", flush=True)
    return web.json_response({"user": username, "message": "conversation history cleared"})


def main():
    port = int(os.environ.get("PORT", "8080"))

    app = web.Application()
    app.router.add_get("/healthz", handle_healthz)
    app.router.add_post("/token", handle_token)
    app.router.add_post("/run", handle_run)
    app.router.add_post("/reset", handle_reset)

    print(f"Gateway listening on :{port}")
    print(f"  GitLab: {GITLAB_URL or '(not configured)'}")
    print(f"  GitLab auth user: {GITLAB_AUTH_USER}")
    print(f"  GitLab token configured: {'yes' if GITLAB_TOKEN else 'no'}")
    print(f"  Token TTL: {TOKEN_TTL_SECONDS}s")
    print(f"  Rate limit: {MAX_REQUESTS_PER_HOUR} req/hour/user", flush=True)

    web.run_app(app, host="0.0.0.0", port=port, print=None)


if __name__ == "__main__":
    main()
