/**
 * checker/http.js
 * Shared HTTP GET utility — no external dependencies
 */

import https from "https";
import http from "http";

export function httpGet(url, options = {}) {
  return new Promise((resolve, reject) => {
    const lib = url.startsWith("https") ? https : http;
    const parsed = new URL(url);

    const reqOptions = {
      hostname: parsed.hostname,
      path: parsed.pathname + parsed.search,
      method: "GET",
      headers: {
        "User-Agent":
          "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        Accept: "application/json, text/html, */*",
        "Accept-Language": "en-CA,en;q=0.9",
        "Cache-Control": "no-cache",
        Pragma: "no-cache",
        ...options.headers,
      },
      timeout: options.timeout ?? 15000,
    };

    const req = lib.request(reqOptions, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        httpGet(res.headers.location, options).then(resolve).catch(reject);
        return;
      }

      let body = "";
      res.on("data", (chunk) => (body += chunk));
      res.on("end", () => resolve({ status: res.statusCode, body, headers: res.headers }));
    });

    req.on("timeout", () => {
      req.destroy();
      reject(new Error(`Request timeout after ${reqOptions.timeout}ms`));
    });

    req.on("error", reject);
    req.end();
  });
}

export function httpPost(url, payload, options = {}) {
  const body = typeof payload === "string" ? payload : JSON.stringify(payload);

  return new Promise((resolve, reject) => {
    const lib = url.startsWith("https") ? https : http;
    const parsed = new URL(url);

    const req = lib.request(
      {
        hostname: parsed.hostname,
        path: parsed.pathname + parsed.search,
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(body),
          ...options.headers,
        },
        timeout: options.timeout ?? 15000,
      },
      (res) => {
        let data = "";
        res.on("data", (c) => (data += c));
        res.on("end", () => resolve({ status: res.statusCode, body: data }));
      }
    );

    req.on("timeout", () => {
      req.destroy();
      reject(new Error("Request timeout"));
    });
    req.on("error", reject);
    req.write(body);
    req.end();
  });
}
