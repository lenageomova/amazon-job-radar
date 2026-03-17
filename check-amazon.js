import { chromium } from "playwright";

const TELEGRAM_BOT_TOKEN = process.env.8759968532:AAHbTV4T3nP-HOYwetCSDAyjR9Mmy_uJz9Q;
const TELEGRAM_CHAT_ID = process.env.650824092;

const SEARCH_URL =
  "https://hiring.amazon.ca/search/warehouse-jobs?base_query=&loc_query=Calgary";

async function sendTelegram(message) {
  const url =
    `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage` +
    `?chat_id=${TELEGRAM_CHAT_ID}&text=${encodeURIComponent(message)}`;

  await fetch(url);
}

async function checkAmazon() {
  const browser = await chromium.launch();
  const page = await browser.newPage();

  await page.goto(SEARCH_URL);

  const content = await page.content();

  if (content.includes("Warehouse") || content.includes("Calgary")) {
    await sendTelegram(
      "⚠️ Possible Amazon job detected in Calgary\nhttps://hiring.amazon.ca/"
    );
  }

  await browser.close();
}

checkAmazon();
