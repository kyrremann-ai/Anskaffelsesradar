// Avskrevne flagg i D1 (binding DB), som flag_dismissals i serverversjonen (backend/radar/dismissals.py).
// Én rad per avskrivning. Angring setter revoked_at; rader slettes ikke, så revisjonssporet blir stående.
export async function table(db) {
  await db.prepare("CREATE TABLE IF NOT EXISTS dismissals (key TEXT PRIMARY KEY, data TEXT NOT NULL, at TEXT NOT NULL, actor TEXT NOT NULL)").run();
}

// Samme koder som dismissals.REASONS
export const REASONS = ["konsern", "rammeavtale", "navnelikhet", "datafeil", "forklart", "annet"];
export const SUPPLIER_SCOPE = ["konsern", "navnelikhet"];

const str = (v, max) => typeof v === "string" && v.length <= max;

// Ny avskrivning, eller angring av en som finnes. Aktør og tidspunkt settes her, ikke av klienten.
export function nextDismissal(prev, body, actor, now) {
  if (prev) {
    if (prev.revoked_at) throw new Error("Avskrivningen er allerede angret");
    if (!body.revoked) throw new Error("En avskrivning kan bare angres, ikke endres");
    return { ...prev, revoked_at: now, revoked_by: actor };
  }
  if (!str(body.contract_id, 300) || !body.contract_id) throw new Error("Mangler contract_id");
  if (!str(body.flag_key, 100) || !body.flag_key) throw new Error("Mangler flag_key");
  if (!REASONS.includes(body.reason)) throw new Error("Ugyldig årsak");
  const note = str(body.note ?? "", 5000) ? (body.note ?? "").trim() : null;
  if (note === null) throw new Error("Ugyldig begrunnelse");
  if (body.reason === "annet" && !note) throw new Error("Årsaken «Annet» krever en begrunnelse");
  const scope = body.scope ?? "kontrakt";
  if (!["kontrakt", "leverandor"].includes(scope)) throw new Error("Ugyldig omfang");
  if (scope === "leverandor" && (!SUPPLIER_SCOPE.includes(body.reason) || !/^\d{9}$/.test(String(body.orgnr ?? ""))))
    throw new Error("Avskrivning for leverandøren krever konsern eller navnelikhet og et gyldig orgnr");
  return { contract_id: body.contract_id, flag_key: body.flag_key, reason: body.reason, note, scope,
    orgnr: scope === "leverandor" ? String(body.orgnr) : null, actor, at: now, revoked_at: null, revoked_by: null };
}
