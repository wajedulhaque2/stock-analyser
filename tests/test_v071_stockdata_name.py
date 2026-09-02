from stock_analyser.models import StockData


def test_stockdata_name_prefers_long_name():
    data = StockData(ticker="META", info={"longName": "Meta Platforms, Inc.", "shortName": "Meta"})
    assert data.name == "Meta Platforms, Inc."


def test_stockdata_name_falls_back_to_ticker():
    data = StockData(ticker="META")
    assert data.name == "META"
