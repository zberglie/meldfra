/**
 * <meldfra-widget> — knappen og arket. Shadow DOM, så portalens CSS ikke lekker inn og vår
 * ikke lekker ut. Farger og form styres med CSS-variabler på :root (se README).
 *
 * TRE STEG:
 *   1. SKRIV      — brukeren skriver én setning. Ingen skjema.
 *   2. INTERVJU   — HØYST 2 spørsmål, ett om gangen, med hurtigsvar. Null spørsmål er vanlig.
 *   3. GODKJENN   — KI-utkastet som REDIGERBAR tekst. Eierskapet skapes av redigeringen.
 *
 * PRINSIPPER:
 *   • «Bare send det jeg skrev» er ALLTID synlig under intervjuet. En tynn rapport slår en
 *     avbrutt rapport.
 *   • Brukeren SER nøyaktig hva som sendes: «Se» åpner selve bildet og selve hendelseslista.
 *   • «Send» og «Avbryt» har samme visuelle vekt (EDPB om villedende design).
 *   • KI-merking ved første kontaktpunkt (AI Act art. 50).
 *   • Feiler KI-en, går vi rett til steg 3 med brukerens egne ord.
 *
 * Arket rendres på nytt ved HANDLINGER, aldri ved tastetrykk — ellers mister feltet fokus.
 */
import { clearCrumbs, formatCrumb, getCrumbs, type Crumb } from "./breadcrumbs";
import { STRINGS, type Lang, type Strings } from "./i18n";
import { capture, type MaskMode } from "./screenshot";

export interface MeldFraOptions {
  /** Hvor routeren er montert. Standard: mappa scriptet ble lastet fra, ellers "/meldfra". */
  endpoint?: string;
  /** Ekstra headere per kall, f.eks. { Authorization: `Bearer ${token}` }. */
  headers?: () => Record<string, string> | Promise<Record<string, string>>;
  /** Standard "include" (cookie-basert innlogging på samme domene). */
  credentials?: RequestCredentials;
  lang?: Lang;
  appVersion?: string;
  position?: "bottom-left" | "bottom-right";
  /** false = ingen flytende knapp; verten kaller MeldFra.open() fra sin egen meny. */
  showButton?: boolean;
  /** false = aldri skjermbilde. */
  screenshot?: boolean;
  /** "marked" (standard): kun [data-meldfra-sensitive] + skjemafelter. "all-text": all tekst. */
  mask?: MaskMode;
  /** Stier (prefiks) der knappen skjules, f.eks. ["/kasse"]. */
  hideOn?: string[];
}

type Kind = "bug" | "idea";
type Slot = "expected" | "blocking" | "frequency";
type Question = { slot: Slot; text: string; chips: { label: string; value: string | boolean }[] };
type Draft = {
  kind: Kind; title: string; observed: string; expected?: string | null; steps?: string[] | null;
  blocking?: boolean | null; frequency?: string | null;
};
type Report = { id: number; kind: Kind; title: string; status: string; created_at: string | null };
type View = "write" | "interview" | "review" | "done" | "mine";

const CSS = `
:host { all: initial; }
* { box-sizing: border-box; font-family: var(--meldfra-font, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif); }
.fab { position: fixed; bottom: var(--meldfra-offset-bottom, 20px); z-index: 2147483000; height: 48px; padding: 0 16px;
  display: flex; align-items: center; gap: 8px; border: 0; border-radius: 999px; cursor: pointer; font-size: 14px; font-weight: 600;
  background: var(--meldfra-primary, #2f5d50); color: var(--meldfra-on-primary, #fff); box-shadow: 0 4px 14px rgba(0,0,0,.18); }
.fab.left { left: 16px; } .fab.right { right: 16px; }
.fab:focus-visible, button:focus-visible, textarea:focus-visible, input:focus-visible { outline: 3px solid var(--meldfra-focus, #f0b429); outline-offset: 2px; }
.dot { width: 9px; height: 9px; border-radius: 50%; background: var(--meldfra-badge, #e5484d); }
.scrim { position: fixed; inset: 0; z-index: 2147483001; display: flex; align-items: flex-end; justify-content: center; padding: 12px; background: rgba(0,0,0,.45); }
@media (min-width: 640px) { .scrim { align-items: center; } }
.sheet { width: 100%; max-width: 520px; max-height: 90vh; overflow-y: auto; border-radius: var(--meldfra-radius, 16px); padding: 20px;
  background: var(--meldfra-surface, #fff); color: var(--meldfra-text, #1b1f1d); box-shadow: 0 10px 40px rgba(0,0,0,.25); }
.stack > * + * { margin-top: 14px; }
h2 { margin: 0; font-size: 18px; } p { margin: 0; }
.muted { color: var(--meldfra-muted, #5b6560); font-size: 12px; }
.row { display: flex; gap: 8px; align-items: center; } .wrap { flex-wrap: wrap; } .grow { flex: 1; } .end { margin-left: auto; }
textarea, input[type=text] { width: 100%; border: 1px solid var(--meldfra-border, #d5dbd8); border-radius: 12px; padding: 10px 12px; font-size: 14px;
  background: var(--meldfra-field, #f4f6f5); color: inherit; resize: vertical; }
label > span { display: block; font-size: 11px; letter-spacing: .03em; margin-bottom: 4px; color: var(--meldfra-muted, #5b6560); }
.btn { height: 44px; padding: 0 18px; border-radius: 999px; font-size: 14px; font-weight: 600; cursor: pointer; border: 1px solid transparent; }
.btn.primary { background: var(--meldfra-primary, #2f5d50); color: var(--meldfra-on-primary, #fff); }
.btn.ghost { background: transparent; color: inherit; border-color: var(--meldfra-border, #d5dbd8); }
.btn:disabled { opacity: .6; cursor: default; }
.chip { height: 40px; padding: 0 14px; border-radius: 999px; border: 1px solid var(--meldfra-border, #d5dbd8); cursor: pointer; font-size: 14px;
  background: var(--meldfra-field, #f4f6f5); color: inherit; }
.chip[aria-pressed=true] { background: var(--meldfra-primary, #2f5d50); color: var(--meldfra-on-primary, #fff); border-color: transparent; }
.link { background: none; border: 0; padding: 0; color: var(--meldfra-link, #2f5d50); text-decoration: underline; cursor: pointer; font-size: 12px; }
.bubble { padding: 12px; border-radius: 16px 16px 16px 4px; background: var(--meldfra-bubble, #e3eee9); font-size: 14px; }
.badge { font-size: 11px; padding: 2px 8px; border-radius: 999px; background: var(--meldfra-field, #f4f6f5); }
.box { border-radius: 12px; padding: 12px; background: var(--meldfra-field, #f4f6f5); }
.box > * + * { margin-top: 10px; }
.consent { display: flex; gap: 8px; align-items: flex-start; font-size: 13px; }
.consent input { margin-top: 2px; width: 16px; height: 16px; accent-color: var(--meldfra-primary, #2f5d50); }
.crumbs { max-height: 160px; overflow-y: auto; font: 11px/1.5 ui-monospace, monospace; padding: 8px; border-radius: 8px; background: var(--meldfra-surface, #fff); }
img.shot { width: 100%; max-height: 220px; object-fit: cover; object-position: top; border-radius: 8px; border: 1px solid var(--meldfra-border, #d5dbd8); }
.err { color: var(--meldfra-error, #c62828); font-size: 12px; }
.center { text-align: center; padding: 16px 0; } .big { font-size: 32px; }
ul.reports { list-style: none; padding: 0; margin: 0; } ul.reports li { padding: 10px 0; border-bottom: 1px solid var(--meldfra-border, #d5dbd8); }
.status { font-size: 11px; font-weight: 600; } .status.fikset, .status.bekreftet { color: var(--meldfra-success, #2e7d32); }
@media (prefers-color-scheme: dark) {
  .sheet { background: var(--meldfra-surface-dark, #1d2321); color: var(--meldfra-text-dark, #e6ebe8); }
  textarea, input[type=text], .chip, .box, .badge { background: var(--meldfra-field-dark, #2a322f); border-color: #3a4440; }
  .crumbs { background: var(--meldfra-surface-dark, #1d2321); } .bubble { background: #2c3d36; }
  .muted, label > span { color: #a7b1ac; } .link { color: #8fc7b3; }
}
`;

export class MeldFraWidget extends HTMLElement {
  private opts: Required<Omit<MeldFraOptions, "headers" | "appVersion">> & Pick<MeldFraOptions, "headers" | "appVersion">;
  private t: Strings;
  private root: ShadowRoot;
  private open = false;
  private view: View = "write";
  private kind: Kind = "bug";
  private observed = "";
  private answers: { expected?: string; blocking?: boolean; frequency?: string } = {};
  private asked: Slot[] = [];
  private question: Question | null = null;
  private freeAnswer = "";
  private draft: Draft | null = null;
  private stepsText = "";
  private consent = { screenshot: false, breadcrumbs: true };
  private shot: string | null = null;
  private crumbs: Crumb[] = [];
  private route = "/";
  private showShot = false;
  private showCrumbs = false;
  private busy = false;
  private err = "";
  private doneId: number | null = null;
  private reports: Report[] = [];
  private hasFixed = false;
  private returnFocus: HTMLElement | null = null;

  constructor(opts: MeldFraOptions) {
    super();
    this.opts = {
      endpoint: (opts.endpoint || "/meldfra").replace(/\/$/, ""),
      credentials: opts.credentials ?? "include",
      lang: opts.lang && STRINGS[opts.lang] ? opts.lang : "nb",
      position: opts.position ?? "bottom-left",
      showButton: opts.showButton ?? true,
      screenshot: opts.screenshot ?? true,
      mask: opts.mask ?? "marked",
      hideOn: opts.hideOn ?? [],
      headers: opts.headers,
      appVersion: opts.appVersion,
    };
    this.t = STRINGS[this.opts.lang];
    this.setAttribute("data-meldfra-ignore", "");
    this.root = this.attachShadow({ mode: "open" });
    window.addEventListener("popstate", () => this.render());
  }

  connectedCallback() {
    this.render();
    void this.refreshMine(true);
  }

  // ── API mot serveren ───────────────────────────────────────────────────────────────────
  private async call(method: string, path: string, body?: unknown) {
    const extra = this.opts.headers ? await this.opts.headers() : {};
    const res = await fetch(`${this.opts.endpoint}${path}`, {
      method, credentials: this.opts.credentials,
      headers: { ...(body !== undefined ? { "Content-Type": "application/json" } : {}), ...extra },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    return { ok: res.ok, status: res.status, data };
  }

  private errorFrom(r: { status: number; data: { detail?: unknown } }, fallback: string) {
    if (r.status === 401) return this.t.errLogin;
    return typeof r.data?.detail === "string" ? r.data.detail : fallback;
  }

  /** Henter «Mine rapporter». `quiet` = kun for å vise prikken på knappen (fikset, ikke kvittert). */
  private async refreshMine(quiet = false) {
    try {
      const r = await this.call("GET", "/reports");
      if (!r.ok) return;
      this.reports = r.data.reports || [];
      this.hasFixed = this.reports.some((x) => x.status === "fikset");
      if (!quiet || !this.open) this.render();
    } catch {
      /* ikke innlogget / offline — knappen virker likevel */
    }
  }

  // ── Åpne / lukke ───────────────────────────────────────────────────────────────────────
  async openSheet(view: View = "write") {
    if (this.open || this.busy) return;
    this.returnFocus = document.activeElement as HTMLElement | null;
    this.busy = true;
    this.render();
    try {
      // Rekkefølgen er kritisk: fang skjermen mens den fortsatt viser det brukeren så.
      this.shot = this.opts.screenshot && view === "write" ? await capture(this.opts.mask) : null;
    } finally {
      this.busy = false;
    }
    this.crumbs = getCrumbs();
    this.route = location.pathname;
    this.open = true;
    this.view = view;
    this.render();
    if (view === "mine") void this.refreshMine();
  }

  private close() {
    if (this.busy) return;
    const wasDone = this.doneId !== null;
    this.open = false;
    Object.assign(this, {
      view: "write", kind: "bug", observed: "", answers: {}, asked: [], question: null, freeAnswer: "",
      draft: null, stepsText: "", consent: { screenshot: false, breadcrumbs: true }, shot: null,
      showShot: false, showCrumbs: false, err: "", doneId: null,
    });
    // Sporet nullstilles etter en innsendt rapport: neste rapport skal handle om det som skjer
    // FRA NÅ. Avbryter brukeren, beholdes sporet — feilen har jo ikke blitt meldt.
    if (wasDone) clearCrumbs();
    this.render();
    this.returnFocus?.focus?.();
  }

  // ── Intervju ───────────────────────────────────────────────────────────────────────────
  private async runInterview(answers: typeof this.answers, asked: Slot[]) {
    this.busy = true;
    this.err = "";
    this.render();
    try {
      const r = await this.call("POST", "/interview", {
        observed: this.observed.trim(), kind: this.kind, answers, asked, breadcrumbs: this.crumbs, route: this.route,
      });
      if (!r.ok || !r.data.draft) {
        if (r.status === 401 || r.status === 429) {
          this.err = this.errorFrom(r, this.t.errConnect);
          return;
        }
        throw new Error("intervju feilet");
      }
      this.applyDraft(r.data.draft);
      const q: Question | undefined = r.data.question;
      if (q && this.draft?.kind !== "idea") {
        this.question = q;
        this.freeAnswer = "";
        this.view = "interview";
      } else {
        this.question = null;
        this.view = "review";
      }
    } catch {
      // KI-en skal aldri blokkere en rapport: gå videre med brukerens egne ord.
      const o = this.observed.trim();
      this.applyDraft({ kind: this.kind, title: o.split("\n")[0].slice(0, 120), observed: o });
      this.view = "review";
    } finally {
      this.busy = false;
      this.render();
    }
  }

  private applyDraft(d: Draft) {
    this.draft = { ...d };
    this.stepsText = (d.steps || []).join("\n");
    if (d.kind === "bug" || d.kind === "idea") this.kind = d.kind;
  }

  private answer(value?: string | boolean) {
    if (!this.question) return;
    const a = { ...this.answers };
    if (value !== undefined && value !== "") {
      if (this.question.slot === "expected") a.expected = String(value);
      else if (this.question.slot === "blocking") a.blocking = Boolean(value);
      else a.frequency = String(value);
    }
    const asked = [...this.asked, this.question.slot];
    this.answers = a;
    this.asked = asked;
    void this.runInterview(a, asked);
  }

  // ── Innsending ─────────────────────────────────────────────────────────────────────────
  private async submit() {
    const d = this.draft;
    if (!d) return;
    if (!d.observed.trim()) {
      this.err = this.t.errEmpty;
      this.render();
      return;
    }
    this.busy = true;
    this.err = "";
    this.render();
    try {
      const steps = this.stepsText.split("\n").map((s) => s.trim()).filter(Boolean);
      const r = await this.call("POST", "/reports", {
        kind: d.kind,
        title: d.title.trim() || d.observed.trim().split("\n")[0].slice(0, 120),
        observed: d.observed.trim(),
        expected: d.expected?.trim() || undefined,
        steps: steps.length ? steps : undefined,
        frequency: d.frequency || undefined,
        blocking: typeof d.blocking === "boolean" ? d.blocking : undefined,
        route: this.route,
        device: deviceInfo(this.opts.appVersion),
        consent: this.consent,
        // Vedlegg sendes KUN når bryteren står på — serveren ignorerer dem uansett uten samtykke.
        ...(this.consent.breadcrumbs ? { breadcrumbs: this.crumbs } : {}),
        ...(this.consent.screenshot && this.shot ? { screenshot: this.shot } : {}),
      });
      if (r.ok && r.data.ok) {
        this.doneId = r.data.id ?? null;
        this.view = "done";
      } else this.err = this.errorFrom(r, this.t.errSend);
    } catch {
      this.err = this.t.errConnect;
    } finally {
      this.busy = false;
      this.render();
    }
  }

  private async verify(id: number, fixed: boolean) {
    this.busy = true;
    this.render();
    try {
      await this.call("POST", `/reports/${id}/verify`, { fixed });
    } finally {
      this.busy = false;
    }
    await this.refreshMine();
  }

  private async deleteReport(id: number) {
    if (!confirm(this.t.deleteQ)) return;
    this.busy = true;
    try {
      await this.call("DELETE", `/reports/${id}`);
    } finally {
      this.busy = false;
    }
    await this.refreshMine();
  }

  // ── Rendering ──────────────────────────────────────────────────────────────────────────
  private render() {
    const t = this.t;
    const hidden = this.opts.hideOn.some((p) => location.pathname.startsWith(p));
    const parts: string[] = [`<style>${CSS}</style>`];
    if (this.opts.showButton && !hidden && !this.open) {
      parts.push(`<button class="fab ${this.opts.position === "bottom-right" ? "right" : "left"}" data-act="open"
        aria-label="${esc(t.fabAria)}" title="${esc(t.fabAria)}" ${this.busy ? "disabled" : ""}>
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 11.5a8.4 8.4 0 0 1-9 8.5 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7A8.4 8.4 0 0 1 12 3a8.5 8.5 0 0 1 9 8.5z"/></svg>
        <span>${esc(this.busy ? "…" : t.fab)}</span>${this.hasFixed ? `<span class="dot" aria-hidden="true"></span>` : ""}
      </button>`);
    }
    if (this.open) {
      parts.push(`<div class="scrim" data-act="scrim"><div class="sheet stack" role="dialog" aria-modal="true" aria-label="${esc(t.fabAria)}">${this.body()}</div></div>`);
    }
    this.root.innerHTML = parts.join("");
    this.bind();
  }

  private body(): string {
    const t = this.t;
    const err = this.err ? `<p class="err" role="alert">${esc(this.err)}</p>` : "";
    const dis = this.busy ? "disabled" : "";

    if (this.view === "done") {
      return `<div class="center stack"><div class="big" aria-hidden="true">✓</div><h2>${esc(t.thanks)}</h2>
        <p class="muted">${esc(t.receipt(this.doneId ?? 0))}</p>
        <div><button class="btn primary" data-act="close">${esc(t.done)}</button></div></div>`;
    }

    if (this.view === "mine") {
      const items = this.reports.map((r) => `<li class="stack">
          <div class="row"><strong class="grow">${esc(r.title)}</strong><span class="status ${esc(r.status)}">${esc(t.status[r.status] || r.status)}</span></div>
          ${r.status === "fikset" ? `<div class="row wrap"><span class="muted">${esc(t.fixedQ)}</span>
            <button class="chip" data-act="verify" data-id="${r.id}" data-fixed="1" ${dis}>${esc(t.yes)}</button>
            <button class="chip" data-act="verify" data-id="${r.id}" data-fixed="0" ${dis}>${esc(t.no)}</button></div>` : ""}
          <div class="row"><span class="muted">#${r.id}${r.created_at ? " · " + esc(new Date(r.created_at).toLocaleDateString()) : ""}</span>
            <button class="link end" data-act="delete" data-id="${r.id}">${esc(t.delete)}</button></div>
        </li>`).join("");
      return `<div class="row"><h2 class="grow">${esc(t.mine)}</h2></div>
        ${this.reports.length ? `<ul class="reports">${items}</ul>` : `<p class="muted">${esc(t.mineEmpty)}</p>`}
        <div class="row"><button class="btn ghost grow" data-act="back">${esc(t.back)}</button></div>`;
    }

    if (this.view === "write") {
      const k = (kind: Kind, label: string) =>
        `<button class="chip grow" data-act="kind" data-kind="${kind}" aria-pressed="${this.kind === kind}" data-testid="meldfra-type-${kind}">${esc(label)}</button>`;
      return `<h2>${esc(this.kind === "bug" ? t.titleBug : t.titleIdea)}</h2>
        <div class="row">${k("bug", t.kindBug)}${k("idea", t.kindIdea)}</div>
        <textarea rows="3" data-field="observed" data-testid="meldfra-tekst" aria-label="${esc(this.kind === "bug" ? t.titleBug : t.titleIdea)}"
          placeholder="${esc(this.kind === "bug" ? t.placeholderBug : t.placeholderIdea)}">${esc(this.observed)}</textarea>
        <p class="muted">${esc(t.aiNotice)}</p>${err}
        <div class="row"><button class="btn ghost grow" data-act="close" ${dis}>${esc(t.cancel)}</button>
          <button class="btn primary grow" data-act="start" data-testid="meldfra-fortsett" ${dis}>${esc(this.busy ? t.reading : t.next)}</button></div>
        ${this.reports.length ? `<div class="row"><button class="link" data-act="mine">${esc(t.mine)} (${this.reports.length})</button></div>` : ""}`;
    }

    if (this.view === "interview" && this.question) {
      const q = this.question;
      const text = t.questions[q.slot] || q.text;
      const answerUi = q.chips.length
        ? `<div class="row wrap">${q.chips.map((c) => `<button class="chip" data-act="chip" data-value="${esc(JSON.stringify(c.value))}" data-testid="meldfra-svar-${q.slot}" ${dis}>${esc(t.chips[`${q.slot}:${c.value}`] || c.label)}</button>`).join("")}</div>`
        : `<textarea rows="2" data-field="freeAnswer" data-testid="meldfra-svar-fritekst" aria-label="${esc(text)}" placeholder="${esc(t.shortAnswer)}">${esc(this.freeAnswer)}</textarea>
           <button class="btn primary" style="width:100%" data-act="free" ${dis}>${esc(this.busy ? t.writing : t.answer)}</button>`;
      return `<div class="row"><span class="badge">${esc(t.aiBadge)}</span><span class="muted">${esc(this.asked.length === 0 ? t.twoQuestions : t.lastQuestion)}</span></div>
        <div class="bubble">${esc(text)}</div>${answerUi}${err}
        <div class="row"><button class="link" data-act="skipq" ${dis}>${esc(t.dontKnow)}</button>
          <button class="link end" data-act="review" data-testid="meldfra-hopp-over" ${dis}>${esc(t.justSend)}</button></div>`;
    }

    // review
    const d = this.draft!;
    const shotRow = `<div class="consent"><input type="checkbox" data-consent="screenshot" data-testid="meldfra-samtykke-skjermbilde"
        ${this.consent.screenshot && this.shot ? "checked" : ""} ${this.shot ? "" : "disabled"} id="c-shot">
      <div class="grow"><label for="c-shot">${esc(t.screenshot)}</label>
        ${this.shot ? ` <button class="link" data-act="peekshot">${esc(this.showShot ? t.hide : t.peek)}</button>` : ""}
        <div class="muted">${esc(this.shot ? t.screenshotHint : t.screenshotNone)}</div></div></div>
      ${this.showShot && this.shot ? `<img class="shot" alt="" src="${this.shot}">` : ""}`;
    const crumbRow = `<div class="consent"><input type="checkbox" data-consent="breadcrumbs" data-testid="meldfra-samtykke-trykk"
        ${this.consent.breadcrumbs && this.crumbs.length ? "checked" : ""} ${this.crumbs.length ? "" : "disabled"} id="c-crumbs">
      <div class="grow"><label for="c-crumbs">${esc(t.crumbs(this.crumbs.length))}</label>
        ${this.crumbs.length ? ` <button class="link" data-act="peekcrumbs">${esc(this.showCrumbs ? t.hide : t.peek)}</button>` : ""}
        <div class="muted">${esc(t.crumbsHint)}</div></div></div>
      ${this.showCrumbs && this.crumbs.length ? `<div class="crumbs">${this.crumbs.slice(-20).map((c) => `<div>${esc(formatCrumb(c, t.verbs))}</div>`).join("")}</div>` : ""}`;
    return `<div class="row"><h2 class="grow">${esc(t.review)}</h2><span class="muted">${esc(t.reviewBadge)}</span></div>
      <label><span>${esc(t.fTitle)}</span><input type="text" data-field="title" data-testid="meldfra-utkast-tittel" value="${esc(d.title)}"></label>
      <label><span>${esc(d.kind === "idea" ? t.fObservedIdea : t.fObserved)}</span><textarea rows="3" data-field="d.observed" data-testid="meldfra-utkast-tekst">${esc(d.observed)}</textarea></label>
      ${d.kind === "bug" ? `<label><span>${esc(t.fExpected)}</span><textarea rows="2" data-field="expected" placeholder="${esc(t.optional)}">${esc(d.expected || "")}</textarea></label>` : ""}
      ${this.stepsText ? `<label><span>${esc(t.fSteps)}</span><textarea rows="${Math.min(6, this.stepsText.split("\n").length + 1)}" data-field="steps">${esc(this.stepsText)}</textarea></label>` : ""}
      <div class="box"><div class="muted"><strong>${esc(t.attached)}</strong></div>${shotRow}${crumbRow}
        <div class="consent"><span aria-hidden="true">✓</span><div><div>${esc(describeDevice())}</div><div class="muted">${esc(t.device)}</div></div></div></div>
      ${err}
      <div class="row"><button class="btn ghost grow" data-act="close" ${dis}>${esc(t.cancel)}</button>
        <button class="btn primary grow" data-act="send" data-testid="meldfra-send" ${dis}>${esc(this.busy ? t.sending : t.send)}</button></div>`;
  }

  private bind() {
    const r = this.root;
    r.querySelectorAll<HTMLElement>("[data-act]").forEach((el) => {
      el.addEventListener("click", (e) => {
        const act = el.dataset.act;
        if (act === "scrim") {
          if (e.target === el) this.close();
          return;
        }
        e.stopPropagation();
        this.act(act!, el);
      });
    });
    r.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>("[data-field]").forEach((el) => {
      el.addEventListener("input", () => {
        const f = el.dataset.field!;
        const v = el.value;
        if (f === "observed") this.observed = v;
        else if (f === "freeAnswer") this.freeAnswer = v;
        else if (f === "steps") this.stepsText = v;
        else if (this.draft) {
          if (f === "title") this.draft.title = v;
          else if (f === "d.observed") this.draft.observed = v;
          else if (f === "expected") this.draft.expected = v;
        }
      });
    });
    r.querySelectorAll<HTMLInputElement>("[data-consent]").forEach((el) => {
      el.addEventListener("change", () => {
        this.consent = { ...this.consent, [el.dataset.consent!]: el.checked };
      });
    });
    const sheet = r.querySelector<HTMLElement>(".sheet");
    if (sheet) {
      sheet.addEventListener("keydown", (e) => {
        if (e.key === "Escape") this.close();
      });
      const first = sheet.querySelector<HTMLElement>("textarea, input[type=text], button.primary");
      first?.focus();
    }
  }

  private act(act: string, el: HTMLElement) {
    switch (act) {
      case "open":
        void this.openSheet();
        break;
      case "close":
        this.close();
        break;
      case "kind":
        this.kind = el.dataset.kind as Kind;
        this.render();
        break;
      case "start":
        if (!this.observed.trim()) {
          this.err = this.t.errEmpty;
          this.render();
        } else void this.runInterview({}, []);
        break;
      case "chip":
        this.answer(JSON.parse(el.dataset.value || "null"));
        break;
      case "free":
        this.answer(this.freeAnswer.trim());
        break;
      case "skipq":
        this.answer(undefined);
        break;
      case "review":
        this.question = null;
        this.view = "review";
        this.render();
        break;
      case "peekshot":
        this.showShot = !this.showShot;
        this.render();
        break;
      case "peekcrumbs":
        this.showCrumbs = !this.showCrumbs;
        this.render();
        break;
      case "send":
        void this.submit();
        break;
      case "mine":
        this.view = "mine";
        this.render();
        void this.refreshMine();
        break;
      case "back":
        this.view = "write";
        this.render();
        break;
      case "verify":
        void this.verify(Number(el.dataset.id), el.dataset.fixed === "1");
        break;
      case "delete":
        void this.deleteReport(Number(el.dataset.id));
        break;
    }
  }
}

function esc(s: unknown): string {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}

/** Automatisk kontekst. Inneholder per definisjon ingen personopplysninger. */
export function deviceInfo(appVersion?: string): Record<string, string | number> {
  const ua = navigator.userAgent;
  const browser = /Edg\/(\d+)/.exec(ua) ? `Edge ${RegExp.$1}` : /Firefox\/(\d+)/.exec(ua) ? `Firefox ${RegExp.$1}`
    : /Chrome\/(\d+)/.exec(ua) ? `Chrome ${RegExp.$1}` : /Version\/(\d+).*Safari/.exec(ua) ? `Safari ${RegExp.$1}` : "ukjent";
  const os = /Windows NT/.test(ua) ? "Windows" : /Android (\d+)/.exec(ua) ? `Android ${RegExp.$1}`
    : /iPhone|iPad/.test(ua) ? "iOS" : /Mac OS X/.test(ua) ? "macOS" : /Linux/.test(ua) ? "Linux" : "ukjent";
  return {
    browser, os, viewport: `${window.innerWidth}×${window.innerHeight}`, pixel_ratio: window.devicePixelRatio || 1,
    locale: navigator.language, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "",
    ...(appVersion ? { app_version: appVersion } : {}),
  };
}

function describeDevice(): string {
  const d = deviceInfo();
  return `${d.browser} · ${d.os} · ${d.viewport}`;
}
