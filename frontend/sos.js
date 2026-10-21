// Discreet SOS (Feature 4).
//
// A fake incoming call. Tapping Decline declines it. Pressing and holding Decline for
// 2 seconds ALSO declines it, so the screen looks exactly the same, but silently:
//   1. reads the location (real GPS, then live updates while the SOS is on),
//   2. encrypts it in the browser with the server's RSA-OAEP public key,
//   3. POSTs it to /sos. The server alerts her trusted contacts (Telegram live pin + email)
//      and nearby verified responders (Telegram with Accept / Decline).
// The right-hand panel is the presenter's "behind the scenes" view, not part of the phone.
(() => {
  const API = new URLSearchParams(location.search).get("api") || "";
  const $ = (sel) => document.querySelector(sel);
  const HOLD_MS = 2000;
  const LIVE_EVERY_MS = 15000; // send a live position at most this often
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
    herName: $("#her-name"),
    channels: $("#channels-status"),
    contactList: $("#contact-setup"),
    addContact: $("#add-contact"),
    shield: $("#shield-body"),
  };

  let isOpen = false;
  let holdTimer = null;
  let firedThisPress = false;
  let callTimer = null;
  let pollTimer = null;
  let current = null; // last /sos response
  let log = [];
  let demoMode = false;
  let liveWatch = null;
  let lastLiveSent = 0;
  let spkiCache = null;
  let setup = { contacts: [], telegram: { on: false, bot: null }, email_on: false };

  fetch(`${API}/health`)
    .then((r) => r.json())
    .then((h) => (demoMode = !!h.demo_mode))
    .catch(() => {});

  const esc = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const hhmmss = () => new Date().toLocaleTimeString("en-GB");

  async function api(path, opts = {}) {
    const res = await fetch(`${API}${path}`, {
      ...opts,
      headers: opts.body ? { "Content-Type": "application/json" } : undefined,
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
    return body;
  }

  // ---------- this-device settings (caller name, location mode) ----------
  function loadSettings() {
    try {
      return JSON.parse(localStorage.getItem(SETTINGS_KEY)) || {};
    } catch {
      return {};
    }
  }
  const settings = { caller: "Mom", locMode: "gps", ...loadSettings() };
  function saveSettings() {
    try {
      localStorage.setItem(SETTINGS_KEY, JSON.stringify({ caller: settings.caller, locMode: settings.locMode }));
    } catch {
      /* private mode: settings just won't persist */
    }
  }
  els.callerInput.value = settings.caller;
  els.locMode.value = settings.locMode;
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

  // ---------- setup: her name + trusted contacts (saved on the server) ----------
  async function loadSetup() {
    try {
      setup = await api("/sos/setup");
    } catch {
      els.channels.textContent = "Kavach is offline";
      return;
    }
    if (document.activeElement !== els.herName) els.herName.value = setup.name === "Someone you know" ? "" : setup.name;
    els.channels.textContent = `Telegram ${setup.telegram.on ? "on" : "not set up"} · Email ${setup.email_on ? "on" : "not set up"}`;
    renderContacts();
  }

  function renderContacts() {
    if (!setup.contacts.length) {
      els.contactList.innerHTML = `<li class="note">Add at least one person who should get your SOS.</li>`;
      return;
    }
    els.contactList.innerHTML = setup.contacts
      .map((c) => {
        const link = setup.telegram.bot ? `https://t.me/${setup.telegram.bot}?start=${c.link_code}` : null;
        const tg = c.telegram_linked
          ? `<span class="ok-tag">Telegram ✓</span>`
          : link
            ? `<button type="button" class="link-btn" data-qr="${esc(link)}" data-who="${esc(c.name)}">Connect Telegram</button>`
            : `<span class="muted small">Telegram not set up</span>`;
        return `<li class="contact-row">
          <div><strong>${esc(c.name)}</strong><small>${esc(c.email || "no email")}</small></div>
          ${tg}
          <button type="button" class="row-del" data-del="${esc(c.id)}" title="Remove">×</button>
        </li>`;
      })
      .join("");
  }

  let nameTimer = null;
  els.herName.addEventListener("input", () => {
    clearTimeout(nameTimer);
    nameTimer = setTimeout(() => {
      const name = els.herName.value.trim();
      if (name) api("/sos/profile", { method: "PUT", body: JSON.stringify({ name }) }).catch(() => {});
    }, 500);
  });

  els.addContact.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(els.addContact);
    try {
      await api("/people", {
        method: "POST",
        body: JSON.stringify({ kind: "contact", name: f.get("name"), email: f.get("email") || "" }),
      });
      els.addContact.reset();
      await loadSetup();
    } catch (err) {
      window.kavach.toast(`Couldn't add: ${err.message}`);
    }
  });

  els.contactList.addEventListener("click", async (e) => {
    const del = e.target.closest("[data-del]");
    if (del) {
      await api(`/people/${del.dataset.del}`, { method: "DELETE" }).catch(() => {});
      return loadSetup();
    }
    const qr = e.target.closest("[data-qr]");
    if (qr) showQr(qr.dataset.qr, qr.dataset.who);
  });

  // Each contact scans this (or gets the link) and taps Start once.
  function showQr(link, who) {
    let svg = "";
    try {
      const q = qrcode(0, "M");
      q.addData(link);
      q.make();
      svg = q.createSvgTag({ cellSize: 5, margin: 2, scalable: true });
    } catch {
      /* QR library not loaded: the link still works */
    }
    const box = document.createElement("div");
    box.className = "qr-modal";
    box.innerHTML = `<div class="qr-card">
      <p class="section-title">Connect ${esc(who)} on Telegram</p>
      <div class="qr">${svg}</div>
      <p class="note">${esc(who)} scans this with their phone camera, or open the link on their phone, and taps <strong>Start</strong>.</p>
      <div class="qr-actions">
        <button type="button" class="btn" data-copy>Copy link</button>
        <button type="button" class="btn btn--primary" data-close>Done</button>
      </div>
    </div>`;
    box.addEventListener("click", async (e) => {
      if (e.target.closest("[data-copy]")) {
        try {
          await navigator.clipboard.writeText(link);
          window.kavach.toast("Link copied");
        } catch {
          window.prompt("Copy this link", link);
        }
      }
      if (e.target === box || e.target.closest("[data-close]")) {
        box.remove();
        loadSetup();
      }
    });
    document.body.append(box);
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
    addLog(`Decline held ${HOLD_MS / 1000} s. Screen shows a normal declined call.`);
    try {
      const loc = await readLocationOnce();
      addLog(`Location read (${loc.source === "gps" ? "GPS" : "demo"}, ±${Math.round(loc.accuracy)} m).`, "ok");
      const ciphertext = await encrypt({ lat: loc.lat, lng: loc.lng, accuracy: loc.accuracy });
      addLog(`Encrypted on the phone: <code>${esc(ciphertext.slice(0, 20))}…</code>`, "ok");

      current = await api("/sos", {
        method: "POST",
        body: JSON.stringify({ ciphertext, trigger, location_source: loc.source }),
      });
      if (current.updated_existing) {
        addLog("SOS already active: location updated.", "ok");
      } else {
        const n = current.contacts_notified.length;
        addLog(n ? `Alerting ${n} trusted contact${n === 1 ? "" : "s"}…` : "No trusted contacts set up: add them in Setup.", n ? "ok" : "warn");
        addLog(`${current.responders_alerted.length} nearby responder${current.responders_alerted.length === 1 ? "" : "s"} alerted (approximate area only).`, "ok");
      }
      if (loc.source === "gps") startLive();
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

  // Keep her position updated while the SOS is on (only while this page stays open).
  function startLive() {
    stopLive();
    if (!navigator.geolocation) return;
    liveWatch = navigator.geolocation.watchPosition(
      async (p) => {
        if (!current || current.status !== "active" || Date.now() - lastLiveSent < LIVE_EVERY_MS) return;
        lastLiveSent = Date.now();
        try {
          const ciphertext = await encrypt({ lat: p.coords.latitude, lng: p.coords.longitude, accuracy: p.coords.accuracy });
          await api(`/sos/${current.sos_id}/location`, { method: "POST", body: JSON.stringify({ ciphertext }) });
        } catch {
          /* next update will try again */
        }
      },
      () => {},
      { enableHighAccuracy: true, maximumAge: 5000 }
    );
    lastLiveSent = Date.now();
    addLog("Live location on: updates every 15 s while this screen stays open.", "ok");
  }

  function stopLive() {
    if (liveWatch !== null) navigator.geolocation.clearWatch(liveWatch);
    liveWatch = null;
  }

  async function encrypt(obj) {
    spkiCache ||= (await api("/sos/key")).spki;
    const der = Uint8Array.from(atob(spkiCache), (c) => c.charCodeAt(0));
    const key = await crypto.subtle.importKey("spki", der, { name: "RSA-OAEP", hash: "SHA-256" }, false, ["encrypt"]);
    const ct = await crypto.subtle.encrypt({ name: "RSA-OAEP" }, key, new TextEncoder().encode(JSON.stringify(obj)));
    return btoa(String.fromCharCode(...new Uint8Array(ct)));
  }

  function startPolling() {
    clearInterval(pollTimer);
    pollTimer = setInterval(async () => {
      if (!current || !isOpen) return;
      try {
        const fresh = await api(`/sos/${current.sos_id}`);
        const snapshot = (c) => JSON.stringify([c.status, c.responders_alerted, c.contacts_notified]);
        const before = snapshot(current);
        const acceptedBefore = new Set(current.responders_alerted.filter((r) => r.status === "accepted").map((r) => r.id));
        current = { ...current, ...fresh };
        current.responders_alerted
          .filter((r) => r.status === "accepted" && !acceptedBefore.has(r.id))
          .forEach((r) => addLog(`${esc(r.name)} accepted: sees your exact location.`, "ok"));
        if (current.status !== "active") {
          clearInterval(pollTimer);
          stopLive();
        }
        if (snapshot(current) !== before) renderPanel();
      } catch {
        /* keep polling */
      }
    }, 3000);
  }

  async function imSafe() {
    if (!current) return;
    stopLive();
    await api(`/sos/${current.sos_id}/cancel`, { method: "POST" }).catch(() => {});
    clearInterval(pollTimer);
    addLog("Marked safe. Everyone was told and sharing stopped.", "ok");
    current.status = "cancelled";
    renderPanel();
  }

  // ---------- presenter panel ----------
  function addLog(text, cls = "") {
    log.push({ t: hhmmss(), text, cls });
    renderPanel();
  }

  const deliveryTag = (label, status) => {
    const cls = status === "sent" ? "ok" : status === "sending…" ? "" : "warn";
    return `<span class="dl ${cls}">${label} ${esc(status)}</span>`;
  };

  function renderPanel() {
    if (!isOpen) return;
    const steps = log.map((l) => `<li><time>${l.t}</time><span class="${l.cls}">${l.text}</span></li>`).join("");
    const c = current;
    const live = c && c.status === "active";
    els.shield.innerHTML = `<div class="verdict no-anim">
      <p class="section-title">Behind the scenes</p>
      ${log.length ? `<ol class="sos-log">${steps}</ol>` : `<p class="note">Ring the call, then hold <strong>Decline</strong> for 2 s.</p>`}
      ${c ? `
        <div>
          <p class="section-title">Trusted contacts</p>
          <div class="who">${c.contacts_notified.map((x) => `<div class="who__row"><span>${esc(x.name)}<small>${deliveryTag("Telegram", x.telegram)} ${deliveryTag("Email", x.email)}</small></span></div>`).join("") || '<p class="note">None set up.</p>'}</div>
          ${live ? `<a class="btn btn--block portal-btn" style="margin-top:6px" href="${esc(c.track_path)}" target="_blank" rel="noopener">Open live-location link ↗</a>` : ""}
        </div>
        <div>
          <p class="section-title">Nearby responders</p>
          <div class="who">
            ${c.responders_alerted.map((r) => `<div class="who__row"><span>${esc(r.name)}<small>~${r.distance_km} km · ${deliveryTag("Telegram", r.telegram)}</small></span><span class="state ${r.status}">${r.status}</span></div>`).join("")}
            ${c.responders_excluded.map((r) => `<div class="who__row"><span>${esc(r.name)}<small>${esc(r.reason)}</small></span><span class="state excluded">not alerted</span></div>`).join("")}
            ${!c.responders_alerted.length && !c.responders_excluded.length ? '<p class="note">No responders registered yet.</p>' : ""}
          </div>
          <a class="btn btn--block portal-btn" style="margin-top:6px" href="/responder.html" target="_blank" rel="noopener">Open responder view ↗</a>
        </div>
        ${live ? `<button type="button" class="btn btn--block safe-btn" data-safe>I'm safe · stop sharing</button>` : `<p class="note">SOS ended.</p>`}` : ""}
      ${demoMode ? `<button type="button" class="btn btn--ghost btn--block" data-sos-reset>Reset SOS demo</button>` : ""}
    </div>`;
  }

  els.shield.addEventListener("click", (e) => {
    if (isOpen && e.target.closest("[data-safe]")) imSafe();
    if (isOpen && e.target.closest("[data-sos-reset]")) resetDemo();
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
    loadSetup();
    restoreActive();
  }

  // An SOS keeps running on the server if this page is reloaded; pick it up again so
  // "I'm safe" is always reachable.
  async function restoreActive() {
    try {
      const { active } = await api("/sos/active");
      if (!active || current?.sos_id === active.sos_id) return;
      current = active;
      const since = new Date(active.created_at).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
      log = [{ t: hhmmss(), text: `SOS still active since ${since}. Sharing continues until you mark yourself safe.`, cls: "warn" }];
      startPolling();
      renderPanel();
    } catch {
      /* backend offline: nothing to restore */
    }
  }

  async function resetDemo() {
    stopLive();
    await api("/sos/demo/reset", { method: "POST" }).catch(() => {});
    clearInterval(pollTimer);
    current = null;
    log = [];
    ring();
    renderPanel();
    window.kavach.toast("SOS demo reset");
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
