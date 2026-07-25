# Tablas: qué guarda cada una y qué pasa al pulsar una tecla

Documentación funcional, no exhaustiva. Para el detalle completo de columnas
(incluidos campos cosméticos como colores/iconos) ver `estructura-bd.md`.

## Dónde vive cada cosa

- **Tareas** → `tasks` (una fila por tarea). Pertenecen a un `projects` (lista)
  y tienen un `status_id` que apunta a `statuses` (p.ej. "Por hacer" / "Hecho").
  Pueden tener subtareas en `checklist_items` y etiquetas vía `task_tags`.
- **Hábitos** → `habits` (una fila por hábito, la definición: nombre, tipo,
  objetivo diario). Es la "plantilla", no cambia día a día.
- **Registros diarios de hábito (los "checks")** → `habit_checkins`. Una fila
  por combinación `(habit_id, fecha)`. **Aquí es donde vive el progreso real**:
  cuánto llevas hoy de un hábito.

La distinción importante: `habits` describe *qué es* el hábito y cuál es su
meta; `habit_checkins` describe *qué has hecho hoy*. El daemon de la Stream
Deck solo lee/escribe `habits` y `habit_checkins` — nunca toca `tasks`.

## Qué pasa al pulsar una tecla de hábito (streamdeck-habits)

1. El daemon ya tiene en memoria, desde el último refresco, la lista de
   hábitos (`habits`) y el progreso de hoy (`habit_checkins` de la fecha
   actual).
2. Calcula el valor nuevo:
   - Hábito **booleano** (marca sí/no) → siempre pasa a "hecho" (`value = 1`).
   - Hábito **cuantificable** ("Real", p.ej. Flexiones) → suma su `step` al
     valor de hoy, sin superar el `goal` (p.ej. si vas 15/100 y el paso es 5,
     pasa a 20/100).
3. Envía ese valor **total** (no un incremento) a `habit_checkins` como un
   **upsert** sobre `(habit_id, checkin_date)`:
   - Si hoy **no** habías pulsado esa tecla → se **inserta** una fila nueva en
     `habit_checkins`.
   - Si hoy **ya** habías pulsado esa tecla antes → se **actualiza** la fila
     existente (se sobreescribe `value`, no se suma una fila nueva).
4. `habits` **no se toca nunca** al pulsar. Solo cambia si editas manualmente
   la definición del hábito (su meta, su nombre...).
5. La tecla se repinta al momento con el nuevo valor, sin esperar al próximo
   refresco.

Consecuencia práctica: **cada hábito tiene como mucho una fila en
`habit_checkins` por día**, sin importar cuántas veces pulses la tecla ese
día — las pulsaciones de un mismo día actualizan la misma fila, no crean
filas nuevas. Al día siguiente se crea una fila distinta (fecha distinta), y
el progreso vuelve a arrancar de 0 porque ya no hay fila para la fecha de
hoy.

## Qué pasaría al "pulsar un botón" de una tarea (si algún día se hace)

Esto no existe todavía en el daemon (que solo gestiona hábitos), pero así es
como está pensado el modelo para tareas:

- Marcar una tarea como hecha = actualizar **la propia fila de `tasks`**
  (no se crea una fila nueva en ninguna tabla): se le cambia `status_id`
  para que apunte al estado "Hecho" en `statuses`, y se rellena
  `completed_time` con la fecha/hora.
- A diferencia de los hábitos, una tarea no tiene "progreso diario": es un
  estado único que se sobreescribe. No hay tabla equivalente a
  `habit_checkins` para tareas.

## Resumen de efectos por tabla

| Acción | Tabla que cambia | Qué cambia |
| --- | --- | --- |
| Pulsar tecla de hábito (1ª vez hoy) | `habit_checkins` | **INSERT** fila nueva `(habit_id, hoy, value)` |
| Pulsar tecla de hábito (2ª+ vez hoy) | `habit_checkins` | **UPDATE** del `value` de la fila de hoy |
| Pasa un día | *(ninguna, pasivo)* | Mañana no hay fila para la nueva fecha → el hábito vuelve a verse "pendiente" |
| Completar una tarea | `tasks` | **UPDATE** de `status_id` + `completed_time` en su propia fila |
| Crear/editar un hábito | `habits` | INSERT/UPDATE de la definición (meta, nombre...) — no afecta a `habit_checkins` |
