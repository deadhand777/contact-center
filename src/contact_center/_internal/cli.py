# Why does this file exist, and why not put this in `__main__`?
#
# You might be tempted to import things from `__main__` later,
# but that will cause problems: the code will get executed twice:
#
# - When you run `python -m contact_center` python will execute
#   `__main__.py` as a script. That means there won't be any
#   `contact_center.__main__` in `sys.modules`.
# - When you import `__main__` it will get executed again (as a module) because
#   there's no `contact_center.__main__` in `sys.modules`.

from __future__ import annotations

import argparse
import sys
from typing import Any

from contact_center._internal import chat, connect_chat, debug, eval_runner


class _DebugInfo(argparse.Action):
    def __init__(self, nargs: int | str | None = 0, **kwargs: Any) -> None:
        super().__init__(nargs=nargs, **kwargs)

    def __call__(self, *args: Any, **kwargs: Any) -> None:  # noqa: ARG002
        debug._print_debug_info()
        sys.exit(0)


def get_parser() -> argparse.ArgumentParser:
    """Return the CLI argument parser.

    Returns:
        An argparse parser.
    """
    parser = argparse.ArgumentParser(prog="contact-center")
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {debug._get_version()}")
    parser.add_argument("--debug-info", action=_DebugInfo, help="Print debug information.")
    subparsers = parser.add_subparsers(dest="command")
    chat_parser = subparsers.add_parser("chat", help="Chat with the deployed contact-center agent.")
    chat_parser.add_argument("-q", "--question", default=None, help="Ask one question and exit.")
    chat_parser.add_argument(
        "--customer", default="KND-1001", help="Authenticated customer id for banking questions.",
    )
    chat_parser.add_argument(
        "--connect", action="store_true", help="Go through the Amazon Connect front door instead of direct invoke.",
    )
    eval_parser = subparsers.add_parser("eval", help="Score the golden set against the deployed agent.")
    eval_parser.add_argument(
        "--threshold", type=float, default=1.0, help="Minimum item pass-rate; exit nonzero below it.",
    )
    eval_parser.add_argument(
        "--golden", default=None, help="Path to the golden set JSON (default: docs/eval/golden.json).",
    )
    return parser


def main(args: list[str] | None = None) -> int:
    """Run the main program.

    This function is executed when you type `contact-center` or `python -m contact_center`.

    Parameters:
        args: Arguments passed from the command line.

    Returns:
        An exit code.
    """
    parser = get_parser()
    opts = parser.parse_args(args=args)
    if opts.command == "chat":
        if opts.connect:
            return connect_chat.run_connect_chat(opts.question, customer_id=opts.customer)
        return chat.run_chat(opts.question, customer_id=opts.customer)
    if opts.command == "eval":
        return eval_runner.run_eval(opts.golden, threshold=opts.threshold)
    print(opts)
    return 0
