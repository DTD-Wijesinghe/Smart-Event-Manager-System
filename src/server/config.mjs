import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const serverDir = dirname(fileURLToPath(import.meta.url));

export const config = {
  port: Number(process.env.PORT || 4174),
  rootDir: join(serverDir, '..', '..'),
  publicDir: join(serverDir, '..', '..', 'public'),
  supabaseUrl: process.env.SUPABASE_URL || '',
  supabaseKey: process.env.SUPABASE_SERVICE_ROLE_KEY || process.env.SUPABASE_ANON_KEY || ''
};

export const storageMode = config.supabaseUrl && config.supabaseKey ? 'supabase' : 'demo';
