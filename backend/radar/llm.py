"""LLM-adapter for AI-vurdering. Mistral (EU) eller Claude (Anthropic), byttbar.

Personvern: vi sender bare kontraktsfelt og flagg. Aldri navn fra Brreg-roller.
Uten API-nøkkel brukes en regelbasert mal, så appen virker uten LLM.

Kallene går gjennom agent-core (felles pakke for prosjektene): SDK-ene prøver selv to ganger
ved 429/5xx/nettfeil. Hvert kall logges i runs.sqlite i datamappen (tokens, tid, status,
feilmelding, kostnad for Claude). Feiler hovedleverandøren midlertidig og begge nøklene finnes,
prøves den andre én gang (providers + assess_first). Ingen ruter: dette er én jobb.
"""
from __future__ import annotations

import functools
import json
import time
from pathlib import Path
from typing import Protocol

from agent_core import RunLog, get_provider, is_transient
from agent_core.telemetry import describe

SYSTEM = (
    "Du er en erfaren kommunerevisor. Skriv en kort, nøktern vurdering på norsk bokmål av om en offentlig "
    "kontrakt bør prioriteres for nærmere gjennomgang. Bruk bare opplysningene du får. Hver påstand skal vise "
    "til et felt i kilden i hakeparentes, for eksempel [Antall tilbud]. Skriv aldri at noe er korrupsjon eller "
    "ulovlig. Skriv hva som bør sjekkes og hvilke dokumenter revisor bør be om. Maks 180 ord. Format:\n"
    "**Vurdering:** én setning\n**Hvorfor:**\n- punkt\n**Sjekk dette:**\n- punkt"
)

# Listepris i USD per million tokens (Anthropic, sjekket 2026-11-14), for kostnad i runs.sqlite.
# Mistral faktureres etter egne priser og logges uten kostnad.
PRICES = {
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00},
    "claude-opus-5-5": {"input": 4.00, "output": 20.00},
}


class Provider(Protocol):
    name: str
    model: str

    def complete(self, prompt: str) -> str: ...


class CoreProvider:
    """Én agent-core-leverandør med fast modell og instruks. ``name`` lagres i assessments."""

    def __init__(self, name: str, model: str, core, max_tokens: int, log: RunLog | None = None):
        self.name, self.model, self.core, self.max_tokens, self.log = name, model, core, max_tokens, log

    def complete(self, prompt: str) -> str:
        t0 = time.perf_counter()
        try:
            out = self.core.complete(model=self.model, system=SYSTEM, prompt=prompt,
                                     max_tokens=self.max_tokens, temperature=0.2)
        except Exception as e:
            self._log(t0, error=e)
            raise
        self._log(t0, out=out)
        return out.text.strip()

    def _log(self, t0: float, out=None, error=None) -> None:
        if self.log is None:
            return
        try:
            self.log.log(client="radar", agent="ai_vurdering", tier="single", model=self.model,
                         route_reason=self.name, input_tokens=out.input_tokens if out else 0,
                         output_tokens=out.output_tokens if out else 0,
                         latency_ms=int((time.perf_counter() - t0) * 1000), provider=self.core.name,
                         status="error" if error else "ok", error=describe(error) if error else None)
        except Exception:  # noqa: BLE001 - logging skal aldri stoppe en vurdering
            pass


def MistralProvider(api_key: str, model: str, log: RunLog | None = None) -> CoreProvider:
    return CoreProvider("mistral", model, get_provider("mistral", api_key=api_key), 500, log)


def ClaudeProvider(api_key: str, model: str, log: RunLog | None = None) -> CoreProvider:
    return CoreProvider("claude", model, get_provider("anthropic", api_key=api_key), 600, log)


@functools.lru_cache
def _run_log(path: str) -> RunLog:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return RunLog(path, prices=PRICES)


def run_log(s) -> RunLog:
    return _run_log(str(s.data_dir / "runs.sqlite"))


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


def providers(s, log: RunLog | None = None) -> list[Provider]:
    """Leverandører i prioritert rekkefølge. LLM_PROVIDER=claude|mistral velger den første;
    ellers Mistral (EU) foran Claude. Den andre brukes bare ved midlertidig feil. Uten nøkler: malen."""
    have = {}
    if s.mistral_api_key:
        have["mistral"] = lambda: MistralProvider(s.mistral_api_key, s.mistral_model, log)
    if s.anthropic_api_key:
        have["claude"] = lambda: ClaudeProvider(s.anthropic_api_key, s.claude_model, log)
    if not have:
        return [TemplateProvider()]
    want = (s.llm_provider or "").lower()
    if want in ("claude", "mistral") and want not in have:
        return [TemplateProvider()]  # som før: et eksplisitt valg uten nøkkel gir malen
    order = sorted(have, key=lambda n: (n != want, n != "mistral"))
    return [have[n]() for n in order]


def provider(s) -> Provider:
    """Hovedleverandøren (uten logging). Brukes til visning av hvilken LLM som er aktiv."""
    return providers(s)[0]


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


def assess_first(ps: list[Provider], c: dict) -> tuple[dict, str, Provider]:
    """Som assess, men går videre til neste leverandør ved midlertidig feil (429/5xx/nett).
    Andre feil (f.eks. 400, feil nøkkel) kastes med en gang. Returnerer leverandøren som svarte."""
    for i, p in enumerate(ps):
        try:
            data, text = assess(p, c)
            return data, text, p
        except Exception as e:
            if i == len(ps) - 1 or not is_transient(e):
                raise
    raise RuntimeError("ingen leverandør")  # pragma: no cover
