"use strict";
const $ = id => document.getElementById(id);
const c = $("map"), g = c.getContext("2d");
const info = {
  localized: "已定位", offline: "离线", map_mismatch: "地图不一致", unlocalized: "等待定位", stale: "位置已过期"
};
const states = {queued:"待发送",sent:"待回执",ok:"已回执",rejected:"已拒绝",failed:"失败",unknown:"结果未知",expired:"已过期"};
const commands = {ping:"通信探测",status:"读取状态",seed_pose:"初始位姿",preview_path:"路线预览"};
const colors = ["#007560", "#2264aa", "#a94f19", "#7249a7", "#8b3859"];
let data, selected, img, scale = 1, dx = 0, dy = 0, target, drag, connected = false, lastList = "";
let vw = 1, vh = 1;
const rad = Math.PI/180;
function element(tag, text, cls) {
  const n = document.createElement(tag); n.textContent = text;
  if (cls) n.className = cls;
  return n;
}
function xy(x,y) {
  const m=data.map, [ox,oy,t]=m.origin, X=x-ox,Y=y-oy;
  return [(X*Math.cos(t)+Y*Math.sin(t))/m.resolution,
          m.height-(-X*Math.sin(t)+Y*Math.cos(t))/m.resolution];
}
function world(u,v) {
  const m=data.map, [ox,oy,t]=m.origin, X=u*m.resolution,Y=(m.height-v)*m.resolution;
  return [ox+X*Math.cos(t)-Y*Math.sin(t), oy+X*Math.sin(t)+Y*Math.cos(t)];
}
function fit() {
  if (!data) return;
  const m=data.map;
  scale=Math.max(.001,Math.min((vw-48)/m.width,(vh-48)/m.height));
  dx=(vw-m.width*scale)/2; dy=(vh-m.height*scale)/2;
  draw();
}
function zoom(f,x=vw/2,y=vh/2) {
  const next=Math.min(200,Math.max(.005,scale*f)), ratio=next/scale;
  dx=x-(x-dx)*ratio; dy=y-(y-dy)*ratio; scale=next; draw();
}
function dot(x,y,col,r=6) {
  g.beginPath(); g.arc(x,y,r,0,2*Math.PI); g.fillStyle=col; g.fill();
}
function screen(p) {
  const [u,v]=xy(p[0],p[1]); return [dx+scale*u,dy+scale*v];
}
function draw() {
  g.clearRect(0,0,vw,vh);
  if (!data) return;
  const m=data.map;
  if (img?.complete && img.naturalWidth) {
    g.imageSmoothingEnabled=scale<1;
    g.drawImage(img,dx,dy,m.width*scale,m.height*scale);
  }
  if (target) {
    const [x,y]=screen(target);
    g.strokeStyle="#805000";g.lineWidth=2;g.beginPath();
    g.moveTo(x-8,y);g.lineTo(x+8,y);g.moveTo(x,y-8);g.lineTo(x,y+8);g.stroke();
  }
  data.robots.forEach((r,i)=>{
    const color=colors[i%colors.length];
    if (r.id===selected && r.plan?.length && connected && r.state==="localized") {
      g.strokeStyle=color;g.lineWidth=3;g.beginPath();
      r.plan.forEach((p,j)=>{const [x,y]=screen(p);j?g.lineTo(x,y):g.moveTo(x,y);});
      g.stroke();
    }
    if (!r.pose) return;
    const [u,v]=xy(r.pose.x,r.pose.y);
    if (u<0 || v<0 || u>=m.width || v>m.height) return;
    const x=dx+scale*u,y=dy+scale*v,live=r.state==="localized"&&connected;
    if (selected===r.id) dot(x,y,"#ffffff",11);
    if (live) {
      const a=r.pose.yaw_deg*rad-m.origin[2];
      g.lineWidth=3;g.strokeStyle=color;g.beginPath();g.moveTo(x,y);g.lineTo(x+16*Math.cos(a),y-16*Math.sin(a));g.stroke();
      dot(x,y,color);
    } else {dot(x,y,"#5f706b");dot(x,y,"#fff",3.5);}
    g.font="600 12px system-ui";g.lineWidth=3;g.strokeStyle="#fff";g.strokeText(r.name,x+12,y-8);
    g.fillStyle="#1c322b";g.fillText(r.name,x+12,y-8);
  });
  const perPixel=m.resolution/scale, value=10**Math.floor(Math.log10(85*perPixel));
  if (Number.isFinite(value) && value>0) {
    const span=value/perPixel,y=vh-21;
    g.fillStyle="#f8fbf9";g.fillRect(9,y-20,Math.max(span+20,64),33);
    g.strokeStyle="#263f37";g.lineWidth=2;g.beginPath();g.moveTo(16,y);g.lineTo(16+span,y);g.stroke();
    g.fillStyle="#263f37";g.font="12px system-ui";g.fillText(value+" m",16,y-5);
  }
}
function select(id) {selected=id;lastList="";render();}
function render() {
  if (!data) return;
  const {robots,map,jobs,demo}=data;
  $("scope").textContent=demo?"离线演示 · 模拟机器人":"实机模式";
  $("scope").className=demo?"demo":"";
  $("counts").textContent=connected?`${robots.filter(r=>r.online).length} / ${robots.length} 在线`:"控制台连接中断";
  $("mapName").textContent=map.title||map.map_id;
  $("mapInfo").textContent=`${map.frame} · ${map.resolution} m/px`;
  const listKey=JSON.stringify(robots.map(r=>[r.id,r.state,r.address,r.expected_ip,r.name,connected,selected]));
  if (listKey!==lastList) {
    lastList=listKey;
    $("robots").replaceChildren(...robots.map(r=>{
      const b=element("button","","robot"), s=element("span",connected?info[r.state]:"未知","state");
      b.type="button";b.setAttribute("aria-pressed",r.id===selected);b.onclick=()=>select(r.id);
      s.dataset.state=r.state;
      b.append(element("b",r.name),s,element("span",r.address||r.expected_ip||"未连接","addr"),element("span",r.id,"addr"));
      return b;
    }));
  }
  const r=robots.find(r=>r.id===selected);
  $("selection").textContent=r?.name||"未选中";
  const d=r?.data||{};
  $("detail").textContent=r?`位姿 ${r.pose?`${r.pose.x.toFixed(2)}, ${r.pose.y.toFixed(2)} m · ${r.pose.yaw_deg.toFixed(0)}°`:"—"}　IMU ${d.imu_fresh?"有数据":"无数据"}　可用内存 ${d.memory_available_mib??"—"} MiB`:"";
  document.querySelectorAll("[data-cmd]").forEach(b=>{
    const cmd=b.dataset.cmd, matches=r?.state!=="map_mismatch";
    b.disabled=!(connected&&r?.online&&r.commands.includes(cmd)&&
      (cmd!=="seed_pose"||matches)&&(cmd!=="preview_path"||r.state==="localized"));
  });
  $("jobs").replaceChildren(...jobs.map(j=>{
    const li=element("li",`${new Date(j.time*1000).toLocaleTimeString()}　${j.robot} · ${commands[j.command]} · ${states[j.state]}`);
    li.append(element("small",j.message)); return li;
  }));
  draw();
}
function notice(s,error=false) {$("notice").textContent=s;$("notice").className=error?"error":"";}
async function command(cmd) {
  let args={};
  if (cmd==="seed_pose"||cmd==="preview_path") {
    if (["x","y","yaw"].some(id=>$(id).value===""||!$(id).checkValidity())) {
      notice("请先在图上取坐标，或填写有效的 X、Y 和朝向。",true);return;
    }
    args={x:+$("x").value,y:+$("y").value,yaw_deg:+$("yaw").value};
    if (cmd==="seed_pose"&&!data.demo&&!confirm("设置初始位姿会重启该机器人的定位。请确认机器人已停稳。")) return;
  }
  try {
    const response=await fetch("/api/commands",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({robot:selected,command:cmd,args,map_id:data.map.map_id}),signal:AbortSignal.timeout(4000)});
    const value=await response.json();
    if (!response.ok) throw Error(value.error);
    notice(`已提交${commands[cmd]}，请看回执。`);
  } catch(e) {notice(e.message,true);}
}
document.querySelectorAll("[data-cmd]").forEach(b=>b.onclick=()=>command(b.dataset.cmd));
$("zoomIn").onclick=()=>zoom(1.25);$("zoomOut").onclick=()=>zoom(.8);$("fit").onclick=fit;
new ResizeObserver(()=>{
  const w=c.clientWidth,h=c.clientHeight,dpr=devicePixelRatio||1;
  if (!w||!h) return;
  dx+=(w-vw)/2;dy+=(h-vh)/2;vw=w;vh=h;c.width=w*dpr;c.height=h*dpr;
  g.setTransform(dpr,0,0,dpr,0,0);draw();
}).observe(c);
function position(e) {const r=c.getBoundingClientRect();return [e.clientX-r.left,e.clientY-r.top];}
c.addEventListener("wheel",e=>{if(!data)return;e.preventDefault();zoom(e.deltaY<0?1.18:.85,...position(e));},{passive:false});
c.onpointerdown=e=>{if(!data||e.button!==0)return;c.setPointerCapture(e.pointerId);drag={p:position(e),dx,dy};};
c.onpointermove=e=>{
  if(!data)return;
  const [x,y]=position(e);
  if(drag&&Math.hypot(x-drag.p[0],y-drag.p[1])>3){drag.moved=true;dx=drag.dx+x-drag.p[0];dy=drag.dy+y-drag.p[1];draw();}
  const [a,b]=world((x-dx)/scale,(y-dy)/scale);
  $("cursor").textContent=`X ${a.toFixed(2)} m　Y ${b.toFixed(2)} m`;
};
c.onpointerup=e=>{
  if(!drag)return;
  if(!drag.moved){
    const [x,y]=position(e);
    const hit=data.robots.find(r=>r.pose&&Math.hypot(...screen([r.pose.x,r.pose.y]).map((n,i)=>n-[x,y][i]))<10);
    if(hit)select(hit.id);
    else {
      const u=(x-dx)/scale,v=(y-dy)/scale;
      if(u>=0&&u<data.map.width&&v>0&&v<=data.map.height) {
        target=world(u,v);$("x").value=target[0].toFixed(2);$("y").value=target[1].toFixed(2);draw();
      }
    }
  }
  drag=null;
};
c.onpointercancel=()=>{drag=null;};
async function refresh() {
  try {
    const response=await fetch("/api/state",{signal:AbortSignal.timeout(3000)});
    if(!response.ok)throw Error("unavailable");
    const next=await response.json(), fresh=next.map.map_id!==data?.map.map_id;
    connected=true;data=next;
    if(!selected&&data.robots.length)selected=data.robots[0].id;
    if(fresh){img=new Image();img.onload=fit;img.src="/map.png";}
    render();
  } catch(e) {
    connected=false;render();$("counts").textContent="控制台连接中断";
  } finally {setTimeout(refresh,850);}
}
refresh();
