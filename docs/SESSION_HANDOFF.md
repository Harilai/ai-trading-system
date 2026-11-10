# Session handoff — AI Trading System

> **Purpose:** permitir que una nueva conversación retome el desarrollo sin depender del historial del chat.
>
> **Última revisión del repositorio:** 2026-11-10  
> **Rama base revisada:** `main`  
> **Último commit observado en `main`:** `773425bd22388f9bed9666075e6d47e4f91cf5e1` — dashboard responsive (#38).  
> **Estado de ejecución:** paper trading únicamente; live trading no implementado y deshabilitado.

## 1. Cómo retomar en una conversación nueva

Antes de modificar código:

1. Leer este documento (`docs/SESSION_HANDOFF.md`).
2. Leer `docs/ROADMAP.md` y las restricciones de seguridad de `README.md`.
3. Consultar el estado actual de `main`, los últimos commits y las PR abiertas. Este documento es una guía de continuidad, no sustituye la comprobación del repositorio vivo.
4. Comparar el HEAD actual con el commit indicado arriba. Si `main` ha avanzado, revisar qué se integró y actualizar este handoff antes de continuar.
5. Continuar por **la siguiente fase pendiente** de este documento. No repetir tareas marcadas como integradas.
6. Trabajar en una rama por cambio lógico, añadir pruebas, revisar el diff y exigir CI verde antes de fusionar.

No asumir que una conversación anterior dejó cambios sin subir. El repositorio y sus PR son la fuente de verdad.

## 2. Trabajo ya completado que motivó esta continuación

- **PR #17 — Walk-forward y benchmark como gates obligatorios:** fusionada en `main` (merge commit `482b14b0b0f0af183003c49cb8d51d7e86ff6445`). Exige evidencia en tres ventanas walk-forward, operaciones cerradas por ventana, ventanas positivas, retorno medio positivo, drawdown limitado y superar buy-and-hold neto de costes. Si falta evidencia, la estrategia no se promociona.
- **PR #18 — Validación de indicadores técnicos:** fusionada en `main` (merge commit `5f2f96ba77941c28ddda7787dba29ee4fa5a39fb`). Endurece validaciones de NaN/infinito, periodos y coherencia OHLC del ATR, y añade pruebas con series numéricas de referencia.
- Después se integraron mejoras posteriores hasta la PR #38: historial/versionado de investigación, estrés de costes, diagnóstico de régimen, Sniper, cálculo de riesgo round-trip, robustez del descubrimiento de mercados, validación de entradas finitas, backups SQLite verificados y programados, y dashboard responsive.
- La PR #27 de backups quedó cerrada sin merge porque divergió de `main`; su funcionalidad fue sustituida por las PR #36 y #37, que sí están fusionadas.

## 3. Estado funcional conocido

- Backend FastAPI y cartera paper persistida en SQLite.
- Datos públicos de crypto spot mediante CCXT; Kraken es el proveedor inicial por defecto.
- Twelve Data Basic opcional para datos e investigación read-only de acciones/ETF estadounidenses.
- Scout, Charts, Strategy Research, Chief, Risk Manager y Sniper integrados.
- Backtesting cronológico 70/30, benchmark buy-and-hold, walk-forward y sensibilidad de costes.
- Worker persistente con cola durable, leases, reintentos y ciclos periódicos.
- Órdenes paper con comisión, slippage, stop-loss/take-profit e idempotencia por `client_order_id`.
- Dashboard, health/readiness, controles autenticados y backups SQLite.
- No hay ejecución real, adaptadores de órdenes live, wallets, firma blockchain, broker de acciones, derivados ni DeFi. No habilitarlos como parte de esta fase.

## 4. Siguiente fase obligatoria: ciclo de vida de órdenes y conciliación

**Prioridad inmediata.** La hoja de ruta ya identifica como pendiente completar los estados legales de órdenes, conciliación y recuperación frente a fallos en cada frontera de persistencia.

### Observación de partida

En el flujo actual de `backend/app/autonomous.py`, `decision_cycle` utiliza el ID de tarea como `client_order_id`, consulta primero si existe una operación previa y, si la encuentra, evita repetir la ejecución. `PaperPortfolio.execute()` persiste la operación, el efectivo y la posición en una transacción SQLite. Sin embargo, el registro de la operación en la cartera y el marcado de la tarea como completada en `AutonomousStore.finish_task()` son pasos separados; la evidencia completa de decisión también se guarda por separado en memoria/auditoría.

Esto ya ofrece una protección básica contra duplicados, pero no constituye todavía una máquina de estados de órdenes ni una conciliación explícita de todas las fronteras de persistencia. No dar por resuelto este punto solo porque exista idempotencia.

### Objetivo

Hacer que cada decisión ejecutable tenga un ciclo de vida durable, auditable y recuperable. La implementación debe seguir siendo paper-only.

### Plan de implementación

1. **Definir estados y transiciones legales** para una orden paper (por ejemplo: `intent_created`, `risk_approved`, `paper_filled`, `rejected`, `failed` y `reconciliation_required`). Documentar qué transiciones están permitidas y cuáles son terminales. No simular estados de broker live que aún no existen.
2. **Persistir una intención de orden antes de ejecutarla**, con `task_id` único, proveedor/instrumento, lado, cantidad solicitada, precio de referencia, estrategia, stop/take, decisión del Chief y evidencia de aprobación/rechazo del Risk Manager. No guardar secretos.
3. **Garantizar idempotencia y consistencia transaccional.** Evaluar si intención, fill paper, actualización de cartera y finalización de tarea pueden compartir una transacción SQLite; si no, definir una secuencia de recuperación explícita y segura. Mantener una restricción única por clave idempotente.
4. **Implementar conciliación al arrancar y/o al recuperar leases.** Detectar intenciones sin fill, fills persistidos con tarea aún running, tareas completadas sin resultado consistente y estados imposibles. Nunca volver a ejecutar a ciegas una orden cuyo resultado sea incierto.
5. **Añadir pruebas de inyección de fallos** en cada frontera: antes de persistir intención, después de intención, después de aprobación de riesgo, después de persistir fill/cartera y antes de finalizar la tarea; además, retry duplicado, lease expirado y reinicio del worker.
6. **Criterios de aceptación:** como máximo un fill por intención; efectivo, posiciones, trades y estado de orden coherentes; ninguna compra sin aprobación del Risk Manager y stop-loss; ninguna entrada si la conciliación es incierta; recuperación determinista después de reinicio; CI verde sobre el commit exacto.
7. Actualizar `docs/ROADMAP.md`, este handoff y `README.md` con los estados realmente implementados. No marcar una tarea como terminada antes de superar las pruebas.

### Archivos principales a revisar

- `backend/app/autonomous.py`
- `backend/app/portfolio.py`
- `backend/app/agents/contracts.py`
- `backend/app/agents/risk.py`
- `backend/app/agent_api.py`
- `backend/tests/test_autonomous.py`
- `backend/tests/test_portfolio.py`
- `backend/tests/test_chief.py`

No modificar todos estos archivos por obligación: limitar el cambio al mínimo necesario tras inspeccionar el flujo completo.

## 5. Fases posteriores — no adelantar a la fase inmediata

Una vez completado y validado el ciclo de órdenes:

1. **Capital y retiradas:** mínimos, costes y tiempos de retirada por proveedor; recalcular capital disponible y límites de riesgo después de retiradas. Las claves de trading nunca tendrán permisos de retirada.
2. **Acciones/ETF:** diseñar soporte de cartera multi-divisa, FX verificable, horarios, reglas de mercado y Risk Manager específico antes de permitir que Chief considere esos activos. Mantenerlo read-only hasta validar toda la cadena.
3. **Más fuentes de señal:** News/Sentiment con fuentes atribuibles, timestamps, frescura y controles contra datos manipulados.
4. **Dashboard:** vistas detalladas de investigación, historial de estrategias, curva de equity y retiradas; solo mostrar métricas que estén calculadas y verificadas.
5. **Operación VPS:** despliegue, TLS/reverse proxy, copia off-host, monitorización y medición de disponibilidad real. No exponer el puerto API directamente a Internet.
6. **Nuevos mercados:** wallets read-only y monitorización blockchain antes de cualquier firma; DEX, FX, índices, commodities y derivados solo con adaptadores, riesgos y conciliación específicos.

## 6. Límites que no se deben saltar

- Mantener paper trading como único modo operativo.
- Live trading no puede activarse desde agentes, LLM, dashboard ni configuración accidental.
- No solicitar ni guardar seeds, claves privadas ni credenciales en Git, logs o frontend.
- No dar permisos de retirada a claves de trading.
- Solo crypto spot EUR está habilitado para decisiones y órdenes paper en la implementación actual.
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
