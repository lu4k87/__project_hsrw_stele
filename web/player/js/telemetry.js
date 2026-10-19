// Heartbeat alle 15 s mit Zustand, neuen Fehlern, Wiedergabe-/Touch-Protokoll und Befehlsergebnissen
// (SPEC §9.7/§9.8). Bei Netzfehlern bleiben die Einträge in begrenzten Warteschlangen und gehen beim
// nächsten erfolgreichen Heartbeat mit. Befehlsergebnisse überleben einen Neustart (localStorage).

import { TIMING, PLAYER_VERSION } from './config.js';
import { store } from './store.js';

const LIMITS = { errors: 100, played: 500, touch: 500, results: 50 };
const PER_BEAT = { errors: 20, played: 200, touch: 200, results: 50 };

// Laufende Nummer je Eintrag (außerhalb des Eintrags: nicht im Heartbeat, nicht im localStorage). Nach einem
// erfolgreichen Heartbeat werden nur Einträge bis zur höchsten gesendeten Nummer entfernt – läuft die Warteschlange
// währenddessen über (älteste fallen vorn heraus), gehen so keine ungesendeten verloren.
const seqOf = new WeakMap();
let nextSeq = 1;

function tag(item) {
  if (!seqOf.has(item)) seqOf.set(item, nextSeq++);
  return item;
}

function pushBounded(list, item, max) {
  list.push(tag(item));
  if (list.length > max) list.splice(0, list.length - max);
}

function removeSent(list, sent) {
  if (!sent.length) return;
  const maxSeq = seqOf.get(sent[sent.length - 1]);
  let n = 0;
  while (n < list.length && seqOf.get(list[n]) <= maxSeq) n++;
  list.splice(0, n);
}

const loadQueue = (key) => (store.get(key, []) || []).filter((x) => x && typeof x === 'object').map(tag);

export class Telemetry {
  // opts: api, getState() → {manifest_version, mode, current}, onResponse(data, rttMs), onAuthError()
  constructor(opts) {
    this.o = opts;
    this.q = {
      errors: [],
      played: loadQueue('pendingPlayed'),
      touch: loadQueue('pendingTouch'),
      results: loadQueue('pendingResults'),
    };
    // Statistik aus der Zeit vor dem Neustart ist jetzt im Speicher; gespeicherte Kopie löschen (sonst doppelt)
    store.remove('pendingPlayed');
    store.remove('pendingTouch');
    this.bootedAt = performance.now();
    this.timer = null;
    this.inFlight = false;
    this.online = null;
    this.lastOkAt = 0;
  }

  error(entry) { pushBounded(this.q.errors, entry, LIMITS.errors); }
  played(rec) { pushBounded(this.q.played, rec, LIMITS.played); }
  touch(ev) { pushBounded(this.q.touch, ev, LIMITS.touch); }

  result(id, ok, message = '') {
    pushBounded(this.q.results, { id, ok: Boolean(ok), message: String(message) }, LIMITS.results);
    store.set('pendingResults', this.q.results);
  }

  // Vor einem Neustart: offene Ergebnisse und Statistik sichern (werden nach dem Laden gemeldet).
  persist() {
    store.set('pendingResults', this.q.results);
    store.set('pendingPlayed', this.q.played);
    store.set('pendingTouch', this.q.touch);
  }

  start() {
    if (this.timer) return;
    this.beat();
    this.timer = setInterval(() => this.beat(), TIMING.heartbeatMs);
  }

  uptimeS() { return Math.round((performance.now() - this.bootedAt) / 1000); }

  async beat() {
    if (this.inFlight) return;
    this.inFlight = true;
    const take = {};
    for (const k of Object.keys(PER_BEAT)) take[k] = this.q[k].slice(0, PER_BEAT[k]);
    const s = this.o.getState();
    const body = {
      player_version: PLAYER_VERSION,
      manifest_version: s.manifest_version || '',
      mode: s.mode,
      current: s.current || null,
      screen: { w: window.screen.width, h: window.screen.height },
      user_agent: navigator.userAgent,
      uptime_s: this.uptimeS(),
      errors: take.errors,
      played: take.played,
      touch: take.touch,
      command_results: take.results,
    };
    const t0 = performance.now();
    try {
      const res = await this.o.api.post('/api/player/heartbeat', body, { timeoutMs: 10_000 });
      // Erfolgreich übertragene Einträge entfernen (neue sind inzwischen hinten angehängt, älteste evtl. verdrängt).
      for (const k of Object.keys(take)) removeSent(this.q[k], take[k]);
      store.set('pendingResults', this.q.results);
      this.online = true;
      this.lastOkAt = Date.now();
      if (this.o.onResponse) this.o.onResponse(res.data || {}, performance.now() - t0);
    } catch (err) {
      this.online = false;
      if (err && (err.status === 401 || err.status === 403) && this.o.onAuthError) this.o.onAuthError(err.status);
    } finally {
      this.inFlight = false;
    }
  }
}
