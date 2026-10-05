#!/usr/bin/env node
/**
 * mozart-opendots packaging patch: per-Dot computers (split topology).
 *
 * The OpenDots app only accepts a computer endpoint that is either a container
 * name on a shared network, or a loopback address when the supervisor itself is
 * configured on loopback (pinned commit c2569bb, src/server/computer-service.ts).
 * A split deployment — app on one host, OpenBot supervisor on another — returns
 * computer URLs on the supervisor's own host, which the checks reject with
 * "Computer endpoint is not bound to this Dot."
 *
 * Two edits, both failing the build loudly if the pinned source changes shape:
 *
 * 1. the `local` check accepts exactly the supervisor's own hostname (single-
 *    host loopback behaviour unchanged; any other host still rejected);
 * 2. the URL fallback for a supervisor response that carries only a port now
 *    uses the supervisor's host instead of assuming loopback.
 *
 * Run from the repo root before `npm run build`.
 */
import { readFileSync, writeFileSync } from 'node:fs';

const FILE = 'src/server/computer-service.ts';
const MARKER = 'COMPUTER-SPLIT-PATCH';

const ANCHOR = [
  "    const local =",
  "      url.hostname === '127.0.0.1' &&",
  '      !!state.port &&',
  '      url.port === String(state.port) &&',
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

const FALLBACK_ANCHOR = [
  '    const url = new URL(',
  '      state.url ??',
  '        (state.port',
  '          ? `http://127.0.0.1:${state.port}`',
  '          : `http://${expected}:4100`),',
  '    );',
].join('\n');

const FALLBACK_REPLACEMENT = [
  '    const url = new URL(',
  '      state.url ??',
  '        (state.port',
  "          ? `http://${new URL(this.config.computerSupervisorUrl!).hostname}:${state.port}`",
  '          : `http://${expected}:4100`),',
  '    );',
].join('\n');

const text = readFileSync(FILE, 'utf8');
if (text.includes(MARKER)) {
  console.log(`[computers-patch] already applied: ${FILE}`);
  process.exit(0);
}
for (const { label, anchor } of [
  { label: 'local check', anchor: ANCHOR },
  { label: 'url fallback', anchor: FALLBACK_ANCHOR },
]) {
  const matches = text.split(anchor).length - 1;
  if (matches !== 1) {
    console.error(
      `[computers-patch] ERROR: anchor "${label}" matched ${matches} times in ${FILE} (expected 1); pinned source changed`,
    );
    process.exit(1);
  }
}
writeFileSync(
  FILE,
  text.replace(ANCHOR, REPLACEMENT).replace(FALLBACK_ANCHOR, FALLBACK_REPLACEMENT),
);
console.log(`[computers-patch] patched: ${FILE}`);
