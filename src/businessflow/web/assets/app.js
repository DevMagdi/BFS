(() => {
  "use strict";

  const API_BASE = "/api/v1";
  const SESSION_KEYS = {
    access: "businessflow.m10.access",
    refresh: "businessflow.m10.refresh",
    tenant: "businessflow.m10.tenant",
    tenantRequest: "businessflow.m10.tenant-request",
  };
  const DATASETS = ["employees", "projects", "weekly_capacity", "resource_assignments"];
  const DATASET_LABELS = {
    employees: "Employees",
    projects: "Projects",
    weekly_capacity: "Weekly capacity",
    resource_assignments: "Resource assignments",
  };
  const state = {
    accessToken: sessionStorage.getItem(SESSION_KEYS.access),
    refreshToken: sessionStorage.getItem(SESSION_KEYS.refresh),
    user: null,
    tenants: [],
    tenant: null,
    demo: null,
    templates: new Map(),
    templateHashes: new Map(),
    imports: new Map(),
    selectedImports: Object.create(null),
    financeImports: [],
    selectedFinanceId: "",
    stagedFinance: null,
    briefs: [],
    review: null,
    m4Tasks: new Map(),
    noticeTimer: null,
  };

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  function node(tag, className = "", text = null) {
    const result = document.createElement(tag);
    if (className) result.className = className;
    if (text !== null && text !== undefined) result.textContent = String(text);
    return result;
  }

  function fieldValue(selector) {
    return $(selector).value;
  }

  function setVisible(element, visible) {
    element.hidden = !visible;
  }

  function showMessage(message, kind = "error") {
    const box = $("#global-message");
    box.textContent = message;
    box.classList.toggle("is-success", kind === "success");
    box.hidden = false;
    window.clearTimeout(state.noticeTimer);
    state.noticeTimer = window.setTimeout(() => {
      box.hidden = true;
    }, 6500);
  }

  function clearMessage() {
    $("#global-message").hidden = true;
  }

  function formatDate(value) {
    if (!value) return "Not available";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return String(value);
    return new Intl.DateTimeFormat(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
      ...(String(value).includes("T") ? { hour: "2-digit", minute: "2-digit" } : {}),
    }).format(parsed);
  }

  function formatWeek(dateString) {
    return dateString ? `Week of ${formatDate(`${dateString}T12:00:00Z`)}` : "Date unavailable";
  }

  function shortId(value, count = 10) {
    if (!value) return "Not available";
    const text = String(value);
    return text.length > count ? `${text.slice(0, count)}…` : text;
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

  function persistTokens(tokens) {
    state.accessToken = tokens.access_token;
    state.refreshToken = tokens.refresh_token;
    sessionStorage.setItem(SESSION_KEYS.access, tokens.access_token);
    sessionStorage.setItem(SESSION_KEYS.refresh, tokens.refresh_token);
  }

  function clearAuth() {
    state.accessToken = null;
    state.refreshToken = null;
    state.user = null;
    state.tenant = null;
    state.demo = null;
    sessionStorage.removeItem(SESSION_KEYS.access);
    sessionStorage.removeItem(SESSION_KEYS.refresh);
    sessionStorage.removeItem(SESSION_KEYS.tenant);
    showAuthenticated(false);
  }

  function randomKey(prefix = "m10") {
    const random = window.crypto && typeof window.crypto.randomUUID === "function"
      ? window.crypto.randomUUID().replaceAll("-", "")
      : `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
    return `${prefix}-${random}`;
  }

  async function refreshAccessToken() {
    if (!state.refreshToken) return false;
    try {
      const response = await fetch(`${API_BASE}/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: state.refreshToken }),
        credentials: "same-origin",
        cache: "no-store",
      });
      const data = response.status === 204 ? null : await response.json().catch(() => null);
      if (!response.ok || !data || !data.access_token) {
        clearAuth();
        return false;
      }
      persistTokens(data);
      return true;
    } catch (_error) {
      return false;
    }
  }

  async function request(path, options = {}, retry = true) {
    const headers = new Headers(options.headers || {});
    const requestOptions = { ...options, headers, credentials: "same-origin", cache: "no-store" };
    const hasBody = Object.prototype.hasOwnProperty.call(options, "body");
    if (hasBody && !(options.body instanceof FormData) && typeof options.body !== "string") {
      headers.set("Content-Type", "application/json");
      requestOptions.body = JSON.stringify(options.body);
    }
    const pathOnly = path.split("?", 1)[0];
    const publicAuthPath = ["/auth/login", "/auth/register", "/auth/refresh"].includes(pathOnly);
    if (state.accessToken && !publicAuthPath) {
      headers.set("Authorization", `Bearer ${state.accessToken}`);
    }

    let response;
    try {
      response = await fetch(`${API_BASE}${path}`, requestOptions);
    } catch (_error) {
      throw new ApiFailure("The local server could not be reached. Check that the API is running.", 0, "network_error");
    }
    if (response.status === 401 && retry && !publicAuthPath && pathOnly !== "/auth/logout" && await refreshAccessToken()) {
      return request(path, options, false);
    }
    const data = response.status === 204 ? null : await response.json().catch(() => null);
    if (!response.ok) {
      const error = data && data.error ? data.error : null;
      throw new ApiFailure(apiErrorMessage(data, response.status), response.status, error?.code);
    }
    return { data, response };
  }

  async function requestBlob(path) {
    const headers = new Headers();
    if (state.accessToken) headers.set("Authorization", `Bearer ${state.accessToken}`);
    let response;
    try {
      response = await fetch(`${API_BASE}${path}`, {
        headers,
        credentials: "same-origin",
        cache: "no-store",
      });
    } catch (_error) {
      throw new ApiFailure("The local server could not be reached. Check that the API is running.", 0, "network_error");
    }
    if (response.status === 401 && await refreshAccessToken()) return requestBlob(path);
    if (!response.ok) {
      const data = await response.json().catch(() => null);
      throw new ApiFailure(apiErrorMessage(data, response.status), response.status, data?.error?.code);
    }
    return response.blob();
  }

  async function sha256(blobOrBuffer) {
    if (!window.crypto || !window.crypto.subtle) {
      throw new ApiFailure("This browser cannot verify the built-in synthetic file. Use a current browser on localhost or HTTPS.", 0, "crypto_unavailable");
    }
    const buffer = blobOrBuffer instanceof Blob ? await blobOrBuffer.arrayBuffer() : blobOrBuffer;
    const digest = await window.crypto.subtle.digest("SHA-256", buffer);
    return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
  }

  function bytesEqual(left, right) {
    if (left.byteLength !== right.byteLength) return false;
    const a = new Uint8Array(left);
    const b = new Uint8Array(right);
    return a.every((value, index) => value === b[index]);
  }

  async function getTemplate(dataset) {
    if (!state.templates.has(dataset)) {
      const blob = await requestBlob(`/tenants/${state.tenant.id}/position-reviews/demo/templates/${encodeURIComponent(dataset)}`);
      state.templates.set(dataset, blob);
      state.templateHashes.set(dataset, await sha256(blob));
    }
    return state.templates.get(dataset);
  }

  async function verifyBuiltInFile(file, dataset) {
    const template = await getTemplate(dataset);
    const same = bytesEqual(await file.arrayBuffer(), await template.arrayBuffer());
    if (!same) {
      throw new ApiFailure(
        `For this internal synthetic-only slice, ${DATASET_LABELS[dataset] || "this dataset"} must be the exact built-in demo template. No customer or confidential data is accepted.`,
        422,
        "synthetic_template_mismatch",
      );
    }
  }

  function showAuthenticated(authenticated) {
    setVisible($("#auth-panel"), !authenticated);
    setVisible($("#tenant-panel"), authenticated && state.tenants.length === 0);
    setVisible($("#workspace"), authenticated && state.tenants.length > 0 && Boolean(state.tenant));
    setVisible($("#user-label"), authenticated);
    setVisible($("#logout-button"), authenticated);
    $("#user-label").textContent = state.user ? state.user.display_name : "";
  }

  function toggleRegisterFields() {
    const registering = fieldValue("#auth-mode") === "register";
    setVisible($("#display-name-field"), registering);
    $("#display-name").required = registering;
    $("#password").autocomplete = registering ? "new-password" : "current-password";
    $("#password-help").textContent = registering
      ? "Use at least 12 characters for this local account."
      : "Use your existing BusinessFlow local account.";
  }

  async function submitAuth(event) {
    event.preventDefault();
    clearMessage();
    const registering = fieldValue("#auth-mode") === "register";
    const body = {
      email: fieldValue("#email").trim(),
      password: fieldValue("#password"),
    };
    const endpoint = registering ? "/auth/register" : "/auth/login";
    if (registering) body.display_name = fieldValue("#display-name").trim();
    const button = $("#auth-form button[type='submit']");
    button.disabled = true;
    try {
      const { data } = await request(endpoint, { method: "POST", body });
      persistTokens(data);
      const me = await request("/auth/me");
      state.user = me.data;
      await loadTenants();
      showMessage(registering ? "Local account created. Continue in the synthetic workspace." : "Signed in.", "success");
      $("#password").value = "";
    } catch (error) {
      showMessage(error.message || "Sign-in failed.");
    } finally {
      button.disabled = false;
    }
  }

  function tenantRequestKey() {
    let value = sessionStorage.getItem(SESSION_KEYS.tenantRequest);
    if (!value) {
      value = randomKey("m10-tenant");
      sessionStorage.setItem(SESSION_KEYS.tenantRequest, value);
    }
    return value;
  }

  async function createTenant(body) {
    const button = $("#tenant-form button[type='submit']");
    if (button) button.disabled = true;
    try {
      const { data: created } = await request("/tenants", {
        method: "POST",
        headers: { "Idempotency-Key": tenantRequestKey() },
        body,
      });
      sessionStorage.removeItem(SESSION_KEYS.tenantRequest);
      await loadTenants(true, created?.id);
      showMessage("Synthetic demo workspace created. Your existing workspaces were not changed.", "success");
    } catch (error) {
      showMessage(error.message || "The workspace could not be created.");
    } finally {
      if (button) button.disabled = false;
    }
  }

  function tenantCreateBody() {
    return {
      name: fieldValue("#tenant-name").trim(),
      company: {
        legal_name: fieldValue("#company-name").trim(),
        base_currency: fieldValue("#company-currency").trim().toUpperCase(),
        timezone: fieldValue("#company-timezone").trim(),
        locale: "en",
      },
    };
  }

  async function loadTenants(preferLatest = false, preferredTenantId = null) {
    const { data } = await request("/tenants");
    state.tenants = Array.isArray(data?.items) ? data.items : [];
    if (!state.tenants.length) {
      state.tenant = null;
      showAuthenticated(true);
      return;
    }
    const saved = sessionStorage.getItem(SESSION_KEYS.tenant);
    const selected = preferredTenantId
      ? state.tenants.find((tenant) => tenant.id === preferredTenantId)
      : (!preferLatest && saved ? state.tenants.find((tenant) => tenant.id === saved) : null);
    state.tenant = selected || state.tenants[0];
    sessionStorage.setItem(SESSION_KEYS.tenant, state.tenant.id);
    showAuthenticated(true);
    renderTenantPicker();
    await loadWorkspace();
  }

  function renderTenantPicker() {
    const wrapper = $("#company-picker-wrap");
    const picker = $("#company-picker");
    picker.replaceChildren();
    for (const tenant of state.tenants) {
      const option = node("option", "", `${tenant.name} · ${tenant.company?.legal_name || "Company"}`);
      option.value = tenant.id;
      option.selected = tenant.id === state.tenant?.id;
      picker.append(option);
    }
    setVisible(wrapper, state.tenants.length > 1);
    const label = state.tenant
      ? `${state.tenant.name} · ${state.tenant.company?.legal_name || "Company"} · ${state.tenant.company?.base_currency || ""}`
      : "";
    $("#company-caption").textContent = label;
  }

  function isLabeledDemo() {
    const tenantName = state.tenant?.name || "";
    const companyName = state.tenant?.company?.legal_name || "";
    return tenantName.toLowerCase().includes("demo") && companyName.toLowerCase().includes("synthetic");
  }

  async function loadWorkspace() {
    if (!state.tenant) return;
    clearMessage();
    state.demo = null;
    state.templates.clear();
    state.templateHashes.clear();
    state.selectedImports = Object.create(null);
    state.selectedFinanceId = "";
    state.stagedFinance = null;
    state.review = null;
    state.imports.clear();
    state.financeImports = [];
    state.briefs = [];
    state.m4Tasks.clear();
    renderTenantPicker();
    resetBriefUi();
    resetRecordUi();

    const status = $("#workspace-status");
    status.replaceChildren(node("p", "loading", "Loading the company-scoped synthetic demo state…"));
    try {
      const { data } = await request(`/tenants/${state.tenant.id}/position-reviews/demo`);
      state.demo = data;
      status.replaceChildren();
      if (!data.demo_seed) {
        if (isLabeledDemo()) {
          setVisible($("#seed-card"), true);
          setVisible($("#pack-card"), false);
          const note = node("p", "muted", "This labeled Company has no M10 baseline yet. Seeding is an explicit, idempotent action and is refused if existing Company operational data is present.");
          status.append(note);
        } else {
          setVisible($("#seed-card"), false);
          setVisible($("#pack-card"), false);
          status.append(node("p", "muted", "M10 seeding is locked to a fresh Company labeled “Synthetic” in a tenant labeled “Demo”. Create a separate workspace; this Company will not be modified."));
          const button = node("button", "button button-secondary", "Create a separate synthetic demo workspace");
          button.type = "button";
          button.addEventListener("click", () => {
            const body = {
              name: "M10 Synthetic Demo",
              company: {
                legal_name: "Synthetic Demo Company",
                base_currency: "USD",
                timezone: "Africa/Cairo",
                locale: "en",
              },
            };
            createTenant(body);
          });
          status.append(button);
        }
        resetPackLabels();
        return;
      }

      setVisible($("#seed-card"), false);
      setVisible($("#pack-card"), true);
      const seed = data.demo_seed;
      $("#baseline-title").textContent = `${formatDate(seed.baseline_start_week_date)} – ${formatDate(seed.baseline_end_week_date)}`;
      $("#baseline-meta").textContent = `M5 baseline · 4 Monday–Sunday weeks · ${seed.baseline_metric_catalog_version} · ${seed.baseline_calculation_version}`;
      const currentEnd = addDays(seed.current_start_week_date, 27);
      $("#current-title").textContent = `${formatDate(seed.current_start_week_date)} – ${formatDate(currentEnd)}`;
      $("#current-meta").textContent = "Four-week planned period · use only the exact built-in synthetic CSV templates";
      const seedCaption = node("p", "muted", `${seed.synthetic_label} · seed ${seed.seed_version} · manifest ${shortId(seed.manifest_hash, 16)}`);
      status.append(seedCaption);

      await Promise.all(DATASETS.map((dataset) => getTemplate(dataset)));
      await loadOperationalImportHistory();
      await loadFinancialImportHistory();
      await loadBriefHistory();
      populateFinanceSelect();
      populateBriefSelect();
      renderFinanceSelection();
      renderBriefSelection();
    } catch (error) {
      status.replaceChildren(node("p", "preview-summary is-error", error.message || "The M10 workspace could not be loaded."));
    }
  }

  function addDays(value, days) {
    const parsed = new Date(`${value}T00:00:00Z`);
    parsed.setUTCDate(parsed.getUTCDate() + days);
    return parsed.toISOString().slice(0, 10);
  }

  function resetPackLabels() {
    $("#baseline-title").textContent = "Prior four-week plan";
    $("#baseline-meta").textContent = "";
    $("#current-title").textContent = "Current recorded plan";
    $("#current-meta").textContent = "";
  }

  function ensureImportSelector(card) {
    let wrapper = $(".import-select-wrap", card);
    if (wrapper) return wrapper;
    wrapper = node("label", "import-select-wrap field");
    wrapper.append(node("span", "", "Applied synthetic template to include"));
    const select = node("select", "import-select");
    select.append(new Option("Choose an applied import", ""));
    select.addEventListener("change", () => {
      const dataset = card.dataset.dataset;
      const selectedId = select.value;
      state.selectedImports[dataset] = selectedId;
      const selected = state.imports.get(selectedId);
      const stateLabel = $(".import-state", card);
      stateLabel.classList.remove("is-error");
      stateLabel.classList.toggle("is-success", Boolean(selected));
      stateLabel.textContent = selected
        ? `Selected ${selected.file_name} · ${selected.status} · exact synthetic template`
        : "No current import selected.";
      updateCreateButtonState();
    });
    wrapper.append(select);
    const importState = $(".import-state", card);
    importState.before(wrapper);
    return wrapper;
  }

  async function loadOperationalImportHistory() {
    const { data } = await request(`/tenants/${state.tenant.id}/imports?limit=100&offset=0`);
    state.imports.clear();
    const candidates = Array.isArray(data?.items) ? data.items : [];
    for (const item of candidates) state.imports.set(item.id, item);

    for (const dataset of DATASETS) {
      const card = $(`.import-card[data-dataset='${dataset}']`);
      const selector = ensureImportSelector(card).querySelector("select");
      const previousSelection = state.selectedImports[dataset] || "";
      const expectedHash = state.templateHashes.get(dataset);
      const imports = candidates
        .filter((item) => item.dataset_type === dataset)
        .filter((item) => ["applied", "applied_with_warnings"].includes(item.status))
        .filter((item) => item.file_hash === expectedHash)
        .filter((item) => !state.demo?.demo_seed?.created_at || new Date(item.created_at) > new Date(state.demo.demo_seed.created_at))
        .filter((item) => (item.counts?.invalid_rows || 0) === 0)
        .filter((item) => !["weekly_capacity", "resource_assignments"].includes(dataset)
          || ((item.counts?.warning_rows || 0) === 0 && (item.counts?.duplicate_rows || 0) === 0))
        .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));

      selector.replaceChildren(new Option("Choose an applied import", ""));
      for (const item of imports) {
        const option = new Option(
          `${item.file_name} · ${formatDate(item.created_at)} · ${item.status}`,
          item.id,
        );
        selector.append(option);
      }
      const preferred = imports.some((item) => item.id === previousSelection) ? previousSelection : (imports[0]?.id || "");
      selector.value = preferred;
      state.selectedImports[dataset] = preferred;
      const stateLabel = $(".import-state", card);
      stateLabel.classList.toggle("is-success", Boolean(preferred));
      stateLabel.textContent = preferred
        ? `Selected ${state.imports.get(preferred)?.file_name} · exact built-in synthetic template`
        : "No applied synthetic import selected. Upload, validate, confirm, and apply the built-in template.";
    }
    updateCreateButtonState();
  }

  async function inspectOperationalFile(card) {
    clearMessage();
    const dataset = card.dataset.dataset;
    const input = $(".file-input", card);
    const file = input.files && input.files[0];
    if (!file) {
      setImportCardError(card, "Choose the built-in synthetic CSV template first.");
      return;
    }
    const button = $(".inspect-button", card);
    button.disabled = true;
    try {
      await verifyBuiltInFile(file, dataset);
      const form = new FormData();
      form.append("file", file, file.name);
      const fileHash = await sha256(file);
      const key = `m10-${fileHash}-${dataset}`;
      const { data } = await request(`/tenants/${state.tenant.id}/imports`, {
        method: "POST",
        headers: { "Idempotency-Key": key },
        body: form,
      });
      const importRecord = data?.import;
      if (!importRecord?.id) throw new ApiFailure("The M4 inspection response was incomplete.", 500);
      state.m4Tasks.set(dataset, {
        importId: importRecord.id,
        fileName: importRecord.file_name,
        fileHash: importRecord.file_hash,
        worksheetName: null,
      });
      renderWorksheetOptions(card, data.inspection?.worksheets || []);
      setVisible($(".import-wizard", card), true);
      setVisible($(".validate-button", card), false);
      $(".mapping-editor", card).replaceChildren();
      $(".preview-area", card).replaceChildren();
      $(".confirm-area", card).hidden = true;
      setImportCardSuccess(card, `Synthetic template inspected · ${importRecord.file_name} · ${importRecord.counts?.total_rows ?? "?"} rows. Select fields, then validate.`);
    } catch (error) {
      setImportCardError(card, error.message || "The file could not be inspected.");
    } finally {
      button.disabled = false;
    }
  }

  function renderWorksheetOptions(card, worksheets) {
    const select = $(".worksheet-select", card);
    select.replaceChildren();
    for (const worksheet of worksheets) {
      const option = new Option(`${worksheet.name} · ${worksheet.row_count} rows`, worksheet.name);
      select.append(option);
    }
    const dataset = card.dataset.dataset;
    const task = state.m4Tasks.get(dataset);
    const syncWorksheet = () => {
      const current = state.m4Tasks.get(dataset);
      if (!current) return;
      current.worksheetName = select.value || "CSV";
      const worksheet = worksheets.find((item) => item.name === select.value);
      current.headers = Array.isArray(worksheet?.headers) ? worksheet.headers : [];
    };
    syncWorksheet();
    select.onchange = syncWorksheet;
  }

  async function loadImportMapping(card) {
    clearMessage();
    const dataset = card.dataset.dataset;
    const task = state.m4Tasks.get(dataset);
    if (!task) {
      setImportCardError(card, "Inspect a built-in synthetic file first.");
      return;
    }
    const worksheet = $(".worksheet-select", card).value;
    const query = new URLSearchParams({ dataset_type: dataset, worksheet_name: worksheet });
    const button = $(".load-mapping-button", card);
    button.disabled = true;
    try {
      const { data } = await request(`/tenants/${state.tenant.id}/imports/${task.importId}/mapping?${query}`);
      renderMappingEditor(card, data);
      setVisible($(".validate-button", card), true);
      setImportCardSuccess(card, `Review the suggested field mapping for ${task.fileName}. All source columns must be mapped or explicitly ignored.`);
    } catch (error) {
      setImportCardError(card, error.message || "The field mapping could not be loaded.");
    } finally {
      button.disabled = false;
    }
  }

  function renderMappingEditor(card, mappingResponse) {
    const editor = $(".mapping-editor", card);
    editor.replaceChildren();
    const definitions = Array.isArray(mappingResponse.field_definitions) ? mappingResponse.field_definitions : [];
    const options = [new Option("Choose a destination", "")];
    for (const definition of definitions) {
      const required = definition.required ? " · required" : "";
      options.push(new Option(`${definition.name}${required}`, definition.name));
    }
    options.push(new Option("Explicitly ignore this source column", "__ignore__"));
    const suggested = mappingResponse.suggested_mapping || {};
    const ignored = new Set(mappingResponse.ignored_columns || []);
    const headers = Object.keys(suggested).length
      ? Object.keys(suggested)
      : Array.from(new Set([...(mappingResponse.unmapped_columns || []), ...ignored]));
    // For the supported synthetic CSVs, the staged inspection is the source of truth for all headers.
    const task = state.m4Tasks.get(card.dataset.dataset);
    const fileHeaders = task?.headers;
    const headerList = fileHeaders || headers;
    for (const header of headerList) {
      const row = node("div", "mapping-row");
      const label = node("label", "", header);
      const select = node("select", "mapping-choice");
      select.dataset.header = header;
      for (const option of options) select.append(option.cloneNode(true));
      const exactField = definitions.find((definition) => definition.name === header)?.name;
      select.value = suggested[header] || exactField || (ignored.has(header) ? "__ignore__" : "");
      row.append(label, select);
      editor.append(row);
    }
  }

  function collectMapping(card) {
    const fieldMapping = Object.create(null);
    const ignoredColumns = [];
    const missing = [];
    for (const select of $$(".mapping-choice", card)) {
      const header = select.dataset.header;
      if (!select.value) {
        missing.push(header);
      } else if (select.value === "__ignore__") {
        ignoredColumns.push(header);
      } else {
        fieldMapping[header] = select.value;
      }
    }
    if (missing.length) throw new ApiFailure(`Map or explicitly ignore every source column: ${missing.join(", ")}.`, 422, "mapping_incomplete");
    if (Object.keys(fieldMapping).length === 0) throw new ApiFailure("At least one source column must be mapped.", 422, "mapping_empty");
    return { field_mapping: fieldMapping, ignored_columns: ignoredColumns };
  }

  async function validateOperationalImport(card) {
    clearMessage();
    const dataset = card.dataset.dataset;
    const task = state.m4Tasks.get(dataset);
    if (!task) {
      setImportCardError(card, "Inspect a built-in synthetic file first.");
      return;
    }
    const button = $(".validate-button", card);
    button.disabled = true;
    try {
      const mapping = collectMapping(card);
      await request(`/tenants/${state.tenant.id}/imports/${task.importId}/mapping`, {
        method: "PUT",
        body: { dataset_type: dataset, worksheet_name: $(".worksheet-select", card).value, ...mapping },
      });
      const { data: report } = await request(`/tenants/${state.tenant.id}/imports/${task.importId}/validate`, { method: "POST" });
      const summary = report?.import;
      if (!summary) throw new ApiFailure("The M4 validation response was incomplete.", 500);
      const { data: preview } = await request(`/tenants/${state.tenant.id}/imports/${task.importId}/preview?limit=25&offset=0`);
      renderImportPreview(card, report, preview);
      const invalid = summary.counts?.invalid_rows || 0;
      const warnings = summary.counts?.warning_rows || 0;
      const duplicates = summary.counts?.duplicate_rows || 0;
      const confirmArea = $(".confirm-area", card);
      const acknowledgment = $(".acknowledge-warnings", card);
      const safeRosterWarnings = ["employees", "projects"].includes(dataset) && warnings > 0;
      if (acknowledgment) acknowledgment.checked = false;
      if (acknowledgment) acknowledgment.parentElement.hidden = !safeRosterWarnings;
      if (warnings > 0 && !safeRosterWarnings) {
        confirmArea.hidden = true;
        setImportCardError(card, `${invalid} invalid row(s), ${warnings} warning(s), ${duplicates} duplicate(s). M10 requires zero invalid or warned capacity/assignment rows; fix or use the exact synthetic template.`);
      } else if (invalid > 0) {
        confirmArea.hidden = true;
        setImportCardError(card, `${invalid} invalid row(s). Correct the source before confirming; M10 will not use partial imports.`);
      } else {
        confirmArea.hidden = false;
        const countLine = `${summary.counts?.total_rows ?? 0} rows · ${duplicates} duplicate(s)${warnings ? ` · ${warnings} warning(s) to acknowledge` : " · no warnings"}`;
        setImportCardSuccess(card, `Validation complete · ${countLine}. Confirm and apply; M4 does not overwrite existing facts.`);
      }
      task.validationHash = summary.validation_hash;
      task.counts = summary.counts;
      task.status = summary.status;
      task.validationReport = report.validation_report;
    } catch (error) {
      setImportCardError(card, error.message || "Validation failed.");
    } finally {
      button.disabled = false;
    }
  }

  function renderImportPreview(card, report, preview) {
    const area = $(".preview-area", card);
    area.replaceChildren();
    const summary = report.import;
    const counts = summary?.counts || {};
    const summaryBox = node("div", "preview-summary", `${counts.total_rows ?? 0} rows · ${counts.valid_rows ?? 0} valid · ${counts.invalid_rows ?? 0} invalid · ${counts.warning_rows ?? 0} warnings · ${counts.duplicate_rows ?? 0} duplicates. Validation ${shortId(summary?.validation_hash, 16)}.`);
    if ((counts.invalid_rows || 0) > 0 || (counts.warning_rows || 0) > 0) summaryBox.classList.add("is-warning");
    area.append(summaryBox);
    for (const row of (preview?.items || [])) {
      const cardRow = node("div", "preview-row");
      cardRow.append(node("strong", "", `Row ${row.row_number} · ${row.row_status}`));
      const values = node("pre");
      values.textContent = JSON.stringify({ source: row.source_values, mapped: row.mapped_values }, null, 2);
      cardRow.append(values);
      if (Array.isArray(row.issues) && row.issues.length) {
        const issueList = node("ul", "row-issues");
        for (const issue of row.issues) issueList.append(node("li", "", `${issue.code}: ${issue.message}`));
        cardRow.append(issueList);
      }
      area.append(cardRow);
    }
    const importId = summary?.id;
    const task = state.m4Tasks.get(card.dataset.dataset);
    if (task && importId) task.importId = importId;
  }

  async function confirmAndApplyImport(card) {
    clearMessage();
    const dataset = card.dataset.dataset;
    const task = state.m4Tasks.get(dataset);
    if (!task || !task.validationHash) {
      setImportCardError(card, "Validate the current mapping before confirming.");
      return;
    }
    const warningCount = task.counts?.warning_rows || 0;
    const acknowledgeWarnings = warningCount > 0 && Boolean($(".acknowledge-warnings", card)?.checked);
    if (warningCount > 0 && ["employees", "projects"].includes(dataset) && !acknowledgeWarnings) {
      setImportCardError(card, "Review and acknowledge the duplicate warnings before confirming.");
      return;
    }
    const button = $(".apply-button", card);
    button.disabled = true;
    try {
      await request(`/tenants/${state.tenant.id}/imports/${task.importId}/confirm`, {
        method: "POST",
        body: {
          validation_hash: task.validationHash,
          allow_partial: false,
          acknowledge_warnings: acknowledgeWarnings,
        },
      });
      const { data } = await request(`/tenants/${state.tenant.id}/imports/${task.importId}/apply`, { method: "POST" });
      const summary = data?.import;
      setImportCardSuccess(card, `Applied ${summary?.created_rows ?? 0} new row(s); ${summary?.skipped_rows ?? 0} duplicate row(s) were skipped. Source links are retained.`);
      setVisible($(".import-wizard", card), false);
      $(".file-input", card).value = "";
      state.m4Tasks.delete(dataset);
      await loadOperationalImportHistory();
    } catch (error) {
      setImportCardError(card, error.message || "The import could not be applied.");
    } finally {
      button.disabled = false;
    }
  }

  function setImportCardSuccess(card, message) {
    const label = $(".import-state", card);
    label.textContent = message;
    label.classList.add("is-success");
    label.classList.remove("is-error");
  }

  function setImportCardError(card, message) {
    const label = $(".import-state", card);
    label.textContent = message;
    label.classList.add("is-error");
    label.classList.remove("is-success");
  }

  function updateCreateButtonState() {
    const ready = DATASETS.every((dataset) => Boolean(state.selectedImports[dataset]))
      && Boolean($("#synthetic-confirm")?.checked);
    $("#create-review-button").disabled = !ready;
  }

  async function uploadFinanceFile() {
    clearMessage();
    const file = $("#finance-file").files?.[0];
    if (!file) {
      renderFinanceError("Choose the exact built-in synthetic finance CSV template first.");
      return;
    }
    const button = $("#finance-upload-button");
    button.disabled = true;
    try {
      await verifyBuiltInFile(file, "financial_export");
      const fileHash = await sha256(file);
      const form = new FormData();
      form.append("file", file, file.name);
      const { data } = await request(`/tenants/${state.tenant.id}/position-reviews/financial-imports`, {
        method: "POST",
        headers: { "Idempotency-Key": `m10-finance-${fileHash}` },
        body: form,
      });
      state.stagedFinance = data;
      renderFinancePreview(data);
    } catch (error) {
      renderFinanceError(error.message || "The synthetic finance rows could not be validated.");
    } finally {
      button.disabled = false;
    }
  }

  function renderFinanceError(message) {
    const area = $("#finance-preview");
    area.replaceChildren(node("div", "preview-summary is-error", message));
  }

  function renderFinancePreview(data) {
    const area = $("#finance-preview");
    area.replaceChildren();
    const summary = data?.financial_import;
    if (!summary) {
      renderFinanceError("The M10 finance validation response was incomplete.");
      return;
    }
    const report = data.validation_report || {};
    const issueCount = summary.invalid_row_count || 0;
    const banner = node("div", `preview-summary${issueCount ? " is-error" : ""}`, `${summary.file_name} · ${summary.row_count} row(s) · ${issueCount} invalid · ${summary.status} · file ${shortId(summary.file_hash, 16)}`);
    area.append(banner);
    const note = node("p", "fine-print", data.retention_note || "Original file bytes are not retained.");
    area.append(note);
    if (Array.isArray(data.observations)) {
      for (const observation of data.observations) {
        const row = node("div", "preview-row");
        row.append(node("strong", "", `${observation.source_label || "Unlabeled source"} · ${observation.currency_code || "currency unavailable"} · ${observation.period_start || "?"} to ${observation.period_end || "?"}`));
        const text = node("pre");
        text.textContent = JSON.stringify({
          source_report: observation.source_report,
          source_value_text: observation.source_value_text,
          exported_at: observation.exported_at,
          source_record_id: observation.source_record_id,
          worksheet_name: observation.worksheet_name,
          row_number: observation.row_number,
          row_hash: observation.row_hash,
          issues: observation.issues,
        }, null, 2);
        row.append(text);
        area.append(row);
      }
    }
    const confirmationText = node("p", "fine-print", "Confirming this source preserves the CSV values, currency codes, labels, periods, and row references. No finance formula, currency conversion, side-by-side comparison, or conclusion is applied.");
    area.append(confirmationText);
    if (summary.status === "validated" && issueCount === 0) {
      const button = node("button", "button button-primary", "Confirm synthetic source rows");
      button.type = "button";
      button.addEventListener("click", confirmFinanceImport);
      area.append(button);
    } else if (summary.status === "confirmed") {
      const button = node("button", "button button-secondary", "Use this confirmed source in the next brief");
      button.type = "button";
      button.addEventListener("click", () => {
        state.selectedFinanceId = summary.id;
        populateFinanceSelect();
        renderFinanceSelection();
        showMessage("Confirmed synthetic finance source selected for the next brief.", "success");
      });
      area.append(button);
    } else {
      area.append(node("div", "preview-summary is-error", `This source is not confirmable. Validation reported ${report.invalid_rows ?? issueCount} invalid row(s).`));
    }
  }

  async function confirmFinanceImport() {
    const staged = state.stagedFinance;
    const summary = staged?.financial_import;
    if (!summary || summary.status !== "validated") return;
    const button = $("#finance-preview button");
    if (button) button.disabled = true;
    try {
      const { data } = await request(`/tenants/${state.tenant.id}/position-reviews/financial-imports/${summary.id}/confirm`, {
        method: "POST",
        body: { validation_hash: summary.validation_hash },
      });
      state.stagedFinance = data;
      await loadFinancialImportHistory();
      state.selectedFinanceId = data.financial_import.id;
      populateFinanceSelect();
      renderFinancePreview(data);
      renderFinanceSelection();
      showMessage("Synthetic finance source confirmed. Its values remain source-native.", "success");
    } catch (error) {
      renderFinanceError(error.message || "The finance import could not be confirmed.");
    } finally {
      if (button) button.disabled = false;
    }
  }

  async function loadFinancialImportHistory() {
    const { data } = await request(`/tenants/${state.tenant.id}/position-reviews/financial-imports?limit=100&offset=0`);
    state.financeImports = Array.isArray(data?.items) ? data.items : [];
  }

  function populateFinanceSelect() {
    const select = $("#finance-history");
    const previous = state.selectedFinanceId || select.value;
    select.replaceChildren(new Option("No finance file — create an explicit abstention", ""));
    const confirmed = state.financeImports.filter((item) => item.status === "confirmed");
    for (const item of confirmed) {
      const option = new Option(
        `${item.file_name} · ${item.row_count} source rows · ${formatDate(item.created_at)}`,
        item.id,
      );
      select.append(option);
    }
    const hasPrevious = confirmed.some((item) => item.id === previous);
    select.value = hasPrevious ? previous : "";
    state.selectedFinanceId = select.value;
    select.onchange = () => {
      state.selectedFinanceId = select.value;
      renderFinanceSelection();
    };
  }

  function renderFinanceSelection() {
    const existing = $("#finance-selection-note");
    if (existing) existing.remove();
    const selected = state.financeImports.find((item) => item.id === state.selectedFinanceId);
    if (!selected) return;
    const message = node("div", "preview-summary", `Selected confirmed source ${selected.file_name} · ${selected.row_count} source-native rows. No calculation or period comparison will be performed.`);
    message.id = "finance-selection-note";
    $("#finance-history").after(message);
  }

  async function loadBriefHistory() {
    const { data } = await request(`/tenants/${state.tenant.id}/position-reviews/briefs?limit=100&offset=0`);
    state.briefs = Array.isArray(data?.items) ? data.items : [];
  }

  function populateBriefSelect() {
    const select = $("#brief-history");
    const selectedId = state.review?.id || select.value;
    select.replaceChildren(new Option(state.briefs.length ? "Choose a saved brief" : "No saved brief yet", ""));
    for (const item of state.briefs) {
      const label = `${formatDate(item.created_at)} · ${item.result_state} · ${shortId(item.result_hash, 12)}`;
      select.append(new Option(label, item.id));
    }
    select.value = state.briefs.some((item) => item.id === selectedId) ? selectedId : "";
    $("#reload-brief-button").disabled = !select.value;
    select.onchange = async () => {
      $("#reload-brief-button").disabled = !select.value;
      if (select.value) await loadPositionBrief(select.value);
      else {
        state.review = null;
        resetBriefUi();
        resetRecordUi();
      }
    };
  }

  function resetBriefUi() {
    const content = $("#brief-content");
    content.replaceChildren();
    const empty = node("div", "empty-state");
    empty.append(node("span", "empty-mark", "↗"), node("h3", "", "No brief selected"), node("p", "", "Complete the intake in Create Review, then generate a versioned evidence brief."));
    content.append(empty);
    $("#brief-history").replaceChildren(new Option("No saved brief yet", ""));
    $("#reload-brief-button").disabled = true;
  }

  async function createPositionBrief() {
    clearMessage();
    const operationalImports = Object.fromEntries(DATASETS.map((dataset) => [dataset, state.selectedImports[dataset]]));
    if (DATASETS.some((dataset) => !operationalImports[dataset])) {
      showMessage("Select an applied synthetic import for each of the four operational datasets.");
      return;
    }
    if (!$("#synthetic-confirm").checked) {
      showMessage("Confirm that every source selected for the brief is synthetic/demo data only.");
      return;
    }
    const body = {
      start_week_date: state.demo.demo_seed.current_start_week_date,
      baseline_metric_run_id: state.demo.demo_seed.baseline_metric_run_id,
      operational_imports: operationalImports,
      financial_import_id: state.selectedFinanceId || null,
      synthetic_data_acknowledged: true,
      attestations: {
        m5_definition_acknowledged: $("#attest-m5").checked,
        active_cohort_comparability_attested: $("#attest-cohort").checked,
        baseline_capacity_currentness_attested: $("#attest-baseline-capacity").checked,
        current_capacity_currentness_attested: $("#attest-current-capacity").checked,
        committed_assignment_completeness_attested: $("#attest-assignments").checked,
      },
    };
    const bodyText = JSON.stringify(body);
    const keyName = `businessflow.m10.review-key.${state.tenant.id}`;
    let keyRecord;
    try { keyRecord = JSON.parse(sessionStorage.getItem(keyName) || "null"); } catch (_error) { keyRecord = null; }
    if (!keyRecord || keyRecord.body !== bodyText || typeof keyRecord.key !== "string") {
      keyRecord = { body: bodyText, key: randomKey("m10-review") };
      sessionStorage.setItem(keyName, JSON.stringify(keyRecord));
    }
    const button = $("#create-review-button");
    button.disabled = true;
    try {
      const { data } = await request(`/tenants/${state.tenant.id}/position-reviews/briefs`, {
        method: "POST",
        headers: { "Idempotency-Key": keyRecord.key },
        body,
      });
      sessionStorage.removeItem(keyName);
      state.review = data;
      if (!state.briefs.some((item) => item.id === data.id)) state.briefs.unshift(data);
      populateBriefSelect();
      $("#brief-history").value = data.id;
      renderBriefSelection();
      renderPositionBrief(data);
      showScreen("brief");
      loadBriefHistory().then(() => {
        populateBriefSelect();
        $("#brief-history").value = data.id;
      }).catch(() => {});
      showMessage("Position Decision Brief created. Inspect its evidence and abstentions before recording a human review.", "success");
    } catch (error) {
      showMessage(error.message || "The position brief could not be created.");
    } finally {
      button.disabled = false;
      updateCreateButtonState();
    }
  }

  async function loadPositionBrief(reviewId) {
    if (!reviewId) return;
    const content = $("#brief-content");
    content.replaceChildren(node("p", "loading", "Loading and verifying the saved calculation…"));
    try {
      const { data } = await request(`/tenants/${state.tenant.id}/position-reviews/briefs/${reviewId}`);
      state.review = data;
      renderPositionBrief(data);
      updateRecordUi(data);
    } catch (error) {
      content.replaceChildren(node("div", "preview-summary is-error", error.message || "The saved brief could not be verified."));
    }
  }

  function renderBriefSelection() {
    if (state.review) updateRecordUi(state.review);
  }

  function addTag(parent, label, muted = false) {
    parent.append(node("span", `tag${muted ? " tag-muted" : ""}`, label));
  }

  function appendLabeledValue(parent, label, value, className = "") {
    const container = node("div", className || "metric-value");
    container.append(node("span", "", label), node("strong", "", value ?? "Not available"));
    parent.append(container);
  }

  function resultCard(title, eyebrow = "") {
    const card = node("article", "result-card");
    if (eyebrow) card.append(node("p", "eyebrow", eyebrow));
    card.append(node("h3", "", title));
    return card;
  }

  function appendList(parent, values, className = "reason-list") {
    const list = node("ul", className);
    for (const value of values || []) list.append(node("li", "", typeof value === "string" ? value : JSON.stringify(value)));
    parent.append(list);
    return list;
  }

  function renderEvidenceRows(parent, records, title) {
    const details = node("details", "evidence-card");
    details.append(node("summary", "", `${title} · ${records?.length || 0} source record(s)`));
    const body = node("div", "evidence-body");
    if (!records?.length) body.append(node("p", "muted", "No source rows were attached to this fact."));
    for (const record of records || []) {
      const fact = node("div", "evidence-fact");
      const head = [record.project_code, record.record_id, record.week_start_date].filter(Boolean).join(" · ");
      fact.append(node("h4", "", head || "Source fact"));
      const fields = {
        import_id: record.import_id,
        file_name: record.file_name,
        planned_hours_per_week: record.planned_hours_per_week,
        start_date: record.start_date,
        end_date: record.end_date,
        status: record.status,
        source_rows: record.source_rows,
      };
      for (const [key, value] of Object.entries(fields)) {
        if (value === undefined || value === null) continue;
        const line = node("p");
        line.append(node("strong", "", `${key}: `), document.createTextNode(typeof value === "object" ? JSON.stringify(value) : String(value)));
        fact.append(line);
      }
      body.append(fact);
    }
    details.append(body);
    parent.append(details);
  }

  function renderOperationalSection(container, operational) {
    const comparison = operational?.comparison || {};
    const section = resultCard("Recorded operational plan", "M5 · FOUR-WEEK COMPARISON");
    const stateLine = node("div", "hero-tags");
    addTag(stateLine, comparison.state === "available" ? "Comparison available · source-limited" : "Comparison abstained", comparison.state !== "available");
    addTag(stateLine, operational.metric_catalog_status || "M5 status unavailable", true);
    section.append(stateLine);
    const periodText = node("p", "muted", `Baseline ${comparison.baseline_period?.start_week_date || "?"} – ${comparison.baseline_period?.end_week_date || "?"} · Current ${comparison.current_period?.start_week_date || "?"} – ${comparison.current_period?.end_week_date || "?"}.`);
    section.append(periodText);
    section.append(node("p", "muted", comparison.interpretation_limit || "Recorded planned assignments are not actual work, timesheets, or utilization."));
    if (comparison.state === "available") {
      const pair = node("div", "metric-pair");
      appendLabeledValue(pair, "Baseline sum · planned hours/week across 4 buckets", comparison.baseline_recorded_planned_hours);
      appendLabeledValue(pair, "Current sum · planned hours/week across 4 buckets", comparison.current_recorded_planned_hours);
      appendLabeledValue(pair, "Recorded arithmetic difference · not a cause", comparison.change_in_recorded_planned_hours, "metric-value is-change");
      section.append(pair);
    } else {
      section.append(node("div", "abstention-card"));
      const abstention = $(".abstention-card", section);
      abstention.append(node("h4", "", "No four-week change calculated"));
      abstention.append(node("p", "", comparison.abstention || "One or more period, version, cohort, source-completeness, or human-attestation requirements were not met."));
      if (Array.isArray(comparison.reason_codes)) appendList(abstention, comparison.reason_codes);
    }

    const tableWrap = node("div", "record-table-wrap");
    const table = node("table", "week-table");
    const head = node("thead");
    const headerRow = node("tr");
    for (const label of ["Period", "Week of", "Recorded planned assignment hours/week", "Completeness"]) headerRow.append(node("th", "", label));
    head.append(headerRow);
    table.append(head);
    const tbody = node("tbody");
    for (const periodName of ["baseline", "current"]) {
      for (const week of comparison.weekly_values?.[periodName] || []) {
        const tr = node("tr");
        tr.append(node("td", "", periodName === "baseline" ? "Baseline" : "Current"));
        tr.append(node("td", "", week_start_label(week.week_start_date)));
        tr.append(node("td", "code-value", week.active_employee_committed_hours_per_week ?? "Not recorded"));
        tr.append(node("td", "", week.assignment_completeness || "Not attested"));
        tbody.append(tr);
      }
    }
    table.append(tbody);
    tableWrap.append(table);
    section.append(tableWrap);
    if (Array.isArray(comparison.reason_codes) && comparison.reason_codes.length) {
      section.append(node("h4", "", "Abstention reasons"));
      appendList(section, comparison.reason_codes);
    }
    renderEvidenceRows(section, comparison.baseline_assignment_evidence || [], "Baseline M5 assignment lineage");
    renderEvidenceRows(section, comparison.current_assignment_evidence || [], "Current M5 assignment lineage");
    container.append(section);
  }

  function week_start_label(value) {
    return value ? formatWeek(value) : "Date unavailable";
  }

  function renderCapacityFindings(container, operational) {
    const findings = operational?.current_recorded_capacity_findings || [];
    const unresolved = operational?.current_unresolved_capacity_comparisons || [];
    const card = resultCard("Current capacity observations", "M5 · MANAGEMENT ATTENTION ONLY");
    card.append(node("p", "muted", "These are recorded plan-versus-capacity comparisons, not proof of workload, actual performance, or assignment completeness."));
    if (!findings.length) card.append(node("p", "muted", "No source-linked confirmed or estimated capacity finding is available."));
    for (const finding of findings) {
      const fact = node("div", "evidence-fact");
      const severity = finding.state === "confirmed_capacity_observation" ? "Confirmed-capacity comparison" : "Estimated-capacity comparison";
      fact.append(node("h4", "", `${severity} · ${finding.employee_code || shortId(finding.employee_id)} · ${week_start_label(finding.week_start_date)}`));
      fact.append(node("p", "", `Recorded available capacity: ${finding.available_hours ?? "Not available"} hours/week · recorded committed plan: ${finding.committed_hours_per_week ?? "Not available"} hours/week · arithmetic excess: ${finding.recorded_excess_hours ?? "Not available"} hours/week.`));
      fact.append(node("p", "", `Capacity currentness attestation: ${finding.capacity_currentness_attestation || "not_attested"}.`));
      fact.append(node("p", "", finding.interpretation_limit || "Reported values do not prove all commitments are represented."));
      appendList(fact, finding.limitations || []);
      renderEvidenceRows(fact, [finding.capacity_evidence].filter(Boolean), "Capacity input evidence");
      renderEvidenceRows(fact, finding.assignment_evidence || [], "Committed assignment evidence");
      card.append(fact);
    }
    if (unresolved.length) {
      card.append(node("h4", "", "Unresolved comparisons"));
      for (const item of unresolved) {
        const fact = node("div", "abstention-card");
        fact.append(node("h4", "", `${item.employee_code || item.state || "Unresolved"} · ${item.state || "capacity unavailable"}`));
        fact.append(node("p", "", item.interpretation_limit || "Missing capacity is not treated as zero."));
        for (const week of item.weeks || []) {
          fact.append(node("p", "", `${week_start_label(week.week_start_date)} · ${week.capacity_state || "unknown"} · ${week.reasons?.join(", ") || "no reason detail"}`));
          renderEvidenceRows(fact, week.assignment_evidence || [], "Recorded assignment rows");
        }
        if (item.items) renderEvidenceRows(fact, item.items, "Unresolved M5 source records");
        card.append(fact);
      }
    }
    container.append(card);
  }

  function renderFinanceSection(container, financial) {
    const card = resultCard("Source-native finance observations", "NO FINANCIAL FORMULA OR COMPARISON");
    const stateTags = node("div", "hero-tags");
    addTag(stateTags, financial?.state === "source_native_observations" ? "Source values preserved" : "No source rows provided", true);
    addTag(stateTags, "Financial comparison abstained", true);
    card.append(stateTags);
    for (const limitation of financial?.limitations || []) card.append(node("p", "muted", limitation));
    const observations = financial?.observations || [];
    if (!observations.length) {
      card.append(node("div", "abstention-card"));
      const abstention = $(".abstention-card", card);
      abstention.append(node("h4", "", "No financial observations"));
      abstention.append(node("p", "", financial?.abstention_reasons?.join(", ") || "No finance export was provided. No financial conclusion is inferred."));
    } else {
      const wrap = node("div", "finance-table-wrap");
      const table = node("table", "finance-table");
      const thead = node("thead");
      const trHead = node("tr");
      for (const label of ["Source label", "Source value text", "Currency", "Source period", "Source record"]) trHead.append(node("th", "", label));
      thead.append(trHead);
      table.append(thead);
      const tbody = node("tbody");
      for (const item of observations) {
        const row = node("tr");
        row.append(node("td", "", item.source_label));
        row.append(node("td", "amount", item.source_value_text));
        row.append(node("td", "", item.currency_code));
        row.append(node("td", "", `${item.period_start || "?"} – ${item.period_end || "?"}`));
        row.append(node("td", "code-value", item.source_record_id || "Not supplied"));
        tbody.append(row);
      }
      table.append(tbody);
      wrap.append(table);
      card.append(wrap);
      card.append(node("p", "fine-print", "Values are displayed as their source text; they are not parsed, aggregated, converted, or compared."));
    }
    if (Array.isArray(financial?.abstention_reasons)) appendList(card, financial.abstention_reasons);
    if (financial?.source_import) {
      const meta = node("details", "evidence-card");
      meta.append(node("summary", "", "Finance source import provenance"));
      const body = node("div", "evidence-body");
      for (const [key, value] of Object.entries(financial.source_import)) {
        const line = node("p");
        line.append(node("strong", "", `${key}: `), document.createTextNode(String(value ?? "Not available")));
        body.append(line);
      }
      meta.append(body);
      card.append(meta);
    }
    container.append(card);
  }

  function renderSourceCatalog(container, input) {
    const card = resultCard("Evidence register", "SAVED INPUT SNAPSHOT");
    const sources = input.source_import_catalog || [];
    card.append(node("p", "muted", `${sources.length} company-scoped M4 imports are referenced by the immutable M5/M10 snapshots. File bytes are not retained by M4 after apply; source-row hashes, mappings, and trace links remain.`));
    for (const source of sources) {
      const details = node("details", "evidence-card");
      details.append(node("summary", "", `${source.dataset_type || "Source"} · ${source.file_name || "file"} · ${source.status || "status unknown"}`));
      const body = node("div", "evidence-body");
      for (const [key, value] of Object.entries(source)) {
        const line = node("p");
        line.append(node("strong", "", `${key}: `), document.createTextNode(String(value ?? "Not available")));
        body.append(line);
      }
      details.append(body);
      card.append(details);
    }
    const runCard = node("div", "evidence-fact");
    runCard.append(node("h4", "", "Exact M5 run versions"));
    for (const [name, run] of Object.entries({ baseline: input.baseline_metric_run, current: input.current_metric_run })) {
      if (!run) continue;
      const line = node("p");
      line.textContent = `${name}: ${run.id} · ${run.metric_catalog_version} · ${run.calculation_version} · input ${run.input_hash} · result ${run.result_hash}`;
      runCard.append(line);
    }
    card.append(runCard);
    container.append(card);
  }

  function renderPositionBrief(review) {
    const content = $("#brief-content");
    content.replaceChildren();
    const result = review.result_snapshot || {};
    const input = review.input_snapshot || {};
    const operational = result.operational || {};
    const hero = node("section", "result-hero");
    const tags = node("div", "hero-tags");
    addTag(tags, result.synthetic_label || "SYNTHETIC DEMO DATA", true);
    addTag(tags, review.calculation_version || "M10 version unavailable", true);
    addTag(tags, review.metric_catalog_version || "M5 version unavailable", true);
    hero.append(tags);
    hero.append(node("h3", "", "Position Decision Brief"));
    hero.append(node("p", "", `Saved ${formatDate(review.created_at)} · ${review.start_week_date} – ${review.end_week_date} · state ${review.result_state}. This is evidence for a human review, not a recommendation, approval, or external action.`));
    hero.append(node("code", "hash-line", `Input hash ${review.input_hash} · Result hash ${review.result_hash}`));
    content.append(hero);

    const grid = node("div", "brief-grid");
    renderOperationalSection(grid, operational);
    renderCapacityFindings(grid, operational);
    renderFinanceSection(grid, result.financial || {});

    const attestationCard = resultCard("Human attestations", "ATTRIBUTION · NOT SOURCE PROOF");
    const attestations = result.human_attestations || input.attestations || {};
    for (const [key, value] of Object.entries(attestations)) {
      if (key.endsWith("_attested") || key === "m5_definition_acknowledged") {
        const fact = node("div", "evidence-fact");
        fact.append(node("h4", "", key.replaceAll("_", " ")));
        fact.append(node("p", "", value === true ? "Attested" : value === false ? "Not attested" : String(value)));
        attestationCard.append(fact);
      }
    }
    attestationCard.append(node("p", "fine-print", `Recorded by ${attestations.actor_display_name || "unknown user"} · ${formatDate(attestations.attested_at)}. Attestations do not independently establish source completeness, currentness, or accuracy.`));
    grid.append(attestationCard);

    const limitsCard = resultCard("Limitations & abstentions", "READ BEFORE INTERPRETING");
    appendList(limitsCard, result.limitations || [], "limitation-list");
    for (const abstention of result.abstentions || []) {
      const fact = node("div", "abstention-card");
      fact.append(node("h4", "", abstention.area || "Abstained"));
      fact.append(node("p", "", abstention.message || "No conclusion was produced."));
      if (Array.isArray(abstention.reason_codes)) appendList(fact, abstention.reason_codes);
      limitsCard.append(fact);
    }
    grid.append(limitsCard);

    const sourceCard = resultCard("Provenance & replay context", "HASH-BOUND EVIDENCE");
    sourceCard.append(node("p", "muted", `M10 ${review.calculation_version} · input ${review.input_hash} · result ${review.result_hash}. The API verifies the stored M10 output against these immutable inputs when the brief is read.`));
    for (const source of review.operational_imports || []) {
      const fact = node("div", "evidence-fact");
      fact.append(node("h4", "", `${DATASET_LABELS[source.dataset_type] || source.dataset_type} · ${source.file_name}`));
      fact.append(node("p", "", `Import ${source.import_id} · ${source.status} · ${source.total_rows} rows · ${source.created_rows} created · ${source.skipped_rows} skipped`));
      fact.append(node("p", "", `File ${source.file_hash} · validation ${source.validation_hash} · mapping v${source.mapping_version}`));
      sourceCard.append(fact);
    }
    renderSourceCatalog(sourceCard, input);
    grid.append(sourceCard);
    content.append(grid);
    updateRecordUi(review);
  }

  async function loadSelectedBrief() {
    const id = $("#brief-history").value;
    if (id) await loadPositionBrief(id);
  }

  function updateRecordUi(review) {
    const binding = $("#review-binding-text");
    const form = $("#disposition-form");
    const history = $("#disposition-history");
    history.replaceChildren();
    if (!review) {
      binding.textContent = "Select a saved brief in Position Brief first.";
      setVisible(form, false);
      return;
    }
    binding.textContent = `Brief ${review.id} · result hash ${review.result_hash} · ${review.calculation_version} · ${review.start_week_date} – ${review.end_week_date}`;
    const mine = (review.dispositions || []).find((item) => item.actor_user_id === state.user?.id);
    if (mine) {
      setVisible(form, false);
      const existing = node("div", "preview-summary");
      existing.append(node("strong", "", `Your immutable disposition: ${mine.disposition.replaceAll("_", " ")}. `));
      existing.append(document.createTextNode(`Recorded ${formatDate(mine.created_at)}. To change your view, create a new versioned brief; this disposition cannot be edited.`));
      history.append(existing);
    } else {
      setVisible(form, true);
    }
    for (const disposition of review.dispositions || []) {
      const card = node("article", "disposition-history-card");
      card.append(node("h3", "", `${disposition.disposition.replaceAll("_", " ")} · ${disposition.actor_display_name}`));
      card.append(node("p", "", disposition.rationale));
      card.append(node("p", "fine-print", `Recorded ${formatDate(disposition.created_at)} · bound to ${disposition.review_hash}`));
      history.append(card);
    }
  }

  async function submitDisposition(event) {
    event.preventDefault();
    clearMessage();
    const review = state.review;
    const rationale = fieldValue("#rationale").trim();
    if (!review) {
      showMessage("Load an exact saved brief before recording a human review.");
      return;
    }
    if (!rationale) {
      showMessage("Add a rationale or follow-up note.");
      return;
    }
    const button = $("#disposition-form button[type='submit']");
    button.disabled = true;
    try {
      await request(`/tenants/${state.tenant.id}/position-reviews/briefs/${review.id}/dispositions`, {
        method: "POST",
        body: {
          review_hash: review.result_hash,
          disposition: fieldValue("#disposition"),
          rationale,
        },
      });
      const { data } = await request(`/tenants/${state.tenant.id}/position-reviews/briefs/${review.id}`);
      state.review = data;
      renderPositionBrief(data);
      updateRecordUi(data);
      $("#rationale").value = "";
      showMessage("Human review recorded immutably against the exact brief hash. No external action was taken.", "success");
    } catch (error) {
      showMessage(error.message || "The human review could not be saved.");
    } finally {
      button.disabled = false;
    }
  }

  function showScreen(name) {
    const allowed = ["create", "brief", "record"];
    if (!allowed.includes(name)) return;
    for (const screen of allowed) setVisible($(`#screen-${screen}`), screen === name);
    for (const button of $$(".step-button")) {
      const selected = button.dataset.screen === name;
      button.classList.toggle("is-current", selected);
      button.setAttribute("aria-current", selected ? "step" : "false");
    }
    if (name === "record" && state.review) updateRecordUi(state.review);
  }

  async function logout() {
    try {
      if (state.accessToken) await request("/auth/logout", { method: "POST" }, false);
    } catch (_error) {
      // Tokens are cleared locally even if the server cannot be reached.
    }
    clearAuth();
    state.tenants = [];
    showAuthenticated(false);
  }

  function bindEvents() {
    $("#auth-mode").addEventListener("change", toggleRegisterFields);
    $("#auth-form").addEventListener("submit", submitAuth);
    $("#tenant-form").addEventListener("submit", (event) => {
      event.preventDefault();
      createTenant(tenantCreateBody());
    });
    $("#logout-button").addEventListener("click", logout);
    $("#company-picker").addEventListener("change", async (event) => {
      const selected = state.tenants.find((tenant) => tenant.id === event.target.value);
      if (!selected) return;
      state.tenant = selected;
      sessionStorage.setItem(SESSION_KEYS.tenant, selected.id);
      await loadWorkspace();
    });
    $("#seed-button").addEventListener("click", async () => {
      if (!state.demo || state.demo.demo_seed || !isLabeledDemo()) return;
      const button = $("#seed-button");
      button.disabled = true;
      try {
        const { data } = await request(`/tenants/${state.tenant.id}/position-reviews/demo/seed`, { method: "POST" });
        showMessage(`Synthetic baseline prepared · ${data.synthetic_label} · ${data.seed_version}.`, "success");
        await loadWorkspace();
      } catch (error) {
        showMessage(error.message || "The synthetic baseline could not be prepared.");
      } finally {
        button.disabled = false;
      }
    });
    for (const card of $$(".import-card")) {
      $(".inspect-button", card).addEventListener("click", () => inspectOperationalFile(card));
      $(".load-mapping-button", card).addEventListener("click", () => loadImportMapping(card));
      $(".validate-button", card).addEventListener("click", () => validateOperationalImport(card));
      $(".apply-button", card).addEventListener("click", () => confirmAndApplyImport(card));
      $(".file-input", card).addEventListener("change", () => {
        setImportCardSuccess(card, "Selected file stays in this browser until you inspect it; only the exact built-in synthetic template is accepted.");
      });
    }
    for (const link of $$("[data-template]")) {
      link.addEventListener("click", async (event) => {
        event.preventDefault();
        if (!state.tenant) return;
        try {
          const blob = await requestBlob(`/tenants/${state.tenant.id}/position-reviews/demo/templates/${encodeURIComponent(link.dataset.template)}`);
          const objectUrl = URL.createObjectURL(blob);
          const anchor = node("a");
          anchor.href = objectUrl;
          anchor.download = `synthetic_demo_${link.dataset.template}.csv`;
          document.body.append(anchor);
          anchor.click();
          anchor.remove();
          URL.revokeObjectURL(objectUrl);
        } catch (error) {
          showMessage(error.message || "The synthetic template could not be downloaded.");
        }
      });
    }
    $("#finance-upload-button").addEventListener("click", uploadFinanceFile);
    $("#finance-file").addEventListener("change", () => {
      $("#finance-preview").replaceChildren(node("p", "fine-print", "Selected finance file stays in this browser until validated. M10 accepts only the exact built-in synthetic file."));
    });
    $("#synthetic-confirm").addEventListener("change", updateCreateButtonState);
    $("#create-review-button").addEventListener("click", createPositionBrief);
    for (const button of $$(".step-button")) button.addEventListener("click", () => showScreen(button.dataset.screen));
    $("#reload-brief-button").addEventListener("click", loadSelectedBrief);
    $("#disposition-form").addEventListener("submit", submitDisposition);
  }

  async function bootstrap() {
    bindEvents();
    toggleRegisterFields();
    updateCreateButtonState();
    if (!state.accessToken) {
      showAuthenticated(false);
      return;
    }
    try {
      const { data } = await request("/auth/me");
      state.user = data;
      await loadTenants();
    } catch (_error) {
      clearAuth();
      showAuthenticated(false);
    }
  }

  bootstrap();
})();
