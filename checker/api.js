/**
 * checker/api.js
 * Amazon hiring API — parallel endpoints + retry with exponential backoff
 */

import { httpGet } from "./http.js";
import { normalizeApiJob } from "./normalize.js";
import { log } from "./logger.js";

// ─── Endpoints ────────────────────────────────────────────────────────────────
// Amazon hiring portal API — multiple queries run in parallel
const API_BASE = "https://hiring.amazon.ca/api/v1/search";

const QUERIES = [
  { base_query: "warehouse associate", label: "warehouse" },
  { base_query: "fulfillment center", label: "fulfillment" },
  { base_query: "sortation associate", label: "sortation" },
  { base_query: "delivery station", label: "delivery" },
  { base_query: "package handler", label: "pkg-handler" },
  { base_query: "stower picker packer", label: "picker-packer" },
];

const LOCATIONS = ["Calgary", "Balzac", "Airdrie"];

function buildUrl(query, location) {
  const p = new URLSearchParams({
    base_query: query.base_query,
    loc_query: location,
    radius: "80",
    page: "1",
    size: "20",
  });
  return `${API_BASE}?${p.toString()}`;
}

// ─── Retry with exponential backoff ──────────────────────────────────────────

async function fetchWithRetry(url, label, retries = 3) {
  let lastError;

  for (let attempt = 1; attempt <= retries; attempt++) {
    try {
      const { status, body } = await httpGet(url, {
        headers: { Accept: "application/json" },
        timeout: 12000,
      });

      if (status === 429) {
        // Rate limited — back off longer
        const delay = 2000 * attempt;
        log.warn(`[API:${label}] Rate limited (429), waiting ${delay}ms...`);
        await sleep(delay);
        continue;
      }

      if (status !== 200) {
        log.warn(`[API:${label}] HTTP ${status}`);
        return null;
      }

      let data;
      try {
        data = JSON.parse(body);
      } catch {
        log.warn(`[API:${label}] Response is not JSON`);
        return null;
      }

      return data;
    } catch (err) {
      lastError = err;
      const delay = 1000 * Math.pow(2, attempt - 1);
      log.warn(
        `[API:${label}] Attempt ${attempt}/${retries} failed: ${err.message} — retrying in ${delay}ms`
      );
      await sleep(delay);
    }
  }

  log.error(`[API:${label}] All ${retries} attempts failed: ${lastError?.message}`);
  return null;
}

// ─── Parse API response ───────────────────────────────────────────────────────

function parseApiResponse(data, label) {
  const raw =
    data?.jobs ?? data?.results ?? data?.data?.jobs ?? data?.jobResults ?? data?.items ?? [];

  if (!Array.isArray(raw)) {
    log.warn(`[API:${label}] Unexpected response shape`);
    return [];
  }

  return raw;
}

// ─── Main export ──────────────────────────────────────────────────────────────

/**
 * Fetch all queries × all locations in parallel.
 * Returns deduplicated array of normalized job objects.
 */
export async function fetchJobsViaApi() {
  const tasks = [];
  for (const query of QUERIES) {
    for (const location of LOCATIONS) {
      tasks.push({ query, location, url: buildUrl(query, location) });
    }
  }

  log.info(`[API] Running ${tasks.length} parallel requests...`);

  const results = await Promise.allSettled(
    tasks.map(({ query, location, url }) => fetchWithRetry(url, `${query.label}/${location}`))
  );

  const seen = new Set();
  const allJobs = [];

  results.forEach((result, i) => {
    if (result.status === "rejected") return;

    const data = result.value;
    if (!data) return;

    const label = `${tasks[i].query.label}/${tasks[i].location}`;
    const raw = parseApiResponse(data, label);

    log.info(`[API:${label}] → ${raw.length} items`);

    for (const item of raw) {
      const job = normalizeApiJob(item);
      if (!job || seen.has(job.id)) continue;
      seen.add(job.id);
      allJobs.push(job);
    }
  });

  log.info(`[API] Total unique jobs: ${allJobs.length}`);
  return allJobs;
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}
