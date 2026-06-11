"""Portfolio-level intelligence: risk-tier balance, concentration, and health.

Takes the current portfolio (and individual candidates) and returns structured
observations the agents use to make decisions that improve the portfolio as a
whole, rather than just picking good individual stocks. Risk classification is
based purely on technical signals (ATR, ADX, RSI) — no market cap or sector
data is involved.
"""

from config import settings

# Risk tiers used to classify both held positions and candidates, based purely
# on technical signals (volatility, trend strength, momentum extremes).
RISK_TIERS = ["compounder", "growth", "speculative"]


def classify_risk_tier(signals: dict) -> str:
    """Classify a position or candidate into a risk tier from its technicals.

    - speculative: high volatility (ATR% >= 6) or RSI at an extreme (>=75 or <=25)
    - growth: meaningful volatility (ATR% >= 3) with a strong trend (ADX >= 25)
    - compounder: everything else — low volatility, steadier movers
    """
    atr_pct = float(signals.get("atr_pct", 0) or 0)
    adx = float(signals.get("adx", 0) or 0)
    rsi = float(signals.get("rsi", 50) or 50)

    if atr_pct >= 6 or rsi >= 75 or rsi <= 25:
        return "speculative"
    if atr_pct >= 3 and adx >= 25:
        return "growth"
    return "compounder"


def _position_value(position: dict) -> float:
    """Market value of a position, estimated from qty * avg_cost if absent."""
    mv = position.get("market_value")
    if mv is not None:
        try:
            return abs(float(mv))
        except (TypeError, ValueError):
            pass
    qty = float(position.get("qty", 0) or 0)
    avg_cost = float(position.get("avg_cost", 0) or 0)
    return abs(qty * avg_cost)


def analyse_portfolio(
    portfolio: dict, enriched_positions: list[dict] | None = None
) -> dict:
    """Compute risk-tier breakdown, concentration, and a 0-100 health score.

    `enriched_positions` are positions already merged with their technical
    signals (as built in Phase 1). If omitted, every position is classified
    using empty signals (defaults to "compounder").
    """
    positions = portfolio.get("positions", []) or []
    net_liq = float(portfolio.get("net_liquidation", 0) or 0)
    cash = float(portfolio.get("cash", 0) or 0)

    # Denominator for weights: prefer net liquidation, fall back to the sum of
    # position values so we never divide by zero.
    total_position_value = sum(_position_value(p) for p in positions)
    denom = net_liq if net_liq > 0 else total_position_value

    signals_by_symbol = {p.get("symbol"): p for p in (enriched_positions or [])}

    tier_value: dict[str, float] = {}
    stock_weight: dict[str, float] = {}
    for position in positions:
        symbol = position.get("symbol", "")
        value = _position_value(position)
        signals = signals_by_symbol.get(symbol, {})
        tier = classify_risk_tier(signals)
        tier_value[tier] = tier_value.get(tier, 0.0) + value
        if denom > 0:
            stock_weight[symbol] = stock_weight.get(symbol, 0.0) + value / denom

    tier_breakdown = {
        tier: (value / denom if denom > 0 else 0.0)
        for tier, value in tier_value.items()
    }

    # Dominant risk tier and most concentrated single stock.
    if tier_breakdown:
        dominant_tier = max(tier_breakdown, key=tier_breakdown.get)
        dominant_tier_pct = round(tier_breakdown[dominant_tier] * 100, 1)
    else:
        dominant_tier = "None"
        dominant_tier_pct = 0.0

    if stock_weight:
        most_concentrated_stock = max(stock_weight, key=stock_weight.get)
        most_concentrated_pct = round(stock_weight[most_concentrated_stock] * 100, 1)
    else:
        most_concentrated_stock = "None"
        most_concentrated_pct = 0.0

    tier_count = len(tier_breakdown)
    total_positions = len(positions)

    underrepresented_tiers = [
        t for t in RISK_TIERS if tier_breakdown.get(t, 0.0) == 0.0
    ]

    is_overconcentrated = most_concentrated_pct > settings.MAX_POSITION_PCT
    is_tier_heavy = dominant_tier_pct > 60.0
    cash_pct = (cash / net_liq * 100) if net_liq > 0 else 0.0

    # --- Health score: start at 100 and deduct for risk factors. ---
    health_score = 100
    health_notes: list[str] = []

    if most_concentrated_pct > 20:
        health_score -= 20
    elif most_concentrated_pct > 15:
        health_score -= 10

    if dominant_tier_pct > 60:
        health_score -= 15
    elif dominant_tier_pct > 45:
        health_score -= 8

    if tier_count < 2:
        health_score -= 15

    if total_positions < 4:
        health_score -= 10
    if total_positions > 12:
        health_score -= 5

    if cash_pct < 10:
        health_score -= 10

    health_score = max(0, min(100, health_score))

    # --- Human-readable observations. ---
    if dominant_tier_pct > 60 and dominant_tier != "None":
        health_notes.append(
            f"{dominant_tier.title()} positions are {dominant_tier_pct:.0f}% "
            "of portfolio — consider mixing in other risk tiers"
        )
    if most_concentrated_pct > 15 and most_concentrated_stock != "None":
        health_notes.append(
            f"{most_concentrated_stock} represents {most_concentrated_pct:.0f}% "
            "of portfolio — near position limit"
        )
    if underrepresented_tiers:
        missing = " or ".join(t.title() for t in underrepresented_tiers)
        health_notes.append(f"No exposure to {missing} positions")
    if cash_pct < 10 and net_liq > 0:
        health_notes.append(
            f"Cash reserve is only {cash_pct:.0f}% — limited room to deploy"
        )
    if total_positions < 4:
        health_notes.append(
            f"Only {total_positions} position(s) — portfolio is thinly spread"
        )
    if health_score >= 80 and tier_count >= 2:
        health_notes.append(
            f"Portfolio health is strong — balanced across "
            f"{tier_count} risk tiers"
        )

    return {
        "total_positions": total_positions,
        "tier_breakdown": tier_breakdown,
        "dominant_tier": dominant_tier,
        "dominant_tier_pct": dominant_tier_pct,
        "most_concentrated_stock": most_concentrated_stock,
        "most_concentrated_pct": most_concentrated_pct,
        "tier_count": tier_count,
        "is_overconcentrated": is_overconcentrated,
        "is_tier_heavy": is_tier_heavy,
        "underrepresented_tiers": underrepresented_tiers,
        "health_score": health_score,
        "health_notes": health_notes,
    }


def score_candidate_fit(symbol: str, signals: dict, portfolio_analysis: dict) -> dict:
    """Score how well adding `symbol` would improve the portfolio's risk balance."""
    tier = classify_risk_tier(signals)
    breakdown = portfolio_analysis.get("tier_breakdown", {})
    current_alloc = breakdown.get(tier, 0.0)  # fraction (0..1)
    current_pct = current_alloc * 100

    fit_score = 5
    if current_alloc == 0.0:
        fit_score += 3
        fit_note = f"Adds {tier} exposure — currently 0%"
    elif current_pct < 25:
        fit_score += 2
        fit_note = f"{tier.title()} is underrepresented at {current_pct:.0f}%"
    elif current_pct <= 45:
        fit_note = f"{tier.title()} allocation is balanced at {current_pct:.0f}%"
    elif current_pct <= 60:
        fit_score -= 2
        fit_note = f"{tier.title()} is already heavy at {current_pct:.0f}%"
    else:
        fit_score -= 4
        fit_note = f"{tier.title()} is dangerously concentrated at {current_pct:.0f}%"

    fit_score = max(0, min(10, fit_score))

    adds_diversification = current_pct < 25
    reduces_concentration = (
        tier != portfolio_analysis.get("dominant_tier") and current_pct < 45
    )

    return {
        "risk_tier": tier,
        "adds_diversification": adds_diversification,
        "reduces_concentration": reduces_concentration,
        "fit_score": fit_score,
        "fit_note": fit_note,
    }
