"""
InvertirCL — Quant-Copilot con IA v1.0
=========================================
Item 18 del Roadmap.

Traduce lenguaje natural a estrategias de trading y las optimiza
automáticamente usando Claude API.

FUNCIONALIDADES:
  1. Traductor lenguaje natural → estrategia
     "Quiero comprar cuando el RSI esté bajo 30 y el precio
      esté sobre la EMA de 21 días" → genera la estrategia
      y la corre en backtest automáticamente.

  2. Optimizador automático de parámetros
     Analiza el resultado del backtest y sugiere ajustes:
     "Si ajustas tu stop_mult de 1.5 a 1.8, tu Sharpe
      subiría de 1.2 a 1.8 y el max drawdown bajaría."

  3. Diagnóstico de estrategia
     Dado un resultado de backtest, explica en lenguaje simple
     qué está fallando y cómo mejorarlo.

  4. Generador de ideas de estrategia
     Basado en el perfil del usuario (risk tolerance, estilo)
     sugiere estrategias a explorar.

USO:
  from quantcopilot import QuantCopilot
  from market import Market

  copilot = QuantCopilot()

  # Traducir idea a estrategia
  resultado = copilot.idea_a_estrategia(
      idea = "Quiero comprar COPEC cuando el RSI caiga bajo 30
              y haya un cruce alcista de la EMA de 9 sobre la de 21",
      symbol = "COPEC.SN",
      period = "1y"
  )
  print(resultado)

  # Optimizar parámetros existentes
  copilot.optimizar_estrategia(
      resultado_backtest = bt_resultado,
      estrategia = "rsi_mean_reversion"
  )
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

try:
    import requests
    REQUESTS_OK = True
except ImportError:
    REQUESTS_OK = False
    print("[QuantCopilot] Instala: pip install requests")


# ══════════════════════════════════════════════════════════════
# CONFIGURACIÓN DE LA API
# ══════════════════════════════════════════════════════════════
CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL   = "claude-opus-4-5"
MAX_TOKENS     = 2048

# ── Prompts del sistema ────────────────────────────────────────
SYSTEM_PROMPT_TRADUCTOR = """Eres el Quant-Copilot de InvertirCL, una plataforma educativa
de trading para el mercado chileno. Tu rol es ayudar a usuarios a convertir
ideas de trading en estrategias concretas y analizables.

CONTEXTO TÉCNICO:
Las estrategias en InvertirCL se definen con estos parámetros:
- rsi_mean_reversion: {"rsi_n": int, "os": int, "ob": int, "stop_mult": float}
- ema_crossover:      {"fast": int, "slow": int, "stop_mult": float}
- bollinger_reversion:{"n": int, "std": float}
- macd_signal:        {"fast": int, "slow": int, "signal": int, "stop_mult": float}

REGLAS:
1. Siempre responde en español chileno
2. Sé directo y pedagógico
3. Explica el razonamiento detrás de cada parámetro
4. Advierte sobre riesgos cuando corresponda
5. Nunca garantices resultados

Cuando el usuario describa una idea, responde con JSON válido en este formato:
{
  "estrategia": "nombre_estrategia",
  "params": {...},
  "explicacion": "Por qué estos parámetros",
  "advertencias": ["lista de advertencias"],
  "alternativas": ["otras estrategias a considerar"]
}"""

SYSTEM_PROMPT_OPTIMIZADOR = """Eres el Quant-Copilot de InvertirCL, especialista en
optimización de estrategias de trading algorítmico para el mercado chileno.

Analiza resultados de backtest y sugiere mejoras concretas y cuantificadas.

REGLAS:
1. Basa tus sugerencias en los datos del backtest — no inventes números
2. Explica el impacto esperado de cada cambio
3. Prioriza reducir el drawdown sobre aumentar el retorno
4. Siempre menciona el riesgo de overfitting al ajustar parámetros
5. Responde en español chileno

Formato de respuesta: JSON válido con:
{
  "diagnostico": "qué está fallando y por qué",
  "sugerencias": [
    {
      "parametro": "nombre",
      "valor_actual": x,
      "valor_sugerido": y,
      "impacto_esperado": "descripción del impacto",
      "razon": "por qué este cambio"
    }
  ],
  "resumen": "resumen ejecutivo en 2 líneas",
  "advertencia_overfitting": "advertencia sobre overfitting"
}"""

SYSTEM_PROMPT_DIAGNOSTICO = """Eres el Quant-Copilot de InvertirCL, tu rol es explicar
resultados de backtest en lenguaje simple para traders que están aprendiendo.

Sé honesto pero constructivo. Si la estrategia es mala, dilo claramente.
Si tiene potencial, explica qué mejorar.

Responde siempre en español chileno, de forma directa y sin tecnicismos innecesarios.
Máximo 3 párrafos."""


# ══════════════════════════════════════════════════════════════
# ESTRUCTURAS
# ══════════════════════════════════════════════════════════════
@dataclass
class RespuestaCopilot:
    tipo:          str          # "traduccion" | "optimizacion" | "diagnostico"
    exito:         bool
    contenido:     dict         # respuesta parseada
    texto_bruto:   str          # respuesta original del modelo
    tokens_usados: int
    timestamp:     str

    def resumen(self):
        lineas = [
            f"\n{'='*60}",
            f"  🤖 QUANT-COPILOT — {self.tipo.upper()}",
            f"{'='*60}",
        ]

        if not self.exito:
            lineas.append(f"  ❌ Error: {self.contenido.get('error', 'Error desconocido')}")
            lineas.append(f"{'='*60}")
            return "\n".join(lineas)

        if self.tipo == "traduccion":
            c = self.contenido
            lineas += [
                f"  Estrategia:  {c.get('estrategia', 'N/A')}",
                f"  Parámetros:  {json.dumps(c.get('params', {}), ensure_ascii=False)}",
                f"",
                f"  📖 Explicación:",
                f"  {c.get('explicacion', '')}",
            ]
            if c.get("advertencias"):
                lineas.append(f"\n  ⚠️  Advertencias:")
                for adv in c.get("advertencias", []):
                    lineas.append(f"  • {adv}")
            if c.get("alternativas"):
                lineas.append(f"\n  💡 Alternativas a explorar:")
                for alt in c.get("alternativas", []):
                    lineas.append(f"  • {alt}")

        elif self.tipo == "optimizacion":
            c = self.contenido
            lineas += [
                f"  📊 Diagnóstico:",
                f"  {c.get('diagnostico', '')}",
                f"",
                f"  🔧 Sugerencias de parámetros:",
            ]
            for sug in c.get("sugerencias", []):
                lineas.append(
                    f"  • {sug.get('parametro')}: "
                    f"{sug.get('valor_actual')} → {sug.get('valor_sugerido')} "
                    f"({sug.get('impacto_esperado', '')})"
                )
            if c.get("resumen"):
                lineas += [f"\n  📋 Resumen:", f"  {c.get('resumen')}"]
            if c.get("advertencia_overfitting"):
                lineas += [f"\n  ⚠️  Overfitting:", f"  {c.get('advertencia_overfitting')}"]

        elif self.tipo == "diagnostico":
            lineas.append(f"  {self.texto_bruto}")

        lineas += [
            f"",
            f"  Tokens usados: {self.tokens_usados}",
            f"{'='*60}",
        ]
        return "\n".join(lineas)


# ══════════════════════════════════════════════════════════════
# MOTOR DEL COPILOT
# ══════════════════════════════════════════════════════════════
class QuantCopilot:
    """
    Copiloto de trading basado en Claude API.
    Traduce ideas a estrategias, optimiza parámetros y diagnostica backtests.
    """

    def __init__(self, api_key: str = None):
        """
        api_key: clave de la API de Anthropic.
                 Si no se pasa, intenta leerla de la variable de entorno
                 ANTHROPIC_API_KEY o usa modo simulado.
        """
        import os
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.modo_simulado = not self.api_key

        if self.modo_simulado:
            print("[QuantCopilot] ⚠️  Sin API key — ejecutando en modo simulado")
            print("               Para usar la API real: export ANTHROPIC_API_KEY='tu-clave'")
        else:
            print(f"[QuantCopilot] ✅ Claude API configurada ({CLAUDE_MODEL})")

    # ── LLAMADA A LA API ──────────────────────────────────────
    def _llamar_api(self, system: str, user: str) -> tuple:
        """
        Llama a Claude API y retorna (texto, tokens).
        Si está en modo simulado, retorna respuesta de ejemplo.
        """
        if self.modo_simulado:
            return self._respuesta_simulada(user), 0

        if not REQUESTS_OK:
            return '{"error": "requests no instalado"}', 0

        try:
            headers = {
                "x-api-key":         self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
            }
            body = {
                "model":      CLAUDE_MODEL,
                "max_tokens": MAX_TOKENS,
                "system":     system,
                "messages":   [{"role": "user", "content": user}],
            }
            r = requests.post(CLAUDE_API_URL, headers=headers,
                              json=body, timeout=30)
            r.raise_for_status()
            data   = r.json()
            texto  = data["content"][0]["text"]
            tokens = data.get("usage", {}).get("output_tokens", 0)
            return texto, tokens

        except Exception as e:
            return f'{{"error": "{str(e)}"}}', 0

    def _respuesta_simulada(self, user: str) -> str:
        """Respuestas simuladas para cuando no hay API key."""
        user_lower = user.lower()

        if "rsi" in user_lower and "ema" in user_lower:
            return json.dumps({
                "estrategia":  "rsi_mean_reversion",
                "params":      {"rsi_n": 14, "os": 30, "ob": 70, "stop_mult": 1.5},
                "explicacion": "Combinas RSI sobrevendido con EMA como filtro de tendencia. RSI(14) < 30 indica sobreventa, y exigir que el precio esté sobre la EMA(21) filtra señales en tendencia bajista. Stop de 1.5 ATR es conservador y adecuado para el IPSA.",
                "advertencias": [
                    "Esta combinación puede tener pocas señales en mercados con tendencia fuerte",
                    "El RSI(14) es estándar pero puedes probar RSI(10) para más señales",
                ],
                "alternativas": [
                    "ema_crossover: más señales pero mayor drawdown",
                    "bollinger_reversion: similar pero usa bandas de volatilidad",
                ],
            }, ensure_ascii=False)

        elif "optimiz" in user_lower or "mejorar" in user_lower:
            return json.dumps({
                "diagnostico": "El Win Rate bajo indica que las entradas son prematuras. El stop_mult de 1.5 puede estar demasiado ajustado, causando que stops válidos se toquen por ruido normal del mercado.",
                "sugerencias": [
                    {"parametro": "stop_mult", "valor_actual": 1.5, "valor_sugerido": 2.0,
                     "impacto_esperado": "Reduce stops prematuros, aumenta Win Rate estimado en 5-8%",
                     "razon": "El ATR del IPSA justifica un stop más amplio"},
                    {"parametro": "os", "valor_actual": 30, "valor_sugerido": 25,
                     "impacto_esperado": "Señales de mayor calidad pero menos frecuentes",
                     "razon": "RSI < 25 indica sobreventa más extrema y rebote más probable"},
                ],
                "resumen": "Ampliar el stop y exigir sobreventa más extrema debería mejorar la calidad de las entradas. Espera un Win Rate de 55-60% con estos ajustes.",
                "advertencia_overfitting": "Estos ajustes fueron optimizados sobre datos históricos. Siempre valida con Walk-Forward antes de operar en vivo.",
            }, ensure_ascii=False)

        else:
            return json.dumps({
                "estrategia":  "rsi_mean_reversion",
                "params":      {"rsi_n": 14, "os": 30, "ob": 70, "stop_mult": 1.5},
                "explicacion": "Estrategia base de reversión a la media usando RSI. Apropiada para mercados sin tendencia fuerte como el IPSA intradía.",
                "advertencias": ["Valida siempre con Walk-Forward antes de operar en vivo"],
                "alternativas": ["ema_crossover para mercados con tendencia"],
            }, ensure_ascii=False)

    def _parsear_json(self, texto: str) -> dict:
        """Extrae y parsea JSON de la respuesta del modelo."""
        try:
            # Intentar parsear directamente
            return json.loads(texto)
        except json.JSONDecodeError:
            pass

        # Buscar bloque JSON en el texto
        patrones = [
            r'```json\s*([\s\S]*?)\s*```',
            r'```\s*([\s\S]*?)\s*```',
            r'\{[\s\S]*\}',
        ]
        for patron in patrones:
            match = re.search(patron, texto)
            if match:
                try:
                    return json.loads(match.group(1) if '```' in patron else match.group())
                except json.JSONDecodeError:
                    continue

        return {"error": "No se pudo parsear la respuesta", "texto": texto}

    # ── 1. TRADUCIR IDEA A ESTRATEGIA ─────────────────────────
    def idea_a_estrategia(self, idea: str, symbol: str = "^IPSA",
                           period: str = "1y",
                           correr_backtest: bool = True) -> RespuestaCopilot:
        """
        Convierte una idea en lenguaje natural a una estrategia de trading
        y opcionalmente la corre en backtest automáticamente.

        idea:            descripción de la estrategia en español
        symbol:          activo para el backtest
        period:          período de datos
        correr_backtest: si True, corre el backtest automáticamente
        """
        print(f"[QuantCopilot] Traduciendo idea: '{idea[:60]}...'")

        prompt_usuario = f"""
El usuario quiere implementar esta estrategia de trading para el mercado chileno:

"{idea}"

Activo objetivo: {symbol}
Período de datos disponible: {period}

Tradúcela a los parámetros del sistema InvertirCL.
Responde SOLO con JSON válido, sin texto adicional.
"""
        texto, tokens = self._llamar_api(SYSTEM_PROMPT_TRADUCTOR, prompt_usuario)
        contenido     = self._parsear_json(texto)
        exito         = "error" not in contenido

        resultado = RespuestaCopilot(
            tipo          = "traduccion",
            exito         = exito,
            contenido     = contenido,
            texto_bruto   = texto,
            tokens_usados = tokens,
            timestamp     = datetime.now().isoformat(),
        )

        # Correr backtest automáticamente si se solicitó
        if exito and correr_backtest and contenido.get("estrategia"):
            resultado = self._correr_backtest_auto(resultado, symbol, period)

        return resultado

    def _correr_backtest_auto(self, resultado: RespuestaCopilot,
                               symbol: str, period: str) -> RespuestaCopilot:
        """Corre backtest automáticamente con la estrategia traducida."""
        try:
            from backtest_engine import BacktestEngine
            from market import Market

            print(f"[QuantCopilot] Corriendo backtest automático: {symbol} {period}...")
            df = Market.get_historico(symbol, period=period)

            if df.empty:
                print("[QuantCopilot] Sin datos para backtest automático")
                return resultado

            engine = BacktestEngine(capital_inicial=10_000_000, riesgo_pct=2.0)
            bt = engine.run(
                df,
                estrategia = resultado.contenido.get("estrategia", "rsi_mean_reversion"),
                symbol     = symbol,
                params     = resultado.contenido.get("params"),
            )

            resultado.contenido["backtest_auto"] = {
                "retorno_total":  bt.retorno_total,
                "sharpe":         bt.sharpe,
                "win_rate":       bt.win_rate,
                "max_drawdown":   bt.max_drawdown,
                "profit_factor":  bt.profit_factor,
                "n_trades":       bt.n_trades,
                "veredicto":      bt.veredicto,
            }
            print(f"[QuantCopilot] Backtest completado: {bt.veredicto}")

        except Exception as e:
            resultado.contenido["backtest_auto"] = {"error": str(e)}

        return resultado

    # ── 2. OPTIMIZAR PARÁMETROS ───────────────────────────────
    def optimizar_estrategia(self, resultado_backtest,
                              estrategia: str = None) -> RespuestaCopilot:
        """
        Dado un resultado de backtest, sugiere ajustes de parámetros.

        resultado_backtest: ResultadoBacktest de backtest_engine.py
        estrategia:         nombre de la estrategia (opcional)
        """
        # Construir resumen del backtest para el modelo
        try:
            bt = resultado_backtest
            resumen_bt = {
                "estrategia":    estrategia or "desconocida",
                "retorno_total": bt.retorno_total if hasattr(bt, "retorno_total") else bt.get("retorno_total", 0),
                "sharpe":        bt.sharpe        if hasattr(bt, "sharpe")        else bt.get("sharpe", 0),
                "win_rate":      bt.win_rate      if hasattr(bt, "win_rate")      else bt.get("win_rate", 0),
                "max_drawdown":  bt.max_drawdown  if hasattr(bt, "max_drawdown")  else bt.get("max_drawdown", 0),
                "profit_factor": bt.profit_factor if hasattr(bt, "profit_factor") else bt.get("profit_factor", 0),
                "n_trades":      bt.n_trades      if hasattr(bt, "n_trades")      else bt.get("n_trades", 0),
                "avg_rr":        bt.avg_rr        if hasattr(bt, "avg_rr")        else bt.get("avg_rr", 0),
            }
        except Exception as e:
            resumen_bt = {"error": str(e)}

        print(f"[QuantCopilot] Optimizando estrategia: {estrategia}...")

        prompt_usuario = f"""
Analiza este resultado de backtest de la estrategia "{estrategia}" en el mercado chileno:

{json.dumps(resumen_bt, indent=2, ensure_ascii=False)}

Sugiere mejoras concretas de parámetros para mejorar el Sharpe Ratio
sin aumentar el drawdown máximo.
Responde SOLO con JSON válido, sin texto adicional.
"""
        texto, tokens = self._llamar_api(SYSTEM_PROMPT_OPTIMIZADOR, prompt_usuario)
        contenido     = self._parsear_json(texto)
        contenido["backtest_original"] = resumen_bt

        return RespuestaCopilot(
            tipo          = "optimizacion",
            exito         = "error" not in contenido,
            contenido     = contenido,
            texto_bruto   = texto,
            tokens_usados = tokens,
            timestamp     = datetime.now().isoformat(),
        )

    # ── 3. DIAGNOSTICAR BACKTEST ──────────────────────────────
    def diagnosticar(self, resultado_backtest) -> RespuestaCopilot:
        """
        Explica en lenguaje simple qué está pasando en un backtest.
        """
        try:
            bt = resultado_backtest
            resumen = (
                f"Estrategia: {bt.estrategia if hasattr(bt,'estrategia') else 'N/A'}\n"
                f"Retorno total: {bt.retorno_total if hasattr(bt,'retorno_total') else 'N/A'}%\n"
                f"Sharpe: {bt.sharpe if hasattr(bt,'sharpe') else 'N/A'}\n"
                f"Win Rate: {bt.win_rate if hasattr(bt,'win_rate') else 'N/A'}%\n"
                f"Max Drawdown: {bt.max_drawdown if hasattr(bt,'max_drawdown') else 'N/A'}%\n"
                f"Profit Factor: {bt.profit_factor if hasattr(bt,'profit_factor') else 'N/A'}\n"
                f"Trades: {bt.n_trades if hasattr(bt,'n_trades') else 'N/A'}"
            )
        except Exception:
            resumen = str(resultado_backtest)

        print("[QuantCopilot] Diagnosticando backtest...")

        prompt = f"""
Explica en lenguaje simple (para alguien que está aprendiendo) qué está
pasando con este backtest del mercado chileno:

{resumen}

¿Es buena la estrategia? ¿Qué está fallando? ¿Qué debería mejorar?
Sé directo y honesto. Máximo 3 párrafos breves.
"""
        texto, tokens = self._llamar_api(SYSTEM_PROMPT_DIAGNOSTICO, prompt)

        return RespuestaCopilot(
            tipo          = "diagnostico",
            exito         = True,
            contenido     = {"diagnostico": texto},
            texto_bruto   = texto,
            tokens_usados = tokens,
            timestamp     = datetime.now().isoformat(),
        )

    # ── 4. GENERAR IDEAS ──────────────────────────────────────
    def generar_ideas(self, perfil: dict = None) -> RespuestaCopilot:
        """
        Sugiere estrategias basadas en el perfil del usuario.

        perfil: dict con claves opcionales:
          - estilo:      "conservador" | "moderado" | "agresivo"
          - mercado:     "tendencia" | "lateral" | "volátil"
          - horizonte:   "intradía" | "swing" | "posición"
          - experiencia: "principiante" | "intermedio" | "avanzado"
        """
        p = perfil or {}
        print("[QuantCopilot] Generando ideas de estrategia...")

        system = """Eres el Quant-Copilot de InvertirCL. Sugiere estrategias de trading
para el mercado chileno (IPSA, acciones .SN) basadas en el perfil del usuario.
Responde en JSON con formato:
{
  "ideas": [
    {
      "nombre": "nombre de la estrategia",
      "descripcion": "descripción en 1 línea",
      "estrategia_sistema": "nombre en el sistema",
      "params_sugeridos": {...},
      "adecuada_para": "descripción del perfil ideal",
      "riesgo": "bajo|medio|alto"
    }
  ],
  "recomendacion_principal": "la más adecuada para este perfil y por qué"
}"""

        prompt = f"""
Perfil del usuario:
- Estilo de riesgo:  {p.get('estilo', 'moderado')}
- Tipo de mercado:   {p.get('mercado', 'mixto')}
- Horizonte:         {p.get('horizonte', 'swing (días a semanas)')}
- Nivel:             {p.get('experiencia', 'intermedio')}

Sugiere 3 estrategias adecuadas para el mercado chileno (IPSA, COPEC, SQM, BCI).
Responde SOLO con JSON válido.
"""
        texto, tokens = self._llamar_api(system, prompt)
        contenido     = self._parsear_json(texto)

        return RespuestaCopilot(
            tipo          = "ideas",
            exito         = "error" not in contenido,
            contenido     = contenido,
            texto_bruto   = texto,
            tokens_usados = tokens,
            timestamp     = datetime.now().isoformat(),
        )


# ══════════════════════════════════════════════════════════════
# TEST — python quantcopilot.py
# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("\n" + "="*60)
    print("  InvertirCL — Quant-Copilot con IA v1.0")
    print("="*60)

    copilot = QuantCopilot()  # sin api_key → modo simulado

    # ── Test 1: Traducir idea ──
    print("\n🤖 Test 1: Traducir idea a estrategia")
    r1 = copilot.idea_a_estrategia(
        idea = """Quiero comprar cuando el RSI caiga bajo 30 y además
                  el precio esté sobre la EMA de 21 días.
                  El stop loss lo quiero basado en el ATR.""",
        symbol          = "^IPSA",
        period          = "1y",
        correr_backtest = False,  # False en test para no necesitar conexión
    )
    print(r1.resumen())

    # ── Test 2: Optimizar parámetros ──
    print("\n🤖 Test 2: Optimizar parámetros de estrategia")

    # Simular un resultado de backtest
    class BTSimulado:
        estrategia    = "rsi_mean_reversion"
        retorno_total = 8.4
        sharpe        = 0.72
        win_rate      = 48.5
        max_drawdown  = 18.3
        profit_factor = 1.21
        n_trades      = 24
        avg_rr        = 1.6

    r2 = copilot.optimizar_estrategia(BTSimulado(), "rsi_mean_reversion")
    print(r2.resumen())

    # ── Test 3: Diagnóstico ──
    print("\n🤖 Test 3: Diagnóstico de backtest")
    r3 = copilot.diagnosticar(BTSimulado())
    print(r3.resumen())

    # ── Test 4: Generar ideas ──
    print("\n🤖 Test 4: Generar ideas de estrategia")
    r4 = copilot.generar_ideas(perfil={
        "estilo":      "moderado",
        "mercado":     "mixto",
        "horizonte":   "swing",
        "experiencia": "intermedio",
    })
    print(r4.resumen())

    # ── Instrucciones para usar con API real ──
    print("\n" + "="*60)
    print("  Para usar con Claude API real:")
    print("  export ANTHROPIC_API_KEY='sk-ant-...'")
    print("  python quantcopilot.py")
    print()
    print("  O en código:")
    print("  copilot = QuantCopilot(api_key='sk-ant-...')")
    print("="*60)
