# AI Trading System

Plataforma modular de trading algorítmico asistido por agentes de IA.

## Estado actual

**Sprint 1: backend inicial de paper trading.** Utiliza datos públicos de mercado mediante CCXT y una cartera virtual persistida en SQLite. No envía órdenes reales.

## Arquitectura objetivo

- **Chief:** coordina informes y resuelve señales.
- **Scout:** explora mercados y prioriza oportunidades.
- **News / Sentiment:** analizan noticias y sentimiento.
- **Charts:** calcula indicadores y analiza tendencias.
- **Sniper:** propone entrada, salida, stop-loss y tamaño.
- **Risk Manager:** valida propuestas con reglas deterministas.
- **Trading engine:** gestiona órdenes y posiciones.
- **Dashboard:** presenta cartera, actividad de agentes y rendimiento.

Los agentes se incorporarán por fases. Los mensajes entre agentes usarán esquemas tipados; un LLM nunca podrá saltarse el motor de riesgo.

## Sprint 1 incluye

- API REST con FastAPI.
- Datos públicos de criptomonedas mediante CCXT (Kraken por defecto).
- Indicadores EMA, SMA, RSI, MACD, Bollinger Bands y ATR.
- Cartera paper persistida en SQLite.
- Simulación de compras y ventas con comisión y slippage configurables; las comisiones de entrada se incluyen en el coste medio y el P/L realizado es neto de ambas patas.
- Rechazo de datos ausentes o desactualizados.
- Restricción inicial a pares cotizados en EUR para evitar conversiones FX incorrectas.
- Docker Compose y pruebas automatizadas.

Capital virtual inicial: **20 EUR**, según la decisión acordada para el proyecto. El documento de requisitos menciona 20 USD; esta configuración mantiene el saldo en euros.

## Límites actuales

- No hay ejecución real ni integración de órdenes privadas.
- Acciones y ETF pendientes de un proveedor independiente y conversión de divisa validada.
- El fill paper se calcula con ticker público, comisión y slippage configurados.
- Cartera de una sola cuenta.
- Agentes LLM, dashboard y backtesting quedan para siguientes sprints.
- El coste de posición incluye las comisiones de entrada; el P/L realizado descuenta tanto las comisiones de entrada como las de salida.
- El campo `invested_value` representa coste de adquisición, no valor de mercado; el marcado a mercado y el P/L no realizado siguen pendientes.

## Ejecución local

Requiere Python 3.12+.

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

API: http://localhost:8000  
Swagger: http://localhost:8000/docs

Tests desde backend/: `pytest`.

GitHub Actions ejecuta la suite del backend en los pushes y pull requests cubiertos por `.github/workflows/backend-ci.yml`.

## Docker

```bash
cp .env.example .env
docker compose up --build -d
docker compose logs -f api
```

## Endpoints

- `GET /api/health`
- `GET /api/market/{symbol}/ticker`
- `GET /api/market/{symbol}/ohlcv`
- `GET /api/market/{symbol}/indicators`
- `GET /api/portfolio`
- `GET /api/trades`
- `POST /api/orders` (solo paper)

Ejemplo de orden simulada: `{"symbol":"BTC/EUR","side":"buy","quantity":0.00005}`.

## Seguridad

- Paper trading es el único modo disponible.
- No se almacenan claves API ni se solicitan permisos de retirada.
- Live trading requerirá una implementación separada, activación explícita, límites independientes y pruebas específicas.


## Persistent autonomous agent (foundation)

The persistent-agent foundation adds:

- A SQLite-backed operational state that survives restarts.
- A durable task queue with leases, retries and recovery of expired running tasks.
- Separate persistent memory records, validated-knowledge flag and audit events.
- A continuously running asyncio worker, started with the API process; it remains paused by default.
- Authenticated supervision endpoints for status, pause, resume, emergency stop, reset and task enqueue.
- An interchangeable LLM provider protocol and an opt-in OpenAI-compatible adapter. No LLM calls occur by default.

### Configure supervision

Set a long random `AGENT_CONTROL_TOKEN` in the root `.env` file. Agent control endpoints require it in the `X-Agent-Token` header. If the token is unset, these endpoints return 503 and cannot be used. Do not expose the API publicly until HTTPS, authentication, network restrictions and production secret management are configured.

Endpoints:
- `GET /api/agent/status`
- `POST /api/agent/resume`
- `POST /api/agent/pause`
- `POST /api/agent/emergency-stop`
- `POST /api/agent/reset-emergency-stop`
- `POST /api/agent/tasks` (currently only `health_check` tasks are accepted)

The worker starts paused and will not initiate trades. In this phase it only executes a deterministic health-check task. Market scanning, agent delegation, LLM reasoning triggers and automated trading are not yet implemented. Emergency stop disables the worker; it does not need to cancel broker orders because live execution does not exist.

The persistent memory tables currently store operational observations and audit events. Historical market analysis, strategy knowledge, objectives, outcomes and retrieval policies will be added in later phases. Risk controls remain code/configuration controlled; agents cannot change them.
