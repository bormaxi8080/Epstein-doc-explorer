/** Read-only, paginated inventory views. Catalogue coverage is separate from OCR coverage. */
import type { Express, Request, Response } from 'express';
import Database from 'better-sqlite3';
import fs from 'fs';
import path from 'path';

export function registerArchive(app: Express, viewer: Database.Database, dbPath: string): () => void {
  const requested = process.env.INVENTORY_DB_PATH;
  const filename = requested || path.join(path.dirname(dbPath), 'inventory.sqlite3');
  if (requested && !fs.existsSync(filename)) throw new Error('Configured inventory catalogue is missing');
  let catalog: Database.Database | undefined;
  let identity: fs.Stats | undefined;
  let metadata: any = null;
  const has = (name: string) => !!viewer.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?").get(name);
  if (fs.existsSync(filename)) {
    identity = fs.statSync(filename);
    catalog = new Database(filename, { readonly: true, fileMustExist: true });
    catalog.pragma('cache_size = -8192');
    catalog.pragma('temp_store = MEMORY');
    metadata = Object.fromEntries((catalog.prepare('SELECT key,value FROM meta').all() as any[]).map(r => [r.key, JSON.parse(r.value)]));
    if (metadata.format !== 'explorer-inventory-catalog' || metadata.version !== 1) throw new Error('Unsupported inventory catalogue');
    if (has('ingestion_archive_sources') && viewer.prepare('SELECT 1 FROM ingestion_archive_sources WHERE source_sha256!=? LIMIT 1').get(metadata.source_sha256)) throw new Error('Mixed inventory and Viewer sources');
  }
  const manifestPath = path.join(path.dirname(dbPath), 'manifest.json');
  const bundle = fs.existsSync(manifestPath) && fs.statSync(manifestPath).size < 8*1024*1024 ? JSON.parse(fs.readFileSync(manifestPath, 'utf8')) : {};
  const counts = {
    documents: (viewer.prepare('SELECT COUNT(*) AS n FROM documents').get() as any).n,
    approved_imported: has('ingestion_documents') ? (viewer.prepare("SELECT COUNT(*) AS n FROM ingestion_documents WHERE review_status='approved'").get() as any).n : 0,
    pending_imported: has('ingestion_documents') ? (viewer.prepare("SELECT COUNT(*) AS n FROM ingestion_documents WHERE review_status!='approved'").get() as any).n : 0,
    archive_assets_with_text: has('ingestion_asset_documents') ? (viewer.prepare('SELECT COUNT(*) AS n FROM (SELECT source_sha256,url FROM ingestion_asset_documents GROUP BY source_sha256,url)').get() as any).n : null,
  };
  app.use(['/api/archive', '/api/dataset'], (_req, res, next) => {
    try {
      if (identity) {
        const now = fs.statSync(filename);
        if (now.dev !== identity.dev || now.ino !== identity.ino || now.size !== identity.size || now.mtimeMs !== identity.mtimeMs) throw new Error();
      }
      next();
    } catch { res.status(503).json({ error: 'Catalogue storage changed; restore the volume and restart the Viewer' }); }
  });
  app.get('/api/dataset', (_req, res) => res.json({
    synthetic: bundle.synthetic === true,
    private_review: bundle.include_unreviewed === true,
    catalogue: metadata ? { snapshot_id: metadata.snapshot_id, source_sha256: metadata.source_sha256, complete: metadata.complete, ...metadata.validation } : null,
    viewer: counts,
    processing_complete: false,
    coverage_note: 'Inventory completeness describes source links. Viewer counts describe this immutable text bundle; they do not certify full download or OCR completion.',
  }));
  function integer(value: unknown, fallback: number, max = Number.MAX_SAFE_INTEGER) {
    if (value === undefined) return fallback;
    if (typeof value !== 'string' || !/^\d+$/.test(value)) throw new Error('Invalid pagination');
    const n = Number(value); if (!Number.isSafeInteger(n) || n > max) throw new Error('Invalid pagination');
    return n;
  }
  const route = (run: (req: Request, res: Response, db: Database.Database) => unknown) => (req: Request, res: Response) => {
    if (!catalog) return res.status(503).json({ error: 'Full inventory catalogue is not installed' });
    try { return run(req, res, catalog); } catch { return res.status(400).json({ error: 'Invalid catalogue query' }); }
  };
  app.get('/api/archive/children', route((req, res, db) => {
    const limit = Math.max(1, integer(req.query.limit, 50, 100));
    const parent = req.query.parent === undefined || req.query.parent === '' ? null : integer(req.query.parent, 0);
    const rows = db.prepare('SELECT seq,parent,node_id,kind,name,asset_id FROM nodes WHERE parent IS ? AND seq>? ORDER BY seq LIMIT ?').all(parent, integer(req.query.after, 0), limit+1) as any[];
    const more = rows.length > limit; rows.splice(limit);
    res.json({ nodes: rows, next: more ? rows[rows.length-1].seq : null });
  }));
  app.get('/api/archive/assets', route((req, res, db) => {
    const limit = Math.max(1, integer(req.query.limit, 50, 100));
    const q = String(req.query.q || '').trim().slice(0, 200);
    const kind = String(req.query.kind || '').slice(0, 40);
    const rows = db.prepare(`SELECT rowid AS cursor,id,url,name,kind,bytes FROM assets WHERE rowid>? ${q ? 'AND rowid IN (SELECT rowid FROM assets_fts WHERE assets_fts MATCH ?)' : ''} ${kind ? 'AND kind=?' : ''} ORDER BY rowid LIMIT ?`).all(integer(req.query.after, 0), ...(q ? ['"'+q.replaceAll('"', '""')+'"'] : []), ...(kind ? [kind] : []), limit+1) as any[];
    const more = rows.length > limit; rows.splice(limit);
    res.json({ assets: rows, next: more ? rows[rows.length-1].cursor : null });
  }));
  app.get('/api/archive/asset/:id', route((req, res, db) => {
    const asset = db.prepare("SELECT id,url,name,kind,bytes,json_extract(payload,'$.mime_type') AS mime_type FROM assets WHERE id=?").get(req.params.id) as any;
    if (!asset) return res.status(404).json({ error: 'Unknown inventory asset' });
    const after = integer(req.query.after, 0), limit = 50;
    const rows = db.prepare('SELECT seq,parent,node_id,name FROM nodes WHERE asset_id=? AND seq>? ORDER BY seq LIMIT ?').all(req.params.id, after, limit+1) as any[];
    const more = rows.length > limit; rows.splice(limit);
    const review = has('ingestion_documents');
    let documents: any[] = [], linkage = 'current';
    const selection = `SELECT DISTINCT d.doc_id,d.one_sentence_summary,d.category,${review ? "COALESCE(i.review_status,'upstream')" : "'upstream'"} AS review_status FROM documents d ${review ? 'LEFT JOIN ingestion_documents i ON i.doc_id=d.doc_id' : ''}`;
    if (has('ingestion_asset_documents')) documents = viewer.prepare(selection+ ' JOIN ingestion_asset_documents s ON s.doc_id=d.doc_id WHERE s.source_sha256=? AND s.url=? ORDER BY d.doc_id LIMIT 101').all(metadata.source_sha256, asset.url) as any[];
    else if (has('ingestion_archive_sources') && (viewer.prepare('SELECT COUNT(*) AS n FROM ingestion_archive_sources').get() as any).n <= 10000) {
      linkage = 'legacy';
      documents = viewer.prepare(selection+ " JOIN ingestion_archive_sources s ON s.doc_id=d.doc_id WHERE s.source_sha256=? AND COALESCE(json_extract(s.metadata,'$.download_url'),json_extract(s.metadata,'$.url'))=? ORDER BY d.doc_id LIMIT 101").all(metadata.source_sha256, asset.url) as any[];
    } else linkage = 'unavailable';
    const moreDocs = documents.length > 100; documents.splice(100);
    res.json({ asset, placements: rows, next: more ? rows[rows.length-1].seq : null, documents, more_documents: moreDocs, linkage });
  }));
  return () => { catalog?.close(); };
}
