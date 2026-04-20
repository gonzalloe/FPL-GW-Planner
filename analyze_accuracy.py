"""Analyze prediction accuracy vs actual points"""
import json
from data_fetcher import fetch_bootstrap, get_current_gameweek

def analyze_predictions():
    # Load predictions
    try:
        with open('output/gw33_predictions.json', 'r') as f:
            gw33 = json.load(f)
    except:
        gw33 = None
    
    try:
        with open('output/gw34_predictions.json', 'r') as f:
            gw34 = json.load(f)
    except:
        gw34 = None
    
    # Get live data
    bootstrap = fetch_bootstrap()
    current_gw = get_current_gameweek()
    
    print(f"Current GW: {current_gw}")
    print(f"Total players in bootstrap: {len(bootstrap['elements'])}")
    
    # Analyze GW33 if available
    if gw33:
        print(f"\n{'='*60}")
        print(f"GW33 Analysis")
        print(f"{'='*60}")
        
        # Get actual points from bootstrap
        player_map = {p['id']: p for p in bootstrap['elements']}
        
        top_predicted = sorted(gw33['players'], key=lambda x: x['xPts'], reverse=True)[:20]
        
        print(f"\nTop 20 Predicted Players:")
        print(f"{'Name':<25} {'Team':<5} {'xPts':>6} {'Actual':>7} {'Diff':>7}")
        print("-" * 60)
        
        errors = []
        for p in top_predicted:
            player_id = p.get('id')
            actual_player = player_map.get(player_id)
            
            if actual_player:
                # Get GW33 points (assuming it's in history)
                actual_pts = 0
                if 'history' in actual_player and len(actual_player['history']) > 0:
                    # Find GW33 in history
                    for h in actual_player['history']:
                        if h['round'] == 33:
                            actual_pts = h['total_points']
                            break
                
                xpts = p['xPts']
                diff = actual_pts - xpts
                errors.append(abs(diff))
                
                print(f"{p['name']:<25} {p['team']:<5} {xpts:>6.1f} {actual_pts:>7} {diff:>+7.1f}")
        
        if errors:
            mae = sum(errors) / len(errors)
            print(f"\nMean Absolute Error: {mae:.2f}")
    
    # Show recent actual points
    print(f"\n{'='*60}")
    print(f"Recent High Scorers (Last GW)")
    print(f"{'='*60}")
    
    recent_top = sorted(
        bootstrap['elements'],
        key=lambda x: x.get('event_points', 0),
        reverse=True
    )[:20]
    
    print(f"\n{'Name':<25} {'Team':<5} {'Pos':<4} {'Points':>7}")
    print("-" * 50)
    for p in recent_top:
        team_name = next((t['short_name'] for t in bootstrap['teams'] if t['id'] == p['team']), '?')
        pos_map = {1: 'GKP', 2: 'DEF', 3: 'MID', 4: 'FWD'}
        pos = pos_map.get(p['element_type'], '?')
        pts = p.get('event_points', 0)
        print(f"{p['web_name']:<25} {team_name:<5} {pos:<4} {pts:>7}")

if __name__ == '__main__':
    analyze_predictions()
