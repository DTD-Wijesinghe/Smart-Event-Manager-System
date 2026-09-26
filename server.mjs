import http from 'node:http';
import { readFile } from 'node:fs/promises';

try {
  const env = await readFile(new URL('./.env', import.meta.url), 'utf8');
  for (const line of env.split(/\r?\n/)) {
    const match = line.match(/^\s*([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*$/);
    if (match && !process.env[match[1]]) process.env[match[1]] = match[2].replace(/^['"]|['"]$/g, '');
  }
} catch { /* .env is optional; environment variables still work */ }

const { config } = await import('./backend/config.mjs');
const { handleRequest } = await import('./backend/http.mjs');

const server = http.createServer((req, res) => {
  res.setHeader('x-powered-by', 'Smart Event Manager');
  handleRequest(req, res);
});

server.listen(config.port, '127.0.0.1', () => {
  console.log(`Smart Event Manager running at http://127.0.0.1:${config.port}`);
});
