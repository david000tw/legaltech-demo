"""子專案丙 Task 4：裁判費試算（設計 5.10）。純函式，不用 AI。"""
import pytest

from app.tools import DISCLAIMER, court_fee


@pytest.mark.parametrize("amount,fee", [
    (100_000, 1_500), (50_000, 1_500), (1_000_000, 13_200), (10_000_000, 118_500), (100_000_000, 910_500),
    (1_000_000_000, 7_840_500), (15_000_000, 162_500), (1_010_000, 13_317), (1_000_001, 13_317),
])
def test_first_instance_fee(amount, fee):
    r = court_fee.first_instance(amount)
    assert r.fee == fee
    assert r.steps and all(s.basis for s in r.steps)
    assert "民事訴訟法第77條之13、第77條之27" in r.steps[-1].basis
    assert DISCLAIMER in r.notes


def test_appeal_fee_rounds_half_up():
    r = court_fee.appeal(1_010_000)  # 13,317 × 1.5 = 19,975.5
    assert r.fee == 19_976
    assert "尾數處理以法院通知為準" in r.notes and DISCLAIMER in r.notes
    assert "第77條之16" in r.steps[-1].basis


def test_partial_appeal_and_third_instance(lawyer_client):
    client, _, _ = lawyer_client
    r = client.post("/api/tools/court-fee",
                    json={"property": True, "amount": 5_000_000, "instance": 3, "appeal_amount": 1_000_000})
    assert r.status_code == 200
    body = r.json()
    assert body["fee"] == 19_800
    assert "尾數處理以法院通知為準" in body["notes"]
    r = client.post("/api/tools/court-fee", json={"property": True, "amount": 1_000_000, "instance": 1})
    assert r.json()["fee"] == 13_200


def test_non_property_suit(lawyer_client):
    client, _, _ = lawyer_client
    r = client.post("/api/tools/court-fee", json={"property": False, "amount": 0, "instance": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["fee"] is None
    assert len(body["steps"]) == 1 and "依民事訴訟法第77條之14" in body["steps"][0]["value"]
