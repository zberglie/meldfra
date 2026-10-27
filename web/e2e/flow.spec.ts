// Ende-til-ende mot demoappen (examples/demo_app.py på :8765). Kjøres med `npm run e2e`.
import { expect, test } from "@playwright/test";

const BASE = process.env.MELDFRA_DEMO_URL || "http://localhost:8765";

test("hele løypa: feil → skriv → (intervju) → godkjenn → send → mine rapporter", async ({ page }) => {
  await page.goto(`${BASE}/kontingent`);
  await page.getByTestId("betal-kontingent").click();
  await expect(page.locator("#status")).toHaveText("Noe gikk galt.");

  const w = page.locator("meldfra-widget");
  await w.getByRole("button", { name: /Meld fra/ }).click();
  await w.getByTestId("meldfra-tekst").fill("Trykket betal, men ingenting skjedde");
  await w.getByTestId("meldfra-fortsett").click();

  // Uten KI-nøkkel kommer spørsmålet om forventet atferd; med nøkkel kan det også komme.
  const skip = w.getByTestId("meldfra-hopp-over");
  if (await skip.isVisible().catch(() => false)) await skip.click();

  await expect(w.getByTestId("meldfra-send")).toBeVisible();
  // Samtykke-kontrakten: skjermbildet er AV som standard, trykkhistorikken PÅ.
  await expect(w.getByTestId("meldfra-samtykke-skjermbilde")).not.toBeChecked();
  await expect(w.getByTestId("meldfra-samtykke-trykk")).toBeChecked();

  // Brukeren ser hva som sendes — trykket og det feilende kallet står i lista, uten tekstinnhold.
  await w.getByRole("button", { name: "Se", exact: true }).last().click();
  await expect(w.locator(".crumbs")).toContainText("betal-kontingent");
  await expect(w.locator(".crumbs")).toContainText("POST /api/betal → 502");

  // Skjermbildet: slå på, se på det.
  await w.getByTestId("meldfra-samtykke-skjermbilde").check();
  await w.getByRole("button", { name: "Se", exact: true }).first().click();
  await expect(w.locator("img.shot")).toBeVisible();
  await page.screenshot({ path: "e2e/out/godkjenn.png" });

  await w.getByTestId("meldfra-send").click();
  await expect(w.getByText("Takk. Denne var nyttig.")).toBeVisible();
  await w.getByRole("button", { name: "Ferdig" }).click();

  await w.getByRole("button", { name: /Meld fra/ }).click();
  await w.getByRole("button", { name: /Mine rapporter/ }).click();
  await expect(w.locator("ul.reports li").first()).toContainText(/Mottatt|Til vurdering/);
});
