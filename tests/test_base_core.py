from urllib import parse

import pytest

import yahooquery.base as base_module
from yahooquery.base import _YahooFinance
from yahooquery.constants import CONFIG


class FakeResponse:
    def __init__(
        self,
        url,
        payload,
        status_code=200,
        reason="OK",
        raise_http=False,
        json_error=None,
    ):
        self.url = url
        self._payload = payload
        self.status_code = status_code
        self.reason = reason
        self._raise_http = raise_http
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise self._json_error
        return self._payload

    def raise_for_status(self):
        if self._raise_http:
            raise RuntimeError(f"{self.status_code} {self.reason}")


class FakeFuture:
    def __init__(self, response):
        self._response = response

    def result(self):
        return self._response


class FakeSyncSession:
    def get(self, url, params=None):
        params = params or {}
        query = parse.urlencode(params)
        response_url = f"{url}?{query}" if query else url
        symbol = params.get("symbol") or url.rsplit("/", 1)[-1]
        payload = {
            "quoteSummary": {"error": None, "result": [{"symbol": symbol.upper()}]},
            "finance": {"error": None, "result": [{"symbol": symbol.upper()}]},
        }
        return FakeResponse(response_url, payload)

    def post(self, url, params=None, json=None):
        params = params or {}
        query = parse.urlencode(params)
        response_url = f"{url}?{query}" if query else url
        payload = {"quoteSummary": {"error": None, "result": [json or {}]}}
        return FakeResponse(response_url, payload)


class FakeAsyncSession(FakeSyncSession):
    def get(self, url, params=None):
        return FakeFuture(super().get(url, params=params))

    def post(self, url, params=None, json=None):
        return FakeFuture(super().post(url, params=params, json=json))


def make_instance(session, symbols=None):
    instance = object.__new__(_YahooFinance)
    instance.progress = False
    instance.crumb = None
    instance._country_params = {}
    instance._symbols = symbols or ["aapl", "msft"]
    instance.session = session
    return instance


def test_get_response_field_accepts_standard_and_legacy_keys():
    assert _YahooFinance._get_response_field({"response_field": "finance"}, "k") == (
        "finance"
    )
    assert _YahooFinance._get_response_field({"responseField": "finance"}, "k") == (
        "finance"
    )


def test_get_response_field_raises_for_missing_value():
    with pytest.raises(KeyError):
        _YahooFinance._get_response_field({"path": "x"}, "broken")


def test_get_data_sync_symbol_query(monkeypatch):
    key = "unit_sync_symbol"
    monkeypatch.setitem(
        base_module.CONFIG,
        key,
        {
            "path": "https://example.test/data",
            "response_field": "quoteSummary",
            "query": {"symbol": {"required": True, "default": None}},
        },
    )
    client = make_instance(FakeSyncSession())
    data = client._get_data(key)
    assert data == {"aapl": {"symbol": "AAPL"}, "msft": {"symbol": "MSFT"}}


def test_get_data_async_symbol_query(monkeypatch):
    key = "unit_async_symbol"
    monkeypatch.setitem(
        base_module.CONFIG,
        key,
        {
            "path": "https://example.test/data",
            "response_field": "quoteSummary",
            "query": {"symbol": {"required": True, "default": None}},
        },
    )
    monkeypatch.setattr(base_module, "FuturesSession", FakeAsyncSession)
    monkeypatch.setattr(base_module, "as_completed", lambda urls: urls)
    client = make_instance(FakeAsyncSession())
    data = client._get_data(key)
    assert data == {"aapl": {"symbol": "AAPL"}, "msft": {"symbol": "MSFT"}}


def test_get_data_supports_legacy_response_field(monkeypatch):
    key = "unit_legacy_response_field"
    monkeypatch.setitem(
        base_module.CONFIG,
        key,
        {
            "path": "https://example.test/data/{symbol}",
            "responseField": "finance",
            "query": {},
        },
    )
    client = make_instance(FakeSyncSession(), symbols=["aapl"])
    data = client._get_data(key)
    assert data == {"aapl": {"symbol": "AAPL"}}


def test_validate_response_shapes():
    client = make_instance(FakeSyncSession(), symbols=["aapl"])
    assert (
        client._validate_response(
            {"quoteSummary": {"error": {"description": "bad"}, "result": []}},
            "quoteSummary",
        )
        == "bad"
    )
    assert (
        client._validate_response(
            {"quoteSummary": {"error": None, "result": []}}, "quoteSummary"
        )
        == "No data found"
    )
    assert (
        client._validate_response({"finance": {"error": {"description": "bad"}}}, "x")
        == "bad"
    )
    wrapped = client._validate_response({"foo": "bar"}, "quoteSummary")
    assert wrapped == {"quoteSummary": {"result": [{"foo": "bar"}]}}


def test_config_entries_have_expected_shape():
    for key, config in CONFIG.items():
        assert "path" in config, key
        assert "query" in config and isinstance(config["query"], dict), key
        assert "response_field" in config or "responseField" in config, key


def test_extract_response_json_handles_http_status_error():
    client = make_instance(FakeSyncSession(), symbols=["aapl"])
    response = FakeResponse(
        "https://example.test/data",
        {},
        status_code=503,
        reason="Service Unavailable",
        raise_http=True,
    )
    data = client._extract_response_json(response, "quoteSummary")
    assert data == "HTTP 503 Service Unavailable"


def test_extract_response_json_handles_invalid_json():
    client = make_instance(FakeSyncSession(), symbols=["aapl"])
    response = FakeResponse(
        "https://example.test/data",
        {},
        status_code=502,
        reason="Bad Gateway",
        json_error=ValueError("bad json"),
    )
    data = client._extract_response_json(response, "quoteSummary")
    assert data == "HTTP 502 Invalid JSON response"


def test_get_data_returns_error_dict_when_request_method_fails(monkeypatch):
    key = "unit_async_failure"
    monkeypatch.setitem(
        base_module.CONFIG,
        key,
        {
            "path": "https://example.test/data",
            "response_field": "quoteSummary",
            "query": {"symbol": {"required": True, "default": None}},
        },
    )
    monkeypatch.setattr(base_module, "FuturesSession", FakeAsyncSession)
    client = make_instance(FakeAsyncSession())
    monkeypatch.setattr(
        client,
        "_async_requests",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert client._get_data(key) == {"error": "boom"}
