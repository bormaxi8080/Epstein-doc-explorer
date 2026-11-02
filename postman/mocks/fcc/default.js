const http = require('http');
let requests = 0;
const modelRequests = {};
http.createServer(async (req, res) => {
  res.setHeader('Content-Type', 'application/json');
  if (req.method === 'GET' && req.url === '/calls') {
    res.end(JSON.stringify({ requests, modelRequests }));
    return;
  }
  if (req.method !== 'POST' || req.url !== '/v1/messages') {
    res.statusCode = 501;
    res.end(JSON.stringify({ error: 'Endpoint outside this simulation contract' }));
    return;
  }
  requests += 1;
  const data = [];
  for await (const part of req) data.push(part);
  let body;
  try {
    body = req.body || JSON.parse(Buffer.concat(data).toString());
  } catch {
    res.statusCode = 400;
    res.end(JSON.stringify({ error: 'Invalid JSON' }));
    return;
  }
  if (!body.model || body.stream !== false || body.max_tokens < 1 ||
      body.messages?.[0]?.role !== 'user' || body.tools) {
    res.statusCode = 400;
    res.end(JSON.stringify({ error: 'Invalid Messages request' }));
    return;
  }
  modelRequests[body.model] = (modelRequests[body.model] || 0) + 1;
  if (body.model === 'failure-429' && modelRequests[body.model] % 2 === 1) {
    res.statusCode = 429;
    res.setHeader('Retry-After', '0');
    res.end(JSON.stringify({ error: 'Synthetic quota limit' }));
    return;
  }
  if (body.model === 'failure-500') {
    res.statusCode = 500;
    res.end(JSON.stringify({ error: 'Synthetic upstream failure' }));
    return;
  }
  if (body.model === 'failure-timeout') {
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  let fragment;
  try {
    fragment = JSON.parse(body.messages[0].content.split('\nDOCUMENT FRAGMENT:\n')[1]);
  } catch {
    res.statusCode = 400;
    res.end(JSON.stringify({ error: 'Missing document fragment' }));
    return;
  }
  const analysis = {
    one_sentence_summary: 'Synthetic email fixture.',
    paragraph_summary: 'The synthetic fixture records an email.',
    category: 'email', content_tags: ['correspondence'],
    date_range_earliest: null, date_range_latest: null,
    rdf_triples: [{
      actor: 'Jeffrey Epstein', action: 'sent email to', target: 'Person B',
      timestamp: null, location: null, tags: ['correspondence'],
      explicit_topic: 'synthetic correspondence', assertion_type: 'document_statement',
      evidence: { page: fragment.page, quote: body.model === 'failure-evidence' ? 'Invented quote' : fragment.text }
    }]
  };
  res.end(JSON.stringify({
    id: 'msg_synthetic', type: 'message', role: 'assistant', model: body.model,
    stop_reason: body.model === 'failure-truncated' ? 'max_tokens' : 'end_turn',
    content: [{ type: 'text', text: JSON.stringify(analysis) }],
    usage: { input_tokens: 25, output_tokens: 50 }
  }));
}).listen(process.env.PORT || 4010);
