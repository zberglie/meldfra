/**
 * Skjermbilde med MASKERING I NETTLESEREN.
 *
 * Data som aldri forlot nettleseren, er heller ikke behandlet av oss. Maskeringen skjer derfor
 * i `onclone` — html2canvas gir en KOPI av dokumentet før den rendrer, så vi sladder i kopien
 * uten at den ekte siden blinker.
 *
 * Hva som maskeres:
 *   • alt inne i `[data-meldfra-sensitive]` (navn, adresse, betalingsinfo …)
 *   • alle `<input>`/`<textarea>`/`<select>`-verdier
 *   • med mask: "all-text" — ALL tekst på siden (layouten består, innholdet er borte)
 *   • rapport-UI-et selv (`[data-meldfra-ignore]`)
 *
 * Dette er en allowlist-invertering, ikke en garanti: glemmer verten å merke en ny komponent,
 * lekker den med "marked". Derfor er skjermbildet AV som standard, og brukeren SER bildet før
 * det sendes. Er portalen full av personopplysninger, bruk mask: "all-text".
 *
 * Feiler noe, returneres null — vi sender heller intet bilde enn et umaskert.
 */

export type MaskMode = "marked" | "all-text";

function blockText(root: Document, el: Element) {
  const walker = root.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  const nodes: Text[] = [];
  for (let n = walker.nextNode(); n; n = walker.nextNode()) nodes.push(n as Text);
  for (const t of nodes) {
    const len = (t.textContent || "").trim().length;
    if (len > 0) t.textContent = "█".repeat(Math.min(len, 40));
  }
}

function redact(root: Document, mode: MaskMode): void {
  root.querySelectorAll("[data-meldfra-ignore]").forEach((el) => el.remove());
  const targets = mode === "all-text" ? [root.body] : Array.from(root.querySelectorAll("[data-meldfra-sensitive]"));
  for (const el of targets) {
    blockText(root, el);
    el.querySelectorAll("img, canvas, svg, video, picture").forEach((m) => {
      (m as HTMLElement).style.visibility = "hidden";
    });
  }
  root.querySelectorAll("input, textarea").forEach((el) => {
    const input = el as HTMLInputElement;
    if (input.value) input.value = "█".repeat(Math.min(input.value.length, 30));
    input.setAttribute("placeholder", "");
  });
  root.querySelectorAll("select").forEach((el) => {
    (el as HTMLSelectElement).style.color = "transparent";
  });
}

/** Fang skjermen som maskert PNG data-URL. MÅ kalles FØR arket åpnes (ellers fanger vi arket). */
export async function capture(mode: MaskMode): Promise<string | null> {
  try {
    const html2canvas = (await import("html2canvas")).default;
    const canvas = await html2canvas(document.body, {
      logging: false,
      useCORS: true,
      scale: Math.min(1, 0.7 * (window.devicePixelRatio || 1)),
      ignoreElements: (el) => el.hasAttribute("data-meldfra-ignore"),
      onclone: (doc) => redact(doc, mode),
      x: window.scrollX,
      y: window.scrollY,
      width: window.innerWidth,
      height: window.innerHeight,
    });
    return canvas.toDataURL("image/png");
  } catch {
    return null;
  }
}
