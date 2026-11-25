const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/+$/, "");

async function request(path, { token, ...options } = {}) {
  let response;
  try {
    response = await fetch(`${apiBaseUrl}${path}`, {
      ...options,
      headers: {
        Accept: "application/json",
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...options.headers,
      },
    });
  } catch {
    throw new Error(
      "Could not reach the backend. Check that it is running and VITE_API_BASE_URL is correct.",
    );
  }

  if (!response.ok) {
    let message = `The request failed (HTTP ${response.status}).`;
    try {
      const error = await response.json();
      if (typeof error?.error === "string") message = error.error;
    } catch {
      // Keep the HTTP status message when the server does not return JSON.
    }
    const requestError = new Error(message);
    requestError.status = response.status;
    throw requestError;
  }

  if (response.status === 204) return null;
  try {
    return await response.json();
  } catch {
    throw new Error("The backend returned an invalid response.");
  }
}

export async function register(email, password) {
  return request("/api/v1/auth/register", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export async function login(email, password) {
  return request("/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export async function getCurrentUser(token) {
  const response = await request("/api/v1/auth/me", { token });
  return response.user;
}

export async function logout(token) {
  return request("/api/v1/auth/logout", { method: "POST", token });
}

export async function listScans(token) {
  const response = await request("/api/v1/scans?limit=20", { token });
  return response.scans;
}

export async function getScan(token, scanId) {
  const response = await request(`/api/v1/scans/${encodeURIComponent(scanId)}`, { token });
  return response.scan;
}

export async function scanWorkflow(workflow, token) {
  const result = await request("/api/v1/scans", {
    method: "POST",
    token,
    body: JSON.stringify({ platform: "n8n", workflow }),
  });

  if (!result || !Array.isArray(result.findings)) {
    throw new Error("The backend returned an unexpected scan response.");
  }
  return result;
}
