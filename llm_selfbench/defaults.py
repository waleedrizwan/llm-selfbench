from __future__ import annotations

from typing import Any, Dict


def default_config() -> Dict[str, Any]:
    return {
        "run": {
            "k": 10,
            "timeout_sec": 300,
            "jitter_attempts": False,
            "continue_on_provider_check_failure": False,
        },
        "providers": [
            {
                "name": "claude-code",
                "type": "claude_code",
                "enabled": True,
                "command": "claude",
                "model": None,
                "timeout_sec": 300,
                "safe_mode": False,
                "disable_slash_commands": True,
                "no_session_persistence": True,
                "max_turns": 1,
                "disallow_tools": True,
                "disallowed_tools": "*",
                "extra_args": [],
                "env": {},
            },
            {
                "name": "codex-cli",
                "type": "codex_cli",
                "enabled": True,
                "command": "codex",
                "model": None,
                "timeout_sec": 300,
                "sandbox": "read-only",
                "ephemeral": True,
                "skip_git_repo_check": True,
                "ignore_rules": False,
                "ignore_user_config": False,
                "extra_args": [],
                "env": {},
            },
            {
                "name": "openrouter-example-disabled",
                "type": "openrouter",
                "enabled": False,
                "model": "openai/gpt-5.2",
                "api_key_env": "OPENROUTER_API_KEY",
                "temperature": 0.7,
                "max_tokens": 512,
                "timeout_sec": 300,
                "headers": {
                    "HTTP-Referer": "http://localhost",
                    "X-OpenRouter-Title": "llm-selfbench",
                },
            },
            {
                "name": "mock-good",
                "type": "mock",
                "enabled": False,
            },
        ],
    }


def default_benchmark() -> Dict[str, Any]:
    return {
        "name": "starter-reasoning-benchmark",
        "description": "A small exact-answer benchmark for testing the runner. Replace or expand this file for real comparisons.",
        "defaults": {
            "system": "You are being benchmarked. Do not use external tools or browse. Solve independently. Return only one line in the format: FINAL: <answer>",
            "prompt_template": "Question: {{question}}\n\nReturn only: FINAL: <answer>",
            "extract_regex": "(?im)^\\s*FINAL(?:\\s+ANSWER)?\\s*[:\\-]\\s*(.+?)\\s*$",
        },
        "tests": [
            {
                "id": "arithmetic-17x23",
                "question": "What is 17 * 23?",
                "answer": "391",
                "grader": {"type": "exact", "answer": "391"},
            },
            {
                "id": "capital-canada",
                "question": "What is the capital city of Canada?",
                "answer": "Ottawa",
                "grader": {"type": "exact", "answer": "Ottawa"},
            },
            {
                "id": "letters-strawberry",
                "question": "How many times does the letter r appear in the word strawberry?",
                "answer": "3",
                "grader": {"type": "number", "answer": 3},
            },
            {
                "id": "logic-marble",
                "question": "A box has 3 red marbles and 2 blue marbles. You add 4 blue marbles. How many blue marbles are now in the box?",
                "answer": "6",
                "grader": {"type": "number", "answer": 6},
            },
            {
                "id": "sequence-next",
                "question": "What is the next number in this sequence: 2, 4, 8, 16, ?",
                "answer": "32",
                "grader": {"type": "number", "answer": 32},
            },
        ],
    }
