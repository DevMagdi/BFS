# BusinessFlow — Historical Logical Entity Catalog Proposal

- **Status:** Historical logical schema proposal from the earlier project-feasibility hypothesis. It is not the current product direction, a migration plan, or an inventory of shipped tables; many entities, relationships, and policies below are not implemented.
- **Actual implementation (current branch):** The migration chain runs from `0001_backend_foundation` through `0017_core_financial_cash_transfers`, covering bounded M2–M8 slices, a separate synthetic-only M10 slice, M12's Company-scoped registry for existing M4 imports, M13 metric comparisons, M14's manual finance-specific source intake, experimental M15/M16 finance prototypes, and the default-disabled Core Financials six-measure workflow (general-ledger evidence for five P&L measures; bank activity for net observed Cash Flow movement). Migration 0017 permits explicitly marked bank transfers. Cash Flow is not a cash-flow statement and does not infer opening/closing balances. 0014 restricts runtime account creation; 0015 revokes runtime user-account updates. M9 was documentation-only and M11 is a database-free calculation/verification package. Core Financials policies remain illustrative and do not approve accounting policy or production/customer-data use. M10 remains an internal, explicitly seeded synthetic demo, not general finance or company-wide BI capability. M12 does not implement an external connector or scheduler. M14's production-source, correction, retention, finance-owner and independent-control gates remain open; M15/M16 do not approve accounting policy, forecast completeness/accuracy, or production use. Current schema and behavior are documented in [`BACKEND_IMPLEMENTATION.md`](BACKEND_IMPLEMENTATION.md), [`CORE_FINANCIALS.md`](CORE_FINANCIALS.md), [`M10_SYNTHETIC_DEMO.md`](M10_SYNTHETIC_DEMO.md), [`M12_COMPANY_DATA_INTELLIGENCE.md`](M12_COMPANY_DATA_INTELLIGENCE.md), [`M13_METRIC_INSIGHTS.md`](M13_METRIC_INSIGHTS.md), [`M14_FINANCE_SOURCE_CONTRACTS.md`](M14_FINANCE_SOURCE_CONTRACTS.md), [`M15_DETERMINISTIC_FINANCIAL_CALCULATIONS.md`](M15_DETERMINISTIC_FINANCIAL_CALCULATIONS.md), [`M16_DIRECT_CASH_FORECAST.md`](M16_DIRECT_CASH_FORECAST.md), and assessed against the M9 baseline in [`architecture/CURRENT_VS_TARGET_GAP_ANALYSIS.md`](architecture/CURRENT_VS_TARGET_GAP_ANALYSIS.md).
- **Tenant/company rule:** `Tenant` and `Company` are distinct, with exactly one company per tenant enforced in migration `0001_backend_foundation` by a unique company-per-tenant constraint and a deferred composite primary-company reference. This is implemented, not merely a proposal. It does not imply multi-company or consolidation support.
- **Historical scope:** The entity candidates and “MVP” labels below describe the former project-decision hypothesis. Their Must/Should/Out priorities in [`BRD.md`](BRD.md) and [`MVP_SCOPE.md`](MVP_SCOPE.md) are historical, not current product priorities. See the M9 [`product vision`](product/PRODUCT_VISION.md) and [`product roadmap`](roadmap/PRODUCT_ROADMAP.md).

## 1. Ownership and isolation conventions

- **A (Tenant boundary anchor):** the `tenant` root table. Its `id` is the isolation key; it does not carry a redundant `tenant_id` column. Restrict reads/writes using the verified tenant context, and apply an RLS policy against `id` where the table is included in the application runtime role.
- **G (global identity/catalog):** not Tenant-owned. Access is restricted through authenticated principals and application authorization. These are the other exceptions to the tenant `tenant_id` rule.
- **T (Tenant-owned):** carries `tenant_id NOT NULL`, RLS, tenant-aware foreign keys, and tenant-scoped uniqueness.
- **TC (Tenant + Company-owned):** carries `tenant_id NOT NULL` and `company_id NOT NULL`; composite FK `(tenant_id, company_id)` references Company. RLS first limits Tenant; application authorization checks Company/department/sensitive scope.
- **PK/FK convention:** UUID primary keys unless a stable global permission code or stated composite key is more appropriate. UUIDs do not grant access. Parent rows expose composite unique keys such as `(tenant_id,id)` and `(tenant_id,company_id,id)` for same-owner foreign keys.
- **Source records:** external IDs are unique only within `(tenant_id,data_source_id,external_id)`. Raw records are immutable; correcting a mapping creates a new mapping/run version, not an overwrite of original input.
- **Identity boundary:** this historical proposal suggested a Keycloak identity service, but no Keycloak integration is implemented or selected. Current M2 uses local password authentication, application sessions, and hashed refresh-token storage; MFA, password recovery, and external SSO are not implemented. See [`IDENTITY_AND_AUTHORIZATION.md`](IDENTITY_AND_AUTHORIZATION.md) and [`BACKEND_IMPLEMENTATION.md`](BACKEND_IMPLEMENTATION.md).
- Unless a row is explicitly identified as part of the actual M2–M8 subset, the entities, relationships, and rules below are candidates only. “MVP” labels reflect the earlier hypothesis, not current product priority or technical approval. The current direction and gates are in [`product/PRODUCT_VISION.md`](product/PRODUCT_VISION.md) and [`roadmap/PRODUCT_ROADMAP.md`](roadmap/PRODUCT_ROADMAP.md); implemented PostgreSQL/RLS behavior is documented in the backend guide.

## 2. Identity, tenancy and authorization entities

| Entity | Scope / MVP status | PK and important FKs | Isolation, uniqueness and integrity |
|---|---|---|---|
| `tenant` | A / MVP | `id`; `primary_company_id` → Company, deferred composite FK | Unique stable slug; exactly one primary Company row is required at commit and the pair is created atomically. Company lifecycle does not remove the 1:1 relationship; suspended/deleted semantics remain open. It is the isolation root, not a child tenant-owned row. |
| `company` | T / MVP | `id`; `tenant_id` → Tenant | `UNIQUE(tenant_id)` in MVP; `UNIQUE(tenant_id,id)` for composite FKs; currency/timezone/locale explicit; tenant pointer must reference its own company. |
| `user_account` | G / MVP | `id`; external principal identifiers are in ExternalIdentity | Unique normalized profile email only if product requires it, never authorization identity; disabled/erased status; no password field. |
| `external_identity` | G / MVP | `id`; `user_account_id` → UserAccount | `UNIQUE(issuer,subject)`; issuer canonicalized; one active identity cannot map to multiple users; explicit active/unlinked/revoked state and verification timestamp; email is profile/claim data, not key. |
| `identity_link_event` | G / MVP audit metadata | `id`; user, external identity, authenticated actor IDs | Append-only link/unlink/recovery-related proof outcome, time, reason and correlation; no proof secret/token. Matching email alone never links accounts; concurrent links must be serialized. |
| `application_session` | G / MVP | `id`; `user_account_id` → UserAccount | Unique high-entropy token hash; store only hash, created/last-seen/idle-expiry/absolute-expiry/revoked, auth strength and redacted client metadata; check active account and membership per request. |
| `identity_recovery_event` | G / MVP audit metadata | `id`; target user; optional actor | Record recovery initiation/completion/failure, timestamps and correlation; no recovery token, code or secret. Keycloak owns token lifecycle. |
| `tenant_membership` | T / MVP | `id`; `tenant_id`, `user_account_id`, `invited_by_user_id` | `UNIQUE(tenant_id,user_account_id)`; status/invitation/activation/revocation timestamps; membership is required for every user data request. |
| `access_role` | T / MVP | `id`; `tenant_id` | `UNIQUE(tenant_id,code)`; role definitions are tenant-scoped; immutable system templates may be copied into a Tenant, not used to bypass Tenant checks. |
| `permission` | G / MVP | Stable permission `code` | Global reviewed catalog; unique code; not assignable from a client or model. |
| `role_permission` | T / MVP | composite `(tenant_id,role_id,permission_code)` | FK role within same Tenant; permission FK to global catalog; unique triple. Changes are audited/versioned. |
| `membership_role` | T / MVP | `id`; `tenant_id`, `membership_id`, `role_id`; optional `company_id`, `department_id` | Same-Tenant FKs; Department implies its Company; check valid scope combinations; unique assignment per membership/role/scope. No polymorphic unvalidated scope ID. |
| `service_principal` | T / Deferred | `id`; `tenant_id` | Add only when a real connector/job needs a non-human identity; least privilege, rotation and distinct audit actor. No human impersonation. |

## 3. Organization, employees, skills and capacity

| Entity | Scope / MVP status | PK and important FKs | Isolation, uniqueness and integrity |
|---|---|---|---|
| `department` | TC / MVP | `id`; optional `parent_department_id`, `manager_employee_id` | Unique `(tenant_id,company_id,code)`; parent and manager must be same Company; no cycles; company/department visibility authorized separately. |
| `job_role` | TC / MVP | `id`; `tenant_id,company_id` | Unique `(tenant_id,company_id,code)`; business classification only, never an authorization role. |
| `employee` | TC / MVP | `id`; optional `department_id`, `job_role_id`, `manager_employee_id`, `user_account_id` | Employee remains distinct from User; all organization FKs same Company; employment dates/status constrained; profile/HR fields minimized and sensitive. |
| `employee_cost_rate` | TC / MVP for cost comparison | `id`; employee ID | Effective-dated, currency-explicit, compensation-protected; no overlapping approved effective intervals for same employee/cost basis; never default missing rate to zero. |
| `business_resource` | TC / MVP (employee resources first) | `id`; optional `employee_id` | Unique employee mapping within Company; type constrained. Contractor/subcontractor resource types deferred unless a pilot needs them. |
| `skill` | TC / MVP | `id`; `tenant_id,company_id` | Unique `(tenant_id,company_id,code)`; taxonomy/version owner; no inferred skill from job title. |
| `resource_skill` | TC / MVP | composite or UUID PK; resource and skill IDs | Both FKs same Company; unique resource/skill/effective-period; proficiency and validity explicitly sourced/reviewed. |
| `resource_capacity` | TC / MVP | `id`; `business_resource_id` | Same Company; unique resource/period/capacity basis; non-negative hours, timezone/calendar, approved/forecast status and source. |
| `resource_allocation` | TC / MVP | `id`; resource and project IDs | Both FKs same Company; period/skill/required hours/status; prevent duplicate active allocation keys and detect overlapping/overcommitted capacity in domain validation. |
| `business_unit` | TC / Deferred | `id`; optional parent ID | Add after real organizational need; same-Company FK, unique code, no cycles. Department can exist without it in MVP. |

## 4. Projects, contracts, time, commitments and financial facts

| Entity | Scope / MVP status | PK and important FKs | Isolation, uniqueness and integrity |
|---|---|---|---|
| `business_party` | TC / MVP | `id`; no assumed external ERP key | Unique external identifiers per source; customer/supplier roles attached separately; name matching does not merge automatically. |
| `party_role` | TC / MVP | composite or UUID PK; Party ID | Role is constrained (customer/supplier/both); unique `(tenant_id,company_id,party_id,role)`; same Company. |
| `project` | TC / MVP | `id`; optional customer Party ID; manager Employee ID | Unique `(tenant_id,company_id,project_code)`; proposed/active/closed state; date order; source status and deletion rules explicit. Proposed new projects are decision subjects, not accepted projects. |
| `contract` | TC / MVP (source-linked terms) | `id`; customer Party ID; optional source link | Same Company; contract currency and amount explicit; signed/proposed status, effective dates and payment terms; signed document remains source of truth; amendments are versioned. |
| `project_contract` | TC / MVP | composite `(tenant_id,company_id,project_id,contract_id)` | Both FKs same Company; unique pair; effective relationship dates and status. |
| `timesheet` | TC / MVP | `id`; employee ID | Same Company; reporting period and status; only approved entries contribute to actuals unless a separately approved forecast rule says otherwise. |
| `time_entry` | TC / MVP | `id`; timesheet, employee, project IDs | FKs same Company; work date, exact hours, source key, approval status; unique source entry per source; no duplicate/negative hours; approved cost rate effective on work date. |
| `commitment` | TC / MVP | `id`; optional project, resource, contract, supplier IDs | Same Company; type is financial or capacity; exactly the matching amount/currency or hours/period fields populated; status and effective interval; do not double-count allocations/timesheets. |
| `revenue_fact` | TC / MVP | `id`; project, optional contract and source link | Same Company; amount/currency/date/type/source state; recognized, planned, contract, invoiced and cash values are not interchangeable; source reference unique per source. |
| `expense_fact` | TC / MVP | `id`; optional project, supplier, contract, source link | Same Company; exact amount/currency/date/category/status; distinguish direct versus indirect only through approved policy; external source identity unique per source. |
| `outsourcing_quote` | TC / Conditional MVP scenario input | `id`; project/decision case, supplier Party ID | Same Company; quote currency, scope, quantity/rate/fixed amount, issue/expiry/status and evidence; only current, comparable quotes count as validated. |
| `invoice` | TC / MVP for collection evidence | `id`; customer Party ID; optional project/contract | Same Company; unique invoice number per source/account; issue/due/status, totals/currency; invoice is not recognized revenue or cash. |
| `invoice_line` | TC / MVP | `id`; invoice and optional project IDs | Same Company; exact quantity/amount/currency/tax fields; cannot attach a line to a project in another Company. |
| `payment_receipt` | TC / MVP for collection evidence | `id`; invoice ID | Same Company; exact received amount/currency/date/source; idempotent source key; partial/late/overpayment states explicit. |
| `cash_position` | TC / Optional MVP input | `id`; source ID | Same Company; as-of timestamp, amount/currency/source/freshness; point-in-time observation, not bank account or payment authorization. |

## 5. Data sources, imports, normalization and reconciliation

| Entity | Scope / MVP status | PK and important FKs | Isolation, uniqueness and integrity |
|---|---|---|---|
| `data_source` | T, optional Company scope / MVP | `id`; optional `company_id` | Tenant-owned; unique `(tenant_id,source_type,external_account_key)`; stores credential reference, never secret; least-privilege configuration and enabled state. |
| `import_batch` | T / MVP | `id`; source ID; actor user/service ID | Same Tenant; idempotency key unique per source; file hash, lifecycle/status, counts, mapping version, timestamps; a retry returns the same batch or a new explicit version. |
| `raw_record` | T / MVP | `id`; batch ID | Immutable received bytes/JSON or secure blob locator, row/cursor, source key and checksum; unique `(tenant_id,batch_id,source_record_key)`; strict retention/access policy. |
| `field_mapping_version` | T / MVP | `id`; source ID; created/approved by | Same Tenant; immutable version with source and canonical field map, type rules and effective timestamp; unique `(tenant_id,source_id,version)`; activation is explicit. |
| `normalization_run` | T / MVP | `id`; batch and mapping version IDs | Same Tenant; immutable transform/runtime version, start/end, counts and status; unique idempotency key per input+mapping version. |
| `normalized_record` | T / MVP | `id`; raw record and normalization run IDs | Same Tenant; typed fields plus explicit error/status; one run may not overwrite another; promotable only when validated. |
| `reconciliation_finding` | T / MVP | `id`; batch/normalized record and optional canonical IDs | Same Tenant; type, severity, evidence, status, resolver and timestamp; unresolved critical conflicts block dependent calculations; resolution is audited. |
| `source_entity_link` | T / MVP | `id`; source ID, normalized record, canonical entity reference | Same Tenant; strict type allow-list or typed link tables, same-owner integrity; unique `(tenant_id,source_id,external_id,entity_type)`; no arbitrary unvalidated polymorphic FK. |

The tables in this section remain a logical proposal. M12 implements a smaller, differently scoped set: a global migration-seeded `company_data_contracts` catalog plus Company-scoped `company_data_sources` registrations and `company_data_ingestions` snapshots bound to existing M4 imports. M14 separately implements a finance-only migration-seeded contract and manual CSV intake with Company-scoped mapping, intake, and row-hash evidence; it is not a general connector, raw-record store, normalization pipeline, or part of M12's workforce/project registry. These names do not represent an external `data_source` connector, credential store, `import_batch`, general mapping/normalization pipeline, or scheduler. See [`M12_COMPANY_DATA_INTELLIGENCE.md`](M12_COMPANY_DATA_INTELLIGENCE.md) and [`M14_FINANCE_SOURCE_CONTRACTS.md`](M14_FINANCE_SOURCE_CONTRACTS.md) for the respective actual schemas, lifecycle, and limitations.

## 6. Metrics, decisions and evidence

| Entity | Scope / MVP status | PK and important FKs | Isolation, uniqueness and integrity |
|---|---|---|---|
| `metric_definition` | T / MVP for approved project metrics | `id`; owner principal ID | Same Tenant; unique `(tenant_id,metric_code,version)`; unit, dimensions, formula contract, currency semantics, input policy and review status. No user/model SQL. |
| `calculation_version` | T / MVP | `id`; metric definition ID | Same Tenant; immutable implementation/rule version, effective dates and approver; no overwrite; each metric run references exactly one version. |
| `metric_run` | T / MVP | `id`; company, calculation version, input snapshot IDs | Same Tenant and Company; immutable input snapshot, run timestamp, quality state and result hash; idempotency key for deterministic reruns. |
| `metric_observation` | TC / MVP | `id`; metric run and definition IDs | Same Company; period/value/amount/unit/currency/quality/lineage; unique key per run/dimension/period; exact numeric values. |
| `decision_case` | TC / MVP | `id`; requester ID; optional subject project/customer | Same Tenant and Company; question/type/status, source snapshot, created-by, timestamps; no cross-company subject links. |
| `scenario_version` | TC / MVP | `id`; decision case ID; optional parent version | Same Company; immutable option, assumptions, inputs, calculation versions, results and limitations; unique case/option/version; edits create new rows. |
| `recommendation` | TC / MVP | `id`; decision case and scenario version IDs | Same Company; immutable version, status, preferred option only if policy gates pass; calculation/policy version, data-quality state, author/provider metadata. |
| `evidence_snapshot` | TC / MVP | `id`; decision/recommendation/metric references | Same Tenant and Company; immutable evidence locator, source IDs, hashes, timestamps and transform/formula version; no unredacted source secrets; protects snapshot integrity. |

## 7. Approval, action, outcome and audit

| Entity | Scope / MVP status | PK and important FKs | Isolation, uniqueness and integrity |
|---|---|---|---|
| `policy_version` | T / MVP | `id`; author/approver principals | Same Tenant; immutable validated declarative rule, version/effective interval; unique `(tenant_id,policy_code,version)`; no arbitrary executable user/model code. |
| `approval_request` | TC / MVP | `id`; decision case, recommendation/scenario or action ID; policy version | Same Tenant/Company; target type must be an allow-listed exact target; immutable payload hash and expiry; a revision invalidates prior request. |
| `approval_decision` | TC / MVP | `id`; approval request ID; authenticated approver user ID | Same Tenant/Company; append-only decision/reason/time; approver must hold permission and satisfy approved separation-of-duties policy; never accepts caller-supplied actor identity. |
| `action` | TC / MVP only for approved internal follow-up | `id`; decision case, approval request, requester and assignee | Same Tenant/Company; immutable payload hash and idempotency key unique per scope; state transition constraints; external connector targets deferred. |
| `action_attempt` | TC / MVP | `id`; action ID; worker/service actor | Same Tenant/Company; unique attempt sequence; timestamps, failure class and redacted metadata; retries reuse action/idempotency identity. |
| `internal_followup_task` | TC / MVP first controlled action | `id`; decision case/action, assignee membership/user | Same Tenant/Company; unique action/task mapping; reversible by cancel/close with reason, not deletion; assignee must be active member in Tenant. |
| `action_verification` | TC / MVP | `id`; action and attempt IDs; verifier actor | Same Tenant/Company; separate fresh read-back result, timestamp, expected/observed payload hash; only successful verification marks action verified. |
| `outcome_measurement` | TC / MVP after decision pilot | `id`; decision case, recommendation, optional action IDs | Same Tenant/Company; period, actual/expected values, formula version and evidence; append-only; no retroactive rewrite or unsupported causality claim. |
| `audit_event` | T / MVP | `id`; actor and optional target references | Tenant-owned; append-only to runtime role, actor type/ID, action/target/time/request ID/outcome, redacted metadata; unique event ID; DB admins remain capable of alteration, so do not claim tamper-proof storage. |
| `business_event` | TC / Deferred unless required for import state | `id`; Company/source references | Same Tenant/Company; typed operational fact, distinct from audit; provenance required. |

## 8. Historical proposal: deferred from its first slice

The following exclusions belonged to the former project-feasibility hypothesis and are not current roadmap priorities: business-unit hierarchies; payroll, hiring execution, employee-management workflows; contractor as a capacity resource; budgets/goals/goal-KPI; generalized KPI catalog beyond project decision metrics; broad risk-management register; full industry-pack/plugin system; multi-company selection/consolidation; general purpose service principals; vector search; arbitrary ERP schema mirroring; outgoing accounting/payment/bank operations; external project creation; and high-impact actions. Any future inclusion requires a fresh product and owner decision under the M9 roadmap.

## 9. Future schema-design gate

This catalog is not a migration plan. The bounded M2–M8, M10 and M12–M14 implementations are documented in [`BACKEND_IMPLEMENTATION.md`](BACKEND_IMPLEMENTATION.md); the table catalog above does not describe the complete current schema. Before adding any future domain schema, select a concrete use case, approve a typed data contract and ownership, define access/retention/correction rules, and convert only the necessary constraints into reviewed PostgreSQL migrations and ORM models. Test tenant/company references, forced RLS, runtime grants, pooling, idempotency, immutable versions, and domain-specific state transitions against real PostgreSQL. M9 made no application-code, API, model, or migration changes. M12's registry is not an implementation of every proposed ingestion entity or an authorization to mirror arbitrary ERP schemas; M14's separate bounded intake does not authorize production/customer accounting-data processing.
