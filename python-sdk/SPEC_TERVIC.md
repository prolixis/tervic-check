# Tervic SDK — Complete Build Specification & Prompt

> **How to use this file:** Create an empty repo `tervic`, put this file in the root as `SPEC_TERVIC.md`, open Claude Code in that repo and say:
> **"Read SPEC_TERVIC.md fully and build everything in it. Do not skip any section, do not leave TODOs, and confirm each item in the Definition of Done at the end."**

---

## 0. Instructions to the builder (read first)

You are building **Tervic v1.0**: a production-ready, open-source Python SDK + CLI. This is a real product, not a prototype.

Rules:
1. Implement **every** section. No placeholders, no `TODO`, no mocked logic in production paths.
2. **Python 3.10+**, fully typed (`mypy --strict` passes), packaged with `pyproject.toml` (hatchling), managed with `uv`.
3. Minimal dependencies in the core: `pydantic>=2`, `typer`, `rich`. Everything else (LangGraph, OpenAI Agents SDK, CrewAI, OpenTelemetry, LLM providers) is an **optional extra** and imported lazily. `import tervic` must work with only the core installed and must take < 150 ms.
4. Before writing any adapter, **read the current official docs** of that framework (LangGraph callbacks/streaming, OpenAI Agents SDK tracing processors, CrewAI event listeners) and use the current public API. Write what you verified in `docs/DECISIONS.md`.
5. Write all code independently. Tervic is a different product from other tools in this space; do not copy code from any other project.
6. Tervic must **never break the user's agent**. Every recorder/detector call is wrapped so an internal Tervic error is logged once and swallowed. The agent's behaviour, return values and exceptions must be identical with and without Tervic.
7. When something is ambiguous, choose the option that is safer for user data and simpler for the user, and record it in `docs/DECISIONS.md`.

---

## 1. Product: the one problem Tervic solves

**Tervic tells you which step of your AI agent silently broke, and stops it from shipping.**

AI agents fail *silently*: the run finishes with no exception, but one step returned an empty dict, dropped a field, swallowed a tool error, got truncated, looped, or hallucinated a tool. The final answer is wrong and nobody knows where it went wrong.

Tervic:
1. **Records** every step of an agent run (inputs, outputs, tool calls, LLM calls, state changes).
2. **Detects** silent failures with deterministic checks (no LLM needed).
3. **Finds the root cause**: traces a bad value back to the step that first produced it.
4. **Gates** CI: `tervic check` exits 1 when a run is bad.
5. **Helps fix**: replays from the broken step without re-paying for earlier LLM calls, and writes a ready-to-paste fix prompt for Claude Code / Cursor.

Everything else in this spec exists to serve those five things.

Positioning vs existing tools (for README):
- **Framework-agnostic**: LangGraph, OpenAI Agents SDK, CrewAI and plain Python, all with the same detectors.
- **Works in CI and in production**: same SDK, with sampling and OpenTelemetry export in prod.
- **Built for coding agents**: ships an MCP server so Claude Code / Cursor can ask Tervic directly what broke.
- **Local-first and free**: data stays in `.tervic/` in your repo. No account needed.

---

## 2. Architecture

```
User's agent code
   │  tervic.attach(graph) / @tervic.step / adapters
   ▼
Recorder ──► Event bus (in-process, non-blocking queue)
   │                      │
   │                      ├─► Local store: .tervic/runs/<run_id>/  (JSONL + SQLite index)
   │                      └─► Exporters (optional): OpenTelemetry, webhook
   ▼
Analyzer (after run end, or on demand)
   ├─ Detectors (deterministic rules)
   ├─ Contracts (user-declared field rules)
   ├─ Lineage + root cause
   ├─ Baseline diff
   └─ Optional LLM judge (user's own key)
   ▼
Report ──► CLI (rich) / HTML report / JUnit XML / SARIF / Markdown PR comment / MCP server
```

---

## 3. Repository structure (create exactly this)

```
tervic/
├── pyproject.toml
├── README.md
├── LICENSE                      # Apache-2.0
├── src/tervic/
│   ├── __init__.py              # public API: attach, step, run, check, Contract, config
│   ├── _version.py
│   ├── config.py                # tervic.toml + env vars + code config
│   ├── core/
│   │   ├── events.py            # pydantic event models (section 5)
│   │   ├── run.py               # Run context, run_id, contextvars
│   │   ├── recorder.py          # thread/async-safe recorder, event queue
│   │   ├── serialize.py         # safe serialization of arbitrary objects
│   │   ├── redact.py            # secret/PII redaction
│   │   └── safety.py            # "never break the agent" wrappers
│   ├── store/
│   │   ├── local.py             # .tervic/ JSONL + SQLite index
│   │   └── cassette.py          # recorded LLM/tool responses for replay
│   ├── adapters/
│   │   ├── decorator.py         # @tervic.step, tervic.run() for plain Python
│   │   ├── langgraph.py
│   │   ├── openai_agents.py
│   │   ├── crewai.py
│   │   └── llm_clients.py       # wraps openai / anthropic / google-genai clients
│   ├── analyze/
│   │   ├── engine.py            # runs detectors, builds Findings
│   │   ├── detectors/           # one file per detector family (section 7)
│   │   ├── contracts.py
│   │   ├── lineage.py           # field provenance + root cause
│   │   ├── baseline.py          # golden-run diff
│   │   └── judge.py             # optional LLM judge
│   ├── replay/
│   │   └── replayer.py
│   ├── fix/
│   │   └── prompt.py            # fix prompt generator
│   ├── report/
│   │   ├── console.py           # rich output
│   │   ├── html.py              # single self-contained HTML file
│   │   ├── junit.py
│   │   ├── sarif.py
│   │   └── markdown.py          # PR comment
│   ├── export/
│   │   ├── otel.py              # OpenTelemetry GenAI semantic conventions
│   │   └── webhook.py
│   ├── mcp/
│   │   └── server.py            # `tervic mcp` (stdio)
│   ├── pytest_plugin.py         # `pytest --tervic`
│   └── cli.py                   # typer app
├── action/action.yml            # GitHub Action
├── examples/
│   ├── langgraph_rag/           # deliberately broken + fixed versions
│   ├── openai_agents_support/
│   ├── crewai_research/
│   └── plain_python_pipeline/
├── tests/
│   ├── unit/
│   ├── adapters/
│   ├── e2e/
│   └── fixtures/
└── docs/
    ├── QUICKSTART.md
    ├── DETECTORS.md             # every detector, what it catches, how to silence it
    ├── CONTRACTS.md
    ├── CI.md
    ├── PRODUCTION.md
    ├── MCP.md
    ├── PRIVACY.md
    └── DECISIONS.md
```

---

## 4. Public API (the developer experience)

Getting started must be **two lines** for each framework.

```python
import tervic

# LangGraph
app = tervic.attach(graph.compile())          # returns the same object, instrumented

# OpenAI Agents SDK
tervic.attach_openai_agents()                  # registers a trace processor

# CrewAI
tervic.attach_crewai()                         # registers an event listener

# Plain Python
@tervic.step                                   # or @tervic.step(name="retrieve", kind="retriever")
def retrieve(state): ...

with tervic.run("support-agent", tags={"env": "ci"}) as run:
    result = pipeline(input)
print(run.verdict)                             # "pass" | "warn" | "fail"
```

Also:
- `tervic.attach_llm(client)` wraps an `openai`, `anthropic` or `google-genai` client to record LLM calls (model, tokens, cost, latency, finish reason) without changing return values.
- `tervic.check(run_id=None) -> Report` runs analysis programmatically.
- `tervic.configure(mode="ci"|"dev"|"prod", sample_rate=1.0, redact=True, store_dir=".tervic", fail_on="error")`.
- Async functions, generators, threads and asyncio tasks are all supported (use `contextvars` for run/step context).

---

## 5. Event model (`core/events.py`)

All events are pydantic models, serialized as one JSON line each.

- `RunStarted { run_id (ULID), name, framework, tags, git: {sha, branch, dirty}, tervic_version, python_version, started_at }`
- `StepStarted { run_id, step_id, parent_step_id, name, kind: node|tool|llm|retriever|agent|handoff|custom, attempt, input_snapshot, started_at }`
- `StepEnded { run_id, step_id, output_snapshot, state_diff, error: {type, message, traceback}|null, duration_ms, ended_at }`
- `LLMCall { run_id, step_id, provider, model, input_messages (redacted), output, finish_reason, input_tokens, output_tokens, cost_usd, latency_ms, tool_calls }`
- `ToolCall { run_id, step_id, tool_name, args, result, error, available_tools }`
- `RunEnded { run_id, status: completed|crashed, final_output_snapshot, duration_ms, total_cost_usd }`

Snapshots (`serialize.py`):
- Safely serialize any object: pydantic models, dataclasses, dicts, LangChain messages, numpy arrays (shape/dtype only), bytes (length + sha256), unknown objects → `repr` truncated.
- Limits: depth 8, 200 keys per dict, 200 items per list, strings truncated at 4,000 chars (keep length + sha256 of the full value so emptiness and changes can still be detected).
- `state_diff`: added / removed / changed keys between step input state and output state (for graph frameworks).

---

## 6. Storage (`store/local.py`)

- Layout: `.tervic/runs/<run_id>/events.jsonl`, `.tervic/runs/<run_id>/report.json`, `.tervic/index.sqlite` (run list: id, name, verdict, started_at, duration, cost, git sha, tags).
- `.tervic/` gets its own `.gitignore` with `*` on first creation, except `.tervic/baselines/` and `tervic.toml`, which users may commit.
- Retention: keep last 200 runs by default (`retention_runs` config). `tervic gc` cleans up.
- Writes go through a background thread with a bounded queue (10,000 events). If the queue is full in `prod` mode, drop events and count drops; in `ci`/`dev` mode, block briefly (max 50 ms) then drop. Never block the agent longer.
- Flush on run end and at interpreter exit (`atexit`).

---

## 7. Detectors (the heart of the product)

Each detector: `id`, `name`, `severity` (`error`/`warn`/`info`), `description`, `how_to_fix`, configurable thresholds, and can be disabled globally or per step. All deterministic and offline. Each produces `Finding { detector_id, severity, step_id, step_name, field_path, message, evidence, root_cause_step_id }`.

### 7.1 Output integrity
| id | Catches |
|---|---|
| `TV001 empty-output` | Step returned `None`, `{}`, `[]`, `""` or whitespace when it normally returns content |
| `TV002 dropped-field` | A state field present before the step is missing after it, and is read later |
| `TV003 null-field` | A field written as `None`/empty that a later step reads |
| `TV004 never-written` | A field read by a step but never written by any earlier step |
| `TV005 type-drift` | A field changes type (e.g. list → str) between steps or vs baseline |
| `TV006 schema-violation` | Output fails a declared pydantic/JSON schema contract |

### 7.2 Swallowed errors
| id | Catches |
|---|---|
| `TV101 error-in-output` | Output contains error text (`Traceback`, `Exception:`, `Error:`, HTTP 4xx/5xx bodies, `{"error": ...}`) but no exception was raised |
| `TV102 tool-error-ignored` | A tool returned an error and the next step continued as if it succeeded |
| `TV103 caught-parse-failure` | JSON/structured-output parsing failed and fell back to a default |
| `TV104 retry-exhausted-silent` | Max retries reached but the run continued |

### 7.3 LLM behaviour
| id | Catches |
|---|---|
| `TV201 truncated-output` | `finish_reason` = length / max_tokens |
| `TV202 refusal` | Model refused ("I can't help with that", provider refusal fields) |
| `TV203 hallucinated-tool` | Model called a tool name that was not available |
| `TV204 invalid-tool-args` | Tool args failed the tool's schema |
| `TV205 empty-completion` | Model returned no text and no tool calls |

### 7.4 Retrieval / RAG
| id | Catches |
|---|---|
| `TV301 empty-retrieval` | Retriever returned 0 documents |
| `TV302 retrieval-unused` | Retrieved docs never appear in any later LLM input |

### 7.5 Control flow & budgets
| id | Catches |
|---|---|
| `TV401 loop` | Same step (or same tool with same args) repeated more than N times (default 5) |
| `TV402 dead-end` | Run ended with no final output |
| `TV403 cost-budget` | Run cost over `budget.cost_usd` |
| `TV404 latency-budget` | Run or step over `budget.latency_ms` |
| `TV405 token-budget` | Tokens over `budget.tokens` |

### 7.6 Safety
| id | Catches |
|---|---|
| `TV501 secret-leak` | Secrets (API keys, private keys, JWTs, connection strings) appear in an LLM input or final output |
| `TV502 pii-in-output` | Emails / phone numbers / card-like numbers in final output (warn) |

Every detector must have at least one positive and one negative test.

---

## 8. Contracts (`analyze/contracts.py`)

Users can declare what each step must produce, in Python or in `tervic.toml`:

```toml
[contracts.retrieve]
writes = ["documents"]
non_empty = ["documents"]

[contracts.answer]
reads = ["documents", "question"]
writes = ["answer"]
schema = "app.schemas:Answer"     # pydantic model import path
min_length = { answer = 20 }
```

```python
tervic.contract("retrieve", writes=["documents"], non_empty=["documents"])
```

- `tervic contracts infer` reads the last N successful runs and writes a suggested contract file (which fields each step reads/writes, which are always non-empty). The user reviews it before committing.
- Contract violations become Findings with severity `error`.

---

## 9. Root cause (`analyze/lineage.py`)

- Build a field provenance graph: for every field, which step wrote each value, and which steps read it.
- For each error Finding, walk back through the provenance graph to the **earliest** step where the value first became bad (empty, wrong type, error text). That step is `root_cause_step_id`.
- Group findings that share the same root cause so the user sees **one problem, not ten symptoms**.
- If source is available, attach the root-cause step's function `file:line` (from `inspect`) to the Finding.

---

## 10. Baselines (`analyze/baseline.py`)

- `tervic baseline set <run_id> --name happy-path` stores a golden run in `.tervic/baselines/` (committable).
- `tervic diff <run_a> <run_b>` and automatic diff against the baseline of the same run name during `check`:
  - structural diff of each step's output (fields added/removed/type changed/became empty),
  - step sequence diff (steps skipped, new steps, different order),
  - cost / latency / token change (warn if > 50% worse; configurable).
- No LLM needed for diffs.

---

## 11. Optional LLM judge (`analyze/judge.py`)

- Off by default. Enabled with `tervic key set --provider openai|anthropic|gemini` (stored in OS keyring via `keyring`, fallback to env vars) and `judge.enabled = true`.
- Judges only: final answer vs question relevance, and "is this step's output reasonable given its input" for steps flagged `warn`. Returns a short reason.
- Sends redacted snapshots only. Hard cap per run on judge calls (default 10) and on cost.
- When disabled, nothing leaves the machine.

---

## 12. Replay (`replay/replayer.py`, `store/cassette.py`)

- During recording, LLM and tool responses are saved to a cassette per run.
- `tervic replay <run_id> --from <step>`: re-runs the user's agent with steps before `<step>` served from the recording (no LLM calls, no cost), and steps from `<step>` onward executed live with the user's current code.
- Works for LangGraph (resume from state snapshot) and plain Python `@tervic.step` pipelines. For OpenAI Agents SDK and CrewAI, support replay with cassette-served LLM calls (`--offline`) and document any limits in `docs/DECISIONS.md`.
- Entry point discovery: `tervic.toml` `[replay] entrypoint = "app.main:run"`.
- After replay, run `check` automatically and print a before/after comparison.

---

## 13. Fix prompt (`fix/prompt.py`)

`tervic fix last` (or `tervic fix <run_id>`) prints and copies to clipboard a prompt for a coding agent, generated offline (no LLM):

```
Tervic found a silent failure in run <id> (<run name>).

Root cause: step `retrieve` (src/agent/nodes.py:42) returned an empty `documents` list.
Effect: `answer` step generated an answer with no sources (TV301, TV302).
Evidence: input query="...", output documents=[] (0 items). Baseline run had 5 documents.

Please:
1. Open src/agent/nodes.py:42 and find why `documents` can be empty.
2. Fix the cause (do not just add a fallback that hides it).
3. If an empty result is legitimate, raise or return an explicit "no results" state the next step handles.
4. Re-run with: tervic replay <id> --from retrieve
```

Also `--format claude|cursor|plain` and `--write .tervic/fix.md`.

---

## 14. CLI (`cli.py`)

| Command | Purpose |
|---|---|
| `tervic init` | Create `tervic.toml`, add `.tervic/` gitignore, detect framework, print the 2-line snippet |
| `tervic runs` | List recent runs (verdict, name, duration, cost, git sha) |
| `tervic show [run_id\|last]` | Step tree with timings, cost, and findings inline |
| `tervic check [run_id\|last\|--all-since <sha>]` | Analyze; exit 0 pass, 1 fail, 2 Tervic internal error. `--fail-on error\|warn` |
| `tervic diff <a> <b>` | Baseline/run diff |
| `tervic baseline set\|list\|rm` | Manage baselines |
| `tervic replay <run_id> --from <step>` | Replay (section 12) |
| `tervic fix [run_id\|last]` | Fix prompt (section 13) |
| `tervic report <run_id> --format html\|junit\|sarif\|markdown\|json -o <file>` | Export report |
| `tervic contracts infer\|validate` | Contracts (section 8) |
| `tervic key set --provider` | Judge key |
| `tervic mcp` | Start MCP server (section 16) |
| `tervic gc` | Retention cleanup |
| `tervic doctor` | Check installation, adapters, permissions, store health |

Output: rich, colour, works with `NO_COLOR`, and `--json` on every command for scripting.

---

## 15. CI integration

- **pytest plugin**: `pytest --tervic` wraps each test in a `tervic.run(test_name)`, and fails the test if the run verdict is `fail`. Adds a summary section to pytest output.
- **GitHub Action** (`action/action.yml`): installs tervic, runs the user's command, runs `tervic check`, uploads SARIF to code scanning, posts/updates one Markdown PR comment with findings and root causes, uploads the HTML report as an artifact.
- **JUnit XML** for other CI systems (GitLab, Jenkins).
- Document copy-paste setups for GitHub Actions and GitLab in `docs/CI.md`.

---

## 16. MCP server (`mcp/server.py`) — for Claude Code / Cursor

`tervic mcp` starts a **stdio** MCP server using the official `mcp` Python SDK (optional extra `tervic[mcp]`). Verify against current MCP docs.

Tools (all read-only except `run_replay`):
- `list_runs(limit, verdict)` — recent runs.
- `get_findings(run_id="last")` — findings grouped by root cause, with file:line.
- `get_step(run_id, step_name)` — input/output snapshots for one step.
- `get_fix_prompt(run_id="last")` — same text as `tervic fix`.
- `diff_runs(a, b)`.
- `run_replay(run_id, from_step)` — runs replay and returns the new verdict.

Server `instructions`: "Tervic records AI agent runs in this repo. When the user reports wrong agent output, call get_findings first, then get_step for the root-cause step, fix the code, and call run_replay to verify."

Document the Claude Code command (`claude mcp add tervic -- tervic mcp`) and Cursor config in `docs/MCP.md`.

---

## 17. Production mode (`docs/PRODUCTION.md`)

- `tervic.configure(mode="prod", sample_rate=0.1)`: record a sample of runs, but **always** keep runs that have an error-severity finding or an exception (tail-based: decide at run end).
- Detectors run in the background thread after run end, never in the request path. Added latency per step < 1 ms p95 (measure and document).
- **OpenTelemetry exporter** (`tervic[otel]`): emit spans following the OTel GenAI semantic conventions, with Tervic findings as span events, so users can view them in Langfuse, Datadog, Grafana, Honeycomb etc. Verify current conventions before implementing.
- **Webhook exporter**: POST a JSON finding summary to a URL (e.g. Slack incoming webhook format option) when a prod run fails.
- Hosted dashboard is **out of scope**; define an `Exporter` protocol so a future cloud backend plugs in without SDK changes.

---

## 18. Privacy & redaction (`core/redact.py`, `docs/PRIVACY.md`)

- Redaction on by default before anything is written to disk or exported: API keys, private keys, JWTs, bearer tokens, connection strings with passwords, `password=`/`secret=` values, high-entropy tokens ≥ 32 chars. Replace with `[REDACTED:<type>:<sha256 prefix>]` so equality is still comparable.
- Optional PII redaction (`redact_pii = true`): emails, phone numbers, card-like numbers.
- User hooks: `tervic.configure(redact_fn=...)` and per-step `@tervic.step(capture_input=False)`.
- No telemetry about users is sent anywhere. State this plainly in README and PRIVACY.md.

---

## 19. Config (`tervic.toml`)

```toml
[tervic]
mode = "dev"            # dev | ci | prod
store_dir = ".tervic"
fail_on = "error"       # error | warn
retention_runs = 200

[budget]
cost_usd = 0.50
latency_ms = 60000
tokens = 200000

[detectors]
disable = []            # e.g. ["TV502"]
loop_threshold = 5

[detectors.per_step.summarize]
disable = ["TV001"]

[judge]
enabled = false
provider = "anthropic"
max_calls = 10

[replay]
entrypoint = "app.main:run"
```

Precedence: code `configure()` > env vars (`TERVIC_MODE`, `TERVIC_STORE_DIR`, ...) > `tervic.toml` > defaults.

---

## 20. HTML report (`report/html.py`)

One self-contained HTML file (no external requests): run summary + verdict, step timeline (waterfall with durations and cost), findings grouped by root cause, click a step to see input/output/state diff, baseline diff tab. Opens with `tervic show last --html`.

---

## 21. Examples (`examples/`)

Each example has `broken.py` and `fixed.py`, a README, and a test asserting that `tervic check` fails on broken and passes on fixed:
- `langgraph_rag`: retriever returns `[]` silently → TV301/TV302 with root cause on retriever.
- `openai_agents_support`: tool returns `{"error": "timeout"}` and agent continues → TV101/TV102.
- `crewai_research`: an agent loops on the same tool → TV401.
- `plain_python_pipeline`: a step drops a field → TV002 with root cause.

Use cassettes so example tests run offline with no API keys.

---

## 22. Testing (must exist and pass)

- **Unit**: every detector (positive + negative), serialization edge cases (cycles, huge objects, unserializable objects), redaction patterns (positives + placeholder negatives like `YOUR_API_KEY`), lineage/root-cause on synthetic graphs, baseline diff, config precedence, fix prompt snapshot tests.
- **Safety tests**: inject failures inside Tervic (store unwritable, serializer raising, queue full) and assert the agent's output and exceptions are unchanged.
- **Adapter tests** for LangGraph, OpenAI Agents SDK, CrewAI using recorded fixtures (no network).
- **Concurrency**: 50 concurrent asyncio runs and 8 threads, no event mix-up between runs.
- **Performance**: benchmark overhead per step (target < 1 ms p95 in prod mode, < 3 ms in dev); record results in `docs/DECISIONS.md`.
- **E2E**: CLI commands against the examples; exit codes correct; SARIF validates against the SARIF 2.1.0 schema; JUnit XML parses.
- **MCP**: start `tervic mcp`, call every tool with the MCP inspector or SDK client in a test.
- Coverage ≥ 85%. `ruff`, `mypy --strict`, `pytest` all run in GitHub Actions on Python 3.10–3.13, Linux/macOS/Windows.

---

## 23. Packaging & release

- PyPI name: `tervic` (check availability first; if taken, use `tervic-sdk` and keep the import name `tervic`, and note it in DECISIONS.md).
- Extras: `tervic[langgraph]`, `tervic[openai-agents]`, `tervic[crewai]`, `tervic[otel]`, `tervic[mcp]`, `tervic[judge]`, `tervic[all]`.
- Console script: `tervic`. Pytest entry point for the plugin.
- Release workflow: tag → build → publish via PyPI trusted publishing.
- Semantic versioning; `CHANGELOG.md`.

---

## 24. README.md must contain

1. The one-line promise (section 1) and a 15-second GIF placeholder spot.
2. Install + the 2-line quickstart per framework.
3. Example terminal output of `tervic check` showing a root cause.
4. Detector table (link to DETECTORS.md).
5. CI setup in 5 lines.
6. MCP setup for Claude Code / Cursor.
7. Privacy statement.
8. Links to all docs.

---

## 25. Definition of Done (verify every line and report the result)

- [ ] `pip install tervic` with no extras works; `import tervic` < 150 ms.
- [ ] Two-line attach works for LangGraph, OpenAI Agents SDK, CrewAI and plain Python.
- [ ] Agent behaviour identical with and without Tervic; safety tests pass.
- [ ] All events recorded per section 5 with safe serialization and redaction.
- [ ] All detectors in section 7 implemented, documented in DETECTORS.md, each with positive + negative tests.
- [ ] Contracts (Python + TOML) and `contracts infer` work.
- [ ] Root cause groups findings and points to the origin step with file:line.
- [ ] Baseline set/diff works.
- [ ] Replay from a step works without re-calling earlier LLMs (verified by cassette hit count).
- [ ] `tervic fix last` produces the prompt format in section 13.
- [ ] Every CLI command in section 14 works, with `--json`, and correct exit codes.
- [ ] pytest plugin, GitHub Action, JUnit and SARIF outputs work.
- [ ] MCP server exposes the 6 tools and works from Claude Code.
- [ ] Prod mode: sampling with always-keep-failures, background analysis, OTel + webhook exporters.
- [ ] HTML report is self-contained and opens offline.
- [ ] All 4 examples: broken fails, fixed passes, offline.
- [ ] Tests, ruff, mypy --strict pass on 3.10–3.13 across 3 OSes; coverage ≥ 85%.
- [ ] Overhead benchmark recorded in DECISIONS.md and within targets.
- [ ] README and all docs written.
