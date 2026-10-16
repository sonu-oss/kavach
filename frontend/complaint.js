// Complaint Assistant (Feature 3).
//
// Drafts a complaint from a sealed Vault capture via POST /complaint, lets the
// victim fill in her own details (kept in this browser only) and edit the
// letter, then hands off to cybercrime.gov.in with every field ready to paste.
(() => {
  const API = new URLSearchParams(location.search).get("api") || "";
  const $ = (sel) => document.querySelector(sel);
  const els = {
    view: $("#complaint-view"),
    sub: $("#complaint-sub"),
    pill: $("#complaint-pill"),
    loading: $("#complaint-loading"),
    status: $("#complaint-status"),
    letter: $("#letter"),
    details: $("#my-details"),
    copy: $("#copy-letter"),
    pdf: $("#letter-pdf"),
    file: $("#file-report"),
    shield: $("#shield-body"),
  };
  const DETAILS_KEY = "kavach.myDetails";
  const PLACEHOLDER = {
    name: "[Your full name]",
    phone: "[Your phone number]",
    email: "[Your email]",
    city: "[City]",
    address: "[Your address]",
  };

  let data = null; // last /complaint response
  let tab = "laws";
  let edited = false;
  let isOpen = false;
  let seq = 0;

  const esc = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const actShort = (act) => (act.startsWith("Bharatiya") ? "BNS, 2023" : "IT Act, 2000");

  // ---------- her details: localStorage only ----------
  function loadDetails() {
    try {
      return JSON.parse(localStorage.getItem(DETAILS_KEY)) || {};
    } catch {
      return {};
    }
  }
  function saveDetails(d) {
    try {
      localStorage.setItem(DETAILS_KEY, JSON.stringify(d));
    } catch {
      /* private mode: details just won't be remembered */
    }
  }
  const inputs = [...els.details.querySelectorAll("input[data-field]")];
  const details = loadDetails();
  inputs.forEach((i) => (i.value = details[i.dataset.field] || ""));
  inputs.forEach((i) =>
    i.addEventListener("input", () => {
      const before = { ...details };
      details[i.dataset.field] = i.value.trim();
      saveDetails(details);
      if (!data) return;
      if (!edited) {
        els.letter.value = composeLetter();
      } else {
        // She has edited the letter: only swap the field she just changed.
        const f = i.dataset.field;
        const old = before[f] || PLACEHOLDER[f];
        if (old && els.letter.value.includes(old)) els.letter.value = els.letter.value.split(old).join(details[f] || PLACEHOLDER[f]);
      }
    })
  );
  els.letter.addEventListener("input", () => (edited = true));

  // Wipe her details from this browser (e.g. after a demo on a shared laptop).
  document.querySelector("#clear-details").addEventListener("click", () => {
    const before = { ...details };
    Object.keys(details).forEach((k) => delete details[k]);
    inputs.forEach((i) => (i.value = ""));
    try {
      localStorage.removeItem(DETAILS_KEY);
    } catch {
      /* nothing stored */
    }
    if (data) {
      if (!edited) {
        els.letter.value = composeLetter();
      } else {
        for (const [f, v] of Object.entries(before)) {
          if (v) els.letter.value = els.letter.value.split(v).join(PLACEHOLDER[f]);
        }
      }
    }
    window.kavach.toast("Your details were cleared from this device");
  });

  // ---------- open ----------
  async function open(captureId, title) {
    window.kavachImport?.close();
    window.kavachVault?.close();
    window.kavachSos?.close();
    window.kavachFlow?.set(4);
    isOpen = true;
    document.body.classList.add("complaining");
    els.view.hidden = false;
    window.kavach.deselect("");
    els.sub.textContent = title || "";
    els.pill.className = "chain-pill";
    els.pill.textContent = "Drafting…";
    els.letter.hidden = true;
    els.loading.hidden = false;
    edited = false;
    data = null;
    tab = "laws";
    if (!Object.values(details).some(Boolean)) els.details.open = true;

    const mySeq = ++seq;
    const steps = ["Finding the laws that apply…", "Checking each law against the facts…", "Writing your complaint…", "Checking every citation…"];
    let si = 0;
    els.status.textContent = steps[0];
    const ticker = setInterval(() => (els.status.textContent = steps[Math.min(++si, steps.length - 1)]), 1400);
    els.shield.innerHTML = `<div class="loading"><div class="spinner"></div>Preparing the legal sections…</div>`;

    try {
      const res = await fetch(`${API}/complaint`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ capture_id: captureId }),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
      if (mySeq !== seq || !isOpen) return;
      data = body;
      els.sub.textContent = `${data.complaint.accused.join(", ")} · ${data.complaint.platform}${data.complaint.incident_date ? ` · happened ${data.complaint.incident_date}` : ""} · evidence sealed ${data.complaint.evidence_sealed_on}`;
      els.pill.className = `chain-pill ${data.pipeline.source === "llm" ? "ai" : "unchecked"}`;
      els.pill.textContent = data.pipeline.source === "llm" ? `AI drafted · ${data.sections.length} laws cited` : "Template draft (AI unavailable)";
      els.letter.value = composeLetter();
      els.letter.hidden = false;
      renderPanel();
    } catch (err) {
      if (mySeq !== seq) return;
      els.pill.className = "chain-pill bad";
      els.pill.textContent = "Couldn't draft";
      els.shield.innerHTML = `<div class="error"><strong>Couldn't draft the complaint.</strong><br>${esc(err.message)}</div>`;
    } finally {
      clearInterval(ticker);
      if (mySeq === seq) els.loading.hidden = true;
    }
  }

  function close() {
    if (!isOpen) return;
    isOpen = false;
    seq++;
    document.body.classList.remove("complaining");
    els.view.hidden = true;
  }

  // ---------- the letter ----------
  function composeLetter() {
    const c = data.complaint;
    const d = (f) => details[f] || PLACEHOLDER[f];
    const today = new Date().toLocaleDateString("en-GB");
    const lines = [];
    lines.push("To,", "The Officer-in-Charge,", "Cyber Crime Police Station,", d("city"), "", `Date: ${today}`, "");
    lines.push(`Subject: ${c.subject}`, "", "Respected Sir/Madam,", "");
    lines.push(`I, ${d("name")}, residing at ${d("address")}, wish to report the following incident and request action.`, "");
    if (c.incident_date) lines.push(`Date of incident: ${c.incident_date}`, `Platform: ${c.platform}`, "");
    lines.push(c.incident_summary, "");

    if (c.timeline.length) {
      lines.push("Timeline of events (times as shown in the chat):");
      c.timeline.forEach((t, i) => {
        const ev = t.evidence_ids?.length ? ` (evidence ${t.evidence_ids.join(", ")})` : "";
        lines.push(`${i + 1}. ${t.when ? `${t.when}: ` : ""}${t.what}${ev}`);
      });
      lines.push("");
    }

    if (data.sections.length) {
      lines.push("Legal provisions that may apply (suggested, to be confirmed by the police or a lawyer):");
      data.sections.forEach((s) => {
        const caveat = s.low_confidence ? " [to be confirmed by a lawyer]" : "";
        lines.push(`- Section ${s.section} of the ${actShort(s.act)}, ${s.title}: ${s.how_it_applies}${caveat}`);
      });
      lines.push("");
    }

    lines.push("Evidence (sealed in the Kavach Evidence Vault with SHA-256 and a server timestamp):");
    c.evidence.forEach((e) => {
      lines.push(`- ${e.id}: ${e.description} [SHA-256 ${e.sha256.slice(0, 16)}…]${e.verified ? "" : " (FAILED VERIFICATION)"}`);
    });
    lines.push(
      `Evidence chain status: ${c.chain_ok ? "verified, unaltered" : "BROKEN, see attached report"}. Chain head: ${c.chain_head}.`,
      "The full evidence report with complete hash values and a Section 63 BSA certificate is attached.",
      ""
    );

    if (c.relief_sought.length) {
      lines.push("I therefore request you to:");
      c.relief_sought.forEach((r, i) => lines.push(`${i + 1}. ${r}`));
      lines.push("");
    }
    lines.push("I declare that the information given above is true and correct to the best of my knowledge.", "");
    lines.push("Yours faithfully,", d("name"), `Phone: ${d("phone")}`, `Email: ${d("email")}`);
    return lines.join("\n");
  }

  els.copy.addEventListener("click", () => copy(els.letter.value, "Letter copied"));

  els.pdf.addEventListener("click", async () => {
    if (!data) return;
    els.pdf.disabled = true;
    try {
      const res = await fetch(`${API}/complaint/pdf`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          capture_id: data.capture_id,
          letter: els.letter.value,
          sections: data.sections.map(({ id, act, section, title, confidence, low_confidence }) => ({
            id, act, section, title, confidence, low_confidence,
          })),
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const url = URL.createObjectURL(await res.blob());
      const a = Object.assign(document.createElement("a"), { href: url, download: "kavach-complaint.pdf" });
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
    } catch (err) {
      window.kavach.toast(`PDF failed: ${err.message}`);
    } finally {
      els.pdf.disabled = false;
    }
  });

  els.file.addEventListener("click", () => {
    tab = "file";
    renderPanel();
  });

  // ---------- right panel ----------
  function renderPanel() {
    if (!data || !isOpen) return;
    els.shield.innerHTML = `<div class="verdict">
      <div class="tabs" role="tablist">
        <button class="tab ${tab === "laws" ? "active" : ""}" data-tab="laws" role="tab">Laws cited</button>
        <button class="tab ${tab === "file" ? "active" : ""}" data-tab="file" role="tab">File the report</button>
      </div>
      ${tab === "laws" ? lawsHtml() : fileHtml()}
    </div>`;
  }

  function lawsHtml() {
    const laws = data.sections
      .map((s) => {
        const pct = Math.round(s.confidence * 100);
        return `<div class="law ${s.low_confidence ? "low" : ""}" title="${esc(s.punishment)}">
          <div class="law__head"><span class="law__id">Sec ${esc(s.section)} · ${esc(actShort(s.act))}</span><span class="law__title">${esc(s.title)}</span></div>
          <div class="conf"><div class="conf__bar"><span style="width:${pct}%"></span></div>${pct}%</div>
          <p class="law__why">${esc(s.how_it_applies)}</p>
          ${s.low_confidence ? `<div class="law__warn">Ask a lawyer to confirm</div>` : ""}
          <div class="law__meta">
            <a href="${esc(s.source)}" target="_blank" rel="noopener">Official text</a>
            ${s.verified_against_official_text ? "<span>✓ Verified</span>" : "<span>Not verified</span>"}
          </div>
        </div>`;
      })
      .join("");

    const dropped = data.dropped_citations.length
      ? `<div class="dropped">Removed unverified citation${data.dropped_citations.length === 1 ? "" : "s"}: ${data.dropped_citations.map((d) => `<code>${esc(d.citation)}</code>`).join(", ")}</div>`
      : `<p class="note">✓ Every cited law is from the verified list.</p>`;

    const rows = data.retrieval
      .map(
        (r) => `<tr><td>${esc(r.id)}</td><td>${r.ranks.bm25 ?? "–"}</td><td>${r.ranks.dense ?? "–"}</td><td>${r.ranks.prior ?? "–"}</td>
          <td>${r.rerank_score ?? "–"}</td><td class="${r.kept ? "kept" : ""}">${r.kept ? "✓" : ""}</td></tr>`
      )
      .join("");
    const how = `<details class="how"><summary>How these were chosen</summary>
      <p class="note">Keyword + meaning search, then AI scoring. 40+ is cited.</p>
      <table><thead><tr><th>Section</th><th>BM25</th><th>Meaning</th><th>Type</th><th>AI score</th><th>Cited</th></tr></thead><tbody>${rows}</tbody></table>
      <p class="note">Models: ${esc(Object.values(data.pipeline.models).join(", ") || "none (template)")}</p>
    </details>`;

    return `<p class="section-title">Laws that may apply</p>${laws || '<p class="note">No law matched with confidence. Ask the police or a lawyer.</p>'}${dropped}${how}`;
  }

  function fileHtml() {
    const p = data.portal;
    const field = (label, value, long = false) =>
      `<div class="copy-field"><span class="label">${esc(label)}</span><span class="value ${long ? "long" : ""}">${esc(value)}</span>
        <button type="button" class="copy-btn" data-copy="${esc(value)}">Copy</button></div>`;
    const short = p.description_chars < 200;
    return `
      ${window.kavach.callsHtml()}
      ${p.money_lost_note ? `<p class="note warn-note">Lost money? Call 1930 now: the first hours matter.</p>` : ""}
      <ol class="steps">
        <li><strong>Open the portal</strong> · log in with your phone OTP
          <a class="btn btn--primary btn--block portal-btn" style="margin-top:8px" href="${esc(p.url)}" target="_blank" rel="noopener">Open cybercrime.gov.in ↗</a></li>
        <li><strong>Category</strong> <div class="route">${p.route.map(esc).join(" → ")}</div></li>
        <li><strong>Incident</strong>
          ${field("Date", p.incident_date)}
          ${field("App", p.platform)}
          ${field("Details", p.description, true)}
          ${short ? `<span class="note warn-note">Add more detail: the portal needs 200+ characters.</span>` : ""}</li>
        <li><strong>Suspect</strong>
          ${p.suspect_identifiers.map((i) => field(i.type, i.value)).join("")}</li>
        <li><strong>Evidence</strong>
          <div class="copy-field"><span class="label">PDF</span><span class="value">Sealed evidence report</span>
            <a class="copy-btn" href="${API}/evidence/report" download>Download</a></div></li>
        <li><strong>Submit</strong> · save the acknowledgement number</li>
      </ol>
      <p class="note">You submit it yourself. Kavach can't log in for you.</p>`;
  }

  els.shield.addEventListener("click", (e) => {
    if (!isOpen) return;
    const t = e.target.closest("[data-tab]");
    if (t) {
      tab = t.dataset.tab;
      renderPanel();
    }
    const c = e.target.closest("[data-copy]");
    if (c) copy(c.dataset.copy, "Copied");
  });

  async function copy(text, msg) {
    try {
      await navigator.clipboard.writeText(text);
      window.kavach.toast(msg);
    } catch {
      window.kavach.toast("Copy failed: select the text and copy it manually");
    }
  }

  window.kavachComplaint = { open, close };
})();
