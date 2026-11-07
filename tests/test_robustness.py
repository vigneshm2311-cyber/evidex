"""Background jobs, wording checks and planned-but-unused spine rows."""
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_agents import GOOD_CLAIMS, FakeLLM, conn, run  # noqa: E402,F401

from evidex import drafts, jobs  # noqa: E402
from evidex.agents import steps  # noqa: E402

ROW = {"id": "dx-ogtt", "subject_name": "Diabetes mellitus", "predicate": "diagnosed_by",
       "object_name": "2-hour plasma glucose during 75-g oral glucose tolerance test", "operator": ">=",
       "value": 200, "value_high": None, "unit": "mg/dL", "population_id": None, "population_name": None,
       "condition_text": ""}


def _wait(job, timeout=5):
    t = time.time()
    while not job.done and time.time() - t < timeout:
        time.sleep(0.01)
    return job


# ---- background jobs ----

def test_job_runs_in_background_and_releases_the_lock():
    lock = threading.Lock()
    lock.acquire()
    job = _wait(jobs.start(lambda: iter([{"step": "a"}, {"step": "done"}]), on_finish=lock.release))
    assert job.done and [e["step"] for e in job.events] == ["a", "done"]
    assert lock.acquire(blocking=False)
    assert jobs.get(job.id) is job and job not in jobs.running()


def test_job_error_is_kept_and_lock_still_released():
    lock = threading.Lock()
    lock.acquire()

    def boom():
        yield {"step": "scope_guard"}
        raise RuntimeError("model went away")
    job = _wait(jobs.start(boom, on_finish=lock.release))
    assert job.error == "RuntimeError: model went away"
    assert job.events[-1]["status"] == "error" and lock.acquire(blocking=False)


# ---- wording checks ----

def test_cut_off_word_is_flagged():
    issues = steps.wording_issues("Diabetes mell, is diagnosed by 2-hour plasma glucose ≥ 200 mg/dL.", [ROW])
    assert issues == ["wording: 'mell' looks cut off (did you mean 'mellitus'?)"]


def test_run_together_words_and_repeats_are_flagged():
    botched = ("Diabetes is diagnosed when the 2-hour plasma glucose is ≥ 200 mDiabetes is diagnosed when "
               "the 2-hour plasma glucose is ≥ 200 mg/dL.g/dL.")
    issues = " ".join(steps.wording_issues(botched, [ROW]))
    assert "'mDiabetes'" in issues and "repeated phrase" in issues


def test_clean_and_parallel_wording_passes():
    for t in ["Diabetes is diagnosed when the 2-hour plasma glucose during a 75-g OGTT is ≥ 200 mg/dL.",
              "Overweight is BMI at or above the 85th percentile; obesity is BMI at or above the 95th percentile.",
              "Hypertension (≥ 130/80 mmHg), e.g. in adults, is a risk factor; HbA1c is unreliable."]:
        assert steps.wording_issues(t, [ROW]) == [], t


def test_wording_fault_blocks_approval_via_verifier():
    res = steps.check_text("Diabetes mell, is diagnosed by 2-hour plasma glucose ≥ 200 mg/dL.", [ROW])
    assert any(i.startswith("wording:") for i in res["issues"])


# ---- planned rows that no claim used ----

def test_unused_planned_rows_are_saved_with_the_draft(conn):
    plan = {"title": "Diagnosing diabetes", "not_covered": [],
            "sections": [{"heading": "Criteria", "assertion_ids": ["dx-a1c", "dx-fpg", "pre-a1c"]},
                         {"heading": "Caveats", "assertion_ids": ["a1c-sickle"]}]}
    ev = run(conn, FakeLLM(plan=plan, claims=GOOD_CLAIMS))
    d = drafts.load(conn, ev[-1]["data"]["draft_id"])
    assert d["unused_rows"] == {"Criteria": ["pre-a1c"]}
