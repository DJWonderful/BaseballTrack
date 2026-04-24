"""Collect roster transactions (rehab assignments, options, callups) from MLB Stats API."""

import json
import os
import re
import time

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.db.connection import get_session
from src.utils.logger import get_logger

logger = get_logger("collectors.transactions")

MLB_API = os.getenv("MLB_API_BASE", "https://statsapi.mlb.com/api/v1")

# Transaction types that can affect MiLB attendance
RELEVANT_TYPE_CODES = "ASG,OPT,CU,DES,OUT,SE"

# Regex to extract position abbreviation from description text
# e.g., "sent LHP Marco Gonzales on a rehab" -> "LHP"
POSITION_PATTERN = re.compile(
    r'\b(LHP|RHP|C|1B|2B|3B|SS|LF|CF|RF|OF|DH|IF|P|SP|RP|INF|UT)\b'
)


def _extract_position(description: str | None) -> str | None:
    """Extract player position from transaction description text."""
    if not description:
        return None
    match = POSITION_PATTERN.search(description)
    return match.group(1) if match else None


def _is_rehab(description: str | None) -> bool:
    """Check if a transaction description indicates a rehab assignment."""
    if not description:
        return False
    return "rehab" in description.lower()


def _enrich_player(client: httpx.Client, player_id: int) -> dict:
    """Fetch player details to determine MLB veteran status."""
    try:
        url = f"{MLB_API}/people/{player_id}"
        resp = client.get(url, timeout=15)
        if resp.status_code != 200:
            return {}
        data = resp.json()
        people = data.get("people", [])
        if not people:
            return {}
        person = people[0]
        return {
            "mlb_debut_date": person.get("mlbDebutDate"),
            "is_mlb_veteran": person.get("mlbDebutDate") is not None,
        }
    except Exception:
        return {}


def collect_transactions(session: Session, seasons: list[int] | None = None,
                         team_id: int | None = None,
                         enrich_players: bool = True,
                         force: bool = False) -> int:
    """Collect roster transactions for all MiLB parent organizations.

    We query by MLB parent org (30 teams) rather than by 120 MiLB teams,
    because the transactions endpoint uses MLB team IDs and transactions
    flow between MLB and their MiLB affiliates.

    Args:
        session: SQLAlchemy session
        seasons: Years to collect (default from env)
        team_id: Optional — filter to a single MLB parent org ID
        enrich_players: Whether to call /people endpoint for MLB debut info

    Returns:
        Total transactions upserted.
    """
    from src.collectors.delta import active_seasons, parse_seasons_env

    if seasons is None:
        seasons = parse_seasons_env()
    seasons, skipped = active_seasons(seasons, force=force)
    if skipped:
        logger.info(f"Transactions: skipping completed seasons {skipped}")
    if not seasons:
        logger.info("Transactions: no active seasons to collect")
        return 0

    # Get all MLB parent org IDs from our teams table
    if team_id:
        org_ids = [team_id]
    else:
        result = session.execute(text(
            "SELECT DISTINCT org_id FROM milb.teams WHERE org_id IS NOT NULL ORDER BY org_id"
        ))
        org_ids = [row[0] for row in result]

    if not org_ids:
        logger.warning("No organizations found in database")
        return 0

    total_upserted = 0
    # Cache player enrichment to avoid duplicate API calls
    player_cache: dict[int, dict] = {}

    with httpx.Client() as client:
        for season in seasons:
            # MiLB season roughly April through September
            start_date = f"04/01/{season}"
            end_date = f"10/01/{season}"

            logger.info(f"Collecting transactions for {season} ({len(org_ids)} orgs)")

            for org_id in org_ids:
                try:
                    url = f"{MLB_API}/transactions"
                    params = {
                        "teamId": org_id,
                        "startDate": start_date,
                        "endDate": end_date,
                        "typeCode": RELEVANT_TYPE_CODES,
                    }

                    resp = client.get(url, params=params, timeout=30)
                    if resp.status_code != 200:
                        continue

                    data = resp.json()
                    transactions = data.get("transactions", [])

                    for txn in transactions:
                        player = txn.get("person", {})
                        player_id = player.get("id")
                        if not player_id:
                            continue

                        from_team = txn.get("fromTeam", {})
                        to_team = txn.get("toTeam", {})
                        description = txn.get("description", "")
                        type_code = txn.get("typeCode", "")

                        # Enrich player with MLB debut info (cached)
                        player_info = {}
                        if enrich_players and player_id not in player_cache:
                            player_info = _enrich_player(client, player_id)
                            player_cache[player_id] = player_info
                            time.sleep(0.1)  # light rate limiting for people endpoint
                        elif player_id in player_cache:
                            player_info = player_cache[player_id]

                        session.execute(text("""
                            INSERT INTO milb.transactions (
                                mlb_transaction_id, transaction_date, effective_date,
                                resolution_date, player_id, player_name, player_position,
                                mlb_debut_date, is_mlb_veteran,
                                from_team_id, from_team_name, to_team_id, to_team_name,
                                type_code, type_desc, is_rehab, description,
                                raw_json, created_at
                            ) VALUES (
                                :txn_id, :txn_date, :eff_date,
                                :res_date, :player_id, :player_name, :position,
                                :debut_date, :is_veteran,
                                :from_id, :from_name, :to_id, :to_name,
                                :type_code, :type_desc, :is_rehab, :description,
                                CAST(:raw_json AS jsonb), NOW()
                            )
                            ON CONFLICT (mlb_transaction_id, player_id, transaction_date, type_code)
                            DO UPDATE SET
                                description = EXCLUDED.description,
                                mlb_debut_date = EXCLUDED.mlb_debut_date,
                                is_mlb_veteran = EXCLUDED.is_mlb_veteran,
                                raw_json = EXCLUDED.raw_json,
                                updated_at = NOW()
                        """), {
                            "txn_id": txn.get("id"),
                            "txn_date": txn.get("date"),
                            "eff_date": txn.get("effectiveDate"),
                            "res_date": txn.get("resolutionDate"),
                            "player_id": player_id,
                            "player_name": player.get("fullName", "Unknown"),
                            "position": _extract_position(description),
                            "debut_date": player_info.get("mlb_debut_date"),
                            "is_veteran": player_info.get("is_mlb_veteran", False),
                            "from_id": from_team.get("id"),
                            "from_name": from_team.get("name"),
                            "to_id": to_team.get("id"),
                            "to_name": to_team.get("name"),
                            "type_code": type_code,
                            "type_desc": txn.get("typeDesc"),
                            "is_rehab": _is_rehab(description),
                            "description": description,
                            "raw_json": json.dumps(txn),
                        })
                        total_upserted += 1

                except Exception as e:
                    logger.warning(f"Failed transactions for org {org_id}, {season}: {e}")
                    session.rollback()
                    continue

                time.sleep(0.3)

            session.commit()
            logger.info(f"Season {season} transactions complete: {total_upserted} total so far")

    logger.info(f"Transactions collection complete: {total_upserted} total")
    return total_upserted


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    session = get_session()
    try:
        collect_transactions(session)
    finally:
        session.close()
