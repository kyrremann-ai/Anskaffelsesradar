// node --test deploy/cloudflare/test/
import assert from "node:assert/strict";
import { test } from "node:test";
import { nextCase } from "../functions/api/cases/_db.js";
import { onRequestDelete, onRequestPut } from "../functions/api/cases/[key].js";

const T = "2026-10-28T20:00:00.000Z";

test("ny sak får logglinje fra serveren, ikke fra klienten", () => {
  const { c } = nextCase(null, { contract_id: "ted-1", title: "Renhold", status: "Ny",
    events: [{ at: "1999", actor: "falsk", field: "opprettet" }] }, "kari@kommune.no", T);
  assert.equal(c.events.length, 1);
  assert.deepEqual(c.events[0], { at: T, actor: "kari@kommune.no", actor_id: "kari@kommune.no", field: "opprettet", old: null, new: "Ny" });
  assert.equal(c.created_at, T);
});

test("endringer logges ut fra hva som faktisk endret seg", () => {
  const prev = nextCase(null, { contract_id: "ted-1", status: "Ny" }, "kari@kommune.no", T).c;
  const body = { ...prev, note: "Sjekk protokoll", status: "Ny", events: [] }; // klienten prøver å tømme loggen
  const { c, changed } = nextCase(prev, body, "ola@kommune.no", "2026-10-29T08:00:00.000Z");
  assert.ok(changed);
  assert.equal(c.events.length, 2);
  assert.deepEqual(c.events[1], { at: "2026-10-29T08:00:00.000Z", actor: "ola@kommune.no", actor_id: "ola@kommune.no", field: "note", old: null, new: "Sjekk protokoll" });
});

test("faste felt og opprettet-tid kan ikke overskrives", () => {
  const prev = nextCase(null, { contract_id: "ted-1", title: "Renhold" }, "kari@kommune.no", T).c;
  const { c, changed } = nextCase(prev, { contract_id: "ted-2", title: "Annet", created_at: "2000" }, "ola@kommune.no", T);
  assert.equal(changed, false);
  assert.equal(c.contract_id, "ted-1");
  assert.equal(c.created_at, T);
});

test("ugyldig status og vurdering avvises", () => {
  const prev = nextCase(null, { contract_id: "ted-1" }, "k", T).c;
  assert.throws(() => nextCase(prev, { status: "Slettet" }, "k", T), /status/);
  assert.throws(() => nextCase(prev, { vurdering: "Kanskje" }, "k", T), /vurdering/);
});

test("grunnlaget lagres bare sammen med ny vurdering", () => {
  const prev = nextCase(null, { contract_id: "ted-1" }, "k", T).c;
  const g = { priority: 91, tier: "Høy", flags: ["Ett registrert tilbud"], build: { commit: "abc1234" } };
  const { c } = nextCase(prev, { vurdering: "Relevant", vurdert_grunnlag: g }, "k", T);
  assert.equal(c.vurdert_grunnlag.priority, 91);
  assert.equal(c.vurdert_grunnlag.at, T);
  const { c: c2 } = nextCase(c, { note: "x", vurdert_grunnlag: { priority: 1 } }, "k", T);
  assert.equal(c2.vurdert_grunnlag.priority, 91);
});

// D1 i minnet, nok til å kjøre PUT-handleren
function fakeDb() {
  const cases = new Map(), history = [];
  const stmt = (sql) => ({
    sql, args: [],
    bind(...a) { this.args = a; return this; },
    async first() { const r = cases.get(this.args[0]); return r ? { data: r } : null; },
    async run() {
      if (sql.startsWith("INSERT INTO cases")) cases.set(this.args[0], this.args[1]);
      if (sql.startsWith("INSERT INTO case_history")) history.push(this.args);
    },
  });
  return { cases, history, prepare: stmt, async batch(xs) { for (const s of xs) await s.run(); } };
}

test("PUT lagrer sak og historikk med Access-e-post som aktør", async () => {
  const db = fakeDb();
  const put = (body, email) => onRequestPut({ env: { DB: db }, params: { key: "ted-1" }, data: { email },
    request: new Request("https://x", { method: "PUT", body: JSON.stringify(body) }) });
  assert.equal((await put({ contract_id: "ted-1", status: "Ny", events: [] }, "kari@kommune.no")).status, 200);
  assert.equal((await put({ status: "Under vurdering" }, "ola@kommune.no")).status, 200);
  assert.equal((await put({ status: "Tull" }, "ola@kommune.no")).status, 422);
  const saved = JSON.parse(db.cases.get("ted-1"));
  assert.deepEqual(saved.events.map((e) => [e.actor, e.field]), [["kari@kommune.no", "opprettet"], ["ola@kommune.no", "status"]]);
  assert.equal(db.history.length, 2);
});

test("saker kan ikke slettes", async () => {
  assert.equal((await onRequestDelete()).status, 405);
});
