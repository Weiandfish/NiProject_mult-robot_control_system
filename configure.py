#!/usr/bin/env python3
"""Generate private matching host/robot configuration files, without overwriting."""
import argparse
import json
import os
from pathlib import Path
import secrets

from common import ident, read

ROOT = Path(__file__).resolve().parent
ALL = ["ping", "status", "seed_pose", "preview_path"]


def write(path, data):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def generate(out, map_path, entries, *, bind, host_url, port=8766, ui=8765, cert=None, key=None,
             ca=None, demo=False, allow_spatial=False):
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    map_path = map_path.resolve()
    meta = read(map_path)
    ids = [row[0] for row in entries]
    if any(not ident(k) or k == "host" for k in ids) or len(set(ids)) != len(ids):
        raise ValueError("robot IDs must be unique and valid")
    if any((out/p).exists() for p in ["host.json", *(k+".json" for k in ids)]):
        raise FileExistsError("configuration already exists; choose a new directory")
    allowed = ALL if allow_spatial or demo else ALL[:2]
    listener = {"host": bind, "port": port}
    if cert and key:
        listener.update(cert=str(cert.resolve()), key=str(key.resolve()))
    robots = []
    for rid, ip, name, pose in entries:
        token = secrets.token_urlsafe(40)
        robots.append({"id": rid, "expected_ip": ip, "name": name, "token": token, "commands": allowed})
        cfg = {"id": rid, "token": token, "map_id": meta["map_id"], "frame": meta["frame"],
               "host_url": host_url, "commands": allowed}
        if demo:
            cfg.update(demo=True, demo_pose=pose)
        if ca:
            cfg["ca"] = ca
        write(out/(rid+".json"), cfg)
    write(out/"host.json", {"map": str(map_path), "operator_port": ui, "robot_listen": listener,
                            "robots": robots, "demo": demo})


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("directory", type=Path)
    p.add_argument("--map", type=Path, default=ROOT/"maps/desk/map.json")
    p.add_argument("--host", required=True, help="控制主机可被板子访问的 IP 或证书域名")
    p.add_argument("--bind", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8766)
    p.add_argument("--operator-port", type=int, default=8765)
    p.add_argument("--cert", type=Path, required=True)
    p.add_argument("--key", type=Path, required=True)
    p.add_argument("--ca-name", default="fleet-ca.crt", help="板端证书相对路径；从主机单独复制该公有证书")
    p.add_argument("--robot", action="append", required=True, metavar="ID,IP,NAME",
                   help="可重复；已核对的固定 IP，也可省略 IP（如 a210-01,,一号车）")
    p.add_argument("--allow-spatial", action="store_true")
    a = p.parse_args()
    rows = []
    for line in a.robot:
        fields = line.split(",", 2)
        if len(fields) != 3:
            p.error("--robot expects ID,IP,NAME")
        rid, ip, name = fields
        rows.append((rid, ip or None, name, None))
    if not a.cert.is_file() or not a.key.is_file():
        p.error("certificate and key must already exist")
    generate(a.directory, a.map, rows, bind=a.bind, host_url=f"https://{a.host}:{a.port}",
             port=a.port, ui=a.operator_port, cert=a.cert, key=a.key, ca=a.ca_name,
             allow_spatial=a.allow_spatial)
    print(f"配置已生成：{a.directory.resolve()}")


if __name__ == "__main__":
    main()
