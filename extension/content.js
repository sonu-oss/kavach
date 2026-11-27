/**
 * Kavach Live Chat Shield — Content Script for WhatsApp Web & Telegram Web
 * Automatically detects when a user opens or switches a chat,
 * extracts incoming conversation messages reliably across modern DOM structures,
 * analyzes threats in real-time with Groq AI,
 * automatically dispatches emergency SOS alerts with GPS to guardians when Threat Score >= 95%,
 * presents full protection options when Threat Score is under 95%,
 * and pairs device identity between WhatsApp Web and the local Kavach account.
 */

(() => {
  const activePlatform = location.host.includes("whatsapp.com") ? "whatsapp" : "telegram";
  let lastScannedFingerprint = "";
  let lastContactName = "";
  let lastMessageCount = 0;
  let autoAlertDispatched = new Set();
  let autoVaultSaved = new Set();
  let scanDebounceTimer = null;
  let isProtectionEnabled = true;
  let currentBackendUrl = "http://127.0.0.1:8000";
  let pairedKavachUser = "Adarsh";

  chrome.storage?.sync?.get(["kavachEnabled", "kavachApiUrl"], (res) => {
    if (res?.kavachEnabled !== undefined) isProtectionEnabled = res.kavachEnabled;
    if (res?.kavachApiUrl) currentBackendUrl = res.kavachApiUrl;
  });

  // Extract WhatsApp Web Self User (e.g. "@adarsh.gangareddy (You)")
  function extractWhatsAppSelfUser() {
    const youEl = document.querySelector(
      "#pane-side span[title*='(You)'], div[data-testid='cell-frame-container'] span[title*='(You)'], span[title*='You']"
    );
    if (youEl) {
      const title = youEl.getAttribute("title") || youEl.textContent?.trim();
      if (title) {
        chrome.runtime?.sendMessage?.({
          type: "PAIR_DEVICE_IDENTITY",
          whatsappUser: title,
        });
        return title;
      }
    }
    return null;
  }

  function getGeolocation(cb) {
    if (!navigator.geolocation) return cb(null);
    navigator.geolocation.getCurrentPosition(
      (pos) => cb({ lat: pos.coords.latitude, lng: pos.coords.longitude }),
      () => cb(null),
      { timeout: 3500, maximumAge: 60000 }
    );
  }

  // Inject Kavach persistent floating badge into the page
  function injectStatusBadge() {
    if (document.getElementById("kavach-live-badge")) return;
    const badge = document.createElement("div");
    badge.id = "kavach-live-badge";
    badge.className = "kavach-status-badge";
    badge.innerHTML = `<span class="kavach-status-dot"></span><span>🛡️ Kavach Active</span>`;
    badge.title = "Kavach Live Shield is active. Paired with local account. Click to re-scan this chat.";
    badge.onclick = (e) => {
      e.stopPropagation();
      badge.innerHTML = `<span class="kavach-status-dot" style="background:#f59e0b;"></span><span>⚡ Scanning Chat...</span>`;
      performScan(true);
    };
    document.body.appendChild(badge);
  }

  function isIgnoredHeaderString(str) {
    if (!str) return true;
    const s = str.trim().toLowerCase();
    return (
      s === "online" ||
      s.startsWith("typing") ||
      s.startsWith("last seen") ||
      s.includes("click here") ||
      s.includes("tap here") ||
      s.length < 2
    );
  }

  // Detect active contact name in WhatsApp Web header
  function getWhatsAppActiveContact() {
    const mainPanel = document.querySelector("#main") || document.querySelector("div[data-testid='conversation-panel-wrapper']");
    if (!mainPanel) return null;
    const header = mainPanel.querySelector("header");
    if (!header) return null;

    // 1. Explicit chat title data-testid
    const testIdTitle = header.querySelector("span[data-testid='conversation-info-header-chat-title']");
    if (testIdTitle && testIdTitle.textContent) {
      const val = testIdTitle.textContent.trim();
      if (!isIgnoredHeaderString(val)) return val;
    }

    // 2. Any span with title attribute inside header
    const titleSpans = header.querySelectorAll("span[title], div[role='button'] span[title]");
    for (const span of titleSpans) {
      const title = span.getAttribute("title")?.trim();
      if (title && !isIgnoredHeaderString(title)) return title;
    }

    // 3. Header heading or dir=auto text
    const headings = header.querySelectorAll("h2, div[role='heading'], span[dir='auto'], ._ao3e");
    for (const el of headings) {
      const text = el.textContent?.trim();
      if (text && !isIgnoredHeaderString(text)) return text;
    }

    return null;
  }

  // Extract clean text from a message row
  function extractWhatsAppMessageText(rowNode) {
    // 1. Common selectable text wrappers
    const textEl = rowNode.querySelector(
      ".selectable-text, span.selectable-text, .copyable-text, span.copyable-text, span[dir='ltr'], span._ao3e, span[data-lexical-text='true'], div[data-pre-plain-text]"
    );
    let text = textEl ? (textEl.innerText || textEl.textContent || "").trim() : "";

    // 2. Fallback: inspect row bubble excluding timestamps & status icons
    if (!text) {
      const clone = rowNode.cloneNode(true);
      clone.querySelectorAll(
        "span[data-testid='msg-time'], [data-testid='status-check'], [data-testid='status-dblcheck'], [data-testid='msg-meta'], button, svg"
      ).forEach((el) => el.remove());
      text = (clone.innerText || clone.textContent || "").trim();
    }

    // Filter out WhatsApp system info banners and call logs
    const lower = text.toLowerCase();
    if (
      !text ||
      text.length < 2 ||
      text.includes("Messages and calls are end-to-end encrypted") ||
      text.includes("Security code changed") ||
      text.includes("added you to this group") ||
      text.includes("is a contact") ||
      text.includes("waiting for this message") ||
      lower === "video call" ||
      lower === "voice call" ||
      lower.startsWith("missed video call") ||
      lower.startsWith("missed voice call") ||
      lower === "answered on other device" ||
      lower === "click to call back"
    ) {
      return null;
    }

    return text;
  }

  // Determine if a message row is outgoing (sent by user) or incoming
  function isWhatsAppOutgoing(rowNode) {
    const dataId = rowNode.getAttribute("data-id") || rowNode.closest("div[data-id]")?.getAttribute("data-id") || "";
    if (dataId.startsWith("true_")) return true;
    if (dataId.startsWith("false_")) return false;

    const cls = (rowNode.className || "") + " " + (rowNode.parentElement?.className || "");
    if (cls.includes("message-out")) return true;
    if (cls.includes("message-in")) return false;

    if (rowNode.querySelector("[data-testid='msg-check'], [data-testid='msg-dblcheck']")) return true;
    return false;
  }

  // Extract messages from modern WhatsApp Web DOM
  function parseWhatsAppMessages() {
    // 1. Strict active conversation panel ONLY (#main)
    const mainPanel = document.querySelector("#main") || document.querySelector("div[data-testid='conversation-panel-wrapper']");
    if (!mainPanel) return null;

    const contactName = getWhatsAppActiveContact();
    if (!contactName) return null;

    // 2. Find ONLY message containers inside #main — NEVER use div[role='row'] which matches sidebar items!
    const rows = mainPanel.querySelectorAll(
      "div[data-testid='msg-container'], div.message-in, div.message-out, div[class*='message-in'], div[class*='message-out']"
    );
    if (!rows.length) return null;

    const messages = [];
    const seenTexts = new Set();

    rows.forEach((node, idx) => {
      // Must not be in header, footer (input bar), or any sidebar
      if (node.closest("header, footer, #pane-side, #side, div[data-testid='chat-list']")) return;

      const text = extractWhatsAppMessageText(node);
      if (!text) return;

      const isOut = isWhatsAppOutgoing(node);
      const sender = isOut ? "me" : contactName;
      const dedupeKey = `${sender}::${text}`;
      if (seenTexts.has(dedupeKey)) return;
      seenTexts.add(dedupeKey);

      messages.push({
        id: `wa-${idx}-${text.slice(0, 16).replace(/[^a-zA-Z0-9]/g, "_")}`,
        sender: sender,
        text: text,
        timestamp: "",
      });
    });

    if (!messages.length) return null;

    return {
      platform: "whatsapp-web",
      self_handle: "me",
      contact_name: contactName,
      messages: messages.slice(-30),
    };
  }

  // Extract messages from Telegram Web DOM
  function parseTelegramMessages() {
    const header = document.querySelector(".chat-info .peer-title, .top .name, .chat-title, .user-title");
    const contactName = header?.textContent?.trim() || "Sender";

    const msgNodes = document.querySelectorAll(".bubble, .message, .bubble-content");
    if (!msgNodes.length) return null;

    const messages = [];
    msgNodes.forEach((node, idx) => {
      const isOut = node.classList.contains("is-out") || node.classList.contains("own");
      const textNode = node.querySelector(".message-content, .text-content, .bubble-content, .message");
      const text = textNode?.textContent?.trim();
      if (!text || text.length < 2) return;

      messages.push({
        id: `tg-${idx}-${text.slice(0, 16).replace(/[^a-zA-Z0-9]/g, "_")}`,
        sender: isOut ? "me" : contactName,
        text: text,
        timestamp: "",
      });
    });

    return {
      platform: "telegram-web",
      self_handle: "me",
      contact_name: contactName,
      messages: messages.slice(-30),
    };
  }

  // Execute scan of currently open chat
  function performScan(force = false) {
    if (!isProtectionEnabled) return;

    extractWhatsAppSelfUser();
    const badge = document.getElementById("kavach-live-badge");
    const chatData = activePlatform === "whatsapp" ? parseWhatsAppMessages() : parseTelegramMessages();

    if (!chatData || !chatData.messages.length) {
      if (force && badge) {
        badge.innerHTML = `<span class="kavach-status-dot" style="background:#ef4444;"></span><span>⚠️ Open a chat to scan</span>`;
        setTimeout(() => {
          badge.innerHTML = `<span class="kavach-status-dot"></span><span>🛡️ Kavach Active</span>`;
        }, 2200);
      }
      return;
    }

    // Detect chat switch
    if (chatData.contact_name && chatData.contact_name !== lastContactName) {
      lastContactName = chatData.contact_name;
      lastScannedFingerprint = "";
      lastMessageCount = 0;
      removeThreatBanner();
      if (badge) {
        badge.innerHTML = `<span class="kavach-status-dot" style="background:#f59e0b;"></span><span>🔄 Analyzing ${chatData.contact_name}...</span>`;
      }
    }

    const incoming = chatData.messages.filter((m) => m.sender !== "me");
    if (!incoming.length) {
      removeThreatBanner();
      if (badge) {
        badge.innerHTML = `<span class="kavach-status-dot" style="background:#10b981;"></span><span>✓ No Threat (${chatData.contact_name})</span>`;
      }
      return;
    }

    // Fingerprint based on contact and recent incoming text
    const fingerprint = `${chatData.contact_name}::` + incoming.slice(-4).map((m) => m.text).join("::");
    if (!force && fingerprint === lastScannedFingerprint && incoming.length === lastMessageCount) {
      return;
    }
    lastScannedFingerprint = fingerprint;
    lastMessageCount = incoming.length;

    if (badge) {
      badge.innerHTML = `<span class="kavach-status-dot" style="background:#6366f1;"></span><span>🔄 Analyzing ${chatData.contact_name}...</span>`;
    }

    // Fail-safe timeout so badge never gets permanently stuck on "Analyzing..."
    let scanFinished = false;
    const safetyTimeout = setTimeout(() => {
      if (!scanFinished && badge) {
        badge.innerHTML = `<span class="kavach-status-dot"></span><span>🛡️ Kavach Active</span>`;
      }
    }, 6000);

    chrome.runtime?.sendMessage?.(
      {
        type: "ANALYZE_CHAT",
        payload: {
          platform: chatData.platform,
          self_handle: "me",
          messages: chatData.messages,
        },
      },
      (response) => {
        scanFinished = true;
        clearTimeout(safetyTimeout);

        if (!response || !response.ok) {
          if (badge) {
            badge.innerHTML = `<span class="kavach-status-dot" style="background:#ef4444;"></span><span>⚠️ API Offline</span>`;
            setTimeout(() => {
              badge.innerHTML = `<span class="kavach-status-dot"></span><span>🛡️ Kavach Active</span>`;
            }, 3000);
          }
          return;
        }

        if (response.apiUrl) currentBackendUrl = response.apiUrl;
        const verdict = response.data;

        // Threat Flagged: Risk score >= 50 and category not safe
        if (verdict.severity !== "low" && verdict.category !== "safe" && verdict.risk_score >= 50) {
          const isCritical = verdict.risk_score >= 95;
          let isAutoDispatched = false;

          // Requirement: If threat level >= 95%, automatically dispatch emergency alert to guardians AND auto-save to vault
          if (isCritical) {
            if (!autoAlertDispatched.has(fingerprint)) {
              autoAlertDispatched.add(fingerprint);
              isAutoDispatched = true;

              getGeolocation((loc) => {
                chrome.runtime?.sendMessage?.(
                  {
                    type: "TRIGGER_AUTO_ALERT",
                    payload: {
                      category: verdict.category,
                      risk_score: verdict.risk_score,
                      reason: verdict.reason,
                      sender: chatData.contact_name,
                      guardian_advice: verdict.guardian_advice,
                      lat: loc?.lat,
                      lng: loc?.lng,
                    },
                  },
                  (alertResp) => {
                    console.log("Kavach Auto-Alert Dispatched (95%+ Critical Threat):", alertResp);
                  }
                );
              });
            } else {
              isAutoDispatched = true;
            }

            // Auto-save ONLY for 95%+ Critical Threat (once per incident fingerprint)
            if (!autoVaultSaved.has(fingerprint)) {
              autoVaultSaved.add(fingerprint);

              chrome.runtime?.sendMessage?.({ type: "CAPTURE_CHAT_SCREENSHOT" }, (shotResp) => {
                const screenshots = [];
                if (shotResp && shotResp.ok && shotResp.dataUrl) {
                  screenshots.push({
                    data_url: shotResp.dataUrl,
                    kind: "chat_capture",
                  });
                }

                const vaultPayload = {
                  platform: chatData.platform,
                  conversation: chatData.contact_name || "WhatsApp Contact",
                  messages: chatData.messages.map((m) => ({
                    ref: m.id,
                    sender: m.sender,
                    text: m.text,
                    time: m.timestamp || "",
                    flagged: m.sender !== "me",
                  })),
                  screenshots: screenshots,
                  analysis: verdict,
                  client_captured_at: new Date().toISOString(),
                  incident_date: new Date().toISOString().slice(0, 10),
                };

                chrome.runtime?.sendMessage?.({
                  type: "SAVE_TO_VAULT",
                  payload: vaultPayload,
                });
              });
            }
          }

          displayThreatBanner(verdict, chatData, isAutoDispatched);

          if (badge) {
            badge.innerHTML = `<span class="kavach-status-dot" style="background:#ef4444;"></span><span>🚨 Threat: ${verdict.risk_score}% (${isCritical ? "95%+ Critical" : "Review"})</span>`;
          }

          chrome.runtime?.sendMessage?.({
            type: "THREAT_DETECTED",
            category: verdict.category,
            severity: verdict.severity,
            risk_score: verdict.risk_score,
            reason: verdict.reason,
            sender: chatData.contact_name,
          });
        } else {
          // Chat is safe
          removeThreatBanner();
          if (badge) {
            badge.innerHTML = `<span class="kavach-status-dot" style="background:#10b981;"></span><span>✓ No Threat (${chatData.contact_name})</span>`;
            setTimeout(() => {
              if (badge && badge.textContent.includes("No Threat")) {
                badge.innerHTML = `<span class="kavach-status-dot"></span><span>🛡️ Kavach Active</span>`;
              }
            }, 3500);
          }
        }
      }
    );
  }

  // Display high-visibility fixed safety banner
  function displayThreatBanner(verdict, chatData, isAutoDispatched = false) {
    let banner = document.getElementById("kavach-threat-banner");
    if (!banner) {
      banner = document.createElement("div");
      banner.id = "kavach-threat-banner";
      banner.className = "kavach-shield-banner";
      document.body.appendChild(banner);
    }

    const categoryLabel = (verdict.category || "SAFETY THREAT").replace(/_/g, " ").toUpperCase();
    const senderName = chatData.contact_name || "Sender";
    const isCritical = verdict.risk_score >= 95;

    // Header badge & score display
    const badgeClass = isCritical ? "kavach-threat-badge" : "kavach-threat-badge kavach-threat-warn";
    const headerHtml = `
      <div class="kavach-banner-top">
        <span class="${badgeClass}">${isCritical ? "🚨 CRITICAL THREAT DETECTED" : "⚠️ SAFETY THREAT DETECTED"}: ${categoryLabel}</span>
        <span class="kavach-risk-score">Threat Level: ${verdict.risk_score}% (${(verdict.severity || "HIGH").toUpperCase()})</span>
      </div>
    `;

    // Body content: Auto-alert info for 95%+, or Options explanation for < 95%
    let bodyHtml = `
      <div class="kavach-banner-body">
        <strong>Harassment & Coercion Alert from ${senderName}:</strong>
        <div style="margin-top:4px; opacity:0.95; font-size:12.5px;">${verdict.reason}</div>
    `;

    if (isCritical || isAutoDispatched) {
      bodyHtml += `
        <div class="kavach-auto-dispatched-box">
          <span style="font-size:16px;">⚡</span>
          <span><strong>EMERGENCY ALERT SENT (Threat Level ${verdict.risk_score}% &ge; 95%):</strong> An emergency SOS alert with live GPS coordinates and supportive guidance was automatically dispatched to your connected Kavach guardians via Telegram and Email.</span>
        </div>
      `;
    } else {
      bodyHtml += `
        <div class="kavach-options-info">
          <span>ℹ️ <strong>Threat level is ${verdict.risk_score}% (under 95% auto-dispatch threshold).</strong> Full safety options are available below:</span>
        </div>
      `;
    }
    bodyHtml += `</div>`;

    // Action buttons
    let actsHtml = `<div class="kavach-banner-acts">`;
    if (isCritical || isAutoDispatched) {
      actsHtml += `
        <button type="button" class="kavach-btn kavach-btn-sos" id="kavach-act-sos-status">✓ Emergency Alert Active</button>
        <button type="button" class="kavach-btn" id="kavach-act-vault">🔒 Save Evidence to Vault</button>
        <button type="button" class="kavach-btn" id="kavach-act-complaint">⚖️ View Legal Draft</button>
        <button type="button" class="kavach-btn" id="kavach-act-dismiss">Dismiss</button>
      `;
    } else {
      actsHtml += `
        <button type="button" class="kavach-btn kavach-btn-sos-manual" id="kavach-act-sos-manual">🚨 Send Emergency Alert to Guardians</button>
        <button type="button" class="kavach-btn" id="kavach-act-vault">🔒 Save Evidence to Vault</button>
        <button type="button" class="kavach-btn" id="kavach-act-complaint">⚖️ Draft Legal Complaint</button>
        <button type="button" class="kavach-btn" id="kavach-act-dismiss">Dismiss</button>
      `;
    }
    actsHtml += `</div>`;

    banner.innerHTML = headerHtml + bodyHtml + actsHtml;

    // Save Evidence to Vault action
    const vaultBtn = document.getElementById("kavach-act-vault");
    if (vaultBtn) {
      vaultBtn.onclick = () => {
        vaultBtn.disabled = true;
        vaultBtn.textContent = "📸 Capturing & Sealing...";

        // Capture visible chat screenshot
        chrome.runtime?.sendMessage?.({ type: "CAPTURE_CHAT_SCREENSHOT" }, (shotResp) => {
          const screenshots = [];
          if (shotResp && shotResp.ok && shotResp.dataUrl) {
            screenshots.push({
              data_url: shotResp.dataUrl,
              kind: "chat_capture",
            });
          }

          const targetContactName = chatData.contact_name || senderName || "WhatsApp Contact";
          const vaultPayload = {
            platform: chatData.platform,
            conversation: targetContactName,
            messages: chatData.messages.map((m) => ({
              ref: m.id,
              sender: m.sender,
              text: m.text,
              time: m.timestamp || "",
              flagged: m.sender !== "me",
            })),
            screenshots: screenshots,
            analysis: verdict,
            client_captured_at: new Date().toISOString(),
            incident_date: new Date().toISOString().slice(0, 10),
          };

          chrome.runtime?.sendMessage?.(
            {
              type: "SAVE_TO_VAULT",
              payload: vaultPayload,
            },
            (resp) => {
              if (resp && resp.ok) {
                vaultBtn.textContent = "✓ Sealed in Vault!";
                vaultBtn.style.background = "#10b981";
                vaultBtn.style.borderColor = "#10b981";
                const targetUrl = resp.apiUrl || currentBackendUrl;
                setTimeout(() => {
                  window.open(`${targetUrl}/index.html#vault`, "_blank");
                }, 600);
              } else {
                vaultBtn.disabled = false;
                vaultBtn.textContent = "⚠️ Save Failed - Retry";
              }
            }
          );
        });
      };
    }

    // Manual SOS Alert dispatch (for threats < 95%)
    const manualSosBtn = document.getElementById("kavach-act-sos-manual");
    if (manualSosBtn) {
      manualSosBtn.onclick = () => {
        manualSosBtn.disabled = true;
        manualSosBtn.textContent = "⚡ Dispatching Alert...";

        getGeolocation((loc) => {
          chrome.runtime?.sendMessage?.(
            {
              type: "TRIGGER_AUTO_ALERT",
              payload: {
                category: verdict.category,
                risk_score: verdict.risk_score,
                reason: verdict.reason,
                sender: senderName,
                guardian_advice: verdict.guardian_advice,
                lat: loc?.lat,
                lng: loc?.lng,
              },
            },
            (alertResp) => {
              if (alertResp && alertResp.ok) {
                manualSosBtn.textContent = "✓ Sent to Guardians!";
                manualSosBtn.style.background = "#10b981";
                manualSosBtn.style.borderColor = "#10b981";
                const targetUrl = alertResp.apiUrl || currentBackendUrl;
                setTimeout(() => {
                  window.open(`${targetUrl}/index.html#guardians`, "_blank");
                }, 800);
              } else {
                manualSosBtn.disabled = false;
                manualSosBtn.textContent = "⚠️ Retry Sending Alert";
              }
            }
          );
        });
      };
    }

    // View Active SOS action (for 95%+)
    const statusSosBtn = document.getElementById("kavach-act-sos-status");
    if (statusSosBtn) {
      statusSosBtn.onclick = () => {
        window.open(`${currentBackendUrl}/index.html#guardians`, "_blank");
      };
    }

    // Legal Complaint drafting action
    const complaintBtn = document.getElementById("kavach-act-complaint");
    if (complaintBtn) {
      complaintBtn.onclick = () => {
        window.open(`${currentBackendUrl}/index.html#safetyspace`, "_blank");
      };
    }

    // Dismiss banner action
    const dismissBtn = document.getElementById("kavach-act-dismiss");
    if (dismissBtn) {
      dismissBtn.onclick = () => {
        banner.remove();
      };
    }
  }

  function removeThreatBanner() {
    const existing = document.getElementById("kavach-threat-banner");
    if (existing) existing.remove();
  }

  // Listen for direct messages from extension popup
  chrome.runtime?.onMessage?.addListener((request, sender, sendResponse) => {
    if (request.action === "TRIGGER_TEST_BANNER") {
      performScan(true);
      sendResponse({ ok: true });
    }
  });

  // Watch for active chat changes and sidebar interactions
  function setupChatWatchers() {
    // 1. Fast triggers on sidebar contact clicks or searches
    document.addEventListener("click", (e) => {
      const isChatClick = e.target.closest(
        "#pane-side, div[role='listitem'], div[data-testid='cell-frame-container'], div[data-testid='chat-list-item'], div[data-testid='chat'], header, div[role='button']"
      );
      if (isChatClick) {
        // Run progressive scans as WhatsApp renders the messages
        setTimeout(() => performScan(true), 300);
        setTimeout(() => performScan(true), 800);
        setTimeout(() => performScan(false), 1600);
      }
    });

    // 2. Keyboard chat switching (Arrow keys / Enter in search)
    document.addEventListener("keyup", (e) => {
      if (e.key === "Enter" || e.key === "ArrowDown" || e.key === "ArrowUp") {
        setTimeout(() => performScan(true), 400);
      }
    });

    // 3. Heartbeat chat watcher (checks every 750ms if active chat or messages changed)
    setInterval(() => {
      const activeContact = activePlatform === "whatsapp" ? getWhatsAppActiveContact() : "Telegram";
      if (activeContact && activeContact !== "Sender" && activeContact !== lastContactName) {
        performScan(true);
      }
    }, 750);

    // 4. MutationObserver for DOM changes with fast 350ms debounce
    const observer = new MutationObserver(() => {
      clearTimeout(scanDebounceTimer);
      scanDebounceTimer = setTimeout(() => performScan(false), 350);
    });

    observer.observe(document.body, { childList: true, subtree: true });

    injectStatusBadge();
    extractWhatsAppSelfUser();

    // Initial scans after page load
    setTimeout(() => performScan(true), 1200);
    setTimeout(() => performScan(false), 2500);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", setupChatWatchers);
  } else {
    setupChatWatchers();
  }
})();
