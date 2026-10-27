"""Oppfølgingssløyfa: lukk sirkelen tilbake til den som meldte fra.

DET VIKTIGSTE GREPET I HELE MODULEN. Folk motiveres ikke av premier for å melde fra — de
motiveres av å SE at bidraget førte til noe, og de slutter når rapporten forsvinner i et svart
hull. Kvitteringen lover «du får beskjed når vi har fikset den». Et brutt løfte er verre enn
intet løfte.

En løkke poller trackeren for lukkede saker. Hver rapport som peker på en lukket sak settes til
`fikset` (eller `avvist` ved «not planned» på GitHub), og rapportøren varsles. Duplikater arver
klyngens sak, så ALLE som meldte samme feil får beskjed.

Vi poller i stedet for å ta imot webhooks: én kilde til sannhet, tåler at tjenesten har vært
nede, og ingen webhook-hemmelighet å forvalte.
"""
import asyncio
from datetime import datetime, timedelta, timezone

LOOKBACK_HOURS = 48


def message_for(row):
    """Konkret, ikke generisk: brukeren skal kjenne igjen SIN rapport. Og en IDÉ er ikke en
    «feil som er rettet» — det leses som at tjenesten kalte forslaget en bug."""
    title = (row.get("title") or "").strip()
    short = (title[:60] + "…") if len(title) > 60 else title
    if row.get("status") == "avvist":
        return ("Vi har vurdert rapporten din",
                f"«{short}» — vi har sett på den, men kommer ikke til å gjøre endringer nå. Takk for at du sa fra.")
    if (row.get("kind") or "") == "idea":
        return "Tatt inn! 💡", f"«{short}» — forslaget ditt er nå på plass. Takk for idéen."
    return "Fikset! 🎉", f"«{short}» — feilen du meldte er rettet. Takk for at du sa fra."


async def sync_once(store, tracker, notify=None):
    """Én runde. Returnerer antall rapporter som skiftet status."""
    if not tracker.configured:
        return 0
    since = (datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    changed = 0
    for issue in await tracker.list_closed_since(since):
        rows = await asyncio.to_thread(store.mark_closed, tracker.name, issue["number"], issue.get("reason"))
        for row in rows:
            changed += 1
            if not notify:
                continue  # widgeten viser statusen uansett i «Mine rapporter»
            try:
                title, text = message_for(row)
                res = notify(row["user_id"], title, text, row)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:
                # Ett feilet varsel skal ikke stoppe de andre; statusen er allerede satt.
                print("meldfra: varsel feilet (status er satt):", e)
    return changed


async def run_loop(store, tracker, notify, interval_s):
    """Evig løkke. Én feilet runde stopper aldri løkka. Rydder også utløpte vedlegg én gang
    i døgnet (lovpålagt lagringsminimering)."""
    await asyncio.sleep(5)
    purge_day = None
    while True:
        try:
            n = await sync_once(store, tracker, notify)
            if n:
                print(f"meldfra: {n} rapport(er) markert som lukket.")
            today = datetime.now(timezone.utc).date()
            if purge_day != today:
                purge_day = today
                p = await asyncio.to_thread(store.purge_expired_attachments)
                if p:
                    print(f"meldfra: slettet {p} utløpt(e) vedlegg.")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print("meldfra: sync feilet (fortsetter):", e)
        await asyncio.sleep(interval_s)
