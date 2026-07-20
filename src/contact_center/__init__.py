"""contact-center package.

Contact center app
"""

from __future__ import annotations

from contact_center._internal.cli import get_parser, main

__all__: list[str] = ["get_parser", "main"]
