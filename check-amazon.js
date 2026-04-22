import { chromium } from "playwright";
import fs from "fs/promises";
import https from "https";
import http from "http";
import crypto from "crypto";
import path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const TELEGRAM_BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN;
const TELEGRAM_CHAT_ID = process.env.TELEGRAM_CHAT_ID;
const PUSHOVER_TOKEN = process.env.PUSHOVER_TOKEN;
const PUSHOVER_USER = process.env.PUSHOVER_USER;
const DRY_RUN = /^(1|true|yes)$/i.test(process.env.DRY_RUN || "");

const API_ENDPOINTS = [
  "https://hiring.amazon.ca/api/v1/search?base_query=warehouse&loc_query=Calgary&radius=50&page=1&size=20",
  "https://hiring.amazon.ca/api/v1/search?base_query=fulfillment+associate&loc_query=Calgary&radius=50&page=1&size=20",
];

const SEARCH_URL =
  "https://hiring.amazon.ca/search/warehouse-jobs?base_query=&loc_query=Calgary";
const HISTORY_FILE = path.join(__dirname, "logs", "check-history.json");
const MAX_HISTORY_ENTRIES = 100;
const SEEN_JOB_TTL_DAYS = 45;
const RETRY_DELAY_MS = 6000;
const CLOUDFRONT_BLOCK = "the request could not be satisfied";
const NO_JOBS_TEXT = "sorry, there are no jobs available that match your search";

const LOCATION_WHITELIST = [
  "calgary",
  "balzac",
  "airdrie",
  "crossiron",
  "rocky view",
  "ab ",
  "alberta",
  "t3z",
  "t4b",
];

const JOB_WHITELIST = [
  "warehouse",
  "fulfillment",
  "sort",
  "picker",
  "packer",
  "stower",
  "receiver",
  "associate",
  "operator",
  "yard",
  "dock",
  "delivery station",
  "fc associate",
  "package handler",
  "material handler",
];

const JOB_BLACKLIST = [
  "software",
  "engineer",
  "developer",
  "manager",
  "hr ",
  "human resource",
  "recruiter",
  "analyst",
  "finance",
  "legal",
  "marketing",
  "data scientist",
  "product manager",
  "aws",
  "senior",
  "principal",
  "director",
  "intern",
  "corporate",
];

function requireEnv(name, value) {
  if (!value) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function stableJobId(job) {
  const patterns = [
    /\/jobs\/(\d+)/i,
    /jobId[=:](\d+)/i,
    /requisitionId[=:](\d+)/i,
    /[?&]id=(\d+)/i,
    /\/(\d{8,})/,
  ];

  for (const pattern of patterns) {
    const match = (job.url || "").match(pattern);
    if (match) {
      return `amzn_${match[1]}`;
    }
  }

  const raw = (job.title || "").toLowerCase().trim();
  return `amzn_${crypto.createHash("sha1").update(raw).digest("hex").slice(0, 12)}`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function httpGet(url, options = {}) {
  return new Promise((resolve, reject) => {
    const lib = url.startsWith("https") ? https : http;
    const parsed = new URL(url);
    const request = lib.request(
      {
        hostname: parsed.hostname,
        path: parsed.pathname + parsed.search,
        method: "GET",
        headers: {
          "User-Agent":
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " +
            "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
          Accept: "application/json, text/html, */*",
          "Accept-Language": "en-CA,en;q=0.9",
          "Cache-Control": "no-cache",
          ...options.headers,
        },
        timeout: 15000,
      },
      (response) => {
        let body = "";

        response.on("data", (chunk) => {
          body += chunk;
        });

        response.on("end", () => {
          resolve({
            status: response.statusCode,
            body,
            headers: response.headers,
          });
        });
      }
    );

    request.on("timeout", () => {
      request.destroy();
      reject(new Error("Request timeout"));
    });
    request.on("error", reject);
    request.end();
  });
}

async function sendTelegram(message) {
  if (!TELEGRAM_BOT_TOKEN || !TELEGRAM_CHAT_ID) {
    return;
  }

  const url =
    `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage` +
    `?chat_id=${encodeURIComponent(TELEGRAM_CHAT_ID)}` +
    `&text=${encodeURIComponent(message)}` +
    `&parse_mode=HTML` +
    `&disable_web_page_preview=1`;

  const { status, body } = await httpGet(url);
  if (status !== 200) {
    throw new Error(`Telegram API returned ${status}: ${body || "no body"}`);
  }
}

async function sendPushover(title, message, priority = 1) {
  if (!PUSHOVER_TOKEN || !PUSHOVER_USER) {
    return;
  }

  const payload = new URLSearchParams({
    token: PUSHOVER_TOKEN,
    user: PUSHOVER_USER,
    title,
    message,
    priority: String(priority),
    sound: "siren",
    html: "1",
  });

  if (priority >= 2) {
    payload.set("retry", "60");
    payload.set("expire", "3600");
  }

  await new Promise((resolve, reject) => {
    const request = https.request(
      {
        hostname: "api.pushover.net",
        path: "/1/messages.json",
        method: "POST",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded",
          "Content-Length": Buffer.byteLength(payload.toString()),
        },
      },
      (response) => {
        let body = "";

        response.on("data", (chunk) => {
          body += chunk;
        });

        response.on("end", () => {
          if (response.statusCode !== 200) {
            reject(new Error(`Pushover ${response.statusCode}: ${body}`));
            return;
          }

          resolve();
        });
      }
    );

    request.on("error", reject);
    request.write(payload.toString());
    request.end();
  });
}

async function notifyNewJobs(newJobs) {
  const count = newJobs.length;
  const jobList = newJobs
    .slice(0, 5)
    .map(
      (job) =>
        `• ${escapeHtml(job.title)} — ${escapeHtml(job.city || "Calgary area")}\n${job.url}`
    )
    .join("\n");

  const telegramMessage =
    `🚨 <b>Amazon Calgary: ${count} new job${count > 1 ? "s" : ""}</b>\n\n` +
    `${jobList}\n\n🔗 ${SEARCH_URL}`;
  const pushoverTitle = `Amazon: ${count} warehouse job${count > 1 ? "s" : ""}`;
  const pushoverMessage =
    newJobs.slice(0, 3).map((job) => `• ${job.title}`).join("\n") +
    "\n\nCalgary/Balzac area";

  if (DRY_RUN) {
    console.log(`[DRY_RUN] Would send Telegram alert for ${count} new job(s)`);
    console.log(`[DRY_RUN] Telegram preview:\n${telegramMessage}`);
    if (PUSHOVER_TOKEN && PUSHOVER_USER) {
      console.log(`[DRY_RUN] Would send Pushover alert: ${pushoverTitle}`);
      console.log(`[DRY_RUN] Pushover preview:\n${pushoverMessage}`);
    } else {
      console.log("[DRY_RUN] Pushover is not configured; skipping preview");
    }
    return;
  }

  const [telegramResult, pushoverResult] = await Promise.allSettled([
    sendTelegram(telegramMessage),
    sendPushover(pushoverTitle, pushoverMessage, 0),
  ]);

  if (telegramResult.status === "rejected") {
    throw telegramResult.reason;
  }

  if (pushoverResult.status === "rejected") {
    console.warn(`[Pushover] ${pushoverResult.reason.message}`);
  }
}

async function readHistory() {
  try {
    const raw = await fs.readFile(HISTORY_FILE, "utf8");
    const parsed = JSON.parse(raw);
    let seenJobs = [];

    if (Array.isArray(parsed.seenJobs)) {
      seenJobs = parsed.seenJobs
        .filter((entry) => entry && typeof entry.id === "string")
        .map((entry) => ({
          id: entry.id,
          seenAt:
            typeof entry.seenAt === "string"
              ? entry.seenAt
              : new Date().toISOString(),
        }));
    } else if (Array.isArray(parsed.seenJobIds)) {
      seenJobs = parsed.seenJobIds.map((id) => ({
        id,
        seenAt: new Date().toISOString(),
      }));
    } else if (Array.isArray(parsed.seenJobKeys)) {
      seenJobs = parsed.seenJobKeys.map((id) => ({
        id,
        seenAt: new Date().toISOString(),
      }));
    }

    return {
      seenJobs,
      checks: Array.isArray(parsed.checks) ? parsed.checks : [],
    };
  } catch (error) {
    if (error.code === "ENOENT") {
      return { seenJobs: [], checks: [] };
    }

    throw error;
  }
}

async function writeHistory(history) {
  await fs.mkdir(path.dirname(HISTORY_FILE), { recursive: true });
  await fs.writeFile(HISTORY_FILE, JSON.stringify(history, null, 2) + "\n");
}

async function writeHistoryIfNeeded(history) {
  if (DRY_RUN) {
    console.log("[DRY_RUN] Skipping history write");
    return;
  }

  await writeHistory(history);
}

function isRelevantJob(job) {
  const titleAndDesc = `${job.title || ""} ${job.description || ""}`.toLowerCase();
  const locationHaystack =
    `${titleAndDesc} ${job.city || ""} ${job.location || ""} ${job.url || ""}`.toLowerCase();

  const hasLocation = LOCATION_WHITELIST.some((term) =>
    locationHaystack.includes(term)
  );
  if (!hasLocation) {
    return false;
  }

  const hasJobType = JOB_WHITELIST.some((term) => titleAndDesc.includes(term));
  if (!hasJobType) {
    return false;
  }

  return !JOB_BLACKLIST.some((term) => titleAndDesc.includes(term));
}

function normalizeApiJob(item) {
  const title = item.title ?? item.jobTitle ?? item.position ?? "";
  const url = item.applyUrl ?? item.url ?? item.jobUrl ?? item.link ?? "";
  const city = item.city ?? item.locationName ?? item.location ?? "";
  const jobId = String(item.jobId ?? item.id ?? item.requisitionId ?? "");
  const description = item.description ?? item.shortDescription ?? "";

  if (!title) {
    return null;
  }

  const normalizedUrl = url.startsWith("http")
    ? url
    : `https://hiring.amazon.ca${url.startsWith("/") ? "" : "/"}${url}`;

  return {
    id: jobId ? `amzn_${jobId}` : stableJobId({ url: normalizedUrl, title, city }),
    title,
    url: normalizedUrl,
    city,
    description,
    source: "api",
  };
}

function normalizeDomJob(item) {
  return {
    ...item,
    id: stableJobId(item),
  };
}

function dedupeJobs(jobs) {
  const uniqueJobs = new Map();

  for (const job of jobs) {
    const key = job.id || job.url || `${job.title}_${job.city || ""}`;
    if (!uniqueJobs.has(key)) {
      uniqueJobs.set(key, job);
    }
  }

  return Array.from(uniqueJobs.values());
}

async function fetchJobsViaApi() {
  const allJobs = [];

  for (const endpoint of API_ENDPOINTS) {
    try {
      const { status, body } = await httpGet(endpoint, {
        headers: { Accept: "application/json" },
      });

      if (status !== 200) {
        continue;
      }

      let data;
      try {
        data = JSON.parse(body);
      } catch {
        continue;
      }

      const rawJobs = data?.jobs ?? data?.results ?? data?.data?.jobs ?? [];
      if (!Array.isArray(rawJobs) || rawJobs.length === 0) {
        continue;
      }

      for (const item of rawJobs) {
        const job = normalizeApiJob(item);
        if (job) {
          allJobs.push(job);
        }
      }
    } catch (error) {
      console.warn(`[API] Endpoint failed: ${endpoint} :: ${error.message}`);
    }
  }

  return dedupeJobs(allJobs);
}

async function fetchJobsViaBrowser() {
  const browser = await chromium.launch({
    headless: true,
    args: [
      "--no-sandbox",
      "--disable-setuid-sandbox",
      "--disable-blink-features=AutomationControlled",
    ],
  });
  const context = await browser.newContext({
    userAgent:
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " +
      "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    locale: "en-CA",
    timezoneId: "America/Edmonton",
    viewport: { width: 1440, height: 900 },
    extraHTTPHeaders: {
      "Accept-Language": "en-CA,en;q=0.9",
    },
  });
  await context.addInitScript(() => {
    Object.defineProperty(navigator, "webdriver", { get: () => undefined });
  });
  const page = await context.newPage();

  try {
    for (let attempt = 1; attempt <= 2; attempt += 1) {
      try {
        const response = await page.goto(SEARCH_URL, {
          waitUntil: "domcontentloaded",
          timeout: 30000,
        });

        if (!response?.ok()) {
          throw new Error(`Page load failed: ${response?.status() ?? "no response"}`);
        }

        const bodyText = (await page.locator("body").innerText())
          .replace(/\s+/g, " ")
          .trim()
          .toLowerCase();

        if (bodyText.includes(CLOUDFRONT_BLOCK)) {
          throw new Error("Blocked by CloudFront");
        }

        if (bodyText.includes(NO_JOBS_TEXT)) {
          return [];
        }

        await page.waitForLoadState("networkidle", { timeout: 15000 }).catch(() => {});
        return dedupeJobs((await extractJobsFromDom(page)).map(normalizeDomJob));
      } catch (error) {
        if (attempt === 2) {
          throw error;
        }

        await sleep(RETRY_DELAY_MS);
      }
    }
  } finally {
    try {
      await context.close();
    } finally {
      await browser.close();
    }
  }

  return [];
}

async function extractJobsFromDom(page) {
  return page.evaluate(() => {
    const jobs = [];
    const selectors = [
      "[data-test='job-list'] article",
      "[data-test='job-list'] li",
      ".job-card",
      ".job-tile",
      "article[class*='job']",
      "li[class*='job']",
    ];

    let cards = [];
    for (const selector of selectors) {
      cards = Array.from(document.querySelectorAll(selector));
      if (cards.length > 0) {
        break;
      }
    }

    if (cards.length === 0) {
      const jobLinks = Array.from(document.querySelectorAll("a[href*='/jobs/']")).filter(
        (anchor) => /\/jobs\/\d+/.test(anchor.href)
      );

      for (const anchor of jobLinks) {
        const container =
          anchor.closest("article, li, [class*='card'], [class*='tile']") ??
          anchor.parentElement;
        const card = container ?? anchor;

        if (!cards.includes(card)) {
          cards.push(card);
        }
      }
    }

    const seenUrls = new Set();
    for (const card of cards) {
      const anchor =
        card.tagName === "A"
          ? card
          : card.querySelector("a[href*='/jobs/'], a[href*='hiring.amazon']");
      if (!anchor) {
        continue;
      }

      const url = anchor.href || "";
      if (!url || seenUrls.has(url)) {
        continue;
      }
      seenUrls.add(url);

      const titleElement = card.querySelector(
        "h1, h2, h3, h4, [class*='title'], [class*='heading']"
      );
      const title = (titleElement ?? anchor).textContent?.replace(/\s+/g, " ").trim() || "";
      const locationElement = card.querySelector(
        "[class*='location'], [class*='city'], address, [data-test*='location']"
      );
      const city = locationElement?.textContent?.replace(/\s+/g, " ").trim() || "";
      const description = card.textContent?.replace(/\s+/g, " ").trim().slice(0, 300) || "";

      if (!title && !url) {
        continue;
      }

      jobs.push({
        title,
        url,
        city,
        description,
        source: "dom",
      });
    }

    return jobs;
  });
}

async function checkAmazon() {
  if (!DRY_RUN) {
    requireEnv("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN);
    requireEnv("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID);
  } else {
    console.log("[DRY_RUN] Active: notifications and history writes are disabled");
  }

  const history = await readHistory();
  const cutoffTime = new Date(
    Date.now() - SEEN_JOB_TTL_DAYS * 24 * 60 * 60 * 1000
  ).toISOString();
  history.seenJobs = history.seenJobs.filter((entry) => entry.seenAt > cutoffTime);
  let rawJobs = [];
  let strategy = "api";

  try {
    rawJobs = await fetchJobsViaApi();
    console.log(`[API] Fetched ${rawJobs.length} raw jobs`);
  } catch (error) {
    console.warn(`[API] Failed: ${error.message}`);
  }

  if (rawJobs.length === 0) {
    strategy = "playwright";
    console.log("[Playwright] Falling back to browser...");

    try {
      rawJobs = await fetchJobsViaBrowser();
      console.log(`[Playwright] Extracted ${rawJobs.length} raw jobs`);
    } catch (error) {
      history.checks = [
        {
          checkedAt: new Date().toISOString(),
          status: DRY_RUN ? "dry-run-error" : "error",
          strategy,
          message: error.message,
        },
        ...history.checks,
      ].slice(0, MAX_HISTORY_ENTRIES);
      await writeHistoryIfNeeded(history);
      throw error;
    }
  }

  const relevantJobs = dedupeJobs(rawJobs.filter(isRelevantJob));
  console.log(`[Filter] ${rawJobs.length} raw -> ${relevantJobs.length} relevant`);

  const currentIds = relevantJobs.map((job) => job.id);
  const seenIds = new Set(history.seenJobs.map((entry) => entry.id));
  const newJobs = relevantJobs.filter((job) => !seenIds.has(job.id));
  console.log(`[Dedup] ${relevantJobs.length} relevant -> ${newJobs.length} new`);

  if (newJobs.length > 0) {
    await notifyNewJobs(newJobs);
    if (!DRY_RUN) {
      const seenAt = new Date().toISOString();
      history.seenJobs = [
        ...history.seenJobs,
        ...newJobs.map((job) => ({
          id: job.id,
          seenAt,
        })),
      ];
    } else {
      console.log("[DRY_RUN] Skipping seen-job updates");
    }
  }

  history.checks = [
    {
      checkedAt: new Date().toISOString(),
      status: DRY_RUN
        ? newJobs.length > 0
          ? "dry-run-notify"
          : "dry-run-monitoring"
        : newJobs.length > 0
          ? "notified"
          : "monitoring",
      strategy,
      rawJobsCount: rawJobs.length,
      relevantCount: relevantJobs.length,
      newJobsCount: newJobs.length,
      currentIds,
      notifiedIds: newJobs.map((job) => job.id),
      dryRun: DRY_RUN,
    },
    ...history.checks,
  ].slice(0, MAX_HISTORY_ENTRIES);

  await writeHistoryIfNeeded(history);
}

checkAmazon().catch((error) => {
  console.error("[FATAL]", error);
  process.exitCode = 1;
});
