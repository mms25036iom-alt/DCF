/** Permit explicit, credential-free source links. React always renders their labels as text. */
export function safeReportUrl(value) {
  if (typeof value !== "string") return "";
  const input = value.trim();
  if (/[\u0000-\u0020\u007f]/.test(input) || !/^https?:\/\//i.test(input)) return "";
  try {
    const url = new URL(input);
    return url.hostname && !url.username && !url.password ? url.href : "";
  } catch { return ""; }
}
