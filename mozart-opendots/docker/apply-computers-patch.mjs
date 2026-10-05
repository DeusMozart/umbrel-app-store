#!/usr/bin/env node
/**
 * mozart-opendots packaging patch: per-Dot computers (split topology).
 *
 * The OpenDots app only accepts a computer endpoint that is either a container
 * name on a shared network, or a loopback address when the supervisor itself is
 * configured on loopback (the `local` check in src/server/computer-service.ts,
 * pinned commit c2569bb). A split deployment — app on one host, OpenBot
 * supervisor on another — returns a computer URL on the supervisor's own host,
 * which the check rejects with "Computer endpoint is not bound to this Dot."
 *
 * This patch widens `local` to accept exactly the supervisor's own hostname:
 *   - single-host (loopback) behaviour is unchanged: the advertised host must
 *     still equal the supervisor host;
 *   - split deployments accept http://<supervisor-host>:<published port>;
 *   - any other host is still rejected (fail closed).
 *
 * Run from the repo root before `npm run build`.
 */
import { readFileSync, writeFileSync } from 'node:fs';

const FILE = 'src/server/computer-service.ts';
const MARKER = 'COMPUTER-SPLIT-PATCH';

const ANCHOR = [
  "    const local =",
  "      url.hostname === '127.0.0.1' &&",
  "      !!state.port &&",
  "      url.port === String(state.port) &&",
  "      new URL(this.config.computerSupervisorUrl!).hostname === '127.0.0.1';",
].join('\n');

const REPLACEMENT = [
  `    // ${MARKER}: accept a computer URL on the supervisor's own host, not`,
  '    // only loopback (app on one host, supervisor on another). The',
  '    // single-host loopback arrangement is unchanged.',
  '    const local =',
  '      !!state.port &&',
  '      url.port === String(state.port) &&',
  "      url.hostname === new URL(this.config.computerSupervisorUrl!).hostname;",
].join('\n');

const text = readFileSync(FILE, 'utf8');
if (text.includes(MARKER)) {
  console.log(`[computers-patch] already applied: ${FILE}`);
  process.exit(0);
}
const matches = text.split(ANCHOR).length - 1;
if (matches !== 1) {
  console.error(
    `[computers-patch] ERROR: anchor matched ${matches} times in ${FILE} (expected 1); pinned source changed`,
  );
  process.exit(1);
}
writeFileSync(FILE, text.replace(ANCHOR, REPLACEMENT));
console.log(`[computers-patch] patched: ${FILE}`);
