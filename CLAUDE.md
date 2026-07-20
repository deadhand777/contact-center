# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`contact-center` is a Python 3.13+ agentic contact-center PoC (Milestones 1-4 done:
knowledge agent + gateway-backed balance lookups with supervisor and structured escalation
+ Amazon Connect chat front door via Lex pipe + deterministic eval harness and PII-safe
correlation logging):
a Strands knowledge agent on Bedrock AgentCore Runtime (eu-central-1), grounded by
a Bedrock Knowledge Base over synthetic bank docs, driven by a `contact-center chat`
CLI harness (`contact_center:main` in `src/contact_center/_internal/cli.py`).
Public API (`__init__.py` `__all__`): `get_parser`, `main`. The repo was
scaffolded from the [`pawamoy/copier-uv`](https://github.com/pawamoy/copier-uv)
template. Regenerating scaffolding happens via Copier (`.copier-answers.yml`); don't
hand-edit generated tool config to fight the template.

## Architecture

- `src/contact_center/` — CLI harness (`chat` subcommand, SSM config in `_internal/aws.py`)
- `contactcenter/` — @aws/agentcore CLI scaffold; agent supervisor and entrypoint in `app/knowledge_agent/{main.py,knowledge.py,banking.py,shared.py}`; pure logic in `contract.py` and `retrieval.py` tested from `tests/` by file path (deployed via `agentcore deploy`)
- `infra/` — hand-written TypeScript CDK; `KnowledgeStack` (KB, S3 Vectors, Guardrail, IAM, SSM; `infra/lambda/balance/` is the balance Lambda);
  `ConnectStack` (Connect instance, Lex V2 pipe bot with FallbackIntent code hook = bridge Lambda in `infra/lambda/bridge/`, escalation queue, loop-free contact flow in `infra/lib/flows/`);
  separate from the agentcore-generated CDK under `contactcenter/agentcore/cdk/` — never merge them
- `docs/corpus/` — synthetic bank docs the KB ingests; integration tests pin facts from them

# Hard Rules

- Never add a runtime dependency without approval.
- Never use relative imports in `src/` — always absolute (`contact_center...`).
- Never store credentials or read `.env` files for secrets.
- Always keep `__init__.py` `__all__` in sync with what tests import from the package.
- Always run `python scripts/make check` before considering work done.

# Preferences

- Google-style docstrings on public functions/classes.
- Concise naming; composition over inheritance.

# Anti-Patterns

- Never refactor or reformat code unrelated to the task.
- Never add speculative abstractions, flexibility, or config nobody asked for.
- Never add type hints, comments, or docstrings to code you did not change.
- Never add error handling for cases that cannot happen; validate at boundaries only.
- Never create new files when editing an existing one suffices.
- **Don't use grep/Read loops for code exploration** — call `codegraph_explore` MCP first; only fall back to `grep`/`Read` if codegraph returns nothing useful.

# Behavioral Guidelines

- **Think before coding.** State assumptions out loud. If the request is
  ambiguous, ask. If a simpler approach exists, push back. When confused, stop
  and name what is unclear instead of guessing.
- **Simplicity first.** Write the minimum code that solves the problem. The
  test: would a senior engineer call this overcomplicated?
- **Surgical changes.** Touch only what the task requires. Every changed line
  traces back to the request.
- **Goal-driven execution.** Turn vague instructions into verifiable targets
  before writing code. "Add validation" becomes "write tests for invalid
  inputs, then make them pass."

# Success Criteria

Good implementations:
- pass `python scripts/make check` (quality/ruff, types/ty, docs)
- keep diffs minimal and scoped to the request
- are easy to scan and predictable

# Gotchas

- pytest does NOT auto-discover `config/pytest.ini` from the repo root: direct runs need
  `uv run pytest -c config/pytest.ini`. Never create a root `pytest.ini` (rejected as drift-prone).
- Live integration tests are double-gated: `-m integration` AND `RUN_INTEGRATION=1`.
- If `python scripts/make ...` fails with "Failed to spawn: duty", prefix `PYTHON_VERSIONS=""`.
- `contactcenter/` is outside ruff scope (duties.py PY_SRC_PATHS) and excluded from ty
  (`[tool.ty.src]` in pyproject.toml + config/ty.toml) — its deps live in the agent's own venv.
- `uv.lock` is git-ignored (template choice) — don't try to commit it.
- AWS deploys: sandbox profile/account only; region eu-central-1; SSM params under `/contact-center/*`.
- Gateway MCP tools are namespaced `<target>___<tool>` (e.g., `balance___get_account_balance`); `toolSchemaFile` in `agentcore.json` resolves relative to the CLI working directory (`contactcenter/`), not the `agentcore.json` location.
- The Lambda ARN inlined in `contactcenter/agentcore/agentcore.json` embeds the CloudFormation logical-ID suffix — recreating the `KnowledgeStack` strands the gateway target; re-inline the new ARN after any stack recreation.
- Agent prompts are deliberately German and form a contract: golden integration tests
  assert German number formats ("4,90", "2.543,17"), citation markers `[Quelle: ...]` are
  emitted by retrieval.py (code + test, not prompt), and escalation `reason` literals
  ("Kundenwunsch", "Systemfehler Kontodienst", ...) are routing tokens for M3 Connect.
  Don't translate or reword any of these casually.
- Synthetic customers: KND-1001 (Girokonto + Tagesgeld), KND-1002 (negative balance),
  KND-1003 (single account); all other ids → UNKNOWN_CUSTOMER. Data lives in
  infra/lambda/balance/handler.py.
- Connect invokes Lex with the contact's default locale (en_US) — the pipe bot must have
  BOTH de_DE and en_US locales built and enabled on the alias, each with the code hook;
  Lex needs ≥1 custom intent with an utterance per locale to build (the `Noop` intent).
- Lex dialog code hooks allow 30 s (the old 8 s Connect flow-Lambda cap is gone); `CfnBotVersion`
  snapshots need a logical-id rotation (V2→V3...) to repoint the alias when the bot changes.
- Chat harness: Connect only starts the flow after the websocket subscribes (websocket-client dep);
  messages sent before the Lex block arms are dropped — the harness settle-drains before sending.
  `StartChatContact` needs the bare flow id, not the ARN.
- The golden eval set lives at `docs/eval/golden.json` (facts pinned from `docs/corpus/` and
  `infra/lambda/balance/handler.py`); the eval scorer (`eval_runner.py`) is deterministic;
  guardrail `refusal_markers` are calibrated to the live agent/guardrail wording. Bridge
  (`infra/lambda/bridge/handler.py`) and agent (`contactcenter/app/knowledge_agent/main.py`)
  emit PII-safe, `session_id`-keyed structured logs (no answer text) for per-conversation
  tracing in CloudWatch.

# Commands

Tasks run via `duty` through `scripts/make`. With `direnv allow` you can use
`make`; otherwise use `python scripts/make <task>`.

```bash
python scripts/make setup                        # create venv + install deps
python scripts/make test                         # full suite (parallel, with coverage)
python scripts/make test -- tests/test_cli.py::test_main  # single test
python scripts/make format                       # ruff auto-fix + format
python scripts/make check-quality                # ruff lint
python scripts/make check-types                  # ty type-check
python scripts/make check                        # all checks (quality + types + docs)
python scripts/make coverage                     # combine and report coverage
python scripts/make docs                         # serve docs at localhost:8000

cd infra && npm test                             # CDK template tests (Jest)
cd contactcenter && agentcore deploy -y          # deploy agent (needs AWS_PROFILE)
AWS_PROFILE=<profile> RUN_INTEGRATION=1 uv run pytest -c config/pytest.ini -o addopts= \
  -m integration --no-cov tests/test_integration.py   # live integration suite
AWS_PROFILE=<profile> RUN_EVAL=1 uv run contact-center eval   # scored golden-set eval
  # against the deployed runtime; gated, never run as part of make check
```

Tool configs live in `config/`: `ruff.toml`, `ty.toml`, `pytest.ini`, `coverage.ini`.
