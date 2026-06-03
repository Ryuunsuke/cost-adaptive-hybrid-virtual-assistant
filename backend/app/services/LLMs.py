from __future__ import annotations

import os
import re

import httpx
from ollama import AsyncClient          # local tier
from openai import AsyncOpenAI          # cloud tier


OLLAMA_PRIMARY_URL  = os.environ.get("OLLAMA_URL",      "http://localhost:11434")

_ollama_client: AsyncClient = AsyncClient(host=OLLAMA_PRIMARY_URL)
_openai_client: AsyncOpenAI = AsyncOpenAI()

LOCAL_MODEL = "llama3.2:3b"

async def initialize_client() -> AsyncClient:

    global _ollama_client

    for label, url in [("primary", OLLAMA_PRIMARY_URL)]:
        candidate = AsyncClient(host=url)
        try:
            await candidate.list()
            print(f"[LLMs] Ollama connected via {label}: {url}")
            _ollama_client = candidate
            return candidate
        except (httpx.ConnectError, Exception) as exc:
            print(f"[LLMs] {label} unreachable ({exc}).")

    raise RuntimeError(
        "Cannot reach any Ollama endpoint. "
    )

_THINK_TAG_RE   = re.compile(r"<think>.*?</think>",       re.DOTALL)
_MULTI_BLANK_RE = re.compile(r"\n{3,}")

# Response cleaning
def _clean(text: str) -> str:
    text = _THINK_TAG_RE.sub("", text)
    text = _MULTI_BLANK_RE.sub("\n\n", text)
    return text.strip()

async def local_response(
    prompt: str,
    system_prompt: str | None = None,
) -> str:

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

async def cloud_response(
    prompt: str,
    model: str,
    system_prompt: str | None = None,
) -> tuple[str, int, int]:
    
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