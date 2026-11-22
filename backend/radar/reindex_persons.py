"""Full re-indeksering av personnøkler etter bytte av PERSON_SALT.

    PERSON_SALT=<nytt salt> python -m radar.reindex_persons --ja
    python -m radar.pipeline

Personnøklene kan ikke regnes om lokalt, fordi fødselsdatoen ikke lagres (bare året). Derfor slettes alle
roller, alle enheter merkes som utdatert, og neste kjøring henter roller for alle orgnr fra Brreg på nytt
(20–40 minutter for ca. 5 500 orgnr). Fingeravtrykket av det nye saltet lagres med en gang.
"""
from __future__ import annotations

import argparse
from . import db
from .config import settings

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ja", action="store_true", help="bekreft at alle roller slettes og hentes på nytt")
    a = ap.parse_args()
    if not a.ja:
        raise SystemExit("Sletter alle Brreg-roller og henter dem på nytt med det nye saltet. Kjør med --ja for å bekrefte.")
    con = db.connect(settings.db_path)
    with db.tx(con):
        n = con.execute("DELETE FROM brreg_roles").rowcount
        con.execute("UPDATE brreg_entities SET fetched_at = ''")
        db.set_meta(con, "person_salt_fp", db.salt_fingerprint(settings.person_salt))
    print(f"Slettet {n} roller. Kjør python -m radar.pipeline for å hente dem på nytt med det nye saltet.")
