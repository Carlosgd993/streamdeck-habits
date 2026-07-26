# Esquema de la base de datos

El esquema ya no vive en este repo: vive en `habits-core`
(`habits-core/docs/contrato.md` y `habits-core/supabase/migrations/`).

## La regla

Este daemon (y cualquier otro cliente) solo puede acceder a **vistas y
funciones** del contrato publico (`v_today_habits`, `v_today_tasks`,
`habit_step`, `habit_set`, `habit_undo`, `complete_task`, `uncomplete_task`,
…). Nunca a una tabla. Las tablas estan cerradas con RLS y sin `GRANT` para el
rol `anon`; un intento de leer/escribir una tabla directamente falla con
401/403.

Si algo que necesitas no esta en el contrato, falta ampliarlo en
`habits-core` -- no se soluciona leyendo la tabla por debajo.

Ver `.claude/supabase.http` para peticiones de ejemplo contra el contrato.
