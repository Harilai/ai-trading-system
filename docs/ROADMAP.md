# Engineering roadmap

> Continuity between ChatGPT sessions: read [`docs/SESSION_HANDOFF.md`](SESSION_HANDOFF.md) for project state and [`docs/AGENT_WORKFLOW.md`](AGENT_WORKFLOW.md) for the mandatory multi-agent development method.

Actualizado: 2026-11-10. Este documento distingue lo implementado y validado de lo que sigue pendiente. Una tarea no se considera terminada solo por existir código o pruebas: debe superar CI y revisión funcional.

## Siguiente secuencia de desarrollo acordada

1. **Cartera paper multidivisa para acciones/ETF:** efectivo EUR/USD separado, conversión FX paper explícita y auditable, valoración y riesgo consolidados en EUR. El soporte manual paper de equities se está validando en PR #41; el Chief sigue read-only para equities.
2. **Conciliación de órdenes y fills:** estados legales, idempotencia, recuperación y fallos entre límites transaccionales.
3. **Backups externos:** copia fuera del volumen/servidor Docker y restauración verificada.
4. **Dashboard ampliado:** gráficos, investigación y retiradas.
5. **Despliegue persistente 24/7:** VPS, seguridad, monitorización y recuperación.

## Estado integrado

| Área | Estado |
|---|---|
| Cartera paper multidivisa EUR/USD, conversiones FX y manual equity/ETF paper | En validación en PR #41; no integrado hasta CI/revisión |\n| Backend FastAPI y paper portfolio SQLite | Implementado |
| Datos públicos crypto spot vía CCXT | Implementado; solo datos públicos |
| Datos públicos US equities/ETF vía Twelve Data Basic | Adaptador implementado y probado; API key gratuita opcional; solo datos, sin trading |
| Investigación técnica y backtesting inicial de acciones/ETF | Pipeline manual y tarea autónoma periódica de solo lectura implementados; sin propuestas ni órdenes |
| Contratos normalizados de proveedores e instrumentos | Implementado |
| Identidad de posiciones por `provider_id + instrument_id` | Implementado; migración SQLite incluida |
| Aislamiento de posiciones con mismo símbolo entre proveedores | Implementado y probado |
| Valoración de cartera por instrumento y proveedor | Implementado |
| Registro de proveedores y capability matrix | Implementado |
| Descubrimiento multi-provider para adaptadores configurados | Implementado en datos; actualmente un proveedor por defecto |
| Router de ejecución normalizado | Contrato inicial; ningún adaptador live |
| Perfiles de riesgo por clase/tipo de mercado | Contratos implementados; solo crypto spot habilitado |
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
| Risk Manager conectado obligatoriamente a órdenes paper | Implementado; crypto spot EUR autónomo y cash equities/ETF USD manual-only paper en PR #41 |
| Sniper y generación de propuestas | Pendiente |
| Ejecución paper desde `decision_cycle` y monitorización de stop/take | Implementada; ciclo periódico cada 5 min al reanudar worker |
| Datos de acciones/ETF estadounidenses | Adaptador Twelve Data Basic y pipeline autónomo read-only integrados; órdenes manuales paper USD en PR #41, sin broker ni ejecución real |
| Broker de acciones/ETF y ejecución | Pendiente; requiere broker adapter, elegibilidad y validación |
| DEX, wallets, blockchain y DeFi | Pendiente; no hay integración ni firma |
| Futuros, opciones, margin y derivados | Deshabilitados; perfiles y límites específicos pendientes |
| News/Sentiment con fuentes auditables | Pendiente |
| Valoración a mercado, P/L realizado/no realizado | Implementado en cartera paper; depende de cotización fresca |
| Reserva 50/50 de beneficio neto realizado | Implementada como cálculo de reserva; política provisional configurable pendiente |
| Solicitud/aprobación/cancelación manual de retiradas paper | Implementada; cualquier efectivo disponible, con desglose auditado entre beneficio reservado y capital no reservado; sin liquidación automática |
| Dashboard | MVP responsive integrado: cartera, posiciones, operaciones, estado/heartbeat, tareas y controles autenticados; gráficos avanzados y gestión de retiradas siguen pendientes |
| Infraestructura gratuita/Oracle Always Free y despliegue 24/7 | Pendiente |
| Live trading | No implementado; deshabilitado |

## Prioridad 1 — calidad de datos y estrategia

- [x] Rotar de forma persistente el muestreo estratificado del catálogo de acciones/ETF para cubrir progresivamente el universo sin exceder cuotas.
- [x] Añadir pruebas de descubrimiento con tickers ausentes/malformados/no finitos, mercados inactivos, cotizaciones stale/futuras, spreads extremos y volúmenes inválidos; los payloads defectuosos se descartan sin abortar el universo.
- [x] Rechazar OHLCV no finito, volúmenes negativos, timestamps duplicados/desordenados y velas OHLC inconsistentes antes del backtest.\n- [x] Verificar SMA, EMA, RSI, MACD, Bollinger y ATR con series de referencia; rechazar valores no finitos y parámetros inválidos.
- [x] Añadir benchmark buy-and-hold OOS con la misma fracción de exposición y retorno excedente por estrategia.\n- [x] Añadir escenarios de sensibilidad de comisiones y slippage a 1,5x y 2x; exigir rentabilidad neta y superar buy-and-hold en 1,5x para promocionar, mantener 2x como diagnóstico.
- [x] Añadir tres ventanas walk-forward diagnósticas y métricas por ventana.\n- [x] Exigir tres ventanas walk-forward con mínimo de 40 velas evaluables y una operación cerrada por ventana, al menos dos ventanas positivas, media positiva, drawdown limitado y superar buy-and-hold neto de costes; devolver motivos de rechazo.
- [x] Persistir por instrumento las versiones/configuración de estrategia, resultados OOS, benchmark, walk-forward, sensibilidad de costes y motivos de promoción/rechazo; escritura idempotente por tarea.
- [x] Añadir diagnóstico de estructura (tendencia alcista/bajista/rango) mediante efficiency ratio y volatilidad relativa mediante percentil rolling; se persiste como contexto y no modifica gates ni autoriza operaciones.

## Prioridad 2 — orquestación segura

- [x] Implementar Chief con contratos tipados y decisiones auditables.
- [x] Programar ciclos periódicos cada 5 minutos cuando el worker está habilitado.
- [x] Integrar Scout, Charts y Strategy Research en una tarea de decisión bajo demanda; programación recurrente pendiente.
- [x] Implementar Sniper determinista separado para proponer entrada, stop-loss, take-profit y tamaño según ATR, presupuesto de riesgo neto de costes estimados de entrada/salida, asignación máxima y efectivo; Risk Manager sigue siendo el gate obligatorio.
- [x] Conectar Risk Manager como puerta obligatoria para propuestas del Chief y órdenes paper.
- [x] Añadir idempotencia de órdenes paper mediante `client_order_id` vinculado a la tarea.
- [ ] Completar estados legales de órdenes, conciliación y recuperación frente a fallos en cada frontera de persistencia.
- [x] Implementar paper execution y monitorización de posiciones con pruebas de extremo a extremo.
- [ ] Mantener el sistema en hold/cash si no existe una oportunidad validada.

## Prioridad 3 — contabilidad y control del capital

- [x] Añadir valoración a mercado y separar coste base, valor actual, P/L realizado y no realizado.
- [x] Calcular reserva del 50% de beneficios netos realizados y reinversión del 50% (valor por defecto provisional).
- [x] Permitir solicitudes manuales sobre cualquier efectivo disponible, con aprobación explícita, reserva de efectivo para solicitudes pendientes y desglose entre beneficio reservado y capital no reservado.
- [ ] Calcular mínimos y costes de retirada según proveedor; nunca conceder permisos de retirada a claves de trading.
- [ ] Recalcular riesgo y capital viable tras una retirada.

## Prioridad 4 — cobertura y producto

- [x] Implementar adaptador de datos Twelve Data Basic para catálogo, cotizaciones y OHLCV de acciones/ETF estadounidenses (sin órdenes).
- [x] Implementar pipeline manual de investigación de acciones/ETF con frescura, divisa USD, bid/ask, Charts y backtesting 70/30.
- [x] Integrar investigación de acciones/ETF en el worker con tarea read-only independiente cada 6 horas, muestreo acotado, cuotas persistentes, estado de mercado, liquidez y calidad de cotización.\n- [ ] Integrar acciones/ETF en decisiones del Chief y Risk Manager solo después de validar estrategia, capital, moneda y reglas de cartera específicas; sin ejecución por ahora.
- [ ] Implementar y validar un adaptador de broker de acciones/ETF, horarios, permisos, datos, mínimos y comisiones.
- [x] Generalizar el portfolio a claves `provider_id + instrument_id`, con migración de datos legacy y aislamiento de símbolos duplicados.
- [ ] Completar conciliación multi-proveedor de órdenes, fills parciales y estados de ejecución.
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
- [x] Añadir liveness y readiness con verificación SQLite y heartbeat del worker, integrado en el healthcheck Docker.\n- [x] Añadir backup SQLite online con integrity_check, restauración con copia previa y servicio Docker aislado de backup diario con retención local de 14 días; copia off-host sigue pendiente.
- [x] Proteger el endpoint manual de órdenes paper con token y gate obligatorio del Risk Manager; compras con stop-loss explícito.\n- [x] Ejecutar Docker como usuario no root, rootfs read-only, eliminar capacidades y enlazar el puerto a localhost por defecto.\n- [ ] Configurar TLS/reverse proxy para acceso remoto; no exponer directamente el puerto API.
- [ ] Medir disponibilidad real antes de afirmar que funciona 24/7.

## Seguridad no negociable

- Paper trading hasta completar pruebas y revisión separada de live execution.
- Un provider solo se considera disponible si está registrado, sus capacidades están declaradas y se han probado; no inferir ejecución desde datos públicos.
- Las capacidades no implementadas (balances, órdenes, cancelación, partial fills, reconciliación, withdrawals, signing) deben permanecer false.
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
