// Saker i D1 (binding DB): én rad per sak, hele saken som JSON (samme form som i artifactens database).
export async function table(db) {
  await db.prepare("CREATE TABLE IF NOT EXISTS cases (key TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at TEXT NOT NULL, updated_by TEXT)").run();
}
