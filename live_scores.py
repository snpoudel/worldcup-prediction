"""Fetch real World Cup 2026 knockout results from the free, open
openfootball/worldcup.json feed and sync them into our local bracket.

Source: https://github.com/openfootball/worldcup.json
Raw data: https://raw.githubusercontent.com/openfootball/worldcup.json/master/2026/worldcup.json
No API key required. Data is community-maintained, updated close to
real-time but by hand -- so don't expect second-by-second live scores.
"""
import requests

FEED_URL = "https://raw.githubusercontent.com/openfootball/worldcup.json/master/2026/worldcup.json"

# Map the feed's "round" string + match "num" to our internal (round, slot).
# Knockout match numbering in the feed is fixed: 73-88 = R32, 89-96 = R16,
# 97-100 = QF, 101-102 = SF, 104 = Final. (103 is third-place, which we don't
# track since this app is knockout-bracket-only, no 3rd place game.)
FEED_ROUND_TO_OURS = {
    "Round of 32": "R32",
    "Round of 16": "R16",
    "Quarter-final": "QF",
    "Semi-final": "SF",
    "Final": "F",
}

NUM_RANGES = {
    "R32": (73, 88),
    "R16": (89, 96),
    "QF": (97, 100),
    "SF": (101, 102),
    "F": (104, 104),
}


def fetch_feed():
    """Fetch and return the raw feed JSON. Raises on network/HTTP errors --
    caller should catch and show a friendly message rather than crashing."""
    resp = requests.get(FEED_URL, timeout=10)
    resp.raise_for_status()
    return resp.json()


def extract_knockout_results(feed_json):
    """Return a list of dicts: {round, slot, team1, team2, home, away}
    for every knockout match that has a final score in the feed.
    `slot` is our 0-indexed position within the round.
    Returns [] if no knockout matches have results yet (e.g. still in groups)."""
    results = []
    for m in feed_json.get("matches", []):
        our_round = FEED_ROUND_TO_OURS.get(m.get("round"))
        if not our_round:
            continue
        num = m.get("num")
        if num is None:
            continue
        lo, hi = NUM_RANGES[our_round]
        if not (lo <= num <= hi):
            continue
        score = m.get("score", {}).get("ft")
        if not score:
            continue  # not played yet
        slot = num - lo
        results.append({
            "round": our_round,
            "slot": slot,
            "team1": m.get("team1"),
            "team2": m.get("team2"),
            "home": score[0],
            "away": score[1],
        })
    return results


def sync_results(group_id, db_module):
    """Pull live results and apply them to our bracket for the given group.
    Returns (num_updated, num_skipped_draws, error_message_or_None).

    - Updates team names from the feed (since our local R16+ start as TBD
      until earlier rounds resolve -- feed already knows real team names
      once their group stage / previous knockout round is final).
    - Applies scores via db.set_result(), which also auto-advances winners.
    - Draws are skipped automatically (flagged for manual penalty-winner
      entry in the Admin tab), since the feed doesn't reliably carry
      penalty-shootout outcomes.
    """
    try:
        feed = fetch_feed()
    except Exception as e:
        return 0, 0, f"Could not fetch live scores: {e}"

    knockout_results = extract_knockout_results(feed)
    if not knockout_results:
        return 0, 0, "No knockout results available yet in the feed."

    existing = {(m["round"], m["slot"]): m for m in db_module.get_matches(group_id)}
    updated = 0
    skipped_draws = 0

    for r in knockout_results:
        key = (r["round"], r["slot"])
        local_match = existing.get(key)
        if not local_match:
            continue
        # Already has this exact result recorded -- skip re-applying.
        if (local_match["actual_home"] == r["home"]
                and local_match["actual_away"] == r["away"]):
            continue

        # Sync team names if the feed has real names and ours are still TBD
        # or placeholders.
        if r["team1"] and r["team2"]:
            db_module.update_team_names(local_match["id"], r["team1"], r["team2"])

        if r["home"] == r["away"]:
            # Draw decided on penalties -- feed's ft score alone can't tell us
            # who advances. Record the scoreline for prediction-scoring
            # purposes, but don't auto-advance; flag for manual handling.
            db_module.set_result(local_match["id"], r["home"], r["away"])
            skipped_draws += 1
        else:
            db_module.set_result(local_match["id"], r["home"], r["away"])
            updated += 1

    return updated, skipped_draws, None
