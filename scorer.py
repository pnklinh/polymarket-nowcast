import os
import json
from datetime import datetime
from groq import Groq
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
# ── The prompt that IS your model ────────────────────────────────────────────
SCORER_PROMPT = """You are a quantitative analyst for a prediction market nowcasting system.

Your job: read raw content from a primary source and score how it should update our probability estimate for a specific market question.

MARKET
Question: {question}
Current crowd probability (Polymarket): {crowd_price_pct}%
Resolution date: {end_date}

SIGNAL
Source URL: {source_url}
Source type: {source_type}
Retrieved at: {retrieved_at}

Raw content:
---
{content}
---

Return ONLY valid JSON. No explanation, no markdown, no code fences. Just the JSON object:
{{
  "relevance": 0.0,
  "implied_probability": 0.0,
  "confidence": 0.0,
  "direction": "bullish",
  "information_type": "new_evidence",
  "key_sentence": "the single most important sentence from this content verbatim",
  "reasoning": "2 sentences max explaining the probability estimate"
}}

Field definitions:
- relevance (0–1): Does this content actually address the market question? 0.9+ = directly relevant, 0.5 = tangential, 0.1 = barely related
- implied_probability (0–1): If this content were your ONLY information, what P(YES) would a rational analyst assign? Ignore the crowd price.
- confidence (0–1): How certain are you in that estimate?
  * Official primary source (Fed, SEC, BLS, IAEA) = 0.80–0.95
  * Credible secondary source (Reuters, Bloomberg) = 0.50–0.70
  * Ambiguous or indirect = 0.20–0.40
- direction: "bullish" (YES more likely), "bearish" (NO more likely), or "neutral"
- information_type: one of "confirms", "contradicts", "new_evidence", "noise", "procedural"
- key_sentence: exact quote, under 100 characters
- reasoning: plain English, max 2 sentences

Rules:
- Be CONSERVATIVE. When uncertain, lower confidence.
- If content is not relevant to the question, set relevance < 0.3 and confidence = 0.1
- Do not anchor to the crowd price when estimating implied_probability
- If content is in a foreign language, translate mentally before scoring"""


def score_signal(raw: dict, market: dict) -> dict | None:
    """
    Takes raw crawled content and market context.
    Returns scored signal dict, or None if scoring fails.
    """

    # Don't waste API calls on failed crawls or empty content
    if not raw.get("success") or not raw.get("content"):
        print(f"  Skipping scorer for {raw.get('source_id')} — no content")
        return None

    # Map signal_strength to source_type label for the prompt
    strength_to_type = {
        "very_high": "official primary source",
        "high": "credible primary source",
        "medium": "secondary source",
        "low": "aggregator"
    }
    source_type = strength_to_type.get(raw.get("signal_strength", "medium"), "unknown source")

    prompt = SCORER_PROMPT.format(
        question=market["question"],
        crowd_price_pct=round(market["current_price"] * 100, 1),
        end_date=market.get("end_date", "unknown"),
        source_url=raw["source_url"],
        source_type=source_type,
        retrieved_at=raw["retrieved_at"],
        content=raw["content"][:3000]  # keep within context limits
    )

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}]
    )   
        raw_text = response.choices[0].message.content.strip()


        # Strip any accidental markdown code fences
        if raw_text.startswith("```"):
            raw_text = raw_text.split("```")[1]
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]

        scores = json.loads(raw_text)

        # Attach metadata for aggregator
        scores["source_url"] = raw["source_url"]
        scores["source_id"] = raw.get("source_id", "unknown")
        scores["retrieved_at"] = datetime.fromisoformat(raw["retrieved_at"])
        scores["method"] = raw.get("method", "unknown")

        print(f"  ✓ Scored {raw.get('source_id')}: "
              f"implied_prob={scores['implied_probability']:.2f} "
              f"confidence={scores['confidence']:.2f} "
              f"direction={scores['direction']}")

        return scores

    except json.JSONDecodeError as e:
        print(f"  ✗ JSON parse error for {raw.get('source_id')}: {e}")
        print(f"    Raw response: {raw_text[:200]}")
        return None

    except Exception as e:
        print(f"  ✗ Scorer error for {raw.get('source_id')}: {e}")
        return None


# ── Quick test (run this file directly) ──────────────────────────────────────
if __name__ == "__main__":
    # Test with fake content that looks like a real signal
    test_raw = {
        "success": True,
        "source_url": "https://www.federalreserve.gov/newsevents/speech.htm",
        "source_id": "fed_speeches",
        "signal_strength": "high",
        "content": """
        Chair Powell Speech - March 20, 2026
        "Recent data suggests inflation is on a sustained path toward our 2% target.
        The labor market remains resilient but is showing signs of gradual cooling.
        We remain data-dependent, but the conditions for policy adjustment are becoming
        more favorable. A rate reduction at the June meeting would be appropriate
        if incoming data continues in the current direction."
        """,
        "retrieved_at": datetime.utcnow().isoformat()
    }

    test_market = {
        "question": "Will the Fed cut rates in Q2 2026 (before July 1)?",
        "current_price": 0.72,
        "end_date": "2026-07-01"
    }

    print("Testing scorer on fake Fed speech...")
    result = score_signal(test_raw, test_market)
    print(f"\nResult: {json.dumps(result, default=str, indent=2)}")
