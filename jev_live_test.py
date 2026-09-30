"""
Jev vs. LLM live test
=====================

Sends the same six finance/ops decisions (workloads.json) to TypeSafe's Jev and to a
comparable LLM, and records what each call actually cost, how many tokens were billed,
how long it took, and whether the two models agreed.

The output (jev_live_results.csv) has the same columns as the LiveTest tab in
TypeSafe_Jev_Unit_Economics.xlsx, so you can paste the rows straight in.

Setup (one time)
----------------
1. Python 3.9+ (no extra packages needed).
2. A TypeSafe API key from console.typesafe.ai (new accounts come with $5 credit).
3. An OpenRouter API key from openrouter.ai (one key covers OpenAI, Anthropic, Google, etc.).
4. Set both keys as environment variables in your terminal, never in this file:
       export TYPESAFE_API_KEY="..."
       export OPENROUTER_API_KEY="..."

Run
---
    python jev_live_test.py --llm <openrouter-model-id> --repeats 3

Find the exact model id on openrouter.ai/models (e.g. the id shown for GPT-5.6 Terra,
Claude Haiku 4.5, or Gemini 3.8 Flash). Run it once with a cheap non-reasoning model
and once with a reasoning model to fill both comparison cases.

Expected cost: well under $1 in total at the repeats=3 default.
"""

import argparse
import csv
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request

JEV_URL = "https://api.typesafe.ai/v1/systemone"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
JEV_PRICE_PER_MTOK = 0.042  # list price, input tokens; output tokens are free


def post_json(url, payload, headers, timeout=120):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"HTTP {e.code} from {url}: {detail}") from None
    elapsed_ms = (time.perf_counter() - start) * 1000
    return body, elapsed_ms


def call_jev(workload, key):
    payload = {"state": workload["state"], "model": "jev-latest", "questions": workload["questions"]}
    body, ms = post_json(JEV_URL, payload, {"Authorization": f"Bearer {key}"})
    usage = body.get("usage", {})
    tokens_in = usage.get("input_tokens", 0)
    tokens_out = usage.get("output_tokens", 0)
    answers = {}
    for qid, a in body.get("answers", {}).items():
        t = a.get("type")
        if t == "choice":
            answers[qid] = a.get("choice")
        elif t == "noul":
            answers[qid] = a.get("noul")
        elif t == "score":
            answers[qid] = a.get("score")
    return {"ms": ms, "tokens_in": tokens_in, "tokens_out": tokens_out,
            "cost": tokens_in / 1e6 * JEV_PRICE_PER_MTOK, "answers": answers}


def llm_prompt(workload):
    lines = [
        "You are making automated decisions inside business software.",
        "Read the STATE, then answer every QUESTION.",
        "Return ONLY a JSON object mapping each question id to an object:",
        '  choice questions -> {"choice": "<one option key>", "probability": <0-1>}',
        '  noul questions   -> {"noul": <probability the answer is true, 0-1>}',
        '  score questions  -> {"score": <index of the chosen level, 0 = first level>, "probability": <0-1>}',
        "",
        "STATE:",
        json.dumps(workload["state"], indent=1),
        "",
        "QUESTIONS:",
        json.dumps(workload["questions"], indent=1),
    ]
    return "\n".join(lines)


def call_llm(workload, key, model):
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": llm_prompt(workload)}],
        "response_format": {"type": "json_object"},
        "usage": {"include": True},
    }
    body, ms = post_json(OPENROUTER_URL, payload, {"Authorization": f"Bearer {key}"})
    usage = body.get("usage", {}) or {}
    details = usage.get("completion_tokens_details") or {}
    text = (body.get("choices") or [{}])[0].get("message", {}).get("content") or "{}"
    # Some providers ignore response_format and wrap the JSON in a code fence followed by
    # prose, so decode the first JSON object in the text rather than the whole string.
    try:
        parsed, _ = json.JSONDecoder().raw_decode(text[text.index("{"):])
    except (ValueError, json.JSONDecodeError):
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    answers = {}
    for qid, a in parsed.items():
        if isinstance(a, dict):
            answers[qid] = a.get("choice", a.get("noul", a.get("score")))
    return {"ms": ms,
            "tokens_in": usage.get("prompt_tokens", 0),
            "tokens_out": usage.get("completion_tokens", 0),
            "reasoning_tokens": details.get("reasoning_tokens", 0),
            "cost": usage.get("cost", ""),  # OpenRouter reports actual $ cost when usage.include is set
            "answers": answers, "valid_json": bool(parsed)}


def agreement(workload, jev_ans, llm_ans):
    """Share of choice/noul questions where both models gave the same decision."""
    checked = agreed = 0
    for qid, q in workload["questions"].items():
        j, l = jev_ans.get(qid), llm_ans.get(qid)
        if j is None or l is None:
            continue
        if q["type"] == "choice":
            checked += 1
            agreed += int(str(j) == str(l))
        elif q["type"] == "noul":
            try:
                checked += 1
                agreed += int((float(j) >= 0.5) == (float(l) >= 0.5))
            except (TypeError, ValueError):
                pass
    return agreed / checked if checked else ""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--llm", required=True, help="OpenRouter model id for the comparison LLM")
    ap.add_argument("--repeats", type=int, default=3, help="calls per workload per model (median latency)")
    ap.add_argument("--workloads", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "workloads.json"))
    ap.add_argument("--out", default="jev_live_results.csv")
    args = ap.parse_args()

    jev_key = os.environ.get("TYPESAFE_API_KEY")
    or_key = os.environ.get("OPENROUTER_API_KEY")
    if not jev_key or not or_key:
        sys.exit("Set TYPESAFE_API_KEY and OPENROUTER_API_KEY as environment variables first.")

    with open(args.workloads) as f:
        workloads = json.load(f)

    rows = []
    for w in workloads:
        jev_runs, llm_runs = [], []
        for i in range(args.repeats):
            try:
                jev_runs.append(call_jev(w, jev_key))
            except RuntimeError as e:
                print(f"[jev] {w['id']} run {i+1}: {e}")
            try:
                llm_runs.append(call_llm(w, or_key, args.llm))
            except RuntimeError as e:
                print(f"[llm] {w['id']} run {i+1}: {e}")
            time.sleep(0.5)
        if not jev_runs or not llm_runs:
            print(f"Skipping {w['id']}: no successful runs for one of the models.")
            continue
        j0, l0 = jev_runs[0], llm_runs[0]
        llm_costs = [r["cost"] for r in llm_runs if r["cost"] != ""]
        rows.append({
            "workload": w["name"],
            "questions": len(w["questions"]),
            "jev_input_tokens": j0["tokens_in"],
            "jev_output_tokens": j0["tokens_out"],
            "jev_median_latency_ms": round(statistics.median(r["ms"] for r in jev_runs)),
            "jev_cost_usd": round(j0["cost"], 8),
            "llm_model": args.llm,
            "llm_input_tokens": l0["tokens_in"],
            "llm_output_tokens": l0["tokens_out"],
            "llm_reasoning_tokens": l0["reasoning_tokens"],
            "llm_median_latency_ms": round(statistics.median(r["ms"] for r in llm_runs)),
            "llm_cost_usd": round(statistics.median(llm_costs), 8) if llm_costs else "",
            "llm_valid_json": all(r["valid_json"] for r in llm_runs),
            "agreement": agreement(w, j0["answers"], l0["answers"]),
            "jev_answers": json.dumps(j0["answers"]),
            "llm_answers": json.dumps(l0["answers"]),
        })
        print(f"done: {w['name']}")

    if not rows:
        sys.exit("No results to write.")
    with open(args.out, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nWrote {len(rows)} rows to {args.out}. Paste them into the LiveTest tab (row 18 onward).")


if __name__ == "__main__":
    main()
