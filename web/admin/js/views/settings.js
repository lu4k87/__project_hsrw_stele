// Einstellungen (#/settings?tab=): Allgemein, Sicherheit, Medien, Betrieb, System. Ein Formular für alle Tabs,
// gespeichert werden nur geänderte Werte (PATCH /api/settings), Schutz vor ungespeicherten Änderungen.
import { h, useStyles, mount as fill } from '../dom.js';
import { api, ApiError, errorMessage } from '../api.js';
import { loadSession } from '../session.js';
import { icon } from '../icons.js';
import { formatDateTime, formatTime, formatDuration } from '../format.js';
import { page, pageHeader, card, button } from '../ui/page.js';
import { field, input, select, numberInput, switchToggle, setFieldErrors, clearFieldErrors } from '../ui/form.js';
import { tabs } from '../ui/tabs.js';
import { confirmDialog } from '../ui/dialog.js';
import { toast } from '../ui/toast.js';
import { chip } from '../ui/status.js';
import { errorState, loadingBlock, skeletonLines } from '../ui/empty.js';

const TABS = [
  { id: 'general', label: 'Allgemein', icon: 'building' },
  { id: 'security', label: 'Sicherheit', icon: 'lock' },
  { id: 'media', label: 'Medien', icon: 'images' },
  { id: 'operation', label: 'Betrieb', icon: 'activity' },
  { id: 'system', label: 'System', icon: 'server' },
];
// Feld → Tab (für Fehlermeldungen des Servers)
const FIELD_TAB = {
  org_name: 'general', timezone: 'general', default_slide_duration_s: 'general', default_design_id: 'general', stale_after_days: 'general',
  session_idle_minutes: 'security', lockout_attempts: 'security', lockout_minutes: 'security', password_min_length: 'security', dev_login_enabled: 'security',
  upload_max_mb: 'media', auto_transcode: 'media', offline_after_s: 'operation', retention_days: 'operation',
};

function timezones(current) {
  let list = [];
  try { list = Intl.supportedValuesOf('timeZone'); } catch { list = []; }
  const common = ['Europe/Berlin', 'Europe/Vienna', 'Europe/Zurich', 'Europe/Luxembourg', 'Europe/Amsterdam', 'Europe/Brussels', 'Europe/Paris', 'Europe/London', 'UTC'];
  const rest = list.filter((z) => !common.includes(z));
  const opts = [{ group: 'Häufig', options: common.map((z) => ({ value: z, label: z })) }];
  if (rest.length) opts.push({ group: 'Alle Zeitzonen', options: rest.map((z) => ({ value: z, label: z })) });
  if (current && !common.includes(current) && !rest.includes(current)) opts.unshift({ value: current, label: current });
  return opts;
}

export default async function mount(root, ctx) {
  await useStyles('/admin/css/views/settings.css');
  const initial = TABS.some((t) => t.id === ctx.query.tab) ? ctx.query.tab : 'general';
  const panels = {};
  const t = tabs({ ariaLabel: 'Bereiche der Einstellungen', value: initial, items: TABS, onChange: (id) => show(id) });
  const bodySlot = h('div', {}, card({ body: skeletonLines(6) }));
  root.append(page({ narrow: true },
    pageHeader({ title: 'Einstellungen', description: 'Systemweite Vorgaben für Organisation, Sicherheit, Medien und Betrieb.' }),
    bodySlot));

  let saved;
  let designs = [];
  try {
    [saved, designs] = await Promise.all([
      api.get('/api/settings', { signal: ctx.signal }),
      api.get('/api/designs', { signal: ctx.signal }).then((r) => r.items || []).catch(() => []),
    ]);
  } catch (err) {
    if (err.name === 'AbortError') return undefined;
    fill(bodySlot, card({ body: errorState({ error: err, onRetry: () => ctx.navigate('/settings', { replace: true, query: ctx.query }) }) }));
    return undefined;
  }

  // ---------- Felder ----------
  const c = {};
  c.org_name = input({ value: saved.org_name, maxLength: 80 });
  c.timezone = select({ value: saved.timezone, options: timezones(saved.timezone) });
  c.default_slide_duration_s = numberInput({ value: saved.default_slide_duration_s, min: 2, max: 600, unit: 's' });
  c.default_design_id = select({ value: saved.default_design_id ?? '', options: [{ value: '', label: 'Automatisch (erstes Design)' }, ...designs.map((d) => ({ value: d.id, label: d.name }))] });
  c.session_idle_minutes = numberInput({ value: saved.session_idle_minutes, min: 5, max: 1440, unit: 'Min.' });
  c.lockout_attempts = numberInput({ value: saved.lockout_attempts, min: 3, max: 20, unit: 'Versuche' });
  c.lockout_minutes = numberInput({ value: saved.lockout_minutes, min: 1, max: 1440, unit: 'Min.' });
  c.password_min_length = numberInput({ value: saved.password_min_length, min: 6, max: 64, unit: 'Zeichen' });
  c.dev_login_enabled = switchToggle({ label: 'Schnellanmeldung mit Demo-Konten (Testbetrieb)', hint: 'Anmeldung per Klick auf ein Demo-Konto – nur von diesem Rechner aus (localhost).', checked: !!saved.dev_login_enabled });
  c.upload_max_mb = numberInput({ value: saved.upload_max_mb, min: 1, max: 128, unit: 'MB' });
  c.auto_transcode = switchToggle({ label: 'Videos automatisch umwandeln', hint: 'Wandelt Videos beim Hochladen in ein Format um, das die Stele zuverlässig abspielt (H.264/MP4). Dauert je nach Länge einige Minuten.', checked: !!saved.auto_transcode });
  c.offline_after_s = numberInput({ value: saved.offline_after_s, min: 20, max: 3600, unit: 's' });
  c.retention_days = numberInput({ value: saved.retention_days, min: 1, max: 3650, unit: 'Tage' });
  c.stale_after_days = numberInput({ value: saved.stale_after_days, min: 1, max: 365, unit: 'Tage' });

  const read = {
    org_name: () => c.org_name.value.trim(),
    timezone: () => c.timezone.value,
    default_slide_duration_s: () => num(c.default_slide_duration_s),
    default_design_id: () => (c.default_design_id.value ? Number(c.default_design_id.value) : null),
    session_idle_minutes: () => num(c.session_idle_minutes),
    lockout_attempts: () => num(c.lockout_attempts),
    lockout_minutes: () => num(c.lockout_minutes),
    password_min_length: () => num(c.password_min_length),
    dev_login_enabled: () => c.dev_login_enabled.input.checked,
    upload_max_mb: () => num(c.upload_max_mb),
    auto_transcode: () => c.auto_transcode.input.checked,
    offline_after_s: () => num(c.offline_after_s),
    retention_days: () => num(c.retention_days),
    stale_after_days: () => num(c.stale_after_days),
  };
  function num(ctl) { const v = ctl.input.value; return v === '' ? null : Number(v); }
  const RANGES = { default_slide_duration_s: [2, 600], session_idle_minutes: [5, 1440], lockout_attempts: [3, 20], lockout_minutes: [1, 1440], password_min_length: [6, 64], upload_max_mb: [1, 128], offline_after_s: [20, 3600], retention_days: [1, 3650], stale_after_days: [1, 365] };

  function changes() {
    const out = {};
    for (const [k, fn] of Object.entries(read)) {
      const v = fn();
      if (JSON.stringify(v) !== JSON.stringify(saved[k] ?? (k === 'default_design_id' ? null : undefined))) out[k] = v;
    }
    return out;
  }

  // Warnung Testbetrieb
  const devWarn = h('div', { class: 'alert alert--warning' }, icon('alert-triangle'), h('div', { class: 'alert__body' },
    h('div', { class: 'alert__title' }, 'Nur für den Testbetrieb'),
    h('div', { class: 'alert__text' }, 'Solange die Schnellanmeldung aktiv ist, kann sich jede Person an diesem Rechner ohne Passwort als Demo-Konto anmelden – auch als Administrator. Vor dem Netzwerkbetrieb unbedingt ausschalten.')));
  const syncDev = () => { devWarn.hidden = !c.dev_login_enabled.input.checked; };
  c.dev_login_enabled.input.addEventListener('change', syncDev);
  syncDev();

  const f = (label, name, control, opts = {}) => field({ label, control, name, ...opts });
  panels.general = card({ title: 'Organisation und Vorgaben', icon: 'building', body: h('div', { class: 'form' },
    f('Name der Organisation', 'org_name', c.org_name, { required: true, hint: 'Erscheint im CMS und auf dem Standbild der Stele.' }),
    f('Zeitzone', 'timezone', c.timezone, { hint: 'Gilt für Zeitplan, Nachtmodus, Gültigkeiten und die Uhr auf der Stele – unabhängig von der Uhr des Stelen-PCs.' }),
    h('div', { class: 'form-row' },
      f('Standard-Foliendauer', 'default_slide_duration_s', c.default_slide_duration_s, { hint: 'Für neue Präsentationen; je Folie änderbar.' }),
      f('Standard-Design', 'default_design_id', c.default_design_id, { hint: 'Wird neuen Präsentationen zugewiesen.' })),
    f('Hinweis „Länger nicht aktualisiert“ nach', 'stale_after_days', c.stale_after_days, { hint: 'Übersicht meldet Präsentationen auf Stelen, die so lange nicht neu veröffentlicht wurden.' })) });

  panels.security = h('div', { class: 'stack' },
    card({ title: 'Anmeldung und Sitzungen', icon: 'lock', body: h('div', { class: 'form' },
      f('Automatisch abmelden nach', 'session_idle_minutes', c.session_idle_minutes, { hint: 'Ohne Aktivität endet die Sitzung; vorher erscheint ein Hinweis.' }),
      h('div', { class: 'form-row' },
        f('Konto sperren nach', 'lockout_attempts', c.lockout_attempts, { hint: 'Fehlversuche in Folge' }),
        f('Sperrdauer', 'lockout_minutes', c.lockout_minutes)),
      f('Mindestlänge für Passwörter', 'password_min_length', c.password_min_length, { hint: 'Gilt für neue und geänderte Passwörter.' })) }),
    card({ title: 'Testbetrieb', icon: 'info', body: h('div', { class: 'form' }, c.dev_login_enabled, devWarn) }));

  panels.media = card({ title: 'Hochladen und Verarbeitung', icon: 'upload', body: h('div', { class: 'form' },
    f('Maximale Dateigröße je Upload', 'upload_max_mb', c.upload_max_mb, { hint: 'Größere Dateien werden abgelehnt. Höchstens 128 MB.' }),
    c.auto_transcode) });

  panels.operation = card({ title: 'Überwachung und Aufbewahrung', icon: 'activity', body: h('div', { class: 'form' },
    f('Stele gilt als offline nach', 'offline_after_s', c.offline_after_s, { hint: 'Ohne Meldung der Stele in dieser Zeit. Die Stele meldet sich alle 15 Sekunden – mindestens 45 s sind sinnvoll.' }),
    f('Aufbewahrung von Protokoll und Statistik', 'retention_days', c.retention_days, { hint: 'Ältere Wiedergabe-, Touch-, Ereignis- und Messwerte werden automatisch gelöscht.' })) });

  // System
  const sysSlot = h('div', {}, loadingBlock('Systeminformationen werden geladen …'));
  panels.system = h('div', { class: 'stack' },
    card({ title: 'System', icon: 'server', body: sysSlot }),
    card({ title: 'Sicherung', icon: 'database', body: h('div', { class: 'stack' },
      h('p', {}, 'Lädt eine Kopie der Datenbank herunter (Inhalte-Verzeichnis, Präsentationen, Zeitplan, Benutzer, Protokoll). Mediendateien liegen separat im Datenordner und sollten zusätzlich gesichert werden.'),
      h('div', { class: 'cluster' }, button({ label: 'Datenbank-Sicherung herunterladen', icon: 'download', onClick: () => { api.download('/api/system/backup'); toast.info('Die Sicherung wird erstellt und heruntergeladen.'); } }))) }),
    h('div', { class: 'alert' }, icon('globe'), h('div', { class: 'alert__body' },
      h('div', { class: 'alert__title' }, 'Netzwerkbetrieb folgt'),
      h('div', { class: 'alert__text' }, 'Das CMS ist derzeit nur auf diesem Rechner erreichbar. Netzwerkzugriff für die Stelen, HTTPS und die endgültige Anmeldung werden in einem späteren Schritt eingerichtet.'))));

  async function loadSystem() {
    try {
      const s = await api.get('/api/system/info', { signal: ctx.signal });
      const yes = (ok, label) => (ok ? chip('success', `${label} vorhanden`, 'check-circle', { size: 'sm' }) : chip('warning', `${label} fehlt`, 'alert-triangle', { size: 'sm' }));
      fill(sysSlot, h('dl', { class: 'meta-list' },
        h('dt', {}, 'Version'), h('dd', {}, s.version || '–'),
        h('dt', {}, 'Adresse'), h('dd', { class: 'mono' }, `${s.host}:${s.port}`),
        h('dt', {}, 'Gestartet'), h('dd', {}, s.started_at ? `${formatDateTime(s.started_at)} (seit ${formatDuration((Date.now() - new Date(s.started_at).getTime()) / 1000)})` : '–'),
        h('dt', {}, 'Datenordner'), h('dd', { class: 'mono' }, s.data_dir || '–'),
        h('dt', {}, 'Datenbank'), h('dd', { class: 'mono' }, s.db_path || '–'),
        h('dt', {}, 'Medien'), h('dd', { class: 'mono' }, s.media_dir || '–'),
        h('dt', {}, 'Python'), h('dd', {}, s.python || '–'),
        h('dt', {}, 'Werkzeuge'), h('dd', { class: 'cluster set-tools' }, yes(s.ffmpeg, 'ffmpeg (Videos)'), yes(s.pdftoppm, 'pdftoppm (PDFs)'))));
    } catch (err) {
      if (err.name !== 'AbortError') fill(sysSlot, errorState({ error: err, onRetry: () => loadSystem() }));
    }
  }

  // ---------- Speichern ----------
  const saveState = h('span', { class: 'set-save__state', role: 'status' });
  const saveBtn = button({ label: 'Einstellungen speichern', icon: 'save', variant: 'primary', type: 'submit' });
  const resetBtn = button({ label: 'Verwerfen', icon: 'undo', variant: 'ghost', onClick: () => discard() });
  const saveBar = h('div', { class: 'set-save' }, saveState, resetBtn, saveBtn);
  const form = h('form', { class: 'stack', novalidate: true });
  for (const el of Object.values(panels)) t.panel.append(el);
  form.append(t.el, t.panel, saveBar);
  fill(bodySlot, form);

  function show(id) {
    for (const [k, el] of Object.entries(panels)) el.hidden = k !== id;
    saveBar.hidden = id === 'system';
    ctx.setQuery({ tab: id === 'general' ? null : id });
    if (id === 'system' && !sysSlot.dataset.loaded) { sysSlot.dataset.loaded = '1'; loadSystem(); }
  }

  function sync() {
    const n = Object.keys(changes()).length;
    ctx.setDirty(n ? 'Die Einstellungen sind noch nicht gespeichert.' : false);
    resetBtn.hidden = !n;
    fill(saveState, n ? h('span', { class: 'set-save__dirty' }, icon('alert-triangle', { size: 16 }), n === 1 ? '1 ungespeicherte Änderung' : `${n} ungespeicherte Änderungen`) : (saveState.dataset.saved ? `Gespeichert um ${saveState.dataset.saved}` : 'Alle Änderungen gespeichert'));
  }
  form.addEventListener('input', sync);
  form.addEventListener('change', sync);

  function setControls(values) {
    c.org_name.value = values.org_name;
    c.timezone.value = values.timezone;
    c.default_slide_duration_s.input.value = values.default_slide_duration_s;
    c.default_design_id.value = values.default_design_id ?? '';
    c.session_idle_minutes.input.value = values.session_idle_minutes;
    c.lockout_attempts.input.value = values.lockout_attempts;
    c.lockout_minutes.input.value = values.lockout_minutes;
    c.password_min_length.input.value = values.password_min_length;
    c.dev_login_enabled.input.checked = !!values.dev_login_enabled;
    c.upload_max_mb.input.value = values.upload_max_mb;
    c.auto_transcode.input.checked = !!values.auto_transcode;
    c.offline_after_s.input.value = values.offline_after_s;
    c.retention_days.input.value = values.retention_days;
    c.stale_after_days.input.value = values.stale_after_days;
    syncDev();
  }
  function discard() { setControls(saved); clearFieldErrors(form); sync(); }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const patch = changes();
    if (!Object.keys(patch).length) { toast.info('Keine Änderungen zu speichern.'); return; }
    const errors = {};
    if ('org_name' in patch && !patch.org_name) errors.org_name = 'Bitte einen Namen eingeben.';
    for (const [k, [lo, hi]] of Object.entries(RANGES)) {
      if (!(k in patch)) continue;
      const v = patch[k];
      if (v === null || !Number.isFinite(v) || !Number.isInteger(v) || v < lo || v > hi) errors[k] = `Bitte eine ganze Zahl zwischen ${lo} und ${hi} eingeben.`;
    }
    if (Object.keys(errors).length) { focusErrors(errors); return; }
    if (patch.dev_login_enabled === true) {
      const ok = await confirmDialog({ title: 'Schnellanmeldung einschalten?', message: 'Jede Person an diesem Rechner kann sich dann ohne Passwort als Demo-Konto anmelden. Nur für den Testbetrieb.', confirmLabel: 'Einschalten', icon: 'alert-triangle' });
      if (!ok) return;
    }
    saveBtn.setAttribute('aria-busy', 'true');
    try {
      saved = await api.patch('/api/settings', patch);
      setControls(saved);
      clearFieldErrors(form);
      saveState.dataset.saved = formatTime(new Date());
      sync();
      toast.success('Einstellungen gespeichert.');
      if ('org_name' in patch || 'dev_login_enabled' in patch || 'session_idle_minutes' in patch) { try { await loadSession(); } catch { /* Anzeige aktualisiert sich beim nächsten Laden */ } }
    } catch (err) {
      if (err instanceof ApiError && Object.keys(err.fields || {}).length) focusErrors(err.fields);
      else toast.error(errorMessage(err));
    } finally { saveBtn.removeAttribute('aria-busy'); }
  });

  function focusErrors(errors) {
    const first = Object.keys(errors).find((k) => FIELD_TAB[k]);
    if (first) { t.set(FIELD_TAB[first]); show(FIELD_TAB[first]); }
    const rest = setFieldErrors(form, errors);
    if (Object.keys(rest).length) toast.error(Object.values(rest)[0]);
    else toast.error('Bitte die markierten Eingaben prüfen.');
  }

  show(initial);
  sync();
  return undefined;
}
