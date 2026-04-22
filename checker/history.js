/**
 * checker/history.js
 * Read/write check history (seen job IDs + run log)
 */

import fs from "fs/promises";
import path from "path";
import { fileURLToPath } from "url";
import { log } from "./logger.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const HISTORY_FILE = path.join(__dirname, "..", "logs", "check-history.json");
const MAX_HISTORY_ENTRIES = 100;
const MAX_SEEN_IDS = 2000;

export async function readHistory() {
  try {
    const raw = await fs.readFile(HISTORY_FILE, "utf8");
    const parsed = JSON.parse(raw);

    return {
      seenJobIds: Array.isArray(parsed.seenJobIds) ? parsed.seenJobIds : [],
      checks: Array.isArray(parsed.checks) ? parsed.checks : [],
    };
  } catch (err) {
    if (err.code === "ENOENT") {
      log.info("[History] No history file found — starting fresh");
      return { seenJobIds: [], checks: [] };
    }
    throw err;
  }
}

export async function writeHistory(history) {
  await fs.mkdir(path.dirname(HISTORY_FILE), { recursive: true });

  const trimmed = {
    seenJobIds: history.seenJobIds.slice(-MAX_SEEN_IDS),
    checks: history.checks.slice(0, MAX_HISTORY_ENTRIES),
  };

  await fs.writeFile(HISTORY_FILE, JSON.stringify(trimmed, null, 2) + "\n");
}

export function buildCheckEntry(status, details = {}) {
  return {
    checkedAt: new Date().toISOString(),
    status,
    ...details,
  };
}
