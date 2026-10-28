from pathlib import Path

from radar.ingest.ted import parse_notice

XML = (Path(__file__).parent / "fixtures" / "can_sample.xml").read_bytes()


def test_parse_eforms_can():
    c = parse_notice(XML, "123456-2025", "2025-03-01")
    assert c["buyer_id"] == "964338531" and c["buyer"] == "Testkommune"
    assert c["method"] == "Direkte tildeling" and c["justification"] == "urgency"
    assert c["num_bidders"] == 1 and c["award_value"] == 2500000 and c["est_value"] == 2000000
    assert c["winner_ids"] == ["912345678"] and c["winner_names"] == ["Leverandør AS"]
    assert c["cpv"] == "45000000" and c["sector"] == "Bygg og anlegg" and c["region"] == "Vestland"
    assert c["framework"] == 0 and round(c["contract_months"]) == 12


def test_parse_ted_search_fields():
    from radar.ingest.ted import parse_fields
    n = {"publication-number": "1-2026", "publication-date": "2026-01-05+01:00",
         "notice-title": {"eng": "Norway – Lawnmowers – __NEEDS_TRANSLATION_x__", "dan": "Norge – Plæneklippere – Klipping"},
         "buyer-name": {"eng": ["__NEEDS_TRANSLATION_y__"]}, "buyer-identifier": ["964338531"], "buyer-country-sub": ["NO0A2"],
         "winner-identifier": ["912345678", "912345678"], "winner-name": {"eng": ["Leverandør AS"]},
         "procedure-type": "neg-wo-call", "classification-cpv": ["45000000"], "result-value-notice": "2500000",
         "result-value-cur-notice": "NOK", "received-submissions-type-code": ["t-esubm", "tenders"],
         "received-submissions-type-val": ["4", "1"], "framework-agreement-lot": ["none"]}
    c = parse_fields(n)
    assert c["title"] == "Klipping" and c["buyer"] is None and c["buyer_id"] == "964338531"
    assert c["method"] == "Direkte tildeling" and c["num_bidders"] == 1 and c["award_value"] == 2500000
    assert c["winner_ids"] == ["912345678"] and c["winner_names"] == ["Leverandør AS"]
    assert c["region"] == "Vestland" and c["framework"] == 0 and c["pub_date"] == "2026-01-05"


def test_parse_doffin_hit():
    from radar.ingest.doffin import parse_hit
    h = {"id": "2026-100001", "heading": "Snørydding", "buyer": [{"id": "x", "organizationId": "964338531", "name": "Testkommune"}],
         "type": "RESULT", "allTypes": ["RESULT"], "publicationDate": "2026-02-01T00:00:00Z", "receivedTenders": 1,
         "estimatedValue": {"currencyCode": "NOK", "amount": 900000}, "cpvCodes": ["90620000"],
         "lots": [{"heading": "", "description": "", "winner": [{"id": "w", "organizationId": "912345678", "name": "Brøyt AS"}]}]}
    c = parse_hit(h)
    assert c["id"] == "doffin-2026-100001" and c["source"] == "doffin" and c["buyer_id"] == "964338531"
    assert c["winner_ids"] == ["912345678"] and c["num_bidders"] == 1 and c["est_value"] == 900000
    assert c["sector"] == "Miljø og renovasjon" and c["pub_date"] == "2026-02-01"
    assert parse_hit({**h, "lots": []}) is None
