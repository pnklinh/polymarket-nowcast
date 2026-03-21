import os
import httpx
from bs4 import BeautifulSoup
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# ── TinyFish client ──────────────────────────────────────────────────────────
# Lazy import so the app doesn't crash if tinyfish isn't installed yet
def get_tinyfish_client():
    try:
        from tinyfish import TinyFish
        return TinyFish()  # reads TINYFISH_API_KEY from env automatically
    except ImportError:
        print("WARNING: tinyfish package not installed. Run: pip install tinyfish")
        return None


# ── Free tier: plain HTTP + BeautifulSoup ────────────────────────────────────
def crawl_free(url: str, goal: str) -> dict:
    """
    For public static pages — no cost, instant.
    Examples: Fed speeches page, BLS jobs report, SEC EDGAR search
    """
    try:
        r = httpx.get(
            url,
            timeout=15,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; NowcastBot/1.0)"}
        )
        r.raise_for_status()

        soup = BeautifulSoup(r.text, "html.parser")

        # Strip nav, footer, scripts — keep content only
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        text = soup.get_text(separator="\n", strip=True)
        # Remove blank lines and very short lines (menu items etc.)
        lines = [l.strip() for l in text.splitlines() if len(l.strip()) > 40]
        content = "\n".join(lines)[:5000]  # cap at 5k chars for LLM

        return {
            "source_url": url,
            "goal": goal,
            "content": content,
            "retrieved_at": datetime.utcnow().isoformat(),
            "method": "free",
            "success": True,
            "error": None
        }

    except Exception as e:
        return {
            "source_url": url,
            "goal": goal,
            "content": "",
            "retrieved_at": datetime.utcnow().isoformat(),
            "method": "free",
            "success": False,
            "error": str(e)
        }


# ── TinyFish tier: real browser, handles JS + auth ───────────────────────────
def crawl_tinyfish(url: str, goal: str) -> dict:
    """
    For dynamic, JS-rendered, or authenticated pages.
    Costs TinyFish steps — use only when free tier can't reach the page.
    Examples: CME FedWatch (JS dashboard), CoinDesk markets (dynamic)
    """
    tf = get_tinyfish_client()

    if tf is None:
        return {
            "source_url": url,
            "goal": goal,
            "content": "",
            "retrieved_at": datetime.utcnow().isoformat(),
            "method": "tinyfish",
            "success": False,
            "error": "TinyFish client not available"
        }

    try:
        result_text = ""

        # Stream events from TinyFish agent
        with tf.agent.stream(url=url, goal=goal) as stream:
            for event in stream:
                # The final event contains the structured output
                if hasattr(event, 'output') and event.output:
                    result_text = event.output
                elif hasattr(event, 'content') and event.content:
                    result_text = event.content
                # Print progress for demo logging
                print(f"  [TinyFish] event: {type(event).__name__}")

        return {
            "source_url": url,
            "goal": goal,
            "content": result_text[:5000],
            "retrieved_at": datetime.utcnow().isoformat(),
            "method": "tinyfish",
            "success": bool(result_text),
            "error": None if result_text else "Empty response from TinyFish"
        }

    except Exception as e:
        return {
            "source_url": url,
            "goal": goal,
            "content": "",
            "retrieved_at": datetime.utcnow().isoformat(),
            "method": "tinyfish",
            "success": False,
            "error": str(e)
        }


# ── Router: pick the right tier ──────────────────────────────────────────────
def crawl_source(source: dict) -> dict:
    """
    Routes to free or TinyFish based on source config.
    Adds source metadata to the result.
    """
    print(f"  Crawling [{source['tier'].upper()}] {source['url'][:60]}...")

    if source["tier"] == "free":
        result = crawl_free(source["url"], source["goal"])
    else:
        result = crawl_tinyfish(source["url"], source["goal"])

    # Attach source metadata so scorer knows what it's working with
    result["source_id"] = source["id"]
    result["signal_strength"] = source["signal_strength"]

    if result["success"]:
        print(f"  ✓ Got {len(result['content'])} chars from {source['id']}")
    else:
        print(f"  ✗ Failed {source['id']}: {result['error']}")

    return result


# ── Quick test (run this file directly to test a single crawl) ───────────────
if __name__ == "__main__":
    print("Testing free crawler on Fed speeches page...")
    result = crawl_free(
        "https://www.federalreserve.gov/newsevents/speech.htm",
        "List recent Fed governor speeches"
    )
    print(f"Success: {result['success']}")
    print(f"Content preview:\n{result['content'][:500]}")
