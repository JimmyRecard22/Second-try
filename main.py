#!/usr/bin/env python3
"""
main.py – NRL SuperCoach AI Manager CLI

Usage:
    python main.py

Special commands (type these at the prompt):
    /scrape [year] [rounds]   Scrape data from the SuperCoach API
                              Examples:
                                /scrape               → current year, all rounds
                                /scrape 2025          → 2025 season, all rounds
                                /scrape 2026 1-10     → 2026 rounds 1-10
                                /scrape 2026 5        → round 5 only

    /team                     Show your current squad
    /add <player name>        Add a player to your team (AI will find the ID)
    /remove <player name>     Remove a player from your team
    /captain <player name>    Set your captain
    /vc <player name>         Set your vice-captain
    /byes [year] [round]      Show bye schedule
    /rounds [year]            Show which rounds have been scraped
    /help                     Show this help
    /quit                     Exit

Anything else is sent to your AI manager.
"""

import sys
from datetime import date

import database as db
import ai_manager as ai
import bye_schedule as bye_sched

CURRENT_YEAR = date.today().year

BANNER = f"""
╔══════════════════════════════════════════════╗
║   NRL SuperCoach AI Manager – {CURRENT_YEAR} Season   ║
╚══════════════════════════════════════════════╝
Type /help for commands, or just ask anything.
"""


# ---------------------------------------------------------------------------
# Quick team helpers (find player by name, delegate updates to DB directly)
# ---------------------------------------------------------------------------

def find_player_by_name(name: str) -> dict | None:
    """Return the first player matching name, or None."""
    results = db.search_players(name=name, year=CURRENT_YEAR, limit=1)
    return results[0] if results else None


def cmd_add(args: list[str], conversation: list[dict]) -> str:
    name = " ".join(args)
    player = find_player_by_name(name)
    if not player:
        return f"Player '{name}' not found. Make sure data is scraped and try a different name."
    db.add_to_my_team(player["id"])
    return f"Added {player['full_name']} ({player['nrl_club']}) to your team."


def cmd_remove(args: list[str], conversation: list[dict]) -> str:
    name = " ".join(args)
    player = find_player_by_name(name)
    if not player:
        return f"Player '{name}' not found."
    db.remove_from_my_team(player["id"])
    return f"Removed {player['full_name']} from your team."


def cmd_captain(args: list[str], vice: bool = False) -> str:
    name = " ".join(args)
    player = find_player_by_name(name)
    if not player:
        return f"Player '{name}' not found."
    db.set_captain(player["id"], vice=vice)
    role = "vice-captain" if vice else "captain"
    return f"{player['full_name']} set as {role}."


def cmd_team() -> str:
    team = db.get_my_team(CURRENT_YEAR)
    if not team:
        return "Your team is empty. Use /add <name> to add players."
    lines = [f"Your Squad ({len(team)} players):", ""]
    for p in team:
        role = " [C]" if p["is_captain"] else (" [VC]" if p["is_vice_captain"] else "")
        avg = p["avg_score"] or "–"
        last = p["last_score"] if p["last_score"] is not None else "–"
        price = f"${p['price']:,}" if p["price"] else "–"
        lines.append(
            f"  {p['full_name']:<25} {p['positions'] or '?':<10} "
            f"Avg: {avg:<6} Last: {last:<5} {price}{role}"
        )
    return "\n".join(lines)


def cmd_byes(args: list[str]) -> str:
    year = CURRENT_YEAR
    rnd = None
    if args:
        try:
            first = int(args[0])
            if first > 30:          # treat large numbers as year
                year = first
                rnd = int(args[1]) if len(args) > 1 else None
            else:
                rnd = first
        except ValueError:
            pass

    if rnd is not None:
        teams = bye_sched.get_teams_on_bye(year, rnd)
        if not teams:
            return (
                f"No bye data for {year} round {rnd}. "
                "Edit bye_schedule.py and run: python bye_schedule.py --load"
            )
        return f"Round {rnd} byes ({year}): " + ", ".join(teams)
    else:
        # List all bye rounds in the year
        from database import get_connection
        conn = get_connection()
        rows = conn.execute(
            "SELECT round, GROUP_CONCAT(team, ', ') as teams "
            "FROM bye_schedule WHERE year = ? GROUP BY round ORDER BY round",
            (year,),
        ).fetchall()
        conn.close()
        if not rows:
            return f"No bye data stored for {year}."
        lines = [f"Bye schedule for {year}:"]
        for row in rows:
            lines.append(f"  Round {row['round']}: {row['teams']}")
        return "\n".join(lines)


def cmd_rounds(args: list[str]) -> str:
    year = int(args[0]) if args else CURRENT_YEAR
    rounds = db.get_available_rounds(year)
    if not rounds:
        return f"No data scraped for {year}. Run: python scraper.py --year {year}"
    return f"Scraped rounds for {year}: {', '.join(str(r) for r in rounds)}"


def cmd_scrape(args: list[str]) -> str:
    import scraper
    year = CURRENT_YEAR
    round_spec = "1-27"

    if args:
        try:
            year = int(args[0])
        except ValueError:
            return "Usage: /scrape [year] [rounds]  e.g. /scrape 2026 1-10"
    if len(args) > 1:
        round_spec = args[1]

    rounds = scraper.parse_rounds(round_spec)
    print(f"\nScraping {year} rounds {rounds[0]}–{rounds[-1]}… (this may take a while)\n")
    scraper.scrape(year, rounds)
    return "Scrape complete."


def cmd_help() -> str:
    return __doc__


# ---------------------------------------------------------------------------
# Main REPL
# ---------------------------------------------------------------------------

def main():
    db.init_db()
    conversation: list[dict] = []

    print(BANNER)

    rounds = db.get_available_rounds(CURRENT_YEAR)
    if not rounds:
        print(
            f"[Tip] No {CURRENT_YEAR} data found. Run /scrape to download player stats.\n"
        )

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            sys.exit(0)

        if not user_input:
            continue

        # ---- Slash commands ----
        if user_input.startswith("/"):
            parts = user_input[1:].split()
            cmd = parts[0].lower() if parts else ""
            args = parts[1:]

            if cmd in ("quit", "exit", "q"):
                print("Goodbye!")
                sys.exit(0)
            elif cmd == "help":
                print(cmd_help())
            elif cmd == "team":
                print(cmd_team())
            elif cmd == "add":
                print(cmd_add(args, conversation))
            elif cmd == "remove":
                print(cmd_remove(args, conversation))
            elif cmd == "captain":
                print(cmd_captain(args, vice=False))
            elif cmd in ("vc", "vice"):
                print(cmd_captain(args, vice=True))
            elif cmd == "byes":
                print(cmd_byes(args))
            elif cmd == "rounds":
                print(cmd_rounds(args))
            elif cmd == "scrape":
                print(cmd_scrape(args))
            else:
                print(f"Unknown command '/{cmd}'. Type /help for a list of commands.")
            print()
            continue

        # ---- AI chat ----
        try:
            print("\nAI Manager: ", end="", flush=True)
            reply, conversation = ai.chat(conversation, user_input)
            print(reply)
            print()
        except Exception as exc:
            print(f"\n[Error] {exc}")
            print("Make sure ANTHROPIC_API_KEY is set in your environment.\n")


if __name__ == "__main__":
    main()
