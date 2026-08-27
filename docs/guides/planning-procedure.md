# Planning procedure

This document defines the mandatory planning procedure for every task that requires a plan. The procedure lives here so `AGENTS.md` and `developer-guide.md` can reference it instead of repeating the text.

## Goal model

Two goals exist for every task: the described goal (what the task text or specification literally says) and the implied goal (what the user actually experiences as a working result after the task is done). The implied goal is the real acceptance test; the described goal only serves it.

## Procedure

When you receive a task and the user says "plan", follow this procedure.

### Stage 0. Research

This comes before any planning and decides how long the plan needs to be.

0.1 Write down every unknown fact that the choice of approach depends on: who owns the relevant state, which tool or client actually works, the exact API call needed, how a library behaves, whether a ready-made solution already exists.

0.2 For each unknown, use every available method to resolve it, not just one. Try things directly on the machine. Search the internet. When searching, try more than one query: use different wordings, synonyms, and also search in English even if the task or conversation is in another language, because English sources are often more complete. Do not stop at the first search result or the first attempt if it did not give a clear answer.

0.3 Record the result of each probe and each search as a fact: for something found on the machine, record the exact command run and its output; for something found on the internet, record the source. Do not write down a claim as a fact unless it is backed by an actual command output or an actual source.

0.4 Any unknown that could not be resolved after reasonable effort must be explicitly labeled as an assumption and carried forward into the plan, not silently guessed at.

0.5 The unknowns that remain as assumptions after this stage determine how much of the following steps are required. If the key mechanism is confirmed by facts, keep the plan short and skip comparing alternative approaches where no real alternative exists. If important assumptions remain, step 4 (comparing approaches) and step 7 (test coverage) below are mandatory for those specific points.

One rule applies throughout research: never guess at a mechanism that a quick probe or a search could settle. Never run a probe that disrupts the current working session (for example, restarting the window manager or the display session is forbidden).

### Stage 1. Restate the task

Restate the task in your own words. Point out any assumptions in the task that were not stated explicitly.

### Stage 2. List requirements

List requirements separately: first the functional requirements (what the system must do), then the non-functional ones (performance, security, compatibility, other constraints).

### Stage 3. State the scope

State the scope clearly: which files or modules will be changed, and explicitly which ones will not be touched.

### Stage 4. Propose approaches

Propose at least two possible approaches, with their tradeoffs (complexity, amount of code, risk of breaking existing behavior, time needed). Pick one and explain why. This step is mandatory for any point still marked as an assumption after stage 0. It can be skipped for points already confirmed as facts.

### Stage 5. Write the detailed plan

Write a detailed plan for the chosen approach. Tag every decision in the plan as one of three things: a fact, an assumption, or your own choice.

### Stage 6. Maximize reuse

Maximize reuse instead of writing new code. Actively look for and reuse existing code, existing functions, existing methods, and the existing architecture of the project. Also look for existing external tools or programs that already solve part of the problem, instead of building something new. Avoid duplicating logic that already exists elsewhere. Avoid adding abstraction layers that are not needed yet.

### Stage 7. Define test coverage

For each requirement listed in stage 2, define exactly what a test would check to verify it. State clearly what is not covered by tests and why. Any assumption carried forward from stage 0.4 must have a test that specifically checks whether that assumption holds.

### Stage 8. Split into stages

Split the plan into stages. Each stage must be checkable on its own, for example by running tests, a linter, or a build, before moving to the next stage.

### Stage 9. Select the first stage

If the overall plan is large, select only the first stage to actually start with. That first stage should be the smallest one that still tests the main risk or the main open question in the plan. Mark this first stage separately from the rest.

### Stage 10. List risks

List the risks and the weak points of the plan together, in one list. Include technical risks, architectural risks, schedule risks, unclear ownership of parts of the task, and dependencies between stages that may be underestimated. Give a mitigation for each item.

### Stage 11. Critique and rewrite (optional)

Only if important assumptions still remain after stage 0, or if the plan is large: go through every point of the plan and criticize it individually for correctness, completeness, whether it is minimal, and whether it fits the existing code style and architecture. Write concrete fixes for every weak point found. Then rewrite the whole plan with those fixes included, using the same structure as above, with continuous numbering. If the plan is short and based on confirmed facts, skip this rewriting cycle and instead just check once that stages 1 through 10 are complete and consistent.

### Stage 12. Present and get approval

Present the final plan and ask for approval. Do not start implementing anything before receiving explicit confirmation.

### Stage 13. Verify after implementation

After implementation is done, verify the result on the real machine: actually run the task and check that the implied goal (see the beginning of this procedure) is met in practice, not only that unit tests pass. Unit tests confirm the decision logic is correct; running the task live confirms the actual mechanism works.
