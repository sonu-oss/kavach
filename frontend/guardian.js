// Kavach Guardian: the AI agent box at the bottom of the side panel.
//
// Sends her request (and the chat she has open, if any) to POST /agent. The
// backend agent picks Kavach's tools itself; this file shows the steps it took
// and hands its results to the rest of the app (verdict, Vault, complaint, SOS).
(() => {
  const API = new URLSearchParams(location.search).get("api") || "";
  const $ = (sel) => document.querySelector(sel);
  const els = {
    form: $("#guardian-form"),
    input: $("#guardian-input"),
    go: $("#guardian-go"),
    shield: $("#shield-body"),
    chatArea: $("#chat-area"),
  };
  const TOOL_LABEL = {
    analyze_chat: "Checked the chat",
    save_to_vault: "Sealed evidence in the Vault",
    find_laws: "Looked up the laws",
    draft_complaint: "Drafted a complaint",
    search_evidence: "Searched sealed evidence",
    suggest_sos: "Suggested SOS",
  };
  const SOURCE_LABEL = {
    agent: (m) => `AI agent · ${m || "Groq"}`,
    planned: () => "Fixed plan (AI unavailable)",
    "agent+planned": (m) => `AI agent · ${m || "Groq"}, finished by the fixed plan`,
  };
  const esc = (s) => window.kavach.esc(s ?? "");
  let busy = false;
  let last = null; // last /agent response, for the buttons

  // The chat on screen, or null when she is on the Vault / home / import screens.
  function openChat() {
    const c = window.kavach.active();
    return c && !els.chatArea.closest("[hidden]") ? c : null;
  }

  async function ask(goal) {
    if (busy || !goal.trim()) return;
    busy = true;
    els.go.disabled = true;
    const conv = openChat();
    window.kavachMobile?.show("panel");
    els.shield.innerHTML = `<div class="loading"><div class="spinner"></div>Kavach Guardian is working on it…</div>`;
    try {
      const body = { goal: goal.trim(), self_handle: "me", conversation: null };
      if (conv) {
        body.conversation = {
          name: conv.name,
          platform: conv.platform || (conv.source === "screenshot" ? "Screenshot import" : "ChatSpace (demo)"),
          url: conv.source === "screenshot" ? null : `${location.origin}${location.pathname}#chat=${conv.id}`,
          incident_date: conv.incident_date || null,
          messages: conv.messages.map((m) => ({ id: m.id, sender: m.sender, text: m.text, time: m.time || "" })),
          // Only sealed if the agent decides to save the chat.
          screenshots: await window.kavachVault.screenshotsFor(conv, els.chatArea).catch(() => []),
        };
      }
      const res = await fetch(`${API}/agent`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `HTTP ${res.status}`);
      last = { ...data, conv };
      if (conv && data.analysis) {
        // The panel shows the same verdict and sealed state as if she had pressed the buttons herself.
        window.kavach.adoptResult(conv.id, { ...data.analysis, ...(data.saved ? { saved: data.saved } : {}) });
      }
      if (data.saved) window.kavachVault.refreshCount?.();
      render(data);
    } catch (err) {
      els.shield.innerHTML = `<div class="error"><strong>Kavach Guardian couldn't finish.</strong><br>${esc(err.message)}</div>`;
    } finally {
      busy = false;
      els.go.disabled = false;
    }
  }

  function render(d) {
    const steps = d.steps
      .map(
        (s, i) => `<li class="${s.ok ? "" : "fail"}" style="animation-delay:${i * 120}ms">
          <span class="agent__tool">${esc(TOOL_LABEL[s.tool] || s.tool)}${s.ok ? "" : " (skipped)"}</span>
          <span class="agent__sum">${esc(s.summary)}</span>
        </li>`
      )
      .join("");
    const hits = d.steps.filter((s) => s.tool === "search_evidence").flatMap((s) => s.results || []);
    const hitsHtml = hits.length
      ? `<p class="section-title">Matching sealed messages</p>
         <ul class="agent__hits">${hits
           .map(
             (h) => `<li>“${esc(h.text)}”
               <div class="muted">${esc(h.sender)} · ${esc(h.conversation)}${h.time ? ` · ${esc(h.time)}` : ""} · match ${Math.round(h.similarity * 100)}%${h.tamper_check === "ok" ? " · ✓ untampered" : " · ⚠ failed tamper check"}</div>
             </li>`
           )
           .join("")}</ul>`
      : "";
    const high = d.analysis?.severity === "high";

    els.shield.innerHTML = `
      <div class="agent">
        <p class="agent__answer">${esc(d.answer)}</p>
        ${d.complaint ? `<button class="btn btn--primary btn--block" data-agent="complaint">Review the drafted complaint →</button>` : ""}
        ${d.sos_suggested ? `<button class="btn btn--block agent__sos" data-agent="sos">🆘 Open SOS (${esc(d.sos_suggested)})</button>` : ""}
        ${d.analysis && last.conv ? `<button class="btn btn--block" data-agent="verdict">Show the full risk check</button>` : ""}
        ${hitsHtml}
        ${steps ? `<p class="section-title">What the agent did</p><ol class="agent__steps">${steps}</ol>` : ""}
        ${high ? window.kavach.callsHtml() : ""}
        <p class="meta-line">${esc(SOURCE_LABEL[d.source]?.(d.model) || d.source)} · ${d.steps.length} step${d.steps.length === 1 ? "" : "s"}</p>
      </div>`;
  }

  els.shield.addEventListener("click", (e) => {
    const action = e.target.closest("[data-agent]")?.dataset.agent;
    if (!action || !last) return;
    if (action === "complaint") window.kavachComplaint.open(last.complaint.capture_id, last.conv?.name || "", last.complaint);
    if (action === "sos") window.kavachSos.open();
    if (action === "verdict" && last.conv) window.kavach.adoptResult(last.conv.id, window.kavach.results[last.conv.id]);
  });

  els.form.addEventListener("submit", (e) => {
    e.preventDefault();
    ask(els.input.value);
  });
  els.form.addEventListener("click", (e) => {
    const chip = e.target.closest("[data-goal]");
    if (!chip) return;
    els.input.value = chip.dataset.goal;
    ask(chip.dataset.goal);
  });
})();
