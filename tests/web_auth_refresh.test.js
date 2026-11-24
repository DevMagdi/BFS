const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

let activeFetch = async () => {
  throw new Error("No mock fetch was configured.");
};
const window = {};
vm.runInNewContext(
  fs.readFileSync(path.join(__dirname, "../src/businessflow/web/assets/api_client.js"), "utf8"),
  {
    window,
    fetch: (...args) => activeFetch(...args),
    Headers,
    FormData,
    Blob,
  },
);

class ApiFailure extends Error {
  constructor(message, status, code = "request_failed") {
    super(message);
    this.name = "ApiFailure";
    this.status = status;
    this.code = code;
  }
}

function makeResponse(status, data) {
  return {
    status,
    ok: status >= 200 && status < 300,
    json: async () => data,
    blob: async () => new Blob(["download"]),
  };
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function makeClient(onAuthCleared = () => {}) {
  const values = new Map([
    ["access", "old-access"],
    ["refresh", "old-refresh"],
  ]);
  const sessionStorage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };
  const state = {
    accessToken: "old-access",
    refreshToken: "old-refresh",
    user: { id: "user-1" },
    tenant: { id: "tenant-1" },
    demo: null,
  };
  const client = window.BusinessFlowApiClient.create({
    apiBase: "/api/v1",
    state,
    sessionStorage,
    sessionKeys: { access: "access", refresh: "refresh", tenant: "tenant" },
    ApiFailure,
    apiErrorMessage: (data, status) => data?.error?.message || `HTTP ${status}`,
    onAuthCleared,
  });
  return { client, state, sessionStorage };
}

async function successfulRotationIsSharedAndBlobRetryIsBounded() {
  let refreshCalls = 0;
  let apiCalls = 0;
  let blobCalls = 0;
  let clearCalls = 0;
  const authorization = [];
  activeFetch = async (url, options) => {
    if (url === "/api/v1/auth/refresh") {
      refreshCalls += 1;
      await new Promise((resolve) => setTimeout(resolve, 10));
      return makeResponse(200, {
        access_token: "fresh-access",
        refresh_token: "fresh-refresh",
      });
    }
    const token = options.headers.get("Authorization");
    authorization.push([url, token]);
    if (url === "/api/v1/tenants/demo") {
      apiCalls += 1;
      return apiCalls === 1
        ? makeResponse(401, { error: { message: "expired" } })
        : makeResponse(200, { ready: true });
    }
    if (url === "/api/v1/tenants/demo/templates/employees") {
      blobCalls += 1;
      return makeResponse(401, { error: { message: "still unauthorized" } });
    }
    throw new Error(`Unexpected request: ${url}`);
  };

  const { client, state, sessionStorage } = makeClient(() => {
    clearCalls += 1;
  });
  const apiRequest = client.request("/tenants/demo");
  const blobRequest = client.requestBlob("/tenants/demo/templates/employees");
  const [apiResult, blobResult] = await Promise.allSettled([apiRequest, blobRequest]);

  assert.equal(apiResult.status, "fulfilled");
  assert.deepEqual(apiResult.value.data, { ready: true });
  assert.equal(blobResult.status, "rejected");
  assert.equal(blobResult.reason.status, 401);
  assert.equal(refreshCalls, 1, "API and blob 401s must share one rotation request");
  assert.equal(apiCalls, 2, "normal API request retries once");
  assert.equal(blobCalls, 2, "blob request retries once and cannot loop");
  assert.deepEqual(
    authorization,
    [
      ["/api/v1/tenants/demo", "Bearer old-access"],
      ["/api/v1/tenants/demo/templates/employees", "Bearer old-access"],
      ["/api/v1/tenants/demo", "Bearer fresh-access"],
      ["/api/v1/tenants/demo/templates/employees", "Bearer fresh-access"],
    ],
  );
  assert.equal(state.refreshToken, "fresh-refresh");
  assert.equal(sessionStorage.getItem("refresh"), "fresh-refresh");
  assert.equal(clearCalls, 0);
}

async function invalidRefreshClearsAuthOnceWithoutRetryingOrRedirecting() {
  let refreshCalls = 0;
  let apiCalls = 0;
  let blobCalls = 0;
  let clearCalls = 0;
  activeFetch = async (url) => {
    if (url === "/api/v1/auth/refresh") {
      refreshCalls += 1;
      await new Promise((resolve) => setTimeout(resolve, 10));
      return makeResponse(401, { error: { message: "invalid refresh token" } });
    }
    if (url === "/api/v1/tenants/demo") {
      apiCalls += 1;
      return makeResponse(401, { error: { message: "expired" } });
    }
    if (url === "/api/v1/tenants/demo/templates/employees") {
      blobCalls += 1;
      return makeResponse(401, { error: { message: "expired" } });
    }
    throw new Error(`Unexpected request: ${url}`);
  };

  const { client, state, sessionStorage } = makeClient(() => {
    clearCalls += 1;
  });
  const results = await Promise.allSettled([
    client.request("/tenants/demo"),
    client.requestBlob("/tenants/demo/templates/employees"),
  ]);

  assert.deepEqual(results.map((result) => result.status), ["rejected", "rejected"]);
  assert.equal(refreshCalls, 1);
  assert.equal(apiCalls, 1, "failed refresh must not retry the normal request");
  assert.equal(blobCalls, 1, "failed refresh must not retry the blob request");
  assert.equal(clearCalls, 1, "concurrent 401 responses must clear auth state only once");
  assert.equal(state.accessToken, null);
  assert.equal(state.refreshToken, null);
  assert.equal(sessionStorage.getItem("access"), null);
  assert.equal(sessionStorage.getItem("refresh"), null);
}

async function staleRefreshFailureDoesNotClearNewLogin() {
  const refreshStarted = deferred();
  const refreshResult = deferred();
  const authorization = [];
  let refreshCalls = 0;
  let apiCalls = 0;
  let clearCalls = 0;
  activeFetch = async (url, options) => {
    if (url === "/api/v1/auth/refresh") {
      refreshCalls += 1;
      refreshStarted.resolve();
      return refreshResult.promise;
    }
    if (url === "/api/v1/tenants/stale") {
      authorization.push(options.headers.get("Authorization"));
      apiCalls += 1;
      return apiCalls === 1
        ? makeResponse(401, { error: { message: "old access expired" } })
        : makeResponse(200, { session: "new login" });
    }
    throw new Error(`Unexpected request: ${url}`);
  };

  const { client, state, sessionStorage } = makeClient(() => {
    clearCalls += 1;
  });
  const pending = client.request("/tenants/stale");
  await refreshStarted.promise;
  client.persistTokens({
    access_token: "new-login-access",
    refresh_token: "new-login-refresh",
  });
  refreshResult.resolve(makeResponse(401, { error: { message: "old refresh rejected" } }));

  const result = await pending;
  assert.deepEqual(result.data, { session: "new login" });
  assert.equal(refreshCalls, 1);
  assert.equal(apiCalls, 2, "the request may retry once with the replacement login");
  assert.deepEqual(authorization, ["Bearer old-access", "Bearer new-login-access"]);
  assert.equal(clearCalls, 0, "a stale 401 must not clear the replacement login");
  assert.equal(state.accessToken, "new-login-access");
  assert.equal(state.refreshToken, "new-login-refresh");
  assert.equal(sessionStorage.getItem("access"), "new-login-access");
  assert.equal(sessionStorage.getItem("refresh"), "new-login-refresh");
}

async function lateUnauthorizedRequestReusesRotatedAccessToken() {
  const refreshStarted = deferred();
  const refreshResult = deferred();
  const delayedUnauthorized = deferred();
  const slowRequestStarted = deferred();
  const authorization = [];
  let refreshCalls = 0;
  let fastCalls = 0;
  let slowCalls = 0;
  activeFetch = async (url, options) => {
    if (url === "/api/v1/auth/refresh") {
      refreshCalls += 1;
      refreshStarted.resolve();
      return refreshResult.promise;
    }
    const token = options.headers.get("Authorization");
    authorization.push([url, token]);
    if (url === "/api/v1/fast") {
      fastCalls += 1;
      return fastCalls === 1 ? makeResponse(401, {}) : makeResponse(200, { ok: "fast" });
    }
    if (url === "/api/v1/slow") {
      slowCalls += 1;
      if (slowCalls === 1) {
        slowRequestStarted.resolve();
        return delayedUnauthorized.promise;
      }
      return makeResponse(200, { ok: "slow" });
    }
    throw new Error(`Unexpected request: ${url}`);
  };

  const { client, state } = makeClient();
  const fastRequest = client.request("/fast");
  const slowRequest = client.request("/slow");
  await slowRequestStarted.promise;
  await refreshStarted.promise;
  refreshResult.resolve(
    makeResponse(200, { access_token: "fresh-access", refresh_token: "fresh-refresh" }),
  );
  assert.deepEqual((await fastRequest).data, { ok: "fast" });

  delayedUnauthorized.resolve(makeResponse(401, { error: { message: "late old-token 401" } }));
  assert.deepEqual((await slowRequest).data, { ok: "slow" });
  assert.equal(refreshCalls, 1, "a late 401 must reuse the already-rotated token");
  assert.equal(fastCalls, 2, "the first API request retries at most once");
  assert.equal(slowCalls, 2, "the delayed API request retries at most once");
  assert.deepEqual(
    authorization,
    [
      ["/api/v1/fast", "Bearer old-access"],
      ["/api/v1/slow", "Bearer old-access"],
      ["/api/v1/fast", "Bearer fresh-access"],
      ["/api/v1/slow", "Bearer fresh-access"],
    ],
  );
  assert.equal(state.refreshToken, "fresh-refresh");
}

async function logout401DoesNotStartRefreshOrRetry() {
  let refreshCalls = 0;
  let logoutCalls = 0;
  let logoutRequest;
  activeFetch = async (url, options) => {
    if (url === "/api/v1/auth/refresh") refreshCalls += 1;
    if (url === "/api/v1/auth/logout") {
      logoutCalls += 1;
      logoutRequest = {
        authorization: options.headers.get("Authorization"),
        body: JSON.parse(options.body),
      };
      return makeResponse(401, { error: { message: "already expired" } });
    }
    throw new Error(`Unexpected request: ${url}`);
  };

  const { client } = makeClient();
  await assert.rejects(
    client.request(
      "/auth/logout",
      { method: "POST", body: { refresh_token: "old-refresh" } },
      false,
    ),
    (error) => error.status === 401,
  );
  assert.equal(logoutCalls, 1);
  assert.equal(refreshCalls, 0, "logout must not trigger a refresh loop");
  assert.equal(logoutRequest.authorization, "Bearer old-access");
  assert.deepEqual(logoutRequest.body, { refresh_token: "old-refresh" });
}

(async () => {
  await successfulRotationIsSharedAndBlobRetryIsBounded();
  await invalidRefreshClearsAuthOnceWithoutRetryingOrRedirecting();
  await staleRefreshFailureDoesNotClearNewLogin();
  await lateUnauthorizedRequestReusesRotatedAccessToken();
  await logout401DoesNotStartRefreshOrRetry();
  console.log("Browser API refresh coordination checks passed.");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
