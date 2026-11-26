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
  };
  const state = {
    accessToken: sessionStorage.getItem(SESSION_KEYS.access),
    refreshToken: sessionStorage.getItem(SESSION_KEYS.refresh),
    user: null,
    tenants: [],
    tenant: null,
    canWrite: false,
    tenantDataReady: false,
    loadSequence: 0,
    contracts: [],
    coverage: null,
    sources: [],
    sourceTotal: 0,
    imports: [],
    importTotal: 0,
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
  const { clearAuth, request } = api;

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
    const notice = $("#role-notice");
    notice.textContent = !state.tenantDataReady
      ? "Loading Company permissions and evidence. Write actions stay disabled until the selected workspace is loaded."
      : roleCanWrite
        ? "Owner/admin write access is active. The server remains authoritative for source registration, import linking, and all Company permissions."
        : "Read-only Company membership. You can inspect source and quality evidence; source registration and ingestion recording require an authorized owner/admin. The server remains authoritative for every permission check.";
    notice.hidden = state.canWrite;
    for (const selector of ["#source-form", "#ingestion-form"]) {
      for (const field of $$("input, select, button", $(selector))) field.disabled = !state.canWrite;
    }
  }

  function clearTenantWorkspace() {
    state.tenantDataReady = false;
    state.contracts = [];
    state.coverage = null;
    state.sources = [];
    state.sourceTotal = 0;
    state.imports = [];
    state.importTotal = 0;
    $("#coverage-evaluated-at").textContent = "";
    $("#coverage-note").textContent = "";
    $("#coverage-grid").replaceChildren(node("p", "df-empty", "Loading evidence for the selected Company…"));
    $("#source-list").replaceChildren(node("p", "loading", "Loading Company data evidence…"));
    $("#evidence-panel").hidden = true;
    $("#evidence-content").replaceChildren();
    $("#global-message").hidden = true;
    $("#global-message").textContent = "";
    renderDatasetOptions();
    renderSourceOptions();
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
    const versions = state.contracts.filter((contract) => contract.dataset_type === selectedDataset);
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
      if (seen.has(contract.dataset_type)) continue;
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
    const eligible = source ? state.imports.filter((item) =>
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
    const placeholder = node("option", "", state.sources.length ? "Choose a registered source…" : "Register a source first…");
    placeholder.value = "";
    select.append(placeholder);
    for (const source of state.sources) {
      const option = node("option", "", `${source.display_name} · ${DATASET_LABELS[source.dataset_type] || source.dataset_type}`);
      option.value = source.id;
      select.append(option);
    }
    if (previous && state.sources.some((source) => source.id === previous)) select.value = previous;
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
    if (!state.sources.length) {
      target.append(node("p", "df-empty", "No M12 source is registered in this Company yet. This means the source is not tracked in BusinessFlow; it is not evidence that the business has no data."));
      return;
    }
    for (const source of state.sources) {
      const card = node("article", "df-source-card");
      const heading = node("div", "df-source-heading");
      const identity = node("div");
      identity.append(
        node("h3", "", source.display_name),
        node("p", "df-source-key", `${source.source_key} · ${DATASET_LABELS[source.dataset_type] || source.dataset_type} · contract v${source.contract_version}`),
      );
      heading.append(identity);
      const statuses = node("div", "df-status-badges");
      if (!source.latest_ingestion_status) statuses.append(badge("No ingestion"));
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
      card.append(node("p", "df-source-meta", source.latest_ingestion_at
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
      const [contractsResult, coverageResult, sourcesResult, importsResult] = await Promise.all([
        request(`${base}/contracts`),
        request(`${base}/coverage`),
        request(`${base}/sources?limit=${PAGE_SIZE}&offset=0`),
        request(`/tenants/${encodeURIComponent(tenantId)}/imports?limit=${PAGE_SIZE}&offset=0`),
      ]);
      if (sequence !== state.loadSequence || state.tenant?.id !== tenantId) return;
      state.contracts = contractsResult.data?.items || [];
      state.coverage = coverageResult.data;
      state.sources = sourcesResult.data?.items || [];
      state.sourceTotal = sourcesResult.data?.total || 0;
      state.imports = importsResult.data?.items || [];
      state.importTotal = importsResult.data?.total || 0;
      state.tenantDataReady = true;
      renderWorkspace();
      setStatus(`Company data evidence loaded · ${state.tenant.role || "member"} · ${state.sourceTotal} registered sources · ${state.importTotal} M4 imports in history.`, "is-success");
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

  function bindEvents() {
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
    $("#load-more-sources-button").addEventListener("click", loadMoreSources);
    $("#load-more-imports-button").addEventListener("click", loadMoreImports);
    $("#close-evidence-button").addEventListener("click", () => { $("#evidence-panel").hidden = true; });
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
