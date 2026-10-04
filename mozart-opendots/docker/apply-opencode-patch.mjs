#!/usr/bin/env node
/**
 * mozart-opendots packaging patch: OpenCode Go support.
 *
 * OpenCode Go (https://opencode.ai/docs/go) requires model requests to carry a
 * stable `x-opencode-session` header for routing and prompt caching; requests
 * without it are rejected with HTTP 400 MissingSessionID. Upstream OpenDots
 * (pinned commit c2569bb) does not send it, so this script injects the header
 * into both model call sites:
 *
 *   - src/server/dot-agent.ts  (TanStack AI adapter -> defaultHeaders)
 *   - src/server/research.ts   (direct fetch -> spread into headers)
 *
 * The header is sent only when OPENCODE_SESSION_ID is set, so the patch is
 * inert for other model providers. Run from the repo root before `npm run build`.
 */
import { readFileSync, writeFileSync } from 'node:fs';

const MODULE_PATH = 'src/server/opencode-headers.ts';
const MODULE_SOURCE = `/**
 * Added by the mozart-opendots packaging (umbrel community app).
 * OpenCode Go requires a stable x-opencode-session header for routing and
 * prompt caching. Enabled only when OPENCODE_SESSION_ID is set, so this file
 * is inert for other model providers.
 */
export const OPENCODE_HEADERS: Record<string, string> = process.env.OPENCODE_SESSION_ID
  ? {
      'x-opencode-session': process.env.OPENCODE_SESSION_ID,
      'user-agent': process.env.OPENCODE_USER_AGENT || 'opendots/1.0',
    }
  : {};
`;

function fail(message) {
  console.error(`[opencode-patch] ERROR: ${message}`);
  process.exit(1);
}

function patchFile(path, replacements) {
  let text = readFileSync(path, 'utf8');
  if (text.includes('OPENCODE_HEADERS')) {
    console.log(`[opencode-patch] already applied: ${path}`);
    return;
  }
  for (const { find, replace, label } of replacements) {
    const matches = text.split(find).length - 1;
    if (matches !== 1) {
      fail(`anchor "${label}" matched ${matches} times in ${path} (expected 1)`);
    }
    text = text.replace(find, replace);
  }
  writeFileSync(path, text);
  console.log(`[opencode-patch] patched: ${path}`);
}

writeFileSync(MODULE_PATH, MODULE_SOURCE);
console.log(`[opencode-patch] wrote: ${MODULE_PATH}`);

patchFile('src/server/dot-agent.ts', [
  {
    label: 'dot-agent import',
    find: "import { openaiCompatibleText } from '@tanstack/ai-openai/compatible';",
    replace:
      "import { openaiCompatibleText } from '@tanstack/ai-openai/compatible';\n" +
      "import { OPENCODE_HEADERS } from './opencode-headers.js';",
  },
  {
    label: 'dot-agent adapter options',
    find: '          maxRetries: 1,\n',
    replace: '          maxRetries: 1,\n          defaultHeaders: OPENCODE_HEADERS,\n',
  },
]);

patchFile('src/server/research.ts', [
  {
    label: 'research import',
    find: "import type { Memory, Result } from '../shared/types.js';",
    replace:
      "import type { Memory, Result } from '../shared/types.js';\n" +
      "import { OPENCODE_HEADERS } from './opencode-headers.js';",
  },
  {
    label: 'research model fetch headers',
    find: '        Authorization: `Bearer ${config.apiKey}`,\n',
    replace: '        Authorization: `Bearer ${config.apiKey}`,\n        ...OPENCODE_HEADERS,\n',
  },
]);

console.log('[opencode-patch] done');
