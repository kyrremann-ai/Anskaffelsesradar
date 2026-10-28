// Cloudflare-versjonen (radar.vikingsea.no): erstatter artifactens window.claude med Pages Functions.
// Klienten er den samme som artifacten (VITE_STATIC=1). Saker lagres i D1 via /api/cases,
// brukeren er e-posten Cloudflare Access har logget inn, og filer lastes ned i nettleseren.
(() => {
  const json = (r) => { if (!r.ok) throw new Error(`${r.status} ${r.statusText}`); return r.json(); };
  let me = null;
  const email = () => (me ??= fetch("api/me").then(json).then((m) => m.email || null));

  const db = {
    collection(name) {
      return {
        onSnapshot(next, error) {
          const tick = () => fetch(`api/${name}`).then(json)
            .then((docs) => next({ docs: Object.entries(docs).map(([id, d]) => ({ id, data: () => d })) }))
            .catch((e) => error && error(e));
          tick();
          const t = setInterval(tick, 20000); // andres endringer dukker opp innen 20 s
          return () => clearInterval(t);
        },
      };
    },
    doc(path) {
      return {
        set: (data) => fetch(`api/${path}`, { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(data) }).then(json),
      };
    },
  };

  const user = {
    id: email,
    me: () => email().then((e) => ({ name: e })),
    profiles: async (ids) => Object.fromEntries(ids.map((id) => [id, { name: id }])),
  };

  const downloads = {
    async save({ filename, data }) {
      const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(data), download: filename });
      document.body.append(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    },
  };

  const caps = { db, user, downloads };
  window.claude = { use: async (name) => { if (!caps[name]) throw new Error(`Ikke tilgjengelig: ${name}`); return caps[name]; } };
})();
