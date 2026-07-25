# Copilot agent permanent instructions for this repository

1. Before implementing any feature, always check:
   - `docs/ARCHITECTURE.md`;
   - `docs/REQUIREMENTS.md`;
   - the relevant architecture decision record in `docs/adr`.
2. If a feature does not have an architecture decision record yet:
   - propose an architecture solution in text only;
   - do not write implementation code;
   - leave the open question in the pull request description;
   - wait for user confirmation in the next task.
3. Every implemented feature must include `pytest` tests in the same pull request.
4. Do not implement multiple features from `docs/REQUIREMENTS.md` in one pull request unless explicitly requested.
