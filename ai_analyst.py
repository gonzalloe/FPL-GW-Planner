"""
FPL Predictor - AI Analyst
Generates natural-language analysis and recommendations using AI.
Can integrate with any LLM API (OpenAI, Claude, local models).
"""
import json


class AIAnalyst:
    """
    Generates analysis prompts and processes AI responses.
    Works standalone (generates structured prompts) or with an LLM API.
    """

    @staticmethod
    def generate_analysis_prompt(predictions: list[dict],
                                  squad: dict,
                                  gameweek: int) -> str:
        """
        Build a comprehensive analysis prompt for an AI model.
        You can feed this to ChatGPT, Claude, or any LLM.
        """
        captain = squad.get("captain", {})
        vice = squad.get("vice_captain", {})
        xi = squad.get("starting_xi", [])
        differentials = [p for p in predictions
                         if float(p.get("selected_by_percent", "100")) < 10
                         and p.get("predicted_points", 0) > 4][:5]

        prompt = f"""You are an expert Fantasy Premier League analyst. Analyze the following data for Gameweek {gameweek} and provide:

1. **Captain Pick Analysis** — Why the recommended captain is the best choice, with alternatives
2. **Key Differentials** — Low-ownership players that could gain big rank advantages
3. **Fixture Analysis** — Which fixtures favor attackers vs defenders
4. **Risk Assessment** — Players with injury concerns or rotation risk
5. **Transfer Recommendations** — Who to bring in/out this week

## Recommended Squad (Formation: {squad.get('formation', '?')})
**Captain:** {captain.get('name', '?')} ({captain.get('team', '?')}) — {captain.get('predicted_points', 0):.1f} xPts
**Vice Captain:** {vice.get('name', '?')} ({vice.get('team', '?')}) — {vice.get('predicted_points', 0):.1f} xPts

### Starting XI:
"""
        for p in xi:
            fix = p.get("fixture", {})
            prompt += (f"- {p.get('name', '?')} ({p.get('position', '?')}, {p.get('team', '?')}) "
                       f"— £{p.get('price', 0):.1f}m — xPts: {p.get('predicted_points', 0):.1f} "
                       f"— vs {fix.get('opponent', '?')}({fix.get('venue', '?')}) FDR:{fix.get('fdr', '?')}\n")

        prompt += "\n### Top Differentials (<10% ownership):\n"
        for p in differentials:
            fix = p.get("fixture", {})
            prompt += (f"- {p.get('name', '?')} ({p.get('position', '?')}, {p.get('team', '?')}) "
                       f"— {float(p.get('selected_by_percent', 0)):.1f}% owned "
                       f"— xPts: {p.get('predicted_points', 0):.1f}\n")

        prompt += "\n### Top 10 Predicted Players:\n"
        for i, p in enumerate(predictions[:10], 1):
            fix = p.get("fixture", {})
            prompt += (f"{i}. {p.get('name', '?')} ({p.get('position', '?')}, {p.get('team', '?')}) "
                       f"— xPts: {p.get('predicted_points', 0):.1f} "
                       f"— vs {fix.get('opponent', '?')}({fix.get('venue', '?')}) FDR:{fix.get('fdr', '?')}\n")

        prompt += """
Please provide your analysis in a structured format with clear sections and bullet points.
Focus on actionable insights that an FPL manager can immediately use.
Consider recent injuries, suspensions, and team form in your analysis.
"""
        return prompt

    @staticmethod
    def generate_captain_prompt(top_candidates: list[dict], gameweek: int) -> str:
        """Generate a focused captain pick analysis prompt."""
        prompt = f"""Analyze these captain candidates for FPL Gameweek {gameweek}.
Rank them and explain your reasoning based on form, fixture difficulty, and expected output.

Candidates:
"""
        for i, p in enumerate(top_candidates[:8], 1):
            fix = p.get("fixture", {})
            factors = p.get("factors", {})
            prompt += (f"\n{i}. **{p.get('name', '?')}** ({p.get('team', '?')}, {p.get('position', '?')})\n"
                       f"   - Predicted: {p.get('predicted_points', 0):.1f} pts\n"
                       f"   - Fixture: vs {fix.get('opponent', '?')} ({fix.get('venue', '?')}) FDR: {fix.get('fdr', '?')}\n"
                       f"   - Form: {factors.get('form', 0):.3f} | ICT: {factors.get('ict_index', 0):.3f}\n"
                       f"   - Price: £{p.get('price', 0):.1f}m | Owned: {p.get('selected_by_percent', '?')}%\n")

        prompt += "\nProvide: Top 3 picks with reasoning, risk factors for each, and a bold differential captain option."
        return prompt

    @staticmethod
    def generate_transfer_prompt(current_squad: list[dict],
                                  best_replacements: list[dict],
                                  free_transfers: int,
                                  bank: float) -> str:
        """Generate transfer advice prompt."""
        prompt = f"""FPL Transfer Advice
Free transfers: {free_transfers} | Bank: £{bank:.1f}m

Current underperformers (consider selling):
"""
        for p in current_squad[:5]:
            prompt += f"- {p.get('name', '?')} ({p.get('position', '?')}) — xPts: {p.get('predicted_points', 0):.1f}\n"

        prompt += "\nBest available replacements:\n"
        for p in best_replacements[:10]:
            prompt += (f"- {p.get('name', '?')} ({p.get('position', '?')}, {p.get('team', '?')}) "
                       f"— £{p.get('price', 0):.1f}m — xPts: {p.get('predicted_points', 0):.1f}\n")

        prompt += f"\nRecommend the best {free_transfers} transfer(s) considering points gain, fixture run, and budget."
        return prompt

    @staticmethod
    def build_weekly_report(data: dict) -> str:
        """Build a markdown weekly report from prediction data."""
        sq = data.get("optimal_squad", {})
        gw = data.get("gameweek", "?")
        cap = sq.get("captain", {})
        vice = sq.get("vice_captain", {})

        report = f"""# ⚽ FPL Predictor — Gameweek {gw} Report
*Generated: {data.get('generated_at', '?')}*

---

## 🏆 Optimal Squad
**Formation:** {sq.get('formation', '?')} | **Cost:** £{sq.get('total_cost', 0)}m | **Budget Left:** £{sq.get('budget_remaining', 0)}m
**Predicted Points:** {sq.get('predicted_total_points', 0)}

### 👑 Captain: {cap.get('name', '?')} ({cap.get('team', '?')}) — {cap.get('xpts', 0):.1f} xPts
### © Vice: {vice.get('name', '?')} ({vice.get('team', '?')}) — {vice.get('xpts', 0):.1f} xPts

### Starting XI
| # | Player | Pos | Team | Price | xPts | Fixture | FDR |
|---|--------|-----|------|-------|------|---------|-----|
"""
        for i, p in enumerate(sq.get("starting_xi", []), 1):
            fix = p.get("fixture", {})
            report += (f"| {i} | **{p.get('name', '?')}** | {p.get('pos', '?')} | "
                       f"{p.get('team', '?')} | £{p.get('price', 0):.1f}m | "
                       f"{p.get('xpts', 0):.1f} | {fix.get('opponent', '?')}({fix.get('venue', '?')}) | "
                       f"{fix.get('fdr', '?')} |\n")

        report += "\n### Bench\n"
        for i, p in enumerate(sq.get("bench", []), 1):
            fix = p.get("fixture", {})
            report += f"- B{i}: {p.get('name', '?')} ({p.get('pos', '?')}, {p.get('team', '?')}) — {p.get('xpts', 0):.1f} xPts\n"

        # Differentials
        diffs = data.get("differentials", [])
        if diffs:
            report += "\n---\n\n## 💎 Top Differentials (Low Ownership)\n"
            report += "| Player | Pos | Team | Price | xPts | Owned | Fixture |\n"
            report += "|--------|-----|------|-------|------|-------|--------|\n"
            for p in diffs[:10]:
                fix = p.get("fixture", {})
                report += (f"| {p.get('name', '?')} | {p.get('pos', '?')} | {p.get('team', '?')} | "
                           f"£{p.get('price', 0):.1f}m | {p.get('xpts', 0):.1f} | "
                           f"{p.get('selected_pct', '?')}% | {fix.get('opponent', '?')}({fix.get('venue', '?')}) |\n")

        # Value picks
        vals = data.get("value_picks", [])
        if vals:
            report += "\n---\n\n## 💰 Best Value Picks (≤ £6.0m)\n"
            for p in vals[:10]:
                fix = p.get("fixture", {})
                ppv = p.get("xpts", 0) / max(p.get("price", 4.0), 3.5)
                report += (f"- **{p.get('name', '?')}** ({p.get('pos', '?')}, {p.get('team', '?')}) "
                           f"— £{p.get('price', 0):.1f}m — {p.get('xpts', 0):.1f} xPts "
                           f"— {ppv:.2f} pts/£\n")

        report += "\n---\n\n*Model uses multi-factor analysis: form, fixtures, ICT, team strength, set pieces, and transfer momentum.*\n"
        return report
