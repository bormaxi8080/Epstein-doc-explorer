#!/usr/bin/env node
/** Optional native Chrome acceptance. Generates synthetic data and uses loopback only. */
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import net from 'node:net';
import { spawn, execFileSync } from 'node:child_process';
import { once } from 'node:events';
import { fileURLToPath } from 'node:url';
const checkout=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const options=Object.fromEntries(Array.from({length:Math.floor((process.argv.length-2)/2)},(_,i)=>[process.argv[2+i*2],process.argv[3+i*2]]));
if(!options['--scratch']||!options['--chrome'])throw new Error('Use --scratch EXISTING_EXTERNAL_DIRECTORY --chrome CHROME_BINARY [--python PYTHON]');
const scratch=fs.realpathSync(options['--scratch']);
if(scratch===checkout||scratch.startsWith(checkout+path.sep))throw new Error('Browser artifacts need an external root');
const root=fs.mkdtempSync(path.join(scratch,'browser-'));
const python=options['--python']||path.join(checkout,'.venv/bin/python');
execFileSync(python,['-c', `from pathlib import Path
import sys
sys.path.insert(0,'ingestion')
from explorer_ingest.distribution_demo import demo_bundle
from explorer_ingest.synthetic_archive import generate_inventory
from explorer_ingest.inventory_catalog import build_catalog
from explorer_ingest.state import digest_file
import json,sqlite3
r=Path(sys.argv[1]);demo_bundle(r/'viewer',0)
db=sqlite3.connect(r/'viewer/document_analysis.db')
db.execute("INSERT INTO documents(doc_id,file_path,one_sentence_summary,paragraph_summary,category,content_tags,analysis_timestamp) VALUES ('missing-text','missing.txt','SYNTHETIC missing text record','No text included','other','[]','synthetic')")
db.commit();db.close()
meta=json.loads((r/'viewer/manifest.json').read_text());meta['database_sha256']=digest_file(r/'viewer/document_analysis.db')
(r/'viewer/manifest.json').write_text(json.dumps(meta))
generate_inventory(r/'inventory.json',3)
build_catalog(r/'inventory.json',r/'viewer/inventory.sqlite3',reserve_bytes=0)
`,root],{cwd:checkout,stdio:'ignore'});
const port=await new Promise((resolve,reject)=>{const s=net.createServer();s.on('error',reject);s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>resolve(p));});});
const log=fs.openSync(path.join(root,'browser.log'),'w');
const api=spawn(path.join(checkout,'node_modules/.bin/tsx'),['api_server.ts'],{cwd:checkout,env:{...process.env,HOST:'127.0.0.1',PORT:String(port),DB_PATH:path.join(root,'viewer/document_analysis.db'),TAG_CLUSTERS_PATH:path.join(root,'viewer/tag_clusters.json'),INVENTORY_DB_PATH:path.join(root,'viewer/inventory.sqlite3')},stdio:['ignore',log,log]});
let chrome,ws;
const delay=ms=>new Promise(r=>setTimeout(r,ms));
async function poll(run){for(let i=0;i<150;i++){try{const v=await run();if(v)return v;}catch{}await delay(100);}throw new Error('Browser acceptance timed out; inspect external logs');}
try {
  await poll(async()=>{if(api.exitCode!==null)throw new Error('API exited');return (await fetch(`http://127.0.0.1:${port}/health`)).ok;});
  const profile=path.join(root,'chrome-profile');fs.mkdirSync(profile);
  chrome=spawn(options['--chrome'],['--headless=new','--disable-gpu','--disable-gpu-vsync','--no-first-run','--no-default-browser-check','--disable-background-networking','--disable-extensions','--disable-sync','--disable-component-update','--remote-debugging-port=0',`--user-data-dir=${profile}`,'--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1','about:blank'],{env:{...process.env,TMPDIR:root},stdio:['ignore',log,log]});
  const debugFile=path.join(profile,'DevToolsActivePort');
  const debugPort=await poll(()=>{if(!fs.existsSync(debugFile))return null;const lines=fs.readFileSync(debugFile,'utf8').split('\n');const p=Number(lines[0]);return Number.isInteger(p)&&p>0&&p<65536&&lines[1]?.startsWith('/devtools/browser/')?p:null;});
  await poll(async()=> (await fetch(`http://127.0.0.1:${debugPort}/json/version`)).ok);
  const target=await (await fetch(`http://127.0.0.1:${debugPort}/json/new?about:blank`,{method:'PUT'})).json();
  ws=new WebSocket(target.webSocketDebuggerUrl);await once(ws,'open');
  let id=0;const pending=new Map();
  ws.addEventListener('message',event=>{const value=JSON.parse(event.data);const request=pending.get(value.id);if(request){pending.delete(value.id);value.error?request.reject(new Error(value.error.message)):request.resolve(value.result);}});
  async function call(method,params={}){fs.writeSync(log,'CDP '+method+'\n');const requestId=++id;return new Promise((resolve,reject)=>{const timer=setTimeout(()=>{pending.delete(requestId);reject(new Error('CDP timeout: '+method));},30000);pending.set(requestId,{resolve:v=>{clearTimeout(timer);resolve(v);},reject:e=>{clearTimeout(timer);reject(e);}});ws.send(JSON.stringify({id:requestId,method,params}));});}
  const evaluate=async expression=>{const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw new Error('Browser assertion failed');return r.result.value;};
  const escaped=execFileSync(process.execPath,['--import','tsx','--input-type=module','-e',"import {escapeHtml} from './network-ui/src/html.ts';process.stdout.write(escapeHtml('<img src=x onerror=alert(1)> & \"quoted\"'))"],{cwd:checkout,encoding:'utf8'});
  await call('Page.enable');
  await call('Page.addScriptToEvaluateOnNewDocument',{source:"localStorage.setItem('hasSeenWelcome','true')"});
  await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  await call('Page.navigate',{url:`http://127.0.0.1:${port}`});
  await poll(()=>evaluate("document.querySelector('[aria-label=\"Dataset coverage\"]')?.textContent.includes('SYNTHETIC')"));
  if(!await evaluate(`(()=>{const d=document.createElement('div');d.innerHTML=${JSON.stringify(escaped)};return !d.querySelector('img')&&d.textContent.startsWith('<img');})()`))throw new Error('Untrusted tooltip markup was not escaped');
  await evaluate("[...document.querySelectorAll('button')].find(b=>b.textContent==='Archive').click()");
  await poll(()=>evaluate("document.querySelector('[aria-label=\"Archive entries\"]')?.textContent.includes('Folder: Synthetic')"));
  await evaluate("[...document.querySelectorAll('button')].find(b=>b.textContent==='Folder: Synthetic').click()");
  await poll(()=>evaluate("document.querySelector('[aria-label=\"Archive entries\"]')?.textContent.includes('Folder: Group 0')"));
  await evaluate("[...document.querySelectorAll('button')].find(b=>b.textContent==='Folder: Group 0').click()");
  await poll(()=>evaluate("document.querySelector('[aria-label=\"Archive entries\"]')?.textContent.includes('File: Document 0.txt')"));
  await evaluate("[...document.querySelectorAll('button')].find(b=>b.textContent==='File: Document 0.txt').click()");
  await poll(()=>evaluate("document.querySelector('[aria-label=\"Source file details\"]')?.textContent.includes('No matching text')"));
  await evaluate("document.querySelector('[aria-label=\"Search archive filenames\"]').focus()");
  await call('Input.insertText',{text:'Document 2'});
  await evaluate("document.querySelector('[aria-label=\"Search archive filenames\"]').form.requestSubmit()");
  await poll(()=>evaluate("document.querySelector('[aria-label=\"Archive entries\"]')?.textContent.includes('Document 2.txt ·')"));
  for(const [name,width,height,mobile] of [['desktop',1440,1000,false],['mobile',390,844,true]]){
    await call('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile});
    await delay(200);
    const shot=await call('Page.captureScreenshot',{format:'png'});
    fs.writeFileSync(path.join(root,name+'.png'),Buffer.from(shot.data,'base64'));
  }
  await evaluate("[...document.querySelectorAll('button')].find(b=>b.textContent==='Close archive').click()");
  await evaluate("[...document.querySelectorAll('button')].find(b=>b.textContent==='Documents').click()");
  await poll(()=>evaluate("[...document.querySelectorAll('button')].some(b=>b.textContent.includes('SYNTHETIC missing text record'))"));
  await evaluate("[...document.querySelectorAll('button')].find(b=>b.textContent.includes('SYNTHETIC missing text record')).click()");
  await poll(()=>evaluate("document.querySelector('[role=alert]')?.textContent.includes('Source text is not included')"));
  fs.writeFileSync(path.join(root,'verification.json'),JSON.stringify({passed:true,synthetic:true,folder_browsing:true,asset_details:true,filename_search:true,missing_text_explicit:true,desktop_and_mobile:true,source_downloads:0,platform:os.platform()},null,2)+'\n');
  console.log(JSON.stringify({passed:true,output:root}));
  await call('Browser.close').catch(()=>{});
} finally {
  ws?.close();
  if(chrome?.exitCode===null)chrome.kill('SIGTERM');
  if(api.exitCode===null){api.kill('SIGTERM');await Promise.race([once(api,'exit'),delay(12000)]);if(api.exitCode===null)api.kill('SIGKILL');}
  fs.closeSync(log);
}
