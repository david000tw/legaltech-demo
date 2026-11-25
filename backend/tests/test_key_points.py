"""重點清單（設計 6.5）：草稿回應了對方哪幾點、寫在第幾段；已勾選卻沒有段落回應的標出來。"""
from sqlalchemy import select

from app.db import tenant_session
from app.key_points import key_points
from app.models import Claim, Draft, Paragraph, Round
from tests.helpers import create_firm
from tests.pipeline_helpers import make_round


def _setup(db, seqs_per_paragraph, selected=(1, 2, 3)):
    firm = create_firm("甲事務所")
    _, rid = make_round(firm, our_side="defendant")
    with tenant_session(firm) as s:
        for seq, summary in enumerate(("被告未讓幹道車先行", "醫療費3萬元", "事故發生在5月2日", "精神慰撫金"), start=1):
            s.add(Claim(firm_id=firm, round_id=rid, seq=seq, summary=summary, quote="", selected=seq in selected))
        draft = Draft(firm_id=firm, round_id=rid)
        s.add(draft)
        s.flush()
        for n, seqs in enumerate(seqs_per_paragraph, start=1):
            s.add(Paragraph(firm_id=firm, draft_id=draft.id, seq=n, heading=f"回應第{n}段", text="內文",
                            claim_seqs=seqs))
        s.get(Round, rid).status = "drafted"
        return firm, draft.id


def test_key_points_map_claims_to_paragraphs(db):
    firm, draft_id = _setup(db, [[1], [2], []])
    with tenant_session(firm) as s:
        kp = key_points(s, draft_id)
    assert kp["legacy"] is False
    by_seq = {it["seq"]: it for it in kp["items"]}
    assert [p["no"] for p in by_seq[1]["paragraphs"]] == [1] and by_seq[1]["paragraphs"][0]["heading"] == "回應第1段"
    assert [p["no"] for p in by_seq[2]["paragraphs"]] == [2]
    assert by_seq[3]["selected"] and by_seq[3]["uncovered"]          # 已勾選但沒有段落回應
    assert not by_seq[4]["selected"] and not by_seq[4]["uncovered"]  # 沒勾選：不算漏
    assert [it["seq"] for it in kp["items"]] == [1, 2, 3, 4]


def test_legacy_draft_without_claim_seqs(db):
    firm, draft_id = _setup(db, [None, None])
    with tenant_session(firm) as s:
        kp = key_points(s, draft_id)
    assert kp["legacy"] is True
    assert all(it["paragraphs"] == [] for it in kp["items"])


def test_draft_view_includes_key_points(lawyer_client):
    client, firm, _ = lawyer_client
    _, rid = make_round(firm, our_side="defendant")
    with tenant_session(firm) as s:
        s.add(Claim(firm_id=firm, round_id=rid, seq=1, summary="被告未讓幹道車先行", quote=""))
        draft = Draft(firm_id=firm, round_id=rid)
        s.add(draft)
        s.flush()
        s.add(Paragraph(firm_id=firm, draft_id=draft.id, seq=1, heading="關於路權", text="內文", claim_seqs=[1]))
        draft_id = draft.id
    body = client.get(f"/api/drafts/{draft_id}").json()
    assert body["key_points"]["items"][0]["paragraphs"][0]["no"] == 1
