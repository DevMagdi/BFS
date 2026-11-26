(() => {
  "use strict";

  const API_BASE = "/api/v1";
  const PAGE_SIZE = 100;
  const SESSION_KEYS = {
    access: "businessflow.m10.access",
    refresh: "businessflow.m10.refresh",
    tenant: "businessflow.m10.tenant",
  };
  const DATASET_LABELS = {
    employees: "Employees",
    skills: "Skills",
    employee_skills: "Employee skills",
    weekly_capacity: "Weekly capacity",
    projects: "Projects",
    resource_assignments: "Resource assignments",
    customer_commitments: "Customer commitments (manual)",
  };
  const M4_DATASET_TYPES = new Set([
    "employees", "skills", "employee_skills", "weekly_capacity", "projects", "resource_assignments",
  ]);
  const COMMITMENT_STATUS_LABELS = {
    draft: "Draft",
    submitted: "Submitted",
    changes_requested: "Changes requested",
    accepted: "Accepted",
    rejected: "Rejected",
  };
  const state = {
    accessToken: sessionStorage.getItem(SESSION_KEYS.access),
    refreshToken: sessionStorage.getItem(SESSION_KEYS.refresh),
    user: null,
    tenants: [],
    tenant: null,
    canWrite: false,
    canCommitmentWrite: false,
    tenantDataReady: false,
    loadSequence: 0,
    contracts: [],
    coverage: null,
    sources: [],
    sourceTotal: 0,
    imports: [],
    importTotal: 0,
    commitments: [],
    commitmentTotal: 0,
    commitmentFilter: "",
    selectedCommitment: null,
    commitmentHistory: [],
    commitmentHistoryOffset: 0,
    commitmentHistoryTotal: 0,
    universalSources: [],
    universalSourceTotal: 0,
    universalLoadError: null,
    universalEntities: [],
    universalMappings: [],
    universalMappingDraft: null,
    universalCurrent: null,
    universalReviewSequence: 0,
    universalIngestionsSourceId: null,
    universalProfileHistorySourceId: null,
    universalProfileHistory: [],
    universalProfileHistoryTotal: 0,
    universalRecordFilter: null,
    universalRecordLineageId: null,
    universalRowsOffset: 0,
    universalRowsTotal: 0,
    universalIngestions: [],
    universalIngestionTotal: 0,
    universalRecords: [],
    universalRecordTotal: 0,
    universalUploadAttempt: null,
    universalSyncAttempt: null,
    messageTimer: null,
  };

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  function node(tag, className = "", text = null) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== null && text !== undefined) element.textContent = String(text);
    return element;
  }

  function apiErrorMessage(data, status) {
    if (data && data.error && typeof data.error.message === "string") return data.error.message;
    return status ? `The request could not be completed (HTTP ${status}).` : "The request could not be completed.";
  }

  class ApiFailure extends Error {
    constructor(message, status, code = "request_failed") {
      super(message);
      this.name = "ApiFailure";
      this.status = status;
      this.code = code;
    }
  }

  function showSignedOut() {
    $("#auth-required").hidden = false;
    $("#empty-workspace").hidden = true;
    $("#data-foundation").hidden = true;
  }

  const api = window.BusinessFlowApiClient.create({
    apiBase: API_BASE,
    state,
    sessionStorage,
    sessionKeys: SESSION_KEYS,
    ApiFailure,
    apiErrorMessage,
    onAuthCleared: showSignedOut,
  });
  const { clearAuth, request, requestBlob } = api;

  function setStatus(message, kind = "muted") {
    const target = $("#workspace-status");
    target.textContent = message;
    target.classList.remove("is-error", "is-success");
    if (kind === "is-error" || kind === "is-success") target.classList.add(kind);
  }

  function showMessage(message, kind = "error") {
    const box = $("#global-message");
    box.textContent = message;
    box.classList.toggle("is-success", kind === "success");
    box.hidden = false;
    window.clearTimeout(state.messageTimer);
    state.messageTimer = window.setTimeout(() => { box.hidden = true; }, 9000);
  }

  function dateTimeText(value) {
    if (!value) return "Not recorded";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return String(value);
    return new Intl.DateTimeFormat(undefined, {
      year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    }).format(parsed);
  }

  function shortHash(value) {
    if (!value) return "Not recorded";
    return `${String(value).slice(0, 16)}…`;
  }

  function captureWorkspace() {
    return { tenantId: state.tenant?.id || null, sequence: state.loadSequence };
  }

  function workspaceIsCurrent(context) {
    return Boolean(
      context?.tenantId && state.tenant?.id === context.tenantId &&
      state.loadSequence === context.sequence,
    );
  }

  function setTenantCaption() {
    const tenant = state.tenant;
    $("#company-caption").textContent = tenant
      ? `${tenant.name} · ${tenant.company?.legal_name || "Company"} · ${tenant.company?.base_currency || ""}`
      : "No Company workspace is selected.";
  }

  function renderTenantPicker() {
    const picker = $("#company-picker");
    picker.replaceChildren();
    for (const tenant of state.tenants) {
      const option = node("option", "", `${tenant.name} · ${tenant.company?.legal_name || "Company"}`);
      option.value = tenant.id;
      option.selected = tenant.id === state.tenant?.id;
      picker.append(option);
    }
    $("#company-picker-wrap").hidden = state.tenants.length < 2;
    setTenantCaption();
  }

  function updateRoleUi() {
    const roleCanWrite = ["owner", "admin"].includes(state.tenant?.role);
    state.canWrite = roleCanWrite && state.tenantDataReady;
    state.canCommitmentWrite = Boolean(state.tenantDataReady && state.tenant);
    const notice = $("#role-notice");
    notice.textContent = !state.tenantDataReady
      ? "Loading Company permissions and evidence. Write actions stay disabled until the selected workspace is loaded."
      : roleCanWrite
        ? "Owner/admin write access is active for M12 source registration, Universal Data review/promotion, and M4 import evidence. The server remains authoritative for every permission check."
        : "M12 and Universal Data are read-only for members; M4 import recording is also owner/admin-only. Active members can create and submit their own manual customer-commitment drafts; only an owner/admin can review them.";
    notice.hidden = state.canWrite;
    for (const selector of ["#source-form", "#ingestion-form", "#ud-source-form", "#ud-upload-form", "#ud-sync-form", "#ud-mapping-form"]) {
      for (const field of $$("input, select, textarea, button", $(selector))) field.disabled = !state.canWrite;
    }
    const universalIngestion = state.universalCurrent?.detail?.ingestion;
    $("#ud-validate-button").disabled = !state.canWrite || !universalIngestion ||
      universalIngestion.status === "promoted" || !universalIngestion.mapping_profile_version_id;
    const needsUniversalAcknowledgement = Boolean(universalIngestion &&
      Number(universalIngestion.counts.warning_rows || 0) + Number(universalIngestion.counts.duplicate_rows || 0) > 0);
    $("#ud-promote-button").disabled = !state.canWrite || !universalIngestion ||
      !["validated", "review_required"].includes(universalIngestion.status) ||
      (needsUniversalAcknowledgement && !$("#ud-acknowledge").checked);
    $("#ud-acknowledge").disabled = !state.canWrite;
    $("#ud-correction-reason").disabled = !state.canWrite;
    const commitmentNotice = $("#commitment-role-notice");
    commitmentNotice.textContent = !state.tenantDataReady
      ? "Loading Company permissions. Manual workflow actions stay disabled until the selected workspace is loaded."
      : roleCanWrite
        ? "Active members can create and correct their own drafts. Owner/admin reviewers can accept, request correction, or reject; the server enforces all actions."
        : "You can create, edit, and submit your own drafts. An owner/admin must independently review submissions; the server enforces Company scope and every transition.";
    commitmentNotice.hidden = !state.tenantDataReady;
    $("#new-commitment-button").disabled = !state.canCommitmentWrite;
    $("#commitment-status-filter").disabled = !state.tenantDataReady;
    updateCommitmentEditorUi();
  }

  function clearTenantWorkspace() {
    state.tenantDataReady = false;
    state.contracts = [];
    state.coverage = null;
    state.sources = [];
    state.sourceTotal = 0;
    state.imports = [];
    state.importTotal = 0;
    state.commitments = [];
    state.commitmentTotal = 0;
    state.commitmentFilter = "";
    state.selectedCommitment = null;
    state.commitmentHistory = [];
    state.commitmentHistoryOffset = 0;
    state.commitmentHistoryTotal = 0;
    state.universalSources = [];
    state.universalSourceTotal = 0;
    state.universalLoadError = null;
    state.universalEntities = [];
    state.universalMappings = [];
    state.universalMappingDraft = null;
    state.universalCurrent = null;
    state.universalReviewSequence += 1;
    state.universalIngestionsSourceId = null;
    state.universalProfileHistorySourceId = null;
    state.universalProfileHistory = [];
    state.universalProfileHistoryTotal = 0;
    state.universalRecordFilter = null;
    state.universalRecordLineageId = null;
    state.universalRowsOffset = 0;
    state.universalRowsTotal = 0;
    state.universalIngestions = [];
    state.universalIngestionTotal = 0;
    state.universalRecords = [];
    state.universalRecordTotal = 0;
    state.universalUploadAttempt = null;
    state.universalSyncAttempt = null;
    $("#commitment-status-filter").value = "";
    clearCommitmentEditor();
    for (const selector of ["#ud-source-form", "#ud-upload-form", "#ud-sync-form", "#ud-mapping-form"]) {
      $(selector).reset();
    }
    $("#coverage-evaluated-at").textContent = "";
    $("#coverage-note").textContent = "";
    $("#coverage-grid").replaceChildren(node("p", "df-empty", "Loading evidence for the selected Company…"));
    $("#source-list").replaceChildren(node("p", "loading", "Loading Company data evidence…"));
    $("#commitment-list").replaceChildren(node("p", "loading", "Loading customer commitments…"));
    $("#commitment-list-load-more").hidden = true;
    $("#commitment-list-load-more").disabled = true;
    $("#evidence-panel").hidden = true;
    $("#evidence-content").replaceChildren();
    $("#ud-review-panel").hidden = true;
    $("#ud-ingestion-summary").replaceChildren();
    $("#ud-inspection").replaceChildren();
    $("#ud-validation-summary").replaceChildren();
    $("#ud-mapping-fields").replaceChildren();
    $("#ud-existing-mapping").replaceChildren(node("option", "", "Create a new mapping version"));
    $("#ud-existing-mapping").options[0].value = "";
    $("#ud-row-list").replaceChildren();
    $("#ud-ingestion-list").replaceChildren(node("p", "df-empty", "Select a source to review its runs."));
    $("#ud-record-list").replaceChildren(node("p", "df-empty", "No canonical records loaded."));
    $("#ud-record-count").textContent = "";
    $("#ud-record-lineage").replaceChildren();
    $("#ud-record-lineage").hidden = true;
    $("#ud-profile-history").replaceChildren();
    $("#ud-profile-history").hidden = true;
    $("#ud-status").textContent = "";
    $("#global-message").hidden = true;
    $("#global-message").textContent = "";
    renderDatasetOptions();
    renderSourceOptions();
    renderUniversalData();
    updateRoleUi();
  }

  function appendCountRow(parent, title, counts, entries) {
    const group = node("div", "df-count-group");
    group.append(node("p", "df-count-title", title));
    const row = node("div", "df-count-row");
    for (const [label, ...keys] of entries) {
      const cell = node("div", "df-count-cell");
      const value = keys.reduce((total, key) => total + Number(counts?.[key] || 0), 0);
      cell.append(node("span", "", label), node("strong", "", String(value)));
      row.append(cell);
    }
    group.append(row);
    parent.append(group);
  }

  function renderCoverage() {
    const target = $("#coverage-grid");
    target.replaceChildren();
    const coverage = state.coverage;
    if (!coverage?.datasets?.length) {
      target.append(node("p", "df-empty", "No published M4 contract coverage is available."));
      return;
    }
    $("#coverage-evaluated-at").textContent = `Evaluated ${dateTimeText(coverage.evaluated_at)}`;
    $("#coverage-note").textContent = coverage.note || "Coverage is limited to evidence registered for this Company.";
    for (const dataset of coverage.datasets) {
      const card = node("article", "df-coverage-card");
      const heading = node("div", "df-coverage-heading");
      heading.append(
        node("h3", "", DATASET_LABELS[dataset.dataset_type] || dataset.dataset_type),
        node("span", "df-source-count", `${dataset.registered_source_count} registered source${dataset.registered_source_count === 1 ? "" : "s"}`),
      );
      card.append(heading);
      card.append(node("p", "df-contract-line", `${dataset.sources_without_ingestion_count} source(s) without M12 ingestion evidence`));
      appendCountRow(card, "Latest ingestion", dataset.latest_ingestion_counts, [
        ["Succeeded", "succeeded"], ["With warnings", "succeeded_with_warnings"], ["Blocked", "blocked"],
      ]);
      appendCountRow(card, "Latest quality", dataset.latest_quality_counts, [
        ["Passed", "passed"], ["Warning", "warning"], ["Failed", "failed"],
      ]);
      appendCountRow(card, "Current freshness", dataset.current_freshness_counts, [
        ["Fresh", "fresh"], ["Stale", "stale"],
        ["Policy / as-of missing", "policy_missing", "source_as_of_missing"], ["Future as-of", "future_as_of"],
      ]);
      if (dataset.contract_versions.length) {
        for (const contract of dataset.contract_versions) {
          const line = node("p", "df-contract-line");
          line.append(node("code", "", `${contract.contract_key} · v${contract.version}`));
          line.append(document.createTextNode(` · ${shortHash(contract.definition_hash)}`));
          card.append(line);
        }
      } else {
        card.append(node("p", "df-contract-line", "No published contract version is recorded."));
      }
      if (dataset.latest_ingestion_at) {
        card.append(node("p", "df-contract-line", `Latest source snapshot recorded ${dateTimeText(dataset.latest_ingestion_at)}.`));
      }
      target.append(card);
    }
  }

  function renderContractOptions() {
    const selectedDataset = $("#source-dataset").value;
    const versions = state.contracts.filter((contract) =>
      contract.dataset_type === selectedDataset && M4_DATASET_TYPES.has(contract.dataset_type));
    const select = $("#source-contract");
    select.replaceChildren();
    const placeholder = node("option", "", versions.length ? "Choose a published version…" : "No contract available");
    placeholder.value = "";
    select.append(placeholder);
    for (const contract of versions) {
      const option = node("option", "", `${contract.contract_key} · v${contract.version}`);
      option.value = contract.version;
      select.append(option);
    }
    if (versions.length === 1) select.value = versions[0].version;
  }

  function renderDatasetOptions() {
    const select = $("#source-dataset");
    const previous = select.value;
    const seen = new Set();
    select.replaceChildren();
    for (const contract of state.contracts) {
      if (!M4_DATASET_TYPES.has(contract.dataset_type) || seen.has(contract.dataset_type)) continue;
      seen.add(contract.dataset_type);
      const option = node("option", "", DATASET_LABELS[contract.dataset_type] || contract.dataset_type);
      option.value = contract.dataset_type;
      select.append(option);
    }
    if (previous && Array.from(select.options).some((option) => option.value === previous)) select.value = previous;
    renderContractOptions();
  }

  function sourceById(sourceId) {
    return state.sources.find((source) => source.id === sourceId) || null;
  }

  function renderIngestionImportOptions() {
    const source = sourceById($("#ingestion-source").value);
    const select = $("#ingestion-import");
    const previous = select.value;
    select.replaceChildren();
    const placeholder = node("option", "", source ? "Choose an applied M4 import…" : "Choose a source first…");
    placeholder.value = "";
    select.append(placeholder);
    const eligible = source && M4_DATASET_TYPES.has(source.dataset_type) ? state.imports.filter((item) =>
      item.dataset_type === source.dataset_type && ["applied", "applied_with_warnings"].includes(item.status)) : [];
    for (const item of eligible) {
      const option = node("option", "", `${item.file_name} · ${item.status.replaceAll("_", " ")} · ${dateTimeText(item.created_at)}`);
      option.value = item.id;
      select.append(option);
    }
    if (previous && eligible.some((item) => item.id === previous)) select.value = previous;
    const note = $("#import-picker-note");
    if (!source) note.textContent = "Select a registered source to see applied imports with the same dataset type.";
    else if (!eligible.length) note.textContent = `No applied ${DATASET_LABELS[source.dataset_type] || source.dataset_type} imports are loaded. Import data in the Capacity workflow first.`;
    else note.textContent = "An M4 import can be bound to one M12 source only. If already linked elsewhere, the server will reject the duplicate.";
    const more = $("#load-more-imports-button");
    more.hidden = state.imports.length >= state.importTotal;
    more.disabled = !state.tenant || state.imports.length >= state.importTotal;
  }

  function renderSourceOptions() {
    const select = $("#ingestion-source");
    const previous = select.value;
    select.replaceChildren();
    const m4Sources = state.sources.filter((source) => M4_DATASET_TYPES.has(source.dataset_type));
    const placeholder = node("option", "", m4Sources.length ? "Choose a registered M4 source…" : "Register an M4 source first…");
    placeholder.value = "";
    select.append(placeholder);
    for (const source of m4Sources) {
      const option = node("option", "", `${source.display_name} · ${DATASET_LABELS[source.dataset_type] || source.dataset_type}`);
      option.value = source.id;
      select.append(option);
    }
    if (previous && m4Sources.some((source) => source.id === previous)) select.value = previous;
    renderIngestionImportOptions();
    const more = $("#load-more-sources-button");
    more.hidden = state.sources.length >= state.sourceTotal;
    more.disabled = !state.tenant || state.sources.length >= state.sourceTotal;
  }

  function badge(text, kind = "") {
    return node("span", `df-badge${kind ? ` is-${kind}` : ""}`, text);
  }

  function renderSources() {
    const target = $("#source-list");
    target.replaceChildren();
    const m12Sources = state.sources.filter((source) => source.dataset_type !== "universal_records");
    if (!m12Sources.length) {
      target.append(node("p", "df-empty", "No M4 or manual customer-commitment source is registered in this Company yet. This means it is not tracked here; it is not evidence that the business has no data."));
      return;
    }
    for (const source of m12Sources) {
      const card = node("article", "df-source-card");
      const heading = node("div", "df-source-heading");
      const identity = node("div");
      identity.append(
        node("h3", "", source.display_name),
        node("p", "df-source-key", `${source.source_key} · ${DATASET_LABELS[source.dataset_type] || source.dataset_type} · contract v${source.contract_version}`),
      );
      heading.append(identity);
      const statuses = node("div", "df-status-badges");
      if (source.dataset_type === "customer_commitments") statuses.append(badge("Manual capture", "warning"));
      else if (!source.latest_ingestion_status) statuses.append(badge("No ingestion"));
      else {
        const ingestionKind = source.latest_ingestion_status === "blocked" ? "failed" :
          source.latest_ingestion_status === "succeeded_with_warnings" ? "warning" : "passed";
        statuses.append(badge(String(source.latest_ingestion_status).replaceAll("_", " "), ingestionKind));
        const qualityKind = source.latest_quality_status === "failed" ? "failed" :
          source.latest_quality_status === "warning" ? "warning" :
            source.latest_quality_status === "passed" ? "passed" : "";
        statuses.append(badge(`quality ${source.latest_quality_status || "unknown"}`, qualityKind));
        const freshness = source.latest_freshness_status || "unknown";
        const freshnessKind = freshness === "fresh" ? "passed" :
          ["stale", "future_as_of"].includes(freshness) ? "warning" : "";
        statuses.append(badge(`freshness ${freshness.replaceAll("_", " ")}`, freshnessKind));
      }
      heading.append(statuses);
      card.append(heading);
      card.append(node("p", "df-source-meta", source.dataset_type === "customer_commitments"
        ? `Registered ${dateTimeText(source.created_at)} · manual records and review history are tracked in the Customer commitment intake above; this source does not accept M4 imports.`
        : source.latest_ingestion_at
          ? `Latest ingestion ${dateTimeText(source.latest_ingestion_at)} · last successful ${dateTimeText(source.last_successful_ingestion_at)} · source owner ${source.owner_user_id}`
          : `Registered ${dateTimeText(source.created_at)} · no ingestion evidence recorded yet.`));
      if (source.latest_ingestion_id) {
        const actions = node("div", "df-source-actions");
        const review = node("button", "button button-secondary button-small", "Review latest evidence and lineage");
        review.type = "button";
        review.addEventListener("click", () => loadIngestionEvidence(source));
        actions.append(review);
        card.append(actions);
      }
      target.append(card);
    }
    if (state.sourceTotal > state.sources.length) {
      target.append(node("p", "df-contract-line", `Showing ${state.sources.length} of ${state.sourceTotal} registered sources.`));
    }
  }

  function renderWorkspace() {
    renderCoverage();
    renderDatasetOptions();
    renderSourceOptions();
    renderSources();
    renderUniversalData();
    renderCommitments();
    updateRoleUi();
  }

  async function loadWorkspace() {
    if (!state.tenant) return;
    const tenantId = state.tenant.id;
    const sequence = ++state.loadSequence;
    const base = `/tenants/${encodeURIComponent(tenantId)}/company-data`;
    $("#evidence-panel").hidden = true;
    $("#evidence-content").replaceChildren();
    setStatus("Loading Company-scoped source and M4 evidence…");
    try {
      const commitmentPath = `${base}/customer-commitments`;
      const commitmentFilter = $("#commitment-status-filter").value;
      const commitmentQuery = commitmentFilter ? `&status=${encodeURIComponent(commitmentFilter)}` : "";
      const universalBase = `${base}/universal`;
      const universalRequest = Promise.all([
        request(`${universalBase}/sources?limit=${PAGE_SIZE}&offset=0`),
        request(`${universalBase}/entities`),
      ]).then(([sources, entities]) => ({ sources, entities, error: null }))
        .catch((error) => ({ sources: null, entities: null, error }));
      const [coreResults, universalResult] = await Promise.all([
        Promise.all([
          request(`${base}/contracts`),
          request(`${base}/coverage`),
          request(`${base}/sources?limit=${PAGE_SIZE}&offset=0`),
          request(`/tenants/${encodeURIComponent(tenantId)}/imports?limit=${PAGE_SIZE}&offset=0`),
          request(`${commitmentPath}?limit=${PAGE_SIZE}&offset=0${commitmentQuery}`),
        ]),
        universalRequest,
      ]);
      if (sequence !== state.loadSequence || state.tenant?.id !== tenantId) return;
      const [contractsResult, coverageResult, sourcesResult, importsResult, commitmentsResult] = coreResults;
      state.contracts = contractsResult.data?.items || [];
      state.coverage = coverageResult.data;
      state.sources = sourcesResult.data?.items || [];
      state.sourceTotal = sourcesResult.data?.total || 0;
      state.imports = importsResult.data?.items || [];
      state.importTotal = importsResult.data?.total || 0;
      state.commitments = commitmentsResult.data?.items || [];
      state.commitmentTotal = commitmentsResult.data?.total || 0;
      state.universalLoadError = universalResult.error
        ? universalResult.error.message || "Universal Data could not be loaded."
        : null;
      state.universalSources = universalResult.sources?.data?.items || [];
      state.universalSourceTotal = universalResult.sources?.data?.total || 0;
      state.universalEntities = universalResult.entities?.data?.items || [];
      if (state.universalLoadError) {
        state.universalCurrent = null;
        state.universalMappingDraft = null;
        state.universalIngestions = [];
        state.universalIngestionTotal = 0;
        state.universalRecords = [];
        state.universalRecordTotal = 0;
      }
      state.commitmentFilter = commitmentFilter;
      state.tenantDataReady = true;
      renderWorkspace();
      setStatus(`Company data evidence loaded · ${state.tenant.role || "member"} · ${state.sourceTotal} registered sources · ${state.importTotal} M4 imports in history.`, "is-success");
      const historySourceId = $("#ud-history-source").value;
      if (historySourceId) void loadUniversalIngestions(historySourceId);
      void loadUniversalRecords();
    } catch (error) {
      if (sequence !== state.loadSequence) return;
      if ([403, 404].includes(error.status)) clearTenantWorkspace();
      if (!state.tenantDataReady) {
        $("#role-notice").textContent = "Company data could not be loaded. Write actions remain disabled; refresh to retry.";
        $("#role-notice").hidden = false;
      }
      setStatus(error.message || "Company data evidence could not be loaded.", "is-error");
      showMessage(error.message || "Company data evidence could not be loaded.");
    }
  }

  async function loadTenants() {
    const { data } = await request("/tenants");
    state.tenants = Array.isArray(data?.items) ? data.items : [];
    if (!state.tenants.length) {
      state.tenant = null;
      $("#auth-required").hidden = true;
      $("#empty-workspace").hidden = false;
      $("#data-foundation").hidden = true;
      return;
    }
    const savedId = sessionStorage.getItem(SESSION_KEYS.tenant);
    state.tenant = state.tenants.find((item) => item.id === savedId) || state.tenants[0];
    sessionStorage.setItem(SESSION_KEYS.tenant, state.tenant.id);
    clearTenantWorkspace();
    $("#auth-required").hidden = true;
    $("#empty-workspace").hidden = true;
    $("#data-foundation").hidden = false;
    $("#user-label").textContent = state.user?.display_name || "";
    $("#user-label").hidden = false;
    renderTenantPicker();
    await loadWorkspace();
  }

  async function loadMoreSources() {
    const context = captureWorkspace();
    if (!context.tenantId || state.sources.length >= state.sourceTotal) return;
    const button = $("#load-more-sources-button");
    button.disabled = true;
    try {
      const { data } = await request(`/tenants/${encodeURIComponent(context.tenantId)}/company-data/sources?limit=${PAGE_SIZE}&offset=${state.sources.length}`);
      if (!workspaceIsCurrent(context)) return;
      state.sources.push(...(data?.items || []));
      state.sourceTotal = data?.total || state.sourceTotal;
      renderSourceOptions();
      renderSources();
    } catch (error) {
      if (workspaceIsCurrent(context)) showMessage(error.message || "Additional sources could not be loaded.");
    } finally {
      if (workspaceIsCurrent(context)) button.disabled = state.sources.length >= state.sourceTotal;
    }
  }

  async function loadMoreImports() {
    const context = captureWorkspace();
    if (!context.tenantId || state.imports.length >= state.importTotal) return;
    const button = $("#load-more-imports-button");
    button.disabled = true;
    try {
      const { data } = await request(`/tenants/${encodeURIComponent(context.tenantId)}/imports?limit=${PAGE_SIZE}&offset=${state.imports.length}`);
      if (!workspaceIsCurrent(context)) return;
      state.imports.push(...(data?.items || []));
      state.importTotal = data?.total || state.importTotal;
      renderIngestionImportOptions();
    } catch (error) {
      if (workspaceIsCurrent(context)) showMessage(error.message || "Additional M4 imports could not be loaded.");
    } finally {
      if (workspaceIsCurrent(context)) button.disabled = state.imports.length >= state.importTotal;
    }
  }

  async function registerSource(event) {
    event.preventDefault();
    if (!state.canWrite || !state.tenant) {
      showMessage("An authorized owner/admin permission is required to register a source.");
      return;
    }
    const context = captureWorkspace();
    const body = {
      source_key: $("#source-key").value.trim(),
      display_name: $("#source-name").value.trim(),
      dataset_type: $("#source-dataset").value,
      contract_version: $("#source-contract").value,
    };
    const maxAge = $("#source-freshness-age").value.trim();
    if (maxAge) body.freshness_max_age_hours = Number(maxAge);
    const button = $("#register-source-button");
    button.disabled = true;
    try {
      const { data } = await request(`/tenants/${encodeURIComponent(context.tenantId)}/company-data/sources`, { method: "POST", body });
      if (!workspaceIsCurrent(context)) return;
      $("#source-form").reset();
      await loadWorkspace();
      if (state.tenant?.id === context.tenantId) showMessage(`Source “${data.display_name}” registered. Record an applied M4 import to add evidence.`, "success");
    } catch (error) {
      if (workspaceIsCurrent(context)) showMessage(error.message || "The source could not be registered.");
    } finally {
      if (state.tenant?.id === context.tenantId) button.disabled = !state.canWrite;
    }
  }

  async function recordIngestion(event) {
    event.preventDefault();
    if (!state.canWrite || !state.tenant) {
      showMessage("An authorized owner/admin permission is required to record an ingestion.");
      return;
    }
    const context = captureWorkspace();
    const sourceId = $("#ingestion-source").value;
    const importId = $("#ingestion-import").value;
    const source = sourceById(sourceId);
    const imported = state.imports.find((item) => item.id === importId);
    if (!source || !imported || imported.dataset_type !== source.dataset_type ||
        !["applied", "applied_with_warnings"].includes(imported.status)) {
      showMessage("Choose an applied M4 import that matches the registered source dataset.");
      return;
    }
    const body = { import_id: importId };
    const asOfValue = $("#source-as-of").value;
    if (asOfValue) {
      const parsed = new Date(asOfValue);
      if (Number.isNaN(parsed.getTime())) {
        showMessage("Enter a valid source as-of date and time.");
        return;
      }
      body.source_as_of_at = parsed.toISOString();
    }
    const button = $("#record-ingestion-button");
    button.disabled = true;
    try {
      const { data } = await request(`/tenants/${encodeURIComponent(context.tenantId)}/company-data/sources/${encodeURIComponent(sourceId)}/ingestions`, { method: "POST", body });
      if (!workspaceIsCurrent(context)) return;
      $("#source-as-of").value = "";
      await loadWorkspace();
      if (state.tenant?.id === context.tenantId) {
        showMessage(`M12 evidence recorded with quality state “${data.ingestion?.quality_status || "recorded"}”. Review the source card for freshness, findings and lineage.`, "success");
      }
    } catch (error) {
      if (workspaceIsCurrent(context)) showMessage(error.message || "The applied M4 import could not be recorded.");
    } finally {
      if (state.tenant?.id === context.tenantId) button.disabled = !state.canWrite;
    }
  }

  function appendEvidenceLine(parent, label, value) {
    const line = node("p");
    line.append(node("strong", "", `${label}: `));
    line.append(node("code", "df-code", value === null || value === undefined || value === "" ? "Not recorded" : String(value)));
    parent.append(line);
  }

  function renderIngestionEvidence(response, lineage) {
    const target = $("#evidence-content");
    target.replaceChildren();
    const ingestion = response.ingestion;
    const summary = node("section", "df-evidence-block");
    summary.append(node("h3", "", `${ingestion.ingestion_status.replaceAll("_", " ")} · quality ${ingestion.quality_status} · current freshness ${ingestion.current_freshness_status.replaceAll("_", " ")}`));
    appendEvidenceLine(summary, "M12 ingestion ID", ingestion.id);
    appendEvidenceLine(summary, "M4 import ID", ingestion.import_id);
    appendEvidenceLine(summary, "Contract", `${ingestion.contract_key} v${ingestion.contract_version}`);
    appendEvidenceLine(summary, "Contract SHA-256", ingestion.contract_hash);
    appendEvidenceLine(summary, "File SHA-256", ingestion.file_hash);
    appendEvidenceLine(summary, "M4 validation SHA-256", ingestion.validation_hash);
    appendEvidenceLine(summary, "Snapshot SHA-256", ingestion.snapshot_hash);
    appendEvidenceLine(summary, "User-reported source as-of", ingestion.source_as_of_at);
    appendEvidenceLine(summary, "Rows inspected", ingestion.total_rows);
    summary.append(node("p", "df-contract-line", "A hash identifies retained evidence; it does not authenticate the source, establish authority, or prove all business records were supplied."));
    target.append(summary);

    const findingsBlock = node("section", "df-evidence-block");
    findingsBlock.append(node("h3", "", "Quality findings"));
    const findings = response.quality_findings || [];
    if (!findings.length) findingsBlock.append(node("p", "", "No M12 quality findings were recorded for this ingestion."));
    else {
      const list = node("ul", "df-finding-list");
      for (const finding of findings) {
        const count = finding.count === undefined ? "" : ` · count ${finding.count}`;
        list.append(node("li", "", `${finding.severity || "info"} · ${finding.code || "finding"}${count} · ${finding.message || "No detail recorded."}`));
      }
      findingsBlock.append(list);
    }
    target.append(findingsBlock);

    const lineageBlock = node("section", "df-evidence-block");
    lineageBlock.append(node("h3", "", `Canonical row lineage · ${lineage.total || 0} link(s)`));
    if (!lineage.items?.length) lineageBlock.append(node("p", "", "No canonical source-row links were returned for this ingestion."));
    else {
      const list = node("ul", "df-lineage-list");
      for (const row of lineage.items) {
        list.append(node("li", "", `${row.worksheet_name} row ${row.row_number} · ${row.canonical_type} ${row.canonical_id} · row ${shortHash(row.row_hash)} · ${row.row_status}`));
      }
      lineageBlock.append(list);
      if (lineage.total > lineage.items.length) lineageBlock.append(node("p", "df-contract-line", `Showing ${lineage.items.length} of ${lineage.total} lineage links; use the paginated API for remaining rows.`));
    }
    target.append(lineageBlock);
  }

  async function loadIngestionEvidence(source) {
    if (!state.tenant || !source.latest_ingestion_id) return;
    const context = captureWorkspace();
    const base = `/tenants/${encodeURIComponent(context.tenantId)}/company-data/sources/${encodeURIComponent(source.id)}/ingestions/${encodeURIComponent(source.latest_ingestion_id)}`;
    setStatus(`Verifying saved M12 evidence for ${source.display_name}…`);
    try {
      const [detailResult, lineageResult] = await Promise.all([
        request(base), request(`${base}/lineage?limit=25&offset=0`),
      ]);
      if (!workspaceIsCurrent(context)) return;
      renderIngestionEvidence(detailResult.data, lineageResult.data);
      $("#evidence-panel").hidden = false;
      $("#evidence-panel").scrollIntoView({ behavior: "smooth", block: "nearest" });
      setStatus(`M12 snapshot hash verified · ${source.display_name}.`, "is-success");
    } catch (error) {
      if (workspaceIsCurrent(context)) {
        setStatus(error.message || "M12 evidence could not be loaded.", "is-error");
        showMessage(error.message || "M12 evidence could not be loaded.");
      }
    }
  }

  function commitmentPath(tenantId = state.tenant?.id) {
    return `/tenants/${encodeURIComponent(tenantId)}/company-data/customer-commitments`;
  }

  function setCommitmentStatus(message, kind = "muted") {
    const target = $("#commitment-status");
    target.textContent = message;
    target.classList.remove("is-error", "is-success");
    if (kind === "is-error" || kind === "is-success") target.classList.add(kind);
  }

  function clearCommitmentEditor() {
    state.selectedCommitment = null;
    state.commitmentHistory = [];
    state.commitmentHistoryOffset = 0;
    state.commitmentHistoryTotal = 0;
    $("#commitment-form").reset();
    $("#commitment-editor-title").textContent = "New draft";
    $("#commitment-revision").textContent = "";
    $("#commitment-validation").replaceChildren(
      node("p", "df-contract-line", "Required fields are checked by the server before submission. Drafts may be incomplete."),
    );
    $("#commitment-evidence-panel").hidden = true;
    $("#commitment-review-panel").hidden = true;
    $("#commitment-history-panel").hidden = true;
    $("#commitment-evidence-list").replaceChildren();
    $("#commitment-history-list").replaceChildren();
    $("#commitment-review-comment").value = "";
    $("#commitment-acknowledge-wrap").hidden = true;
    $("#commitment-review-acknowledge-wrap").hidden = true;
    updateCommitmentEditorUi();
  }

  function commitmentFormValues() {
    const clean = (selector) => $(selector).value.trim() || null;
    return {
      customer_name: clean("#commitment-customer-name"),
      customer_reference: clean("#commitment-reference"),
      order_date: $("#commitment-order-date").value || null,
      requested_delivery_date: $("#commitment-delivery-date").value || null,
      summary: clean("#commitment-summary"),
      notes: clean("#commitment-notes"),
    };
  }

  function updateCommitmentEditorUi() {
    const record = state.selectedCommitment;
    const roleCanReview = ["owner", "admin"].includes(state.tenant?.role);
    const editableStatus = !record || ["draft", "changes_requested"].includes(record.status);
    const isOwnerOrAdmin = roleCanReview;
    const isRecordEditor = !record || isOwnerOrAdmin || record.created_by_user_id === state.user?.id;
    const editable = Boolean(state.canCommitmentWrite && editableStatus && isRecordEditor);
    const canReview = Boolean(
      state.tenantDataReady && roleCanReview && record?.status === "submitted" &&
      record.submitted_by_user_id !== state.user?.id,
    );
    for (const field of $$("input, textarea", $("#commitment-form"))) field.disabled = !editable;
    $("#commitment-save-button").disabled = !editable;
    $("#commitment-submit-button").disabled = !editable;
    $("#new-commitment-button").disabled = !state.canCommitmentWrite;
    $("#commitment-evidence-panel").hidden = !record;
    $("#commitment-evidence-form").hidden = !editable;
    for (const field of $$("input, button", $("#commitment-evidence-form"))) field.disabled = !editable;
    $("#commitment-review-panel").hidden = !canReview;
    for (const field of $$("textarea, input, button", $("#commitment-review-panel"))) field.disabled = !canReview;
    $("#commitment-history-panel").hidden = !record;
    $("#commitment-reset-button").disabled = !state.tenantDataReady;
    if (record) {
      $("#commitment-editor-title").textContent = record.customer_name || "Untitled customer commitment";
      $("#commitment-revision").textContent = `Revision ${record.current_revision} · ${COMMITMENT_STATUS_LABELS[record.status] || record.status}`;
      if (record.status === "submitted" && record.submitted_by_user_id === state.user?.id && roleCanReview) {
        $("#commitment-review-title").textContent = "Independent reviewer required";
      } else {
        $("#commitment-review-title").textContent = "Review submission";
      }
    } else {
      $("#commitment-editor-title").textContent = "New draft";
      $("#commitment-revision").textContent = "";
    }
  }

  function renderCommitmentFindings(validation) {
    const target = $("#commitment-validation");
    target.replaceChildren();
    const errors = validation?.errors || [];
    const warnings = validation?.warnings || [];
    if (!errors.length && !warnings.length) {
      target.append(node("p", "df-contract-line", "Required intake fields are complete; no optional-information or evidence warnings are outstanding."));
    }
    for (const [items, heading, kind] of [
      [errors, "Errors · fix before submission", ""],
      [warnings, "Warnings · optional information or evidence missing", "is-warning"],
    ]) {
      if (!items.length) continue;
      const group = node("section", `df-finding${kind ? ` ${kind}` : ""}`);
      group.append(node("p", "df-finding-heading", heading));
      const list = node("ul", "df-finding-list");
      for (const finding of items) {
        list.append(node("li", "", `${finding.field ? `${finding.field.replaceAll("_", " ")}: ` : ""}${finding.message}`));
      }
      group.append(list);
      target.append(group);
    }
    $("#commitment-acknowledge-wrap").hidden = warnings.length === 0;
    $("#commitment-review-acknowledge-wrap").hidden = warnings.length === 0;
  }

  function renderCommitmentEvidence(evidence) {
    const target = $("#commitment-evidence-list");
    target.replaceChildren();
    if (!evidence?.length) {
      target.append(node("p", "df-contract-line", "No file evidence attached. Missing evidence is a warning, not proof that the commitment is false."));
      return;
    }
    for (const item of evidence) {
      const row = node("div", "df-evidence-item");
      const info = node("p");
      info.append(node("strong", "", item.file_name));
      info.append(document.createTextNode(` · ${item.media_type} · ${Number(item.file_size_bytes).toLocaleString()} bytes · SHA-256 ${shortHash(item.file_hash)} · added ${dateTimeText(item.created_at)}`));
      row.append(info);
      const button = node("button", "button button-quiet button-small", "Download");
      button.type = "button";
      button.addEventListener("click", () => downloadCommitmentEvidence(item));
      row.append(button);
      target.append(row);
    }
  }

  function renderCommitmentHistory() {
    const target = $("#commitment-history-list");
    target.replaceChildren();
    if (!state.commitmentHistory.length) {
      target.append(node("li", "df-empty", "No history events are available."));
    }
    for (const event of state.commitmentHistory) {
      const item = node("li", "df-history-item");
      const action = String(event.event_type || "event").replaceAll("_", " ");
      item.append(node("h5", "", `${action} · ${COMMITMENT_STATUS_LABELS[event.status_after] || event.status_after}`));
      item.append(node("p", "", `Revision ${event.current_revision} · ${dateTimeText(event.occurred_at)} · actor ${event.actor_user_id}`));
      if (event.comment) item.append(node("p", "", `Comment: ${event.comment}`));
      const snapshot = event.snapshot || {};
      const evidenceCount = Array.isArray(snapshot.evidence) ? snapshot.evidence.length : 0;
      item.append(node("p", "", `${snapshot.customer_name || "Customer name missing"} · ${snapshot.summary || "Commitment summary missing"} · order date ${snapshot.order_date || "not recorded"} · requested delivery ${snapshot.requested_delivery_date || "not recorded"} · ${evidenceCount} evidence file(s)`));
      const context = snapshot.action_context || {};
      if (context.process_criteria_confirmed === true) {
        item.append(node("p", "", "Reviewer confirmed the intake criteria; this does not verify the underlying event."));
      }
      if (context.warnings_acknowledged === true) item.append(node("p", "", "Outstanding warnings were explicitly acknowledged."));
      item.append(node("p", "", `Snapshot ${shortHash(event.snapshot_hash)} · event ${shortHash(event.event_hash)} · previous ${shortHash(event.previous_event_hash)}`));
      target.append(item);
    }
    const more = $("#commitment-history-load-more");
    more.hidden = state.commitmentHistoryOffset >= state.commitmentHistoryTotal;
    more.disabled = more.hidden || !state.selectedCommitment;
  }

  function renderCommitments() {
    const target = $("#commitment-list");
    const more = $("#commitment-list-load-more");
    target.replaceChildren();
    more.hidden = state.commitments.length >= state.commitmentTotal;
    more.disabled = more.hidden || !state.tenantDataReady;
    if (!state.commitments.length) {
      target.append(node("p", "df-empty", state.commitmentFilter
        ? "No customer commitments match this status filter."
        : "No manually captured customer commitments are recorded yet. Create a draft to register the manual source in the Company Data Foundation."));
      return;
    }
    for (const record of state.commitments) {
      const card = node("article", `df-commitment-card${state.selectedCommitment?.id === record.id ? " is-selected" : ""}`);
      const heading = node("div", "df-commitment-card-heading");
      heading.append(node("h4", "", record.customer_name || "Customer name missing"));
      const kind = record.status === "accepted" ? "passed" :
        ["rejected"].includes(record.status) ? "failed" :
          record.status === "changes_requested" ? "warning" : "";
      heading.append(badge(COMMITMENT_STATUS_LABELS[record.status] || record.status, kind));
      card.append(heading);
      card.append(node("p", "", `${record.summary || "Commitment summary missing"}`));
      card.append(node("p", "", `Manual source · revision ${record.current_revision} · order date ${record.order_date || "not recorded"} · updated ${dateTimeText(record.updated_at)}`));
      const actions = node("div", "df-commitment-card-actions");
      const open = node("button", "button button-secondary button-small", state.selectedCommitment?.id === record.id ? "Refresh record" : "Open record");
      open.type = "button";
      open.addEventListener("click", () => openCommitment(record.id));
      actions.append(open);
      card.append(actions);
      target.append(card);
    }
    if (state.commitmentTotal > state.commitments.length) {
      target.append(node("p", "df-contract-line", `Showing ${state.commitments.length} of ${state.commitmentTotal} records in this view.`));
    }
  }

  function renderCommitmentDetail(detail, { preserveAcknowledgement = false } = {}) {
    const record = detail.commitment;
    const sameRecord = state.selectedCommitment?.id === record.id;
    state.selectedCommitment = record;
    $("#commitment-customer-name").value = record.customer_name || "";
    $("#commitment-reference").value = record.customer_reference || "";
    $("#commitment-order-date").value = record.order_date || "";
    $("#commitment-delivery-date").value = record.requested_delivery_date || "";
    $("#commitment-summary").value = record.summary || "";
    $("#commitment-notes").value = record.notes || "";
    $("#commitment-review-comment").value = record.review_comment || "";
    if (!preserveAcknowledgement && !sameRecord) {
      $("#commitment-acknowledge").checked = false;
      $("#commitment-review-acknowledge").checked = false;
      $("#commitment-review-criteria").checked = false;
    }
    renderCommitmentFindings(detail.validation);
    renderCommitmentEvidence(detail.evidence);
    $("#commitment-status-filter").value = state.commitmentFilter;
    updateCommitmentEditorUi();
    renderCommitments();
  }

  async function loadCommitmentList(tenantId = state.tenant?.id) {
    if (!tenantId) return;
    const context = captureWorkspace();
    const status = $("#commitment-status-filter").value;
    const suffix = status ? `&status=${encodeURIComponent(status)}` : "";
    try {
      const { data } = await request(`${commitmentPath(tenantId)}?limit=${PAGE_SIZE}&offset=0${suffix}`);
      if (state.tenant?.id !== tenantId || context.sequence !== state.loadSequence ||
          $("#commitment-status-filter").value !== status) return;
      state.commitments = data?.items || [];
      state.commitmentTotal = data?.total || 0;
      state.commitmentFilter = status;
      renderCommitments();
    } catch (error) {
      if (state.tenant?.id === tenantId) {
        setCommitmentStatus(error.message || "Customer commitments could not be loaded.", "is-error");
      }
    }
  }

  async function loadMoreCommitments() {
    const context = captureWorkspace();
    if (!context.tenantId || state.commitments.length >= state.commitmentTotal) return;
    const status = state.commitmentFilter;
    const suffix = status ? `&status=${encodeURIComponent(status)}` : "";
    const button = $("#commitment-list-load-more");
    button.disabled = true;
    try {
      const { data } = await request(
        `${commitmentPath(context.tenantId)}?limit=${PAGE_SIZE}&offset=${state.commitments.length}${suffix}`,
      );
      if (!workspaceIsCurrent(context) || state.commitmentFilter !== status) return;
      const existing = new Set(state.commitments.map((item) => item.id));
      state.commitments.push(...(data?.items || []).filter((item) => !existing.has(item.id)));
      state.commitmentTotal = data?.total || state.commitmentTotal;
      renderCommitments();
    } catch (error) {
      if (workspaceIsCurrent(context)) {
        showMessage(error.message || "Additional customer commitments could not be loaded.");
      }
    } finally {
      if (workspaceIsCurrent(context) && state.commitmentFilter === status) {
        button.disabled = state.commitments.length >= state.commitmentTotal;
      }
    }
  }

  async function openCommitment(commitmentId, { preserveAcknowledgement = false } = {}) {
    if (!state.tenant) return;
    const context = captureWorkspace();
    setCommitmentStatus("Loading the company-scoped record, evidence, and hash-linked history…");
    try {
      const path = `${commitmentPath(context.tenantId)}/${encodeURIComponent(commitmentId)}`;
      const [detailResult, historyResult] = await Promise.all([
        request(path),
        request(`${path}/history?limit=50&offset=0`),
      ]);
      if (!workspaceIsCurrent(context)) return;
      state.commitmentHistory = historyResult.data?.items || [];
      state.commitmentHistoryOffset = state.commitmentHistory.length;
      state.commitmentHistoryTotal = historyResult.data?.total || 0;
      renderCommitmentDetail(detailResult.data, { preserveAcknowledgement });
      renderCommitmentHistory();
      $("#commitment-history-panel").hidden = false;
      setCommitmentStatus(`Record opened · ${state.commitmentHistoryTotal} history event(s) hash-checked by the server.`, "is-success");
    } catch (error) {
      if (workspaceIsCurrent(context)) {
        setCommitmentStatus(error.message || "The customer commitment could not be opened.", "is-error");
        showMessage(error.message || "The customer commitment could not be opened.");
      }
    }
  }

  async function refreshCommitmentsAfterWrite(tenantId) {
    const status = $("#commitment-status-filter").value;
    const suffix = status ? `&status=${encodeURIComponent(status)}` : "";
    const { data } = await request(`${commitmentPath(tenantId)}?limit=${PAGE_SIZE}&offset=0${suffix}`);
    if (state.tenant?.id !== tenantId) return;
    state.commitments = data?.items || [];
    state.commitmentTotal = data?.total || 0;
    state.commitmentFilter = status;
    renderCommitments();
  }

  async function saveCurrentCommitment({ quiet = false, preserveAcknowledgement = false } = {}) {
    if (!state.canCommitmentWrite || !state.tenant) {
      showMessage("An active Company membership is required to save a manual customer-commitment draft.");
      return null;
    }
    const context = captureWorkspace();
    const current = state.selectedCommitment;
    const body = commitmentFormValues();
    const button = $("#commitment-save-button");
    button.disabled = true;
    setCommitmentStatus("Saving the draft and recording its provenance…");
    try {
      let result;
      if (current) {
        result = await request(`${commitmentPath(context.tenantId)}/${encodeURIComponent(current.id)}`, {
          method: "PUT",
          body: { ...body, expected_revision: current.current_revision },
        });
      } else {
        result = await request(commitmentPath(context.tenantId), { method: "POST", body });
      }
      if (!workspaceIsCurrent(context)) return null;
      const commitmentId = result.data.commitment.id;
      await refreshCommitmentsAfterWrite(context.tenantId);
      await openCommitment(commitmentId, { preserveAcknowledgement });
      if (!quiet) setCommitmentStatus("Draft saved. Required fields, optional warnings, and evidence remain distinct.", "is-success");
      return state.selectedCommitment;
    } catch (error) {
      if (workspaceIsCurrent(context)) {
        setCommitmentStatus(error.message || "The draft could not be saved.", "is-error");
        showMessage(error.message || "The draft could not be saved.");
      }
      return null;
    } finally {
      if (workspaceIsCurrent(context)) updateCommitmentEditorUi();
    }
  }

  async function submitCommitment() {
    const warningsAcknowledged = $("#commitment-acknowledge").checked;
    const record = await saveCurrentCommitment({ quiet: true, preserveAcknowledgement: true });
    if (!record || !state.tenant) return;
    const context = captureWorkspace();
    setCommitmentStatus("Checking required fields and recording submission…");
    try {
      const { data } = await request(`${commitmentPath(context.tenantId)}/${encodeURIComponent(record.id)}/submit`, {
        method: "POST",
        body: { expected_revision: record.current_revision, acknowledge_warnings: warningsAcknowledged },
      });
      if (!workspaceIsCurrent(context)) return;
      await refreshCommitmentsAfterWrite(context.tenantId);
      await openCommitment(data.commitment.id, { preserveAcknowledgement: true });
      setCommitmentStatus("Submitted for independent review. This records the submitter's assertion; it is not factual verification.", "is-success");
    } catch (error) {
      if (workspaceIsCurrent(context)) {
        setCommitmentStatus(error.message || "The record could not be submitted.", "is-error");
        showMessage(error.message || "The record could not be submitted.");
        if (state.selectedCommitment?.id === record.id) await openCommitment(record.id, { preserveAcknowledgement: true });
      }
    }
  }

  async function uploadCommitmentEvidence(event) {
    event.preventDefault();
    const record = state.selectedCommitment;
    if (!record || !state.canCommitmentWrite) return;
    const file = $("#commitment-evidence-file").files?.[0];
    if (!file) {
      showMessage("Choose a PDF, PNG, JPEG, or UTF-8 text evidence file.");
      return;
    }
    const context = captureWorkspace();
    const form = new FormData();
    form.append("file", file, file.name);
    const button = $("#commitment-evidence-upload-button");
    button.disabled = true;
    setCommitmentStatus("Hashing and storing the user-supplied evidence file…");
    try {
      await request(`${commitmentPath(context.tenantId)}/${encodeURIComponent(record.id)}/evidence`, {
        method: "POST",
        body: form,
      });
      if (!workspaceIsCurrent(context)) return;
      $("#commitment-evidence-form").reset();
      await refreshCommitmentsAfterWrite(context.tenantId);
      await openCommitment(record.id);
      setCommitmentStatus("Evidence attached and recorded in the append-only history. Its hash does not authenticate its source.", "is-success");
    } catch (error) {
      if (workspaceIsCurrent(context)) {
        setCommitmentStatus(error.message || "The evidence file could not be attached.", "is-error");
        showMessage(error.message || "The evidence file could not be attached.");
      }
    } finally {
      if (workspaceIsCurrent(context)) updateCommitmentEditorUi();
    }
  }

  async function downloadCommitmentEvidence(evidence) {
    if (!state.tenant || !state.selectedCommitment) return;
    const context = captureWorkspace();
    const path = `${commitmentPath(context.tenantId)}/${encodeURIComponent(state.selectedCommitment.id)}/evidence/${encodeURIComponent(evidence.id)}`;
    try {
      const blob = await requestBlob(path);
      if (!workspaceIsCurrent(context)) return;
      const objectUrl = URL.createObjectURL(blob);
      const link = node("a");
      link.href = objectUrl;
      link.download = evidence.file_name;
      document.body.append(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    } catch (error) {
      if (workspaceIsCurrent(context)) showMessage(error.message || "The evidence file could not be downloaded.");
    }
  }

  async function reviewCommitment(decision) {
    const record = state.selectedCommitment;
    if (!record || !state.tenant) return;
    const context = captureWorkspace();
    const body = {
      expected_revision: record.current_revision,
      decision,
      comment: $("#commitment-review-comment").value.trim() || null,
      criteria_confirmed: decision === "accept" && $("#commitment-review-criteria").checked,
      acknowledge_warnings: $("#commitment-review-acknowledge").checked,
    };
    const buttonId = decision === "accept" ? "#commitment-accept-button" :
      decision === "reject" ? "#commitment-reject-button" : "#commitment-request-correction-button";
    const button = $(buttonId);
    button.disabled = true;
    setCommitmentStatus(`Recording owner/admin review: ${decision.replaceAll("_", " ")}…`);
    try {
      const { data } = await request(`${commitmentPath(context.tenantId)}/${encodeURIComponent(record.id)}/review`, {
        method: "POST",
        body,
      });
      if (!workspaceIsCurrent(context)) return;
      await refreshCommitmentsAfterWrite(context.tenantId);
      await openCommitment(data.commitment.id);
      setCommitmentStatus(`Review recorded · ${COMMITMENT_STATUS_LABELS[data.commitment.status] || data.commitment.status}. The review confirms process criteria, not the underlying event.`, "is-success");
    } catch (error) {
      if (workspaceIsCurrent(context)) {
        setCommitmentStatus(error.message || "The review decision could not be recorded.", "is-error");
        showMessage(error.message || "The review decision could not be recorded.");
      }
    } finally {
      if (workspaceIsCurrent(context)) updateCommitmentEditorUi();
    }
  }

  async function loadMoreCommitmentHistory() {
    const record = state.selectedCommitment;
    if (!record || state.commitmentHistoryOffset >= state.commitmentHistoryTotal || !state.tenant) return;
    const context = captureWorkspace();
    const button = $("#commitment-history-load-more");
    button.disabled = true;
    try {
      const path = `${commitmentPath(context.tenantId)}/${encodeURIComponent(record.id)}/history?limit=50&offset=${state.commitmentHistoryOffset}`;
      const { data } = await request(path);
      if (!workspaceIsCurrent(context) || state.selectedCommitment?.id !== record.id) return;
      state.commitmentHistory.push(...(data?.items || []));
      state.commitmentHistoryOffset += (data?.items || []).length;
      state.commitmentHistoryTotal = data?.total || state.commitmentHistoryTotal;
      renderCommitmentHistory();
    } catch (error) {
      if (workspaceIsCurrent(context)) showMessage(error.message || "Older record history could not be loaded.");
    } finally {
      if (workspaceIsCurrent(context)) renderCommitmentHistory();
    }
  }

  function universalBase(tenantId = state.tenant?.id) {
    return `/tenants/${encodeURIComponent(tenantId || "")}/company-data/universal`;
  }

  function setUniversalStatus(message, kind = "muted") {
    const target = $("#ud-status");
    target.textContent = message;
    target.classList.remove("is-error", "is-success");
    if (kind === "is-error" || kind === "is-success") target.classList.add(kind);
  }

  function shortJson(value, maximum = 1800) {
    const text = JSON.stringify(value, null, 2);
    return text.length > maximum ? `${text.slice(0, maximum)}\n…` : text;
  }

  function selectedUniversalSource(sourceId) {
    return state.universalSources.find((source) => source.source_id === sourceId) || null;
  }

  function renderUniversalOptions() {
    for (const selector of ["#ud-upload-source", "#ud-history-source"]) {
      const select = $(selector);
      const previous = select.value;
      select.replaceChildren();
      const enabledOnly = selector === "#ud-upload-source";
      const sources = state.universalSources.filter((source) => !enabledOnly || source.source_status === "enabled");
      const placeholder = node("option", "", sources.length ? "Choose a Universal source…" : "Register an enabled source first…");
      placeholder.value = "";
      select.append(placeholder);
      for (const source of sources) {
        const option = node("option", "", `${source.display_name} · ${source.source_type.replaceAll("_", " ")}${source.source_status === "disabled" ? " · disabled" : ""}`);
        option.value = source.source_id;
        select.append(option);
      }
      const preferred = state.universalCurrent?.sourceId || previous || sources[0]?.source_id;
      if (preferred && sources.some((source) => source.source_id === preferred)) select.value = preferred;
    }
    renderUniversalConnectorMode();
  }

  function renderUniversalConnectorMode() {
    const source = selectedUniversalSource($("#ud-upload-source").value);
    const local = source?.source_type === "local_directory";
    $("#ud-sync-form").hidden = !local;
    $("#ud-upload-file").required = true;
    $("#ud-apply-mapping").disabled = !state.canWrite || !state.universalCurrent || !$("#ud-existing-mapping").value;
  }

  function renderUniversalEntityOptions() {
    for (const selector of ["#ud-entity-type", "#ud-record-entity"]) {
      const select = $(selector);
      const previous = select.value;
      select.replaceChildren();
      const allOption = node("option", "", selector === "#ud-record-entity" ? "All entity types" : "Choose a canonical entity…");
      allOption.value = "";
      select.append(allOption);
      for (const entity of state.universalEntities) {
        const option = node("option", "", entity.label);
        option.value = entity.entity_type;
        select.append(option);
      }
      if (previous && Array.from(select.options).some((option) => option.value === previous)) select.value = previous;
    }
  }

  function renderUniversalSources() {
    const target = $("#ud-source-list");
    const more = $("#ud-sources-load-more");
    more.hidden = state.universalSources.length >= state.universalSourceTotal;
    more.disabled = !state.tenant || state.universalSources.length >= state.universalSourceTotal;
    target.replaceChildren();
    if (!state.universalSources.length) {
      target.append(node("p", "df-empty", "No Universal Data source is registered for this Company. Registration is metadata only; it does not verify a provider or source authority."));
      return;
    }
    for (const source of state.universalSources) {
      const card = node("article", "df-source-card");
      const heading = node("div", "df-source-heading");
      const identity = node("div");
      identity.append(
        node("h3", "", source.display_name),
        node("p", "df-source-key", `${source.source_key} · ${source.source_type.replaceAll("_", " ")} · profile v${source.version}`),
      );
      heading.append(identity);
      const statuses = node("div", "df-status-badges");
      statuses.append(badge(source.source_status, source.source_status === "enabled" ? "passed" : "warning"));
      statuses.append(badge(source.authority_classification.replaceAll("_", " "), source.authority_classification === "unclassified" ? "warning" : ""));
      if (!source.last_attempted_at) statuses.append(badge("No ingestion"));
      heading.append(statuses);
      card.append(heading);
      card.append(node("p", "df-source-meta", `Registered ${dateTimeText(source.created_at)} · owner ${source.owner_user_id} · ${source.connection_reference_configured ? "connector reference configured" : "no connector reference"} · ${source.credential_reference_configured ? "credential reference configured" : "no credential reference"}`));
      const actions = node("div", "df-ingestion-actions");
      const useButton = node("button", "button button-secondary button-small", "Use for ingestion");
      useButton.type = "button";
      useButton.disabled = source.source_status !== "enabled";
      useButton.addEventListener("click", () => {
        $("#ud-upload-source").value = source.source_id;
        $("#ud-history-source").value = source.source_id;
        renderUniversalConnectorMode();
        void loadUniversalIngestions(source.source_id);
      });
      const historyButton = node("button", "button button-quiet button-small", "Profile history");
      historyButton.type = "button";
      historyButton.addEventListener("click", () => loadUniversalProfileHistory(source.source_id));
      actions.append(useButton, historyButton);
      card.append(actions);
      target.append(card);
    }
    if (state.universalSources.length < state.universalSourceTotal) {
      target.append(node("p", "df-contract-line", `Showing ${state.universalSources.length} of ${state.universalSourceTotal} Universal sources.`));
    }
  }

  async function loadMoreUniversalSources() {
    const context = captureWorkspace();
    const offset = state.universalSources.length;
    const button = $("#ud-sources-load-more");
    if (!context.tenantId || button.disabled || offset >= state.universalSourceTotal) return;
    button.disabled = true;
    try {
      const { data } = await request(`${universalBase(context.tenantId)}/sources?limit=100&offset=${offset}`);
      if (!workspaceIsCurrent(context) || state.universalSources.length !== offset) return;
      const existing = new Set(state.universalSources.map((source) => source.source_id));
      state.universalSources.push(...(data?.items || []).filter((source) => !existing.has(source.source_id)));
      state.universalSourceTotal = data?.total ?? state.universalSourceTotal;
      renderUniversalOptions();
      renderUniversalSources();
    } catch (error) {
      if (workspaceIsCurrent(context)) setUniversalStatus(error.message || "Additional Universal sources could not be loaded.", "is-error");
    } finally {
      if (workspaceIsCurrent(context)) button.disabled = state.universalSources.length >= state.universalSourceTotal;
    }
  }

  function renderUniversalProfileHistory() {
    const target = $("#ud-profile-history");
    target.replaceChildren(node("h4", "", "Source profile history"));
    for (const profile of state.universalProfileHistory) {
      const item = node("article", "df-history-item");
      item.append(node("h5", "", `Version ${profile.version} · ${profile.display_name}`));
      item.append(node("p", "", `${profile.source_type.replaceAll("_", " ")} · ${profile.source_status} · ${profile.authority_classification.replaceAll("_", " ")} · owner ${profile.owner_user_id}`));
      item.append(node("p", "", `Recorded ${dateTimeText(profile.created_at)} by ${profile.created_by_user_id} · freshness ${profile.freshness_max_age_hours ?? "not set"} hours`));
      target.append(item);
    }
    if (state.universalProfileHistory.length < state.universalProfileHistoryTotal) {
      const more = node("button", "button button-quiet button-small", "Load older profile versions");
      more.type = "button";
      more.classList.add("df-profile-history-load-more");
      more.addEventListener("click", () => loadUniversalProfileHistory(state.universalProfileHistorySourceId, { append: true }));
      target.append(more);
      target.append(node("p", "df-contract-line", `Showing ${state.universalProfileHistory.length} of ${state.universalProfileHistoryTotal} profile versions.`));
    }
    target.hidden = false;
  }

  async function loadUniversalProfileHistory(sourceId, { append = false } = {}) {
    const context = captureWorkspace();
    if (append && state.universalProfileHistorySourceId !== sourceId) return;
    const offset = append ? state.universalProfileHistory.length : 0;
    state.universalProfileHistorySourceId = sourceId;
    if (!append) {
      state.universalProfileHistory = [];
      state.universalProfileHistoryTotal = 0;
      const target = $("#ud-profile-history");
      target.replaceChildren(node("p", "df-empty", "Loading append-only profile history…"));
      target.hidden = false;
    } else {
      const more = $("#ud-profile-history").querySelector(".df-profile-history-load-more");
      if (more) more.disabled = true;
    }
    try {
      const { data } = await request(`${universalBase(context.tenantId)}/sources/${encodeURIComponent(sourceId)}/profile-history?limit=50&offset=${offset}`);
      if (!workspaceIsCurrent(context) || state.universalProfileHistorySourceId !== sourceId ||
        (append && state.universalProfileHistory.length !== offset)) return;
      const items = data?.items || [];
      state.universalProfileHistory = append ? [...state.universalProfileHistory, ...items] : items;
      state.universalProfileHistoryTotal = data?.total || 0;
      renderUniversalProfileHistory();
    } catch (error) {
      if (workspaceIsCurrent(context) && state.universalProfileHistorySourceId === sourceId) {
        if (append) setUniversalStatus(error.message || "Additional source profile history could not be loaded.", "is-error");
        else $("#ud-profile-history").replaceChildren(node("p", "df-finding", error.message || "Source profile history could not be loaded."));
      }
    } finally {
      if (append && workspaceIsCurrent(context) && state.universalProfileHistorySourceId === sourceId) renderUniversalProfileHistory();
    }
  }

  function renderUniversalIngestions() {
    const target = $("#ud-ingestion-list");
    const more = $("#ud-ingestions-load-more");
    more.hidden = state.universalIngestions.length >= state.universalIngestionTotal;
    more.disabled = !state.tenant || state.universalIngestions.length >= state.universalIngestionTotal;
    target.replaceChildren();
    if (!state.universalIngestions.length) {
      target.append(node("p", "df-empty", "No ingestion run is recorded for this source yet."));
      return;
    }
    for (const ingestion of state.universalIngestions) {
      const item = node("article", "df-ingestion-item");
      const heading = node("div", "df-ingestion-heading");
      heading.append(node("strong", "", ingestion.file_name));
      heading.append(badge(ingestion.status.replaceAll("_", " "), ingestion.status === "blocked" || ingestion.status === "failed" ? "failed" : ingestion.status === "review_required" ? "warning" : ingestion.status === "promoted" ? "passed" : ""));
      item.append(heading);
      item.append(node("p", "", `${dateTimeText(ingestion.created_at)} · ${ingestion.counts.total_rows} rows · ${ingestion.entity_type || "not mapped"} · ${shortHash(ingestion.input_fingerprint)}`));
      item.append(node("p", "", `Valid ${ingestion.counts.valid_rows} · invalid ${ingestion.counts.invalid_rows} · warnings ${ingestion.counts.warning_rows} · duplicates ${ingestion.counts.duplicate_rows} · promoted ${ingestion.counts.promoted_rows}`));
      const open = node("button", "button button-secondary button-small", "Open review");
      open.type = "button";
      open.addEventListener("click", () => openUniversalIngestion(ingestion.source_id, ingestion.id));
      item.append(open);
      target.append(item);
    }
    if (state.universalIngestions.length < state.universalIngestionTotal) {
      target.append(node("p", "df-contract-line", `Showing ${state.universalIngestions.length} of ${state.universalIngestionTotal} ingestion runs.`));
    }
  }

  async function loadUniversalIngestions(sourceId, { append = false } = {}) {
    const context = captureWorkspace();
    if (append && state.universalIngestionsSourceId !== sourceId) return;
    const offset = append ? state.universalIngestions.length : 0;
    state.universalIngestionsSourceId = sourceId;
    if (!append) {
      state.universalIngestions = [];
      state.universalIngestionTotal = 0;
      $("#ud-ingestions-load-more").hidden = true;
      $("#ud-ingestions-load-more").disabled = true;
    }
    const sourceSelect = $("#ud-history-source");
    if (sourceSelect && state.universalSources.some((source) => source.source_id === sourceId)) sourceSelect.value = sourceId;
    if (!append) $("#ud-ingestion-list").replaceChildren(node("p", "loading", "Loading ingestion history…"));
    try {
      const { data } = await request(`${universalBase(context.tenantId)}/sources/${encodeURIComponent(sourceId)}/ingestions?limit=50&offset=${offset}`);
      if (!workspaceIsCurrent(context) || state.universalIngestionsSourceId !== sourceId ||
        (append && state.universalIngestions.length !== offset)) return;
      const items = data?.items || [];
      state.universalIngestions = append ? [...state.universalIngestions, ...items] : items;
      state.universalIngestionTotal = data?.total || 0;
      renderUniversalIngestions();
    } catch (error) {
      if (workspaceIsCurrent(context) && state.universalIngestionsSourceId === sourceId) {
        if (append) setUniversalStatus(error.message || "Additional ingestion history could not be loaded.", "is-error");
        else $("#ud-ingestion-list").replaceChildren(node("p", "df-empty", error.message || "Ingestion history could not be loaded."));
      }
    } finally {
      if (append && workspaceIsCurrent(context) && state.universalIngestionsSourceId === sourceId) renderUniversalIngestions();
    }
  }

  async function loadMoreUniversalIngestions() {
    const sourceId = state.universalIngestionsSourceId;
    const button = $("#ud-ingestions-load-more");
    if (!sourceId || button.disabled || state.universalIngestions.length >= state.universalIngestionTotal) return;
    button.disabled = true;
    await loadUniversalIngestions(sourceId, { append: true });
  }

  function renderUniversalData() {
    renderUniversalOptions();
    renderUniversalEntityOptions();
    if (state.universalLoadError) {
      $("#ud-source-list").replaceChildren(node("p", "df-finding", state.universalLoadError));
      $("#ud-ingestion-list").replaceChildren(node("p", "df-finding", "Universal ingestion history is unavailable."));
      $("#ud-record-list").replaceChildren(node("p", "df-finding", "Universal canonical records are unavailable."));
      $("#ud-profile-history").replaceChildren();
      $("#ud-profile-history").hidden = true;
      $("#ud-record-lineage").replaceChildren();
      $("#ud-record-lineage").hidden = true;
      $("#ud-review-panel").hidden = true;
      setUniversalStatus(`Universal Data is temporarily unavailable: ${state.universalLoadError}`, "is-error");
      return;
    }
    renderUniversalSources();
    renderUniversalIngestions();
    renderUniversalRecords();
    const current = state.universalCurrent;
    if (current?.detail) renderUniversalIngestion();
    else $("#ud-review-panel").hidden = true;
    setUniversalStatus(state.tenantDataReady
      ? `Universal Data loaded · ${state.universalSourceTotal} source(s) · ${state.universalSources.length} shown.`
      : "Loading Universal Data permissions and evidence…");
  }

  async function registerUniversalSource(event) {
    event.preventDefault();
    if (!state.canWrite || !state.tenant) {
      showMessage("Owner/admin access is required to register a Universal Data source.");
      return;
    }
    const context = captureWorkspace();
    const sourceType = $("#ud-source-type").value;
    const body = {
      source_key: $("#ud-source-key").value.trim(),
      display_name: $("#ud-source-name").value.trim(),
      source_type: sourceType,
      authority_classification: $("#ud-source-authority").value,
    };
    const domain = $("#ud-source-domain").value.trim();
    const freshness = $("#ud-source-freshness").value.trim();
    if (domain) body.business_domain = domain;
    if (freshness) body.freshness_max_age_hours = Number(freshness);
    if (sourceType === "local_directory") body.connection_reference = "config://universal/local-directory";
    const button = $("#ud-register-source-button");
    button.disabled = true;
    setUniversalStatus("Recording a new Company-scoped source profile…");
    try {
      const { data } = await request(`${universalBase(context.tenantId)}/sources`, { method: "POST", body });
      if (!workspaceIsCurrent(context)) return;
      $("#ud-source-form").reset();
      await loadWorkspace();
      if (state.tenant?.id === context.tenantId) {
        $("#ud-upload-source").value = data.source_id;
        $("#ud-history-source").value = data.source_id;
        renderUniversalConnectorMode();
        await loadUniversalIngestions(data.source_id);
        setUniversalStatus(`Source ${data.display_name} registered at profile version ${data.version}. This records metadata only.`, "is-success");
      }
    } catch (error) {
      if (state.tenant?.id === context.tenantId) setUniversalStatus(error.message || "The Universal source could not be registered.", "is-error");
    } finally {
      if (state.tenant?.id === context.tenantId) button.disabled = !state.canWrite;
    }
  }

  function freshIdempotencyKey() {
    return window.crypto?.randomUUID?.() || `ud-${Date.now()}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
  }

  function idempotencyKeyForAttempt(stateProperty, fingerprint) {
    const current = state[stateProperty];
    if (current?.fingerprint === fingerprint) return current.key;
    const key = freshIdempotencyKey();
    state[stateProperty] = { fingerprint, key };
    return key;
  }

  async function stageUniversalFile(event) {
    event.preventDefault();
    if (!state.canWrite || !state.tenant) {
      showMessage("Owner/admin access is required to stage Universal Data files.");
      return;
    }
    const sourceId = $("#ud-upload-source").value;
    const file = $("#ud-upload-file").files?.[0];
    if (!sourceId || !file) {
      setUniversalStatus("Choose an enabled Universal source and a CSV/XLSX file.", "is-error");
      return;
    }
    const context = captureWorkspace();
    const form = new FormData();
    form.append("file", file, file.name);
    form.append("csv_delimiter", $("#ud-delimiter").value);
    form.append("csv_encoding", $("#ud-encoding").value);
    form.append("header_row", $("#ud-header-row").value || "1");
    const uploadFingerprint = JSON.stringify([
      context.tenantId,
      sourceId,
      file.name,
      file.size,
      file.lastModified,
      $("#ud-delimiter").value,
      $("#ud-encoding").value,
      $("#ud-header-row").value || "1",
    ]);
    const idempotencyKey = idempotencyKeyForAttempt("universalUploadAttempt", uploadFingerprint);
    const button = $("#ud-upload-button");
    button.disabled = true;
    setUniversalStatus("Inspecting file and retaining the original evidence…");
    try {
      const { data } = await request(`${universalBase(context.tenantId)}/sources/${encodeURIComponent(sourceId)}/ingestions`, {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey },
        body: form,
      });
      if (!workspaceIsCurrent(context)) return;
      state.universalUploadAttempt = null;
      $("#ud-upload-file").value = "";
      await loadUniversalIngestions(sourceId);
      await openUniversalIngestion(sourceId, data.ingestion.id);
      setUniversalStatus(`Evidence retained and inspected · ${data.ingestion.counts.total_rows} row(s) · original bytes are fingerprinted and preserved.`, "is-success");
    } catch (error) {
      if (error.code === "idempotency_key_reused") state.universalUploadAttempt = null;
      if (workspaceIsCurrent(context)) setUniversalStatus(error.message || "The Universal Data file could not be staged.", "is-error");
    } finally {
      if (workspaceIsCurrent(context)) button.disabled = !state.canWrite;
    }
  }

  async function stageUniversalLocalFile(event) {
    event.preventDefault();
    if (!state.canWrite || !state.tenant) {
      showMessage("Owner/admin access is required to use the local connector.");
      return;
    }
    const sourceId = $("#ud-upload-source").value;
    const relativePath = $("#ud-sync-path").value.trim();
    if (!sourceId || !relativePath) {
      setUniversalStatus("Choose a local-directory source and enter a relative file path.", "is-error");
      return;
    }
    const context = captureWorkspace();
    const parseOptions = {
      csv_delimiter: $("#ud-delimiter").value,
      csv_encoding: $("#ud-encoding").value,
      header_row: Number($("#ud-header-row").value || 1),
    };
    const syncFingerprint = JSON.stringify([context.tenantId, sourceId, relativePath, parseOptions]);
    const idempotencyKey = idempotencyKeyForAttempt("universalSyncAttempt", syncFingerprint);
    const button = $("#ud-sync-button");
    button.disabled = true;
    setUniversalStatus("Reading a bounded file from the configured local connector…");
    try {
      const { data } = await request(`${universalBase(context.tenantId)}/sources/${encodeURIComponent(sourceId)}/sync`, {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey },
        body: { relative_path: relativePath, ...parseOptions },
      });
      if (!workspaceIsCurrent(context)) return;
      state.universalSyncAttempt = null;
      await loadUniversalIngestions(sourceId);
      await openUniversalIngestion(sourceId, data.ingestion.id);
      setUniversalStatus(`Local connector staged ${data.ingestion.file_name}. The original bytes are retained as source evidence.`, "is-success");
    } catch (error) {
      if (error.code === "idempotency_key_reused") state.universalSyncAttempt = null;
      if (workspaceIsCurrent(context)) setUniversalStatus(error.message || "The local connector could not stage this file.", "is-error");
    } finally {
      if (workspaceIsCurrent(context)) button.disabled = !state.canWrite;
    }
  }

  async function loadUniversalMappings(sourceId) {
    const context = captureWorkspace();
    state.universalMappings = [];
    const select = $("#ud-existing-mapping");
    select.replaceChildren(node("option", "", "Loading saved mapping versions…"));
    select.options[0].value = "";
    renderUniversalConnectorMode();
    try {
      const { data } = await request(`${universalBase(context.tenantId)}/sources/${encodeURIComponent(sourceId)}/mapping-profiles?limit=100&offset=0`);
      if (!workspaceIsCurrent(context) || state.universalCurrent?.sourceId !== sourceId) return;
      state.universalMappings = data?.items || [];
      const previous = select.value;
      select.replaceChildren(node("option", "", "Create a new mapping version"));
      select.options[0].value = "";
      for (const mapping of state.universalMappings) {
        const option = node("option", "", `${mapping.profile_key} · v${mapping.version} · ${mapping.entity_type}`);
        option.value = mapping.id;
        select.append(option);
      }
      if (previous && state.universalMappings.some((mapping) => mapping.id === previous)) select.value = previous;
      renderUniversalConnectorMode();
    } catch (_error) {
      state.universalMappings = [];
      select.replaceChildren(node("option", "", "Saved mappings could not be loaded"));
      select.options[0].value = "";
      renderUniversalConnectorMode();
    }
  }

  function populateUniversalWorksheetOptions(detail, selected) {
    const select = $("#ud-worksheet");
    select.replaceChildren();
    for (const sheet of detail.available_worksheets || []) {
      const option = node("option", "", `${sheet.name} · ${sheet.row_count ?? 0} row(s)`);
      option.value = sheet.name;
      select.append(option);
    }
    if (selected && Array.from(select.options).some((option) => option.value === selected)) select.value = selected;
  }

  function renderUniversalInspection(detail) {
    const target = $("#ud-inspection");
    target.replaceChildren();
    const sheets = detail.available_worksheets || [];
    for (const sheet of sheets) {
      const card = node("article", "df-inspection-sheet");
      card.append(node("h4", "", `${sheet.name} · ${sheet.row_count ?? 0} row(s)`));
      card.append(node("p", "", `${(sheet.headers || []).length} column(s) · ${sheet.blank_rows_ignored ?? 0} blank row(s) ignored`));
      for (const column of sheet.columns || []) {
        const confidence = Math.round(Number(column.confidence || 0) * 100);
        card.append(node("p", "", `${column.position}. ${column.name} · inferred ${column.inferred_type} (${confidence}% advisory) · ${column.note}`));
      }
      target.append(card);
    }
  }

  function renderUniversalSummary(detail) {
    const target = $("#ud-ingestion-summary");
    target.replaceChildren();
    const ingestion = detail.ingestion;
    const values = [
      ["Status", ingestion.status.replaceAll("_", " ")],
      ["File", ingestion.file_name],
      ["Rows", `${ingestion.counts.total_rows} total`],
      ["Mapping", ingestion.mapping_profile_name ? `${ingestion.mapping_profile_name} · v${ingestion.mapping_version}` : "Not mapped"],
      ["Entity", ingestion.entity_type || "Not selected"],
      ["Validation", ingestion.validation_hash ? shortHash(ingestion.validation_hash) : "Not run"],
      ["Evidence SHA-256", shortHash(ingestion.input_fingerprint)],
      ["Captured source profile ID", shortHash(ingestion.source_profile_version_id)],
    ];
    for (const [label, value] of values) {
      const item = node("div", "df-universal-summary-item");
      item.append(node("span", "", label), node("strong", "", value));
      target.append(item);
    }
  }

  function renderUniversalMappingFields() {
    const target = $("#ud-mapping-fields");
    target.replaceChildren();
    const current = state.universalCurrent;
    if (!current?.detail) return;
    const sheetName = $("#ud-worksheet").value;
    const sheet = (current.detail.available_worksheets || []).find((item) => item.name === sheetName);
    const entity = state.universalEntities.find((item) => item.entity_type === $("#ud-entity-type").value);
    if (!sheet || !entity) {
      target.append(node("p", "df-empty", "Choose a canonical entity and worksheet to create an explicit column mapping."));
      return;
    }
    if (!state.universalMappingDraft) state.universalMappingDraft = { assignments: {}, field_options: {} };
    const draft = state.universalMappingDraft;
    const fieldByName = new Map((entity.fields || []).map((field) => [field.name, field]));
    const fieldOptions = [{ value: "", label: "Choose a canonical field…" }, ...entity.fields.map((field) => ({ value: field.name, label: `${field.name}${field.required ? " · required" : ""}` })), { value: "__ignore__", label: "Explicitly ignore this column" }];
    for (const header of sheet.headers || []) {
      const row = node("div", "df-mapping-row");
      const identity = node("div");
      identity.append(node("strong", "", header));
      const inference = (sheet.columns || []).find((column) => column.name === header);
      if (inference) identity.append(node("span", "df-mapping-inference", `${inference.inferred_type} · ${Math.round(Number(inference.confidence || 0) * 100)}% advisory`));
      const select = node("select");
      select.setAttribute("aria-label", `Map source column ${header}`);
      for (const optionData of fieldOptions) {
        const option = node("option", "", optionData.label);
        option.value = optionData.value;
        select.append(option);
      }
      select.value = draft.assignments[header] || "";
      const optionsSlot = node("div", "df-mapping-options");
      select.addEventListener("change", () => {
        const value = select.value;
        if (value && value !== "__ignore__") {
          for (const [otherHeader, assignment] of Object.entries(draft.assignments)) {
            if (otherHeader !== header && assignment === value) draft.assignments[otherHeader] = "";
          }
        }
        draft.assignments[header] = value;
        renderUniversalMappingFields();
      });
      const mappedField = fieldByName.get(select.value);
      if (mappedField) renderUniversalFieldOptions(optionsSlot, mappedField, draft);
      row.append(identity, select, optionsSlot);
      target.append(row);
    }
    const requiredMissing = entity.fields.filter((field) => field.required && !Object.values(draft.assignments).includes(field.name));
    target.append(node("p", "df-contract-line", requiredMissing.length
      ? `Required fields not currently mapped: ${requiredMissing.map((field) => field.name).join(", ")}. Validation will report missing fields until explicitly mapped.`
      : "All required canonical fields have an explicit mapping. Verify any optional columns and field options before saving."));
  }

  function renderUniversalFieldOptions(target, field, draft) {
    const saved = draft.field_options[field.name] || {};
    const addSelect = (labelText, choices, current, onChange) => {
      const label = node("label");
      label.append(node("span", "", labelText));
      const select = node("select");
      for (const choice of choices) {
        const option = node("option", "", choice.label);
        option.value = choice.value;
        select.append(option);
      }
      select.value = current ?? "";
      select.addEventListener("change", () => onChange(select.value));
      label.append(select);
      target.append(label);
    };
    if (field.type === "date") {
      addSelect("Date format", [
        { value: "", label: "ISO dates only / no conversion" },
        { value: "%Y-%m-%d", label: "YYYY-MM-DD" },
        { value: "%d/%m/%Y", label: "DD/MM/YYYY" },
        { value: "%m/%d/%Y", label: "MM/DD/YYYY" },
        { value: "%Y/%m/%d", label: "YYYY/MM/DD" },
        { value: "%d-%m-%Y", label: "DD-MM-YYYY" },
      ], saved.date_format || "", (value) => {
        const next = { ...(draft.field_options[field.name] || {}) };
        if (value) next.date_format = value;
        else delete next.date_format;
        draft.field_options[field.name] = next;
      });
      return;
    }
    if (field.type === "decimal") {
      addSelect("Decimal separator", [
        { value: "", label: "Choose…" }, { value: ".", label: "Dot" }, { value: ",", label: "Comma" },
      ], saved.decimal_separator || "", (value) => {
        const next = { ...(draft.field_options[field.name] || {}) };
        if (value) next.decimal_separator = value;
        else delete next.decimal_separator;
        if (next.thousands_separator === value) delete next.thousands_separator;
        draft.field_options[field.name] = next;
        renderUniversalMappingFields();
      });
      const decimal = saved.decimal_separator;
      const groupChoices = [
        { value: "", label: "No thousands separator" },
        { value: ",", label: "Comma" }, { value: ".", label: "Dot" },
        { value: " ", label: "Space" }, { value: "'", label: "Apostrophe" },
      ].filter((choice) => choice.value === "" || choice.value !== decimal);
      addSelect("Thousands separator", groupChoices, saved.thousands_separator || "", (value) => {
        const next = { ...(draft.field_options[field.name] || {}) };
        if (value) next.thousands_separator = value;
        else delete next.thousands_separator;
        draft.field_options[field.name] = next;
      });
      return;
    }
    addSelect("Text case", [
      { value: "preserve", label: "Preserve source" },
      { value: "upper", label: "Uppercase (explicit)" },
      { value: "lower", label: "Lowercase (explicit)" },
    ], saved.text_case || "preserve", (value) => {
      draft.field_options[field.name] = { ...(draft.field_options[field.name] || {}), text_case: value };
    });
  }

  function createUniversalMappingDraft(version) {
    const draft = { assignments: {}, field_options: {} };
    if (version) {
      for (const [fieldName, header] of Object.entries(version.field_mapping || {})) draft.assignments[header] = fieldName;
      for (const header of version.ignored_columns || []) draft.assignments[header] = "__ignore__";
      draft.field_options = JSON.parse(JSON.stringify(version.field_options || {}));
    }
    return draft;
  }

  function renderUniversalValidationSummary(detail) {
    const target = $("#ud-validation-summary");
    target.replaceChildren();
    const snapshot = detail.validation_snapshot;
    if (!snapshot) {
      target.append(node("p", "df-empty", "Save a mapping version and run validation to review row findings and duplicate candidates."));
      return;
    }
    const counts = detail.ingestion.counts;
    target.append(node("p", "", `Validation ${shortHash(detail.ingestion.validation_hash)} · ${counts.valid_rows} valid · ${counts.invalid_rows} invalid · ${counts.warning_rows} warning · ${counts.duplicate_rows} duplicate. Optional blanks remain explicit; no missing values are inferred.`));
    for (const finding of snapshot.dataset_findings || []) {
      target.append(node("p", finding.severity === "error" ? "df-finding" : "df-finding is-warning", `${finding.code}: ${finding.message}`));
    }
    if (counts.warning_rows || counts.duplicate_rows) {
      target.append(node("p", "df-contract-line", "Promotion requires explicit warning/duplicate acknowledgement. Possible cross-source matches remain separate records unless a reviewer resolves them; no automatic merge occurs."));
    }
  }

  function renderUniversalRows() {
    const current = state.universalCurrent;
    const target = $("#ud-row-list");
    target.replaceChildren();
    if (!current?.rows?.length) {
      target.append(node("p", "df-empty", "No data rows are available for this worksheet."));
    } else {
      for (const row of current.rows) {
        const item = node("article", "df-row-item");
        const heading = node("div", "df-row-heading");
        heading.append(node("strong", "", `${row.worksheet_name} · source row ${row.row_number}`));
        const statusKind = row.row_status === "invalid" ? "failed" : row.row_status === "warning" || row.row_status === "duplicate" ? "warning" : row.row_status === "valid" ? "passed" : "";
        heading.append(badge(row.row_status, statusKind));
        item.append(heading);
        if (row.source_record_id) item.append(node("p", "", `Source record key: ${row.source_record_id}`));
        const data = node("pre");
        data.textContent = `Raw evidence: ${shortJson(row.raw_values, 1000)}\nMapped values: ${shortJson(row.mapped_values, 1200)}`;
        item.append(data);
        for (const finding of row.findings || []) {
          const issue = node("p", finding.severity === "error" ? "df-finding" : finding.severity === "warning" ? "df-finding is-warning" : "df-contract-line");
          issue.textContent = `${finding.code}: ${finding.message}`;
          item.append(issue);
          if (Array.isArray(finding.candidates) && finding.candidates.length) {
            const candidates = node("ul", "df-lineage-list");
            for (const candidate of finding.candidates) {
              const li = node("li", "", `${candidate.record_id} · source ${candidate.source_id} · ${candidate.matching_fields.join(", ")} · unresolved`);
              candidates.append(li);
            }
            item.append(candidates);
          }
        }
        target.append(item);
      }
    }
    const more = $("#ud-rows-load-more");
    more.hidden = !current || current.rows.length >= current.rowsTotal;
    more.disabled = !current || current.rows.length >= current.rowsTotal;
  }

  function renderUniversalIngestion() {
    const current = state.universalCurrent;
    if (!current?.detail) return;
    const detail = current.detail;
    const ingestion = detail.ingestion;
    $("#ud-review-panel").hidden = false;
    $("#ud-review-title").textContent = `${ingestion.file_name} · ingestion review`;
    renderUniversalSummary(detail);
    renderUniversalInspection(detail);
    renderUniversalValidationSummary(detail);
    populateUniversalWorksheetOptions(detail, ingestion.selected_worksheet || detail.mapping_profile_version?.worksheet_name);
    $("#ud-entity-type").value = detail.mapping_profile_version?.entity_type || $("#ud-entity-type").value || "";
    if (detail.mapping_profile_version) {
      $("#ud-profile-key").value = detail.mapping_profile_version.profile_key;
      $("#ud-profile-name").value = detail.mapping_profile_version.display_name;
    }
    if (!state.universalMappingDraft) state.universalMappingDraft = createUniversalMappingDraft(detail.mapping_profile_version);
    renderUniversalMappingFields();
    renderUniversalRows();
    const promoted = ingestion.status === "promoted";
    $("#ud-map-button").disabled = !state.canWrite || promoted;
    $("#ud-validate-button").disabled = !state.canWrite || promoted || !ingestion.mapping_profile_version_id;
    $("#ud-promote-button").disabled = !state.canWrite || promoted || !["validated", "review_required"].includes(ingestion.status);
    $("#ud-promote-button").hidden = promoted || !["validated", "review_required"].includes(ingestion.status);
    $("#ud-download-evidence").disabled = false;
    $("#ud-acknowledge").checked = false;
    $("#ud-correction-reason").value = "";
    renderUniversalConnectorMode();
  }

  async function loadUniversalRows({ append = false } = {}) {
    const current = state.universalCurrent;
    if (!current || !state.tenant) return;
    const context = captureWorkspace();
    const offset = append ? current.rows.length : 0;
    try {
      const { data } = await request(`${universalBase(context.tenantId)}/sources/${encodeURIComponent(current.sourceId)}/ingestions/${encodeURIComponent(current.ingestionId)}/rows?limit=50&offset=${offset}`);
      if (!workspaceIsCurrent(context) || state.universalCurrent?.ingestionId !== current.ingestionId) return;
      current.rows = append ? [...current.rows, ...(data.items || [])] : (data.items || []);
      current.rowsTotal = data.total || 0;
      state.universalRowsOffset = current.rows.length;
      state.universalRowsTotal = current.rowsTotal;
      renderUniversalRows();
    } catch (error) {
      if (workspaceIsCurrent(context)) setUniversalStatus(error.message || "Ingestion rows could not be loaded.", "is-error");
    }
  }

  async function openUniversalIngestion(sourceId, ingestionId) {
    if (!state.tenant) return;
    const context = captureWorkspace();
    const reviewSequence = ++state.universalReviewSequence;
    setUniversalStatus("Loading immutable ingestion evidence and review state…");
    try {
      const [detailResult, rowsResult] = await Promise.all([
        request(`${universalBase(context.tenantId)}/sources/${encodeURIComponent(sourceId)}/ingestions/${encodeURIComponent(ingestionId)}`),
        request(`${universalBase(context.tenantId)}/sources/${encodeURIComponent(sourceId)}/ingestions/${encodeURIComponent(ingestionId)}/rows?limit=50&offset=0`),
      ]);
      if (!workspaceIsCurrent(context) || state.universalReviewSequence !== reviewSequence) return;
      const detail = detailResult.data;
      state.universalCurrent = {
        sourceId,
        ingestionId,
        detail,
        rows: rowsResult.data?.items || [],
        rowsTotal: rowsResult.data?.total || 0,
      };
      state.universalMappingDraft = createUniversalMappingDraft(detail.mapping_profile_version);
      $("#ud-profile-key").value = detail.mapping_profile_version?.profile_key || "";
      $("#ud-profile-name").value = detail.mapping_profile_version?.display_name || "";
      renderUniversalIngestion();
      await loadUniversalMappings(sourceId);
      if (!workspaceIsCurrent(context) || state.universalReviewSequence !== reviewSequence) return;
      $("#ud-review-panel").scrollIntoView({ behavior: "smooth", block: "start" });
      setUniversalStatus(`Ingestion ${detail.ingestion.id} loaded · source profile and mapping versions are preserved in its lineage.`);
      updateRoleUi();
    } catch (error) {
      if (workspaceIsCurrent(context) && state.universalReviewSequence === reviewSequence) setUniversalStatus(error.message || "The Universal ingestion could not be opened.", "is-error");
    }
  }

  function buildUniversalMappingBody() {
    const current = state.universalCurrent;
    if (!current) throw new Error("Open an ingestion before saving a mapping.");
    const entityType = $("#ud-entity-type").value;
    const worksheetName = $("#ud-worksheet").value;
    const worksheet = (current.detail.available_worksheets || []).find((sheet) => sheet.name === worksheetName);
    if (!worksheet) throw new Error("Choose a worksheet from the uploaded file.");
    const draft = state.universalMappingDraft || { assignments: {}, field_options: {} };
    const fieldMapping = {};
    const ignoredColumns = [];
    for (const header of worksheet.headers || []) {
      const assignment = draft.assignments[header];
      if (!assignment) throw new Error(`Choose a field or explicitly ignore “${header}”.`);
      if (assignment === "__ignore__") ignoredColumns.push(header);
      else {
        if (fieldMapping[assignment]) throw new Error(`Only one source column may map to “${assignment}”.`);
        fieldMapping[assignment] = header;
      }
    }
    const fieldOptions = {};
    for (const [field, options] of Object.entries(draft.field_options || {})) {
      if (fieldMapping[field] && options && Object.keys(options).length) fieldOptions[field] = options;
    }
    return {
      profile_key: $("#ud-profile-key").value.trim(),
      display_name: $("#ud-profile-name").value.trim(),
      entity_type: entityType,
      worksheet_name: worksheetName,
      field_mapping: fieldMapping,
      ignored_columns: ignoredColumns,
      field_options: fieldOptions,
    };
  }

  async function saveUniversalMapping(event) {
    event.preventDefault();
    const current = state.universalCurrent;
    if (!current || !state.canWrite) return;
    const context = captureWorkspace();
    let body;
    try {
      body = buildUniversalMappingBody();
    } catch (error) {
      setUniversalStatus(error.message, "is-error");
      return;
    }
    const button = $("#ud-map-button");
    button.disabled = true;
    setUniversalStatus("Saving an immutable mapping-profile version and attaching it to this ingestion…");
    try {
      await request(`${universalBase(context.tenantId)}/sources/${encodeURIComponent(current.sourceId)}/ingestions/${encodeURIComponent(current.ingestionId)}/mapping`, { method: "POST", body });
      if (!workspaceIsCurrent(context)) return;
      await openUniversalIngestion(current.sourceId, current.ingestionId);
      setUniversalStatus("Mapping version saved. The displayed source columns were either mapped or explicitly ignored.", "is-success");
    } catch (error) {
      if (workspaceIsCurrent(context)) setUniversalStatus(error.message || "The mapping could not be saved.", "is-error");
    } finally {
      if (workspaceIsCurrent(context)) button.disabled = !state.canWrite;
    }
  }

  async function applyUniversalMapping() {
    const current = state.universalCurrent;
    const versionId = $("#ud-existing-mapping").value;
    if (!current || !versionId || !state.canWrite) return;
    const context = captureWorkspace();
    const button = $("#ud-apply-mapping");
    button.disabled = true;
    try {
      await request(`${universalBase(context.tenantId)}/sources/${encodeURIComponent(current.sourceId)}/ingestions/${encodeURIComponent(current.ingestionId)}/mapping`, {
        method: "PUT",
        body: { mapping_profile_version_id: versionId },
      });
      if (!workspaceIsCurrent(context)) return;
      await openUniversalIngestion(current.sourceId, current.ingestionId);
      setUniversalStatus("The selected immutable mapping version was applied to this ingestion.", "is-success");
    } catch (error) {
      if (workspaceIsCurrent(context)) setUniversalStatus(error.message || "The saved mapping version could not be applied.", "is-error");
    } finally {
      if (workspaceIsCurrent(context)) renderUniversalConnectorMode();
    }
  }

  async function validateUniversalIngestion() {
    const current = state.universalCurrent;
    if (!current || !state.canWrite) return;
    const context = captureWorkspace();
    const button = $("#ud-validate-button");
    button.disabled = true;
    setUniversalStatus("Validating rows against the versioned contract and mapping…");
    try {
      const { data } = await request(`${universalBase(context.tenantId)}/sources/${encodeURIComponent(current.sourceId)}/ingestions/${encodeURIComponent(current.ingestionId)}/validate`, { method: "POST" });
      if (!workspaceIsCurrent(context)) return;
      await openUniversalIngestion(current.sourceId, current.ingestionId);
      const counts = data.ingestion.counts;
      setUniversalStatus(`Validation complete · ${counts.valid_rows} valid · ${counts.invalid_rows} invalid · ${counts.warning_rows} warning · ${counts.duplicate_rows} duplicate.`, counts.invalid_rows ? "is-error" : "is-success");
    } catch (error) {
      if (workspaceIsCurrent(context)) setUniversalStatus(error.message || "Validation could not be completed.", "is-error");
    } finally {
      if (workspaceIsCurrent(context)) updateRoleUi();
    }
  }

  async function promoteUniversalIngestion() {
    const current = state.universalCurrent;
    if (!current || !state.canWrite) return;
    const context = captureWorkspace();
    const body = {
      acknowledge_warnings: $("#ud-acknowledge").checked,
      correction_reason: $("#ud-correction-reason").value.trim() || null,
    };
    const button = $("#ud-promote-button");
    button.disabled = true;
    setUniversalStatus("Recording owner/admin review and applying qualified rows with full source lineage…");
    try {
      const { data } = await request(`${universalBase(context.tenantId)}/sources/${encodeURIComponent(current.sourceId)}/ingestions/${encodeURIComponent(current.ingestionId)}/promote`, { method: "POST", body });
      if (!workspaceIsCurrent(context)) return;
      await openUniversalIngestion(current.sourceId, current.ingestionId);
      await loadUniversalIngestions(current.sourceId);
      await loadUniversalRecords();
      setUniversalStatus(`Promotion recorded · ${data.created_records} created · ${data.revised_records} revised · ${data.unchanged_records} unchanged · ${data.withheld_duplicate_rows} duplicate row(s) withheld. No Core Financials facts were created.`, "is-success");
    } catch (error) {
      if (workspaceIsCurrent(context)) setUniversalStatus(error.message || "Promotion was not completed.", "is-error");
    } finally {
      if (workspaceIsCurrent(context)) updateRoleUi();
    }
  }

  async function downloadUniversalEvidence() {
    const current = state.universalCurrent;
    if (!current) return;
    const context = captureWorkspace();
    try {
      const blob = await requestBlob(`${universalBase(context.tenantId)}/sources/${encodeURIComponent(current.sourceId)}/ingestions/${encodeURIComponent(current.ingestionId)}/evidence`);
      if (!workspaceIsCurrent(context)) return;
      const objectUrl = URL.createObjectURL(blob);
      const link = node("a");
      link.href = objectUrl;
      link.download = current.detail.ingestion.file_name;
      document.body.append(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    } catch (error) {
      if (workspaceIsCurrent(context)) setUniversalStatus(error.message || "Original source evidence could not be downloaded.", "is-error");
    }
  }

  function renderUniversalRecords() {
    const target = $("#ud-record-list");
    if (!target) return;
    const count = $("#ud-record-count");
    count.textContent = state.universalRecordTotal
      ? `Showing ${state.universalRecords.length} of ${state.universalRecordTotal} canonical records.`
      : "";
    const more = $("#ud-records-load-more");
    more.hidden = state.universalRecords.length >= state.universalRecordTotal;
    more.disabled = !state.tenant || state.universalRecords.length >= state.universalRecordTotal;
    target.replaceChildren();
    if (!state.universalRecords.length) {
      target.append(node("p", "df-empty", "No canonical records are available for the selected filter. This is not evidence that the business has no such data."));
      return;
    }
    for (const record of state.universalRecords) {
      const item = node("article", "df-record-item");
      const heading = node("div", "df-record-heading");
      heading.append(node("strong", "", `${record.entity_type} · ${record.source_record_id}`));
      heading.append(badge(`revision ${record.revision}`, "passed"));
      item.append(heading);
      item.append(node("p", "", `Source ${record.source_id} · ingestion ${record.ingestion_id} · ${shortHash(record.attributes_hash)}`));
      const attributes = node("pre");
      attributes.textContent = shortJson(record.attributes, 1200);
      item.append(attributes);
      const lineageButton = node("button", "button button-quiet button-small", "View revision lineage");
      lineageButton.type = "button";
      lineageButton.addEventListener("click", () => loadUniversalRecordLineage(record.id));
      item.append(lineageButton);
      target.append(item);
    }
  }

  async function loadUniversalRecords({ append = false } = {}) {
    if (!state.tenant) return;
    const context = captureWorkspace();
    const entityType = $("#ud-record-entity").value;
    if (append && state.universalRecordFilter !== entityType) return;
    const filterChanged = state.universalRecordFilter !== entityType;
    state.universalRecordFilter = entityType;
    const offset = append ? state.universalRecords.length : 0;
    if (filterChanged && !append) {
      state.universalRecords = [];
      state.universalRecordTotal = 0;
      $("#ud-record-count").textContent = "";
      state.universalRecordLineageId = null;
      $("#ud-record-lineage").replaceChildren();
      $("#ud-record-lineage").hidden = true;
      $("#ud-records-load-more").hidden = true;
      $("#ud-records-load-more").disabled = true;
      $("#ud-record-list").replaceChildren(node("p", "loading", "Loading canonical records…"));
    }
    const query = new URLSearchParams({ limit: "50", offset: String(offset) });
    if (entityType) query.set("entity_type", entityType);
    try {
      const { data } = await request(`${universalBase(context.tenantId)}/records?${query.toString()}`);
      if (!workspaceIsCurrent(context) || state.universalRecordFilter !== entityType ||
        (append && state.universalRecords.length !== offset)) return;
      const items = data?.items || [];
      state.universalRecords = append ? [...state.universalRecords, ...items] : items;
      state.universalRecordTotal = data?.total || 0;
      renderUniversalRecords();
    } catch (error) {
      if (workspaceIsCurrent(context) && state.universalRecordFilter === entityType) {
        if (append) setUniversalStatus(error.message || "Additional canonical records could not be loaded.", "is-error");
        else $("#ud-record-list").replaceChildren(node("p", "df-empty", error.message || "Canonical records could not be loaded."));
      }
    } finally {
      if (append && workspaceIsCurrent(context) && state.universalRecordFilter === entityType) renderUniversalRecords();
    }
  }

  async function loadMoreUniversalRecords() {
    const button = $("#ud-records-load-more");
    if (!state.tenant || button.disabled || state.universalRecords.length >= state.universalRecordTotal) return;
    button.disabled = true;
    await loadUniversalRecords({ append: true });
  }

  async function loadUniversalRecordLineage(recordId) {
    const context = captureWorkspace();
    state.universalRecordLineageId = recordId;
    const target = $("#ud-record-lineage");
    target.replaceChildren(node("p", "loading", "Loading immutable revision lineage…"));
    target.hidden = false;
    try {
      const { data } = await request(`${universalBase(context.tenantId)}/records/${encodeURIComponent(recordId)}/lineage?limit=50&offset=0`);
      if (!workspaceIsCurrent(context) || state.universalRecordLineageId !== recordId) return;
      target.replaceChildren(node("h4", "", "Immutable revision lineage"));
      for (const revision of data?.items || []) {
        const item = node("article", "df-history-item");
        item.append(node("h5", "", `Revision ${revision.revision} · ${revision.source_record_id}`));
        item.append(node("p", "", `${revision.file_name} · ${revision.worksheet_name}, row ${revision.row_number} · captured ${dateTimeText(revision.captured_at)} · promoted ${dateTimeText(revision.promoted_at)}`));
        item.append(node("p", "", `Mapping ${revision.mapping_profile_id} v${revision.mapping_version} · source profile v${revision.source_profile_version} · reviewer ${revision.promoted_by_user_id}`));
        item.append(node("p", "", `Row ${shortHash(revision.row_hash)} · validation ${shortHash(revision.validation_hash)} · attributes ${shortHash(revision.attributes_hash)}`));
        target.append(item);
      }
      target.hidden = false;
    } catch (error) {
      if (workspaceIsCurrent(context) && state.universalRecordLineageId === recordId) {
        target.replaceChildren(node("p", "df-finding", error.message || "Record lineage could not be loaded."));
        setUniversalStatus(error.message || "Record lineage could not be loaded.", "is-error");
      }
    }
  }

  async function loadMoreUniversalRows() {
    const current = state.universalCurrent;
    const button = $("#ud-rows-load-more");
    if (!current || button.disabled || current.rows.length >= current.rowsTotal) return;
    button.disabled = true;
    await loadUniversalRows({ append: true });
    if (state.universalCurrent?.ingestionId === current.ingestionId) {
      button.disabled = current.rows.length >= current.rowsTotal;
    }
  }

  async function bindEvents() {
    $("#company-picker").addEventListener("change", async (event) => {
      const tenant = state.tenants.find((item) => item.id === event.target.value);
      if (!tenant) return;
      const changedWorkspace = state.tenant?.id !== tenant.id;
      state.tenant = tenant;
      if (changedWorkspace) clearTenantWorkspace();
      sessionStorage.setItem(SESSION_KEYS.tenant, tenant.id);
      renderTenantPicker();
      await loadWorkspace();
    });
    $("#refresh-button").addEventListener("click", loadWorkspace);
    $("#source-dataset").addEventListener("change", renderContractOptions);
    $("#source-form").addEventListener("submit", registerSource);
    $("#ingestion-source").addEventListener("change", renderIngestionImportOptions);
    $("#ingestion-form").addEventListener("submit", recordIngestion);
    $("#new-commitment-button").addEventListener("click", () => {
      clearCommitmentEditor();
      $("#commitment-customer-name").focus();
      setCommitmentStatus("New draft opened. Save it to register the manual source and preserve its history.");
    });
    $("#commitment-reset-button").addEventListener("click", () => {
      clearCommitmentEditor();
      setCommitmentStatus("Editor cleared. Saved records remain unchanged.");
    });
    $("#commitment-form").addEventListener("submit", (event) => {
      event.preventDefault();
      saveCurrentCommitment();
    });
    $("#commitment-save-button").addEventListener("click", () => saveCurrentCommitment());
    $("#commitment-submit-button").addEventListener("click", submitCommitment);
    $("#commitment-evidence-form").addEventListener("submit", uploadCommitmentEvidence);
    $("#commitment-accept-button").addEventListener("click", () => reviewCommitment("accept"));
    $("#commitment-request-correction-button").addEventListener("click", () => reviewCommitment("request_correction"));
    $("#commitment-reject-button").addEventListener("click", () => reviewCommitment("reject"));
    $("#commitment-history-load-more").addEventListener("click", loadMoreCommitmentHistory);
    $("#commitment-list-load-more").addEventListener("click", loadMoreCommitments);
    $("#commitment-status-filter").addEventListener("change", () => loadCommitmentList());
    $("#load-more-sources-button").addEventListener("click", loadMoreSources);
    $("#load-more-imports-button").addEventListener("click", loadMoreImports);
    $("#close-evidence-button").addEventListener("click", () => { $("#evidence-panel").hidden = true; });
    $("#ud-refresh-button").addEventListener("click", loadWorkspace);
    $("#ud-source-form").addEventListener("submit", registerUniversalSource);
    $("#ud-upload-form").addEventListener("submit", stageUniversalFile);
    $("#ud-sync-form").addEventListener("submit", stageUniversalLocalFile);
    $("#ud-upload-source").addEventListener("change", (event) => {
      const sourceId = event.target.value;
      if (sourceId && state.universalSources.some((source) => source.source_id === sourceId)) {
        $("#ud-history-source").value = sourceId;
        void loadUniversalIngestions(sourceId);
      }
      renderUniversalConnectorMode();
    });
    $("#ud-history-source").addEventListener("change", (event) => {
      const sourceId = event.target.value;
      if (sourceId) void loadUniversalIngestions(sourceId);
      else {
        state.universalIngestionsSourceId = null;
        state.universalIngestions = [];
        state.universalIngestionTotal = 0;
        renderUniversalIngestions();
      }
    });
    $("#ud-mapping-form").addEventListener("submit", saveUniversalMapping);
    $("#ud-entity-type").addEventListener("change", () => {
      state.universalMappingDraft = { assignments: {}, field_options: {} };
      renderUniversalMappingFields();
    });
    $("#ud-worksheet").addEventListener("change", renderUniversalMappingFields);
    $("#ud-existing-mapping").addEventListener("change", renderUniversalConnectorMode);
    $("#ud-apply-mapping").addEventListener("click", applyUniversalMapping);
    $("#ud-validate-button").addEventListener("click", validateUniversalIngestion);
    $("#ud-promote-button").addEventListener("click", promoteUniversalIngestion);
    $("#ud-acknowledge").addEventListener("change", updateRoleUi);
    $("#ud-download-evidence").addEventListener("click", downloadUniversalEvidence);
    $("#ud-close-review").addEventListener("click", () => {
      state.universalReviewSequence += 1;
      state.universalCurrent = null;
      state.universalMappingDraft = null;
      $("#ud-review-panel").hidden = true;
      updateRoleUi();
    });
    $("#ud-rows-load-more").addEventListener("click", loadMoreUniversalRows);
    $("#ud-record-entity").addEventListener("change", loadUniversalRecords);
    $("#ud-records-refresh").addEventListener("click", () => loadUniversalRecords());
    $("#ud-sources-load-more").addEventListener("click", loadMoreUniversalSources);
    $("#ud-ingestions-load-more").addEventListener("click", loadMoreUniversalIngestions);
    $("#ud-records-load-more").addEventListener("click", loadMoreUniversalRecords);
  }

  async function bootstrap() {
    bindEvents();
    const browserTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "local time zone";
    $("#timezone-note").textContent = `If entered, interpreted in ${browserTimezone}. This is a user assertion, not independently verified.`;
    if (!state.accessToken) {
      showSignedOut();
      return;
    }
    try {
      const { data } = await request("/auth/me");
      state.user = data;
      $("#user-label").textContent = data.display_name;
      $("#user-label").hidden = false;
      await loadTenants();
    } catch (_error) {
      clearAuth();
      showSignedOut();
    }
  }

  bootstrap();
})();
