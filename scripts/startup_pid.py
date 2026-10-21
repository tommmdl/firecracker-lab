"""A created-but-empty jailer PID file is not a published process identity."""
from pathlib import Path


def read_startup_pid(path):
    try:text=Path(path).read_text(encoding='ascii').strip()
    except FileNotFoundError:return None
    if not text:return None
    if not text.isascii() or not text.isdecimal() or int(text)<=0:
        raise ValueError('Malformed jailed PID file')
    return int(text)
