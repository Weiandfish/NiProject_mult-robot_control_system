import http.client
import json
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from agent import Transport
from common import encode, image_xy
from configure import ROOT
from host import Fleet, Server


def wait_for(fn, seconds=8):
    until = time.monotonic()+seconds
    while time.monotonic() < until:
        v = fn()
        if v:
            return v
        time.sleep(.1)
    raise AssertionError("timeout")


class FleetTest(unittest.TestCase):
    def setUp(self):
        self.d = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.map = ROOT/"maps/desk/map.json"

    def setup_servers(self, demo=True, tls=None):
        self.cfg = {"map": str(self.map), "demo": demo,
                    "robots": [{"id": k, "token": k*40, "commands": ["ping", "status", "seed_pose", "preview_path"]}
                               for k in ["a", "b"]]}
        self.fleet = Fleet(self.cfg, self.d)
        self.api = self.start("robot", tls)
        self.ui = self.start("operator")

    def start(self, role, tls=None):
        s = self.enterContext(Server(("127.0.0.1", 0), self.fleet, role, tls))
        thread = threading.Thread(target=s.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(s.shutdown)
        return s.server_port

    def request(self, port, url, body=None, **headers):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        try:
            c.request("GET" if body is None else "POST", url, None if body is None else encode(body),
                      {"Content-Type": "application/json"} | headers)
            resp = c.getresponse()
            return resp.status, json.loads(resp.read())
        finally:
            c.close()

    def robots(self):
        _, data = self.request(self.ui, "/api/state")
        return {r["id"]:r for r in data["robots"]}

    def submit(self, k, command, **args):
        return self.request(self.ui, "/api/commands",
                            {"robot": k, "command": command, "args": args, "map_id": self.fleet.map["map_id"]},
                            Origin=f"http://127.0.0.1:{self.ui}")

    def job(self, key):
        _, d = self.request(self.ui, "/api/state")
        return next(j for j in d["jobs"] if j["id"] == key)

    def worker(self, key, position):
        cfg = {"id": key, "token": key*40, "commands": self.cfg["robots"][0]["commands"], "frame": "world",
               "map_id": self.fleet.map["map_id"], "host_url": f"http://127.0.0.1:{self.api}",
               "demo": True, "demo_pose": position}
        p = self.d/(key+".json")
        p.write_bytes(encode(cfg))
        proc = subprocess.Popen([sys.executable, str(ROOT/"agent.py"), str(p)], stdout=subprocess.DEVNULL)
        def stop():
            if proc.poll() is None:
                proc.terminate()
                proc.wait(4)
        self.addCleanup(stop)
        return stop

    def test_two_agents_dispatch_and_disconnect(self):
        self.setup_servers()
        stop_a = self.worker("a", [0,0,30])
        self.worker("b", [2,1,75])
        wait_for(lambda: all(r["state"] == "localized" for r in self.robots().values()))
        self.assertGreater(self.robots()["b"]["pose"]["x"], 1.8)
        status, job = self.submit("a", "ping")
        self.assertEqual(status, 202)
        wait_for(lambda: self.job(job["id"])["state"] == "ok")
        status, job = self.submit("b", "preview_path", x=3, y=2, yaw_deg=90)
        self.assertEqual(status, 202)
        wait_for(lambda: self.job(job["id"])["state"] == "ok")
        self.assertEqual(self.robots()["b"]["plan"][-1], [3,2])
        self.assertIsNone(self.robots()["a"]["plan"])
        status, job = self.submit("b", "seed_pose", x=1, y=2, yaw_deg=45)
        self.assertEqual(status, 202)
        wait_for(lambda: self.job(job["id"])["state"] == "ok")
        self.assertEqual(self.robots()["b"]["pose"]["yaw_deg"], 45)
        stop_a()
        wait_for(lambda: self.robots()["a"]["state"] == "offline")
        self.assertIsNotNone(self.robots()["a"]["pose"])
        self.assertEqual(self.submit("a", "status")[0], 409)
        self.worker("a", [0,0,-30])
        wait_for(lambda: self.robots()["a"]["state"] == "localized")
        self.assertEqual(self.robots()["a"]["pose"]["yaw_deg"], -30)

    def test_auth_map_and_command_once(self):
        self.setup_servers()
        meta = self.fleet.map
        body = {"version": 1, "robot": "a", "session": "one", "status":
                {"mode": "demo", "map_id": "wrong", "frame": meta["frame"]}}
        def sync():
            return self.request(self.api, "/robot/v1/sync", body, Authorization="Bearer "+"a"*40)
        self.assertEqual(self.request(self.api, "/robot/v1/sync", body, Authorization="Bearer wrong")[0], 401)
        self.assertEqual(sync()[0], 200)
        self.assertEqual(self.robots()["a"]["state"], "map_mismatch")
        self.assertEqual(self.submit("a", "seed_pose", x=1, y=1, yaw_deg=30)[0], 409)
        body["status"]["map_id"] = meta["map_id"]
        self.assertEqual(sync()[0], 200)
        self.assertEqual(self.robots()["a"]["state"], "unlocalized")
        self.assertEqual(self.submit("a", "seed_pose", x=200, y=200, yaw_deg=30)[0], 400)
        status, job = self.submit("a", "status")
        self.assertEqual(status, 202)
        self.assertEqual(sync()[1]["command"]["id"], job["id"])
        self.assertIsNone(sync()[1]["command"])
        body["result"] = {"id": job["id"], "state":"ok", "message":"done"}
        self.assertEqual(sync()[1]["ack"], job["id"])
        self.assertEqual(sync()[1]["ack"], job["id"])
        self.assertEqual(self.job(job["id"])["state"], "ok")
        self.assertEqual(self.request(self.ui, "/api/commands", {}, Origin="https://outside.invalid")[0], 403)
        self.assertEqual(self.request(self.api, "/api/state")[0], 404)

    def test_https_certificate_and_robot_channel(self):
        cert, key = self.d/"local.crt", self.d/"local.key"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                        "-subj", "/CN=fleet", "-addext", "subjectAltName=IP:127.0.0.1", "-out", str(cert),
                        "-keyout", str(key)], check=True, capture_output=True)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert, key)
        self.setup_servers(demo=False, tls=tls)
        cfg = {"id": "a", "token": "a"*40, "host_url": f"https://127.0.0.1:{self.api}"}
        status = {"mode": "ros2", "map_id": self.fleet.map["map_id"], "frame": "world"}
        with self.assertRaises(ssl.SSLCertVerificationError):
            Transport(cfg, self.d).sync(status, None)
        tx = Transport(cfg | {"ca": cert.name}, self.d)
        self.assertEqual(tx.sync(status, None)["version"], 1)
        status_code, job = self.submit("a", "ping")
        self.assertEqual(status_code, 202)
        self.assertEqual(tx.sync(status, None)["command"]["id"], job["id"])
        tx.sync(status, {"id":job["id"],"state":"ok","message":"HTTPS round trip"})
        self.assertEqual(self.job(job["id"])["state"], "ok")

    def test_rotated_map_origin(self):
        import math
        m = {"origin":[12,8,math.pi/2],"resolution":.5,"height":80}
        u,v = image_xy(m,11,9.5)
        self.assertAlmostEqual(u,3)
        self.assertAlmostEqual(v,78)


if __name__ == "__main__":
    unittest.main()
