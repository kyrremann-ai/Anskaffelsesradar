"""Flaggene skal beskrive observasjoner, ikke tolkninger."""
from types import SimpleNamespace

from radar.ingest.brreg import BrregIndex, entity_summary
from radar.scoring import rule_flags


def _row(**kw):
    base = dict(num_bidders=None, method="Åpen", repeat_pair_count=0, award_over_estimate_pct=None, prev_contract=0,
                accelerated=False, winner_market_share_pct=None, framework=False)
    return SimpleNamespace(**(base | kw))


def test_single_bid_only_when_one_bid_reported():
    assert [f["key"] for f in rule_flags(_row(num_bidders=1))] == ["single_bid"]
    keys = [f["key"] for f in rule_flags(_row(method="Direkte tildeling"))]
    assert keys == ["direct"]
    assert rule_flags(_row(num_bidders=1))[0]["label"] == "Ett registrert tilbud"


def _index(**ents):
    idx = BrregIndex.__new__(BrregIndex)
    idx.ent = {k: entity_summary(v) for k, v in ents.items()}
    idx.people, idx.dead, idx.auditor, idx.by_addr = {}, set(), set(), {}
    return idx


def test_deleted_and_missing_are_separate():
    idx = _index(**{"111111111": {"organisasjonsnummer": "111111111", "kind": "slettet", "slettedato": "2023-05-01"},
                    "222222222": {"organisasjonsnummer": "222222222", "kind": "ukjent"}})
    c = {"pub_date": "2024-01-01", "award_value": 1e6}
    deleted = idx.flags(c | {"winner_ids": ["111111111"]}, set())
    assert deleted[0]["key"] == "brreg_deleted" and deleted[0]["label"] == "Slettet enhet" and deleted[0]["weight"] > 0
    missing = [f for f in idx.flags(c | {"winner_ids": ["222222222"]}, set()) if f["key"] == "brreg_missing"]
    assert missing and missing[0]["weight"] == 0 and missing[0]["source"] == "datakvalitet"
