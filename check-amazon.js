import { chromium } from "playwright";
import fs from "fs/promises";
import https from "https";
import path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const TELEGRAM_BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN;
const TELEGRAM_CHAT_ID = process.env.TELEGRAM_CHAT_ID;

const SEARCH_URL =
  "https://hiring.amazon.ca/search/warehouse-jobs?base_query=&loc_query=Calgary";
const HISTORY_FILE = path.join(__dirname, "logs", "check-history.json");
const MAX_HISTORY_ENTRIES = 100;
const NO_JOBS_TEXT = "sorry, there are no jobs available that match your search.";
const BLOCKED_PAGE_TEXT = "the request could not be satisfied";
const RETRY_DELAY_MS = 5000;

function requireEnv(name, value) {
  if (!value) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
}

function sendTelegram(message) {
  const url =
    `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage` +
    `?chat_id=${TELEGRAM_CHAT_ID}&text=${encodeURIComponent(message)}`;

  return new Promise((resolve, reject) => {
    const request = https.get(url, (response) => {
      let body = "";

      response.on("data", (chunk) => {
        body += chunk;
      });

      response.on("end", () => {
        if (response.statusCode !== 200) {
          reject(
            new Error(
              `Telegram API returned ${response.statusCode}: ${body || "no body"}`
            )
          );
          return;
        }

        resolve();
      });
    });

    request.on("error", reject);
  });
}

async function readHistory() {
  try {
    const raw = await fs.readFile(HISTORY_FILE, "utf8");
    const parsed = JSON.parse(raw);

    return {
      seenJobKeys: Array.isArray(parsed.seenJobKeys) ? parsed.seenJobKeys : [],
      checks: Array.isArray(parsed.checks) ? parsed.checks : [],
    };
  } catch (error) {
    if (error.code === "ENOENT") {
      return { seenJobKeys: [], checks: [] };
    }

    throw error;
  }
}

async function writeHistory(history) {
  await fs.mkdir(path.dirname(HISTORY_FILE), { recursive: true });
  await fs.writeFile(HISTORY_FILE, JSON.stringify(history, null, 2) + "\n");
}

async function extractJobs(page) {
  await page.waitForLoadState("networkidle", { timeout: 20000 });

  return page.evaluate(() => {
    const anchors = Array.from(document.querySelectorAll("a[href]"));

    const jobs = anchors
      .map((anchor) => {
        const href = anchor.href || "";
        const text = anchor.textContent?.replace(/\s+/g, " ").trim() || "";
        const cardText =
          anchor.closest("article, li, div")?.textContent
            ?.replace(/\s+/g, " ")
            .trim() || "";
        const combinedText = `${text} ${cardText}`.trim();

        if (!href.includes("amazon") && !href.includes("/job")) {
          return null;
        }

        if (!combinedText) {
          return null;
        }

        return {
          key: href,
          url: href,
          title: text || combinedText.slice(0, 160),
          text: combinedText,
        };
      })
      .filter(Boolean);

    const uniqueJobs = new Map();

    for (const job of jobs) {
      const key = job.url || job.title;

      if (!uniqueJobs.has(key)) {
        uniqueJobs.set(key, job);
      }
    }

    return Array.from(uniqueJobs.values());
  });
}

function filterRelevantJobs(jobs) {
  return jobs.filter((job) => {
    const haystack = `${job.title} ${job.text} ${job.url}`.toLowerCase();
    return haystack.includes("calgary");
  });
}

async function readBodyText(page) {
  return (await page.locator("body").innerText()).replace(/\s+/g, " ").trim();
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function getJobKey(job) {
  const jobIdMatch = job.url.match(/\/jobs\/([^/?#]+)/i);
  if (jobIdMatch) {
    return jobIdMatch[1];
  }

  return job.url || job.title;
}

async function loadSearchPage(page) {
  const response = await page.goto(SEARCH_URL, {
    waitUntil: "domcontentloaded",
    timeout: 30000,
  });

  if (!response || !response.ok()) {
    throw new Error(
      `Amazon search page failed to load: ${response?.status() ?? "no response"}`
    );
  }

  const bodyText = (await readBodyText(page)).toLowerCase();

  if (bodyText.includes(BLOCKED_PAGE_TEXT)) {
    throw new Error("Amazon search page was blocked by CloudFront");
  }

  return bodyText;
}

async function loadSearchPageWithRetry(page) {
  let lastError;

  for (let attempt = 1; attempt <= 2; attempt += 1) {
    try {
      return await loadSearchPage(page);
    } catch (error) {
      lastError = error;

      const isRetryable =
        error.message.includes("CloudFront") ||
        error.message.includes("timeout") ||
        error.message.includes("failed to load");

      if (!isRetryable || attempt === 2) {
        throw error;
      }

      await sleep(RETRY_DELAY_MS);
    }
  }

  throw lastError;
}

function buildCheckEntry(status, details = {}) {
  return {
    checkedAt: new Date().toISOString(),
    status,
    ...details,
  };
}

async function checkAmazon() {
  requireEnv("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN);
  requireEnv("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID);

  const history = await readHistory();
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    userAgent:
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " +
      "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    locale: "en-CA",
    timezoneId: "America/Edmonton",
    viewport: { width: 1440, height: 900 },
  });
  const page = await context.newPage();

  try {
    const bodyText = await loadSearchPageWithRetry(page);

    if (bodyText.includes(NO_JOBS_TEXT)) {
      history.checks = [
        buildCheckEntry("no-jobs", {
          jobsFound: 0,
          newJobsFound: 0,
          foundJobKeys: [],
        }),
        ...history.checks,
      ].slice(0, MAX_HISTORY_ENTRIES);

      await writeHistory(history);
      return;
    }

    const jobs = filterRelevantJobs(await extractJobs(page)).map((job) => ({
      ...job,
      key: getJobKey(job),
    }));
    const currentJobKeys = jobs.map((job) => job.key);
    const newJobs = jobs.filter((job) => {
      return !history.seenJobKeys.includes(job.key);
    });

    if (newJobs.length > 0) {
      const messageLines = [
        `Amazon Calgary jobs found: ${newJobs.length}`,
        ...newJobs.slice(0, 5).map((job) => `- ${job.title}\n${job.url}`),
      ];

      await sendTelegram(messageLines.join("\n"));
      history.seenJobKeys = Array.from(
        new Set([
          ...history.seenJobKeys,
          ...newJobs.map((job) => job.key),
        ])
      );
    }

    history.checks = [
      buildCheckEntry(newJobs.length > 0 ? "notified" : "monitoring", {
        jobsFound: jobs.length,
        newJobsFound: newJobs.length,
        foundJobKeys: currentJobKeys,
        notifiedJobKeys: newJobs.map((job) => job.key),
      }),
      ...history.checks,
    ].slice(0, MAX_HISTORY_ENTRIES);

    await writeHistory(history);
  } catch (error) {
    history.checks = [
      buildCheckEntry("error", { message: error.message }),
      ...history.checks,
    ].slice(0, MAX_HISTORY_ENTRIES);
    await writeHistory(history);
    throw error;
  } finally {
    await context.close();
    await browser.close();
  }
}

checkAmazon().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
