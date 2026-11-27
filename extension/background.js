/**
 * Background Service Worker for Kavach Live Chat Shield.
 * Handles desktop push notifications, badging, background API proxying,
 * and authenticated session linking with the local Kavach web app.
 */

chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.sync.set({
    kavachEnabled: true,
    kavachApiUrl: "http://127.0.0.1:8000",
    threatCount: 0,
  });
});

// Safely retrieve the Kavach session token from cookies
async function getKavachAuth() {
  return new Promise((resolve) => {
    try {
      if (!chrome.cookies) {
        return resolve({ token: null, host: null });
      }
      // Check 127.0.0.1 first (default local address)
      chrome.cookies.get({ url: "http://127.0.0.1:8000", name: "kavach_session" }, (c1) => {
        if (c1 && c1.value) {
          return resolve({ token: c1.value, host: "http://127.0.0.1:8000" });
        }
        // Then check localhost
        chrome.cookies.get({ url: "http://localhost:8000", name: "kavach_session" }, (c2) => {
          if (c2 && c2.value) {
            return resolve({ token: c2.value, host: "http://localhost:8000" });
          }
          // Fallback to storage
          chrome.storage.local.get(["kavachToken", "kavachApiUrl"], (s) => {
            resolve({ token: s?.kavachToken || null, host: s?.kavachApiUrl || null });
          });
        });
      });
    } catch (err) {
      console.warn("getKavachAuth error:", err);
      resolve({ token: null, host: null });
    }
  });
}

// Find working API endpoint with timeout guard
async function resolveApiBase() {
  try {
    const auth = await getKavachAuth();
    const cfg = await new Promise((r) => chrome.storage.sync.get(["kavachApiUrl"], r));
    const candidateUrls = [
      auth.host,
      cfg?.kavachApiUrl,
      "http://127.0.0.1:8000",
      "http://localhost:8000",
    ].filter(Boolean);

    for (const url of candidateUrls) {
      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 1200);
        const res = await fetch(`${url}/health`, { signal: controller.signal });
        clearTimeout(timeoutId);
        if (res.ok) {
          return { apiUrl: url, token: auth.token };
        }
      } catch (e) {
        // Continue to next candidate
      }
    }
    return { apiUrl: cfg?.kavachApiUrl || "http://127.0.0.1:8000", token: auth.token };
  } catch (err) {
    return { apiUrl: "http://127.0.0.1:8000", token: null };
  }
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "ANALYZE_CHAT") {
    (async () => {
      try {
        const { apiUrl, token } = await resolveApiBase();
        const headers = {
          "Content-Type": "application/json",
          "X-Kavach-Client": "extension",
        };
        if (token) {
          headers["Authorization"] = `Bearer ${token}`;
          headers["X-Kavach-Token"] = token;
        }

        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 12000); // 12s timeout for AI analysis
        const res = await fetch(`${apiUrl}/analyze`, {
          method: "POST",
          headers: headers,
          credentials: "include",
          signal: controller.signal,
          body: JSON.stringify(msg.payload),
        });
        clearTimeout(timeoutId);

        if (!res.ok) {
          const errText = await res.text().catch(() => "");
          sendResponse({ ok: false, error: errText || `HTTP ${res.status}`, apiUrl });
        } else {
          const data = await res.json();
          sendResponse({ ok: true, data, apiUrl, token });
        }
      } catch (err) {
        sendResponse({ ok: false, error: err.message || "Network request failed" });
      }
    })();
    return true; // Keep channel open
  }

  if (msg.type === "SAVE_TO_VAULT") {
    (async () => {
      try {
        const { apiUrl, token } = await resolveApiBase();
        const headers = {
          "Content-Type": "application/json",
          "X-Kavach-Client": "extension",
        };
        if (token) {
          headers["Authorization"] = `Bearer ${token}`;
          headers["X-Kavach-Token"] = token;
        }

        const res = await fetch(`${apiUrl}/evidence`, {
          method: "POST",
          headers: headers,
          credentials: "include",
          body: JSON.stringify(msg.payload),
        });

        if (!res.ok) {
          const errText = await res.text().catch(() => "");
          sendResponse({ ok: false, error: errText || `HTTP ${res.status}`, apiUrl });
        } else {
          const data = await res.json();
          sendResponse({ ok: true, data, apiUrl });
        }
      } catch (err) {
        sendResponse({ ok: false, error: err.message || "Save failed" });
      }
    })();
    return true;
  }

  if (msg.type === "TRIGGER_AUTO_ALERT") {
    (async () => {
      try {
        const { apiUrl, token } = await resolveApiBase();
        const headers = {
          "Content-Type": "application/json",
          "X-Kavach-Client": "extension",
        };
        if (token) {
          headers["Authorization"] = `Bearer ${token}`;
          headers["X-Kavach-Token"] = token;
        }

        const res = await fetch(`${apiUrl}/sos/auto-alert`, {
          method: "POST",
          headers: headers,
          credentials: "include",
          body: JSON.stringify(msg.payload),
        });

        const data = await res.json().catch(() => ({}));
        sendResponse({ ok: res.ok, data, apiUrl });
      } catch (err) {
        sendResponse({ ok: false, error: err.message || "Alert dispatch failed" });
      }
    })();
    return true;
  }

  if (msg.type === "GET_KAVACH_SESSION") {
    (async () => {
      try {
        const { apiUrl, token } = await resolveApiBase();
        const headers = { "X-Kavach-Client": "extension" };
        if (token) {
          headers["Authorization"] = `Bearer ${token}`;
          headers["X-Kavach-Token"] = token;
        }

        const res = await fetch(`${apiUrl}/auth/me`, {
          headers: headers,
          credentials: "include",
        });

        if (res.ok) {
          const info = await res.json();
          sendResponse({
            ok: true,
            user: info.user,
            guardians_count: info.guardians_count || 0,
            guardians_names: info.guardians_names || [],
            apiUrl,
            token,
          });
        } else {
          sendResponse({ ok: false, error: "Not logged in", apiUrl });
        }
      } catch (err) {
        sendResponse({ ok: false, error: err.message || "Connection failed" });
      }
    })();
    return true;
  }

  if (msg.type === "CAPTURE_CHAT_SCREENSHOT") {
    try {
      chrome.tabs.captureVisibleTab(null, { format: "png" }, (dataUrl) => {
        if (chrome.runtime.lastError || !dataUrl) {
          sendResponse({ ok: false, error: chrome.runtime.lastError?.message || "Capture failed" });
        } else {
          sendResponse({ ok: true, dataUrl });
        }
      });
    } catch (e) {
      sendResponse({ ok: false, error: e.message });
    }
    return true;
  }

  if (msg.type === "PAIR_DEVICE_IDENTITY") {
    // Store device-level pairing between WhatsApp Web user and local Kavach account
    chrome.storage.local.set({
      whatsappUser: msg.whatsappUser || "",
      pairedAt: new Date().toISOString(),
      pairedDevice: navigator.userAgent.slice(0, 50),
    }, () => {
      sendResponse({ ok: true });
    });
    return true;
  }

  if (msg.type === "THREAT_DETECTED") {
    chrome.action.setBadgeText({ text: "!" });
    chrome.action.setBadgeBackgroundColor({ color: "#ef4444" });

    chrome.storage.sync.get(["threatCount"], (data) => {
      const count = (data.threatCount || 0) + 1;
      chrome.storage.sync.set({ threatCount: count });
    });

    const isCritical = (msg.risk_score || 0) >= 95;
    chrome.notifications.create({
      type: "basic",
      iconUrl: "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%23ef4444'><path d='M12 2L1 21h22L12 2zm0 3.5L20 19H4L12 5.5zM11 10v4h2v-4h-2zm0 6v2h2v-2h-2z'/></svg>",
      title: isCritical
        ? `🚨 CRITICAL EMERGENCY (Score: ${msg.risk_score}%): ${msg.category.replace(/_/g, " ").toUpperCase()}`
        : `⚠️ Kavach Alert: ${msg.category.replace(/_/g, " ").toUpperCase()} (${msg.risk_score}%)`,
      message: `Threat from ${msg.sender}: ${msg.reason.slice(0, 110)}...`,
      priority: 2,
    });
    sendResponse({ received: true });
  }
});
