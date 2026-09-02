"""Controlled provider identities for the V1 infrastructure boundary."""

from enum import Enum


class ProviderId(str, Enum):
    YAHOO = "yahoo"
    FISCAL = "fiscal"
    FMP = "fmp"
    FINNHUB = "finnhub"
    FRED = "fred"
    DAMODARAN = "damodaran_nyu_stern"
    ALPHA_VANTAGE = "alpha_vantage"
    SEC = "sec"
