from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app.py"


def test_fiscal_loader_uses_resolved_company_name():
    source = APP.read_text(encoding="utf-8")
    assert "load_fiscal_packet(ticker, company, api_key)" in source
    assert "load_fiscal_packet(ticker, data.name, api_key)" not in source
