"""Run the evaluation and write eval/results/<time>.json and eval/REPORT.md.

  python scripts/eval.py                 all parts
  python scripts/eval.py --part auto     automatic metrics + number-error injection only
  python scripts/eval.py --part gold     re-score gold metrics from the latest results (no model calls)
  python scripts/eval.py --only q01,q03  --limit 5
  python scripts/eval.py --fresh         discard saved progress and start again

Progress is saved after every question (eval/results/partial.jsonl); a rerun skips finished
questions, so a dropped connection costs only the question in progress.
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import psycopg  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from evidex import evaluate  # noqa: E402
from evidex.llm import OllamaLLM  # noqa: E402

load_dotenv(ROOT / ".env")
ap = argparse.ArgumentParser()
ap.add_argument("--part", choices=["all", "auto", "gold"], default="all")
ap.add_argument("--only", default="")
ap.add_argument("--limit", type=int, default=0)
ap.add_argument("--fresh", action="store_true")
a = ap.parse_args()

E = ROOT / "eval"
questions = evaluate.load_questions(E / "questions.csv", E / "questions.jsonl")
if a.only:
    keep = set(a.only.split(","))
    questions = [q for q in questions if q["id"] in keep]
if a.limit:
    questions = questions[:a.limit]

import time  # noqa: E402


def connect():
    for attempt in range(30):
        try:
            return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, connect_timeout=10)
        except psycopg.OperationalError as e:
            print(f"    database not reachable ({e.__class__.__name__}); retry {attempt + 1}/30 in 20 s", flush=True)
            time.sleep(20)
    sys.exit("database unreachable; rerun later, finished questions are kept")


conn = connect()
llm = OllamaLLM()
started = datetime.now().strftime("%Y-%m-%d %H:%M")
spine_rows, spine_checked = conn.execute(
    "select count(*), count(*) filter (where doctor_checked) from assertion where status = 'accepted'").fetchone()
meta = {"started": started, "model": llm.model, "questions": len(questions),
        "spine_rows": spine_rows, "spine_checked": spine_checked}

if a.part == "gold":
    latest = sorted((E / "results").glob("*.json"))[-1]
    saved = json.loads(latest.read_text())
    results, auto, fid, inj = saved["results"], saved["auto"], saved["export_fidelity"], saved["injection"]
    meta = {**saved["meta"], "rescored": started}
    print(f"re-scoring gold labels on {latest.name}")
else:
    partial = E / "results" / "partial.jsonl"
    if a.fresh and partial.exists():
        partial.unlink()
    done = {}
    if partial.exists():
        for line in partial.read_text().splitlines():
            r = json.loads(line)
            done[r["id"]] = r
        print(f"resuming: {len(done)} question(s) already done", flush=True)
    results = []
    for q in questions:
        if q["id"] in done and done[q["id"]]["request"] == q["request"]:
            results.append(done[q["id"]])
            continue
        print(f"{q['id']} {q['request'][:60]} ...", flush=True)
        for attempt in range(3):
            try:
                r = evaluate.run_question(conn, llm, q)
                break
            except psycopg.OperationalError as e:
                print(f"    connection lost ({e.__class__.__name__}); reconnecting and retrying", flush=True)
                conn = connect()
        else:
            sys.exit("giving up after 3 connection failures; rerun to continue")
        r = json.loads(json.dumps(r, default=str))
        with partial.open("a") as f:
            f.write(json.dumps(r) + "\n")
        print(f"    -> {r['outcome']} in {r['total_s']} s, {len(r['claims'])} claims", flush=True)
        results.append(r)
    auto = evaluate.auto_metrics(results)
    fid = evaluate.export_fidelity(results)
    print("injection test ...", flush=True)
    try:
        inj = evaluate.injection_test(conn, llm, evaluate.make_injections(conn, results))
    except psycopg.OperationalError:
        conn = connect()
        inj = evaluate.injection_test(conn, llm, evaluate.make_injections(conn, results))

gold = evaluate.gold_metrics(questions, results) if a.part in ("all", "gold") else None
out = E / "results" / f"{datetime.now():%Y%m%d-%H%M}.json"
out.write_text(json.dumps({"meta": meta, "results": results, "auto": auto, "export_fidelity": fid,
                           "injection": inj, "gold": gold}, indent=1, default=str))
notes = E / "REPORT_NOTES.md"
(E / "REPORT.md").write_text(evaluate.report_md(meta, results, auto, fid, inj, gold)
                             + ("\n" + notes.read_text() if notes.exists() else ""))
print(f"wrote {out.relative_to(ROOT)} and eval/REPORT.md")
