/* ============================================================
   app.js — Doctor's UI frontend logic
   Talks to FastAPI endpoints at /api/*
   ============================================================ */

const API = '';   // same origin

// ----------------------------------------------------------------
// Icon set — small inline SVGs, reused across static and
// dynamically-rendered content so the whole app shares one
// consistent line-icon language.
// ----------------------------------------------------------------
const ICONS = {
  file:     '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M6 2h9l5 5v15H6V2Z"/><path d="M15 2v5h5"/></svg>',
  sparkles: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l1.5 4.5L18 9l-4.5 1.5L12 15l-1.5-4.5L6 9l4.5-1.5L12 3Z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8L19 15Z"/></svg>',
  flask:    '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 2.5h6M10 2.5v5.7L4.8 18.4A2 2 0 0 0 6.6 21h10.8a2 2 0 0 0 1.8-2.6L14 8.2V2.5"/></svg>',
  check:    '<svg class="icon toast-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><polyline points="8,12.5 11,15.5 16,9"/></svg>',
  alert:    '<svg class="icon toast-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3 2 20h20L12 3Z"/><line x1="12" y1="9" x2="12" y2="13.5"/><circle cx="12" cy="16.7" r="0.75" fill="currentColor" stroke="none"/></svg>',
  info:     '<svg class="icon toast-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><line x1="12" y1="11" x2="12" y2="16"/><circle cx="12" cy="8.2" r="0.75" fill="currentColor" stroke="none"/></svg>',
};

// The signature traveling pulse trace, used only for "the system is
// thinking" moments — the chat typing indicator and modal loading state.
function pulseTraceSvg(fast) {
  return `<span class="pulse-trace${fast ? ' fast' : ''}">
    <svg viewBox="0 0 120 24">
      <path class="pulse-line" d="M0,12 L10,12 L14,4 L19,20 L24,3 L29,20 L34,12 L46,12 L50,6 L55,17 L60,12 L72,12 L76,4 L81,20 L86,3 L91,20 L96,12 L120,12" />
    </svg>
  </span>`;
}

// Gemini's responses come back as markdown (**bold**, "* bullet" lists).
// This renders just those two constructs — never raw HTML — so text
// stays escaped/safe while still reading properly instead of showing
// literal asterisks. Anything fancier than bold + bullets is left as
// plain text on purpose, to keep this simple and safe.
function renderMarkdownLite(str) {
  let html = escHtml(str);
  html = html.replace(/^\*(?!\*)\s+/gm, '• ');            // "* item"  -> "• item"
  html = html.replace(/\*\*([^*]+?)\*\*/g, '<strong>$1</strong>'); // **bold** -> <strong>
  return html;
}

// ----------------------------------------------------------------
// State
// ----------------------------------------------------------------
let documents = [];       // cached doc list
let activePanel = 'chat'; // 'chat' | 'docs' | 'analyze'

// ----------------------------------------------------------------
// DOM refs
// ----------------------------------------------------------------
const chatMessages    = document.getElementById('chat-messages');
const chatInput       = document.getElementById('chat-input');
const sendBtn         = document.getElementById('send-btn');
const docsGrid        = document.getElementById('docs-grid');
const docCount        = document.getElementById('doc-count');
const docsEmpty       = document.getElementById('docs-empty');
const uploadZone      = document.getElementById('upload-zone');
const fileInput       = document.getElementById('file-input');
const uploadProgress  = document.getElementById('upload-progress');
const progressBar     = document.getElementById('progress-bar');
const uploadStatus    = document.getElementById('upload-status');
const analyzeDocSel   = document.getElementById('analyze-doc-select');
const analyzeQuestion = document.getElementById('analyze-question');
const summarizeQuestion = document.getElementById('summarize-question');
const analyzeBtn      = document.getElementById('analyze-btn');
const summarizeBtn    = document.getElementById('summarize-btn');
const analyzeResult   = document.getElementById('analyze-result');
const analyzeResultContent = document.getElementById('analyze-result-content');
const summarizeResult = document.getElementById('summarize-result');
const summarizeResultContent = document.getElementById('summarize-result-content');
const toastContainer  = document.getElementById('toast-container');
const localDot        = document.getElementById('local-dot');
const cloudDot        = document.getElementById('cloud-dot');
const localStatus     = document.getElementById('local-status');
const cloudStatus     = document.getElementById('cloud-status');
const modalOverlay    = document.getElementById('modal-overlay');
const modalTitle      = document.getElementById('modal-title');
const modalBody       = document.getElementById('modal-body');
const topbarTitle     = document.getElementById('topbar-title');
const topbarSub       = document.getElementById('topbar-sub');

// ----------------------------------------------------------------
// Navigation
// ----------------------------------------------------------------
const panelTitles = {
  chat:    { title: 'Clinical Q&A',      sub: 'Ask questions about your patient documents' },
  docs:    { title: 'Document Library',  sub: 'Manage and search your medical documents' },
  analyze: { title: 'Deep Analysis',     sub: 'Run structured analysis over full documents' },
};

document.querySelectorAll('.nav-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const target = btn.dataset.panel;
    if (!target) return;
    switchPanel(target);
  });
});

function switchPanel(name) {
  activePanel = name;

  document.querySelectorAll('.nav-btn').forEach(btn => {
    const isActive = btn.dataset.panel === name;
    btn.classList.toggle('active', isActive);

    if (isActive) {
      btn.setAttribute('aria-current', 'page');
    } else {
      btn.removeAttribute('aria-current');
    }
  });

  document.querySelectorAll('.panel').forEach(panel => {
    panel.classList.toggle('active', panel.id === `panel-${name}`);
  });

  topbarTitle.textContent = panelTitles[name].title;
  topbarSub.textContent = panelTitles[name].sub;
}

// ----------------------------------------------------------------
// Health check — runs on load and every 30s
// ----------------------------------------------------------------
async function pollHealth() {
  try {
    const res = await fetch(`${API}/api/health`);
    if (!res.ok) throw new Error();
    const data = await res.json();
    setStatus(localDot, localStatus, data.local_ok, data.local_message);
    setStatus(cloudDot, cloudStatus, data.cloud_ok, data.cloud_message);
  } catch {
    setStatus(localDot, localStatus, false, 'Unreachable');
    setStatus(cloudDot, cloudStatus, false, 'Unreachable');
  }
}

function setStatus(dot, label, ok, message) {
  dot.className = 'status-dot ' + (ok ? 'ok' : 'error');
  dot.title = message;
  label.textContent = ok ? 'Online' : 'Offline';
}

// ----------------------------------------------------------------
// Chat
// ----------------------------------------------------------------
function addMessage(role, content, meta = null) {
  // Remove empty state if present
  const empty = chatMessages.querySelector('.chat-empty');
  if (empty) empty.remove();

  const el = document.createElement('div');
  el.className = `msg msg-${role}`;

  if (role === 'question') {
    el.innerHTML = `<div class="msg-bubble">${escHtml(content)}</div>`;
  } else if (role === 'typing') {
    el.className = 'msg msg-answer typing-indicator';
    el.id = 'typing-indicator';
    el.innerHTML = `<div class="msg-bubble">${pulseTraceSvg(true)}</div>`;
  } else {
    // Answer
    const localOk = meta && meta.local && meta.local.ok;
    const sourceBadge = meta
      ? `<span class="source-badge ${meta.used_cloud ? (localOk ? 'both' : 'cloud') : 'local'}">
           ${meta.used_cloud ? (localOk ? 'Both' : 'Cloud') : 'Local'}
         </span>`
      : '';


    const latency = meta
      ? `<span class="latency-chip">
           local ${(meta.local.latency_seconds * 1000).toFixed(0)}ms
           &nbsp;|&nbsp;
           cloud ${meta.cloud.ok ? (meta.cloud.latency_seconds).toFixed(2) + 's' : 'N/A'}
         </span>`
      : '';

    el.innerHTML = `
      <div class="msg-bubble">${renderMarkdownLite(content)}</div>
      <div class="msg-meta">${sourceBadge}${latency}</div>
    `;
  }

  chatMessages.appendChild(el);
  chatMessages.scrollTop = chatMessages.scrollHeight;
  return el;
}

async function sendQuestion() {
  const q = chatInput.value.trim();
  if (!q) return;

  chatInput.value = '';
  autoResize(chatInput);
  sendBtn.disabled = true;

  addMessage('question', q);
  const typingEl = addMessage('typing');

  try {
    const res = await fetch(`${API}/api/ask`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: q, top_k: 5 }),
    });

    typingEl.remove();

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Unknown error' }));
      addMessage('answer', `Error: ${err.detail || res.statusText}`);
      return;
    }

    const data = await res.json();
    addMessage('answer', data.answer, data);
  } catch (err) {
    typingEl.remove();
    addMessage('answer', `Network error: ${err.message}`);
  } finally {
    sendBtn.disabled = false;
    chatInput.focus();
  }
}

sendBtn.addEventListener('click', sendQuestion);

chatInput.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendQuestion();
  }
});

chatInput.addEventListener('input', () => autoResize(chatInput));

function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 120) + 'px';
}

// Suggestion chips
document.querySelectorAll('.chip').forEach(chip => {
  chip.addEventListener('click', () => {
    chatInput.value = chip.textContent.trim();
    autoResize(chatInput);
    chatInput.focus();
  });
});

// ----------------------------------------------------------------
// Document Upload
// ----------------------------------------------------------------
uploadZone.addEventListener('click', () => fileInput.click());
uploadZone.addEventListener('dragover', e => { e.preventDefault(); uploadZone.classList.add('drag-over'); });
uploadZone.addEventListener('dragleave', () => uploadZone.classList.remove('drag-over'));
uploadZone.addEventListener('drop', e => {
  e.preventDefault();
  uploadZone.classList.remove('drag-over');
  const files = Array.from(e.dataTransfer.files);
  if (files.length) handleUpload(files[0]);
});

fileInput.addEventListener('change', () => {
  if (fileInput.files.length) handleUpload(fileInput.files[0]);
  fileInput.value = '';
});

async function handleUpload(file) {
  const allowed = ['.pdf', '.txt', '.md'];
  const ext = '.' + file.name.split('.').pop().toLowerCase();
  if (!allowed.includes(ext)) {
    showToast('error', 'Unsupported file type. Use PDF, TXT, or MD.');
    return;
  }

  // Show progress
  uploadProgress.classList.add('visible');
  uploadStatus.textContent = `Uploading ${file.name}…`;
  progressBar.style.width = '30%';

  const formData = new FormData();
  formData.append('file', file);
  formData.append('title', file.name.replace(/\.[^.]+$/, ''));

  try {
    progressBar.style.width = '60%';
    const res = await fetch(`${API}/api/documents`, {
      method: 'POST',
      body: formData,
    });
    progressBar.style.width = '90%';

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Upload failed' }));
      throw new Error(err.detail);
    }

    const data = await res.json();
    progressBar.style.width = '100%';
    uploadStatus.textContent = `Cached: ${data.local_result}`;

    setTimeout(() => {
      uploadProgress.classList.remove('visible');
      progressBar.style.width = '0%';
    }, 2000);

    showToast('success', `"${data.title}" uploaded successfully`);
    await loadDocuments();
  } catch (err) {
    uploadProgress.classList.remove('visible');
    progressBar.style.width = '0%';
    showToast('error', err.message || 'Upload failed');
  }
}

async function loadDocuments() {
  try {
    const res = await fetch(`${API}/api/documents`);
    if (!res.ok) return;
    documents = await res.json();
    renderDocuments();
    updateAnalyzeSelect();
  } catch { /* silently fail if backend isn't ready */ }
}

function renderDocuments() {
  docCount.textContent = documents.length;

  if (documents.length === 0) {
    docsGrid.classList.add('hidden');
    docsEmpty.classList.remove('hidden');
    return;
  }

  docsEmpty.classList.add('hidden');
  docsGrid.classList.remove('hidden');
  docsGrid.innerHTML = '';

  documents.forEach(doc => {
    const date = new Date(doc.created_at * 1000).toLocaleDateString('en-IN', {
      day: 'numeric', month: 'short', year: 'numeric'
    });
    const card = document.createElement('div');
    card.className = 'doc-card';
    card.innerHTML = `
      <div class="doc-card-header">
        <div class="doc-icon">${ICONS.file}</div>
        <div class="doc-title">${escHtml(doc.title)}</div>
      </div>
      <div class="doc-meta">
        <span class="doc-tag">${escHtml(doc.source)}</span>
        <span class="doc-date">${date}</span>
      </div>
      <div class="doc-actions">
        <button class="btn-sm btn-primary" onclick="summarizeDoc('${escHtml(doc.doc_id)}', '${escHtml(doc.title)}')">
          ${ICONS.sparkles} Summarize
        </button>
        <button class="btn-sm btn-ghost" onclick="analyzeDoc('${escHtml(doc.doc_id)}')">
          ${ICONS.flask} Analyze
        </button>
      </div>
    `;
    docsGrid.appendChild(card);
  });
}

function updateAnalyzeSelect() {
  const current = analyzeDocSel.value;
  analyzeDocSel.innerHTML = '<option value="">— Paste text below instead —</option>';
  documents.forEach(doc => {
    const opt = document.createElement('option');
    opt.value = doc.doc_id;
    opt.textContent = doc.title;
    analyzeDocSel.appendChild(opt);
  });
  if (current) analyzeDocSel.value = current;
}

// Quick shortcuts from doc cards
window.summarizeDoc = async function(docId, title) {
  openModal(`Summarizing "${title}"…`);
  try {
    const res = await fetch(`${API}/api/summarize`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ doc_id: docId, focus: '' }),
    });
    if (!res.ok) throw new Error((await res.json()).detail);
    const data = await res.json();
    setModalContent(`Summary of "${title}"`, data.result);
  } catch (err) {
    setModalContent('Error', err.message);
  }
};

window.analyzeDoc = function(docId) {
  switchPanel('analyze');
  analyzeDocSel.value = docId;
  analyzeQuestion.focus();
};

// ----------------------------------------------------------------
// Analyze Panel
// ----------------------------------------------------------------
analyzeBtn.addEventListener('click', async () => {
  const docId = analyzeDocSel.value;
  const question = analyzeQuestion.value.trim();

  if (!question) { showToast('error', 'Enter a question first.'); return; }
  if (!docId && !document.getElementById('analyze-paste').value.trim()) {
    showToast('error', 'Select a document or paste text.'); return;
  }

  setAnalyzeLoading(true);
  analyzeResult.classList.remove('visible');

  try {
    const body = { question };
    if (docId) {
      body.doc_id = docId;
    } else {
      body.text = document.getElementById('analyze-paste').value.trim();
    }

    const res = await fetch(`${API}/api/analyze`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });

    if (!res.ok) throw new Error((await res.json()).detail);
    const data = await res.json();
    analyzeResultContent.innerHTML = renderMarkdownLite(data.result);
    analyzeResult.classList.add('visible');
    analyzeResult.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (err) {
    showToast('error', err.message);
  } finally {
    setAnalyzeLoading(false);
  }
});

summarizeBtn.addEventListener('click', async () => {
  const docId = analyzeDocSel.value;
  const focus  = summarizeQuestion.value.trim();
  const pasteText = document.getElementById('analyze-paste').value.trim();

  if (!docId && !pasteText) {
    showToast('error', 'Select a document or paste text.'); return;
  }

  summarizeResult.classList.remove('visible');
  summarizeBtn.disabled = true;
  summarizeBtn.innerHTML = `<span class="spinner"></span> Summarizing…`;

  try {
    const body = { focus };
    if (docId) body.doc_id = docId; else body.text = pasteText;

    const res = await fetch(`${API}/api/summarize`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });

    if (!res.ok) throw new Error((await res.json()).detail);
    const data = await res.json();
    summarizeResultContent.innerHTML = renderMarkdownLite(data.result);
    summarizeResult.classList.add('visible');
  } catch (err) {
    showToast('error', err.message);
  } finally {
    summarizeBtn.disabled = false;
    summarizeBtn.innerHTML = `${ICONS.sparkles} Summarize`;
  }
});

function setAnalyzeLoading(loading) {
  analyzeBtn.disabled = loading;
  analyzeBtn.innerHTML = loading
    ? `<span class="spinner"></span> Analyzing…`
    : `${ICONS.flask} Run deep analysis`;
}

// ----------------------------------------------------------------
// Modal (summarize result)
// ----------------------------------------------------------------
function openModal(loadingText) {
  modalBody.innerHTML = `<div class="modal-loading">${pulseTraceSvg(true)} ${escHtml(loadingText)}</div>`;
  modalTitle.textContent = 'Processing…';
  modalOverlay.classList.add('visible');
}

function setModalContent(title, content) {
  modalTitle.textContent = title;
  modalBody.innerHTML = renderMarkdownLite(content);
}

document.getElementById('modal-close').addEventListener('click', () => {
  modalOverlay.classList.remove('visible');
});

modalOverlay.addEventListener('click', e => {
  if (e.target === modalOverlay) modalOverlay.classList.remove('visible');
});

// ----------------------------------------------------------------
// Toast
// ----------------------------------------------------------------
function showToast(type, message) {
  const icon = type === 'success' ? ICONS.check : type === 'info' ? ICONS.info : ICONS.alert;
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.innerHTML = `${icon}<span class="toast-msg">${escHtml(message)}</span>`;
  toastContainer.appendChild(el);

  setTimeout(() => {
    el.style.animation = 'slideOut 0.3s ease forwards';
    setTimeout(() => el.remove(), 300);
  }, 3500);
}

// ----------------------------------------------------------------
// Utilities
// ----------------------------------------------------------------
function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ----------------------------------------------------------------
// Init
// ----------------------------------------------------------------
async function init() {
  // Set initial status dots to loading
  localDot.className = 'status-dot loading';
  cloudDot.className = 'status-dot loading';

  await pollHealth();
  await loadDocuments();

  setInterval(pollHealth, 30_000);
  setInterval(loadDocuments, 60_000);
}

init();
