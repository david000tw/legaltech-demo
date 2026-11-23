"""開發用指令（以超級使用者連線，只在開發者本機使用）。

python -m app.cli create-firm --name NAME [--internal]
python -m app.cli create-member --firm NAME --email E --password P --role lawyer|assistant
                                [--bar-number N --bar-verified]
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from app.auth import hash_password
from app.db import admin_session
from app.models import Firm, Member


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
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main(sys.argv[1:])
