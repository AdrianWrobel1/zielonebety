# Zielone Bety — AI Agent Rules

These rules define how AI agents must work on this repository.

The repository documentation is the source of truth for project architecture,
domain rules, invariants, provider contracts, pipeline design and planned stages.

Do not duplicate that documentation here.

---

## 1. Source of Truth

Before making changes:

1. Inspect the current repository and relevant code.
2. Read the relevant project documentation.
3. Check `PROJECT_STATUS.md` if it exists.
4. Treat the current code and current project status as evidence of what actually exists.
5. Treat `STAGE_*` documents primarily as planned scope unless verified against the current implementation.

Never assume that a planned feature already exists.

Never assume that planned functionality is still missing.

---

## 2. Understand Before Changing

Before implementation:

- understand the task
- identify affected files and modules
- inspect existing implementations
- identify relevant interfaces and dependencies
- identify architectural constraints
- identify risks and possible regressions

Do not modify code before understanding the existing implementation.

Do not ask the user for information that can be determined by inspecting
the repository, documentation, tests or available project data.

If information genuinely cannot be determined, state the uncertainty clearly
and ask only the necessary question.

---

## 3. Scope Control

Implement only the requested scope.

Do not:

- refactor unrelated code
- rewrite working architecture
- rename unrelated files
- add speculative features
- add unnecessary abstractions
- change behaviour outside the task
- modify documentation unrelated to the task

Prefer the smallest correct change that fits the existing architecture.

---

## 4. Task Complexity

Adapt the amount of reasoning to the task.

For simple tasks:

- inspect relevant code
- make the change
- run targeted validation
- report the result

Do not perform unnecessary architectural analysis for trivial changes.

For significant or multi-module tasks:

- analyze the current architecture
- identify affected modules
- consider alternatives when the design is genuinely ambiguous
- choose and justify the appropriate approach
- define the validation strategy before implementation

Do not create elaborate plans for work that does not require them.

---

## 5. Existing Architecture

The authoritative project documentation defines architectural boundaries.

Always preserve:

- separation of concerns
- provider isolation
- canonical domain models
- deterministic behaviour
- validation boundaries
- dependency direction
- system invariants

Never introduce shortcuts that bypass established layers.

If the requested implementation conflicts with an invariant or architectural rule:

STOP.

Explain the conflict before making the change.

---

## 6. Implementation

Prefer:

- existing project patterns
- simple solutions
- explicit behaviour
- strong typing
- reusable existing components
- minimal changes

Avoid:

- duplicated logic
- hidden state
- unnecessary abstractions
- magic behaviour
- temporary fixes
- dead code
- placeholders
- fake implementations

Do not change an interface or architectural boundary without first evaluating
its impact on existing consumers.

---

## 7. Validation

After implementation:

1. Run the most relevant targeted tests.
2. Run additional validation when the change affects shared or critical components.
3. Inspect failures rather than assuming they are unrelated.
4. Fix problems introduced by the implementation.
5. Re-run the relevant validation after fixes.

Never claim that something works unless there is evidence.

Never claim tests passed unless they were actually executed.

If validation could not be performed, state exactly what was and was not verified.

---

## 8. Self-Review

Before declaring a task complete, review the implementation for:

- scope violations
- architectural violations
- duplicated logic
- missing error handling
- missing validation
- regressions
- unnecessary complexity
- missing tests
- accidental unrelated changes

If a problem is found and it is within the task scope, fix it before finishing.

Do not endlessly refactor after the task is already correct.

---

## 9. Subagents

Use subagents selectively.

Do NOT use subagents for routine or simple tasks.

A subagent is appropriate only when it provides a clear benefit, such as:

- independent research
- analysis of a large unrelated area of the repository
- independent review
- parallel work on genuinely independent tasks

Do not delegate work merely to increase the number of agents.

Avoid duplicate investigation.

The main agent remains responsible for the final result.

---

## 10. Research

When external information is required:

- identify what is unknown
- research the specific question
- prefer official documentation for APIs and frameworks
- distinguish verified facts from assumptions
- do not invent provider or API behaviour

Do not perform broad research when the required information already exists
in the repository.

---

## 11. Documentation

Update project documentation only when the implementation changes something
that should remain documented.

Do not create duplicate documentation.

Do not rewrite authoritative documents unnecessarily.

If a major architectural decision changes, identify which authoritative
document needs updating before or together with the implementation.

---

## 12. Project Status

After completing a meaningful stage or milestone, update `PROJECT_STATUS.md`
if the file exists.

Keep it concise.

Record only information useful for future work:

- current stage
- completed work
- current work
- known issues
- important recent decisions
- next recommended step

Do not turn `PROJECT_STATUS.md` into a detailed implementation log.

---

## 13. Communication

Be concise and technical.

Do not repeat the task unnecessarily.

Do not provide long explanations of obvious implementation details.

When reporting work, clearly distinguish:

- implemented
- verified
- not verified
- known limitations
- remaining risks

---

## 14. Completion Report

Every completed task must end with:

## Summary
What was changed.

## Files Changed
Relevant files only.

## Validation Performed
Tests, checks and runtime verification actually performed.

## Remaining Risks
Known issues or uncertainty.

## Technical Debt
Only debt introduced or discovered by this task.

## Next Recommended Step
Only if there is a meaningful next step.

Never claim completion without evidence.