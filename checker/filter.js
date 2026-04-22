/**
 * checker/filter.js
 * Three-layer job relevance filter: location → job type → blacklist
 */

// ─── Location whitelist ───────────────────────────────────────────────────────
// At least ONE must match (case-insensitive substring)

export const LOCATION_WHITELIST = [
  "calgary",
  "balzac",
  "airdrie",
  "crossiron",
  "rocky view",
  " ab,",
  ", ab ",
  "alberta",
  "t3z",
  "t4b",
  "t1x",
  "t3n",
];

// ─── Job type whitelist ───────────────────────────────────────────────────────
// At least ONE must match

export const JOB_WHITELIST = [
  "warehouse",
  "fulfillment",
  "fulfillment center",
  "fc associate",
  "sort",
  "sortation",
  "picker",
  "packer",
  "stower",
  "receiver",
  "receiving",
  "associate",
  "operator",
  "yard",
  "dock",
  "delivery station",
  "package handler",
  "material handler",
  "inbound",
  "outbound",
  "robotics",
];

// ─── Job blacklist ────────────────────────────────────────────────────────────
// ANY match → excluded immediately

export const JOB_BLACKLIST = [
  "software engineer",
  "software developer",
  " sde ",
  "data engineer",
  "data scientist",
  "machine learning",
  " ml ",
  "product manager",
  " pm ",
  " hr ",
  "human resource",
  "recruiter",
  "talent acquisition",
  "finance",
  "legal",
  "compliance",
  "marketing",
  "aws ",
  " sde",
  "senior manager",
  "general manager",
  "operations manager",
  "site manager",
  "engineering manager",
  "principal engineer",
  "director",
  "vice president",
  " vp ",
  "intern",
  "co-op",
  "corporate",
  "business analyst",
  "financial analyst",
  "technical program",
  "program manager",
];

// ─── Filter function ──────────────────────────────────────────────────────────

/**
 * Returns true if the job is a relevant warehouse role in the target area.
 * All three layers must pass.
 */
export function isRelevantJob(job) {
  const haystack = [job.title, job.description, job.city, job.location, job.url]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();

  if (!LOCATION_WHITELIST.some((term) => haystack.includes(term))) {
    return false;
  }

  if (!JOB_WHITELIST.some((term) => haystack.includes(term))) {
    return false;
  }

  if (JOB_BLACKLIST.some((term) => haystack.includes(term))) {
    return false;
  }

  return true;
}

/**
 * Returns a reason string for why a job was filtered out (for debug logging).
 */
export function filterReason(job) {
  const haystack = [job.title, job.description, job.city, job.url]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();

  if (!LOCATION_WHITELIST.some((t) => haystack.includes(t))) return "location-mismatch";
  if (!JOB_WHITELIST.some((t) => haystack.includes(t))) return "not-warehouse-role";
  if (JOB_BLACKLIST.some((t) => haystack.includes(t))) {
    const hit = JOB_BLACKLIST.find((t) => haystack.includes(t));
    return `blacklisted:${hit}`;
  }
  return null;
}
