export async function readApiError(response: Response): Promise<string> {
  const fallback = `Request failed (${response.status}).`;
  try {
    const body = await response.json();
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) {
      return body.detail.map((item: { loc?: (string | number)[]; msg?: string }) =>
        `${item.loc?.slice(1).join(".") || "Request"}: ${item.msg || fallback}`,
      ).join("; ");
    }
  } catch {
    // A stopped backend can return an HTML or empty proxy error.
  }
  return fallback;
}
