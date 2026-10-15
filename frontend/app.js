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

  const conversations = structuredClone(window.SEED_CONVERSATIONS).map((c) => ({
    ...c,
    messages: c.messages.map((m, i) => ({ ...m, id: `${c.id}-${i + 1}` })),
  }));
  const results = {}; // conversation id -> last analysis
  let activeId = null;
  let scanSeq = 0;

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

  // ---------- mock chat ----------
  function renderContacts() {
    els.contacts.innerHTML = conversations
      .map((c) => {
        const last = c.messages[c.messages.length - 1];
        const r = results[c.id];
        const dot = r ? `<span class="contact__dot sev-${r.severity}" style="background:var(--risk-color)" title="${esc(CATEGORY_LABEL[r.category])}"></span>` : "";
        return `<li class="contact ${c.id === activeId ? "active" : ""}" data-id="${c.id}">
          <div class="avatar">${esc(c.avatar)}</div>
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
    els.chatName.textContent = c.name;
    els.chatSub.textContent = c.subtitle;
    els.chatAvatar.textContent = c.avatar;
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
    activeId = id;
    renderContacts();
    renderChat();
    if (results[id]) renderVerdict(results[id]);
    else if (els.auto.checked) scan();
    else renderEmpty();
  }

  els.contacts.addEventListener("click", (e) => {
    const li = e.target.closest(".contact");
    if (li) openChat(li.dataset.id);
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
    els.shield.innerHTML = `<div class="loading"><div class="spinner"></div>Scanning ${messages.length} messages…</div>`;
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
    els.shield.innerHTML = `<div class="empty"><p>Press <strong>Scan this chat</strong> to analyse it.</p></div>`;
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

        <div>
          <p class="section-title">Why</p>
          <p class="reason">${esc(r.reason)}</p>
        </div>

        ${flagged.length ? `
        <div>
          <p class="section-title">Flagged messages (${flagged.length})</p>
          <ul class="flag-list">
            ${flagged.map((id) => `<li data-jump="${esc(id)}">“${esc(byId[id].text)}”</li>`).join("")}
          </ul>
        </div>` : ""}

        ${risky ? `
        <div class="actions">
          ${r.saved
            ? `<button class="btn btn--saved" data-action="open-vault" title="Capture ${esc(r.saved.capture_id)}">✓ Sealed in Vault</button>`
            : `<button class="btn btn--primary" data-action="vault">Save to Vault</button>`}
          <button class="btn" data-action="complaint">Draft Complaint</button>
        </div>
        <div class="helpline">In danger right now? Call <strong>112</strong>. Cyber-crime helpline <strong>1930</strong> ·
          <a href="https://cybercrime.gov.in" target="_blank" rel="noopener">cybercrime.gov.in</a></div>` : ""}

        <div class="meta">
          <span class="chip">${esc(SOURCE_LABEL[r.source]?.(r.model) || r.source)}</span>
          <span class="chip">${r.pii_masked} PII item${r.pii_masked === 1 ? "" : "s"} masked</span>
        </div>
      </div>`;

    // Animate the gauge after paint.
    requestAnimationFrame(() => {
      const fill = els.shield.querySelector(".gauge__fill");
      if (fill) fill.style.strokeDashoffset = offset;
    });
    highlight(flagged, r.severity);
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
      els.chatArea
        .querySelector(`[data-msg-id="${CSS.escape(jump.dataset.jump)}"]`)
        ?.scrollIntoView({ behavior: "smooth", block: "center" });
    }
    const action = e.target.closest("[data-action]")?.dataset.action;
    // Hooks for Feature 2 (Evidence Vault) and Feature 3 (Complaint Assistant).
    if (action === "vault") saveToVault(e.target.closest("button"));
    if (action === "open-vault") window.kavachVault.open();
    if (action === "complaint") toast("Complaint Assistant is coming in Feature 3.");
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

  // Screenshot imports become ordinary conversations, so the panel, highlights and
  // the Vault / Complaint hand-off work the same way for them.
  function addConversation(conv) {
    conversations.unshift(conv);
    openChat(conv.id);
  }

  // Leaves the chat view (used by the screenshot import screen).
  function deselect(message) {
    activeId = null;
    scanSeq++;
    renderContacts();
    els.shield.innerHTML = `<div class="empty"><p>${message}</p></div>`;
  }

  window.kavach = { results, conversations, captureVisibleMessages, scan, addConversation, deselect, toast };

  checkHealth();
  openChat(conversations[1].id);
})();
