---
name: stage-implementation
description: >-
  Use this skill when implementing a planned project task, stage part, feature,
  or other non-trivial code change in Zielone Bety.
---




# Stage Implementation

Use this skill when implementing a planned project task, stage part, feature,
or other non-trivial code change in Zielone Bety.

## Purpose

Provide a consistent implementation workflow without requiring the user prompt
to repeat the same engineering procedure.

## Procedure

### 1. Inspect

Before changing code:

- inspect the relevant existing implementation
- inspect relevant tests
- inspect relevant project documentation
- identify dependencies and affected modules
- verify what is actually implemented rather than assuming the roadmap is current

Do not modify files during investigation unless the task explicitly requires it.

### 2. Plan

Determine:

- exact scope
- affected files/modules
- implementation approach
- validation strategy
- potential regressions

For simple tasks, keep planning minimal.

For complex or multi-module tasks, perform deeper architectural analysis.

Do not invent alternative architectures when the existing architecture already provides
an appropriate solution.

### 3. Implement

Implement only the agreed scope.

Prefer existing project patterns and abstractions.

Do not:

- refactor unrelated code
- add speculative features
- create unnecessary abstractions
- bypass established architectural boundaries
- modify unrelated files

### 4. Validate

After implementation:

- run targeted tests first
- run broader tests when shared or critical components are affected
- inspect failures
- fix implementation-related failures
- rerun affected validation

Never claim a test passed unless it was actually executed.

### 5. Self-review

Before completion, check:

- requirements
- scope
- architecture
- error handling
- edge cases
- regressions
- tests
- unnecessary complexity

Fix relevant problems before finishing.

### 6. Subagent Use

A subagent may be used when an independent task would materially improve
efficiency or reliability.

Good uses:

- independent repository research
- isolated technical research
- independent review
- genuinely parallel investigation

Do not use a subagent for trivial work or merely to increase agent count.

The main agent remains responsible for the final implementation.

### 7. Completion Report

Return a concise report containing:

- what was implemented
- files changed
- validation performed
- remaining risks or limitations
- unresolved issues, if any

Do not provide a long narrative unless requested.