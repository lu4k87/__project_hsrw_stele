// Info-Folien-Editor (#/media/text/:id, :id = 'new'): Vorlage wählen, Texte, Gestaltung (Farben/Paletten/Verlauf),
// Schrift und Layout – Feinwerte null = wie Design –, Live-Vorschau.
import { h, useStyles, mount as fill } from '../dom.js';
import { icon } from '../icons.js';
import { api, ApiError, errorMessage } from '../api.js';
import { can } from '../session.js';
import { page, pageHeader, card } from '../ui/page.js';
import { field, input, textarea, select, segmented, switchToggle, tagsInput, setFieldErrors } from '../ui/form.js';
import { menuButton } from '../ui/menu.js';
import { confirmDialog } from '../ui/dialog.js';
import { toast } from '../ui/toast.js';
import { errorState, loadingBlock } from '../ui/empty.js';
import { contentStyles, clone, usageList, showInUse, TEMPLATE_LABELS } from '../ui/content-common.js';
import { contentRefField } from '../ui/content-ref.js';
import { colorChoice, contrastWarning, darken } from '../ui/color-choice.js';
import { livePreview } from '../ui/live-preview.js';
import { editorSave } from '../ui/editor-save.js';
import { fontPicker, inheritSelect, paletteRow, loadFonts, WEIGHTS, LINE_HEIGHTS, TRACKINGS, CASES } from '../ui/typography.js';
import { fontStack } from '/shared/fonts.js';

const DEFAULT_DATA = {
  template: 'title_text',
  fields: { title: '', subtitle: '', body: '', image_content_id: null, date: '', time: '', location: '', items: [], audience: '', admission: '', qr_url: '', qr_label: '' },
  style: {
    bg_color: '#0F2747', text_color: '#FFFFFF', accent_color: '#F5B400', bg_image_content_id: null, overlay: 0.4, align: 'left', size: 'm',
    heading_font: null, body_font: null, heading_weight: null, body_weight: null, body_px: null, heading_scale: null, line_height: null,
    heading_tracking: null, heading_case: null, title_color: null, subtitle_color: null, bg_color2: null, bg_angle: null,
    padding: null, valign: null, box: null, box_color: null, box_radius: null, rule: null, logo_corner: null,
  },
};
const HEADING_SCALES = [{ value: 1.6, label: 'Klein' }, { value: 1.9, label: 'Mittel' }, { value: 2.4, label: 'Groß' }, { value: 2.9, label: 'Sehr groß' }];
const ANGLES = [{ value: 180, label: 'Oben → unten' }, { value: 135, label: 'Diagonal' }, { value: 90, label: 'Links → rechts' }, { value: 0, label: 'Unten → oben' }];
const RADII = [{ value: 0, label: 'Eckig' }, { value: 16, label: 'Leicht gerundet' }, { value: 56, label: 'Stark gerundet' }];
const CORNERS = [{ value: 'top-left', label: 'Oben links' }, { value: 'top-right', label: 'Oben rechts' }, { value: 'bottom-left', label: 'Unten links' }, { value: 'bottom-right', label: 'Unten rechts' }];

const TEMPLATES = [
  { id: 'title_text', label: TEMPLATE_LABELS.title_text, desc: 'Überschrift mit Fließtext.' },
  { id: 'image_text', label: TEMPLATE_LABELS.image_text, desc: 'Großes Bild oben, Text darunter.' },
  { id: 'statement', label: TEMPLATE_LABELS.statement, desc: 'Zitat oder Kernbotschaft, groß.' },
  { id: 'event', label: TEMPLATE_LABELS.event, desc: 'Termin mit Datum, Uhrzeit und Ort.' },
  { id: 'list', label: TEMPLATE_LABELS.list, desc: 'Überschrift mit Aufzählung.' },
];

// Felder je Vorlage (Reihenfolge = Anzeige)
const FIELDS = {
  title_text: ['title', 'subtitle', 'body'],
  image_text: ['image_content_id', 'title', 'subtitle', 'body'],
  statement: ['title', 'subtitle', 'body'],
  event: ['title', 'subtitle', 'audience', 'date', 'time', 'location', 'admission', 'body'],
  list: ['title', 'subtitle', 'items', 'body'],
};
const LABELS = {
  title_text: { title: 'Überschrift', subtitle: 'Unterzeile', body: 'Text' },
  image_text: { title: 'Überschrift', subtitle: 'Unterzeile', body: 'Text', image_content_id: 'Bild' },
  statement: { title: 'Aussage oder Zitat', subtitle: 'Quelle / Person', body: 'Ergänzender Text' },
  event: { title: 'Name der Veranstaltung', subtitle: 'Rubrik', body: 'Beschreibung', date: 'Datum', time: 'Uhrzeit', location: 'Ort', audience: 'Für wen?', admission: 'Eintritt' },
  list: { title: 'Überschrift', subtitle: 'Unterzeile', items: 'Aufzählungspunkte', body: 'Text unter der Liste' },
};
// Lesbarkeit im Vorbeigehen (Content-Strategie): Überschrift + ein Satz oder 1–2 Stichpunkte, lesbar aus 3–5 m
const MAX_WORDS = 30;
const READABLE_PX = 36; // Fließtext darunter ist aus 3–5 m schwer lesbar (Player verkleinert bis 30 px)

const HINTS = {
  statement: { subtitle: 'Wird als „— Quelle“ angezeigt.' },
  event: { subtitle: 'z. B. Vortrag, Führung, Konzert', time: 'z. B. 18:00 oder 18:00–20:00', location: 'z. B. Großer Saal, 2. OG', audience: 'z. B. Studierende, alle Interessierten', admission: 'z. B. Eintritt frei' },
  list: { items: 'Ein Punkt pro Zeile. Kurz halten – im Vorbeigehen werden 1–2 Punkte erfasst.' },
  image_text: { image_content_id: 'Am besten ein Bild im Querformat (z. B. 1920 × 1080).' },
};

export default async function mount(root, ctx) {
  await Promise.all([useStyles('/admin/css/views/text-slide-editor.css'), contentStyles()]);
  const isNew = ctx.params.id === 'new';
  const canEdit = can('content.edit');
  const ro = !canEdit;

  root.append(loadingBlock());
  let content = null;
  let designs = [];
  let defaultDesignId = null;
  try {
    const [c, ds, st] = await Promise.all([
      // Schriften zuerst anmelden (Schriftproben); Fehler → nur mitgelieferte Schriften
      isNew ? Promise.resolve(null) : api.get(`/api/contents/${encodeURIComponent(ctx.params.id)}`, { signal: ctx.signal }),
      can('presentations.view') ? api.get('/api/designs', { signal: ctx.signal }).catch(() => ({ items: [] })) : Promise.resolve({ items: [] }),
      api.get('/api/settings', { signal: ctx.signal }).catch(() => ({})),
      loadFonts(ctx.signal),
    ]);
    content = c;
    designs = ds.items || [];
    defaultDesignId = st.default_design_id ?? null;
  } catch (err) {
    if (err?.name === 'AbortError') return undefined;
    fill(root, page({}, pageHeader({ title: 'Info-Folie', back: { href: '#/media', label: 'Mediathek' } }),
      card({ body: errorState({ title: 'Info-Folie konnte nicht geladen werden', error: err, onRetry: () => ctx.navigate(`/media/text/${ctx.params.id}`, { replace: true }) }) })));
    return undefined;
  }
  if (content && content.type !== 'text') {
    ctx.navigate('/media', { replace: true });
    return undefined;
  }
  if (isNew && !canEdit) {
    fill(root, page({}, pageHeader({ title: 'Neue Info-Folie', back: { href: '#/media', label: 'Mediathek' } }),
      card({ body: h('p', {}, 'Für das Anlegen von Info-Folien fehlt das Recht „Inhalte hochladen, anlegen und bearbeiten“.') })));
    return undefined;
  }

  const data = merge(DEFAULT_DATA, content?.data || {});
  let title = content?.title || '';
  let tags = [...(content?.tags || [])];
  let designId = designs.some((d) => d.id === defaultDesignId) ? defaultDesignId : (designs[0]?.id ?? null);
  const designConfig = () => designs.find((d) => d.id === designId)?.config || null;

  function snapshot() { return clone({ data, title, tags }); }
  ctx.setTitle(isNew ? 'Neue Info-Folie' : (content.title || 'Info-Folie'));

  // ---------- Speichern ----------
  const formRoot = h('div', { class: 'ts-form' });
  const ed = editorSave({
    ctx, ro, formRoot, label: isNew ? 'Anlegen' : 'Speichern', emphasize: false, stateClass: 'ts-savestate',
    dirtyMessage: 'Die Info-Folie hat ungespeicherte Änderungen.', savedLabel: isNew ? '' : 'Gespeichert',
    snapshot,
    validate: () => {
      const f = data.fields;
      const errs = {};
      if (!String(f.title || '').trim()) errs['fields.title'] = 'Bitte eine Überschrift eingeben.';
      if (data.template === 'image_text' && !f.image_content_id) errs['fields.image_content_id'] = 'Bitte ein Bild auswählen.';
      if (f.qr_url && !/^https?:\/\/\S+$/i.test(f.qr_url)) errs['fields.qr_url'] = 'Bitte eine Adresse eingeben, die mit http:// oder https:// beginnt.';
      if (!Object.keys(errs).length) return true;
      setFieldErrors(formRoot, errs);
      toast.error('Bitte die markierten Felder prüfen.');
      return false;
    },
    send: () => {
      const payload = { title: (title.trim() || String(data.fields.title).trim().split('\n')[0]).slice(0, 120), tags, data: clone(data) };
      return isNew ? api.post('/api/contents', { type: 'text', ...payload })
        : api.patch(`/api/contents/${content.id}`, { ...payload, expected_updated_at: content.updated_at });
    },
    onSaved: (res) => {
      ctx.setDirty(false);
      if (isNew) {
        toast.success(`Info-Folie „${res.title}“ angelegt.`);
        ctx.navigate(`/media/text/${res.id}`, { replace: true });
        return;
      }
      content = { ...content, ...res };
      title = res.title;
      titleIn.value = title;
      ed.markSaved();
      header.titleEl.textContent = res.title;
      ctx.setTitle(res.title);
      ed.changed(false);
      toast.success(usages.length ? 'Gespeichert. Auf den Stelen sichtbar nach erneutem Veröffentlichen der Präsentation.' : 'Info-Folie gespeichert.');
    },
    fieldMap: (k) => k.replace(/^data\./, ''),
    onChanged: (preview) => {
      updateContrast();
      updateReadHint();
      if (preview) refreshPreview();
    },
    // 409 edit_conflict: jemand anderes hat gespeichert → Hinweis mit „Neu laden“ statt Überschreiben
    onReload: isNew ? null : () => { ctx.setDirty(false); ctx.navigate(`/media/text/${content.id}`, { replace: true }); },
  });
  const changed = ed.changed;

  // ---------- Kopf ----------
  const headerTitle = isNew ? 'Neue Info-Folie' : (content.title || 'Info-Folie');
  const header = pageHeader({
    title: headerTitle,
    back: { href: '#/media', label: 'Mediathek' },
    description: 'Text-Folie aus einer Vorlage gestalten – die Vorschau rechts zeigt sie wie auf der Stele.',
    actions: canEdit ? [
      ed.saveState,
      !isNew ? menuButton({ items: [
        { label: 'Duplizieren', icon: 'copy', onClick: duplicate },
        { separator: true },
        can('content.delete') ? { label: 'Löschen', icon: 'trash', danger: true, onClick: remove } : null,
      ] }) : null,
      ed.saveBtn,
    ] : [],
  });

  // Hinweis „wird verwendet“
  const usages = (content?.usages || []).filter((u) => u.type === 'presentation');
  const usedAlert = usages.length ? h('div', { class: 'alert' }, icon('info'), h('div', { class: 'alert__body' },
    h('div', { class: 'alert__title' }, `Wird in ${usages.length === 1 ? 'einer Präsentation' : `${new Set(usages.map((u) => u.id)).size} Präsentationen`} verwendet`),
    h('div', { class: 'alert__text' }, 'Änderungen erscheinen auf den Stelen erst, nachdem die Präsentation erneut veröffentlicht wurde.'),
    h('details', { class: 'ts-usages' }, h('summary', {}, 'Verwendungen anzeigen'), usageList(content.usages)))) : null;
  const roAlert = ro ? h('div', { class: 'alert alert--neutral' }, icon('eye'), h('div', { class: 'alert__body' },
    h('div', { class: 'alert__text' }, 'Nur Ansicht – zum Bearbeiten fehlt das Recht „Inhalte hochladen, anlegen und bearbeiten“.'))) : null;

  // ---------- Vorlage ----------
  const tplGroup = h('div', { class: 'ts-templates', role: 'radiogroup', 'aria-label': 'Vorlage' });
  function renderTemplates(focus = false) {
    fill(tplGroup, ...TEMPLATES.map((t) => {
      const on = data.template === t.id;
      return h('button', {
        type: 'button', class: 'ts-tpl', role: 'radio', 'aria-checked': String(on), tabindex: on ? '0' : '-1',
        disabled: ro, dataset: { tpl: t.id },
        onClick: () => setTemplate(t.id),
      }, tplIllustration(t.id), h('span', { class: 'ts-tpl__label' }, t.label), h('span', { class: 'ts-tpl__desc' }, t.desc));
    }));
    if (focus) tplGroup.querySelector('[aria-checked="true"]')?.focus();
  }
  tplGroup.addEventListener('keydown', (e) => {
    if (!['ArrowRight', 'ArrowLeft', 'ArrowDown', 'ArrowUp'].includes(e.key)) return;
    e.preventDefault();
    const i = TEMPLATES.findIndex((t) => t.id === data.template);
    const n = (i + (e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1 : -1) + TEMPLATES.length) % TEMPLATES.length;
    setTemplate(TEMPLATES[n].id, true);
  });
  function setTemplate(id, focus = false) {
    if (data.template === id) return;
    data.template = id;
    renderTemplates(focus);
    renderFields();
    changed();
  }

  // ---------- Felder ----------
  const fieldsBox = h('div', { class: 'form' });
  const titleIn = input({ value: title, maxLength: 120, disabled: ro, placeholder: 'wie die Überschrift', onInput: (v) => { title = v; changed(false); } });
  const tagsIn = tagsInput({ value: tags, onChange: (v) => { tags = v; changed(false); } });
  if (ro) tagsIn.input.disabled = true;

  function renderFields() {
    const tpl = data.template;
    const f = data.fields;
    const labels = LABELS[tpl];
    const hints = HINTS[tpl] || {};
    const nodes = FIELDS[tpl].map((key) => {
      const label = labels[key];
      if (key === 'image_content_id') {
        return field({ label, name: `fields.${key}`, hint: hints[key], control: contentRefField({
          value: f.image_content_id, types: ['image'], pickerTitle: 'Bild für die Info-Folie wählen', disabled: ro,
          onChange: (id) => { f.image_content_id = id; changed(); },
        }) });
      }
      if (key === 'body') {
        return field({ label, name: `fields.${key}`, optional: true, hint: 'Zeilenumbrüche werden übernommen. Kurze Absätze lesen sich auf der Stele besser.', control: textarea({ value: f.body, rows: 5, maxLength: 2000, disabled: ro, onInput: (v) => { f.body = v; changed(); } }) });
      }
      if (key === 'items') {
        return field({ label, name: `fields.${key}`, hint: hints.items, control: textarea({
          value: (f.items || []).join('\n'), rows: 6, disabled: ro,
          onInput: (v) => { f.items = v.split('\n').map((s) => s.trim()).filter(Boolean); changed(); },
        }) });
      }
      if (key === 'date') {
        return field({ label, name: `fields.${key}`, control: input({ type: 'date', value: /^\d{4}-\d{2}-\d{2}$/.test(f.date) ? f.date : '', disabled: ro, onInput: (v) => { f.date = v; changed(); } }) });
      }
      if (key === 'title' && tpl === 'statement') {
        return field({ label, name: `fields.${key}`, required: true, control: textarea({ value: f.title, rows: 3, maxLength: 300, disabled: ro, onInput: (v) => { f.title = v; changed(); } }) });
      }
      return field({
        label, name: `fields.${key}`, hint: hints[key], required: key === 'title', optional: key !== 'title',
        control: input({ value: f[key] || '', maxLength: key === 'title' ? 200 : (['audience', 'admission'].includes(key) ? 120 : 160), disabled: ro, onInput: (v) => { f[key] = v; changed(); } }),
      });
    });
    const keep = document.activeElement && fieldsBox.contains(document.activeElement);
    fill(fieldsBox, ...nodes);
    if (keep) fieldsBox.querySelector('input, textarea, button')?.focus();
  }

  // ---------- QR-Code (alle Vorlagen) ----------
  const qrUrlIn = input({ type: 'url', value: data.fields.qr_url || '', maxLength: 500, placeholder: 'https://…', disabled: ro, onInput: (v) => { data.fields.qr_url = v.trim(); changed(); } });
  const qrLabelIn = input({ value: data.fields.qr_label || '', maxLength: 80, disabled: ro, onInput: (v) => { data.fields.qr_label = v; changed(); } });

  // ---------- Gestaltung ----------
  const s = data.style;
  const contrast = contrastWarning();
  const bgColor = colorChoice({ label: 'Hintergrundfarbe', value: s.bg_color, onChange: (v) => { s.bg_color = v; changed(); } });
  const textColor = colorChoice({ label: 'Textfarbe', value: s.text_color, onChange: (v) => { s.text_color = v; changed(); } });
  const accentColor = colorChoice({ label: 'Akzentfarbe', value: s.accent_color, onChange: (v) => { s.accent_color = v; changed(); } });
  const overlayOut = h('output', {}, `${Math.round(s.overlay * 100)} %`);
  const overlayIn = h('input', { type: 'range', min: '0', max: '0.8', step: '0.05', value: String(s.overlay), disabled: ro, 'aria-label': 'Abdunklung des Hintergrundbilds' });
  overlayIn.addEventListener('input', () => { s.overlay = Number(overlayIn.value); overlayOut.textContent = `${Math.round(s.overlay * 100)} %`; changed(); });
  const overlayField = field({ label: 'Abdunklung', name: 'style.overlay', hint: 'Macht Text über dem Bild besser lesbar (empfohlen ab 40 %).', control: h('div', { class: 'cu-range' }, overlayIn, overlayOut) });
  overlayField.hidden = !s.bg_image_content_id;
  const bgImage = contentRefField({
    value: s.bg_image_content_id, types: ['image'], pickerTitle: 'Hintergrundbild wählen', emptyLabel: 'Kein Hintergrundbild', disabled: ro,
    onChange: (id) => { s.bg_image_content_id = id; overlayField.hidden = !id; changed(); },
  });
  const alignSeg = segmented({ value: s.align, ariaLabel: 'Ausrichtung', options: [{ value: 'left', label: 'Links' }, { value: 'center', label: 'Zentriert' }], onChange: (v) => { s.align = v; changed(); } });
  const sizeSeg = segmented({ value: s.body_px === null ? s.size : 'custom', ariaLabel: 'Schriftgröße', options: [{ value: 's', label: 'Klein' }, { value: 'm', label: 'Mittel' }, { value: 'l', label: 'Groß' }, { value: 'custom', label: 'Eigene' }],
    onChange: (v) => {
      if (v === 'custom') { s.body_px = Number(pxIn.value); } else { s.size = v; s.body_px = null; }
      pxField.hidden = v !== 'custom';
      changed();
    } });
  const set = (key) => (v) => { s[key] = v; changed(); };
  const designTheme = () => designConfig()?.theme || {};

  // Paletten und Verlauf
  const bg2Color = colorChoice({ label: 'Zweite Verlaufsfarbe', value: s.bg_color2 || s.bg_color, onChange: (v) => { s.bg_color2 = v; changed(); } });
  const gradBox = h('div', { class: 'form-row' },
    field({ label: 'Zweite Farbe', name: 'style.bg_color2', control: bg2Color }),
    inheritSelect({ label: 'Richtung', value: s.bg_angle, options: ANGLES, inheritLabel: 'Oben → unten (Standard)', disabled: ro, onChange: set('bg_angle') }));
  gradBox.hidden = !s.bg_color2;
  const gradSw = switchToggle({ label: 'Farbverlauf', hint: 'Hintergrund läuft von der Hintergrundfarbe in eine zweite Farbe.', checked: !!s.bg_color2, disabled: ro,
    onChange: (v) => { s.bg_color2 = v ? bg2Color.getValue() : null; gradBox.hidden = !v; changed(); } });
  const palettes = paletteRow({ disabled: ro, onPick: (p) => {
    Object.assign(s, { bg_color: p.bg, text_color: p.text, accent_color: p.accent, bg_color2: p.bg2 || null, title_color: null, subtitle_color: null });
    bgColor.setValue(p.bg); textColor.setValue(p.text); accentColor.setValue(p.accent);
    if (p.bg2) bg2Color.setValue(p.bg2);
    gradSw.querySelector('input').checked = !!p.bg2;
    gradBox.hidden = !p.bg2;
    titleColor.reset(); subtitleColor.reset();
    changed();
  } });

  // Optionale Farbe: aus = erbt (Überschrift wie Text, Unterzeile wie Akzent)
  function optionalColor({ label, key, offHint, fallback }) {
    const choice = colorChoice({ label, value: s[key] || fallback(), onChange: (v) => { s[key] = v; changed(); } });
    const box = field({ label, name: `style.${key}`, control: choice });
    box.hidden = !s[key];
    const sw = switchToggle({ label: `Eigene ${label}`, hint: offHint, checked: !!s[key], disabled: ro,
      onChange: (v) => { s[key] = v ? choice.getValue() : null; box.hidden = !v; changed(); } });
    const el = h('div', { class: 'stack stack--sm' }, sw, box);
    el.reset = () => { sw.querySelector('input').checked = false; box.hidden = true; choice.setValue(fallback()); };
    return el;
  }
  const titleColor = optionalColor({ label: 'Farbe der Überschrift', key: 'title_color', offHint: 'Aus: wie Textfarbe.', fallback: () => s.text_color });
  const subtitleColor = optionalColor({ label: 'Farbe der Unterzeile', key: 'subtitle_color', offHint: 'Aus: wie Akzentfarbe.', fallback: () => s.accent_color });

  // Schrift
  const designFont = (k) => () => fontStack(designTheme()[k]) || fontStack(designTheme().font) || fontStack('sans');
  const headingFont = fontPicker({ label: 'Schrift für Überschriften', value: s.heading_font, inheritLabel: 'Wie Design', inheritStack: designFont('heading_font'), disabled: ro, onChange: set('heading_font') });
  const bodyFont = fontPicker({ label: 'Schrift für Text', value: s.body_font, inheritLabel: 'Wie Design', inheritStack: designFont('font'), disabled: ro, onChange: set('body_font') });
  const pxOut = h('output', {}, `${s.body_px || 48} px`);
  const pxIn = h('input', { type: 'range', min: '28', max: '96', step: '2', value: String(s.body_px || 48), disabled: ro, 'aria-label': 'Eigene Schriftgröße des Texts' });
  pxIn.addEventListener('input', () => { s.body_px = Number(pxIn.value); pxOut.textContent = `${pxIn.value} px`; changed(); });
  const pxField = field({ label: 'Eigene Größe', name: 'style.body_px', hint: 'Fließtext auf der 1080 px breiten Stele; ab 40 px gut lesbar aus 3–5 m.', control: h('div', { class: 'cu-range' }, pxIn, pxOut) });
  pxField.hidden = s.body_px === null;

  // Layout
  const boxColor = optionalColor({ label: 'Farbe des Textfelds', key: 'box_color', offHint: 'Aus: etwas dunkler als der Hintergrund.', fallback: () => s.bg_color });
  const boxDetails = h('div', { class: 'stack' }, boxColor,
    inheritSelect({ label: 'Ecken des Textfelds', value: s.box_radius, options: RADII, inheritLabel: 'Gerundet (Standard)', disabled: ro, onChange: set('box_radius') }));
  boxDetails.hidden = !s.box || s.box === 'none';
  const boxSeg = segmented({ value: s.box || 'none', ariaLabel: 'Textfeld', options: [{ value: 'none', label: 'Ohne' }, { value: 'solid', label: 'Fläche' }, { value: 'glass', label: 'Milchglas' }],
    onChange: (v) => { s.box = v === 'none' ? null : v; boxDetails.hidden = !s.box; changed(); } });
  const valignSeg = segmented({ value: s.valign || 'center', ariaLabel: 'Senkrechte Ausrichtung', options: [{ value: 'top', label: 'Oben' }, { value: 'center', label: 'Mitte' }, { value: 'bottom', label: 'Unten' }],
    onChange: (v) => { s.valign = v === 'center' ? null : v; changed(); } });
  const ruleSw = switchToggle({ label: 'Akzentlinie anzeigen', hint: 'Linie über der Überschrift bzw. unter dem Bild.', checked: s.rule !== false, disabled: ro, onChange: (v) => { s.rule = v ? null : false; changed(); } });

  if (ro) for (const b of [...alignSeg.querySelectorAll('button'), ...sizeSeg.querySelectorAll('button'), ...boxSeg.querySelectorAll('button'), ...valignSeg.querySelectorAll('button'), ...bgColor.querySelectorAll('input,button'), ...textColor.querySelectorAll('input,button'), ...accentColor.querySelectorAll('input,button')]) b.disabled = true;

  // ---------- Vorschau ----------
  const pv = livePreview({ title: 'Vorschau der Info-Folie' });
  const readHint = h('div', { class: 'stack ts-read', role: 'status' });
  let fitState = null;
  pv.frame.on('player:fit', (m) => { fitState = { body_px: Number(m.body_px) || 0, clipped: Boolean(m.clipped) }; updateReadHint(); });
  const designSel = select({
    value: designId === null ? '' : String(designId), 'aria-label': 'Rahmen für die Vorschau',
    options: [{ value: '', label: 'Ohne Header und Footer' }, ...designs.map((d) => ({ value: String(d.id), label: d.name }))],
    onChange: (v) => { designId = v ? Number(v) : null; headingFont.refreshSample(); bodyFont.refreshSample(); refreshPreview(); },
  });

  function refreshPreview(now = false) {
    const body = {
      settings: null,
      design: designConfig(),
      touch_menu: null,
      items: [{ id: null, content_id: content?.id ?? null, content: { type: 'text', data: previewData() }, enabled: true, duration_s: null, transition: 'none', valid_from: null, valid_until: null, caption: '', options: {} }],
    };
    const pick = (res) => ({ slide: res.slides?.[0] || null, design: res.design, settings: res.settings, view: 'slide' });
    if (now) pv.now(body, pick); else pv.update(body, pick);
  }

  // Leere Überschrift in der Vorschau als Platzhalter zeigen (nur Vorschau, wird nicht gespeichert)
  function previewData() {
    const d = clone(data);
    if (!String(d.fields.title || '').trim()) d.fields.title = LABELS[d.template].title;
    return d;
  }

  function updateContrast() {
    const surface = s.box === 'solid' && s.box_color ? s.box_color : s.bg_color;
    const pairs = [
      { fg: s.text_color, bg: surface, label: 'Text auf Hintergrundfarbe' },
      { fg: s.accent_color, bg: surface, label: 'Akzentfarbe auf Hintergrund (Linien, Symbole)', min: 3 },
    ];
    if (s.title_color) pairs.push({ fg: s.title_color, bg: surface, label: 'Überschrift auf Hintergrund', min: 3 });
    if (s.subtitle_color) pairs.push({ fg: s.subtitle_color, bg: surface, label: 'Unterzeile auf Hintergrund' });
    if (s.bg_color2 && !(s.box === 'solid' && s.box_color)) pairs.push({ fg: s.text_color, bg: s.bg_color2, label: 'Text auf zweiter Verlaufsfarbe' });
    if (s.bg_image_content_id) pairs.push({ fg: s.text_color, bg: darken('#8A8A8A', s.overlay), label: 'Text auf abgedunkeltem Hintergrundbild (Schätzung)' });
    contrast.update(pairs);
  }

  function wordCount() {
    const f = data.fields;
    const parts = ['title', 'subtitle', 'body', 'items'].filter((k) => FIELDS[data.template].includes(k))
      .map((k) => (Array.isArray(f[k]) ? f[k].join(' ') : String(f[k] || '')));
    return parts.join(' ').split(/\s+/).filter((w) => /[\p{L}\p{N}]/u.test(w)).length;
  }

  function readAlert(kind, title, text) {
    return h('div', { class: `alert alert--${kind}` }, icon(kind === 'danger' ? 'alert-circle' : 'alert-triangle'),
      h('div', { class: 'alert__body' }, h('div', { class: 'alert__title' }, title), h('div', { class: 'alert__text' }, text)));
  }

  function updateReadHint() {
    const words = wordCount();
    fill(readHint,
      words > MAX_WORDS ? readAlert('warning', `Viel Text: ${words} Wörter`, `Im Vorbeigehen wird nur wenig gelesen. Empfohlen: Überschrift und ein Satz oder 1–2 Stichpunkte (bis ca. ${MAX_WORDS} Wörter).`) : null,
      fitState?.clipped ? readAlert('danger', 'Text passt nicht auf die Folie', 'Das Ende wird abgeschnitten. Text kürzen.')
        : fitState && fitState.body_px < READABLE_PX ? readAlert('warning', `Schrift auf ${fitState.body_px} px verkleinert`, 'Aus 3–5 m Abstand schwer lesbar. Text kürzen, damit die Schrift groß bleibt.') : null);
  }

  async function duplicate() {
    if (ed.isDirty() && !(await confirmDialog({ title: 'Ungespeicherte Änderungen', message: 'Die Kopie wird vom zuletzt gespeicherten Stand erstellt. Trotzdem duplizieren?', confirmLabel: 'Duplizieren', icon: 'copy' }))) return;
    try {
      const copy = await api.post(`/api/contents/${content.id}/duplicate`);
      ctx.setDirty(false);
      toast.success(`Kopie „${copy.title}“ angelegt.`);
      ctx.navigate(`/media/text/${copy.id}`);
    } catch (err) { toast.error(errorMessage(err)); }
  }

  async function remove() {
    const ok = await confirmDialog({ title: `„${content.title}“ löschen?`, message: 'Die Info-Folie wird endgültig entfernt.', confirmLabel: 'Info-Folie löschen', danger: true });
    if (!ok) return;
    try {
      await api.del(`/api/contents/${content.id}`);
      ctx.setDirty(false);
      toast.success('Info-Folie gelöscht.');
      ctx.navigate('/media');
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) await showInUse({ title: 'Info-Folie kann nicht gelöscht werden', message: err.message, usages: err.details?.usages || [] });
      else toast.error(errorMessage(err));
    }
  }

  // ---------- Aufbau ----------
  renderTemplates();
  renderFields();
  formRoot.append(
    card({ title: 'Vorlage', icon: 'layers', body: tplGroup }),
    card({ title: 'Inhalt', icon: 'type', body: h('div', { class: 'form' },
      fieldsBox,
      h('div', { class: 'form-section' },
        field({ label: 'Titel in der Mediathek', name: 'title', optional: true, hint: 'Leer = Überschrift wird verwendet.', control: titleIn }),
        field({ label: 'Schlagworte', name: 'tags', optional: true, control: tagsIn }))) }),
    card({ title: 'QR-Code', icon: 'qr-code', subtitle: 'Für weiterführende Infos, z. B. Anmeldung oder Lageplan', body: h('div', { class: 'form' },
      field({ label: 'Adresse', name: 'fields.qr_url', optional: true, hint: 'Leer = kein QR-Code. Kurze Adressen ergeben einen gröberen, besser scanbaren Code.', control: qrUrlIn }),
      field({ label: 'Beschriftung', name: 'fields.qr_label', optional: true, hint: 'z. B. „Jetzt anmelden“', control: qrLabelIn })) }),
    card({ title: 'Gestaltung', icon: 'palette', body: h('div', { class: 'form' },
      field({ label: 'Farbpalette', control: palettes, hint: 'Übernimmt Hintergrund, Text, Akzent und ggf. Verlauf – danach frei anpassbar.' }),
      h('div', { class: 'ts-colors' },
        field({ label: 'Hintergrundfarbe', name: 'style.bg_color', control: bgColor }),
        field({ label: 'Textfarbe', name: 'style.text_color', control: textColor }),
        field({ label: 'Akzentfarbe', name: 'style.accent_color', hint: 'Linie, Symbole und Aufzählungspunkte.', control: accentColor })),
      gradSw, gradBox,
      h('div', { class: 'form-row' }, titleColor, subtitleColor),
      contrast,
      field({ label: 'Hintergrundbild', name: 'style.bg_image_content_id', optional: true, control: bgImage }),
      overlayField) }),
    card({ title: 'Schrift', icon: 'type', subtitle: '„Wie Design“ übernimmt die Schrift-Einstellungen des Designs der Präsentation', body: h('div', { class: 'form' },
      h('div', { class: 'form-row' }, headingFont, bodyFont),
      h('div', { class: 'form-row' },
        field({ label: 'Schriftgröße', name: 'style.size', hint: 'Für 3–5 m Abstand „Mittel“ oder „Groß“. Lange Texte werden automatisch verkleinert.', control: sizeSeg }),
        pxField),
      h('div', { class: 'ty-grid' },
        inheritSelect({ label: 'Größe der Überschrift', value: s.heading_scale, options: HEADING_SCALES, inheritLabel: 'Standard der Vorlage', disabled: ro, onChange: set('heading_scale') }),
        inheritSelect({ label: 'Stärke der Überschrift', value: s.heading_weight, options: WEIGHTS, inheritLabel: 'Wie Design', disabled: ro, onChange: set('heading_weight') }),
        inheritSelect({ label: 'Stärke des Texts', value: s.body_weight, options: WEIGHTS, inheritLabel: 'Wie Design', disabled: ro, onChange: set('body_weight') }),
        inheritSelect({ label: 'Zeilenabstand', value: s.line_height, options: LINE_HEIGHTS, inheritLabel: 'Wie Design', disabled: ro, onChange: set('line_height') }),
        inheritSelect({ label: 'Laufweite der Überschrift', value: s.heading_tracking, options: TRACKINGS, inheritLabel: 'Wie Design', disabled: ro, onChange: set('heading_tracking') }),
        inheritSelect({ label: 'Schreibweise der Überschrift', value: s.heading_case, options: CASES, inheritLabel: 'Wie Design', disabled: ro, onChange: set('heading_case') }))) }),
    card({ title: 'Layout', icon: 'grid', body: h('div', { class: 'form' },
      h('div', { class: 'form-row' },
        field({ label: 'Ausrichtung', name: 'style.align', control: alignSeg }),
        field({ label: 'Senkrechte Lage', name: 'style.valign', hint: 'Bei langen Texten beginnt der Text immer oben.', control: valignSeg })),
      h('div', { class: 'form-row' },
        inheritSelect({ label: 'Innenabstand', value: s.padding, options: [{ value: 's', label: 'Schmal' }, { value: 'l', label: 'Breit' }], inheritLabel: 'Standard', disabled: ro, onChange: set('padding') }),
        inheritSelect({ label: 'Logo in der Ecke', value: s.logo_corner, options: CORNERS, inheritLabel: 'Kein Logo', hint: 'Zeigt das Logo aus dem Design der Präsentation.', disabled: ro, onChange: set('logo_corner') })),
      field({ label: 'Textfeld', name: 'style.box', hint: 'Fläche hinter dem Text – hilft bei Hintergrundbildern.', control: boxSeg }),
      boxDetails,
      ruleSw) }),
  );

  if (ro) for (const el of formRoot.querySelectorAll('.cu-color input, .cu-color button')) el.disabled = true;

  const previewCard = card({
    title: 'Vorschau', icon: 'eye', className: 'ts-preview',
    body: h('div', { class: 'stack' },
      designs.length ? field({ label: 'Rahmen (Design) für die Vorschau', control: designSel, hint: 'Nur für die Vorschau – auf der Stele gilt das Design der Präsentation.' }) : null,
      pv.el,
      readHint),
  });

  fill(root, page({ wide: true, className: 'ts-page' },
    header,
    roAlert,
    ed.conflictBox,
    usedAlert,
    h('div', { class: 'ts-layout' }, formRoot, h('aside', { class: 'ts-side' }, previewCard)),
  ));
  changed(false);
  ed.saveState.textContent = isNew ? 'Noch nicht gespeichert' : 'Gespeichert';
  refreshPreview(true);

  return () => {
    ed.destroy();
    pv.destroy();
    headingFont.destroy();
    bodyFont.destroy();
  };
}

/** Standardwerte tief auffüllen (wie merge_defaults des Servers). */
function merge(defaults, value) {
  const out = clone(defaults);
  for (const [k, v] of Object.entries(value || {})) {
    if (!(k in out)) continue;
    if (out[k] && typeof out[k] === 'object' && !Array.isArray(out[k]) && v && typeof v === 'object' && !Array.isArray(v)) out[k] = merge(out[k], v);
    else if (v !== undefined) out[k] = clone(v);
  }
  return out;
}

/** Kleine Skizze je Vorlage (rein dekorativ). */
function tplIllustration(id) {
  const b = (cls) => h('span', { class: `ts-sk__${cls}` });
  const parts = {
    title_text: [b('rule'), b('title'), b('line'), b('line'), b('line short')],
    image_text: [b('img'), b('title'), b('line'), b('line short')],
    statement: [b('quote'), b('title center'), b('line center short')],
    event: [b('badge'), b('title'), b('fact'), b('fact'), b('fact')],
    list: [b('rule'), b('title'), b('bullet'), b('bullet'), b('bullet')],
  };
  return h('span', { class: `ts-sk ts-sk--${id}`, 'aria-hidden': 'true' }, parts[id]);
}
