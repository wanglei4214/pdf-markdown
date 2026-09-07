const API_BASE = '';
let pollTimer = null;
let currentTaskId = null;
let currentUser = null;
let paymentConfig = { enabled: false, has_paid: false, test_mode: false };
let googleClientId = null;

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

// ---------- 登录鉴权 ----------

function renderAuthArea() {
  const area = $('#auth-area');
  if (currentUser) {
    const name = currentUser.name || currentUser.email || 'User';
    const avatar = currentUser.picture
      ? `<img src="${currentUser.picture}" alt="" referrerpolicy="no-referrer" class="w-8 h-8 rounded-full border">`
      : '';
    // 付费状态徽章 / 升级按钮（支付未配置时不显示）
    let planHtml = '';
    if (paymentConfig.enabled) {
      planHtml = paymentConfig.has_paid
        ? `<span class="px-2.5 py-1 rounded-full bg-green-100 text-green-700 text-xs font-medium whitespace-nowrap">Pro${paymentConfig.test_mode ? ' (test)' : ''}</span>`
        : `<button id="btn-upgrade" class="px-3 py-1.5 rounded-lg bg-amber-500 text-white hover:bg-amber-600 text-sm font-medium whitespace-nowrap">Upgrade Pro</button>`;
    }
    area.innerHTML = `
      <div class="flex items-center gap-2">
        ${planHtml}
        <div class="flex items-center gap-2 bg-white border border-gray-200 rounded-full pl-1.5 pr-3 py-1 shadow-sm">
          ${avatar}
          <span class="text-sm text-gray-700 max-w-[120px] md:max-w-[200px] truncate" title="${currentUser.email || ''}">${name}</span>
        </div>
        <button id="btn-logout" class="px-3 py-1.5 rounded-lg bg-white border border-gray-200 text-gray-600 hover:bg-gray-100 text-sm whitespace-nowrap">Logout</button>
      </div>`;
    $('#btn-logout').addEventListener('click', logout);
    const upgradeBtn = $('#btn-upgrade');
    if (upgradeBtn) upgradeBtn.addEventListener('click', startCheckout);
  } else {
    area.innerHTML = `
      <button id="manual-signin-btn" type="button" class="px-4 py-2 rounded-lg bg-white border border-gray-300 text-gray-700 hover:bg-gray-50 text-sm font-medium flex items-center gap-2 shadow-sm">
        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 17l5-5-5-5m5 5H3m11-7h4a2 2 0 012 2v10a2 2 0 01-2 2h-4"/>
        </svg>
        Sign in
      </button>`;
    $('#manual-signin-btn').addEventListener('click', () => {
      window.location.href = `${API_BASE}/api/auth/google/login`;
    });
  }
}

async function logout() {
  try {
    const res = await fetch(`${API_BASE}/api/auth/logout`, { method: 'POST' });
    if (!res.ok) throw new Error('Logout failed');
  } catch (err) {
    alert(err.message);
    return;
  }

  if (window.google?.accounts?.id) {
    window.google.accounts.id.disableAutoSelect();
    window.google.accounts.id.cancel();
  }
  currentUser = null;
  stopPolling();
  renderAuthArea();
  refreshAfterAuth();
}

function refreshAfterAuth() {
  renderAuthArea();
  if (currentUser) {
    $('#tasks-list').innerHTML = '<p class="text-gray-500">Loading...</p>';
    loadPaymentConfig().then(renderAuthArea);
    loadTasks();
  } else {
    paymentConfig = { enabled: false, has_paid: false, test_mode: false };
    $('#tasks-list').innerHTML = '<p class="text-gray-500">Please sign in to view your tasks</p>';
    $('#upload-status').classList.add('hidden');
  }
}

async function handleLoginResult() {
  const params = new URLSearchParams(window.location.search);
  const loginStatus = params.get('login');
  window.history.replaceState({}, document.title, window.location.pathname);

  if (loginStatus === 'failed') {
    alert('Google login failed. Please try again.');
  }
  await initAuth();
}

async function initAuth() {
  try {
    const [configRes, sessionRes] = await Promise.all([
      fetch(`${API_BASE}/api/auth/config`),
      fetch(`${API_BASE}/api/auth/me`),
    ]);

    if (configRes.ok) {
      const config = await configRes.json();
      googleClientId = config.google_client_id || null;
    }

    if (sessionRes.ok) {
      const session = await sessionRes.json();
      currentUser = session.user || null;
    }
  } catch (error) {
    currentUser = null;
    console.warn('Failed to initialize authentication', error);
  }

  if (!currentUser) {
    paymentConfig = { enabled: false, has_paid: false, test_mode: false };
    renderAuthArea();
    $('#tasks-list').innerHTML = '<p class="text-gray-500">Please sign in to view your tasks</p>';
    return;
  }

  await loadPaymentConfig();
  renderAuthArea();
  loadTasks();
  showPaymentSuccess();

  if (new URLSearchParams(window.location.search).get('checkout') === 'success') {
    setTimeout(() => { loadPaymentConfig().then(renderAuthArea); }, 3000);
  }
}

function requireLogin() {
  if (!currentUser) {
    alert('Please sign in with Google to use this feature');
    return false;
  }
  return true;
}

// ---------- Creem 支付 ----------

async function loadPaymentConfig() {
  if (!currentUser) {
    paymentConfig = { enabled: false, has_paid: false, test_mode: false };
    return;
  }
  try {
    const res = await fetch(`${API_BASE}/api/payments/config`);
    if (res.ok) paymentConfig = await res.json();
  } catch (e) {
    paymentConfig = { enabled: false, has_paid: false, test_mode: false };
  }
}

async function startCheckout() {
  if (!requireLogin()) return;
  try {
    const res = await fetch(`${API_BASE}/api/payments/checkout`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Failed to start checkout');
    // 跳转到 Creem 托管的结账页，支付完成后跳回 success_url
    window.location.href = data.checkout_url;
  } catch (err) {
    alert('Checkout failed: ' + err.message);
  }
}

function showPaymentSuccess() {
  // 从 Creem 支付页跳回时 URL 带 ?checkout=success
  const params = new URLSearchParams(window.location.search);
  if (params.get('checkout') === 'success') {
    window.history.replaceState({}, document.title, window.location.pathname);
    const status = $('#upload-status');
    status.classList.remove('hidden');
    status.innerHTML = '<p class="text-green-600">Payment received! Your Pro access is being activated…</p>';
  }
}

function showView(name) {
  $$('.view').forEach(el => el.classList.add('hidden'));
  $(`#view-${name}`).classList.remove('hidden');
  document.body.classList.toggle('result-view', name === 'result');

  $$('.nav-btn').forEach(btn => {
    const active = btn.dataset.view === name;
    btn.classList.toggle('nav-active', active);
    btn.classList.toggle('bg-indigo-600', active);
    btn.classList.toggle('text-white', active);
    btn.classList.toggle('bg-white', !active);
    btn.classList.toggle('text-gray-700', !active);
    btn.classList.toggle('border', !active);
  });
}

function formatSize(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(2) + ' MB';
}

function formatTime(iso) {
  const d = new Date(iso);
  return d.toLocaleString();
}

function statusBadge(status) {
  const map = {
    pending: ['Pending', 'bg-gray-100 text-gray-700'],
    processing: ['Processing', 'bg-blue-100 text-blue-700'],
    success: ['Done', 'bg-green-100 text-green-700'],
    failed: ['Failed', 'bg-red-100 text-red-700'],
  };
  const [text, cls] = map[status] || ['Unknown', 'bg-gray-100'];
  return `<span class="px-2 py-1 rounded text-xs font-medium ${cls}">${text}</span>`;
}

async function uploadFile(file) {
  if (!requireLogin()) return;
  const status = $('#upload-status');
  status.classList.remove('hidden');
  status.innerHTML = '<p class="text-blue-600">Uploading...</p>';

  const form = new FormData();
  form.append('file', file);

  try {
    const res = await fetch(`${API_BASE}/api/tasks/upload`, {
      method: 'POST',
      body: form,
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || 'Upload failed');
    }
    status.innerHTML = '<p class="text-green-600">Upload successful. OCR processing has started.</p>';
    showView('tasks');
    loadTasks();
  } catch (err) {
    status.innerHTML = `<p class="text-red-600">Error: ${err.message}</p>`;
  }
}

function setupUpload() {
  const dropZone = $('#drop-zone');
  const input = $('#file-input');

  dropZone.addEventListener('click', () => input.click());

  input.addEventListener('change', () => {
    if (input.files.length) uploadFile(input.files[0]);
  });

  dropZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropZone.classList.add('dragover');
  });

  dropZone.addEventListener('dragleave', () => {
    dropZone.classList.remove('dragover');
  });

  dropZone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropZone.classList.remove('dragover');
    const files = e.dataTransfer.files;
    if (files.length) uploadFile(files[0]);
  });
}

async function loadTasks() {
  if (!currentUser) return;
  const container = $('#tasks-list');
  try {
    const res = await fetch(`${API_BASE}/api/tasks`);
    if (res.status === 401) {
      currentUser = null;
      renderAuthArea();
      container.innerHTML = '<p class="text-gray-500">Please sign in to view your tasks</p>';
      stopPolling();
      return;
    }
    const tasks = await res.json();
    if (!tasks.length) {
      container.innerHTML = '<p class="text-gray-500">No tasks yet</p>';
      return;
    }

    container.innerHTML = tasks.map(t => {
      const progress = t.total_pages
        ? Math.min(100, Math.round((t.current_page / t.total_pages) * 100))
        : 0;
      const pageProgress = t.status === 'processing'
        ? (t.total_pages ? `· Progress ${t.current_page}/${t.total_pages} pages` : '· Analyzing PDF pages...')
        : (t.total_pages ? `· Page ${t.current_page}/${t.total_pages}` : '');
      return `
        <div class="bg-white rounded-lg shadow p-4 flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div class="flex-1 min-w-0">
            <div class="flex items-center gap-2 mb-1">
              <h3 class="font-medium truncate" title="${t.original_name}">${t.original_name}</h3>
              ${statusBadge(t.status)}
            </div>
            <p class="text-sm text-gray-500">
              ${formatSize(t.file_size)} · ${formatTime(t.created_at)}
              ${pageProgress}
            </p>
            ${t.status === 'processing' ? `
              <div class="w-full bg-gray-200 rounded-full h-2 mt-3">
                <div class="bg-blue-600 h-2 rounded-full transition-all" style="width: ${progress}%"></div>
              </div>
            ` : ''}
            ${t.status === 'failed' ? `<p class="text-xs text-red-500 mt-2 truncate" title="${t.error_message || ''}">${t.error_message || ''}</p>` : ''}
          </div>
          <div class="flex gap-2">
            ${t.status === 'success' ? `
              <button data-id="${t.id}" class="btn-preview px-3 py-1.5 rounded bg-indigo-50 text-indigo-700 hover:bg-indigo-100 text-sm">Preview</button>
              <a href="${API_BASE}/api/tasks/${t.id}/download" class="px-3 py-1.5 rounded bg-indigo-600 text-white hover:bg-indigo-700 text-sm">Download</a>
            ` : `
              <button data-id="${t.id}" class="btn-retry px-3 py-1.5 rounded bg-blue-50 text-blue-700 hover:bg-blue-100 text-sm">${t.status === 'processing' ? 'Restart' : 'Resume'}</button>
            `}
            <button data-id="${t.id}" class="btn-delete px-3 py-1.5 rounded bg-gray-100 text-gray-700 hover:bg-gray-200 text-sm">Delete</button>
          </div>
        </div>
      `;
    }).join('');

    $$('.btn-preview').forEach(btn => {
      btn.addEventListener('click', () => loadResult(btn.dataset.id));
    });
    $$('.btn-retry').forEach(btn => {
      btn.addEventListener('click', () => retryTask(btn.dataset.id));
    });
    $$('.btn-delete').forEach(btn => {
      btn.addEventListener('click', () => deleteTask(btn.dataset.id));
    });

    const hasRunning = tasks.some(t => t.status === 'processing' || t.status === 'pending');
    if (hasRunning) startPolling();
    else stopPolling();
  } catch (err) {
    container.innerHTML = `<p class="text-red-500">Failed to load: ${err.message}</p>`;
  }
}

async function deleteTask(id) {
  if (!confirm('Delete this task and its generated files?')) return;
  await fetch(`${API_BASE}/api/tasks/${id}`, { method: 'DELETE' });
  loadTasks();
}

async function retryTask(id) {
  try {
    const res = await fetch(`${API_BASE}/api/tasks/${id}/retry`, { method: 'POST' });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || 'Failed to resume processing');
    }
    loadTasks();
  } catch (err) {
    alert('Failed to resume processing: ' + err.message);
  }
}

async function loadResult(id) {
  currentTaskId = id;
  try {
    const [taskRes, mdRes] = await Promise.all([
      fetch(`${API_BASE}/api/tasks/${id}`),
      fetch(`${API_BASE}/api/tasks/${id}/markdown`),
    ]);
    const task = await taskRes.json();
    const md = await mdRes.text();

    $('#result-title').textContent = task.original_name;
    $('#result-content').innerHTML = marked.parse(md);
    $('#btn-download').onclick = () => {
      window.location.href = `${API_BASE}/api/tasks/${id}/download`;
    };
    $('#btn-copy').onclick = async () => {
      await navigator.clipboard.writeText(md);
      const old = $('#btn-copy').textContent;
      $('#btn-copy').textContent = 'Copied';
      setTimeout(() => $('#btn-copy').textContent = old, 1500);
    };

    showView('result');
    // Reset mobile TOC to collapsed state
    closeToc();
    const toc = $('#result-toc');
    toc.innerHTML = '';
    requestAnimationFrame(() => buildToc());
  } catch (err) {
    alert('Failed to load result: ' + err.message);
  }
}

function closeToc() {
  const toc = $('#result-toc');
  toc.classList.remove('toc-open');
  document.body.classList.remove('toc-open');
  $('#btn-toc').setAttribute('aria-label', 'Open table of contents');
  $('#btn-toc').setAttribute('title', 'Open table of contents');
  $('#toc-backdrop').setAttribute('aria-hidden', 'true');
}

function openToc() {
  $('#result-toc').classList.add('toc-open');
  document.body.classList.add('toc-open');
  $('#btn-toc').setAttribute('aria-label', 'Close table of contents');
  $('#btn-toc').setAttribute('title', 'Close table of contents');
  $('#toc-backdrop').setAttribute('aria-hidden', 'false');
}

function buildToc() {
  const headings = [...$$('#result-content h1, #result-content h2, #result-content h3, #result-content h4, #result-content h5, #result-content h6')];
  const toc = $('#result-toc');
  if (!headings.length) {
    toc.innerHTML = '<p class="text-sm text-gray-400">No table of contents</p>';
    return;
  }

  const content = $('#result-content');
  const links = [];
  const ul = document.createElement('ul');
  headings.forEach((h, i) => {
    const id = `heading-${i}`;
    h.id = id;
    const li = document.createElement('li');
    li.style.paddingLeft = (parseInt(h.tagName[1]) - 1) * 12 + 'px';
    const a = document.createElement('a');
    a.href = '#' + id;
    a.textContent = h.textContent;
    a.addEventListener('click', (e) => {
      e.preventDefault();
      const isMobile = window.matchMedia('(max-width: 767px)').matches;
      if (isMobile) {
        const y = h.getBoundingClientRect().top + window.scrollY - 24;
        closeToc();
        window.scrollTo({ top: y, behavior: 'smooth' });
      } else {
        const top = h.getBoundingClientRect().top - content.getBoundingClientRect().top + content.scrollTop - 20;
        content.scrollTo({ top, behavior: 'smooth' });
      }
    });
    li.appendChild(a);
    ul.appendChild(li);
    links.push({ heading: h, link: a });
  });
  toc.innerHTML = '<h3 class="font-semibold mb-3 text-sm text-gray-700">Table of Contents</h3>';
  toc.appendChild(ul);

  const updateActive = () => {
    const marker = content.getBoundingClientRect().top + 32;
    let active = links[0];
    links.forEach(item => {
      if (item.heading.getBoundingClientRect().top <= marker) active = item;
    });
    links.forEach(item => item.link.classList.toggle('active', item === active));
    active.link.scrollIntoView({ block: 'nearest' });
  };
  content.onscroll = updateActive;
  window.addEventListener('scroll', updateActive, { passive: true });
  updateActive();
}

function startPolling() {
  if (pollTimer) return;
  pollTimer = setInterval(loadTasks, 2000);
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

function setupNav() {
  $$('.nav-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const view = btn.dataset.view;
      if (view === 'tasks') loadTasks();
      showView(view);
    });
  });

  $('#btn-back').addEventListener('click', () => {
    showView('tasks');
    loadTasks();
  });

  $('#btn-toc').addEventListener('click', () => {
    const toc = $('#result-toc');
    if (toc.classList.contains('toc-open')) closeToc();
    else openToc();
  });

  $('#toc-backdrop').addEventListener('click', closeToc);
}

document.addEventListener('DOMContentLoaded', () => {
  setupUpload();
  setupNav();
  handleLoginResult();
});
