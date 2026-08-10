# All in Luna

[简体中文](README.md)

All in Luna turns “get this outcome done” into an execution path that can continue and be verified. Give it a goal, an existing plan, an active run, or a Research Routes packet; it separates the work into independent top-level Tasks, advances their dependencies, and retains evidence for implementation, checks, and handoffs. It is for work that spans tools and deliverables, where a claim of “done” is not enough.

## Why add a Top-level Task layer?

Ordinary subagents are excellent for small local work, but they are usually temporary workers inside one conversation. All in Luna first organizes an outcome into independent top-level Task Lanes: the Coordinator holds cross-task dependencies, permissions, and the final result, while each Lane receives only the scope and context needed for its part. A failure, wait, or evidence gap in one Task therefore cannot quietly become completion for another. Local workers are still useful, but they are not the product entry point and do not replace top-level Tasks.

## Start in 60 seconds

1. In Codex Plugins, choose `plugins/allinluna/`, then tell All in Luna your goal, for example: “Add a tested health-check endpoint.”
2. For the command line, install this repository and create then inspect a run:

   ```bash
   python -m pip install -e .
   allinluna start --goal "Add a tested health-check endpoint"
   allinluna status RUN_ID
   ```

3. Review `next-actions`, let the host perform the explicitly requested action, then use `allinluna drive RUN_ID` to continue.

You do not need to write a TaskGraph, select a scheduler, or choose a model first.

## One real example

“Add a tested health-check endpoint” is an ordinary software-delivery goal. The default `delivery` Pack compiles it into traceable work until the endpoint, targeted tests, and changed-path evidence can be checked. Create the run with `allinluna start --goal "Add a tested health-check endpoint"`, then use `status` and `next-actions` to inspect its real state and next step; compilation or preview alone is not delivery completion. See the complete [plain-goal example](docs/examples/plain-goal.md).

## Default resource behavior

All in Luna is vendor-neutral. When you do not name a model, resource choice follows explicit user request, Task/WorkUnit override, user preference, Pack capability, deployment/host, and the current session default. It retains `requested`, `resolved`, and host-reported `actual` separately. Without telemetry, `actual` stays unresolved; the runtime never invents a fallback model or execution record.

## Workflow Packs

- `delivery`: the default software-delivery path.
- `gsd`: use it when you explicitly want clarify → specify → decompose → implement → verify → integrate.
- `research-routes-bridge`: preserves Claims, Evidence, unknowns, contradictions, and experiment authorization for a research route; it does not turn research material into implementation authority.

## Installation

Install `plugins/allinluna/` in Codex to start from a conversation. For development or automation:

```bash
python -m pip install -e .
allinluna --help
```

The public Skill is `plugins/allinluna/skills/allinluna/SKILL.md`. The registry only makes it discoverable; it is not a required user entry point.

## Permissions and safety boundaries

All in Luna asks for permissions only when an action is reached. Credentials, push, deployment, publication, destructive work, and external live mutation do not happen by default; they require explicit authorization. Host observations are not guessed: `identity`, `create`, `read`, `wait`, `cancel`, and `idempotency`, along with the `requested`, `resolved`, and `actual` resource layers, must come from the relevant real records.

## Documentation map

- [Quickstart](docs/user/quickstart.md): everyday plugin and CLI entry points.
- [Inputs and journeys](docs/user/input-and-journeys.md): how goals, plans, runs, and Research Routes inputs are handled.
- [Plain-goal example](docs/examples/plain-goal.md): a copyable API and CLI example.
- [Troubleshooting](docs/troubleshooting/common-issues.md): relay, resource, project-resolution, and recovery help.
- [Public surface and evidence boundaries](docs/architecture/public-surface.md): architecture for readers who need traceability.
- [RC2 technical contracts](docs/architecture/v2-rc2/): developer detail for Store, receipts, CLI, and conformance diagnostics.

## RC status

All in Luna `2.0.0-rc.2` is a release candidate; PR #2 remains Draft and this is not a stable release. Use it for evaluation and integration qualification. It can be considered Ready only after remote CI, full runtime journeys, distribution validation, and the real host canary all pass.

Apache License 2.0. See [LICENSE](LICENSE).
