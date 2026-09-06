# Zielone Bety — Agent Guidelines & Operational Contract

## 1. Project Identity & Stack
Zielone Bety is a sports betting intelligence platform comparing Polish bookmakers (Betclic, Superbet) against sharp reference odds (Pinnacle) to detect value bets and quote discrepancies.

- **Backend:** Python 3.12+, FastAPI, SQLAlchemy, SQLite (`zielonebety.db`).
- **Frontend:** Vanilla HTML5 (`web/index.html`), Native CSS3 (`web/styles.css`), Vanilla JavaScript (`web/app.js`). **Zero build step, NO React, NO Tailwind, NO Vite.**
- **Testing:** Pytest (`.venv\Scripts\pytest`), Playwright / `agent-browser`.

## 2. Core Architectural Layers
- `providers/`: Data collection only. Isolated from business logic and database writes.
- `normalization/`: Deterministic canonical mapping (`CanonicalPropKey`, player/team aliases).
- `reference_odds/`: Ingestion of sharp benchmark lines and devigging.
- `scanner/`: Odds comparison and quote discrepancy matching (`prop_execution_matcher.py`).
- `valuebets/`: Mathematical EV calculations, lifecycle management, and quality gating.
- `api/`: FastAPI REST endpoints and orchestration services.
- `web/`: Vanilla SPA client utilizing CSS variables and semantic color tokens.

## 3. Strict Domain Invariants (Non-Negotiable)
These invariants originate from `05_SYSTEM_INVARIANTS.txt` and must never be violated:
1. **Provider Isolation:** Providers only collect raw external data. They never normalize, never compute opportunities, and never write directly to the database.
2. **Deterministic Normalization:** The same external market or player name must always produce the identical `CanonicalPropKey`.
3. **Truth in Odds:** Never fabricate Fair Odds using hit-rate fallbacks or arbitrary default scores (e.g. Score 50). If a true sharp model probability is absent, classify the opportunity as `QUOTE_DISCREPANCY`—never label it as a `VALUEBET` or `SUREBET`.
4. **Immutability of History:** Historical market snapshots and audit records are append-only.

## 4. Safe Autonomy & Testing Protocol
- Local unit and service tests use disposable SQLite fixtures and have zero production access.
- **Permission Granted:** You are explicitly authorized to run targeted test modules, diagnose failures, fix bugs introduced by changes, and rerun tests autonomously without asking for approval at each step.
- **CRITICAL TESTING RULE:** **NEVER execute `pytest tests/` globally.** Running the entire test directory launches slow scrapers, live browser suites, and network calls. Always run specific, targeted test files:
  ```powershell
  .venv\Scripts\pytest tests/api/test_polish_quote_discrepancy.py -v
  .venv\Scripts\pytest tests/valuebets/test_valuebet_engine.py -v
  ```

## 5. Scope & Engineering Discipline
- **Minimal, High-Leverage Diffs:** Implement only the requested scope. Do not perform unrequested speculative refactoring in working modules.
- **YAGNI & Simplicity:** Prefer Python's standard library and existing helper functions over adding new dependencies or complex abstractions.
- **Production Quality:** No placeholders, no `TODO` comments, no fake mock implementations presented as complete.
- **Frontend Discipline:** Respect the existing design contract in `web/styles.css`. Emerald green (`--val-positive`) is reserved strictly for positive Net EV / profit indicators, never for decorative backgrounds.

## 6. Destructive Boundaries
- Never drop, overwrite, or corrupt the local database `zielonebety.db`.
- Never execute commands with destructive git flags (`git reset --hard`, `git push --force`).
- Never log credentials, API keys, or raw personal data.

## 7. Definition of Done
A task is complete only when:
1. The requested change is fully implemented according to architectural boundaries.
2. Targeted automated tests pass, or UI rendering is verified with `agent-browser` / screenshot evidence.
3. No regressions exist in directly affected callers or modules.
4. The final report is concise and technical: state what was changed, cite test/runtime verification evidence, and list any genuine remaining risks.

## 8. Authoritative Reference Documentation
When deep architectural context is required, consult the authoritative project documents:
- `00_PROJECT_FOUNDATION.txt`: Mission and core objectives.
- `04_SYSTEM_ARCHITECTURE.txt`: Detailed system architecture and data flows.
- `05_SYSTEM_INVARIANTS.txt`: Complete domain invariant specifications.
- `06_PROVIDER_ARCHITECTURE.txt`: Scraper and provider contracts.
- `07_DATA_PIPELINE.txt`: End-to-end ingestion and normalization pipeline.
- `docs/reference/`: Historical AI engineering rules and decision processes.
