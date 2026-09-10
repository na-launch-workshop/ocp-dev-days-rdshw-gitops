#!/usr/bin/env python3
"""CLI client for the Claude agent gateway. Runs inside Dev Spaces workspaces."""

import json
import os
import sys
import urllib.request
import urllib.error

GATEWAY_URL = os.environ.get(
    "CLAUDE_GATEWAY_URL", "http://claude-gateway.claude-sandbox.svc:8080"
)
TOKEN_FILE = os.path.expanduser("~/.claude-token")


def _load_token() -> str | None:
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
        print("No token found. Run: claude-client register <your-name>", file=sys.stderr)
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
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read())
        print(result.get("response", ""))
    except urllib.error.HTTPError as e:
        body = json.loads(e.read())
        print(f"Error ({e.code}): {body.get('error', 'unknown')}", file=sys.stderr)
        if e.code == 401:
            print("Token expired. Run: claude-client register <your-name>", file=sys.stderr)
        sys.exit(1)


def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  claude-client register <your-name>   # Get a session token")
        print("  claude-client run <prompt>            # Run a prompt")
        print()
        print("Examples:")
        print('  claude-client register jdoe')
        print('  claude-client run "Write a Python script that prints fibonacci numbers"')
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "register":
        if len(sys.argv) < 3:
            print("Usage: claude-client register <your-name>", file=sys.stderr)
            sys.exit(1)
        register(sys.argv[2])
    elif cmd == "run":
        prompt = " ".join(sys.argv[2:])
        if not prompt:
            print("Usage: claude-client run <prompt>", file=sys.stderr)
            sys.exit(1)
        run(prompt)
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
