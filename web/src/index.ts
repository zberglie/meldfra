/**
 * Inngang. To måter å ta modulen i bruk på:
 *
 *   1. Én script-tagg (serveres av Python-routeren):
 *        <script src="/meldfra/meldfra.js" data-meldfra-auto data-lang="nb"></script>
 *      Endepunktet utledes av script-adressen. Valg som data-attributter: data-lang,
 *      data-position, data-mask, data-app-version, data-show-button="false", data-hide-on="/kasse,/admin".
 *
 *   2. Programmatisk (npm / bundler, eller når tokenet må hentes fra portalens auth-klient):
 *        MeldFra.init({ endpoint: "/meldfra", headers: async () => ({ Authorization: `Bearer ${await token()}` }) })
 *        MeldFra.open()        // fra en egen «Meld fra»-lenke i menyen
 *        MeldFra.openMine()    // «Mine rapporter», f.eks. fra profilsiden
 */
import { crumbError, installBreadcrumbs } from "./breadcrumbs";
import { MeldFraWidget, type MeldFraOptions } from "./widget";

export type { MeldFraOptions } from "./widget";

const TAG = "meldfra-widget";
let instance: MeldFraWidget | null = null;

export function init(opts: MeldFraOptions = {}): MeldFraWidget {
  if (instance) return instance;
  if (!customElements.get(TAG)) customElements.define(TAG, MeldFraWidget);
  // Sporingen installeres FØRST og uansett — feilen kan ha skjedd før knappen ble trykket.
  installBreadcrumbs(opts.endpoint || "/meldfra");
  instance = new MeldFraWidget(opts);
  const mount = () => document.body.appendChild(instance!);
  if (document.body) mount();
  else document.addEventListener("DOMContentLoaded", mount, { once: true });
  return instance;
}

export const open = () => void (instance ?? init()).openSheet("write");
export const openMine = () => void (instance ?? init()).openSheet("mine");
/** La portalen legge egne feil inn i sporet (f.eks. fra en global error boundary). */
export const reportError = (message: unknown, source?: string) => crumbError(message, source);

const api = { init, open, openMine, reportError };
declare global {
  interface Window {
    MeldFra: typeof api;
  }
}
window.MeldFra = api;

// Auto-init fra script-taggen.
const script = document.currentScript as HTMLScriptElement | null;
if (script && script.hasAttribute("data-meldfra-auto")) {
  const ds = script.dataset;
  const src = script.src ? new URL(script.src, location.href) : null;
  init({
    endpoint: ds.endpoint || (src ? src.pathname.replace(/\/[^/]*$/, "") : "/meldfra"),
    lang: (ds.lang as MeldFraOptions["lang"]) || undefined,
    position: (ds.position as MeldFraOptions["position"]) || undefined,
    mask: (ds.mask as MeldFraOptions["mask"]) || undefined,
    appVersion: ds.appVersion,
    showButton: ds.showButton !== "false",
    screenshot: ds.screenshot !== "false",
    hideOn: ds.hideOn ? ds.hideOn.split(",").map((s) => s.trim()).filter(Boolean) : undefined,
  });
}
