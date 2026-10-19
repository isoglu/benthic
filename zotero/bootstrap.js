/* Benthic für Zotero 7: ausgewählte Einträge mit Benthic prüfen, die Urteile kommen als Schlagwörter zurück.
 *
 * Rechtsklick auf Einträge, „Mit Benthic prüfen“. Das Plug-in schickt DOI oder Angabe jedes Eintrags an die laufende
 * Benthic-App (127.0.0.1:8327), wartet auf das Ergebnis und setzt je Eintrag ein Schlagwort wie „Benthic: Bestätigt“
 * oder „Benthic: Zurückgezogen“ (ältere Benthic-Schlagwörter werden ersetzt). Nichts geht an einen Server von Benthic;
 * geprüft wird wie in der App bei Crossref, OpenAlex und den übrigen Quellen. Läuft Benthic nicht, öffnet das Plug-in
 * die App und bittet, es noch einmal zu versuchen.
 */
"use strict";

var BenthicZotero;

const BASE = "http://127.0.0.1:8327";
const TAG_PREFIX = "Benthic: ";
const VERDICTS = {
  verified: ["Bestätigt", "Verified"], corrected: ["DOI korrigiert", "DOI corrected"], partial: ["Weicht ab", "Deviates"],
  uncertain: ["Unsicher", "Uncertain"], mismatch: ["Passt nicht", "Mismatch"], not_found: ["Nicht auffindbar", "Not found"],
  retracted: ["Zurückgezogen", "Retracted"],
};
const german = () => String(Zotero.locale || "").toLowerCase().startsWith("de");
const S = (de, en) => (german() ? de : en);

function startup({ rootURI }) {
  BenthicZotero = new BenthicPlugin(rootURI);
  for (const win of Zotero.getMainWindows()) BenthicZotero.addToWindow(win);
}

function onMainWindowLoad({ window }) {
  if (BenthicZotero) BenthicZotero.addToWindow(window);
}

function onMainWindowUnload({ window }) {
  if (BenthicZotero) BenthicZotero.removeFromWindow(window);
}

function shutdown() {
  if (!BenthicZotero) return;
  for (const win of Zotero.getMainWindows()) BenthicZotero.removeFromWindow(win);
  BenthicZotero = undefined;
}

function install() {}
function uninstall() {}

class BenthicPlugin {
  constructor(rootURI) {
    this.icon = rootURI + "icon.png";
  }

  addToWindow(win) {
    const doc = win.document;
    const menu = doc.getElementById("zotero-itemmenu");
    if (!menu || doc.getElementById("benthic-check-items")) return;
    const sep = doc.createXULElement("menuseparator");
    sep.id = "benthic-separator";
    const item = doc.createXULElement("menuitem");
    item.id = "benthic-check-items";
    item.setAttribute("label", S("Mit Benthic prüfen", "Check with Benthic"));
    item.addEventListener("command", () => this.check(win));
    menu.append(sep, item);
  }

  removeFromWindow(win) {
    for (const id of ["benthic-separator", "benthic-check-items"]) {
      const node = win.document.getElementById(id);
      if (node) node.remove();
    }
  }

  // DOI, sonst eine Angabe aus Autor:innen, Jahr, Titel und Zeitschrift, wie Benthic sie auch eingefügt prüft.
  static reference(item) {
    const extra = item.getField("extra") || "";
    const doi = String(item.getField("DOI") || "").trim() || ((/^DOI:\s*(\S+)/im.exec(extra) || [])[1] || "");
    if (doi) return doi;
    const names = item.getCreators().map((c) => c.lastName || c.name).filter(Boolean);
    const year = String(item.getField("date", true, true) || "").slice(0, 4) || "n.d.";
    const venue = item.getField("publicationTitle") || item.getField("bookTitle") || item.getField("publisher") || "";
    const who = names.slice(0, 3).join(", ") + (names.length > 3 ? " et al." : "");
    return `${who} (${year}). ${item.getField("title")}. ${venue}`.trim();
  }

  async api(method, path, body) {
    const xhr = await Zotero.HTTP.request(method, BASE + path, {
      body: body ? JSON.stringify(body) : undefined,
      headers: { "Content-Type": "application/json", "X-Benthic-Client": "zotero" },
      responseType: "json",
      timeout: 30000,
    });
    return xhr.response;
  }

  async check(win) {
    const items = win.ZoteroPane.getSelectedItems().filter((i) => i.isRegularItem());
    const progress = new Zotero.ProgressWindow({ closeOnClick: true });
    progress.changeHeadline("Benthic");
    if (!items.length) {
      progress.addDescription(S("Bitte Einträge auswählen (keine Notizen oder Anhänge).", "Please select items (not notes or attachments)."));
      progress.show();
      progress.startCloseTimer(5000);
      return;
    }
    const line = new progress.ItemProgress(this.icon, S(`Prüft ${items.length} Einträge …`, `Checking ${items.length} items …`));
    progress.show();
    let job;
    try {
      // Kein Projekt: eine Auswahl aus Zotero soll das aktive Benthic-Projekt nicht füllen
      job = await this.api("POST", "/api/verify", { text: items.map(BenthicPlugin.reference).join("\n"), project: "" });
    } catch (e) {
      line.setError();
      line.setText(S("Benthic läuft nicht. Die App wird geöffnet; dann bitte noch einmal versuchen.",
        "Benthic is not running. The app is opening; please try again then."));
      Zotero.launchURL("benthic://");
      progress.startCloseTimer(8000);
      return;
    }
    let state = null;
    for (let i = 0; i < 400; i++) {  // höchstens zehn Minuten
      await Zotero.Promise.delay(1500);
      try { state = await this.api("GET", `/api/jobs/${encodeURIComponent(job.job_id)}`); } catch (e) { continue; }
      line.setProgress(state.total ? Math.round((100 * (state.done || 0)) / state.total) : 0);
      if (state.status !== "running") break;
    }
    const results = (state && state.results) || [];
    const byOrder = new Map(results.map((r) => [r.order, r]));
    const byDoi = new Map(results.filter((r) => r.doi).map((r) => [String(r.doi).toLowerCase(), r]));
    const counts = {};
    for (const [index, item] of items.entries()) {
      const ref = BenthicPlugin.reference(item);
      const r = (results.length === items.length && byOrder.get(index)) || byDoi.get(ref.toLowerCase());
      if (!r || !VERDICTS[r.verdict]) continue;
      for (const tag of item.getTags()) {
        if (tag.tag.startsWith(TAG_PREFIX)) item.removeTag(tag.tag);
      }
      item.addTag(TAG_PREFIX + VERDICTS[r.verdict][german() ? 0 : 1], 1);
      await item.saveTx();
      counts[r.verdict] = (counts[r.verdict] || 0) + 1;
    }
    const parts = Object.keys(VERDICTS).filter((v) => counts[v]).map((v) => `${counts[v]} ${VERDICTS[v][german() ? 0 : 1]}`);
    line.setProgress(100);
    line.setText(parts.length ? parts.join(" · ") : S("Kein Ergebnis zurückbekommen.", "No result came back."));
    if (counts.retracted || counts.not_found || counts.mismatch) {
      progress.addDescription(S("Die Einträge tragen jetzt ein Schlagwort „Benthic: …“. Den ganzen Prüfbericht zeigt Benthic im Verlauf.",
        "The items now carry a “Benthic: …” tag. The full check report is in Benthic's history."));
    }
    progress.startCloseTimer(10000);
  }
}
