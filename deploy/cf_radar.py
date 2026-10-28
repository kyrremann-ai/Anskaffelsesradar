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

PROJECT = os.environ.get("RADAR_CF_PROJECT", "anskaffelsesradar")
DOMAIN = os.environ.get("RADAR_DOMAIN", "radar.vikingsea.no")  # tomt = bare pages.dev
ZONE = DOMAIN.split(".", 1)[1] if DOMAIN else ""
PAGES = f"{PROJECT}.pages.dev"
D1 = os.environ.get("RADAR_D1", "radar-saker")
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
    ids = [a["id"] for a in api("GET", "/access/apps") or [] if a.get("name", "").startswith(PROJECT)]
    if not ids:
        sys.exit("Access-app finnes ikke – kjør setup")
    return ids


def policies(aid):
    return api("GET", f"/access/apps/{aid}/policies") or []


def d1_id():
    dbs = api("GET", f"/d1/database?name={D1}") or []
    hit = [d for d in dbs if d["name"] == D1]
    return hit[0]["uuid"] if hit else api("POST", "/d1/database", {"name": D1})["uuid"]


def setup():
    soft("pages", api, "POST", "/pages/projects", {"name": PROJECT, "production_branch": "main"})
    # Saker i D1, koblet til Functions som env.DB (både produksjon og forhåndsvisning)
    db = {"DB": {"id": d1_id()}}
    cfg = {"d1_databases": db, "compatibility_date": "2024-09-23"}
    api("PATCH", f"/pages/projects/{PROJECT}", {"deployment_configs": {"production": cfg, "preview": cfg}})
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
    cmds = {"setup": setup, "deploy": deploy, "grant": grant, "invite": invite, "revoke": revoke, "list": lst, "who": who}
    if c not in cmds:
        sys.exit(__doc__)
    if not (len(ACC) == 32 and ACC.isalnum()) or len(TOK) < 30:
        sys.exit("Mangler CLOUDFLARE_ACCOUNT_ID (32 tegn) og CLOUDFLARE_API_TOKEN i miljøet eller " + str(ENV_FILE))
    cmds[c](*a)
