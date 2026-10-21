// Evidence Vault (Feature 2).
//
// "Save to Vault" captures the chat as an image (html2canvas), sends it with the
// messages and the Shield verdict to POST /evidence, where the server timestamps
// and hash-chains every record. The Vault screen lists the records and re-checks
// the chain with GET /evidence/verify.
(() => {
  const API = new URLSearchParams(location.search).get("api") || "";
  const $ = (sel) => document.querySelector(sel);
  const els = {
    btn: $("#vault-btn"),
    count: $("#vault-count"),
    view: $("#vault-view"),
    body: $("#vault-body"),
    pill: $("#chain-pill"),
    verify: $("#verify-btn"),
    report: $("#report-btn"),
    shield: $("#shield-body"),
  };
  els.report.href = `${API}/evidence/report`;

  const CATEGORY_LABEL = {
    harassment: "Harassment",
    threat_of_violence: "Threat of violence",
    sextortion_blackmail: "Sextortion / blackmail",
    stalking: "Stalking",
    impersonation: "Impersonation",
    scam: "Scam / fraud",
    safe: "Looks safe",
  };
  const STATUS = {
    ok: { icon: "✓", label: "Verified", cls: "ok" },
    unchecked: { icon: "•", label: "Not checked since the last change", cls: "unchecked" },
    tampered: { icon: "✕", label: "Altered: content no longer matches its seal", cls: "bad" },
    rehashed: { icon: "✕", label: "Altered and re-sealed: the next record exposes it", cls: "bad" },
    broken_link: { icon: "✕", label: "Link broken: the record before this was altered", cls: "bad" },
    file_changed: { icon: "✕", label: "Screenshot file changed or missing", cls: "bad" },
    after_break: { icon: "!", label: "Unverified: comes after a break in the chain", cls: "warn" },
  };

  let records = [];
  let statusById = {};
  let verification = null;
  let demoMode = false;
  let isOpen = false;

  const esc = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const dmy = (iso) => iso.split("-").reverse().join("/");
  const short = (h) => `${h.slice(0, 8)}…${h.slice(-6)}`;
  const when = (ts) =>
    new Date(ts).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", dateStyle: "medium", timeStyle: "medium" }) + " IST";

  async function api(path, opts) {
    const res = await fetch(`${API}${path}`, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    return data;
  }

  // ---------- saving ----------
  async function save(conv, analysis, chatArea) {
    const screenshots = [];
    const capture = await captureChat(chatArea);
    if (capture) screenshots.push({ kind: "chat_capture", data_url: capture });
    for (const img of conv.images || []) {
      screenshots.push({ kind: "original_upload", data_url: await asPngOrJpeg(img) });
    }

    const flagged = new Set(analysis.flagged_message_ids);
    const body = {
      platform: conv.platform || (conv.source === "screenshot" ? "Screenshot import" : "ChatSpace (demo)"),
      conversation: conv.name,
      url: conv.source === "screenshot" ? null : `${location.origin}${location.pathname}#chat=${conv.id}`,
      messages: conv.messages.map((m) => ({
        ref: m.id,
        sender: m.sender,
        text: m.text,
        time: m.time || "",
        flagged: flagged.has(m.id),
      })),
      screenshots,
      analysis: {
        category: analysis.category,
        risk_score: analysis.risk_score,
        severity: analysis.severity,
        reason: analysis.reason,
        flagged_message_ids: analysis.flagged_message_ids,
        source: analysis.source,
        model: analysis.model,
      },
      client_captured_at: new Date().toISOString(),
      incident_date: conv.incident_date || null,
    };
    const saved = await api("/evidence", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    refreshCount();
    return saved;
  }

  // Renders the whole conversation (not just the visible part), without Kavach's
  // own highlight badges, so the capture shows the chat as the victim saw it.
  async function captureChat(chatArea) {
    if (!window.html2canvas) return null;
    // html2canvas copies ::before badges before onclone runs, so un-highlight the live DOM briefly.
    const flagged = [...chatArea.querySelectorAll(".msg.flagged")];
    flagged.forEach((m) => m.classList.remove("flagged"));
    try {
      const last = chatArea.lastElementChild;
      const contentHeight = last
        ? Math.ceil(last.getBoundingClientRect().bottom - chatArea.getBoundingClientRect().top + chatArea.scrollTop + 20)
        : chatArea.scrollHeight;
      const canvas = await html2canvas(chatArea, {
        backgroundColor: getComputedStyle(chatArea).backgroundColor,
        scale: Math.min(2, window.devicePixelRatio || 1),
        logging: false,
        height: contentHeight,
        windowHeight: contentHeight + 400,
        onclone: (doc) => {
          const el = doc.getElementById("chat-area");
          el.style.height = `${contentHeight}px`;
          el.style.maxHeight = "none";
          el.style.overflow = "visible";
        },
      });
      return canvas.toDataURL("image/png");
    } catch (err) {
      console.warn("chat capture failed", err);
      return null;
    } finally {
      flagged.forEach((m) => m.classList.add("flagged"));
    }
  }

  async function asPngOrJpeg(dataUrl) {
    if (/^data:image\/(png|jpeg);/.test(dataUrl)) return dataUrl;
    const img = await new Promise((resolve, reject) => {
      const i = new Image();
      i.onload = () => resolve(i);
      i.onerror = reject;
      i.src = dataUrl;
    });
    const c = document.createElement("canvas");
    c.width = img.naturalWidth;
    c.height = img.naturalHeight;
    c.getContext("2d").drawImage(img, 0, 0);
    return c.toDataURL("image/png");
  }

  // ---------- screen ----------
  async function open() {
    window.kavachImport?.close();
    window.kavachComplaint?.close();
    window.kavachSos?.close();
    window.kavachFlow?.set(3);
    window.kavachMobile?.show("main");
    window.kavachMobile?.pill("Details ›");
    isOpen = true;
    document.body.classList.add("vaulting");
    els.view.hidden = false;
    els.btn.classList.add("active");
    window.kavach.deselect("");
    await load({ verified: true });
  }

  function close() {
    if (!isOpen) return;
    isOpen = false;
    document.body.classList.remove("vaulting");
    els.view.hidden = true;
    els.btn.classList.remove("active");
  }

  async function load({ verified }) {
    const data = await api("/evidence");
    records = data.records;
    verification = verified ? data.verification : null;
    statusById = Object.fromEntries(
      records.map((r) => [r.id, verified ? data.verification.records.find((v) => v.id === r.id)?.status : "unchecked"])
    );
    els.count.textContent = records.filter((r) => r.kind === "message").length;
    render();
    renderPanel();
  }

  function render() {
    renderPill();
    if (!records.length) {
      els.body.innerHTML = `<div class="empty"><p>Nothing sealed yet.</p>
        <p class="small">Scan a chat, then press <strong>Seal as evidence</strong>.</p></div>`;
      return;
    }
    const captures = new Map();
    for (const r of records) {
      if (!captures.has(r.capture_id)) captures.set(r.capture_id, []);
      captures.get(r.capture_id).push(r);
    }
    els.body.innerHTML = [...captures.values()].reverse().map(renderCapture).join("");
  }

  function renderCapture(items) {
    const first = items[0];
    const a = first.analysis_json ? JSON.parse(first.analysis_json) : null;
    const shots = items.filter((r) => r.kind === "screenshot");
    const msgs = items.filter((r) => r.kind === "message");
    return `<article class="capture">
      <header class="capture__head">
        <div>
          <div class="capture__title">${esc(first.conversation)} <span class="muted">· ${esc(first.platform)}</span></div>
          <div class="muted small">${first.incident_date ? `Happened ${esc(dmy(first.incident_date))} · ` : ""}Sealed ${esc(when(first.server_timestamp))} · ${items.length} records</div>
        </div>
        ${a ? `<span class="sev-chip sev-${esc(a.severity)}">${esc(CATEGORY_LABEL[a.category] || a.category)} · ${a.risk_score}</span>` : ""}
      </header>
      ${shots.length ? `<div class="capture__shots">${shots.map(renderShot).join("")}</div>` : ""}
      <ol class="records">${msgs.map(renderRecord).join("")}</ol>
      ${a && a.severity !== "low" ? `<div class="capture__actions"><button type="button" class="btn btn--primary" data-complaint="${esc(first.capture_id)}" data-title="${esc(first.conversation)}">Next: draft complaint →</button></div>` : ""}
    </article>`;
  }

  function renderShot(r) {
    const st = STATUS[statusById[r.id]] || STATUS.unchecked;
    const src = `${API}/evidence/screenshot/${r.screenshot_sha256}`;
    return `<a class="shot-card ${st.cls}" href="${src}" target="_blank" rel="noopener" data-rec="${r.id}" title="${esc(st.label)}">
      <img src="${src}" alt="Saved screenshot" loading="lazy" />
      <span class="shot-card__meta"><span class="rec-status">${st.icon}</span> #${r.id} ${r.screenshot_kind === "chat_capture" ? "Chat capture" : "Original"}</span>
    </a>`;
  }

  function renderRecord(r) {
    const st = STATUS[statusById[r.id]] || STATUS.unchecked;
    return `<li class="record ${st.cls}" data-rec="${r.id}" title="${esc(st.label)}">
      <span class="rec-status">${st.icon}</span>
      <span class="rec-id">#${r.id}</span>
      <div class="rec-main">
        <div class="rec-text"><strong>${esc(r.sender)}</strong>${r.flagged ? ' <span class="flag-tag">flagged</span>' : ""}
          ${r.message_time ? `<span class="muted small">${esc(r.message_time)}</span>` : ""}<br>${esc(r.message_text)}</div>
        <div class="rec-hash" title="SHA-256 ${esc(r.hash)}&#10;prev ${esc(r.prev_hash)}">${short(r.hash)}</div>
      </div>
    </li>`;
  }

  function renderPill() {
    const pill = els.pill;
    if (!records.length) {
      pill.className = "chain-pill";
      pill.textContent = "Empty";
    } else if (!verification) {
      pill.className = "chain-pill unchecked";
      pill.textContent = "Changed since last check";
    } else if (verification.ok) {
      pill.className = "chain-pill ok";
      pill.textContent = `✓ Chain verified · ${verification.count} records`;
    } else {
      pill.className = "chain-pill bad";
      pill.textContent = `✕ Chain broken at #${verification.first_broken_id}`;
    }
  }

  // Walk the chain on screen so the audience can watch each seal being checked.
  async function verify() {
    els.verify.disabled = true;
    els.verify.textContent = "Checking…";
    try {
      const result = await api("/evidence/verify");
      const order = [...records].sort((a, b) => a.id - b.id);
      for (const r of order) {
        statusById[r.id] = result.records.find((v) => v.id === r.id)?.status || "unchecked";
        const el = els.body.querySelector(`[data-rec="${r.id}"]`);
        if (el) {
          const st = STATUS[statusById[r.id]];
          el.classList.remove("ok", "bad", "warn", "unchecked");
          el.classList.add(st.cls, "pulse");
          el.title = st.label;
          el.querySelector(".rec-status").textContent = st.icon;
          if (st.cls === "bad") el.scrollIntoView({ block: "center", behavior: "smooth" });
        }
        await new Promise((res) => setTimeout(res, Math.max(25, 700 / order.length)));
      }
      verification = result;
      renderPill();
      renderPanel();
    } catch (err) {
      window.kavach.toast(`Verify failed: ${err.message}`);
    } finally {
      els.verify.disabled = false;
      els.verify.textContent = "Verify chain";
    }
  }

  function renderPanel() {
    if (!isOpen) return;
    const head = records.length ? records[records.length - 1].hash : null;
    const broken = verification && !verification.ok;
    const brokenRec = broken && records.find((r) => r.id === verification.first_broken_id);
    els.shield.innerHTML = `<div class="verdict vault-panel">
      ${broken ? `<div class="error"><strong>Evidence was altered.</strong><br>Record #${verification.first_broken_id}
        ${brokenRec?.message_text ? `now reads “${esc(brokenRec.message_text)}”` : "changed"}. Everything after it is unverified.</div>` : ""}
      <div>
        <p class="section-title">Why it holds up</p>
        <ul class="explain">
          <li><strong>Sealed</strong> · SHA-256 per message</li>
          <li><strong>Server time</strong> · not the phone's clock</li>
          <li><strong>Chained</strong> · one edit breaks the rest</li>
        </ul>
      </div>
      ${head ? `<div><p class="section-title">Vault fingerprint</p><code class="hash-block">${esc(head)}</code></div>` : ""}
      ${demoMode && records.length ? `<div class="demo-box">
        <p class="section-title">Demo: play the attacker</p>
        <div class="demo-actions">
          <button type="button" class="btn" data-demo="tamper">Edit a message</button>
          <button type="button" class="btn" data-demo="rehash">Edit + re-seal it</button>
        </div>
      </div>` : ""}
    </div>`;
  }

  els.shield.addEventListener("click", async (e) => {
    const action = e.target.closest("[data-demo]")?.dataset.demo;
    if (!action || !isOpen) return;
    try {
      const out = await api("/evidence/demo/tamper", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rehash: action === "rehash" }),
      });
      await load({ verified: false });
      els.body.querySelector(`[data-rec="${out.record_id}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" });
      window.kavach.toast(`Record #${out.record_id} changed to “${out.after}”. Now press Verify chain.`);
    } catch (err) {
      window.kavach.toast(err.message);
    }
  });

  els.body.addEventListener("click", (e) => {
    const b = e.target.closest("[data-complaint]");
    if (b) window.kavachComplaint.open(b.dataset.complaint, b.dataset.title);
  });

  document.querySelector("#clear-vault").addEventListener("click", async () => {
    if (!confirm("Delete all sealed evidence? This can't be undone. Export the PDF report first if you need it.")) return;
    try {
      await api("/evidence", { method: "DELETE" });
      Object.values(window.kavach.results).forEach((r) => delete r.saved);
      await load({ verified: true });
      window.kavach.toast("Vault cleared");
    } catch (err) {
      window.kavach.toast(err.message);
    }
  });

  els.btn.addEventListener("click", open);
  els.verify.addEventListener("click", verify);

  async function refreshCount() {
    try {
      const data = await api("/evidence");
      els.count.textContent = data.records.filter((r) => r.kind === "message").length;
    } catch {
      /* backend offline: the Shield panel already says so */
    }
  }

  (async () => {
    try {
      demoMode = !!(await api("/health")).demo_mode;
    } catch {
      demoMode = false;
    }
    refreshCount();
  })();

  window.kavachVault = { save, open, close };
})();
