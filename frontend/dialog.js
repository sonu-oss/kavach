// In-page replacements for confirm() / alert(). Some browsers (in-app webviews, embedded
// previews, iOS home-screen apps in some modes) block native popups and return "Cancel"
// at once, which made every Clear / Reset / Delete button silently do nothing.
(() => {
  const css = document.createElement("style");
  css.textContent = `
    .kdialog { border: 1px solid var(--border-strong, #3a3a3a); border-radius: 12px; padding: 22px;
      width: min(420px, calc(100vw - 32px)); background: var(--surface, #111); color: var(--text, #f4ede4);
      font: 15px/1.5 Inter, system-ui, sans-serif; }
    .kdialog::backdrop { background: rgba(0, 0, 0, 0.6); }
    .kdialog p { margin: 0 0 18px; white-space: pre-wrap; overflow-wrap: anywhere; }
    .kdialog .kdialog-actions { display: flex; gap: 10px; justify-content: flex-end; flex-wrap: wrap; }
    .kdialog .kdialog-danger { background: var(--risk-high, #ff4d4f); border-color: transparent; color: #fff; }`;
  document.head.appendChild(css);

  function show(message, { ok = "OK", cancel = "Cancel", danger = false, alertOnly = false } = {}) {
    return new Promise((resolve) => {
      const d = document.createElement("dialog");
      d.className = "kdialog";
      d.innerHTML = `<p></p><div class="kdialog-actions">
        ${alertOnly ? "" : `<button type="button" class="btn" data-v="0"></button>`}
        <button type="button" class="btn ${danger ? "kdialog-danger" : "btn--primary"}" data-v="1"></button></div>`;
      d.querySelector("p").textContent = message;
      d.querySelector('[data-v="1"]').textContent = ok;
      if (!alertOnly) d.querySelector('[data-v="0"]').textContent = cancel;
      const done = (v) => { d.close(); d.remove(); resolve(v); };
      d.addEventListener("click", (e) => {
        const b = e.target.closest("[data-v]");
        if (b) done(b.dataset.v === "1");
        else if (e.target === d) done(false); // click on the backdrop
      });
      d.addEventListener("cancel", (e) => { e.preventDefault(); done(false); }); // Esc
      document.body.appendChild(d);
      d.showModal();
      d.querySelector(danger && !alertOnly ? '[data-v="0"]' : '[data-v="1"]').focus(); // Enter never deletes by accident
    });
  }

  // Clipboard API first, then the older execCommand route (works where the API is denied).
  window.kavachCopy = async (text) => {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      const ta = Object.assign(document.createElement("textarea"), { value: text });
      ta.style.cssText = "position:fixed;opacity:0";
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand("copy");
      ta.remove();
      return ok;
    }
  };

  // Signed out or session expired: any API call answering 401 sends the page to the login screen.
  const nativeFetch = window.fetch.bind(window);
  window.fetch = async (input, init) => {
    const res = await nativeFetch(input, init);
    const url = new URL(typeof input === "string" ? input : input.url, location.href);
    if (res.status === 401 && url.origin === location.origin && !url.pathname.startsWith("/auth/")
        && !/\/(login|responder|track|connect)\.html$/.test(location.pathname)) {
      location.replace("/login.html");
    }
    return res;
  };

  // Log out: ends the session on the server, then back to the login screen.
  document.addEventListener("click", async (e) => {
    if (!e.target.closest("#logout-btn")) return;
    if (!(await show("Log out of Kavach on this device?", { ok: "Log out" }))) return;
    await nativeFetch("/auth/logout", { method: "POST", credentials: "same-origin" }).catch(() => {});
    location.replace("/login.html");
  });

  window.kavachConfirm = (message, opts) => show(message, opts);
  window.kavachAlert = (message) => show(message, { alertOnly: true });
})();
