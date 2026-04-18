"""Check O'Reilly's position in predictions after fix."""
import json
data = json.load(open('output/gw33_predictions.json', 'r', encoding='utf-8'))

# Find O'Reilly
for i, p in enumerate(data['predictions']):
    if 'reill' in p.get('name', '').lower():
        print(f"O'Reilly ranking: #{i+1} out of {len(data['predictions'])}")
        print(f"  predicted_points (risk-adjusted): {p['predicted_points']}")
        print(f"  raw_xpts (if fully fit): {p.get('raw_xpts', 'N/A')}")
        print(f"  availability: {p['availability']}")
        print(f"  news: {p.get('news', '')}")
        print(f"  starter_quality: {p['starter_quality']['tier']}")
        print(f"  is_dgw: {p.get('is_dgw')}")
        print()

# Check squad
squad_names = [p['name'] for p in data['squad']['squad']]
xi_names = [p['name'] for p in data['squad']['starting_xi']]
print(f"In squad? {'Yes' if any('reill' in n.lower() for n in squad_names) else 'No'}")
print(f"In starting XI? {'Yes' if any('reill' in n.lower() for n in xi_names) else 'No'}")
print()

# Show all flagged players in predictions
print("=== All flagged players in predictions ===")
flagged = [p for p in data['predictions'] if p.get('availability', {}).get('status') == 'doubtful']
for p in sorted(flagged, key=lambda x: x['predicted_points'], reverse=True):
    raw = p.get('raw_xpts', p['predicted_points'])
    adj = p['predicted_points']
    chance = p['availability'].get('chance', '?')
    discount = round((1 - adj/raw) * 100, 1) if raw > 0 else 0
    print(f"  {p['name']:20s} | raw={raw:5.1f} | adj={adj:5.1f} | -{discount}% | chance={chance}% | {p.get('news','')[:50]}")
