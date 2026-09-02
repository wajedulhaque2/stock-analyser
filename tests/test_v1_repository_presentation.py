from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
DOCS = ROOT / "docs"
ASSETS = DOCS / "assets"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_readme_has_required_production_structure_in_order():
    content = _text(README)
    headings = (
        "## Overview",
        "## Key capabilities",
        "## Research workflow",
        "## Valuation framework",
        "## Market expectations / reverse DCF",
        "## Data sources",
        "## Architecture",
        "## Screenshots",
        "## Running locally",
        "## Configuration",
        "## Testing",
        "## Security and data handling",
        "## Known limitations",
        "## Project structure",
        "## Release / rollback",
        "## Disclaimer",
    )
    positions = [content.index(heading) for heading in headings]
    assert positions == sorted(positions)


def test_readme_documents_canonical_startup_and_idle_boundary():
    content = _text(README)
    assert "streamlit run app.py" in content
    assert "canonical release command" in content
    assert "makes no provider request until Analyse is selected" in content


def test_release_version_and_explicit_legacy_rollback_are_unchanged():
    readme = _text(README)
    package = _text(ROOT / "src" / "stock_analyser" / "__init__.py")
    assert '__version__ = "1.0.0"' in package
    assert "v1.0.0" in readme
    assert "USE_LEGACY_UI=true" in readme
    assert "ENABLE_V1_REPORT_UI" in readme
    assert "is ignored" in readme


def test_env_example_remains_allowlisted_and_secret_free():
    lines = _text(ROOT / ".env.example").splitlines()
    assert lines == [
        "FISCAL_API_KEY=",
        "FMP_API_KEY=",
        "FINNHUB_API_KEY=",
        "FRED_API_KEY=",
        "ALPHAVANTAGE_API_KEY=",
        "USE_LEGACY_UI=false",
    ]


def test_public_documentation_contains_no_credential_assignment_or_secret_pattern():
    public_paths = (
        README,
        DOCS / "ARCHITECTURE.md",
        DOCS / "METHODOLOGY.md",
        DOCS / "DATA_SOURCES.md",
        DOCS / "PORTFOLIO_COPY.md",
        DOCS / "RELEASE_NOTES_v1.0.0.md",
    )
    content = "\n".join(_text(path) for path in public_paths)
    assert not re.search(r"(?i)\bsk-[a-z0-9_-]{16,}\b", content)
    assert "api_key=" not in content.lower()
    assert "companykey=" not in content.lower()
    assert not re.search(r"(?i)(authorization|x-api-key)\s*[:=]\s*\S+", content)


def test_provider_roles_are_consistent_across_public_documents():
    content = "\n".join(
        _text(path)
        for path in (README, DOCS / "DATA_SOURCES.md", DOCS / "METHODOLOGY.md")
    )
    required = (
        "FMP is the canonical provider",
        "Finnhub and Alpha Vantage are independent validators",
        "reference-only",
        "FRED DGS10",
        "SEC supplies independent",
        "Reverse DCF is an expectations lens, not a third valuation family",
        "No implicit FX conversion",
    )
    for statement in required:
        assert statement.lower() in content.lower()


def test_architecture_and_methodology_keep_ui_provider_free_and_fail_closed():
    architecture = _text(DOCS / "ARCHITECTURE.md")
    methodology = _text(DOCS / "METHODOLOGY.md")
    assert architecture.count("```mermaid") >= 2
    assert "Provider-free Streamlit renderer" in architecture
    assert "performs no valuation, price-gap, publication, or reverse-DCF calculation" in architecture
    assert "## Fail-closed policy" in methodology
    assert "no aggregate data-quality score" in methodology


def test_five_safe_release_screenshots_exist_at_consistent_dimensions():
    expected = {
        "v1-overview.png",
        "v1-consensus-expectations.png",
        "v1-evidence-readiness.png",
        "v1-scenario-analysis.png",
        "v1-provider-unavailable.png",
    }
    assert {path.name for path in ASSETS.glob("*.png")} == expected
    for name in expected:
        data = (ASSETS / name).read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        assert int.from_bytes(data[16:20], "big") == 1440
        assert int.from_bytes(data[20:24], "big") == 900
        assert len(data) > 20_000


def test_portfolio_and_release_documents_cover_requested_handoff_material():
    portfolio = _text(DOCS / "PORTFOLIO_COPY.md")
    release = _text(DOCS / "RELEASE_NOTES_v1.0.0.md")
    for heading in (
        "## Short description",
        "## Medium description",
        "## Tech stack",
        "## Key highlights",
        "## Repository CTA",
        "## CV / LinkedIn bullets",
    ):
        assert heading in portfolio
    assert "## Known limitations" in release
    assert "1,788 provider-free tests passed" in release
    assert "USE_LEGACY_UI=true" in release
