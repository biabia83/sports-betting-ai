"""
model_interface.py — Abstract base + 5 provider adapters for LLM sports picks.
"""

import os
import json
from abc import ABC, abstractmethod
from typing import Dict, List
from datetime import date

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = (
    "You are a sharp sports bettor. You strictly output JSON. "
    "No markdown. No conversational filler."
)

USER_PROMPT_TEMPLATE = (
    "Give me your top 3 {league} picks for {date}. "
    "Return JSON format: "
    '[{{"game": "Team A vs Team B", "pick": "Team A -3.5", '
    '"odds": -110, "confidence": 85}}]'
)


# ---------------------------------------------------------------------------
# Abstract Base
# ---------------------------------------------------------------------------
class SportsPredictor(ABC):
    """Base contract every provider adapter must fulfill."""

    @abstractmethod
    def get_daily_picks(self, league: str, date: str) -> List[Dict]:
        """Return a list of pick dicts for the given league and date."""
        ...


# ---------------------------------------------------------------------------
# Helper — extract JSON from potentially messy LLM output
# ---------------------------------------------------------------------------
import re

def _extract_json(raw: str) -> list:
    """Parse JSON from raw LLM text, handling markdown fences and preamble."""
    raw = raw.strip()

    # Strip markdown code fences if present
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)```", raw, re.DOTALL)
    if fence_match:
        raw = fence_match.group(1).strip()

    # Try direct parse first
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            return parsed.get("picks", [parsed])
        return [parsed]
    except json.JSONDecodeError:
        pass

    # Try to find the first [ ... ] in the text
    bracket_match = re.search(r"\[.*\]", raw, re.DOTALL)
    if bracket_match:
        try:
            return json.loads(bracket_match.group(0))
        except json.JSONDecodeError:
            pass

    # Try to find the first { ... } in the text
    brace_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if brace_match:
        try:
            parsed = json.loads(brace_match.group(0))
            return parsed.get("picks", [parsed]) if isinstance(parsed, dict) else [parsed]
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not extract JSON from response: {raw[:200]}")


# ---------------------------------------------------------------------------
# Helper — shared OpenAI-compatible call (used by OpenAI, Grok, DeepSeek)
# ---------------------------------------------------------------------------
def _openai_compatible_call(
    api_key: str,
    model: str,
    league: str,
    pick_date: str,
    base_url: str | None = None,
) -> List[Dict]:
    from openai import OpenAI

    kwargs = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url

    client = OpenAI(**kwargs)
    user_prompt = USER_PROMPT_TEMPLATE.format(league=league, date=pick_date)

    resp = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,
    )
    raw = resp.choices[0].message.content.strip()
    return _extract_json(raw)


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------
class OpenAIAdapter(SportsPredictor):
    """GPT-4o via the OpenAI API."""

    def __init__(self, model: str = "gpt-4o"):
        self.model = model
        self.api_key = os.environ["OPENAI_API_KEY"]

    def get_daily_picks(self, league: str, date: str) -> List[Dict]:
        try:
            return _openai_compatible_call(self.api_key, self.model, league, date)
        except Exception as e:
            print(f"[OpenAI] Error: {e}")
            return []


class AnthropicAdapter(SportsPredictor):
    """Claude 3.5 Sonnet via the Anthropic API."""

    def __init__(self, model: str = "claude-sonnet-4-5-20250929"):
        self.model = model
        self.api_key = os.environ["ANTHROPIC_API_KEY"]

    def get_daily_picks(self, league: str, date: str) -> List[Dict]:
        try:
            from anthropic import Anthropic

            client = Anthropic(api_key=self.api_key)
            user_prompt = USER_PROMPT_TEMPLATE.format(league=league, date=date)

            resp = client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
                temperature=0.3,
            )
            raw = resp.content[0].text.strip()
            return _extract_json(raw)
        except Exception as e:
            print(f"[Anthropic] Error: {e}")
            return []


class GeminiAdapter(SportsPredictor):
    """Gemini 1.5 Pro via the Google Generative AI SDK."""

    def __init__(self, model: str = "gemini-2.0-flash"):
        self.model = model
        self.api_key = os.environ["GOOGLE_API_KEY"]

    def get_daily_picks(self, league: str, date: str) -> List[Dict]:
        try:
            import google.generativeai as genai

            genai.configure(api_key=self.api_key)
            model = genai.GenerativeModel(
                model_name=self.model,
                system_instruction=SYSTEM_PROMPT,
            )
            user_prompt = USER_PROMPT_TEMPLATE.format(league=league, date=date)

            resp = model.generate_content(
                user_prompt,
                generation_config={
                    "response_mime_type": "application/json",
                    "temperature": 0.3,
                },
            )
            raw = resp.text.strip()
            return _extract_json(raw)
        except Exception as e:
            print(f"[Gemini] Error: {e}")
            return []


class GrokAdapter(SportsPredictor):
    """xAI Grok Beta via OpenAI-compatible endpoint."""

    def __init__(
        self,
        model: str = "grok-3",
        base_url: str = "https://api.x.ai/v1",
    ):
        self.model = model
        self.base_url = base_url
        self.api_key = os.environ["GROK_API_KEY"]

    def get_daily_picks(self, league: str, date: str) -> List[Dict]:
        try:
            return _openai_compatible_call(
                self.api_key, self.model, league, date, base_url=self.base_url,
            )
        except Exception as e:
            print(f"[Grok] Error: {e}")
            return []


class DeepSeekAdapter(SportsPredictor):
    """DeepSeek V3 via OpenAI-compatible endpoint."""

    def __init__(
        self,
        model: str = "deepseek-chat",
        base_url: str = "https://api.deepseek.com/v1",
    ):
        self.model = model
        self.base_url = base_url
        self.api_key = os.environ["DEEPSEEK_API_KEY"]

    def get_daily_picks(self, league: str, date: str) -> List[Dict]:
        try:
            return _openai_compatible_call(
                self.api_key, self.model, league, date, base_url=self.base_url,
            )
        except Exception as e:
            print(f"[DeepSeek] Error: {e}")
            return []


# ---------------------------------------------------------------------------
# Convenience — run all adapters
# ---------------------------------------------------------------------------
ALL_ADAPTERS: List[SportsPredictor] = []


def build_adapters() -> List[SportsPredictor]:
    """Instantiate every adapter whose API key is present in the environment."""
    mapping = {
        "OPENAI_API_KEY": OpenAIAdapter,
        "ANTHROPIC_API_KEY": AnthropicAdapter,
        "GOOGLE_API_KEY": GeminiAdapter,
        "GROK_API_KEY": GrokAdapter,
        "DEEPSEEK_API_KEY": DeepSeekAdapter,
    }
    adapters = []
    for env_var, cls in mapping.items():
        if os.environ.get(env_var):
            adapters.append(cls())
        else:
            print(f"[skip] {cls.__name__} — {env_var} not set")
    return adapters


# ---------------------------------------------------------------------------
# Quick smoke test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    today = date.today().isoformat()
    adapters = build_adapters()

    for adapter in adapters:
        name = type(adapter).__name__
        print(f"\n{'='*50}")
        print(f"Provider: {name}")
        picks = adapter.get_daily_picks("NBA", today)
        if picks:
            print(json.dumps(picks, indent=2))
        else:
            print("No picks returned.")
