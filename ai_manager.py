"""
ai_manager.py – Claude-powered NRL SuperCoach manager.

The AI has a set of tools to query the local SQLite database for player
stats and make decisions around team selection, trades, captaincy, and
bye management.
"""

import json
from datetime import date

import anthropic
import database as db
import bye_schedule as bye_sched

# ---------------------------------------------------------------------------
# Current season context
# ---------------------------------------------------------------------------
CURRENT_YEAR = date.today().year

SYSTEM_PROMPT = f"""You are an expert NRL SuperCoach team manager assistant for the {CURRENT_YEAR} season. \
Your job is to help the user manage their SuperCoach team by providing data-driven advice.

You have access to tools that query a live database of NRL SuperCoach player statistics \
scraped from the official SuperCoach API.

When giving advice:
- ALWAYS check bye rounds before recommending a captain or team selection.
  A player on a bye scores 0 – never captain or field someone on a bye.
- For captain picks, prioritise high-average players who do NOT have a bye.
- For trade advice, consider the player's break-even (be) – if a player's \
  break-even is much higher than their average, their price will drop, making \
  them a sell candidate.
- For trade targets, look for players with rising form, low break-evens, \
  and favourable upcoming fixtures (no byes in the next 2 rounds).
- When listing players, always include their position, price, average score, \
  and last round score.
- Be concise but thorough. Use bullet points for lists.
- If the database is empty (not yet scraped), tell the user to run: \
  python scraper.py --year {CURRENT_YEAR}

Today's date: {date.today().strftime("%d %B %Y")}
Current season: {CURRENT_YEAR}
"""

# ---------------------------------------------------------------------------
# Tool definitions (sent to Claude)
# ---------------------------------------------------------------------------
TOOLS = [
    {
        "name": "search_players",
        "description": (
            "Search the database for NRL SuperCoach players. "
            "Filter by name, NRL club, or position. Returns price, average score, last round score, and positions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Partial or full player name to search for",
                },
                "team": {
                    "type": "string",
                    "description": "NRL club name (e.g. 'Broncos', 'Storm', 'Panthers')",
                },
                "position": {
                    "type": "string",
                    "description": "Position code or name (e.g. 'HLF', 'HOK', 'FRF', '2RF', 'CTR', 'WFB', '5/8')",
                },
                "year": {
                    "type": "integer",
                    "description": f"Season year (default {CURRENT_YEAR})",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results (default 15)",
                },
            },
        },
    },
    {
        "name": "get_player_stats",
        "description": (
            "Get round-by-round stats for a specific player. "
            "Returns score, break-even, and price for each scraped round."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "player_id": {
                    "type": "integer",
                    "description": "The player's unique SuperCoach ID",
                },
                "year": {
                    "type": "integer",
                    "description": f"Season year (default {CURRENT_YEAR})",
                },
                "round": {
                    "type": "integer",
                    "description": "Specific round number (omit for all rounds)",
                },
            },
            "required": ["player_id"],
        },
    },
    {
        "name": "get_top_scorers",
        "description": (
            "Get the highest-scoring players for a specific round, "
            "optionally filtered by position."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "position": {
                    "type": "string",
                    "description": "Filter by position (e.g. 'HLF', 'HOK'). Omit for all positions.",
                },
                "round": {
                    "type": "integer",
                    "description": "Round number. Omit for the latest scraped round.",
                },
                "year": {
                    "type": "integer",
                    "description": f"Season year (default {CURRENT_YEAR})",
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of results to return (default 10)",
                },
            },
        },
    },
    {
        "name": "get_bye_info",
        "description": "Find out which NRL teams have a bye in a given round.",
        "input_schema": {
            "type": "object",
            "properties": {
                "round": {
                    "type": "integer",
                    "description": "Round number to check",
                },
                "year": {
                    "type": "integer",
                    "description": f"Season year (default {CURRENT_YEAR})",
                },
            },
            "required": ["round"],
        },
    },
    {
        "name": "get_my_team",
        "description": (
            "Retrieve the user's current SuperCoach team roster, including "
            "each player's price, positions, average score, last round score, "
            "and whether they are captain or vice-captain."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "year": {
                    "type": "integer",
                    "description": f"Season year (default {CURRENT_YEAR})",
                },
            },
        },
    },
    {
        "name": "update_my_team",
        "description": (
            "Add or remove a player from the user's team, or assign the captain / vice-captain armband."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "remove", "set_captain", "set_vice_captain"],
                    "description": "Action to perform",
                },
                "player_id": {
                    "type": "integer",
                    "description": "The player's SuperCoach ID",
                },
            },
            "required": ["action", "player_id"],
        },
    },
    {
        "name": "check_bye_impact",
        "description": (
            "Check which players in my current team have a bye in a specific round. "
            "Essential before setting a captain or selecting a team."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "round": {
                    "type": "integer",
                    "description": "Round number to check",
                },
                "year": {
                    "type": "integer",
                    "description": f"Season year (default {CURRENT_YEAR})",
                },
            },
            "required": ["round"],
        },
    },
    {
        "name": "get_available_rounds",
        "description": "List the round numbers that have been scraped and are available in the database.",
        "input_schema": {
            "type": "object",
            "properties": {
                "year": {
                    "type": "integer",
                    "description": f"Season year (default {CURRENT_YEAR})",
                },
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _search_players(inputs: dict) -> str:
    results = db.search_players(
        name=inputs.get("name"),
        team=inputs.get("team"),
        position=inputs.get("position"),
        year=inputs.get("year", CURRENT_YEAR),
        limit=inputs.get("limit", 15),
    )
    if not results:
        return "No players found matching those criteria."
    return json.dumps(results, indent=2)


def _get_player_stats(inputs: dict) -> str:
    results = db.get_player_stats(
        player_id=inputs["player_id"],
        year=inputs.get("year", CURRENT_YEAR),
        round_num=inputs.get("round"),
    )
    if not results:
        return "No stats found for this player. Make sure the data has been scraped."
    return json.dumps(results, indent=2)


def _get_top_scorers(inputs: dict) -> str:
    results = db.get_top_scorers(
        position=inputs.get("position"),
        round_num=inputs.get("round"),
        year=inputs.get("year", CURRENT_YEAR),
        limit=inputs.get("limit", 10),
    )
    if not results:
        return "No scoring data found. Scrape some rounds first."
    return json.dumps(results, indent=2)


def _get_bye_info(inputs: dict) -> str:
    year = inputs.get("year", CURRENT_YEAR)
    rnd = inputs["round"]
    teams = bye_sched.get_teams_on_bye(year, rnd)
    if not teams:
        return (
            f"No bye data stored for {year} round {rnd}. "
            "Update bye_schedule.py and run: python bye_schedule.py --load"
        )
    return json.dumps({"year": year, "round": rnd, "teams_on_bye": teams}, indent=2)


def _get_my_team(inputs: dict) -> str:
    year = inputs.get("year", CURRENT_YEAR)
    team = db.get_my_team(year)
    if not team:
        return "Your team is empty. Add players with: /add <player name>"
    return json.dumps(team, indent=2)


def _update_my_team(inputs: dict) -> str:
    action = inputs["action"]
    pid = inputs["player_id"]

    if action == "add":
        db.add_to_my_team(pid)
        return f"Player {pid} added to your team."
    elif action == "remove":
        db.remove_from_my_team(pid)
        return f"Player {pid} removed from your team."
    elif action == "set_captain":
        db.set_captain(pid, vice=False)
        return f"Player {pid} set as captain."
    elif action == "set_vice_captain":
        db.set_captain(pid, vice=True)
        return f"Player {pid} set as vice-captain."
    return "Unknown action."


def _check_bye_impact(inputs: dict) -> str:
    year = inputs.get("year", CURRENT_YEAR)
    rnd = inputs["round"]
    team = db.get_my_team(year)
    if not team:
        return "Your team is empty."

    on_bye = []
    available = []
    for p in team:
        club = p.get("nrl_club", "")
        if bye_sched.is_team_on_bye(year, rnd, club):
            on_bye.append(p)
        else:
            available.append(p)

    return json.dumps(
        {
            "round": rnd,
            "players_on_bye": on_bye,
            "players_available": available,
        },
        indent=2,
    )


def _get_available_rounds(inputs: dict) -> str:
    year = inputs.get("year", CURRENT_YEAR)
    rounds = db.get_available_rounds(year)
    if not rounds:
        return f"No data scraped for {year}. Run: python scraper.py --year {year}"
    return json.dumps({"year": year, "available_rounds": rounds})


# ---------------------------------------------------------------------------
# Tool dispatcher
# ---------------------------------------------------------------------------
TOOL_MAP = {
    "search_players": _search_players,
    "get_player_stats": _get_player_stats,
    "get_top_scorers": _get_top_scorers,
    "get_bye_info": _get_bye_info,
    "get_my_team": _get_my_team,
    "update_my_team": _update_my_team,
    "check_bye_impact": _check_bye_impact,
    "get_available_rounds": _get_available_rounds,
}


def run_tool(name: str, inputs: dict) -> str:
    func = TOOL_MAP.get(name)
    if not func:
        return f"Unknown tool: {name}"
    try:
        return func(inputs)
    except Exception as exc:
        return f"Tool error ({name}): {exc}"


# ---------------------------------------------------------------------------
# Agentic conversation loop
# ---------------------------------------------------------------------------

def chat(conversation: list[dict], user_message: str) -> tuple[str, list[dict]]:
    """
    Send a user message, run the tool loop, and return the final reply
    plus the updated conversation history.
    """
    client = anthropic.Anthropic()

    conversation.append({"role": "user", "content": user_message})

    while True:
        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=conversation,
        )

        # Collect all content blocks
        assistant_content = response.content
        conversation.append({"role": "assistant", "content": assistant_content})

        # If no tool calls, we're done
        if response.stop_reason != "tool_use":
            # Extract text reply
            text = " ".join(
                block.text for block in assistant_content if hasattr(block, "text")
            )
            return text, conversation

        # Process tool calls
        tool_results = []
        for block in assistant_content:
            if block.type == "tool_use":
                result = run_tool(block.name, block.input)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    }
                )

        # Feed tool results back into the conversation
        conversation.append({"role": "user", "content": tool_results})
