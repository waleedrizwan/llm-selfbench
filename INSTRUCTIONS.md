# Running `llm-selfbench`

This folder contains the full source code and run instructions for `llm-selfbench`, a small Python CLI that benchmarks:

- Claude Code CLI through `claude -p`
- Codex CLI through `codex exec`
- OpenRouter models through the OpenRouter chat completions endpoint
- a built-in `mock-good` provider for smoke testing without spending model credits

The benchmark uses **external self-consistency**: it sends the same benchmark item to each provider `k` separate times, extracts the answer from each attempt, and then reports `pass@1`, `majority@k`, `any@k`, individual attempt accuracy, answer stability, error rate, latency, and OpenRouter token usage when available.

## 1. Prerequisites

You need Python 3.9+.

For local Claude Code benchmarking, install and log in to Claude Code so this command works in your terminal:

```bash
claude -p "Say FINAL: ok" --output-format text
```

For local Codex benchmarking, install and log in to Codex so this command works in your terminal:

```bash
codex exec --skip-git-repo-check --sandbox read-only "Say FINAL: ok"
```

For OpenRouter benchmarking, create an OpenRouter API key and set it as an environment variable:

```bash
export OPENROUTER_API_KEY=sk-or-v1-...
```

PowerShell equivalent:

```powershell
$env:OPENROUTER_API_KEY = "sk-or-v1-..."
```

## 2. Install the tool

From the unzipped folder:

```bash
cd llm-selfbench
python3 -m pip install -e .
```

Windows PowerShell:

```powershell
cd llm-selfbench
py -m pip install -e .
```

Verify:

```bash
llm-selfbench --help
```

You can also run without installing:

```bash
python3 -m llm_selfbench --help
```

## 3. Create a benchmark working folder

```bash
llm-selfbench init --dir llm-bench
cd llm-bench
```

This creates:

```text
config.json
benchmark.json
```

`config.json` controls providers. `benchmark.json` controls benchmark questions, expected answers, extraction, and grading.

## 4. Smoke test the scoring pipeline without paid calls

Edit `config.json` and set only `mock-good` to enabled:

```json
{
  "name": "mock-good",
  "type": "mock",
  "enabled": true
}
```

Or temporarily run only the mock provider after enabling it:

```bash
llm-selfbench run --providers mock-good --k 3
```

You should see every starter benchmark item pass.

## 5. Check your real providers

```bash
llm-selfbench check --config config.json
```

Check only Claude and Codex:

```bash
llm-selfbench check --config config.json --providers claude-code,codex-cli
```

Check an OpenRouter model:

```bash
llm-selfbench check \
  --config config.json \
  --openrouter-model anthropic/claude-sonnet-4
```

## 6. Run Claude Code and Codex with 10-pass self-consistency

```bash
llm-selfbench run \
  --config config.json \
  --bench benchmark.json \
  --providers claude-code,codex-cli \
  --k 10
```

The default config disables Claude tools and runs Codex in a read-only sandbox so the starter benchmark behaves like a pure Q&A benchmark.

## 7. Run Claude Code, Codex, and OpenRouter together

```bash
export OPENROUTER_API_KEY=sk-or-v1-...

llm-selfbench run \
  --config config.json \
  --bench benchmark.json \
  --providers claude-code,codex-cli \
  --k 10 \
  --openrouter-model anthropic/claude-sonnet-4
```

You can pass multiple OpenRouter models:

```bash
llm-selfbench run \
  --config config.json \
  --bench benchmark.json \
  --providers claude-code,codex-cli \
  --k 10 \
  --openrouter-model anthropic/claude-sonnet-4 \
  --openrouter-model openai/gpt-5.2 \
  --openrouter-model google/gemini-3.1-pro
```

To run only OpenRouter:

```bash
llm-selfbench run \
  --providers openrouter \
  --openrouter-model openai/gpt-5.2 \
  --k 10
```

## 8. List OpenRouter models

```bash
llm-selfbench list-openrouter --search claude --limit 20
llm-selfbench list-openrouter --search gpt --limit 20
llm-selfbench list-openrouter --search gemini --limit 20
```

## 9. Read results

Each run creates a folder like:

```text
runs/starter-reasoning-benchmark-20260702-123456/
  report.md
  summary.csv
  summary.json
  attempts.jsonl
  config_used.json
  benchmark_used.json
  provider_checks.json
```

Open `report.md` first. Use `attempts.jsonl` when you want to inspect every raw answer and extraction.

## 10. Customize questions

Edit `benchmark.json`. A minimal exact-answer item looks like this:

```json
{
  "id": "capital-canada",
  "question": "What is the capital city of Canada?",
  "grader": {"type": "exact", "answer": "Ottawa"}
}
```

Useful graders:

```json
{"type": "exact", "answer": "Ottawa"}
{"type": "one_of", "answers": ["USA", "United States", "United States of America"]}
{"type": "contains", "value": "Ottawa"}
{"type": "icontains", "value": "ottawa"}
{"type": "regex", "pattern": "\\bOttawa\\b"}
{"type": "number", "answer": 3, "abs_tol": 0}
{"type": "none"}
```

The default prompt asks models to return:

```text
FINAL: <answer>
```

That makes extraction and majority voting much more reliable.

## 11. Self-consistency options

Set `k` to the number of attempts per provider per question:

```bash
llm-selfbench run --k 10
llm-selfbench run --k 20
```

By default, every attempt uses the same prompt. To add an attempt marker to encourage independent samples:

```bash
llm-selfbench run --k 10 --jitter-attempts
```

Use the same `--jitter-attempts` setting for every model if you want a fair comparison.

## 12. Common troubleshooting

If provider checks fail:

```bash
which claude
which codex
claude --version
codex --version
```

If OpenRouter fails, verify:

```bash
echo $OPENROUTER_API_KEY
llm-selfbench list-openrouter --search claude --limit 5
```

If runs are slow or expensive, lower `k` or reduce the number of tests. Total calls are:

```text
number of providers × number of tests × k
```

For example, 3 providers × 50 tests × 10 attempts = 1,500 model calls.
