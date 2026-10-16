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

  // ---------- open ----------
  async function open(captureId, title) {
    window.kavachImport?.close();
    window.kavachVault?.close();
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
      els.sub.textContent = `${data.complaint.accused.join(", ")} · ${data.complaint.platform} · evidence sealed ${data.complaint.evidence_sealed_on}`;
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
        return `<div class="law ${s.low_confidence ? "low" : ""}">
          <div class="law__head"><span class="law__id">Section ${esc(s.section)} · ${esc(actShort(s.act))}</span></div>
          <div class="law__title">${esc(s.title)}</div>
          <div class="conf"><div class="conf__bar"><span style="width:${pct}%"></span></div>${pct}%</div>
          ${s.low_confidence ? `<div class="law__warn">Consult a lawyer to confirm this applies</div>` : ""}
          <p class="law__why">${esc(s.how_it_applies)}</p>
          <div class="law__meta"><span>${esc(s.punishment)}</span></div>
          <div class="law__meta">
            <a href="${esc(s.source)}" target="_blank" rel="noopener">Source</a>
            ${s.verified_against_official_text ? "<span>✓ verified against official text</span>" : "<span>Not yet checked against the official Gazette</span>"}
          </div>
        </div>`;
      })
      .join("");

    const dropped = data.dropped_citations.length
      ? `<div class="dropped"><strong>${data.dropped_citations.length} citation(s) removed.</strong> The AI mentioned
          ${data.dropped_citations.map((d) => `<code>${esc(d.citation)}</code>`).join(", ")}, which ${data.dropped_citations.length === 1 ? "was" : "were"} not in the retrieved laws.</div>`
      : `<p class="note">✓ Citation check passed: every law in the draft came from the verified corpus.</p>`;

    const rows = data.retrieval
      .map(
        (r) => `<tr><td>${esc(r.id)}</td><td>${r.ranks.bm25 ?? "–"}</td><td>${r.ranks.dense ?? "–"}</td><td>${r.ranks.prior ?? "–"}</td>
          <td>${r.rerank_score ?? "–"}</td><td class="${r.kept ? "kept" : ""}">${r.kept ? "✓" : ""}</td></tr>`
      )
      .join("");
    const how = `<details class="how"><summary>How these laws were chosen</summary>
      <p class="note">Hybrid search over the legal corpus (keyword BM25${data.pipeline.dense_retrieval ? " + meaning-based embeddings" : ""} + threat type), then the AI scored each candidate against the facts. Only sections scoring 40+ are cited.</p>
      <table><thead><tr><th>Section</th><th>BM25</th><th>Meaning</th><th>Type</th><th>AI score</th><th>Cited</th></tr></thead><tbody>${rows}</tbody></table>
      <p class="note">Models: ${esc(Object.values(data.pipeline.models).join(", ") || "none (template)")}</p>
    </details>`;

    return `<p class="section-title">Laws that may apply</p>${laws || '<p class="note">No section passed the confidence check. Ask the police or a lawyer which law applies.</p>'}${dropped}${how}`;
  }

  function fileHtml() {
    const p = data.portal;
    const field = (label, value, long = false) =>
      `<div class="copy-field"><span class="label">${esc(label)}</span><span class="value ${long ? "long" : ""}">${esc(value)}</span>
        <button type="button" class="copy-btn" data-copy="${esc(value)}">Copy</button></div>`;
    const shots = data.complaint.evidence.filter((e) => e.kind === "screenshot");
    const short = p.description_chars < 200;
    return `
      <div class="urgent">
        <a class="u112" href="tel:112">In danger right now? Call 112 <span>→</span></a>
        ${p.money_lost_note ? `<a class="u1930" href="tel:1930">Lost money? Call 1930 now, within hours <span>→</span></a>` : `<a class="u1930" href="tel:1930">Cyber crime helpline 1930 <span>→</span></a>`}
        <a class="u181" href="tel:181">Women helpline 181 <span>→</span></a>
      </div>
      <p class="note">Kavach can't file for you: the portal needs you to log in with your own phone OTP. Everything below is ready to paste.</p>
      <ol class="steps">
        <li>Open the portal and choose <strong>File a complaint</strong>, accept the terms, then log in with your mobile number.
          <a class="btn btn--primary portal-btn" style="margin-top:6px" href="${esc(p.url)}" target="_blank" rel="noopener">Open cybercrime.gov.in ↗</a></li>
        <li>Pick the category. Suggested: <div class="route">${p.route.map(esc).join(" → ")}</div>
          <span class="note">The portal's menus change; choose the closest option you see.</span></li>
        <li>Incident details
          ${field("Date", p.incident_date)}
          ${field("Platform", p.platform)}
          ${field("Description", p.description, true)}
          <span class="note">${p.description_chars} characters${short ? ". The portal needs at least 200, so add more detail." : " (the portal needs at least 200)."}</span></li>
        <li>Suspect details (optional on the portal, but they help)
          ${p.suspect_identifiers.map((i) => field(i.type, i.value)).join("")}</li>
        <li>Upload evidence
          <div class="copy-field"><span class="label">Report</span><span class="value">Evidence report PDF with hashes and Section 63 certificate</span>
            <a class="copy-btn" href="${API}/evidence/report" download>Download</a></div>
          ${shots.map((s) => `<div class="copy-field"><span class="label">${esc(s.id)}</span><span class="value">${esc(s.description)}</span></div>`).join("")}
          <span class="note">Screenshots are also inside the evidence report.</span></li>
        <li>Fill in your own details, review, and submit. <strong>Write down the acknowledgement number</strong> so you can track the complaint.</li>
      </ol>`;
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
