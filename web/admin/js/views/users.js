// Benutzer (#/users): Tabelle mit Suche/Filter, anlegen (Passwort einmalig anzeigen), bearbeiten in Schublade.
// Schutzregeln (SPEC §6.3) werden vorab erklärt und die betroffenen Aktionen deaktiviert; der Server prüft zusätzlich.
import { h, useStyles, initials, mount as fill } from '../dom.js';
import { api, ApiError, errorMessage } from '../api.js';
import { can, isAdmin, session } from '../session.js';
import { icon } from '../icons.js';
import { formatRelative, formatDateTime, plural } from '../format.js';
import { page, pageHeader, card, button } from '../ui/page.js';
import { field, input, select, segmented, checkbox, switchToggle, searchInput, setFieldErrors, clearFieldErrors } from '../ui/form.js';
import { openDialog, openDrawer, confirmDialog } from '../ui/dialog.js';
import { dataTable } from '../ui/table.js';
import { toast } from '../ui/toast.js';
import { chip } from '../ui/status.js';
import { emptyState, errorState, skeletonLines } from '../ui/empty.js';
import { copyField } from '../ui/stele-ui.js';

const STATUS_FILTER = [
  { value: '', label: 'Alle Status' },
  { value: 'active', label: 'Aktiv' },
  { value: 'inactive', label: 'Deaktiviert' },
  { value: 'locked', label: 'Gesperrt' },
];

export function userStatus(u) {
  if (!u.is_active) return chip('neutral', 'Deaktiviert', 'power', { size: 'sm' });
  if (u.locked) return chip('danger', 'Gesperrt', 'lock', { size: 'sm', title: u.locked_until ? `bis ${formatDateTime(u.locked_until)}` : null });
  return chip('success', 'Aktiv', 'check-circle', { size: 'sm' });
}

export default async function mount(root, ctx) {
  await useStyles('/admin/css/views/users.css');
  const manage = can('users.manage');
  const me = session.user?.id;
  const state = { users: [], roles: [], q: ctx.query.q || '', role: ctx.query.role || '', status: ctx.query.status || '' };

  const addBtn = manage ? button({ label: 'Benutzer anlegen', icon: 'plus', variant: 'primary', onClick: () => openCreate() }) : null;
  const search = searchInput({ value: state.q, placeholder: 'Name, Benutzername oder E-Mail …', label: 'Benutzer durchsuchen', onInput: (v) => { state.q = v; ctx.setQuery({ q: v || null }); render(); } });
  search.classList.add('toolbar__search');
  const roleFilter = h('div');
  const statusFilter = select({ value: state.status, options: STATUS_FILTER, 'aria-label': 'Nach Status filtern', onChange: (v) => { state.status = v; ctx.setQuery({ status: v || null }); render(); } });
  const count = h('p', { class: 'text-2 text-sm', 'aria-live': 'polite' });
  const listSlot = h('div', {}, card({ body: skeletonLines(6) }));

  root.append(page({},
    pageHeader({ title: 'Benutzer', description: 'Konten, die sich am CMS anmelden können, und ihre Rollen.', actions: [addBtn] }),
    h('div', { class: 'toolbar' }, search, roleFilter, h('div', { class: 'usr-filter' }, statusFilter)),
    manage ? null : h('div', { class: 'alert alert--neutral' }, icon('eye'), h('div', { class: 'alert__body' }, h('div', { class: 'alert__text' }, 'Nur Ansicht – Konten anlegen oder ändern erfordert das Recht „Benutzer anlegen, bearbeiten, sperren und löschen“.'))),
    count, listSlot));

  const activeAdmins = () => state.users.filter((u) => u.role?.is_admin && u.is_active).length;
  const isLastAdmin = (u) => u.role?.is_admin && u.is_active && activeAdmins() <= 1;
  const roleById = (id) => state.roles.find((r) => r.id === id);
  // §6.3: ohne Administrator-Rolle nur Rollen vergeben und Konten verwalten, deren Rechte das eigene Konto selbst hat
  const coversRole = (r) => isAdmin() || (!!r && !r.is_admin && (r.effective_permissions || []).every((p) => session.permissions.has(p)));
  const canManage = (u) => manage && coversRole(roleById(u.role?.id));

  const table = dataTable({
    caption: 'Benutzer',
    sort: { key: 'display_name', dir: 'asc' },
    onRowClick: (u) => openEdit(u),
    columns: [
      { key: 'display_name', label: 'Name', sortable: true, rowHeader: true, render: (u) => h('div', { class: 'usr-person' },
        h('span', { class: 'avatar', 'aria-hidden': 'true' }, initials(u.display_name || u.username)),
        h('div', { class: 'usr-person__text' },
          h('span', { class: 'cluster', style: { '--cluster-gap': '6px' } },
            h('button', { type: 'button', class: 'usr-name', onClick: () => openEdit(u) }, u.display_name),
            u.id === me ? h('span', { class: 'tag' }, 'Sie') : null,
            u.is_demo ? h('span', { class: 'tag', title: 'Demo-Konto für die Schnellanmeldung im Testbetrieb' }, 'Demo') : null),
          h('span', { class: 'table__secondary' }, `@${u.username}${u.email ? ` · ${u.email}` : ''}`))) },
      { key: 'role', label: 'Rolle', sortable: true, sortValue: (u) => u.role?.name, render: (u) => h('span', { class: 'cluster', style: { '--cluster-gap': '6px' } }, u.role?.is_admin ? icon('shield', { size: 16 }) : null, u.role?.name || '–') },
      { key: 'status', label: 'Status', sortable: true, sortValue: (u) => (!u.is_active ? 2 : u.locked ? 1 : 0), render: (u) => h('span', { class: 'cluster', style: { '--cluster-gap': '6px' } }, userStatus(u),
        u.must_change_password && u.is_active ? chip('info', 'Passwortwechsel offen', 'key', { size: 'sm' }) : null) },
      { key: 'last_login_at', label: 'Letzte Anmeldung', sortable: true, render: (u) => (u.last_login_at ? h('time', { datetime: u.last_login_at, title: formatDateTime(u.last_login_at) }, formatRelative(u.last_login_at)) : h('span', { class: 'text-2' }, 'noch nie')) },
      { key: 'actions', label: 'Aktionen', align: 'actions', headerHidden: true, render: (u) => { const m = canManage(u); return button({ label: m ? 'Bearbeiten' : 'Ansehen', size: 'sm', variant: 'ghost', icon: m ? 'pencil' : 'eye', ariaLabel: `${u.display_name} ${m ? 'bearbeiten' : 'ansehen'}`, onClick: () => openEdit(u) }); } },
    ],
    empty: () => emptyState({ icon: 'search', title: 'Kein Benutzer gefunden', text: 'Keine Konten passen zu Suche und Filtern.', actions: [button({ label: 'Filter zurücksetzen', onClick: resetFilters })] }),
  });

  function resetFilters() {
    state.q = ''; state.role = ''; state.status = '';
    search.input.value = ''; statusFilter.value = '';
    const rf = roleFilter.querySelector('select'); if (rf) rf.value = '';
    ctx.setQuery({ q: null, role: null, status: null });
    render();
  }

  function filtered() {
    const q = state.q.toLowerCase();
    return state.users.filter((u) => {
      if (q && ![u.display_name, u.username, u.email].some((v) => (v || '').toLowerCase().includes(q))) return false;
      if (state.role && String(u.role?.id) !== String(state.role)) return false;
      if (state.status === 'active' && !(u.is_active && !u.locked)) return false;
      if (state.status === 'inactive' && u.is_active) return false;
      if (state.status === 'locked' && !u.locked) return false;
      return true;
    });
  }

  function render() {
    const list = filtered();
    count.textContent = list.length === state.users.length ? plural(state.users.length, 'Benutzer', 'Benutzer') : `${list.length} von ${plural(state.users.length, 'Benutzer', 'Benutzern')}`;
    table.update(list);
    if (listSlot.firstChild?.dataset?.usr !== 'table') {
      const c = card({ flush: true, body: table.el });
      c.dataset.usr = 'table';
      fill(listSlot, c);
    }
  }

  async function load() {
    try {
      const [users, roles] = await Promise.all([api.get('/api/users', { signal: ctx.signal }), api.get('/api/roles', { signal: ctx.signal }).catch(() => ({ items: [] }))]);
      state.users = users.items || [];
      state.roles = roles.items || [];
      fill(roleFilter, h('div', { class: 'usr-filter' }, select({ value: state.role, 'aria-label': 'Nach Rolle filtern', options: [{ value: '', label: 'Alle Rollen' }, ...state.roles.map((r) => ({ value: r.id, label: r.name }))], onChange: (v) => { state.role = v; ctx.setQuery({ role: v || null }); render(); } })));
      render();
    } catch (err) {
      if (err.name === 'AbortError') return;
      fill(listSlot, card({ body: errorState({ error: err, onRetry: () => load() }) }));
    }
  }

  function roleSelect(value, { disabled = false } = {}) {
    const desc = h('div', { class: 'field__hint usr-roledesc' });
    const roles = disabled ? state.roles : state.roles.filter(coversRole);
    const sel = select({ value, disabled, options: roles.map((r) => ({ value: r.id, label: r.name })), onChange: () => sync() });
    function sync() {
      const r = roleById(Number(sel.value));
      fill(desc, r ? (r.is_admin ? 'Darf alles, auch Benutzer, Rollen und Systemeinstellungen verwalten.' : (r.description || `${plural((r.effective_permissions || r.permissions || []).length, 'Recht', 'Rechte')}.`)) : '');
    }
    sync();
    return { sel, desc };
  }

  // ---------- Einmalige Passwort-Anzeige ----------
  function showPassword(user, pw, { reset = false } = {}) {
    return openDialog({
      title: reset ? 'Neues Passwort' : 'Konto angelegt',
      size: 'md',
      dismissible: false,
      content: h('div', { class: 'stack' },
        h('p', {}, reset ? `Das Passwort von ${user.display_name} wurde zurückgesetzt.` : `Das Konto „${user.username}“ für ${user.display_name} ist angelegt.`),
        h('div', { class: 'alert alert--warning' }, icon('alert-triangle'), h('div', { class: 'alert__body' },
          h('div', { class: 'alert__title' }, 'Dieses Passwort wird nur jetzt angezeigt'),
          h('div', { class: 'alert__text' }, 'Bitte kopieren und sicher weitergeben (nicht per E-Mail im Klartext). Bei der ersten Anmeldung muss ein eigenes Passwort gewählt werden.'))),
        copyField({ label: 'Passwort', value: pw }),
        copyField({ label: 'Benutzername', value: user.username })),
      actions: [{ label: 'Passwort notiert – schließen', variant: 'primary', icon: 'check' }],
    }).result;
  }

  // ---------- Anlegen ----------
  function openCreate() {
    const username = input({ maxLength: 32, autocomplete: 'off', spellcheck: 'false', placeholder: 'z. B. m.muster' });
    const displayName = input({ maxLength: 80, autocomplete: 'off', placeholder: 'z. B. Maria Muster' });
    const email = input({ type: 'email', maxLength: 120, autocomplete: 'off' });
    const grantable = state.roles.filter(coversRole);
    const defaultRole = grantable.find((r) => !r.is_admin && /betrachter/i.test(r.name)) || grantable.find((r) => !r.is_admin) || grantable[0];
    const { sel: roleSel, desc } = roleSelect(defaultRole?.id ?? '');
    const pw = input({ type: 'password', autocomplete: 'new-password', maxLength: 128 });
    const pwField = field({ label: 'Startpasswort', control: pw, required: true, name: 'password', hint: 'Mindestlänge laut Sicherheitseinstellungen (Standard 8 Zeichen).' });
    pwField.hidden = true;
    const mode = segmented({ value: 'generate', ariaLabel: 'Passwort', options: [{ value: 'generate', label: 'Erzeugen lassen', icon: 'sparkles' }, { value: 'set', label: 'Selbst festlegen', icon: 'key' }], onChange: (v) => { pwField.hidden = v !== 'set'; if (v === 'set') pw.focus(); } });
    const mustChange = checkbox({ label: 'Bei der ersten Anmeldung Passwort ändern', hint: 'Empfohlen – nur die Person selbst kennt dann ihr Passwort.', checked: true });
    const form = h('div', { class: 'form' },
      h('div', { class: 'form-row' },
        field({ label: 'Anzeigename', control: displayName, required: true, name: 'display_name', hint: 'Vor- und Nachname, erscheint im Protokoll.' }),
        field({ label: 'Benutzername', control: username, required: true, name: 'username', hint: '3–32 Zeichen: Buchstaben, Ziffern, Punkt, Binde- und Unterstrich.' })),
      field({ label: 'E-Mail', control: email, optional: true, name: 'email' }),
      field({ label: 'Rolle', control: h('div', { class: 'stack stack--sm' }, roleSel, desc), required: true, name: 'role_id' }),
      h('div', { class: 'field' }, h('span', { class: 'field__label' }, 'Passwort'), mode), pwField, mustChange);
    // Benutzername aus dem Anzeigenamen vorschlagen
    let touched = false;
    username.addEventListener('input', () => { touched = true; });
    displayName.addEventListener('input', () => {
      if (touched) return;
      const parts = displayName.value.trim().toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/ß/g, 'ss').split(/\s+/).filter(Boolean);
      username.value = parts.length > 1 ? `${parts[0][0]}.${parts[parts.length - 1]}`.replace(/[^a-z0-9._-]/g, '').slice(0, 32) : (parts[0] || '').replace(/[^a-z0-9._-]/g, '').slice(0, 32);
    });

    openDialog({
      title: 'Benutzer anlegen', size: 'md', content: form,
      actions: [
        { label: 'Abbrechen', value: null },
        { label: 'Benutzer anlegen', variant: 'primary', icon: 'plus', onClick: async () => {
          const errors = {};
          if (!displayName.value.trim()) errors.display_name = 'Bitte einen Anzeigenamen eingeben.';
          if (!/^[a-zA-Z0-9._-]{3,32}$/.test(username.value.trim())) errors.username = 'Bitte 3–32 Zeichen verwenden: Buchstaben, Ziffern, Punkt, Binde- oder Unterstrich.';
          if (email.value.trim() && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.value.trim())) errors.email = 'Bitte eine gültige E-Mail-Adresse eingeben.';
          if (mode.getValue() === 'set' && !pw.value) errors.password = 'Bitte ein Startpasswort eingeben.';
          if (Object.keys(errors).length) { setFieldErrors(form, errors); return false; }
          clearFieldErrors(form);
          const body = { username: username.value.trim(), display_name: displayName.value.trim(), email: email.value.trim(), role_id: Number(roleSel.value), must_change_password: mustChange.input.checked };
          if (mode.getValue() === 'set') body.password = pw.value;
          let r;
          try { r = await api.post('/api/users', body); } catch (err) {
            if (err instanceof ApiError && Object.keys(err.fields || {}).length) { const rest = setFieldErrors(form, err.fields); if (Object.keys(rest).length) toast.error(Object.values(rest)[0]); return false; }
            if (err instanceof ApiError && err.status === 409) { setFieldErrors(form, { username: err.message }); return false; }
            throw err;
          }
          await load();
          if (r.generated_password) showPassword(r.user, r.generated_password);
          else toast.success(`Konto für ${r.user.display_name} angelegt.`);
          return true;
        } },
      ],
    });
  }

  // ---------- Bearbeiten (Schublade) ----------
  function openEdit(u) {
    const self = u.id === me;
    const lastAdmin = isLastAdmin(u);
    const ro = !canManage(u);
    const displayName = input({ value: u.display_name, maxLength: 80, disabled: ro });
    const email = input({ type: 'email', value: u.email || '', maxLength: 120, disabled: ro });
    const roleLocked = ro || self || lastAdmin;
    const { sel: roleSel, desc } = roleSelect(u.role?.id, { disabled: roleLocked });
    const active = switchToggle({ label: 'Konto aktiv', hint: 'Deaktivierte Konten können sich nicht anmelden; laufende Sitzungen enden.', checked: !!u.is_active, disabled: ro || self || lastAdmin });
    const why = (text) => h('p', { class: 'usr-why' }, icon('lock', { size: 16 }), text);
    const protection = manage && ro ? 'Dieses Konto hat Rechte, die Ihr eigenes Konto nicht hat. Ändern, entsperren, zurücksetzen oder löschen kann es nur, wer diese Rechte selbst hat oder Administrator ist.'
      : self ? 'Das eigene Konto kann weder deaktiviert noch gelöscht werden, und die eigene Rolle lässt sich nicht ändern. Das kann nur eine andere Person mit Administratorrechten.'
      : lastAdmin ? 'Dies ist das letzte aktive Konto mit der Rolle Administrator. Es muss immer mindestens eines geben – daher sind Rollenwechsel, Deaktivieren und Löschen gesperrt.' : null;

    const form = h('form', { class: 'form', novalidate: true },
      h('div', { class: 'usr-head' },
        h('span', { class: 'avatar avatar--lg', 'aria-hidden': 'true' }, initials(u.display_name)),
        h('div', { class: 'stack', style: { '--stack-gap': '4px' } },
          h('span', { class: 'fw-semibold' }, `@${u.username}`),
          h('span', { class: 'cluster', style: { '--cluster-gap': '6px' } }, userStatus(u), u.is_demo ? h('span', { class: 'tag' }, 'Demo-Konto') : null, self ? h('span', { class: 'tag' }, 'Ihr Konto') : null),
          h('span', { class: 'text-2 text-sm' }, u.last_login_at ? `Letzte Anmeldung ${formatRelative(u.last_login_at)}` : 'Noch nie angemeldet'))),
      protection ? h('div', { class: 'alert alert--neutral' }, icon('shield'), h('div', { class: 'alert__body' }, h('div', { class: 'alert__text' }, protection))) : null,
      h('div', { class: 'form-section' },
        h('h3', { class: 'form-section__title' }, 'Stammdaten'),
        field({ label: 'Anzeigename', control: displayName, required: true, name: 'display_name' }),
        field({ label: 'E-Mail', control: email, optional: true, name: 'email' })),
      h('div', { class: 'form-section' },
        h('h3', { class: 'form-section__title' }, 'Rolle und Zugang'),
        field({ label: 'Rolle', control: h('div', { class: 'stack stack--sm' }, roleSel, desc), name: 'role_id' }),
        active));

    const dangerZone = h('div', { class: 'form-section' });
    if (!ro) {
      const lockBlock = u.locked ? h('div', { class: 'alert alert--danger' }, icon('lock'), h('div', { class: 'alert__body' },
        h('div', { class: 'alert__title' }, 'Konto vorübergehend gesperrt'),
        h('div', { class: 'alert__text' }, `Nach zu vielen Fehlversuchen${u.locked_until ? ` bis ${formatDateTime(u.locked_until)}` : ''}.`),
        h('div', { class: 'alert__actions' }, button({ label: 'Sperre aufheben', icon: 'unlock', size: 'sm', onClick: () => unlock(u, d) })))) : null;
      h(dangerZone, {},
        h('h3', { class: 'form-section__title' }, 'Sicherheit'),
        lockBlock,
        h('div', { class: 'cluster' }, button({ label: 'Passwort zurücksetzen …', icon: 'key', onClick: () => resetPassword(u) })),
        h('p', { class: 'text-2 text-sm' }, 'Beim Zurücksetzen werden alle Sitzungen der Person beendet; sie muss bei der nächsten Anmeldung ein neues Passwort wählen.'),
        h('div', { class: 'usr-danger' },
          button({ label: 'Konto löschen …', icon: 'trash', variant: 'danger-ghost', disabled: self || lastAdmin, onClick: () => remove(u, d) }),
          self || lastAdmin ? why(self ? 'Das eigene Konto kann nicht gelöscht werden.' : 'Das letzte aktive Administrator-Konto kann nicht gelöscht werden.') : h('span', { class: 'text-2 text-sm' }, 'Alternative: Konto deaktivieren – das Protokoll bleibt vollständig lesbar.')));
      form.append(dangerZone);
    }

    let dirty = false;
    form.addEventListener('input', () => { dirty = true; });
    form.addEventListener('change', () => { dirty = true; });
    const d = openDrawer({
      title: u.display_name,
      description: ro ? 'Konto ansehen' : 'Konto bearbeiten',
      width: '520px',
      content: form,
      actions: !ro ? [
        { label: 'Abbrechen', value: null },
        { label: 'Änderungen speichern', variant: 'primary', icon: 'save', onClick: async () => {
          if (!dirty) return true;
          const errors = {};
          if (!displayName.value.trim()) errors.display_name = 'Bitte einen Anzeigenamen eingeben.';
          if (email.value.trim() && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.value.trim())) errors.email = 'Bitte eine gültige E-Mail-Adresse eingeben.';
          if (Object.keys(errors).length) { setFieldErrors(form, errors); return false; }
          const body = { display_name: displayName.value.trim(), email: email.value.trim() };
          if (!roleLocked && Number(roleSel.value) !== u.role?.id) body.role_id = Number(roleSel.value);
          if (!(self || lastAdmin) && active.input.checked !== !!u.is_active) body.is_active = active.input.checked;
          try { await api.patch(`/api/users/${u.id}`, body); } catch (err) {
            if (err instanceof ApiError && Object.keys(err.fields || {}).length) { setFieldErrors(form, err.fields); return false; }
            if (err instanceof ApiError && ['last_admin', 'self_protection', 'stale'].includes(err.code)) { toast.warning(err.message); return false; }
            throw err;
          }
          toast.success('Änderungen gespeichert.');
          await load();
          return true;
        } },
      ] : [{ label: 'Schließen', value: null }],
    });
    // Schließen mit ungespeicherten Änderungen: nachfragen
    const origClose = d.close;
    d.close = async (result = null) => {
      if (result === null && dirty && !ro) {
        const ok = await confirmDialog({ title: 'Änderungen verwerfen?', message: 'Die Änderungen an diesem Konto sind noch nicht gespeichert.', confirmLabel: 'Verwerfen', cancelLabel: 'Weiter bearbeiten', danger: true, icon: 'undo' });
        if (!ok) return;
      }
      origClose(result);
    };
  }

  async function unlock(u, d) {
    try {
      await api.post(`/api/users/${u.id}/unlock`);
      toast.success(`Sperre von ${u.display_name} aufgehoben.`);
      await load();
      d.close(true);
    } catch (err) { toast.error(errorMessage(err)); }
  }

  async function resetPassword(u) {
    const pw = input({ type: 'password', autocomplete: 'new-password', maxLength: 128 });
    const pwField = field({ label: 'Neues Passwort', control: pw, required: true, name: 'password' });
    pwField.hidden = true;
    const mode = segmented({ value: 'generate', ariaLabel: 'Neues Passwort', options: [{ value: 'generate', label: 'Erzeugen lassen', icon: 'sparkles' }, { value: 'set', label: 'Selbst festlegen', icon: 'key' }], onChange: (v) => { pwField.hidden = v !== 'set'; } });
    const form = h('div', { class: 'form' },
      h('p', { class: 'text-2' }, `${u.display_name} wird überall abgemeldet und muss bei der nächsten Anmeldung ein eigenes Passwort wählen.`),
      mode, pwField);
    const result = await openDialog({
      title: 'Passwort zurücksetzen', size: 'sm', content: form,
      actions: [
        { label: 'Abbrechen', value: null },
        { label: 'Passwort zurücksetzen', variant: 'primary', icon: 'key', onClick: async () => {
          if (mode.getValue() === 'set' && !pw.value) { setFieldErrors(form, { password: 'Bitte ein Passwort eingeben.' }); return false; }
          try {
            return await api.post(`/api/users/${u.id}/password`, mode.getValue() === 'set' ? { password: pw.value } : {});
          } catch (err) {
            if (err instanceof ApiError && Object.keys(err.fields || {}).length) { setFieldErrors(form, err.fields); return false; }
            throw err;
          }
        } },
      ],
    }).result;
    if (!result) return;
    await load();
    if (result.generated_password) showPassword(u, result.generated_password, { reset: true });
    else toast.success(`Passwort von ${u.display_name} zurückgesetzt.`);
  }

  async function remove(u, d) {
    const ok = await confirmDialog({
      title: `Konto „${u.username}“ löschen?`,
      message: `${u.display_name} kann sich danach nicht mehr anmelden. Einträge im Protokoll bleiben mit dem Namen erhalten. Das lässt sich nicht rückgängig machen.`,
      confirmLabel: 'Konto löschen', danger: true,
    });
    if (!ok) return;
    try {
      await api.del(`/api/users/${u.id}`);
      toast.success(`Konto von ${u.display_name} gelöscht.`);
      d.close(true);
      await load();
    } catch (err) { toast.error(errorMessage(err)); }
  }

  await load();
  return undefined;
}
