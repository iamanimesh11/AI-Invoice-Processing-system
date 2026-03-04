"""
services/extraction_service/prompt_templates.py
Centralised LLM prompts for invoice field extraction.
"""

SYSTEM_PROMPT = """You are an expert invoice data extraction assistant.
Analyse raw OCR text from invoice PDFs and return ONLY valid JSON — no markdown
fences, no explanation, no preamble. Use null for fields that cannot be found.
Monetary amounts must be numbers (float), never strings."""

INVOICE_EXTRACTION_PROMPT = """Extract the following fields from the invoice text.

Required output schema (JSON only):
{{
  "invoice_number":  string | null,
  "vendor":          string | null,
  "invoice_date":    "YYYY-MM-DD" | null,
  "due_date":        "YYYY-MM-DD" | null,
  "total_amount":    float,
  "tax_amount":      float,
  "currency":        string,   // ISO 4217 code e.g. "USD"
  "line_items": [
    {{
      "description": string,
      "quantity":    float,
      "unit_price":  float,
      "total":       float
    }}
  ],
  "confidence":      float     // 0.0–1.0, your extraction confidence
}}

--- INVOICE TEXT ---
{invoice_text}
--- END ---"""


def build_extraction_prompt(invoice_text: str) -> str:
    """Populate the extraction prompt with OCR text."""
    return INVOICE_EXTRACTION_PROMPT.format(invoice_text=invoice_text)
