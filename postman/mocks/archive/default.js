const payload = Buffer.from('JVBERi0xLjMKJeLjz9MKMSAwIG9iago8PAovUHJvZHVjZXIgKHB5cGRmKQo+PgplbmRvYmoKMiAwIG9iago8PAovVHlwZSAvUGFnZXMKL0NvdW50IDEKL0tpZHMgWyA0IDAgUiBdCj4+CmVuZG9iagozIDAgb2JqCjw8Ci9UeXBlIC9DYXRhbG9nCi9QYWdlcyAyIDAgUgo+PgplbmRvYmoKNCAwIG9iago8PAovVHlwZSAvUGFnZQovUmVzb3VyY2VzIDw8Cj4+Ci9NZWRpYUJveCBbIDAuMCAwLjAgMTAwIDEwMCBdCi9QYXJlbnQgMiAwIFIKPj4KZW5kb2JqCnhyZWYKMCA1CjAwMDAwMDAwMDAgNjU1MzUgZiAKMDAwMDAwMDAxNSAwMDAwMCBuIAowMDAwMDAwMDU0IDAwMDAwIG4gCjAwMDAwMDAxMTMgMDAwMDAgbiAKMDAwMDAwMDE2MiAwMDAwMCBuIAp0cmFpbGVyCjw8Ci9TaXplIDUKL1Jvb3QgMyAwIFIKL0luZm8gMSAwIFIKPj4Kc3RhcnR4cmVmCjI1NgolJUVPRgo=', "base64");
const http = require('http');
http.createServer((req, res) => {
  if (req.method !== 'GET' || req.url !== '/source/document.pdf') {
    res.writeHead(501, {'Content-Type':'application/json'});
    res.end(JSON.stringify({error:'Outside the synthetic archive contract'}));
    return;
  }
  let start = 0;
  let status = 200;
  if (req.headers.range && req.headers['if-range'] === '"mock-pdf-v1"') {
    const match = /^bytes=(\d+)-$/.exec(req.headers.range);
    if (!match || Number(match[1]) >= payload.length) {
      res.writeHead(416, {'Content-Range':`bytes */${payload.length}`});
      res.end();
      return;
    }
    start = Number(match[1]);
    status = 206;
  }
  const headers = {'Content-Type':'application/pdf', 'ETag':'"mock-pdf-v1"',
    'Content-Length': String(payload.length-start), 'Accept-Ranges':'bytes'};
  if (status === 206) headers['Content-Range'] = `bytes ${start}-${payload.length-1}/${payload.length}`;
  res.writeHead(status,headers);
  res.end(payload.subarray(start));
}).listen(process.env.PORT || 4011);
