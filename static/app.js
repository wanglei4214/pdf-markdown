const API_BASE = '';
let pollTimer = null;
let currentTaskId = null;
let currentUser = null;
let paymentConfig = { enabled: false, has_paid: false, test_mode: false, quota: null };
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
      if (paymentConfig.has_paid) {
        planHtml = `<span class="px-2.5 py-1 rounded-full bg-green-100 text-green-700 text-xs font-medium whitespace-nowrap">Pro${paymentConfig.test_mode ? ' (test)' : ''}</span>`;
      } else {
        planHtml += `<button id="btn-upgrade" class="px-3 py-1.5 rounded-lg bg-amber-500 text-white hover:bg-amber-600 text-sm font-medium whitespace-nowrap">Upgrade Pro</button>`;
      }
    }
    area.innerHTML = `
      <div class="flex items-center gap-2">
        ${planHtml}
        <div class="relative user-profile-container">
          <div class="flex items-center gap-2 bg-white border border-gray-200 rounded-full pl-1.5 pr-3 py-1 shadow-sm cursor-pointer hover:bg-gray-50 transition">
            ${avatar}
            <span class="text-sm text-gray-700 max-w-[120px] md:max-w-[200px] truncate" title="${currentUser.email || ''}">${name}</span>
          </div>
          <div id="quota-tooltip" class="hidden absolute right-0 top-full mt-2 w-72 bg-white border border-gray-200 rounded-xl shadow-lg p-4 z-50">
            <div class="mb-3">
              <p class="text-xs text-gray-500 mb-1">Current Plan</p>
              <p class="text-lg font-bold text-indigo-700">${paymentConfig.has_paid ? 'Pro' : 'Free'}${paymentConfig.test_mode ? ' (test)' : ''}</p>
            </div>
            <div class="mb-3 pb-3 border-b border-gray-100">
              <p class="text-xs text-gray-500 mb-1">This Month's Usage</p>
              <div class="flex items-baseline gap-2">
                <p class="text-2xl font-bold text-gray-800" id="tooltip-used">-</p>
                <p class="text-sm text-gray-500">/ <span id="tooltip-limit">-</span> pages</p>
              </div>
              <div class="mt-2 w-full bg-gray-100 rounded-full h-2">
                <div id="tooltip-progress" class="bg-indigo-600 h-2 rounded-full transition-all" style="width: 0%"></div>
              </div>
            </div>
            <p class="text-xs text-gray-500 mb-3">Quota resets on the 1st of each month</p>
            ${!paymentConfig.has_paid ? '<a href="pricing.html" class="block w-full text-center px-4 py-2 rounded-lg bg-amber-500 text-white hover:bg-amber-600 text-sm font-medium mb-2">Upgrade to Pro</a>' : ''}
            <button id="tooltip-logout" class="block w-full text-center px-4 py-2 rounded-lg bg-white border border-gray-200 text-gray-600 hover:bg-gray-100 text-sm">Logout</button>
          </div>
        </div>
      </div>`;
    
    // 更新配额 tooltip 数据
    if (paymentConfig.quota) {
      const q = paymentConfig.quota;
      const used = q.used_pages;
      const limit = q.limit;
      const usedEl = $('#tooltip-used');
      const limitEl = $('#tooltip-limit');
      const progressEl = $('#tooltip-progress');
      if (usedEl) usedEl.textContent = used.toString();
      if (limitEl) limitEl.textContent = limit.toString();
      if (progressEl) {
        const percentage = limit > 0 ? Math.min(100, (used / limit) * 100) : 0;
        progressEl.style.width = percentage + '%';
        if (percentage >= 90) {
          progressEl.className = 'bg-red-600 h-2 rounded-full transition-all';
        } else if (percentage >= 70) {
          progressEl.className = 'bg-amber-500 h-2 rounded-full transition-all';
        }
      }
    }
    
    // 头像悬停显示/隐藏 tooltip，延迟隐藏避免鼠标移动时误关闭
    const profileContainer = $('.user-profile-container');
    const tooltip = $('#quota-tooltip');
    let hideTimer = null;
    if (profileContainer && tooltip) {
      profileContainer.addEventListener('mouseenter', () => {
        if (hideTimer) {
          clearTimeout(hideTimer);
          hideTimer = null;
        }
        tooltip.classList.remove('hidden');
      });
      profileContainer.addEventListener('mouseleave', () => {
        hideTimer = setTimeout(() => {
          tooltip.classList.add('hidden');
        }, 1000);
      });
      tooltip.addEventListener('mouseenter', () => {
        if (hideTimer) {
          clearTimeout(hideTimer);
          hideTimer = null;
        }
        tooltip.classList.remove('hidden');
      });
      tooltip.addEventListener('mouseleave', () => {
        hideTimer = setTimeout(() => {
          tooltip.classList.add('hidden');
        }, 200);
      });
    }
    
    const tooltipLogout = $('#tooltip-logout');
    if (tooltipLogout) tooltipLogout.addEventListener('click', logout);
    
    const upgradeBtn = $('#btn-upgrade');
    if (upgradeBtn) upgradeBtn.addEventListener('click', () => { window.location.href = 'pricing.html'; });
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
    paymentConfig = { enabled: false, has_paid: false, test_mode: false, quota: null };
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
    paymentConfig = { enabled: false, has_paid: false, test_mode: false, quota: null };
    renderAuthArea();
    $('#tasks-list').innerHTML = '<p class="text-gray-500">Please sign in to view your tasks</p>';
    return;
  }

  await loadPaymentConfig();
  renderAuthArea();
  loadTasks();
  showPaymentSuccess();
  claimPendingCheckout();
  handlePendingIntent();

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

function showQuotaError(message) {
  const status = $('#upload-status');
  if (!status) return;
  status.classList.remove('hidden');
  status.innerHTML = `
    <div class="bg-red-50 border border-red-200 rounded-lg p-4 text-sm">
      ${message}
      <button id="btn-quota-upgrade" class="mt-3 px-4 py-2 rounded-lg bg-amber-500 text-white hover:bg-amber-600 text-sm font-medium">Upgrade to Pro — $1/month for 2,000 pages</button>
    </div>`;
  const upgradeBtn = $('#btn-quota-upgrade');
  if (upgradeBtn) upgradeBtn.addEventListener('click', () => { window.location.href = 'pricing.html'; });
}

// ---------- Creem 支付 ----------

async function loadPaymentConfig() {
  if (!currentUser) {
    paymentConfig = { enabled: false, has_paid: false, test_mode: false, quota: null };
    return;
  }
  try {
    const res = await fetch(`${API_BASE}/api/payments/config`);
    if (res.ok) paymentConfig = await res.json();
  } catch (e) {
    paymentConfig = { enabled: false, has_paid: false, test_mode: false, quota: null };
  }
}

function showPaymentSuccess() {
  // 从 Creem 支付页跳回时 URL 带 ?checkout=success&checkout_id=...
  // checkout_id 留给 claimPendingCheckout 认领（匿名支付场景）
  const params = new URLSearchParams(window.location.search);
  if (params.get('checkout') === 'success') {
    const checkoutId = params.get('checkout_id');
    if (checkoutId) localStorage.setItem('pending_checkout_id', checkoutId);
    window.history.replaceState({}, document.title, window.location.pathname);
    const status = $('#upload-status');
    status.classList.remove('hidden');
    status.innerHTML = '<p class="text-green-600">Payment received! Your Pro access is being activated…</p>';
  }
}

// 匿名先付款、后登录的用户：登录后凭 checkout_id 向后端认领权益（兼容旧流程）
async function claimPendingCheckout() {
  const checkoutId = localStorage.getItem('pending_checkout_id');
  if (!checkoutId || !currentUser) return;
  localStorage.removeItem('pending_checkout_id');
  try {
    const res = await fetch(`${API_BASE}/api/billing/claim`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ checkout_id: checkoutId }),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || data.detail || 'claim failed');
    const status = $('#upload-status');
    status.classList.remove('hidden');
    status.innerHTML = '<p class="text-green-600">Pro activated — 2,000 pages/month. Enjoy!</p>';
    await loadPaymentConfig();
    renderAuthArea();
  } catch (err) {
    console.warn('checkout claim failed', err);
  }
}

// 登录后检测 sessionStorage 中的 pending_intent，自动续跳到支付页
async function handlePendingIntent() {
  const intent = sessionStorage.getItem('pending_intent');
  if (!intent || !currentUser) return;
  
  if (intent === 'checkout') {
    sessionStorage.removeItem('pending_intent');
    try {
      const res = await fetch(`${API_BASE}/api/billing/checkout`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      const data = await res.json();
      if (res.ok && data.checkout_url) {
        window.location.href = data.checkout_url;
      } else {
        throw new Error(data.detail || 'Failed to create checkout');
      }
    } catch (err) {
      console.error('Auto-checkout after login failed:', err);
      alert('Unable to continue to checkout: ' + err.message);
    }
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
  
  // 上传前仅拦截已用尽的额度。
  const quota = paymentConfig.quota;
  if (quota) {
    const remaining = Math.max(0, quota.limit - quota.used_pages);
    if (remaining === 0) {
      showQuotaError(`
        <p class="text-red-700 font-medium">Your quota has been exhausted.</p>
        <p class="text-red-600 mt-1">You have used all ${quota.limit} pages for this month. Quota resets on the 1st.</p>`);
      return;
    }
  }
  
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
      // 【后端验证失败】区分配额已用尽 vs 配额不足两种情况
      const d = data && data.detail;
      if (res.status === 403 && d && typeof d === 'object') {
        if (d.error === 'quota_exhausted') {
          showQuotaError(`
            <p class="text-red-700 font-medium">Your quota has been exhausted.</p>
            <p class="text-red-600 mt-1">You have used all ${d.limit} pages for this month. Quota resets on the 1st.</p>`);
        } else if (d.error === 'quota_exceeded') {
          showQuotaError(`
            <p class="text-red-700 font-medium">Not enough quota for this PDF.</p>
            <p class="text-red-600 mt-1">This PDF has ${d.pages_required} page${d.pages_required === 1 ? '' : 's'}, but you only have ${d.pages_remaining} page${d.pages_remaining === 1 ? '' : 's'} left this month.</p>`);
        }
        loadPaymentConfig().then(renderAuthArea);
        return;
      }
      throw new Error((d && typeof d === 'string' ? d : 'Upload failed'));
    }
    status.innerHTML = '<p class="text-green-600">Upload successful. OCR processing has started.</p>';
    loadPaymentConfig().then(renderAuthArea);
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
    
    // 获取队列状态
    let queueInfo = null;
    try {
      const queueRes = await fetch(`${API_BASE}/api/tasks/queue/status`);
      if (queueRes.ok) {
        queueInfo = await queueRes.json();
      }
    } catch (e) {
      // 队列状态获取失败不影响主流程
    }
    
    // 显示队列状态提示（如果有排队任务）
    let queueNotice = '';
    if (queueInfo && (queueInfo.user_queued > 0 || queueInfo.processing_count > 0)) {
      // 统计数据库中 pending 状态的任务数量
      const pendingCount = tasks.filter(t => t.status === 'pending').length;
      const displayQueued = Math.max(queueInfo.user_queued, pendingCount);
      
      const waitMsg = queueInfo.estimated_wait_minutes > 0 
        ? `· Estimated wait: ~${queueInfo.estimated_wait_minutes} min`
        : '';
      queueNotice = `
        <div class="bg-blue-50 border border-blue-200 rounded-lg p-3 mb-4 text-sm">
          <div class="flex items-start gap-2">
            <svg class="w-5 h-5 text-blue-600 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/>
            </svg>
            <div class="text-blue-700">
              <p class="font-medium">Processing Queue Status</p>
              <p class="text-blue-600 mt-1">
                ${queueInfo.processing_count} task${queueInfo.processing_count === 1 ? '' : 's'} in progress 
                · ${displayQueued} of your task${displayQueued === 1 ? '' : 's'} queued
                ${waitMsg}
              </p>
              <p class="text-blue-500 text-xs mt-1">Fair scheduling: All users get equal processing opportunities</p>
            </div>
          </div>
        </div>`;
    }
    
    if (!tasks.length) {
      container.innerHTML = queueNotice + '<p class="text-gray-500">No tasks yet</p>';
      return;
    }

    container.innerHTML = queueNotice + tasks.map(t => {
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
  // 先获取任务信息，判断是否正在处理
  let task;
  try {
    const res = await fetch(`${API_BASE}/api/tasks/${id}`);
    if (res.ok) {
      task = await res.json();
    }
  } catch (err) {
    // 获取失败时继续删除流程
  }
  
  // 如果任务正在处理，提示用户确认
  if (task && task.status === 'processing') {
    const currentPage = task.current_page || 0;
    const totalPages = task.total_pages || 0;
    const progress = totalPages > 0 ? `${currentPage}/${totalPages}` : 'in progress';
    if (!confirm(`This task is currently being processed (${progress}). Are you sure you want to cancel and delete it?`)) {
      return;
    }
  } else {
    if (!confirm('Delete this task and its generated files?')) return;
  }
  
  try {
    const res = await fetch(`${API_BASE}/api/tasks/${id}`, { method: 'DELETE' });
    if (!res.ok) throw new Error('Delete failed');
    await loadTasks();
  } catch (err) {
    alert('Failed to delete task: ' + err.message);
  }
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
