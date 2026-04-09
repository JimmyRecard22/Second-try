"""
scraper.py – Fetch NRL SuperCoach player data from the official API
and store it in the local SQLite database.

Usage:
    python scraper.py                        # scrape 2026 season, all rounds attempted
    python scraper.py --year 2025            # scrape a specific year
    python scraper.py --year 2026 --rounds 1-10   # scrape rounds 1 to 10
    python scraper.py --year 2026 --rounds 5      # scrape only round 5
"""

import argparse
import json
import time
import requests

import database as db

API_URL = "https://www.supercoach.com.au/{year}/api/nrl/classic/v1/players-cf"
API_PARAMS = "embed=notes%2Codds%2Cplayer_stats%2Cpositions&round={round}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.5",
    "Connection": "keep-alive",
    "Referer": "https://www.foxsports.com.au/",
}


def fetch_round(year: int, rnd: int) -> list[dict] | None:
    """Return raw player list for a given year/round, or None if unavailable."""
    url = f"{API_URL.format(year=year)}?{API_PARAMS.format(round=rnd)}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        players = resp.json()
    except requests.RequestException as exc:
        print(f"  [!] Network error for round {rnd}: {exc}")
        return None
    except ValueError:
        print(f"  [!] JSON decode error for round {rnd}")
        return None

    if not players:
        return None

    # If the API returns data for a different round the requested round hasn't
    # been played yet – skip it (same guard used in the reference repo).
    first_stats = players[0].get("player_stats") if players else []
    if first_stats and isinstance(first_stats, list) and first_stats:
        returned_round = first_stats[0].get("round")
        if returned_round is not None and returned_round != rnd:
            return None

    return players


def store_players(year: int, rnd: int, players: list[dict]):
    """Persist a round's worth of player data to SQLite."""
    conn = db.get_connection()

    for player in players:
        pid = player.get("id")
        if not pid:
            continue

        first = player.get("first_name", "")
        last = player.get("last_name", "")
        full = f"{first} {last}".strip()
        club = player.get("nrl_club", "")
        price = player.get("price", 0)

        # Store / update player record
        db.upsert_player(
            conn,
            {
                "id": pid,
                "first_name": first,
                "last_name": last,
                "full_name": full,
                "nrl_club": club,
                "price": price,
                "raw_json": json.dumps(player),
            },
        )

        # Store per-round stats
        stats_list = player.get("player_stats", [])
        if stats_list and isinstance(stats_list, list) and len(stats_list) > 0:
            s = stats_list[0]
            db.upsert_stat(
                conn,
                {
                    "player_id": pid,
                    "year": year,
                    "round": rnd,
                    "score": s.get("score"),
                    "be": s.get("be"),
                    "price": price,
                    "raw_json": json.dumps(s),
                },
            )

        # Store positions (insert-only – positions rarely change)
        positions = player.get("positions", [])
        if positions and isinstance(positions, list):
            for pos in positions:
                db.upsert_position(
                    conn,
                    {
                        "player_id": pid,
                        "position_id": pos.get("position", 0),
                        "position_name": pos.get("position_name", ""),
                    },
                )

    conn.commit()
    conn.close()
    print(f"  Stored {len(players)} players for {year} round {rnd}")


def scrape(year: int, rounds: list[int], delay: float = 0.5):
    """Main scrape loop."""
    db.init_db()
    scraped = 0
    skipped = 0

    for rnd in rounds:
        print(f"Fetching {year} round {rnd}…", end=" ", flush=True)
        players = fetch_round(year, rnd)

        if players is None:
            print("no data (round not yet played or API unavailable)")
            skipped += 1
            # Stop early if three consecutive rounds have no data
            if skipped >= 3:
                print("Three consecutive rounds with no data – stopping early.")
                break
        else:
            skipped = 0
            store_players(year, rnd, players)
            scraped += 1

        time.sleep(delay)

    print(f"\nDone. Scraped {scraped} round(s) for {year}.")


def parse_rounds(spec: str) -> list[int]:
    """Parse a round specification like '1-10' or '5' into a list of ints."""
    if "-" in spec:
        start, end = spec.split("-", 1)
        return list(range(int(start), int(end) + 1))
    return [int(spec)]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape NRL SuperCoach player stats")
    parser.add_argument("--year", type=int, default=2026, help="Season year (default: 2026)")
    parser.add_argument(
        "--rounds",
        type=str,
        default="1-27",
        help="Round range to scrape, e.g. '1-10' or '5' (default: 1-27)",
    )
    args = parser.parse_args()

    rounds = parse_rounds(args.rounds)
    print(f"Scraping {args.year} rounds {rounds[0]}–{rounds[-1]}…\n")
    scrape(args.year, rounds)
