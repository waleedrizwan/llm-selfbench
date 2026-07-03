# llm-selfbench

`llm-selfbench` is a small, dependency-free Python CLI for benchmarking:

- local **Claude Code CLI** via `claude -p`
- local **Codex CLI** via `codex exec`
- any **OpenRouter** model slug via the OpenRouter Chat Completions API

It uses **external self-consistency**: for every provider and every benchmark item, it runs `k` independent attempts, extracts the final answer, votes on the most common answer, and scores the result.

## What it reports

For each provider, the report includes:

- `pass@1`: whether the first attempt was correct
- `majority@k`: whether the most common extracted answer across `k` attempts was correct
- `any@k`: whether at least one attempt was correct
- `attempt acc`: correctness across all individual attempts
- `stability`: top vote count divided by valid attempts
- error rate
- average latency per attempt
- OpenRouter token usage, when returned by the API

`any@k` is useful but optimistic: it assumes you have an oracle that can identify the correct attempt. `majority@k` is the normal self-consistency score.

## Install

From this folder:

```bash
python3 -m pip install -e .
```

Then verify:

```bash
llm-selfbench --help
```

You can also run without installing:

```bash
python3 -m llm_selfbench --help
```

## Initialize a benchmark folder

```bash
llm-selfbench init --dir llm-bench
cd llm-bench
```

This creates:

```text
config.json
benchmark.json
```

## Check providers

```bash
llm-selfbench check --config config.json
```

For OpenRouter:

```bash
export OPENROUTER_API_KEY=sk-or-v1-...
llm-selfbench check --config config.json --openrouter-model anthropic/claude-sonnet-4
```

List OpenRouter models:

```bash
llm-selfbench list-openrouter --search claude --limit 20
llm-selfbench list-openrouter --search gpt --limit 20
```

## Run Claude Code CLI and Codex CLI

```bash
llm-selfbench run \
  --config config.json \
  --bench benchmark.json \
  --k 10
```

Outputs go to:

```text
runs/<benchmark-name>-<timestamp>/
  report.md
  summary.csv
  summary.json
  attempts.jsonl
  config_used.json
  benchmark_used.json
  provider_checks.json
```

## Run Claude Code CLI, Codex CLI, and one OpenRouter model

```bash
export OPENROUTER_API_KEY=sk-or-v1-...

llm-selfbench run \
  --config config.json \
  --bench benchmark.json \
  --k 10 \
  --openrouter-model anthropic/claude-sonnet-4
```

You can add multiple OpenRouter models:

```bash
llm-selfbench run \
  --config config.json \
  --bench benchmark.json \
  --k 10 \
  --openrouter-model anthropic/claude-sonnet-4 \
  --openrouter-model openai/gpt-5.2 \
  --openrouter-model google/gemini-3.1-pro
```

## Run only selected providers

```bash
llm-selfbench run --providers claude-code,codex-cli --k 10
```

```bash
llm-selfbench run --providers openrouter --openrouter-model openai/gpt-5.2 --k 10
```

## Benchmark file format

A benchmark is a JSON file with `defaults` and `tests`.

```json
{
  "name": "my-benchmark",
  "defaults": {
    "system": "You are being benchmarked. Return only one line: FINAL: <answer>",
    "prompt_template": "Question: {{question}}\n\nReturn only: FINAL: <answer>",
    "extract_regex": "(?im)^\\s*FINAL(?:\\s+ANSWER)?\\s*[:\\-]\\s*(.+?)\\s*$"
  },
  "tests": [
    {
      "id": "arithmetic-1",
      "question": "What is 17 * 23?",
      "grader": {"type": "exact", "answer": "391"}
    },
    {
      "id": "capital-1",
      "question": "What is the capital of Canada?",
      "grader": {"type": "exact", "answer": "Ottawa"}
    }
  ]
}
```

Supported graders:

```json
{"type": "exact", "answer": "Ottawa"}
{"type": "one_of", "answers": ["USA", "United States", "United States of America"]}
{"type": "contains", "value": "Ottawa"}
{"type": "icontains", "value": "ottawa"}
{"type": "regex", "pattern": "\\bOttawa\\b"}
{"type": "number", "answer": 3, "abs_tol": 0}
{"type": "none"}
```

For best results, make the model output a machine-extractable answer such as:

```text
FINAL: 391
```

## Provider config

`config.json` controls providers and defaults.

Claude Code CLI provider:

```json
{
  "name": "claude-code",
  "type": "claude_code",
  "enabled": true,
  "command": "claude",
  "model": null,
  "safe_mode": false,
  "disable_slash_commands": true,
  "no_session_persistence": true,
  "max_turns": 1,
  "disallow_tools": true,
  "disallowed_tools": "*"
}
```

Codex CLI provider:

```json
{
  "name": "codex-cli",
  "type": "codex_cli",
  "enabled": true,
  "command": "codex",
  "model": null,
  "sandbox": "read-only",
  "ephemeral": true,
  "skip_git_repo_check": true
}
```

OpenRouter provider:

```json
{
  "name": "openrouter-sonnet",
  "type": "openrouter",
  "enabled": true,
  "model": "anthropic/claude-sonnet-4",
  "api_key_env": "OPENROUTER_API_KEY",
  "temperature": 0.7,
  "max_tokens": 512,
  "headers": {
    "HTTP-Referer": "http://localhost",
    "X-OpenRouter-Title": "llm-selfbench"
  }
}
```

## Self-consistency details

For each `(provider, test)` pair, the tool:

1. renders the prompt
2. calls the provider `k` times
3. extracts the answer from each raw output
4. grades each attempt
5. computes the most common normalized answer
6. grades the majority answer
7. writes raw attempt logs and summary files

By default, prompts are identical across attempts. Use this only when you want to measure natural sampling variability. If your CLI/model appears deterministic, you can use:

```bash
llm-selfbench run --k 10 --jitter-attempts
```

That appends an attempt marker to the prompt to encourage independent samples. This can help with deterministic wrappers, but it also changes the prompt slightly, so compare models with the same setting.

## Safety notes

The default configuration is intended for pure Q&A benchmarks:

- Claude Code is called in print mode with tools disallowed.
- Codex is called in non-interactive mode with `read-only` sandboxing and an isolated temporary workspace.
- OpenRouter uses the stateless chat completions endpoint.

For coding-agent benchmarks where tools edit files and run tests, create a separate benchmark harness around temporary git worktrees. This starter tool intentionally avoids letting agents modify your repository during simple Q&A scoring.

## Example quick run with the mock provider

To validate the scoring pipeline without using any paid model, edit `config.json` and enable `mock-good`, or run only it after enabling it:

```json
{"name": "mock-good", "type": "mock", "enabled": true}
```

Then:

```bash
llm-selfbench run --providers mock-good --k 3
```
