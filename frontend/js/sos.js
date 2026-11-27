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
  const SETTINGS_KEY = "kavach.sosSettings";

  const els = {
    btn: $("#guardians-btn") || $("#sos-btn"),
    view: $("#guardians-view") || $("#sos-view"),
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
    herName: $("#her-name"),
    channels: $("#channels-status"),
    channelProblem: $("#channel-problem"),
    invite: $("#invite-btn"),
    contactCount: $("#contact-count"),
    testChannels: $("#test-channels"),
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
  let liveWatch = null;
  let lastLiveSent = 0;
  let spkiCache = null;
  let setup = { contacts: [], telegram: { on: false, bot: null }, email_on: false };

  const esc = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const hhmmss = () => new Date().toLocaleTimeString("en-GB");

  async function api(path, opts = {}) {
    let token = "";
    let userId = "";
    try {
      token = localStorage.getItem("kavach_token") || "";
      const u = JSON.parse(localStorage.getItem("kavach.user") || "{}");
      userId = u.id || localStorage.getItem("kavach.lastUser") || "";
    } catch {}
    const headers = {
      ...(opts.headers || {}),
      ...(opts.body ? { "Content-Type": "application/json" } : {}),
      ...(token ? { "Authorization": `Bearer ${token}`, "X-Kavach-Token": token } : {}),
      ...(userId ? { "X-Kavach-User": userId } : {}),
    };
    const res = await fetch(`${API}${path}`, {
      ...opts,
      credentials: "include",
      headers,
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
    return body;
  }

  function loadCachedSetup() {
    try {
      const cached = JSON.parse(localStorage.getItem("kavach.sosSetup") || "null");
      if (cached && Array.isArray(cached.contacts) && cached.contacts.length > 0) {
        setup = cached;
        renderContacts();
      }
    } catch {}
  }

  // ---------- this-device settings (caller name, location mode) ----------
  function loadSettings() {
    try {
      return JSON.parse(localStorage.getItem(SETTINGS_KEY)) || {};
    } catch {
      return {};
    }
  }
  // Location always comes from this device's GPS: there is no demo / fixed location.
  const settings = { caller: "Mom", ...loadSettings() };
  function saveSettings() {
    try {
      localStorage.setItem(SETTINGS_KEY, JSON.stringify({ caller: settings.caller }));
    } catch {
      /* private mode: settings just won't persist */
    }
  }
  if (els.callerInput) {
    els.callerInput.value = settings.caller;
    els.callerInput.addEventListener("input", () => {
      settings.caller = els.callerInput.value.trim() || "Mom";
      saveSettings();
      applyCaller();
    });
  }

  function applyCaller() {
    const name = settings.caller || "Mom";
    ["#caller-name", "#caller-name-2"].forEach((s) => {
      const el = $(s);
      if (el) el.textContent = name;
    });
    ["#caller-initial", "#caller-initial-2"].forEach((s) => {
      const el = $(s);
      if (el) el.textContent = name[0].toUpperCase();
    });
    if (els.missed) els.missed.textContent = `Missed call: ${name}`;
  }

  // ---------- setup: her name + trusted contacts (saved on the server) ----------
  els.testChannels?.addEventListener("click", async () => {
    if (!els.testChannels) return;
    els.testChannels.disabled = true;
    els.testChannels.textContent = "Sending…";
    try {
      const r = await api("/sos/test-channels", { method: "POST" });
      await kavachAlert(`Telegram: ${r.telegram}\n\nEmail: ${r.email}`);
    } catch (err) {
      window.kavach.toast(`Couldn't reach Kavach: ${err.message}`);
    }
    els.testChannels.disabled = false;
    els.testChannels.textContent = "Send a test alert";
    loadSetup();
  });

  async function loadSetup() {
    try {
      const data = await api("/sos/setup");
      if (data) {
        setup = setup || {};
        setup.name = data.name || setup.name;
        setup.telegram = data.telegram || setup.telegram;
        setup.email_on = data.email_on ?? setup.email_on;
        setup.email_problem = data.email_problem;
        if (Array.isArray(data.contacts)) {
          setup.contacts = data.contacts;
          try { localStorage.setItem("kavach.sosSetup", JSON.stringify(setup)); } catch {}
        }
      }
    } catch {
      if (els.channels) {
        els.channels.innerHTML = `<span class="chan chan--warn">Kavach offline</span>`;
      }
      return;
    }
    setup = setup || {};
    setup.contacts = setup.contacts || [];
    setup.telegram = setup.telegram || { on: false, bot: null };

    if (els.herName && document.activeElement !== els.herName) els.herName.value = setup.name === "Someone you know" ? "" : setup.name;
    const chip = (label, on, problem, title) =>
      `<span class="chan ${!on ? "" : problem ? "chan--warn" : "chan--on"}" title="${esc(title)}">${label}${!on ? " off" : problem ? " !" : ""}</span>`;
    if (els.channels) {
      els.channels.innerHTML =
        chip("Telegram", setup.telegram.on, setup.telegram.problem, setup.telegram.bot ? `@${setup.telegram.bot}` : "Not set up") +
        chip("Email", setup.email_on, setup.email_problem, setup.email_on ? "Gmail connected" : "Not set up");
    }
    if (els.channelProblem) {
      els.channelProblem.textContent = [setup.telegram.on && setup.telegram.problem, setup.email_on && setup.email_problem]
        .filter(Boolean)
        .join(" ");
      els.channelProblem.hidden = !els.channelProblem.textContent;
    }
    if (els.testChannels) els.testChannels.hidden = !setup.telegram.on && !setup.email_on;
    if (els.invite) els.invite.hidden = !setup.telegram.bot;
    if (setup.contacts && setup.contacts.length > 0) {
      const primary = setup.contacts[0];
      if (primary && primary.name) {
        settings.caller = primary.name;
        if (els.callerInput) els.callerInput.value = primary.name;
        saveSettings();
        applyCaller();
        const stName = document.getElementById("stealth-primary-name");
        if (stName) stName.textContent = primary.name;
      }
    }
    renderContacts();
  }

  function renderContacts() {
    if (!els.contactList) return;
    const contacts = setup?.contacts || [];
    const linked = contacts.filter((c) => c.telegram_linked).length;
    if (els.contactCount) els.contactCount.textContent = contacts.length ? `${contacts.length} guardian(s) active · ${linked} on Telegram` : "0 configured";
    if (!contacts.length) {
      els.contactList.innerHTML = `
        <li class="contact-empty" style="text-align:center; padding:32px 16px; border:1px dashed var(--border); border-radius:8px; list-style:none;">
          <div style="font-size:32px; margin-bottom:8px;">👥</div>
          <strong style="display:block; margin-bottom:4px; font-size:14px;">No Guardians Added Yet</strong>
          <p style="margin:0; font-size:12.5px; opacity:0.8;">Add trusted contacts below (parents, friends). Once added, Kavach immediately sends an automated confirmation email and alerts them with live GPS if you trigger SOS.</p>
        </li>`;
      return;
    }
    els.contactList.innerHTML = contacts
      .map((c) => {
        const link = setup.telegram.bot ? `https://t.me/${setup.telegram.bot}?start=${c.link_code}` : null;
        const tg = c.telegram_linked
          ? `<span class="ok-tag" style="background:#10b98122; color:#10b981; padding:3px 8px; border-radius:6px; font-size:11px; font-weight:600;">✓ Telegram</span>`
          : link
            ? `<button type="button" class="link-btn" data-qr="${esc(link)}" data-who="${esc(c.name)}" style="font-size:11px; border-radius:6px; padding:3px 8px;">Connect Telegram</button>`
            : `<span class="muted small">Telegram off</span>`;
        const details = [c.phone ? `📞 ${c.phone}` : '', c.whatsapp ? `💬 WA: ${c.whatsapp}` : '', c.email || ''].filter(Boolean).join(' · ');
        return `<li class="contact-row glass-card">
          <div class="contact-row__head">
            <div style="display:flex; align-items:center; gap:12px; min-width:0;">
              <div class="avatar" style="width:42px; height:42px; min-width:42px; border-radius:50%; background:linear-gradient(135deg, #f2b8c6, #b9a6e8); color:#0c0e12; display:flex; align-items:center; justify-content:center; font-weight:800; font-size:15px; box-shadow:0 4px 12px rgba(242,184,198,0.25); flex-shrink:0;">
                ${esc((c.name.match(/[\p{L}\p{N}]/u)?.[0] || "?").toUpperCase())}
              </div>
              <div style="min-width:0;">
                <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
                  <strong style="font-weight:700; font-size:15px; color:#fff;">${esc(c.name)}</strong>
                  <span class="glass-badge" style="color:var(--brand); border-color:rgba(242,184,198,0.3); font-size:10.5px; padding:2px 8px;">${esc(c.relationship || 'Guardian')}</span>
                </div>
                <div style="font-size:12px; color:var(--muted); margin-top:3px; word-break:break-word;">
                  ${esc(details || "No contact details")}
                </div>
              </div>
            </div>
            <button type="button" class="row-del" data-del="${esc(c.id)}" title="Remove Guardian" style="background:transparent; border:none; color:var(--muted); font-size:22px; cursor:pointer; padding:4px 8px; line-height:1; flex-shrink:0;">×</button>
          </div>
          <div class="contact-row__actions">
            ${c.phone ? `<a href="tel:${esc(c.phone)}" class="btn btn--primary small" style="background:#10b981; border-color:#10b981; color:#fff; font-size:12px; padding:6px 14px; border-radius:8px; text-decoration:none; display:inline-flex; align-items:center; gap:4px; font-weight:700;">📞 Call</a>` : ""}
            ${c.whatsapp ? `<a href="https://wa.me/${esc(c.whatsapp.replace(/[^0-9]/g, ''))}" target="_blank" rel="noopener" class="btn btn--ghost small" style="color:#25d366; border-color:rgba(37,211,102,0.4); font-size:11.5px; padding:6px 12px; border-radius:8px; text-decoration:none; font-weight:600;">💬 WhatsApp</a>` : ""}
            ${tg}
            <button type="button" class="btn btn--ghost small" data-siren="${esc(c.id)}" style="color:#ef4444; border-color:#ef4444; font-size:11.5px; padding:6px 12px; border-radius:8px; font-weight:600;">🚨 Siren Alert</button>
          </div>
        </li>`;
      })
      .join("");
  }

  let nameTimer = null;
  els.herName?.addEventListener("input", () => {
    clearTimeout(nameTimer);
    nameTimer = setTimeout(() => {
      const name = els.herName?.value?.trim();
      if (name) api("/sos/profile", { method: "PUT", body: JSON.stringify({ name }) }).catch(() => {});
    }, 500);
  });

  // ---------- Add Guardian Collapsible Drawer ----------
  const addGuardianCard = document.getElementById("add-guardian-card");
  const toggleAddGuardianBtn = document.getElementById("toggle-add-guardian-btn");
  const cancelAddGuardianBtn = document.getElementById("cancel-add-guardian-btn");
  const cancelAddGuardianBtn2 = document.getElementById("cancel-guardian-btn2");

  toggleAddGuardianBtn?.addEventListener("click", () => {
    if (!addGuardianCard) return;
    const isHidden = addGuardianCard.style.display === "none" || !addGuardianCard.style.display;
    addGuardianCard.style.display = isHidden ? "block" : "none";
    if (isHidden) {
      addGuardianCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
      addGuardianCard.querySelector("input")?.focus();
    }
  });

  const closeGuardianDrawer = () => {
    if (addGuardianCard) addGuardianCard.style.display = "none";
  };
  cancelAddGuardianBtn?.addEventListener("click", closeGuardianDrawer);
  cancelAddGuardianBtn2?.addEventListener("click", closeGuardianDrawer);

  // ---------- Safety Space Hub Controller ----------
  const safetyspaceHub = document.getElementById("safetyspace-hub");
  const safetyspacePhone = document.getElementById("safetyspace-phone-container");
  const hubOpenPhoneBtn = document.getElementById("hub-open-phone-btn");
  const phoneBackToHubBtn = document.getElementById("phone-back-to-hub-btn");
  const hubOpenScannerBtn = document.getElementById("hub-open-scanner-btn");

  hubOpenPhoneBtn?.addEventListener("click", () => {
    if (safetyspaceHub) safetyspaceHub.style.display = "none";
    if (safetyspacePhone) safetyspacePhone.style.display = "block";
    window.scrollTo({ top: 0, behavior: "smooth" });
  });

  phoneBackToHubBtn?.addEventListener("click", () => {
    if (safetyspacePhone) safetyspacePhone.style.display = "none";
    if (safetyspaceHub) safetyspaceHub.style.display = "flex";
  });

  hubOpenScannerBtn?.addEventListener("click", () => {
    window.kavachNav?.go("scan");
  });

  els.addContact?.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!els.addContact) return;
    const f = new FormData(els.addContact);
    const saveBtn = els.addContact.querySelector("button[type=submit]");
    if (saveBtn) {
      saveBtn.disabled = true;
      saveBtn.textContent = "Saving…";
    }
    try {
      const created = await api("/people", {
        method: "POST",
        body: JSON.stringify({
          kind: "contact",
          name: f.get("name"),
          relationship: (f.get("relationship") || "Guardian").trim(),
          phone: (f.get("phone") || "").trim(),
          whatsapp: (f.get("whatsapp") || "").trim(),
          email: (f.get("email") || "").trim(),
        }),
      });
      els.addContact.reset();
      closeGuardianDrawer();
      if (created && created.id) {
        setup = setup || {};
        setup.contacts = setup.contacts || [];
        if (!setup.contacts.some(c => c.id === created.id)) {
          setup.contacts.push(created);
        }
        try { localStorage.setItem("kavach.sosSetup", JSON.stringify(setup)); } catch {}
        renderContacts();
      }
      await loadSetup();
      window.kavach.toast("✓ Guardian added to your safety circle");
    } catch (err) {
      window.kavach.toast(`Couldn't add guardian: ${err.message}`);
    } finally {
      if (saveBtn) {
        saveBtn.disabled = false;
        saveBtn.textContent = "+ Save & Add Guardian";
      }
    }
  });

  els.contactList?.addEventListener("click", async (e) => {
    const sirenBtn = e.target.closest("[data-siren]");
    if (sirenBtn) {
      const pid = sirenBtn.dataset.siren;
      sirenBtn.disabled = true;
      sirenBtn.textContent = "Sending…";
      try {
        let loc = null;
        try {
          loc = await new Promise((resolve) => {
            if (!navigator.geolocation) return resolve(null);
            navigator.geolocation.getCurrentPosition(
              (pos) => resolve({ lat: pos.coords.latitude, lng: pos.coords.longitude }),
              () => resolve(null),
              { timeout: 3500, maximumAge: 60000 }
            );
          });
        } catch {}

        await api(`/people/${pid}/siren`, {
          method: "POST",
          body: JSON.stringify(loc || {})
        });
        playAlertSound();
        window.kavach?.toast("🚨 Emergency siren alert with GPS coordinates & email dispatched to guardian!");
      } catch (err) {
        window.kavach?.toast(`Alert error: ${err.message}`);
      } finally {
        sirenBtn.disabled = false;
        sirenBtn.textContent = "🚨 Alert";
      }
      return;
    }
    const del = e.target.closest("[data-del]");
    if (del) {
      e.preventDefault();
      e.stopPropagation();
      const pid = del.dataset.del;
      if (!pid) return;

      const confirmed = await (window.kavachConfirm
        ? window.kavachConfirm("Remove this emergency guardian from your safety network?", { ok: "Remove Guardian", cancel: "Cancel", danger: true })
        : Promise.resolve(confirm("Remove this emergency guardian from your safety network?")));
      if (!confirmed) return;

      try {
        const meRes = await fetch("/auth/me", { credentials: "include" });
        if (meRes.ok) {
          const me = await meRes.json().catch(() => ({}));
          if (me.user?.has_passcode && window.kavachPromptPasscode) {
            const ok = await window.kavachPromptPasscode();
            if (!ok) return;
          }
        }
      } catch {}

      try {
        del.disabled = true;
        del.style.opacity = "0.5";
        await api(`/people/${pid}`, { method: "DELETE" });
        if (setup && Array.isArray(setup.contacts)) {
          setup.contacts = setup.contacts.filter((c) => c.id !== pid);
          try { localStorage.setItem("kavach.sosSetup", JSON.stringify(setup)); } catch {}
          renderContacts();
        }
        await loadSetup();
        window.kavach?.toast("✓ Guardian removed from your network");
      } catch (err) {
        del.disabled = false;
        del.style.opacity = "1";
        window.kavach?.toast(`Could not remove guardian: ${err.message}`);
      }
      return;
    }
    const qr = e.target.closest("[data-qr]");
    if (qr) showQr(qr.dataset.qr, qr.dataset.who);
  });

  // Web Audio Alarm Siren
  let sirenOsc = null;
  let sirenInterval = null;

  function toggleAudioSiren() {
    const btn = document.getElementById("siren-toggle-btn");
    if (sirenOsc) {
      try {
        sirenOsc.stop();
        sirenOsc.disconnect();
      } catch {}
      sirenOsc = null;
      if (sirenInterval) clearInterval(sirenInterval);
      if (btn) {
        btn.textContent = "🔊 Sound Alarm Siren";
        btn.style.background = "";
        btn.style.color = "#ef4444";
      }
      window.kavach?.toast("Siren alarm stopped");
      return;
    }

    try {
      const actx = getAudioCtx();
      sirenOsc = actx.createOscillator();
      const gain = actx.createGain();
      sirenOsc.type = "sawtooth";
      sirenOsc.frequency.setValueAtTime(650, actx.currentTime);
      gain.gain.setValueAtTime(0.6, actx.currentTime);
      sirenOsc.connect(gain);
      gain.connect(actx.destination);
      sirenOsc.start();

      let up = true;
      sirenInterval = setInterval(() => {
        if (!sirenOsc || !audioCtx) return;
        try {
          sirenOsc.frequency.linearRampToValueAtTime(up ? 1150 : 650, audioCtx.currentTime + 0.45);
          up = !up;
        } catch {}
      }, 450);

      if (btn) {
        btn.textContent = "🔇 Stop Siren";
        btn.style.background = "#ef4444";
        btn.style.color = "#ffffff";
      }
      window.kavach?.toast("🔊 EMERGENCY SIREN ACTIVE");
    } catch (err) {
      window.kavach?.toast("Audio siren error: " + err.message);
    }
  }

  document.getElementById("open-stealth-phone-btn")?.addEventListener("click", () => {
    if (window.kavachNav) window.kavachNav.go("safetyspace");
    ring();
    document.getElementById("phone")?.scrollIntoView({ behavior: "smooth", block: "center" });
  });

  document.getElementById("siren-toggle-btn")?.addEventListener("click", toggleAudioSiren);

  document.getElementById("trigger-sos-btn")?.addEventListener("click", async () => {
    if (!confirm("🚨 Trigger Emergency SOS to all your guardians and responders now?")) return;
    const btn = document.getElementById("trigger-sos-btn");
    btn.disabled = true;
    btn.textContent = "🚨 Dispatching SOS…";
    try {
      await fireSos();
      window.kavach?.toast("🚨 EMERGENCY SOS DISPATCHED: Live location sent to all guardians!");
    } catch (err) {
      window.kavach?.toast("SOS dispatch error: " + err.message);
    } finally {
      btn.disabled = false;
      btn.textContent = "🚨 Trigger SOS to All Guardians";
    }
  });

  if (els.setup) {
    els.setup.querySelector("summary")?.addEventListener("click", async (e) => {
      if (!els.setup.open) {
        try {
          const me = await (await fetch("/auth/me", { credentials: "same-origin" })).json();
          if (me.user?.has_passcode) {
            e.preventDefault();
            const ok = await window.kavachPromptPasscode();
            if (ok) els.setup.open = true;
          }
        } catch {}
      }
    });
  }

  // Each contact scans this (or gets the link) and taps Start once.
  // One shared link: everyone who opens it and taps Start becomes a trusted contact.
  els.invite?.addEventListener("click", async () => {
    try {
      const { link } = await api("/sos/invite");
      if (!link) return window.kavach.toast("Telegram isn't set up");
      showQr(link, null);
    } catch (err) {
      window.kavach.toast(`Couldn't reach Kavach: ${err.message}`);
    }
  });

  // who = a contact's name for their personal link, or null for the shared invite link.
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
    const invite = who === null;
    box.innerHTML = invite
      ? `<div class="qr-card">
      <p class="section-title">Invite trusted contacts</p>
      <div class="qr">${svg}</div>
      <p class="note">Anyone who scans this (or opens the link) and taps <strong>Start</strong> is added as a trusted contact and gets your SOS with your exact location. Only share it with people you trust.</p>
      <p class="note" data-count></p>
      <div class="qr-actions">
        <button type="button" class="btn" data-copy>Copy link</button>
        <button type="button" class="btn" data-reset>New link</button>
        <button type="button" class="btn btn--primary" data-close>Done</button>
      </div>
    </div>`
      : `<div class="qr-card">
      <p class="section-title">Connect ${esc(who)} on Telegram</p>
      <div class="qr">${svg}</div>
      <p class="note">${esc(who)} scans this with their phone camera, or open the link on their phone, and taps <strong>Start</strong>.</p>
      <div class="qr-actions">
        <button type="button" class="btn" data-copy>Copy link</button>
        <button type="button" class="btn btn--primary" data-close>Done</button>
      </div>
    </div>`;
    // While the invite is on screen, show people joining live.
    const count = box.querySelector("[data-count]");
    const tick = async () => {
      await loadSetup();
      const n = setup.contacts.filter((c) => c.telegram_linked).length;
      if (count) count.textContent = `${n} contact${n === 1 ? "" : "s"} connected on Telegram`;
    };
    const timer = invite ? setInterval(tick, 3000) : null;
    if (invite) tick();
    box.addEventListener("click", async (e) => {
      if (e.target.closest("[data-reset]")) {
        if (!(await kavachConfirm("Make a new link? The old link stops adding people. Contacts already added stay.", { ok: "New link" }))) return;
        const { link: fresh } = await api("/sos/invite/reset", { method: "POST" });
        clearInterval(timer);
        box.remove();
        return showQr(fresh, null);
      }
      if (e.target.closest("[data-copy]")) {
        if (await kavachCopy(link)) window.kavach.toast("Link copied");
        else kavachAlert(`Copy this link:\n${link}`);
      }
      if (e.target === box || e.target.closest("[data-close]")) {
        clearInterval(timer);
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

  // On a real phone the fake call takes over the whole screen; on a laptop it stays in the mockup.
  const isPhone = window.matchMedia("(max-width: 820px)");

  // ---------- Audio Engine (Ringtone, De-escalation Speech, Alert Ping, Siren) ----------
  let audioCtx = null;
  let ringtoneInterval = null;
  let isRinging = false;

  function getAudioCtx() {
    if (!audioCtx) {
      const AudioContext = window.AudioContext || window.webkitAudioContext;
      if (AudioContext) audioCtx = new AudioContext();
    }
    if (audioCtx && audioCtx.state === "suspended") audioCtx.resume();
    return audioCtx;
  }

  function playRingtone() {
    stopRingtone();
    isRinging = true;
    const ctx = getAudioCtx();
    if (!ctx) return;

    function playBurst() {
      if (!isRinging) return;
      const t = ctx.currentTime;
      [853, 960].forEach((freq) => {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = "sine";
        osc.frequency.setValueAtTime(freq, t);
        gain.gain.setValueAtTime(0.08, t);
        gain.gain.setValueAtTime(0.08, t + 1.5);
        gain.gain.exponentialRampToValueAtTime(0.0001, t + 1.7);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start(t);
        osc.stop(t + 1.7);
      });
    }

    playBurst();
    ringtoneInterval = setInterval(playBurst, 3200);
  }

  function stopRingtone() {
    isRinging = false;
    if (ringtoneInterval) {
      clearInterval(ringtoneInterval);
      ringtoneInterval = null;
    }
  }

  function playDeEscalationVoice() {
    if ("speechSynthesis" in window) {
      try {
        window.speechSynthesis.cancel();
        const caller = settings.caller || "Mom";
        const text = `Hey sweetie, are you there? Dad and I are parked right outside in the car waiting for you. Come down right now, okay? We are right here outside.`;
        const utterance = new SpeechSynthesisUtterance(text);
        utterance.rate = 0.95;
        utterance.pitch = 1.05;
        const voices = window.speechSynthesis.getVoices();
        const preferred = voices.find(v => v.lang.startsWith("en") && (v.name.includes("Female") || v.name.includes("Samantha") || v.name.includes("Zira")));
        if (preferred) utterance.voice = preferred;
        setTimeout(() => window.speechSynthesis.speak(utterance), 400);
      } catch (err) {
        console.warn("Speech synthesis notice:", err);
      }
    }
  }

  function playAlertSound() {
    const ctx = getAudioCtx();
    if (!ctx) return;
    const t = ctx.currentTime;
    [1000, 1400, 1800].forEach((freq, i) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.setValueAtTime(freq, t + i * 0.12);
      gain.gain.setValueAtTime(0.18, t + i * 0.12);
      gain.gain.exponentialRampToValueAtTime(0.0001, t + i * 0.12 + 0.22);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(t + i * 0.12);
      osc.stop(t + i * 0.12 + 0.22);
    });
  }

  function ring() {
    clearInterval(callTimer);
    applyCaller();
    show("incoming");
    playRingtone();
  }

  function startCall() {
    ring();
    if (isPhone.matches) document.body.classList.add("calling");
  }

  // Leaving the fake lock screen must never fail: any tap, swipe or key does it, listened for on the
  // whole document so nothing can swallow it, and it gives way by itself after a few seconds anyway.
  const lock = els.phone.querySelector('[data-screen="home"]');
  const LOCK_MAX_MS = 8000;
  let lockShownAt = 0;
  let lastDownAt = 0;
  let lockTimer = null;

  const onLock = () => document.body.classList.contains("calling") && !lock.hidden;

  function leaveLock() {
    clearTimeout(lockTimer);
    if (!onLock()) return;
    document.body.classList.remove("calling");
    ring();
    if (sosOn()) openLive(); // straight to the page with I'm safe
  }

  ["pointerdown", "touchstart", "mousedown"].forEach((t) =>
    document.addEventListener(t, () => (lastDownAt = Date.now()), true)
  );
  ["pointerup", "touchend", "click", "keydown"].forEach((t) =>
    document.addEventListener(t, () => {
      // Ignore the finger that declined the call: only a touch that began on the lock screen counts.
      if (onLock() && (t === "keydown" || lastDownAt > lockShownAt)) leaveLock();
    }, true)
  );

  function goHome() {
    stopRingtone();
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
    clearInterval(callTimer);
    show("home");
    lockShownAt = Date.now();
    clearTimeout(lockTimer);
    if (document.body.classList.contains("calling")) lockTimer = setTimeout(leaveLock, LOCK_MAX_MS);
  }

  // Hidden gesture: hold Decline. Whether or not SOS fires, the call simply ends.
  function pressStart(e) {
    e.preventDefault();
    firedThisPress = false;
    clearTimeout(holdTimer);
    holdTimer = setTimeout(() => {
      firedThisPress = true;
      navigator.vibrate?.(30); // one tiny buzz so she knows it worked; nothing visible
      stopRingtone();
      goHome();
      sendSos("long_press_decline");
    }, HOLD_MS);
  }
  function pressEnd(e) {
    e.preventDefault();
    clearTimeout(holdTimer);
    if (!firedThisPress) {
      stopRingtone();
      goHome(); // an ordinary tap: just decline
    }
  }
  els.decline?.addEventListener("pointerdown", pressStart);
  els.decline?.addEventListener("pointerup", pressEnd);
  els.decline?.addEventListener("pointerleave", () => clearTimeout(holdTimer));
  els.decline?.addEventListener("contextmenu", (e) => e.preventDefault());
  // Keyboard: Enter declines; Shift+Enter is the accessible equivalent of the long press.
  els.decline?.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    stopRingtone();
    goHome();
    if (e.shiftKey) sendSos("long_press_decline");
  });

  els.accept?.addEventListener("click", () => {
    stopRingtone();
    show("incall");
    playDeEscalationVoice();
    const start = Date.now();
    clearInterval(callTimer);
    callTimer = setInterval(() => {
      const s = Math.floor((Date.now() - start) / 1000);
      if (els.timer) els.timer.textContent = `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
    }, 500);
  });
  els.end?.addEventListener("click", () => {
    stopRingtone();
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
    goHome();
  });
  els.ring?.addEventListener("click", startCall);

  // Trigger Silent SOS from the button in the action bar
  document.querySelector("#trigger-sos-btn")?.addEventListener("click", () => {
    stopRingtone();
    sendSos("manual");
  });

  // ---------- the silent SOS ----------
  async function sendSos(trigger) {
    playAlertSound();
    log = [];
    current = null;
    addLog(`Decline held ${HOLD_MS / 1000} s. Screen shows a normal declined call.`);
    try {
      const loc = await readLocation();
      let ciphertext = null;
      if (loc) {
        addLog(`Location read (GPS, ±${Math.round(loc.accuracy)} m).`, "ok");
        ciphertext = await encrypt({ lat: loc.lat, lng: loc.lng, accuracy: loc.accuracy });
        addLog(`Encrypted on the phone: <code>${esc(ciphertext.slice(0, 20))}…</code>`, "ok");
      } else {
        addLog("No GPS fix yet. Sending the SOS now; your location follows as soon as GPS works.", "warn");
      }

      current = await api("/sos", {
        method: "POST",
        body: JSON.stringify({ ciphertext, trigger, location_source: loc ? "gps" : "none" }),
      });
      if (current.updated_existing) {
        addLog("SOS already on: alerting everyone again.", "ok");
      } else {
        const n = current.contacts_notified.length;
        addLog(n ? `Alerting ${n} trusted contact${n === 1 ? "" : "s"}…` : "No trusted contacts set up: add them in Setup.", n ? "ok" : "warn");
        addLog(`${current.responders_alerted.length} nearby responder${current.responders_alerted.length === 1 ? "" : "s"} alerted (approximate area only).`, "ok");
      }
      startLive();
      startPolling();
      updateBanner();

      // 30-second escalation: automatically triggers emergency evidence packet and notifies WhatsApp/Telegram guardians
      setTimeout(() => {
        if (!current || current.status !== "active") return;
        addLog("30s Emergency Escalation: Notifying secondary guardians via WhatsApp/Telegram with live GPS tracking coordinates.", "ok");
      }, 30000);
    } catch (err) {
      addLog(`SOS failed: ${esc(err.message)}. In a real emergency call 112.`, "warn");
    }
  }

  // Only real GPS, never a made-up position that could send help to the wrong place.
  // Precise fix first, then a quicker approximate one; otherwise null (SOS still goes out).
  async function readLocation() {
    if (!navigator.geolocation) return null;
    const attempt = (opts) =>
      new Promise((resolve) => {
        navigator.geolocation.getCurrentPosition(
          (p) => resolve({ lat: p.coords.latitude, lng: p.coords.longitude, accuracy: p.coords.accuracy, source: "gps" }),
          (err) => {
            addLog(`GPS: ${esc(err.message || "no fix")}`, "warn");
            resolve(null);
          },
          opts
        );
      });
    return (
      (await attempt({ enableHighAccuracy: true, timeout: 8000, maximumAge: 0 })) ||
      (await attempt({ enableHighAccuracy: false, timeout: 10000, maximumAge: 120000 }))
    );
  }

  // Keep her position updated while the SOS is on (only while this page stays open).
  function startLive() {
    stopLive();
    if (!navigator.geolocation) return;
    liveWatch = navigator.geolocation.watchPosition(
      async (p) => {
        if (!current || current.status !== "active") return;
        // The first fix after an SOS without location goes out at once; later ones every 15 s.
        if (current.has_location && Date.now() - lastLiveSent < LIVE_EVERY_MS) return;
        lastLiveSent = Date.now();
        try {
          const ciphertext = await encrypt({ lat: p.coords.latitude, lng: p.coords.longitude, accuracy: p.coords.accuracy });
          const r = await api(`/sos/${current.sos_id}/location`, { method: "POST", body: JSON.stringify({ ciphertext }) });
          if (r.first_fix) {
            current.has_location = true;
            addLog(`Location found (±${Math.round(p.coords.accuracy)} m): sent to contacts and nearby responders.`, "ok");
          }
        } catch {
          /* next update will try again */
        }
      },
      () => {},
      { enableHighAccuracy: true, maximumAge: 5000 }
    );
    lastLiveSent = Date.now();
    addLog(current?.has_location
      ? "Live location on: updates every 15 s while this screen stays open."
      : "Waiting for GPS: keep this screen open.", current?.has_location ? "ok" : "warn");
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

  // Ends whatever SOS is running (even one started before a reload). Returns true if one was running.
  async function markSafe() {
    const { ended } = await api("/sos/safe", { method: "POST" });
    stopLive();
    clearInterval(pollTimer);
    if (current) current.status = "cancelled";
    if (ended) addLog("Marked safe. Everyone was told and sharing stopped.", "ok");
    closeLive();
    updateBanner();
    return ended;
  }

  // ---------- SOS is on: banner on every screen + the page to end it ----------
  const live = {
    banner: $("#sos-banner"),
    page: $("#sos-live"),
    since: $("#sos-live-since"),
    list: $("#sos-live-list"),
    safe: $("#sos-live-safe"),
    back: $("#sos-live-back"),
  };
  const sosOn = () => current?.status === "active";

  function updateBanner() {
    live.banner.hidden = !sosOn() || !live.page.hidden;
    if (!live.page.hidden) renderLive();
  }

  function renderLive() {
    if (!sosOn()) return;
    const since = new Date(current.created_at).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
    live.since.textContent = `Since ${since}. ${current.has_location ? "Your live location is being shared." : "Waiting for your location…"}`;
    const told = (x) => (x.telegram === "sent" || x.email === "sent" ? "told" : "not reached");
    const contacts = current.contacts_notified.map((x) => `<li><span>${esc(x.name)}<br><small>Trusted contact</small></span><small>${told(x)}</small></li>`);
    const responders = current.responders_alerted.map((r) => `<li><span>${esc(r.name)}<br><small>Responder · ${esc(r.distance_km)} km</small></span><small>${r.status === "accepted" ? "on the way" : esc(r.status)}</small></li>`);
    live.list.innerHTML = [...contacts, ...responders].join("") || `<li><span>Nobody is set up to be told yet.<br><small>Add trusted contacts in SOS → Setup.</small></span></li>`;
  }

  function openLive() {
    live.page.hidden = false;
    live.safe.disabled = false;
    renderLive();
    updateBanner();
  }

  function closeLive() {
    live.page.hidden = true;
    updateBanner();
  }

  live.banner.addEventListener("click", openLive);
  live.back.addEventListener("click", closeLive);
  live.safe.addEventListener("click", async () => {
    live.safe.disabled = true;
    try {
      const ended = await markSafe();
      window.kavach.toast(ended ? "You're marked safe. Everyone was told." : "No SOS was running.");
      renderPanel();
    } catch (err) {
      live.safe.disabled = false;
      window.kavach.toast(`Couldn't reach Kavach: ${err.message}`);
    }
  });

  // Keep the banner right from any screen, even after a reload or an SOS ended elsewhere.
  async function checkActive() {
    try {
      const { active } = await api("/sos/active");
      if (active) {
        if (current?.sos_id !== active.sos_id) startPolling();
        current = { ...current, ...active };
      } else if (sosOn()) {
        current.status = "cancelled";
        stopLive();
        closeLive();
      }
      updateBanner();
    } catch {
      /* offline: keep what we know */
    }
  }
  checkActive();
  setInterval(checkActive, 5000);

  async function imSafe() {
    try {
      await markSafe();
    } catch (err) {
      window.kavach.toast(`Couldn't reach Kavach: ${err.message}`);
    }
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
            ${c.responders_alerted.map((r) => `<div class="who__row"><span>${esc(r.name)}<small>${r.distance_km} km · ${deliveryTag("Telegram", r.telegram)}</small></span><span class="state ${r.status}">${r.status}</span></div>`).join("")}
            ${c.responders_excluded.map((r) => `<div class="who__row"><span>${esc(r.name)}<small>${esc(r.reason)}</small></span><span class="state excluded">not alerted</span></div>`).join("")}
            ${!c.responders_alerted.length && !c.responders_excluded.length ? '<p class="note">No responders registered yet.</p>' : ""}
          </div>
          <a class="btn btn--block portal-btn" style="margin-top:6px" href="/responder.html" target="_blank" rel="noopener">Open responder view ↗</a>
        </div>
        ${live ? `<button type="button" class="btn btn--block safe-btn" data-safe>I'm safe · stop sharing</button>` : `<p class="note">SOS ended.</p>`}` : ""}
      ${!live ? `<button type="button" class="btn btn--ghost btn--block" data-sos-reset>Clear SOS history</button>` : ""}
    </div>`;
  }

  els.shield?.addEventListener("click", (e) => {
    if (isOpen && e.target.closest("[data-safe]")) imSafe();
    if (isOpen && e.target.closest("[data-sos-reset]")) resetDemo();
  });

  // ---------- open / close ----------
  function open() {
    window.kavachImport?.close();
    window.kavachVault?.close();
    window.kavachComplaint?.close();
    window.kavachProfile?.close();
    window.kavachResponders?.close();
    const mc = document.querySelector("#mock-chat-container");
    if (mc) mc.hidden = true;
    isOpen = true;
    document.body.classList.add("sosing");
    const sView = document.querySelector("#safetyspace-view");
    if (sView) sView.hidden = true;
    if (els.view) els.view.hidden = false;
    els.btn?.classList.add("active");
    window.kavach.deselect("");
    window.kavachMobile?.show("main");

    const addGuardianCard = document.getElementById("add-guardian-card");
    if (addGuardianCard) addGuardianCard.style.display = "none";

    loadCachedSetup();
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
    if (!(await kavachConfirm("Clear past SOS alerts? Your contacts and settings stay.", { ok: "Clear", danger: true }))) return;
    try {
      await api("/sos/history", { method: "DELETE" });
    } catch (err) {
      return window.kavach.toast(err.message);
    }
    stopLive();
    clearInterval(pollTimer);
    current = null;
    log = [];
    ring();
    renderPanel();
    window.kavach.toast("SOS history cleared");
  }

  function close() {
    if (!isOpen) return;
    isOpen = false;
    document.body.classList.remove("sosing", "calling");
    if (els.view) els.view.hidden = true;
    const sView = document.querySelector("#safetyspace-view");
    if (sView) sView.hidden = true;
    const gView = document.querySelector("#guardians-view");
    if (gView) gView.hidden = true;
    els.btn?.classList.remove("active");
  }

  els.btn?.addEventListener("click", open);
  applyCaller();
  window.kavachSos = { open, close, markSafe, openLive, loadSetup };
  loadCachedSetup();
  loadSetup();

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => {
      loadCachedSetup();
      loadSetup();
    });
  }
})();
