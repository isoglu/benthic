"use strict";

const $ = (sel) => document.querySelector(sel);

// ---------------------------------------------------------------------------
// Sprache: /i18n.js (vor dieser Datei geladen) liefert Sprache und Katalog.
// Die Texte stehen hier auf Deutsch; T() schlägt die englische Fassung nach
// (benthic/locales/en.json, siehe i18n.py). {name} wird aus vars gefüllt.
// ---------------------------------------------------------------------------
const LANG = window.PD_LANG || "de";
const LOCALE = window.PD_LOCALE || "de-DE";
const I18N = window.PD_I18N || {};

function T(de, vars) {
  const s = I18N[de] || de;
  return vars ? s.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m)) : s;
}

// Einzahl oder Mehrzahl, {n} ist die Zahl: TN(3, "{n} Quelle", "{n} Quellen").
function TN(n, one, other, vars) {
  return T(n === 1 ? one : other, Object.assign({ n }, vars || {}));
}

let appVersion = null;
const fmtNum = (n) => Number(n || 0).toLocaleString(LOCALE);

const PROVIDERS = [
  ["arxiv", "arXiv"],
  ["openalex", "OpenAlex"],
  ["unpaywall", "Unpaywall"],
  ["europepmc", "Europe PMC"],
  ["semantic_scholar", "Semantic Scholar"],
  ["core", "CORE"],
  ["elsevier", "Elsevier"],
  ["crossref", "Crossref"],
];
const SOURCE_LABELS = Object.fromEntries(PROVIDERS);
SOURCE_LABELS.Bibliothek = T("Bibliothek");

const VERSION_LABELS = {
  published: T("Verlagsversion"),
  accepted: T("Akzeptiertes Manuskript"),
  submitted: T("Preprint"),
};

// ---------------------------------------------------------------------------
// State system: every result maps to exactly one state, and every state has
// one word, one icon and one colour, used in chips, table, log and history.
// ---------------------------------------------------------------------------
const STATES = {
  library:   { label: T("Vorhanden"),      icon: "i-library",  cls: "library",          title: T("Bereits in der Bibliothek") },
  legal:     { label: "Open Access",       icon: "i-legal",    cls: "legal",            title: T("Über Open Access geladen") },
  oa:        { label: T("OA verfügbar"),   icon: "i-oa",       cls: "legal outline",    title: T("Freie Fassung bekannt, noch nicht geladen") },
  blocked:   { label: T("Manuell öffnen"), icon: "i-blocked",  cls: "legal outline",    title: T("Freie Fassung bekannt, PDF nicht automatisch abrufbar") },
  route:     { label: T("Zusatzweg"),      icon: "i-route",    cls: "deep",             title: T("Über einen Zusatzweg geladen") },
  absent:    { label: T("Nicht gefunden"), icon: "i-absent",   cls: "library outline",  title: T("Keine Open-Access-Kopie gefunden") },
  fail:      { label: T("Fehler"),         icon: "i-fail",     cls: "fail",             title: T("Fehlgeschlagen") },
  running:   { label: T("Läuft"),          icon: "i-activity", cls: "activity",         title: T("Lauf aktiv") },
  done:      { label: T("Fertig"),         icon: "i-legal",    cls: "library",          title: T("Lauf abgeschlossen") },
  cancelled: { label: T("Abgebrochen"),    icon: "i-cancel",   cls: "library",          title: T("Von dir abgebrochen") },
  // Urteile der Quellenprüfung (Hallucination-Check)
  verified:  { label: T("Bestätigt"),        icon: "i-legal",    cls: "legal",         title: T("Quelle existiert und passt zur Angabe") },
  corrected: { label: T("DOI korrigiert"),   icon: "i-fix",      cls: "legal outline", title: T("Quelle existiert, die angegebene DOI war falsch oder fehlte") },
  partial:   { label: T("Weicht ab"),        icon: "i-warn",     cls: "warn",          title: T("Quelle existiert, aber Jahr, Titel oder Autor:innen weichen ab") },
  uncertain: { label: T("Unsicher"),         icon: "i-question", cls: "warn outline",  title: T("Ähnlicher Treffer, bitte selbst prüfen") },
  mismatch:  { label: T("Passt nicht"),      icon: "i-notequal", cls: "fail outline",  title: T("Die DOI gehört zu einem anderen Werk") },
  not_found: { label: T("Nicht auffindbar"), icon: "i-fail",     cls: "fail",          title: T("Kein passendes Werk gefunden, vermutlich erfunden") },
  retracted: { label: T("Zurückgezogen"),    icon: "i-ban",      cls: "fail solid",    title: T("Die Arbeit wurde zurückgezogen") },
};

function stateOf(res) {
  const st = res.status;
  if (st === "skipped" || st === "present") return "library";
  if (st === "ok") return isRouteChannel(res.channel) ? "route" : "legal";
  if (st === "oa") return "oa";
  if (st === "blocked") return "blocked";
  if (st === "absent") return "absent";
  if (st === "error" && /Open Access bekannt/i.test(res.error || "")) return "blocked";
  return "fail";
}

function icon(id, cls = "ic") {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("class", cls);
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(ns, "use");
  use.setAttribute("href", "#" + id);
  svg.appendChild(use);
  return svg;
}

function chip(stateKey, text) {
  const s = STATES[stateKey];
  const el = document.createElement("span");
  el.className = "chip " + s.cls;
  el.title = s.title;
  el.append(icon(s.icon), document.createTextNode(text == null ? s.label : text));
  return el;
}

// "10.1016/j.ijinfomgt.2024.102783" -> "10.1016/j.ijinf…102783"
function midTrunc(str, max) {
  if (!str || str.length <= max) return str || "";
  const keep = max - 1;
  const head = Math.ceil(keep * 0.6);
  return str.slice(0, head) + "…" + str.slice(str.length - (keep - head));
}

function hostOf(url) {
  try { return new URL(url).host; } catch (e) { return url; }
}

let settings = {};
let pollTimer = null;
let currentJobId = null;
let currentResults = [];
let lastSeq = 0;
let eventLog = [];
let lastFailed = [];

// ---------------------------------------------------------------------------
// Health
// ---------------------------------------------------------------------------
async function checkHealth() {
  const el = $("#health");
  const dot = $("#health-dot");
  try {
    const r = await fetch("/api/health");
    const j = await r.json();
    el.textContent = T("verbunden · v{version}", { version: j.version });
    appVersion = j.version;
    dot.className = "dot ok";
  } catch (e) {
    el.textContent = "offline";
    dot.className = "dot err";
  }
}

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------
async function loadSettings() {
  try {
    const r = await fetch("/api/settings");
    settings = await r.json();
  } catch (e) { settings = {}; }
  renderSettings();
}

function renderSettings() {
  $("#set-routes").checked = !!settings.extra_routes;
  $("#quick-routes").checked = !!settings.extra_routes;
  $("#set-skip").checked = !!settings.skip_existing;
  $("#set-jufo").checked = settings.journal_jufo !== false;
  $("#set-lists").checked = settings.journal_lists !== false;
  $("#set-hijacked").checked = !!settings.hijacked_check;
  loadListState();
  $("#quick-skip").checked = !!settings.skip_existing;
  $("#set-prefer").checked = !!settings.prefer_published;
  $("#quick-prefer").checked = !!settings.prefer_published;
  $("#set-concurrency").value = settings.concurrency || 3;
  $("#quick-concurrency").value = settings.concurrency || 3;
  $("#set-institution").value = settings.institution || "";
  $("#set-email").value = settings.unpaywall_email || "";
  $("#set-core-key").value = settings.core_api_key || "";
  $("#set-openalex-key").value = settings.openalex_api_key || "";
  $("#set-s2-key").value = settings.semanticscholar_api_key || "";
  $("#set-elsevier-key").value = settings.elsevier_api_key || "";
  $("#set-download-dir").value = settings.download_dir || "";
  $("#set-proxy").value = settings.proxy_prefix || "";
  $("#set-sources").value = (settings.library_sources || []).join("\n");
  $("#set-update-url").value = settings.update_url || "";
  renderProviders();
  renderRoute();
  renderMode();
  pluginHooks.settings.forEach((fn) => { try { fn(settings); } catch (e) { console.error(e); } });
}

function renderProviders() {
  const enabled = new Set(settings.oa_providers || []);
  const box = $("#settings-providers");
  box.innerHTML = "";
  PROVIDERS.forEach(([name, label], i) => {
    const el = document.createElement("label");
    el.className = "provider" + (enabled.has(name) ? " on" : "");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = enabled.has(name);
    cb.onchange = () => {
      const set = new Set(settings.oa_providers || []);
      if (cb.checked) set.add(name); else set.delete(name);
      settings.oa_providers = PROVIDERS.map((p) => p[0]).filter((p) => set.has(p));
      el.classList.toggle("on", cb.checked);
      renderRoute();
    };
    const n = document.createElement("span");
    n.className = "n";
    n.textContent = label;
    const o = document.createElement("span");
    o.className = "o";
    o.textContent = String(i + 1);
    el.append(cb, n, o);
    box.appendChild(el);
  });
}

function renderRoute() {
  const c = $("#cascade");
  c.innerHTML = "";
  const enabled = new Set(settings.oa_providers || []);
  PROVIDERS.filter(([n]) => enabled.has(n)).forEach(([, label]) => {
    const s = document.createElement("span");
    s.className = "route-source";
    s.textContent = label;
    c.appendChild(s);
  });
  if (!c.children.length) c.innerHTML = `<span class="hint">${T("keine Quelle aktiv")}</span>`;
  const extra = $("#route-extra");
  extra.classList.toggle("off", !routesOn());
  $("#route-extra-label").textContent = routesOn() ? routesLabel() : T("{label} (aus)", { label: routesLabel() });
}

// Only shown when plugins add extra routes: says whether they are on.
function renderMode() {
  const on = routesOn();
  const b = $("#mode-badge");
  b.classList.toggle("deep", on);
  b.querySelector("use").setAttribute("href", on ? "#i-route" : "#i-shield");
  $("#mode-title").textContent = on ? T("Zusatzwege an") : T("Nur Open Access");
  $("#mode-sub").textContent = on ? ROUTES.map((r) => r.label).join(", ") : T("Zusatzwege aus");
}

function collectSettings() {
  const s = JSON.parse(JSON.stringify(settings));
  s.extra_routes = $("#set-routes").checked;
  s.skip_existing = $("#set-skip").checked;
  s.journal_jufo = $("#set-jufo").checked;
  s.journal_lists = $("#set-lists").checked;
  s.hijacked_check = $("#set-hijacked").checked;
  s.prefer_published = $("#set-prefer").checked;
  s.concurrency = parseInt($("#set-concurrency").value, 10) || 3;
  s.unpaywall_email = $("#set-email").value.trim();
  s.institution = $("#set-institution").value.trim();
  s.core_api_key = ($("#set-core-key").value || "").trim();
  s.openalex_api_key = ($("#set-openalex-key").value || "").trim();
  s.semanticscholar_api_key = ($("#set-s2-key").value || "").trim();
  s.elsevier_api_key = ($("#set-elsevier-key").value || "").trim();
  s.download_dir = $("#set-download-dir").value.trim();
  s.proxy_prefix = $("#set-proxy").value.trim();
  s.library_sources = $("#set-sources").value.split("\n").map((x) => x.trim()).filter(Boolean);
  s.update_url = $("#set-update-url").value.trim();
  return s;
}

async function postSettings(body) {
  const r = await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error("HTTP " + r.status);
  return r.json();
}

async function saveSettings() {
  try {
    settings = await postSettings(collectSettings());
    renderSettings();
    flash("#settings-saved", T("gespeichert"));
  } catch (e) {
    flash("#settings-saved", T("Speichern fehlgeschlagen"));
  }
}

// Quick switches on the Download view take effect at once: the backend reads
// the saved settings when a job starts, so an unsaved switch would be ignored.
async function persistSetting(partial) {
  Object.assign(settings, partial);
  renderRoute();
  renderMode();
  try {
    settings = await postSettings(partial);
    renderSettings();
  } catch (e) {
    toast(T("Einstellung nicht gespeichert"), T("Bitte erneut versuchen."));
    loadSettings();
  }
}

// ---------------------------------------------------------------------------
// Library
// ---------------------------------------------------------------------------
async function loadLibrary() {
  try {
    const r = await fetch("/api/library");
    const j = await r.json();
    $("#side-count").textContent = fmtNum(j.count);
    renderLibrary(j);
  } catch (e) { /* ignore */ }
}

function renderLibrary(j) {
  const box = $("#library-stats");
  box.innerHTML = "";
  box.appendChild(chip("library", TN(j.count || 0, "{n} DOI", "{n} DOIs", { n: fmtNum(j.count) })));
  const st = j.stats || {};
  if (st.oa) box.appendChild(chip("legal", `${fmtNum(st.oa)} Open Access`));
  if (st.routes) box.appendChild(chip("route", `${st.routes} ${routesLabel()}`));

  const list = $("#library-entries");
  list.innerHTML = "";
  (j.entries || []).slice(0, 10).forEach((e) => {
    const row = document.createElement("div");
    row.className = "entry";
    const doi = document.createElement("span");
    doi.className = "mono";
    doi.textContent = midTrunc(e.doi, 34);
    doi.title = e.doi;
    const title = document.createElement("span");
    title.className = "t";
    title.textContent = e.title || "";
    title.title = e.title || "";
    row.append(doi, title);
    list.appendChild(row);
  });
}

// ---------------------------------------------------------------------------
// Zotero: senden (Connector auf 127.0.0.1:23119) und die Bibliothek auf Rückzüge prüfen
// ---------------------------------------------------------------------------
async function loadZotero() {
  let st = {};
  try { st = await (await fetch("/api/zotero")).json(); } catch (e) { return; }
  const t = st.target || {};
  $("#zotero-state").textContent = st.running
    ? T("Zotero läuft") + (t.collection || t.library ? " · " + T("Ziel: {name}", { name: t.collection || t.library }) : "")
    : (st.data_dir ? T("Zotero ist geschlossen") : T("Zotero nicht gefunden"));
  $("#zotero-scan-btn").disabled = !st.data_dir;
}

async function sendToZotero(body, btn) {
  btn.disabled = true;
  let r = null;
  let j = {};
  try {
    r = await fetch("/api/zotero/send", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    j = await r.json();
  } catch (e) { /* unten */ }
  btn.disabled = false;
  if (!r || !r.ok) { toast(T("Nicht an Zotero gesendet"), j.error || T("Zotero antwortet nicht.")); return; }
  const t = j.target || {};
  toast(T("An Zotero gesendet"), TN(j.sent, "{n} Eintrag", "{n} Einträge") +
    (t.collection || t.library ? " · " + (t.collection || t.library) : "") +
    ((j.missing || []).length ? " · " + TN(j.missing.length, "{n} ohne Zitierdaten", "{n} ohne Zitierdaten") : ""));
}

async function scanZotero() {
  const btn = $("#zotero-scan-btn");
  const msg = $("#zotero-msg");
  const list = $("#zotero-flagged");
  btn.disabled = true;
  msg.textContent = T("Prüft die Zotero-Bibliothek …");
  list.hidden = true;
  let r = null;
  let j = {};
  try {
    r = await fetch("/api/zotero/scan", { method: "POST" });
    j = await r.json();
  } catch (e) { /* unten */ }
  btn.disabled = false;
  if (!r || !r.ok) { msg.textContent = j.error || T("Prüfung fehlgeschlagen"); return; }
  const flagged = j.flagged || [];
  msg.textContent = [TN(j.items, "{n} Eintrag", "{n} Einträge"), TN(j.with_doi, "{n} mit DOI", "{n} mit DOI"),
    flagged.length ? TN(flagged.length, "{n} auffällig", "{n} auffällig") : T("nichts Auffälliges")].join(" · ");
  list.innerHTML = "";
  flagged.slice(0, 100).forEach((f) => {
    const li = document.createElement("li");
    const top = document.createElement("div");
    top.className = "wp-top";
    f.notices.forEach((n) => top.appendChild(watchTag(n.kind, n.date)));
    const t = document.createElement("span");
    t.className = "wp-title";
    t.textContent = f.title || f.doi;
    top.appendChild(t);
    const m = document.createElement("div");
    m.className = "res-meta";
    const a = document.createElement("a");
    a.className = "mono";
    a.href = "https://doi.org/" + f.doi;
    a.target = "_blank";
    a.rel = "noopener";
    a.textContent = midTrunc(f.doi, 44);
    const z = document.createElement("a");
    z.href = f.link;
    z.textContent = T("In Zotero zeigen");
    m.append(a, z);
    const notice = (f.notices.find((n) => n.notice_doi && n.notice_doi !== f.doi.toLowerCase()) || {}).notice_doi;
    if (notice) {
      const n = document.createElement("a");
      n.href = "https://doi.org/" + notice;
      n.target = "_blank";
      n.rel = "noopener";
      n.textContent = T("Mitteilung lesen");
      m.appendChild(n);
    }
    li.append(top, m);
    list.appendChild(li);
  });
  list.hidden = !flagged.length;
}

async function scanLibrarySources() {
  $("#library-msg").textContent = T("Scanne …");
  try {
    const r = await fetch("/api/library/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sources: true }),
    });
    const j = await r.json();
    const added = (j.results || []).reduce((n, x) => n + (x.added || 0), 0);
    $("#library-msg").textContent = T("{added} DOIs übernommen · {count} in der Bibliothek", { added: fmtNum(added), count: fmtNum(j.count) });
    loadLibrary();
  } catch (e) {
    $("#library-msg").textContent = T("Import fehlgeschlagen");
  }
}

async function clearLibrary() {
  if (!window.confirm(T("Bibliothek wirklich leeren? Benthic lädt danach auch Vorhandenes erneut."))) return;
  const r = await fetch("/api/library/clear", { method: "POST" });
  const j = await r.json();
  $("#library-msg").textContent = TN(j.cleared, "Bibliothek geleert ({n} Eintrag)", "Bibliothek geleert ({n} Einträge)");
  loadLibrary();
}

async function checkLibrary() {
  const text = $("#doi-input").value;
  if (!parseIdentifiers(text).length) return;
  const r = await fetch("/api/library/check", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  const j = await r.json();
  const results = (j.results || []).map((x) => ({
    identifier: x.identifier,
    status: x.present ? "present" : "absent",
    source: x.present ? "Bibliothek" : null,
    channel: x.present ? "library" : null,
    title: x.title,
    filename: x.filename,
    error: x.present ? null : T("nicht in der Bibliothek"),
  }));
  showView("download");
  showResults(results, T("Prüfung: Bibliothek"));
}

// ---------------------------------------------------------------------------
// Zusatzwege (Plugins). Without plugins ROUTES stays empty and every element
// with the class "needs-routes" stays hidden.
// ---------------------------------------------------------------------------
let ROUTES = [];
const pluginHooks = { settings: [] };
const isRouteChannel = (ch) => !!ch && ch !== "oa" && ch !== "library";
const routesOn = () => ROUTES.length > 0 && !!settings.extra_routes;

function routeLabel(id) {
  const r = ROUTES.find((x) => x.id === id);
  return r ? r.label : T("Zusatzweg");
}

function routesLabel() {
  return ROUTES.length === 1 ? ROUTES[0].label : T("Zusatzwege");
}

async function loadRoutes() {
  let j = { routes: [], ui: [] };
  try { j = await (await fetch("/api/routes")).json(); } catch (e) { /* ohne Plugins */ }
  ROUTES = j.routes || [];
  document.documentElement.classList.toggle("has-routes", ROUTES.length > 0);
  if (ROUTES.length === 1) {
    STATES.route.label = ROUTES[0].label;
    STATES.route.title = T("Über {label} geladen", { label: ROUTES[0].label });
  }
  (j.ui || []).forEach((u) => {
    if (u.css) {
      const l = document.createElement("link");
      l.rel = "stylesheet";
      l.href = u.css;
      document.head.appendChild(l);
    }
    if (u.js) {
      const sc = document.createElement("script");
      sc.src = u.js;
      document.body.appendChild(sc);
    }
  });
  renderRoute();
  renderMode();
}

function mkBtn(label, title, onclick, disabled = false) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "btn ghost icon";
  b.textContent = label;
  b.title = title;
  b.setAttribute("aria-label", title);
  b.disabled = disabled;
  b.onclick = onclick;
  return b;
}

// Small API for plugin UI modules (ui.js): own views, settings cards, hooks.
window.Benthic = {
  version: 1,
  $, chip, toast, esc: (x) => esc(x), hostOf, mkBtn,
  lang: LANG, T, TN,
  settings: () => settings,
  routes: () => ROUTES,
  addView({ id, label, icon = "i-link", html = "", before = "history", onShow = null }) {
    const nav = document.createElement("button");
    nav.className = "nav-item";
    nav.type = "button";
    nav.dataset.view = id;
    nav.innerHTML = `<svg class="ic"><use href="#${esc(icon)}"/></svg><span>${esc(label)}</span>`;
    nav.onclick = () => showView(id);
    const anchor = document.querySelector(`.nav-item[data-view="${before}"]`);
    anchor.parentNode.insertBefore(nav, anchor);
    const sec = document.createElement("section");
    sec.className = "view";
    sec.id = "view-" + id;
    sec.innerHTML = html;
    const viewAnchor = document.getElementById("view-" + before);
    viewAnchor.parentNode.insertBefore(sec, viewAnchor);
    VIEW_NAMES[id] = label;
    VIEW_ICONS[id] = icon;
    if (onShow) viewHooks[id] = onShow;
    updateViewKeys();
    if ((location.hash || "").replace("#", "") === id) showView(id);
    return sec;
  },
  addSettingsCard(html) {
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = html;
    const anchor = $("#ai-card");
    anchor.parentNode.insertBefore(card, anchor.nextSibling);
    return card;
  },
  onSettings(fn) { pluginHooks.settings.push(fn); fn(settings); },
};

// ---------------------------------------------------------------------------
// Input
// ---------------------------------------------------------------------------
function parseIdentifiers(text) {
  return text.split(/[;,\n]+/).map((s) => s.trim()).filter(Boolean);
}

let counterTimer = null;
function updateCounter() {
  const text = $("#doi-input").value;
  const tokens = parseIdentifiers(text);
  const clean = tokens.every((t) => /^(https?:\/\/|10\.\d{4,9}\/)/i.test(t) || /doi\.org\/10\./i.test(t));
  if (clean) {
    $("#doi-counter").textContent = TN(tokens.length, "{n} DOI", "{n} DOIs");
    return;
  }
  // Ganze Literaturliste: über den Parser zählen (ohne Netzwerk).
  clearTimeout(counterTimer);
  counterTimer = setTimeout(async () => {
    const s = await parseSummary(text);
    if (!s) return;
    const extra = s.title_only + s.with_pmid;
    $("#doi-counter").textContent = TN(s.with_doi, "{n} DOI", "{n} DOIs") + (extra ? " · " + T("{n} ohne DOI", { n: extra }) : "");
  }, 250);
}

async function parseSummary(text) {
  try {
    const r = await fetch("/api/refs/parse", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }),
    });
    return (await r.json()).summary;
  } catch (e) { return null; }
}

// ---------------------------------------------------------------------------
// Jobs
// ---------------------------------------------------------------------------
function setRunning(running) {
  $("#download-btn").disabled = running;
  $("#check-oa-btn").disabled = running;
  $("#check-library-btn").disabled = running;
  $("#cancel-btn").disabled = !running;
  $("#progressbar").classList.toggle("running", running);
}

async function startJob(mode, extra = {}) {
  const text = $("#doi-input").value;
  if (!parseIdentifiers(text).length) return;
  setRunning(true);
  $("#retry-failed-btn").hidden = true;
  if ("Notification" in window && Notification.permission === "default" && !window.webkit) {
    Notification.requestPermission();
  }
  const r = await fetch(mode === "check" ? "/api/check" : "/api/download", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, ...extra }),
  });
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    flash("#doi-counter", j.error || T("Start fehlgeschlagen"));
    setRunning(false);
    return;
  }
  const j = await r.json();
  if (seenJobs) seenJobs.add(String(j.job_id));
  currentJobId = j.job_id;
  currentResults = [];
  lastSeq = 0;
  eventLog = [];
  $("#progress-card").hidden = false;
  $("#results-card").hidden = false;
  const note = $("#progress-dir");
  note.hidden = !j.dir_note;
  note.textContent = j.dir_note || "";
  $("#progress-fill").style.width = "0%";
  dive.reset();
  $("#progress-stats").innerHTML = "";
  $("#live-now").innerHTML = "";
  $("#event-feed").innerHTML = "";
  toast(mode === "check" ? T("Prüfung gestartet") : T("Download gestartet"),
    TN(j.total, "{n} DOI", "{n} DOIs") + (routesOn() && extra.extra_routes !== false ? " · " + T("Zusatzwege an") : ""));
  if (j.unresolved) {
    toast(T("Angaben ohne DOI übersprungen"), TN(j.unresolved, "{n} Eintrag ohne DOI. Unter „Prüfen“ lässt er sich auflösen.",
      "{n} Einträge ohne DOI. Unter „Prüfen“ lassen sie sich auflösen."));
  }
  pollJob(j.job_id, mode);
}

function pollJob(jobId, mode) {
  clearTimeout(pollTimer);
  async function tick() {
    let job;
    try {
      const r = await fetch(`/api/jobs/${jobId}`);
      job = await r.json();
    } catch (e) {
      pollTimer = setTimeout(tick, 1500);
      return;
    }
    renderProgress(job);
    if (job.results) showResults(job.results, mode === "check" ? T("Prüfung: Open Access") : T("Ergebnisse"));
    if (job.status === "done" || job.status === "cancelled") {
      setRunning(false);
      updateRetryButton(job.results || []);
      notifyOutcome(job);
      loadFiles();
      loadLibrary();
      loadHistory();
      loadProjects();
      if (document.querySelector("#view-overview.active")) loadOverview();
      return;
    }
    pollTimer = setTimeout(tick, 700);
  }
  tick();
}

async function cancelJob() {
  if (!currentJobId) return;
  $("#cancel-btn").disabled = true;
  await fetch(`/api/jobs/${currentJobId}/cancel`, { method: "POST" });
}

function renderProgress(job) {
  const total = job.total || 0;
  const done = job.done || 0;
  const pct = total ? Math.round((done / total) * 100) : 0;
  $("#progress-fill").style.width = pct + "%";
  $("#progressbar").setAttribute("aria-valuenow", String(pct));
  $("#progress-count").textContent = `${done} / ${total} · ${pct} %`;
  const statusKey = job.status === "cancelled" ? "cancelled" : (job.status === "done" ? "done" : "running");
  const badge = $("#progress-status");
  badge.innerHTML = "";
  badge.appendChild(chip(statusKey));

  const now = Date.now() / 1000;
  const end = job.finished_at || now;
  const elapsed = job.created_at ? Math.max(0, end - job.created_at) : 0;
  let eta = T("Laufzeit {time}", { time: fmtDur(elapsed) });
  if (job.status === "running" && done > 0 && done < total) {
    eta += " · " + T("noch etwa {time}", { time: fmtDur((elapsed / done) * (total - done)) });
  }
  $("#progress-eta").textContent = eta;

  renderSummary($("#progress-stats"), job.results || [], true);
  renderLive(job);
  processEvents(job);
  dive.update(job);
  dockBadge(job.status === "running" ? Math.max(0, total - done) || "…" : "");
}

// Summary chips in state order: surface, legal, extra routes, failures.
function renderSummary(box, results, emptyHint) {
  box.innerHTML = "";
  const counts = {};
  results.forEach((r) => { const k = stateOf(r); counts[k] = (counts[k] || 0) + 1; });
  ["library", "legal", "oa", "blocked", "route", "absent", "fail"].forEach((k) => {
    if (counts[k]) box.appendChild(chip(k, `${counts[k]} ${STATES[k].label}`));
  });
  if (emptyHint && !results.length) {
    const h = document.createElement("span");
    h.className = "hint";
    h.textContent = T("Noch keine Ergebnisse");
    box.appendChild(h);
  }
}

function renderLive(job) {
  const box = $("#live-now");
  box.innerHTML = "";
  const active = job.active || {};
  const ids = Object.keys(active);
  if (!ids.length) {
    if (job.status === "running") box.innerHTML = `<span class="hint">${T("warte auf den nächsten Schritt …")}</span>`;
    return;
  }
  ids.slice(0, 8).forEach((ident) => {
    const a = active[ident];
    const row = document.createElement("div");
    row.className = "live-row";
    const id = document.createElement("span");
    id.className = "live-id";
    id.textContent = ident;
    id.title = ident;
    const step = document.createElement("span");
    step.className = "live-step " + (a.route ? "deep" : "legal");
    step.appendChild(icon("i-activity"));
    const txt = document.createElement("span");
    txt.innerHTML = stepLabel(a);
    step.appendChild(txt);
    row.append(id, step);
    box.appendChild(row);
  });
}

function sourceLabel(s) { return SOURCE_LABELS[s] || s || ""; }
function versionLabel(v) { return VERSION_LABELS[v] || ""; }
function esc(s) { return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

function stepLabel(a) {
  const label = esc(a.route ? (a.target || routeLabel(a.route)) : sourceLabel(a.source));
  const ver = versionLabel(a.version);
  switch (a.phase) {
    case "start": return T("startet …");
    case "provider_start": return T("prüfe <b>{label}</b> …", { label });
    case "provider_done": return T("<b>{label}</b> geprüft", { label });
    case "download_start": return ver ? T("lade PDF von <b>{label}</b> ({version}) …", { label, version: esc(ver) })
      : T("lade PDF von <b>{label}</b> …", { label });
    case "download_fail": return T("<b>{label}</b>: kein PDF", { label });
    case "route_try": return T("Zusatzweg: <b>{label}</b> …", { label });
    case "route_fail": return T("Zusatzweg: <b>{label}</b> ohne Erfolg", { label });
    default: return label || esc(a.phase || "");
  }
}

function processEvents(job) {
  let changed = false;
  (job.events || []).forEach((ev) => {
    if (ev.seq > lastSeq) {
      lastSeq = ev.seq;
      eventLog.push(ev);
      changed = true;
    }
  });
  if (eventLog.length > 120) eventLog = eventLog.slice(-120);
  if (changed) renderFeed();
}

function renderFeed() {
  const box = $("#event-feed");
  box.innerHTML = "";
  eventLog.slice(-40).reverse().forEach((ev) => {
    const row = document.createElement("div");
    row.className = "feed-row";
    const t = document.createElement("span");
    t.className = "feed-ts";
    t.textContent = ev.ts ? new Date(ev.ts * 1000).toLocaleTimeString(LOCALE) : "";
    const id = document.createElement("span");
    id.className = "feed-id";
    id.textContent = ev.identifier || "";
    id.title = ev.identifier || "";
    const msg = document.createElement("span");
    msg.className = "feed-msg " + feedClass(ev);
    msg.textContent = eventText(ev);
    row.append(t, id, msg);
    box.appendChild(row);
  });
}

function feedClass(ev) {
  if (ev.phase === "done") {
    if (ev.status === "ok") return isRouteChannel(ev.channel) ? "deep" : "legal";
    if (ev.status === "skipped" || ev.status === "absent") return "library";
    if (ev.status === "blocked") return "legal";
    return "fail";
  }
  if (ev.phase === "download_ok") return "legal";
  if (ev.phase && ev.phase.startsWith("route_")) return "deep";
  return "";
}

function eventText(ev) {
  const label = ev.route ? (ev.target || ev.route_label || routeLabel(ev.route)) : sourceLabel(ev.source);
  const ver = versionLabel(ev.version);
  switch (ev.phase) {
    case "start": return T("Start");
    case "provider_start": return T("prüfe {label}", { label });
    case "provider_done": return ev.candidates ? TN(ev.candidates, "{label}: {n} Kandidat", "{label}: {n} Kandidaten", { label })
      : T("{label}: nichts", { label });
    case "download_start": return ver ? T("lade PDF ({label}, {version})", { label, version: ver }) : T("lade PDF ({label})", { label });
    case "download_ok": return T("{label}: PDF gespeichert", { label });
    case "download_fail": return T("{label}: kein PDF", { label });
    case "route_try": return T("Zusatzweg: {label}", { label });
    case "route_fail": return T("Zusatzweg: {label}, {reason}", { label, reason: ev.reason || T("ohne Erfolg") });
    case "route_ok": return T("{label}: PDF gespeichert", { label: ev.route_label || routeLabel(ev.route) });
    case "done":
      if (ev.status === "ok") return T("fertig: {label}", { label: isRouteChannel(ev.channel) ? (ev.route_label || routeLabel(ev.channel)) : "Open Access" });
      if (ev.status === "skipped") return T("bereits in der Bibliothek");
      if (ev.status === "blocked") return T("frei verfügbar, PDF manuell öffnen");
      if (ev.status === "absent") return String(ev.error || T("keine Open-Access-Kopie gefunden"));
      return T("Ende: {error}", { error: String(ev.error || T("fehlgeschlagen")).slice(0, 120) });
    default: return ev.phase || "";
  }
}

function fmtDur(sec) {
  sec = Math.max(0, Math.round(sec));
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return m ? `${m}:${String(s).padStart(2, "0")} min` : `${s} s`;
}

function updateRetryButton(results) {
  lastFailed = (results || []).filter((r) => r.status === "error" && stateOf(r) === "fail").map((r) => r.identifier);
  const btn = $("#retry-failed-btn");
  if (lastFailed.length) {
    btn.hidden = false;
    btn.disabled = false;
    btn.querySelector("span").textContent = T("Fehlgeschlagene erneut versuchen ({n})", { n: lastFailed.length });
  } else {
    btn.hidden = true;
  }
}

function notifyOutcome(job) {
  const results = job.results || [];
  const count = (k) => results.filter((r) => stateOf(r) === k).length;
  const parts = [`${count("legal")} Open Access`];
  if (count("route")) parts.push(`${count("route")} ${routesLabel()}`);
  if (count("library")) parts.push(T("{n} vorhanden", { n: count("library") }));
  if (count("blocked")) parts.push(T("{n} manuell", { n: count("blocked") }));
  if (count("fail") + count("absent")) parts.push(T("{n} ohne PDF", { n: count("fail") + count("absent") }));
  const title = job.status === "cancelled" ? T("Benthic: Lauf abgebrochen") : T("Benthic: Lauf fertig");
  notify(title, T("{done} von {total}", { done: results.length, total: job.total }) + " · " + parts.join(" · "));
}

function notify(title, body) {
  toast(title, body);
  try {
    if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.benthicNotify) {
      window.webkit.messageHandlers.benthicNotify.postMessage({ title, body });
      return;
    }
    if ("Notification" in window && Notification.permission === "granted") {
      new Notification(title, { body, icon: "/static/favicon.svg" });
    }
  } catch (e) { /* ignore */ }
}

function toast(title, body) {
  const box = $("#toasts");
  const el = document.createElement("div");
  el.className = "toast";
  const t = document.createElement("strong");
  t.textContent = title;
  const b = document.createElement("span");
  b.textContent = body || "";
  el.append(t, b);
  box.appendChild(el);
  setTimeout(() => {
    el.classList.add("out");
    setTimeout(() => el.remove(), 320);
  }, 5000);
}

// ---------------------------------------------------------------------------
// Results
// ---------------------------------------------------------------------------
function showResults(results, title) {
  currentResults = results || [];
  $("#results-card").hidden = false;
  $("#results-title").textContent = title || T("Ergebnisse");
  const body = $("#results-body");
  body.innerHTML = "";
  currentResults.forEach((res) => body.appendChild(resultRow(res)));
  renderSummary($("#results-stats"), currentResults, false);
}

function resultRow(res) {
  const key = stateOf(res);
  const tr = document.createElement("tr");

  const st = document.createElement("td");
  st.className = "c-state";
  st.appendChild(chip(key));

  // Title first, then DOI and file as meta; long values are shortened in the middle.
  const main = document.createElement("td");
  const title = document.createElement("div");
  title.className = "res-title" + (res.title ? "" : " untitled");
  title.textContent = res.title || T("Titel unbekannt");
  const meta = document.createElement("div");
  meta.className = "res-meta";
  const ident = res.identifier || "";
  const isDoi = /^10\.\d{4,9}\//.test(ident);
  const idEl = document.createElement(isDoi ? "a" : "span");
  idEl.className = "mono";
  idEl.textContent = midTrunc(ident, 44);
  idEl.title = ident;
  if (isDoi) {
    idEl.href = "https://doi.org/" + ident;
    idEl.target = "_blank";
    idEl.rel = "noopener";
  }
  meta.appendChild(idEl);
  if (res.filename) {
    const fn = document.createElement("span");
    fn.className = "mono";
    fn.textContent = midTrunc(res.filename, 44);
    fn.title = res.filename;
    meta.appendChild(fn);
  }
  main.append(title, meta);
  const note = noteFor(res, key);
  if (note) {
    const n = document.createElement("div");
    n.className = "res-note" + (key === "fail" ? " fail" : "");
    n.textContent = note.length > 180 ? note.slice(0, 180) + " …" : note;
    n.title = res.error || note;
    main.appendChild(n);
  }

  const src = document.createElement("td");
  src.className = "c-src";
  const name = document.createElement("div");
  name.className = "src-name";
  const s = res.source || "";
  name.textContent = isRouteChannel(res.channel) ? (s || routeLabel(res.channel)) : (key === "library" ? T("Bibliothek") : sourceLabel(s));
  name.title = s;
  src.appendChild(name);
  const ver = versionLabel(res.version);
  if (ver) {
    const v = document.createElement("div");
    v.className = "src-version";
    v.textContent = ver;
    src.appendChild(v);
  }

  const act = document.createElement("td");
  act.className = "c-act";
  // Ohne freie Kopie führt der Link über den Proxy der Hochschule, sonst zur Landingpage.
  const viaProxy = (key === "absent" || key === "fail") && !!res.proxy_url;
  const link = viaProxy ? res.proxy_url : (res.landing_url || res.proxy_url);
  if (link && key !== "legal" && key !== "route" && key !== "library") {
    const a = document.createElement("a");
    const label = viaProxy ? T("Über deine Hochschule öffnen") : T("Im Browser öffnen");
    a.href = link;
    a.target = "_blank";
    a.rel = "noopener";
    a.className = "open-link";
    a.title = label;
    a.setAttribute("aria-label", label);
    a.appendChild(icon("i-blocked"));
    act.appendChild(a);
  }

  tr.append(st, main, src, act);
  return tr;
}

function noteFor(res, key) {
  if (key === "legal" || key === "route") return "";
  if (key === "library") return res.status === "skipped" ? T("Bereits in der Bibliothek, nicht erneut geladen.") : "";
  if (key === "blocked") return T("Freie Fassung bekannt, das PDF lässt sich aber nicht automatisch laden. Im Browser öffnen.");
  if (key === "oa") return T("Freie Fassung verfügbar.");
  const text = (res.error || "").replace(/https?:\/\//g, "");
  return res.retryable ? T("{text}. Später erneut versuchen.", { text }) : text;
}

async function exportJob(fmt) {
  if (!currentResults.length) return;
  const r = await fetch("/api/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ format: fmt, results: currentResults }),
  });
  if (!r.ok) return;
  const blob = await r.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `benthic-export.${fmt}`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// ---------------------------------------------------------------------------
// History
// ---------------------------------------------------------------------------
async function loadHistory() {
  try {
    const r = await fetch("/api/history");
    const j = await r.json();
    $("#side-runs").textContent = (j.summaries || []).length;
    renderHistory(j.summaries || []);
  } catch (e) { /* ignore */ }
}

function renderHistory(items) {
  const box = $("#history-list");
  box.innerHTML = "";
  if (!items.length) {
    box.innerHTML = `<span class="hint">${T("Noch keine Läufe.")}</span>`;
    return;
  }
  items.slice(0, 20).forEach((it) => {
    const row = document.createElement("div");
    row.className = "history-row";
    const kind = document.createElement("div");
    kind.className = "history-kind";
    const k = document.createElement("strong");
    k.textContent = `#${it.id} ${runKind(it.mode)}`;
    const when = document.createElement("span");
    when.textContent = it.finished_at ? new Date(it.finished_at * 1000).toLocaleString(LOCALE, { dateStyle: "medium", timeStyle: "short" }) : "";
    kind.append(k, when);
    const chips = document.createElement("div");
    chips.className = "chips";
    const add = (key, n) => { if (n) chips.appendChild(chip(key, `${n} ${STATES[key].label}`)); };
    if (it.mode === "verify") {
      VERDICT_ORDER.forEach((v) => add(v, (it.verdicts || {})[v]));
    }
    add("legal", it.oa);
    add("route", it.routes);
    add("library", it.present);
    add("blocked", it.blocked);
    add("absent", it.absent);
    add("fail", it.errors);
    if (!chips.children.length) {
      const h = document.createElement("span");
      h.className = "hint";
      h.textContent = TN(it.total || 0, "{n} DOI", "{n} DOIs");
      chips.appendChild(h);
    }
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn small";
    btn.textContent = T("Anzeigen");
    btn.onclick = () => viewHistory(it.id);
    row.append(kind, chips, btn);
    box.appendChild(row);
  });
}

function runKind(mode) {
  return { check: T("OA-Prüfung"), verify: T("Quellencheck") }[mode] || "Download";
}

async function viewHistory(jobId) {
  const r = await fetch(`/api/history/${jobId}`);
  if (!r.ok) return;
  const j = await r.json();
  if (j.mode === "verify") {
    showView("verify");
    $("#verify-progress").hidden = true;
    verifyJobId = jobId;
    verifyStyle = j.style || null;
    verifyQuotes = j.quote_check || null;
    verifyMissing = j.missing_works || null;
    renderDocument(j.document || null);  // zuerst: die Zeilen holen sich daraus ihre Zitatstellen
    renderVerify(j.results || [], T("Prüfbericht #{id}", { id: jobId }), false);
    renderStyle(verifyStyle);
    loadVerifyHealth();
    scrollMainTo($("#verify-results"));
    return;
  }
  currentJobId = jobId;
  showView("download");
  showResults(j.results || [], T("Lauf #{id}", { id: jobId }));
  scrollMainTo($("#results-card"));
}

// Scrolls only the main column. scrollIntoView would also move the window in
// WebKit, because overflow: hidden does not stop programmatic scrolling.
function scrollMainTo(el, smooth = true) {
  const main = $(".main");
  const top = el.getBoundingClientRect().top - main.getBoundingClientRect().top + main.scrollTop - 16;
  main.scrollTo({ top: Math.max(0, top), behavior: smooth ? "smooth" : "auto" });
}

// ---------------------------------------------------------------------------
// Files
// ---------------------------------------------------------------------------
function fmtSize(bytes) {
  if (bytes < 1024) return bytes + " B";
  const one = (x) => x.toLocaleString(LOCALE, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  if (bytes < 1024 * 1024) return one(bytes / 1024) + " KB";
  return one(bytes / (1024 * 1024)) + " MB";
}

async function loadFiles() {
  try {
    const r = await fetch("/api/files");
    const j = await r.json();
    $("#files-dir").textContent = j.dir || "";
    const ul = $("#file-list");
    ul.innerHTML = "";
    const files = (j.files || []).slice().sort((a, b) => (b.mtime || 0) - (a.mtime || 0)).slice(0, 15);
    $("#files-card").hidden = !files.length;
    files.forEach((f) => {
      const li = document.createElement("li");
      li.className = "file-item";
      const nm = document.createElement("span");
      nm.className = "nm";
      nm.textContent = f.name;
      nm.title = f.name;
      const sz = document.createElement("span");
      sz.className = "sz";
      sz.textContent = fmtSize(f.size);
      li.append(icon("i-file"), nm, sz);
      ul.appendChild(li);
    });
  } catch (e) { /* ignore */ }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
const flashTimers = {};
function flash(sel, msg) {
  const el = $(sel);
  if (!el) return;
  if (!(sel in flashTimers)) el.dataset.orig = el.textContent;
  el.textContent = msg;
  el.style.color = "var(--accent)";
  clearTimeout(flashTimers[sel]);
  flashTimers[sel] = setTimeout(() => {
    el.textContent = el.dataset.orig || "";
    el.style.color = "";
    delete flashTimers[sel];
    if (sel === "#doi-counter") updateCounter();
  }, 2200);
}

// ---------------------------------------------------------------------------
// Navigation
// ---------------------------------------------------------------------------
function showView(name) {
  if (!document.getElementById("view-" + name)) name = "verify";
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + name));
  document.querySelectorAll(".nav-item").forEach((n) => {
    const on = n.dataset.view === name;
    n.classList.toggle("active", on);
    if (on) n.setAttribute("aria-current", "page"); else n.removeAttribute("aria-current");
  });
  if (window.history && history.replaceState) history.replaceState(null, "", "#" + name);
  if (name === "library") { loadLibrary(); loadFiles(); loadZotero(); }
  if (name === "history") loadHistory();
  if (viewHooks[name]) { try { viewHooks[name](); } catch (e) { console.error(e); } }
  if (name === "settings") { loadSettings(); loadMcp(); loadRankings(); renderSendCard(); loadUsage(); }
  if (name === "projects") loadProjects();
  if (name === "batch") loadBatches();
  if (name === "settings") showSettingsSection();
  if (name === "about") $("#about-version").textContent = appVersion ? T("Version {v}", { v: appVersion }) : "";
  if (name === "overview") loadOverview();
}

function syncToggle(key, checked) {
  const pairs = {
    extra_routes: ["#set-routes", "#quick-routes"],
    skip_existing: ["#set-skip", "#quick-skip"],
    prefer_published: ["#set-prefer", "#quick-prefer"],
  };
  (pairs[key] || []).forEach((sel) => { $(sel).checked = checked; });
}

// ---------------------------------------------------------------------------
// Prüfen: paste anything + hallucination check
// ---------------------------------------------------------------------------
const VERDICT_ORDER = ["verified", "corrected", "partial", "uncertain", "mismatch", "not_found", "retracted"];
const LOADABLE = new Set(["verified", "corrected", "partial"]);
const SAMPLE = `Here are key sources for a presentation on LLMs in research:

1. Harris, C. R., Millman, K. J., & van der Walt, S. J. (2020). Array programming with NumPy. Nature, 585, 357-362. https://doi.org/10.1038/s41586-020-2649-2
2. Vaswani, A., Shazeer, N., et al. (2017). Attention is all you need. Advances in Neural Information Processing Systems.
3. Smith, J. et al. (2021). "A systematic audit of citation reliability in large language models". Journal of Imaginary Results.
4. He, K. et al. Deep residual learning for image recognition. arXiv:1512.03385
5. Wakefield, A. J. et al. (1998). Ileal-lymphoid-nodular hyperplasia, non-specific colitis, and pervasive developmental disorder in children. The Lancet. https://doi.org/10.1016/S0140-6736(97)11096-0
6. Smith, J. (2019). "Deep learning for protein folding at scale". Science. https://doi.org/10.1126/science.1065467
7. LeCun, Y., Bengio, Y., & Hinton, G. (2015). Deep learning. Nature, 521, 436-444. https://doi.org/10.1038/nature99999
8. Krizhevsky, A., Sutskever, I., & Hinton, G. (2014). ImageNet classification with deep convolutional neural networks. https://doi.org/10.1145/3065386`;

let verifyTimer = null;
let verifyJobId = null;    // Lauf, zu dem der sichtbare Prüfbericht gehört (für die Stilwahl)
let verifyStyle = null;    // Stilprüfung dieses Berichts (style.check_list)
let verifyDoc = null;      // geprüfte Arbeit (document.analyse), für die Zitatstellen je Eintrag
let verifyResults = [];
let previewTimer = null;
const shownRows = new Set();

function schedulePreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(runPreview, 250);
}

async function runPreview() {
  const text = $("#verify-input").value;
  const el = $("#verify-preview");
  el.classList.remove("ready");
  if (!text.trim()) { el.textContent = T("Noch nichts eingefügt"); return; }
  const s = await parseSummary(text);
  if (!s) return;
  if (!s.count) { el.textContent = T("Keine Literaturangaben erkannt"); return; }
  const parts = [TN(s.count, "{n} Quelle erkannt", "{n} Quellen erkannt")];
  if (s.with_doi) parts.push(T("{n} mit DOI", { n: s.with_doi }));
  if (s.with_pmid) parts.push(T("{n} mit PMID", { n: s.with_pmid }));
  if (s.title_only) parts.push(T("{n} nur Titel", { n: s.title_only }));
  const fmt = { bibtex: "BibTeX", ris: "RIS" }[s.format];
  if (fmt) parts.push(fmt);
  el.textContent = parts.join(" · ");
  el.classList.add("ready");
}

// Mehrere Dateien: LaTeX-Projekt (.tex mit .bib, oder .zip) als ganze Arbeit, sonst die erste Datei.
function loadFilesInto(files) {
  const list = [...(files || [])];
  if (!list.length) return;
  if (list.some((f) => /\.(tex|ltx|zip)$/i.test(f.name))) { uploadDocument(list); return; }
  loadFileInto(list[0]);
}

function loadFileInto(file) {
  if (!file) return;
  if (/\.(pdf|docx)$/i.test(file.name)) { uploadDocument([file]); return; }
  if (/\.(sqlite|db)$/i.test(file.name)) {
    toast(T("Dateityp hier nicht möglich"), T("Zotero-Datenbanken lassen sich unter Bibliothek oder in der Einrichtung importieren."));
    return;
  }
  const reader = new FileReader();
  reader.onload = () => {
    $("#verify-input").value = String(reader.result || "");
    $("#verify-input").dataset.name = file.name;  // Name für den Bericht, falls es eine ganze Arbeit ist
    runPreview();
    toast(T("Datei geladen"), file.name);
  };
  reader.readAsText(file);
}

// ---------------------------------------------------------------------------
// Text aus anderen Apps: Dienste-Menü, Lesezeichen im Browser, benthic://check?text=…
// (die App ruft window.benthicReceive auf, sobald die Seite geladen ist)
// ---------------------------------------------------------------------------
const SEND_SOURCE = {
  service: () => T("aus dem Dienste-Menü"),
  browser: () => T("aus dem Browser"),
  link: () => T("über einen Link"),
};

window.benthicReceive = ({ text, source } = {}) => {
  const value = String(text || "").trim();
  if (!value) return;
  showView("verify");
  $("#verify-input").value = value;
  runPreview();
  toast(T("Text empfangen"), (SEND_SOURCE[source] || SEND_SOURCE.link)() + " · " +
    TN(value.length, "{n} Zeichen", "{n} Zeichen", { n: fmtNum(value.length) }));
  if ($("#setup").hidden) startVerify();  // während der Einrichtung nur bereitlegen
};

function bookmarkletCode() {
  const scheme = (window.benthicShell && window.benthicShell.scheme) || "benthic";
  const hint = JSON.stringify(T("Erst die Literaturangaben markieren, dann das Lesezeichen anklicken."));
  const code = `(()=>{const s=String(getSelection()).trim();if(!s){alert(${hint});return}` +
    `const b='${scheme}://check?source=browser&';const u=b+'text='+encodeURIComponent(s);` +
    `if(u.length<30000){location.href=u}else{navigator.clipboard.writeText(s).then(()=>{location.href=b+'clipboard=1'})}})()`;
  return "javascript:" + encodeURIComponent(code);
}

function renderSendCard() {
  const scheme = (window.benthicShell && window.benthicShell.scheme) || "benthic";
  const link = $("#bookmarklet");
  link.href = bookmarkletCode();
  link.onclick = (e) => { e.preventDefault(); toast(T("Lesezeichen"), T("In die Lesezeichenleiste ziehen, nicht anklicken.")); };
  $("#bookmarklet-copy").onclick = () => copyValue(bookmarkletCode());
  $("#send-scheme").textContent = `${scheme}://check?text=…`;
  $("#send-clip").textContent = `${scheme}://check?clipboard=1`;
}

// ---------------------------------------------------------------------------
// Arbeit beim Schreiben beobachten (watchfile.py): bei jedem Sichern neu prüfen
// ---------------------------------------------------------------------------
let watchFiles = [];
let watchFileTimer = null;
const WATCH_ISSUE = {
  missing: () => T("Zitat ohne Eintrag"),
  not_found: () => T("Nicht auffindbar"),
  mismatch: () => T("Passt nicht"),
  retracted: () => T("Zurückgezogen"),
};

async function chooseWatchFile() {
  let path = await pickPath("file", ["pdf", "docx", "tex", "ltx", "txt", "md"]);
  if (!path && !nativePick) path = window.prompt(T("Pfad der Datei, die Benthic beobachten soll:"), "");
  if (!path) return;
  const r = await fetch("/api/watch-file", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path }) });
  const j = await r.json();
  if (!r.ok) { toast(T("Nicht beobachtet"), j.error || ""); return; }
  toast(T("Benthic beobachtet die Datei"), T("Bei jedem Sichern prüft Benthic neu und meldet neue Probleme."));
  renderWatchFiles(j.files || []);
  pollWatchFiles();
}

function renderWatchFiles(files) {
  watchFiles = files;
  const box = $("#watch-files");
  box.innerHTML = "";
  box.hidden = !files.length;
  files.forEach((f) => {
    const row = document.createElement("div");
    row.className = "wf-row";
    const name = document.createElement("strong");
    name.append(icon("i-clock"), document.createTextNode(f.name));
    name.title = f.path;
    const meta = document.createElement("span");
    meta.className = "hint";
    const state = f.error === "missing" ? T("Datei nicht gefunden")
      : f.error === "no-bibliography" ? T("kein Literaturverzeichnis erkannt")
      : f.checking ? T("prüft gerade …")
      : f.last_check ? T("zuletzt geprüft {when}", { when: new Date(f.last_check * 1000).toLocaleTimeString(LOCALE, { hour: "2-digit", minute: "2-digit" }) })
      : T("wartet auf die erste Prüfung");
    const open = (f.issues || []).length;
    meta.textContent = [state, f.last_check ? (open ? TN(open, "{n} offenes Problem", "{n} offene Probleme") : T("keine Probleme")) : null]
      .filter(Boolean).join(" · ");
    row.append(name, meta);
    if (f.job && !f.checking) {
      const v = document.createElement("button");
      v.type = "button";
      v.className = "btn small ghost";
      v.textContent = T("Bericht ansehen");
      v.onclick = () => viewHistory(f.job);
      row.appendChild(v);
    }
    const stop = document.createElement("button");
    stop.type = "button";
    stop.className = "btn small ghost";
    stop.textContent = T("Nicht mehr beobachten");
    stop.onclick = async () => {
      const r = await fetch("/api/watch-file", { method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path: f.path }) });
      renderWatchFiles(((await r.json()).files) || []);
    };
    row.appendChild(stop);
    box.appendChild(row);
  });
}

async function pollWatchFiles() {
  clearTimeout(watchFileTimer);
  let j = {};
  try { j = await (await fetch("/api/watch-file")).json(); } catch (e) { /* später erneut */ }
  const files = j.files || [];
  renderWatchFiles(files);
  const fresh = files.filter((f) => f.unseen);
  if (fresh.length) {
    fresh.forEach((f) => {
      const items = (f.new || []).slice(0, 3).map((i) => `${WATCH_ISSUE[i.kind] ? WATCH_ISSUE[i.kind]() : i.kind}: ${i.label}`);
      const body = (f.new || []).length
        ? TN(f.new.length, "{n} neues Problem", "{n} neue Probleme") + (items.length ? " · " + items.join(" · ") : "")
        : TN(f.resolved, "{n} Problem behoben", "{n} Probleme behoben");
      notify(T("Benthic: {name} neu geprüft", { name: f.name }), body);
    });
    fetch("/api/watch-file", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ seen: true }) });
  }
  if (files.length) watchFileTimer = setTimeout(pollWatchFiles, files.some((f) => f.checking) ? 3000 : 8000);
}

// ---------------------------------------------------------------------------
// Stapelprüfung (batch.py): ein Ordner mit Abgaben, eine Zeile je Arbeit
// ---------------------------------------------------------------------------
let batchId = null;
let batchTimer = null;
let batchSort = { key: null, dir: -1 };
let batchData = null;
const batchOpen = new Set();
const batchNotified = new Set();
const BATCH_ERROR = { "no-bibliography": () => T("Kein Literaturverzeichnis erkannt"), unreadable: () => T("Nicht lesbar") };
const BATCH_STATE = { queued: () => T("wartet"), checking: () => T("prüft …"), done: () => T("geprüft"), error: () => T("Fehler"), skipped: () => T("ausgelassen") };
const BATCH_PROBLEMS = { missing: () => T("Zitate ohne Eintrag"), not_found: () => T("Nicht auffindbar"), mismatch: () => T("Passt nicht"),
  retracted: () => T("Zurückgezogen"), quotes: () => T("Auffällige wörtliche Zitate") };

async function loadBatches(select) {
  let j = {};
  try { j = await (await fetch("/api/batch")).json(); } catch (e) { return; }
  const sel = $("#batch-select");
  sel.innerHTML = "";
  const list = j.batches || [];
  sel.hidden = !list.length;
  list.forEach((b) => sel.appendChild(new Option(`${b.name} · ${new Date(b.created_at * 1000).toLocaleString(LOCALE, { dateStyle: "medium", timeStyle: "short" })}`, b.id)));
  const pick = select || batchId || (list[0] && list[0].id);
  if (pick) { sel.value = pick; showBatch(pick); }
  loadBatchQuota();
}

async function loadBatchQuota() {
  let j = {};
  try { j = await (await fetch("/api/usage")).json(); } catch (e) { return; }
  const oa = (j.services || []).find((s) => s.key === "openalex") || {};
  $("#batch-quota").textContent = oa.paused_until
    ? T("OpenAlex ist heute aufgebraucht; geprüft wird mit Crossref, Semantic Scholar und arXiv.")
    : (oa.remaining !== null && oa.remaining !== undefined ? T("OpenAlex heute noch: {n} Anfragen (etwa zwei je Literaturangabe).", { n: fmtNum(oa.remaining) }) : "");
}

async function startBatch() {
  let folder = await pickPath("folder");
  if (!folder && !nativePick) folder = window.prompt(T("Pfad des Ordners mit den Abgaben:"), "");
  if (!folder) return;
  const body = { folder, recursive: $("#batch-recursive").checked, quotes: $("#batch-quotes").checked, project: $("#batch-project").checked };
  const r = await fetch("/api/batch", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json();
  if (!r.ok) { toast(T("Stapel nicht gestartet"), j.error || ""); return; }
  toast(T("Stapelprüfung gestartet"), TN(j.total, "{n} Arbeit", "{n} Arbeiten"));
  loadBatches(j.id);
}

function fmtDuration(sec) {
  if (sec < 60) return T("{n} s", { n: Math.max(1, Math.round(sec)) });
  return T("{n} min", { n: Math.round(sec / 60) });
}

async function showBatch(id) {
  clearTimeout(batchTimer);
  batchId = id;
  let b;
  try { b = await (await fetch(`/api/batch/${encodeURIComponent(id)}`)).json(); } catch (e) { return; }
  if (!b || b.error) return;
  batchData = b;
  $("#batch-card").hidden = false;
  $("#batch-title").textContent = b.name;
  const running = b.status === "running";
  const t = b.totals || {};
  $("#batch-cancel").hidden = !running;
  $("#batch-rerun").hidden = running || !t.errors;
  $("#batch-state").textContent = [
    running ? T("{done} von {total}", { done: b.done, total: b.total }) + (b.current ? " · " + T("prüft {name}", { name: b.current }) : "")
      : (b.status === "cancelled" ? T("Abgebrochen") : TN(b.total, "{n} Arbeit geprüft", "{n} Arbeiten geprüft")),
    running && b.eta_seconds ? T("noch etwa {time}", { time: fmtDuration(b.eta_seconds) }) : null,
    (b.options || {}).quotes ? T("mit Zitatprüfung") : null,
    b.project ? T("Ergebnisse im Projekt") : null,
  ].filter(Boolean).join(" · ");
  $("#batch-fill").style.width = `${b.total ? Math.round(100 * b.done / b.total) : 0}%`;
  $("#batch-bar").classList.toggle("running", running);
  const tot = $("#batch-totals");
  tot.innerHTML = "";
  [[T("geprüft"), t.checked], [T("mit Problemen"), t.flagged_files, t.flagged_files ? "bad" : ""],
   [T("mit zurückgezogenen Quellen"), t.retracted_files, t.retracted_files ? "bad" : ""],
   [T("Einträge insgesamt"), t.entries], [T("nicht auffindbar"), t.not_found, t.not_found ? "bad" : ""],
   [T("nicht lesbar"), t.errors, t.errors ? "warn" : ""]].forEach(([label, n, cls]) => {
    const d = document.createElement("div");
    d.className = "bt " + (cls || "");
    d.innerHTML = `<b>${fmtNum(n || 0)}</b><span>${esc(label)}</span>`;
    tot.appendChild(d);
  });
  renderBatchRows();
  if (running) batchTimer = setTimeout(() => showBatch(id), 2000);
  else if (b.status === "done" && b.finished_at && Date.now() / 1000 - b.finished_at < 10 && !batchNotified.has(id)) {
    batchNotified.add(id);
    notify(T("Benthic: Stapel fertig"), TN(b.total, "{n} Arbeit geprüft", "{n} Arbeiten geprüft") + " · " + TN(t.flagged_files || 0, "{n} mit Problemen", "{n} mit Problemen"));
  }
}

function renderBatchRows() {
  const b = batchData;
  if (!b) return;
  const body = $("#batch-body");
  body.innerHTML = "";
  let rows = [...(b.rows || [])];
  if ($("#batch-only-problems").checked) rows = rows.filter((r) => r.flagged || r.state === "error");
  if (batchSort.key) {
    rows.sort((x, y) => {
      const a = x[batchSort.key] ?? "", c = y[batchSort.key] ?? "";
      return (typeof a === "number" && typeof c === "number" ? a - c : String(a).localeCompare(String(c))) * batchSort.dir;
    });
  }
  rows.forEach((r) => {
    const tr = document.createElement("tr");
    tr.className = "batch-row" + (r.state === "checking" ? " checking" : "");
    const name = document.createElement("td");
    const t = document.createElement("div");
    t.className = "res-title";
    t.textContent = r.name;
    const meta = document.createElement("div");
    meta.className = "res-meta";
    meta.textContent = [BATCH_STATE[r.state] ? BATCH_STATE[r.state]() : r.state, r.step === "quotes" ? T("Zitate") : null,
      r.seconds ? fmtDuration(r.seconds) : null, r.style_name].filter(Boolean).join(" · ");
    name.append(t, meta);
    if (r.error) {
      const n = document.createElement("div");
      n.className = "res-note warn";
      n.textContent = (BATCH_ERROR[r.error] || (() => r.error))();
      name.appendChild(n);
    }
    tr.appendChild(name);
    ["entries", "missing", "uncited", "not_found", "mismatch", "retracted", "quotes_bad", "style"].forEach((k) => {
      const td = document.createElement("td");
      td.className = "num" + ((["missing", "not_found", "mismatch", "retracted", "quotes_bad"].includes(k) && r[k]) ? " bad" : "");
      td.textContent = r.state === "done" ? (r[k] ?? "") : "";
      tr.appendChild(td);
    });
    const act = document.createElement("td");
    act.className = "c-act batch-act";
    const button = (ic, title, fn) => {
      const x = document.createElement("button");
      x.type = "button";
      x.className = "btn ghost icon";
      x.title = title;
      x.setAttribute("aria-label", title);
      x.appendChild(icon(ic));
      x.onclick = (e) => { e.stopPropagation(); fn(); };
      act.appendChild(x);
    };
    if (r.job_id) {
      button("i-chevron", T("Bericht ansehen"), () => viewHistory(r.job_id));
      button("i-file", T("Bericht als PDF sichern"), () => saveReport(r.job_id, r.name));
    }
    if (nativeReveal) button("i-folder", T("Im Finder zeigen"), () => window.webkit.messageHandlers.benthicReveal.postMessage({ path: r.path }));
    if (b.status !== "running" && (r.state === "done" || r.state === "error")) {
      button("i-retry", T("Erneut prüfen"), async () => {
        await fetch(`/api/batch/${encodeURIComponent(b.id)}/rerun`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ names: [r.name] }) });
        showBatch(b.id);
      });
    }
    tr.appendChild(act);
    const problems = Object.entries(r.problems || {}).filter(([, v]) => v && v.length);
    if (problems.length) {
      tr.classList.add("expandable");
      tr.onclick = () => { batchOpen.has(r.name) ? batchOpen.delete(r.name) : batchOpen.add(r.name); renderBatchRows(); };
    }
    body.appendChild(tr);
    if (problems.length && batchOpen.has(r.name)) {
      const dr = document.createElement("tr");
      dr.className = "batch-detail";
      const td = document.createElement("td");
      td.colSpan = 10;
      problems.forEach(([k, items]) => {
        const h = document.createElement("strong");
        h.textContent = BATCH_PROBLEMS[k] ? BATCH_PROBLEMS[k]() : k;
        const ul = document.createElement("ul");
        items.forEach((i) => { const li = document.createElement("li"); li.textContent = i; ul.appendChild(li); });
        td.append(h, ul);
      });
      dr.appendChild(td);
      body.appendChild(dr);
    }
  });
}

// ---------------------------------------------------------------------------
// Nutzung und Grenzen der Dienste (quota.py): offen zeigen, was Benthic fragt
// ---------------------------------------------------------------------------
const USAGE_HELP = {
  openalex: () => T("Kostenloser eigener Key: openalex.org, dann unter Kontakt und Zugang eintragen."),
  semanticscholar: () => T("Kostenloser Key auf Anfrage: semanticscholar.org/product/api."),
  crossref: () => T("Mit E-Mail-Adresse unter Kontakt und Zugang gilt der schnellere, höfliche Pool."),
};

function timeOf(ts) {
  return new Date(ts * 1000).toLocaleTimeString(LOCALE, { hour: "2-digit", minute: "2-digit" });
}

async function loadUsage() {
  let j = {};
  try { j = await (await fetch("/api/usage")).json(); } catch (e) { return; }
  const box = $("#usage-table");
  box.innerHTML = "";
  const shown = (j.services || []).filter((s) => s.today || s.week || s.paused_until || ["openalex", "crossref", "semanticscholar", "unpaywall"].includes(s.key));
  shown.forEach((svc) => {
    const row = document.createElement("div");
    row.className = "usage-row" + (svc.paused_until ? " paused" : "");
    const name = document.createElement("strong");
    name.textContent = T(svc.name);
    const count = document.createElement("span");
    count.className = "usage-count";
    count.textContent = T("{n} heute", { n: fmtNum(svc.today) }) + (svc.week > svc.today ? " · " + T("{n} in 7 Tagen", { n: fmtNum(svc.week) }) : "");
    const state = document.createElement("span");
    state.className = "usage-state";
    if (svc.paused_until) {
      state.textContent = svc.paused_reason === "budget"
        ? T("Tageskontingent aufgebraucht, wieder ab {time}", { time: timeOf(svc.paused_until) })
        : T("pausiert bis {time} (zu viele Anfragen)", { time: timeOf(svc.paused_until) });
    } else if (svc.key === "openalex" && svc.remaining !== null && svc.remaining !== undefined) {
      state.textContent = T("noch {n} von {limit} Anfragen heute", { n: fmtNum(svc.remaining), limit: fmtNum(svc.limit || 0) }) +
        (svc.keyed ? " · " + T("eigener Key") : "");
    } else if (svc.limited) {
      state.textContent = TN(svc.limited, "{n}-mal gebremst", "{n}-mal gebremst");
    } else {
      state.textContent = svc.today ? T("in Ordnung") : T("heute nicht gefragt");
    }
    const limit = document.createElement("span");
    limit.className = "hint usage-limit";
    limit.textContent = T(svc.limit_text) + (USAGE_HELP[svc.key] && !(j.keys || {})[svc.key] ? " " + USAGE_HELP[svc.key]() : "");
    row.append(name, count, state, limit);
    if (svc.paused_until) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "btn small ghost";
      b.textContent = T("Pause aufheben");
      b.onclick = async () => { await fetch("/api/usage/reset", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ service: svc.key }) }); loadUsage(); };
      row.appendChild(b);
    }
    box.appendChild(row);
  });
  const web = document.createElement("p");
  web.className = "hint";
  web.textContent = T("Dazu {n} Abrufe bei Verlagen und Repositorien (PDFs, Links) seit dem Start.", { n: fmtNum(j.web_today || 0) });
  box.appendChild(web);
  const log = $("#usage-log");
  log.innerHTML = "";
  (j.log || []).slice(0, 120).forEach((l) => {
    const li = document.createElement("li");
    const ok = l.status && l.status < 400;
    li.className = ok ? "" : "bad";
    li.textContent = `${timeOf(l.at)} · ${l.service} · ${l.status || T("keine Antwort")} · ${l.ms} ms · ${l.url}`;
    log.appendChild(li);
  });
}

function renderPaused(job) {
  const el = $("#verify-paused");
  const list = (job && job.paused) || [];
  el.hidden = !list.length;
  if (!list.length) return;
  el.textContent = list.map((p) => p.reason === "budget"
    ? T("{name}: Tageskontingent aufgebraucht bis {time}. Benthic prüft mit den übrigen Quellen weiter.", { name: p.name, time: timeOf(p.until) })
    : T("{name} bremst gerade (zu viele Anfragen), Benthic wartet bis {time}.", { name: p.name, time: timeOf(p.until) })).join(" ");
}

// "So geprüft": welche Quellen gefragt wurden und was sie fanden
const TRACE_STEP = {
  search: (t) => t.failed ? T("keine Antwort") : TN(t.hits || 0, "Suche: {n} Treffer", "Suche: {n} Treffer"),
  doi: (t) => t.found ? T("DOI gefunden") : T("DOI nicht gefunden"),
  best: (t) => T("bester Treffer, Titel zu {n} %", { n: Math.round((t.similarity || 0) * 100) }),
};

function traceLine(res) {
  const steps = res.trace || [];
  if (!steps.length && !(res.sources_failed || []).length) return null;
  const d = document.createElement("details");
  d.className = "trace";
  const sum = document.createElement("summary");
  sum.textContent = T("So geprüft");
  const ol = document.createElement("ol");
  steps.forEach((t) => {
    const li = document.createElement("li");
    const f = TRACE_STEP[t.step];
    li.textContent = `${t.source}: ${f ? f(t) : t.step}` + (t.title && t.step === "best" ? ` · „${t.title}“` : "");
    ol.appendChild(li);
  });
  (res.sources_failed || []).forEach((f) => {
    const li = document.createElement("li");
    li.className = "bad";
    li.textContent = `${f.name}: ` + (f.kind === "paused" ? T("ausgelassen (pausiert)") : f.kind === "limited" ? T("gebremst") : T("keine Antwort"));
    ol.appendChild(li);
  });
  d.append(sum, ol);
  return d;
}

// ---------------------------------------------------------------------------
// Einstellungen in Abschnitten, Prüfbericht in Reitern
// ---------------------------------------------------------------------------
function showSettingsSection(name) {
  let section = name;
  if (!section) { try { section = localStorage.getItem("benthic-settings-section"); } catch (e) { /* ohne Speicher */ } }
  if (!section || !document.querySelector(`#settings-nav [data-section="${section}"]`)) section = "general";
  document.querySelectorAll("#settings-nav button").forEach((b) => b.classList.toggle("on", b.dataset.section === section));
  document.querySelectorAll("#view-settings .card[data-section]").forEach((c) => { c.classList.toggle("section-off", c.dataset.section !== section); });
  try { localStorage.setItem("benthic-settings-section", section); } catch (e) { /* ohne Speicher */ }
}

function showReportTab(tab) {
  $("#verify-results").dataset.tab = tab;
  document.querySelectorAll("#report-tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
}

// Reiter nur zeigen, wenn es dafür Inhalt gibt; Zahlen an den Reitern
function updateReportTabs() {
  const has = {
    sources: true,
    document: !$("#verify-doc").hidden,
    style: !$("#verify-style").hidden,
    list: !$("#verify-health").hidden || !$("#verify-missing").hidden,
  };
  const doc = verifyDoc || {};
  const docCount = (doc.missing_in_list || []).length + (doc.uncited || []).length +
    (verifyQuotes || []).filter((q) => ["not_found", "close"].includes(q.status)).length;
  const styleCount = verifyStyle && verifyStyle.applicable ? Object.values(verifyStyle.entries || {}).filter((v) => v && v.length).length : 0;
  const labels = { sources: T("Quellen"), document: T("Abgleich und Zitate"), style: T("Zitierstil"), list: T("Liste") };
  const counts = { sources: verifyResults.length, document: docCount, style: styleCount };
  // Nur schreiben, was sich ändert: Auch ein gleich bleibendes hidden-Attribut löst den Beobachter
  // unten erneut aus, und die Seite hinge in einer Endlosschleife.
  document.querySelectorAll("#report-tabs button").forEach((b) => {
    const t = b.dataset.tab;
    if (b.hidden !== !has[t]) b.hidden = !has[t];
    const label = labels[t] + (counts[t] ? ` · ${counts[t]}` : "");
    if (b.textContent !== label) b.textContent = label;
  });
  const single = Object.values(has).filter(Boolean).length < 2;
  if ($("#report-tabs").hidden !== single) $("#report-tabs").hidden = single;
  const current = $("#verify-results").dataset.tab;
  if (!has[current]) showReportTab("sources");
}

async function startVerify() {
  const text = $("#verify-input").value;
  if (!text.trim()) { flash("#verify-preview", T("Bitte zuerst etwas einfügen")); return; }
  $("#verify-btn").disabled = true;
  const r = await fetch("/api/verify", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, name: $("#verify-input").dataset.name || null }),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) {
    flash("#verify-preview", j.error || T("Prüfung fehlgeschlagen"));
    $("#verify-btn").disabled = false;
    return;
  }
  beginVerify(j.job_id, j.document || null);
  toast(j.document ? T("Ganze Arbeit erkannt") : T("Prüfung gestartet"),
    j.document ? T("Benthic prüft nur das Literaturverzeichnis ({n} Einträge) und gleicht es mit den Zitaten im Text ab.", { n: j.total })
      : TN(j.total, "{n} Quelle", "{n} Quellen"));
}

// ---------------------------------------------------------------------------
// Zitierstil: Hinweise zur Liste und je Angabe, richtige Fassung über doi.org
// ---------------------------------------------------------------------------
function renderStyle(report) {
  const box = $("#verify-style");
  box.innerHTML = "";
  box.hidden = !(report && report.applicable);
  if (box.hidden) return;
  const head = document.createElement("div");
  head.className = "sc-head";
  const h = document.createElement("h3");
  h.textContent = T("Zitierstil");
  const seg = document.createElement("div");
  seg.className = "segmented";
  seg.setAttribute("role", "radiogroup");
  seg.setAttribute("aria-label", T("Zitierstil"));
  Object.entries(report.styles || {}).forEach(([key, name]) => {
    const b = document.createElement("button");
    b.type = "button";
    b.setAttribute("role", "radio");
    b.setAttribute("aria-checked", String(key === report.style));
    b.textContent = name;
    if (key === report.detected) b.title = T("im Text erkannt");
    b.onclick = () => restyle(key);
    seg.appendChild(b);
  });
  head.append(icon("i-quote"), h, seg);
  box.appendChild(head);
  const flagged = Object.keys(report.entries || {}).length;
  const sum = document.createElement("p");
  sum.className = "sc-sum" + (flagged || (report.list || []).length ? "" : " ok");
  const name = (report.styles || {})[report.style] || report.style;
  if (!flagged && !(report.list || []).length) {
    sum.append(icon("i-legal"), document.createTextNode(T("Alle Angaben folgen {style}.", { style: name })));
  } else {
    sum.textContent = (report.detected && report.detected === report.style
      ? T("Erkannt: {style}.", { style: name }) : T("Geprüft gegen {style}.", { style: name }))
      + " " + TN(flagged, "{n} Angabe mit Hinweisen, sie stehen unten bei der Angabe.", "{n} Angaben mit Hinweisen, sie stehen unten bei der Angabe.");
  }
  box.appendChild(sum);
  if ((report.list || []).length) {
    const ul = document.createElement("ul");
    ul.className = "sc-list";
    report.list.forEach((i) => { const li = document.createElement("li"); li.textContent = i.text; ul.appendChild(li); });
    box.appendChild(ul);
  }
}

async function restyle(key) {
  if (!verifyJobId) return;
  try {
    const r = await fetch("/api/style", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: verifyJobId, style: key }) });
    if (!r.ok) return;
    verifyStyle = await r.json();
  } catch (e) { return; }
  renderStyle(verifyStyle);
  renderVerify(verifyResults, $("#verify-title").textContent, false);
}

function styleNotes(res, issues) {
  const box = document.createElement("div");
  box.className = "style-notes";
  const label = document.createElement("span");
  label.className = "k";
  label.textContent = T("Stil:");
  const list = document.createElement("span");
  list.textContent = issues.map((i) => i.text).join(" ");
  box.append(label, list);
  const doi = res.doi || (res.found || {}).doi;
  if (doi && ["verified", "corrected", "partial"].includes(res.verdict)) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "btn ghost small";
    const name = ((verifyStyle || {}).styles || {})[(verifyStyle || {}).style] || "";
    b.textContent = T("Richtige Fassung ({style})", { style: name });
    b.onclick = async () => {
      b.disabled = true;
      let j = {};
      try { j = await (await fetch("/api/format?" + new URLSearchParams({ doi, style: verifyStyle.style }))).json(); } catch (e) { /* unten */ }
      b.disabled = false;
      if (!j.text) { toast(T("Keine Fassung geladen"), j.error || ""); return; }
      const out = document.createElement("div");
      out.className = "style-fixed";
      const t = document.createElement("span");
      t.textContent = j.text;
      const c = document.createElement("button");
      c.type = "button";
      c.className = "btn small";
      c.textContent = T("Kopieren");
      c.onclick = () => copyValue(j.text);
      out.append(t, c);
      b.replaceWith(out);
    };
    box.appendChild(b);
  }
  return box;
}

// Prüfbericht leeren und einem Lauf folgen (Einfügen, ganze Arbeit, Lauf eines KI-Assistenten).
function beginVerify(jobId, doc) {
  if (seenJobs) seenJobs.add(String(jobId));
  showReportTab(doc ? "document" : "sources");
  verifyResults = [];
  shownRows.clear();
  $("#verify-body").innerHTML = "";
  $("#verify-progress").hidden = false;
  $("#verify-results").hidden = false;
  $("#verify-bar").classList.add("running");
  $("#verify-fill").style.width = "0%";
  verifyDive.reset();
  renderDocument(doc || null);
  scrollMainTo($("#verify-progress"));
  pollVerify(jobId);
}

// Ganze Arbeit (PDF, Word): Verzeichnis und Zitate abgleichen, dann die Einträge prüfen.
async function uploadDocument(files) {
  const fd = new FormData();
  files.forEach((f) => fd.append("file", f));
  const file = files.find((f) => /\.(tex|ltx|zip|pdf|docx)$/i.test(f.name)) || files[0];
  $("#verify-btn").disabled = true;
  const el = $("#verify-preview");
  el.classList.remove("ready");
  el.textContent = T("Lese {name} …", { name: files.length > 1 ? files.map((f) => f.name).join(", ") : file.name });
  let r = null;
  let j = {};
  try {
    r = await fetch("/api/document", { method: "POST", body: fd });
    j = await r.json();
  } catch (e) { /* unten gemeldet */ }
  $("#verify-btn").disabled = false;
  if (!r || !r.ok) {
    el.textContent = "";
    toast(T("Arbeit nicht geprüft"), j.error || T("Prüfung fehlgeschlagen"));
    return;
  }
  const d = j.document;
  el.textContent = [d.name || file.name, TN(d.entries, "{n} Eintrag", "{n} Einträge"), TN(d.citations, "{n} Zitat im Text", "{n} Zitate im Text")].join(" · ");
  el.classList.add("ready");
  beginVerify(j.job_id, d);
  toast(T("Arbeit wird geprüft"), TN(j.total, "{n} Quelle", "{n} Quellen"));
}

function renderDocument(doc) {
  verifyDoc = doc;
  const box = $("#verify-doc");
  box.innerHTML = "";
  box.hidden = !doc;
  if (!doc) return;
  const head = document.createElement("div");
  head.className = "dc-head";
  const h = document.createElement("h3");
  h.textContent = T("Abgleich mit der Arbeit");
  const meta = document.createElement("span");
  meta.className = "hint";
  meta.textContent = [doc.name, TN(doc.entries, "{n} Eintrag", "{n} Einträge"), TN(doc.citations, "{n} Zitat im Text", "{n} Zitate im Text")]
    .filter(Boolean).join(" · ");
  head.append(icon("i-file"), h, meta);
  box.appendChild(head);
  if ((doc.claims || []).length) {
    const tip = document.createElement("p");
    tip.className = "hint dc-tip";
    tip.append(icon("i-quote"), document.createTextNode(T("Bei bestätigten Quellen sucht das Anführungszeichen im geladenen Volltext die Stelle, die zu deinem Satz passt.")));
    box.appendChild(tip);
  }
  if (!doc.cross_check) {
    const p = document.createElement("p");
    p.className = "hint dc-note";
    p.textContent = T("Die Zitate im Text ließen sich nicht sicher lesen, zum Beispiel hochgestellte Zahlen. Benthic prüft deshalb nur das Verzeichnis.");
    box.appendChild(p);
    box.appendChild(quotesSection(doc));
    return;
  }
  const missing = doc.missing_in_list || [];
  const uncited = doc.uncited || [];
  if (!missing.length && !uncited.length) {
    const p = document.createElement("p");
    p.className = "dc-ok";
    p.append(icon("i-legal"), document.createTextNode(T("Jedes Zitat steht im Verzeichnis, und jeder Eintrag wird zitiert.")));
    box.appendChild(p);
    box.appendChild(quotesSection(doc));
    return;
  }
  const cols = document.createElement("div");
  cols.className = "dc-cols";
  const col = (cls, title, items, render) => {
    const c = document.createElement("div");
    c.className = "dc-col " + cls;
    const t = document.createElement("h4");
    t.append(document.createTextNode(title + " "));
    const n = document.createElement("b");
    n.textContent = items.length;
    t.appendChild(n);
    c.appendChild(t);
    if (!items.length) {
      const p = document.createElement("p");
      p.className = "hint";
      p.textContent = T("Keine.");
      c.appendChild(p);
    }
    const ul = document.createElement("ul");
    items.slice(0, 30).forEach((it) => ul.appendChild(render(it)));
    c.appendChild(ul);
    if (items.length > 30) {
      const more = document.createElement("p");
      more.className = "hint";
      more.textContent = T("und {n} weitere", { n: items.length - 30 });
      c.appendChild(more);
    }
    return c;
  };
  cols.append(
    col("warn", doc.style === "latex" ? T("Zitiert, aber nicht in der .bib-Datei") : T("Im Text zitiert, im Verzeichnis nicht gefunden"), missing, (m) => {
      const li = document.createElement("li");
      const b = document.createElement("strong");
      b.textContent = m.label;
      li.appendChild(b);
      if (m.count > 1) { const c = document.createElement("span"); c.className = "hint"; c.textContent = ` ${m.count}×`; li.appendChild(c); }
      const s = document.createElement("p");
      s.className = "dc-sentence";
      s.textContent = m.sentence;
      li.appendChild(s);
      return li;
    }),
    col("", doc.style === "latex" ? T("In der .bib-Datei, nicht zitiert (erscheint nicht im Verzeichnis)") : T("Im Verzeichnis, im Text nie zitiert"), uncited, (u) => {
      const li = document.createElement("li");
      li.textContent = u.label;
      return li;
    }),
  );
  box.appendChild(cols);
  box.appendChild(quotesSection(doc));
}

// ---------------------------------------------------------------------------
// Wörtliche Zitate: Wortlaut und Seite im geladenen Volltext prüfen (quotes.py)
// ---------------------------------------------------------------------------
let verifyQuotes = null;   // Ergebnis von /api/quotes für den sichtbaren Bericht
const QUOTE_STATE = {
  exact: ["ok", "i-legal", () => T("Wortgleich")],
  close: ["warn", "i-warn", () => T("Wortlaut weicht ab")],
  not_found: ["fail", "i-fail", () => T("Nicht in der Quelle gefunden")],
  no_pdf: ["", "i-file", () => T("PDF fehlt")],
  no_source: ["", "i-question", () => T("Quelle nicht bestätigt")],
  pending: ["", "i-clock", () => T("Quelle wird noch geprüft")],
};

function quotesSection(doc) {
  const wrap = document.createElement("div");
  wrap.className = "dc-quotes";
  const list = doc.quotes || [];
  if (!list.length) return wrap;
  const head = document.createElement("div");
  head.className = "dq-head";
  const h = document.createElement("h4");
  h.append(document.createTextNode(T("Wörtliche Zitate") + " "));
  const n = document.createElement("b");
  n.textContent = list.length;
  h.appendChild(n);
  const run = document.createElement("button");
  run.type = "button";
  run.className = "btn small";
  run.append(icon("i-search"), document.createTextNode(T("Mit den Volltexten abgleichen")));
  const note = document.createElement("span");
  note.className = "hint";
  head.append(h, run, note);
  wrap.appendChild(head);
  const ul = document.createElement("ul");
  ul.className = "dq-list";
  wrap.appendChild(ul);

  const render = (checked) => {
    ul.innerHTML = "";
    const rows = checked || list.map((q) => ({ ...q, cited_page: q.page, status: null }));
    rows.slice(0, 80).forEach((q) => ul.appendChild(quoteItem(q)));
    if (checked) {
      const missing = [...new Set(checked.filter((q) => q.status === "no_pdf" && q.doi).map((q) => q.doi))];
      note.textContent = "";
      if (missing.length) note.appendChild(fetchQuoteSources(missing, runCheck));
    } else {
      note.textContent = T("Benthic sucht jedes Zitat wortgleich im geladenen Volltext der Quelle.");
    }
  };
  const runCheck = async () => {
    run.disabled = true;
    note.textContent = T("Gleicht ab …");
    let r = null;
    let j = {};
    try {
      r = await fetch("/api/quotes", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_id: verifyJobId }) });
      j = await r.json();
    } catch (e) { /* unten */ }
    run.disabled = false;
    if (!r || !r.ok) { note.textContent = j.error || T("Abgleich fehlgeschlagen"); return; }
    verifyQuotes = j.quotes;
    render(j.quotes);
  };
  run.onclick = runCheck;
  render(verifyQuotes);
  return wrap;
}

function quoteItem(q) {
  const li = document.createElement("li");
  const top = document.createElement("div");
  top.className = "wp-top";
  if (q.status) {
    const [cls, ic, label] = QUOTE_STATE[q.status] || ["", "i-question", () => T("Nicht prüfbar")];
    const tag = document.createElement("span");
    tag.className = "watch-tag" + (cls ? " " + cls : "");
    tag.append(icon(ic), document.createTextNode(label()));
    top.appendChild(tag);
  }
  const src = document.createElement("span");
  src.className = "hint";
  src.textContent = [q.label, q.cited_page ? T("S. {page}", { page: q.cited_page }) : null].filter(Boolean).join(" · ");
  top.appendChild(src);
  const text = document.createElement("p");
  text.className = "dq-quote";
  text.textContent = `“${q.quote}”`;
  li.append(top, text);
  const where = q.printed_page || q.page;
  if (q.status === "exact" || q.status === "close") {
    const m = document.createElement("p");
    m.className = "dq-meta" + (q.page_ok === false ? " warn" : "");
    if (q.page_ok === false) {
      m.textContent = T("Angegeben ist S. {stated}, in der Quelle steht es auf S. {found}.", { stated: q.stated_page, found: where });
    } else if (q.page_ok === true) {
      m.textContent = T("Seite stimmt (S. {page}).", { page: q.stated_page });
    } else {
      m.textContent = T("In der Quelle auf S. {page}.", { page: where }) +
        (q.printed_page && q.printed_page !== q.page ? " " + T("(PDF-Seite {n})", { n: q.page }) : "");
    }
    li.appendChild(m);
  }
  if ((q.status === "close" || (q.status === "not_found" && q.nearest)) && q.excerpt) {
    const ex = document.createElement("p");
    ex.className = "dq-excerpt";
    const k = document.createElement("span");
    k.className = "k";
    k.textContent = q.status === "close" ? T("In der Quelle:") : T("Ähnlichste Stelle:");
    ex.append(k, document.createTextNode(` ${q.excerpt}`));
    li.appendChild(ex);
  }
  return li;
}

function fetchQuoteSources(dois, again) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "btn small ghost";
  b.append(icon("i-download"), document.createTextNode(TN(dois.length, "{n} fehlende PDF laden", "{n} fehlende PDFs laden")));
  b.onclick = async () => {
    b.disabled = true;
    b.lastChild.textContent = T("Lädt …");
    try {
      const r = await fetch("/api/download", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: dois.join("\n") }) });
      const j = await r.json();
      if (seenJobs) seenJobs.add(String(j.job_id));
      for (let i = 0; i < 600; i++) {
        await new Promise((ok) => setTimeout(ok, 1000));
        const job = await (await fetch(`/api/jobs/${j.job_id}`)).json();
        if (job.status !== "running") break;
      }
    } catch (e) { /* die erneute Prüfung zeigt, was jetzt da ist */ }
    again();
  };
  return b;
}

function pollVerify(jobId) {
  clearTimeout(verifyTimer);
  verifyJobId = jobId;
  verifyStyle = null;
  verifyQuotes = null;
  verifyMissing = null;
  $("#verify-missing").hidden = true;
  renderStyle(null);
  async function tick() {
    let job;
    try {
      job = await (await fetch(`/api/jobs/${jobId}`)).json();
    } catch (e) {
      verifyTimer = setTimeout(tick, 1500);
      return;
    }
    renderVerifyProgress(job);
    renderPaused(job);
    if (job.document) renderDocument(job.document);
    renderVerify(job.results || [], job.document ? T("Prüfbericht: {name}", { name: job.document.name }) : T("Prüfbericht"), true);
    if (job.status === "done" || job.status === "cancelled") {
      $("#verify-btn").disabled = false;
      $("#verify-bar").classList.remove("running");
      const c = countVerdicts(job.results || []);
      const parts = VERDICT_ORDER.filter((v) => c[v]).map((v) => `${c[v]} ${STATES[v].label.toLowerCase()}`);
      notify(T("Benthic: Prüfung fertig"), [TN((job.results || []).length, "{n} Quelle", "{n} Quellen"), ...parts].join(" · "));
      verifyStyle = job.style || null;
      renderVerify(job.results || [], job.document ? T("Prüfbericht: {name}", { name: job.document.name }) : T("Prüfbericht"), false);
      renderStyle(verifyStyle);
      loadVerifyHealth();
      loadHistory();
      loadProjects();
      if (document.querySelector("#view-overview.active")) loadOverview();
      return;
    }
    verifyTimer = setTimeout(tick, 600);
  }
  tick();
}

const VERIFY_STEPS = {
  verify_start: () => T("startet …"),
  lookup: (a) => T("frage <b>{source}</b> …", { source: esc({ openalex: "OpenAlex", europepmc: "Europe PMC" }[a.source] || a.source || "") }),
  search: () => T("sucht nach dem <b>Titel</b> …"),
};

function renderVerifyProgress(job) {
  const total = job.total || 0;
  const done = job.done || 0;
  const pct = total ? Math.round((done / total) * 100) : 0;
  $("#verify-fill").style.width = pct + "%";
  $("#verify-bar").setAttribute("aria-valuenow", String(pct));
  $("#verify-count").textContent = `${done} / ${total} · ${pct} %`;
  const key = job.status === "cancelled" ? "cancelled" : (job.status === "done" ? "done" : "running");
  const badge = $("#verify-status");
  badge.innerHTML = "";
  badge.appendChild(chip(key));
  const end = job.finished_at || Date.now() / 1000;
  const elapsed = job.created_at ? Math.max(0, end - job.created_at) : 0;
  $("#verify-eta").textContent = T("Laufzeit {time}", { time: fmtDur(elapsed) });
  verifyDive.update(job);
  dockBadge(job.status === "running" ? Math.max(0, total - done) || "…" : "");

  const box = $("#verify-live");
  box.innerHTML = "";
  Object.entries(job.active || {}).slice(0, 6).forEach(([ident, a]) => {
    const row = document.createElement("div");
    row.className = "live-row";
    const id = document.createElement("span");
    id.className = "live-id";
    id.textContent = ident;
    id.title = ident;
    const step = document.createElement("span");
    step.className = "live-step legal";
    step.appendChild(icon("i-activity"));
    const t = document.createElement("span");
    t.innerHTML = (VERIFY_STEPS[a.phase] || (() => ""))(a);
    step.appendChild(t);
    row.append(id, step);
    box.appendChild(row);
  });
}

function countVerdicts(results) {
  const c = {};
  results.forEach((r) => { c[r.verdict] = (c[r.verdict] || 0) + 1; });
  return c;
}

function renderVerify(results, title, animate) {
  verifyResults = (results || []).slice().sort((a, b) => (a.order || 0) - (b.order || 0));
  if (animate) $("#verify-health").hidden = true;
  $("#verify-results").hidden = false;
  $("#verify-title").textContent = title || T("Prüfbericht");
  const body = $("#verify-body");
  body.innerHTML = "";
  verifyResults.forEach((res) => {
    const tr = verifyRow(res);
    const key = `${res.order}:${res.identifier}`;
    if (animate && !shownRows.has(key)) tr.classList.add("reveal");
    shownRows.add(key);
    body.appendChild(tr);
  });

  const c = countVerdicts(verifyResults);
  const stats = $("#verify-stats");
  stats.innerHTML = "";
  VERDICT_ORDER.forEach((v) => { if (c[v]) stats.appendChild(chip(v, `${c[v]} ${STATES[v].label}`)); });

  // Vertrauensbalken: Anteil je Urteil.
  const trust = $("#verify-trust");
  const total = verifyResults.length || 1;
  if (!trust.children.length) VERDICT_ORDER.forEach((v) => {
    const seg = document.createElement("span");
    seg.className = v;
    trust.appendChild(seg);
  });
  [...trust.children].forEach((seg) => {
    const n = c[seg.className] || 0;
    seg.style.width = `${(n / total) * 100}%`;
    seg.title = n ? `${n} ${STATES[seg.className].label}` : "";
  });

  const loadable = verifyResults.filter((r) => LOADABLE.has(r.verdict) && r.doi);
  const btn = $("#verify-fetch-btn");
  btn.disabled = !loadable.length;
  $("#verify-zotero-btn").disabled = !loadable.length;
  btn.querySelector("span").textContent = loadable.length
    ? TN(loadable.length, "{n} bestätigte laden", "{n} bestätigte laden") : T("Nichts zu laden");
}

function verifyRow(res) {
  const tr = document.createElement("tr");
  tr.dataset.doi = String(res.doi || (res.found || {}).doi || "").toLowerCase();
  const st = document.createElement("td");
  st.className = "c-state";
  st.appendChild(chip(res.verdict || "uncertain"));

  const main = document.createElement("td");
  const claimed = res.claimed || {};
  const found = res.found || {};
  const title = document.createElement("div");
  title.className = "res-title";
  title.textContent = claimed.title || found.title || res.identifier || T("Ohne Titel");
  main.appendChild(title);
  const metaBits = [];
  if (!claimed.title && res.input) metaBits.push(midTrunc(res.input.replace(/^\s*\d{1,3}[.)]\s+/, ""), 90));
  if ((claimed.authors || []).length) metaBits.push(claimed.authors.slice(0, 3).join(", ") + (claimed.authors.length > 3 ? T(" u. a.") : ""));
  if (claimed.year) metaBits.push(claimed.year);
  if (metaBits.length) {
    const m = document.createElement("div");
    m.className = "claim-meta";
    m.textContent = T("Angabe: {details}", { details: metaBits.join(" · ") });
    main.appendChild(m);
  }

  // Was es wirklich gibt, wenn es von der Angabe abweicht.
  const differs = found.title && claimed.title && ["corrected", "mismatch", "uncertain", "partial", "retracted"].includes(res.verdict);
  if (differs) {
    const f = document.createElement("div");
    f.className = "found-line";
    const k = document.createElement("span");
    k.className = "k";
    k.textContent = T("Gefunden:");
    const v = document.createElement("span");
    v.textContent = [found.title, found.year, found.venue].filter(Boolean).join(" · ");
    f.append(k, v);
    main.appendChild(f);
  }

  const meta = document.createElement("div");
  meta.className = "res-meta";
  if (res.doi) {
    const a = document.createElement("a");
    a.className = "mono";
    a.href = "https://doi.org/" + res.doi;
    a.target = "_blank";
    a.rel = "noopener";
    a.textContent = midTrunc(res.doi, 44);
    a.title = res.doi;
    meta.appendChild(a);
  }
  const via = { title: T("über Titel"), arxiv: T("über arXiv"), pmid: T("über PMID") }[res.resolved_by];
  if (via) {
    const t = document.createElement("span");
    t.className = "via";
    t.textContent = via;
    meta.appendChild(t);
  }
  if (found.cited_by) {
    const c = document.createElement("span");
    c.textContent = T("{n}× zitiert", { n: fmtNum(found.cited_by) });
    meta.appendChild(c);
  }
  if (meta.children.length) main.appendChild(meta);
  if (res.note) {
    const n = document.createElement("div");
    const sev = ["not_found", "mismatch", "retracted"].includes(res.verdict) || (res.hijacked || []).length ? " fail"
      : (["partial", "uncertain"].includes(res.verdict) ? " warn" : "");
    n.className = "res-note" + sev;
    n.textContent = res.note;
    main.appendChild(n);
  }
  if (res.published_version) main.appendChild(publishedLine(res.published_version));
  const trace = traceLine(res);
  if (trace) main.appendChild(trace);
  const styleIssues = verifyStyle && verifyStyle.applicable ? (verifyStyle.entries || {})[String(res.order || 0)] : null;
  if (styleIssues && styleIssues.length) main.appendChild(styleNotes(res, styleIssues));

  const sc = document.createElement("td");
  sc.className = "c-score";
  const score = Math.max(0, Math.min(100, Number(res.score) || 0));
  const s = document.createElement("div");
  s.className = "score" + (score >= 85 ? "" : (score >= 60 ? " mid" : " low"));
  s.title = T("Übereinstimmung von Titel, Jahr und Autor:innen");
  const num = document.createElement("span");
  num.textContent = res.verdict === "not_found" ? "–" : `${score} %`;
  const bar = document.createElement("span");
  bar.className = "bar";
  const fill = document.createElement("i");
  fill.style.width = (res.verdict === "not_found" ? 0 : score) + "%";
  bar.appendChild(fill);
  s.append(num, bar);
  sc.appendChild(s);

  const act = document.createElement("td");
  act.className = "c-act";
  if (res.doi && res.verdict !== "not_found") {
    const a = document.createElement("a");
    a.href = "https://doi.org/" + res.doi;
    a.target = "_blank";
    a.rel = "noopener";
    a.className = "open-link";
    a.title = T("Im Browser öffnen");
    a.setAttribute("aria-label", T("Im Browser öffnen"));
    a.appendChild(icon("i-blocked"));
    act.appendChild(a);
  }
  if (res.doi && LOADABLE.has(res.verdict)) {
    const claims = verifyDoc ? (verifyDoc.claims || []).filter((c) => c.entry === (res.order || 0)) : [];
    act.prepend(supportButton(tr, res.doi, claims.map((c) => c.sentence)));
  }
  tr.append(st, main, sc, act);
  return tr;
}

// ---------------------------------------------------------------------------
// Belegstellen: welche Passage im geladenen Volltext passt zu dem Satz, der die Quelle zitiert?
// Benthic vergleicht Wörter, nicht Bedeutung; das Urteil bleibt beim Lesen.
// ---------------------------------------------------------------------------
const SUPPORT_LEVEL = {
  close: () => T("Passage mit fast allen tragenden Wörtern gefunden"),
  partial: () => T("Passage mit einem Teil der tragenden Wörter gefunden"),
  weak: () => T("Keine Passage kommt dem Satz nahe. Vielleicht steht es so nicht in der Quelle."),
  none: () => T("Keine Passage teilt die tragenden Wörter. Vielleicht steht es so nicht in der Quelle."),
};

function supportButton(tr, doi, sentences) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "btn ghost icon support-btn";
  b.title = T("Belegstelle im Volltext suchen");
  b.setAttribute("aria-label", T("Belegstelle im Volltext suchen"));
  b.appendChild(icon("i-quote"));
  b.onclick = () => {
    const open = tr.nextElementSibling && tr.nextElementSibling.classList.contains("support-row");
    if (open) { tr.nextElementSibling.remove(); b.classList.remove("on"); return; }
    b.classList.add("on");
    tr.after(supportRow(tr.children.length, doi, sentences));
    tr.nextElementSibling.querySelector("textarea").focus();
  };
  return b;
}

function supportRow(span, doi, sentences) {
  const row = document.createElement("tr");
  row.className = "support-row";
  const td = document.createElement("td");
  td.colSpan = span;
  const box = document.createElement("div");
  box.className = "support";
  const label = document.createElement("label");
  label.textContent = T("Satz, der diese Quelle zitiert");
  const ta = document.createElement("textarea");
  ta.rows = 2;
  ta.placeholder = T("z. B. Tiefere Netze ohne Residualverbindungen haben einen höheren Trainingsfehler.");
  ta.value = sentences[0] || "";
  label.appendChild(ta);
  box.appendChild(label);
  if (sentences.length > 1) {
    const pick = document.createElement("div");
    pick.className = "support-pick";
    const k = document.createElement("span");
    k.className = "hint";
    k.textContent = TN(sentences.length, "{n} Stelle in deiner Arbeit:", "{n} Stellen in deiner Arbeit:");
    pick.appendChild(k);
    sentences.slice(0, 8).forEach((sentence, i) => {
      const c = document.createElement("button");
      c.type = "button";
      c.className = "btn small ghost" + (i ? "" : " on");
      c.textContent = String(i + 1);
      c.title = sentence;
      c.onclick = () => {
        ta.value = sentence;
        pick.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === c));
        run();
      };
      pick.appendChild(c);
    });
    box.appendChild(pick);
  }
  const bar = document.createElement("div");
  bar.className = "support-bar";
  const go = document.createElement("button");
  go.type = "button";
  go.className = "btn small";
  go.append(icon("i-search"), document.createTextNode(T("Im Volltext suchen")));
  const note = document.createElement("span");
  note.className = "hint";
  note.textContent = T("Benthic vergleicht Wörter, nicht Bedeutung, am besten in derselben Sprache wie die Quelle. Ob die Passage den Satz stützt, entscheidest du beim Lesen.");
  bar.append(go, note);
  box.appendChild(bar);
  const out = document.createElement("div");
  out.className = "support-out";
  box.appendChild(out);
  td.appendChild(box);
  row.appendChild(td);

  async function run(translated) {
    const claim = ta.value.trim();
    if (!claim) { ta.focus(); return; }
    go.disabled = true;
    out.innerHTML = "";
    out.appendChild(spinnerLine(translated ? T("Sucht mit der Übersetzung …") : T("Suche im Volltext …")));
    let r = null;
    let j = {};
    try {
      r = await fetch("/api/support", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ doi, claim, translated: translated || null }),
      });
      j = await r.json();
    } catch (e) { /* unten */ }
    go.disabled = false;
    out.innerHTML = "";
    if (!r || !r.ok) { out.appendChild(supportNote(j.error || T("Suche fehlgeschlagen"), "fail")); return; }
    if (!j.available) {
      out.appendChild(supportNote(j.error, "warn"));
      if (!j.unreadable) out.appendChild(fetchForSupport(doi, () => run()));
      return;
    }
    if (j.needs_translation && !translated) {
      // Satz und Quelle in verschiedenen Sprachen: auf dem Mac übersetzen und erneut suchen
      out.appendChild(spinnerLine(T("Satz und Quelle haben verschiedene Sprachen. Übersetzt den Satz auf dem Mac …")));
      const t = await translateText(claim, j.claim_language, j.source_language);
      if (t.text) { run(t.text); return; }
      out.innerHTML = "";
      out.appendChild(supportNote(t.error === "unsupported"
        ? T("Satz und Quelle haben verschiedene Sprachen. Ab macOS 15 übersetzt Benthic den Satz vorher; sonst hilft es, ihn in der Sprache der Quelle einzugeben.")
        : T("Satz und Quelle haben verschiedene Sprachen, und die Übersetzung auf dem Mac war nicht möglich. Gib den Satz in der Sprache der Quelle ein."), "warn"));
    }
    if (j.translated) {
      const tn = document.createElement("p");
      tn.className = "hint support-translated";
      tn.textContent = T("Gesucht mit der Übersetzung von macOS: „{text}“", { text: j.translated });
      out.appendChild(tn);
    }
    const best = (j.passages || [])[0];
    out.appendChild(supportNote(SUPPORT_LEVEL[j.level](), j.level === "close" ? "ok" : (j.level === "partial" ? "" : "warn")));
    const terms = (best && best.terms) || [];
    (j.passages || []).forEach((p) => {
      const q = document.createElement("blockquote");
      q.className = "support-passage";
      const h = document.createElement("div");
      h.className = "support-meta";
      h.textContent = [T("S. {page}", { page: p.page }), T("{n} % der tragenden Wörter", { n: Math.round(p.coverage * 100) }), j.file]
        .filter(Boolean).join(" · ");
      const t = document.createElement("p");
      highlightTerms(t, p.text, p.terms || terms);
      const c = document.createElement("button");
      c.type = "button";
      c.className = "btn small ghost";
      c.textContent = T("Kopieren");
      c.onclick = () => copyValue(`${p.text} (${T("S. {page}", { page: p.page })})`);
      h.appendChild(c);
      q.append(h, t);
      out.appendChild(q);
    });
  }
  go.onclick = () => run();
  ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) run(); });
  if (ta.value) setTimeout(() => run(), 0);
  return row;
}

// Übersetzung auf dem Mac (TranslateBridge in der App, ab macOS 15)
const translateWaiters = {};
let translateSeq = 0;
window.benthicTranslated = ({ id, text, error }) => {
  const done = translateWaiters[id];
  delete translateWaiters[id];
  if (done) done({ text, error });
};

function translateText(text, source, target) {
  const bridge = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.benthicTranslate;
  if (!bridge || !target) return Promise.resolve({ error: "unsupported" });
  const id = "t" + (++translateSeq);
  return new Promise((done) => {
    translateWaiters[id] = done;
    bridge.postMessage({ id, text, source: source || null, target });
    setTimeout(() => { if (translateWaiters[id]) { delete translateWaiters[id]; done({ error: "timeout" }); } }, 180000);
  });
}

function supportNote(text, cls) {
  const p = document.createElement("p");
  p.className = "support-note" + (cls ? " " + cls : "");
  p.append(icon(cls === "ok" ? "i-legal" : (cls === "fail" ? "i-fail" : (cls === "warn" ? "i-warn" : "i-search"))), document.createTextNode(text));
  return p;
}

function spinnerLine(text) {
  const p = document.createElement("p");
  p.className = "hint support-wait";
  p.textContent = text;
  return p;
}

// PDF für die Suche laden (nur die eine DOI), danach erneut suchen.
function fetchForSupport(doi, again) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "btn small";
  b.append(icon("i-download"), document.createTextNode(T("PDF laden und suchen")));
  b.onclick = async () => {
    b.disabled = true;
    b.lastChild.textContent = T("Lädt …");
    try {
      const r = await fetch("/api/download", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: doi }),
      });
      const j = await r.json();
      if (!r.ok) throw new Error(j.error || "");
      if (seenJobs) seenJobs.add(String(j.job_id));
      for (let i = 0; i < 240; i++) {
        await new Promise((ok) => setTimeout(ok, 1000));
        const job = await (await fetch(`/api/jobs/${j.job_id}`)).json();
        if (job.status !== "running") break;
      }
    } catch (e) { /* die erneute Suche meldet, ob die PDF jetzt da ist */ }
    again();
  };
  return b;
}

// Wörter der Passage hervorheben, deren Stamm in der Aussage vorkommt.
function highlightTerms(el, text, terms) {
  const fold = (w) => w.normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
  const stems = (terms || []).filter((t) => t.length >= 3 || /^\d/.test(t));
  text.split(/(\s+)/).forEach((part) => {
    const w = fold(part.replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}%]+$/gu, ""));
    if (w && stems.some((t) => (/^\d/.test(t) ? w === t || w === t + "%" : w.startsWith(t)))) {
      const m = document.createElement("mark");
      m.textContent = part;
      el.appendChild(m);
    } else {
      el.appendChild(document.createTextNode(part));
    }
  });
}

// ---------------------------------------------------------------------------
// Oft zitiert von deinen Quellen, aber nicht in deiner Liste (related.py)
// ---------------------------------------------------------------------------
function renderMissingWorks(box, payload, saved, projectId) {
  box.innerHTML = "";
  box.hidden = false;
  const head = document.createElement("div");
  head.className = "mw-head";
  const h = document.createElement("h3");
  h.textContent = T("Oft zitiert von deinen Quellen");
  const lead = document.createElement("span");
  lead.className = "hint";
  lead.textContent = T("Werke, die deine bestätigten Quellen immer wieder zitieren und die in deiner Liste fehlen.");
  const run = document.createElement("button");
  run.type = "button";
  run.className = "btn small";
  run.append(icon("i-search"), document.createTextNode(saved ? T("Erneut suchen") : T("Suchen")));
  head.append(icon("i-chart"), h, lead, run);
  box.appendChild(head);
  const out = document.createElement("div");
  box.appendChild(out);
  const show = (j) => {
    out.innerHTML = "";
    const list = j.suggestions || [];
    const meta = document.createElement("p");
    meta.className = "hint mw-meta";
    meta.textContent = list.length
      ? T("Aus den Literaturverzeichnissen von {n} deiner Quellen (Quelle: {src}).", { n: j.sources, src: j.source === "crossref" ? "Crossref" : "OpenAlex" })
      : (j.sources < 2 ? T("Zu wenige deiner Quellen haben ein lesbares Literaturverzeichnis.") : T("Kein Werk wird von mehreren deiner Quellen zitiert, das dir fehlt."));
    out.appendChild(meta);
    if (!list.length) return;
    const ul = document.createElement("ul");
    ul.className = "mw-list";
    list.forEach((w) => {
      const li = document.createElement("li");
      const c = document.createElement("span");
      c.className = "mw-count";
      c.textContent = `${w.count}/${j.sources}`;
      c.title = T("Von {n} deiner {of} Quellen zitiert", { n: w.count, of: j.sources });
      const main = document.createElement("div");
      const t = document.createElement("div");
      t.className = "res-title";
      t.textContent = w.title;
      const m = document.createElement("div");
      m.className = "res-meta";
      const bits = [(w.authors || []).join(", ") + ((w.author_count || 0) > 3 ? T(" u. a.") : ""), w.year, w.venue,
        w.cited_by ? T("{n}× zitiert", { n: fmtNum(w.cited_by) }) : null].filter(Boolean);
      m.textContent = bits.join(" · ");
      main.append(t, m);
      const act = document.createElement("div");
      act.className = "mw-act";
      if (w.doi) {
        const a = document.createElement("a");
        a.className = "mono";
        a.href = "https://doi.org/" + w.doi;
        a.target = "_blank";
        a.rel = "noopener";
        a.textContent = midTrunc(w.doi, 30);
        act.appendChild(a);
        if (projectId) {
          const add = document.createElement("button");
          add.type = "button";
          add.className = "btn small ghost";
          add.append(icon("i-plus"), document.createTextNode(T("Ins Projekt")));
          add.onclick = async () => {
            add.disabled = true;
            await fetch(`/api/projects/${encodeURIComponent(projectId)}/items`, { method: "POST",
              headers: { "Content-Type": "application/json" }, body: JSON.stringify({ dois: [w.doi] }) });
            add.lastChild.textContent = T("Hinzugefügt");
          };
          act.appendChild(add);
        }
      }
      li.append(c, main, act);
      ul.appendChild(li);
    });
    out.appendChild(ul);
  };
  run.onclick = async () => {
    run.disabled = true;
    out.innerHTML = "";
    out.appendChild(spinnerLine(T("Liest die Literaturverzeichnisse deiner Quellen …")));
    let r = null;
    let j = {};
    try {
      r = await fetch("/api/missing-works", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      j = await r.json();
    } catch (e) { /* unten */ }
    run.disabled = false;
    if (!r || !r.ok) { out.innerHTML = ""; out.appendChild(supportNote(j.error || T("Suche fehlgeschlagen"), "warn")); return; }
    show(j);
  };
  if (saved) show(saved);
}

// Preprint, inzwischen veröffentlicht: Hinweis mit der Zeitschriftenfassung und ihrer Angabe zum Kopieren
function publishedLine(pub) {
  const box = document.createElement("div");
  box.className = "published-line";
  const k = document.createElement("span");
  k.className = "k";
  k.append(icon("i-book"), document.createTextNode(T("Inzwischen veröffentlicht:")));
  const v = document.createElement("span");
  v.textContent = [pub.venue, pub.year].filter(Boolean).join(", ") || pub.title;
  const a = document.createElement("a");
  a.className = "mono";
  a.href = "https://doi.org/" + pub.doi;
  a.target = "_blank";
  a.rel = "noopener";
  a.textContent = midTrunc(pub.doi, 40);
  const b = document.createElement("button");
  b.type = "button";
  b.className = "btn small ghost";
  b.textContent = T("Angabe kopieren");
  b.onclick = async () => {
    b.disabled = true;
    try {
      const style = (verifyStyle && verifyStyle.style) || "apa";
      const j = await (await fetch(`/api/format?doi=${encodeURIComponent(pub.doi)}&style=${style}`)).json();
      if (j.text) { copyValue(j.text); toast(T("Angabe kopiert"), j.text.slice(0, 120)); }
      else toast(T("Keine Angabe erhalten"), j.error || "");
    } catch (e) { /* offline */ }
    b.disabled = false;
  };
  const hint = document.createElement("span");
  hint.className = "hint";
  hint.textContent = T("Zitiere besser diese Fassung als den {server}-Preprint.", { server: pub.server || "Preprint" });
  box.append(k, v, a, b, hint);
  return box;
}

// Prüfbericht als PDF: in der App über macOS (ReportBridge), im Browser über den Druckdialog
function saveReport(jobId, name) {
  saveReportPath(encodeURIComponent(jobId), name || T("Prüfbericht #{id}", { id: jobId }));
}

function saveReportPath(path, name) {
  const base = String(name || "").replace(/\.[a-z]{2,4}$/i, "").replace(/[^\w\-. ]+/g, "-").trim();
  const url = `${location.origin}/report/${path}`;
  const bridge = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.benthicReport;
  if (bridge) bridge.postMessage({ url, name: `${T("Benthic-Bericht")} ${base}.pdf` });
  else window.open(`${url}?print=1`, "_blank", "noopener");
}

const nativeReveal = !!(window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.benthicReveal);

function loadableDois() {
  return verifyResults.filter((r) => LOADABLE.has(r.verdict) && r.doi).map((r) => r.doi);
}

function fetchVerified() {
  const dois = loadableDois();
  if (!dois.length) return;
  $("#doi-input").value = dois.join("\n");
  updateCounter();
  showView("download");
  startJob("download");
}

async function copyDois() {
  const text = loadableDois().join("\n");
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
  } catch (e) {
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }
  toast(T("DOIs kopiert"), TN(text.split("\n").length, "{n} bestätigte DOI in der Zwischenablage", "{n} bestätigte DOIs in der Zwischenablage"));
}

async function exportRows(rows, fmt, name) {
  if (!rows.length) return;
  const r = await fetch("/api/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ format: fmt, results: rows }),
  });
  if (!r.ok) return;
  const url = URL.createObjectURL(await r.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = `${name}.${fmt}`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// ---------------------------------------------------------------------------
// Ersteinrichtung
// ---------------------------------------------------------------------------
const nativePick = !!(window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.benthicPick);
const pickWaiters = {};
let pickSeq = 0;
window.benthicPicked = ({ id, path }) => {
  const done = pickWaiters[id];
  delete pickWaiters[id];
  if (done) done(path || null);
};

function pickPath(kind, types, start) {
  if (!nativePick) return Promise.resolve(null);
  const id = "p" + (++pickSeq);
  return new Promise((resolve) => {
    pickWaiters[id] = resolve;
    window.webkit.messageHandlers.benthicPick.postMessage({ id, kind, types: types || [], start: start || "" });
  });
}

const setup = { step: 0, choice: "empty", path: "", suggestions: { sources: [] }, sample: false };

async function openSetup(force) {
  let j;
  try { j = await (await fetch("/api/setup")).json(); } catch (e) { return; }
  if (!force && !j.needed) return;
  const cur = j.current || {};
  setup.suggestions = j.suggestions || { sources: [] };
  $("#su-email").value = /example\.(org|com)$/.test(cur.unpaywall_email || "") ? "" : (cur.unpaywall_email || "");
  $("#su-institution").value = cur.institution || "";
  $("#su-proxy").value = cur.proxy_prefix || "";
  $("#su-folder").value = cur.download_dir || setup.suggestions.download_dir || "";
  const existing = (cur.library_sources || [])[0];
  const detected = setup.suggestions.sources || [];
  if (existing) {
    const hit = detected.find((s) => s.path === existing);
    setup.choice = hit ? `src:${detected.indexOf(hit)}` : "file";
    setup.path = existing;
  } else {
    setup.choice = detected.length ? "src:0" : "empty";
    setup.path = "";
  }
  $("#su-file").value = setup.choice === "file" ? setup.path : "";
  ["#su-folder-pick", "#su-file-pick"].forEach((sel) => { $(sel).hidden = !nativePick; });
  renderChoices();
  renderLangChoice();
  $("#setup").hidden = false;
  goStep(0);  // nach einem Sprachwechsel (force) wieder bei der Sprachwahl, jetzt in der neuen Sprache
}

// ---------------------------------------------------------------------------
// Sprache: Auswahl in der Einrichtung und in den Einstellungen. Ein Wechsel
// speichert die Einstellung und lädt die Oberfläche in der neuen Sprache neu.
// ---------------------------------------------------------------------------
function renderLangChoice() {
  document.querySelectorAll("#su-lang [data-lang], #set-lang [data-lang]").forEach((b) => {
    b.setAttribute("aria-checked", String(b.dataset.lang === LANG));
  });
}

async function switchLanguage(lang, resumeSetup) {
  if (lang === LANG) return renderLangChoice();
  document.querySelectorAll("#su-lang [data-lang], #set-lang [data-lang]").forEach((b) => {
    b.setAttribute("aria-checked", String(b.dataset.lang === lang));
  });
  try {
    await postSettings({ language: lang });
  } catch (e) {
    toast(T("Einstellung nicht gespeichert"), T("Bitte erneut versuchen."));
    return renderLangChoice();
  }
  try { if (resumeSetup) sessionStorage.setItem("pd-setup-resume", "1"); } catch (e) { /* ohne Speicher */ }
  location.reload();
}

function tellShellLanguage() {
  try { window.webkit.messageHandlers.benthicLang.postMessage({ lang: LANG }); } catch (e) { /* Browser */ }
}

function renderChoices() {
  const box = $("#su-choices");
  box.innerHTML = "";
  const opts = [{ key: "empty", icon: "i-library", title: T("Leer starten"), sub: T("Die Bibliothek wächst mit jedem Download.") }];
  (setup.suggestions.sources || []).forEach((s, i) => opts.push({
    key: `src:${i}`, icon: s.kind === "zotero" ? "i-book" : "i-file", title: T("{label} übernehmen", { label: s.label }), sub: s.path,
  }));
  opts.push({ key: "file", icon: "i-upload", title: T("Andere Datei"), sub: T("BibTeX, RIS, Markdown, Text oder Zotero-Datenbank") });
  opts.forEach((o) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "choice";
    b.setAttribute("role", "radio");
    b.setAttribute("aria-checked", String(setup.choice === o.key));
    const t = document.createElement("span");
    t.className = "t";
    const strong = document.createElement("strong");
    strong.textContent = o.title;
    const sub = document.createElement("span");
    sub.textContent = o.sub;
    sub.title = o.sub;
    t.append(strong, sub);
    b.append(icon(o.icon), t);
    b.onclick = () => { setup.choice = o.key; renderChoices(); };
    box.appendChild(b);
  });
  $("#su-file-row").hidden = setup.choice !== "file";
}

// Schritte: 0 Sprache, 1 Willkommen, 2 Über dich, 3 Ablage, 4 Bibliothek, 5 Fertig.
const SETUP_NEXT = [T("Weiter"), T("Los geht’s"), T("Weiter"), T("Weiter"), T("Einrichten"), T("Benthic starten")];
const SETUP_DONE = SETUP_NEXT.length - 1;

function goStep(n, back = false) {
  setup.step = n;
  document.querySelectorAll(".setup-step").forEach((s) => {
    const on = Number(s.dataset.step) === n;
    s.classList.toggle("active", on);
    s.classList.toggle("back", on && back);
  });
  document.querySelectorAll("#setup-dots li").forEach((d, i) => d.classList.toggle("on", i <= n));
  $("#su-back").hidden = n === 0 || n === SETUP_DONE;
  $("#su-sample").hidden = n !== SETUP_DONE;
  $("#su-next").textContent = SETUP_NEXT[n];
  $("#su-next").disabled = false;
  $("#su-error").textContent = "";
  const first = document.querySelector(`.setup-step[data-step="${n}"] input, .setup-step[data-step="${n}"] [aria-checked="true"]`);
  if (first) setTimeout(() => first.focus(), 60);
}

function setupFail(msg, field) {
  $("#su-error").textContent = msg;
  const map = { email: ["#su-email", 2], proxy: ["#su-proxy", 2], folder: ["#su-folder", 3], library: ["#su-file", 4] };
  const hit = map[field];
  if (hit) {
    if (setup.step !== hit[1]) goStep(hit[1], true);
    $("#su-error").textContent = msg;
    $(hit[0]).classList.add("invalid");
    $(hit[0]).focus();
  }
}

async function setupNext() {
  document.querySelectorAll(".setup-form input").forEach((i) => i.classList.remove("invalid"));
  $("#su-error").textContent = "";
  const step = setup.step;
  if (step === 2) {
    const email = $("#su-email").value.trim();
    if (email && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) return setupFail(T("Die E-Mail-Adresse sieht unvollständig aus."), "email");
    const proxy = $("#su-proxy").value.trim();
    if (proxy && !/^https?:\/\//.test(proxy)) return setupFail(T("Der Proxy-Link muss mit https:// beginnen."), "proxy");
  }
  if (step === 3 && !/^(\/|~)/.test($("#su-folder").value.trim())) {
    return setupFail(T("Bitte einen vollständigen Ordnerpfad angeben, z. B. ~/Documents/Benthic."), "folder");
  }
  if (step === 4) return submitSetup();
  if (step === SETUP_DONE) {
    $("#setup").hidden = true;
    loadSettings();
    loadLibrary();
    loadFiles();
    showView("verify");
    if (setup.sample) {
      $("#verify-input").value = SAMPLE;
      runPreview();
    }
    return;
  }
  goStep(step + 1);
}

async function submitSetup() {
  let library = { kind: "empty" };
  if (setup.choice.startsWith("src:")) {
    const s = setup.suggestions.sources[Number(setup.choice.slice(4))];
    library = { kind: s.kind, path: s.path };
  } else if (setup.choice === "file") {
    const path = $("#su-file").value.trim();
    if (!path) return setupFail(T("Bitte eine Datei auswählen."), "library");
    library = { kind: "file", path };
  }
  const body = {
    email: $("#su-email").value.trim(),
    institution: $("#su-institution").value.trim(),
    proxy_prefix: $("#su-proxy").value.trim(),
    download_dir: $("#su-folder").value.trim(),
    library,
  };
  $("#su-next").disabled = true;
  $("#su-next").textContent = T("Richte ein …");
  const r = await fetch("/api/setup", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) {
    $("#su-next").disabled = false;
    $("#su-next").textContent = SETUP_NEXT[4];
    return setupFail(j.error || T("Einrichtung fehlgeschlagen."), j.field);
  }
  const s = j.settings || {};
  settings = s;
  const recap = $("#su-recap");
  recap.innerHTML = "";
  const none = T("nicht angegeben");
  const rows = [
    [T("Sprache"), LANG === "de" ? "Deutsch" : "English"],
    [T("E-Mail"), s.unpaywall_email || none],
    [T("Hochschule"), s.institution || none],
    ["Proxy", s.proxy_prefix ? T("eingerichtet") : T("keiner")],
    [T("Zielordner"), s.download_dir],
    [T("Bibliothek"), j.imported ? TN(j.imported.found, "{n} DOI übernommen", "{n} DOIs übernommen") : T("leer")],
    ...(ROUTES.length ? [[T("Zusatzwege"), s.extra_routes ? T("an") : T("aus")]] : []),
  ];
  rows.forEach(([k, v]) => {
    const li = document.createElement("li");
    const a = document.createElement("span");
    a.textContent = k;
    const b = document.createElement("span");
    b.textContent = v;
    li.append(a, b);
    recap.appendChild(li);
  });
  $("#su-summary").textContent = T("Füge jetzt eine Literaturliste ein, zum Beispiel aus ChatGPT oder Claude, und lass sie prüfen.");
  goStep(SETUP_DONE);
}

// ---------------------------------------------------------------------------
// Projekte
// ---------------------------------------------------------------------------
let projectList = [];
let activeProject = null;
let shownProject = null;

// ---------------------------------------------------------------------------
// Rückzugswache: die Quellen aller Projekte einmal am Tag neu prüfen (watch.py)
// ---------------------------------------------------------------------------
const WATCH_KIND = {
  retraction: ["fail", "i-fail", () => T("Zurückgezogen")],
  withdrawal: ["fail", "i-fail", () => T("Entfernt")],
  concern: ["warn", "i-warn", () => T("Bedenken geäußert")],
  correction: ["", "i-fix", () => T("Korrigiert")],
};
let watchState = null;
let watchNotified = null;

function watchTag(kind, date) {
  const [cls, ic, label] = WATCH_KIND[kind] || WATCH_KIND.correction;
  const t = document.createElement("span");
  t.className = "watch-tag" + (cls ? " " + cls : "");
  t.append(icon(ic), document.createTextNode(label() + (date ? " " + date : "")));
  return t;
}

async function loadWatch(markSeen = false) {
  let st;
  try { st = await (await fetch("/api/watch")).json(); } catch (e) { return null; }
  watchState = st;
  const badge = $("#nav-watch");
  badge.hidden = !st.unseen;
  badge.textContent = st.unseen > 9 ? "9+" : String(st.unseen || "");
  badge.title = TN(st.unseen, "{n} neuer Fund der Rückzugswache", "{n} neue Funde der Rückzugswache");
  if (watchNotified === null) watchNotified = st.unseen;
  else if (st.unseen > watchNotified) {
    const fresh = (st.findings || []).filter((f) => !f.seen);
    const worst = fresh.find((f) => f.kind === "retraction" || f.kind === "withdrawal") || fresh[0];
    if (worst) notify(T("Benthic: Rückzugswache"), `${WATCH_KIND[worst.kind][2]()}: ${worst.title || worst.doi}` +
      (fresh.length > 1 ? " " + T("und {n} weitere", { n: fresh.length - 1 }) : ""));
    watchNotified = st.unseen;
  }
  if (document.querySelector("#view-projects.active")) {
    renderWatch(st);
    if (markSeen && st.unseen) {
      fetch("/api/watch/seen", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })
        .then(() => { badge.hidden = true; watchNotified = 0; }).catch(() => {});
    }
  }
  return st;
}

function renderWatch(st) {
  const box = $("#watch-panel");
  box.innerHTML = "";
  box.hidden = !st || (!st.watched && !(st.findings || []).length);
  if (box.hidden) return;
  const head = document.createElement("div");
  head.className = "wp-head";
  const h = document.createElement("h3");
  h.textContent = T("Rückzugswache");
  const meta = document.createElement("span");
  meta.className = "hint";
  const when = st.last_run ? new Date(st.last_run * 1000).toLocaleString(LOCALE, { dateStyle: "medium", timeStyle: "short" }) : null;
  meta.textContent = [TN(st.watched, "{n} Quelle in deinen Projekten", "{n} Quellen in deinen Projekten"),
    st.running ? T("prüft gerade …") : (when ? T("zuletzt geprüft {when}", { when }) : T("noch nicht geprüft"))].join(" · ");
  const toggle = document.createElement("label");
  toggle.className = "check wp-toggle";
  const cb = document.createElement("input");
  cb.type = "checkbox";
  cb.checked = st.enabled;
  cb.onchange = async () => {
    await fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ retraction_watch: cb.checked }) });
    loadWatch();
  };
  const tl = document.createElement("span");
  tl.textContent = T("Täglich prüfen");
  toggle.append(cb, tl);
  const run = document.createElement("button");
  run.type = "button";
  run.className = "btn small";
  run.disabled = !!st.running || !st.watched;
  run.append(icon("i-retry"), document.createTextNode(T("Jetzt prüfen")));
  run.onclick = runWatch;
  head.append(icon("i-shield"), h, meta, toggle, run);
  box.appendChild(head);

  const findings = st.findings || [];
  if (!findings.length) {
    const ok = document.createElement("p");
    ok.className = "wp-ok";
    ok.append(icon("i-legal"), document.createTextNode(st.last_run
      ? T("Keine Rückzüge, Bedenken oder Korrekturen zu deinen Quellen.")
      : T("Benthic prüft deine Projektquellen einmal am Tag auf Rückzüge, Bedenken und Korrekturen.")));
    box.appendChild(ok);
  } else {
    const ul = document.createElement("ul");
    ul.className = "wp-list";
    findings.slice(0, 40).forEach((f) => {
      const li = document.createElement("li");
      li.className = f.seen ? "" : "new";
      const top = document.createElement("div");
      top.className = "wp-top";
      top.appendChild(watchTag(f.kind, f.date));
      if (!f.seen) {
        const n = document.createElement("span");
        n.className = "wp-new";
        n.textContent = T("Neu");
        top.appendChild(n);
      }
      const t = document.createElement("span");
      t.className = "wp-title";
      t.textContent = f.title || f.doi;
      top.appendChild(t);
      const m = document.createElement("div");
      m.className = "res-meta";
      const a = document.createElement("a");
      a.className = "mono";
      a.href = "https://doi.org/" + f.doi;
      a.target = "_blank";
      a.rel = "noopener";
      a.textContent = midTrunc(f.doi, 44);
      m.appendChild(a);
      if (f.notice_doi && f.notice_doi.toLowerCase() !== String(f.doi).toLowerCase()) {
        const n = document.createElement("a");
        n.href = "https://doi.org/" + f.notice_doi;
        n.target = "_blank";
        n.rel = "noopener";
        n.textContent = T("Mitteilung lesen");
        m.appendChild(n);
      }
      const where = (f.projects || []).map((p) => p.name).join(", ");
      if (where) { const w = document.createElement("span"); w.textContent = where; m.appendChild(w); }
      li.append(top, m);
      ul.appendChild(li);
    });
    box.appendChild(ul);
  }
  const src = document.createElement("p");
  src.className = "hint wp-src";
  src.textContent = T("Quellen: Mitteilungen bei Crossref (mit der Datenbank von Retraction Watch) und OpenAlex. Abgefragt werden nur die DOIs.");
  box.appendChild(src);
}

async function runWatch() {
  try { await fetch("/api/watch/run", { method: "POST" }); } catch (e) { return; }
  for (let i = 0; i < 120; i++) {
    const st = await loadWatch();
    if (!st || !st.running) break;
    await new Promise((ok) => setTimeout(ok, 1500));
  }
  loadWatch(true);
  loadProjects();
}

async function loadProjects() {
  if (document.querySelector("#view-projects.active")) loadWatch(true);
  try {
    const j = await (await fetch("/api/projects")).json();
    projectList = j.projects || [];
    activeProject = j.active || null;
  } catch (e) { return; }
  renderProjectSwitch();
  renderProjectGrid();
  if (shownProject && projectList.some((p) => p.id === shownProject)) showProject(shownProject, false);
  else if (!projectList.length) $("#project-detail").hidden = true;
}

function renderProjectSwitch() {
  const sel = $("#project-select");
  sel.innerHTML = "";
  const none = new Option(T("Kein Projekt"), "");
  sel.appendChild(none);
  projectList.forEach((p) => sel.appendChild(new Option(p.name, p.id)));
  sel.appendChild(new Option(T("Neues Projekt …"), "__new"));
  sel.value = activeProject || "";
  const name = (projectList.find((p) => p.id === activeProject) || {}).name;
  document.querySelectorAll(".project-hint").forEach((el) => {
    el.hidden = !name;
    el.textContent = name ? T("Ergebnisse gehen in „{name}“", { name }) : "";
  });
}

async function setActiveProject(id) {
  const j = await (await fetch("/api/projects/active", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: id || null }),
  })).json();
  activeProject = j.active;
  renderProjectSwitch();
  renderProjectGrid();
}

function renderProjectGrid() {
  const grid = $("#project-grid");
  grid.innerHTML = "";
  if (!projectList.length) {
    grid.innerHTML = `<p class="hint">${T("Noch keine Projekte. Lege eines an, zum Beispiel für dein Referat.")}</p>`;
    return;
  }
  projectList.forEach((p) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "project-card" + (p.id === shownProject ? " selected" : "");
    const name = document.createElement("div");
    name.className = "name";
    const n = document.createElement("span");
    n.textContent = p.name;
    name.append(icon("i-folder"), n);
    if (p.id === activeProject) {
      const a = document.createElement("span");
      a.className = "badge-active";
      a.textContent = T("aktiv");
      name.appendChild(a);
    }
    const desc = document.createElement("div");
    desc.className = "desc";
    desc.textContent = p.description || "";
    const meter = document.createElement("div");
    meter.className = "meter";
    const total = Math.max(1, p.count);
    [["v", p.verified], ["f", p.flagged]].forEach(([cls, v]) => {
      const i = document.createElement("i");
      i.className = cls;
      i.style.width = `${(v / total) * 100}%`;
      meter.appendChild(i);
    });
    const facts = document.createElement("div");
    facts.className = "facts";
    facts.innerHTML = "";
    [TN(p.count, "{n} Quelle", "{n} Quellen"), T("{n} bestätigt", { n: p.verified }), T("{n} markiert", { n: p.flagged }),
      T("{n} in der Bibliothek", { n: p.in_library })]
      .forEach((t) => { const s = document.createElement("span"); s.textContent = t; facts.appendChild(s); });
    b.append(name, desc, meter, facts);
    b.onclick = () => showProject(p.id, true);
    grid.appendChild(b);
  });
}

async function showProject(id, scroll) {
  const r = await fetch(`/api/projects/${encodeURIComponent(id)}`);
  if (!r.ok) return;
  const p = await r.json();
  shownProject = p.id;
  renderProjectGrid();
  $("#project-detail").hidden = false;
  $("#pd-name").textContent = p.name;
  $("#pd-desc").textContent = p.description || "";
  const stats = $("#pd-stats");
  stats.innerHTML = "";
  stats.appendChild(chip("library", TN(p.count, "{n} Quelle", "{n} Quellen")));
  if (p.verified) stats.appendChild(chip("verified", T("{n} bestätigt", { n: p.verified })));
  if (p.flagged) stats.appendChild(chip("not_found", T("{n} markiert", { n: p.flagged })));
  $("#pd-activate").hidden = p.id === activeProject;
  const body = $("#pd-body");
  body.innerHTML = "";
  if (!p.items.length) {
    body.innerHTML = `<tr><td colspan="4" class="hint">${T("Noch leer. Prüfe eine Literaturliste oder lade DOIs, während das Projekt aktiv ist.")}</td></tr>`;
  }
  p.items.forEach((it) => body.appendChild(projectRow(p.id, it)));
  const missing = projectMissing(p);
  $("#pd-fetch").disabled = !missing.length;
  $("#pd-fetch").querySelector("span").textContent = missing.length ? TN(missing.length, "{n} fehlende laden", "{n} fehlende laden") : T("Alles geladen");
  $("#project-detail").dataset.id = p.id;
  if (p.items.length) loadHealth($("#pd-health"), { project: p.id }, body);
  else $("#pd-health").hidden = true;
  if (p.items.length >= 2) renderMissingWorks($("#pd-missing"), { project: p.id }, null, p.id);
  else $("#pd-missing").hidden = true;
  $("#pd-watch-toggle").checked = p.watch !== false;
  $("#pd-recheck").disabled = !p.items.length;
  $("#pd-watch-now").disabled = !p.items.length;
  loadProjectRuns(p.id);
  if (scroll) scrollMainTo($("#project-detail"));
}

// Läufe eines Projekts, mit Bericht und PDF je Lauf
async function loadProjectRuns(pid) {
  let j = {};
  try { j = await (await fetch(`/api/projects/${encodeURIComponent(pid)}/runs`)).json(); } catch (e) { return; }
  const runs = j.runs || [];
  const box = $("#pd-runs");
  const list = $("#pd-runs-list");
  list.innerHTML = "";
  box.hidden = !runs.length;
  const latest = runs.find((r) => r.mode === "verify");
  $("#pd-report").hidden = !latest;
  $("#pd-report").onclick = () => latest && saveReport(latest.id, $("#pd-name").textContent);
  runs.slice(0, 12).forEach((r) => {
    const li = document.createElement("li");
    const when = r.finished_at ? new Date(r.finished_at * 1000).toLocaleString(LOCALE, { dateStyle: "medium", timeStyle: "short" }) : "";
    const bad = (r.counts.not_found || 0) + (r.counts.mismatch || 0) + (r.counts.retracted || 0);
    const t = document.createElement("span");
    t.textContent = [when, runKind(r.mode), r.document, TN(r.total, "{n} Quelle", "{n} Quellen"),
      r.mode === "verify" ? (bad ? TN(bad, "{n} auffällig", "{n} auffällig") : T("nichts Auffälliges")) : null].filter(Boolean).join(" · ");
    const open = document.createElement("button");
    open.type = "button";
    open.className = "btn small ghost";
    open.textContent = T("Ansehen");
    open.onclick = () => viewHistory(r.id);
    li.append(t, open);
    if (r.mode === "verify") {
      const pdf = document.createElement("button");
      pdf.type = "button";
      pdf.className = "btn small ghost";
      pdf.textContent = "PDF";
      pdf.onclick = () => saveReport(r.id, $("#pd-name").textContent);
      li.appendChild(pdf);
    }
    list.appendChild(li);
  });
}

async function recheckProject() {
  if (!shownProject) return;
  const r = await fetch(`/api/projects/${encodeURIComponent(shownProject)}/check`, { method: "POST" });
  const j = await r.json();
  if (!r.ok) { toast(T("Nicht gestartet"), j.error || ""); return; }
  const est = j.estimate || {};
  if (est.openalex_paused || est.enough === false) {
    toast(T("Hinweis zu den Kontingenten"), T("OpenAlex reicht heute nicht für alle Quellen; Benthic prüft mit Crossref und den übrigen Quellen weiter."));
  }
  showView("verify");
  beginVerify(j.job_id);
  toast(T("Projekt wird neu geprüft"), TN(j.total, "{n} Quelle", "{n} Quellen"));
}

async function watchProjectNow() {
  if (!shownProject) return;
  const btn = $("#pd-watch-now");
  btn.disabled = true;
  await fetch("/api/watch/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ project: shownProject }) });
  for (let i = 0; i < 120; i++) {
    await new Promise((ok) => setTimeout(ok, 1500));
    const st = await (await fetch("/api/watch")).json();
    if (!st.running) break;
  }
  btn.disabled = false;
  loadWatch(true);
  showProject(shownProject, false);
  toast(T("Rückzüge geprüft"), $("#pd-name").textContent);
}

function projectMissing(p) {
  return p.items.filter((i) => !i.filename && (!i.verdict || LOADABLE.has(i.verdict))).map((i) => i.doi);
}

function projectRow(pid, it) {
  const tr = document.createElement("tr");
  tr.dataset.doi = String(it.doi || "").toLowerCase();
  const st = document.createElement("td");
  st.className = "c-state";
  if (it.verdict && STATES[it.verdict]) st.appendChild(chip(it.verdict));
  else {
    const s = document.createElement("span");
    s.className = "hint";
    s.textContent = T("nicht geprüft");
    st.appendChild(s);
  }
  const main = document.createElement("td");
  const title = document.createElement("div");
  title.className = "res-title" + (it.title ? "" : " untitled");
  title.textContent = it.title || T("Titel unbekannt");
  const meta = document.createElement("div");
  meta.className = "res-meta";
  const a = document.createElement("a");
  a.className = "mono";
  a.href = "https://doi.org/" + it.doi;
  a.target = "_blank";
  a.rel = "noopener";
  a.textContent = midTrunc(it.doi, 44);
  a.title = it.doi;
  meta.appendChild(a);
  if (it.year) { const y = document.createElement("span"); y.textContent = it.year; meta.appendChild(y); }
  if (it.source && it.source.startsWith("mcp")) {
    const c = document.createElement("span");
    c.className = "via";
    c.textContent = T("über {name}", { name: clientName(it.source) });
    meta.appendChild(c);
  }
  main.append(title, meta);
  if ((it.notices || []).length) {
    const tags = document.createElement("div");
    tags.className = "watch-tags";
    it.notices.forEach((n) => tags.appendChild(watchTag(n.kind, n.date)));
    main.appendChild(tags);
  }
  const lib = document.createElement("td");
  lib.className = "c-src";
  lib.appendChild(it.filename ? chip("legal", T("PDF da")) : (it.in_library ? chip("library") : document.createTextNode("")));
  const act = document.createElement("td");
  act.className = "c-act";
  const rm = document.createElement("button");
  rm.type = "button";
  rm.className = "btn ghost icon";
  rm.title = T("Aus dem Projekt entfernen");
  rm.setAttribute("aria-label", T("Aus dem Projekt entfernen"));
  rm.appendChild(icon("i-x"));
  rm.onclick = async () => {
    await fetch(`/api/projects/${encodeURIComponent(pid)}/items/${it.doi}`, { method: "DELETE" });
    loadProjects();
  };
  if (it.filename) act.appendChild(supportButton(tr, it.doi, []));
  act.appendChild(rm);
  tr.append(st, main, lib, act);
  return tr;
}

// ---------------------------------------------------------------------------
// Gesundheit einer Literaturliste: Echtheit, Aktualität, Publikationsorte
// ---------------------------------------------------------------------------
const HEALTH_ICON = { ok: "i-legal", notes: "i-warn", critical: "i-fail" };
const healthSeq = new WeakMap();

let verifyMissing = null;  // gespeicherte Vorschläge des sichtbaren Berichts

function loadVerifyHealth() {
  const real = verifyResults.filter((r) => r.doi && LOADABLE.has(r.verdict)).length;
  if (real >= 2 && verifyJobId) renderMissingWorks($("#verify-missing"), { job_id: verifyJobId }, verifyMissing, null);
  else $("#verify-missing").hidden = true;
  const items = verifyResults.map((r) => ({
    doi: r.doi || (r.found || {}).doi || "",
    year: (r.found || {}).year || (r.claimed || {}).year || null,
    verdict: r.verdict || null,
  }));
  if (items.length) loadHealth($("#verify-health"), { items }, $("#verify-body"));
}

async function loadHealth(el, payload, tbody) {
  const seq = (healthSeq.get(el) || 0) + 1;
  healthSeq.set(el, seq);
  el.hidden = false;
  el.innerHTML = `<p class="hint ph-wait">${T("Bewerte die Literaturliste und ihre Zeitschriften …")}</p>`;
  let data;
  try {
    const r = await fetch("/api/health", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    data = await r.json();
    if (!r.ok) throw new Error(data.error || T("Fehler"));
  } catch (e) {
    if (healthSeq.get(el) === seq) el.innerHTML = `<p class="hint">${T("Bewertung nicht möglich: {reason}.", { reason: esc(e.message || T("keine Verbindung")) })}</p>`;
    return;
  }
  if (healthSeq.get(el) !== seq) return;
  renderHealth(el, data);
  if (tbody) annotateVenues(tbody, data.venues || {});
}

function healthBars(title, rows, total) {
  const box = document.createElement("div");
  box.className = "ph-chart";
  const h = document.createElement("h4");
  h.textContent = title;
  const list = document.createElement("div");
  list.className = "barlist";
  const max = Math.max(1, ...rows.map((r) => r[1]));
  rows.forEach(([name, n, tip]) => {
    const row = document.createElement("div");
    row.className = "barlist-row";
    const lab = document.createElement("span");
    lab.className = "name";
    lab.textContent = name;
    const t = document.createElement("div");
    t.className = "track";
    const b = document.createElement("div");
    b.className = "bar";
    b.tabIndex = 0;
    b.dataset.tip = TN(total, "{k} von {n} Quelle", "{k} von {n} Quellen", { k: n }) + "\n" + (tip || name);
    t.appendChild(b);
    const v = document.createElement("span");
    v.className = "v";
    v.textContent = n;
    row.append(lab, t, v);
    list.appendChild(row);
    setTimeout(() => { b.style.width = `${(n / max) * 100}%`; }, 30);
  });
  box.append(h, list);
  return box;
}

function renderHealth(el, data) {
  const h = data.health;
  el.innerHTML = "";
  const head = document.createElement("div");
  head.className = "ph-head";
  const badge = document.createElement("span");
  badge.className = `ph-badge ph-${h.state}`;
  if (HEALTH_ICON[h.state]) badge.appendChild(icon(HEALTH_ICON[h.state]));
  badge.append(h.label);
  const title = document.createElement("h3");
  title.textContent = T("Gesundheit der Literaturliste");
  const src = document.createElement("span");
  src.className = "hint";
  const lists = (data.rankings || []).length ? `, ${data.rankings.join(", ")}` : "";
  src.textContent = TN(h.count, "{n} Quelle", "{n} Quellen") + " · " + T("Zeitschriften: {sources}", { sources: "OpenAlex, JUFO" + lists });
  head.append(title, badge, src);

  const dims = document.createElement("div");
  dims.className = "ph-dims";
  h.dimensions.forEach((d) => {
    const c = document.createElement("div");
    c.className = `ph-dim ph-${d.state}`;
    c.innerHTML = `<span class="ph-label">${esc(d.label)}</span><strong>${esc(d.headline)}</strong><span class="ph-detail">${esc(d.detail)}</span>`;
    dims.appendChild(c);
  });

  const charts = document.createElement("div");
  charts.className = "ph-charts";
  const age = h.age || {};
  if (age.known) {
    charts.appendChild(healthBars(T("Alter der Quellen"), age.buckets.map((b) => [b.label, b.count, T("{label} alt", { label: b.label })]), age.known));
  }
  const v = h.venues || {};
  const vrows = [[T("Zeitschriften"), v.journals || 0], [T("Preprints"), v.preprints || 0, T("Preprint-Server, nicht begutachtet")],
                 [T("Sonstige"), v.other || 0, T("Konferenzen, Bücher, Repositorien")], [T("Unbekannt"), v.unknown || 0, T("kein Publikationsort bekannt")]]
    .filter((r) => r[1]);
  if (v.jufo_known) ["3", "2", "1", "0"].forEach((k) => {
    if (v.jufo[k]) vrows.push([T("JUFO-Stufe {k}", { k }), v.jufo[k], T("Zeitschriften der JUFO-Stufe {k} (0 bis 3)", { k })]);
  });
  if (vrows.length) charts.appendChild(healthBars(T("Publikationsorte"), vrows, h.count));

  el.append(head, dims);
  if (charts.children.length) el.appendChild(charts);
  el.hidden = false;
}

function annotateVenues(tbody, venues) {
  tbody.querySelectorAll("tr[data-doi]").forEach((tr) => {
    tr.querySelectorAll(".venue-line").forEach((n) => n.remove());
    const v = venues[tr.dataset.doi];
    const cell = tr.children[1];
    if (!v || !cell) return;
    const line = document.createElement("div");
    line.className = "venue-line";
    const name = document.createElement("span");
    name.className = "venue-name";
    name.textContent = v.name || T("Unbekannter Ort");
    line.appendChild(name);
    const tag = (text, title, cls) => {
      const t = document.createElement("span");
      t.className = "venue-tag" + (cls ? " " + cls : "");
      t.textContent = text;
      if (title) t.title = title;
      line.appendChild(t);
    };
    if (v.preprint) tag("Preprint", T("Preprint-Server: nicht begutachtet"), "warn");
    if (v.jufo !== null && v.jufo !== undefined) tag(`JUFO ${v.jufo}`, T("Stufe der finnischen Publikationsliste (0 bis 3)"), v.jufo === 0 ? "warn" : "");
    if (v.citedness !== null && v.citedness !== undefined && !v.preprint) {
      const cites = Number(v.citedness).toLocaleString(LOCALE, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
      tag(T("{n} Zit./Artikel", { n: cites }), T("Durchschnittliche Zitate je Artikel der letzten zwei Jahre (OpenAlex)"));
    }
    if (v.doaj) tag("DOAJ", T("Im Directory of Open Access Journals"));
    (v.flags || []).forEach((f) => {
      if (f.kind === "doaj_removed") {
        tag(f.date ? T("Aus DOAJ entfernt {date}", { date: f.date }) : T("Aus DOAJ entfernt"),
          T("DOAJ hat die Zeitschrift entfernt: {reason}. Ältere Artikel können trotzdem in Ordnung sein. Quelle: {credit}", { reason: f.reason || "", credit: f.credit }), "warn");
      } else if (f.kind === "doaj_false") {
        tag(T("Falsche DOAJ-Angabe"), T("Die Zeitschrift gibt fälschlich an, im DOAJ zu stehen. Quelle: {credit}", { credit: f.credit }), "warn");
      }
    });
    (v.ranks || []).forEach((r) => tag(`${r.list}: ${r.rank}`, T("Aus deiner importierten Rankingliste"), "rank"));
    cell.appendChild(line);
  });
}

// Listen fragwürdiger Zeitschriften (Einstellungen): Stand und Herkunft
async function loadListState() {
  let j = {};
  try { j = await (await fetch("/api/journal-lists")).json(); } catch (e) { return; }
  const l = j.lists || {};
  const when = (x) => (x && x.fetched_at ? new Date(x.fetched_at * 1000).toLocaleDateString(LOCALE, { dateStyle: "medium" }) : null);
  const parts = [];
  if (l.doaj_withdrawn && l.doaj_withdrawn.count) {
    parts.push(T("DOAJ: {removed} entfernte Zeitschriften, {false} mit falscher Angabe, Stand {when}",
      { removed: fmtNum(l.doaj_withdrawn.count), false: fmtNum((l.doaj_false || {}).count || 0), when: when(l.doaj_withdrawn) }));
  }
  if (l.hijacked && l.hijacked.count) {
    parts.push(T("Retraction Watch: {n} gekaperte Websites, Stand {when}", { n: fmtNum(l.hijacked.count), when: when(l.hijacked) }));
  }
  $("#lists-state").textContent = parts.length ? parts.join(" · ") : T("Die Listen lädt Benthic bei der ersten Bewertung einer Literaturliste.");
}

// Eigene Rankinglisten (Einstellungen)
async function loadRankings() {
  let list = [];
  try { list = (await (await fetch("/api/rankings")).json()).rankings || []; } catch (e) { /* offline */ }
  const ul = $("#ranking-list");
  ul.innerHTML = "";
  if (!list.length) {
    ul.innerHTML = `<li class="hint">${T("Noch keine eigene Liste importiert.")}</li>`;
    return;
  }
  list.forEach((r) => {
    const li = document.createElement("li");
    const n = document.createElement("span");
    n.innerHTML = `<strong>${esc(r.name)}</strong> <span class="hint">${TN(r.count, "{n} Zeitschrift", "{n} Zeitschriften")} · ${esc(r.file || "")}</span>`;
    const rm = document.createElement("button");
    rm.type = "button";
    rm.className = "btn ghost small";
    rm.textContent = T("Entfernen");
    rm.onclick = async () => {
      await fetch(`/api/rankings/${encodeURIComponent(r.id)}`, { method: "DELETE" });
      loadRankings();
    };
    li.append(n, rm);
    ul.appendChild(li);
  });
}

async function importRanking() {
  const file = $("#ranking-file").files[0];
  if (!file) { toast(T("Keine Datei gewählt"), T("CSV oder Excel mit Spalten für Titel und Rang, gern mit ISSN.")); return; }
  const fd = new FormData();
  fd.append("file", file);
  fd.append("name", $("#ranking-name").value.trim() || file.name.replace(/\.[^.]+$/, ""));
  const r = await fetch("/api/rankings", { method: "POST", body: fd });
  const j = await r.json();
  if (!r.ok) { toast(T("Import fehlgeschlagen"), j.error || ""); return; }
  toast(T("Rankingliste importiert"), `${j.name}: ${TN(j.count, "{n} Zeitschrift", "{n} Zeitschriften")}`);
  $("#ranking-file").value = "";
  $("#ranking-name").value = "";
  loadRankings();
}

async function createProject() {
  const name = $("#project-name").value.trim();
  if (!name) { $("#project-name").focus(); return; }
  const r = await fetch("/api/projects", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, description: $("#project-desc").value.trim() }),
  });
  const j = await r.json();
  if (!r.ok) { toast(T("Projekt nicht angelegt"), j.error || ""); return; }
  $("#project-name").value = "";
  $("#project-desc").value = "";
  shownProject = j.id;
  toast(T("Projekt angelegt"), T("„{name}“ ist jetzt aktiv.", { name: j.name }));
  await loadProjects();
  showProject(j.id, true);
}

async function deleteProject() {
  const id = $("#project-detail").dataset.id;
  const p = projectList.find((x) => x.id === id);
  if (!p || !window.confirm(T("Projekt „{name}“ löschen? Die Bibliothek und die PDFs bleiben erhalten.", { name: p.name }))) return;
  await fetch(`/api/projects/${encodeURIComponent(id)}`, { method: "DELETE" });
  shownProject = null;
  $("#project-detail").hidden = true;
  loadProjects();
}

async function exportBibliography() {
  const id = $("#project-detail").dataset.id;
  const [fmt, style] = $("#pd-format").value.split(":");
  const q = new URLSearchParams({ format: fmt, style: style || "apa" });
  $("#pd-export").disabled = true;
  try {
    const r = await fetch(`/api/projects/${encodeURIComponent(id)}/bibliography?${q}`);
    const blob = await r.blob();
    const name = (r.headers.get("Content-Disposition") || "").split("filename=")[1] || (LANG === "de" ? "literatur.txt" : "references.txt");
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } finally {
    $("#pd-export").disabled = false;
  }
}

async function fetchProjectMissing() {
  const id = $("#project-detail").dataset.id;
  const r = await fetch(`/api/projects/${encodeURIComponent(id)}`);
  const p = await r.json();
  const dois = projectMissing(p);
  if (!dois.length) return;
  $("#doi-input").value = dois.join("\n");
  updateCounter();
  showView("download");
  startJob("download", { project: id, extra_routes: false });
}

// ---------------------------------------------------------------------------
// KI-Anbindung: Status, Verbinden, Läufen von Claude live folgen
// ---------------------------------------------------------------------------
let seenJobs = null;
let activityTimer = null;

// Name des MCP-Clients (clientInfo.name) für die Anzeige; Reihenfolge zählt:
// "codex-mcp-client" enthält "code", "cursor-vscode" auch.
const CLIENT_NAMES = [
  ["claude", "Claude"], ["codex", "Codex"], ["chatgpt", "ChatGPT"], ["antigravity", "Antigravity"],
  ["gemini", "Gemini"], ["cursor", "Cursor"], ["grok", "Grok"], ["devin", "Devin"], ["windsurf", "Windsurf"],
  ["zed", "Zed"], ["goose", "Goose"], ["kiro", "Kiro"], ["perplexity", "Perplexity"], ["raycast", "Raycast"],
  ["lm", "LM Studio"], ["code", "VS Code"],
];

function clientName(origin) {
  const raw = String(origin || "").replace(/^mcp:?/, "").toLowerCase();
  if (!raw) return "Claude";
  const hit = CLIENT_NAMES.find(([key]) => raw.includes(key));
  return hit ? hit[1] : raw.charAt(0).toUpperCase() + raw.slice(1);
}

function ago(ts) {
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (s < 60) return T("vor {n} s", { n: s });
  if (s < 3600) return T("vor {n} min", { n: Math.round(s / 60) });
  return new Date(ts * 1000).toLocaleTimeString(LOCALE, { hour: "2-digit", minute: "2-digit" });
}

async function pollActivity() {
  clearTimeout(activityTimer);
  try {
    const [act, jobs] = await Promise.all([
      fetch("/api/activity").then((r) => r.json()),
      fetch("/api/jobs").then((r) => r.json()),
    ]);
    renderActivity(act);
    followClientJobs(jobs.jobs || []);
  } catch (e) { /* Backend kurz weg */ }
  activityTimer = setTimeout(pollActivity, 3000);
}

function renderActivity(act) {
  const live = (act.clients || []).filter((c) => c.client.startsWith("mcp") && c.connected)
    .sort((a, b) => b.last_seen - a.last_seen);
  const pill = $("#ai-status");
  pill.classList.toggle("on", !!live.length);
  $("#ai-status-text").textContent = live.length
    ? T("{name} aktiv · {ago}", { name: clientName(live[0].client), ago: ago(live[0].last_seen) }) : T("KI: nicht verbunden");
  const status = $("#ai-card-status");
  status.innerHTML = "";
  status.appendChild(live.length ? chip("verified", T("{name} verbunden", { name: clientName(live[0].client) }))
    : chip("library", T("Noch keine Verbindung")));
  const box = $("#ai-activity");
  box.innerHTML = "";
  (act.events || []).slice(0, 6).forEach((e) => {
    const row = document.createElement("div");
    row.className = "activity-row";
    const t = document.createElement("span");
    t.className = "t";
    t.textContent = ago(e.ts);
    const c = document.createElement("span");
    c.className = "c";
    c.textContent = clientName(e.client);
    const m = document.createElement("span");
    m.textContent = e.summary || e.tool;
    row.append(t, c, m);
    box.appendChild(row);
  });
}

// Ein LLM startet eine Prüfung oder einen Download: die App zeigt ihn live.
function followClientJobs(jobs) {
  if (seenJobs === null) {  // beim Start nur merken, nicht alten Läufen folgen
    seenJobs = new Set(jobs.map((j) => j.id));
    return;
  }
  jobs.forEach((j) => {
    if (seenJobs.has(j.id)) return;
    seenJobs.add(j.id);
    if (!String(j.origin || "").startsWith("mcp")) return;
    // Auch schnelle Läufe zeigen (z. B. aus dem Cache), solange sie frisch sind.
    if (j.status !== "running" && Date.now() / 1000 - (j.created_at || 0) > 60) return;
    const who = clientName(j.origin);
    if (j.mode === "verify") {
      toast(TN(j.total, "{who} prüft {n} Quelle", "{who} prüft {n} Quellen", { who }),
        j.project_name ? T("Projekt „{name}“", { name: j.project_name }) : T("Live in Benthic"));
      showView("verify");
      beginVerify(j.id, j.document);
    } else {
      toast(TN(j.total, "{who} lädt {n} Quelle", "{who} lädt {n} Quellen", { who }),
        j.project_name ? T("Projekt „{name}“", { name: j.project_name }) : T("Über Open Access"));
      showView("download");
      currentJobId = j.id;
      currentResults = [];
      lastSeq = 0;
      eventLog = [];
      $("#progress-card").hidden = false;
      $("#results-card").hidden = false;
      setRunning(true);
      dive.reset();
      pollJob(j.id, j.mode === "check" ? "check" : "download");
    }
  });
}

async function loadMcp() {
  try {
    const j = await (await fetch("/api/mcp")).json();
    const tools = $("#ai-tools");
    tools.innerHTML = "";
    const names = {
      verify_references: T("Quellen prüfen"), fetch_papers: T("Laden"), assess_references: T("Literaturliste bewerten"),
      check_open_access: T("Open Access prüfen"), get_job: T("Laufstatus"), search_library: T("Bibliothek durchsuchen"),
      get_fulltext: T("Artikel lesen"), list_projects: T("Projekte auflisten"), create_project: T("Projekt anlegen"),
      project_status: T("Projektstatus"), export_bibliography: T("Literaturverzeichnis"),
    };
    (j.tools || []).forEach((t) => {
      const c = document.createElement("span");
      c.className = "chip library";
      c.textContent = names[t.name] || t.title;
      c.title = t.name;
      tools.appendChild(c);
    });
    mcpInfo = j;
    renderAssistantPicker();
  } catch (e) { /* ignore */ }
}

// KI-Assistenten: Auswahl und Einrichtung (Claude, Codex, Gemini CLI, Antigravity, ...).
let mcpInfo = null;
function renderAssistantPicker() {
  const list = (mcpInfo && mcpInfo.assistants) || [];
  const sel = $("#ai-assistant");
  if (!list.length) { sel.closest(".assistant-pick").hidden = true; return; }
  let stored = null;
  try { stored = localStorage.getItem("benthic-assistant"); } catch (e) { /* privat */ }
  sel.innerHTML = "";
  const groups = {};
  list.forEach((a) => {
    let g = groups[a.group];
    if (!g) { g = groups[a.group] = document.createElement("optgroup"); g.label = a.group; sel.appendChild(g); }
    const o = document.createElement("option");
    o.value = a.id;
    o.textContent = a.name;
    g.appendChild(o);
  });
  sel.value = list.some((a) => a.id === stored) ? stored : list[0].id;
  sel.onchange = () => {
    try { localStorage.setItem("benthic-assistant", sel.value); } catch (e) { /* privat */ }
    renderAssistantGuide();
  };
  renderAssistantGuide();
}

function snippetBlock(label, text) {
  const box = document.createElement("div");
  box.className = "snippet";
  const head = document.createElement("div");
  head.className = "snippet-head";
  const l = document.createElement("span");
  l.textContent = label;
  const b = document.createElement("button");
  b.type = "button";
  b.className = "btn small";
  b.textContent = T("Kopieren");
  const pre = document.createElement("pre");
  pre.textContent = text;
  b.onclick = () => copyValue(text);
  head.append(l, b);
  box.append(head, pre);
  return box;
}

function renderAssistantGuide() {
  const a = ((mcpInfo && mcpInfo.assistants) || []).find((x) => x.id === $("#ai-assistant").value);
  const box = $("#ai-guide");
  box.innerHTML = "";
  if (!a) return;
  const ol = document.createElement("ol");
  ol.className = "assistant-steps";
  a.steps.forEach((t) => { const li = document.createElement("li"); li.textContent = t; ol.appendChild(li); });
  box.appendChild(ol);
  const row = document.createElement("div");
  row.className = "actions";
  const missingApp = a.installed === false;
  if (a.kind === "oneclick") {
    const c = document.createElement("button");
    c.type = "button";
    c.className = "btn primary";
    c.id = "ai-connect";
    c.append(icon("i-plug"), document.createTextNode(T("Mit Claude Desktop verbinden")));
    c.disabled = !mcpInfo.claude_desktop;
    c.title = mcpInfo.claude_desktop ? "" : T("Claude Desktop ist nicht installiert");
    c.onclick = connectClaude;
    const d = document.createElement("a");
    d.className = "btn";
    d.href = "/api/mcp/bundle";
    d.setAttribute("download", "Benthic.mcpb");
    d.append(icon("i-download"), document.createTextNode(T("Paket herunterladen")));
    row.append(c, d);
  } else if (a.kind === "auto") {
    const c = document.createElement("button");
    c.type = "button";
    c.className = "btn primary";
    c.append(icon("i-plug"), document.createTextNode(a.state === "current" ? T("Erneut eintragen") : T("Mit {app} verbinden", { app: a.app })));
    c.onclick = () => installAssistant(a, c);
    row.appendChild(c);
  } else if (a.kind === "deeplink") {
    const c = document.createElement("a");
    c.className = "btn primary" + (missingApp ? " disabled" : "");
    c.append(icon("i-plug"), document.createTextNode(T("Mit {app} verbinden", { app: a.app })));
    if (missingApp) {
      c.setAttribute("aria-disabled", "true");
      c.title = T("{app} ist nicht installiert", { app: a.app });
    } else {
      c.href = a.link;
    }
    row.appendChild(c);
  }
  const badge = assistantBadge(a);
  if (badge) row.appendChild(badge);
  if (row.children.length) box.appendChild(row);

  if (a.kind === "command") {
    box.appendChild(snippetBlock(T("Im Terminal"), a.snippet));
    if (a.alt_snippet) box.appendChild(snippetBlock(a.alt_label || T("Oder in {file}", { file: a.alt_file }), a.alt_snippet));
  } else if (a.kind === "auto" || a.kind === "deeplink" || a.kind === "file") {
    box.appendChild(snippetBlock(a.snippet_label || T("Eintrag für {file}", { file: a.file }), a.snippet));
    if (a.alt_snippet) box.appendChild(snippetBlock(T("Oder in {file}", { file: a.alt_file }), a.alt_snippet));
  } else if (a.kind === "paste") {
    box.appendChild(snippetBlock(T("Bitte an den Assistenten"), a.snippet));
    const go = document.createElement("button");
    go.type = "button";
    go.className = "btn";
    go.append(icon("i-verify"), document.createTextNode(T("Zu „Prüfen“")));
    go.onclick = () => showView("verify");
    const r2 = document.createElement("div");
    r2.className = "actions";
    r2.appendChild(go);
    box.appendChild(r2);
  }
  if (a.docs) {
    const p = document.createElement("p");
    p.className = "hint";
    const link = document.createElement("a");
    link.href = a.docs;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = T("Anleitung des Herstellers");
    p.append(link);
    box.appendChild(p);
  }
  if (a.kind !== "oneclick" && a.kind !== "paste" && mcpInfo.launcher) {
    const p = document.createElement("p");
    p.className = "hint";
    p.textContent = T("Der Befehl startet Benthic über eine kleine Datei in deinem Benthic-Ordner. Sie findet die App auch nach dem Verschieben.");
    box.appendChild(p);
  }
}

// Zustand neben dem Verbinden-Knopf: eingetragen, veraltet, App fehlt.
function assistantBadge(a) {
  if (a.state === "current") return chip("verified", T("Eingetragen"));
  if (a.state === "stale") {
    const c = chip("partial", T("Alter Eintrag"));
    c.title = T("Der Eintrag zeigt auf eine andere Benthic-Installation. Erneut verbinden aktualisiert ihn.");
    return c;
  }
  if (a.state === "unreadable") return chip("uncertain", T("Datei nicht lesbar"));
  if (a.installed === false) return chip("library", T("{app} nicht gefunden", { app: a.app || a.name }));
  return null;
}

async function installAssistant(a, btn) {
  btn.disabled = true;
  try {
    const r = await fetch("/api/mcp/install", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: a.id }),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) { toast(T("Nicht eingetragen"), j.error || ""); return; }
    toast(T("In {path} eingetragen", { path: j.path }), T("{app} neu starten, dann steht Benthic als Werkzeug bereit.", { app: a.app }));
    await loadMcp();
  } finally {
    btn.disabled = false;
  }
}

async function connectClaude() {
  $("#ai-connect").disabled = true;
  try {
    const j = await (await fetch("/api/mcp/connect", { method: "POST" })).json();
    if (j.opened) {
      toast(T("Claude Desktop öffnet sich"), T("Bestätige dort „Installieren“. Danach steht Benthic in Claude als Werkzeug bereit."));
    } else {
      toast(T("Claude Desktop nicht gefunden"), T("Lade das Paket herunter und öffne es mit Claude Desktop."));
    }
  } finally {
    $("#ai-connect").disabled = false;
  }
}

async function copyText(id) {
  return copyValue($("#" + id).textContent);
}

async function copyValue(text) {
  try { await navigator.clipboard.writeText(text); } catch (e) {
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }
  toast(T("Kopiert"), T("In die Zwischenablage übernommen."));
}

// ---------------------------------------------------------------------------
// Motion helpers: count-up, grow bars, shared tooltip, theme, Dock badge
// ---------------------------------------------------------------------------
const reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function countUp(el, to) {
  if (to == null) return;
  const final = Number(to).toLocaleString(LOCALE);
  if (reduceMotion || document.hidden) { el.textContent = final; return; }
  const start = performance.now();
  const dur = 700;
  let finished = false;
  const step = (now) => {
    if (finished) return;
    const t = Math.min(1, (now - start) / dur);
    const eased = 1 - Math.pow(1 - t, 3);
    el.textContent = Math.round(to * eased).toLocaleString(LOCALE);
    if (t < 1) requestAnimationFrame(step); else finished = true;
  };
  requestAnimationFrame(step);
  // Fallback: ohne Bildwiederholung (Fenster im Hintergrund) trotzdem den Endwert zeigen.
  setTimeout(() => { finished = true; el.textContent = final; }, dur + 150);
}

// Breiten erst nach dem Einfügen setzen, damit die Balken wachsen.
function grow(pairs) {
  setTimeout(() => pairs.forEach(([el, w]) => { el.style.width = w; }), 40);
}

(() => {
  const tip = $("#tip");
  const show = (el, x, y) => {
    const html = el.dataset.tip;
    if (!html) return;
    tip.innerHTML = "";
    html.split("\n").forEach((line, i) => {
      const row = document.createElement(i === 0 ? "b" : "div");
      row.textContent = line;
      tip.appendChild(row);
    });
    tip.hidden = false;
    const r = tip.getBoundingClientRect();
    tip.style.left = Math.min(window.innerWidth - r.width - 8, x + 12) + "px";
    tip.style.top = Math.max(8, y - r.height - 10) + "px";
  };
  document.addEventListener("pointermove", (e) => {
    const el = e.target.closest && e.target.closest("[data-tip]");
    if (el) show(el, e.clientX, e.clientY); else tip.hidden = true;
  });
  document.addEventListener("focusin", (e) => {
    const el = e.target.closest && e.target.closest("[data-tip]");
    if (!el) return;
    const r = el.getBoundingClientRect();
    show(el, r.left + r.width / 2, r.top);
  });
  document.addEventListener("focusout", () => { tip.hidden = true; });
})();

function applyTheme(mode) {
  mode = mode === "light" || mode === "dark" ? mode : "system";
  if (mode === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = mode;
  try { localStorage.setItem("benthic-theme", mode); } catch (e) { /* privat */ }
  document.querySelectorAll("#set-theme button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.themeMode === mode)));
  // Native App: Fensterrahmen und Titelleiste folgen derselben Wahl.
  try { window.webkit.messageHandlers.benthicTheme.postMessage({ theme: mode }); } catch (e) { /* Browser */ }
}
// Dunkel ist die Voreinstellung, wie die Website. Wer noch auf Automatisch stand (die frühere
// Voreinstellung), wechselt einmal; eine spätere Wahl in den Einstellungen bleibt.
try {
  let mode = localStorage.getItem("benthic-theme");
  if (!localStorage.getItem("benthic-theme-v13")) {
    if (!mode || mode === "system") mode = "dark";
    localStorage.setItem("benthic-theme-v13", "1");
  }
  applyTheme(mode);
} catch (e) { applyTheme("dark"); }
document.querySelectorAll("#set-theme button").forEach((b) => { b.onclick = () => applyTheme(b.dataset.themeMode); });

function dockBadge(text) {
  try {
    if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.benthicDock) {
      window.webkit.messageHandlers.benthicDock.postMessage({ badge: text ? String(text) : "" });
    }
  } catch (e) { /* nur in der App */ }
}

// ---------------------------------------------------------------------------
// Übersicht (Dashboard)
// ---------------------------------------------------------------------------
let ovDays = "";

function renderOverviewProjects() {
  const sel = $("#ov-project");
  const cur = sel.value;
  sel.innerHTML = "";
  sel.appendChild(new Option(T("Alle Projekte"), ""));
  projectList.forEach((p) => sel.appendChild(new Option(p.name, p.id)));
  sel.value = projectList.some((p) => p.id === cur) ? cur : "";
}

async function loadOverview() {
  renderOverviewProjects();
  const body = $("#ov-body");
  body.classList.add("loading");
  const q = new URLSearchParams();
  if ($("#ov-project").value) q.set("project", $("#ov-project").value);
  if (ovDays) q.set("days", ovDays);
  let s;
  try {
    s = await (await fetch("/api/stats?" + q)).json();
  } catch (e) {
    return;
  } finally {
    body.classList.remove("loading");
  }
  renderOverview(s);
}

function kpiTile(label, value, suffix, sub, alert) {
  const t = document.createElement("div");
  t.className = "kpi" + (alert ? " alert" : "");
  const l = document.createElement("div");
  l.className = "label";
  l.textContent = label;
  const v = document.createElement("div");
  v.className = "value";
  const num = document.createElement("span");
  num.textContent = "0";
  v.appendChild(num);
  if (value == null) {
    num.textContent = "";
    const s = document.createElement("small");
    s.textContent = T("noch keine Daten");
    v.appendChild(s);
  } else {
    if (suffix) { const s = document.createElement("small"); s.textContent = suffix; v.appendChild(s); }
    countUp(num, value);
  }
  const d = document.createElement("div");
  d.className = "sub";
  d.textContent = sub || "";
  t.append(l, v, d);
  return t;
}

function renderOverview(s) {
  const dl = s.downloads || {};
  const loadedAny = Object.values(dl).some((n) => n > 0);
  const empty = !s.kpi.checked && !loadedAny;
  $("#ov-empty").hidden = !empty;
  $("#ov-body").hidden = empty;
  $("#ov-scope").textContent = s.scope.project_name
    ? TN(s.scope.runs, "{n} Lauf in „{name}“", "{n} Läufe in „{name}“", { name: s.scope.project_name })
    : TN(s.scope.runs, "{n} Lauf", "{n} Läufe");
  if (empty) return;

  const real = (s.funnel[1] || {}).value || 0;
  const kpis = $("#ov-kpis");
  kpis.innerHTML = "";
  kpis.append(
    kpiTile(T("Geprüft"), s.kpi.checked, "", T("{n} auffällig", { n: s.kpi.flagged })),
    kpiTile(T("Echt"), s.kpi.real_share, " %", T("{done} von {total}", { done: real, total: s.kpi.checked })),
    kpiTile(T("Zurückgezogen"), s.kpi.retracted, "", s.kpi.retracted ? T("nicht zitieren") : T("keine gefunden"), s.kpi.retracted > 0),
    kpiTile("Open Access", s.kpi.legal_share, " %", T("der geladenen PDFs")),
    kpiTile("PDFs", s.kpi.pdfs, "", T("in der Bibliothek")),
  );

  // Trichter: eine Farbe, Länge = Anzahl, Abfall zwischen den Stufen benannt.
  const f = $("#ov-funnel");
  f.innerHTML = "";
  const top = Math.max(1, s.funnel[0].value);
  const drops = [T("erfunden, falsch zugeordnet oder unsicher"), T("ohne bekannte freie Fassung oder nicht versucht"), T("noch nicht in der Bibliothek")];
  const grows = [];
  s.funnel.forEach((st, i) => {
    if (i > 0) {
      const d = s.funnel[i - 1].value - st.value;
      const drop = document.createElement("div");
      drop.className = "funnel-row";
      const sp = document.createElement("span");
      const txt = document.createElement("div");
      txt.className = "funnel-drop";
      if (d > 0) {
        const b = document.createElement("b");
        b.textContent = `− ${d}`;
        txt.append(b, document.createTextNode(` ${drops[i - 1]}`));
      }
      drop.append(sp, txt);
      f.appendChild(drop);
    }
    const row = document.createElement("div");
    row.className = "funnel-row";
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = st.label;
    const track = document.createElement("div");
    track.className = "track";
    const bar = document.createElement("div");
    bar.className = "bar";
    const pct = Math.round((st.value / top) * 100);
    bar.tabIndex = 0;
    bar.dataset.tip = TN(st.value, "{n} Quelle", "{n} Quellen") + "\n" + T("{label}: {pct} % der genannten", { label: st.label, pct });
    track.appendChild(bar);
    const val = document.createElement("span");
    val.className = "val";
    const b = document.createElement("b");
    b.textContent = fmtNum(st.value);
    const p = document.createElement("span");
    p.textContent = `${pct} %`;
    val.append(b, p);
    row.append(name, track, val);
    f.appendChild(row);
    grows.push([bar, `${st.value ? Math.max(1.5, (st.value / top) * 100) : 0}%`]);
  });

  // Wie Quellen geladen wurden: Teil vom Ganzen, vier Ausgänge.
  const share = $("#ov-share");
  share.innerHTML = "";
  const parts = [
    ["legal", T("Open Access oder vorhanden"), (dl.oa || 0) + (dl.library || 0)],
    ["manual", T("Frei verfügbar, manuell öffnen"), dl.blocked || 0],
    ["deep", T("Über {label}", { label: routesLabel() }), dl.route || 0],
    ["none", T("Keine Kopie"), dl.none || 0],
  ].filter(([key, , n]) => key !== "deep" || ROUTES.length || n);
  const total = parts.reduce((n, p) => n + p[2], 0);
  const head = document.createElement("div");
  head.className = "headline";
  const hb = document.createElement("b");
  const hs = document.createElement("span");
  if (s.kpi.legal_share == null) {
    hb.textContent = total ? `${total}` : "0";
    hs.textContent = total ? T("Download-Versuche, noch nichts geladen") : T("noch keine Downloads");
  } else {
    hb.textContent = `${s.kpi.legal_share} %`;
    hs.textContent = T("der geladenen PDFs kamen über Open Access");
  }
  head.append(hb, hs);
  const stack = document.createElement("div");
  stack.className = "stack";
  const legend = document.createElement("div");
  legend.className = "legend";
  parts.forEach(([key, label, n]) => {
    const seg = document.createElement("i");
    seg.className = "c-" + key;
    seg.tabIndex = n ? 0 : -1;
    seg.dataset.tip = TN(n, "{n} Quelle", "{n} Quellen") + `\n${label} (${total ? Math.round((n / total) * 100) : 0} %)`;
    stack.appendChild(seg);
    grows.push([seg, total ? `${(n / total) * 100}%` : "0%"]);
    const item = document.createElement("span");
    item.className = "c-" + key;
    item.textContent = label + " ";
    const em = document.createElement("em");
    em.textContent = String(n);
    item.appendChild(em);
    legend.appendChild(item);
  });
  share.append(head, stack, legend);

  // Woher die PDFs kamen: eine Reihe, sortiert.
  const src = $("#ov-sources");
  src.innerHTML = "";
  const maxSrc = Math.max(1, ...(s.sources || []).map((x) => x.value));
  if (!(s.sources || []).length) src.innerHTML = `<p class="empty-note">${T("Noch keine PDFs über Open Access geladen.")}</p>`;
  (s.sources || []).forEach((x) => {
    const row = document.createElement("div");
    row.className = "barlist-row";
    const n = document.createElement("span");
    n.className = "name";
    n.textContent = x.name;
    const t = document.createElement("div");
    t.className = "track";
    const b = document.createElement("div");
    b.className = "bar";
    b.tabIndex = 0;
    b.dataset.tip = TN(x.value, "{n} PDF", "{n} PDFs") + "\n" + T("über {name}", { name: x.name });
    t.appendChild(b);
    const v = document.createElement("span");
    v.className = "v";
    v.textContent = x.value;
    row.append(n, t, v);
    src.appendChild(row);
    grows.push([b, `${(x.value / maxSrc) * 100}%`]);
  });

  // Urteile: Wort und Symbol als Kennung, Farbe nach Gruppe.
  const ver = $("#ov-verdicts");
  ver.innerHTML = "";
  const vmax = Math.max(1, ...Object.values(s.verdicts));
  const group = { verified: "", corrected: "", partial: " manual", uncertain: " manual", mismatch: " none", not_found: " none", retracted: " none" };
  const anyVerdict = Object.values(s.verdicts).some((n) => n);
  if (!anyVerdict) ver.innerHTML = `<p class="empty-note">${T("Noch keine Prüfung in diesem Bereich.")}</p>`;
  VERDICT_ORDER.forEach((v) => {
    const n = s.verdicts[v] || 0;
    if (!n) return;
    const row = document.createElement("div");
    row.className = "barlist-row";
    const lab = document.createElement("span");
    lab.className = "name";
    lab.appendChild(chip(v));
    const t = document.createElement("div");
    t.className = "track";
    const b = document.createElement("div");
    b.className = "bar" + group[v];
    b.tabIndex = 0;
    b.dataset.tip = TN(n, "{n} Quelle", "{n} Quellen") + "\n" + STATES[v].title;
    t.appendChild(b);
    const val = document.createElement("span");
    val.className = "v";
    val.textContent = n;
    row.append(lab, t, val);
    ver.appendChild(row);
    grows.push([b, `${(n / vmax) * 100}%`]);
  });

  const fl = $("#ov-flagged");
  fl.innerHTML = "";
  if (!(s.flagged || []).length) fl.innerHTML = `<p class="empty-note">${T("Keine auffälligen Quellen. Gut so.")}</p>`;
  (s.flagged || []).forEach((x) => {
    const row = document.createElement("div");
    row.className = "flag";
    const c = chip(x.verdict);
    const t = document.createElement("span");
    t.className = "t";
    t.textContent = x.title || x.doi || "";
    t.title = x.title || "";
    const n = document.createElement("span");
    n.className = "n";
    n.textContent = x.doi ? `${x.doi}${x.note ? " · " + x.note : ""}` : (x.note || "");
    row.append(c, t, n);
    fl.appendChild(row);
  });

  // Tabellenansicht: dieselben Zahlen ohne Grafik.
  const table = $("#ov-table");
  table.innerHTML = "";
  const rows = [[T("Bereich"), T("Kennzahl"), T("Wert")]];
  s.funnel.forEach((st) => rows.push([T("Trichter"), st.label, st.value]));
  parts.forEach(([, label, n]) => rows.push([T("Laden"), label, n]));
  VERDICT_ORDER.forEach((v) => rows.push([T("Prüfung"), STATES[v].label, s.verdicts[v] || 0]));
  (s.sources || []).forEach((x) => rows.push([T("Quelle"), x.name, x.value]));
  rows.forEach((r, i) => {
    const tr = document.createElement("tr");
    r.forEach((cell, j) => {
      const td = document.createElement(i === 0 ? "th" : "td");
      td.textContent = String(cell);
      if (j === 2 && i > 0) td.className = "n";
      tr.appendChild(td);
    });
    table.appendChild(tr);
  });

  grow(grows);
}

// ---------------------------------------------------------------------------
// Live-Tauchgang: jede Quelle als Blase, die so tief sinkt wie nötig
// ---------------------------------------------------------------------------
class Dive {
  constructor(el, mode) {
    this.el = el;
    this.mode = mode;  // "download" | "verify"
    this.items = new Map();
    this.done = 0;
  }

  bands() {
    return this.mode === "verify"
      ? [[T("Erkannt"), 0], [T("Nachschlagen"), 0.34], [T("Titelsuche"), 0.68]]
      : [[T("Oberfläche"), 0], ["Open Access", 0.34], [routesOn() ? T("Zusatzwege") : T("Hochschule"), 0.68]];
  }

  reset() {
    this.items.forEach((it) => it.el.remove());
    this.items.clear();
    this.done = 0;
    this.el.innerHTML = "";
    const h = this.el.clientHeight || 176;
    this.bands().forEach(([label, top], i) => {
      const band = document.createElement("div");
      band.className = "dive-band";
      band.style.top = `${top * 100}%`;
      band.style.height = `${(i === 2 ? 1 - top : 0.34) * 100}%`;
      const l = document.createElement("span");
      l.className = "lbl";
      l.textContent = label;
      band.appendChild(l);
      this.el.appendChild(band);
    });
    this.ticks = { mid: {}, deep: {} };
    const mid = this.mode === "verify" ? ["OpenAlex", "Crossref", "Europe PMC", "arXiv", T("DOI-Register")]
      : PROVIDERS.filter(([n]) => (settings.oa_providers || []).includes(n)).map(([, l]) => l);
    const deep = this.mode === "verify" ? ["Crossref", "OpenAlex", "arXiv"]
      : (routesOn() ? ROUTES.flatMap((r) => ((r.targets || []).length ? r.targets.slice(0, 4) : [r.label])).slice(0, 6)
        : [T("Hochschulzugang (Proxy-Link)")]);
    const place = (names, row, y) => names.forEach((name, i) => {
      const t = document.createElement("span");
      t.className = "dive-tick" + (row === "deep" ? " deep" : "");
      t.textContent = name;
      t.style.left = `${24 + ((i + 0.5) / names.length) * 72}%`;
      // Bei vielen Quellen abwechselnd auf zwei Zeilen, damit nichts überlappt.
      t.style.top = `${y + (names.length > 4 && i % 2 ? 13 : 0)}px`;
      this.el.appendChild(t);
      this.ticks[row][name] = t;
    });
    place(mid, "mid", Math.round(h * 0.34) + 34);
    place(deep, "deep", Math.round(h * 0.68) + 34);
    this.tray = document.createElement("span");
    this.tray.className = "dive-tray";
    this.el.appendChild(this.tray);
  }

  target(a, lane) {
    const w = this.el.clientWidth || 600;
    const h = this.el.clientHeight || 176;
    const jitter = ((lane % 3) - 1) * 6;
    const at = (row, name) => {
      const t = this.ticks[row][name];
      return t ? (parseFloat(t.style.left) / 100) * w : w * 0.6;
    };
    const ph = a.phase || "";
    if (this.mode === "verify") {
      if (ph === "lookup") return [at("mid", { openalex: "OpenAlex", europepmc: "Europe PMC" }[a.source] || "Crossref"), h * 0.45 + jitter, "mid", null];
      if (ph === "search") return [w * (0.4 + (lane % 5) * 0.1), h * 0.8 + jitter, "deep", null];
    } else {
      if (ph.startsWith("route_")) {
        const name = a.target || routeLabel(a.route);
        return [at("deep", name), h * 0.8 + jitter, "deep", name];
      }
      if (ph.startsWith("provider") || ph.startsWith("download")) {
        const label = sourceLabel(a.source);
        return [at("mid", label), h * 0.45 + jitter, "mid", label];
      }
    }
    return [130 + lane * 26, h * 0.2 + jitter, "top", null];
  }

  outcome(res) {
    if (!res) return "library";
    if (this.mode === "verify") {
      if (LOADABLE.has(res.verdict)) return "legal";
      return res.verdict === "uncertain" ? "manual" : "none";
    }
    if (res.status === "ok") return isRouteChannel(res.channel) ? "deep" : "legal";
    if (res.status === "skipped") return "library";
    if (res.status === "blocked") return "manual";
    return "none";
  }

  update(job) {
    if (!this.ticks) this.reset();
    const active = job.active || {};
    const results = new Map((job.results || []).map((r) => [r.identifier, r]));
    const lit = { mid: new Set(), deep: new Set() };
    const used = new Set([...this.items.values()].map((it) => it.lane));
    Object.entries(active).forEach(([ident, a]) => {
      let it = this.items.get(ident);
      if (!it) {
        let lane = 0;
        while (used.has(lane)) lane++;
        used.add(lane);
        const el = document.createElement("span");
        el.className = "bubble wobble";
        el.title = ident;
        this.el.appendChild(el);
        it = { el, lane };
        this.items.set(ident, it);
        const [x0, y0] = this.target({ phase: "start" }, lane);
        el.style.transform = `translate(${x0}px, ${y0 - 30}px)`;
        el.getBoundingClientRect();  // Startposition festhalten, dann animieren
      }
      const [x, y, row, tick] = this.target(a, it.lane);
      it.el.style.transform = `translate(${x}px, ${y}px)`;
      if (row === "mid" || row === "deep") {
        if (tick) lit[row].add(tick);
        else if (row === "mid" && this.mode === "verify") lit.mid.add({ openalex: "OpenAlex", europepmc: "Europe PMC" }[a.source] || "Crossref");
      }
    });
    Object.entries(this.ticks).forEach(([row, map]) => Object.entries(map).forEach(([name, el]) => el.classList.toggle("on", lit[row].has(name))));
    // Fertige Quellen tauchen auf und zeigen ihr Ergebnis.
    this.items.forEach((it, ident) => {
      if (active[ident] || it.finishing) return;
      const res = results.get(ident);
      if (!res && job.status === "running") return;
      it.finishing = true;
      this.done += 1;
      const w = this.el.clientWidth || 600;
      it.el.classList.remove("wobble");
      it.el.classList.add("done", this.outcome(res));
      it.el.style.transform = `translate(${w - 40 - (this.done % 5) * 14}px, 34px)`;
      setTimeout(() => it.el.classList.add("out"), 1300);
      setTimeout(() => { it.el.remove(); this.items.delete(ident); }, 1900);
    });
    this.tray.textContent = T("{done} / {total} aufgetaucht", { done: job.done || 0, total: job.total || 0 });
    if (job.status !== "running") Object.values(this.ticks).forEach((m) => Object.values(m).forEach((el) => el.classList.remove("on")));
  }
}

const dive = new Dive($("#dive"), "download");
const verifyDive = new Dive($("#verify-dive"), "verify");

// ---------------------------------------------------------------------------
// Befehlspalette (Cmd+K)
// ---------------------------------------------------------------------------
// Views in sidebar order; plugins can add their own (window.Benthic.addView).
let VIEW_KEYS = [];
const VIEW_NAMES = { overview: T("Übersicht"), verify: T("Prüfen"), batch: T("Stapelprüfung"), download: "Download",
  projects: T("Projekte"), library: T("Bibliothek"), history: T("Verlauf"), settings: T("Einstellungen"),
  about: T("Über Benthic") };
const VIEW_ICONS = { overview: "i-chart", verify: "i-verify", batch: "i-file", download: "i-download", projects: "i-folder",
  library: "i-book", history: "i-clock", settings: "i-sliders", about: "i-user" };
const viewHooks = {};

function updateViewKeys() {
  VIEW_KEYS = [...document.querySelectorAll(".nav-item")].map((n) => n.dataset.view);
  $("#palette-last-key").textContent = `⌘${Math.min(VIEW_KEYS.length, 9)}`;
}
let paletteItems = [];
let paletteSel = 0;
let paletteRuns = [];
let paletteSearch = [];
let paletteTimer = null;

function paletteCommands() {
  const cmds = [];
  VIEW_KEYS.forEach((k, i) => {
    if (i >= 9) return;
    cmds.push({ group: T("Ansichten"), label: VIEW_NAMES[k], icon: VIEW_ICONS[k], key: `⌘${i + 1}`, run: () => showView(k) });
  });
  const act = T("Aktionen");
  cmds.push(
    { group: act, label: T("Beispiel-Literaturliste prüfen"), icon: "i-verify", run: () => { showView("verify"); $("#verify-input").value = SAMPLE; runPreview(); startVerify(); } },
    { group: act, label: T("Prüfung starten"), hint: T("Text im Feld Prüfen"), icon: "i-verify", run: () => { showView("verify"); startVerify(); } },
    { group: act, label: T("Download starten"), hint: T("DOIs im Feld Download"), icon: "i-download", run: () => { showView("download"); startJob("download"); } },
    { group: act, label: T("Neues Projekt anlegen"), icon: "i-plus", run: () => { showView("projects"); setTimeout(() => $("#project-name").focus(), 80); } },
    ...(ROUTES.length ? [{ group: act, label: settings.extra_routes ? T("Zusatzwege ausschalten") : T("Zusatzwege einschalten"), icon: settings.extra_routes ? "i-shield" : "i-route",
      run: () => { syncToggle("extra_routes", !settings.extra_routes); persistSetting({ extra_routes: !settings.extra_routes }); } }] : []),
    { group: act, label: T("KI-Assistent verbinden"), icon: "i-plug", run: () => { showView("settings"); scrollMainTo($("#ai-card")); } },
    { group: act, label: T("Bibliothek scannen"), icon: "i-book", run: () => { showView("library"); scanLibrarySources(); } },
    { group: act, label: T("Einrichtung erneut starten"), icon: "i-user", run: () => openSetup(true) },
  );
  if (activeProject) {
    cmds.push({ group: act, label: T("Literaturverzeichnis des aktiven Projekts (BibTeX)"), icon: "i-quote",
      run: () => { window.location.href = `/api/projects/${encodeURIComponent(activeProject)}/bibliography?format=bibtex`; } });
  }
  const proj = T("Projekte");
  cmds.push({ group: proj, label: T("Kein Projekt"), hint: activeProject ? "" : T("aktiv"), icon: "i-folder", run: () => setActiveProject("") });
  projectList.forEach((p) => cmds.push({ group: proj, label: T("Projekt: {name}", { name: p.name }),
    hint: p.id === activeProject ? T("aktiv") : TN(p.count, "{n} Quelle", "{n} Quellen"), icon: "i-folder", run: () => setActiveProject(p.id) }));
  [["system", T("Darstellung wie macOS"), "i-sun"], ["light", T("Darstellung hell"), "i-sun"], ["dark", T("Darstellung dunkel"), "i-moon"]]
    .forEach(([m, label, ic]) => cmds.push({ group: T("Darstellung"), label, icon: ic, run: () => applyTheme(m) }));
  [["en", "English"], ["de", "Deutsch"]].forEach(([lang, name]) => cmds.push({ group: T("Sprache"), label: `${T("Sprache")}: ${name}`,
    hint: lang === LANG ? T("aktiv") : "", icon: "i-user", run: () => switchLanguage(lang, false) }));
  paletteRuns.slice(0, 5).forEach((r) => cmds.push({ group: T("Letzte Läufe"), label: T("Lauf #{id}", { id: r.id }) + " " + runKind(r.mode),
    hint: r.total ? TN(r.total, "{n} Quelle", "{n} Quellen") : "", icon: "i-clock", run: () => viewHistory(r.id) }));
  paletteSearch.forEach((h) => cmds.push({ group: T("Bibliothek"), label: h.title || h.doi, hint: h.doi, icon: h.filename ? "i-file" : "i-book",
    run: () => window.open("https://doi.org/" + h.doi, "_blank") }));
  return cmds;
}

function scoreMatch(label, q) {
  if (!q) return 1;
  const l = label.toLowerCase();
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.every((w) => l.includes(w))) return 0;
  return l.startsWith(words[0]) ? 3 : (l.includes(" " + words[0]) ? 2 : 1);
}

function renderPalette() {
  const q = $("#palette-input").value.trim();
  const list = $("#palette-list");
  paletteItems = paletteCommands()
    .map((c, i) => ({ ...c, s: c.group === T("Bibliothek") ? 1 : scoreMatch(`${c.label} ${c.hint || ""}`, q), i }))
    .filter((c) => c.s > 0)
    .sort((a, b) => (q ? b.s - a.s : 0) || a.i - b.i);
  paletteSel = Math.min(paletteSel, Math.max(0, paletteItems.length - 1));
  list.innerHTML = "";
  if (!paletteItems.length) {
    list.innerHTML = `<div class="palette-empty">${T("Nichts gefunden.")}</div>`;
    return;
  }
  let lastGroup = null;
  paletteItems.forEach((c, idx) => {
    if (!q && c.group !== lastGroup) {
      const g = document.createElement("div");
      g.className = "palette-group";
      g.textContent = c.group;
      list.appendChild(g);
      lastGroup = c.group;
    }
    const el = document.createElement("div");
    el.className = "palette-item";
    el.id = `pi-${idx}`;
    el.setAttribute("role", "option");
    el.setAttribute("aria-selected", String(idx === paletteSel));
    const lab = document.createElement("span");
    lab.className = "lbl";
    lab.textContent = c.label;
    if (c.hint) { const sm = document.createElement("small"); sm.textContent = c.hint; lab.appendChild(sm); }
    el.append(icon(c.icon || "i-chevron"), lab);
    if (c.key) { const k = document.createElement("kbd"); k.textContent = c.key; el.appendChild(k); }
    el.onmousemove = () => { if (paletteSel !== idx) { paletteSel = idx; markPalette(); } };
    el.onclick = () => runPalette(idx);
    list.appendChild(el);
  });
  markPalette();
}

function markPalette() {
  document.querySelectorAll(".palette-item").forEach((el, i) => el.setAttribute("aria-selected", String(i === paletteSel)));
  const cur = $(`#pi-${paletteSel}`);
  if (cur) {
    cur.scrollIntoView({ block: "nearest" });
    $("#palette-input").setAttribute("aria-activedescendant", cur.id);
  }
}

function runPalette(idx) {
  const c = paletteItems[idx];
  closePalette();
  if (c) setTimeout(() => c.run(), 30);
}

async function openPalette() {
  $("#palette").hidden = false;
  $("#palette-input").value = "";
  paletteSel = 0;
  paletteSearch = [];
  renderPalette();
  setTimeout(() => $("#palette-input").focus(), 20);
  try { paletteRuns = (await (await fetch("/api/history")).json()).summaries || []; renderPalette(); } catch (e) { /* ignore */ }
}

function closePalette() {
  $("#palette").hidden = true;
}

function paletteInput() {
  paletteSel = 0;
  renderPalette();
  clearTimeout(paletteTimer);
  const q = $("#palette-input").value.trim();
  if (q.length < 3) { paletteSearch = []; return; }
  paletteTimer = setTimeout(async () => {
    try {
      paletteSearch = (await (await fetch("/api/library/search?" + new URLSearchParams({ q, limit: 6 }))).json()).results || [];
      renderPalette();
    } catch (e) { /* ignore */ }
  }, 180);
}

// ---------------------------------------------------------------------------
// Updates
// ---------------------------------------------------------------------------
let pendingUpdate = null;

async function checkUpdate(manual) {
  if (manual) $("#upd-status").textContent = T("Suche …");
  let u;
  try { u = await (await fetch("/api/update" + (manual ? "?force=1" : ""))).json(); } catch (e) { return; }
  $("#upd-current").textContent = T("Version {v}", { v: u.current });
  $("#set-update-url").placeholder = u.default_url || T("leer = keine Update-Prüfung");
  let status = T("Du hast die neueste Version.");
  if (!u.configured) status = T("Keine Update-Adresse hinterlegt (Einstellungen › Erweitert).");
  else if (u.error) status = u.error;
  else if (u.available) status = T("Version {v} ist verfügbar.", { v: u.latest });
  if (!updateBusy) $("#upd-status").textContent = status;
  if (u.available) pendingUpdate = u;
  $("#upd-install").hidden = !(u.available && canInstallUpdate());
  let dismissed = null;
  try { dismissed = localStorage.getItem("benthic-update-dismissed"); } catch (e) { /* privat */ }
  if (u.available && canInstallUpdate() && window.benthicShell && window.benthicShell.autoUpdate) { startUpdate(); return; }
  if (u.available && !updateBusy && (manual || dismissed !== u.latest)) {
    $("#ub-title").textContent = T("Benthic {v} ist da", { v: u.latest });
    $("#ub-get").textContent = canInstallUpdate() ? T("Jetzt aktualisieren") : T("Herunterladen");
    $("#ub-notes").textContent = (u.notes || []).join(" · ") || (canInstallUpdate()
      ? T("Ein Klick lädt die neue Version, prüft sie und startet Benthic neu.")
      : T("Neue Version herunterladen und wie gewohnt in den Programme-Ordner ziehen."));
    $("#update-banner").hidden = false;
  }
}

// Aktualisieren aus der App: Das Backend lädt das DMG und prüft die Prüfsumme
// (/api/update/download), die native App hängt es ein, prüft die App darin, tauscht sie aus und
// startet neu (BenthicApp.swift, benthicUpdate). Im Browser bleibt es beim Link.
let updateBusy = false;
const updateCallbacks = {};
function canInstallUpdate() {
  try { return !!window.webkit.messageHandlers.benthicUpdate; } catch (e) { return false; }
}
window.benthicUpdateDone = (id, ok, code) => {
  const cb = updateCallbacks[id];
  delete updateCallbacks[id];
  if (cb) cb(ok, code);
};
function updateCall(msg, cb) {
  const id = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  updateCallbacks[id] = cb;
  window.webkit.messageHandlers.benthicUpdate.postMessage({ id, ...msg });
}
function setUpdateUi(text, busy) {
  $("#update-banner").hidden = false;
  $("#ub-notes").textContent = text;
  $("#upd-status").textContent = text;
  ["#ub-get", "#ub-later", "#upd-install", "#upd-check"].forEach((s) => { if ($(s)) $(s).disabled = busy; });
}
async function startUpdate() {
  if (!pendingUpdate || updateBusy) return;
  if (!canInstallUpdate()) { window.open(pendingUpdate.url, "_blank"); return; }
  updateBusy = true;
  const failed = T("Das Update ließ sich nicht laden. Bitte später noch einmal versuchen.");
  const mb = (n) => Math.max(0, Math.round((n || 0) / 1048576));
  setUpdateUi(T("Lade Benthic {v} …", { v: pendingUpdate.latest }), true);
  let st;
  try { st = await (await fetch("/api/update/download", { method: "POST" })).json(); } catch (e) { st = { state: "error", error: failed }; }
  while (st.state === "downloading") {
    setUpdateUi(st.total ? T("Lade Benthic {v} … {a} von {b} MB", { v: st.version, a: mb(st.received), b: mb(st.total) })
      : T("Lade Benthic {v} …", { v: st.version }), true);
    await new Promise((r) => setTimeout(r, 400));
    try { st = await (await fetch("/api/update/status")).json(); } catch (e) { /* nächster Versuch */ }
  }
  if (st.state !== "ready") {
    updateBusy = false;
    setUpdateUi(st.error || failed, false);
    return;
  }
  setUpdateUi(T("Prüfe und installiere Benthic {v} …", { v: st.version }), true);
  updateCall({ action: "install", path: st.path, version: st.version }, (ok, code) => {
    if (ok) { setUpdateUi(T("Benthic startet neu …"), true); return; }
    updateBusy = false;
    const msg = {
      "not-writable": T("Benthic darf sich an seinem Ort nicht selbst ersetzen. Das Update wird im Finder geöffnet: Benthic in den Programme-Ordner ziehen."),
      location: T("Benthic läuft nicht aus dem Programme-Ordner. Das Update wird im Finder geöffnet: Benthic in den Programme-Ordner ziehen."),
    }[code] || T("Das Update ließ sich nicht installieren ({code}). Es wird im Finder geöffnet: Benthic in den Programme-Ordner ziehen.", { code });
    setUpdateUi(msg, false);
    updateCall({ action: "open", path: st.path }, () => {});
  });
}

// ---------------------------------------------------------------------------
// Events + init
// ---------------------------------------------------------------------------
$("#settings-save-btn").onclick = saveSettings;
$("#settings-save-top").onclick = saveSettings;
$("#library-scan-btn").onclick = scanLibrarySources;
$("#library-clear-btn").onclick = clearLibrary;
$("#check-library-btn").onclick = checkLibrary;
$("#download-btn").onclick = () => startJob("download");
$("#check-oa-btn").onclick = () => startJob("check");
$("#cancel-btn").onclick = cancelJob;
$("#export-csv-btn").onclick = () => exportJob("csv");
$("#export-xlsx-btn").onclick = () => exportJob("xlsx");
$("#doi-input").addEventListener("input", updateCounter);
$("#files-refresh-btn").onclick = loadFiles;
$("#history-refresh-btn").onclick = loadHistory;
$("#mode-badge").onclick = () => showView("settings");
$("#setup-rerun-btn").onclick = () => openSetup(true);
$("#verify-btn").onclick = startVerify;
$("#verify-sample").onclick = () => { $("#verify-input").value = SAMPLE; runPreview(); };
$("#watch-file-btn").onclick = chooseWatchFile;
document.querySelectorAll("#settings-nav button").forEach((b) => { b.onclick = () => showSettingsSection(b.dataset.section); });
document.querySelectorAll("#report-tabs button").forEach((b) => { b.onclick = () => showReportTab(b.dataset.tab); });
new MutationObserver(updateReportTabs).observe($("#verify-results"), { attributes: true, subtree: true, attributeFilter: ["hidden"] });
$("#usage-refresh").onclick = loadUsage;
$("#batch-pick").onclick = startBatch;
$("#batch-select").addEventListener("change", (e) => showBatch(e.target.value));
$("#batch-cancel").onclick = () => batchId && fetch(`/api/batch/${encodeURIComponent(batchId)}/cancel`, { method: "POST" });
$("#batch-csv").onclick = () => { if (batchId) location.href = `/api/batch/${encodeURIComponent(batchId)}/export?format=csv`; };
$("#batch-xlsx").onclick = () => { if (batchId) location.href = `/api/batch/${encodeURIComponent(batchId)}/export?format=xlsx`; };
$("#batch-pdf").onclick = () => batchId && saveReportPath(`batch/${encodeURIComponent(batchId)}`, `${T("Stapelprüfung")} ${(batchData || {}).name || ""}`);
$("#batch-rerun").onclick = async () => { if (!batchId) return; await fetch(`/api/batch/${encodeURIComponent(batchId)}/rerun`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); showBatch(batchId); };
$("#batch-only-problems").addEventListener("change", renderBatchRows);
document.querySelectorAll("#view-batch th[data-sort]").forEach((th) => {
  th.addEventListener("click", () => {
    const key = th.dataset.sort;
    batchSort = { key, dir: batchSort.key === key ? -batchSort.dir : (key === "name" ? 1 : -1) };
    renderBatchRows();
  });
});
$("#verify-input").addEventListener("input", () => { delete $("#verify-input").dataset.name; schedulePreview(); });
$("#verify-input").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) startVerify(); });
$("#verify-file").addEventListener("change", (e) => { loadFilesInto(e.target.files); e.target.value = ""; });
$("#verify-fetch-btn").onclick = fetchVerified;
$("#verify-copy-btn").onclick = copyDois;
$("#verify-zotero-btn").onclick = () => sendToZotero({ dois: loadableDois() }, $("#verify-zotero-btn"));
$("#zotero-scan-btn").onclick = scanZotero;
const REPORT_FILE = LANG === "de" ? "benthic-pruefbericht" : "benthic-check-report";
$("#verify-csv-btn").onclick = () => exportRows(verifyResults, "csv", REPORT_FILE);
$("#verify-xlsx-btn").onclick = () => exportRows(verifyResults, "xlsx", REPORT_FILE);
$("#verify-report-btn").onclick = () => verifyJobId && saveReport(verifyJobId, verifyDoc && verifyDoc.name);
(() => {
  const zone = $("#verify-drop");
  let depth = 0;
  zone.addEventListener("dragenter", (e) => { e.preventDefault(); depth++; zone.classList.add("dragging"); });
  zone.addEventListener("dragover", (e) => e.preventDefault());
  zone.addEventListener("dragleave", () => { if (--depth <= 0) { depth = 0; zone.classList.remove("dragging"); } });
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    depth = 0;
    zone.classList.remove("dragging");
    const fl = e.dataTransfer && e.dataTransfer.files;
    if (fl && fl.length) loadFilesInto(fl);
    else if (e.dataTransfer) { $("#verify-input").value = e.dataTransfer.getData("text"); runPreview(); }
  });
  // Dateien außerhalb der Zone nicht im Fenster öffnen.
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => e.preventDefault());
})();
$("#project-select").addEventListener("change", (e) => {
  if (e.target.value === "__new") {
    e.target.value = activeProject || "";
    showView("projects");
    setTimeout(() => $("#project-name").focus(), 80);
    return;
  }
  setActiveProject(e.target.value);
});
$("#project-create").onclick = createProject;
$("#project-name").addEventListener("keydown", (e) => { if (e.key === "Enter") createProject(); });
$("#pd-delete").onclick = deleteProject;
$("#pd-export").onclick = exportBibliography;
$("#pd-recheck").onclick = recheckProject;
$("#pd-watch-now").onclick = watchProjectNow;
$("#pd-watch-toggle").addEventListener("change", async (e) => {
  if (!shownProject) return;
  await fetch(`/api/projects/${encodeURIComponent(shownProject)}`, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ watch: e.target.checked }) });
  loadWatch();
});
$("#pd-zotero").onclick = () => shownProject && sendToZotero({ project: shownProject }, $("#pd-zotero"));
$("#pd-fetch").onclick = fetchProjectMissing;
$("#pd-activate").onclick = async () => { await setActiveProject($("#project-detail").dataset.id); showProject($("#project-detail").dataset.id, false); };
$("#ai-status").onclick = () => { showView("settings"); setTimeout(() => scrollMainTo($("#ai-card")), 60); };
document.querySelectorAll("[data-copy]").forEach((b) => { b.onclick = () => copyText(b.dataset.copy); });
$("#upd-check").onclick = () => checkUpdate(true);
$("#ub-get").onclick = startUpdate;
$("#upd-install").onclick = startUpdate;
$("#ub-later").onclick = () => {
  $("#update-banner").hidden = true;
  try { if (pendingUpdate) localStorage.setItem("benthic-update-dismissed", pendingUpdate.latest); } catch (e) { /* privat */ }
};
$("#cmdk-btn").onclick = openPalette;
$("#palette-input").addEventListener("input", paletteInput);
$("#palette-input").addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown") { e.preventDefault(); paletteSel = Math.min(paletteItems.length - 1, paletteSel + 1); markPalette(); }
  else if (e.key === "ArrowUp") { e.preventDefault(); paletteSel = Math.max(0, paletteSel - 1); markPalette(); }
  else if (e.key === "Enter") { e.preventDefault(); runPalette(paletteSel); }
  else if (e.key === "Escape") { e.preventDefault(); closePalette(); }
});
$("#palette").addEventListener("mousedown", (e) => { if (e.target.id === "palette") closePalette(); });
document.addEventListener("keydown", (e) => {
  const mod = e.metaKey || e.ctrlKey;
  if (mod && e.key.toLowerCase() === "k") {
    e.preventDefault();
    if ($("#palette").hidden) openPalette(); else closePalette();
    return;
  }
  if (mod && /^[1-9]$/.test(e.key) && $("#setup").hidden && VIEW_KEYS[Number(e.key) - 1]) {
    e.preventDefault();
    closePalette();
    showView(VIEW_KEYS[Number(e.key) - 1]);
  }
});
$("#ov-project").addEventListener("change", loadOverview);
document.querySelectorAll("#ov-range button").forEach((b) => {
  b.onclick = () => {
    ovDays = b.dataset.days;
    document.querySelectorAll("#ov-range button").forEach((x) => x.setAttribute("aria-checked", String(x === b)));
    loadOverview();
  };
});
$("#ov-start").onclick = () => { showView("verify"); $("#verify-input").value = SAMPLE; runPreview(); startVerify(); };
$("#su-next").onclick = setupNext;
$("#su-back").onclick = () => goStep(Math.max(0, setup.step - 1), true);
$("#su-sample").onclick = () => { setup.sample = true; setupNext(); };
$("#su-proxy-test").onclick = () => {
  const p = $("#su-proxy").value.trim();
  if (!/^https?:\/\//.test(p)) return setupFail(T("Bitte zuerst einen Proxy-Link eintragen."), "proxy");
  window.open(p + "10.1038/s41586-020-2649-2", "_blank");
};
$("#su-folder-pick").onclick = async () => {
  const path = await pickPath("folder", [], $("#su-folder").value.trim());
  if (path) $("#su-folder").value = path;
};
$("#su-file-pick").onclick = async () => {
  const path = await pickPath("file", ["bib", "ris", "md", "txt", "sqlite", "nbib"], "~");
  if (path) { $("#su-file").value = path; setup.choice = "file"; renderChoices(); }
};
document.addEventListener("keydown", (e) => {
  if ($("#setup").hidden) return;
  if (e.key === "Enter" && e.target.tagName !== "BUTTON") { e.preventDefault(); setupNext(); }
});
$("#retry-failed-btn").onclick = () => {
  if (!lastFailed.length) return;
  $("#doi-input").value = lastFailed.join("\n");
  updateCounter();
  startJob("download");
};

document.querySelectorAll(".nav-item").forEach((btn) => {
  btn.onclick = () => showView(btn.dataset.view);
});

// Download view: saved at once. Settings view: saved with the Save button.
[["quick-routes", "extra_routes"], ["quick-skip", "skip_existing"], ["quick-prefer", "prefer_published"]]
  .forEach(([id, key]) => {
    $("#" + id).addEventListener("change", (e) => {
      syncToggle(key, e.target.checked);
      persistSetting({ [key]: e.target.checked });
    });
  });
[["set-routes", "extra_routes"], ["set-skip", "skip_existing"], ["set-prefer", "prefer_published"]]
  .forEach(([id, key]) => {
    $("#" + id).addEventListener("change", (e) => {
      syncToggle(key, e.target.checked);
      settings[key] = e.target.checked;
      renderRoute();
      renderMode();
    });
  });
$("#ranking-add-btn").onclick = importRanking;
$("#licenses").addEventListener("toggle", async (e) => {
  const pre = $("#licenses-text");
  if (!e.target.open || pre.textContent) return;
  try { pre.textContent = (await (await fetch("/api/licenses")).json()).text; } catch (err) { pre.textContent = T("Nicht verfügbar."); }
});
$("#quick-concurrency").addEventListener("change", (e) => {
  const v = Math.max(1, Math.min(8, parseInt(e.target.value, 10) || 3));
  $("#set-concurrency").value = v;
  persistSetting({ concurrency: v });
});

updateViewKeys();
checkHealth();
loadSettings();
loadRoutes();
loadLibrary();
loadHistory();
loadFiles();
updateCounter();
// Start: wer schon Läufe hat, landet in der Übersicht, sonst in „Prüfen“.
const initialView = (location.hash || "").replace("#", "");
showView(initialView || "verify");
if (!initialView) {
  fetch("/api/history").then((r) => r.json()).then((j) => {
    if ((j.summaries || []).length && document.querySelector("#view-verify.active") && !$("#verify-input").value) showView("overview");
  }).catch(() => {});
}
let resumeSetup = false;
try { resumeSetup = sessionStorage.getItem("pd-setup-resume") === "1"; sessionStorage.removeItem("pd-setup-resume"); } catch (e) { /* ohne Speicher */ }
openSetup(resumeSetup);
tellShellLanguage();
document.querySelectorAll("#su-lang [data-lang]").forEach((b) => { b.onclick = () => switchLanguage(b.dataset.lang, true); });
document.querySelectorAll("#set-lang [data-lang]").forEach((b) => { b.onclick = () => switchLanguage(b.dataset.lang, false); });
renderLangChoice();
loadProjects();
pollActivity();
setTimeout(() => checkUpdate(false), 1500);
loadWatch();
pollWatchFiles();
setTimeout(() => loadWatch(), 3 * 60 * 1000);   // die erste Prüfung nach dem Start läuft nach 90 s
setInterval(() => loadWatch(), 10 * 60 * 1000);  // Funde der täglichen Prüfung melden, solange die App offen ist

// Optional deep link to a running or finished job (?job=<id>).
const jobParam = new URLSearchParams(location.search).get("job");
if (jobParam) {
  currentJobId = jobParam;
  $("#progress-card").hidden = false;
  $("#results-card").hidden = false;
  setRunning(true);
  pollJob(jobParam, "download");
}
