from __future__ import annotations

import csv
import random
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from .grading import extract_answer, grade_answer
from .providers import Provider, ProviderResult, build_provider
from .util import append_jsonl, mean, percent, render_template, slugify, utc_now_iso, write_json


def _test_question(test: Mapping[str, Any]) -> str:
    return str(test.get("prompt") or test.get("question") or test.get("input") or "")


def _render_prompt(bench: Mapping[str, Any], test: Mapping[str, Any], attempt_index: int, k: int, jitter_attempts: bool) -> Tuple[str, str]:
    defaults = bench.get("defaults", {}) if isinstance(bench.get("defaults"), Mapping) else {}
    template = str(test.get("prompt_template") or defaults.get("prompt_template") or "{{question}}")
    system = str(test.get("system") if test.get("system") is not None else defaults.get("system", ""))

    data: Dict[str, Any] = {}
    if isinstance(test.get("vars"), Mapping):
        data.update(test["vars"])
    data.update(dict(test))
    data.setdefault("question", _test_question(test))
    data.setdefault("prompt", _test_question(test))
    data["attempt"] = attempt_index
    data["k"] = k
    user_prompt = render_template(template, data)
    if jitter_attempts:
        user_prompt += f"\n\nBenchmark attempt {attempt_index} of {k}. Solve independently; do not rely on prior attempts."
    return system, user_prompt


def _choose_majority(attempts: List[Dict[str, Any]]) -> Dict[str, Any]:
    valid = [a for a in attempts if not a.get("error") and a.get("normalized")]
    if not valid:
        return {"answer": "", "normalized": "", "count": 0, "valid_attempts": 0, "tie": False}
    counts = Counter(a["normalized"] for a in valid)
    max_count = max(counts.values())
    tied = {ans for ans, cnt in counts.items() if cnt == max_count}
    winner_norm = None
    # Stable tie-break: first answer that reached the top count / earliest occurrence.
    for a in valid:
        if a["normalized"] in tied:
            winner_norm = a["normalized"]
            break
    winner_answer = next(a["extracted"] for a in valid if a["normalized"] == winner_norm)
    return {
        "answer": winner_answer,
        "normalized": winner_norm or "",
        "count": max_count,
        "valid_attempts": len(valid),
        "tie": len(tied) > 1,
        "vote_distribution": dict(counts.most_common()),
    }


def _safe_bool_score(values: Iterable[Optional[bool]]) -> Optional[float]:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return sum(1 for v in vals if v) / len(vals)


def _summarize_provider(provider_name: str, per_test_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    rows = [r for r in per_test_rows if r["provider"] == provider_name]
    attempts = [a for r in rows for a in r["attempts"]]
    graded_attempts = [a for a in attempts if a.get("correct") is not None]
    usage_prompt = 0
    usage_completion = 0
    usage_total = 0
    usage_seen = False
    for a in attempts:
        usage = a.get("usage") or {}
        for key in ("prompt_tokens", "promptTokens"):
            if usage.get(key) is not None:
                usage_prompt += int(usage.get(key) or 0)
                usage_seen = True
                break
        for key in ("completion_tokens", "completionTokens"):
            if usage.get(key) is not None:
                usage_completion += int(usage.get(key) or 0)
                usage_seen = True
                break
        for key in ("total_tokens", "totalTokens"):
            if usage.get(key) is not None:
                usage_total += int(usage.get(key) or 0)
                usage_seen = True
                break
    if usage_total == 0 and (usage_prompt or usage_completion):
        usage_total = usage_prompt + usage_completion

    return {
        "provider": provider_name,
        "tests": len(rows),
        "attempts": len(attempts),
        "errors": sum(1 for a in attempts if a.get("error")),
        "error_rate": (sum(1 for a in attempts if a.get("error")) / len(attempts)) if attempts else None,
        "pass_at_1": _safe_bool_score(r.get("first_correct") for r in rows),
        "majority_at_k": _safe_bool_score(r.get("majority_correct") for r in rows),
        "any_at_k": _safe_bool_score(r.get("any_correct") for r in rows),
        "attempt_accuracy": _safe_bool_score(a.get("correct") for a in graded_attempts),
        "mean_stability": mean(r.get("stability") for r in rows),
        "avg_latency_attempt_sec": mean(a.get("latency_sec") for a in attempts),
        "total_latency_sec": sum(float(a.get("latency_sec") or 0.0) for a in attempts),
        "usage": {
            "prompt_tokens": usage_prompt if usage_seen else None,
            "completion_tokens": usage_completion if usage_seen else None,
            "total_tokens": usage_total if usage_seen else None,
        },
    }


def _format_score_table(summary_rows: List[Dict[str, Any]], k: int) -> str:
    headers = ["provider", "pass@1", f"majority@{k}", f"any@{k}", "attempt acc", "stability", "errors", "avg sec"]
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for r in summary_rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(r["provider"]),
                    percent(r.get("pass_at_1")),
                    percent(r.get("majority_at_k")),
                    percent(r.get("any_at_k")),
                    percent(r.get("attempt_accuracy")),
                    percent(r.get("mean_stability")),
                    f"{r.get('errors', 0)}/{r.get('attempts', 0)}",
                    f"{(r.get('avg_latency_attempt_sec') or 0):.2f}",
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def run_benchmark(
    *,
    bench: Mapping[str, Any],
    config: Mapping[str, Any],
    out_dir: Path,
    k_override: Optional[int] = None,
    provider_filters: Optional[List[str]] = None,
    openrouter_models: Optional[List[str]] = None,
    timeout_override: Optional[int] = None,
    jitter_attempts_override: Optional[bool] = None,
    shuffle_tests: bool = False,
    continue_on_provider_check_failure: Optional[bool] = None,
    quiet: bool = False,
) -> Dict[str, Any]:
    run_cfg = config.get("run", {}) if isinstance(config.get("run"), Mapping) else {}
    k = int(k_override or run_cfg.get("k", 10))
    timeout_sec = int(timeout_override or run_cfg.get("timeout_sec", 300))
    jitter_attempts = bool(run_cfg.get("jitter_attempts", False) if jitter_attempts_override is None else jitter_attempts_override)
    continue_on_fail = bool(run_cfg.get("continue_on_provider_check_failure", False) if continue_on_provider_check_failure is None else continue_on_provider_check_failure)

    providers_cfg: List[Dict[str, Any]] = [dict(p) for p in config.get("providers", [])]
    for model in dict.fromkeys(openrouter_models or []):  # de-duplicate, preserve order
        providers_cfg.append(
            {
                "name": f"openrouter:{model}",
                "type": "openrouter",
                "enabled": True,
                "model": model,
                "api_key_env": "OPENROUTER_API_KEY",
                "temperature": 0.7,
                "max_tokens": 512,
                "timeout_sec": timeout_sec,
                "headers": {
                    "HTTP-Referer": "http://localhost",
                    "X-OpenRouter-Title": "llm-selfbench",
                },
            }
        )

    filters = [f.lower().strip() for f in (provider_filters or []) if f.strip()]

    def selected(cfg: Mapping[str, Any]) -> bool:
        name = str(cfg.get("name", "")).lower()
        ptype = str(cfg.get("type", "")).lower()
        enabled = bool(cfg.get("enabled", True))
        if filters:
            for f in filters:
                if f == name:
                    return True  # exact name is an explicit opt-in; overrides enabled=false
                if (f == ptype or f in name) and enabled:
                    return True  # a type/substring match must not resurrect a disabled provider
            return False
        return enabled

    selected_cfgs: List[Dict[str, Any]] = []
    seen_names: set = set()
    for cfg in providers_cfg:
        if not selected(cfg):
            continue
        pname = str(cfg.get("name") or cfg.get("type") or "provider")
        if pname in seen_names:
            continue  # duplicate names would double-count the per-provider summary
        seen_names.add(pname)
        selected_cfgs.append(cfg)

    providers: List[Provider] = [build_provider(cfg) for cfg in selected_cfgs]
    if not providers:
        raise ValueError("No providers selected. Enable providers in config or pass --openrouter-model / --providers.")

    out_dir.mkdir(parents=True, exist_ok=True)
    attempts_path = out_dir / "attempts.jsonl"
    if attempts_path.exists():
        attempts_path.unlink()

    provider_checks = [p.check() for p in providers]
    bad = [c for c in provider_checks if not c.get("ok")]
    if bad and not continue_on_fail:
        write_json(out_dir / "provider_checks.json", provider_checks)
        bad_text = "; ".join(f"{b['name']}: {b['details']}" for b in bad)
        raise RuntimeError(f"Provider check failed: {bad_text}. Details written to {out_dir / 'provider_checks.json'}")

    tests: List[Dict[str, Any]] = [dict(t) for t in bench.get("tests", [])]
    if shuffle_tests:
        random.shuffle(tests)
    if not tests:
        raise ValueError("Benchmark contains no tests.")

    write_json(out_dir / "config_used.json", dict(config))
    write_json(out_dir / "benchmark_used.json", dict(bench))
    write_json(out_dir / "provider_checks.json", provider_checks)

    defaults = bench.get("defaults", {}) if isinstance(bench.get("defaults"), Mapping) else {}
    per_test_rows: List[Dict[str, Any]] = []
    started_at = utc_now_iso()
    total_work = len(providers) * len(tests) * k
    done = 0

    for provider in providers:
        if not quiet:
            print(f"\n== {provider.name} ==")
        for test_index, test in enumerate(tests, start=1):
            test_id = str(test.get("id") or f"test-{test_index}")
            if not quiet:
                print(f"  {test_id}: ", end="", flush=True)
            attempts: List[Dict[str, Any]] = []
            for attempt_index in range(1, k + 1):
                system, user_prompt = _render_prompt(bench, test, attempt_index, k, jitter_attempts)
                try:
                    result = provider.generate(system, user_prompt, {"test": test, "attempt": attempt_index, "k": k, "timeout_sec": timeout_sec})
                except Exception as exc:  # noqa: BLE001 - preserve failed attempt instead of aborting the whole run
                    result = ProviderResult(raw_output="", latency_sec=0.0, error=f"provider invocation failed: {exc}")
                raw = result.raw_output or ""
                attempt_error = result.error
                if attempt_error:
                    # Infrastructure failure (timeout, non-zero exit, HTTP error):
                    # record it, but grade as "no data" (None) so it drops out of
                    # pass@1 / attempt-accuracy rather than counting as a wrong
                    # answer. It is still reflected in error_rate via the error field.
                    extracted_info = {"answer": "", "normalized": "", "method": "none"}
                    grade = {"correct": None, "grade_type": "error", "error": attempt_error}
                else:
                    try:
                        extracted_info = extract_answer(raw, test, defaults)
                        grade = grade_answer(extracted_info["answer"], raw, test)
                    except Exception as exc:  # noqa: BLE001 - a grader bug must not abort the whole run
                        attempt_error = f"grading failed: {exc}"
                        extracted_info = {"answer": "", "normalized": "", "method": "none"}
                        grade = {"correct": None, "grade_type": "error", "error": attempt_error}
                attempt_row: Dict[str, Any] = {
                    "run_started_at": started_at,
                    "provider": provider.name,
                    "provider_type": provider.type,
                    "test_id": test_id,
                    "attempt": attempt_index,
                    "k": k,
                    "question": _test_question(test),
                    "raw_output": raw,
                    "extracted": extracted_info["answer"],
                    "normalized": extracted_info["normalized"],
                    "extract_method": extracted_info["method"],
                    "correct": grade.get("correct"),
                    "grade": grade,
                    "latency_sec": result.latency_sec,
                    "error": attempt_error,
                    "returncode": result.returncode,
                    "stderr": result.stderr,
                    "usage": result.usage,
                    "meta": result.meta or {},
                }
                attempts.append(attempt_row)
                append_jsonl(attempts_path, attempt_row)
                done += 1
                if not quiet:
                    symbol = "E" if attempt_error else ("✓" if grade.get("correct") is True else ("×" if grade.get("correct") is False else "."))
                    print(symbol, end="", flush=True)
            majority = _choose_majority(attempts)
            if majority["answer"]:
                # Grade the majority against a representative winning attempt's full
                # raw output so target:"raw" graders behave like per-attempt grading.
                winner_raw = next(
                    (a["raw_output"] for a in attempts
                     if not a.get("error") and a.get("normalized") == majority["normalized"]),
                    majority["answer"],
                )
                majority_grade = grade_answer(majority["answer"], winner_raw, test)
            else:
                majority_grade = {"correct": None, "grade_type": "majority", "error": "no valid majority answer"}
            first_correct = attempts[0].get("correct") if attempts else None
            any_correct = any(a.get("correct") is True for a in attempts)
            graded = [a for a in attempts if a.get("correct") is not None]
            correct_count = sum(1 for a in graded if a.get("correct") is True)
            stability = (majority["count"] / majority["valid_attempts"]) if majority["valid_attempts"] else None
            row = {
                "provider": provider.name,
                "provider_type": provider.type,
                "test_id": test_id,
                "question": _test_question(test),
                "k": k,
                "first_answer": attempts[0].get("extracted") if attempts else "",
                "first_correct": first_correct,
                "majority_answer": majority["answer"],
                "majority_normalized": majority["normalized"],
                "majority_count": majority["count"],
                "majority_correct": majority_grade.get("correct"),
                "majority_grade": majority_grade,
                "any_correct": any_correct if graded else None,
                "attempt_accuracy": (correct_count / len(graded)) if graded else None,
                "valid_attempts": majority["valid_attempts"],
                "stability": stability,
                "tie": majority.get("tie", False),
                "vote_distribution": majority.get("vote_distribution", {}),
                "errors": sum(1 for a in attempts if a.get("error")),
                "avg_latency_attempt_sec": mean(a.get("latency_sec") for a in attempts),
                "total_latency_sec": sum(float(a.get("latency_sec") or 0.0) for a in attempts),
                "attempts": attempts,
            }
            per_test_rows.append(row)
            if not quiet:
                maj = "✓" if row["majority_correct"] is True else ("×" if row["majority_correct"] is False else ".")
                print(f"  majority={maj} stability={percent(row['stability'])}")

    provider_names = [p.name for p in providers]
    summary_rows = [_summarize_provider(name, per_test_rows) for name in provider_names]
    finished_at = utc_now_iso()
    summary = {
        "benchmark": bench.get("name"),
        "started_at": started_at,
        "finished_at": finished_at,
        "k": k,
        "tests": len(tests),
        "providers": provider_names,
        "provider_checks": provider_checks,
        "summary_by_provider": summary_rows,
        "per_test": [
            {k2: v for k2, v in row.items() if k2 != "attempts"}
            for row in per_test_rows
        ],
        "artifacts": {
            "attempts_jsonl": str(attempts_path),
            "summary_json": str(out_dir / "summary.json"),
            "summary_csv": str(out_dir / "summary.csv"),
            "report_md": str(out_dir / "report.md"),
        },
    }

    write_json(out_dir / "summary.json", summary)
    with (out_dir / "summary.csv").open("w", encoding="utf-8", newline="") as f:
        fieldnames = [
            "provider",
            "tests",
            "attempts",
            "pass_at_1",
            "majority_at_k",
            "any_at_k",
            "attempt_accuracy",
            "mean_stability",
            "errors",
            "error_rate",
            "avg_latency_attempt_sec",
            "total_latency_sec",
            "total_tokens",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in summary_rows:
            writer.writerow({
                **{k2: r.get(k2) for k2 in fieldnames},
                "total_tokens": (r.get("usage") or {}).get("total_tokens"),
            })

    report_lines = [
        f"# LLM SelfBench Report",
        "",
        f"Benchmark: `{bench.get('name')}`",
        f"Started: `{started_at}`",
        f"Self-consistency passes per test: `{k}`",
        "",
        "## Provider scores",
        "",
        _format_score_table(summary_rows, k),
        "",
        "## Per-test results",
        "",
        "| provider | test | first | majority | any | stability | majority answer |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in per_test_rows:
        report_lines.append(
            "| "
            + " | ".join(
                [
                    str(row["provider"]),
                    str(row["test_id"]),
                    "✓" if row["first_correct"] is True else ("×" if row["first_correct"] is False else "."),
                    "✓" if row["majority_correct"] is True else ("×" if row["majority_correct"] is False else "."),
                    "✓" if row["any_correct"] is True else ("×" if row["any_correct"] is False else "."),
                    percent(row["stability"]),
                    str(row["majority_answer"]).replace("|", "\\|"),
                ]
            )
            + " |"
        )
    report_lines.extend([
        "",
        "## Metric definitions",
        "",
        "- `pass@1`: whether the first attempt was correct.",
        f"- `majority@{k}`: whether the most common extracted answer across `{k}` attempts was correct.",
        f"- `any@{k}`: whether at least one of `{k}` attempts was correct. This is optimistic unless you have an oracle to pick the right answer.",
        "- `stability`: top vote count divided by valid attempts. Higher means the model gave the same answer more often.",
        "- `attempt acc`: correctness across every individual attempt.",
        "",
        "Full raw outputs are in `attempts.jsonl`.",
    ])
    (out_dir / "report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    return summary
