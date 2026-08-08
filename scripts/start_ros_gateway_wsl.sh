#!/usr/bin/env bash
set -euo pipefail

# Run from WSL. Source ROS and the workspace that contains px4_msgs before this
# script, or set ROS_SETUP / PX4_MSGS_SETUP to explicit setup.bash files.

ROS_SETUP="${ROS_SETUP:-/opt/ros/jazzy/setup.bash}"
PX4_MSGS_SETUP="${PX4_MSGS_SETUP:-}"
GATEWAY_WS="${GATEWAY_WS:-$HOME/ws_px4}"
REPO_ROOT="${REPO_ROOT:-/mnt/c/Users/26494/Desktop/airsim_agent}"
PX4_MSGS_SRC="${PX4_MSGS_SRC:-$GATEWAY_WS/src/px4_msgs}"
AIRSIM_AGENT_ROS_SRC="${AIRSIM_AGENT_ROS_SRC:-$REPO_ROOT/ros2/airsim_agent_ros}"
SYNC_PACKAGE="${SYNC_PACKAGE:-1}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8766}"

source_setup() {
  local setup_file="$1"
  if [ -f "$setup_file" ]; then
    set +u
    # shellcheck source=/dev/null
    source "$setup_file"
    set -u
  fi
}

if [ -f "$ROS_SETUP" ]; then
  source_setup "$ROS_SETUP"
fi

if [ -n "$PX4_MSGS_SETUP" ] && [ -f "$PX4_MSGS_SETUP" ]; then
  source_setup "$PX4_MSGS_SETUP"
fi

if ! command -v ros2 >/dev/null 2>&1; then
  echo "ros2 command not found. Source ROS first or set ROS_SETUP=/path/to/setup.bash" >&2
  exit 1
fi

if ! command -v colcon >/dev/null 2>&1; then
  echo "colcon command not found. Install python3-colcon-common-extensions in WSL." >&2
  exit 1
fi

mkdir -p "$GATEWAY_WS/src"
package_dst="$GATEWAY_WS/src/airsim_agent_ros"
if [ "$SYNC_PACKAGE" = "1" ]; then
  if [ ! -d "$AIRSIM_AGENT_ROS_SRC" ]; then
    echo "airsim_agent_ros source not found: $AIRSIM_AGENT_ROS_SRC" >&2
    exit 1
  fi
  src_real="$(readlink -f "$AIRSIM_AGENT_ROS_SRC")"
  dst_real="$(readlink -m "$package_dst")"
  case "$dst_real" in
    "$GATEWAY_WS/src/"*) ;;
    *) echo "refusing unsafe package target: $package_dst" >&2; exit 1 ;;
  esac
  if [ "$src_real" != "$dst_real" ]; then
    rm -rf -- "$package_dst"
    cp -a "$AIRSIM_AGENT_ROS_SRC" "$package_dst"
  fi
elif [ ! -e "$package_dst" ]; then
  ln -s "$AIRSIM_AGENT_ROS_SRC" "$package_dst"
fi

if ! ros2 pkg prefix px4_msgs >/dev/null 2>&1 && [ ! -e "$GATEWAY_WS/src/px4_msgs" ]; then
  if [ -f "$PX4_MSGS_SRC/package.xml" ]; then
    ln -s "$PX4_MSGS_SRC" "$GATEWAY_WS/src/px4_msgs"
  else
    cat >&2 <<EOF
px4_msgs is not available in this ROS environment.

Install or clone px4_msgs, then rerun this script. Example:

  cd "$HOME"
  git clone https://github.com/PX4/px4_msgs.git
  PX4_MSGS_SRC="$HOME/px4_msgs" bash "$REPO_ROOT/scripts/start_ros_gateway_wsl.sh"

Use a px4_msgs branch/tag compatible with your PX4-Autopilot version.
EOF
    exit 1
  fi
fi

cd "$GATEWAY_WS"
if [ -d "$GATEWAY_WS/install/px4_msgs" ] || ros2 pkg prefix px4_msgs >/dev/null 2>&1; then
  colcon build --packages-select airsim_agent_ros
else
  colcon build --packages-up-to airsim_agent_ros
fi
source_setup install/setup.bash

ros2 run airsim_agent_ros gateway_node --host "$HOST" --port "$PORT" "$@"
