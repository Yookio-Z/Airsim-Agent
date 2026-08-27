/* // 地图与航点：maplibre 初始化、车辆标记/航迹、航点编辑与飞控调用、地图绘制 */

const waypointPanelToggle = document.getElementById("waypointPanelToggle");
const waypointPanel = document.getElementById("waypointPanel");
if (waypointPanelToggle && waypointPanel) {
  waypointPanelToggle.addEventListener("click", () => {
    const collapsed = waypointPanel.dataset.collapsed !== "true";
    waypointPanel.dataset.collapsed = String(collapsed);
    waypointPanelToggle.textContent = collapsed ? "⌄" : "⌃";
  });
}

// ---- 多机航线规划目标机：由左上角无人机 chips 点击切换（无下拉框）----
// 每架机一条航线一种颜色，一键派发后各机执行各自航线。

const VEHICLE_ROUTE_PALETTE = ["#55dff4", "#f0b84a", "#64e1ae", "#ff5b6e", "#b18cff", "#ffd166"];

// 每架机的航线颜色（按遥测车辆顺序稳定分配）
function vehicleRouteColor(name) {
  const key = String(name || "");
  const vehicles = Array.isArray(latestState?.tool_runtime?.vehicles) ? latestState.tool_runtime.vehicles : [];
  const idx = vehicles.findIndex((v) => String(v.vehicle_name || "") === key);
  if (idx >= 0) return VEHICLE_ROUTE_PALETTE[idx % VEHICLE_ROUTE_PALETTE.length];
  let hash = 0;
  for (const ch of key) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return VEHICLE_ROUTE_PALETTE[hash % VEHICLE_ROUTE_PALETTE.length];
}

// 当前规划目标机名（"" = 未选择/单机默认）
function currentMissionVehicleName() {
  return String(missionTargetVehicle || "");
}

// 是否多机规划模式（后端报了多架车）
function isMultiVehiclePlanning() {
  const vehicles = Array.isArray(latestState?.tool_runtime?.vehicles) ? latestState.tool_runtime.vehicles : [];
  return vehicles.length > 1;
}

// 面板标题旁的当前目标机徽标
function updateMissionTargetBadge() {
  const badge = document.getElementById("missionTargetBadge");
  if (!badge) return;
  const target = currentMissionVehicleName();
  badge.hidden = !target;
  if (!target) return;
  const dot = badge.querySelector(".dot");
  const name = badge.querySelector(".name");
  const color = vehicleRouteColor(target);
  if (dot) dot.style.background = color;
  if (dot) dot.style.boxShadow = `0 0 6px ${color}`;
  if (name) name.textContent = target;
  badge.style.borderColor = `${color}66`;
}

// 点击 chips 切换目标机：暂存当前航线 → 载入目标机航线
function switchMissionTarget(name) {
  const target = String(name || "");
  if (target === missionTargetVehicle) {
    panMapToMissionVehicle();
    return;
  }
  if (missionTargetVehicle) {
    if (missionWaypoints.length) missionPlans[missionTargetVehicle] = missionWaypoints;
    else delete missionPlans[missionTargetVehicle];
  }
  missionTargetVehicle = target;
  missionWaypoints = (missionPlans[target] || []).slice();
  delete missionPlans[target];
  selectedWaypointIndex = -1;
  markMissionEdited();
  hideWaypointProperties();
  renderWaypoints();
  drawMissionPath();
  updateMissionTargetBadge();
  highlightMissionVehicleMarkers();
  panMapToMissionVehicle();
}

// 地图 marker 高亮当前规划目标机
function highlightMissionVehicleMarkers() {
  const target = currentMissionVehicleName();
  for (const [name, entry] of vehicleMarkers.entries()) {
    const el = entry.marker?.getElement();
    if (el) el.classList.toggle("selected", Boolean(target) && name === target);
  }
}

// ---- 目标机选择（单选）：点芯片选中一台，再点一下取消 ----
// 未选中 = 左侧小工具作用于全部无人机；选中 = 只作用于该机。
// 选中的机同时就是航线规划目标。
let controlSelectionVehicle = "";

function controlTargetList() {
  if (controlSelectionVehicle) return [controlSelectionVehicle];
  const vehicles = Array.isArray(latestState?.tool_runtime?.vehicles) ? latestState.tool_runtime.vehicles : [];
  return vehicles.map((v) => String(v.vehicle_name || "")).filter(Boolean);
}

function controlTargetLabel() {
  return controlSelectionVehicle || "全部无人机";
}

function toggleControlSelection(name) {
  const next = controlSelectionVehicle === name ? "" : name;
  controlSelectionVehicle = next;
  if (missionTargetVehicle !== next) switchMissionTarget(next);
  updateChipStates();
}

// 只更新芯片选中态，不重建 DOM（避免上方状态栏闪烁）
function updateChipStates() {
  document.querySelectorAll("#vehicleList .hud-vehicle-chip[data-vehicle]").forEach((chip) => {
    chip.classList.toggle("selected", chip.dataset.vehicle === controlSelectionVehicle);
  });
}

// 切换目标机后把地图平移到该机位置
function panMapToMissionVehicle() {
  if (!maplibreMap) return;
  const runtime = latestState?.tool_runtime || {};
  const vehicles = Array.isArray(runtime.vehicles) ? runtime.vehicles : [];
  if (vehicles.length <= 1) return;
  const targetName = currentMissionVehicleName();
  const target = vehicles.find((v) => String(v.vehicle_name || "") === targetName);
  const pos = target ? vehicleMarkerPosition(target, runtime) : null;
  if (pos) maplibreMap.panTo(pos, { animate: true });
}

els.canvas = document.querySelector("#missionMap");
// DEBUG: 排查地图黑屏 — 捕获 canvas 真实状态
// 遮罩层显隐会改变页面的合成层; 在切换完成后强制一次地图重算 + 重绘作为双保险.
function refreshMapAfterLayoutChange() {
  if (!maplibreMap) return;
  const repaint = () => {
    try { maplibreMap.resize(); } catch (e) {}
    try { maplibreMap.triggerRepaint(); } catch (e) {}
  };
  requestAnimationFrame(() => {
    repaint();
    requestAnimationFrame(() => {
      repaint();
      setTimeout(repaint, 160);
    });
  });
}

initMissionMap();

function initMissionMap() {
  if (!window.maplibregl || !els.missionMap) {
    console.warn("MapLibre GL 或地图容器不可用，地图功能将不可用");
    return;
  }
  const cfg = MAP_LAYERS[currentLayerKey] || MAP_LAYERS.satellite;
  maplibreMap = new maplibregl.Map({
    container: els.missionMap,
    style: {
      version: 8,
      sources: {
        tiles: {
          type: "raster",
          tiles: [cfg.url],
          tileSize: 256,
          maxzoom: cfg.maxZoom,
        },
        "wp-source": { type: "geojson", data: { type: "FeatureCollection", features: [] } },
        "path-source": { type: "geojson", data: { type: "FeatureCollection", features: [] } },
        "vehicle-track-source": { type: "geojson", data: { type: "FeatureCollection", features: [] } },
        "active-leg-source": { type: "geojson", data: { type: "FeatureCollection", features: [] } },
        "fence-source": { type: "geojson", data: { type: "FeatureCollection", features: [] } },
        "plan-source": { type: "geojson", data: { type: "FeatureCollection", features: [] } },
      },
      layers: [
        { id: "tiles", type: "raster", source: "tiles", minzoom: 0, maxzoom: cfg.maxZoom },
      ],
    },
    center: [AIRSIM_HOME_LON, AIRSIM_HOME_LAT],
    zoom: 15,
    attributionControl: false,
    dragRotate: false,
    // 关键: 保留绘制缓冲, 避免全屏遮罩层出现触发页面重新合成时 WebGL
    // 缓冲被清空导致卫星图黑屏 (display 切换 / 合成层变化都会触发清缓冲)
    preserveDrawingBuffer: true,
  });

  maplibreMap.on("load", () => {
    // 注册航点数字 sprite（失败不中断后续 layer/连线渲染）
    try {
      registerWaypointSprites(maplibreMap);
    } catch (e) {
      console.warn("registerWaypointSprites failed:", e);
    }

    // 航线连线
    maplibreMap.addLayer({
      id: "wp-path",
      type: "line",
      source: "path-source",
      layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": "#55dff4", "line-width": 2.5, "line-opacity": 0.85 },
    });

    maplibreMap.addLayer({
      id: "vehicle-track",
      type: "line",
      source: "vehicle-track-source",
      layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": "#ff4b5c", "line-width": 3, "line-opacity": 0.82 },
    });

    maplibreMap.addLayer({
      id: "active-leg",
      type: "line",
      source: "active-leg-source",
      layout: { "line-join": "round", "line-cap": "round" },
      paint: {
        "line-color": "#f0b84a",
        "line-width": 2,
        "line-opacity": 0.88,
        "line-dasharray": [2, 2],
      },
    });

    // 航点选中光环（circle layer 支持 feature-state；选中时半径放大形成动效）
    maplibreMap.addLayer({
      id: "wp-halo",
      type: "circle",
      source: "wp-source",
      paint: {
        "circle-radius": [
          "case",
          ["boolean", ["feature-state", "selected"], false],
          18,
          10,
        ],
        "circle-color": [
          "case",
          ["boolean", ["feature-state", "selected"], false],
          "#f0b84a",
          "#55dff4",
        ],
        "circle-opacity": [
          "case",
          ["boolean", ["feature-state", "selected"], false],
          0.35,
          0.18,
        ],
        "circle-stroke-width": [
          "case",
          ["boolean", ["feature-state", "selected"], false],
          2.5,
          1.5,
        ],
        "circle-stroke-color": [
          "case",
          ["boolean", ["feature-state", "selected"], false],
          "#f0b84a",
          "#55dff4",
        ],
        "circle-stroke-opacity": [
          "case",
          ["boolean", ["feature-state", "selected"], false],
          1.0,
          0.7,
        ],
      },
    });

    // 航点序号图标（symbol 引用 sprite；icon-image 表达式不能依赖 feature-state）
    maplibreMap.addLayer({
      id: "wp-icon",
      type: "symbol",
      source: "wp-source",
      layout: {
        "icon-image": ["concat", "wp-", ["to-string", ["get", "seq"]], "-", ["get", "type"]],
        "icon-allow-overlap": true,
        "icon-anchor": "center",
        "icon-size": 1.0,
      },
    });

    // 地理围栏图层（半透明红色填充 + 虚线边框）
    maplibreMap.addLayer({
      id: "fence-fill",
      type: "fill",
      source: "fence-source",
      paint: {
        "fill-color": "#ff5b6e",
        "fill-opacity": 0.15,
      },
    });
    maplibreMap.addLayer({
      id: "fence-line",
      type: "line",
      source: "fence-source",
      paint: {
        "line-color": "#ff5b6e",
        "line-width": 2,
        "line-dasharray": [4, 3],
        "line-opacity": 0.85,
      },
    });

    // 多机规划航线层：每机一条彩色航线（当前目标机高亮，其余半透明）
    // 插在 wp-halo 之下：航线线在航点图标下面
    maplibreMap.addLayer({
      id: "plan-lines",
      type: "line",
      source: "plan-source",
      filter: ["==", ["get", "kind"], "line"],
      paint: {
        "line-color": ["get", "color"],
        "line-width": ["case", ["==", ["get", "active"], true], 2.5, 2],
        "line-opacity": ["case", ["==", ["get", "active"], true], 0.95, 0.45],
      },
    }, "wp-halo");
    maplibreMap.addLayer({
      id: "plan-dots",
      type: "circle",
      source: "plan-source",
      filter: ["==", ["get", "kind"], "dot"],
      paint: {
        "circle-color": ["get", "color"],
        "circle-radius": ["case", ["==", ["get", "active"], true], 4.5, 3.5],
        "circle-opacity": ["case", ["==", ["get", "active"], true], 0.95, 0.55],
        "circle-stroke-width": 1,
        "circle-stroke-color": "rgba(6, 14, 22, 0.8)",
      },
    }, "wp-halo");

    // Home marker（动态目标用 marker OK，固定不动不闪）
    const homeEl = document.createElement("div");
    homeEl.className = "wp-home-icon";
    homeEl.innerHTML = '<div class="wp-home-badge">H</div>';
    homeMarker = new maplibregl.Marker({ element: homeEl, anchor: "center" })
      .setLngLat([AIRSIM_HOME_LON, AIRSIM_HOME_LAT])
      .addTo(maplibreMap);

    drawMissionPath();
    if (latestState) updateMapView(latestState);
  });

  // 地图缩放/移动时刷新剖面图比例尺（比例尺反映当前 zoom 下的地面距离）
  maplibreMap.on("zoom", () => drawMissionProfile());

  // 点击航点：queryRenderedFeatures 命中则选中；围栏模式下添加围栏顶点
  maplibreMap.on("click", (e) => {
    if (fenceDrawingMode) {
      missionFence.push({ lat: round6(e.lngLat.lat), lon: round6(e.lngLat.lng) });
      drawFence();
      showNotice(`围栏顶点 ${missionFence.length}`, "success");
      return;
    }
    const feats = maplibreMap.queryRenderedFeatures(e.point, { layers: ["wp-icon", "wp-halo"] });
    if (feats.length) {
      const seq = Number(feats[0].properties.seq);
      selectedWaypointIndex = seq - 1;
      renderWaypoints();
      drawMissionPath();
      showWaypointProperties(selectedWaypointIndex);
      return;
    }
    addWaypointFromMap({ lat: e.lngLat.lat, lng: e.lngLat.lng });
  });

  // 双击航点删除（参考 QGC：双击航点删除该项）
  maplibreMap.on("dblclick", (e) => {
    const feats = maplibreMap.queryRenderedFeatures(e.point, { layers: ["wp-icon", "wp-halo"] });
    if (feats.length) {
      e.preventDefault();
      const seq = Number(feats[0].properties.seq);
      const idx = seq - 1;
      missionWaypoints.splice(idx, 1);
      markMissionEdited();
      selectedWaypointIndex = -1;
      renderWaypoints();
      drawMissionPath();
    }
  });

  // 拖拽航点（wp-icon / wp-halo 共用同一处理器）
  function onWaypointMouseDown(e) {
    if (!e.features || !e.features.length) return;
    e.preventDefault();
    const seq = Number(e.features[0].properties.seq);
    const idx = seq - 1;
    wpDragging = true;
    selectedWaypointIndex = idx;
    const onMove = (me) => {
      const wp = missionWaypoints[idx];
      if (!wp) return;
      wp.lat = round6(me.lngLat.lat);
      wp.lon = round6(me.lngLat.lng);
      if (!isPx4MavlinkBackend()) {
        const ned = gpsToNed(wp.lat, wp.lon, -wp.alt_m);
        wp.x = round1(ned.x);
        wp.y = round1(ned.y);
        wp.z = round1(ned.z);
      }
      drawMissionPath();
    };
    const onUp = () => {
      wpDragging = false;
      maplibreMap.off("mousemove", onMove);
      maplibreMap.off("mouseup", onUp);
      markMissionEdited();
      renderWaypoints();
      drawMissionPath();
    };
    maplibreMap.on("mousemove", onMove);
    maplibreMap.on("mouseup", onUp);
  }
  maplibreMap.on("mousedown", "wp-icon", onWaypointMouseDown);
  maplibreMap.on("mousedown", "wp-halo", onWaypointMouseDown);

  // 鼠标悬停航点
  function setGrabCursor() {
    maplibreMap.getCanvas().style.cursor = "grab";
  }
  function clearGrabCursor() {
    maplibreMap.getCanvas().style.cursor = "";
  }
  maplibreMap.on("mouseenter", "wp-icon", setGrabCursor);
  maplibreMap.on("mouseleave", "wp-icon", clearGrabCursor);
  maplibreMap.on("mouseenter", "wp-halo", setGrabCursor);
  maplibreMap.on("mouseleave", "wp-halo", clearGrabCursor);

  // 用户拖拽地图后不再自动跟随无人机
  maplibreMap.on("dragstart", () => {
    maplibreMap._userPanned = true;
  });
}

// 切换底图图层（切换 raster source 的 tiles 并清缓存重绘）
function applyMapLayer(key) {
  const cfg = MAP_LAYERS[key];
  if (!cfg || !maplibreMap) return;
  const source = maplibreMap.getSource("tiles");
  if (source) {
    source.tiles = [cfg.url];
    try {
      maplibreMap.style.sourceCaches.tiles.clearTiles();
    } catch (err) {
      // 忽略 sourceCaches 未就绪
    }
    maplibreMap.triggerRepaint();
  }
  currentLayerKey = key;
}

// 注册航点序号 sprite 1-50（canvas 2D 绘制圆+数字，ImageData 同步注册，无 CORS/字体依赖）
// 分帧生成：先立即生成前 20 个，避免阻塞首帧/首次点击。
function registerWaypointSprites(map, immediateLimit = 20, total = 50) {
  const TYPE_COLORS = {
    waypoint: "#55dff4",
    takeoff: "#4ee6a4",
    land: "#f0b84a",
    rtl: "#ff5b6e",
  };
  const SIZE = 32;
  const draw = (n, color, selected) => {
    const canvas = document.createElement("canvas");
    canvas.width = SIZE;
    canvas.height = SIZE;
    const ctx = canvas.getContext("2d");
    const cx = SIZE / 2;
    const cy = SIZE / 2;
    const r = selected ? 15 : 14;
    ctx.clearRect(0, 0, SIZE, SIZE);

    // 外发光（type 颜色）
    ctx.beginPath();
    ctx.arc(cx, cy, r + 2, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.globalAlpha = 0.18;
    ctx.fill();
    ctx.globalAlpha = 1;

    // 实心深色圆底
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fillStyle = "#0b1219";
    ctx.fill();

    // 类型颜色描边，选中时加粗并变 amber
    ctx.lineWidth = selected ? 3 : 2;
    ctx.strokeStyle = selected ? "#f0b84a" : color;
    ctx.stroke();

    // 白色粗体数字，高对比度
    ctx.fillStyle = "#ffffff";
    ctx.font = "900 13px system-ui, -apple-system, 'Segoe UI', sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.shadowColor = "rgba(0,0,0,0.8)";
    ctx.shadowBlur = 2;
    ctx.fillText(String(n), cx, cy + 0.5);
    ctx.shadowBlur = 0;
    return ctx.getImageData(0, 0, SIZE, SIZE);
  };

  const addFor = (n) => {
    ["waypoint", "takeoff", "land", "rtl"].forEach((type) => {
      const color = TYPE_COLORS[type];
      const idNormal = `wp-${n}-${type}`;
      const idSel = `wp-${n}-${type}-sel`;
      try {
        if (!map.hasImage(idNormal)) map.addImage(idNormal, draw(n, color, false), { pixelRatio: 2 });
      } catch (e) {
        console.warn("addImage failed:", idNormal, e);
      }
      try {
        if (!map.hasImage(idSel)) map.addImage(idSel, draw(n, color, true), { pixelRatio: 2 });
      } catch (e) {
        console.warn("addImage failed:", idSel, e);
      }
    });
  };

  // 立即生成常用序号，保证前几个航点不卡顿
  for (let n = 1; n <= immediateLimit; n++) addFor(n);

  // 剩余序号在空闲时分批生成，避免阻塞主线程
  let current = immediateLimit + 1;
  function batch() {
    const end = Math.min(current + 5, total);
    for (let n = current; n <= end; n++) addFor(n);
    current = end + 1;
    if (current <= total) requestAnimationFrame(batch);
  }
  requestAnimationFrame(batch);
}

// 无人机 DOM 元素（青色圆形 + 朝向三角，整体随航向旋转）
function createDroneElement() {
  const el = document.createElement("div");
  el.className = "wp-drone-icon";
  el.title = "当前无人机位置";
  el.innerHTML = `
    <svg class="wp-drone-svg" viewBox="0 0 48 48" aria-hidden="true">
      <circle class="wp-drone-ring" cx="24" cy="24" r="17"></circle>
      <path class="wp-drone-body" d="M24 5 L36 39 L24 31 L12 39 Z"></path>
      <circle class="wp-drone-core" cx="24" cy="24" r="4"></circle>
    </svg>
  `;
  return el;
}

// 多机 marker：无人机图标 + 机名标签（多机模式使用）
function createVehicleElement(name) {
  const el = document.createElement("div");
  el.className = "wp-drone-icon wp-vehicle-marker";
  const label = String(name || "?");
  el.title = `载具 ${label}`;
  el.innerHTML = `
    <svg class="wp-drone-svg" viewBox="0 0 48 48" aria-hidden="true">
      <circle class="wp-drone-ring" cx="24" cy="24" r="17"></circle>
      <path class="wp-drone-body" d="M24 5 L36 39 L24 31 L12 39 Z"></path>
      <circle class="wp-drone-core" cx="24" cy="24" r="4"></circle>
    </svg>
    <span class="wp-vehicle-label">${label}</span>
  `;
  return el;
}

function vehicleMarkerPosition(vehicle, runtime) {
  const gps = droneGpsPosition(vehicle, runtime);
  if (!gps) return null;
  return [gps[1], gps[0]]; // [lng, lat]
}

// 多机标记防重叠（保形放大）：出生点/返航点间距只有几米，卫星图缩放下必然
// 叠成一个点。对彼此间距 ≤ 15m 的聚簇，围绕簇中心按同一比例放大显示，
// 保持真实相对布局与朝向（AirSim 里是一条线，地图上仍是一条线，只是拉宽到
// 直径 ~30m 便于肉眼分辨）。只影响显示坐标，真实位置数据/轨迹不变。
function deOverlapMarkers(positions) {
  // positions: [{ name, lngLat: [lng, lat] }]
  const result = new Map();
  if (positions.length < 2) {
    for (const p of positions) result.set(p.name, p.lngLat);
    return result;
  }
  const TARGET_DIAMETER_M = 30;
  const clusters = [];
  for (const p of positions) {
    let cluster = null;
    for (const cand of clusters) {
      if (cand.some((q) => haversineMeters(p.lngLat[1], p.lngLat[0], q.lngLat[1], q.lngLat[0]) <= 15)) {
        cluster = cand;
        break;
      }
    }
    if (!cluster) {
      cluster = [];
      clusters.push(cluster);
    }
    cluster.push(p);
  }
  for (const cluster of clusters) {
    if (cluster.length === 1) {
      result.set(cluster[0].name, cluster[0].lngLat);
      continue;
    }
    const cLat = cluster.reduce((s, p) => s + p.lngLat[1], 0) / cluster.length;
    const cLng = cluster.reduce((s, p) => s + p.lngLat[0], 0) / cluster.length;
    let maxDist = 0;
    for (let i = 0; i < cluster.length; i++) {
      for (let j = i + 1; j < cluster.length; j++) {
        const d = haversineMeters(
          cluster[i].lngLat[1], cluster[i].lngLat[0],
          cluster[j].lngLat[1], cluster[j].lngLat[0]
        );
        if (d > maxDist) maxDist = d;
      }
    }
    const scale = TARGET_DIAMETER_M / Math.max(1.0, maxDist);
    for (const p of cluster) {
      const dLat = (p.lngLat[1] - cLat) * scale;
      const dLon = (p.lngLat[0] - cLng) * scale;
      result.set(p.name, [cLng + dLon, cLat + dLat]);
    }
  }
  return result;
}

// 多机模式：每机一个 marker（即时更新，不做插值动画，保持简单可靠）
function updateVehicleMarkers(vehicles, runtime) {
  if (!maplibreMap || !Array.isArray(vehicles) || vehicles.length === 0) return;
  const liveNames = new Set();
  const positions = [];
  for (const vehicle of vehicles) {
    const name = String(vehicle.vehicle_name || "");
    if (!name) continue;
    liveNames.add(name);
    const lngLat = vehicleMarkerPosition(vehicle, runtime);
    if (lngLat) positions.push({ name, lngLat });
  }
  const displayLngLats = deOverlapMarkers(positions);
  for (const vehicle of vehicles) {
    const name = String(vehicle.vehicle_name || "");
    if (!liveNames.has(name)) continue;
    const lngLat = vehicleMarkerPosition(vehicle, runtime);
    const heading = normalizeHeadingDeg(droneHeadingDeg(vehicle));
    let entry = vehicleMarkers.get(name);
    if (!entry) {
      // 无有效位置先不创建 marker（避免叠在 [0,0]），等遥测到位再上屏
      if (!lngLat) continue;
      const marker = new maplibregl.Marker({
        element: createVehicleElement(name),
        anchor: "center",
        rotationAlignment: "map",
      })
        .setLngLat(displayLngLats.get(name) || lngLat)
        .setRotation(heading)
        .addTo(maplibreMap);
      entry = { marker, lngLat, heading };
      vehicleMarkers.set(name, entry);
    }
    if (lngLat) {
      entry.marker.setLngLat(displayLngLats.get(name) || lngLat).setRotation(heading);
      entry.lngLat = lngLat;
      entry.heading = heading;
    }
    // 每机轨迹（真实坐标，不随防重叠外扩漂移）
    if (applicationSettings.map.show_vehicle_track && lngLat) {
      updateVehicleTrack(lngLat, vehicle, true, name);
    }
  }
  // 清理已消失的载具
  for (const [name, entry] of vehicleMarkers.entries()) {
    if (!liveNames.has(name)) {
      entry.marker.remove();
      vehicleMarkers.delete(name);
      vehicleTracks.delete(name);
    }
  }
  highlightMissionVehicleMarkers();
  updateVehicleHomeMarkers(vehicles, runtime);
}

// 每机返航点标记：位置 = 该机初始位置（home_position_ned，由后端首次地面记录）
function createVehicleHomeElement(name, color) {
  const el = document.createElement("div");
  el.className = "wp-vehicle-home";
  el.style.setProperty("--home-color", color);
  el.textContent = "H";
  el.title = `${name} 的返航点（初始位置）`;
  return el;
}

// 各机 H 点的地图经纬度：后端两种格式都兼容——
// AirSim 用 home_position_ned（NED，本地换算），PX4/真机用
// home_position（MAVLink HOME_POSITION 的 lat/lon）。
function vehicleHomeLngLat(vehicle) {
  const hn = vehicle?.home_position_ned;
  if (hn && Number.isFinite(Number(hn.x)) && Number.isFinite(Number(hn.y))) {
    const g = nedToGps(Number(hn.x), Number(hn.y), Number(hn.z || 0));
    return [g.lon, g.lat];
  }
  const hp = vehicle?.home_position || {};
  if (Number.isFinite(Number(hp.lat)) && Number.isFinite(Number(hp.lon))) {
    return [Number(hp.lon), Number(hp.lat)];
  }
  return null;
}

function updateVehicleHomeMarkers(vehicles, runtime) {
  if (!maplibreMap || !Array.isArray(vehicles)) return;
  const liveNames = new Set();
  // 各机 H 点（精确位置）与该机当前真实位置：无人机在自家 H 上时 H 被
  // 无人机图标占据（不显示）——初始/返航落地时二者天然重合，不需分离；
  // 无人机飞走后 H 原地显示，表示返航点。
  for (const vehicle of vehicles) {
    const name = String(vehicle.vehicle_name || "");
    if (!name) continue;
    const homeLngLat = vehicleHomeLngLat(vehicle);
    if (!homeLngLat) continue;
    liveNames.add(name);
    const droneLngLat = vehicleMarkerPosition(vehicle, runtime);
    const occupied = Boolean(droneLngLat) && haversineMeters(
      homeLngLat[1], homeLngLat[0], droneLngLat[1], droneLngLat[0]
    ) < 15;
    if (occupied) {
      const marker = vehicleHomeMarkers.get(name);
      if (marker) {
        marker.remove();
        vehicleHomeMarkers.delete(name);
      }
      continue;
    }
    let marker = vehicleHomeMarkers.get(name);
    if (!marker) {
      marker = new maplibregl.Marker({ element: createVehicleHomeElement(name, vehicleRouteColor(name)), anchor: "center" })
        .setLngLat(homeLngLat)
        .addTo(maplibreMap);
      vehicleHomeMarkers.set(name, marker);
    } else {
      marker.setLngLat(homeLngLat);
    }
  }
  for (const [name, marker] of vehicleHomeMarkers.entries()) {
    if (!liveNames.has(name)) {
      marker.remove();
      vehicleHomeMarkers.delete(name);
    }
  }
}

function updateDroneMarker(lngLat, heading, options = {}) {
  if (!Array.isArray(lngLat) || !Number.isFinite(Number(lngLat[0])) || !Number.isFinite(Number(lngLat[1]))) return;
  const normalizedHeading = normalizeHeadingDeg(heading);
  if (!droneMarker) {
    droneMarker = new maplibregl.Marker({
      element: createDroneElement(),
      anchor: "center",
      rotationAlignment: "map",
    })
      .setLngLat(lngLat)
      .setRotation(normalizedHeading)
      .addTo(maplibreMap);
    droneRenderedLngLat = lngLat.slice();
    droneRenderedHeading = normalizedHeading;
    if (SHOW_ACTIVE_LEG) updateActiveLeg(droneRenderedLngLat);
    return;
  }

  if (options.immediate) {
    stopDroneAnimation();
    droneMarker.setLngLat(lngLat);
    droneMarker.setRotation(normalizedHeading);
    droneRenderedLngLat = lngLat.slice();
    droneRenderedHeading = normalizedHeading;
    if (SHOW_ACTIVE_LEG) updateActiveLeg(droneRenderedLngLat);
    return;
  }

  const from = currentDroneAnimationPosition();
  const distance = from ? haversineMeters(from[1], from[0], lngLat[1], lngLat[0]) : Infinity;
  if (!from || distance > DRONE_MARKER_SNAP_DISTANCE_M) {
    stopDroneAnimation();
    droneMarker.setLngLat(lngLat);
    droneMarker.setRotation(normalizedHeading);
    droneRenderedLngLat = lngLat.slice();
    droneRenderedHeading = normalizedHeading;
    if (SHOW_ACTIVE_LEG) updateActiveLeg(droneRenderedLngLat);
    return;
  }

  stopDroneAnimation();
  droneAnimationFrom = from.slice();
  droneAnimationTo = lngLat.slice();
  droneAnimationHeadingFrom = Number.isFinite(droneRenderedHeading) ? droneRenderedHeading : normalizedHeading;
  droneAnimationHeadingTo = normalizedHeading;
  droneAnimationStartedAt = performance.now();
  const duration = Math.max(80, Math.min(DRONE_MARKER_ANIMATION_MS, Math.max(100, distance * 35)));
  droneAnimationDurationMs = duration;

  const step = (now) => {
    const t = clamp((now - droneAnimationStartedAt) / duration, 0, 1);
    const eased = easeInOutCubic(t);
    const pos = interpolateLngLat(droneAnimationFrom, droneAnimationTo, eased);
    const rot = interpolateHeadingDeg(droneAnimationHeadingFrom, droneAnimationHeadingTo, eased);
    droneMarker.setLngLat(pos);
    droneMarker.setRotation(rot);
    droneRenderedLngLat = pos;
    droneRenderedHeading = rot;
    if (SHOW_ACTIVE_LEG) updateActiveLeg(pos);
    if (t < 1) {
      droneAnimationFrame = requestAnimationFrame(step);
    } else {
      droneAnimationFrame = null;
      droneAnimationFrom = null;
      droneAnimationTo = null;
      droneRenderedLngLat = lngLat.slice();
      droneRenderedHeading = normalizedHeading;
    }
  };
  droneAnimationFrame = requestAnimationFrame(step);
}

function stopDroneAnimation() {
  if (droneAnimationFrame) {
    cancelAnimationFrame(droneAnimationFrame);
    droneAnimationFrame = null;
  }
}

function currentDroneAnimationPosition() {
  if (!droneAnimationFrame || !droneAnimationFrom || !droneAnimationTo) {
    return droneRenderedLngLat ? droneRenderedLngLat.slice() : null;
  }
  const elapsed = performance.now() - droneAnimationStartedAt;
  const t = clamp(elapsed / Math.max(1, droneAnimationDurationMs), 0, 1);
  return interpolateLngLat(droneAnimationFrom, droneAnimationTo, easeInOutCubic(t));
}

function resolveDroneHeading(drone, lngLat) {
  const telemetryHeading = normalizeHeadingDeg(droneHeadingDeg(drone));
  if (droneLastTelemetryLngLat) {
    const movementM = haversineMeters(droneLastTelemetryLngLat[1], droneLastTelemetryLngLat[0], lngLat[1], lngLat[0]);
    if (movementM >= 0.45) {
      return calculateBearing(droneLastTelemetryLngLat[1], droneLastTelemetryLngLat[0], lngLat[1], lngLat[0]);
    }
  }
  return telemetryHeading;
}

function interpolateLngLat(from, to, t) {
  return [
    Number(from[0]) + (Number(to[0]) - Number(from[0])) * t,
    Number(from[1]) + (Number(to[1]) - Number(from[1])) * t,
  ];
}

function easeInOutCubic(t) {
  return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
}

function normalizeHeadingDeg(value) {
  const n = Number(value);
  return Number.isFinite(n) ? ((n % 360) + 360) % 360 : 0;
}

function interpolateHeadingDeg(from, to, t) {
  const start = normalizeHeadingDeg(from);
  const delta = (((normalizeHeadingDeg(to) - start) + 540) % 360) - 180;
  return normalizeHeadingDeg(start + delta * t);
}

function addWaypointFromMap(latlng) {
  const runtime = activeFlightRuntime();
  const contract = runtime.operation_contract || {};
  if (contract.vehicle_kind === "real_px4" && applicationSettings.safety.require_gps_for_global_mission && !contract.global_mission_ready) {
    showNotice("真实 PX4 的 GPS 位置尚不可靠，不能创建全局航点", "error");
    return;
  }
  if (isMultiVehiclePlanning() && !currentMissionVehicleName()) {
    showNotice("请先点击左上角的无人机，选择要规划航线的那一架", "error");
    return;
  }
  const index = missionWaypoints.length;
  const alt = currentDefaultAltitude();
  const item = {
    id: `wp_${String(index + 1).padStart(3, "0")}`,
    type: "waypoint",
    frame: isPx4MavlinkBackend() ? "global_relative_alt" : "local_ned",
    lat: round6(latlng.lat),
    lon: round6(latlng.lng),
    alt_m: alt,
    speed_mps: 2,
    hold_s: 0,
    acceptance_radius_m: 2,
    actions: [],
    metadata: { source: "ui_map_click" },
  };
  if (!isPx4MavlinkBackend()) {
    // AirSim 后端：同时维护 NED 坐标，便于 fly_path 兼容
    const ned = gpsToNed(item.lat, item.lon, -alt);
    item.x = round1(ned.x);
    item.y = round1(ned.y);
    item.z = round1(ned.z);
  }
  missionWaypoints.push(item);
  markMissionEdited();
  renderWaypoints();
  drawMissionPath();
}

function currentDefaultAltitude() {
  const drone = latestState?.tool_runtime?.drone || {};
  const z = Number(drone.position_ned?.z || 0);
  if (z < -0.8) return Math.abs(z);
  return 3;
}

function activeFlightRuntime() {
  return latestState?.tool_runtime || {};
}

function requireLiveFlightLink() {
  const runtime = activeFlightRuntime();
  if (runtime.connected && !runtime.stale_connection) return runtime;
  const heartbeatAge = Number(runtime.drone?.heartbeat_age_s);
  const age = Number.isFinite(heartbeatAge) ? `，最后心跳 ${heartbeatAge.toFixed(1)}s 前` : "";
  throw new Error(`飞控链路离线或心跳过期${age}，请检查连接设置`);
}

function approvalCommandForTool(tool, params = {}) {
  if (tool === "drone_arm") return "解锁无人机";
  if (tool === "drone_takeoff") return `起飞到 ${Number(params.altitude || 3)} 米并悬停`;
  if (tool === "drone_land") return "降落无人机";
  return "";
}

async function invokeFlightTool(tool, params = {}) {
  const runtime = requireLiveFlightLink();
  const capabilities = runtime.backend_profile?.capabilities || {};
  const approvalCommand = approvalCommandForTool(tool, params);
  if (approvalCommand && capabilities.real_vehicle && capabilities.requires_operator_approval) {
    const approved = await confirmDialog({
      title: "确认真实飞控操作",
      message: `${approvalCommand}。当前通道：${runtime.operation_contract?.command_channel || runtime.backend}。`,
      confirmLabel: "确认执行",
      danger: true,
    });
    if (!approved) throw new Error("操作已取消");
  }
  return post("/api/tool", {
    tool,
    params,
    dry_run: false,
    expected_backend: runtime.backend,
  });
}

async function invokeFlightControl(action) {
  const normalized = String(action || "").toLowerCase();
  const targets = controlTargetList();
  requireLiveFlightLink();
  const result = await post("/api/control", {
    action: normalized,
    vehicles: targets,
    expected_backend: activeFlightRuntime().backend || "",
  });
  if ((normalized === "return_home" || normalized === "rtl") && result?.ok && targets.length) {
    activeReturnHomeVehicles = [...targets];
  }
  return result;
}

async function runButton(button, fn, successMessage) {
  button.disabled = true;
  try {
    const result = await fn();
    await refresh();
    showNotice(extractApiSuccess(result, successMessage), "success");
  } catch (error) {
    showNotice(error.message || "指令执行失败", "error");
  } finally {
    button.disabled = false;
    updateFlightControlButtons(activeFlightRuntime());
  }
}

function currentBackendId() {
  const runtime = latestState?.tool_runtime || {};
  return runtime.backend_profile?.id || runtime.backend || "";
}

function isPx4MavlinkBackend() {
  return currentBackendId() === "px4_mavlink";
}



function markMissionEdited() {
  missionExecutionActive = false;
  resetActiveTargetProgress();
  clearActiveLeg();
}

function markMissionExecutionStarted() {
  missionExecutionActive = true;
  resetActiveTargetProgress();
}

function resetActiveTargetProgress() {
  activeTargetRouteKey = "";
  activeTargetIndex = 0;
  lastMissionProgress = null;
}

function buildLocalMissionItems(route = missionWaypoints) {
  const items = [];
  const firstAltitude = Math.max(0.5, Number(route[0]?.alt_m || 3));
  const drone = latestState?.tool_runtime?.drone || {};
  const droneHome = currentDroneGeo(drone);
  const hasTakeoff = route.some((wp) => wp.type === "takeoff");
  if (!drone.flying && !hasTakeoff) {
    const takeoffLat = droneHome?.lat ?? route[0]?.lat ?? AIRSIM_HOME_LAT;
    const takeoffLon = droneHome?.lon ?? route[0]?.lon ?? AIRSIM_HOME_LON;
    items.push({
      id: "local_takeoff",
      type: "takeoff",
      frame: isPx4MavlinkBackend() ? "global_relative_alt" : "local_ned",
      lat: takeoffLat,
      lon: takeoffLon,
      x: 0,
      y: 0,
      z: -firstAltitude,
      alt_m: firstAltitude,
      speed_mps: Number(route[0]?.speed_mps || 2),
      hold_s: 0,
      acceptance_radius_m: 2,
      actions: [],
      metadata: { source: "ui_auto_takeoff" },
    });
  }

  route.forEach((wp, index) => {
    items.push({
      id: wp.id || `wp_${String(index + 1).padStart(3, "0")}`,
      type: wp.type || "waypoint",
      frame: wp.frame || (isPx4MavlinkBackend() ? "global_relative_alt" : "local_ned"),
      lat: wp.lat,
      lon: wp.lon,
      alt_m: Math.max(0.5, Number(wp.alt_m || 3)),
      x: wp.x,
      y: wp.y,
      z: wp.z,
      speed_mps: Number(wp.speed_mps || 2),
      hold_s: Number(wp.hold_s || 0),
      acceptance_radius_m: Number(wp.acceptance_radius_m || 2),
      actions: Array.isArray(wp.actions) ? wp.actions : [],
      metadata: wp.metadata || { source: "ui_waypoint_panel" },
    });
  });
  return items;
}





// 收集多机任务：当前航线 + 暂存的所有目标机航线
function collectMissionAssignments() {
  const assignments = [];
  const collect = (vehicle, route) => {
    if (!vehicle) return;
    const items = buildLocalMissionItems(Array.isArray(route) ? route : []);
    if (items.length) assignments.push({ vehicle, items });
  };
  collect(currentMissionVehicleName(), missionWaypoints);
  for (const [vehicle, route] of Object.entries(missionPlans)) {
    if (vehicle === currentMissionVehicleName()) continue;
    collect(vehicle, route);
  }
  return assignments;
}

async function deployAndStartMission() {
  const runtime = requireLiveFlightLink();
  // 多机模式：当前目标机已选择 → 一键派发所有机的航线（各机执行各自的）
  if (isMultiVehiclePlanning() && currentMissionVehicleName()) {
    const assignments = collectMissionAssignments();
    if (!assignments.length) throw new Error("没有可派发的航线，请先为目标机添加航点");
    const summary = assignments
      .map((a) => `${a.vehicle}: ${a.items.filter((it) => it.type !== "takeoff").length} 航点`)
      .join("；");
    const ok = await confirmDialog({
      title: "上传并开始多机航线",
      message: `将同时派发各机航线并立即执行：${summary}。每架飞机执行完自己的航线后悬停。是否继续？`,
      confirmLabel: "上传并开始",
      danger: true,
    });
    if (!ok) return null;
    const result = await post("/api/gcs/mission/start_multi", {
      assignments,
      expected_backend: runtime.backend,
    });
    extractApiSuccess(result, "多机航线已派发");
    markMissionExecutionStarted();
    activeFlightTaskVehicles = assignments.map((a) => a.vehicle);
    return result;
  }
  // 单机流程（原逻辑）
  const uploadResult = await uploadMissionToVehicle();
  extractApiSuccess(uploadResult, "任务已上传");
  markMissionExecutionStarted();
  try {
    const startResult = await startVehicleMission();
    extractApiSuccess(startResult, "任务启动指令已发送");
    return startResult;
  } catch (error) {
    markMissionEdited();
    throw error;
  }
}





function clearLocalMissionDraft() {
  missionWaypoints = [];
  missionPlans = {};
  missionFence = [];
  selectedWaypointIndex = -1;
  markMissionEdited();
  renderWaypoints();
  drawMissionPath();
  drawFence();
  updateMissionTargetBadge();
}

async function clearMissionEverywhere() {
  const result = await clearVehicleMission();
  extractApiSuccess(result, "飞控任务已清空");
  clearLocalMissionDraft();
  return result;
}



function buildMissionDraftFromItems(items) {
  // 构造 backend-neutral MissionPlanDraft，供 GCS /api/gcs/mission/upload 使用
  const home = (() => {
    const first = currentDroneGeo(latestState?.tool_runtime?.drone || {}) || items.find((it) => it.type === "takeoff") || items[0];
    if (first && first.lat != null && first.lon != null) {
      return { lat: Number(first.lat), lon: Number(first.lon), alt_m: Number(first.alt_m || 0) };
    }
    return null;
  })();
  return {
    name: "UI mission",
    vehicle: currentMissionVehicleName(),
    home,
    items: items.map((it) => ({
      id: it.id,
      type: it.type || "waypoint",
      frame: it.frame || "global_relative_alt",
      lat: it.lat,
      lon: it.lon,
      alt_m: Number(it.alt_m || 0),
      x: it.x,
      y: it.y,
      z: it.z,
      speed_mps: Number(it.speed_mps || 0),
      hold_s: Number(it.hold_s || 0),
      acceptance_radius_m: Number(it.acceptance_radius_m || 2),
      actions: Array.isArray(it.actions) ? it.actions : [],
      metadata: it.metadata || {},
    })),
  };
}

async function executeLocalPath() {
  await post("/api/tool", {
    tool: "drone_fly_path",
    params: {
      waypoints_json: JSON.stringify(
        missionWaypoints.map((wp) => ({
          x: wp.x ?? 0,
          y: wp.y ?? 0,
          z: wp.z ?? -Number(wp.alt_m || 3),
        }))
      ),
      velocity: Number(missionWaypoints[0]?.speed_mps || 2),
    },
    dry_run: false,
  });
}







