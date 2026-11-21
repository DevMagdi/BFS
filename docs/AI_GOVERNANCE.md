# BusinessFlow AI — AI Governance and Decision-Support Boundaries

- **Status:** Product/security proposal for owner review; no model provider, SDK, vector store or AI runtime is selected or implemented.
- **Product constraint:** AI may help users ask questions, map data and understand results; BusinessFlow-owned canonical facts, financial calculations, authorization, approval and execution remain deterministic and server-controlled.

## 1. Allowed and prohibited responsibilities

| AI may assist with (proposal) | AI must not control or claim |
|---|---|
| Translate a user question into an approved structured query/intent; ask for clarification where ambiguous. | The source of truth for revenue, cost, margin, capacity, cash, currency conversion or other financial metrics. |
| Explain or summarize a deterministic result using permitted evidence and assumptions. | Tenant selection, membership, permissions, sensitive-data access, approval identity or execution authorization. |
| Suggest candidate import mappings or data-quality labels for a human to review. | Silent promotion/repair of imported records, resolving disputed facts, or inventing missing values. |
| Help users explore scenarios by filling explicitly labeled assumptions that are validated and confirmed before calculation. | Choosing a scenario based on fabricated evidence or autonomously taking a high-impact/irreversible action. |
| Provide language assistance over content the requesting principal is authorized to access. | Arbitrary SQL, direct database credentials, unconstrained tools, policy changes or approval bypass. |

If inputs required for the selected analysis are missing, contradictory, stale, source-unauthorized or untrusted, the system must state the limitation or return `insufficient_reliable_data`. If the caller lacks permission, return `not_authorized` without exposing protected facts. A fluent explanation is not proof that the underlying analysis is correct.

## 2. Request and evidence flow

1. Authenticate the user and establish tenant/company/resource authorization through the backend.
2. Interpret a natural-language question only into a bounded, schema-validated intent; do not forward the user's tenant or permissions as trusted model claims.
3. Run a deterministic BusinessFlow query/metric/scenario service over approved data and policies.
4. Attach versioned evidence references, data-quality status, calculation/rule version, assumptions and freshness metadata to the result.
5. If a model generates an explanation, give it only the minimum authorized and redacted evidence needed; validate its output and ensure numeric claims match the deterministic result.
6. Record relevant model/provider, prompt/template, policy and calculation versions without logging credentials, unnecessary personal data or unrestricted source documents.
7. Reauthorize every tool call server-side. The model cannot approve or execute the resulting action; a separately authenticated human and the action policy are required.

Imported documents, spreadsheet cells, user prompts, retrieved content and connector responses are untrusted data, not privileged instructions. Prompt-injection text must not alter system policy, tenant scope, authorization, financial formulas or tool permissions.

## 3. Data protection and deployment constraints

The self-hosted/on-prem-first direction makes data-flow control a product requirement. Before any external model provider is used, classify the data that could leave the customer environment and explicitly decide provider, region, retention/training terms, customer consent, redaction, encryption, deletion and outage behavior. Do not transmit compensation, credentials, full source files or personal data unless a documented use case and authorization explicitly permit it.

A local model, external API, vector database or retrieval service is not presumed. Each adds different quality, privacy, security, cost and operating trade-offs and needs a separate decision and dependency/license/security review. The core should work for structured deterministic analysis without an LLM.

## 4. Evaluation and monitoring gates

Before any AI-assisted feature is released, build a versioned, domain-owner-reviewed evaluation set and test:

- Question/intent mapping against intended metrics, filters and relationships; ambiguous questions must request clarification.
- Numeric fidelity: generated explanations must match exact deterministic values, units, currency and period; no model-generated number may replace a computed result.
- Evidence attribution: each material statement links to authorized source/calculation evidence or is labeled as an assumption/opinion.
- Abstention: stale, missing, conflicting, low-quality or unauthorized data yields an explicit limitation rather than a confident recommendation.
- Prompt injection and data exfiltration: untrusted imports and retrieved content cannot alter instructions, access another tenant, expose secrets or invoke unapproved tools.
- Authorization parity: the model-assisted route cannot reveal data or perform an operation that the same user could not request through the direct API.
- Multilingual/business terminology, latency, availability, cost and provider failure behavior for the languages and customer populations the product actually supports.

Track errors, unsupported claims, refusal/abstention quality, user corrections and model/provider versions. These signals inform improvement but do not establish causality or financial correctness. Disable or roll back an AI feature if evaluations regress; deterministic workflows must remain available.

## 5. Decisions before model integration

- Select supported AI use cases and define what may be sent to a model by data classification.
- Choose external, customer-hosted or no model inference for each deployment mode; determine the cloud/on-prem/air-gap implications.
- Approve providers, regions, contracts, retention/training settings, credentials, deletion and outage/fallback policy.
- Define prompt/template and model versioning, audit metadata, human review and customer disclosure requirements.
- Determine multilingual evaluation scope and acceptance thresholds with design partners; no language or locale is inferred from the owner's approximate location.

## 6. Project-acceptance decision and abstention contract

The proposed minimum inputs, candidate calculation framework, finance-owner approvals, scenario versioning and `insufficient_reliable_data` output are specified in [`MVP_DECISION_MODEL.md`](MVP_DECISION_MODEL.md). AI cannot fill a missing cost rate, effort estimate, FX rate, collection probability, threshold, or accounting rule; it cannot change a deterministic output or choose an option when the calculation service abstains. If used to explain a result, it receives only authorized, minimized evidence and must preserve units, currency, period, formula version, assumptions and limitations.

These remain review gates, not implementation decisions. No AI dependency, provider, vector store, agent framework or model endpoint has been added.
