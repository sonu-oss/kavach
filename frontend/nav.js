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
  }

  function leaveHome() {
    body.classList.remove("at-home");
  }

  function goHome() {
    body.classList.add("at-home");
    setActive(null);
    mobile.pill(null);
  }

  function go(where) {
    if (where === "home") return goHome();
    leaveHome();
    if (where === "shield") {
      // On a phone, Shield starts at the chat list; on a laptop, straight into a chat.
      if (phone.matches) {
        mobile.show("list");
        mobile.pill(null);
      } else {
        window.kavach.openShield();
      }
      setActive("shield");
    } else if (where === "scan") {
      document.querySelector("#import-btn").click();
      setActive("shield");
    } else if (where === "vault") {
      window.kavachVault.open();
      setActive("vault");
    } else if (where === "sos") {
      window.kavachSos.open();
      setActive("sos");
    }
  }

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

  pill.addEventListener("click", () => mobile.show("panel"));
  document.querySelector("#panel-back").addEventListener("click", () => mobile.show("main"));
  document.querySelector("#chat-back").addEventListener("click", () => {
    mobile.show("list");
    mobile.pill(null);
  });
  // Opening a chat or the screenshot import moves her to the main pane.
  document.querySelector("#contact-list").addEventListener("click", (e) => {
    if (e.target.closest(".contact")) mobile.show("main");
    setActive("shield");
  });
  document.querySelector("#import-btn").addEventListener("click", () => mobile.show("main"));

  // ---------- top nav + tab bar ----------
  // Vault and SOS nav buttons also have their own click handlers (vault.js, sos.js),
  // so for those we only need to leave home and set the highlight.
  links.forEach((l) =>
    l.addEventListener("click", () => {
      if (l.dataset.nav === "shield") return go("shield");
      leaveHome();
      setActive(l.dataset.nav);
    })
  );
  tabs.forEach((t) => t.addEventListener("click", () => go(t.dataset.tab)));
  document.querySelectorAll("[data-go]").forEach((el) => el.addEventListener("click", () => go(el.dataset.go)));
  document.querySelector("#home-link").addEventListener("click", goHome);
  setActive(null);

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
