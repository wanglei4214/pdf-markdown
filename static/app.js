const API_BASE = '';
let pollTimer = null;
let currentTaskId = null;

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

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
  return d.toLocaleString('zh-CN');
}

function statusBadge(status) {
  const map = {
    pending: ['等待中', 'bg-gray-100 text-gray-700'],
    processing: ['处理中', 'bg-blue-100 text-blue-700'],
    success: ['完成', 'bg-green-100 text-green-700'],
    failed: ['失败', 'bg-red-100 text-red-700'],
  };
  const [text, cls] = map[status] || ['未知', 'bg-gray-100'];
  return `<span class="px-2 py-1 rounded text-xs font-medium ${cls}">${text}</span>`;
}

async function uploadFile(file) {
  const status = $('#upload-status');
  status.classList.remove('hidden');
  status.innerHTML = '<p class="text-blue-600">正在上传...</p>';

  const form = new FormData();
  form.append('file', file);

  try {
    const res = await fetch(`${API_BASE}/api/tasks/upload`, {
      method: 'POST',
      body: form,
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || '上传失败');
    }
    status.innerHTML = '<p class="text-green-600">上传成功，OCR 已开始处理</p>';
    showView('tasks');
    loadTasks();
  } catch (err) {
    status.innerHTML = `<p class="text-red-600">错误：${err.message}</p>`;
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
  const container = $('#tasks-list');
  try {
    const res = await fetch(`${API_BASE}/api/tasks`);
    const tasks = await res.json();
    if (!tasks.length) {
      container.innerHTML = '<p class="text-gray-500">暂无任务</p>';
      return;
    }

    container.innerHTML = tasks.map(t => {
      const progress = t.total_pages
        ? Math.min(100, Math.round((t.current_page / t.total_pages) * 100))
        : 0;
      const pageProgress = t.status === 'processing'
        ? (t.total_pages ? `· 当前进度 ${t.current_page}/${t.total_pages} 页` : '· 正在分析 PDF 页面...')
        : (t.total_pages ? `· 页码 ${t.current_page}/${t.total_pages}` : '');
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
              <button data-id="${t.id}" class="btn-preview px-3 py-1.5 rounded bg-indigo-50 text-indigo-700 hover:bg-indigo-100 text-sm">预览</button>
              <a href="${API_BASE}/api/tasks/${t.id}/download" class="px-3 py-1.5 rounded bg-indigo-600 text-white hover:bg-indigo-700 text-sm">下载</a>
            ` : `
              <button data-id="${t.id}" class="btn-retry px-3 py-1.5 rounded bg-blue-50 text-blue-700 hover:bg-blue-100 text-sm">${t.status === 'processing' ? '重启处理' : '继续处理'}</button>
            `}
            <button data-id="${t.id}" class="btn-delete px-3 py-1.5 rounded bg-gray-100 text-gray-700 hover:bg-gray-200 text-sm">删除</button>
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
    container.innerHTML = `<p class="text-red-500">加载失败：${err.message}</p>`;
  }
}

async function deleteTask(id) {
  if (!confirm('确定删除该任务及生成的文件？')) return;
  await fetch(`${API_BASE}/api/tasks/${id}`, { method: 'DELETE' });
  loadTasks();
}

async function retryTask(id) {
  try {
    const res = await fetch(`${API_BASE}/api/tasks/${id}/retry`, { method: 'POST' });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || '继续处理失败');
    }
    loadTasks();
  } catch (err) {
    alert('继续处理失败：' + err.message);
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
      $('#btn-copy').textContent = '已复制';
      setTimeout(() => $('#btn-copy').textContent = old, 1500);
    };

    showView('result');
    // 重置移动端目录为收起状态
    const toc = $('#result-toc');
    toc.classList.remove('toc-open');
    toc.innerHTML = '';
    $('#btn-toc').textContent = '查看目录';
    requestAnimationFrame(() => buildToc());
  } catch (err) {
    alert('加载结果失败：' + err.message);
  }
}

function buildToc() {
  const headings = [...$$('#result-content h1, #result-content h2, #result-content h3')];
  const toc = $('#result-toc');
  if (!headings.length) {
    toc.innerHTML = '<p class="text-sm text-gray-400">暂无目录</p>';
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
        // 移动端整页滚动：目录收起后定位到标题
        const y = h.getBoundingClientRect().top + window.scrollY - 16;
        toc.classList.remove('toc-open');
        $('#btn-toc').textContent = '查看目录';
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
  toc.innerHTML = '<h3 class="font-semibold mb-3 text-sm text-gray-700">目录</h3>';
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
    const open = toc.classList.toggle('toc-open');
    $('#btn-toc').textContent = open ? '收起目录' : '查看目录';
  });
}

document.addEventListener('DOMContentLoaded', () => {
  setupUpload();
  setupNav();
  loadTasks();
});
