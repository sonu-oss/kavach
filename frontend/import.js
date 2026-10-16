// Screenshot import for Smart Shield.
//
// The victim pastes / drops / picks screenshots of a real chat. OCR runs in this
// browser (Tesseract.js), so the image never leaves the device; only the
// extracted text is sent to /analyze, where PII is masked before the LLM.
// Who sent each message is inferred from bubble position (right = you,
// left = them), then the victim reviews and fixes the result before scanning.
(() => {
  const $ = (sel) => document.querySelector(sel);
  const els = {
    btn: $("#import-btn"),
    view: $("#import-view"),
    dropzone: $("#dropzone"),
    file: $("#file-input"),
    sample: $("#sample-btn"),
    progress: $("#ocr-progress"),
    bar: $("#ocr-progress .ocr-progress__bar span"),
    status: $("#ocr-status"),
    shots: $("#shots"),
    review: $("#review"),
    list: $("#review-list"),
    name: $("#other-name"),
    platform: $("#platform"),
    date: $("#incident-date"),
    addRow: $("#add-row"),
    cancel: $("#import-cancel"),
    scan: $("#import-scan"),
    hint: $("#import-hint"),
  };

  const SAMPLES = ["samples/sample-hinglish.png", "samples/sample-kannada.png"];
  const OCR_STATUS = {
    "loading tesseract core": "Loading text reader…",
    "initializing tesseract": "Starting text reader…",
    "loading language traineddata": "Loading language data (first time only)…",
    "initializing api": "Starting text reader…",
    "recognizing text": "Reading messages…",
  };

  let rows = []; // { sender: "them" | "me", text, time, lowConf }
  let images = []; // data URLs, kept for the Evidence Vault
  let queue = Promise.resolve();
  let workerPromise = null;
  let sampleIndex = 0;
  let isOpen = false;

  // ---------- open / close ----------
  function open() {
    if (isOpen) return;
    window.kavachVault?.close();
    window.kavachComplaint?.close();
    window.kavachSos?.close();
    window.kavachFlow?.set(1);
    isOpen = true;
    rows = [];
    images = [];
    sampleIndex = 0;
    els.name.value = "";
    els.platform.value = "";
    els.platform.classList.remove("missing");
    els.date.value = localDate(new Date());
    els.date.max = localDate(new Date());
    els.shots.innerHTML = "";
    els.view.classList.remove("has-shots");
    renderRows();
    document.body.classList.add("importing");
    els.view.hidden = false;
    window.kavach.deselect("Paste a chat screenshot. Kavach reads it on this device, then you check the messages and scan.");
    els.dropzone.focus();
  }

  function close() {
    if (!isOpen) return;
    isOpen = false;
    document.body.classList.remove("importing");
    els.view.hidden = true;
  }

  // ---------- input: paste, drop, pick, sample ----------
  document.addEventListener("paste", (e) => {
    const files = [...(e.clipboardData?.files || [])].filter((f) => f.type.startsWith("image/"));
    if (!files.length) return; // plain text paste into inputs works as normal
    e.preventDefault();
    open();
    files.forEach(addImage);
  });

  ["dragenter", "dragover"].forEach((t) =>
    els.view.addEventListener(t, (e) => {
      e.preventDefault();
      els.dropzone.classList.add("drag");
    })
  );
  ["dragleave", "drop"].forEach((t) =>
    els.view.addEventListener(t, (e) => {
      e.preventDefault();
      if (t === "dragleave" && els.view.contains(e.relatedTarget)) return;
      els.dropzone.classList.remove("drag");
    })
  );
  els.view.addEventListener("drop", (e) => {
    [...e.dataTransfer.files].filter((f) => f.type.startsWith("image/")).forEach(addImage);
  });

  els.file.addEventListener("change", () => {
    [...els.file.files].forEach(addImage);
    els.file.value = "";
  });

  els.sample.addEventListener("click", async () => {
    const url = SAMPLES[sampleIndex++ % SAMPLES.length];
    const blob = await (await fetch(url)).blob();
    addImage(blob);
  });

  els.btn.addEventListener("click", open);
  els.cancel.addEventListener("click", () => {
    close();
    const first = window.kavach.conversations[0];
    if (first) document.querySelector(`.contact[data-id="${first.id}"]`)?.click();
  });

  // Screenshots are processed one at a time, in the order they were added.
  function addImage(blob) {
    queue = queue.then(() => processImage(blob)).catch((err) => {
      console.error(err);
      showProgress(false);
      window.kavach.toast("Couldn't read that image. You can type the messages instead.");
      if (!rows.length) rows.push({ sender: "them", text: "", time: "", lowConf: false });
      renderRows();
    });
  }

  // ---------- OCR ----------
  async function processImage(blob) {
    const dataUrl = await blobToDataUrl(blob);
    const img = await loadImage(dataUrl);
    images.push(dataUrl);
    addThumb(dataUrl);

    showProgress(true, 0, "Loading text reader…");
    const worker = await getWorker();
    const prepared = prepare(img);
    const { data } = await worker.recognize(prepared.canvas);
    showProgress(false);

    const parsed = parseChat(data.lines || [], prepared.canvas.width, prepared.canvas.height);
    if (parsed.name && !els.name.value) els.name.value = parsed.name;
    if (!els.platform.value) els.platform.classList.add("missing");
    const fresh = dropOverlap(rows, parsed.rows);
    rows.push(...fresh);
    renderRows();
    if (!parsed.rows.length) window.kavach.toast("No chat text found in that image.");
  }

  function getWorker() {
    if (!window.Tesseract) throw new Error("Tesseract.js did not load (offline?)");
    workerPromise ||= Tesseract.createWorker("eng", 1, {
      logger: (m) => showProgress(true, m.progress, OCR_STATUS[m.status] || "Reading messages…"),
    }).then(async (w) => {
      await w.setParameters({ preserve_interword_spaces: "1" });
      return w;
    });
    return workerPromise;
  }

  // Upscale small screenshots and normalise to dark text on a light background,
  // which is what Tesseract reads best. Dark-mode screenshots get inverted.
  function prepare(img) {
    // Only upscale small images; phone screenshots (1080px+) read best at native size.
    const scale = Math.min(3, Math.max(1, 1000 / img.naturalWidth));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(img.naturalWidth * scale);
    canvas.height = Math.round(img.naturalHeight * scale);
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);

    const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height);
    const d = pixels.data;
    let sum = 0;
    for (let i = 0; i < d.length; i += 4) {
      const lum = 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
      d[i] = d[i + 1] = d[i + 2] = lum;
      sum += lum;
    }
    if (sum / (d.length / 4) < 110) {
      for (let i = 0; i < d.length; i += 4) d[i] = d[i + 1] = d[i + 2] = 255 - d[i];
    }
    ctx.putImageData(pixels, 0, 0);
    return { canvas };
  }

  // ---------- turning OCR lines into chat messages ----------
  // OCR often misreads digits in small timestamp text ("1s:52", "os:10", "19.05"),
  // and turns read-receipt ticks into "vv" or "«/".
  const TIME_AT_END = /\s*([0-9oOsSlI]{1,2}[:.\-][0-9oOsSlI]{2})\s*(?:[ap]\.?\s?m\.?)?\s*(?:[✓✔vV/\\»«|]{1,3})?\s*$/;
  const STATUS_BAR = /^[0-9oOsSlI]{1,2}[:.][0-9oO]{2}\b/;
  const fixDigits = (t) => t.replace(/[oO]/g, "0").replace(/[sS]/g, "8").replace(/[lI]/g, "1").replace(/[.\-]/, ":");
  const DATE_LINE = /^(today|yesterday|\d{1,2}\s+[a-z]{3,9}(\s+\d{4})?|[a-z]{3,9}\s+\d{1,2},?(\s+\d{4})?|(mon|tues|wednes|thurs|fri|satur|sun)day)$/i;
  const UI_LINE = /^(type a message|message|send a message|message\.\.\.|online|typing\.\.\.|last seen.*|seen|delivered|read)$/i;
  const JUNK = /^[\W_\d]{0,3}$/;

  function parseChat(rawLines, W, H) {
    const isFullPhone = H / W > 1.7;
    const lines = rawLines
      .map((l) => ({
        // Tesseract reads a lone capital I as "|".
        text: l.text.replace(/\s+/g, " ").trim().replace(/(^|\s)\|(?=\s)/g, "$1I"),
        conf: l.confidence,
        x0: l.bbox.x0,
        x1: l.bbox.x1,
        y0: l.bbox.y0,
        y1: l.bbox.y1,
      }))
      .filter((l) => l.text && l.conf > 30 && !JUNK.test(l.text))
      .sort((a, b) => a.y0 - b.y0);

    // Full phone screenshots: the top strip is the status bar + contact header
    // (use it to guess the name), the bottom strip is the message box.
    let name = "";
    const body = lines.filter((l) => {
      if (isFullPhone && l.y1 < H * 0.11) {
        const inStatusBar = l.y1 < H * 0.045 || STATUS_BAR.test(l.text);
        if (!name && !inStatusBar && /[a-z]{2,}/i.test(l.text)) name = guessName(l.text);
        return false;
      }
      if (isFullPhone && l.y0 > H * 0.92) return false;
      return true;
    });

    const heights = body.map((l) => l.y1 - l.y0).sort((a, b) => a - b);
    const lineH = heights[Math.floor(heights.length / 2)] || 20;

    const out = [];
    let cur = null;
    let prev = null;
    for (const l of body) {
      let text = l.text;
      let time = "";
      const t = text.match(TIME_AT_END);
      if (t) {
        time = fixDigits(t[1]);
        text = text.slice(0, t.index).trim();
      }
      if (DATE_LINE.test(text) || UI_LINE.test(text)) {
        prev = null;
        continue;
      }

      const leftGap = l.x0 / W;
      const rightGap = (W - l.x1) / W;
      let side = Math.abs(leftGap - rightGap) < 0.06 ? cur?.sender || "them" : leftGap > rightGap ? "me" : "them";

      const gap = prev ? l.y0 - prev.y1 : Infinity;
      const startNew = !cur || cur.closed || side !== cur.sender || gap > lineH * 0.8;
      if (startNew) {
        if (!text && time && cur) {
          // A timestamp sitting on its own line belongs to the bubble above it.
          cur.time ||= time;
          cur.closed = true;
          prev = l;
          continue;
        }
        cur = { sender: side, text: "", time: "", confs: [], closed: false };
        out.push(cur);
      }
      if (text) cur.text = cur.text ? `${cur.text} ${text}` : text;
      cur.confs.push(l.conf);
      if (time) {
        cur.time = time;
        cur.closed = true; // timestamps end a bubble
      }
      prev = l;
    }

    return {
      name,
      rows: out
        .filter((m) => m.text)
        .map((m) => ({
          sender: m.sender,
          text: m.text,
          time: m.time,
          lowConf: m.confs.reduce((a, b) => a + b, 0) / m.confs.length < 70,
        })),
    };
  }

  function guessName(text) {
    // Strip back arrows, avatars read as letters, and status words.
    return text
      .replace(/^[<←‹«(\s\W]+/, "")
      .replace(/\b(online|typing\.*|last seen.*)$/i, "")
      .trim()
      .slice(0, 60);
  }

  // Consecutive screenshots usually overlap by a few messages; skip repeats.
  function dropOverlap(existing, incoming) {
    const norm = (s) => s.toLowerCase().replace(/\W+/g, "");
    const tail = new Set(existing.slice(-6).map((r) => norm(r.text)));
    let i = 0;
    while (i < incoming.length && tail.has(norm(incoming[i].text))) i++;
    return incoming.slice(i);
  }

  // ---------- review list ----------
  function renderRows() {
    els.review.hidden = rows.length === 0;
    updateScanButton();
    els.list.innerHTML = "";
    rows.forEach((r, i) => {
      const li = document.createElement("li");
      li.className = `review__row ${r.sender}`;

      const pill = document.createElement("button");
      pill.type = "button";
      pill.className = "sender-pill";
      pill.textContent = r.sender === "me" ? "You" : "Them";
      pill.title = "Switch who sent this";
      pill.onclick = () => {
        r.sender = r.sender === "me" ? "them" : "me";
        renderRows();
      };

      const ta = document.createElement("textarea");
      ta.rows = Math.min(4, Math.ceil(r.text.length / 60) || 1);
      ta.value = r.text;
      ta.placeholder = "Message text";
      if (r.lowConf) {
        ta.classList.add("low-conf");
        ta.title = "This line was hard to read. Please check it.";
      }
      ta.oninput = () => {
        r.text = ta.value;
        r.lowConf = false;
        ta.classList.remove("low-conf");
        updateScanButton();
      };

      const time = document.createElement("span");
      time.className = "row-time";
      time.textContent = r.time;

      const del = document.createElement("button");
      del.type = "button";
      del.className = "row-del";
      del.textContent = "×";
      del.title = "Remove this line";
      del.onclick = () => {
        rows.splice(i, 1);
        renderRows();
      };

      li.append(pill, ta, time, del);
      els.list.append(li);
    });
  }

  // A complaint must name the real app, so the platform is required before scanning.
  // Say why the button is disabled right next to it, not in a tooltip nobody sees.
  function updateScanButton() {
    const hasText = rows.some((r) => r.text.trim());
    const missing = !rows.length ? "" : !els.platform.value ? "↑ Choose which app this chat is from" : !hasText ? "Add at least one message" : "";
    els.scan.disabled = Boolean(missing) || !rows.length;
    els.hint.textContent = missing;
    els.hint.hidden = !missing;
  }
  els.platform.addEventListener("change", () => {
    els.platform.classList.toggle("missing", !els.platform.value);
    updateScanButton();
  });
  els.hint.addEventListener("click", () => {
    if (els.platform.value) return;
    els.platform.scrollIntoView({ behavior: "smooth", block: "center" });
    els.platform.focus();
  });

  els.addRow.addEventListener("click", () => {
    rows.push({ sender: "them", text: "", time: "", lowConf: false });
    renderRows();
    els.list.lastElementChild?.querySelector("textarea").focus();
  });

  // ---------- hand-off to the Shield ----------
  els.scan.addEventListener("click", () => {
    const kept = rows.filter((r) => r.text.trim());
    if (!kept.length || !els.platform.value) return;
    const name = els.name.value.trim() || "Screenshot chat";
    const handle = name.toLowerCase().replace(/[^\w.]+/g, "_").slice(0, 40) || "sender";
    const id = `shot-${Date.now()}`;
    const conv = {
      id,
      name,
      platform: els.platform.value,
      incident_date: els.date.value || null,
      subtitle: `${els.platform.value} · from ${images.length} screenshot${images.length === 1 ? "" : "s"}`,
      avatar: (name.match(/[a-z0-9]/i)?.[0] || "?").toUpperCase(),
      source: "screenshot",
      images, // kept for the Evidence Vault (Feature 2)
      messages: kept.map((r, i) => ({
        id: `${id}-${i + 1}`,
        sender: r.sender === "me" ? "me" : handle === "me" ? "sender" : handle,
        text: r.text.trim(),
        time: r.time || "",
      })),
    };
    close();
    window.kavach.addConversation(conv);
    if (!document.querySelector("#auto-scan").checked) window.kavach.scan();
  });

  // ---------- small helpers ----------
  function showProgress(on, progress = 0, label = "") {
    els.progress.hidden = !on;
    if (!on) return;
    els.bar.style.width = `${Math.round((progress || 0) * 100)}%`;
    if (label) els.status.textContent = label;
  }

  function addThumb(src) {
    els.view.classList.add("has-shots");
    const div = document.createElement("div");
    div.className = "shot";
    const img = document.createElement("img");
    img.src = src;
    img.alt = "Imported screenshot";
    const n = document.createElement("span");
    n.textContent = els.shots.children.length + 1;
    div.append(img, n);
    els.shots.append(div);
  }

  // YYYY-MM-DD in her local time zone (toISOString would give UTC, a day off late at night in India).
  function localDate(d) {
    return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  }

  function blobToDataUrl(blob) {
    return new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => resolve(r.result);
      r.onerror = reject;
      r.readAsDataURL(blob);
    });
  }

  function loadImage(src) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = reject;
      img.src = src;
    });
  }

  window.kavachImport = { open, close, parseChat };
})();
