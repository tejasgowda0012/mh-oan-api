#!/usr/bin/env python3
"""
Run a batch of Bhili (bhb) questions against the /chat endpoint and collect the
model's responses.

Since the NMT layer was removed, the model now answers Bhili directly, so every
request uses source_lang=bhb and target_lang=bhb — exactly the path any other
language takes.

Usage
-----
    # Make sure the API is running first, e.g.:
    #   uvicorn main:app --host 0.0.0.0 --port 8000
    python evaluation/bhili_questions_run.py

    # Options
    python evaluation/bhili_questions_run.py --base-url http://localhost:8000 \
        --out evaluation/bhili_responses.json

Outputs both a JSON file (structured) and a Markdown file (human-readable) next
to each other.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path

import httpx

# Each question is answered in its own fresh session so there is no
# cross-contamination from conversation history.
QUESTIONS: list[str] = [
    "आगला काही दिही पांय पोडनू शक्यता हाय का?",
    "या आठवडाम हवामान केहकी री?",
    "या आठवडाम खत टाकनु योग्य री का?",
    "या आठवडाम जोरदार पांय पोडनू शक्यता हाय का?",
    "साताराम गोंव पिकाल इया आठवडाम पांय देवनू का?",
    "पायाकी या आठवडाम फवारणी नाय केनु का?",
    "लातूर माजे नाय तेथायत जोरदार पैसा पडताहा का?",
    "नाशिक जिल्हा खातोर 5 दिवसा हावमान अंदाज केयोहो?",
    "सोलापूरू माजमे उदया पाडन्या शक्य आही का?",
    "अहमदनगरूमे सोयाबीनू आरी फवारणी केरूलो का?",
    "माझ्या खेता जागेने तुवी साठवणुकी खातोर बादाहाने जागऱ्या सुविधा केल्ली हाय?",
    "हिंगोली जिल्हाम मा हळदी पीक मा सुरक्षित किही थोवी सेकू?",
    "माझ्या गव्हा पिका खातोर आखाहा पाहीने सरकारी गोदाम देखावा.",
    "जळगाव जवळ केळीसाठी शीतगृह केंद्र शोधा.",
    "चंद्रपूर पाही भात (Paddy) थोवाखातोर गोदाम उपलब्ध हाय का?",
    "वाशिम भागाम मा मूग पिकाखातोर गोदाम होदा.",
    "स्फुरद आणि पालाशची तपासणी करणारी प्रयोगशाळा माझ्यत्रण केहेकी केनु ?",
    "जसवंदावेने पुंड्यां ढेणकून चे नैसर्गिक शत्रू केलो हाय?",
    "वांग्यावेने शेंडा आन फळी पोकळणाऱ्या अडि खातोर कामगंधपाश केहेकी वापरुनु?",
    "भाजीपाला पिकांम सुत्रकृमी व्यवस्थापन केहकी केनू?",
    "सोयाबीनाम पिवलो मोसेक ईया लक्षण काय हाय?",
    "सावली-जली पुगंम माशी नियंत्रण केरुलो का?",
    "मृद संवर्धन केताहा एकसारको बंद केहकी केनू?",
    "झेंडू आने टमाटाम फसा पिक पद्धत कशिशी वापरूलो?",
    "मेंढ्यामाध्य फऱ्या (लाज) या रोगहा लक्षणे कोते हाय?",
    "पावसाला साठी शेळ्याहा कामु खोरचतला निवारा केहकी बनवुलो ?",
    "अतिरिक्त रासायन खत वापरूलो जमिनीमेने जीव जंतू केहकी परिणाम वेहे?",
    "चुन्याचा व्यापार केनू आमली जमीन काशी सुधारवी?",
    "मिरची रंग टिकवा खातोर अनहत वाळवना योग्य पद्धत कोती?",
    "लातुर मंडीम आज सोयाबीन भाव कोतो हाय?",
]


async def ask_one(
    client: httpx.AsyncClient,
    base_url: str,
    question: str,
    idx: int,
    total: int,
) -> dict:
    """Stream one question through /chat and return the collected response."""
    session_id = str(uuid.uuid4())
    params = {
        "query": question,
        "session_id": session_id,
        "source_lang": "bhb",
        "target_lang": "bhb",
        "user_id": "bhili_eval",
    }

    print(f"[{idx}/{total}] {question}")
    chunks: list[str] = []
    error: str | None = None
    try:
        async with client.stream(
            "GET", f"{base_url}/api/chat/", params=params, timeout=180.0
        ) as resp:
            resp.raise_for_status()
            # The endpoint yields raw text chunks (not SSE data: frames),
            # so we just concatenate them.
            async for chunk in resp.aiter_text():
                chunks.append(chunk)
    except Exception as e:  # noqa: BLE001 — record and continue with the batch
        error = f"{type(e).__name__}: {e}"
        print(f"    ! error: {error}")

    answer = "".join(chunks).strip()
    if answer and not error:
        preview = answer.replace("\n", " ")[:80]
        print(f"    -> {preview}{'…' if len(answer) > 80 else ''}")

    return {
        "index": idx,
        "session_id": session_id,
        "question": question,
        "answer": answer,
        "error": error,
    }


async def run(base_url: str, out_json: Path, concurrency: int) -> None:
    base_url = base_url.rstrip("/")
    results: list[dict] = []
    total = len(QUESTIONS)
    semaphore = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient() as client:
        async def bounded(question: str, idx: int) -> dict:
            async with semaphore:
                return await ask_one(client, base_url, question, idx, total)

        tasks = [bounded(q, i) for i, q in enumerate(QUESTIONS, start=1)]
        for coro in asyncio.as_completed(tasks):
            results.append(await coro)

    results.sort(key=lambda r: r["index"])

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "base_url": base_url,
        "source_lang": "bhb",
        "target_lang": "bhb",
        "count": len(results),
        "results": results,
    }
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # Human-readable Markdown alongside the JSON.
    out_md = out_json.with_suffix(".md")
    lines = [
        f"# Bhili (bhb) chat responses",
        "",
        f"- Generated: {payload['generated_at']}",
        f"- Endpoint: `{base_url}/api/chat/` (source_lang=bhb, target_lang=bhb)",
        f"- Questions: {len(results)}",
        "",
    ]
    for r in results:
        lines.append(f"## {r['index']}. {r['question']}")
        lines.append("")
        if r["error"]:
            lines.append(f"> ⚠️ **Error:** {r['error']}")
        else:
            lines.append(r["answer"] or "_(empty response)_")
        lines.append("")
    out_md.write_text("\n".join(lines), encoding="utf-8")

    errors = sum(1 for r in results if r["error"])
    print(
        f"\nDone. {len(results)} questions, {errors} error(s).\n"
        f"  JSON: {out_json}\n"
        f"  MD:   {out_md}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch-run Bhili questions against /chat.")
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="Base URL of the running API (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).parent / "bhili_responses.json",
        help="Output JSON path (a .md sibling is also written).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=3,
        help="How many questions to run in parallel (default: 3).",
    )
    args = parser.parse_args()
    asyncio.run(run(args.base_url, args.out, args.concurrency))


if __name__ == "__main__":
    main()
