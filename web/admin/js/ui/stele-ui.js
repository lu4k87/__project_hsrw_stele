// Gemeinsame Bausteine rund um Stelen (Übersicht, Stelen-Liste, Stele-Detail, Monitoring).
//
//   steleCard(stele, { live: true })        → Karte mit Status, läuft jetzt, nächster Wechsel, Aktionen; .update(stele), .destroy()
//   nowPlaying(stele) / nextChange(stele)   → Textbausteine
//   sendCommand(stele, 'reload')            → Befehl senden mit Rückmeldung
//   commandBlocked(stele, cmd)              → Grund, warum der Befehl nicht geht (Stele nie gemeldet), sonst null
//   commandHint(stele)                      → sichtbarer Hinweis dazu oder null
//   steleMirror(steleId)                    → aufklappbare Live-Ansicht (Player im Modus mirror), .destroy()
//   copyField({ label, value })             → Nur-Lese-Feld mit „Kopieren“
//   defaultPresentationPicker({ steleId, current, presentations, onSaved }) → Auswahl + „Übernehmen“
//   kioskCommand(url), agentCommand(url)    → Aufrufe für den Stelen-PC
import { h, useStyles, copyText, uid, mount as fill } from '../dom.js';
import { icon } from '../icons.js';
import { api, errorMessage } from '../api.js';
import { can } from '../session.js';
import { formatRelative, formatSince, formatTime, formatDateTime, formatDuration } from '../format.js';
import { steleStatus, chip } from './status.js';
import { button } from './page.js';
import { select } from './form.js';
import { toast } from './toast.js';
import { playerFrame, playerUrls } from './player-frame.js';

useStyles('/admin/css/views/stele-ui.css');

export const COMMANDS = {
  reload: { label: 'Neu laden', icon: 'refresh', done: 'Befehl „Neu laden“ gesendet. Die Stele lädt beim nächsten Kontakt (≤ 15 s) neu.' },
  identify: { label: 'Identifizieren', icon: 'crosshair', done: 'Befehl „Identifizieren“ gesendet. Die Stele zeigt gleich 10 s lang ihren Namen.' },
  screenshot: { label: 'Screenshot anfordern', icon: 'camera', done: 'Screenshot angefordert. Er erscheint nach der nächsten Meldung des Stelen-PCs (bis zu 1 Min.).' },
  clear_cache: { label: 'Zwischenspeicher leeren', icon: 'trash', done: 'Befehl gesendet. Die Stele leert ihren Zwischenspeicher und lädt neu.' },
};

const SOURCE_LABEL = { schedule: 'laut Zeitplan', default: 'Standard-Präsentation', none: '' };
const MODE_LABEL = { slideshow: 'Diashow', touch: 'Touch-Bedienung durch Besucher', standby: 'Nachtmodus', idle: 'Läuft, aber nichts anzuzeigen', pairing: 'Wartet auf Kopplung' };

export const isLive = (s) => s && (s.status === 'online' || s.status === 'standby');

/** „Läuft jetzt: Präsentation · Folie“ (online) bzw. „Laut Zeitplan: …“ (offline). */
export function nowPlaying(stele, { compact = false } = {}) {
  const n = stele?.now || {};
  const live = isLive(stele);
  if (live && n.mode === 'standby') {
    return h('div', { class: 'stele-now' }, icon('moon', { size: 18 }), h('span', {}, h('span', { class: 'stele-now__label' }, 'Nachtmodus: '), 'Bildschirm ist aus'));
  }
  if (live && n.mode === 'idle') {
    return h('div', { class: 'stele-now stele-now--none' }, icon('alert-triangle', { size: 18 }),
      h('span', {}, h('span', { class: 'stele-now__label' }, `${MODE_LABEL.idle}: `), 'Standbild – Präsentation planen oder Standard-Präsentation setzen'));
  }
  if (!n.presentation) {
    return h('div', { class: 'stele-now stele-now--none' }, icon('alert-triangle', { size: 18 }),
      h('span', {}, h('span', { class: 'stele-now__label' }, live ? 'Läuft jetzt: ' : 'Geplant: '), 'keine Präsentation (Standbild)'));
  }
  const parts = [h('strong', {}, n.presentation.name || 'Präsentation')];
  if (live && n.item?.title) parts.push(' · ', h('span', {}, n.item.title));
  const hint = [SOURCE_LABEL[n.source], live && n.mode === 'touch' ? MODE_LABEL.touch : null].filter(Boolean).join(' · ');
  return h('div', { class: 'stele-now' }, icon(live ? 'play' : 'calendar', { size: 18 }),
    h('span', { class: 'stele-now__text' },
      h('span', { class: 'stele-now__label' }, live ? 'Läuft jetzt: ' : 'Laut Zeitplan: '), parts,
      !compact && hint ? h('span', { class: 'stele-now__hint' }, ` (${hint})`) : null));
}

/** „Ab 18:00: Abendprogramm“ oder null. */
export function nextChangeText(stele) {
  const nc = stele?.next_change;
  if (!nc || !nc.at) return null;
  const when = sameLocalDay(nc.at) ? `heute ${formatTime(nc.at)}` : formatDateTime(nc.at);
  return `${when} Uhr → ${nc.presentation?.name || 'keine Präsentation (Standbild)'}`;
}

function sameLocalDay(v) {
  const d = new Date(v);
  return d.toDateString() === new Date().toDateString();
}

/** „zuletzt gemeldet vor 2 Min.“ */
export function lastSeenText(stele) {
  if (!stele?.last_seen_at) return 'noch nie gemeldet';
  return `zuletzt gemeldet ${formatRelative(stele.last_seen_at)}`;
}

// Spiegel von COMMAND_TTL_S (server/stelecms/steles.py): nicht abgeholte Befehle verfallen
const COMMAND_TTL_MIN = 10;
const NEVER_REASON = 'Die Stele hat sich noch nie gemeldet. Befehle gehen erst, wenn sie gekoppelt ist und läuft.';

/** Grund, warum der Befehl nicht sinnvoll ist, oder null. Screenshot läuft über den Stelen-PC-Agent und bleibt möglich. */
export function commandBlocked(stele, command = 'reload') {
  return stele?.status === 'never' && command !== 'screenshot' ? NEVER_REASON : null;
}

/** Sichtbarer Grund für gesperrte Befehls-Knöpfe (nur mit steles.control) oder null. */
export function commandHint(stele) {
  if (!can('steles.control') || !commandBlocked(stele)) return null;
  return h('p', { class: 'command-hint' }, icon('info', { size: 16 }), h('span', {}, NEVER_REASON));
}

export async function sendCommand(stele, command) {
  const c = COMMANDS[command];
  const blocked = commandBlocked(stele, command);
  if (blocked) { toast.warning(blocked); return false; }
  try {
    await api.post(`/api/steles/${stele.id}/commands`, { command });
    if (!isLive(stele) && command !== 'screenshot') toast.warning(`${c.label}: Die Stele ist gerade offline. Der Befehl wird beim nächsten Kontakt ausgeführt – meldet sie sich nicht innerhalb von ${COMMAND_TTL_MIN} Min., verfällt er.`);
    else toast.success(c.done);
    return true;
  } catch (err) {
    toast.error(errorMessage(err));
    return false;
  }
}

/** Knöpfe „Neu laden“ / „Identifizieren“ (nur mit steles.control). */
export function commandButtons(stele, { size = 'sm', commands = ['reload', 'identify'], variant = 'secondary' } = {}) {
  if (!can('steles.control')) return [];
  return commands.map((cmd) => {
    const c = COMMANDS[cmd];
    const blocked = commandBlocked(stele, cmd);
    const b = button({ label: c.label, icon: c.icon, size, variant, disabled: !!blocked, title: blocked });
    b.addEventListener('click', async () => {
      b.setAttribute('aria-busy', 'true');
      await sendCommand(stele, cmd);
      b.removeAttribute('aria-busy');
    });
    return b;
  });
}

/** Aufklappbare kleine Live-Ansicht. Player wird erst beim Öffnen geladen und beim Schließen entfernt. */
export function steleMirror(steleId, { label = 'Live-Ansicht', maxHeight = '420px' } = {}) {
  let frame = null;
  let item = null;
  const regionId = uid('live');
  const region = h('div', { class: 'stele-live__region', id: regionId, hidden: true });
  const btn = h('button', { type: 'button', class: 'btn btn--ghost btn--sm stele-live__toggle', 'aria-expanded': 'false', 'aria-controls': regionId });
  const renderBtn = (open) => {
    fill(btn, icon(open ? 'eye-off' : 'eye', { size: 16 }), open ? `${label} ausblenden` : `${label} zeigen`);
    btn.setAttribute('aria-expanded', String(open));
  };
  renderBtn(false);
  btn.addEventListener('click', () => {
    const open = region.hidden;
    region.hidden = !open;
    renderBtn(open);
    if (open) {
      frame = playerFrame({ src: playerUrls.mirror(steleId), maxHeight, title: 'Live-Ansicht der Stele (nachgebildet)' });
      if (item) frame.send({ type: 'showItem', item_id: item });
      fill(region, frame.el, h('p', { class: 'stele-live__note' }, 'Nachgebildet aus dem veröffentlichten Stand – ohne Ton, folgt der aktuellen Folie der Stele.'));
    } else {
      frame?.destroy();
      frame = null;
      fill(region);
    }
  });
  const el = h('div', { class: 'stele-live' }, btn, region);
  return {
    el,
    /** Aktuelle Folie der Stele (aus dem Heartbeat) an die Live-Ansicht weitergeben. */
    showItem(itemId) {
      if (itemId && itemId !== item && frame) frame.send({ type: 'showItem', item_id: itemId });
      item = itemId || null;
    },
    destroy() { frame?.destroy(); frame = null; },
  };
}

/** Karte einer Stele (Übersicht, Stelen-Liste). update(stele) aktualisiert Texte, ohne die Live-Ansicht neu zu laden. */
export function steleCard(stele, { live = true, showMeta = false } = {}) {
  const titleLink = h('a', { class: 'stele-card__title', href: `#/steles/${stele.id}` });
  const statusSlot = h('span');
  const subtitle = h('p', { class: 'stele-card__sub' });
  const nowSlot = h('div');
  const facts = h('dl', { class: 'stele-card__facts' });
  const hintSlot = h('div');
  const actions = h('div', { class: 'stele-card__actions' });
  const preview = live && can('steles.view') ? steleMirror(stele.id) : null;
  const el = h('article', { class: 'card stele-card' },
    h('div', { class: 'stele-card__head' },
      h('div', { class: 'stele-card__icon', 'aria-hidden': 'true' }, icon('stele')),
      h('div', { class: 'stele-card__titles' }, h('h3', { class: 'stele-card__h' }, titleLink), subtitle),
      statusSlot),
    h('div', { class: 'stele-card__body' }, nowSlot, facts, hintSlot),
    preview ? preview.el : null,
    actions,
  );
  let current = stele;
  function fact(label, value) {
    return [h('dt', {}, label), h('dd', {}, value ?? '–')];
  }
  function update(s) {
    current = s;
    titleLink.textContent = s.name;
    fill(statusSlot, steleStatus(s));
    subtitle.textContent = [s.location, s.ip_address].filter(Boolean).join(' · ') || 'Kein Standort angegeben';
    fill(nowSlot, nowPlaying(s));
    const rows = [];
    const next = nextChangeText(s);
    if (next) rows.push(fact('Nächster Wechsel', next));
    if (showMeta) {
      rows.push(fact('Standard', s.default_presentation?.name || 'nicht festgelegt'));
      rows.push(fact('Player', s.player?.version ? `Version ${s.player.version}${s.player.manifest_current === false ? ' · veralteter Stand' : ''}` : '–'));
    }
    rows.push(fact('Zuletzt gemeldet', s.last_seen_at ? h('time', { datetime: s.last_seen_at, title: formatDateTime(s.last_seen_at) }, formatRelative(s.last_seen_at)) : 'noch nie'));
    fill(facts, ...rows.flat());
    preview?.showItem(s.status === 'online' ? s.now?.item?.id : null);
    const hint = commandHint(s);
    fill(hintSlot, hint);
    hintSlot.hidden = !hint;
    fill(actions, 
      ...commandButtons(s),
      h('a', { class: 'btn btn--ghost btn--sm stele-card__more', href: `#/steles/${s.id}` }, 'Details', icon('chevron-right', { size: 16 })),
    );
  }
  update(stele);
  return { el, update, destroy() { preview?.destroy(); }, get stele() { return current; } };
}

/** Nur-Lese-Feld mit Kopieren-Knopf. secret: Wert zunächst verdeckt (Anzeigen-Knopf). */
export function copyField({ label, value, multiline = false, secret = false, hint = null }) {
  const id = uid('copy');
  const ctl = multiline
    ? h('textarea', { class: 'textarea mono stele-copy__text', id, readonly: true, rows: 3, spellcheck: 'false' })
    : h('input', { class: 'input mono', id, type: secret ? 'password' : 'text', readonly: true, spellcheck: 'false' });
  ctl.value = value || '';
  const copyBtn = h('button', { type: 'button', class: 'btn btn--secondary' }, icon('copy', { size: 18 }), 'Kopieren');
  copyBtn.addEventListener('click', async () => {
    const ok = await copyText(value || '');
    if (ok) toast.success(`${label} kopiert.`); else toast.error('Kopieren nicht möglich – bitte Text markieren und mit Strg+C kopieren.');
  });
  let showBtn = null;
  if (secret) {
    showBtn = h('button', { type: 'button', class: 'btn btn--ghost', 'aria-pressed': 'false' }, icon('eye', { size: 18 }), 'Anzeigen');
    showBtn.addEventListener('click', () => {
      const show = ctl.type === 'password';
      ctl.type = show ? 'text' : 'password';
      showBtn.setAttribute('aria-pressed', String(show));
      fill(showBtn, icon(show ? 'eye-off' : 'eye', { size: 18 }), show ? 'Verbergen' : 'Anzeigen');
    });
  }
  return h('div', { class: 'field' },
    h('label', { class: 'field__label', for: id }, label),
    h('div', { class: ['stele-copy', multiline && 'stele-copy--multi'] }, ctl, h('div', { class: 'stele-copy__btns' }, showBtn, copyBtn)),
    hint ? h('div', { class: 'field__hint' }, hint) : null);
}

export function kioskCommand(playerUrl) {
  return `google-chrome --kiosk --noerrdialogs --disable-infobars --autoplay-policy=no-user-gesture-required --overscroll-history-navigation=0 --disable-pinch "${playerUrl}"`;
}

export function agentCommand(playerUrl) {
  try {
    const u = new URL(playerUrl, location.origin);
    const key = u.searchParams.get('key') || '<schlüssel>';
    return `python3 stele_agent.py --server ${u.origin} --key ${key} --interval 60 --allow-screenshots`;
  } catch {
    return 'python3 stele_agent.py --server <cms-adresse> --key <schlüssel> --allow-screenshots';
  }
}

/** Kurzbeschreibung des Player-Zustands. */
export function playerSummary(p) {
  if (!p) return 'Noch keine Meldung des Players';
  const parts = [p.version ? `Version ${p.version}` : null, p.screen ? `${p.screen.w} × ${p.screen.h}` : null, p.uptime_s ? `läuft seit ${formatDuration(p.uptime_s)}` : null];
  return parts.filter(Boolean).join(' · ');
}


const ALERT_LINKS = {
  job_failed: () => ({ href: '#/media', label: 'Zur Mediathek' }),
  disk_low: () => (can('monitoring.view') ? { href: '#/monitoring', label: 'Zum Monitoring' } : null),
};

/** Liste von Warnungen/Alarmen (Icon + Text + Zeit + Link). limit: nur die ersten n zeigen, Rest als Link. */
export function alertList(alerts = [], { limit = null } = {}) {
  const sorted = [...alerts].sort((a, b) => (a.level === b.level ? 0 : a.level === 'error' ? -1 : 1));
  const shown = limit ? sorted.slice(0, limit) : sorted;
  const items = shown.map((a) => {
    const err = a.level === 'error';
    let link = ALERT_LINKS[a.code]?.() || null;
    if (!link && a.stele_id && can('steles.view')) link = { href: `#/steles/${a.stele_id}`, label: 'Stele öffnen' };
    return h('li', { class: ['stele-alert', err ? 'stele-alert--error' : 'stele-alert--warning'] },
      icon(err ? 'alert-circle' : 'alert-triangle', { size: 20 }),
      h('div', { class: 'stele-alert__body' },
        h('span', { class: 'stele-alert__level' }, err ? 'Störung: ' : 'Hinweis: '),
        h('span', {}, a.message),
        a.since ? h('span', { class: 'stele-alert__time' }, ` · ${formatSince(a.since)}`) : null),
      link ? h('a', { class: 'btn btn--secondary btn--sm stele-alert__link', href: link.href }, link.label) : null);
  });
  const rest = limit && sorted.length > limit ? sorted.length - limit : 0;
  return h('div', { class: 'stack stack--sm' },
    h('ul', { class: 'stele-alerts', 'aria-label': 'Hinweise und Störungen' }, items),
    rest ? h('a', { class: 'text-sm', href: can('monitoring.view') ? '#/monitoring' : '#/steles' }, `${rest} weitere ${rest === 1 ? 'Meldung' : 'Meldungen'} anzeigen`) : null);
}

const PRES_STATUS_TEXT = { draft: 'Entwurf – nicht veröffentlicht', changed: 'veröffentlicht, Änderungen offen', published: 'veröffentlicht' };

/** Präsentationen für Auswahllisten laden. Liefert [] ohne Recht (403) statt Fehler. */
export async function fetchPresentations({ signal } = {}) {
  try {
    const r = await api.get('/api/presentations', { signal });
    return (r?.items || []).slice().sort((a, b) => a.name.localeCompare(b.name, 'de'));
  } catch (err) {
    if (err?.status === 403) return [];
    throw err;
  }
}

/** Optionen für select(): veröffentlichte zuerst, Entwürfe gruppiert und gekennzeichnet. */
export function presentationOptions(list = [], { none = 'Keine (Standbild)', current = null } = {}) {
  const pub = list.filter((p) => p.status !== 'draft');
  const draft = list.filter((p) => p.status === 'draft');
  const opts = [];
  if (none !== null) opts.push({ value: '', label: none });
  if (current && !list.some((p) => p.id === current.id)) opts.push({ value: current.id, label: current.name });
  if (pub.length) opts.push({ group: 'Veröffentlicht', options: pub.map((p) => ({ value: p.id, label: p.status === 'changed' ? `${p.name} (Änderungen offen)` : p.name })) });
  if (draft.length) opts.push({ group: 'Noch nicht veröffentlicht', options: draft.map((p) => ({ value: p.id, label: `${p.name} (Entwurf)` })) });
  return opts;
}

/**
 * Standard-Präsentation einer Stele wählen: Auswahl + „Übernehmen“ (nur bei Änderung sichtbar). Layout setzt die Ansicht.
 * onSaved(stele) nach dem Speichern (Antwort des Servers); onDirty(bool) bei jeder Auswahl.
 */
export function defaultPresentationPicker({ steleId, current, presentations, successMessage = 'Standard-Präsentation geändert.', onSaved, onDirty = null, ...attrs }) {
  const sel = select({ value: current?.id ?? '', options: presentationOptions(presentations, { current }), ...attrs, onChange: () => {
    const dirty = String(sel.value) !== String(current?.id ?? '');
    saveBtn.hidden = !dirty;
    onDirty?.(dirty);
  } });
  const saveBtn = button({ label: 'Übernehmen', icon: 'save', size: 'sm', variant: 'primary', onClick: async () => {
    saveBtn.setAttribute('aria-busy', 'true');
    try {
      const res = await api.patch(`/api/steles/${steleId}`, { default_presentation_id: sel.value ? Number(sel.value) : null });
      toast.success(successMessage);
      await onSaved(res);
    } catch (err) { toast.error(errorMessage(err)); } finally { saveBtn.removeAttribute('aria-busy'); }
  } });
  saveBtn.hidden = true;
  return { select: sel, saveBtn };
}

export function presentationStatusText(status) { return PRES_STATUS_TEXT[status] || ''; }

const EVENT_LEVEL = {
  info: { kind: 'info', label: 'Info', icon: 'info' },
  warning: { kind: 'warning', label: 'Warnung', icon: 'alert-triangle' },
  error: { kind: 'danger', label: 'Fehler', icon: 'alert-circle' },
};
/** Stufe eines Stelen-Ereignisses als Chip (Icon + Text). */
export function eventLevelChip(level) {
  const l = EVENT_LEVEL[level] || EVENT_LEVEL.info;
  return chip(l.kind, l.label, l.icon, { size: 'sm' });
}
