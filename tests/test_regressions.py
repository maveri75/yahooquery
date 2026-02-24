import yahooquery.base as base_module
import yahooquery.misc as misc
from yahooquery import Ticker
from yahooquery.constants import COUNTRIES


class DummyResponse:
    def __init__(self, text="crumb"):
        self.text = text
        self.url = "https://query2.finance.yahoo.com/v1/test/getcrumb"
        self.content = b""


class DummySession:
    def __init__(self, text="crumb"):
        self._text = text

    def get(self, *args, **kwargs):
        return DummyResponse(self._text)


def test_default_query_params_does_not_mutate_country_defaults():
    country_params = COUNTRIES["united states"]
    original = country_params.copy()
    try:
        country_params.pop("crumb", None)
        ticker = Ticker("aapl", session=DummySession("crumb-123"))
        params = ticker.default_query_params
        assert params["crumb"] == "crumb-123"
        assert "crumb" not in country_params
    finally:
        country_params.clear()
        country_params.update(original)


def test_option_chain_handles_missing_contract_keys(monkeypatch):
    ticker = Ticker("aapl", session=DummySession("crumb-123"))
    monkeypatch.setattr(
        ticker,
        "_get_data",
        lambda key, params=None, **kwargs: {"aapl": {"options": [{}]}},
    )
    assert ticker.option_chain == "No option chain data found"


def test_make_request_does_not_reuse_default_params(monkeypatch):
    captured = []

    class FakeResponse:
        def json(self):
            return {"ok": True}

    class FakeSession:
        def get(self, url, params=None, json=None):
            captured.append(dict(params))
            return FakeResponse()

    monkeypatch.setattr(misc, "initialize_session", lambda **kwargs: FakeSession())
    misc._make_request("https://example.com", country="United States")
    misc._make_request("https://example.com")

    assert "lang" in captured[0]
    assert captured[1] == {}


def test_ticker_passes_setup_url_to_session_initializer(monkeypatch):
    captured = {}
    session = DummySession("crumb-123")

    def fake_initialize_session(existing_session=None, **kwargs):
        captured["session"] = existing_session
        captured["kwargs"] = kwargs
        return existing_session or session

    monkeypatch.setattr(base_module, "initialize_session", fake_initialize_session)
    monkeypatch.setattr(base_module, "get_crumb", lambda _session: "crumb-123")

    Ticker("aapl", session=session, setup_url="https://finance.yahoo.com/quote/AAPL")
    assert captured["kwargs"]["url"] == "https://finance.yahoo.com/quote/AAPL"
