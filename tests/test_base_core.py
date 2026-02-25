import pytest

import yahooquery.base as base_module
from yahooquery.base import _YahooFinance
from yahooquery.constants import CONFIG, validate_config_schema

pytestmark = pytest.mark.unit


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


def test_get_data_sync_symbol_query(monkeypatch, http_mocks):
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
    client = make_instance(http_mocks.SyncSession())
    data = client._get_data(key)
    assert data == {"aapl": {"symbol": "AAPL"}, "msft": {"symbol": "MSFT"}}


def test_get_data_async_symbol_query(monkeypatch, http_mocks):
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
    monkeypatch.setattr(base_module, "FuturesSession", http_mocks.AsyncSession)
    monkeypatch.setattr(base_module, "as_completed", lambda urls: urls)
    client = make_instance(http_mocks.AsyncSession())
    data = client._get_data(key)
    assert data == {"aapl": {"symbol": "AAPL"}, "msft": {"symbol": "MSFT"}}


def test_get_data_supports_legacy_response_field(monkeypatch, http_mocks):
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
    client = make_instance(http_mocks.SyncSession(), symbols=["aapl"])
    data = client._get_data(key)
    assert data == {"aapl": {"symbol": "AAPL"}}


def test_validate_response_shapes(http_mocks):
    client = make_instance(http_mocks.SyncSession(), symbols=["aapl"])
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
    # Should not raise for package endpoint map.
    validate_config_schema(CONFIG)


def test_config_schema_validation_rejects_inconsistent_entries():
    with pytest.raises(ValueError):
        validate_config_schema(
            {
                "broken": {
                    "path": "https://example.test/{symbol}",
                    "response_field": "finance",
                    "query": {
                        "symbol": {"required": True, "default": None},
                        "symbols": {"required": False, "default": None},
                    },
                }
            }
        )


def test_extract_response_json_handles_http_status_error(http_mocks):
    client = make_instance(http_mocks.SyncSession(), symbols=["aapl"])
    response = http_mocks.Response(
        "https://example.test/data",
        {},
        status_code=503,
        reason="Service Unavailable",
        raise_http=True,
    )
    data = client._extract_response_json(response, "quoteSummary")
    assert data == "HTTP 503 Service Unavailable"


def test_extract_response_json_handles_invalid_json(http_mocks):
    client = make_instance(http_mocks.SyncSession(), symbols=["aapl"])
    response = http_mocks.Response(
        "https://example.test/data",
        {},
        status_code=502,
        reason="Bad Gateway",
        json_error=ValueError("bad json"),
    )
    data = client._extract_response_json(response, "quoteSummary")
    assert data == "HTTP 502 Invalid JSON response"


def test_get_data_returns_error_dict_when_request_method_fails(monkeypatch, http_mocks):
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
    monkeypatch.setattr(base_module, "FuturesSession", http_mocks.AsyncSession)
    client = make_instance(http_mocks.AsyncSession())
    monkeypatch.setattr(
        client,
        "_async_requests",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert client._get_data(key) == {"error": "boom"}
