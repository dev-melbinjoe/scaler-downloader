"""Lets the web page's Stop button halt long work between steps (downloads and transcripts resume later)."""

import threading

STOP = threading.Event()


class Stopped(Exception):
    pass


def check() -> None:
    if STOP.is_set():
        raise Stopped("stopped by you; run it again to continue where it left off")
