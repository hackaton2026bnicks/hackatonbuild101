"""Rutas que funcionan igual en Mac, Linux y Windows para las herramientas de insights.

En Git Bash de Windows, ~ se expande a /c/Users/... y Python no la entiende: la toma como
\\c\\Users en el disco actual y el glob no encuentra nada.
"""

from __future__ import annotations

import os
import re

DRIVE = re.compile(r"^/([a-zA-Z])(?=/|$)")


def local_path(path: str, windows: bool | None = None) -> str:
    path = os.path.expanduser(path)
    if os.name == "nt" if windows is None else windows:
        path = DRIVE.sub(lambda m: m[1].upper() + ":", path)
    return path
