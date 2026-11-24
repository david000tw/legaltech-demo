"""匯入行政機關辦公日曆（seed/calendar/*.csv，來源見該資料夾 SOURCE.md），以日期 upsert，可重跑。"""
from __future__ import annotations

import csv
import io
from datetime import datetime
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import WorkCalendarDay

FLAGS = {"0": False, "2": True}  # 是否放假：0 上班、2 放假


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp950")  # 2025 年的檔案是 Big5


def load_calendar(admin_s: Session, folder: Path) -> int:
    rows = []
    for f in sorted(Path(folder).glob("*.csv")):
        for row in csv.reader(io.StringIO(_decode(f.read_bytes()))):
            if not row or not row[0].strip().isdigit():
                continue  # 標題列
            flag = row[2].strip()
            if flag not in FLAGS:
                raise ValueError(f"{f.name}：無法辨識的放假代碼 {flag!r}（{row[0]}）")
            rows.append({"day": datetime.strptime(row[0].strip(), "%Y%m%d").date(), "is_holiday": FLAGS[flag],
                         "note": row[3].strip() if len(row) > 3 else ""})
    if rows:
        stmt = insert(WorkCalendarDay).values(rows)
        admin_s.execute(stmt.on_conflict_do_update(
            index_elements=["day"], set_={"is_holiday": stmt.excluded.is_holiday, "note": stmt.excluded.note}))
    admin_s.flush()
    return len(rows)
