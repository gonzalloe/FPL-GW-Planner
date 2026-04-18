"""Quick debug script to check player availability data."""
from data_fetcher import fetch_bootstrap, build_player_map

players = build_player_map()

# Find O'Reilly and similar flagged players
print("=== Searching for O'Reilly ===")
for pid, p in players.items():
    name = p.get('web_name', '').lower()
    sname = p.get('second_name', '').lower()
    if 'reill' in name or 'reill' in sname:
        print(f"ID={p['id']} | {p['web_name']} ({p['team_short']})")
        print(f"  status={p.get('status')} | chance_next={p.get('chance_of_playing_next_round')}")
        print(f"  chance_this={p.get('chance_of_playing_this_round')} | news='{p.get('news','')}'")
        print(f"  form={p.get('form')} | mins={p.get('minutes')} | starts={p.get('starts')}")
        print(f"  ppg={p.get('points_per_game')} | total_pts={p.get('total_points')}")
        print()

print("\n=== All flagged players (status != 'a') in top 100 by predicted points ===")
# Show status distribution
from collections import Counter
statuses = Counter()
flagged = []
for pid, p in players.items():
    s = p.get('status', 'a')
    statuses[s] += 1
    if s != 'a':
        flagged.append(p)

print(f"Status distribution: {dict(statuses)}")
print(f"Total flagged: {len(flagged)}")
print()

# Show players with yellow flag (doubtful/chance < 100)
print("=== Yellow-flagged (status='d' or chance < 100) ===")
for p in sorted(flagged, key=lambda x: float(x.get('form', 0)), reverse=True)[:20]:
    chance = p.get('chance_of_playing_next_round')
    print(f"  {p['web_name']:20s} ({p['team_short']}) | status={p['status']} | chance={chance} | news='{p.get('news','')[:60]}' | form={p.get('form')}")
