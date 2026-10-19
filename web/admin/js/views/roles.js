// Rollen & Rechte (#/roles?role=&tab=compare): Rollenliste + Rechte-Matrix der gewählten Rolle (gruppiert, enthaltene
// Rechte automatisch markiert), Vergleich aller Rollen. Explizit speichern mit Schutz vor ungespeicherten Änderungen.
import { h, useStyles, mount as fill } from '../dom.js';
import { api, ApiError, errorMessage } from '../api.js';
import { can, isAdmin, session, applySession, loadSession } from '../session.js';
import { icon } from '../icons.js';
import { plural } from '../format.js';
import { page, pageHeader, card, button } from '../ui/page.js';
import { field, input, textarea, select, setFieldErrors } from '../ui/form.js';
import { openDialog, confirmDialog } from '../ui/dialog.js';
import { menuButton } from '../ui/menu.js';
import { tabs } from '../ui/tabs.js';
import { toast } from '../ui/toast.js';
import { emptyState, errorState, skeletonLines } from '../ui/empty.js';
import { confirmLeave } from '../router.js';

export default async function mount(root, ctx) {
  await useStyles('/admin/css/views/roles.css');
  const manage = can('roles.manage');
  const state = { roles: [], groups: [], catalog: new Map(), selected: null, draft: null, users: null };

  const addBtn = manage ? button({ label: 'Rolle anlegen', icon: 'plus', variant: 'primary', onClick: () => createRole() }) : null;
  const t = tabs({
    ariaLabel: 'Ansicht', value: ctx.query.tab === 'compare' ? 'compare' : 'roles',
    items: [{ id: 'roles', label: 'Rollen bearbeiten', icon: 'shield' }, { id: 'compare', label: 'Vergleich aller Rollen', icon: 'grid' }],
    onChange: (id) => { ctx.setQuery({ tab: id === 'compare' ? 'compare' : null }); renderTab(); },
  });
  const content = h('div', {}, card({ body: skeletonLines(6) }));
  t.panel.append(content);
  root.append(page({ wide: true },
    pageHeader({ title: 'Rollen & Rechte', description: 'Rollen bündeln Rechte. Jedes Konto hat genau eine Rolle.', actions: [addBtn] }),
    manage ? null : h('div', { class: 'alert alert--neutral' }, icon('eye'), h('div', { class: 'alert__body' }, h('div', { class: 'alert__text' }, 'Nur Ansicht – Rollen und Rechte ändern erfordert das Recht „Rollen und Rechte verwalten“.'))),
    h('div', {}, t.el, t.panel)));

  // ---------- Rechte-Logik ----------
  function closure(keys) {
    const out = new Set();
    const stack = [...keys];
    while (stack.length) {
      const k = stack.pop();
      if (out.has(k)) continue;
      out.add(k);
      for (const i of state.catalog.get(k)?.implies || []) stack.push(i);
    }
    return out;
  }
  /** Rechte, die p automatisch mitbringen (unter den gesetzten). */
  function impliedBy(p, set) {
    return [...set].filter((q) => q !== p && closure([q]).has(p));
  }
  const label = (k) => state.catalog.get(k)?.label || k;
  const sameSet = (a, b) => a.size === b.size && [...a].every((x) => b.has(x));

  // ---------- Laden ----------
  async function load(selectId = null) {
    try {
      const [perms, roles] = await Promise.all([
        state.groups.length ? Promise.resolve({ groups: state.groups }) : api.get('/api/permissions', { signal: ctx.signal }),
        api.get('/api/roles', { signal: ctx.signal }),
      ]);
      state.groups = perms.groups || [];
      state.catalog = new Map(state.groups.flatMap((g) => g.permissions.map((p) => [p.key, { ...p, group: g.label }])));
      state.roles = roles.items || [];
      const want = selectId ?? state.selected?.id ?? Number(ctx.query.role);
      selectRole(state.roles.find((r) => r.id === want) || state.roles.find((r) => !r.is_admin) || state.roles[0], { force: true });
    } catch (err) {
      if (err.name === 'AbortError') return;
      fill(content, card({ body: errorState({ error: err, onRetry: () => load() }) }));
    }
  }

  function isDirty() { return !!(state.selected && state.draft && !sameSet(state.draft, new Set(state.selected.permissions || []))); }
  function syncDirty() { ctx.setDirty(isDirty() ? `Die Rechte der Rolle „${state.selected.name}“ sind noch nicht gespeichert.` : false); }

  async function selectRole(role, { force = false } = {}) {
    if (!force && isDirty()) {
      if (!(await confirmLeave())) return;
    }
    state.selected = role || null;
    state.draft = role ? new Set(role.permissions || []) : null;
    syncDirty();
    ctx.setQuery({ role: role ? role.id : null });
    renderTab();
  }

  function renderTab() {
    if (!state.roles.length && !state.groups.length) return;
    fill(content, t.value === 'compare' ? renderCompare() : renderRoles());
  }

  // ---------- Rollen bearbeiten ----------
  function renderRoles() {
    const list = h('ul', { class: 'rol-list', 'aria-label': 'Rollen' }, state.roles.map((r) => {
      const on = state.selected?.id === r.id;
      const b = h('button', { type: 'button', class: ['rol-item', on && 'is-selected'], 'aria-current': on ? 'true' : null },
        h('span', { class: 'rol-item__top' },
          h('span', { class: 'rol-item__name' }, r.name),
          r.is_admin ? h('span', { class: 'rol-lock', title: 'Geschützte Rolle' }, icon('lock', { size: 16 }), h('span', { class: 'visually-hidden' }, ' (geschützt)')) : null),
        h('span', { class: 'rol-item__desc' }, r.description || (r.is_admin ? 'Alle Rechte' : '–')),
        h('span', { class: 'rol-item__meta' }, icon('users', { size: 14 }), plural(r.user_count || 0, 'Benutzer', 'Benutzer'), ' · ',
          r.is_admin ? 'alle Rechte' : plural((r.effective_permissions || []).length, 'Recht', 'Rechte')));
      b.addEventListener('click', () => selectRole(r));
      return h('li', {}, b);
    }));
    return h('div', { class: 'rol-layout' },
      h('nav', { class: 'rol-side', 'aria-label': 'Rollenauswahl' }, list),
      h('div', { class: 'rol-main' }, state.selected ? renderMatrix(state.selected) : card({ body: emptyState({ icon: 'shield', title: 'Keine Rolle gewählt' }) })));
  }

  function renderMatrix(role) {
    const locked = role.is_admin;
    // §6.3: ohne Administrator-Rolle nur Rollen verwalten, deren Rechte das eigene Konto selbst hat
    const covered = isAdmin() || (role.effective_permissions || []).every((p) => session.permissions.has(p));
    const ro = !manage || locked || !covered;
    const draft = locked ? new Set(state.catalog.keys()) : state.draft;
    const eff = closure(draft);
    const saveBar = h('div', { class: 'rol-save', hidden: !isDirty() },
      h('span', { class: 'rol-save__text' }, icon('alert-triangle', { size: 18 }), 'Ungespeicherte Änderungen'),
      button({ label: 'Verwerfen', variant: 'ghost', icon: 'undo', onClick: () => { state.draft = new Set(role.permissions || []); syncDirty(); renderTab(); } }),
      button({ label: 'Rechte speichern', variant: 'primary', icon: 'save', onClick: (e) => save(role, e.currentTarget) }));

    const groups = state.groups.map((g) => h('fieldset', { class: 'rol-group' },
      h('legend', { class: 'rol-group__title' }, g.label),
      h('ul', { class: 'rol-perms' }, g.permissions.map((p) => {
        const set = draft.has(p.key);
        const via = locked ? [] : impliedBy(p.key, draft);
        const included = !set && eff.has(p.key);
        const checked = set || included || locked;
        const inp = h('input', { type: 'checkbox', role: 'switch', checked, disabled: ro || (included && !set) });
        inp.addEventListener('change', () => {
          if (inp.checked) state.draft.add(p.key); else state.draft.delete(p.key);
          syncDirty();
          renderTab();
          content.querySelector(`[data-perm="${CSS.escape(p.key)}"] input`)?.focus();
        });
        const note = locked ? 'immer enthalten (Administrator)'
          : via.length ? `enthalten in ${via.map((q) => `„${label(q)}“`).join(', ')}` : null;
        const implies = (p.implies || []).length ? `bringt mit: ${p.implies.map((q) => label(q)).join(', ')}` : null;
        return h('li', { class: ['rol-perm', checked && 'is-on', included && 'is-included'], dataset: { perm: p.key } },
          h('label', { class: 'switch rol-perm__switch' }, inp,
            h('span', { class: 'switch__text' },
              h('span', { class: 'switch__label' }, p.label),
              h('span', { class: 'switch__hint' }, p.description),
              note ? h('span', { class: 'rol-perm__note' }, icon(included ? 'link' : 'lock', { size: 14 }), note) : null,
              implies && !locked ? h('span', { class: 'rol-perm__implies' }, implies) : null)));
      }))));

    const actions = !ro ? menuButton({ label: `Aktionen für Rolle „${role.name}“`, items: [
      { label: 'Umbenennen / Beschreibung …', icon: 'pencil', onClick: () => editRole(role) },
      { label: 'Als Vorlage kopieren …', icon: 'copy', onClick: () => createRole(role) },
      { separator: true },
      { label: 'Rolle löschen …', icon: 'trash', danger: true, onClick: () => deleteRole(role) },
    ] }) : null;

    return h('div', { class: 'stack' },
      card({
        title: role.name, icon: locked ? 'lock' : 'shield',
        subtitle: role.description || null,
        actions: [can('users.view') ? h('a', { class: 'btn btn--ghost btn--sm', href: `#/users?role=${role.id}` }, icon('users', { size: 16 }), plural(role.user_count || 0, 'Benutzer', 'Benutzer')) : null, actions],
        body: h('div', { class: 'stack' },
          locked ? h('div', { class: 'alert alert--neutral' }, icon('lock'), h('div', { class: 'alert__body' },
            h('div', { class: 'alert__title' }, 'Geschützte Rolle'),
            h('div', { class: 'alert__text' }, 'Der Administrator hat immer alle Rechte. Name und Rechte lassen sich nicht ändern, die Rolle kann nicht gelöscht werden – so bleibt das System immer verwaltbar.'))) :
            manage && !covered ? h('div', { class: 'alert alert--neutral' }, icon('eye'), h('div', { class: 'alert__body' }, h('div', { class: 'alert__text' }, 'Nur Ansicht – diese Rolle hat Rechte, die Ihr eigenes Konto nicht hat. Ändern oder löschen kann sie nur, wer diese Rechte selbst hat oder Administrator ist.'))) :
            h('p', { class: 'text-2 text-sm' }, manage
              ? 'Rechte ein- oder ausschalten und anschließend speichern. Manche Rechte bringen andere automatisch mit – diese sind dann als „enthalten“ markiert.'
              : 'Übersicht der Rechte dieser Rolle.'),
          h('div', { class: 'rol-groups' }, groups)),
      }),
      saveBar);
  }

  async function save(role, btn) {
    btn?.setAttribute('aria-busy', 'true');
    try {
      const updated = await api.patch(`/api/roles/${role.id}`, { permissions: [...state.draft] });
      toast.success(`Rechte der Rolle „${updated.name}“ gespeichert. Sie gelten ab der nächsten Aktion der betroffenen Personen.`);
      ctx.setDirty(false);
      if (session.user?.role?.id === role.id) { try { applySession(await loadSession()); } catch { /* egal */ } }
      await load(role.id);
    } catch (err) {
      toast.error(errorMessage(err));
    } finally { btn?.removeAttribute('aria-busy'); }
  }

  // ---------- Vergleich ----------
  function renderCompare() {
    const effs = new Map(state.roles.map((r) => [r.id, r.is_admin ? new Set(state.catalog.keys()) : new Set(r.effective_permissions || closure(r.permissions || []))]));
    const sets = new Map(state.roles.map((r) => [r.id, new Set(r.permissions || [])]));
    const cell = (r, key) => {
      const on = effs.get(r.id).has(key);
      const direct = r.is_admin || sets.get(r.id).has(key);
      if (!on) return h('td', { class: 'rol-cmp__cell' }, h('span', { class: 'rol-cmp__no', 'aria-hidden': 'true' }, '–'), h('span', { class: 'visually-hidden' }, 'nein'));
      return h('td', { class: ['rol-cmp__cell', direct ? 'is-yes' : 'is-inc'] },
        icon(direct ? 'check' : 'link', { size: 16 }), h('span', { class: 'rol-cmp__txt' }, direct ? 'ja' : 'enthalten'));
    };
    const table = h('table', { class: 'table rol-cmp' },
      h('caption', { class: 'visually-hidden' }, 'Rechte aller Rollen im Vergleich'),
      h('thead', {}, h('tr', {}, h('th', { scope: 'col' }, 'Recht'), state.roles.map((r) => h('th', { scope: 'col', class: 'rol-cmp__role' },
        h('button', { type: 'button', class: 'rol-cmp__rolebtn', title: 'Rolle bearbeiten', onClick: () => { t.set('roles'); ctx.setQuery({ tab: null }); selectRole(r); } }, r.is_admin ? icon('lock', { size: 14 }) : null, r.name))))),
      state.groups.map((g) => h('tbody', {},
        h('tr', { class: 'rol-cmp__group' }, h('th', { scope: 'colgroup', colspan: String(state.roles.length + 1) }, g.label)),
        g.permissions.map((p) => h('tr', {}, h('th', { scope: 'row', class: 'rol-cmp__perm' }, p.label), state.roles.map((r) => cell(r, p.key)))))));
    return card({
      title: 'Rechte × Rollen', icon: 'grid',
      subtitle: '„ja“ = direkt vergeben · „enthalten“ = automatisch durch ein anderes Recht · „–“ = nicht vorhanden',
      flush: true,
      body: h('div', { class: 'table-wrap rol-cmp-wrap' }, table),
    });
  }

  // ---------- Anlegen / Umbenennen / Löschen ----------
  function roleForm(role = null, { withCopy = false, copyFrom = null } = {}) {
    const name = input({ value: role?.name || '', maxLength: 60, autocomplete: 'off', placeholder: 'z. B. Empfang' });
    const desc = textarea({ value: role?.description || '', rows: 2, maxLength: 200, placeholder: 'Wofür ist die Rolle gedacht?' });
    const copy = withCopy ? select({ value: copyFrom?.id ?? '', options: [{ value: '', label: 'Leer beginnen (keine Rechte)' }, ...state.roles.map((r) => ({ value: r.id, label: `Rechte von „${r.name}“ übernehmen` }))] }) : null;
    const form = h('div', { class: 'form' },
      field({ label: 'Name', control: name, required: true, name: 'name' }),
      field({ label: 'Beschreibung', control: desc, optional: true, name: 'description', hint: 'Erscheint bei der Rollenauswahl für neue Benutzer.' }),
      copy ? field({ label: 'Vorlage', control: copy, name: 'copy_from' }) : null);
    return { form, name, desc, copy };
  }

  async function createRole(copyFrom = null) {
    if (isDirty() && !(await confirmLeave())) return;
    const f = roleForm(null, { withCopy: true, copyFrom });
    const created = await openDialog({
      title: 'Rolle anlegen', size: 'md', content: f.form,
      actions: [{ label: 'Abbrechen', value: null }, { label: 'Rolle anlegen', variant: 'primary', icon: 'plus', onClick: async () => {
        if (!f.name.value.trim()) { setFieldErrors(f.form, { name: 'Bitte einen Namen eingeben.' }); return false; }
        const body = { name: f.name.value.trim(), description: f.desc.value.trim() };
        if (f.copy.value) body.copy_from = Number(f.copy.value);
        try { return await api.post('/api/roles', body); } catch (err) {
          if (err instanceof ApiError && Object.keys(err.fields || {}).length) { setFieldErrors(f.form, err.fields); return false; }
          if (err instanceof ApiError && err.status === 409) { setFieldErrors(f.form, { name: err.message }); return false; }
          throw err;
        }
      } }],
    }).result;
    if (!created) return;
    toast.success(`Rolle „${created.name}“ angelegt. Jetzt die Rechte festlegen.`);
    t.set('roles'); ctx.setQuery({ tab: null });
    ctx.setDirty(false);
    state.selected = null;
    await load(created.id);
  }

  async function editRole(role) {
    const f = roleForm(role);
    const updated = await openDialog({
      title: 'Rolle umbenennen', size: 'md', content: f.form,
      actions: [{ label: 'Abbrechen', value: null }, { label: 'Speichern', variant: 'primary', icon: 'save', onClick: async () => {
        if (!f.name.value.trim()) { setFieldErrors(f.form, { name: 'Bitte einen Namen eingeben.' }); return false; }
        try { return await api.patch(`/api/roles/${role.id}`, { name: f.name.value.trim(), description: f.desc.value.trim() }); } catch (err) {
          if (err instanceof ApiError && Object.keys(err.fields || {}).length) { setFieldErrors(f.form, err.fields); return false; }
          if (err instanceof ApiError && err.status === 409) { setFieldErrors(f.form, { name: err.message }); return false; }
          throw err;
        }
      } }],
    }).result;
    if (!updated) return;
    toast.success('Rolle gespeichert.');
    const keepDraft = state.draft;
    const idx = state.roles.findIndex((r) => r.id === role.id);
    if (idx >= 0) state.roles[idx] = { ...state.roles[idx], name: updated.name, description: updated.description };
    state.selected = state.roles[idx];
    state.draft = keepDraft;
    syncDirty();
    renderTab();
  }

  async function usersOfRole(role) {
    if (!can('users.view')) return [];
    try { const r = await api.get('/api/users'); return (r.items || []).filter((u) => u.role?.id === role.id); } catch { return []; }
  }

  async function deleteRole(role) {
    if ((role.user_count || 0) > 0) {
      const users = await usersOfRole(role);
      openDialog({
        title: 'Rolle wird noch verwendet', size: 'sm',
        content: h('div', { class: 'stack' },
          h('p', {}, `Die Rolle „${role.name}“ ist ${plural(role.user_count, 'Benutzer', 'Benutzern')} zugewiesen. Vor dem Löschen bitte diesen Konten eine andere Rolle geben.`),
          users.length ? h('ul', { class: 'stack stack--sm' }, users.map((u) => h('li', {}, h('a', { href: '#/users' }, u.display_name), ` (@${u.username})`))) : null),
        actions: [can('users.view') ? { label: 'Zu den Benutzern', onClick: () => { ctx.navigate('/users', { query: { role: role.id } }); return true; } } : null, { label: 'Verstanden', variant: 'primary' }].filter(Boolean),
      });
      return;
    }
    const ok = await confirmDialog({ title: `Rolle „${role.name}“ löschen?`, message: 'Die Rolle wird keinem Konto mehr zugeordnet und kann nicht wiederhergestellt werden.', confirmLabel: 'Rolle löschen', danger: true });
    if (!ok) return;
    try {
      await api.del(`/api/roles/${role.id}`);
      toast.success(`Rolle „${role.name}“ gelöscht.`);
      ctx.setDirty(false);
      state.selected = null;
      await load();
    } catch (err) {
      if (err instanceof ApiError && err.code === 'role_in_use') {
        await load(role.id);
        deleteRole({ ...role, user_count: err.details?.user_count || role.user_count || 1 });
        return;
      }
      toast.error(errorMessage(err));
    }
  }

  await load();
  return () => ctx.setDirty(false);
}
