"""
InvertirCL — Score de Disciplina Institucional v1.0
=====================================================
Item 09 del Roadmap — El feature más original del proyecto.

Evalúa en tiempo real si el usuario opera con disciplina institucional.
Un Risk Manager en un banco real haría exactamente esto.

MÉTRICAS QUE EVALÚA:
  1. Respeto al Stop Loss        — ¿movió el stop para evitar pérdidas?
  2. Respeto al Take Profit      — ¿cerró antes del target por impaciencia?
  3. Overtrading                 — ¿opera demasiado seguido?
  4. Revenge Trading             — ¿opera impulsivo después de una pérdida?
  5. Position Sizing             — ¿respeta el máximo por trade?
  6. Trades sin Stop Loss        — ¿entra sin protección?
  7. Consistencia estratégica    — ¿opera fuera de su plan definido?
  8. Horario de operación        — ¿opera en horas de baja liquidez?

SCORE: 0-100
  90-100 → Institucional ⭐ (nivel prop trader)
  70-89  → Disciplinado ✅
  50-69  → Aceptable ⚠️
  30-49  → En riesgo 🟡
  0-29   → Mesa bloqueada 🔴

USO:
  from discipline import DisciplineScore

  score = DisciplineScore(config={
      "capital_inicial": 10_000_000,
      "riesgo_max_pct":  2.0,
      "max_trades_hora": 3,
  })

  # Registrar cada trade
  score.registrar_trade({
      "id": "trade_001",
      "symbol": "COPEC",
      "side": "buy",
      "entrada": 8420,
      "stop_original": 8210,
      "take_profit": 8841,
      "cantidad": 100,
      "timestamp_entrada": "2026-04-04T10:30:00",
  })

  # Evaluar cuando cierra el trade
  score.evaluar_cierre({
      "id": "trade_001",
      "precio_cierre": 8200,       # salió antes del stop (peor)
      "stop_final": 8000,          # movió el stop hacia abajo
      "timestamp_cierre": "2026-04-04T11:15:00",
      "razon_cierre": "stop_movido",
  })

  resultado = score.calcular()
  print(resultado)
"""

import json
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass, field, asdict


# ══════════════════════════════════════════════════════════════
# CONFIGURACIÓN POR DEFECTO
# ══════════════════════════════════════════════════════════════
DEFAULT_CONFIG = {
    "capital_inicial":   10_000_000,  # CLP
    "riesgo_max_pct":    2.0,         # % máximo por trade
    "max_trades_hora":   3,           # overtrading si supera esto
    "min_rr_ratio":      1.5,         # R/R mínimo aceptable
    "ventana_revenge":   30,          # minutos para detectar revenge trading
    "horario_optimo": {               # Bolsa de Santiago
        "apertura": "09:30",
        "cierre":   "17:30",
        "evitar_apertura_min": 15,    # primeros 15 min muy volátiles
        "evitar_cierre_min":   15,    # últimos 15 min muy volátiles
    },
}


# ══════════════════════════════════════════════════════════════
# ESTRUCTURAS DE DATOS
# ══════════════════════════════════════════════════════════════
@dataclass
class Trade:
    id:               str
    symbol:           str
    side:             str             # "buy" | "sell"
    entrada:          float
    stop_original:    float           # stop loss al momento de entrar
    take_profit:      float
    cantidad:         int
    timestamp_entrada: str
    capital_total:    float = 0.0     # monto total de la operación

    # Se llenan al cerrar
    precio_cierre:    Optional[float] = None
    stop_final:       Optional[float] = None  # stop al momento de cerrar (puede haber sido movido)
    timestamp_cierre: Optional[str]  = None
    razon_cierre:     Optional[str]  = None   # "stop", "take_profit", "manual", "stop_movido"
    pnl:              Optional[float] = None

    # Flags de disciplina (calculados)
    movio_stop:       bool = False
    cerro_antes_tp:   bool = False
    sin_stop:         bool = False
    rr_invalido:      bool = False


@dataclass
class Penalizacion:
    categoria:   str
    descripcion: str
    puntos:      float    # negativo = penalización, positivo = bonus
    timestamp:   str
    trade_id:    Optional[str] = None


@dataclass
class ResultadoScore:
    score:           float           # 0-100
    nivel:           str             # Institucional / Disciplinado / etc.
    emoji:           str
    color:           str             # hex color
    bloqueado:       bool            # si debe bloquearse la mesa
    breakdown:       dict            # desglose por categoría
    penalizaciones:  list            # lista de penalizaciones
    bonuses:         list            # lista de bonuses
    trades_analizados: int
    resumen:         str             # texto para mostrar al usuario
    recomendaciones: list            # qué mejorar


# ══════════════════════════════════════════════════════════════
# MOTOR DE DISCIPLINA
# ══════════════════════════════════════════════════════════════
class DisciplineScore:
    """
    Evalúa la disciplina de trading del usuario en tiempo real.
    Mantiene un historial de trades y penalizaciones.
    """

    def __init__(self, config: dict = None):
        self.config       = {**DEFAULT_CONFIG, **(config or {})}
        self.trades:       list[Trade]        = []
        self.penalizaciones: list[Penalizacion] = []
        self._trade_map:   dict[str, Trade]   = {}  # id → Trade

    # ── REGISTRAR APERTURA DE TRADE ──────────────────────────
    def registrar_trade(self, data: dict) -> Trade:
        """
        Registrar un nuevo trade al momento de abrir la posición.
        Evalúa inmediatamente: sin stop, R/R inválido, overtrading,
        revenge trading, horario.
        """
        t = Trade(
            id               = data["id"],
            symbol           = data["symbol"],
            side             = data["side"],
            entrada          = float(data["entrada"]),
            stop_original    = float(data.get("stop_original", 0)),
            take_profit      = float(data.get("take_profit", 0)),
            cantidad         = int(data["cantidad"]),
            timestamp_entrada = data.get("timestamp_entrada", datetime.now().isoformat()),
            capital_total    = float(data.get("entrada", 0)) * int(data.get("cantidad", 0)),
        )

        # ── CHECK 1: Sin stop loss ──
        if t.stop_original == 0:
            t.sin_stop = True
            self._penalizar("stop_loss", "Trade sin stop loss definido", -15, t.id)

        # ── CHECK 2: R/R ratio inválido ──
        if t.stop_original > 0 and t.take_profit > 0:
            riesgo   = abs(t.entrada - t.stop_original)
            ganancia = abs(t.take_profit - t.entrada)
            rr       = ganancia / riesgo if riesgo > 0 else 0
            if rr < self.config["min_rr_ratio"]:
                t.rr_invalido = True
                self._penalizar("rr_ratio", f"R/R ratio bajo ({rr:.1f}:1 < {self.config['min_rr_ratio']}:1)", -8, t.id)

        # ── CHECK 3: Position sizing ──
        riesgo_pct = (abs(t.entrada - t.stop_original) * t.cantidad / self.config["capital_inicial"] * 100) if t.stop_original > 0 else 0
        if riesgo_pct > self.config["riesgo_max_pct"]:
            self._penalizar("sizing", f"Riesgo por trade ({riesgo_pct:.1f}%) supera máximo ({self.config['riesgo_max_pct']}%)", -10, t.id)

        # ── CHECK 4: Overtrading ──
        trades_ultima_hora = self._trades_en_ultima_hora(t.timestamp_entrada)
        if trades_ultima_hora >= self.config["max_trades_hora"]:
            self._penalizar("overtrading", f"Overtrading: {trades_ultima_hora + 1} trades en la última hora", -12, t.id)

        # ── CHECK 5: Revenge trading ──
        if self._es_revenge_trading(t.timestamp_entrada):
            self._penalizar("revenge", "Revenge trading detectado: operando inmediatamente después de una pérdida", -20, t.id)

        # ── CHECK 6: Horario de operación ──
        horario = self._evaluar_horario(t.timestamp_entrada)
        if horario == "fuera_horario":
            self._penalizar("horario", "Operando fuera del horario de la Bolsa de Santiago", -5, t.id)
        elif horario == "apertura_volatil":
            self._penalizar("horario", f"Operando en los primeros {self.config['horario_optimo']['evitar_apertura_min']} min (alta volatilidad)", -3, t.id)
        elif horario == "cierre_volatil":
            self._penalizar("horario", f"Operando en los últimos {self.config['horario_optimo']['evitar_cierre_min']} min (baja liquidez)", -3, t.id)

        self.trades.append(t)
        self._trade_map[t.id] = t
        return t

    # ── EVALUAR CIERRE DE TRADE ───────────────────────────────
    def evaluar_cierre(self, data: dict) -> Trade:
        """
        Evaluar disciplina cuando el usuario cierra una posición.
        Detecta: stop movido, cierre antes del take profit.
        """
        trade_id = data["id"]
        t = self._trade_map.get(trade_id)
        if not t:
            raise ValueError(f"Trade {trade_id} no encontrado")

        t.precio_cierre    = float(data["precio_cierre"])
        t.stop_final       = float(data.get("stop_final", t.stop_original))
        t.timestamp_cierre = data.get("timestamp_cierre", datetime.now().isoformat())
        t.razon_cierre     = data.get("razon_cierre", "manual")

        # P&L
        if t.side == "buy":
            t.pnl = (t.precio_cierre - t.entrada) * t.cantidad
        else:
            t.pnl = (t.entrada - t.precio_cierre) * t.cantidad

        # ── CHECK 7: Stop movido ──
        stop_movido = False
        if t.side == "buy"  and t.stop_final < t.stop_original * 0.999:
            stop_movido = True
        if t.side == "sell" and t.stop_final > t.stop_original * 1.001:
            stop_movido = True

        if stop_movido or t.razon_cierre == "stop_movido":
            t.movio_stop = True
            self._penalizar("stop_movido",
                f"Stop loss movido en {t.symbol}: original ${t.stop_original:,.0f} → final ${t.stop_final:,.0f}",
                -25, t.id)

        # ── CHECK 8: Cerró antes del take profit ──
        if t.razon_cierre == "manual" and t.pnl and t.pnl > 0:
            # Salió en ganancia pero no en el take profit
            ganancia_potencial = abs(t.take_profit - t.entrada) * t.cantidad
            if t.pnl < ganancia_potencial * 0.5:
                t.cerro_antes_tp = True
                self._penalizar("tp_anticipado",
                    f"Cierre anticipado en {t.symbol}: capturó solo el {t.pnl/ganancia_potencial*100:.0f}% del potencial",
                    -8, t.id)

        # ── BONUS: Respetó stop loss ──
        if t.razon_cierre == "stop" and not t.movio_stop:
            self._bonus("disciplina_stop",
                f"Respetó el stop loss en {t.symbol} (${abs(t.pnl):,.0f} pérdida controlada)",
                +5, t.id)

        # ── BONUS: Llegó al take profit ──
        if t.razon_cierre == "take_profit":
            self._bonus("disciplina_tp",
                f"Take profit alcanzado en {t.symbol} (+${t.pnl:,.0f})",
                +8, t.id)

        return t

    # ── CALCULAR SCORE ────────────────────────────────────────
    def calcular(self) -> ResultadoScore:
        """
        Calcular el Score de Disciplina Institucional actual.
        Retorna un ResultadoScore con todo el desglose.
        """
        if not self.trades:
            return ResultadoScore(
                score=100, nivel="Sin trades", emoji="⭐",
                color="#8a9bb0", bloqueado=False,
                breakdown={}, penalizaciones=[], bonuses=[],
                trades_analizados=0,
                resumen="Sin trades registrados. ¡Empieza a operar!",
                recomendaciones=["Define tu estrategia antes de operar", "Siempre usa stop loss"],
            )

        # Calcular puntos base por categoría
        breakdown = {
            "stop_loss":     {"nombre": "Respeto al Stop Loss",      "peso": 25, "puntos": 0},
            "rr_ratio":      {"nombre": "R/R Ratio mínimo",          "peso": 15, "puntos": 0},
            "sizing":        {"nombre": "Position Sizing",           "peso": 15, "puntos": 0},
            "overtrading":   {"nombre": "Control de Overtrading",    "peso": 15, "puntos": 0},
            "revenge":       {"nombre": "Sin Revenge Trading",       "peso": 20, "puntos": 0},
            "horario":       {"nombre": "Horario de Operación",      "peso":  5, "puntos": 0},
            "consistencia":  {"nombre": "Consistencia Estratégica",  "peso":  5, "puntos": 0},
        }

        # Partir de 100 y descontar penalizaciones
        total_penalizaciones = sum(abs(p.puntos) for p in self.penalizaciones if p.puntos < 0)
        total_bonuses        = sum(p.puntos for p in self.penalizaciones if p.puntos > 0)

        # Distribuir penalizaciones por categoría
        for pen in self.penalizaciones:
            cat = pen.categoria
            if cat in breakdown:
                breakdown[cat]["puntos"] += pen.puntos

        # Score base 100 - penalizaciones + bonuses
        # Normalizar según número de trades (penalizaciones se diluyen con experiencia)
        n        = len(self.trades)
        factor   = min(1.0, 1.0 + (n - 1) * 0.02)  # más trades → más contexto
        raw_score = 100 - (total_penalizaciones / max(n, 1)) * factor + (total_bonuses / max(n, 1))
        score     = max(0.0, min(100.0, round(raw_score, 1)))

        # Nivel y color
        if score >= 90:
            nivel, emoji, color = "Institucional",  "⭐", "#00e676"
        elif score >= 70:
            nivel, emoji, color = "Disciplinado",   "✅", "#4a9eff"
        elif score >= 50:
            nivel, emoji, color = "Aceptable",      "⚠️", "#f5c518"
        elif score >= 30:
            nivel, emoji, color = "En riesgo",      "🟡", "#ff6b35"
        else:
            nivel, emoji, color = "Mesa bloqueada", "🔴", "#ff3d57"

        bloqueado = score < 30

        # Recomendaciones personalizadas
        recomendaciones = self._generar_recomendaciones()

        # Resumen
        n_cierres = len([t for t in self.trades if t.pnl is not None])
        wins      = len([t for t in self.trades if t.pnl and t.pnl > 0])
        wr        = round(wins / n_cierres * 100, 1) if n_cierres > 0 else 0

        resumen = (
            f"Score {score}/100 — {nivel} {emoji}. "
            f"{n} trades operados, {n_cierres} cerrados, Win Rate {wr}%. "
            f"{'Mesa de dinero BLOQUEADA por 1 hora.' if bloqueado else 'Puedes seguir operando.'}"
        )

        return ResultadoScore(
            score            = score,
            nivel            = nivel,
            emoji            = emoji,
            color            = color,
            bloqueado        = bloqueado,
            breakdown        = breakdown,
            penalizaciones   = [asdict(p) for p in self.penalizaciones if p.puntos < 0],
            bonuses          = [asdict(p) for p in self.penalizaciones if p.puntos > 0],
            trades_analizados= n,
            resumen          = resumen,
            recomendaciones  = recomendaciones,
        )

    # ── HELPERS PRIVADOS ──────────────────────────────────────
    def _penalizar(self, categoria: str, descripcion: str, puntos: float, trade_id: str = None):
        self.penalizaciones.append(Penalizacion(
            categoria   = categoria,
            descripcion = descripcion,
            puntos      = puntos,
            timestamp   = datetime.now().isoformat(),
            trade_id    = trade_id,
        ))

    def _bonus(self, categoria: str, descripcion: str, puntos: float, trade_id: str = None):
        self._penalizar(categoria, descripcion, puntos, trade_id)

    def _trades_en_ultima_hora(self, timestamp: str) -> int:
        try:
            ts  = datetime.fromisoformat(timestamp)
            h1  = ts - timedelta(hours=1)
            return sum(1 for t in self.trades
                       if datetime.fromisoformat(t.timestamp_entrada) >= h1)
        except Exception:
            return len(self.trades)

    def _es_revenge_trading(self, timestamp: str) -> bool:
        """Detecta si hay una pérdida reciente antes de este trade."""
        try:
            ts      = datetime.fromisoformat(timestamp)
            ventana = timedelta(minutes=self.config["ventana_revenge"])
            recientes_perdidas = [
                t for t in self.trades
                if t.pnl is not None and t.pnl < 0
                and t.timestamp_cierre
                and datetime.fromisoformat(t.timestamp_cierre) >= ts - ventana
                and datetime.fromisoformat(t.timestamp_cierre) <= ts
            ]
            return len(recientes_perdidas) > 0
        except Exception:
            return False

    def _evaluar_horario(self, timestamp: str) -> str:
        """Retorna: 'optimo', 'apertura_volatil', 'cierre_volatil', 'fuera_horario'."""
        try:
            ts   = datetime.fromisoformat(timestamp)
            hora = ts.hour * 60 + ts.minute

            h  = self.config["horario_optimo"]
            ap = int(h["apertura"].split(":")[0]) * 60 + int(h["apertura"].split(":")[1])
            cl = int(h["cierre"].split(":")[0])   * 60 + int(h["cierre"].split(":")[1])

            if hora < ap or hora > cl:
                return "fuera_horario"
            if hora < ap + h["evitar_apertura_min"]:
                return "apertura_volatil"
            if hora > cl - h["evitar_cierre_min"]:
                return "cierre_volatil"
            return "optimo"
        except Exception:
            return "optimo"

    def _generar_recomendaciones(self) -> list:
        recs = []
        cats = {}
        for p in self.penalizaciones:
            if p.puntos < 0:
                cats[p.categoria] = cats.get(p.categoria, 0) + abs(p.puntos)

        if cats.get("stop_movido", 0) > 0:
            recs.append("🔴 Nunca muevas el stop loss. El stop está donde está por una razón — protege tu capital.")
        if cats.get("revenge", 0) > 0:
            recs.append("🧠 Revenge trading detectado. Tómate 1 hora de descanso después de una pérdida.")
        if cats.get("overtrading", 0) > 0:
            recs.append("⏳ Menos es más. Espera setups de alta probabilidad en vez de operar por operar.")
        if cats.get("stop_loss", 0) > 0:
            recs.append("🛡️ Siempre define el stop loss ANTES de entrar — no después.")
        if cats.get("rr_ratio", 0) > 0:
            recs.append("📐 Apunta a R/R ≥ 2:1. Por cada peso en riesgo, debes poder ganar al menos $2.")
        if cats.get("sizing", 0) > 0:
            recs.append("📏 Nunca arriesgues más del 2% por trade. Así puedes sobrevivir 50 pérdidas seguidas.")
        if not recs:
            recs.append("✅ Excelente disciplina. Sigue respetando tu plan — eso es lo que hacen los profesionales.")
        return recs

    # ── PERSISTENCIA ─────────────────────────────────────────
    def exportar_json(self) -> dict:
        """Exportar todo el estado a JSON (para guardar en Supabase)."""
        return {
            "score":          self.calcular().__dict__,
            "trades":         [asdict(t) for t in self.trades],
            "penalizaciones": [asdict(p) for p in self.penalizaciones],
            "config":         self.config,
            "timestamp":      datetime.now().isoformat(),
        }

    def importar_json(self, data: dict):
        """Restaurar estado desde JSON (al cargar desde Supabase)."""
        self.config = data.get("config", self.config)
        for td in data.get("trades", []):
            t = Trade(**td)
            self.trades.append(t)
            self._trade_map[t.id] = t
        for pd in data.get("penalizaciones", []):
            self.penalizaciones.append(Penalizacion(**pd))

    def reset(self):
        """Reiniciar para nueva sesión."""
        self.trades.clear()
        self.penalizaciones.clear()
        self._trade_map.clear()


# ══════════════════════════════════════════════════════════════
# TEST — ejecutar directamente: python discipline.py
# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("\n" + "="*60)
    print("  InvertirCL — Score de Disciplina Institucional v1.0")
    print("="*60)

    score = DisciplineScore(config={
        "capital_inicial": 10_000_000,
        "riesgo_max_pct":  2.0,
        "max_trades_hora": 3,
    })

    print("\n📋 Simulando sesión de trading con errores de disciplina...\n")

    # Trade 1: Bien ejecutado
    print("Trade 1 — COPEC: Entrada con stop y TP correctos")
    score.registrar_trade({
        "id": "t001", "symbol": "COPEC", "side": "buy",
        "entrada": 8420, "stop_original": 8210, "take_profit": 8841,
        "cantidad": 100,
        "timestamp_entrada": "2026-04-04T10:35:00",
    })
    score.evaluar_cierre({
        "id": "t001", "precio_cierre": 8841,
        "stop_final": 8210, "razon_cierre": "take_profit",
        "timestamp_cierre": "2026-04-04T12:10:00",
    })

    # Trade 2: Sin stop loss
    print("Trade 2 — SQM: Sin stop loss definido ❌")
    score.registrar_trade({
        "id": "t002", "symbol": "SQM-B", "side": "buy",
        "entrada": 38200, "stop_original": 0, "take_profit": 40000,
        "cantidad": 20,
        "timestamp_entrada": "2026-04-04T11:00:00",
    })

    # Trade 3: Movió el stop para evitar pérdida
    print("Trade 3 — BCI: Movió el stop hacia abajo ❌")
    score.registrar_trade({
        "id": "t003", "symbol": "BCI", "side": "buy",
        "entrada": 28500, "stop_original": 27800, "take_profit": 30000,
        "cantidad": 30,
        "timestamp_entrada": "2026-04-04T11:30:00",
    })
    score.evaluar_cierre({
        "id": "t003", "precio_cierre": 27200,
        "stop_final": 27000,  # movió el stop de 27800 a 27000
        "razon_cierre": "stop_movido",
        "timestamp_cierre": "2026-04-04T13:45:00",
    })

    # Trade 4: Revenge trading (inmediatamente después de t003)
    print("Trade 4 — ENTEL: Revenge trading justo después de pérdida ❌")
    score.registrar_trade({
        "id": "t004", "symbol": "ENTEL", "side": "buy",
        "entrada": 4210, "stop_original": 4100, "take_profit": 4400,
        "cantidad": 500,
        "timestamp_entrada": "2026-04-04T13:50:00",
    })

    # Calcular score final
    print("\n" + "="*60)
    resultado = score.calcular()
    print(f"\n  SCORE FINAL: {resultado.score}/100 — {resultado.nivel} {resultado.emoji}")
    print(f"  {resultado.resumen}")
    print(f"\n  {'🔴 MESA BLOQUEADA' if resultado.bloqueado else '✅ Puede seguir operando'}")

    print("\n📊 Penalizaciones:")
    for p in resultado.penalizaciones:
        print(f"  {p['puntos']:+.0f} pts — {p['descripcion']}")

    print("\n🎯 Bonuses:")
    for b in resultado.bonuses:
        print(f"  {b['puntos']:+.0f} pts — {b['descripcion']}")

    print("\n💡 Recomendaciones:")
    for r in resultado.recomendaciones:
        print(f"  {r}")

    # Exportar a JSON
    print("\n📁 Estado exportable a Supabase:")
    data = score.exportar_json()
    print(f"  Trades: {len(data['trades'])} | Penalizaciones: {len(data['penalizaciones'])}")
    print(f"  Score: {data['score']['score']}/100")

    print("\n" + "="*60)
