# Session handoff — AI Trading System

> **Última revisión:** 2026-11-11 Europe/Madrid
> **Repositorio:** `Harilai/ai-trading-system`
> **Último cambio integrado:** PR #123 (ADR de semántica financiera Alpaca) en `main`; PR #124 sincronizada a `demo`.
> **`main`:** `05870aa8c3f10b75a8d1bf46dd4856df1a3575ae`
> **`demo`:** `6445726293db4fd408bfa046b1b4d746bbeabc3e`
> **Árboles:** divergencia histórica de commits; los ocho archivos del adaptador Alpaca y su documentación tienen blobs idénticos en `main` y `demo`.
> **PR abiertas:** ninguna tras la integración de #123 y #124 (verificado 2026-11-11).
> **Operación:** paper trading y Binance Spot Demo aislado; live trading no implementado ni habilitado.

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
- PR #123 y #124 integradas con CI Python y Docker exitosa; no hay PR abiertas tras su integración. No se ejecutaron pruebas locales en este entorno.
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


## Semántica financiera Alpaca — ADR integrado

- Documento: `docs/ALPACA_ACCOUNT_FINANCIAL_SEMANTICS_ADR.md` (PR #123 en `main`, PR #124 en `demo`).
- `cash` se modela como saldo de efectivo; `equity` como valor neto de la cuenta; `buying_power` como capacidad de compra dependiente de reglas/multiplicador. Nunca son intercambiables.
- `portfolio_value` es equivalente/deprecado respecto a equity; no se suma dos veces.
- `cash_withdrawable/transferable`, transferencias pendientes, valores de mercado, margen y fees son métricas independientes, no saldos fungibles.
- No reutilizar `AccountBalance(free/used/total)` ni ampliar el store Spot-only para almacenar métricas Alpaca.
- Próxima implementación separada: snapshot financiero tipado y ledger append-only; después coordinarlo con posiciones `cash` solo con cobertura, identidad, timestamp/skew y atomicidad verificables.
- No se conecta a cartera paper, Chief, Risk Manager ni ejecución. No se habilitan transferencias, retiradas ni LIVE.

## Continuidad

1. Verificar heads, PR abiertas, CI y árboles de `main`/`demo` antes de cada bloque.
2. Tras cada merge, eliminar la rama de trabajo si no es permanente y no contiene trabajo pendiente. La conexión GitHub actual no expone una acción de borrar ramas; requiere habilitar auto-delete de ramas en el repositorio o conceder una integración con permiso `delete ref`.
2. Revisar el diff y dependencias de PR; integrar automáticamente solo cuando mergeability, pruebas y CI del SHA exacto estén correctos.
3. No cerrar PR por limpieza ni fusionar ramas divergentes sin comparar árboles y clasificar funcionalidades.
4. Actualizar roadmap y handoff al completar fases.
5. Distinguir implementado, probado por CI, integrado y validado operativamente.
