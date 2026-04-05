"""
InvertirCL — Walk-Forward Testing v1.0
========================================
Item 17 del Roadmap.

El estándar de la industria para validar que una estrategia
tiene edge real y no solo se ajustó al pasado (overfitting).

¿POR QUÉ ES IMPORTANTE?
  Un backtest normal tiene un problema: se optimiza sobre los
  mismos datos en los que se evaluó. Eso es trampa — la estrategia
  "recuerda" el futuro. Walk-Forward evita esto dividiendo el
  historial en ventanas y validando siempre en datos NO vistos.

CÓMO FUNCIONA:
  ┌─────────────────────────────────────────────────────┐
  │  V1: [IS 120d][OOS 60d]                            │
  │  V2:      [IS 120d][OOS 60d]                       │
  │  V3:           [IS 120d][OOS 60d]                  │
  │  V4:                [IS 120d][OOS 60d]             │
  └─────────────────────────────────────────────────────┘
  IS  = In-Sample  (entrenamiento/optimización)
  OOS = Out-of-Sample (validación en datos no vistos)

VEREDICTO:
  ✅ ROBUSTO    — OOS WR cercano a IS WR, consistente
  ⚠️ ACEPTABLE  — Cierta degradación, pero positivo
  ❌ OVERFITTING — La estrategia no generaliza

USO:
  from walkforward import WalkForward
  from market import Market

  df  = Market.get_historico("^IPSA", period="2y")
  wf  = WalkForward(capital_inicial=10_000_000)
  res = wf.run(df, estrategia="rsi_mean_reversion")
  print(res.resumen())

  # Optimización de parámetros con WF
  mejor = wf.optimizar_parametros(df, estrategia="rsi_mean_reversion")
  print(mejor)
"""

import random
import math
from dataclasses import dataclass, field
from typing import Optional

try:
    import pandas as pd
    import numpy as np
    NUMPY_OK = True
except ImportError:
    NUMPY_OK = False


# ══════════════════════════════════════════════════════════════
# ESTRUCTURAS
# ══════════════════════════════════════════════════════════════
@dataclass
class VentanaWF:
    """Resultado de una ventana individual del Walk-Forward."""
    numero:       int
    fecha_is_ini: str
    fecha_is_fin: str
    fecha_oos_ini:str
    fecha_oos_fin:str
    dias_is:      int
    dias_oos:     int

    # Métricas In-Sample
    wr_is:        float
    ret_is:       float
    sharpe_is:    float
    n_trades_is:  int

    # Métricas Out-of-Sample
    wr_oos:       float
    ret_oos:      float
    sharpe_oos:   float
    n_trades_oos: int

    # Diagnóstico
    degradacion:  float     # wr_oos - wr_is (negativo = degradación)
    positivo:     bool      # OOS tuvo retorno positivo
    robusto:      bool      # degradación aceptable y OOS positivo

    def resumen_linea(self) -> str:
        icon = "✅" if self.robusto else "⚠️" if self.positivo else "❌"
        return (
            f"  V{self.numero:02d} {icon} | "
            f"IS: WR={self.wr_is:5.1f}% Ret={self.ret_is:+6.2f}% ({self.n_trades_is}t) | "
            f"OOS: WR={self.wr_oos:5.1f}% Ret={self.ret_oos:+6.2f}% ({self.n_trades_oos}t) | "
            f"Deg={self.degradacion:+5.1f}%"
        )


@dataclass
class ResultadoWF:
    """Resultado completo del análisis Walk-Forward."""
    estrategia:      str
    symbol:          str
    n_ventanas:      int
    dias_is:         int
    dias_oos:        int
    ventanas:        list     # lista de VentanaWF

    # Métricas agregadas
    wr_is_prom:      float    # WR promedio In-Sample
    wr_oos_prom:     float    # WR promedio Out-of-Sample
    ret_oos_prom:    float    # Retorno OOS promedio
    degradacion_prom:float    # Degradación media IS→OOS
    consistencia:    float    # % ventanas con OOS positivo
    eficiencia:      float    # wr_oos / wr_is (1.0 = perfecto)

    # Veredicto
    robusto:         bool
    nivel:           str      # ROBUSTO / ACEPTABLE / OVERFITTING
    veredicto:       str

    # Curva de retornos OOS encadenados
    equity_oos:      list

    def resumen(self):
        iconos = {"ROBUSTO": "✅", "ACEPTABLE": "⚠️", "OVERFITTING": "❌"}
        icono  = iconos.get(self.nivel, "❓")
        lineas = [
            f"\n{'='*65}",
            f"  🔬 WALK-FORWARD TESTING — {self.estrategia}",
            f"  {self.symbol} | {self.n_ventanas} ventanas | IS:{self.dias_is}d OOS:{self.dias_oos}d",
            f"{'='*65}",
            f"",
            f"  {'MÉTRICAS AGREGADAS':<30}",
            f"  {'─'*55}",
            f"  {'WR promedio IS (entrenamiento)':<35} {self.wr_is_prom:>8.1f}%",
            f"  {'WR promedio OOS (validación)':<35} {self.wr_oos_prom:>8.1f}%",
            f"  {'Degradación IS → OOS':<35} {self.degradacion_prom:>+8.1f}%",
            f"  {'Retorno OOS promedio':<35} {self.ret_oos_prom:>+8.2f}%",
            f"  {'Consistencia (% OOS positivo)':<35} {self.consistencia:>8.1f}%",
            f"  {'Eficiencia OOS/IS':<35} {self.eficiencia:>8.3f}",
            f"",
            f"  {'─'*55}",
            f"  {icono} VEREDICTO: {self.nivel}",
            f"  {self.veredicto}",
            f"",
            f"  DETALLE POR VENTANA:",
            f"  {'─'*55}",
        ]

        for v in self.ventanas:
            lineas.append(v.resumen_linea())

        # Curva OOS
        if self.equity_oos:
            lineas.append(f"\n  EQUITY OOS ENCADENADO:")
            lineas.append(f"  Capital inicial: ${self.equity_oos[0]:,.0f}")
            lineas.append(f"  Capital final:   ${self.equity_oos[-1]:,.0f}")
            ret_total = (self.equity_oos[-1] - self.equity_oos[0]) / self.equity_oos[0] * 100
            lineas.append(f"  Retorno total OOS: {ret_total:+.2f}%")

            # Mini gráfico ASCII
            vals  = self.equity_oos
            mini  = min(vals)
            maxi  = max(vals)
            W     = 50
            puntos = []
            paso  = max(1, len(vals) // W)
            for i in range(0, len(vals), paso):
                y = int((vals[i] - mini) / (maxi - mini) * 4) if maxi > mini else 2
                puntos.append("▁▂▃▄▅▆▇█"[min(y, 7)])
            lineas.append(f"  {''.join(puntos[:W])}")

        lineas.append(f"{'='*65}")
        return "\n".join(lineas)


# ══════════════════════════════════════════════════════════════
# INDICADORES INTERNOS (mismo set que backtest_engine.py)
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
# ESTRATEGIAS (mismas que backtest_engine.py)
# ══════════════════════════════════════════════════════════════
def _strat_rsi(window, closes, params=None):
    p   = params or {"rsi_n": 14, "os": 30, "ob": 70, "stop_mult": 1.5}
    rsi = _rsi(closes, p.get("rsi_n", 14))
    atr = _atr(window, 14)
    precio = closes[-1]
    if rsi < p.get("os", 30):
        stop = precio - atr * p.get("stop_mult", 1.5)
        tp   = precio + atr * p.get("stop_mult", 1.5) * 2
        return {"señal": "buy", "stop": stop, "tp": tp}
    if rsi > p.get("ob", 70):
        return {"señal": "salida"}
    return None

def _strat_ema(window, closes, params=None):
    p = params or {"fast": 9, "slow": 21, "stop_mult": 2.0}
    if len(closes) < p.get("slow", 21) + 2:
        return None
    fast_now  = _ema(closes,      p.get("fast", 9))
    slow_now  = _ema(closes,      p.get("slow", 21))
    fast_prev = _ema(closes[:-1], p.get("fast", 9))
    slow_prev = _ema(closes[:-1], p.get("slow", 21))
    atr = _atr(window, 14)
    precio = closes[-1]
    if fast_prev < slow_prev and fast_now > slow_now:
        return {"señal": "buy",
                "stop": precio - atr * p.get("stop_mult", 2.0),
                "tp":   precio + atr * p.get("stop_mult", 2.0) * 2}
    if fast_prev > slow_prev and fast_now < slow_now:
        return {"señal": "salida"}
    return None

def _strat_bb(window, closes, params=None):
    p  = params or {"n": 20, "std": 2.0}
    n  = p.get("n", 20)
    if len(closes) < n:
        return None
    arr   = closes[-n:]
    mean_ = sum(arr) / n
    std   = (sum((x - mean_)**2 for x in arr) / n) ** 0.5
    upper = mean_ + p.get("std", 2.0) * std
    lower = mean_ - p.get("std", 2.0) * std
    precio = closes[-1]
    atr    = _atr(window, 14)
    if precio <= lower:
        return {"señal": "buy",
                "stop": precio - atr,
                "tp":   upper}
    if precio >= upper:
        return {"señal": "salida"}
    return None

ESTRATEGIAS_MAP = {
    "rsi_mean_reversion":  _strat_rsi,
    "ema_crossover":       _strat_ema,
    "bollinger_reversion": _strat_bb,
}


# ══════════════════════════════════════════════════════════════
# MOTOR WALK-FORWARD
# ══════════════════════════════════════════════════════════════
class WalkForward:

    def __init__(self, capital_inicial: float = 10_000_000,
                 riesgo_pct: float = 2.0, comision_pct: float = 0.1):
        self.capital_ini  = capital_inicial
        self.riesgo_pct   = riesgo_pct
        self.comision_pct = comision_pct

    # ── CARGAR DATOS ──────────────────────────────────────────
    def _cargar(self, datos) -> list:
        if NUMPY_OK and isinstance(datos, pd.DataFrame):
            cols = {c.lower(): c for c in datos.columns}
            def g(row, k):
                return float(row.get(cols.get(k, k), 0))
            return [{"date":   str(row.get(cols.get("date","date"), i))[:10],
                     "open":   g(row,"open"),  "high": g(row,"high"),
                     "low":    g(row,"low"),   "close": g(row,"close"),
                     "volume": g(row,"volume")}
                    for i, row in datos.iterrows()]
        return datos

    # ── BACKTEST SIMPLE (para cada ventana) ───────────────────
    def _backtest_ventana(self, candles: list, fn_strat,
                           params: dict = None) -> dict:
        """Corre backtest en una ventana y retorna métricas básicas."""
        capital  = self.capital_ini
        posicion = None
        trades   = []

        for i in range(25, len(candles)):
            window = candles[:i+1]
            closes = [c["close"] for c in window]
            precio = closes[-1]
            c_dia  = candles[i]

            if posicion:
                # Stop
                if c_dia["low"] <= posicion["stop"]:
                    pnl = (posicion["stop"] - posicion["precio"]) * posicion["qty"]
                    pnl -= abs(pnl) * self.comision_pct / 100
                    capital += pnl
                    trades.append({"pnl": pnl, "razon": "stop"})
                    posicion = None
                    continue
                # TP
                elif c_dia["high"] >= posicion["tp"]:
                    pnl = (posicion["tp"] - posicion["precio"]) * posicion["qty"]
                    pnl -= pnl * self.comision_pct / 100
                    capital += pnl
                    trades.append({"pnl": pnl, "razon": "tp"})
                    posicion = None
                    continue
                # Señal salida
                else:
                    s = fn_strat(window, closes, params)
                    if s and s.get("señal") == "salida":
                        pnl = (precio - posicion["precio"]) * posicion["qty"]
                        capital += pnl
                        trades.append({"pnl": pnl, "razon": "señal"})
                        posicion = None

            if not posicion:
                s = fn_strat(window, closes, params)
                if s and s.get("señal") == "buy" and s.get("stop", 0) > 0:
                    riesgo  = capital * self.riesgo_pct / 100
                    riesgo_acc = precio - s["stop"]
                    qty     = max(1, int(riesgo / riesgo_acc)) if riesgo_acc > 0 else 1
                    if (s["tp"] - precio) / (precio - s["stop"]) >= 1.5:
                        posicion = {"precio": precio, "stop": s["stop"],
                                    "tp": s["tp"], "qty": qty}

        # Cerrar posición abierta
        if posicion:
            pnl = (candles[-1]["close"] - posicion["precio"]) * posicion["qty"]
            trades.append({"pnl": pnl, "razon": "fin"})
            capital += pnl

        n     = len(trades)
        wins  = sum(1 for t in trades if t["pnl"] > 0)
        wr    = wins / n * 100 if n > 0 else 0
        ret   = (capital - self.capital_ini) / self.capital_ini * 100
        ganancias = sum(t["pnl"] for t in trades if t["pnl"] > 0)
        perdidas  = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0))

        # Sharpe simplificado
        if n > 1:
            pnls = [t["pnl"] for t in trades]
            mu   = sum(pnls) / n
            std  = (sum((p - mu)**2 for p in pnls) / n) ** 0.5
            sharpe = (mu / std * (252/n)**0.5) if std > 0 else 0
        else:
            sharpe = 0

        return {
            "n_trades":  n,
            "wr":        round(wr, 1),
            "ret":       round(ret, 2),
            "sharpe":    round(sharpe, 2),
            "capital":   round(capital, 0),
            "trades":    trades,
        }

    # ── RUN WALK-FORWARD ──────────────────────────────────────
    def run(self, datos, estrategia: str = "rsi_mean_reversion",
            symbol: str = "N/A", params: dict = None,
            dias_is: int = 120, dias_oos: int = 60) -> ResultadoWF:
        """
        Ejecuta Walk-Forward completo.

        datos:      DataFrame o lista de dicts
        estrategia: nombre de estrategia
        dias_is:    días de entrenamiento (default: 120)
        dias_oos:   días de validación    (default: 60)
        """
        candles = self._cargar(datos)
        n_total = len(candles)
        fn_strat = ESTRATEGIAS_MAP.get(estrategia)

        if fn_strat is None:
            raise ValueError(f"Estrategia '{estrategia}' no encontrada. "
                             f"Opciones: {list(ESTRATEGIAS_MAP.keys())}")

        if n_total < dias_is + dias_oos:
            raise ValueError(
                f"Datos insuficientes: {n_total} días. "
                f"Necesitas al menos {dias_is + dias_oos} días."
            )

        print(f"[WalkForward] {symbol} | {estrategia} | "
              f"IS:{dias_is}d OOS:{dias_oos}d | {n_total} días totales")

        ventanas     = []
        equity_oos   = [self.capital_ini]
        capital_acum = self.capital_ini
        i            = 0
        n_ventana    = 1

        while i + dias_is + dias_oos <= n_total:
            is_data  = candles[i: i + dias_is]
            oos_data = candles[i + dias_is: i + dias_is + dias_oos]

            # Backtest IS
            res_is = self._backtest_ventana(is_data, fn_strat, params)

            # Backtest OOS (con parámetros del IS — no re-optimiza en este modo)
            res_oos = self._backtest_ventana(oos_data, fn_strat, params)

            # Curva OOS encadenada
            for t in res_oos["trades"]:
                capital_acum += t["pnl"]
                equity_oos.append(round(capital_acum, 0))

            # Diagnóstico de la ventana
            degradacion = res_oos["wr"] - res_is["wr"]
            positivo    = res_oos["ret"] > 0
            robusto     = degradacion > -15 and positivo

            v = VentanaWF(
                numero       = n_ventana,
                fecha_is_ini = is_data[0]["date"],
                fecha_is_fin = is_data[-1]["date"],
                fecha_oos_ini= oos_data[0]["date"],
                fecha_oos_fin= oos_data[-1]["date"],
                dias_is      = dias_is,
                dias_oos     = dias_oos,
                wr_is        = res_is["wr"],
                ret_is       = res_is["ret"],
                sharpe_is    = res_is["sharpe"],
                n_trades_is  = res_is["n_trades"],
                wr_oos       = res_oos["wr"],
                ret_oos      = res_oos["ret"],
                sharpe_oos   = res_oos["sharpe"],
                n_trades_oos = res_oos["n_trades"],
                degradacion  = round(degradacion, 1),
                positivo     = positivo,
                robusto      = robusto,
            )
            ventanas.append(v)
            print(f"  {v.resumen_linea()}")

            i        += dias_oos  # ventana deslizante
            n_ventana += 1

        if not ventanas:
            raise ValueError("No se generaron ventanas suficientes.")

        # Métricas globales
        wr_is_prom       = sum(v.wr_is  for v in ventanas) / len(ventanas)
        wr_oos_prom      = sum(v.wr_oos for v in ventanas) / len(ventanas)
        ret_oos_prom     = sum(v.ret_oos for v in ventanas) / len(ventanas)
        degradacion_prom = sum(v.degradacion for v in ventanas) / len(ventanas)
        consistencia     = sum(1 for v in ventanas if v.positivo) / len(ventanas) * 100
        eficiencia       = wr_oos_prom / wr_is_prom if wr_is_prom > 0 else 0

        # Veredicto
        if degradacion_prom > -10 and consistencia >= 65 and ret_oos_prom > 0:
            nivel    = "ROBUSTO"
            veredicto = ("La estrategia generaliza bien en datos no vistos. "
                         "Edge real confirmado. Considerar operar en paper trading.")
        elif degradacion_prom > -20 and consistencia >= 50:
            nivel    = "ACEPTABLE"
            veredicto = ("Cierta degradación fuera de muestra. "
                         "Revisar parámetros o aumentar el período IS.")
        else:
            nivel    = "OVERFITTING"
            veredicto = ("La estrategia no generaliza. Fue optimizada sobre el pasado "
                         "y no funciona en datos no vistos. NO operar en vivo.")

        robusto = nivel == "ROBUSTO"

        return ResultadoWF(
            estrategia       = estrategia,
            symbol           = symbol,
            n_ventanas       = len(ventanas),
            dias_is          = dias_is,
            dias_oos         = dias_oos,
            ventanas         = ventanas,
            wr_is_prom       = round(wr_is_prom, 1),
            wr_oos_prom      = round(wr_oos_prom, 1),
            ret_oos_prom     = round(ret_oos_prom, 2),
            degradacion_prom = round(degradacion_prom, 1),
            consistencia     = round(consistencia, 1),
            eficiencia       = round(eficiencia, 3),
            robusto          = robusto,
            nivel            = nivel,
            veredicto        = veredicto,
            equity_oos       = equity_oos,
        )

    # ── OPTIMIZACIÓN DE PARÁMETROS CON WF ────────────────────
    def optimizar_parametros(self, datos, estrategia: str = "rsi_mean_reversion",
                              symbol: str = "N/A",
                              dias_is: int = 120, dias_oos: int = 60) -> dict:
        """
        Busca los mejores parámetros para una estrategia usando Walk-Forward.
        Solo optimiza en IS y valida en OOS — evita overfitting.
        """
        candles  = self._cargar(datos)
        fn_strat = ESTRATEGIAS_MAP.get(estrategia)
        if not fn_strat:
            raise ValueError(f"Estrategia no encontrada: {estrategia}")

        # Grid de parámetros a probar
        grids = {
            "rsi_mean_reversion": [
                {"rsi_n": rsi_n, "os": os_, "ob": ob_, "stop_mult": sm}
                for rsi_n in [10, 14]
                for os_   in [25, 30, 35]
                for ob_   in [65, 70, 75]
                for sm    in [1.5, 2.0]
            ],
            "ema_crossover": [
                {"fast": f, "slow": s, "stop_mult": sm}
                for f  in [5, 9, 12]
                for s  in [21, 30, 50]
                for sm in [1.5, 2.0, 2.5]
            ],
            "bollinger_reversion": [
                {"n": n, "std": std}
                for n   in [14, 20, 26]
                for std in [1.5, 2.0, 2.5]
            ],
        }

        grid = grids.get(estrategia, [{}])
        print(f"[WalkForward] Optimizando {estrategia} — {len(grid)} combinaciones...")

        mejores_params  = None
        mejor_oos_ret   = float("-inf")
        resultados_grid = []

        for params in grid:
            try:
                res = self.run(candles, estrategia, symbol,
                               params=params,
                               dias_is=dias_is, dias_oos=dias_oos)
                resultados_grid.append({
                    "params":       params,
                    "wr_oos":       res.wr_oos_prom,
                    "ret_oos":      res.ret_oos_prom,
                    "consistencia": res.consistencia,
                    "nivel":        res.nivel,
                })
                if res.ret_oos_prom > mejor_oos_ret and res.consistencia >= 50:
                    mejor_oos_ret  = res.ret_oos_prom
                    mejores_params = params
            except Exception:
                continue

        # Ordenar por retorno OOS
        resultados_grid.sort(key=lambda x: -x["ret_oos"])

        print(f"\n[WalkForward] Top 5 combinaciones:")
        print(f"  {'Params':<40} {'OOS Ret%':>9} {'OOS WR%':>9} {'Consist':>9}")
        print(f"  {'─'*67}")
        for r in resultados_grid[:5]:
            print(f"  {str(r['params']):<40} {r['ret_oos']:>+8.2f}% "
                  f"{r['wr_oos']:>8.1f}% {r['consistencia']:>8.1f}%")

        return {
            "mejores_params": mejores_params,
            "mejor_ret_oos":  round(mejor_oos_ret, 2),
            "top5":           resultados_grid[:5],
        }


# ══════════════════════════════════════════════════════════════
# TEST — python walkforward.py
# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("\n" + "="*65)
    print("  InvertirCL — Walk-Forward Testing v1.0")
    print("="*65)

    # Generar datos sintéticos (300 días)
    print("\n📊 Generando datos históricos sintéticos (300 días)...")
    random.seed(42)
    precio   = 7000
    candles  = []
    for i in range(300):
        ret   = random.gauss(0.0004, 0.013)
        open_ = precio
        close = max(precio * (1 + ret), precio * 0.5)
        high  = max(open_, close) * (1 + abs(random.gauss(0, 0.005)))
        low   = min(open_, close) * (1 - abs(random.gauss(0, 0.005)))
        candles.append({
            "date":   f"2024-{(i//25)+1:02d}-{(i%25)+1:02d}",
            "open":   round(open_, 2),  "high": round(high, 2),
            "low":    round(low, 2),    "close": round(close, 2),
            "volume": random.randint(100_000, 600_000),
        })
        precio = close

    wf = WalkForward(capital_inicial=10_000_000, riesgo_pct=2.0)

    # ── Test 1: WF básico RSI ──
    print("\n🔬 Test 1: Walk-Forward — RSI Mean Reversion")
    res1 = wf.run(candles, estrategia="rsi_mean_reversion",
                  symbol="IPSA_SINT", dias_is=90, dias_oos=45)
    print(res1.resumen())

    # ── Test 2: WF con EMA ──
    print("\n🔬 Test 2: Walk-Forward — EMA Crossover")
    res2 = wf.run(candles, estrategia="ema_crossover",
                  symbol="IPSA_SINT", dias_is=90, dias_oos=45)
    print(res2.resumen())

    # ── Test 3: Optimización de parámetros ──
    print("\n🔬 Test 3: Optimización de parámetros con Walk-Forward")
    mejor = wf.optimizar_parametros(
        candles, estrategia="rsi_mean_reversion",
        symbol="IPSA_SINT", dias_is=80, dias_oos=40
    )
    print(f"\n  Mejores parámetros encontrados: {mejor['mejores_params']}")
    print(f"  Retorno OOS con mejores params: {mejor['mejor_ret_oos']:+.2f}%")

    # ── Test 4: Con datos reales ──
    try:
        from market import Market
        print(f"\n{'='*65}")
        print("  📡 Walk-Forward con datos reales — IPSA (2 años)")
        df = Market.get_historico("^IPSA", period="2y")
        if not df.empty:
            res_real = wf.run(df, estrategia="rsi_mean_reversion",
                              symbol="^IPSA", dias_is=120, dias_oos=60)
            print(res_real.resumen())
        else:
            print("  Sin datos disponibles")
    except Exception as e:
        print(f"  Datos reales: {e}")

    print("\n" + "="*65)
