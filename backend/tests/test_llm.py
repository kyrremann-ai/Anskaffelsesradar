"""LLM-laget: valg av leverandør, fallback ved midlertidig feil, logging. Uten nettverk."""
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from agent_core import Completion, RunLog

from radar import llm


@dataclass
class S:
    mistral_api_key: str = ""
    mistral_model: str = "mistral-small-latest"
    anthropic_api_key: str = ""
    claude_model: str = "claude-haiku-5-5"
    llm_provider: str = ""
    data_dir: Path = field(default_factory=lambda: Path("data"))


class StatusError(Exception):
    def __init__(self, code):
        super().__init__(f"HTTP {code}")
        self.status_code = code


class Core:
    def __init__(self, name, result):
        self.name, self.result, self.calls = name, result, 0

    def complete(self, **kw):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


C = {"title": "Renhold", "buyer": "Kommune", "pub_date": "2026-01-01", "method": "open", "priority": 90,
     "tier": "Høy", "flags": [{"label": "Ett registrert tilbud"}]}


@pytest.mark.parametrize(("s", "names"), [
    (S(), ["mal"]),
    (S(mistral_api_key="m"), ["mistral"]),
    (S(anthropic_api_key="a"), ["claude"]),
    (S(mistral_api_key="m", anthropic_api_key="a"), ["mistral", "claude"]),
    (S(mistral_api_key="m", anthropic_api_key="a", llm_provider="claude"), ["claude", "mistral"]),
    (S(mistral_api_key="m", llm_provider="claude"), ["mal"]),  # som før: valgt uten nøkkel gir malen
])
def test_providers_order(s, names):
    assert [p.name for p in llm.providers(s)] == names
    assert llm.provider(s).name == names[0]


def test_fallback_on_transient_error_and_logging(tmp_path):
    log = RunLog(tmp_path / "runs.sqlite", prices=llm.PRICES)
    down = llm.CoreProvider("mistral", "mistral-small-latest", Core("mistral", StatusError(503)), 500, log)
    up = llm.CoreProvider("claude", "claude-haiku-5-5",
                          Core("anthropic", Completion("**Vurdering:** ok", "claude-haiku-5-5", 1000, 200)), 600, log)
    data, text, used = llm.assess_first([down, up], C)
    assert used is up and text.startswith("**Vurdering")
    assert data["Tittel"] == "Renhold"
    rows = log.rows("SELECT provider, model, status, error, input_tokens, cost_usd FROM runs ORDER BY id")
    assert rows == [
        ("mistral", "mistral-small-latest", "error", "StatusError: HTTP 503", 0, None),
        ("anthropic", "claude-haiku-5-5", "ok", None, 1000, 0.0002),
    ]


def test_bad_request_is_not_retried(tmp_path):
    first = llm.CoreProvider("mistral", "m", Core("mistral", StatusError(400)), 500)
    second = Core("anthropic", Completion("x", "c"))
    with pytest.raises(StatusError):
        llm.assess_first([first, llm.CoreProvider("claude", "c", second, 600)], C)
    assert second.calls == 0


def test_template_still_works_without_keys():
    data, text, used = llm.assess_first(llm.providers(S()), C)
    assert used.name == "mal" and "Vurdering" in text


def test_real_adapters_are_agent_core():
    m = llm.MistralProvider("k", "mistral-small-latest")
    c = llm.ClaudeProvider("k", "claude-haiku-5-5")
    assert (m.core.name, c.core.name) == ("mistral", "anthropic")
    assert str(m.core._client.base_url).startswith("https://api.mistral.ai")
