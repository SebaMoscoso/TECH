# BROTA 🇨🇱

> Plataforma educativa de inversiones para el mercado chileno. Simula trading real con datos en vivo del IPSA, USD/CLP y acciones locales — sin arriesgar plata real.

**Desarrollado por Joaquín Ocare y Sebastián Moscoso — Magíster en Finanzas, Universidad Adolfo Ibáñez.**

---

## ¿Qué es InvertirCL?

InvertirCL es una herramienta educativa donde personas con conocimientos básicos de inversión pueden aprender a operar en el mercado chileno **practicando con dinero ficticio y datos reales**. No es solo un gráfico — es una experiencia guiada que enseña disciplina institucional.

**Público objetivo:** chilenos que quieren dar el paso de Fintual/eToro a entender realmente cómo funciona el mercado.

**Modelo de negocio:** Freemium — plan gratis + plan Pro a $9.990 CLP/mes.

---

## Diferenciadores

| Feature | InvertirCL | Competencia |
|---|---|---|
| Score de Disciplina Institucional (0-100) | ✅ | ❌ |
| Anti-Tilt — bloqueo de mesa por tilt emocional | ✅ | ❌ |
| Black Swan Simulator (Crisis 2008, COVID, Estallido) | ✅ | ❌ |
| Contexto 100% chileno (IPSA, UF, USD/CLP, COPEC) | ✅ | Parcial |
| Matching Engine con slippage y comisiones reales | ✅ | Raro |
| Replay de días históricos tick a tick | ✅ | Parcial |
| Bot Copiloto que opera en paralelo al usuario | ✅ | ❌ |

---

## Stack tecnológico

### Frontend
- **HTML/CSS/JS puro** — sin frameworks, abre directo en Chrome
- **Tipografías:** Syne (display), DM Sans (cuerpo), DM Mono (números)
- **Dark theme** con paleta fija: `#00e676` verde · `#ff3d57` rojo · `#4a9eff` azul · `#07090d` fondo

### Datos de mercado (todas gratuitas)
| Fuente | Datos |
|---|---|
| [DolarApi.com](https://cl.dolarapi.com) | USD/CLP en tiempo real |
| [mindicador.cl](https://mindicador.cl/api) | UF, IPC, TPM, Cobre, Euro |
| [Yahoo Finance](https://finance.yahoo.com) | IPSA (^IPSA), acciones .SN — 15min delay |
| allorigins.win | Proxy CORS para Yahoo Finance |

### Módulos JS (APP/JS/)
```
market.js     → APIs de mercado, caché TTL, fallbacks, indicadores técnicos,
                MatchingEngine, Performance metrics
poller.js     → Polling Yahoo Finance cada 15s + interpolación tick-a-tick cada 2s
```

### Módulos Python (PY/)
```
market.py         → Equivalente Python de market.js (para scripts y análisis)
discipline.py     → Score de Disciplina Institucional (0-100)
antitilt.py       → Detección de tilt comportamental + bloqueo de mesa
blackswan.py      → Simulador de crisis históricas con datos reales
replay.py         → Reproducción tick a tick de días históricos
shadowtrader.py   → Bot copiloto con disciplina perfecta
backtest_engine.py→ Motor de backtest con datos históricos reales
montecarlo.py     → Simulador de 1.000 variaciones de estrategia
walkforward.py    → Walk-forward testing In-Sample / Out-of-Sample
hrp.py            → Hierarchical Risk Parity (López de Prado 2016)
quantcopilot.py   → Quant-Copilot IA: lenguaje natural → estrategia
```

---

## Estructura del proyecto

```
TECH/
├── README.md
├── ROADMAP.html              ← Roadmap interactivo con división de trabajo
├── APP/
│   ├── dashboard.html        ← Dashboard principal estilo Bloomberg
│   ├── simulator.html        ← Simulador de trading con velas en tiempo real
│   ├── backtest.html         ← Backtest de estrategias con editor visual
│   ├── risk.html             ← Gestión de riesgo y calculadora de position sizing
│   ├── learn.html            ← Módulos educativos con quiz interactivo
│   ├── test_apis.html        ← Suite de 17 tests para verificar APIs
│   └── JS/
│       ├── market.js         ← Módulo central de datos de mercado
│       └── poller.js         ← Polling en tiempo real de acciones chilenas
└── PY/
    ├── market.py
    ├── discipline.py
    ├── antitilt.py
    ├── blackswan.py
    ├── replay.py
    ├── shadowtrader.py
    ├── backtest_engine.py
    ├── montecarlo.py
    ├── walkforward.py
    ├── hrp.py
    └── quantcopilot.py
```

---

## Cómo correr el proyecto localmente

No se necesita servidor ni instalación. El frontend corre directamente en el navegador.

**1. Clonar el repositorio**
```bash
git clone https://github.com/SebaMoscoso/TECNOLOGIA.git
cd TECNOLOGIA
```

**2. Abrir en Chrome**
```
Doble click en TECH/APP/dashboard.html
```

**3. Verificar que las APIs funcionan**
```
Abrir TECH/APP/test_apis.html → "Correr todos los tests"
Deben pasar los 17 tests.
```

**Para los módulos Python** (requiere Python 3.9+):
```bash
pip install requests yfinance pandas numpy
# Opcional para HRP:
pip install riskfolio-lib

# Ejemplo de uso:
cd TECH/PY
python market.py
python antitilt.py
```

---

## Indicadores técnicos disponibles

Todos implementados en `market.js` como `Market.Indicators.*` y en `market.py` como `Indicators.*`. Sin librerías externas, precisos al cuarto decimal.

| Indicador | Función JS | Función Python |
|---|---|---|
| RSI(n) | `Market.Indicators.RSI(closes, 14)` | `Indicators.rsi(closes, 14)` |
| EMA(n) | `Market.Indicators.EMA(closes, 9)` | `Indicators.ema(closes, 9)` |
| SMA(n) | `Market.Indicators.SMA(closes, 50)` | `Indicators.sma(closes, 50)` |
| MACD | `Market.Indicators.MACD(closes)` | `Indicators.macd(closes)` |
| Bollinger Bands | `Market.Indicators.BB(closes, 20, 2)` | `Indicators.bb(closes, 20, 2)` |
| ATR(n) | `Market.Indicators.ATR(candles, 14)` | `Indicators.atr(candles, 14)` |
| VWAP | `Market.Indicators.VWAP(candles)` | `Indicators.vwap(candles)` |
| Sharpe Ratio | `Market.Indicators.Sharpe(returns)` | `Indicators.sharpe(returns)` |
| Sortino Ratio | `Market.Indicators.Sortino(returns)` | `Indicators.sortino(returns)` |
| Max Drawdown | `Market.Indicators.MaxDrawdown(equity)` | `Indicators.max_drawdown(equity)` |
| Profit Factor | `Market.Indicators.ProfitFactor(trades)` | `Indicators.profit_factor(trades)` |

---

## Uso de market.js

```html
<script src="JS/market.js"></script>
<script>
  // Obtener todos los indicadores de una vez
  const data = await Market.getAll();
  console.log(data.usdclp.promedio);  // ej: 928.50
  console.log(data.ipsa.price);       // ej: 7284.0
  console.log(data.uf.valor);         // ej: 38741.0

  // Acción chilena
  const copec = await Market.getAccion('COPEC');
  console.log(copec.price);           // ej: 8420.0

  // Histórico
  const hist = await Market.getHistorico('^IPSA', '3mo', '1d');
  const closes = hist.map(c => c.close);
  console.log(Market.Indicators.RSI(closes, 14)); // ej: 58.3421

  // Costos de una operación
  const costos = Market.MatchingEngine.costos(8420, 100, 'buy');
  // { precioEjecucion: 8424.26, comision: 8424, slippage: 426, costoTotal: 8850 }
</script>
```

---

## APIs de mercado

### USD/CLP — DolarApi.com
```javascript
const usd = await Market.getUSDCLP();
// { compra: 926.0, venta: 930.0, promedio: 928.0, fuente: 'DolarApi.com', live: true }
```

### Indicadores Chile — mindicador.cl
```javascript
const uf    = await Market.getUF();    // { valor: 38741.0, unidad: 'CLP' }
const cobre = await Market.getCobre(); // { valor: 4.82, unidad: 'USD/lb' }
const tpm   = await Market.getTPM();   // { valor: 5.0, unidad: '%' }
```

### IPSA y acciones — Yahoo Finance
```javascript
const ipsa = await Market.getIPSA();
// { price: 7284.0, changePct: -0.41, fuente: 'Yahoo Finance (15min delay)' }

const sqm = await Market.getAccion('SQM-B');
// { symbol: 'SQM-B.SN', price: 38200.0, changePct: -2.3, volume: 315000 }
```

---

## Convenciones de código

- Todo el texto en **español chileno**
- Precios en CLP con `.toLocaleString('es-CL')`
- Nemotécnicos chilenos: `COPEC.SN`, `SQM-B.SN`, `BCI.SN`, `ENTEL.SN`
- Commits en español: `"feat: integrar discipline.py al simulador"` · `"fix: sidebar dashboard"`
- El HTML debe abrir sin servidor — no usar rutas absolutas

---

## Estado del proyecto

Ver **ROADMAP.html** en la raíz del repositorio para el estado actualizado de cada feature, quién está trabajando en qué y qué está completado.

---

## Aviso Legal

InvertirCL es una plataforma de **educación financiera y simulación**. No constituye asesoría de inversión ni está regulada por la CMF. Los resultados de simulación no garantizan rendimientos futuros. El usuario es el único responsable de sus decisiones de inversión con dinero real.

---

*Magíster en Finanzas — Universidad Adolfo Ibáñez · Santiago, Chile · 2026*
