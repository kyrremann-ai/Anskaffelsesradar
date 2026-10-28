// Hele domenet ligger bak Cloudflare Access. Dette er en ekstra sperre: uten Access-e-post, ingen API.
export async function onRequest(ctx) {
  const email = (ctx.request.headers.get("Cf-Access-Authenticated-User-Email") || "").toLowerCase();
  if (!email) return new Response("Ikke innlogget", { status: 401 });
  ctx.data.email = email;
  return ctx.next();
}
