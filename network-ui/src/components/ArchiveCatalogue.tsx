import { useEffect, useState } from 'react';
import { API_BASE as API } from '../api';
import type { CatalogueDocument } from './DocumentCatalogue';
type Node = {seq:number; parent:number|null; kind:string; name:string; asset_id:string|null};
type Asset = {id:string;name:string;url:string;kind:string;bytes:number|null};
async function read(url:string,signal?:AbortSignal) { const r=await fetch(API+url,{signal}); if(!r.ok) throw new Error(r.status===503?'Full archive catalogue is not available. Install the catalogue data and restart the Viewer.':'Archive request failed'); return r.json(); }
export function DatasetBadge() {
  const [data,setData]=useState<any>(null);
  useEffect(()=>{const c=new AbortController();read('/dataset',c.signal).then(setData).catch(()=>{});return()=>c.abort();},[]);
  if(!data)return null;
  return <p className="fixed bottom-2 left-2 z-40 max-w-lg bg-gray-900/90 text-white text-xs p-2 rounded" aria-label="Dataset coverage">
    {data.synthetic?'SYNTHETIC DEMO · ':data.private_review?'PRIVATE REVIEW · ':''}
    {data.catalogue?`${data.catalogue.unique_files_count.toLocaleString()} source files · `:''}
    {data.viewer.documents.toLocaleString()} documents with text · {data.viewer.pending_imported.toLocaleString()} pending review.
    <span className="block">Source inventory and processed text have separate coverage.</span>
  </p>;
}
export function ArchiveCatalogue({onClose,onDocument}:{onClose:()=>void;onDocument:(d:CatalogueDocument)=>void}) {
  const [trail,setTrail]=useState<{seq:number;name:string}[]>([]),[after,setAfter]=useState(0),[next,setNext]=useState<number|null>(null);
  const [nodes,setNodes]=useState<Node[]>([]),[assets,setAssets]=useState<Asset[]>([]),[q,setQ]=useState(''),[search,setSearch]=useState('');
  const [detail,setDetail]=useState<any>(null),[error,setError]=useState(''),[loading,setLoading]=useState(false);
  const parent=trail.at(-1)?.seq;
  useEffect(()=>{const c=new AbortController();setLoading(true);setError('');setNodes([]);setAssets([]);
    read(search?`/archive/assets?q=${encodeURIComponent(search)}&after=${after}`:`/archive/children?parent=${parent??''}&after=${after}`,c.signal)
    .then(r=>{setNodes(r.nodes||[]);setAssets(r.assets||[]);setNext(r.next);}).catch(e=>{if(e.name!=='AbortError')setError(e.message);}).finally(()=>{if(!c.signal.aborted)setLoading(false);});return()=>c.abort();
  },[search,parent,after]);
  useEffect(()=>{setDetail(null);},[search,parent]);
  async function open(id:string) {setError('');try{setDetail(await read('/archive/asset/'+encodeURIComponent(id)));}catch(e){setError((e as Error).message);}}
  return <div role="dialog" aria-modal="true" aria-label="Full archive catalogue" className="fixed inset-0 z-50 bg-gray-900 text-white overflow-auto p-4">
    <button className="float-right rounded bg-gray-700 p-2" onClick={onClose}>Close archive</button><h1 className="text-2xl mb-3">Full archive catalogue</h1>
    <p className="mb-3">Browse every source file. Text and analysis become available as verified processing is published.</p>
    <form className="flex gap-2 mb-4" onSubmit={e=>{e.preventDefault();setSearch(q.trim());setAfter(0);}}><input className="bg-gray-700 p-2 min-w-0" aria-label="Search archive filenames" placeholder="Search filenames" value={q} onChange={e=>setQ(e.target.value)}/><button>Search</button><button type="button" onClick={()=>{setSearch('');setQ('');setAfter(0);}}>Browse folders</button></form>
    <nav aria-label="Archive folders"><button onClick={()=>{setTrail([]);setSearch('');setAfter(0);}}>Archive root</button>{trail.map((t,i)=><span key={t.seq}> / <button onClick={()=>{setTrail(trail.slice(0,i+1));setSearch('');setAfter(0);}}>{t.name}</button></span>)}</nav>
    {loading&&<p>Loading…</p>}{error&&<p role="alert" className="text-amber-300">{error}</p>}
    <div className="grid md:grid-cols-2 gap-8 mt-4"><section aria-label="Archive entries">
      {nodes.map(n=><button key={n.seq} className="block w-full text-left p-3 border-b border-gray-700 break-words" onClick={()=>{if(n.asset_id)void open(n.asset_id);else{setTrail([...trail,{seq:n.seq,name:n.name}]);setAfter(0);}}}>{n.asset_id?'File: ':'Folder: '}{n.name}</button>)}
      {assets.map(a=><button key={a.id} className="block w-full text-left p-3 border-b border-gray-700 break-words" onClick={()=>void open(a.id)}>{a.name} · {a.kind} · {a.bytes===null?'size unknown':a.bytes.toLocaleString()+' bytes'}</button>)}
      {next!==null&&<button className="p-3" onClick={()=>setAfter(next)}>Next entries</button>}{after>0&&<button className="p-3" onClick={()=>setAfter(0)}>First entries</button>}
    </section><section aria-label="Source file details">{detail&&<><h2 className="text-xl break-words">{detail.asset.name}</h2><p>{detail.asset.kind} · {detail.asset.bytes?.toLocaleString()??'Unknown size'} bytes</p>
      <a className="underline break-all" href={detail.asset.url} target="_blank" rel="noreferrer">Open original source</a>
      <h3 className="mt-4">Recognized documents in this bundle</h3>{detail.documents.length===0&&<p>No matching text is included in this bundle.</p>}
      {detail.documents.map((d:CatalogueDocument)=><button className="block text-left p-3 border-b border-gray-700" key={d.doc_id} onClick={()=>onDocument(d)}>{d.one_sentence_summary}<span className="block text-sm">Review: {d.review_status}</span></button>)}
      {detail.more_documents&&<p>First 100 documents shown. Use the document catalogue to search the remaining text.</p>}
      <p className="mt-4">Archive placements shown: {detail.placements.length}{detail.next!==null?' (additional locations exist)':''}.</p>
    </>}</section></div>
  </div>;
}
