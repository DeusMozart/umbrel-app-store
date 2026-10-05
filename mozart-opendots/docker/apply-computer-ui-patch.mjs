#!/usr/bin/env node
/**
 * mozart-opendots packaging patch: per-Dot computer status + controls.
 *
 * Computers are configured at the deployment level (COMPUTER_* env vars), so
 * there is nothing to "add" per Dot — but upstream also gives the Dot editor
 * no visibility into the computer at all. This patch adds a Computer section
 * to the Dot editor (existing Dots only): current state, permission summary,
 * and Start/Stop buttons that reuse the existing /dots/:id/computer routes.
 *
 *   1. WorkspaceDialog.tsx - Computer section in the Dot editor.
 *
 * Anchors fail the build loudly if the pinned source changes shape.
 * Run from the repo root before `npm run build`.
 */
import { readFileSync, writeFileSync } from 'node:fs';

const MARKER = 'COMPUTER-UI-PATCH';

const groups = [
  {
    path: 'src/client/WorkspaceDialog.tsx',
    sentinel: 'Computers are not configured on this server',
    edits: [
      {
        label: 'ComputerStatus import',
        find: "import type { Dot, Memory, State, WorkspaceState } from '../shared/types';",
        replace: [
          "import type { Dot, Memory, State, WorkspaceState } from '../shared/types';",
          "import type { ComputerStatus } from '../shared/computer-types';",
        ].join('\n'),
      },
      {
        label: 'computer states',
        find: [
          "  const [error, setError] = useState('');",
          '  const container = useRef<HTMLElement>(null);',
        ].join('\n'),
        replace: [
          "  const [error, setError] = useState('');",
          '  const [computer, setComputer] = useState<ComputerStatus>();',
          '  const [computerBusy, setComputerBusy] = useState(false);',
          "  const [computerError, setComputerError] = useState('');",
          '  const container = useRef<HTMLElement>(null);',
        ].join('\n'),
      },
      {
        label: 'computer fetch + actions',
        find: '  const title =',
        replace: [
          "  const computerDotId = dialog.type === 'dot' ? dialog.dot?.id : undefined;",
          '  useEffect(() => {',
          '    if (!computerDotId) return;',
          '    let active = true;',
          '    void api<ComputerStatus>(',
          '      `/dots/${encodeURIComponent(computerDotId)}/computer`,',
          "      'GET',",
          '    )',
          '      .then((status) => {',
          '        if (active) {',
          '          setComputer(status);',
          "          setComputerError('');",
          '        }',
          '      })',
          '      .catch((cause) => {',
          '        if (active)',
          '          setComputerError(',
          '            cause instanceof Error',
          '              ? cause.message',
          "              : 'Could not load the computer.',",
          '          );',
          '      });',
          '    return () => {',
          '      active = false;',
          '    };',
          '  }, [computerDotId]);',
          "  const computerAction = async (verb: 'start' | 'stop') => {",
          '    if (!computerDotId || computerBusy) return;',
          '    setComputerBusy(true);',
          "    setComputerError('');",
          '    try {',
          '      const status = await api<ComputerStatus>(',
          '        `/dots/${encodeURIComponent(computerDotId)}/computer/${verb}`,',
          "        'POST',",
          '        {},',
          '      );',
          '      setComputer(status);',
          '    } catch (cause) {',
          '      setComputerError(',
          "        cause instanceof Error ? cause.message : 'Computer action failed.',",
          '      );',
          '    } finally {',
          '      setComputerBusy(false);',
          '    }',
          '  };',
          '  const title =',
        ].join('\n'),
      },
      {
        label: 'Computer fieldset',
        find: "          {dialog.type === 'schedule' && (",
        replace: [
          "          {dialog.type === 'dot' && dialog.dot && (",
          '            <fieldset className="space-access-fields">',
          '              <legend>Computer</legend>',
          '              {computerError && (',
          '                <p className="chat-error" role="alert">',
          '                  {computerError}',
          '                </p>',
          '              )}',
          '              {!computer && !computerError && <p className="muted">Checking…</p>}',
          '              {computer && !computer.configured && (',
          '                <p className="muted">',
          '                  Computers are not configured on this server. Set',
          '                  COMPUTER_SUPERVISOR_URL, COMPUTER_SUPERVISOR_TOKEN and',
          '                  COMPUTER_TOKEN in the app environment to enable them.',
          '                </p>',
          '              )}',
          '              {computer && computer.configured && (',
          '                <>',
          '                  <p className="muted">',
          "                    {computer.state === 'running'",
          "                      ? 'Running'",
          "                      : computer.state === 'stopped'",
          "                        ? 'Stopped'",
          "                        : computer.state === 'unavailable'",
          "                          ? 'Supervisor unreachable'",
          "                          : 'Not started'}",
          "                    {' · '}",
          "                    Permissions:{' '}",
          '                    {computer.permissions.enabled',
          "                      ? [computer.permissions.browser && 'browser', computer.permissions.files && 'files', computer.permissions.shell && 'shell']",
          '                          .filter((item): item is string => !!item)',
          "                          .join(', ') || 'none'",
          "                      : 'disabled'}",
          '                  </p>',
          '                  {computer.state === \'running\' ? (',
          '                    <button',
          '                      type="button"',
          '                      className="primary"',
          '                      disabled={computerBusy}',
          '                      onClick={() => void computerAction(\'stop\')}',
          '                    >',
          '                      Stop computer',
          '                    </button>',
          '                  ) : computer.permissions.enabled ? (',
          '                    <button',
          '                      type="button"',
          '                      className="primary"',
          '                      disabled={computerBusy}',
          '                      onClick={() => void computerAction(\'start\')}',
          '                    >',
          '                      Start computer',
          '                    </button>',
          '                  ) : (',
          '                    <p className="muted">',
          '                      Enable computer permissions from the computer panel in',
          '                      chat, then start it here.',
          '                    </p>',
          '                  )}',
          '                </>',
          '              )}',
          '            </fieldset>',
          '          )}',
          "          {dialog.type === 'schedule' && (",
        ].join('\n'),
      },
    ],
  },
];

for (const group of groups) {
  let text = readFileSync(group.path, 'utf8');
  if (text.includes(group.sentinel)) {
    console.log(`[computer-ui-patch] already applied: ${group.path}`);
    continue;
  }
  for (const { label, find, replace } of group.edits) {
    const matches = text.split(find).length - 1;
    if (matches !== 1) {
      console.error(
        `[computer-ui-patch] ERROR: anchor "${label}" matched ${matches} times in ${group.path} (expected 1); pinned source changed`,
      );
      process.exit(1);
    }
    text = text.replace(find, replace);
  }
  writeFileSync(group.path, text);
  console.log(`[computer-ui-patch] patched: ${group.path}`);
}
console.log('[computer-ui-patch] done');
