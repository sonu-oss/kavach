// Top navigation, the home screen, and the phone layout.
//
// The feature screens (Shield, Vault, SOS) already know how to open themselves;
// this file leaves / returns to the home screen, keeps the nav highlight right, and on
// phones shows one pane at a time (chat list, main screen, or side panel).
(() => {
  const API = new URLSearchParams(location.search).get("api") || "";
  const body = document.body;
  const links = [...document.querySelectorAll(".nav-link[data-nav]")];
  const tabs = [...document.querySelectorAll(".tabbar [data-tab]")];
  const phone = window.matchMedia("(max-width: 820px)");

  function setActive(name) {
    links.forEach((l) => l.classList.toggle("active", l.dataset.nav === name));
    tabs.forEach((t) => t.classList.toggle("active", t.dataset.tab === (name || "home")));
    document.querySelectorAll(".desktop-nav-item[data-nav]").forEach((b) => {
      b.classList.toggle("active", b.dataset.nav === (name || "home"));
    });
  }

  function leaveHome() {
    body.classList.remove("at-home");
  }

  function goHome() {
    body.classList.add("at-home");
    setActive("home");
    mobile.pill(null);
  }

  function closeAllViews() {
    window.kavachVault?.close();
    window.kavachImport?.close();
    window.kavachComplaint?.close();
    window.kavachSos?.close();
    window.kavachResponders?.close();
    window.kavachProfile?.close();
    window.kavachSettings?.close();

    const views = ["#import-view", "#vault-view", "#complaint-view", "#guardians-view", "#safetyspace-view", "#responders-view", "#profile-view", "#settings-view"];
    views.forEach((sel) => {
      const el = document.querySelector(sel);
      if (el) el.hidden = true;
    });

    const mc = document.querySelector("#mock-chat-container");
    if (mc) mc.hidden = true;

    const chatArea = document.querySelector("#chat-area");
    if (chatArea) chatArea.hidden = true;

    mobile.pill(null);
    body.classList.remove("vaulting", "sosing", "calling");
  }

  function go(where) {
    body.classList.remove("menu-open", "shield-open", "shield-mode");
    mobile.show("main");
    if (where === "home") {
      closeAllViews();
      return goHome();
    }
    leaveHome();
    closeAllViews();

    const stTrigger = document.querySelector("#safetyspace-mobile-trigger");
    const contactsEyebrow = document.querySelector("#contacts-eyebrow");

    if (where === "safetyspace" || where === "shield") {
      window.kavachSos?.close();
      const gView = document.querySelector("#guardians-view");
      if (gView) gView.hidden = true;
      const el = document.querySelector("#safetyspace-view");
      if (el) el.hidden = false;

      const safetyspaceHub = document.getElementById("safetyspace-hub");
      const safetyspacePhone = document.getElementById("safetyspace-phone-container");
      if (safetyspaceHub) safetyspaceHub.style.display = "flex";
      if (safetyspacePhone) safetyspacePhone.style.display = "none";

      body.classList.add("left-collapsed");
      setActive("safetyspace");
    } else if (where === "guardians" || where === "sos") {
      const sView = document.querySelector("#safetyspace-view");
      if (sView) sView.hidden = true;
      window.kavachSos?.open();
      window.kavachSos?.loadSetup?.();
      const el = document.querySelector("#guardians-view");
      if (el) el.hidden = false;
      body.classList.add("left-collapsed");
      setActive("guardians");
    } else if (where === "scan") {
      window.kavachSos?.close();
      window.kavachImport?.open();
      const iv = document.querySelector("#import-view");
      if (iv) iv.hidden = false;
      body.classList.add("left-collapsed");
      setActive("safetyspace");
    } else if (where === "vault") {
      window.kavachVault?.open();
      const el = document.querySelector("#vault-view");
      if (el) el.hidden = false;
      body.classList.remove("left-collapsed");
      if (stTrigger) stTrigger.style.display = "none";
      if (contactsEyebrow) contactsEyebrow.textContent = "Previous Evidence";
      setActive("vault");
    } else if (where === "responders") {
      window.kavachResponders?.open();
      const el = document.querySelector("#responders-view");
      if (el) el.hidden = false;
      body.classList.add("left-collapsed");
      if (stTrigger) stTrigger.style.display = "none";
      setActive("responders");
    } else if (where === "profile") {
      window.kavachProfile?.open();
      const el = document.querySelector("#profile-view");
      if (el) el.hidden = false;
      body.classList.add("left-collapsed");
      if (stTrigger) stTrigger.style.display = "none";
      setActive("profile");
    } else if (where === "settings") {
      window.kavachSettings?.open();
      const el = document.querySelector("#settings-view");
      if (el) el.hidden = false;
      body.classList.add("left-collapsed");
      if (stTrigger) stTrigger.style.display = "none";
      setActive("settings");
    }
  }

  // Mobile Menu Drawer Toggle (3 lines button)
  const mobileMenuBtn = document.querySelector("#mobile-menu-btn");
  const closeSidebarMenuBtn = document.querySelector("#close-sidebar-menu-btn");
  mobileMenuBtn?.addEventListener("click", (e) => {
    e.stopPropagation();
    body.classList.toggle("menu-open");
  });
  closeSidebarMenuBtn?.addEventListener("click", (e) => {
    e.stopPropagation();
    body.classList.remove("menu-open");
  });

  // Mobile Sidebar Dashboard links
  document.querySelectorAll(".sidebar-nav-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      body.classList.remove("menu-open");
      const navTarget = btn.dataset.nav;
      if (navTarget) go(navTarget);
    });
  });

  // Sidebar Kavach AI button (symbol + name, borderless)
  document.querySelector("#sidebar-ai-btn")?.addEventListener("click", () => {
    body.classList.remove("menu-open");
    body.classList.add("shield-open");
  });

  // Navbar Auto-Hide on Scroll Down, Show on Scroll Up
  let lastScrollY = window.scrollY;
  const topbar = document.querySelector(".topbar");
  window.addEventListener("scroll", () => {
    const currentScrollY = window.scrollY;
    if (currentScrollY > 50 && currentScrollY > lastScrollY + 8) {
      topbar?.classList.add("topbar--hidden");
    } else if (currentScrollY < lastScrollY - 6 || currentScrollY <= 20) {
      topbar?.classList.remove("topbar--hidden");
    }
    lastScrollY = currentScrollY;
  }, { passive: true });

  // Sidebar Toggles (Left Collapsible Sidebar & Right AI Drawer)
  document.querySelector("#toggle-left-sidebar-btn")?.addEventListener("click", () => {
    body.classList.toggle("left-collapsed");
  });

  document.querySelector("#toggle-ai-sidebar-btn")?.addEventListener("click", () => {
    body.classList.toggle("shield-open");
  });

  document.querySelector("#close-ai-panel-btn")?.addEventListener("click", () => {
    body.classList.remove("shield-open");
  });

  // ---------- phone layout: one pane at a time ----------
  const pill = document.querySelector("#m-pill");
  let pillLabel = null;
  const mobile = {
    show(view) {
      body.classList.remove("m-list", "m-main", "m-panel");
      body.classList.add(`m-${view}`);
      pill.hidden = view !== "main" || !pillLabel;
      if (phone.matches) window.scrollTo(0, 0);
    },
    // The floating button that opens the side panel ("Risk 85 · High ›", "Details ›"...).
    pill(label) {
      pillLabel = label;
      pill.textContent = label || "";
      pill.hidden = !label || !body.classList.contains("m-main") || body.classList.contains("at-home");
    },
  };
  window.kavachMobile = mobile;

  pill?.addEventListener("click", () => mobile.show("panel"));
  document.querySelector("#panel-back")?.addEventListener("click", () => mobile.show("main"));
  document.querySelector("#chat-back")?.addEventListener("click", () => {
    mobile.show("list");
    mobile.pill(null);
  });
  // Opening a chat or the screenshot import moves to the main pane and closes menu drawer
  document.querySelector("#contact-list")?.addEventListener("click", (e) => {
    leaveHome();
    body.classList.remove("menu-open");
    if (e.target.closest(".contact")) mobile.show("main");
    setActive("shield");
  });
  document.querySelector("#import-btn")?.addEventListener("click", () => {
    leaveHome();
    closeAllViews();
    body.classList.remove("menu-open");
    mobile.show("main");
  });

  // Profile avatar button in topbar: opens User Profile dashboard directly
  document.querySelector("#google-account-btn")?.addEventListener("click", (e) => {
    e.stopPropagation();
    const popover = document.querySelector("#google-account-popover");
    if (popover) popover.style.display = "none";
    go("profile");
  });

  // Desktop 3-Line Menu Drawer / Dropdown Toggle
  const desktopMenuBtn = document.querySelector("#desktop-menu-btn");
  const desktopDashboardsMenu = document.querySelector("#desktop-dashboards-menu");

  function closeDesktopMenu() {
    if (desktopDashboardsMenu) {
      desktopDashboardsMenu.style.display = "none";
      desktopDashboardsMenu.classList.remove("is-open");
    }
  }

  desktopMenuBtn?.addEventListener("click", (e) => {
    e.stopPropagation();
    if (desktopDashboardsMenu) {
      const isShown = desktopDashboardsMenu.style.display === "flex";
      if (isShown) {
        closeDesktopMenu();
      } else {
        desktopDashboardsMenu.style.display = "flex";
        desktopDashboardsMenu.classList.add("is-open");
      }
    }
  });

  document.querySelectorAll(".desktop-nav-item[data-nav]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      closeDesktopMenu();
      const navTarget = btn.dataset.nav;
      if (navTarget) go(navTarget);
    });
  });

  document.querySelector("#desktop-ai-menu-item")?.addEventListener("click", (e) => {
    e.stopPropagation();
    closeDesktopMenu();
    body.classList.add("shield-open");
  });

  document.querySelector("#desktop-menu-logout-btn")?.addEventListener("click", async (e) => {
    e.stopPropagation();
    closeDesktopMenu();
    if (await window.kavachConfirm?.("Log out of Kavach on this device?", { ok: "Log out" })) {
      await fetch(`${API}/auth/logout`, { method: "POST" }).catch(() => {});
      try {
        ["kavach_token", "kavach.user", "kavach.sosSetup", "kavach.myDetails", "kavach.lastUser", "kavach_user_lat", "kavach_user_lng"].forEach((k) => localStorage.removeItem(k));
      } catch {}
      location.replace("/login.html");
    }
  });

  // Close menus when clicking outside
  document.addEventListener("click", (e) => {
    if (desktopDashboardsMenu && !desktopDashboardsMenu.contains(e.target) && !desktopMenuBtn?.contains(e.target)) {
      closeDesktopMenu();
    }
    if (body.classList.contains("menu-open")) {
      const contactsEl = document.querySelector(".contacts");
      const menuBtn = document.querySelector("#mobile-menu-btn");
      if (contactsEl && !contactsEl.contains(e.target) && !menuBtn?.contains(e.target)) {
        body.classList.remove("menu-open");
      }
    }
  });

  // ---------- top nav + tab bar ----------
  // Top nav bar: clicking any section switches directly to it
  links.forEach((l) =>
    l.addEventListener("click", () => {
      go(l.dataset.nav);
    })
  );
  tabs.forEach((t) => t.addEventListener("click", () => go(t.dataset.tab)));
  document.querySelectorAll("[data-go]").forEach((el) => el.addEventListener("click", () => go(el.dataset.go)));
  document.querySelector("#home-link").addEventListener("click", goHome);
  document.querySelector("#settings-back-btn")?.addEventListener("click", goHome);
  document.querySelector("#quick-exit")?.addEventListener("click", () => {
    window.kavachImport?.close();
    window.kavachVault?.close();
    window.kavachComplaint?.close();
    window.kavachSos?.close();
    goHome();
  });

  // Default motion and normal contrast enabled
  body.classList.remove("reduce-motion");
  body.classList.remove("high-contrast");
  try {
    localStorage.removeItem("bloom.reduceMotion");
    localStorage.removeItem("bloom.highContrast");
  } catch {}

  setActive(null);
  window.kavachHome = goHome;
  window.kavachNav = { go, goHome, leaveHome, closeAllViews, setActive };

  // Reset handler (if button exists or triggered programmatically)
  document.querySelector("#reset-all")?.addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    if (!(await kavachConfirm("Reset everything? This ends any running SOS and deletes sealed evidence. Contacts and responders stay.", { ok: "Reset", danger: true }))) return;
    btn.disabled = true;
    btn.textContent = "Resetting…";
    try {
      const res = await fetch(`${new URLSearchParams(location.search).get("api") || ""}/reset`, { method: "POST" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      try {
        localStorage.removeItem("kavach.myDetails");
      } catch {}
      location.reload();
    } catch (err) {
      btn.disabled = false;
      btn.textContent = "Reset everything";
      window.kavach?.toast(`Reset failed: ${err.message}`);
    }
  });

  // ---------- Scan → Check → Seal → Report bar ----------
  const steps = [...document.querySelectorAll("#flowbar [data-step]")];
  window.kavachFlow = {
    set(n) {
      steps.forEach((s) => {
        const k = Number(s.dataset.step);
        s.classList.toggle("done", k < n);
        s.classList.toggle("current", k === n);
      });
    },
  };

  // Installable app + offline shell (https or localhost only).
  if ("serviceWorker" in navigator && window.isSecureContext) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }

  // Handle deep-links from WhatsApp extension or direct URLs (#guardians, #sos, #vault, #shield, #scan, #responders, #profile)
  function handleDeepLink() {
    const hash = (location.hash || "").replace("#", "").toLowerCase();
    if (!hash) return;
    if (hash === "guardians" || hash === "sos" || hash === "safetyspace") {
      setTimeout(() => go("guardians"), 150);
    } else if (hash === "vault") {
      setTimeout(() => go("vault"), 150);
    } else if (hash === "responders") {
      setTimeout(() => go("responders"), 150);
    } else if (hash === "profile") {
      setTimeout(() => go("profile"), 150);
    } else if (hash === "shield" || hash === "scan") {
      setTimeout(() => go(hash), 150);
    }
  }

  window.addEventListener("hashchange", handleDeepLink);
  setTimeout(handleDeepLink, 250);

  (async () => {
    const status = document.querySelector("#nav-status");
    try {
      const h = await (await fetch(`${API}/health`)).json();
      status.textContent = h.llm_configured ? "AI on" : "Keyword mode";
    } catch {
      status.textContent = "Offline";
    }
  })();
})();
