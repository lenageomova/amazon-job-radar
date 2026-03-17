import { chromium } from "playwright";
import https from "https";

const TELEGRAM_BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN;
const TELEGRAM_CHAT_ID = process.env.TELEGRAM_CHAT_ID;

const SEARCH_URL =
  "https://hiring.amazon.ca/search/warehouse-jobs?base_query=&loc_query=Calgary";

function sendTelegram(message) {
  const url =
    `https://api.telegram.org/bot${8759968532:AAHbTV4T3nP-HOYwetCSDAyjR9Mmy_uJz9Q}/sendMessage` +
    `?chat_id=${650824092}&text=${encodeURIComponent(появилась)}`;

  https.get(url);
}

async function checkAmazon() {
  const browser = await chromium.launch();
  const page = await browser.newPage();

  await page.goto(SEARCH_URL);

  const content = await page.content();

  if (content.includes("Warehouse") || content.includes("Calgary")) {
    sendTelegram(
      "⚠️ Amazon job possibly detected in Calgary\nhttps://hiring.amazon.ca/"
    );
  }

  await browser.close();
}

checkAmazon();
