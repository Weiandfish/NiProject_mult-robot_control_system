#!/usr/bin/env bash
# Default domain matches the existing A210 localization dashboard.
set -eo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [ "$#" -ne 1 ]; then echo "用法：$0 /path/to/robot.json" >&2; exit 2; fi
project=${A210_GPS_SLAM_DIR:-"$HOME/gps_slam_current"}
source "$project/scripts/env.sh"
nav_setup=${A210_NAV_SETUP:-"$HOME/ros2_humble_nav/install/local_setup.bash"}
if [[ -r "$nav_setup" ]]; then
    source "$nav_setup"
fi
export ROS_DOMAIN_ID=${A210_RVIZ_DOMAIN_ID:-92}
export ROS_LOCALHOST_ONLY=1
exec "$project/tools/bin/python" "$root/agent.py" "$1"
