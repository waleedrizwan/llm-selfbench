from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from .util import env_with, which


@dataclass
class ProviderResult:
    raw_output: str
    latency_sec: float
    error: Optional[str] = None
    returncode: Optional[int] = None
    stderr: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None
    meta: Optional[Dict[str, Any]] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "raw_output": self.raw_output,
            "latency_sec": self.latency_sec,
            "error": self.error,
            "returncode": self.returncode,
            "stderr": self.stderr,
            "usage": self.usage,
            "meta": self.meta or {},
        }


class Provider:
    def __init__(self, cfg: Mapping[str, Any]) -> None:
        self.cfg = dict(cfg)
        self.name = str(cfg.get("name") or cfg.get("type") or "provider")
        self.type = str(cfg.get("type") or "unknown")
        self.timeout_sec = int(cfg.get("timeout_sec", 300))

    def check(self) -> Dict[str, Any]:
        return {"name": self.name, "type": self.type, "ok": True, "details": "ok"}

    def generate(self, system: str, user_prompt: str, context: Mapping[str, Any]) -> ProviderResult:
        raise NotImplementedError

    def _combined_prompt(self, system: str, user_prompt: str) -> str:
        if system and system.strip():
            return f"{system.strip()}\n\n{user_prompt.strip()}"
        return user_prompt.strip()


class ClaudeCodeProvider(Provider):
    def check(self) -> Dict[str, Any]:
        cmd = str(self.cfg.get("command", "claude"))
        path = which(cmd)
        return {
            "name": self.name,
            "type": self.type,
            "ok": path is not None,
            "details": path or f"command not found: {cmd}",
        }

    def generate(self, system: str, user_prompt: str, context: Mapping[str, Any]) -> ProviderResult:
        prompt = self._combined_prompt(system, user_prompt)
        cmd = str(self.cfg.get("command", "claude"))
        timeout = int(self.cfg.get("timeout_sec", context.get("timeout_sec", self.timeout_sec)))
        args: List[str] = [cmd, "-p"]

        if self.cfg.get("safe_mode", False):
            args.append("--safe-mode")
        if self.cfg.get("disable_slash_commands", True):
            args.append("--disable-slash-commands")

        args.extend(["--output-format", str(self.cfg.get("output_format", "text"))])
        if self.cfg.get("no_session_persistence", True):
            args.append("--no-session-persistence")
        max_turns = self.cfg.get("max_turns", 1)
        if max_turns:
            args.extend(["--max-turns", str(max_turns)])
        model = self.cfg.get("model")
        if model:
            args.extend(["--model", str(model)])
        if self.cfg.get("disallow_tools", True):
            args.extend(["--disallowedTools", str(self.cfg.get("disallowed_tools", "*"))])

        for extra in self.cfg.get("extra_args", []) or []:
            args.append(str(extra))
        # Pass the prompt on stdin instead of as a trailing positional argument.
        # The claude CLI's --disallowedTools/--allowedTools flags are variadic
        # (`<tools...>`), so a positional prompt after them is greedily consumed
        # as tool names, leaving no prompt ("Input must be provided ..."). stdin
        # sidesteps that entirely and also avoids argv length limits.

        extra_env = dict(self.cfg.get("env", {}) or {})
        if self.cfg.get("no_session_persistence", True):
            extra_env.setdefault("CLAUDE_CODE_SKIP_PROMPT_HISTORY", "1")
        extra_env.setdefault("NO_COLOR", "1")

        cwd_cfg = self.cfg.get("cwd")
        start = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="llm-selfbench-claude-") as td:
            cwd = str(cwd_cfg or td)
            try:
                proc = subprocess.run(
                    args,
                    cwd=cwd,
                    env=env_with(extra_env),
                    text=True,
                    capture_output=True,
                    input=prompt,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired as exc:
                latency = time.perf_counter() - start
                return ProviderResult(
                    raw_output=(exc.stdout or "") if isinstance(exc.stdout, str) else "",
                    latency_sec=latency,
                    error=f"claude timed out after {timeout}s",
                    stderr=(exc.stderr or "") if isinstance(exc.stderr, str) else None,
                    meta={"argv_preview": args[:8] + ["<prompt>"]},
                )
            except FileNotFoundError:
                latency = time.perf_counter() - start
                return ProviderResult(raw_output="", latency_sec=latency, error=f"command not found: {cmd}", meta={"argv_preview": args[:8] + ["<prompt>"]})
        latency = time.perf_counter() - start
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        if proc.returncode != 0:
            return ProviderResult(
                raw_output=out,
                latency_sec=latency,
                error=err or f"claude exited with code {proc.returncode}",
                returncode=proc.returncode,
                stderr=err,
                meta={"argv_preview": args[:8] + ["<prompt>"]},
            )
        return ProviderResult(raw_output=out, latency_sec=latency, returncode=proc.returncode, stderr=err, meta={"argv_preview": args[:8] + ["<prompt>"]})


class CodexCLIProvider(Provider):
    def check(self) -> Dict[str, Any]:
        cmd = str(self.cfg.get("command", "codex"))
        path = which(cmd)
        return {
            "name": self.name,
            "type": self.type,
            "ok": path is not None,
            "details": path or f"command not found: {cmd}",
        }

    def generate(self, system: str, user_prompt: str, context: Mapping[str, Any]) -> ProviderResult:
        prompt = self._combined_prompt(system, user_prompt)
        cmd = str(self.cfg.get("command", "codex"))
        timeout = int(self.cfg.get("timeout_sec", context.get("timeout_sec", self.timeout_sec)))
        start = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="llm-selfbench-codex-") as td:
            td_path = Path(td)
            output_file = td_path / "last_message.txt"
            workdir = Path(str(self.cfg.get("cwd") or (td_path / "workspace")))
            workdir.mkdir(parents=True, exist_ok=True)

            args: List[str] = [cmd, "exec"]
            if self.cfg.get("ephemeral", True):
                args.append("--ephemeral")
            args.extend(["--color", str(self.cfg.get("color", "never"))])
            args.extend(["--cd", str(workdir)])
            sandbox = self.cfg.get("sandbox", "read-only")
            if sandbox:
                args.extend(["--sandbox", str(sandbox)])
            if self.cfg.get("skip_git_repo_check", True):
                args.append("--skip-git-repo-check")
            if self.cfg.get("ignore_rules", False):
                args.append("--ignore-rules")
            if self.cfg.get("ignore_user_config", False):
                args.append("--ignore-user-config")
            model = self.cfg.get("model")
            if model:
                args.extend(["--model", str(model)])
            args.extend(["--output-last-message", str(output_file)])
            if self.cfg.get("json_events", False):
                args.append("--json")
            for extra in self.cfg.get("extra_args", []) or []:
                args.append(str(extra))
            args.append(prompt)

            try:
                proc = subprocess.run(
                    args,
                    cwd=str(workdir),
                    env=env_with(dict(self.cfg.get("env", {}) or {}, NO_COLOR="1")),
                    text=True,
                    capture_output=True,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired as exc:
                latency = time.perf_counter() - start
                return ProviderResult(
                    raw_output=(exc.stdout or "") if isinstance(exc.stdout, str) else "",
                    latency_sec=latency,
                    error=f"codex timed out after {timeout}s",
                    stderr=(exc.stderr or "") if isinstance(exc.stderr, str) else None,
                    meta={"argv_preview": args[:12] + ["<prompt>"]},
                )
            except FileNotFoundError:
                latency = time.perf_counter() - start
                return ProviderResult(raw_output="", latency_sec=latency, error=f"command not found: {cmd}", meta={"argv_preview": args[:12] + ["<prompt>"]})
            if output_file.exists():
                out = output_file.read_text(encoding="utf-8", errors="replace").strip()
            else:
                out = (proc.stdout or "").strip()
        latency = time.perf_counter() - start
        err = (proc.stderr or "").strip()
        if proc.returncode != 0:
            return ProviderResult(
                raw_output=out,
                latency_sec=latency,
                error=err or f"codex exited with code {proc.returncode}",
                returncode=proc.returncode,
                stderr=err,
                meta={"argv_preview": args[:12] + ["<prompt>"]},
            )
        return ProviderResult(raw_output=out, latency_sec=latency, returncode=proc.returncode, stderr=err, meta={"argv_preview": args[:12] + ["<prompt>"]})


class OpenRouterProvider(Provider):
    endpoint = "https://openrouter.ai/api/v1/chat/completions"

    def check(self) -> Dict[str, Any]:
        api_key_env = str(self.cfg.get("api_key_env", "OPENROUTER_API_KEY"))
        api_key = self.cfg.get("api_key") or os.environ.get(api_key_env)
        model = self.cfg.get("model")
        ok = bool(api_key and model)
        missing = []
        if not api_key:
            missing.append(f"env {api_key_env}")
        if not model:
            missing.append("model")
        return {
            "name": self.name,
            "type": self.type,
            "ok": ok,
            "details": "ok" if ok else "missing " + ", ".join(missing),
        }

    def generate(self, system: str, user_prompt: str, context: Mapping[str, Any]) -> ProviderResult:
        timeout = int(self.cfg.get("timeout_sec", context.get("timeout_sec", self.timeout_sec)))
        api_key_env = str(self.cfg.get("api_key_env", "OPENROUTER_API_KEY"))
        api_key = self.cfg.get("api_key") or os.environ.get(api_key_env)
        if not api_key:
            return ProviderResult(raw_output="", latency_sec=0.0, error=f"missing OpenRouter API key in {api_key_env}")
        model = self.cfg.get("model")
        if not model:
            return ProviderResult(raw_output="", latency_sec=0.0, error="OpenRouter provider missing model")

        messages = []
        if system and system.strip():
            messages.append({"role": "system", "content": system.strip()})
        messages.append({"role": "user", "content": user_prompt.strip()})

        body: Dict[str, Any] = {
            "model": str(model),
            "messages": messages,
            "stream": False,
        }
        for key in [
            "temperature",
            "top_p",
            "top_k",
            "min_p",
            "max_tokens",
            "max_completion_tokens",
            "frequency_penalty",
            "presence_penalty",
            "seed",
            "provider",
            "reasoning",
            "transforms",
            "models",
            "route",
        ]:
            if key in self.cfg and self.cfg[key] is not None:
                body[key] = self.cfg[key]
        extra_body = self.cfg.get("extra_body") or {}
        if isinstance(extra_body, Mapping):
            body.update(extra_body)

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        for k, v in (self.cfg.get("headers") or {}).items():
            if v is not None:
                headers[str(k)] = str(v)
        if self.cfg.get("referer"):
            headers["HTTP-Referer"] = str(self.cfg["referer"])
        if self.cfg.get("title"):
            headers["X-OpenRouter-Title"] = str(self.cfg["title"])

        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.endpoint, data=data, headers=headers, method="POST")
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                status = getattr(resp, "status", None)
        except urllib.error.HTTPError as exc:
            raw_err = exc.read().decode("utf-8", errors="replace")
            latency = time.perf_counter() - start
            return ProviderResult(raw_output="", latency_sec=latency, error=f"OpenRouter HTTP {exc.code}: {raw_err[:1000]}", returncode=exc.code)
        except Exception as exc:  # noqa: BLE001 - returned to user as provider error
            latency = time.perf_counter() - start
            return ProviderResult(raw_output="", latency_sec=latency, error=f"OpenRouter request failed: {exc}")
        latency = time.perf_counter() - start
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return ProviderResult(raw_output=raw, latency_sec=latency, error="OpenRouter returned non-JSON response", returncode=status)

        if payload.get("error"):
            return ProviderResult(raw_output="", latency_sec=latency, error=json.dumps(payload.get("error"), ensure_ascii=False), returncode=status, meta={"response": payload})

        choices = payload.get("choices") or []
        content: Any = ""
        if choices:
            msg = choices[0].get("message") or {}
            content = msg.get("content", "")
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, Mapping):
                    if "text" in part:
                        parts.append(str(part["text"]))
                    elif "content" in part:
                        parts.append(str(part["content"]))
                else:
                    parts.append(str(part))
            content = "".join(parts)
        return ProviderResult(
            raw_output=str(content).strip(),
            latency_sec=latency,
            returncode=status,
            usage=payload.get("usage"),
            meta={"id": payload.get("id"), "model": payload.get("model"), "finish_reason": choices[0].get("finish_reason") if choices else None},
        )


class MockProvider(Provider):
    def generate(self, system: str, user_prompt: str, context: Mapping[str, Any]) -> ProviderResult:
        test = context.get("test", {})
        response = test.get("mock_response")
        if response is None:
            grader = test.get("grader", {}) if isinstance(test.get("grader"), Mapping) else {}
            response = test.get("answer") or grader.get("answer") or "mock"
        return ProviderResult(raw_output=f"FINAL: {response}", latency_sec=0.001, meta={"mock": True})


PROVIDER_TYPES = {
    "claude_code": ClaudeCodeProvider,
    "claude": ClaudeCodeProvider,
    "codex_cli": CodexCLIProvider,
    "codex": CodexCLIProvider,
    "openrouter": OpenRouterProvider,
    "mock": MockProvider,
}


def build_provider(cfg: Mapping[str, Any]) -> Provider:
    ptype = str(cfg.get("type", "")).lower()
    cls = PROVIDER_TYPES.get(ptype)
    if not cls:
        raise ValueError(f"Unknown provider type: {ptype!r} for provider {cfg.get('name')!r}")
    return cls(cfg)


def list_openrouter_models(api_key: Optional[str] = None, timeout: int = 30) -> Dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request("https://openrouter.ai/api/v1/models", headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw)
