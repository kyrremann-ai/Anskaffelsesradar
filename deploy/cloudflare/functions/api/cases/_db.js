// Saker i D1 (binding DB): én rad per sak, hele saken som JSON (samme form som i artifactens database).
// case_history har hver lagrede versjon, så revisjonssporet ikke avhenger av siste versjon av saken.
export async function table(db) {
  await db.batch([
    db.prepare("CREATE TABLE IF NOT EXISTS cases (key TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at TEXT NOT NULL, updated_by TEXT)"),
    db.prepare("CREATE TABLE IF NOT EXISTS case_history (id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL, at TEXT NOT NULL, actor TEXT NOT NULL, data TEXT NOT NULL)"),
  ]);
}

// Feltene revisor kan endre. Alt annet i saken settes av serveren eller tas fra forrige versjon.
export const FIELDS = ["status", "assignee", "note", "vurdering", "vurdering_grunn"];
export const STATUSES = ["Ny", "Under vurdering", "Fulgt opp", "Avsluttet – ingen funn", "Avsluttet – funn"];
export const VURDERINGER = ["", "Relevant", "Ikke relevant"];
const FIXED = ["contract_id", "title", "buyer", "score"];

// Lager neste versjon av saken. Logglinjene lages her fra det som faktisk endret seg, med aktøren
// fra Cloudflare Access. Logglinjer klienten sender med, brukes ikke.
export function nextCase(prev, body, actor, now) {
  for (const k of FIELDS) {
    if (k in body && body[k] != null && typeof body[k] !== "string") throw new Error(`Ugyldig ${k}`);
  }
  if ("status" in body && body.status != null && !STATUSES.includes(body.status)) throw new Error("Ugyldig status");
  if ("vurdering" in body && body.vurdering != null && !VURDERINGER.includes(body.vurdering)) throw new Error("Ugyldig vurdering");
  if (!prev) {
    if (typeof body.contract_id !== "string" || !body.contract_id) throw new Error("Mangler contract_id");
    const c = { events: [] };
    for (const k of FIXED) if (k in body) c[k] = body[k];
    for (const k of FIELDS) c[k] = body[k] ?? (k === "status" ? "Ny" : "");
    c.created_at = now;
    c.updated_at = now;
    c.events.push({ at: now, actor, actor_id: actor, field: "opprettet", old: null, new: c.status });
    return { c, changed: true };
  }
  const c = { ...prev, events: [...(prev.events ?? [])] };
  let changed = false;
  for (const k of FIELDS) {
    if (!(k in body)) continue;
    const old = prev[k] ?? "", neu = body[k] ?? "";
    if (old === neu) continue;
    c[k] = neu;
    c.events.push({ at: now, actor, actor_id: actor, field: k, old: old || null, new: neu || null });
    changed = true;
  }
  // Grunnlaget (prioritet, flagg, programversjon) da vurderingen ble satt. Lagres bare sammen med en ny vurdering.
  if (changed && "vurdering" in body && body.vurdering !== (prev.vurdering ?? "") && body.vurdert_grunnlag && typeof body.vurdert_grunnlag === "object") {
    c.vurdert_grunnlag = { ...body.vurdert_grunnlag, at: now };
  }
  if (changed) c.updated_at = now;
  return { c, changed };
}
