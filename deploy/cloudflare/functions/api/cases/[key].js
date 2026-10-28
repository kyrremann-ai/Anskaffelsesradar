import { nextCase, table } from "./_db.js";

const bad = (msg, status = 400) => new Response(msg, { status });

export async function onRequestPut({ env, params, request, data }) {
  if (!/^[A-Za-z0-9_-]{1,200}$/.test(params.key)) return bad("Ugyldig nøkkel");
  const body = await request.text();
  if (body.length > 500_000) return bad("For stor", 413);
  let b;
  try { b = JSON.parse(body); } catch { return bad("Ugyldig JSON"); }
  if (!b || typeof b !== "object") return bad("Ugyldig sak");
  await table(env.DB);
  const old = await env.DB.prepare("SELECT data FROM cases WHERE key = ?1").bind(params.key).first();
  let res;
  try {
    res = nextCase(old ? JSON.parse(old.data) : null, b, data.email, new Date().toISOString());
  } catch (e) {
    return bad(e.message, 422);
  }
  if (!res.changed) return Response.json({ ok: true, changed: false });
  const json = JSON.stringify(res.c);
  await env.DB.batch([
    env.DB.prepare("INSERT INTO cases (key, data, updated_at, updated_by) VALUES (?1, ?2, ?3, ?4) " +
      "ON CONFLICT(key) DO UPDATE SET data = ?2, updated_at = ?3, updated_by = ?4")
      .bind(params.key, json, res.c.updated_at, data.email),
    env.DB.prepare("INSERT INTO case_history (key, at, actor, data) VALUES (?1, ?2, ?3, ?4)")
      .bind(params.key, res.c.updated_at, data.email, json),
  ]);
  return Response.json({ ok: true, changed: true });
}

// Saker slettes ikke, så revisjonssporet blir stående. Avslutt saken med en status i stedet.
export const onRequestDelete = () =>
  bad("Saker kan ikke slettes. Sett status til «Avsluttet – ingen funn» i stedet.", 405);
