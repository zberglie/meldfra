# Arkitektur og designvalg

Valgene her er ikke tilfeldige. Mange av dem ble tatt etter feil som bare viste seg i ekte bruk. Før du endrer på noe, les begrunnelsen.

## 1. KI-intervjuet: reglene ligger i koden, ikke i prompten

LLM-er som får mye kontekst, lar systematisk være å stille spørsmål. Når «forventet atferd» mangler, finner de den heller på enn å si at den mangler (arXiv 2605.25284 og 2504.18804). Derfor er ansvaret delt slik:

- **KI-en** gjør bare tre ting. Den renskriver brukerens tekst, klassifiserer den som feil eller idé, og utleder reproduksjonssteg fra trykkhistorikken (`interview.py`, strukturert uttrekk med Pydantic).
- **`next_question()`** er ren Python og bestemmer hva som spørres om. Rekkefølgen er forventet atferd, så om feilen stoppet brukeren, så hvor ofte den skjer. Det er et **hardt tak på 2 spørsmål**, fordi fullføringsraten faller fra 83 % til 65 % ved 4–8 spørsmål. At det ikke stilles noen spørsmål, er et vanlig og gyldig utfall. Ideer får aldri spørsmål.
- Spørsmålene er **faste tekster** med hurtigsvar og blir aldri formulert av KI-en.
- `expected` fylles **bare** når brukeren selv har sagt hva hen forventet. Brukerens svar overstyrer alltid modellens.
- Brukerens eget valg «idé» vinner alltid. «Feil» er bare det forvalgte valget i skjemaet, så hvis modellen leser teksten som en idé, vinner modellen. Ellers får et ønske spørsmålet «kom du videre?».
- Intervjuet er **tilstandsløst**. Klienten sender `answers` og `asked` med hver tur, og serveren holder ingen økt.
- **Faller KI-en ut, går rapporten likevel gjennom**, med brukerens egne ord som utkast. Det samme gjelder uten API-nøkkel.

## 2. Personvern i tre lag

1. **I nettleseren.** Skjermbildet maskeres i en *kopi* av DOM-en (`html2canvas.onclone`) før det blir til piksler. Trykkhistorikken logger `data-testid` og `aria-label`, aldri synlig tekst. HTTP-kall logges med metode, sti og status, uten query og body. Lengre sifferrekker i etikettene byttes ut med `#`.
2. **Intervju-prompten** forbyr modellen å gjengi personopplysninger.
3. **Saneringen på serveren** (`scrub.py`) fjerner e-post, fødselsnummer, kortnummer, kontonummer og telefonnummer fra all fritekst *før* den lagres. Dette er sikkerhetsnettet. Egne mønstre, for eksempel medlemsnummer, legges inn via `Scrubber(extra_patterns=…)`.

**Lagringen har to lag.** `meldfra_report` inneholder den sanerte, brukergodkjente teksten, og det er *bare* den som speiles til trackeren. `meldfra_attachment` inneholder rå bevis (skjermbilder) med TTL og kommer aldri inn i en sak. Verken GitHub eller GitLab har noen sletterutine for opplastede filer.

**Samtykke** gis per rapport og per vedlegg, og det lagres sammen med rapporten slik at det kan dokumenteres. Skjermbildet er av som standard. Serveren lagrer ikke vedlegg som mangler samtykke, selv om klienten sender dem likevel. Den godtar bare ekte PNG-filer på inntil 3 MB.

Knappene **«Send» og «Avbryt» har samme visuelle vekt**, i tråd med EDPB om villedende design. KI-bruken er merket der brukeren først møter den (AI Act art. 50). **Sletting** av egne rapporter fjerner også vedleggene (art. 7(3)).

## 3. Triage i bakgrunnen

Brukeren får ✓ med en gang. Triagen tar 2–5 sekunder og kjører som en asyncio-oppgave (`triage.py`).

- **Duplikater oppdages i to steg.** Først hentes kandidater med embedding-likhet ≥ 0,65 (bevisst lavt, for å fange omskrivinger). Deretter vurderer en **KI-dommer** hver kandidat på en skala fra 0 til 100.
  - ≥ 90: rapporten knyttes til klyngen og får en kommentar på den eksisterende saken, ikke en ny sak.
  - 50–89: rapporten får egen sak med labelen «mulig-duplikat» og en kryss-lenke, og et menneske avgjør.
  - Under 50: rapporten får egen sak.
- Embedding-likhet alene er for svakt. «Datovelgeren åpner feil måned» og «påmeldingen forsvinner» kan ligge på samme side, men er ulike feil.
- **En rapport lukkes aldri automatisk.** Et duplikat *arver* klyngens sak, slik at alle som meldte feilen får beskjed. Invariant: **én sak per klynge**.
- **Labels på en eksisterende sak røres ikke**, siden et menneske kan ha triagert den.
- **RICE-lite.** Reach er antall *distinkte* brukere i klyngen, og det er det eneste som kan automatiseres. Klyngen lagres *før* Reach telles, ellers teller ikke den nye rapportøren med. Effort er 1, og mennesket justerer i trackeren.
- Uten `MELDFRA_EMBEDDINGS_URL` hoppes duplikatsjekken over. Resten virker som normalt.
- Likhetssøket gjøres i Python over de siste 2000 vektorene, så det trengs ingen pgvector. Det holder godt for en medlemsportal. Blir det for tregt, er `Store.find_similar` stedet å bytte ut.

## 4. Oppfølgingsløkka

Folk som melder fra, motiveres av å *se* at det førte til noe, og de slutter når rapporten forsvinner i et svart hull. Kvitteringen lover at brukeren får beskjed, og `sync.py` holder det løftet.

- Løkka spør trackeren hvert 30. minutt om saker som er lukket de siste 48 timene. Den bruker polling og ikke webhooks: én kilde til sannhet, den tåler nedetid, og det er ingen hemmelighet å forvalte.
- **Idempotent.** Bare rader som faktisk skifter status, varsles.
- **`bekreftet` endres ikke av løkka.** Uten dette ville neste runde rullet brukerens bekreftelse tilbake og sendt varselet på nytt. Dette er en reell feil som ble funnet i brukertest. `gjenapnet` skal derimot varsles *på nytt* når saken lukkes igjen, og det er slik løkka er ment å fungere.
- Varselteksten avhenger av typen. En idé er ikke en «feil som er rettet», og «not planned» gir en egen nøytral tekst.
- Utløpte vedlegg slettes én gang i døgnet. Det er lovpålagt lagringsminimering, ikke vanlig opprydding.

Statusflyt: `ny → triaged → fikset | avvist → bekreftet | gjenapnet (→ fikset)`.

## 5. Ting som må bestå

- Ingen innsending skal velte fordi KI-en, embedding-tjenesten eller trackeren er nede. Alle eksterne kall er pakket inn i `try`, og triagen kan kjøres på nytt med `POST /admin/reports/{id}/retriage`.
- Statusfeltene `endret` og `issue_number` må ikke blandes. En rapport uten sak kan fortsatt bekreftes.
- Feltene i enhetsinformasjonen filtreres med `is not None`, ikke truthiness, fordi `False` og `0` kan være meningsfulle verdier.
- Serverens versjon (`MELDFRA_SERVER_VERSION`/`GIT_SHA`) settes **på serveren** og ikke av klienten. Den avslører «backenden er ikke deployet ennå» uten at noen må spørre.

## 6. Kjente begrensninger

- Rate-limitingen ligger i minnet og gjelder én prosess. Ved flere instanser bør det begrenses i proxyen.
- Sporing av SPA-navigasjon lytter på `pushState` og `replaceState`. Rutere som ikke bruker History-API-et, gir ikke nav-hendelser.
- html2canvas gjengir moderne CSS upresist og har ikke fått noen release siden 2022. `capture()` i `screenshot.ts` er stedet å bytte til for eksempel snapdom.
- Saneringen bruker regex og gjenkjenner ikke navn. Microsoft Presidio er den naturlige oppgraderingen, og `Scrubber.text()` er stedet den kobles inn.
