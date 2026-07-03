from __future__ import annotations

import math
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple


def normalize_for_vote(text: Optional[str]) -> str:
    if text is None:
        return ""
    text = unicodedata.normalize("NFKC", str(text))
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z0-9_-]*\s*|\s*```$", "", text, flags=re.S)
    text = text.strip()
    # Remove common final-answer wrappers while preserving actual content.
    text = re.sub(r"(?i)^\s*(final\s*(answer)?\s*[:\-])\s*", "", text)
    text = re.sub(r"\s+", " ", text)
    text = text.strip(" \t\r\n.,;:!?`'\"")
    return text.lower()


def normalize_loose(text: Optional[str]) -> str:
    base = normalize_for_vote(text)
    # Helpful for exact-answer grading of simple facts/numbers; avoids punctuation-only mismatches.
    # Replace disallowed punctuation with a SPACE (not nothing) so "a,b,c" and
    # "a, b, c" both collapse to "a b c" instead of "abc" vs "a b c".
    loose = re.sub(r"[^\w\s.+\-/]", " ", base, flags=re.UNICODE)
    loose = re.sub(r"\s+", " ", loose).strip()
    # Don't let a symbol-only answer (e.g. "<", ">", "=") collapse to "" and thus
    # compare equal to every other symbol; fall back to the vote-normalized form.
    return loose or base


def _regex_extract(raw: str, patterns: Iterable[str]) -> Optional[str]:
    for pattern in patterns:
        try:
            m = re.search(pattern, raw, flags=re.MULTILINE | re.DOTALL)
        except re.error:
            continue
        if m:
            if m.groups():
                # Return the first non-None captured group.
                for g in m.groups():
                    if g is not None:
                        return str(g).strip()
            return m.group(0).strip()
    return None


def extract_answer(raw: str, test: Mapping[str, Any], defaults: Mapping[str, Any]) -> Dict[str, Any]:
    raw = raw or ""
    patterns: List[str] = []
    test_pattern = test.get("extract_regex") or test.get("answer_regex")
    default_pattern = defaults.get("extract_regex") or defaults.get("answer_regex")
    if isinstance(test_pattern, str):
        patterns.append(test_pattern)
    elif isinstance(test_pattern, list):
        patterns.extend([str(p) for p in test_pattern])
    if isinstance(default_pattern, str):
        patterns.append(default_pattern)
    elif isinstance(default_pattern, list):
        patterns.extend([str(p) for p in default_pattern])
    # Good default for benchmark prompts that request FINAL: <answer>.
    patterns.append(r"(?im)^\s*FINAL(?:\s+ANSWER)?\s*[:\-]\s*(.+?)\s*$")

    extracted = _regex_extract(raw, patterns)
    method = "regex" if extracted is not None else "last_nonempty_line"
    if extracted is None:
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        extracted = lines[-1] if lines else raw.strip()
    return {
        "answer": extracted.strip(),
        "normalized": normalize_for_vote(extracted),
        "method": method,
    }


def _number_from_text(text: str) -> Optional[float]:
    # Supports integers, decimals (including leading-dot like ".5"), optional
    # thousands commas, signs, and scientific notation. Returns the LAST number
    # in the text: benchmark answers place the final numeric result last, so a
    # restated question ("...giving 6 marbles", "2, 4, 8, 16, 32") would
    # otherwise be mis-read as its first (distractor) number.
    matches = re.findall(r"[-+]?(?:\d[\d,]*(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?", text or "")
    if not matches:
        return None
    try:
        return float(matches[-1].replace(",", ""))
    except ValueError:
        return None


def _default_grader(test: Mapping[str, Any]) -> Dict[str, Any]:
    if "grader" in test and isinstance(test["grader"], Mapping):
        return dict(test["grader"])
    if "answer" in test:
        return {"type": "exact", "answer": test["answer"]}
    if "answers" in test:
        return {"type": "one_of", "answers": test["answers"]}
    return {"type": "none"}


def grade_answer(extracted_answer: str, raw_output: str, test: Mapping[str, Any]) -> Dict[str, Any]:
    grader = _default_grader(test)
    gtype = str(grader.get("type", "exact")).lower()
    target_name = str(grader.get("target", "extracted")).lower()
    target = raw_output if target_name == "raw" else extracted_answer

    if gtype in {"none", "manual", "ungraded"}:
        return {"correct": None, "grade_type": gtype, "details": "ungraded"}

    if gtype == "exact":
        expected = str(grader.get("answer", ""))
        case_sensitive = bool(grader.get("case_sensitive", False))
        loose = bool(grader.get("loose", True))
        if case_sensitive:
            got = (target or "").strip()
            exp = expected.strip()
        elif loose:
            got = normalize_loose(target)
            exp = normalize_loose(expected)
        else:
            got = normalize_for_vote(target)
            exp = normalize_for_vote(expected)
        return {
            "correct": got == exp,
            "grade_type": gtype,
            "expected": expected,
            "got_normalized": got,
            "expected_normalized": exp,
        }

    if gtype == "one_of":
        answers = grader.get("answers", [])
        if not isinstance(answers, list):
            answers = [answers]
        got = normalize_loose(target)
        normalized_answers = [normalize_loose(str(a)) for a in answers]
        return {
            "correct": got in normalized_answers,
            "grade_type": gtype,
            "expected": answers,
            "got_normalized": got,
        }

    if gtype == "contains":
        expected = str(grader.get("value", grader.get("answer", "")))
        return {
            "correct": expected in (target or ""),
            "grade_type": gtype,
            "expected": expected,
        }

    if gtype == "icontains":
        expected = str(grader.get("value", grader.get("answer", ""))).lower()
        return {
            "correct": expected in (target or "").lower(),
            "grade_type": gtype,
            "expected": expected,
        }

    if gtype == "regex":
        pattern = str(grader.get("pattern", grader.get("value", "")))
        flags = re.MULTILINE | re.DOTALL
        if not grader.get("case_sensitive", False):
            flags |= re.IGNORECASE
        try:
            ok = re.search(pattern, target or "", flags=flags) is not None
        except re.error as exc:
            return {"correct": False, "grade_type": gtype, "error": f"bad regex: {exc}"}
        return {"correct": ok, "grade_type": gtype, "pattern": pattern}

    if gtype in {"number", "numeric"}:
        expected_raw = grader.get("answer", grader.get("value"))
        try:
            expected = float(str(expected_raw).replace(",", ""))
        except (TypeError, ValueError):
            return {"correct": False, "grade_type": gtype, "error": "numeric grader missing numeric answer"}
        got = _number_from_text(target)
        if got is None:
            return {"correct": False, "grade_type": gtype, "expected": expected, "got": None}
        abs_tol = float(grader.get("abs_tol", grader.get("tolerance", 0.0)))
        # Default to math.isclose's normal relative tolerance so accumulated
        # float drift (e.g. 0.1 + 0.2 printed as 0.30000000000000004) doesn't
        # fail exact-equality by surprise. Set rel_tol/abs_tol explicitly to 0
        # for strict equality.
        rel_tol = float(grader.get("rel_tol", 1e-9))
        if abs_tol < 0 or rel_tol < 0:
            return {"correct": False, "grade_type": gtype, "error": "tolerance must be non-negative"}
        ok = math.isclose(got, expected, abs_tol=abs_tol, rel_tol=rel_tol)
        return {"correct": ok, "grade_type": gtype, "expected": expected, "got": got, "abs_tol": abs_tol, "rel_tol": rel_tol}

    return {"correct": False, "grade_type": gtype, "error": f"unknown grader type: {gtype}"}
