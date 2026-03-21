import os
import json
import sqlite3
from datetime import datetime
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from crawler import crawl_source
from scorer import score_signal
from nowcast import compute_nowcast

load_dotenv()

app = FastAPI(title="NowCast Edge API", version="1.0.0")

# Allow the frontend (localhost:5173 or your HTML file) to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

# ── Load source maps once at startup ─────────────────────────────────────────
with open("source_maps.json") as f:
    SOURCE_MAPS = json.load(f)

# ── SQLite setup ──────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect("nowcast.db")
    conn.row_factory = sqlite3.Row  # lets us access columns by name
    return conn

def init_db():
    db = get_db()
    db.execute("""
        CREATE TABLE IF NOT EXISTS nowcasts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id       TEXT NOT NULL,
            timestamp       TEXT NOT NULL,
            nowcast         REAL,
            crowd_price     REAL,
            delta           REAL,
            delta_pct       TEXT,
            direction       TEXT,
            signal_count    INTEGER,
            confidence      REAL,
            top_source_id   TEXT,
            top_sentence    TEXT,
            full_result     TEXT
        )
    """)
    db.commit()
    db.close()
    print("Database ready.")

init_db()


# ── Core scan function (runs in background) ───────────────────────────────────
def run_scan(market_id: str):
    """
    Full pipeline for one market:
    1. Load source map
    2. Crawl each source (free or TinyFish)
    3. Score each result with LLM
    4. Aggregate into nowcast
    5. Save to database
    """
    print(f"\n{'='*50}")
    print(f"SCAN START: {market_id} at {datetime.utcnow().isoformat()}")

    if market_id not in SOURCE_MAPS:
        print(f"ERROR: Unknown market_id: {market_id}")
        return

    market_config = SOURCE_MAPS[market_id]
    market_ctx = {
        "question": market_config["question"],
        "current_price": market_config["p_market"],
        "end_date": market_config.get("end_date", "unknown")
    }

    # Step 1: Crawl all sources
    print(f"\n[1/3] Crawling {len(market_config['sources'])} sources...")
    raw_results = []
    for source in market_config["sources"]:
        result = crawl_source(source)
        raw_results.append(result)

    successful = [r for r in raw_results if r["success"]]
    print(f"  → {len(successful)}/{len(raw_results)} sources crawled successfully")

    # Step 2: Score each result
    print(f"\n[2/3] Scoring signals with LLM...")
    scored = []
    for raw in raw_results:
        score = score_signal(raw, market_ctx)
        if score:
            scored.append(score)

    print(f"  → {len(scored)} signals scored")

    # Step 3: Compute nowcast
    print(f"\n[3/3] Computing nowcast...")
    result = compute_nowcast(
        signals=scored,
        crowd_price=market_ctx["current_price"]
    )

    result["market_id"] = market_id
    result["timestamp"] = datetime.utcnow().isoformat()

    # Save to database
    db = get_db()
    db.execute("""
        INSERT INTO nowcasts
            (market_id, timestamp, nowcast, crowd_price, delta, delta_pct,
             direction, signal_count, confidence, top_source_id, top_sentence, full_result)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        market_id,
        result["timestamp"],
        result["nowcast"],
        result["crowd_price"],
        result["delta"],
        result["delta_pct"],
        result["direction"],
        result["signal_count"],
        result["confidence_score"],
        result["top_signal"]["source_id"] if result["top_signal"] else None,
        result["top_signal"]["key_sentence"] if result["top_signal"] else None,
        json.dumps(result, default=str)
    ))
    db.commit()
    db.close()

    print(f"\nRESULT:")
    print(f"  Crowd:   {result['crowd_price']:.1%}")
    print(f"  Nowcast: {result['nowcast']:.1%}")
    print(f"  Delta:   {result['delta_pct']}  →  {result['direction']}")
    if result["top_signal"]:
        print(f"  Top:     {result['top_signal']['key_sentence'][:80]}")
    print(f"{'='*50}\n")


# ── API Endpoints ─────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"status": "NowCast Edge API running", "markets": list(SOURCE_MAPS.keys())}


@app.get("/markets")
def get_markets():
    """List all available markets with their questions and current crowd prices."""
    return [
        {
            "id": market_id,
            "question": config["question"],
            "crowd_price": config["p_market"],
            "source_count": len(config["sources"])
        }
        for market_id, config in SOURCE_MAPS.items()
    ]


@app.post("/scan/{market_id}")
def trigger_scan(market_id: str, background_tasks: BackgroundTasks):
    """
    Trigger a nowcast scan for a market.
    Runs in the background — returns immediately.
    Poll /nowcast/{market_id} to get results.
    """
    if market_id not in SOURCE_MAPS:
        raise HTTPException(status_code=404, detail=f"Unknown market: {market_id}")

    background_tasks.add_task(run_scan, market_id)

    return {
        "status": "scan_started",
        "market_id": market_id,
        "message": f"Scanning {len(SOURCE_MAPS[market_id]['sources'])} sources. Poll /nowcast/{market_id} for results."
    }


@app.get("/nowcast/{market_id}")
def get_nowcast(market_id: str):
    """Get the most recent nowcast result for a market."""
    db = get_db()
    row = db.execute("""
        SELECT full_result, timestamp FROM nowcasts
        WHERE market_id = ?
        ORDER BY timestamp DESC
        LIMIT 1
    """, (market_id,)).fetchone()
    db.close()

    if not row:
        return {
            "status": "no_data",
            "message": f"No scan run yet for {market_id}. POST /scan/{market_id} to start."
        }

    result = json.loads(row["full_result"])
    result["status"] = "ok"
    return result


@app.get("/history/{market_id}")
def get_history(market_id: str, limit: int = 20):
    """Get the last N nowcast results for a market — useful for the sparkline."""
    db = get_db()
    rows = db.execute("""
        SELECT timestamp, nowcast, crowd_price, delta, direction,
               top_source_id, top_sentence, signal_count
        FROM nowcasts
        WHERE market_id = ?
        ORDER BY timestamp DESC
        LIMIT ?
    """, (market_id, limit)).fetchall()
    db.close()

    return {
        "market_id": market_id,
        "history": [dict(row) for row in rows]
    }


@app.get("/signals/{market_id}")
def get_signals(market_id: str):
    """Get all individual signal contributions from the latest scan."""
    db = get_db()
    row = db.execute("""
        SELECT full_result FROM nowcasts
        WHERE market_id = ?
        ORDER BY timestamp DESC LIMIT 1
    """, (market_id,)).fetchone()
    db.close()

    if not row:
        return {"signals": []}

    result = json.loads(row["full_result"])
    return {
        "market_id": market_id,
        "nowcast": result.get("nowcast"),
        "crowd_price": result.get("crowd_price"),
        "signals": result.get("all_contributions", [])
    }


# ── Run directly with: python api.py ─────────────────────────────────────────
app.mount("/", StaticFiles(directory=".", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
