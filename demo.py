#!/usr/bin/env python3
"""Run a local host and eight simulated robots."""
import argparse
from contextlib import ExitStack
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from configure import ROOT, generate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operator-port", type=int, default=8765)
    parser.add_argument("--robot-port", type=int, default=8766)
    a = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="campus-fleet-demo-") as temp, ExitStack() as stack:
        directory = Path(temp)
        def start(script, config):
            proc = subprocess.Popen([sys.executable, str(ROOT/script), str(directory/config)],
                                    start_new_session=True)
            def stop():
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(4)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
            stack.callback(stop)
            return proc
        positions = [(-.60, -.65, 54), (1.30, .60, -35), (.60, -2.0, 90), (3.10, -.90, 16),
                     (2.70, 2.0, 135), (4.90, -1.90, -75), (5.80, 1.30, -160), (3.80, 3.40, 38)]
        robots = [(f"a210-{i:02}", None, f"机器人 {i:02}", position)
                  for i, position in enumerate(positions, start=1)]
        generate(directory, ROOT/"maps/desk/map.json", robots,
                 bind="127.0.0.1", host_url=f"http://127.0.0.1:{a.robot_port}",
                 port=a.robot_port, ui=a.operator_port, demo=True)
        host = start("host.py", "host.json")
        time.sleep(.2)
        if host.poll() is not None:
            raise SystemExit(host.returncode)
        for robot_id, *_ in robots:
            start("agent.py", robot_id + ".json")
        try:
            host.wait()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
