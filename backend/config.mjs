import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const serverDir = dirname(fileURLToPath(import.meta.url));

export const config = {
  port: Number(process.env.PORT || 4174),
  rootDir: join(serverDir, '..'),
  publicDir: join(serverDir, '..', 'frontend'),
  supabaseUrl: process.env.SUPABASE_URL || '',
  supabaseKey: process.env.SUPABASE_SERVICE_ROLE_KEY || process.env.SUPABASE_ANON_KEY || '',
  geminiApiKey: process.env.GEMINI_API_KEY || '',
  geminiTextModel: process.env.GEMINI_TEXT_MODEL || 'gemini-2.5-flash',
  geminiBatchModel: process.env.GEMINI_BATCH_MODEL || 'gemini-3.5-transcribe',
  geminiLiveModel: process.env.GEMINI_LIVE_MODEL || 'gemini-3.5-transcribe-live'
};

export const storageMode = config.supabaseUrl && config.supabaseKey ? 'supabase' : 'demo';
