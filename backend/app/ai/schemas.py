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


# 卷證說明（設計 5.6）：每份卷證的證明方向與注意事項，每句附頁碼與逐字摘錄；說「沒有寫、不存在」的句子標 absence
EVIDENCE_NOTES_SCHEMA = _obj({
    "documents": {"type": "array", "items": _obj({
        "exhibit": _STR,
        "notes": {"type": "array", "items": _obj({
            "kind": {"type": "string", "enum": ["proves", "caution"]},
            "text": _STR,
            "absence": {"type": "boolean"},
            "page_no": _INT,
            "quote": _STR,
        })},
    })},
})


# 大事記（設計 5.7）：date 是西元 YYYY-MM-DD、YYYY-MM 或空字串；source 是證物代號或「對造書狀A」
TIMELINE_SCHEMA = _obj({
    "events": {"type": "array", "items": _obj({
        "date": _STR,
        "description": _STR,
        "amount": {"type": ["integer", "null"]},
        "source": _STR,
        "page_no": _INT,
        "quote": _STR,
    })},
})
