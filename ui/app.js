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
  html = html.replace(/^###\s+(.+)$/gm, '<h4 class="md-header">$1</h4>');
  html = html.replace(/^##\s+(.+)$/gm, '<h3 class="md-header">$1</h3>');
  html = html.replace(/^#\s+(.+)$/gm, '<h2 class="md-header">$1</h2>');
  html = html.replace(/^---+$/gm, '<hr class="md-divider" />');
  html = html.replace(/^[-*](?!\*)\s+/gm, '• ');                   // "* item" or "- item" -> "• item"
  html = html.replace(/\*\*([^*]+?)\*\*/g, '<strong>$1</strong>'); // **bold** -> <strong>
  html = html.replace(/\*([^*]+?)\*/g, '<em>$1</em>');             // *italic* -> <em>
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
const topbarHealth    = document.getElementById('topbar-health');
const topbarHealthText = document.getElementById('topbar-health-text');
const docSearch       = document.getElementById('doc-search');
const docTypeFilter   = document.getElementById('doc-type-filter');
const docSort         = document.getElementById('doc-sort');
const docsStatus      = document.getElementById('docs-status');
const chatCharCount   = document.getElementById('chat-char-count');

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

  const localOk = localDot.classList.contains('ok');
  const cloudOk = cloudDot.classList.contains('ok');
  topbarHealth.classList.toggle('all-ok', localOk && cloudOk);
  topbarHealth.classList.toggle('has-error', !localOk || !cloudOk);
  topbarHealthText.textContent = localOk && cloudOk
    ? 'All services online'
    : localOk || cloudOk
      ? 'Service partially available'
      : 'Services unavailable';
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

    // Guard the UI against older backend processes that still return the
    // local retriever's document-preview dump as the "answer".
    const retrievalDump = /^\s*based on \d+ cached local document/i.test(String(content || ''))
      || /^\s*based on \d+ cached local document/i.test(String(content || '').replace(/\(s\)|s:/gi, ''));
    const displayContent = retrievalDump
      ? 'The search returned document-preview snippets instead of a reliable answer. Check the supporting evidence below; if no passages are listed, restart the app with the latest backend code and try again.'
      : content;

    const rawCitations = Array.isArray(meta?.citations) ? meta.citations : [];
    const citationSeen = new Set();
    const citations = rawCitations.filter(citation => {
      const passage = String(citation.passage || '').trim();
      if (!passage || !citation.doc_id) return false;
      const key = String(citation.doc_id).toLowerCase() + '|' + passage.toLowerCase().replace(/\s+/g, ' ').slice(0, 220);
      if (citationSeen.has(key)) return false;
      citationSeen.add(key);
      return true;
    });
    const citationHtml = citations.length ? `
      <section class="evidence-list" aria-label="Supporting source evidence">
        <h4>Supporting evidence <span>${citations.length} passages</span></h4>
        <p class="evidence-intro">These passages were retrieved from your cached records. Open a passage to inspect the exact text.</p>
        ${citations.map((citation, index) => `
          <details class="evidence-card" ${index === 0 ? 'open' : ''}>
            <summary><span class="evidence-index">${index + 1}</span><span class="evidence-source">${escHtml(citation.title || 'Source document')}</span><span class="evidence-page">${citation.page_number ? 'Page ' + escHtml(citation.page_number) : 'Page not identified'}</span></summary>
            <blockquote>${escHtml(citation.passage)}</blockquote>
            <small><a class="source-open-link" href="/api/documents/${encodeURIComponent(citation.doc_id)}${citation.page_number ? '/source#page=' + encodeURIComponent(citation.page_number) : '/source'}" target="_blank" rel="noopener noreferrer">Open original source</a><span> · Page numbers are shown only when available in the extracted evidence.</span></small>
          </details>`).join('')}
      </section>` : '<section class="evidence-list evidence-empty"><h4>Supporting evidence unavailable</h4><p>The retrieval system did not return a source passage for this answer. Treat the response as unverified and check the original document before relying on it.</p></section>';
    el.innerHTML = `
      <div class="msg-bubble">${renderMarkdownLite(displayContent)}</div>
      <div class="msg-meta">${sourceBadge}${latency}</div>
      ${citationHtml}
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

chatInput.addEventListener('input', () => {
  if (chatInput.value.length > 4000) chatInput.value = chatInput.value.slice(0, 4000);
  autoResize(chatInput);
  chatCharCount.textContent = `${chatInput.value.length} / 4000`;
});

function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 120) + 'px';
}

// Empty-state onboarding
const emptyUploadCta = document.getElementById('empty-upload-cta');
emptyUploadCta?.addEventListener('click', () => {
  switchPanel('docs');
  document.getElementById('upload-zone')?.focus();
});

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
// The associated label opens the picker; avoid a duplicate click handler.
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
  const maxBytes = 20 * 1024 * 1024;
  const ext = '.' + file.name.split('.').pop().toLowerCase();
  if (!allowed.includes(ext)) {
    showToast('error', 'Unsupported file type. Use PDF, TXT, or MD.');
    return;
  }
  if (file.size > maxBytes) {
    showToast('error', 'This file is larger than 20 MB. Choose a smaller document.');
    return;
  }

  // Show progress
  uploadProgress.classList.add('visible');
  uploadStatus.textContent = `Uploading ${file.name}…`;
  progressBar.style.width = '30%';

  const formData = new FormData();
  formData.append('file', file);
  formData.append('title', file.name);

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
    }, 1500);

    // Show persistent upload success card
    const successCard = document.getElementById('upload-success-card');
    const successName = document.getElementById('upload-success-name');
    const successDetail = document.getElementById('upload-success-detail');
    const askNowBtn = document.getElementById('upload-ask-now-btn');
    if (successCard && successName) {
      successName.textContent = file.name;
      successDetail.textContent = `Cached locally (${(file.size / 1024).toFixed(1)} KB) · Ready for clinical Q&A`;
      successCard.classList.remove('hidden');
      if (askNowBtn) {
        askNowBtn.onclick = () => {
          switchPanel('chat');
          chatInput.focus();
          chatInput.placeholder = `Ask a question about ${file.name}…`;
        };
      }
    }

    showToast('success', `"${data.title || file.name}" uploaded successfully`);
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

function getDocExtension(doc) {
  const combined = `${doc.title || ''} ${doc.source || ''} ${doc.doc_id || ''}`.toLowerCase();
  if (combined.includes('.pdf') || combined.endsWith('pdf')) return 'pdf';
  if (combined.includes('.txt') || combined.endsWith('txt')) return 'txt';
  if (combined.includes('.md') || combined.endsWith('md')) return 'md';
  return '';
}

function renderDocuments() {
  const query = (docSearch?.value || '').trim().toLowerCase();
  const type = docTypeFilter?.value || 'all';
  const sort = docSort?.value || 'newest';
  const filtered = documents.filter(doc => {
    const title = String(doc.title || '');
    const source = String(doc.source || '');
    const docId = String(doc.doc_id || '');
    const matchesQuery = !query || title.toLowerCase().includes(query) || source.toLowerCase().includes(query) || docId.toLowerCase().includes(query);
    const extension = getDocExtension(doc);
    const matchesType = type === 'all' || extension === type;
    return matchesQuery && matchesType;
  });
  const visibleDocuments = [...filtered].sort((a, b) => {
    if (sort === 'name') return String(a.title || '').localeCompare(String(b.title || ''), undefined, { sensitivity: 'base' });
    const aTime = Number(a.created_at) || 0;
    const bTime = Number(b.created_at) || 0;
    return sort === 'oldest' ? aTime - bTime : bTime - aTime;
  });

  docCount.textContent = query || type !== 'all' ? `${visibleDocuments.length} / ${documents.length}` : documents.length;
  docsGrid.innerHTML = '';
  docsStatus.textContent = documents.length ? `${visibleDocuments.length} of ${documents.length} documents` : '';

  if (documents.length === 0 || visibleDocuments.length === 0) {
    docsGrid.classList.add('hidden');
    docsEmpty.classList.remove('hidden');
    docsEmpty.querySelector('p').textContent = documents.length === 0
      ? 'Your library is empty. Upload a PDF, TXT, or Markdown file above to start asking questions about your documents.'
      : 'No documents match these filters. Try another search or file type.';
    return;
  }

  docsEmpty.classList.add('hidden');
  docsGrid.classList.remove('hidden');

  visibleDocuments.forEach(doc => {
    const timestamp = Number(doc.created_at);
    const date = Number.isFinite(timestamp) && timestamp > 0
      ? new Date(timestamp * 1000).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' })
      : 'Date unavailable';
    const source = String(doc.source || 'Document');
    const title = String(doc.title || 'Untitled document');
    const ext = getDocExtension(doc);
    const extension = ext ? ext.toUpperCase() : 'DOC';
    const card = document.createElement('article');
    card.className = 'doc-card';
    const header = document.createElement('div');
    header.className = 'doc-card-header';
    const icon = document.createElement('div');
    icon.className = 'doc-icon';
    icon.innerHTML = ICONS.file;
    icon.setAttribute('aria-hidden', 'true');
    const titleEl = document.createElement('h3');
    titleEl.className = 'doc-title';
    titleEl.textContent = title;
    header.append(icon, titleEl);

    const meta = document.createElement('div');
    meta.className = 'doc-meta';
    const tag = document.createElement('span');
    tag.className = 'doc-tag';
    tag.textContent = extension;
    const dateEl = document.createElement('span');
    dateEl.className = 'doc-date';
    dateEl.textContent = date;
    meta.append(tag, dateEl);

    const actions = document.createElement('div');
    actions.className = 'doc-actions';
    const summarize = document.createElement('button');
    summarize.type = 'button';
    summarize.className = 'btn-sm btn-primary';
    summarize.innerHTML = `${ICONS.sparkles} Summarize`;
    summarize.addEventListener('click', () => window.summarizeDoc(String(doc.doc_id || ''), title));
    const analyze = document.createElement('button');
    analyze.type = 'button';
    analyze.className = 'btn-sm btn-ghost';
    analyze.innerHTML = `${ICONS.flask} Analyze`;
    analyze.addEventListener('click', () => window.analyzeDoc(String(doc.doc_id || '')));
    actions.append(summarize, analyze);
    card.append(header, meta, actions);
    docsGrid.appendChild(card);
  });
}

docSearch?.addEventListener('input', renderDocuments);
docTypeFilter?.addEventListener('change', renderDocuments);
docSort?.addEventListener('change', renderDocuments);

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
let modalPreviousFocus = null;

function closeModal() {
  modalOverlay.classList.remove('visible');
  modalOverlay.setAttribute('aria-hidden', 'true');
  modalPreviousFocus?.focus();
}

function openModal(loadingText) {
  modalPreviousFocus = document.activeElement;
  modalBody.innerHTML = `<div class="modal-loading">${pulseTraceSvg(true)} ${escHtml(loadingText)}</div>`;
  modalTitle.textContent = 'Processing…';
  modalOverlay.classList.add('visible');
  modalOverlay.setAttribute('aria-hidden', 'false');
  document.getElementById('modal-close').focus();
}

function setModalContent(title, content) {
  modalTitle.textContent = title;
  modalBody.innerHTML = renderMarkdownLite(content);
}

document.getElementById('modal-close').addEventListener('click', closeModal);

modalOverlay.addEventListener('click', e => {
  if (e.target === modalOverlay) closeModal();
});

modalOverlay.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeModal();
  if (e.key !== 'Tab') return;
  const focusable = [...modalOverlay.querySelectorAll('button:not([disabled]), a[href], input:not([disabled]), textarea:not([disabled])')];
  if (!focusable.length) return;
  const first = focusable[0], last = focusable[focusable.length - 1];
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
});

document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && modalOverlay.classList.contains('visible')) {
    modalOverlay.classList.remove('visible');
  }
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


// Copy generated analysis or summary without changing the source content.
document.querySelectorAll('[data-copy-target]').forEach(button => {
  button.addEventListener('click', async () => {
    const target = document.getElementById(button.dataset.copyTarget);
    const text = target?.innerText?.trim();
    if (!text) { showToast('error', 'There is no result to copy yet.'); return; }
    try {
      await navigator.clipboard.writeText(text);
      showToast('success', 'Result copied to clipboard.');
    } catch {
      showToast('error', 'Clipboard access is unavailable in this browser. Select the result text and copy it manually.');
    }
  });
});


// Source-linked clinical timeline and record discrepancy review.
const timelineButton = document.getElementById('load-timeline-btn');
const safetyButton = document.getElementById('run-safety-check-btn');
const timelineResults = document.getElementById('timeline-results');
const safetyResults = document.getElementById('safety-results');

function sourceLine(item) {
  return `${escHtml(item.source_title || item.title || item.doc_id || 'Source record')}${item.page_number ? ' · Page ' + escHtml(item.page_number) : ' · Page not available'}`;
}
function renderSourcePassage(item) {
  const sourceUrl = `/api/documents/${encodeURIComponent(item.doc_id || '')}/source${item.page_number ? '#page=' + encodeURIComponent(item.page_number) : ''}`;
  return `<details class="record-source"><summary>${sourceLine(item)}</summary><blockquote>${escHtml(item.passage || item.description || '')}</blockquote><p class="record-source-link"><a href="${sourceUrl}" target="_blank" rel="noopener noreferrer">Open source record${item.page_number ? ' at page ' + escHtml(item.page_number) : ''}</a></p></details>`;
}
timelineButton?.addEventListener('click', async () => {
  timelineButton.disabled = true;
  timelineButton.textContent = 'Building timeline…';
  timelineResults.innerHTML = '<p class="record-loading">Extracting dated events from cached records…</p>';
  try {
    const response = await fetch(`${API}/api/timeline`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Could not build timeline.');
    if (!data.events.length) {
      timelineResults.innerHTML = '<p class="record-empty">No clearly dated clinical events were detected. Add dated records or check that the uploaded text is extractable.</p>';
      return;
    }
    timelineResults.innerHTML = `<h3>Medical timeline <span>${data.events.length} events · ${data.document_count} records</span></h3><ol class="medical-timeline">${data.events.map(event => `
      <li class="timeline-event"><div class="timeline-event-top"><time>${escHtml(event.date)}</time><span class="timeline-category">${escHtml(event.category)}</span></div><p>${escHtml(event.description)}</p>${renderSourcePassage({doc_id:event.doc_id,source_title:event.source_title,page_number:event.page_number,description:event.description})}</li>`).join('')}</ol>`;
  } catch (error) {
    timelineResults.innerHTML = `<p class="record-error">${escHtml(error.message || 'Timeline request failed.')}</p>`;
  } finally {
    timelineButton.disabled = false;
    timelineButton.textContent = 'Build medical timeline';
  }
});
safetyButton?.addEventListener('click', async () => {
  safetyButton.disabled = true;
  safetyButton.textContent = 'Checking records…';
  safetyResults.innerHTML = '<p class="record-loading">Comparing medication, allergy, date, and lab context…</p>';
  try {
    const response = await fetch(`${API}/api/safety-check`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Could not check records.');
    if (!data.findings.length) {
      safetyResults.innerHTML = '<p class="record-empty">No potential discrepancies were detected by the current checks. This does not establish that the records are complete or consistent.</p>';
      return;
    }
    safetyResults.innerHTML = `<h3>Potential discrepancies <span>${data.findings.length} flags · ${data.document_count} records</span></h3><div class="safety-findings">${data.findings.map(finding => `
      <article class="safety-finding"><div class="safety-finding-head"><span class="safety-flag">${finding.severity === 'high_review' ? 'Priority review' : 'Review'}</span><h4>${escHtml(finding.title)}</h4></div><p>${escHtml(finding.message)}</p>${(finding.mentions || [finding]).map(renderSourcePassage).join('')}</article>`).join('')}</div>`;
  } catch (error) {
    safetyResults.innerHTML = `<p class="record-error">${escHtml(error.message || 'Safety check failed.')}</p>`;
  } finally {
    safetyButton.disabled = false;
    safetyButton.textContent = 'Check for discrepancies';
  }
});
