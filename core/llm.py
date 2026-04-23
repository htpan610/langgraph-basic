from __future__ import annotations

import json
import time
from typing import Any, Protocol

from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError

from core.config import LLMSettings
from data.models import Process, SkillProcess


class LLMProvider(Protocol):
    def map_process_batch(self, processes: list[Process], skill_processes: list[SkillProcess]) -> list[dict[str, Any]]:
        ...

    def suggest_split_ratios(self, process: Process, employee_names: list[str]) -> list[dict[str, Any]]:
        ...


class DeepSeekProvider:
    def __init__(self, settings: LLMSettings) -> None:
        self.settings = settings
        self.client = OpenAI(
            api_key=settings.api_key or "missing",
            base_url=settings.base_url,
            timeout=None,
            max_retries=0,
        )

    @property
    def enabled(self) -> bool:
        return bool(self.settings.api_key)

    def map_process_batch(self, processes: list[Process], skill_processes: list[SkillProcess]) -> list[dict[str, Any]]:
        if not self.enabled:
            raise RuntimeError("DeepSeek API key is not configured")
        # The mapper sends a fully structured prompt so downstream parsing stays deterministic and reviewable.
        prompt = {
            "task": "Map each sewing process to one standard process from the pants process library.",
            "rules": [
                "Return JSON only.",
                "Return one result per input process.",
                "Use empty process_id and suggested_new_skill=true when no good match exists.",
                "confidence must be between 0 and 1.",
            ],
            "category": "pants",
            "standard_processes": [
                {
                    "process_id": process.id,
                    "display_name": process.display_name,
                    "aliases": process.aliases,
                }
                for process in skill_processes
                if process.is_active
            ],
            "input_processes": [
                {
                    "process_hash": process.identity_hash,
                    "style_no": process.style_no,
                    "component": process.component,
                    "process_no": process.process_no,
                    "description": process.description,
                    "standard_time": process.standard_time,
                }
                for process in processes
            ],
            "schema": [
                {
                    "process_hash": "string",
                    "process_id": "string",
                    "display_name": "string",
                    "confidence": "number",
                    "reason": "string",
                    "suggested_new_skill": "boolean",
                }
            ],
        }
        content = self._chat(self.settings.mapping_model, json.dumps(prompt, ensure_ascii=False))
        value = json.loads(_strip_json_fences(content))
        if not isinstance(value, list):
            raise ValueError("DeepSeek batch mapping did not return a JSON array")
        return [item for item in value if isinstance(item, dict)]

    def suggest_split_ratios(self, process: Process, employee_names: list[str]) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        prompt = {
            "task": "Generate 3 to 5 split-ratio candidates for assigning one process to 2 or 3 employees.",
            "rules": ["Return JSON only", "Each ratios list must sum to 1.0"],
            "process": {"description": process.description, "standard_time": process.standard_time},
            "employees": employee_names,
        }
        content = self._chat(self.settings.reasoning_model, json.dumps(prompt, ensure_ascii=False))
        result = json.loads(_strip_json_fences(content))
        return result if isinstance(result, list) else []

    def _chat(self, model: str, prompt: str) -> str:
        last_error: Exception | None = None
        for attempt in range(1, 3):
            try:
                response = self.client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": "You are an industrial engineering expert for sewing lines. Output JSON only."},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.1,
                )
                return response.choices[0].message.content or "[]"
            except APITimeoutError as exc:
                last_error = exc
            except (APIConnectionError, RateLimitError) as exc:
                last_error = exc
                # Retry only on transient transport/rate failures; semantic errors should surface immediately.
                time.sleep(1.5)
        if last_error is not None:
            raise RuntimeError(f"DeepSeek request failed: {last_error}") from last_error
        raise RuntimeError("DeepSeek request failed without a valid response")


def _strip_json_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:]
    return stripped.strip()
