"""
InvertirCL — Black Swan Simulator v1.0
========================================
Item 10 del Roadmap.

Carga escenarios históricos reales de crisis y permite al usuario
operar en ese entorno con volatilidad real. Enseña que:
  - Los stop loss no siempre se ejecutan al precio deseado (slippage masivo)
  - La volatilidad puede ser 4-10x la normal
  - Las correlaciones entre activos se rompen en crisis

ESCENARIOS DISPONIBLES:
  1. Crisis Subprime 2008     → IPSA cayó 37% en 6 meses
  2. COVID Marzo 2020         → IPSA cayó 35% en 3 semanas
  3. Estallido Social 2019    → IPSA cayó 15% en 1 semana
  4. Flash Crash Mayo 2010    → S&P500 cayó 9% en minutos
  5. Crisis Rusa 1998         → Mercados emergentes -50%

USO:
  from blackswan import BlackSwanSimulator

  sim = BlackSwanSimulator()
  escenario = sim.cargar_escenario("covid_2020")
  print(escenario.resumen())

  # Simular operación en la crisis
  resultado = sim.simular_trade(
      escenario   = escenario,
      dia_entrada = 5,           # día 5 del escenario
      precio_entrada = 7200,
      stop_loss   = 6900,
      take_profit = 7500,
      cantidad    = 100,
      side        = "buy",
  )
  print(resultado)
"""

import random
import math
from dataclasses import dataclass, field
from typing import Optional

try:
    import yfinance as yf
    import pandas as pd
    import numpy as np
    LIBS_OK = True
except ImportError:
    LIBS_OK = False
    print("[BlackSwan] Instala dependencias: pip install yfinance pandas numpy")


# ══════════════════════════════════════════════════════════════
# ESCENARIOS HISTÓRICOS
# ══════════════════════════════════════════════════════════════
ESCENARIOS = {
    "crisis_2008": {
        "nombre":      "Crisis Subprime 2008",
        "descripcion": "Colapso del sistema financiero global. Lehman Brothers quiebra el 15 de septiembre de 2008.",
        "emoji":       "🏦",
        "color":       "#ff3d57",
        "fecha_inicio": "2008-09-01",
        "fecha_fin":    "2009-03-31",
        "symbol_ipsa":  "^IPSA",
        "caida_max":   -37.0,  # %
        "duracion_dias": 180,
        "volatilidad_mult": 4.2,  # veces la volatilidad normal
        "slippage_extra":   0.03, # 3% extra en momentos de pánico
        "datos_clave": [
            "15 Sep 2008: Lehman Brothers quiebra — IPSA cae 7% en 1 día",
            "Oct 2008: Volatilidad más alta desde 1929",
            "Los stops se ejecutaron con hasta 5% de slippage",
            "Correlación de todos los activos → 1.0 (nada diversifica)",
        ],
        "leccion": "En una crisis sistémica, la diversificación falla. El único hedge real es el cash.",
    },
    "covid_2020": {
        "nombre":      "Crash COVID-19 Marzo 2020",
        "descripcion": "El mercado cayó más rápido que en cualquier crisis anterior. El IPSA perdió 35% en 3 semanas.",
        "emoji":       "🦠",
        "color":       "#ff6b35",
        "fecha_inicio": "2020-02-20",
        "fecha_fin":    "2020-04-30",
        "symbol_ipsa":  "^IPSA",
        "caida_max":   -35.0,
        "duracion_dias": 70,
        "volatilidad_mult": 6.8,
        "slippage_extra":   0.05,
        "datos_clave": [
            "24 Feb 2020: Primera caída masiva — IPSA -5% en 1 día",
            "12 Mar 2020: Circuitos cortafuego activados en múltiples bolsas",
            "18 Mar 2020: Mínimo — IPSA en 3.800 puntos desde 5.800",
            "Slippage de hasta 8% en activos ilíquidos",
        ],
        "leccion": "La velocidad de la caída COVID fue sin precedentes. Los stops de mercado se ejecutaron muy por debajo del precio.",
    },
    "estallido_2019": {
        "nombre":      "Estallido Social Chile 2019",
        "descripcion": "Crisis política y social única de Chile. El IPSA cayó 15% en la primera semana.",
        "emoji":       "🇨🇱",
        "color":       "#f5c518",
        "fecha_inicio": "2019-10-18",
        "fecha_fin":    "2019-12-31",
        "symbol_ipsa":  "^IPSA",
        "caida_max":   -20.0,
        "duracion_dias": 74,
        "volatilidad_mult": 3.5,
        "slippage_extra":   0.025,
        "datos_clave": [
            "18 Oct 2019: Inicio del estallido — IPSA cae 4% en 1 día",
            "22 Oct 2019: IPSA pierde 15% en una semana",
            "Dólar sube de $710 a $830 en 2 semanas",
            "Acciones de retail (Falabella, Cencosud) las más golpeadas",
        ],
        "leccion": "El riesgo político local puede ser más rápido y severo que el riesgo de mercado global.",
    },
    "flash_crash_2010": {
        "nombre":      "Flash Crash Mayo 2010",
        "descripcion": "El Dow Jones cayó 1.000 puntos en minutos por órdenes algorítmicas. Se recuperó casi completamente en 20 minutos.",
        "emoji":       "⚡",
        "color":       "#9b59b6",
        "fecha_inicio": "2010-05-06",
        "fecha_fin":    "2010-05-06",
        "symbol_ipsa":  "^GSPC",
        "caida_max":   -9.2,
        "duracion_dias": 1,
        "volatilidad_mult": 15.0,
        "slippage_extra":   0.08,
        "datos_clave": [
            "14:42 ET: El Dow cae 600 puntos en minutos",
            "14:47 ET: Caída total de 998 puntos (-9.2%)",
            "15:07 ET: Recuperación de casi el 70%",
            "Stops ejecutados hasta 50% por debajo del precio (acciones a $0.01)",
        ],
        "leccion": "En un Flash Crash, tus stops de mercado pueden ejecutarse a precios absurdos. Los stops limit no se ejecutan.",
    },
    "crisis_rusa_1998": {
        "nombre":      "Crisis Financiera Rusia 1998",
        "descripcion": "Default ruso y colapso de LTCM. Los mercados emergentes perdieron hasta 50%.",
        "emoji":       "🇷🇺",
        "color":       "#4a9eff",
        "fecha_inicio": "1998-07-01",
        "fecha_fin":    "1998-10-31",
        "symbol_ipsa":  "^GSPC",
        "caida_max":   -22.0,
        "duracion_dias": 122,
        "volatilidad_mult": 3.8,
        "slippage_extra":   0.04,
        "datos_clave": [
            "17 Ago 1998: Rusia declara default de deuda soberana",
            "LTCM pierde $4.000 millones en semanas",
            "Mercados emergentes caen 50% en 3 meses",
            "El contagio llegó a América Latina — Chile incluido",
        ],
        "leccion": "El contagio financiero es rápido y no discrimina. Un default en otro país puede hundir tu portafolio local.",
    },
}


# ══════════════════════════════════════════════════════════════
# ESTRUCTURAS DE DATOS
# ══════════════════════════════════════════════════════════════
@dataclass
class DiaSimulado:
    dia:          int
    fecha:        str
    precio_open:  float
    precio_high:  float
    precio_low:   float
    precio_close: float
    volumen:      float
    retorno_dia:  float      # % cambio vs día anterior
    volatilidad:  float      # ATR del día como % del precio
    es_crisis:    bool = True

    def resumen(self):
        return (f"Día {self.dia:3d} | {self.fecha} | "
                f"O:{self.precio_open:,.0f} H:{self.precio_high:,.0f} "
                f"L:{self.precio_low:,.0f} C:{self.precio_close:,.0f} | "
                f"Ret:{self.retorno_dia:+.2f}%")


@dataclass
class ResultadoTrade:
    # Entrada
    dia_entrada:     int
    precio_entrada:  float
    stop_loss:       float
    take_profit:     float
    cantidad:        int
    side:            str

    # Ejecución real (con slippage)
    precio_ejec_entrada: float
    slippage_entrada:    float

    # Salida
    dia_salida:      Optional[int]   = None
    precio_salida:   Optional[float] = None
    precio_ejec_salida: Optional[float] = None
    slippage_salida: Optional[float] = None
    razon_salida:    Optional[str]   = None

    # P&L
    pnl_bruto:       Optional[float] = None
    pnl_neto:        Optional[float] = None  # después de slippage
    pnl_pct:         Optional[float] = None

    # Análisis
    sobrevivio:      bool = False
    lecciones:       list = field(default_factory=list)

    def resumen(self):
        lineas = [
            f"\n{'='*55}",
            f"  RESULTADO DEL TRADE — Crisis",
            f"{'='*55}",
            f"  Entrada:  Día {self.dia_entrada} @ ${self.precio_entrada:,.0f}",
            f"  Ejec.entrada: ${self.precio_ejec_entrada:,.0f} (slippage: ${self.slippage_entrada:,.0f})",
            f"  Stop Loss: ${self.stop_loss:,.0f}",
            f"  Take Profit: ${self.take_profit:,.0f}",
        ]
        if self.dia_salida:
            lineas += [
                f"  Salida: Día {self.dia_salida} @ ${self.precio_salida:,.0f} ({self.razon_salida})",
                f"  Ejec.salida: ${self.precio_ejec_salida:,.0f} (slippage: ${self.slippage_salida:,.0f})",
                f"  P&L bruto: ${self.pnl_bruto:+,.0f}",
                f"  P&L neto:  ${self.pnl_neto:+,.0f} ({self.pnl_pct:+.1f}%)",
                f"  Sobrevivió: {'✅ Sí' if self.sobrevivio else '❌ No'}",
            ]
        if self.lecciones:
            lineas.append(f"\n  💡 Lecciones:")
            for l in self.lecciones:
                lineas.append(f"    • {l}")
        lineas.append(f"{'='*55}")
        return "\n".join(lineas)


@dataclass
class Escenario:
    id:           str
    config:       dict
    dias:         list = field(default_factory=list)

    def resumen(self):
        c = self.config
        retorno_total = ((self.dias[-1].precio_close / self.dias[0].precio_open) - 1) * 100 if self.dias else 0
        vols = [d.volatilidad for d in self.dias]
        vol_avg = sum(vols) / len(vols) if vols else 0

        return (
            f"\n{'='*55}\n"
            f"  {c['emoji']} {c['nombre']}\n"
            f"{'='*55}\n"
            f"  {c['descripcion']}\n\n"
            f"  📅 Período:    {c['fecha_inicio']} → {c['fecha_fin']}\n"
            f"  📉 Caída máx:  {c['caida_max']}%\n"
            f"  ⚡ Vol. mult:  {c['volatilidad_mult']}x la normal\n"
            f"  💸 Slippage:   hasta {c['slippage_extra']*100:.1f}% extra\n"
            f"  📊 Días sim.:  {len(self.dias)}\n"
            f"  📈 Retorno sim:{retorno_total:+.1f}%\n"
            f"  🌊 Vol. promedio: {vol_avg:.2f}%/día\n\n"
            f"  Datos clave:\n"
            + "\n".join(f"  • {d}" for d in c.get("datos_clave", []))
            + f"\n\n  💡 Lección: {c['leccion']}\n"
            f"{'='*55}"
        )


# ══════════════════════════════════════════════════════════════
# SIMULADOR
# ══════════════════════════════════════════════════════════════
class BlackSwanSimulator:
    """
    Genera datos históricos de crisis y permite simular operaciones
    en ese entorno con slippage realista y volatilidad real.
    """

    def __init__(self):
        self.escenarios_cargados = {}

    # ── CARGAR ESCENARIO ──────────────────────────────────────
    def cargar_escenario(self, escenario_id: str) -> Escenario:
        """
        Carga un escenario de crisis.
        Si yfinance está disponible, usa datos reales.
        Si no, genera datos sintéticos pero realistas.
        """
        if escenario_id not in ESCENARIOS:
            raise ValueError(f"Escenario '{escenario_id}' no existe. Disponibles: {list(ESCENARIOS.keys())}")

        if escenario_id in self.escenarios_cargados:
            return self.escenarios_cargados[escenario_id]

        config = ESCENARIOS[escenario_id]
        print(f"[BlackSwan] Cargando escenario: {config['nombre']}...")

        dias = []
        if LIBS_OK:
            dias = self._datos_reales(config)

        if not dias:
            print("[BlackSwan] Usando datos sintéticos (instala yfinance para datos reales)")
            dias = self._datos_sinteticos(config)

        escenario = Escenario(id=escenario_id, config=config, dias=dias)
        self.escenarios_cargados[escenario_id] = escenario
        print(f"[BlackSwan] ✅ {len(dias)} días cargados")
        return escenario

    def _datos_reales(self, config: dict) -> list:
        """Descarga datos históricos reales de Yahoo Finance."""
        try:
            df = yf.download(
                config["symbol_ipsa"],
                start=config["fecha_inicio"],
                end=config["fecha_fin"],
                progress=False,
            )
            if df.empty:
                return []

            df = df.reset_index()
            df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]

            dias = []
            prev_close = None
            for i, row in df.iterrows():
                close = float(row.get("Close", row.get("close", 0)))
                open_ = float(row.get("Open",  row.get("open",  close)))
                high  = float(row.get("High",  row.get("high",  close)))
                low   = float(row.get("Low",   row.get("low",   close)))
                vol   = float(row.get("Volume",row.get("volume",0)))
                ret   = ((close - prev_close) / prev_close * 100) if prev_close else 0
                atr   = ((high - low) / close * 100) if close > 0 else 0
                fecha = str(row.get("Date", row.get("date", "")))[:10]

                dias.append(DiaSimulado(
                    dia=i+1, fecha=fecha,
                    precio_open=open_, precio_high=high,
                    precio_low=low, precio_close=close,
                    volumen=vol, retorno_dia=round(ret,2),
                    volatilidad=round(atr,2),
                ))
                prev_close = close
            return dias
        except Exception as e:
            print(f"[BlackSwan] Error datos reales: {e}")
            return []

    def _datos_sinteticos(self, config: dict) -> list:
        """
        Genera datos sintéticos realistas cuando no hay acceso a Yahoo Finance.
        Usa el perfil de la crisis real como base.
        """
        n_dias   = config["duracion_dias"]
        caida    = config["caida_max"] / 100
        vol_mult = config["volatilidad_mult"]

        # Precio base IPSA ~7.000
        precio_base  = 7000
        vol_normal   = 0.008  # 0.8% diario normal
        vol_crisis   = vol_normal * vol_mult

        # Perfil de crisis: caída rápida + rebote parcial + lenta recuperación
        dias  = []
        precio = precio_base
        prev   = precio

        # Curva de volatilidad: alta al inicio, se modera después
        def vol_dia(i, n):
            # Pico al 30% del escenario, decrece después
            t = i / n
            if t < 0.3:
                return vol_crisis * (1 + t * 3)
            elif t < 0.6:
                return vol_crisis * (1.9 - t * 2)
            else:
                return vol_normal * 2 + (vol_crisis - vol_normal * 2) * (1 - t)

        # Sesgo diario: caída neta al final
        caida_total_esperada = caida
        sesgo_diario = caida_total_esperada / n_dias

        for i in range(n_dias):
            vol = vol_dia(i, n_dias)
            # Ruido gaussiano + sesgo bajista
            u1 = random.random() + 1e-10
            u2 = random.random()
            gauss = math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * u2)
            retorno = gauss * vol + sesgo_diario

            open_  = precio
            close  = max(precio * (1 + retorno), precio * 0.5)
            high   = max(open_, close) * (1 + abs(gauss) * vol * 0.3)
            low    = min(open_, close) * (1 - abs(gauss) * vol * 0.3)
            ret_dia = (close - prev) / prev * 100 if prev > 0 else 0
            atr_dia = (high - low) / close * 100 if close > 0 else 0

            # Fecha aproximada
            from datetime import datetime, timedelta
            fecha_inicio = datetime.strptime(config["fecha_inicio"], "%Y-%m-%d")
            fecha_dia    = fecha_inicio + timedelta(days=i)
            # Saltar fines de semana
            while fecha_dia.weekday() >= 5:
                fecha_dia += timedelta(days=1)

            dias.append(DiaSimulado(
                dia=i+1, fecha=fecha_dia.strftime("%Y-%m-%d"),
                precio_open=round(open_,1), precio_high=round(high,1),
                precio_low=round(low,1), precio_close=round(close,1),
                volumen=random.randint(50_000, 500_000),
                retorno_dia=round(ret_dia,2),
                volatilidad=round(atr_dia,2),
            ))
            prev   = close
            precio = close

        return dias

    # ── SIMULAR TRADE ─────────────────────────────────────────
    def simular_trade(
        self,
        escenario:     Escenario,
        dia_entrada:   int,
        precio_entrada: float,
        stop_loss:     float,
        take_profit:   float,
        cantidad:      int,
        side:          str = "buy",
    ) -> ResultadoTrade:
        """
        Simula la ejecución de un trade en el escenario de crisis.
        Aplica slippage realista según la volatilidad del día.
        """
        config = escenario.config
        dias   = escenario.dias

        if dia_entrada > len(dias):
            raise ValueError(f"Día {dia_entrada} fuera del rango ({len(dias)} días disponibles)")

        # Slippage de entrada (mercado ya está en pánico)
        slippage_base   = config["slippage_extra"]
        sl_entrada_pct  = random.uniform(0, slippage_base)
        if side == "buy":
            precio_ejec = precio_entrada * (1 + sl_entrada_pct)
        else:
            precio_ejec = precio_entrada * (1 - sl_entrada_pct)

        slippage_entrada = abs(precio_ejec - precio_entrada) * cantidad

        resultado = ResultadoTrade(
            dia_entrada     = dia_entrada,
            precio_entrada  = precio_entrada,
            stop_loss       = stop_loss,
            take_profit     = take_profit,
            cantidad        = cantidad,
            side            = side,
            precio_ejec_entrada = round(precio_ejec, 1),
            slippage_entrada    = round(slippage_entrada, 0),
        )

        # Simular día a día hasta que se ejecute el stop o el TP
        for i in range(dia_entrada, len(dias)):
            dia = dias[i]

            # Volatilidad del día amplifica el slippage
            vol_factor = dia.volatilidad / 1.0  # normalizado

            # ── ¿Se ejecutó el Stop Loss? ──
            stop_tocado = (
                (side == "buy"  and dia.precio_low  <= stop_loss) or
                (side == "sell" and dia.precio_high >= stop_loss)
            )
            if stop_tocado:
                # Slippage extra en el stop durante crisis
                sl_salida_pct = random.uniform(
                    slippage_base * 0.5,
                    slippage_base * 2.0 * vol_factor  # hasta el doble en días muy volátiles
                )
                if side == "buy":
                    precio_salida = stop_loss * (1 - sl_salida_pct)
                else:
                    precio_salida = stop_loss * (1 + sl_salida_pct)

                slippage_salida = abs(precio_salida - stop_loss) * cantidad

                pnl_bruto = (precio_salida - precio_ejec) * cantidad if side == "buy" else (precio_ejec - precio_salida) * cantidad
                pnl_neto  = pnl_bruto - slippage_salida
                pnl_pct   = pnl_neto / (precio_ejec * cantidad) * 100

                resultado.dia_salida          = i + 1
                resultado.precio_salida       = round(precio_salida, 1)
                resultado.precio_ejec_salida  = round(precio_salida, 1)
                resultado.slippage_salida     = round(slippage_salida, 0)
                resultado.razon_salida        = "stop_loss"
                resultado.pnl_bruto           = round(pnl_bruto, 0)
                resultado.pnl_neto            = round(pnl_neto, 0)
                resultado.pnl_pct             = round(pnl_pct, 2)
                resultado.sobrevivio          = pnl_neto > -(precio_ejec * cantidad * 0.2)

                lecciones = []
                if sl_salida_pct > slippage_base:
                    lecciones.append(
                        f"Tu stop se ejecutó {sl_salida_pct*100:.1f}% peor que el precio esperado. "
                        f"En crisis, el slippage puede duplicarse."
                    )
                if not resultado.sobrevivio:
                    lecciones.append("Pérdida superior al 20% del capital en esta posición. "
                                     "En crisis, el position sizing es aún más crítico.")
                lecciones.append(f"Lección del escenario: {config['leccion']}")
                resultado.lecciones = lecciones
                return resultado

            # ── ¿Se ejecutó el Take Profit? ──
            tp_tocado = (
                (side == "buy"  and dia.precio_high >= take_profit) or
                (side == "sell" and dia.precio_low  <= take_profit)
            )
            if tp_tocado:
                sl_salida_pct = random.uniform(0, slippage_base * 0.3)
                if side == "buy":
                    precio_salida = take_profit * (1 - sl_salida_pct)
                else:
                    precio_salida = take_profit * (1 + sl_salida_pct)

                slippage_salida = abs(precio_salida - take_profit) * cantidad
                pnl_bruto = (precio_salida - precio_ejec) * cantidad if side == "buy" else (precio_ejec - precio_salida) * cantidad
                pnl_neto  = pnl_bruto - slippage_salida
                pnl_pct   = pnl_neto / (precio_ejec * cantidad) * 100

                resultado.dia_salida          = i + 1
                resultado.precio_salida       = round(take_profit, 1)
                resultado.precio_ejec_salida  = round(precio_salida, 1)
                resultado.slippage_salida     = round(slippage_salida, 0)
                resultado.razon_salida        = "take_profit"
                resultado.pnl_bruto           = round(pnl_bruto, 0)
                resultado.pnl_neto            = round(pnl_neto, 0)
                resultado.pnl_pct             = round(pnl_pct, 2)
                resultado.sobrevivio          = True
                resultado.lecciones           = [
                    "¡Take profit alcanzado en plena crisis! Disciplina recompensada.",
                    config["leccion"],
                ]
                return resultado

        # Sin stop ni TP tocados — fin del escenario
        ultimo_precio = dias[-1].precio_close
        pnl_bruto = (ultimo_precio - precio_ejec) * cantidad if side == "buy" else (precio_ejec - ultimo_precio) * cantidad
        pnl_neto  = pnl_bruto
        pnl_pct   = pnl_neto / (precio_ejec * cantidad) * 100

        resultado.dia_salida          = len(dias)
        resultado.precio_salida       = round(ultimo_precio, 1)
        resultado.precio_ejec_salida  = round(ultimo_precio, 1)
        resultado.slippage_salida     = 0
        resultado.razon_salida        = "fin_escenario"
        resultado.pnl_bruto           = round(pnl_bruto, 0)
        resultado.pnl_neto            = round(pnl_neto, 0)
        resultado.pnl_pct             = round(pnl_pct, 2)
        resultado.sobrevivio          = pnl_neto > 0
        resultado.lecciones           = [
            f"Posición abierta durante todo el escenario.",
            f"Retorno final: {pnl_pct:+.1f}%",
            config["leccion"],
        ]
        return resultado

    # ── STRESS TEST ───────────────────────────────────────────
    def stress_test(
        self,
        escenario_id:    str,
        precio_entrada:  float,
        stop_loss:       float,
        take_profit:     float,
        cantidad:        int,
        n_simulaciones:  int = 1000,
        side:            str = "buy",
    ) -> dict:
        """
        Corre N simulaciones del mismo trade en el escenario de crisis
        con variación aleatoria de slippage para ver la distribución de resultados.
        """
        escenario = self.cargar_escenario(escenario_id)
        resultados_pnl = []

        print(f"[BlackSwan] Corriendo {n_simulaciones} simulaciones de stress test...")

        for _ in range(n_simulaciones):
            # Variar el día de entrada aleatoriamente (primeros 30% del escenario)
            n_dias    = len(escenario.dias)
            dia_max   = max(1, int(n_dias * 0.3))
            dia_entry = random.randint(1, dia_max)

            try:
                r = self.simular_trade(
                    escenario, dia_entry, precio_entrada,
                    stop_loss, take_profit, cantidad, side
                )
                if r.pnl_neto is not None:
                    resultados_pnl.append(r.pnl_neto)
            except Exception:
                pass

        if not resultados_pnl:
            return {"error": "Sin resultados válidos"}

        import statistics
        pnls = sorted(resultados_pnl)
        n    = len(pnls)

        return {
            "n_simulaciones":   n_simulaciones,
            "n_validas":        n,
            "pnl_promedio":     round(sum(pnls) / n, 0),
            "pnl_mediano":      round(pnls[n // 2], 0),
            "pnl_min":          round(pnls[0], 0),
            "pnl_max":          round(pnls[-1], 0),
            "var_95":           round(pnls[int(n * 0.05)], 0),   # peor 5%
            "var_99":           round(pnls[int(n * 0.01)], 0),   # peor 1%
            "prob_perdida":     round(len([p for p in pnls if p < 0]) / n * 100, 1),
            "prob_ruina":       round(len([p for p in pnls if p < -precio_entrada * cantidad * 0.2]) / n * 100, 1),
            "escenario":        escenario.config["nombre"],
        }

    # ── LISTAR ESCENARIOS ─────────────────────────────────────
    @staticmethod
    def listar_escenarios():
        print("\n📋 Escenarios disponibles:\n")
        for id_, cfg in ESCENARIOS.items():
            print(f"  {cfg['emoji']} [{id_}]")
            print(f"     {cfg['nombre']} ({cfg['fecha_inicio'][:4]})")
            print(f"     Caída máx: {cfg['caida_max']}% | Vol: {cfg['volatilidad_mult']}x | Slippage: {cfg['slippage_extra']*100:.1f}%\n")


# ══════════════════════════════════════════════════════════════
# TEST — python blackswan.py
# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("\n" + "="*55)
    print("  InvertirCL — Black Swan Simulator v1.0")
    print("="*55)

    sim = BlackSwanSimulator()
    sim.listar_escenarios()

    # Cargar COVID 2020
    print("\n🦠 Cargando escenario: COVID Marzo 2020...")
    escenario = sim.cargar_escenario("covid_2020")
    print(escenario.resumen())

    # Mostrar primeros 10 días
    print("\n📊 Primeros 10 días del escenario:")
    for dia in escenario.dias[:10]:
        print(f"  {dia.resumen()}")

    # Simular trade en plena crisis
    print("\n🎮 Simulando trade: LONG IPSA en plena crisis COVID...")
    resultado = sim.simular_trade(
        escenario       = escenario,
        dia_entrada     = 5,
        precio_entrada  = 4800,
        stop_loss       = 4400,   # stop 8% abajo
        take_profit     = 5400,   # TP 12% arriba
        cantidad        = 100,
        side            = "buy",
    )
    print(resultado.resumen())

    # Stress test
    print("\n🔬 Stress Test: 500 simulaciones del mismo trade...")
    stress = sim.stress_test(
        escenario_id   = "covid_2020",
        precio_entrada = 4800,
        stop_loss      = 4400,
        take_profit    = 5400,
        cantidad       = 100,
        n_simulaciones = 500,
    )
    print(f"\n  Escenario:       {stress['escenario']}")
    print(f"  Simulaciones:    {stress['n_validas']}")
    print(f"  P&L promedio:    ${stress['pnl_promedio']:+,.0f}")
    print(f"  P&L mediano:     ${stress['pnl_mediano']:+,.0f}")
    print(f"  Mejor resultado: ${stress['pnl_max']:+,.0f}")
    print(f"  Peor resultado:  ${stress['pnl_min']:+,.0f}")
    print(f"  VaR 95%:         ${stress['var_95']:+,.0f}")
    print(f"  VaR 99%:         ${stress['var_99']:+,.0f}")
    print(f"  Prob. pérdida:   {stress['prob_perdida']}%")
    print(f"  Prob. ruina:     {stress['prob_ruina']}%")

    print("\n" + "="*55)
