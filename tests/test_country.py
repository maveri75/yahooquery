import pytest

from yahooquery import Ticker
from yahooquery.constants import COUNTRIES

pytestmark = pytest.mark.integration


@pytest.fixture
def ticker():
    return Ticker("aapl", country="brazil")


def test_country_change(ticker):
    ticker.country = "hong kong"
    assert ticker.country == "hong kong"


def test_bad_country():
    with pytest.raises(ValueError):
        assert Ticker("aapl", country="china")


def test_default_query_param(ticker):
    expected = COUNTRIES[ticker.country]
    params = ticker.default_query_params

    for key, value in expected.items():
        assert params[key] == value

    if ticker.crumb is not None:
        assert params["crumb"] == ticker.crumb
    else:
        assert "crumb" not in params
