#!/usr/bin/env python3
import json, re, urllib.request, xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
UA = {"User-Agent": "Rayi-Fallback-Monitor/1.0"}

FPL_BOOTSTRAP = "https://fantasy.premierleague.com/api/bootstrap-static/"
FPL_FIXTURES = "https://fantasy.premierleague.com/api/fixtures/"
FPL_LIVE = "https://fantasy.premierleague.com/api/event/{event}/live/"
RSS = {
    "bbc_sport": "https://feeds.bbci.co.uk/sport/football/rss.xml",
    "sky_sports": "https://www.skysports.com/rss/12040",
}

def fetch_bytes(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def fetch_json(url):
    return json.loads(fetch_bytes(url).decode("utf-8"))

def write_json(name, payload):
    p = DATA / name
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z")

def choose_event(events):
    nxt = next((e for e in events if e.get("is_next")), None)
    cur = next((e for e in events if e.get("is_current")), None)
    return (nxt or cur or (events[0] if events else None))

def textnorm(s):
    return re.sub(r"\s+", " ", (s or "").strip()).lower()

watch = json.loads((DATA / "watchlist.json").read_text(encoding="utf-8"))
wanted = set(int(x) for x in watch.get("player_ids", []))
updated_at = now_iso()
errors = []

bootstrap = fetch_json(FPL_BOOTSTRAP)
event = choose_event(bootstrap.get("events", []))
target_event = int(event["id"]) if event else int(watch.get("target_event_hint", 0) or 0)

teams = {int(t["id"]): t for t in bootstrap.get("teams", [])}
positions = {1:"GK",2:"DEF",3:"MID",4:"FWD"}
players = []
aliases = {}
for e in bootstrap.get("elements", []):
    pid = int(e["id"])
    if pid not in wanted:
        continue
    team = teams.get(int(e.get("team") or 0), {})
    full = " ".join(x for x in [e.get("first_name",""), e.get("second_name","")] if x).strip()
    player = {
        "id": pid,
        "web_name": e.get("web_name"),
        "full_name": full,
        "team_id": e.get("team"),
        "team_name": team.get("name"),
        "position": positions.get(e.get("element_type")),
        "now_cost": e.get("now_cost"),
        "status": e.get("status"),
        "chance_of_playing_next_round": e.get("chance_of_playing_next_round"),
        "news": e.get("news"),
        "news_added": e.get("news_added"),
        "selected_by_percent": e.get("selected_by_percent"),
        "form": e.get("form"),
        "ep_next": e.get("ep_next"),
        "total_points": e.get("total_points"),
        "minutes": e.get("minutes"),
    }
    players.append(player)
    terms = {textnorm(e.get("web_name")), textnorm(full), textnorm(e.get("second_name"))}
    aliases[pid] = [t for t in terms if len(t) >= 4]

fixtures_all = fetch_json(FPL_FIXTURES)
fixtures = []
for f in fixtures_all:
    if int(f.get("event") or 0) != target_event:
        continue
    fixtures.append({
        "id": f.get("id"),
        "event": f.get("event"),
        "kickoff_time": f.get("kickoff_time"),
        "team_h": f.get("team_h"),
        "team_a": f.get("team_a"),
        "team_h_name": teams.get(int(f.get("team_h") or 0), {}).get("name"),
        "team_a_name": teams.get(int(f.get("team_a") or 0), {}).get("name"),
        "finished": f.get("finished"),
        "started": f.get("started"),
        "team_h_score": f.get("team_h_score"),
        "team_a_score": f.get("team_a_score"),
    })

live = {"available": False, "elements": []}
if target_event:
    try:
        raw_live = fetch_json(FPL_LIVE.format(event=target_event))
        live_elements = []
        for row in raw_live.get("elements", []):
            pid = int(row.get("id") or 0)
            if pid in wanted:
                stats = row.get("stats", {})
                live_elements.append({
                    "id": pid,
                    "total_points": stats.get("total_points"),
                    "minutes": stats.get("minutes"),
                    "goals_scored": stats.get("goals_scored"),
                    "assists": stats.get("assists"),
                    "clean_sheets": stats.get("clean_sheets"),
                    "bonus": stats.get("bonus"),
                })
        live = {"available": True, "elements": live_elements}
    except Exception as ex:
        live = {"available": False, "error": type(ex).__name__}

news_items = []
for source, url in RSS.items():
    try:
        root = ET.fromstring(fetch_bytes(url))
        for item in root.findall(".//item")[:120]:
            title = (item.findtext("title") or "").strip()
            desc = (item.findtext("description") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub = (item.findtext("pubDate") or "").strip()
            hay = textnorm(title + " " + re.sub("<[^>]+>", " ", desc))
            matched = []
            for pid, terms in aliases.items():
                if any(phrase_match(hay, term) for term in terms):
                    matched.append(pid)
            if matched:
                news_items.append({
                    "source": source,
                    "title": title,
                    "url": link,
                    "published_at_raw": pub,
                    "matched_player_ids": sorted(set(matched)),
                })
    except Exception as ex:
        errors.append({"source": source, "error": type(ex).__name__})

# Deduplicate news by URL/title and keep newest feed order.
seen = set()
deduped = []
for item in news_items:
    key = item.get("url") or item.get("title")
    if key in seen:
        continue
    seen.add(key)
    deduped.append(item)

fpl_payload = {
    "schema_version": 1,
    "updated_at": updated_at,
    "target_event": target_event,
    "deadline_time": event.get("deadline_time") if event else None,
    "event_name": event.get("name") if event else None,
    "players": sorted(players, key=lambda x: x["id"]),
    "fixtures": fixtures,
    "live": live,
}
news_payload = {
    "schema_version": 1,
    "updated_at": updated_at,
    "target_event": target_event,
    "sources": list(RSS.keys()),
    "items": deduped[:200],
    "errors": errors,
}
status_payload = {
    "service": "Ray.i FPL fallback data",
    "schema_version": 1,
    "state": "OK" if not errors else "DEGRADED",
    "updated_at": updated_at,
    "target_event": target_event,
    "deadline_time": event.get("deadline_time") if event else None,
    "watchlist_count": len(wanted),
    "players_found": len(players),
    "fixture_count": len(fixtures),
    "news_matches": len(deduped),
    "live_available": bool(live.get("available")),
    "errors": errors,
    "note": "Temporary public read-only fallback. No secrets, private model code, or personal identifiers."
}

# Compact history: keep only meaningful changes, not every half-hour snapshot.
history_path = DATA / "history.json"
try:
    history = json.loads(history_path.read_text(encoding="utf-8"))
except Exception:
    history = {"schema_version": 1, "events": []}
events = list(history.get("events", []))

try:
    prev_fpl = json.loads((DATA / "fpl.json").read_text(encoding="utf-8"))
except Exception:
    prev_fpl = {}
prev_players = {int(p["id"]): p for p in prev_fpl.get("players", []) if p.get("id") is not None}

for p in players:
    old = prev_players.get(int(p["id"]))
    if not old:
        continue
    checks = [
        ("status", old.get("status"), p.get("status")),
        ("chance_of_playing_next_round", old.get("chance_of_playing_next_round"), p.get("chance_of_playing_next_round")),
        ("news", old.get("news") or "", p.get("news") or ""),
        ("now_cost", old.get("now_cost"), p.get("now_cost")),
    ]
    changed = {k: {"before": a, "after": b} for k,a,b in checks if a != b}
    if changed:
        events.append({
            "detected_at": updated_at,
            "type": "player_state_change",
            "player_id": p["id"],
            "web_name": p.get("web_name"),
            "source": "official_fpl",
            "changes": changed,
        })

try:
    prev_news = json.loads((DATA / "news.json").read_text(encoding="utf-8"))
except Exception:
    prev_news = {}
prev_keys = {(x.get("url") or x.get("title")) for x in prev_news.get("items", [])}
for item in deduped:
    key = item.get("url") or item.get("title")
    if key and key not in prev_keys:
        events.append({
            "detected_at": updated_at,
            "type": "trusted_news_match",
            "source": item.get("source"),
            "title": item.get("title"),
            "url": item.get("url"),
            "published_at_raw": item.get("published_at_raw"),
            "matched_player_ids": item.get("matched_player_ids", []),
        })

history = {
    "schema_version": 1,
    "updated_at": updated_at,
    "events": events[-500:],
}

write_json("fpl.json", fpl_payload)
write_json("news.json", news_payload)
write_json("status.json", status_payload)
write_json("history.json", history)
print(json.dumps(status_payload, ensure_ascii=False))
