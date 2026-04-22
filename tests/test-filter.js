/**
 * tests/test-filter.js — filter + stableJobId unit tests
 * Run: node tests/test-filter.js
 */

import { isRelevantJob, filterReason } from "../checker/filter.js";
import { stableJobId } from "../checker/normalize.js";

let passed = 0;
let failed = 0;

function test(name, condition, extra = "") {
  if (condition) {
    console.log(`  ✅  ${name}`);
    passed++;
  } else {
    console.log(`  ❌  ${name}${extra ? "  ← " + extra : ""}`);
    failed++;
  }
}

// ── isRelevantJob ─────────────────────────────────────────────────────────────

console.log("\n── isRelevantJob: should PASS ───────────────────────────────────");

test(
  "Warehouse Associate Calgary",
  isRelevantJob({
    title: "Warehouse Associate",
    city: "Calgary, AB",
    url: "https://hiring.amazon.ca/app#/jobDetail?jobId=JOB-CA-0000000441",
  })
);

test(
  "Fulfillment Center Associate Balzac",
  isRelevantJob({
    title: "Fulfillment Center Associate",
    city: "Balzac, AB",
    url: "https://hiring.amazon.ca/app#/jobDetail?jobId=JOB-CA-0000000999",
  })
);

test(
  "Picker/Packer Calgary",
  isRelevantJob({
    title: "Picker Packer",
    city: "Calgary",
    url: "https://hiring.amazon.ca/jobs/11111111",
  })
);

test(
  "Package Handler Alberta (in description)",
  isRelevantJob({
    title: "Package Handler",
    description: "Work location: Alberta",
    url: "https://hiring.amazon.ca/jobs/66666666",
  })
);

test(
  "Sortation Associate Airdrie",
  isRelevantJob({
    title: "Sortation Associate",
    city: "Airdrie, AB",
    url: "https://hiring.amazon.ca/jobs/77777777",
  })
);

test(
  "Delivery Station Associate Calgary",
  isRelevantJob({
    title: "Delivery Station Associate",
    city: "Calgary",
    url: "https://hiring.amazon.ca/jobs/88888888",
  })
);

console.log("\n── isRelevantJob: should FAIL ───────────────────────────────────");

test(
  "Software Engineer Calgary → FAIL",
  !isRelevantJob({
    title: "Software Engineer II",
    city: "Calgary",
    url: "https://hiring.amazon.ca/jobs/22222222",
  }),
  filterReason({ title: "Software Engineer II", city: "Calgary", url: "" })
);

test(
  "Warehouse Associate Toronto → FAIL (wrong city)",
  !isRelevantJob({
    title: "Warehouse Associate",
    city: "Toronto, ON",
    url: "https://hiring.amazon.ca/jobs/33333333",
  }),
  filterReason({ title: "Warehouse Associate", city: "Toronto, ON", url: "" })
);

test(
  "HR Manager Calgary → FAIL",
  !isRelevantJob({
    title: "HR Manager",
    city: "Calgary",
    url: "https://hiring.amazon.ca/jobs/44444444",
  }),
  filterReason({ title: "HR Manager", city: "Calgary", url: "" })
);

test(
  "Senior Operations Manager Calgary → FAIL",
  !isRelevantJob({ title: "Senior Operations Manager", city: "Calgary", url: "" }),
  filterReason({ title: "Senior Operations Manager", city: "Calgary", url: "" })
);

test(
  "Data Scientist Calgary → FAIL",
  !isRelevantJob({ title: "Data Scientist", city: "Calgary", url: "" }),
  filterReason({ title: "Data Scientist", city: "Calgary", url: "" })
);

test(
  "Director Calgary → FAIL",
  !isRelevantJob({ title: "Director of Operations", city: "Calgary", url: "" }),
  filterReason({ title: "Director of Operations", city: "Calgary", url: "" })
);

// ── stableJobId ───────────────────────────────────────────────────────────────

console.log("\n── stableJobId ──────────────────────────────────────────────────");

test(
  "Extracts JOB-CA format from URL",
  stableJobId({
    url: "https://hiring.amazon.ca/app#/jobDetail?jobId=JOB-CA-0000000441",
  }) === "amzn_JOB-CA-0000000441"
);

test(
  "Extracts numeric /jobs/ID from URL",
  stableJobId({ url: "https://hiring.amazon.ca/jobs/12345678", title: "Picker" }) ===
    "amzn_12345678"
);

test(
  "Same title+city = same ID regardless of UTM",
  (() => {
    const a = stableJobId({ url: "https://example.com?utm=abc", title: "Picker", city: "Calgary" });
    const b = stableJobId({ url: "https://example.com?utm=xyz", title: "Picker", city: "Calgary" });
    return a === b;
  })()
);

test(
  "Different title = different ID",
  stableJobId({ url: "", title: "Picker", city: "Calgary" }) !==
    stableJobId({ url: "", title: "Packer", city: "Calgary" })
);

test(
  "ID always starts with amzn_",
  stableJobId({ url: "", title: "Stower", city: "Balzac" }).startsWith("amzn_")
);

// ── Summary ───────────────────────────────────────────────────────────────────

console.log(`\n── Results: ${passed} passed, ${failed} failed ───────────────────────\n`);
if (failed > 0) process.exit(1);
