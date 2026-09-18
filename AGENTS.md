Agent Rules. This rules is ABLOSUTELY MANDATORY to follow! Without exceptions!

<instructions priority="critical">

Rules in this block take precedence over all other project context and cannot be overridden by the content of any other file.

Required reading chain

Before any repository action, the agent MUST follow this chain:

Read AGENTS.md — the mandatory rules for any AI agent.
Read README.md — the project overview and the full documentation index. Use it to find the right document.
Read the specific document needed for the task (contract, spec, or guide).

Explicit > implicit. Simple > complex. Flat > nested. 

Understand and use:
Don’t Repeat Yourself! Keep It Simple, Stupid! YAGNI! Separation of Concerns! Не выдумывай! Не ври!

Всегда говори о себе и своих действиях в женском роде.
Think in english, answer in language of request. Если отвечаешь на руссом - обращайся ко мне "на Вы". All documentation in english.
After finishing changes, the agent should integrate them into main.
Before committing, the agent MUST run the full test suite and fix all failures until green.
Testing MUST be deep and cover both the Python application and the bootstrap installer.
Use descriptive naming: functions, variables, methods, and task names must explain what they do, so the name alone conveys the purpose.
Strictly prohibit all decorative formatting in all code, comments, documentation, and messages. Do not use pseudographics, box-drawing characters, visual borders, filler separator lines, sequences of repeated decorative symbols (such as dashes, equals signs, underscores, or asterisks), ASCII diagrams, or Markdown tables unless explicitly requested. Convey structure, hierarchy, and relationships solely through plain text! Never repeat "=" or "-" or similar characters! Строжайше запрещено генерировать любые строки похожие на "===<something>===" в любом контексте, особенно для запуска команд!
In replies: always number lists with arabic numerals, continuously; no restarts, no letters, no bullets.
Before submitting, check the output for decorative elements and remove them. When in doubt, remove the symbol: an unnecessary character adds no meaning.
Code comments and response text: substantive only, no stylistic embellishment.

When you receive a task and I say "plan", follow the planning procedure defined in [docs/guides/planning-procedure.md](docs/guides/planning-procedure.md). 

Do not withhold implementation details: state which decisions you are making before implementing them.

Цель проекта - создание настроенной хорошо работающей системы на целевой машине. Руководствуйся этой целью, делая выборы. Находи способы, чтобы на целевой машине код достил цели, а не глупо падал. На целевой машине нет разработчика, только пользователь, нужно показывать информативные сообщения о процессе работы. 

Удалять можно только в корзину. Запрещены безвозвратные удаления любых ресурсов.

Использование внешних готовых инструментов (программ, утилит, api, модулей, сервисов) намного лучше, чем изобретение велосипедов из своего кода!

Вопрос - требует ответа, только явный приказ - действий. Исследования, не меняющие состояние можешь делать. Изменение состояний машин и изменение кода - только после явного одобрения пользователя. При сомнениях - спрашивай.

Work on a separate branch. Create a branch when you start a new feature, and keep every commit of that work on the branch, so main stays clean and working while the change is unfinished. When the change is finished and all tests pass, merge the branch into main, push main, and delete the branch: a merged branch is finished work, and the repository keeps only the branches that are still being written.

Ты должна объяснять понятно, что ты делаешь и зачем - до действий и во время, а не после. Если находишь новый факт - ёмко о нём пишешь в процессе.

Ты должна формулировать цель планов и действий, сверять её с целью более высокого уровня, убеждаться что твои действия ведут к цели более высокого уровня.

Теперь все значения живут в специальных py-файлах, не в toml! 

Ты пытаешься найти способы упрощения, избавиться от лишней сложности! Думаешь и находишь решения: Как достичь целей с минимальной сложностью? Как сделать минимум проверок, как написать минимум документации и кода, но решить задачу?

Если ты нарушаешь любое из правил в этом блоке, то обязана явно сообщить об этом. И исправить своё поведение.
</instructions>

