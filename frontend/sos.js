// Discreet SOS (Feature 4, prototype).
//
// A fake incoming call. Tapping Decline declines it. Pressing and holding Decline for
// 2 seconds ALSO declines it, so the screen looks exactly the same, but silently:
//   1. reads the location once (never before),
//   2. encrypts it in the browser with the server's RSA-OAEP public key,
//   3. POSTs it to /sos with her trusted contacts.
// The right-hand panel is the presenter's "behind the scenes" view, not part of the phone.
(() => {
  const API = new URLSearchParams(location.search).get("api") || "";
  const $ = (sel) => document.querySelector(sel);
  const HOLD_MS = 2000;
  const DEMO_LOCATION = { lat: 12.9352, lng: 77.6245, accuracy: 30 }; // Koramangala, Bengaluru
  const SETTINGS_KEY = "kavach.sosSettings";

  const els = {
    btn: $("#sos-btn"),
    view: $("#sos-view"),
    phone: $("#phone"),
    decline: $("#decline-btn"),
    accept: $("#accept-btn"),
    end: $("#end-btn"),
    ring: $("#ring-btn"),
    timer: $("#call-timer"),
    clock: $("#phone-clock"),
    lockTime: $("#lock-time"),
    lockDate: $("#lock-date"),
    missed: $("#missed-text"),
    callerInput: $("#caller-input"),
    locMode: $("#loc-mode"),
    shield: $("#shield-body"),
  };

  let isOpen = false;
  let holdTimer = null;
  let firedThisPress = false;
  let callTimer = null;
  let pollTimer = null;
  let current = null; // last /sos response
  let log = [];

  const esc = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const hhmmss = () => new Date().toLocaleTimeString("en-GB");

  // ---------- settings (localStorage only) ----------
  function loadSettings() {
    try {
      return JSON.parse(localStorage.getItem(SETTINGS_KEY)) || null;
    } catch {
      return null;
    }
  }
  const settings = loadSettings() || {
    caller: "Mom",
    contacts: [{ name: "Mom", phone: "" }, { name: "Riya (roommate)", phone: "" }],
    locMode: "demo",
  };
  function saveSettings() {
    try {
      localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
    } catch {
      /* private mode: settings just won't persist */
    }
  }
  els.callerInput.value = settings.caller;
  els.locMode.value = settings.locMode;
  document.querySelectorAll("[data-contact]").forEach((i) => {
    i.value = settings.contacts[i.dataset.contact]?.[i.dataset.k] || "";
    i.addEventListener("input", () => {
      settings.contacts[i.dataset.contact] ||= { name: "", phone: "" };
      settings.contacts[i.dataset.contact][i.dataset.k] = i.value.trim();
      saveSettings();
    });
  });
  els.callerInput.addEventListener("input", () => {
    settings.caller = els.callerInput.value.trim() || "Mom";
    saveSettings();
    applyCaller();
  });
  els.locMode.addEventListener("change", () => {
    settings.locMode = els.locMode.value;
    saveSettings();
  });

  function applyCaller() {
    const name = settings.caller || "Mom";
    ["#caller-name", "#caller-name-2"].forEach((s) => ($(s).textContent = name));
    ["#caller-initial", "#caller-initial-2"].forEach((s) => ($(s).textContent = name[0].toUpperCase()));
    els.missed.textContent = `Missed call: ${name}`;
  }

  // ---------- phone screens ----------
  function show(screen) {
    els.phone.querySelectorAll(".screen").forEach((s) => (s.hidden = s.dataset.screen !== screen));
  }

  function tickClock() {
    const now = new Date();
    const t = now.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
    els.clock.textContent = t;
    els.lockTime.textContent = t;
    els.lockDate.textContent = now.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
  }
  setInterval(tickClock, 15000);
  tickClock();

  function ring() {
    clearInterval(callTimer);
    applyCaller();
    show("incoming");
  }

  function goHome() {
    clearInterval(callTimer);
    show("home");
  }

  // Hidden gesture: hold Decline. Whether or not SOS fires, the call simply ends.
  function pressStart(e) {
    e.preventDefault();
    firedThisPress = false;
    clearTimeout(holdTimer);
    holdTimer = setTimeout(() => {
      firedThisPress = true;
      navigator.vibrate?.(30); // one tiny buzz so she knows it worked; nothing visible
      goHome();
      sendSos("long_press_decline");
    }, HOLD_MS);
  }
  function pressEnd(e) {
    e.preventDefault();
    clearTimeout(holdTimer);
    if (!firedThisPress) goHome(); // an ordinary tap: just decline
  }
  els.decline.addEventListener("pointerdown", pressStart);
  els.decline.addEventListener("pointerup", pressEnd);
  els.decline.addEventListener("pointerleave", () => clearTimeout(holdTimer));
  els.decline.addEventListener("contextmenu", (e) => e.preventDefault());
  // Keyboard: Enter declines; Shift+Enter is the accessible equivalent of the long press.
  els.decline.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    goHome();
    if (e.shiftKey) sendSos("long_press_decline");
  });

  els.accept.addEventListener("click", () => {
    show("incall");
    const start = Date.now();
    clearInterval(callTimer);
    callTimer = setInterval(() => {
      const s = Math.floor((Date.now() - start) / 1000);
      els.timer.textContent = `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
    }, 500);
  });
  els.end.addEventListener("click", goHome);
  els.ring.addEventListener("click", ring);

  // ---------- the silent SOS ----------
  async function sendSos(trigger) {
    log = [];
    current = null;
    addLog(`Long-press on Decline detected (${HOLD_MS / 1000} s). Screen shows a normal declined call.`);
    renderPanel();
    try {
      const loc = await readLocationOnce();
      addLog(`Location read <strong>once</strong>, now (${loc.source === "gps" ? "device GPS" : "demo location"}), ±${Math.round(loc.accuracy)} m. Never tracked before this moment.`, "ok");

      const { spki } = await (await fetch(`${API}/sos/key`)).json();
      const ciphertext = await encrypt({ lat: loc.lat, lng: loc.lng, accuracy: loc.accuracy }, spki);
      addLog(`Encrypted in the browser with RSA-OAEP-256 (server public key): <code>${esc(ciphertext.slice(0, 28))}…</code>`, "ok");

      const contacts = settings.contacts.filter((c) => c?.name);
      const res = await fetch(`${API}/sos`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ciphertext, trusted_contacts: contacts, trigger, location_source: loc.source }),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
      current = body;
      addLog(body.updated_existing ? "SOS already active: location updated." : "Sent to <code>POST /sos</code>. Server decrypted it.", "ok");
      simulateCalls(body.contacts_notified);
      addLog(`${body.responders_alerted.length} verified responders within 3 km alerted with an <strong>approximate</strong> area only.`, "ok");
      startPolling();
    } catch (err) {
      addLog(`SOS failed: ${esc(err.message)}. In a real emergency call 112.`, "warn");
    }
  }

  function readLocationOnce() {
    if (settings.locMode !== "gps" || !navigator.geolocation) {
      return Promise.resolve({ ...DEMO_LOCATION, source: "demo" });
    }
    return new Promise((resolve) => {
      navigator.geolocation.getCurrentPosition(
        (p) => resolve({ lat: p.coords.latitude, lng: p.coords.longitude, accuracy: p.coords.accuracy, source: "gps" }),
        (err) => {
          addLog(`GPS unavailable (${esc(err.message)}); using the demo location.`, "warn");
          resolve({ ...DEMO_LOCATION, source: "demo" });
        },
        { enableHighAccuracy: true, timeout: 8000, maximumAge: 0 }
      );
    });
  }

  async function encrypt(obj, spkiB64) {
    const der = Uint8Array.from(atob(spkiB64), (c) => c.charCodeAt(0));
    const key = await crypto.subtle.importKey("spki", der, { name: "RSA-OAEP", hash: "SHA-256" }, false, ["encrypt"]);
    const ct = await crypto.subtle.encrypt({ name: "RSA-OAEP" }, key, new TextEncoder().encode(JSON.stringify(obj)));
    return btoa(String.fromCharCode(...new Uint8Array(ct)));
  }

  // Calls are simulated in the prototype; the sequence shows what the real app would do.
  function simulateCalls(contacts) {
    if (!contacts.length) {
      addLog("No trusted contacts set up: add them in Setup.", "warn");
      return;
    }
    contacts.forEach((c, i) => {
      setTimeout(() => addLog(`Calling ${esc(c.name)}… and sending an SMS with the live-location link <span class="muted">(simulated)</span>`), 600 + i * 1400);
      setTimeout(() => addLog(i === 0 ? `${esc(c.name)}: no answer, moving on <span class="muted">(simulated)</span>` : `${esc(c.name)}: connected <span class="muted">(simulated)</span>`, i === 0 ? "warn" : "ok"), 1300 + i * 1400);
    });
  }

  function startPolling() {
    clearInterval(pollTimer);
    pollTimer = setInterval(async () => {
      if (!current || !isOpen) return;
      try {
        const res = await fetch(`${API}/sos/${current.sos_id}`);
        if (!res.ok) return;
        const fresh = await res.json();
        const before = JSON.stringify(current.responders_alerted.map((r) => r.status));
        current = { ...current, ...fresh };
        if (JSON.stringify(current.responders_alerted.map((r) => r.status)) !== before) {
          current.responders_alerted.filter((r) => r.status === "accepted").forEach((r) => {
            if (!log.some((l) => l.text.includes(`${r.name} accepted`))) addLog(`${esc(r.name)} accepted and now sees your exact location.`, "ok");
          });
          renderPanel();
        }
      } catch {
        /* keep polling */
      }
    }, 3000);
  }

  async function imSafe() {
    if (!current) return;
    await fetch(`${API}/sos/${current.sos_id}/cancel`, { method: "POST" });
    clearInterval(pollTimer);
    addLog("Marked safe: live link and responders' exact view switched off.", "ok");
    current.status = "cancelled";
    renderPanel();
  }

  // ---------- presenter panel ----------
  function addLog(text, cls = "") {
    log.push({ t: hhmmss(), text, cls });
    renderPanel();
  }

  function renderPanel() {
    if (!isOpen) return;
    const steps = log.map((l) => `<li><time>${l.t}</time><span class="${l.cls}">${l.text}</span></li>`).join("");
    const c = current;
    const live = c && c.status === "active";
    els.shield.innerHTML = `<div class="verdict no-anim">
      <p class="section-title">Behind the scenes</p>
      ${log.length ? `<ol class="sos-log">${steps}</ol>` : `<p class="note">Ring the fake call, then press and hold <strong>Decline</strong> for 2 seconds. A quick tap just declines.</p>`}
      ${c ? `
        <div>
          <p class="section-title">Trusted contacts</p>
          <div class="who">${c.contacts_notified.map((x) => `<div class="who__row"><span>${esc(x.name)}<small>${esc(x.channel)}</small></span></div>`).join("") || '<p class="note">None set up.</p>'}</div>
          ${live ? `<a class="btn btn--block portal-btn" style="margin-top:6px" href="${esc(c.track_path)}" target="_blank" rel="noopener">Open their live-location link ↗</a>
          <p class="note">Private link, expires in ${c.track_expires_in_min / 60} hours or when she marks herself safe.</p>` : ""}
        </div>
        <div>
          <p class="section-title">Responders alerted (approximate area only)</p>
          <div class="who">
            ${c.responders_alerted.map((r) => `<div class="who__row"><span>${esc(r.name)}<small>${esc(r.role)} · ~${r.distance_km} km</small></span><span class="state ${r.status}">${r.status}</span></div>`).join("")}
            ${c.responders_excluded.map((r) => `<div class="who__row"><span>${esc(r.name)}<small>${esc(r.reason)}</small></span><span class="state excluded">not alerted</span></div>`).join("")}
          </div>
          <a class="btn btn--block portal-btn" style="margin-top:6px" href="/responder.html" target="_blank" rel="noopener">Open the responder view ↗</a>
        </div>
        ${live ? `<button type="button" class="btn btn--block safe-btn" data-safe>I'm safe: stop sharing</button>` : `<p class="note">SOS ended.</p>`}` : ""}
    </div>`;
  }

  els.shield.addEventListener("click", (e) => {
    if (isOpen && e.target.closest("[data-safe]")) imSafe();
  });

  // ---------- open / close ----------
  function open() {
    window.kavachImport?.close();
    window.kavachVault?.close();
    window.kavachComplaint?.close();
    isOpen = true;
    document.body.classList.add("sosing");
    els.view.hidden = false;
    els.btn.classList.add("active");
    window.kavach.deselect("");
    ring();
    renderPanel();
  }

  function close() {
    if (!isOpen) return;
    isOpen = false;
    document.body.classList.remove("sosing");
    els.view.hidden = true;
    els.btn.classList.remove("active");
  }

  els.btn.addEventListener("click", open);
  applyCaller();
  window.kavachSos = { open, close };
})();
