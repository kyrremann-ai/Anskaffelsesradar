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
