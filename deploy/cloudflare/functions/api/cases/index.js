import { table } from "./_db.js";

export async function onRequestGet({ env }) {
  await table(env.DB);
  const { results } = await env.DB.prepare("SELECT key, data FROM cases").all();
  return Response.json(Object.fromEntries(results.map((r) => [r.key, JSON.parse(r.data)])),
    { headers: { "Cache-Control": "no-store" } });
}
