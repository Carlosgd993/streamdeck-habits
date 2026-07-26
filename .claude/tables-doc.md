# El contrato: qué expone y qué pasa al pulsar una tecla

Documentación funcional, no exhaustiva. Para el detalle completo del contrato
(qué vistas/funciones existen, qué garantizan) ver `habits-core/docs/contrato.md`.
Para por qué este repo ya no toca tablas, ver `estructura-bd.md`.

## Dónde vive cada cosa (vista del cliente)

- **Hábitos de hoy** → vista `v_today_habits`. Una fila por hábito activo,
  con el progreso de hoy ya calculado en `current_value` y `done` (si alcanzó
  el objetivo). No hay "definición" y "progreso" como dos fuentes separadas
  desde el punto de vista del daemon: la vista ya las junta.
- **Tareas de hoy** → vista `v_today_tasks`. No la usa este daemon todavía.

La distinción entre "qué es el hábito" y "qué has hecho hoy" sigue existiendo
en la base de datos (tablas `habits`/`habit_checkins`), pero el daemon ya no
la ve: son tablas cerradas con RLS, inaccesibles con la clave publishable.

## Qué pasa al pulsar una tecla de hábito (streamdeck-habits)

1. El daemon ya tiene en memoria, desde el último refresco, la lista de
   hábitos de `v_today_habits` (cada uno con su `current_value` de hoy).
2. Al pulsar, llama a la función `habit_step(p_habit_id)` del contrato. La
   base decide el valor nuevo y lo devuelve:
   - Hábito **booleano** → salta directo a `goal` (es binario).
   - Hábito **cuantificable** ("Real", p.ej. Flexiones) → suma su `step`,
     **sin tope** (10/8 es un estado válido y deliberado).
3. `habit_step` es atómico: pulsar en el deck y en el móvil a la vez no
   pierde un incremento. El daemon no lee-calcula-escribe; solo pide el paso
   y pinta lo que la base devuelve.
4. La tecla se repinta al momento con el nuevo valor, sin esperar al próximo
   refresco. Alcanzar el objetivo no bloquea la tecla: se sigue pudiendo
   pulsar y, si es cuantificable, sigue sumando.
