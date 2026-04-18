import sys,io,time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
t0 = time.time()
from prediction_engine import PredictionEngine
from squad_optimizer import SquadOptimizer, ChipAdvisor
e = PredictionEngine()
gw_info = e.get_gw_info()
p = e.predict_all()
o = SquadOptimizer(p)
sq = o.optimize_squad()
ca = ChipAdvisor(p, gw_info)
chips = ca.analyze()
t1 = time.time()
print(f"Total: {t1-t0:.1f}s, predictions: {len(p)}, squad: {len(sq.get('squad', []))}")
