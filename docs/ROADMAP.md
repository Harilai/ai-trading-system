# Engineering roadmap

> Continuity between ChatGPT sessions: read [`docs/SESSION_HANDOFF.md`](SESSION_HANDOFF.md) for project state and [`docs/AGENT_WORKFLOW.md`](AGENT_WORKFLOW.md) for the mandatory multi-agent development method.

Actualizado: 2026-11-10. Este documento distingue lo implementado y validado de lo que sigue pendiente. Una tarea no se considera terminada solo por existir código o pruebas: debe superar CI y revisión funcional.

## Objetivo de producto

Construir una plataforma algorítmica multiactivo y multi-venue, preparada para capital real y extensible a los instrumentos accesibles mediante los proveedores conectados. El universo objetivo no se limita a crypto spot ni a EUR: incluye crypto CEX/DEX, acciones, ETF, FX, futuros, opciones, bonos, materias primas y otros instrumentos cuando existan adaptador, elegibilidad, datos, contabilidad y perfil de riesgo adecuados.

"Operar cualquier cosa del mercado" significa poder integrar progresivamente los instrumentos que permitan los proveedores y la cuenta; no implica disponibilidad universal. Discovery, market data, paper y live execution son capacidades separadas. Ningún instrumento se habilita automáticamente por aparecer en el catálogo.

La arquitectura objetivo y los controles obligatorios están en [MARKET_EXPANSION_ARCHITECTURE.md](MARKET_EXPANSION_ARCHITECTURE.md).

## Siguiente secuencia de desarrollo acordada

1. **Completado — cartera paper multidivisa para acciones/ETF (PR #41):** efectivo EUR/USD separado, conversión FX explícita y auditable, valoración y riesgo consolidados en EUR. Las órdenes equity siguen siendo manuales y paper-only; el Chief no opera equities autónomamente.
2. **Completado — ciclo de vida y conciliación paper (PR #44):** intentos persistentes, fills idempotentes, conciliación contra el ledger, recuperación de tareas con leases y caducidad de aprobaciones stale.
3. **Completado — backups externos y restauración (PR #48):** replicación S3-compatible verificada, status API y descarga/validación de restore candidate. La configuración real del proveedor sigue pendiente en el entorno operativo.
4. **Completado en PR #50 — dashboard ampliado:** velas OHLC, salud/readiness de conectores, investigación de acciones/ETF, historial de estrategias, intentos/fills paper, backup status y gestión contable de retiradas paper. No hay transferencia real.
5. **Completado — contrato normalizado de instrumentos v2 (PR #52):** venue/native symbol, precision mode, tick/step, amount/cost bounds, contract size, settlement, expiry, strike, option right, underlying y trading status. No añade aún ejecución de nuevos mercados.
6. **Completado — catálogo persistente y venue-aware identity (PR #54):** snapshots, first/last seen, filtros, discovery history, fallos sanitizados e identidad canónica paralela al ID histórico.
7. **Completado — preparación de operación 24/7 (PR #56):** healthcheck de backups, reintentos, rotación de logs, runbook VPS y systemd unit. El VPS real aún no está desplegado.
8. **Completado — Binance Spot read-only (PR #57):** consulta autenticada de balances con permisos mínimos, sin órdenes ni retiradas; requiere configurar una API key read-only en el servidor.
9. **Completado — account snapshot ledger y conciliación read-only (PR #59):** historial inmutable por cuenta/venue, snapshots por activo, deltas exactos y separación de la cartera paper.
10. **Completado — Binance Spot Demo Trading connector (PR #61):** CCXT Demo mode, verificación estricta de `demo-api.binance.com`, balances virtuales y discovery Spot.
11. **Completado — lifecycle de órdenes Binance Spot Demo (PR #63):** limit orders, intents persistentes, clientOrderId, timeout/unknown-state reconciliation, partial fills, cancelación, auditoría append-only y fail-closed. No existe fallback a producción.
12. **Siguiente — smoke tests operativos con una cuenta Binance Demo real:** seguir `docs/BINANCE_DEMO_SMOKE_TEST_RUNBOOK.md` y verificar respuestas reales, filtros/precisión, partial fills, cancelación, timeout y reconciliación tras restart. Credenciales Demo solo en el servidor; no se simula una prueba real si el propietario no ha configurado la cuenta.
13. **Completado en PR #66 — ledger idempotente de fills individuales y comisiones:** trade IDs, order ID, cantidad, precio, quote cost, comisión/activo, timestamp, persistencia append-only y bloqueo fail-closed hasta cubrir el fill acumulado.
14. **Completado en PR #68 — reconciliación periódica Demo:** polling de órdenes abiertas/unknown y fills con leases persistentes, fencing, backoff, lotes acotados y recuperación tras restart; funciona con estrategia pausada y nunca reenvía órdenes.
15. **Completado — contratos normalizados de cuenta (PR #71):** registry multi-account, permisos y balances normalizados.
16. **Completado — adaptador genérico CCXT read-only (PR #72):** CEX Spot configurable por exchange ID, Kraken por defecto; consulta balances sin métodos de escritura.
17. **Completado — contrato y endpoint de posiciones normalizadas (PR #73):** snapshot tipado por proveedor/cuenta/entorno, validación decimal e identidad de instrumento y capability gate. Los adaptadores actuales siguen declarando `positions=false`; endpoint preparado para proveedores que implementen lectura de posiciones.
18. **En curso — ledger persistente de posiciones multi-venue (PR #74 draft):** snapshots append-only por proveedor/cuenta/instrumento, diferencias sin asumir ausencias como cero, coste/mark/P&L y divisa con procedencia y frescura; rebase contra el `main` actualizado tras PR #75 y completar revisión/CI antes de marcar ready.
19. **Completado en PR #75 — demo local paper:** Compose aislado con 10.000 EUR virtuales, datos públicos Kraken, clave Twelve Data opcional solo para cotizaciones, sin credenciales de cuenta ni ruta de ejecución. CI: 460 tests Python y validación/build Docker correctos en el SHA `7abd5f527612d0c593e93d8a94852cc29d0a72d8`.
20. Supervisión paper equity y operación 24/7: stops/take-profit equity monitorizados, VPS real, proveedor de backups externo configurado y drills de restauración.
21. Live controlado por venue/clase: solo tras smoke tests, sandbox, conciliación, pruebas adversas, revisión de seguridad y habilitación explícita del propietario. Sigue fuera del alcance de esta fase.


## Estado integrado

| Área | Estado |
|---|---|
| Contratos normalizados de account providers | Integrado en PR #71 (`a7a9bb8`); 433 tests, Docker hardening y endpoints genéricos autenticados |
| Adaptador CCXT read-only multi-CEX | Integrado en PR #72 (`c44bc97`); Spot balance reads only, sin operaciones de escritura |
| Contrato y endpoint de posiciones normalizadas | Integrado en PR #73 (`d9fefaf`); CI Python y Docker correctos; capability gate, sin adaptador de lectura real conectado |
| Adaptador CCXT read-only multi-CEX | Integrado en PR #72; balance Spot only, API key scope no verificado por software, sin operaciones de escritura |
| Catálogo persistente de instrumentos | Implementado y fusionado en PR #54 (`b9dbba5`); 323 tests y Docker hardening correctos |
| Preparación operativa 24/7 | Implementada en PR #56 (`c892ff3`); 330 tests y Docker hardening correctos; VPS pendiente |
| Binance Spot account connector (read-only) | Implementado en PR #57 (`8773d75`); 345 tests y Docker hardening correctos; API key no configurada |
| Snapshots y conciliación read-only de cuentas | Implementado en PR #59 (`b3cca3d`); 360 tests y Docker hardening correctos; no altera cartera paper |
| Binance Spot Demo Trading | Implementado en PR #61; balances virtuales y discovery Spot. PR #63 añade limit orders Demo con lifecycle persistente; desactivado por defecto y sin ruta de producción |
| Binance Spot Demo order lifecycle | Implementado y fusionado en PR #63 (`013f44f`); 400 tests y Docker hardening correctos; Demo-only, limit orders y cap 25 quote por defecto |
| Binance Spot Demo individual fill/fee ledger | Implementado y fusionado en PR #66 (`0f667db`); 410 tests y Docker hardening correctos; append-only, idempotente y fail-closed ante evidencia incompleta |
| Binance Spot Demo periodic reconciliation | Implementado y fusionado en PR #68 (`8943fde`); 420 tests y Docker hardening correctos; SQLite lease fencing, backoff y worker independiente del estado de estrategias |



| Contrato normalizado de instrumentos v2 | Implementado y fusionado en PR #52 (`1d6ad47`); 312 tests y Docker hardening correctos; no habilita trading de derivados |
| Dashboard de supervisión y operaciones paper | Implementado y fusionado en PR #50 (`848d1d3`); 305 tests y Docker hardening correctos |
| Backups externos S3-compatible y restore candidate verificado | Implementado y fusionado en PR #48 (`ed08cec`); off-host desactivado por defecto hasta configurar `.env.backup`; 304 tests y Docker hardening correctos |
| Ciclo de vida y conciliación de órdenes paper | Implementado y fusionado en PR #44 (`df76011`); 292 tests y Docker hardening correctos |
| Cartera paper multidivisa EUR/USD, conversiones FX y manual equity/ETF paper | Implementado y fusionado en PR #41 (`23cfcb5`); 270 tests y Docker hardening correctos |
| Backend FastAPI y paper portfolio SQLite | Implementado |
| Datos públicos crypto spot vía CCXT | Implementado; solo datos públicos |
| Datos públicos US equities/ETF vía Twelve Data Basic | Adaptador de datos/FX; órdenes manuales paper locales habilitadas para cash equity/ETF USD; sin broker ni ejecución real |
| Investigación técnica y backtesting inicial de acciones/ETF | Pipeline manual y tarea autónoma periódica de solo lectura implementados; sin propuestas ni órdenes |
| Contratos normalizados de proveedores e instrumentos | Implementado |
| Identidad de posiciones por `provider_id + instrument_id` | Implementado; migración SQLite incluida |
| Aislamiento de posiciones con mismo símbolo entre proveedores | Implementado y probado |
| Valoración de cartera por instrumento y proveedor | Implementado |
| Registro de proveedores y capability matrix | Implementado |
| Descubrimiento multi-provider para adaptadores configurados | Implementado en datos; actualmente un proveedor por defecto |
| Router de ejecución normalizado | Contrato inicial; ningún adaptador live |
| Perfiles de riesgo por clase/tipo de mercado | Crypto spot autónomo y cash equity/ETF USD manual-only paper habilitados; margin/derivados deshabilitados |
| Indicadores técnicos | Implementado |
| Contabilidad de comisiones en coste base y P/L realizado | Implementado |
| Validación de datos obsoletos y órdenes | Implementado |
| Worker persistente, cola, leases y reintentos | Implementado; readiness comprueba DB y heartbeat reciente |
| Programación periódica de decision_cycle sin solapamientos | Implementada; 5 min por defecto, solo con worker habilitado |
| Recuperación de leases expirados en ejecución | Implementado; el worker registra fallos del bucle y continúa tras errores transitorios |
| API de control y órdenes paper autenticadas | Implementadas con token; Docker no-root, rootfs read-only, capacidades eliminadas y bind local por defecto |
| Scout: descubrimiento de spot EUR por volumen/spread/frescura | Implementado |
| Charts Agent | Implementado |
| Strategy Research: EMA, RSI, Bollinger, split 70/30, OHLCV validado, benchmark buy-and-hold y gates walk-forward | Implementado como filtro de investigación; no habilita ejecución |
| Chief: selección de oportunidad y propuesta buy/sell/hold | Implementado con Risk Manager obligatorio |
| Chief Agent y coordinación de decisiones | Implementado; ciclo ejecuta paper bajo gate de riesgo |
| Risk Manager conectado obligatoriamente a órdenes paper | Implementado; crypto spot EUR autónomo y cash equities/ETF USD manual-only paper con límites propios |
| Sniper y generación de propuestas | Implementado; propuestas deterministas bajo Risk Manager obligatorio |
| Ejecución paper desde `decision_cycle` y monitorización de stop/take | Implementada para crypto spot EUR; monitor equity stop/take pendiente |
| Datos de acciones/ETF estadounidenses | Adaptador Twelve Data Basic y pipeline autónomo read-only; órdenes manuales paper USD integradas, sin broker ni ejecución real |
| Ejecución real multi-venue (CEX/broker/DEX/wallet) | No implementada; requiere adaptadores autenticados, elegibilidad, sandbox y gates de seguridad |
| DEX, wallets, blockchain y DeFi | Pendiente; sin conexión, firma ni ejecución |
| Futuros, opciones, margin y derivados | Deshabilitados; perfiles y límites específicos pendientes |
| News/Sentiment con fuentes auditables | Pendiente |
| Valoración a mercado, P/L realizado/no realizado | Implementado en cartera paper; depende de cotización fresca |
| Reserva 50/50 de beneficio neto realizado | Implementada como cálculo de reserva; política provisional configurable pendiente |
| Solicitud/aprobación/cancelación manual de retiradas paper | Implementada solo sobre efectivo EUR; desglose auditado entre beneficio reservado y capital no reservado; sin liquidación automática |
| Dashboard | MVP responsive integrado: cartera, posiciones, operaciones, estado/heartbeat, tareas y controles autenticados; gráficos avanzados y gestión de retiradas siguen pendientes |
| Infraestructura gratuita/Oracle Always Free y despliegue 24/7 | Pendiente |
| Live trading | No implementado; deshabilitado |

## Prioridad 1 — calidad de datos y estrategia

- [x] Rotar de forma persistente el muestreo estratificado del catálogo de acciones/ETF para cubrir progresivamente el universo sin exceder cuotas.
- [x] Añadir pruebas de descubrimiento con tickers ausentes/malformados/no finitos, mercados inactivos, cotizaciones stale/futuras, spreads extremos y volúmenes inválidos; los payloads defectuosos se descartan sin abortar el universo.
- [x] Rechazar OHLCV no finito, volúmenes negativos, timestamps duplicados/desordenados y velas OHLC inconsistentes antes del backtest.
- [x] Verificar SMA, EMA, RSI, MACD, Bollinger y ATR con series de referencia; rechazar valores no finitos y parámetros inválidos.
- [x] Añadir benchmark buy-and-hold OOS con la misma fracción de exposición y retorno excedente por estrategia.
- [x] Añadir escenarios de sensibilidad de comisiones y slippage a 1,5x y 2x; exigir rentabilidad neta y superar buy-and-hold en 1,5x para promocionar, mantener 2x como diagnóstico.
- [x] Añadir tres ventanas walk-forward diagnósticas y métricas por ventana.
- [x] Exigir tres ventanas walk-forward con mínimo de 40 velas evaluables y una operación cerrada por ventana, al menos dos ventanas positivas, media positiva, drawdown limitado y superar buy-and-hold neto de costes; devolver motivos de rechazo.
- [x] Persistir por instrumento las versiones/configuración de estrategia, resultados OOS, benchmark, walk-forward, sensibilidad de costes y motivos de promoción/rechazo; escritura idempotente por tarea.
- [x] Añadir diagnóstico de estructura (tendencia alcista/bajista/rango) mediante efficiency ratio y volatilidad relativa mediante percentil rolling; se persiste como contexto y no modifica gates ni autoriza operaciones.

## Prioridad 2 — orquestación segura

- [x] Implementar Chief con contratos tipados y decisiones auditables.
- [x] Programar ciclos periódicos cada 5 minutos cuando el worker está habilitado.
- [x] Integrar Scout, Charts y Strategy Research en una tarea de decisión bajo demanda; programación recurrente pendiente.
- [x] Implementar Sniper determinista separado para proponer entrada, stop-loss, take-profit y tamaño según ATR, presupuesto de riesgo neto de costes estimados de entrada/salida, asignación máxima y efectivo; Risk Manager sigue siendo el gate obligatorio.
- [x] Conectar Risk Manager como puerta obligatoria para propuestas del Chief y órdenes paper.
- [x] Añadir idempotencia de órdenes paper mediante `client_order_id` vinculado a la tarea.
- [x] Completar estados legales de órdenes paper (PR #44) y lifecycle limit-order Binance Spot Demo (PR #63), con idempotencia, reconciliación y fail-closed.
- [x] Añadir reconciliación periódica automática de órdenes Demo abiertas/unknown tras restart, con lease, rate limit y sin reenvío ciego.
- [ ] Añadir monitorización paper de stop-loss/take-profit equity con cotizaciones USD y FX frescas, cierres idempotentes y recuperación tras reinicio.
- [x] Implementar paper execution y monitorización de posiciones con pruebas de extremo a extremo.
- [ ] Mantener el sistema en hold/cash si no existe una oportunidad validada.

## Prioridad 3 — contabilidad y control del capital

- [x] Añadir valoración a mercado y separar coste base, valor actual, P/L realizado y no realizado.
- [x] Calcular reserva del 50% de beneficios netos realizados y reinversión del 50% (valor por defecto provisional).
- [x] Permitir solicitudes manuales sobre cualquier efectivo disponible, con aprobación explícita, reserva de efectivo para solicitudes pendientes y desglose entre beneficio reservado y capital no reservado.
- [ ] Calcular mínimos y costes de retirada según proveedor; nunca conceder permisos de retirada a claves de trading.
- [ ] Recalcular riesgo y capital viable tras una retirada.
- [x] Persistir fills individuales de Binance Demo con trade ID, cantidad, precio, comisión y activo de comisión; reconciliar idempotentemente contra el exchange antes de usar balances Demo como evidencia contable.

## Prioridad 4 — cobertura y producto

- [x] Implementar adaptador de datos Twelve Data Basic para catálogo, cotizaciones y OHLCV de acciones/ETF estadounidenses (sin órdenes).
- [x] Implementar pipeline manual de investigación de acciones/ETF con frescura, divisa USD, bid/ask, Charts y backtesting 70/30.
- [x] Integrar investigación de acciones/ETF en el worker con tarea read-only independiente cada 6 horas, muestreo acotado, cuotas persistentes, estado de mercado, liquidez y calidad de cotización.
- [ ] Integrar acciones/ETF en decisiones del Chief y Risk Manager solo después de validar estrategia, capital, moneda y reglas de cartera específicas; sin ejecución por ahora.
- [ ] Implementar y validar un adaptador de broker de acciones/ETF, horarios, permisos, datos, mínimos y comisiones.
- [x] Generalizar el portfolio a claves `provider_id + instrument_id`, con migración de datos legacy y aislamiento de símbolos duplicados.
- [ ] Completar conciliación multi-proveedor de órdenes, fills parciales y estados de ejecución (PR #78 añade los contratos normalizados; aún faltan adaptadores, ledger compartido y worker de conciliación).
- [x] Añadir contrato y endpoint autenticado para posiciones normalizadas, exigiendo capacidad explícita y sin sintetizarlas desde balances.
- [ ] Persistir snapshots append-only de posiciones por cuenta/instrumento y conciliar sus cambios con coste, mark, P&L y divisa trazables.
- [ ] Implementar adaptadores FX, índices y commodities solo tras validar APIs y elegibilidad.
- [ ] Diseñar wallet read-only y blockchain monitoring antes de añadir firma aislada; no usar seeds en agentes.
- [ ] Implementar DEX swaps con simulación, gas, slippage, MEV y reconciliación antes de habilitar ejecución.
- [ ] Mantener margin, futures, options y apalancamiento deshabilitados hasta completar sus perfiles y pruebas independientes.
- [ ] Añadir FX con tipos y timestamps verificables antes de mezclar divisas.
- [ ] Añadir News/Sentiment solo con atribución de fuente, frescura y calidad.
- [x] Crear dashboard responsive MVP con cartera, posiciones, operaciones, heartbeat, tareas y controles autenticados; pendientes gráficos avanzados, vistas detalladas de investigación y retiradas.
- [ ] Añadir PostgreSQL/migraciones cuando la carga lo justifique.

## Prioridad 5 — despliegue y operación

- [ ] Evaluar Oracle Cloud Always Free y alternativas en el momento del despliegue; no asumir disponibilidad permanente.
- [ ] Mantener operación sin APIs de inferencia de pago; LLM local opcional y lógica determinista como fallback.
- [x] Añadir liveness y readiness con verificación SQLite y heartbeat del worker, integrado en el healthcheck Docker.
- [x] Añadir backup SQLite online con integrity_check, restauración con copia previa, servicio Docker aislado de backup diario y replicación S3-compatible cifrada con verificación SHA-256. Configuración del proveedor y restore drills en el VPS siguen pendientes.
- [x] Proteger el endpoint manual de órdenes paper con token y gate obligatorio del Risk Manager; compras con stop-loss explícito.
- [x] Ejecutar Docker como usuario no root, rootfs read-only, eliminar capacidades y enlazar el puerto a localhost por defecto.
- [ ] Configurar TLS/reverse proxy para acceso remoto; no exponer directamente el puerto API.
- [ ] Medir disponibilidad real antes de afirmar que funciona 24/7.

## Seguridad no negociable

- Paper trading hasta completar pruebas y revisión separada de live execution.
- Un provider solo se considera disponible si está registrado, sus capacidades están declaradas y se han probado; no inferir ejecución desde datos públicos.
- Las capacidades no implementadas por venue deben permanecer false. Binance Spot Demo admite únicamente limit orders; Binance de producción sigue read-only. Transfers, withdrawals, wallet signing y live execution permanecen deshabilitados.
- Live trading deshabilitado por defecto y nunca activable por agentes o LLM.
- Ningún agente puede cambiar sus propios límites de riesgo.
- No incluir claves de exchange/broker en Git, logs ni frontend.
- Las claves de trading no tendrán permisos de retirada.
- Si la calidad de datos, el estado del proveedor o la conciliación son inciertos, se bloquean nuevas entradas.
- No prometer rentabilidad ni seleccionar estrategias solo por resultados in-sample.

## Pruebas y definición de terminado

Cada fase debe incluir:
1. Tests unitarios de lógica y límites.
2. Tests de integración con proveedores simulados.
3. Tests de fallos, datos stale, rate limits, reinicios y duplicados.
4. CI verde sobre el commit exacto que se propone fusionar.
5. Revisión de diferencias y actualización de documentación.

No se fusionará código con CI fallida o con una protección financiera no probada.
