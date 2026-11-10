# AI Trading System

Plataforma modular de trading algorítmico multiactivo y multi-venue, orientada a investigación, gestión de cartera y eventual ejecución con capital real. Se construye por fases con prioridad en validación, trazabilidad y protección del capital.

**Continuidad entre conversaciones:** consulta [`docs/SESSION_HANDOFF.md`](docs/SESSION_HANDOFF.md) para conocer el último estado revisado y el siguiente paso concreto. El método obligatorio de trabajo por roles especializados está en [`docs/AGENT_WORKFLOW.md`](docs/AGENT_WORKFLOW.md). El conector Binance de solo lectura y sus límites están en [`docs/BINANCE_ACCOUNT_CONNECTOR.md`](docs/BINANCE_ACCOUNT_CONNECTOR.md); el conector Binance Demo Trading aislado está en [`docs/BINANCE_DEMO_TRADING.md`](docs/BINANCE_DEMO_TRADING.md), y el procedimiento de validación operativa en [`docs/BINANCE_DEMO_SMOKE_TEST_RUNBOOK.md`](docs/BINANCE_DEMO_SMOKE_TEST_RUNBOOK.md); el historial inmutable de saldos y la conciliación entre snapshots se describen en [`docs/ACCOUNT_SNAPSHOT_RECONCILIATION.md`](docs/ACCOUNT_SNAPSHOT_RECONCILIATION.md). Los contratos normalizados de cuentas multi-venue están en [`docs/ACCOUNT_PROVIDER_CONTRACTS.md`](docs/ACCOUNT_PROVIDER_CONTRACTS.md). El diseño de cartera EUR/USD está en [`docs/MULTICURRENCY_DESIGN.md`](docs/MULTICURRENCY_DESIGN.md). La arquitectura objetivo multiactivo y multi-venue está en [`docs/MARKET_EXPANSION_ARCHITECTURE.md`](docs/MARKET_EXPANSION_ARCHITECTURE.md). El contrato normalizado de instrumentos está definido en [`docs/INSTRUMENT_CONTRACT_V2.md`](docs/INSTRUMENT_CONTRACT_V2.md). El catálogo persistente y su identidad venue-aware se describen en [`docs/INSTRUMENT_CATALOG_DESIGN.md`](docs/INSTRUMENT_CATALOG_DESIGN.md). El despliegue endurecido en VPS se documenta en [`docs/VPS_DEPLOYMENT_RUNBOOK.md`](docs/VPS_DEPLOYMENT_RUNBOOK.md). Comprueba siempre el estado vivo de `main` y las PR antes de continuar.

## Estado real de implementación

La rama integrada contiene actualmente:

- Backend FastAPI y cartera simulada persistida en SQLite.
- Capa de integración extensible: contratos normalizados de proveedor, instrumentos, clases de activo, tipos de mercado y registro/capability matrix; las capacidades reales dependen de cada adaptador.
- Adaptador inicial de datos públicos CEX crypto spot mediante CCXT (Kraken por defecto; lista configurable de proveedores CCXT).
- Adaptador opcional de datos de acciones y ETF estadounidenses mediante Twelve Data Basic, activado únicamente al configurar una API key gratuita; incorpora caché SQLite persistente, límites persistentes diario y móvil de 60 segundos basados en créditos estimados, separación mínima entre peticiones y conciliación del consumo con la cabecera `api-credits-used` del proveedor cuando está disponible.
- Pipeline separado de investigación de acciones/ETF con muestreo estratificado con cursor SQLite persistente que rota entre ejecuciones por todo el catálogo, para limitar las cotizaciones sin quedarse siempre con los mismos símbolos, ranking de candidatos por volumen, frescura de cotización y spread; solo descarga histórico para los mejores candidatos. La clasificación usa pesos explícitos (60% volumen, 25% frescura, 15% spread), desempate determinista por símbolo y mantiene investigación histórica separada de los bloqueos de ejecución; endpoint manual de solo lectura `/api/research/equities`.
- Descubrimiento y enrutamiento de datos por proveedor; endpoints `/api/providers`, `/api/markets` y `/api/risk-profiles`.
- Conector autenticado Binance Spot de solo lectura opcional: consulta de balances reales con owner token; sin órdenes, transferencias ni retiradas.
- Indicadores EMA, SMA, RSI, MACD, Bollinger Bands y ATR.
- Simulación de compras y ventas con comisiones y slippage configurables.
- Cartera paper multidivisa con efectivo EUR/USD separado, posiciones por `provider_id + instrument_id`, coste base EUR por posición USD y migración automática de bases SQLite anteriores.
- Conversión paper EUR/USD explícita, autenticada, idempotente y auditada; tipos FX de Twelve Data con caché, cuota persistente y validación de frescura.
- Órdenes paper manuales (solo propietario autenticado) para acciones/ETF cash USD de EE. UU. descubiertas por Twelve Data, con cotización reciente, spread máximo si hay bid/ask, sesión abierta, conversión FX, stop-loss obligatorio en compras y Risk Manager determinista. No hay ejecución mediante broker ni operaciones autónomas de equities.
- Rechazo de pares no admitidos y datos de mercado obsoletos.
- Worker asíncrono persistente con cola durable, leases, reintentos, recuperación de tareas expiradas y tolerancia a fallos transitorios del bucle de supervisión.
- Ciclo de vida paper de órdenes persistido: intentos aprobados inmutables, fills atómicos, eventos de auditoría append-only, conciliación de ledger, caducidad de datos stale y recuperación acotada con fencing de leases (PR #44).
- Historial autenticado de intentos y eventos disponible en `/api/agent/order-intents`; el token de control se envía por cabecera y no se almacena en el navegador.
- API de supervisión autenticada: estado, pausa, reanudación, parada de emergencia y encolado de tareas.
- Scout que descubre mercados spot cotizados en EUR, filtra por liquidez, spread y frescura de ticker.
- Charts Agent determinista.
- Strategy Research Agent con tres estrategias candidatas long-only (EMA 12/26, RSI(14) y Bollinger(20,2)), backtesting cronológico 70/30, validación de OHLCV finito y cronológico, benchmark buy-and-hold OOS con la misma exposición, tres ventanas walk-forward con gates de promoción, escenarios de estrés de comisiones/slippage a 1,5x y 2x, ejecución simulada en la apertura de la vela siguiente y costes configurables. La promoción exige al menos 3 operaciones OOS, tres ventanas walk-forward con al menos 40 velas evaluables y una operación por ventana, dos ventanas positivas como mínimo, retorno medio walk-forward positivo, drawdown dentro del límite y superar buy-and-hold neto de costes. El escenario 1,5x es un gate obligatorio: la estrategia debe conservar retorno neto positivo y superar buy-and-hold; el escenario 2x sigue siendo diagnóstico adicional. El análisis también etiqueta tendencia alcista/bajista/rango y volatilidad relativa como contexto diagnóstico, sin alterar la elegibilidad ni habilitar órdenes.

**Límite funcional actual:** el `decision_cycle` autónomo sigue limitado a crypto spot EUR y solo ejecuta cuando el Risk Manager aprueba. Las acciones/ETF estadounidenses pueden mantenerse y negociarse únicamente en modo paper mediante órdenes manuales autenticadas, después de convertir efectivo EUR a USD; el Chief no genera ni ejecuta propuestas de equities. Twelve Data es exclusivamente proveedor de datos/FX, no broker: no existe conexión de ejecución real. No hay adaptadores de ejecución para brokers, DEX, wallets, forex trading, índices, materias primas, futuros, opciones ni DeFi. La investigación de acciones/ETF puede ejecutarse como tarea autónoma de solo lectura cada 6 horas por defecto; no crea propuestas ni órdenes. La cartera reporta equity consolidado en EUR y conserva saldos/valores nativos, pero la valoración queda incompleta si falta una cotización o FX fresco. No hay trading real ni permisos privados de proveedor.

## Capital y divisa

El backend parte de una cartera simulada de **20 EUR**. EUR es la divisa base de reporting; el efectivo USD se mantiene en un saldo separado. Para comprar acciones/ETF estadounidenses en paper es obligatorio convertir EUR a USD mediante una operación explícita, autenticada e idempotente. La conversión conserva tipo, proveedor, timestamp, importes y coste. La valoración agregada convierte USD a EUR con FX fresco; si no hay tipo válido, se marca incompleta y se bloquean nuevas órdenes hasta recuperar una valoración fiable. El trading autónomo continúa limitado a crypto spot EUR. Los valores paper iniciales son supuestos configurables, no tarifas de un broker: conversión FX 0,10%, comisión equity 0,10%, slippage equity 0,10%, spread máximo 2%, FX máximo 300 s y orden mínima 1 USD, límite por orden del 10%, concentración máxima por instrumento del 20% y distancia máxima del stop del 5%. Se ajustan con `FX_CONVERSION_FEE_RATE`, `EQUITY_PAPER_FEE_RATE`, `EQUITY_PAPER_SLIPPAGE_RATE`, `MAX_EQUITY_SPREAD_FRACTION`, `MAX_EQUITY_ORDER_FRACTION`, `MAX_EQUITY_POSITION_FRACTION`, `MAX_EQUITY_STOP_DISTANCE_FRACTION`, `MAX_FX_DATA_AGE_SECONDS` y `MIN_ORDER_VALUE_USD`.

## Arquitectura objetivo

- **Chief:** coordina informes, oportunidades y decisiones.
- **Scout:** explora mercados y prioriza activos.
- **News / Sentiment:** analizan noticias y sentimiento con atribución y frescura de fuentes.
- **Charts:** calcula indicadores y analiza tendencias.
- **Strategy Research:** descubre, compara y valida estrategias.
- **Sniper:** propone entrada, salida, stop-loss y tamaño.
- **Portfolio Manager:** gestiona exposición, posiciones y asignación de beneficios.
- **Risk Manager:** valida propuestas con reglas deterministas y no eludibles.
- **Trading Engine:** gestiona órdenes y posiciones paper; la ejecución live está planificada, pero no implementada ni habilitada.
- **Dashboard:** cartera y posiciones, gráfico OHLC, salud/readiness de proveedores, investigación read-only, historial de estrategias, órdenes paper y conciliación, estado de backups locales/off-host y solicitudes de retirada paper. El token de control se mantiene solo en memoria, y las retiradas nunca mueven dinero real.

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
Dashboard: http://localhost:8000/dashboard  
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

El contenedor API se ejecuta como usuario no privilegiado, con sistema de archivos raíz de solo lectura, capacidades Linux eliminadas y volumen persistente para SQLite. Un servicio independiente `backup` ejecuta copias diarias de ambas bases SQLite y, si se configura, las replica a un almacén S3-compatible por una red de egreso dedicada. El contenedor no publica puertos, usa usuario no privilegiado, filesystem read-only, capacidades eliminadas y healthcheck propio; los ciclos fallidos se reintentan sin esperar 24 horas. Ambos contenedores tienen rotación de logs. Las credenciales de almacenamiento se cargan únicamente desde `.env.backup` en el servicio `backup`; no se inyectan en el contenedor API. El puerto se publica solo en `127.0.0.1`; no queda expuesto directamente a Internet. Para administrar una instancia remota, usa un túnel SSH (`ssh -L 8000:127.0.0.1:8000 usuario@servidor`) o configura un reverse proxy TLS independiente y restringido.

## Demo local paper

Para probar el dashboard sin cuenta de exchange ni claves de trading, desde la raíz del repositorio ejecuta un único comando:

```bash
docker compose -f docker-compose.demo.yml up --build
```

Abre <http://localhost:8000/dashboard>. La instancia usa **10.000 EUR virtuales**, una base de datos/volumen separado (`paper_demo_data`) y cotizaciones públicas de Kraken. Las órdenes de la cartera son paper: no se envían a ningún exchange. Binance Spot Demo, Binance read-only, CCXT autenticado y backups externos se fuerzan desactivados; el compose no declara ningún servicio de producción. El puerto queda enlazado a localhost.

La demo local habilita un **modo de exploración experimental paper-only** para que el worker pueda probar señales aunque todavía no superen todos los criterios de promoción de estrategias. No se considera evidencia de rentabilidad: puede perder el saldo virtual. Cada estrategia y operación queda identificada con el prefijo `experimental:`. Este modo solo se activa cuando `PAPER_DEMO_MODE=true` y `PAPER_EXPERIMENTAL_STRATEGIES_ENABLED=true`; ambos valores están activados únicamente en el Compose de demo y desactivados por defecto en otras configuraciones.

Los límites de la demo experimental son deliberadamente más estrictos: riesgo presupuestado por operación del **0,25% del equity**, notional máximo por orden del **5% del equity** y distancia máxima del stop del **3%**. Se mantienen el Risk Manager, la frescura de mercado, la identidad de instrumento, la idempotencia y la conciliación paper. Las estrategias experimentales deben tener tres ventanas walk-forward completas y drawdown OOS/walk-forward no superior al 25%, pero no tienen que ser rentables ni superar buy-and-hold. Por eso el dashboard debe distinguir claramente el modo experimental del modo de estrategias validadas.

El endpoint `/api/health/ready` informa `experimental_paper_enabled`. Esta opción solo simula operaciones en la cartera local de 10.000 EUR; no activa Binance Demo, no conecta una wallet y no puede enviar órdenes reales. El ciclo puede quedarse en `hold` sin crear operaciones si ninguna estrategia supera las comprobaciones fuera de muestra, walk-forward y costes; no se fuerza actividad. Kraken puede omitir el timestamp del exchange: en ese caso se conserva la hora local de recepción, identificada explícitamente como `retrieved_at` y limitada a datos de mercado paper.

El control local del dashboard usa el token fijo `local-paper-demo-only`; solo es apropiado para esta instancia enlazada a localhost. No expongas este compose a una red ni lo reutilices como despliegue. Para apagar la demo, pulsa `Ctrl+C`; para iniciarla en segundo plano, añade `-d` y usa `docker compose -f docker-compose.demo.yml down` para detenerla. Los datos virtuales persisten en el volumen `paper_demo_data`.

Por defecto se muestra crypto con datos públicos. Para habilitar datos de acciones/ETF, copia `.env.demo.example` a `.env.demo` y añade una clave Twelve Data Basic de **solo datos**. Esa clave es opcional y solo se inyecta al servicio API como proveedor de cotizaciones; no se requieren ni deben añadirse credenciales de exchange, broker, Binance Demo, wallets o producción. `.env.demo` está excluido de Git.

La configuración se puede revisar sin iniciar contenedores con:

```bash
docker compose -f docker-compose.demo.yml config --quiet
```


## Copias de seguridad y restauración SQLite

La utilidad `app.backup` usa la API de backup online de SQLite, verifica `PRAGMA integrity_check` y escribe el destino de forma atómica. Incluye los commits del WAL. Haz copias de ambas bases: cartera/worker (`trading.db`) y estado/cuotas de Twelve Data (`twelve_data_state.sqlite3`).

Desde la carpeta `backend`:

```bash
python -m app.backup backup --source ../data/trading.db --destination ../data/backups/trading.db
python -m app.backup backup --source ../data/twelve_data_state.sqlite3 --destination ../data/backups/twelve_data_state.sqlite3
```

En Docker:

```bash
docker compose exec -T api python -m app.backup backup --source /app/data/trading.db --destination /app/data/backups/trading.db
docker compose exec -T api python -m app.backup backup --source /app/data/twelve_data_state.sqlite3 --destination /app/data/backups/twelve_data_state.sqlite3
```

Para restaurar, detén primero la API para evitar escrituras concurrentes. La restauración valida el backup y crea automáticamente una copia de seguridad del fichero actual antes de sustituirlo:

```bash
docker compose stop api
docker compose run --rm --no-deps api python -m app.backup restore --source /app/data/backups/trading.db --destination /app/data/trading.db
docker compose run --rm --no-deps api python -m app.backup restore --source /app/data/backups/twelve_data_state.sqlite3 --destination /app/data/twelve_data_state.sqlite3
docker compose up -d api
```

El servicio `backup` ejecuta este mismo mecanismo automáticamente cada 24 horas (`BACKUP_INTERVAL_SECONDS`). La retención local por defecto es de 14 días (`BACKUP_RETENTION_DAYS`); si off-host está activado, no elimina un snapshot local hasta que su objeto remoto esté verificado.

### Activar copias externas S3

1. Copia `.env.backup.example` a `.env.backup`.
2. Configura un bucket privado S3 o compatible (R2, B2, MinIO), región, prefijo y modo de cifrado. La identidad necesita listar el bucket y hacer Put/Get solo en el prefijo; no necesita permisos de borrado.
3. Usa un IAM/workload role cuando sea posible; si necesitas claves estáticas, guárdalas solo en `.env.backup`. No las copies a `.env` ni al navegador.
4. Configura versioning, bloqueo de acceso público y lifecycle/retención en el proveedor. El sistema no elimina objetos remotos ni necesita permiso de borrado.
5. Arranca/actualiza con `docker compose up --build -d`.

El modo externo está desactivado por defecto. Se requiere HTTPS y cifrado server-side `AES256` o `aws:kms`; `provider-managed` solo cuando el proveedor garantiza cifrado en reposo. Cada upload se verifica con HEAD, tamaño, metadatos SHA-256 y lectura de vuelta. Si falla la copia externa, la copia local continúa y el estado muestra el fallo; los snapshots no verificados se conservan.

Consulta estado con `GET /api/agent/backup-status` y el token de control en la cabecera `X-Agent-Token`. Los logs están en `docker compose logs -f backup`.

### Inspeccionar y restaurar una copia externa

Lista los objetos remotos y descarga uno a un fichero candidato; el comando verifica SHA-256 y la integridad SQLite y no sobrescribe un candidato existente:

```bash
docker compose run --rm --no-deps backup python -m app.backup_remote list
docker compose run --rm --no-deps backup python -m app.backup_remote download --key "ai-trading-system/2026/11/10/trading-...sqlite3" --destination /app/data/restore-candidate-trading.sqlite3
docker compose run --rm --no-deps backup python -m app.backup_remote download --key "ai-trading-system/2026/11/10/twelve_data_state-...sqlite3" --destination /app/data/restore-candidate-twelve-data.sqlite3
```

Para restaurar, detén API y scheduler antes de sustituir cualquier base activa. Restaura cada candidato por separado; `app.backup restore` conserva una copia de seguridad pre-restauración:

```bash
docker compose stop api backup
docker compose run --rm --no-deps backup python -m app.backup restore --source /app/data/restore-candidate-trading.sqlite3 --destination /app/data/trading.db
docker compose run --rm --no-deps backup python -m app.backup restore --source /app/data/restore-candidate-twelve-data.sqlite3 --destination /app/data/twelve_data_state.sqlite3
docker compose up -d api backup
```

Practica restauraciones periódicas en un entorno aislado.

El dashboard muestra cartera, posiciones, operaciones, heartbeat, tareas y controles del worker. Para pausar, reanudar, activar emergencia o encolar tareas, introduce `AGENT_CONTROL_TOKEN`; el token solo se mantiene en memoria de esa página y no se guarda en `localStorage`. La UI aún no incluye un formulario de conversión FX ni de órdenes manuales de equities; esas operaciones están disponibles por API autenticada. No hay conexión de wallets.

## Endpoints principales

- `GET /api/providers` — proveedores configurados y capability matrix. Twelve Data aparece si `TWELVE_DATA_API_KEY` está configurada.
- `GET /api/accounts` — estado y capacidades de conectores de cuenta; requiere `X-Agent-Token`.
- `GET /api/accounts/{provider_id}/balances?account_alias=...` — balances normalizados por proveedor/cuenta; requiere `X-Agent-Token`.
- `GET /api/accounts/{provider_id}/positions?account_alias=...` — posiciones normalizadas solo para conectores que declaren y ejecuten explícitamente la capacidad `positions`; no infiere posiciones spot a partir de balances. Devuelve `409` si el proveedor no soporta posiciones; requiere `X-Agent-Token`.
- El conector CCXT read-only permite añadir un CEX compatible (Kraken por defecto) mediante `CCXT_READONLY_EXCHANGE_ID`, `CCXT_READONLY_API_KEY` y `CCXT_READONLY_API_SECRET`; solo consulta balances Spot y no expone métodos de trading.
- `POST /api/accounts/{provider_id}/snapshots?account_alias=...` — snapshot inmutable solo para cuentas Spot declaradas explícitamente read-only; rechaza cuentas Demo/trading-enabled.
- `GET /api/accounts` también devuelve `normalized_accounts` con capacidades y permisos declarados. `permissions_verified=false` indica que el software no ha verificado los scopes reales de la API key; el operador debe crearla con privilegios mínimos.
- `GET /api/accounts/binance/spot/balances` — balances reales de Binance Spot, solo lectura y sin persistencia; requiere `X-Agent-Token`.
- `GET /api/demo/binance/spot/balances` — balances virtuales de Binance Spot Demo Trading; requiere `X-Agent-Token` y configuración demo explícita.
- `GET /api/demo/binance/spot/markets` — catálogo de mercados Spot disponibles en Demo Trading; requiere `X-Agent-Token`.
- `POST /api/demo/binance/spot/orders` — limit order en Demo Trading, con intent persistido e idempotencia por client order ID.
- `GET /api/demo/binance/spot/orders` y `GET /api/demo/binance/spot/orders/{client_order_id}` — estado persistido.
- `POST /api/demo/binance/spot/orders/{client_order_id}/reconcile` — conciliación explícita tras timeout/restart.
- `POST /api/demo/binance/spot/orders/{client_order_id}/fills/reconcile` y `GET /api/demo/binance/spot/orders/{client_order_id}/fills` — conciliación y consulta de fills individuales y comisiones; nuevas órdenes Demo quedan bloqueadas hasta completar la evidencia; requieren `X-Agent-Token`.
- `POST /api/demo/binance/spot/orders/{client_order_id}/cancel` — cancelación de órdenes abiertas/partial fills.
- `GET /api/demo/binance/spot/orders/{client_order_id}/events` — auditoría append-only.
- Un reconciliador periódico consulta órdenes Demo abiertas/unknown y completa el ledger de fills/fees cada `BINANCE_DEMO_RECONCILIATION_INTERVAL_SECONDS` (30 s por defecto). Usa leases persistentes con fencing y backoff, continúa aunque el worker de estrategias esté pausado y nunca reenvía órdenes. Solo limit orders, máximo 25 unidades de quote por defecto y un único intent sin resolver. Demo-only; no hay ejecución en producción. Las nuevas órdenes quedan bloqueadas hasta conciliar también todos los fills y comisiones de órdenes previas.
- `POST /api/accounts/binance/spot/snapshots` — captura y persiste un snapshot inmutable de balances; requiere `X-Agent-Token`.
- `GET /api/accounts/snapshots` y `GET /api/accounts/snapshots/{snapshot_id}` — historial y diferencias entre snapshots; requieren `X-Agent-Token`. Configura `BINANCE_READONLY_API_KEY` y `BINANCE_READONLY_API_SECRET` con permisos de lectura únicamente.
- `GET /api/research/equities?max_instruments=5&max_quote_candidates=10&timeframe=1day` — investigación manual de acciones/ETF; requiere `TWELVE_DATA_API_KEY`, limita las cotizaciones examinadas (1–100) y el histórico descargado (1–10), y nunca ejecuta operaciones. El muestreo de cotizaciones se distribuye por todo el catálogo y la clasificación preliminar prioriza volumen (60%), frescura (25%) y spread (15%); la ausencia de volumen penaliza la posición, pero no elimina por sí sola la investigación histórica.
- `GET /api/markets` — discovery en vivo; persiste el snapshot y devuelve la identidad de catálogo.
- `GET /api/instruments` — catálogo persistido con filtros `provider_id`, `venue_id`, `asset_class`, `market_type`, `active` y paginación.
- `GET /api/instruments/{catalogue_id}` — detalle del instrumento y timestamps first/last seen.
- `GET /api/instrument-discovery/runs` — historial de ejecuciones de discovery y fallos sanitizados.
- `POST /api/instruments/refresh` — refresco explícito autenticado con `X-Agent-Token`; solo consulta metadatos, no ejecuta órdenes.
- `GET /api/risk-profiles` — perfiles de riesgo y estado habilitado/deshabilitado.
- `GET /api/health` — liveness del proceso.\n- `GET /api/health/ready` — comprueba SQLite y heartbeat reciente del worker; es el healthcheck de Docker.
- `GET /api/market/{symbol}/ticker`
- `GET /api/market/{symbol}/ohlcv`
- `GET /api/market/{symbol}/indicators`
- `GET /api/fx/EUR/USD` y `GET /api/fx/USD/EUR` — tipos FX frescos validados; USD/EUR se deriva invirtiendo la misma observación EUR/USD.
- `GET /api/portfolio` — efectivo separado por moneda y equity consolidado en EUR.
- `GET /api/trades`
- `GET /api/agent/withdrawals` — requiere `X-Agent-Token`.
- `POST /api/agent/withdrawals` — solicita retirada paper de cualquier efectivo disponible; se desglosa la parte de beneficio reservado y la parte no reservada.
- `POST /api/agent/withdrawals/{id}/approve` y `/cancel` — aprobación/cancelación manual con token.
- `GET /api/agent/fx-conversions` y `POST /api/agent/fx-conversions` — historial y conversión paper EUR/USD; requieren `X-Agent-Token` y clave idempotente.
- `POST /api/orders` — solo paper; exige `X-Agent-Token` válido (`AGENT_CONTROL_TOKEN`), aprobación del Risk Manager y stop-loss explícito en compras. Admite crypto spot EUR y órdenes manuales de cash equities/ETF USD de Twelve Data; estas últimas requieren efectivo USD previamente convertido, cotización reciente, sesión abierta y slippage paper configurable; si el proveedor entrega bid/ask, se valida el spread y se usa el lado correspondiente.
- `GET /api/agent/strategy-history?limit=100&strategy_id=ema_12_26&instrument_id=...` — historial persistente de validación, benchmark, walk-forward, costes y motivos de rechazo; requiere `X-Agent-Token`.
- `GET /api/agent/status` — requiere `X-Agent-Token`.
- `GET /api/agent/order-intents` — historial de órdenes paper y eventos de auditoría; requiere `X-Agent-Token`.
- `GET /api/agent/backup-status` — estado de copias locales y off-host; requiere `X-Agent-Token`.
- `POST /api/agent/resume`, `/pause`, `/emergency-stop`, `/reset-emergency-stop`.
- `POST /api/agent/tasks` — acepta `health_check`, `market_scan`, `equity_research` (solo lectura) y `decision_cycle`; este último puede ejecutar una orden paper aprobada.

## Supervisión del worker

Configura un secreto largo y aleatorio en `.env` como `AGENT_CONTROL_TOKEN` y ajusta `MAX_STOP_DISTANCE_FRACTION` si procede (por defecto 0,05). Los endpoints de control y el endpoint manual de órdenes paper requieren ese valor en la cabecera `X-Agent-Token`. Si no está configurado, permanecen deshabilitados y devuelven 503. Las compras manuales necesitan stop-loss y pasan por el Risk Manager determinista. En equities, el stop-loss se almacena pero todavía no se ejecuta automáticamente; el worker autónomo sigue limitado a crypto spot EUR.

El worker publica un heartbeat cada ciclo; `/api/health/ready` falla si la base de datos no responde o el heartbeat supera 600 segundos. Docker usa esta comprobación de readiness, no solo la respuesta HTTP del proceso. El worker se inicia pausado. Al reanudarlo explícitamente, programa un `decision_cycle` cada 5 minutos por defecto (`DECISION_CYCLE_INTERVAL_SECONDS`), sin solapar tareas pendientes/en ejecución. Si Twelve Data está configurado, también programa una tarea `equity_research` independiente cada 6 horas (`EQUITY_RESEARCH_INTERVAL_SECONDS`, mínimo 1 hora). Esta tarea muestrea el catálogo, analiza acciones/ETF y persiste el resultado en la memoria/auditoría del agente; nunca genera órdenes. La cuota de Twelve Data se protege con límites persistentes. Cada ciclo puede ejecutar órdenes paper si el Chief genera una propuesta y el Risk Manager la aprueba. También se pueden encolar análisis manuales. Las salidas stop-loss/take-profit se comprueban en cada ciclo, no continuamente entre ciclos. La contabilidad separa P/L realizado y no realizado; la reserva provisional destina el 50% del beneficio neto realizado a retirada y el 50% a reinversión. Las retiradas paper requieren solicitud y aprobación manual y pueden usar cualquier efectivo disponible, incluido principal y beneficio; la contabilidad identifica cuánto procede de la reserva de beneficio y cuánto de capital no reservado. Las solicitudes pendientes reservan efectivo para evitar compromisos duplicados. No se liquidan posiciones automáticamente: si el efectivo no basta, primero habría que autorizar una venta paper controlada. No expongas la API a Internet sin HTTPS, autenticación, restricciones de red, rate limiting y gestión segura de secretos.

## Seguridad y límites

- Paper trading es el único modo de ejecución disponible.
- No se almacenan claves privadas de exchanges ni se solicitan permisos de retirada.
- El adaptador CCXT inicial es exclusivamente de datos públicos; no autentica cuentas ni envía órdenes.
- Los perfiles de margin, futures, options, forex trading y DeFi están deshabilitados. El Risk Manager permite crypto spot EUR y, por separado, órdenes manuales autenticadas de cash equities/ETF USD en paper; no permite que el Chief opere equities.
- No se solicita ni registra ninguna seed phrase o clave privada de wallet. No existe aún infraestructura de firma.
- El worker depende de que el proceso de la aplicación esté desplegado y activo; una conversación de ChatGPT no mantiene el proceso en ejecución.
- Los backtests son evidencia histórica, no una garantía de rentabilidad.
- Twelve Data Basic requiere una API key y limita el consumo a 8 créditos por minuto y 800 al día; el adaptador inicial consulta solo acciones/ETF estadounidenses. No se consulta ni se habilita ningún plan de pago. Los datos gratuitos pueden tener límites, retrasos o restricciones.
- Live trading requiere un módulo separado, autorización explícita, límites independientes, reconciliación con el proveedor y pruebas específicas.
