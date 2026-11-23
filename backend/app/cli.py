"""開發用指令（以超級使用者連線，只在開發者本機使用）。

python -m app.cli create-firm --name NAME [--internal]
python -m app.cli create-member --firm NAME --email E --password P --role lawyer|assistant
                                [--bar-number N --bar-verified]
python -m app.cli import-testcase <dir> --firm NAME
python -m app.cli load-seed [--resplit]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from app.auth import hash_password
from app.corpus.seed_loader import load_seed
from app.db import admin_session
from app.ingest.service import ingest_document
from app.models import Case, Firm, Member, Round


def _firm_by_name(s, name: str) -> Firm:
    f = s.scalars(select(Firm).where(Firm.name == name)).one_or_none()
    if f is None:
        raise SystemExit(f"找不到事務所：{name}")
    return f


def cmd_create_firm(a) -> None:
    with admin_session() as s:
        f = Firm(name=a.name, is_internal_test=a.internal)
        s.add(f)
        s.flush()
        print(f"firm_id={f.id}")


def cmd_create_member(a) -> None:
    with admin_session() as s:
        f = _firm_by_name(s, a.firm)
        m = Member(firm_id=f.id, email=a.email.strip().lower(), password_hash=hash_password(a.password),
                   display_name=a.display_name or a.email.split("@")[0], role=a.role,
                   bar_number=a.bar_number, bar_verified=bool(a.bar_verified and a.role == "lawyer"))
        s.add(m)
        s.flush()
        print(f"member_id={m.id}")


def import_testcase(folder: Path, firm_name: str) -> tuple[UUID, UUID]:
    """匯入合成測試案件：建案件、依檔名順序上傳卷證、建第 1 輪並掛上對造書狀。"""
    folder = Path(folder)
    meta = json.loads((folder / "case.json").read_text(encoding="utf-8"))
    with admin_session() as s:
        firm = _firm_by_name(s, firm_name)
        case = Case(firm_id=firm.id, title=meta["title"], our_side=meta["our_side"],
                    case_type=meta.get("case_type", "traffic_tort"))  # 計畫 1B：試跑時這欄被忽略
        s.add(case)
        s.flush()
        rnd = Round(firm_id=firm.id, case_id=case.id, seq=1, status="created")
        s.add(rnd)
        s.flush()
        for kind, sub, round_id in (("evidence", "evidence", None), ("opponent_brief", "opponent", rnd.id)):
            for f in sorted((folder / sub).glob("*")):
                if f.is_file():
                    ingest_document(s, firm.id, case.id, kind, f.name, f.read_bytes(), round_id)
        return case.id, rnd.id


def cmd_import_testcase(a) -> None:
    case_id, round_id = import_testcase(Path(a.dir), a.firm)
    print(f"case_id={case_id}")
    print(f"round_id={round_id}")


SEED_DIR = Path(__file__).resolve().parent.parent / "seed"


def cmd_load_seed(a) -> None:
    with admin_session() as s:
        j, art = load_seed(s, SEED_DIR, resplit=a.resplit)
    print(f"judgments_added={j} articles_added={art}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("create-firm")
    f.add_argument("--name", required=True)
    f.add_argument("--internal", action="store_true")
    f.set_defaults(func=cmd_create_firm)

    m = sub.add_parser("create-member")
    m.add_argument("--firm", required=True)
    m.add_argument("--email", required=True)
    m.add_argument("--password", required=True)
    m.add_argument("--role", choices=["lawyer", "assistant"], required=True)
    m.add_argument("--display-name")
    m.add_argument("--bar-number")
    m.add_argument("--bar-verified", action="store_true")
    m.set_defaults(func=cmd_create_member)

    t = sub.add_parser("import-testcase")
    t.add_argument("dir")
    t.add_argument("--firm", required=True)
    t.set_defaults(func=cmd_import_testcase)

    ls = sub.add_parser("load-seed")
    ls.add_argument("--resplit", action="store_true")
    ls.set_defaults(func=cmd_load_seed)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main(sys.argv[1:])
