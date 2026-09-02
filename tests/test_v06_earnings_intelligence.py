import numpy as np

from stock_analyser.official_data import FilingRef, extract_sec_quarter_metrics
from stock_analyser.valuation import dcf_model


def _fact(val, start, end, form="10-Q", filed="2026-07-30"):
    return {"val": val, "start": start, "end": end, "form": form, "filed": filed}


def test_sec_quarter_metrics_extract_gaap_and_cashflow_proxy():
    companyfacts = {
        "facts": {"us-gaap": {
            "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
                _fact(60.0, "2026-04-01", "2026-06-30"),
            ]}},
            "OperatingIncomeLoss": {"units": {"USD": [
                _fact(18.0, "2026-04-01", "2026-06-30"),
            ]}},
            "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [
                _fact(20.0, "2026-01-01", "2026-03-31", filed="2026-04-30"),
                _fact(45.0, "2026-01-01", "2026-06-30"),
            ]}},
            "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": [
                _fact(9.0, "2026-01-01", "2026-03-31", filed="2026-04-30"),
                _fact(23.0, "2026-01-01", "2026-06-30"),
            ]}},
        }}
    }
    filing = FilingRef(form="10-Q", filed="2026-07-30", report_date="2026-06-30", accession="x", primary_document="x.htm")
    m = extract_sec_quarter_metrics(companyfacts, filing)
    assert m["revenue"] == 60.0
    assert m["operating_income"] == 18.0
    assert np.isclose(m["operating_margin"], 0.30)
    # Q2 CFO = 45 - 20; Q2 CapEx = 23 - 9; proxy FCF = 11.
    assert m["cfo"] == 25.0
    assert m["capex"] == 14.0
    assert m["fcf_proxy"] == 11.0


def test_sec_q4_can_be_derived_from_annual_less_q3_ytd():
    companyfacts = {
        "facts": {"us-gaap": {
            "Revenues": {"units": {"USD": [
                _fact(75.0, "2026-01-01", "2026-09-30", filed="2026-10-25"),
                _fact(110.0, "2026-01-01", "2026-12-31", form="10-K", filed="2027-02-10"),
            ]}},
            "OperatingIncomeLoss": {"units": {"USD": [
                _fact(15.0, "2026-01-01", "2026-09-30", filed="2026-10-25"),
                _fact(24.0, "2026-01-01", "2026-12-31", form="10-K", filed="2027-02-10"),
            ]}},
        }}
    }
    filing = FilingRef(form="10-K", filed="2027-02-10", report_date="2026-12-31", accession="x", primary_document="x.htm")
    m = extract_sec_quarter_metrics(companyfacts, filing)
    assert m["revenue"] == 35.0
    assert m["operating_income"] == 9.0


def test_dcf_cash_conversion_fade_changes_forecast_and_value():
    flat = dcf_model(100, 0.25, 0.2, 0.10, 0.09, 0.025, 0, 10, 0.50, end_growth=0.05)
    fade = dcf_model(100, 0.25, 0.2, 0.10, 0.09, 0.025, 0, 10, 0.50, end_growth=0.05, end_fcf_conversion=0.80)
    assert fade["forecast"]["fcf_conversion"].iloc[0] == 0.50
    assert np.isclose(fade["forecast"]["fcf_conversion"].iloc[-1], 0.80)
    assert fade["fair_value"] > flat["fair_value"]
