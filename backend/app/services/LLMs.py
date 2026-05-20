"""
LLMs.py
-------
Two-tier LLM client layer for the cost-adaptive academic assistant (thesis §3.2.4).

Local tier  : llama3.2:3b via Ollama AsyncClient  → zero token cost
Cloud tier  : GPT-4o / GPT-4o mini via OpenAI AsyncOpenAI → per-token cost

Return contract
---------------
local_response()  → str
cloud_response()  → tuple[str, int, int]   (text, input_tokens, output_tokens)

The tuple return from cloud_response() is intentional: cost_tracker.py uses
the token counts to compute the exact per-request cost that populates
route_log.cost (thesis §3.4.3).  Callers that only need the text can unpack
with:  text, *_ = await cloud_response(...)
"""

from __future__ import annotations

import os
import re

import httpx
from ollama import AsyncClient          # local tier only
from openai import AsyncOpenAI          # cloud tier

# ── Ollama endpoint configuration ─────────────────────────────────────────────
# Primary:  remote Ollama server (Azure VM / tailscale peer)
# Fallback: localhost (development)
# OLLAMA_PRIMARY_URL  = os.environ.get("OLLAMA_URL",      "http://100.111.146.123:11434")
# OLLAMA_PRIMARY_URL  = os.environ.get("OLLAMA_URL",      "http://localhost:11434")
OLLAMA_PRIMARY_URL  = os.environ.get("OLLAMA_URL",      "http://20.251.161.66:11434")
OLLAMA_FALLBACK_URL = os.environ.get("OLLAMA_LOCAL_URL", "http://localhost:11434")

# Module-level Ollama client; replaced by initialize_client() if the primary
# is unreachable at startup.
_ollama_client: AsyncClient = AsyncClient(host=OLLAMA_PRIMARY_URL)

# ── OpenAI client (cloud tier) ────────────────────────────────────────────────
# Reads OPENAI_API_KEY from the environment automatically.
# Set it in your .env file:  OPENAI_API_KEY=sk-...
_openai_client: AsyncOpenAI = AsyncOpenAI()

# ── Local model ───────────────────────────────────────────────────────────────
LOCAL_MODEL = "llama3.2:3b"


# ════════════════════════════════════════════════════════════════════════════
# Ollama connection initialiser
# Call once from the FastAPI lifespan startup handler.
# ════════════════════════════════════════════════════════════════════════════

async def initialize_client() -> AsyncClient:
    """
    Return a working Ollama AsyncClient, trying the primary URL first.

    Falls back to OLLAMA_FALLBACK_URL (localhost) if the primary is
    unreachable. Updates the module-level _ollama_client so that
    local_response() uses the working connection automatically.

    Returns
    -------
    AsyncClient
        A connected client, or raises RuntimeError if both fail.
    """
    global _ollama_client

    for label, url in [("primary", OLLAMA_PRIMARY_URL),
                       ("fallback (localhost)", OLLAMA_FALLBACK_URL)]:
        candidate = AsyncClient(host=url)
        try:
            await candidate.list()
            print(f"[LLMs] Ollama connected via {label}: {url}")
            _ollama_client = candidate
            return candidate
        except (httpx.ConnectError, Exception) as exc:
            print(f"[LLMs] {label} unreachable ({exc}); trying next...")

    raise RuntimeError(
        "Cannot reach any Ollama endpoint. "
        "Check OLLAMA_URL / OLLAMA_LOCAL_URL environment variables."
    )


# ════════════════════════════════════════════════════════════════════════════
# Response cleaning
#
# Why not a regex scrubber?
# ─────────────────────────
# Hardcoded regex has two failure modes:
#
#   1. It strips content you want to keep.  The original code removed ALL
#      markdown code blocks (```...```) to catch reasoning artefacts — but
#      that also destroys any legitimate code the assistant returns.
#
#   2. It is a symptom of the wrong model.  <think> tags come from reasoning
#      models (deepseek-r1, qwen3-thinking).  llama3.2:3b never emits them.
#      If you switch to a thinking model, use think=False (Ollama parameter)
#      or reasoning_effort="none" (OpenAI o-series) at the API level so the
#      trace is suppressed before it reaches Python, not after.
#
# The cleaner below is therefore minimal: it only strips tags that llama3.2:3b
# genuinely emits when it leaks partial reasoning (rarely), and it never
# touches code blocks or structured content.
# ════════════════════════════════════════════════════════════════════════════

_THINK_TAG_RE   = re.compile(r"<think>.*?</think>",       re.DOTALL)
_MULTI_BLANK_RE = re.compile(r"\n{3,}")


def _clean(text: str) -> str:
    """
    Minimal post-processing for local model output.

    Only removes:
      - <think>…</think> blocks (defensive; llama3.2:3b should not emit these)
      - runs of 3+ blank lines collapsed to 2

    Does NOT strip markdown code blocks, emojis, or phrasing patterns.
    If a model emits unwanted phrasing, the right fix is a tighter system
    prompt — not a regex applied after the fact.
    """
    text = _THINK_TAG_RE.sub("", text)
    text = _MULTI_BLANK_RE.sub("\n\n", text)
    return text.strip()


# ════════════════════════════════════════════════════════════════════════════
# Local tier  –  llama3.2:3b via Ollama
# ════════════════════════════════════════════════════════════════════════════

async def local_response(
    prompt: str,
    system_prompt: str | None = None,
) -> str:
    """
    Send a prompt to llama3.2:3b through the Ollama local endpoint.

    Parameters
    ----------
    prompt        : the user-turn content
    system_prompt : optional system instruction prepended to the conversation

    Returns
    -------
    str
        The model's response text, cleaned of any reasoning artefacts.
        Returns a fallback error string on connection failure so the caller
        never receives None.

    Notes
    -----
    think=False suppresses chain-of-thought traces at the Ollama API level
    for any model that supports the thinking parameter (e.g. qwen3, deepseek-r1).
    llama3.2:3b ignores it silently, so it is safe to pass unconditionally.
    """
    messages: list[dict] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    try:
        response = await _ollama_client.chat(
            model=LOCAL_MODEL,
            messages=messages,
            think=False,        # suppress reasoning trace at the API level
        )
        return _clean(response["message"]["content"])
    except Exception as exc:
        print(f"[LLMs] Ollama error: {exc}")
        return "Error: local model unreachable."


# ════════════════════════════════════════════════════════════════════════════
# Cloud tier  –  GPT-4o / GPT-4o mini via OpenAI API
# ════════════════════════════════════════════════════════════════════════════

async def cloud_response(
    prompt: str,
    model: str,
    system_prompt: str | None = None,
) -> tuple[str, int, int]:
    """
    Send a prompt to an OpenAI GPT model and return the response with
    exact token counts for cost tracking.

    Parameters
    ----------
    prompt        : the user-turn content
    model         : OpenAI model identifier, e.g. "gpt-4o-mini" or "gpt-4o"
    system_prompt : optional system instruction prepended to the conversation

    Returns
    -------
    tuple[str, int, int]
        (response_text, input_tokens, output_tokens)

        input_tokens and output_tokens come directly from the API response's
        usage object.  cost_tracker.py multiplies them by the per-token rates
        in MODEL_COSTS to produce the exact USD cost stored in route_log
        (thesis §3.4.3).

    Notes on why OpenAI, not Ollama cloud
    ──────────────────────────────────────
    Ollama's cloud tier (gpt-oss:*-cloud) bills a flat monthly subscription,
    making per-request cost unobservable.  The OpenAI API returns a usage
    object on every response, which is the measurement unit the Chapter 4
    evaluation depends on.  See thesis §3.2.4 for the full justification.

    On cleanliness
    ──────────────
    GPT-4o and GPT-4o mini do not emit <think> tags or reasoning artefacts
    in standard chat completions, so no post-processing is applied.  If you
    use an o-series reasoning model, pass extra_body={"reasoning_effort": "low"}
    to reduce (but not eliminate) the internal reasoning budget.
    """
    messages: list[dict] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    try:
        response = await _openai_client.chat.completions.create(
            model=model,
            messages=messages,
        )

        text          = response.choices[0].message.content or ""
        input_tokens  = response.usage.prompt_tokens
        output_tokens = response.usage.completion_tokens

        return text, input_tokens, output_tokens

    except Exception as exc:
        print(f"[LLMs] OpenAI error: {exc}")
        return "Error: cloud model unreachable.", 0, 0