const apiBaseUrl = import.meta.env.VITE_API_BASE_URL?.replace(/\/+$/, "");

export async function scanWorkflow(workflow) {
  if (!apiBaseUrl) {
    throw new Error(
      "The backend is not configured yet. Set VITE_API_BASE_URL in frontend/.env.local.",
    );
  }

  let response;

  try {
    response = await fetch(`${apiBaseUrl}/api/v1/scans`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify({ platform: "n8n", workflow }),
    });
  } catch {
    throw new Error(
      "Could not reach the backend. Check that it is running and VITE_API_BASE_URL is correct.",
    );
  }

  if (!response.ok) {
    throw new Error(`The scan request failed (HTTP ${response.status}).`);
  }

  const result = await response.json();

  if (!result || !Array.isArray(result.findings)) {
    throw new Error("The backend returned an unexpected scan response.");
  }

  return result;
}
