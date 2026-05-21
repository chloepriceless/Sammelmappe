// Sammelmappe — main app logic
(() => {
'use strict';

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const state = {
  filter: 'open',
  query: '',
  invoices: [],
  totals: {},
  selected: new Set(),
  currentEdit: null,
};

const fmtEUR = (v) => (v == null) ? '—' : new Intl.NumberFormat('de-DE', { style: 'currency', currency: 'EUR' }).format(v);
const fmtDate = (s) => s ? new Date(s + 'T00:00:00').toLocaleDateString('de-DE') : '—';

// --- Toasts ---------------------------------------------------------------
function toast(msg, kind = 'success', ms = 3500) {
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.textContent = msg;
  $('#toasts').appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity .25s'; }, ms - 250);
  setTimeout(() => el.remove(), ms);
}

// --- API ------------------------------------------------------------------
async function api(path, options = {}) {
  const r = await fetch(path, { credentials: 'same-origin', ...options });
  if (r.status === 401) { location.href = '/login'; throw new Error('unauth'); }
  if (!r.ok) {
    const data = await r.json().catch(() => ({}));
    const err = new Error(data.detail || `HTTP ${r.status}`);
    err.status = r.status;
    err.data = data;
    throw err;
  }
  if (r.status === 204) return null;
  return r.json();
}

// --- Tabs -----------------------------------------------------------------
function showTab(name) {
  for (const t of ['invoices', 'submissions', 'stats']) {
    $(`#tab-${t}`).classList.toggle('hidden', t !== name);
  }
  $$('.tabbar button').forEach(b => {
    if (b.dataset.tab === name) b.setAttribute('aria-current', 'page');
    else b.removeAttribute('aria-current');
  });
  if (name === 'submissions') loadSubmissions();
  if (name === 'stats') loadStats();
}
$$('.tabbar button').forEach(b => b.addEventListener('click', () => showTab(b.dataset.tab)));

// --- Invoices list --------------------------------------------------------
async function loadInvoices() {
  try {
    const params = new URLSearchParams();
    if (state.filter) params.set('status', state.filter);
    if (state.query) params.set('q', state.query);
    const data = await api('/api/invoices?' + params.toString());
    state.invoices = data.items;
    state.totals = data.totals || {};
    renderList();
    updateCounts();
  } catch (e) {
    if (e.message !== 'unauth') toast(`Laden fehlgeschlagen: ${e.message}`, 'error');
  }
}

function updateCounts() {
  $('#count-open').textContent = state.totals.open?.count ?? 0;
  $('#count-submitted').textContent = state.totals.submitted?.count ?? 0;
}

function renderList() {
  const list = $('#invoice-list');
  const empty = $('#empty-state');
  list.innerHTML = '';
  if (!state.invoices.length) {
    empty.classList.remove('hidden');
    return;
  }
  empty.classList.add('hidden');
  for (const inv of state.invoices) {
    list.appendChild(renderCard(inv));
  }
}

function renderCard(inv) {
  const card = document.createElement('div');
  card.className = 'card' + (state.selected.has(inv.id) ? ' selected' : '');
  card.dataset.id = inv.id;

  const thumb = document.createElement('div');
  thumb.className = 'thumb';
  thumb.style.backgroundImage = `url(/api/invoices/${inv.id}/thumbnail)`;
  if (inv.mime === 'application/pdf') {
    thumb.innerHTML = '<div class="placeholder">📄</div>';
  }

  const meta = document.createElement('div');
  meta.className = 'meta';
  meta.innerHTML = `
    <h3 class="vendor">${escapeHtml(inv.vendor || inv.original_name || 'Unbekannt')}</h3>
    <div class="sub">
      <span>${fmtDate(inv.invoice_date)}</span>
      ${inv.category ? `<span>· ${escapeHtml(inv.category)}</span>` : ''}
      ${inv.invoice_number ? `<span>· #${escapeHtml(inv.invoice_number)}</span>` : ''}
    </div>
  `;

  const right = document.createElement('div');
  right.className = 'right';
  const amt = document.createElement('div');
  amt.className = 'amount';
  amt.textContent = fmtEUR(inv.amount);
  right.appendChild(amt);

  const badge = document.createElement('span');
  if (inv.amount == null) {
    badge.className = 'badge no-amount';
    badge.textContent = 'Betrag?';
  } else if (inv.ocr_confidence != null && inv.ocr_confidence < 0.5 && !inv.manually_edited) {
    badge.className = 'badge low-conf';
    badge.textContent = 'Prüfen';
  } else if (inv.status === 'submitted') {
    badge.className = 'badge submitted';
    badge.textContent = 'Eingereicht';
  } else {
    badge.className = 'badge';
    badge.textContent = 'Offen';
  }
  right.appendChild(badge);

  card.appendChild(thumb);
  card.appendChild(meta);
  card.appendChild(right);

  // Tap: toggle select (for open) OR open edit
  let pressTimer = null;
  let longPress = false;
  card.addEventListener('touchstart', () => {
    longPress = false;
    pressTimer = setTimeout(() => { longPress = true; openEdit(inv); }, 500);
  }, { passive: true });
  card.addEventListener('touchend', () => clearTimeout(pressTimer));
  card.addEventListener('touchmove', () => clearTimeout(pressTimer));

  card.addEventListener('click', (e) => {
    if (longPress) return;
    if (inv.status === 'submitted') {
      // submitted invoices can't be re-selected; just edit/view
      openEdit(inv);
      return;
    }
    if (inv.amount == null) {
      // can't include in export without amount — go fix it first
      openEdit(inv);
      toast('Bitte zuerst den Betrag eintragen.', 'warn');
      return;
    }
    toggleSelect(inv.id);
  });

  // Double-click on desktop opens edit
  card.addEventListener('dblclick', () => openEdit(inv));

  return card;
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// --- Selection ------------------------------------------------------------
function toggleSelect(id) {
  if (state.selected.has(id)) state.selected.delete(id);
  else state.selected.add(id);
  renderList();
  updateSelbar();
}
function clearSelection() {
  state.selected.clear();
  renderList();
  updateSelbar();
}
function updateSelbar() {
  const bar = $('#selbar');
  if (state.selected.size === 0) {
    bar.classList.remove('visible');
    return;
  }
  bar.classList.add('visible');
  const selected = state.invoices.filter(i => state.selected.has(i.id));
  const sum = selected.reduce((a, b) => a + (b.amount || 0), 0);
  $('#sel-count').textContent = state.selected.size;
  $('#sel-sum').textContent = fmtEUR(sum);
}
$('#sel-clear').addEventListener('click', clearSelection);
$('#sel-export').addEventListener('click', () => openExport());

// --- Filter + Search ------------------------------------------------------
$$('.chip').forEach(c => c.addEventListener('click', () => {
  state.filter = c.dataset.filter;
  $$('.chip').forEach(x => x.setAttribute('aria-pressed', x === c ? 'true' : 'false'));
  clearSelection();
  loadInvoices();
}));

let searchTimer = null;
$('#search').addEventListener('input', (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    state.query = e.target.value.trim();
    loadInvoices();
  }, 250);
});

// --- Upload ---------------------------------------------------------------
const fab = $('#fab');
const camInput = $('#capture-cam');
const fileInput = $('#capture-file');

fab.addEventListener('click', () => {
  // On iOS Safari, capture attribute opens camera directly.
  // On desktop, no capture support → fall back to file picker.
  if (/iPhone|iPad|iPod|Android/i.test(navigator.userAgent)) {
    // Use a small menu? Easier: just trigger camera; user can switch to library in iOS sheet.
    camInput.click();
  } else {
    fileInput.click();
  }
});

camInput.addEventListener('change', (e) => handleFiles(e.target.files));
fileInput.addEventListener('change', (e) => handleFiles(e.target.files));

async function handleFiles(filelist) {
  const files = Array.from(filelist);
  if (!files.length) return;
  fab.classList.add('busy');
  fab.innerHTML = '<span class="spinner"></span>';

  let okCount = 0;
  for (const file of files) {
    try {
      await uploadOne(file);
      okCount++;
    } catch (e) {
      if (e.status === 409) {
        toast(`Doppelte Rechnung: ${file.name}`, 'warn', 4500);
      } else {
        toast(`Upload fehlgeschlagen (${file.name}): ${e.message}`, 'error', 5000);
      }
    }
  }
  fab.classList.remove('busy');
  fab.innerHTML = '📷';
  camInput.value = '';
  fileInput.value = '';
  if (okCount) {
    toast(`${okCount} Rechnung${okCount === 1 ? '' : 'en'} hochgeladen`, 'success');
  }
  loadInvoices();
}

async function uploadOne(file) {
  const fd = new FormData();
  fd.append('file', file);
  const r = await fetch('/api/invoices', { method: 'POST', body: fd, credentials: 'same-origin' });
  if (r.status === 401) { location.href = '/login'; throw new Error('unauth'); }
  if (!r.ok) {
    const data = await r.json().catch(() => ({}));
    const err = new Error(data.detail || `HTTP ${r.status}`);
    err.status = r.status;
    err.data = data;
    throw err;
  }
  return r.json();
}

// Drag and drop (desktop convenience)
;['dragenter', 'dragover'].forEach(ev =>
  document.body.addEventListener(ev, (e) => {
    e.preventDefault();
    document.body.classList.add('dropzone-active');
  })
);
;['dragleave', 'drop'].forEach(ev =>
  document.body.addEventListener(ev, (e) => {
    e.preventDefault();
    if (ev === 'drop') handleFiles(e.dataTransfer.files);
    document.body.classList.remove('dropzone-active');
  })
);

// --- Edit modal -----------------------------------------------------------
function openEdit(inv) {
  state.currentEdit = inv;
  $('#edit-preview').src = `/api/invoices/${inv.id}/thumbnail?t=${Date.now()}`;
  $('#edit-vendor').value = inv.vendor || '';
  $('#edit-amount').value = inv.amount != null ? String(inv.amount).replace('.', ',') : '';
  $('#edit-date').value = inv.invoice_date || '';
  $('#edit-number').value = inv.invoice_number || '';
  $('#edit-category').value = inv.category || '';
  $('#edit-notes').value = inv.notes || '';
  $('#edit-download').href = `/api/invoices/${inv.id}/file`;
  $('#modal-edit').classList.add('visible');
}
function closeEdit() {
  $('#modal-edit').classList.remove('visible');
  state.currentEdit = null;
}
$('#edit-cancel').addEventListener('click', closeEdit);
$('#modal-edit').addEventListener('click', (e) => { if (e.target.id === 'modal-edit') closeEdit(); });

$('#edit-save').addEventListener('click', async () => {
  const id = state.currentEdit?.id;
  if (!id) return;
  const rawAmount = $('#edit-amount').value.trim().replace(/\s/g, '').replace('.', '').replace(',', '.');
  const amount = rawAmount === '' ? null : Number(rawAmount);
  if (rawAmount !== '' && !Number.isFinite(amount)) {
    toast('Betrag ist keine gültige Zahl', 'error');
    return;
  }
  const payload = {
    vendor: $('#edit-vendor').value.trim() || null,
    amount,
    invoice_date: $('#edit-date').value || null,
    invoice_number: $('#edit-number').value.trim() || null,
    category: $('#edit-category').value || null,
    notes: $('#edit-notes').value.trim() || null,
  };
  try {
    await api(`/api/invoices/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    toast('Gespeichert', 'success');
    closeEdit();
    loadInvoices();
  } catch (e) {
    toast(`Speichern fehlgeschlagen: ${e.message}`, 'error');
  }
});

$('#edit-delete').addEventListener('click', async () => {
  const id = state.currentEdit?.id;
  if (!id) return;
  if (!confirm('Rechnung wirklich löschen?')) return;
  try {
    await api(`/api/invoices/${id}`, { method: 'DELETE' });
    toast('Gelöscht', 'success');
    state.selected.delete(id);
    closeEdit();
    loadInvoices();
  } catch (e) {
    toast(`Löschen fehlgeschlagen: ${e.message}`, 'error');
  }
});

// --- Export modal ---------------------------------------------------------
function openExport() {
  const selected = state.invoices.filter(i => state.selected.has(i.id));
  if (!selected.length) return;
  const missing = selected.filter(i => i.amount == null);
  if (missing.length) {
    toast(`${missing.length} Rechnung${missing.length === 1 ? '' : 'en'} ohne Betrag — bitte zuerst eintragen`, 'warn', 4500);
    return;
  }
  $('#exp-count').textContent = selected.length;
  $('#exp-sum').textContent = fmtEUR(selected.reduce((a, b) => a + (b.amount || 0), 0));
  $('#exp-label').value = '';
  $('#exp-mark').checked = true;
  $('#modal-export').classList.add('visible');
}
function closeExport() { $('#modal-export').classList.remove('visible'); }
$('#exp-cancel').addEventListener('click', closeExport);
$('#modal-export').addEventListener('click', (e) => { if (e.target.id === 'modal-export') closeExport(); });

$('#exp-confirm').addEventListener('click', async () => {
  const btn = $('#exp-confirm');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Erstelle ZIP…';
  try {
    const result = await api('/api/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        invoice_ids: Array.from(state.selected),
        label: $('#exp-label').value.trim() || null,
        mark_submitted: $('#exp-mark').checked,
      }),
    });
    closeExport();
    clearSelection();
    loadInvoices();
    toast(`Export bereit: ${result.invoice_count} Rechnungen, ${fmtEUR(result.total_amount)}`, 'success', 5000);
    // Auto-trigger download
    const a = document.createElement('a');
    a.href = result.download_url;
    a.download = result.zip_filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
  } catch (e) {
    toast(`Export fehlgeschlagen: ${e.message}`, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'ZIP erstellen';
  }
});

// --- Submissions ----------------------------------------------------------
async function loadSubmissions() {
  try {
    const subs = await api('/api/submissions');
    const list = $('#submissions-list');
    list.innerHTML = '';
    if (!subs.length) {
      $('#submissions-empty').classList.remove('hidden');
      return;
    }
    $('#submissions-empty').classList.add('hidden');
    for (const s of subs) {
      const el = document.createElement('div');
      el.className = 'sub-card';
      el.innerHTML = `
        <div class="title">${escapeHtml(s.label || 'Einreichung #' + s.id)}</div>
        <div class="meta-row">
          <span>${new Date(s.created_at).toLocaleString('de-DE')}</span>
          <span>${s.invoice_count} Rechnungen</span>
        </div>
        <div class="meta-row" style="margin-top:8px;">
          <span style="font-size:18px;font-weight:800;color:var(--text);font-variant-numeric:tabular-nums">${fmtEUR(s.total_amount)}</span>
          <span style="font-size:12px">${escapeHtml(s.zip_filename)}</span>
        </div>
        <div class="actions">
          <a class="primary" href="${s.download_url}" download="${escapeHtml(s.zip_filename)}">ZIP herunterladen</a>
          <button data-revert="${s.id}">Zurücksetzen</button>
        </div>
      `;
      list.appendChild(el);
    }
    $$('#submissions-list button[data-revert]').forEach(b => {
      b.addEventListener('click', async () => {
        if (!confirm('Alle Rechnungen dieser Einreichung wieder als "Offen" markieren?')) return;
        try {
          await api(`/api/submissions/${b.dataset.revert}/revert`, { method: 'POST' });
          toast('Auf „Offen" zurückgesetzt', 'success');
          loadSubmissions();
          loadInvoices();
        } catch (e) {
          toast(`Fehler: ${e.message}`, 'error');
        }
      });
    });
  } catch (e) {
    if (e.message !== 'unauth') toast(`Laden fehlgeschlagen: ${e.message}`, 'error');
  }
}

// --- Stats ----------------------------------------------------------------
async function loadStats() {
  try {
    const s = await api('/api/stats');
    const grid = $('#stats-grid');
    const open = s.by_status.open || { count: 0, sum: 0 };
    const submitted = s.by_status.submitted || { count: 0, sum: 0 };
    grid.innerHTML = `
      <div class="stat warn"><div class="label">Offen</div><div class="value">${fmtEUR(open.sum)}</div><div style="font-size:13px;color:var(--text-muted);margin-top:2px">${open.count} Rechnungen</div></div>
      <div class="stat success"><div class="label">Eingereicht</div><div class="value">${fmtEUR(submitted.sum)}</div><div style="font-size:13px;color:var(--text-muted);margin-top:2px">${submitted.count} Rechnungen</div></div>
      <div class="stat"><div class="label">Einreichungen</div><div class="value">${s.submissions_total}</div></div>
      <div class="stat"><div class="label">Gesamt</div><div class="value">${fmtEUR(open.sum + submitted.sum)}</div></div>
    `;
    const cats = $('#stats-categories');
    cats.innerHTML = '';
    for (const c of s.by_category.sort((a, b) => b.sum - a.sum)) {
      const row = document.createElement('div');
      row.className = 'card';
      row.style.cursor = 'default';
      row.innerHTML = `
        <div class="thumb"><div class="placeholder">🏷️</div></div>
        <div class="meta"><h3 class="vendor">${escapeHtml(c.category)}</h3><div class="sub">${c.count} Rechnungen</div></div>
        <div class="right"><div class="amount">${fmtEUR(c.sum)}</div></div>
      `;
      cats.appendChild(row);
    }
  } catch (e) {
    if (e.message !== 'unauth') toast(`Laden fehlgeschlagen: ${e.message}`, 'error');
  }
}

// --- Init -----------------------------------------------------------------
loadInvoices();

// Refresh when tab becomes visible again (e.g. switching apps on phone)
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) loadInvoices();
});

})();
