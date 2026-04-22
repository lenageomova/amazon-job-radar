/**
 * check-amazon.js
 * Amazon Calgary Warehouse Job Radar — main orchestrator
 *
 * Strategy:
 *   1. Parallel API calls (fast, no browser)
 *   2. Playwright fallback if API returns nothing
 *   3. Three-layer filter (location → job type → blacklist)
 *   4. Stable dedup (ID survives URL changes)
 *   5. Telegram + Pushover (Apple Watch siren), max 5 alerts/run
 */

import { fetchJobsViaApi } from "./checker/api.js";
import { fetchJobsViaBrowser } from "./checker/browser.js";
import { isRelevantJob, filterReason } from "./checker/filter.js";
import { notifyNewJobs } from "./checker/notify.js";
import { readHistory, writeHistory, buildCheckEntry } from "./checker/history.js";
import { log } from "./checker/logger.js";

function requireEnv(name) {
  if (!process.env[name]) throw new Error(`Missing required env var: ${name}`);
}

async function checkAmazon() {
  requireEnv("TELEGRAM_BOT_TOKEN");
  requireEnv("TELEGRAM_CHAT_ID");

  log.info("━━━ Amazon Job Radar — check started ━━━");

  const history = await readHistory();
  let rawJobs = [];
  let strategy = "api";

  // Step 1: Parallel API (fast, no browser)
  try {
    rawJobs = await fetchJobsViaApi();
    log.info(`[API] Raw jobs fetched: ${rawJobs.length}`);
  } catch (err) {
    log.error(`[API] Fatal error: ${err.message}`);
  }

  // Step 2: Playwright fallback
  if (rawJobs.length === 0) {
    strategy = "playwright";
    log.info("[Playwright] API returned nothing — launching browser fallback...");

    try {
      rawJobs = await fetchJobsViaBrowser();
      log.info(`[Playwright] Raw jobs fetched: ${rawJobs.length}`);
    } catch (err) {
      log.error(`[Playwright] Failed: ${err.message}`);
      await writeHistory({
        ...history,
        checks: [buildCheckEntry("error", { strategy, message: err.message }), ...history.checks],
      });
      throw err;
    }
  }

  // Step 3: Filter
  const relevantJobs = [];
  let filteredOut = 0;

  for (const job of rawJobs) {
    if (isRelevantJob(job)) {
      relevantJobs.push(job);
    } else {
      filteredOut++;
      log.debug(`[Filter] ✗ "${job.title}" — ${filterReason(job)}`);
    }
  }

  log.info(
    `[Filter] ${rawJobs.length} raw → ${relevantJobs.length} relevant (${filteredOut} filtered)`
  );

  // Step 4: Dedup
  const seenSet = new Set(history.seenJobIds);
  const newJobs = relevantJobs.filter((j) => !seenSet.has(j.id));
  const currentIds = relevantJobs.map((j) => j.id);

  log.info(`[Dedup] ${relevantJobs.length} relevant → ${newJobs.length} new`);

  if (newJobs.length > 0) {
    for (const j of newJobs) log.info(`  + ${j.id} | ${j.title} | ${j.city || "—"}`);
  }

  // Step 5: Notify
  if (newJobs.length > 0) {
    await notifyNewJobs(newJobs);
    history.seenJobIds = Array.from(
      new Set([...history.seenJobIds, ...newJobs.map((j) => j.id)])
    );
  }

  // Step 6: Save history
  history.checks = [
    buildCheckEntry(newJobs.length > 0 ? "notified" : "monitoring", {
      strategy,
      rawCount: rawJobs.length,
      relevantCount: relevantJobs.length,
      newCount: newJobs.length,
      currentIds,
      notifiedIds: newJobs.map((j) => j.id),
    }),
    ...history.checks,
  ];

  await writeHistory(history);
  log.info(`━━━ Done — ${newJobs.length > 0 ? "🔔 notified" : "✅ monitoring"} ━━━`);
}

checkAmazon().catch((err) => {
  log.error("[FATAL]", err.message);
  process.exitCode = 1;
});
