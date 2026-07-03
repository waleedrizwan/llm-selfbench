from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .bench import run_benchmark
from .defaults import default_benchmark, default_config
from .providers import build_provider, list_openrouter_models
from .util import load_json, slugify, write_json


def _load_config(path: Optional[str]) -> Dict[str, Any]:
    if path:
        return load_json(path)
    default_path = Path("config.json")
    if default_path.exists():
        return load_json(default_path)
    return default_config()


def _load_benchmark(path: Optional[str]) -> Dict[str, Any]:
    if path:
        return load_json(path)
    default_path = Path("benchmark.json")
    if default_path.exists():
        return load_json(default_path)
    return default_benchmark()


def _split_csv(values: Optional[List[str]]) -> List[str]:
    out: List[str] = []
    for value in values or []:
        out.extend([part.strip() for part in value.split(",") if part.strip()])
    return out


def cmd_init(args: argparse.Namespace) -> int:
    target = Path(args.dir)
    target.mkdir(parents=True, exist_ok=True)
    config_path = target / "config.json"
    bench_path = target / "benchmark.json"
    if not args.force:
        existing = [str(p) for p in [config_path, bench_path] if p.exists()]
        if existing:
            print(f"Refusing to overwrite existing files: {', '.join(existing)}", file=sys.stderr)
            print("Use --force to overwrite.", file=sys.stderr)
            return 2
    write_json(config_path, default_config())
    write_json(bench_path, default_benchmark())
    print(f"Wrote {config_path}")
    print(f"Wrote {bench_path}")
    print("Next:")
    print(f"  cd {target}")
    print("  export OPENROUTER_API_KEY=sk-or-v1-...   # only needed for OpenRouter")
    print("  llm-selfbench check --config config.json")
    print("  llm-selfbench run --config config.json --bench benchmark.json --k 10")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    openrouter_models = _split_csv(args.openrouter_model)
    for model in openrouter_models:
        config.setdefault("providers", []).append({
            "name": f"openrouter:{model}",
            "type": "openrouter",
            "enabled": True,
            "model": model,
            "api_key_env": "OPENROUTER_API_KEY",
        })
    filters = [f.lower() for f in _split_csv(args.providers)]
    checks = []
    for cfg in config.get("providers", []):
        name = str(cfg.get("name", "")).lower()
        ptype = str(cfg.get("type", "")).lower()
        if filters:
            include = any(f in name or f == ptype or f == name for f in filters)
        else:
            include = bool(cfg.get("enabled", True))
        if include:
            try:
                checks.append(build_provider(cfg).check())
            except Exception as exc:  # noqa: BLE001
                checks.append({"name": cfg.get("name"), "type": cfg.get("type"), "ok": False, "details": str(exc)})
    if args.json:
        print(json.dumps(checks, indent=2, ensure_ascii=False))
    else:
        for c in checks:
            mark = "OK" if c.get("ok") else "FAIL"
            print(f"{mark:4} {c.get('name')} ({c.get('type')}): {c.get('details')}")
    return 0 if all(c.get("ok") for c in checks) else 1


def cmd_run(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    bench = _load_benchmark(args.bench)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    if args.out:
        out_dir = Path(args.out)
    else:
        out_dir = Path("runs") / f"{slugify(str(bench.get('name') or 'benchmark'))}-{ts}"
    try:
        summary = run_benchmark(
            bench=bench,
            config=config,
            out_dir=out_dir,
            k_override=args.k,
            provider_filters=_split_csv(args.providers),
            openrouter_models=_split_csv(args.openrouter_model),
            timeout_override=args.timeout_sec,
            jitter_attempts_override=args.jitter_attempts,
            shuffle_tests=args.shuffle_tests,
            continue_on_provider_check_failure=args.continue_on_provider_check_failure,
            quiet=args.quiet,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if not args.quiet:
        print("\nDone.")
        print(f"Report:  {out_dir / 'report.md'}")
        print(f"Summary: {out_dir / 'summary.json'}")
        print(f"CSV:     {out_dir / 'summary.csv'}")
        print(f"Raw:     {out_dir / 'attempts.jsonl'}")
    else:
        print(json.dumps({"out_dir": str(out_dir), "summary": summary.get("summary_by_provider", [])}, ensure_ascii=False))
    return 0


def cmd_list_openrouter(args: argparse.Namespace) -> int:
    api_key = os.environ.get(args.api_key_env) if args.api_key_env else None
    try:
        payload = list_openrouter_models(api_key=api_key, timeout=args.timeout_sec)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    models = payload.get("data", payload if isinstance(payload, list) else [])
    search = (args.search or "").lower()
    if search:
        models = [m for m in models if search in str(m.get("id", "")).lower() or search in str(m.get("name", "")).lower()]
    if args.json:
        print(json.dumps(models[: args.limit], indent=2, ensure_ascii=False))
    else:
        for m in models[: args.limit]:
            mid = m.get("id")
            name = m.get("name", "")
            ctx = m.get("context_length", "")
            pricing = m.get("pricing", {}) or {}
            prompt_price = pricing.get("prompt", "")
            completion_price = pricing.get("completion", "")
            print(f"{mid}\t{name}\tcontext={ctx}\tprompt={prompt_price}\tcompletion={completion_price}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="llm-selfbench",
        description="Run external self-consistency benchmarks against Claude Code CLI, Codex CLI, and OpenRouter models.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Create starter config.json and benchmark.json files.")
    p_init.add_argument("--dir", default="llm-bench", help="Directory to initialize. Default: llm-bench")
    p_init.add_argument("--force", action="store_true", help="Overwrite existing config/benchmark files.")
    p_init.set_defaults(func=cmd_init)

    p_check = sub.add_parser("check", help="Check selected providers are configured and available.")
    p_check.add_argument("--config", help="Path to config.json. Defaults to ./config.json or built-in config.")
    p_check.add_argument("--providers", action="append", help="Comma-separated provider names/types to check, e.g. claude-code,codex-cli,openrouter")
    p_check.add_argument("--openrouter-model", action="append", help="Add an OpenRouter model slug to check, e.g. anthropic/claude-sonnet-4")
    p_check.add_argument("--json", action="store_true", help="Print JSON provider check output.")
    p_check.set_defaults(func=cmd_check)

    p_run = sub.add_parser("run", help="Run a benchmark.")
    p_run.add_argument("--config", help="Path to config.json. Defaults to ./config.json or built-in config.")
    p_run.add_argument("--bench", help="Path to benchmark.json. Defaults to ./benchmark.json or built-in starter benchmark.")
    p_run.add_argument("--out", help="Output directory. Defaults to runs/<benchmark>-<timestamp>.")
    p_run.add_argument("--k", type=int, help="Self-consistency passes per provider/test. Overrides config run.k.")
    p_run.add_argument("--providers", action="append", help="Comma-separated provider names/types to run, e.g. claude-code,codex-cli")
    p_run.add_argument("--openrouter-model", action="append", help="Add an OpenRouter model slug for this run. Can be repeated or comma-separated.")
    p_run.add_argument("--timeout-sec", type=int, help="Per-attempt timeout in seconds.")
    jitter = p_run.add_mutually_exclusive_group()
    jitter.add_argument("--jitter-attempts", dest="jitter_attempts", action="store_true", default=None, help="Append an attempt marker to prompts to encourage independent samples.")
    jitter.add_argument("--no-jitter-attempts", dest="jitter_attempts", action="store_false", help="Do not alter prompts between attempts.")
    p_run.add_argument("--shuffle-tests", action="store_true", help="Shuffle test order before running.")
    p_run.add_argument("--continue-on-provider-check-failure", action="store_true", help="Run even when a provider check fails; failed attempts will be recorded.")
    p_run.add_argument("--quiet", action="store_true", help="Only print final JSON location summary.")
    p_run.set_defaults(func=cmd_run)

    p_models = sub.add_parser("list-openrouter", help="List OpenRouter model slugs.")
    p_models.add_argument("--search", help="Filter model id/name by substring.")
    p_models.add_argument("--limit", type=int, default=50, help="Max models to print. Default: 50")
    p_models.add_argument("--json", action="store_true", help="Print JSON instead of tab-separated rows.")
    p_models.add_argument("--api-key-env", default="OPENROUTER_API_KEY", help="Optional API key env var. Default: OPENROUTER_API_KEY")
    p_models.add_argument("--timeout-sec", type=int, default=30)
    p_models.set_defaults(func=cmd_list_openrouter)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
