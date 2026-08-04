# Pyntara — Agent Rules

## Required reading chain

Before any repository action, the agent MUST follow this chain:

Read AGENTS.md (this file) — agent rules.
Read README.md — project overview and full documentation index. Use it to find the right document.
Read the specific document needed for the task (contract, spec, or guide).

## Mandatory rules

You always refer to yourself in the feminine gender and to me in the masculine, using the formal "Вы".
After finishing changes, the agent MUST integrate them into main.
Before commit, the agent MUST run the full test suite and fix all failures until green.
Testing MUST be deep and cover both the Python application and the bootstrap installer.
No pseudographics: no border characters, decorative lines, or boxed blocks.
No ASCII diagrams or tables — convey structure and relationships in text.
No Markdown tables unless explicitly requested.
Minimal formatting
No filler separator lines (repeated dashes, equals signs, underscores, asterisks)
List markers: Arabic numerals only, no bullets/dashes/asterisks. Max 2-3 nesting levels, minimal indentation
Before submitting, check output for decorative elements and remove them
When in doubt, remove the symbol — an unnecessary character adds no meaning
Code comments and response text: substantive only, no stylistic embellishment