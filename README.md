# kannapolis-gas

*by Brad Spry, Kannapolitan*

Finds the cheapest regular or diesel gas prices near Kannapolis, NC (28083),
using [py-gasbuddy](https://pypi.org/project/py-gasbuddy/) plus a couple of
directly-scraped stations that aren't listed on GasBuddy. Ignores stale
listings, applies known rewards-program discounts, ranks stations by effective
price, and tracks week-over-week trends in a local SQLite database.

## Usage

```bash
pip install -r requirements.txt
python gas_prices.py                    # regular grade, default zip code(s)
python gas_prices.py --grade diesel
python gas_prices.py --grade combined      # both grades, one run
python gas_prices.py --zip 28083 28025
python gas_prices.py --limit 5
```

Example output:

```
Friday Fill-up ⛽
(Regular Grade) Lowest Gas Prices Near Kannapolis, NC (28083)
July 17, 2026

$2.699 — Sam's Club (123 Example Rd) (-$0.05 rewards)
$2.759 — Shell (456 Sample Ave) (-$0.05 rewards)
...

Since Last Week: Prices Trending ⬇️ (-$0.020)

Sources: GasBuddy, Dash In

191 days since the Military conflict involving Iran began 🛢️
```

Each run appends a snapshot to `gas_prices.db` (created automatically),
which is used to compute the week-over-week trending.

### Price freshness

GasBuddy prices are crowd-sourced and each listing carries a "last updated"
time. Anything older than `MAX_PRICE_AGE_HOURS` (24 by default) is dropped
before ranking, so a station showing a great price from last week won't appear.

Two things fall outside that filter:

- A GasBuddy listing with no "last updated" time at all is kept, since there's
  nothing to compare against.
- `PINNED_STATIONS` are scraped live from their own sites on every run, so they
  carry no age data and are current by definition.

## Configuration

Everything is configured via constants at the top of `gas_prices.py`:

- `ZIP_CODES` — default zip codes to search, overridable via `--zip`
- `LIMIT` — default number of stations to show, overridable via `--limit`
- `FUEL_GRADES` — fuel grades selectable via `--grade`
- `MAX_PRICE_AGE_HOURS` — ignore GasBuddy prices older than this (24)
- `REWARDS` / `REWARDS_LABEL` — per-station rewards discounts applied on top
  of the listed price
- `PINNED_STATIONS` — stations scraped directly from their own site because
  they aren't on GasBuddy

## License

GPLv3 — see [LICENSE](LICENSE).
