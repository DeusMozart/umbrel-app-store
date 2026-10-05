#!/usr/bin/env node
/**
 * mozart-opendots packaging patch: per-Dot model selection.
 *
 * Upstream (pinned c2569bb) uses one workspace-wide model (OPENAI_MODEL) for
 * every Dot. This patch lets each Dot carry its own model id, falling back to
 * the workspace model when unset:
 *
 *   1. shared/types.ts        - Dot.model field.
 *   2. server/workspace.ts    - dots.model column (+migration), create/update.
 *   3. server/workspace-routes.ts - dot schema accepts `model`; new GET /models
 *      route exposes the provider's model list for the picker.
 *   4. server/dot-agent.ts    - the Dot's adapter uses dot.model when set.
 *   5. client/WorkspaceDialog.tsx - a Model select in the Dot editor (populated
 *      from /models; "Workspace default" keeps the old behaviour).
 *
 * All anchors fail the build loudly if the pinned source changes shape.
 * Run from the repo root before `npm run build`.
 */
import { readFileSync, writeFileSync } from 'node:fs';

const MARKER = 'DOT-MODEL-PATCH';

const groups = [
  {
    path: 'src/shared/types.ts',
    sentinel: 'per-Dot model id',
    edits: [
      {
        label: 'Dot.model',
        find: [
          '  createdAt: number;',
          '  learningContainerId?: string | null;',
          '  skillDeliveryEnabled?: boolean;',
          '}',
        ].join('\n'),
        replace: [
          '  createdAt: number;',
          '  /** Optional per-Dot model id; null/absent falls back to the workspace model. */',
          '  model?: string | null;',
          '  learningContainerId?: string | null;',
          '  skillDeliveryEnabled?: boolean;',
          '}',
        ].join('\n'),
      },
    ],
  },
  {
    path: 'src/server/workspace.ts',
    sentinel: MARKER,
    edits: [
      {
        label: 'dots.model migration',
        find: [
          "      ['dots', 'learningContainerId', 'TEXT'],",
          "      ['dots', 'skillDeliveryEnabled', 'INTEGER NOT NULL DEFAULT 0'],",
        ].join('\n'),
        replace: [
          "      ['dots', 'learningContainerId', 'TEXT'],",
          "      ['dots', 'skillDeliveryEnabled', 'INTEGER NOT NULL DEFAULT 0'],",
          "      ['dots', 'model', 'TEXT'],",
        ].join('\n'),
      },
      {
        label: 'createDot signature',
        find: [
          '    spaceIds: string[] = [spaceId],',
          '    learningContainerId: string | null = null,',
          '    skillDeliveryEnabled = false,',
          '  ): Dot {',
        ].join('\n'),
        replace: [
          '    spaceIds: string[] = [spaceId],',
          '    learningContainerId: string | null = null,',
          '    skillDeliveryEnabled = false,',
          '    model: string | null = null,',
          '  ): Dot {',
        ].join('\n'),
      },
      {
        label: 'createDot object',
        find: [
          '      learningContainerId,',
          '      skillDeliveryEnabled,',
          '      createdAt: Date.now(),',
          '    };',
        ].join('\n'),
        replace: [
          '      learningContainerId,',
          '      skillDeliveryEnabled,',
          '      model,',
          '      createdAt: Date.now(),',
          '    };',
        ].join('\n'),
      },
      {
        label: 'createDot INSERT',
        find: "          'INSERT INTO dots (id, spaceId, name, instructions, researchAllowed, memoryAllowed, createdAt, learningContainerId, skillDeliveryEnabled) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',",
        replace: "          'INSERT INTO dots (id, spaceId, name, instructions, researchAllowed, memoryAllowed, createdAt, learningContainerId, skillDeliveryEnabled, model) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',",
      },
      {
        label: 'createDot run args',
        find: [
          '          dot.createdAt,',
          '          learningContainerId,',
          '          +skillDeliveryEnabled,',
          '        );',
        ].join('\n'),
        replace: [
          '          dot.createdAt,',
          '          learningContainerId,',
          '          +skillDeliveryEnabled,',
          '          model,',
          '        );',
        ].join('\n'),
      },
      {
        label: 'updateDot patch type',
        find: [
          '      spaceId?: string;',
          '      spaceIds?: string[];',
          '      learningContainerId?: string | null;',
          '      skillDeliveryEnabled?: boolean;',
          '    },',
        ].join('\n'),
        replace: [
          '      spaceId?: string;',
          '      spaceIds?: string[];',
          '      learningContainerId?: string | null;',
          '      skillDeliveryEnabled?: boolean;',
          '      model?: string | null;',
          '    },',
        ].join('\n'),
      },
      {
        label: 'updateDot resolve model',
        find: [
          '    const skillDeliveryEnabled =',
          '      patch.skillDeliveryEnabled ?? current.skillDeliveryEnabled ?? false;',
        ].join('\n'),
        replace: [
          '    const skillDeliveryEnabled =',
          '      patch.skillDeliveryEnabled ?? current.skillDeliveryEnabled ?? false;',
          '    // DOT-MODEL-PATCH: undefined keeps the current model; null clears it.',
          '    const model =',
          '      patch.model === undefined ? (current.model ?? null) : patch.model;',
        ].join('\n'),
      },
      {
        label: 'updateDot UPDATE',
        find: "          'UPDATE dots SET name=?, instructions=?, researchAllowed=?, memoryAllowed=?, learningContainerId=?, skillDeliveryEnabled=? WHERE id=?'",
        replace: "          'UPDATE dots SET name=?, instructions=?, researchAllowed=?, memoryAllowed=?, learningContainerId=?, skillDeliveryEnabled=?, model=? WHERE id=?'",
      },
      {
        label: 'updateDot run args',
        find: [
          '          learningContainerId,',
          '          +skillDeliveryEnabled,',
          '          id,',
          '        );',
        ].join('\n'),
        replace: [
          '          learningContainerId,',
          '          +skillDeliveryEnabled,',
          '          model,',
          '          id,',
          '        );',
        ].join('\n'),
      },
    ],
  },
  {
    path: 'src/server/workspace-routes.ts',
    sentinel: MARKER,
    edits: [
      {
        label: 'models cache',
        find: 'const dotSchema = z',
        replace: [
          'let modelsCache: { at: number; list: string[] } | null = null;',
          'const dotSchema = z',
        ].join('\n'),
      },
      {
        label: 'dot schema model',
        find: [
          '    learningContainerId: learningContainerIdSchema.optional(),',
          '    skillDeliveryEnabled: z.boolean().optional(),',
        ].join('\n'),
        replace: [
          '    learningContainerId: learningContainerIdSchema.optional(),',
          '    skillDeliveryEnabled: z.boolean().optional(),',
          '    model: z.string().trim().min(1).max(120).nullable().optional(),',
        ].join('\n'),
      },
      {
        label: 'createDot call model',
        find: [
          '        data.data.learningContainerId,',
          '        data.data.skillDeliveryEnabled,',
          '      ),',
          '      201,',
        ].join('\n'),
        replace: [
          '        data.data.learningContainerId,',
          '        data.data.skillDeliveryEnabled,',
          '        data.data.model ?? null,',
          '      ),',
          '      201,',
        ].join('\n'),
      },
      {
        label: 'GET /models route',
        find: "  app.post('/spaces', async (c) => {",
        replace: [
          '  // DOT-MODEL-PATCH: model list for the per-Dot model picker. Fetched',
          '  // from the configured OpenAI-compatible provider; never exposes the key.',
          "  app.get('/models', async (c) => {",
          '    const { apiKey, baseUrl } = platform.config;',
          '    if (!apiKey) return c.json({ models: [] });',
          '    if (modelsCache && Date.now() - modelsCache.at < 300_000)',
          '      return c.json({ models: modelsCache.list });',
          '    try {',
          '      const response = await fetch(`${baseUrl.replace(/\\/$/, \'\')}/models`, {',
          '        headers: {',
          '          Authorization: `Bearer ${apiKey}`,',
          "          Accept: 'application/json',",
          '        },',
          '        signal: AbortSignal.timeout(10_000),',
          '      });',
          '      if (!response.ok) throw new Error(String(response.status));',
          '      const body = (await response.json()) as { data?: { id?: unknown }[] };',
          '      const list = Array.isArray(body.data)',
          '        ? body.data',
          '            .map((entry) => entry?.id)',
          '            .filter((id): id is string => typeof id === \'string\')',
          '            .sort()',
          '        : [];',
          '      modelsCache = { at: Date.now(), list };',
          '      return c.json({ models: list });',
          '    } catch {',
          '      return c.json({ models: [] });',
          '    }',
          '  });',
          "  app.post('/spaces', async (c) => {",
        ].join('\n'),
      },
    ],
  },
  {
    path: 'src/server/dot-agent.ts',
    sentinel: 'dot.model?.trim()',
    edits: [
      {
        label: 'per-Dot model adapter',
        find: '        const adapter = openaiCompatibleText(this.config.model, {',
        replace: '        const adapter = openaiCompatibleText(dot.model?.trim() || this.config.model, {',
      },
    ],
  },
  {
    path: 'src/client/WorkspaceDialog.tsx',
    sentinel: 'Choose the language model for this Dot',
    edits: [
      {
        label: 'api import',
        find: "import type { Dot, Memory, State, WorkspaceState } from '../shared/types';",
        replace: [
          "import type { Dot, Memory, State, WorkspaceState } from '../shared/types';",
          "import { api } from './api';",
        ].join('\n'),
      },
      {
        label: 'model state + fetch',
        find: [
          '  const [skillDelivery, setSkillDelivery] = useState(',
          "    dialog.type === 'dot' ? (dialog.dot?.skillDeliveryEnabled ?? false) : false,",
          '  );',
        ].join('\n'),
        replace: [
          '  const [skillDelivery, setSkillDelivery] = useState(',
          "    dialog.type === 'dot' ? (dialog.dot?.skillDeliveryEnabled ?? false) : false,",
          '  );',
          '  const [model, setModel] = useState(',
          "    dialog.type === 'dot' ? (dialog.dot?.model ?? '') : '',",
          '  );',
          '  const [models, setModels] = useState<string[]>([]);',
          '  useEffect(() => {',
          "    if (dialog.type !== 'dot') return;",
          '    let active = true;',
          "    void api<{ models: string[] }>('/models', 'GET')",
          '      .then((result) => {',
          '        if (active && Array.isArray(result.models)) setModels(result.models);',
          '      })',
          '      .catch(() => {});',
          '    return () => {',
          '      active = false;',
          '    };',
          '  }, [dialog.type]);',
        ].join('\n'),
      },
      {
        label: 'dot body model',
        find: [
          '                learningContainerId: learningContainer.trim() || null,',
          '                skillDeliveryEnabled: skillDelivery,',
          '              };',
        ].join('\n'),
        replace: [
          '                learningContainerId: learningContainer.trim() || null,',
          '                skillDeliveryEnabled: skillDelivery,',
          '                model: model.trim() || null,',
          '              };',
        ].join('\n'),
      },
      {
        label: 'Model fieldset',
        find: [
          "          {dialog.type === 'dot' && (",
          '            <fieldset className="space-access-fields">',
          '              <legend>Automatic Learning</legend>',
        ].join('\n'),
        replace: [
          "          {dialog.type === 'dot' && (",
          '            <fieldset className="space-access-fields">',
          '              <legend>Model</legend>',
          '              <p className="muted">',
          '                Choose the language model for this Dot. Workspace default',
          '                uses the server-wide model from the app environment.',
          '              </p>',
          '              <select',
          '                value={model}',
          '                onChange={(event) => setModel(event.target.value)}',
          '              >',
          '                <option value="">Workspace default</option>',
          '                {[...new Set([...models, ...(model ? [model] : [])])].map(',
          '                  (id) => (',
          '                    <option key={id} value={id}>',
          '                      {id}',
          '                    </option>',
          '                  ),',
          '                )}',
          '              </select>',
          '            </fieldset>',
          '          )}',
          "          {dialog.type === 'dot' && (",
          '            <fieldset className="space-access-fields">',
          '              <legend>Automatic Learning</legend>',
        ].join('\n'),
      },
    ],
  },
];

for (const group of groups) {
  let text = readFileSync(group.path, 'utf8');
  if (text.includes(group.sentinel)) {
    console.log(`[dot-model-patch] already applied: ${group.path}`);
    continue;
  }
  for (const { label, find, replace } of group.edits) {
    const matches = text.split(find).length - 1;
    if (matches !== 1) {
      console.error(
        `[dot-model-patch] ERROR: anchor "${label}" matched ${matches} times in ${group.path} (expected 1); pinned source changed`,
      );
      process.exit(1);
    }
    text = text.replace(find, replace);
  }
  writeFileSync(group.path, text);
  console.log(`[dot-model-patch] patched: ${group.path}`);
}
console.log('[dot-model-patch] done');
