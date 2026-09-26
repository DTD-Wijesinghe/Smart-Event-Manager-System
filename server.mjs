import http from 'node:http';
import { config } from './src/server/config.mjs';
import { handleRequest } from './src/server/http.mjs';

const server = http.createServer((req, res) => {
  res.setHeader('x-powered-by', 'Smart Event Manager');
  handleRequest(req, res);
});

server.listen(config.port, '127.0.0.1', () => {
  console.log(`Smart Event Manager running at http://127.0.0.1:${config.port}`);
});
