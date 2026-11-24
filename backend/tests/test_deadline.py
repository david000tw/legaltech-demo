"""子專案丙 Task 4：上訴、抗告期限試算（設計 5.10）。行事曆以函式注入：True 放假、False 上班、None 沒涵蓋。"""
from datetime import date, timedelta

import pytest

from app.tools import DISCLAIMER
from app.tools.deadline import Service, compute, effective_date

TYPHOON = "颱風等臨時停止上班不在行事曆內，請自行確認"
UNCOVERED = "行事曆資料未涵蓋，只順延週末"
FAMILY_ERROR = "家事訴訟事件請選上訴；家事非訟事件請選抗告；或改用自訂天數"


def all_workdays(d: date) -> bool:
    return False


def weekends(d: date) -> bool:
    return d.weekday() >= 5


def _appeal(services, calendar=weekends, travel=0, local=False, kind="appeal", procedure="civil", days=None):
    return compute(kind, procedure, services, travel, local, days, calendar)


def test_effective_dates():
    d = date(2026, 3, 1)
    assert effective_date(d, "direct")[0] == date(2026, 3, 1)
    assert effective_date(d, "deposit")[0] == date(2026, 3, 11)
    assert effective_date(d, "public")[0] == date(2026, 3, 21)
    assert effective_date(d, "foreign_public")[0] == date(2026, 4, 30)
    assert effective_date(d, "continued_public")[0] == date(2026, 3, 2)
    step = effective_date(d, "deposit")[1]
    assert "民事訴訟法第138條第2項" in step.basis and step.value == "民國115年3月11日（星期三）"


def test_deposit_example_from_spec():
    r = _appeal([Service("party", date(2026, 3, 1), "deposit")], calendar=all_workdays)
    assert r.due == date(2026, 3, 31)
    labels = [s.label for s in r.steps]
    order = [next(i for i, x in enumerate(labels) if key in x) for key in ("生效", "起算", "屆滿")]
    assert order == sorted(order)
    assert any("民事訴訟法第440條" in s.basis for s in r.steps)
    assert any("民法第120條第2項" in s.basis for s in r.steps)
    assert r.steps[-1].value == "民國115年3月31日（星期二）"


def test_earlier_service_counts():
    r = _appeal([Service("party", date(2026, 3, 1), "deposit"), Service("attorney", date(2026, 3, 5), "direct")],
                calendar=all_workdays)
    assert r.due == date(2026, 3, 25)
    assert any("本人與訴訟代理人中，生效日較早者" in s.basis for s in r.steps)


def test_attorney_local_zero_travel():
    far = _appeal([Service("party", date(2026, 3, 1), "direct")], calendar=all_workdays, travel=5)
    local = _appeal([Service("party", date(2026, 3, 1), "direct")], calendar=all_workdays, travel=5, local=True)
    assert far.due == date(2026, 3, 26)
    assert local.due == date(2026, 3, 21)
    assert any("第162條但書" in s.basis for s in local.steps)


def test_deadline_weekend_postponed():
    r = _appeal([Service("party", date(2026, 3, 15), "direct")])  # 屆滿 4/4（星期六）
    assert r.due == date(2026, 4, 6)
    assert any("民法第122條" in s.basis for s in r.steps)


def test_deadline_postponed_through_long_holiday():
    holidays = {date(2026, 4, 1) + timedelta(i) for i in range(5)}  # 4/1～4/5 連假
    r = _appeal([Service("party", date(2026, 3, 12), "direct")], calendar=lambda d: d in holidays or d.weekday() >= 5)
    assert r.due == date(2026, 4, 6)


def test_deadline_makeup_saturday_not_postponed():
    makeup = date(2026, 4, 4)  # 補行上班的星期六
    r = _appeal([Service("party", date(2026, 3, 15), "direct")],
                calendar=lambda d: False if d == makeup else d.weekday() >= 5)
    assert r.due == makeup
    assert not any("民法第122條" in s.basis for s in r.steps)


def test_service_period_not_postponed():
    r = _appeal([Service("party", date(2026, 3, 1), "deposit")],
                calendar=lambda d: d == date(2026, 3, 11) or d.weekday() >= 5)  # 寄存第 10 日放假
    assert r.due == date(2026, 3, 31)


def test_deadline_outside_calendar_weekend_only():
    r = _appeal([Service("party", date(2026, 3, 15), "direct")], calendar=lambda d: None)
    assert r.due == date(2026, 4, 6)
    assert UNCOVERED in r.notes
    covered = _appeal([Service("party", date(2026, 3, 15), "direct")])
    assert UNCOVERED not in covered.notes


def test_deadline_notes_typhoon():
    r = _appeal([Service("party", date(2026, 3, 1), "direct")])
    assert TYPHOON in r.notes and DISCLAIMER in r.notes


def test_family_procedures_basis():
    fam = _appeal([Service("party", date(2026, 3, 1), "direct")], procedure="family_litigation")
    assert any("家事事件法第51條" in s.basis for s in fam.steps)
    assert fam.due == date(2026, 3, 23)  # 3/21 是星期六
    non = _appeal([Service("party", date(2026, 3, 1), "direct")], kind="interlocutory",
                  procedure="family_nonlitigation")
    assert any("第92條、第93條" in s.basis for s in non.steps)
    assert non.due == date(2026, 3, 11)
    civil = _appeal([Service("party", date(2026, 3, 1), "direct")], kind="interlocutory")
    assert any("民事訴訟法第487條" in s.basis for s in civil.steps)
    for kind, procedure in (("appeal", "family_nonlitigation"), ("interlocutory", "family_litigation")):
        with pytest.raises(ValueError) as e:
            _appeal([Service("party", date(2026, 3, 1), "direct")], kind=kind, procedure=procedure)
        assert str(e.value) == FAMILY_ERROR


def test_custom_days():
    r = _appeal([Service("party", date(2026, 3, 2), "direct")], calendar=all_workdays, kind="custom", days=7)
    assert r.due == date(2026, 3, 9)
    with pytest.raises(ValueError):
        _appeal([Service("party", date(2026, 3, 2), "direct")], kind="custom", days=None)
    with pytest.raises(ValueError):
        _appeal([], calendar=all_workdays)
