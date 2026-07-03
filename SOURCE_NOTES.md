# Source notes for provider integrations

These are the documentation sources used when packaging the tool on 2026-07-02. Check the official docs again if a CLI flag changes on your local installation.

## Claude Code CLI

The Claude provider shells out to `claude -p`, which is Claude Code print mode. The tool uses optional flags such as:

- `--output-format text`
- `--no-session-persistence`
- `--max-turns 1`
- `--disable-slash-commands`
- `--disallowedTools "*"`
- `--model <model>` when configured

Official docs:

- https://code.claude.com/docs/en/cli-reference
- https://code.claude.com/docs/en/quickstart

## Codex CLI

The Codex provider shells out to `codex exec`, Codex's non-interactive mode. The tool uses optional flags such as:

- `--ephemeral`
- `--color never`
- `--cd <temp-workspace>`
- `--sandbox read-only`
- `--skip-git-repo-check`
- `--output-last-message <file>`
- `--model <model>` when configured

Official docs:

- https://developers.openai.com/codex/cli/reference

## OpenRouter

The OpenRouter provider sends HTTP requests to:

```text
POST https://openrouter.ai/api/v1/chat/completions
```

It uses Bearer-token authentication from `OPENROUTER_API_KEY`, and the helper command `list-openrouter` reads from:

```text
GET https://openrouter.ai/api/v1/models
```

Official docs:

- https://openrouter.ai/docs/api/api-reference/chat/send-chat-completion-request
- https://openrouter.ai/docs/api/api-reference/models/get-models
- https://openrouter.ai/docs/api/reference/authentication
