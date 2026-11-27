(() => {
  // Same origin when served by FastAPI; override with ?api=http://host:port for a separate server.
  const API = new URLSearchParams(location.search).get("api") || "";
  const SELF = "me";

  const CATEGORY_LABEL = {
    harassment: "Harassment",
    threat_of_violence: "Threat of violence",
    sextortion_blackmail: "Sextortion / blackmail",
    stalking: "Stalking",
    impersonation: "Impersonation",
    scam: "Scam / fraud",
    safe: "Looks safe",
  };
  const SOURCE_LABEL = {
    llm: (m) => `AI · ${m || "Groq"}`,
    prefilter: () => "Pre-filter (no AI call)",
    keyword_fallback: () => "Keyword fallback",
  };

  const $ = (sel) => document.querySelector(sel);
  const els = {
    contacts: $("#contact-list"),
    chatArea: $("#chat-area"),
    chatName: $("#chat-name"),
    chatSub: $("#chat-sub"),
    chatAvatar: $("#chat-avatar"),
    composer: $("#composer"),
    input: $("#composer-input"),
    sendAs: $("#send-as"),
    shield: $("#shield-body"),
    scanBtn: $("#scan-btn"),
    auto: $("#auto-scan"),
    llmStatus: $("#llm-status"),
    toast: $("#toast"),
  };

  const savedChats = (() => {
    try {
      return JSON.parse(localStorage.getItem("kavach_chats") || "[]");
    } catch {
      return [];
    }
  })();
  const conversations = structuredClone(savedChats.length ? savedChats : (window.SEED_CONVERSATIONS || [])).map((c) => ({
    ...c,
    messages: c.messages.map((m, i) => ({ ...m, id: m.id || `${c.id}-${i + 1}` })),
  }));
  const results = {}; // conversation id -> last analysis
  let activeId = null;
  let scanSeq = 0;

  function saveLocalChats() {
    try {
      localStorage.setItem("kavach_chats", JSON.stringify(conversations));
    } catch {
      /* ignore */
    }
  }

  // ---------- helpers ----------
  const esc = (s) =>
    String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const active = () => conversations.find((c) => c.id === activeId);
  const now = () => new Date().toTimeString().slice(0, 5);

  function toast(msg) {
    els.toast.textContent = msg;
    els.toast.classList.add("show");
    clearTimeout(toast.t);
    toast.t = setTimeout(() => els.toast.classList.remove("show"), 2600);
  }

  function loadSampleConversations() {
    const samples = structuredClone(window.SAMPLE_INCIDENTS || []).map((c) => ({
      ...c,
      messages: c.messages.map((m, i) => ({ ...m, id: `${c.id}-${i + 1}` })),
    }));
    conversations.splice(0, conversations.length, ...samples);
    saveLocalChats();
    if (conversations.length) {
      openChat(conversations[0].id);
    } else {
      renderContacts();
      renderChat();
    }
    toast("Sample incident loaded");
  }

  // ---------- mock chat ----------
  function renderContacts() {
    if (!conversations.length) {
      els.contacts.innerHTML = `
        <li class="contact-empty" style="padding: 24px 14px; text-align: center; color: var(--muted); font-size: 13px; line-height: 1.5;">
          <div style="font-size: 26px; margin-bottom: 8px;">🛡️</div>
          <strong style="color:var(--text); display:block; margin-bottom:4px;">No Chats Scanned Yet</strong>
          <p style="margin: 4px 0 14px; font-size: 12px; opacity: 0.85;">Use <strong>Scan a screenshot</strong> above or open WhatsApp Web / Telegram with the Kavach Live Shield.</p>
          <button type="button" class="btn btn--ghost small" id="load-sample-btn" style="font-size: 11.5px; width:100%;">+ Load Sample Incident</button>
        </li>`;
      const btn = document.getElementById("load-sample-btn");
      btn?.addEventListener("click", () => {
        loadSampleConversations();
        document.body.classList.remove("menu-open");
        window.kavachMobile?.show("main");
      });
      return;
    }
    els.contacts.innerHTML = conversations
      .map((c) => {
        const last = c.messages[c.messages.length - 1];
        const r = results[c.id];
        const dot = r ? `<span class="contact__dot sev-${r.severity}" style="background:var(--risk-color)" title="${esc(CATEGORY_LABEL[r.category])}"></span>` : "";
        return `<li class="contact ${c.id === activeId ? "active" : ""}" data-id="${c.id}">
          <div class="avatar">${esc(c.avatar || (c.name ? c.name[0] : "?"))}</div>
          <div class="contact__meta">
            <div class="contact__name">${esc(c.name)}</div>
            <div class="contact__preview">${esc(last ? last.text : "")}</div>
          </div>${dot}
        </li>`;
      })
      .join("");
  }

  function renderChat() {
    const c = active();
    if (!c) {
      els.chatName.textContent = "Kavach Live Shield";
      els.chatSub.textContent = "Real-time threat detection active";
      els.chatAvatar.textContent = "🛡️";
      els.chatArea.innerHTML = `
        <div style="display:flex; flex-direction:column; align-items:center; justify-content:center; height:100%; color:var(--muted); text-align:center; padding:24px;">
          <div style="font-size:38px; margin-bottom:12px;">🛡️</div>
          <h3 style="margin-bottom:8px; color:var(--text); font-size:16px;">Kavach Shield Ready</h3>
          <p style="max-width:380px; font-size:13px; line-height:1.6; opacity:0.9;">
            No chat selected. Paste or scan a screenshot, or open WhatsApp Web / Telegram with the <strong>Kavach Live Chat Shield</strong> extension to detect harassment live.
          </p>
        </div>`;
      renderEmpty();
      return;
    }
    els.chatName.textContent = c.name;
    els.chatSub.textContent = c.subtitle || c.platform || "WhatsApp";
    els.chatAvatar.textContent = c.avatar || (c.name ? c.name[0] : "?");
    els.chatArea.innerHTML = c.messages
      .map(
        (m) => `<div class="msg ${m.sender === SELF ? "msg--me" : ""}"
          data-msg-id="${esc(m.id)}" data-sender="${esc(m.sender)}" data-time="${esc(m.time)}">
          <span class="msg__text">${esc(m.text)}</span><span class="msg__time">${esc(m.time)}</span>
        </div>`
      )
      .join("");
    els.chatArea.scrollTop = els.chatArea.scrollHeight;
  }

  function openChat(id) {
    window.kavachImport?.close();
    window.kavachVault?.close();
    window.kavachComplaint?.close();
    window.kavachSos?.close();
    window.kavachProfile?.close();
    window.kavachResponders?.close();
    const mc = document.querySelector("#mock-chat-container");
    if (mc) mc.hidden = false;
    document.body.classList.remove("left-collapsed");
    document.body.classList.add("shield-open");
    window.kavachMobile?.show("main");
    activeId = id;
    renderContacts();
    renderChat();
    if (results[id]) renderVerdict(results[id]);
    else if (els.auto.checked) scan();
    else renderEmpty();
  }

  els.contacts.addEventListener("click", (e) => {
    const li = e.target.closest(".contact");
    if (li) {
      openChat(li.dataset.id);
      document.body.classList.remove("menu-open");
      window.kavachMobile?.show("main");
    }
  });

  els.composer.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = els.input.value.trim();
    if (!text) return;
    const c = active();
    const sender = els.sendAs.value === "me" ? SELF : c.messages.find((m) => m.sender !== SELF)?.sender || c.id;
    c.messages.push({ id: `${c.id}-${c.messages.length + 1}-${Date.now()}`, sender, text, time: now() });
    els.input.value = "";
    delete results[c.id];
    renderContacts();
    renderChat();
    if (els.auto.checked && sender !== SELF) scan();
  });

  // ---------- Shield panel ----------

  // Reads what is on screen, the same way a browser-extension content script would.
  function captureVisibleMessages() {
    return [...els.chatArea.querySelectorAll(".msg")].map((el) => ({
      id: el.dataset.msgId,
      sender: el.dataset.sender,
      text: el.querySelector(".msg__text").textContent,
      timestamp: el.dataset.time,
    }));
  }

  async function scan() {
    if (!activeId) return;
    const convId = activeId;
    const messages = captureVisibleMessages();
    if (!messages.length) return;
    const seq = ++scanSeq;
    els.scanBtn.disabled = true;
    els.shield.innerHTML = `<div class="loading"><div class="spinner"></div>Checking ${messages.length} messages…</div>`;
    window.kavachFlow?.set(2);
    clearHighlights();

    try {
      const res = await fetch(`${API}/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ platform: "mock-chat", self_handle: SELF, messages }),
      });
      if (!res.ok) throw new Error(`Backend returned ${res.status}`);
      const data = await res.json();
      results[convId] = data;
      if (seq !== scanSeq || convId !== activeId) return; // user moved on
      renderContacts();
      renderVerdict(data);
    } catch (err) {
      if (seq !== scanSeq) return;
      els.shield.innerHTML = `<div class="error"><strong>Couldn't reach Kavach backend.</strong><br>${esc(err.message)}</div>`;
    } finally {
      if (seq === scanSeq) els.scanBtn.disabled = false;
    }
  }

  function renderEmpty() {
    window.kavachFlow?.set(1);
    window.kavachMobile?.pill("Risk check ›");
    els.shield.innerHTML = `<div class="empty"><p>Press <strong>Scan</strong> to check this chat.</p></div>`;
  }

  // Tap-to-call emergency numbers, shown wherever she might need them.
  function callsHtml() {
    return `<div class="calls" aria-label="Emergency numbers">
      <a href="tel:112" class="call--urgent"><strong>112</strong><span>Emergency</span></a>
      <a href="tel:1930"><strong>1930</strong><span>Cyber crime</span></a>
      <a href="tel:181"><strong>181</strong><span>Women</span></a>
    </div>`;
  }

  function renderVerdict(r) {
    const c = active();
    const byId = Object.fromEntries(c.messages.map((m) => [m.id, m]));
    const circumference = 2 * Math.PI * 32;
    const offset = circumference * (1 - r.risk_score / 100);
    const risky = r.severity === "medium" || r.severity === "high";
    const flagged = r.flagged_message_ids.filter((id) => byId[id]);

    els.shield.innerHTML = `
      <div class="verdict sev-${r.severity}">
        <div class="risk">
          <div class="gauge">
            <svg viewBox="0 0 76 76" width="76" height="76">
              <circle class="gauge__track" cx="38" cy="38" r="32" fill="none" stroke-width="7"/>
              <circle class="gauge__fill" cx="38" cy="38" r="32" fill="none" stroke-width="7"
                stroke-dasharray="${circumference}" stroke-dashoffset="${circumference}"/>
            </svg>
            <div class="gauge__num">${r.risk_score}</div>
          </div>
          <div>
            <div class="risk__label">${r.severity} risk</div>
            <div class="risk__cat">${esc(CATEGORY_LABEL[r.category] || r.category)}</div>
          </div>
        </div>

        <p class="reason">${esc(r.reason)}</p>

        ${flagged.length ? `<button type="button" class="flag-count" data-jump="${esc(flagged[0])}">⚑ ${flagged.length} message${flagged.length === 1 ? "" : "s"} flagged in the chat</button>` : ""}

        ${risky ? `
        ${r.saved
          ? `<p class="done">✓ Sealed as evidence</p>
             <button class="btn btn--primary btn--block" data-action="complaint">Next: draft complaint →</button>`
          : `<button class="btn btn--primary btn--block" data-action="vault">Next: seal as evidence →</button>`}
        ${callsHtml()}` : ""}

        <p class="meta-line">${esc(SOURCE_LABEL[r.source]?.(r.model) || r.source)} · ${r.pii_masked} personal detail${r.pii_masked === 1 ? "" : "s"} masked</p>
      </div>`;

    // Animate the gauge after paint.
    requestAnimationFrame(() => {
      const fill = els.shield.querySelector(".gauge__fill");
      if (fill) fill.style.strokeDashoffset = offset;
    });
    highlight(flagged, r.severity);
    window.kavachFlow?.set(r.saved ? 3 : 2);
    window.kavachMobile?.pill(`Risk ${r.risk_score} · ${r.severity} ›`);
  }

  function clearHighlights() {
    els.chatArea.querySelectorAll(".msg.flagged").forEach((el) => el.classList.remove("flagged"));
  }

  function highlight(ids, severity) {
    clearHighlights();
    const color = { low: "var(--risk-low)", medium: "var(--risk-med)", high: "var(--risk-high)" }[severity];
    ids.forEach((id) => {
      const el = els.chatArea.querySelector(`[data-msg-id="${CSS.escape(id)}"]`);
      if (el) {
        el.classList.add("flagged");
        el.style.setProperty("--flag-color", color);
      }
    });
  }

  els.shield.addEventListener("click", (e) => {
    const jump = e.target.closest("[data-jump]");
    if (jump) {
      window.kavachMobile?.show("main"); // on a phone the chat is behind this pane
      els.chatArea
        .querySelector(`[data-msg-id="${CSS.escape(jump.dataset.jump)}"]`)
        ?.scrollIntoView({ behavior: "smooth", block: "center" });
    }
    const action = e.target.closest("[data-action]")?.dataset.action;
    // Hooks for Feature 2 (Evidence Vault) and Feature 3 (Complaint Assistant).
    if (action === "vault") saveToVault(e.target.closest("button"));
    if (action === "open-vault") window.kavachVault.open();
    if (action === "complaint") draftComplaint(e.target.closest("button"));
  });

  els.scanBtn.addEventListener("click", scan);

  async function checkHealth() {
    try {
      const h = await (await fetch(`${API}/health`)).json();
      els.llmStatus.textContent = h.llm_configured ? `AI on · ${h.model}` : "AI key missing · keyword mode";
    } catch {
      els.llmStatus.textContent = "backend offline";
    }
  }

  // Exposed for the upcoming Vault / Complaint features and for debugging.
  async function saveToVault(button) {
    const c = active();
    const r = results[c.id];
    if (!r || r.saved) return;
    button.disabled = true;
    button.textContent = "Sealing…";
    try {
      r.saved = await window.kavachVault.save(c, r, els.chatArea);
      toast(`Sealed ${r.saved.records} records in the Evidence Vault`);
      renderContacts();
    } catch (err) {
      toast(`Couldn't save to Vault: ${err.message}`);
    }
    if (activeId === c.id) renderVerdict(r);
  }

  // A complaint is drafted from sealed evidence, so save to the Vault first if needed.
  async function draftComplaint(button) {
    const c = active();
    const r = results[c.id];
    if (!r) return;
    if (!r.saved) {
      button.disabled = true;
      button.textContent = "Sealing evidence…";
      try {
        r.saved = await window.kavachVault.save(c, r, els.chatArea);
        toast(`Evidence sealed first: ${r.saved.records} records`);
      } catch (err) {
        toast(`Couldn't seal evidence: ${err.message}`);
        if (activeId === c.id) renderVerdict(r);
        return;
      }
    }
    window.kavachComplaint.open(r.saved.capture_id, c.name);
  }

  // Screenshot imports or extension syncs become ordinary conversations
  function addConversation(conv) {
    const existing = conversations.find((c) => c.id === conv.id || c.name === conv.name);
    if (existing) {
      Object.assign(existing, conv);
      saveLocalChats();
      openChat(existing.id);
    } else {
      conversations.unshift(conv);
      saveLocalChats();
      openChat(conv.id);
    }
  }

  // Leaves the chat view (used by the screenshot import screen).
  function deselect(message) {
    activeId = null;
    scanSeq++;
    renderContacts();
    renderChat();
    els.shield.innerHTML = `<div class="empty"><p>${message}</p></div>`;
  }

  // Clears active scanned chats
  document.querySelector("#clear-chats").addEventListener("click", async () => {
    if (!(await kavachConfirm("Clear scanned chats and results? Sealed evidence stays permanently in the Vault.", { ok: "Clear", danger: true }))) return;
    conversations.length = 0;
    saveLocalChats();
    Object.keys(results).forEach((k) => delete results[k]);
    deselect("Chats cleared. Use Scan a screenshot or the Kavach Live Shield to scan new chats.");
    toast("Chats cleared");
  });

  // Opens active chat or ready view
  function openShield() {
    const mc = document.querySelector("#mock-chat-container");
    if (mc) mc.hidden = false;
    document.body.classList.remove("left-collapsed");
    document.body.classList.add("shield-open");
    if (conversations.length) {
      openChat(activeId || conversations[0].id);
    } else {
      activeId = null;
      renderContacts();
      renderChat();
    }
  }

  // Takes a verdict produced elsewhere (the Guardian agent) as if this panel had scanned it.
  function adoptResult(convId, result) {
    results[convId] = result;
    renderContacts();
    if (convId === activeId) renderVerdict(result);
  }

  window.kavach = { results, conversations, active, captureVisibleMessages, scan, addConversation, deselect, toast, openShield, callsHtml, adoptResult, esc };

  checkHealth();
  renderContacts();
  // No chat is opened (or scanned) until she leaves the home screen: nav.js calls openShield().
})();
