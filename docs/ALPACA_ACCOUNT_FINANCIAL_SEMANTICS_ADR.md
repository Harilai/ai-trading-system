# ADR — semántica financiera de Alpaca Paper account

- Estado: decisión de dominio aprobada para una futura implementación read-only; no añade todavía un lector de balances ni habilita snapshots coordinados.
- Fecha: 2026-11-11.
- Ámbito: interpretación de `GET /v2/account` para cuentas Alpaca Paper de acciones/ETF.
- Referencias oficiales: [Get Account](https://docs.alpaca.markets/us/reference/getaccount-1), [Working with /account](https://docs.alpaca.markets/us/docs/working-with-account).

## Contexto

El adaptador `AlpacaPaperReadOnlyAccount` actual verifica la identidad de la cuenta y obtiene posiciones abiertas, pero declara `balances=false`. No debe cambiarse esa capacidad hasta separar los conceptos contables del contrato genérico `AccountBalance(free, used, total)`.

Alpaca devuelve métricas distintas en `GET /v2/account`. No son intercambiables ni representan todas efectivo disponible.

## Semántica normativa de campos

| Campo Alpaca | Significado de dominio | Tratamiento |
|---|---|---|
| `cash` | Saldo de efectivo de la cuenta | Métrica `cash_balance`, divisa `currency`; no asumir por sí sola que es efectivo libre para nuevas órdenes |
| `equity` | Cash más valor de posiciones largas y cortas | `account_equity`; valoración neta, nunca cash |
| `portfolio_value` | Total cash + posiciones; documentación lo marca equivalente/deprecado respecto a equity | Solo dato legado opcional; no sumar a equity ni usar como cash |
| `buying_power` | Capacidad de compra calculada según multiplicador/margen y reglas de cuenta | `buying_power`; capacidad de compra, no balance ni efectivo |
| `regt_buying_power`, `non_marginable_buying_power`, `options_buying_power` | Límites/capacidades de compra con reglas distintas | Métricas separadas y tipadas; no consolidar ni sustituir buying_power |
| `cash_withdrawable`, `cash_transferable` | Cantidades elegibles bajo reglas de retirada/transferencia | No mapear a cash ni habilitar operaciones de retirada/transferencia |
| `pending_transfer_in/out` | Flujos pendientes | Métricas de pasivo/flujo pendientes separadas; no incorporarlas silenciosamente a cash |
| `long_market_value`, `short_market_value` | Valoración de posiciones | Métricas de valoración; no sumar otra vez a equity |
| `initial_margin`, `maintenance_margin` | Requisitos de margen | Métricas de riesgo; no son efectivo |
| `last_equity`, `last_cash`, `last_* ` | Valores de referencia del periodo anterior, ligados a `balance_asof` cuando se expone | Históricos con fecha propia; nunca actuales ni mezclados con métricas actuales |
| `accrued_fees`, `pending_reg_taf_fees` | Comisiones devengadas o pendientes | Métricas de coste separadas; no restar de cash sin una regla contable explícita |

La divisa de la cuenta se debe leer de `currency` y validar explícitamente. No hardcodear USD en el modelo aunque la documentación actual indique USD para este producto.

## Decisión de modelo

No reutilizar `AccountBalance` para almacenar todos los campos de Alpaca. Crear un contrato separado, por ejemplo `AccountFinancialMetricsSnapshot`, con:

- identidad: provider, account alias, environment, account ID verificado y currency;
- `fetched_at` con zona horaria y `timestamp_source=retrieved_at`;
- métricas decimales opcionales, tipadas y con nombre semántico (cash balance, equity, buying power, margin, market values, pending transfers y fees);
- campos booleanos de estado/restricción separados de importes;
- cobertura explícita y versión de esquema;
- ningún campo genérico `free/used/total` para equity o buying power.

Los importes deben ser strings decimales planos, parseados con `Decimal`, finitos y dentro de límites de precisión. Rechazar floats, exponentes, NaN, infinito, tipos inesperados y valores negativos cuando la semántica no los permita. No aplicar una regla global de no-negatividad a métricas que legítimamente puedan ser negativas, como equity o P/L; cada campo debe declarar su rango.

## Contrato del proveedor futuro

- Mantener host fijo `https://paper-api.alpaca.markets`, solo GET y validación de cuenta esperada.
- Reutilizar una única lectura autenticada de `/v2/account` para verificar identidad y extraer métricas de esa misma respuesta; evitar un segundo GET innecesario.
- Exigir `id`, `currency`, `status` y campos mínimos documentados. Campo opcional ausente => null con cobertura parcial o métrica no disponible; nunca inventar cero.
- Respuesta 200 malformada, identidad distinta, cuenta/env no esperados, HTTP 401/403/429/5xx o timeout => error fail-closed, nunca balance vacío.
- Un campo decimal inválido invalida el snapshot financiero completo salvo que una política explícita de cobertura parcial por campo se diseñe y audite.
- La capacidad `balances` del contrato genérico no se activará hasta que exista un endpoint separado y semánticamente seguro para métricas financieras; no mentir sobre compatibilidad con `AccountBalance`.

## Persistencia y snapshot coordinado

No extender ahora `AccountSnapshotStore`: su tabla actual modela activos con free/used/total, está limitada a `spot` y no puede representar correctamente métricas de cuenta de Alpaca. Tampoco forzar `market_type=cash` en ese esquema.

La siguiente decisión técnica deberá introducir un ledger append-only separado para métricas financieras, o una abstracción versionada de account metrics, y coordinarlo con el ledger de posiciones `cash` usando una transacción SQLite única. La captura solo será `complete` cuando ambas lecturas correspondan a la misma cuenta Paper verificada, tengan cobertura íntegra y cumplan skew/freshness. Las métricas de cuenta y las posiciones conservarán sus timestamps/procedencias por separado.

No sumar equity + market value, no sumar portfolio_value + cash, no usar buying_power como efectivo, no calcular cash libre a partir de buying_power y no inferir posiciones desde balances.

## Límites de seguridad

- Read-only; no órdenes, cancelaciones, transferencias, retiradas ni firma.
- No conectar estas métricas a cartera paper, Chief, Risk Manager ni motor de ejecución.
- No activar LIVE ni cambiar el host Paper.
- No conceder capacidad de transferencia/retirada aunque la API informe de cantidades transferibles.
- La futura integración con riesgo requerirá una fase independiente de reconciliación y validación operativa.

## Pruebas obligatorias antes de implementación

1. Cash, equity y buying_power divergentes: preservar los tres valores y tipos.
2. Portfolio_value equivalente/deprecado: no duplicar equity.
3. Multiplicadores 1/2/4 y cuentas con margen: no derivar cash ni buying power localmente.
4. Métricas opcionales ausentes: null/cobertura explícita, nunca cero sintético.
5. Valores negativos permitidos/rechazados según campo.
6. Decimal strings válidos, precisión extrema, floats, exponentes, NaN e infinito.
7. Currency ausente/no soportada, account ID mismatch y status restringido.
8. HTTP 401/403/429/5xx, timeout, JSON malformado y payload incompleto.
9. Confirmar GET-only, host Paper fijo y ausencia de secretos en logs/errores.
10. Persistencia append-only, idempotencia, skew, rollback y aislamiento Paper/production.
11. Verificar que ningún dato externo modifica el portfolio paper ni crea órdenes.

## Resultado

La lectura de `/v2/account` es útil como evidencia financiera read-only, pero no es una lista de balances fungibles. La implementación queda separada hasta disponer de un modelo tipado y un ledger compatible con snapshots coordinados `cash`.
