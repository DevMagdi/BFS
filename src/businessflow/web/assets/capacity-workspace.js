(() => {
  "use strict";

  const API_BASE = "/api/v1";
  const SESSION_KEYS = {
    access: "businessflow.m10.access",
    refresh: "businessflow.m10.refresh",
    tenant: "businessflow.m10.tenant",
  };
  const DATASETS = [
    "employees",
    "skills",
    "employee_skills",
    "weekly_capacity",
    "projects",
    "resource_assignments",
  ];
  const DATASET_LABELS = {
    employees: "Employees",
    skills: "Skills",
    employee_skills: "Employee skills",
    weekly_capacity: "Weekly capacity",
    projects: "Projects",
    resource_assignments: "Resource assignments",
  };
  const STATUS_LABELS = {
    feasible: "Feasible — capacity only",
    feasible_with_conditions: "Feasible with conditions — capacity only",
    not_feasible: "Not feasible — capacity only",
    incomplete: "Incomplete — evidence does not support a capacity conclusion",
  };
  const DECISION_LABELS = {
    proceed_to_financial_evaluation: "Proceed to separate financial evaluation",
    hold: "Hold",
    request_information: "Request information",
    capacity_declined: "Capacity declined",
  };
  const state = {
    accessToken: sessionStorage.getItem(SESSION_KEYS.access),
    refreshToken: sessionStorage.getItem(SESSION_KEYS.refresh),
    user: null,
    tenants: [],
    tenant: null,
    demo: null,
    canWrite: false,
    importAccessDenied: false,
    workforceAccessDenied: false,
    loadSequence: 0,
    intakeSequence: 0,
    assessmentSequence: 0,
    intakeLoading: false,
    importItems: [],
    stagedImport: null,
    mappingResponse: null,
    validationReport: null,
    validationPreview: null,
    intakeItems: [],
    intake: null,
    assessmentItems: [],
    assessment: null,
    metricRun: null,
    decisions: null,
    currentDecision: null,
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

  function setVisible(element, visible) {
    element.hidden = !visible;
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

  function showSignedOut() {
    setVisible($("#auth-required"), true);
    setVisible($("#empty-workspace"), false);
    setVisible($("#capacity-workspace"), false);
  }

  function showMessage(message, kind = "error") {
    const box = $("#global-message");
    box.textContent = message;
    box.classList.toggle("is-success", kind === "success");
    box.hidden = false;
    window.clearTimeout(state.messageTimer);
    state.messageTimer = window.setTimeout(() => { box.hidden = true; }, 9000);
  }

  function clearMessage() {
    $("#global-message").hidden = true;
  }

  function setStatus(message, kind = "muted") {
    const target = $("#workspace-status");
    target.textContent = message;
    target.classList.remove("is-error", "is-success", "muted");
    target.classList.add(kind);
  }

  function dateText(value, options = {}) {
    if (!value) return "Not recorded";
    const parsed = new Date(`${String(value).slice(0, 10)}T12:00:00Z`);
    if (Number.isNaN(parsed.getTime())) return String(value);
    return new Intl.DateTimeFormat(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
      ...options,
    }).format(parsed);
  }

  function dateTimeText(value) {
    if (!value) return "Not recorded";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return String(value);
    return new Intl.DateTimeFormat(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(parsed);
  }

  function addDays(dateValue, count) {
    const parsed = new Date(`${dateValue}T00:00:00Z`);
    parsed.setUTCDate(parsed.getUTCDate() + count);
    return parsed.toISOString().slice(0, 10);
  }

  function nextMonday() {
    const now = new Date();
    const today = new Date(Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()));
    const weekday = today.getUTCDay();
    const days = (8 - (weekday || 7)) % 7;
    today.setUTCDate(today.getUTCDate() + days);
    return today.toISOString().slice(0, 10);
  }

  function isMonday(value) {
    if (!value) return false;
    const parsed = new Date(`${value}T00:00:00Z`);
    return !Number.isNaN(parsed.getTime()) && parsed.getUTCDay() === 1;
  }

  function displayUnknown(value, suffix = "") {
    if (value === null || value === undefined || value === "") return "Unknown — not recorded";
    return `${String(value)}${suffix}`;
  }

  function idempotencyKey(prefix) {
    const random = window.crypto && typeof window.crypto.randomUUID === "function"
      ? window.crypto.randomUUID().replaceAll("-", "")
      : `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
    return `${prefix}-${random}`;
  }

  function canWrite() {
    return Boolean(state.canWrite && state.tenant && state.user);
  }

  function captureWorkspace() {
    return { tenantId: state.tenant?.id || null, sequence: state.loadSequence };
  }

  function workspaceIsCurrent(context) {
    return Boolean(
      context?.tenantId &&
      state.tenant?.id === context.tenantId &&
      state.loadSequence === context.sequence,
    );
  }

  function setTenantPicker() {
    const picker = $("#company-picker");
    picker.replaceChildren();
    for (const tenant of state.tenants) {
      const option = node("option", "", `${tenant.name} · ${tenant.company?.legal_name || "Company"}`);
      option.value = tenant.id;
      option.selected = tenant.id === state.tenant?.id;
      picker.append(option);
    }
    setVisible($("#company-picker-wrap"), state.tenants.length > 1);
    const company = state.tenant?.company;
    $("#company-caption").textContent = state.tenant
      ? `${state.tenant.name} · ${company?.legal_name || "Company"} · ${company?.base_currency || ""}`
      : "No Company workspace is selected.";
  }

  function clearAssessmentAttestations() {
    $("#capacity-current-confirmed").checked = false;
    $("#commitments-complete-confirmed").checked = false;
  }

  function clearDecisionDraft() {
    $("#decision-outcome").value = "";
    $("#decision-rationale").value = "";
    $("#decision-conditions").value = "";
    $("#decision-requested-info").value = "";
    $("#decision-information-response").value = "";
    $("#decision-information-response").required = false;
    setVisible($("#decision-response-wrap"), false);
    $("#approval-outcome").value = "approved";
    $("#approval-rationale").value = "";
    updateDecisionFields();
  }

  function updateRoleUi() {
    state.canWrite = ["owner", "admin"].includes(state.tenant?.role);
    const notice = $("#role-notice");
    if (!state.canWrite) {
      notice.textContent = "Read-only Company membership. You can inspect project-intake details and static catalogs, but staged imports, M6 capacity assessments, and M7 decisions contain workforce evidence and require workforce access. M4 writes, assessments, submissions, and approvals require an authorized owner/admin. The server remains authoritative for every permission check.";
      setVisible(notice, true);
    } else {
      notice.textContent = "Owner/admin access is active for this Company. M7 approval still requires a different authorized person than the decision submitter.";
      setVisible(notice, false);
    }
    $("#upload-button").disabled = !canWrite();
    $("#operational-file").disabled = !canWrite();
    $("#intake-select").disabled = state.intakeLoading;
    $("#save-intake-button").disabled = !canWrite() || state.intakeLoading;
    $("#run-assessment-button").disabled = !canWrite() || !state.intake || state.intakeLoading;
  }

  async function loadTenants() {
    const { data } = await request("/tenants");
    state.tenants = Array.isArray(data?.items) ? data.items : [];
    if (!state.tenants.length) {
      state.tenant = null;
      setVisible($("#auth-required"), false);
      setVisible($("#empty-workspace"), true);
      setVisible($("#capacity-workspace"), false);
      return;
    }
    const savedId = sessionStorage.getItem(SESSION_KEYS.tenant);
    state.tenant = state.tenants.find((item) => item.id === savedId) || state.tenants[0];
    sessionStorage.setItem(SESSION_KEYS.tenant, state.tenant.id);
    setVisible($("#auth-required"), false);
    setVisible($("#empty-workspace"), false);
    setVisible($("#capacity-workspace"), true);
    $("#user-label").textContent = state.user?.display_name || "";
    setVisible($("#user-label"), true);
    setTenantPicker();
    updateRoleUi();
    await loadWorkspace();
  }

  function resetWorkspaceState() {
    state.intakeSequence += 1;
    state.assessmentSequence += 1;
    state.intakeLoading = false;
    state.demo = null;
    state.importAccessDenied = false;
    state.workforceAccessDenied = false;
    state.importItems = [];
    state.stagedImport = null;
    state.mappingResponse = null;
    state.validationReport = null;
    state.validationPreview = null;
    state.intakeItems = [];
    state.intake = null;
    state.assessmentItems = [];
    state.assessment = null;
    state.metricRun = null;
    state.decisions = null;
    state.currentDecision = null;
    $("#upload-form").reset();
    $("#inspection-summary").replaceChildren();
    $("#mapping-editor").replaceChildren();
    $("#validation-summary").replaceChildren();
    $("#validation-issues").replaceChildren();
    $("#validation-preview").replaceChildren();
    $("#import-report-content").replaceChildren();
    $("#staged-import-panel").hidden = true;
    $("#mapping-panel").hidden = true;
    $("#validation-panel").hidden = true;
    $("#import-report-panel").hidden = true;
    renderImportHistory();
    renderIntakeOptions();
    renderSyntheticFixtureActions();
  }

  async function loadWorkspace() {
    if (!state.tenant) return;
    const tenantId = state.tenant.id;
    const sequence = ++state.loadSequence;
    clearMessage();
    resetWorkspaceState();
    resetIntakeForm();
    setTenantPicker();
    updateRoleUi();
    setStatus("Loading company-scoped M4–M7 evidence…");
    try {
      const [importsResult, intakesResult, demoResult] = await Promise.all([
        request(`/tenants/${tenantId}/imports?limit=100&offset=0`).catch((error) => {
          if (error.status === 403) return { data: { items: [] }, permissionDenied: true };
          throw error;
        }),
        request(`/tenants/${tenantId}/project-intakes?limit=100&offset=0`),
        request(`/tenants/${tenantId}/position-reviews/demo`).catch(() => ({ data: null })),
      ]);
      if (sequence !== state.loadSequence || state.tenant?.id !== tenantId) return;
      state.importAccessDenied = importsResult.permissionDenied === true;
      state.importItems = importsResult.data?.items || [];
      state.intakeItems = intakesResult.data?.items || [];
      state.demo = demoResult.data;
      renderImportHistory();
      renderIntakeOptions();
      renderSyntheticFixtureActions();
      const role = state.tenant.role || "member";
      const importSummary = state.importAccessDenied
        ? "M4 import history requires workforce access"
        : `${state.importItems.length} recent M4 imports`;
      setStatus(`Workspace ready · ${state.tenant.company?.legal_name || state.tenant.name} · ${role} · ${importSummary} · ${state.intakeItems.length} saved proposals.`, "is-success");
      if (!state.intakeItems.length) {
        resetIntakeForm();
        renderAssessmentSelect();
        renderDecisionPanel();
      }
    } catch (error) {
      if (sequence !== state.loadSequence) return;
      setStatus(error.message || "The Company workspace could not be loaded.", "is-error");
      showMessage(error.message || "The Company workspace could not be loaded.");
    }
  }

  async function loadImportHistory() {
    if (!state.tenant) return;
    const context = captureWorkspace();
    const { data } = await request(`/tenants/${context.tenantId}/imports?limit=100&offset=0`);
    if (!workspaceIsCurrent(context)) return;
    state.importItems = data?.items || [];
    renderImportHistory();
  }

  function renderSyntheticFixtureActions() {
    const fixtureToolbar = $("#fixture-toolbar");
    const syntheticNotice = $("#synthetic-notice");
    const seed = state.demo?.demo_seed;
    const active = Boolean(seed);
    const links = $("#fixture-links");
    links.replaceChildren();
    syntheticNotice.replaceChildren();
    setVisible(fixtureToolbar, active);
    setVisible(syntheticNotice, active);
    if (!active) return;

    syntheticNotice.append(node("strong", "", "SYNTHETIC DEMO COMPANY · NOT REAL BUSINESS DATA."));
    syntheticNotice.append(document.createTextNode(` Exact M10 seed ${seed.seed_version || ""}. The M4 API accepts only the date-bound built-in operational CSV fixtures in this seeded Company.`));
    for (const dataset of ["employees", "projects", "weekly_capacity", "resource_assignments"]) {
      const link = node("a", "button button-quiet button-small", `Download synthetic ${DATASET_LABELS[dataset]} CSV`);
      link.href = "#";
      link.addEventListener("click", async (event) => {
        event.preventDefault();
        if (!state.tenant) return;
        const context = captureWorkspace();
        try {
          const blob = await requestBlob(`/tenants/${context.tenantId}/position-reviews/demo/templates/${encodeURIComponent(dataset)}`);
          if (!workspaceIsCurrent(context)) return;
          const objectUrl = URL.createObjectURL(blob);
          const anchor = node("a");
          anchor.href = objectUrl;
          anchor.download = `synthetic_demo_${dataset}.csv`;
          document.body.append(anchor);
          anchor.click();
          anchor.remove();
          URL.revokeObjectURL(objectUrl);
        } catch (error) {
          if (workspaceIsCurrent(context)) {
            showMessage(error.message || "The exact synthetic fixture could not be downloaded.");
          }
        }
      });
      links.append(link);
    }
  }

  function badge(text, kind = "") {
    return node("span", `cw-badge${kind ? ` is-${kind}` : ""}`, text);
  }

  function appendCodeLine(parent, label, value) {
    const line = node("p", "cw-code-line");
    line.append(node("strong", "", `${label}: `));
    line.append(node("code", "", value === null || value === undefined ? "Not available" : String(value)));
    parent.append(line);
  }

  function renderImportHistory() {
    const target = $("#import-history");
    target.replaceChildren();
    $("#refresh-imports-button").disabled = state.importAccessDenied;
    if (state.importAccessDenied) {
      target.append(node("p", "cw-empty-state", "M4 import history can contain employee evidence and requires workforce:read."));
      return;
    }
    if (!state.importItems.length) {
      target.append(node("p", "cw-empty-state", "No M4 imports are saved for this Company. Upload an operational CSV or XLSX to begin."));
      return;
    }
    for (const item of state.importItems.slice(0, 15)) {
      const card = node("article", "cw-import-history-card");
      const heading = node("div", "cw-history-heading");
      heading.append(node("strong", "", item.file_name));
      heading.append(badge(item.status.replaceAll("_", " "), item.status.includes("warning") ? "warning" : item.status === "applied" ? "success" : "muted"));
      card.append(heading);
      card.append(node("p", "", `${DATASET_LABELS[item.dataset_type] || "Dataset not selected"} · ${item.counts?.total_rows ?? 0} rows · ${dateTimeText(item.created_at)}`));
      card.append(node("p", "cw-history-counts", `Valid ${item.counts?.valid_rows ?? 0} · invalid ${item.counts?.invalid_rows ?? 0} · warnings ${item.counts?.warning_rows ?? 0} · skipped ${item.counts?.skipped_rows ?? 0}`));
      const hashes = node("details", "cw-history-hashes");
      hashes.append(node("summary", "", "File, validation and mapping evidence"));
      appendCodeLine(hashes, "M4 import ID", item.id);
      appendCodeLine(hashes, "File SHA-256", item.file_hash);
      appendCodeLine(hashes, "Validation SHA-256", item.validation_hash);
      hashes.append(node("p", "fine-print", `Mapping version ${item.mapping_version}. A mapping or file fingerprint proves which bytes and mapping M4 recorded; it does not authenticate the external source or verify current canonical values.`));
      card.append(hashes);
      const reportButton = node("button", "button button-quiet button-small", "Open saved M4 report");
      reportButton.type = "button";
      reportButton.addEventListener("click", () => loadSavedImportReport(item.id));
      card.append(reportButton);
      target.append(card);
    }
  }

  async function loadSavedImportReport(importId) {
    if (!state.tenant) return;
    const context = captureWorkspace();
    try {
      const { data } = await request(`/tenants/${context.tenantId}/imports/${importId}/report`);
      if (!workspaceIsCurrent(context)) return;
      renderSavedImportReport(data);
    } catch (error) {
      if (workspaceIsCurrent(context)) {
        showMessage(error.message || "The saved M4 report could not be loaded.");
      }
    }
  }

  function renderSavedImportReport(report) {
    const box = $("#import-report-content");
    box.replaceChildren();
    const summary = report.import;
    box.append(node("p", "cw-report-summary", `${summary.file_name} · ${DATASET_LABELS[summary.dataset_type] || "Dataset not selected"} · ${summary.status.replaceAll("_", " ")}`));
    appendCodeLine(box, "File SHA-256", summary.file_hash);
    appendCodeLine(box, "Validation SHA-256", summary.validation_hash);
    box.append(node("p", "fine-print", `M4 mapping version ${summary.mapping_version} · ${summary.counts.total_rows} total rows · ${summary.counts.invalid_rows} invalid · ${summary.counts.warning_rows} warnings · ${summary.counts.duplicate_rows} duplicates · ${summary.counts.skipped_rows} skipped. ${report.retention_note}`));
    const issues = node("div", "cw-validation-issues");
    issues.append(node("h4", "", "Validation issue counts"));
    if (!report.issue_counts?.length) {
      issues.append(node("p", "fine-print", "No classified row issues were retained in this report."));
    } else {
      const list = node("ul", "cw-issue-list");
      for (const issue of report.issue_counts) list.append(node("li", "", `${issue.severity} · ${issue.code} · ${issue.count}`));
      issues.append(list);
    }
    box.append(issues);
    if (report.validation_report) {
      const details = node("details", "cw-json-details");
      details.append(node("summary", "", "Full saved validation summary"));
      details.append(node("pre", "cw-json", JSON.stringify(report.validation_report, null, 2)));
      box.append(details);
    }
    setVisible($("#import-report-panel"), true);
    $("#import-report-panel").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function fillDatasetOptions(suggested = []) {
    const select = $("#dataset-select");
    select.replaceChildren();
    for (const dataset of DATASETS) {
      const extra = suggested.includes(dataset) ? " · suggested by headers" : "";
      const option = node("option", "", `${DATASET_LABELS[dataset]}${extra}`);
      option.value = dataset;
      select.append(option);
    }
    if (suggested.length) select.value = suggested[0];
  }

  function fillWorksheetOptions(worksheets = []) {
    const select = $("#worksheet-select");
    select.replaceChildren();
    for (const worksheet of worksheets) {
      const option = node("option", "", `${worksheet.name} · ${worksheet.row_count} rows`);
      option.value = worksheet.name;
      select.append(option);
    }
  }

  function renderInspection() {
    const target = $("#inspection-summary");
    target.replaceChildren();
    const imported = state.stagedImport?.import;
    if (!imported) return;
    const inspection = state.stagedImport.inspection;
    target.append(node("p", "", `${imported.file_name} · ${inspection.file_format.toUpperCase()} · SHA-256 ${imported.file_hash}`));
    target.append(node("p", "fine-print", `${inspection.total_rows} data rows across ${inspection.worksheets.length} worksheet(s) · blank rows ignored ${inspection.blank_rows_ignored} · staged rows expire ${dateTimeText(imported.staging_expires_at)}.`));
    const datasetSelect = $("#dataset-select");
    fillDatasetOptions(state.stagedImport.suggested_datasets || []);
    const worksheetSelect = $("#worksheet-select");
    fillWorksheetOptions(inspection.worksheets || []);
    if (inspection.worksheets?.length) worksheetSelect.value = inspection.worksheets[0].name;
    $("#staged-status").textContent = imported.status.replaceAll("_", " ");
    setVisible($("#staged-import-panel"), true);
    setVisible($("#mapping-panel"), false);
    setVisible($("#validation-panel"), false);
    setVisible($("#import-report-panel"), false);
    datasetSelect.focus();
  }

  async function uploadOperationalFile(event) {
    event.preventDefault();
    clearMessage();
    if (!canWrite()) {
      showMessage("An authorized owner/admin permission is required to upload operational data.");
      return;
    }
    const file = $("#operational-file").files?.[0];
    if (!file) {
      showMessage("Choose a CSV or XLSX file before uploading.");
      return;
    }
    const context = captureWorkspace();
    const button = $("#upload-button");
    button.disabled = true;
    try {
      const form = new FormData();
      form.append("file", file);
      const { data } = await request(`/tenants/${context.tenantId}/imports`, {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey("capacity-import") },
        body: form,
      });
      if (!workspaceIsCurrent(context)) return;
      state.stagedImport = data;
      state.mappingResponse = null;
      state.validationReport = null;
      state.validationPreview = null;
      renderInspection();
      await loadImportHistory();
      if (workspaceIsCurrent(context)) {
        showMessage("File staged and inspected by M4. Choose its dataset and review every source column before saving a mapping.", "success");
      }
    } catch (error) {
      if (workspaceIsCurrent(context)) showMessage(error.message || "The operational file could not be staged.");
    } finally {
      button.disabled = !canWrite();
    }
  }

  async function loadImportMapping() {
    if (!state.stagedImport?.import?.id) return;
    if (!canWrite()) {
      showMessage("An authorized owner/admin permission is required to map this import.");
      return;
    }
    const context = captureWorkspace();
    const importId = state.stagedImport.import.id;
    const dataset = $("#dataset-select").value;
    const worksheet = $("#worksheet-select").value;
    if (!dataset || !worksheet) {
      showMessage("Choose an M4 dataset and worksheet first.");
      return;
    }
    const button = $("#load-mapping-button");
    button.disabled = true;
    try {
      const query = new URLSearchParams({ dataset_type: dataset, worksheet_name: worksheet });
      const { data } = await request(`/tenants/${context.tenantId}/imports/${importId}/mapping?${query}`);
      if (!workspaceIsCurrent(context)) return;
      state.mappingResponse = data;
      state.validationReport = null;
      state.validationPreview = null;
      renderMappingEditor();
      setVisible($("#mapping-panel"), true);
      setVisible($("#validation-panel"), false);
      setVisible($("#import-report-panel"), false);
    } catch (error) {
      if (workspaceIsCurrent(context)) showMessage(error.message || "The M4 mapping contract could not be loaded.");
    } finally {
      button.disabled = false;
    }
  }

  function renderMappingEditor() {
    const editor = $("#mapping-editor");
    editor.replaceChildren();
    const response = state.mappingResponse;
    if (!response) return;
    const headers = state.stagedImport.inspection.worksheets.find(
      (item) => item.name === $("#worksheet-select").value,
    )?.headers || [];
    const fieldDefinitions = response.field_definitions || [];
    const saved = response.import?.field_mapping || {};
    const suggestions = response.suggested_mapping || {};
    const selectedByHeader = new Map();
    for (const [field, header] of Object.entries(saved)) selectedByHeader.set(header, field);
    for (const [field, header] of Object.entries(suggestions)) {
      if (!selectedByHeader.has(header)) selectedByHeader.set(header, field);
    }
    const ignored = new Set(response.ignored_columns || []);
    editor.append(node("div", "cw-mapping-column cw-mapping-head", "Source column"));
    editor.append(node("div", "cw-mapping-choice cw-mapping-head", "Map to a canonical field or explicitly ignore"));
    for (const header of headers) {
      const label = node("label", "cw-mapping-column", header);
      label.htmlFor = `mapping-${encodeURIComponent(header)}`;
      const select = node("select", "cw-mapping-choice");
      select.id = `mapping-${encodeURIComponent(header)}`;
      select.dataset.header = header;
      const none = node("option", "", "— not mapped —");
      none.value = "";
      select.append(none);
      const ignoreOption = node("option", "", "Explicitly ignore this source column");
      ignoreOption.value = "__ignore__";
      select.append(ignoreOption);
      for (const field of fieldDefinitions) {
        const option = node("option", "", `${field.name}${field.required ? " · required" : ""} — ${field.description}`);
        option.value = field.name;
        select.append(option);
      }
      if (ignored.has(header)) select.value = "__ignore__";
      else select.value = selectedByHeader.get(header) || "";
      editor.append(label, select);
    }
    $("#mapping-version").textContent = `Current M4 version ${response.import?.mapping_version ?? 0}`;
    $("#validate-import-button").disabled = !canWrite() || !response.import?.field_mapping;
  }

  function collectMapping() {
    const fieldMapping = {};
    const ignoredColumns = [];
    const unaccounted = [];
    for (const select of $$("#mapping-editor select")) {
      const header = select.dataset.header;
      if (select.value === "__ignore__") ignoredColumns.push(header);
      else if (select.value) fieldMapping[select.value] = header;
      else unaccounted.push(header);
    }
    return { fieldMapping, ignoredColumns, unaccounted };
  }

  async function saveImportMapping() {
    if (!state.stagedImport?.import?.id || !state.mappingResponse) return;
    if (!canWrite()) {
      showMessage("An authorized owner/admin permission is required to save a mapping.");
      return;
    }
    const context = captureWorkspace();
    const importId = state.stagedImport.import.id;
    const { fieldMapping, ignoredColumns, unaccounted } = collectMapping();
    if (unaccounted.length) {
      showMessage(`Map or explicitly ignore every source column. Unaccounted: ${unaccounted.join(", ")}.`);
      return;
    }
    const button = $("#save-mapping-button");
    button.disabled = true;
    try {
      const body = {
        dataset_type: $("#dataset-select").value,
        worksheet_name: $("#worksheet-select").value,
        field_mapping: fieldMapping,
        ignored_columns: ignoredColumns,
      };
      const { data } = await request(`/tenants/${context.tenantId}/imports/${importId}/mapping`, {
        method: "PUT",
        body,
      });
      if (!workspaceIsCurrent(context)) return;
      state.mappingResponse = data;
      state.validationReport = null;
      state.validationPreview = null;
      renderMappingEditor();
      $("#validate-import-button").disabled = !canWrite();
      await loadImportHistory();
      if (workspaceIsCurrent(context)) {
        showMessage("Mapping saved. Run M4 row validation to review data quality and issues.", "success");
      }
    } catch (error) {
      if (workspaceIsCurrent(context)) showMessage(error.message || "The M4 mapping could not be saved.");
    } finally {
      button.disabled = false;
    }
  }

  async function validateImport() {
    if (!state.stagedImport?.import?.id || !canWrite()) return;
    const context = captureWorkspace();
    const importId = state.stagedImport.import.id;
    const button = $("#validate-import-button");
    button.disabled = true;
    try {
      const base = `/tenants/${context.tenantId}/imports/${importId}`;
      const { data: report } = await request(`${base}/validate`, { method: "POST" });
      if (!workspaceIsCurrent(context)) return;
      const { data: preview } = await request(`${base}/preview?limit=25&offset=0`);
      if (!workspaceIsCurrent(context)) return;
      state.validationReport = report;
      state.validationPreview = preview;
      renderValidation();
      await loadImportHistory();
      if (workspaceIsCurrent(context)) {
        showMessage("M4 validation is complete. Review invalid rows, warnings, duplicates, and the exact validation hash before applying.", "success");
      }
    } catch (error) {
      if (workspaceIsCurrent(context)) showMessage(error.message || "M4 validation could not be completed.");
    } finally {
      button.disabled = !canWrite();
    }
  }

  function renderValidation() {
    const report = state.validationReport;
    const preview = state.validationPreview;
    if (!report || !preview) return;
    const imported = report.import;
    const counts = imported.counts;
    $("#validation-status").textContent = imported.status.replaceAll("_", " ");
    const summary = $("#validation-summary");
    summary.replaceChildren();
    const countGrid = node("div", "cw-count-grid");
    for (const [label, value] of [
      ["Total rows", counts.total_rows],
      ["Valid", counts.valid_rows],
      ["Invalid", counts.invalid_rows],
      ["Warnings", counts.warning_rows],
      ["Duplicates", counts.duplicate_rows],
      ["Eligible", counts.eligible_rows],
    ]) {
      const item = node("div", "cw-count-item");
      item.append(node("span", "", label));
      item.append(node("strong", "", String(value)));
      countGrid.append(item);
    }
    summary.append(countGrid);
    appendCodeLine(summary, "File SHA-256", imported.file_hash);
    appendCodeLine(summary, "Validation SHA-256", imported.validation_hash);
    summary.append(node("p", "fine-print", `Mapping version ${imported.mapping_version}. Staged row values are purged after successful apply, cancellation, or expiry; hashes, issues, and canonical row links remain under M4 retention.`));

    const issueTarget = $("#validation-issues");
    issueTarget.replaceChildren();
    issueTarget.append(node("h4", "", "Classified validation issues"));
    if (!report.issue_counts?.length) issueTarget.append(node("p", "fine-print", "No classified issues were reported."));
    else {
      const list = node("ul", "cw-issue-list");
      for (const issue of report.issue_counts) list.append(node("li", "", `${issue.severity} · ${issue.code} · ${issue.count}`));
      issueTarget.append(list);
    }

    const previewTarget = $("#validation-preview");
    previewTarget.replaceChildren();
    previewTarget.append(node("h4", "", `First ${preview.items.length} source rows · ${preview.total} total`));
    if (!preview.items.length) previewTarget.append(node("p", "fine-print", "No preview rows are available."));
    else {
      const tableWrap = node("div", "cw-table-wrap");
      const table = node("table", "cw-preview-table");
      const columns = new Set();
      for (const row of preview.items) {
        for (const key of Object.keys(row.source_values || {})) columns.add(key);
      }
      const headerRow = node("tr");
      for (const label of ["Row", "M4 state", ...columns, "Issues"]) headerRow.append(node("th", "", label));
      const thead = node("thead");
      thead.append(headerRow);
      table.append(thead);
      const tbody = node("tbody");
      for (const row of preview.items) {
        const tr = node("tr");
        tr.append(node("td", "", `${row.worksheet_name} · ${row.row_number}`));
        tr.append(node("td", "", row.row_status.replaceAll("_", " ")));
        for (const column of columns) tr.append(node("td", "", row.source_values?.[column] ?? "—"));
        tr.append(node("td", "", row.issues?.map((issue) => `${issue.severity}: ${issue.message}`).join("; ") || "—"));
        tbody.append(tr);
      }
      table.append(tbody);
      tableWrap.append(table);
      previewTarget.append(tableWrap);
    }

    const partialLabel = $("#partial-confirm-label");
    const warningLabel = $("#warning-confirm-label");
    $("#allow-partial").checked = false;
    $("#acknowledge-warnings").checked = false;
    setVisible(partialLabel, counts.invalid_rows > 0);
    setVisible(warningLabel, counts.warning_rows > 0);
    setVisible($("#confirmation-controls"), ["validated", "ready"].includes(imported.status));
    updateApplyEnabled();
    setVisible($("#validation-panel"), true);
    $("#validation-panel").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function updateApplyEnabled() {
    const report = state.validationReport;
    const button = $("#apply-import-button");
    if (!report) {
      button.disabled = true;
      return;
    }
    const counts = report.import.counts;
    const hasRows = counts.eligible_rows > 0 || counts.duplicate_rows > 0;
    const partialOk = counts.invalid_rows === 0 || $("#allow-partial").checked;
    const warningsOk = counts.warning_rows === 0 || $("#acknowledge-warnings").checked;
    button.disabled = !canWrite() || !hasRows || !partialOk || !warningsOk;
  }

  async function applyImport() {
    const report = state.validationReport;
    if (!report || !canWrite()) return;
    const context = captureWorkspace();
    const importId = report.import.id;
    const button = $("#apply-import-button");
    button.disabled = true;
    try {
      const { data } = await request(`/tenants/${context.tenantId}/imports/${importId}/confirm`, {
        method: "POST",
        body: {
          validation_hash: report.import.validation_hash,
          allow_partial: $("#allow-partial").checked,
          acknowledge_warnings: $("#acknowledge-warnings").checked,
        },
      });
      const { data: applied } = await request(`/tenants/${context.tenantId}/imports/${importId}/apply`, { method: "POST" });
      if (!workspaceIsCurrent(context)) return;
      if (state.stagedImport?.import?.id === importId) state.stagedImport.import = applied.import;
      await loadImportHistory();
      if (!workspaceIsCurrent(context)) return;
      await loadIntakes();
      if (!workspaceIsCurrent(context)) return;
      const message = `M4 import applied · ${applied.links_created} source-row lineage link(s) created. M5 will read canonical Company facts on the next assessment; import-time fingerprints do not prove values remain current.`;
      showMessage(message, "success");
      setStatus(`${data.status === "confirmed" ? "Import confirmed and applied" : "Import applied"} · file ${applied.import.file_hash} · validation ${applied.import.validation_hash}.`, "is-success");
      if (state.stagedImport?.import?.id === importId) setVisible($("#confirmation-controls"), false);
    } catch (error) {
      if (workspaceIsCurrent(context)) {
        showMessage(error.message || "The M4 import could not be confirmed and applied.");
        try {
          await loadImportHistory();
        } catch (_ignored) {
          // Keep the primary apply error visible.
        }
      }
    } finally {
      button.disabled = !canWrite();
    }
  }

  function hoursToCents(value) {
    const match = /^(?:(\d+)(?:\.(\d{1,2}))?|\.(\d{1,2}))$/.exec(String(value).trim());
    if (!match) return null;
    try {
      const whole = BigInt(match[1] || "0");
      const fraction = BigInt((match[2] || match[3] || "").padEnd(2, "0") || "0");
      return whole * 100n + fraction;
    } catch (_error) {
      return null;
    }
  }

  function readWeeklyDemand() {
    return $$("#weekly-demand-grid input[data-week]").map((input) => ({
      week_start_date: input.dataset.week,
      hours: input.value.trim(),
    })).filter((item) => item.hours !== "");
  }

  function renderWeeklyDemandGrid(values = null) {
    const grid = $("#weekly-demand-grid");
    const prior = values || readWeeklyDemand();
    const byWeek = new Map(prior.map((item) => [item.week_start_date, item.hours]));
    grid.replaceChildren();
    const start = $("#start-week").value;
    const count = Number.parseInt($("#week-count").value, 10);
    if (!isMonday(start) || !Number.isInteger(count) || count < 1 || count > 52) {
      grid.append(node("p", "cw-empty-state", "Choose a valid Monday and a week count from 1 to 52 to enter weekly team-hours."));
      return;
    }
    for (let index = 0; index < count; index += 1) {
      const week = addDays(start, index * 7);
      const item = node("label", "cw-demand-week");
      const head = node("span", "cw-demand-week-label", `Week of ${dateText(week)}`);
      const input = node("input");
      input.type = "number";
      input.min = "0";
      input.max = "1000000";
      input.step = "0.01";
      input.inputMode = "decimal";
      input.dataset.week = week;
      input.setAttribute("aria-label", `Proposed team-hours for week starting ${week}`);
      input.placeholder = "Unknown / not entered";
      input.value = byWeek.get(week) ?? "";
      item.append(head, input);
      grid.append(item);
    }
  }

  function resetIntakeForm() {
    state.intakeSequence += 1;
    state.assessmentSequence += 1;
    state.intakeLoading = false;
    state.intake = null;
    state.workforceAccessDenied = false;
    clearAssessmentAttestations();
    clearDecisionDraft();
    $("#intake-select").value = "";
    $("#project-code").value = "";
    $("#project-name").value = "";
    $("#start-week").value = nextMonday();
    $("#week-count").value = "4";
    $("#total-demand").value = "";
    $("#proposal-notes").value = "";
    $("#distribution-confirmed").checked = false;
    $("#intake-version-badge").textContent = "New proposal · not yet saved";
    $("#save-intake-button").textContent = "Create proposal";
    renderWeeklyDemandGrid([]);
    state.assessmentItems = [];
    state.assessment = null;
    state.metricRun = null;
    state.decisions = { items: [], total: 0, current_version: null };
    state.currentDecision = null;
    renderAssessmentSelect();
    renderAssessmentResult();
    renderDecisionPanel();
    updateRoleUi();
  }

  function renderIntakeOptions() {
    const select = $("#intake-select");
    const selectedId = state.intake?.id || "";
    select.replaceChildren();
    const newOption = node("option", "", "＋ New proposed workload");
    newOption.value = "";
    select.append(newOption);
    for (const intake of state.intakeItems) {
      const option = node("option", "", `${intake.project_code} · ${intake.data.name} · v${intake.current_version}`);
      option.value = intake.id;
      select.append(option);
    }
    select.value = state.intakeItems.some((item) => item.id === selectedId) ? selectedId : "";
  }

  async function loadIntakes() {
    if (!state.tenant) return;
    const context = captureWorkspace();
    const selectedId = state.intake?.id || "";
    const { data } = await request(`/tenants/${context.tenantId}/project-intakes?limit=100&offset=0`);
    if (!workspaceIsCurrent(context)) return;
    state.intakeItems = data?.items || [];
    renderIntakeOptions();
    if (selectedId && state.intakeItems.some((item) => item.id === selectedId)) {
      $("#intake-select").value = selectedId;
    }
  }

  async function selectIntake(intakeId) {
    const context = captureWorkspace();
    if (!intakeId) {
      resetIntakeForm();
      return;
    }
    resetIntakeForm();
    const sequence = ++state.intakeSequence;
    state.intakeLoading = true;
    $("#intake-select").value = intakeId;
    $("#intake-version-badge").textContent = "Loading saved proposal…";
    updateRoleUi();
    try {
      const { data } = await request(`/tenants/${context.tenantId}/project-intakes/${intakeId}`);
      if (!workspaceIsCurrent(context) || sequence !== state.intakeSequence) return;
      state.intakeLoading = false;
      state.intake = data;
      const intakeData = data.data;
      $("#project-code").value = intakeData.project_code;
      $("#project-name").value = intakeData.name;
      $("#start-week").value = intakeData.start_week_date;
      $("#week-count").value = String(intakeData.week_count);
      $("#total-demand").value = intakeData.total_demand_hours;
      $("#proposal-notes").value = intakeData.notes || "";
      $("#distribution-confirmed").checked = intakeData.demand_distribution_confirmed;
      renderWeeklyDemandGrid(intakeData.weekly_demand || []);
      $("#intake-version-badge").textContent = `Saved version ${data.current_version} · SHA-256 ${data.current_version_hash}`;
      $("#save-intake-button").textContent = "Save new intake version";
      updateRoleUi();
      await loadIntakeHistory(intakeId);
    } catch (error) {
      if (workspaceIsCurrent(context) && sequence === state.intakeSequence) {
        resetIntakeForm();
        showMessage(error.message || "The saved proposal could not be loaded.");
      }
    }
  }

  async function saveIntake(event) {
    event.preventDefault();
    clearMessage();
    if (!canWrite()) {
      showMessage("An authorized owner/admin permission is required to save a proposed workload.");
      return;
    }
    const startWeek = $("#start-week").value;
    if (!isMonday(startWeek)) {
      showMessage("The first week must start on a Monday. No demand has been saved.");
      return;
    }
    const weekCount = Number.parseInt($("#week-count").value, 10);
    if (!Number.isInteger(weekCount) || weekCount < 1 || weekCount > 52) {
      showMessage("Choose a supported period of 1 to 52 Monday-based weeks.");
      return;
    }
    const weeklyDemand = readWeeklyDemand().map((item) => ({
      week_start_date: item.week_start_date,
      hours: item.hours,
    }));
    const distributionConfirmed = $("#distribution-confirmed").checked;
    if (distributionConfirmed && weeklyDemand.length !== weekCount) {
      showMessage("A confirmed distribution needs an explicit value for every week. Use 0.00 only when zero is intentionally declared; do not leave unknown weeks blank.");
      return;
    }
    if (distributionConfirmed) {
      const declaredCents = hoursToCents($("#total-demand").value);
      const weeklyCents = weeklyDemand.map((item) => hoursToCents(item.hours));
      if (declaredCents === null || weeklyCents.some((value) => value === null)) {
        showMessage("Use non-negative team-hour values with at most two decimal places.");
        return;
      }
      const distributedCents = weeklyCents.reduce((sum, value) => sum + value, 0n);
      if (distributedCents !== declaredCents) {
        showMessage("The confirmed weekly team-hour values must sum exactly to the declared total. M6 will not spread or adjust demand.");
        return;
      }
    }
    const context = captureWorkspace();
    const intakeAtSubmit = state.intake;
    const intakeSequence = state.intakeSequence;
    const data = {
      project_code: $("#project-code").value.trim(),
      name: $("#project-name").value.trim(),
      start_week_date: startWeek,
      week_count: weekCount,
      total_demand_hours: $("#total-demand").value,
      demand_unit: "team_hours",
      weekly_demand: weeklyDemand,
      demand_distribution_confirmed: distributionConfirmed,
      notes: $("#proposal-notes").value.trim() || null,
    };
    const button = $("#save-intake-button");
    button.disabled = true;
    try {
      let response;
      if (intakeAtSubmit) {
        const { data: updated } = await request(`/tenants/${context.tenantId}/project-intakes/${intakeAtSubmit.id}`, {
          method: "PUT",
          body: { expected_version: intakeAtSubmit.current_version, data },
        });
        response = updated;
      } else {
        const { data: created } = await request(`/tenants/${context.tenantId}/project-intakes`, {
          method: "POST",
          body: data,
        });
        response = created;
      }
      if (!workspaceIsCurrent(context) || state.intakeSequence !== intakeSequence) return;
      state.intake = response;
      await loadIntakes();
      if (!workspaceIsCurrent(context) || state.intakeSequence !== intakeSequence) return;
      $("#intake-select").value = response.id;
      $("#intake-version-badge").textContent = `Saved version ${response.current_version} · SHA-256 ${response.current_version_hash}`;
      $("#save-intake-button").textContent = "Save new intake version";
      updateRoleUi();
      await loadIntakeHistory(response.id);
      if (workspaceIsCurrent(context) && state.intakeSequence === intakeSequence) {
        showMessage(`Proposal version ${response.current_version} saved immutably. Weekly demand values are explicit; missing weeks were not spread from the declared total.`, "success");
      }
    } catch (error) {
      if (workspaceIsCurrent(context) && state.intakeSequence === intakeSequence) {
        showMessage(error.message || "The proposed workload could not be saved.");
      }
    } finally {
      button.disabled = !canWrite() || state.intakeLoading;
    }
  }

  async function loadIntakeHistory(intakeId) {
    if (!state.tenant || !intakeId) return;
    const context = captureWorkspace();
    const intakeSequence = state.intakeSequence;
    try {
      const [assessmentResult, decisionResult] = await Promise.all([
        request(`/tenants/${context.tenantId}/project-intakes/${intakeId}/assessments?limit=100&offset=0`),
        request(`/tenants/${context.tenantId}/project-intakes/${intakeId}/decisions?limit=100&offset=0`),
      ]);
      if (
        !workspaceIsCurrent(context) ||
        state.intakeSequence !== intakeSequence ||
        state.intake?.id !== intakeId
      ) return;
      state.workforceAccessDenied = false;
      state.assessmentItems = assessmentResult.data?.items || [];
      state.decisions = decisionResult.data || { items: [], total: 0, current_version: null };
      state.currentDecision = state.decisions.items.find((item) => item.is_current) || null;
      renderAssessmentSelect();
      renderDecisionPanel();
      if (state.assessmentItems.length) {
        await loadAssessment(state.assessmentItems[0].id);
      } else {
        state.assessment = null;
        state.metricRun = null;
        renderAssessmentResult();
        renderDecisionPanel();
      }
    } catch (error) {
      if (
        !workspaceIsCurrent(context) ||
        state.intakeSequence !== intakeSequence ||
        state.intake?.id !== intakeId
      ) return;
      if (error.status !== 403) throw error;
      state.workforceAccessDenied = true;
      state.assessmentItems = [];
      state.assessment = null;
      state.metricRun = null;
      state.decisions = { items: [], total: 0, current_version: null };
      state.currentDecision = null;
      renderAssessmentSelect();
      renderAssessmentResult();
      renderDecisionPanel();
    }
  }

  function renderAssessmentSelect() {
    const select = $("#assessment-select");
    select.replaceChildren();
    if (!state.assessmentItems.length) {
      const option = node("option", "", state.workforceAccessDenied
        ? "M6 evidence requires workforce access"
        : "No assessment yet");
      option.value = "";
      select.append(option);
      select.disabled = true;
      return;
    }
    for (const item of state.assessmentItems) {
      const option = node("option", "", `M6 ${item.capacity_status.replaceAll("_", " ")} · ${dateTimeText(item.created_at)} · v${item.intake_version}`);
      option.value = item.id;
      select.append(option);
    }
    select.disabled = false;
    if (state.assessment) select.value = state.assessment.id;
    else select.value = state.assessmentItems[0].id;
  }

  async function loadAssessment(assessmentId) {
    if (!state.intake || !assessmentId) return;
    const sequence = ++state.assessmentSequence;
    const context = captureWorkspace();
    const intakeId = state.intake.id;
    try {
      const { data } = await request(`/tenants/${context.tenantId}/project-intakes/${intakeId}/assessments/${assessmentId}`);
      if (
        !workspaceIsCurrent(context) ||
        sequence !== state.assessmentSequence ||
        state.intake?.id !== intakeId
      ) return;
      state.assessment = data;
      state.metricRun = null;
      try {
        const { data: metricRun } = await request(`/tenants/${context.tenantId}/metrics/runs/${data.metric_run_id}`);
        if (
          workspaceIsCurrent(context) &&
          sequence === state.assessmentSequence &&
          state.intake?.id === intakeId &&
          state.assessment?.id === assessmentId
        ) state.metricRun = metricRun;
      } catch (_error) {
        // The M6 result remains readable; show missing M5 drill-through explicitly.
      }
      if (!workspaceIsCurrent(context) || sequence !== state.assessmentSequence) return;
      renderAssessmentSelect();
      renderAssessmentResult();
      renderDecisionPanel();
    } catch (error) {
      if (workspaceIsCurrent(context) && sequence === state.assessmentSequence) {
        showMessage(error.message || "The exact M6 assessment could not be loaded.");
      }
    }
  }

  async function runAssessment() {
    if (!state.intake || !canWrite()) {
      showMessage("Save a proposal and use an authorized owner/admin account before running M6.");
      return;
    }
    const context = captureWorkspace();
    const intakeId = state.intake.id;
    const intakeSequence = state.intakeSequence;
    const intakeVersion = state.intake.current_version;
    const button = $("#run-assessment-button");
    button.disabled = true;
    try {
      const body = {
        intake_version: intakeVersion,
        assumptions: {
          capacity_data_current_confirmed: $("#capacity-current-confirmed").checked,
          recorded_commitments_complete_confirmed: $("#commitments-complete-confirmed").checked,
        },
      };
      const { data } = await request(`/tenants/${context.tenantId}/project-intakes/${intakeId}/assessments`, {
        method: "POST",
        body,
      });
      if (
        !workspaceIsCurrent(context) ||
        state.intakeSequence !== intakeSequence ||
        state.intake?.id !== intakeId
      ) return;
      state.assessment = data;
      await loadIntakeHistory(intakeId);
      if (
        !workspaceIsCurrent(context) ||
        state.intakeSequence !== intakeSequence ||
        state.intake?.id !== intakeId
      ) return;
      await loadAssessment(data.id);
      if (workspaceIsCurrent(context) && state.intakeSequence === intakeSequence) {
        clearAssessmentAttestations();
        showMessage(`M6 saved an immutable ${data.capacity_status.replaceAll("_", " ")} capacity assessment. Overall business feasibility remains incomplete.`, "success");
      }
    } catch (error) {
      if (workspaceIsCurrent(context)) {
        showMessage(error.message || "The M6 capacity comparison could not be completed.");
      }
    } finally {
      button.disabled = !canWrite() || !state.intake;
    }
  }

  function appendList(parent, title, items, className = "cw-reason-list") {
    const section = node("section", "cw-result-detail");
    section.append(node("h4", "", title));
    if (!Array.isArray(items) || !items.length) {
      section.append(node("p", "fine-print", "None recorded."));
    } else {
      const list = node("ul", className);
      for (const item of items) {
        const text = typeof item === "string" ? item : `${item.code || "Evidence"}: ${item.message || JSON.stringify(item)}`;
        list.append(node("li", "", text));
      }
      section.append(list);
    }
    parent.append(section);
  }

  function renderAssessmentResult() {
    const target = $("#assessment-result");
    target.replaceChildren();
    const assessment = state.assessment;
    if (!assessment) {
      const message = state.workforceAccessDenied
        ? "M6 assessments include M5 workforce evidence and require workforce:read. The saved project-intake demand remains readable."
        : state.intake
          ? "No M6 assessment has been saved for this proposal yet. Record assumptions and run a comparison."
          : "Save a proposal and run M6 to create a capacity-only assessment.";
      target.append(node("p", "cw-empty-state", message));
      return;
    }
    const result = assessment.result_snapshot || {};
    const heading = node("div", `cw-result-hero is-${assessment.capacity_status}`);
    heading.append(node("p", "eyebrow", "M6 · COMPANY-AGGREGATE CAPACITY ONLY"));
    heading.append(node("h3", "", STATUS_LABELS[assessment.capacity_status] || assessment.capacity_status));
    heading.append(node("p", "", `Period ${dateText(assessment.start_week_date)} – ${dateText(assessment.end_week_date)} · ${assessment.week_count} Monday–Sunday week(s) · requested ${dateTimeText(assessment.created_at)}.`));
    target.append(heading);

    const businessBoundary = node("div", "cw-business-incomplete");
    businessBoundary.append(node("strong", "", "Overall business feasibility: INCOMPLETE — always."));
    businessBoundary.append(node("p", "", "M6 assesses capacity only. It is not financial feasibility, project acceptance/rejection, commercial viability, employee allocation, or skill matching."));
    target.append(businessBoundary);

    const evidence = node("details", "cw-evidence-card");
    evidence.open = true;
    evidence.append(node("summary", "", "Exact M4–M6 evidence binding and hashes"));
    const evidenceBody = node("div", "cw-evidence-body");
    const intakeBinding = assessment.input_snapshot?.intake || {};
    appendCodeLine(evidenceBody, "Proposal intake ID", intakeBinding.id);
    appendCodeLine(evidenceBody, "Intake version", intakeBinding.version);
    appendCodeLine(evidenceBody, "Intake version hash", intakeBinding.version_hash);
    appendCodeLine(evidenceBody, "M6 assessment ID", assessment.id);
    appendCodeLine(evidenceBody, "M6 input hash", assessment.input_hash);
    appendCodeLine(evidenceBody, "M6 result hash", assessment.result_hash);
    appendCodeLine(evidenceBody, "M5 metric run ID", assessment.metric_run_id);
    appendCodeLine(evidenceBody, "M5 input hash", assessment.metric_input_hash);
    appendCodeLine(evidenceBody, "M5 result hash", assessment.metric_result_hash);
    appendCodeLine(evidenceBody, "M5 catalog / calculation", `${assessment.metric_catalog_version} / ${assessment.metric_calculation_version}`);
    if (state.metricRun) {
      appendCodeLine(evidenceBody, "M5 run reliability", state.metricRun.reliability_status);
      const provenance = state.metricRun.result_snapshot?.provenance || {};
      evidenceBody.append(node("p", "fine-print", `${provenance.facts_with_import_lineage ?? 0} canonical facts have recorded M4 lineage; ${provenance.facts_without_recorded_import_lineage ?? 0} have none. ${provenance.import_fingerprint_scope || "M4 lineage does not authenticate or verify current source values."}`));
    } else {
      evidenceBody.append(node("p", "fine-print", "The exact M5 metric run could not be loaded for source drill-through; M6 hashes are still shown."));
    }
    evidence.append(evidenceBody);
    target.append(evidence);

    const weekTableWrap = node("div", "cw-table-wrap");
    const table = node("table", "cw-result-table");
    const columns = [
      "Week",
      "Proposed demand · team-hours",
      "Aggregate recorded capacity",
      "Recorded committed planned hours",
      "M5 capacity minus recorded commitments",
      "M6 projected remaining",
      "Evidence states",
    ];
    const head = node("thead");
    const trHead = node("tr");
    for (const label of columns) trHead.append(node("th", "", label));
    head.append(trHead);
    table.append(head);
    const body = node("tbody");
    for (const week of result.weekly || []) {
      const row = node("tr");
      const demand = week.demand || {};
      row.append(node("th", "", `${dateText(week.week_start_date)} – ${dateText(week.week_end_date)}`));
      row.append(node("td", "", `${displayUnknown(demand.hours, " h")} · ${demand.state || "state unknown"}`));
      row.append(node("td", "", `${displayUnknown(week.available_capacity_hours, " h")} · ${week.capacity_basis || "basis unknown"}`));
      row.append(node("td", "", `${displayUnknown(week.existing_committed_hours, " h")} · ${week.existing_commitments_state || "state unknown"}`));
      row.append(node("td", "", displayUnknown(week.remaining_capacity_before_project_hours, " h")));
      row.append(node("td", "", displayUnknown(week.projected_remaining_capacity_hours, " h")));
      const stateCell = node("td", "");
      stateCell.append(badge(`Demand ${demand.state || "unknown"}`, demand.state === "missing" || demand.state === "unconfirmed" ? "warning" : "muted"));
      stateCell.append(badge(`Capacity ${week.capacity_basis || "unknown"}`, week.capacity_basis === "estimated" ? "warning" : "muted"));
      stateCell.append(badge(`Commitments ${week.existing_commitments_state || "unknown"}`, week.existing_commitments_state === "not_recorded_not_zero" || week.existing_commitments_state === "unresolved" ? "warning" : "muted"));
      row.append(stateCell);
      body.append(row);
      const detailRow = node("tr", "cw-detail-row");
      const detailCell = node("td", "", null);
      detailCell.colSpan = columns.length;
      detailCell.append(renderWeekEvidence(week));
      detailRow.append(detailCell);
      body.append(detailRow);
    }
    table.append(body);
    weekTableWrap.append(table);
    target.append(weekTableWrap);

    const outcomeSections = node("div", "cw-assessment-notes");
    appendList(outcomeSections, "M6 blockers", result.blockers || []);
    appendList(outcomeSections, "M6 warnings and assumptions", result.warnings || []);
    appendList(outcomeSections, "M6 limitations", result.limitations || []);
    target.append(outcomeSections);
    renderDecisionPanel();
  }

  function renderWeekEvidence(week) {
    const details = node("details", "cw-week-evidence");
    details.append(node("summary", "", `M5 evidence and M4 source-row lineage for week starting ${week.week_start_date}`));
    const content = node("div", "cw-week-evidence-body");
    const m5Evidence = week.m5_weekly_evidence || {};
    const capacity = m5Evidence.capacity || {};
    const assignments = m5Evidence.assignments || {};
    const comparison = m5Evidence.comparison || {};
    content.append(node("p", "", `M5 coverage: ${capacity.reliability_state || "not reported"}; active-company count ${capacity.active_employee_count ?? "unknown"}; confirmed ${capacity.confirmed_employee_count ?? "unknown"}; estimated ${capacity.estimated_employee_count ?? "unknown"}; explicitly unknown ${capacity.unknown_employee_count ?? "unknown"}; missing ${capacity.missing_employee_count ?? "unknown"}; invalid/conflicting ${capacity.invalid_employee_count ?? "unknown"}.`));
    content.append(node("p", "", `M5 assignment evidence: ${assignments.committed_assignment_count ?? "unknown"} committed planned assignment(s); ${assignments.proposed_assignment_count ?? "unknown"} proposed planned assignment(s). No assignment is presented as actual work.`));
    content.append(node("p", "", `M5 comparison state: ${comparison.state || "not reported"}. Capacity basis: ${week.capacity_basis || "unknown"}. Existing commitment state: ${week.existing_commitments_state || "unknown"}.`));
    const reasonList = [];
    for (const reason of capacity.reasons || []) reasonList.push(`Capacity: ${reason}`);
    for (const reason of assignments.reasons || []) reasonList.push(`Assignments: ${reason}`);
    for (const reason of comparison.reasons || []) reasonList.push(`Comparison: ${reason}`);
    appendList(content, "M5 evidence-quality reasons", reasonList);

    const sourceIds = week.source_evidence || {};
    const recordGroup = node("div", "cw-source-records");
    recordGroup.append(node("h4", "", "Canonical record references used by M6"));
    recordGroup.append(node("p", "fine-print", "IDs below identify aggregate evidence rows; no person-level capacity, assignment allocation, or skills match is shown."));
    const capacityIds = sourceIds.capacity_source_record_ids || [];
    const activeIds = sourceIds.active_committed_assignment_ids || [];
    const inactiveIds = sourceIds.inactive_employee_committed_assignment_ids || [];
    const proposedIds = sourceIds.proposed_assignment_ids || [];
    appendCodeLine(recordGroup, "Weekly-capacity record IDs", capacityIds.length ? capacityIds.join(", ") : "None linked in this M5 snapshot");
    appendCodeLine(recordGroup, "Active committed assignment IDs", activeIds.length ? activeIds.join(", ") : "None recorded in this week");
    appendCodeLine(recordGroup, "Inactive-employee commitment IDs", inactiveIds.length ? inactiveIds.join(", ") : "None recorded in this week");
    appendCodeLine(recordGroup, "Proposed assignment IDs (not subtracted)", proposedIds.length ? proposedIds.join(", ") : "None recorded in this week");
    content.append(recordGroup);

    const input = state.metricRun?.input_snapshot || {};
    const capacityRows = (input.weekly_capacity || []).filter((item) => item.week_start_date === week.week_start_date);
    const assignmentIds = new Set([...activeIds, ...inactiveIds, ...proposedIds]);
    const assignmentRows = (input.resource_assignments || []).filter((item) => assignmentIds.has(item.id));
    const lineageSection = node("div", "cw-lineage-section");
    lineageSection.append(node("h4", "", "Original M4 import lineage"));
    lineageSection.append(node("p", "fine-print", "M4 source-row links are exact file/row provenance. Import-time fingerprints are not a check that mutable canonical facts still match their source."));
    const lineageEntries = [];
    for (const row of capacityRows) {
      for (const reference of row.lineage || []) lineageEntries.push({ ...reference, canonical_type: "weekly_capacity", canonical_id: row.id });
    }
    for (const row of assignmentRows) {
      for (const reference of row.lineage || []) lineageEntries.push({ ...reference, canonical_type: "resource_assignment", canonical_id: row.id });
    }
    if (!lineageEntries.length) {
      lineageSection.append(node("p", "cw-no-lineage", "No M4 source-row lineage is attached to the capacity or assignment records used for this week. Those canonical facts may have been entered manually or through a path without retained M4 links; their source is not inferred."));
    } else {
      for (const reference of lineageEntries) {
        const card = node("div", "cw-lineage-item");
        card.append(node("strong", "", `${DATASET_LABELS[reference.dataset_type] || reference.dataset_type || reference.canonical_type} · ${reference.import_status || "import status unknown"}`));
        card.append(node("p", "", `M4 import ${reference.import_id} · worksheet ${reference.worksheet_name} · source row ${reference.row_number} · mapping v${reference.mapping_version}`));
        appendCodeLine(card, "Source file SHA-256", reference.file_hash);
        appendCodeLine(card, "Source row SHA-256", reference.row_hash);
        appendCodeLine(card, "M4 source-row ID", reference.source_row_id);
        lineageSection.append(card);
      }
    }
    content.append(lineageSection);
    details.append(content);
    return details;
  }

  async function submitDecision(event) {
    event.preventDefault();
    clearMessage();
    if (!state.intake || !state.assessment || !canWrite()) {
      showMessage("A saved proposal, M6 assessment, and owner/admin permission are required before an M7 submission.");
      return;
    }
    const selectedLatestAssessment =
      state.assessmentItems[0]?.id === state.assessment.id &&
      state.assessment.intake_version === state.intake.current_version;
    if (!selectedLatestAssessment) {
      showMessage("M7 submissions must use the newest M6 assessment for the current intake version. Run M6 again after changing the proposal.");
      return;
    }
    const context = captureWorkspace();
    const intakeId = state.intake.id;
    const intakeSequence = state.intakeSequence;
    const assessmentId = state.assessment.id;
    const outcome = $("#decision-outcome").value;
    const rationale = $("#decision-rationale").value.trim();
    const conditions = $("#decision-conditions").value.split("\n").map((item) => item.trim()).filter(Boolean);
    const requestedInformation = $("#decision-requested-info").value.split("\n").map((item) => item.trim()).filter(Boolean);
    const informationResponse = $("#decision-information-response").value.trim();
    if (rationale.length < 10) {
      showMessage("Add a decision rationale of at least 10 characters.");
      return;
    }
    if (outcome === "hold" && !conditions.length) {
      showMessage("A hold requires at least one condition for resuming review.");
      return;
    }
    if (outcome === "request_information" && !requestedInformation.length) {
      showMessage("List at least one item of information to request.");
      return;
    }
    if (state.currentDecision?.outcome === "request_information" && !informationResponse) {
      showMessage("Respond to the open information request before creating a new decision version.");
      return;
    }
    const body = {
      assessment_id: assessmentId,
      outcome,
      rationale,
      conditions,
      requested_information: outcome === "request_information" ? requestedInformation : [],
    };
    if (state.decisions?.current_version !== null && state.decisions?.current_version !== undefined) {
      body.expected_version = state.decisions.current_version;
    }
    if (state.currentDecision?.outcome === "request_information") body.information_response = informationResponse;
    const button = $("#submit-decision-button");
    button.disabled = true;
    try {
      const { data } = await request(`/tenants/${context.tenantId}/project-intakes/${intakeId}/decisions`, {
        method: "POST",
        body,
      });
      if (!workspaceIsCurrent(context) || state.intakeSequence !== intakeSequence) return;
      await loadIntakeHistory(intakeId);
      if (workspaceIsCurrent(context) && state.intakeSequence === intakeSequence) {
        showMessage(`M7 decision version ${data.version} submitted and hash-bound to M6 ${data.assessment_id}. A different authorized owner/admin must review it.`, "success");
      }
    } catch (error) {
      if (workspaceIsCurrent(context)) showMessage(error.message || "The M7 decision could not be submitted.");
    } finally {
      button.disabled = !canWrite();
    }
  }

  function renderDecisionPanel() {
    const entry = $("#decision-entry-panel");
    const approvalPanel = $("#approval-panel");
    const history = $("#decision-history");
    history.replaceChildren();
    const assessment = state.assessment;
    const decisions = state.decisions?.items || [];
    const current = state.currentDecision;
    const intakeId = state.intake?.id;

    $("#refresh-decisions-button").disabled = !intakeId || state.workforceAccessDenied;
    if (!intakeId) {
      $("#decision-scope-badge").textContent = "No decision selected";
      entry.hidden = true;
      approvalPanel.hidden = true;
      history.append(node("p", "cw-empty-state", "Choose a saved proposal to load its M7 history."));
      return;
    }
    if (state.workforceAccessDenied) {
      $("#decision-scope-badge").textContent = "Workforce access required";
      entry.hidden = true;
      approvalPanel.hidden = true;
      history.append(node("p", "cw-empty-state", "M6 assessment and M7 decision history bind workforce evidence and require workforce:read. Project-intake demand remains available under Company Context read access."));
      return;
    }
    if (!assessment) {
      $("#decision-scope-badge").textContent = "M6 assessment required";
      entry.hidden = true;
      approvalPanel.hidden = true;
      history.append(node("p", "cw-empty-state", "Run M6 before submitting or reviewing a capacity-stage decision."));
      return;
    }
    const isLatestAssessment =
      state.assessmentItems[0]?.id === assessment.id &&
      assessment.intake_version === state.intake.current_version;
    $("#decision-scope-badge").textContent = isLatestAssessment
      ? `${assessment.capacity_status.replaceAll("_", " ")} · M7 capacity stage only`
      : `M6 refresh required · assessment v${assessment.intake_version} · intake v${state.intake.current_version}`;

    const sameAssessment = current?.assessment_id === assessment.id;
    const hasPendingForThisAssessment = sameAssessment && current?.status === "pending_approval" && !current.approval;
    const alreadyApprovedForThisAssessment = sameAssessment && current?.status === "approved";
    const canSubmit = canWrite() && isLatestAssessment && !hasPendingForThisAssessment && !alreadyApprovedForThisAssessment;
    setVisible(entry, canSubmit);
    if (canSubmit) configureDecisionForm();

    const canReview = Boolean(current && current.status === "pending_approval" && !current.approval);
    setVisible(approvalPanel, canReview);
    if (canReview) configureApprovalPanel(current);

    if (!isLatestAssessment) {
      history.append(node(
        "p",
        "cw-empty-state",
        `This M6 assessment is bound to proposal version ${assessment.intake_version}, while the current proposal is version ${state.intake.current_version}. Run a fresh M6 assessment before submitting a capacity-stage decision.`,
      ));
    }
    if (!decisions.length) {
      if (isLatestAssessment) {
        history.append(node("p", "cw-empty-state", "No M7 decisions have been submitted for this proposal."));
      }
      return;
    }
    for (const decision of decisions) history.append(renderDecisionCard(decision));
  }

  function configureDecisionForm() {
    const select = $("#decision-outcome");
    const priorValue = select.value;
    select.replaceChildren(node("option", "", "Choose an outcome…"));
    const status = state.assessment?.capacity_status;
    const allowed = status === "feasible" || status === "feasible_with_conditions"
      ? ["proceed_to_financial_evaluation", "hold", "request_information"]
      : status === "not_feasible"
        ? ["capacity_declined", "hold", "request_information"]
        : ["hold", "request_information"];
    for (const value of allowed) {
      const option = node("option", "", DECISION_LABELS[value]);
      option.value = value;
      select.append(option);
    }
    select.value = allowed.includes(priorValue) ? priorValue : "";
    const responseRequired = state.currentDecision?.outcome === "request_information";
    setVisible($("#decision-response-wrap"), responseRequired);
    $("#decision-information-response").required = responseRequired;
    setVisible($("#decision-conditions-wrap"), select.value === "hold" || (select.value === "proceed_to_financial_evaluation" && status === "feasible_with_conditions"));
    setVisible($("#decision-info-wrap"), select.value === "request_information");
    $("#decision-conditions").required = select.value === "hold" || (select.value === "proceed_to_financial_evaluation" && status === "feasible_with_conditions");
    $("#decision-requested-info").required = select.value === "request_information";
    const latest =
      state.assessmentItems[0]?.id === state.assessment?.id &&
      state.assessment?.intake_version === state.intake?.current_version;
    const context = [];
    context.push(`Submitted decision will bind to M6 assessment ${state.assessment.id}, M5 run ${state.assessment.metric_run_id}, and current intake version ${state.intake.current_version}.`);
    if (!latest) context.push("Run the newest M6 assessment for this intake version before submitting to M7.");
    if (state.currentDecision?.outcome === "request_information") context.push("The previous version requested information; a response is required before superseding it.");
    if (status === "feasible_with_conditions") context.push("M7 requires explicit conditions before recording a proceed-to-financial-evaluation outcome.");
    context.push("M7 does not perform or approve financial evaluation; this remains a capacity-stage human record.");
    $("#decision-form-context").textContent = context.join(" ");
    $("#submit-decision-button").disabled = !canWrite() || !latest;
  }

  function configureApprovalPanel(decision) {
    const eligibility = $("#approval-eligibility");
    const binding = $("#approval-binding");
    const form = $("#approval-form");
    const readonly = $("#approval-readonly-note");
    binding.replaceChildren();
    appendCodeLine(binding, "Decision version", `${decision.version} · ${decision.id}`);
    appendCodeLine(binding, "Submitter user ID", decision.created_by_user_id);
    appendCodeLine(binding, "Exact M6 assessment", decision.assessment_id);
    appendCodeLine(binding, "Evidence SHA-256", decision.evidence_hash);
    appendCodeLine(binding, "Decision SHA-256", decision.decision_hash);
    const allowed = canWrite() && decision.created_by_user_id !== state.user?.id && decision.evidence_freshness === "fresh" && decision.is_current;
    const reason = !canWrite()
      ? "Read-only role"
      : decision.created_by_user_id === state.user?.id
        ? "Submitter cannot approve own decision"
        : decision.evidence_freshness !== "fresh"
          ? `Evidence ${decision.evidence_freshness}; refresh M6 before approval`
          : !decision.is_current
            ? "Only the current version can be reviewed"
            : "Different authorized owner/admin required";
    eligibility.textContent = allowed ? "Eligible for independent review" : reason;
    eligibility.classList.toggle("is-warning", !allowed);
    setVisible(form, allowed);
    setVisible(readonly, !allowed);
    readonly.textContent = allowed ? "" : `${reason}. The API enforces authorization, self-review rejection, current-version checks, exact hashes, and evidence freshness.`;
    $("#approval-rationale").value = "";
    $("#approval-outcome").value = "approved";
  }

  function renderDecisionCard(decision) {
    const card = node("article", `cw-decision-card${decision.is_current ? " is-current" : ""}`);
    const heading = node("div", "cw-decision-card-heading");
    const left = node("div");
    left.append(node("p", "eyebrow", `M7 DECISION · VERSION ${decision.version}${decision.is_current ? " · CURRENT" : ""}`));
    left.append(node("h4", "", DECISION_LABELS[decision.outcome] || decision.outcome.replaceAll("_", " ")));
    heading.append(left);
    heading.append(badge(decision.status.replaceAll("_", " "), decision.status === "approved" ? "success" : decision.status === "changes_requested" ? "warning" : "muted"));
    card.append(heading);
    card.append(node("p", "", `Submitted ${dateTimeText(decision.created_at)} · actor ${decision.created_by_user_id} · M6 capacity ${decision.capacity_status.replaceAll("_", " ")} · business feasibility ${decision.business_feasibility_status} · evidence ${decision.evidence_freshness}.`));
    card.append(node("p", "cw-decision-rationale", decision.rationale));
    if (decision.conditions?.length) card.append(node("p", "", `Conditions: ${decision.conditions.join("; ")}`));
    if (decision.requested_information?.length) card.append(node("p", "", `Requested information: ${decision.requested_information.join("; ")}`));
    if (decision.information_response) card.append(node("p", "", `Response to prior request: ${decision.information_response}`));
    const binding = node("details", "cw-decision-hashes");
    binding.append(node("summary", "", "Evidence binding and immutable hashes"));
    appendCodeLine(binding, "Intake version / hash", `${decision.intake_version} / ${decision.intake_version_hash}`);
    appendCodeLine(binding, "M6 assessment ID", decision.assessment_id);
    appendCodeLine(binding, "M6 input / result hashes", `${decision.assessment_input_hash} / ${decision.assessment_result_hash}`);
    appendCodeLine(binding, "M5 metric run ID", decision.metric_run_id);
    appendCodeLine(binding, "M5 input / result hashes", `${decision.metric_input_hash} / ${decision.metric_result_hash}`);
    appendCodeLine(binding, "M7 evidence hash", decision.evidence_hash);
    appendCodeLine(binding, "M7 decision hash", decision.decision_hash);
    if (decision.staleness_reasons?.length) binding.append(node("p", "cw-stale-note", `Staleness reasons: ${decision.staleness_reasons.join(", ")}.`));
    card.append(binding);
    if (decision.approval) {
      const review = node("div", `cw-approval-record${decision.approval.outcome === "approved" ? " is-approved" : " is-changes"}`);
      review.append(node("strong", "", `Reviewer recorded: ${decision.approval.outcome.replaceAll("_", " ")}`));
      review.append(node("p", "", `${decision.approval.rationale} · reviewer ${decision.approval.approver_user_id} · ${dateTimeText(decision.approval.created_at)}`));
      appendCodeLine(review, "Approval hash", decision.approval.approval_hash);
      card.append(review);
    }
    if (!decision.is_current) card.append(node("p", "fine-print", "Superseded immutable version. It remains in history and cannot be newly reviewed."));
    return card;
  }

  async function refreshDecisionHistory() {
    if (!state.tenant || !state.intake || state.workforceAccessDenied) return;
    const context = captureWorkspace();
    const intakeId = state.intake.id;
    const intakeSequence = state.intakeSequence;
    try {
      const { data } = await request(`/tenants/${context.tenantId}/project-intakes/${intakeId}/decisions?limit=100&offset=0`);
      if (
        !workspaceIsCurrent(context) ||
        state.intakeSequence !== intakeSequence ||
        state.intake?.id !== intakeId
      ) return;
      state.decisions = data;
      state.currentDecision = data.items?.find((item) => item.is_current) || null;
      renderDecisionPanel();
    } catch (error) {
      if (workspaceIsCurrent(context) && state.intakeSequence === intakeSequence) {
        showMessage(error.message || "M7 decision history could not be refreshed.");
      }
    }
  }

  async function submitApproval(event) {
    event.preventDefault();
    clearMessage();
    const decision = state.currentDecision;
    const context = captureWorkspace();
    const intakeId = state.intake?.id;
    const intakeSequence = state.intakeSequence;
    if (!decision || !canWrite() || decision.created_by_user_id === state.user?.id) {
      showMessage("Only a different authorized owner/admin can review a submitted decision.");
      return;
    }
    if (!decision.is_current || decision.evidence_freshness !== "fresh") {
      showMessage("This decision is superseded or its evidence is stale. Refresh the exact M6 assessment before reviewing.");
      return;
    }
    const rationale = $("#approval-rationale").value.trim();
    if (rationale.length < 10) {
      showMessage("Add reviewer rationale of at least 10 characters.");
      return;
    }
    const button = $("#submit-approval-button");
    button.disabled = true;
    try {
      const body = {
        expected_decision_hash: decision.decision_hash,
        expected_evidence_hash: decision.evidence_hash,
        outcome: $("#approval-outcome").value,
        rationale,
      };
      await request(`/tenants/${context.tenantId}/project-intakes/${intakeId}/decisions/${decision.id}/approval`, {
        method: "POST",
        body,
      });
      if (
        !workspaceIsCurrent(context) ||
        state.intakeSequence !== intakeSequence ||
        state.intake?.id !== intakeId
      ) return;
      await refreshDecisionHistory();
      if (workspaceIsCurrent(context) && state.intakeSequence === intakeSequence) {
        showMessage("Independent M7 review saved immutably against the exact decision and evidence hashes. No external action was taken.", "success");
      }
    } catch (error) {
      if (workspaceIsCurrent(context) && state.intakeSequence === intakeSequence) {
        showMessage(error.message || "The M7 review could not be saved.");
        await refreshDecisionHistory();
      }
    } finally {
      button.disabled = false;
    }
  }

  function updateDecisionFields() {
    const outcome = $("#decision-outcome").value;
    const status = state.assessment?.capacity_status;
    const conditionsRequired = outcome === "hold" || (outcome === "proceed_to_financial_evaluation" && status === "feasible_with_conditions");
    const infoRequired = outcome === "request_information";
    setVisible($("#decision-conditions-wrap"), conditionsRequired);
    setVisible($("#decision-info-wrap"), infoRequired);
    $("#decision-conditions").required = conditionsRequired;
    $("#decision-requested-info").required = infoRequired;
  }

  function bindEvents() {
    $("#company-picker").addEventListener("change", async (event) => {
      const tenant = state.tenants.find((item) => item.id === event.target.value);
      if (!tenant) return;
      state.tenant = tenant;
      sessionStorage.setItem(SESSION_KEYS.tenant, tenant.id);
      await loadWorkspace();
    });
    $("#refresh-workspace-button").addEventListener("click", loadWorkspace);
    $("#refresh-imports-button").addEventListener("click", async () => {
      try {
        await loadImportHistory();
        showMessage("M4 import history refreshed for the selected Company.", "success");
      } catch (error) {
        showMessage(error.message || "M4 imports could not be refreshed.");
      }
    });
    $("#upload-form").addEventListener("submit", uploadOperationalFile);
    $("#dataset-select").addEventListener("change", loadImportMapping);
    $("#worksheet-select").addEventListener("change", loadImportMapping);
    $("#load-mapping-button").addEventListener("click", loadImportMapping);
    $("#save-mapping-button").addEventListener("click", saveImportMapping);
    $("#validate-import-button").addEventListener("click", validateImport);
    $("#apply-import-button").addEventListener("click", applyImport);
    $("#allow-partial").addEventListener("change", updateApplyEnabled);
    $("#acknowledge-warnings").addEventListener("change", updateApplyEnabled);
    $("#close-import-report-button").addEventListener("click", () => setVisible($("#import-report-panel"), false));

    $("#intake-select").addEventListener("change", (event) => selectIntake(event.target.value));
    $("#reload-intakes-button").addEventListener("click", async () => {
      try {
        await loadIntakes();
        showMessage("Saved proposals refreshed.", "success");
      } catch (error) {
        showMessage(error.message || "Proposals could not be refreshed.");
      }
    });
    $("#intake-form").addEventListener("submit", saveIntake);
    $("#start-week").addEventListener("change", () => renderWeeklyDemandGrid());
    $("#week-count").addEventListener("change", () => renderWeeklyDemandGrid());
    $("#reset-intake-button").addEventListener("click", resetIntakeForm);
    $("#run-assessment-button").addEventListener("click", runAssessment);
    $("#assessment-select").addEventListener("change", (event) => loadAssessment(event.target.value));
    $("#decision-outcome").addEventListener("change", updateDecisionFields);
    $("#decision-form").addEventListener("submit", submitDecision);
    $("#approval-form").addEventListener("submit", submitApproval);
    $("#refresh-decisions-button").addEventListener("click", refreshDecisionHistory);
  }

  async function bootstrap() {
    bindEvents();
    $("#start-week").value = nextMonday();
    renderWeeklyDemandGrid([]);
    if (!state.accessToken) {
      showSignedOut();
      return;
    }
    try {
      const { data } = await request("/auth/me");
      state.user = data;
      setVisible($("#user-label"), true);
      $("#user-label").textContent = data.display_name;
      await loadTenants();
    } catch (_error) {
      clearAuth();
      showSignedOut();
    }
  }

  bootstrap();
})();
