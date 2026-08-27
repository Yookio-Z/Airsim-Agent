/* // 任务上传/下载/启动与航点动作分发（handleWaypointAction） */

function extractApiSuccess(data, fallback = "ok") {
  if (data?.ok === false || data?.result?.ok === false) {
    throw new Error(extractApiError(data, "command failed"));
  }
  const candidates = [
    data?.message,
    data?.result?.message,
    data?.result?.data?.message,
    data?.progress?.message,
  ];
  for (const item of candidates) {
    if (typeof item === "string" && item.trim()) return item;
  }
  return fallback;
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

normalizeAgentSettingsCopy();
normalizeSystemSettingsCopy();
