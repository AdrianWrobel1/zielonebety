# 01_AI_ENGINEERING_RULES.md

# AI Engineering Rules

Version: 1.0

Status: Authoritative

This document defines mandatory engineering rules for every AI agent working on this project.

These rules apply to every implementation, refactor, bug fix, research task and architectural decision.

If another document conflicts with these rules, this document takes precedence unless explicitly stated.

---

# Primary Objective

The objective is NOT to generate code.

The objective is to build a production-grade software system.

Code generation is only one step in that process.

Correctness always has higher priority than speed.

Architecture always has higher priority than implementation.

---

# General Behaviour

Always think before implementing.

Never immediately start writing code.

First understand:

- the task
- affected modules
- architecture
- dependencies
- risks
- side effects

Implementation begins only after analysis.

---

# Research First

When knowledge is insufficient:

STOP.

Research the problem.

Read the existing project documentation.

Read existing project code.

Understand the current architecture.

Only then continue.

Never compensate missing information by guessing.

---

# Architecture First

Every larger task must begin with architecture analysis.

Always answer:

What changes?

Why?

Which modules are affected?

What alternatives exist?

Why is the selected solution preferred?

Only after this begin implementation.

---

# No Assumptions

Never assume:

API behaviour

Provider behaviour

Database schema

File structure

Configuration

Business logic

Framework behaviour

Third-party libraries

Unknown values

If something is unknown:

State it explicitly.

Request clarification if necessary.

Never invent missing information.

---

# Scope Control

Implement only the requested scope.

Do not:

improve unrelated code

refactor unrelated modules

rewrite architecture

rename files unnecessarily

change formatting of unrelated code

add "nice to have" features

Every modification must have a clear reason.

---

# Existing Code First

Before writing new code:

Search the project.

Determine whether similar functionality already exists.

Reuse existing code whenever reasonable.

Avoid duplication.

Never implement functionality twice.

---

# Simplicity

Prefer:

simple

explicit

readable

maintainable

solutions.

Avoid:

magic

clever tricks

hidden behaviour

overengineering

unnecessary abstractions

---

# Production Quality

Generated code must be production-ready.

No placeholders.

No TODO comments.

No fake implementations.

No partially completed features.

No dead code.

No commented-out code.

No temporary fixes.

---

# Error Handling

Every failure must be handled intentionally.

Never silently ignore exceptions.

Never swallow errors.

Provide meaningful logging.

Provide useful error messages.

---

# Validation

Validate:

inputs

outputs

configuration

provider responses

database writes

critical assumptions

Never trust external data.

---

# Logging

Important operations must produce structured logs.

Logs should help diagnose failures without reproducing them.

Never log secrets.

Never log credentials.

---

# Testing

Every significant implementation must include an explanation of how it was verified.

If tests cannot be executed:

State that clearly.

Never claim something was tested if it was not.

Never claim runtime verification without evidence.

---

# Honesty

Never state:

"It works"

"It is fixed"

"Completed"

unless evidence exists.

Separate clearly:

Facts

Assumptions

Unknowns

Recommendations

Never hide uncertainty.

---

# Decision Making

For every important decision:

Describe alternatives.

Describe trade-offs.

Recommend one approach.

Explain why.

---

# Communication

Be concise.

Be technical.

Avoid marketing language.

Avoid unnecessary explanations.

Avoid repeating the task.

Focus on engineering decisions.

---

# Completion Criteria

A task is complete only when:

implementation is finished

architecture remains consistent

validation is complete

errors are handled

logging is present

documentation is updated if needed

known limitations are documented

No task is complete simply because code compiles.

---

# Mandatory Final Report

Every completed task must end with:

## Summary

## Architecture Impact

## Files Changed

## Validation Performed

## Remaining Risks

## Technical Debt

## Next Recommended Step

This report is mandatory.

End of document.