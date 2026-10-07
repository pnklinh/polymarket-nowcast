# polymarket-nowcasting
Real-time nowcasting system for prediction markets. Monitors primary sources across the hidden web — government portals, SEC filings, regulatory databases, foreign press — and surfaces information edges before the crowd reprices.

### How it works

For each active Polymarket market, DeltaNow:

1. Dispatches AI web agents (TinyFish) to crawl primary sources the crowd isn't watching
2. Scores each signal for relevance, implied probability, and confidence using an LLM
3. Aggregates scores with exponential recency decay into a single nowcast estimate
4. Computes `divergence = nowcast − crowd price`
5. Flags markets where the gap exceeds a threshold as potential edges

### Architecture

| Component | Role |
|---|---|
| Browser | Frontend — polls REST endpoints |
| FastAPI backend | Orchestrates crawls, scoring, aggregation |
| TinyFish | AI web agents — crawls hidden web sources |
| Polymarket Gamma API | Fetches live crowd prices |
| Groq LLM | Scores signals for relevance and implied probability |


### Stack

`Python` `FastAPI` `TinyFish` `Groq (LLaMA 3.3 70B)` `Polymarket API` `SQLite` `React`

### Known limitations

- LLM scoring is probabilistic — confidence varies by source quality and market type. An updated version would contain an ensemble scoring technique.
- TinyFish crawls take 30–120s per source depending on site complexity.
- Divergence ≠ edge. Information asymmetry exists but is not guaranteed to persist until resolution.
