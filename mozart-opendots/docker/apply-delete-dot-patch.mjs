#!/usr/bin/env node
/**
 * mozart-opendots packaging patch: Delete Dot.
 *
 * Upstream (pinned c2569bb) has no way to remove a Dot: no UI affordance, no
 * server route, no store method. This patch adds the full path:
 *
 *   1. workspace.ts        - deleteDot(): cascades the Dot's conversations
 *      (thread_bindings), captures, calls, scheduled tasks (+runs/events),
 *      space links, computer permissions/audit, then the Dot row itself.
 *   2. computer-service.ts - remove(): best-effort supervisor /reset so the
 *      Dot's computer container and volume go away too.
 *   3. workspace-routes.ts - DELETE /dots/:id.
 *   4. WorkspaceDialog.tsx - a two-step "Delete this Dot" control in the
 *      Dot editor (its conversations go with it).
 *   5. style.css           - styles for the delete control.
 *
 * All anchors fail the build loudly if the pinned source changes shape.
 * Run from the repo root before `npm run build`.
 */
import { readFileSync, writeFileSync } from 'node:fs';

const MARKER = 'DELETE-DOT-PATCH';

const groups = [
  {
    path: 'src/server/workspace.ts',
    sentinel: 'deleteDot(',
    edits: [
      {
        label: 'deleteDot method',
        find: '  conversations(): Conversation[] {',
        replace: [
          '  deleteDot(id: string): boolean {',
          '    const dot = this.dot(id);',
          "    if (!dot) throw new Error('Dot not found.');",
          '    const threads = this.db',
          "      .prepare('SELECT id FROM thread_bindings WHERE dotId=?')",
          '      .all(id)',
          '      .map((row) => String(row.id));',
          '    const taskIds = threads.length',
          '      ? (',
          '          this.db',
          '            .prepare(',
          "              `SELECT taskId FROM task_threads WHERE threadId IN (${threads",
          "                .map(() => '?')",
          "                .join(',')})`,",
          '            )',
          '            .all(...threads) as { taskId: string }[]',
          '        ).map((row) => String(row.taskId))',
          '      : [];',
          "    this.db.exec('BEGIN');",
          '    try {',
          '      for (const threadId of threads) {',
          "        this.db.prepare('DELETE FROM captures WHERE threadId=?').run(threadId);",
          "        this.db.prepare('DELETE FROM calls WHERE threadId=?').run(threadId);",
          "        this.db.prepare('DELETE FROM task_threads WHERE threadId=?').run(threadId);",
          '      }',
          '      for (const taskId of taskIds) {',
          "        this.db.prepare('DELETE FROM events WHERE taskId=?').run(taskId);",
          "        this.db.prepare('DELETE FROM runs WHERE taskId=?').run(taskId);",
          "        this.db.prepare('DELETE FROM tasks WHERE id=?').run(taskId);",
          '      }',
          "      this.db.prepare('DELETE FROM thread_bindings WHERE dotId=?').run(id);",
          "      this.db.prepare('DELETE FROM dot_spaces WHERE dotId=?').run(id);",
          "      this.db.prepare('DELETE FROM computer_permissions WHERE dotId=?').run(id);",
          "      this.db.prepare('DELETE FROM computer_audit WHERE dotId=?').run(id);",
          "      this.db.prepare('DELETE FROM dots WHERE id=?').run(id);",
          "      this.db.exec('COMMIT');",
          '    } catch (error) {',
          "      this.db.exec('ROLLBACK');",
          '      throw error;',
          '    }',
          '    return true;',
          '  }',
          '  conversations(): Conversation[] {',
        ].join('\n'),
      },
    ],
  },
  {
    path: 'src/server/computer-service.ts',
    sentinel: MARKER,
    edits: [
      {
        label: 'ComputerService.remove',
        find: "  async control(id: string, verb: 'take' | 'release') {",
        replace: [
          '  // DELETE-DOT-PATCH: best-effort computer teardown for a deleted Dot.',
          '  async remove(id: string) {',
          '    this.requireDot(id);',
          '    if (!this.configured) return;',
          '    await this.supervisor(`/computers/${id}/reset`, {});',
          '  }',
          "  async control(id: string, verb: 'take' | 'release') {",
        ].join('\n'),
      },
    ],
  },
  {
    path: 'src/server/workspace-routes.ts',
    sentinel: MARKER,
    edits: [
      {
        label: 'DELETE /dots/:id route',
        find: "  app.post('/conversations', async (c) => {",
        replace: [
          "  app.delete('/dots/:id', async (c) => {",
          "    const id = c.req.param('id');",
          '    if (!platform.workspace.dot(id))',
          "      return c.json({ error: 'Dot not found.' }, 404);",
          '    // DELETE-DOT-PATCH: the computer is best-effort cleanup; deletion',
          '    // proceeds even when the supervisor is offline.',
          '    try {',
          '      await platform.computers.remove(id);',
          '    } catch {',
          '      // ignore: supervisor unavailable',
          '    }',
          '    platform.workspace.deleteDot(id);',
          '    return c.json({ deleted: id });',
          '  });',
          "  app.post('/conversations', async (c) => {",
        ].join('\n'),
      },
    ],
  },
  {
    path: 'src/client/WorkspaceDialog.tsx',
    sentinel: 'dot-danger',
    edits: [
      {
        label: 'delete confirm state',
        find: [
          '  const [busy, setBusy] = useState(false);',
          "  const [error, setError] = useState('');",
        ].join('\n'),
        replace: [
          '  const [busy, setBusy] = useState(false);',
          '  const [confirmingDelete, setConfirmingDelete] = useState(false);',
          "  const [error, setError] = useState('');",
        ].join('\n'),
      },
      {
        label: 'delete control',
        find: [
          '          <button className="primary full" disabled={busy}>',
          "            {busy ? 'Saving…' : 'Save'}",
          '          </button>',
        ].join('\n'),
        replace: [
          '          {dialog.type === \'dot\' && dialog.dot && (',
          '            <div className="dot-danger">',
          '              {confirmingDelete ? (',
          '                <button',
          '                  type="button"',
          '                  className="danger full"',
          '                  disabled={busy}',
          '                  onClick={async () => {',
          '                    const id = dialog.dot?.id;',
          '                    if (!id) return;',
          "                    if (await mutate(`/dots/${id}`, 'DELETE')) onClose();",
          '                  }}',
          '                >',
          '                  Yes, delete this Dot and its conversations',
          '                </button>',
          '              ) : (',
          '                <button',
          '                  type="button"',
          '                  className="danger-link"',
          '                  onClick={() => setConfirmingDelete(true)}',
          '                >',
          '                  Delete this Dot…',
          '                </button>',
          '              )}',
          '            </div>',
          '          )}',
          '          <button className="primary full" disabled={busy}>',
          "            {busy ? 'Saving…' : 'Save'}",
          '          </button>',
        ].join('\n'),
      },
    ],
  },
  {
    path: 'src/client/style.css',
    sentinel: MARKER,
    edits: [
      {
        label: 'delete styles',
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
          '/* DELETE-DOT-PATCH */',
          '.dot-danger {',
          '  margin-top: 20px;',
          '  display: grid;',
          '  gap: 8px;',
          '}',
          '.dot-danger .danger-link {',
          '  background: none;',
          '  border: 0;',
          '  color: #e09a9a;',
          '  cursor: pointer;',
          '  font: inherit;',
          '  padding: 0;',
          '  text-align: left;',
          '  text-decoration: underline;',
          '}',
          '.dot-danger .danger {',
          '  background: #7a2f2f;',
          '  color: #ffe9e9;',
          '  border: 0;',
          '  border-radius: 10px;',
          '  padding: 12px 16px;',
          '  cursor: pointer;',
          '  font: inherit;',
          '}',
          '.dot-danger .danger:disabled {',
          '  opacity: 0.6;',
          '  cursor: default;',
          '}',
        ].join('\n'),
      },
    ],
  },
];

for (const group of groups) {
  let text = readFileSync(group.path, 'utf8');
  if (text.includes(group.sentinel)) {
    console.log(`[delete-dot-patch] already applied: ${group.path}`);
    continue;
  }
  for (const { label, find, replace } of group.edits) {
    const matches = text.split(find).length - 1;
    if (matches !== 1) {
      console.error(
        `[delete-dot-patch] ERROR: anchor "${label}" matched ${matches} times in ${group.path} (expected 1); pinned source changed`,
      );
      process.exit(1);
    }
    text = text.replace(find, replace);
  }
  writeFileSync(group.path, text);
  console.log(`[delete-dot-patch] patched: ${group.path}`);
}
console.log('[delete-dot-patch] done');
