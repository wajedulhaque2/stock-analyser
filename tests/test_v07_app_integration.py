from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app.py"
ROOT = Path(__file__).resolve().parents[1]


def test_app_uses_fiscal_with_bounded_optional_ollama_workflow():
    source = APP.read_text(encoding="utf-8").lower()
    assert "load fiscal.ai earnings" in source
    assert "fiscal.ai api key" in source
    assert "optional local analyst interpretation" in source
    assert "check ollama" in source
    assert "embeddinggemma" not in source
    assert "/api/embed" not in source


def test_local_secret_files_are_gitignored():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in ignore
    assert (ROOT / ".env.example").exists()
