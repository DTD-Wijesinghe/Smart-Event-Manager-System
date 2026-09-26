import { readFile, stat } from 'node:fs/promises';
import { extname, join, normalize } from 'node:path';
import { config, storageMode } from './config.mjs';
import { createSession, createTranscript, dashboard, list } from './repository.mjs';
import { summarizeEventText, transcribeAudio } from './gemini.mjs';

const contentTypes = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.json': 'application/json', '.png': 'image/png' };

export function sendJson(res, payload, status = 200) {
  res.writeHead(status, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' });
  res.end(JSON.stringify(payload));
}

async function parseJson(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  return JSON.parse(Buffer.concat(chunks).toString() || '{}');
}

async function serveStatic(urlPath, res) {
  const requested = urlPath === '/' ? 'index.html' : urlPath.replace(/^\//, '');
  const file = normalize(join(config.publicDir, requested));
  if (!file.startsWith(config.publicDir)) return sendJson(res, { error: 'Not found' }, 404);
  try {
    const info = await stat(file);
    if (!info.isFile()) throw new Error('not a file');
    res.writeHead(200, { 'content-type': `${contentTypes[extname(file)] || 'application/octet-stream'}; charset=utf-8` });
    res.end(await readFile(file));
  } catch { sendJson(res, { error: 'Not found' }, 404); }
}

export async function handleRequest(req, res) {
  const url = new URL(req.url, `http://${req.headers.host}`);
  try {
    if (url.pathname === '/api/health') return sendJson(res, { ok: true, mode: storageMode, geminiConfigured: Boolean(config.geminiApiKey), time: new Date().toISOString() });
    if (url.pathname === '/api/dashboard') return sendJson(res, await dashboard());
    if (url.pathname === '/api/sessions' && req.method === 'GET') return sendJson(res, await list('sessions'));
    if (url.pathname === '/api/sessions' && req.method === 'POST') return sendJson(res, await createSession(await parseJson(req)), 201);
    if (url.pathname === '/api/attendees') return sendJson(res, await list('attendees'));
    if (url.pathname === '/api/insights') return sendJson(res, await list('insights'));
    if (url.pathname === '/api/share-links') return sendJson(res, await list('share_links'));
    if (url.pathname === '/api/transcripts' && req.method === 'GET') return sendJson(res, await list('transcripts'));
    if (url.pathname === '/api/ai/summarize' && req.method === 'POST') {
      const body = await parseJson(req);
      if (!body.text?.trim()) return sendJson(res, { error: 'Transcript text is required' }, 400);
      return sendJson(res, await summarizeEventText(body.text, body.targetLanguage || 'English'));
    }
    if (url.pathname === '/api/transcription/batch' && req.method === 'POST') {
      const body = await parseJson(req);
      const result = await transcribeAudio(body);
      const transcript = await createTranscript({ text: result.transcript, model: result.model, language: body.languageCodes?.[0] || 'auto', session_id: body.session_id });
      return sendJson(res, { ...result, transcript });
    }
    return serveStatic(url.pathname, res);
  } catch (error) {
    console.error(`[api] ${req.method} ${url.pathname}`, error);
    return sendJson(res, { error: error.message || 'Request failed' }, error.status || 500);
  }
}
