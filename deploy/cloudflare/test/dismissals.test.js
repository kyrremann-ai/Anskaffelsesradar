// node --test deploy/cloudflare/test/
import assert from "node:assert/strict";
import { test } from "node:test";
import { nextDismissal } from "../functions/api/dismissals/_db.js";

const T = "2026-11-25T12:00:00.000Z";
const base = { contract_id: "ted-1", flag_key: "brreg_shared_owner", reason: "konsern", note: " Samme konsern ", scope: "kontrakt" };

test("ny avskrivning: aktør og tid fra serveren", () => {
  const d = nextDismissal(null, { ...base, actor: "falsk", at: "1999" }, "kari@kommune.no", T);
  assert.equal(d.actor, "kari@kommune.no");
  assert.equal(d.at, T);
  assert.equal(d.note, "Samme konsern");
  assert.equal(d.orgnr, null);
  assert.equal(d.revoked_at, null);
});

test("ugyldig årsak og «annet» uten begrunnelse avvises", () => {
  assert.throws(() => nextDismissal(null, { ...base, reason: "lei av flagget" }, "a", T), /årsak/);
  assert.throws(() => nextDismissal(null, { ...base, reason: "annet", note: "  " }, "a", T), /begrunnelse/);
});

test("leverandøromfang krever riktig årsak og orgnr", () => {
  assert.throws(() => nextDismissal(null, { ...base, scope: "leverandor", reason: "datafeil", orgnr: "123456789" }, "a", T));
  assert.throws(() => nextDismissal(null, { ...base, scope: "leverandor", orgnr: "12" }, "a", T));
  assert.equal(nextDismissal(null, { ...base, scope: "leverandor", orgnr: "123456789" }, "a", T).orgnr, "123456789");
});

test("en avskrivning kan bare angres, én gang", () => {
  const d = nextDismissal(null, base, "a", T);
  assert.throws(() => nextDismissal(d, { ...base, note: "endret" }, "b", T), /angres/);
  const r = nextDismissal(d, { revoked: true }, "b", "2026-11-26T00:00:00.000Z");
  assert.equal(r.revoked_by, "b");
  assert.equal(r.note, "Samme konsern");
  assert.throws(() => nextDismissal(r, { revoked: true }, "b", T), /allerede/);
});
