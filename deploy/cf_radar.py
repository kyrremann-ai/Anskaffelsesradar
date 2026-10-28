#!/usr/bin/env python3
"""Anskaffelsesradar på Cloudflare Pages + Access (e-post engangskode), som Havbruk Signal.

Env:  CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID (eller i ~/.config/havbruk/env, KEY=VALUE)
Bruk (fra repo-roten):
  python deploy/cf_radar.py setup                  # Pages-prosjekt, D1, domene, DNS og Access (én gang)
  python deploy/cf_radar.py deploy ut-pages/       # publiser et bygg fra deploy/build_pages.sh
  python deploy/cf_radar.py invite kari@firma.no [Kari]  # gi tilgang + skriv ut invitasjon
  python deploy/cf_radar.py grant kari@firma.no    # gi én person tilgang
  python deploy/cf_radar.py revoke kari@firma.no   # fjern tilgang + kast aktive sesjoner
  python deploy/cf_radar.py list                   # hvem har tilgang nå
  python deploy/cf_radar.py who                    # hvem har åpnet siden og når
  python deploy/cf_radar.py backup ut/             # eksporter sakene (SQL + antall rader) for sikkerhetskopi
  python deploy/cf_radar.py restore-test ut/       # gjenopprett kopien i en testdatabase og sammenlign antall
  python deploy/cf_radar.py migrer-eu              # flytt saker fra den gamle databasen til en ny med EU-jurisdiksjon
  RADAR_KUNDE=kommune-x python deploy/cf_radar.py setup   # kunde fra deploy/kunder.json (standard: demo)

Token-rettigheter: Account: Cloudflare Pages Edit, D1 Edit, Access: Apps and Policies Edit,
Access: Audit Logs Read; Zone (vikingsea.no): DNS Edit, Zone Read.
"""
import json
import os
import pathlib
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

# Én installasjon per kunde (deploy/kunder.json). RADAR_KUNDE velger kunde; enkeltverdier kan overstyres med env.
KUNDER = {k: v for k, v in json.loads((pathlib.Path(__file__).parent / "kunder.json").read_text(encoding="utf-8")).items()
          if not k.startswith("_")}
KUNDE = os.environ.get("RADAR_KUNDE") or "demo"
if KUNDE not in KUNDER:
    sys.exit(f"Ukjent kunde {KUNDE!r}. Kjente: {', '.join(KUNDER)} (deploy/kunder.json)")
_K = KUNDER[KUNDE]
PROJECT = os.environ.get("RADAR_CF_PROJECT") or _K["project"]
DOMAIN = os.environ.get("RADAR_DOMAIN", _K.get("domain", ""))  # tomt = bare pages.dev
ZONE = DOMAIN.split(".", 1)[1] if DOMAIN else ""
PAGES = f"{PROJECT}.pages.dev"
D1 = os.environ.get("RADAR_D1") or _K["d1"]
D1_OLD = os.environ.get("RADAR_D1_OLD", "radar-saker")  # databasen fra før EU-jurisdiksjon, brukes av migrer-eu
SESSION = os.environ.get("RADAR_SESSION", "24h")  # hvor lenge en innlogging varer
ENV_FILE = pathlib.Path(os.environ.get("HAVBRUK_ENV", pathlib.Path.home() / ".config" / "havbruk" / "env"))

if ENV_FILE.exists():  # samme fil som Havbruk Signal; ekte miljøvariabler vinner
    for line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
        k, sep, v = line.strip().partition("=")
        if sep and not k.startswith("#") and not os.environ.get(k.strip()):
            os.environ[k.strip()] = v.strip().strip('"')
ACC = (os.environ.get("CLOUDFLARE_ACCOUNT_ID") or "").strip()
TOK = (os.environ.get("CLOUDFLARE_API_TOKEN") or "").strip()
ROOT = "https://api.cloudflare.com/client/v4"
API = f"{ROOT}/accounts/{ACC}"


def api(method, path, body=None, base=None):
    req = urllib.request.Request(
        (base or API) + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {TOK}", "Content-Type": "application/json"},
    )
    try:
        r = json.load(urllib.request.urlopen(req))
    except urllib.error.HTTPError as e:
        r = json.loads(e.read() or b"{}")
    if not r.get("success"):
        raise SystemExit(f"{method} {path}: {r.get('errors')}")
    return r.get("result")


def soft(label, fn, *a):
    try:
        return fn(*a)
    except SystemExit as e:
        print(f"{label}: {e}")


def app_ids():
    # Eksakte navn: med flere kunder starter alle prosjektnavn med «anskaffelsesradar»
    names = {PROJECT, PROJECT + "-domain"}
    ids = [a["id"] for a in api("GET", "/access/apps") or [] if a.get("name") in names]
    if not ids:
        sys.exit("Access-app finnes ikke – kjør setup")
    return ids


def policies(aid):
    return api("GET", f"/access/apps/{aid}/policies") or []


def find_d1(name):
    return next((d for d in api("GET", f"/d1/database?name={name}") or [] if d["name"] == name), None)


def d1_id(name=None):
    """Databasen for sakene. Nye databaser lages med jurisdiksjon EU, så dataene lagres og behandles i EU."""
    name = name or D1
    if hit := find_d1(name):
        if hit.get("jurisdiction") not in (None, "eu"):
            print(f"Advarsel: {name} har jurisdiksjon {hit.get('jurisdiction')}, ikke eu")
        return hit["uuid"]
    return api("POST", "/d1/database", {"name": name, "jurisdiction": "eu"})["uuid"]


def query(db_id, sql):
    return api("POST", f"/d1/database/{db_id}/query", {"sql": sql})[0]["results"]


def counts(db_id):
    tables = {r["name"] for r in query(db_id, "SELECT name FROM sqlite_master WHERE type='table'")}
    return {t: query(db_id, f"SELECT COUNT(*) AS n FROM {t}")[0]["n"] for t in ("cases", "case_history") if t in tables}


def bound_d1():
    """Databasen Pages-prosjektet faktisk bruker (kan være den gamle før migrer-eu). Gir (id, navn, jurisdiksjon)."""
    proj = api("GET", f"/pages/projects/{PROJECT}")
    dbs = ((proj.get("deployment_configs") or {}).get("production") or {}).get("d1_databases") or {}
    if "DB" not in dbs:
        sys.exit(f"{PROJECT} har ingen D1-kobling – kjør setup")
    d = api("GET", f"/d1/database/{dbs['DB']['id']}")
    return d["uuid"], d["name"], d.get("jurisdiction")


def wrangler(*args):
    subprocess.run(["npx", "--yes", "wrangler@3", "d1", *args], check=True, shell=os.name == "nt")


def setup():
    soft("pages", api, "POST", "/pages/projects", {"name": PROJECT, "production_branch": "main"})
    # Saker i D1, koblet til Functions som env.DB (både produksjon og forhåndsvisning).
    # Finnes bare den gamle databasen (fra før EU-jurisdiksjon), flyttes sakene først, så ingen saker forsvinner.
    if KUNDE == "demo" and not find_d1(D1) and find_d1(D1_OLD):
        migrer_eu()
    else:
        bind_d1()
    # Én Access-app per vert (Access takler ikke flere domener i én app)
    apps = {x["name"]: x for x in api("GET", "/access/apps") or []}
    wanted = {PROJECT: [PAGES, f"*.{PAGES}"]}
    if DOMAIN:
        wanted[PROJECT + "-domain"] = [DOMAIN]
        soft("domene", api, "POST", f"/pages/projects/{PROJECT}/domains", {"name": DOMAIN})
        zones = api("GET", f"/zones?name={ZONE}", base=ROOT) or []
        if zones:
            zid = zones[0]["id"]
            rec = api("GET", f"/zones/{zid}/dns_records?name={DOMAIN}", base=ROOT) or []
            body = {"type": "CNAME", "name": DOMAIN, "content": PAGES, "proxied": True, "ttl": 1}
            if rec:
                soft("dns", api, "PUT", f"/zones/{zid}/dns_records/{rec[0]['id']}", body, ROOT)
            else:
                soft("dns", api, "POST", f"/zones/{zid}/dns_records", body, ROOT)
        else:
            print(f"dns: fant ikke sonen {ZONE}; lag CNAME {DOMAIN} -> {PAGES} selv")
    for name, uris in wanted.items():
        body = {
            "name": name,
            "type": "self_hosted",
            "session_duration": SESSION,
            "destinations": [{"type": "public", "uri": u} for u in uris],
            "app_launcher_visible": False,
        }
        if name in apps:
            soft("access", api, "PUT", f"/access/apps/{apps[name]['id']}", body)
        else:
            soft("access", api, "POST", "/access/apps", body)
    print(
        f"OK. Del https://{DOMAIN or PAGES} (kun adresser med grant/invite slippes inn). "
        "Sjekk at 'One-time PIN' er på under Zero Trust > Settings > Authentication."
    )


def deploy(out="pages-out"):
    out = pathlib.Path(out).resolve()
    if not (out / "public" / "index.html").exists():
        sys.exit(f"Fant ikke {out}/public/index.html – kjør deploy/build_pages.sh {out} først")
    subprocess.run(
        ["npx", "--yes", "wrangler@3", "pages", "deploy", "public", "--project-name", PROJECT,
         "--branch", "main", "--commit-dirty=true"],
        check=True, cwd=out, shell=os.name == "nt",
    )


def bind_d1():
    db = {"DB": {"id": d1_id()}}
    cfg = {"d1_databases": db, "compatibility_date": "2024-09-23"}
    api("PATCH", f"/pages/projects/{PROJECT}", {"deployment_configs": {"production": cfg, "preview": cfg}})


def backup(out="backup"):
    """Eksporterer sakene til <out>/<kunde>-saker.sql, med antall rader i <kunde>-saker.json (brukes av restore-test)."""
    out = pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=True)
    sql, meta = out / f"{KUNDE}-saker.sql", out / f"{KUNDE}-saker.json"
    db_id, name, jur = bound_d1()
    if jur != "eu":
        print(f"::warning::{PROJECT} bruker {name} uten EU-jurisdiksjon. Kjør «Radar-tilgang» → migrer-eu.")
    n = counts(db_id)
    if n:
        wrangler("export", name, "--remote", f"--output={sql}")
    else:
        sql.write_text("", encoding="utf-8")  # ingen saker ennå
    meta.write_text(json.dumps({"kunde": KUNDE, "d1": name, "counts": n}), encoding="utf-8")
    print(f"backup {KUNDE}: {n} -> {sql}")


def restore_test(out="backup"):
    """Gjenoppretter siste kopi i en midlertidig database og sjekker at alle rader kom med. Feiler hvis ikke."""
    out = pathlib.Path(out)
    sql, want = out / f"{KUNDE}-saker.sql", json.loads((out / f"{KUNDE}-saker.json").read_text(encoding="utf-8"))["counts"]
    if not sql.read_text(encoding="utf-8").strip():
        print(f"restore-test {KUNDE}: ingen saker å gjenopprette, OK")
        return
    test = f"{D1}-gjenoppretting-test"
    if old := find_d1(test):  # rester fra en avbrutt test
        api("DELETE", f"/d1/database/{old['uuid']}")
    tid = d1_id(test)
    try:
        wrangler("execute", test, "--remote", f"--file={sql}", "--yes")
        got = counts(tid)
    finally:
        api("DELETE", f"/d1/database/{tid}")
    if got != want:
        sys.exit(f"restore-test {KUNDE}: FEIL, forventet {want}, fikk {got}")
    print(f"restore-test {KUNDE}: OK {got}")


def migrer_eu():
    """Kopierer sakene fra den gamle databasen (uten jurisdiksjon) til D1 med jurisdiksjon EU, og kobler Pages til den nye.
    Den gamle databasen slettes ikke. Slett den i Cloudflare når den nye er sjekket."""
    old = find_d1(D1_OLD)
    if not old:
        sys.exit(f"Fant ikke {D1_OLD}, ingenting å flytte")
    new = d1_id()
    if counts(new).get("cases"):
        sys.exit(f"{D1} har allerede saker; avbryter for ikke å blande")
    want = counts(old["uuid"])
    for t in counts(new):  # tomme tabeller laget av Pages Functions før flyttingen; eksporten lager dem på nytt
        query(new, f"DROP TABLE IF EXISTS {t}")
    if want.get("cases"):
        tmp = pathlib.Path("migrering.sql")
        wrangler("export", D1_OLD, "--remote", f"--output={tmp}")
        wrangler("execute", D1, "--remote", f"--file={tmp}", "--yes")
        tmp.unlink()
    got = counts(new)
    if got.get("cases", 0) != want.get("cases", 0):
        sys.exit(f"Flyttingen stemmer ikke: {want} i {D1_OLD}, {got} i {D1}")
    bind_d1()
    print(f"Flyttet {got} fra {D1_OLD} til {D1} (EU). Publiser på nytt (nattjobben eller «Radar på Cloudflare») for å ta den i bruk.")


def kunder():
    for k, v in KUNDER.items():
        print(f"{k}: https://{v.get('domain') or v['project'] + '.pages.dev'} (prosjekt {v['project']}, D1 {v['d1']})")


def grant(email):
    email = email.strip().lower()
    for aid in app_ids():
        if any(p["name"] == f"radar: {email}" for p in policies(aid)):
            continue
        api("POST", f"/access/apps/{aid}/policies", {
            "name": f"radar: {email}", "decision": "allow", "session_duration": SESSION,
            "include": [{"email": {"email": email}}],
        })
    print("tilgang gitt:", email)


INVITE = """Hei{navn},

Her er tilgangen din til Anskaffelsesradar: {url}

Slik logger du inn:
1. Åpne lenken og skriv inn denne e-postadressen: {email}
2. Du får en engangskode på e-post fra noreply@notify.cloudflare.com. Den kan ligge i \
søppelpost, og gjelder i 10 minutter.
3. Skriv inn koden, så kommer du rett inn.

Dataene er offentlige kunngjøringer fra TED og Doffin. Prioriteten sier hva som bør sjekkes \
først, og er ikke bevis for misligheter. Tilgangen er personlig.

Mvh
Kyrre Foros"""


def invite(email, navn=""):
    grant(email)
    body = INVITE.format(navn=f" {navn}" if navn else "", url=f"https://{DOMAIN or PAGES}", email=email.strip())
    subject = "Tilgang til Anskaffelsesradar"
    print(f"\n--- Invitasjon til {email} ---\n{subject}\n\n{body}\n")
    link = f"mailto:{email.strip()}?subject={urllib.parse.quote(subject)}&body={urllib.parse.quote(body)}"
    try:
        os.startfile(link)  # type: ignore[attr-defined]  # Windows: åpner e-postprogrammet
    except (AttributeError, OSError):
        pass


def revoke(email):
    email = email.strip().lower()
    for aid in app_ids():
        for p in policies(aid):
            if p["name"] == f"radar: {email}":
                api("DELETE", f"/access/apps/{aid}/policies/{p['id']}")
    api("POST", "/access/organizations/revoke_user", {"email": email})
    print("tilgang fjernet og sesjoner kastet:", email)


def lst():
    for n in sorted({p["name"] for aid in app_ids() for p in policies(aid)}):
        print(n.removeprefix("radar: "))


def who():
    for e in api("GET", "/access/logs/access_requests?limit=100") or []:
        d = e.get("app_domain") or ""
        if PROJECT in d or (DOMAIN and DOMAIN in d):
            print(e.get("created_at", ""), e.get("user_email", ""), "OK" if e.get("allowed") else "NEKTET")


if __name__ == "__main__":
    c, *a = sys.argv[1:] or ["help"]
    cmds = {"setup": setup, "deploy": deploy, "grant": grant, "invite": invite, "revoke": revoke, "list": lst, "who": who,
            "backup": backup, "restore-test": restore_test, "migrer-eu": migrer_eu, "kunder": kunder}
    if c not in cmds:
        sys.exit(__doc__)
    if c != "kunder" and (not (len(ACC) == 32 and ACC.isalnum()) or len(TOK) < 30):
        sys.exit("Mangler CLOUDFLARE_ACCOUNT_ID (32 tegn) og CLOUDFLARE_API_TOKEN i miljøet eller " + str(ENV_FILE))
    cmds[c](*a)
