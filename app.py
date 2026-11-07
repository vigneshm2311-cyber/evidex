"""Evidex: create drafts from the diabetes spine, review and sign them off, and browse/edit the spine.

Run:  .venv/bin/streamlit run app.py
"""
from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation

import psycopg
import streamlit as st
from dotenv import load_dotenv

from evidex import drafts, export, jobs, pipeline
from evidex.agents import steps
from evidex.spine import api
from evidex.spine.vocab import OPERATORS, STATUSES

load_dotenv()
st.set_page_config(page_title="Evidex", page_icon="📚", layout="wide")
try:  # Streamlit Cloud keeps settings in st.secrets; copy plain values into the environment
    for _k, _v in st.secrets.items():
        if isinstance(_v, (str, int, float)) and _k not in os.environ:
            os.environ[_k] = str(_v)
except Exception:  # no secrets file when running locally
    pass

HOSTED = os.environ.get("LLM_PROVIDER", "ollama").lower() == "groq"
MODEL_SHORT = ("a hosted model; usually 1–3 minutes" if HOSTED
               else "a local model on this Mac; usually 1–6 minutes")
MODEL_ABOUT = ("Runs on a **hosted open model (Qwen3.8-27B on Groq)**: your request and the spine rows are sent to it, "
               "so enter general topics only, never patient details." if HOSTED else
               "Runs on a **local model on the team's Mac** (no patient data, no external AI service).")


BLOCKS = {
    ("2", "a"): "A · Diagnostic criteria",
    ("2", "b"): "B · Prediabetes",
    ("2", "c"): "C · When A1C is unreliable",
    ("2", "d"): "D · Screening adults without symptoms",
    ("2", "e"): "E · Classification and specific types",
    ("2", "f"): "F · Type 1 diabetes staging",
    ("2", "g"): "G · Children and adolescents",
    ("3", ""): "Prevention or delay of diabetes",
}
TYPE_COLOUR = {
    "Condition": "#cfe2ff", "Test": "#d1f0d6", "Population": "#fde2c4", "RiskFactor": "#f6d0d6",
    "Intervention": "#e3d7f7", "DrugClass": "#e3d7f7", "Outcome": "#d6eef2", "Rule": "#eeeeee",
    "Threshold": "#fff3b0", "Complication": "#f6d0d6",
}


# ---------- data ----------

PING_EVERY_S = 30


def fresh_connection():
    # Keepalives and a TCP user timeout make a connection that died with the network
    # fail within seconds instead of hanging every page until the OS gives up.
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, connect_timeout=10,
                           keepalives=1, keepalives_idle=20, keepalives_interval=5,
                           keepalives_count=3, tcp_user_timeout=15000)


@st.cache_resource
def _connection():
    return fresh_connection()


def conn():
    import time
    c = _connection()
    stale = time.monotonic() - getattr(c, "_evidex_ping", 0) > PING_EVERY_S
    if not (c.closed or c.broken) and stale:
        try:
            c.execute("select 1")
            c._evidex_ping = time.monotonic()
        except psycopg.Error:
            pass
    if c.closed or c.broken:
        try:
            c.close()
        except Exception:
            pass
        _connection.clear()
        c = _connection()
        c._evidex_ping = time.monotonic()
    return c


@st.cache_data(ttl=300, show_spinner="Loading the spine…")
def load():
    return api.all_concepts(conn()), api.all_assertions(conn())


@st.cache_data(ttl=300, show_spinner=False)
def export_files():
    return api.export_csv(conn())


def block_of(a: dict) -> tuple[str, str]:
    parts = a["id"].split("-")  # ada26-s2-a-...
    sec = a["section"]
    if sec == "2" and len(parts) > 2 and len(parts[2]) == 1:
        return ("2", parts[2])
    return (sec, "")


# ---------- access ----------

def _session_token(code: str) -> str:
    """Kept in the URL so a page refresh does not log an evaluator out. Keyed with a server
    secret, so the access code cannot be worked out from it."""
    import hashlib
    import hmac
    secret = os.environ.get("SESSION_SECRET") or os.environ.get("DOCTOR_CODE") or code
    return hmac.new(secret.encode(), b"evaluator:" + code.encode(), hashlib.sha256).hexdigest()[:24]


def gate():
    """ACCESS_CODE: evaluators (everything except editing the spine).
    DOCTOR_CODE: the doctor (can also edit the spine). Without codes set: local doctor mode."""
    code, doctor = os.environ.get("ACCESS_CODE"), os.environ.get("DOCTOR_CODE")
    if st.session_state.get("authorised"):
        return
    if not code and not doctor:
        st.session_state.update(authorised=True, role="doctor")
        return
    if code and st.query_params.get("s") == _session_token(code):
        st.session_state.update(authorised=True, role="evaluator")
        return
    st.title("Evidex")
    st.caption("Doctor productivity and knowledge assistant · Health-a-thon 2026 · Team Synexis (AZYZ69)")
    st.markdown("Drafts **teaching slides and handouts** on diabetes diagnosis, classification, screening and "
                "prevention from a doctor-curated knowledge base anchored to the *ADA Standards of Care 2026*. "
                "Every claim is cited and checked, and nothing is exported until a doctor signs off.")
    with st.form("login"):
        entered = st.text_input("Access code", type="password", help="Given in the submission form.")
        submitted = st.form_submit_button("Enter", type="primary")
    if submitted and entered:
        entered = entered.strip()
        if doctor and entered == doctor:
            st.session_state.update(authorised=True, role="doctor")
            st.rerun()
        if code and entered == code:
            st.session_state.update(authorised=True, role="evaluator")
            st.query_params["s"] = _session_token(code)
            st.rerun()
        st.error("That code is not right.")
    st.stop()


def is_doctor() -> bool:
    return st.session_state.get("role") == "doctor"


# ---------- shared limits for a public link ----------

RUNS_PER_HOUR = int(os.environ.get("RUNS_PER_HOUR", "10"))


@st.cache_resource
def model_lock():
    """One model job at a time across all visitors (the local model can only do one)."""
    import threading
    return threading.Lock()


def run_allowed() -> str | None:
    import time as _t
    now = _t.time()
    recent = [t for t in st.session_state.get("run_times", []) if now - t < 3600]
    st.session_state.run_times = recent
    if len(recent) >= RUNS_PER_HOUR:
        return f"Limit reached: {RUNS_PER_HOUR} drafts per hour per visitor. Please try again later."
    return None


# ---------- rendering ----------

def badges(a: dict) -> str:
    b = [f"`{a['status']}`",
         f"grade **{a['grade']}**" if a["grade"] else "grade :orange[TODO(doctor)]",
         ":green[✔ checked against 2026 text]" if a["doctor_checked"] else ":orange[○ not yet checked]"]
    if a["reviewed_by"]:
        b.append(f"reviewed by {a['reviewed_by']}")
    return " · ".join(b)


def _dec(s: str, label: str):
    s = (s or "").strip()
    if not s:
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        raise ValueError(f"{label} must be a number, not '{s}'")


def edit_form(a: dict, ctx: str):
    with st.form(f"edit-{ctx}-{a['id']}", border=False):
        c1, c2, c3, c4 = st.columns([1, 1.4, 1, 1.2])
        grade = c1.text_input("Grade", a["grade"] or "", key=f"g-{ctx}-{a['id']}")
        locator = c2.text_input("Rec / Table", a["locator"] or "", placeholder="Rec 2.1 or Table 2.2",
                                key=f"l-{ctx}-{a['id']}")
        page = c3.text_input("Page", a["page"] or "", placeholder="S27", key=f"p-{ctx}-{a['id']}")
        checked = c4.checkbox("Checked against my 2026 copy", a["doctor_checked"], key=f"c-{ctx}-{a['id']}")

        st.caption("Value, status and wording: change only if the 2026 text differs")
        if True:
            ops = [""] + sorted(OPERATORS)
            d1, d2, d3, d4, d5 = st.columns([1, 1, 1, 1.3, 1.3])
            op = d1.selectbox("Operator", ops, ops.index(a["operator"] or ""), key=f"o-{ctx}-{a['id']}")
            val = d2.text_input("Value (low)", api._fmt_num(a["value"]), key=f"v-{ctx}-{a['id']}")
            high = d3.text_input("High (ranges)", api._fmt_num(a["value_high"]), key=f"h-{ctx}-{a['id']}")
            unit = d4.text_input("Unit", a["unit"] or "", key=f"u-{ctx}-{a['id']}")
            statuses = sorted(STATUSES)
            status = d5.selectbox("Status", statuses, statuses.index(a["status"]), key=f"s-{ctx}-{a['id']}")
            cond = st.text_area("Condition / qualifier (your words, not pasted ADA text)",
                                a["condition_text"] or "", key=f"t-{ctx}-{a['id']}", height=70)
            notes = st.text_input("Notes", a["notes"] or "", key=f"n-{ctx}-{a['id']}")

        if not st.form_submit_button("Save", type="primary"):
            return
        who = st.session_state.get("reviewer", "").strip()
        if not who:
            st.error("Enter your name in the sidebar first; every edit is logged with it.")
            return
        try:
            new = {
                "grade": grade.strip() or None, "locator": locator.strip() or None,
                "page": page.strip() or None, "doctor_checked": checked,
                "operator": op or None, "value": _dec(val, "Value"),
                "value_high": _dec(high, "High"), "unit": unit.strip() or None,
                "status": status, "condition_text": cond.strip() or None,
                "notes": notes.strip() or None,
            }
            old = {k: a[k] for k in new}
            changes = {k: v for k, v in new.items() if v != old[k]}
            if not changes:
                st.info("Nothing changed.")
                return
            api.update_assertion(conn(), a["id"], who, changes)
        except ValueError as e:
            st.error(str(e))
            return
        except psycopg.Error as e:
            msg = (e.diag.message_primary or str(e)) if hasattr(e, "diag") else str(e)
            st.error(f"Not saved; the database rejected it: {msg}")
            return
        load.clear()
        export_files.clear()
        st.session_state.flash = f"Saved {a['id']} ({', '.join(changes)})"
        st.rerun()


def card(a: dict, ctx: str, editable: bool = True):
    with st.container(border=True):
        st.markdown(f"**{api.statement(a)}**")
        if a["condition_text"]:
            st.caption(a["condition_text"])
        st.caption(f"📍 {api.anchor_text(a)}  ·  `{a['id']}`")
        st.markdown(badges(a))
        if a["notes"]:
            (st.warning if "LOWER CONFIDENCE" in a["notes"] else st.caption)(a["notes"])
        if editable and is_doctor():
            with st.expander("Check / edit"):
                edit_form(a, ctx)
                if st.checkbox("Show change history", key=f"hist-{ctx}-{a['id']}"):
                    hist = api.change_history(conn(), a["id"])
                    st.dataframe(
                        [{"when": h["at"].strftime("%d %b %H:%M"), "who": h["actor"],
                          "action": h["action"]} for h in hist],
                        hide_index=True, use_container_width=True)


def order_key(a: dict):
    for i, (_, preds) in enumerate(api.OUTLINE_ORDER):
        if a["predicate"] in preds:
            return (i, preds.index(a["predicate"]), a["id"])
    return (99, 0, a["id"])


# ---------- pages ----------

def encyclopedia(concepts, assertions):
    by_id = {c["id"]: c for c in concepts}
    counts = {}
    for a in assertions:
        for k in ("subject_id", "object_id", "population_id"):
            if a[k]:
                counts[a[k]] = counts.get(a[k], 0) + 1
    left, mid = st.columns([1, 2])
    q = left.text_input("Search", placeholder="HbA1c, gestational, BMI, metformin…")
    if q:
        ql = q.lower()
        matches = [c for c in concepts if any(ql in t.lower() for t in api._terms(c))]
    else:
        matches = concepts
    if not matches:
        st.info("Not found in the spine.")
        return
    matches = sorted(matches, key=lambda c: (-counts.get(c["id"], 0), c["name"]))
    default = st.query_params.get("concept", "cond:diabetes")
    idx = next((i for i, c in enumerate(matches) if c["id"] == default), 0)
    cid = mid.selectbox(
        f"Concept ({len(matches)} found)", [c["id"] for c in matches], index=idx,
        format_func=lambda i: f"{by_id[i]['name']}  ·  {by_id[i]['type']} · {counts.get(i, 0)} statements")
    if cid != default:
        st.query_params["concept"] = cid
    with st.container():
        c = by_id[cid]
        st.subheader(c["name"])
        st.caption(f"`{c['id']}` · {c['type']}"
                   + (f" · also: {', '.join(c['synonyms'])}" if c["synonyms"] else ""))
        if c["notes"]:
            st.caption(c["notes"])
        groups = [
            ("Statements about this concept", [a for a in assertions if a["subject_id"] == cid]),
            ("Where it appears in other statements", [a for a in assertions if a["object_id"] == cid]),
            ("Statements that apply to this population",
             [a for a in assertions if a["population_id"] == cid]),
        ]
        shown = False
        for title, rows in groups:
            if not rows:
                continue
            shown = True
            st.markdown(f"##### {title} ({len(rows)})")
            for a in sorted(rows, key=order_key):
                card(a, f"enc{title[:4]}")
        if not shown:
            st.info("No statements reference this concept yet.")


def _q(s: str) -> str:
    return '"' + s.replace('"', "'") + '"'


def graph(concepts, assertions):
    by_id = {c["id"]: c for c in concepts}
    opts = [k for k in BLOCKS if any(block_of(a) == k for a in assertions)]
    c1, c2 = st.columns([2, 1])
    picked = c1.multiselect("Blocks", opts, default=[("2", "a"), ("2", "b"), ("2", "c")],
                            format_func=lambda k: f"Section {k[0]} · {BLOCKS[k]}")
    hide_general = c2.toggle("Hide classification edges", value=False)
    rows = [a for a in assertions if block_of(a) in picked
            and not (hide_general and a["predicate"] in ("classified_as", "subtype_of"))]
    if not rows:
        st.info("Pick at least one block.")
        return
    nodes = {a["subject_id"] for a in rows} | {a["object_id"] for a in rows if a["object_id"]}
    lines = ['digraph spine {', 'rankdir=LR; bgcolor="transparent"; nodesep=0.25; ranksep=0.6;',
             'node [shape=box style="rounded,filled" fontname="Helvetica" fontsize=11 '
             'color="#888888" fontcolor="#111111"];',
             'edge [fontname="Helvetica" fontsize=9 color="#888888" fontcolor="#777777"];']
    for n in sorted(nodes):
        c = by_id[n]
        lines.append(f'{_q(n)} [label={_q(c["name"])} fillcolor="{TYPE_COLOUR.get(c["type"], "#eee")}" '
                     f'tooltip={_q(c["type"] + " · " + n)}];')
    for a in rows:
        label = a["predicate"].replace("_", " ")
        v = api.format_value(a)
        if v:
            label += f"\\n{v}"
        if a["population_id"] and a["population_id"] != "pop:general":
            label += f"\\n[{a['population_name']}]"
        style = "solid" if a["doctor_checked"] else "dashed"
        target = a["object_id"] or a["subject_id"]
        lines.append(f'{_q(a["subject_id"])} -> {_q(target)} [label={_q(label)} style={style} '
                     f'tooltip={_q(a["id"])}];')
    lines.append("}")
    st.graphviz_chart("\n".join(lines), use_container_width=True)
    st.caption(f"{len(rows)} statements, {len(nodes)} concepts · dashed = not yet checked against "
               "the 2026 text · colours: blue condition, green test, orange population, "
               "pink risk factor, purple intervention/drug")


def review(concepts, assertions):
    if not is_doctor():
        st.info("**How the spine is being verified.** Every row was entered from the ADA Standards of Care "
                "(Sections 2–3). The doctor is now checking each one against the 2026 text and adding its "
                "recommendation number and page. Until a row is checked, drafts and exported files say "
                "*not yet checked by the doctor*. Nothing here is filled in by AI.")
    total = len(assertions)
    checked = sum(a["doctor_checked"] for a in assertions)
    anchored = sum(bool(a["locator"]) for a in assertions)
    graded = sum(bool(a["grade"]) for a in assertions)
    c1, c2, c3 = st.columns(3)
    c1.metric("Checked against 2026 text", f"{checked} / {total}")
    c2.metric("Rec / Table entered", f"{anchored} / {total}")
    c3.metric("Grade entered", f"{graded} / {total}")
    st.progress(checked / total if total else 0.0)

    f1, f2 = st.columns([1, 1])
    sec = f1.selectbox("Section", ["All", "2", "3"])
    only_open = f2.toggle("Only rows not yet checked", value=True)
    rows = [a for a in assertions if (sec == "All" or a["section"] == sec)
            and (not only_open or not a["doctor_checked"])]
    rows.sort(key=lambda a: ("LOWER CONFIDENCE" not in (a["notes"] or ""), a["id"]))
    st.caption(f"{len(rows)} row(s) · lower-confidence rows first")
    PAGE = 15
    pages = max(1, -(-len(rows) // PAGE))
    pg = st.number_input("Page", 1, pages, 1, key="review_page") if pages > 1 else 1
    for a in rows[(pg - 1) * PAGE: pg * PAGE]:
        card(a, "rev")
    st.caption(f"Page {pg} of {pages}")

    st.divider()
    st.markdown("**Export** (the database is now the master copy; these files re-import cleanly)")
    files = export_files()
    d1, d2 = st.columns(2)
    d1.download_button("concepts.csv", files["concepts.csv"], "concepts.csv", "text/csv")
    d2.download_button("assertions.csv", files["assertions.csv"], "assertions.csv", "text/csv")


# ---------- review (doctor) ----------

STATUS_BADGE = {"pending": ":orange[● to review]", "approved": ":green[✔ approved]",
                "rejected": ":gray[✖ rejected]"}


def _who() -> str:
    return (st.session_state.get("reviewer") or "").strip()


def _act(fn, *args, ok_msg: str = "", **kw):
    try:
        out = fn(*args, **kw)
    except drafts.ReviewError as e:
        st.session_state.flash_error = str(e)
        return None
    except psycopg.Error as e:
        st.session_state.flash_error = f"Not saved: {getattr(e.diag, 'message_primary', None) or e}"
        return None
    if ok_msg:
        st.session_state.flash = ok_msg
    return out


def claim_card(c: dict, locked: bool, ctx: str):
    v = c["verdict"]
    key = f"{ctx}-{c['id']}"
    with st.container(border=True):
        top = STATUS_BADGE[c["status"]] + "  ·  " + (":green[Verifier ✔]" if v.get("ok") else ":red[Verifier ⚠]")
        if v.get("regenerated"):
            top += "  ·  rewritten once by the Verifier"
        if v.get("edited"):
            top += "  ·  edited by doctor"
        st.markdown(top)

        editing = (not locked and c["status"] != "rejected"
                   and st.session_state.get(f"edit-{key}", False))
        if not editing:
            st.markdown(("~~" + c["text"] + "~~") if c["status"] == "rejected" else c["text"])
        else:
            with st.form(f"editform-{key}", border=False):
                new_text = st.text_area("Claim", c["text"], key=f"txt-{key}", height=80,
                                        label_visibility="collapsed")
                save = st.form_submit_button("Save edit and re-verify", type="primary")
            if save and new_text.strip() != c["text"]:
                st.session_state[f"edit-{key}"] = False
                lock = model_lock()
                if not lock.acquire(timeout=20):
                    st.session_state.flash_error = ("The model is busy with another draft; "
                                                    "try saving again in a minute.")
                else:
                    try:
                        with st.spinner("Re-verifying…"):
                            _act(drafts.edit, conn(), model(), c["id"], _who(), new_text,
                                 ok_msg="Edit saved and re-verified")
                    finally:
                        lock.release()
                st.rerun()
        if c["original_text"] != c["text"]:
            st.caption(f"Original draft: {c['original_text']}")

        nums = v.get("number_checks", [])
        if nums:
            st.caption("Numbers: " + "  ".join(
                f"{'✔' if n['ok'] else '✖'} {n['value']} {n['unit']}".strip() for n in nums))
        for issue in v.get("issues", []):
            st.warning(issue)
        if v.get("support"):
            st.caption(f"Support judge: {v['support']['verdict'].replace('_', ' ')} — {v['support']['reason']}")
        st.caption("Grade (from spine): " + ", ".join(sorted(set(v.get("grades", [])))) + " · cites "
                   + ", ".join(f"`{x}`" for x in c["assertion_ids"]))

        if not locked:
            for n in nums:
                if not n["ok"] and n.get("assertion_id") and not n.get("range"):
                    if st.button(f"Propose {n['value']} {n['unit']} as a spine correction",
                                 key=f"prop-{key}-{n['value']}"):
                        new_id = _act(drafts.propose_correction, conn(), c["id"], _who(), n)
                        if new_id:
                            load.clear()
                            st.session_state.flash = (f"Proposed {new_id}. The spine is unchanged until you accept it "
                                                      "in the Encyclopedia.")
                        st.rerun()

        if st.toggle("Source", key=f"src-{key}"):
            for an in v.get("anchors", []):
                st.markdown(f"> {an['statement']}" + (f" — {an['condition_text']}" if an.get("condition_text") else ""))
                st.caption(f"📍 {an['anchor']} · `{an['assertion_id']}` · "
                           + ("✔ checked against 2026 text" if an["doctor_checked"] else "○ not yet checked by doctor"))

        if not locked:
            b1, b2, b3, b4, b5 = st.columns([1.25, 1.15, 1.05, 0.95, 1.15])
            b5.toggle("Edit", key=f"edit-{key}", disabled=c["status"] == "rejected")
            ticked = b1.checkbox("Numbers checked", c["numbers_checked"], key=f"num-{key}",
                                 disabled=not nums) if nums else False
            if b2.button("Approve", key=f"ok-{key}", disabled=c["status"] == "approved"):
                _act(drafts.decide, conn(), c["id"], _who(), "approved", numbers_checked=ticked)
                st.rerun()
            if b3.button("Reject", key=f"no-{key}", disabled=c["status"] == "rejected"):
                _act(drafts.decide, conn(), c["id"], _who(), "rejected")
                st.rerun()
            if b4.button("Undo", key=f"undo-{key}", disabled=c["status"] == "pending"):
                _act(drafts.decide, conn(), c["id"], _who(), "pending")
                st.rerun()


def review_draft(draft_id: str, ctx: str):
    d = drafts.load(conn(), draft_id)
    if d is None:
        st.info("Draft not found.")
        return
    locked = d["status"] == "signed_off"
    st.subheader(d["title"])
    st.caption(f"{d['artifact']} · {d['audience']} · request: “{d['request']}” · draft `{d['id']}`")
    state = drafts.signoff_state(d)
    flagged = sum(not c["verdict"].get("ok") and c["status"] != "rejected" for c in d["claims"])
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Approved", state["approved"])
    m2.metric("Rejected", state["rejected"])
    m3.metric("To review", state["pending"])
    m4.metric("Flagged (open)", flagged)
    if locked:
        st.success(f"Signed off by **{d['signed_by']}** on {d['signed_at'].astimezone():%d %b %Y %H:%M}. Locked.")
    elif not _who():
        st.info("Enter your name in the sidebar to review; every decision is logged with it.")
    if not locked:
        ready = [c for c in d["claims"] if c["status"] == "pending" and c["verdict"].get("ok")]
        if len(ready) > 1:
            with st.container(border=True):
                a1, a2 = st.columns([3, 1.3])
                sure = a1.checkbox(f"I have read the {len(ready)} Verifier-passed claims still to review "
                                   "and checked their numbers against the sources", key=f"allnum-{ctx}-{d['id']}")
                if a2.button(f"Approve all {len(ready)}", disabled=not sure, key=f"allok-{ctx}-{d['id']}"):
                    done = 0
                    for c in ready:
                        if _act(drafts.decide, conn(), c["id"], _who(), "approved", numbers_checked=True) is None:
                            break
                        done += 1
                    if done:
                        st.session_state.flash = f"Approved {done} claim(s)"
                    st.rerun()
                a1.caption("Or approve one by one below. Claims flagged by the Verifier must be fixed or rejected.")

    unused = d.get("unused_rows") or {}
    by_id = {a["id"]: a for a in load()[1]}
    for s in d["sections"]:
        st.markdown(f"#### {s['heading']}")
        for c in s["claims"]:
            claim_card(c, locked, ctx)
        left_out = [by_id[i] for i in unused.get(s["heading"], []) if i in by_id]
        if left_out and not locked:
            st.warning("**Planned for this section but not in any claim.** Check whether the slides need it "
                       "(edit a claim to add it, or ignore):\n\n"
                       + "\n".join(f"- {api.statement(a)} (`{a['id']}`)" for a in left_out))

    st.divider()
    st.markdown("### Sign-off")
    if state["unchecked_rows"]:
        rows_txt = ", ".join(f"`{x}`" for x in state["unchecked_rows"])
        if is_doctor():
            st.warning(f"{len(state['unchecked_rows'])} spine row(s) behind approved claims are not yet checked "
                       f"against your 2026 copy: {rows_txt}")
        else:
            st.info(f"{len(state['unchecked_rows'])} spine row(s) behind these claims are still pending the "
                    f"doctor's check against the ADA 2026 text ({rows_txt}). The exported file says so.")
    if locked:
        st.markdown("### Export")
        st.caption("The file contains exactly the approved claim text, numbered references to the "
                   "spine and its ADA anchors, and a note on how it was made. Every export is logged.")
        order = ["pptx", "docx"] if d["artifact"] == "slides" else ["docx", "pptx"]
        cols = st.columns(2)
        for col, fmt in zip(cols, order):
            try:
                out = export.render(conn(), d["id"], fmt)
            except export.ExportError as e:
                col.error(str(e))
                continue
            col.download_button(
                {"pptx": "⬇ Slides (PPTX)", "docx": "⬇ Handout (DOCX)"}[fmt], out["data"],
                file_name=out["filename"], mime=out["mime"], key=f"dl-{ctx}-{fmt}",
                type="primary" if fmt == order[0] else "secondary",
                on_click=export.log_export,
                args=(conn(), d["id"], fmt, _who() or d["signed_by"], out["sha256"], out["claims"]))
            col.caption(f"Fidelity check passed · SHA-256 `{out['sha256'][:12]}…`")
        return
    for r in state["reasons"]:
        st.caption(f"• {r}")
    if st.button("Sign off this draft", type="primary", disabled=not state["can_sign"], key=f"sign-{ctx}"):
        _act(drafts.sign_off, conn(), d["id"], _who(), ok_msg="Signed off. Export is unlocked.")
        st.rerun()


def drafts_page():
    rows = drafts.list_drafts(conn())
    if not rows:
        st.info("No drafts yet. Create one in the Create tab.")
        return
    labels = {r["id"]: f"{r['title']} · {r['created_at'].astimezone():%d %b %H:%M} · "
                       + ("signed off" if r["status"] == "signed_off" else f"{r['pending']}/{r['claims']} to review")
              for r in rows}
    ids = list(labels)
    default = st.session_state.get("open_draft")
    pick = st.selectbox("Draft", ids, index=ids.index(default) if default in ids else 0,
                        format_func=labels.get)
    review_draft(pick, "drafts")


# ---------- create (agents) ----------

EXAMPLES = [
    "Diagnostic criteria for prediabetes",
    "Diagnosing diabetes in Indian adults",
    "When is HbA1c unreliable for diagnosing diabetes?",
    "Preventing type 2 diabetes in people with prediabetes",
    "What metformin dose should I start for my 54-year-old patient with A1C 7.2?",
    "Insulin pump settings for type 1 diabetes",
]
STEP_NAMES = {"scope_guard": "Scope Guard", "spine_lookup": "Spine lookup", "planner": "Planner",
              "done": "Done"}


@st.cache_resource
def model():
    from evidex.llm import make_llm
    return make_llm()


def _step_name(step: str) -> str:
    if step.startswith("synthesizer:"):
        return f"Synthesizer · {step.split(':', 1)[1]}"
    return STEP_NAMES.get(step, step)


def _set_topic(t: str, reset: bool = False):
    st.session_state.topic = t
    if reset:
        st.session_state.last_run = None


def render_run(events: list[dict]):
    last = events[-1]
    d = last["data"]
    st.caption("Steps: " + " → ".join(f"{_step_name(e['step'])} ({e['ms'] / 1000:.0f}s)"
                                      for e in events if e["step"] != "done")
               + f" · total {sum(e['ms'] for e in events) / 1000:.0f}s")
    if last["status"] == "declined":
        st.error(f"**Declined by the Scope Guard** ({d['decided_by']}). {d['reason']}\n\n"
                 "Evidex reports what guidelines say in general; it does not advise on individual patients.")
        if d.get("general_query"):
            st.button(f"Ask the general question instead: “{d['general_query']}”",
                      on_click=_set_topic, args=(d["general_query"], True))
    elif last["status"] == "not_found":
        st.warning(f"**{d['message']}.** The spine (ADA 2026 Sections 2–3) does not cover this topic, "
                   "so Evidex will not write about it.")
        if d.get("not_covered"):
            st.caption("Not covered: " + "; ".join(d["not_covered"]))
    elif last["status"] == "error":
        st.error(f"{_step_name(last['step'])} failed: {d['error']}")
    elif last["step"] == "done":
        if d.get("draft_id"):
            st.success("Draft saved. Review each claim below, then sign off. "
                       "(It is also listed in the Drafts tab.)")
            review_draft(d["draft_id"], "create")
        else:
            st.warning("The run produced no claims to review.")

    with st.expander("What each agent saw and returned (audit log)"):
        for e in events:
            st.markdown(f"**{_step_name(e['step'])}** · {e['status']} · {e['ms'] / 1000:.1f}s")
        try:
            rows = conn().execute("select step, model, input, output, error, attempts, ms "
                                  "from agent_call where run_id = %s order by id",
                                  (last["run_id"],)).fetchall()
        except psycopg.Error as err:
            rows = []
            st.caption(f"Could not load the log: {err}")
        for step, mdl, inp, out, err, attempts, ms in rows:
            st.markdown(f"---\n**{_step_name(step)}** · `{mdl}` · {attempts} attempt(s) · {ms / 1000:.1f}s")
            st.text_area("System prompt", inp["system"], height=120, disabled=True,
                         key=f"sys-{last['run_id']}-{step}")
            st.text_area("What it was given", inp["user"], height=160, disabled=True,
                         key=f"usr-{last['run_id']}-{step}")
            st.json(out if out is not None else {"error": err}, expanded=False)
        st.caption(f"run `{last['run_id']}`")


def _current_job():
    """The job this visitor started; kept in the URL too, so a page refresh finds it again."""
    job_id = st.session_state.get("job_id") or st.query_params.get("job")
    job = jobs.get(job_id)
    if job is not None:
        st.session_state.job_id = job.id
    elif job_id and not st.session_state.get("last_run"):
        st.info("That draft run is no longer tracked here (the app was restarted). Drafts that finished "
                "are saved: open the **Drafts** tab.")
    return job


@st.fragment(run_every=3)
def job_progress(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        return
    if job.done:
        st.rerun()  # whole page: show the result
    with st.status(f"Working… {job.elapsed:.0f}s so far ({MODEL_SHORT})",
                   expanded=True, state="running"):
        st.caption("You can switch tabs, refresh or come back later: the draft keeps running and is saved "
                   "to the Drafts tab when it is ready.")
        for e in job.events:
            if e["step"] != "done":
                st.write(f"{_step_name(e['step'])}: **{e['status']}** · {e['ms'] / 1000:.1f}s")


def create(concepts, assertions):
    st.session_state.spine_rows = assertions
    job = _current_job()
    if job is not None and job.done and st.session_state.get("last_run_job") != job.id:
        st.session_state.last_run = job.events
        st.session_state.last_run_job = job.id
    working = job is not None and not job.done
    has_run = bool(st.session_state.get("last_run")) or working
    with st.expander("✍️ Start another draft" if has_run else "✍️ What are you creating?", expanded=not has_run):
        st.selectbox("Pick a demo example (optional)", [""] + EXAMPLES, key="example",
                     on_change=lambda: _set_topic(st.session_state.example),
                     format_func=lambda x: x or "—")
        with st.form("create_form", border=False):
            c1, c2 = st.columns(2)
            artifact = c1.radio("Output", list(steps.ARTIFACTS), horizontal=True,
                                format_func=lambda k: {"slides": "Teaching slides", "handout": "Teaching handout"}[k])
            audience = c2.selectbox("Audience", steps.AUDIENCES)
            st.text_area("Topic", key="topic", height=70,
                         placeholder="e.g. Diagnosing diabetes in Indian adults")
            st.caption("Covers ADA 2026 Sections 2–3: diagnosis, classification, screening and prevention. "
                       "General teaching topics only: no individual patients, doses or treatment plans.")
            submitted = st.form_submit_button("Create draft", type="primary")
    topic = (st.session_state.get("topic") or "").strip()
    if submitted and not topic:
        st.warning("Type a topic or pick a demo example first.")
    if submitted and topic:
        import time as _t
        limit = run_allowed()
        lock = model_lock()
        if working:
            st.warning("Your previous draft is still being created; it is shown below.")
        elif limit:
            st.warning(limit)
        elif not lock.acquire(blocking=False):
            st.warning("Another draft is being created right now (the model runs one job at a time). "
                       "Please try again in a minute or two.")
        else:
            st.session_state.run_times.append(_t.time())
            llm = model()

            def run():
                c = fresh_connection()
                try:
                    yield from pipeline.run(c, llm, topic, artifact, audience)
                finally:
                    c.close()

            new = jobs.start(run, label=topic, on_finish=lock.release)
            st.session_state.job_id = new.id
            st.session_state.last_run = None
            st.query_params["job"] = new.id
            st.rerun()
    if working:
        job_progress(job.id)
    elif st.session_state.get("last_run"):
        render_run(st.session_state.last_run)


# ---------- about / how to test ----------

def about_page():
    st.markdown("""
### Evidex: verified teaching material from a doctor-curated diabetes knowledge base

Doctors spend their time **assembling and checking** evidence, not typing. Evidex drafts teaching
slides or handouts from a **knowledge spine** anchored to the *ADA Standards of Care in Diabetes 2026*
(Sections 2–3: diagnosis, classification, screening, prevention). Every claim cites spine rows,
numbers are checked by code, and **nothing is exported until a doctor approves every claim and signs off**.

**Context → Evidence → Artifact → Verify → Approve**

#### Try it (about 10 minutes)
1. **Enter your name** in the sidebar (on a phone, tap **›** at the top left to open it). Every review
   action is logged with it.
2. **A patient-specific question is declined.** In *Create*, pick the demo example
   *"What metformin dose should I start for my 54-year-old patient…"* and click **Create draft**
   (~5 s). Evidex declines and offers a general question instead.
3. **"Not found in sources".** Click the suggested general question, or pick *"Insulin pump settings…"*.
   The spine has no dosing, treatment or device statements, so Evidex says so instead of guessing.
4. **Create a draft.** Pick *"Diagnostic criteria for prediabetes"* (~1 min) or
   *"Diagnosing diabetes in Indian adults"* (~3 min). Watch each step: Scope Guard, spine lookup,
   Planner, Synthesizer, Verifier.
5. **Review.** For each claim, open **Source** to see the spine row and its ADA anchor.
   Switch on **Edit** for a claim, **change a number** (e.g. 5.7 to 6.0) and click *Save edit and re-verify*: the Verifier flags it
   ("spine says 5.7–6.4 %") and approval is blocked. Reject it or change it back.
6. **Sign off and export.** Approve the remaining claims (one by one with *Numbers checked*, or with
   **Approve all** after confirming you checked them), click **Sign off**,
   then download the **PPTX** or **DOCX**. The file contains exactly the approved text, numbered
   references and an AI-use note.
7. Optional: browse the spine in **Encyclopedia** and **Graph**; open *"What each agent saw and returned"*
   under a run to see every prompt and output.

#### Good to know
- @@MODEL@@ It handles **one draft at a time**; if someone else is running one, you will be asked to
  wait a minute.
- Evidex reports what the guideline says; it **does not** give advice about individual patients.
- Spine values were entered from long-standing ADA criteria; rows marked *"not yet checked"* are still
  being verified by the doctor against the 2026 text, and missing recommendation numbers or pages show as
  *TODO(doctor)*. Nothing is filled in by AI.
- Synthetic content only. Drafts you create are visible in the *Drafts* tab to other evaluators.
""".replace("@@MODEL@@", MODEL_ABOUT))
    st.caption("Team Synexis · AZYZ69 · source code: github.com/vigneshm2311-cyber/evidex")


# ---------- main ----------

gate()
concepts, assertions = load()

with st.sidebar:
    st.markdown("### Evidex")
    st.caption("Diabetes knowledge spine · ADA Standards of Care in Diabetes 2026, Sections 2–3")
    st.text_input("Your name (for the change log)", key="reviewer", placeholder="Dr …")
    st.caption("Signed in as **doctor** (can edit the spine)" if is_doctor()
               else "Signed in as **evaluator** (spine is read-only)")
    st.divider()
    st.caption(f"{len(concepts)} concepts · {len(assertions)} statements")
    st.caption("The spine reports what the guideline says and where. It does not give advice "
               "about any individual patient.")
    if is_doctor() and st.button("Reload from database"):
        load.clear()
        export_files.clear()
        st.rerun()

if msg := st.session_state.pop("flash", None):
    st.toast(msg, icon="✅")
if err := st.session_state.pop("flash_error", None):
    st.toast(err, icon="⛔")
    st.error(err)

tabi, tab0, tabd, tab1, tab2, tab3 = st.tabs(["ℹ️ How to test", "✍️ Create", "📝 Drafts", "📚 Encyclopedia",
                                              "🕸️ Graph", "✅ Spine review" if is_doctor() else "✅ Verification"])
with tabi:
    about_page()
with tab0:
    create(concepts, assertions)
with tabd:
    drafts_page()
with tab1:
    encyclopedia(concepts, assertions)
with tab2:
    graph(concepts, assertions)
with tab3:
    review(concepts, assertions)
