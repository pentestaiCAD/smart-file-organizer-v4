"""Optional AI backends — OpenRouter, OpenAI, Google Gemini, or any custom
OpenAI-compatible endpoint (including a local Ollama server).

In 4.0 the AI is used for *naming* and *search descriptions*, never to guess
which life-domain bucket a file belongs to. Stdlib only (urllib) so the
packaged .exe stays small and dependency-free.

Every outbound request is written to %APPDATA%\\SmartFileOrganizer\\ai-calls.log
before it is sent (hard constraint: "every outbound call is logged"). Only
text that was already extracted locally is ever sent; callers must never pass
the contents of credential-like files.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import app_config

PROVIDERS = {
    "openrouter": {
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1/chat/completions",
        "style": "openai",
        "default_model": "openai/gpt-4o-mini",
        "needs_key": True,
        "hint": "Get a key at openrouter.ai/keys — one key, hundreds of models.",
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1/chat/completions",
        "style": "openai",
        "default_model": "gpt-4o-mini",
        "needs_key": True,
        "hint": "Get a key at platform.openai.com/api-keys",
    },
    "gemini": {
        "label": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        "style": "gemini",
        "default_model": "gemini-2.0-flash",
        "needs_key": True,
        "hint": "Free key at aistudio.google.com/apikey",
    },
    "custom": {
        "label": "Custom / Local (Ollama, LM Studio, etc.)",
        "base_url": "http://localhost:11434/v1/chat/completions",
        "style": "openai",
        "default_model": "llama3.1",
        "needs_key": False,
        "hint": "Any OpenAI-compatible /chat/completions endpoint. Ollama needs no key.",
    },
}


class AIError(Exception):
    pass


# Circuit breaker: if bulk naming/description calls keep failing (dead quota,
# bad key, offline), stop hitting the network after a few misses so a scan of
# hundreds of files can't fire hundreds of doomed requests. Reset on success or
# by restarting the app. The explicit "Test connection" button bypasses this.
_MAX_FAILS = 3
_fail_count = 0
_tripped = False


def _breaker_blocked() -> bool:
    return _tripped


def _breaker_note(ok: bool) -> None:
    global _fail_count, _tripped
    if ok:
        _fail_count = 0
        _tripped = False
    else:
        _fail_count += 1
        if _fail_count >= _MAX_FAILS:
            _tripped = True


def reset_breaker() -> None:
    global _fail_count, _tripped
    _fail_count = 0
    _tripped = False


def log_call(provider: str, model: str, purpose: str, detail: str, sent_bytes: int) -> None:
    """Append one line per outbound request. Never records the API key or the
    file content itself — only that a call happened, for what, and how big."""
    try:
        app_config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        line = f"{stamp}\t{provider}\t{model}\t{purpose}\t{sent_bytes}B\t{detail}\n"
        with app_config.AI_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass  # logging must never break the operation, but we still tried


def _post_json(url: str, payload: dict, headers: dict, timeout: float = 20.0) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "ignore")
        raise AIError(f"HTTP {exc.code}: {body[:300]}") from exc
    except urllib.error.URLError as exc:
        raise AIError(f"connection failed: {exc.reason}") from exc
    except (TimeoutError, json.JSONDecodeError) as exc:
        raise AIError(str(exc)) from exc


def _ask_openai_style(cfg: dict, prompt: str) -> str:
    headers = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    if cfg.get("provider") == "openrouter":
        headers["HTTP-Referer"] = "https://localhost/smart-file-organizer"
        headers["X-Title"] = "Smart File Organizer"
    payload = {
        "model": cfg["model"],
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 40,
    }
    result = _post_json(cfg["base_url"], payload, headers)
    try:
        return result["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise AIError(f"unexpected response shape: {result}") from exc


def _ask_gemini(cfg: dict, prompt: str) -> str:
    url = cfg["base_url"].format(model=cfg["model"]) + f"?key={cfg['api_key']}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 40},
    }
    result = _post_json(url, payload, {"Content-Type": "application/json"})
    try:
        return result["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise AIError(f"unexpected response shape: {result}") from exc


def resolve_config(ai_settings: dict) -> dict:
    """Merge saved settings with provider defaults into a ready-to-use cfg."""
    provider = ai_settings.get("provider", "none")
    meta = PROVIDERS.get(provider, {})
    base_url = ai_settings.get("base_url") or meta.get("base_url", "")
    model = ai_settings.get("model") or meta.get("default_model", "")
    return {
        "provider": provider,
        "base_url": base_url,
        "model": model,
        "api_key": ai_settings.get("api_key", ""),
    }


def ask(cfg: dict, prompt: str, purpose: str = "query", detail: str = "") -> str:
    log_call(cfg.get("provider", "?"), cfg.get("model", "?"), purpose, detail, len(prompt.encode("utf-8")))
    style = PROVIDERS.get(cfg["provider"], {}).get("style", "openai")
    if style == "gemini":
        return _ask_gemini(cfg, prompt)
    return _ask_openai_style(cfg, prompt)


def test_connection(cfg: dict) -> tuple[bool, str]:
    try:
        reply = ask(cfg, "Reply with just the single word: OK", purpose="test", detail="connection test")
        reset_breaker()          # a good test re-arms bulk naming/description
        return True, reply[:160]
    except Exception as exc:  # noqa: BLE001 - surfaced to the user verbatim
        return False, str(exc)


def suggest_name(cfg: dict, filename: str, snippet: str | None) -> str | None:
    """Ask the model for a short, purpose-describing base name (no extension).
    Returns a slug-ish suggestion or None. Content passed here must already be
    locally extracted and screened (never a credential file)."""
    prompt = (
        "Suggest a short, descriptive file name (no extension, max 6 words) that "
        "captures this file's PURPOSE, not its type. Prefer the form "
        "subject_YYYY-MM-DD_kind, e.g. 'Staples_2026-03-14_invoice_paid'. "
        "Use only letters, digits, dashes and underscores.\n"
        f"Current file name: {filename}\n"
    )
    if snippet:
        prompt += f"First bit of the file's text:\n{snippet[:800]}\n"
    prompt += "Answer with ONLY the suggested name."
    if _breaker_blocked():
        return None
    try:
        raw = ask(cfg, prompt, purpose="name", detail=filename)
        _breaker_note(True)
    except AIError:
        _breaker_note(False)
        return None
    return raw.splitlines()[0].strip().strip('"').strip() or None


def describe(cfg: dict, filename: str, snippet: str | None) -> str | None:
    """One-line description used to make a file findable by meaning."""
    prompt = (
        "In one short sentence, describe what this file is about so someone "
        "could find it later by searching for its topic.\n"
        f"File name: {filename}\n"
    )
    if snippet:
        prompt += f"First bit of the file's text:\n{snippet[:1200]}\n"
    prompt += "Answer with ONLY the one-sentence description."
    if _breaker_blocked():
        return None
    try:
        raw = ask(cfg, prompt, purpose="describe", detail=filename)
        _breaker_note(True)
    except AIError:
        _breaker_note(False)
        return None
    return raw.strip() or None
