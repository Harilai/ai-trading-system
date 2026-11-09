# AI Trading System

Plataforma modular de trading algorítmico autónomo, construida por fases con prioridad en la validación, la trazabilidad y la protección del capital.

## Estado real de implementación

La rama integrada contiene actualmente:

- Backend FastAPI y cartera simulada persistida en SQLite.
- Datos públicos de criptomonedas mediante CCXT (Kraken por defecto).
- Indicadores EMA, SMA, RSI, MACD, Bollinger Bands y ATR.
- Simulación de compras y ventas con comisiones y slippage configurables.
- Rechazo de pares no admitidos y datos de mercado obsoletos.
- Worker asíncrono persistente con cola durable, leases, reintentos y recuperación de tareas expiradas.
- API de supervisión autenticada: estado, pausa, reanudación, parada de emergencia y encolado de tareas.
- Scout que descubre mercados spot cotizados en EUR, filtra por liquidez, spread y frescura de ticker.
- Charts Agent determinista.
- Strategy Research Agent con tres estrategias candidatas long-only (EMA 12/26, RSI(14) y Bollinger(20,2)), backtesting cronológico 70/30, ejecución simulada en la apertura de la vela siguiente y costes configurables.

**Límite funcional actual:** el `decision_cycle` ya puede ejecutar compras/ventas paper solo cuando el Risk Manager aprueba la propuesta. Las posiciones conservan estrategia, stop-loss y take-profit; el worker vuelve a incluir posiciones abiertas para comprobar salidas y utiliza un identificador idempotente para evitar órdenes duplicadas tras reinicios. El ciclo todavía se encola bajo demanda: no hay programación periódica, dashboard, acciones/ETF ni trading real. No hay trading real, acciones/ETF, dashboard ni despliegue 24/7.

## Capital y divisa

El backend actual usa una cartera simulada de **20 EUR**, no 20 USD. Solo admite pares cotizados en EUR. La conversión de divisas y los instrumentos de bolsa requieren una implementación y validación separadas.

## Arquitectura objetivo

- **Chief:** coordina informes, oportunidades y decisiones.
- **Scout:** explora mercados y prioriza activos.
- **News / Sentiment:** analizan noticias y sentimiento con atribución y frescura de fuentes.
- **Charts:** calcula indicadores y analiza tendencias.
- **Strategy Research:** descubre, compara y valida estrategias.
- **Sniper:** propone entrada, salida, stop-loss y tamaño.
- **Portfolio Manager:** gestiona exposición, posiciones y asignación de beneficios.
- **Risk Manager:** valida propuestas con reglas deterministas y no eludibles.
- **Trading Engine:** gestiona órdenes y posiciones paper/live separadas.
- **Dashboard:** presenta cartera, actividad de agentes y rendimiento.

Los mensajes entre agentes usan esquemas tipados. Un LLM no puede ejecutar operaciones ni saltarse el Risk Manager. El funcionamiento esencial debe poder continuar sin un LLM.

## Ejecución local

Requiere Python 3.12+.

```bash
cd backend
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

API: http://localhost:8000  
Swagger: http://localhost:8000/docs

Tests desde `backend/`:

```bash
pytest -q
```

## Docker

```bash
cp .env.example .env
docker compose up --build -d
docker compose logs -f api
```

## Endpoints principales

- `GET /api/health`
- `GET /api/market/{symbol}/ticker`
- `GET /api/market/{symbol}/ohlcv`
- `GET /api/market/{symbol}/indicators`
- `GET /api/portfolio`
- `GET /api/trades`
- `POST /api/orders` — solo paper.
- `GET /api/agent/status` — requiere `X-Agent-Token`.
- `POST /api/agent/resume`, `/pause`, `/emergency-stop`, `/reset-emergency-stop`.
- `POST /api/agent/tasks` — acepta `health_check`, `market_scan` y `decision_cycle`; este último puede ejecutar una orden paper aprobada.

## Supervisión del worker

Configura un secreto largo y aleatorio en `.env` como `AGENT_CONTROL_TOKEN`. Los endpoints de control requieren ese valor en la cabecera `X-Agent-Token`. Si no está configurado, los controles devuelven 503.

El worker se inicia pausado y no realiza operaciones financieras. Para ejecutar un análisis, se debe reanudar explícitamente y encolar una tarea `market_scan`. No expongas la API a Internet sin HTTPS, autenticación, restricciones de red, rate limiting y gestión segura de secretos.

## Seguridad y límites

- Paper trading es el único modo de ejecución disponible.
- No se almacenan claves privadas de exchanges ni se solicitan permisos de retirada.
- El worker depende de que el proceso de la aplicación esté desplegado y activo; una conversación de ChatGPT no mantiene el proceso en ejecución.
- Los backtests son evidencia histórica, no una garantía de rentabilidad.
- Los datos gratuitos pueden tener límites, retrasos o restricciones.
- Live trading requiere un módulo separado, autorización explícita, límites independientes, reconciliación con el proveedor y pruebas específicas.
