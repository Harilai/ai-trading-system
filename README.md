# AI Trading System

Plataforma modular de trading algorítmico autónomo, construida por fases con prioridad en la validación, la trazabilidad y la protección del capital.

## Estado real de implementación

La rama integrada contiene actualmente:

- Backend FastAPI y cartera simulada persistida en SQLite.
- Capa de integración universal: contratos normalizados de proveedor, instrumentos, clases de activo, tipos de mercado y registro/capability matrix.
- Adaptador inicial de datos públicos CEX crypto spot mediante CCXT (Kraken por defecto; lista configurable de proveedores CCXT).
- Adaptador opcional de datos de acciones y ETF estadounidenses mediante Twelve Data Basic, activado únicamente al configurar una API key gratuita; incorpora caché SQLite persistente, límite diario conservador, separación mínima entre peticiones y conciliación del consumo con la cabecera `api-credits-used` del proveedor cuando está disponible.
- Pipeline separado de investigación de acciones/ETF con filtros de divisa, frescura, bid/ask y backtesting cronológico; endpoint manual de solo lectura `/api/research/equities`.
- Descubrimiento y enrutamiento de datos por proveedor; endpoints `/api/providers`, `/api/markets` y `/api/risk-profiles`.
- Indicadores EMA, SMA, RSI, MACD, Bollinger Bands y ATR.
- Simulación de compras y ventas con comisiones y slippage configurables.
- Cartera paper con posiciones y operaciones aisladas por `provider_id + instrument_id`, incluida migración automática de bases SQLite anteriores.
- Rechazo de pares no admitidos y datos de mercado obsoletos.
- Worker asíncrono persistente con cola durable, leases, reintentos y recuperación de tareas expiradas.
- API de supervisión autenticada: estado, pausa, reanudación, parada de emergencia y encolado de tareas.
- Scout que descubre mercados spot cotizados en EUR, filtra por liquidez, spread y frescura de ticker.
- Charts Agent determinista.
- Strategy Research Agent con tres estrategias candidatas long-only (EMA 12/26, RSI(14) y Bollinger(20,2)), backtesting cronológico 70/30, ejecución simulada en la apertura de la vela siguiente y costes configurables.

**Límite funcional actual:** el `decision_cycle` puede ejecutar compras/ventas paper solo cuando el Risk Manager aprueba la propuesta. La capa universal es una arquitectura extensible, no soporte universal ya operativo: actualmente solo está implementado el adaptador de datos públicos crypto spot vía CCXT. No hay adaptadores de ejecución para brokers, DEX, wallets, forex, índices, materias primas, futuros, opciones ni DeFi. Twelve Data aporta datos de acciones/ETF de EE. UU., pero no ejecución ni conexión con brokers; el ciclo autónomo de selección/órdenes sigue limitado a crypto spot EUR. La investigación de acciones/ETF se ejecuta manualmente y no crea propuestas ni órdenes. La cartera paper ya identifica cada posición mediante `provider_id + instrument_id`, por lo que puede mantener el mismo símbolo en proveedores distintos. La migración SQLite conserva las posiciones antiguas asignándolas al proveedor histórico `ccxt:kraken`. No hay trading real ni permisos privados de proveedor.

## Capital y divisa

El backend actual usa una cartera simulada de **20 EUR**, no 20 USD. La primera integración solo analiza pares crypto spot cotizados en EUR. La arquitectura de proveedores no implica acceso a todos los mercados. Forex, acciones, ETF, índices, commodities, derivados, DEX, wallets y DeFi siguen sin adaptadores operativos.

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

## Configuración de credenciales

Crea el archivo `.env` en la raíz del repositorio, junto a `docker-compose.yml`, copiando `.env.example`. Añade la clave nueva de Twelve Data en `TWELVE_DATA_API_KEY=`. El archivo está excluido por Git y no debe subirse al repositorio. La configuración carga ese archivo tanto al ejecutar desde la raíz como desde `backend/`; Docker Compose lo inyecta mediante `env_file`.

La integración conserva su caché y estado de cuota en `data/twelve_data_state.sqlite3` (dentro del volumen persistente de Docker). Por defecto limita el ritmo a una petición cada 7,5 segundos y aplica un presupuesto de seguridad de 750 unidades de petición estimadas por día, dejando margen respecto al límite publicado de 800 créditos. El coste real en créditos puede depender del endpoint; la caché evita repetir llamadas idénticas mientras los datos siguen vigentes.

## Docker

```bash
cp .env.example .env
# Edita .env y añade TWELVE_DATA_API_KEY
docker compose up --build -d
docker compose logs -f api
```

## Endpoints principales

- `GET /api/providers` — proveedores configurados y capability matrix. Twelve Data aparece si `TWELVE_DATA_API_KEY` está configurada.
- `GET /api/markets` — puede incluir el catálogo de acciones/ETF de EE. UU. de Twelve Data.
- `GET /api/research/equities?max_instruments=5&timeframe=1day` — investigación manual de acciones/ETF; requiere `TWELVE_DATA_API_KEY`, está limitada a 1–10 instrumentos y nunca ejecuta operaciones.
- `GET /api/markets` — instrumentos detectados y restricciones conocidas.
- `GET /api/risk-profiles` — perfiles de riesgo y estado habilitado/deshabilitado.
- `GET /api/health`
- `GET /api/market/{symbol}/ticker`
- `GET /api/market/{symbol}/ohlcv`
- `GET /api/market/{symbol}/indicators`
- `GET /api/portfolio`
- `GET /api/trades`
- `GET /api/agent/withdrawals` — requiere `X-Agent-Token`.
- `POST /api/agent/withdrawals` — solicita retirada paper de la reserva de beneficio.
- `POST /api/agent/withdrawals/{id}/approve` y `/cancel` — aprobación/cancelación manual con token.
- `POST /api/orders` — solo paper; admite `provider_id` e `instrument_id`.
- `GET /api/agent/status` — requiere `X-Agent-Token`.
- `POST /api/agent/resume`, `/pause`, `/emergency-stop`, `/reset-emergency-stop`.
- `POST /api/agent/tasks` — acepta `health_check`, `market_scan` y `decision_cycle`; este último puede ejecutar una orden paper aprobada.

## Supervisión del worker

Configura un secreto largo y aleatorio en `.env` como `AGENT_CONTROL_TOKEN`. Los endpoints de control requieren ese valor en la cabecera `X-Agent-Token`. Si no está configurado, los controles devuelven 503.

El worker se inicia pausado. Al reanudarlo explícitamente, programa un `decision_cycle` cada 5 minutos por defecto (`DECISION_CYCLE_INTERVAL_SECONDS`), sin solapar tareas pendientes/en ejecución. Cada ciclo puede ejecutar órdenes paper si el Chief genera una propuesta y el Risk Manager la aprueba. También se pueden encolar análisis manuales. Las salidas stop-loss/take-profit se comprueban en cada ciclo, no continuamente entre ciclos. La contabilidad separa P/L realizado y no realizado; la reserva provisional destina el 50% del beneficio neto realizado a retirada y el 50% a reinversión. Las retiradas paper requieren solicitud y aprobación manual, y no pueden consumir principal ni ganancias no realizadas. No expongas la API a Internet sin HTTPS, autenticación, restricciones de red, rate limiting y gestión segura de secretos.

## Seguridad y límites

- Paper trading es el único modo de ejecución disponible.
- No se almacenan claves privadas de exchanges ni se solicitan permisos de retirada.
- El adaptador CCXT inicial es exclusivamente de datos públicos; no autentica cuentas ni envía órdenes.
- Los perfiles de margin, futures, options, forex y DeFi están deshabilitados. El Risk Manager solo permite crypto spot.
- No se solicita ni registra ninguna seed phrase o clave privada de wallet. No existe aún infraestructura de firma.
- El worker depende de que el proceso de la aplicación esté desplegado y activo; una conversación de ChatGPT no mantiene el proceso en ejecución.
- Los backtests son evidencia histórica, no una garantía de rentabilidad.
- Twelve Data Basic requiere una API key y limita el consumo a 8 créditos por minuto y 800 al día; el adaptador inicial consulta solo acciones/ETF estadounidenses. No se consulta ni se habilita ningún plan de pago. Los datos gratuitos pueden tener límites, retrasos o restricciones.
- Live trading requiere un módulo separado, autorización explícita, límites independientes, reconciliación con el proveedor y pruebas específicas.
