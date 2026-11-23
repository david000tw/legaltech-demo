"""各 AI 步驟的結構化輸出（JSON Schema）。"""


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or list(props),
            "additionalProperties": False}


_STR = {"type": "string"}
_INT = {"type": "integer"}

CLAIMS_SCHEMA = _obj({
    "claims": {"type": "array", "items": _obj({"summary": _STR, "quote": _STR, "page_no": _INT})},
})
