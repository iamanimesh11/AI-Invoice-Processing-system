"""
services/extraction_service/extractor.py
LLM-based invoice field extraction.

FIX from v1:
- LLM_PROVIDER / OPENAI_API_KEY read via get_settings() at call time, not
  at module import time. Env vars can be set after module load (tests, etc.).
- extract_invoice_fields() saves JSON to disk and returns the output *path*
  so the Airflow DAG only XComs small strings, not full result dicts.
- Extraction failure is surfaced as a typed exception rather than silently
  returning a zeroed-out default dict.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import requests

from services.extraction_service.prompt_templates import SYSTEM_PROMPT, build_extraction_prompt

logger = logging.getLogger(__name__)


# ── Exceptions ─────────────────────────────────────────────────────────────────

class ExtractionError(Exception):
    """Raised when the LLM call or JSON parsing fails."""


class LLMProviderError(ExtractionError):
    """Raised on network or API-level failures."""


# ── LLM back-ends ──────────────────────────────────────────────────────────────

def _call_openai(prompt: str, system: str) -> str:
    from config.settings import get_settings
    settings = get_settings()
    if not settings.openai_api_key:
        raise LLMProviderError("OPENAI_API_KEY is not configured.")
    try:
        import openai
        client = openai.OpenAI(api_key=settings.openai_api_key)
        response = client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=2048,
            response_format={"type": "json_object"},  # Force JSON mode where supported
        )
        return response.choices[0].message.content
    except Exception as exc:
        raise LLMProviderError(f"OpenAI API call failed: {exc}") from exc


def _call_local_llm(prompt: str, system: str) -> str:
    from config.settings import get_settings
    settings = get_settings()
    url = f"{settings.local_llm_url}/api/chat"
    payload = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "options": {"temperature": 0},
        "format": "json",  # Ollama JSON mode
    }
    try:
        resp = requests.post(url, json=payload, timeout=120)
        resp.raise_for_status()
        return resp.json()["message"]["content"]
    except Exception as exc:
        raise LLMProviderError(f"Local LLM call failed: {exc}") from exc


def call_llm(prompt: str, system: str = SYSTEM_PROMPT) -> str:
    """Route to configured LLM provider."""
    from config.settings import get_settings
    provider = get_settings().llm_provider
    logger.info("LLM call: provider=%s", provider)
    if provider == "local":
        return _call_local_llm(prompt, system)
    return _call_openai(prompt, system)


# ── JSON parsing ───────────────────────────────────────────────────────────────

def _parse_json_response(raw: str) -> Dict[str, Any]:
    """Extract a JSON object from an LLM response, handling markdown fences."""
    cleaned = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    raise ExtractionError(f"No valid JSON found in LLM response:\n{raw[:400]}")


# ── Field normalisation ────────────────────────────────────────────────────────

DEFAULT_FIELDS: Dict[str, Any] = {
    "invoice_number": None,
    "vendor": None,
    "invoice_date": None,
    "due_date": None,
    "total_amount": 0.0,
    "tax_amount": 0.0,
    "currency": "USD",
    "line_items": [],
    "confidence": 0.0,
}


def _normalise(data: Dict[str, Any]) -> Dict[str, Any]:
    result = {**DEFAULT_FIELDS, **data}
    for field in ("total_amount", "tax_amount", "confidence"):
        try:
            result[field] = float(result[field] or 0)
        except (TypeError, ValueError):
            result[field] = 0.0
    return result


# ── Output path helper ─────────────────────────────────────────────────────────

def get_extraction_output_path(pdf_name: str) -> Path:
    from config.settings import get_settings
    return Path(get_settings().extracted_output_dir) / f"{Path(pdf_name).stem}_extracted.json"


def extraction_result_exists(pdf_name: str) -> bool:
    return get_extraction_output_path(pdf_name).exists()


# ── Public API ─────────────────────────────────────────────────────────────────

def extract_invoice_fields(
    ocr_text: str,
    pdf_name: str,
    file_path: Optional[str] = None,
) -> str:
    """
    Send OCR text to the LLM, parse structured fields, save JSON, return output path.

    FIX: Returns the path string rather than the result dict so Airflow XCom
    carries only a small string between tasks, not the full extracted payload.

    Args:
        ocr_text: Full text from Tesseract OCR.
        pdf_name: Source PDF filename (for output naming).
        file_path: Original file path to embed in the result.

    Returns:
        Absolute path to the saved extraction JSON file.

    Raises:
        ExtractionError: If the LLM call or JSON parsing fails.
    """
    from config.settings import get_settings
    output_dir = Path(get_settings().extracted_output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    prompt = build_extraction_prompt(ocr_text)

    try:
        raw = call_llm(prompt)
        extracted = _parse_json_response(raw)
    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError(f"Unexpected error during extraction: {exc}") from exc

    result = _normalise(extracted)
    result["source_file"] = pdf_name
    result["file_path"] = file_path or pdf_name
    result["extracted_at"] = datetime.utcnow().isoformat()

    output_path = get_extraction_output_path(pdf_name)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    logger.info(
        "Extraction saved: %s (confidence=%.2f)", output_path.name, result.get("confidence", 0)
    )
    return str(output_path)


def load_extracted_result(extraction_json_path: str) -> Dict[str, Any]:
    """Load and parse a saved extraction JSON file."""
    p = Path(extraction_json_path)
    if not p.exists():
        raise FileNotFoundError(f"Extraction result not found: {extraction_json_path}")
    return json.loads(p.read_text())
