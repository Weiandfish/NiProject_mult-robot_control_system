#!/usr/bin/env python3
"""Run a local host and two clearly marked simulated robots."""
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
        generate(directory, ROOT/"maps/desk/map.json",
                 [("a210-01", None, "一号车", [-.6, -.65, 54]),
                  ("a210-02", None, "二号车", [1.3, .6, -35]),
                  ("a210-03", None, "三号车", [0., 0., 0])],
                 bind="127.0.0.1", host_url=f"http://127.0.0.1:{a.robot_port}",
                 port=a.robot_port, ui=a.operator_port, demo=True)
        host = start("host.py", "host.json")
        time.sleep(.2)
        if host.poll() is not None:
            raise SystemExit(host.returncode)
        start("agent.py", "a210-01.json")
        start("agent.py", "a210-02.json")
        try:
            host.wait()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
