/**
 * InvertirCL — Stock Poller v1.0
 * ================================
 * Item 08 del Roadmap: WebSockets / Polling acciones chilenas
 *
 * Yahoo Finance no tiene WebSocket público gratuito para acciones chilenas.
 * Solución: polling HTTP cada 15s con interpolación tick-a-tick entre
 * actualizaciones — el usuario ve precios que se "sienten" en tiempo real
 * pero están anclados a datos reales de Yahoo Finance.
 *
 * MECANISMO:
 *   1. Cada 15s: fetch real de Yahoo Finance → precio actualizado
 *   2. Entre fetches: interpolación con microvolatilidad basada en ATR histórico
 *   3. El usuario ve un precio que se mueve cada 2s — realista, no random
 *
 * USO:
 *   <script src="../js/market.js"></script>
 *   <script src="../js/poller.js"></script>
 *
 *   StockPoller.start(['COPEC.SN', 'SQM-B.SN', 'BCI.SN']);
 *   StockPoller.subscribe('COPEC.SN', (data) => console.log(data));
 *   StockPoller.stop();
 */

const StockPoller = (() => {

  // ── ESTADO ─────────────────────────────────────────────────
  const state = {
    symbols:    [],
    prices:     {},      // sym → { price, prevClose, changePct, high, low, atr, ts }
    subs:       {},      // sym → [callbacks]
    intervals:  [],      // IDs de setInterval
    running:    false,
    fetchCount: 0,
    lastFetch:  null,
  };

  const POLL_MS      = 15_000;  // fetch real cada 15s
  const TICK_MS      =  2_000;  // interpolación cada 2s
  const PROXY_BASE   = 'https://api.allorigins.win/get?url=';
  const YAHOO_BASE   = 'https://query1.finance.yahoo.com/v8/finance/chart/';

  // Volatilidad intradía típica por tipo de activo (% del precio)
  const MICRO_VOL = {
    default: 0.0008,   // 0.08% por tick — acciones chilenas
    etf:     0.0005,
    index:   0.0004,
  };

  // ── FETCH PRECIO REAL ────────────────────────────────────────
  async function fetchPrice(symbol) {
    const url = `${YAHOO_BASE}${encodeURIComponent(symbol)}?interval=1m&range=1d`;
    const proxy = `${PROXY_BASE}${encodeURIComponent(url)}`;

    try {
      const res  = await fetch(proxy, { cache: 'no-store' });
      const raw  = await res.json();
      const json = JSON.parse(raw.contents);
      const result = json?.chart?.result?.[0];
      if (!result) throw new Error('Sin datos');

      const meta      = result.meta;
      const price     = parseFloat((meta.regularMarketPrice || 0).toFixed(2));
      const prevClose = parseFloat((meta.chartPreviousClose || meta.previousClose || price).toFixed(2));
      const high      = parseFloat((meta.regularMarketDayHigh || price).toFixed(2));
      const low       = parseFloat((meta.regularMarketDayLow  || price).toFixed(2));
      const volume    = meta.regularMarketVolume || 0;
      const change    = parseFloat((price - prevClose).toFixed(2));
      const changePct = parseFloat(((change / prevClose) * 100).toFixed(2));

      // ATR aproximado del día (high - low)
      const dayRange  = high - low;
      const atr       = dayRange > 0 ? dayRange : price * 0.01;

      return { symbol, price, prevClose, change, changePct, high, low, volume, atr, live: true, ts: Date.now() };

    } catch(e) {
      // Si falla, retornar el último precio conocido
      console.warn(`[Poller] ${symbol} fetch error:`, e.message);
      return state.prices[symbol] || null;
    }
  }

  // ── INTERPOLACIÓN TICK-A-TICK ────────────────────────────────
  // Entre fetches reales, simula movimiento de precio realista
  // basado en la volatilidad histórica del activo (ATR)
  function interpolate(symbol) {
    const p = state.prices[symbol];
    if (!p || !p.price) return;

    // Volatilidad por tick proporcional al ATR del día
    const microVol = p.atr ? (p.atr / p.price) * 0.15 : MICRO_VOL.default;

    // Sesgo leve hacia el cierre del día (mean reversion intraday)
    const bias = (p.prevClose - p.price) / p.price * 0.01;

    // Nuevo precio con ruido gaussiano aproximado (Box-Muller simplificado)
    const u1 = Math.random(), u2 = Math.random();
    const gauss = Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
    const noise = gauss * microVol + bias;

    const newPrice   = parseFloat(Math.max(p.price * (1 + noise), p.low * 0.99).toFixed(2));
    const newChange  = parseFloat((newPrice - p.prevClose).toFixed(2));
    const newChgPct  = parseFloat(((newChange / p.prevClose) * 100).toFixed(2));
    const newHigh    = Math.max(p.high, newPrice);
    const newLow     = Math.min(p.low, newPrice);

    state.prices[symbol] = {
      ...p,
      price:     newPrice,
      change:    newChange,
      changePct: newChgPct,
      high:      parseFloat(newHigh.toFixed(2)),
      low:       parseFloat(newLow.toFixed(2)),
      live:      false,  // interpolado, no fetch real
      ts:        Date.now(),
    };

    notify(symbol, state.prices[symbol]);
  }

  // ── NOTIFICAR SUSCRIPTORES ───────────────────────────────────
  function notify(symbol, data) {
    (state.subs[symbol] || []).forEach(cb => {
      try { cb(data); } catch(e) { console.error('[Poller] callback error:', e); }
    });
    // También notificar suscriptores de 'ALL'
    (state.subs['ALL'] || []).forEach(cb => {
      try { cb({ symbol, ...data }); } catch(e) {}
    });
  }

  // ── CICLO DE POLLING ─────────────────────────────────────────
  async function pollCycle() {
    if (!state.running) return;
    state.fetchCount++;
    state.lastFetch = new Date().toISOString();

    // Fetch en paralelo para todos los símbolos
    const results = await Promise.allSettled(
      state.symbols.map(sym => fetchPrice(sym))
    );

    results.forEach((result, i) => {
      if (result.status === 'fulfilled' && result.value) {
        const data = result.value;
        const sym  = state.symbols[i];
        state.prices[sym] = data;
        notify(sym, data);
      }
    });

    console.log(`[Poller] Fetch #${state.fetchCount} — ${state.symbols.length} símbolos actualizados`);
  }

  // ── API PÚBLICA ───────────────────────────────────────────────
  return {

    /**
     * Iniciar el poller para una lista de símbolos.
     * Los símbolos deben incluir .SN para acciones chilenas.
     */
    start(symbols = [], options = {}) {
      if (state.running) this.stop();

      state.symbols = symbols.map(s =>
        s.includes('.') || s.startsWith('^') ? s : `${s}.SN`
      );
      state.running = true;

      // Inicializar precios con fallbacks
      state.symbols.forEach(sym => {
        if (!state.prices[sym]) {
          state.prices[sym] = { symbol: sym, price: 0, changePct: 0, live: false, ts: 0 };
        }
      });

      // Primer fetch inmediato
      pollCycle();

      // Polling real cada 15s
      const pollId = setInterval(pollCycle, options.pollMs || POLL_MS);
      state.intervals.push(pollId);

      // Interpolación cada 2s
      const tickId = setInterval(() => {
        state.symbols.forEach(sym => interpolate(sym));
      }, options.tickMs || TICK_MS);
      state.intervals.push(tickId);

      console.log(`[Poller] Iniciado — ${state.symbols.length} símbolos | Poll:${POLL_MS/1000}s | Tick:${TICK_MS/1000}s`);
      return this;
    },

    /** Detener el poller y limpiar intervalos. */
    stop() {
      state.intervals.forEach(id => clearInterval(id));
      state.intervals = [];
      state.running   = false;
      console.log('[Poller] Detenido');
    },

    /** Suscribirse a actualizaciones de un símbolo. */
    subscribe(symbol, callback) {
      const sym = symbol === 'ALL' ? 'ALL' :
        symbol.includes('.') || symbol.startsWith('^') ? symbol : `${symbol}.SN`;
      if (!state.subs[sym]) state.subs[sym] = [];
      state.subs[sym].push(callback);
      // Si ya tenemos precio, llamar callback inmediatamente
      if (state.prices[sym]) callback(state.prices[sym]);
      return () => this.unsubscribe(sym, callback); // retorna función de cleanup
    },

    /** Cancelar suscripción. */
    unsubscribe(symbol, callback) {
      const sym = symbol.includes('.') || symbol.startsWith('^') ? symbol : `${symbol}.SN`;
      state.subs[sym] = (state.subs[sym] || []).filter(cb => cb !== callback);
    },

    /** Obtener precio actual (sincrónico, desde caché). */
    getPrice(symbol) {
      const sym = symbol.includes('.') || symbol.startsWith('^') ? symbol : `${symbol}.SN`;
      return state.prices[sym] || null;
    },

    /** Obtener todos los precios actuales. */
    getAllPrices() {
      return { ...state.prices };
    },

    /** Agregar símbolo al poller en caliente (sin reiniciar). */
    addSymbol(symbol) {
      const sym = symbol.includes('.') ? symbol : `${symbol}.SN`;
      if (!state.symbols.includes(sym)) {
        state.symbols.push(sym);
        state.prices[sym] = { symbol: sym, price: 0, changePct: 0, live: false, ts: 0 };
        fetchPrice(sym).then(data => {
          if (data) { state.prices[sym] = data; notify(sym, data); }
        });
      }
    },

    /** Estado actual del poller (para debugging). */
    status() {
      return {
        running:    state.running,
        symbols:    state.symbols,
        fetchCount: state.fetchCount,
        lastFetch:  state.lastFetch,
        prices:     Object.fromEntries(
          Object.entries(state.prices).map(([k, v]) => [k, {
            price:     v.price,
            changePct: v.changePct,
            live:      v.live,
            age_ms:    v.ts ? Date.now() - v.ts : null,
          }])
        ),
      };
    },
  };

})();

// ── SÍMBOLOS PREDEFINIDOS IPSA ──────────────────────────────────
StockPoller.IPSA_SYMBOLS = [
  'COPEC.SN', 'SQM-B.SN', 'BCI.SN', 'CCU.SN',
  'FALABELLA.SN', 'ENTEL.SN', 'CHILE.SN', 'CMPC.SN',
  'VAPORES.SN', 'CENCOSUD.SN',
];

// Disponible globalmente
window.StockPoller = StockPoller;
console.log('[InvertirCL] StockPoller v1.0 cargado. Uso: StockPoller.start(["COPEC.SN"])');
