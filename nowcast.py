import math
from datetime import datetime


# ── Recency decay ─────────────────────────────────────────────────────────────
def recency_weight(retrieved_at: datetime, lambda_decay: float = 0.07) -> float:
    """
    Signals decay over time. Lambda=0.07 means:
      - 0 hours ago  → weight 1.00
      - 6 hours ago  → weight 0.65
      - 24 hours ago → weight 0.19
      - 48 hours ago → weight 0.03
    """
    hours_ago = (datetime.utcnow() - retrieved_at).total_seconds() / 3600
    return math.exp(-lambda_decay * max(0, hours_ago))


# ── Main aggregator ───────────────────────────────────────────────────────────
def compute_nowcast(
    signals: list[dict],
    crowd_price: float,
    market_liquidity: float = 0.5,
    edge_threshold: float = 0.05
) -> dict:
    """
    Combines all scored signals into a single nowcast probability.

    The crowd price is treated as one more signal — informed but lagged.
    Higher liquidity = higher confidence in crowd price.

    Args:
        signals:          list of dicts from scorer.py (each has implied_probability,
                          confidence, relevance, retrieved_at, source_url, key_sentence)
        crowd_price:      current Polymarket price, 0.0–1.0
        market_liquidity: 0.0 (thin/niche) to 1.0 (very liquid), default 0.5
        edge_threshold:   minimum |delta| to generate a BUY signal, default 0.05

    Returns:
        dict with nowcast, crowd_price, delta, direction, top_signal, all contributions
    """

    # Add crowd price as an explicit signal
    crowd_signal = {
        "implied_probability": crowd_price,
        "confidence": 0.25 + (market_liquidity * 0.45),  # 0.25–0.70 based on liquidity
        "relevance": 1.0,
        "retrieved_at": datetime.utcnow(),
        "source_url": "polymarket_crowd",
        "source_id": "crowd",
        "key_sentence": f"Crowd currently pricing this at {crowd_price:.0%}",
        "direction": "neutral",
        "information_type": "crowd_consensus"
    }

    # Filter out low-relevance noise (< 0.3) and failed scores
    valid_signals = [s for s in signals if s and s.get("relevance", 0) >= 0.3]
    all_signals = valid_signals + [crowd_signal]

    if not all_signals:
        return {
            "nowcast": crowd_price,
            "crowd_price": crowd_price,
            "delta": 0.0,
            "delta_pct": "0%",
            "direction": "NO EDGE",
            "signal_count": 0,
            "confidence_score": 0.0,
            "top_signal": None,
            "all_contributions": [],
            "error": "No valid signals"
        }

    # Compute weighted sum
    weighted_sum = 0.0
    weight_total = 0.0
    contributions = []

    for s in all_signals:
        rw = recency_weight(s["retrieved_at"])
        # Weight = how relevant × how confident × how recent
        w = s["confidence"] * s["relevance"] * rw

        weighted_sum += s["implied_probability"] * w
        weight_total += w

        contributions.append({
            "source_id": s.get("source_id", "unknown"),
            "source_url": s.get("source_url", ""),
            "implied_prob": round(s["implied_probability"], 3),
            "weight": round(w, 4),
            "direction": s.get("direction", "neutral"),
            "key_sentence": s.get("key_sentence", ""),
            "information_type": s.get("information_type", "unknown"),
            "recency_weight": round(rw, 3)
        })

    if weight_total == 0:
        return {
            "nowcast": crowd_price,
            "crowd_price": crowd_price,
            "delta": 0.0,
            "delta_pct": "0%",
            "direction": "NO EDGE",
            "signal_count": 0,
            "confidence_score": 0.0,
            "top_signal": None,
            "all_contributions": contributions,
            "error": "All signal weights were zero"
        }

    # Final nowcast
    nowcast = weighted_sum / weight_total
    nowcast = round(max(0.01, min(0.99, nowcast)), 3)
    delta = round(nowcast - crowd_price, 3)

    # Direction signal
    if delta > edge_threshold:
        direction = "BUY YES"
    elif delta < -edge_threshold:
        direction = "BUY NO"
    else:
        direction = "NO EDGE"

    # Sort contributions by weight descending so top signal is most influential
    contributions.sort(key=lambda x: x["weight"], reverse=True)

    # Overall confidence = average weight of non-crowd signals
    non_crowd = [c for c in contributions if c["source_id"] != "crowd"]
    avg_confidence = (
        sum(c["weight"] for c in non_crowd) / len(non_crowd)
        if non_crowd else 0.0
    )

    return {
        "nowcast": nowcast,
        "crowd_price": round(crowd_price, 3),
        "delta": delta,
        "delta_pct": f"{delta:+.1%}",
        "direction": direction,
        "signal_count": len(valid_signals),
        "confidence_score": round(avg_confidence, 3),
        "top_signal": contributions[0] if contributions else None,
        "all_contributions": contributions,
        "error": None
    }


# ── Quick test ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    from datetime import timedelta

    # Simulate 3 scored signals of varying recency and strength
    test_signals = [
        {
            "implied_probability": 0.82,
            "confidence": 0.85,
            "relevance": 0.95,
            "retrieved_at": datetime.utcnow(),           # just now
            "source_url": "federalreserve.gov",
            "source_id": "fed_speeches",
            "key_sentence": "Conditions for rate reduction becoming more favorable",
            "direction": "bullish",
            "information_type": "new_evidence"
        },
        {
            "implied_probability": 0.60,
            "confidence": 0.70,
            "relevance": 0.80,
            "retrieved_at": datetime.utcnow() - timedelta(hours=6),  # 6hrs old
            "source_url": "atlantafed.org",
            "source_id": "gdpnow",
            "key_sentence": "GDPNow estimate revised down to 1.2% for Q1 2026",
            "direction": "bullish",
            "information_type": "confirms"
        },
        {
            "implied_probability": 0.35,
            "confidence": 0.75,
            "relevance": 0.90,
            "retrieved_at": datetime.utcnow() - timedelta(hours=2),
            "source_url": "bls.gov",
            "source_id": "bls_jobs",
            "key_sentence": "Unemployment fell to 3.8%, beating expectations of 4.0%",
            "direction": "bearish",
            "information_type": "contradicts"
        }
    ]

    result = compute_nowcast(test_signals, crowd_price=0.72)

    print(f"Crowd price:  {result['crowd_price']:.1%}")
    print(f"Nowcast:      {result['nowcast']:.1%}")
    print(f"Delta:        {result['delta_pct']}")
    print(f"Direction:    {result['direction']}")
    print(f"Top signal:   {result['top_signal']['key_sentence']}")
    print(f"\nAll contributions:")
    for c in result["all_contributions"]:
        print(f"  {c['source_id']:20s}  implied={c['implied_prob']:.2f}  weight={c['weight']:.3f}  {c['direction']}")
