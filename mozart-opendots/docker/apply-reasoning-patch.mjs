#!/usr/bin/env node
/**
 * mozart-opendots packaging patch: reasoning display.
 *
 * The pinned model (and any reasoning-capable OpenAI-compatible model) streams
 * a reasoning trace that is persisted as messages with role "reasoning". The
 * upstream UI filters those out, so owners never see why a Dot did what it
 * did. This patch renders each reasoning message as a collapsed "Reasoning"
 * disclosure between the surrounding messages:
 *
 *   1. Chat.tsx         - include role "reasoning" in the visible transcript.
 *   2. ChatTranscript.tsx - render reasoning messages as <details> instead of
 *      chat bubbles.
 *   3. style.css        - muted styling for the disclosure.
 *
 * All anchors fail the build loudly if the pinned source changes shape.
 * Run from the repo root before `npm run build`.
 */
import { readFileSync, writeFileSync } from 'node:fs';

const MARKER = 'REASONING-DISPLAY-PATCH';

const groups = [
  {
    path: 'src/client/Chat.tsx',
    sentinel: MARKER,
    edits: [
      {
        label: 'visible filter',
        find: "      ['user', 'assistant'].includes(message.role) &&",
        replace: [
          '      // REASONING-DISPLAY-PATCH: show the model reasoning trace too.',
          "      ['user', 'assistant', 'reasoning'].includes(message.role) &&",
        ].join('\n'),
      },
    ],
  },
  {
    path: 'src/client/ChatTranscript.tsx',
    sentinel: MARKER,
    edits: [
      {
        label: 'reasoning disclosure',
        find: [
          "          {typeof message.content === 'string' && message.content.trim() && (",
          '            <div className={`chat-bubble ${message.role}`}>',
        ].join('\n'),
        replace: [
          '          {/* REASONING-DISPLAY-PATCH: reasoning renders as a collapsed disclosure. */}',
          "          {message.role === 'reasoning' &&",
          "            typeof message.content === 'string' &&",
          '            message.content.trim() && (',
          '              <details className="chat-reasoning">',
          '                <summary>Reasoning</summary>',
          '                <div className="chat-reasoning-body">',
          '                  <ReactMarkdown>{String(message.content)}</ReactMarkdown>',
          '                </div>',
          '              </details>',
          '            )}',
          "          {message.role !== 'reasoning' &&",
          "            typeof message.content === 'string' &&",
          '            message.content.trim() && (',
          '            <div className={`chat-bubble ${message.role}`}>',
        ].join('\n'),
      },
    ],
  },
  {
    path: 'src/client/style.css',
    sentinel: MARKER,
    edits: [
      {
        label: 'reasoning styles',
        find: [
          '.call-caption .call-user-caption {',
          '  font-size: 14px;',
          '  color: #c9e8dd;',
          '}',
        ].join('\n'),
        replace: [
          '.call-caption .call-user-caption {',
          '  font-size: 14px;',
          '  color: #c9e8dd;',
          '}',
          '',
          '/* REASONING-DISPLAY-PATCH */',
          '.chat-reasoning {',
          '  align-self: flex-start;',
          '  max-width: 79%;',
          '  margin: 2px 0 6px 2px;',
          '  border-left: 2px solid rgba(168, 175, 189, 0.4);',
          '  padding: 2px 0 2px 10px;',
          '  font-size: 12.5px;',
          '  color: #a8afbd;',
          '}',
          '.chat-reasoning summary {',
          '  cursor: pointer;',
          '  font-size: 10px;',
          '  letter-spacing: 0.09em;',
          '  text-transform: uppercase;',
          '  opacity: 0.8;',
          '}',
          '.chat-reasoning-body {',
          '  margin-top: 6px;',
          '}',
          '.chat-reasoning-body p {',
          '  margin: 0 0 8px;',
          '}',
        ].join('\n'),
      },
    ],
  },
];

for (const group of groups) {
  let text = readFileSync(group.path, 'utf8');
  if (text.includes(group.sentinel)) {
    console.log(`[reasoning-patch] already applied: ${group.path}`);
    continue;
  }
  for (const { label, find, replace } of group.edits) {
    const matches = text.split(find).length - 1;
    if (matches !== 1) {
      console.error(
        `[reasoning-patch] ERROR: anchor "${label}" matched ${matches} times in ${group.path} (expected 1); pinned source changed`,
      );
      process.exit(1);
    }
    text = text.replace(find, replace);
  }
  writeFileSync(group.path, text);
  console.log(`[reasoning-patch] patched: ${group.path}`);
}
console.log('[reasoning-patch] done');
