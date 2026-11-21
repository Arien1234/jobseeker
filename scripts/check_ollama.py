"""Check that the local Ollama model answers. No resume or profile is sent."""

import json
import os
import sys
import urllib.request

HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
MODEL = os.environ.get("OLLAMA_MODEL", "qwen3.5:9b")


def main() -> int:
    payload = {
        "model": MODEL,
        "stream": False,
        "format": "json",
        "think": False,
        "messages": [
            {"role": "system", "content": "只输出 JSON：{\"ok\": true}"},
            {"role": "user", "content": "回复 ok"},
        ],
        "options": {"temperature": 0},
    }
    request = urllib.request.Request(
        f"{HOST}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            body = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        print(f"连不上 {HOST} 的模型 {MODEL}：{type(exc).__name__}")
        return 1
    print(f"模型 {MODEL} 有回复。")
    content = ((body.get("message") or {}).get("content") or "").strip()
    print(content[:200])
    return 0


if __name__ == "__main__":
    sys.exit(main())
