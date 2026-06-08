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
  updateSelectAllLabel();
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
      ${inv.doc_type === 'Kassenbeleg' ? '<span class="kb">🧾 Kassenbeleg</span>' : ''}
      ${inv.doc_type === 'E-Rechnung' ? '<span class="erech">📐 E-Rechnung</span>' : ''}
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

  // Explicit single-click "Öffnen" — opens the detail/edit dialog without the
  // double-click (desktop) or long-press (mobile). stopPropagation so it doesn't
  // also toggle the card's export selection.
  const openBtn = document.createElement('button');
  openBtn.className = 'card-open';
  openBtn.type = 'button';
  openBtn.title = 'Beleg öffnen';
  openBtn.setAttribute('aria-label', 'Beleg öffnen');
  openBtn.textContent = 'Öffnen';
  openBtn.addEventListener('click', (e) => { e.stopPropagation(); openEdit(inv); });
  right.appendChild(openBtn);

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

// Invoices that can go into an export: open (not yet submitted) and with an amount.
function selectableInvoices() {
  return state.invoices.filter(i => i.status !== 'submitted' && i.amount != null);
}

// Select all open invoices in the current view at once — or deselect them all if
// they're already selected (the button doubles as select-all / deselect-all).
function selectAllOpen() {
  const selectable = selectableInvoices();
  if (!selectable.length) {
    toast('Keine offenen Belege mit Betrag in dieser Ansicht.', 'warn');
    return;
  }
  const allSelected = selectable.every(i => state.selected.has(i.id));
  selectable.forEach(i => allSelected ? state.selected.delete(i.id) : state.selected.add(i.id));
  renderList();
  updateSelbar();
}

function updateSelectAllLabel() {
  const btn = $('#select-all-open');
  if (!btn) return;
  const selectable = selectableInvoices();
  const allSelected = selectable.length > 0 && selectable.every(i => state.selected.has(i.id));
  btn.textContent = allSelected ? 'Alle abwählen' : 'Alle offenen auswählen';
  btn.disabled = selectable.length === 0;
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
$('#select-all-open').addEventListener('click', selectAllOpen);

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
const libInput = $('#capture-library');
const fileInput = $('#capture-file');
const uploadSheet = $('#modal-upload');
const isMobile = /iPhone|iPad|iPod|Android/i.test(navigator.userAgent);

fab.addEventListener('click', () => {
  if (isMobile) {
    uploadSheet.classList.add('visible');
  } else {
    // Desktop: skip the sheet, just open the file picker (which already covers any source).
    fileInput.click();
  }
});

uploadSheet.addEventListener('click', (e) => {
  if (e.target.id === 'modal-upload' || e.target.id === 'upload-cancel') {
    uploadSheet.classList.remove('visible');
    return;
  }
  const btn = e.target.closest('.sheet-btn');
  if (!btn) return;
  uploadSheet.classList.remove('visible');
  const action = btn.dataset.action;
  if (action === 'camera')  camInput.click();
  if (action === 'library') libInput.click();
  if (action === 'file')    fileInput.click();
});

camInput.addEventListener('change', (e) => handleFiles(e.target.files));
libInput.addEventListener('change', (e) => handleFiles(e.target.files));
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
  libInput.value = '';
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
  $('#edit-labor').value = inv.labor_amount != null ? String(inv.labor_amount).replace('.', ',') : '';
  $('#edit-payment-method').value = inv.payment_method || '';
  $('#edit-payment-date').value = inv.payment_date || '';
  $('#edit-download').href = `/api/invoices/${inv.id}/file?download=true`;

  // OCR meta
  const meta = $('#edit-ocr-meta');
  const engine = inv.ocr_engine || '—';
  const conf = inv.ocr_confidence != null ? Math.round(inv.ocr_confidence * 100) + '%' : '—';
  const docType = inv.doc_type || 'Rechnung';
  const typeLabel = docType === 'Kassenbeleg' ? '🧾 Kassenbeleg (TSE-QR)'
                  : docType === 'E-Rechnung' ? '📐 E-Rechnung (XML)'
                  : '📄 Rechnung';
  const retentionLine = inv.retention_until
    ? `<span title="§ 14b UStG: Belege zu Leistungen an einem Grundstück 2 Jahre aufbewahren — auch Zahlungsbeleg, Bauvertrag und Abnahmeprotokoll. Tipp: Für Gewährleistung bei Baumängeln gilt zudem i.d.R. eine 5-jährige Verjährung ab Abnahme (§ 634a BGB) — Rechnung + Abnahmeprotokoll entsprechend länger behalten. Keine Steuer-/Rechtsberatung.">Aufbewahren bis: <strong>${fmtDate(inv.retention_until)}</strong> · §14b</span>`
    : '';
  meta.innerHTML = `
    <span>Typ: <strong>${typeLabel}</strong></span>
    <span>Engine: <strong>${escapeHtml(engine)}</strong></span>
    <span>Konfidenz: <strong>${conf}</strong></span>
    ${retentionLine}
    ${inv.manually_edited ? '<span><strong>manuell bearbeitet</strong></span>' : ''}
  `;

  // Reset any leftover diff panel from a previous invoice
  $('#reocr-diff').classList.add('hidden');

  // E-invoice positions (read-only, lazy — only for structured e-invoices)
  renderEditLines(inv);

  $('#modal-edit').classList.add('visible');
  refreshReocrButtons();
}

// Fetch + render the invoice positions read straight from the e-invoice XML.
// View-only nicety: never blocks the dialog, fails silently.
async function renderEditLines(inv) {
  const box = $('#edit-lines');
  box.classList.add('hidden');
  box.innerHTML = '';
  if (!inv || inv.doc_type !== 'E-Rechnung') return;
  try {
    const data = await api(`/api/invoices/${inv.id}/lines`);
    if (state.currentEdit?.id !== inv.id) return;  // a different invoice opened meanwhile
    if (!data || !data.available || !data.lines.length) return;
    const rows = data.lines.map(l => {
      const qtyNum = l.quantity != null ? String(l.quantity).replace('.', ',') : '';
      const qty = qtyNum && l.unit_label ? `${qtyNum} ${escapeHtml(l.unit_label)}`
                : qtyNum ? qtyNum : '';
      const vat = l.vat_percent != null ? ` · ${String(l.vat_percent).replace('.', ',')} %` : '';
      const sub = (qty || vat) ? `<span class="li-sub">${qty}${vat}</span>` : '';
      const net = l.net_amount != null ? fmtEUR(l.net_amount) : '';
      return `<tr>
        <td class="li-pos">${escapeHtml(l.position || '')}</td>
        <td class="li-desc">${escapeHtml(l.description || '—')}${sub}</td>
        <td class="li-net">${net}</td>
      </tr>`;
    }).join('');
    box.innerHTML = `
      <div class="modal-section-label">Positionen (aus E-Rechnung)</div>
      <table class="li-table"><tbody>${rows}</tbody></table>
      <div class="hint">Beträge netto · direkt aus dem XML${data.truncated ? ' · Liste gekürzt' : ''}</div>`;
    box.classList.remove('hidden');
  } catch (e) {
    /* view-only — swallow errors, leave section hidden */
  }
}
function closeEdit() {
  $('#modal-edit').classList.remove('visible');
  state.currentEdit = null;
  pendingReocr = null;
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
  const rawLabor = $('#edit-labor').value.trim().replace(/\s/g, '').replace('.', '').replace(',', '.');
  const labor = rawLabor === '' ? null : Number(rawLabor);
  if (rawLabor !== '' && !Number.isFinite(labor)) {
    toast('Arbeitskosten-Anteil ist keine gültige Zahl', 'error');
    return;
  }
  const payload = {
    vendor: $('#edit-vendor').value.trim() || null,
    amount,
    invoice_date: $('#edit-date').value || null,
    invoice_number: $('#edit-number').value.trim() || null,
    category: $('#edit-category').value || null,
    notes: $('#edit-notes').value.trim() || null,
    labor_amount: labor,
    payment_method: $('#edit-payment-method').value || null,
    payment_date: $('#edit-payment-date').value || null,
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
  if (!confirm('Beleg wirklich löschen? Die Datei wird vom Server entfernt.')) return;
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

// --- Re-OCR + comparison preview -----------------------------------------
let pendingReocr = null;  // { engine, extracted, elapsed } awaiting Übernehmen / Verwerfen

async function refreshReocrButtons() {
  const claudeBtn = $('#edit-reocr-claude');
  const hint = $('#reocr-hint');
  try {
    const s = await api('/api/settings');
    if (s.anthropic_api_key_set) {
      claudeBtn.disabled = false;
      claudeBtn.title = '';
      hint.textContent = '';
    } else {
      claudeBtn.disabled = true;
      claudeBtn.title = 'Kein API Key gesetzt';
      hint.textContent = 'Claude steht erst zur Verfügung wenn ein API Key in den Einstellungen gesetzt ist.';
    }
  } catch (_) { /* ignore */ }
}

async function runReocrPreview(engine, btn) {
  const inv = state.currentEdit;
  if (!inv) return;
  hideDiff();
  const orig = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> erkenne…';
  try {
    const r = await api(`/api/invoices/${inv.id}/reocr?engine=${engine}&preview=true`, { method: 'POST' });
    pendingReocr = { engine, extracted: r.extracted, current: r.current };
    showDiff(r.current, r.extracted);
  } catch (e) {
    toast(`Re-OCR fehlgeschlagen: ${e.message}`, 'error', 6000);
  } finally {
    btn.disabled = false;
    btn.innerHTML = orig;
    refreshReocrButtons();
  }
}

$('#edit-reocr-tesseract').addEventListener('click', (e) => runReocrPreview('tesseract', e.currentTarget));
$('#edit-reocr-claude').addEventListener('click', (e) => runReocrPreview('claude', e.currentTarget));

function showDiff(current, extracted) {
  const panel = $('#reocr-diff');
  $('#diff-engine-label').textContent = (extracted.ocr_engine === 'claude' ? '✨ Claude' : '🔄 Tesseract');
  const conf = extracted.ocr_confidence != null ? Math.round(extracted.ocr_confidence * 100) + '%' : '—';
  $('#diff-meta').textContent = `${extracted.elapsed_seconds ?? '?'}s · Konfidenz ${conf}`;

  const rows = $('#diff-rows');
  rows.innerHTML = '';
  const fields = [
    { key: 'vendor',         label: 'Rechnungssteller', fmt: v => v || '—' },
    { key: 'amount',         label: 'Betrag',           fmt: v => fmtEUR(v) },
    { key: 'invoice_date',   label: 'Datum',            fmt: v => v ? fmtDate(v) : '—' },
    { key: 'invoice_number', label: 'Nummer',           fmt: v => v || '—' },
  ];
  for (const f of fields) {
    const oldVal = current[f.key];
    const newVal = extracted[f.key];
    const changed = (oldVal ?? null) !== (newVal ?? null) &&
                    !(oldVal == null && newVal == null);
    const row = document.createElement('div');
    row.className = 'diff-row' + (changed ? ' changed' : '');
    if (changed) {
      row.innerHTML = `
        <div class="label">${f.label}</div>
        <div class="vals">
          <div class="old">${escapeHtml(f.fmt(oldVal))}</div>
          <div class="arrow">→</div>
          <div class="new">${escapeHtml(f.fmt(newVal))}</div>
        </div>
      `;
    } else {
      row.innerHTML = `
        <div class="label">${f.label}</div>
        <div class="vals"><div class="same">unverändert: ${escapeHtml(f.fmt(oldVal))}</div></div>
      `;
    }
    rows.appendChild(row);
  }
  panel.classList.remove('hidden');
  panel.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function hideDiff() {
  $('#reocr-diff').classList.add('hidden');
  pendingReocr = null;
}

$('#diff-discard').addEventListener('click', hideDiff);

$('#diff-apply').addEventListener('click', async () => {
  if (!pendingReocr || !state.currentEdit) return;
  const inv = state.currentEdit;
  const { extracted, engine } = pendingReocr;
  const applyBtn = $('#diff-apply');
  applyBtn.disabled = true;
  applyBtn.innerHTML = '<span class="spinner"></span> übernehme…';
  try {
    // Re-run without preview to commit; this saves AND resets manually_edited so
    // the OCR meta strip reflects the new engine + confidence honestly.
    await api(`/api/invoices/${inv.id}/reocr?engine=${engine}`, { method: 'POST' });
    toast(`${engine === 'claude' ? 'Claude' : 'Tesseract'}-Ergebnis übernommen`, 'success');
    hideDiff();
    closeEdit();
    await loadInvoices();
    const fresh = state.invoices.find(x => x.id === inv.id);
    if (fresh) openEdit(fresh);
  } catch (e) {
    toast(`Übernehmen fehlgeschlagen: ${e.message}`, 'error');
  } finally {
    applyBtn.disabled = false;
    applyBtn.innerHTML = 'Übernehmen';
  }
});

// --- Detail viewer --------------------------------------------------------
const viewer = $('#viewer');
const viewerImg = $('#viewer-img');

function openViewer(invoice) {
  if (invoice.mime === 'application/pdf') {
    // Browsers render PDFs natively in a new tab much better than any in-page viewer.
    window.open(`/api/invoices/${invoice.id}/file`, '_blank');
    return;
  }
  viewerImg.src = `/api/invoices/${invoice.id}/file`;
  viewer.classList.add('visible');
}
function closeViewer() {
  viewer.classList.remove('visible');
  viewerImg.src = '';
}
$('#viewer-close').addEventListener('click', closeViewer);
viewer.addEventListener('click', (e) => { if (e.target === viewer) closeViewer(); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && viewer.classList.contains('visible')) closeViewer(); });

$('#edit-view').addEventListener('click', () => {
  if (state.currentEdit) openViewer(state.currentEdit);
});
$('#edit-preview').addEventListener('click', () => {
  if (state.currentEdit) openViewer(state.currentEdit);
});

// --- Settings modal ------------------------------------------------------
$('#settings-btn').addEventListener('click', () => openSettings());

async function openSettings() {
  $('#modal-settings').classList.add('visible');
  $('#set-anthropic-key').value = '';
  $('#set-status').className = 'status-row';
  $('#set-status').textContent = 'Lade…';
  try {
    const s = await api('/api/settings');
    renderSettingsStatus(s);
    $('#set-model').value = s.claude_model || '';
    $('#set-threshold').value = s.ocr_confidence_threshold ?? 0.6;
    $('#set-prefer-claude').checked = !!s.ocr_prefer_claude;
    $('#set-move-in').value = s.move_in_date || '';
  } catch (e) {
    $('#set-status').textContent = `Fehler: ${e.message}`;
    $('#set-status').className = 'status-row err';
  }
}

// Auto-save the toggle when the user flips it — feels more natural than
// having to click "Speichern" for what's a single switch.
$('#set-prefer-claude').addEventListener('change', async (e) => {
  const wanted = e.target.checked;
  try {
    const s = await api('/api/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ocr_prefer_claude: wanted }),
    });
    e.target.checked = !!s.ocr_prefer_claude;
    toast(s.ocr_prefer_claude
      ? 'Claude wird jetzt bevorzugt verwendet'
      : 'Standardmodus aktiv (Tesseract zuerst, Claude bei niedriger Konfidenz)', 'success', 3500);
  } catch (err) {
    e.target.checked = !wanted;
    toast(`Konnte nicht speichern: ${err.message}`, 'error');
  }
});

function renderSettingsStatus(s) {
  const el = $('#set-status');
  if (!s.anthropic_api_key_set) {
    el.textContent = 'Status: kein API Key — OCR läuft nur lokal mit Tesseract.';
    el.className = 'status-row warn';
  } else {
    const where = s.anthropic_api_key_source === 'db' ? 'in der DB gespeichert' : 'aus .env geladen';
    el.textContent = `Status: aktiv (${where}) — ${s.anthropic_api_key_preview || ''}`;
    el.className = 'status-row ok';
  }
}

function closeSettings() { $('#modal-settings').classList.remove('visible'); }
$('#set-cancel').addEventListener('click', closeSettings);
$('#modal-settings').addEventListener('click', (e) => { if (e.target.id === 'modal-settings') closeSettings(); });

$('#set-clear').addEventListener('click', async () => {
  if (!confirm('Anthropic API Key wirklich entfernen? Die OCR läuft danach wieder nur lokal.')) return;
  try {
    const s = await api('/api/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ anthropic_api_key: '' }),
    });
    renderSettingsStatus(s);
    toast('Key entfernt', 'success');
  } catch (e) {
    toast(`Fehler: ${e.message}`, 'error');
  }
});

$('#set-test').addEventListener('click', async () => {
  const btn = $('#set-test');
  const original = btn.textContent;
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> teste…';
  // If the user typed a new key but didn't save yet, save it first.
  const newKey = $('#set-anthropic-key').value.trim();
  try {
    if (newKey) {
      await api('/api/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ anthropic_api_key: newKey }),
      });
      $('#set-anthropic-key').value = '';
    }
    const r = await api('/api/settings/test-claude', { method: 'POST' });
    toast(`✓ Verbindung OK (Modell: ${r.model})`, 'success', 4500);
    const s = await api('/api/settings');
    renderSettingsStatus(s);
  } catch (e) {
    toast(`Test fehlgeschlagen: ${e.message}`, 'error', 5000);
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
});

$('#set-save').addEventListener('click', async () => {
  const body = {};
  const newKey = $('#set-anthropic-key').value.trim();
  if (newKey) body.anthropic_api_key = newKey;
  const model = $('#set-model').value.trim();
  if (model) body.claude_model = model;
  const t = parseFloat($('#set-threshold').value);
  if (Number.isFinite(t)) body.ocr_confidence_threshold = t;
  body.move_in_date = $('#set-move-in').value || '';  // always sent so it can be cleared
  if (Object.keys(body).length === 0) {
    closeSettings();
    return;
  }
  try {
    const s = await api('/api/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    renderSettingsStatus(s);
    $('#set-anthropic-key').value = '';
    toast('Gespeichert', 'success');
  } catch (e) {
    toast(`Speichern fehlgeschlagen: ${e.message}`, 'error');
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
    renderSection35a();
  } catch (e) {
    if (e.message !== 'unauth') toast(`Laden fehlgeschlagen: ${e.message}`, 'error');
  }
}

const S35A_REASONS = {
  no_labor: 'ohne erfassten Arbeitskosten-Anteil',
  cash: 'bar bezahlt (nicht anerkannt)',
  payment_unconfirmed: 'Zahlungsart nicht als Überweisung bestätigt',
  before_move_in: 'vor Einzug (Neubauphase)',
  no_move_in: 'Einzugsdatum nicht gesetzt',
  no_date: 'ohne Rechnungsdatum',
};

// § 35a Handwerkerbonus overview card. Self-contained: a failure here never breaks the stats tab.
async function renderSection35a() {
  const box = $('#stats-35a');
  if (!box) return;
  try {
    const s = await api('/api/section35a');
    let html = '';
    if (!s.move_in_set) {
      html += `<div class="s35a-note">Setz dein <b>Einzugsdatum</b> in den Einstellungen (⚙), damit § 35a geschätzt werden kann. Vor dem Einzug (Neubauphase) ist § 35a i.d.R. nicht begünstigt.</div>`;
    }
    html += `<div class="s35a-head">
      <div class="s35a-big">${fmtEUR(s.estimated_deduction)}</div>
      <div class="s35a-sub">geschätzte Steuerermäßigung · ${s.confirmed_count} Beleg${s.confirmed_count === 1 ? '' : 'e'} · ${fmtEUR(s.confirmed_labor)} Arbeitskosten</div>
    </div>`;
    if (s.years.length) {
      html += '<div class="s35a-years">' + s.years.map(y =>
        `<div class="s35a-year"><span>${y.year}</span><span>${fmtEUR(y.deduction)}${y.capped ? ' · Höchstbetrag erreicht' : ''}</span></div>`
      ).join('') + '</div>';
    }
    const exReasons = Object.entries(s.excluded || {});
    if (exReasons.length) {
      html += '<div class="s35a-excluded"><div class="s35a-ex-title">Nicht gezählt:</div>' +
        exReasons.map(([code, n]) => `<div>· ${n}× ${escapeHtml(S35A_REASONS[code] || code)}</div>`).join('') +
        (s.excluded_labor_total ? `<div class="s35a-ex-sum">${fmtEUR(s.excluded_labor_total)} erfasste Arbeitskosten zählen aktuell nicht</div>` : '') +
        '</div>';
    }
    if (s.year_assumed_any) {
      html += `<div class="hint">Belege ohne Zahlungsdatum: Steuerjahr aus dem Rechnungsdatum geschätzt (maßgeblich ist das Zahlungsjahr, § 11 EStG).</div>`;
    }
    html += `<div class="hint s35a-caveat">20 % der Arbeitskosten, max 1.200 €/Jahr. Nur Arbeit (kein Material), nur unbar, nur am bezogenen Haushalt (nicht Neubau). § 35a mindert die Steuer, nicht das Einkommen — bei zu geringer Steuer verpufft ein Teil. <b>Keine Steuerberatung.</b></div>`;
    box.innerHTML = html;
  } catch (e) {
    box.innerHTML = '';
  }
}

// --- Init -----------------------------------------------------------------
loadInvoices();

// Refresh when tab becomes visible again (e.g. switching apps on phone)
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) loadInvoices();
});

})();
