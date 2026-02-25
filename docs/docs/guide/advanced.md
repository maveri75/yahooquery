!!! info
**For Yahoo Finance Premium Subscribers**

There might be a use case for combining the functionalities of both the Ticker and Research class. And, ideally, the user wouldn't have to utilize the login functionality in both instances. Here's how you would do that:

```python
from yahooquery import Research, Ticker

r = Research(username='username@yahoo.com', password='password')

# I want to retrieve last week's Bullish Analyst Report's
# for the Financial Services sector
df = r.reports(
    sector='Financial Services',
    report_date='Last Week',
    investment_rating='Bullish',
    report_type='Analyst Report'
)

# But now I want to get the data I find relevant and run my own analysis

# Using aapl as a default symbol (we will change that later).
# But, the important part is passing the current session and crumb
# from our Research instance
tickers = Ticker('aapl', session=r.session, crumb=r.crumb)

# Now, I can loop through the dataframe and retrieve relevant data for
# each ticker within the dataframe utilizing the Ticker instance
for i, row in df.iterrows():
    tickers.symbols = row['Tickers']
    data = tickers.p_company_360
    # Do something with data
    # ...

# Or, pass all tickers to the Ticker instance
ticker_list = df['Tickers'].tolist()
ticker_list = list(set(flatten_list(ticker_list)))
tickers = Ticker(ticker_list, session=r.session, crumb=r.crumb)
data = tickers.p_company_360
# Do something with data
# ...
```

## Performance Best Practices

For high-frequency workflows (for example, 1-minute snapshots), use these defaults:

1. Reuse `Ticker` instances for the full process lifetime.
2. Use `asynchronous=True` when fetching many symbols at once.
3. Tune `max_workers` based on concurrency needs and provider limits.

```python
from yahooquery import Ticker

symbols = "^SPX ^VIX ^VVIX SPY UVXY VXX"
t = Ticker(symbols, asynchronous=True, max_workers=8, timeout=5)

# Reuse t across cycles instead of creating a new Ticker each minute.
snapshot = t.quotes
```

## Fast Snapshots vs Heavy Endpoints

Use lightweight endpoints for frequent polling and move heavier endpoints to lower-frequency jobs.

### Frequent (snapshot friendly)

```python
from yahooquery import Ticker

t = Ticker("^SPX ^VIX ^VVIX", asynchronous=True, max_workers=8)
quotes = t.quotes
```

### Heavy (schedule less often)

```python
from yahooquery import Ticker

t = Ticker("^SPX")
all_options = t.option_chain      # full chain for all expirations
all_modules = t.all_modules       # large quote summary payload
history = t.history(period="max") # broad historical pull
```

When you need options snapshots at high cadence, prefer one expiration at a time:

```python
from yahooquery import Ticker

t = Ticker("^SPX")
one_expiration = t._get_data("options", {"date": 1778976000})
```

## Optional In-Memory Cache Evaluation

For real-time quotes/options snapshots, response caching is usually not recommended because stale data risk is high.

Cache can still be useful for near-static metadata:

- expiration calendars
- symbol validation results
- infrequent module payloads

Recommended policy:

- keep cache disabled by default
- enable only for metadata endpoints
- use short TTL windows (for example, 5-30 seconds)
- never cache critical tick-by-tick snapshot payloads
