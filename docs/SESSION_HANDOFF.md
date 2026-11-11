# Session handoff — AI Trading System

> **Última revisión:** 2026-11-11 Europe/Madrid
> **Repositorio:** `Harilai/ai-trading-system`
> **Último cambio integrado:** PR #113 (snapshot coordinado) en `main`, PR #114 y #115 sincronizadas a `demo`; último commit de documentación en `main`.
> **`main`:** `c5481f03480dd9d74cfbffa7c1a1ec0494d44de1`
> **`demo`:** `06e3d1cb56fde74e2a7b92b613355940d69bbbb6`
> **Árboles:** idénticos en la última comparación, 112 archivos y 112 blob SHAs coincidentes.
> **PR abiertas:** ninguna en la última consulta.
> **Operación:** paper trading y Binance Spot Demo aislado; live trading no implementado ni habilitado.

## Integraciones recientes verificadas

- PR #109: diagnóstico seguro de claves de activo inválidas en Binance Demo.
- PR #110: sincronización del fix a `demo`.
- PR #113: snapshot coordinado balances+posiciones, persistencia atómica, idempotencia y API autenticada.
- PR #114: sincronización de implementación a `demo`.
- PR #115: sincronización de documentación a `demo`.
- CI de PR #113: 581 tests passed, 1 warning; Docker/Compose/non-root correctos en el head exacto `800f8fd63d87fb8f1c6e2ed5c5bc12adb926b0b0`. CI de sincronización #114 y #115 también pasó en sus respectivos heads.
- No hay PR abiertas en la última consulta del 2026-11-11. No se ejecutaron pruebas locales en este entorno.
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

## Próxima prioridad técnica

- Integrar un proveedor read-only de posiciones para acciones/ETF. Evaluación inicial: Alpaca Trading API ofrece `GET /v2/positions` en Paper y devuelve posiciones abiertas; una posición cerrada deja de aparecer. Esto es un candidato, no una integración aprobada ni probada.
- Implementar primero un adaptador aislado con host Paper fijo, llamadas GET explícitas, identidad de cuenta, validación decimal y timestamps, cobertura documentada y tests de errores/respuestas vacías. No exponer métodos de escritura.
- Investigar/implementar lectura de cuenta y balances con semántica correcta. No confundir buying power con efectivo y no habilitar snapshot coordinado hasta que ambos lados declaren cobertura verificable.
- El store coordinado actualmente restringe `market_type=spot`; una integración equity requiere extender y migrar el modelo de forma explícita, sin relajar el guard.

## Continuidad

1. Verificar heads, PR abiertas, CI y árboles de `main`/`demo` antes de cada bloque.
2. Tras cada merge, eliminar la rama de trabajo si no es permanente y no contiene trabajo pendiente. La conexión GitHub actual no expone una acción de borrar ramas; requiere habilitar auto-delete de ramas en el repositorio o conceder una integración con permiso `delete ref`.
2. Revisar el diff y dependencias de PR; integrar automáticamente solo cuando mergeability, pruebas y CI del SHA exacto estén correctos.
3. No cerrar PR por limpieza ni fusionar ramas divergentes sin comparar árboles y clasificar funcionalidades.
4. Actualizar roadmap y handoff al completar fases.
5. Distinguir implementado, probado por CI, integrado y validado operativamente.
