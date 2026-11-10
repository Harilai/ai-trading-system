# Session handoff — AI Trading System

> **Última revisión del repositorio:** 2026-11-10 16:50 Europe/Madrid
> **Rama base revisada:** `main`
> **Último commit integrado en `main`:** `35b894800ebf24324aebb2f09c363bdda8d1fca8` — documentación de la demo local paper.
> **Estado de ejecución:** paper trading únicamente; Binance Spot Demo aislado; live trading no implementado ni habilitado.

## Fase en curso — ledger común de órdenes y ejecuciones (PR #79 draft)

- PR #78 (`feat/normalized-account-orders`) está abierto y contiene los contratos `AccountOrder` / `AccountOrderFill`; no añade adaptadores ni worker genérico.
- PR #79 (`feat/provider-order-ledger`) está apilado sobre el head de PR #78 `8f91087100d7ef438c1fe6eed6b9e68b732f9634`. Añade un ledger SQLite con historial append-only de observaciones y fills, deduplicación por trade ID y ámbito de cuenta/entorno/mercado, validación de transiciones y cobertura exacta de fills.
- El ledger no envía, cancela ni reintenta órdenes y no se conecta a proveedores. Binance Demo conserva su conciliador separado; producción continúa deshabilitada.
- Tests dirigidos: **27 passed**. Suite completa en Docker aislado con `--network none`: **493 passed, 1 warning** (deprecación Starlette/AnyIO).
- PR #79 ejecutó correctamente `Python tests` y `Docker build and runtime hardening` en el head `68986d10aaa13f32be1a950969d33222dbeedd84` (run 42861461390). Revisión independiente pendiente; no integrar hasta completarla según `docs/AGENT_WORKFLOW.md`.
- PR #74 y PR #77 siguen abiertos pendientes de revisión independiente; verificar sus checks sobre los heads vigentes.
- La demo local paper sigue activa en localhost y no se reinició.
- No se consultaron secretos ni se conectaron fondos, cuentas de producción o servicios de ejecución real.

## Fase completada — Demo local paper (PR #75)

- Compose independiente `docker-compose.demo.yml`: solo API, publicación en `127.0.0.1`, volumen persistente propio, 10.000 EUR virtuales, mercado público Kraken y hardening del contenedor. No lee `.env` de producción ni inicia el servicio de backups.
- Binance read-only, CCXT autenticado, Binance Spot Demo y backups externos quedan forzados desactivados y sin claves. Solo se puede añadir opcionalmente `TWELVE_DATA_API_KEY` para datos de cotizaciones, en `.env.demo` ignorado por Git.
- Arranque documentado: `docker compose -f docker-compose.demo.yml up --build`; dashboard local en `/dashboard`. Las órdenes paper existentes siguen protegidas por Risk Manager y token.
- Añadida validación de configuración del Compose demo a CI. En el SHA `7abd5f527612d0c593e93d8a94852cc29d0a72d8`, `Python tests` pasó con **460 tests** y `Docker build and runtime hardening` pasó, incluida la validación de los Compose base y demo.
- Entorno de trabajo actual no tiene Docker ni intérprete Python disponibles, así que no es posible ejecutar localmente `docker compose config` ni `pytest -q`; ejecutar ambos gates en GitHub Actions.
- Sin credenciales reales, fondos, despliegue ni conexión de ejecución a producción.

## Siguiente acción

Obtener CI verde y revisión independiente para PR #78 y PR #79 sobre sus heads exactos; después, diseñar un adaptador read-only de reconciliación para consumir el ledger, sin alterar Binance Demo ni habilitar producción.

## 1. Cómo retomar en una conversación nueva

Antes de modificar código, leer este documento, `docs/AGENT_WORKFLOW.md`, `docs/ROADMAP.md`, `README.md` y la arquitectura multi-activo. Comprobar siempre el estado vivo de `main`, PR abiertas, CI y commits. El repositorio es la fuente de verdad.

Trabajar en ramas por cambio lógico, seguir los roles y gates de calidad del workflow, añadir pruebas, revisar el diff y exigir CI verde sobre el SHA exacto antes de mergear. Actualizar este handoff y el roadmap al cerrar cada fase.

## 2. Estado integrado y verificado

- **PR #71 — contratos normalizados de cuenta:** merge commit `a7a9bb8487e7935f791cc6034dee8fd10e24ec6d`; registry por `provider_id + account_alias`, capabilities/permisos explícitos, balances normalizados y endpoints autenticados.
- **PR #72 — adaptador genérico CCXT read-only:** merge commit `c44bc9741ecc9856625133b938548192a25f8eb9`; exchange configurable (Kraken por defecto), consulta balances Spot autenticados y no expone operaciones de escritura. El software no verifica los scopes reales de la API key.
- **PR #73 — contrato de posiciones normalizadas:** merge commit `d9fefaf80d3a9dec4f7f5c73b42530917433cdaf`; añade `AccountPosition.normalize`, snapshot inmutable, validación decimal estricta, identidad de instrumento, asset class, market type, side y moneda quote, detección de duplicados y endpoint owner-authenticated `GET /api/accounts/{provider_id}/positions?account_alias=...`.
- **CI PR #73:** los jobs `Python tests` y `Docker build and runtime hardening` terminaron con `success` en el head exacto `253bb828d4d13a10638d64b89d58d56d46890745`. El merge fue squash y el commit resultante es el indicado arriba.
- Los conectores Binance production read-only y CCXT genérico declaran actualmente `positions=false`. Por tanto el endpoint devuelve HTTP 409 para ellos hasta que un adaptador concreto implemente lectura de posiciones.
- No se crean posiciones artificiales convirtiendo balances spot en posiciones. Binance Demo orders/fills tampoco se tratan como posiciones abiertas sin reconciliación/contabilidad específica.
- El contrato de cuenta, balances y posiciones conserva aislamiento entre proveedor, alias de cuenta, entorno y tipo de mercado.
- PR #68 ya incorpora conciliación periódica Demo con leases SQLite, fencing, backoff, recuperación tras reinicio y conciliación de fills; solo lectura y no reenvía órdenes.

## 3. Siguiente fase obligatoria

**Crear el ledger persistente append-only de snapshots de posiciones multi-venue.**

Alcance recomendado:

1. Almacenar snapshots por `provider_id + account_alias + environment + market_type`, con timestamp externo, timestamp de captura, hash del contenido y referencia al snapshot anterior.
2. Almacenar posiciones por identidad estable de instrumento/cuenta, incluyendo cantidad, lado, coste/entry, mark, market value, P&L no realizado y moneda quote/base cuando estén disponibles.
3. Comparar snapshots sin interpretar la ausencia de una posición como liquidación/cantidad cero si el proveedor devolvió un resultado parcial, error o conjunto incompleto. Incorporar estado de cobertura/completitud explícito.
4. Decimal strings exactos, timestamps con zona horaria, validación stale/future, idempotencia, detección de duplicados y triggers append-only.
5. Endpoint autenticado para persistir snapshot solo desde proveedores con capability `positions=true`; endpoints de historial/detalle y diffs por cuenta.
6. No mezclar los snapshots de cuentas externas con la cartera paper; no convertir balances en posiciones ni estimar precios/P&L sin evidencia con fuente y timestamp.
7. Añadir pruebas de concurrencia, duplicados, rollback, orden temporal, snapshots parciales, posición añadida/ausente/cambiada y separación de cuentas/entornos.

Después del ledger, implementar adaptadores específicos de posiciones para exchanges/brokers que ofrezcan API de posiciones. CCXT general Spot balance-only debe seguir declarando `positions=false` salvo que se pruebe un método compatible con el mercado correspondiente.

## 4. Límites y requisitos de seguridad

- Paper trading continúa siendo el único modo disponible para operaciones generales.
- Binance Spot Demo solo usa el entorno virtual demo, limit orders y controles fail-closed; no hay fallback a producción.
- Binance Spot producción y CCXT genérico son solo lectura de balances. El software no verifica la configuración real de permisos de API keys (`permissions_verified=false`).
- No solicitar ni almacenar en Git/chat/frontend/logs API secrets, claves privadas ni seed phrases.
- No dar permisos de retirada o transferencia a claves de trading.
- No activar live execution por configuración accidental, agente o dashboard. Cualquier futura ejecución real exige diseño aislado, autorización explícita, límites por instrumento/cuenta, sandbox, conciliación, pruebas adversas, auditoría y revisión independiente.
- No habilitar margen, futuros, opciones, forex trading, DEX o DeFi solo por aparecer en catálogo.
- Un instrumento solo podrá valorarse/negociarse si hay identidad estable, datos frescos, divisa, costes, reglas de mercado, elegibilidad y riesgo compatibles.
- Una fuente desconocida, obsoleta o contradictoria debe producir estado incompleto/bloqueado, no un cero ni una posición inventada.

## 5. Operativa pendiente del propietario

- Configurar credenciales Binance Spot Demo únicamente en el servidor y ejecutar `docs/BINANCE_DEMO_SMOKE_TEST_RUNBOOK.md`; todavía no hay evidencia de una smoke test contra la cuenta Demo real.
- Configurar credenciales read-only de Binance/CCXT en el servidor, con privilegios mínimos y sin trading, transferencias ni retiradas.
- Desplegar el VPS real, configurar backup S3-compatible y realizar ejercicios de restauración. No afirmar disponibilidad 24/7 hasta medirla.

## 6. Referencias

- PR #71: https://github.com/Harilai/ai-trading-system/pull/71
- PR #72: https://github.com/Harilai/ai-trading-system/pull/72
- PR #73: https://github.com/Harilai/ai-trading-system/pull/73
- Arquitectura multiactivo: `docs/MARKET_EXPANSION_ARCHITECTURE.md`
- Contratos de cuenta: `docs/ACCOUNT_PROVIDER_CONTRACTS.md`
- Contrato de instrumentos: `docs/INSTRUMENT_CONTRACT_V2.md`
- Workflow de agentes: `docs/AGENT_WORKFLOW.md`

**Regla de continuidad:** la siguiente conversación debe comenzar comprobando el estado vivo de GitHub y continuar el ledger persistente de snapshots de posiciones, no reimplementar PR #71–#73.
