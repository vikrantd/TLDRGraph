/* =============================================================
 * TLDRGraph canvas application.
 *
 * Sections:
 *   1. Constants, lookups and state
 *   2. Layout (tier 1 modules, tier 2 symbols)
 *   3. Markdown rendering
 *   4. Focus engine and visibility rules
 *   5. Camera, render loop and canvas drawing
 *   6. Hit testing, pan / drag / zoom interaction
 *   7. Inspector drawer
 *   8. Search, legend, keyboard shortcuts
 * ============================================================= */

'use strict';

// -------------------------------------------------------------
// 1. Constants, lookups and state
// -------------------------------------------------------------
const FALLBACK_COLOR = {
  color: '#94a3b8',
  border: '#475569',
  bg: 'rgba(148, 163, 184, 0.12)',
  glow: 'rgba(148, 163, 184, 0.28)',
  name: 'Slate'
};

const MODULE_W = 220;
const MODULE_H = 56;
const NODE_W = 208;
const NODE_H = 56;

// Expanded card: the focused node grows into a readable panel that carries its
// own documentation, inputs and outputs, so the canvas answers without the drawer.
const CARD_W = 460;
const CARD_PAD = 16;
const CARD_MAX_BODY_LINES = 16;
const CARD_CODE_LINES = 14;       // source preview lines drawn on the card itself
const CHIP_H = 18;                // chip height, shared by layout and hit testing
const CARD_FONTS = {
  kind: 'bold 9px monospace',
  title: 'bold 15px -apple-system, sans-serif',
  meta: '10px monospace',
  section: 'bold 9px -apple-system, sans-serif',
  body: '11.5px -apple-system, sans-serif',
  bodyBold: 'bold 11.5px -apple-system, sans-serif',
  code: '10.5px monospace',
  chip: '10px monospace'
};

const TIER_SWITCH_SCALE = 0.85;   // below this zoom we show modules, above it symbols
const FOCUS_MIN_SCALE = 0.85;     // never zoom a focused card below readable size
const MIN_SCALE = 0.12;
const MAX_SCALE = 3.2;
const DRAG_THRESHOLD = 4;         // px of pointer travel before a click becomes a drag
const VISIBLE_EPSILON = 0.05;     // render opacity under which an item is skipped entirely
const LERP = 0.2;                 // position easing
const FADE = 0.3;                 // opacity easing (a touch faster, so hidden nodes clear quickly)

const canvas = document.getElementById('main-canvas');
const ctx = canvas.getContext('2d');
const container = document.getElementById('canvas-container');
const tooltipEl = document.getElementById('canvas-tooltip');

let width = container.clientWidth || window.innerWidth;
let height = container.clientHeight || (window.innerHeight - 56);
let dpr = window.devicePixelRatio || 1;

// Camera: rendered values plus targets the render loop eases towards.
let panX = width / 2;
let panY = height / 2;
let scale = 0.65;
let tPanX = panX;
let tPanY = panY;
let tScale = scale;

let hideTests = true;
let showDeadOnly = false;
let selectedId = null;          // id shown in the inspector drawer
let hoveredId = null;
let hoveredChipId = null;       // contained-symbol chip under the cursor

function isDeadItem(item) {
  if (!item) return false;
  const s = item.dead_code_status;
  return s === 'candidate' || s === 'unreviewed' || (s && s !== 'live' && s !== 'entry_point' && s !== 'not_code');
}

function moduleHasDead(m) {
  if (!m) return false;
  return isDeadItem(m) || (m.subnodes || []).some(isDeadItem);
}

// Focus mode: only the focused node and its direct neighbours stay on canvas.
let focusId = null;
let focusKind = null;           // 'module' | 'node'
let focusUpstream = new Set();
let focusDownstream = new Set();
let focusVisible = new Set();   // focusId plus its neighbours

// Trace mode: transitive up/downstream closure of the selected node.
let traceActive = false;
let traceIds = new Set();
let traceKind = null;

const modulesById = {};
const nodesById = {};
const layerById = {};

DATA.layers.forEach(l => { layerById[l.id] = l; });
DATA.nodes.forEach(n => { nodesById[n.id] = n; });
DATA.modules.forEach(m => {
  modulesById[m.id] = m;
  // Modules ship their own copies of subnodes; re-point them at the canonical
  // node objects so position and opacity state is shared.
  m.subnodes = (m.subnodes || []).map(s => nodesById[s.id] || s);
});

function getItem(id) {
  return modulesById[id] || nodesById[id] || null;
}

function isModuleId(id) {
  return !!modulesById[id];
}

// -------------------------------------------------------------
// 2. Layout
// -------------------------------------------------------------
// Two independent layouts share the same canvas space:
//   - tier 1 packs module cards tightly into per-layer columns
//   - tier 2 gives every module enough vertical room for its symbol cluster
const modulesByLayer = {};
DATA.layers.forEach(l => { modulesByLayer[l.id] = []; });
DATA.modules.forEach(m => {
  (modulesByLayer[m.layer_id] = modulesByLayer[m.layer_id] || []).push(m);
});

const layerBounds = { 1: {}, 2: {} };

function layoutTier1() {
  const layerCount = Math.max(1, DATA.layers.length);
  const xSpacing = 560;
  const startX = -((layerCount - 1) * xSpacing) / 2;
  const gapY = 28;

  DATA.layers.forEach((layer, idx) => {
    const cx = startX + idx * xSpacing;
    const mods = modulesByLayer[layer.id] || [];
    const totalH = Math.max(0, mods.length * (MODULE_H + gapY) - gapY);
    const top = -totalH / 2;

    mods.forEach((m, mIdx) => {
      m.w = MODULE_W;
      m.h = MODULE_H;
      m.baseX = cx;
      m.baseY = top + mIdx * (MODULE_H + gapY) + MODULE_H / 2;
    });

    layerBounds[1][layer.id] = {
      minX: cx - MODULE_W / 2,
      maxX: cx + MODULE_W / 2,
      minY: top,
      maxY: top + totalH,
      color: layer.color,
      bg: layer.bg,
      name: layer.name,
      count: mods.length
    };
  });
}

function layoutTier2() {
  const layerCount = Math.max(1, DATA.layers.length);
  const xSpacing = 720;
  const startX = -((layerCount - 1) * xSpacing) / 2;
  const colGap = 16;
  const rowGap = 14;
  const clusterGap = 64;
  const clusterW = NODE_W * 2 + colGap;

  DATA.layers.forEach((layer, idx) => {
    const cx = startX + idx * xSpacing;
    const mods = modulesByLayer[layer.id] || [];

    let totalH = 0;
    mods.forEach(m => {
      const rows = Math.max(1, Math.ceil((m.subnodes || []).length / 2));
      m.clusterH = rows * (NODE_H + rowGap) - rowGap;
      totalH += m.clusterH + clusterGap;
    });
    totalH = Math.max(0, totalH - clusterGap);

    let cursorY = -totalH / 2;
    mods.forEach(m => {
      const clusterTop = cursorY;
      (m.subnodes || []).forEach((s, sIdx) => {
        const col = sIdx % 2;
        const row = Math.floor(sIdx / 2);
        s.w = NODE_W;
        s.h = NODE_H;
        s.baseX = cx + (col === 0 ? -(NODE_W + colGap) / 2 : (NODE_W + colGap) / 2);
        s.baseY = clusterTop + row * (NODE_H + rowGap) + NODE_H / 2;
      });
      cursorY += m.clusterH + clusterGap;
    });

    layerBounds[2][layer.id] = {
      minX: cx - clusterW / 2,
      maxX: cx + clusterW / 2,
      minY: -totalH / 2,
      maxY: totalH / 2,
      color: layer.color,
      bg: layer.bg,
      name: layer.name,
      count: mods.length
    };
  });
}

layoutTier1();
layoutTier2();

// Every drawable item carries render state; seed it from the base layout.
function seedRenderState(item) {
  item.w = item.w || NODE_W;
  item.h = item.h || NODE_H;
  // Collapsed footprint, restored whenever the item stops being the focus.
  item.baseW = item.w;
  item.baseH = item.h;
  item.expanded = null;
  item.baseX = Number.isFinite(item.baseX) ? item.baseX : 0;
  item.baseY = Number.isFinite(item.baseY) ? item.baseY : 0;
  item.focusX = null;
  item.focusY = null;
  item.targetX = item.baseX;
  item.targetY = item.baseY;
  item.renderX = item.baseX;
  item.renderY = item.baseY;
  item.targetOpacity = 1;
  item.renderOpacity = 1;
  item.dragging = false;
}

DATA.modules.forEach(seedRenderState);
DATA.nodes.forEach(seedRenderState);

// -------------------------------------------------------------
// 3. Markdown rendering
// -------------------------------------------------------------
// A small but genuinely block-aware renderer: fenced code, headings, nested
// lists, tables, quotes and rules all survive, and inline code is protected
// from the emphasis passes.
function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

const CODE_TOKEN_PREFIX = 'CCCODE';
const CODE_TOKEN_SUFFIX = '';

function renderInline(text) {
  const codeSpans = [];
  // Pull inline code out first so ** or _ inside it is never treated as markup.
  let s = String(text).replace(/(`+)([\s\S]*?)\1/g, (m, ticks, code) => {
    codeSpans.push(code.trim());
    return CODE_TOKEN_PREFIX + (codeSpans.length - 1) + CODE_TOKEN_SUFFIX;
  });

  s = escapeHtml(s);

  // Images degrade to their alt text: the page must stay dependency-free.
  s = s.replace(/!\[([^\]]*)\]\(([^)]*)\)/g, (m, alt) => alt || '');
  // Links render as styled text with the destination in a title attribute;
  // nothing in this standalone file should navigate anywhere.
  s = s.replace(/\[([^\]]+)\]\(([^)]*)\)/g, (m, label, href) =>
    '<span class="md-link" title="' + escapeHtml(href.trim()) + '">' + label + '</span>');

  s = s.replace(/\*\*\*([^*]+)\*\*\*/g, '<strong><em>$1</em></strong>');
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/(^|[\s(\[])__([^_]+)__(?=$|[\s).,!?\];:])/g, '$1<strong>$2</strong>');
  s = s.replace(/(^|[^*\w])\*([^*\n]+)\*(?![*\w])/g, '$1<em>$2</em>');
  // Underscore emphasis only at word boundaries, so snake_case_names survive.
  s = s.replace(/(^|[\s(\[])_([^_\n]+)_(?=$|[\s).,!?\];:])/g, '$1<em>$2</em>');
  s = s.replace(/~~([^~]+)~~/g, '<del>$1</del>');

  const tokenRe = new RegExp(CODE_TOKEN_PREFIX + '(\\d+)' + CODE_TOKEN_SUFFIX, 'g');
  return s.replace(tokenRe, (m, i) =>
    '<code class="md-code">' + escapeHtml(codeSpans[Number(i)]) + '</code>');
}

const RE_FENCE = /^\s{0,3}(```+|~~~+)\s*([\w+-]*)\s*$/;
const RE_HEADING = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const RE_RULE = /^\s{0,3}([-*_])(\s*\1){2,}\s*$/;
const RE_QUOTE = /^\s{0,3}>\s?(.*)$/;
const RE_LIST = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;

function isBlockStart(line) {
  return RE_FENCE.test(line) || RE_HEADING.test(line) || RE_RULE.test(line) ||
         RE_QUOTE.test(line) || RE_LIST.test(line) || line.trim() === '';
}

function isTableSeparator(line) {
  return line.indexOf('|') !== -1 &&
         /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line);
}

function splitTableRow(line) {
  return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(c => c.trim());
}

function renderList(lines, start) {
  const stack = [];
  let html = '';
  let i = start;

  const closeFrame = () => {
    const frame = stack.pop();
    html += '</li></' + frame.tag + '>';
  };

  while (i < lines.length) {
    const m = lines[i].match(RE_LIST);
    if (!m) {
      // Lazy continuation: plain text directly under an item belongs to it.
      if (stack.length && lines[i].trim() !== '' && !isBlockStart(lines[i])) {
        html += ' ' + renderInline(lines[i].trim());
        i++;
        continue;
      }
      // A single blank line between items does not end the list.
      if (stack.length && lines[i].trim() === '' && (i + 1) < lines.length && RE_LIST.test(lines[i + 1])) {
        i++;
        continue;
      }
      break;
    }

    const indent = m[1].replace(/\t/g, '    ').length;
    const ordered = /\d/.test(m[2]);
    const tag = ordered ? 'ol' : 'ul';
    const content = renderInline(m[3]);

    if (!stack.length) {
      stack.push({ indent: indent, tag: tag });
      html += '<' + tag + ' class="md-list"><li>' + content;
    } else if (indent > stack[stack.length - 1].indent + 1) {
      stack.push({ indent: indent, tag: tag });
      html += '<' + tag + ' class="md-list"><li>' + content;
    } else {
      while (stack.length > 1 && indent < stack[stack.length - 1].indent - 1) {
        closeFrame();
      }
      html += '</li><li>' + content;
    }
    i++;
  }

  while (stack.length) {
    closeFrame();
  }
  return { html: html, next: i };
}

function renderMarkdown(md) {
  if (md === undefined || md === null || String(md).trim() === '') {
    return '<p><em>No documentation recorded for this component.</em></p>';
  }

  const lines = String(md).replace(/\r\n?/g, '\n').split('\n');
  let html = '';
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    if (line.trim() === '') { i++; continue; }

    const fence = line.match(RE_FENCE);
    if (fence) {
      const closer = fence[1].charAt(0).repeat(3);
      const body = [];
      i++;
      while (i < lines.length && lines[i].trim().indexOf(closer) !== 0) {
        body.push(lines[i]);
        i++;
      }
      i++; // consume the closing fence (a missing one just ends the block)
      html += '<pre class="md-pre"><code>' + escapeHtml(body.join('\n')) + '</code></pre>';
      continue;
    }

    const heading = line.match(RE_HEADING);
    if (heading) {
      const level = Math.min(4, heading[1].length);
      html += '<div class="md-h' + level + '">' + renderInline(heading[2]) + '</div>';
      i++;
      continue;
    }

    if (RE_RULE.test(line)) {
      html += '<hr class="md-hr" />';
      i++;
      continue;
    }

    if (line.indexOf('|') !== -1 && (i + 1) < lines.length && isTableSeparator(lines[i + 1])) {
      const header = splitTableRow(line);
      i += 2;
      let table = '<table class="md-table"><thead><tr>' +
        header.map(c => '<th>' + renderInline(c) + '</th>').join('') +
        '</tr></thead><tbody>';
      while (i < lines.length && lines[i].indexOf('|') !== -1 && lines[i].trim() !== '') {
        table += '<tr>' + splitTableRow(lines[i]).map(c => '<td>' + renderInline(c) + '</td>').join('') + '</tr>';
        i++;
      }
      html += table + '</tbody></table>';
      continue;
    }

    if (RE_QUOTE.test(line)) {
      const body = [];
      while (i < lines.length && RE_QUOTE.test(lines[i])) {
        body.push(lines[i].match(RE_QUOTE)[1]);
        i++;
      }
      html += '<div class="md-quote">' + renderMarkdown(body.join('\n')) + '</div>';
      continue;
    }

    if (RE_LIST.test(line)) {
      const res = renderList(lines, i);
      html += res.html;
      i = res.next;
      continue;
    }

    const para = [];
    while (i < lines.length && lines[i].trim() !== '' && !isBlockStart(lines[i])) {
      para.push(lines[i].trim());
      i++;
    }
    if (para.length) {
      html += '<p>' + para.map(renderInline).join('<br/>') + '</p>';
    } else {
      i++; // defensive: never spin on a line no branch consumed
    }
  }

  return html || '<p><em>No documentation recorded for this component.</em></p>';
}

function markdownToPlainText(md, maxLen) {
  if (!md) return '';
  let text = String(md)
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/^\s{0,3}#{1,6}\s+/gm, '')
    .replace(/^\s{0,3}>\s?/gm, '')
    .replace(/^\s*([-*+]|\d+[.)])\s+/gm, '')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/\*([^*]+)\*/g, '$1')
    .replace(/\s+/g, ' ')
    .trim();
  const limit = maxLen || 200;
  if (text.length > limit) text = text.slice(0, limit - 1).trim() + '...';
  return text;
}

// -------------------------------------------------------------
// 3b. Source code: highlighting and file linkage
// -------------------------------------------------------------
const CODE_KEYWORDS = new Set([
  'abstract','and','as','assert','async','await','base','bool','break','byte','case','catch',
  'char','class','const','constructor','continue','crate','def','default','del','delete','do',
  'double','elif','else','enum','except','export','extends','extern','false','final','finally',
  'float','fn','for','from','func','function','global','go','goto','if','impl','implements',
  'import','in','instanceof','int','interface','is','lambda','let','long','match','mod','module',
  'mut','namespace','new','nil','none','not','null','or','package','pass','private','protected',
  'pub','public','raise','readonly','ref','return','select','self','static','string','struct',
  'super','switch','this','throw','throws','trait','true','try','type','typeof','undefined',
  'union','unsafe','use','using','var','void','when','where','while','with','yield'
]);

const HASH_COMMENT_LANGUAGES = new Set(['python', 'ruby', 'shell', 'yaml', 'toml', 'r', 'perl']);

const TRIPLE_DOUBLE = '"' + '""';
const TRIPLE_SINGLE = "'" + "''";

function escapeCode(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

/**
 * Line-by-line tokenizer that carries block state (triple-quoted strings and
 * C block comments) across lines, so every emitted line is self-contained HTML
 * and can be dropped straight into a numbered table row.
 */
function highlightCode(code, language) {
  const hashComments = HASH_COMMENT_LANGUAGES.has(language) || language === 'plain';
  const lines = String(code).split('\n');
  const out = [];
  let blockDelimiter = null;   // an open triple-quote or block comment

  lines.forEach(raw => {
    let html = '';
    let i = 0;

    while (i < raw.length) {
      if (blockDelimiter) {
        const close = raw.indexOf(blockDelimiter, i);
        const cls = blockDelimiter === '*/' ? 'tok-com' : 'tok-str';
        if (close === -1) {
          html += '<span class="' + cls + '">' + escapeCode(raw.slice(i)) + '</span>';
          i = raw.length;
        } else {
          const end = close + blockDelimiter.length;
          html += '<span class="' + cls + '">' + escapeCode(raw.slice(i, end)) + '</span>';
          i = end;
          blockDelimiter = null;
        }
        continue;
      }

      const rest = raw.slice(i);

      const triple = rest.indexOf(TRIPLE_DOUBLE) === 0 ? TRIPLE_DOUBLE
                   : rest.indexOf(TRIPLE_SINGLE) === 0 ? TRIPLE_SINGLE
                   : null;
      if (triple) {
        const close = rest.indexOf(triple, triple.length);
        if (close === -1) {
          blockDelimiter = triple;
          html += '<span class="tok-str">' + escapeCode(rest) + '</span>';
          i = raw.length;
        } else {
          const end = close + triple.length;
          html += '<span class="tok-str">' + escapeCode(rest.slice(0, end)) + '</span>';
          i += end;
        }
        continue;
      }

      if (rest.indexOf('/*') === 0) {
        const close = rest.indexOf('*/', 2);
        if (close === -1) {
          blockDelimiter = '*/';
          html += '<span class="tok-com">' + escapeCode(rest) + '</span>';
          i = raw.length;
        } else {
          html += '<span class="tok-com">' + escapeCode(rest.slice(0, close + 2)) + '</span>';
          i += close + 2;
        }
        continue;
      }

      if (rest.indexOf('//') === 0 || (hashComments && rest.charAt(0) === '#')) {
        html += '<span class="tok-com">' + escapeCode(rest) + '</span>';
        i = raw.length;
        continue;
      }

      const strMatch = rest.match(/^[bruf]{0,2}(["'`])(?:\\.|(?!\1)[^\\])*\1?/i);
      if (strMatch) {
        html += '<span class="tok-str">' + escapeCode(strMatch[0]) + '</span>';
        i += strMatch[0].length;
        continue;
      }

      const numMatch = rest.match(/^\d[\d_]*(\.\d+)?([eE][+-]?\d+)?/);
      if (numMatch) {
        html += '<span class="tok-num">' + escapeCode(numMatch[0]) + '</span>';
        i += numMatch[0].length;
        continue;
      }

      const wordMatch = rest.match(/^[A-Za-z_$][\w$]*/);
      if (wordMatch) {
        const word = wordMatch[0];
        const after = rest.slice(word.length);
        if (CODE_KEYWORDS.has(word)) {
          html += '<span class="tok-key">' + word + '</span>';
        } else if (/^\s*\(/.test(after)) {
          html += '<span class="tok-fn">' + word + '</span>';
        } else {
          html += escapeCode(word);
        }
        i += word.length;
        continue;
      }

      html += escapeCode(raw.charAt(i));
      i++;
    }

    out.push(html);
  });

  return out;
}

/** Absolute path of an item, for editor links and clipboard copies. */
function absolutePath(item) {
  const root = (DATA.root || '').replace(/\/$/, '');
  if (!item || !item.path) return root;
  return root ? root + '/' + item.path : item.path;
}

/** Editor deep link - VS Code and its forks register this scheme. */
function editorLink(item) {
  const line = (item && item.code_start) ? item.code_start : 1;
  return 'vscode://file' + absolutePath(item) + ':' + line;
}

function copyToClipboard(text, button) {
  const confirmCopy = () => {
    if (!button) return;
    const original = button.getAttribute('data-label') || button.textContent;
    button.setAttribute('data-label', original);
    button.textContent = 'Copied';
    setTimeout(() => { button.textContent = original; }, 1200);
  };

  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(confirmCopy, () => {});
    return;
  }
  const helper = document.createElement('textarea');
  helper.value = text;
  document.body.appendChild(helper);
  helper.select();
  try { document.execCommand('copy'); confirmCopy(); } catch (err) { /* clipboard unavailable */ }
  document.body.removeChild(helper);
}

/**
 * Other symbols declared in the same file, ordered by line. Modules already
 * list their contents in their own section, so this is for symbols only.
 */
function siblingsOf(item) {
  if (isModuleId(item.id)) return [];
  const mod = modulesById[item.module_id];
  if (!mod) return [];
  return (mod.subnodes || [])
    .filter(s => s.id !== item.id)
    .slice()
    .sort((a, b) => (a.code_start || 0) - (b.code_start || 0));
}

// -------------------------------------------------------------
// 4. Focus engine and visibility rules
// -------------------------------------------------------------
function getActiveTier() {
  if (focusKind === 'module') return 1;
  if (focusKind === 'node') return 2;
  if (traceActive && traceKind) return traceKind === 'module' ? 1 : 2;
  return scale < TIER_SWITCH_SCALE ? 1 : 2;
}

function neighboursOf(id) {
  const up = [];
  const down = [];
  const mod = modulesById[id];
  if (mod) {
    (mod.inbound_modules || []).forEach(mid => { if (modulesById[mid]) up.push(modulesById[mid]); });
    (mod.outbound_modules || []).forEach(mid => { if (modulesById[mid]) down.push(modulesById[mid]); });
    return { up: up, down: down };
  }
  const node = nodesById[id];
  if (node) {
    const seenUp = new Set();
    const seenDown = new Set();
    (node.inbound || []).forEach(c => {
      const n = nodesById[c.source_id];
      if (n && n.id !== id && !seenUp.has(n.id)) { seenUp.add(n.id); up.push(n); }
    });
    (node.outbound || []).forEach(c => {
      const n = nodesById[c.target_id];
      if (n && n.id !== id && !seenDown.has(n.id)) { seenDown.add(n.id); down.push(n); }
    });
  }
  return { up: up, down: down };
}

/** Lays the focused node out in the centre with callers left and callees right. */
function applyFocusLayout() {
  focusUpstream.clear();
  focusDownstream.clear();
  focusVisible.clear();

  DATA.modules.forEach(collapseItem);
  DATA.nodes.forEach(collapseItem);

  if (!focusId) return;

  const target = getItem(focusId);
  if (!target) return;

  const rel = neighboursOf(focusId);

  // The focused card grows to hold its readme, inputs and outputs.
  target.expanded = buildExpandedCard(target);
  target.w = target.expanded.w;
  target.h = target.expanded.h;

  target.focusX = 0;
  target.focusY = 0;
  focusVisible.add(target.id);

  const place = (list, sign, bucket) => {
    if (!list.length) return;
    // Long fan-outs wrap into extra columns instead of one unreadable strip.
    const perColumn = Math.max(1, Math.min(list.length, Math.ceil(Math.sqrt(list.length) * 2)));
    const rowGap = (list[0].h || NODE_H) + 26;
    list.forEach((item, idx) => {
      const col = Math.floor(idx / perColumn);
      const rowIdx = idx % perColumn;
      const rows = Math.min(perColumn, list.length - col * perColumn);
      item.focusX = sign * (target.w / 2 + 150 + item.w / 2 + col * (item.w + 70));
      item.focusY = (rowIdx - (rows - 1) / 2) * rowGap;
      bucket.add(item.id);
      focusVisible.add(item.id);
    });
  };

  place(rel.up, -1, focusUpstream);
  place(rel.down, 1, focusDownstream);
}

function enterFocus(id) {
  const item = getItem(id);
  if (!item) return;
  focusId = id;
  focusKind = isModuleId(id) ? 'module' : 'node';
  traceActive = false;
  traceIds.clear();
  const traceBtn = document.getElementById('btn-trace-flow');
  if (traceBtn) traceBtn.classList.remove('active');
  applyFocusLayout();
  updateFocusBanner();
  updateHud();
  fitToVisible(true);

  // Pull the source in the background; the card rebuilds when it lands.
  if (item.path && !cachedSymbolSource(item)) {
    loadSymbolSource(item).then(() => {
      if (focusId === id) {
        applyFocusLayout();
        fitToVisible(true);
      }
    });
  }
}

function exitFocus(options) {
  const keepDrawer = options && options.keepDrawer;
  focusId = null;
  focusKind = null;
  focusUpstream.clear();
  focusDownstream.clear();
  focusVisible.clear();
  traceActive = false;
  traceIds.clear();
  DATA.modules.forEach(collapseItem);
  DATA.nodes.forEach(collapseItem);
  hoveredChipId = null;
  const traceBtn = document.getElementById('btn-trace-flow');
  if (traceBtn) traceBtn.classList.remove('active');
  updateFocusBanner();
  updateHud();
  if (!keepDrawer) closeDrawer();
}

function updateFocusBanner() {
  const banner = document.getElementById('focus-banner');
  const text = document.getElementById('focus-banner-text');
  if (focusId) {
    const item = getItem(focusId);
    const name = item ? (item.display_label || item.label) : focusId;
    const kind = focusKind === 'module' ? 'module' : 'symbol';
    text.textContent = 'Focused ' + kind + ': ' + name + ' - ' +
      focusUpstream.size + ' in / ' + focusDownstream.size + ' out';
    banner.classList.add('visible');
  } else if (traceActive) {
    text.textContent = 'Tracing flow - ' + traceIds.size + ' connected nodes';
    banner.classList.add('visible');
  } else {
    banner.classList.remove('visible');
  }
}

/** Single source of truth for what belongs on the canvas right now. */
function isVisible(item, isModule) {
  if (hideTests && item.is_test) return false;
  if (showDeadOnly) {
    if (isModule) {
      if (!moduleHasDead(item)) return false;
    } else {
      if (!isDeadItem(item)) return false;
    }
  }
  const tier = getActiveTier();
  if (isModule !== (tier === 1)) return false;
  if (focusId) return focusVisible.has(item.id);
  if (traceActive) return traceIds.has(item.id);
  return true;
}

// -------------------------------------------------------------
// 5. Camera, render loop and canvas drawing
// -------------------------------------------------------------
function setCamera(nextPanX, nextPanY, nextScale, animate) {
  tPanX = nextPanX;
  tPanY = nextPanY;
  tScale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, nextScale));
  if (!animate) {
    panX = tPanX;
    panY = tPanY;
    scale = tScale;
  }
  updateHud();
}

function screenToWorld(sx, sy) {
  return { x: (sx - panX) / scale, y: (sy - panY) / scale };
}

function drawArrow(fromX, fromY, toX, toY, color, size) {
  const angle = Math.atan2(toY - fromY, toX - fromX);
  ctx.beginPath();
  ctx.moveTo(toX, toY);
  ctx.lineTo(toX - size * Math.cos(angle - Math.PI / 7), toY - size * Math.sin(angle - Math.PI / 7));
  ctx.lineTo(toX - size * Math.cos(angle + Math.PI / 7), toY - size * Math.sin(angle + Math.PI / 7));
  ctx.closePath();
  ctx.fillStyle = color;
  ctx.fill();
}

function roundRect(x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

function truncateText(str, maxW) {
  const text = String(str === undefined || str === null ? '' : str);
  if (ctx.measureText(text).width <= maxW) return text;
  let s = text;
  while (s.length > 1 && ctx.measureText(s + '...').width > maxW) {
    s = s.slice(0, -1);
  }
  return s + '...';
}

function baseName(path) {
  if (!path) return '';
  const parts = String(path).split('/');
  return parts[parts.length - 1];
}

function stepItem(item, isModule) {
  item.targetOpacity = isVisible(item, isModule) ? 1 : 0;

  if (!item.dragging) {
    const useFocus = focusId && item.focusX !== null && item.focusX !== undefined;
    item.targetX = useFocus ? item.focusX : item.baseX;
    item.targetY = useFocus ? item.focusY : item.baseY;
  }

  item.renderX += (item.targetX - item.renderX) * LERP;
  item.renderY += (item.targetY - item.renderY) * LERP;
  item.renderOpacity += (item.targetOpacity - item.renderOpacity) * FADE;
  // Snap the tail of the fade so hidden nodes never linger as ghosts.
  if (Math.abs(item.targetOpacity - item.renderOpacity) < 0.06) {
    item.renderOpacity = item.targetOpacity;
  }
}

function renderLoop() {
  DATA.modules.forEach(m => stepItem(m, true));
  DATA.nodes.forEach(n => stepItem(n, false));

  panX += (tPanX - panX) * LERP;
  panY += (tPanY - panY) * LERP;
  const prevScale = scale;
  scale += (tScale - scale) * LERP;
  if (Math.abs(tScale - scale) < 0.0005) scale = tScale;
  if (scale !== prevScale) updateHud();

  drawCanvas();
  requestAnimationFrame(renderLoop);
}

function inViewport(item) {
  const margin = 140;
  const sx = item.renderX * scale + panX;
  const sy = item.renderY * scale + panY;
  const halfW = (item.w / 2) * scale + margin;
  const halfH = (item.h / 2) * scale + margin;
  return sx + halfW >= 0 && sx - halfW <= width && sy + halfH >= 0 && sy - halfH <= height;
}

function drawLayerHalos(tier) {
  const bounds = layerBounds[tier] || {};
  Object.keys(bounds).forEach(lid => {
    const b = bounds[lid];
    if (!b || !b.count) return;
    const mods = (modulesByLayer[lid] || []).filter(m => {
      if (hideTests && m.is_test) return false;
      if (showDeadOnly && !moduleHasDead(m)) return false;
      return true;
    });
    if (!mods.length) return;

    const padX = 90;
    const padY = 70;
    const x = b.minX - padX;
    const y = b.minY - padY;
    const w = (b.maxX - b.minX) + padX * 2;
    const h = (b.maxY - b.minY) + padY * 2;

    const grad = ctx.createLinearGradient(x, y, x, y + h);
    grad.addColorStop(0, b.bg || FALLBACK_COLOR.bg);
    grad.addColorStop(0.5, 'rgba(255,255,255,0.015)');
    grad.addColorStop(1, b.bg || FALLBACK_COLOR.bg);

    ctx.save();
    ctx.globalAlpha = 0.55;
    roundRect(x, y, w, h, 28);
    ctx.fillStyle = grad;
    ctx.fill();
    ctx.strokeStyle = b.color;
    ctx.globalAlpha = 0.18;
    ctx.lineWidth = 1.5;
    ctx.stroke();
    ctx.restore();

    ctx.save();
    ctx.globalAlpha = 0.45;
    ctx.fillStyle = b.color;
    ctx.font = 'bold 20px -apple-system, sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'alphabetic';
    ctx.fillText(String(b.name).toUpperCase(), (b.minX + b.maxX) / 2, y + 34);
    ctx.restore();
  });
}

function drawEdge(src, tgt, opts) {
  const startX = src.renderX + src.w / 2;
  const startY = src.renderY;
  const endX = tgt.renderX - tgt.w / 2;
  const endY = tgt.renderY;
  const dx = endX - startX;
  const bend = Math.max(35, Math.abs(dx) * 0.45);
  const cp1x = startX + bend;
  const cp2x = endX - bend;

  ctx.beginPath();
  ctx.moveTo(startX, startY);
  ctx.bezierCurveTo(cp1x, startY, cp2x, endY, endX, endY);
  ctx.strokeStyle = opts.color;
  ctx.lineWidth = opts.width;
  ctx.globalAlpha = opts.alpha;
  if (opts.glow) {
    ctx.shadowColor = opts.color;
    ctx.shadowBlur = 8;
  }
  ctx.stroke();
  ctx.shadowBlur = 0;
  drawArrow(cp2x, endY, endX, endY, opts.color, opts.head);
  ctx.globalAlpha = 1;
}

function isHighlighted(a, b) {
  return (focusId && (a.id === focusId || b.id === focusId)) ||
         (selectedId && (a.id === selectedId || b.id === selectedId)) ||
         (hoveredId && (a.id === hoveredId || b.id === hoveredId));
}

function drawModuleEdges() {
  DATA.module_edges.forEach(e => {
    const src = modulesById[e.source];
    const tgt = modulesById[e.target];
    if (!src || !tgt) return;
    // An edge is only meaningful when both endpoints are actually on screen.
    const alpha = Math.min(src.renderOpacity, tgt.renderOpacity);
    if (alpha <= VISIBLE_EPSILON) return;

    const highlight = isHighlighted(src, tgt);
    drawEdge(src, tgt, {
      color: highlight ? '#38bdf8' : 'rgba(56, 189, 248, 0.5)',
      width: highlight ? 3.2 : 1.6,
      alpha: highlight ? alpha : alpha * 0.7,
      head: highlight ? 9 : 6,
      glow: highlight
    });
  });
}

function drawChildEdges() {
  DATA.child_edges.forEach(e => {
    const src = nodesById[e.source];
    const tgt = nodesById[e.target];
    if (!src || !tgt) return;
    const alpha = Math.min(src.renderOpacity, tgt.renderOpacity);
    if (alpha <= VISIBLE_EPSILON) return;

    const highlight = isHighlighted(src, tgt);
    drawEdge(src, tgt, {
      color: highlight ? '#38bdf8' : 'rgba(56, 189, 248, 0.42)',
      width: highlight ? 3 : 1.4,
      alpha: highlight ? alpha : alpha * 0.6,
      head: highlight ? 8 : 5,
      glow: highlight
    });
  });
}

// --- Expanded focus card ------------------------------------------------
// The focused node stops being a label and becomes a small document: readme
// (markdown intent), input fields and output fields, laid out in world units
// so it pans and zooms with everything else.

/** Restores an item to its collapsed footprint. */
function collapseItem(item) {
  item.focusX = null;
  item.focusY = null;
  item.expanded = null;
  item.w = item.baseW;
  item.h = item.baseH;
}

/** Greedy word wrap against the current canvas font. */
function wrapText(text, maxW, font) {
  ctx.font = font;
  const words = String(text).split(/\s+/).filter(Boolean);
  const lines = [];
  let line = '';

  const pushLongWord = (word) => {
    let chunk = '';
    for (let i = 0; i < word.length; i++) {
      if (ctx.measureText(chunk + word[i]).width > maxW && chunk) {
        lines.push(chunk);
        chunk = '';
      }
      chunk += word[i];
    }
    return chunk;
  };

  words.forEach(word => {
    const candidate = line ? line + ' ' + word : word;
    if (ctx.measureText(candidate).width <= maxW) {
      line = candidate;
      return;
    }
    if (line) lines.push(line);
    line = ctx.measureText(word).width > maxW ? pushLongWord(word) : word;
  });

  if (line) lines.push(line);
  return lines;
}

/** Flattens markdown into drawable blocks: heading, paragraph, bullet, code. */
function markdownToBlocks(md) {
  if (!md) return [];
  const lines = String(md).replace(/\r\n?/g, '\n').split('\n');
  const blocks = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    if (line.trim() === '') { i++; continue; }

    const fence = line.match(RE_FENCE);
    if (fence) {
      const closer = fence[1].charAt(0).repeat(3);
      const body = [];
      i++;
      while (i < lines.length && lines[i].trim().indexOf(closer) !== 0) {
        body.push(lines[i]);
        i++;
      }
      i++;
      body.forEach(codeLine => blocks.push({ type: 'code', text: codeLine }));
      continue;
    }

    const heading = line.match(RE_HEADING);
    if (heading) {
      blocks.push({ type: 'heading', text: markdownToPlainText(heading[2], 400) });
      i++;
      continue;
    }

    if (RE_RULE.test(line)) { i++; continue; }

    const listItem = line.match(RE_LIST);
    if (listItem) {
      blocks.push({ type: 'bullet', text: markdownToPlainText(listItem[3], 400) });
      i++;
      continue;
    }

    const quote = line.match(RE_QUOTE);
    if (quote) {
      blocks.push({ type: 'para', text: markdownToPlainText(quote[1], 400) });
      i++;
      continue;
    }

    const para = [];
    while (i < lines.length && lines[i].trim() !== '' && !isBlockStart(lines[i])) {
      para.push(lines[i].trim());
      i++;
    }
    blocks.push({ type: 'para', text: markdownToPlainText(para.join(' '), 600) });
  }

  return blocks;
}

/**
 * Packs chips into rows that fit the card width. Each chip keeps its x offset
 * (relative to the card's inner left edge) so hit testing needs no draw pass,
 * and an optional node id when the chip navigates somewhere.
 */
function buildChipRows(chips, innerW) {
  ctx.font = CARD_FONTS.chip;
  const rows = [];
  let row = [];
  let cursorX = 0;

  chips.forEach(chip => {
    const raw = chip && chip.text !== undefined ? chip.text : chip;
    const text = truncateText(typeof raw === 'string' ? raw : JSON.stringify(raw), innerW - 18);
    const w = ctx.measureText(text).width + 16;
    if (row.length && cursorX + w > innerW) {
      rows.push(row);
      row = [];
      cursorX = 0;
    }
    row.push({ text: text, w: w, x: cursorX, id: (chip && chip.id) || null });
    cursorX += w + 6;
  });

  if (row.length) rows.push(row);
  return rows;
}

/**
 * Measures the focused card once and returns a flat draw list plus its size,
 * so drawing each frame is a straight loop with no re-measuring.
 */
function buildExpandedCard(item) {
  const isModule = isModuleId(item.id);
  const innerW = CARD_W - CARD_PAD * 2;
  const entries = [];
  let y = CARD_PAD;

  const kind = isModule ? { text: 'mod', color: null } : symbolKind(item);
  entries.push({
    kind: 'header',
    y: y,
    title: item.display_label || item.label,
    badge: kind.text,
    badgeColor: kind.color
  });
  y += 24;

  const range = item.code_start ? '  (L' + item.code_start + '-' + item.code_end + ')'
              : (item.source_location ? '  (' + item.source_location + ')' : '');
  const metaText = (item.file || '') + range;
  wrapText(metaText, innerW, CARD_FONTS.meta).slice(0, 2).forEach(line => {
    entries.push({ kind: 'meta', y: y, text: line });
    y += 14;
  });

  y += 6;
  entries.push({ kind: 'divider', y: y });
  y += 12;

  // Readme / intent.
  entries.push({ kind: 'section', y: y, text: 'README / INTENT' });
  y += 16;

  const blocks = markdownToBlocks(item.intent);
  let bodyLines = 0;
  let truncated = false;

  for (let b = 0; b < blocks.length && !truncated; b++) {
    const block = blocks[b];
    const font = block.type === 'heading' ? CARD_FONTS.bodyBold
               : block.type === 'code' ? CARD_FONTS.code
               : CARD_FONTS.body;
    const indent = block.type === 'bullet' ? 14 : 0;
    const lines = wrapText(block.text, innerW - indent, font);

    for (let l = 0; l < lines.length; l++) {
      if (bodyLines >= CARD_MAX_BODY_LINES) { truncated = true; break; }
      entries.push({
        kind: 'body',
        y: y,
        text: lines[l],
        font: font,
        indent: indent,
        bullet: block.type === 'bullet' && l === 0,
        color: block.type === 'heading' ? '#38bdf8' : (block.type === 'code' ? '#c084fc' : '#e2e8f0')
      });
      y += block.type === 'code' ? 15 : 16;
      bodyLines++;
    }
    if (block.type === 'heading' || block.type === 'para') y += 4;
  }

  if (!blocks.length) {
    entries.push({ kind: 'body', y: y, text: 'No documentation recorded.', font: CARD_FONTS.body, indent: 0, color: '#64748b' });
    y += 16;
  } else if (truncated) {
    entries.push({ kind: 'body', y: y, text: '... (full text in the inspector)', font: CARD_FONTS.body, indent: 0, color: '#64748b' });
    y += 16;
  }

  const chipSection = (title, chips, color, bg) => {
    if (!chips || !chips.length) return;
    const clickable = !!(chips[0] && chips[0].id);
    y += 8;
    entries.push({
      kind: 'section',
      y: y,
      text: title + ' (' + chips.length + ')' + (clickable ? '   CLICK TO OPEN' : '')
    });
    y += 16;
    buildChipRows(chips.slice(0, 24), innerW).forEach(row => {
      entries.push({ kind: 'chips', y: y, row: row, color: color, bg: bg, clickable: clickable });
      y += 24;
    });
  };

  const codeSection = () => {
    // Source is read live; the card shows it as soon as it arrives, and the
    // loader rebuilds the card when it does.
    const loaded = cachedSymbolSource(item);
    if (!loaded) {
      if (!item.path) return;
      y += 10;
      entries.push({ kind: 'section', y: y, text: 'SOURCE' });
      y += 16;
      entries.push({
        kind: 'body',
        y: y,
        text: sourceAccessMode() === 'none'
          ? 'Connect the project to read source'
          : 'Reading ' + item.path + ' ...',
        font: CARD_FONTS.body,
        indent: 0,
        color: '#64748b'
      });
      y += 16;
      return;
    }

    const shown = loaded.lines.slice(0, CARD_CODE_LINES);

    y += 10;
    entries.push({
      kind: 'section',
      y: y,
      text: 'SOURCE  L' + loaded.start + '-' + loaded.end + (loaded.relocated ? '  (re-resolved)' : '')
    });
    y += 16;

    entries.push({ kind: 'codebg', y: y - 5, h: shown.length * 14 + 10 });
    ctx.font = CARD_FONTS.code;
    shown.forEach((line, idx) => {
      entries.push({
        kind: 'codeline',
        y: y,
        num: loaded.start + idx,
        text: truncateText(line.replace(/\t/g, '    '), innerW - 40),
        comment: /^\s*(#|\/\/|\*)/.test(line)
      });
      y += 14;
    });
    y += 6;

    const remaining = loaded.lines.length - shown.length;
    if (remaining > 0) {
      entries.push({
        kind: 'body',
        y: y,
        text: '+ ' + remaining + ' more lines - press V for the whole file',
        font: CARD_FONTS.body,
        indent: 0,
        color: '#64748b'
      });
      y += 16;
    }
  };

  if (isModule) {
    chipSection(
      'CONTAINED SYMBOLS',
      (item.subnodes || []).map(s => ({ text: s.display_label || s.label, id: s.id })),
      '#34d399',
      'rgba(52, 211, 153, 0.14)'
    );
  } else {
    chipSection('INPUTS', item.input_fields, '#38bdf8', 'rgba(56, 189, 248, 0.14)');
    chipSection('OUTPUTS', item.output_fields, '#c084fc', 'rgba(192, 132, 252, 0.14)');
    const other = (item.fields || []).filter(f =>
      (item.input_fields || []).indexOf(f) === -1 && (item.output_fields || []).indexOf(f) === -1);
    chipSection('FIELDS', other, '#fbbf24', 'rgba(251, 191, 36, 0.14)');
  }

  codeSection();

  return { w: CARD_W, h: y + CARD_PAD, entries: entries };
}

function drawExpandedCard(item) {
  const layer = layerById[item.layer_id] || FALLBACK_COLOR;
  const card = item.expanded;
  const left = item.renderX - item.w / 2;
  const top = item.renderY - item.h / 2;
  const innerW = item.w - CARD_PAD * 2;

  ctx.globalAlpha = item.renderOpacity;

  roundRect(left, top, item.w, item.h, 12);
  ctx.fillStyle = '#12162a';
  ctx.strokeStyle = '#38bdf8';
  ctx.lineWidth = 2.4;
  ctx.shadowColor = 'rgba(56, 189, 248, 0.55)';
  ctx.shadowBlur = 18;
  ctx.fill();
  ctx.stroke();
  ctx.shadowBlur = 0;

  // Layer accent strip down the left edge.
  ctx.save();
  roundRect(left, top, item.w, item.h, 12);
  ctx.clip();
  ctx.fillStyle = layer.color;
  ctx.fillRect(left, top, 4, item.h);
  ctx.restore();

  card.entries.forEach(entry => {
    const x = left + CARD_PAD;
    const y = top + entry.y;

    if (entry.kind === 'header') {
      ctx.textAlign = 'left';
      ctx.textBaseline = 'top';
      ctx.font = CARD_FONTS.kind;
      ctx.fillStyle = entry.badgeColor || layer.color;
      ctx.fillText(entry.badge, x, y + 4);
      const badgeW = ctx.measureText(entry.badge).width + 10;
      ctx.font = CARD_FONTS.title;
      ctx.fillStyle = '#f8fafc';
      ctx.fillText(truncateText(entry.title, innerW - badgeW), x + badgeW, y);
      return;
    }

    if (entry.kind === 'meta') {
      ctx.font = CARD_FONTS.meta;
      ctx.fillStyle = '#94a3b8';
      ctx.textAlign = 'left';
      ctx.textBaseline = 'top';
      ctx.fillText(entry.text, x, y);
      return;
    }

    if (entry.kind === 'divider') {
      ctx.strokeStyle = 'rgba(148, 163, 184, 0.22)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, y);
      ctx.lineTo(x + innerW, y);
      ctx.stroke();
      return;
    }

    if (entry.kind === 'section') {
      ctx.font = CARD_FONTS.section;
      ctx.fillStyle = '#64748b';
      ctx.textAlign = 'left';
      ctx.textBaseline = 'top';
      ctx.fillText(entry.text, x, y);
      return;
    }

    if (entry.kind === 'body') {
      ctx.font = entry.font;
      ctx.fillStyle = entry.color;
      ctx.textAlign = 'left';
      ctx.textBaseline = 'top';
      if (entry.bullet) {
        ctx.fillStyle = '#64748b';
        ctx.fillText('-', x, y);
        ctx.fillStyle = entry.color;
      }
      ctx.fillText(entry.text, x + entry.indent, y);
      return;
    }

    if (entry.kind === 'codebg') {
      roundRect(x - 4, y, innerW + 8, entry.h, 6);
      ctx.fillStyle = '#0b0e1a';
      ctx.fill();
      ctx.strokeStyle = 'rgba(148, 163, 184, 0.14)';
      ctx.lineWidth = 1;
      ctx.stroke();
      return;
    }

    if (entry.kind === 'codeline') {
      ctx.font = CARD_FONTS.code;
      ctx.textBaseline = 'top';
      ctx.textAlign = 'right';
      ctx.fillStyle = '#3f4661';
      ctx.fillText(String(entry.num), x + 26, y);
      ctx.textAlign = 'left';
      ctx.fillStyle = entry.comment ? '#64748b' : '#cbd5e1';
      ctx.fillText(entry.text, x + 36, y);
      return;
    }

    if (entry.kind === 'chips') {
      ctx.font = CARD_FONTS.chip;
      ctx.textBaseline = 'middle';
      entry.row.forEach(chip => {
        const chipX = x + chip.x;
        const active = chip.id && chip.id === hoveredChipId;
        roundRect(chipX, y, chip.w, CHIP_H, 5);
        ctx.fillStyle = active ? entry.color : entry.bg;
        ctx.fill();
        ctx.strokeStyle = entry.color;
        ctx.lineWidth = active ? 1.6 : 1;
        ctx.stroke();
        ctx.fillStyle = active ? '#0b0e1a' : entry.color;
        ctx.textAlign = 'left';
        ctx.fillText(chip.text, chipX + 8, y + CHIP_H / 2 + 1);
        // Clickable chips get an underline so they read as navigable.
        if (chip.id && !active) {
          ctx.strokeStyle = entry.color;
          ctx.lineWidth = 0.8;
          ctx.beginPath();
          ctx.moveTo(chipX + 8, y + CHIP_H - 4);
          ctx.lineTo(chipX + chip.w - 8, y + CHIP_H - 4);
          ctx.stroke();
        }
      });
      ctx.textBaseline = 'top';
    }
  });

  ctx.globalAlpha = 1;
}

function symbolKind(node) {
  if (node.type === 'endpoint') return { text: 'api', color: '#34d399' };
  if (node.type === 'class' || /^[A-Z][A-Za-z0-9]*$/.test(node.label || '')) return { text: 'cls', color: '#c084fc' };
  if (node.type === 'method') return { text: 'mth', color: '#fbbf24' };
  return { text: 'fn', color: '#38bdf8' };
}

function drawModuleCard(m) {
  if (m.expanded) { drawExpandedCard(m); return; }
  const layer = layerById[m.layer_id] || FALLBACK_COLOR;
  const active = selectedId === m.id || focusId === m.id;
  const isHovered = hoveredId === m.id;
  const isNeighbour = focusUpstream.has(m.id) || focusDownstream.has(m.id);

  const left = m.renderX - m.w / 2;
  const top = m.renderY - m.h / 2;

  ctx.globalAlpha = m.renderOpacity;
  roundRect(left, top, m.w, m.h, 9);

  if (active) {
    ctx.fillStyle = '#1e293b';
    ctx.strokeStyle = '#38bdf8';
    ctx.lineWidth = 2.5;
    ctx.shadowColor = '#38bdf8';
    ctx.shadowBlur = 12;
  } else if (isHovered || isNeighbour) {
    ctx.fillStyle = '#1e293b';
    ctx.strokeStyle = layer.color;
    ctx.lineWidth = 2;
    ctx.shadowColor = layer.color;
    ctx.shadowBlur = 6;
  } else {
    ctx.fillStyle = '#141829';
    ctx.strokeStyle = layer.border;
    ctx.lineWidth = 1.2;
    ctx.shadowBlur = 0;
  }
  ctx.fill();
  ctx.stroke();
  ctx.shadowBlur = 0;

  ctx.beginPath();
  ctx.arc(left + 16, top + 19, 5, 0, Math.PI * 2);
  ctx.fillStyle = layer.color;
  ctx.fill();

  ctx.textAlign = 'left';
  ctx.textBaseline = 'middle';
  ctx.fillStyle = active ? '#38bdf8' : '#f8fafc';
  ctx.font = 'bold 13px -apple-system, sans-serif';
  ctx.fillText(truncateText(m.label, m.w - 58), left + 28, top + 19);

  ctx.font = '600 11px monospace';
  ctx.fillStyle = '#94a3b8';
  ctx.fillText(m.subnode_count + ' public symbols', left + 14, top + 39);

  if (m.is_test) {
    ctx.textAlign = 'right';
    ctx.fillStyle = '#e2e8f0';
    ctx.font = '10px -apple-system, sans-serif';
    ctx.fillText('🧪 test', left + m.w - 12, top + 39);
  }

  const deadCount = (m.subnodes || []).filter(isDeadItem).length;
  if (deadCount > 0) {
    ctx.textAlign = 'right';
    ctx.fillStyle = '#f59e0b';
    ctx.font = '10px -apple-system, sans-serif';
    ctx.fillText('⚠️ ' + deadCount + ' dead', left + m.w - (m.is_test ? 65 : 12), top + 39);
  }

  ctx.globalAlpha = 1;
}

function drawNodeCard(n) {
  if (n.expanded) { drawExpandedCard(n); return; }
  const layer = layerById[n.layer_id] || FALLBACK_COLOR;
  const active = selectedId === n.id || focusId === n.id;
  const isHovered = hoveredId === n.id;
  const isNeighbour = focusUpstream.has(n.id) || focusDownstream.has(n.id);
  const isDead = isDeadItem(n);

  const left = n.renderX - n.w / 2;
  const top = n.renderY - n.h / 2;

  ctx.globalAlpha = n.renderOpacity;
  roundRect(left, top, n.w, n.h, 7);

  if (active) {
    ctx.fillStyle = '#1e293b';
    ctx.strokeStyle = '#38bdf8';
    ctx.lineWidth = 2.3;
    ctx.shadowColor = '#38bdf8';
    ctx.shadowBlur = 12;
  } else if (isHovered || isNeighbour) {
    ctx.fillStyle = '#222842';
    ctx.strokeStyle = layer.color;
    ctx.lineWidth = 1.8;
    ctx.shadowColor = layer.color;
    ctx.shadowBlur = 6;
  } else if (isDead) {
    ctx.fillStyle = '#181422';
    ctx.strokeStyle = '#f59e0b';
    ctx.lineWidth = 1.4;
    ctx.shadowBlur = 0;
  } else {
    ctx.fillStyle = '#141829';
    ctx.strokeStyle = layer.border;
    ctx.lineWidth = 1.2;
    ctx.shadowBlur = 0;
  }
  ctx.fill();
  ctx.stroke();
  ctx.shadowBlur = 0;

  const kind = symbolKind(n);
  ctx.textAlign = 'left';
  ctx.textBaseline = 'middle';
  ctx.fillStyle = active ? '#38bdf8' : kind.color;
  ctx.font = 'bold 9px monospace';
  ctx.fillText(kind.text, left + 9, top + 18);

  ctx.fillStyle = '#f8fafc';
  ctx.font = 'bold 12px -apple-system, sans-serif';
  ctx.fillText(truncateText(n.display_label || n.label, n.w - 48), left + 34, top + 18);

  ctx.fillStyle = '#94a3b8';
  ctx.font = '10px monospace';
  ctx.fillText(truncateText(baseName(n.file), n.w - 74), left + 9, top + 38);

  ctx.textAlign = 'right';
  ctx.font = '9px monospace';
  let chipX = left + n.w - 9;
  if (isDead) {
    ctx.fillStyle = '#f59e0b';
    ctx.fillText('⚠️ dead', chipX, top + 38);
    chipX -= 44;
  }
  if ((n.output_fields || []).length) {
    ctx.fillStyle = '#c084fc';
    ctx.fillText('out', chipX, top + 38);
    chipX -= 26;
  }
  if ((n.input_fields || []).length) {
    ctx.fillStyle = '#38bdf8';
    ctx.fillText('in', chipX, top + 38);
  }

  ctx.globalAlpha = 1;
}

function drawCanvas() {
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  ctx.save();
  ctx.translate(panX, panY);
  ctx.scale(scale, scale);

  const tier = getActiveTier();

  if (!focusId && !traceActive) drawLayerHalos(tier);

  if (tier === 1) {
    drawModuleEdges();
    DATA.modules.forEach(m => {
      if (m.renderOpacity <= VISIBLE_EPSILON || !inViewport(m)) return;
      drawModuleCard(m);
    });
  } else {
    drawChildEdges();
    DATA.nodes.forEach(n => {
      if (n.renderOpacity <= VISIBLE_EPSILON || !inViewport(n)) return;
      drawNodeCard(n);
    });
  }

  ctx.restore();
}

function resize() {
  const rect = container.getBoundingClientRect();
  width = rect.width || window.innerWidth;
  height = rect.height || (window.innerHeight - 56);
  dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(width * dpr);
  canvas.height = Math.round(height * dpr);
}

// -------------------------------------------------------------
// 6. Hit testing, pan / drag / zoom interaction
// -------------------------------------------------------------
/**
 * Chips inside an expanded card are their own click targets. Their rectangles
 * come straight from the layout, so no draw pass is needed to find them.
 */
function chipHitTest(item, worldX, worldY) {
  if (!item || !item.expanded) return null;

  const left = item.renderX - item.w / 2 + CARD_PAD;
  const top = item.renderY - item.h / 2;

  for (let e = 0; e < item.expanded.entries.length; e++) {
    const entry = item.expanded.entries[e];
    if (entry.kind !== 'chips' || !entry.clickable) continue;

    const rowTop = top + entry.y;
    if (worldY < rowTop || worldY > rowTop + CHIP_H) continue;

    for (let c = 0; c < entry.row.length; c++) {
      const chip = entry.row[c];
      if (!chip.id) continue;
      const chipLeft = left + chip.x;
      if (worldX >= chipLeft && worldX <= chipLeft + chip.w) return chip.id;
    }
  }
  return null;
}

function hitTest(mouseX, mouseY) {
  const world = screenToWorld(mouseX, mouseY);
  const isModule = getActiveTier() === 1;
  const list = isModule ? DATA.modules : DATA.nodes;
  // Reverse order so the topmost drawn card wins the hit.
  for (let i = list.length - 1; i >= 0; i--) {
    const item = list[i];
    if (item.renderOpacity < 0.35) continue;
    if (world.x >= item.renderX - item.w / 2 && world.x <= item.renderX + item.w / 2 &&
        world.y >= item.renderY - item.h / 2 && world.y <= item.renderY + item.h / 2) {
      return { item: item, isModule: isModule };
    }
  }
  return null;
}

let pointerMode = null;        // 'pan' | 'node'
let pointerMoved = false;
let pointerStart = { x: 0, y: 0 };
let panGrab = { x: 0, y: 0 };
let dragItem = null;
let dragOffset = { x: 0, y: 0 };
let pendingChipId = null;      // chip pressed on pointerdown, opened on a clean click
let spaceHeld = false;

function localPos(ev) {
  const rect = container.getBoundingClientRect();
  return { x: ev.clientX - rect.left, y: ev.clientY - rect.top };
}

container.addEventListener('pointerdown', (ev) => {
  if (ev.button !== 0 && ev.button !== 1) return;
  ev.preventDefault();
  try { container.setPointerCapture(ev.pointerId); } catch (err) { /* not capturable */ }
  fadeHint();

  const p = localPos(ev);
  pointerStart = p;
  pointerMoved = false;

  // Middle button and space-drag always pan, even when over a node.
  const hit = (ev.button === 1 || spaceHeld) ? null : hitTest(p.x, p.y);

  pendingChipId = null;

  if (hit) {
    pointerMode = 'node';
    dragItem = hit.item;
    const world = screenToWorld(p.x, p.y);
    pendingChipId = chipHitTest(hit.item, world.x, world.y);
    dragOffset = { x: world.x - dragItem.renderX, y: world.y - dragItem.renderY };
  } else {
    // Empty space: start panning right away.
    pointerMode = 'pan';
    panGrab = { x: p.x - panX, y: p.y - panY };
    container.classList.add('panning');
    hideTooltip();
  }
});

container.addEventListener('pointermove', (ev) => {
  const p = localPos(ev);

  if (pointerMode && !pointerMoved) {
    const dist = Math.hypot(p.x - pointerStart.x, p.y - pointerStart.y);
    if (dist > DRAG_THRESHOLD) pointerMoved = true;
  }

  if (pointerMode === 'pan') {
    setCamera(p.x - panGrab.x, p.y - panGrab.y, tScale, false);
    return;
  }

  if (pointerMode === 'node' && dragItem) {
    if (!pointerMoved) return;
    dragItem.dragging = true;
    container.classList.add('moving-node');
    hideTooltip();
    const world = screenToWorld(p.x, p.y);
    const nx = world.x - dragOffset.x;
    const ny = world.y - dragOffset.y;
    dragItem.renderX = nx;
    dragItem.renderY = ny;
    dragItem.targetX = nx;
    dragItem.targetY = ny;
    // Persist the move in the free layout and, when focused, the focus layout.
    dragItem.baseX = nx;
    dragItem.baseY = ny;
    if (focusId && dragItem.focusX !== null && dragItem.focusX !== undefined) {
      dragItem.focusX = nx;
      dragItem.focusY = ny;
    }
    return;
  }

  const hit = hitTest(p.x, p.y);
  if (hit) {
    hoveredId = hit.item.id;
    container.classList.add('over-node');
    const world = screenToWorld(p.x, p.y);
    hoveredChipId = chipHitTest(hit.item, world.x, world.y);
    // Hovering a chip previews the symbol it opens, not the card it sits on.
    const tipItem = (hoveredChipId && getItem(hoveredChipId)) || hit.item;
    showTooltip(tipItem, p.x, p.y);
  } else {
    if (hoveredId) hideTooltip();
    hoveredId = null;
    hoveredChipId = null;
    container.classList.remove('over-node');
  }
});

function endPointer(ev) {
  if (!pointerMode) return;

  if (!pointerMoved) {
    if (pointerMode === 'node' && dragItem) {
      // A chip press opens that symbol; anywhere else on the card selects it.
      const chipTarget = pendingChipId && getItem(pendingChipId) ? pendingChipId : null;
      selectItem(chipTarget || dragItem.id, true);
    } else if (pointerMode === 'pan') {
      // A click on empty canvas leaves focus mode and clears the inspector.
      if (focusId || traceActive) exitFocus();
      else clearSelection();
    }
  }

  if (dragItem) dragItem.dragging = false;
  dragItem = null;
  pendingChipId = null;
  pointerMode = null;
  container.classList.remove('panning');
  container.classList.remove('moving-node');
  if (ev && ev.pointerId !== undefined && container.hasPointerCapture && container.hasPointerCapture(ev.pointerId)) {
    container.releasePointerCapture(ev.pointerId);
  }
}

container.addEventListener('pointerup', endPointer);
container.addEventListener('pointercancel', endPointer);
container.addEventListener('pointerleave', hideTooltip);
container.addEventListener('contextmenu', (ev) => ev.preventDefault());

container.addEventListener('wheel', (ev) => {
  ev.preventDefault();
  const p = localPos(ev);
  if (ev.shiftKey && !ev.ctrlKey && !ev.metaKey) {
    setCamera(tPanX - ev.deltaX, tPanY - ev.deltaY, tScale, false);
  } else {
    zoomAt(ev.deltaY < 0 ? 1.12 : 0.89, p.x, p.y);
  }
  fadeHint();
}, { passive: false });

function zoomAt(factor, centerX, centerY) {
  const next = Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale * factor));
  const nx = centerX - (centerX - panX) * (next / scale);
  const ny = centerY - (centerY - panY) * (next / scale);
  setCamera(nx, ny, next, false);
}

function updateHud() {
  document.getElementById('hud-zoom-text').textContent = Math.round(scale * 100) + '%';
  const tier = getActiveTier();
  const lod = document.getElementById('lod-text');
  if (focusId) {
    lod.textContent = tier === 1 ? 'Focused module neighbourhood' : 'Focused symbol neighbourhood';
  } else if (traceActive) {
    lod.textContent = 'Traced flow - ' + traceIds.size + ' nodes';
  } else {
    lod.textContent = tier === 1 ? 'Modules overview' : 'Components and methods';
  }
}

/** Frames whatever is currently visible (focus cluster, trace set, or all). */
function fitToVisible(animate) {
  const tier = getActiveTier();
  const isModule = tier === 1;
  const list = isModule ? DATA.modules : DATA.nodes;

  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  let count = 0;

  list.forEach(item => {
    if (!isVisible(item, isModule)) return;
    const useFocus = focusId && item.focusX !== null && item.focusX !== undefined;
    const x = useFocus ? item.focusX : item.baseX;
    const y = useFocus ? item.focusY : item.baseY;
    if (!Number.isFinite(x) || !Number.isFinite(y)) return;
    count++;
    minX = Math.min(minX, x - item.w / 2);
    maxX = Math.max(maxX, x + item.w / 2);
    minY = Math.min(minY, y - item.h / 2);
    maxY = Math.max(maxY, y + item.h / 2);
  });

  if (!count || !Number.isFinite(minX)) return;

  const padding = focusId ? 120 : 160;
  const spanX = Math.max(240, maxX - minX + padding * 2);
  const spanY = Math.max(240, maxY - minY + padding * 2);
  const fitScale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, Math.min(width / spanX, height / spanY)));
  // Fitting must never flip the level of detail out from under the viewer, and
  // a focused card has to stay readable even when its fan-out is huge - the
  // viewer can pan to the outliers.
  const clamped = focusId ? Math.max(fitScale, FOCUS_MIN_SCALE)
    : traceActive ? fitScale
    : (tier === 1 ? Math.min(fitScale, TIER_SWITCH_SCALE - 0.02)
                  : Math.max(fitScale, TIER_SWITCH_SCALE + 0.02));

  // When the min-scale clamp means the cluster cannot fit, keep the focused
  // card itself centred rather than letting it drift off the edge.
  const focusedItem = focusId ? getItem(focusId) : null;
  const anchorX = (clamped > fitScale && focusedItem) ? focusedItem.focusX : (minX + maxX) / 2;
  const anchorY = (clamped > fitScale && focusedItem) ? focusedItem.focusY : (minY + maxY) / 2;

  setCamera(
    width / 2 - anchorX * clamped,
    height / 2 - anchorY * clamped,
    clamped,
    animate !== false
  );
}

function centerOn(id, animate) {
  const item = getItem(id);
  if (!item) return;
  const useFocus = focusId && item.focusX !== null && item.focusX !== undefined;
  const x = useFocus ? item.focusX : item.baseX;
  const y = useFocus ? item.focusY : item.baseY;
  setCamera(width / 2 - x * tScale, height / 2 - y * tScale, tScale, animate !== false);
}

function showTooltip(item, mouseX, mouseY) {
  const layer = layerById[item.layer_id] || FALLBACK_COLOR;
  const desc = markdownToPlainText(item.intent, 180);
  tooltipEl.innerHTML =
    '<div class="tooltip-header"><span style="color:' + layer.color + '">*</span>' +
    '<span>' + escapeHtml(item.display_label || item.label) + '</span></div>' +
    '<div class="tooltip-file">' + escapeHtml(item.file || '') + '</div>' +
    (desc ? '<div style="font-size:11px; color:#cbd5e1;">' + escapeHtml(desc) + '</div>' : '');
  tooltipEl.style.display = 'block';
  // Flip the tooltip when it would run past the edge of the canvas.
  const tw = tooltipEl.offsetWidth;
  const th = tooltipEl.offsetHeight;
  const left = mouseX + 16 + tw > width ? Math.max(4, mouseX - 16 - tw) : mouseX + 16;
  const top = mouseY + 16 + th > height ? Math.max(4, mouseY - 16 - th) : mouseY + 16;
  tooltipEl.style.left = left + 'px';
  tooltipEl.style.top = top + 'px';
}

function hideTooltip() {
  tooltipEl.style.display = 'none';
}

let hintFaded = false;
function fadeHint() {
  if (hintFaded) return;
  hintFaded = true;
  document.getElementById('canvas-hint').classList.add('faded');
}

// -------------------------------------------------------------
// 7. Inspector drawer
// -------------------------------------------------------------
function selectItem(id, focus) {
  const item = getItem(id);
  if (!item) return;
  selectedId = id;
  // The layout is about to move under the cursor; a stale tooltip would lie.
  hoveredChipId = null;
  hideTooltip();
  // Open the drawer first and re-measure: it steals canvas width, and the
  // focus fit that follows has to frame the cluster inside what is left.
  populateDrawer(item);
  resize();
  if (focus) enterFocus(id);
}

function clearSelection() {
  selectedId = null;
  closeDrawer();
}

function closeDrawer() {
  document.getElementById('inspector-drawer').classList.add('collapsed');
  selectedId = null;
  // The drawer takes canvas width with it, so re-measure after the reflow.
  window.requestAnimationFrame(resize);
}

function fieldPills(list, cls) {
  if (!list || !list.length) return '<em>(None)</em>';
  return list.map(f => {
    const text = typeof f === 'string' ? f : JSON.stringify(f);
    return '<span class="field-tag ' + cls + '">' + escapeHtml(text) + '</span>';
  }).join('');
}

function metaRow(key, value, linkId) {
  if (value === undefined || value === null || value === '') return '';
  const cls = linkId ? 'meta-val link' : 'meta-val';
  const attr = linkId ? ' data-goto="' + escapeHtml(linkId) + '"' : '';
  return '<div class="meta-key">' + escapeHtml(key) + '</div>' +
         '<div class="' + cls + '"' + attr + '>' + escapeHtml(String(value)) + '</div>';
}

function connectionRows(items, direction) {
  if (!items.length) {
    return '<em>' + (direction === 'in' ? 'No upstream callers found' : 'No downstream connections found') + '</em>';
  }
  return items.map(c => {
    const arrow = direction === 'in' ? '&larr;' : '&rarr;';
    const confidence = (c.confidence !== undefined && c.confidence < 1)
      ? ' ' + Math.round(c.confidence * 100) + '%'
      : '';
    return '<div class="connection-item" data-goto="' + escapeHtml(c.id) + '">' +
      '<div class="connection-main">' +
        '<span class="connection-label">' + arrow + ' ' + escapeHtml(c.label) + '</span>' +
        (c.file ? '<span class="connection-file">' + escapeHtml(c.file) + '</span>' : '') +
      '</div>' +
      '<span class="connection-relation">' + escapeHtml(c.relation || '') + confidence + '</span>' +
    '</div>';
  }).join('');
}

/** Strips render/layout bookkeeping so the raw record stays readable. */
function rawRecord(item) {
  const skip = {
    w: 1, h: 1, baseX: 1, baseY: 1, focusX: 1, focusY: 1, targetX: 1, targetY: 1,
    renderX: 1, renderY: 1, targetOpacity: 1, renderOpacity: 1, dragging: 1,
    subnodes: 1, clusterH: 1, baseW: 1, baseH: 1, expanded: 1
  };
  const out = {};
  Object.keys(item).forEach(k => {
    if (skip[k]) return;
    out[k] = item[k];
  });
  if (item.subnodes) out.subnodes = item.subnodes.map(s => s.display_label || s.label);
  return JSON.stringify(out, null, 2);
}

/** Explains why no code is on screen, and offers the fix. */
function codePlaceholder(reason, item) {
  if (reason === 'no-access') {
    return '<div class="code-placeholder">Source is read live and this page has no access to the project yet.' +
      '<br/>Use <strong>Connect project</strong> in the header, or run ' +
      '<code class="md-code">tldrgraph ui --serve</code> to serve the repo.' +
      '<br/><button class="file-btn" data-connect-source>Connect project folder</button></div>';
  }
  if (reason === 'not-found') {
    return '<div class="code-placeholder">Could not find <code class="md-code">' +
      escapeHtml(item.name || item.label) + '</code> in <code class="md-code">' +
      escapeHtml(item.path) + '</code>. The file has probably changed since the last scan.</div>';
  }
  if (reason === 'unreadable') {
    return '<div class="code-placeholder">This file could not be read from the connected source.</div>';
  }
  return '<div class="code-placeholder">This node has no file of its own.</div>';
}

/** Renders the numbered, highlighted source block for the inspector. */
function populateCodeSection(item) {
  const section = document.getElementById('drawer-code-section');
  const table = document.getElementById('drawer-code-table');
  const note = document.getElementById('drawer-code-note');
  const range = document.getElementById('drawer-code-range');
  const holder = document.getElementById('drawer-code-holder');

  if (!item.path) {
    section.style.display = 'none';
    return;
  }

  section.style.display = 'block';
  range.textContent = item.code_start ? 'L' + item.code_start + '-' + item.code_end : '';
  note.style.display = 'none';
  table.innerHTML = '';
  holder.innerHTML = '<div class="code-placeholder">Reading source...</div>';

  const requestedFor = item.id;

  loadSymbolSource(item).then(result => {
    // The user may have selected something else while the file was loading.
    if (selectedId !== requestedFor) return;

    if (result.unavailable) {
      holder.innerHTML = codePlaceholder(result.unavailable, item);
      table.innerHTML = '';
      return;
    }

    holder.innerHTML = '';
    range.textContent = 'L' + result.start + '-' + result.end;

    if (result.relocated) {
      note.style.display = 'block';
      note.textContent = 'The scan recorded ' + (item.source_location || 'another line') +
        ', but the file has changed since; this is the current declaration.';
    }

    table.innerHTML = highlightCode(result.lines.join('\n'), item.language)
      .map((line, idx) =>
        '<tr><td class="code-gutter">' + (result.start + idx) + '</td>' +
        '<td class="code-line">' + (line || ' ') + '</td></tr>')
      .join('');
  });
}

/** File linkage: same-file symbols, ordered by line, click to jump. */
function populateSiblings(item) {
  const section = document.getElementById('drawer-siblings-section');
  const siblings = siblingsOf(item);

  section.style.display = siblings.length ? 'block' : 'none';
  if (!siblings.length) return;

  document.getElementById('drawer-siblings-count').textContent = siblings.length;
  document.getElementById('drawer-siblings-list').innerHTML = siblings.map(s =>
    '<div class="connection-item" data-goto="' + escapeHtml(s.id) + '">' +
      '<div class="connection-main">' +
        '<span class="connection-label">' + escapeHtml(s.display_label || s.label) + '</span>' +
        '<span class="connection-file">' + escapeHtml(s.type || 'symbol') + '</span>' +
      '</div>' +
      '<span class="connection-relation">' + (s.code_start ? 'L' + s.code_start : '') + '</span>' +
    '</div>'
  ).join('');
}

/** File linkage controls in the drawer header. */
function populateFileActions(item) {
  const actions = document.getElementById('drawer-file-actions');
  if (!item.path) {
    actions.style.display = 'none';
    return;
  }
  actions.style.display = 'flex';
  const link = document.getElementById('btn-open-editor');
  link.href = editorLink(item);
  link.title = 'Open ' + absolutePath(item) +
               (item.code_start ? ' at line ' + item.code_start : '') + ' in your editor';
}

function populateDrawer(item) {
  const isModule = isModuleId(item.id);
  const layer = layerById[item.layer_id] || FALLBACK_COLOR;
  document.getElementById('inspector-drawer').classList.remove('collapsed');

  const layerBadge = document.getElementById('drawer-layer-badge');
  layerBadge.textContent = item.layer || 'Layer';
  layerBadge.style.background = layer.bg;
  layerBadge.style.color = layer.color;
  layerBadge.style.border = '1px solid ' + layer.border;

  document.getElementById('drawer-type-badge').textContent = isModule ? 'Module' : (item.type || 'Component');
  document.getElementById('drawer-test-badge').style.display = item.is_test ? 'inline-block' : 'none';

  const deadBadge = document.getElementById('drawer-dead-badge');
  const isDead = !isModule && item.dead_code_status && item.dead_code_status !== 'live';
  deadBadge.style.display = isDead ? 'inline-block' : 'none';
  if (isDead) deadBadge.textContent = item.dead_code_status;

  document.getElementById('drawer-title').textContent = item.display_label || item.label;
  // Prefer the range resolved against live content over the recorded line,
  // which can be stale if the source moved since the scan.
  const live = cachedSymbolSource(item);
  const rangeText = live ? '  (L' + live.start + '-' + live.end + ')'
    : (item.code_start ? '  (L' + item.code_start + '-' + item.code_end + ')'
    : (item.source_location ? '  (' + item.source_location + ')' : ''));
  document.getElementById('drawer-file').textContent = (item.file || '') + rangeText;

  const inItems = isModule
    ? (item.inbound_modules || []).map(mid => ({
        id: mid,
        label: (modulesById[mid] || {}).label || mid,
        file: (modulesById[mid] || {}).file || '',
        relation: 'module call'
      }))
    : (item.inbound || []).map(c => ({
        id: c.source_id,
        label: c.source_label,
        file: c.source_file,
        relation: c.relation,
        confidence: c.confidence
      }));

  const outItems = isModule
    ? (item.outbound_modules || []).map(mid => ({
        id: mid,
        label: (modulesById[mid] || {}).label || mid,
        file: (modulesById[mid] || {}).file || '',
        relation: 'module call'
      }))
    : (item.outbound || []).map(c => ({
        id: c.target_id,
        label: c.target_label,
        file: c.target_file,
        relation: c.relation,
        confidence: c.confidence
      }));

  document.getElementById('drawer-meta-grid').innerHTML = [
    metaRow('Kind', isModule ? 'Module / file' : (item.type || 'component')),
    metaRow('Layer', item.layer),
    metaRow('File', item.file),
    metaRow('Location', item.code_start
      ? 'L' + item.code_start + '-' + item.code_end
      : item.source_location),
    isModule ? metaRow('Symbols', item.subnode_count)
             : metaRow('Module', baseName(item.file), item.module_id),
    metaRow('Inbound', inItems.length),
    metaRow('Outbound', outItems.length),
    (!isModule && item.dead_code_status) ? metaRow('Status', item.dead_code_status) : '',
    metaRow('Node id', item.id)
  ].join('');

  document.getElementById('drawer-intent').innerHTML = renderMarkdown(item.intent);

  const summarySection = document.getElementById('drawer-summary-section');
  const hasSummary = item.summary && item.summary.trim() &&
                     item.summary.trim() !== String(item.intent || '').trim();
  summarySection.style.display = hasSummary ? 'block' : 'none';
  if (hasSummary) {
    document.getElementById('drawer-summary').innerHTML = renderMarkdown(item.summary);
  }

  const inFields = item.input_fields || [];
  const outFields = item.output_fields || [];
  const otherFields = (item.fields || []).filter(f =>
    inFields.indexOf(f) === -1 && outFields.indexOf(f) === -1);

  const inputsSection = document.getElementById('drawer-inputs-section');
  inputsSection.style.display = inFields.length ? 'block' : 'none';
  if (inFields.length) {
    document.getElementById('drawer-input-count').textContent = inFields.length;
    document.getElementById('drawer-input-pills').innerHTML = fieldPills(inFields, 'tag-input');
  }

  const outputsSection = document.getElementById('drawer-outputs-section');
  outputsSection.style.display = outFields.length ? 'block' : 'none';
  if (outFields.length) {
    document.getElementById('drawer-output-count').textContent = outFields.length;
    document.getElementById('drawer-output-pills').innerHTML = fieldPills(outFields, 'tag-output');
  }

  const fieldsSection = document.getElementById('drawer-fields-section');
  fieldsSection.style.display = otherFields.length ? 'block' : 'none';
  if (otherFields.length) {
    document.getElementById('drawer-fields-count').textContent = otherFields.length;
    document.getElementById('drawer-field-pills').innerHTML = fieldPills(otherFields, 'tag-field');
  }

  const subnodes = isModule ? (item.subnodes || []) : [];
  document.getElementById('drawer-subnodes-section').style.display = subnodes.length ? 'block' : 'none';
  if (subnodes.length) {
    document.getElementById('drawer-subnode-count').textContent = subnodes.length;
    document.getElementById('drawer-subnodes-list').innerHTML = subnodes.map(sn =>
      '<div class="subnode-card" data-goto="' + escapeHtml(sn.id) + '">' +
        '<span class="subnode-name">' + escapeHtml(sn.display_label || sn.label) + '</span>' +
        '<span class="badge badge-neutral">' + escapeHtml(sn.type || 'symbol') + '</span>' +
      '</div>'
    ).join('');
  }

  document.getElementById('drawer-inbound-count').textContent = inItems.length;
  document.getElementById('drawer-inbound-list').innerHTML = connectionRows(inItems, 'in');
  document.getElementById('drawer-outbound-count').textContent = outItems.length;
  document.getElementById('drawer-outbound-list').innerHTML = connectionRows(outItems, 'out');

  populateFileActions(item);
  populateCodeSection(item);
  populateSiblings(item);

  document.getElementById('drawer-raw-json').textContent = rawRecord(item);
  document.getElementById('btn-trace-flow').classList.toggle('active', traceActive);

  window.requestAnimationFrame(resize);
}

// Delegated navigation for every clickable id inside the drawer.
document.getElementById('inspector-drawer').addEventListener('click', (ev) => {
  const target = ev.target.closest('[data-goto]');
  if (!target) return;
  const id = target.getAttribute('data-goto');
  if (getItem(id)) selectItem(id, true);
});

function traceSelectedFlow() {
  if (!selectedId) return;

  if (traceActive) {
    traceActive = false;
    traceIds.clear();
    document.getElementById('btn-trace-flow').classList.remove('active');
    updateFocusBanner();
    updateHud();
    return;
  }

  const rootId = selectedId;
  traceKind = isModuleId(rootId) ? 'module' : 'node';
  traceIds = new Set([rootId]);

  const walk = (getNext) => {
    const queue = [rootId];
    while (queue.length) {
      const curr = queue.shift();
      getNext(curr).forEach(next => {
        if (!traceIds.has(next) && getItem(next)) {
          traceIds.add(next);
          queue.push(next);
        }
      });
    }
  };

  walk(id => {
    const m = modulesById[id];
    if (m) return m.outbound_modules || [];
    const n = nodesById[id];
    return n ? (n.outbound || []).map(o => o.target_id) : [];
  });
  walk(id => {
    const m = modulesById[id];
    if (m) return m.inbound_modules || [];
    const n = nodesById[id];
    return n ? (n.inbound || []).map(o => o.source_id) : [];
  });

  // Tracing replaces focus mode: it shows a whole reachable subgraph in place.
  focusId = null;
  focusKind = null;
  focusUpstream.clear();
  focusDownstream.clear();
  focusVisible.clear();
  DATA.modules.forEach(collapseItem);
  DATA.nodes.forEach(collapseItem);

  traceActive = true;
  document.getElementById('btn-trace-flow').classList.add('active');
  updateFocusBanner();
  updateHud();
  fitToVisible(true);
}

// -------------------------------------------------------------
// 8. Search, legend, keyboard shortcuts
// -------------------------------------------------------------
const searchInput = document.getElementById('search-input');
const searchDropdown = document.getElementById('search-dropdown');
let searchResults = [];
let searchCursor = -1;

function matches(haystack, needle) {
  return !!haystack && String(haystack).toLowerCase().indexOf(needle) !== -1;
}

function handleSearch(q) {
  const query = q.trim().toLowerCase();
  searchResults = [];
  searchCursor = -1;

  if (!query) {
    searchDropdown.style.display = 'none';
    return;
  }

  DATA.modules.forEach(m => {
    if (searchResults.length >= 40) return;
    if (matches(m.label, query) || matches(m.file, query) || matches(m.intent, query)) {
      searchResults.push({ id: m.id, isModule: true, title: m.label, meta: m.file, layer_id: m.layer_id });
    }
  });

  DATA.nodes.forEach(n => {
    if (searchResults.length >= 40) return;
    const fieldHit = (n.input_fields || []).some(f => matches(String(f), query)) ||
                     (n.output_fields || []).some(f => matches(String(f), query)) ||
                     (n.fields || []).some(f => matches(String(f), query));
    if (matches(n.label, query) || matches(n.display_label, query) || matches(n.file, query) ||
        matches(n.intent, query) || fieldHit) {
      searchResults.push({
        id: n.id,
        isModule: false,
        title: n.display_label || n.label,
        meta: n.file + ' (' + n.type + ')',
        layer_id: n.layer_id
      });
    }
  });

  if (!searchResults.length) {
    searchDropdown.innerHTML = '<div style="padding:10px; font-size:12px; color:var(--text-dim); text-align:center;">' +
      'No results for "' + escapeHtml(q) + '"</div>';
    searchDropdown.style.display = 'block';
    return;
  }

  searchDropdown.innerHTML = searchResults.slice(0, 12).map((r, idx) => {
    const layer = layerById[r.layer_id] || FALLBACK_COLOR;
    return '<div class="search-result-item" data-idx="' + idx + '">' +
      '<div class="search-result-title">' +
        '<span style="color:' + layer.color + '">*</span>' +
        '<span>' + escapeHtml(r.title) + '</span>' +
        '<span class="badge badge-neutral">' + (r.isModule ? 'Module' : 'Symbol') + '</span>' +
      '</div>' +
      '<div class="search-result-meta">' + escapeHtml(r.meta) + '</div>' +
    '</div>';
  }).join('');
  searchDropdown.style.display = 'block';
}

function highlightSearchCursor() {
  Array.prototype.forEach.call(searchDropdown.children, (el, idx) => {
    el.classList.toggle('active', idx === searchCursor);
  });
}

function selectSearchResult(idx) {
  const result = searchResults[idx];
  if (!result) return;
  searchDropdown.style.display = 'none';
  selectItem(result.id, true);
}

searchInput.addEventListener('input', (ev) => handleSearch(ev.target.value));

searchInput.addEventListener('keydown', (ev) => {
  const visibleCount = Math.min(searchResults.length, 12);
  if (ev.key === 'ArrowDown' && visibleCount) {
    ev.preventDefault();
    searchCursor = (searchCursor + 1) % visibleCount;
    highlightSearchCursor();
  } else if (ev.key === 'ArrowUp' && visibleCount) {
    ev.preventDefault();
    searchCursor = (searchCursor - 1 + visibleCount) % visibleCount;
    highlightSearchCursor();
  } else if (ev.key === 'Enter' && visibleCount) {
    ev.preventDefault();
    selectSearchResult(searchCursor >= 0 ? searchCursor : 0);
  } else if (ev.key === 'Escape') {
    searchDropdown.style.display = 'none';
    searchInput.blur();
  }
});

searchDropdown.addEventListener('click', (ev) => {
  const row = ev.target.closest('[data-idx]');
  if (row) selectSearchResult(Number(row.getAttribute('data-idx')));
});

document.addEventListener('click', (ev) => {
  if (!ev.target.closest('.search-box')) searchDropdown.style.display = 'none';
});

const legendContainer = document.getElementById('layer-legend');
DATA.layers.forEach(layer => {
  const count = DATA.modules.filter(m => m.layer_id === layer.id).length;
  const pill = document.createElement('div');
  pill.className = 'layer-pill';
  pill.innerHTML = '<span class="layer-dot" style="background:' + layer.color + '"></span>' +
                   '<span>' + escapeHtml(layer.name) + ' (' + count + ')</span>';
  pill.addEventListener('click', () => {
    if (focusId || traceActive) exitFocus({ keepDrawer: true });
    const bounds = layerBounds[getActiveTier()][layer.id];
    if (!bounds) return;
    setCamera(
      width / 2 - ((bounds.minX + bounds.maxX) / 2) * tScale,
      height / 2 - ((bounds.minY + bounds.maxY) / 2) * tScale,
      tScale,
      true
    );
  });
  legendContainer.appendChild(pill);
});

function toggleTests() {
  hideTests = !hideTests;
  const btn = document.getElementById('btn-toggle-tests');
  btn.classList.toggle('active', hideTests);
  btn.textContent = hideTests ? '🧪 Tests: Hidden' : '🧪 Tests: Shown';
  const focused = focusId ? getItem(focusId) : null;
  if (hideTests && focused && focused.is_test) exitFocus();
}

function toggleDeadOnly() {
  showDeadOnly = !showDeadOnly;
  const btn = document.getElementById('btn-toggle-dead');
  btn.classList.toggle('active-dead', showDeadOnly);
  btn.textContent = showDeadOnly ? '⚠️ Dead Nodes: Only' : '⚠️ Dead Nodes';
  const focused = focusId ? getItem(focusId) : null;
  if (showDeadOnly && focused && !isDeadItem(focused)) exitFocus();
  if (showDeadOnly) fitToVisible(true);
}

function resetView() {
  exitFocus();
  hoveredId = null;
  hideTooltip();
  searchInput.value = '';
  searchDropdown.style.display = 'none';
  setCamera(panX, panY, 0.65, false);
  fitToVisible(true);
}

document.addEventListener('keydown', (ev) => {
  if (ev.target === searchInput) return;

  if (ev.code === 'Space') {
    spaceHeld = true;
    container.classList.add('panning');
    ev.preventDefault();
    return;
  }
  if (ev.key === 'Escape') {
    if (fileViewerPath) closeFileViewer();
    else if (focusId || traceActive) exitFocus();
    else clearSelection();
  } else if (ev.key === 'v' || ev.key === 'V') {
    openFileForSelection();
  } else if (ev.key === 'f' || ev.key === 'F') {
    fitToVisible(true);
  } else if (ev.key === '/') {
    ev.preventDefault();
    searchInput.focus();
  } else if (ev.key === '+' || ev.key === '=') {
    zoomAt(1.15, width / 2, height / 2);
  } else if (ev.key === '-' || ev.key === '_') {
    zoomAt(0.87, width / 2, height / 2);
  }
});

document.addEventListener('keyup', (ev) => {
  if (ev.code === 'Space') {
    spaceHeld = false;
    if (pointerMode !== 'pan') container.classList.remove('panning');
  }
});

document.getElementById('btn-zoom-in').addEventListener('click', () => zoomAt(1.25, width / 2, height / 2));
document.getElementById('btn-zoom-out').addEventListener('click', () => zoomAt(0.8, width / 2, height / 2));
document.getElementById('btn-fit').addEventListener('click', () => fitToVisible(true));
document.getElementById('btn-reset-view').addEventListener('click', resetView);
document.getElementById('btn-toggle-tests').addEventListener('click', toggleTests);
document.getElementById('btn-toggle-dead').addEventListener('click', toggleDeadOnly);
document.getElementById('btn-close-drawer').addEventListener('click', closeDrawer);
document.getElementById('btn-exit-focus').addEventListener('click', () => exitFocus({ keepDrawer: true }));
document.getElementById('btn-trace-flow').addEventListener('click', traceSelectedFlow);
document.getElementById('btn-copy-path').addEventListener('click', (ev) => {
  const item = selectedId ? getItem(selectedId) : null;
  if (item) copyToClipboard(absolutePath(item), ev.currentTarget);
});
document.getElementById('btn-copy-code').addEventListener('click', (ev) => {
  const item = selectedId ? getItem(selectedId) : null;
  const loaded = item ? cachedSymbolSource(item) : null;
  if (loaded) copyToClipboard(loaded.lines.join('\n'), ev.currentTarget);
});
document.getElementById('btn-view-file').addEventListener('click', openFileForSelection);
document.getElementById('btn-connect-source').addEventListener('click', connectProjectFolder);
document.getElementById('source-folder-input').addEventListener('change', (ev) => {
  adoptUploadedFiles(ev.target.files);
});
document.getElementById('file-viewer-close').addEventListener('click', closeFileViewer);
document.getElementById('file-viewer-outline').addEventListener('click', (ev) => {
  const row = ev.target.closest('[data-line]');
  if (!row) return;
  const line = Number(row.getAttribute('data-line'));
  if (line) scrollViewerToLine(line);
  document.querySelectorAll('#file-viewer-outline .outline-item').forEach(el => el.classList.remove('active'));
  row.classList.add('active');
});
document.getElementById('inspector-drawer').addEventListener('click', (ev) => {
  if (ev.target.closest('[data-connect-source]')) connectProjectFolder();
});
document.getElementById('drawer-file').addEventListener('click', openFileForSelection);
document.getElementById('btn-center-node').addEventListener('click', () => {
  if (selectedId) centerOn(selectedId, true);
});

window.addEventListener('resize', () => {
  resize();
  fitToVisible(false);
});

// -------------------------------------------------------------
// Boot
// -------------------------------------------------------------
resize();
updateHud();
fitToVisible(false);
requestAnimationFrame(renderLoop);
initSourceAccess();
