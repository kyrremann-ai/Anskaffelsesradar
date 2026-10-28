import { table } from "./_db.js";

const bad = (msg, status = 400) => new Response(msg, { status });

export async function onRequestPut({ env, params, request, data }) {
  if (!/^[A-Za-z0-9_-]{1,200}$/.test(params.key)) return bad("Ugyldig nøkkel");
  const body = await request.text();
  if (body.length > 500_000) return bad("For stor", 413);
  let c;
  try { c = JSON.parse(body); } catch { return bad("Ugyldig JSON"); }
  if (!c || typeof c !== "object" || !Array.isArray(c.events)) return bad("Ugyldig sak");
  await table(env.DB);
  const old = await env.DB.prepare("SELECT data FROM cases WHERE key = ?1").bind(params.key).first();
  const prev = old ? JSON.parse(old.data).events : [];
  // Loggen er bare-legg-til: eldre hendelser kommer fra databasen, ikke fra klienten,
  // og nye hendelser får Access-e-posten som aktør (ikke det klienten oppgir).
  if (c.events.length < prev.length) return bad("Loggen kan ikke forkortes", 409);
  c.events = [...prev, ...c.events.slice(prev.length).map((e) => ({ ...e, actor: data.email, actor_id: data.email }))];
  await env.DB.prepare("INSERT INTO cases (key, data, updated_at, updated_by) VALUES (?1, ?2, ?3, ?4) " +
    "ON CONFLICT(key) DO UPDATE SET data = ?2, updated_at = ?3, updated_by = ?4")
    .bind(params.key, JSON.stringify(c), new Date().toISOString(), data.email).run();
  return Response.json({ ok: true });
}
