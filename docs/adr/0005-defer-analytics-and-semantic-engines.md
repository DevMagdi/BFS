# ADR 0005: Defer analytical and semantic-engine adoption until measured

- **Status:** Proposed; benchmark gate assigned to Milestone 5.
- **Date:** 2026-11-21

## Context

DuckDB and Polars may accelerate file processing and analytics. WrenAI and Cube may provide semantic models, metrics, query APIs or natural-language query support. The product requires canonical ownership, tenant isolation, correct joins/financial calculations and predictable operations; supported languages and terminology scope remain an owner/product decision.

## Options considered

1. Adopt DuckDB, Polars, WrenAI and Cube together immediately.
2. Choose one semantic and one analytics engine from feature descriptions alone.
3. Start with PostgreSQL and a BusinessFlow-owned deterministic metric catalog; benchmark candidates on actual workloads before optional adoption.

## Proposed decision

**Recommend option 3.** PostgreSQL remains the proposed canonical transactional store; DuckDB is an optional analytical execution engine and Polars an optional dataframe transformation tool, not substitutes for the canonical model. Benchmark each on representative workloads; they may be complementary but must justify separate complexity. WrenAI and Cube remain a fair benchmark, but they serve adjacent, non-identical use cases: question/query planning versus a semantic metrics/query API. Define separate comparable acceptance tracks (intent/clarification/query correctness for WrenAI; semantic model/join/security-context correctness for Cube). Adopt at most one optional read-only semantic candidate only if it passes correctness, tenant/access, approved-language, safety, resource and operating-cost gates. If neither passes, keep the internal deterministic metrics/query service. BusinessFlow owns canonical facts, formulas, metrics, decisions, permissions, approvals and outcomes regardless of benchmark results.

## Reasoning

Source inspection confirms useful functionality but does not establish BusinessFlow-specific correctness in any supported language, permission propagation, tenant isolation or production operating cost. A benchmark is the appropriate evidence before adding services or large dependency trees; language tests follow approved product scope.

## Consequences

- No analytics/semantic dependency is introduced before the benchmark milestone.
- The BusinessFlow KPI/financial formula catalog remains canonical and deterministic regardless of the selected adapter.
- Package-level and transitive licenses, security advisories, maintenance state and integration behavior must be audited for any selected release.
