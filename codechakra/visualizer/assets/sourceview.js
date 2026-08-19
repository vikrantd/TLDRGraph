/* =============================================================
 * Live source access.
 *
 * Nothing about the project's code is baked into this file. Content is read
 * on demand, through whichever channel is available:
 *
 *   1. served  - the page is on a local server, so files are fetched relative
 *                to it (`codechakra ui --serve` sets this up)
 *   2. folder  - the viewer is granted read access to the project directory
 *                via the File System Access API; the grant is remembered
 *   3. upload  - a plain directory <input>, for browsers without the above
 *
 * Because content is live, the line ranges baked into the payload are treated
 * as hints only: every load re-verifies that the range still declares the
 * symbol, and re-finds it when the file has moved on.
 * ============================================================= */

'use strict';

const SOURCE_CACHE = new Map();      // path -> {lines, error}
const SYMBOL_SOURCE = new Map();     // nodeId -> {lines, start, end, relocated}
const RANGE_CACHE = new Map();       // nodeId -> {start, end, relocated, missing}
const PENDING_LOADS = new Map();     // path -> Promise

const IDB_NAME = 'codechakra-visualizer';
const IDB_STORE = 'handles';

let accessMode = 'none';             // 'served' | 'folder' | 'upload' | 'none'
let fetchBase = null;                // resolved prefix that works for fetch()
let directoryHandle = null;          // FileSystemDirectoryHandle
let uploadedFiles = null;            // Map<relativePath, File>

function isServed() {
  return location.protocol.indexOf('http') === 0;
}

/** Current access channel, for callers that render without waiting. */
function sourceAccessMode() {
  return accessMode;
}

/** Already-loaded source for an item, or null - never triggers a load. */
function cachedSymbolSource(item) {
  return (item && SYMBOL_SOURCE.get(item.id)) || null;
}

function projectName() {
  const root = (DATA.root || '').replace(/\/$/, '');
  return root.split('/').filter(Boolean).pop() || 'project';
}

// -------------------------------------------------------------
// Persisting the folder grant between sessions
// -------------------------------------------------------------
function idb() {
  return new Promise((resolve, reject) => {
    if (!window.indexedDB) { reject(new Error('no indexeddb')); return; }
    const req = indexedDB.open(IDB_NAME, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(IDB_STORE);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function idbPut(key, value) {
  return idb().then(db => new Promise((resolve, reject) => {
    const tx = db.transaction(IDB_STORE, 'readwrite');
    tx.objectStore(IDB_STORE).put(value, key);
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
  })).catch(() => {});
}

function idbGet(key) {
  return idb().then(db => new Promise((resolve, reject) => {
    const tx = db.transaction(IDB_STORE, 'readonly');
    const req = tx.objectStore(IDB_STORE).get(key);
    req.onsuccess = () => resolve(req.result || null);
    req.onerror = () => reject(req.error);
  })).catch(() => null);
}

// -------------------------------------------------------------
// Channel setup
// -------------------------------------------------------------
/** Probes fetch bases until one returns a real file. */
function probeServedAccess() {
  const probePath = (DATA.modules[0] || {}).path;
  if (!probePath) return Promise.resolve(false);

  const candidates = ['../', './', '/'];
  const attempt = (idx) => {
    if (idx >= candidates.length) return Promise.resolve(false);
    return fetch(candidates[idx] + probePath, { method: 'GET' })
      .then(res => {
        if (!res.ok) return attempt(idx + 1);
        return res.text().then(text => {
          // A dev server that answers everything with index.html is not access.
          if (text.indexOf('<!DOCTYPE html>') === 0) return attempt(idx + 1);
          fetchBase = candidates[idx];
          return true;
        });
      })
      .catch(() => attempt(idx + 1));
  };
  return attempt(0);
}

function restoreFolderAccess() {
  if (!window.showDirectoryPicker) return Promise.resolve(false);
  return idbGet(DATA.root || 'root').then(handle => {
    if (!handle || !handle.queryPermission) return false;
    return handle.queryPermission({ mode: 'read' }).then(state => {
      if (state !== 'granted') return false;
      directoryHandle = handle;
      return true;
    });
  }).catch(() => false);
}

function initSourceAccess() {
  const finish = (mode) => {
    accessMode = mode;
    updateSourceStatus();
    return mode;
  };

  if (isServed()) {
    return probeServedAccess().then(ok => finish(ok ? 'served' : 'none'));
  }
  return restoreFolderAccess().then(ok => finish(ok ? 'folder' : 'none'));
}

/** User gesture: pick the project directory and remember the grant. */
function connectProjectFolder() {
  if (window.showDirectoryPicker) {
    return window.showDirectoryPicker({ mode: 'read' }).then(handle => {
      directoryHandle = handle;
      accessMode = 'folder';
      SOURCE_CACHE.clear();
      RANGE_CACHE.clear();
      SYMBOL_SOURCE.clear();
      idbPut(DATA.root || 'root', handle);
      updateSourceStatus();
      refreshOpenViews();
    }).catch(() => {});
  }
  document.getElementById('source-folder-input').click();
  return Promise.resolve();
}

function adoptUploadedFiles(fileList) {
  uploadedFiles = new Map();
  Array.prototype.forEach.call(fileList, file => {
    const rel = (file.webkitRelativePath || file.name).split('/').slice(1).join('/');
    if (rel) uploadedFiles.set(rel, file);
  });
  accessMode = uploadedFiles.size ? 'upload' : 'none';
  SOURCE_CACHE.clear();
  RANGE_CACHE.clear();
  SYMBOL_SOURCE.clear();
  updateSourceStatus();
  refreshOpenViews();
}

// -------------------------------------------------------------
// Reading one file
// -------------------------------------------------------------
function readViaFetch(path) {
  return fetch(fetchBase + path).then(res => {
    if (!res.ok) throw new Error('HTTP ' + res.status);
    return res.text();
  });
}

function readViaHandle(path) {
  const parts = path.split('/').filter(Boolean);
  const fileName = parts.pop();
  let dir = Promise.resolve(directoryHandle);
  parts.forEach(segment => {
    dir = dir.then(d => d.getDirectoryHandle(segment));
  });
  return dir
    .then(d => d.getFileHandle(fileName))
    .then(fh => fh.getFile())
    .then(file => file.text());
}

function readViaUpload(path) {
  const file = uploadedFiles && uploadedFiles.get(path);
  if (!file) return Promise.reject(new Error('not in the selected folder'));
  return file.text();
}

/** Returns a promise for `{lines, language, error}` for one project file. */
function loadSourceFile(path) {
  if (!path) return Promise.resolve(null);
  if (SOURCE_CACHE.has(path)) return Promise.resolve(SOURCE_CACHE.get(path));
  if (PENDING_LOADS.has(path)) return PENDING_LOADS.get(path);

  let reader;
  if (accessMode === 'served') reader = readViaFetch(path);
  else if (accessMode === 'folder') reader = readViaHandle(path);
  else if (accessMode === 'upload') reader = readViaUpload(path);
  else return Promise.resolve(null);

  const pending = reader
    .then(text => {
      const entry = { lines: text.replace(/\r\n?/g, '\n').split('\n'), error: null };
      SOURCE_CACHE.set(path, entry);
      return entry;
    })
    .catch(err => {
      const entry = { lines: null, error: err.message || 'unreadable' };
      SOURCE_CACHE.set(path, entry);
      return entry;
    })
    .then(entry => {
      PENDING_LOADS.delete(path);
      return entry;
    });

  PENDING_LOADS.set(path, pending);
  return pending;
}

// -------------------------------------------------------------
// Re-resolving a symbol against live content
// -------------------------------------------------------------
const DECLARATION_START = /^\s*(?:@\w|(?:def|class|function|func|fn|type|interface|struct|enum|impl|trait|module|export|public|private|protected|internal|static|const|let|var|abstract|final|async|sub|package)\b)/;

const BRACE_LANGUAGES = new Set([
  'javascript', 'typescript', 'java', 'go', 'rust', 'c', 'cpp', 'csharp',
  'kotlin', 'swift', 'scala', 'php', 'dart', 'groovy'
]);

function escapeRegExp(str) {
  return String(str).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function declaresSymbol(line, name) {
  if (!name) return false;
  if (!new RegExp('\\b' + escapeRegExp(name) + '\\b').test(line)) return false;
  if (DECLARATION_START.test(line)) return true;
  return new RegExp('^\\s*' + escapeRegExp(name) + '\\s*[:=]').test(line);
}

function findDeclaration(lines, name) {
  if (!name) return 0;
  const strong = new RegExp(
    '^\\s*(?:async\\s+)?(?:def|class|function|func|fn|interface|struct|type|enum)\\s+' +
    escapeRegExp(name) + '\\b'
  );
  for (let i = 0; i < lines.length; i++) {
    if (strong.test(lines[i])) return i + 1;
  }
  for (let i = 0; i < lines.length; i++) {
    if (declaresSymbol(lines[i], name)) return i + 1;
  }
  return 0;
}

function indentOf(line) {
  return line.length - line.replace(/^\s+/, '').length;
}

function findEndByIndent(lines, startIdx) {
  const headerIndent = indentOf(lines[startIdx]);
  let idx = startIdx;
  let depth = 0;

  while (idx < lines.length) {
    const line = lines[idx];
    depth += (line.split('(').length - 1) + (line.split('[').length - 1) + (line.split('{').length - 1);
    depth -= (line.split(')').length - 1) + (line.split(']').length - 1) + (line.split('}').length - 1);
    if (depth <= 0) break;
    idx++;
  }

  let end = idx + 1;
  for (let i = idx + 1; i < lines.length; i++) {
    if (!lines[i].trim()) continue;
    if (indentOf(lines[i]) <= headerIndent) return end;
    end = i + 1;
  }
  return end;
}

function findEndByBraces(lines, startIdx) {
  let depth = 0;
  let opened = false;
  for (let i = startIdx; i < lines.length; i++) {
    for (let c = 0; c < lines[i].length; c++) {
      const ch = lines[i].charAt(c);
      if (ch === '{') { depth++; opened = true; }
      else if (ch === '}') { depth--; }
    }
    if (opened && depth <= 0) return i + 1;
    if (!opened && i > startIdx && lines[i].trim().slice(-1) === ';') return i + 1;
  }
  return Math.min(lines.length, startIdx + 24);
}

function leadingContext(lines, startIdx) {
  let idx = startIdx;
  while (idx > 0) {
    const prev = lines[idx - 1].trim();
    if (prev.charAt(0) === '@' || prev.charAt(0) === '#' || prev.indexOf('//') === 0) {
      idx--;
      continue;
    }
    break;
  }
  return idx;
}

/**
 * Confirms (or re-finds) the symbol's range in freshly loaded content.
 * The payload's range is a hint from generation time; the file is the truth.
 */
function resolveRange(item, lines) {
  const name = item.name || '';
  let startLine = 0;
  let relocated = false;

  if (item.code_start && item.code_start <= lines.length &&
      (!name || declaresSymbol(lines[item.code_start - 1], name))) {
    startLine = item.code_start;
  } else {
    startLine = findDeclaration(lines, name);
    relocated = !!startLine && startLine !== item.code_start;
  }

  if (!startLine) return null;

  const startIdx = leadingContext(lines, startLine - 1);
  let endIdx = BRACE_LANGUAGES.has(item.language)
    ? findEndByBraces(lines, startLine - 1)
    : findEndByIndent(lines, startLine - 1);

  endIdx = Math.max(endIdx, startLine);
  while (endIdx > startIdx && !lines[endIdx - 1].trim()) endIdx--;
  if (endIdx <= startIdx) return null;

  return { start: startIdx + 1, end: endIdx, relocated: relocated };
}

/**
 * Resolves an item to `{lines, start, end, relocated}` of live source, or a
 * `{unavailable}` marker explaining why not.
 */
function loadSymbolSource(item) {
  if (!item || !item.path) return Promise.resolve({ unavailable: 'no-file' });
  if (accessMode === 'none') return Promise.resolve({ unavailable: 'no-access' });

  return loadSourceFile(item.path).then(entry => {
    if (!entry || !entry.lines) {
      return { unavailable: 'unreadable', error: entry && entry.error };
    }
    if (isModuleId(item.id)) {
      const whole = {
        lines: entry.lines,
        start: 1,
        end: entry.lines.length,
        relocated: false,
        whole: true
      };
      SYMBOL_SOURCE.set(item.id, whole);
      return whole;
    }

    const range = RANGE_CACHE.get(item.id) || resolveRange(item, entry.lines);
    if (!range) return { unavailable: 'not-found' };
    RANGE_CACHE.set(item.id, range);

    const resolved = {
      lines: entry.lines.slice(range.start - 1, range.end),
      start: range.start,
      end: range.end,
      relocated: range.relocated
    };
    SYMBOL_SOURCE.set(item.id, resolved);
    return resolved;
  });
}

// -------------------------------------------------------------
// Access status chip in the header
// -------------------------------------------------------------
function updateSourceStatus() {
  const chip = document.getElementById('source-status');
  const button = document.getElementById('btn-connect-source');
  if (!chip || !button) return;

  const labels = {
    served: 'Source: served',
    folder: 'Source: ' + projectName(),
    upload: 'Source: ' + projectName() + ' (uploaded)',
    none: 'Source: not connected'
  };

  chip.textContent = labels[accessMode] || labels.none;
  chip.classList.toggle('connected', accessMode !== 'none');
  button.style.display = (accessMode === 'served') ? 'none' : 'flex';
  button.textContent = accessMode === 'none' ? 'Connect project' : 'Change folder';
}

/** Re-renders whatever is currently showing code after access changes. */
function refreshOpenViews() {
  const item = selectedId ? getItem(selectedId) : null;
  if (item) {
    populateCodeSection(item);
    if (item.path) {
      loadSymbolSource(item).then(() => {
        if (focusId === item.id) applyFocusLayout();
      });
    }
  }
  if (fileViewerPath) openFileViewer(fileViewerPath, fileViewerHighlight);
}

// -------------------------------------------------------------
// Full file viewer
// -------------------------------------------------------------
let fileViewerPath = null;
let fileViewerHighlight = null;

function fileSymbols(path) {
  const mod = DATA.modules.find(m => m.path === path);
  if (!mod) return [];
  return (mod.subnodes || [])
    .slice()
    .sort((a, b) => (a.code_start || 0) - (b.code_start || 0));
}

function openFileViewer(path, highlight) {
  if (!path) return;
  fileViewerPath = path;
  fileViewerHighlight = highlight || null;

  const overlay = document.getElementById('file-viewer');
  const body = document.getElementById('file-viewer-body');
  const outline = document.getElementById('file-viewer-outline');

  overlay.classList.add('visible');
  document.getElementById('file-viewer-path').textContent = path;
  document.getElementById('file-viewer-meta').textContent = 'loading...';
  body.innerHTML = '<div class="viewer-placeholder">Reading file...</div>';
  outline.innerHTML = '';

  const openLink = document.getElementById('file-viewer-open');
  openLink.href = 'vscode://file' + (DATA.root || '').replace(/\/$/, '') + '/' + path +
                  ':' + ((highlight && highlight.start) || 1);

  if (accessMode === 'none') {
    body.innerHTML = '<div class="viewer-placeholder">' +
      'Connect the project folder to read files, or run ' +
      '<code class="md-code">codechakra ui --serve</code> to serve them.</div>';
    document.getElementById('file-viewer-meta').textContent = 'no access';
    return;
  }

  loadSourceFile(path).then(entry => {
    if (fileViewerPath !== path) return;   // user moved on while loading

    if (!entry || !entry.lines) {
      body.innerHTML = '<div class="viewer-placeholder">Could not read this file' +
        (entry && entry.error ? ': ' + escapeHtml(entry.error) : '') + '.</div>';
      document.getElementById('file-viewer-meta').textContent = 'unreadable';
      return;
    }

    const language = (DATA.modules.find(m => m.path === path) || {}).language || 'plain';
    const highlighted = highlightCode(entry.lines.join('\n'), language);
    const from = highlight ? highlight.start : 0;
    const to = highlight ? highlight.end : -1;

    document.getElementById('file-viewer-meta').textContent =
      entry.lines.length + ' lines - ' + language;

    body.innerHTML = '<table class="code-table">' + highlighted.map((line, idx) => {
      const num = idx + 1;
      const active = num >= from && num <= to ? ' class="code-row-active"' : '';
      return '<tr' + active + ' id="vline-' + num + '">' +
        '<td class="code-gutter">' + num + '</td>' +
        '<td class="code-line">' + (line || ' ') + '</td></tr>';
    }).join('') + '</table>';

    const symbols = fileSymbols(path);
    outline.innerHTML = symbols.length
      ? symbols.map(s => {
          const range = RANGE_CACHE.get(s.id);
          const line = (range && range.start) || s.code_start || 0;
          const isActive = highlight && line === highlight.start;
          return '<div class="outline-item' + (isActive ? ' active' : '') + '" ' +
            'data-line="' + line + '" data-node="' + escapeHtml(s.id) + '">' +
            '<span class="outline-name">' + escapeHtml(s.display_label || s.label) + '</span>' +
            '<span class="outline-line">' + (line ? 'L' + line : '') + '</span>' +
          '</div>';
        }).join('')
      : '<div class="viewer-placeholder small">No indexed symbols in this file.</div>';

    if (highlight && highlight.start) scrollViewerToLine(highlight.start);
  });
}

function scrollViewerToLine(line) {
  const row = document.getElementById('vline-' + line);
  const body = document.getElementById('file-viewer-body');
  if (!row || !body) return;
  body.scrollTop = Math.max(0, row.offsetTop - body.clientHeight / 3);
}

function closeFileViewer() {
  fileViewerPath = null;
  fileViewerHighlight = null;
  document.getElementById('file-viewer').classList.remove('visible');
}

function openFileForSelection() {
  const item = selectedId ? getItem(selectedId) : null;
  if (!item || !item.path) return;
  const range = RANGE_CACHE.get(item.id);
  const highlight = isModuleId(item.id)
    ? null
    : { start: (range && range.start) || item.code_start, end: (range && range.end) || item.code_end };
  openFileViewer(item.path, highlight && highlight.start ? highlight : null);
}
