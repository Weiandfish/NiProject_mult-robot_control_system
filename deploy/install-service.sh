#!/usr/bin/env bash
set -euo pipefail

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
case "${1:-}" in
    nuc) config=host.json ;;
    a210) config=robot.json ;;
    *) printf '用法：bash deploy/install-service.sh {nuc|a210}\n' >&2; exit 2 ;;
esac
unit="campus-fleet-$1.service"
if [[ ! -d "$HOME/campus-fleet" || ! "$HOME/campus-fleet" -ef "$root" ]]; then
    printf '请将本仓库克隆或链接到 %s/campus-fleet。\n' "$HOME" >&2
    exit 1
fi
if [[ ! -r "$HOME/.config/campus-fleet/$config" ]]; then
    printf '请先准备 %s/.config/campus-fleet/%s。\n' "$HOME" "$config" >&2
    exit 1
fi
if ! systemctl --user show-environment >/dev/null; then
    printf '请在部署账户的 Linux 登录会话中运行，确认用户 systemd 已启动。\n' >&2
    exit 1
fi
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
install -d "$unit_dir"
install -m 0644 "$root/deploy/systemd/$unit" "$unit_dir/$unit"
systemctl --user daemon-reload
printf '已安装 %s。启动并启用服务：systemctl --user enable --now %s\n' "$unit" "$unit"
