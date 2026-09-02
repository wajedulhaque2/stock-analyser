from __future__ import annotations

import ast
from pathlib import Path
import socket

import pytest
from streamlit.testing.v1 import AppTest

from stock_analyser.application import (
    ProviderAvailabilityStatus,
    V1ResearchCoordinator,
)
from stock_analyser.live_identity import IdentityHttpStatusCategory
from stock_analyser.ui.feature_flags import (
    ENABLE_V1_REPORT_UI_ENV,
    USE_LEGACY_UI_ENV,
    is_legacy_ui_enabled,
    is_v1_report_ui_enabled,
)
from stock_analyser.ui.v1_route import render_v1_report_route
from test_v1_provider_availability_cutover import diagnostic, unavailable_outcome
from test_v1_research_integration import RouteUI, UIService, build_result
from test_v1_research_report import NOW, partial_report


ROOT = Path(__file__).resolve().parents[1]
APP_SOURCE = ROOT / "app.py"
FLAG_SOURCE = ROOT / "src" / "stock_analyser" / "ui" / "feature_flags.py"
ROUTE_SOURCE = ROOT / "src" / "stock_analyser" / "ui" / "v1_route.py"
REPORT_SOURCE = ROOT / "src" / "stock_analyser" / "ui" / "v1_report.py"


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "TrUe", "yes", "YES", "on", "ON"])
def test_explicit_legacy_true_values_select_only_rollback(value):
    environment = {USE_LEGACY_UI_ENV: value}
    assert is_legacy_ui_enabled(environment) is True
    assert is_v1_report_ui_enabled(environment) is False


@pytest.mark.parametrize("value", [None, "", " ", "0", "false", "off", "no", "invalid"])
def test_absent_false_blank_or_invalid_rollback_defaults_to_v1(value):
    environment = {} if value is None else {USE_LEGACY_UI_ENV: value}
    assert is_legacy_ui_enabled(environment) is False
    assert is_v1_report_ui_enabled(environment) is True


@pytest.mark.parametrize("old_value", ["", "0", "false", "1", "true", "invalid"])
def test_deprecated_positive_flag_has_no_routing_authority(old_value):
    assert is_v1_report_ui_enabled({ENABLE_V1_REPORT_UI_ENV: old_value}) is True
    assert is_v1_report_ui_enabled({
        ENABLE_V1_REPORT_UI_ENV: old_value,
        USE_LEGACY_UI_ENV: "true",
    }) is False


def _deny_network(*args, **kwargs):
    pytest.fail("provider/network call during startup")


def _rendered_text(app_test: AppTest) -> str:
    values: list[str] = []
    for collection in (
        app_test.markdown, app_test.caption, app_test.info, app_test.warning,
        app_test.error, app_test.title, app_test.subheader,
    ):
        values.extend(str(item.value) for item in collection)
    return "\n".join(values)


def test_default_streamlit_startup_is_empty_v1_and_provider_free(monkeypatch):
    monkeypatch.delenv(USE_LEGACY_UI_ENV, raising=False)
    monkeypatch.delenv(ENABLE_V1_REPORT_UI_ENV, raising=False)
    monkeypatch.setattr(socket, "create_connection", _deny_network)

    app = AppTest.from_file(str(APP_SOURCE), default_timeout=20).run()
    text = _rendered_text(app)

    assert len(app.exception) == 0
    assert [(item.label, item.value) for item in app.text_input] == [("Ticker", "")]
    assert [item.label for item in app.button] == ["Analyse"]
    assert "Stock research report" in text
    assert "Enter a ticker to begin" in text
    assert "Legacy interface" not in text
    assert all(token not in text.lower() for token in ("traceback", "api key", "companykey"))


def test_explicit_rollback_startup_is_idle_legacy_only(monkeypatch):
    monkeypatch.setenv(USE_LEGACY_UI_ENV, "TrUe")
    monkeypatch.setenv(ENABLE_V1_REPORT_UI_ENV, "true")
    monkeypatch.setattr(socket, "create_connection", _deny_network)

    app = AppTest.from_file(str(APP_SOURCE), default_timeout=20).run()
    text = _rendered_text(app)

    assert len(app.exception) == 0
    assert [item.value for item in app.title] == ["STOCK ANALYSER"]
    assert [(item.label, item.value) for item in app.text_input] == [("Ticker", "")]
    assert [item.label for item in app.button] == ["Analyse"]
    assert "Legacy interface" in text
    assert "No provider request runs at startup" in text
    assert "Stock research report" not in text


def test_default_branch_stops_before_legacy_and_never_silently_falls_back():
    source = APP_SOURCE.read_text(encoding="utf-8")
    route_start = source.index("if is_v1_report_ui_enabled():")
    route_end = source.index("    st.stop()", route_start) + len("    st.stop()")
    route = source[route_start:route_end]
    assert "render_v1_report_route()" in route
    assert "st.stop()" in route
    assert "except" not in route and "fallback" not in route.lower()


def test_access_denied_remains_controlled_without_legacy_or_alternate_security():
    item = diagnostic(IdentityHttpStatusCategory.ACCESS_DENIED)
    result = V1ResearchCoordinator(
        runner=lambda *args, **kwargs: unavailable_outcome(item)
    ).build("RR.L", environment={}, analysis_as_of=NOW)
    ui = RouteUI(symbol="RR.L", buttons={"Analyse": [True]})
    rendered = render_v1_report_route(
        coordinator=UIService(result), environment={}, streamlit_module=ui, now=lambda: NOW,
    )
    text = "\n".join(ui.markdowns)
    assert rendered is result
    assert result.provider_availability is ProviderAvailabilityStatus.ACCESS_DENIED
    assert "Legacy interface" not in text
    assert "SHEL.L" not in text and "NASDAQ" not in text


def test_partial_report_remains_valid_on_the_default_route(monkeypatch):
    service = UIService(build_result(partial_report()))
    ui = RouteUI(symbol="SYN", buttons={"Analyse": [True]})
    monkeypatch.setattr(
        "stock_analyser.ui.v1_route.render_stock_research_report",
        lambda *args, **kwargs: None,
    )
    result = render_v1_report_route(
        coordinator=service, environment={}, streamlit_module=ui, now=lambda: NOW,
    )
    assert is_v1_report_ui_enabled({}) is True
    assert result is not None and result.report is not None
    assert len(service.build_calls) == 1


def test_release_dependencies_and_canonical_command_are_declared():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    for dependency in ("streamlit", "altair", "requests", "pandas", "numpy", "scipy"):
        assert any(line.startswith(dependency) for line in requirements.splitlines())
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "streamlit run app.py" in readme
    assert "USE_LEGACY_UI=true" in readme
    assert "ENABLE_V1_REPORT_UI" in readme and "deprecated" in readme


def test_secret_files_and_release_artifacts_are_ignored_safely():
    ignored = set((ROOT / ".gitignore").read_text(encoding="utf-8").splitlines())
    for pattern in (
        ".env", ".env.*", "!.env.example", ".streamlit/secrets.toml",
        ".test_deps/", "data_cache/", "raw_provider_payloads/",
        "browser_artifacts/", "playwright-report/", "test-results/", "*.log", "*.har",
    ):
        assert pattern in ignored


def test_env_example_contains_names_and_safe_placeholders_only():
    lines = [
        line.strip() for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    values = dict(line.split("=", 1) for line in lines)
    assert set(values) == {
        "FISCAL_API_KEY", "FMP_API_KEY", "FINNHUB_API_KEY", "FRED_API_KEY",
        "ALPHAVANTAGE_API_KEY", "USE_LEGACY_UI",
    }
    assert all(value in {"", "false"} for value in values.values())


def test_release_version_has_one_python_source_of_truth():
    assignments: list[tuple[Path, str]] = []
    for path in (ROOT / "src").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "__version__"
                for target in node.targets
            ):
                assignments.append((path, node.value.value))
    assert assignments == [(ROOT / "src" / "stock_analyser" / "__init__.py", "1.0.0")]
    assert "Current build: v1.0.0" in (ROOT / "README.md").read_text(encoding="utf-8")


def test_release_ui_hides_preview_naming_stance_and_generated_narrative():
    production = "\n".join(path.read_text(encoding="utf-8") for path in (ROUTE_SOURCE, REPORT_SOURCE))
    lowered = production.lower()
    for forbidden in (
        "v1 research report", "v1 equity research snapshot", '"buy"', '"hold"',
        '"sell"', "investment thesis", "executive summary", "generated narrative",
    ):
        assert forbidden not in lowered
    assert 'showErrorDetails = "none"' in (ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")


def test_cutover_adds_no_target_branch_fx_or_provider_change():
    production = (FLAG_SOURCE.read_text(encoding="utf-8") + APP_SOURCE.read_text(encoding="utf-8"))
    string_literals = {
        node.value.lower()
        for node in ast.walk(ast.parse(production))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert string_literals.isdisjoint({"meta", "msft", "nvda", "shel.l", "rr.l", "goog", "googl"})
    flag_source = FLAG_SOURCE.read_text(encoding="utf-8").lower()
    assert all(token not in flag_source for token in ("requests", "provider", "currency", "fx", "valuation"))
