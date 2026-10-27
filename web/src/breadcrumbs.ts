/**
 * Interaksjonsspor («trykkhistorikk») — ringbuffer + instrumentering.
 *
 * ARKITEKTUREN ER ET PERSONVERNVALG. Vi tar bevisst IKKE opp sesjonen (rrweb/session replay):
 * et DOM-opptak ville båret alt som sto på skjermen ut av nettleseren. Vi logger HVA brukeren
 * gjorde, aldri HVA som sto der:
 *
 *   ✅  tap:betal-knapp  på  /kontingent        → etikett, ikke innhold
 *   ❌  tap "Ola Nordmann, medlemsnr 12345"      → logges ALDRI
 *
 * Etiketter hentes fra data-testid → aria-label → tag/rolle, ALDRI fra tekstinnhold.
 * HTTP-crumbs får metode + sti + status + varighet, aldri query eller body. Alt ligger i minnet
 * til brukeren trykker «Send».
 *
 * Tips til vertsløsningen: gi viktige knapper en data-testid. Da blir sporet presist, og det er
 * nøyaktig det en Playwright-test trenger som locator.
 */

export type CrumbType = "nav" | "tap" | "http" | "error" | "app";
export type CrumbLevel = "info" | "warning" | "error";

export interface Crumb {
  t: number;
  type: CrumbType;
  category: string;
  message: string;
  level: CrumbLevel;
  data?: Record<string, string | number | boolean>;
}

export const MAX_CRUMBS = 50;
const MAX_LABEL = 80;
const buffer: Crumb[] = [];
let ignorePath = "";

/** Etiketter skal være IDer, ikke tekst: siffergrupper skrubbes som siste skanse. */
function sanitizeLabel(raw: unknown): string {
  const s = String(raw ?? "").replace(/\s+/g, " ").trim();
  if (!s) return "ukjent";
  return s.replace(/\d{2,}/g, "#").slice(0, MAX_LABEL);
}

export function addCrumb(c: Omit<Crumb, "t" | "level"> & { level?: CrumbLevel }): void {
  try {
    buffer.push({ t: Date.now(), type: c.type, category: c.category, message: sanitizeLabel(c.message),
      level: c.level ?? "info", ...(c.data ? { data: c.data } : {}) });
    while (buffer.length > MAX_CRUMBS) buffer.shift();
  } catch {
    /* aldri kast fra sporing */
  }
}

export const getCrumbs = (): Crumb[] => buffer.slice();
export const clearCrumbs = (): void => {
  buffer.length = 0;
};

/** Menneskelesbar linje — brukeren skal kunne LESE hva som sendes. Det er det som gjør
 *  samtykket informert, og grunnen til at vi valgte en liste framfor en video. */
export function formatCrumb(c: Crumb, verbs: Record<string, string>): string {
  const d = new Date(c.t);
  const p = (n: number) => String(n).padStart(2, "0");
  const verb = c.category === "ui.rage" ? verbs.rage : verbs[c.type] ?? "";
  const suffix = c.data?.status !== undefined ? ` → ${c.data.status}` : "";
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}  ${verb} ${c.message}${suffix}`;
}

// Rage-tap: 3+ trykk på samme etikett innen ett sekund betyr som regel at ingenting skjedde.
let lastLabel = "";
let lastTaps: number[] = [];
function noteRage(label: string): boolean {
  const now = Date.now();
  if (label !== lastLabel) {
    lastLabel = label;
    lastTaps = [now];
    return false;
  }
  lastTaps = lastTaps.filter((t) => now - t < 1000);
  lastTaps.push(now);
  if (lastTaps.length >= 3) {
    lastTaps = [];
    addCrumb({ type: "tap", category: "ui.rage", message: label, level: "warning", data: { taps: 3 } });
    return true;
  }
  return false;
}

/** Trygg etikett for et element. Rekkefølgen er en personvernregel: kun IDer og aria-labels. */
function labelFor(el: Element | null): string | null {
  let node: Element | null = el;
  for (let i = 0; node && i < 5; i++) {
    if (node.closest("[data-meldfra-ignore]")) return null;
    const id = node.getAttribute("data-testid") || node.getAttribute("data-meldfra-label");
    if (id) return id;
    const aria = node.getAttribute("aria-label");
    if (aria) return aria;
    node = node.parentElement;
  }
  const tag = el?.tagName?.toLowerCase();
  const role = el?.getAttribute("role");
  if (tag === "button" || tag === "a" || role === "button") return `${tag}${role ? `[${role}]` : ""}`;
  return null;
}

const route = () => {
  try {
    return window.location.pathname;
  } catch {
    return "";
  }
};

/** Sti uten query — et søk kan inneholde et navn. */
function safePath(url: string): string {
  try {
    return new URL(url, window.location.origin).pathname;
  } catch {
    return String(url).split("?")[0].slice(0, 120);
  }
}

function noteHttp(method: string, url: string, status: number, started: number) {
  const path = safePath(url);
  if (ignorePath && path.startsWith(ignorePath)) return; // rapporteringens egne kall er støy
  addCrumb({
    type: "http", category: "fetch", message: `${method.toUpperCase()} ${path}`,
    level: status >= 500 || status === 0 ? "error" : status >= 400 ? "warning" : "info",
    data: { status, ms: Date.now() - started },
  });
}

let installed = false;

/** Idempotent. Alt i try/catch — sporingen skal aldri velte siden den skal hjelpe oss å fikse. */
export function installBreadcrumbs(endpointPath: string): void {
  try {
    if (installed || typeof window === "undefined") return;
    installed = true;
    ignorePath = safePath(endpointPath);
    addCrumb({ type: "app", category: "lifecycle", message: "sidelast" });

    window.addEventListener("click", (e) => {
      try {
        const label = labelFor(e.target as Element);
        if (!label || noteRage(label)) return;
        addCrumb({ type: "tap", category: "ui.tap", message: label, data: { route: route() } });
      } catch {
        /* ignorer */
      }
    }, { capture: true, passive: true });

    let lastPath = route();
    const noteNav = () => {
      const to = route();
      if (to === lastPath) return;
      addCrumb({ type: "nav", category: "navigation", message: to, data: { from: lastPath } });
      lastPath = to;
    };
    for (const k of ["pushState", "replaceState"] as const) {
      const orig = history[k];
      history[k] = function (this: History, ...args: Parameters<History["pushState"]>) {
        const r = orig.apply(this, args);
        noteNav();
        return r;
      };
    }
    window.addEventListener("popstate", noteNav);

    const origFetch = window.fetch;
    window.fetch = async function (input: RequestInfo | URL, init?: RequestInit) {
      const started = Date.now();
      const method = String(init?.method || (input instanceof Request ? input.method : "GET"));
      const url = input instanceof Request ? input.url : String(input);
      try {
        const res = await origFetch.call(this, input, init);
        noteHttp(method, url, res.status, started);
        return res;
      } catch (err) {
        noteHttp(method, url, 0, started);
        throw err;
      }
    };

    // XHR også — axios og mange eldre portaler går via XMLHttpRequest, ikke fetch.
    const origOpen = XMLHttpRequest.prototype.open;
    const origSend = XMLHttpRequest.prototype.send;
    const meta = new WeakMap<XMLHttpRequest, { m: string; u: string }>();
    XMLHttpRequest.prototype.open = function (this: XMLHttpRequest, m: string, u: string | URL, ...rest: unknown[]) {
      meta.set(this, { m, u: String(u) });
      return (origOpen as (...a: unknown[]) => void).call(this, m, u, ...rest);
    } as typeof XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.send = function (this: XMLHttpRequest, body?: Document | XMLHttpRequestBodyInit | null) {
      const started = Date.now();
      const info = meta.get(this);
      if (info) this.addEventListener("loadend", () => noteHttp(info.m, info.u, this.status, started));
      return origSend.call(this, body);
    };

    window.addEventListener("error", (e) => crumbError(e.message, "window.onerror"));
    window.addEventListener("unhandledrejection", (e) =>
      crumbError((e.reason && (e.reason.message || String(e.reason))) || "unhandledrejection", "promise"));
    document.addEventListener("visibilitychange", () =>
      addCrumb({ type: "app", category: "lifecycle", message: document.hidden ? "skjult" : "synlig" }));
  } catch {
    /* best-effort */
  }
}

/** Feilmeldinger kan inneholde brukerdata («Cannot read … of 'ola@…'») — kun første 120 tegn,
 *  og sanitizeLabel fjerner sifre. Serveren sanerer i tillegg. */
export function crumbError(message: unknown, source?: string): void {
  addCrumb({ type: "error", category: "console", message: String(message ?? "Ukjent feil").slice(0, 120),
    level: "error", ...(source ? { data: { source } } : {}) });
}
