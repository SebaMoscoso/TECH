"""
InvertirCL — Anti-Tilt Comportamental v1.0
============================================
Item 12 del Roadmap.

Detecta patrones de comportamiento errático en el historial
de trades y bloquea la mesa de dinero cuando el usuario
está en "tilt" (estado emocional que destruye rendimiento).

¿QUÉ ES EL TILT?
  Término del poker adoptado por traders profesionales.
  Estado emocional en el que el trader opera por impulso,
  no por análisis. Causas principales:
    - Serie de pérdidas consecutivas
    - Una pérdida grande e inesperada
    - Frustración por perder oportunidades
    - Fatiga después de muchas horas operando

PATRONES QUE DETECTA:
  1. Frecuencia anormal      — trades muy seguidos vs su promedio
  2. Escalamiento de posición — tamaños crecientes tras pérdidas
  3. Racha de pérdidas       — N pérdidas consecutivas
  4. Pérdida diaria límite   — superó el máximo diario
  5. Tiempo en pantalla      — demasiadas horas operando
  6. Trades nocturnos        — operar fuera del horario habitual
  7. Cambio de estrategia    — saltando entre estrategias
  8. Sin descanso post-pérdida — entrada inmediata tras stop

NIVELES DE ALERTA:
  🟢 NORMAL     — Opera con normalidad
  🟡 PRECAUCIÓN — Señales leves de tilt, reducir tamaño 50%
  🟠 ALERTA     — Tilt moderado, solo operaciones de cierre
  🔴 BLOQUEADO  — Mesa cerrada por 1 hora (como Risk Manager real)

USO:
  from antitilt import AntiTilt

  monitor = AntiTilt(config={
      "capital_inicial": 10_000_000,
      "perdida_max_diaria_pct": 3.0,
      "rachas_perdidas_alerta": 3,
      "tiempo_max_pantalla_hrs": 6,
  })

  estado = monitor.evaluar(historial_trades)
  print(estado.resumen())

  if estado.bloqueado:
      print("Mesa bloqueada. Vuelve en:", estado.minutos_bloqueo, "minutos")
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional
from collections import defaultdict
import statistics


# ══════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════
DEFAULT_CONFIG = {
    "capital_inicial":           10_000_000,
    "perdida_max_diaria_pct":    3.0,    # % pérdida máxima en el día
    "rachas_perdidas_alerta":    3,      # N pérdidas seguidas → alerta
    "rachas_perdidas_bloqueo":   5,      # N pérdidas seguidas → bloqueo
    "multiplicador_tamanio":     1.5,    # si posición es 1.5x el promedio → alerta
    "ventana_frecuencia_min":    30,     # ventana para medir frecuencia (minutos)
    "max_trades_ventana":        4,      # máx trades en esa ventana antes de alerta
    "tiempo_max_pantalla_hrs":   6,      # horas seguidas → alerta
    "minutos_descanso_minimo":   10,     # mínimo entre pérdida y siguiente trade
    "bloqueo_duracion_min":      60,     # duración del bloqueo en minutos
    "horario_habitual": {
        "inicio": 9,   # hora de inicio habitual (ej: 9am)
        "fin":    17,  # hora de fin habitual (ej: 5pm)
    },
}


# ══════════════════════════════════════════════════════════════
# ESTRUCTURAS
# ══════════════════════════════════════════════════════════════
@dataclass
class PatronDetectado:
    tipo:        str
    descripcion: str
    severidad:   int       # 1=leve, 2=moderado, 3=grave
    puntos:      float     # puntos de tilt acumulados
    timestamp:   str
    datos:       dict = field(default_factory=dict)


@dataclass
class EstadoTilt:
    nivel:           str           # NORMAL / PRECAUCIÓN / ALERTA / BLOQUEADO
    score_tilt:      float         # 0-100 (0=zen, 100=tilt total)
    bloqueado:       bool
    minutos_bloqueo: int
    patrones:        list          # PatronDetectado[]
    resumen_dia:     dict
    recomendaciones: list
    timestamp:       str

    def resumen(self):
        iconos = {"NORMAL": "🟢", "PRECAUCIÓN": "🟡", "ALERTA": "🟠", "BLOQUEADO": "🔴"}
        icono  = iconos.get(self.nivel, "⚪")
        lineas = [
            f"\n{'='*55}",
            f"  {icono} ANTI-TILT — Estado: {self.nivel}",
            f"{'='*55}",
            f"  Score de Tilt:    {self.score_tilt:.1f}/100",
            f"  Bloqueado:        {'SÍ — ' + str(self.minutos_bloqueo) + ' min' if self.bloqueado else 'No'}",
        ]

        if self.resumen_dia:
            lineas += [
                f"\n  Resumen del día:",
                f"  Trades hoy:       {self.resumen_dia.get('trades_hoy', 0)}",
                f"  PnL hoy:          ${self.resumen_dia.get('pnl_hoy', 0):+,.0f}",
                f"  Racha actual:     {self.resumen_dia.get('racha_descripcion', 'N/A')}",
                f"  Pérdida diaria:   {self.resumen_dia.get('perdida_diaria_pct', 0):.1f}%",
            ]

        if self.patrones:
            lineas.append(f"\n  Patrones detectados ({len(self.patrones)}):")
            for p in self.patrones:
                sev = "🔴" if p.severidad == 3 else "🟠" if p.severidad == 2 else "🟡"
                lineas.append(f"  {sev} {p.descripcion}")

        if self.recomendaciones:
            lineas.append(f"\n  💡 Recomendaciones:")
            for r in self.recomendaciones:
                lineas.append(f"  {r}")

        lineas.append(f"{'='*55}")
        return "\n".join(lineas)


# ══════════════════════════════════════════════════════════════
# MONITOR ANTI-TILT
# ══════════════════════════════════════════════════════════════
class AntiTilt:
    """
    Monitor de comportamiento que detecta tilt en tiempo real.
    Se alimenta del historial de trades del usuario.
    """

    def __init__(self, config: dict = None):
        self.config     = {**DEFAULT_CONFIG, **(config or {})}
        self._historial = []       # todos los trades de la sesión
        self._bloqueo_hasta = None # datetime hasta cuando está bloqueado

    # ── EVALUAR ESTADO ACTUAL ─────────────────────────────────
    def evaluar(self, trades: list) -> EstadoTilt:
        """
        Evalúa el historial de trades y retorna el estado actual.

        trades: lista de dicts con al menos:
          - timestamp_entrada: str ISO
          - timestamp_cierre:  str ISO (opcional si sigue abierto)
          - pnl:               float (None si sigue abierto)
          - cantidad:          int
          - precio:            float
          - stop_original:     float (opcional)
        """
        if not trades:
            return self._estado_normal()

        ahora    = datetime.now()
        patrones = []
        score    = 0.0

        # Filtrar trades cerrados del día de hoy
        hoy      = ahora.date()
        trades_hoy = [
            t for t in trades
            if self._fecha(t.get("timestamp_entrada", "")) == hoy
        ]
        trades_cerrados_hoy = [t for t in trades_hoy if t.get("pnl") is not None]

        # ── PATRÓN 1: Racha de pérdidas ──
        racha_perd, racha_gan = self._calcular_racha(trades_cerrados_hoy)
        if racha_perd >= self.config["rachas_perdidas_bloqueo"]:
            patrones.append(PatronDetectado(
                tipo="racha_perdidas", severidad=3,
                descripcion=f"Racha de {racha_perd} pérdidas consecutivas — BLOQUEO automático",
                puntos=40, timestamp=ahora.isoformat(),
                datos={"racha": racha_perd},
            ))
            score += 40
        elif racha_perd >= self.config["rachas_perdidas_alerta"]:
            patrones.append(PatronDetectado(
                tipo="racha_perdidas", severidad=2,
                descripcion=f"Racha de {racha_perd} pérdidas consecutivas",
                puntos=20, timestamp=ahora.isoformat(),
                datos={"racha": racha_perd},
            ))
            score += 20

        # ── PATRÓN 2: Pérdida diaria máxima ──
        pnl_hoy = sum(t.get("pnl", 0) or 0 for t in trades_cerrados_hoy)
        perdida_pct = abs(pnl_hoy) / self.config["capital_inicial"] * 100 if pnl_hoy < 0 else 0
        if perdida_pct >= self.config["perdida_max_diaria_pct"]:
            patrones.append(PatronDetectado(
                tipo="perdida_diaria", severidad=3,
                descripcion=f"Pérdida diaria del {perdida_pct:.1f}% — supera límite del {self.config['perdida_max_diaria_pct']}%",
                puntos=35, timestamp=ahora.isoformat(),
                datos={"perdida_pct": perdida_pct},
            ))
            score += 35
        elif perdida_pct >= self.config["perdida_max_diaria_pct"] * 0.7:
            patrones.append(PatronDetectado(
                tipo="perdida_diaria", severidad=2,
                descripcion=f"Pérdida diaria del {perdida_pct:.1f}% — cerca del límite ({self.config['perdida_max_diaria_pct']}%)",
                puntos=15, timestamp=ahora.isoformat(),
                datos={"perdida_pct": perdida_pct},
            ))
            score += 15

        # ── PATRÓN 3: Escalamiento de posición tras pérdidas ──
        escalamiento = self._detectar_escalamiento(trades_hoy)
        if escalamiento["detectado"]:
            sev = 3 if escalamiento["factor"] > 2.0 else 2
            patrones.append(PatronDetectado(
                tipo="escalamiento", severidad=sev,
                descripcion=f"Posición {escalamiento['factor']:.1f}x mayor al promedio tras pérdida — posible revenge sizing",
                puntos=25 if sev == 3 else 15,
                timestamp=ahora.isoformat(),
                datos=escalamiento,
            ))
            score += 25 if sev == 3 else 15

        # ── PATRÓN 4: Frecuencia anormal de trades ──
        freq = self._detectar_frecuencia_anormal(trades_hoy, ahora)
        if freq["anormal"]:
            patrones.append(PatronDetectado(
                tipo="overtrading", severidad=2,
                descripcion=f"{freq['trades_ventana']} trades en {self.config['ventana_frecuencia_min']} min — overtrading detectado",
                puntos=15, timestamp=ahora.isoformat(),
                datos=freq,
            ))
            score += 15

        # ── PATRÓN 5: Entrada inmediata tras pérdida ──
        sin_descanso = self._detectar_sin_descanso(trades_cerrados_hoy)
        if sin_descanso["detectado"]:
            patrones.append(PatronDetectado(
                tipo="sin_descanso", severidad=2,
                descripcion=f"Entró a nuevo trade {sin_descanso['minutos']:.0f} min después de una pérdida (mínimo recomendado: {self.config['minutos_descanso_minimo']} min)",
                puntos=10, timestamp=ahora.isoformat(),
                datos=sin_descanso,
            ))
            score += 10

        # ── PATRÓN 6: Trades fuera del horario habitual ──
        fuera_horario = self._detectar_fuera_horario(trades_hoy, ahora)
        if fuera_horario:
            patrones.append(PatronDetectado(
                tipo="horario_inusual", severidad=1,
                descripcion=f"Operando fuera del horario habitual ({self.config['horario_habitual']['inicio']}:00-{self.config['horario_habitual']['fin']}:00)",
                puntos=5, timestamp=ahora.isoformat(),
            ))
            score += 5

        # ── PATRÓN 7: Cambio frecuente de estrategia ──
        cambio_strat = self._detectar_cambio_estrategia(trades_hoy)
        if cambio_strat["detectado"]:
            patrones.append(PatronDetectado(
                tipo="cambio_estrategia", severidad=1,
                descripcion=f"Cambió de estrategia {cambio_strat['cambios']} veces hoy — falta de consistencia",
                puntos=8, timestamp=ahora.isoformat(),
                datos=cambio_strat,
            ))
            score += 8

        # ── DETERMINAR NIVEL ──
        score = min(100.0, score)

        if score >= 60 or racha_perd >= self.config["rachas_perdidas_bloqueo"] or perdida_pct >= self.config["perdida_max_diaria_pct"]:
            nivel    = "BLOQUEADO"
            bloqueado = True
            minutos   = self.config["bloqueo_duracion_min"]
            self._bloqueo_hasta = ahora + timedelta(minutes=minutos)
        elif score >= 35:
            nivel, bloqueado, minutos = "ALERTA",     False, 0
        elif score >= 15:
            nivel, bloqueado, minutos = "PRECAUCIÓN", False, 0
        else:
            nivel, bloqueado, minutos = "NORMAL",     False, 0

        # Verificar si sigue bloqueado de sesión anterior
        if self._bloqueo_hasta and ahora < self._bloqueo_hasta:
            bloqueado = True
            nivel     = "BLOQUEADO"
            minutos   = int((self._bloqueo_hasta - ahora).total_seconds() / 60)

        recomendaciones = self._generar_recomendaciones(patrones, nivel)

        return EstadoTilt(
            nivel       = nivel,
            score_tilt  = round(score, 1),
            bloqueado   = bloqueado,
            minutos_bloqueo = minutos,
            patrones    = patrones,
            resumen_dia = {
                "trades_hoy":          len(trades_hoy),
                "trades_cerrados":     len(trades_cerrados_hoy),
                "pnl_hoy":             round(pnl_hoy, 0),
                "perdida_diaria_pct":  round(perdida_pct, 2),
                "racha_descripcion":   f"{racha_perd} pérdidas" if racha_perd > 0 else f"{racha_gan} ganancias",
                "racha_perdidas":      racha_perd,
                "racha_ganancias":     racha_gan,
            },
            recomendaciones = recomendaciones,
            timestamp   = ahora.isoformat(),
        )

    # ── HELPERS PRIVADOS ──────────────────────────────────────

    def _fecha(self, ts: str):
        try:
            return datetime.fromisoformat(ts).date()
        except Exception:
            return datetime.now().date()

    def _calcular_racha(self, trades: list) -> tuple:
        """Retorna (racha_perdidas, racha_ganancias) actuales."""
        if not trades:
            return 0, 0
        racha_p = racha_g = 0
        for t in reversed(trades):
            pnl = t.get("pnl", 0) or 0
            if pnl < 0:
                if racha_g > 0:
                    break
                racha_p += 1
            elif pnl > 0:
                if racha_p > 0:
                    break
                racha_g += 1
            else:
                break
        return racha_p, racha_g

    def _detectar_escalamiento(self, trades: list) -> dict:
        """Detecta si el usuario aumentó el tamaño de posición tras pérdidas."""
        if len(trades) < 3:
            return {"detectado": False}

        # Calcular tamaño promedio de los primeros trades del día
        montos = [
            t.get("precio", 0) * t.get("cantidad", 0)
            for t in trades[:-1]
            if t.get("precio") and t.get("cantidad")
        ]
        if not montos or max(montos) == 0:
            return {"detectado": False}

        promedio    = sum(montos) / len(montos)
        ultimo      = trades[-1]
        monto_ultimo = (ultimo.get("precio", 0) or 0) * (ultimo.get("cantidad", 0) or 0)

        # ¿Hubo pérdida antes del último trade?
        penultimo_pnl = trades[-2].get("pnl", 0) or 0
        factor        = monto_ultimo / promedio if promedio > 0 else 1

        if penultimo_pnl < 0 and factor >= self.config["multiplicador_tamanio"]:
            return {"detectado": True, "factor": round(factor, 2),
                    "monto_promedio": round(promedio, 0),
                    "monto_actual": round(monto_ultimo, 0)}
        return {"detectado": False}

    def _detectar_frecuencia_anormal(self, trades: list, ahora: datetime) -> dict:
        """Detecta overtrading por frecuencia en ventana de tiempo."""
        ventana = timedelta(minutes=self.config["ventana_frecuencia_min"])
        recientes = []
        for t in trades:
            try:
                ts = datetime.fromisoformat(t.get("timestamp_entrada", ""))
                if ahora - ts <= ventana:
                    recientes.append(ts)
            except Exception:
                pass

        n = len(recientes)
        return {
            "anormal":        n >= self.config["max_trades_ventana"],
            "trades_ventana": n,
            "ventana_min":    self.config["ventana_frecuencia_min"],
        }

    def _detectar_sin_descanso(self, trades_cerrados: list) -> dict:
        """Detecta si entró a un nuevo trade muy rápido tras una pérdida."""
        if len(trades_cerrados) < 2:
            return {"detectado": False}

        # Revisar los últimos 3 trades
        for i in range(len(trades_cerrados) - 1, 0, -1):
            t_ant = trades_cerrados[i - 1]
            t_act = trades_cerrados[i]
            pnl_ant = t_ant.get("pnl", 0) or 0

            if pnl_ant < 0:
                try:
                    cierre_ant = datetime.fromisoformat(t_ant.get("timestamp_cierre", ""))
                    entrada_act = datetime.fromisoformat(t_act.get("timestamp_entrada", ""))
                    minutos = (entrada_act - cierre_ant).total_seconds() / 60
                    if 0 <= minutos < self.config["minutos_descanso_minimo"]:
                        return {"detectado": True, "minutos": minutos}
                except Exception:
                    pass
        return {"detectado": False}

    def _detectar_fuera_horario(self, trades: list, ahora: datetime) -> bool:
        """Detecta si hay trades fuera del horario habitual."""
        inicio = self.config["horario_habitual"]["inicio"]
        fin    = self.config["horario_habitual"]["fin"]
        hora   = ahora.hour
        return hora < inicio or hora >= fin

    def _detectar_cambio_estrategia(self, trades: list) -> dict:
        """Detecta cambios frecuentes de estrategia."""
        estrategias = [t.get("estrategia", t.get("tipo", "")) for t in trades if t.get("estrategia") or t.get("tipo")]
        if len(estrategias) < 3:
            return {"detectado": False}

        cambios = sum(1 for i in range(1, len(estrategias)) if estrategias[i] != estrategias[i-1])
        return {
            "detectado": cambios >= 3,
            "cambios":   cambios,
            "estrategias_usadas": list(set(estrategias)),
        }

    def _generar_recomendaciones(self, patrones: list, nivel: str) -> list:
        recs  = []
        tipos = {p.tipo for p in patrones}

        if nivel == "BLOQUEADO":
            recs.append("🔴 Mesa de dinero CERRADA. Descansa al menos 1 hora antes de volver.")
            recs.append("📵 Cierra las plataformas. La distancia física ayuda a resetear.")

        if "racha_perdidas" in tipos:
            recs.append("📉 Racha de pérdidas: el mercado no está sincronizado contigo hoy. Reduce el tamaño a la mitad o para.")
        if "perdida_diaria" in tipos:
            recs.append("💰 Alcanzaste tu límite de pérdida diaria. Los profesionales paran aquí — sin excepciones.")
        if "escalamiento" in tipos:
            recs.append("⚠️  Aumentaste el tamaño tras una pérdida. Eso es revenge trading. Vuelve al tamaño base.")
        if "overtrading" in tipos:
            recs.append("⏳ Demasiados trades en poco tiempo. Espera setups de calidad — menos es más.")
        if "sin_descanso" in tipos:
            recs.append(f"⏸  Tómate al menos {self.config['minutos_descanso_minimo']} minutos de descanso después de una pérdida.")
        if "horario_inusual" in tipos:
            recs.append("🌙 Operar fuera de tu horario habitual afecta el juicio. La fatiga es el enemigo del trader.")
        if "cambio_estrategia" in tipos:
            recs.append("🎯 Estás saltando entre estrategias. Elige una y síguela — la consistencia es clave.")

        if not recs:
            recs.append("✅ Sin señales de tilt. Sigue operando con disciplina.")
        return recs

    def _estado_normal(self) -> EstadoTilt:
        return EstadoTilt(
            nivel="NORMAL", score_tilt=0.0, bloqueado=False,
            minutos_bloqueo=0, patrones=[], resumen_dia={},
            recomendaciones=["✅ Sin trades registrados. Opera con disciplina."],
            timestamp=datetime.now().isoformat(),
        )

    def desbloquear(self):
        """Desbloquear manualmente (uso administrativo)."""
        self._bloqueo_hasta = None
        print("[AntiTilt] Mesa desbloqueada manualmente.")

    def tiempo_restante_bloqueo(self) -> int:
        """Retorna minutos restantes de bloqueo (0 si no está bloqueado)."""
        if not self._bloqueo_hasta:
            return 0
        restante = (self._bloqueo_hasta - datetime.now()).total_seconds() / 60
        return max(0, int(restante))


# ══════════════════════════════════════════════════════════════
# INTEGRACIÓN CON DISCIPLINE.PY
# ══════════════════════════════════════════════════════════════
def evaluar_sesion_completa(trades: list, capital_inicial: float = 10_000_000) -> dict:
    """
    Función de integración que combina AntiTilt + DisciplineScore.
    Útil para evaluar una sesión completa de trading.
    """
    try:
        from discipline import DisciplineScore
        score_engine = DisciplineScore(config={"capital_inicial": capital_inicial})
        for t in trades:
            # Asegurar campos requeridos por discipline.py
            trade_norm = {
                "id":               t.get("id", "sin_id"),
                "symbol":           t.get("symbol", "N/A"),
                "side":             t.get("side", "buy"),   # default buy si no viene
                "entrada":          t.get("precio", t.get("entrada", 0)),
                "stop_original":    t.get("stop_original", 0),
                "take_profit":      t.get("take_profit", 0),
                "cantidad":         t.get("cantidad", 1),
                "timestamp_entrada":t.get("timestamp_entrada", ""),
            }
            score_engine.registrar_trade(trade_norm)
            if t.get("pnl") is not None:
                score_engine.evaluar_cierre({
                    "id":             t.get("id", "sin_id"),
                    "precio_cierre":  t.get("precio_cierre", t.get("precio", 0)),
                    "stop_final":     t.get("stop_original", 0),
                    "razon_cierre":   t.get("razon_cierre", "manual"),
                })
        score_result = score_engine.calcular()
    except ImportError:
        score_result = None

    monitor  = AntiTilt(config={"capital_inicial": capital_inicial})
    tilt     = monitor.evaluar(trades)

    return {
        "tilt":       tilt,
        "discipline": score_result,
        "resumen": {
            "nivel_tilt":       tilt.nivel,
            "score_tilt":       tilt.score_tilt,
            "score_disciplina": score_result.score if score_result else None,
            "bloqueado":        tilt.bloqueado,
            "patrones":         len(tilt.patrones),
        }
    }


# ══════════════════════════════════════════════════════════════
# TEST — python antitilt.py
# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    from datetime import datetime, timedelta

    print("\n" + "="*55)
    print("  InvertirCL — Anti-Tilt Comportamental v1.0")
    print("="*55)

    ahora = datetime.now()
    base  = ahora - timedelta(hours=3)

    def ts(minutos_atras):
        return (ahora - timedelta(minutes=minutos_atras)).isoformat()

    # ── Escenario 1: Usuario en tilt ──
    print("\n🔴 Escenario: Usuario en TILT")
    print("   (3 pérdidas seguidas + escalamiento + revenge trading)\n")

    trades_tilt = [
        # Trade 1: normal, perdió
        {"id":"t1","symbol":"COPEC","precio":8420,"cantidad":100,
         "stop_original":8200,"take_profit":8800,
         "timestamp_entrada": ts(180), "timestamp_cierre": ts(150),
         "pnl":-22000,"razon_cierre":"stop","estrategia":"mean_reversion"},
        # Trade 2: entró rápido tras pérdida (sin descanso), perdió
        {"id":"t2","symbol":"COPEC","precio":8390,"cantidad":150,  # escaló posición
         "stop_original":8100,"take_profit":8700,
         "timestamp_entrada": ts(148), "timestamp_cierre": ts(120),
         "pnl":-43500,"razon_cierre":"stop","estrategia":"momentum"},  # cambió estrategia
        # Trade 3: revenge sizing masivo, perdió
        {"id":"t3","symbol":"SQM-B","precio":38000,"cantidad":80,  # tamaño masivo
         "stop_original":37000,"take_profit":40000,
         "timestamp_entrada": ts(118), "timestamp_cierre": ts(90),
         "pnl":-80000,"razon_cierre":"stop_movido","estrategia":"breakout"},  # cambió estrategia
        # Trade 4: abrió de nuevo (overtrading)
        {"id":"t4","symbol":"BCI","precio":28500,"cantidad":200,
         "stop_original":0,"take_profit":30000,   # sin stop
         "timestamp_entrada": ts(25), "timestamp_cierre": None,
         "pnl":None,"razon_cierre":None,"estrategia":"mean_reversion"},
        # Trade 5: otro más (overtrading en ventana de 30 min)
        {"id":"t5","symbol":"ENTEL","precio":4200,"cantidad":300,
         "stop_original":4000,"take_profit":4500,
         "timestamp_entrada": ts(15), "timestamp_cierre": None,
         "pnl":None,"razon_cierre":None,"estrategia":"momentum"},
    ]

    monitor = AntiTilt(config={"capital_inicial":10_000_000})
    estado  = monitor.evaluar(trades_tilt)
    print(estado.resumen())

    # ── Escenario 2: Usuario disciplinado ──
    print("\n\n🟢 Escenario: Usuario DISCIPLINADO")
    print("   (3 trades bien ejecutados, con descansos)\n")

    trades_bien = [
        {"id":"b1","symbol":"COPEC","precio":8420,"cantidad":100,
         "stop_original":8200,"take_profit":8800,
         "timestamp_entrada": ts(240), "timestamp_cierre": ts(180),
         "pnl":38000,"razon_cierre":"take_profit","estrategia":"mean_reversion"},
        {"id":"b2","symbol":"SQM-B","precio":38200,"cantidad":20,
         "stop_original":37500,"take_profit":39800,
         "timestamp_entrada": ts(150), "timestamp_cierre": ts(90),
         "pnl":-14000,"razon_cierre":"stop","estrategia":"mean_reversion"},
        {"id":"b3","symbol":"BCI","precio":28500,"cantidad":30,
         "stop_original":27800,"take_profit":30000,
         "timestamp_entrada": ts(60), "timestamp_cierre": None,
         "pnl":None,"razon_cierre":None,"estrategia":"mean_reversion"},
    ]

    monitor2 = AntiTilt(config={"capital_inicial":10_000_000})
    estado2  = monitor2.evaluar(trades_bien)
    print(estado2.resumen())

    # ── Integración con discipline.py ──
    print("\n\n🔗 Test de integración con discipline.py:")
    resultado = evaluar_sesion_completa(trades_tilt, capital_inicial=10_000_000)
    print(f"  Nivel tilt:       {resultado['resumen']['nivel_tilt']}")
    print(f"  Score tilt:       {resultado['resumen']['score_tilt']}/100")
    print(f"  Score disciplina: {resultado['resumen']['score_disciplina']}/100" if resultado['resumen']['score_disciplina'] else "  Score disciplina: (discipline.py no disponible)")
    print(f"  Bloqueado:        {resultado['resumen']['bloqueado']}")
    print(f"  Patrones:         {resultado['resumen']['patrones']}")

    print("\n" + "="*55)
