# Session handoff — AI Trading System

> **Purpose:** permitir que una nueva conversación retome el desarrollo sin depender del historial del chat.
>
> **Última revisión del repositorio:** 2026-11-10  
> **Rama base revisada:** `main`  
> **Último commit observado en `main`:** `c44bc9741ecc9856625133b938548192a25f8eb9` — merge de PR #72, adaptador genérico CCXT read-only para cuentas CEX Spot.  
> **Estado de ejecución:** paper trading únicamente; live trading no implementado y deshabilitado.

## 1. Cómo retomar en una conversación nueva

Antes de modificar código:

1. Leer este documento (`docs/SESSION_HANDOFF.md`) y el método de trabajo obligatorio (`docs/AGENT_WORKFLOW.md`).
2. Leer `docs/ROADMAP.md` y las restricciones de seguridad de `README.md`.
3. Consultar el estado actual de `main`, los últimos commits y las PR abiertas. Este documento es una guía de continuidad, no sustituye la comprobación del repositorio vivo.
4. Comparar el HEAD actual con el commit indicado arriba. Si `main` ha avanzado, revisar qué se integró y actualizar este handoff antes de continuar.
5. Continuar por **la siguiente fase pendiente** de este documento. No repetir tareas marcadas como integradas.
6. Aplicar los roles y las puertas de calidad descritos en `docs/AGENT_WORKFLOW.md`: planificación, arquitectura, investigación cuantitativa, desarrollo, riesgo/seguridad, QA, revisión independiente y release/documentación.
7. Trabajar en una rama por cambio lógico, añadir pruebas, revisar el diff y exigir CI verde antes de fusionar.

No asumir que una conversación anterior dejó cambios sin subir. El repositorio y sus PR son la fuente de verdad.

La forma de trabajo no es opcional ni depende del chat: `docs/AGENT_WORKFLOW.md` define los roles especializados, las fases, los criterios de revisión y el protocolo de cierre.

## 2. Trabajo ya completado que motivó esta continuación

- **PR #17 — Walk-forward y benchmark como gates obligatorios:** fusionada en `main` (merge commit `482b14b0b0f0af183003c49cb8d51d7e86ff6445`). Exige evidencia en tres ventanas walk-forward, operaciones cerradas por ventana, ventanas positivas, retorno medio positivo, drawdown limitado y superar buy-and-hold neto de costes. Si falta evidencia, la estrategia no se promociona.
- **PR #18 — Validación de indicadores técnicos:** fusionada en `main` (merge commit `5f2f96ba77941c28ddda7787dba29ee4fa5a39fb`). Endurece validaciones de NaN/infinito, periodos y coherencia OHLC del ATR, y añade pruebas con series numéricas de referencia.
- Después se integraron mejoras posteriores hasta la PR #38: historial/versionado de investigación, estrés de costes, diagnóstico de régimen, Sniper, cálculo de riesgo round-trip, robustez del descubrimiento de mercados, validación de entradas finitas, backups SQLite verificados y programados, y dashboard responsive.
- La PR #27 de backups quedó cerrada sin merge porque divergió de `main`; su funcionalidad fue sustituida por las PR #36 y #37, que sí están fusionadas.

## 3. Estado funcional conocido

- Backend FastAPI y cartera paper persistida en SQLite.
- Datos públicos de crypto spot mediante CCXT; Kraken es el proveedor inicial por defecto.
- Twelve Data Basic opcional para datos/FX de acciones y ETF estadounidenses; el Chief mantiene investigación read-only y las órdenes equity solo son manuales paper.
- Scout, Charts, Strategy Research, Chief, Risk Manager y Sniper integrados.
- Backtesting cronológico 70/30, benchmark buy-and-hold, walk-forward y sensibilidad de costes.
- Worker persistente con cola durable, leases con token de fencing, heartbeat, reintentos acotados y ciclos periódicos.
- Ciclo de vida paper de órdenes con intentos risk-approved persistidos, fills atómicos, eventos append-only, reconciliación contra el ledger y bloqueo fail-closed ante discrepancias.
- Órdenes paper con comisión, slippage, stop-loss/take-profit e idempotencia por `client_order_id`.
- Dashboard, health/readiness, controles autenticados y backups SQLite.
- No hay ejecución en producción/live, wallets, firma blockchain, broker de acciones, derivados ni DeFi. Binance Spot Demo permite únicamente órdenes limit en el entorno virtual; el conector Binance de producción sigue siendo read-only.

## 4. Fases completadas: cartera multidivisa, conciliación y ejecución Demo

**Completada en PR #41.** La cartera paper mantiene efectivo y posiciones EUR/USD, permite conversiones explícitas y valora acciones/ETF estadounidenses en USD dentro de una cartera cuya divisa base continúa siendo EUR. La investigación sigue siendo read-only para el Chief: las órdenes equity son manuales y paper-only.

### Binance Spot Demo — PR #68: reconciliación periódica

- PR: https://github.com/Harilai/ai-trading-system/pull/68
- Merge commit: `8943fdec94104d881d138d8d8d6da0bfd7cd4518`.
- Head validado: `a5d126395efbc7f2441343be258942422832f96b`.
- CI: 420 pruebas Python correctas; Compose, build Docker y ejecución non-root correctos.
- Polling de intents interrumpidos, órdenes open/partially-filled y terminales con ledger incompleto.
- Leases persistentes, fencing de escrituras, eventos append-only, backoff y lotes acotados.
- Se ejecuta aunque la automatización de estrategias esté pausada o en emergency stop.
- Consulta por clientOrderId; no crea, reenvía, cancela ni modifica órdenes.
- Órdenes acknowledged ausentes o estados contradictorios se ponen en cuarentena; fill/fee incompleto bloquea nuevas órdenes Demo.

### Binance Spot Demo — PR #66: ledger individual de fills y comisiones

- PR: https://github.com/Harilai/ai-trading-system/pull/66
- Merge commit: `0f667db605da2baaf84d47f498fa85fd620c495a`.
- Head validado: `6c12158183cd8c7738dacded8b6679dc11bd0edc`.
- CI: 410 pruebas Python correctas; Compose, build Docker y ejecución non-root correctos.
- Tabla append-only por trade ID + símbolo: order ID, símbolo, side, cantidad, precio, coste quote, comisión, activo y timestamp.
- Reconciliación autenticada por order ID con idempotencia y rechazo de identidades/evidencias contradictorias.
- Cada fill exige comisión identificada; se bloquean nuevas órdenes Demo hasta que el total de fills individuales cubra el acumulado de la orden y todas las comisiones estén presentes.
- Endpoints: `POST /api/demo/binance/spot/orders/{client_order_id}/fills/reconcile` y `GET /api/demo/binance/spot/orders/{client_order_id}/fills`.
- No es todavía un ledger de contabilidad de producción ni se mezcla con balances reales o la cartera paper.

### Decisiones de arquitectura y límites

1. EUR seguirá siendo la divisa base de reporting y la moneda del capital inicial configurable (20 EUR por defecto).
2. El efectivo se mantendrá por divisa. El saldo USD no se presumirá ni se creará automáticamente al comprar un activo cotizado en USD.
3. La conversión EUR/USD será una operación paper explícita, auditada e idempotente, con importe origen/destino, tipo de cambio, timestamp, proveedor, coste/spread simulado y referencia de cotización.
4. El tipo FX se obtendrá de Twelve Data mediante `/exchange_rate` cuando esté configurado. Debe respetar el limitador de cuota ya existente. No se usarán tipos hardcodeados ni tasas stale/futuras/malformadas. Sin FX verificable, se bloquea la conversión; la valoración queda incompleta y Chief/API bloquean nuevas órdenes hasta recuperar una valoración fiable.
5. Cada posición conservará precio/coste base en su divisa de cotización. La cartera calculará equity, exposición y P/L agregado en EUR usando tipos FX identificados y timestamped; la valoración se marcará incompleta si falta cualquier cotización o conversión requerida.
6. La ruta de acciones/ETF permanecerá paper-only y limitada a instrumentos cash equity/ETF de EE. UU. identificados por el proveedor Twelve Data. No se habilitarán brokers ni ejecución real.
7. Risk Manager deberá validar clase de activo, tipo de mercado, divisa, efectivo disponible en la divisa de cotización, exposición y tamaño expresados de forma consistente en EUR. Los límites de crypto spot actuales no se ampliarán por defecto a equities.
8. Retiradas paper existentes seguirán limitadas a efectivo EUR hasta que exista un diseño separado de retirada multidivisa. No se convertirá USD de forma implícita para financiar retiradas.

### Resultado integrado — PR #41

- PR: https://github.com/Harilai/ai-trading-system/pull/41
- Merge commit: `23cfcb597c78060d37d190ebff9820c5c24f3716`.
- Rama de trabajo: `phase/multicurrency-paper-portfolio`.
- CI sobre el head final antes del merge: 270 pruebas correctas; validación Compose, build de imagen y ejecución non-root correctas.
- Ledger EUR/USD, migración aditiva desde `account.cash`, conversiones paper idempotentes y auditadas, FX Twelve Data con caché/cuota y validación estricta.
- Posiciones USD con coste medio nativo y EUR; fills con tasa/proveedor/timestamp FX; P/L y equity consolidados en EUR. Los costes de conversión reducen el P/L realizado.
- Órdenes manuales paper para cash equities/ETF USD con efectivo preconvertido, cotización fresca, sesión abierta, control de spread cuando hay bid/ask, slippage simulado, idempotencia y Risk Manager.
- Perfil equity separado: máximo 10% por orden, 20% de concentración por instrumento, 5% de distancia máxima de stop, comisión/slippage/spread y mínimo configurables.
- Chief/decision cycle siguen limitados a crypto spot EUR; la valoración del worker sí incorpora FX y marks USD frescos cuando hay efectivo/posiciones USD. Valoración incompleta bloquea nuevas órdenes.
- Dashboard muestra saldos por divisa y monedas nativas.
- **Limitación:** stop-loss/take-profit equity se guardan, pero todavía no los monitoriza/ejecuta el worker. Las salidas equity son manuales hasta implementar esa supervisión en la siguiente fase.
- No hay broker, ejecución real, wallets, firma, margin, derivados ni retiradas USD.

### Implementación entregada

1. [x] Añadir contrato y adaptador FX Twelve Data, incluyendo caché/cuotas, validación de tasa positiva/finita, timestamp y antigüedad máxima.
2. [x] Evolucionar SQLite con saldos por divisa, registro de conversiones y metadatos FX en trades/posiciones; conservar migración segura de bases existentes.
3. [x] Añadir conversión paper explícita EUR/USD con coste configurable, control de fondos, idempotencia y auditoría.
4. [x] Añadir valoración multidivisa: efectivo, posiciones, equity, exposición y P/L en EUR, con desglose original por moneda y estado de valoración completa/incompleta.
5. [x] Extender contratos de instrumentos y Risk Manager para permitir únicamente cash equities/ETFs USD de Twelve Data en paper, manteniendo bloqueados otros activos, mercados y proveedores sin soporte.
6. [x] Mantener la investigación read-only para el Chief; permitir solo órdenes manuales paper tras los gates de cartera, FX, sesión bursátil, costes y riesgo. No activar órdenes autónomas de equities.
7. [x] Añadir pruebas de migración, FX stale/futuro/malformado, falta de cuota, conversiones duplicadas, saldo insuficiente, movimientos entre divisas, valoración parcial, cambios de FX, comisiones y regresión crypto EUR.
8. [x] PR #44 completa el ciclo de vida y conciliación paper; PR #48 completa backups externos y PR #63 completa el lifecycle de órdenes Binance Spot Demo.

### Criterios de aceptación

- Las bases existentes migran sin alterar el saldo EUR ni las posiciones crypto actuales.
- EUR y USD se contabilizan por separado; ninguna compra gasta una divisa distinta de la cotizada sin conversión paper explícita.
- Toda conversión conserva evidencia reproducible de tipo, proveedor, timestamp, costes e importes.
- Equity y exposición se reportan en EUR sin ocultar los saldos/valores originales en USD.
- Una cotización FX ausente, stale, futura, no finita o no positiva bloquea conversiones y produce valoración incompleta; nunca se inventa un tipo.
- El Risk Manager sigue siendo obligatorio y no permite shorting, margin, derivados, broker live ni wallets.
- La suite completa y CI deben pasar en el SHA exacto de la PR antes del merge.

### Resultado integrado — PR #44: ciclo de vida y conciliación paper

- PR: https://github.com/Harilai/ai-trading-system/pull/44
- Merge commit: `df760119580b56a345d0e7c9ecdc60b45d2365dd`.
- Head validado: `0c20359375af24be79c5ebd008c8ca9fa32e84d1`.
- CI: 292 pruebas Python correctas; Compose, build Docker y ejecución non-root correctos.
- Intentos risk-approved persistidos con request/decision inmutables; fills y cambios de ledger atómicos.
- Reconciliación de intentos frente a trades, verificación del payload completo, eventos append-only y estado `reconciliation_required`.
- Recuperación tras restart con market/FX freshness, backoff y presupuesto finito; fencing por lease token y heartbeat.
- Nuevas compras bloqueadas mientras exista un intento sin resolver; las ventas de reducción de riesgo siguen permitidas.
- API owner-authenticated `/api/agent/order-intents` para consultar intents y eventos.

### Resultado integrado — PR #48: copias externas verificadas

- PR: https://github.com/Harilai/ai-trading-system/pull/48
- Merge commit: `ed08cec879662facbcbc0c5c2c2eb26058441133`.
- Head validado: `2e5b251b666df8faccf7857597bd50bc284be22d`.
- CI: 304 pruebas Python correctas; Compose, build Docker y ejecución non-root correctos.
- Replicación opcional de las dos bases SQLite a S3-compatible, con objetos content-addressed, HTTPS, cifrado server-side y verificación SHA-256 mediante read-back.
- CLI `app.backup_remote list/download`; las descargas se validan con SHA-256 e integridad SQLite y no sobrescriben candidatos existentes.
- API owner-authenticated `/api/agent/backup-status`.
- Credenciales S3 aisladas en `.env.backup`, cargadas solo en el contenedor backup; el API no las recibe.
- Retención local conserva snapshots cuyo off-host copy no está verificado. No hay borrado remoto desde la aplicación.
- **Importante:** la función off-host está desactivada por defecto. Debe configurarse el proveedor y crear `.env.backup` a partir de `.env.backup.example`.

### Fase dashboard — PR #50

- PR: https://github.com/Harilai/ai-trading-system/pull/50
- Merge commit: `848d1d375bedbd463f6433b42a47ea9d94c39733`.
- Head validado: `3c993a79e4448a909cdc310e5a6245914cfe9ba6`.
- CI: 305 pruebas Python correctas; Compose, build Docker y ejecución non-root correctos.
- Rama: `feature/dashboard-operations-v2`.
- Añade gráfico nativo de velas OHLC, selector de símbolo/proveedor/intervalo y divisa de cotización correcta.
- Muestra capabilities declaradas y readiness API/worker; no equipara descubrimiento de instrumentos con cuenta autenticada o trading habilitado.
- Añade consulta manual de investigación equities/ETF y lectura de historial de estrategias.
- Muestra intents de órdenes paper, estados de conciliación y operaciones recientes.
- Muestra el estado de backups locales/off-host, snapshots pendientes de verificación y timestamps de comprobación remota.
- Añade solicitudes y aprobación/cancelación de retiradas paper. Son cambios en el ledger simulado; no hay transferencia bancaria, exchange ni wallet.
- El token se mantiene en memoria JavaScript, se borra del campo al validarse y las respuestas autenticadas usan `cache: no-store`.
- Todo el contenido recibido de API se representa mediante text nodes; no se inserta como HTML.
- La validación se ejecutó en el HEAD exacto indicado arriba antes del merge.

### Fase completada — contrato normalizado de instrumentos v2 (PR #52)

- PR: https://github.com/Harilai/ai-trading-system/pull/52
- Merge commit: `1d6ad47d36927f5ec77d4eb0ffac5132e744a68a`.
- Head validado: `f2a1e765671dce0e8ef242915065f2425a6f205a`.
- CI: 312 pruebas Python correctas; Compose, build Docker y ejecución non-root correctos.
- Extiende `MarketInstrument` con venue/native symbol, precision mode, price tick, amount step, precision digits, min/max amount/cost, contract type/size, settlement currency, expiry, strike, option right, underlying, trading status y session timezone.
- Valida números finitos, tick/step/multiplicador positivos, límites coherentes y metadatos mínimos para opciones.
- CCXT mapea precision mode correctamente: tick-size no se confunde con decimal places/significant digits.
- Twelve Data conserva MIC/exchange y native symbol para equities/ETF; no inventa lot size, tick, settlement ni capacidades de trading.
- `/api/markets` expone los campos normalizados.
- Especificación: `docs/INSTRUMENT_CONTRACT_V2.md`.
- **No habilita** futuros, opciones, margin, authenticated CEX trading, broker execution, wallets ni live trading. Los registros de derivados son metadatos, no un adaptador implementado.
- Los campos nuevos todavía no están conectados a la validación de órdenes existente; la ejecución paper mantiene el alcance previo.

### Fase completada — catálogo persistente de instrumentos (PR #54)

- Rama: `feature/persistent-instrument-catalog`.
- Merge commit: `b9dbba57ffcf5c908f318d3962f93407e94cb14f`.
- Head validado: `22243c18500fe055f3725e591af649e261452706`.
- CI: 323 pruebas Python correctas; Docker Compose, build API y ejecución non-root correctos en el mismo SHA.
- Añade tablas SQLite `instrument_catalog` e `instrument_discovery_runs` en la base persistente existente.
- Mantiene `instrument_id = provider_id:market_type:symbol` sin cambios y añade `catalogue_id` determinista venue-aware.
- Persiste payload normalizado, first/last seen, estado y metadatos; conserva registros ante discovery parcial o fallido.
- Registra runs, contadores y fallos sanitizados; redacción recursiva de claves sensibles.
- `GET /api/markets` persiste snapshots y devuelve `catalogue_sync` y `catalogue_id`.
- API: `GET /api/instruments`, detalle `/api/instruments/{catalogue_id}`, `GET /api/instrument-discovery/runs` y refresh autenticado `POST /api/instruments/refresh`.
- Diseño: `docs/INSTRUMENT_CATALOG_DESIGN.md`.
- Solo discovery de metadatos: no añade ejecución, autenticación privada, derivados, wallets ni live trading.

### Fase completada — preparación operativa 24/7 (PR #56)

- Merge commit: `c892ff35b292515624629d783becc17a9ff60f1b`.
- 330 pruebas Python correctas; Compose, build API y ejecución non-root correctos.
- Healthcheck de backups; reintentos cortos ante fallos locales/off-host; rotación de logs.
- Runbook `docs/VPS_DEPLOYMENT_RUNBOOK.md` y unidad systemd `deploy/ai-trading-compose.service`.
- No se ha aprovisionado ni desplegado todavía un VPS.

### Fase completada — Binance Spot read-only (PR #57)

- Merge commit: `8773d75313aa225a2cd7f0789024c66ab6c2613b`.
- Head validado: `b1b7b8a07490ba401ace8908a80b40e87be2bf09`.
- 345 pruebas Python correctas; Compose, build API y ejecución non-root correctos.
- Endpoints owner-authenticated: `GET /api/accounts` y `GET /api/accounts/binance/spot/balances`.
- Solo usa `fetch_balance()`; balances devueltos como strings decimales; secretos `SecretStr`; errores externos sanitizados.
- El conector no verifica permisos reales de la API key (`account_key_permissions_verified=false`); el propietario debe crear una clave solo lectura, sin trading, transferencias ni retiradas.
- No hay órdenes reales, ni sincronización con la cartera paper, ni claves configuradas en el VPS.

### Fase completada — snapshots y conciliación read-only de cuentas (PR #59)

- Merge commit: `b3cca3d4ff60c134d62d90571284c469239ec403`.
- Head validado: `961693fccfdd5bf76a61eadf8a2914fe76b499c8`.
- CI: 360 pruebas Python correctas; Compose, build API y ejecución non-root correctos.
- Tablas append-only `account_snapshots` y `account_snapshot_balances`, separadas de la cartera paper.
- Captura owner-authenticated con `POST /api/accounts/binance/spot/snapshots`; historial y detalle en `GET /api/accounts/snapshots` y `GET /api/accounts/snapshots/{snapshot_id}`.
- Comparación por cuenta y venue: `initial`, `unchanged`, `changed`; assets nuevos y ausentes se señalan sin asumir que los ausentes equivalen a cero.
- Importes almacenados como decimal strings canónicos; rechaza floats, cantidades inválidas, snapshots stale/future/out-of-order y fuentes que no declaren read-only.
- No hay FX valuation, P&L, transfer detection, ni sincronización con la cartera paper.

### Fase completada — Binance Spot Demo Trading (PR #61)

- Merge commit: `6fb6350fb904dd1df7d27cd86f02637a7fb49cf8`.
- Head validado: `7eb2764f4f31e129106e79cba040f08945e2cdc0`.
- CI: 372 pruebas Python correctas; Compose, build API y ejecución non-root correctos.
- Usa CCXT `enable_demo_trading(True)`; no usa el antiguo `set_sandbox_mode(True)`.
- Comprueba que las rutas REST Spot públicas/privadas usan `demo-api.binance.com` y falla si detecta `api.binance.com` de producción.
- Variables separadas: `BINANCE_SPOT_DEMO_ENABLED`, `BINANCE_SPOT_DEMO_API_KEY`, `BINANCE_SPOT_DEMO_API_SECRET`; desactivado por defecto.
- Endpoints owner-authenticated: `GET /api/demo/binance/spot/balances` y `GET /api/demo/binance/spot/markets`.
- El conector base Demo fue ampliado por PR #63 con limit orders, consulta, cancelación y conciliación persistente. No expone órdenes market, transferencias, retiradas ni wallet signing.
- Claves Demo separadas de las de producción; no están configuradas en el entorno operativo.

### Fase completada — ciclo de vida de órdenes Binance Spot Demo (PR #63)

- PR: https://github.com/Harilai/ai-trading-system/pull/63
- Merge commit: `013f44f7be1da1a8c540bf01722be91e05560efe`.
- Head validado: `0ad80bcd0ca98175cf60891766304057fc38961d`.
- CI: 400 pruebas Python correctas; Compose, build de imagen API y verificación non-root correctos.
- Adaptador de ejecución aislado en CCXT Demo Trading; verificación del host demo, sin fallback a producción.
- Limit orders Spot solamente; inputs decimales exactos, mercado activo, precisión/ticks y límites min/max de cantidad/coste.
- Cap por orden de 25 unidades de la moneda quote por defecto, configurable hasta 1000; allowlist inicial USDT/USDC/FDUSD/TUSD/EUR.
- Intentos inmutables persistidos antes de la llamada externa; client order IDs estables; timeout/crash nunca provocan reenvío ciego.
- Estados open/partial/filled/cancelled/expired/rejected/submission_unknown/reconciliation_required con eventos append-only.
- Conciliación valida client ID, exchange ID, símbolo, lado, importe solicitado y cantidades acumuladas; los fills no pueden retroceder y el remaining no puede aumentar.
- Solo un intent sin resolver a la vez. Las discrepancias bloquean nuevas órdenes y conservan el último snapshot conocido como válido.
- Endpoints owner-authenticated para crear, listar, consultar, reconciliar, cancelar y auditar órdenes Demo.
- **No hay ejecución real:** balances Demo virtuales separados de Binance real y de la cartera paper; no market orders, transfers, withdrawals ni wallet signing.

## 5. Objetivo de producto y siguientes fases

El objetivo final no es un bot exclusivamente crypto. Debe evolucionar a una plataforma multiactivo y multi-venue preparada para capital real: conectar exchanges como Binance, brokers y wallets, y descubrir/operar los instrumentos que cada proveedor y cuenta permita. El universo objetivo incluye crypto CEX/DEX, acciones, ETF, FX, futuros, opciones, bonos, materias primas y otros mercados integrables. La disponibilidad depende del proveedor, país, cuenta, API, liquidez y elegibilidad. Descubrir un símbolo no habilita su trading.

La arquitectura de referencia está en `docs/MARKET_EXPANSION_ARCHITECTURE.md`.

Siguiente secuencia:
1. **Completado — backups externos y restauración (PR #48):** replicación S3-compatible verificada, status API y descarga/validación de restore candidate. Falta configurar el proveedor real en el entorno operativo.
2. **Completado en PR #50 — dashboard ampliado:** velas OHLC, salud de conectores/readiness, investigación, historial de estrategias, intents/fills paper, backup status y retiradas paper.
3. **Completado — contrato normalizado de instrumentos v2 (PR #52)** y **catálogo persistente venue-aware (PR #54)**.
4. **Completado — preparación operativa 24/7 (PR #56):** healthcheck, reintentos, rotación de logs, runbook VPS y unidad systemd. El VPS real todavía no está desplegado.
5. **Completado — Binance Spot read-only y snapshots (PR #57/#59):** balances reales en modo lectura e historial/conciliación inmutable, separados de la cartera paper.
6. **Completado — Binance Spot Demo connector y lifecycle de órdenes (PR #61/#63):** limit orders, idempotencia, reconciliación, cancelación y fail-closed. No existe ruta de órdenes a producción.
7. **Siguiente — smoke tests operativos con una cuenta Binance Demo real:** seguir `docs/BINANCE_DEMO_SMOKE_TEST_RUNBOOK.md`; configurar las credenciales Demo únicamente en el servidor y verificar respuestas reales, filtros de mercado, precisión, partial fills, cancelación, timeout y reconciliación tras restart. No compartir claves en el chat ni guardarlas en Git. No usar claves de producción. Esta comprobación requiere una cuenta Demo y acceso al servidor del propietario; no se simula como si se hubiera ejecutado.
8. **Completado — ledger individual de fills/fees (PR #66):** persistencia append-only, IDs de trade, cantidades, precios, costes, comisiones/activos y reconciliación idempotente por order ID. Nuevas órdenes Demo se bloquean mientras falte cobertura de fills o comisiones.
9. **Completado — reconciliación periódica Binance Demo (PR #68):** worker con leases SQLite y fencing, backoff, polling por clientOrderId y conciliación de fills; corre aunque la estrategia esté pausada y nunca reenvía órdenes.
10. **Completado — PR #71, contratos normalizados de account providers:** `AccountProviderRegistry` por `provider_id + account_alias`, balances normalizados, capacidades/permisos explícitos y endpoints genéricos autenticados.
11. **Completado — PR #72, adaptador CCXT read-only:** CEX Spot configurable por exchange ID (Kraken por defecto), API key/secret, sin métodos de escritura y con permisos reales de la key no verificados.
12. **En curso — PR #73, snapshots de posiciones y ledger multi-market:** almacenamiento append-only de posiciones por instrumento, market type y entorno, con deltas exactos; todavía no hay un broker adapter conectado que publique posiciones.
13. Añadir adaptadores específicos de broker/posiciones y supervisión paper de stops/take-profit equity antes de decisiones autónomas o live.
14. Completar despliegue persistente 24/7 en VPS y configurar el proveedor externo de backups en el entorno operativo.
15. Evaluar live limitado por venue/clase solo tras smoke tests, sandbox, conciliación, pruebas adversas, revisión de seguridad y habilitación explícita del propietario.

### PR #68 — conciliación periódica Binance Spot Demo

- PR: https://github.com/Harilai/ai-trading-system/pull/68
- Head validado: `a5d126395efbc7f2441343be258942422832f96b`.
- 420 pruebas Python correctas; Compose, build Docker y ejecución non-root correctos.
- Lease SQLite durable con fencing, polling acotado, backoff y recuperación tras restart.
- Reconciliación de estados y fills sin reenvío ciego; funciona aunque la estrategia esté pausada o en emergency stop.

### PR #71 — contratos normalizados de cuentas

- PR: https://github.com/Harilai/ai-trading-system/pull/71.
- Merge commit: `a7a9bb8487e7935f791cc6034dee8fd10e24ec6d`.
- Head validado: `aa8b8673bec7f6bab8eb7dab25107d8b7ccd525e`.
- CI: **433 pruebas Python correctas**; Compose, build Docker y ejecución non-root correctos.
- Registry por `provider_id + account_alias`; Binance production read-only y Binance Demo permanecen como cuentas separadas.
- Contratos para capacidades/permisos, balances, posiciones y órdenes; endpoints genéricos para balances y snapshots read-only.
- No añade broker genérico ni ejecución live.

### PR #72 — adaptador genérico CCXT read-only

- PR: https://github.com/Harilai/ai-trading-system/pull/72.
- Merge commit: `c44bc9741ecc9856625133b938548192a25f8eb9`.
- Head validado: `5839ba303af343134b2c235a446d092db6fd6560`.
- CI: **446 pruebas Python correctas**; Compose, build Docker y ejecución non-root correctos.
- Solo invoca `fetch_balance()` Spot; no contiene submit/cancel/transfer/withdraw/signing.
- Kraken es el exchange por defecto, opcional por credenciales; scopes reales no verificados por software.

### PR #73 — ledger de posiciones multi-market (en revisión)

- Rama: `phase/account-position-snapshots`.
- Amplía snapshots append-only con posiciones, environment, venue, permisos, quantities, prices, market value y unrealized P&L.
- Detecta posiciones añadidas, modificadas y ausentes por instrumento/side; mantiene cuentas, market types y entornos separados.
- El registry admite providers position-only y snapshots combinados; la API genérica expone posiciones y snapshots read-only.
- No incorpora todavía un broker adapter real ni cambia la cartera paper.

## 6. Límites que no se deben saltar

- Mantener paper trading como único modo operativo en producción. Binance Spot Demo es un entorno virtual independiente, limitado a órdenes limit y con cap por orden.
- Live trading no puede activarse desde agentes, LLM, dashboard ni configuración accidental.
- No solicitar ni guardar seeds, claves privadas ni credenciales en Git, logs o frontend.
- No dar permisos de retirada a claves de trading.
- Solo crypto spot EUR está habilitado para decisiones autónomas. Las órdenes cash equities/ETF USD son manuales y paper-only, requieren autenticación owner y conversión explícita; no habilitan equities en Chief.
- El stop-loss/take-profit de equities queda guardado como metadato y no está monitorizado por el worker todavía; las salidas deben ser manuales hasta la fase de conciliación/supervisión.
- Datos stale, inconsistentes o conciliación incierta deben bloquear nuevas entradas.
- No prometer rentabilidad; backtests y paper trading no prueban rentabilidad futura.

## 7. Actualización obligatoria al cerrar cada sesión

Al terminar una fase, actualizar este documento en el mismo PR o en un PR documental asociado:

- Fecha y SHA de `main` revisado.
- PR/commit integrado y resumen verificable.
- Pruebas ejecutadas y resultado de CI.
- Qué queda pendiente, con el siguiente paso concreto.
- Riesgos conocidos o decisiones que una nueva conversación debe respetar.

**Regla de continuidad:** una nueva conversación debe poder empezar leyendo el repositorio y continuar el siguiente paso sin pedir al usuario que reconstruya el contexto del chat.
