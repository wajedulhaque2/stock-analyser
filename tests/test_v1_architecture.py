from __future__ import annotations

import ast
from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOT = PROJECT_ROOT / "src" / "stock_analyser"
TICKER_VARIABLES = {"ticker", "symbol", "canonical_symbol"}
TICKER_LITERAL = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")


def _target_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id.lower()
    if isinstance(node, ast.Attribute):
        return node.attr.lower()
    return None


def _ticker_literals(node: ast.AST) -> frozenset[str]:
    values = []
    candidates = node.elts if isinstance(node, (ast.Set, ast.Tuple, ast.List)) else (node,)
    for candidate in candidates:
        if isinstance(candidate, ast.Constant) and isinstance(candidate.value, str) and TICKER_LITERAL.fullmatch(candidate.value):
            values.append(candidate.value)
    return frozenset(values)


class ConditionalTickerVisitor(ast.NodeVisitor):
    def __init__(self, relative_path: str):
        self.relative_path = relative_path
        self.function = "<module>"
        self.findings: list[tuple[str, str, int, frozenset[str]]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        previous = self.function
        self.function = node.name
        self.generic_visit(node)
        self.function = previous

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_If(self, node: ast.If) -> None:
        self._inspect_condition(node.test)
        self.generic_visit(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self._inspect_condition(node.test)
        self.generic_visit(node)

    def _inspect_condition(self, condition: ast.AST) -> None:
        for comparison in (n for n in ast.walk(condition) if isinstance(n, ast.Compare)):
            chain = (comparison.left, *comparison.comparators)
            for left, right in zip(chain, chain[1:]):
                left_name, right_name = _target_name(left), _target_name(right)
                literals = _ticker_literals(right) if left_name in TICKER_VARIABLES else frozenset()
                if right_name in TICKER_VARIABLES:
                    literals |= _ticker_literals(left)
                if literals:
                    self.findings.append((self.relative_path, self.function, comparison.lineno, literals))


def test_production_has_no_new_literal_ticker_conditionals():
    findings = []
    for path in PRODUCTION_ROOT.rglob("*.py"):
        relative = path.relative_to(PROJECT_ROOT).as_posix()
        visitor = ConditionalTickerVisitor(relative)
        visitor.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        findings.extend(visitor.findings)

    # V0.9.4 already special-cases Berkshire's dotted Yahoo symbols. Milestone 1 is
    # forbidden from changing legacy behavior, so this exact branch is characterized
    # but no additional literal-ticker behavior is permitted.
    grandfathered = {
        ("src/stock_analyser/fiscal_ai.py", "normalize_ticker", frozenset({"BRK.A", "BRK.B"}))
    }
    unexpected = [
        finding for finding in findings
        if (finding[0], finding[1], finding[3]) not in grandfathered
    ]
    assert not unexpected, f"ticker-specific production conditionals found: {unexpected}"
    assert any((p, fn, literals) in grandfathered for p, fn, _, literals in findings)


def test_v1_domain_and_numeric_valuation_do_not_import_ollama_interpretation():
    guarded = [
        *sorted((PRODUCTION_ROOT / "domain").rglob("*.py")),
        PRODUCTION_ROOT / "valuation.py",
        PRODUCTION_ROOT / "adaptive_valuation.py",
    ]
    forbidden = []
    for path in guarded:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any("local_interpretation" in name or "ollama" in name for name in names):
                forbidden.append((path.relative_to(PROJECT_ROOT).as_posix(), node.lineno, names))
    assert not forbidden, f"numeric domain/valuation depends on Ollama interpretation: {forbidden}"


def test_provider_dependency_direction_and_legacy_isolation():
    domain_imports = []
    for path in (PRODUCTION_ROOT / "domain").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            module = node.module if isinstance(node, ast.ImportFrom) else ""
            names = [item.name for item in node.names] if isinstance(node, ast.Import) else []
            if "providers" in (module or "") or any("providers" in name for name in names):
                domain_imports.append((path.name, node.lineno))
    assert not domain_imports, f"domain imports provider infrastructure: {domain_imports}"

    legacy_paths = [
        PROJECT_ROOT / "app.py", PRODUCTION_ROOT / "analysis.py", PRODUCTION_ROOT / "scoring.py",
        PRODUCTION_ROOT / "valuation.py", PRODUCTION_ROOT / "adaptive_valuation.py",
    ]
    provider_imports = []
    for path in legacy_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            module = node.module if isinstance(node, ast.ImportFrom) else ""
            names = [item.name for item in node.names] if isinstance(node, ast.Import) else []
            if "stock_analyser.providers" in (module or "") or any("stock_analyser.providers" in name for name in names):
                provider_imports.append((path.name, node.lineno))
    assert not provider_imports, f"legacy UI/valuation imports V1 provider DTOs: {provider_imports}"


def test_v1_provider_infrastructure_has_no_live_network_client():
    forbidden = []
    for path in (PRODUCTION_ROOT / "providers").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(name.split(".")[0] in {"requests", "httpx", "urllib3", "yfinance"} for name in names):
                forbidden.append((path.name, node.lineno, names))
    assert not forbidden, f"V1 providers added a live network client: {forbidden}"


def test_provider_specific_fields_stop_at_adapter_modules():
    provider_fields = {
        "regularMarketPrice", "financialCurrency", "totalRevenue",
        "RevenueFromContractWithCustomerExcludingAssessedTax", "OperatingIncomeLoss",
        "revenueLow", "revenueAvg", "ebitdaHigh", "netIncomeAvg", "numAnalystsRevenue",
        "targetConsensus", "capexSignConvention", "annualEstimates", "quarterlyEstimates",
        "revenueAverage", "epsUpRevisions30Days", "companyKey", "totalSharesOutstanding",
        "shareClasses",
    }
    escaped = []
    guarded = [*sorted((PRODUCTION_ROOT / "domain").rglob("*.py")), *sorted((PRODUCTION_ROOT / "services").rglob("*.py"))]
    for path in guarded:
        text = path.read_text(encoding="utf-8")
        for field in provider_fields:
            if field in text:
                escaped.append((path.relative_to(PROJECT_ROOT).as_posix(), field))
    assert not escaped, f"provider-specific fields escaped adapter modules: {escaped}"


def test_v1_adapters_do_not_cache_raw_or_normalized_payloads_yet():
    # Adapter cache integration remains deferred. This prevents a
    # future accidental raw-response CacheEntry while these adapters are isolated.
    adapter_paths = [
        PRODUCTION_ROOT / "providers" / name
        for name in (
            "yahoo.py", "fiscal.py", "sec.py", "fmp.py", "finnhub.py",
            "alpha_vantage.py", "fred.py",
        )
    ]
    findings = []
    for path in adapter_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("cache"):
                findings.append((path.name, node.lineno, "cache import"))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "set":
                findings.append((path.name, node.lineno, "set call"))
    assert not findings, f"V1 adapters unexpectedly write cache values: {findings}"


def test_numeric_adapter_normalization_does_not_import_ollama():
    forbidden = []
    for path in (PRODUCTION_ROOT / "providers").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            module = node.module if isinstance(node, ast.ImportFrom) else ""
            names = [item.name for item in node.names] if isinstance(node, ast.Import) else []
            if "ollama" in (module or "") or "local_interpretation" in (module or "") or any(
                "ollama" in name or "local_interpretation" in name for name in names
            ):
                forbidden.append((path.name, node.lineno))
    assert not forbidden


def test_reconciliation_service_has_no_provider_dto_valuation_coverage_or_ui_dependencies():
    path = PRODUCTION_ROOT / "services" / "reconciliation.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    forbidden_imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [item.name for item in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        if any(
            name.startswith("stock_analyser.providers")
            or "valuation" in name
            or "streamlit" in name
            or "coverage" in name
            for name in names
        ):
            forbidden_imports.append((node.lineno, names))
    assert not forbidden_imports

    text = path.read_text(encoding="utf-8")
    forbidden_contracts = {"ValuationResult", "CoverageLevel", "WACC", "ERP", "streamlit"}
    assert not {name for name in forbidden_contracts if name in text}


def test_reconciliation_is_not_wired_into_legacy_application_paths():
    legacy_paths = [
        PROJECT_ROOT / "app.py", PRODUCTION_ROOT / "analysis.py", PRODUCTION_ROOT / "scoring.py",
        PRODUCTION_ROOT / "valuation.py", PRODUCTION_ROOT / "adaptive_valuation.py",
    ]
    escaped = []
    for path in legacy_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                names = [item.name for item in node.names]
                if "services.reconciliation" in module or any(
                    name in {"reconcile_sources", "reconcile_many", "ObservationSource"} for name in names
                ):
                    escaped.append((path.relative_to(PROJECT_ROOT).as_posix(), node.lineno))
            elif isinstance(node, ast.Import) and any(
                "services.reconciliation" in item.name for item in node.names
            ):
                escaped.append((path.relative_to(PROJECT_ROOT).as_posix(), node.lineno))
    assert not escaped


def test_historical_valuation_input_layer_has_no_distribution_or_forward_valuation_dependencies():
    paths = [
        PRODUCTION_ROOT / "domain" / "valuation_history.py",
        PRODUCTION_ROOT / "providers" / "fiscal.py",
    ]
    forbidden_imports = []
    forbidden_calls = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                names = []
            if any(
                "services.consensus" in name
                or name.endswith(".fmp")
                or "adaptive_valuation" in name
                or "streamlit" in name
                for name in names
            ):
                forbidden_imports.append((path.name, node.lineno, names))
            if isinstance(node, ast.Call):
                name = _target_name(node.func)
                if name in {"median", "percentile", "quantile", "winsorize"}:
                    forbidden_calls.append((path.name, node.lineno, name))
    assert not forbidden_imports
    assert not forbidden_calls


def test_historical_valuation_is_not_wired_into_coverage_or_legacy_ui():
    guarded = [
        PRODUCTION_ROOT / "domain" / "coverage.py",
        PRODUCTION_ROOT / "services" / "coverage.py",
        PROJECT_ROOT / "app.py",
        PRODUCTION_ROOT / "analysis.py",
        PRODUCTION_ROOT / "valuation.py",
        PRODUCTION_ROOT / "adaptive_valuation.py",
    ]
    escaped = []
    for path in guarded:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                names = [item.name for item in node.names]
            elif isinstance(node, ast.Import):
                module = ""
                names = [item.name for item in node.names]
            else:
                continue
            if "valuation_history" in module or any(
                "valuation_history" in name or name.endswith("HistoricalValuationObservation")
                for name in names
            ):
                escaped.append((path.relative_to(PROJECT_ROOT).as_posix(), node.lineno))
    assert not escaped


def test_historical_distribution_service_is_canonical_only_and_non_valuing():
    path = PRODUCTION_ROOT / "services" / "valuation_history.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    forbidden_imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [item.name for item in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        if any(
            name.startswith("stock_analyser.providers")
            or name.startswith("stock_analyser.services.consensus")
            or name.split(".")[0] in {"requests", "httpx", "streamlit", "yfinance"}
            or name.endswith(".fmp")
            or "adaptive_valuation" in name
            for name in names
        ):
            forbidden_imports.append((node.lineno, names))
    assert not forbidden_imports
    text = path.read_text(encoding="utf-8")
    assert "MetricObservation" not in text
    assert "ValuationResult" not in text
    assert "fair_value" not in text
    assert "target_price" not in text


def test_historical_distribution_is_not_wired_into_coverage_or_legacy_execution():
    guarded = [
        PRODUCTION_ROOT / "services" / "coverage.py",
        PROJECT_ROOT / "app.py",
        PRODUCTION_ROOT / "analysis.py",
        PRODUCTION_ROOT / "valuation.py",
        PRODUCTION_ROOT / "adaptive_valuation.py",
    ]
    escaped = []
    for path in guarded:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                names = [item.name for item in node.names]
            elif isinstance(node, ast.Import):
                module = ""
                names = [item.name for item in node.names]
            else:
                continue
            if "services.valuation_history" in module or any(
                "services.valuation_history" in name
                or name.endswith("HistoricalMultipleDistribution")
                or name.endswith("build_historical_distribution")
                for name in names
            ):
                escaped.append((path.relative_to(PROJECT_ROOT).as_posix(), node.lineno))
    assert not escaped


def test_own_history_input_service_is_canonical_only_and_contains_no_valuation_arithmetic():
    path = PRODUCTION_ROOT / "services" / "own_history_inputs.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    forbidden_imports = []
    multiplications = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [item.name for item in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            names = []
        if any(
            name.startswith("stock_analyser.providers")
            or name.split(".")[0] in {"requests", "httpx", "streamlit", "yfinance"}
            or "adaptive_valuation" in name
            for name in names
        ):
            forbidden_imports.append((node.lineno, names))
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
            multiplications.append(node.lineno)
    assert not forbidden_imports
    assert not multiplications, "Milestone 7C must not multiply a historical multiple by a forward denominator"
    text = path.read_text(encoding="utf-8")
    assert "ValuationResult" not in text
    assert "target_price" not in text
    assert "fair_value" not in text
    assert "upside" not in text and "downside" not in text


def test_own_history_inputs_are_not_wired_into_legacy_execution_or_ui():
    guarded = [
        PROJECT_ROOT / "app.py", PRODUCTION_ROOT / "analysis.py", PRODUCTION_ROOT / "scoring.py",
        PRODUCTION_ROOT / "valuation.py", PRODUCTION_ROOT / "adaptive_valuation.py",
    ]
    escaped = []
    for path in guarded:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                names = [item.name for item in node.names]
            elif isinstance(node, ast.Import):
                module = ""
                names = [item.name for item in node.names]
            else:
                continue
            if "own_history_inputs" in module or any("own_history_inputs" in name for name in names):
                escaped.append((path.relative_to(PROJECT_ROOT).as_posix(), node.lineno))
    assert not escaped


def test_own_history_valuation_is_canonical_only_and_has_no_scale_or_external_reference_shortcuts():
    path = PRODUCTION_ROOT / "services" / "own_history_valuation.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    forbidden_imports = []
    suspicious_scale_arithmetic = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [item.name for item in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            names = []
        if any(
            name.startswith("stock_analyser.providers")
            or name.split(".")[0] in {"requests", "httpx", "streamlit", "yfinance"}
            or "adaptive_valuation" in name
            or name.endswith(".valuation")
            for name in names
        ):
            forbidden_imports.append((node.lineno, names))
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mult, ast.Div)):
            constants = [
                child.value for child in ast.walk(node)
                if isinstance(child, ast.Constant)
                and isinstance(child.value, (int, float))
                and not isinstance(child.value, bool)
            ]
            if any(abs(value) >= 1_000 for value in constants):
                suspicious_scale_arithmetic.append((node.lineno, constants))
    assert not forbidden_imports
    assert not suspicious_scale_arithmetic
    text = path.read_text(encoding="utf-8").lower()
    forbidden_concepts = {
        "externalvaluationreference", "analyst_target", "standard_dcf", "current_price",
        "upside", "downside", "margin_of_safety", "investment_stance", "peer_valuation",
        "aggregationstatus", "meta",
    }
    assert not {name for name in forbidden_concepts if name in text}


def test_own_history_numeric_valuation_is_not_wired_into_coverage_legacy_or_ui():
    guarded = [
        PRODUCTION_ROOT / "services" / "coverage.py",
        PROJECT_ROOT / "app.py",
        PRODUCTION_ROOT / "analysis.py",
        PRODUCTION_ROOT / "scoring.py",
        PRODUCTION_ROOT / "valuation.py",
        PRODUCTION_ROOT / "adaptive_valuation.py",
    ]
    escaped = []
    for path in guarded:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                names = [item.name for item in node.names]
            elif isinstance(node, ast.Import):
                module = ""
                names = [item.name for item in node.names]
            else:
                continue
            if "own_history_valuation" in module or any(
                "own_history_valuation" in name
                or name.endswith("OwnHistoryValuationResult")
                or name.endswith("calculate_own_history_valuation")
                for name in names
            ):
                escaped.append((path.relative_to(PROJECT_ROOT).as_posix(), node.lineno))
    assert not escaped
