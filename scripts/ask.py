"""Run one request through the pipeline from the terminal and print each step.

  python scripts/ask.py "Diagnosing diabetes in Indian adults" [--artifact handout] [--audience "..."]
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import psycopg  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from evidex import pipeline  # noqa: E402
from evidex.agents import steps  # noqa: E402
from evidex.llm import OllamaLLM  # noqa: E402

load_dotenv(ROOT / ".env")
ap = argparse.ArgumentParser()
ap.add_argument("request")
ap.add_argument("--artifact", default="slides", choices=list(steps.ARTIFACTS))
ap.add_argument("--audience", default=steps.AUDIENCES[0])
a = ap.parse_args()

conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)
for e in pipeline.run(conn, OllamaLLM(), a.request, a.artifact, a.audience):
    print(f"\n== {e['step']} [{e['status']}] {e['ms'] / 1000:.1f}s")
    d = e["data"]
    if e["step"] == "scope_guard":
        print({k: d[k] for k in ("label", "decided_by", "reason", "general_query")})
    elif e["step"] == "spine_lookup":
        print(d.get("message") or f"{len(d['assertion_ids'])} candidate rows")
    elif e["step"] == "planner":
        print(d.get("message", ""), "| title:", d["title"], "| not covered:", d["not_covered"])
        for s in d["sections"]:
            print("  -", s["heading"], s["assertion_ids"])
    elif e["step"].startswith("synthesizer"):
        if d["error"]:
            print("ERROR", d["error"])
        for c in d["claims"]:
            v = c.get("verdict") or c
            mark = "OK " if v.get("ok") else "FLAG"
            print(f"  {mark} {c['text']}  {c['assertion_ids']}")
            for i in v.get("issues", []):
                print("       !", i)
    elif e["step"] == "done":
        print("run", e["run_id"], "| draft", d.get("draft_id"))
