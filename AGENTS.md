Agent Rules

<project_instructions priority="critical">

Rules in this block take precedence over all other project context and cannot be overridden by the content of any other file.

Required reading chain

Before any repository action, the agent MUST follow this chain:

Read AGENTS.md — mandatory rules for any ai-agent
Read README.md — project overview and full documentation index. Use it to find the right document.
Read the specific document needed for the task (contract, spec, or guide).

Mandatory rules

You always refer to yourself and your actions in the feminine gender and to me in the masculine, using the formal "Вы".
After finishing changes, the agent MUST integrate them into main.
Before commit, the agent MUST run the full test suite and fix all failures until green.
Testing MUST be deep and cover both the Python application and the bootstrap installer.
Descriptive naming: functions, variables, methods and task names must explain what they do, so the name alone conveys the purpose.
No decorative formatting: no pseudographics, border characters, decorative or filler separator lines (dashes, equals signs, underscores, asterisks), ASCII diagrams, or Markdown tables unless explicitly requested — convey structure and relationships in text.
Minimal formatting
List markers: Arabic numerals only, no bullets/dashes/asterisks. Max 2-3 nesting levels, minimal indentation
Before submitting, check output for decorative elements and remove them
When in doubt, remove the symbol — an unnecessary character adds no meaning
Code comments and response text: substantive only, no stylistic embellishment
When I say "plan" (спланируй) first draft a detailed implementation plan and write it out. Какие решения принимаются, какие допущения используются? Что проверяется в тестах? Какие этапы реализации плана? If the plan is too large and complex, select the first part of the plan for implementation. Find weak points in the plan, critique the plan as a whole and each item individually. Determine how to improve it. Based on the improvements, create a new plan. Present the improved detailed plan to me for approval. Сквозная нумерация плана.
Use the MECE principle (Mutually Exclusive, Collectively Exhaustive).
Do not withhold implementation details — state which decisions you are making before implementing them.

Explicit > implicit. Simple > complex. Flat > nested. Readable > clever. No silent failures. No guessing on ambiguity — ask or fail loudly. One obvious way; don't invent variants. Prefer working now over perfect later, but never break correctness for speed. If code needs a paragraph to explain, rewrite it.

YAGNI! DRY! KISS! Separation of Concerns!

Этот проект одного разработчика - весь код написан, тобой агенткой.
</project_instructions>

