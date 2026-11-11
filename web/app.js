"use strict";

const byId = id => document.getElementById(id);
const canvas = byId("map");
const ctx = canvas.getContext("2d");
const commandChoice = byId("commandChoice");
const spatialCommands = new Set(["seed_pose", "preview_path"]);
const statusNames = {
  localized: "已定位",
  offline: "离线",
  map_mismatch: "地图不一致",
  unlocalized: "等待定位",
  stale: "位置过期",
  disconnected: "连接中断"
};
const jobStates = {
  queued: "待发送", sent: "待回执", ok: "已回执", rejected: "已拒绝",
  failed: "失败", unknown: "结果未知", expired: "已过期"
};
const commandNames = {
  ping: "通信探测", status: "读取状态", seed_pose: "初始位姿", preview_path: "路线预览"
};
const colors = ["#166c59", "#2365a1", "#a54819", "#694ba0", "#943557", "#3e6a2b", "#176c79", "#796019"];
const lastPositionColor = "#596963";
const radians = Math.PI / 180;
const roster = new Map();
const submitting = new Set();
let data, image, selected, target, drag, markers = [];
let connected = false;
let draftVersion = 0;
let receiptSignature;
let viewWidth = 1, viewHeight = 1;
let scale = 1, offsetX = 0, offsetY = 0;
let fitted = true;

function element(tag, text, className) {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}

function selectedRobot() {
  return data?.robots.find(robot => robot.id === selected);
}

function robotState(robot) {
  return connected ? robot.state : "disconnected";
}

function markerColor(robot, index) {
  return robotState(robot) === "localized" ? colors[index % colors.length] : lastPositionColor;
}

function markerNumber(index) {
  return String(index + 1).padStart(2, "0");
}

function toImage(x, y) {
  const {origin: [ox, oy, yaw], resolution, height} = data.map;
  const dx = x - ox, dy = y - oy;
  const c = Math.cos(yaw), s = Math.sin(yaw);
  return [(c * dx + s * dy) / resolution, height - (-s * dx + c * dy) / resolution];
}

function toWorld(u, v) {
  const {origin: [ox, oy, yaw], resolution, height} = data.map;
  const x = u * resolution, y = (height - v) * resolution;
  const c = Math.cos(yaw), s = Math.sin(yaw);
  return [ox + c * x - s * y, oy + s * x + c * y];
}

function toScreen(x, y) {
  const [u, v] = toImage(x, y);
  return [offsetX + scale * u, offsetY + scale * v];
}

function onMap(u, v) {
  return u >= 0 && u < data.map.width && v > 0 && v <= data.map.height;
}

function fit() {
  if (!data) return;
  const {width, height} = data.map;
  fitted = true;
  scale = Math.max(.001, Math.min((viewWidth - 56) / width, (viewHeight - 56) / height));
  offsetX = (viewWidth - width * scale) / 2;
  offsetY = (viewHeight - height * scale) / 2;
  draw();
}

function zoom(factor, x = viewWidth / 2, y = viewHeight / 2) {
  if (!data) return;
  fitted = false;
  const next = Math.min(200, Math.max(.005, scale * factor));
  offsetX = x - (x - offsetX) * next / scale;
  offsetY = y - (y - offsetY) * next / scale;
  scale = next;
  draw();
}

function circle(x, y, radius, fill, stroke, width = 1) {
  ctx.beginPath();
  ctx.arc(x, y, radius, 0, 2 * Math.PI);
  ctx.fillStyle = fill;
  ctx.fill();
  if (stroke) {
    ctx.strokeStyle = stroke;
    ctx.lineWidth = width;
    ctx.stroke();
  }
}

function line(x, y, u, v, color, width) {
  ctx.beginPath();
  ctx.moveTo(x, y);
  ctx.lineTo(u, v);
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.stroke();
}

function markerCenter(x, y) {
  const free = (px, py) => px >= 14 && py >= 14 && px <= viewWidth - 14 && py <= viewHeight - 14 &&
    markers.every(pin => Math.hypot(px - pin.x, py - pin.y) >= 26);
  if (free(x, y)) return [x, y];
  // Move overlapping labels while keeping the actual pose at the end of the leader.
  for (const distance of [28, 54, 80]) {
    for (let step = 0; step < 8; step++) {
      const angle = step * Math.PI / 4;
      const px = x + distance * Math.cos(angle), py = y + distance * Math.sin(angle);
      if (free(px, py)) return [px, py];
    }
  }
  return [x, y];
}

function drawMarkers() {
  markers = [];
  data.robots.forEach((robot, index) => {
    if (!robot.pose || !onMap(...toImage(robot.pose.x, robot.pose.y))) return;
    const [anchorX, anchorY] = toScreen(robot.pose.x, robot.pose.y);
    if (anchorX < 0 || anchorY < 0 || anchorX > viewWidth || anchorY > viewHeight) return;
    const [x, y] = markerCenter(anchorX, anchorY);
    markers.push({robot, index, x, y, anchorX, anchorY});
  });

  for (const {robot, index, x, y, anchorX, anchorY} of markers) {
    const color = markerColor(robot, index);
    if (x !== anchorX || y !== anchorY) {
      line(anchorX, anchorY, x, y, color, 1.5);
      circle(anchorX, anchorY, 3, color, "#fff");
    }
    if (robotState(robot) === "localized") {
      const angle = robot.pose.yaw_deg * radians - data.map.origin[2];
      line(x, y, x + 17 * Math.cos(angle), y - 17 * Math.sin(angle), color, 3);
    }
  }
  for (const {robot, index, x, y} of markers) {
    const live = robotState(robot) === "localized";
    const color = markerColor(robot, index);
    if (robot.id === selected) circle(x, y, 14, "#fff", color, 2);
    circle(x, y, 10.5, live ? color : "#fff", live ? "#fff" : color, 1.5);
    ctx.fillStyle = live ? "#fff" : color;
    ctx.font = "650 10px ui-monospace, monospace";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(markerNumber(index), x, y + .5);
  }
}

function drawScale() {
  const metersPerPixel = data.map.resolution / scale;
  const value = 10 ** Math.floor(Math.log10(85 * metersPerPixel));
  const span = value / metersPerPixel, y = viewHeight - 20;
  ctx.fillStyle = "#f8fbf9";
  ctx.fillRect(9, y - 20, Math.max(span + 20, 64), 32);
  line(16, y, 16 + span, y, "#263f37", 2);
  ctx.fillStyle = "#263f37";
  ctx.font = "12px system-ui";
  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";
  ctx.fillText(value + " m", 16, y - 5);
}

function draw() {
  ctx.clearRect(0, 0, viewWidth, viewHeight);
  if (!data) return;
  if (image?.complete && image.naturalWidth) {
    ctx.imageSmoothingEnabled = scale < 1;
    ctx.drawImage(image, offsetX, offsetY, data.map.width * scale, data.map.height * scale);
  }
  const robot = selectedRobot();
  if (robot && robotState(robot) === "localized" && robot.plan?.length) {
    ctx.beginPath();
    robot.plan.forEach(([x, y], index) => {
      const point = toScreen(x, y);
      if (index === 0) ctx.moveTo(...point);
      else ctx.lineTo(...point);
    });
    ctx.strokeStyle = markerColor(robot, data.robots.indexOf(robot));
    ctx.lineWidth = 3;
    ctx.stroke();
  }
  if (target && spatialCommands.has(commandChoice.value)) {
    const [x, y] = toScreen(...target);
    line(x - 7, y, x + 7, y, "#805000", 2);
    line(x, y - 7, x, y + 7, "#805000", 2);
  }
  drawMarkers();
  drawScale();
}

function positionText(robot) {
  if (!robot.pose) return "暂无位置";
  const {x, y, yaw_deg: yaw} = robot.pose;
  const label = robotState(robot) === "localized" ? "" : "末位 ";
  return `${label}X ${x.toFixed(2)} · Y ${y.toFixed(2)} m · ${yaw.toFixed(0)}°`;
}

function makeRobotRow(id) {
  const button = element("button", "", "robot");
  button.type = "button";
  button.dataset.robot = id;
  button.setAttribute("aria-controls", "commandPane");
  button.onclick = () => selectRobot(id);
  const pin = element("span", "", "markerKey");
  const name = element("strong", "", "robotName");
  const status = element("span", "", "state");
  const pose = element("span", "", "robotPose");
  button.append(pin, name, status, pose);
  return {button, pin, name, status, pose};
}

function renderRoster(robots) {
  const list = byId("robots");
  const needsOrder = robots.length !== roster.size ||
    robots.some((robot, index) => list.children[index]?.dataset.robot !== robot.id);
  if (needsOrder) {
    const next = robots.map(robot => {
      if (!roster.has(robot.id)) roster.set(robot.id, makeRobotRow(robot.id));
      return roster.get(robot.id).button;
    });
    for (const id of roster.keys()) {
      if (!robots.some(robot => robot.id === id)) roster.delete(id);
    }
    list.replaceChildren(...next);
  }
  if (!robots.length) {
    list.replaceChildren(element("p", "尚未登记机器人", "empty"));
  }
  robots.forEach((robot, index) => {
    const {button, pin, name, status, pose} = roster.get(robot.id);
    const state = robotState(robot);
    button.setAttribute("aria-pressed", robot.id === selected);
    button.title = `${robot.id} · ${robot.address || robot.expected_ip || "无连接地址"}`;
    pin.textContent = markerNumber(index);
    pin.style.setProperty("--marker", markerColor(robot, index));
    name.textContent = robot.name;
    status.textContent = statusNames[state];
    status.dataset.state = state;
    pose.textContent = positionText(robot);
  });
}

function renderOverview(robots) {
  const online = robots.filter(robot => robot.online).length;
  const localized = robots.filter(robot => robot.state === "localized").length;
  byId("onlineCount").textContent = connected ? `${online} / ${robots.length}` : "—";
  byId("localizedCount").textContent = connected ? localized : "—";
  byId("waitingCount").textContent = connected ? online - localized : "—";
  byId("offlineCount").textContent = connected ? robots.length - online : "—";
  byId("fleetCount").textContent = `${robots.length} 台`;
}

function canCommand(robot, command) {
  if (!connected || !robot?.online || !robot.commands.includes(command)) return false;
  if (command === "seed_pose") return robot.state !== "map_mismatch";
  if (command === "preview_path") return robot.state === "localized";
  return true;
}

function renderReceipts(robot) {
  const jobs = data.jobs.filter(job => job.robot === robot.id).slice(0, 3);
  const signature = JSON.stringify([robot.id, jobs]);
  if (signature === receiptSignature) return;
  receiptSignature = signature;
  const list = byId("jobs");
  list.hidden = !jobs.length;
  list.replaceChildren(...jobs.map(job => {
    const time = new Date(job.time * 1000).toLocaleTimeString();
    const item = element("li", `${time} · ${commandNames[job.command]} · ${jobStates[job.state]}`);
    item.append(element("small", job.message));
    return item;
  }));
}

function renderCommandPane() {
  const robot = selectedRobot();
  byId("commandPane").hidden = !robot;
  canvas.classList.toggle("picking", !!robot && canCommand(robot, commandChoice.value) &&
    spatialCommands.has(commandChoice.value));
  if (!robot) return;

  byId("selection").textContent = `指令 · ${robot.name}`;
  const state = robotState(robot);
  const address = robot.address || robot.expected_ip;
  const details = [robot.id, address, statusNames[state]];
  if (connected && robot.online) {
    details.push(robot.data?.imu_fresh ? "IMU 有数据" : "IMU 无数据");
    if (robot.data?.memory_available_mib != null) {
      details.push(`内存 ${robot.data.memory_available_mib} MiB`);
    }
  }
  byId("detail").textContent = details.filter(Boolean).join(" · ");
  for (const option of commandChoice.options) {
    option.disabled = !!option.value && !canCommand(robot, option.value);
  }
  commandChoice.disabled = !connected || !robot.online || submitting.has(robot.id);
  byId("coordinates").hidden = !spatialCommands.has(commandChoice.value);
  byId("sendCommand").disabled = !canCommand(robot, commandChoice.value) || submitting.has(robot.id);
  renderReceipts(robot);
}

function render() {
  const scope = byId("scope");
  scope.textContent = connected ? (data.demo ? "模拟车队" : "实机车队") : "控制台连接中断";
  scope.className = connected ? (data.demo ? "demo" : "") : "disconnected";
  if (!data) return;
  byId("mapName").textContent = data.map.title || data.map.map_id;
  byId("mapInfo").textContent = `${data.map.frame} · ${data.map.resolution} m/px`;
  renderOverview(data.robots);
  renderRoster(data.robots);
  renderCommandPane();
  draw();
}

function notice(message, error = false) {
  const node = byId("notice");
  node.textContent = message;
  node.hidden = !message;
  node.className = error ? "error" : "";
}

function selectRobot(id, reveal = true) {
  if (id === selected) return;
  selected = id;
  draftVersion++;
  byId("commandForm").reset();
  target = null;
  notice("");
  render();
  if (id && reveal && matchMedia("(max-width: 760px)").matches) {
    byId("commandPane").scrollIntoView({block: "nearest"});
  }
}

function spatialArguments() {
  const fields = ["x", "y", "yaw"].map(byId);
  if (fields.some(field => field.value === "" || !field.checkValidity())) return null;
  return {x: Number(fields[0].value), y: Number(fields[1].value), yaw_deg: Number(fields[2].value)};
}

async function sendCommand(event) {
  event.preventDefault();
  const robot = selectedRobot();
  const command = commandChoice.value;
  if (!canCommand(robot, command) || submitting.has(robot.id)) return;
  let args = {};
  if (spatialCommands.has(command)) {
    args = spatialArguments();
    if (!args) {
      notice("请在地图点选目标，或填写 X、Y 和朝向。", true);
      return;
    }
    if (command === "seed_pose" && !data.demo &&
        !confirm(`确认 ${robot.name} 已停稳，并重新设置初始位姿？`)) return;
  }

  const version = draftVersion;
  submitting.add(robot.id);
  renderCommandPane();
  try {
    const response = await fetch("/api/commands", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({robot: robot.id, command, args, map_id: data.map.map_id}),
      signal: AbortSignal.timeout(4000)
    });
    const result = await response.json();
    if (!response.ok) throw Error(result.error);
    if (version === draftVersion) notice(`${commandNames[command]}已提交。回执显示在下方。`);
  } catch (error) {
    if (version === draftVersion) notice(error.message, true);
  } finally {
    submitting.delete(robot.id);
    renderCommandPane();
  }
}

byId("commandForm").addEventListener("submit", sendCommand);
commandChoice.onchange = () => {
  draftVersion++;
  if (!spatialCommands.has(commandChoice.value)) target = null;
  notice(spatialCommands.has(commandChoice.value) ? "在地图点选目标，或输入坐标。" : "");
  renderCommandPane();
  draw();
};
for (const id of ["x", "y"]) {
  byId(id).oninput = () => {
    target = null;
    draw();
  };
}
byId("closeSelection").onclick = () => {
  const previous = selected;
  selectRobot(null);
  roster.get(previous)?.button.focus();
};
byId("zoomIn").onclick = () => zoom(1.25);
byId("zoomOut").onclick = () => zoom(.8);
byId("fit").onclick = fit;

new ResizeObserver(() => {
  const width = canvas.clientWidth, height = canvas.clientHeight;
  if (!width || !height) return;
  offsetX += (width - viewWidth) / 2;
  offsetY += (height - viewHeight) / 2;
  viewWidth = width;
  viewHeight = height;
  const dpr = devicePixelRatio || 1;
  canvas.width = width * dpr;
  canvas.height = height * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  if (fitted) fit();
  else draw();
}).observe(canvas);

function pointer(event) {
  const bounds = canvas.getBoundingClientRect();
  return [event.clientX - bounds.left, event.clientY - bounds.top];
}

canvas.addEventListener("wheel", event => {
  if (!data) return;
  event.preventDefault();
  zoom(event.deltaY < 0 ? 1.18 : .85, ...pointer(event));
}, {passive: false});
canvas.onpointerdown = event => {
  if (!data || event.button !== 0) return;
  canvas.setPointerCapture(event.pointerId);
  drag = {point: pointer(event), offsetX, offsetY, moved: false};
};
canvas.onpointermove = event => {
  if (!data) return;
  const [x, y] = pointer(event);
  if (drag) {
    const dx = x - drag.point[0], dy = y - drag.point[1];
    if (drag.moved || Math.hypot(dx, dy) > 3) {
      drag.moved = true;
      fitted = false;
      canvas.classList.add("dragging");
      offsetX = drag.offsetX + dx;
      offsetY = drag.offsetY + dy;
      draw();
    }
  }
  const [wx, wy] = toWorld((x - offsetX) / scale, (y - offsetY) / scale);
  byId("cursor").textContent = `X ${wx.toFixed(2)} m · Y ${wy.toFixed(2)} m`;
};
canvas.onpointerup = event => {
  if (!drag) return;
  if (!drag.moved) {
    const [x, y] = pointer(event);
    const pin = markers.findLast(item => Math.hypot(item.x - x, item.y - y) <= 12);
    if (pin) {
      selectRobot(pin.robot.id);
    } else if (spatialCommands.has(commandChoice.value) && canCommand(selectedRobot(), commandChoice.value)) {
      const u = (x - offsetX) / scale, v = (y - offsetY) / scale;
      if (onMap(u, v)) {
        target = toWorld(u, v).map(value => Number(value.toFixed(2)));
        byId("x").value = target[0].toFixed(2);
        byId("y").value = target[1].toFixed(2);
        draw();
      }
    }
  }
  drag = null;
  canvas.classList.remove("dragging");
};
canvas.onpointercancel = () => {
  drag = null;
  canvas.classList.remove("dragging");
};

async function refresh() {
  try {
    const response = await fetch("/api/state", {signal: AbortSignal.timeout(3000)});
    if (!response.ok) throw Error("unavailable");
    const next = await response.json();
    const mapChanged = next.map.map_id !== data?.map.map_id;
    connected = true;
    data = next;
    if (selected && (mapChanged || !selectedRobot())) selectRobot(null, false);
    if (mapChanged) {
      image = new Image();
      image.onload = fit;
      image.src = next.map.image;
      fit();
    }
    render();
  } catch {
    connected = false;
    render();
  } finally {
    setTimeout(refresh, 850);
  }
}
refresh();
