// Top navigation and the home screen.
//
// The feature screens (Shield, Vault, SOS) already know how to open themselves;
// this file only leaves / returns to the home screen and keeps the nav highlight right.
(() => {
  const API = new URLSearchParams(location.search).get("api") || "";
  const body = document.body;
  const links = [...document.querySelectorAll(".nav-link[data-nav]")];

  function setActive(name) {
    links.forEach((l) => l.classList.toggle("active", l.dataset.nav === name));
  }

  function leaveHome() {
    body.classList.remove("at-home");
  }

  function go(where) {
    leaveHome();
    if (where === "shield") {
      window.kavach.openShield();
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

  // Vault and SOS nav buttons also have their own click handlers (vault.js, sos.js),
  // so for those we only need to leave home and set the highlight.
  links.forEach((l) =>
    l.addEventListener("click", () => {
      if (l.dataset.nav === "shield") return go("shield");
      leaveHome();
      setActive(l.dataset.nav);
    })
  );

  document.querySelectorAll("[data-go]").forEach((el) => el.addEventListener("click", () => go(el.dataset.go)));

  document.querySelector("#home-link").addEventListener("click", () => {
    body.classList.add("at-home");
    setActive(null);
  });

  // Picking a chat from the list (e.g. while in the Vault) means she is back in Shield.
  document.querySelector("#contact-list").addEventListener("click", () => setActive("shield"));

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
