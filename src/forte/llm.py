"""LLM plumbing: a content-addressed response cache, the Claude client, and the
best-effort API-key loader.

The cache key is a SHA-256 of the *fully-rendered request*, so a hit can only
occur for an input the model actually received: change the prompt template,
decoding params, or model and the key changes. With a populated cache the whole
demo is free and bit-identical on re-run; with no key at all every caller has a
deterministic offline path.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class DecodingParams:
    temperature: float = 0.0
    top_p: float = 1.0
    max_tokens: int = 400
    seed: int = 0


def request_key(*, model_id: str, params: DecodingParams, prompt_version: str,
                adapter_version: str, rendered_prompt: str) -> str:
    payload = {"model_id": model_id, "params": asdict(params),
               "prompt_version": prompt_version, "adapter_version": adapter_version,
               "rendered_prompt": rendered_prompt}
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


class ResponseCache:
    def __init__(self, root: str | Path = ".cache/llm") -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> dict | None:
        p = self._path(key)
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        return None

    def put(self, key: str, record: dict) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


DEFAULT_MODEL = "claude-opus-4-8"
ALLOWED_MODELS = {"claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"}


class ClaudeModel:
    """Constructed lazily so a fully-cached run needs no API key at all."""

    def __init__(self, model_id: str = DEFAULT_MODEL) -> None:
        self.model_id = model_id if model_id in ALLOWED_MODELS else DEFAULT_MODEL
        self._client = None

    def _get_client(self):
        if self._client is None:
            from anthropic import Anthropic  # reads ANTHROPIC_API_KEY
            self._client = Anthropic()
        return self._client

    def complete(self, rendered_prompt: str, params: DecodingParams) -> str:
        msg = self._get_client().messages.create(
            model=self.model_id,
            max_tokens=params.max_tokens,
            messages=[{"role": "user", "content": rendered_prompt}],
        )
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def load_api_key() -> bool:
    """Best-effort: load ANTHROPIC_API_KEY from .env or an out-of-repo credentials
    file so live runs work without manual env setup. Returns True if a key is set."""
    cur = os.environ.get("ANTHROPIC_API_KEY")
    if cur:
        cleaned = "".join(cur.split())   # a stray newline makes an illegal HTTP header
        if cleaned != cur:
            os.environ["ANTHROPIC_API_KEY"] = cleaned
        return bool(cleaned)
    home = Path(os.path.expanduser("~"))
    candidates = [
        Path(".env"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "forte" / "credentials.env",
        home / ".forte" / "credentials.env",
    ]
    for p in candidates:
        try:
            if str(p) and p.exists():
                for line in p.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    if line.lower().startswith("export "):
                        line = line[7:]
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        except Exception:
            pass
    return bool(os.environ.get("ANTHROPIC_API_KEY"))
