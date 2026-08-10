# All in Luna

[简体中文](README.md)

<p align="center">
  <img src="docs/assets/brand/all-in-luna-mark.svg" width="112" alt="All in Luna mark" />
</p>

> **Stop running an entire project inside one AI conversation.**

Give All in Luna one big goal.

It turns the work into independent top-level tasks: **run what can run in parallel, wait only on real dependencies, keep each task's context separate, and bring the results back together.**

Each task can still use its own subagents, tools, Skills, or MCPs.

**Parallel across tasks. Recursive inside tasks.**

<p align="center">
  <img src="docs/assets/brand/hero-topology.svg" alt="All in Luna task topology" />
</p>

---

## Why does this exist?

Small AI coding tasks are easy.

The hard part looks more like this:

> “Refactor authentication end to end, including the backend, frontend, migration, tests, and documentation.”

At first, everything is fine.

Then the agent reads files, edits code, runs tests, starts subagents, handles failures, reads more files, and keeps pushing more execution detail back into the same conversation.

After enough turns, familiar problems appear:

- the context keeps growing;
- unrelated work starts contaminating other work;
- earlier constraints become easier to forget;
- one local blocker stalls the whole flow;
- subagent results become harder to manage;
- a new conversation has to reconstruct what really happened;
- the agent says “done,” but the outcome may not actually be complete.

**All in Luna starts from one simple idea: one conversation should not have to carry an entire project.**

<p align="center">
  <img src="docs/assets/brand/before-after.svg" alt="One giant context versus clear task lanes" />
</p>

---

## One more layer above subagents

A typical agent workflow looks like this:

```text
You
 │
 ▼
Main Agent
 ├─ subagent
 ├─ subagent
 └─ subagent
```

All in Luna adds a real **Top-level Task** layer above local workers:

```text
You
 │
 ▼
All in Luna
 │
 ├─ Top-level Task A
 │    ├─ local work
 │    └─ subagents / tools / Skills
 │
 ├─ Top-level Task B
 │    ├─ local work
 │    └─ subagents / tools / MCPs
 │
 └─ Top-level Task C
      └─ waits only when it actually depends on A
```

**A Top-level Task is not just another subagent.**

It is an independent work domain with its own goal, context, dependencies, working state, local execution process, and result boundary.

A subagent is a local worker a Task may use when that Task needs to split its own work further.

> **All in Luna does not replace subagents. It gives them a better place to live.**

---

# What you get

## 1. Real top-level tasks

A large goal can become independent work domains instead of temporary chat branches.

For example:

```text
Add billing to this app

├─ Billing backend
├─ Checkout UI
├─ Database migration
├─ Integration tests
└─ Documentation
```

Each task can move independently, wait on dependencies, produce results, and keep its own working context.

## 2. Parallel when possible

Independent work does not need to queue behind unrelated work.

```text
Billing backend       ● running
Checkout UI           ● running
Documentation         ● running
Database migration    ○ waiting for schema
Integration tests     ○ waiting for backend
```

**One blocked task doesn't freeze unrelated work.**

## 3. Separate working contexts

Backend debugging does not need to share one giant context with frontend changes, test logs, documentation, and release work.

Your main conversation should mostly see:

```text
✓ what is done
● what is running
○ what is waiting
! what needs your decision
```

File reads, terminal output, test logs, diffs, and implementation detail can stay with the Task that produced them.

**Your main conversation does not need to become the project's log file.**

## 4. Recursive local workers

A Top-level Task can still split into WorkUnits or use local subagents when it is complex on its own.

```text
Backend Task
 ├─ API
 ├─ database changes
 ├─ tests
 └─ migration checks
```

Local complexity stays local.

**Parallel across tasks. Recursive inside tasks.**

## 5. Resume instead of restarting

All in Luna persists run state, task state, dependencies, and results.

Long-running work does not have to remain attached to one ever-growing conversation.

```text
start
→ work
→ stop
→ come back
→ resume
```

Completed work does not have to be rediscovered from chat history.

## 6. Verify before “done”

An agent saying:

> “Done.”

is not the same as the task actually being complete.

All in Luna can check tests, builds, changed files, artifacts, and declared outputs before accepting a task as complete.

## 7. Bring your own workflow

All in Luna is not one fixed workflow.

Use the default delivery path for ordinary software work.

Use **GSD** inside a Task when you want a more explicit development workflow.

Connect **Research Routes** for research-oriented work.

Tasks can also use other Skills, MCPs, tools, and host capabilities.

**The Core runs complex work. It does not dictate how every task must think.**

---

# Example

Suppose you say:

> **“Refactor this application's authentication system, including backend, frontend, migration, and tests.”**

All in Luna can organize it as:

```text
Authentication refactor

├─ Task 1 — Auth backend
│    ├─ session/token logic
│    ├─ API
│    └─ backend tests
│
├─ Task 2 — Frontend auth flow
│    ├─ login
│    ├─ logout
│    └─ protected routes
│
├─ Task 3 — Migration
│    └─ waits for auth contract
│
└─ Task 4 — Integration
     └─ waits for backend + frontend
```

Task 1 and Task 2 can move at the same time.

Task 1 can still use its own subagents if needed.

Task 3 waits only for the result it actually depends on.

You do not have to follow the complete implementation history of all four tasks in one conversation.

---

# When should I use it?

All in Luna is a good fit for:

- large features;
- work spanning frontend / backend / tests / docs;
- major refactors;
- migrations;
- several outcomes that can move independently;
- long-running coding sessions;
- work where one blocker should not stop the whole project;
- tasks that need different tools, models, or workflows;
- work you want to resume instead of reconstructing from chat history.

If you are fixing one typo, explaining one function, changing one CSS rule, or doing another tiny linear task, using the current agent directly is usually faster.

**All in Luna solves the organization problem of complex work. It does not make simple work complicated.**

---

# Models & performance

<p align="center">
  <img src="docs/assets/brand/models-performance.svg" alt="Models and performance routing" />
</p>

## You do not have to configure anything

Most users do not need to choose a model policy first.

If you do not specify one, All in Luna uses resources available through the current environment, host, or deployment policy.

It does not require every user to use one fixed model or provider.

## You can take control when you want

Different kinds of work do not always deserve the same amount of expensive reasoning.

For example:

```text
Planning        → stronger reasoning
Implementation  → balanced
Mechanical work → fast / efficient
Verification    → strong / independent
```

> **Spend strong reasoning where it matters, not everywhere.**

All in Luna also separates the problem itself. Strong models can work on narrower, more stable objectives with less unrelated context and fewer cross-task switches.

**Less unrelated context. Less task switching. Less room for drift.**

Common ways to use the resource system include:

- **Balanced** — sensible defaults for most projects;
- **Quality first** — stronger reasoning for architecture, difficult debugging, risky refactors, and research;
- **Efficient** — reserve stronger reasoning for decomposition, hard blockers, synthesis, and final verification;
- **Single model** — keep the run on one explicitly selected model where possible.

These are usage patterns, not model names hard-coded into the Core. Advanced users can still override concrete models and reasoning at Task or WorkUnit scope.

See [Models & performance](docs/user/models-and-performance.md).

---

# Workflow Packs

The All in Luna Core is responsible for:

```text
top-level tasks
dependencies
scheduling
context
results
recovery
```

A Workflow Pack can define how an individual Task should work.

### Delivery

The default software-delivery path for features, bug fixes, refactors, migrations, and integrations.

### GSD

Use GSD when you want a more explicit development workflow:

```text
clarify
→ specify
→ decompose
→ implement
→ verify
→ integrate
```

GSD and All in Luna operate at different layers.

**GSD can run inside an All in Luna Top-level Task.**

### Research Routes

Preserves Claims, Evidence, unknowns, contradictions, experiments, and research route changes.

Research judgment does not automatically become implementation authorization.

---

# How is it different?

| | Subagents | GSD | All in Luna |
|---|---:|---:|---:|
| Split local work | ✓ | ✓ | ✓ |
| Detailed development workflow | — | ✓ | Optional |
| Independent top-level tasks | — | — | **✓** |
| Top-level dependency scheduling | — | — | **✓** |
| Separate context per top-level task | Limited | Phase-oriented | **✓** |
| Local workers inside each task | ✓ | ✓ | **✓** |
| Pluggable workflows | — | — | **✓** |
| Persistent run / recovery | Depends | Depends | **✓** |

The key difference is not who can create more agents.

> **All in Luna makes the Top-level Task itself a first-class runtime object.**

### What about Sol Advisor?

Sol Advisor-style systems are closer to:

```text
Strong Primary Architect
→ bounded implementation / review workers
```

All in Luna moves the orchestration boundary one level higher:

```text
Global Coordinator
→ multiple persistent Top-level Tasks
→ each Task has its own workflow and workers
```

They focus on different abstraction layers rather than being simple substitutes.

---

# Quickstart

## Vibe coding

After installing All in Luna, the simplest way to use it is just to say:

```text
Use All in Luna to finish the authentication refactor.
Keep independent parts moving in parallel where possible.
```

That's it.

You do not need to design a TaskGraph, choose a scheduler, create an agent hierarchy, or fill in a resource questionnaire first.

## CLI

When you want explicit control over a run:

```bash
python -m pip install -e .

allinluna start --goal "Finish the authentication refactor"
allinluna status RUN_ID
allinluna drive RUN_ID
```

See all commands:

```bash
allinluna --help
```

Lane, direct-work, recovery, and diagnostic commands live in the advanced CLI documentation.

---

# Permissions

Starting a run does not grant All in Luna every external permission.

Permissions matter only when the corresponding action is actually reached, including:

- push;
- merge;
- deploy;
- publish;
- credentials;
- destructive operations;
- live external mutations.

Ordinary local exploration and task organization do not require a giant permission questionnaire up front.

---

# Design philosophy

**Keep the Core small.**  
Top-level scheduling, context, protocols, and recovery belong in the Core. Specific workflows belong in Packs.

**Protocols instead of management theater.**  
Correctness should come from runtime contracts where possible, not from adding another layer of Reviewer / Auditor / Manager agents.

**Keep local complexity local.**  
A Task's tool noise and local workers should not pollute the whole project.

**Stay model-neutral.**  
The Core describes the capability it needs instead of forcing every user onto one model.

**Leave room for the user.**  
All in Luna runs complex work without silently expanding the user's goal.

---

# Documentation

### Start here

- [Quickstart](docs/user/quickstart.md)
- [Inputs & journeys](docs/user/input-and-journeys.md)
- [Models & performance](docs/user/models-and-performance.md)
- [Example](docs/examples/plain-goal.md)

### Go deeper

- [Troubleshooting](docs/troubleshooting/common-issues.md)
- [Public runtime surface](docs/architecture/public-surface.md)
- [Architecture](docs/architecture/)
- [Brand guide](docs/brand/BRAND.md)

---

# Release

See GitHub Releases and the Changelog for the current version, upgrade notes, and known limitations.

---

# License

Apache License 2.0 — see [LICENSE](LICENSE).
