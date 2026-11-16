import { useEffect, useState } from 'react';
import { API_BASE as API } from '../api';
async function read(url:string,signal?:AbortSignal) {
  const r=await fetch(API+url,{signal});if(!r.ok)throw new Error('Document request failed');return r.json();
}
type Doc={doc_id:string;one_sentence_summary:string;category:string;review_status:string};
type Page={page:number;text:string;quality:string;method:string;truncated:boolean};
type Evidence={triple_id:number;page:number;quote:string;assertion_type:string};
export function DocumentCatalogue({onClose}:{onClose:()=>void}) {
  const [q,setQ]=useState(''),[submitted,setSubmitted]=useState(''),[after,setAfter]=useState('');
  const [docs,setDocs]=useState<Doc[]>([]),[next,setNext]=useState<string|null>(null);
  const [selected,setSelected]=useState<Doc|null>(null),[pageAfter,setPageAfter]=useState(0);
  const [pages,setPages]=useState<Page[]>([]),[pageNext,setPageNext]=useState<number|null>(null);
  const [evidence,setEvidence]=useState<Evidence[]>([]),[evidenceAfter,setEvidenceAfter]=useState(0),[evidenceNext,setEvidenceNext]=useState<number|null>(null);
  const [error,setError]=useState(''),[loading,setLoading]=useState(false);
  useEffect(()=>{const c=new AbortController();setLoading(true);setError('');
    read(`/documents?q=${encodeURIComponent(submitted)}&after=${encodeURIComponent(after)}&limit=25`,c.signal)
      .then(r=>{setDocs(r.documents);setNext(r.next);}).catch(e=>{if(e.name!=='AbortError')setError(e.message);}).finally(()=>{if(!c.signal.aborted)setLoading(false);});return()=>c.abort();
  },[submitted,after]);
  useEffect(()=>{if(!selected)return;const c=new AbortController();setPages([]);setError('');
    read(`/document/${encodeURIComponent(selected.doc_id)}/pages?after=${pageAfter}&limit=1`,c.signal)
      .then(async r=>{setPageNext(r.next);if(r.pages.length)setPages(r.pages);else if(pageAfter===0){const t=await read(`/document/${encodeURIComponent(selected.doc_id)}/text`,c.signal);setPages([{page:1,text:t.text,quality:'upstream',method:'legacy text preview',truncated:true}]);}})
      .catch(e=>{if(e.name!=='AbortError')setError(e.message);});return()=>c.abort();
  },[selected,pageAfter]);
  useEffect(()=>{if(!selected)return;const c=new AbortController();setEvidence([]);
    read(`/document/${encodeURIComponent(selected.doc_id)}/evidence?after=${evidenceAfter}`,c.signal)
      .then(r=>{setEvidence(r.evidence);setEvidenceNext(r.next);}).catch(e=>{if(e.name!=='AbortError')setError(e.message);});return()=>c.abort();
  },[selected,evidenceAfter]);
  return <div className="fixed inset-0 z-50 bg-gray-900 text-white overflow-auto p-4" role="dialog" aria-modal="true" aria-label="Document catalogue">
    <button className="float-right p-2 bg-gray-700 rounded" onClick={onClose}>Close catalogue</button><h1 className="text-2xl mb-4">Document catalogue</h1>
    <p className="mb-4">Includes documents without graph relationships. OCR and analysis can require review.</p>
    <form onSubmit={e=>{e.preventDefault();setAfter('');setSubmitted(q);}}><input className="bg-gray-700 p-2" aria-label="Search document text" value={q} onChange={e=>setQ(e.target.value)}/><button className="p-2" type="submit">Search text</button></form>
    {error&&<p role="alert">{error}</p>}{loading&&<p>Loading…</p>}
    <div className="grid md:grid-cols-2 gap-6 mt-4"><section><h2>Documents</h2>
      {docs.map(d=><button key={d.doc_id} className="block text-left border-b border-gray-700 p-3 w-full" onClick={()=>{setSelected(d);setPageAfter(0);setEvidenceAfter(0);}}>
        <strong>{d.doc_id}</strong><p>{d.one_sentence_summary}</p><small>{d.category} · Review: {d.review_status}</small></button>)}
      {next&&<button className="p-3" onClick={()=>setAfter(next)}>Next documents</button>}
      {after&&<button className="p-3" onClick={()=>setAfter('')}>First documents</button>}
    </section><section>{selected&&<><h2 className="break-all">{selected.doc_id}</h2><p>Review: {selected.review_status}</p>
      {pages.map(p=><article key={p.page}><h3>Page {p.page} · {p.method} · {p.quality}</h3>{p.truncated&&<p>Text preview truncated; use the canonical TXT for the full page.</p>}<pre className="whitespace-pre-wrap break-words my-4">{p.text}</pre></article>)}
      {pageNext!==null&&<button className="p-3" onClick={()=>setPageAfter(pageNext)}>Next page</button>}
      {pageAfter>0&&<button className="p-3" onClick={()=>setPageAfter(0)}>First page</button>}
      <h3>Evidence</h3>{evidence.map(e=><blockquote key={e.triple_id} className="border-l pl-3 my-3"><button onClick={()=>setPageAfter(e.page-1)}>Page {e.page}</button> · {e.assertion_type}<p>{e.quote}</p></blockquote>)}
      {evidenceNext!==null&&<button onClick={()=>setEvidenceAfter(evidenceNext)}>More evidence</button>}
    </>}</section></div></div>;
}
