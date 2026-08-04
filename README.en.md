# All in Luna

[简体中文（默认）](README.md) | [English](README.en.md)

**Give All in Luna an outcome and you get more than a longer chat: you get a real Codex team at work. An independent Coordinator resolves dependencies, multiple top-level Owners progress in the sidebar, and the work is integrated, recovered, and accepted.**

If this is your first look, remember three things: it is for finishing complete work; Chinese is the default entry; you do not need to learn the schemas before you start.

## Understand it in 30 seconds

All in Luna is a Codex orchestration layer for complete delivery. It turns “what should be built” into an executable workflow and keeps planning, resources, ownership, worktrees, failure recovery, integration, and acceptance on one real path.

You get:

- a Sponsor conversation that keeps direction and authorization human-owned;
- an independent primary Coordinator for dependencies, resources, dispatch, and recovery;
- multiple visible top-level Owners in the sidebar, each with a clear scope and verification result;
- CounterPilot, Integration, and Acceptance when the work calls for them;
- a complete result, not “a demo now and the rest later.”

All in Luna does not create a Goal by default. Goals, pushes, publication, and external writes require explicit authorization; a resource mode changes allocation and speed, never the completion standard.

## How it differs from a single agent / ordinary GSD

| Experience | Single agent or ordinary GSD | All in Luna |
| --- | --- | --- |
| Who moves the work | One thread reads, thinks, writes, and waits | Sponsor keeps direction; Coordinator schedules; multiple Owners execute |
| Concurrency | Usually serial, or hidden inside one response | Independent Owners are sidebar-visible top-level Codex tasks and run concurrently when dependencies allow |
| Context and files | One context and workspace; boundaries can blur | Each Owner has a self-contained brief, ownership, and worktree; shared files have an explicit owner |
| After failure | Return to the chat and explain the work again | Persist state, recover unfinished Owners, and return behavioral defects to the original Owner |
| Definition of done | Stop when the plan or first slice passes | Finish the authorized scope, then perform required integration and acceptance |

## Default execution topology (important): Sponsor → Coordinator → multiple Owners

You are the Sponsor in the current conversation: you describe the outcome, add facts, and make choices that require a person. Once execution starts, All in Luna creates an independent Coordinator; the Coordinator turns real product work into sidebar-visible top-level tasks. The Sponsor does not become the Coordinator, and ordinary Owner implementation does not get stuffed back into this chat.

```text
You / Sponsor
└─ All in Luna Coordinator (independent; dependencies and recovery)
   ├─ CounterPilot (optional, independent read-only challenge)
   ├─ Owner: backend / data
   ├─ Owner: frontend / interaction
   ├─ Owner: tests / documentation
   ├─ Integration (mechanical boundaries, when needed)
   └─ Acceptance (independent checks, risk-adaptive)
```

This is what you should expect in the sidebar: not one “manager thread” claiming to have done everything, but a Coordinator, CounterPilot, and named top-level Owners with their own progress, commits, worktrees, and evidence. An Owner with unmet dependencies waits; unrelated Owners keep moving.

## Three fastest ways in

### 1. You only have an idea

Describe the outcome; you do not need to write a plan first. All in Luna receives the context and turns the idea into a complete plan.

```text
Use All in Luna to turn this idea into a deliverable product:
[describe the goal, users, constraints, and what “done” means]
Preserve my direction, produce an executable plan, and after approval use an independent Coordinator to dispatch sidebar-visible top-level Owners through acceptance.
```

### 2. You have a plan: give a path or paste it

You can provide a `.md`, `.txt`, `.json`, `.yaml`, or repository path, or paste the plan in full. For an already-complete plan, use `parallel-only`: normalize dependencies, ownership, resources, and recovery without redesigning product direction.

```text
Use All in Luna parallel-only to execute this existing plan:
Plan path: C:\path\to\plan.md
Preserve its direction and completion standard; have an independent Coordinator dispatch conflict-free sidebar top-level Owners and continue until the plan is complete.
```

### 3. You only need concurrent takeover

When the plan is approved and direction is frozen, say “take over execution” and skip another design round.

```text
Take over concurrent execution only; do not rewrite product direction:
[paste the approved plan or provide its path]
Use parallel-only; preserve dependencies, ownership, stop boundaries, and recovery constraints, and complete every Owner, integration step, and required check.
```

## Spark: a lightweight, mechanical, tightly bounded execution resource

Added with this version, Spark (`gpt-5.3-codex-spark`) is intended for mechanical documentation, formatting, clearly bounded small fixes, scanning and classification, targeted tests, deterministic migrations, and boilerplate. It is a way to finish low-ambiguity work quickly while keeping complete delivery with the right Owner.

Spark is not the Coordinator, CounterPilot, scientific authority, architecture integrator, or independent acceptance role. It does not own global dependency decomposition, scientific judgment, cross-Owner semantic integration, or the final completion decision. Actual availability depends on runtime discovery; when unavailable, follow the resource profile’s real fallback or pause policy rather than presenting a requested model as one that ran.

## CounterPilot: an optional independent second view

CounterPilot is an independent, read-only challenger for scope, assumptions, dependencies, recovery claims, or important milestones. It can report a reproducible issue with evidence, but it cannot edit product files, raise authority, or make a human decision for you; an implementation defect goes back to the original Owner.

| Mode | Use it when | What you see |
| --- | --- | --- |
| `off` | Low-risk, very clear small changes | No CounterPilot is created |
| `risk-triggered` | Recommended default | One challenge at high-risk, failure, or boundary changes |
| `milestone` | You want checkpoints | Challenges at plan formation, before integration, or milestones |
| `continuous` | Long-running or uncertain work | Ongoing independent, read-only evidence checks |

## Resource modes: model, reasoning, and concurrency you can see

The table describes request policy, not invented fixed model names: `tier:frontier`, `tier:standard`, `tier:fast`, and `family:luna` are resolved by the current Codex host into available models. Run state records requested and actual values separately; host telemetry that is not exposed is recorded as unavailable. Effective concurrency is also bounded by the host, machine capacity, dependency width, file ownership, and budget.

| Mode | Model request visible to you | Reasoning emphasis | Target concurrency | Best fit |
| --- | --- | --- | ---: | --- |
| `balanced` | Planning/challenge `frontier`; Coordinator/Owner `standard`; mechanical workers `fast` | Planning high, challenge xhigh, Owners high | 8 | Default balance of quality, speed, and cost |
| `economy` | `family:luna` preferred for every role | Coordinator/worker medium; other roles high/max by responsibility | 4 | Small team or tight resources without shrinking scope |
| `speed` | Planning/challenge `frontier`; Coordinator `standard`; workers `fast` | Coordinator medium; main execution high | 12 | Clear dependencies where wait time matters most |
| `fast` | Planning/challenge `frontier`; Coordinator/Owner `standard`; workers `fast` | Planning/challenge xhigh; Coordinator/Owners high | 24 | Many independent Owners with hierarchical scheduling |
| `ultra-fast` | Planning/coordination/challenge `frontier`; Owners `standard`; workers `fast` | Planning/challenge ultra; integration xhigh; execution high | 48 | Many conflict-free tasks when host and machine allow it |
| `all-luna` | Hard-lock every role to `family:luna` | Usually high; CounterPilot max | 8 | Keep every role in the Luna family |
| `mad-luna` | Hard-lock every role to `family:luna` | Max for every role; independent high-risk review | 24 | Maximum safe Luna-only concurrency without exceeding host limits |
| `custom` | User-specified model/family, fallback, and role assignments | User-specified | 1–64 | Exact model, budget, or organizational policy |

`premium` is also available: target concurrency 12, with `frontier` and max reasoning prioritized for planning, authority, and acceptance. Resource modes change allocation and speed; they never turn complete execution into an MVP.

## First use: what you will see

The first run is outcome-first; you do not need to learn the internal protocol. Your Sponsor conversation keeps direction, an independent Coordinator appears in the sidebar, and then two or more named top-level Owners appear. Repeating a refresh or tick reconciles known dispatches with `no-op`, `reuse`, or `wait`; it does not create the same Owner twice.

You then see each Owner’s real thread receipt, host/worktree/repository identity, and monitor cursor before the `mechanical-only` integration boundary. Without a real Codex App receipt, the result is explicitly BLOCKED/UNVERIFIED; a CI fixture is never presented as real success.

### Resource confirmation card

The run keeps three values separate: `requested` (the tool/capability you asked for), `resolved` (what the host resolved), and `actual` (what the host receipt proves was used). If the host does not expose telemetry, it must say `unavailable`; a request is not evidence of actual use.

| Field | Evidence to expect |
| --- | --- |
| `thread` / `host` | Distinct Sponsor, Coordinator, and Owner identities |
| `worktree` / `repo` | Real isolation and repository identity in each Owner receipt |
| `duplicate` | `no-op` for a duplicate tick, `reuse` for completed work, `wait` for pending work |
| `monitor` / `integration` | Cursor and receipts, with integration explicitly mechanical-only |

### Shortest copyable prompt

```text
Use All in Luna to implement this outcome completely:
[outcome, users, constraints, and definition of done]
First receive the context I already supplied, then create an independent Coordinator and multiple sidebar top-level Owners; continue through real thread receipts, monitoring, integration, and acceptance. Do not create a Goal, push, or publish unless I explicitly authorize it.
```

### One successful run and one recovery

A successful run shows Coordinator → multiple top-level Owners → no duplicate after a repeated tick → a real receipt for every Owner → monitor cursor → mechanical-only integration. If an Owner reports `product_failure`, recovery returns to the original dispatch identity; host/tool unavailability and checker errors stop separately as BLOCKED or CHECKER_ERROR with missing evidence reported.

See [`docs/first-use-protocol.md`](docs/first-use-protocol.md) for the advanced protocol, schema, and read-only checker. CI may run fixture success/recovery, but `FIXTURE_PASS` never equals `REAL_PASS`.

## Shortest install and first use

### Install directly from this repository

In Codex Plugins, choose installation from a local path and select the repository root. Its `.agents/plugins/marketplace.json` lists both `allinluna` and `research-routes`; to install only All in Luna, select `plugins/allinluna/` directly.

### Build a portable local distribution

```powershell
python scripts/build_distributions.py --output dist
python scripts/validate_distributions.py
python scripts/validate_installations.py
```

Then install `dist/all-in-luna` as a local Codex plugin source. For a first run, copy this:

```text
Use All in Luna to implement this outcome completely:
[your outcome or plan]
Chinese is the default entry; first receive the context I already supplied, then create an independent Coordinator and any needed CounterPilot. Put independent work in multiple sidebar top-level Owners and continue through integration and acceptance. Do not create a Goal, push, or publish unless I explicitly authorize it.
```

Coordinator dispatch appears in the sidebar as trackable Owner tasks. You can see each Owner’s status, worktree, commit, and verification; if a status update is delayed, the recorded task/worktree identity is reconciled before recovery, so polling delays do not create the same Owner or worktree twice.

## Two small examples: input to complete result

### Small software project

```text
Use All in Luna to build a small todo service: Python + FastAPI, add/complete/delete actions, SQLite persistence, a minimal web page, API tests, and a README.
First produce the complete dependency plan; after approval split backend, page, and tests/docs across Owners, preserve worktree boundaries, run tests, and deliver a runnable result.
```

Expected sidebar: Coordinator → `Owner: API + SQLite`, `Owner: Web UI`, `Owner: tests + README` → Integration → Acceptance. The result should start, test, and hand off cleanly—not be three unrelated code fragments.

### Research route

```text
Use Research Routes to compare sparse retrieval and knowledge-graph retrieval for this question:
Preserve Claims, Evidence, unknowns, contradictory results, and failure regimes; do not select a route prematurely. Design only one reversible next probe.
If we later choose product implementation, hand a bounded evidence package to All in Luna.
```

Expected sidebar: Research Routes route Owners compare routes A/B and their evidence in parallel, with a read-only CounterPilot challenge; the result is a route-neutral terrain map and reversible probe, not AI-invented experiment authorization or a canonical conclusion.

## What you should see after the first run starts

- You remain in control of the Sponsor conversation; lack of a Goal does not remove top-level task orchestration.
- An independent Coordinator appears in the sidebar with dependency, resource, recovery, and completion state.
- Every substantive work surface has a top-level Owner; bounded subagents may exist inside an Owner, but they are not separate completion evidence.
- While the plan remains executable, conflict-free ready work keeps dispatching; failures leave evidence and return to the correct Owner.
- Before completion, the run can answer who changed what, in which worktree, with which checks, and what remains unknown.

## Frequently asked questions

**I do not have a plan. Can I start anyway?** Yes. Give the idea, outcome, constraints, and definition of done, then start with `$allinluna-plan`.

**I already have a complete plan. Will it be redesigned?** No. Say `parallel-only`; All in Luna normalizes dependencies, ownership, resources, recovery, and dispatch without reopening product direction.

**Does concurrency 48 always mean 48 tasks run at once?** No. 48 is the `ultra-fast` target; host capacity, machine capacity, dependency width, and file conflicts determine effective concurrency.

**Can delayed Coordinator dispatch create the same Owner twice?** No. Dispatch identity and worktree are recorded and reconciled; a delay changes when progress appears, not whether a duplicate Owner is created.

**Can Owners overwrite each other?** Independent Owners use exclusive paths and worktrees. Shared files belong to Integration or an explicit shared Owner; conflicts are not resolved by silently choosing one lane.

**Can CounterPilot veto my decision?** It is read-only and evidence-bearing. Product direction, authority boundaries, and irreversible choices remain yours.

**Will All in Luna push, publish, or create a Goal automatically?** No. Push, publication, external writes, and Goals require explicit authorization.

**How are Research Routes and All in Luna related?** Research Routes handles multi-route evidence and reversible exploration; All in Luna handles authorized complete software delivery. A research map is not an implementation plan.

## Advanced documentation and distribution notes

Most users can stop above. For precise control, continue with:

- Intake and routing: [`allinluna-intake`](plugins/allinluna/skills/allinluna-intake/SKILL.md);
- Plan contract: [`allinluna-plan`](plugins/allinluna/skills/allinluna-plan/SKILL.md);
- Execution, recovery, Owners, and acceptance: [`allinluna-run`](plugins/allinluna/skills/allinluna-run/SKILL.md);
- Authoritative resource profiles: [`resource-profiles.json`](plugins/allinluna/skills/allinluna-run/assets/resource-profiles.json);
- Dual-distribution contract: [`distribution-manifest.json`](distributions/distribution-manifest.json);
- Research Routes entry points and boundaries: [`plugins/research-routes/skills/`](plugins/research-routes/skills/).

## License

Apache License 2.0. See [`LICENSE`](LICENSE).
