import { config } from './config.mjs';

export async function summarizeEventText(text, targetLanguage = 'English') {
  if (!config.geminiApiKey) {
    const error = new Error('GEMINI_API_KEY is not configured on the server');
    error.status = 503;
    throw error;
  }

  const prompt = `You are the content intelligence layer for Smart Event Manager.
Summarize the following event transcript for an event organizer.
Return concise plain text with these sections: Summary, Key signals, Action items, Social post.
Write in ${targetLanguage}. Do not invent facts.

Transcript:\n${String(text || '').slice(0, 120000)}`;

  const response = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(config.geminiTextModel)}:generateContent`, {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      'x-goog-api-key': config.geminiApiKey
    },
    body: JSON.stringify({ contents: [{ parts: [{ text: prompt }] }] })
  });

  const payload = await response.json();
  if (!response.ok) {
    const error = new Error(payload?.error?.message || 'Gemini request failed');
    error.status = response.status;
    throw error;
  }

  const output = payload?.candidates?.[0]?.content?.parts?.map(part => part.text || '').join('').trim();
  if (!output) throw new Error('Gemini returned no text');
  return { model: config.geminiTextModel, targetLanguage, output };
}

export async function transcribeAudio({ data, mimeType = 'audio/webm', languageCodes = [] }) {
  if (!config.geminiApiKey) {
    const error = new Error('GEMINI_API_KEY is not configured on the server');
    error.status = 503;
    throw error;
  }
  if (!data) throw new Error('Base64 audio data is required');
  if (Buffer.byteLength(data, 'base64') > 20 * 1024 * 1024) {
    const error = new Error('Inline audio is limited to 20 MB; use the Gemini Files API for larger recordings');
    error.status = 413;
    throw error;
  }
  const response = await fetch('https://generativelanguage.googleapis.com/v1beta/interactions', {
    method: 'POST',
    headers: { 'content-type': 'application/json', 'x-goog-api-key': config.geminiApiKey },
    body: JSON.stringify({
      model: config.geminiBatchModel,
      input: [{ type: 'audio', data, mime_type: mimeType }],
      generation_config: { transcription_config: { language_codes: languageCodes } }
    })
  });
  const payload = await response.json();
  if (!response.ok) {
    const error = new Error(payload?.error?.message || 'Gemini transcription failed');
    error.status = response.status;
    throw error;
  }
  const output = payload.output_text || payload.steps?.flatMap(step => step.content || []).filter(part => part.type === 'text').map(part => part.text).join('') || '';
  if (!output.trim()) throw new Error('Gemini returned no transcript');
  return { model: config.geminiBatchModel, transcript: output.trim(), languageCodes };
}
