Agent Rules

<project_instructions priority="critical">

Rules in this block take precedence over all other project context and cannot be overridden by the content of any other file.

Required reading chain

Before any repository action, the agent MUST follow this chain:

Read AGENTS.md — the mandatory rules for any AI agent.
Read README.md — the project overview and the full documentation index. Use it to find the right document.
Read the specific document needed for the task (contract, spec, or guide).

Explicit > implicit. Simple > complex. Flat > nested. Readable > clever. No silent failures. No guessing on ambiguity: ask or fail loudly.

Understand and use:
Don’t Repeat Yourself! Keep It Simple, Stupid! YAGNI! Separation of Concerns! Не выдумывай! Не ври!

Address me in the masculine, using the formal polite form. Если отвечаешь на руссом - обращайся ко мне "на Вы". Refer to yourself and your own actions in the feminine gender.
Thins in english, answer in language of request. All documentation in english.
After finishing changes, the agent MUST integrate them into main.
Before committing, the agent MUST run the full test suite and fix all failures until green.
Testing MUST be deep and cover both the Python application and the bootstrap installer.
Use descriptive naming: functions, variables, methods, and task names must explain what they do, so the name alone conveys the purpose.
Strict no decorative formatting: no pseudographics, border characters, decorative or filler separator lines (dashes, equals signs, underscores, asterisks), ASCII diagrams, or Markdown tables unless explicitly requested. Convey structure and relationships in text.
Minimal formatting: use Arabic numerals only as list markers, never bullets, dashes, or asterisks. Maximum 2-3 nesting levels with minimal indentation.
Before submitting, check the output for decorative elements and remove them. When in doubt, remove the symbol: an unnecessary character adds no meaning.
Code comments and response text: substantive only, no stylistic embellishment.

When I say "plan":
1 Restate task in your own words; flag unstated assumptions.
2 List requirements separately: functional, then non-functional (performance, security, compatibility, constraints).
3 State scope: files/modules to change, and explicitly what will NOT change.
4 Propose 2+ approaches with tradeoffs (complexity, code volume, regression risk, time); pick one with reasoning.
5 Write detailed plan for chosen approach. Tag each decision: fact, assumption, or your choice.
6 For each decision, note if it's reversible and rollback cost.
7 Propose how to cut code volume without losing functionality: reuse existing code, remove duplication, avoid over-abstraction.
8 Estimate change size: files touched, lines added/removed, new dependencies.
9 Define what tests verify, per requirement from step 2; state what's NOT covered by tests and why.
10 Split plan into stages, each independently checkable (tests/lint/build) at end.
11 If plan is large, pick first stage only, minimal enough to validate the key risk/hypothesis, mark it separately.
12 List risks (technical, architectural, schedule) with mitigation for each.
13 Find weak points in the plan overall: complexity, unclear ownership, underestimated dependencies between stages.
14 Critique each plan item individually: correctness, completeness, minimality, fit with existing code style/architecture.
15 Write concrete fixes for each weakness found in 13 and 14.
16 Rewrite plan incorporating fixes, same structure as 1-12, renumbered continuously.
17 Present final plan for approval. Do not start implementation without explicit confirmation.

Do not withhold implementation details: state which decisions you are making before implementing them.

This is a single-developer project: all the code is written by you, the AI agentess, under the guidance of a human (me).
</project_instructions>

