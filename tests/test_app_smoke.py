"""Render every tab of the Streamlit app headless (reads only; needs DATABASE_URL)."""
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def app():
    pytest.importorskip("streamlit")
    from dotenv import load_dotenv
    from streamlit.testing.v1 import AppTest

    load_dotenv(ROOT / ".env")
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("no DATABASE_URL")
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.session_state["authorised"] = True
    return at


def test_all_tabs_render_without_exceptions(app):
    app.run()
    assert not app.exception, app.exception
    assert len(app.tabs) == 6
    text = " ".join(m.value for m in app.markdown)
    assert "Diabetes mellitus is diagnosed by" in text


def test_gate_blocks_without_code(app):
    if not os.environ.get("ACCESS_CODE"):
        pytest.skip("no ACCESS_CODE set")
    app.session_state["authorised"] = False
    app.run()
    assert not app.tabs
    assert app.text_input[0].label == "Access code"


def test_evaluator_cannot_see_spine_edit_forms(app):
    app.session_state["role"] = "evaluator"
    app.run()
    assert not app.exception
    assert not any(e.label == "Check / edit" for e in app.expander)


def test_doctor_sees_spine_edit_forms(app):
    app.session_state["role"] = "doctor"
    app.run()
    assert any(e.label == "Check / edit" for e in app.expander)
