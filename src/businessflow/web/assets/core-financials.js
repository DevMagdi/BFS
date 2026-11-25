(() => {
  "use strict";

  const API_BASE = "/api/v1";
  const SESSION_KEYS = {
    access: "businessflow.m10.access",
    refresh: "businessflow.m10.refresh",
    tenant: "businessflow.m10.tenant",
  };
  const POLICY_KEY = "illustrative.core-financials";
  const MEASURE_LABELS = {
    revenue: "Revenue",
    expenses: "Expenses",
    gross_profit: "Gross Profit",
    operating_profit: "Operating Profit",
    net_profit: "Net Profit",
    cash_flow: "Cash Flow — net observed bank movement",
  };
  const PAGE_SIZE = 100;
  const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;
  const LEDGER_SAMPLE = [
    "source_record_id,transaction_date,account_code,debit,credit,currency,event_type,reversal_of_record_id",
    "sale-001,2026-11-10,4000,0,1000,USD,normal,",
    "refund-001,2026-11-18,4090,100,0,USD,refund,",
  ].join("\n") + "\n";
  const BANK_SAMPLE = [
    "source_record_id,transaction_date,bank_account_id,cash_in,cash_out,currency,event_type,reversal_of_record_id",
    "bank-in-001,2026-11-10,operating-1,750,0,USD,normal,",
    "bank-out-001,2026-11-12,operating-1,0,100,USD,normal,",
    "bank-transfer-001,2026-11-15,operating-1,0,50,USD,transfer,",
  ].join("\n") + "\n";
  const CONTROL_SAMPLE = [
    "control_id,expected_revenue,currency_code",
    "illustrative-control-2026-11,900,USD",
  ].join("\n") + "\n";

  const state = {
    accessToken: sessionStorage.getItem(SESSION_KEYS.access),
    refreshToken: sessionStorage.getItem(SESSION_KEYS.refresh),
    user: null,
    tenants: [],
    tenant: null,
    template: null,
    policies: [],
    policy: null,
    file: null,
    headers: [],
    mapping: null,
    imported: null,
    importHistoryItems: [],
    importHistoryTotal: 0,
    recordItems: [],
    recordTotal: 0,
    recordOffset: 0,
    run: null,
    evidenceItems: [],
    evidenceTotal: 0,
    evidenceOffset: 0,
    controlFile: null,
    importIdempotencyKey: null,
    messageTimer: null,
  };

  const $ = (selector, root = document) => root.querySelector(selector);
  const node = (tag, className = "", text = null) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== null && text !== undefined) element.textContent = String(text);
    return element;
  };
  const show = (element, visible) => { element.hidden = !visible; };

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
  const { clearAuth, request } = api;

  function showSignedOut() {
    show($("#core-app"), false);
    show($("#auth-required"), true);
  }

  function showMessage(message, kind = "error") {
    const box = $("#global-message");
    box.textContent = message;
    box.classList.toggle("is-success", kind === "success");
    box.hidden = false;
    window.clearTimeout(state.messageTimer);
    state.messageTimer = window.setTimeout(() => { box.hidden = true; }, 9000);
  }

  function setStatus(selector, message, kind = "muted") {
    const element = $(selector);
    element.textContent = message;
    element.classList.remove("is-error", "is-success", "muted");
    element.classList.add(kind);
  }

  function coreBase() {
    if (!state.tenant) throw new Error("Choose a Company workspace first.");
    return `/tenants/${encodeURIComponent(state.tenant.id)}/core-financials`;
  }

  function randomIdempotencyKey() {
    const random = window.crypto && typeof window.crypto.randomUUID === "function"
      ? window.crypto.randomUUID().replaceAll("-", "")
      : `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
    return `core-financial-${random}`;
  }

  function shortHash(value) {
    return value ? `${String(value).slice(0, 16)}…` : "Not available";
  }

  function selectedSourceType() {
    return $("#source-type").value;
  }

  function selectedMeasure() {
    return $("#measure-select").value;
  }

  function measureLabel(metric) {
    return MEASURE_LABELS[metric] || metric;
  }

  function setTenantCaption() {
    const tenant = state.tenant;
    $("#company-caption").textContent = tenant
      ? `${tenant.name} · ${tenant.company?.legal_name || "Company"} · ${tenant.company?.base_currency || ""}`
      : "No Company workspace is selected.";
  }

  function fillTenantPicker() {
    const picker = $("#company-picker");
    picker.replaceChildren();
    for (const tenant of state.tenants) {
      const option = new Option(
        `${tenant.name} · ${tenant.company?.legal_name || "Company"}`,
        tenant.id,
      );
      option.selected = tenant.id === state.tenant?.id;
      picker.append(option);
    }
    show($("#company-picker-wrap"), state.tenants.length > 1);
    setTenantCaption();
  }

  function setFeatureDisabled(detail = "The server is configured with CORE_FINANCIALS_ENABLED=false.") {
    show($("#disabled-panel"), true);
    show($("#workflow"), false);
    $("#disabled-detail").textContent = detail;
    $("#workflow-status").replaceChildren();
  }

  function normalizedHeader(value) {
    return value.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/_+/g, "_").replace(/^_|_$/g, "");
  }

  function parseCsvHeader(text) {
    const input = text.replace(/^\uFEFF/, "");
    const headers = [];
    let current = "";
    let quoted = false;
    let ended = false;
    for (let index = 0; index < input.length; index += 1) {
      const character = input[index];
      if (quoted) {
        if (character === '"') {
          if (input[index + 1] === '"') {
            current += '"';
            index += 1;
          } else {
            quoted = false;
          }
        } else {
          current += character;
        }
        continue;
      }
      if (character === '"') {
        if (current.length !== 0) throw new Error("Invalid quote in CSV header row.");
        quoted = true;
      } else if (character === ",") {
        headers.push(current.trim());
        current = "";
      } else if (character === "\n" || character === "\r") {
        headers.push(current.trim());
        if (character === "\r" && input[index + 1] === "\n") index += 1;
        ended = true;
        break;
      } else {
        current += character;
      }
    }
    if (quoted) throw new Error("Unclosed quoted field in CSV header row.");
    if (!ended) headers.push(current.trim());
    if (!headers.length || headers.some((header) => !header || header.length > 128)) {
      throw new Error("CSV headers must be non-empty and no longer than 128 characters.");
    }
    const normalized = headers.map(normalizedHeader);
    if (normalized.some((header) => !header) || new Set(normalized).size !== normalized.length) {
      throw new Error("CSV headers must be unique after case and punctuation normalization.");
    }
    if (headers.length > 256) throw new Error("A CSV may contain no more than 256 columns.");
    return headers;
  }

  async function fileUtf8Text(file) {
    const bytes = await file.arrayBuffer();
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  }

  async function sha256(file) {
    if (!window.crypto || !window.crypto.subtle) {
      throw new Error("This browser does not support SHA-256. Open the app on HTTPS or localhost.");
    }
    const digest = await window.crypto.subtle.digest("SHA-256", await file.arrayBuffer());
    return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
  }

  function downloadCsv(filename, contents) {
    const blob = new Blob([contents], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = node("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  }

  function invalidateReport(message = "Source or policy selections changed. Re-import to bind a new immutable result.") {
    state.imported = null;
    state.run = null;
    state.recordItems = [];
    state.evidenceItems = [];
    state.importIdempotencyKey = null;
    $("#import-summary").replaceChildren();
    $("#record-table").replaceChildren();
    $("#run-result").replaceChildren();
    $("#verification-result").replaceChildren();
    show($("#verify-run-button"), false);
    show($("#load-more-records"), false);
    show($("#load-more-evidence"), false);
    setStatus("#run-state", message, "muted");
    updateRunButton();
  }

  function defaultPolicyDefinition() {
    const definition = JSON.parse(JSON.stringify(state.template.illustrative_policy));
    definition.policy_key = POLICY_KEY;
    definition.category_coverage.revenue = "complete";
    definition.category_coverage.revenue_reduction = "complete";
    return definition;
  }

  function policyDefinition() {
    let definition;
    try {
      definition = JSON.parse($("#policy-definition").value);
    } catch (_error) {
      throw new Error("The policy editor must contain valid JSON before saving a version.");
    }
    if (!definition || typeof definition !== "object" || Array.isArray(definition)) {
      throw new Error("The policy editor must contain one JSON object.");
    }
    return definition;
  }

  function selectPolicy(policy) {
    state.policy = policy || null;
    const selector = $("#policy-history");
    if (policy) {
      if (!state.policies.some((item) => item.id === policy.id)) state.policies.push(policy);
      if (!Array.from(selector.options).some((option) => option.value === policy.id)) {
        selector.append(new Option(
          `${policy.policy_key} · v${policy.version} · ${shortHash(policy.definition_hash)}`,
          policy.id,
        ));
      }
      selector.value = policy.id;
      selector.disabled = false;
      $("#policy-definition").value = JSON.stringify(policy.definition, null, 2);
    }
    const detail = policy
      ? `Policy ${policy.policy_key} · version ${policy.version} · illustrative and non-authoritative. Unknown or unsupported coverage withholds affected measures.`
      : "No policy selected. Review the illustrative example, then create or reuse an explicit version.";
    setStatus("#policy-state", detail, policy ? "is-success" : "muted");
    if (!policy) state.mapping = null;
    const hasPolicy = Boolean(state.policy);
    $("#save-mapping-button").disabled = !hasPolicy || !state.file;
    $("#import-button").disabled = !hasPolicy || !state.mapping || !state.file;
    updateRunButton();
  }

  async function loadPolicies() {
    const { data } = await request(`${coreBase()}/policies?limit=100&offset=0`);
    state.policies = Array.isArray(data?.items) ? data.items : [];
    const selector = $("#policy-history");
    selector.replaceChildren(new Option("Choose a saved policy", ""));
    for (const policy of state.policies) {
      selector.append(new Option(`${policy.policy_key} · v${policy.version} · ${shortHash(policy.definition_hash)}`, policy.id));
    }
    const selected = state.policy
      ? state.policies.find((item) => item.id === state.policy.id)
      : state.policies.find((item) => item.policy_key === POLICY_KEY) || state.policies[0];
    selectPolicy(selected || null);
    selector.disabled = state.policies.length === 0;
  }

  async function createPolicy() {
    const button = $("#create-policy-button");
    button.disabled = true;
    try {
      const { data } = await request(`${coreBase()}/policies`, {
        method: "POST",
        body: policyDefinition(),
      });
      state.policy = data;
      invalidateReport("Policy is versioned. Select an import created from this exact version.");
      await loadPolicies();
      selectPolicy(data);
      showMessage(`Illustrative policy version ${data.version} is saved and ready.`, "success");
    } catch (error) {
      handleError(error);
    } finally {
      button.disabled = false;
    }
  }

  function renderMappingEditor() {
    const fields = $("#mapping-fields");
    fields.replaceChildren();
    const sourceType = selectedSourceType();
    const headerKey = sourceType === "bank_activity" ? "bank_activity_headers" : "general_ledger_headers";
    const canonicalFields = Array.isArray(state.template?.[headerKey])
      ? state.template[headerKey]
      : [];
    $("#source-file-label").textContent = sourceType === "bank_activity"
      ? "Choose a bank-activity CSV"
      : "Choose a general-ledger CSV";
    for (const canonical of canonicalFields) {
      const row = node("div", "cf-mapping-row");
      const label = node("label", "", canonical.replaceAll("_", " "));
      label.htmlFor = `mapping-${canonical}`;
      const select = node("select");
      select.id = `mapping-${canonical}`;
      select.dataset.canonical = canonical;
      select.append(new Option("Choose source header", ""));
      for (const header of state.headers) select.append(new Option(header, header));
      if (state.headers.includes(canonical)) select.value = canonical;
      select.addEventListener("change", refreshIgnoredColumns);
      row.append(label, select);
      fields.append(row);
    }
    refreshIgnoredColumns();
    show($("#mapping-editor"), true);
    const hasPolicy = Boolean(state.policy);
    $("#save-mapping-button").disabled = !hasPolicy;
    $("#import-button").disabled = !state.mapping || !hasPolicy;
  }

  function refreshIgnoredColumns() {
    const selected = new Set(
      Array.from($("#mapping-fields").querySelectorAll("select"))
        .map((element) => element.value)
        .filter(Boolean),
    );
    const previous = new Map(
      Array.from($("#ignored-fields").querySelectorAll(".cf-ignored-row")).map((row) => [
        row.dataset.header,
        { checked: $("input[type='checkbox']", row).checked, rationale: $("input[type='text']", row).value },
      ]),
    );
    const ignored = $("#ignored-fields");
    ignored.replaceChildren();
    const extras = state.headers.filter((header) => !selected.has(header));
    if (!extras.length) {
      ignored.append(node("p", "fine-print", "Every source header is mapped; no ignored columns."));
    }
    for (const header of extras) {
      const row = node("div", "cf-ignored-row");
      row.dataset.header = header;
      const label = node("label");
      const checkbox = node("input");
      checkbox.type = "checkbox";
      checkbox.checked = previous.get(header)?.checked ?? true;
      const text = node("span", "", `Ignore “${header}”`);
      label.append(checkbox, text);
      const rationale = node("input");
      rationale.type = "text";
      rationale.maxLength = 500;
      rationale.value = previous.get(header)?.rationale ?? "Not used in this illustrative calculation";
      rationale.setAttribute("aria-label", `Rationale for ignoring ${header}`);
      row.append(label, rationale);
      ignored.append(row);
    }
  }

  function mappingPayload() {
    const fieldMapping = {};
    const selectedHeaders = new Set();
    for (const select of $("#mapping-fields").querySelectorAll("select")) {
      if (!select.value) throw new Error(`Choose a source header for ${select.dataset.canonical}.`);
      if (selectedHeaders.has(select.value)) throw new Error("A source header may map to only one canonical field.");
      selectedHeaders.add(select.value);
      fieldMapping[select.dataset.canonical] = select.value;
    }
    const ignoredColumns = [];
    for (const row of $("#ignored-fields").querySelectorAll(".cf-ignored-row")) {
      const checkbox = $("input[type='checkbox']", row);
      const rationale = $("input[type='text']", row).value.trim();
      if (!checkbox.checked) throw new Error(`Map or explicitly ignore the ${row.dataset.header} column.`);
      if (!rationale) throw new Error(`Add a rationale for ignoring ${row.dataset.header}.`);
      selectedHeaders.add(row.dataset.header);
      ignoredColumns.push({ header: row.dataset.header, rationale });
    }
    if (selectedHeaders.size !== state.headers.length || state.headers.some((header) => !selectedHeaders.has(header))) {
      throw new Error("Every CSV header must be mapped or explicitly ignored.");
    }
    return {
      source_key: $("#source-key").value.trim(),
      source_type: selectedSourceType(),
      field_mapping: fieldMapping,
      ignored_columns: ignoredColumns,
    };
  }

  async function saveMapping() {
    const button = $("#save-mapping-button");
    if (!state.file || !state.policy) return;
    button.disabled = true;
    try {
      const body = mappingPayload();
      const { data } = await request(`${coreBase()}/mappings`, { method: "POST", body });
      state.mapping = data;
      invalidateReport("Mapping is saved and versioned. Import the selected CSV to normalize its rows.");
      setStatus("#file-state", `Mapping version ${data.version} saved · ${shortHash(data.mapping_hash)}. Ready to import.`, "is-success");
      $("#import-button").disabled = !state.mapping || !state.policy;
    } catch (error) {
      handleError(error);
      button.disabled = !state.file || !state.policy;
    }
  }

  async function processLedgerFile(file) {
    if (!file) return;
    if (file.size <= 0 || file.size > MAX_UPLOAD_BYTES) {
      showMessage("Choose a non-empty CSV no larger than 25 MB (the default server limit).", "error");
      return;
    }
    state.file = file;
    state.headers = [];
    state.mapping = null;
    invalidateReport("File changed. Map headers and import this file before calculating.");
    try {
      const text = await fileUtf8Text(file);
      state.headers = parseCsvHeader(text);
      renderMappingEditor();
      setStatus("#file-state", `${file.name} · ${(file.size / 1024).toFixed(1)} KB · ${state.headers.length} headers parsed in the browser; file not sent yet.`, "is-success");
    } catch (error) {
      state.file = null;
      show($("#mapping-editor"), false);
      setStatus("#file-state", error.message || "The CSV header could not be read.", "is-error");
      showMessage(error.message || "The CSV header could not be read.");
    }
  }

  function loadLedgerExample() {
    $("#source-type").value = "general_ledger";
    $("#source-key").value = "ledger-demo";
    const file = new File([LEDGER_SAMPLE], "illustrative_financials_ledger.csv", { type: "text/csv" });
    processLedgerFile(file);
  }

  function loadBankExample() {
    $("#source-type").value = "bank_activity";
    $("#source-key").value = "bank-demo";
    const file = new File([BANK_SAMPLE], "illustrative_bank_activity.csv", { type: "text/csv" });
    processLedgerFile(file);
  }

  function loadControlExample() {
    if (selectedMeasure() !== "revenue") {
      showMessage("The synthetic control example is Revenue-only. Select Revenue before loading it.");
      return;
    }
    state.controlFile = new File([CONTROL_SAMPLE], "illustrative_revenue_control.csv", { type: "text/csv" });
    $("#control-expected").value = "900";
    $("#control-source-id").value = "illustrative-control-2026-11";
    $("#control-locator").value = "csv:row:2/expected_revenue";
    setStatus("#control-file-state", "Synthetic control CSV selected. Amount, source identity, and independence remain caller assertions.", "is-success");
  }

  function selectedImportPeriod() {
    return {
      start: $("#source-period-start").value,
      end: $("#source-period-end").value,
      completeness: $("#source-completeness").value,
    };
  }

  async function importLedger() {
    const sourceType = selectedSourceType();
    if (
      !state.file
      || !state.policy
      || !state.mapping
      || state.mapping.source_type !== sourceType
    ) {
      showMessage("Select a versioned policy, a source CSV, and a saved mapping for the selected adapter before importing.");
      return;
    }
    const period = selectedImportPeriod();
    if (!period.start || !period.end || period.end < period.start) {
      showMessage("Enter a valid inclusive source period.");
      return;
    }
    if (state.mapping.source_key !== $("#source-key").value.trim()) {
      showMessage("The source key changed after mapping. Save a mapping for the current source key first.");
      return;
    }
    const button = $("#import-button");
    button.disabled = true;
    setStatus("#file-state", "Uploading to the authenticated Company-scoped API and normalizing rows…", "muted");
    try {
      if (!state.importIdempotencyKey) state.importIdempotencyKey = randomIdempotencyKey();
      const body = new FormData();
      body.append("metadata_json", JSON.stringify({
        source_key: $("#source-key").value.trim(),
        source_type: sourceType,
        mapping_id: state.mapping.id,
        policy_id: state.policy.id,
        period_start: period.start,
        period_end: period.end,
        period_completeness: period.completeness,
      }));
      body.append("file", state.file, state.file.name);
      const { data } = await request(`${coreBase()}/imports`, {
        method: "POST",
        headers: { "Idempotency-Key": state.importIdempotencyKey },
        body,
      });
      state.importIdempotencyKey = null;
      state.imported = data;
      state.run = null;
      renderImportSummary(data);
      await loadRecords(true);
      await loadHistory();
      syncRunDates(period.start, period.end);
      updateRunButton();
      const importVerb = data.replayed ? "Existing import reused" : "Import saved";
      showMessage(`${importVerb} · ${data.accepted_rows} accepted, ${data.rejected_rows} rejected, ${data.duplicate_rows} duplicate.`, data.quality_status === "passed" ? "success" : "error");
    } catch (error) {
      handleError(error);
      setStatus("#file-state", error.message || "The CSV could not be imported.", "is-error");
    } finally {
      button.disabled = !state.policy || !state.mapping || !state.file;
      updateRunButton();
    }
  }

  function renderImportSummary(imported) {
    const root = $("#import-summary");
    root.replaceChildren();
    const card = node("article", `cf-summary-card${imported.quality_status === "passed" ? "" : " is-warning"}`);
    card.append(
      node("h3", "", `Import ${imported.quality_status === "passed" ? "validated" : "needs review"}`),
      node("p", "", `${imported.file_name} · ${imported.source_type} · source key ${imported.source_key}`),
      node("p", "", `${imported.accepted_rows} accepted · ${imported.rejected_rows} rejected · ${imported.duplicate_rows} duplicate · period ${imported.period_start} to ${imported.period_end} (${imported.period_completeness}, caller-asserted)`),
      node("p", "cf-hash", `File SHA-256 ${imported.file_hash}`),
      node("p", "cf-hash", `Mapping v${imported.mapping_version} · ${imported.mapping_hash}`),
      node("p", "cf-hash", `Policy v${imported.policy_version} · ${imported.policy_hash}`),
      node("p", "cf-hash", `Import input ${imported.input_hash} · result ${imported.result_hash}`),
    );
    root.append(card);
    if (imported.quality_status !== "passed") {
      card.append(node("p", "cf-row-issues", "Rejected or duplicate source rows block calculated values. Inspect the row issues below."));
    }
  }

  function table(headers, rows) {
    const wrapper = node("div", "cf-table-wrap");
    const result = node("table", "cf-table");
    const thead = node("thead");
    const headRow = node("tr");
    for (const text of headers) headRow.append(node("th", "", text));
    thead.append(headRow);
    const tbody = node("tbody");
    for (const row of rows) {
      const tr = node("tr");
      for (const cell of row) {
        const td = node("td", cell.className || "", cell.value ?? "—");
        tr.append(td);
      }
      tbody.append(tr);
    }
    result.append(thead, tbody);
    wrapper.append(result);
    return wrapper;
  }

  async function loadRecords(reset = false) {
    if (!state.imported) return;
    if (reset) {
      state.recordItems = [];
      state.recordOffset = 0;
      state.recordTotal = 0;
      $("#record-table").replaceChildren();
    }
    const { data } = await request(`${coreBase()}/imports/${state.imported.id}/records?limit=${PAGE_SIZE}&offset=${state.recordOffset}`);
    state.recordItems.push(...(data.items || []));
    state.recordOffset = state.recordItems.length;
    state.recordTotal = data.total || 0;
    renderRecords();
    show($("#load-more-records"), state.recordItems.length < state.recordTotal);
  }

  function renderRecords() {
    const rows = state.recordItems.map((record) => [
      { value: record.source_row_number, className: "cf-mono" },
      { value: record.source_record_id || "—", className: "cf-break" },
      { value: record.transaction_date || "—" },
      { value: record.account_code || "—" },
      { value: record.category || "—" },
      { value: record.signed_amount || "—", className: "cf-mono" },
      { value: record.row_status, className: record.row_status === "accepted" ? "" : "cf-row-issues" },
      { value: (record.issues || []).join(", ") || "—", className: "cf-break cf-row-issues" },
      { value: record.row_hash, className: "cf-break cf-mono" },
    ]);
    $("#record-table").replaceChildren(
      node("p", "fine-print", `Normalized source records · showing ${state.recordItems.length} of ${state.recordTotal}.`),
      table(["Row", "Source record ID", "Date", "Account", "Category", "Signed amount", "Status", "Validation issues", "Source-row hash"], rows),
    );
  }

  function syncRunDates(start, end) {
    $("#report-period-start").value = start;
    $("#report-period-end").value = end;
  }

  function updateRunButton() {
    const imported = state.imported;
    const measure = selectedMeasure();
    const expectedSourceType = measure === "cash_flow" ? "bank_activity" : "general_ledger";
    const sourcePeriod = imported
      ? selectedImportPeriod()
      : { start: "", end: "", completeness: "unknown" };
    const reportStart = $("#report-period-start").value;
    const reportEnd = $("#report-period-end").value;
    const periodCovered = imported
      && reportStart >= imported.period_start
      && reportEnd <= imported.period_end
      && reportEnd >= reportStart;
    const ready = Boolean(
      state.policy
      && imported
      && state.policy.id === imported.policy_id
      && imported.source_type === expectedSourceType
      && imported.quality_status === "passed"
      && imported.period_completeness === "complete"
      && periodCovered
      && sourcePeriod.completeness === "complete",
    );
    $("#create-run-button").disabled = !ready;
    if (!ready && imported) {
      let message = `${measureLabel(measure)} needs its matching source, the import's exact policy version, complete caller-asserted coverage, and a fully accepted import.`;
      if (imported.source_type !== expectedSourceType) {
        message = measure === "cash_flow"
          ? "Cash Flow requires a bank-activity import; select or import bank evidence rather than P&L transactions."
          : `${measureLabel(measure)} requires a general-ledger import.`;
      } else if (state.policy && state.policy.id !== imported.policy_id) {
        message = "The selected policy version differs from the import's immutable policy; restore or create an import for the selected version.";
      } else if (imported.quality_status !== "passed") {
        message = "Fix rejected or duplicate rows and import a clean source before calculating.";
      } else if (imported.period_completeness !== "complete") {
        message = "The source period is not caller-asserted complete; the workflow will withhold the measure.";
      } else if (!periodCovered) {
        message = "The report period must be valid and contained within the imported source period.";
      }
      setStatus("#run-state", message, "muted");
    } else if (ready && !state.run) {
      setStatus("#run-state", "Ready. Source identity and completeness are caller assertions; the server does not independently authenticate the export.", "is-success");
    }
  }

  async function buildControls() {
    const file = state.controlFile;
    const inputs = [
      $("#control-expected").value.trim(),
      $("#control-source-id").value.trim(),
      $("#control-locator").value.trim(),
    ];
    const anyControl = Boolean(file) || inputs.some(Boolean);
    if (!anyControl) return [];
    if (!file || inputs.some((value) => !value)) {
      throw new Error("For reconciliation, provide the control CSV, expected measure value, source ID, and record locator.");
    }
    const expected = inputs[0];
    if (!/^-?(0|[1-9][0-9]*)(\.[0-9]+)?$/.test(expected)) throw new Error("Expected measure value must be plain decimal text.");
    const absolute = $("#control-absolute-tolerance").value.trim() || "0";
    const relative = $("#control-relative-tolerance").value.trim() || "0";
    for (const value of [absolute, relative]) {
      if (!/^(0|[1-9][0-9]*)(\.[0-9]+)?$/.test(value)) throw new Error("Tolerances must be non-negative plain decimal text.");
    }
    return [{
      metric: selectedMeasure(),
      expected_value: expected,
      currency_code: $("#report-currency").value.trim().toUpperCase(),
      absolute_tolerance: absolute,
      relative_tolerance: relative,
      source: {
        source_id: inputs[1],
        source_hash: await sha256(file),
        record_locator: inputs[2],
      },
    }];
  }

  async function createRun() {
    if (!state.imported || !state.policy) return;
    const measure = selectedMeasure();
    const sourceIdField = measure === "cash_flow" ? "bank_import_id" : "ledger_import_id";
    const button = $("#create-run-button");
    button.disabled = true;
    setStatus("#run-state", `Calculating ${measureLabel(measure)} against the matching accepted source rows…`, "muted");
    try {
      const controls = await buildControls();
      const { data } = await request(`${coreBase()}/runs`, {
        method: "POST",
        body: {
          policy_id: state.policy.id,
          measure,
          [sourceIdField]: state.imported.id,
          period_start: $("#report-period-start").value,
          period_end: $("#report-period-end").value,
          currency_code: $("#report-currency").value.trim().toUpperCase(),
          controls,
        },
      });
      state.run = data;
      await renderRun(data);
      await loadHistory();
      showMessage(`${measureLabel(measure)} run persisted with row-level evidence.`, "success");
    } catch (error) {
      handleError(error);
      setStatus("#run-state", error.message || "The financial calculation failed.", "is-error");
    } finally {
      updateRunButton();
    }
  }

  function reconciliationLabel(metric) {
    const stateText = metric.reconciliation?.state || "not_provided";
    const expected = metric.reconciliation?.expected_value;
    const variance = metric.reconciliation?.variance;
    const reasons = metric.reconciliation?.reason_codes || [];
    return `${stateText}${expected ? ` · expected ${expected} ${metric.currency_code}` : ""}${variance !== null && variance !== undefined ? ` · variance ${variance} ${metric.currency_code}` : ""}${reasons.length ? ` · ${reasons.join(", ")}` : ""}`;
  }

  async function renderRun(run) {
    const root = $("#run-result");
    root.replaceChildren();
    const metric = Array.isArray(run.metrics) ? run.metrics[0] : null;
    if (!metric) throw new Error("This saved run does not contain a supported financial measure.");
    const categorySummary = Object.entries(metric.category_totals || {})
      .map(([category, total]) => `${category}: ${total} ${metric.currency_code}`)
      .join(" · ") || "Bank movement under the saved Cash Flow policy; no P&L rows are used.";
    const sourceId = run.ledger_import_id || run.bank_import_id || "not available";
    const card = node("article", `cf-summary-card${metric.status === "calculated" ? "" : " is-warning"}`);
    card.append(
      node("h3", "", metric.status === "calculated"
        ? `${measureLabel(metric.metric)}: ${metric.reported_value} ${metric.currency_code}`
        : `${measureLabel(metric.metric)} ${metric.status}`),
      node("p", "", `${run.period_start} to ${run.period_end} inclusive · ${metric.basis} · ${metric.included_record_count} included rows · source ${run.ledger_import_id ? "general ledger" : "bank activity"} · ${run.authority}`),
      node("p", "", `Versioned policy inputs: ${categorySummary}`),
      node("p", "", `Excluded: ${metric.excluded_outside_period_count} outside period · ${metric.excluded_other_currency_count} other currency · ${metric.excluded_transfer_count} marked transfers. Reasons: ${(metric.reason_codes || []).join(", ") || "none"}`),
      node("p", "", `Reconciliation: ${reconciliationLabel(metric)} · independence ${metric.reconciliation?.source_independence || "caller asserted, not verified"}`),
      node("p", "", `Source import ${sourceId} · run ${run.state} · policy v${run.policy_version} · input ${shortHash(run.input_hash)} · result ${shortHash(run.result_hash)}`),
      node("p", "cf-hash", `Trace hash ${run.trace_hash}`),
    );
    root.append(card);
    for (const statement of run.analysis || []) {
      root.append(node("p", "fine-print", statement.text));
    }
    setStatus("#run-state", `Persisted ${measureLabel(metric.metric)} run ${run.id}. The value is illustrative and non-authoritative.`, metric.status === "calculated" ? "is-success" : "is-error");
    show($("#verify-run-button"), true);
    await loadEvidence(true);
  }

  async function loadEvidence(reset = false) {
    if (!state.run) return;
    if (reset) {
      state.evidenceItems = [];
      state.evidenceTotal = 0;
      state.evidenceOffset = 0;
    }
    const metric = state.run.metrics?.[0]?.metric;
    if (!metric) return;
    const { data } = await request(`${coreBase()}/runs/${state.run.id}/evidence?metric=${encodeURIComponent(metric)}&limit=${PAGE_SIZE}&offset=${state.evidenceOffset}`);
    state.evidenceItems.push(...(data.items || []));
    state.evidenceOffset = state.evidenceItems.length;
    state.evidenceTotal = data.total || 0;
    renderEvidence();
    show($("#load-more-evidence"), state.evidenceItems.length < state.evidenceTotal);
  }

  function renderEvidence() {
    const root = $("#run-result");
    const rows = state.evidenceItems.map((item) => [
      { value: item.source_row_number, className: "cf-mono" },
      { value: item.source_record_id, className: "cf-break" },
      { value: item.transaction_date },
      { value: item.account_code || "—" },
      { value: item.category || "—" },
      { value: item.source_signed_amount, className: "cf-mono" },
      { value: item.formula_coefficient, className: "cf-mono" },
      { value: item.contribution_amount, className: "cf-mono" },
      { value: item.source_file_hash, className: "cf-break cf-mono" },
      { value: item.source_row_hash, className: "cf-break cf-mono" },
    ]);
    const section = node("section", "cf-evidence");
    section.append(
      node("h3", "", `${measureLabel(state.run?.metrics?.[0]?.metric)} evidence · ${state.evidenceItems.length} of ${state.evidenceTotal}`),
      node("p", "fine-print", "Each contribution links a persisted report line to its canonical import record, source file hash, CSV row number, and source-row hash."),
      table(["Source row", "Record ID", "Date", "Account", "Category", "Signed amount", "Coefficient", "Contribution", "File hash", "Row hash"], rows),
    );
    root.append(section);
  }

  async function verifyRun() {
    if (!state.run) return;
    const button = $("#verify-run-button");
    button.disabled = true;
    try {
      const { data } = await request(`${coreBase()}/runs/${state.run.id}/verify`, { method: "POST" });
      const root = $("#verification-result");
      root.replaceChildren();
      const card = node("article", `cf-summary-card${data.passed ? "" : " is-warning"}`);
      card.append(
        node("h3", "", data.passed ? "Replay verification passed" : "Replay verification failed"),
        node("p", "", `Input hash: recorded ${shortHash(data.recorded_input_hash)} · replayed ${shortHash(data.replayed_input_hash)}`),
        node("p", "", `Result hash: recorded ${shortHash(data.recorded_result_hash)} · replayed ${shortHash(data.replayed_result_hash)}`),
        node("p", "", `Reason codes: ${(data.reason_codes || []).join(", ") || "none"} · engine ${data.engine_version}`),
      );
      root.append(card);
    } catch (error) {
      handleError(error);
    } finally {
      button.disabled = false;
    }
  }

  function renderImportHistory() {
    const importRoot = $("#import-history");
    importRoot.replaceChildren();
    if (!state.importHistoryItems.length) {
      importRoot.append(node("p", "fine-print", "No saved imports for this Company."));
    }
    for (const item of state.importHistoryItems) {
      const card = node("article", "cf-history-item");
      const isSelected = item.id === state.imported?.id;
      const selectButton = node(
        "button",
        "button button-secondary button-small",
        isSelected ? "Selected import" : "Select saved import",
      );
      selectButton.type = "button";
      selectButton.dataset.importId = item.id;
      selectButton.setAttribute("aria-pressed", String(isSelected));
      selectButton.addEventListener("click", () => selectSavedImport(item.id));
      card.classList.toggle("is-selected", isSelected);
      card.append(
        node("p", "", `${item.source_key} · ${item.source_type} · ${item.period_start} to ${item.period_end}`),
        node("p", "", `${item.accepted_rows} accepted · ${item.rejected_rows} rejected · ${item.duplicate_rows} duplicate · ${item.period_completeness} completeness`),
        node("p", "", `${item.file_name} · SHA-256 ${shortHash(item.file_hash)} · ${item.quality_status}`),
        node("p", "cf-hash", `Original mapping v${item.mapping_version} · ${shortHash(item.mapping_hash)} · policy v${item.policy_version} · ${shortHash(item.policy_hash)}`),
        selectButton,
      );
      importRoot.append(card);
    }
    show($("#load-more-imports"), state.importHistoryItems.length < state.importHistoryTotal);
  }

  async function selectSavedImport(importId) {
    if (!state.tenant || !importId) return;
    const historyButtons = Array.from(
      $("#import-history").querySelectorAll("button[data-import-id]"),
    );
    historyButtons.forEach((button) => { button.disabled = true; });
    try {
      const { data: imported } = await request(
        `${coreBase()}/imports/${encodeURIComponent(importId)}`,
      );
      const { data: policy } = await request(
        `${coreBase()}/policies/${encodeURIComponent(imported.policy_id)}`,
      );
      if (
        policy.id !== imported.policy_id
        || policy.version !== imported.policy_version
        || policy.definition_hash !== imported.policy_hash
      ) {
        throw new Error("The saved import's immutable policy version could not be verified.");
      }

      invalidateReport("Restoring saved normalized rows and their original immutable versions…");
      state.mapping = null;
      state.imported = imported;
      state.controlFile = null;
      $("#source-type").value = imported.source_type;
      $("#source-file-label").textContent = imported.source_type === "bank_activity"
        ? "Choose a bank-activity CSV"
        : "Choose a general-ledger CSV";
      $("#control-file").value = "";
      $("#control-expected").value = "";
      $("#control-source-id").value = "";
      $("#control-locator").value = "";
      $("#control-absolute-tolerance").value = "0";
      $("#control-relative-tolerance").value = "0";
      setStatus("#control-file-state", "No control file selected; reconciliation will be marked not provided.", "muted");
      $("#run-history").value = "";
      $("#load-run-button").disabled = true;
      selectPolicy(policy);
      $("#source-key").value = imported.source_key;
      $("#source-period-start").value = imported.period_start;
      $("#source-period-end").value = imported.period_end;
      $("#source-completeness").value = imported.period_completeness;
      $("#import-button").disabled = true;
      syncRunDates(imported.period_start, imported.period_end);
      setStatus(
        "#file-state",
        state.file
          ? "Saved import selected. The current CSV remains an upload draft; save its mapping before creating a new import."
          : "Saved import recovered from Company history. No source CSV is needed to calculate from its retained normalized rows.",
        "is-success",
      );
      renderImportSummary(imported);
      await loadRecords(true);
      renderImportHistory();
      updateRunButton();
      showMessage(
        `Saved import restored · ${imported.source_type} · mapping v${imported.mapping_version} · policy v${imported.policy_version}. ${imported.quality_status === "passed" ? "" : "This import is incomplete and cannot produce a calculated measure."}`,
        imported.quality_status === "passed" ? "success" : "error",
      );
    } catch (error) {
      if (state.imported?.id === importId) renderImportHistory();
      handleError(error);
    } finally {
      Array.from($("#import-history").querySelectorAll("button[data-import-id]")).forEach((button) => {
        button.disabled = false;
      });
    }
  }

  async function loadHistory() {
    if (!state.tenant || !state.template) return;
    try {
      const [importsResponse, runsResponse] = await Promise.all([
        request(`${coreBase()}/imports?limit=100&offset=0`),
        request(`${coreBase()}/runs?limit=100&offset=0`),
      ]);
      state.importHistoryItems = importsResponse.data?.items || [];
      state.importHistoryTotal = importsResponse.data?.total || 0;
      const runs = runsResponse.data?.items || [];
      renderImportHistory();
      const selector = $("#run-history");
      selector.replaceChildren(new Option("Choose a saved measure run", ""));
      for (const item of runs) {
        const metric = (item.metrics || [])[0];
        if (!metric) continue;
        selector.append(new Option(
          `${measureLabel(metric.metric)} · ${item.period_start} to ${item.period_end} · ${metric.reported_value ?? metric.status} · ${shortHash(item.id)}`,
          item.id,
        ));
      }
      $("#load-run-button").disabled = !selector.value;
      $("#workflow-status").replaceChildren();
    } catch (error) {
      if (error.code === "core_financials_disabled") setFeatureDisabled(error.message);
      else showMessage(error.message || "Company-scoped financial history could not be loaded.");
    }
  }

  async function loadMoreImports() {
    if (!state.tenant || state.importHistoryItems.length >= state.importHistoryTotal) return;
    const button = $("#load-more-imports");
    button.disabled = true;
    try {
      const { data } = await request(
        `${coreBase()}/imports?limit=${PAGE_SIZE}&offset=${state.importHistoryItems.length}`,
      );
      state.importHistoryItems.push(...(data.items || []));
      state.importHistoryTotal = data.total || state.importHistoryTotal;
      renderImportHistory();
    } catch (error) {
      handleError(error);
    } finally {
      button.disabled = false;
    }
  }

  async function inspectSavedRun() {
    const runId = $("#run-history").value;
    if (!runId) return;
    try {
      const { data } = await request(`${coreBase()}/runs/${runId}`);
      state.run = data;
      await renderRun(data);
    } catch (error) {
      handleError(error);
    }
  }

  function handleError(error) {
    if (error?.code === "core_financials_disabled") {
      setFeatureDisabled(error.message);
      return;
    }
    showMessage(error?.message || "The request could not be completed.");
  }

  async function loadCompany() {
    state.policy = null;
    state.mapping = null;
    state.file = null;
    state.headers = [];
    state.imported = null;
    $("#source-type").value = "general_ledger";
    $("#source-file").value = "";
    $("#source-file-label").textContent = "Choose a general-ledger CSV";
    state.importHistoryItems = [];
    state.importHistoryTotal = 0;
    state.run = null;
    state.template = null;
    $("#run-history").replaceChildren(new Option("Choose a saved measure run", ""));
    $("#load-run-button").disabled = true;
    renderImportHistory();
    show($("#disabled-panel"), false);
    show($("#workflow"), false);
    $("#workflow-status").replaceChildren(node("p", "loading", "Loading the authenticated Company workspace…"));
    setTenantCaption();
    try {
      const { data } = await request(`${coreBase()}/template`);
      state.template = data;
      $("#policy-definition").value = JSON.stringify(defaultPolicyDefinition(), null, 2);
      show($("#disabled-panel"), false);
      show($("#workflow"), true);
      $("#create-policy-button").disabled = false;
      await loadPolicies();
      await loadHistory();
      updateRunButton();
    } catch (error) {
      if (error?.code === "core_financials_disabled") {
        setFeatureDisabled(error.message);
      } else {
        $("#workflow-status").replaceChildren(node("p", "cf-status is-error", error?.message || "The Core Financials workspace could not be loaded."));
      }
    }
  }

  async function loadTenants() {
    const { data } = await request("/tenants");
    state.tenants = Array.isArray(data?.items) ? data.items : [];
    if (!state.tenants.length) {
      state.tenant = null;
      fillTenantPicker();
      $("#workflow-status").replaceChildren(node("p", "cf-status", "No active Company workspace is available. Create one from the main workspace, then return here."));
      return;
    }
    const savedId = sessionStorage.getItem(SESSION_KEYS.tenant);
    state.tenant = state.tenants.find((tenant) => tenant.id === savedId) || state.tenants[0];
    sessionStorage.setItem(SESSION_KEYS.tenant, state.tenant.id);
    fillTenantPicker();
    await loadCompany();
  }

  async function loadApp() {
    show($("#auth-required"), false);
    show($("#core-app"), true);
    if (!state.accessToken) {
      showSignedOut();
      return;
    }
    try {
      const { data } = await request("/auth/me");
      state.user = data;
      $("#user-label").textContent = data.display_name || data.email || "Signed in";
      show($("#user-label"), true);
      await loadTenants();
    } catch (error) {
      if (error?.status === 401) clearAuth();
      else $("#workflow-status").replaceChildren(node("p", "cf-status is-error", error.message || "Authentication could not be verified."));
    }
  }

  function bindEvents() {
    $("#company-picker").addEventListener("change", async (event) => {
      const tenant = state.tenants.find((item) => item.id === event.target.value);
      if (!tenant) return;
      state.tenant = tenant;
      sessionStorage.setItem(SESSION_KEYS.tenant, tenant.id);
      invalidateReport("Company changed. The API will load only the selected Company's records.");
      fillTenantPicker();
      await loadCompany();
    });
    $("#create-policy-button").addEventListener("click", createPolicy);
    $("#policy-history").addEventListener("change", () => {
      const policy = state.policies.find((item) => item.id === $("#policy-history").value) || null;
      if (policy?.id !== state.policy?.id) {
        state.mapping = null;
        invalidateReport("Policy version changed. Import rows against the selected immutable policy version.");
      }
      selectPolicy(policy);
    });
    $("#source-file").addEventListener("change", (event) => processLedgerFile(event.target.files?.[0] || null));
    $("#source-type").addEventListener("change", () => {
      state.file = null;
      state.headers = [];
      state.mapping = null;
      $("#source-file").value = "";
      $("#source-file-label").textContent = selectedSourceType() === "bank_activity"
        ? "Choose a bank-activity CSV"
        : "Choose a general-ledger CSV";
      show($("#mapping-editor"), false);
      setStatus("#file-state", "Choose a CSV and save a mapping for the selected source adapter.", "muted");
      invalidateReport("Source adapter changed. Map and import rows from the matching source before calculation.");
    });
    $("#use-ledger-example").addEventListener("click", loadLedgerExample);
    $("#download-ledger-template").addEventListener("click", () => downloadCsv("illustrative_financials_ledger.csv", LEDGER_SAMPLE));
    $("#use-bank-example").addEventListener("click", loadBankExample);
    $("#download-bank-template").addEventListener("click", () => downloadCsv("illustrative_bank_activity.csv", BANK_SAMPLE));
    $("#use-control-example").addEventListener("click", loadControlExample);
    $("#download-control-template").addEventListener("click", () => downloadCsv("illustrative_revenue_control.csv", CONTROL_SAMPLE));
    $("#control-file").addEventListener("change", (event) => {
      state.controlFile = event.target.files?.[0] || null;
      setStatus("#control-file-state", state.controlFile ? `${state.controlFile.name} selected. Its SHA-256 is sent as a caller-provided control reference.` : "No control file selected; reconciliation will be marked not provided.", state.controlFile ? "is-success" : "muted");
    });
    $("#source-key").addEventListener("input", () => {
      state.mapping = null;
      $("#import-button").disabled = true;
      $("#save-mapping-button").disabled = !state.policy || !state.file;
      invalidateReport("Source key changed. Save a versioned mapping for this key before importing.");
    });
    for (const selector of ["#source-period-start", "#source-period-end", "#source-completeness"]) {
      $(selector).addEventListener("change", () => {
        if (state.imported) invalidateReport("Import metadata changed. Create a new immutable import to use these assertions.");
      });
    }
    $("#measure-select").addEventListener("change", () => {
      state.run = null;
      state.evidenceItems = [];
      state.controlFile = null;
      $("#control-file").value = "";
      $("#control-expected").value = "";
      $("#control-source-id").value = "";
      $("#control-locator").value = "";
      $("#control-absolute-tolerance").value = "0";
      $("#control-relative-tolerance").value = "0";
      setStatus("#control-file-state", "Measure changed. Select a separate control for the newly selected measure, or leave reconciliation not provided.", "muted");
      $("#run-result").replaceChildren();
      $("#verification-result").replaceChildren();
      show($("#verify-run-button"), false);
      show($("#load-more-evidence"), false);
      updateRunButton();
    });
    for (const selector of ["#report-period-start", "#report-period-end", "#report-currency"]) {
      $(selector).addEventListener("input", () => {
        state.run = null;
        $("#run-result").replaceChildren();
        $("#verification-result").replaceChildren();
        show($("#verify-run-button"), false);
        updateRunButton();
      });
    }
    $("#save-mapping-button").addEventListener("click", saveMapping);
    $("#import-button").addEventListener("click", importLedger);
    $("#create-run-button").addEventListener("click", createRun);
    $("#verify-run-button").addEventListener("click", verifyRun);
    $("#load-more-records").addEventListener("click", () => loadRecords(false).catch(handleError));
    $("#load-more-evidence").addEventListener("click", () => loadEvidence(false).catch(handleError));
    $("#reload-history-button").addEventListener("click", loadHistory);
    $("#load-more-imports").addEventListener("click", loadMoreImports);
    $("#run-history").addEventListener("change", () => {
      $("#load-run-button").disabled = !$("#run-history").value;
    });
    $("#load-run-button").addEventListener("click", inspectSavedRun);
  }

  bindEvents();
  loadApp();
})();
