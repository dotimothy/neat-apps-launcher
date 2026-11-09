/* Neat Apps Launcher: vanilla JS, no build step. */
(function () {
  "use strict";

  const S = window.LAUNCHER.static;
  const $ = (sel, el) => (el || document).querySelector(sel);
  const $$ = (sel, el) => Array.from((el || document).querySelectorAll(sel));
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const icon = (name, cls) => `<svg${cls ? ` class="${cls}"` : ""}><use href="#i-${name}"/></svg>`;

  const state = {
    apps: {},          // id -> catalog fields + live snapshot
    order: [],
    platform: null,    // sima-cli / bundle / Insight / samples + the setup job
    system: null,
    updates: {},       // launcher | bundle -> check result
    route: { page: "home" },
    expanded: new Set(),
    filter: { q: "", cat: "", lang: "", input: "", status: "" },
  };


  // ------------------------------------------------------------------ launch animation
  // A Modalix chip warps in through a starfield (canvas, slowing as the chip
  // lands) and bursts into the Neat palette (CSS). The markup is kept so the
  // animation can be replayed from the header button or with ?intro.
  const INTRO_EL = document.getElementById("intro");
  const INTRO_HTML = INTRO_EL ? INTRO_EL.outerHTML : "";
  let introPlaying = false;

  function firstVisit() {
    try { return localStorage.getItem("neatAppsWelcomed") !== "1"; } catch (e) { return false; }
  }

  // stay: first visit, so the intro waits on its "Explore the Apps" button.
  function playIntro(el, stay) {
    return new Promise((resolve) => {
      if (stay) el.classList.add("stay");
      introPlaying = true;
      try { sessionStorage.setItem("neatAppsIntroPlayed", "1"); } catch (e) { /* ignore */ }
      const cv = el.querySelector(".intro-stars");
      const ctx = cv.getContext("2d");
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      let W = 0, H = 0;
      const size = () => { W = cv.width = innerWidth * dpr; H = cv.height = innerHeight * dpr; };
      size(); addEventListener("resize", size);
      const COLORS = ["#ffffff", "#ffffff", "#ffffff", "#16c8a6", "#3a86ec", "#a9c81c", "#f2801d"];
      const stars = Array.from({ length: 520 }, () => ({
        x: (Math.random() - .5) * 2, y: (Math.random() - .5) * 2, z: Math.random(), pz: 0,
        c: COLORS[(Math.random() * COLORS.length) | 0],
      }));
      const t0 = performance.now();
      let raf = 0, finished = false;
      const frame = (now) => {
        const t = (now - t0) / 1000;
        // fast warp, easing to a drift by the time the chip lands (~2.2 s)
        const speed = t < 2.2 ? 0.045 * Math.pow(1 - t / 2.2, 1.6) + 0.0025 : 0.0025;
        ctx.fillStyle = "rgba(2,4,9,0.38)";
        ctx.fillRect(0, 0, W, H);
        const cx = W / 2, cy = H / 2, k = Math.max(W, H) * 0.5;
        for (const st of stars) {
          st.pz = st.z;
          st.z -= speed;
          if (st.z <= 0.02) { st.x = (Math.random() - .5) * 2; st.y = (Math.random() - .5) * 2; st.z = 1; st.pz = 1; continue; }
          const sx = cx + (st.x / st.z) * k, sy = cy + (st.y / st.z) * k;
          const px = cx + (st.x / st.pz) * k, py = cy + (st.y / st.pz) * k;
          ctx.strokeStyle = st.c; ctx.globalAlpha = Math.min(1, (1 - st.z) * 1.4);
          ctx.lineWidth = Math.max(0.6, (1 - st.z) * 2.6) * dpr;
          ctx.beginPath(); ctx.moveTo(px, py); ctx.lineTo(sx, sy); ctx.stroke();
        }
        ctx.globalAlpha = 1;
        if (t < 3.2 && !finished) raf = requestAnimationFrame(frame);
      };
      raf = requestAnimationFrame(frame);
      const onKey = (e) => {
        if (stay) { if (e.key === "Escape") el.querySelector(".intro-explore").click(); return; }
        if (e.key === "Escape" || e.key === "Enter" || e.key === " ") { e.preventDefault(); skip(); }
      };
      const finish = () => {
        if (finished) return;
        finished = true; introPlaying = false;
        cancelAnimationFrame(raf);
        removeEventListener("resize", size);
        removeEventListener("keydown", onKey);
        el.remove(); resolve();
      };
      const skip = () => { el.classList.add("skip"); setTimeout(finish, 260); };
      el.querySelector(".intro-skip").addEventListener("click", skip);
      el.querySelector(".intro-explore").addEventListener("click", () => {
        try { localStorage.setItem("neatAppsWelcomed", "1"); } catch (e) { /* ignore */ }
        skip();
        setTimeout(() => { location.hash = "#apps"; }, 300);
      });
      addEventListener("keydown", onKey);
      if (stay) setTimeout(() => { const b = el.querySelector(".intro-explore"); if (b && b.isConnected) b.focus({ preventScroll: true }); }, 4500);
      if (!stay) {
        el.addEventListener("dblclick", skip);
        setTimeout(finish, 6850);
      }
    });
  }

  function replayIntro() {
    if (introPlaying || !INTRO_HTML) return Promise.resolve();
    const wrap = document.createElement("div");
    wrap.innerHTML = INTRO_HTML;
    const el = wrap.firstElementChild;
    el.removeAttribute("id");
    el.classList.add("replay");                // shows even under :root.no-intro
    document.body.prepend(el);
    return playIntro(el);
  }

  const introDone = (() => {
    if (!INTRO_EL) return Promise.resolve();
    const forced = /[?&]intro\b/.test(location.search);
    if (document.documentElement.classList.contains("no-intro") && !forced) { INTRO_EL.remove(); return Promise.resolve(); }
    if (forced) { INTRO_EL.classList.add("replay"); }
    return playIntro(INTRO_EL, firstVisit());
  })();

  // ------------------------------------------------------------------ utils
  function fmtBytes(n) {
    if (n == null) return "–";
    const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return `${n.toFixed(n >= 100 || i === 0 ? 0 : 1)} ${u[i]}`;
  }
  function fmtDur(s) {
    if (!s) return "–";
    s = Math.floor(s);
    const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
    if (d) return `${d}d ${h}h`;
    if (h) return `${h}h ${m}m`;
    if (m) return `${m}m ${s % 60}s`;
    return `${s}s`;
  }
  function ago(iso) {
    const t = typeof iso === "number" ? iso * 1000 : Date.parse(iso);
    const s = (Date.now() - t) / 1000;
    if (s < 3600) return `${Math.max(1, Math.round(s / 60))} min ago`;
    if (s < 86400) return `${Math.round(s / 3600)} h ago`;
    return `${Math.round(s / 86400)} days ago`;
  }
  async function api(path, opts) {
    const o = Object.assign({ method: "GET", headers: {} }, opts || {});
    if (o.body && typeof o.body !== "string") { o.body = JSON.stringify(o.body); o.headers["Content-Type"] = "application/json"; }
    const r = await fetch(path, o);
    let data = null;
    try { data = await r.json(); } catch (e) { /* not json */ }
    if (!r.ok || (data && data.ok === false)) throw new Error((data && data.error) || `HTTP ${r.status}`);
    return data;
  }

  function toast(msg, kind) {
    const el = document.createElement("div");
    el.className = `toast ${kind || "info"}`;
    el.innerHTML = `${icon(kind === "err" ? "alert" : kind === "ok" ? "check" : "sparkle")}<span>${esc(msg)}</span>`;
    el.querySelector("svg").setAttribute("width", "16"); el.querySelector("svg").setAttribute("height", "16");
    $("#toasts").appendChild(el);
    setTimeout(() => { el.style.opacity = "0"; el.style.transition = "opacity .3s"; }, 4200);
    setTimeout(() => el.remove(), 4600);
  }

  function modal({ title, body, actions, input, onOpen }) {
    return new Promise((resolve) => {
      const back = document.createElement("div");
      back.className = "modal-back";
      back.innerHTML = `<div class="modal" role="dialog" aria-modal="true"><h3>${esc(title)}</h3>${body || ""}
        ${input ? `<input id="modalInput" placeholder="${esc(input.placeholder || "")}" autocomplete="off">` : ""}
        <div class="btn-row"></div></div>`;
      const row = back.querySelector(".btn-row");
      const close = (v) => { back.remove(); document.removeEventListener("keydown", onKey); resolve(v); };
      (actions || [{ label: "OK", value: true, cls: "primary" }]).forEach((a) => {
        const b = document.createElement("button");
        b.className = `btn ${a.cls || ""}`; b.textContent = a.label;
        b.onclick = () => close(input && a.value === true ? back.querySelector("#modalInput").value : a.value);
        row.appendChild(b);
      });
      const onKey = (e) => {
        if (e.key === "Escape") close(null);
        else if (e.key === "Enter" && input && e.target === back.querySelector("#modalInput")) { e.preventDefault(); close(e.target.value); }
      };
      document.addEventListener("keydown", onKey);
      back.addEventListener("click", (e) => { if (e.target === back) close(null); });
      document.body.appendChild(back);
      const inp = back.querySelector("#modalInput"); if (inp) inp.focus();
      if (typeof onOpen === "function") onOpen(back);
    });
  }

  // ------------------------------------------------------------------ theme
  function applyThemeIcon() {
    const dark = document.documentElement.getAttribute("data-theme") === "dark";
    $("#themeToggle use").setAttribute("href", dark ? "#i-sun" : "#i-moon");
  }
  $("#themeToggle").addEventListener("click", () => {
    const root = document.documentElement;
    const next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
    root.classList.add("theme-anim");
    root.setAttribute("data-theme", next);
    try { localStorage.setItem("theme", next); } catch (e) { /* private mode */ }
    applyThemeIcon();
    setTimeout(() => root.classList.remove("theme-anim"), 380);
  });
  applyThemeIcon();

  // Full screen: the whole launcher.
  (() => {
    const btn = $("#fullscreenBtn");
    const root = document.documentElement;
    const can = root.requestFullscreen || root.webkitRequestFullscreen;
    if (!can || document.fullscreenEnabled === false) { btn.classList.add("hidden"); return; }
    const isFs = () => !!(document.fullscreenElement || document.webkitFullscreenElement);
    const sync = () => {
      const on = isFs();
      btn.querySelector("use").setAttribute("href", on ? "#i-fs-exit" : "#i-fs-enter");
      btn.title = on ? "Exit Full Screen" : "Full Screen";
      btn.setAttribute("aria-label", btn.title);
      btn.setAttribute("aria-pressed", String(on));
    };
    btn.addEventListener("click", async () => {
      try {
        if (isFs()) await (document.exitFullscreen || document.webkitExitFullscreen).call(document);
        else await (root.requestFullscreen || root.webkitRequestFullscreen).call(root);
      } catch (e) { toast("Full Screen Isn't Available Here", "err"); }
    });
    document.addEventListener("fullscreenchange", sync);
    document.addEventListener("webkitfullscreenchange", sync);
    sync();
  })();

  // ------------------------------------------------------------------ terminals
  const XTERM_THEME = {
    background: "#0d1117", foreground: "#e9edf2", cursor: "#2fd4c0", cursorAccent: "#0d1117",
    selectionBackground: "rgba(47,212,192,.3)",
    black: "#151a21", red: "#ef5b62", green: "#35c489", yellow: "#e0ad4a", blue: "#3a86ec",
    magenta: "#c678dd", cyan: "#2fd4c0", white: "#e9edf2",
    brightBlack: "#727d8b", brightRed: "#ff7b82", brightGreen: "#4fe0a3", brightYellow: "#f2c66d",
    brightBlue: "#69a7ff", brightMagenta: "#d99cf0", brightCyan: "#5ff0dd", brightWhite: "#ffffff",
  };
  // Copy and paste. xterm treats Ctrl+C as a terminal key, so copying a
  // selection is handled here: Ctrl/Cmd+C copies when text is selected (in the
  // shell, Ctrl+C with nothing selected still interrupts), Ctrl+Shift+C always
  // copies, Ctrl/Cmd+A selects a whole log, and Ctrl+V / Ctrl+Shift+V paste
  // into the shell through the browser's own paste.
  async function copyText(text) {
    try {
      if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(text); return true; }
    } catch (e) { /* fall back below */ }
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.cssText = "position:fixed;top:0;left:0;opacity:0;pointer-events:none";
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    ta.remove();
    return ok;
  }
  function termText(term) {
    if (term.hasSelection()) return term.getSelection();
    const b = term.buffer.active, lines = [];
    for (let i = 0; i < b.length; i++) { const l = b.getLine(i); lines.push(l ? l.translateToString(true) : ""); }
    while (lines.length && !lines[lines.length - 1]) lines.pop();
    return lines.join("\n");
  }
  async function copyTerm(term, what) {
    const sel = term.hasSelection();
    const text = termText(term);
    if (!text) { toast(`Nothing to Copy Yet`, "info"); return; }
    const n = text.split("\n").length;
    if (await copyText(text)) toast(sel ? `Copied the Selection (${plural(n, "line")})` : `Copied ${what || "the Log"} (${plural(n, "line")})`, "ok");
    else toast("Copy Failed: select the text and press Ctrl+C", "err");
  }
  function makeTerm(host, opts) {
    const term = new window.Terminal(Object.assign({
      fontFamily: "'JetBrains Mono', ui-monospace, monospace", fontSize: 12.5, lineHeight: 1.2,
      theme: XTERM_THEME, scrollback: 8000, convertEol: false, cursorBlink: false, allowProposedApi: true,
    }, opts || {}));
    const readOnly = !!(opts && opts.disableStdin);
    term.attachCustomKeyEventHandler((ev) => {
      if (ev.type !== "keydown") return true;
      const mod = ev.ctrlKey || ev.metaKey, k = (ev.key || "").toLowerCase();
      if (mod && k === "c" && (term.hasSelection() || ev.shiftKey || readOnly)) {
        ev.preventDefault();
        if (term.hasSelection()) copyText(term.getSelection());
        return false;
      }
      if (mod && k === "a" && readOnly) { ev.preventDefault(); term.selectAll(); return false; }
      if (mod && k === "v" && !readOnly) return false;     // the browser pastes; xterm sends it
      return true;
    });
    const fit = new window.FitAddon.FitAddon();
    term.loadAddon(fit);
    term.open(host);
    term._fit = () => { try { if (host.offsetParent !== null) fit.fit(); } catch (e) { /* hidden */ } };
    const ro = new ResizeObserver(() => term._fit());
    ro.observe(host);
    term._ro = ro;
    setTimeout(term._fit, 30);
    return term;
  }

  // Log viewers: several xterms can follow one log (an app's, Insight's, setup).
  const logViewers = [];
  const logSubs = {};
  function attachLog(id, host) {
    const term = makeTerm(host, { disableStdin: true });
    const v = { id, term, ready: false };
    logViewers.push(v);
    logSubs[id] = (logSubs[id] || 0) + 1;
    socket.emit("subscribe_log", { id });
    return {
      term,
      dispose() {
        const i = logViewers.indexOf(v); if (i >= 0) logViewers.splice(i, 1);
        logSubs[id]--; if (!logSubs[id]) socket.emit("unsubscribe_log", { id });
        term._ro.disconnect(); term.dispose();
      },
    };
  }


  // ------------------------------------------------------------------ socket
  const socket = io({ transports: ["websocket", "polling"] });
  // After an update the server's script stamp changes: reload so this page
  // never keeps running old code (one reload per stamp, so it can't loop).
  const MY_STAMP = ((document.querySelector('script[src*="launcher.js"]') || {}).src || "").split("v=")[1] || "";
  socket.on("hello", ({ asset }) => {
    if (!asset || !MY_STAMP || asset === MY_STAMP) return;
    let tried = "";
    try { tried = sessionStorage.getItem("neatAppsReloadedFor") || ""; } catch (e) { /* ignore */ }
    if (tried === asset) return;
    try { sessionStorage.setItem("neatAppsReloadedFor", asset); sessionStorage.setItem("neatAppsIntroPlayed", "1"); } catch (e) { /* ignore */ }
    toast("Launcher Updated: Reloading…", "info");
    setTimeout(() => location.reload(), 900);
  });

  // ------------------------------------------------------------------ labels
  const STATE_LABEL = {
    not_installed: "Not Installed", needs_models: "Ready", downloading: "Downloading",
    ready: "Ready", preparing: "Preparing", starting: "Starting", running: "Running",
    stopping: "Stopping", finished: "Finished", error: "Needs Attention", external: "Studio",
  };
  const PILL_CLASS = {
    running: "running", starting: "starting", preparing: "starting", downloading: "installing",
    stopping: "stopping", error: "error", finished: "finished",
  };
  const BUSY = new Set(["downloading", "preparing", "starting", "stopping"]);
  const UP = new Set(["running", "starting", "preparing", "stopping"]);
  const INPUT_LABEL = { folder: "Image Folder", rtsp: "Live Stream", streams: "Multi-Stream", video: "Video File", camera: "Camera", image: "Single Image", synthetic: "Synthetic", none: "–" };
  const INPUT_ICON = { folder: "image", rtsp: "video", streams: "grid", video: "video", camera: "camera", image: "image", synthetic: "gauge", none: "box" };
  const LANG_LABEL = { cpp: "C++", python: "Python" };
  const WORDS = { genai: "GenAI", mipi: "MIPI", yolo: "YOLO", rtsp: "RTSP", json: "JSON" };
  const titleCase = (s) => String(s || "").replace(/[-_]/g, " ").replace(/\b\w+/g, (w) => WORDS[w.toLowerCase()] || w[0].toUpperCase() + w.slice(1));
  const running = () => state.order.filter((id) => UP.has(state.apps[id].state));
  const short = (sha) => (sha ? String(sha).slice(0, 7) : "");
  const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;

  // The viewer and Insight URLs come back with the board's loopback address:
  // show them on whatever host this page was opened with.
  function hostUrl(url) {
    try { const u = new URL(url); u.hostname = location.hostname; return u.toString(); } catch (e) { return url; }
  }
  function insightUrl() {
    const i = (state.platform && state.platform.insight) || {};
    return `${i.scheme || "https"}://${location.hostname}:${i.port || 9900}/`;
  }

  // ------------------------------------------------------------------ socket events
  let connectedOnce = false;
  socket.on("connect", () => {
    if (connectedOnce) checkUpdates(false);
    connectedOnce = true;
    Object.keys(logSubs).forEach((id) => { if (logSubs[id]) { logViewers.filter((v) => v.id === id).forEach((v) => { v.ready = false; v.term.reset(); }); socket.emit("subscribe_log", { id }); } });
  });
  socket.on("disconnect", () => { $("#statusText").textContent = "Reconnecting…"; });
  socket.on("apps", (snaps) => {
    snaps.forEach((s) => { if (state.apps[s.id]) Object.assign(state.apps[s.id], s); });
    renderAll();
  });
  socket.on("platform", (p) => { state.platform = p; renderPlatform(); });
  socket.on("system", (sys) => { state.system = sys; renderSystem(); });
  socket.on("log_init", ({ id, data }) => {
    logViewers.filter((v) => v.id === id && !v.ready).forEach((v) => { v.term.reset(); v.term.write(data); v.ready = true; });
  });
  socket.on("log_reset", ({ id }) => { logViewers.filter((v) => v.id === id).forEach((v) => v.term.reset()); });
  socket.on("log", ({ id, data }) => { logViewers.filter((v) => v.id === id && v.ready).forEach((v) => v.term.write(data)); });
  socket.on("launcher_updated", ({ code }) => {
    if (code === 0) toast("Launcher updated. Restart it (neat-apps stop, then neat-apps) to apply.", "ok");
    else toast("Launcher update failed: run neat-apps update in a shell to see why.", "err");
    checkUpdates(true);
  });

  // ------------------------------------------------------------------ actions
  async function post(path, body) {
    try { return await api(path, { method: "POST", body: body || {} }); }
    catch (e) { toast(e.message, "err"); return null; }
  }
  const appAct = (id, action, body) => post(`/api/apps/${id}/${action}`, body);

  async function runApp(id) {
    const a = state.apps[id];
    if (!a) return;
    if (a.link && a.link.url) { window.open(a.link.url.replace("{host}", location.hostname), "_blank", "noopener"); return; }
    if (!state.platform || !state.platform.bundle.present) {
      const go = await modal({
        title: "Set Up the Board First",
        body: `<p>The apps bundle isn't installed yet. <b>Set Up Everything</b> installs it with sima-cli (with Neat Core and Insight), then gets the sample videos. It takes a few minutes.</p>`,
        actions: [{ label: "Cancel", value: false, cls: "ghost" }, { label: "Set Up Everything", value: true, cls: "primary" }],
      });
      if (go) setupAll(false);
      return;
    }
    let mode = "exclusive";
    const others = running().filter((o) => o !== id);
    if (others.length) {
      const free = state.system && state.system.cma_total ? `<p class="muted">MLA memory free: <b>${fmtBytes(state.system.cma_free)}</b> of ${fmtBytes(state.system.cma_total)}.</p>` : "";
      const pick = await modal({
        title: `Run ${a.name}?`,
        body: `<p>${others.map((o) => `<b>${esc(state.apps[o].name)}</b>`).join(", ")} ${others.length === 1 ? "is" : "are"} running on the MLA. The MLA works best with one app at a time.</p>${free}`,
        actions: [{ label: "Cancel", value: null, cls: "ghost" }, { label: "Run Alongside", value: "alongside" }, { label: `Stop ${others.length === 1 ? "It" : "Them"} and Run`, value: "exclusive", cls: "primary" }],
      });
      if (!pick) return;
      mode = pick;
    }
    const p = a.prefs || {};
    const ok = await appAct(id, "run", { mode, lang: p.lang, models: p.models, streams: p.streams, input: p.input, urls: p.urls });
    if (ok) {
      const miss = (a.models_status || []).filter((m) => a.chosen_models.includes(m.id) && !m.present);
      toast(miss.length ? `Starting ${a.name}: downloading ${plural(miss.length, "model")} first` : `Starting ${a.name}`, "info");
      if (state.route.page !== "app" || state.route.id !== id) location.hash = `#/app/${id}`;
      else if (appPage.tab !== "log" && !(a.insight)) selectTab("log");
    }
  }

  async function stopApp(id, force) {
    if (force) {
      const ok = await modal({ title: `Force Kill ${state.apps[id].name}?`, body: "<p>SIGKILL ends it at once, without letting it free its models from the MLA. Use Stop first.</p>",
        actions: [{ label: "Cancel", value: false, cls: "ghost" }, { label: "Kill", value: true, cls: "danger solid" }] });
      if (!ok) return;
    }
    await appAct(id, force ? "kill" : "stop");
  }

  async function setupAll(upgrade) {
    if (upgrade) {
      const ok = await modal({ title: "Upgrade the Apps Bundle?", body: `<p>Runs <span class="mono">sima-cli neat install apps</span> again. It keeps <span class="mono">models/</span> and backs up the old bundle to <span class="mono">.prebuilt-apps-backup/</span>. Running apps should be stopped first.</p>`,
        actions: [{ label: "Cancel", value: false, cls: "ghost" }, { label: "Upgrade", value: true, cls: "primary" }] });
      if (!ok) return;
    }
    if (await post("/api/platform/setup", { upgrade: !!upgrade })) toast(upgrade ? "Upgrading the apps bundle" : "Setting up the board", "info");
  }

  async function killPid(pid) {
    const ok = await modal({ title: `Kill PID ${pid}?`, body: "<p>Sends SIGKILL to this one process.</p>",
      actions: [{ label: "Cancel", value: false, cls: "ghost" }, { label: "Kill", value: true, cls: "danger solid" }] });
    if (ok && await post(`/api/procs/${pid}/kill`)) toast(`Killed ${pid}`, "ok");
  }

  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-act]");
    if (!b) return;
    e.preventDefault(); e.stopPropagation();
    const [act, id, arg] = b.dataset.act.split(":");
    switch (act) {
      case "run": runApp(id); break;
      case "stop": stopApp(id); break;
      case "kill": stopApp(id, true); break;
      case "open": location.hash = `#/app/${id}`; break;
      case "download": appAct(id, "download").then((r) => r && toast("Downloading models", "info")); break;
      case "cancel": appAct(id, "cancel"); break;
      case "dismiss": appAct(id, "dismiss"); break;
      case "killpid": killPid(+arg); break;
      case "expand": state.expanded.has(id) ? state.expanded.delete(id) : state.expanded.add(id); renderProcTable(); break;
      case "setup": setupAll(false); break;
      case "upgrade": setupAll(true); break;
      case "samples": post("/api/platform/samples"); break;
      case "install": installPrereq(id); break;
      case "signin": signIn(); break;
      case "firmware": updateFirmware(); break;
      case "platform-cancel": post("/api/platform/cancel"); break;
      case "platform-dismiss": post("/api/platform/dismiss"); break;
      case "insight-start": post("/api/insight/start").then((r) => r && toast("Starting Neat Insight", "info")); break;
      case "insight-stop": post("/api/insight/stop"); break;
      case "insight-restart": post("/api/insight/restart"); break;
      case "insight-dismiss": post("/api/insight/dismiss"); break;
      case "logs": location.hash = "#/manager"; setTimeout(() => selectConsole(`log:${id}`, true), 80); break;
      case "launcher-update": post("/api/launcher/update").then((r) => r && toast("Updating the launcher", "info")); break;
      case "filter": setFilter(id, arg || ""); break;
      default: break;
    }
  });

  // ------------------------------------------------------------------ home: prerequisites
  // A command typed into the Manager's shell: run (enter) or left for the user to check and run.
  function runInShell(cmd, enter) {
    location.hash = "#/manager";
    setTimeout(() => selectConsole("shell", true), 120);
    const started = Date.now();
    const go = () => {
      if (shellSock && shellSock.connected) {
        setTimeout(() => shellSock.emit("input", { s: shellSeq++, d: cmd + (enter ? "\r" : "") }), 500);
      } else if (Date.now() - started < 10000) setTimeout(go, 150);
      else toast("The shell did not open: run the command yourself", "err");
    };
    setTimeout(go, 200);
  }
  const cliCmd = () => { const c = state.platform && state.platform.sima_cli; return c && c.path ? c.path : "sima-cli"; };
  const PREREQ = {
    "sima-cli": { title: "Install sima-cli?", body: "<p>Runs SiMa.ai's installer (<span class=\"mono\">artifacts.neat.sima.ai/sima-cli/linux-mac.sh</span>), which installs sima-cli into <span class=\"mono\">/data/sima-cli</span> on the board. It may install python3-venv with sudo.</p>", go: "Install sima-cli" },
    core: { title: "Install Neat Core?", body: "<p>Installs Neat Core's runtime packages with sudo and the pyneat Python environment, with <span class=\"mono\">sima-cli neat install core</span>. It takes a few minutes. Stop running apps first: the runtime they use is replaced.</p>", go: "Install Neat Core" },
    pyneat: { title: "Link pyneat?", body: "<p>Neat Core's Python environment is at <span class=\"mono\">/media/nvme/pyneat</span>; this links <span class=\"mono\">~/pyneat</span> to it, where the Python apps look.</p>", go: "Link It" },
  };
  async function installPrereq(what) {
    const d = PREREQ[what];
    if (!d) return;
    const ok = await modal({ title: d.title, body: d.body, actions: [{ label: "Cancel", value: false, cls: "ghost" }, { label: d.go, value: true, cls: "primary solid" }] });
    if (ok && await post("/api/platform/install", { what })) toast(`${d.go}: see the Setup log`, "info");
  }
  function signIn() {
    toast("Sign in from the Shell: open the link it prints and enter the code", "info");
    runInShell(`SIMA_CLI_CHECK_FOR_UPDATE=0 ${cliCmd()} login`, true);    // straight to the sign-in, no update prompt
  }
  async function updateFirmware() {
    const fw = state.platform.firmware;
    const ok = await modal({ title: "Update the Board's Firmware?",
      body: `<p>The board runs platform <b>${esc(fw.version)}</b>; these apps need <b>${esc(fw.required)}</b> or newer.</p><p><span class="mono">sima-cli update</span> downloads the new firmware, flashes it and <b>reboots the board</b>, which stops everything running, this launcher included. Start the launcher again afterwards and run Set Up Everything.</p><p>The command is typed into the Shell; press Enter there to start it.</p>`,
      actions: [{ label: "Cancel", value: false, cls: "ghost" }, { label: "Open the Shell", value: true, cls: "primary solid" }] });
    if (ok) runInShell(`${cliCmd()} update`, false);
  }

  // ------------------------------------------------------------------ home: setup card
  function check(ok, label, detail, warn) {
    return `<li class="${ok ? "ok" : warn ? "warn" : "todo"}"><span class="ck">${icon(ok ? "check" : warn ? "alert" : "x")}</span><div><b>${label}</b><span>${detail}</span></div></li>`;
  }
  function renderPlatform() {
    const p = state.platform;
    if (!p) return;
    const ins = p.insight;
    $("#navInsightDot").classList.toggle("hidden", !ins.running);
    const job = p.job && !p.job.done ? p.job : null;
    const b = p.bundle;
    const fw = p.firmware || { ok: true };
    const core = p.core;
    const link = (act, text) => ` · <a href="#" data-act="${act}">${text}</a>`;
    const rows = [
      ...(fw.version ? [check(fw.ok, "Firmware", fw.ok ? `Platform <span class="mono">${esc(fw.version)}</span>` : `Platform <span class="mono">${esc(fw.version)}</span>; the apps need ${esc(fw.required)} or newer${link("firmware", "Update Firmware…")}`)] : []),
      check(p.sima_cli.present, "sima-cli", p.sima_cli.present ? `v${esc(p.sima_cli.version || "?")}` : `Not found${link("install:sima-cli", "Install")}`),
      check(!p.sima_cli.present || (p.signin && p.signin.present), "Developer Portal", p.signin && p.signin.present ? "Signed in: models can download" : `Sign in so the apps' models can download${p.sima_cli.present ? link("signin", "Sign In") : ""}`, true),
      check(b.present, "Apps Bundle", b.present ? `${b.apps} apps · ${esc(b.ref || b.branch)} <span class="mono">${esc(short(b.spec))}</span>${b.update ? ` · <a href="#" data-act="upgrade">Update to ${esc(b.latest)}</a>` : ""}` : `Installs into <span class="mono">${esc(b.dir)}</span>`, b.present && b.update),
      check(core.ok, "Neat Core", core.ok ? `<span class="mono">${esc(core.version)}</span>`
        : core.present ? `<span class="mono">${esc(core.version)}</span>; the bundle is built for ${esc(core.wanted)}${p.sima_cli.present && fw.ok ? link("install:core", "Install Neat Core") : ""}`
        : `Not installed${p.sima_cli.present && fw.ok ? link("install:core", "Install") : ""} (Set Up Everything installs it)`, core.present),
      check(ins.present, "Neat Insight", ins.present ? `${ins.running ? (ins.healthy ? "Running" : "Starting…") : "Installed, not running"} · port ${ins.port}` : "Comes with the apps bundle"),
      check(p.pyneat.present, "pyneat", p.pyneat.present ? "For the Python versions" : p.pyneat.linkable ? `Found in /media/nvme/pyneat${link("install:pyneat", "Link It")}` : "For the Python versions; comes with Neat Core", !p.pyneat.present),
      check(p.ffmpeg && p.ffmpeg.present, "ffmpeg", p.ffmpeg && p.ffmpeg.present ? "Insight streams the samples with it" : "Needed to stream the sample videos (installed by setup)"),
      check(p.samples.cached >= p.samples.total, "Sample Videos", `${p.samples.cached} of ${p.samples.total} for the streaming apps`),
      ...(p.samples.images_total ? [check(p.samples.images_cached >= p.samples.images_total, "Sample Images", `${p.samples.images_cached} of ${p.samples.images_total} for the image apps (or fetched on first run)`, p.samples.images_cached < p.samples.images_total)] : []),
    ].join("");
    const ready = p.ready && p.samples.cached > 0;
    let foot;
    if (job) {
      foot = `<div class="job"><span class="spin"></span><span>${esc(job.step || "Working…")}</span><span class="muted">${fmtDur(job.elapsed)}</span></div>
        <div class="btn-row"><button class="btn sm" data-act="logs:platform">${icon("terminal")}Log</button><button class="btn sm danger" data-act="platform-cancel">${icon("x")}Cancel</button></div>`;
    } else if (p.job_error) {
      foot = `<div class="err-line">${icon("alert")}<span>${esc(p.job_error)}</span></div>
        <div class="btn-row"><button class="btn sm primary" data-act="setup">${icon("refresh")}Try Again</button><button class="btn sm" data-act="logs:platform">${icon("terminal")}Log</button><button class="btn sm ghost" data-act="platform-dismiss">Dismiss</button></div>`;
    } else if (ready) {
      foot = `<div class="ready-line">${icon("check")}<span>Ready. Pick an app below and press Run.</span></div>
        <div class="btn-row">${ins.running ? `<a class="btn sm" href="#/insight">${icon("eye")}Insight</a>` : `<button class="btn sm" data-act="insight-start">${icon("play")}Start Insight</button>`}<button class="btn sm ghost" data-act="logs:platform">${icon("terminal")}Setup Log</button></div>`;
    } else {
      const can = p.sima_cli.present && fw.ok;
      foot = `<button class="btn spectrum block" data-act="setup" ${can ? "" : "disabled"}>${icon("rocket")}Set Up Everything</button>
        <div class="muted small">${!fw.ok ? "Update the board's firmware first (above), then come back." : p.sima_cli.present ? "Installs what's missing above. Models download later, per app." : "Install sima-cli first (above), then come back."}</div>`;
    }
    const key = JSON.stringify([rows, foot]);
    const card = $("#setupCard");
    if (card.dataset.k !== key) {
      card.dataset.k = key;
      card.innerHTML = `<div class="setup-head"><div class="eyebrow">Board Setup</div><h3>${ready ? "All Set" : "Get the Board Ready"}</h3></div><ul class="checklist">${rows}</ul>${foot}`;
    }
    if (state.route.page === "insight") renderInsightPage(false);
    if (state.route.page === "manager") renderUpdates();
    renderHeader();
  }

  // ------------------------------------------------------------------ home: catalog
  function inputKind(a) { return (a.input && a.input.kind) || "none"; }
  function appChips(a) {
    const k = inputKind(a);
    const chips = [];
    (a.available_languages && a.available_languages.length ? a.available_languages : a.languages).forEach((l) => chips.push(`<span class="tag lang">${LANG_LABEL[l] || l}</span>`));
    if (k !== "none") chips.push(`<span class="tag">${icon(INPUT_ICON[k])}${k === "streams" ? `${a.streams} Streams` : INPUT_LABEL[k]}</span>`);
    if (a.insight) chips.push(`<span class="tag insight">${icon("eye")}Insight</span>`);
    return chips.join("");
  }
  function statusPill(a) {
    const cls = PILL_CLASS[a.state] || (a.state === "not_installed" || a.state === "external" ? "" : "ready");
    const spin = BUSY.has(a.state) ? `<span class="spin"></span>` : "";
    const hint = a.state === "needs_models" ? ` title="Its model downloads the first time you run it"` : "";
    return `<span class="pill ${cls}"${hint}>${spin}${a.state === "needs_models" ? icon("download") : ""}${STATE_LABEL[a.state] || a.state}</span>`;
  }
  function primaryButton(a, sm) {
    const s = sm ? "sm" : "";
    if (a.state === "external") return `<button class="btn ${s} primary" data-act="run:${a.id}">${icon("external")}${esc((a.link && a.link.label) || "Open")}</button>`;
    if (UP.has(a.state)) return `<button class="btn ${s} primary" data-act="open:${a.id}">${icon(a.insight ? "eye" : "terminal")}${a.insight ? "Watch" : "View"}</button><button class="btn ${s}" data-act="stop:${a.id}" title="Stop" ${a.state === "stopping" ? "disabled" : ""}>${icon("stop")}${sm ? "" : "Stop"}</button>`;
    if (a.state === "downloading") return `<button class="btn ${s}" data-act="open:${a.id}"><span class="spin"></span>Downloading</button>`;
    return `<button class="btn ${s} primary" data-act="run:${a.id}">${icon("play")}Run</button>`;
  }
  function matches(a) {
    const f = state.filter;
    if (f.cat && a.category !== f.cat) return false;
    if (f.lang && !(a.languages || []).includes(f.lang)) return false;
    if (f.input) {
      const k = inputKind(a);
      const g = f.input === "live" ? ["rtsp", "streams", "camera"].includes(k) : f.input === "files" ? ["folder", "video", "image"].includes(k) : k === f.input;
      if (!g) return false;
    }
    if (f.status === "running" && !UP.has(a.state)) return false;
    if (f.status === "models" && (a.models_status || []).every((m) => !m.present)) return false;
    if (f.q) {
      const hay = [a.name, a.id, a.category, a.summary, a.model_label, ...(a.tags || []), ...(a.models || []).map((m) => m.id)].join(" ").toLowerCase();
      if (!f.q.toLowerCase().split(/\s+/).filter(Boolean).every((w) => hay.includes(w))) return false;
    }
    return true;
  }
  function setFilter(key, val) {
    state.filter[key] = state.filter[key] === val ? "" : val;
    renderFilters(); renderGrid(true);
  }
  function renderFilters() {
    const f = state.filter;
    const cats = {};
    state.order.forEach((id) => { const c = state.apps[id].category; cats[c] = (cats[c] || 0) + 1; });
    const chip = (key, val, label, n) => `<button class="fchip ${f[key] === val ? "on" : ""}" data-act="filter:${key}:${val}">${label}${n != null ? `<i>${n}</i>` : ""}</button>`;
    $("#filters").innerHTML = `
      <div class="frow">${chip("cat", "", "All", state.order.length)}${Object.keys(cats).sort().map((c) => chip("cat", c, titleCase(c), cats[c])).join("")}</div>
      <div class="frow">
        <span class="flabel">Input</span>${chip("input", "live", `${icon("video")}Live Streams`)}${chip("input", "files", `${icon("image")}Images &amp; Video`)}
        <span class="flabel">Language</span>${chip("lang", "cpp", "C++")}${chip("lang", "python", "Python")}
        <span class="flabel">Show</span>${chip("status", "running", "Running")}${chip("status", "models", "Models Downloaded")}
      </div>`;
  }
  // Cards are rebuilt only when the set or order changes; state changes update
  // the pill and buttons in place (so the grid does not flicker every 2 s).
  function renderGrid(force) {
    const grid = $("#appGrid");
    const ids = state.order.filter((id) => matches(state.apps[id]));
    $("#gridEmpty").classList.toggle("hidden", ids.length > 0);
    const key = ids.join("|");
    if (force || grid.dataset.k !== key) {
      grid.dataset.k = key;
      grid.innerHTML = ids.map((id) => {
        const a = state.apps[id];
        const img = a.image ? `<img loading="lazy" src="${S}${a.image}" alt="">` : `<div class="noimg">${icon(INPUT_ICON[inputKind(a)])}</div>`;
        return `<article class="card app-card" data-id="${id}">
          <a class="card-media" href="#/app/${id}" tabindex="-1">${img}<div class="card-badges"><span class="cat-badge">${esc(titleCase(a.category))}</span><span class="st"></span></div></a>
          <div class="card-body">
            <a class="card-name" href="#/app/${id}">${esc(a.name)}</a>
            <p class="card-summary">${esc(a.summary)}</p>
            <div class="tags">${appChips(a)}</div>
            <div class="card-foot"><span class="model mono" title="Model">${esc(a.model_label && a.model_label !== "None" ? a.model_label : "")}</span><div class="btn-row act"></div></div>
          </div></article>`;
      }).join("");
    }
    $$(".app-card", grid).forEach((el) => {
      const a = state.apps[el.dataset.id];
      const k = `${a.state}|${a.installed}`;
      if (el.dataset.k === k) return;
      el.dataset.k = k;
      el.classList.toggle("is-running", UP.has(a.state));
      $(".st", el).innerHTML = statusPill(a);
      $(".act", el).innerHTML = primaryButton(a, true);
    });
  }

  function renderSystem() {
    const s = state.system;
    if (!s) return;
    const ip = (s.ips && s.ips[0]) || location.hostname;
    $("#boardChips").innerHTML = [
      `<span class="chip">${icon("cpu")}<b>${esc(s.board)}</b></span>`,
      `<span class="chip">${icon("wifi")}<b>${esc(ip)}</b> · ${esc(s.hostname)}</span>`,
      `<span class="chip">${icon("zap")}CPU <b>${Math.round(s.cpu)}%</b></span>`,
      s.cma_total ? `<span class="chip">${icon("layers")}MLA Mem <b>${fmtBytes(s.cma_total - s.cma_free)}</b> / ${fmtBytes(s.cma_total)}</span>` : `<span class="chip">${icon("server")}RAM <b>${fmtBytes(s.mem_used)}</b> / ${fmtBytes(s.mem_total)}</span>`,
      s.temp != null ? `<span class="chip">${icon("temp")}<b>${s.temp}°C</b></span>` : "",
    ].join("");
    const host = `${esc(s.hostname)} · ${esc(ip)} · Up ${fmtDur(s.uptime)}`;
    $("#footerHost").innerHTML = host; $("#footerHost2").innerHTML = host;
    if (state.route.page === "manager") renderStats();
  }

  function meter(pct) {
    const cls = pct > 90 ? "crit" : pct > 75 ? "hot" : "";
    return `<div class="meter ${cls}"><i style="width:${Math.min(100, Math.max(0, pct)).toFixed(1)}%"></i></div>`;
  }
  function renderStats() {
    const s = state.system;
    if (!s) return;
    const cmaUsed = s.cma_total ? s.cma_total - s.cma_free : null;
    $("#stats").innerHTML = [
      `<div class="stat"><div class="s-label">CPU</div><div class="s-val">${Math.round(s.cpu)}%</div><div class="s-sub">${s.cores} Cores · Load ${s.load[0]}</div>${meter(s.cpu)}</div>`,
      `<div class="stat"><div class="s-label">Memory</div><div class="s-val">${fmtBytes(s.mem_used)}</div><div class="s-sub">of ${fmtBytes(s.mem_total)} Total</div>${meter(100 * s.mem_used / s.mem_total)}</div>`,
      `<div class="stat"><div class="s-label">MLA Memory (CMA)</div><div class="s-val">${cmaUsed != null ? fmtBytes(cmaUsed) : "–"}</div><div class="s-sub">${s.cma_total ? `${fmtBytes(s.cma_free)} Free` : "Not Reported"}</div>${s.cma_total ? meter(100 * cmaUsed / s.cma_total) : ""}</div>`,
      `<div class="stat"><div class="s-label">Temperature</div><div class="s-val">${s.temp != null ? `${s.temp}°C` : "–"}</div><div class="s-sub">Hottest Zone</div>${s.temp != null ? meter(s.temp) : ""}</div>`,
      `<div class="stat"><div class="s-label">Storage</div><div class="s-val">${fmtBytes(s.disk_total - s.disk_used)}</div><div class="s-sub">Free on ${esc(s.disk_path)}</div>${meter(100 * s.disk_used / s.disk_total)}</div>`,
      `<div class="stat"><div class="s-label">Board</div><div class="s-val" title="${esc(s.board)}">${esc(s.hostname)}</div><div class="s-sub">${esc((s.ips || []).join(", "))} · Up ${fmtDur(s.uptime)}</div></div>`,
    ].join("");
  }

  // ------------------------------------------------------------------ header
  function renderHeader() {
    const r = running();
    const busy = state.order.filter((id) => BUSY.has(state.apps[id].state)).length;
    const ins = state.platform && state.platform.insight;
    $("#statusText").innerHTML = `<i class="dot ${r.length ? "ok" : busy ? "warn" : ""}"></i>${r.length ? `${plural(r.length, "App")} Running` : busy ? "Working…" : "No Apps Running"}${ins && ins.running ? ` · <span class="muted">Insight On</span>` : ""}`;
    if (!$("#statusMenu").classList.contains("hidden")) renderStatusMenu();
    const n = Object.values(state.updates).filter((u) => u && (u.status === "available" || u.update)).length;
    $("#navUpdateDot").classList.toggle("hidden", !n);
  }
  function renderStatusMenu() {
    const r = running();
    const rows = r.map((id) => {
      const a = state.apps[id];
      return `<div class="sm-row"><div class="sm-main"><a href="#/app/${id}"><b>${esc(a.name)}</b></a>
        <span>${statusPill(a)} <em>${a.procs.length ? `${fmtBytes(a.rss)} · ${a.cpu}% CPU · ${fmtDur(a.uptime)}` : ""}</em></span></div>
        <div class="btn-row">${primaryButton(a, true)}</div></div>`;
    }).join("");
    const ins = state.platform && state.platform.insight;
    $("#statusMenu").innerHTML = (rows || `<div class="sm-row"><div class="sm-main"><span class="muted">Nothing is running. Pick an app and press Run.</span></div></div>`)
      + `<div class="sm-row"><div class="sm-main"><a href="#/insight"><b>Neat Insight</b></a><span class="muted">${ins ? (ins.running ? `Running · port ${ins.port}` : ins.present ? "Not running" : "Not installed") : ""}</span></div>
         <div class="btn-row">${ins && ins.running ? `<a class="btn sm" href="#/insight">${icon("eye")}Open</a>` : ins && ins.present ? `<button class="btn sm" data-act="insight-start">${icon("play")}Start</button>` : ""}</div></div>`
      + `<div class="sm-foot"><a class="btn sm ghost" href="#/manager">${icon("sliders")}Open Manager</a></div>`;
  }
  function toggleStatusMenu(open) {
    const menu = $("#statusMenu");
    const want = open == null ? menu.classList.contains("hidden") : open;
    menu.classList.toggle("hidden", !want);
    $("#statusStrip").setAttribute("aria-expanded", String(want));
    if (want) renderStatusMenu();
  }
  $("#statusStrip").addEventListener("click", (e) => { e.stopPropagation(); toggleStatusMenu(); });
  document.addEventListener("click", (e) => { if (!e.target.closest(".status-wrap")) toggleStatusMenu(false); });

  // ------------------------------------------------------------------ markdown (README sections)
  function md(src) {
    const lines = String(src || "").replace(/\r/g, "").split("\n");
    const out = [];
    const inline = (t) => esc(t)
      .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
      .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
      .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, "$1");
    let i = 0;
    while (i < lines.length) {
      const l = lines[i];
      if (/^```/.test(l)) {
        const buf = []; i++;
        while (i < lines.length && !/^```/.test(lines[i])) buf.push(lines[i++]);
        i++; out.push(`<pre><code>${esc(buf.join("\n"))}</code></pre>`); continue;
      }
      const h = l.match(/^(#{3,6})\s+(.*)/);
      if (h) { out.push(`<h4>${inline(h[2])}</h4>`); i++; continue; }
      if (/^\s*\|/.test(l)) {
        const rows = [];
        while (i < lines.length && /^\s*\|/.test(lines[i])) rows.push(lines[i++]);
        const cells = (r) => r.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
        const body = rows.filter((r) => !/^\s*\|[\s:|-]+\|\s*$/.test(r));
        out.push(`<div class="md-table"><table>${body.map((r, j) => `<tr>${cells(r).map((c) => j === 0 ? `<th>${inline(c)}</th>` : `<td>${inline(c)}</td>`).join("")}</tr>`).join("")}</table></div>`);
        continue;
      }
      if (/^\s*([-*]|\d+\.)\s+/.test(l)) {
        const ordered = /^\s*\d+\./.test(l);
        const items = [];
        while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i])) {
          let it = lines[i++].replace(/^\s*([-*]|\d+\.)\s+/, "");
          while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !/^\s*([-*]|\d+\.)\s+/.test(lines[i])) it += " " + lines[i++].trim();
          items.push(`<li>${inline(it)}</li>`);
        }
        out.push(ordered ? `<ol>${items.join("")}</ol>` : `<ul>${items.join("")}</ul>`); continue;
      }
      if (!l.trim()) { i++; continue; }
      const para = [];
      while (i < lines.length && lines[i].trim() && !/^(```|#{3,6}\s|\s*\||\s*([-*]|\d+\.)\s+)/.test(lines[i])) para.push(lines[i++]);
      if (para.length) out.push(`<p>${inline(para.join(" "))}</p>`); else i++;
    }
    return out.join("");
  }

  // ------------------------------------------------------------------ app page
  const appPage = { id: null, tab: null, log: null, built: "" };
  const TABS = [
    { id: "live", label: "Live", icon: "eye" },
    { id: "results", label: "Results", icon: "image" },
    { id: "log", label: "Log", icon: "terminal" },
    { id: "about", label: "About", icon: "book" },
    { id: "config", label: "Config", icon: "code" },
  ];
  function tabsFor(a) {
    return TABS.filter((t) => (t.id === "live" ? !!a.insight : t.id === "results" ? !a.insight || a.output.kind !== "log" : true));
  }
  function disposeAppPage() {
    if (appPage.log) { appPage.log.dispose(); appPage.log = null; }
    appPage.id = null; appPage.tab = null; appPage.built = "";
  }

  function renderAppPage(force) {
    const id = state.route.id;
    const a = state.apps[id];
    const view = $("#appView");
    if (!a) { view.innerHTML = `<div class="wrap section"><p>Unknown app. <a href="#/">Back to the apps</a></p></div>`; return; }
    if (force || appPage.id !== id) {
      disposeAppPage();
      appPage.id = id;
      const tabs = tabsFor(a);
      view.innerHTML = `
        <div class="app-bar wrap">
          <a class="btn sm ghost" href="#apps">${icon("back")}Apps</a>
          <div class="app-title"><h2>${esc(a.name)}</h2><span class="muted">${esc(titleCase(a.category))} · ${esc(a.difficulty || "")} · <span class="mono">${esc(a.rel)}</span></span></div>
          <div class="app-state" id="appState"></div>
          <div class="btn-row" id="appActions"></div>
        </div>
        <div class="wrap app-layout">
          <div class="app-main">
            <div class="tabs" id="appTabs">${tabs.map((t) => `<button data-tab="${t.id}">${icon(t.icon)}${t.label}</button>`).join("")}</div>
            <div class="tab-body" id="tabBody"></div>
          </div>
          <aside class="app-side" id="appSide"></aside>
        </div>`;
      $$("#appTabs svg").forEach((s) => { s.setAttribute("width", 14); s.setAttribute("height", 14); });
      $("#appTabs").onclick = (e) => { const b = e.target.closest("[data-tab]"); if (b) selectTab(b.dataset.tab); };
      selectTab(state.route.tab || (UP.has(a.state) ? (a.insight ? "live" : "log") : (a.insight ? "live" : "about")));
      renderSide(true);
    }
    updateAppPage();
  }

  function updateAppPage() {
    const a = state.apps[appPage.id];
    if (!a) return;
    const err = a.error ? `<span class="err-inline" title="${esc(a.error)}">${icon("alert")}${esc(a.error)}</span><button class="btn sm ghost" data-act="dismiss:${a.id}">Dismiss</button>` : "";
    const job = a.job && !a.job.done ? `<span class="muted small">${esc(a.job.step || "")}</span>` : "";
    const up = UP.has(a.state) && a.procs.length ? `<span class="muted small">${a.cpu}% CPU · ${fmtBytes(a.rss)} · ${fmtDur(a.uptime)}</span>` : "";
    const fin = a.state === "finished" ? `<span class="muted small">Exit ${a.exit_code} · ${ago(a.finished_at)}</span>` : "";
    const sk = [a.state, a.error, job, up, fin].join("|");
    const st = $("#appState");
    if (st.dataset.k !== sk) { st.dataset.k = sk; st.innerHTML = `${statusPill(a)}${job}${up}${fin}${err}`; }
    const acts = [];
    if (a.state === "external") acts.push(primaryButton(a));
    else if (UP.has(a.state)) {
      acts.push(`<button class="btn" data-act="stop:${a.id}" ${a.state === "stopping" ? "disabled" : ""}>${icon("stop")}Stop</button>`,
        `<button class="btn danger" data-act="kill:${a.id}" title="Force kill">${icon("skull")}</button>`);
    } else if (a.state === "downloading") acts.push(`<button class="btn danger" data-act="cancel:${a.id}">${icon("x")}Cancel Download</button>`);
    else acts.push(`<button class="btn spectrum" data-act="run:${a.id}" ${a.installed ? "" : ""}>${icon("play")}${a.state === "finished" ? "Run Again" : "Run"}</button>`);
    const ak = acts.join("");
    if ($("#appActions").dataset.k !== ak) { $("#appActions").dataset.k = ak; $("#appActions").innerHTML = ak; }
    // Tabs that follow the run.
    const tk = `${a.state}|${(a.info && a.info.channels || []).join(",")}|${a.outputs}`;
    if (appPage.tabKey !== tk) {
      const prev = appPage.tabKey;
      appPage.tabKey = tk;
      if (prev && appPage.tab === "live") renderLive();
      if (prev && appPage.tab === "results") renderResults();
      if (prev && prev.split("|")[0] !== a.state && UP.has(a.state) && a.state === "running" && a.insight && appPage.tab !== "live") selectTab("live");
    }
    renderSide(false);
  }

  function selectTab(tab) {
    const a = state.apps[appPage.id];
    if (!a) return;
    const tabs = tabsFor(a).map((t) => t.id);
    if (!tabs.includes(tab)) tab = tabs[0];
    appPage.tab = tab;
    $$("#appTabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    if (appPage.log && tab !== "log") { appPage.log.dispose(); appPage.log = null; }
    const body = $("#tabBody");
    body.className = `tab-body tab-${tab}`;
    if (tab === "log") {
      body.innerHTML = `<div class="console flat"><div class="console-bar"><span>${esc(a.name)} · Live Log</span><span class="btn-row"><button class="btn sm" id="logCopy" title="Copy the selection, or the whole log (Ctrl+C copies a selection)">${icon("copy")}Copy</button><a class="btn sm" href="/api/logs/${a.id}">${icon("download")}Download</a><button class="btn sm" id="logClear">${icon("trash")}Clear</button></span></div><div class="xterm-host" id="appLogHost"></div></div>`;
      appPage.log = attachLog(a.id, $("#appLogHost"));
      $("#logCopy").onclick = () => appPage.log && copyTerm(appPage.log.term);
      $("#logClear").onclick = async () => { await post(`/api/logs/${a.id}/clear`); appPage.log && appPage.log.term.reset(); };
    } else if (tab === "about") {
      const secs = (a.sections || []).filter((s) => !["metadata"].includes(s.slug));
      body.innerHTML = `<div class="about">
        ${a.image ? `<img class="about-img" src="${S}${a.image}" alt="">` : ""}
        <div class="meta-grid">
          <div><span>Model</span><b class="mono">${esc(a.model_label || "–")}</b></div>
          <div><span>Input</span><b>${INPUT_LABEL[inputKind(a)]}</b></div>
          <div><span>Languages</span><b>${(a.languages || []).map((l) => LANG_LABEL[l]).join(", ")}</b></div>
          <div><span>Status</span><b>${esc(titleCase(a.status))}</b></div>
          <div><span>Tags</span><b>${(a.tags || []).map(esc).join(", ")}</b></div>
          <div><span>Source</span><b><a href="https://github.com/sima-neat/apps/tree/main/${esc(a.rel)}" target="_blank" rel="noopener">${esc(a.rel)}</a></b></div>
        </div>
        ${secs.map((s) => `<details ${["concept", "how-it-works", "overview"].includes(s.slug) ? "open" : ""}><summary>${esc(s.title)}</summary><div class="md">${md(s.markdown)}</div></details>`).join("")}
      </div>`;
    } else if (tab === "config") {
      renderConfigTab();
    } else if (tab === "results") {
      renderResults();
    } else if (tab === "live") {
      renderLive();
    }
    $$("svg", body).forEach((s) => { if (!s.getAttribute("width")) { s.setAttribute("width", 14); s.setAttribute("height", 14); } });
    const h = `#/app/${a.id}/${tab}`;
    if (location.hash !== h) history.replaceState(null, "", h);
  }

  async function renderLive() {
    const a = state.apps[appPage.id];
    const body = $("#tabBody");
    if (!a || appPage.tab !== "live") return;
    const ins = state.platform && state.platform.insight;
    if (!UP.has(a.state)) {
      body.innerHTML = `<div class="live-empty">
        ${a.image ? `<img src="${S}${a.image}" alt="">` : ""}
        <div class="panel"><h3>${a.state === "finished" || a.state === "error" ? "The Run Ended" : "Live Output Shows Here"}</h3>
        <p>Press <b>Run</b>. The launcher ${a.streams ? `starts ${a.streams === 1 ? "a sample stream" : `${a.streams} sample streams`} in Insight, ` : ""}${ins && ins.running ? "" : "starts Neat Insight, "}runs ${esc(a.name)} on the MLA and shows its video ${a.insight ? "with detections " : ""}right here.</p>
        <div class="btn-row"><button class="btn spectrum" data-act="run:${a.id}">${icon("play")}Run</button></div></div></div>`;
      return;
    }
    if (a.state !== "running" || !(a.info && a.info.channels && a.info.channels.length)) {
      body.innerHTML = `<div class="live-empty"><div class="panel"><h3><span class="spin"></span> Starting</h3><p>${esc((a.job && a.job.step) || "Preparing the run…")}</p><div class="btn-row"><button class="btn sm" data-tab-go="log">${icon("terminal")}Watch the Log</button></div></div></div>`;
      body.querySelector("[data-tab-go]").onclick = () => selectTab("log");
      return;
    }
    const key = `${a.id}|${a.info.channels.join(",")}|${a.launched_at}`;
    if (body.dataset.live === key && $("#liveFrame", body)) return;
    body.dataset.live = key;
    body.innerHTML = `<div class="live-wrap"><div class="live-bar"><span>${icon("eye")}Insight Viewer · Channel${a.info.channels.length > 1 ? "s" : ""} ${a.info.channels.join(", ")}${a.info.input === "custom" ? " · Your Streams" : a.info.slots && a.info.slots.length ? ` · Streams src${a.info.slots.join(", src")}` : ""}</span>
      <span class="btn-row"><a class="btn sm" id="livePop" target="_blank" rel="noopener">${icon("external")}New Tab</a><a class="btn sm" href="${insightUrl()}" target="_blank" rel="noopener">${icon("eye")}Full Insight</a></span></div>
      <div class="frame-box"><iframe id="liveFrame" title="Insight viewer" allow="autoplay; fullscreen"></iframe></div></div>`;
    $$("svg", body).forEach((s) => { s.setAttribute("width", 14); s.setAttribute("height", 14); });
    try {
      const v = await api(`/api/apps/${a.id}/viewer`);
      if (!v.url) throw new Error(v.error || "no viewer URL");
      const url = hostUrl(v.url);
      $("#livePop").href = url;
      loadFrame($("#liveFrame"), url, "Insight's Viewer", () => appPage.id === a.id && appPage.tab === "live");
    } catch (e) {
      $(".frame-box", body).innerHTML = `<div class="panel"><p>${esc(e.message)}</p></div>`;
    }
  }

  async function renderResults() {
    const a = state.apps[appPage.id];
    const body = $("#tabBody");
    if (!a || appPage.tab !== "results") return;
    let r;
    try { r = await api(`/api/apps/${a.id}/outputs`); } catch (e) { body.innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
    if (appPage.tab !== "results") return;
    const parts = [];
    if (r.report) {
      parts.push(`<div class="report"><h4>${icon("gauge")}Report</h4>${reportHtml(r.report)}<details><summary>Raw JSON</summary><pre><code>${esc(JSON.stringify(r.report, null, 2))}</code></pre></details></div>`);
    }
    if (r.images.length) {
      parts.push(`<div class="results-head"><b>${plural(r.images.length, "Image")}</b><span class="muted">runs/${esc(a.id)}/output</span></div>
        <div class="results-grid">${r.images.map((p, i) => `<a href="/api/apps/${a.id}/output/${encodeURI(p)}" data-i="${i}" class="res"><img loading="lazy" src="/api/apps/${a.id}/output/${encodeURI(p)}?t=${a.finished_at || 0}" alt="${esc(p)}"><span>${esc(p)}</span></a>`).join("")}</div>`);
    }
    if (!parts.length) {
      parts.push(`<div class="live-empty"><div class="panel"><h3>${UP.has(a.state) ? "Working…" : "No Results Yet"}</h3><p>${a.output.kind === "images" ? "Annotated images land here when the app runs." : a.output.kind === "report" ? "The benchmark report shows here when the run finishes." : "This app writes its results to the log."}</p>
        ${UP.has(a.state) ? "" : `<div class="btn-row"><button class="btn spectrum" data-act="run:${a.id}">${icon("play")}Run</button></div>`}</div></div>`);
    }
    body.innerHTML = parts.join("");
    $$("svg", body).forEach((s) => { s.setAttribute("width", 16); s.setAttribute("height", 16); });
    $$(".res", body).forEach((el) => el.addEventListener("click", (e) => { e.preventDefault(); lightbox(r.images.map((p) => `/api/apps/${a.id}/output/${encodeURI(p)}`), +el.dataset.i); }));
  }

  function reportHtml(rep) {
    const flat = [];
    const walk = (o, p) => {
      if (flat.length > 24) return;
      if (o && typeof o === "object" && !Array.isArray(o)) Object.entries(o).forEach(([k, v]) => walk(v, p ? `${p}.${k}` : k));
      else if (typeof o === "number" || typeof o === "string" || typeof o === "boolean") flat.push([p, o]);
    };
    walk(rep, "");
    return `<div class="meta-grid">${flat.map(([k, v]) => `<div><span>${esc(titleCase(k.split(".").slice(-2).join(" ")))}</span><b class="mono">${esc(typeof v === "number" ? (Number.isInteger(v) ? v : v.toFixed(3)) : v)}</b></div>`).join("")}</div>`;
  }

  function lightbox(urls, i) {
    const back = document.createElement("div");
    back.className = "lightbox";
    const show = () => { back.innerHTML = `<img src="${urls[i]}" alt=""><div class="lb-bar"><span>${i + 1} / ${urls.length}</span><button class="btn sm" data-lb="-1">${icon("back")}</button><button class="btn sm" data-lb="1">${icon("chevron")}</button><button class="btn sm" data-lb="x">${icon("x")}</button></div>`; };
    const close = () => { back.remove(); document.removeEventListener("keydown", key); };
    const key = (e) => { if (e.key === "Escape") close(); if (e.key === "ArrowRight") { i = (i + 1) % urls.length; show(); } if (e.key === "ArrowLeft") { i = (i - 1 + urls.length) % urls.length; show(); } };
    back.addEventListener("click", (e) => {
      const b = e.target.closest("[data-lb]");
      if (!b) { if (e.target === back) close(); return; }
      if (b.dataset.lb === "x") close(); else { i = (i + +b.dataset.lb + urls.length) % urls.length; show(); }
    });
    document.addEventListener("keydown", key);
    show(); document.body.appendChild(back);
  }

  async function renderConfigTab() {
    const a = state.apps[appPage.id];
    const body = $("#tabBody");
    body.innerHTML = `<p class="muted">Loading…</p>`;
    let c;
    try { c = await api(`/api/apps/${a.id}/config`); } catch (e) { body.innerHTML = `<div class="panel"><p>${esc(e.message)}</p></div>`; return; }
    if (appPage.tab !== "config") return;
    body.innerHTML = `<div class="config-tab">
      <p class="muted small">${c.custom ? `<b>Your edited config is used for every run.</b> Reset to go back to the generated one.` : `This is the config the launcher writes for the next run, from the app's <span class="mono">src/common/config.yaml</span> with the model, streams and Insight filled in. Edit it and save to use your own.`}</p>
      <textarea id="cfgText" spellcheck="false" class="mono">${esc(c.text)}</textarea>
      <div class="btn-row"><button class="btn primary" id="cfgSave">${icon("check")}Save My Config</button>${c.custom ? `<button class="btn" id="cfgReset">${icon("refresh")}Reset to Generated</button>` : ""}
      <details class="cfg-default"><summary>Original config.yaml</summary><pre><code>${esc(c.default)}</code></pre></details></div></div>`;
    $$("svg", body).forEach((s) => { s.setAttribute("width", 14); s.setAttribute("height", 14); });
    $("#cfgSave").onclick = async () => { if (await post(`/api/apps/${a.id}/config`, { text: $("#cfgText").value })) { toast("Saved: the next run uses your config", "ok"); renderConfigTab(); } };
    if ($("#cfgReset")) $("#cfgReset").onclick = async () => { if (await post(`/api/apps/${a.id}/config`, { reset: true })) { toast("Back to the generated config", "ok"); renderConfigTab(); } };
  }

  // Right column: how the next run is set up.
  function renderSide(force) {
    const a = state.apps[appPage.id];
    const side = $("#appSide");
    if (!a || !side) return;
    const p = a.prefs || {};
    const locked = UP.has(a.state) || a.state === "downloading";
    const ms = a.models_status || [];
    const key = JSON.stringify([a.state, a.available_languages, ms, a.chosen_models, p, a.custom_config, locked]);
    if (!force && side.dataset.k === key) return;
    side.dataset.k = key;
    const parts = [];
    if (a.state === "external") {
      side.innerHTML = `<div class="side-card"><h4>${icon("external")}Runs Elsewhere</h4><p class="muted small">${esc(a.note)}</p>${primaryButton(a)}</div>`;
      return;
    }
    if (a.needs) parts.push(`<div class="side-note warn">${icon("alert")}<span>${esc(a.needs)}</span></div>`);
    if (a.note) parts.push(`<div class="side-note">${icon("sparkle")}<span>${esc(a.note)}</span></div>`);
    if (!a.installed) parts.push(`<div class="side-note warn">${icon("alert")}<span>Not in the installed apps bundle. <a href="#" data-act="setup">Set Up Everything</a>${state.platform && state.platform.bundle.present ? " (or update the bundle in the Manager)" : ""}.</span></div>`);
    // language
    const langs = a.available_languages.length ? a.available_languages : a.languages;
    const lang = langs.includes(p.lang) ? p.lang : langs[0];
    if (langs.length) {
      parts.push(`<div class="side-card"><h4>${icon("code")}Version</h4><div class="seg">${langs.map((l) => `<button data-pref="lang" data-val="${l}" class="${l === lang ? "on" : ""}" ${locked ? "disabled" : ""}>${LANG_LABEL[l]}</button>`).join("")}</div>
        <p class="muted small">${lang === "python" ? "Runs src/python/main.py in ~/pyneat." : "Runs the prebuilt C++ binary."}</p></div>`);
    }
    // models
    if (ms.length) {
      const multi = !a.model_placeholder;
      const rows = ms.map((m) => {
        const on = a.chosen_models.includes(m.id);
        return `<label class="model-row ${on ? "on" : ""}"><input type="${multi ? "checkbox" : "radio"}" name="model" value="${esc(m.id)}" ${on ? "checked" : ""} ${locked || !m.downloadable ? "disabled" : ""}>
          <span class="m-name mono" title="${esc(m.file)}">${esc(m.id)}</span>
          <span class="m-state">${m.present ? `<span class="ok-txt">${icon("check")}${fmtBytes(m.size)}</span>` : m.downloadable ? `<span class="muted">Not Downloaded</span>` : `<span class="muted">${esc(m.source)}</span>`}</span></label>`;
      }).join("");
      const missing = ms.filter((m) => a.chosen_models.includes(m.id) && !m.present);
      parts.push(`<div class="side-card"><h4>${icon("box")}Model${ms.length > 1 ? "s" : ""}</h4><div class="models ${ms.length > 6 ? "scroll" : ""}">${rows}</div>
        ${missing.length ? `<div class="btn-row"><button class="btn sm" data-act="download:${a.id}" ${locked ? "disabled" : ""}>${icon("download")}Download Now</button><span class="muted small">or just Run: it downloads first</span></div>` : ""}</div>`);
    }
    // input
    const k = inputKind(a);
    if (a.streams) {
      const custom = p.input === "custom";
      const n = +(p.streams || a.streams);
      const counts = k === "streams" ? [1, 2, 4, 8, 16].filter((c) => c <= Math.max(16, a.streams)) : [];
      parts.push(`<div class="side-card"><h4>${icon("video")}Input</h4>
        <div class="seg"><button data-pref="input" data-val="samples" class="${custom ? "" : "on"}" ${locked ? "disabled" : ""}>${a.media && a.media.length ? "Bundled Clip" : "Sample Videos"}</button><button data-pref="input" data-val="custom" class="${custom ? "on" : ""}" ${locked ? "disabled" : ""}>My RTSP</button></div>
        ${counts.length ? `<label class="field">Streams <select data-pref="streams" ${locked ? "disabled" : ""}>${counts.map((c) => `<option value="${c}" ${c === n ? "selected" : ""}>${c}</option>`).join("")}</select></label>` : ""}
        ${custom ? `<textarea class="mono urls" data-pref="urls" placeholder="rtsp://camera-ip:554/stream${n > 1 ? "\nrtsp://…  (one per line)" : ""}" ${locked ? "disabled" : ""}>${esc((p.urls || []).join("\n"))}</textarea>`
          : `<p class="muted small">${a.media && a.media.length ? `Insight streams <span class="mono">${esc(a.media[0].split("/").pop())}</span>` : `Insight streams ${n === 1 ? "a sample video" : `${n} sample videos`}`} over RTSP; the app reads ${n === 1 ? "it" : "them"} like ${n === 1 ? "a camera" : "cameras"}.</p>`}</div>`);
    } else if (k !== "none" && a.sample_set) {
      const ss = a.sample_set;
      parts.push(`<div class="side-card"><h4>${icon(INPUT_ICON[k])}Input</h4><p class="muted small">${esc(ss.label.charAt(0).toUpperCase() + ss.label.slice(1))}${ss.ready ? "" : ", downloaded on the first run"}. Set <span class="mono">${k === "image" ? "io.input" : "io.input_dir"}</span> in Config to use your own.</p><p class="muted small">From ${esc(ss.source)}.</p></div>`);
    } else if (k !== "none") {
      const what = { folder: `The ${esc((a.input.path || "").split("/").pop())} sample images in the bundle.`, video: `The bundled clip ${esc((a.input.path || "").split("/").pop())}.`, image: "A sample photo (set io.image in Config for your own).", camera: "The MIPI camera on the board.", synthetic: "Synthetic frames: it measures the model, not a pipeline." }[k] || "";
      parts.push(`<div class="side-card"><h4>${icon(INPUT_ICON[k])}Input</h4><p class="muted small">${what}</p></div>`);
    }
    if (a.insight) parts.push(`<div class="side-card"><h4>${icon("eye")}Output</h4><p class="muted small">Video${a.output.kind === "log" ? "" : ""} and detections go to Neat Insight${a.info && a.info.channels && a.info.channels.length ? ` (channel${a.info.channels.length > 1 ? "s" : ""} ${a.info.channels.join(", ")})` : ""}. Watch them on the Live tab.</p></div>`);
    else if (a.output.kind !== "log") parts.push(`<div class="side-card"><h4>${icon(a.output.kind === "report" ? "gauge" : "image")}Output</h4><p class="muted small">${a.output.kind === "report" ? "A JSON benchmark report" : "Annotated images"} in <span class="mono">runs/${esc(a.id)}/output</span>, shown on the Results tab.</p></div>`);
    if (a.custom_config) parts.push(`<div class="side-note">${icon("code")}<span>Runs with your edited config. <a href="#" id="toConfig">Open Config</a></span></div>`);
    side.innerHTML = parts.join("");
    $$("svg", side).forEach((s) => { s.setAttribute("width", 15); s.setAttribute("height", 15); });
    const save = (upd) => post(`/api/apps/${a.id}/prefs`, upd);
    $$("[data-pref]", side).forEach((el) => {
      const pref = el.dataset.pref;
      if (el.tagName === "BUTTON") el.onclick = () => save({ [pref]: el.dataset.val });
      else if (el.tagName === "SELECT") el.onchange = () => save({ [pref]: +el.value });
      else if (el.tagName === "TEXTAREA") el.onchange = () => save({ urls: el.value.split(/\s+/).filter(Boolean) });
    });
    $$("input[name=model]", side).forEach((el) => el.onchange = () => {
      const ids = $$("input[name=model]:checked", side).map((x) => x.value);
      save({ models: ids.length ? ids : null });
    });
    const tc = $("#toConfig"); if (tc) tc.onclick = (e) => { e.preventDefault(); selectTab("config"); };
  }

  // A page on the board with its own self-signed certificate (Insight, its
  // viewer): browsers never show the certificate warning inside a frame, so
  // the warning opens in a small window; the launcher watches for the
  // certificate to be accepted, closes the window and loads the frame.
  async function reachable(url) {
    try { await fetch(url, { mode: "no-cors", cache: "no-store" }); return true; } catch (e) { return false; }
  }
  async function loadFrame(frame, url, label, stillHere) {
    const ok = await reachable(url);
    if (!stillHere()) return;
    if (ok) { frame.src = url; return; }
    const box = frame.parentElement;
    const ov = document.createElement("div");
    ov.className = "overlay-panel";
    ov.innerHTML = `<div class="panel trust-panel">
      <div style="display:flex;gap:12px;align-items:center"><div class="trust-icon">${icon("shield")}</div><h3>One More Step: Trust ${esc(label)}</h3></div>
      <p>${esc(label)} uses its own self-signed certificate. Your browser asks about it once, and only in a window of its own.</p>
      <ol class="trust-steps">
        <li><b>Click Trust.</b> A small window opens with the browser's warning.</li>
        <li><b>Choose Advanced → Proceed</b> (Chrome: "Proceed to …"; Firefox: "Accept the Risk and Continue").</li>
        <li><b>That's it.</b> The window closes by itself and the page opens here.</li>
      </ol>
      <div class="trust-status muted" aria-live="polite"></div>
      <div class="btn-row"><button class="btn primary trust-open">${icon("shield")}Trust ${esc(label)}</button><a class="btn ghost" href="${esc(url)}" target="_blank" rel="noopener">${icon("external")}Use a New Tab Instead</a></div>
    </div>`;
    $$("svg", ov).forEach((g) => { g.setAttribute("width", 18); g.setAttribute("height", 18); });
    box.appendChild(ov);
    const status = ov.querySelector(".trust-status");
    let timer = null, win = null;
    const done = () => { if (timer) clearInterval(timer); timer = null; };
    ov.querySelector(".trust-open").onclick = () => {
      const w = 860, h = 660;
      win = window.open(url, "neat-trust", `popup=yes,width=${w},height=${h},left=${Math.max(0, (screen.availWidth - w) / 2)},top=${Math.max(0, (screen.availHeight - h) / 2)}`);
      if (!win) win = window.open(url, "_blank");
      if (!win) { status.textContent = "Your browser blocked the window. Allow pop-ups for the launcher, or use a new tab."; return; }
      status.textContent = "Waiting for you to choose Advanced → Proceed in the small window…";
      done();
      const started = Date.now();
      timer = setInterval(async () => {
        if (!stillHere() || !ov.isConnected) { done(); return; }
        if (await reachable(url)) {
          done();
          try { if (win && !win.closed) win.close(); } catch (e) { /* gone */ }
          status.innerHTML = `${icon("check")} Trusted. Opening…`;
          setTimeout(() => { if (stillHere()) { ov.remove(); frame.src = url; } }, 500);
          return;
        }
        if (win.closed) { done(); status.textContent = "The window closed before the certificate was accepted. Click Trust to try again."; return; }
        if (Date.now() - started > 10 * 60 * 1000) { done(); status.textContent = "Still waiting? Click Trust to open the window again."; }
      }, 1000);
    };
  }

  // ------------------------------------------------------------------ Insight page
  const insightPage = { built: "" };
  async function renderInsightPage(force) {
    const p = state.platform;
    const view = $("#insightView");
    if (!p) { view.innerHTML = `<div class="wrap section"><p class="muted">Loading…</p></div>`; return; }
    const ins = p.insight;
    const st = !ins.present ? "absent" : ins.running ? (ins.healthy ? "up" : "starting") : "down";
    if (!force && insightPage.built === st && view.innerHTML) {
      return;
    }
    insightPage.built = st;
    const bar = `<div class="app-bar wrap"><div class="app-title"><h2>Neat Insight</h2><span class="muted">The board's live viewer for the apps: video, detections and streaming sources · port ${ins.port}${ins.version ? ` · ${esc(ins.version)}` : ""}</span></div>
      <div class="app-state">${st === "up" ? `<span class="pill running">Running</span>` : st === "starting" ? `<span class="pill starting"><span class="spin"></span>Starting</span>` : st === "down" ? `<span class="pill">Stopped</span>` : `<span class="pill">Not Installed</span>`}${ins.error ? `<span class="err-inline">${icon("alert")}${esc(ins.error)}</span>` : ""}</div>
      <div class="btn-row">${st === "up" || st === "starting" ? `<a class="btn" href="${insightUrl()}" target="_blank" rel="noopener">${icon("external")}New Tab</a><button class="btn" data-act="insight-restart">${icon("refresh")}Restart</button><button class="btn" data-act="insight-stop">${icon("stop")}Stop</button><button class="btn sm ghost" data-act="logs:insight">${icon("terminal")}Log</button>`
        : st === "down" ? `<button class="btn spectrum" data-act="insight-start">${icon("play")}Start Insight</button><button class="btn sm ghost" data-act="logs:insight">${icon("terminal")}Log</button>` : `<button class="btn spectrum" data-act="setup">${icon("rocket")}Set Up Everything</button>`}</div></div>`;
    if (st === "up") {
      view.innerHTML = `${bar}<div class="wrap insight-frame"><div class="frame-box tall"><iframe id="insightFrame" title="Neat Insight" allow="autoplay; fullscreen"></iframe></div></div>`;
      loadFrame($("#insightFrame"), insightUrl(), "Neat Insight", () => state.route.page === "insight");
    } else {
      view.innerHTML = `${bar}<div class="wrap section"><div class="panel"><h3>${st === "absent" ? "Insight Isn't Installed Yet" : st === "starting" ? "Insight Is Starting…" : "Insight Is Stopped"}</h3>
        <p>${st === "absent" ? "It comes with the apps bundle: Set Up Everything installs both." : "Apps that stream video start it automatically when you press Run. Start it now to browse its media library, streaming sources and viewer."}</p></div></div>`;
    }
    $$("svg", view).forEach((s) => { if (!s.getAttribute("width")) { s.setAttribute("width", 15); s.setAttribute("height", 15); } });
  }

  // ------------------------------------------------------------------ manager
  function renderProcTable() {
    const rows = [];
    const ids = state.order.filter((id) => { const a = state.apps[id]; return UP.has(a.state) || a.procs.length || ["finished", "error", "downloading"].includes(a.state) || a.exit_code != null; });
    ids.forEach((id) => {
      const a = state.apps[id];
      const up = UP.has(a.state);
      const first = a.procs && a.procs[0];
      const open = state.expanded.has(id);
      const acts = [];
      if (up) acts.push(`<button class="btn sm" data-act="stop:${id}" ${a.state === "stopping" ? "disabled" : ""}>${icon("stop")}Stop</button>`, `<button class="btn sm danger" data-act="kill:${id}" title="Force kill (SIGKILL)">${icon("skull")}Kill</button>`);
      else if (a.state === "downloading") acts.push(`<button class="btn sm danger" data-act="cancel:${id}">${icon("x")}Cancel</button>`);
      else acts.push(`<button class="btn sm primary" data-act="run:${id}">${icon("play")}Run</button>`);
      acts.push(`<button class="btn sm" data-act="logs:${id}" title="Live Log">${icon("terminal")}</button>`);
      rows.push(`<tr class="studio-row">
        <td class="c-exp">${a.procs.length ? `<button class="expander ${open ? "open" : ""}" data-act="expand:${id}" title="Processes">${icon("chevron")}</button>` : ""}</td>
        <td class="c-studio"><a href="#/app/${id}"><b>${esc(a.name)}</b></a><div class="muted mono path">${esc(a.rel)}${a.info && a.info.lang ? ` · ${LANG_LABEL[a.info.lang]}` : ""}${a.owned || !a.procs.length ? "" : " · Started Outside the Launcher"}</div></td>
        <td data-label="State">${statusPill(a)}</td>
        <td class="num mono" data-label="PID">${first ? first.pid : "–"}${a.procs.length > 1 ? ` <span class="muted">+${a.procs.length - 1}</span>` : ""}</td>
        <td class="num" data-label="CPU">${up ? `${a.cpu}%` : "–"}</td>
        <td class="num" data-label="Memory">${up ? fmtBytes(a.rss) : "–"}</td>
        <td class="num" data-label="Uptime">${up ? fmtDur(a.uptime) : "–"}</td>
        <td class="c-actions"><div class="btn-row row-actions">${acts.join("")}</div></td></tr>`);
      if (open) a.procs.forEach((pr) => rows.push(`<tr class="proc-row"><td></td><td colspan="2"><div class="cmd mono" title="${esc(pr.cmd)}">${esc(pr.cmd)}</div></td>
        <td class="num mono">${pr.pid}</td><td class="num">${pr.cpu}%</td><td class="num">${fmtBytes(pr.rss)}</td><td class="num">${fmtDur(Date.now() / 1000 - pr.started)}</td>
        <td><div class="btn-row" style="justify-content:flex-end"><button class="btn sm danger" data-act="killpid:${id}:${pr.pid}">${icon("skull")}Kill PID</button></div></td></tr>`));
    });
    const ins = state.platform && state.platform.insight;
    if (ins) {
      const p0 = ins.procs && ins.procs[0];
      rows.push(`<tr class="studio-row"><td></td><td class="c-studio"><a href="#/insight"><b>Neat Insight</b></a><div class="muted mono path">port ${ins.port}</div></td>
        <td>${ins.running ? `<span class="pill running">Running</span>` : `<span class="pill">${ins.present ? "Stopped" : "Not Installed"}</span>`}</td>
        <td class="num mono">${p0 ? p0.pid : "–"}${ins.procs && ins.procs.length > 1 ? ` <span class="muted">+${ins.procs.length - 1}</span>` : ""}</td>
        <td class="num">${p0 ? `${ins.procs.reduce((s, x) => s + x.cpu, 0).toFixed(1)}%` : "–"}</td><td class="num">${p0 ? fmtBytes(ins.procs.reduce((s, x) => s + x.rss, 0)) : "–"}</td>
        <td class="num">${p0 ? fmtDur(Date.now() / 1000 - p0.started) : "–"}</td>
        <td class="c-actions"><div class="btn-row row-actions">${ins.running ? `<button class="btn sm" data-act="insight-stop">${icon("stop")}Stop</button>` : ins.present ? `<button class="btn sm primary" data-act="insight-start">${icon("play")}Start</button>` : ""}<button class="btn sm" data-act="logs:insight" title="Live Log">${icon("terminal")}</button></div></td></tr>`);
    }
    if (!ids.length) rows.unshift(`<tr><td></td><td colspan="7" class="muted">No app has run yet.</td></tr>`);
    $("#procTable").innerHTML = rows.join("");
  }

  let lastUpdateCheck = 0;
  async function checkUpdates(force) {
    lastUpdateCheck = Date.now();
    try {
      const r = await api(`/api/updates${force ? "?force=1" : ""}`);
      state.updates = r;
      renderUpdates(); renderHeader();
      if (force) toast("Checked for updates", "ok");
    } catch (e) { if (force) toast(e.message, "err"); }
  }
  function renderUpdates() {
    const list = $("#updatesList");
    const u = state.updates.launcher;
    const p = state.platform;
    const rows = [];
    if (p) {
      const b = p.bundle;
      rows.push(`<div class="update-row"><img src="${S}icons/neat-mark.png" width="26" height="26" alt=""><div class="u-main"><div class="u-name">Apps Bundle <span class="muted">(sima-neat/apps, ${esc(b.branch)})</span></div>
        <div class="u-sub">${b.present ? `Installed <span class="mono">${esc(short(b.spec))}</span> · ${b.apps} apps · Core ${esc(p.core.version || b.core_ref || "?")} · <span class="mono">${esc(b.dir)}</span>` : "Not installed"}${b.latest ? ` · Latest <span class="mono">${esc(b.latest)}</span>` : b.latest_error ? ` · <span title="${esc(b.latest_error)}">Can't Reach artifacts.neat.sima.ai</span>` : ""}</div></div>
        ${b.present ? (b.update ? `<span class="pill update">Update Available</span><button class="btn sm primary" data-act="upgrade">${icon("download")}Update</button>` : b.latest ? `<span class="pill running">Up to Date</span>` : "") : `<button class="btn sm primary" data-act="setup">${icon("rocket")}Install</button>`}</div>`);
      rows.push(`<div class="update-row"><img src="${S}icons/neat-mark.png" width="26" height="26" alt=""><div class="u-main"><div class="u-name">Sample Videos and Images</div><div class="u-sub">${p.samples.cached} of ${p.samples.total} videos for the streaming apps${p.samples.images_total ? `, ${p.samples.images_cached} of ${p.samples.images_total} images for the image apps` : ""}</div></div>
        ${p.samples.cached < p.samples.total || p.samples.images_cached < p.samples.images_total ? `<button class="btn sm" data-act="samples">${icon("download")}Get Them</button>` : `<span class="pill running">Ready</span>`}</div>`);
      rows.push(`<div class="update-row"><img src="${S}icons/neat-mark.png" width="26" height="26" alt=""><div class="u-main"><div class="u-name">sima-cli</div><div class="u-sub">${p.sima_cli.present ? `v${esc(p.sima_cli.version)} · <span class="mono">${esc(p.sima_cli.path)}</span> · Model downloads need a SiMa Developer Portal login: run <span class="mono">sima-cli login</span> in the Shell once.` : "Not found"}</div></div></div>`);
    }
    if (u) {
      const L = u.latest || {};
      const txt = u.status === "up_to_date" ? `<span class="pill running">Up to Date</span>` : u.status === "available" ? `<span class="pill update">${u.behind_by != null ? `${plural(u.behind_by, "Commit")} Behind` : "Update Available"}</span><button class="btn sm primary" data-act="launcher-update">${icon("download")}Update</button>` : u.status === "error" ? `<span class="pill error" title="${esc(u.error || "")}">Can't Check</span>` : `<span class="pill">${esc(titleCase(u.status))}</span>`;
      rows.push(`<div class="update-row"><img src="${S}icons/neat-logo.png" width="26" height="26" alt=""><div class="u-main"><div class="u-name">Neat Apps Launcher</div><div class="u-sub">v${esc(window.LAUNCHER.version)}${L.sha ? ` · Latest <span class="mono">${short(L.sha)}</span> ${esc(L.message || "")}` : ""}</div></div>${txt}</div>`);
    }
    list.innerHTML = rows.join("") || `<div class="muted" style="font-size:13px">Not Checked Yet</div>`;
  }

  // Console: Shell, Setup and Insight logs, plus any app's log (picked from a menu).
  const consoles = {};
  let currentConsole = null;
  let shellSock = null;
  let shellSeq = 0;
  function buildConsoleTabs() {
    const tabs = $("#consoleTabs");
    tabs.innerHTML = `<button data-console="shell">${icon("terminal")}Shell</button><button data-console="log:platform">${icon("rocket")}Setup Log</button><button data-console="log:insight">${icon("eye")}Insight Log</button>
      <select id="appLogPick" aria-label="App log"><option value="">App Log…</option>${state.order.map((id) => `<option value="${id}">${esc(state.apps[id].name)}</option>`).join("")}</select>`;
    $$("svg", tabs).forEach((s) => { s.setAttribute("width", 14); s.setAttribute("height", 14); });
    tabs.onclick = (e) => { const b = e.target.closest("[data-console]"); if (b) selectConsole(b.dataset.console); };
    $("#appLogPick").onchange = (e) => { if (e.target.value) selectConsole(`log:${e.target.value}`); };
  }
  function openShell(c) {
    if (shellSock) shellSock.disconnect();
    shellSock = io("/term", { transports: ["websocket", "polling"], forceNew: true });
    shellSeq = 0;
    c.term.reset();
    shellSock.on("connect", () => shellSock.emit("open", { cols: c.term.cols, rows: c.term.rows, cwd: "bundle" }));
    shellSock.on("output", (d) => c.term.write(d));
    shellSock.on("exit", () => c.term.write("\r\n\x1b[38;2;114;125;139m[shell exited: press Restart for a new one]\x1b[0m\r\n"));
  }
  function consoleName(id) { return id === "platform" ? "Setup" : id === "insight" ? "Neat Insight" : (state.apps[id] || {}).name || id; }
  function selectConsole(key, scroll) {
    if (!$("#consoleTabs").children.length) buildConsoleTabs();
    currentConsole = key;
    $$("#consoleTabs button").forEach((b) => b.classList.toggle("active", b.dataset.console === key));
    const pick = $("#appLogPick");
    const appId = key.startsWith("log:") && state.apps[key.slice(4)] ? key.slice(4) : "";
    if (pick) { pick.value = appId; pick.classList.toggle("active", !!appId); }
    Object.values(consoles).forEach((c) => { c.host.style.display = "none"; });
    let c = consoles[key];
    if (!c) {
      const host = document.createElement("div");
      host.style.height = "100%";
      $("#consoleHost").appendChild(host);
      if (key === "shell") {
        const term = makeTerm(host, { cursorBlink: true });
        c = { host, term };
        let pending = "", timer = null;
        term.onData((d) => {
          pending += d;
          if (!timer) timer = setTimeout(() => { if (shellSock) shellSock.emit("input", { s: shellSeq++, d: pending }); pending = ""; timer = null; }, 8);
        });
        term.onResize(({ cols, rows }) => shellSock && shellSock.emit("resize", { cols, rows }));
        consoles[key] = c;
        setTimeout(() => { term._fit(); openShell(c); }, 60);
      } else {
        const lv = attachLog(key.slice(4), host);
        c = { host, term: lv.term, lv };
        consoles[key] = c;
      }
    }
    c.host.style.display = "block";
    setTimeout(() => { c.term._fit(); if (key === "shell") c.term.focus(); }, 30);
    const actions = $("#consoleActions");
    if (key === "shell") {
      $("#consoleLabel").textContent = `Shell · ${state.system ? state.system.hostname : "Board"} (in the apps bundle, as the launcher's user)`;
      actions.innerHTML = `<button class="btn sm" id="shellCopy" title="Copy the selection, or everything on screen (Ctrl+Shift+C copies, Ctrl+V pastes)">${icon("copy")}Copy</button><button class="btn sm" id="shellRestart">${icon("refresh")}Restart</button>`;
      $("#shellCopy").onclick = () => copyTerm(c.term, "the Shell");
      $("#shellRestart").onclick = () => openShell(consoles.shell);
    } else {
      const id = key.slice(4);
      $("#consoleLabel").textContent = `${consoleName(id)} · Live Log`;
      actions.innerHTML = `<button class="btn sm" id="logCopy2" title="Copy the selection, or the whole log (Ctrl+C copies a selection)">${icon("copy")}Copy</button><a class="btn sm" href="/api/logs/${id}">${icon("download")}Download</a><button class="btn sm" id="logClear2">${icon("trash")}Clear</button>`;
      $("#logCopy2").onclick = () => copyTerm(c.term);
      $("#logClear2").onclick = async () => { await post(`/api/logs/${id}/clear`); c.term.reset(); };
    }
    $$("svg", actions).forEach((s) => { s.setAttribute("width", 14); s.setAttribute("height", 14); });
    if (scroll) $("#consoleTabs").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ------------------------------------------------------------------ router
  function route() {
    const h = location.hash || "#/";
    if (h === "#apps") { show("home"); setTimeout(() => $("#apps").scrollIntoView({ behavior: "smooth" }), 30); return; }
    const m = h.match(/^#\/app\/([\w.-]+)(?:\/(\w+))?/);
    if (m) {
      const same = state.route.page === "app" && state.route.id === m[1];
      state.route = { page: "app", id: m[1], tab: m[2] };
      show("app");
      if (same && m[2] && m[2] !== appPage.tab) selectTab(m[2]);
      else if (!same) { renderAppPage(true); window.scrollTo(0, 0); }
      return;
    }
    if (h.startsWith("#/insight")) { state.route = { page: "insight" }; show("insight"); renderInsightPage(true); return; }
    if (h.startsWith("#/manager")) { state.route = { page: "manager" }; show("manager"); return; }
    state.route = { page: "home" }; show("home");
  }
  function show(page) {
    if (page !== "app") { disposeAppPage(); $("#appView").innerHTML = ""; }
    if (page !== "insight") { $("#insightView").innerHTML = ""; insightPage.built = ""; }
    $$(".page").forEach((p) => p.classList.toggle("active", p.id === `page-${page}`));
    $$(".nav a").forEach((a) => a.classList.toggle("active", a.dataset.nav === page || (page === "app" && a.dataset.nav === "home")));
    document.body.classList.toggle("in-app", page === "app" || page === "insight");
    if (page === "manager") {
      renderStats(); renderProcTable(); renderUpdates();
      if (Date.now() - lastUpdateCheck > 60 * 1000) checkUpdates(false);
      if (!currentConsole) selectConsole("shell"); else setTimeout(() => consoles[currentConsole] && consoles[currentConsole].term._fit(), 30);
    }
    if (page !== "app") window.scrollTo(0, 0);
  }
  window.addEventListener("hashchange", route);
  $("#exploreBtn").addEventListener("click", (e) => { e.preventDefault(); $("#apps").scrollIntoView({ behavior: "smooth" }); });
  $("#checkUpdatesBtn").addEventListener("click", () => checkUpdates(true));
  $("#stopAllBtn").addEventListener("click", () => post("/api/platform/stop-all"));
  $("#clearFilters").addEventListener("click", (e) => { e.preventDefault(); state.filter = { q: "", cat: "", lang: "", input: "", status: "" }; $("#search").value = ""; renderFilters(); renderGrid(true); });
  let searchTimer = null;
  $("#search").addEventListener("input", (e) => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { state.filter.q = e.target.value.trim(); renderGrid(true); }, 120); });
  $("#resetMlaBtn").addEventListener("click", async () => {
    let m;
    try { m = await api("/api/system/mla"); } catch (e) { toast(e.message, "err"); return; }
    let body;
    if (m.legacy_service) {
      body = `<p>Restarts <span class="mono">simaai-appcomplex.service</span>, which releases every model loaded on the MLA (for every program on the board, not only these apps).</p>`;
    } else if (!m.users.length) {
      await modal({ title: "The MLA Is Free", body: "<p>No program has the MLA open, so there is nothing to reset.</p>",
        actions: [{ label: "OK", value: true, cls: "solid" }] });
      return;
    } else {
      body = `<p>Each program holds its own models on the MLA, and they are freed when it exits. This stops ${plural(m.users.length, "program")} that ${m.users.length === 1 ? "has" : "have"} the MLA open, including any not started from here:</p>
        <ul class="mono" style="font-size:12px;max-height:180px;overflow:auto;padding-left:18px">${m.users.map((u) => `<li><b>${u.pid}</b> ${esc(u.cmd.length > 110 ? u.cmd.slice(0, 110) + "…" : u.cmd)}</li>`).join("")}</ul>
        <p class="muted">Each gets up to a minute to exit cleanly, then it is killed.</p>`;
    }
    const ok = await modal({
      title: "Reset the MLA?", body,
      actions: [{ label: "Cancel", value: false, cls: "ghost" }, { label: m.legacy_service ? "Reset MLA" : "Stop and Reset", value: true, cls: "danger solid" }],
    });
    if (!ok) return;
    if (!m.legacy_service) toast(`Stopping ${plural(m.users.length, "program")} on the MLA…`, "info");
    try {
      const r = await api("/api/system/reset-mla", { method: "POST" });
      toast(r.method === "stop" ? `MLA Reset: Stopped ${plural(r.stopped.length, "program")}` : "MLA Reset", "ok");
    } catch (e) { toast(e.message, "err"); }
  });
  $$("[data-replay-intro]").forEach((b) => b.addEventListener("click", (e) => { e.preventDefault(); replayIntro(); }));

  function renderAll() {
    renderHeader();
    renderGrid(false);
    if (state.route.page === "app") renderAppPage(false);
    if (state.route.page === "manager") renderProcTable();
  }

  // ------------------------------------------------------------------ welcome
  function welcome() {
    let seen = false;
    try { seen = localStorage.getItem("neatAppsWelcomed") === "1"; } catch (e) { /* ignore */ }
    if (seen || /[?&]nowelcome\b/.test(location.search)) return;
    const back = document.createElement("div");
    back.className = "welcome-back";
    back.innerHTML = `<div class="welcome">
      <img class="w-mark" src="${S}icons/neat-mark.png" alt="">
      <div class="eyebrow">Welcome to SiMa.ai Modalix</div>
      <h1>${state.order.length} Example Apps, Ready to Run.</h1>
      <p>This launcher runs the Neat example apps on this board and shows their results live in Neat Insight.</p>
      <div class="steps">
        <div class="step"><div class="n">1</div><b>Set Up Once</b><span>One Click Installs the Apps, Neat Core, Insight and Sample Videos.</span></div>
        <div class="step"><div class="n">2</div><b>Pick an App</b><span>Detection, Segmentation, Tracking, Pose, Depth and More.</span></div>
        <div class="step"><div class="n">3</div><b>Press Run</b><span>Its Model Downloads, Then It Runs on the MLA, Live in Insight.</span></div>
      </div>
      <button class="btn spectrum lg" id="welcomeGo">${icon("play")}Get Started</button>
    </div>`;
    document.body.appendChild(back);
    $("#welcomeGo").onclick = () => {
      try { localStorage.setItem("neatAppsWelcomed", "1"); } catch (e) { /* ignore */ }
      back.style.transition = "opacity .3s"; back.style.opacity = "0";
      setTimeout(() => back.remove(), 300);
    };
  }

  // ------------------------------------------------------------------ boot
  async function boot() {
    const [data, plat] = await Promise.all([api("/api/apps"), api("/api/platform")]);
    data.apps.forEach((a) => { state.apps[a.id] = a; state.order.push(a.id); });
    state.platform = plat;
    renderFilters();
    renderPlatform();
    renderAll();
    route();
    introDone.then(welcome);
    checkUpdates(false);
    setInterval(() => checkUpdates(false), 10 * 60 * 1000);
  }
  boot().catch((e) => toast(`Cannot reach the launcher: ${e.message}`, "err"));
})();
