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
    # claim_seqs：這段回應的主張（或請求項目）編號，前言、結論可以留空（設計 6.5）
    "paragraphs": {"type": "array", "items": _obj({"heading": _STR, "text": _STR, "citations": _CITATIONS,
                                                   "claim_seqs": {"type": "array", "items": _INT}})},
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


HEALTH_LEVELS = ["high", "suggest", "gap"]


def health_schema(codes: list[str], with_undisputed: bool) -> dict:
    """書狀健檢（設計 5.8）：判決法條只能填本輪候選代碼（沒有候選就拿掉 references）；
    清單外的寫在 other_references。起訴狀沒有對造陳述，拿掉 undisputed。"""
    source = _obj({"source": _STR, "page_no": _INT, "quote": _STR})
    item: dict = {
        "level": {"type": "string", "enum": HEALTH_LEVELS},
        "title": _STR,
        "explanation": _STR,
        "claim_seqs": {"type": "array", "items": _INT},
        "absence": {"type": "boolean"},
        "evidence": {"type": "array", "items": source},
    }
    if codes:
        item["references"] = {"type": "array", "items": {"type": "string", "enum": list(codes)}}
    item["other_references"] = {"type": "array", "items": _STR}
    props: dict = {"items": {"type": "array", "items": _obj(item)}}
    if with_undisputed:
        props["undisputed"] = {"type": "array", "items": _obj(
            {"fact": _STR, "source": _STR, "page_no": _INT, "quote": _STR})}
    props["issues"] = {"type": "array", "items": _STR}
    return _obj(props)


def support_schema(ids: list[str]) -> dict:
    """支持度檢查（設計 7.1）：每筆引用一個結果，id 只能填這批的代碼。
    不加 minItems／maxItems（結構化輸出的嚴格模式限制陣列長度條件）；缺漏的由程式記成尚未檢查。"""
    from app.models import SUPPORT_VERDICTS
    item = _obj({"id": {"type": "string", "enum": list(ids)},
                 "verdict": {"type": "string", "enum": list(SUPPORT_VERDICTS)},
                 "explanation": _STR})
    return _obj({"items": {"type": "array", "items": item}})


def judgment_summary_schema() -> dict:
    """判決要旨（節錄式，計畫 3 丙）：爭點標籤＋法院理由原文節錄（附段號）。"""
    return _obj({
        "issues": {"type": "array", "items": _STR},
        "excerpts": {"type": "array", "items": _obj({"passage_seq": _INT, "text": _STR})},
    })
