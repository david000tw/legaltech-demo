"""各 AI 步驟的結構化輸出（JSON Schema）。"""


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or list(props),
            "additionalProperties": False}


_STR = {"type": "string"}
_INT = {"type": "integer"}

CLAIMS_SCHEMA = _obj({
    "claims": {"type": "array", "items": _obj({"summary": _STR, "quote": _STR, "page_no": _INT})},
})


_CITATIONS = {"type": "array", "items": _obj({"code": _STR, "quote": _STR})}

DRAFT_SCHEMA = _obj({
    "paragraphs": {"type": "array", "items": _obj({"heading": _STR, "text": _STR, "citations": _CITATIONS})},
})

REWRITE_SCHEMA = _obj({"text": _STR, "citations": _CITATIONS})


def select_schema(codes: dict[str, list[str]]) -> dict:
    """篩選步驟的輸出格式（設計 5.7）：代碼欄位只能填本輪候選代碼；某一類沒有候選就拿掉該欄位
    （結構化輸出的嚴格模式不支援 maxItems: 0）。清單外想引用的項目寫在 other_references。"""
    props: dict = {"claim_seq": _INT}
    if codes.get("evidence"):
        props["evidence"] = {"type": "array", "items": _obj(
            {"code": {"type": "string", "enum": list(codes["evidence"])}, "quote": _STR})}
    for field in ("judgments", "statutes"):
        if codes.get(field):
            props[field] = {"type": "array", "items": {"type": "string", "enum": list(codes[field])}}
    props["other_references"] = {"type": "array", "items": _STR}
    props["note"] = _STR
    return _obj({"selections": {"type": "array", "items": _obj(props)}})


REQUESTS_SCHEMA = _obj({
    "items": {"type": "array", "items": _obj({
        "label": _STR,
        "request_type": {"type": "string", "enum": ["monetary", "non_monetary"]},
        "amount": {"type": ["integer", "null"]},
        "no_receipt": {"type": "boolean"},
        "basis_text": _STR,
        "evidence": {"type": "array", "items": _obj({"exhibit": _STR, "page_no": _INT, "quote": _STR})},
    })},
})
