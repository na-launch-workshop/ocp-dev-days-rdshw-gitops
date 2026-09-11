#!/usr/bin/env python3
"""CLI client for the Claude agent gateway. Runs inside Dev Spaces workspaces."""

import json
import os
import sys
import urllib.request
import urllib.error
from typing import Optional

GATEWAY_URL = os.environ.get(
    "CLAUDE_GATEWAY_URL", "http://claude-gateway.claude-sandbox.svc:8080"
)
TOKEN_FILE = os.path.expanduser("~/.claude-token")


def _load_token() -> Optional[str]:
    try:
        return open(TOKEN_FILE).read().strip()
    except FileNotFoundError:
        return None


def _save_token(token: str):
    with open(TOKEN_FILE, "w") as f:
        f.write(token)
    os.chmod(TOKEN_FILE, 0o600)


def register(username: str) -> str:
    data = json.dumps({"username": username}).encode()
    req = urllib.request.Request(
        f"{GATEWAY_URL}/token",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        result = json.loads(resp.read())
    _save_token(result["token"])
    print(f"Registered as {result['username']}, token expires at {result['expires_at']}")
    return result["token"]


def run(prompt: str):
    token = _load_token()
    if not token:
        print("No token found. Run: python claude-client.py register <your-name>", file=sys.stderr)
        sys.exit(1)

    data = json.dumps({"prompt": prompt}).encode()
    req = urllib.request.Request(
        f"{GATEWAY_URL}/run",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            while True:
                chunk = resp.read(4096)
                if not chunk:
                    break
                print(chunk.decode("utf-8"), end="", flush=True)
        print()
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read())
            print(f"Error ({e.code}): {body.get('error', 'unknown')}", file=sys.stderr)
        except Exception:
            print(f"Error ({e.code})", file=sys.stderr)
        if e.code == 401:
            print("Token expired. Run: python claude-client.py register <your-name>", file=sys.stderr)
        sys.exit(1)


def reset():
    token = _load_token()
    if not token:
        print("No token found. Run: python claude-client.py register <your-name>", file=sys.stderr)
        sys.exit(1)

    req = urllib.request.Request(
        f"{GATEWAY_URL}/reset",
        data=b"{}",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
        print(result.get("message", "Conversation history cleared."))
    except urllib.error.HTTPError as e:
        print(f"Error ({e.code})", file=sys.stderr)
        sys.exit(1)


def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python claude-client.py register <your-name>   # Get a session token")
        print("  python claude-client.py run <prompt>           # Run a prompt")
        print("  python claude-client.py reset                  # Clear conversation history")
        print()
        print("Examples:")
        print('  python claude-client.py register jdoe')
        print('  python claude-client.py run "Clone my GitLab repo python-microservices"')
        print('  python claude-client.py run "Add a /health endpoint and commit it"')
        print('  python claude-client.py run "Push my changes and open a draft MR"')
        print('  python claude-client.py reset')
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "register":
        if len(sys.argv) < 3:
            print("Usage: python claude-client.py register <your-name>", file=sys.stderr)
            sys.exit(1)
        register(sys.argv[2])
    elif cmd == "run":
        prompt = " ".join(sys.argv[2:])
        if not prompt:
            print("Usage: python claude-client.py run <prompt>", file=sys.stderr)
            sys.exit(1)
        run(prompt)
    elif cmd == "reset":
        reset()
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
