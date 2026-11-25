import { nextDismissal, table } from "./_db.js";

const bad = (msg, status = 400) => new Response(msg, { status });

export async function onRequestPut({ env, params, request, data }) {
  if (!/^[A-Za-z0-9_-]{1,100}$/.test(params.key)) return bad("Ugyldig nøkkel");
  const text = await request.text();
  if (text.length > 20_000) return bad("For stor", 413);
  let b;
  try { b = JSON.parse(text); } catch { return bad("Ugyldig JSON"); }
  if (!b || typeof b !== "object") return bad("Ugyldig avskrivning");
  await table(env.DB);
  const old = await env.DB.prepare("SELECT data FROM dismissals WHERE key = ?1").bind(params.key).first();
  let d;
  try {
    d = nextDismissal(old ? JSON.parse(old.data) : null, b, data.email, new Date().toISOString());
  } catch (e) {
    return bad(e.message, 422);
  }
  await env.DB.prepare("INSERT INTO dismissals (key, data, at, actor) VALUES (?1, ?2, ?3, ?4) " +
    "ON CONFLICT(key) DO UPDATE SET data = ?2").bind(params.key, JSON.stringify(d), d.at, d.actor).run();
  return Response.json({ ok: true, dismissal: d });
}

export const onRequestDelete = () => bad("Avskrivninger slettes ikke. Angre i stedet.", 405);
