#!/usr/bin/env python3
"""Gas price scraper for Kannapolis, NC (28083) using py-gasbuddy."""

import argparse
import asyncio
import re
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import aiohttp
from py_gasbuddy import GasBuddy

ZIP_CODES = [28083]
CITY = "Kannapolis, NC"
LIMIT = 10
MAX_PRICE_AGE_HOURS = 48
DB_PATH = Path(__file__).parent / "gas_prices.db"

# Fuel grades this script can fetch. `field` is the GasBuddy API's price key,
# `pinned_label` is the label used to find the price on pinned stations' pages.
FUEL_GRADES: dict[str, dict[str, str]] = {
    "regular": {"field": "regular_gas", "label": "Regular Grade", "pinned_label": "Regular"},
    "diesel":  {"field": "diesel",      "label": "Diesel",        "pinned_label": "Diesel"},
}

# Rewards program discounts in $/gal. Keys are matched case-insensitively
# against station names. Set a value to 0.0 to exclude that program.
REWARDS: dict[str, float] = {
    "sam's club": 0.05,  # Plus membership: 5¢/gal on top of already-discounted member price
    "bp":         0.05,  # earnify™ (replaced BPme): pay with app
    "murphy":     0.10,  # Walmart+ benefit at Murphy USA
    "shell":      0.05,  # Fuel Rewards Gold status (free; auto-Gold for new members)
    "exxon":      0.03,  # Mobil Rewards+: 3¢/gal earned as points, no card required
    "mobil":      0.03,  # Same Exxon Mobil Rewards+ program, works at Mobil stations too
    "marathon":   0.05,  # Marathon ARCO Rewards: earn 5¢/gal, redeem at pump
    "pilot":      0.05,  # myRewards Plus base; activate monthly app offer for $0.10/gal
    "quiktrip":   0.05,  # QT Pay (app, ACH): 5¢/gal ongoing (25¢/gal introductory)
    "dash in":    0.25,  # Dash In Rewards: 25¢/gal for first 60 days; 5¢/gal thereafter
}

# Custom label overrides for rewards programs (defaults to "rewards")
REWARDS_LABEL: dict[str, str] = {
    "quiktrip": "QT Pay",
}

# Stations not on GasBuddy — prices scraped directly from their own sites.
PINNED_STATIONS: list[dict] = [
    {
        "name": "Dash In",
        "address": "970 Vinehaven Drive NE",
        "url": "https://locations.dashin.com/l/nc/970-vinehaven-drive-northeast-28025/6266057",
    },
]


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _open_db() -> sqlite3.Connection:
    """Open the SQLite DB, creating the snapshots table if needed."""
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS snapshots (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at    TEXT NOT NULL,
            name      TEXT NOT NULL,
            address   TEXT NOT NULL,
            price     REAL NOT NULL,
            grade     TEXT NOT NULL DEFAULT 'regular'
        )
    """)
    try:
        con.execute("ALTER TABLE snapshots ADD COLUMN grade TEXT NOT NULL DEFAULT 'regular'")
    except sqlite3.OperationalError:
        pass  # column already exists
    con.commit()
    return con


def save_snapshot(stations: list[dict], grade: str) -> None:
    """Record the current station prices as a single timestamped run."""
    run_at = datetime.now(tz=timezone.utc).isoformat()
    con = _open_db()
    con.executemany(
        "INSERT INTO snapshots (run_at, name, address, price, grade) VALUES (?, ?, ?, ?, ?)",
        [(run_at, s["name"], s["address"], s["price"], grade) for s in stations],
    )
    con.commit()
    con.close()


def last_week_avg(grade: str) -> float | None:
    """Return the average price from the run closest to 7 days ago, or None."""
    con = _open_db()
    # Find the run_at timestamp nearest to 7 days ago
    row = con.execute("""
        SELECT run_at
        FROM snapshots
        WHERE run_at < datetime('now', '-3 days') AND grade = ?
        ORDER BY ABS(julianday(run_at) - julianday('now', '-7 days'))
        LIMIT 1
    """, (grade,)).fetchone()
    if not row:
        con.close()
        return None
    target_run = row[0]
    avg = con.execute(
        "SELECT AVG(price) FROM snapshots WHERE run_at = ? AND grade = ?", (target_run, grade)
    ).fetchone()[0]
    con.close()
    return avg


# ---------------------------------------------------------------------------
# GasBuddy fetch
# ---------------------------------------------------------------------------

async def _fetch_one_zip(gb: "GasBuddy", zip_code: int, limit: int, fuel_field: str) -> list[dict]:
    """Fetch station info and `fuel_field`-grade prices for a single zip code."""
    locations, prices_data = await asyncio.gather(
        gb.location_search(zipcode=zip_code),
        gb.price_lookup_service(zipcode=zip_code, limit=limit * 2),
    )

    station_info: dict[str, dict] = {}
    for s in locations["data"]["locationBySearchTerm"]["stations"]["results"]:
        station_info[s["id"]] = {
            "name": s["name"],
            "address": _fmt_address((s.get("address") or {}).get("line1", "")),
        }

    now = datetime.now(tz=timezone.utc)
    results = []
    for entry in prices_data.get("results", []):
        sid = entry.get("station_id")
        reg = entry.get(fuel_field) or {}
        price = reg.get("price")
        if price is None:
            continue

        last_updated = reg.get("last_updated")
        if last_updated:
            updated_at = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
            if (now - updated_at).total_seconds() / 3600 > MAX_PRICE_AGE_HOURS:
                continue

        info = station_info.get(str(sid), {})
        results.append(
            {
                "price": float(price),
                "name": info.get("name", f"Station {sid}"),
                "address": info.get("address", ""),
            }
        )
    return results


async def _fetch_pinned_stations(pinned_label: str) -> list[dict]:
    """Scrape `pinned_label`-grade prices for stations not listed on GasBuddy."""
    results = []
    async with aiohttp.ClientSession() as session:
        for station in PINNED_STATIONS:
            try:
                async with session.get(station["url"], timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    html = await resp.text()
                pattern = rf'{re.escape(pinned_label)}(?:<!-- -->)?:.*?\$([0-9]+\.[0-9]+)</span>'
                m = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
                if m:
                    results.append({
                        "name": station["name"],
                        "address": station["address"],
                        "price": float(m.group(1)),
                    })
            except Exception:
                pass  # skip silently if the site is unreachable
    return results


async def fetch_prices(zip_codes: list[int], limit: int, fuel_field: str, pinned_label: str) -> list[dict]:
    """Fetch GasBuddy + pinned station prices, dedupe, and return the cheapest `limit`."""
    gb = GasBuddy()
    all_results, pinned = await asyncio.gather(
        asyncio.gather(*[_fetch_one_zip(gb, z, limit, fuel_field) for z in zip_codes]),
        _fetch_pinned_stations(pinned_label),
    )

    seen: set[str] = set()
    merged = []
    for batch in all_results:
        for s in batch:
            key = (s["name"].lower(), s["address"].lower())
            if key not in seen:
                seen.add(key)
                merged.append(s)

    for s in pinned:
        key = (s["name"].lower(), s["address"].lower())
        if key not in seen:
            seen.add(key)
            merged.append(s)

    merged.sort(key=lambda x: x["price"])
    return merged[:limit]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

# Abbreviations that title() lowercases incorrectly in addresses
_ADDR_ABBR = re.compile(r'\b(Ne|Nw|Se|Sw|Nc)\b')

def _fmt_address(address: str) -> str:
    """Title-case an address while keeping directional/state abbreviations uppercase."""
    return _ADDR_ABBR.sub(lambda m: m.group().upper(), address.title())


def _fmt(price: float) -> str:
    """Format a price to 2–3 decimal places, never dropping below 2."""
    s = f"{price:.3f}"
    return s[:-1] if s[-1] == "0" else s


def _rewards_discount(station_name: str) -> float:
    """Look up the per-gallon rewards discount for a station, if any."""
    name_lower = station_name.lower()
    for keyword, discount in REWARDS.items():
        if keyword in name_lower:
            return discount
    return 0.0


def _rewards_label(station_name: str) -> str:
    """Look up the display label for a station's rewards program."""
    name_lower = station_name.lower()
    for keyword, label in REWARDS_LABEL.items():
        if keyword in name_lower:
            return label
    return "rewards"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Find the cheapest gas prices near Kannapolis, NC.")
    parser.add_argument(
        "--grade", choices=sorted(FUEL_GRADES) + ["combined"], default="regular",
        help="fuel grade to fetch, or 'combined' for both (default: regular)",
    )
    parser.add_argument(
        "--zip", type=int, nargs="+", default=ZIP_CODES,
        help=f"zip code(s) to search (default: {' '.join(str(z) for z in ZIP_CODES)})",
    )
    parser.add_argument(
        "--limit", type=int, default=LIMIT,
        help=f"number of stations to show (default: {LIMIT})",
    )
    return parser.parse_args()


def _pinned_sources(stations: list[dict]) -> list[str]:
    """Return the names of pinned (non-GasBuddy) stations present in `stations`, deduped."""
    pinned_names = {p["name"].lower() for p in PINNED_STATIONS}
    seen: set[str] = set()
    result = []
    for s in stations:
        if s["name"].lower() in pinned_names and s["name"] not in seen:
            seen.add(s["name"])
            result.append(s["name"])
    return result


def _run_grade(grade_key: str, args: argparse.Namespace, show_sources: bool = True) -> list[dict]:
    """Fetch, print, and save a snapshot for one fuel grade. Returns the stations found."""
    grade = FUEL_GRADES[grade_key]
    stations = asyncio.run(fetch_prices(args.zip, args.limit, grade["field"], grade["pinned_label"]))

    if not stations:
        print(f"No {grade['label'].lower()} prices found.")
        return []

    stations.sort(key=lambda s: s["price"] - _rewards_discount(s["name"]))

    for s in stations:
        discount = _rewards_discount(s["name"])
        effective = s["price"] - discount
        eff_str = _fmt(effective)
        short_addr = re.sub(r'^\d+\s+', '', s["address"])
        if discount > 0:
            label = _rewards_label(s["name"])
            rewards_note = f" (-${_fmt(discount)} {label})"
        else:
            rewards_note = ""
        print(f"${eff_str} — {s['name']} ({short_addr}){rewards_note}")

    current_avg = sum(s["price"] for s in stations) / len(stations)
    prior_avg = last_week_avg(grade_key)

    save_snapshot(stations, grade_key)

    if prior_avg is not None:
        delta = current_avg - prior_avg
        delta_str = f"+${_fmt(delta)}" if delta >= 0 else f"-${_fmt(abs(delta))}"
        if current_avg > prior_avg + 0.005:
            trend = f"Since Last Week: Prices Trending ⬆️ ({delta_str})"
        elif current_avg < prior_avg - 0.005:
            trend = f"Since Last Week: Prices Trending ⬇️ ({delta_str})"
        else:
            trend = "Since Last Week: Prices Trending ➡️ (steady)"
        print()
        print(trend)

    if show_sources:
        print()
        sources = ["GasBuddy"] + _pinned_sources(stations)
        print(f"Sources: {', '.join(sources)}")
    return stations


def main() -> None:
    """Fetch prices, print the ranked list(s) with trend info, and save snapshot(s)."""
    args = _parse_args()

    today = date.today().strftime("%B %d, %Y")
    print("Friday Fill-up ⛽")
    zip_label = "/".join(str(z) for z in args.zip)

    if args.grade == "combined":
        print(f"Lowest Gas Prices Near {CITY} ({zip_label})")
        print(today)
        print()
        all_stations: list[dict] = []
        for i, grade_key in enumerate(FUEL_GRADES):
            if i > 0:
                print()
            print(f"{FUEL_GRADES[grade_key]['label']}:")
            all_stations += _run_grade(grade_key, args, show_sources=False)
        if not all_stations:
            sys.exit(1)
        print()
        sources = ["GasBuddy"] + _pinned_sources(all_stations)
        print(f"Sources: {', '.join(sources)}")
    else:
        grade = FUEL_GRADES[args.grade]
        print(f"({grade['label']}) Lowest Gas Prices Near {CITY} ({zip_label})")
        print(today)
        print()
        if not _run_grade(args.grade, args):
            sys.exit(1)


if __name__ == "__main__":
    main()
