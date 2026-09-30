# TypeSafe Jev live test

A cost, latency and quality comparison of TypeSafe's Jev model against two LLMs (Claude Haiku 4.5 and GPT-5.6 Terra, both via OpenRouter), run on six finance/ops decision workloads. The measured results feed a unit-economics model of TypeSafe.

Run date: 2026-09-29. Total API spend for the test: about $0.10.

## Files

| File | What it is |
|---|---|
| `jev_live_test.py` | Sends each workload to Jev and to one LLM, writes a CSV of tokens, cost, latency and answers |
| `workloads.json` | The six decision workloads, in Jev's request format |
| `results_fast.csv` | Results vs. Claude Haiku 4.5 (`anthropic/claude-haiku-4.5`), 3 repeats |
| `results_reasoning.csv` | Results vs. GPT-5.6 Terra (`openai/gpt-5.6-terra`), 3 repeats |
| `TypeSafe_Jev_Unit_Economics.xlsx` | The original model, before live results |
| `TypeSafe_Jev_Unit_Economics_live.xlsx` | The model with live results pasted into LiveTest, the tokens-per-decision source set to "Live test", and measured LLM output/reasoning tokens on the Prices tab |

## Results

| | Jev | Claude Haiku 4.5 | GPT-5.6 Terra |
|---|---|---|---|
| Median latency | ~260 ms | 4.4 s | 3.1 s |
| Cost per decision | $0.000038 | $0.00265 | $0.00251 |
| Cost vs. Jev | 1x | 69x | 65x |
| Agreement with Jev (choice and yes/no questions) | – | 91.7% | 91.7% |

- **Jev bills about 2x the offline token estimate.** Average billed input was 913 tokens per decision, against 453 counted offline (ratio 2.02x). A rough fit across the six workloads gives about 180 tokens of fixed overhead per call plus about 1.6x the offline count. Structured inputs with many options cost more per token than prose.
- **Jev billed more input tokens than either LLM received**, even though the LLM prompts include extra output-format instructions (913 vs. 812 for Haiku and 687 for Terra). The model's assumption that an LLM needs 150 extra prompt tokens (Assumptions B26) does not hold here.
- **LLM output was very different from the model's estimates.** Haiku averaged 371 visible output tokens per decision (estimate: 60) because it added an explanation of its reasoning after the JSON. Terra averaged 43 visible and 78 reasoning tokens (estimate: 60 and 1,000).
- **Where the models clearly disagreed:**
  - Ticket routing: Jev chose `tech_support_p1`; Haiku chose `account_mgmt`.
  - Expense check: Jev picked the swag purchase as the worst violation; Terra picked the premium-economy airfare.

## Caveats

- Jev's cost is list price ($0.042 per million input tokens) times billed tokens. Jev's API doesn't return a dollar cost. LLM costs are the actual charges OpenRouter reported.
- Token counts, costs and answers come from the first run of each workload. Latency, and the LLM cost, use the median of 3 runs.
- The agreement rate only compares choice and yes/no questions. On score questions, Jev returns a weighted average that can fall between levels, so it isn't compared directly with the LLMs' whole-number levels.
- The workbook's LiveTest summary breaks results out per LLM. Per-model latency there is the average of the per-workload medians, so it differs slightly from the medians in the table above.
- The offline token counts use OpenAI's o200k tokenizer as a proxy for Jev's. Jev's own tokenizer is not public.

## Running it yourself

Requires Python 3.9+ and no extra packages. Set your keys as environment variables; never commit them:

```
export TYPESAFE_API_KEY="..."
export OPENROUTER_API_KEY="..."
python3 jev_live_test.py --llm anthropic/claude-haiku-4.5 --repeats 3 --out results_fast.csv
python3 jev_live_test.py --llm openai/gpt-5.6-terra --repeats 3 --out results_reasoning.csv
```

One change was made to the original script: the LLM answer parser now reads the first JSON object in the reply. Before the change, parsing failed whenever a model ignored `response_format` and added text after the JSON, which Haiku did on every call.
