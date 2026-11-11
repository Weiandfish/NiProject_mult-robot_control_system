#!/usr/bin/env python3
"""A210 fleet agent; the host never gets access to the board's ROS graph or shell."""
import argparse
import http.client
import math
import os
from pathlib import Path
import ssl
import time
from urllib.parse import urlsplit
from uuid import uuid4

from common import (COMMANDS, LIMIT, SPATIAL, TOKEN, VERSION, decode, encode,
                    ident, is_loopback, read, spatial_args)


def health():
    result = {"load_1m": round(os.getloadavg()[0], 2)}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    result["memory_available_mib"] = int(line.split()[1])//1024
                    break
        result["uptime_s"] = int(float(Path("/proc/uptime").read_text().split()[0]))
    except OSError:
        pass
    return result


def yaw(q):
    norm = math.hypot(q.x, q.y, q.z, q.w)
    if not math.isfinite(norm) or abs(norm-1) > .1:
        raise ValueError("invalid quaternion")
    return math.degrees(math.atan2(2*(q.w*q.z+q.x*q.y), norm*norm-2*(q.y*q.y+q.z*q.z)))


class Local:
    def __init__(self, cfg):
        import rclpy
        from geometry_msgs.msg import PoseWithCovarianceStamped
        from nav_msgs.msg import Odometry
        from sensor_msgs.msg import Imu
        from rclpy.qos import qos_profile_sensor_data

        self.rclpy = rclpy
        rclpy.init(args=[])
        self.node = rclpy.create_node("campus_fleet_agent")
        self.cfg = cfg
        self.pose = None
        self.imu = 0.
        self.blocked_until = 0.
        self.progress = lambda: None
        self.seed_type = PoseWithCovarianceStamped
        self.seed_pub = self.node.create_publisher(PoseWithCovarianceStamped, cfg.get("seed_topic", "/initialpose_ui"), 10)
        self.odom_sub = self.node.create_subscription(
            Odometry, cfg.get("odom_topic", "/Odometry"), self.odom, qos_profile_sensor_data)
        self.imu_sub = self.node.create_subscription(
            Imu, cfg.get("imu_topic", "/mid360/imu"), self.read_imu, qos_profile_sensor_data)
        self.planner = None
        if "preview_path" in cfg["commands"]:
            try:
                from nav2_msgs.action import ComputePathToPose
            except ModuleNotFoundError as error:
                if error.name != "nav2_msgs":
                    raise
                self.node.get_logger().warning("未加载 nav2_msgs，路线预览不可用。")
            else:
                from rclpy.action import ActionClient
                self.goal_type = ComputePathToPose
                action = cfg.get("planner_action", "/compute_path_to_pose")
                self.planner = ActionClient(self.node, ComputePathToPose, action)

    def odom(self, m):
        if (m.header.frame_id != self.cfg["frame"] or m.child_frame_id != self.cfg.get("child_frame", "body")
                or time.monotonic() < self.blocked_until):
            return
        source_ns = m.header.stamp.sec*1_000_000_000+m.header.stamp.nanosec
        source_age = (self.node.get_clock().now().nanoseconds-source_ns)/1e9
        if source_ns <= 0 or not -.5 <= source_age <= 3:
            return
        p = m.pose.pose.position
        try:
            values = (p.x, p.y, p.z, yaw(m.pose.pose.orientation))
            if all(math.isfinite(v) for v in values):
                self.pose = dict(zip(("x", "y", "z", "yaw_deg"), values)), time.monotonic()-max(0, source_age)
        except ValueError:
            pass

    def read_imu(self, _message):
        self.imu = time.monotonic()

    def current(self):
        if not self.pose:
            return None
        pose, at = self.pose
        age = time.monotonic()-at
        return dict(pose, age_s=age) if age < 2.99 else None

    def status(self):
        pose = self.current()
        return {"pose": pose, "imu_fresh": time.monotonic()-self.imu < 2,
                "ros_state": "tracking" if pose else "waiting_for_odometry"}

    def spin(self, timeout):
        self.rclpy.spin_once(self.node, timeout_sec=timeout)

    def await_future(self, future, deadline):
        while not future.done() and self.rclpy.ok() and time.monotonic() < deadline:
            self.spin(.05)
            self.progress()
        if not future.done():
            raise TimeoutError
        return future.result()

    def fill_pose(self, pose, args, z):
        pose.position.x, pose.position.y, pose.position.z = float(args["x"]), float(args["y"]), float(z)
        half = math.radians(args["yaw_deg"])/2
        pose.orientation.z, pose.orientation.w = math.sin(half), math.cos(half)

    def execute(self, command, args):
        if command == "seed_pose":
            if self.seed_pub.get_subscription_count() == 0:
                return "rejected", "本机定位台未订阅初始位姿", {}
            prev = self.current()
            m = self.seed_type()
            m.header.frame_id = self.cfg["frame"]
            m.header.stamp = self.node.get_clock().now().to_msg()
            self.fill_pose(m.pose.pose, args, prev["z"] if prev else 0.)
            m.pose.covariance[0], m.pose.covariance[7], m.pose.covariance[35] = .25, .25, .07
            self.seed_pub.publish(m)
            self.pose, self.blocked_until = None, time.monotonic()+2.
            return "ok", "初始位姿已发布给本机定位台；等待定位输出", {}
        if not self.current():
            return "rejected", "本机无当前定位", {}
        if self.planner is None:
            return "rejected", "本机未加载 Nav2 接口", {}
        if not self.planner.server_is_ready():
            return "rejected", "本机 Nav2 规划服务未就绪", {}
        request = self.goal_type.Goal()
        request.goal.header.frame_id = self.cfg["frame"]
        request.goal.header.stamp = self.node.get_clock().now().to_msg()
        self.fill_pose(request.goal.pose, args, 0.)
        request.planner_id = self.cfg.get("planner_id", "GridBased")
        request.use_start = False
        deadline = time.monotonic()+10
        handle = None
        try:
            handle = self.await_future(self.planner.send_goal_async(request), deadline)
            if not handle or not handle.accepted:
                return "rejected", "Nav2 拒绝规划请求", {}
            result = self.await_future(handle.get_result_async(), deadline)
        except TimeoutError:
            if handle:
                handle.cancel_goal_async()
            return "unknown", "Nav2 未在时限内返回规划结果", {}
        poses = result.result.path.poses
        if result.status != 4 or not poses or result.result.path.header.frame_id != self.cfg["frame"]:
            return "failed", "Nav2 未生成有效路线", {}
        step = max(1, math.ceil(len(poses)/299))
        sample = list(poses[::step])
        if (len(poses)-1) % step:
            sample.append(poses[-1])
        path = [[p.pose.position.x, p.pose.position.y] for p in sample]
        length = sum(math.dist(a, b) for a, b in zip(path, path[1:]))
        return "ok", f"路线预览：{len(poses)} 点，约 {length:.2f} 米", {"path": path}

    def close(self):
        self.node.destroy_node()
        self.rclpy.try_shutdown()


class Demo:
    def __init__(self, cfg):
        self.anchor = list(cfg["demo_pose"])
        self.start = time.monotonic()

    def spin(self, timeout):
        time.sleep(timeout)

    def status(self):
        t = time.monotonic()-self.start
        x, y, h = self.anchor
        return {"pose": {"x": x+.15*math.sin(t*.3), "y": y+.15*math.sin(t*.2),
                         "z": 0., "yaw_deg": h, "age_s": 0.},
                "imu_fresh": True, "ros_state": "simulated"}

    def execute(self, command, args):
        if command == "seed_pose":
            self.anchor = [args["x"], args["y"], args["yaw_deg"]]
            return "ok", "模拟初始位姿已更新", {}
        p = self.status()["pose"]
        return "ok", "模拟路线（直线）", {"path": [[p["x"], p["y"]], [args["x"], args["y"]]]}

    def close(self):
        pass


class Transport:
    def __init__(self, cfg, directory):
        self.robot = cfg["id"]
        self.token = cfg["token"]
        self.session = uuid4().hex
        self.url = urlsplit(cfg["host_url"])
        u = self.url
        if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password or u.path not in ("", "/") or u.query or u.fragment:
            raise ValueError("host_url must be an HTTP(S) origin")
        if u.scheme == "http" and not is_loopback(u.hostname):
            raise ValueError("HTTPS required outside loopback")
        if cfg.get("demo") is True and (not is_loopback(u.hostname) or u.scheme != "http"):
            raise ValueError("simulation requires loopback HTTP")
        ca = cfg.get("ca")
        self.tls = ssl.create_default_context(cafile=str(directory/ca) if ca else None) if u.scheme == "https" else None

    def sync(self, status, result, busy=False):
        cls = http.client.HTTPSConnection if self.tls else http.client.HTTPConnection
        options = {"context": self.tls} if self.tls else {}
        connection = cls(self.url.hostname, self.url.port, timeout=2, **options)
        try:
            connection.request("POST", "/robot/v1/sync", encode({
                "version": VERSION, "robot": self.robot, "session": self.session,
                "status": status, "result": result, "busy": busy}), {"Content-Type": "application/json",
                                                     "Authorization": "Bearer "+self.token})
            res = connection.getresponse()
            body = res.read(LIMIT+1)
            if res.status != 200:
                raise ValueError(f"主机拒绝同步（HTTP {res.status}）")
            if len(body) > LIMIT:
                raise ValueError("response size exceeded")
            data = decode(body)
            if not isinstance(data, dict) or data.get("version") != VERSION:
                raise ValueError("incompatible protocol")
            return data
        finally:
            connection.close()


def execute(job, cfg, local):
    key = job["id"]
    command, args = job.get("command"), job.get("args")
    state, message, extra = "rejected", "指令未在板端启用", {}
    if isinstance(command, str) and command in cfg["commands"]:
        if command in SPATIAL:
            if job.get("map_id") != cfg["map_id"] or job.get("frame") != cfg["frame"] or not spatial_args(args):
                message = "地图版本、坐标系或目标不匹配"
            else:
                state, message, extra = local.execute(command, args)
        elif args == {}:
            state = "ok"
            if command == "ping":
                message = "板端已收到探测"
            else:
                data = local.status()
                mem = health().get("memory_available_mib")
                mem = f"{mem} MiB" if mem is not None else "未知"
                message = f"定位={'有位姿' if data['pose'] else '等待'}；雷达={'有 IMU' if data['imu_fresh'] else '无 IMU'}；可用内存={mem}"
    return {"id": key, "state": state, "message": message} | extra


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    a = parser.parse_args()
    cfg = read(a.config)
    if not all(ident(cfg.get(k)) for k in ("id", "map_id", "frame")) or not TOKEN.fullmatch(cfg["token"]):
        parser.error("invalid ID, frame, map or token")
    cfg.setdefault("commands", ["ping", "status"])
    if not isinstance(cfg["commands"], list) or not set(cfg["commands"]) <= COMMANDS:
        parser.error("unknown command in agent allowlist")
    demo = cfg.get("demo", False) is True
    tx = Transport(cfg, a.config.resolve().parent)
    local = Demo(cfg) if demo else Local(cfg)
    result = None
    delay = 0.
    last_error = None
    first_sync = True

    def snapshot():
        return local.status() | health() | {
            "mode": "demo" if demo else "ros2", "map_id": cfg["map_id"], "frame": cfg["frame"]}

    last_progress = 0.

    def progress():
        nonlocal last_progress
        now = time.monotonic()
        if now-last_progress >= .75:
            last_progress = now
            try:
                tx.sync(snapshot(), None, busy=True)
            except (OSError, http.client.HTTPException, ValueError):
                pass

    if not demo:
        local.progress = progress
    try:
        while demo or local.rclpy.ok():
            next_at = time.monotonic()+delay
            while time.monotonic() < next_at:
                local.spin(min(.05, max(0, next_at-time.monotonic())))
            try:
                res = tx.sync(snapshot(), result)
            except (OSError, http.client.HTTPException, ValueError) as e:
                msg = str(e)
                if msg != last_error:
                    print(f"同步失败：{msg}", flush=True)
                last_error, delay = msg, min(2., max(.5, delay*2))
                continue
            if last_error is not None or first_sync:
                print(f"{cfg['id']} 已连接主机", flush=True)
            first_sync = False
            last_error, delay = None, .5
            if result and res.get("ack") == result["id"]:
                result = None
            job = res.get("command")
            if not result and isinstance(job, dict) and ident(job.get("id")):
                try:
                    result = execute(job, cfg, local)
                except Exception as e:
                    result = {"id": job["id"], "state": "failed", "message": f"本机执行异常：{type(e).__name__}"}
                delay = 0.
    except KeyboardInterrupt:
        pass
    finally:
        local.close()


if __name__ == "__main__":
    main()
