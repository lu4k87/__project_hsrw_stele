// Steuerung der Bühne: Manifest → aktive Präsentation (Zeitplan), Rahmen, Diashow, Touch-Modus,
// Standby und Nachtmodus. Gemeinsam genutzt von Normalbetrieb, Live-Ansicht, Vorschau und Einzelfolie.

import { TIMING, normalizeSteleSettings, DEFAULT_TIMEZONE } from './config.js';
import { h } from './util.js';
import { clock, wallParts, validTimezone } from './time.js';
import { resolveActive, nightModeActive } from './schedule.js';
import { Frame, frameInsets } from './frame.js';
import { Slideshow, preparePresentation } from './slideshow.js';
import { TouchMode } from './touch.js';
import { Standby, NightScreen } from './overlays.js';
import { registerFonts } from '/shared/fonts.js';

export class Player {
  // opts: stageEl, stage, mode ('normal'|'mirror'|'preview'|'slide'), muted, touchAllowed,
  //       autoplay, onState(), onPlayed(rec), onTouchEvent(ev), onReady()
  constructor(opts) {
    this.o = opts;
    this.mode = opts.mode;
    this.stage = opts.stage;
    this.stageEl = opts.stageEl;
    this.layers = h('div', { class: 'layers' });
    this.stageEl.appendChild(this.layers);
    this.frame = new Frame(this.stageEl);
    this.manifest = null;
    this.timezone = DEFAULT_TIMEZONE;
    this.steleSettings = normalizeSteleSettings(null);
    this.presentationId = null; // Vorschau: feste Präsentation
    this.activeSig = null;
    this.activePres = null;
    this.shownPres = null;
    this.source = 'none';
    this.night = false;
    this.empty = false;
    this.userPaused = opts.autoplay === false;
    this.slideMode = opts.mode === 'slide';

    this.show = new Slideshow({
      container: this.layers,
      stage: this.stage,
      autoplay: opts.autoplay !== false,
      muted: Boolean(opts.muted),
      allowPlaceholder: this.slideMode,
      respectValidity: !this.slideMode,
      loopSingle: this.slideMode,
      getTimezone: () => this.timezone,
      getVolume: () => this.steleSettings.volume,
      onShown: (entry, ms) => this.onShown(entry, ms),
      onEmpty: (empty) => this.onEmpty(empty),
      onPlayed: (rec) => this.o.onPlayed && this.o.onPlayed(rec),
      onState: () => this.emitState(),
    });

    this.touch = new TouchMode({
      stageEl: this.stageEl,
      frame: this.frame,
      getInsets: () => {
        const p = this.shownPres || this.activePres;
        return p ? frameInsets(p.design, p.settings, null) : { top: 0, bottom: 0 };
      },
      getVolume: () => (this.o.muted ? 0 : this.steleSettings.volume),
      getSettings: () => (this.shownPres || this.activePres || { settings: { background: '#000000', default_duration_s: 7 } }).settings,
      onOpen: () => {
        this.show.pause();
        this.frame.setHidden(false, 250);
        this.frame.setTickerPaused(true);
        this.emitState();
      },
      onClose: () => {
        if (!this.userPaused && !this.night) this.show.play();
        const cur = this.show.current;
        this.frame.setHidden(Boolean(cur && cur.slide.fullscreen), 250);
        this.frame.setTickerPaused(false);
        this.emitState();
      },
      onEvent: (ev) => this.o.onTouchEvent && this.o.onTouchEvent(ev),
    });

    this.standby = new Standby(this.stageEl);
    this.nightScreen = new NightScreen(this.stageEl);

    // Antippen irgendwo (außer Diagnose-Ecke) öffnet das Touch-Menü.
    this.stageEl.addEventListener('click', (ev) => {
      if (!this.o.touchAllowed || this.touch.isOpen || this.night || this.empty) return;
      if (!this.touch.available) return;
      const p = this.stage.toStage(ev.clientX, ev.clientY);
      if (p.x <= 150 && p.y <= 150) return;
      this.touch.open();
    });

    if (!this.slideMode) {
      this.checkTimer = setInterval(() => this.evaluate(), TIMING.scheduleCheckMs);
    }
  }

  // ---------- Manifest ----------

  applyManifest(manifest, { immediate = false, startIndex = null } = {}) {
    this.manifest = manifest;
    registerFonts(manifest.fonts);
    this.timezone = validTimezone(manifest.timezone || DEFAULT_TIMEZONE);
    this.frame.setContext({ timezone: this.timezone, feeds: manifest.feeds || {} });
    this.standby.set({ orgName: manifest.org_name || '', timezone: this.timezone });
    const stele = manifest.stele || null;
    this.stage.setSize(stele ? stele.width : 1080, stele ? stele.height : 1920);
    this.steleSettings = normalizeSteleSettings(stele ? stele.settings : null);
    const showCursor = this.mode === 'normal' ? this.steleSettings.show_cursor : true;
    document.documentElement.classList.toggle('hide-cursor', !showCursor);
    this.evaluate({ immediate, startIndex });
  }

  // Vorschau: feste Präsentation statt Zeitplan.
  setFixedPresentation(id) { this.presentationId = id; }

  resolve() {
    const m = this.manifest;
    if (!m) return { presentation: null, source: 'none' };
    if (this.mode === 'preview') {
      const all = m.presentations || {};
      const raw = (this.presentationId != null && all[String(this.presentationId)])
        || (m.default_presentation_id != null && all[String(m.default_presentation_id)])
        || Object.values(all)[0] || null;
      return { presentation: raw, source: raw ? 'default' : 'none' };
    }
    return resolveActive(m, clock.now());
  }

  evaluate({ immediate = false, startIndex = null } = {}) {
    if (!this.manifest || this.slideMode) return;
    // Nachtmodus (nur mit Stelen-Einstellungen)
    const parts = wallParts(clock.now(), this.timezone);
    const night = this.mode !== 'preview' && nightModeActive(this.steleSettings.night_mode, parts);
    if (night !== this.night) {
      this.night = night;
      if (night) {
        this.touch.close();
        this.show.stop();
        this.activeSig = null;
        this.nightScreen.show(true);
        this.emitState();
        return;
      }
      this.nightScreen.show(false);
    }
    if (night) return;
    const { presentation, source } = this.resolve();
    this.source = source;
    const sig = presentation ? JSON.stringify(presentation) : null;
    if (sig === this.activeSig && this.show.pres) return;
    this.activeSig = sig;
    const prepared = presentation ? preparePresentation(presentation) : null;
    this.activePres = prepared;
    this.show.setPresentation(prepared, { immediate, startIndex });
  }

  // ---------- Rückmeldungen der Diashow ----------

  onShown(entry, ms) {
    const pres = entry.pres;
    if (pres !== this.shownPres) {
      this.shownPres = pres;
      this.frame.apply(pres.design, pres.settings);
      // Live-Ansicht zeigt das Hinweis-Element wie die Stele; Antippen öffnet nur mit touchAllowed.
      if (!this.slideMode) this.touch.setMenu(pres.touch, this.steleSettings.touch_enabled);
    }
    this.frame.setHidden(Boolean(entry.slide.fullscreen) && !this.touch.isOpen, ms || 300);
    this.touch.updateAttract(entry.caption, frameInsets(pres.design, pres.settings, null));
  }

  onEmpty(empty) {
    this.empty = empty;
    this.standby.show(empty && !this.slideMode);
    this.touch.setSuppressed(empty);
    if (empty) {
      if (this.touch.isOpen && !this.touch.persistent) this.touch.close();
      if (this.slideMode) this.frame.apply(null, {});
    }
    this.emitState();
  }

  // ---------- Einzelfolie (mode=slide) ----------

  render(msg) {
    registerFonts(msg.fonts);
    const settings = msg.settings && typeof msg.settings === 'object' ? msg.settings : {};
    const slide = msg.slide && typeof msg.slide === 'object' ? msg.slide : null;
    const pres = preparePresentation({
      id: null, name: '', settings, design: msg.design || null, touch_menu: msg.touch_menu || null,
      slides: slide ? [slide] : [],
    });
    this.activePres = pres;
    if (msg.view === 'touch') {
      this.frame.apply(pres.design, pres.settings);
      this.frame.setHidden(false, 0);
      this.shownPres = pres;
      if (pres.touch) {
        this.touch.setMenu(pres.touch, true);
        this.touch.open({ persistent: true });
      } else {
        this.touch.close();
        this.touch.setMenu(null, false);
      }
      if (slide) this.show.setPresentation(pres, { immediate: true, startIndex: 0 });
      this.emitState();
      return;
    }
    if (this.touch.isOpen) this.touch.close();
    this.touch.setMenu(null, false);
    if (!slide) {
      this.show.stop();
      this.frame.apply(pres.design, pres.settings);
      this.emitState();
      return;
    }
    this.show.setPresentation(pres, { immediate: true, startIndex: 0 });
    this.emitState();
  }

  // ---------- Steuerung (Admin-Nachrichten) ----------

  play() { this.userPaused = false; if (!this.touch.isOpen) this.show.play(); this.emitState(); }
  pause() { this.userPaused = true; this.show.pause(); this.emitState(); }

  openTouch() {
    const menu = (this.shownPres || this.activePres || {}).touch;
    if (!menu) return false;
    if (!this.touch.menu) this.touch.setMenu(menu, true);
    return this.touch.open({ persistent: this.mode === 'preview' });
  }

  closeTouch() { this.touch.close(); }

  // ---------- Zustand ----------

  get uiMode() {
    if (this.slideMode) return this.touch.isOpen ? 'touch' : 'slide';
    if (this.night) return 'standby';
    if (this.empty) return 'idle'; // läuft, aber nichts anzuzeigen (Standbild) – kein Nachtmodus
    if (this.touch.isOpen) return 'touch';
    return 'slideshow';
  }

  current() {
    const cur = this.show.current;
    if (!cur || this.night) return null;
    return {
      presentation_id: cur.pres.id,
      item_id: cur.slide.id ?? null,
      content_id: cur.slide.content_id ?? null,
      title: String(cur.slide.title || ''),
      started_at: cur.startedAt ? new Date(cur.startedAt).toISOString().replace(/\.\d{3}Z$/, 'Z') : null,
    };
  }

  emitState() {
    if (this.stateQueued) return;
    this.stateQueued = true;
    queueMicrotask(() => {
      this.stateQueued = false;
      if (this.o.onState) this.o.onState();
    });
  }
}
