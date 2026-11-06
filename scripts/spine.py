"""Spine command line.

  python scripts/spine.py migrate                 apply migrations/*.sql to DATABASE_URL
  python scripts/spine.py check  [--dir DIR]      validate CSVs only, write nothing
  python scripts/spine.py import [--dir DIR] [--actor NAME]
  python scripts/spine.py emit-sql [--dir DIR] [--out FILE]   SQL for loading Supabase
  python scripts/spine.py stats                    row counts in the database
  python scripts/spine.py export [--dir DIR]       write the database back to CSV (after app edits)
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evidex.spine import importer  # noqa: E402


def connect():
    import psycopg
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set (see .env.example)")
    return psycopg.connect(url)


def cmd_migrate(_):
    with connect() as conn:
        for f in sorted((ROOT / "migrations").glob("*.sql")):
            conn.execute(f.read_text())
            print(f"applied {f.name}")


def _load_or_exit(directory: Path, conn=None):
    spine, problems = importer.load(directory)
    known = importer.known_concepts(conn) if conn else None
    problems += importer.validate(spine, known)
    if problems:
        print(f"{len(problems)} problem(s) found; nothing was written:\n")
        for p in problems:
            print("  -", p)
        sys.exit(1)
    return spine


def cmd_check(a):
    spine = _load_or_exit(Path(a.dir))
    print(f"OK: {len(spine.concepts)} concepts, {len(spine.assertions)} assertions, no problems")


def cmd_import(a):
    with connect() as conn:
        spine = _load_or_exit(Path(a.dir), conn)
        n = importer.write(conn, spine, a.actor)
        print(f"imported {n['concepts']} concepts and {n['assertions']} assertions (actor: {a.actor})")


def cmd_emit_sql(a):
    spine = _load_or_exit(Path(a.dir))
    sql = importer.to_sql(spine, a.actor)
    Path(a.out).write_text(sql)
    print(f"wrote {a.out} ({len(sql.splitlines())} statements)")


def cmd_stats(_):
    with connect() as conn:
        for q, label in [
            ("select count(*) from concept", "concepts"),
            ("select count(*) from assertion", "assertions"),
            ("select status || ': ' || count(*) from assertion group by status", "by status"),
            ("select 'section ' || section || ': ' || count(*) from source_anchor group by section order by 1", "by section"),
            ("select count(*) from assertion where doctor_checked", "doctor-checked"),
            ("select count(*) from source_anchor where locator is null", "anchors missing rec/table (TODO doctor)"),
            ("select count(*) from assertion where grade is null", "assertions missing grade (TODO doctor)"),
            ("select count(*) from change_log", "change_log rows"),
        ]:
            rows = [r[0] for r in conn.execute(q).fetchall()]
            print(f"{label:42} {', '.join(map(str, rows))}")


def cmd_export(a):
    from evidex.spine import api

    with connect() as conn:
        files = api.export_csv(conn)
    d = Path(a.dir)
    d.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (d / name).write_text(text)
        print(f"wrote {d / name} ({text.count(chr(10)) - 1} rows)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate").set_defaults(fn=cmd_migrate)
    sub.add_parser("stats").set_defaults(fn=cmd_stats)
    for name, fn in [("check", cmd_check), ("import", cmd_import), ("emit-sql", cmd_emit_sql),
                     ("export", cmd_export)]:
        p = sub.add_parser(name)
        p.add_argument("--dir", default=str(ROOT / "data" / "spine"))
        p.add_argument("--actor", default="claude")
        if name == "emit-sql":
            p.add_argument("--out", default="spine_load.sql")
        p.set_defaults(fn=fn)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
