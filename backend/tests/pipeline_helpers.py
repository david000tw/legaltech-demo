"""AI 流程測試共用：建立案件、輪次與文件（以 tenant_session 走正式路徑）。"""
from uuid import UUID

from app.db import tenant_session
from app.ingest.service import ingest_document
from app.models import Case, Round

OPPONENT_P1 = "民事答辯狀\n㈠原告行經無號誌路口時亦未減速慢行，未作隨時停車之準備，對於損害之發生與有過失。"
OPPONENT_P2 = "㈡原告請求慰撫金15萬元過高，請鈞院審酌兩造身分、地位及原告傷勢，予以酌減。"


def make_round(firm_id: UUID, our_side: str = "plaintiff",
               opponent_pages: tuple[str, ...] = (OPPONENT_P1, OPPONENT_P2),
               evidence: dict[str, str] | None = None) -> tuple[UUID, UUID]:
    """回傳 (case_id, round_id)。evidence 為 {檔名: 內文}，依檔名順序上傳。"""
    with tenant_session(firm_id) as s:
        case = Case(firm_id=firm_id, title="測試案", our_side=our_side)
        s.add(case)
        s.flush()
        rnd = Round(firm_id=firm_id, case_id=case.id, seq=1, status="created")
        s.add(rnd)
        s.flush()
        for name, body in sorted((evidence or {}).items()):
            ingest_document(s, firm_id, case.id, "evidence", name, body.encode("utf-8"))
        if opponent_pages:
            ingest_document(s, firm_id, case.id, "opponent_brief", "答辯狀.txt",
                            "\f".join(opponent_pages).encode("utf-8"), rnd.id)
        return case.id, rnd.id


def run_two_stage(firm_id: UUID, round_id: UUID, settings, provider=None, provider_factory=None,
                  member_id: UUID | None = None) -> None:
    """依序跑拆解、起草（全部勾選，照舊測試的「一鍵執行」）；第一段沒停在待確認就不跑第二段。"""
    from app.pipeline.run import run_draft, run_extract, start_stage
    kw = {"provider": provider} if provider_factory is None else {"provider_factory": provider_factory}
    with tenant_session(firm_id) as s:
        start_stage(s, round_id, "extract")
    run_extract(firm_id, round_id, member_id, settings, **kw)
    with tenant_session(firm_id) as s:
        if s.get(Round, round_id).status != "extracted":
            return
        start_stage(s, round_id, "draft")
    run_draft(firm_id, round_id, member_id, settings, **kw)


def api_run(client, round_id):
    """透過 API 依序呼叫兩段（全部勾選）；回傳最後一個請求的回應。"""
    r = client.post(f"/api/rounds/{round_id}/extract")
    if r.status_code == 202 and client.get(f"/api/rounds/{round_id}").json()["status"] == "extracted":
        r = client.post(f"/api/rounds/{round_id}/draft")
    return r
