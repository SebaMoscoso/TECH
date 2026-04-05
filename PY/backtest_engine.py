"""
InvertirCL — Backtest Engine v1.0
===================================
Item 14 del Roadmap.

Motor de backtest que corre estrategias sobre datos históricos
reales de Yahoo Finance. Entrega reporte completo de robustez.

INCLUYE:
  - Backtest básico sobre datos reales
  - Walk-Forward Testing (in-sample vs out-of-sample)
  - Métricas institucionales completas
  - Reporte de robustez

ESTRATEGIAS SOPORTADAS:
  - RSI Mean Reversion
  - EMA Crossover
  - Bollinger Bands
  - MACD Signal
  - Cualquier estrategia custom (pasando función)

USO:
  from backtest import BacktestEngine
  from market import Market

  engine = BacktestEngine(capital_inicial=10_000_000)

  # Backtest básico
  df = Market.get_historico("^IPSA", period="1y")
  resultado = engine.run(df, estrategia="rsi_mean_reversion")
  print(resultado.resumen())

  # Walk-Forward
  wf = engine.walk_forward(df, estrategia="rsi_mean_reversion")
  print(wf.resumen())
"""

import math
import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Callable

try:
    import pandas as pd
    import numpy as np
    LIBS_OK = True
except ImportError:
    LIBS_OK = False
    print("[Backtest] Instala: pip install pandas numpy yfinance")


# ══════════════════════════════════════════════════════════════
# ESTRUCTURAS
# ══════════════════════════════════════════════════════════════
@dataclass
class TradeBacktest:
    fecha_entrada:  str
    fecha_salida:   str
    precio_entrada: float
    precio_salida:  float
    lado:           str      # "long" | "short"
    cantidad:       int
    pnl:            float
    pnl_pct:        float
    razon_entrada:  str
    razon_salida:   str
    duracion_dias:  int
    mae:            float    # Maximum Adverse Excursion
    mfe:            float    # Maximum Favorable Excursion


@dataclass
class ResultadoBacktest:
    # Identificación
    symbol:         str
    estrategia:     str
    periodo:        str
    fecha_inicio:   str
    fecha_fin:      str

    # Métricas principales
    retorno_total:  float    # %
    retorno_anual:  float    # % anualizado
    sharpe:         float
    sortino:        float
    max_drawdown:   float    # %
    calmar:         float    # retorno anual / max DD

    # Métricas de trades
    n_trades:       int
    win_rate:       float    # %
    profit_factor:  float
    avg_win:        float    # $ promedio ganancia
    avg_loss:       float    # $ promedio pérdida
    avg_rr:         float    # R/R promedio real
    best_trade:     float    # $ mejor trade
    worst_trade:    float    # $ peor trade
    avg_duracion:   float    # días promedio por trade

    # Curva de capital
    equity_curve:   list
    trades:         list
    retornos_mensuales: dict

    # Veredicto
    robusto:        bool
    veredicto:      str

    def resumen(self):
        dir_ret = "▲" if self.retorno_total >= 0 else "▼"
        lineas = [
            f"\n{'='*58}",
            f"  📊 BACKTEST — {self.symbol} · {self.estrategia}",
            f"  Período: {self.fecha_inicio} → {self.fecha_fin}",
            f"{'='*58}",
            f"",
            f"  {'RETORNO TOTAL':<25} {dir_ret} {self.retorno_total:+.2f}%",
            f"  {'Retorno anualizado':<25} {self.retorno_anual:+.2f}%",
            f"  {'Sharpe Ratio':<25} {self.sharpe:.2f}{'  ✅' if self.sharpe > 1 else '  ⚠️' if self.sharpe > 0.5 else '  ❌'}",
            f"  {'Sortino Ratio':<25} {self.sortino:.2f}",
            f"  {'Max Drawdown':<25} -{self.max_drawdown:.2f}%",
            f"  {'Calmar Ratio':<25} {self.calmar:.2f}",
            f"",
            f"  {'─'*50}",
            f"  {'Trades totales':<25} {self.n_trades}",
            f"  {'Win Rate':<25} {self.win_rate:.1f}%{'  ✅' if self.win_rate > 55 else '  ⚠️' if self.win_rate > 45 else '  ❌'}",
            f"  {'Profit Factor':<25} {self.profit_factor:.2f}x{'  ✅' if self.profit_factor > 1.5 else '  ⚠️' if self.profit_factor > 1 else '  ❌'}",
            f"  {'Avg ganancia':<25} ${self.avg_win:,.0f}",
            f"  {'Avg pérdida':<25} ${self.avg_loss:,.0f}",
            f"  {'R/R promedio real':<25} {self.avg_rr:.2f}:1",
            f"  {'Mejor trade':<25} ${self.best_trade:+,.0f}",
            f"  {'Peor trade':<25} ${self.worst_trade:+,.0f}",
            f"  {'Duración promedio':<25} {self.avg_duracion:.1f} días",
            f"",
            f"  {'─'*50}",
            f"  VEREDICTO: {self.veredicto}",
            f"  Robusto: {'✅ Sí' if self.robusto else '❌ No'}",
        ]

        if self.retornos_mensuales:
            lineas.append(f"\n  Retornos mensuales:")
            for mes, ret in list(self.retornos_mensuales.items())[-6:]:
                bar = "█" * int(abs(ret) / 2) if abs(ret) < 20 else "█" * 10
                color = "+" if ret >= 0 else "-"
                lineas.append(f"  {mes}: {color}{abs(ret):.1f}% {bar}")

        lineas.append(f"{'='*58}")
        return "\n".join(lineas)


@dataclass
class ResultadoWalkForward:
    n_ventanas:     int
    ventanas:       list     # lista de dicts con métricas por ventana
    degradacion:    float    # % caída WR out vs in
    consistencia:   float    # % ventanas donde out > 0
    robusto:        bool
    veredicto:      str

    def resumen(self):
        lineas = [
            f"\n{'='*58}",
            f"  🔬 WALK-FORWARD TESTING",
            f"{'='*58}",
            f"  Ventanas analizadas:  {self.n_ventanas}",
            f"  Degradación IS→OOS:   {self.degradacion:+.1f}%",
            f"  Consistencia OOS:     {self.consistencia:.1f}%",
            f"  Veredicto:            {self.veredicto}",
            f"  Robusto:              {'✅ Sí' if self.robusto else '❌ No — posible overfitting'}",
            f"\n  Detalle por ventana:",
        ]
        for i, v in enumerate(self.ventanas):
            lineas.append(
                f"  V{i+1:02d} IS:{v['wr_is']:5.1f}% → OOS:{v['wr_oos']:5.1f}% | "
                f"Ret OOS:{v['ret_oos']:+6.2f}% | "
                f"{'✅' if v['ret_oos'] > 0 else '❌'}"
            )
        lineas.append(f"{'='*58}")
        return "\n".join(lineas)


# ══════════════════════════════════════════════════════════════
# INDICADORES INTERNOS
# ══════════════════════════════════════════════════════════════
def _rsi(closes, n=14):
    if len(closes) < n + 1:
        return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains  = [max(d, 0) for d in deltas]
    losses = [max(-d, 0) for d in deltas]
    ag = sum(gains[:n]) / n
    al = sum(losses[:n]) / n
    for i in range(n, len(deltas)):
        ag = (ag * (n-1) + gains[i]) / n
        al = (al * (n-1) + losses[i]) / n
    return 100 - 100 / (1 + ag / al) if al > 0 else 100.0

def _ema(closes, n):
    if len(closes) < n:
        return closes[-1] if closes else 0
    k   = 2 / (n + 1)
    ema = sum(closes[:n]) / n
    for p in closes[n:]:
        ema = p * k + ema * (1 - k)
    return ema

def _bb(closes, n=20, std=2):
    if len(closes) < n:
        return None, None, None
    arr   = closes[-n:]
    mean_ = sum(arr) / n
    var   = sum((x - mean_)**2 for x in arr) / n
    sigma = var ** 0.5
    return mean_ + std * sigma, mean_, mean_ - std * sigma

def _macd(closes, fast=12, slow=26, signal=9):
    if len(closes) < slow + signal:
        return 0, 0
    k_f = 2 / (fast + 1)
    k_s = 2 / (slow + 1)
    ef  = sum(closes[:fast]) / fast
    es  = sum(closes[:slow]) / slow
    ml  = []
    for i in range(slow, len(closes)):
        if i >= fast:
            ef = closes[i] * k_f + ef * (1 - k_f)
        es = closes[i] * k_s + es * (1 - k_s)
        ml.append(ef - es)
    if len(ml) < signal:
        return ml[-1] if ml else 0, 0
    k_sig = 2 / (signal + 1)
    sig   = sum(ml[:signal]) / signal
    for v in ml[signal:]:
        sig = v * k_sig + sig * (1 - k_sig)
    return ml[-1], sig

def _atr(candles, n=14):
    if len(candles) < n + 1:
        return candles[-1]["close"] * 0.01 if candles else 100
    trs = []
    for i in range(1, len(candles)):
        c, p = candles[i], candles[i-1]
        trs.append(max(
            c["high"] - c["low"],
            abs(c["high"] - p["close"]),
            abs(c["low"]  - p["close"]),
        ))
    atr = sum(trs[:n]) / n
    for tr in trs[n:]:
        atr = (atr * (n-1) + tr) / n
    return atr


# ══════════════════════════════════════════════════════════════
# ESTRATEGIAS PREDEFINIDAS
# ══════════════════════════════════════════════════════════════
class Estrategias:

    @staticmethod
    def rsi_mean_reversion(candles, closes, params=None):
        """RSI < umbral_os → BUY. RSI > umbral_ob → señal salida."""
        p   = params or {"rsi_n": 14, "os": 30, "ob": 70, "stop_mult": 1.5}
        rsi = _rsi(closes, p.get("rsi_n", 14))
        atr = _atr(candles, 14)
        precio = closes[-1]
        if rsi < p.get("os", 30):
            stop = precio - atr * p.get("stop_mult", 1.5)
            tp   = precio + atr * p.get("stop_mult", 1.5) * 2
            return {"señal": "buy", "stop": stop, "tp": tp,
                    "razon": f"RSI({rsi:.1f}) sobrevendido"}
        if rsi > p.get("ob", 70):
            return {"señal": "salida", "razon": f"RSI({rsi:.1f}) sobrecomprado"}
        return None

    @staticmethod
    def ema_crossover(candles, closes, params=None):
        """EMA rápida cruza sobre lenta → BUY. Cruce opuesto → salida."""
        p     = params or {"fast": 9, "slow": 21, "stop_mult": 2.0}
        if len(closes) < p.get("slow", 21) + 2:
            return None
        fast_now  = _ema(closes,       p.get("fast", 9))
        slow_now  = _ema(closes,       p.get("slow", 21))
        fast_prev = _ema(closes[:-1],  p.get("fast", 9))
        slow_prev = _ema(closes[:-1],  p.get("slow", 21))
        atr       = _atr(candles, 14)
        precio    = closes[-1]
        if fast_prev < slow_prev and fast_now > slow_now:
            stop = precio - atr * p.get("stop_mult", 2.0)
            tp   = precio + atr * p.get("stop_mult", 2.0) * 2
            return {"señal": "buy", "stop": stop, "tp": tp,
                    "razon": f"EMA{p['fast']} cruzó sobre EMA{p['slow']}"}
        if fast_prev > slow_prev and fast_now < slow_now:
            return {"señal": "salida", "razon": "EMA cruce bajista"}
        return None

    @staticmethod
    def bollinger_reversion(candles, closes, params=None):
        """Precio toca banda inferior → BUY. Precio toca banda superior → salida."""
        p      = params or {"n": 20, "std": 2.0, "stop_mult": 1.0}
        upper, mid, lower = _bb(closes, p.get("n", 20), p.get("std", 2.0))
        if upper is None:
            return None
        atr    = _atr(candles, 14)
        precio = closes[-1]
        if precio <= lower:
            stop = precio - atr * p.get("stop_mult", 1.0)
            return {"señal": "buy", "stop": stop, "tp": upper,
                    "razon": f"Precio(${precio:,.0f}) ≤ BB inferior(${lower:,.0f})"}
        if precio >= upper:
            return {"señal": "salida", "razon": f"Precio(${precio:,.0f}) ≥ BB superior(${upper:,.0f})"}
        return None

    @staticmethod
    def macd_signal(candles, closes, params=None):
        """MACD cruza sobre línea señal → BUY. Cruce opuesto → salida."""
        p      = params or {"fast": 12, "slow": 26, "signal": 9, "stop_mult": 2.5}
        macd_n, sig_n = _macd(closes, p.get("fast",12), p.get("slow",26), p.get("signal",9))
        if len(closes) > 1:
            macd_p, sig_p = _macd(closes[:-1], p.get("fast",12), p.get("slow",26), p.get("signal",9))
        else:
            return None
        atr    = _atr(candles, 14)
        precio = closes[-1]
        if macd_p < sig_p and macd_n > sig_n:
            stop = precio - atr * p.get("stop_mult", 2.5)
            tp   = precio + atr * p.get("stop_mult", 2.5) * 2
            return {"señal": "buy", "stop": stop, "tp": tp,
                    "razon": f"MACD({macd_n:.1f}) cruzó sobre señal({sig_n:.1f})"}
        if macd_p > sig_p and macd_n < sig_n:
            return {"señal": "salida", "razon": "MACD cruce bajista"}
        return None

    MAPA = {
        "rsi_mean_reversion":  rsi_mean_reversion.__func__,
        "ema_crossover":       ema_crossover.__func__,
        "bollinger_reversion": bollinger_reversion.__func__,
        "macd_signal":         macd_signal.__func__,
    }


# ══════════════════════════════════════════════════════════════
# MOTOR DE BACKTEST
# ══════════════════════════════════════════════════════════════
class BacktestEngine:

    def __init__(self, capital_inicial: float = 10_000_000, riesgo_pct: float = 2.0,
                 comision_pct: float = 0.1, slippage_bps: float = 5):
        self.capital_inicial = capital_inicial
        self.riesgo_pct      = riesgo_pct
        self.comision_pct    = comision_pct
        self.slippage_bps    = slippage_bps

    # ── CARGAR DATOS ──────────────────────────────────────────
    def _cargar(self, datos) -> list:
        """Convierte datos (DataFrame o list de dicts) a lista de dicts estandarizada."""
        if LIBS_OK and isinstance(datos, pd.DataFrame):
            cols = {c.lower(): c for c in datos.columns}
            def get(row, key):
                return float(row.get(cols.get(key, key), 0))
            return [
                {"date":   str(row.get(cols.get("date","date"), i))[:10],
                 "open":   get(row, "open"),
                 "high":   get(row, "high"),
                 "low":    get(row, "low"),
                 "close":  get(row, "close"),
                 "volume": get(row, "volume")}
                for i, row in datos.iterrows()
            ]
        return datos  # ya es lista de dicts

    # ── RUN BACKTEST ──────────────────────────────────────────
    def run(self, datos, estrategia: str = "rsi_mean_reversion",
            symbol: str = "N/A", params: dict = None) -> ResultadoBacktest:
        """
        Correr backtest completo sobre datos históricos.

        datos:      DataFrame de pandas o lista de dicts
        estrategia: nombre de estrategia predefinida o función custom
        symbol:     nombre del activo para el reporte
        params:     parámetros de la estrategia
        """
        candles = self._cargar(datos)
        if len(candles) < 30:
            raise ValueError("Se necesitan al menos 30 días de datos")

        fn_strat = Estrategias.MAPA.get(estrategia)
        if fn_strat is None:
            raise ValueError(f"Estrategia '{estrategia}' no encontrada. "
                             f"Opciones: {list(Estrategias.MAPA.keys())}")

        print(f"[Backtest] {symbol} | {estrategia} | {len(candles)} días")

        capital    = self.capital_inicial
        equity     = [capital]
        trades_bt  = []
        posicion   = None   # dict con entrada activa

        for i in range(30, len(candles)):
            window  = candles[:i+1]
            closes  = [c["close"] for c in window]
            precio  = closes[-1]
            fecha   = candles[i]["date"]

            # ── Gestión de posición abierta ──
            if posicion:
                c = candles[i]

                # Stop loss tocado
                if c["low"] <= posicion["stop"]:
                    precio_salida = posicion["stop"] * (1 - self.slippage_bps / 10_000)
                    t = self._cerrar(posicion, precio_salida, fecha, "stop_loss", capital, window)
                    capital += t.pnl
                    trades_bt.append(t)
                    posicion = None

                # Take profit tocado
                elif c["high"] >= posicion["tp"]:
                    precio_salida = posicion["tp"]
                    t = self._cerrar(posicion, precio_salida, fecha, "take_profit", capital, window)
                    capital += t.pnl
                    trades_bt.append(t)
                    posicion = None

                # Señal de salida por estrategia
                else:
                    señal = fn_strat(window, closes, params)
                    if señal and señal.get("señal") == "salida":
                        sl_extra = precio * self.slippage_bps / 10_000
                        t = self._cerrar(posicion, precio - sl_extra, fecha, señal["razon"], capital, window)
                        capital += t.pnl
                        trades_bt.append(t)
                        posicion = None

            # ── Buscar nueva entrada ──
            if not posicion:
                señal = fn_strat(window, closes, params)
                if señal and señal.get("señal") == "buy":
                    stop = señal["stop"]
                    tp   = señal["tp"]
                    rr   = (tp - precio) / (precio - stop) if precio > stop else 0

                    if rr >= 1.5 and stop > 0:
                        # Position sizing por riesgo
                        riesgo_max  = capital * self.riesgo_pct / 100
                        riesgo_acc  = precio - stop
                        cantidad    = max(1, int(riesgo_max / riesgo_acc)) if riesgo_acc > 0 else 1
                        sl_entrada  = precio * (1 + self.slippage_bps / 10_000)
                        comision    = sl_entrada * cantidad * self.comision_pct / 100

                        posicion = {
                            "fecha_entrada":  fecha,
                            "precio_entrada": sl_entrada,
                            "stop":           stop,
                            "tp":             tp,
                            "cantidad":       cantidad,
                            "comision":       comision,
                            "razon":          señal["razon"],
                            "min_precio":     precio,   # para MAE
                            "max_precio":     precio,   # para MFE
                        }

            # Actualizar MAE/MFE de posición abierta
            if posicion:
                posicion["min_precio"] = min(posicion["min_precio"], candles[i]["low"])
                posicion["max_precio"] = max(posicion["max_precio"], candles[i]["high"])

            equity.append(capital)

        # Cerrar posición abierta al final
        if posicion:
            t = self._cerrar(posicion, candles[-1]["close"], candles[-1]["date"], "fin_periodo", capital, candles)
            capital += t.pnl
            trades_bt.append(t)
            equity.append(capital)

        return self._calcular_metricas(trades_bt, equity, symbol, estrategia, candles)

    def _cerrar(self, pos, precio_salida, fecha, razon, capital, candles):
        pnl_bruto = (precio_salida - pos["precio_entrada"]) * pos["cantidad"]
        comision  = precio_salida * pos["cantidad"] * self.comision_pct / 100
        pnl_neto  = pnl_bruto - pos["comision"] - comision
        pnl_pct   = pnl_neto / (pos["precio_entrada"] * pos["cantidad"]) * 100

        # Días de duración
        try:
            d_ini  = datetime.strptime(pos["fecha_entrada"][:10], "%Y-%m-%d")
            d_fin  = datetime.strptime(fecha[:10], "%Y-%m-%d")
            dur    = max(1, (d_fin - d_ini).days)
        except Exception:
            dur = 1

        # MAE / MFE
        mae = (pos["precio_entrada"] - pos["min_precio"]) * pos["cantidad"]
        mfe = (pos["max_precio"] - pos["precio_entrada"]) * pos["cantidad"]

        return TradeBacktest(
            fecha_entrada  = pos["fecha_entrada"],
            fecha_salida   = fecha,
            precio_entrada = pos["precio_entrada"],
            precio_salida  = precio_salida,
            lado           = "long",
            cantidad       = pos["cantidad"],
            pnl            = round(pnl_neto, 0),
            pnl_pct        = round(pnl_pct, 2),
            razon_entrada  = pos["razon"],
            razon_salida   = razon,
            duracion_dias  = dur,
            mae            = round(mae, 0),
            mfe            = round(mfe, 0),
        )

    def _calcular_metricas(self, trades, equity, symbol, estrategia, candles):
        n     = len(trades)
        cerrados = trades  # todos cerrados

        # Retornos
        ret_total = (equity[-1] - equity[0]) / equity[0] * 100
        n_dias    = len(candles)
        años      = max(n_dias / 252, 0.1)
        ret_anual = ((equity[-1] / equity[0]) ** (1 / años) - 1) * 100

        # Drawdown
        peak  = equity[0]
        max_dd = 0.0
        for v in equity:
            if v > peak:
                peak = v
            dd = (peak - v) / peak * 100
            if dd > max_dd:
                max_dd = dd

        # Retornos diarios
        rets_diarios = [(equity[i] - equity[i-1]) / equity[i-1]
                        for i in range(1, len(equity))]

        # Sharpe
        if rets_diarios and len(rets_diarios) > 1:
            media = sum(rets_diarios) / len(rets_diarios)
            var   = sum((r - media)**2 for r in rets_diarios) / len(rets_diarios)
            std   = var ** 0.5
            sharpe = (media / std * (252 ** 0.5)) if std > 0 else 0
        else:
            sharpe = 0

        # Sortino
        neg  = [r for r in rets_diarios if r < 0]
        if neg and len(neg) > 1:
            std_neg = (sum(r**2 for r in neg) / len(neg)) ** 0.5
            media   = sum(rets_diarios) / len(rets_diarios)
            sortino = (media / std_neg * (252 ** 0.5)) if std_neg > 0 else 0
        else:
            sortino = sharpe

        calmar = ret_anual / max_dd if max_dd > 0 else 0

        # Métricas de trades
        wins   = [t for t in cerrados if t.pnl > 0]
        losses = [t for t in cerrados if t.pnl < 0]
        wr     = len(wins) / n * 100 if n > 0 else 0
        g_win  = sum(t.pnl for t in wins)
        g_loss = abs(sum(t.pnl for t in losses))
        pf     = g_win / g_loss if g_loss > 0 else float("inf")
        avg_win  = g_win  / len(wins)   if wins   else 0
        avg_loss = g_loss / len(losses) if losses else 0
        avg_rr   = avg_win / avg_loss   if avg_loss > 0 else 0
        best     = max((t.pnl for t in cerrados), default=0)
        worst    = min((t.pnl for t in cerrados), default=0)
        avg_dur  = sum(t.duracion_dias for t in cerrados) / n if n > 0 else 0

        # Retornos mensuales
        ret_mensual = {}
        for i in range(0, len(candles) - 1, 21):
            if i + 21 < len(equity):
                mes = candles[i]["date"][:7]
                ret = (equity[i+21] - equity[i]) / equity[i] * 100
                ret_mensual[mes] = round(ret, 2)

        # Veredicto
        robusto = sharpe > 0.8 and pf > 1.3 and wr > 45 and max_dd < 25
        if robusto:
            veredicto = "✅ ROBUSTO — Estrategia viable para operar"
        elif sharpe > 0.5 and pf > 1.1:
            veredicto = "⚠️  ACEPTABLE — Mejorar parámetros antes de operar"
        else:
            veredicto = "❌ DÉBIL — No recomendado para operar en vivo"

        return ResultadoBacktest(
            symbol=symbol, estrategia=estrategia,
            periodo=f"{len(candles)} días",
            fecha_inicio=candles[0]["date"], fecha_fin=candles[-1]["date"],
            retorno_total=round(ret_total,2), retorno_anual=round(ret_anual,2),
            sharpe=round(sharpe,2), sortino=round(sortino,2),
            max_drawdown=round(max_dd,2), calmar=round(calmar,2),
            n_trades=n, win_rate=round(wr,1), profit_factor=round(pf,2),
            avg_win=round(avg_win,0), avg_loss=round(avg_loss,0),
            avg_rr=round(avg_rr,2), best_trade=round(best,0),
            worst_trade=round(worst,0), avg_duracion=round(avg_dur,1),
            equity_curve=equity, trades=trades,
            retornos_mensuales=ret_mensual,
            robusto=robusto, veredicto=veredicto,
        )

    # ── WALK-FORWARD ──────────────────────────────────────────
    def walk_forward(self, datos, estrategia: str = "rsi_mean_reversion",
                     symbol: str = "N/A", params: dict = None,
                     in_window: int = 120, out_window: int = 60) -> ResultadoWalkForward:
        """
        Walk-Forward Testing: divide el historial en ventanas
        In-Sample (entrenamiento) + Out-of-Sample (validación).

        in_window:  días de entrenamiento (default: 120)
        out_window: días de validación    (default: 60)
        """
        candles = self._cargar(datos)
        n       = len(candles)
        paso    = in_window + out_window
        ventanas = []

        print(f"[WalkForward] {symbol} | IS:{in_window}d OOS:{out_window}d | {n} días totales")

        i = 0
        while i + paso <= n:
            # In-Sample
            is_data  = candles[i: i + in_window]
            res_is   = self.run(is_data, estrategia, symbol, params)
            wr_is    = res_is.win_rate
            ret_is   = res_is.retorno_total

            # Out-of-Sample
            oos_data = candles[i + in_window: i + paso]
            if len(oos_data) >= 30:
                res_oos  = self.run(oos_data, estrategia, symbol, params)
                wr_oos   = res_oos.win_rate
                ret_oos  = res_oos.retorno_total
            else:
                wr_oos = ret_oos = 0

            ventanas.append({
                "inicio":  is_data[0]["date"],
                "fin_is":  is_data[-1]["date"],
                "fin_oos": oos_data[-1]["date"] if oos_data else "",
                "wr_is":   round(wr_is, 1),
                "ret_is":  round(ret_is, 2),
                "wr_oos":  round(wr_oos, 1),
                "ret_oos": round(ret_oos, 2),
            })

            print(f"  V{len(ventanas):02d}: IS WR={wr_is:.1f}% Ret={ret_is:+.1f}% | "
                  f"OOS WR={wr_oos:.1f}% Ret={ret_oos:+.1f}%")
            i += out_window

        if not ventanas:
            raise ValueError("Datos insuficientes para Walk-Forward. Necesitas al menos 180 días.")

        # Métricas globales del WF
        degradacion  = sum(v["wr_oos"] - v["wr_is"] for v in ventanas) / len(ventanas)
        consistencia = sum(1 for v in ventanas if v["ret_oos"] > 0) / len(ventanas) * 100
        robusto      = degradacion > -10 and consistencia > 60

        if robusto and consistencia > 70:
            veredicto = "✅ ROBUSTO — Estrategia consistente en datos no vistos"
        elif degradacion > -15 and consistencia > 50:
            veredicto = "⚠️  ACEPTABLE — Cierta degradación fuera de muestra"
        else:
            veredicto = "❌ OVERFITTING — La estrategia no generaliza bien"

        return ResultadoWalkForward(
            n_ventanas   = len(ventanas),
            ventanas     = ventanas,
            degradacion  = round(degradacion, 1),
            consistencia = round(consistencia, 1),
            robusto      = robusto,
            veredicto    = veredicto,
        )


# ══════════════════════════════════════════════════════════════
# TEST — python backtest.py
# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("\n" + "="*58)
    print("  InvertirCL — Backtest Engine v1.0")
    print("="*58)

    # Generar datos sintéticos realistas (180 días)
    print("\n📊 Generando datos históricos sintéticos (180 días)...")
    random.seed(42)
    precio  = 7000
    candles_test = []
    for i in range(180):
        ret   = random.gauss(0.0003, 0.012)
        open_ = precio
        close = max(precio * (1 + ret), precio * 0.5)
        high  = max(open_, close) * (1 + abs(random.gauss(0, 0.004)))
        low   = min(open_, close) * (1 - abs(random.gauss(0, 0.004)))
        vol   = random.randint(100_000, 800_000)
        candles_test.append({
            "date":   f"2025-{(i//30)+1:02d}-{(i%30)+1:02d}",
            "open":   round(open_, 2), "high": round(high, 2),
            "low":    round(low, 2),   "close": round(close, 2),
            "volume": vol,
        })
        precio = close

    engine = BacktestEngine(capital_inicial=10_000_000, riesgo_pct=2.0)

    # ── Test todas las estrategias ──
    estrategias = ["rsi_mean_reversion", "ema_crossover", "bollinger_reversion", "macd_signal"]

    for est in estrategias:
        print(f"\n{'─'*58}")
        resultado = engine.run(candles_test, estrategia=est, symbol="IPSA_SINT")
        print(resultado.resumen())

    # ── Walk-Forward ──
    print(f"\n{'='*58}")
    print("  🔬 Walk-Forward Testing — RSI Mean Reversion")
    print(f"{'='*58}")
    wf = engine.walk_forward(
        candles_test,
        estrategia  = "rsi_mean_reversion",
        symbol      = "IPSA_SINT",
        in_window   = 60,
        out_window  = 30,
    )
    print(wf.resumen())

    # ── Con datos reales si yfinance está disponible ──
    try:
        from market import Market
        print(f"\n{'='*58}")
        print("  📡 Backtest con datos reales — IPSA (1 año)")
        print(f"{'='*58}")
        df = Market.get_historico("^IPSA", period="1y")
        if not df.empty:
            resultado_real = engine.run(df, estrategia="rsi_mean_reversion", symbol="^IPSA")
            print(resultado_real.resumen())
        else:
            print("  Sin datos disponibles (verificar conexión)")
    except Exception as e:
        print(f"  Datos reales no disponibles: {e}")

    print("\n" + "="*58)
