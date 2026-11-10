# Session handoff — AI Trading System

> **Última revisión del repositorio:** 2026-11-10
> **Rama base revisada:** `main`
> **Último feature merge:** `a9378266626aca81be46f854f8d11011ee4804ca` — PR #83, experimental paper demo.
> **Estado de ejecución:** paper trading únicamente; demo local aislada con 10.000 EUR virtuales; Binance Spot Demo no se usa en esta demo; live trading no implementado ni habilitado.

## Fase completada — timestamps Kraken (PR #81)

- Merge commit `5bbb8800dfab86405bb798f51e749609923ca852`.
- Las cotizaciones Kraken sin timestamp del exchange usan hora local de recepción `retrieved_at`, nunca etiquetada como hora de evento.
- La procedencia se conserva hasta el ledger paper; se rechazan observaciones stale/future o sin evidencia de recepción.
- CI y revisión independiente pasaron sobre el head exacto `4862851717ad414777f5b160b48417aaf3e75a5d`.
- Se añadieron pruebas para persistencia de procedencia, replay idempotente y migración legacy.

## Fase completada — Independent Reviewer Worker (PR #82)

- Merge commit `ede62ebfd64dc9364b2465cc70d932226d912e17`.
- Añadido `docs/agents/INDEPENDENT_REVIEWER.md` y actualizado `docs/AGENT_WORKFLOW.md`.
- Cada PR requiere una pasada independiente del diff, veredicto, severidad de hallazgos y CI verde sobre el SHA exacto.
- Los merges rutinarios están delegados al Engineering Lead y no requieren confirmación del usuario si todos los gates pasan.
- No existe un segundo modelo persistente en este runtime: si no hay runtime de subagentes, el Orchestrator debe invocar explícitamente el rol de reviewer y no afirmar que un proceso en background está activo.

## Fase completada — demo paper experimental (PR #83)

- Merge commit `a9378266626aca81be46f854f8d11011ee4804ca`.
- El Compose local mantiene 10.000 EUR virtuales, base SQLite/volumen separado y datos públicos Kraken.
- Añadido modo experimental paper-only, activado solo por `PAPER_DEMO_MODE=true` y `PAPER_EXPERIMENTAL_STRATEGIES_ENABLED=true`; ambos default-off fuera del Compose demo.
- Las estrategias experimentales deben tener tres ventanas walk-forward completas, historia suficiente y drawdown OOS/walk-forward no superior al 25%, pero **no se consideran rentables ni promocionadas**.
- Cada entrada experimental se etiqueta con `experimental:`; el dashboard muestra el modo y la columna de estrategia.
- Límites demo: 0,25% de equity como presupuesto de riesgo por operación, 5% de notional máximo por orden y 3% de stop máximo. Risk Manager, frescura, idempotencia y ledger paper siguen obligatorios.
- CI en el head exacto `ea9dfa7982cfec49871d3f2815adeedcb74a6b44`: **470 tests passed, 1 warning**; Docker Compose validation, build y non-root runtime correctos.
- No se activa Binance Demo, broker, wallet, transferencias, retiradas ni live execution.

## Acción operativa pendiente

La instancia que el usuario ya tiene arrancada fue creada antes de PR #83; necesita reconstruirse para cargar las variables y el código nuevos. Desde la raíz del repositorio:

```bash
docker compose -f docker-compose.demo.yml up --build -d
```

No usar `down -v`: el volumen `paper_demo_data` contiene la cartera ficticia y debe conservarse. Después:
1. Consultar `GET /api/health/ready` y confirmar `status=ready`, `mode=paper`, `experimental_paper_enabled=true` y `live_trading_enabled=false`.
2. Abrir `/dashboard`; debe mostrar `EXPERIMENTAL PAPER`.
3. Conectar controles con el token local de demo y pulsar Reanudar.
4. Observar ciclos y operaciones etiquetadas `experimental:`. Si no hay señal, datos frescos o aprobación de riesgo, el resultado correcto sigue siendo `hold`.

## Siguiente fase

- Verificar el comportamiento de la demo reconstruida durante varios ciclos; revisar fills paper, stops, comisiones, slippage y drawdown.
- Diseñar investigación de copy trading como fuente de señales: solo track records verificables, timestamps, P&L neto, drawdown, leverage, costes y latencia; primero shadow/paper, nunca copia ciega.
- Mantener como backlog el ledger append-only de posiciones multi-venue y sus adaptadores, sin mezclar snapshots externos con la cartera paper.

## Fase completada — Demo local paper (PR #75)

- Compose independiente `docker-compose.demo.yml`: solo API, publicación en `127.0.0.1`, volumen persistente propio, 10.000 EUR virtuales, mercado público Kraken y hardening del contenedor. No lee `.env` de producción ni inicia el servicio de backups.
- Binance read-only, CCXT autenticado, Binance Spot Demo y backups externos quedan forzados desactivados y sin claves. Solo se puede añadir opcionalmente `TWELVE_DATA_API_KEY` para datos de cotizaciones, en `.env.demo` ignorado por Git.
- Arranque documentado: `docker compose -f docker-compose.demo.yml up --build`; dashboard local en `/dashboard`. Las órdenes paper existentes siguen protegidas por Risk Manager y token.
- Añadida validación de configuración del Compose demo a CI. En el SHA `7abd5f527612d0c593e93d8a94852cc29d0a72d8`, `Python tests` pasó con **460 tests** y `Docker build and runtime hardening` pasó, incluida la validación de los Compose base y demo.
- Entorno de trabajo actual no tiene Docker ni intérprete Python disponibles, así que no es posible ejecutar localmente `docker compose config` ni `pytest -q`; ejecutar ambos gates en GitHub Actions.
- Sin credenciales reales, fondos, despliegue ni conexión de ejecución a producción.

## Siguiente acción

1. Completar CI y conseguir revisión independiente de PR #81; integrar solo cuando ambos gates estén verdes.
2. Revisar el estado vivo de PR #77–80 (varias ramas están apiladas); mantener el orden de dependencias y no mergear una rama base sin verificar sus descendientes.
3. Tras integrar el fix, medir si las estrategias producen oportunidades validadas en más ciclos. No rebajar los gates OOS/walk-forward/costes para fabricar operaciones.
4. Actualizar el roadmap/handoff con el SHA integrado y el siguiente phase gate.

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
- PR #73: https://github.com/Harilai/ai-trading-system/pull/73\n- PR #81: https://github.com/Harilai/ai-trading-system/pull/81\n- PR #82: https://github.com/Harilai/ai-trading-system/pull/82\n- PR #83: https://github.com/Harilai/ai-trading-system/pull/83
- Arquitectura multiactivo: `docs/MARKET_EXPANSION_ARCHITECTURE.md`
- Contratos de cuenta: `docs/ACCOUNT_PROVIDER_CONTRACTS.md`
- Contrato de instrumentos: `docs/INSTRUMENT_CONTRACT_V2.md`
- Workflow de agentes: `docs/AGENT_WORKFLOW.md`

**Regla de continuidad:** la siguiente conversación debe comprobar el estado vivo de GitHub y el resultado operativo de la demo reconstruida. No reimplementar PR #81–#83. Después continuar el diseño de copy-trading research y el backlog del ledger de posiciones multi-venue.
