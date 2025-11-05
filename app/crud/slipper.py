"""Compatibility shim: exports the CRUD functions from app.crud.stepup.

This file remains to provide backward compatibility for imports that still
reference `app.crud.slipper`. The canonical implementation lives in
`app.crud.stepup` (functions keep compatible names).
"""

from app.crud.stepup import *  # noqa: F401,F403


