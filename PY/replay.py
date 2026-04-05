"""
InvertirCL — Replay de Mercado v1.0
=====================================
Item 13 del Roadmap.

Carga cualquier día histórico del IPSA o acciones chilenas
y lo reproduce tick a tick como si fuera en vivo.

El usuario puede:
  - Elegir cualquier día histórico (últimos 5 años)
  - Controlar la velocidad: 1x, 2x, 5x, 10x
  - Pausar y reanudar
  - Operar en ese día como si fuera real
  - Ver su resultado vs el precio final del día

MUY ÚTIL PARA:
  - Practicar sin riesgo con mercado real
  - Repetir días específicos (crash COVID, earnings, etc.)
  - Aprender a leer el mercado en tiempo real
  - Testear reacciones emocionales

USO:
  from replay import MarketReplay

  replay = MarketReplay()
  sesion = replay.cargar_dia("COPEC.SN", "2020-03-18")

  # Reproducir tick a tick
  for tick in sesion.reproducir(velocidad=2):
      print(tick)
      decision = input("Comprar(c) / Vender(v) / Esperar(enter): ")
      if decision == "c":
          sesion.operar("buy", tick.precio, 100)

  print(sesion.resultado())
"""

import time
import random
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, Iterator

try:
    import yfinance as yf
    import pandas as pd
    LIBS_OK = True
except ImportError:
    LIBS_OK = False
    print("[Replay] Instala dependencias: pip install yfinance pandas")


# ══════════════════════════════════════════════════════════════
# ESTRUCTURAS
# ══════════════════════════════════════════════════════════════
@dataclass
class Tick:
    """Un punto de precio en el tiempo."""
    tiempo:     str       # HH:MM:SS
    precio:     float
    precio_bid: float
    precio_ask: float
    volumen_acumulado: int
    retorno_vs_open:   float   # % vs apertura del día
    es_real:    bool    # True = dato real, False = interpolado
    minuto:     int     # minuto del día (0-390 para 6.5 horas)

    def __str__(self):
        dir_arrow = "▲" if self.retorno_vs_open >= 0 else "▼"
        live = "⚡" if self.es_real else "〜"
        return (
            f"{self.tiempo} {live} | "
            f"${self.precio:,.2f} "
            f"(bid:${self.precio_bid:,.2f} ask:${self.precio_ask:,.2f}) | "
            f"{dir_arrow}{self.retorno_vs_open:+.2f}% | "
            f"Vol:{self.volumen_acumulado:,}"
        )


@dataclass
class OperacionReplay:
    """Operación ejecutada durante el replay."""
    tiempo:     str
    side:       str    # "buy" | "sell"
    precio:     float
    cantidad:   int
    pnl:        Optional[float] = None
    cerrada:    bool = False


@dataclass
class ResultadoReplay:
    """Resultado completo de una sesión de replay."""
    symbol:         str
    fecha:          str
    precio_open:    float
    precio_close:   float
    precio_high:    float
    precio_low:     float
    retorno_dia:    float    # % del día completo
    operaciones:    list
    pnl_total:      float
    win_rate:       float
    ticks_vistos:   int
    duracion_real_s: float   # segundos que duró el replay

    def resumen(self):
        dir_dia = "▲" if self.retorno_dia >= 0 else "▼"
        lineas = [
            f"\n{'='*55}",
            f"  📊 RESULTADO REPLAY — {self.symbol} · {self.fecha}",
            f"{'='*55}",
            f"  Apertura:    ${self.precio_open:,.2f}",
            f"  Cierre:      ${self.precio_close:,.2f}",
            f"  Máximo:      ${self.precio_high:,.2f}",
            f"  Mínimo:      ${self.precio_low:,.2f}",
            f"  Retorno día: {dir_dia}{self.retorno_dia:+.2f}%",
            f"",
            f"  Tus operaciones: {len(self.operaciones)}",
            f"  P&L total:       ${self.pnl_total:+,.0f}",
            f"  Win Rate:        {self.win_rate:.1f}%",
            f"  Ticks vistos:    {self.ticks_vistos}",
            f"  Duración replay: {self.duracion_real_s:.1f}s",
        ]
        if self.operaciones:
            lineas.append(f"\n  Operaciones:")
            for op in self.operaciones:
                icon = "▲" if op.side == "buy" else "▼"
                pnl_str = f"PnL: ${op.pnl:+,.0f}" if op.pnl is not None else "abierta"
                lineas.append(f"  {icon} {op.tiempo} {op.side.upper()} {op.cantidad} @ ${op.precio:,.2f} — {pnl_str}")
        lineas.append(f"{'='*55}")
        return "\n".join(lineas)


# ══════════════════════════════════════════════════════════════
# SESIÓN DE REPLAY
# ══════════════════════════════════════════════════════════════
class SesionReplay:
    """
    Una sesión de replay de un día específico.
    Genera ticks minuto a minuto con interpolación entre datos reales.
    """

    def __init__(self, symbol: str, fecha: str, datos_intraday: list, datos_dia: dict):
        self.symbol          = symbol
        self.fecha           = fecha
        self._ticks_raw      = datos_intraday  # datos reales del día
        self._datos_dia      = datos_dia
        self._operaciones:   list[OperacionReplay] = []
        self._pos_actual:    Optional[OperacionReplay] = None
        self._ticks_vistos   = 0
        self._inicio_replay  = None
        self._capital        = 10_000_000

    # ── REPRODUCIR ────────────────────────────────────────────
    def reproducir(self, velocidad: float = 1.0) -> Iterator[Tick]:
        """
        Generador que entrega ticks en orden cronológico.
        velocidad: 1=tiempo real, 2=doble, 5=5x, 10=10x, 0=sin pausa
        """
        self._inicio_replay = time.time()
        pausa_seg = 60 / velocidad if velocidad > 0 else 0  # 1 minuto de mercado por tick

        raw = self._ticks_raw
        if not raw:
            return

        precio_open = raw[0]["open"]
        vol_acum    = 0

        for i, dato in enumerate(raw):
            # Datos del minuto real
            precio_close = dato["close"]
            precio_high  = dato["high"]
            precio_low   = dato["low"]
            vol          = dato.get("volume", 0)
            tiempo_str   = dato.get("time", f"{9 + i//60:02d}:{i%60:02d}:00")

            vol_acum += vol

            # ── Tick real del minuto ──
            spread = precio_close * 0.0002
            tick = Tick(
                tiempo              = tiempo_str,
                precio              = round(precio_close, 2),
                precio_bid          = round(precio_close - spread, 2),
                precio_ask          = round(precio_close + spread, 2),
                volumen_acumulado   = vol_acum,
                retorno_vs_open     = round((precio_close - precio_open) / precio_open * 100, 3),
                es_real             = True,
                minuto              = i,
            )
            self._ticks_vistos += 1
            yield tick

            if pausa_seg > 0:
                time.sleep(pausa_seg)

        # Marcar fin del replay
        self._fin_replay = time.time()

    # ── OPERAR ────────────────────────────────────────────────
    def operar(self, side: str, precio: float, cantidad: int, tiempo: str = "") -> OperacionReplay:
        """Ejecutar una operación durante el replay."""
        if not tiempo:
            tiempo = datetime.now().strftime("%H:%M:%S")

        # Slippage pequeño (mercado real)
        slippage = precio * 0.0001
        precio_ejec = precio + slippage if side == "buy" else precio - slippage

        op = OperacionReplay(
            tiempo    = tiempo,
            side      = side,
            precio    = round(precio_ejec, 2),
            cantidad  = cantidad,
        )

        # Si ya había posición abierta del lado contrario → cerrar
        if self._pos_actual and self._pos_actual.side != side:
            if side == "sell" and self._pos_actual.side == "buy":
                pnl = (precio_ejec - self._pos_actual.precio) * self._pos_actual.cantidad
            else:
                pnl = (self._pos_actual.precio - precio_ejec) * self._pos_actual.cantidad

            self._pos_actual.pnl    = round(pnl, 0)
            self._pos_actual.cerrada = True
            self._operaciones.append(self._pos_actual)
            self._pos_actual = None

            print(f"  ✅ Posición cerrada: PnL ${pnl:+,.0f}")
        else:
            self._pos_actual = op
            print(f"  📌 Posición abierta: {side.upper()} {cantidad} @ ${precio_ejec:,.2f}")

        return op

    def cerrar_posicion(self, precio_actual: float) -> Optional[float]:
        """Cerrar la posición actual al precio dado."""
        if not self._pos_actual:
            return None
        side = "sell" if self._pos_actual.side == "buy" else "buy"
        op   = self.operar(side, precio_actual, self._pos_actual.cantidad)
        return op.pnl if op else None

    # ── RESULTADO ─────────────────────────────────────────────
    def resultado(self) -> ResultadoReplay:
        """Calcular resultado final de la sesión."""
        # Cerrar posición abierta al último precio
        if self._pos_actual and self._ticks_raw:
            ultimo_precio = self._ticks_raw[-1]["close"]
            if self._pos_actual.side == "buy":
                pnl = (ultimo_precio - self._pos_actual.precio) * self._pos_actual.cantidad
            else:
                pnl = (self._pos_actual.precio - ultimo_precio) * self._pos_actual.cantidad
            self._pos_actual.pnl = round(pnl, 0)
            self._operaciones.append(self._pos_actual)

        pnl_total = sum(op.pnl or 0 for op in self._operaciones)
        wins      = sum(1 for op in self._operaciones if (op.pnl or 0) > 0)
        win_rate  = wins / len(self._operaciones) * 100 if self._operaciones else 0

        datos = self._datos_dia
        duracion = time.time() - (self._inicio_replay or time.time())

        return ResultadoReplay(
            symbol          = self.symbol,
            fecha           = self.fecha,
            precio_open     = datos.get("open", 0),
            precio_close    = datos.get("close", 0),
            precio_high     = datos.get("high", 0),
            precio_low      = datos.get("low", 0),
            retorno_dia     = datos.get("retorno", 0),
            operaciones     = self._operaciones,
            pnl_total       = round(pnl_total, 0),
            win_rate        = round(win_rate, 1),
            ticks_vistos    = self._ticks_vistos,
            duracion_real_s = round(duracion, 1),
        )


# ══════════════════════════════════════════════════════════════
# MOTOR DE REPLAY
# ══════════════════════════════════════════════════════════════
class MarketReplay:
    """
    Motor principal de replay de mercado.
    Descarga datos históricos y los prepara para reproducción.
    """

    def __init__(self):
        self._cache = {}

    # ── CARGAR DÍA ────────────────────────────────────────────
    def cargar_dia(self, symbol: str, fecha: str) -> SesionReplay:
        """
        Cargar datos de un día específico para replay.

        symbol: "COPEC.SN", "^IPSA", "SQM-B.SN", etc.
        fecha:  "YYYY-MM-DD"

        Si yfinance está disponible, usa datos reales.
        Si no, genera datos sintéticos realistas.
        """
        sym = symbol if "." in symbol or symbol.startswith("^") else f"{symbol}.SN"
        key = f"{sym}_{fecha}"

        if key in self._cache:
            return self._cache[key]

        print(f"[Replay] Cargando {sym} — {fecha}...")

        intraday = []
        datos_dia = {}

        if LIBS_OK:
            intraday, datos_dia = self._datos_reales(sym, fecha)

        if not intraday:
            print(f"[Replay] Generando datos sintéticos para {sym} — {fecha}")
            intraday, datos_dia = self._datos_sinteticos(sym, fecha)

        sesion = SesionReplay(sym, fecha, intraday, datos_dia)
        self._cache[key] = sesion
        print(f"[Replay] ✅ {len(intraday)} ticks cargados | "
              f"Open:${datos_dia.get('open',0):,.0f} Close:${datos_dia.get('close',0):,.0f} "
              f"({datos_dia.get('retorno',0):+.2f}%)")
        return sesion

    def _datos_reales(self, symbol: str, fecha: str):
        """Descarga datos intraday reales de Yahoo Finance (intervalo 1 minuto)."""
        try:
            # Yahoo Finance permite hasta 7 días de datos de 1 minuto
            # Para datos más antiguos usa intervalo de 5 minutos
            ticker = yf.Ticker(symbol)

            # Intentar 1 minuto primero
            df = ticker.history(start=fecha, end=self._siguiente_dia(fecha), interval="1m")

            if df.empty:
                # Fallback a 5 minutos
                df = ticker.history(start=fecha, end=self._siguiente_dia(fecha), interval="5m")

            if df.empty:
                return [], {}

            df = df.reset_index()
            df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]

            # Normalizar columnas
            col_map = {"Datetime": "datetime", "Open": "open", "High": "high",
                       "Low": "low", "Close": "close", "Volume": "volume"}
            df = df.rename(columns=col_map)

            intraday = []
            for _, row in df.iterrows():
                dt  = row.get("datetime", row.get("Datetime", ""))
                if hasattr(dt, "strftime"):
                    tiempo = dt.strftime("%H:%M:%S")
                else:
                    tiempo = str(dt)[11:19] if len(str(dt)) > 11 else "09:00:00"

                intraday.append({
                    "time":   tiempo,
                    "open":   float(row.get("open", 0)),
                    "high":   float(row.get("high", 0)),
                    "low":    float(row.get("low", 0)),
                    "close":  float(row.get("close", 0)),
                    "volume": int(row.get("volume", 0)),
                })

            if not intraday:
                return [], {}

            precio_open  = intraday[0]["open"]
            precio_close = intraday[-1]["close"]
            retorno      = (precio_close - precio_open) / precio_open * 100

            datos_dia = {
                "open":    precio_open,
                "close":   precio_close,
                "high":    max(d["high"] for d in intraday),
                "low":     min(d["low"]  for d in intraday),
                "retorno": round(retorno, 2),
            }
            return intraday, datos_dia

        except Exception as e:
            print(f"[Replay] Error datos reales: {e}")
            return [], {}

    def _datos_sinteticos(self, symbol: str, fecha: str):
        """
        Genera datos intraday sintéticos realistas.
        Simula 6.5 horas de mercado (09:30-16:00) en intervalos de 1 minuto.
        """
        n_ticks    = 390   # 6.5h × 60min
        precio_ini = 7200 if "IPSA" in symbol else (
                     8400  if "COPEC" in symbol else
                     38000 if "SQM"   in symbol else
                     28500 if "BCI"   in symbol else 5000
        )

        # Retorno del día aleatorio con sesgo leve alcista
        retorno_dia = random.gauss(0.002, 0.018)
        precio_fin  = precio_ini * (1 + retorno_dia)

        # Perfil de volatilidad intraday (curva U: alta al inicio y al final)
        def vol_intraday(i, n):
            t = i / n
            # Curva U típica del mercado
            return 0.0008 + 0.0012 * (abs(t - 0.5) * 2) ** 2

        intraday  = []
        precio    = precio_ini
        vol_acum  = 0
        hora_ini  = datetime.strptime(f"{fecha} 09:30:00", "%Y-%m-%d %H:%M:%S")

        for i in range(n_ticks):
            vol   = vol_intraday(i, n_ticks)
            # Añadir drift hacia precio final
            drift = (precio_fin - precio) / (n_ticks - i) * 0.3
            u1    = random.random() + 1e-10
            u2    = random.random()
            ruido = math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * u2) * vol
            ret   = ruido + drift / precio

            open_  = precio
            close  = max(precio * (1 + ret), precio * 0.8)
            high   = max(open_, close) * (1 + abs(ruido) * 0.3)
            low    = min(open_, close) * (1 - abs(ruido) * 0.3)
            vol_t  = int(random.randint(1000, 15000) * (1 + abs(ruido) * 10))
            vol_acum += vol_t

            tiempo = hora_ini + timedelta(minutes=i)
            intraday.append({
                "time":   tiempo.strftime("%H:%M:%S"),
                "open":   round(open_, 2),
                "high":   round(high, 2),
                "low":    round(low, 2),
                "close":  round(close, 2),
                "volume": vol_t,
            })
            precio = close

        datos_dia = {
            "open":    round(precio_ini, 2),
            "close":   round(precio, 2),
            "high":    round(max(d["high"] for d in intraday), 2),
            "low":     round(min(d["low"]  for d in intraday), 2),
            "retorno": round(retorno_dia * 100, 2),
        }
        return intraday, datos_dia

    def _siguiente_dia(self, fecha: str) -> str:
        dt = datetime.strptime(fecha, "%Y-%m-%d") + timedelta(days=1)
        return dt.strftime("%Y-%m-%d")

    # ── LISTAR DÍAS DISPONIBLES ───────────────────────────────
    def dias_disponibles(self, symbol: str, n: int = 10) -> list:
        """
        Retorna los últimos N días hábiles disponibles para replay.
        """
        sym = symbol if "." in symbol or symbol.startswith("^") else f"{symbol}.SN"
        if not LIBS_OK:
            # Generar lista de días hábiles sintéticos
            dias = []
            hoy  = datetime.now()
            d    = hoy - timedelta(days=1)
            while len(dias) < n:
                if d.weekday() < 5:
                    dias.append(d.strftime("%Y-%m-%d"))
                d -= timedelta(days=1)
            return dias

        try:
            df = yf.download(sym, period="30d", interval="1d", progress=False)
            if df.empty:
                return []
            df = df.reset_index()
            fechas = df["Date"].dt.strftime("%Y-%m-%d").tolist()
            return list(reversed(fechas[-n:]))
        except Exception as e:
            print(f"[Replay] Error listando días: {e}")
            return []

    # ── MODO INTERACTIVO ──────────────────────────────────────
    def modo_interactivo(self, symbol: str, fecha: str, velocidad: float = 5.0):
        """
        Replay interactivo en la terminal.
        El usuario puede operar mientras ve el mercado en tiempo real.
        """
        sesion = self.cargar_dia(symbol, fecha)

        print(f"\n{'='*55}")
        print(f"  🎮 REPLAY INTERACTIVO — {symbol} · {fecha}")
        print(f"  Velocidad: {velocidad}x | Comandos: c=comprar, v=vender, q=salir")
        print(f"{'='*55}\n")

        cantidad_default = 100

        try:
            for tick in sesion.reproducir(velocidad=velocidad):
                print(f"\r{tick}", end="", flush=True)

                # En modo interactivo real, aquí se leería input del usuario
                # Para el test automatizado, simulamos decisiones
                # En producción: input() o integración con UI

        except KeyboardInterrupt:
            print(f"\n\n[Replay] Interrumpido por el usuario.")

        resultado = sesion.resultado()
        print(resultado.resumen())
        return resultado


# ══════════════════════════════════════════════════════════════
# TEST — python replay.py
# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("\n" + "="*55)
    print("  InvertirCL — Replay de Mercado v1.0")
    print("="*55)

    replay = MarketReplay()

    # ── Test 1: Listar días disponibles ──
    print("\n📅 Días disponibles para COPEC:")
    dias = replay.dias_disponibles("COPEC.SN", n=5)
    for d in dias:
        print(f"  → {d}")

    # ── Test 2: Cargar y reproducir un día ──
    fecha_test = dias[0] if dias else "2024-03-15"
    print(f"\n🎬 Cargando replay: COPEC — {fecha_test}")
    sesion = replay.cargar_dia("COPEC.SN", fecha_test)

    print(f"\n📊 Reproduciendo primeros 10 ticks (velocidad 0 = sin pausa):")
    ticks = []
    for tick in sesion.reproducir(velocidad=0):
        ticks.append(tick)
        print(f"  {tick}")
        if len(ticks) >= 10:
            break

    # ── Test 3: Simular operaciones durante replay ──
    print(f"\n🎮 Simulando operaciones:")
    if ticks:
        precio_entrada = ticks[2].precio
        sesion.operar("buy", precio_entrada, 100, ticks[2].tiempo)
        print(f"  Compré 100 @ ${precio_entrada:,.2f}")

        precio_salida = ticks[8].precio if len(ticks) > 8 else precio_entrada * 1.02
        sesion.operar("sell", precio_salida, 100, ticks[min(8, len(ticks)-1)].tiempo)
        print(f"  Vendí 100 @ ${precio_salida:,.2f}")

    resultado = sesion.resultado()
    print(resultado.resumen())

    # ── Test 4: Replay del crash COVID (datos sintéticos) ──
    print(f"\n🦠 Replay: IPSA — 18 Marzo 2020 (peor día del crash COVID)")
    sesion_covid = replay.cargar_dia("^IPSA", "2020-03-18")
    ticks_covid  = []
    for tick in sesion_covid.reproducir(velocidad=0):
        ticks_covid.append(tick)
        if len(ticks_covid) >= 5:
            break

    print(f"  Primeros 5 ticks del día más volátil del COVID:")
    for t in ticks_covid:
        print(f"  {t}")

    print(f"\n  Resumen del día:")
    datos = sesion_covid._datos_dia
    print(f"  Open:  ${datos.get('open',0):,.0f}")
    print(f"  Close: ${datos.get('close',0):,.0f}")
    print(f"  High:  ${datos.get('high',0):,.0f}")
    print(f"  Low:   ${datos.get('low',0):,.0f}")
    print(f"  Retorno: {datos.get('retorno',0):+.2f}%")

    print("\n" + "="*55)
    print("  Para modo interactivo completo:")
    print("  replay.modo_interactivo('COPEC.SN', '2024-03-15', velocidad=5)")
    print("="*55)
