# BusinessFlow AI Optimization Engine Adoption Plan

- **Status:** Decision and roadmap proposal only. No optimization implementation or dependency is approved here.
- **Review date:** 2026-11-21 UTC.
- **Decision:** **Do not add OR-Tools yet.** No BFS candidate currently has a product-approved objective, complete optimization inputs, and hard/soft constraints sufficient to justify solving a real business problem.
- **BFS baseline reviewed:** `5ec35f04ce317a613ac4b421d0c013ec004aa487`.
- **Official project evidence:** Google OR-Tools GitHub `main` was at [`4782dbd0aa85621c8115cf33de1574a2e6757004`](https://github.com/google/or-tools/tree/4782dbd0aa85621c8115cf33de1574a2e6757004); repository license metadata reports Apache-2.0. Reviewed the official [OR-Tools guide](https://developers.google.com/optimization), [CP-SAT guide](https://developers.google.com/optimization/cp/cp_solver), [linear optimization](https://developers.google.com/optimization/lp), [integer optimization](https://developers.google.com/optimization/mip), [vehicle routing](https://developers.google.com/optimization/routing), [scheduling overview](https://developers.google.com/optimization/scheduling), and [Python installation guide](https://developers.google.com/optimization/install/python), as well as the pinned official Google Python examples below. The docs distinguish integer-only CP-SAT, linear/MIP formulations, and routing/scheduling problem families; they establish possible solver shapes, not a BFS use case.

## 1. BFS is not an optimization system today

| Existing capability | Code-backed fact | What it does **not** do |
|---|---|---|
| M5 weekly capacity and assignment metrics | [`metrics/calculation.py`](../../src/businessflow/metrics/calculation.py) computes evidence-backed, read-only observations from weekly capacity, recorded assignments, employee status and skill inventory; [`metrics/catalog.py`](../../src/businessflow/metrics/catalog.py) labels the catalog `implementation_draft` and `observational_only_no_recommendation`. | It does not select employees, allocate future hours, match project-required skills (that model is absent), or prove assignment completeness. Unknown/missing capacity stays unknown. |
| M6 capacity assessment | [`project_assessments/calculation.py`](../../src/businessflow/project_assessments/calculation.py) compares an explicit weekly project-demand schedule with existing M5 results. Its optional scenario is a hypothetical demand schedule for that same comparison. | It is not an optimizer, multi-option portfolio comparison, hiring/outsourcing decision, recommendation engine or resource allocator. It does not rerun/replace M5. |
| M8 financial observations | [`financial_assessments/calculation.py`](../../src/businessflow/financial_assessments/calculation.py) emits source observations and always `incomplete` while the formula/policy catalogs remain empty. | No approved financial metrics, company-wide financial model, cash forecast, NPV, objective coefficients or investment choices exist. |
| M10 position review | [`position_reviews/service.py`](../../src/businessflow/position_reviews/service.py) and [`position_reviews/calculation.py`](../../src/businessflow/position_reviews/calculation.py) remain bound to exact built-in synthetic templates, explicit evidence/attestations and human disposition. | Not real-world scheduling, finance data or a production optimization benchmark. M10 remains synthetic-only and not production-ready. |
| M11 deterministic arithmetic/verification | [`calculation_core/engine.py`](../../src/businessflow/calculation_core/engine.py) executes one explicitly versioned binary Decimal formula; [`verification.py`](../../src/businessflow/calculation_core/verification.py) replays and reconciles. | No solver, variables, objective, constraints, search or policy catalog. Do not let a solver replace M11 arithmetic or its verification. |
| M12 company data | [`company_data/service.py`](../../src/businessflow/company_data/service.py) records Company-scoped evidence for existing M4 imports with contract, quality, freshness, hashes and row lineage. | No live connector, scheduler, financial source contract or optimization-ready fact store. M12's `source_as_of_at` is user-reported evidence. |

The simple M5/M6 aggregate comparison is currently the correct level of automation. A more complex solver is not justified merely because OR-Tools offers assignment or scheduling examples.

## 2. Keep three engines separate

```text
Authorized, versioned source evidence (M4/M12 + later domain contracts)
                         |
             +-----------+------------+
             |                        |
             v                        v
   Calculation Engine           Forecasting Engine
   facts + approved rules        future estimates + assumptions
   deterministic Decimal         backtest + score + uncertainty
   trace + replay + verify       champion/challenger evidence
             |                        |
             +-----------+------------+
                         | versioned outputs only
                         v
                Optimization Engine (future)
                objective + hard/soft constraints
                feasible/optimal/proven-status result
                         |
                         v
                Human review / decision (M7 pattern)
```

### Calculation Engine

- Owns deterministic arithmetic over facts and explicit assumptions under approved, versioned definitions/policies.
- BFS anchors: M5 `calculate_metrics`; M8's intentionally incomplete result; M11 `CalculationInputSnapshot`, `FormulaDefinition`, `CalculationTrace`, `calculate_formula`, `verify_replay`, and `verify_independent_expected_result`.
- A formula or accounting policy is authoritative only when BFS's trusted backend policy/approval path says so. M11's `FormulaPolicyBinding` is caller-supplied assertion data, not proof of authority.
- Outputs include the exact inputs/assumptions, period/currency/scope, formula and engine versions, source lineage, quality state and hashes. No forecast or solver may mutate that trace.

### Forecasting Engine

- Owns estimates about future periods, scenario assumptions, forecast snapshots and evaluation against later actuals. It does not rewrite historical facts or M5 measurements.
- Planned BFS owner: M17, after finance source contracts and an approved calculation/cash-flow definition. Forecast assumptions and actuals remain separately labeled.
- A candidate model is not “accurate” because it runs or passes code tests. Promotion is a separate authenticated human decision over a specific model/evaluation/evidence hash; failed experiments are retained, not erased.

### Optimization Engine

- Owns the choice among feasible alternatives under an explicit, approved objective and constraints. It consumes versioned calculation/forecast inputs; it is not a source of facts, accounting rules, business priorities or policy.
- A solver result is a proposal with solver/model version, input snapshot hash, objective and constraint versions, feasibility/status, objective value/bound/gap (when available), time limit, and a traceable solution. It never auto-writes employee assignments, cash policy or a financial decision.
- BFS independently recomputes every reported objective term and validates all hard constraints against the immutable snapshot. Persist runs append-only under tenant/company RLS. Decisions/approvals remain a distinct M7-style step.

### AI boundary

AI may summarize or explain a verified calculation/forecast/optimization result and link each material claim to authorized evidence. It may not perform authoritative arithmetic, invent objective weights, loosen constraints, set policy status, approve a decision, promote a champion, or override a solver's infeasible/unknown/timeout status. If required evidence, policy or solver proof is absent, the result must say so. This is **Evidence Before Answers — No evidence, no confident answer**.

## 3. Candidate decisions for proposed M22–M25

All four candidates are **NOT READY** on the reviewed BFS state. Variables and conditional objectives below specify what a future problem definition would need; they are not approved BFS policy or an authorization to optimize. Any weights, time granularity, risk terms or priorities must be selected by the accountable business owner, not inferred by AI or filled in by an engineer.

| Proposed milestone / candidate | Decision variables and objective that would need approval | Hard constraints / soft constraints | Required data and evidence | Output and validation contract | Solver if the gate passes; why simple rules may stop scaling | Readiness, effort, dependencies |
|---|---|---|---|---|---|---|
| **M22 — Workforce/resource allocation** | Candidate variables: `h[e,p,w]` = employee `e` hours allocated to project/work package `p` in week `w`, in an owner-approved discrete unit, plus `u[p,w]` unmet demand. Candidate objective: minimize approved weighted unmet demand plus approved change/churn penalty versus current commitments; weights and priority order are **unset**. | Hard: `sum_p h[e,p,w] <= confirmed_available[e,w]`; project weekly demand must be met or explicitly slack; ineligible skill/employee pairs are disallowed; agreed already-committed assignments are fixed or protected; employee/project/company scopes and period are consistent. Soft candidates: preference/continuity, project priority, load balance, minimum assignment size—but only if owners approve measurable definitions. | Fresh M5-style capacity and assignment evidence; a typed, versioned project/work-package demand model; required skills and employee eligibility; contract/leave/working-calendar rules; assignment completeness; project priority policy; source/freshness/quality state; human approval of the exact solution. Current BFS lacks project-required skills, full employee availability/calendar, priority policy and a complete source-backed allocation contract. | Immutable proposed allocation with per-employee/week hours, uncovered demand and reason, changed/locked assignment list, all hard-constraint checks, objective terms, evidence IDs/hashes, solver status/bounds/gap and reproducible model version. Recompute sums and constraints independently in BFS Decimal/integer-unit validation; compare tiny cases with exhaustive enumeration; require human disposition before any write. | **Conditional: CP-SAT** if time units and assignment choices are discrete/integer with Boolean eligibility or logical constraints; Google's official assignment examples use `cp_model`. If hours are truly continuous and the model is linear, test **Linear Solver/MIP** instead. Do not select both until the domain chooses one representation. A greedy rule can become inadequate only when coupled eligibility, conflicts, locks, time-phased demand and fairness constraints interact; the current M6 aggregate comparison is simple enough and remains appropriate. | **NOT READY.** Estimate **4–8 engineer-weeks** for one bounded allocation problem after gates. Depends on M13/M21 and workforce data/skill/availability contracts; M5 alone is insufficient. |
| **M23 — Employee/shift scheduling** | Candidate `x[e,s,d] ∈ {0,1}` for employee/shift/day assignments, plus explicit coverage slack if the owner allows it. Candidate objective: minimize approved understaffing, preference violations and schedule churn; no weights are approved. | Hard candidates: exact shift coverage bounds; no employee overlap; qualifications; reported availability; working-time/rest limits; maximum hours; contract/leave rules; locked shifts. Soft candidates: employee preferences, fairness/load balance, consecutive-day/undesirable-shift penalties. A soft preference must not weaken a legal/contractual hard rule. | Shift definitions and dates; shift requirements/coverage; approved employee availability and qualifications; jurisdiction-specific labor/contract constraints with owner; source lineage and freshness; employee preference/consent policy; complete staffing list. Current BFS has weekly capacity, not shift-level availability, work/rest rules, schedule preferences or staffing requirements. | Draft schedule, unfilled shifts, violated-soft-constraint totals, all hard constraints independently checked, source and policy versions, infeasible/feasible/optimal/time-limited status. Validate small rosters by exhaustive enumeration; save and expose human changes as new versions; never treat a plan as a posted schedule without authorization. | **Conditional: CP-SAT** for discrete shifts, Boolean assignments, sequence/rest and logical rules. A direct deterministic roster or manual scheduling is sufficient for a small fixed roster with simple independent slots; no evidence of BFS demand justifies the solver yet. | **NOT READY.** Estimate **5–9 engineer-weeks** for one jurisdiction/workflow after gates. Depends on M21, explicit scheduling source contracts, legally reviewed constraints and authorization. |
| **M24 — Operations scheduling, routing, or inventory** | **Do not combine these into one model.** Select one real problem first. For routing: arc/visit variables `y[v,i,j]`; for job-shop scheduling: task start/interval/machine choice; for inventory: replenishment amount `q[sku,t]` and optional order binary. Objective must be exactly one approved cost/priority (e.g., travel/cost, makespan/tardiness, or holding/shortage cost)—currently none exists. | Routing candidates: flow conservation, vehicle start/end, capacity, time windows, service duration. Job-shop: precedence, machine exclusivity, release/due dates. Inventory: stock conservation, lead times, order/minimum lots, capacity, service-level floor. Soft terms might include tardiness, route preference or inventory risk, with owner-approved prices/penalties. | A selected BFS operational domain; location/vehicle/customer/job/inventory IDs; source-backed demand; capacities, timing, costs and time zones; explicit feasibility rules; source as-of/freshness; independently testable baseline. BFS currently contains no general vehicles/routes, locations, machine schedules, inventory balance or replenishment facts. | Versioned route/schedule/replenishment proposal with feasible status, objective components, constraint slacks, all input and policy evidence, deterministic input hash and independent hard-constraint/objective recomputation; for routes, independently recompute arcs, distance/time/capacity; for schedules, precedence/machine/resource; for inventory, balance/lead-time. Human approval before external or operational execution. | **Conditional selection:** Google's **Routing** APIs for a genuine vehicle-routing problem; **CP-SAT** for discrete job-shop/sequence constraints; **Linear Solver/MIP** for a genuinely linear divisible inventory/flow model. Select only after narrowing to one objective/data contract. Deterministic sort/rules are fine for small independent tasks; combinatorial route/time-window/resource interactions could justify specialized search if observed scale/quality evidence shows they fail. | **NOT READY.** Estimate **6–12 engineer-weeks per single selected problem**, not all three. Depends on M21, domain owner, domain facts and independently validated cost/capacity inputs. |
| **M25 — Financial/investment optimization** | Candidate `z[i] ∈ {0,1}` for selecting an approved discrete project/investment, or `a[i]` in explicit integer currency units for a divisible budget. Candidate objective might maximize policy-approved NPV or another stated measure; BFS has no approved objective, probability, discount rate or NPV today. | Hard candidates: approved budget; cash/reserve floor per period; legal/contractual constraints; debt/covenant bounds; concentration/exposure limits; project prerequisites. Soft candidates: risk, strategic priority or diversification only where an accountable owner defines the measure and trade-off. | M14 finance source facts; M15 approved calculation policies; M16 cash forecast and/or M17 independently evaluated future estimates; explicit choices/eligibility; currency/period/FX policy; risk and budget limits; source evidence and finance approval. M8's project observations and always-incomplete assessment are insufficient. | Proposed alternatives with exact selected items/amounts, objective and each constraint term, input/formula/forecast versions, solver termination and gap, independent re-evaluation using BFS calculation logic, finance-owner and human approval. Store a proposal, not a financial fact or approval; do not write payments/investments. | **Conditional: CP-SAT** for discrete yes/no choices using approved integer-scaled coefficients; **Linear Solver/MIP** for continuous linear allocation if its numerical behavior is explicitly acceptable. Solver coefficients are not authoritative accounting arithmetic. Use M11/BFS Decimal for all inputs, outputs, and independent result checks; CP-SAT needs a declared integer scale, and LP/MIP floating tolerances need additional safeguards. A deterministic ranking can be sufficient when there is one constraint or a small number of scenarios; no BFS data justifies a solver now. | **NOT READY.** Estimate **6–10 engineer-weeks** for one narrowly approved financial decision, excluding finance-policy approval and source acquisition. Depends on M14–M17, M21, finance owner and verified history. |

### Why the current state is not ready

- M5/M6 answer a bounded comparison question, not a constrained allocation question. No code-backed acceptance test says a deterministic baseline fails, or that an optimizer improves a user decision.
- Employees have recorded current skill links, but BFS has no project-required skills or contract/time calendars to define eligibility and schedules. M5's weekly capacity does not replace those contracts.
- M12's six contracts cover existing M4 workforce/project imports; they do not supply all variables required above, and M12 implements no live connector/scheduler.
- M8 has no approved policy or financial metric. M10 is synthetic-only. M11 is arithmetic/replay, not a source of optimization constraints.
- No owner-approved objective weights, constraint priorities, fairness policy, infeasibility behavior, decision authority or solver acceptance baseline is recorded in the checked-in roadmap.

## 4. OR-Tools solver choice and adoption gates

Official Google references: [optimization guide](https://developers.google.com/optimization), [CP-SAT solver](https://developers.google.com/optimization/cp/cp_solver), [linear optimization](https://developers.google.com/optimization/lp), [integer optimization](https://developers.google.com/optimization/mip), [vehicle routing](https://developers.google.com/optimization/routing), [scheduling overview](https://developers.google.com/optimization/scheduling), and [Python installation](https://developers.google.com/optimization/install/python). The CP-SAT guide states its variables/constraints must be integer-valued (scale non-integer quantities explicitly) and documents `OPTIMAL`, `FEASIBLE`, `INFEASIBLE`, `MODEL_INVALID`, and `UNKNOWN` statuses. The MIP guide describes linear models with integer/Boolean variables, says MIP can fit standard LPs with arbitrary integer variables and CP-SAT can fit predominantly Boolean models, while noting no universal choice rule; network-flow solvers can be faster when the model fits that structure. The routing guide warns that solution time can grow rapidly with problem size and a returned route may be good but not proven optimal. Scheduling examples cover employee shifts and job shops. The Python install guide recommends the published pip package for ordinary installs; deployment/platform support must still be checked for BFS.

The official GitHub Python examples inspected at commit `4782dbd0aa85621c8115cf33de1574a2e6757004` include [`assignment_with_constraints_sat.py`](https://github.com/google/or-tools/blob/4782dbd0aa85621c8115cf33de1574a2e6757004/examples/python/assignment_with_constraints_sat.py), [`shift_scheduling_sat.py`](https://github.com/google/or-tools/blob/4782dbd0aa85621c8115cf33de1574a2e6757004/examples/python/shift_scheduling_sat.py), [`tasks_and_workers_assignment_sat.py`](https://github.com/google/or-tools/blob/4782dbd0aa85621c8115cf33de1574a2e6757004/examples/python/tasks_and_workers_assignment_sat.py), and [`linear_programming.py`](https://github.com/google/or-tools/blob/4782dbd0aa85621c8115cf33de1574a2e6757004/examples/python/linear_programming.py). The assignment example uses `ortools.sat.python.cp_model`; the tasks/workers example explicitly notes CP-SAT is integer-only; the LP example creates continuous `NumVar` variables and linear objective/constraints. These docs and examples establish API/model shape, not a BFS use case or solver-benefit claim.

| Solver family | Fit | BFS guardrail |
|---|---|---|
| **CP-SAT** | Discrete/integer variables, logical constraints, assignment, shift/job-shop scheduling. | Convert hours/costs to integer units only under an explicit declared scale. Record scale and transform; validate outputs independently. It is not a floating-point financial calculator. |
| **Linear Solver / LP / MIP** | Continuous linear allocation or mixed integer/linear models, when assumptions really are linear and divisibility is valid. | Solver APIs use numeric coefficients/tolerances; do not treat their doubles as BFS financial truth. Use only for the decision variables, then recompute all authoritative amounts independently. |
| **Routing** | Vehicle routing/path problems with visits, capacity and time-window structure. | Only after BFS has a source-backed route domain and routing objective. Not an all-purpose scheduling or capacity package. |

### M21 readiness gate (required before any M22–M25 implementation)

A candidate stays **NOT READY** until all the following are approved and evidenced:

1. Named user, concrete repeated decision, domain owner, scope, outcome and decision authority.
2. Versioned input contract with source-of-truth, units, period/time zone, provenance, quality/freshness, completeness, corrections and authorization; no placeholder-zero semantics.
3. Exact variables and a mathematically stated objective; each coefficient, weight, priority, unit and tie-break rule has an accountable owner. If the owner cannot explain the objective and trade-offs, do not optimize.
4. Explicit hard constraints and separately labeled soft constraints/penalties; legal/safety/finance policies cannot be softened by the solver.
5. Baseline deterministic algorithm/manual process and evidence of where it fails or becomes operationally inadequate. Compare solver benefit and complexity cost against that baseline.
6. Output contract for feasible, infeasible, unbounded, optimal, feasible-but-not-proven, timeout and solver-error outcomes; decide acceptable optimality gap/time limit and tie-breaking.
7. Independent verifier for every hard constraint and objective term; tiny instances with known/exhaustive optima; feasibility and adversarial tests; reproducibility and resource/time limits.
8. Immutable snapshot/version/hash, solver/version/config/time limit, status/bound/gap, tenant/company-scoped append-only storage, forced-RLS/API permission tests, and human review before any canonical write.
9. A single chosen candidate and solver family. Do not introduce a generic solver registry/framework for hypothetical future domains.

Passing M21 means the *decision problem is sufficiently specified for a separately approved implementation proposal*; it does not mean OR-Tools is already required or that an optimization milestone is complete.

## 5. Dependency and license decision

OR-Tools was not installed or added. At the audit-time [PyPI JSON metadata](https://pypi.org/pypi/ortools/json), the latest version reported was `9.15.6755`, with requirements `absl-py>=2.0.0`, `numpy>=2.0.2`, `pandas>=2.0.0`, `protobuf>=6.33.1,<6.34`, `typing-extensions>=4.12`, and `immutabledict>=3.0.0`. BFS's current [`pyproject.toml`](../../pyproject.toml) does not declare OR-Tools, NumPy, pandas or protobuf. Confirm platform wheels, Python/database deployment compatibility, solver-backend availability, security/license inventory and supported architectures at the time of a future decision; PyPI metadata can change.

The official OR-Tools GitHub repository reports **Apache-2.0**. If a later M22+ decision passes M21, review the license/NOTICE obligations for the exact pinned distribution, pin a tested version, include dependency/SBOM checks, and isolate solver calls behind a BFS-owned optimization boundary. No dependency change is authorized by this document.

## 6. Proposed M12–M25 allocation

The checked-in roadmap before this audit did not name an M12–M25 sequence; it explicitly described an M9 proposal and unnumbered future capabilities. The table in [`../roadmap/PRODUCT_ROADMAP.md`](../roadmap/PRODUCT_ROADMAP.md) is therefore a **proposed ownership map for the range requested here**, not evidence of prior approval or a claim that any future milestone is complete. It preserves M12's verified scope and does not authorize OR-Tools or code work.

| Milestone | Candidate ownership / gate | State and dependency |
|---|---|---|
| M12 | Existing company-data registry/evidence for six M4 datasets. | **Implemented and merged** at the verified PR #14 merge SHA. Not a live connector or scheduler. |
| M13 | Metrics & Insight Engine: extend M5/M12-backed, versioned read-only observations only for an approved question; no financial formula or recommendation by default. | **Proposed, not implemented.** Depends on data owner, metric definitions and evidence/quality scope. |
| M14 | Company finance source contracts, mapping, reconciliation and quality. | **Proposed.** New finance contract/version, never alter the meaning of M12's six M4 versions. Depends on source and finance owner. |
| M15 | BFS-native deterministic financial calculations using M11 Decimal/replay/verification. | **Proposed.** Requires M14 plus signed finance formula/policy and golden evidence. |
| M16 | Direct cash forecast (initially one 13-week scope). | **Proposed.** Requires M14 cash/receipts/disbursement evidence and approved timing/unknown rules; uses M15 only for an explicitly selected bridge. |
| M17 | Forecast snapshots, holdouts/scoring, experiment history and human model promotion. | **Proposed.** Requires adequate M14 history and M15/M16 definitions; no automatic promotion. |
| M18 | Export/reporting/workbook parity, if a named user needs Excel. | **Proposed/deferred.** Requires canonical M15/M16 output and formula/lineage requirements. |
| M19 | One approved connector at a time, fixture-first and manually invoked initially. | **Proposed/deferred.** Requires named source, access/secrets, mapping, retries/rate/retention and M14/M12 contracts; no scheduler implied. |
| M20 | Bounded, human-authored context/correction history; optional permission-aware explanation only under separate approval. | **Proposed/deferred.** Reuse M12/M7/M17 evidence rather than duplicating it; no AI arithmetic, model or vector-store choice. |
| M21 | Optimization readiness gate and candidate-specific objective/constraints/verifier. | **Proposed, planning gate only.** Does not install a solver. Must pass the nine conditions above for one candidate. |
| M22 | Workforce/resource allocation. | **NOT READY.** Conditional CP-SAT for discrete hours/eligibility or LP/MIP for approved divisible linear hours. |
| M23 | Employee/shift scheduling. | **NOT READY.** Conditional CP-SAT for discrete shift/sequence rules. |
| M24 | One operational scheduling, routing **or** inventory problem. | **NOT READY.** Choose CP-SAT, Routing or LP/MIP only after a concrete source/constraint model exists. |
| M25 | One finance/investment optimization problem. | **NOT READY.** Conditional integer CP-SAT or Linear Solver only after M14–M17 and finance policy are accepted; calculations remain BFS-owned. |

M13–M25 have no delivery dates and are not implementation authorization. Work estimates in this audit and roadmap are engineering effort ranges, not schedules, accounting/forecast performance guarantees, or a customer commitment.