// „Zu Präsentation hinzufügen“ (aus der Mediathek): Präsentation wählen, Inhalte als Folien anhängen.
//
//   await addToPresentation(contents);   // contents: [{ id, title, type }]
import { h, mount as fill } from '../dom.js';
import { api, errorMessage } from '../api.js';
import { navigate } from '../router.js';
import { openDialog } from './dialog.js';
import { field, select } from './form.js';
import { toast } from './toast.js';
import { loadingBlock, errorState } from './empty.js';
import { plural } from '../format.js';
import { isEditConflict } from './edit-conflict.js';

/** Folie in die Form für PUT /api/presentations/<id>/items bringen. */
export function itemToPayload(it) {
  return {
    id: it.id ?? null,
    content_id: it.content_id ?? it.content?.id,
    enabled: it.enabled !== false,
    duration_s: it.duration_s ?? null,
    transition: it.transition || null,
    valid_from: it.valid_from || null,
    valid_until: it.valid_until || null,
    caption: it.caption || '',
    options: it.options || {},
  };
}

export function newItem(contentId) {
  return { id: null, content_id: contentId, enabled: true, duration_s: null, transition: null, valid_from: null, valid_until: null, caption: '', options: {} };
}

export async function addToPresentation(contents) {
  const n = contents.length;
  if (!n) return false;
  const holder = h('div', {}, loadingBlock('Präsentationen werden geladen …'));
  let sel = null;
  const dlg = openDialog({
    title: 'Zu Präsentation hinzufügen',
    description: n === 1 ? `„${contents[0].title}“ wird als Folie am Ende angehängt.` : `${plural(n, 'Inhalt', 'Inhalte')} werden als Folien am Ende angehängt.`,
    size: 'sm',
    content: holder,
    actions: [{ label: 'Abbrechen', value: null }],
  });

  async function load() {
    try {
      const res = await api.get('/api/presentations');
      const list = (res.items || []).slice().sort((a, b) => a.name.localeCompare(b.name, 'de'));
      if (!list.length) {
        fill(holder, h('p', {}, 'Es gibt noch keine Präsentation. Bitte zuerst unter „Präsentationen“ eine anlegen.'));
        dlg.setActions([{ label: 'Schließen', value: null }, { label: 'Zu den Präsentationen', variant: 'primary', onClick: () => { navigate('/presentations'); } }]);
        return;
      }
      sel = select({ value: String(list[0].id), options: list.map((p) => ({ value: String(p.id), label: `${p.name} (${plural(p.item_count ?? 0, 'Folie', 'Folien')})` })) });
      fill(holder, h('div', { class: 'stack stack--sm' },
        field({ label: 'Präsentation', control: sel }),
        h('p', { class: 'text-2 text-sm' }, 'Die Änderung betrifft den Entwurf. Auf der Stele erscheint sie erst nach dem Veröffentlichen.')));
      dlg.setActions([
        { label: 'Abbrechen', value: null },
        { label: 'Hinzufügen', variant: 'primary', icon: 'plus', onClick: submit },
      ]);
    } catch (err) {
      fill(holder, errorState({ error: err, onRetry: load }));
    }
  }

  async function submit() {
    const id = Number(sel.value);
    const p = await api.get(`/api/presentations/${id}`);
    const items = (p.items || []).map(itemToPayload);
    for (const c of contents) items.push(newItem(c.id));
    try {
      // expected_updated_at: zwischen Laden und Speichern geänderte Folien nicht stillschweigend überschreiben
      await api.put(`/api/presentations/${id}/items`, { items, expected_updated_at: p.updated_at });
    } catch (err) {
      if (!isEditConflict(err)) throw err;
      toast.error(`„${p.name}“ wurde gerade anderweitig geändert. Bitte erneut „Hinzufügen“ wählen – die Folien werden dann an den aktuellen Stand angehängt.`);
      return false;
    }
    toast.success(`${plural(n, 'Folie', 'Folien')} zu „${p.name}“ hinzugefügt.`, {
      action: { label: 'Präsentation öffnen', onClick: () => navigate(`/presentations/${id}`) },
    });
    return true;
  }

  load();
  try {
    return !!(await dlg.result);
  } catch (err) {
    toast.error(errorMessage(err));
    return false;
  }
}
