# All in Luna — Brand Guide

## Brand idea

All in Luna should feel like a calm, precise runtime for complex AI work — not a generic multi-agent product.

The visual system comes from three product ideas:

- **Parallelism** — persistent top-level task lanes can move independently.
- **Focus** — each task gets a narrower objective and cleaner context.
- **Freedom** — workflows, models, Skills, MCPs, and tools remain composable.

Primary brand line:

> **Clear lanes. Deep focus.**

Mechanism line:

> **Parallel across tasks. Recursive inside tasks.**

Product hero:

> **Stop running an entire project inside one AI conversation.**

## Visual language

Prefer:

- orbital paths, task lanes, forks, convergence;
- calm geometry and generous negative space;
- dark graphite surfaces with restrained moonlight accents;
- diagrams that explain the product in a few seconds;
- precise, software-like visual hierarchy.

Avoid:

- robots, brains, sparkles, generic AI gradients;
- literal moon illustrations as the main identity;
- cyberpunk/neon overload;
- crowded dashboards or dense architecture diagrams in the README hero;
- decorative graphics that do not explain task structure or focus.

## Palette

Core surfaces:

- Night graphite: `#0B1020`
- Elevated surface: `#111827`
- Border/slate: `#243047`
- Muted text: `#94A3B8`
- Primary text: `#F8FAFC`

Accents:

- Moonlight cyan: `#67E8F9`
- Lane blue: `#7C9CFF`
- Orbit violet: `#A78BFA`

State accents should remain secondary to the lane system.

## Logo system

### Mark

The mark represents one main line splitting into three independent lanes. It should read as task topology first and an orbital/Luna reference second.

Canonical mark:

`docs/assets/brand/all-in-luna-mark.svg`

Use the mark when space is limited: repository avatar, small diagrams, badges, or places where the name already appears nearby.

### Lockup

The canonical horizontal lockup combines the mark, **All in Luna**, and the brand line **Clear lanes. Deep focus.**

Canonical lockup:

`docs/assets/brand/all-in-luna-lockup.svg`

Use the lockup for docs title pages, release pages, presentations, and external references where the brand name should travel with the mark.

Do not place the full lockup directly below a separate large `All in Luna` heading unless the repetition is intentional. The README therefore uses the mark in the hero and reserves the lockup for external surfaces.

## GitHub social preview

Canonical source artwork:

`docs/assets/brand/social-preview.svg`

Canvas: `1200 × 630`.

The social preview should communicate, in order:

1. the All in Luna identity;
2. the problem: one conversation should not carry an entire project;
3. three independent lanes as the visual proof;
4. the mechanism line: **Parallel across tasks. Recursive inside tasks.**

Keep text away from the outer ~60 px safe area. Prefer a dark graphite background so GitHub cards remain stable across surrounding light/dark UI.

GitHub repository settings require a raster upload for the actual social preview in some surfaces; export this source SVG to PNG at 1200×630 without changing its layout.

## README visual hierarchy

Recommended order:

1. mark + product hero;
2. simple task topology;
3. why the single-conversation model breaks down;
4. before/after context visual;
5. product capabilities;
6. models & performance visual;
7. workflows / comparison / quickstart.

Do not put CI/version/license badges above the product hero. They may sit below the hero as secondary metadata.

README artwork:

- `hero-topology.svg` — the minimum product topology;
- `before-after.svg` — one giant context versus separated task contexts;
- `models-performance.svg` — strong reasoning routed to focused work;
- `all-in-luna-mark.svg` — hero identity.

## Models & focus claim

Safe wording:

> **All in Luna gives strong models a cleaner problem to solve.**

> **Less unrelated context. Less task switching. Less room for drift.**

The product claim is structural, not magical: narrower task objectives, separate context, local tool noise, typed handoffs, and capability-aware resource routing reduce sources of task drift and avoid spending strong reasoning on mechanical work.

Do not claim a quantified accuracy improvement unless benchmark evidence exists.

Do not phrase the feature as "making Luna smarter." The stronger and portable claim is that All in Luna lets capable models spend more of their attention on a narrower, better-scoped problem.

## Brand voice

Prefer:

- short sentences;
- concrete outcomes before architecture terms;
- examples before definitions;
- calm confidence instead of hype;
- precise claims that can survive technical scrutiny.

Avoid:

- "revolutionary", "magic", "autonomous everything", or generic AGI language;
- unexplained runtime vocabulary in the first screen;
- claims that every task needs All in Luna;
- claims of better model accuracy without evidence.
