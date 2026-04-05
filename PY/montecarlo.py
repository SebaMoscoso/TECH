"""
InvertirCL — Monte Carlo Simulator v1.0
=========================================
Item 16 del Roadmap.

Proyecta 1.000 variaciones de una estrategia o portafolio
para determinar distribución de resultados posibles.

USOS:
  1. Stress test de estrategia   — ¿sobrevive la estrategia al peor 5%?
  2. Proyección de portafolio    — ¿cuánto puedo perder en 1 año?
  3. Probabilidad de ruina       — ¿cuándo se agota el capital?
  4. VaR y CVaR                  — pérdida máxima con X% confianza
  5. Horizonte de inversión      — ¿cuánto tiempo necesito?

MÉTODOS:
  1. MC sobre trades históricos  — reordena y varía trades reales
  2. MC sobre retornos           — simula retornos con distribución empírica
  3. MC geométrico (GBM)         — Geometric Brownian Motion clásico
  4. MC con fat tails            — distribución t de Student (más realista)

USO:
  from montecarlo import MonteCarlo

  mc = MonteCarlo(capital_inicial=10_000_000)

  # Sobre trades históricos
  resultado = mc.simular_trades(trades, n=1000)
  print(resultado.resumen())

  # Proyección de portafolio
  resultado = mc.proyectar_portafolio(
      retorno_anual=0.12,
      volatilidad_anual=0.15,
      años=3,
      n=1000
  )
  print(resultado.resumen())
"""

import random
import math
import statistics
from dataclasses import dataclass, field
from typing import Optional

try:
    import numpy as np
    NUMPY_OK = True
except ImportError:
    NUMPY_OK = False
    print("[MonteCarlo] numpy no instalado — usando implementación pura Python")


# ══════════════════════════════════════════════════════════════
# ESTRUCTURAS
# ══════════════════════════════════════════════════════════════
@dataclass
class ResultadoMC:
    metodo:          str
    n_simulaciones:  int
    capital_inicial: float
    horizonte_dias:  int

    # Distribución de resultados finales
    media:           float
    mediana:         float
    std:             float
    min_resultado:   float
    max_resultado:   float

    # Risk metrics
    var_95:          float    # VaR 95% — pérdida máxima con 95% confianza
    var_99:          float    # VaR 99%
    cvar_95:         float    # CVaR/ES — pérdida esperada en el peor 5%
    prob_perdida:    float    # % simulaciones con pérdida
    prob_ruina:      float    # % simulaciones con pérdida > 20%
    prob_doblar:     float    # % simulaciones que duplican capital

    # Percentiles
    p5:              float    # peor 5%
    p25:             float    # peor 25%
    p75:             float    # mejor 25%
    p95:             float    # mejor 5%

    # Curvas (muestra de simulaciones para graficar)
    curvas_muestra:  list     # 10 curvas representativas

    # Retorno esperado
    retorno_medio:   float    # % retorno promedio

    def resumen(self):
        ret_med = self.retorno_medio
        dir_r   = "▲" if ret_med >= 0 else "▼"
        lineas  = [
            f"\n{'='*60}",
            f"  🎲 MONTE CARLO — {self.metodo}",
            f"  {self.n_simulaciones:,} simulaciones | {self.horizonte_dias} días | "
            f"Capital: ${self.capital_inicial:,.0f}",
            f"{'='*60}",
            f"",
            f"  DISTRIBUCIÓN DE RESULTADOS",
            f"  {'─'*50}",
            f"  {'Resultado promedio':<28} ${self.media:>12,.0f}  ({ret_med:+.1f}%)",
            f"  {'Resultado mediano':<28} ${self.mediana:>12,.0f}",
            f"  {'Desviación estándar':<28} ${self.std:>12,.0f}",
            f"  {'Mejor simulación':<28} ${self.max_resultado:>12,.0f}",
            f"  {'Peor simulación':<28} ${self.min_resultado:>12,.0f}",
            f"",
            f"  MÉTRICAS DE RIESGO",
            f"  {'─'*50}",
            f"  {'VaR 95% (pérdida máx)':<28} ${self.var_95:>12,.0f}",
            f"  {'VaR 99% (pérdida máx)':<28} ${self.var_99:>12,.0f}",
            f"  {'CVaR 95% (pérdida esp)':<28} ${self.cvar_95:>12,.0f}",
            f"",
            f"  PROBABILIDADES",
            f"  {'─'*50}",
            f"  {'Prob. de pérdida':<28} {self.prob_perdida:>12.1f}%",
            f"  {'Prob. de ruina (>20%)':<28} {self.prob_ruina:>12.1f}%",
            f"  {'Prob. de doblar capital':<28} {self.prob_doblar:>12.1f}%",
            f"",
            f"  PERCENTILES",
            f"  {'─'*50}",
            f"  {'Peor  5% (P5)':<28} ${self.p5:>12,.0f}",
            f"  {'Peor 25% (P25)':<28} ${self.p25:>12,.0f}",
            f"  {'Mejor 25% (P75)':<28} ${self.p75:>12,.0f}",
            f"  {'Mejor  5% (P95)':<28} ${self.p95:>12,.0f}",
        ]

        # Histograma ASCII
        lineas.append(f"\n  DISTRIBUCIÓN (histograma)")
        lineas.append(f"  {'─'*50}")
        resultados_finales = [self.p5, self.p25, self.mediana, self.p75, self.p95]
        labels = ["P5", "P25", "P50", "P75", "P95"]
        max_val = max(resultados_finales)
        for val, lbl in zip(resultados_finales, labels):
            bar_len = int(val / max_val * 40) if max_val > 0 else 0
            bar     = "█" * bar_len
            color   = "+" if val >= self.capital_inicial else "-"
            lineas.append(f"  {lbl:<4} ${val:>12,.0f} {bar}")

        lineas.append(f"{'='*60}")
        return "\n".join(lineas)


# ══════════════════════════════════════════════════════════════
# MOTOR MONTE CARLO
# ══════════════════════════════════════════════════════════════
class MonteCarlo:

    def __init__(self, capital_inicial: float = 10_000_000):
        self.capital_ini = capital_inicial

    # ── HELPERS ───────────────────────────────────────────────
    def _gauss(self) -> float:
        """Distribución normal estándar (Box-Muller)."""
        u1 = random.random() + 1e-10
        u2 = random.random()
        return math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * u2)

    def _t_student(self, df: float = 5) -> float:
        """Distribución t de Student — colas más pesadas que la normal."""
        z   = self._gauss()
        chi = sum(self._gauss()**2 for _ in range(int(df)))
        return z / math.sqrt(chi / df) if chi > 0 else z

    def _percentil(self, datos: list, p: float) -> float:
        """Percentil p (0-100) de una lista ordenada."""
        datos_s = sorted(datos)
        idx     = (p / 100) * (len(datos_s) - 1)
        low     = int(idx)
        frac    = idx - low
        if low + 1 < len(datos_s):
            return datos_s[low] + frac * (datos_s[low+1] - datos_s[low])
        return datos_s[low]

    def _construir_resultado(self, resultados: list, curvas: list,
                              metodo: str, horizonte: int) -> ResultadoMC:
        """Construye ResultadoMC desde lista de resultados finales."""
        n      = len(resultados)
        sorted_r = sorted(resultados)
        media  = sum(resultados) / n
        var    = sum((r - media)**2 for r in resultados) / n
        std    = var ** 0.5

        # VaR y CVaR (pérdidas relativas al capital inicial)
        perdidas = sorted([self.capital_ini - r for r in sorted_r], reverse=True)
        var_95   = self._percentil(perdidas, 95)
        var_99   = self._percentil(perdidas, 99)
        cvar_95  = sum(perdidas[:max(1, int(n*0.05))]) / max(1, int(n*0.05))

        # Probabilidades
        prob_perd  = sum(1 for r in resultados if r < self.capital_ini) / n * 100
        prob_ruina = sum(1 for r in resultados if r < self.capital_ini * 0.8) / n * 100
        prob_dob   = sum(1 for r in resultados if r >= self.capital_ini * 2) / n * 100

        # Percentiles de resultados finales
        p5  = self._percentil(sorted_r, 5)
        p25 = self._percentil(sorted_r, 25)
        p50 = self._percentil(sorted_r, 50)
        p75 = self._percentil(sorted_r, 75)
        p95 = self._percentil(sorted_r, 95)

        ret_medio = (media - self.capital_ini) / self.capital_ini * 100

        return ResultadoMC(
            metodo          = metodo,
            n_simulaciones  = n,
            capital_inicial = self.capital_ini,
            horizonte_dias  = horizonte,
            media           = round(media, 0),
            mediana         = round(p50, 0),
            std             = round(std, 0),
            min_resultado   = round(min(resultados), 0),
            max_resultado   = round(max(resultados), 0),
            var_95          = round(var_95, 0),
            var_99          = round(var_99, 0),
            cvar_95         = round(cvar_95, 0),
            prob_perdida    = round(prob_perd, 1),
            prob_ruina      = round(prob_ruina, 1),
            prob_doblar     = round(prob_dob, 1),
            p5              = round(p5, 0),
            p25             = round(p25, 0),
            p75             = round(p75, 0),
            p95             = round(p95, 0),
            curvas_muestra  = curvas[:10],
            retorno_medio   = round(ret_medio, 2),
        )

    # ── MÉTODO 1: MONTE CARLO SOBRE TRADES ───────────────────
    def simular_trades(self, trades: list, n: int = 1_000,
                       slippage_var: float = 0.005) -> ResultadoMC:
        """
        Monte Carlo sobre historial de trades reales.
        Reordena aleatoriamente los trades y varía el slippage.
        Esto evita el "sesgo de orden" del backtest.

        trades: lista de dicts con campo 'pnl'
        slippage_var: variación adicional de slippage (0-1%)
        """
        print(f"[MC] Simulando {n:,} variaciones de {len(trades)} trades...")

        pnls = [t.get("pnl", t.pnl if hasattr(t, "pnl") else 0)
                for t in trades]
        pnls = [p for p in pnls if p is not None]

        if not pnls:
            raise ValueError("No hay trades con PnL definido")

        resultados = []
        curvas     = []

        for sim in range(n):
            # Reordenar trades aleatoriamente
            pnls_sim = pnls[:]
            random.shuffle(pnls_sim)

            # Variar slippage: cada trade varía ±slippage_var%
            precio_ref = self.capital_ini / len(pnls_sim) if pnls_sim else 1
            pnls_var   = [
                p * (1 + random.uniform(-slippage_var, slippage_var))
                for p in pnls_sim
            ]

            # Construir curva de equity
            capital = self.capital_ini
            curva   = [capital]
            for pnl in pnls_var:
                capital += pnl
                capital  = max(capital, 0)  # no puede ser negativo
                curva.append(capital)

            resultados.append(capital)
            if sim < 10:
                curvas.append(curva)

        horizonte = len(pnls)
        return self._construir_resultado(
            resultados, curvas,
            f"MC sobre trades ({len(pnls)} trades históricos)",
            horizonte
        )

    # ── MÉTODO 2: MC SOBRE RETORNOS HISTÓRICOS ────────────────
    def simular_retornos(self, retornos: list, horizonte_dias: int = 252,
                          n: int = 1_000) -> ResultadoMC:
        """
        Monte Carlo muestreando retornos históricos (bootstrap).
        Preserva la distribución empírica real — no asume normalidad.

        retornos: lista de retornos diarios históricos (ej: [0.01, -0.005, ...])
        """
        print(f"[MC] Bootstrap de retornos: {n:,} sims × {horizonte_dias} días...")

        if not retornos:
            raise ValueError("Se necesitan retornos históricos")

        resultados = []
        curvas     = []

        for sim in range(n):
            # Muestrear retornos con reemplazo (bootstrap)
            rets_sim = random.choices(retornos, k=horizonte_dias)

            capital = self.capital_ini
            curva   = [capital]
            for r in rets_sim:
                capital *= (1 + r)
                capital  = max(capital, 0)
                curva.append(capital)

            resultados.append(capital)
            if sim < 10:
                curvas.append(curva)

        return self._construir_resultado(
            resultados, curvas,
            f"MC Bootstrap de retornos ({len(retornos)} obs históricos)",
            horizonte_dias
        )

    # ── MÉTODO 3: GBM — GEOMETRIC BROWNIAN MOTION ─────────────
    def gbm(self, retorno_anual: float, volatilidad_anual: float,
             horizonte_dias: int = 252, n: int = 1_000) -> ResultadoMC:
        """
        Simulación clásica GBM (log-normal).
        S(t) = S(0) × exp((μ - σ²/2)×t + σ×√t×Z)

        retorno_anual:    tasa de retorno anual (ej: 0.12 = 12%)
        volatilidad_anual: volatilidad anual (ej: 0.20 = 20%)
        """
        print(f"[MC] GBM: {n:,} sims × {horizonte_dias} días | "
              f"μ={retorno_anual*100:.1f}% σ={volatilidad_anual*100:.1f}%")

        dt      = 1 / 252  # un día de trading
        drift   = (retorno_anual - 0.5 * volatilidad_anual**2) * dt
        difusion = volatilidad_anual * math.sqrt(dt)

        resultados = []
        curvas     = []

        for sim in range(n):
            capital = self.capital_ini
            curva   = [capital]
            for _ in range(horizonte_dias):
                z       = self._gauss()
                capital *= math.exp(drift + difusion * z)
                curva.append(capital)

            resultados.append(capital)
            if sim < 10:
                curvas.append(curva)

        return self._construir_resultado(
            resultados, curvas,
            f"GBM (μ={retorno_anual*100:.1f}%, σ={volatilidad_anual*100:.1f}%)",
            horizonte_dias
        )

    # ── MÉTODO 4: GBM CON FAT TAILS (t de Student) ────────────
    def gbm_fat_tails(self, retorno_anual: float, volatilidad_anual: float,
                       horizonte_dias: int = 252, n: int = 1_000,
                       df: float = 5) -> ResultadoMC:
        """
        GBM con distribución t de Student para simular fat tails.
        Más realista que GBM puro para mercados financieros reales.
        df: grados de libertad (menor df = colas más pesadas)
        """
        print(f"[MC] GBM Fat Tails: {n:,} sims | t-dist df={df}")

        dt      = 1 / 252
        drift   = (retorno_anual - 0.5 * volatilidad_anual**2) * dt
        difusion = volatilidad_anual * math.sqrt(dt)
        # Factor de escala para t-Student (ajustar varianza)
        scale   = math.sqrt((df - 2) / df) if df > 2 else 1.0

        resultados = []
        curvas     = []

        for sim in range(n):
            capital = self.capital_ini
            curva   = [capital]
            for _ in range(horizonte_dias):
                z       = self._t_student(df) * scale
                capital *= math.exp(drift + difusion * z)
                capital  = max(capital, 0)
                curva.append(capital)

            resultados.append(capital)
            if sim < 10:
                curvas.append(curva)

        return self._construir_resultado(
            resultados, curvas,
            f"GBM Fat Tails (t-dist df={df}, μ={retorno_anual*100:.1f}%, "
            f"σ={volatilidad_anual*100:.1f}%)",
            horizonte_dias
        )

    # ── ANÁLISIS DE HORIZONTE ─────────────────────────────────
    def analizar_horizonte(self, retorno_anual: float, volatilidad_anual: float,
                            objetivo: float = 2.0, n: int = 500) -> dict:
        """
        ¿Cuántos años necesito para doblar (o X veces) mi capital
        con X% de probabilidad?

        objetivo: multiplicador (2.0 = doblar, 1.5 = 50% más)
        """
        print(f"[MC] Analizando horizonte para objetivo {objetivo}x capital...")

        horizontes = [63, 126, 252, 504, 756, 1260]  # 3m, 6m, 1a, 2a, 3a, 5a
        resultado  = {}

        for h in horizontes:
            r = self.gbm(retorno_anual, volatilidad_anual, h, n)
            prob = r.prob_doblar if objetivo >= 2.0 else \
                   sum(1 for c in [r.p5, r.p25, r.mediana, r.p75, r.p95]
                       if c >= self.capital_ini * objetivo) / 5 * 100
            años = h / 252
            resultado[f"{años:.1f}a"] = {
                "dias":       h,
                "años":       round(años, 1),
                "prob_obj":   round(prob, 1),
                "mediana":    round(r.mediana, 0),
                "var_95":     round(r.var_95, 0),
            }

        return resultado

    # ── COMPARAR MÉTODOS ──────────────────────────────────────
    def comparar_metodos(self, retorno_anual: float = 0.12,
                          volatilidad_anual: float = 0.18,
                          horizonte_dias: int = 252,
                          n: int = 500) -> dict:
        """Compara GBM vs GBM Fat Tails para mostrar la diferencia."""
        print(f"\n[MC] Comparando métodos ({n:,} sims cada uno)...")

        gbm_r  = self.gbm(retorno_anual, volatilidad_anual, horizonte_dias, n)
        fat_r  = self.gbm_fat_tails(retorno_anual, volatilidad_anual,
                                     horizonte_dias, n, df=5)

        print(f"\n{'='*60}")
        print(f"  COMPARATIVA: GBM Normal vs Fat Tails")
        print(f"  μ={retorno_anual*100:.1f}% | σ={volatilidad_anual*100:.1f}% | {horizonte_dias} días")
        print(f"{'='*60}")
        print(f"  {'Métrica':<25} {'GBM Normal':>14} {'Fat Tails':>14}")
        print(f"  {'─'*53}")

        metricas = [
            ("Mediana",         gbm_r.mediana,      fat_r.mediana,      "${:,.0f}"),
            ("VaR 95%",         gbm_r.var_95,       fat_r.var_95,       "${:,.0f}"),
            ("CVaR 95%",        gbm_r.cvar_95,      fat_r.cvar_95,      "${:,.0f}"),
            ("Prob. pérdida",   gbm_r.prob_perdida, fat_r.prob_perdida, "{:.1f}%"),
            ("Prob. ruina",     gbm_r.prob_ruina,   fat_r.prob_ruina,   "{:.1f}%"),
            ("Peor sim (P5)",   gbm_r.p5,           fat_r.p5,           "${:,.0f}"),
            ("Mejor sim (P95)", gbm_r.p95,          fat_r.p95,          "${:,.0f}"),
        ]
        for nombre, vg, vf, fmt in metricas:
            print(f"  {nombre:<25} {fmt.format(vg):>14} {fmt.format(vf):>14}")

        print(f"\n  💡 Fat Tails muestra peores escenarios extremos")
        print(f"     porque usa distribución t-Student (colas pesadas).")
        print(f"     Los mercados reales tienen más crashes que predice")
        print(f"     una distribución normal. Usar Fat Tails es más realista.")
        print(f"{'='*60}")

        return {"gbm": gbm_r, "fat_tails": fat_r}


# ══════════════════════════════════════════════════════════════
# TEST — python montecarlo.py
# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("\n" + "="*60)
    print("  InvertirCL — Monte Carlo Simulator v1.0")
    print("="*60)

    mc = MonteCarlo(capital_inicial=10_000_000)

    # ── Test 1: MC sobre trades ──
    print("\n🎲 Test 1: Monte Carlo sobre trades históricos")
    trades_test = [
        {"pnl":  42000},  {"pnl": -21000}, {"pnl":  18000},
        {"pnl": -15000},  {"pnl":  55000}, {"pnl": -8000},
        {"pnl":  31000},  {"pnl": -25000}, {"pnl":  12000},
        {"pnl":  67000},  {"pnl": -18000}, {"pnl":  24000},
        {"pnl": -32000},  {"pnl":  41000}, {"pnl": -11000},
        {"pnl":  19000},  {"pnl": -14000}, {"pnl":  38000},
        {"pnl":  22000},  {"pnl": -9000},
    ]
    r1 = mc.simular_trades(trades_test, n=1_000)
    print(r1.resumen())

    # ── Test 2: GBM clásico ──
    print("\n🎲 Test 2: GBM clásico — IPSA proyección 1 año")
    r2 = mc.gbm(
        retorno_anual     = 0.10,   # 10% retorno esperado
        volatilidad_anual = 0.18,   # 18% volatilidad histórica IPSA
        horizonte_dias    = 252,
        n                 = 1_000,
    )
    print(r2.resumen())

    # ── Test 3: Fat Tails ──
    print("\n🎲 Test 3: GBM Fat Tails (más realista)")
    r3 = mc.gbm_fat_tails(
        retorno_anual     = 0.10,
        volatilidad_anual = 0.18,
        horizonte_dias    = 252,
        n                 = 1_000,
        df                = 5,      # df=5 → colas moderadamente pesadas
    )
    print(r3.resumen())

    # ── Test 4: Comparativa métodos ──
    print("\n🔬 Test 4: Comparativa GBM Normal vs Fat Tails")
    mc.comparar_metodos(
        retorno_anual     = 0.10,
        volatilidad_anual = 0.18,
        horizonte_dias    = 252,
        n                 = 500,
    )

    # ── Test 5: Análisis de horizonte ──
    print("\n📅 Test 5: ¿Cuánto tiempo para doblar el capital?")
    horizontes = mc.analizar_horizonte(
        retorno_anual     = 0.12,
        volatilidad_anual = 0.18,
        objetivo          = 2.0,
        n                 = 300,
    )
    print(f"\n  {'Horizonte':>10} {'Mediana':>14} {'VaR 95%':>12} {'Prob x2':>10}")
    print(f"  {'─'*48}")
    for periodo, datos in horizontes.items():
        print(f"  {periodo:>10} ${datos['mediana']:>12,.0f} "
              f"${datos['var_95']:>10,.0f} {datos['prob_obj']:>9.1f}%")

    # ── Test 6: MC con datos reales del backtest ──
    try:
        from backtest_engine import BacktestEngine
        print(f"\n{'='*60}")
        print("  📡 MC integrado con Backtest Engine")
        print(f"{'='*60}")

        # Datos sintéticos para el backtest
        precio = 7000
        candles = []
        for i in range(180):
            import random as _r
            ret = _r.gauss(0.0003, 0.012)
            open_ = precio
            close = max(precio * (1 + ret), precio * 0.5)
            high  = max(open_, close) * (1 + abs(_r.gauss(0, 0.004)))
            low   = min(open_, close) * (1 - abs(_r.gauss(0, 0.004)))
            candles.append({
                "date":   f"2025-{(i//30)+1:02d}-{(i%30)+1:02d}",
                "open":   round(open_, 2), "high": round(high, 2),
                "low":    round(low, 2),   "close": round(close, 2),
                "volume": _r.randint(100_000, 800_000),
            })
            precio = close

        engine   = BacktestEngine(capital_inicial=10_000_000)
        bt_res   = engine.run(candles, estrategia="rsi_mean_reversion",
                               symbol="IPSA_SINT")

        if bt_res.trades:
            print(f"  Backtest: {len(bt_res.trades)} trades | "
                  f"Retorno: {bt_res.retorno_total:+.2f}%")
            mc_bt = mc.simular_trades(bt_res.trades, n=500)
            print(f"  MC sobre esos trades:")
            print(f"    Mediana:      ${mc_bt.mediana:,.0f}")
            print(f"    Prob. pérdida: {mc_bt.prob_perdida:.1f}%")
            print(f"    VaR 95%:      ${mc_bt.var_95:,.0f}")
            print(f"    Prob. ruina:  {mc_bt.prob_ruina:.1f}%")
    except Exception as e:
        print(f"  Integración backtest: {e}")

    print("\n" + "="*60)
