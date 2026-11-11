# Session handoff — AI Trading System

 > **Última revisión:** 2026-11-11 Europe/Madrid
> **Repositorio:** `Harilai/ai-trading-system`
 > **Último cambio de código integrado:** PR #155, merge SHA `23dcac3b65c46a59d3e24edf487cefe88ffc88fd`.
> **main y demo:** deben permanecer idénticas por SHA; tras actualizar este handoff, sincronizar `demo` por fast-forward y verificar el compare.
> **PR abiertas:** ninguna tras integrar PR #155.
> **CI:** PR #155 head `4c4d30b781012c477f9f2c14fb35455bea00df8f`: 778 Python tests passed, 1 warning; Docker build/runtime hardening passed (run `43080178575`). Post-merge main run `43080343137`: success. Documentación de roadmap/handoff en SHA `a2e82317a350964a7202b0c078cc2f8e01c78fae`: 778 Python tests passed, 1 warning; Docker build/runtime hardening passed (run `43080509437`).

> **Validación local:** no se pudo clonar el repositorio desde el shell por falta de resolución de GitHub; no afirmar tests locales. La validación ejecutada fue GitHub Actions.
> **Operación:** investigación y PAPER; LIVE sigue deshabilitado. No ejecutar operaciones reales ni usar credenciales productivas.

## Integración más reciente — PR #155: capacidades históricas por proveedor

- PR #155 integrada por squash en `main`; merge SHA `23dcac3b65c46a59d3e24edf487cefe88ffc88fd`.
- `main` y `demo` se compararon después de la integración como identical; `demo` se actualizó por fast-forward con expected SHA para preservar exactamente el mismo commit.
- CI del head exacto de PR `4c4d30b781012c477f9f2c14fb35455bea00df8f`: 778 tests Python passed, 1 warning; Docker build/runtime hardening passed. No tests locales ejecutados.
- Nuevo contrato `ProviderCapabilities`: histórico OHLCV, timeframes, paginación, máximo por petición y profundidad histórica.
- Kraken Spot declara 720 velas máximas por intervalo y por petición. Los demás proveedores no heredan ese límite; la profundidad desconocida se conserva como desconocida.
- El scheduler falla de forma cerrada ante capacidades ausentes, nulas, con identidad incorrecta o inválidas. Persiste requested/effective range y migración compatible de estado.
- Runbook actualizado en `docs/HISTORICAL_DATA_SYNC.md`.
- No se usaron credenciales privadas, no se enviaron órdenes y LIVE sigue deshabilitado.
- Post-merge CI de `main`: run `43080343137`, pendiente al escribir esta nota.

## Siguiente bloque recomendado — costes de ejecución y robustez cuantitativa

1. Auditar el modelo actual de fees y slippage del backtester; evitar duplicar costes ya aplicados.
2. Incorporar spread bid-ask observable con timestamp y procedencia; no inventar spread cuando falten bid/ask.
3. Separar spread observado, fee del proveedor, slippage modelado y coste de liquidez/impacto estimado.
4. Asegurar que cada backtest usa solo información disponible en el timestamp de decisión (sin look-ahead).
5. Añadir escenarios de sensibilidad y holdouts temporales independientes sin relajar gates.
6. Mantener LIVE deshabilitado y no ejecutar acciones financieras.

## Integraciones recientes verificadas

- PR #109: diagnóstico seguro de claves de activo inválidas en Binance Demo.
- PR #110: sincronización del fix a `demo`.
- PR #113: snapshot coordinado balances+posiciones, persistencia atómica, idempotencia y API autenticada.
- PR #114: sincronización de implementación a `demo`.
- PR #115: sincronización de documentación a `demo`.
- PR #116: evaluación/ADR Alpaca read-only fusionada en `main`; PR #117: sincronización de esos documentos a `demo`, fusionada con SHA `710cb978bb4e5c62bd3e0559b5921b67f628f689`. Ambos checks de #117 (Python tests y Docker runtime) pasaron.
- PR #118: adaptador Alpaca Paper read-only fusionado en `main` (SHA `d42b9fada02d60d7073bbc76171bd69c31a9e8a3`); CI exacta: 609 tests Python passed y Docker build/runtime hardening passed.
- PR #120: sincronización del adaptador a `demo` (SHA `23fa997a878da7fe7ba7735b2baa80f035f7a8d7`); CI Python y Docker pasó. PR #119 se cerró como intento directo `main`→`demo` marcado dirty; #120 preservó commits exclusivos de demo y sincronizó solo ocho archivos.
- CI de PR #113: 581 tests passed, 1 warning; Docker/Compose/non-root correctos en el head exacto `800f8fd63d87fb8f1c6e2ed5c5bc12adb926b0b0`. CI de sincronización #114 y #115 también pasó en sus respectivos heads.
- PR #123 y #124 integradas con CI Python y Docker exitosa; no se ejecutaron pruebas locales en este entorno.
- PR #129: validación de discovery contra provider identity/capabilities, merge `e7bccdb62ddb5ac3464049c41a95686d3b45085f`; CI Python y Docker passed.
- PR #130: filtros por proveedor/clase en discovery, merge `98760e46740556144478623929ed233f7e7402bd`; CI Python 645 passed y Docker passed.
- PR #131: evaluación fail-closed de elegibilidad de instrumentos, merge `9c0bca72e13ecd2edb8b2ff0649a42b0458c6940`; CI Python 655 passed y Docker passed.
- Las funcionalidades #78–#80 (contratos de órdenes/fills, ledger y completitud/reconciliación read-only), #99 (ledger de posiciones) y #100 (diagnósticos experimentales y salidas seguras) están integradas en `main` y `demo`.
- No se ha ejecutado un preflight posterior a #109 contra el contenedor Binance Demo local. El problema original de asset key sigue pendiente de confirmar con evidencia del contenedor del propietario.

## Estado operativo Binance Demo

- El preflight automatizado de solo lectura está implementado (#103); realiza tres GET autenticados y verifica identidad Demo, capacidades y catálogo.
- El fix #109 mejora el diagnóstico del error de balance.
- Sigue pendiente ejecutar el preflight en el entorno local del propietario. No pedir ni recibir API keys ni tokens por chat.
- No ejecutar el drill de órdenes hasta que el preflight pase. El smoke test de órdenes Demo requiere autorización explícita del propietario y debe usar exclusivamente el host Demo; no se debe cambiar a producción.
- Ningún test de CI sustituye la validación operativa contra la cuenta Demo real.

## Snapshot coordinado — implementado e integrado en PR #113

La implementación está en `docs/COORDINATED_ACCOUNT_SNAPSHOT_DESIGN.md` y `backend/app/accounts/coordinated_snapshot_store.py`. Los stores de balances y posiciones ahora admiten inserts dentro de una transacción compartida. El coordinador crea referencias a ambos registros y el grupo en una sola transacción SQLite, sin duplicar los datos. También se añadió aislamiento de balance snapshots por entorno con migración legacy a `production`, idempotencia por request ID y API autenticada.

CI sobre el head exacto `800f8fd63d87fb8f1c6e2ed5c5bc12adb926b0b0`: 581 tests passed, 1 warning; Docker build/runtime hardening pasó, incluida validación Compose y ejecución non-root. PR #114 sincronizó la implementación a `demo`; los árboles `main` y `demo` vuelven a ser idénticos (112 archivos). No se ejecutó test local en el entorno del propietario.

La implementación exige:
- identidad común proveedor/cuenta/entorno/mercado y `capture_id`;
- cobertura explícita de balances y posiciones;
- timestamps de origen/retrieval con procedencia y skew máximo;
- normalización decimal y validación completa antes de persistir;
- referencias a los dos ledgers existentes, sin duplicar datos;
- persistencia atómica y rollback total si falla cualquiera de las escrituras;
- ningún efecto en la cartera paper ni en decisiones de trading.

La captura real sigue bloqueada hasta que exista un proveedor que exponga ambas lecturas con cobertura verificable. Los conectores Binance Spot read-only y CCXT Spot actuales no declaran capacidad de posiciones ni cobertura de balances explícita; Binance Spot Demo es un conector de órdenes y no debe tratarse como proveedor de posiciones. El endpoint responde fail-closed (409) si falta cualquiera de esas capacidades. No inferir posiciones desde balances ni fills.

## Arquitectura y seguridad

- Mantener separados MOCK, BACKTEST, PAPER, DEMO/TESTNET y LIVE.
- LIVE deshabilitado por defecto; no activar ni operar con fondos reales.
- Binance Demo nunca debe hacer fallback a producción.
- No introducir secretos en Git, logs, documentación, frontend ni conversaciones.
- No inferir posiciones desde balances.
- Una captura parcial/desconocida no permite concluir que una posición o activo haya desaparecido.
- IA/Chief siempre detrás del Risk Manager.
- Mantener idempotencia, exactitud decimal, append-only, aislamiento por cuenta/entorno/mercado y conciliación fail-closed.

## Alpaca Paper read-only — integrado en main y demo

- Adaptador `backend/app/accounts/alpaca_paper_readonly.py` integrado mediante PR #118 y sincronizado mediante PR #120.
- Adaptador `backend/app/accounts/alpaca_paper_readonly.py`: host fijo `https://paper-api.alpaca.markets`, solo GET /v2/account y GET /v2/positions, verificación estricta del ID de cuenta esperado, decimal strings y fail-closed ante errores HTTP/JSON/identidad.
- Solo registra posiciones `cash` de `us_equity` como `equity`; no infiere ETF, balances, permisos reales ni cash desde buying power/equity.
- Opt-in apagado por defecto. Requiere `ALPACA_PAPER_ENABLED=true`, key/secret y `ALPACA_PAPER_EXPECTED_ACCOUNT_ID`. No introducir credenciales en Git.
- Tests MockTransport cubren posiciones, lista vacía, identidad, duplicados, clases/decimales inválidos, host fijo y HTTP 401/403/429/5xx. CI: 609 tests passed y Docker hardening passed. No hay validación operativa contra Alpaca.
- No conecta snapshots a cartera, Chief, Risk Manager ni ejecución. El snapshot coordinado sigue limitado a `spot`; balances/cash y extensión a `cash` quedan como fase separada.


## Semántica financiera Alpaca — ADR integrado y ledger inicial en desarrollo

- Documento: `docs/ALPACA_ACCOUNT_FINANCIAL_SEMANTICS_ADR.md` (PR #123 en `main`, PR #124 en `demo`).
- `cash` se modela como saldo de efectivo; `equity` como valor neto de la cuenta; `buying_power` como capacidad de compra dependiente de reglas/multiplicador. Nunca son intercambiables.
- `portfolio_value` es equivalente/deprecado respecto a equity; no se suma dos veces.
- `cash_withdrawable/transferable`, transferencias pendientes, valores de mercado, margen y fees son métricas independientes, no saldos fungibles.
- No reutilizar `AccountBalance(free/used/total)` ni ampliar el store Spot-only para almacenar métricas Alpaca.
- Rama `feat/alpaca-financial-metrics-ledger`: añade lector `GET /v2/account` de una sola llamada, snapshot financiero tipado, ledger SQLite append-only/idempotente y endpoints autenticados de captura/historial. Requiere CI/revisión antes de integrarse.
- No se conecta a cartera paper, Chief, Risk Manager ni ejecución. No se habilitan transferencias, retiradas ni LIVE.

## Continuidad

1. Verificar heads, PR abiertas, CI y árboles de `main`/`demo` antes de cada bloque.
2. Tras cada merge, eliminar la rama de trabajo si no es permanente y no contiene trabajo pendiente, cuando la conexión disponga del permiso correspondiente.
3. El Engineering Lead revisa el diff completo, ejecuta/verifica tests y CI, decide la aprobación técnica y realiza el merge rutinario. El propietario no debe revisar, aprobar ni fusionar PR ordinarias.
4. Si GitHub exige una aprobación de otra cuenta, no autoaprobar ni saltarse la protección; registrar el bloqueo de plataforma. No pedir al propietario que revise la PR.
3. No cerrar PR por limpieza ni fusionar ramas divergentes sin comparar árboles y clasificar funcionalidades.
4. Actualizar roadmap y handoff al completar fases.
5. Distinguir implementado, probado por CI, integrado y validado operativamente.


## Política obligatoria de ramas

- `main` es la fuente de verdad; `demo` debe ser idéntica a `main` por SHA.
- No desarrollar directamente en `demo` ni fusionar su historial hacia `main` por defecto.
- Tras cada merge válido a `main`, mover/sincronizar `demo` al mismo SHA y verificar `compare main...demo = identical`.
- La antigua punta divergente de `demo` está preservada en `archive/demo-before-main-sync-2026-11-11`.

## Snapshot coordinado Alpaca cash — integrado (PR #128)

- Rama: `feat/coordinated-cash-account-snapshots`.
- Nuevo store `backend/app/accounts/coordinated_cash_snapshot_store.py`: vincula ledger financiero Alpaca y ledger de posiciones cash en una transacción SQLite.
- `AccountFinancialMetricsStore` expone inserción transaccional y lectura por snapshot ID.
- Endpoint autenticado: `POST /api/accounts/alpaca/financial-metrics/snapshots/coordinated`.
- Tests añadidos para captura completa, scope, skew, rollback, idempotencia y append-only.
- CI de PR #128: Python tests y Docker build/runtime hardening completados con éxito sobre el head `ef753ceb6d4f60d5d3aea03e0bfbdd8d39bd0484`.
- Merge squash: `7ab1e0056c3f1fd749d26cdfc52eca0ac6abad6a`. `main` y `demo` sincronizadas a ese SHA y compare verificado como identical.
- Pendiente: validación operativa read-only con credenciales Paper del propietario; no se ha ejecutado ninguna captura real. LIVE sigue deshabilitado.


## Continuidad — endurecimiento del descubrimiento de proveedores

- Rama feat/provider-discovery-contract-validation: valida identidad, asset class y market type de cada instrumento descubierto contra las capacidades declaradas y los filtros solicitados.
- Los registros incompatibles se descartan con fallos tipados; los errores de proveedor no propagan texto arbitrario potencialmente sensible.
- Tests añadidos en backend/tests/test_providers.py. PR #129 integrada por squash en `e7bccdb62ddb5ac3464049c41a95686d3b45085f`; CI Python y Docker pasó.


## Continuidad — filtros de descubrimiento por proveedor

- Rama feat/provider-scoped-instrument-discovery añade provider_ids a ProviderRegistry.discover_markets.
- API GET /api/markets y POST /api/instruments/refresh aceptan provider_id y asset_class; refresh continúa protegido por X-Agent-Token.
- Se cubren proveedor inexistente, clase no soportada, aislamiento de proveedores, filtro global, filtros vacíos y compatibilidad de mocks API.
- PR #130 integrada por squash en `98760e46740556144478623929ed233f7e7402bd`; CI Python: 645 passed, 1 warning; Docker build/runtime hardening pasó. CI post-merge de main pasó.


## Estado de ramas tras PR #130

- `main` y `demo` sincronizadas al SHA `98760e46740556144478623929ed233f7e7402bd`; compare `main...demo` verificado como `identical` (0 ahead/behind, 0 commits).
- No había PR abiertas en la última consulta.
- Próximo bloque: revisar cobertura real de los adaptadores y diseñar el siguiente proveedor/clase de activo a partir de capacidades verificadas. No inferir soporte por catálogo de enum; no habilitar ejecución por el mero hecho de descubrir instrumentos.


## Bloque integrado — elegibilidad de instrumentos (PR #131)

- Rama feat/instrument-trading-eligibility.
- Se añade un evaluador read-only para diferenciar soporte de datos, soporte declarado de trading, configuración del adaptador de ejecución y completitud de metadatos.
- Se bloquea el estado supported si faltan capacidades de idempotencia, fills parciales, cancelación, conciliación, balances o posiciones.
- API GET /api/instruments/{catalogue_id}/eligibility.
- Tests unitarios y de API añadidos; CI exacta 655 passed, 1 warning y Docker build/runtime hardening passed. Merge completado.
- No cambia el comportamiento de órdenes ni activa LIVE.


## Estado tras PR #131

- Evaluador read-only y endpoint `GET /api/instruments/{catalogue_id}/eligibility` integrados.
- La API no marca instrumentos como operables porque no tiene un registro de adaptadores de ejecución conectado a este evaluador; `execution_adapter_configured=false` de forma explícita.
- Siguiente bloque: conectar una fuente verificable de capacidades de ejecución y requisitos de mercado al gate de elegibilidad sin duplicar el registro de adaptadores; después validar integración Demo/Paper con mocks y preflight. LIVE permanece deshabilitado.


## Master plan — prioridades inmediatas tras PR #131

1. Conectar el evaluador de elegibilidad a una fuente de verdad de adaptadores de ejecución, sin duplicar el ProviderRegistry ni confundirlo con el registro de market data.
2. Normalizar y verificar capacidades de ejecución por proveedor/venue/market type, incluyendo account scope, order types, idempotency, partial fills, cancel, reconciliation, balances/positions y restricciones.
3. Completar requisitos por instrumento (tick/lot/min notional, fees, sesiones, expiry/contracts, settlement) y enlazar la decisión de elegibilidad al RiskManager/ExecutionRouter con fail-closed.
4. Validar Paper/Demo con mocks y preflight; ejecutar smoke test real de Binance Demo solo con autorización y credenciales configuradas por el propietario en su servidor.
5. Después ampliar proveedores/mercados y realizar backtests fuera de muestra con costes; VPS/backup restore drill siguen pendientes.
6. LIVE queda fuera de alcance hasta aprobación explícita, auditoría, sandbox, conciliación y pruebas adversas completas.


## Bloque integrado — execution eligibility gate (PR #132)

- PR #132: https://github.com/Harilai/ai-trading-system/pull/132
- Merge squash SHA: `15e80e59dc05348581cee8acb29f7fee76df6341`.
- Cambios: registro de ejecución separado del registro market-data; lookup de adaptadores y capabilities; compatibilidad asset class/market type; elegibilidad incluye instrument_id; ExecutionRouter.submit exige evidencia supported ligada a provider e instrumento; RiskManager permite exigir esa evidencia como precondición fail-closed.
- Tests/CI sobre head exacto `a204cdd410c64364ab3a45744a4bbdee2462187b`: 661 passed, 1 warning; Docker build/runtime hardening pasó. No se ejecutaron tests locales.
- Fallos de CI intermedios se investigaron por logs y corrigieron antes del merge. El head final es el que se considera validado.
- No se registra ningún adaptador genérico en la aplicación: endpoint de elegibilidad sigue devolviendo no_configured para los instrumentos actuales, correctamente. Binance Demo y Alpaca no se convierten en adaptadores genéricos por inferencia.
- LIVE continúa deshabilitado; no se enviaron órdenes ni se hicieron acciones financieras.

## Método obligatorio de continuidad por conversación

- Iniciar cada conversación con revisión live de GitHub, main/demo, PR, CI, roadmap y handoff.
- Mostrar al propietario un resumen de estado: implementaciones integradas, capacidades disponibles, estado de pruebas, riesgos/bloqueos y próximo bloque.
- Ejecutar bloques coherentes sin pedir confirmación por cada paso técnico ordinario.
- Al cerrar cada bloque, consolidar cambios, archivos, PR/merges, pruebas exactas, limitaciones y siguiente prioridad.
- Actualizar siempre ROADMAP y SESSION_HANDOFF para que una conversación nueva pueda continuar sin depender del chat anterior.
- Documento normativo ampliado: `docs/AGENT_WORKFLOW.md`, sección “Session continuity contract”.

## Próximo bloque — execution adapter registry v2

1. Diseñar scope explícito por provider/venue/account/environment/market_type; no equiparar market-data provider con broker.
2. Separar capabilities de ejecución de capabilities de datos, balances y posiciones; cada una debe tener procedencia y estado.
3. Revisar Binance Demo y PAPER existentes para decidir wrappers seguros; no declarar balances/positions/reconciliation si el contrato no los ofrece realmente.
4. Conectar account readiness, stale data y reconciliación al preflight de nuevas entradas; preservar salidas de reducción/emergencia.
5. Añadir tests de cuenta equivocada, entorno equivocado, capabilities parciales, estado desconocido, desconexión, snapshots incompletos y mismatch de reconciliación.
6. Mantener registry vacío y fail-closed hasta que exista adaptador validado. No LIVE.

## Estado de ramas — verificado tras documentación

- PR #132 fusionada en main.
- `main` y `demo` sincronizadas y compare verificado como `identical` tras los commits documentales de esta sesión; actualizar el SHA exacto al inicio de la próxima conversación.

## Estado actual — registry, adaptadores y gobernanza (2026-11-11)

- PR #133 integrada en `main`; merge commit `ffe940be7b7d399b8ad4409b09adf9e26c8cb872`.
- PR #134 integrada mediante squash; merge commit `9f42a272cbc4c2d603137db859b43078af5ca881`.
- PR #135 integrada mediante squash; merge commit `a11201319c503fb110cd6aec315e7b9c1ab77ccf`. Actualiza este handoff y el roadmap tras #134.
- CI de PR #134 en head exacto `2930df8471596e6fe131d418db1e5824f9b6581e`: run `43050767811`, Python tests y Docker build/runtime hardening ambos success. CI post-merge de #134: run `43050890327`, ambos success.
- CI de PR #135 en head exacto `bfd19dfafbe3323259261e3a056b3729ab5cbb6c`: run `43051061358`, ambos jobs success. CI post-merge: run `43051187209`, ambos jobs success.
- La revisión técnica interna la realizó el Engineering Lead. No se solicitó al propietario revisar, aprobar ni fusionar ninguna de estas PR.
- Implementado: `BinanceSpotDemoScopedAdapter` y `PaperPortfolioScopedAdapter`, snapshots con scope explícito, balances y posiciones. Binance Spot balances no se convierten artificialmente en posiciones; positions sigue UNKNOWN. Los account_id son alias locales no verificados como identidad remota.
- PAPER sigue limitado a crypto Spot y simulación market atómica; partial fills y cancelación de exchange permanecen UNSUPPORTED. Readiness falla cerrado. Los wrappers no están registrados automáticamente ni conectados a rutas de envío.
- La cantidad de posiciones PAPER se valida con Decimal finito, sin filtrar cantidades positivas mediante float; tests cubren cantidades positivas diminutas y valores no finitos.
- `main` y `demo` están sincronizadas y el compare se verificó como `identical` (0 ahead/behind). Consultar el SHA actual directamente en GitHub al iniciar la siguiente sesión.
- No se han enviado órdenes ni realizado acciones financieras. LIVE permanece deshabilitado.


## Continuity update — account readiness preflight integrado (2026-11-11)

- PR #137 integrada en `main` mediante squash; merge commit `e3158a8999a7571dce79aa40809beddbbff792f5`.
- Head exacto validado: `8c7ecea237d52d6a12bddc7d569b83dd5a2d6eaa`; CI run `43053950094`: 718 Python tests passed, 1 warning; Docker build/runtime hardening success.
- `PaperAccountReadiness` verifica scope interno `internal:paper-portfolio / internal-ledger / paper-primary / PAPER / spot`, origen y frescura del snapshot, integridad Decimal de balances/posiciones y conciliación de todos los intents no terminales.
- La identidad es la del ledger local, no una cuenta remota verificada. No afirmar que `paper-primary` sea un UID de broker.
- El adaptador interno PAPER está registrado en el registry scoped, pero mantiene partial fills y cancelación de exchange UNSUPPORTED; no pasa generic exchange readiness y no se conecta al ExecutionRouter.
- Endpoint autenticado `POST /api/execution/preflight`.
- Compras manuales PAPER crypto y Twelve Data equity/ETF pasan preflight, entregan evidencia al RiskManager y revalidan inmediatamente antes de ejecución. Las ventas mantienen controles independientes y no se bloquean por el gate de entrada.
- Chief de producción exige readiness para nuevas compras autónomas. El worker revalida antes de ejecutar y al reanudar intents persistidos; ante fallo rechaza/caduca el intent y no ejecuta.
- Añadido `PaperPortfolio.list_unresolved_order_intents()` para inventario completo sin límite de paginación.
- La CI de PR #137 está verde (718 tests, 1 warning; Docker hardening success). CI post-merge de main run `43054150372`: Python tests y Docker build/runtime hardening success.
- No se ejecutaron tests locales. No se utilizaron credenciales externas ni se enviaron órdenes. LIVE sigue deshabilitado.
- PR #138 documental integrada por squash en `23f437ebc2e3a9a06c2f0c8f5f822178714fba83`; CI post-merge run `43054569103` (Python tests y Docker hardening) success.
- `main` y `demo` se sincronizaron al SHA `23f437ebc2e3a9a06c2f0c8f5f822178714fba83`; compare verificado `identical` (0 ahead/behind).
- Próximo bloque: identidad verificable de cuenta Binance Spot Demo, mapeo balances→posiciones por instrumento con valoración/timestamps y cobertura, manteniendo UNKNOWN cuando no haya evidencia.



## Continuidad — fiabilidad de Strategy Research (#142–#144)

- PR #142 añade exposición, duración media, expectancy, media de ganadoras/perdedoras, payoff, volatilidad por vela y Sharpe/Sortino por vela. Ratios no anualizados. La liquidación final reemplaza el último mark de equity y no crea un periodo artificial.
- PR #143 añade `data_quality`: número de velas, días cubiertos, cadencia mediana, huecos >1,5x cadencia, velas OOS y barras por ventana walk-forward. El detector no conoce calendarios de mercado; aplicarlo a acciones/FX requiere calendario por venue.
- PR #144 persiste `data_quality` en `strategy_research_history` y presenta las métricas OOS y calidad del histórico en el dashboard. Registros históricos incompletos siguen siendo válidos.
- PR #142 merge SHA `257aabafd2ea8dfbb8f0a8726688abc81b13b18a`; PR #143 merge SHA `59b3f7ed960911240134295e9e3a7cace3b6b09c`; PR #144 merge SHA `24d2235fd7fba9a9158fe688c92e5df087cdf387`.
- CI PR #142 run `43065850901` success y post-merge `43066016747` success. CI PR #143 run `43066316270` success y post-merge `43066501575` success. CI PR #144 run `43066715216` success y post-merge `43066860684` success.
- `ScoutAgent` usa timeframe `1h` y `candle_limit=250`; unas 10,4 jornadas, 75 barras OOS y 49 barras por ventana walk-forward. La muestra es demasiado corta para afirmar robustez.
- El universo de estrategia tiene tres candidatos: EMA 12/26, RSI(14) mean reversion y Bollinger(20,2) mean reversion. No se añadieron estrategias nuevas ni se relajaron gates.

## Siguiente bloque recomendado

1. Verificar paginación, límites máximos y cadencia por exchange antes de ampliar targets.
2. Añadir diagnóstico de huecos/cobertura por calendario y sesión de cada venue.
3. Conectar Strategy Research a muestras históricas más profundas y separar insuficiencia de muestra, pérdidas, drawdown y sensibilidad a costes.
4. Incorporar spread/costes observables y después estrategias distintas con control de selección múltiple.
5. Validar con holdouts independientes; mantener los gates y límites actuales.

LIVE permanece deshabilitado. `main` es la fuente de verdad y `demo` debe permanecer idéntica a `main`.


## Bloque integrado — histórico persistente/paginado (PR #146, correcciones #148)

- PR #146 integrada por squash en `main`, merge SHA `db83df4f0de25c9c24bdf96263cbab1a96567e89`. PR #148 integrada con merge SHA `094df642ee1366f63c80c48f1a99bbbfe21d9161`; `demo` sincronizada al SHA final y compare verificado como identical.
- Nuevo módulo `backend/app/historical_data.py`: store SQLite provider/instrument/timeframe scoped; valores OHLCV guardados como decimales de texto; lecturas por rango; cobertura; idempotencia.
- Las velas provisionales se pueden refrescar. Las finalizadas son inmutables: discrepancias rechazan la operación y hacen rollback; un replay antiguo no degrada una vela finalizada.
- Colector paginado con `since`, `limit`, orden temporal estricto, límite de páginas y cursor `next_since_ms` para reanudar.
- Endpoints autenticados `POST /api/market-data/history/sync` y `GET /api/market-data/history`. La sincronización requiere instrumento activo del catálogo del proveedor y reutiliza rate limiting CCXT. Otros proveedores no se habilitan sin soporte paginado declarado.
- CI PR #146: 741 tests Python passed, 1 warning; Docker build/runtime hardening passed. PR #148: 744 tests Python passed, 1 warning; Docker build/runtime hardening passed. CI post-merge #148 success.
- No se ejecutaron tests locales ni validación operativa contra proveedores reales. El scheduler incremental se integra en PR #150, es opt-in y la retención raw se define indefinida, sin poda automática. LIVE permanece deshabilitado.



## Corrección de consultas de histórico — PR #148

- `HistoricalCandleStore.get(..., limit=N)` devuelve ahora las N velas más recientes, en orden cronológico ascendente. Antes devolvía las primeras N históricas y podía ocultar los datos recientes tras un backfill grande.
- Canonicalización decimal para que `10`, `10.0` y `10.000` sean idempotentes sin redondear valores.
- Rechazo explícito de timestamps fraccionarios, evitando truncamientos silenciosos.
- PR #148 merge SHA `094df642ee1366f63c80c48f1a99bbbfe21d9161`; CI del head: 744 passed, 1 warning; Docker build/runtime hardening passed.

## Bloque integrado — scheduler incremental e integración de histórico (PR #150)

- PR #150 integrada por squash; head validado 9e5d139b97ce7c0cc102fd5912b9412d57185389; merge SHA 89bb3e76e3bab21ae2b98496f0fc54b801ec0f93. main y demo se compararon como identical.
- Implementación nueva: backend/app/historical_data_scheduler.py, con allowlist explícita, parser de targets, cursor persistente por proveedor/instrumento/timeframe, backfill paginado reanudable, refresco de vela provisional, estado por target y errores acotados.
- Doble opt-in: Compose profile history-sync y setting HISTORICAL_DATA_SYNC_ENABLED=false por defecto. La lista HISTORICAL_DATA_SYNC_TARGETS exige destinos explícitos en formato ccxt:kraken,BTC/EUR,1h; varios se separan por punto y coma.
- Servicio aislado history-sync comparte solo el volumen SQLite, no recibe .env ni credenciales de brokers. Ejecuta exclusivamente OHLCV público con CCXT rate limiting, secuencialmente y con límite de páginas por target/ciclo.
- Retención: indefinida y sin poda automática; storage stats suman DB/WAL/SHM. Se pospone cualquier borrado hasta diseñar exportación y recuperación.
- Scout ahora conserva velas recientes del proveedor para Charts y, si el histórico persistido tiene más velas finalizadas, lo usa para Strategy Research. Velas provisionales excluidas; fallback a datos recientes ante ausencia/error del store. La fuente y número de barras quedan registrados en el resultado.
- Nuevo endpoint autenticado GET /api/market-data/history/scheduler; runbook docs/HISTORICAL_DATA_SYNC.md; variables añadidas a .env.example; README actualizado.
- Tests añadidos para allowlist, rechazo de proveedores no soportados, persistencia/reanudación de cursor, refresco de provisional, política de retención, status API y consumo de histórico en Scout.
- CI head exacto: 760 passed, 1 warning; Docker hardening passed. CI post-merge run 43073260919: ambos jobs success. No se ejecutaron tests locales ni sincronización real contra un exchange. Scheduler desactivado por defecto.
- LIVE deshabilitado; sin órdenes ni acciones financieras.

### Siguiente bloque

1. Verificar paginación y límites reales por exchange, incluyendo since, límite máximo y cadencia.
2. Añadir diagnóstico de huecos/cobertura por sesión/venue, sin aplicar heurísticas crypto 24/7 a equities/FX.
3. Conectar Strategy Research a rangos persistidos y medir la suficiencia OOS/walk-forward real antes de introducir más estrategias.
4. Modelar spread y costes observables, y después añadir candidatos estructuralmente distintos con control de selección múltiple.


## Bloque integrado — clasificación de fallos y suficiencia de muestra (PR #152)

- PR #152 integrada por squash; head validado 8aa757c7004fb46e8db66170ba818f69108c3d79; merge SHA d2bd60484a121bd1c93bf643c418233a12191be8. main y demo verificadas como identical.
- StrategyResearchAgent añade validation_failure_categories por candidato: sample, performance, risk, cost y other. Los validation_failures originales se mantienen.
- Añade sample_adequacy con barras disponibles, OOS bars, barras por ventana walk-forward y umbral configurado. El estado solo informa cumplimiento del mínimo de barras, no robustez estadística.
- Scout conserva el estado compatible insufficient_or_invalid_history y añade diagnostic_code/failure_category para diferenciar insufficient_history de invalid_history; valida OHLCV antes de clasificar muestras cortas.
- AutonomousStore persiste categorías y sample_adequacy; dashboard muestra los tipos de bloqueo y la cobertura de ventanas.
- CI head: 763 passed, 1 warning; Docker build/runtime hardening passed. Post-merge run 43074905339: Python tests y Docker hardening success. Sin tests locales.
- No se modifican promotion gates, riesgo, estrategias, costes ni ejecución. LIVE deshabilitado.

## Siguiente bloque recomendado

1. Verificar límites reales de paginación y cadencia por exchange y mejorar el contrato de capacidades del adaptador.
2. Cobertura y gaps con calendarios/sesiones por venue, manteniendo separado crypto 24/7 de equities/FX.
3. Costes observables de spread/liquidez y validación holdout independiente.
4. Después, ampliar candidatos de estrategia con control de selección múltiple; no relajar gates.


## Bloque en desarrollo — cobertura de histórico consciente de la cadencia (2026-11-11)

- Estado base verificado: main y demo identical en 41e069fd4f20f6a2ed70587df3c0665725857402; PR abiertas 0 tras PR #153.
- PR #153 documenta la integración de #152 y su evidencia CI: 763 tests Python y Docker hardening correctos; run post-merge #153 (43075325981) finalizado correctamente.
- En rama feat/venue-aware-research-coverage se añade expected_interval_seconds opcional a StrategyResearchAgent; el diagnóstico explicita cadence_source, expected interval, estimated_missing_intervals y calendar_model.
- Scout transmite la cadencia derivada de timeframe para su universo crypto spot EUR y declara crypto_24x7 solo para ese universo. Timeframes calendario/mes no se convierten a duración fija.
- Se mantiene calendar_adjustment=false: esto detecta desviaciones de cadencia, no aplica calendario bursátil ni prueba que el dato ausente sea negociable.
- Tests añadidos para huecos con cadencia explícita, estimación de barras ausentes y rechazo de intervalos cero/NaN/Inf/bool. CI pendiente para el head final; no tests locales.
- No se cambian estrategias ni gates. LIVE deshabilitado.


## Rama histórica supersedida

- `fix/kraken-ohlcv-history-capabilities` sigue existiendo, pero no debe fusionarse: está divergida respecto a `main` y su versión del scheduler permite continuar con adaptadores sin contrato de capacidades históricas, debilitando el fail-closed.
- PR #155 (`feat(history): enforce provider OHLCV pagination capabilities`) ya integró en `main` la implementación equivalente con validación estricta de identidad, soporte, timeframe, paginación y límites. La rama antigua no tiene PR abierto y queda supersedida; conservarla solo como referencia hasta decidir su limpieza.

## Trabajo en curso — spread observado en Strategy Research

- Rama: `feat/research-observed-spread-costs` (base `main` SHA `885fa5dc022882f8218ec95187c6b6c4930dd556`).
- Store `HistoricalQuoteStore` para bid/ask públicos con identidad de proveedor/instrumento, provenance de timestamp y timestamp local de recepción; incluye migración para snapshots anteriores.
- Scout captura solo quotes validados. Si falta bid/ask no crea spread sintético.
- Strategy Research aplica spread observado como medio spread por lado en el siguiente fill de apertura/cierre, solo si el snapshot ya se había recibido antes del fill y está dentro del límite de edad. Se ha añadido prueba específica contra look-ahead por hora de recepción.
- La cobertura se calcula por OOS y walk-forward y constituye un gate de promoción aditivo: la cobertura debe ser completa en ambos. Los umbrales de rendimiento, riesgo, número de operaciones y estrés existentes permanecen intactos.
- Fee y slippage permanecen como supuestos configurados; no equivalen a tarifas personalizadas de cuenta.
- CI del PR pendiente; no declarar integrado hasta pasar Python tests y Docker build/runtime hardening en el head exacto.
- Siguiente: validar que la cobertura de quotes crece con los scans; implementar comisiones maker/taker por instrumento/proveedor y slippage/liquidez con procedencia; después, holdouts independientes y controles de selección múltiple antes de ampliar estrategias.
- LIVE sigue deshabilitado. No se han realizado operaciones financieras.
