/**
 * checker/browser.js
 * Playwright fallback — anti-detect hardened
 */

import { chromium } from "playwright";
import { log } from "./logger.js";
import { stableJobId } from "./normalize.js";

const SEARCH_URL = "https://hiring.amazon.ca/app#/jobSearch";

const CLOUDFRONT_PHRASES = [
  "the request could not be satisfied",
  "request blocked",
  "error from cloudfront",
  "403 forbidden",
];

const NO_JOBS_PHRASES = [
  "sorry, there are no jobs",
  "no jobs available",
  "no results found",
  "0 jobs found",
];

// ─── Random helpers ───────────────────────────────────────────────────────────

const USER_AGENTS = [
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
];

const VIEWPORTS = [
  { width: 1440, height: 900 },
  { width: 1920, height: 1080 },
  { width: 1536, height: 864 },
  { width: 1280, height: 800 },
  { width: 1366, height: 768 },
];

function pick(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}

function jitter(base, spread = 200) {
  return base + Math.floor(Math.random() * spread);
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

// ─── Anti-detect init script ──────────────────────────────────────────────────

const STEALTH_SCRIPT = `
  // Remove webdriver flag
  Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

  // Fake plugins
  Object.defineProperty(navigator, 'plugins', {
    get: () => [1, 2, 3, 4, 5],
  });

  // Fake languages
  Object.defineProperty(navigator, 'languages', {
    get: () => ['en-CA', 'en', 'fr-CA'],
  });

  // Chrome runtime stub
  window.chrome = { runtime: {} };

  // Permissions stub
  const originalQuery = window.navigator.permissions?.query;
  if (originalQuery) {
    window.navigator.permissions.query = (parameters) =>
      parameters.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : originalQuery(parameters);
  }
`;

// ─── Scroll simulation ────────────────────────────────────────────────────────

async function simulateHumanScroll(page) {
  await page.evaluate(async () => {
    await new Promise((resolve) => {
      let totalHeight = 0;
      const distance = 120 + Math.floor(Math.random() * 80);
      const timer = setInterval(() => {
        window.scrollBy(0, distance);
        totalHeight += distance;
        if (totalHeight >= Math.min(document.body.scrollHeight, 2400)) {
          clearInterval(timer);
          resolve();
        }
      }, 120 + Math.floor(Math.random() * 80));
    });
  });
  await sleep(jitter(600, 400));
}

// ─── DOM extraction ───────────────────────────────────────────────────────────

async function extractJobsFromDom(page) {
  return page.evaluate(() => {
    const CARD_SELECTORS = [
      "[data-test='job-list'] article",
      "[data-test='job-list'] li",
      "[class*='JobCard']",
      "[class*='job-card']",
      "[class*='jobCard']",
      ".job-tile",
      "article[class*='job']",
      "li[class*='result']",
    ];

    let cards = [];
    for (const sel of CARD_SELECTORS) {
      cards = Array.from(document.querySelectorAll(sel));
      if (cards.length > 0) break;
    }

    if (cards.length === 0) {
      const jobLinks = Array.from(
        document.querySelectorAll("a[href*='jobDetail'], a[href*='jobId']")
      );
      for (const anchor of jobLinks) {
        const container =
          anchor.closest("article, li, section, div[class*='card']") ?? anchor.parentElement;
        if (container && !cards.includes(container)) cards.push(container);
      }
    }

    const seen = new Set();
    const jobs = [];

    for (const card of cards) {
      const anchor =
        card.tagName === "A"
          ? card
          : card.querySelector("a[href*='jobDetail'], a[href*='jobId'], a[href*='/jobs/']");

      if (!anchor) continue;

      const url = anchor.href || "";
      if (!url || seen.has(url)) continue;
      seen.add(url);

      const titleEl = card.querySelector(
        "h1,h2,h3,h4,[class*='title'],[class*='heading'],[class*='Title']"
      );
      const title = (titleEl ?? anchor).textContent?.replace(/\s+/g, " ").trim() || "";

      const locationEl = card.querySelector(
        "[class*='location'],[class*='Location'],[class*='city'],address"
      );
      const city = locationEl?.textContent?.replace(/\s+/g, " ").trim() || "";

      const description = card.textContent?.replace(/\s+/g, " ").trim().slice(0, 400) || "";

      if (!title && !url) continue;

      jobs.push({ title, url, city, description, source: "dom" });
    }

    return jobs;
  });
}

// ─── Main export ──────────────────────────────────────────────────────────────

export async function fetchJobsViaBrowser() {
  const ua = pick(USER_AGENTS);
  const viewport = pick(VIEWPORTS);

  log.info(`[Browser] UA: ${ua.slice(0, 60)}...`);
  log.info(`[Browser] Viewport: ${viewport.width}×${viewport.height}`);

  const browser = await chromium.launch({
    headless: true,
    args: [
      "--no-sandbox",
      "--disable-setuid-sandbox",
      "--disable-blink-features=AutomationControlled",
      "--disable-infobars",
      "--disable-dev-shm-usage",
    ],
  });

  const context = await browser.newContext({
    userAgent: ua,
    viewport,
    locale: "en-CA",
    timezoneId: "America/Edmonton",
    extraHTTPHeaders: { "Accept-Language": "en-CA,en;q=0.9,fr-CA;q=0.8" },
  });

  await context.addInitScript(STEALTH_SCRIPT);

  const page = await context.newPage();

  try {
    for (let attempt = 1; attempt <= 2; attempt++) {
      try {
        await sleep(jitter(800, 600));

        const response = await page.goto(SEARCH_URL, {
          waitUntil: "domcontentloaded",
          timeout: 35000,
        });

        if (!response?.ok()) {
          throw new Error(`HTTP ${response?.status() ?? "no response"}`);
        }

        const bodyText = (await page.locator("body").innerText())
          .replace(/\s+/g, " ")
          .trim()
          .toLowerCase();

        if (CLOUDFRONT_PHRASES.some((p) => bodyText.includes(p))) {
          throw new Error("Blocked by CloudFront");
        }

        if (NO_JOBS_PHRASES.some((p) => bodyText.includes(p))) {
          log.info("[Browser] Amazon says: no jobs available");
          return [];
        }

        await page.waitForLoadState("networkidle", { timeout: 20000 }).catch(() => {});
        await sleep(jitter(1200, 800));

        await simulateHumanScroll(page);

        const jobs = await extractJobsFromDom(page);
        log.info(`[Browser] Extracted ${jobs.length} jobs from DOM`);

        return jobs.map((j) => ({ ...j, id: stableJobId(j) }));
      } catch (err) {
        log.warn(`[Browser] Attempt ${attempt}/2: ${err.message}`);
        if (attempt === 2) throw err;
        await sleep(jitter(6000, 2000));
      }
    }
  } finally {
    await context.close();
    await browser.close();
  }

  return [];
}
