# stdlib
import os
from types import SimpleNamespace
from urllib import parse

# third party
import pytest


class MockHTTPResponse:
    def __init__(
        self,
        url,
        payload=None,
        status_code=200,
        reason="OK",
        raise_http=False,
        json_error=None,
        text="",
        content=b"",
    ):
        self.url = url
        self._payload = {} if payload is None else payload
        self.status_code = status_code
        self.reason = reason
        self._raise_http = raise_http
        self._json_error = json_error
        self.text = text
        self.content = content

    def json(self):
        if self._json_error:
            raise self._json_error
        return self._payload

    def raise_for_status(self):
        if self._raise_http:
            raise RuntimeError(f"{self.status_code} {self.reason}")


class MockFuture:
    def __init__(self, response):
        self._response = response

    def result(self):
        return self._response


class MockSyncSession:
    def __init__(self, response_factory=None):
        self._response_factory = response_factory or MockHTTPResponse

    def get(self, url, params=None):
        params = params or {}
        query = parse.urlencode(params)
        response_url = f"{url}?{query}" if query else url
        symbol = params.get("symbol") or url.rsplit("/", 1)[-1]
        payload = {
            "quoteSummary": {"error": None, "result": [{"symbol": symbol.upper()}]},
            "finance": {"error": None, "result": [{"symbol": symbol.upper()}]},
        }
        return self._response_factory(response_url, payload)

    def post(self, url, params=None, json=None):
        params = params or {}
        query = parse.urlencode(params)
        response_url = f"{url}?{query}" if query else url
        payload = {"quoteSummary": {"error": None, "result": [json or {}]}}
        return self._response_factory(response_url, payload)


class MockAsyncSession(MockSyncSession):
    def get(self, url, params=None):
        return MockFuture(super().get(url, params=params))

    def post(self, url, params=None, json=None):
        return MockFuture(super().post(url, params=params, json=json))


class DummyCrumbSession:
    def __init__(self, text="crumb"):
        self._text = text

    def get(self, *args, **kwargs):
        return MockHTTPResponse(
            "https://query2.finance.yahoo.com/v1/test/getcrumb",
            payload={},
            text=self._text,
            content=b"",
        )


@pytest.fixture
def http_mocks():
    return SimpleNamespace(
        Response=MockHTTPResponse,
        Future=MockFuture,
        SyncSession=MockSyncSession,
        AsyncSession=MockAsyncSession,
    )


@pytest.fixture
def crumb_session_factory():
    def _factory(text="crumb"):
        return DummyCrumbSession(text=text)

    return _factory


def pytest_collection_modifyitems(config, items):
    run_integration = os.getenv("YQ_RUN_INTEGRATION") == "1"
    run_premium = os.getenv("YQ_RUN_PREMIUM") == "1"
    skip_integration = pytest.mark.skip(
        reason="Integration tests disabled. Set YQ_RUN_INTEGRATION=1 to enable."
    )
    skip_premium = pytest.mark.skip(
        reason="Premium tests disabled. Set YQ_RUN_PREMIUM=1 to enable."
    )
    for item in items:
        if "integration" in item.keywords and not run_integration:
            item.add_marker(skip_integration)
        if "premium" in item.keywords and not run_premium:
            item.add_marker(skip_premium)
