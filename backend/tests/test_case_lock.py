"""案件列的鎖不能擋住「新增參照案件的資料」（外鍵檢查只需要 KEY SHARE）。

段落鎖住後的同一交易會新增支持度檢查列（外鍵指向案件，Postgres 會對案件列取 KEY SHARE）；
另一邊辨識修正先鎖案件列再鎖段落。案件列若用 FOR UPDATE，兩邊互等就死結；改用 FOR NO KEY UPDATE
（案件鎖之間照樣互斥）就不會擋外鍵檢查。
"""
import hashlib

import pytest
from sqlalchemy import text

from app.db import tenant_session
from app.models import SupportCheck
from app.ocr.service import lock_page
from tests.helpers import create_firm
from tests.pipeline_helpers import make_round
from tests.test_ocr_correction import ocr_page


def test_case_lock_does_not_block_rows_that_reference_the_case(db):
    firm = create_firm("甲事務所")
    case_id, _ = make_round(firm)
    _, pid = ocr_page(firm, case_id, "醫療費用合計新臺幣5,870元", state="read")
    with tenant_session(firm) as holder:
        lock_page(holder, pid)  # 修正視窗：鎖著案件列與頁列
        with tenant_session(firm) as s:
            s.execute(text("SET LOCAL lock_timeout = '1s'"))
            s.add(SupportCheck(firm_id=firm, case_id=case_id, fingerprint=hashlib.sha256(b"x").hexdigest(),
                               kind="evidence", label="原證1", quote="q", sentence="s", source="src"))
            s.flush()  # 外鍵檢查要對案件列取 KEY SHARE；不能等修正視窗結束


def test_case_locks_still_exclude_each_other(db):
    firm = create_firm("甲事務所")
    case_id, _ = make_round(firm)
    _, pid = ocr_page(firm, case_id, "醫療費用合計新臺幣5,870元", state="read")
    from sqlalchemy.exc import OperationalError
    with tenant_session(firm) as holder:
        lock_page(holder, pid)
        with pytest.raises(OperationalError, match="lock timeout"):
            with tenant_session(firm) as s:
                s.execute(text("SET LOCAL lock_timeout = '1s'"))
                lock_page(s, pid)
