(() => {
  "use strict";

  function createApiClient({
    apiBase,
    state,
    sessionStorage,
    sessionKeys,
    ApiFailure,
    apiErrorMessage,
    onAuthCleared,
  }) {
    let refreshInFlight = null;

    function persistTokens(tokens) {
      state.accessToken = tokens.access_token;
      state.refreshToken = tokens.refresh_token;
      sessionStorage.setItem(sessionKeys.access, tokens.access_token);
      sessionStorage.setItem(sessionKeys.refresh, tokens.refresh_token);
    }

    function clearAuth() {
      const hadAuth = Boolean(
        state.accessToken ||
        state.refreshToken ||
        state.user ||
        state.tenant ||
        sessionStorage.getItem(sessionKeys.access) ||
        sessionStorage.getItem(sessionKeys.refresh),
      );
      state.accessToken = null;
      state.refreshToken = null;
      state.user = null;
      state.tenant = null;
      state.demo = null;
      sessionStorage.removeItem(sessionKeys.access);
      sessionStorage.removeItem(sessionKeys.refresh);
      sessionStorage.removeItem(sessionKeys.tenant);
      if (hadAuth) onAuthCleared();
    }

    function refreshAccessToken() {
      const refreshToken = state.refreshToken;
      if (!refreshToken) return Promise.resolve(false);
      if (refreshInFlight && refreshInFlight.refreshToken === refreshToken) {
        return refreshInFlight.promise;
      }

      const flight = { refreshToken, promise: null };
      refreshInFlight = flight;
      flight.promise = (async () => {
        try {
          const response = await fetch(`${apiBase}/auth/refresh`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ refresh_token: refreshToken }),
            credentials: "same-origin",
            cache: "no-store",
          });
          if (response.status === 401) {
            if (state.refreshToken === refreshToken) clearAuth();
            return false;
          }
          if (!response.ok) return false;

          const data = response.status === 204 ? null : await response.json().catch(() => null);
          if (!data || !data.access_token || !data.refresh_token) {
            if (state.refreshToken === refreshToken) clearAuth();
            return false;
          }
          if (state.refreshToken !== refreshToken) {
            return Boolean(state.accessToken && state.refreshToken);
          }
          persistTokens(data);
          return true;
        } catch (_error) {
          return false;
        } finally {
          if (refreshInFlight === flight) refreshInFlight = null;
        }
      })();
      return flight.promise;
    }

    async function refreshAfterUnauthorized(accessTokenUsed) {
      if (state.accessToken && state.accessToken !== accessTokenUsed) return true;
      if (await refreshAccessToken()) return true;
      return Boolean(state.accessToken && state.accessToken !== accessTokenUsed);
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
      const publicAuthPath = ["/auth/login", "/auth/refresh"].includes(pathOnly);
      const accessTokenUsed = state.accessToken;
      if (accessTokenUsed && !publicAuthPath) {
        headers.set("Authorization", `Bearer ${accessTokenUsed}`);
      }

      let response;
      try {
        response = await fetch(`${apiBase}${path}`, requestOptions);
      } catch (_error) {
        throw new ApiFailure("The local server could not be reached. Check that the API is running.", 0, "network_error");
      }
      if (
        response.status === 401 &&
        retry &&
        !publicAuthPath &&
        pathOnly !== "/auth/logout" &&
        await refreshAfterUnauthorized(accessTokenUsed)
      ) {
        return request(path, options, false);
      }
      const data = response.status === 204 ? null : await response.json().catch(() => null);
      if (!response.ok) {
        const error = data && data.error ? data.error : null;
        throw new ApiFailure(apiErrorMessage(data, response.status), response.status, error?.code);
      }
      return { data, response };
    }

    async function requestBlob(path, retry = true) {
      const headers = new Headers();
      const accessTokenUsed = state.accessToken;
      if (accessTokenUsed) headers.set("Authorization", `Bearer ${accessTokenUsed}`);
      let response;
      try {
        response = await fetch(`${apiBase}${path}`, {
          headers,
          credentials: "same-origin",
          cache: "no-store",
        });
      } catch (_error) {
        throw new ApiFailure("The local server could not be reached. Check that the API is running.", 0, "network_error");
      }
      if (response.status === 401 && retry && await refreshAfterUnauthorized(accessTokenUsed)) {
        return requestBlob(path, false);
      }
      if (!response.ok) {
        const data = await response.json().catch(() => null);
        throw new ApiFailure(apiErrorMessage(data, response.status), response.status, data?.error?.code);
      }
      return response.blob();
    }

    return { clearAuth, persistTokens, refreshAccessToken, request, requestBlob };
  }

  window.BusinessFlowApiClient = Object.freeze({ create: createApiClient });
})();
