#!/usr/bin/env node

import express from 'express';
import cors from 'cors';
import Database from 'better-sqlite3';
import fs from 'fs';
import path from 'path';

const app = express();
const PORT = Number(process.env.PORT || 3001);
const DB_PATH = process.env.DB_PATH || 'document_analysis.db';
const DB_READONLY = process.env.DB_READONLY !== 'false';
const ALLOWED_ORIGINS = (process.env.ALLOWED_ORIGINS && process.env.ALLOWED_ORIGINS.trim())
  ? process.env.ALLOWED_ORIGINS.split(',').map(o => o.trim())
  : [`http://127.0.0.1:${PORT}`, `http://localhost:${PORT}`, 'http://localhost:5173', 'http://localhost:3000', 'https://epsteinvisualizer.com', 'https://www.epsteinvisualizer.com'];

console.log('Allowed CORS origins:', ALLOWED_ORIGINS);

// CORS configuration with origin whitelist
app.use(cors({
  origin: (origin, callback) => {
    // Allow requests with no origin (same-origin requests, mobile apps, curl)
    if (!origin) return callback(null, true);

    // Allow localhost origins for development
    if (ALLOWED_ORIGINS.includes(origin)) {
      return callback(null, true);
    }

    // Allow Render deployment domains (*.onrender.com)
    if (origin && (origin.includes('.onrender.com') || origin.endsWith('onrender.com'))) {
      return callback(null, true);
    }

    // Log rejected origins for debugging
    console.warn(`CORS blocked origin: ${origin}`);

    // Reject other origins
    callback(new Error('Not allowed by CORS'));
  },
  credentials: true,
  maxAge: 86400
}));

// Request size limits
app.use(express.json({ limit: '10mb' }));

// Simple rate limiting middleware
const requestCounts = new Map<string, { count: number; resetTime: number }>();
const RATE_LIMIT_WINDOW = 15 * 60 * 1000; // 15 minutes
const RATE_LIMIT_MAX_REQUESTS = 1000; // Max requests per window

app.use((req, res, next) => {
  const ip = req.ip || req.socket.remoteAddress || 'unknown';
  const now = Date.now();
  const userData = requestCounts.get(ip);

  if (!userData || now > userData.resetTime) {
    requestCounts.set(ip, { count: 1, resetTime: now + RATE_LIMIT_WINDOW });
    return next();
  }

  if (userData.count >= RATE_LIMIT_MAX_REQUESTS) {
    return res.status(429).json({ error: 'Too many requests, please try again later' });
  }

  userData.count++;
  next();
});

// Initialize database with error handling
let db: Database.Database;
try {
  db = new Database(DB_PATH, { readonly: DB_READONLY, fileMustExist: true });
  if (!DB_READONLY) db.pragma('journal_mode = WAL');
  db.pragma('cache_size = -8192');
  const memoryMb=Number(process.env.SQLITE_MEMORY_MB || 512);
  if (!Number.isInteger(memoryMb) || memoryMb<64 || memoryMb>2048) throw new Error('Invalid SQLITE_MEMORY_MB');
  db.pragma(`hard_heap_limit = ${memoryMb*1024*1024}`);
  const requestedTemp=process.env.SQLITE_TMPDIR?.trim();
  if (requestedTemp) {
    if (!path.isAbsolute(requestedTemp)) throw new Error('SQLITE_TMPDIR must be absolute');
    const temporary=fs.realpathSync(requestedTemp);
    const identity=fs.statSync(temporary);
    if (!identity.isDirectory()) throw new Error('SQLITE_TMPDIR must exist');
    fs.accessSync(temporary,fs.constants.W_OK);
    db.pragma(`temp_store_directory = '${temporary.replaceAll("'","''")}'`);
    if (db.pragma('temp_store_directory',{simple:true})!==temporary) throw new Error('SQLite temporary path was not applied');
    db.pragma('temp_store = FILE');
    app.use((_req,res,next)=>{
      try {const current=fs.statSync(temporary);if(current.dev!==identity.dev || current.ino!==identity.ino) throw new Error('Temporary volume changed');next();}
      catch {res.status(503).json({error:'Configured SQLite temporary storage unavailable'});}
    });
    console.log(`SQLite scratch: explicit directory; heap cap ${memoryMb} MiB`);
  } else {
    db.pragma('temp_store = MEMORY');
    console.log(`SQLite scratch: bounded memory; heap cap ${memoryMb} MiB`);
  }
  console.log(`✓ Database initialized: ${DB_PATH}`);
} catch (error) {
  console.error('Failed to initialize database:', error);
  process.exit(1);
}

// New bundles serve cluster metadata from SQLite without loading every tag.
let tagClusters: any[] = [];
try {
  if (db.prepare("SELECT 1 FROM sqlite_master WHERE name='corpus_clusters'").get()) {
    tagClusters=(db.prepare(`SELECT c.id,c.name,c.exemplars,(SELECT COUNT(*) FROM corpus_tags t WHERE t.cluster_id=c.id) AS tagCount FROM corpus_clusters c ORDER BY c.id`).all() as any[])
      .map(c=>({...c,exemplars:JSON.parse(c.exemplars)}));
  } else {
    tagClusters=JSON.parse(fs.readFileSync(process.env.TAG_CLUSTERS_PATH || path.join(process.cwd(),'tag_clusters.json'),'utf8'));
  }
} catch (error) {console.error('Cluster metadata unavailable',error);}

// Get all actors (nodes) with alias resolution
app.get('/api/actors', (req, res) => {
  try {
    const actors = db.prepare(`
      SELECT DISTINCT
        COALESCE(ea.canonical_name, rt.actor) as name,
        COUNT(*) as connection_count
      FROM rdf_triples rt
      LEFT JOIN entity_aliases ea ON rt.actor = ea.original_name
      GROUP BY COALESCE(ea.canonical_name, rt.actor)
      ORDER BY connection_count DESC
      LIMIT 100
    `).all();
    res.json(actors);
  } catch (error) {
    console.error('Error in /api/actors:', error);
    res.status(500).json({ error: 'An internal error occurred' });
  }
});

// Helper function to validate and sanitize inputs
function validateLimit(limit: any): number {
  const parsed = parseInt(limit);
  if (isNaN(parsed) || parsed < 1) return 500;
  return Math.min(20000, Math.max(1, parsed));
}

function validateClusterIds(clusters: any): number[] {
  if (!clusters) return [];
  return String(clusters)
    .split(',')
    .map(Number)
    .filter(n => !isNaN(n) && n >= 0 && Number.isInteger(n))
    .slice(0, 50); // Limit to 50 clusters max
}

function validateCategories(categories: any): string[] {
  if (!categories) return [];
  return String(categories)
    .split(',')
    .map(c => c.trim())
    .filter(c => c.length > 0 && c.length < 100) // Reasonable category name length
    .slice(0, 50); // Limit to 50 categories max
}

function validateYearRange(yearMin: any, yearMax: any): [number, number] | null {
  if (!yearMin && !yearMax) return null; // No year filter

  const min = parseInt(yearMin);
  const max = parseInt(yearMax);

  if (isNaN(min) || isNaN(max)) return null;
  if (min < 1 || max > 9999 || min > max) return null;

  return [min, max];
}

function validateKeywords(keywords: any): string[] {
  if (!keywords) return [];
  return String(keywords)
    .split(',')
    .map(k => k.trim().toLowerCase())
    .filter(k => k.length > 0 && k.length < 100) // Reasonable keyword length
    .slice(0, 20); // Limit to 20 keywords max
}

function validateMaxHops(maxHops: any): number | null {
  if (!maxHops) return null; // No hop filter
  if (maxHops === 'any') return null; // "any" means no limit
  const parsed = parseInt(maxHops);
  if (isNaN(parsed) || parsed < 1 || parsed > 10) return null;
  return parsed;
}

// SQL predicates run before the bounded result window; no archive-sized JS graph.
function relationships(req: express.Request, res: express.Response, actor?: string) {
  try {
    const params: any[] = [];
    const predicates = ['1=1'];
    const joins = `FROM rdf_triples rt
      LEFT JOIN entity_aliases ea_actor ON ea_actor.original_name=rt.actor
      LEFT JOIN entity_aliases ea_target ON ea_target.original_name=rt.target
      LEFT JOIN documents d ON d.doc_id=rt.doc_id`;
    if (actor) {
      if (actor.length > 300) return res.status(400).json({error: 'Actor name too long'});
      predicates.push('(COALESCE(ea_actor.canonical_name,rt.actor)=? OR COALESCE(ea_target.canonical_name,rt.target)=?)');
      params.push(actor,actor);
    }
    const baseWhere=predicates.join(' AND '), baseParams=[...params];
    const categories=validateCategories(req.query.categories);
    if (categories.length) {predicates.push(`d.category IN (${categories.map(()=>'?').join(',')})`);params.push(...categories);}
    const years=validateYearRange(req.query.yearMin,req.query.yearMax);
    if (years) {
      predicates.push(`(rt.timestamp BETWEEN ? AND ? ${req.query.includeUndated !== 'false' ? 'OR rt.timestamp IS NULL' : ''})`);
      params.push(`${String(years[0]).padStart(4,'0')}-01-01`,`${String(years[1]).padStart(4,'0')}-12-31`);
    } else if (req.query.includeUndated === 'false') predicates.push('rt.timestamp IS NOT NULL');
    const clusters=validateClusterIds(req.query.clusters);
    if (clusters.length) {
      const normalized=!!db.prepare("SELECT 1 FROM sqlite_master WHERE name='triple_clusters'").get();
      predicates.push(normalized ? `EXISTS (SELECT 1 FROM triple_clusters tc WHERE tc.triple_id=rt.id AND tc.cluster_id IN (${clusters.map(()=>'?').join(',')}))` :
        `EXISTS (SELECT 1 FROM json_each(COALESCE(rt.top_cluster_ids,'[]')) tc WHERE tc.value IN (${clusters.map(()=>'?').join(',')}))`);
      params.push(...clusters);
    }
    const keywords=validateKeywords(req.query.keywords);
    if (keywords.length) {
      predicates.push('('+keywords.map(()=>"instr(lower(rt.actor||' '||rt.action||' '||rt.target||' '||COALESCE(rt.location,'')),?)>0").join(' OR ')+')');
      params.push(...keywords);
    }
    const hops=validateMaxHops(req.query.maxHops);
    if (hops!==null) {
      for (const role of ['actor','target']) {
        predicates.push(`EXISTS (SELECT 1 FROM canonical_entities ce WHERE ce.canonical_name=COALESCE(ea_${role}.canonical_name,rt.${role}) AND ce.hop_distance_from_principal<=?)`);
        params.push(hops);
      }
    }
    const where=predicates.join(' AND ');
    const total=(db.prepare(`SELECT COUNT(*) AS n ${joins} WHERE ${where}`).get(...params) as {n:number}).n;
    const all=(db.prepare(`SELECT COUNT(*) AS n ${joins} WHERE ${baseWhere}`).get(...baseParams) as {n:number}).n;
    const limit=validateLimit(req.query.limit);
    const offset=Math.max(0,Math.min(100000000,Number(req.query.offset)||0));
    const rows=db.prepare(`SELECT rt.id,rt.doc_id,rt.timestamp,COALESCE(ea_actor.canonical_name,rt.actor) AS actor,
      rt.action,COALESCE(ea_target.canonical_name,rt.target) AS target,rt.location,rt.triple_tags,rt.top_cluster_ids
      ${joins} WHERE ${where} ORDER BY rt.timestamp,rt.id LIMIT ? OFFSET ?`).all(...params,limit,offset) as any[];
    res.json({relationships:rows.map(({triple_tags,...r})=>({...r,tags:JSON.parse(triple_tags||'[]')})),
      totalBeforeLimit:total,totalBeforeFilter:all,hasMore:offset+rows.length<total,limit,offset});
  } catch (error) {console.error('Relationship query failed',error);res.status(500).json({error:'Relationship query failed'});}
}
app.get('/api/relationships',(req,res)=>relationships(req,res));
app.get('/api/actor/:name/relationships',(req,res)=>relationships(req,res,req.params.name));

// Get statistics with alias resolution
app.get('/api/stats', (req, res) => {
  try {
    const stats = {
      totalDocuments: db.prepare('SELECT COUNT(*) as count FROM documents').get(),
      totalTriples: db.prepare('SELECT COUNT(*) as count FROM rdf_triples').get(),
      totalActors: db.prepare(`
        SELECT COUNT(DISTINCT COALESCE(ea.canonical_name, rt.actor)) as count
        FROM rdf_triples rt
        LEFT JOIN entity_aliases ea ON rt.actor = ea.original_name
      `).get(),
      yearBounds: db.prepare("SELECT MIN(CAST(substr(timestamp,1,4) AS INTEGER)) AS min,MAX(CAST(substr(timestamp,1,4) AS INTEGER)) AS max FROM rdf_triples WHERE timestamp IS NOT NULL").get(),
      categories: db.prepare(`
        SELECT category, COUNT(*) as count
        FROM documents
        GROUP BY category
        ORDER BY count DESC
      `).all(),
    };
    res.json(stats);
  } catch (error) {
    console.error('Error in /api/stats:', error);
    res.status(500).json({ error: 'An internal error occurred' });
  }
});

// Search actors with alias resolution
app.get('/api/search', (req, res) => {
  try {
    const query = req.query.q as string;
    if (!query) {
      return res.json([]);
    }

    const results = db.prepare(`
      SELECT DISTINCT
        COALESCE(ea.canonical_name, rt.actor) as name,
        COUNT(*) as connection_count
      FROM rdf_triples rt
      LEFT JOIN entity_aliases ea ON rt.actor = ea.original_name
      WHERE COALESCE(ea.canonical_name, rt.actor) LIKE ?
      GROUP BY COALESCE(ea.canonical_name, rt.actor)
      ORDER BY connection_count DESC
      LIMIT 20
    `).all(`%${query}%`);

    res.json(results);
  } catch (error) {
    console.error('Error in /api/search:', error);
    res.status(500).json({ error: 'An internal error occurred' });
  }
});

// Aggregate inside SQLite; return only the requested bounded actor window.
app.get('/api/actor-counts',(req,res)=>{
  try {
    const rows=db.prepare(`SELECT name,COUNT(*) AS n FROM (
      SELECT COALESCE(ea.canonical_name,rt.actor) AS name FROM rdf_triples rt LEFT JOIN entity_aliases ea ON ea.original_name=rt.actor
      UNION ALL SELECT COALESCE(ea.canonical_name,rt.target) FROM rdf_triples rt LEFT JOIN entity_aliases ea ON ea.original_name=rt.target
    ) GROUP BY name ORDER BY n DESC,name LIMIT ?`).all(validateLimit(req.query.limit)) as {name:string,n:number}[];
    res.json(Object.fromEntries(rows.map(r=>[r.name,r.n])));
  } catch {res.status(500).json({error:'Actor counts unavailable'});}
});

// Get total relationship count for a specific actor (unfiltered total)
app.get('/api/actor/:name/count', (req, res) => {
  try {
    const name = req.params.name;

    // Count total relationships for this actor WITHOUT filters
    const result = db.prepare(`
      SELECT COUNT(*) as count
      FROM rdf_triples rt
      LEFT JOIN entity_aliases ea_actor ON rt.actor = ea_actor.original_name
      LEFT JOIN entity_aliases ea_target ON rt.target = ea_target.original_name
      WHERE (COALESCE(ea_actor.canonical_name, rt.actor) = ? OR COALESCE(ea_target.canonical_name, rt.target) = ?)
      
    `).get(name, name) as { count: number };

    res.json({ count: result.count });
  } catch (error) {
    console.error('Error in /api/actor/:name/count:', error);
    res.status(500).json({ error: 'An internal error occurred' });
  }
});

// Get document by doc_id
app.get('/api/document/:docId', (req, res) => {
  try {
    const { docId } = req.params;
    const doc = db.prepare(`
      SELECT
        doc_id,
        file_path,
        one_sentence_summary,
        paragraph_summary,
        category,
        date_range_earliest,
        date_range_latest
      FROM documents
      WHERE doc_id = ?
    `).get(docId);

    if (!doc) {
      return res.status(404).json({ error: 'Document not found' });
    }

    res.json(doc);
  } catch (error) {
    console.error('Error in /api/document/:docId:', error);
    res.status(500).json({ error: 'An internal error occurred' });
  }
});

// Get document text from database
app.get('/api/document/:docId/text', (req, res) => {
  try {
    const { docId } = req.params;

    // Validate docId
    if (!docId || docId.length > 100 || /[<>:"|?*]/.test(docId)) {
      return res.status(400).json({ error: 'Invalid document ID' });
    }

    const doc = db.prepare('SELECT substr(full_text,1,200000) AS full_text FROM documents WHERE doc_id = ?').get(docId) as { full_text: string | null } | undefined;

    if (!doc) {
      return res.status(404).json({ error: 'Document not found' });
    }

    if (!doc.full_text) {
      return res.status(404).json({ error: 'Document text not available' });
    }

    res.json({ text: doc.full_text, preview: true, pagesUrl: `/api/document/${encodeURIComponent(docId)}/pages` });
  } catch (error) {
    console.error('Error in /api/document/:docId/text:', error);
    res.status(500).json({ error: 'An internal error occurred' });
  }
});

// Additive catalogue endpoints include documents with no graph relationships.
const hasTable=(name:string)=>!!db.prepare("SELECT 1 FROM sqlite_master WHERE name=?").get(name);
app.get('/api/documents',(req,res)=>{
  try {
    const q=String(req.query.q||'').trim().slice(0,200);
    const after=String(req.query.after||'').slice(0,100);
    const limit=Math.min(100,validateLimit(req.query.limit));
    const fts=q && hasTable('documents_fts');
    const where=fts ? "AND d.id IN (SELECT rowid FROM documents_fts WHERE documents_fts MATCH ?)" : q ? "AND instr(lower(COALESCE(d.full_text,'')),lower(?))>0" : '';
    const query=fts ? '"'+q.replaceAll('"','""')+'"' : q;
    const review=hasTable('ingestion_documents');
    const rows=db.prepare(`SELECT d.doc_id,d.one_sentence_summary,d.category,
      ${review ? "COALESCE(i.review_status,'upstream')" : "'upstream'"} AS review_status
      FROM documents d ${review ? 'LEFT JOIN ingestion_documents i ON i.doc_id=d.doc_id' : ''}
      WHERE d.doc_id>? ${where} ORDER BY d.doc_id LIMIT ?`).all(...(q?[after,query,limit+1]:[after,limit+1])) as any[];
    const more=rows.length>limit;rows.splice(limit);
    res.json({documents:rows,next:more?rows[rows.length-1].doc_id:null});
  } catch {res.status(500).json({error:'Catalogue unavailable'});}
});
app.get('/api/document/:docId/pages',(req,res)=>{
  try {
    if (!hasTable('ingestion_pages')) return res.json({pages:[],next:null});
    const after=Math.max(0,Number(req.query.after)||0);
    const limit=Math.min(10,validateLimit(req.query.limit));
    const rows=db.prepare('SELECT page,substr(text,1,200000) AS text,length(text) AS characters,quality,method,provenance FROM ingestion_pages WHERE doc_id=? AND page>? ORDER BY page LIMIT ?')
      .all(req.params.docId,after,limit+1) as any[];
    const more=rows.length>limit;rows.splice(limit);
    res.json({pages:rows.map(r=>({...r,truncated:r.characters>200000,provenance:JSON.parse(r.provenance)})),next:more?rows[rows.length-1].page:null});
  } catch {res.status(500).json({error:'Pages unavailable'});}
});
app.get('/api/document/:docId/evidence',(req,res)=>{
  try {
    if (!hasTable('ingestion_evidence')) return res.json({evidence:[],next:null});
    const after=Math.max(0,Number(req.query.after)||0);
    const rows=db.prepare(`SELECT e.*,rt.actor,rt.action,rt.target FROM ingestion_evidence e
      JOIN rdf_triples rt ON rt.id=e.triple_id WHERE rt.doc_id=? AND e.triple_id>? ORDER BY e.triple_id LIMIT 101`)
      .all(req.params.docId,after) as any[];
    const more=rows.length>100;rows.splice(100);
    res.json({evidence:rows,next:more?rows[rows.length-1].triple_id:null});
  } catch {res.status(500).json({error:'Evidence unavailable'});}
});

// Get tag clusters
app.get('/api/tag-clusters', (req, res) => {
  try {
    // Return just the cluster metadata (id, name, exemplars) without all tags
    const clusters = tagClusters.map((cluster: any) => ({
      id: cluster.id,
      name: cluster.name,
      exemplars: cluster.exemplars,
      tagCount: cluster.tagCount ?? cluster.tags.length
    }));
    res.json(clusters);
  } catch (error) {
    console.error('Error in /api/tag-clusters:', error);
    res.status(500).json({ error: 'An internal error occurred' });
  }
});

// Health check endpoint
app.get('/health', (req, res) => {
  res.json({
    status: 'ok',
    uptime: process.uptime(),
    timestamp: new Date().toISOString()
  });
});

// Serve static frontend files
const frontendPath = path.join(process.cwd(), 'network-ui', 'dist');
if (fs.existsSync(frontendPath)) {
  app.use(express.static(frontendPath));

  // Serve index.html for all non-API routes (SPA support)
  app.use((req, res, next) => {
    // Skip API routes
    if (req.path.startsWith('/api') || req.path.startsWith('/health')) {
      return next();
    }
    // Serve index.html for all other routes (client-side routing)
    res.sendFile(path.join(frontendPath, 'index.html'));
  });

  console.log(`✓ Serving frontend from ${frontendPath}`);
} else {
  console.log(`⚠ Frontend build not found at ${frontendPath}`);
}

const server = app.listen(PORT, process.env.HOST || '0.0.0.0', () => {
  console.log(`\n🚀 API Server running at http://localhost:${PORT}`);
  console.log(`📊 Network UI will connect to this server\n`);
});

// Graceful shutdown
const gracefulShutdown = (signal: string) => {
  console.log(`\n${signal} received, closing server gracefully...`);
  server.close(() => {
    console.log('HTTP server closed');
    try {
      db.close();
      console.log('Database connection closed');
    } catch (error) {
      console.error('Error closing database:', error);
    }
    process.exit(0);
  });

  // Force shutdown after 10 seconds
  setTimeout(() => {
    console.error('Forced shutdown after timeout');
    process.exit(1);
  }, 10000);
};

process.on('SIGTERM', () => gracefulShutdown('SIGTERM'));
process.on('SIGINT', () => gracefulShutdown('SIGINT'));
