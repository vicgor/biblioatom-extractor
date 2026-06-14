// ==UserScript==
// @name         BiblioAtom CP Extractor Final
// @namespace    http://tampermonkey.net/
// @version      1.3.0
// @description  Выгрузка текста книги через /rpc/bookviewer/cp/ в режимах Text / HTML / Raw JSON
// @match        https://elib.biblioatom.ru/text/*
// @grant        GM_download
// @grant        GM_setClipboard
// @connect      elib.biblioatom.ru
// @run-at       document-idle
// ==/UserScript==

(function () {
  'use strict';

  const CFG = {
    delayMs: 250,
    timeoutMs: 20000,
    retries: 3,
    panelWidth: 420,
    title: 'BiblioAtom CP Extractor',
    fallbackMaxPage: 545
  };

  const STATE = {
    running: false,
    abort: false,
    bookId: '',
    bookTitle: '',
    from: 0,
    to: 0,
    mode: 'text', // text | html | json
    items: [],
    errors: [],
    finalContent: '',
    finalFilename: ''
  };

  function qs(sel, root = document) {
    return root.querySelector(sel);
  }

  function qsa(sel, root = document) {
    return Array.from(root.querySelectorAll(sel));
  }

  function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  function safeJsonParse(value, fallback = null) {
    try {
      return JSON.parse(value);
    } catch {
      return fallback;
    }
  }

  function sanitizeFilename(name) {
    return String(name || 'output')
      .replace(/[\\/:*?"<>|]+/g, '_')
      .replace(/\s+/g, '_')
      .replace(/_+/g, '_')
      .replace(/^_+|_+$/g, '');
  }

  function extractBookId() {
    const m = location.pathname.match(/^\/text\/([^/]+)/);
    if (m) return m[1];

    const root = qs('.bookviewer-root');
    if (root) {
      const ds = root.getAttribute('data-settings') || '';
      const m2 = ds.match(/"url"\s*"\s*([a-zA-Z0-9_]+)\s*"/);
      if (m2) return m2[1];
    }

    return 'unknown_book';
  }

  function extractBookTitle() {
    return (document.title || extractBookId())
      .replace(/\s+\/\s+Просмотр.*$/i, '')
      .trim();
  }

  function detectMaxDataRel() {
    const rels1 = qsa('.page-gfx[data-rel]')
      .map(el => Number(el.getAttribute('data-rel')))
      .filter(v => Number.isFinite(v));

    if (rels1.length) return Math.max(...rels1);

    const rels2 = qsa('.nav-page[data-rel]')
      .map(el => Number(el.getAttribute('data-rel')))
      .filter(v => Number.isFinite(v));

    if (rels2.length) return Math.max(...rels2);

    return CFG.fallbackMaxPage;
  }

  function buildCpUrl(bookId, page) {
    return `${location.origin}/rpc/bookviewer/cp/?url=${encodeURIComponent(bookId)}&page=${encodeURIComponent(page)}`;
  }

  function decodeHtmlEntities(str) {
    const el = document.createElement('textarea');
    el.innerHTML = String(str ?? '');
    return el.value;
  }

  function htmlToText(html) {
    const doc = new DOMParser().parseFromString(String(html), 'text/html');
    return (doc.body?.innerText || doc.documentElement?.innerText || '').trim();
  }

  function stripServiceMarkers(text) {
    let s = String(text ?? '');

    s = s.replace(/\r/g, '');
    s = s.replace(/\u00A0/g, ' ');
    s = s.replace(/\u200B/g, '');
    s = s.replace(/\u200C/g, '');
    s = s.replace(/\u200D/g, '');
    s = s.replace(/\uFEFF/g, '');

    const patterns = [
      /Загрузка текста издания\.{0,3}/gi,
      /Текст страницы скопирован\.?/gi,
      /Скопировать текст страницы/gi,
      /Предыдущая страница/gi,
      /Следующая страница/gi,
      /Перейти к странице/gi,
      /Найдено страниц\s*[-—]?\s*\d+/gi,
      /Страница\s+\d+\s+из\s+\d+/gi
    ];

    for (const re of patterns) {
      s = s.replace(re, ' ');
    }

    s = s.replace(/[ \t]+\n/g, '\n');
    s = s.replace(/\n{3,}/g, '\n\n');
    s = s.replace(/[ \t]{2,}/g, ' ');
    return s.trim();
  }

  function extractTextFromUnknownPayload(rawText) {
    const parsed = safeJsonParse(rawText, null);

    if (parsed && typeof parsed === 'object') {
      for (const key of ['text', 'content', 'html', 'data', 'result']) {
        const val = parsed[key];
        if (typeof val === 'string' && val.trim()) {
          const decoded = decodeHtmlEntities(val);
          const maybeText = /<[a-z][\s\S]*>/i.test(decoded) ? htmlToText(decoded) : decoded;
          return stripServiceMarkers(maybeText);
        }
      }
      return stripServiceMarkers(JSON.stringify(parsed, null, 2));
    }

    const decoded = decodeHtmlEntities(rawText);
    if (/<[a-z][\s\S]*>/i.test(decoded)) {
      return stripServiceMarkers(htmlToText(decoded));
    }

    return stripServiceMarkers(decoded);
  }

  function cleanHtmlFragment(rawText) {
    const parsed = safeJsonParse(rawText, null);

    if (parsed && typeof parsed === 'object') {
      for (const key of ['html', 'content', 'text', 'data', 'result']) {
        const val = parsed[key];
        if (typeof val === 'string' && val.trim()) {
          return decodeHtmlEntities(val).trim();
        }
      }
      return `<pre>${escapeHtml(JSON.stringify(parsed, null, 2))}</pre>`;
    }

    return decodeHtmlEntities(rawText).trim();
  }

  function escapeHtml(str) {
    return String(str ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function normalizeResponse(raw, mode) {
    const rawText = String(raw ?? '');

    if (mode === 'json') {
      const parsed = safeJsonParse(rawText, null);
      if (parsed !== null) {
        return JSON.stringify(parsed, null, 2);
      }
      return JSON.stringify({
        response_type: 'non_json',
        raw: rawText
      }, null, 2);
    }

    if (mode === 'html') {
      return cleanHtmlFragment(rawText);
    }

    return extractTextFromUnknownPayload(rawText);
  }

  async function fetchWithTimeout(url, options = {}, timeoutMs = CFG.timeoutMs) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);

    try {
      return await fetch(url, {
        ...options,
        signal: ctrl.signal,
        credentials: 'include'
      });
    } finally {
      clearTimeout(timer);
    }
  }

  async function fetchPage(bookId, page, mode) {
    const url = buildCpUrl(bookId, page);
    let lastError = null;

    for (let attempt = 1; attempt <= CFG.retries; attempt++) {
      try {
        const resp = await fetchWithTimeout(url, { method: 'GET' }, CFG.timeoutMs);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

        const raw = await resp.text();
        const content = normalizeResponse(raw, mode);

        return {
          ok: true,
          page,
          raw,
          content
        };
      } catch (err) {
        lastError = err;
        await sleep(250 * attempt);
      }
    }

    return {
      ok: false,
      page,
      error: String(lastError || 'unknown error')
    };
  }

  function extensionForMode(mode) {
    if (mode === 'html') return 'html';
    if (mode === 'json') return 'json';
    return 'txt';
  }

  function mimeForMode(mode) {
    if (mode === 'html') return 'text/html;charset=utf-8';
    if (mode === 'json') return 'application/json;charset=utf-8';
    return 'text/plain;charset=utf-8';
  }

  function buildTextOutput(items, meta) {
    const { title, bookId, from, to } = meta;
    const out = [];

    out.push(`# ${title}`);
    out.push(`book_id: ${bookId}`);
    out.push(`source: ${location.origin}/text/${bookId}/`);
    out.push(`page_range: ${from}-${to}`);
    out.push(`mode: text`);
    out.push(`generated_at: ${new Date().toISOString()}`);
    out.push('');

    for (const item of items) {
      out.push(`===== PAGE ${item.page} =====`);
      if (item.error) {
        out.push(`[ERROR] ${item.error}`);
      } else {
        out.push(item.content || '');
      }
      out.push('');
    }

    return out.join('\n');
  }

  function buildJsonOutput(items, meta) {
    const { title, bookId, from, to } = meta;
    return JSON.stringify({
      title,
      book_id: bookId,
      source: `${location.origin}/text/${bookId}/`,
      page_range: [from, to],
      mode: 'json',
      generated_at: new Date().toISOString(),
      items
    }, null, 2);
  }

  function buildHtmlDocument(items, meta) {
    const { title, bookId, from, to } = meta;

    const sections = items.map(item => {
      if (item.error) {
        return `
<section class="page error" data-page="${item.page}">
  <h2>Page ${item.page}</h2>
  <pre>${escapeHtml(item.error)}</pre>
</section>`;
      }

      let body = item.content || '';

      if (!/<[a-z][\s\S]*>/i.test(body.trim())) {
        body = `<pre>${escapeHtml(body)}</pre>`;
      }

      return `
<section class="page" data-page="${item.page}">
  <h2>Page ${item.page}</h2>
  <div class="page-body">
    ${body}
  </div>
</section>`;
    }).join('\n');

    return `<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>${escapeHtml(title)}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    :root {
      --bg: #f5f5f5;
      --card: #ffffff;
      --text: #111111;
      --muted: #666666;
      --border: #dddddd;
      --error: #7a1f1f;
    }
    html, body {
      margin: 0;
      padding: 0;
      background: var(--bg);
      color: var(--text);
      font: 16px/1.55 system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif;
    }
    main {
      max-width: 980px;
      margin: 0 auto;
      padding: 24px;
    }
    header {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 20px;
      margin-bottom: 20px;
    }
    header h1 {
      margin: 0 0 12px;
      font-size: 28px;
      line-height: 1.2;
    }
    header .meta {
      color: var(--muted);
      font-size: 14px;
    }
    .page {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 18px 20px;
      margin-bottom: 18px;
    }
    .page.error {
      border-color: #e4b3b3;
      background: #fff7f7;
      color: var(--error);
    }
    .page h2 {
      margin: 0 0 14px;
      font-size: 20px;
      line-height: 1.2;
    }
    .page-body {
      overflow-wrap: break-word;
    }
    .page-body pre {
      white-space: pre-wrap;
      word-break: break-word;
      margin: 0;
      font: 15px/1.55 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }
    .page-body img {
      max-width: 100%;
      height: auto;
    }
    .page-body table {
      border-collapse: collapse;
      width: 100%;
    }
    .page-body td, .page-body th {
      border: 1px solid var(--border);
      padding: 6px 8px;
    }
  </style>
</head>
<body>
  <main>
    <header>
      <h1>${escapeHtml(title)}</h1>
      <div class="meta">
        <div>book_id: ${escapeHtml(bookId)}</div>
        <div>source: ${escapeHtml(`${location.origin}/text/${bookId}/`)}</div>
        <div>page_range: ${from}-${to}</div>
        <div>mode: html</div>
        <div>generated_at: ${escapeHtml(new Date().toISOString())}</div>
      </div>
    </header>
    ${sections}
  </main>
</body>
</html>`;
  }

  function buildFinalOutput(mode, items, meta) {
    if (mode === 'json') return buildJsonOutput(items, meta);
    if (mode === 'html') return buildHtmlDocument(items, meta);
    return buildTextOutput(items, meta);
  }

  function downloadFile(filename, content, mimeType) {
    const blob = new Blob([content], { type: mimeType });
    const blobUrl = URL.createObjectURL(blob);

    if (typeof GM_download === 'function') {
      GM_download({
        url: blobUrl,
        name: filename,
        saveAs: true,
        onload: () => URL.revokeObjectURL(blobUrl),
        onerror: () => URL.revokeObjectURL(blobUrl),
        ontimeout: () => URL.revokeObjectURL(blobUrl)
      });
      return;
    }

    const a = document.createElement('a');
    a.href = blobUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();

    setTimeout(() => URL.revokeObjectURL(blobUrl), 3000);
  }

  function copyToClipboard(text) {
    if (typeof GM_setClipboard === 'function') {
      GM_setClipboard(text, 'text');
      return;
    }
    navigator.clipboard?.writeText?.(text).catch(() => {});
  }

  function log(msg) {
    const box = qs('#ba-log');
    if (!box) return;
    const time = new Date().toLocaleTimeString();
    box.value += `[${time}] ${msg}\n`;
    box.scrollTop = box.scrollHeight;
  }

  function setStatus(text, value = 0, max = 100) {
    const status = qs('#ba-status');
    const progress = qs('#ba-progress');
    if (status) status.textContent = text;
    if (progress) {
      progress.max = max;
      progress.value = value;
    }
  }

  function updateButtons() {
    const hasData = !!STATE.finalContent;
    qs('#ba-save').disabled = !hasData;
    qs('#ba-copy').disabled = !hasData;
  }

  function buildUi() {
    if (qs('#ba-panel')) return;

    const style = document.createElement('style');
    style.textContent = `
      #ba-panel {
        position: fixed;
        top: 12px;
        right: 12px;
        z-index: 2147483647;
        width: ${CFG.panelWidth}px;
        background: rgba(18,20,24,0.97);
        color: #f5f7fa;
        border: 1px solid rgba(255,255,255,0.15);
        border-radius: 12px;
        box-shadow: 0 12px 30px rgba(0,0,0,.38);
        padding: 12px;
        font: 13px/1.45 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Arial,sans-serif;
      }
      #ba-panel * { box-sizing: border-box; }
      #ba-title { font-weight: 700; margin-bottom: 10px; }
      .ba-row {
        display: grid;
        grid-template-columns: 96px 1fr;
        gap: 8px;
        align-items: center;
        margin-bottom: 8px;
      }
      .ba-actions, .ba-actions-2 {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 8px;
        margin-top: 10px;
      }
      #ba-panel input,
      #ba-panel select,
      #ba-panel button,
      #ba-panel textarea {
        width: 100%;
        border-radius: 8px;
        border: 1px solid rgba(255,255,255,.14);
        background: rgba(255,255,255,.06);
        color: #fff;
        padding: 8px 9px;
      }
      #ba-panel button {
        cursor: pointer;
        font-weight: 600;
      }
      #ba-panel button:hover {
        background: rgba(255,255,255,.12);
      }
      #ba-panel button:disabled {
        opacity: .5;
        cursor: not-allowed;
      }
      #ba-log {
        margin-top: 10px;
        min-height: 200px;
        resize: vertical;
        font-family: ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
      }
      #ba-progress {
        width: 100%;
        margin-top: 4px;
      }
      #ba-status {
        margin-top: 10px;
        font-size: 12px;
        opacity: .92;
      }
    `;

    const panel = document.createElement('div');
    panel.id = 'ba-panel';
    panel.innerHTML = `
      <div id="ba-title">${CFG.title}</div>

      <div class="ba-row">
        <label for="ba-book-id">Book ID</label>
        <input id="ba-book-id" type="text">
      </div>

      <div class="ba-row">
        <label for="ba-from">From</label>
        <input id="ba-from" type="number" min="0" step="1">
      </div>

      <div class="ba-row">
        <label for="ba-to">To</label>
        <input id="ba-to" type="number" min="0" step="1">
      </div>

      <div class="ba-row">
        <label for="ba-mode">Mode</label>
        <select id="ba-mode">
          <option value="text">Text</option>
          <option value="html">HTML</option>
          <option value="json">Raw JSON</option>
        </select>
      </div>

      <div class="ba-row">
        <label for="ba-delay">Delay ms</label>
        <input id="ba-delay" type="number" min="0" step="50" value="${CFG.delayMs}">
      </div>

      <div class="ba-actions">
        <button id="ba-start">Старт</button>
        <button id="ba-stop">Стоп</button>
      </div>

      <div class="ba-actions-2">
        <button id="ba-save" disabled>Сохранить файл</button>
        <button id="ba-copy" disabled>Копировать</button>
      </div>

      <div id="ba-status">Ready</div>
      <progress id="ba-progress" value="0" max="100"></progress>
      <textarea id="ba-log" spellcheck="false"></textarea>
    `;

    document.documentElement.appendChild(style);
    document.body.appendChild(panel);
  }

  function initForm() {
    STATE.bookId = extractBookId();
    STATE.bookTitle = extractBookTitle();
    STATE.from = 0;
    STATE.to = detectMaxDataRel();

    qs('#ba-book-id').value = STATE.bookId;
    qs('#ba-from').value = STATE.from;
    qs('#ba-to').value = STATE.to;

    log(`Ready`);
    log(`bookId=${STATE.bookId}`);
    log(`auto To from max data-rel = ${STATE.to}`);
    setStatus(`Ready. To=${STATE.to}`, 0, 100);
    updateButtons();
  }

  async function runExtraction() {
    if (STATE.running) return;

    const bookId = qs('#ba-book-id').value.trim() || extractBookId();
    const from = Number(qs('#ba-from').value);
    const to = Number(qs('#ba-to').value);
    const mode = qs('#ba-mode').value;
    const delayMs = Number(qs('#ba-delay').value) || 0;

    if (!Number.isFinite(from) || !Number.isFinite(to) || from > to) {
      alert('Неверный диапазон страниц');
      return;
    }

    STATE.running = true;
    STATE.abort = false;
    STATE.bookId = bookId;
    STATE.bookTitle = extractBookTitle();
    STATE.from = from;
    STATE.to = to;
    STATE.mode = mode;
    STATE.items = [];
    STATE.errors = [];
    STATE.finalContent = '';
    STATE.finalFilename = '';
    updateButtons();

    const total = to - from + 1;
    log(`Start mode=${mode}, range=${from}..${to}`);

    for (let i = 0, page = from; page <= to; page++, i++) {
      if (STATE.abort) {
        log('Stopped by user');
        break;
      }

      setStatus(`Page ${page} (${i + 1}/${total})`, i + 1, total);

      const result = await fetchPage(bookId, page, mode);
      if (!result.ok) {
        STATE.errors.push({ page, error: result.error });
        STATE.items.push({
          page,
          error: result.error,
          content: ''
        });
        log(`ERROR page ${page}: ${result.error}`);
      } else {
        STATE.items.push({
          page,
          content: result.content
        });
        log(`OK page ${page}, chars=${result.content.length}`);
      }

      if (delayMs > 0) {
        await sleep(delayMs);
      }
    }

    STATE.items.sort((a, b) => a.page - b.page);

    STATE.finalContent = buildFinalOutput(mode, STATE.items, {
      title: STATE.bookTitle,
      bookId: STATE.bookId,
      from: STATE.from,
      to: STATE.to
    });

    const ext = extensionForMode(mode);
    STATE.finalFilename = sanitizeFilename(`${STATE.bookTitle}_${STATE.bookId}_${STATE.from}-${STATE.to}.${ext}`);

    updateButtons();

    if (!STATE.abort) {
      downloadFile(STATE.finalFilename, STATE.finalContent, mimeForMode(mode));
      log(`Saved: ${STATE.finalFilename}`);
    }

    setStatus(`Done. ok=${STATE.items.filter(x => !x.error).length}, err=${STATE.errors.length}`, total, total);
    log(`Done. ok=${STATE.items.filter(x => !x.error).length}, err=${STATE.errors.length}`);

    STATE.running = false;
    STATE.abort = false;
  }

  function saveFinalFile() {
    if (!STATE.finalContent) {
      alert('Пока нечего сохранять');
      return;
    }
    downloadFile(STATE.finalFilename, STATE.finalContent, mimeForMode(STATE.mode));
    log(`Saved existing result: ${STATE.finalFilename}`);
  }

  function copyFinalContent() {
    if (!STATE.finalContent) {
      alert('Пока нечего копировать');
      return;
    }
    copyToClipboard(STATE.finalContent);
    log('Copied final content to clipboard');
  }

  function bindUi() {
    qs('#ba-start').addEventListener('click', runExtraction);
    qs('#ba-stop').addEventListener('click', () => {
      STATE.abort = true;
    });
    qs('#ba-save').addEventListener('click', saveFinalFile);
    qs('#ba-copy').addEventListener('click', copyFinalContent);
  }

  function init() {
    buildUi();
    initForm();
    bindUi();
  }

  init();
})();
