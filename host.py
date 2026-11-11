#!/usr/bin/env python3
"""Campus fleet console. Operator UI and robot transport use separate listeners."""
import argparse
from collections import OrderedDict
import hmac
import sys
import ipaddress
from pathlib import Path
import socketserver
import ssl
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from uuid import uuid4

from common import (COMMANDS, LIMIT, SPATIAL, TOKEN, VERSION, decode, encode,
                    ident, is_loopback, number, on_map, read, spatial_args)

ROOT = Path(__file__).resolve().parent
OFFLINE_S = 4
POSE_S = 3
QUEUE_S = 6
EXEC_S = 24
MAX_HISTORY = 160
MAX_PENDING = 8


class Rejected(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def map_config(path):
    meta = read(path)
    if not isinstance(meta, dict) or not ident(meta.get("map_id")) or not ident(meta.get("frame")):
        raise ValueError("map_id/frame are required")
    origin = meta.get("origin")
    if (not isinstance(origin, list) or len(origin) != 3 or
            not all(number(v) for v in origin) or abs(origin[2]) > 6.284 or
            not number(meta.get("resolution"), .0001, 100)):
        raise ValueError("invalid map origin/resolution")
    image = (path.parent / meta["image"]).resolve()
    if image.suffix.lower() != ".png":
        raise ValueError("map image must be PNG")
    with image.open("rb") as f:
        header = f.read(24)
    if header[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR" or len(header) != 24:
        raise ValueError("invalid PNG")
    w, h = struct.unpack(">II", header[16:])
    if not w or not h or w*h > 32_000_000:
        raise ValueError("map exceeds 32 million pixels; downsample the preview")
    return dict(meta, width=w, height=h, image="/map.png"), image


class Fleet:
    def __init__(self, cfg, path):
        self.lock = threading.Lock()
        self.demo = cfg.get("demo", False) is True
        self.map, self.image = map_config((path / cfg["map"]).resolve())
        self.devices = {}
        tokens = set()
        for row in cfg["robots"]:
            key, token = row["id"], row["token"]
            if not ident(key) or key in self.devices or not TOKEN.fullmatch(token) or token in tokens:
                raise ValueError("robot ID and token must be valid and unique")
            tokens.add(token)
            ip = row.get("expected_ip")
            commands = row.get("commands", ["ping", "status"])
            if not isinstance(commands, list) or not set(commands) <= COMMANDS:
                raise ValueError("unknown command in host allowlist")
            self.devices[key] = dict(id=key, token=token, name=row.get("name", key),
                                     expected_ip=str(ipaddress.ip_address(ip)) if ip else None,
                                     commands=commands, seen=None, data={}, address=None,
                                     session=None, last_pose=None, plan=None)
        self.jobs = OrderedDict()

    def advance(self, now):
        for job in self.jobs.values():
            if job["state"] == "queued" and now-job["created"] >= QUEUE_S:
                job.update(state="expired", message="送出前已过期")
            if job["state"] == "sent" and now-job["sent"] >= EXEC_S:
                job.update(state="unknown", message="未收到执行回执；结果未知")
        if len(self.jobs) > MAX_HISTORY:
            for key, job in list(self.jobs.items()):
                if job["state"] not in ("queued", "sent"):
                    del self.jobs[key]
                    if len(self.jobs) <= MAX_HISTORY:
                        break

    def online(self, row, now):
        return row["seen"] is not None and now-row["seen"] < OFFLINE_S

    def matches(self, row):
        data = row["data"]
        return data.get("map_id") == self.map["map_id"] and data.get("frame") == self.map["frame"]

    def public(self, row, now):
        online = self.online(row, now)
        match = self.matches(row)
        entry = row["last_pose"] if match else None
        age = now-entry[1] if entry else None
        pose = entry[0] if entry else None
        state = ("offline" if not online else "map_mismatch" if not match else
                 "unlocalized" if age is None else "stale" if age >= POSE_S else "localized")
        return {k: row[k] for k in ("id", "name", "expected_ip", "address", "commands", "plan")} | {
            "online": online, "state": state, "pose": pose, "pose_age_s": age,
            "seen_age_s": now-row["seen"] if row["seen"] is not None else None,
            "data": row["data"]}

    def status(self):
        with self.lock:
            now = time.monotonic()
            self.advance(now)
            return {"map": self.map, "demo": self.demo,
                    "robots": [self.public(row, now) for row in self.devices.values()],
                    "jobs": [self.show_job(j) for j in list(self.jobs.values())[-24:][::-1]]}

    @staticmethod
    def show_job(job):
        return {k: v for k, v in job.items() if k not in ("session", "created", "sent")}

    def submit(self, data):
        if not isinstance(data, dict) or not ident(data.get("robot")) or not ident(data.get("command")):
            raise Rejected(400, "无效的机器人或指令")
        key, command, args = data["robot"], data["command"], data.get("args", {})
        with self.lock:
            now = time.monotonic()
            self.advance(now)
            row = self.devices.get(key)
            if not row:
                raise Rejected(404, "机器人不存在")
            if command not in row["commands"]:
                raise Rejected(403, "此机器人未启用该指令")
            if not self.online(row, now):
                raise Rejected(409, "机器人离线")
            if command in SPATIAL:
                if not self.matches(row) or data.get("map_id") != self.map["map_id"]:
                    raise Rejected(409, "地图版本不一致")
                if not spatial_args(args) or not on_map(self.map, args["x"], args["y"]):
                    raise Rejected(400, "位姿须在地图范围内")
                if command == "preview_path" and self.public(row, now)["state"] != "localized":
                    raise Rejected(409, "需要有效的当前定位")
            elif args != {}:
                raise Rejected(400, "此指令不接受参数")
            pending = [j for j in self.jobs.values() if j["robot"] == key and j["state"] in ("queued", "sent")]
            if len(pending) >= MAX_PENDING or (command in SPATIAL and any(j["command"] in SPATIAL for j in pending)):
                raise Rejected(409, "有待处理的指令")
            job = {"id": uuid4().hex, "robot": key, "command": command, "args": args, "state": "queued",
                   "message": "等待板端领取", "created": now, "time": time.time(), "session": row["session"]}
            self.jobs[job["id"]] = job
            return self.show_job(job)

    def sync(self, body, auth, ip):
        if not isinstance(body, dict) or body.get("version") != VERSION:
            raise Rejected(400, "protocol version")
        key, session = body.get("robot"), body.get("session")
        row = self.devices.get(key) if ident(key) else None
        if row is None or not hmac.compare_digest(auth.encode(), ("Bearer "+row["token"]).encode()):
            raise Rejected(401, "unknown robot or token")
        if row["expected_ip"] and row["expected_ip"] != ip:
            raise Rejected(403, "unexpected robot IP")
        data = body.get("status")
        if (not ident(session) or not isinstance(data, dict) or not ident(data.get("map_id")) or
                not ident(data.get("frame")) or data.get("mode") not in ("demo", "ros2") or
                (data["mode"] == "demo") != self.demo):
            raise Rejected(400, "invalid robot status/mode")
        with self.lock:
            now = time.monotonic()
            self.advance(now)
            if row["session"] != session:
                if row["session"] and self.online(row, now):
                    raise Rejected(409, "another session is still online")
                row.update(session=session, last_pose=None, plan=None)
            previous = (row["data"].get("map_id"), row["data"].get("frame"))
            metrics = {k: data[k] for k in ("memory_available_mib", "load_1m", "uptime_s")
                       if number(data.get(k), 0, 1_000_000_000_000)}
            row.update(seen=now, address=ip, data=metrics | {
                "map_id": data["map_id"], "frame": data["frame"], "mode": data["mode"],
                "imu_fresh": data.get("imu_fresh") is True})
            if previous != (data["map_id"], data["frame"]):
                row.update(last_pose=None, plan=None)
            pose = data.get("pose")
            fresh_pose = (self.matches(row) and isinstance(pose, dict) and
                          all(number(pose.get(k)) for k in ("x", "y", "z", "yaw_deg")) and
                          number(pose.get("age_s"), 0, POSE_S-.001))
            if fresh_pose:
                row["last_pose"] = ({k: pose[k] for k in ("x", "y", "z", "yaw_deg")}, now-pose["age_s"])
            result = body.get("result")
            ack = None
            if isinstance(result, dict) and ident(result.get("id")):
                ack = result["id"]
                job = self.jobs.get(ack)
                if (job and job["robot"] == key and job.get("session") == session and
                        job["state"] in ("sent", "unknown")):
                    state = result.get("state")
                    if state in ("ok", "rejected", "failed", "unknown"):
                        job.update(state=state, message=str(result.get("message", ""))[:500])
                        if state == "ok" and job["command"] == "seed_pose" and not fresh_pose:
                            row["last_pose"] = None
                        plan = result.get("path")
                        if (state == "ok" and job["command"] == "preview_path" and self.matches(row)
                                and isinstance(plan, list) and 1 <= len(plan) <= 300 and
                                all(isinstance(p, list) and len(p) == 2 and all(number(v) for v in p) for p in plan)):
                            row["plan"] = plan
            job_out = None
            if body.get("busy") is not True and not any(j["robot"] == key and j["state"] == "sent" for j in self.jobs.values()):
                for job in self.jobs.values():
                    if job["robot"] != key or job["state"] != "queued":
                        continue
                    if job["session"] != session:
                        job.update(state="expired", message="板端已重连，旧指令已作废")
                        continue
                    if job["command"] in SPATIAL and (not self.matches(row) or
                            (job["command"] == "preview_path" and self.public(row, now)["state"] != "localized")):
                        job.update(state="rejected", message="机器人地图或定位状态已变化")
                        continue
                    job.update(state="sent", message="已交给板端，等待回执", sent=now, session=session)
                    if job["command"] in SPATIAL:
                        row["plan"] = None
                    job_out = {k: job[k] for k in ("id", "command", "args")} | {
                        "map_id": self.map["map_id"], "frame": self.map["frame"]}
                    break
            return {"version": VERSION, "ack": ack, "command": job_out}


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, fleet, role, tls=None):
        super().__init__(address, Handler)
        self.fleet, self.role, self.tls = fleet, role, tls
        self.slots = threading.BoundedSemaphore(64)

    def server_bind(self):
        # A management listener does not need a reverse DNS lookup to bind.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    def get_request(self):
        sock, address = super().get_request()
        sock.settimeout(5)
        if self.tls:
            # Perform handshakes in worker threads, leaving accept responsive.
            sock = self.tls.wrap_socket(sock, server_side=True, do_handshake_on_connect=False)
        return sock, address

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.slots.release()
            raise

    def handle_error(self, request, address):
        if not isinstance(sys.exc_info()[1], (ssl.SSLError, ConnectionError, TimeoutError)):
            super().handle_error(request, address)

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def reply(self, status, payload, kind="application/json; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(payload)

    def json(self, status, body):
        self.reply(status, encode(body))

    def host_allowed(self):
        port = self.server.server_port
        return self.headers.get("Host") in {f"localhost:{port}", f"127.0.0.1:{port}"}

    def do_GET(self):
        if self.server.role != "operator" or not self.host_allowed():
            self.json(404, {"error": "not found"})
            return
        fleet = self.server.fleet
        path = urlsplit(self.path).path
        if path == "/api/state":
            self.json(200, fleet.status())
            return
        files = {"/": (ROOT/"web/index.html", "text/html; charset=utf-8"),
                 "/app.js": (ROOT/"web/app.js", "text/javascript; charset=utf-8"),
                 "/style.css": (ROOT/"web/style.css", "text/css; charset=utf-8"),
                 "/map.png": (fleet.image, "image/png")}
        item = files.get(path)
        if item:
            self.reply(200, item[0].read_bytes(), item[1])
        else:
            self.json(404, {"error": "not found"})

    def do_POST(self):
        try:
            if self.server.role == "operator":
                if self.path != "/api/commands" or not self.host_allowed():
                    raise Rejected(404, "not found")
                if self.headers.get("Origin") != "http://"+self.headers["Host"]:
                    raise Rejected(403, "invalid origin")
            elif self.path != "/robot/v1/sync":
                raise Rejected(404, "not found")
            if self.headers.get_content_type() != "application/json":
                raise Rejected(415, "JSON required")
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= LIMIT or self.headers.get("Transfer-Encoding"):
                raise Rejected(413, "invalid payload size")
            body = decode(self.rfile.read(size))
            fleet = self.server.fleet
            if self.server.role == "operator":
                self.json(202, fleet.submit(body))
            else:
                self.json(200, fleet.sync(body, self.headers.get("Authorization", ""), self.client_address[0]))
        except Rejected as e:
            self.json(e.status, {"error": e.message})
        except (ValueError, OverflowError, RecursionError):
            self.json(400, {"error": "invalid JSON"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    a = parser.parse_args()
    cfg = read(a.config)
    base = a.config.resolve().parent
    fleet = Fleet(cfg, base)
    address = cfg["robot_listen"]
    bind, port = address["host"], address["port"]
    tls = None
    if address.get("cert") and address.get("key"):
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.minimum_version = ssl.TLSVersion.TLSv1_2
        tls.load_cert_chain(base/address["cert"], base/address["key"])
    elif not is_loopback(bind):
        parser.error("non-loopback robot listener requires TLS cert/key")
    if fleet.demo and (not is_loopback(bind) or tls):
        parser.error("simulation uses loopback HTTP only")
    with Server((bind, port), fleet, "robot", tls) as robots, \
            Server(("127.0.0.1", cfg.get("operator_port", 8765)), fleet, "operator") as operator:
        threading.Thread(target=robots.serve_forever, daemon=True).start()
        print(f"控制台：http://127.0.0.1:{operator.server_port} | 板端：{bind}:{robots.server_port}", flush=True)
        try:
            operator.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            robots.shutdown()


if __name__ == "__main__":
    main()
