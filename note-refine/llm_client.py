from __future__ import annotations

import os
import sys
from types import SimpleNamespace


DEFAULT_GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")


class GeminiClient:
    def __init__(self, api_key: str, model: str = DEFAULT_GEMINI_MODEL):
        self.api_key = api_key
        self.model = model
        self._backend = None
        self._client = None

        try:
            from google import genai

            self._backend = "google_genai"
            self._client = genai.Client(api_key=api_key)
            return
        except ImportError:
            pass

        try:
            import google.generativeai as genai

            genai.configure(api_key=api_key)
            self._backend = "google_generativeai"
            self._client = genai
            return
        except ImportError as exc:
            raise RuntimeError(
                "Gemini SDK が見つかりません。`pip install google-genai` "
                "または `pip install google-generativeai` を実行してください。"
            ) from exc

    @property
    def messages(self) -> "GeminiMessagesAPI":
        return GeminiMessagesAPI(self)


class GeminiMessagesAPI:
    def __init__(self, parent: GeminiClient):
        self.parent = parent

    def create(
        self,
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        system: str | None = None,
        messages: list[dict],
    ):
        prompt = _build_prompt(system=system, messages=messages)
        model_name = model or self.parent.model

        if self.parent._backend == "google_genai":
            response = self.parent._client.models.generate_content(
                model=model_name,
                contents=prompt,
            )
            text = _extract_text_google_genai(response)
        else:
            genai = self.parent._client
            generation_config = {}
            if max_tokens is not None:
                generation_config["max_output_tokens"] = max_tokens
            response = genai.GenerativeModel(model_name=model_name).generate_content(
                prompt,
                generation_config=generation_config or None,
            )
            text = _extract_text_google_generativeai(response)

        return SimpleNamespace(content=[SimpleNamespace(text=text)])


def get_client():
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("❌ GEMINI_API_KEY または GOOGLE_API_KEY 環境変数が設定されていません。")
        print("設定例:")
        print("  set GEMINI_API_KEY=your-api-key")
        print("  set GEMINI_MODEL=gemini-2.5-flash")
        sys.exit(1)

    try:
        return GeminiClient(api_key=api_key)
    except RuntimeError as exc:
        print(f"❌ {exc}")
        sys.exit(1)


def _build_prompt(system: str | None, messages: list[dict]) -> str:
    parts = []
    if system:
        parts.append("## System Instructions")
        parts.append(system.strip())

    for message in messages:
        role = message.get("role", "user").capitalize()
        content = message.get("content", "")
        parts.append(f"## {role}")
        parts.append(str(content).strip())

    return "\n\n".join(part for part in parts if part)


def _extract_text_google_genai(response) -> str:
    text = getattr(response, "text", None)
    if text:
        return text.strip()

    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        collected = []
        for part in parts:
            part_text = getattr(part, "text", None)
            if part_text:
                collected.append(part_text)
        if collected:
            return "\n".join(collected).strip()

    raise RuntimeError("Gemini からテキスト応答を取得できませんでした。")


def _extract_text_google_generativeai(response) -> str:
    text = getattr(response, "text", None)
    if text:
        return text.strip()

    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        collected = []
        for part in parts:
            part_text = getattr(part, "text", None)
            if part_text:
                collected.append(part_text)
        if collected:
            return "\n".join(collected).strip()

    raise RuntimeError("Gemini からテキスト応答を取得できませんでした。")
