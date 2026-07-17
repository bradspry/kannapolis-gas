# gas

*by Brad Spry, Kannapolitan*

Finds the cheapest regular-grade gas prices near Kannapolis, NC (28083), using
[py-gasbuddy](https://pypi.org/project/py-gasbuddy/) plus a couple of
directly-scraped stations that aren't listed on GasBuddy. Applies known
rewards-program discounts, ranks stations by effective price, and tracks
week-over-week trends in a local SQLite database.

## Usage

```bash
pip install -r requirements.txt
python gas_prices.py
```

Example output:

```
Friday Fill-up ⛽
(Regular Grade) Lowest Gas Prices Near Kannapolis, NC (28083)
July 17, 2026
$2.699 — Sam's Club (123 Example Rd) (-$0.05 rewards, $2.749)
$2.759 — Shell (456 Sample Ave) (-$0.05 rewards, $2.809)
...
Since Last Week: Prices Trending ⬇️ (-$0.020)
Sources: GasBuddy, Dash In
```

Each run appends a snapshot to `gas_prices.db` (created automatically, not
tracked in git), which is used to compute the week-over-week trend line.

## Configuration

Everything is configured via constants at the top of `gas_prices.py`:

- `ZIP_CODES` — zip codes to search
- `REWARDS` / `REWARDS_LABEL` — per-station rewards discounts applied on top
  of the listed price
- `PINNED_STATIONS` — stations scraped directly from their own site because
  they aren't on GasBuddy
- `MAX_PRICE_AGE_HOURS` — ignore prices older than this

## License

GPLv3 — see [LICENSE](LICENSE).
