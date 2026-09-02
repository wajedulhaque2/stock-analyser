from pathlib import Path


APP = Path(__file__).resolve().parents[1] / "app.py"


def test_sec_request_uses_form_submission():
    source = APP.read_text(encoding="utf-8")
    assert 'with st.form(key=f"sec_request_form::{ticker}"' in source
    assert 'st.form_submit_button("Load filing"' in source
    assert 'sec_contact_input::{ticker}' in source


def test_sec_request_exposes_connection_status():
    source = APP.read_text(encoding="utf-8")
    assert "SEC connection status:" in source
    assert 'load_official_packet.clear()' in source
