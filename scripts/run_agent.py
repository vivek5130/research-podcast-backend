"""Run the podcast research agent (and optionally TTS) from the command line, without DB/queue.

    python scripts/run_agent.py "startup funding announced in the last 24 hours"
    python scripts/run_agent.py "AI news today" --tts out.mp3
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.handlers.podcast.agent.graph import run_research_agent  # noqa: E402
from app.core import tracing  # noqa: E402
from app.services import tts  # noqa: E402


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("topic")
    p.add_argument("--tts", metavar="OUT.mp3", help="also synthesize audio with Gnani and save it")
    p.add_argument("--duration", type=int, default=300, choices=[15, 30, 60, 120, 300, 600],
                   help="episode length in seconds (default 300)")
    args = p.parse_args()

    with tracing.collect() as traces:
        state = await run_research_agent(args.topic, args.duration)
    print("QUERIES:", *state["queries"], sep="\n  - ")
    print(f"\nRAW RESULTS: {len(state['raw_results'])}  ->  CURATED SOURCES: {len(state['sources'])}")
    for s in state["sources"]:
        print(f"  - {s['title']}  ({s['url']})")
    print(f"\nTITLE: {state['title']}\nWORDS: {state['word_count']}  REVISIONS: {state.get('revisions', 0)}\n")
    print(state["script"])

    if args.tts:
        audio, duration, _ = await tts.synthesize(state["script"])
        with open(args.tts, "wb") as f:
            f.write(audio)
        print(f"\nAUDIO: {args.tts}  ({len(audio)//1024} KB, ~{duration}s)")

    # Same telemetry a real run stores in llm_run_traces, kept in memory here.
    print("\nTRACES:")
    for t in traces:
        tokens = f"in={t.get('input_tokens')} out={t.get('output_tokens')}" if t["kind"] == "llm" else ""
        what = t.get("model") or t["input"].get("query", "")
        print(f"  {t['node'] or '-':13} {t['name']:24} {t['status']:5} {t['latency_ms']:>6} ms  {tokens:22} {what}")
    llm = [t for t in traces if t["kind"] == "llm" and t["status"] == "ok"]
    print(
        f"  LLM calls: {len(llm)}  input tokens: {sum(t.get('input_tokens') or 0 for t in llm)}  "
        f"output tokens: {sum(t.get('output_tokens') or 0 for t in llm)}  "
        f"largest prompt: {max((t.get('input_chars') or 0 for t in llm), default=0)} chars"
    )


if __name__ == "__main__":
    asyncio.run(main())
