/**
 * InvertirCL — Score de Disciplina Institucional v1.0
 * =====================================================
 * Ítem 09 del Roadmap — El feature más original del proyecto.
 * Traducción directa de discipline.py a JS vanilla.
 *
 * USO en simulator.html:
 *   <script src="../js/discipline.js"></script>
 *
 *   // Al abrir un trade:
 *   DisciplineScore.registrarTrade({
 *     id: 'trade_001', symbol: 'COPEC', side: 'buy',
 *     entrada: 8420, stopOriginal: 8210, takeProfit: 8841,
 *     cantidad: 100,
 *   });
 *
 *   // Al cerrar un trade:
 *   DisciplineScore.evaluarCierre({
 *     id: 'trade_001', precioCierre: 8841,
 *     stopFinal: 8210, razonCierre: 'take_profit',
 *   });
 *
 *   // Obtener score actual:
 *   const resultado = DisciplineScore.calcular();
 *   console.log(resultado.score, resultado.nivel);
 */

const DisciplineScore = (() => {

  // ── CONFIGURACIÓN (se lee de ajustes.html via localStorage) ─
  function getConfig() {
    try {
      const cfg = JSON.parse(localStorage.getItem('invertircl_config') || '{}');
      return {
        capitalInicial:   cfg.capital        || 10_000_000,
        riesgoMaxPct:     cfg.riesgoTrade    || 2.0,
        maxTradesHora:    cfg.maxTrades      || 3,
        minRR:            cfg.rrMin          || 1.5,
        ventanaRevenge:   30,  // minutos
        horarioInicio:    cfg.horaInicio     || '09:30',
        horarioCierre:    cfg.horaCierre     || '17:30',
      };
    } catch { return { capitalInicial:10_000_000, riesgoMaxPct:2.0, maxTradesHora:3, minRR:1.5, ventanaRevenge:30 }; }
  }

  // ── ESTADO ────────────────────────────────────────────────────
  const KEY = 'invertircl_discipline_v1';

  function cargarEstado() {
    try {
      const s = JSON.parse(localStorage.getItem(KEY) || '{}');
      return {
        trades:        s.trades        || [],
        penalizaciones:s.penalizaciones|| [],
        sesionInicio:  s.sesionInicio  || new Date().toISOString(),
      };
    } catch { return { trades:[], penalizaciones:[], sesionInicio: new Date().toISOString() }; }
  }

  function guardarEstado(estado) {
    try { localStorage.setItem(KEY, JSON.stringify(estado)); } catch {}
  }

  let estado = cargarEstado();

  // ── PENALIZACIÓN / BONUS ─────────────────────────────────────
  function penalizar(categoria, descripcion, puntos, tradeId=null) {
    estado.penalizaciones.push({
      categoria, descripcion, puntos,
      timestamp: new Date().toISOString(),
      tradeId,
    });
    guardarEstado(estado);
  }

  // ── HELPERS ───────────────────────────────────────────────────
  function tradesEnUltimaHora(timestamp) {
    const ts = new Date(timestamp);
    const h1 = new Date(ts - 60*60*1000);
    return estado.trades.filter(t => new Date(t.timestampEntrada) >= h1).length;
  }

  function esRevengTrading(timestamp) {
    const ts      = new Date(timestamp);
    const ventana = new Date(ts - 30*60*1000);
    return estado.trades.some(t =>
      t.pnl !== null && t.pnl < 0 &&
      t.timestampCierre &&
      new Date(t.timestampCierre) >= ventana &&
      new Date(t.timestampCierre) <= ts
    );
  }

  function evaluarHorario(timestamp) {
    const cfg = getConfig();
    const ts  = new Date(timestamp);
    const min = ts.getHours()*60 + ts.getMinutes();
    const [ah, am] = (cfg.horarioInicio||'09:30').split(':').map(Number);
    const [ch, cm] = (cfg.horarioCierre||'17:30').split(':').map(Number);
    const ap = ah*60+am;
    const cl = ch*60+cm;
    if(min < ap || min > cl) return 'fuera_horario';
    if(min < ap+15)          return 'apertura_volatil';
    if(min > cl-15)          return 'cierre_volatil';
    return 'optimo';
  }

  // ── REGISTRAR TRADE (apertura) ───────────────────────────────
  function registrarTrade(data) {
    const cfg = getConfig();
    const ts  = data.timestamp || new Date().toISOString();

    const trade = {
      id:              data.id || 'trade_' + Date.now(),
      symbol:          data.symbol || '—',
      side:            data.side   || 'buy',
      entrada:         parseFloat(data.entrada       || 0),
      stopOriginal:    parseFloat(data.stopOriginal  || 0),
      takeProfit:      parseFloat(data.takeProfit    || 0),
      cantidad:        parseInt(data.cantidad        || 0),
      timestampEntrada: ts,
      capitalTotal:    parseFloat(data.entrada||0) * parseInt(data.cantidad||0),
      // Se llenan al cerrar:
      precioCierre:    null,
      stopFinal:       null,
      timestampCierre: null,
      razonCierre:     null,
      pnl:             null,
      // Flags:
      movioStop:    false,
      cerroAntesTp: false,
      sinStop:      false,
      rrInvalido:   false,
    };

    // CHECK 1 — Sin stop loss
    if(!trade.stopOriginal || trade.stopOriginal === 0) {
      trade.sinStop = true;
      penalizar('stop_loss', 'Trade sin stop loss definido', -15, trade.id);
    }

    // CHECK 2 — R/R ratio inválido
    if(trade.stopOriginal && trade.takeProfit) {
      const riesgo   = Math.abs(trade.entrada - trade.stopOriginal);
      const ganancia = Math.abs(trade.takeProfit - trade.entrada);
      const rr       = riesgo > 0 ? ganancia/riesgo : 0;
      if(rr < cfg.minRR) {
        trade.rrInvalido = true;
        penalizar('rr_ratio', `R/R ratio bajo (${rr.toFixed(1)}:1 < ${cfg.minRR}:1)`, -8, trade.id);
      }
    }

    // CHECK 3 — Position sizing
    if(trade.stopOriginal) {
      const riesgoPct = (Math.abs(trade.entrada-trade.stopOriginal)*trade.cantidad / cfg.capitalInicial*100);
      if(riesgoPct > cfg.riesgoMaxPct) {
        penalizar('sizing', `Riesgo por trade (${riesgoPct.toFixed(1)}%) supera máximo (${cfg.riesgoMaxPct}%)`, -10, trade.id);
      }
    }

    // CHECK 4 — Overtrading
    const trHora = tradesEnUltimaHora(ts);
    if(trHora >= cfg.maxTradesHora) {
      penalizar('overtrading', `Overtrading: ${trHora+1} trades en la última hora`, -12, trade.id);
    }

    // CHECK 5 — Revenge trading
    if(esRevengTrading(ts)) {
      penalizar('revenge', 'Revenge trading: operando inmediatamente después de una pérdida', -20, trade.id);
    }

    // CHECK 6 — Horario
    const horario = evaluarHorario(ts);
    if(horario === 'fuera_horario') {
      penalizar('horario', 'Trade fuera del horario de la Bolsa de Santiago', -8, trade.id);
    } else if(horario === 'apertura_volatil' || horario === 'cierre_volatil') {
      penalizar('horario', `Operando en zona de alta volatilidad (${horario})`, -4, trade.id);
    }

    estado.trades.push(trade);
    guardarEstado(estado);
    actualizarWidget();
    return trade;
  }

  // ── EVALUAR CIERRE ────────────────────────────────────────────
  function evaluarCierre(data) {
    const trade = estado.trades.find(t => t.id === data.id);
    if(!trade) return null;

    trade.precioCierre    = parseFloat(data.precioCierre || 0);
    trade.stopFinal       = parseFloat(data.stopFinal    || trade.stopOriginal);
    trade.timestampCierre = data.timestamp || new Date().toISOString();
    trade.razonCierre     = data.razonCierre || 'manual';

    // P&L
    const pnlBruto = trade.side === 'buy'
      ? (trade.precioCierre - trade.entrada) * trade.cantidad
      : (trade.entrada - trade.precioCierre) * trade.cantidad;
    trade.pnl = Math.round(pnlBruto);

    // CHECK 7 — Movió el stop
    const stopMovido = trade.side === 'buy'
      ? trade.stopFinal < trade.stopOriginal * 0.995
      : trade.stopFinal > trade.stopOriginal * 1.005;
    if(stopMovido) {
      trade.movioStop = true;
      penalizar('stop_movido', 'Stop loss movido para evitar pérdida', -25, trade.id);
    }

    // CHECK 8 — Cerró antes del TP por impaciencia
    const cerroAntesTp = trade.razonCierre === 'manual' &&
      ((trade.side === 'buy'  && trade.precioCierre < trade.takeProfit * 0.98) ||
       (trade.side === 'sell' && trade.precioCierre > trade.takeProfit * 1.02));
    if(cerroAntesTp) {
      trade.cerroAntesTp = true;
      penalizar('impaciencia', 'Cerró la posición antes de alcanzar el Take Profit', -6, trade.id);
    }

    // BONUS — Respetó el stop
    if(trade.razonCierre === 'stop' && !stopMovido) {
      penalizar('disciplina', '✅ Stop loss respetado — pérdida controlada', +5, trade.id);
    }

    // BONUS — Take Profit alcanzado
    if(trade.razonCierre === 'take_profit') {
      penalizar('disciplina', '✅ Take Profit alcanzado — disciplina recompensada', +10, trade.id);
    }

    guardarEstado(estado);
    actualizarWidget();
    return trade;
  }

  // ── CALCULAR SCORE ────────────────────────────────────────────
  function calcular() {
    let score = 100;

    // Penalizaciones y bonuses
    const breakdown = {
      stop_movido: 0, revenge: 0, overtrading: 0,
      stop_loss:0, rr_ratio:0, sizing:0, horario:0,
      impaciencia:0, disciplina:0,
    };

    const pens = [], bons = [];
    for(const p of estado.penalizaciones) {
      score += p.puntos;
      if(breakdown[p.categoria] !== undefined) breakdown[p.categoria] += p.puntos;
      if(p.puntos < 0) pens.push(p);
      else             bons.push(p);
    }

    score = Math.max(0, Math.min(100, Math.round(score)));

    // Nivel
    let nivel, emoji, color, bloqueado;
    if(score >= 90)      { nivel='Institucional ⭐'; emoji='⭐'; color='#00e676'; bloqueado=false; }
    else if(score >= 70) { nivel='Disciplinado';     emoji='✅'; color='#4a9eff'; bloqueado=false; }
    else if(score >= 50) { nivel='Aceptable';        emoji='⚠️'; color='#f5c518'; bloqueado=false; }
    else if(score >= 30) { nivel='En riesgo';        emoji='🟡'; color='#ff6b35'; bloqueado=false; }
    else                 { nivel='Mesa bloqueada';   emoji='🔴'; color='#ff3d57'; bloqueado=true;  }

    // Resumen
    const tradesTotal  = estado.trades.length;
    const tradesCerrados = estado.trades.filter(t=>t.pnl!==null);
    const wins = tradesCerrados.filter(t=>t.pnl>0).length;
    const wr   = tradesCerrados.length > 0 ? (wins/tradesCerrados.length*100).toFixed(0) : '—';

    let resumen = '';
    if(score >= 90)      resumen = 'Operas con la disciplina de un prop trader institucional.';
    else if(score >= 70) resumen = 'Buena disciplina. Pequeños ajustes para alcanzar el nivel institucional.';
    else if(score >= 50) resumen = 'Disciplina aceptable pero con errores que cuestan rendimiento.';
    else if(score >= 30) resumen = 'Tu gestión de riesgo está en peligro. Revisa tus errores antes de continuar.';
    else                 resumen = 'Tu cuenta está en riesgo. Un Risk Manager detendría tus operaciones ahora.';

    // Recomendaciones
    const cats = {};
    for(const p of estado.penalizaciones) {
      if(p.puntos<0) cats[p.categoria] = (cats[p.categoria]||0) + Math.abs(p.puntos);
    }
    const recomendaciones = [];
    if(cats.stop_movido) recomendaciones.push('🔴 Nunca muevas el stop loss. El stop está donde está por una razón — protege tu capital.');
    if(cats.revenge)     recomendaciones.push('🧠 Revenge trading detectado. Tómate 1 hora de descanso después de una pérdida.');
    if(cats.overtrading) recomendaciones.push('⏳ Menos es más. Espera setups de alta probabilidad.');
    if(cats.stop_loss)   recomendaciones.push('🛡️ Define siempre el stop loss ANTES de entrar.');
    if(cats.rr_ratio)    recomendaciones.push('📐 Apunta a R/R ≥ 2:1. Por cada peso en riesgo, gana mínimo $2.');
    if(cats.sizing)      recomendaciones.push('📏 Nunca arriesgues más del 2% por trade. Sobrevives 50 pérdidas seguidas.');
    if(!recomendaciones.length) recomendaciones.push('✅ Excelente disciplina. Sigue respetando tu plan.');

    return {
      score, nivel, emoji, color, bloqueado,
      breakdown, penalizaciones: pens, bonuses: bons,
      tradesAnalizados: tradesTotal,
      winRate: wr,
      resumen, recomendaciones,
    };
  }

  // ── WIDGET VISUAL ─────────────────────────────────────────────
  // Se inserta automáticamente en el topbar/sidebar del simulador.
  const WIDGET_CSS = `
    #disc-widget {
      position: fixed;
      bottom: 20px;
      left: 20px;
      z-index: 500;
      background: var(--bg2, #0d1117);
      border: 1px solid var(--linea, rgba(255,255,255,0.06));
      border-radius: 12px;
      padding: 12px 16px;
      min-width: 220px;
      box-shadow: 0 8px 32px rgba(0,0,0,0.4);
      font-family: 'DM Sans', sans-serif;
      transition: all 0.3s ease;
      cursor: pointer;
    }
    #disc-widget:hover { transform: translateY(-2px); box-shadow: 0 12px 40px rgba(0,0,0,0.5); }
    #disc-widget.bloqueado { border-color: rgba(255,61,87,0.4); animation: bloqPulse 1.5s infinite; }
    @keyframes bloqPulse { 0%,100%{box-shadow:0 0 0 0 rgba(255,61,87,0.3)}50%{box-shadow:0 0 0 8px rgba(255,61,87,0)} }
    .dw-header { display:flex; align-items:center; justify-content:space-between; margin-bottom:8px; }
    .dw-label { font-size:0.62rem; text-transform:uppercase; letter-spacing:0.08em; color:rgba(255,255,255,0.3); }
    .dw-nivel { font-size:0.68rem; font-weight:600; }
    .dw-score-wrap { display:flex; align-items:flex-end; gap:6px; margin-bottom:8px; }
    .dw-score { font-family:'Syne', sans-serif; font-size:2rem; font-weight:800; line-height:1; }
    .dw-score-sub { font-family:'DM Mono', monospace; font-size:0.68rem; color:rgba(255,255,255,0.3); margin-bottom:4px; }
    .dw-bar-track { height:5px; background:rgba(255,255,255,0.06); border-radius:3px; overflow:hidden; margin-bottom:8px; }
    .dw-bar-fill { height:100%; border-radius:3px; transition: width 0.6s ease, background 0.3s; }
    .dw-rec { font-size:0.7rem; color:rgba(255,255,255,0.5); line-height:1.5; }
    .dw-rec.alerta { color:#ff3d57; }
    .dw-trades { display:flex; gap:10px; margin-top:6px; }
    .dw-trade-stat { font-size:0.65rem; color:rgba(255,255,255,0.3); }
    .dw-trade-stat span { font-family:'DM Mono',monospace; color:rgba(255,255,255,0.6); }
    /* Panel expandido */
    #disc-panel {
      position: fixed; bottom: 20px; left: 254px; z-index: 500;
      background: var(--bg2, #0d1117); border:1px solid rgba(255,255,255,0.08);
      border-radius:12px; padding:16px; width:300px;
      box-shadow:0 8px 32px rgba(0,0,0,0.5);
      display:none;
      font-family:'DM Sans',sans-serif;
    }
    #disc-panel.visible { display:block; animation:panelIn 0.2s ease; }
    @keyframes panelIn{from{opacity:0;transform:translateX(-8px)}to{opacity:1;transform:translateX(0)}}
    .dp-title { font-family:'Syne',sans-serif; font-size:0.88rem; font-weight:700; margin-bottom:12px; }
    .dp-section { margin-bottom:12px; }
    .dp-section-title { font-size:0.6rem; text-transform:uppercase; letter-spacing:0.08em; color:rgba(255,255,255,0.25); margin-bottom:6px; }
    .dp-item { display:flex; justify-content:space-between; font-size:0.72rem; padding:4px 0; border-bottom:1px solid rgba(255,255,255,0.04); }
    .dp-item:last-child{border-bottom:none;}
    .dp-k { color:rgba(255,255,255,0.5); }
    .dp-v { font-family:'DM Mono',monospace; }
    .dp-rec { font-size:0.72rem; color:rgba(255,255,255,0.5); line-height:1.6; padding:6px 8px; background:rgba(255,255,255,0.02); border-radius:6px; margin-bottom:4px; }
    .dp-btn-reset { width:100%; padding:7px; border-radius:6px; border:1px solid rgba(255,61,87,0.2); background:transparent; color:#ff3d57; font-family:'DM Sans',sans-serif; font-size:0.72rem; cursor:pointer; margin-top:8px; }
    .dp-btn-reset:hover{background:rgba(255,61,87,0.06);}
  `;

  function inyectarCSS() {
    if(document.getElementById('disc-css')) return;
    const s = document.createElement('style');
    s.id = 'disc-css';
    s.textContent = WIDGET_CSS;
    document.head.appendChild(s);
  }

  function actualizarWidget() {
    const r = calcular();
    const w = document.getElementById('disc-widget');
    if(!w) return;

    w.className = r.bloqueado ? 'bloqueado' : '';
    w.innerHTML = `
      <div class="dw-header">
        <span class="dw-label">Score Disciplina</span>
        <span class="dw-nivel" style="color:${r.color}">${r.emoji} ${r.nivel}</span>
      </div>
      <div class="dw-score-wrap">
        <div class="dw-score" style="color:${r.color}">${r.score}</div>
        <div class="dw-score-sub">/100</div>
      </div>
      <div class="dw-bar-track">
        <div class="dw-bar-fill" style="width:${r.score}%;background:${r.color}"></div>
      </div>
      <div class="dw-rec ${r.bloqueado?'alerta':''}">${r.bloqueado?'🔴 MESA BLOQUEADA — Para operar hoy':r.recomendaciones[0]||''}</div>
      <div class="dw-trades">
        <div class="dw-trade-stat">Trades: <span>${r.tradesAnalizados}</span></div>
        <div class="dw-trade-stat">WR: <span>${r.winRate}%</span></div>
        <div class="dw-trade-stat">Pens.: <span>${r.penalizaciones.length}</span></div>
      </div>
    `;

    // Actualizar localStorage para ranking.html
    localStorage.setItem('invertircl_score', r.score);
    localStorage.setItem('invertircl_trades', r.tradesAnalizados);
  }

  function actualizarPanel() {
    const r = calcular();
    const p = document.getElementById('disc-panel');
    if(!p || !p.classList.contains('visible')) return;

    const fmt = v => (v>=0?'+':'')+Math.round(v).toLocaleString('es-CL');

    p.innerHTML = `
      <div class="dp-title">📊 Desglose del Score</div>

      <div class="dp-section">
        <div class="dp-section-title">Categorías</div>
        ${[
          ['Stop loss movido', r.breakdown.stop_movido],
          ['Revenge trading',  r.breakdown.revenge],
          ['Overtrading',      r.breakdown.overtrading],
          ['Sin stop loss',    r.breakdown.stop_loss],
          ['R/R inválido',     r.breakdown.rr_ratio],
          ['Position sizing',  r.breakdown.sizing],
          ['Horario',          r.breakdown.horario],
          ['Impaciencia',      r.breakdown.impaciencia],
          ['Disciplina',       r.breakdown.disciplina],
        ].filter(([,v])=>v!==0).map(([k,v])=>`
          <div class="dp-item">
            <span class="dp-k">${k}</span>
            <span class="dp-v" style="color:${v<0?'#ff3d57':'#00e676'}">${v>0?'+':''}${Math.round(v)} pts</span>
          </div>
        `).join('') || '<div class="dp-item"><span class="dp-k">Sin penalizaciones aún</span></div>'}
      </div>

      <div class="dp-section">
        <div class="dp-section-title">Últimas penalizaciones</div>
        ${r.penalizaciones.slice(-5).reverse().map(p=>`
          <div class="dp-item">
            <span class="dp-k">${p.descripcion.slice(0,30)}${p.descripcion.length>30?'…':''}</span>
            <span class="dp-v" style="color:#ff3d57">${Math.round(p.puntos)} pts</span>
          </div>
        `).join('') || '<div class="dp-item"><span class="dp-k">Sin penalizaciones</span></div>'}
      </div>

      <div class="dp-section">
        <div class="dp-section-title">Recomendaciones</div>
        ${r.recomendaciones.map(rec=>`<div class="dp-rec">${rec}</div>`).join('')}
      </div>

      <button class="dp-btn-reset" onclick="DisciplineScore.reset()">↺ Reiniciar sesión</button>
    `;
  }

  function togglePanel() {
    const p = document.getElementById('disc-panel');
    if(!p) return;
    p.classList.toggle('visible');
    if(p.classList.contains('visible')) actualizarPanel();
  }

  function insertarWidget() {
    inyectarCSS();
    if(document.getElementById('disc-widget')) return;

    const widget = document.createElement('div');
    widget.id = 'disc-widget';
    widget.onclick = togglePanel;
    document.body.appendChild(widget);

    const panel = document.createElement('div');
    panel.id = 'disc-panel';
    document.body.appendChild(panel);

    actualizarWidget();
  }

  // ── ALERTA DE RISK MANAGER ─────────────────────────────────
  function mostrarAlertaRiskManager(mensaje) {
    const alerta = document.createElement('div');
    alerta.style.cssText = `
      position:fixed;top:60px;left:50%;transform:translateX(-50%);
      background:#ff3d57;color:#fff;padding:12px 24px;border-radius:10px;
      font-family:'Syne',sans-serif;font-size:0.88rem;font-weight:700;
      z-index:9999;box-shadow:0 8px 32px rgba(255,61,87,0.5);
      animation:rmIn 0.3s ease;
    `;
    const style = document.createElement('style');
    style.textContent = '@keyframes rmIn{from{opacity:0;transform:translateX(-50%) translateY(-10px)}to{opacity:1;transform:translateX(-50%) translateY(0)}}';
    document.head.appendChild(style);
    alerta.textContent = '⚠️ Risk Manager: ' + mensaje;
    document.body.appendChild(alerta);
    setTimeout(() => alerta.remove(), 5000);
  }

  // ── RESET ─────────────────────────────────────────────────────
  function reset() {
    estado = { trades:[], penalizaciones:[], sesionInicio: new Date().toISOString() };
    guardarEstado(estado);
    actualizarWidget();
    const p = document.getElementById('disc-panel');
    if(p) p.classList.remove('visible');
  }

  // ── API PÚBLICA ───────────────────────────────────────────────
  // Auto-insertar widget al cargar
  document.addEventListener('DOMContentLoaded', () => insertarWidget());

  return {
    registrarTrade(data) {
      const t = registrarTrade(data);
      const r = calcular();
      // Alertas de Risk Manager
      if(r.bloqueado) mostrarAlertaRiskManager('Score crítico. Se recomienda detener operaciones hoy.');
      else if(r.score < 50) mostrarAlertaRiskManager(`Score en ${r.score}/100. Revisa tu disciplina antes de continuar.`);
      actualizarPanel();
      return t;
    },
    evaluarCierre(data) {
      const t = evaluarCierre(data);
      const r = calcular();
      if(r.bloqueado) mostrarAlertaRiskManager('Score en zona de bloqueo. Considera detener operaciones.');
      actualizarPanel();
      return t;
    },
    calcular,
    reset,
    getEstado: () => estado,
    insertarWidget,
  };

})();

window.DisciplineScore = DisciplineScore;
console.log('[InvertirCL] DisciplineScore v1.0 cargado. Score actual:', DisciplineScore.calcular().score);
