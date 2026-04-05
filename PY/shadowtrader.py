"""
InvertirCL — Bot Copiloto (Shadow Trader) v1.0
================================================
Item 11 del Roadmap.

Un bot opera en paralelo al usuario siguiendo reglas de disciplina
perfecta. El usuario ve en tiempo real su costo emocional vs el bot.

El bot NUNCA:
  - Mueve el stop loss
  - Hace revenge trading
  - Opera sin stop loss
  - Hace overtrading

El bot SIEMPRE:
  - Respeta su estrategia definida
  - Calcula el position sizing correcto
  - Documenta cada decisión
  - Espera setups de alta probabilidad

ESTRATEGIAS DISPONIBLES:
  1. MeanReversion  — RSI sobrevendido + EMA alineada
  2. Momentum       — Cruce EMA + volumen alto
  3. Breakout       — Ruptura de rango con confirmación

USO:
  from shadowtrader import ShadowTrader
  from market import Market

  bot = ShadowTrader(
      estrategia     = "mean_reversion",
      capital_inicial = 10_000_000,
      riesgo_pct     = 2.0,
  )

  # Dar datos de mercado al bot
  historico = Market.get_historico("^IPSA", period="3mo")
  señales   = bot.analizar(historico)

  # Ver performance del bot vs usuario
  reporte = bot.comparar_vs_usuario(trades_usuario=[...])
  print(reporte.resumen())
"""

import math
import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

try:
    import pandas as pd
    import numpy as np
    LIBS_OK = True
except ImportError:
    LIBS_OK = False
    print("[ShadowTrader] Instala dependencias: pip install pandas numpy")


# ══════════════════════════════════════════════════════════════
# ESTRUCTURAS
# ══════════════════════════════════════════════════════════════
@dataclass
class Señal:
    """Señal generada por el bot."""
    timestamp:   str
    symbol:      str
    side:        str          # "buy" | "sell"
    tipo:        str          # estrategia que la generó
    precio:      float
    stop_loss:   float
    take_profit: float
    cantidad:    int
    capital_riesgo: float     # $ en riesgo
    rr_ratio:    float
    confianza:   float        # 0-1
    razon:       str          # explicación de la señal
    ejecutada:   bool = False
    pnl:         Optional[float] = None

    def resumen(self):
        icon = "▲" if self.side == "buy" else "▼"
        return (
            f"  {icon} {self.tipo.upper()} {self.symbol} @ ${self.precio:,.0f} | "
            f"SL:${self.stop_loss:,.0f} TP:${self.take_profit:,.0f} | "
            f"R/R:{self.rr_ratio:.1f} | Conf:{self.confianza*100:.0f}% | "
            f"PnL:{f'${self.pnl:+,.0f}' if self.pnl is not None else 'abierto'}"
        )


@dataclass
class EstadoBot:
    """Estado completo del bot en un momento dado."""
    capital:       float
    capital_inicial: float
    trades:        list = field(default_factory=list)
    señales:       list = field(default_factory=list)
    posicion_actual: Optional[Señal] = None

    @property
    def retorno_pct(self):
        return (self.capital - self.capital_inicial) / self.capital_inicial * 100

    @property
    def trades_cerrados(self):
        return [t for t in self.trades if t.pnl is not None]

    @property
    def win_rate(self):
        cerrados = self.trades_cerrados
        if not cerrados:
            return 0.0
        wins = len([t for t in cerrados if t.pnl > 0])
        return wins / len(cerrados) * 100

    @property
    def profit_factor(self):
        cerrados = self.trades_cerrados
        ganancias = sum(t.pnl for t in cerrados if t.pnl > 0)
        perdidas  = sum(-t.pnl for t in cerrados if t.pnl < 0)
        return round(ganancias / perdidas, 2) if perdidas > 0 else float('inf')

    @property
    def max_drawdown(self):
        if not self.trades_cerrados:
            return 0.0
        equity = [self.capital_inicial]
        cap    = self.capital_inicial
        for t in self.trades_cerrados:
            cap += t.pnl
            equity.append(cap)
        peak = equity[0]
        max_dd = 0.0
        for v in equity:
            if v > peak:
                peak = v
            dd = (peak - v) / peak * 100
            if dd > max_dd:
                max_dd = dd
        return round(max_dd, 2)


@dataclass
class ComparativaUsuario:
    """Comparativa bot vs usuario."""
    bot:           EstadoBot
    usuario_trades: list      # trades del usuario (formato discipline.py)
    capital_usuario: float
    capital_inicial: float

    def resumen(self):
        cap_bot  = self.bot.capital
        ret_bot  = self.bot.retorno_pct
        ret_usr  = (self.capital_usuario - self.capital_inicial) / self.capital_inicial * 100
        gap      = ret_bot - ret_usr
        n_bot    = len(self.bot.trades_cerrados)
        n_usr    = len(self.usuario_trades)

        lineas = [
            f"\n{'='*58}",
            f"  📊 COMPARATIVA: Bot vs Tú",
            f"{'='*58}",
            f"",
            f"  {'Métrica':<22} {'Bot 🤖':>14} {'Tú 👤':>14}",
            f"  {'-'*50}",
            f"  {'Capital final':<22} ${cap_bot:>12,.0f} ${self.capital_usuario:>12,.0f}",
            f"  {'Retorno':<22} {ret_bot:>+13.1f}% {ret_usr:>+13.1f}%",
            f"  {'Trades':<22} {n_bot:>14} {n_usr:>14}",
            f"  {'Win Rate':<22} {self.bot.win_rate:>13.1f}% {'N/A':>14}",
            f"  {'Profit Factor':<22} {self.bot.profit_factor:>14.2f} {'N/A':>14}",
            f"  {'Max Drawdown':<22} {self.bot.max_drawdown:>13.1f}% {'N/A':>14}",
            f"",
            f"  {'='*50}",
            f"  Costo emocional: {abs(gap):.1f}% {'(el bot te ganó)' if gap > 0 else '(le ganaste al bot)'}",
            f"  {'='*50}",
        ]

        if gap > 5:
            lineas += [
                f"",
                f"  💡 El bot te ganó {gap:.1f}% operando con disciplina perfecta.",
                f"     Cada vez que moviste el stop o hiciste revenge trading,",
                f"     el bot siguió su plan. Ese es tu costo emocional.",
            ]
        elif gap <= 0:
            lineas += [
                f"",
                f"  🎉 ¡Le ganaste al bot! Eso es excepcional.",
                f"     Significa que tu criterio humano superó las reglas mecánicas.",
            ]

        lineas.append(f"{'='*58}")
        return "\n".join(lineas)


# ══════════════════════════════════════════════════════════════
# INDICADORES (versión simplificada para el bot)
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
# ESTRATEGIAS
# ══════════════════════════════════════════════════════════════
class EstrategiaMeanReversion:
    """
    Compra cuando RSI < 30 (sobrevendido) + EMA8 > EMA21 (tendencia alcista).
    Vende cuando RSI > 70 o precio alcanza take profit.
    Stop loss basado en ATR × 1.5.
    """
    nombre = "mean_reversion"
    descripcion = "RSI Sobrevendido + Tendencia Alcista"

    @staticmethod
    def evaluar(candles: list, closes: list) -> Optional[dict]:
        if len(closes) < 25:
            return None
        rsi   = _rsi(closes, 14)
        ema8  = _ema(closes, 8)
        ema21 = _ema(closes, 21)
        atr   = _atr(candles, 14)
        precio = closes[-1]

        # Condición de entrada
        if rsi < 30 and ema8 > ema21:
            stop      = precio - atr * 1.5
            tp        = precio + atr * 3.0
            rr        = (tp - precio) / (precio - stop) if precio > stop else 0
            confianza = min(1.0, (30 - rsi) / 30 * 0.6 + 0.4)  # más sobrevendido = más confianza
            return {
                "side": "buy", "precio": precio,
                "stop_loss": round(stop, 2), "take_profit": round(tp, 2),
                "rr_ratio": round(rr, 2), "confianza": round(confianza, 2),
                "razon": f"RSI({rsi:.1f}) sobrevendido + EMA8(${ema8:,.0f}) > EMA21(${ema21:,.0f}). ATR=${atr:.0f}",
            }
        return None

    @staticmethod
    def evaluar_salida(candles: list, closes: list) -> Optional[str]:
        if len(closes) < 15:
            return None
        rsi = _rsi(closes, 14)
        if rsi > 70:
            return f"RSI sobrecomprado ({rsi:.1f}) — objetivo alcanzado"
        return None


class EstrategiaMomentum:
    """
    Compra cuando EMA9 cruza sobre EMA21 con volumen sobre promedio.
    Señal de momentum institucional.
    """
    nombre = "momentum"
    descripcion = "Cruce EMA + Volumen Institucional"

    @staticmethod
    def evaluar(candles: list, closes: list) -> Optional[dict]:
        if len(closes) < 25 or len(candles) < 22:
            return None

        ema9_prev  = _ema(closes[:-1], 9)
        ema21_prev = _ema(closes[:-1], 21)
        ema9_now   = _ema(closes, 9)
        ema21_now  = _ema(closes, 21)
        atr        = _atr(candles, 14)
        precio     = closes[-1]

        # Volumen relativo
        vols     = [c.get("volume", 1) for c in candles[-20:]]
        vol_avg  = sum(vols) / len(vols) if vols else 1
        vol_act  = candles[-1].get("volume", vol_avg)
        vol_rel  = vol_act / vol_avg if vol_avg > 0 else 1

        # Cruce EMA9 sobre EMA21 con volumen alto
        cruce_alcista = ema9_prev < ema21_prev and ema9_now > ema21_now
        if cruce_alcista and vol_rel > 1.3:
            stop      = precio - atr * 2.0
            tp        = precio + atr * 4.0
            rr        = (tp - precio) / (precio - stop) if precio > stop else 0
            confianza = min(1.0, 0.5 + (vol_rel - 1.3) * 0.3)
            return {
                "side": "buy", "precio": precio,
                "stop_loss": round(stop, 2), "take_profit": round(tp, 2),
                "rr_ratio": round(rr, 2), "confianza": round(confianza, 2),
                "razon": f"Cruce EMA9 sobre EMA21 + Volumen {vol_rel:.1f}x el promedio. Momentum institucional.",
            }
        return None

    @staticmethod
    def evaluar_salida(candles: list, closes: list) -> Optional[str]:
        if len(closes) < 10:
            return None
        ema9  = _ema(closes, 9)
        ema21 = _ema(closes, 21)
        if ema9 < ema21:
            return f"EMA9 cruzó bajo EMA21 — momentum revertido"
        return None


class EstrategiaBreakout:
    """
    Compra cuando el precio rompe el máximo de los últimos 20 días
    con volumen de confirmación. Clásica estrategia de breakout.
    """
    nombre = "breakout"
    descripcion = "Ruptura de Máximos con Volumen"

    @staticmethod
    def evaluar(candles: list, closes: list) -> Optional[dict]:
        if len(closes) < 22 or len(candles) < 22:
            return None

        max_20  = max(c["high"] for c in candles[-21:-1])
        precio  = closes[-1]
        atr     = _atr(candles, 14)
        vols    = [c.get("volume", 1) for c in candles[-20:]]
        vol_avg = sum(vols[:-1]) / len(vols[:-1]) if len(vols) > 1 else 1
        vol_act = candles[-1].get("volume", vol_avg)
        vol_rel = vol_act / vol_avg if vol_avg > 0 else 1

        # Ruptura del máximo de 20 días con volumen
        if precio > max_20 * 1.002 and vol_rel > 1.5:
            stop      = max_20 - atr * 0.5   # stop bajo el nivel roto
            tp        = precio + (precio - stop) * 2.5
            rr        = (tp - precio) / (precio - stop) if precio > stop else 0
            confianza = min(1.0, 0.6 + (vol_rel - 1.5) * 0.15)
            return {
                "side": "buy", "precio": precio,
                "stop_loss": round(stop, 2), "take_profit": round(tp, 2),
                "rr_ratio": round(rr, 2), "confianza": round(confianza, 2),
                "razon": f"Ruptura del máximo de 20 días (${max_20:,.0f}) con volumen {vol_rel:.1f}x.",
            }
        return None

    @staticmethod
    def evaluar_salida(candles: list, closes: list) -> Optional[str]:
        return None  # Solo sale por stop o TP


ESTRATEGIAS = {
    "mean_reversion": EstrategiaMeanReversion,
    "momentum":       EstrategiaMomentum,
    "breakout":       EstrategiaBreakout,
}


# ══════════════════════════════════════════════════════════════
# BOT PRINCIPAL
# ══════════════════════════════════════════════════════════════
class ShadowTrader:
    """
    Bot copiloto que opera en paralelo al usuario con disciplina perfecta.
    Sirve como benchmark para medir el costo emocional del usuario.
    """

    def __init__(
        self,
        estrategia:      str   = "mean_reversion",
        capital_inicial: float = 10_000_000,
        riesgo_pct:      float = 2.0,
        symbol:          str   = "IPSA",
    ):
        if estrategia not in ESTRATEGIAS:
            raise ValueError(f"Estrategia '{estrategia}' no existe. Opciones: {list(ESTRATEGIAS.keys())}")

        self.estrategia_cls  = ESTRATEGIAS[estrategia]
        self.estrategia_id   = estrategia
        self.capital_inicial = capital_inicial
        self.riesgo_pct      = riesgo_pct
        self.symbol          = symbol
        self.estado          = EstadoBot(
            capital          = capital_inicial,
            capital_inicial  = capital_inicial,
        )
        print(f"[ShadowTrader] Bot iniciado — Estrategia: {self.estrategia_cls.descripcion}")
        print(f"[ShadowTrader] Capital: ${capital_inicial:,.0f} | Riesgo max: {riesgo_pct}%/trade")

    # ── ANALIZAR HISTÓRICO ────────────────────────────────────
    def analizar(self, datos) -> list:
        """
        Analizar datos históricos y generar señales.
        datos: list de dicts {date, open, high, low, close, volume}
              o DataFrame de pandas con esas columnas.
        Retorna lista de Señales generadas.
        """
        # Convertir DataFrame si es necesario
        if LIBS_OK and isinstance(datos, pd.DataFrame):
            candles = datos[["open","high","low","close","volume"]].rename(
                columns=str.lower
            ).to_dict("records")
        else:
            candles = [
                {"open":   float(d.get("open",   d.get("Open",   0))),
                 "high":   float(d.get("high",   d.get("High",   0))),
                 "low":    float(d.get("low",    d.get("Low",    0))),
                 "close":  float(d.get("close",  d.get("Close",  0))),
                 "volume": float(d.get("volume", d.get("Volume", 0)))}
                for d in datos
            ]

        señales_generadas = []
        posicion_abierta  = None
        capital           = self.capital_inicial

        for i in range(25, len(candles)):
            window   = candles[:i+1]
            closes   = [c["close"] for c in window]
            precio   = closes[-1]
            fecha    = datos[i].get("date", datos[i].get("Date", f"día_{i}")) if isinstance(datos[i], dict) else str(i)

            # ── Evaluar salida si hay posición abierta ──
            if posicion_abierta:
                # ¿Stop loss tocado?
                if candles[i]["low"] <= posicion_abierta.stop_loss:
                    pnl = (posicion_abierta.stop_loss - posicion_abierta.precio) * posicion_abierta.cantidad
                    posicion_abierta.pnl      = round(pnl, 0)
                    posicion_abierta.ejecutada = True
                    capital += pnl
                    self.estado.trades.append(posicion_abierta)
                    print(f"  [Bot] STOP LOSS alcanzado @ ${posicion_abierta.stop_loss:,.0f} | PnL: ${pnl:+,.0f}")
                    posicion_abierta = None
                    continue

                # ¿Take profit tocado?
                elif candles[i]["high"] >= posicion_abierta.take_profit:
                    pnl = (posicion_abierta.take_profit - posicion_abierta.precio) * posicion_abierta.cantidad
                    posicion_abierta.pnl      = round(pnl, 0)
                    posicion_abierta.ejecutada = True
                    capital += pnl
                    self.estado.trades.append(posicion_abierta)
                    print(f"  [Bot] TAKE PROFIT alcanzado @ ${posicion_abierta.take_profit:,.0f} | PnL: ${pnl:+,.0f}")
                    posicion_abierta = None
                    continue

                # ¿Señal de salida por estrategia?
                else:
                    razon_salida = self.estrategia_cls.evaluar_salida(window, closes)
                    if razon_salida:
                        pnl = (precio - posicion_abierta.precio) * posicion_abierta.cantidad
                        posicion_abierta.pnl      = round(pnl, 0)
                        posicion_abierta.ejecutada = True
                        capital += pnl
                        self.estado.trades.append(posicion_abierta)
                        print(f"  [Bot] SALIDA ESTRATEGIA: {razon_salida} | PnL: ${pnl:+,.0f}")
                        posicion_abierta = None
                        continue

            # ── Evaluar nueva entrada si no hay posición ──
            if not posicion_abierta:
                señal_data = self.estrategia_cls.evaluar(window, closes)
                if señal_data and señal_data.get("rr_ratio", 0) >= 1.5:
                    # Position sizing: riesgo_pct del capital
                    riesgo_max = capital * self.riesgo_pct / 100
                    riesgo_por_accion = abs(señal_data["precio"] - señal_data["stop_loss"])
                    cantidad = max(1, int(riesgo_max / riesgo_por_accion)) if riesgo_por_accion > 0 else 1

                    señal = Señal(
                        timestamp    = str(fecha),
                        symbol       = self.symbol,
                        side         = señal_data["side"],
                        tipo         = self.estrategia_id,
                        precio       = señal_data["precio"],
                        stop_loss    = señal_data["stop_loss"],
                        take_profit  = señal_data["take_profit"],
                        cantidad     = cantidad,
                        capital_riesgo = round(riesgo_max, 0),
                        rr_ratio     = señal_data["rr_ratio"],
                        confianza    = señal_data["confianza"],
                        razon        = señal_data["razon"],
                    )
                    posicion_abierta = señal
                    señales_generadas.append(señal)
                    self.estado.señales.append(señal)
                    print(f"  [Bot] ENTRADA: {señal.resumen()}")

        # Cerrar posición abierta al final del histórico si quedó
        if posicion_abierta:
            ultimo_precio = candles[-1]["close"]
            pnl = (ultimo_precio - posicion_abierta.precio) * posicion_abierta.cantidad
            posicion_abierta.pnl = round(pnl, 0)
            capital += pnl
            self.estado.trades.append(posicion_abierta)

        self.estado.capital = round(capital, 0)
        self.estado.posicion_actual = posicion_abierta
        return señales_generadas

    # ── COMPARAR VS USUARIO ───────────────────────────────────
    def comparar_vs_usuario(
        self,
        trades_usuario:  list,
        capital_usuario: float,
    ) -> ComparativaUsuario:
        """
        Genera comparativa bot vs usuario.
        trades_usuario: lista de trades del usuario (formato discipline.py)
        capital_usuario: capital actual del usuario
        """
        return ComparativaUsuario(
            bot             = self.estado,
            usuario_trades  = trades_usuario,
            capital_usuario = capital_usuario,
            capital_inicial = self.capital_inicial,
        )

    # ── REPORTE DEL BOT ───────────────────────────────────────
    def reporte(self) -> str:
        e = self.estado
        cerrados = e.trades_cerrados
        lineas = [
            f"\n{'='*55}",
            f"  🤖 REPORTE BOT COPILOTO — {self.estrategia_cls.descripcion}",
            f"{'='*55}",
            f"  Capital inicial:  ${e.capital_inicial:>12,.0f}",
            f"  Capital actual:   ${e.capital:>12,.0f}",
            f"  Retorno:          {e.retorno_pct:>+12.1f}%",
            f"  Trades totales:   {len(e.trades):>12}",
            f"  Trades cerrados:  {len(cerrados):>12}",
        ]
        if cerrados:
            wins = len([t for t in cerrados if t.pnl > 0])
            lineas += [
                f"  Win Rate:         {e.win_rate:>12.1f}%",
                f"  Profit Factor:    {e.profit_factor:>12.2f}x",
                f"  Max Drawdown:     {e.max_drawdown:>12.1f}%",
                f"  Ganados:          {wins:>12}",
                f"  Perdidos:         {len(cerrados)-wins:>12}",
            ]
        lineas += [
            f"\n  Reglas del bot (NUNCA violadas):",
            f"  ✅ Stop loss siempre definido antes de entrar",
            f"  ✅ Stop loss NUNCA movido",
            f"  ✅ Máximo {self.riesgo_pct}% del capital por trade",
            f"  ✅ R/R mínimo 1.5:1 para entrar",
            f"  ✅ Sin revenge trading",
            f"  ✅ Sin overtrading",
            f"{'='*55}",
        ]

        if cerrados:
            lineas.append(f"\n  Últimos 5 trades:")
            for t in cerrados[-5:]:
                lineas.append(f"  {t.resumen()}")

        return "\n".join(lineas)


# ══════════════════════════════════════════════════════════════
# TEST — python shadowtrader.py
# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("\n" + "="*55)
    print("  InvertirCL — Bot Copiloto (Shadow Trader) v1.0")
    print("="*55)

    # Generar datos históricos sintéticos realistas
    print("\n📊 Generando datos históricos sintéticos (90 días)...")
    random.seed(42)
    precio  = 7000
    candles = []
    for i in range(90):
        ret   = random.gauss(0.0003, 0.012)  # drift positivo leve
        open_ = precio
        close = precio * (1 + ret)
        high  = max(open_, close) * (1 + random.uniform(0, 0.008))
        low   = min(open_, close) * (1 - random.uniform(0, 0.008))
        vol   = random.randint(100_000, 800_000)
        candles.append({
            "date":   f"2026-{(i//30)+1:02d}-{(i%30)+1:02d}",
            "open":   round(open_, 1),
            "high":   round(high, 1),
            "low":    round(low, 1),
            "close":  round(close, 1),
            "volume": vol,
        })
        precio = close

    # ── Test las 3 estrategias ──
    for estrategia_id in ["mean_reversion", "momentum", "breakout"]:
        print(f"\n{'─'*55}")
        print(f"  Probando estrategia: {ESTRATEGIAS[estrategia_id].descripcion}")
        print(f"{'─'*55}")

        bot = ShadowTrader(
            estrategia      = estrategia_id,
            capital_inicial = 10_000_000,
            riesgo_pct      = 2.0,
        )
        señales = bot.analizar(candles)
        print(bot.reporte())

    # ── Comparativa vs usuario ficticio ──
    print("\n" + "="*55)
    print("  Comparativa Bot vs Usuario")
    print("="*55)

    bot_final = ShadowTrader(
        estrategia="mean_reversion",
        capital_inicial=10_000_000,
        riesgo_pct=2.0
    )
    bot_final.analizar(candles)

    # Usuario que cometió errores de disciplina
    trades_usuario_simulados = [
        {"pnl": -150_000},   # perdió por revenge trading
        {"pnl":  80_000},
        {"pnl": -200_000},   # movió el stop
        {"pnl":  45_000},
        {"pnl": -90_000},    # overtrading
    ]
    capital_usuario = 10_000_000 + sum(t["pnl"] for t in trades_usuario_simulados)

    comparativa = bot_final.comparar_vs_usuario(
        trades_usuario  = trades_usuario_simulados,
        capital_usuario = capital_usuario,
    )
    print(comparativa.resumen())
    print()
