# 校园机器人集成控制台

主机集中显示所有机器人的位置和状态，并向指定 A210 发送指令。已有的 GPS-SLAM、MID360 驱动和 Nav2 继续在各板上运行。控制台不采集雷达原始数据，不运行 SLAM。

```text
操作员浏览器 ── 本机 127.0.0.1:8765 ── 控制主机 host.py
                                            ▲
                               A210 主动访问 │ HTTPS :8766
                           位置、状态、回执 ↑│↓ 待执行指令
                                            │
                A210 的 agent.py ── 本机 ROS 2 ── GPS-SLAM / 定位台 / Nav2
```

## 先看演示

在本目录运行 `python3 demo.py`，浏览器打开 [本机控制台](http://127.0.0.1:8765)。演示使用 Python 3.10+ 和此前的 MID360 局部地图，八台模拟机器人同时上报位姿。界面默认显示车队状态、设备坐标和地图编号。点选列表或地图上的一台机器人后，选择指令并发送。演示的路线采用直线，实机的路线来自 Nav2。按 Ctrl+C 停止演示。

## 共用 Git 仓库

NUC 和 A210 使用同一版本的源码。NUC 主机按 Ubuntu/Debian 和 Python 3.10+ 准备，运行网页和 HTTPS 服务。A210 板端在现有 ROS 2 Humble 环境里运行代理，读取本机位姿和 IMU。通信探测、状态上报和初始位姿使用现有 ROS 接口，启用路线预览时接入板上的 Nav2。

部署时可把 `campus-fleet-v0.1.0.bundle` 复制到两台机器，从同一标签克隆：

```bash
git clone --branch v0.1.0 ./campus-fleet-v0.1.0.bundle "$HOME/campus-fleet"
cd "$HOME/campus-fleet"
git rev-parse HEAD
```

如果后续希望在校园网内拉取更新，可在 NUC 上建立同一仓库的裸库。板端通过 NUC 的 SSH 账号和只读部署权限克隆，NUC 本机可用裸库的本地路径克隆。下面的 `NUC_USER`、`NUC_IP` 和绝对目录按现场替换。

```bash
# NUC 上建立 Git 远端
mkdir -p "$HOME/git"
git clone --bare ./campus-fleet-v0.1.0.bundle "$HOME/git/campus-fleet.git"

# A210 上从该远端克隆指定版本
git clone --branch v0.1.0 'NUC_USER@NUC_IP:/home/NUC_USER/git/campus-fleet.git' "$HOME/campus-fleet"
```

新版本通过 Git 标签发布。正式更新时先停止对应服务，`git fetch --tags` 后切换到目标标签，再启动服务。地图数据和凭据保存在仓库外。Git bundle 是本仓库的离线分发文件。

## NUC 主机

1. 给 NUC 和每台 A210 做 DHCP 地址保留，登记设备 ID、管理口 MAC 和 IP。各网段需要访问 NUC 的 TCP `8766`。板端的雷达继续走独立的 eth0。
2. 在 NUC 的仓库目录执行以下命令。用实际管理 IP 生成证书，并给每台 A210 生成独立配置。示例为自签证书，有校园内部 CA 时可换成相应证书和公有信任链。首次部署将示例中的机器人 IP 改为实际地址。

   ```bash
   read -r -p 'NUC 的固定 IPv4 地址：' NUC_IP
   install -d -m 700 "$HOME/.config/campus-fleet"
   umask 077
   openssl req -x509 -newkey rsa:3072 -nodes -days 365 \
     -subj /CN=campus-fleet -addext "subjectAltName=IP:$NUC_IP" \
     -keyout "$HOME/.config/campus-fleet/fleet.key" \
     -out "$HOME/.config/campus-fleet/fleet-ca.crt"
   python3 configure.py "$HOME/.config/campus-fleet" \
     --host "$NUC_IP" \
     --cert "$HOME/.config/campus-fleet/fleet-ca.crt" \
     --key "$HOME/.config/campus-fleet/fleet.key" \
     --robot 'a210-01,192.168.30.113,一号车'
   ```

   可重复传入 `--robot` 注册多台。想启用初始位姿和规划预览时，在主机与对应板端配置的 `commands` 中加入 `seed_pose` 和 `preview_path`，或在首次生成时加 `--allow-spatial`。校园地图就绪后，用 `--map /absolute/path/to/map.json` 指定地图包。
3. 安装并启动 NUC 服务。在 NUC 本机打开 `http://127.0.0.1:8765`。在另一台电脑上操作时可用 `ssh -N -L 8765:127.0.0.1:8765 NUC_USER@NUC_IP`，然后在该电脑的浏览器打开同一地址。

   ```bash
   bash deploy/install-service.sh nuc
   systemctl --user enable --now campus-fleet-nuc.service
   journalctl --user -u campus-fleet-nuc.service -n 30
   ```

## A210 板端

1. 克隆同一标签。将 NUC 生成的**对应机器人** JSON 安装为 `~/.config/campus-fleet/robot.json`，将公有证书 `fleet-ca.crt` 放在同一目录。配置目录用 `0700`，JSON 用 `0600`。
2. 用板上原有入口启动 GPS-SLAM 定位。代理默认复用 `~/gps_slam_current` 的 Python 和 ROS 2 Humble，使用定位台的 ROS domain 92。它监听 `/Odometry`（`world → body`）和 `/mid360/imu`，初始位姿发给 `/initialpose_ui`。ROS 主题可在机器人 JSON 中调整。若已有安装位置或 domain 不同，可在 `~/.config/campus-fleet/a210.env` 中填写 `A210_GPS_SLAM_DIR`、`A210_NAV_SETUP`、`A210_RVIZ_DOMAIN_ID`。环境文件中的路径用绝对路径。

   ```bash
   cd "$HOME/campus-fleet"
   bash deploy/install-service.sh a210
   systemctl --user enable --now campus-fleet-a210.service
   journalctl --user -u campus-fleet-a210.service -n 30
   ```

   代理接通后即显示在线，收到有效位姿后才显示已定位。需要开机自动启动且设备通常无人登录时，在对应机器上执行 `sudo loginctl enable-linger "$USER"`。需要前台排查时，先停止对应服务，再用 `python3 host.py "$HOME/.config/campus-fleet/host.json"` 或 `bash run-a210.sh "$HOME/.config/campus-fleet/robot.json"`。 [systemd 服务说明](https://manpages.debian.org/bookworm/systemd/systemd.service.5.en.html)

## 接入校园地图

- 主机只需二维 PNG 底图和同目录的 `map.json`，格式见 [现有样例](maps/desk/map.json)。保存全局点云 PCD 给各板定位，另用建图轨迹与点云生成二维栅格。底图可以显示全校园；上下层或局部原点不共用的区域，先统一坐标或扩展分层地图。已存在的桌面脚本用单一观测原点推断空闲区域，不能直接当作校园级通行图。
- `resolution` 为米/像素；`origin` 是 PNG **左下角**在 ROS 坐标中的 `[x, y, yaw_rad]`；PNG 行从上往下。若已有 ROS OccupancyGrid，可沿用其分辨率、原点和 frame，保留原图尺寸；若导出时缩放分辨率，按比例调整。界面预览图限制 3200 万像素；显示图可以降采样，板端 Nav2 仍使用规划栅格。
- `map_id` 为统一发布的地图版本。样例使用旧 PCD 的 SHA-256；如果校园地图同时发布 PCD、规划图和显示图，可用同一个地图包版本或清单哈希。主机切换 `--map` 的文件；每块 A210 必须先加载这版定位地图，再更新自己的 `map_id` 和 `frame`。同叫 `world` 的两张局部图不能直接叠加。配置版本是部署约定，代理不会从 ROS 自动检查板端实际加载的 PCD；试点时须核对地图文件及两个以上测量控制点。若要叠加地理底图，先把它配准到同一平面坐标系。

## 状态与指令语义

- 每块板通过独立 ID 和 token 认证，固定 IP 可作为第二项校验。在线与定位分开：正常每 0.5 秒上报，4 秒未收到心跳判为离线；约 3 秒未见新鲜位姿则不再标为实时位置。离线保留灰色最后位置；版本不符不绘制位置。IMU 指示表示近期有数据，不能代替硬件故障诊断。
- 只有预置的四种指令可以发送。初始位姿的成功回执表示已发布给本机定位台，等新的有效位姿才算恢复定位。路线预览调用 `/compute_path_to_pose`，不调用运动执行接口。暂未接入行驶、底盘急停或电池接口。
- 每条指令有 ID。排队最多 6 秒，交给板端后 24 秒无回执显示「结果未知」；已交付的指令不自动重发，迟到回执仍可更新状态。主机重启时会丢失内存中的回执与末位；本版本没有历史数据库、操作员分权或主机高可用。正式扩展前先测多板并发和 AP 漫游，再决定是否需要持久化、告警、地图瓦片和任务调度。机器人运动、避障及断网安全策略由板端执行，不把这个控制台当作安全控制器。

## 验证

```bash
python3 -m unittest discover -s tests -v
```

覆盖两台独立代理的位姿、指令分发和回执、掉线与重新上线；地图版本和坐标越界拒绝；同一条指令只发一次、重复回执；以及实际 HTTPS 下的证书验证和通信。另用 Chrome 实际点击了通信探测和路线预览，浏览器收到对应回执。实机 ROS 2 接入代码依照现有定位台接口完成，待 A210 恢复 SSH 后进行部署及校园网验证。
