/**
 * checker/notify.js
 * Notifications: Telegram (HTML) + Pushover (Apple Watch siren)
 * Hard cap: max 5 job alerts per run to prevent spam
 */

import { httpGet, httpPost } from "./http.js";
import { log } from "./logger.js";

const SEARCH_URL = "https://hiring.amazon.ca/app#/jobSearch";
const MAX_ALERTS = 5;

// ─── Telegram ─────────────────────────────────────────────────────────────────

export async function sendTelegram(message) {
  const token = process.env.TELEGRAM_BOT_TOKEN;
  const chatId = process.env.TELEGRAM_CHAT_ID;

  if (!token || !chatId) {
    log.warn("[Telegram] Skipped — credentials missing");
    return;
  }

  const url =
    `https://api.telegram.org/bot${token}/sendMessage` +
    `?chat_id=${encodeURIComponent(chatId)}` +
    `&text=${encodeURIComponent(message)}` +
    `&parse_mode=HTML` +
    `&disable_web_page_preview=1`;

  const { status, body } = await httpGet(url);

  if (status !== 200) {
    throw new Error(`Telegram API ${status}: ${body}`);
  }

  log.info("[Telegram] Sent ✓");
}

// ─── Pushover ─────────────────────────────────────────────────────────────────

/**
 * Pushover priority:
 *  -1 = quiet
 *   0 = normal
 *   1 = high (bypasses DND, sounds on Apple Watch immediately)
 *   2 = emergency (repeats every `retry` seconds until acknowledged)
 */
export async function sendPushover(title, message, priority = 1) {
  const token = process.env.PUSHOVER_TOKEN;
  const user = process.env.PUSHOVER_USER;

  if (!token || !user) {
    log.warn("[Pushover] Skipped — credentials missing");
    return;
  }

  const payload = {
    token,
    user,
    title,
    message,
    priority,
    sound: "siren",
    html: 1,
    ...(priority >= 2 ? { retry: 60, expire: 3600 } : {}),
  };

  const { status, body } = await httpPost("https://api.pushover.net/1/messages.json", payload);

  if (status !== 200) {
    throw new Error(`Pushover API ${status}: ${body}`);
  }

  log.info("[Pushover] Sent ✓");
}

// ─── Main notify ──────────────────────────────────────────────────────────────

export async function notifyNewJobs(newJobs) {
  const jobs = newJobs.slice(0, MAX_ALERTS);
  const count = newJobs.length;
  const shown = jobs.length;
  const more = count - shown;

  const jobLines = jobs
    .map((j) => {
      const city = j.city ? ` — ${j.city}` : "";
      return `• <b>${escapeHtml(j.title)}</b>${city}\n  <a href="${j.url}">${j.url}</a>`;
    })
    .join("\n\n");

  const tail = more > 0 ? `\n\n<i>...and ${more} more</i>` : "";

  const telegramMsg = [
    `🚨 <b>Amazon Calgary: ${count} new warehouse job${count > 1 ? "s" : ""}</b>`,
    "",
    jobLines,
    tail,
    "",
    `🔗 <a href="${SEARCH_URL}">View all jobs</a>`,
  ].join("\n");

  const pushTitle = `🚨 Amazon: ${count} warehouse job${count > 1 ? "s" : ""}`;
  const pushLines = jobs.slice(0, 3).map((j) => `• ${j.title}`).join("\n");
  const pushMsg = `${pushLines}${more > 0 ? `\n...+${more} more` : ""}\n\nCalgary / Balzac`;

  const results = await Promise.allSettled([
    sendTelegram(telegramMsg),
    sendPushover(pushTitle, pushMsg, 1),
  ]);

  for (const r of results) {
    if (r.status === "rejected") {
      log.error("[Notify] Channel error:", r.reason?.message);
    }
  }
}

function escapeHtml(str) {
  return (str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}
