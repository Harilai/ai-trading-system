# AI Trading System

Plataforma modular de trading algorítmico autónomo, construida por fases con prioridad en la validación, la trazabilidad y la protección del capital.

## Estado real de implementación

La rama integrada contiene actualmente:

- Backend FastAPI y cartera simulada persistida en SQLite.
- Capa de integración universal: contratos normalizados de proveedor, instrumentos, clases de activo, tipos de mercado y registro/capability matrix.
- Adaptador inicial de datos públicos CEX crypto spot mediante CCXT (Kraken por defecto; lista configurable de proveedores CCXT).
- Adaptador opcional de datos de acciones y ETF estadounidenses mediante Twelve Data Basic, activado únicamente al configurar una API key gratuita; incorpora caché SQLite persistente, límites persistentes diario y móvil de 60 segundos basados en créditos estimados, separación mínima entre peticiones y conciliación del consumo con la cabecera `api-credits-used` del proveedor cuando está disponible.
- Pipeline separado de investigación de acciones/ETF con muestreo estratificado con cursor SQLite persistente que rota entre ejecuciones por todo el catálogo, para limitar las cotizaciones sin quedarse siempre con los mismos símbolos, ranking de candidatos por volumen, frescura de cotización y spread; solo descarga histórico para los mejores candidatos. La clasificación usa pesos explícitos (60% volumen, 25% frescura, 15% spread), desempate determinista por símbolo y mantiene investigación histórica separada de los bloqueos de ejecución; endpoint manual de solo lectura `/api/research/equities`.
- Descubrimiento y enrutamiento de datos por proveedor; endpoints `/api/providers`, `/api/markets` y `/api/risk-profiles`.
- Indicadores EMA, SMA, RSI, MACD, Bollinger Bands y ATR.
- Simulación de compras y ventas con comisiones y slippage configurables.
- Cartera paper con posiciones y operaciones aisladas por `provider_id + instrument_id`, incluida migración automática de bases SQLite anteriores.
- Rechazo de pares no admitidos y datos de mercado obsoletos.
- Worker asíncrono persistente con cola durable, leases, reintentos, recuperación de tareas expiradas y tolerancia a fallos transitorios del bucle de supervisión.
- API de supervisión autenticada: estado, pausa, reanudación, parada de emergencia y encolado de tareas.
- Scout que descubre mercados spot cotizados en EUR, filtra por liquidez, spread y frescura de ticker.
- Charts Agent determinista.
- Strategy Research Agent con tres estrategias candidatas long-only (EMA 12/26, RSI(14) y Bollinger(20,2)), backtesting cronológico 70/30, validación de OHLCV finito y cronológico, benchmark buy-and-hold OOS con la misma exposición, tres ventanas walk-forward con gates de promoción, escenarios de estrés de comisiones/slippage a 1,5x y 2x, ejecución simulada en la apertura de la vela siguiente y costes configurables. La promoción exige al menos 3 operaciones OOS, tres ventanas walk-forward con al menos 40 velas evaluables y una operación por ventana, dos ventanas positivas como mínimo, retorno medio walk-forward positivo, drawdown dentro del límite y superar buy-and-hold neto de costes. El escenario 1,5x es un gate obligatorio: la estrategia debe conservar retorno neto positivo y superar buy-and-hold; el escenario 2x sigue siendo diagnóstico adicional.

**Límite funcional actual:** el `decision_cycle` puede ejecutar compras/ventas paper solo cuando el Risk Manager aprueba la propuesta. La capa universal es una arquitectura extensible, no soporte universal ya operativo: actualmente solo está implementado el adaptador de datos públicos crypto spot vía CCXT. No hay adaptadores de ejecución para brokers, DEX, wallets, forex, índices, materias primas, futuros, opciones ni DeFi. Twelve Data aporta datos de acciones/ETF de EE. UU., pero no ejecución ni conexión con brokers; el ciclo autónomo de selección/órdenes sigue limitado a crypto spot EUR. La investigación de acciones/ETF también puede ejecutarse como tarea autónoma de solo lectura cada 6 horas por defecto cuando el worker está habilitado y Twelve Data está configurado; no crea propuestas ni órdenes. La cartera paper ya identifica cada posición mediante `provider_id + instrument_id`, por lo que puede mantener el mismo símbolo en proveedores distintos. La migración SQLite conserva las posiciones antiguas asignándolas al proveedor histórico `ccxt:kraken`. No hay trading real ni permisos privados de proveedor.

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

La integración conserva su caché y estado de cuota en `data/twelve_data_state.sqlite3` (dentro del volumen persistente de Docker). Por defecto limita el ritmo a una petición cada 7,5 segundos, aplica un máximo móvil de 8 créditos estimados en cualquier ventana de 60 segundos y un presupuesto diario de 750 créditos, dejando margen respecto al límite publicado de 800 créditos. El coste real puede variar por endpoint: cuando la respuesta incluye `api-credits-used`, el adaptador concilia tanto el registro diario como la ventana móvil; si no está presente, conserva la estimación local conservadora. El coste real en créditos puede depender del endpoint; la caché evita repetir llamadas idénticas mientras los datos siguen vigentes.

## Docker

```bash
cp .env.example .env
# Configura AGENT_CONTROL_TOKEN y, opcionalmente, TWELVE_DATA_API_KEY
docker compose up --build -d
docker compose logs -f api
```

El contenedor se ejecuta como usuario no privilegiado, con sistema de archivos raíz de solo lectura, capacidades Linux eliminadas y volumen persistente para SQLite. El puerto se publica solo en `127.0.0.1`; no queda expuesto directamente a Internet. Para administrar una instancia remota, usa un túnel SSH (`ssh -L 8000:127.0.0.1:8000 usuario@servidor`) o configura un reverse proxy TLS independiente y restringido.

## Endpoints principales

- `GET /api/providers` — proveedores configurados y capability matrix. Twelve Data aparece si `TWELVE_DATA_API_KEY` está configurada.
- `GET /api/markets` — puede incluir el catálogo de acciones/ETF de EE. UU. de Twelve Data.
- `GET /api/research/equities?max_instruments=5&max_quote_candidates=10&timeframe=1day` — investigación manual de acciones/ETF; requiere `TWELVE_DATA_API_KEY`, limita las cotizaciones examinadas (1–100) y el histórico descargado (1–10), y nunca ejecuta operaciones. El muestreo de cotizaciones se distribuye por todo el catálogo y la clasificación preliminar prioriza volumen (60%), frescura (25%) y spread (15%); la ausencia de volumen penaliza la posición, pero no elimina por sí sola la investigación histórica.
- `GET /api/markets` — instrumentos detectados y restricciones conocidas.
- `GET /api/risk-profiles` — perfiles de riesgo y estado habilitado/deshabilitado.
- `GET /api/health` — liveness del proceso.\n- `GET /api/health/ready` — comprueba SQLite y heartbeat reciente del worker; es el healthcheck de Docker.
- `GET /api/market/{symbol}/ticker`
- `GET /api/market/{symbol}/ohlcv`
- `GET /api/market/{symbol}/indicators`
- `GET /api/portfolio`
- `GET /api/trades`
- `GET /api/agent/withdrawals` — requiere `X-Agent-Token`.
- `POST /api/agent/withdrawals` — solicita retirada paper de cualquier efectivo disponible; se desglosa la parte de beneficio reservado y la parte no reservada.
- `POST /api/agent/withdrawals/{id}/approve` y `/cancel` — aprobación/cancelación manual con token.
- `POST /api/orders` — solo paper; exige `X-Agent-Token` válido (`AGENT_CONTROL_TOKEN`), aprobación del Risk Manager y stop-loss explícito en compras; admite `provider_id`, `instrument_id`, `strategy_id` y niveles protectores.
- `GET /api/agent/strategy-history?limit=100&strategy_id=ema_12_26&instrument_id=...` — historial persistente de validación, benchmark, walk-forward, costes y motivos de rechazo; requiere `X-Agent-Token`.
- `GET /api/agent/status` — requiere `X-Agent-Token`.
- `POST /api/agent/resume`, `/pause`, `/emergency-stop`, `/reset-emergency-stop`.
- `POST /api/agent/tasks` — acepta `health_check`, `market_scan`, `equity_research` (solo lectura) y `decision_cycle`; este último puede ejecutar una orden paper aprobada.

## Supervisión del worker

Configura un secreto largo y aleatorio en `.env` como `AGENT_CONTROL_TOKEN` y ajusta `MAX_STOP_DISTANCE_FRACTION` si procede (por defecto 0,05). Los endpoints de control y el endpoint manual de órdenes paper requieren ese valor en la cabecera `X-Agent-Token`. Si no está configurado, permanecen deshabilitados y devuelven 503. Las compras manuales necesitan stop-loss y pasan por el mismo Risk Manager determinista que las decisiones autónomas.

El worker publica un heartbeat cada ciclo; `/api/health/ready` falla si la base de datos no responde o el heartbeat supera 600 segundos. Docker usa esta comprobación de readiness, no solo la respuesta HTTP del proceso. El worker se inicia pausado. Al reanudarlo explícitamente, programa un `decision_cycle` cada 5 minutos por defecto (`DECISION_CYCLE_INTERVAL_SECONDS`), sin solapar tareas pendientes/en ejecución. Si Twelve Data está configurado, también programa una tarea `equity_research` independiente cada 6 horas (`EQUITY_RESEARCH_INTERVAL_SECONDS`, mínimo 1 hora). Esta tarea muestrea el catálogo, analiza acciones/ETF y persiste el resultado en la memoria/auditoría del agente; nunca genera órdenes. La cuota de Twelve Data se protege con límites persistentes. Cada ciclo puede ejecutar órdenes paper si el Chief genera una propuesta y el Risk Manager la aprueba. También se pueden encolar análisis manuales. Las salidas stop-loss/take-profit se comprueban en cada ciclo, no continuamente entre ciclos. La contabilidad separa P/L realizado y no realizado; la reserva provisional destina el 50% del beneficio neto realizado a retirada y el 50% a reinversión. Las retiradas paper requieren solicitud y aprobación manual y pueden usar cualquier efectivo disponible, incluido principal y beneficio; la contabilidad identifica cuánto procede de la reserva de beneficio y cuánto de capital no reservado. Las solicitudes pendientes reservan efectivo para evitar compromisos duplicados. No se liquidan posiciones automáticamente: si el efectivo no basta, primero habría que autorizar una venta paper controlada. No expongas la API a Internet sin HTTPS, autenticación, restricciones de red, rate limiting y gestión segura de secretos.

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
