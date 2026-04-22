/**
 * checker/normalize.js
 * Stable job ID + normalization for API and DOM sources
 */

import crypto from "crypto";

// ─── Stable job ID ────────────────────────────────────────────────────────────

/**
 * Produces a deterministic ID that survives URL changes (UTM, redirects).
 *
 * Priority:
 *   1. Amazon job ID from URL (JOB-CA-XXXXXXXXX format)
 *   2. Numeric job ID from URL (/jobs/12345678)
 *   3. Any other ID params (jobId=, requisitionId=)
 *   4. SHA1(title.toLowerCase() + city.toLowerCase()) — never duplicates,
 *      never changes across URL variations
 */
export function stableJobId(job) {
  const url = job.url || "";

  const amazonJobId =
    url.match(/jobId[=:]([A-Z0-9-]+)/i) ??
    url.match(/(JOB-CA-\d+)/i) ??
    url.match(/(JOB-US-\d+)/i);

  if (amazonJobId) return `amzn_${amazonJobId[1].toUpperCase()}`;

  const numericPatterns = [
    /\/jobs\/(\d+)/i,
    /[?&]id=(\d+)/i,
    /requisitionId[=:](\d+)/i,
    /\/(\d{8,})/,
  ];

  for (const re of numericPatterns) {
    const m = url.match(re);
    if (m) return `amzn_${m[1]}`;
  }

  const raw = [
    (job.title || "").toLowerCase().replace(/\s+/g, " ").trim(),
    (job.city || "").toLowerCase().replace(/\s+/g, " ").trim(),
  ].join("|");

  return `amzn_${crypto.createHash("sha1").update(raw).digest("hex").slice(0, 14)}`;
}

// ─── API job normalization ────────────────────────────────────────────────────

/**
 * Normalize a raw Amazon API job object into our standard shape.
 * Handles multiple known API response schemas.
 */
export function normalizeApiJob(item) {
  if (!item || typeof item !== "object") return null;

  const title = (
    item.title ??
    item.jobTitle ??
    item.position ??
    item.positionTitle ??
    ""
  ).trim();

  if (!title) return null;

  const rawUrl =
    item.applyUrl ?? item.url ?? item.jobUrl ?? item.link ?? item.detailUrl ?? "";

  const url = rawUrl.startsWith("http")
    ? rawUrl
    : rawUrl
      ? `https://hiring.amazon.ca${rawUrl.startsWith("/") ? "" : "/"}${rawUrl}`
      : "";

  const city = (
    item.city ??
    item.locationName ??
    item.location ??
    item.locationCity ??
    ""
  ).trim();

  const description = (
    item.description ??
    item.shortDescription ??
    item.summary ??
    ""
  ).slice(0, 500);

  const explicitId =
    item.jobId ?? item.id ?? item.requisitionId ?? item.externalJobId ?? null;

  const id = explicitId
    ? `amzn_${String(explicitId).toUpperCase()}`
    : stableJobId({ url, title, city });

  return {
    id,
    title,
    url,
    city,
    description,
    source: "api",
    raw: item,
  };
}
