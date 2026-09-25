// ui-map.js —— 地图、航点、航线剖面与飞行器标记
// 由 app.js 拆分而来；各文件共享同一份脚本作用域，按顺序加载。

function cameraWindowParts(el) {
  return {
    handle: el.querySelector("[data-camera-role='handle']") || el.querySelector(".camera-viewer-drag-handle"),
    newBtn: el.querySelector("[data-camera-action='new']"),
    closeBtn: el.querySelector("[data-camera-action='close']"),
    cameraSelect: el.querySelector("[data-camera-role='camera']"),
    vehicleSelect: el.querySelector("[data-camera-role='vehicle']"),
    imageTypeSelect: el.querySelector("[data-camera-role='imageType']"),
    sourceSelect: el.querySelector("[data-camera-role='source']"),
    liveIndicator: el.querySelector("[data-camera-role='live']"),
    perfEl: el.querySelector("[data-camera-role='perf']"),
    cameraFieldEl: el.querySelector("[data-camera-role='cameraField']"),
    vehicleFieldEl: el.querySelector("[data-camera-role='vehicleField']"),
    imageTypeFieldEl: el.querySelector("[data-camera-role='imageTypeField']"),
    statusEl: el.querySelector("[data-camera-role='status']"),
    imageEl: el.querySelector("[data-camera-role='image']"),
    placeholderEl: el.querySelector("[data-camera-role='placeholder']"),
    metaEl: el.querySelector("[data-camera-role='meta']"),
    hudEl: el.querySelector("[data-camera-role='hud']"),
  };
}

function setCameraWindowPosition(win, left, top) {
  if (!win?.el) return;
  const stage = win.el.offsetParent;
  if (!stage) return;
  const stageRect = stage.getBoundingClientRect();
  const width = win.el.offsetWidth || 390;
  const height = win.el.offsetHeight || 260;
  const clampedLeft = Math.max(6, Math.min(left, stageRect.width - width - 6));
  const clampedTop = Math.max(58, Math.min(top, stageRect.height - height - 6));
  win.el.style.left = `${Math.round(clampedLeft)}px`;
  win.el.style.top = `${Math.round(clampedTop)}px`;
  win.el.style.right = "auto";
  win.el.style.bottom = "auto";
}

// 当前规划目标机名（"" = 未选择/单机默认）
function currentMissionVehicleName() {
  return String(missionTargetVehicle || "");
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

function controlTargetList() {
  if (controlSelectionVehicle) return [controlSelectionVehicle];
  const vehicles = Array.isArray(latestState?.tool_runtime?.vehicles) ? latestState.tool_runtime.vehicles : [];
  return vehicles.map((v) => String(v.vehicle_name || "")).filter(Boolean);
}

function toggleControlSelection(name) {
  const next = controlSelectionVehicle === name ? "" : name;
  controlSelectionVehicle = next;
  if (missionTargetVehicle !== next) switchMissionTarget(next);
  updateChipStates();
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
    center: [mapOriginLon(), mapOriginLat()],
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
      .setLngLat([mapOriginLon(), mapOriginLat()])
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
  const TYPE_COLORS = WAYPOINT_TYPE_COLORS;
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
  const defaults = missionDefaults();
  const alt = currentDefaultAltitude();
  const item = {
    id: `wp_${String(index + 1).padStart(3, "0")}`,
    type: "waypoint",
    frame: isPx4MavlinkBackend() ? "global_relative_alt" : "local_ned",
    lat: round6(latlng.lat),
    lon: round6(latlng.lng),
    alt_m: alt,
    speed_mps: defaults.speed,
    hold_s: defaults.hold,
    acceptance_radius_m: defaults.acceptance,
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

// 任务默认值（设置 → 任务默认值），对新建航点与自动起飞统一生效
function missionDefaults() {
  const m = applicationSettings?.mission || {};
  return {
    altitude: Math.max(0.5, Number(m.default_altitude_m) || 3),
    speed: Math.max(0.2, Number(m.default_speed_mps) || 2),
    hold: Math.max(0, Number(m.default_hold_s) || 0),
    acceptance: Math.max(0.5, Number(m.default_acceptance_radius_m) || 2),
  };
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
  const defaults = missionDefaults();
  const firstAltitude = Math.max(0.5, Number(route[0]?.alt_m || defaults.altitude));
  const drone = latestState?.tool_runtime?.drone || {};
  const droneHome = currentDroneGeo(drone);
  const hasTakeoff = route.some((wp) => wp.type === "takeoff");
  if (!drone.flying && !hasTakeoff) {
    const takeoffLat = droneHome?.lat ?? route[0]?.lat ?? mapOriginLat();
    const takeoffLon = droneHome?.lon ?? route[0]?.lon ?? mapOriginLon();
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
      speed_mps: Number(route[0]?.speed_mps || defaults.speed),
      hold_s: 0,
      acceptance_radius_m: defaults.acceptance,
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
      alt_m: Math.max(0.5, Number(wp.alt_m ?? defaults.altitude)),
      x: wp.x,
      y: wp.y,
      z: wp.z,
      speed_mps: Number(wp.speed_mps ?? defaults.speed),
      hold_s: Number(wp.hold_s ?? defaults.hold),
      acceptance_radius_m: Number(wp.acceptance_radius_m ?? defaults.acceptance),
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







function checkReturnHomeCompletion(toolRuntime = {}) {
  if (!activeReturnHomeVehicles.length) return;
  const vehicles = Array.isArray(toolRuntime.vehicles) ? toolRuntime.vehicles : [];
  if (!vehicles.length) return;
  const byName = new Map(vehicles.map((v) => [String(v.vehicle_name || ""), v]));
  const states = activeReturnHomeVehicles.map((n) => byName.get(n));
  if (states.some((s) => s === undefined)) return;
  if (!states.every((v) => !v.flying && !v.armed)) return;
  activeReturnHomeVehicles = [];
  showNotice("✅ 返航完成：已全部到达初始点并降落锁定", "success");
}

function render(state) {
  const run = state.current_run;
  const toolRuntime = state.tool_runtime || {};
  const drone = toolRuntime.drone || {};
  const supervisor = state.supervisor || {};
  const llm = state.llm || {};

  renderTopbar(run, toolRuntime, supervisor, llm);
  renderOperationContract(toolRuntime);
  renderContextUsage(state.memory || {});
  syncComposerDensity();
  renderTelemetry(drone, toolRuntime);
  renderPlan(run);
  renderTaskRuns(state.task_runs || state.memory?.task_runs || {});
  renderApprovalDialog(run, state.pending_approvals || []);
  renderEvents(state.events || []);
  renderTools(state.tools || [], toolRuntime.tool_cards || [], toolRuntime);
  renderMemory(state.memory || {});
  renderSkills();
  renderChat(state.messages || [], run, llm);
  renderWaypoints();
  updateMapView(state);
  renderSessions(state.sessions || [], state.current_session);
  renderCurrentSessionLabel(state.current_session);
  if (els.systemSettingsModal && !els.systemSettingsModal.hidden) updateVehicleSettingsAvailability();
  syncCommandSubmitState();
  syncHeader();
}

function renderOperationContract(toolRuntime) {
  const contract = toolRuntime?.operation_contract || {};
  const linked = Boolean(toolRuntime?.connected && !toolRuntime?.stale_connection);
  const commandChannel = String(contract.command_channel || "");
  const missionChannel = String(contract.mission_channel || "");
  if (els.operationChannel) {
    els.operationChannel.textContent = linked
      ? `${commandChannel} · 航点: ${missionChannel}`
      : `${commandChannel || "无控制通道"} · 未连接`;
    els.operationChannel.title = [
      `控制: ${commandChannel || "--"}`,
      `任务: ${missionChannel || "--"}`,
      `返航: ${contract.return_channel || "--"}`,
      `位置: ${contract.position_source || "--"}`,
    ].join("\n");
  }
  document.querySelectorAll("[data-waypoint-action='deploy_start']").forEach((button) => {
    const needsGlobalGps = contract.backend === "px4_mavlink"
      && Boolean(applicationSettings.safety.require_gps_for_global_mission);
    button.disabled = !linked || (needsGlobalGps && !contract.global_mission_ready);
    button.title = button.disabled && needsGlobalGps
      ? "真实 PX4 需要可靠 GPS 后才能上传并开始全局航点"
      : `通过 ${missionChannel || "当前后端"} 上传并开始航线`;
  });
}

function updateFlightControlButtons(toolRuntime = {}) {
  const linked = Boolean(toolRuntime.connected) && !toolRuntime.stale_connection;
  const heartbeatAge = Number(toolRuntime.drone?.heartbeat_age_s);
  const linkReason = Number.isFinite(heartbeatAge)
    ? `飞控心跳已过期（${heartbeatAge.toFixed(1)}s），请检查连接设置`
    : "飞控未连接，请检查连接设置";
  const contract = toolRuntime.operation_contract || {};
  document.querySelectorAll(".map-toolbar button").forEach((button) => {
    if (!button.dataset.baseTitle) button.dataset.baseTitle = button.title || "飞行控制";
    const tool = button.dataset.tool || "";
    const control = button.dataset.control || "";
    const needsLink = Boolean(tool) || ["hover", "land", "return_home", "rtl"].includes(control);
    if (!needsLink) return;
    // busy 瞬时值会让按钮闪烁禁用，指令本身由执行器排队，不再据此禁用
    const disabled = !linked;
    button.disabled = disabled;
    const channel = control === "return_home"
      ? contract.return_channel
      : contract.command_channel;
    button.title = disabled
      ? (!linked ? linkReason : "飞控正在执行任务")
      : `${button.dataset.baseTitle}${channel ? ` · ${channel}` : ""} · 目标: ${controlTargetLabel()}`;
  });
}

function renderMissionMetrics(drone) {
  drone = drone || latestState?.tool_runtime?.drone || {};
  const pos = drone.position_ned || { x: 0, y: 0, z: 0 };
  const altitude = Math.max(0, Math.abs(Number(pos.z || 0)));
  const toolRuntime = latestState?.tool_runtime || {};
  const connected = Boolean(toolRuntime.connected) && !toolRuntime.stale_connection;

  const dronePos = getDroneLatLon(drone);
  const mapPositionReliable = Boolean(dronePos);
  const horizontalDistM = Math.hypot(Number(pos.x || 0), Number(pos.y || 0));

  // 当前目标航点：按无人机在航线上的投影推断，避免离开 1 号航点后虚线/指标倒回旧航点
  let currentWpIdx = -1;
  if (dronePos?.lat != null && dronePos?.lon != null) {
    const target = currentTargetWaypoint([dronePos.lon, dronePos.lat]);
    currentWpIdx = target ? missionWaypoints.indexOf(target) : -1;
  }

  const prevIdx = currentWpIdx > 0 ? currentWpIdx - 1 : -1;
  const nextIdx = currentWpIdx >= 0 && currentWpIdx < missionWaypoints.length - 1 ? currentWpIdx + 1 : -1;

  const targetAlt = nextIdx >= 0
    ? (missionWaypoints[nextIdx].alt_m || 0)
    : currentWpIdx >= 0
    ? (missionWaypoints[currentWpIdx].alt_m || 0)
    : 0;
  const altDiff = targetAlt - altitude;

  // BRG：当前航点指向下一个航点的方位（任务航向）
  const bearing = nextIdx >= 0
    ? calculateBearing(missionWaypoints[currentWpIdx].lat, missionWaypoints[currentWpIdx].lon, missionWaypoints[nextIdx].lat, missionWaypoints[nextIdx].lon)
    : (currentWpIdx > 0
      ? calculateBearing(missionWaypoints[prevIdx].lat, missionWaypoints[prevIdx].lon, missionWaypoints[currentWpIdx].lat, missionWaypoints[currentWpIdx].lon)
      : null);

  // DIST PREV：当前航点与上一航点之间的航线段距离
  const distPrev = currentWpIdx > 0
    ? haversineMeters(missionWaypoints[prevIdx].lat, missionWaypoints[prevIdx].lon, missionWaypoints[currentWpIdx].lat, missionWaypoints[currentWpIdx].lon)
    : null;

  // HOME DIST：当前位置到飞控 HOME_POSITION 的距离。航点距离不会再伪装成飞行范围。
  const home = mapPositionReliable ? returnHomeGeo(drone, toolRuntime) : null;
  const homeDistance = home?.lat != null && home?.lon != null
    ? haversineMeters(dronePos.lat, dronePos.lon, home.lat, home.lon)
    : (!isRealVehicleRuntime(toolRuntime) ? horizontalDistM : null);

  // TILT：机体倾斜角（roll/pitch 合成，后端均为弧度）
  const att = drone.attitude_rad || {};
  const tiltDeg = Math.sqrt(Math.pow(Number(att.roll || 0), 2) + Math.pow(Number(att.pitch || 0), 2)) * (180 / Math.PI);

  if (els.metricHeading) els.metricHeading.textContent = `${fmt(drone.heading_deg || 0)}°`;
  if (els.metricAltDiff) els.metricAltDiff.textContent = missionWaypoints.length && hasReliableVehicleMapPosition(drone, toolRuntime) ? formatSigned(altDiff, "m") : "--";
  if (els.metricBearing) els.metricBearing.textContent = bearing != null ? `${fmt(bearing)}°` : "--";
  if (els.metricDistPrev) els.metricDistPrev.textContent = distPrev != null ? formatDistance(distPrev) : "--";
  if (els.metricMaxRange) els.metricMaxRange.textContent = connected && homeDistance != null ? formatDistance(homeDistance) : "--";
  if (els.metricTilt) els.metricTilt.textContent = `${fmt(tiltDeg)}°`;
}

function renderWaypoints() {
  syncWaypointActionLabels();
  renderMissionMetrics();

  if (!els.waypointList) return;

  if (!missionWaypoints.length) {
    const hint = isMultiVehiclePlanning() && !currentMissionVehicleName()
      ? `点击左上角的无人机选择目标机<br>再点击地图添加航点`
      : `点击地图添加航点<br>双击航点可删除`;
    els.waypointList.innerHTML = `<div class="empty">${hint}</div>`;
    hideWaypointProperties();
    return;
  }

  els.waypointList.innerHTML = missionWaypoints.map((wp, index) => {
    const selected = index === selectedWaypointIndex ? "selected" : "";
    const typeLabel = WAYPOINT_TYPE_LABELS[wp.type] || "航点";
    return `
      <article class="waypoint-item ${selected}" data-waypoint-row="${index}">
        <span class="waypoint-index">${index + 1}</span>
        <div class="waypoint-main">
          <strong>${typeLabel}</strong>
        </div>
        <button class="delete-waypoint" data-waypoint-delete="${index}" title="删除此航点">×</button>
      </article>
    `;
  }).join("");

  document.querySelectorAll("[data-waypoint-delete]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      const idx = Number(button.dataset.waypointDelete);
      missionWaypoints.splice(idx, 1);
      markMissionEdited();
      if (selectedWaypointIndex === idx) selectedWaypointIndex = -1;
      renderWaypoints();
      drawMissionPath();
    }, { once: true });
  });

  document.querySelectorAll("[data-waypoint-row]").forEach((row) => {
    row.addEventListener("click", () => {
      const idx = Number(row.dataset.waypointRow);
      selectedWaypointIndex = idx;
      renderWaypoints();
      drawMissionPath();
      const wp = missionWaypoints[idx];
      if (wp && maplibreMap) maplibreMap.panTo([wp.lon, wp.lat], { animate: true });
      showWaypointProperties(idx);
    });
  });

}

function hideWaypointProperties() {
  if (els.waypointProperties) els.waypointProperties.classList.add("hidden");
}

function showWaypointProperties(index) {
  const wp = missionWaypoints[index];
  if (!wp || !els.waypointProperties) return;
  selectedWaypointIndex = index;
  els.waypointProperties.classList.remove("hidden");
  if (els.wpPropType) els.wpPropType.value = wp.type || "waypoint";
  const defaults = missionDefaults();
  if (els.wpPropAlt) els.wpPropAlt.value = wp.alt_m ?? defaults.altitude;
  if (els.wpPropSpeed) els.wpPropSpeed.value = wp.speed_mps ?? defaults.speed;
  if (els.wpPropHold) els.wpPropHold.value = wp.hold_s ?? defaults.hold;
  if (els.wpPropAccept) els.wpPropAccept.value = wp.acceptance_radius_m ?? defaults.acceptance;
}

function applyWaypointProperties() {
  if (selectedWaypointIndex < 0 || selectedWaypointIndex >= missionWaypoints.length) return;
  const wp = missionWaypoints[selectedWaypointIndex];
  const defaults = missionDefaults();
  wp.type = els.wpPropType ? els.wpPropType.value : wp.type;
  wp.alt_m = Math.max(0.5, parseFloat(els.wpPropAlt?.value) || defaults.altitude);
  wp.speed_mps = Math.max(0.2, parseFloat(els.wpPropSpeed?.value) || defaults.speed);
  wp.hold_s = Math.max(0, parseFloat(els.wpPropHold?.value) || 0);
  wp.acceptance_radius_m = Math.max(0.5, parseFloat(els.wpPropAccept?.value) || defaults.acceptance);
  markMissionEdited();
  renderWaypoints();
  drawMissionPath();
}

function buildWaypointCommand() {
  const route = missionWaypoints
    .map((wp, index) => `${index + 1}. lat ${wp.lat}, lon ${wp.lon}, alt ${wp.alt_m}m`)
    .join("；");
  const speed = missionDefaults().speed;
  return `按以下航点规划并执行飞行，速度${speed}m/s，完成后悬停并报告状态：${route}`;
}

function updateMapView(state) {
  if (!maplibreMap) return;

  // 遥测/状态更新时刷新剖面（合并到下一帧，避免高频重绘）
  scheduleProfileRedraw();

  const runtime = state.tool_runtime || {};
  // 仿真场景挪到别的城市时（AirSim settings.json 的 OriginGeopoint），NED↔GPS
  // 的换算基准跟着换，地图初始中心和 H 标记也跟着换。已经因遥测定位过、或者
  // 用户自己拖动过地图，就不再抢视角。
  if (applyMapOrigin(runtime.map_origin)) {
    if (homeMarker && !homeGpsPosition(drone, runtime, state)) {
      homeMarker.setLngLat([mapOriginLon(), mapOriginLat()]);
    }
    if (!mapCenteredOnFirstVehicle && !maplibreMap._userPanned) {
      maplibreMap.jumpTo({ center: [mapOriginLon(), mapOriginLat()] });
    }
  }
  const drone = runtime.drone || {};
  const backendName = backendDisplayName(runtime);
  const linked = Boolean(runtime.connected) && !runtime.stale_connection;
  // function-scoped: used both inside the multi-vehicle branch and at the
  // status line below — a block-scoped declaration would throw
  // "gps is not defined" (ReferenceError) inside the else branch consumers
  const gps = droneGpsPosition(drone, runtime);

  if (!linked) {
    // 未连接/链路过期/正在切换后端：清除旧的无人机与多机标记、轨迹和
    // H 点，避免把过去链路的残影当作当前状态显示
    stopDroneAnimation();
    droneRenderedLngLat = null;
    droneLastTelemetryLngLat = null;
    if (droneMarker) {
      droneMarker.remove();
      droneMarker = null;
    }
    for (const entry of vehicleMarkers.values()) {
      try {
        entry.marker.remove();
      } catch (e) { /* already removed */ }
    }
    vehicleMarkers.clear();
    vehicleTracks.clear();
    for (const marker of vehicleHomeMarkers.values()) {
      try {
        marker.remove();
      } catch (e) { /* already removed */ }
    }
    vehicleHomeMarkers.clear();
    if (homeMarker) {
      homeMarker.remove();
      homeMarker = null;
    }
    clearVehicleTrack();
    clearActiveLeg();
  }

  const vehicles = Array.isArray(runtime.vehicles) ? runtime.vehicles : [];
  if (vehicles.length > 1) {
    // 多机模式：每机 marker + 独立轨迹（单机逻辑保留给 vehicles.length <= 1）
    stopDroneAnimation();
    if (droneMarker) {
      droneMarker.remove();
      droneMarker = null;
    }
    droneRenderedLngLat = null;
    droneLastTelemetryLngLat = null;
    updateVehicleMarkers(vehicles, runtime);
    if (!applicationSettings.map.show_vehicle_track) clearVehicleTrack();
    // 首次定位跟随面板选中的目标机（未选则默认第一架）
    const followTargetName = currentMissionVehicleName();
    const followTarget = vehicles.find((v) => String(v.vehicle_name || "") === followTargetName) || vehicles[0];
    const firstPos = vehicleMarkerPosition(followTarget, runtime);
    if (firstPos && linked && applicationSettings.map.follow_vehicle && !mapCenteredOnFirstVehicle && !maplibreMap._userPanned) {
      mapCenteredOnFirstVehicle = true;
      maplibreMap.jumpTo({ center: firstPos });
    }
  } else {
  // 更新无人机位置 marker
  if (gps) {
    // gps 为 [lat, lon]，MapLibre 用 [lng, lat]
    const lngLat = [gps[1], gps[0]];
    const displayJumpM = droneLastTelemetryLngLat
      ? haversineMeters(droneLastTelemetryLngLat[1], droneLastTelemetryLngLat[0], lngLat[1], lngLat[0])
      : 0;
    const maxDisplayJumpM = Number(applicationSettings.safety.max_display_jump_m || 120);
    if (isRealVehicleRuntime(runtime) && displayJumpM > maxDisplayJumpM && !drone.flying) {
      els.mapStatus.textContent = `GPS 显示跳变 ${formatDistance(displayJumpM)}，地面状态下已拒绝移动地图标记`;
      return;
    }
    const targetWaypoint = currentTargetWaypoint(lngLat);
    const heading = resolveDroneHeading(drone, lngLat);
    const streamFreshAt = Number(runtime.telemetry_stream_received_at_ms || 0);
    const streamFresh = Boolean(runtime.telemetry_stream_active) && (!streamFreshAt || Date.now() - streamFreshAt < 1500);
    const immediateMarkerUpdate = rosTelemetryConnected || streamFresh;
    updateDroneMarker(lngLat, heading, { immediate: immediateMarkerUpdate });
    droneLastTelemetryLngLat = lngLat.slice();
    if (applicationSettings.map.show_vehicle_track) updateVehicleTrack(lngLat, drone, linked);
    else clearVehicleTrack();
    if (!SHOW_ACTIVE_LEG) clearActiveLeg();
    // 首次或长时间未连接后定位到无人机
    if (linked && applicationSettings.map.follow_vehicle && !mapCenteredOnFirstVehicle && !maplibreMap._userPanned) {
      mapCenteredOnFirstVehicle = true;
      maplibreMap.jumpTo({ center: lngLat });
    }
  } else if (linked && isRealVehicleRuntime(runtime)) {
    stopDroneAnimation();
    if (droneMarker) {
      droneMarker.remove();
      droneMarker = null;
    }
    droneRenderedLngLat = null;
    droneLastTelemetryLngLat = null;
    clearVehicleTrack();
    clearActiveLeg();
  }
  }

  // 更新 home marker（PX4 后端可能有真实 home，AirSim 用模拟原点）
  // 更新 home marker（PX4 后端可能有真实 home；AirSim 单机时用模拟原点）
  // 多机模式下每机有自己的 H 标记，中央的旧 homeMarker 会误导坐标对应，直接移除
  const multiVehicleHomeDisplay =
    isMultiVehiclePlanning() ||
    (Array.isArray(state?.tool_runtime?.vehicles) && state.tool_runtime.vehicles.length > 1);
  const homeGps = homeGpsPosition(drone, runtime, state);
  if (multiVehicleHomeDisplay) {
    if (homeMarker) {
      homeMarker.remove();
      homeMarker = null;
    }
  } else if (homeGps && homeMarker) {
    homeMarker.setLngLat([homeGps[1], homeGps[0]]);
  }

  // 航点不随遥测重绘（参考 QGC：航点 visual 独立，只在 plan 变化时更新，避免闪烁）
  // drawMissionPath 由航点增删/选中/拖拽时显式调用

  const wpCount = missionWaypoints.length;
  els.canvasScale.textContent = `${wpCount} WP · ${backendName}`;
  const fix = Number(drone.gps_fix_type || 0);
  const sats = Number(drone.satellites_visible);
  const accuracy = Number(drone.gps_horizontal_accuracy_m);
  const gpsMeta = [
    fix ? `Fix ${fix}` : "",
    Number.isFinite(sats) ? `${sats} sats` : "",
    Number.isFinite(accuracy) ? `±${accuracy.toFixed(1)} m` : "",
  ].filter(Boolean).join(" · ");
  els.mapStatus.textContent = linked
    ? (gps ? `${backendName} · ${drone.position_source || "GPS"}${gpsMeta ? ` · ${gpsMeta}` : ""}` : `${backendName} 已连接 · 等待可靠 GPS`)
    : `等待 ${backendName} 链路`;
}

// GeoJSON setData makes MapLibre re-parse the data and repaint the canvas.
// These run on every telemetry tick (250 ms, and 20 Hz on the ROS stream), so
// re-uploading unchanged collections repainted the map 4-20 times a second.
// That is what made the translucent HUD above the map look like it gained and
// lost a shadow: each canvas paint invalidates the backdrop blur behind it.
// Styling could not fix it -- the repaints had to stop.
const _sourceDataMemo = new Map();
function setSourceData(source, data) {
  if (!source) return;
  let key = null;
  try { key = JSON.stringify(data); } catch (err) { key = null; }
  const name = source.id || (typeof source.serialize === 'function' ? source.serialize().id : '') || '';
  if (key !== null && _sourceDataMemo.get(name) === key) return;
  if (key !== null) _sourceDataMemo.set(name, key);
  source.setData(data);
}

function drawMissionPath() {
  if (!maplibreMap) return;
  const pathSource = maplibreMap.getSource("path-source");
  const wpSource = maplibreMap.getSource("wp-source");
  if (!pathSource || !wpSource) return;

  // 航线统一由 plan-source 按机配色绘制（每机一色，当前目标机高亮），
  // 旧的白色 path-line 不再使用，保持 source 存在以兼容既有 layer 定义。
  setSourceData(pathSource, { type: "FeatureCollection", features: [] });

  const planSource = maplibreMap.getSource("plan-source");
  if (planSource) {
    const features = [];
    const activeTarget = currentMissionVehicleName();
    const addRoute = (vehicle, route) => {
      if (!Array.isArray(route) || !route.length) return;
      const color = vehicleRouteColor(vehicle);
      const isActive = (vehicle || "") === activeTarget;
      if (route.length >= 2) {
        features.push({
          type: "Feature",
          properties: { vehicle, color, active: isActive, kind: "line" },
          geometry: { type: "LineString", coordinates: route.map((wp) => [wp.lon, wp.lat]) },
        });
      }
      for (const wp of route) {
        features.push({
          type: "Feature",
          properties: { vehicle, color, active: isActive, kind: "dot" },
          geometry: { type: "Point", coordinates: [wp.lon, wp.lat] },
        });
      }
    };
    if (!activeTarget) {
      // 单机模式：当前航线即全部
      addRoute("", missionWaypoints);
    } else {
      for (const [vehicle, route] of Object.entries(missionPlans)) {
        if (vehicle === activeTarget) continue;
        addRoute(vehicle, route);
      }
      addRoute(activeTarget, missionWaypoints);
    }
    setSourceData(planSource, { type: "FeatureCollection", features });
  }

  // 航点 GeoJSON features（id 用于 setFeatureState，type 用于 sprite 配色）
  const features = missionWaypoints.map((wp, index) => ({
    type: "Feature",
    id: index + 1,
    properties: { seq: index + 1, type: wp.type || "waypoint" },
    geometry: { type: "Point", coordinates: [wp.lon, wp.lat] },
  }));
  setSourceData(wpSource, { type: "FeatureCollection", features });

  // 拖拽中只更新数据，不清除/设置选中态（避免中断拖拽）
  if (wpDragging) return;

  // 选中态：先清除全部，再设置当前选中
  for (let i = 0; i < missionWaypoints.length; i++) {
    try {
      maplibreMap.setFeatureState({ source: "wp-source", id: i + 1 }, { selected: false });
    } catch (err) {
      // 忽略 feature 不存在
    }
  }
  if (selectedWaypointIndex >= 0) {
    try {
      maplibreMap.setFeatureState(
        { source: "wp-source", id: selectedWaypointIndex + 1 },
        { selected: true },
      );
    } catch (err) {
      // 忽略 feature 不存在
    }
  }
  drawMissionProfile();
  refreshActiveLegFromCurrentPosition();
}

function drawFence() {
  if (!maplibreMap) return;
  const fenceSource = maplibreMap.getSource("fence-source");
  if (!fenceSource) return;

  if (missionFence.length < 2) {
    setSourceData(fenceSource, { type: "FeatureCollection", features: [] });
    return;
  }

  const coords = missionFence.map((p) => [p.lon, p.lat]);
  if (missionFence.length >= 3) coords.push(coords[0]);

  setSourceData(fenceSource, {
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        geometry: {
          type: missionFence.length >= 3 ? "Polygon" : "LineString",
          coordinates: missionFence.length >= 3 ? [coords] : coords,
        },
      },
    ],
  });
}





// NED (north/east/down meters) → GPS (lat/lon degrees) 转换
// 使用平面近似，小范围（<10km）内有效
function currentDroneGeo(drone, runtime = latestState?.tool_runtime || {}) {
  const gps = drone?.gps || {};
  const realVehicle = isRealVehicleRuntime(runtime);
  const gpsLooksValid = Boolean(gps.lat && gps.lon && Math.abs(Number(gps.lat)) > 0.001);
  if (gpsLooksValid && (!realVehicle || hasReliableVehicleMapPosition(drone, runtime))) {
    return {
      lat: Number(gps.lat),
      lon: Number(gps.lon),
      alt_m: Number(gps.alt ?? gps.relative_alt ?? 0),
    };
  }
  if (realVehicle) return null;
  const pos = drone?.position_ned || {};
  if (typeof pos.x === "number" || typeof pos.y === "number") {
    const gpsFromNed = nedToGps(Number(pos.x || 0), Number(pos.y || 0), Number(pos.z || 0));
    return { lat: gpsFromNed.lat, lon: gpsFromNed.lon, alt_m: gpsFromNed.alt };
  }
  return null;
}

function droneGpsPosition(drone, runtime) {
  const geo = currentDroneGeo(drone, runtime);
  return geo ? [geo.lat, geo.lon] : null;
}

function homeGpsPosition(drone, runtime, state = latestState) {
  if (isRealVehicleRuntime(runtime) && !hasReliableVehicleMapPosition(drone, runtime)) {
    return null;
  }
  const home = returnHomeGeo(drone, runtime);
  return home ? [home.lat, home.lon] : null;
}

function returnHomeGeo(drone, runtime = latestState?.tool_runtime || {}) {
  const backend = String(runtime.backend || "");
  const mavHome = drone?.home_position || {};
  if (Number.isFinite(Number(mavHome.lat)) && Number.isFinite(Number(mavHome.lon))) {
    returnHomeGps = {
      lat: Number(mavHome.lat),
      lon: Number(mavHome.lon),
      alt_m: Number(mavHome.alt || 0),
      source: mavHome.source || "MAVLink HOME_POSITION",
      backend,
    };
    return returnHomeGps;
  }
  if (returnHomeGps?.lat != null && returnHomeGps?.lon != null && returnHomeGps.backend === backend) {
    return returnHomeGps;
  }
  const current = currentDroneGeo(drone, runtime);
  if (current?.lat != null && current?.lon != null) {
    returnHomeGps = {
      lat: Number(current.lat),
      lon: Number(current.lon),
      alt_m: Number(current.alt_m || 0),
      source: isRealVehicleRuntime(runtime) ? "first reliable GPS fix" : "simulation origin",
      backend,
    };
    return returnHomeGps;
  }
  if (isRealVehicleRuntime(runtime)) return null;
  returnHomeGps = { lat: mapOriginLat(), lon: mapOriginLon(), alt_m: 0, source: "AirSim origin", backend };
  return returnHomeGps;
}

function droneHeadingDeg(drone) {
  if (Number.isFinite(Number(drone.heading_deg))) return Number(drone.heading_deg);
  if (Number.isFinite(Number(drone.heading))) return Number(drone.heading);
  const yawRad = Number(drone.attitude_rad?.yaw);
  if (Number.isFinite(yawRad)) return (yawRad * 180 / Math.PI + 360) % 360;
  const yawDeg = Number(drone.attitude_euler?.yaw);
  return Number.isFinite(yawDeg) ? yawDeg : 0;
}

function updateActiveLeg(lngLat, target = currentTargetWaypoint(lngLat)) {
  const source = maplibreMap?.getSource("active-leg-source");
  if (!source) return;
  if (!target) {
    setSourceData(source, { type: "FeatureCollection", features: [] });
    return;
  }
  setSourceData(source, {
    type: "FeatureCollection",
    features: [{
      type: "Feature",
      properties: {},
      geometry: { type: "LineString", coordinates: [lngLat, [target.lon, target.lat]] },
    }],
  });
}

function clearActiveLeg() {
  const source = maplibreMap?.getSource("active-leg-source");
  if (source) setSourceData(source, { type: "FeatureCollection", features: [] });
}

function refreshActiveLegFromCurrentPosition() {
  if (!SHOW_ACTIVE_LEG) {
    clearActiveLeg();
    return;
  }
  const lngLat = droneRenderedLngLat || droneLastTelemetryLngLat;
  if (!lngLat) return;
  updateActiveLeg(lngLat);
}

function currentTargetWaypoint(lngLat) {
  if (!missionWaypoints.length || !Array.isArray(lngLat)) return null;
  const targetIndex = currentTargetWaypointIndex(lngLat);
  return targetIndex >= 0 ? missionWaypoints[targetIndex] : null;
}

function currentTargetWaypointIndex(lngLat) {
  if (!Array.isArray(lngLat)) return -1;
  if (!missionWaypoints.length) {
    activeTargetRouteKey = "";
    activeTargetIndex = 0;
    return -1;
  }
  const points = missionWaypoints
    .map((wp, index) => ({ wp, index }))
    .filter((item) => item.wp.lat != null && item.wp.lon != null);
  if (!points.length) {
    activeTargetRouteKey = "";
    activeTargetIndex = 0;
    return -1;
  }

  const routeKey = missionRouteKey(points);
  if (routeKey !== activeTargetRouteKey) {
    activeTargetRouteKey = routeKey;
    activeTargetIndex = 0;
  }

  if (!missionExecutionActive) {
    return points[0].index;
  }

  const current = { lat: Number(lngLat[1]), lon: Number(lngLat[0]) };
  while (activeTargetIndex < points.length) {
    const target = points[activeTargetIndex].wp;
    const distance = haversineMeters(current.lat, current.lon, target.lat, target.lon);
    const accept = Math.max(2.5, Number(target.acceptance_radius_m || 2) + 1.0);
    if (distance <= accept) {
      activeTargetIndex += 1;
    } else {
      break;
    }
  }

  if (activeTargetIndex >= points.length) {
    missionExecutionActive = false;
    return -1;
  }
  return points[activeTargetIndex].index;
}

function missionRouteKey(points) {
  return points
    .map(({ wp }) => `${round6(wp.lat)}:${round6(wp.lon)}`)
    .join("|");
}

function nedToGps(northM, eastM, downM) {
  const lat0 = mapOriginLat();
  const dLat = northM / EARTH_RADIUS_M * (180 / Math.PI);
  const dLon = eastM / (EARTH_RADIUS_M * Math.cos(lat0 * Math.PI / 180)) * (180 / Math.PI);
  return {
    lat: lat0 + dLat,
    lon: mapOriginLon() + dLon,
    alt: -downM,
  };
}

// GPS → NED 反向转换
function gpsToNed(lat, lon, downM) {
  const lat0 = mapOriginLat();
  const dLat = (lat - lat0) * Math.PI / 180;
  const dLon = (lon - mapOriginLon()) * Math.PI / 180;
  const northM = dLat * EARTH_RADIUS_M;
  const eastM = dLon * EARTH_RADIUS_M * Math.cos(lat0 * Math.PI / 180);
  return { x: northM, y: eastM, z: downM };
}

function getDroneLatLon(drone) {
  const runtime = latestState?.tool_runtime || {};
  if (!hasReliableVehicleMapPosition(drone, runtime)) return null;
  const gps = drone?.gps;
  if (gps && gps.lat != null && gps.lon != null) {
    return { lat: Number(gps.lat), lon: Number(gps.lon) };
  }
  const pos = drone?.position_ned || { x: 0, y: 0, z: 0 };
  return nedToGps(Number(pos.x || 0), Number(pos.y || 0), Number(pos.z || 0));
}

function computeMissionDistance() {
  if (missionWaypoints.length < 2) return 0;
  let total = 0;
  for (let i = 1; i < missionWaypoints.length; i++) {
    const a = missionWaypoints[i - 1];
    const b = missionWaypoints[i];
    if (a.lat != null && a.lon != null && b.lat != null && b.lon != null) {
      total += haversineMeters(a.lat, a.lon, b.lat, b.lon);
    }
  }
  return total;
}

function drawMissionProfile() {
  const canvas = els.profileCanvas;
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  // 仅在尺寸变化时重置画布缓冲，遥测高频重绘时避免无谓的重新分配
  const bufferW = Math.max(1, Math.floor(rect.width * dpr));
  const bufferH = Math.max(1, Math.floor(rect.height * dpr));
  if (canvas.width !== bufferW) canvas.width = bufferW;
  if (canvas.height !== bufferH) canvas.height = bufferH;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const width = rect.width;
  const height = rect.height;

  ctx.clearRect(0, 0, width, height);

  // 折叠或高度不足时只清空，不重绘
  if (height < 60) {
    missionProfileView = null;
    return;
  }

  const padding = { top: 16, right: 42, bottom: 38, left: 50 };
  const chartW = width - padding.left - padding.right;
  const chartH = height - padding.top - padding.bottom;

  // 动态 X 轴范围：使用当前地图可视宽度，让剖面图随卫星图缩放变化
  const visibleDist = getVisibleMapDistanceMeters();

  // 计算航点累计距离/时间，并带出类型、速度、悬停信息
  const points = [];
  let cumulative = 0;
  let cumulativeTime = 0;
  if (missionWaypoints.length >= 2) {
    const first = missionWaypoints[0];
    points.push({
      dist: 0,
      alt: Number(first.alt_m || 0),
      seq: 1,
      type: first.type || "waypoint",
      speed: Math.max(0.1, Number(first.speed_mps || 2)),
      hold: Math.max(0, Number(first.hold_s || 0)),
      time: 0,
    });
    for (let i = 1; i < missionWaypoints.length; i++) {
      const a = missionWaypoints[i - 1];
      const b = missionWaypoints[i];
      const segment = haversineMeters(a.lat, a.lon, b.lat, b.lon);
      cumulative += segment;
      const speed = Math.max(0.1, Number(b.speed_mps || 2));
      const hold = Math.max(0, Number(b.hold_s || 0));
      cumulativeTime += segment / speed + hold;
      points.push({
        dist: cumulative,
        alt: Number(b.alt_m || 0),
        seq: i + 1,
        type: b.type || "waypoint",
        speed,
        hold,
        time: cumulativeTime,
      });
    }
  }

  const maxDist = Math.max(visibleDist, cumulative || 1);
  const maxSpeed = points.length ? Math.max(0.1, ...points.map((p) => p.speed)) : 1;
  const speedScaleMax = Math.max(0.5, maxSpeed * 1.2);

  // 实时无人机：投影到航线求里程，取当前高度
  const droneMarker = computeProfileDroneMarker(points);
  const droneAlt = droneMarker ? droneMarker.alt : null;

  const alts = points.length ? points.map((p) => p.alt) : [0, 10];
  if (droneAlt != null) alts.push(droneAlt);
  let minAlt = Math.min(...alts);
  let maxAlt = Math.max(...alts);
  if (missionProfileDrag) {
    // 拖拽中用固定比例换算高度(见 updateProfileDragAltitude)，此处仅保证被拖点留在图内
    const dragAlt = Number(missionWaypoints[missionProfileDrag.index]?.alt_m ?? minAlt);
    const span = Math.max(1, missionProfileDrag.altRange);
    minAlt = Math.max(0, Math.min(missionProfileDrag.minAlt, dragAlt - span * 0.2));
    maxAlt = Math.max(missionProfileDrag.minAlt + span, dragAlt + span * 0.2);
  } else if (maxAlt - minAlt < 5) {
    const mid = (minAlt + maxAlt) / 2;
    minAlt = mid - 2.5;
    maxAlt = mid + 2.5;
  }
  const altRange = Math.max(1, maxAlt - minAlt);

  const xFor = (d) => padding.left + (d / maxDist) * chartW;
  const yFor = (a) => padding.top + chartH - ((a - minAlt) / altRange) * chartH;
  const yForSpeed = (s) => padding.top + chartH - (Math.max(0, s) / speedScaleMax) * chartH;

  // 记录几何供指针命中测试；单点/空状态时不提供可拖拽点
  missionProfileView = {
    paddingTop: padding.top,
    chartH,
    minAlt,
    altRange,
    points: points.length >= 2
      ? points.map((p) => ({ index: p.seq - 1, x: xFor(p.dist), y: yFor(p.alt) }))
      : [],
  };

  // Y 轴水平网格线
  ctx.strokeStyle = "rgba(255,255,255,0.06)";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const y = padding.top + (chartH * i) / 4;
    ctx.beginPath();
    ctx.moveTo(padding.left, y);
    ctx.lineTo(padding.left + chartW, y);
    ctx.stroke();
  }

  // X 轴垂直网格线与主刻度（根据像素间距自动避免标签重叠）
  let xStep = niceDistance(maxDist / 5);
  const minPxBetweenLabels = 56;
  const ticksCount = maxDist / xStep;
  const pxPerTick = chartW / ticksCount;
  if (xStep > 0 && pxPerTick < minPxBetweenLabels) {
    xStep = xStep * Math.ceil(minPxBetweenLabels / pxPerTick);
  }
  ctx.strokeStyle = "rgba(255,255,255,0.05)";
  for (let d = 0; d <= maxDist + 0.001; d += xStep) {
    const x = xFor(d);
    ctx.beginPath();
    ctx.moveTo(x, padding.top);
    ctx.lineTo(x, padding.top + chartH);
    ctx.stroke();
  }

  if (points.length >= 2) {
    // 高度填充区域
    ctx.beginPath();
    ctx.moveTo(xFor(points[0].dist), yFor(points[0].alt));
    for (let i = 1; i < points.length; i++) ctx.lineTo(xFor(points[i].dist), yFor(points[i].alt));
    ctx.lineTo(xFor(points[points.length - 1].dist), padding.top + chartH);
    ctx.lineTo(xFor(points[0].dist), padding.top + chartH);
    ctx.closePath();
    const gradient = ctx.createLinearGradient(0, padding.top, 0, padding.top + chartH);
    gradient.addColorStop(0, "rgba(85, 223, 244, 0.35)");
    gradient.addColorStop(1, "rgba(85, 223, 244, 0.04)");
    ctx.fillStyle = gradient;
    ctx.fill();

    // 高度线
    ctx.beginPath();
    ctx.moveTo(xFor(points[0].dist), yFor(points[0].alt));
    for (let i = 1; i < points.length; i++) ctx.lineTo(xFor(points[i].dist), yFor(points[i].alt));
    ctx.strokeStyle = "#55dff4";
    ctx.lineWidth = 2;
    ctx.stroke();

    // 速度剖面：右侧独立刻度，紫虚线叠加（配合 tooltip 读取数值）
    ctx.save();
    ctx.beginPath();
    ctx.moveTo(xFor(points[0].dist), yForSpeed(points[0].speed));
    for (let i = 1; i < points.length; i++) ctx.lineTo(xFor(points[i].dist), yForSpeed(points[i].speed));
    ctx.strokeStyle = "rgba(167, 139, 250, 0.9)";
    ctx.lineWidth = 1.5;
    ctx.setLineDash([5, 4]);
    ctx.stroke();
    ctx.restore();

    // 航点标记：按类型配色，选中时加琥珀色粗描边
    points.forEach((p) => {
      const x = xFor(p.dist);
      const y = yFor(p.alt);
      const selected = selectedWaypointIndex + 1 === p.seq;
      const markerColor = WAYPOINT_TYPE_COLORS[p.type] || WAYPOINT_TYPE_COLORS.waypoint;
      ctx.beginPath();
      ctx.arc(x, y, selected ? 5 : 4, 0, Math.PI * 2);
      ctx.fillStyle = markerColor;
      ctx.fill();
      ctx.strokeStyle = selected ? "#f0b84a" : "#06121a";
      ctx.lineWidth = selected ? 2.5 : 1.5;
      ctx.stroke();

      ctx.fillStyle = "rgba(237, 244, 255, 0.8)";
      ctx.font = "10px system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "alphabetic";
      ctx.fillText(String(p.seq), x, y - 8);
    });

    // 实时无人机：竖直参考线 + 紫色三角（与地图无人机标记同色）
    if (droneMarker) {
      const x = xFor(Math.min(droneMarker.dist, maxDist));
      const y = yFor(droneMarker.alt);
      ctx.save();
      ctx.strokeStyle = "rgba(167, 139, 250, 0.45)";
      ctx.lineWidth = 1;
      ctx.setLineDash([2, 3]);
      ctx.beginPath();
      ctx.moveTo(x, padding.top);
      ctx.lineTo(x, padding.top + chartH);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.beginPath();
      ctx.moveTo(x, y - 7);
      ctx.lineTo(x - 5, y + 3);
      ctx.lineTo(x + 5, y + 3);
      ctx.closePath();
      ctx.fillStyle = "#a78bfa";
      ctx.fill();
      ctx.strokeStyle = "#0b0f18";
      ctx.lineWidth = 1.5;
      ctx.stroke();
      ctx.restore();
    }

    // 拖拽中的航点：竖直参考线 + 高度气泡
    if (missionProfileDrag) {
      const dp = points.find((p) => p.seq - 1 === missionProfileDrag.index);
      if (dp) {
        const x = xFor(dp.dist);
        const y = yFor(dp.alt);
        ctx.save();
        ctx.strokeStyle = "rgba(240, 184, 74, 0.85)";
        ctx.lineWidth = 1;
        ctx.setLineDash([3, 3]);
        ctx.beginPath();
        ctx.moveTo(x, padding.top);
        ctx.lineTo(x, padding.top + chartH);
        ctx.stroke();
        ctx.setLineDash([]);

        ctx.beginPath();
        ctx.arc(x, y, 6, 0, Math.PI * 2);
        ctx.fillStyle = "#f0b84a";
        ctx.fill();
        ctx.strokeStyle = "#06121a";
        ctx.lineWidth = 2;
        ctx.stroke();

        const label = `${dp.alt.toFixed(1)} m`;
        ctx.font = "bold 11px system-ui, sans-serif";
        const boxW = ctx.measureText(label).width + 12;
        let boxX = x - boxW / 2;
        boxX = Math.max(padding.left, Math.min(boxX, padding.left + chartW - boxW));
        const boxY = Math.max(padding.top, y - 28);
        ctx.fillStyle = "rgba(7, 9, 15, 0.9)";
        ctx.fillRect(boxX, boxY, boxW, 18);
        ctx.strokeStyle = "rgba(240, 184, 74, 0.7)";
        ctx.lineWidth = 1;
        ctx.strokeRect(boxX, boxY, boxW, 18);
        ctx.fillStyle = "#f0b84a";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(label, boxX + boxW / 2, boxY + 9);
        ctx.restore();
      }
    }

    // 悬停详情：类型/高度/速度/悬停/里程/预计到达
    if (missionProfileHover != null && !missionProfileDrag) {
      const hp = points.find((p) => p.seq - 1 === missionProfileHover);
      if (hp) drawProfileTooltip(ctx, hp, xFor(hp.dist), yFor(hp.alt), padding, chartW, chartH);
    }
  } else {
    // 空状态：轻量网格背景 + 提示
    ctx.strokeStyle = "rgba(255,255,255,0.05)";
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const x = padding.left + (chartW * i) / 4;
      ctx.beginPath();
      ctx.moveTo(x, padding.top);
      ctx.lineTo(x, padding.top + chartH);
      ctx.stroke();
    }

    // 中央提示文字
    ctx.fillStyle = "rgba(237, 244, 255, 0.72)";
    ctx.font = "12px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText("点击地图添加航点", width / 2, height / 2 - 8);
    ctx.fillStyle = "rgba(141, 152, 173, 0.6)";
    ctx.font = "11px system-ui, sans-serif";
    ctx.fillText("至少两个航点后可显示高度/距离剖面", width / 2, height / 2 + 10);
  }

  // 比例尺：根据地图当前缩放级别计算固定像素长度对应的地面距离
  function getMapScale() {
    if (!maplibreMap) return null;
    try {
      const metersPerPx = 1 / (maplibreMap.transform._pixelPerMeter || 1);
      const targetPx = 70;
      const rawDist = metersPerPx * targetPx;
      const dist = niceDistance(rawDist);
      const px = dist / metersPerPx;
      return { dist, px };
    } catch (err) {
      return null;
    }
  }
  const scale = getMapScale();
  if (scale) {
    const label = formatDistance(scale.dist);
    const labelWidth = ctx.measureText(label).width;
    const sx = width - padding.right - Math.max(scale.px, labelWidth) - 10;
    const sy = padding.top + 14;
    // 小背景提升可读性
    ctx.fillStyle = "rgba(7, 9, 15, 0.65)";
    ctx.fillRect(sx - 4, sy - 16, Math.max(scale.px, labelWidth) + 10, 22);
    ctx.strokeStyle = "rgba(237, 244, 255, 0.9)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(sx, sy);
    ctx.lineTo(sx + scale.px, sy);
    ctx.stroke();
    ctx.fillStyle = "rgba(237, 244, 255, 0.95)";
    ctx.font = "bold 10px system-ui, sans-serif";
    ctx.textAlign = "left";
    ctx.textBaseline = "bottom";
    ctx.fillText(label, sx, sy - 4);
  }

  // 坐标轴文字
  ctx.fillStyle = "rgba(141, 152, 173, 0.8)";
  ctx.font = "10px system-ui, sans-serif";
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";
  for (let i = 0; i <= 4; i++) {
    const alt = minAlt + (altRange * i) / 4;
    const y = padding.top + chartH - (chartH * i) / 4;
    ctx.fillText(`${Math.round(alt)}m`, padding.left - 6, y);
  }

  // 速度右轴：仅上限与 0 两个刻度，避免与高度刻度混淆
  if (points.length >= 2) {
    ctx.fillStyle = "rgba(167, 139, 250, 0.85)";
    ctx.textAlign = "left";
    ctx.fillText(`${speedScaleMax.toFixed(1)}`, padding.left + chartW + 6, padding.top);
    ctx.fillText("0", padding.left + chartW + 6, padding.top + chartH);
  }

  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  for (let d = 0; d <= maxDist + 0.001; d += xStep) {
    const x = xFor(d);
    ctx.fillText(formatDistance(d), x, padding.top + chartH + 5);
  }

  // 轴标签
  ctx.save();
  ctx.translate(10, padding.top + chartH / 2);
  ctx.rotate(-Math.PI / 2);
  ctx.textAlign = "center";
  ctx.fillStyle = "rgba(141, 152, 173, 0.7)";
  ctx.fillText("高度", 0, 0);
  ctx.restore();

  ctx.textAlign = "center";
  ctx.textBaseline = "bottom";
  ctx.fillStyle = "rgba(141, 152, 173, 0.7)";
  ctx.fillText("距离", padding.left + chartW / 2, height - 6);
}

// 悬停详情气泡：类型/高度/速度/悬停/里程/预计到达
function drawProfileTooltip(ctx, point, x, y, padding, chartW, chartH) {
  const typeLabel = WAYPOINT_TYPE_LABELS[point.type] || "航点";
  const typeColor = WAYPOINT_TYPE_COLORS[point.type] || WAYPOINT_TYPE_COLORS.waypoint;
  const lines = [
    { text: `${point.seq} · ${typeLabel}`, color: typeColor, bold: true },
    { text: `高度 ${point.alt.toFixed(1)} m`, color: "rgba(237, 244, 255, 0.88)" },
    { text: `速度 ${point.speed.toFixed(1)} m/s`, color: "rgba(167, 139, 250, 0.95)" },
    { text: `悬停 ${fmt(point.hold)} s`, color: "rgba(237, 244, 255, 0.72)" },
    { text: `里程 ${formatDistance(point.dist)}`, color: "rgba(237, 244, 255, 0.72)" },
    { text: point.time > 0 ? `预计 ${formatDuration(point.time)}` : "起点", color: "rgba(78, 230, 164, 0.95)" },
  ];
  const lineH = 13;
  const padX = 8;
  const padY = 6;
  ctx.save();
  let maxTextW = 0;
  lines.forEach((line) => {
    ctx.font = line.bold ? "bold 10px system-ui, sans-serif" : "10px system-ui, sans-serif";
    maxTextW = Math.max(maxTextW, ctx.measureText(line.text).width);
  });
  const boxW = Math.min(chartW, maxTextW + padX * 2);
  const boxH = lines.length * lineH + padY * 2;
  let bx = x + 12;
  if (bx + boxW > padding.left + chartW) bx = x - 12 - boxW;
  bx = Math.max(padding.left, Math.min(bx, padding.left + chartW - boxW));
  let by = y - boxH - 10;
  if (by < padding.top) by = y + 12;
  by = Math.max(padding.top, Math.min(by, padding.top + chartH - boxH));

  ctx.fillStyle = "rgba(7, 9, 15, 0.94)";
  ctx.fillRect(bx, by, boxW, boxH);
  ctx.strokeStyle = "rgba(85, 223, 244, 0.45)";
  ctx.lineWidth = 1;
  ctx.strokeRect(bx, by, boxW, boxH);

  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  lines.forEach((line, i) => {
    ctx.font = line.bold ? "bold 10px system-ui, sans-serif" : "10px system-ui, sans-serif";
    ctx.fillStyle = line.color;
    ctx.fillText(line.text, bx + padX, by + padY + lineH * i + lineH / 2);
  });
  ctx.restore();
}

// 把无人机投影到航线，得到沿线里程与当前高度；离航线过远则不显示
function computeProfileDroneMarker(points) {
  if (!points || points.length < 2) return null;
  const drone = latestState?.tool_runtime?.drone;
  if (!drone) return null;
  const geo = getDroneLatLon(drone);
  if (!geo || geo.lat == null || geo.lon == null) return null;
  let best = null;
  for (let i = 1; i < missionWaypoints.length; i++) {
    const a = missionWaypoints[i - 1];
    const b = missionWaypoints[i];
    const bx = signedMeters(a.lat, a.lon, a.lat, b.lon);
    const by = signedMeters(a.lat, a.lon, b.lat, a.lon);
    const px = signedMeters(a.lat, a.lon, a.lat, geo.lon);
    const py = signedMeters(a.lat, a.lon, geo.lat, a.lon);
    const segLen2 = bx * bx + by * by;
    let t = segLen2 > 0 ? (px * bx + py * by) / segLen2 : 0;
    t = Math.max(0, Math.min(1, t));
    const d = Math.hypot(px - t * bx, py - t * by);
    if (!best || d < best.d) {
      best = { d, dist: points[i - 1].dist + t * Math.sqrt(segLen2) };
    }
  }
  if (!best || best.d > 500) return null;
  return {
    dist: best.dist,
    alt: Math.max(0, Math.abs(Number(drone.position_ned?.z || 0))),
  };
}

// 遥测高频更新时合并重绘，避免一帧内多次画剖面
function scheduleProfileRedraw() {
  if (profileRedrawScheduled) return;
  profileRedrawScheduled = true;
  requestAnimationFrame(() => {
    profileRedrawScheduled = false;
    drawMissionProfile();
  });
}

// 命中测试：找离指针最近的航点（含高度），超出阈值返回 null
function profileHitTest(clientX, clientY) {
  const canvas = els.profileCanvas;
  const view = missionProfileView;
  if (!canvas || !view || !view.points.length) return null;
  const rect = canvas.getBoundingClientRect();
  const mx = clientX - rect.left;
  const my = clientY - rect.top;
  let best = null;
  let bestDist = Infinity;
  for (const p of view.points) {
    const d = Math.hypot(p.x - mx, p.y - my);
    if (d < bestDist) {
      bestDist = d;
      best = p;
    }
  }
  return bestDist <= PROFILE_HIT_RADIUS_PX ? best : null;
}

// 按指针纵向位移换算高度：使用按下时冻结的米/像素比，避免坐标轴自适应导致跳变
function updateProfileDragAltitude(clientY) {
  const drag = missionProfileDrag;
  if (!drag) return;
  const wp = missionWaypoints[drag.index];
  if (!wp) return;
  const metersPerPx = drag.altRange / Math.max(1, drag.chartH);
  const alt = drag.startAlt - (clientY - drag.startY) * metersPerPx;
  wp.alt_m = Math.round(Math.max(0, Math.min(PROFILE_ALT_MAX_M, alt)) * 10) / 10;
  if (!isPx4MavlinkBackend()) {
    const ned = gpsToNed(wp.lat, wp.lon, -wp.alt_m);
    wp.x = round1(ned.x);
    wp.y = round1(ned.y);
    wp.z = round1(ned.z);
  }
  if (els.wpPropAlt) els.wpPropAlt.value = wp.alt_m;
  drawMissionProfile();
}

function setupMissionProfileInteraction() {
  const canvas = els.profileCanvas;
  if (!canvas || canvas.dataset.profileDragBound === "true") return;
  canvas.dataset.profileDragBound = "true";
  canvas.style.touchAction = "none";

  canvas.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    const hit = profileHitTest(event.clientX, event.clientY);
    const view = missionProfileView;
    if (!hit || !view) return;
    event.preventDefault();
    selectedWaypointIndex = hit.index;
    missionProfileHover = null;
    missionProfileDrag = {
      pointerId: event.pointerId,
      index: hit.index,
      startY: event.clientY,
      startAlt: Number(missionWaypoints[hit.index]?.alt_m || 0),
      minAlt: view.minAlt,
      altRange: view.altRange,
      chartH: view.chartH,
    };
    try { canvas.setPointerCapture(event.pointerId); } catch (_) {}
    canvas.style.cursor = "ns-resize";
    updateProfileDragAltitude(event.clientY);
  });

  canvas.addEventListener("pointermove", (event) => {
    if (missionProfileDrag && missionProfileDrag.pointerId === event.pointerId) {
      updateProfileDragAltitude(event.clientY);
      return;
    }
    const hit = profileHitTest(event.clientX, event.clientY);
    canvas.style.cursor = hit ? "ns-resize" : "default";
    const nextHover = hit ? hit.index : null;
    if (nextHover !== missionProfileHover) {
      missionProfileHover = nextHover;
      drawMissionProfile();
    }
  });

  canvas.addEventListener("pointerleave", () => {
    if (missionProfileDrag || missionProfileHover == null) return;
    missionProfileHover = null;
    canvas.style.cursor = "default";
    drawMissionProfile();
  });

  const finishDrag = (event) => {
    if (!missionProfileDrag || missionProfileDrag.pointerId !== event.pointerId) return;
    const index = missionProfileDrag.index;
    missionProfileDrag = null;
    try { canvas.releasePointerCapture(event.pointerId); } catch (_) {}
    canvas.style.cursor = "default";
    markMissionEdited();
    renderWaypoints();
    drawMissionPath();
    showWaypointProperties(index);
  };
  canvas.addEventListener("pointerup", finishDrag);
  canvas.addEventListener("pointercancel", finishDrag);
}

function getVisibleMapDistanceMeters() {
  if (!maplibreMap) return 1000;
  try {
    const bounds = maplibreMap.getBounds();
    const centerLat = bounds.getCenter().lat;
    const dLon = (bounds.getEast() - bounds.getWest()) * Math.PI / 180;
    return Math.max(10, Math.abs(dLon) * EARTH_RADIUS_M * Math.cos(centerLat * Math.PI / 180));
  } catch (err) {
    return 1000;
  }
}

function drawMapTexture(ctx, width, height) {
  const gradient = ctx.createLinearGradient(0, 0, width, height);
  gradient.addColorStop(0, "#05070b");
  gradient.addColorStop(1, "#10131b");
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, width, height);

  ctx.save();
  ctx.translate(width * 0.5, height * 0.5);
  ctx.rotate(-0.42);
  ctx.translate(-width * 0.5, -height * 0.5);

  ctx.strokeStyle = "rgba(255,255,255,0.055)";
  ctx.lineWidth = 2;
  for (let x = -width; x < width * 2; x += 54) {
    line(ctx, x, -height, x + height * 0.35, height * 2);
  }
  ctx.strokeStyle = "rgba(255,255,255,0.08)";
  for (let y = -height; y < height * 2; y += 72) {
    line(ctx, -width, y, width * 2, y + width * 0.1);
  }

  ctx.strokeStyle = "rgba(85, 223, 244, 0.10)";
  ctx.lineWidth = 1;
  for (let x = -width; x < width * 2; x += 135) {
    line(ctx, x, -height, x + height * 0.28, height * 2);
  }
  ctx.restore();
}

function drawGeofence(ctx, cx, cy, scale) {
  ctx.strokeStyle = "rgba(237,244,255,0.16)";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.arc(cx, cy, 100 * scale, 0, Math.PI * 2);
  ctx.stroke();

  ctx.strokeStyle = "rgba(85,223,244,0.55)";
  ctx.lineWidth = 1;
  line(ctx, cx - 16, cy, cx + 16, cy);
  line(ctx, cx, cy - 16, cx, cy + 16);

  ctx.fillStyle = "rgba(85,223,244,0.75)";
  ctx.font = "12px Segoe UI, sans-serif";
  ctx.fillText("HOME", cx + 10, cy - 10);
}

function drawPath(ctx, points, cx, cy, scale, color, numbered) {
  if (!points.length) return;

  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  ctx.lineWidth = 2;
  ctx.setLineDash([7, 8]);
  ctx.beginPath();
  points.forEach((pt, index) => {
    const px = cx + Number(pt.y || 0) * scale;
    const py = cy - Number(pt.x || 0) * scale;
    if (index === 0) ctx.moveTo(px, py);
    else ctx.lineTo(px, py);
  });
  if (points.length > 2) {
    const first = points[0];
    ctx.lineTo(cx + Number(first.y || 0) * scale, cy - Number(first.x || 0) * scale);
  }
  ctx.stroke();
  ctx.setLineDash([]);

  points.forEach((pt, index) => {
    const px = cx + Number(pt.y || 0) * scale;
    const py = cy - Number(pt.x || 0) * scale;
    ctx.beginPath();
    ctx.arc(px, py, 14, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(6, 18, 26, 0.92)";
    ctx.fill();
    ctx.lineWidth = 2;
    ctx.strokeStyle = color;
    ctx.stroke();
    if (numbered) {
      ctx.fillStyle = "#eaffff";
      ctx.font = "bold 12px Segoe UI, sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(String(index + 1), px, py);
      ctx.textAlign = "left";
      ctx.textBaseline = "alphabetic";
    }
  });
}

function drawDrone(ctx, px, py, heading) {
  ctx.save();
  ctx.translate(px, py);
  ctx.rotate(heading);
  ctx.shadowColor = "rgba(85, 223, 244, 0.72)";
  ctx.shadowBlur = 18;
  ctx.fillStyle = "#a78bfa";
  ctx.strokeStyle = "#55dff4";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(0, -20);
  ctx.lineTo(14, 16);
  ctx.lineTo(0, 8);
  ctx.lineTo(-14, 16);
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = "#4ee6a4";
  ctx.fillRect(-4, -14, 8, 24);
  ctx.restore();
}

function extractPlanWaypoints(run) {
  if (!run || !run.plan) return [];
  const points = [];
  for (const step of run.plan.steps || []) {
    if (step.tool === "drone_fly_to") {
      points.push({ x: Number(step.params.x || 0), y: Number(step.params.y || 0), z: Number(step.params.z || -3) });
    }
    if (step.tool === "drone_fly_path" && step.params.waypoints_json) {
      try {
        JSON.parse(step.params.waypoints_json).forEach((wp) => {
          points.push({ x: Number(wp.x || 0), y: Number(wp.y || 0), z: Number(wp.z || -3) });
        });
      } catch (_) {
        return points;
      }
    }
  }
  return points;
}

function initSplitters() {
  // 初始 / 窗口尺寸变化时同步输入区密度（窄面板收缩文字而非换行）
  window.addEventListener("resize", () => syncComposerDensity());
  document.querySelectorAll("[data-splitter]").forEach((splitter) => {
    splitter.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      const kind = splitter.dataset.splitter;
      const shellRect = els.appShell.getBoundingClientRect();
      const mapColumn = document.querySelector(".map-column");
      const mapRect = mapColumn.getBoundingClientRect();
      document.body.classList.add("resizing");
      splitter.setPointerCapture(event.pointerId);

      const move = (moveEvent) => {
        if (kind === "left") {
          const next = clamp(moveEvent.clientX - shellRect.left - 12, 250, 560);
          document.documentElement.style.setProperty("--left-pane", `${next}px`);
          saveLayoutPref("left", Math.round(next));
        } else if (kind === "right") {
          // 下限放宽到 320px（窄面板也保留最小可读宽度），上限 760px
          const next = clamp(shellRect.right - moveEvent.clientX - 12, 320, 760);
          document.documentElement.style.setProperty("--right-pane", `${next}px`);
          saveLayoutPref("right", Math.round(next));
          syncComposerDensity();
        } else if (kind === "timeline") {
          const next = clamp(mapRect.bottom - moveEvent.clientY, 130, 380);
          document.documentElement.style.setProperty("--timeline-height", `${next}px`);
          saveLayoutPref("timeline", Math.round(next));
        }
        if (maplibreMap) maplibreMap.resize();
      };

      const up = () => {
        document.body.classList.remove("resizing");
        splitter.releasePointerCapture(event.pointerId);
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
      };

      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
    });
  });
}

function currentDronePosition() {
  const drone = latestState?.tool_runtime?.drone || {};
  const pos = drone.position_ned || {};
  return {
    x: Number(pos.x || 0),
    y: Number(pos.y || 0),
    z: Number(pos.z || 0),
  };
}

function renderLoopTrace(loopState) {
  const decisions = Array.isArray(loopState?.decisions) ? loopState.decisions : [];
  const results = Array.isArray(loopState?.results) ? loopState.results : [];
  if (!decisions.length && !results.length) return "";
  const rows = decisions.map((decision, index) => {
    if (!decision.action || decision.action === "memory_store") return "";
    const result = results.find((item) => Number(item.step_index || 0) === index + 1);
    const action = decision.action || "complete";
    const state = decision.is_complete ? "completed" : result ? (result.ok ? "completed" : "failed") : "planned";
    const subTools = renderSkillSubTools(result);
    const reason = humanDecisionReason(decision.reason || result?.data?.message || "", action);
    return `
      <article class="loop-row ${state}">
        <div class="loop-head">
          <span class="step-state ${state}">${index + 1}</span>
          <strong>${escapeHtml(humanToolLabel(action))}</strong>
          <small>${escapeHtml(reason)}</small>
        </div>
        ${decision.reflection ? `<p>${escapeHtml(decision.reflection)}</p>` : ""}
        ${subTools}
      </article>
    `;
  }).filter(Boolean).join("");
  if (!rows) return "";
  const status = loopState.status ? ` · ${loopState.status}` : "";
  return `
    <div class="detail-note loop-note">
      <strong>执行过程${escapeHtml(status)}</strong>
      <div class="loop-trace">${rows}</div>
    </div>
  `;
}

function hasReliableVehicleMapPosition(drone = {}, runtime = latestState?.tool_runtime || {}) {
  if (drone.map_position_valid === true) return true;
  if (isRealVehicleRuntime(runtime)) {
    if (drone.map_position_valid === false) return false;
    const gps = drone.gps || {};
    const fixType = Number(drone.gps_fix_type || 0);
    const accuracy = Number(drone.gps_horizontal_accuracy_m);
    const accuracyGood = !Number.isFinite(accuracy) || accuracy <= 50;
    return Boolean(gps.lat && gps.lon && Math.abs(Number(gps.lat)) > 0.001 && fixType >= 3 && accuracyGood);
  }
  return true;
}

function setupRows(rows) {
  return `
    <dl class="vehicle-info-grid">
      ${rows.map(([label, value]) => `<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(valueText(value))}</dd>`).join("")}
    </dl>
  `;
}

function drawMiniHistoryCanvas(canvasId, entries, fields) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(320, Math.floor(rect.width || 420));
  const height = Math.max(120, Math.floor(rect.height || 150));
  const dpr = window.devicePixelRatio || 1;
  if (canvas.width !== Math.floor(width * dpr) || canvas.height !== Math.floor(height * dpr)) {
    canvas.width = Math.floor(width * dpr);
    canvas.height = Math.floor(height * dpr);
  }
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#101219";
  ctx.fillRect(0, 0, width, height);
  const plot = { x: 28, y: 14, w: width - 40, h: height - 26 };
  const latest = Number(entries.at(-1)?.sec || 0);
  const start = Math.max(0, latest - 8);
  const series = fields.map((field) => ({
    ...field,
    points: entries
      .filter((entry) => Number(entry.sec || 0) >= start)
      .map((entry) => ({ x: Number(entry.sec || 0) - latest, y: Number(entry[field.field]) }))
      .filter((point) => Number.isFinite(point.x) && Number.isFinite(point.y)),
  }));
  const values = series.flatMap((item) => item.points.map((point) => point.y));
  let [minY, maxY] = robustChartRange(values, [-1, 1]);
  const pad = Math.max(0.1, (maxY - minY) * 0.1);
  minY -= pad;
  maxY += pad;
  ctx.strokeStyle = "rgba(255,255,255,0.08)";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i += 1) {
    const y = plot.y + (plot.h * i) / 4;
    ctx.beginPath();
    ctx.moveTo(plot.x, y);
    ctx.lineTo(plot.x + plot.w, y);
    ctx.stroke();
  }
  const xFor = (x) => plot.x + ((x + 8) / 8) * plot.w;
  const yFor = (y) => plot.y + plot.h - ((y - minY) / (maxY - minY)) * plot.h;
  series.forEach((item) => {
    if (!item.points.length) return;
    ctx.strokeStyle = item.color;
    ctx.lineWidth = 1.8;
    ctx.beginPath();
    item.points.forEach((point, index) => {
      const x = xFor(point.x);
      const y = yFor(point.y);
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  });
  ctx.fillStyle = "rgba(210,218,235,0.62)";
  ctx.font = "11px Inter, Segoe UI, sans-serif";
  ctx.textAlign = "right";
  ctx.fillText(maxY.toFixed(1), plot.x - 5, plot.y + 4);
  ctx.fillText(minY.toFixed(1), plot.x - 5, plot.y + plot.h);
  ctx.textAlign = "left";
  fields.forEach((field, index) => {
    ctx.fillStyle = field.color;
    ctx.fillText(field.label, plot.x + 26 * index, 10);
  });
}

function drawPidTuningChart() {
  const canvas = document.getElementById("vehiclePidCanvas");
  if (!canvas) return;
  const setup = setupSnapshot();
  const history = setup.history || {};
  const config = pidChartConfig(activePidTuningView);
  const rawPoints = Array.isArray(history[config.history]) ? history[config.history] : [];
  const lastSec = Number(rawPoints.at(-1)?.sec || 0);
  const windowSec = config.windowSec || 8;
  const startSec = Math.max(0, lastSec - windowSec);
  const points = rawPoints.filter((point) => Number(point.sec || 0) >= startSec);
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(640, Math.floor(rect.width || 800));
  const height = Math.max(320, Math.floor(rect.height || 420));
  const dpr = window.devicePixelRatio || 1;
  const targetWidth = Math.floor(width * dpr);
  const targetHeight = Math.floor(height * dpr);
  if (canvas.width !== targetWidth || canvas.height !== targetHeight) {
    canvas.width = targetWidth;
    canvas.height = targetHeight;
  }
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#f7f8fb";
  ctx.fillRect(0, 0, width, height);
  const plot = { x: 58, y: 34, w: width - 82, h: height - 86 };
  ctx.strokeStyle = "#dfe3eb";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 6; i += 1) {
    const x = plot.x + (plot.w * i) / 6;
    ctx.beginPath();
    ctx.moveTo(x, plot.y);
    ctx.lineTo(x, plot.y + plot.h);
    ctx.stroke();
  }
  for (let i = 0; i <= 6; i += 1) {
    const y = plot.y + (plot.h * i) / 6;
    ctx.beginPath();
    ctx.moveTo(plot.x, y);
    ctx.lineTo(plot.x + plot.w, y);
    ctx.stroke();
  }
  const response = decimateChartSeries(points.map((p) => ({ x: Number(p.sec || 0) - startSec, y: Number(p[config.response]) })).filter((p) => Number.isFinite(p.y)), plot.w * 1.5);
  const setpoint = config.setpoint ? decimateChartSeries(points.map((p) => ({ x: Number(p.sec || 0) - startSec, y: Number(p[config.setpoint]) })).filter((p) => Number.isFinite(p.y)), plot.w * 1.5) : [];
  const all = [...response, ...setpoint];
  const maxX = Math.max(3, windowSec, ...all.map((p) => p.x));
  const range = robustChartRange(all.map((p) => p.y), config.defaultRange || [-1, 1]);
  let minY = range[0];
  let maxY = range[1];
  const padY = Math.max(1, (maxY - minY) * 0.12);
  minY -= padY;
  maxY += padY;
  const xFor = (x) => plot.x + (x / maxX) * plot.w;
  const yFor = (y) => plot.y + plot.h - ((y - minY) / (maxY - minY)) * plot.h;
  const drawSeries = (series, color) => {
    if (!series.length) return;
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    series.forEach((point, index) => {
      const x = xFor(point.x);
      const y = yFor(point.y);
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  };
  ctx.save();
  ctx.beginPath();
  ctx.rect(plot.x, plot.y, plot.w, plot.h);
  ctx.clip();
  drawSeries(response, "#20a7e2");
  drawSeries(setpoint, "#8bc34a");
  ctx.restore();
  ctx.strokeStyle = "#aeb5c2";
  ctx.strokeRect(plot.x, plot.y, plot.w, plot.h);
  ctx.fillStyle = "#343944";
  ctx.font = "13px Inter, Segoe UI, sans-serif";
  ctx.textAlign = "center";
  ctx.fillText(config.title, plot.x + plot.w / 2, 20);
  ctx.textAlign = "right";
  ctx.fillText(maxY.toFixed(1), plot.x - 8, plot.y + 5);
  ctx.fillText(minY.toFixed(1), plot.x - 8, plot.y + plot.h);
  ctx.save();
  ctx.translate(16, plot.y + plot.h / 2);
  ctx.rotate(-Math.PI / 2);
  ctx.textAlign = "center";
  ctx.fillText(config.unit, 0, 0);
  ctx.restore();
  ctx.textAlign = "center";
  ctx.fillText("sec", plot.x + plot.w / 2, height - 18);
  if (!response.length) {
    ctx.fillStyle = "#697184";
    ctx.fillText("等待实时 MAVLink 遥测...", plot.x + plot.w / 2, plot.y + plot.h / 2);
  }
}

async function uploadMissionToVehicle() {
  const runtime = requireLiveFlightLink();
  if (!missionWaypoints.length) {
    throw new Error("No waypoints are available to upload.");
  }
  if (runtime.operation_contract?.vehicle_kind === "real_px4"
      && applicationSettings.safety.require_gps_for_global_mission
      && !runtime.operation_contract?.global_mission_ready) {
    throw new Error("真实 PX4 的 GPS 位置尚不可靠，任务未上传");
  }
  const draft = buildMissionDraftFromItems(buildLocalMissionItems());
  return await post("/api/gcs/mission/upload", { draft, expected_backend: runtime.backend });
}

async function startVehicleMission() {
  const runtime = requireLiveFlightLink();
  const payload = { expected_backend: runtime.backend };
  if (missionWaypoints.length) {
    payload.draft = buildMissionDraftFromItems(buildLocalMissionItems());
  }
  return await post("/api/gcs/mission/start", payload);
}

async function downloadVehicleMission() {
  const runtime = requireLiveFlightLink();
  const result = await post("/api/gcs/mission/download", { expected_backend: runtime.backend });
  const draft = result?.draft;
  const items = draft?.items || [];
  if (Array.isArray(items) && items.length) {
    missionWaypoints = items.map((item, idx) => ({
      id: item.id || `wp_${String(idx + 1).padStart(3, "0")}`,
      type: item.type || "waypoint",
      frame: item.frame || (isPx4MavlinkBackend() ? "global_relative_alt" : "local_ned"),
      lat: item.lat != null ? round6(Number(item.lat)) : null,
      lon: item.lon != null ? round6(Number(item.lon)) : null,
      alt_m: Number(item.alt_m || Math.abs(Number(item.z || 3))),
      x: item.x != null ? Number(item.x) : null,
      y: item.y != null ? Number(item.y) : null,
      z: item.z != null ? Number(item.z) : null,
      speed_mps: Number(item.speed_mps || 2),
      hold_s: Number(item.hold_s || 0),
      acceptance_radius_m: Number(item.acceptance_radius_m || 2),
      actions: Array.isArray(item.actions) ? item.actions : [],
      metadata: item.metadata || { source: "vehicle_download" },
    }));
    markMissionEdited();
    renderWaypoints();
    drawMissionPath();
  }
  return result;
}

async function clearVehicleMission() {
  const runtime = requireLiveFlightLink();
  const result = await post("/api/gcs/mission/clear", { expected_backend: runtime.backend });
  markMissionEdited();
  return result;
}

async function refreshMissionProgress() {
  const result = await post("/api/gcs/mission/progress", {});
  lastMissionProgress = result?.progress || result || {};
  return lastMissionProgress;
}

function waypointExecutionMessage() {
  return "Mission uploaded and started through the active backend.";
}

function syncWaypointActionLabels() {
  const deployBtn = document.querySelector("[data-waypoint-action='deploy_start'], [data-waypoint-action='upload']");
  if (deployBtn) {
    deployBtn.title = "上传并开始执行航线";
  }
  const startBtn = document.querySelector("[data-waypoint-action='start']");
  if (startBtn) {
    startBtn.style.display = "none";
  }
  const downloadBtn = document.querySelector("[data-waypoint-action='download']");
  if (downloadBtn) {
    downloadBtn.style.display = "none";
  }
  const progressBtn = document.querySelector("[data-waypoint-action='progress']");
  if (progressBtn) {
    progressBtn.style.display = "";
  }
  const clearVehicleBtn = document.querySelector("[data-waypoint-action='clear_vehicle']");
  if (clearVehicleBtn) {
    clearVehicleBtn.style.display = "";
    clearVehicleBtn.title = "删除本地航点并清空飞控任务";
  }
}

async function handleWaypointAction(action, button) {
  if (action === "clear") {
    missionWaypoints = [];
    missionFence = [];
    selectedWaypointIndex = -1;
    renderWaypoints();
    drawMissionPath();
    drawFence();
    showNotice("Local waypoints and fence cleared.", "success");
    return;
  }

  if (action === "fence") {
    fenceDrawingMode = !fenceDrawingMode;
    if (button) {
      button.classList.toggle("active", fenceDrawingMode);
      button.textContent = fenceDrawingMode ? "Finish fence" : "Fence";
    }
    if (fenceDrawingMode) {
      missionFence = [];
      drawFence();
      showNotice("Fence drawing enabled. Click the map to add vertices.", "success");
    } else if (missionFence.length < 3) {
      missionFence = [];
      drawFence();
      showNotice("Fence needs at least three vertices; discarded.", "error");
    } else {
      showNotice(`Fence saved with ${missionFence.length} vertices.`, "success");
    }
    return;
  }

  if (action === "upload") {
    if (!missionWaypoints.length) {
      showNotice("Add waypoints first.", "error");
      return;
    }
    if (isPx4MavlinkBackend()) {
      const ok = await confirmDialog({
        title: "Upload Mission",
        message: "This replaces the current PX4 mission but does not start it. Continue?",
        confirmLabel: "Upload",
        danger: true,
      });
      if (!ok) return;
    }
    await runButton(button, uploadMissionToVehicle, "Mission staged. Use Start to execute it.");
    return;
  }

  if (action === "deploy_start") {
    if (!missionWaypoints.length && !Object.keys(missionPlans).length) {
      showNotice("请先添加航点", "error");
      return;
    }
    // 多机模式的确认框（含各机航点汇总）在 deployAndStartMission 内弹出
    const multiDispatch = isMultiVehiclePlanning() && currentMissionVehicleName();
    if (!multiDispatch) {
      const ok = await confirmDialog({
        title: "上传并开始航线",
        message: "此操作会替换飞控上的当前 mission，并立即开始执行。是否继续？",
        confirmLabel: "上传并开始",
        danger: true,
      });
      if (!ok) return;
    }
    // 单机模式 drone_fly_path 阻塞到飞完才返回 → 提示"执行完成";
    // 多机为非阻塞派发 → 提示"已派发",结束时的提示由航线完成监控给出
    await runButton(button, deployAndStartMission, multiDispatch ? "多机航线已派发，各机执行各自航线" : "航线执行完成");
    return;
  }

  if (action === "start") {
    const ok = await confirmDialog({
      title: "Start Mission",
      message: "This will execute the staged mission through the active backend. Continue?",
      confirmLabel: "Start",
      danger: true,
    });
    if (!ok) return;
    await runButton(button, async () => {
      markMissionExecutionStarted();
      try {
        return await startVehicleMission();
      } catch (error) {
        markMissionEdited();
        throw error;
      }
    }, "Mission start command sent.");
    return;
  }

  if (action === "download") {
    await runButton(button, downloadVehicleMission, "Mission loaded into the map.");
    return;
  }

  if (action === "progress") {
    await runButton(button, refreshMissionProgress, "Mission progress refreshed.");
    return;
  }

  if (action === "clear_vehicle") {
    const ok = await confirmDialog({
      title: "删除航点任务",
      message: "将删除本地航点，并清空飞控中已部署的 mission。是否继续？",
      confirmLabel: "删除",
      danger: true,
    });
    if (!ok) return;
    await runButton(button, clearMissionEverywhere, "本地航点与飞控任务已清空");
  }
}

// Initial render with defaults so the page is never blank
function renderInitialDefaults() {
  renderChat([], null, {});
  if (latestState) updateMapView(latestState);
}
