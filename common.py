"""Wire validation and shared map geometry."""
import ipaddress
import json
import math
import re
from pathlib import Path

VERSION = 1
LIMIT = 32_768
ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}\Z")
TOKEN = re.compile(r"[a-zA-Z0-9_-]{32,128}\Z")
COMMANDS = {"ping", "status", "seed_pose", "preview_path"}
SPATIAL = {"seed_pose", "preview_path"}


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=_reject_constant)


def _reject_constant(value):
    raise ValueError("invalid JSON number: " + value)


def decode(data):
    return json.loads(data, parse_constant=_reject_constant)


def encode(data):
    return json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


def ident(value):
    return isinstance(value, str) and ID.fullmatch(value) is not None


def number(value, lower=-10_000_000, upper=10_000_000):
    return type(value) in (int, float) and lower <= value <= upper


def is_loopback(host):
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def image_xy(meta, x, y):
    ox, oy, yaw = meta["origin"]
    dx, dy = x-ox, y-oy
    c, s = math.cos(yaw), math.sin(yaw)
    return (c*dx+s*dy)/meta["resolution"], meta["height"]-(-s*dx+c*dy)/meta["resolution"]


def on_map(meta, x, y):
    u, v = image_xy(meta, x, y)
    return 0 <= u < meta["width"] and 0 < v <= meta["height"]


def spatial_args(args):
    return (isinstance(args, dict) and set(args) == {"x", "y", "yaw_deg"}
            and number(args["x"]) and number(args["y"]) and number(args["yaw_deg"], -180, 180))
