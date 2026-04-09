"""
bye_schedule.py – NRL bye round schedule management.

The dictionary below is editable. Add entries for each year/round as the
NRL announces them. Teams listed are those NOT playing that round (on bye).

To persist byes into the database (used by the AI manager), run:
    python bye_schedule.py --load
"""

import argparse
import database as db

# ---------------------------------------------------------------------------
# Edit this dictionary to reflect the actual season bye schedule.
# Format:  BYE_SCHEDULE[year][round] = [list of teams on bye]
#
# NRL team names as returned by the SuperCoach API (nrl_club field):
#   Broncos, Raiders, Bulldogs, Sharks, Titans, Sea Eagles, Storm,
#   Knights, Cowboys, Eels, Panthers, Rabbitohs, Dragons, Roosters,
#   Warriors, Tigers, Dolphins, Bears (if applicable)
# ---------------------------------------------------------------------------
BYE_SCHEDULE: dict[int, dict[int, list[str]]] = {
    2025: {
        # --- Populate with actual 2025 NRL bye schedule ---
        # Example format:
        # 6:  ["Dolphins", "Newcastle Knights", "Penrith Panthers", "Wests Tigers"],
        # 7:  ["Brisbane Broncos", "Cronulla-Sutherland Sharks", "North Queensland Cowboys", "St George Illawarra Dragons"],
        # 8:  ["Canterbury-Bankstown Bulldogs", "Gold Coast Titans", "Melbourne Storm", "Parramatta Eels"],
        # 9:  ["Canberra Raiders", "Manly-Warringah Sea Eagles", "New Zealand Warriors", "South Sydney Rabbitohs"],
        # 12: ["Sydney Roosters"],       # Queen's Birthday weekend etc.
    },
    2026: {
        # --- Populate with actual 2026 NRL bye schedule ---
        # Byes are typically announced round-by-round during the season.
        # Update here and run: python bye_schedule.py --load
        #
        # Example:
        # 7:  ["Brisbane Broncos", "Gold Coast Titans"],
        # 8:  ["Parramatta Eels", "Penrith Panthers"],
    },
}


def get_teams_on_bye(year: int, round_num: int) -> list[str]:
    """Return teams on bye for the given year and round (checks DB first, then falls back to hardcoded)."""
    # Prefer DB (allows dynamic updates)
    db_teams = db.get_teams_on_bye(year, round_num)
    if db_teams:
        return db_teams
    # Fall back to hardcoded schedule
    return BYE_SCHEDULE.get(year, {}).get(round_num, [])


def is_team_on_bye(year: int, round_num: int, team: str) -> bool:
    teams = get_teams_on_bye(year, round_num)
    return any(team.lower() in t.lower() or t.lower() in team.lower() for t in teams)


def load_to_db():
    """Load the hardcoded BYE_SCHEDULE into the database."""
    db.init_db()
    count = 0
    for year, rounds in BYE_SCHEDULE.items():
        for rnd, teams in rounds.items():
            for team in teams:
                db.store_bye(year, rnd, team)
                count += 1
    print(f"Loaded {count} bye entries into the database.")


def add_bye(year: int, round_num: int, teams: list[str]):
    """Add bye entries for specific teams to the database."""
    db.init_db()
    for team in teams:
        db.store_bye(year, round_num, team)
    print(f"Added {len(teams)} team(s) on bye for {year} round {round_num}.")


def list_byes(year: int):
    """Print all stored bye data for a year."""
    from database import get_connection
    conn = get_connection()
    rows = conn.execute(
        "SELECT round, team FROM bye_schedule WHERE year = ? ORDER BY round, team",
        (year,),
    ).fetchall()
    conn.close()

    if not rows:
        print(f"No bye data stored for {year}. Edit bye_schedule.py and run: python bye_schedule.py --load")
        return

    current_round = None
    for row in rows:
        if row["round"] != current_round:
            current_round = row["round"]
            print(f"\nRound {current_round}:")
        print(f"  - {row['team']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NRL bye schedule management")
    parser.add_argument("--load", action="store_true", help="Load hardcoded schedule into DB")
    parser.add_argument("--list", type=int, metavar="YEAR", help="List stored byes for a year")
    parser.add_argument(
        "--add",
        nargs="+",
        metavar=("YEAR:ROUND", "TEAM"),
        help="Add byes: --add 2026:7 'Brisbane Broncos' 'Gold Coast Titans'",
    )
    args = parser.parse_args()

    if args.load:
        load_to_db()
    elif args.list:
        list_byes(args.list)
    elif args.add:
        year_round, *teams = args.add
        year, rnd = year_round.split(":")
        add_bye(int(year), int(rnd), teams)
    else:
        parser.print_help()
