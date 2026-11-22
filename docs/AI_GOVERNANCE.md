# BusinessFlow AI — AI Governance and Decision-Support Boundaries

- **Status:** Product/security proposal for owner review; no model provider, SDK, vector store or AI runtime is selected or implemented.
- **Product constraint:** AI may help users ask questions, map data and understand results; BusinessFlow-owned canonical facts, financial calculations, authorization, approval and execution remain deterministic and server-controlled.

## Foundational principle: Evidence Before Answers — No evidence, no confident answer

Every current or future BusinessFlow AI response must be designed to be:

1. **Evidence-grounded:** linked to relevant company data and source records.
2. **Traceable and verifiable:** supporting evidence and calculations can be inspected.
3. **Transparent:** assumptions, limitations, uncertainty, and missing information are explicit.
4. **Permission-aware:** retrieval and answers respect the requesting user’s authorization boundaries.
5. **Honest about insufficient data:** abstain from confident conclusions when evidence is incomplete or unreliable.
6. **Reproducible:** important assessments and decisions preserve the relevant input, calculation, policy, and evidence versions.

This is an approved target product requirement, not an implemented AI feature. The implemented M2–M8 slices provide only bounded source links, authorization, deterministic calculations, and versioned evidence in specific workflows; no AI runtime or permission-aware AI retrieval exists.

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

Local/on-prem inference is a target architectural requirement for any future, separately approved AI capability so a customer can keep sensitive data within its controlled environment; this is not implemented. No specific LLM, inference runtime, provider, vector database, or retrieval service is selected. Any external model API is optional, never a prerequisite, and requires separate data-flow, privacy, security, cost, dependency/license, and owner review. The core must work for structured deterministic analysis without an LLM.

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
- If an AI use case is separately approved, select a local/on-prem inference runtime that meets the target requirement; separately decide whether any external model is optional and permitted. Determine cloud/on-prem/air-gap implications without making an external provider a prerequisite.
- Approve providers, regions, contracts, retention/training settings, credentials, deletion and outage/fallback policy.
- Define prompt/template and model versioning, audit metadata, human review and customer disclosure requirements.
- Determine multilingual evaluation scope and acceptance thresholds with design partners; no language or locale is inferred from the owner's approximate location.

## 6. Company-wide decision-support boundary

For any selected company domain, AI may not fill missing values, create financial or operational calculations, invent a threshold/policy, resolve disputed facts, change a deterministic result, or select/approve an option when the service abstains. Any future explanation receives only authorized, minimized evidence and must preserve the approved measure, unit, currency, period, source, assumptions, version, and limitations.

The earlier project-specific inputs and candidate formulas in [`MVP_DECISION_MODEL.md`](MVP_DECISION_MODEL.md) are historical proposals, not a universal platform contract. Company-wide domain selection and any financial policy remain review gates, not implementation decisions. No AI dependency, provider, vector store, agent framework or model endpoint has been added.
