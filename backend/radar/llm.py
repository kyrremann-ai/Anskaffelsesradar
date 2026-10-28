"""LLM-adapter for AI-vurdering. Mistral (EU) som standard, byttbar.

Personvern: vi sender bare kontraktsfelt og flagg. Aldri navn fra Brreg-roller.
Uten API-nøkkel brukes en regelbasert mal, så appen virker uten LLM.
"""
from __future__ import annotations

import json
from typing import Protocol

import httpx

SYSTEM = (
    "Du er en erfaren kommunerevisor. Skriv en kort, nøktern vurdering på norsk bokmål av om en offentlig "
    "kontrakt bør prioriteres for nærmere gjennomgang. Bruk bare opplysningene du får. Hver påstand skal vise "
    "til et felt i kilden i hakeparentes, for eksempel [Antall tilbud]. Skriv aldri at noe er korrupsjon eller "
    "ulovlig. Skriv hva som bør sjekkes og hvilke dokumenter revisor bør be om. Maks 180 ord. Format:\n"
    "**Vurdering:** én setning\n**Hvorfor:**\n- punkt\n**Sjekk dette:**\n- punkt"
)


class Provider(Protocol):
    name: str
    model: str

    def complete(self, prompt: str) -> str: ...


class MistralProvider:
    name = "mistral"

    def __init__(self, api_key: str, model: str):
        self.api_key, self.model = api_key, model

    def complete(self, prompt: str) -> str:
        r = httpx.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "temperature": 0.2, "max_tokens": 500,
                  "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]},
            timeout=60,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()


class TemplateProvider:
    """Fallback uten LLM: setter sammen vurderingen fra flagg og nøkkeltall."""
    name, model = "mal", "regelbasert"

    def complete(self, prompt: str) -> str:
        d = json.loads(prompt.split("\n", 1)[1])
        flags = d.get("Røde flagg") or []
        level = {"Høy": "bør prioriteres", "Forhøyet": "kan vurderes"}.get(d.get("Prioritetsnivå"), "har lav prioritet")
        why = [f"- {f} [Røde flagg]" for f in flags[:5]] or ["- Ingen regelbaserte flagg utløst [Røde flagg]"]
        if d.get("Antall tilbud") is None:
            why.append("- Antall tilbud er ikke oppgitt [Antall tilbud]")
        checks = ["- Konkurransegrunnlag og protokoll for anskaffelsen"]
        if any("Direkte" in f or "konkurranse" in f for f in flags):
            checks.append("- Begrunnelse for valg av prosedyre og eventuell intensjonskunngjøring")
        if any("estimat" in f for f in flags):
            checks.append("- Kalkyle bak estimatet og endringer i kontraktsverdi")
        if any("rolle" in f.lower() or "adresse" in f.lower() for f in flags):
            checks.append("- Habilitetserklæringer for saksbehandlere og beslutningstakere")
        checks.append("- Fakturaer mot leverandøren i reskontro")
        return f"**Vurdering:** Kontrakten {level} (prioritet {d.get('Prioritet')}).\n**Hvorfor:**\n" + "\n".join(why) + "\n**Sjekk dette:**\n" + "\n".join(checks)


def provider(api_key: str, model: str) -> Provider:
    return MistralProvider(api_key, model) if api_key else TemplateProvider()


def build_input(c: dict) -> dict:
    """Feltene som sendes til modellen. Ingen personnavn."""
    return {
        "Tittel": c["title"], "Oppdragsgiver": c["buyer"], "Publisert": c["pub_date"], "Prosedyre": c["method"],
        "Begrunnelse for prosedyre": c.get("justification"), "Antall tilbud": c.get("num_bidders"),
        "Estimert verdi (NOK)": c.get("est_value"), "Tildelt verdi (NOK)": c.get("award_value"),
        "Tildelt mot estimat (%)": c.get("award_over_estimate_pct"), "Rammeavtale": bool(c.get("framework")),
        "Varighet (mnd)": c.get("contract_months"), "Leverandør": ", ".join(c.get("winner_names") or []),
        "Tidligere tildelinger samme par": c.get("repeat_pair_count"), "CPV": c.get("cpv"),
        "Prioritet": round(c["priority"]) if c.get("priority") is not None else None, "Prioritetsnivå": c.get("tier"),
        "Røde flagg": [f["label"] + (f" ({f['why']})" if f.get("why") and f.get("source") == "brreg" and "person" not in f["why"] else "")
                       for f in c.get("flags") or []],
    }


def assess(p: Provider, c: dict) -> tuple[dict, str]:
    data = build_input(c)
    prompt = "Kontrakt:\n" + json.dumps(data, ensure_ascii=False, default=str)
    return data, p.complete(prompt)
