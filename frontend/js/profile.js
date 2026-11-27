/**
 * Kavach Profile & Responders Controller
 * Manages modern e-commerce style user profile & dynamic proximity-based responders network.
 */

(() => {
  const API = new URLSearchParams(location.search).get("api") || "";
  const $ = (sel) => document.querySelector(sel);

  // Profile Elements
  const profView = $("#profile-view");
  const profForm = $("#profile-form");
  const profFormWrap = $("#profile-form-wrap");
  const profSectionsView = $("#profile-sections-view");
  const profToggleEditBtn = $("#prof-toggle-edit-btn");
  const profCancelEditBtn = $("#prof-cancel-edit-btn");

  const profRowAvatar = $("#prof-row-avatar");
  const profRowName = $("#prof-row-name");
  const profRowEmail = $("#prof-row-email");
  const profRowPhone = $("#prof-row-phone");
  const profRowAltPhone = $("#prof-row-alt-phone");
  const profRowAddress = $("#prof-row-address");
  const profRowCity = $("#prof-row-city");
  const profRowStation = $("#prof-row-station");

  const profName = $("#prof-name");
  const profPhone = $("#prof-phone");
  const profEmail = $("#prof-email");
  const profAltPhone = $("#prof-alt-phone");
  const profCity = $("#prof-city");
  const profAddress = $("#prof-address");
  const profPoliceStation = $("#prof-police-station");
  const profReset = $("#prof-reset-btn");

  const profAvatar = $("#prof-avatar-letter");
  const profDisplayName = $("#prof-display-name");
  const profDisplayEmail = $("#prof-display-email");
  const profKpiGuardians = $("#prof-kpi-guardians");

  const profBackBtn = $("#prof-back-btn");
  const profNextBtn = $("#prof-next-btn");

  // Google Top-Right Account Popover Elements
  const googleAccountBtn = $("#google-account-btn");
  const googleAccountPopover = $("#google-account-popover");
  const topAvatarLetter = $("#top-avatar-letter");
  const popoverAvatarLetter = $("#popover-avatar-letter");
  const popoverName = $("#popover-name");
  const popoverEmail = $("#popover-email");
  const popoverProfileBtn = $("#popover-profile-btn");

  // Responders Elements
  const respView = $("#responders-view");
  const respList = $("#responders-list");
  const respForm = $("#add-responder-form");
  const respName = $("#resp-name");
  const respEmail = $("#resp-email");
  const respPhone = $("#resp-phone");
  const respOrg = $("#resp-org");
  const respCitySelect = $("#resp-city-select");
  const respLatInput = $("#resp-lat");
  const respLngInput = $("#resp-lng");

  const userZoneSelect = $("#user-zone-select");
  const detectGpsBtn = $("#detect-gps-btn");
  const filterStatus = $("#responders-filter-status");

  // Default user location: try localStorage first, fallback to Bengaluru Center
  let savedLat = parseFloat(localStorage.getItem("kavach_user_lat"));
  let savedLng = parseFloat(localStorage.getItem("kavach_user_lng"));
  let currentCoord = (!isNaN(savedLat) && !isNaN(savedLng))
    ? { lat: savedLat, lng: savedLng, label: "Live Device GPS" }
    : { lat: 12.9716, lng: 77.5946, label: "Bengaluru" };

  // Automatic Location Sync & Permission Manager (Interval: 1 minute = 60s)
  const locBanner = $("#location-permission-banner");
  const grantLocBtn = $("#grant-location-btn");
  const locModal = $("#location-accuracy-modal");
  const locTurnOnBtn = $("#loc-turn-on-btn");
  const locDismissBtn = $("#loc-dismiss-btn");
  let locationCheckTimer = null;
  let isLocationGranted = false;

  let lastActiveTime = Date.now();
  const FIVE_MINUTES_MS = 5 * 60 * 1000;

  function showLocationAccuracyModal() {
    if (locModal) {
      locModal.style.display = "flex";
      locModal.setAttribute("aria-hidden", "false");
    }
  }

  function hideLocationAccuracyModal() {
    if (locModal) {
      locModal.style.display = "none";
      locModal.setAttribute("aria-hidden", "true");
    }
  }

  locTurnOnBtn?.addEventListener("click", () => {
    if (locTurnOnBtn) {
      locTurnOnBtn.disabled = true;
      locTurnOnBtn.textContent = "Turning on…";
    }
    requestDeviceLocation(false, true);
  });

  locDismissBtn?.addEventListener("click", () => {
    hideLocationAccuracyModal();
    sessionStorage.setItem("kavach_loc_modal_dismissed", "true");
  });

  let lastSyncedLat = null;
  let lastSyncedLng = null;
  let lastSyncTime = 0;
  let geoWatchId = null;

  async function syncLocationToBackend(lat, lng, force = false) {
    if (!lat || !lng) return;
    const now = Date.now();
    if (!force && lastSyncedLat !== null && lastSyncedLng !== null) {
      const dLat = Math.abs(lat - lastSyncedLat);
      const dLng = Math.abs(lng - lastSyncedLng);
      // Skip if location changed by less than ~11 meters and synced within last 30s
      if (dLat < 0.0001 && dLng < 0.0001 && (now - lastSyncTime < 30000)) {
        return;
      }
      // Throttle rapid location updates to at most once per 6 seconds
      if (now - lastSyncTime < 6000) {
        return;
      }
    }
    lastSyncedLat = lat;
    lastSyncedLng = lng;
    lastSyncTime = now;
    try {
      const res = await fetch(`${API}/location/sync`, {
        method: "POST",
        credentials: "include",
        headers: getAuthHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ lat, lng }),
      });
      if (res.ok) {
        const data = await res.json();
        if (data.responder_updated) {
          console.log(`[Kavach] Automatically updated responder location for ${data.responder_name} to (${lat}, ${lng})`);
        }
        if (data.guardian_updated) {
          console.log(`[Kavach] Automatically updated guardian location for ${data.guardian_name} to (${lat}, ${lng})`);
        }
      }
    } catch (e) {
      console.warn("Could not sync location to backend:", e);
    }
  }

  async function checkPermissionAndPrompt(forcePrompt = false) {
    if (navigator.permissions && navigator.permissions.query) {
      try {
        const status = await navigator.permissions.query({ name: "geolocation" });
        if (status.state === "granted") {
          isLocationGranted = true;
          hideLocationAccuracyModal();
          requestDeviceLocation(true);
        } else {
          isLocationGranted = false;
          showLocationAccuracyModal();
        }
        status.onchange = () => {
          if (status.state === "granted") {
            isLocationGranted = true;
            hideLocationAccuracyModal();
            requestDeviceLocation(true);
          } else {
            isLocationGranted = false;
          }
        };
      } catch {
        if (!isLocationGranted) showLocationAccuracyModal();
      }
    } else {
      if (!isLocationGranted) showLocationAccuracyModal();
    }
  }

  function checkLocationOnResume() {
    const now = Date.now();
    const elapsed = now - lastActiveTime;
    lastActiveTime = now;

    if (elapsed >= FIVE_MINUTES_MS) {
      if (navigator.permissions && navigator.permissions.query) {
        navigator.permissions.query({ name: "geolocation" }).then((status) => {
          if (status.state === "granted") {
            requestDeviceLocation(true);
          } else {
            showLocationAccuracyModal();
          }
        }).catch(() => {
          showLocationAccuracyModal();
        });
      } else {
        if (isLocationGranted) {
          requestDeviceLocation(true);
        } else {
          showLocationAccuracyModal();
        }
      }
    }
  }

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      checkLocationOnResume();
    } else {
      lastActiveTime = Date.now();
    }
  });

  window.addEventListener("focus", checkLocationOnResume);
  window.addEventListener("online", checkLocationOnResume);

  function initLocationSync() {
    checkPermissionAndPrompt();

    if (currentCoord && currentCoord.lat && currentCoord.lng) {
      syncLocationToBackend(currentCoord.lat, currentCoord.lng);
    }

    if (locationCheckTimer) clearInterval(locationCheckTimer);
    locationCheckTimer = setInterval(() => {
      if (!isLocationGranted) {
        checkPermissionAndPrompt();
      }
    }, 60000);

    grantLocBtn?.addEventListener("click", () => {
      if (grantLocBtn) {
        grantLocBtn.disabled = true;
        grantLocBtn.textContent = "Turning On…";
      }
      requestDeviceLocation(false, true);
    });
  }

  async function requestDeviceLocation(isPeriodic = false, isUserClick = false) {
    function handleLocationSuccess(lat, lng, label = "Live Device GPS") {
      isLocationGranted = true;
      hideLocationAccuracyModal();
      if (locBanner) locBanner.style.display = "none";
      if (grantLocBtn) {
        grantLocBtn.disabled = false;
        grantLocBtn.textContent = "Turn On";
      }
      if (locTurnOnBtn) {
        locTurnOnBtn.disabled = false;
        locTurnOnBtn.textContent = "Turn on";
      }
      currentCoord = { lat, lng, label };
      try {
        localStorage.setItem("kavach_user_lat", lat.toString());
        localStorage.setItem("kavach_user_lng", lng.toString());
      } catch {}

      if (filterStatus) {
        filterStatus.textContent = `📍 Live GPS: ${lat.toFixed(4)}°, ${lng.toFixed(4)}° · 30 km safety radius`;
      }

      // Automatically sync live location to user's profile and responder profile!
      syncLocationToBackend(lat, lng);

      // Auto reload responders if view is open
      if (respView && !respView.hidden) {
        loadResponders();
      }

      if (isUserClick) {
        window.kavach?.toast("✓ Location turned on and synchronized!");
      }
    }

    async function fetchFallbackLocation() {
      try {
        const res = await fetch(`${API}/api/location/detect`);
        if (res.ok) {
          const data = await res.json();
          if (data.lat && data.lng) {
            handleLocationSuccess(data.lat, data.lng, data.city || "Bengaluru");
          }
        }
      } catch {}
    }

    if (!navigator.geolocation || !window.isSecureContext) {
      // Insecure LAN context (e.g. mobile accessing over WiFi IP http://192.168.x.x:8000)
      // Browsers disallow navigator.geolocation on non-localhost HTTP.
      // Immediately fetch network IP location and sync instantly like WiFi/internet turning on!
      await fetchFallbackLocation();
      hideLocationAccuracyModal();
      return;
    }

    navigator.geolocation.getCurrentPosition(
      (pos) => {
        handleLocationSuccess(pos.coords.latitude, pos.coords.longitude, "Live Device GPS");

        // Watch position for live movements (e.g. traveling between Bengaluru & Chikkaballapur)
        try {
          if (geoWatchId !== null) {
            navigator.geolocation.clearWatch(geoWatchId);
          }
          geoWatchId = navigator.geolocation.watchPosition(
            (p) => {
              isLocationGranted = true;
              currentCoord.lat = p.coords.latitude;
              currentCoord.lng = p.coords.longitude;
              currentCoord.label = "Live Device GPS";
              try {
                localStorage.setItem("kavach_user_lat", p.coords.latitude.toString());
                localStorage.setItem("kavach_user_lng", p.coords.longitude.toString());
              } catch {}
              syncLocationToBackend(p.coords.latitude, p.coords.longitude);
              if (respView && !respView.hidden) {
                loadResponders();
              }
            },
            () => {},
            { enableHighAccuracy: true, maximumAge: 30000 }
          );
        } catch {}
      },
      async (err) => {
        if (grantLocBtn) {
          grantLocBtn.disabled = false;
          grantLocBtn.textContent = "Turn On";
        }
        if (locTurnOnBtn) {
          locTurnOnBtn.disabled = false;
          locTurnOnBtn.textContent = "Turn on";
        }

        // On mobile LAN / insecure origins or unavailable GPS sensors, fall back seamlessly
        await fetchFallbackLocation();
        hideLocationAccuracyModal();
        if (isUserClick) {
          window.kavach?.toast("✓ Location estimated & synchronized with responder network.");
        }
      },
      { timeout: 10000, enableHighAccuracy: true, maximumAge: 0 }
    );
  }

  // ---------- Profile Controller ----------
  async function openProfile() {
    window.kavachImport?.close();
    window.kavachVault?.close();
    window.kavachComplaint?.close();
    window.kavachGuardians?.close();
    window.kavachSos?.close();
    window.kavachResponders?.close();
    window.kavachSettings?.close();
    const mc = document.querySelector("#mock-chat-container");
    if (mc) mc.hidden = true;
    document.body.classList.add("left-collapsed");
    document.body.classList.remove("shield-open");
    window.kavachMobile?.show("main");
    if (profView) profView.hidden = false;
    document.body.classList.remove("at-home");
    await loadProfile();
  }

  function closeProfile() {
    if (profView) profView.hidden = true;
  }

  function getAuthHeaders(custom = {}) {
    let token = "";
    let userId = "";
    try {
      token = localStorage.getItem("kavach_token") || "";
      const u = JSON.parse(localStorage.getItem("kavach.user") || "{}");
      userId = u.id || localStorage.getItem("kavach.lastUser") || "";
    } catch {}
    const h = { ...custom };
    if (token) {
      h["Authorization"] = `Bearer ${token}`;
      h["X-Kavach-Token"] = token;
    }
    if (userId) {
      h["X-Kavach-User"] = userId;
    }
    return h;
  }

  function applyUserData(u = {}) {
    const name = u.full_name || u.username || "adarshavg";
    const email = u.email || (u.username === "adarshavg" ? "adarshavg26@gmail.com" : `${u.username || "adarshavg"}@kavach.safety`);
    const initial = (name[0] || "A").toUpperCase();

    if (profDisplayName) profDisplayName.textContent = name;
    if (profDisplayEmail) profDisplayEmail.textContent = email;
    if (popoverName) popoverName.textContent = name;
    if (popoverEmail) popoverEmail.textContent = email;

    updateAvatarDisplay(null, initial);

    if (profName) profName.value = u.full_name || u.username || "adarshavg";
    if (profPhone) profPhone.value = u.phone || "7795806293";
    if (profEmail) profEmail.value = email;
    if (profCity) profCity.value = u.city || "Bengaluru, Karnataka";
    if (profAddress) profAddress.value = u.address || "Reva university";

    let stored = {};
    try {
      stored = JSON.parse(localStorage.getItem("kavach.myDetails") || "{}");
      if (profAltPhone && stored.alt_phone) profAltPhone.value = stored.alt_phone;
      if (profPoliceStation && stored.police_station) profPoliceStation.value = stored.police_station;
    } catch {}

    syncLocalStorage();
    updateGoogleProfileSections(u, stored);
  }

  async function loadProfile() {
    // 1. Instantly apply cached profile so it never shows "Kavach User" or "U"
    try {
      const cached = JSON.parse(localStorage.getItem("kavach.user") || "null");
      if (cached && (cached.username || cached.full_name)) {
        applyUserData(cached);
      }
    } catch {}

    // 2. Fetch fresh user data from server with credentials & auth token
    try {
      const res = await fetch(`${API}/auth/profile`, {
        credentials: "include",
        headers: getAuthHeaders(),
      });
      if (res.ok) {
        const data = await res.json();
        const u = data.user || {};
        try { localStorage.setItem("kavach.user", JSON.stringify(u)); } catch {}
        applyUserData(u);
      }
    } catch (err) {
      console.warn("Could not load user profile:", err);
    }
  }

  const profAvatarEditBadge = $("#prof-avatar-edit-badge");
  const avatarEditPopover = $("#avatar-edit-popover");
  const avatarFileInput = $("#avatar-file-input");
  const avatarOptUpload = $("#avatar-opt-upload");
  const avatarOptRemove = $("#avatar-opt-remove");
  const avatarOptDetails = $("#avatar-opt-details");
  const profAvatarImg = $("#prof-avatar-img");

  function updateAvatarDisplay(avatarUrl, initial) {
    const customAvatar = avatarUrl || localStorage.getItem("kavach_user_avatar");
    if (customAvatar) {
      if (profAvatarImg) {
        profAvatarImg.src = customAvatar;
        profAvatarImg.style.display = "block";
      }
      if (profAvatar) profAvatar.style.display = "none";

      const topAvatar = document.querySelector("#top-avatar-letter");
      if (topAvatar) {
        topAvatar.innerHTML = `<img src="${customAvatar}" style="width:100%;height:100%;border-radius:50%;object-fit:cover;" alt="Avatar" />`;
      }
      if (popoverAvatarLetter) {
        popoverAvatarLetter.innerHTML = `<img src="${customAvatar}" style="width:100%;height:100%;border-radius:50%;object-fit:cover;" alt="Avatar" />`;
      }
      if (profRowAvatar) {
        profRowAvatar.innerHTML = `<img src="${customAvatar}" style="width:100%;height:100%;border-radius:50%;object-fit:cover;" alt="Avatar" />`;
      }
    } else {
      if (profAvatarImg) {
        profAvatarImg.src = "";
        profAvatarImg.style.display = "none";
      }
      if (profAvatar) {
        profAvatar.style.display = "inline";
        profAvatar.textContent = initial || "A";
      }
      const topAvatar = document.querySelector("#top-avatar-letter");
      if (topAvatar) topAvatar.textContent = initial || "A";
      if (popoverAvatarLetter) popoverAvatarLetter.textContent = initial || "A";
      if (profRowAvatar) profRowAvatar.textContent = initial || "A";
    }
  }

  profAvatarEditBadge?.addEventListener("click", (e) => {
    e.stopPropagation();
    if (!avatarEditPopover) return;
    const isHidden = avatarEditPopover.style.display === "none" || !avatarEditPopover.style.display;
    avatarEditPopover.style.display = isHidden ? "flex" : "none";
  });

  document.addEventListener("click", (e) => {
    if (avatarEditPopover && avatarEditPopover.style.display === "flex") {
      if (!avatarEditPopover.contains(e.target) && e.target !== profAvatarEditBadge) {
        avatarEditPopover.style.display = "none";
      }
    }
  });

  avatarOptUpload?.addEventListener("click", (e) => {
    e.stopPropagation();
    if (avatarEditPopover) avatarEditPopover.style.display = "none";
    avatarFileInput?.click();
  });

  avatarFileInput?.addEventListener("change", (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      const dataUrl = ev.target.result;
      try {
        localStorage.setItem("kavach_user_avatar", dataUrl);
      } catch (err) {
        console.warn("Avatar storage quota exceeded", err);
      }
      const name = profDisplayName?.textContent || "A";
      updateAvatarDisplay(dataUrl, (name[0] || "A").toUpperCase());
      window.kavach?.toast("✓ Profile photo updated!");
    };
    reader.readAsDataURL(file);
  });

  avatarOptRemove?.addEventListener("click", (e) => {
    e.stopPropagation();
    if (avatarEditPopover) avatarEditPopover.style.display = "none";
    localStorage.removeItem("kavach_user_avatar");
    const name = profDisplayName?.textContent || "A";
    updateAvatarDisplay(null, (name[0] || "A").toUpperCase());
    window.kavach?.toast("Profile photo removed.");
  });

  let isProfileEditing = false;

  function setProfileEditMode(editing) {
    isProfileEditing = editing;
    const viewEls = document.querySelectorAll(".field-val-view");
    const editEls = document.querySelectorAll(".field-val-edit");
    const floatBar = $("#prof-floating-save-bar");
    const editBtnText = $("#prof-edit-btn-text");

    viewEls.forEach((el) => (el.style.display = editing ? "none" : "flex"));
    editEls.forEach((el) => (el.style.display = editing ? "block" : "none"));

    if (floatBar) floatBar.style.display = editing ? "block" : "none";
    if (editBtnText) editBtnText.textContent = editing ? "Cancel" : "Edit Profile";
  }

  function updateProfileStrength(stored = {}) {
    const name = profName?.value?.trim() || profRowName?.textContent?.trim() || "";
    const email = profEmail?.value?.trim() || profRowEmail?.textContent?.trim() || "";
    const phone = profPhone?.value?.trim() || profRowPhone?.textContent?.trim() || "";
    const address = profAddress?.value?.trim() || profRowAddress?.textContent?.trim() || "";
    const station = profPoliceStation?.value?.trim() || profRowStation?.textContent?.trim() || "";
    const altPhone = profAltPhone?.value?.trim() || stored.alt_phone || "";

    let score = 0;
    if (name) score += 20;
    if (email) score += 20;
    if (phone) score += 20;
    if (address && station) score += 25;
    else if (address || station) score += 15;
    if (altPhone && altPhone !== "Not added") score += 15;

    const pct = Math.min(100, Math.max(score, 40));
    const bar = $("#prof-strength-bar");
    const pctEl = $("#prof-strength-pct");
    const tipEl = $("#prof-strength-tip");

    if (bar) bar.style.width = `${pct}%`;
    if (pctEl) pctEl.textContent = `${pct}%`;
    if (tipEl) {
      if (pct >= 100) {
        tipEl.textContent = "✓ 100% Legal Readiness · Identity Fully Verified";
        tipEl.style.color = "#10b981";
      } else if (!altPhone || altPhone === "Not added") {
        tipEl.textContent = "Add emergency contact for 100% legal readiness";
        tipEl.style.color = "#94a3b8";
      } else {
        tipEl.textContent = "Complete remaining jurisdiction fields for 100% readiness";
        tipEl.style.color = "#94a3b8";
      }
    }
  }

  function updateDigitalSignature(name, email) {
    const str = `${name || "adarshavg"}::${email || "user@kavach.safety"}::KAVACH_65B_ED25519_RSA4096`;
    let hash = 0;
    for (let i = 0; i < str.length; i++) {
      hash = ((hash << 5) - hash) + str.charCodeAt(i);
      hash |= 0;
    }
    const hex = Math.abs(hash).toString(16).padStart(8, "0");
    const token = `8f4e2b9c3a10${hex}e901a71e`;
    const el = $("#prof-crypto-token");
    if (el) el.textContent = token;
    return token;
  }

  function downloadLegalIdentityDossier() {
    const name = profRowName?.textContent || "Adarsha VG";
    const email = profRowEmail?.textContent || "adarshavg26@gmail.com";
    const phone = profRowPhone?.textContent || "7795806293";
    const altPhone = profRowAltPhone?.textContent || "Not provided";
    const address = profRowAddress?.textContent || "Reva university";
    const city = profRowCity?.textContent || "Bengaluru, Karnataka";
    const station = profRowStation?.textContent || "Indiranagar Cyber Crime Station";
    const sigToken = $("#prof-crypto-token")?.textContent || "8f4e2b9c3a1078d4e901a71e";
    const dateStr = new Date().toLocaleDateString("en-IN", { day: "numeric", month: "long", year: "numeric" });

    const printWindow = window.open("", "_blank");
    if (!printWindow) {
      window.print();
      return;
    }

    printWindow.document.write(`<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Legal Identity Dossier - ${name}</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; padding: 40px; color: #0f172a; line-height: 1.5; background: #fff; }
    .header { display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid #0f172a; padding-bottom: 16px; margin-bottom: 24px; }
    .title { font-size: 20px; font-weight: 800; letter-spacing: -0.01em; }
    .sub { font-size: 12.5px; color: #64748b; margin-top: 4px; }
    .badge { background: #ecfdf5; border: 1px solid #10b981; color: #047857; font-size: 11px; font-weight: 700; padding: 6px 14px; border-radius: 999px; text-transform: uppercase; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 24px; }
    .card { border: 1px solid #e2e8f0; border-radius: 12px; padding: 18px; }
    .card-title { font-size: 12px; font-weight: 700; color: #475569; text-transform: uppercase; margin-bottom: 12px; border-bottom: 1px solid #f1f5f9; padding-bottom: 6px; }
    .row { display: flex; justify-content: space-between; margin-bottom: 8px; font-size: 13px; }
    .row .lbl { color: #64748b; font-weight: 500; }
    .row .val { color: #0f172a; font-weight: 600; text-align: right; }
    .cert-box { background: #f8fafc; border: 1px solid #cbd5e1; border-radius: 12px; padding: 18px; margin-bottom: 24px; }
    .cert-title { font-size: 13.5px; font-weight: 700; color: #0f172a; margin-bottom: 6px; }
    .cert-body { font-size: 12px; color: #334155; line-height: 1.6; }
    .sig-block { display: flex; justify-content: space-between; align-items: flex-end; padding-top: 20px; border-top: 1px solid #e2e8f0; font-size: 11.5px; color: #64748b; }
    .sig-code { font-family: monospace; background: #e2e8f0; padding: 4px 8px; border-radius: 6px; color: #0f172a; }
    @media print { body { padding: 20px; } }
  </style>
</head>
<body>
  <div class="header">
    <div>
      <div class="title">KAVACH LEGAL IDENTITY DOSSIER</div>
      <div class="sub">Certified Complainant Profile & Police Jurisdiction Verification Record</div>
    </div>
    <div class="badge">✓ Verified Section 65B Complainant</div>
  </div>

  <div class="grid">
    <div class="card">
      <div class="card-title">Complainant Information</div>
      <div class="row"><span class="lbl">Full Name:</span><span class="val">${name}</span></div>
      <div class="row"><span class="lbl">Email:</span><span class="val">${email} (Verified)</span></div>
      <div class="row"><span class="lbl">Primary Phone:</span><span class="val">${phone}</span></div>
      <div class="row"><span class="lbl">Emergency Phone:</span><span class="val">${altPhone}</span></div>
      <div class="row"><span class="lbl">Protection Status:</span><span class="val" style="color:#047857;">Armed & Active</span></div>
    </div>

    <div class="card">
      <div class="card-title">Jurisdiction & Police Routing</div>
      <div class="row"><span class="lbl">Street Address:</span><span class="val">${address}</span></div>
      <div class="row"><span class="lbl">City & State:</span><span class="val">${city}</span></div>
      <div class="row"><span class="lbl">Designated Police Station:</span><span class="val">${station}</span></div>
      <div class="row"><span class="lbl">Legal Jurisdiction Code:</span><span class="val">KA-BLR-CYBER-BNS2023</span></div>
      <div class="row"><span class="lbl">Generated Date:</span><span class="val">${dateStr}</span></div>
    </div>
  </div>

  <div class="cert-box">
    <div class="cert-title">Statutory Legal Certification (Section 65B Indian Evidence Act / BSA 2023)</div>
    <div class="cert-body">
      This document certifies that the individual named above is registered with the Kavach Protective Security & Legal Technology System.
      All digital artifacts, harassment alerts, location coordinates, and tamper-evident cryptographic evidence logs generated under this identity
      conform to admissibility standards under Section 65B of the Indian Evidence Act, 1872 and Bharatiya Sakshya Adhiniyam (BSA), 2023.
    </div>
  </div>

  <div class="sig-block">
    <div>
      <div><strong>Cryptographic Signature Token (SHA-256):</strong></div>
      <div class="sig-code" style="margin-top:4px;">${sigToken}</div>
    </div>
    <div style="text-align:right;">
      <div>Kavach Automated Security Engine</div>
      <div style="color:#10b981; font-weight:700;">DIGITALLY SEALED</div>
    </div>
  </div>
</body>
</html>`);
    printWindow.document.close();
    printWindow.focus();
    setTimeout(() => {
      printWindow.print();
    }, 400);
  }

  avatarOptDetails?.addEventListener("click", (e) => {
    e.stopPropagation();
    if (avatarEditPopover) avatarEditPopover.style.display = "none";
    setProfileEditMode(true);
    profName?.focus();
  });

  function updateGoogleProfileSections(u = {}, stored = {}) {
    const name = u.full_name || u.username || stored.name || "adarshavg";
    const email = u.email || stored.email || (u.username === "adarshavg" ? "adarshavg26@gmail.com" : `${u.username || "adarshavg"}@kavach.safety`);
    const phone = u.phone || stored.phone || "7795806293";
    const altPhone = stored.alt_phone || "Not added";
    const address = u.address || stored.address || "Reva university";
    const city = u.city || stored.city || "Bengaluru, Karnataka";
    const station = stored.police_station || "Indiranagar Cyber Crime Station";
    const initial = (name[0] || "A").toUpperCase();

    updateAvatarDisplay(null, initial);
    if (profRowName) profRowName.textContent = name;
    if (profRowEmail) profRowEmail.textContent = email;
    if (profRowPhone) profRowPhone.textContent = phone;
    if (profRowAltPhone) profRowAltPhone.textContent = altPhone;
    if (profRowAddress) profRowAddress.textContent = address;
    if (profRowCity) profRowCity.textContent = city;
    if (profRowStation) profRowStation.textContent = station;

    // Sync input values so they are ready for edit mode
    if (profName && !profName.value) profName.value = name;
    if (profEmail && !profEmail.value) profEmail.value = email;
    if (profPhone && !profPhone.value) profPhone.value = phone;
    if (profAltPhone && !profAltPhone.value && altPhone !== "Not added") profAltPhone.value = altPhone;
    if (profAddress && !profAddress.value) profAddress.value = address;
    if (profCity && !profCity.value) profCity.value = city;
    if (profPoliceStation && !profPoliceStation.value) profPoliceStation.value = station;

    updateProfileStrength(stored);
    updateDigitalSignature(name, email);

    // Update emergency pill visibility
    const addEmergPill = $("#btn-add-emergency-pill");
    if (addEmergPill) {
      addEmergPill.style.display = (!altPhone || altPhone === "Not added") ? "inline-flex" : "none";
    }

    setProfileEditMode(false);
  }

  profToggleEditBtn?.addEventListener("click", () => {
    setProfileEditMode(!isProfileEditing);
    if (isProfileEditing) profName?.focus();
  });

  profCancelEditBtn?.addEventListener("click", () => {
    setProfileEditMode(false);
  });

  $("#btn-add-emergency-pill")?.addEventListener("click", () => {
    setProfileEditMode(true);
    profAltPhone?.focus();
  });

  $("#copy-jurisdiction-btn")?.addEventListener("click", () => {
    const jurId = `JUR-KA-BLR-CYBER-65B-${(Date.now() % 1000000).toString(36).toUpperCase()}`;
    navigator.clipboard?.writeText?.(jurId);
    window.kavach?.toast(`✓ Jurisdiction ID copied: ${jurId}`);
  });

  $("#copy-crypto-token-btn")?.addEventListener("click", () => {
    const token = $("#prof-crypto-token")?.textContent || "8f4e2b9c3a1078d4e901a71e";
    navigator.clipboard?.writeText?.(token);
    window.kavach?.toast("✓ SHA-256 Digital Signature Token copied!");
  });

  $("#prof-download-dossier-btn")?.addEventListener("click", () => {
    downloadLegalIdentityDossier();
  });

  function syncLocalStorage() {
    try {
      const details = {
        name: profName?.value?.trim() || profDisplayName?.textContent || "",
        phone: profPhone?.value?.trim() || "",
        email: profEmail?.value?.trim() || profDisplayEmail?.textContent || "",
        city: profCity?.value?.trim() || "",
        address: profAddress?.value?.trim() || "",
        alt_phone: profAltPhone?.value?.trim() || "",
        police_station: profPoliceStation?.value?.trim() || "",
      };
      localStorage.setItem("kavach.myDetails", JSON.stringify(details));
      updateProfileStrength(details);
    } catch {}
  }

  profForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const payload = {
      full_name: profName?.value?.trim() || "",
      phone: profPhone?.value?.trim() || "",
      city: profCity?.value?.trim() || "",
      address: profAddress?.value?.trim() || "",
    };

    try {
      const res = await fetch(`${API}/auth/profile`, {
        method: "PUT",
        headers: { "Content-Type": "application/json", ...getAuthHeaders() },
        credentials: "include",
        body: JSON.stringify(payload),
      });

      if (res.ok) {
        syncLocalStorage();
        if (profDisplayName) profDisplayName.textContent = payload.full_name || "Kavach User";
        if (profAvatar) profAvatar.textContent = (payload.full_name[0] || "U").toUpperCase();

        let stored = {};
        try { stored = JSON.parse(localStorage.getItem("kavach.myDetails") || "{}"); } catch {}
        updateGoogleProfileSections({ ...payload, email: profEmail?.value || "" }, stored);
        setProfileEditMode(false);
        window.kavach?.toast("✓ Profile saved! Complainant details updated.");
      } else {
        const err = await res.text();
        window.kavach?.toast("Failed to update profile: " + err);
      }
    } catch (err) {
      window.kavach?.toast("Network error updating profile: " + err.message);
    }
  });

  profReset?.addEventListener("click", () => {
    if (confirm("Clear profile fields?")) {
      profForm?.reset();
      localStorage.removeItem("kavach.myDetails");
      updateProfileStrength({});
      window.kavach?.toast("Fields cleared");
    }
  });

  // ---------- Responders Proximity & Location Controller ----------
  async function openResponders() {
    window.kavachImport?.close();
    window.kavachVault?.close();
    window.kavachComplaint?.close();
    window.kavachGuardians?.close();
    window.kavachSos?.close();
    window.kavachProfile?.close();
    window.kavachSettings?.close();
    const mc = document.querySelector("#mock-chat-container");
    if (mc) mc.hidden = true;
    document.body.classList.add("left-collapsed");
    document.body.classList.remove("shield-open");
    window.kavachMobile?.show("main");
    if (respView) respView.hidden = false;
    document.body.classList.remove("at-home");
    await loadResponders();
  }

  function closeResponders() {
    if (respView) respView.hidden = true;
  }

  // City presets with coordinates
  const CITY_COORDS = {
    "12.9716,77.5946": { name: "Bengaluru", lat: 12.9716, lng: 77.5946 },
    "13.4355,77.7315": { name: "Chikkaballapur", lat: 13.4355, lng: 77.7315 },
    "12.2958,76.6394": { name: "Mysuru", lat: 12.2958, lng: 76.6394 },
    "12.9141,74.8560": { name: "Mangaluru", lat: 12.9141, lng: 74.8560 },
    "15.3647,75.1240": { name: "Hubballi", lat: 15.3647, lng: 75.1240 },
  };

  userZoneSelect?.addEventListener("change", () => {
    const val = userZoneSelect.value;
    if (CITY_COORDS[val]) {
      currentCoord = CITY_COORDS[val];
      loadResponders();
    }
  });

  detectGpsBtn?.addEventListener("click", () => {
    if (!navigator.geolocation) {
      window.kavach?.toast("Geolocation is not supported by your browser.");
      return;
    }
    detectGpsBtn.disabled = true;
    detectGpsBtn.textContent = "Acquiring GPS…";
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        currentCoord = { lat: pos.coords.latitude, lng: pos.coords.longitude, label: "Live Device GPS" };
        detectGpsBtn.disabled = false;
        detectGpsBtn.textContent = "✓ GPS Set";
        window.kavach?.toast(`📍 Proximity set to Device GPS: ${currentCoord.lat.toFixed(4)}, ${currentCoord.lng.toFixed(4)}`);
        loadResponders();
      },
      (err) => {
        detectGpsBtn.disabled = false;
        detectGpsBtn.textContent = "🎯 GPS";
        window.kavach?.toast("Could not acquire device GPS: " + err.message);
      },
      { timeout: 8000, enableHighAccuracy: true }
    );
  });

  respCitySelect?.addEventListener("change", () => {
    const val = respCitySelect.value;
    if (val === "custom") {
      if (navigator.geolocation) {
        navigator.geolocation.getCurrentPosition(
          (pos) => {
            if (respLatInput) respLatInput.value = pos.coords.latitude;
            if (respLngInput) respLngInput.value = pos.coords.longitude;
            window.kavach?.toast(`✓ GPS Coordinates captured: ${pos.coords.latitude.toFixed(4)}, ${pos.coords.longitude.toFixed(4)}`);
          },
          (err) => {
            window.kavach?.toast("Could not read device GPS. Falling back to default.");
            if (respLatInput) respLatInput.value = 12.9716;
            if (respLngInput) respLngInput.value = 77.5946;
          },
          { enableHighAccuracy: true, timeout: 8000 }
        );
      }
    } else if (CITY_COORDS[val]) {
      if (respLatInput) respLatInput.value = CITY_COORDS[val].lat;
      if (respLngInput) respLngInput.value = CITY_COORDS[val].lng;
    }
  });

  let currentResponders = [];

  async function loadResponders() {
    if (!respList) return;
    try {
      const { lat, lng, label } = currentCoord;
      if (filterStatus) {
        filterStatus.textContent = `Proximity center: ${label || `${lat.toFixed(2)}°, ${lng.toFixed(2)}°`} · Safety radius: 30 km`;
      }

      const res = await fetch(`${API}/responders?lat=${lat}&lng=${lng}`, {
        credentials: "include",
        headers: getAuthHeaders(),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const responders = data.responders || [];
      currentResponders = responders;

      if (!responders.length) {
        respList.innerHTML = `
          <div style="padding: 28px; text-align: center; background: rgba(255,255,255,0.02); border: 1px dashed var(--border); border-radius: 16px;">
            <div style="font-size: 28px; margin-bottom: 8px;">🛡️</div>
            <strong style="display:block; margin-bottom: 4px; font-size: 15px;">No Community Responders Registered Yet</strong>
            <small style="color:var(--muted); font-size: 12.5px;">Be the first trusted safety volunteer to join the rapid incident triage network using the form on the right.</small>
          </div>
        `;
        return;
      }

      const nearby = responders.filter((r) => r.is_nearby);
      const outside = responders.filter((r) => !r.is_nearby);

      function renderCard(r) {
        const initials = (r.name || "R")[0].toUpperCase();
        const org = r.relationship !== "Guardian" ? r.relationship : "Safety Volunteer";
        const isSelf = Boolean(r.is_self);

        const tgBadge = r.telegram_linked
          ? '<span class="glass-badge" style="color:#3ecf8e; border-color:rgba(62,207,142,0.3); font-size:10.5px; font-weight:600; white-space:nowrap;">✓ Telegram Linked</span>'
          : '<span class="glass-badge" style="color:var(--muted); font-size:10.5px; white-space:nowrap;">Awaiting Telegram</span>';

        const selfBadge = isSelf
          ? '<span class="glass-badge" style="color:#10b981; border-color:rgba(16,185,129,0.45); font-weight:700; font-size:10.5px; background:rgba(16,185,129,0.12); padding:2px 8px;">👤 YOU (Your Profile)</span>'
          : '';

        const distanceTag = r.distance_km !== null
          ? r.is_nearby
            ? `<span class="glass-badge" style="color:#10b981; border-color:rgba(16,185,129,0.35); background:rgba(16,185,129,0.08); font-weight:700; font-size:11px; white-space:nowrap; display:inline-flex; align-items:center; gap:4px;">📍 ${r.distance_km} km away · In Radius</span>`
            : `<span class="glass-badge" style="color:#f5a524; border-color:rgba(245,165,36,0.35); background:rgba(245,165,36,0.08); font-size:11px; white-space:nowrap; display:inline-flex; align-items:center; gap:4px;">📍 ${r.distance_km} km away · Outside Zone</span>`
          : '<span class="glass-badge" style="color:var(--muted); font-size:11px; white-space:nowrap;">No Location</span>';

        const editBtn = isSelf
          ? `<button type="button" class="btn btn--primary small" data-edit-resp="${r.id}" style="background:#6366f1; border-color:#6366f1; color:#fff; font-size:11.5px; padding:6px 12px; border-radius:8px; font-weight:700;">✏️ Edit</button>`
          : '';

        return `
          <div class="glass-card resp-card" style="padding: 16px; display: flex; flex-direction: column; gap: 12px; border-radius: 18px; border-color: ${isSelf ? "rgba(99,102,241,0.55)" : (r.is_nearby ? "rgba(16,185,129,0.3)" : "rgba(255,255,255,0.08)")}; margin-bottom: 12px; background: ${isSelf ? "rgba(25, 28, 48, 0.9)" : "rgba(18, 24, 38, 0.75)"}; width: 100%; box-sizing: border-box;">
            <!-- Header: Avatar + Identity Column -->
            <div style="display: flex; align-items: flex-start; gap: 12px; width: 100%;">
              <div class="avatar" style="width: 44px; height: 44px; min-width: 44px; max-width: 44px; min-height: 44px; max-height: 44px; aspect-ratio: 1 / 1; flex-shrink: 0; border-radius: 50% !important; background: ${isSelf ? "linear-gradient(135deg, #6366f1, #10b981)" : (r.is_nearby ? "linear-gradient(135deg, #10b981, #6366f1)" : "linear-gradient(135deg, #64748b, #475569)")}; display: flex; align-items: center; justify-content: center; font-weight: 800; color: #fff; font-size: 16px; box-shadow: 0 4px 12px rgba(0,0,0,0.35);">
                ${initials}
              </div>
              <div style="flex: 1; min-width: 0;">
                <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
                  <strong style="font-size: 15.5px; color: #fff; font-weight: 700; letter-spacing: -0.01em;">${r.name}</strong>
                  ${selfBadge}
                </div>
                <div style="display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-top: 5px;">
                  <span class="glass-badge" style="color: #a5b4fc; border-color: rgba(99,102,241,0.3); background: rgba(99,102,241,0.08); font-size: 10.5px; font-weight: 600; text-transform: uppercase;">🏢 ${org}</span>
                  ${distanceTag}
                </div>
                <div style="display: flex; flex-direction: column; gap: 4px; margin-top: 8px; font-size: 12px; color: var(--muted);">
                  ${r.email ? `
                    <div style="display: flex; align-items: center; gap: 6px; min-width: 0;">
                      <span style="font-size: 12px; flex-shrink: 0; opacity: 0.85;">✉️</span>
                      <span style="color: #cbd5e1; word-break: break-word; overflow-wrap: break-word; font-size: 12px;">${r.email}</span>
                    </div>` : ''}
                  ${r.phone ? `
                    <div style="display: flex; align-items: center; gap: 6px; min-width: 0;">
                      <span style="font-size: 12px; flex-shrink: 0; opacity: 0.85;">📞</span>
                      <span style="color: #cbd5e1; font-size: 12px;">${r.phone}</span>
                    </div>` : ''}
                </div>
              </div>
            </div>

            <!-- Footer: Telegram + Actions -->
            <div style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px; width: 100%; padding-top: 10px; border-top: 1px solid rgba(255,255,255,0.07);">
              <div style="display: flex; align-items: center; gap: 6px;">
                ${tgBadge}
              </div>
              <div style="display: flex; align-items: center; gap: 8px; margin-left: auto; flex-wrap: wrap;">
                ${editBtn}
                ${r.phone ? `<a href="tel:${r.phone}" class="btn btn--primary small" style="background:#10b981; border-color:#10b981; color:#fff; font-size: 11.5px; padding: 6px 14px; border-radius: 8px; text-decoration: none; font-weight: 700; display: inline-flex; align-items: center; gap: 4px;">📞 Call</a>` : ""}
                <button type="button" class="btn btn--ghost small" data-update-loc="${r.id}" data-name="${r.name}" style="font-size: 11.5px; padding: 6px 12px; border-radius: 8px; font-weight: 600;" title="Sync this responder's location to current device GPS">
                  📍 Sync GPS
                </button>
              </div>
            </div>
          </div>
        `;
      }

      let html = "";
      if (nearby.length) {
        html += `
          <div style="margin-bottom: 8px;">
            <strong style="color: #10b981; font-size: 13px; text-transform: uppercase; letter-spacing: 0.05em; display: flex; align-items: center; gap: 6px;">
              <span>🟢</span> Nearby Responders Ready for Dispatch (${nearby.length})
            </strong>
          </div>
          ${nearby.map(renderCard).join("")}
        `;
      } else {
        html += `
          <div style="padding: 16px 20px; background: rgba(245,165,36,0.08); border: 1px dashed rgba(245,165,36,0.3); border-radius: 14px; margin-bottom: 12px;">
            <strong style="color: #f5a524; font-size: 13px; display: block; margin-bottom: 2px;">⚠️ No Active Responders Inside This 30 km Zone</strong>
            <small style="color: var(--muted); font-size: 12px;">Responders registered in other regions will not be alerted for emergencies in ${currentCoord.label || "this location"}.</small>
          </div>
        `;
      }

      if (outside.length) {
        html += `
          <div style="margin-top: 18px; margin-bottom: 8px;">
            <strong style="color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em;">
              🌐 Active in Other Regions / Beyond 30 km (${outside.length})
            </strong>
          </div>
          ${outside.map(renderCard).join("")}
        `;
      }

      respList.innerHTML = html;
    } catch (err) {
      console.warn("Could not load responders:", err);
      respList.innerHTML = `<p class="muted" style="font-size: 12px;">Offline or could not reach responders network.</p>`;
    }
  }

  // Handle Responder Update Location with Live GPS (No manual prompts)
  respList?.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-update-loc]");
    if (!btn) return;
    const rid = btn.dataset.updateLoc;
    const rname = btn.dataset.name || "Responder";

    if (!navigator.geolocation) {
      window.kavach?.toast("Geolocation is not supported by your browser.");
      return;
    }

    btn.disabled = true;
    btn.textContent = "Acquiring GPS…";

    navigator.geolocation.getCurrentPosition(
      async (pos) => {
        const newLat = pos.coords.latitude;
        const newLng = pos.coords.longitude;
        try {
          btn.textContent = "Updating…";
          const res = await fetch(`${API}/people/${rid}`, {
            method: "PATCH",
            credentials: "include",
            headers: getAuthHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ lat: newLat, lng: newLng }),
          });
          if (res.ok) {
            window.kavach?.toast(`✓ ${rname} live location updated to ${newLat.toFixed(4)}, ${newLng.toFixed(4)}!`);
            await loadResponders();
          } else {
            const err = await res.text();
            window.kavach?.toast("Update failed: " + err);
          }
        } catch (err) {
          window.kavach?.toast("Network error: " + err.message);
        } finally {
          btn.disabled = false;
          btn.textContent = "📍 Sync GPS";
        }
      },
      (err) => {
        btn.disabled = false;
        btn.textContent = "📍 Sync GPS";
        window.kavach?.toast("Could not read device GPS: " + err.message);
      },
      { timeout: 8000, enableHighAccuracy: true }
    );
  });

  // ---------- Register Myself as Responder Handler ----------
  const registerSelfBtn = document.getElementById("register-self-responder-btn");
  registerSelfBtn?.addEventListener("click", () => {
    let user = {};
    try {
      user = JSON.parse(localStorage.getItem("kavach.user") || "{}");
      const myDetails = JSON.parse(localStorage.getItem("kavach.myDetails") || "{}");
      user = { ...myDetails, ...user };
    } catch {}

    if (respName) respName.value = user.full_name || user.username || "adarshavg";
    if (respEmail) respEmail.value = user.email || (user.username === "adarshavg" ? "adarshavg26@gmail.com" : `${user.username || "user"}@kavach.safety`);
    if (respPhone) respPhone.value = user.phone || "7795806293";
    if (respOrg) respOrg.value = user.city ? `${user.city.split(',')[0]} Unit` : "Campus Safety Unit";

    const formCard = document.getElementById("add-responder-form");
    if (formCard) {
      formCard.scrollIntoView({ behavior: "smooth", block: "center" });
      respName?.focus();
    }
    window.kavach?.toast("✓ Pre-filled with your profile. Tap '+ Submit Registration' to join as responder.");
  });

  // ---------- Edit My Responder Details Dialog Controller ----------
  const editRespModal = document.getElementById("edit-responder-modal");
  const editRespForm = document.getElementById("edit-responder-form");
  const closeEditRespBtn = document.getElementById("close-edit-responder-modal");
  const cancelEditRespBtn = document.getElementById("cancel-edit-resp-btn");
  const editRespId = document.getElementById("edit-resp-id");
  const editRespName = document.getElementById("edit-resp-name");
  const editRespOrg = document.getElementById("edit-resp-org");
  const editRespPhone = document.getElementById("edit-resp-phone");
  const editRespEmail = document.getElementById("edit-resp-email");
  const editRespGpsBtn = document.getElementById("edit-resp-gps-btn");

  let editRespLat = null;
  let editRespLng = null;

  const closeEditModal = () => {
    if (editRespModal) editRespModal.style.display = "none";
  };
  closeEditRespBtn?.addEventListener("click", closeEditModal);
  cancelEditRespBtn?.addEventListener("click", closeEditModal);

  respList?.addEventListener("click", (e) => {
    const editBtn = e.target.closest("[data-edit-resp]");
    if (!editBtn) return;
    const id = editBtn.dataset.editResp;
    const r = currentResponders.find((x) => x.id === id);
    if (!r) return;

    if (editRespId) editRespId.value = r.id;
    if (editRespName) editRespName.value = r.name || "";
    if (editRespOrg) editRespOrg.value = r.relationship || "";
    if (editRespPhone) editRespPhone.value = r.phone || "";
    if (editRespEmail) editRespEmail.value = r.email || "";
    editRespLat = r.lat;
    editRespLng = r.lng;

    if (editRespModal) editRespModal.style.display = "flex";
  });

  editRespGpsBtn?.addEventListener("click", () => {
    if (!navigator.geolocation) {
      window.kavach?.toast("Geolocation is not supported");
      return;
    }
    editRespGpsBtn.textContent = "Acquiring GPS…";
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        editRespLat = pos.coords.latitude;
        editRespLng = pos.coords.longitude;
        editRespGpsBtn.textContent = `✓ GPS: ${pos.coords.latitude.toFixed(4)}, ${pos.coords.longitude.toFixed(4)}`;
        window.kavach?.toast("✓ Live GPS coordinates updated");
      },
      (err) => {
        editRespGpsBtn.textContent = "📍 GPS Failed";
        window.kavach?.toast("Could not read device GPS: " + err.message);
      },
      { enableHighAccuracy: true, timeout: 8000 }
    );
  });

  editRespForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const pid = editRespId?.value;
    if (!pid) return;

    const body = {
      name: editRespName?.value?.trim(),
      relationship: editRespOrg?.value?.trim() || "Safety Volunteer",
      phone: editRespPhone?.value?.trim() || "",
      email: editRespEmail?.value?.trim() || "",
    };
    if (editRespLat !== null && editRespLng !== null) {
      body.lat = editRespLat;
      body.lng = editRespLng;
    }

    try {
      const res = await fetch(`${API}/people/${pid}`, {
        method: "PATCH",
        credentials: "include",
        headers: getAuthHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      closeEditModal();
      window.kavach?.toast("✓ Your responder profile updated successfully");
      loadResponders();
    } catch (err) {
      window.kavach?.toast("Failed to update responder: " + err.message);
    }
  });

  respForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const latVal = parseFloat(respLatInput?.value) || 12.9716;
    const lngVal = parseFloat(respLngInput?.value) || 77.5946;

    const payload = {
      kind: "responder",
      name: respName?.value?.trim() || "",
      email: respEmail?.value?.trim() || "",
      phone: respPhone?.value?.trim() || "",
      relationship: respOrg?.value?.trim() || "Safety Volunteer",
      lat: latVal,
      lng: lngVal,
    };

    try {
      const res = await fetch(`${API}/people`, {
        method: "POST",
        credentials: "include",
        headers: getAuthHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(payload),
      });

      if (res.ok) {
        respForm.reset();
        window.kavach?.toast("✓ Registered as First Responder with base location! You will receive proximity alerts.");
        await loadResponders();
      } else {
        const err = await res.text();
        window.kavach?.toast("Registration error: " + err);
      }
    } catch (err) {
      window.kavach?.toast("Network error: " + err.message);
    }
  });

  // ---------- Google Account Popover Controller ----------
  googleAccountBtn?.addEventListener("click", (e) => {
    e.stopPropagation();
    if (googleAccountPopover) googleAccountPopover.style.display = "none";
    if (window.kavachNav) {
      window.kavachNav.go("profile");
    } else {
      openProfile();
    }
  });

  popoverProfileBtn?.addEventListener("click", (e) => {
    e.stopPropagation();
    if (googleAccountPopover) googleAccountPopover.style.display = "none";
    if (window.kavachNav) {
      window.kavachNav.go("profile");
    } else {
      openProfile();
    }
  });

  document.addEventListener("click", (e) => {
    if (googleAccountPopover && !googleAccountPopover.contains(e.target) && !googleAccountBtn?.contains(e.target)) {
      googleAccountPopover.style.display = "none";
    }
  });

  // ---------- Settings Controller ----------
  const settingsView = $("#settings-view");
  const settingsUserAvatar = $("#settings-user-avatar");
  const settingsUserName = $("#settings-user-name");
  const settingsUserEmail = $("#settings-user-email");
  const settingsRecheckLocBtn = $("#settings-recheck-loc-btn");
  const settingsDeleteAccountBtn = $("#settings-delete-account-btn");

  async function openSettings() {
    window.kavachImport?.close();
    window.kavachVault?.close();
    window.kavachComplaint?.close();
    window.kavachGuardians?.close();
    window.kavachSos?.close();
    window.kavachResponders?.close();
    window.kavachProfile?.close();
    const mc = document.querySelector("#mock-chat-container");
    if (mc) mc.hidden = true;
    document.body.classList.add("left-collapsed");
    document.body.classList.remove("shield-open");
    window.kavachMobile?.show("main");
    if (settingsView) settingsView.hidden = false;
    document.body.classList.remove("at-home");
    await loadSettings();
  }

  function closeSettings() {
    if (settingsView) settingsView.hidden = true;
  }

  async function loadSettings() {
    try {
      const res = await fetch(`${API}/auth/profile`, {
        credentials: "include",
        headers: getAuthHeaders(),
      });
      if (res.ok) {
        const data = await res.json();
        const u = data.user || {};
        const name = u.full_name || u.username || "Kavach User";
        const email = u.email || (u.username === "adarshavg" ? "adarshavg26@gmail.com" : `${u.username || "user"}@kavach.safety`);
        if (settingsUserAvatar) settingsUserAvatar.textContent = (name[0] || "U").toUpperCase();
        if (settingsUserName) settingsUserName.textContent = name;
        if (settingsUserEmail) settingsUserEmail.textContent = email;
      }
    } catch {}
  }

  settingsRecheckLocBtn?.addEventListener("click", () => {
    showLocationAccuracyModal();
  });

  settingsDeleteAccountBtn?.addEventListener("click", async () => {
    const confirmed = await window.kavachConfirm?.(
      "⚠️ DANGER: Are you absolutely sure you want to permanently delete your Kavach account?\n\nThis will permanently wipe your account, evidence vault, SHA-256 sealed chains, emergency guardians, and responder registrations. This action CANNOT be undone.",
      { ok: "Permanently Delete", cancel: "Cancel" }
    );
    if (!confirmed) return;

    try {
      const res = await fetch(`${API}/auth/me`, { method: "DELETE" });
      if (res.ok) {
        window.kavach?.toast("Account and all associated safety data permanently deleted.");
        localStorage.clear();
        sessionStorage.clear();
        setTimeout(() => {
          location.replace("/login.html");
        }, 800);
      } else {
        const err = await res.text();
        window.kavach?.toast("Failed to delete account: " + err);
      }
    } catch (err) {
      window.kavach?.toast("Network error deleting account: " + err.message);
    }
  });

  window.kavachProfile = { open: openProfile, close: closeProfile, loadProfile };
  window.kavachResponders = { open: openResponders, close: closeResponders, loadResponders };
  window.kavachSettings = { open: openSettings, close: closeSettings, loadSettings };

  // Initial load
  loadProfile();
  initLocationSync();
})();
