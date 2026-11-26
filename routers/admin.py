"""Eliminado 2026-11-25.

Aquí vivió el endpoint temporal POST /api/admin/grant-forge-test, de un
solo uso: crear los 11 gladiadores de prueba de la Forja en la wallet de
Yordi. Se usó una vez en producción (11 creados, $TICK a 200) y se quitó:
el router ya no está registrado en main.py, la variable ADMIN_GRANT_TOKEN
ya no existe en config.py ni en Render, y este archivo queda como
tombstone para que el borrado quede registrado en el historial.

El conector de GitHub no admite borrados en el snapshot, por eso el
archivo se vacía en vez de borrarse.
"""
