/**
 * InvertirCL — Market Data Module v1.0 (Browser JS)
 * ===================================================
 * Equivalente browser de market.py
 *
 * FUENTES DE DATOS (todas gratuitas):
 *   mindicador.cl  → UF, dólar observado, IPC, TPM, cobre, euro
 *   DolarApi.com   → USD/CLP en tiempo real
 *   Yahoo Finance  → IPSA (^IPSA) + acciones chilenas (.SN) — via proxy
 *
 * USO:
 *   <script src="JS/market.js"></script>
 *
 *   const usd  = await Market.getUSDCLP();
 *   const uf   = await Market.getUF();
 *   const ipsa = await Market.getIPSA();
 *   const accion = await Market.getAccion('COPEC');
 *   const hist = await Market.getHistorico('^IPSA', '1mo', '1d');
 *   const rsi  = Market.Indicators.RSI(closes, 14);
 *   const costos = Market.MatchingEngine.costos(8420, 100, 'buy');
 *   const all  = await Market.getAll();
 */

const Market = (() => {

  // ─────────────────────────────────────────────────────────
  // CONFIGURACIÓN
  // ─────────────────────────────────────────────────────────
  const PROXY       = 'https://api.allorigins.win/get?url=';
  const YAHOO_BASE  = 'https://query1.finance.yahoo.com/v8/finance/chart/';
  const MINI_BASE   = 'https://mindicador.cl/api';
  const DOLAR_BASE  = 'https://cl.dolarapi.com/v1/cotizaciones/usd';

  const TTL = {
    usdclp:      30_000,    // 30s — cambia frecuente
    mindicador:  3_600_000, // 1h  — valores diarios
    ipsa:        60_000,    // 1min
    accion:      60_000,
    historico:   3_600_000, // 1h
  };

  // Fallbacks hardcoded — si todas las APIs fallan, el dashboard
  // igual muestra algo coherente en vez de romperse
  const FALLBACKS = {
    usdclp: { compra: 927.0, venta: 930.0, promedio: 928.5, fuente: 'fallback', live: false },
    uf:     { valor: 38741.0, unidad: 'CLP', fuente: 'fallback' },
    ipc:    { valor: 0.4,     unidad: '%',   fuente: 'fallback' },
    tpm:    { valor: 5.0,     unidad: '%',   fuente: 'fallback' },
    cobre:  { valor: 4.82,    unidad: 'USD/lb', fuente: 'fallback' },
    euro:   { valor: 1018.0,  unidad: 'CLP', fuente: 'fallback' },
    ipsa:   { symbol: '^IPSA', price: 7284.0, changePct: 0.0, fuente: 'fallback', live: false },
  };

  // ─────────────────────────────────────────────────────────
  // CACHÉ INTERNA
  // ─────────────────────────────────────────────────────────
  const _cache = {};

  function _cacheGet(key) {
    const item = _cache[key];
    if (!item) return null;
    if (Date.now() - item.ts > item.ttl) return null; // expirado
    return item.value;
  }

  function _cacheSet(key, value, ttl) {
    _cache[key] = { value, ts: Date.now(), ttl };
  }

  // ─────────────────────────────────────────────────────────
  // FETCH HELPER
  // ─────────────────────────────────────────────────────────
  async function _fetch(url, useProxy = false) {
    const target = useProxy
      ? `${PROXY}${encodeURIComponent(url)}`
      : url;
    const res = await fetch(target, { cache: 'no-store' });
    if (!res.ok) throw new Error(`HTTP ${res.status} — ${url}`);
    const raw = await res.json();
    // allorigins envuelve la respuesta en { contents: "..." }
    return useProxy ? JSON.parse(raw.contents) : raw;
  }

  // ─────────────────────────────────────────────────────────
  // INDICADORES TÉCNICOS
  // Portados directamente desde market.py → misma lógica,
  // misma precisión al cuarto decimal
  // ─────────────────────────────────────────────────────────
  const Indicators = {

    /**
     * RSI(n) — Relative Strength Index
     * closes: array de precios de cierre
     * Retorna número 0-100 o null si no hay suficientes datos
     */
    RSI(closes, n = 14) {
      if (closes.length < n + 1) return null;
      let gains = 0, losses = 0;

      for (let i = 1; i <= n; i++) {
        const diff = closes[i] - closes[i - 1];
        if (diff > 0) gains  += diff;
        else          losses -= diff;
      }
      let avgGain = gains  / n;
      let avgLoss = losses / n;

      for (let i = n + 1; i < closes.length; i++) {
        const diff = closes[i] - closes[i - 1];
        avgGain = (avgGain * (n - 1) + Math.max(diff, 0)) / n;
        avgLoss = (avgLoss * (n - 1) + Math.max(-diff, 0)) / n;
      }
      if (avgLoss === 0) return 100;
      const rs = avgGain / avgLoss;
      return +( 100 - 100 / (1 + rs) ).toFixed(4);
    },

    /**
     * EMA(n) — Exponential Moving Average
     * Retorna el último valor o null
     */
    EMA(closes, n) {
      if (closes.length < n) return null;
      const k = 2 / (n + 1);
      let ema = closes.slice(0, n).reduce((a, b) => a + b, 0) / n;
      for (let i = n; i < closes.length; i++) {
        ema = closes[i] * k + ema * (1 - k);
      }
      return +ema.toFixed(4);
    },

    /**
     * SMA(n) — Simple Moving Average
     */
    SMA(closes, n) {
      if (closes.length < n) return null;
      const slice = closes.slice(-n);
      return +(slice.reduce((a, b) => a + b, 0) / n).toFixed(4);
    },

    /**
     * MACD(fast, slow, signal)
     * Retorna { macd, signal, histogram } o null
     */
    MACD(closes, fast = 12, slow = 26, signal = 9) {
      if (closes.length < slow + signal) return null;
      const kFast = 2 / (fast + 1);
      const kSlow = 2 / (slow + 1);

      let emaFast = closes.slice(0, fast).reduce((a, b) => a + b, 0) / fast;
      let emaSlow = closes.slice(0, slow).reduce((a, b) => a + b, 0) / slow;
      const macdLine = [];

      for (let i = slow; i < closes.length; i++) {
        if (i >= fast) emaFast = closes[i] * kFast + emaFast * (1 - kFast);
        emaSlow = closes[i] * kSlow + emaSlow * (1 - kSlow);
        macdLine.push(emaFast - emaSlow);
      }

      const kSig = 2 / (signal + 1);
      let sigVal = macdLine.slice(0, signal).reduce((a, b) => a + b, 0) / signal;
      for (let i = signal; i < macdLine.length; i++) {
        sigVal = macdLine[i] * kSig + sigVal * (1 - kSig);
      }

      const macdVal = +macdLine[macdLine.length - 1].toFixed(4);
      sigVal = +sigVal.toFixed(4);
      return { macd: macdVal, signal: sigVal, histogram: +(macdVal - sigVal).toFixed(4) };
    },

    /**
     * BB(n, std) — Bandas de Bollinger
     * Retorna { upper, middle, lower, width } o null
     */
    BB(closes, n = 20, stdMult = 2) {
      if (closes.length < n) return null;
      const slice = closes.slice(-n);
      const sma   = slice.reduce((a, b) => a + b, 0) / n;
      const variance = slice.reduce((acc, v) => acc + (v - sma) ** 2, 0) / n;
      const sigma = Math.sqrt(variance);
      return {
        upper:  +(sma + stdMult * sigma).toFixed(4),
        middle: +sma.toFixed(4),
        lower:  +(sma - stdMult * sigma).toFixed(4),
        width:  +(4 * stdMult * sigma / sma * 100).toFixed(4),
      };
    },

    /**
     * ATR(n) — Average True Range
     * candles: array de { high, low, close }
     */
    ATR(candles, n = 14) {
      if (candles.length < n + 1) return null;
      const trs = [];
      for (let i = 1; i < candles.length; i++) {
        const c = candles[i], p = candles[i - 1];
        trs.push(Math.max(
          c.high - c.low,
          Math.abs(c.high - p.close),
          Math.abs(c.low  - p.close),
        ));
      }
      let atr = trs.slice(0, n).reduce((a, b) => a + b, 0) / n;
      for (let i = n; i < trs.length; i++) {
        atr = (atr * (n - 1) + trs[i]) / n;
      }
      return +atr.toFixed(4);
    },

    /**
     * VWAP — Volume Weighted Average Price
     * candles: array de { high, low, close, volume }
     */
    VWAP(candles) {
      let cumPV = 0, cumVol = 0;
      for (const c of candles) {
        const tp = (c.high + c.low + c.close) / 3;
        cumPV  += tp * c.volume;
        cumVol += c.volume;
      }
      if (cumVol === 0) return null;
      return +(cumPV / cumVol).toFixed(4);
    },

    /**
     * Sharpe Ratio anualizado
     * retornos: array de retornos diarios (decimales, ej: 0.01 = 1%)
     */
    Sharpe(retornos, tasaLibreRiesgo = 0.05, periodos = 252) {
      if (retornos.length < 2) return null;
      const mean = retornos.reduce((a, b) => a + b, 0) / retornos.length;
      const variance = retornos.reduce((acc, r) => acc + (r - mean) ** 2, 0) / retornos.length;
      const std = Math.sqrt(variance);
      if (std === 0) return null;
      const exc = mean - tasaLibreRiesgo / periodos;
      return +(exc / std * Math.sqrt(periodos)).toFixed(4);
    },

    /**
     * Sortino Ratio — solo penaliza volatilidad negativa
     */
    Sortino(retornos, tasaLibreRiesgo = 0.05, periodos = 252) {
      if (retornos.length < 2) return null;
      const mean = retornos.reduce((a, b) => a + b, 0) / retornos.length;
      const neg  = retornos.filter(r => r < 0);
      if (neg.length === 0) return null;
      const downVar = neg.reduce((acc, r) => acc + r ** 2, 0) / neg.length;
      const downStd = Math.sqrt(downVar);
      if (downStd === 0) return null;
      const exc = mean - tasaLibreRiesgo / periodos;
      return +(exc / downStd * Math.sqrt(periodos)).toFixed(4);
    },

    /**
     * Max Drawdown — caída máxima desde el pico (%)
     * equityCurve: array de valores del portafolio
     */
    MaxDrawdown(equityCurve) {
      let peak = equityCurve[0], maxDD = 0;
      for (const val of equityCurve) {
        if (val > peak) peak = val;
        const dd = (peak - val) / peak * 100;
        if (dd > maxDD) maxDD = dd;
      }
      return +maxDD.toFixed(4);
    },

    /**
     * Profit Factor = suma ganancias / suma pérdidas absolutas
     * trades: array de { pnl: number }
     */
    ProfitFactor(trades) {
      const ganancias = trades.filter(t => t.pnl > 0).reduce((a, t) => a + t.pnl, 0);
      const perdidas  = trades.filter(t => t.pnl < 0).reduce((a, t) => a - t.pnl, 0);
      if (perdidas === 0) return null;
      return +(ganancias / perdidas).toFixed(4);
    },

    /**
     * WinRate — porcentaje de trades ganadores (0-100)
     */
    WinRate(trades) {
      if (trades.length === 0) return null;
      const winners = trades.filter(t => t.pnl > 0).length;
      return +(winners / trades.length * 100).toFixed(2);
    },

    /**
     * Calmar Ratio = retorno anualizado / Max Drawdown
     */
    Calmar(retornoAnual, maxDrawdown) {
      if (maxDrawdown === 0) return null;
      return +(retornoAnual / maxDrawdown).toFixed(4);
    },
  };

  // ─────────────────────────────────────────────────────────
  // MATCHING ENGINE
  // Simula ejecución real con slippage y comisiones
  // Portado desde market.py → misma lógica exacta
  // ─────────────────────────────────────────────────────────
  const MatchingEngine = {

    DEFAULT_CONFIG: {
      slippageBps:  5,    // 5 basis points
      comisionPct:  0.1,  // 0.1% comisión corredor
      spreadPct:    0.02, // 0.02% spread bid-ask
    },

    /**
     * Precio real de ejecución incluyendo slippage y spread
     */
    precioEjecucion(precio, side, config = {}) {
      const c        = { ...this.DEFAULT_CONFIG, ...config };
      const slippage = precio * c.slippageBps / 10_000;
      const halfSpread = precio * c.spreadPct / 2 / 100;
      if (side === 'buy')  return +(precio + slippage + halfSpread).toFixed(2);
      if (side === 'sell') return +(precio - slippage - halfSpread).toFixed(2);
      throw new Error(`side debe ser 'buy' o 'sell', recibido: ${side}`);
    },

    /**
     * Desglose de costos de una operación
     * Retorna: { precioSolicitado, precioEjecucion, cantidad,
     *            montoTotal, comision, slippage, costoTotal }
     */
    costos(precio, cantidad, side, config = {}) {
      const c          = { ...this.DEFAULT_CONFIG, ...config };
      const precioEjec = this.precioEjecucion(precio, side, config);
      const montoTotal = precioEjec * cantidad;
      const comision   = montoTotal * c.comisionPct / 100;
      const slippageM  = Math.abs(precioEjec - precio) * cantidad;
      return {
        precioSolicitado: precio,
        precioEjecucion:  precioEjec,
        cantidad,
        montoTotal:  Math.round(montoTotal),
        comision:    Math.round(comision),
        slippage:    Math.round(slippageM),
        costoTotal:  Math.round(comision + slippageM),
      };
    },

    /**
     * Ejecutar una orden simulada
     * orden: { symbol, side, cantidad, tipo, limitPrice?, stopPrice? }
     * Retorna: { estado, ... } donde estado es 'filled' | 'pending'
     */
    ejecutar(orden, precioMercado) {
      const { side, tipo, cantidad, symbol = '' } = orden;
      let precioEjec = null;
      let estado     = 'pending';

      if (tipo === 'market') {
        precioEjec = this.precioEjecucion(precioMercado, side);
        estado     = 'filled';
      } else if (tipo === 'limit') {
        const lp = orden.limitPrice || 0;
        if (side === 'buy'  && precioMercado <= lp) { precioEjec = lp; estado = 'filled'; }
        if (side === 'sell' && precioMercado >= lp) { precioEjec = lp; estado = 'filled'; }
      } else if (tipo === 'stop') {
        const sp = orden.stopPrice || 0;
        if (side === 'sell' && precioMercado <= sp) {
          // Slippage extra en stops — el mercado se mueve en tu contra
          const extra = sp * 0.001 * (1 + Math.random());
          precioEjec  = +(sp - extra).toFixed(2);
          estado      = 'filled';
        }
      }

      if (estado !== 'filled') return { estado, orden };

      const c = this.costos(precioEjec, cantidad, side);
      return {
        estado:            'filled',
        symbol,
        side,
        cantidad,
        precioSolicitado:  precioMercado,
        precioEjecucion:   c.precioEjecucion,
        montoTotal:        c.montoTotal,
        comision:          c.comision,
        slippage:          c.slippage,
        costoTotal:        c.costoTotal,
        timestamp:         new Date().toISOString(),
      };
    },
  };

  // ─────────────────────────────────────────────────────────
  // MÉTRICAS DE PERFORMANCE
  // Para el dashboard y el panel de estadísticas del simulador
  // ─────────────────────────────────────────────────────────
  const Performance = {

    /**
     * Calcula todas las métricas de una sesión de trading
     * trades: array de { pnl, timestamp_entrada, timestamp_cierre }
     * capitalInicial: número
     */
    calcular(trades, capitalInicial = 10_000_000) {
      const cerrados = trades.filter(t => t.pnl !== null && t.pnl !== undefined);
      if (cerrados.length === 0) return null;

      const pnlTotal   = cerrados.reduce((a, t) => a + t.pnl, 0);
      const retornoPct = pnlTotal / capitalInicial * 100;

      // Curva de capital para Max Drawdown
      let capital = capitalInicial;
      const equityCurve = [capital];
      for (const t of cerrados) {
        capital += t.pnl;
        equityCurve.push(capital);
      }

      // Retornos diarios para Sharpe/Sortino
      const retornos = cerrados.map(t => t.pnl / capitalInicial);

      return {
        trades:       cerrados.length,
        winRate:      Indicators.WinRate(cerrados),
        profitFactor: Indicators.ProfitFactor(cerrados),
        maxDrawdown:  Indicators.MaxDrawdown(equityCurve),
        sharpe:       Indicators.Sharpe(retornos),
        sortino:      Indicators.Sortino(retornos),
        pnlTotal:     Math.round(pnlTotal),
        retornoPct:   +retornoPct.toFixed(2),
        capitalFinal: Math.round(capital),
      };
    },
  };

  // ─────────────────────────────────────────────────────────
  // API PÚBLICA — DATOS DE MERCADO
  // ─────────────────────────────────────────────────────────

  /**
   * 1. USD/CLP — DolarApi.com
   * Más actualizado que mindicador.cl para el tipo de cambio
   */
  async function getUSDCLP() {
    const cached = _cacheGet('usdclp');
    if (cached) return cached;
    try {
      const data  = await _fetch(DOLAR_BASE);
      const value = {
        compra:   data.compra,
        venta:    data.venta,
        promedio: +((data.compra + data.venta) / 2).toFixed(2),
        fecha:    data.fechaActualizacion || null,
        fuente:   'DolarApi.com',
        live:     true,
      };
      _cacheSet('usdclp', value, TTL.usdclp);
      return value;
    } catch (e) {
      console.warn('[Market] USD/CLP fallback:', e.message);
      return FALLBACKS.usdclp;
    }
  }

  /**
   * 2. Indicadores mindicador.cl
   * Un solo fetch, todos los indicadores — eficiente
   */
  let _miniPromise = null; // evita requests paralelos duplicados

  async function _fetchMindicador() {
    const cached = _cacheGet('mindicador');
    if (cached) return cached;

    // Si ya hay un fetch en vuelo, esperamos ese en vez de lanzar otro
    if (_miniPromise) return _miniPromise;

    _miniPromise = (async () => {
      try {
        const data   = await _fetch(MINI_BASE);
        const result = {
          uf:    { valor: data.uf?.valor,          fecha: data.uf?.fecha,          unidad: 'CLP',    fuente: 'mindicador.cl' },
          ipc:   { valor: data.ipc?.valor,         fecha: data.ipc?.fecha,         unidad: '%',      fuente: 'mindicador.cl' },
          tpm:   { valor: data.tpm?.valor,         fecha: data.tpm?.fecha,         unidad: '%',      fuente: 'mindicador.cl' },
          cobre: { valor: data.libra_cobre?.valor, fecha: data.libra_cobre?.fecha, unidad: 'USD/lb', fuente: 'mindicador.cl' },
          euro:  { valor: data.euro?.valor,        fecha: data.euro?.fecha,        unidad: 'CLP',    fuente: 'mindicador.cl' },
        };
        _cacheSet('mindicador', result, TTL.mindicador);
        return result;
      } catch (e) {
        console.warn('[Market] mindicador.cl fallback:', e.message);
        return {
          uf:    FALLBACKS.uf,
          ipc:   FALLBACKS.ipc,
          tpm:   FALLBACKS.tpm,
          cobre: FALLBACKS.cobre,
          euro:  FALLBACKS.euro,
        };
      } finally {
        _miniPromise = null;
      }
    })();

    return _miniPromise;
  }

  async function getUF()    { return (await _fetchMindicador()).uf    ?? FALLBACKS.uf; }
  async function getIPC()   { return (await _fetchMindicador()).ipc   ?? FALLBACKS.ipc; }
  async function getTPM()   { return (await _fetchMindicador()).tpm   ?? FALLBACKS.tpm; }
  async function getCobre() { return (await _fetchMindicador()).cobre ?? FALLBACKS.cobre; }
  async function getEuro()  { return (await _fetchMindicador()).euro  ?? FALLBACKS.euro; }

  /**
   * 3. IPSA — Yahoo Finance via proxy
   */
  async function getIPSA() {
    const cached = _cacheGet('ipsa');
    if (cached) return cached;
    try {
      const url  = `${YAHOO_BASE}${encodeURIComponent('^IPSA')}?interval=1m&range=1d`;
      const json = await _fetch(url, true);
      const meta = json?.chart?.result?.[0]?.meta;
      if (!meta) throw new Error('Sin datos IPSA');

      const price     = +parseFloat(meta.regularMarketPrice).toFixed(2);
      const prevClose = +parseFloat(meta.chartPreviousClose || meta.previousClose || price).toFixed(2);
      const change    = +(price - prevClose).toFixed(2);
      const changePct = +((change / prevClose) * 100).toFixed(2);

      const value = {
        symbol:    '^IPSA',
        price,
        prevClose,
        change,
        changePct,
        high:      +parseFloat(meta.regularMarketDayHigh || price).toFixed(2),
        low:       +parseFloat(meta.regularMarketDayLow  || price).toFixed(2),
        fuente:    'Yahoo Finance (15min delay)',
        live:      false,
      };
      _cacheSet('ipsa', value, TTL.ipsa);
      return value;
    } catch (e) {
      console.warn('[Market] IPSA fallback:', e.message);
      return FALLBACKS.ipsa;
    }
  }

  /**
   * 4. Acción chilena por nemotécnico
   * getAccion('COPEC') → busca COPEC.SN en Yahoo Finance
   */
  async function getAccion(nemo) {
    const symbol   = nemo.includes('.') ? nemo : `${nemo}.SN`;
    const cacheKey = `accion_${symbol}`;
    const cached   = _cacheGet(cacheKey);
    if (cached) return cached;
    try {
      const url  = `${YAHOO_BASE}${encodeURIComponent(symbol)}?interval=1m&range=1d`;
      const json = await _fetch(url, true);
      const meta = json?.chart?.result?.[0]?.meta;
      if (!meta) throw new Error(`Sin datos para ${symbol}`);

      const price     = +parseFloat(meta.regularMarketPrice).toFixed(2);
      const prevClose = +parseFloat(meta.chartPreviousClose || meta.previousClose || price).toFixed(2);
      const change    = +(price - prevClose).toFixed(2);
      const changePct = +((change / prevClose) * 100).toFixed(2);

      const value = {
        symbol,
        nemo,
        price,
        prevClose,
        change,
        changePct,
        high:   +parseFloat(meta.regularMarketDayHigh || price).toFixed(2),
        low:    +parseFloat(meta.regularMarketDayLow  || price).toFixed(2),
        volume: meta.regularMarketVolume || 0,  // Fix F4: volume, no shares
        fuente: 'Yahoo Finance (15min delay)',
        live:   false,
      };
      _cacheSet(cacheKey, value, TTL.accion);
      return value;
    } catch (e) {
      console.warn(`[Market] Acción ${symbol} fallback:`, e.message);
      return { symbol, nemo, error: e.message, live: false };
    }
  }

  /**
   * 5. Datos históricos de precios
   * symbol:   '^IPSA', 'COPEC', 'SQM-B', etc.
   * range:    '1mo', '3mo', '6mo', '1y', '2y', '5y'
   * interval: '1d', '1wk', '1mo'
   * Retorna: array de { date, open, high, low, close, volume }
   */
  async function getHistorico(symbol, range = '1mo', interval = '1d') {
    const sym      = symbol.includes('.') || symbol.startsWith('^') ? symbol : `${symbol}.SN`;
    const cacheKey = `hist_${sym}_${range}_${interval}`;
    const cached   = _cacheGet(cacheKey);
    if (cached) return cached;
    try {
      const url  = `${YAHOO_BASE}${encodeURIComponent(sym)}?interval=${interval}&range=${range}`;
      const json = await _fetch(url, true);
      const result = json?.chart?.result?.[0];
      if (!result) throw new Error(`Sin histórico para ${sym}`);

      const timestamps = result.timestamp || [];
      const ohlcv      = result.indicators?.quote?.[0] || {};
      const { open = [], high = [], low = [], close = [], volume = [] } = ohlcv;

      const candles = timestamps.map((ts, i) => ({
        date:   new Date(ts * 1000).toISOString().split('T')[0],
        open:   open[i]   != null ? +parseFloat(open[i]).toFixed(2)   : null,
        high:   high[i]   != null ? +parseFloat(high[i]).toFixed(2)   : null,
        low:    low[i]    != null ? +parseFloat(low[i]).toFixed(2)    : null,
        close:  close[i]  != null ? +parseFloat(close[i]).toFixed(2)  : null,
        volume: volume[i] || 0,
      })).filter(c => c.close !== null);

      _cacheSet(cacheKey, candles, TTL.historico);
      return candles;
    } catch (e) {
      console.warn(`[Market] Histórico ${sym} fallback:`, e.message);
      return [];
    }
  }

  /**
   * 6. getAll — todos los indicadores en paralelo
   * Útil para el dashboard — una sola llamada al cargar la página
   */
  async function getAll() {
    const [usdclp, mini, ipsa] = await Promise.all([
      getUSDCLP(),
      _fetchMindicador(),
      getIPSA(),
    ]);
    return {
      usdclp,
      uf:    mini.uf    ?? FALLBACKS.uf,
      ipc:   mini.ipc   ?? FALLBACKS.ipc,
      tpm:   mini.tpm   ?? FALLBACKS.tpm,
      cobre: mini.cobre ?? FALLBACKS.cobre,
      euro:  mini.euro  ?? FALLBACKS.euro,
      ipsa,
    };
  }

  /**
   * Estado de la caché — útil para debugging
   */
  function cacheStatus() {
    const now    = Date.now();
    const result = {};
    for (const [key, item] of Object.entries(_cache)) {
      const age = now - item.ts;
      result[key] = {
        age_s:    +(age / 1000).toFixed(1),
        fresh:    age < item.ttl,
        ttl_s:    item.ttl / 1000,
        hasValue: item.value !== null,
      };
    }
    return result;
  }

  // ─────────────────────────────────────────────────────────
  // RETORNO PÚBLICO
  // ─────────────────────────────────────────────────────────
  return {
    // Datos de mercado
    getUSDCLP,
    getUF,
    getIPC,
    getTPM,
    getCobre,
    getEuro,
    getIPSA,
    getAccion,
    getHistorico,
    getAll,
    cacheStatus,

    // Módulos
    Indicators,
    MatchingEngine,
    Performance,

    // Fallbacks accesibles si se necesitan externamente
    FALLBACKS,
  };

})();

// Disponible globalmente
window.Market = Market;
console.log('[InvertirCL] Market.js v1.0 cargado.');
console.log('  Uso: const data = await Market.getAll()');
console.log('  RSI: Market.Indicators.RSI(closes, 14)');
console.log('  Costos: Market.MatchingEngine.costos(8420, 100, "buy")');
