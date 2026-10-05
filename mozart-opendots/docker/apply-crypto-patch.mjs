#!/usr/bin/env node
/**
 * mozart-opendots packaging patch: insecure-context UUID fallback.
 *
 * OpenDots runs fine on plain HTTP (e.g. http://umbrel.local:4310), but the
 * chat composer builds every outbound user message with `crypto.randomUUID()`
 * (pinned commit c2569bb, src/client/Chat.tsx). In a non-secure context
 * (`window.isSecureContext === false`), `crypto.randomUUID` is undefined, so
 * the very first send throws before the message is even added, silently locks
 * the composer's `running` state, and every later send is a no-op — chat
 * appears broken while the server side is healthy.
 *
 * One edit, failing the build loudly if the pinned source changes shape:
 * replace the call with a local helper that uses crypto.randomUUID when
 * available and a v4-shaped fallback otherwise.
 *
 * Run from the repo root before `npm run build`.
 */
import { readFileSync, writeFileSync } from 'node:fs';

const FILE = 'src/client/Chat.tsx';
const MARKER = 'INSECURE-CONTEXT-UUID-PATCH';

const IMPORT_ANCHOR = "import { CallView } from './CallView';";

const HELPER = [
  '',
  `// ${MARKER}: crypto.randomUUID is undefined outside secure contexts`,
  '// (plain-HTTP deployments); fall back to a v4-shaped id so sends work',
  '// over http as well as https.',
  'function uuidFallback(): string {',
  '  const c = (globalThis as { crypto?: { randomUUID?: () => string } }).crypto;',
  "  if (typeof c?.randomUUID === 'function') return c.randomUUID();",
  "  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (ch) => {",
  '    const r = (Math.random() * 16) | 0;',
  "    return (ch === 'x' ? r : (r & 0x3) | 0x8).toString(16);",
  '  });',
  '}',
].join('\n');

const CALL_ANCHOR = ['    agent.addMessage({', '      id: crypto.randomUUID(),'].join('\n');

const CALL_REPLACEMENT = [
  '    agent.addMessage({',
  `      // ${MARKER}: works over http (non-secure contexts) too.`,
  '      id: uuidFallback(),',
].join('\n');

const text = readFileSync(FILE, 'utf8');
if (text.includes(MARKER)) {
  console.log(`[crypto-patch] already applied: ${FILE}`);
  process.exit(0);
}
for (const { label, anchor } of [
  { label: 'import anchor', anchor: IMPORT_ANCHOR },
  { label: 'addMessage call', anchor: CALL_ANCHOR },
]) {
  const matches = text.split(anchor).length - 1;
  if (matches !== 1) {
    console.error(
      `[crypto-patch] ERROR: anchor "${label}" matched ${matches} times in ${FILE} (expected 1); pinned source changed`,
    );
    process.exit(1);
  }
}
writeFileSync(
  FILE,
  text.replace(IMPORT_ANCHOR, IMPORT_ANCHOR + '\n' + HELPER).replace(CALL_ANCHOR, CALL_REPLACEMENT),
);
console.log(`[crypto-patch] patched: ${FILE}`);
