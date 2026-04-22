/**
 * checker/logger.js
 * Lightweight structured logger with timestamps
 */

function ts() {
  return new Date().toISOString();
}

export const log = {
  info: (...args) => console.log(`[${ts()}] INFO `, ...args),
  warn: (...args) => console.warn(`[${ts()}] WARN `, ...args),
  error: (...args) => console.error(`[${ts()}] ERROR`, ...args),
  debug: (...args) => {
    if (process.env.DEBUG) console.log(`[${ts()}] DEBUG`, ...args);
  },
};
