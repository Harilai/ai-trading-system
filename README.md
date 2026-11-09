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
- Simulación de compras y ventas con comisión y slippage configurables.
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
- El valor invertido del resumen inicial se calcula al precio medio de entrada; el marcado a mercado y P/L no realizado se incorporarán con el servicio de valoración.

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

Tests desde backend/: `pytest`

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
