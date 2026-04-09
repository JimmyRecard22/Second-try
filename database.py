import sqlite3
import json
from pathlib import Path

DB_PATH = Path(__file__).parent / "supercoach.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS players (
            id          INTEGER PRIMARY KEY,
            first_name  TEXT NOT NULL,
            last_name   TEXT NOT NULL,
            full_name   TEXT NOT NULL,
            nrl_club    TEXT,
            price       INTEGER DEFAULT 0,
            raw_json    TEXT
        );

        CREATE TABLE IF NOT EXISTS player_stats (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id   INTEGER NOT NULL,
            year        INTEGER NOT NULL,
            round       INTEGER NOT NULL,
            score       INTEGER,
            be          INTEGER,
            price       INTEGER,
            raw_json    TEXT,
            UNIQUE(player_id, year, round)
        );

        CREATE TABLE IF NOT EXISTS player_positions (
            player_id       INTEGER NOT NULL,
            position_id     INTEGER NOT NULL,
            position_name   TEXT,
            PRIMARY KEY (player_id, position_id)
        );

        CREATE TABLE IF NOT EXISTS bye_schedule (
            year    INTEGER NOT NULL,
            round   INTEGER NOT NULL,
            team    TEXT NOT NULL,
            PRIMARY KEY (year, round, team)
        );

        CREATE TABLE IF NOT EXISTS my_team (
            player_id       INTEGER PRIMARY KEY,
            is_captain      INTEGER DEFAULT 0,
            is_vice_captain INTEGER DEFAULT 0,
            added_at        TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Player helpers
# ---------------------------------------------------------------------------

def upsert_player(conn: sqlite3.Connection, player: dict):
    conn.execute(
        """
        INSERT INTO players (id, first_name, last_name, full_name, nrl_club, price, raw_json)
        VALUES (:id, :first_name, :last_name, :full_name, :nrl_club, :price, :raw_json)
        ON CONFLICT(id) DO UPDATE SET
            nrl_club = excluded.nrl_club,
            price    = excluded.price,
            raw_json = excluded.raw_json
        """,
        player,
    )


def upsert_stat(conn: sqlite3.Connection, stat: dict):
    conn.execute(
        """
        INSERT OR REPLACE INTO player_stats
            (player_id, year, round, score, be, price, raw_json)
        VALUES (:player_id, :year, :round, :score, :be, :price, :raw_json)
        """,
        stat,
    )


def upsert_position(conn: sqlite3.Connection, pos: dict):
    conn.execute(
        """
        INSERT OR IGNORE INTO player_positions (player_id, position_id, position_name)
        VALUES (:player_id, :position_id, :position_name)
        """,
        pos,
    )


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def search_players(
    name: str | None = None,
    team: str | None = None,
    position: str | None = None,
    year: int = 2026,
    limit: int = 15,
) -> list[dict]:
    conn = get_connection()
    query = """
        SELECT
            p.id,
            p.full_name,
            p.nrl_club,
            p.price,
            GROUP_CONCAT(DISTINCT pp.position_name) AS positions,
            ROUND(AVG(ps.score), 1)                 AS avg_score,
            MAX(ps.round)                            AS latest_round,
            MAX(CASE WHEN ps.round = (
                SELECT MAX(round) FROM player_stats WHERE year = :year
            ) THEN ps.score END)                     AS last_score,
            MAX(CASE WHEN ps.round = (
                SELECT MAX(round) FROM player_stats WHERE year = :year
            ) THEN ps.be END)                        AS last_be
        FROM players p
        LEFT JOIN player_positions pp ON p.id = pp.player_id
        LEFT JOIN player_stats ps     ON p.id = ps.player_id AND ps.year = :year
        WHERE 1=1
          AND (:name     IS NULL OR p.full_name  LIKE :name_like)
          AND (:team     IS NULL OR p.nrl_club   LIKE :team_like)
          AND (:position IS NULL OR pp.position_name LIKE :pos_like)
        GROUP BY p.id
        ORDER BY avg_score DESC NULLS LAST
        LIMIT :limit
    """
    rows = conn.execute(
        query,
        {
            "year": year,
            "name": name,
            "name_like": f"%{name}%" if name else None,
            "team": team,
            "team_like": f"%{team}%" if team else None,
            "position": position,
            "pos_like": f"%{position}%" if position else None,
            "limit": limit,
        },
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_player_stats(player_id: int, year: int = 2026, round_num: int | None = None) -> list[dict]:
    conn = get_connection()
    query = """
        SELECT ps.year, ps.round, ps.score, ps.be, ps.price,
               p.full_name, p.nrl_club
        FROM player_stats ps
        JOIN players p ON ps.player_id = p.id
        WHERE ps.player_id = :player_id AND ps.year = :year
          AND (:round IS NULL OR ps.round = :round)
        ORDER BY ps.round
    """
    rows = conn.execute(query, {"player_id": player_id, "year": year, "round": round_num}).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_top_scorers(
    position: str | None = None,
    round_num: int | None = None,
    year: int = 2026,
    limit: int = 10,
) -> list[dict]:
    conn = get_connection()
    latest_round = round_num
    if latest_round is None:
        row = conn.execute(
            "SELECT MAX(round) as r FROM player_stats WHERE year = ?", (year,)
        ).fetchone()
        latest_round = row["r"] if row and row["r"] else 1

    query = """
        SELECT
            p.id,
            p.full_name,
            p.nrl_club,
            p.price,
            GROUP_CONCAT(DISTINCT pp.position_name) AS positions,
            ps.score                                 AS round_score,
            ROUND(AVG(ps2.score), 1)                 AS avg_score,
            ps.be                                    AS break_even
        FROM players p
        JOIN player_stats ps      ON p.id = ps.player_id AND ps.year = :year AND ps.round = :round
        JOIN player_positions pp  ON p.id = pp.player_id
        LEFT JOIN player_stats ps2 ON p.id = ps2.player_id AND ps2.year = :year
        WHERE (:position IS NULL OR pp.position_name LIKE :pos_like)
        GROUP BY p.id
        ORDER BY ps.score DESC NULLS LAST
        LIMIT :limit
    """
    rows = conn.execute(
        query,
        {
            "year": year,
            "round": latest_round,
            "position": position,
            "pos_like": f"%{position}%" if position else None,
            "limit": limit,
        },
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Bye schedule helpers
# ---------------------------------------------------------------------------

def store_bye(year: int, round_num: int, team: str):
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO bye_schedule (year, round, team) VALUES (?, ?, ?)",
        (year, round_num, team),
    )
    conn.commit()
    conn.close()


def get_teams_on_bye(year: int, round_num: int) -> list[str]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT team FROM bye_schedule WHERE year = ? AND round = ?",
        (year, round_num),
    ).fetchall()
    conn.close()
    return [r["team"] for r in rows]


# ---------------------------------------------------------------------------
# My team helpers
# ---------------------------------------------------------------------------

def get_my_team(year: int = 2026) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT
            mt.player_id,
            mt.is_captain,
            mt.is_vice_captain,
            mt.added_at,
            p.full_name,
            p.nrl_club,
            p.price,
            GROUP_CONCAT(DISTINCT pp.position_name) AS positions,
            ROUND(AVG(ps.score), 1)                  AS avg_score,
            MAX(CASE WHEN ps.round = (
                SELECT MAX(round) FROM player_stats WHERE year = :year
            ) THEN ps.score END)                      AS last_score
        FROM my_team mt
        JOIN players p          ON mt.player_id = p.id
        LEFT JOIN player_positions pp ON p.id = pp.player_id
        LEFT JOIN player_stats ps     ON p.id = ps.player_id AND ps.year = :year
        GROUP BY mt.player_id
        ORDER BY mt.is_captain DESC, mt.is_vice_captain DESC, avg_score DESC
        """,
        {"year": year},
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_to_my_team(player_id: int):
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO my_team (player_id) VALUES (?)", (player_id,)
    )
    conn.commit()
    conn.close()


def remove_from_my_team(player_id: int):
    conn = get_connection()
    conn.execute("DELETE FROM my_team WHERE player_id = ?", (player_id,))
    conn.commit()
    conn.close()


def set_captain(player_id: int, vice: bool = False):
    conn = get_connection()
    col = "is_vice_captain" if vice else "is_captain"
    other = "is_captain" if vice else "is_vice_captain"
    conn.execute(f"UPDATE my_team SET {col} = 0")
    conn.execute(f"UPDATE my_team SET {col} = 1, {other} = 0 WHERE player_id = ?", (player_id,))
    conn.commit()
    conn.close()


def get_available_rounds(year: int) -> list[int]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT DISTINCT round FROM player_stats WHERE year = ? ORDER BY round",
        (year,),
    ).fetchall()
    conn.close()
    return [r["round"] for r in rows]
