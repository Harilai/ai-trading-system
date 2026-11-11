# Session handoff — AI Trading System

> **Última revisión:** 2026-11-11 Europe/Madrid
> **Repositorio:** `Harilai/ai-trading-system`
> **Último cambio de código integrado:** PR #109 en `main`; sincronización de ramas PR #110.
> **`main`:** `cbbd1510476a1ddb165aa70b3d027c0816ce9598`
> **`demo`:** `7cc1f0e73f54e09e0f911ab5314db1d65bd1ce3b`
> **Árboles:** idénticos, 109 archivos y 109 blob SHAs coincidentes.
> **Operación:** paper trading y Binance Spot Demo aislado; live trading no implementado ni habilitado.

## Trabajo cerrado en esta sesión

- PR #109 fusionada en `main`: diagnóstico seguro de asset keys inválidas en balances Binance Demo. El error muestra tipo y representación ASCII escapada/acotada de la clave, sin importes, credenciales ni payload completo.
- PR #110 fusionada en `demo`: sincroniza el fix de #109. Se compararon los árboles recursivos completos antes de abrirla: 109 archivos en cada rama, sin rutas exclusivas y solo dos blobs distintos (`backend/app/accounts/binance_demo.py`, `backend/tests/test_binance_demo.py`). Tras merge, los árboles vuelven a ser idénticos.
- CI de PR #110 pasó en el head exacto `cbbd1510476a1ddb165aa70b3d027c0816ce9598`: `Python tests` y `Docker build and runtime hardening` correctos, incluida validación Compose, build de imagen y ejecución non-root.
- No hay PR abiertas tras integrar #110.
- Las funcionalidades #78–#80 (contratos de órdenes/fills, ledger y completitud/reconciliación read-only), #99 (ledger de posiciones) y #100 (diagnósticos experimentales y salidas seguras) están integradas en `main` y `demo`.
- No se ha ejecutado un preflight posterior a #109 contra el contenedor Binance Demo local. El problema original de asset key sigue pendiente de confirmar con evidencia del contenedor del propietario.

## Estado operativo Binance Demo

- El preflight automatizado de solo lectura está implementado (#103); realiza tres GET autenticados y verifica identidad Demo, capacidades y catálogo.
- El fix #109 mejora el diagnóstico del error de balance.
- Sigue pendiente ejecutar el preflight en el entorno local del propietario. No pedir ni recibir API keys ni tokens por chat.
- No ejecutar el drill de órdenes hasta que el preflight pase. El smoke test de órdenes Demo requiere autorización explícita del propietario y debe usar exclusivamente el host Demo; no se debe cambiar a producción.
- Ningún test de CI sustituye la validación operativa contra la cuenta Demo real.

## Snapshot coordinado — implementado en PR #113, pendiente de merge

La implementación está en `docs/COORDINATED_ACCOUNT_SNAPSHOT_DESIGN.md` y `backend/app/accounts/coordinated_snapshot_store.py`. Los stores de balances y posiciones ahora admiten inserts dentro de una transacción compartida. El coordinador crea referencias a ambos registros y el grupo en una sola transacción SQLite, sin duplicar los datos. También se añadió aislamiento de balance snapshots por entorno con migración legacy a `production`, idempotencia por request ID y API autenticada.

CI sobre el SHA exacto `61f3240c3afc3fce3c1cf75597a850e45fd50a78`: 581 tests passed, 1 warning; Docker build/runtime hardening pasó, incluida validación Compose y ejecución non-root. No se ejecutó test local en el entorno del propietario.

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

## Continuidad

1. Verificar heads, PR abiertas, CI y árboles de `main`/`demo` antes de cada bloque.
2. Tras cada merge, eliminar la rama de trabajo si no es permanente y no contiene trabajo pendiente. La conexión GitHub actual no expone una acción de borrar ramas; requiere habilitar auto-delete de ramas en el repositorio o conceder una integración con permiso `delete ref`.
2. Revisar el diff y dependencias de PR; integrar automáticamente solo cuando mergeability, pruebas y CI del SHA exacto estén correctos.
3. No cerrar PR por limpieza ni fusionar ramas divergentes sin comparar árboles y clasificar funcionalidades.
4. Actualizar roadmap y handoff al completar fases.
5. Distinguir implementado, probado por CI, integrado y validado operativamente.
