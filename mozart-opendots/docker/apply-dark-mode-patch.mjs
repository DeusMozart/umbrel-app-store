#!/usr/bin/env node
/**
 * apply-dark-mode-patch.mjs — adds a dark theme to OpenDots.
 *
 * Runs FIRST in the build chain (it reads the raw pinned stylesheets and
 * verifies them by sha256; if upstream moves, it fails loudly).
 *
 * What it does (all idempotent):
 *   1. Generates a dark override layer from style.css + editor.css (every
 *      background / color / border / box-shadow color is mapped to a dark
 *      equivalent, scoped entirely under html[data-theme='dark']).
 *   2. Appends that layer to src/client/style.css.
 *   3. Adds src/client/theme.ts (mode storage + apply + system listener).
 *   4. Adds src/client/ThemeToggle.tsx (sidebar control: system -> light -> dark).
 *   5. Wires main.tsx (apply theme before first paint) and App.tsx (toggle in
 *      the sidebar bottom).
 *
 * The mapping is a pure function of the pinned stylesheets; light mode is
 * untouched because every generated rule is scoped to html[data-theme='dark'].
 */
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { createHash } from 'node:crypto';

const STYLE = 'src/client/style.css';
const EDITOR = 'src/client/editor.css';
const MAIN = 'src/client/main.tsx';
const APP = 'src/client/App.tsx';
const THEME = 'src/client/theme.ts';
const TOGGLE = 'src/client/ThemeToggle.tsx';

// sha256 of the pinned stylesheets (commit c2569bb6a13a22e565cf3eb791c62267d06babb1).
const EXPECT_STYLE =
  '1711b4ee56a6fb5ffd0bf4dfdbe8d441d81e29feee4c09736d94e0dc5a03443e';
const EXPECT_EDITOR =
  'b42fa8209ddcf83249fa246e39b213aa553ad6995ac171e65da304995c497eae';

const MARKER = "/* ===== OpenDots dark theme";

const sha = (p) => createHash('sha256').update(readFileSync(p)).digest('hex');

/* ---------- color parsing / mapping ---------- */

function parseColor(t) {
  t = t.trim();
  if (t.startsWith('#')) {
    const h = t.slice(1);
    if (h.length === 3) {
      const [r, g, b] = [...h].map((c) => parseInt(c + c, 16));
      return [r, g, b, 1];
    }
    if (h.length === 6) {
      return [
        parseInt(h.slice(0, 2), 16),
        parseInt(h.slice(2, 4), 16),
        parseInt(h.slice(4, 6), 16),
        1,
      ];
    }
    if (h.length === 8) {
      return [
        parseInt(h.slice(0, 2), 16),
        parseInt(h.slice(2, 4), 16),
        parseInt(h.slice(4, 6), 16),
        parseInt(h.slice(6, 8), 16) / 255,
      ];
    }
    return null;
  }
  const m = t.match(/^rgba?\(([^)]*)\)$/);
  if (m) {
    const parts = m[1].split(/[,\s/]+/).filter(Boolean);
    if (parts.length < 3) return null;
    const comp = (p) => (p.endsWith('%') ? parseFloat(p) * 2.55 : parseFloat(p));
    const [r, g, b] = parts.slice(0, 3).map(comp);
    if ([r, g, b].some((v) => Number.isNaN(v))) return null;
    let a = 1;
    if (parts.length > 3) {
      a = parts[3].endsWith('%')
        ? parseFloat(parts[3]) / 100
        : parseFloat(parts[3]);
      if (Number.isNaN(a)) a = 1;
    }
    return [r, g, b, a];
  }
  return null;
}

const fmt = (r, g, b, a = 1) => {
  if (a >= 0.999) {
    const hx = (v) => Math.round(v).toString(16).padStart(2, '0');
    return '#' + hx(r) + hx(g) + hx(b);
  }
  const trimmed = parseFloat(a.toFixed(2));
  return `rgba(${Math.round(r)},${Math.round(g)},${Math.round(b)},${trimmed})`;
};

function rgbToHsl(r, g, b) {
  r /= 255;
  g /= 255;
  b /= 255;
  const mx = Math.max(r, g, b);
  const mn = Math.min(r, g, b);
  const l = (mx + mn) / 2;
  if (mx === mn) return [0, 0, l];
  const d = mx - mn;
  const s = l > 0.5 ? d / (2 - mx - mn) : d / (mx + mn);
  let h;
  if (mx === r) h = ((g - b) / d + (g < b ? 6 : 0)) / 6;
  else if (mx === g) h = ((b - r) / d + 2) / 6;
  else h = ((r - g) / d + 4) / 6;
  return [h, s, l];
}

function hslToRgb(h, s, l) {
  if (s === 0) {
    const v = l * 255;
    return [v, v, v];
  }
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  const hue = (t) => {
    if (t < 0) t += 1;
    if (t > 1) t -= 1;
    if (t < 1 / 6) return p + (q - p) * 6 * t;
    if (t < 1 / 2) return q;
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
    return p;
  };
  return [hue(h + 1 / 3) * 255, hue(h) * 255, hue(h - 1 / 3) * 255];
}

function toDark(token, kind) {
  const pc = parseColor(token);
  if (!pc) return null;
  const [r, g, b, a] = pc;
  const R = r / 255,
    G = g / 255,
    B = b / 255;
  const L = 0.2126 * R + 0.7152 * G + 0.0722 * B;
  const mx = Math.max(R, G, B),
    mn = Math.min(R, G, B);
  const S = mx === 0 ? 0 : (mx - mn) / mx;

  // faint tints (low alpha)
  if (a < 0.35) {
    if (kind === 'shadow')
      return L > 0.5 ? null : fmt(0, 0, 0, Math.min(0.8, a * 1.7));
    if (kind === 'bg')
      return L < 0.5 ? fmt(0, 0, 0, Math.min(0.55, a * 1.6)) : null;
    if (kind === 'border')
      return L < 0.5
        ? fmt(255, 255, 255, Math.max(0.06, Math.min(0.28, a * 1.1)))
        : null;
    if (kind === 'fg') return L < 0.5 ? fmt(236, 236, 239, a) : null;
    return null;
  }

  if (S < 0.12) {
    // grayscale
    if (kind === 'bg') {
      if (L >= 0.995) return '#1d1e23'; // pure white -> raised surface
      if (L >= 0.94) return '#1a1b20'; // near-white / warm chrome
      if (L >= 0.87) return '#26272d';
      if (L >= 0.45) return '#34353c';
      if (L >= 0.18) return '#2a2b31';
      return '#2f3037'; // near-black bg -> raised dark chip
    }
    if (kind === 'border') {
      if (L >= 0.85) return '#34353c';
      if (L >= 0.6) return '#3a3b42';
      return '#4a4b55';
    }
    if (kind === 'fg') {
      if (L <= 0.3) return '#ececef';
      if (L <= 0.55) return '#c2c3ca';
      if (L <= 0.75) return '#9a9ca4';
      return null;
    }
    if (kind === 'shadow')
      return a < 1 ? fmt(0, 0, 0, Math.min(0.85, 0.25 + a * 0.7)) : '#000';
    return null;
  }

  // chromatic
  const [h, s, l] = rgbToHsl(r, g, b);
  if (kind === 'fg') {
    const [nr, ng, nb] = hslToRgb(h, Math.min(s, 0.8), Math.max(l, 0.68));
    return fmt(nr, ng, nb, a);
  }
  if (kind === 'shadow')
    return a < 1 ? fmt(0, 0, 0, Math.min(0.85, a * 1.2)) : null;
  if (kind === 'border') {
    if (l > 0.7) return null; // light accent borders read fine on dark
    const [nr, ng, nb] = hslToRgb(h, Math.min(s, 0.6), 0.34);
    return fmt(nr, ng, nb, a);
  }
  // bg
  let l2, s2;
  if (l > 0.7) {
    l2 = 0.16;
    s2 = Math.min(s, 0.42);
  } else if (l > 0.4) {
    l2 = 0.3;
    s2 = Math.min(s, 0.55);
  } else {
    l2 = Math.min(l * 1.3 + 0.06, 0.44);
    s2 = Math.min(s, 0.65);
  }
  const [nr, ng, nb] = hslToRgb(h, s2, l2);
  return fmt(nr, ng, nb, a);
}

/* ---------- css parsing ---------- */

const stripComments = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '');

function matchBrace(s, i) {
  let depth = 0;
  for (let j = i; j < s.length; j++) {
    if (s[j] === '{') depth++;
    else if (s[j] === '}') {
      depth--;
      if (depth === 0) return j;
    }
  }
  throw new Error('unbalanced braces in stylesheet');
}

function parseCss(css, media = null, out = []) {
  let i = 0;
  for (;;) {
    const j = css.indexOf('{', i);
    if (j === -1) break;
    const prelude = css.slice(i, j).trim();
    const k = matchBrace(css, j);
    const body = css.slice(j + 1, k);
    if (prelude.startsWith('@media') || prelude.startsWith('@supports')) {
      parseCss(body, media ? media + ' ' + prelude : prelude, out);
    } else if (!prelude.startsWith('@')) {
      out.push([media, prelude, body]);
    }
    i = k + 1;
  }
  return out;
}

const COL = /#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)/g;

const KIND_OF = {
  background: 'bg',
  'background-color': 'bg',
  color: 'fg',
  border: 'border',
  'border-color': 'border',
  'border-top': 'border',
  'border-right': 'border',
  'border-bottom': 'border',
  'border-left': 'border',
  outline: 'border',
  'outline-color': 'border',
  'box-shadow': 'shadow',
  fill: 'fg',
  stroke: 'fg',
};

function scopeSelector(sel) {
  // split on top-level commas only (paren-safe)
  const parts = [];
  let depth = 0,
    cur = '';
  for (const ch of sel) {
    if (ch === '(') depth++;
    if (ch === ')') depth--;
    if (ch === ',' && depth === 0) {
      parts.push(cur);
      cur = '';
    } else cur += ch;
  }
  parts.push(cur);
  return parts
    .map((p) => p.trim())
    .filter(Boolean)
    .map((p) => "html[data-theme='dark'] " + p)
    .join(', ');
}

function generateDarkLayer() {
  const rules = [];
  const counts = { bg: 0, fg: 0, border: 0, shadow: 0 };
  for (const file of [STYLE, EDITOR]) {
    const parsed = parseCss(stripComments(readFileSync(file, 'utf8')));
    for (const [media, sel, body] of parsed) {
      if ([':root', 'html', 'body'].includes(sel.trim())) continue;
      const decls = [];
      for (const decl of body.split(';')) {
        const idx = decl.indexOf(':');
        if (idx === -1) continue;
        const prop = decl.slice(0, idx).trim().toLowerCase();
        const val = decl.slice(idx + 1).trim();
        const kind = KIND_OF[prop];
        if (!kind || !val || val.includes('var(')) continue;
        const important = val.includes('!important');
        const clean = val.replace('!important', '').trim();
        const tokens = clean.match(COL);
        if (!tokens) continue;
        let next = clean;
        let changed = false;
        for (const t of tokens) {
          const mapped = toDark(t, kind);
          if (!mapped || mapped.toLowerCase() === t.toLowerCase()) continue;
          next = next.split(t).join(mapped);
          changed = true;
          counts[kind]++;
        }
        if (changed) decls.push(`${prop}: ${next}${important ? ' !important' : ''};`);
      }
      if (decls.length) rules.push([media, sel, decls]);
    }
  }
  const lines = [];
  lines.push(MARKER + ' =====');
  lines.push('   Added by the mozart-opendots build (apply-dark-mode-patch.mjs).');
  lines.push("   Scoped entirely under html[data-theme='dark']; light mode is untouched.");
  lines.push('   Generated from the pinned stylesheets — see the patch script. */');
  lines.push("html[data-theme='dark'] {");
  lines.push('  color-scheme: dark;');
  lines.push('  --ink: #ececef;');
  lines.push('  --muted: #9a9ca4;');
  lines.push('  --line: #34353c;');
  lines.push('  --blue: #e8e9ed;');
  lines.push('  --blue-dark: #d8d9de;');
  lines.push('  --lavender: #26272d;');
  lines.push('  color: #ececef;');
  lines.push('  background: #17181b;');
  lines.push('}');
  for (const [media, sel, decls] of rules) {
    if (media) {
      lines.push(media + ' {');
      lines.push('  ' + scopeSelector(sel) + ' {');
      for (const d of decls) lines.push('    ' + d);
      lines.push('  }');
      lines.push('}');
    } else {
      lines.push(scopeSelector(sel) + ' {');
      for (const d of decls) lines.push('  ' + d);
      lines.push('}');
    }
  }
  return { css: lines.join('\n') + '\n', ruleCount: rules.length, counts };
}

/* ---------- file patching ---------- */

function patchFile(path, checks) {
  let text = readFileSync(path, 'utf8');
  let touched = false;
  for (const [marker, anchor, replacement] of checks) {
    if (text.includes(marker)) continue; // already applied
    const first = text.indexOf(anchor);
    if (first === -1)
      throw new Error(`Pinned OpenDots source contract changed at: ${path} (${anchor.slice(0, 60)})`);
    if (text.indexOf(anchor, first + 1) !== -1)
      throw new Error(`Ambiguous anchor in ${path}: ${anchor.slice(0, 60)}`);
    text = text.replace(anchor, replacement);
    touched = true;
  }
  if (touched) writeFileSync(path, text);
  return touched;
}

/* ---------- run ---------- */

// 1. CSS layer
if (readFileSync(STYLE, 'utf8').includes(MARKER)) {
  console.log('[dark-mode-patch] css layer already applied');
} else {
  if (sha(STYLE) !== EXPECT_STYLE)
    throw new Error('Pinned OpenDots source contract changed at: style.css (sha256 mismatch)');
  if (sha(EDITOR) !== EXPECT_EDITOR)
    throw new Error('Pinned OpenDots source contract changed at: editor.css (sha256 mismatch)');
  const { css, ruleCount, counts } = generateDarkLayer();
  writeFileSync(STYLE, readFileSync(STYLE, 'utf8') + '\n' + css);
  console.log(
    `[dark-mode-patch] appended ${ruleCount} dark rules ` +
      `(bg:${counts.bg} fg:${counts.fg} border:${counts.border} shadow:${counts.shadow})`,
  );
}

// 2. theme.ts
if (existsSync(THEME)) {
  console.log('[dark-mode-patch] theme.ts already present');
} else {
  writeFileSync(
    THEME,
    `// Theme handling for OpenDots (added by the mozart-opendots build patch).
export type ThemeMode = 'system' | 'light' | 'dark';

const KEY = 'opendots-theme';

export function storedMode(): ThemeMode {
  try {
    const value = localStorage.getItem(KEY);
    if (value === 'light' || value === 'dark') return value;
  } catch {
    /* storage unavailable */
  }
  return 'system';
}

export function resolved(mode: ThemeMode): 'light' | 'dark' {
  if (mode === 'light' || mode === 'dark') return mode;
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches
    ? 'dark'
    : 'light';
}

export function apply(mode: ThemeMode): void {
  const theme = resolved(mode);
  document.documentElement.dataset.theme = theme;
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute('content', theme === 'dark' ? '#17181b' : '#ffffff');
}

export function setMode(mode: ThemeMode): void {
  try {
    if (mode === 'system') localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, mode);
  } catch {
    /* storage unavailable */
  }
  apply(mode);
}

// Apply before first paint (this module is imported by main.tsx) and follow
// live system changes while no explicit choice is stored.
apply(storedMode());
window
  .matchMedia?.('(prefers-color-scheme: dark)')
  .addEventListener?.('change', () => {
    if (storedMode() === 'system') apply('system');
  });
`,
  );
  console.log('[dark-mode-patch] wrote theme.ts');
}

// 3. ThemeToggle.tsx
if (existsSync(TOGGLE)) {
  console.log('[dark-mode-patch] ThemeToggle.tsx already present');
} else {
  writeFileSync(
    TOGGLE,
    `// Sidebar theme control (added by the mozart-opendots build patch).
import { useState } from 'react';
import { Moon, Sun } from 'lucide-react';
import { resolved, setMode, storedMode, type ThemeMode } from './theme';

const NEXT: Record<ThemeMode, ThemeMode> = {
  system: 'light',
  light: 'dark',
  dark: 'system',
};

export function ThemeToggle() {
  const [mode, setLocal] = useState<ThemeMode>(storedMode());
  const Icon = resolved(mode) === 'dark' ? Moon : Sun;
  return (
    <button
      className="nav-item"
      title={\`Theme: \${mode} — click to switch (system → light → dark)\`}
      onClick={() => {
        const next = NEXT[mode];
        setMode(next);
        setLocal(next);
      }}
    >
      <Icon size={17} />
      <span>Theme</span>
      <small>{mode}</small>
    </button>
  );
}
`,
  );
  console.log('[dark-mode-patch] wrote ThemeToggle.tsx');
}

// 4. main.tsx — import theme before render
patchFile(MAIN, [
  [
    "import './theme';",
    "import './editor.css';",
    "import './editor.css';\nimport './theme';",
  ],
]);

// 5. App.tsx — icons, import, sidebar control
patchFile(APP, [
  [
    '  Moon,\n  MoreHorizontal,',
    '  Monitor,\n  MoreHorizontal,',
    '  Monitor,\n  Moon,\n  MoreHorizontal,',
  ],
  [
    '  Sun,\n  Trash2,',
    '  Settings2,\n  Trash2,',
    '  Settings2,\n  Sun,\n  Trash2,',
  ],
  [
    "import { ThemeToggle } from './ThemeToggle';",
    "import { WorkspaceDialog, type Dialog } from './WorkspaceDialog';",
    "import { WorkspaceDialog, type Dialog } from './WorkspaceDialog';\nimport { ThemeToggle } from './ThemeToggle';",
  ],
  [
    '          <ThemeToggle />',
    '          <a\n            className="nav-item"\n            href="https://github.com/CopilotKit/OpenDots"',
    '          <ThemeToggle />\n          <a\n            className="nav-item"\n            href="https://github.com/CopilotKit/OpenDots"',
  ],
]);

console.log('[dark-mode-patch] done');
