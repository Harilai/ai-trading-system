# ADR — proveedor read-only de posiciones: evaluación inicial de Alpaca

- Estado: adaptador de posiciones implementado en PR en revisión; no aprobado operativamente ni conectado a cartera/trading.
- Fecha: 2026-11-11.
- Alcance: lectura de posiciones abiertas para acciones/ETF estadounidenses, inicialmente en Paper.

## Evidencia del proveedor

La documentación oficial de Alpaca describe `GET https://paper-api.alpaca.markets/v2/positions` como endpoint para recuperar todas las posiciones abiertas de la cuenta. El objeto incluye `asset_id`, `symbol`, `asset_class`, `qty`, `side`, `avg_entry_price`, `market_value` y `unrealized_pl`. La documentación indica expresamente que una posición cerrada deja de ser consultable por este endpoint.

Referencia: [Alpaca — All Open Positions](https://docs.alpaca.markets/us/reference/getallopenpositions).

## Decisión

Se implementa un adaptador aislado de posiciones para Alpaca Paper. No ofrece balances ni satisface el contrato coordinado; por tanto, no habilita snapshots coordinados, cartera paper, Chief, Risk Manager ni ejecución. El provider se registra únicamente cuando está habilitado explícitamente y se han configurado credenciales junto con el ID esperado de la cuenta Paper.

## Implementación propuesta

- `backend/app/accounts/alpaca_paper_readonly.py` fija el host `https://paper-api.alpaca.markets` y solo ejecuta GET a `/v2/account` y `/v2/positions`.
- Cada lectura verifica el `id` devuelto por la cuenta contra `ALPACA_PAPER_EXPECTED_ACCOUNT_ID`. Sin coincidencia exacta no se consulta ni acepta el listado de posiciones.
- Solo se acepta `us_equity`; se normaliza como `equity`. El endpoint no aporta evidencia suficiente para distinguir ETF de acciones, por lo que no se infiere esa subclase.
- `asset_id` es la identidad primaria; cantidades/precios/valores/P&L se validan como decimal strings. El timestamp es local de retrieval y la cobertura completa solo se declara tras respuestas válidas de ambos GET.
- `ALPACA_PAPER_ENABLED=false` por defecto. Sin ID esperado de cuenta, key o secret, el provider no se registra.
- El contrato declara posiciones `cash` únicamente; balances, órdenes, cancelaciones, transfers, withdrawals y wallet signing permanecen deshabilitados.


## Requisitos de implementación

1. Host Paper fijo por defecto: `https://paper-api.alpaca.markets`. No aceptar una URL arbitraria ni cambiar silenciosamente a producción.
2. Autenticación mediante headers de API key/secret leídos exclusivamente desde variables de entorno/secret manager. Nunca imprimirlos ni incluirlos en errores.
3. Implementar solo métodos GET requeridos. No incluir submit/cancel/close/liquidate/transfer/withdraw ni SDK con rutas de escritura accesibles.
4. Exigir identidad de cuenta estable y verificarla contra `GET /v2/account`; no aceptar una respuesta de posiciones sin comprobar que pertenece a la cuenta configurada.
5. Normalizar posiciones con `asset_id` como identidad primaria; el símbolo no es una clave global suficiente. Mapear solo clases soportadas; rechazar desconocidas en vez de clasificarlas como crypto/equity por heurística.
6. Cantidades, precios, valores y P/L deben conservarse como decimal strings; rechazar floats, NaN, infinito, negativos donde no correspondan y precisión fuera de límites.
7. Una respuesta HTTP 200 con lista vacía puede representar cero posiciones abiertas solo si la autenticación, cuenta, endpoint y respuesta están validados. Timeout, 401/403, 429, 5xx, JSON malformado o identidad discordante nunca son una lista vacía.
8. La API de posiciones no proporciona por sí sola una marca temporal por posición en el esquema descrito. Registrar hora local de retrieval con `timestamp_source=retrieved_at`; no presentarla como timestamp del exchange.
9. La cobertura `complete` solo se podrá declarar si la respuesta corresponde al endpoint de listado completo, está íntegra, no truncada y la cuenta/entorno están verificados. Si existe cualquier duda, `partial` o error fail-closed.
10. No inferir efectivo desde buying power, ni posiciones desde balances. El endpoint de cuenta y el de posiciones tienen semánticas diferentes.

## Bloqueos antes de snapshot coordinado

- El store coordinado actual acepta únicamente `market_type=spot`. Las posiciones Alpaca de acciones/ETF requieren un scope `cash`/equity y cambios explícitos de contrato, persistencia, índices y pruebas.
- Debe definirse una representación correcta de cash/balances de Alpaca; no convertir buying power, equity o portfolio value en un balance fungible.
- Deben probarse respuestas vacías, posiciones múltiples, símbolos duplicados con asset IDs distintos, activos no soportados, errores de red, rate limits, credenciales inválidas, cuenta incorrecta, paper/live mismatch y reinicios.
- No conectar estos snapshots al portfolio paper, equity, Chief o Risk Manager en esta fase.

## Seguridad

Este adaptador será de observación solamente. La integración no habilita trading ni equivale a verificar los permisos reales de la API key. LIVE permanece deshabilitado y no se usarán cuentas o fondos reales durante pruebas.
