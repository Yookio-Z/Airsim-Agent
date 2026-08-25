const $ = (id) => document.getElementById(id);

const els = {
  appShell: $("appShell"),
  agentColumn: $("agentColumn"),
  connectionDot: $("connectionDot"),
  connectionText: $("connectionText"),
  vehicleState: $("vehicleState"),
  vehicleList: $("vehicleList"),
  plannerBadge: $("plannerBadge"),
  commandForm: $("commandForm"),
  commandInput: $("commandInput"),
  imageInput: $("imageInput"),
  imagePreview: $("imagePreview"),
  attachImageBtn: $("attachImageBtn"),
  chatModeBtn: $("chatModeBtn"),
  executeModeBtn: $("executeModeBtn"),
  modelSelector: $("modelSelector"),
  modelSelectorBtn: $("modelSelectorBtn"),
  modelSelectorLabel: $("modelSelectorLabel"),
  modelSelectorMenu: $("modelSelectorMenu"),
  contextUsage: $("contextUsage"),
  newSessionBtn: $("newSessionBtn"),
  sessionNavBtn: $("sessionNavBtn"),
  sessionsPanel: $("sessionsPanel"),
  sessionsList: $("sessionsList"),
  currentSessionLabel: $("currentSessionLabel"),
  notice: $("notice"),
  metricAltitude: $("metricAltitude"),
  metricPosition: $("metricPosition"),
  metricVelocity: $("metricVelocity"),
  metricHeading: $("metricHeading"),
  metricBattery: $("metricBattery"),
  metricFlight: $("metricFlight"),
  metricWaypoint: $("metricWaypoint"),
  metricAltDiff: $("metricAltDiff"),
  metricBearing: $("metricBearing"),
  metricDistPrev: $("metricDistPrev"),
  metricMaxRange: $("metricMaxRange"),
  metricTilt: $("metricTilt"),
  runProgress: $("runProgress"),
  planSummary: $("planSummary"),
  taskRunList: $("taskRunList"),
  eventList: $("eventList"),
  toolList: $("toolList"),
  toolCount: $("toolCount"),
  memoryList: $("memoryList"),
  memoryCount: $("memoryCount"),
  skillList: $("skillList"),
  skillCount: $("skillCount"),
  waypointList: $("waypointList"),
  waypointProperties: $("waypointProperties"),
  wpPropType: $("wpPropType"),
  wpPropAlt: $("wpPropAlt"),
  wpPropSpeed: $("wpPropSpeed"),
  wpPropHold: $("wpPropHold"),
  wpPropAccept: $("wpPropAccept"),
  chatThread: $("chatThread"),
  canvas: null,
  missionMap: $("missionMap"),
  mapStatus: $("mapStatus"),
  operationChannel: $("operationChannel"),
  canvasScale: $("canvasScale"),
  profileCanvas: $("profileCanvas"),
  profileToggle: $("profileToggle"),
  missionProfile: $("missionProfile"),
  cameraViewBtn: $("cameraViewBtn"),
  cameraViewer: $("cameraViewer"),
  cameraViewerDragHandle: $("cameraViewerDragHandle"),
  cameraViewerNewWindow: $("cameraViewerNewWindow"),
  cameraViewerClose: $("cameraViewerClose"),
  cameraViewerCamera: $("cameraViewerCamera"),
  cameraViewerImageType: $("cameraViewerImageType"),
  cameraLiveIndicator: $("cameraLiveIndicator"),
  cameraSnapshotStatus: $("cameraSnapshotStatus"),
  cameraImage: $("cameraImage"),
  cameraPlaceholder: $("cameraPlaceholder"),
  cameraMeta: $("cameraMeta"),
  agentSettingsDrawer: $("agentSettingsDrawer"),
  systemSettingsModal: $("systemSettingsModal"),
  settingsBackdrop: $("settingsBackdrop"),
  settingsOpen: $("settingsOpen"),
  agentSettingsClose: $("agentSettingsClose"),
  systemSettingsClose: $("systemSettingsClose"),
  systemSettingsMaximize: $("systemSettingsMaximize"),
  systemSettingsNav: $("systemSettingsNav"),
  vehicleSettingsSource: $("vehicleSettingsSource"),
  mapSettingsBtn: $("mapSettingsBtn"),
  addConnectionBtn: $("addConnectionBtn"),
  connectionsList: $("connectionsList"),
  connectionDetailForm: $("connectionDetailForm"),
  connectionDetailId: $("connectionDetailId"),
  connectionDetailName: $("connectionDetailName"),
  connectionDetailType: $("connectionDetailType"),
  connectionDetailPort: $("connectionDetailPort"),
  connectionDetailHost: $("connectionDetailHost"),
  connectionDetailAddress: $("connectionDetailAddress"),
  connectionDetailPortNumber: $("connectionDetailPortNumber"),
  connectionDetailRemotePort: $("connectionDetailRemotePort"),
  connectionDetailRealVehicle: $("connectionDetailRealVehicle"),
  connectionDetailConnect: $("connectionDetailConnect"),
  connectionDetailDelete: $("connectionDetailDelete"),
  connectionDetailCancel: $("connectionDetailCancel"),
  connectionDetailStatus: $("connectionDetailStatus"),
  connectionActualLink: $("connectionActualLink"),
  vehicleInfoPanel: $("vehicleInfoPanel"),
  vehicleAirframePanel: $("vehicleAirframePanel"),
  vehicleSensorsPanel: $("vehicleSensorsPanel"),
  vehicleRadioPanel: $("vehicleRadioPanel"),
  vehicleFlightModesPanel: $("vehicleFlightModesPanel"),
  vehiclePowerPanel: $("vehiclePowerPanel"),
  vehicleActuatorsPanel: $("vehicleActuatorsPanel"),
  vehicleSafetyPanel: $("vehicleSafetyPanel"),
  vehiclePidPanel: $("vehiclePidPanel"),
  vehicleWaveformPanel: $("vehicleWaveformPanel"),
  vehicleFlightBehaviorPanel: $("vehicleFlightBehaviorPanel"),
  vehicleFirmwarePanel: $("vehicleFirmwarePanel"),
  detectedMavlinkLinks: $("detectedMavlinkLinks"),
  detectedMavlinkLinksCount: $("detectedMavlinkLinksCount"),
  refreshFirmwareInfoBtn: $("refreshFirmwareInfoBtn"),
  refreshVehicleParametersBtn: $("refreshVehicleParametersBtn"),
  vehicleParameterSearch: $("vehicleParameterSearch"),
  vehicleParameterSummary: $("vehicleParameterSummary"),
  vehicleParametersPanel: $("vehicleParametersPanel"),
  cameraSource: $("cameraSource"),
  cameraRtspUrlRow: $("cameraRtspUrlRow"),
  cameraRtspUrl: $("cameraRtspUrl"),
  cameraName: $("cameraName"),
  cameraVehicle: $("cameraVehicle"),
  cameraImageType: $("cameraImageType"),
  cameraTimeout: $("cameraTimeout"),
  cameraAutoSave: $("cameraAutoSave"),
  cameraSaveSettingsBtn: $("cameraSaveSettingsBtn"),
  cameraCaptureFromSettingsBtn: $("cameraCaptureFromSettingsBtn"),
  appLanguage: $("appLanguage"),
  appTheme: $("appTheme"),
  appDensity: $("appDensity"),
  appMapLayer: $("appMapLayer"),
  appTelemetryRefresh: $("appTelemetryRefresh"),
  appSetupRefresh: $("appSetupRefresh"),
  appHistorySeconds: $("appHistorySeconds"),
  appFollowVehicle: $("appFollowVehicle"),
  appShowTrack: $("appShowTrack"),
  appRequireGps: $("appRequireGps"),
  appConfirmRealVehicle: $("appConfirmRealVehicle"),
  appRequireMissionGps: $("appRequireMissionGps"),
  appShowContext: $("appShowContext"),
  appAutoMultimodal: $("appAutoMultimodal"),
  appMaxMapJump: $("appMaxMapJump"),
  addModelBtn: $("addModelBtn"),
  modelModal: $("modelModal"),
  modelModalTitle: $("modelModalTitle"),
  modelModalClose: $("modelModalClose"),
  modelModalCancel: $("modelModalCancel"),
  modelForm: $("modelForm"),
  skillModal: $("skillModal"),
  skillModalTitle: $("skillModalTitle"),
  skillModalSubtitle: $("skillModalSubtitle"),
  skillModalClose: $("skillModalClose"),
  skillForm: $("skillForm"),
  addSkillBtn: $("addSkillBtn"),
  importSkillBtn: $("importSkillBtn"),
  skillImportInput: $("skillImportInput"),
  modelEditId: $("modelEditId"),
  modelName: $("modelName"),
  modelProvider: $("modelProvider"),
  modelModelId: $("modelModelId"),
  modelApiType: $("modelApiType"),
  modelBaseUrl: $("modelBaseUrl"),
  modelApiKey: $("modelApiKey"),
  modelRevealKey: $("modelRevealKey"),
  modelReasoningEffort: $("modelReasoningEffort"),
  modelThinkingMode: $("modelThinkingMode"),
};

let latestState = null;
let fullSessionMessageCache = new Map();
let sessionHistoryLoading = new Set();
let commandMode = localStorage.getItem("airsim-agent-command-mode") || "chat";
// missionWaypoints 现在是 backend-neutral MissionItem 结构:
// { id, type: "waypoint"|"takeoff"|"land"|"rtl", lat, lon, alt_m, speed_mps, hold_s, acceptance_radius_m, actions, metadata }
// AirSim 后端下也会维护 x/y/z (local NED) 字段，用于向后兼容。
let missionWaypoints = [];
// 多机航线：missionPlans 保存"非当前目标机"的航线，键为载具名；
// 当前正在编辑的航线始终放在 missionWaypoints，切换目标机时互换。
let missionPlans = {};
// 当前规划目标机（"" = 未选择/单机默认）。由左上角无人机 chips 点击切换。
let missionTargetVehicle = "";
let missionFence = [];
let mapZoom = 1;
let noticeTimer = null;
let mapTransform = { cx: 0, cy: 0, scale: 1 };
let maxTelemetryDistanceM = 0;
const openDetailIds = new Set();
const completedAutoFoldDetailIds = new Set();
let streamSource = null;
let streamReconnectTimer = null;
let rosTelemetrySource = null;
let rosTelemetryUrl = "";
let rosTelemetryReconnectTimer = null;
let rosTelemetryConnected = false;
let forceNextChatScroll = false;
let chatRenderRafId = 0;
let pendingImages = [];
let localPendingMessages = [];
let pendingMessageCounter = 0;
let pendingScrollTargetId = "";
let returnHomeGps = null;
let droneTrackCoords = [];
let droneTrackActive = false;
let droneTrackLastAzimuth = null;
let mapCenteredOnFirstVehicle = false;
let telemetryRefreshInFlight = false;
let telemetryRefreshTimer = null;
let activeTargetRouteKey = "";
let activeTargetIndex = 0;
let missionExecutionActive = false;
let droneAnimationFrame = null;
let droneAnimationFrom = null;
let droneAnimationTo = null;
let droneAnimationHeadingFrom = 0;
let droneAnimationHeadingTo = 0;
let droneAnimationStartedAt = 0;
let droneAnimationDurationMs = 900;
let droneRenderedLngLat = null;
let droneRenderedHeading = null;
let droneLastTelemetryLngLat = null;
// 多机：每机一个 marker / 轨迹（多机模式启用，单机模式保持 droneMarker 单机路径）
let vehicleMarkers = new Map();
// 每机返航点标记（H 图标，位置 = 该机初始位置 home_position_ned）
let vehicleHomeMarkers = new Map();
let vehicleTracks = new Map();
let vehicleMultiMode = false;
const SHOW_ACTIVE_LEG = true;
const VEHICLE_TRACK_DISTANCE_TOLERANCE_M = 2.0;
const VEHICLE_TRACK_AZIMUTH_TOLERANCE_DEG = 1.5;
const VEHICLE_TRACK_MAX_POINTS = 600;
const DRONE_MARKER_ANIMATION_MS = 180;
const DRONE_MARKER_SNAP_DISTANCE_M = 180;

// MapLibre GL 地图实例与图层（与参考项目 airsim_web 对齐）
let maplibreMap = null;
let droneMarker = null;
let homeMarker = null;
let selectedWaypointIndex = -1;
let lastMissionProgress = null;
let currentLayerKey = "satellite";
let wpDragging = false;
let fenceDrawingMode = false;

// 地图图层源：走本地瓦片代理 /tile/{layer}/{z}/{x}/{y}（参考 QGC 磁盘缓存）
// 代理首次从 Esri/OSM 拉取并写本地缓存，后续秒开；前端 URL 顺序统一 z/x/y
const MAP_LAYERS = {
  satellite: {
    name: "卫星",
    url: "/tile/satellite/{z}/{x}/{y}",
    maxZoom: 19,
  },
  street: {
    name: "街道",
    url: "/tile/street/{z}/{x}/{y}",
    maxZoom: 19,
  },
};

// 与 AirSim settings.json 的 OriginGeopoint 对齐（北京天安门）
// AirSim 无人机的 NED 坐标基于此原点，GPS↔NED 转换必须用同一原点
const AIRSIM_HOME_LAT = 39.9042;
const AIRSIM_HOME_LON = 116.4074;
const EARTH_RADIUS_M = 6371000.0;

const DEFAULT_MODELS = [
  { id: "deepseek", name: "DeepSeek", provider: "deepseek", model: "deepseek-chat", api_type: "openai", api_key: "" },
];

let modelsCache = [...DEFAULT_MODELS];
let backendDefaultModelId = "";
let skillsCache = [];
let skillsLoaded = false;
const DEFAULT_APPLICATION_SETTINGS = {
  appearance: { language: "zh-CN", theme: "dark", density: "comfortable" },
  map: { default_layer: "satellite", follow_vehicle: true, show_vehicle_track: false, require_reliable_gps: true },
  telemetry: { refresh_ms: 250, setup_refresh_ms: 100, history_seconds: 60, chart_sample_hz: 20 },
  safety: { confirm_real_vehicle_actions: true, require_gps_for_global_mission: true, max_display_jump_m: 120 },
  agent: { show_context_usage: true, auto_select_multimodal_model: true, persist_full_session_history: true },
};
let applicationSettings = JSON.parse(JSON.stringify(DEFAULT_APPLICATION_SETTINGS));
let applicationSettingsLoaded = false;

// 工具与技能的中文映射（简化 UI，避免英文卡片信息过载）
const TOOL_LOCALE = {
  "drone_arm": { name: "解锁电机", desc: "给飞行器上电并解锁电机，准备起飞。", category: "飞控" },
  "drone_disarm": { name: "锁定电机", desc: "锁定电机，停止动力输出。", category: "飞控" },
  "drone_takeoff": { name: "起飞", desc: "解锁后垂直起飞到指定高度。", category: "飞控" },
  "drone_land": { name: "降落", desc: "控制飞行器降落到地面并锁定。", category: "飞控" },
  "drone_hover": { name: "悬停", desc: "在当前位置保持悬停。", category: "飞控" },
  "drone_connect": { name: "连接飞控", desc: "建立与飞行器后端的通信链路。", category: "链路" },
  "drone_disconnect": { name: "断开飞控", desc: "断开当前通信链路。", category: "链路" },
  "drone_get_status": { name: "获取状态", desc: "读取飞行器当前状态、位置与姿态。", category: "遥测" },
  "drone_get_firmware_info": { name: "固件信息", desc: "读取 PX4 固件版本、板卡 ID、UID 与 MAVLink 能力。", category: "遥测" },
  "drone_get_parameters": { name: "读取参数", desc: "下载或查询 PX4 MAVLink 参数列表。", category: "遥测" },
  "drone_get_telemetry": { name: "获取遥测", desc: "读取实时遥测数据流。", category: "遥测" },
  "drone_fly_to": { name: "飞往目标", desc: "飞往指定的本地 NED 坐标。", category: "导航" },
  "drone_fly_to_gps": { name: "飞往 GPS 点", desc: "飞往指定的 GPS 坐标。", category: "导航" },
  "drone_move_relative": { name: "相对移动", desc: "按机体坐标系向前/右/上移动指定距离。", category: "导航" },
  "drone_upload_mission": { name: "上传任务", desc: "将本地航线任务上传到飞控。", category: "任务" },
  "drone_download_mission": { name: "下载任务", desc: "从飞控下载当前任务到本地。", category: "任务" },
  "drone_clear_mission": { name: "清空任务", desc: "清除飞控中存储的任务。", category: "任务" },
  "drone_start_mission": { name: "启动任务", desc: "命令飞控开始执行已上传任务。", category: "任务" },
  "drone_get_mission_progress": { name: "任务进度", desc: "读取当前任务执行进度。", category: "任务" },
  "drone_set_backend": { name: "切换后端", desc: "在 PX4 与 AirSim 后端之间切换。", category: "系统" },
  "drone_emergency_stop": { name: "紧急停止", desc: "立即切断动力并停止所有动作。", category: "安全" },
  "airsim_search_target": { name: "视觉搜索", desc: "在指定区域搜索目标物体。", category: "感知" },
};

const TOOL_CATEGORY_LOCALE = {
  flight_control: "飞控",
  telemetry: "遥测",
  navigation: "导航",
  mission: "任务",
  safety: "安全",
  perception: "感知",
  system: "系统",
  link: "链路",
};

// 连接配置列表（前端短期缓存，参考 QGC Links）
// 持久化由后端 data/settings.json 负责，严禁使用 localStorage。
let connectionsCache = [];
let connectionSettingsLoaded = false;
let autoConnectEnabled = true;
let activeConnectionId = "";
let selectedConnectionId = "";
let detectedMavlinkLinksCache = [];
let vehicleInfoCache = null;
let vehicleParametersCache = null;
let vehicleParametersLoading = false;
let vehicleParameterSearchTimer = null;
let vehicleSetupCache = null;
let vehicleSetupLoading = false;
let vehicleSetupPollTimer = null;
let vehicleTelemetryLoading = false;
let vehicleTelemetryPollTimer = null;
let vehicleHistoryLastFetchAt = 0;
let vehicleSensorsLastRenderAt = 0;
let activePidTuningView = "rate_roll";
let activeSystemSettingsSection = "links";
let activeSensorSetupTab = "imu";
let activeWaveformWindowSec = 10;
let activeWaveformSampleHz = 20;
let vehicleWaveformPaused = false;
let vehicleWaveformFrozenHistory = null;
const selectedVehicleWaveformKeys = new Set(["attitude.roll", "attitude.pitch", "attitude.yaw"]);
const VEHICLE_SETUP_POLL_MS = 2500;
const VEHICLE_TELEMETRY_POLL_MS = 250;
const VEHICLE_SENSOR_RENDER_THROTTLE_MS = 220;
const DEFAULT_CAMERA_SETTINGS = {
  source: "airsim",
  camera_name: "0",
  vehicle_name: "",
  image_type: "scene",
  timeout_sec: 30,
  auto_save: false,
};
let cameraSettings = { ...DEFAULT_CAMERA_SETTINGS };
let cameraSettingsLoaded = false;
let cameraWindows = new Map();
let cameraWindowCounter = 1;
const CAMERA_STREAM_INTERVAL_MS = 90;
const CAMERA_STREAM_ERROR_INTERVAL_MS = 1400;
const MAX_CAMERA_WINDOWS = 4;
const MAX_CAMERA_STREAM_ERRORS = 3;

function mergeApplicationSettings(raw) {
  const source = raw && typeof raw === "object" ? raw : {};
  return Object.fromEntries(
    Object.entries(DEFAULT_APPLICATION_SETTINGS).map(([group, defaults]) => [
      group,
      { ...defaults, ...(source[group] && typeof source[group] === "object" ? source[group] : {}) },
    ]),
  );
}

async function loadApplicationSettings(force = false) {
  if (applicationSettingsLoaded && !force) return applicationSettings;
  try {
    const data = await api("/api/settings/application");
    applicationSettings = mergeApplicationSettings(data.application);
    applicationSettingsLoaded = true;
    fillApplicationSettingsForm();
    currentLayerKey = applicationSettings.map.default_layer || "satellite";
    if (maplibreMap) applyMapLayer(currentLayerKey);
    restartMainTelemetryRefresh();
  } catch (error) {
    applicationSettings = mergeApplicationSettings(applicationSettings);
    showNotice(`应用设置加载失败: ${error.message || "未知错误"}`, "error");
  }
  return applicationSettings;
}

function fillApplicationSettingsForm() {
  const settings = applicationSettings;
  if (els.appLanguage) els.appLanguage.value = settings.appearance.language;
  if (els.appTheme) els.appTheme.value = settings.appearance.theme;
  if (els.appDensity) els.appDensity.value = settings.appearance.density;
  if (els.appMapLayer) els.appMapLayer.value = settings.map.default_layer;
  if (els.appTelemetryRefresh) els.appTelemetryRefresh.value = String(settings.telemetry.refresh_ms);
  if (els.appSetupRefresh) els.appSetupRefresh.value = String(settings.telemetry.setup_refresh_ms);
  if (els.appHistorySeconds) els.appHistorySeconds.value = String(settings.telemetry.history_seconds);
  if (els.appFollowVehicle) els.appFollowVehicle.checked = Boolean(settings.map.follow_vehicle);
  if (els.appShowTrack) els.appShowTrack.checked = Boolean(settings.map.show_vehicle_track);
  if (els.appRequireGps) els.appRequireGps.checked = Boolean(settings.map.require_reliable_gps);
  if (els.appConfirmRealVehicle) els.appConfirmRealVehicle.checked = Boolean(settings.safety.confirm_real_vehicle_actions);
  if (els.appRequireMissionGps) els.appRequireMissionGps.checked = Boolean(settings.safety.require_gps_for_global_mission);
  if (els.appShowContext) els.appShowContext.checked = Boolean(settings.agent.show_context_usage);
  if (els.appAutoMultimodal) els.appAutoMultimodal.checked = Boolean(settings.agent.auto_select_multimodal_model);
  if (els.appMaxMapJump) els.appMaxMapJump.value = String(settings.safety.max_display_jump_m);
  document.body.dataset.density = settings.appearance.density || "comfortable";
}

function applicationSettingsFromForm() {
  return mergeApplicationSettings({
    appearance: {
      language: els.appLanguage?.value || "zh-CN",
      theme: els.appTheme?.value || "dark",
      density: els.appDensity?.value || "comfortable",
    },
    map: {
      default_layer: els.appMapLayer?.value || "satellite",
      follow_vehicle: Boolean(els.appFollowVehicle?.checked),
      show_vehicle_track: Boolean(els.appShowTrack?.checked),
      require_reliable_gps: Boolean(els.appRequireGps?.checked),
    },
    telemetry: {
      refresh_ms: Number(els.appTelemetryRefresh?.value || 250),
      setup_refresh_ms: Number(els.appSetupRefresh?.value || 100),
      history_seconds: Number(els.appHistorySeconds?.value || 60),
      chart_sample_hz: applicationSettings.telemetry.chart_sample_hz,
    },
    safety: {
      confirm_real_vehicle_actions: Boolean(els.appConfirmRealVehicle?.checked),
      require_gps_for_global_mission: Boolean(els.appRequireMissionGps?.checked),
      max_display_jump_m: Number(els.appMaxMapJump?.value || 120),
    },
    agent: {
      show_context_usage: Boolean(els.appShowContext?.checked),
      auto_select_multimodal_model: Boolean(els.appAutoMultimodal?.checked),
      persist_full_session_history: true,
    },
  });
}

async function saveApplicationSettings() {
  const next = applicationSettingsFromForm();
  const data = await post("/api/settings/application", next);
  if (!data.ok) throw new Error(data.error || "保存失败");
  applicationSettings = mergeApplicationSettings(data.application);
  fillApplicationSettingsForm();
  applyMapLayer(applicationSettings.map.default_layer);
  restartVehicleTelemetryPolling();
  restartMainTelemetryRefresh();
  showNotice("应用设置已保存", "info");
}

async function loadConnectionSettings(force = false) {
  if (connectionSettingsLoaded && !force) {
    return {
      auto_connect: autoConnectEnabled,
      active_connection_id: activeConnectionId,
      connections: connectionsCache,
      detected_mavlink_links: detectedMavlinkLinksCache,
      vehicle_info: vehicleInfoCache,
    };
  }
  try {
    const data = await api("/api/settings/connections");
    connectionsCache = Array.isArray(data.connections) ? data.connections : [];
    autoConnectEnabled = Boolean(data.auto_connect);
    activeConnectionId = String(data.active_connection_id || "");
    detectedMavlinkLinksCache = Array.isArray(data.detected_mavlink_links) ? data.detected_mavlink_links : [];
    vehicleInfoCache = data.vehicle_info || vehicleInfoCache;
    if (vehicleInfoCache?.backend && vehicleInfoCache.backend !== "px4_mavlink") {
      vehicleParametersCache = { status: "error", connected: Boolean(vehicleInfoCache.connected), message: "PX4 MAVLink 后端才支持参数读取", parameters: [] };
    } else if (vehicleInfoCache?.connected === false) {
      vehicleParametersCache = { status: "disconnected", connected: false, parameters: [] };
    } else if (vehicleInfoCache?.parameters && !vehicleParametersCache) {
      vehicleParametersCache = vehicleInfoCache.parameters;
    }
    connectionSettingsLoaded = true;
  } catch (error) {
    // 后端不可用时保持内存状态，避免覆盖。
    showNotice("连接设置加载失败: " + (error.message || "未知错误"), "error");
  }
  return {
    auto_connect: autoConnectEnabled,
    active_connection_id: activeConnectionId,
    connections: connectionsCache,
    detected_mavlink_links: detectedMavlinkLinksCache,
    vehicle_info: vehicleInfoCache,
  };
}

async function saveConnectionSettings() {
  try {
    await post("/api/settings/connections", {
      auto_connect: autoConnectEnabled,
      active_connection_id: activeConnectionId,
      connections: connectionsCache,
    });
    return true;
  } catch (error) {
    showNotice("保存连接设置失败: " + (error.message || "未知错误"), "error");
    return false;
  }
}

async function loadVehicleInfo(refresh = false) {
  try {
    const query = refresh ? "?refresh=1" : "";
    const data = await api(`/api/settings/vehicle-info${query}`);
    vehicleInfoCache = data.vehicle_info || data || {};
    if (vehicleInfoCache?.backend && vehicleInfoCache.backend !== "px4_mavlink") {
      vehicleParametersCache = { status: "error", connected: Boolean(vehicleInfoCache.connected), message: "PX4 MAVLink 后端才支持参数读取", parameters: [] };
    } else if (vehicleInfoCache?.connected === false) {
      vehicleParametersCache = { status: "disconnected", connected: false, parameters: [] };
    } else if (vehicleInfoCache?.parameters && !vehicleParametersCache) {
      vehicleParametersCache = vehicleInfoCache.parameters;
    }
    renderVehicleSettingsPanel();
    renderActualLinkCard();
    return vehicleInfoCache;
  } catch (error) {
    vehicleInfoCache = {
      status: "error",
      message: error.message || "vehicle info unavailable",
    };
    renderVehicleSettingsPanel();
    return vehicleInfoCache;
  }
}

async function loadVehicleParameters(refresh = false) {
  const search = String(els.vehicleParameterSearch?.value || "").trim();
  const params = new URLSearchParams({
    limit: "300",
    offset: "0",
    timeout: refresh ? "24" : "18",
  });
  if (refresh) params.set("refresh", "1");
  if (search) params.set("q", search);
  vehicleParametersLoading = true;
  renderVehicleParametersPanel();
  try {
    const data = await api(`/api/settings/vehicle-parameters?${params.toString()}`);
    vehicleParametersCache = data.parameter_info || data || {};
    if (vehicleInfoCache && typeof vehicleInfoCache === "object") {
      vehicleInfoCache.parameters = {
        status: vehicleParametersCache.status,
        ready: vehicleParametersCache.ready,
        received_count: vehicleParametersCache.received_count,
        expected_count: vehicleParametersCache.expected_count,
        missing_count: vehicleParametersCache.missing_count,
        progress: vehicleParametersCache.progress,
        message: vehicleParametersCache.message,
      };
    }
    renderVehicleSettingsPanel();
    renderVehicleParametersPanel();
    return vehicleParametersCache;
  } catch (error) {
    vehicleParametersCache = {
      status: "error",
      connected: false,
      message: error.message || "vehicle parameters unavailable",
      parameters: [],
    };
    renderVehicleParametersPanel();
    return vehicleParametersCache;
  } finally {
    vehicleParametersLoading = false;
    renderVehicleParametersPanel();
  }
}

function isVehicleSetupSection(section = activeSystemSettingsSection) {
  return [
    "vehicle",
    "airframe",
    "sensors",
    "radio",
    "flight_modes",
    "power",
    "actuators",
    "safety",
    "pid_tuning",
    "waveforms",
    "flight_behavior",
    "parameters",
    "firmware",
  ].includes(section);
}

function isRealtimeVehicleSetupSection(section = activeSystemSettingsSection) {
  return [
    "vehicle",
    "sensors",
    "radio",
    "power",
    "actuators",
    "safety",
    "pid_tuning",
    "waveforms",
  ].includes(section);
}

function mergeVehicleSetupSnapshot(snapshot, options = {}) {
  if (!snapshot || typeof snapshot !== "object") return vehicleSetupCache;
  const previous = vehicleSetupCache && typeof vehicleSetupCache === "object" ? vehicleSetupCache : {};
  const isBusy = snapshot.status === "busy";
  const disconnected = snapshot.connected === false || snapshot.status === "disconnected";

  if (isBusy) {
    vehicleSetupCache = {
      ...previous,
      status: snapshot.status,
      message: snapshot.message || previous.message,
      backend: snapshot.backend || previous.backend,
      connected: previous.connected ?? snapshot.connected,
      updated_at: previous.updated_at,
    };
    return vehicleSetupCache;
  }

  if (disconnected || options.replace) {
    vehicleSetupCache = { ...snapshot };
  } else {
    vehicleSetupCache = {
      ...previous,
      ...snapshot,
      connection: snapshot.connection || previous.connection,
      firmware: snapshot.firmware || previous.firmware,
      parameters: snapshot.parameters || previous.parameters,
      parameter_groups: snapshot.parameter_groups || previous.parameter_groups,
      parameter_highlights: snapshot.parameter_highlights || previous.parameter_highlights,
      summary: snapshot.summary || previous.summary,
      telemetry: snapshot.telemetry || previous.telemetry,
      history: snapshot.history && Object.keys(snapshot.history).length
        ? { ...(previous.history || {}), ...snapshot.history }
        : previous.history,
      read_only: snapshot.read_only || previous.read_only,
    };
  }

  if (vehicleSetupCache?.parameters) {
    vehicleParametersCache = {
      ...(vehicleParametersCache || {}),
      ...vehicleSetupCache.parameters,
      parameters: vehicleParametersCache?.parameters || [],
    };
  }
  if (vehicleInfoCache && typeof vehicleInfoCache === "object") {
    if (vehicleSetupCache.connection) vehicleInfoCache.connection = vehicleSetupCache.connection;
    if (vehicleSetupCache.firmware) vehicleInfoCache.firmware = vehicleSetupCache.firmware;
    if (vehicleSetupCache.parameters) vehicleInfoCache.parameters = vehicleSetupCache.parameters;
    vehicleInfoCache.connected = Boolean(vehicleSetupCache.connected);
    vehicleInfoCache.backend = vehicleSetupCache.backend || vehicleInfoCache.backend;
  }
  return vehicleSetupCache;
}

async function loadVehicleSetup(force = false) {
  if (vehicleSetupLoading && !force) return vehicleSetupCache;
  vehicleSetupLoading = true;
  const includeHistory = force || !Object.keys(vehicleSetupCache?.history || {}).length;
  const historyLimit = Math.min(
    2400,
    Math.max(120, Number(applicationSettings.telemetry.history_seconds || 60) * Number(applicationSettings.telemetry.chart_sample_hz || 20)),
  );
  const params = new URLSearchParams({ history: includeHistory ? "1" : "0", limit: String(historyLimit) });
  try {
    const data = await api(`/api/settings/vehicle-setup?${params.toString()}`);
    mergeVehicleSetupSnapshot(data.vehicle_setup || data || {}, { replace: false });
    renderActiveVehicleSetupPanel("setup");
    return vehicleSetupCache;
  } catch (error) {
    mergeVehicleSetupSnapshot({
      status: "error",
      connected: false,
      message: error.message || "vehicle setup unavailable",
      history: {},
    }, { replace: true });
    renderActiveVehicleSetupPanel("setup-error");
    return vehicleSetupCache;
  } finally {
    vehicleSetupLoading = false;
  }
}

async function loadVehicleTelemetry(force = false) {
  if (vehicleTelemetryLoading && !force) return vehicleSetupCache;
  if (!isRealtimeVehicleSetupSection()) return vehicleSetupCache;
  vehicleTelemetryLoading = true;
  const now = Date.now();
  const includeHistory = force || now - vehicleHistoryLastFetchAt >= 250;
  let historyLimit = 180;
  if (activeSystemSettingsSection === "waveforms") {
    historyLimit = Math.max(180, activeWaveformWindowSec * Math.min(activeWaveformSampleHz, 20));
  } else if (activeSystemSettingsSection === "pid_tuning") {
    historyLimit = 240;
  }
  historyLimit = Math.min(2400, historyLimit);
  const historyGroups = activeVehicleHistoryGroups();
  const params = new URLSearchParams({
    history: includeHistory ? "1" : "0",
    limit: String(historyLimit),
    groups: historyGroups.join(","),
  });
  try {
    const data = await api(`/api/settings/vehicle-telemetry?${params.toString()}`);
    mergeVehicleSetupSnapshot(data.vehicle_telemetry || data || {}, { replace: false });
    if (includeHistory) vehicleHistoryLastFetchAt = now;
    renderActiveVehicleSetupPanel("telemetry");
    return vehicleSetupCache;
  } catch (error) {
    if (!vehicleSetupCache) {
      mergeVehicleSetupSnapshot({
        status: "error",
        connected: false,
        message: error.message || "vehicle telemetry unavailable",
        history: {},
      }, { replace: true });
    }
    renderActiveVehicleSetupPanel("telemetry-error");
    return vehicleSetupCache;
  } finally {
    vehicleTelemetryLoading = false;
  }
}

function activeVehicleHistoryGroups() {
  if (activeSystemSettingsSection === "sensors") return ["attitude", "imu", "vibration"];
  if (activeSystemSettingsSection === "pid_tuning") {
    return [pidChartConfig(activePidTuningView).history];
  }
  if (activeSystemSettingsSection === "waveforms") {
    return [...new Set(selectedWaveformChannels().map((channel) => channel.history))];
  }
  if (activeSystemSettingsSection === "radio") return ["rc"];
  if (activeSystemSettingsSection === "power") return ["battery"];
  if (activeSystemSettingsSection === "actuators") return ["servo"];
  return ["attitude", "position", "battery"];
}

function startVehicleSetupPolling() {
  if (!isVehicleSetupSection()) return;
  if (!vehicleSetupPollTimer) {
    loadVehicleSetup(false).catch(() => {});
    vehicleSetupPollTimer = setInterval(() => {
      if (els.systemSettingsModal?.hidden || !isVehicleSetupSection()) {
        stopVehicleSetupPolling();
        return;
      }
      loadVehicleSetup(false).catch(() => {});
    }, VEHICLE_SETUP_POLL_MS);
  }
  restartVehicleTelemetryPolling();
}

function restartVehicleTelemetryPolling() {
  if (vehicleTelemetryPollTimer) {
    clearInterval(vehicleTelemetryPollTimer);
    vehicleTelemetryPollTimer = null;
  }
  if (els.systemSettingsModal?.hidden || !isVehicleSetupSection() || !isRealtimeVehicleSetupSection()) return;
  loadVehicleTelemetry(true).catch(() => {});
  vehicleTelemetryPollTimer = setInterval(() => {
    if (els.systemSettingsModal?.hidden || !isVehicleSetupSection() || !isRealtimeVehicleSetupSection()) {
      restartVehicleTelemetryPolling();
      return;
    }
    loadVehicleTelemetry(false).catch(() => {});
  }, Math.max(50, Number(applicationSettings.telemetry.setup_refresh_ms || VEHICLE_TELEMETRY_POLL_MS)));
}

function stopVehicleSetupPolling() {
  if (vehicleSetupPollTimer) {
    clearInterval(vehicleSetupPollTimer);
    vehicleSetupPollTimer = null;
  }
  if (vehicleTelemetryPollTimer) {
    clearInterval(vehicleTelemetryPollTimer);
    vehicleTelemetryPollTimer = null;
  }
}

function isConnectionActive(connId) {
  const toolRuntime = latestState?.tool_runtime || {};
  const connected = Boolean(toolRuntime.connected) && !toolRuntime.stale_connection;
  if (!connected) return false;
  return connId === activeConnectionId;
}













async function deleteSelectedConnection() {
  if (!selectedConnectionId) return;
  connectionsCache = connectionsCache.filter((c) => c.id !== selectedConnectionId);
  if (activeConnectionId === selectedConnectionId) {
    activeConnectionId = "";
  }
  const saved = await saveConnectionSettings();
  if (!saved) return;
  selectedConnectionId = connectionsCache[0]?.id || "";
  renderConnectionsList();
  renderConnectionDetail(selectedConnectionId);
}

function normalizeCameraSettings(raw = {}) {
  const imageType = String(raw.image_type || DEFAULT_CAMERA_SETTINGS.image_type).toLowerCase();
  const timeout = Number(raw.timeout_sec || DEFAULT_CAMERA_SETTINGS.timeout_sec);
  return {
    source: String(raw.source || DEFAULT_CAMERA_SETTINGS.source).trim().toLowerCase() || DEFAULT_CAMERA_SETTINGS.source,
    url: String(raw.url || "").trim(),
    camera_name: String(raw.camera_name || DEFAULT_CAMERA_SETTINGS.camera_name).trim() || DEFAULT_CAMERA_SETTINGS.camera_name,
    vehicle_name: String(raw.vehicle_name || "").trim(),
    image_type: ["scene", "depth", "segmentation", "infrared"].includes(imageType) ? imageType : DEFAULT_CAMERA_SETTINGS.image_type,
    timeout_sec: Math.max(3, Math.min(120, Number.isFinite(timeout) ? timeout : DEFAULT_CAMERA_SETTINGS.timeout_sec)),
    auto_save: Boolean(raw.auto_save),
  };
}

async function loadCameraSettings(force = false) {
  if (cameraSettingsLoaded && !force) {
    renderCameraSettings();
    return cameraSettings;
  }
  try {
    const data = await api("/api/settings/camera");
    cameraSettings = normalizeCameraSettings(data.camera || {});
    cameraSettingsLoaded = true;
  } catch (error) {
    showNotice("摄像头设置加载失败: " + (error.message || "未知错误"), "error");
  }
  renderCameraSettings();
  renderCameraMeta();
  return cameraSettings;
}

function readCameraSettingsForm() {
  const source = els.cameraSource?.value || cameraSettings.source;
  return normalizeCameraSettings({
    source: source,
    url: els.cameraRtspUrl?.value || cameraSettings.url || "",
    camera_name: els.cameraName?.value || cameraSettings.camera_name,
    vehicle_name: els.cameraVehicle?.value || "",
    image_type: els.cameraImageType?.value || cameraSettings.image_type,
    timeout_sec: els.cameraTimeout?.value || cameraSettings.timeout_sec,
    auto_save: Boolean(els.cameraAutoSave?.checked),
  });
}





async function saveCameraSettings({ silent = false } = {}) {
  cameraSettings = readCameraSettingsForm();
  renderCameraMeta();
  try {
    const data = await post("/api/settings/camera", cameraSettings);
    cameraSettings = normalizeCameraSettings(data.camera || cameraSettings);
    cameraSettingsLoaded = true;
    renderCameraSettings();
    renderCameraMeta();
    if (!silent) showNotice("摄像头设置已保存", "success");
    return true;
  } catch (error) {
    showNotice("保存摄像头设置失败: " + (error.message || "未知错误"), "error");
    return false;
  }
}









function setupCameraViewerDrag() {
  const handle = els.cameraViewerDragHandle;
  if (!handle || !els.cameraViewer) return;
  let drag = null;

  handle.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || event.target.closest("button, select, input")) return;
    const stage = els.cameraViewer.offsetParent;
    if (!stage) return;
    const viewerRect = els.cameraViewer.getBoundingClientRect();
    const stageRect = stage.getBoundingClientRect();
    drag = {
      pointerId: event.pointerId,
      offsetX: event.clientX - viewerRect.left,
      offsetY: event.clientY - viewerRect.top,
      stageRect,
    };
    els.cameraViewer.style.left = `${Math.round(viewerRect.left - stageRect.left)}px`;
    els.cameraViewer.style.top = `${Math.round(viewerRect.top - stageRect.top)}px`;
    els.cameraViewer.style.right = "auto";
    els.cameraViewer.style.bottom = "auto";
    els.cameraViewer.classList.add("dragging");
    handle.setPointerCapture(event.pointerId);
    event.preventDefault();
  });

  handle.addEventListener("pointermove", (event) => {
    if (!drag || drag.pointerId !== event.pointerId) return;
    const width = els.cameraViewer.offsetWidth;
    const height = els.cameraViewer.offsetHeight;
    const left = Math.max(6, Math.min(event.clientX - drag.stageRect.left - drag.offsetX, drag.stageRect.width - width - 6));
    const top = Math.max(6, Math.min(event.clientY - drag.stageRect.top - drag.offsetY, drag.stageRect.height - height - 6));
    els.cameraViewer.style.left = `${Math.round(left)}px`;
    els.cameraViewer.style.top = `${Math.round(top)}px`;
    cameraViewerPositioned = true;
  });

  const finishDrag = (event) => {
    if (!drag || drag.pointerId !== event.pointerId) return;
    if (handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId);
    drag = null;
    els.cameraViewer.classList.remove("dragging");
  };
  handle.addEventListener("pointerup", finishDrag);
  handle.addEventListener("pointercancel", finishDrag);
}



function cameraSupportsImageCapture() {
  const capabilities = latestState?.tool_runtime?.backend_profile?.capabilities || {};
  return Boolean(capabilities.image_capture);
}

function cameraUnavailableMessage() {
  const runtime = latestState?.tool_runtime || {};
  const backend = backendDisplayName(runtime);
  return `当前 ${backend} 未注册可用图像源，请在系统设置中配置 AirSim 或相机连接`;
}



















function prepareCameraTemplateRoles(el = els.cameraViewer) {
  if (!el) return;
  el.dataset.cameraWindow = el.dataset.cameraWindow || "camera_1";
  el.querySelector("#cameraViewerDragHandle")?.setAttribute("data-camera-role", "handle");
  el.querySelector("#cameraViewerNewWindow")?.setAttribute("data-camera-action", "new");
  el.querySelector("#cameraViewerClose")?.setAttribute("data-camera-action", "close");
  el.querySelector("#cameraViewerSource")?.setAttribute("data-camera-role", "source");
  el.querySelector("#cameraViewerCamera")?.setAttribute("data-camera-role", "camera");
  el.querySelector("#cameraViewerVehicle")?.setAttribute("data-camera-role", "vehicle");
  el.querySelector("#cameraViewerImageType")?.setAttribute("data-camera-role", "imageType");
  el.querySelector("[data-camera-role='camera']")?.closest("label")?.setAttribute("data-camera-role", "cameraField");
  el.querySelector("[data-camera-role='vehicle']")?.closest("label")?.setAttribute("data-camera-role", "vehicleField");
  el.querySelector("[data-camera-role='imageType']")?.closest("label")?.setAttribute("data-camera-role", "imageTypeField");
  el.querySelector("#cameraLiveIndicator")?.setAttribute("data-camera-role", "live");
  el.querySelector("#cameraSnapshotStatus")?.setAttribute("data-camera-role", "status");
  el.querySelector("#cameraImage")?.setAttribute("data-camera-role", "image");
  el.querySelector("#cameraPlaceholder")?.setAttribute("data-camera-role", "placeholder");
  el.querySelector("#cameraMeta")?.setAttribute("data-camera-role", "meta");
}

function stripCameraCloneIds(el) {
  el.removeAttribute("id");
  el.querySelectorAll("[id]").forEach((node) => node.removeAttribute("id"));
}

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
    cameraFieldEl: el.querySelector("[data-camera-role='cameraField']"),
    vehicleFieldEl: el.querySelector("[data-camera-role='vehicleField']"),
    imageTypeFieldEl: el.querySelector("[data-camera-role='imageTypeField']"),
    statusEl: el.querySelector("[data-camera-role='status']"),
    imageEl: el.querySelector("[data-camera-role='image']"),
    placeholderEl: el.querySelector("[data-camera-role='placeholder']"),
    metaEl: el.querySelector("[data-camera-role='meta']"),
  };
}

function ensurePrimaryCameraWindow() {
  if (!els.cameraViewer) return null;
  const existing = cameraWindows.get("camera_1");
  if (existing) return existing;
  prepareCameraTemplateRoles(els.cameraViewer);
  return createCameraWindow({
    id: "camera_1",
    el: els.cameraViewer,
    settings: { ...cameraSettings },
    primary: true,
  });
}

function createCameraWindow({ id = "", el = null, settings = null, primary = false } = {}) {
  if (!el) return null;
  const winId = id || `camera_${++cameraWindowCounter}`;
  el.dataset.cameraWindow = winId;
  el.dataset.streaming = "false";
  el.dataset.state = "idle";
  const win = {
    id: winId,
    primary,
    el,
    ...cameraWindowParts(el),
    settings: normalizeCameraSettings(settings || cameraSettings),
    positioned: false,
    captureInFlight: false,
    streamActive: false,
    timer: null,
    frameSeq: 0,
    objectUrl: "",
    errorCount: 0,
    eventsBound: false,
    lastSuccessSource: "",
  };
  if (!primary && win.imageEl) {
    win.imageEl.removeAttribute("src");
    win.imageEl.hidden = true;
  }
  cameraWindows.set(winId, win);
  syncCameraWindowControls(win);
  renderCameraMeta(null, win);
  setupCameraWindowEvents(win);
  setCameraStreamActive(win, false);
  return win;
}

function primaryCameraWindow() {
  return ensurePrimaryCameraWindow();
}

function visibleCameraWindows() {
  return [...cameraWindows.values()].filter((win) => cameraViewerIsVisible(win));
}

function activeCameraStreams() {
  return [...cameraWindows.values()].filter((win) => win.streamActive && cameraViewerIsVisible(win));
}

function toggleCameraSourceSpecificFields(win) {
  if (!win) return;
  const source = String(win.sourceSelect?.value || win.settings?.source || "airsim").toLowerCase();
  const isAirSim = source === "airsim";
  if (win.cameraFieldEl) win.cameraFieldEl.hidden = !isAirSim;
  if (win.vehicleFieldEl) win.vehicleFieldEl.hidden = !isAirSim;
  if (win.imageTypeFieldEl) win.imageTypeFieldEl.hidden = !isAirSim;
}

function clearCameraWindowImage(win) {
  if (!win) return;
  if (win.objectUrl) {
    try { URL.revokeObjectURL(win.objectUrl); } catch (_) {}
    win.objectUrl = "";
  }
  if (win.imageEl) {
    win.imageEl.removeAttribute("src");
    win.imageEl.hidden = true;
  }
  if (win.placeholderEl) {
    win.placeholderEl.hidden = false;
    win.placeholderEl.textContent = "等待新画面";
  }
}

function syncCameraWindowControls(win) {
  if (!win) return;
  if (win.sourceSelect) win.sourceSelect.value = String(win.settings.source || "airsim").toLowerCase();
  if (win.cameraSelect) {
    const cameraName = String(win.settings.camera_name || "0");
    if (![...win.cameraSelect.options].some((option) => option.value === cameraName)) {
      win.cameraSelect.add(new Option(`${cameraName} · custom`, cameraName));
    }
    win.cameraSelect.value = cameraName;
  }
  if (win.imageTypeSelect) win.imageTypeSelect.value = win.settings.image_type || "scene";
  if (win.vehicleSelect) {
    const vehicleName = String(win.settings.vehicle_name || "");
    const hasOption = [...win.vehicleSelect.options].some((option) => option.value === vehicleName);
    if (vehicleName && !hasOption) {
      win.vehicleSelect.add(new Option(`${vehicleName} · custom`, vehicleName));
    }
    win.vehicleSelect.value = hasOption ? vehicleName : (win.vehicleSelect.value || "");
  }
  toggleCameraSourceSpecificFields(win);
}

function renderCameraSettings() {
  if (els.cameraSource) els.cameraSource.value = cameraSettings.source;
  if (els.cameraName) els.cameraName.value = cameraSettings.camera_name;
  if (els.cameraVehicle) els.cameraVehicle.value = cameraSettings.vehicle_name;
  if (els.cameraImageType) els.cameraImageType.value = cameraSettings.image_type;
  if (els.cameraTimeout) els.cameraTimeout.value = String(Math.round(cameraSettings.timeout_sec));
  if (els.cameraAutoSave) els.cameraAutoSave.checked = Boolean(cameraSettings.auto_save);
  cameraWindows.forEach(syncCameraWindowControls);
}

function syncCameraToolbarState() {
  const visible = visibleCameraWindows().length > 0;
  if (!els.cameraViewBtn) return;
  els.cameraViewBtn.setAttribute("aria-pressed", String(visible));
  els.cameraViewBtn.classList.toggle("active", visible);
  els.cameraViewBtn.title = visible ? "隐藏摄像头窗口" : "显示摄像头窗口";
  els.cameraViewBtn.setAttribute("aria-label", visible ? "隐藏摄像头窗口" : "显示摄像头窗口");
}

function cameraSourceLabel(settings) {
  const source = String(settings.source || "airsim").toLowerCase();
  if (source === "airsim") return "AirSim";
  if (source === "local") return "本机摄像头";
  if (source === "rtsp") {
    const url = String(settings.url || "").trim();
    return url ? `RTSP ${url.length > 28 ? url.slice(0, 27) + "…" : url}` : "RTSP";
  }
  return source;
}

function renderCameraMeta(data = null, win = primaryCameraWindow()) {
  if (!win?.metaEl) return;
  const settings = win.settings || cameraSettings;
  const sourceLabel = cameraSourceLabel(settings);
  const camera = data?.camera || settings.camera_name || "0";
  const vehicle = data?.vehicle || settings.vehicle_name || "default";
  const type = data?.image_type || settings.image_type || "scene";
  const size = data?.size_kb ? ` · ${data.size_kb} KB` : "";
  win.metaEl.textContent = `${sourceLabel} · ${vehicle} · camera ${camera} · ${type}${size}`;
}

function setCameraViewerVisible(visible, win = primaryCameraWindow()) {
  if (!win?.el) return;
  win.el.hidden = !visible;
  syncCameraToolbarState();
  if (visible) {
    focusCameraWindow(win);
    if (!win.positioned) requestAnimationFrame(() => placeCameraViewerAtDefault(win));
  }
}

function cameraViewerIsVisible(win = primaryCameraWindow()) {
  return Boolean(win?.el && !win.el.hidden);
}

function focusCameraWindow(win) {
  if (!win?.el) return;
  win.el.style.zIndex = String(20 + (Date.now() % 100000));
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

function placeCameraViewerAtDefault(win = primaryCameraWindow()) {
  if (!win?.el || win.el.hidden) return;
  const stage = win.el.offsetParent;
  const profile = els.missionProfile;
  if (!stage || !profile) return;
  const stageRect = stage.getBoundingClientRect();
  const profileRect = profile.getBoundingClientRect();
  const viewerRect = win.el.getBoundingClientRect();
  const visible = visibleCameraWindows();
  const index = Math.max(0, visible.findIndex((item) => item.id === win.id));
  const gap = 10;
  const baseLeft = Math.max(10, profileRect.left - stageRect.left);
  const baseTop = profileRect.top - stageRect.top - viewerRect.height - 8;
  const columns = Math.max(1, Math.floor((stageRect.width - baseLeft - 10) / Math.max(1, viewerRect.width + gap)));
  const col = index % columns;
  const row = Math.floor(index / columns);
  setCameraWindowPosition(
    win,
    baseLeft + col * (viewerRect.width + gap),
    baseTop - row * (viewerRect.height + gap),
  );
  win.positioned = true;
}

function clampCameraViewerPosition(win = null) {
  const windows = win ? [win] : [...cameraWindows.values()];
  windows.forEach((item) => {
    if (!item.positioned || !cameraViewerIsVisible(item) || !item.el) return;
    const stage = item.el.offsetParent;
    if (!stage) return;
    const stageRect = stage.getBoundingClientRect();
    const viewerRect = item.el.getBoundingClientRect();
    setCameraWindowPosition(item, viewerRect.left - stageRect.left, viewerRect.top - stageRect.top);
  });
}

function setupCameraWindowDrag(win) {
  const handle = win?.handle;
  if (!handle || !win.el) return;
  let drag = null;

  handle.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || event.target.closest("button, select, input")) return;
    const stage = win.el.offsetParent;
    if (!stage) return;
    const viewerRect = win.el.getBoundingClientRect();
    const stageRect = stage.getBoundingClientRect();
    drag = {
      pointerId: event.pointerId,
      offsetX: event.clientX - viewerRect.left,
      offsetY: event.clientY - viewerRect.top,
      stageRect,
    };
    win.el.style.left = `${Math.round(viewerRect.left - stageRect.left)}px`;
    win.el.style.top = `${Math.round(viewerRect.top - stageRect.top)}px`;
    win.el.style.right = "auto";
    win.el.style.bottom = "auto";
    win.el.classList.add("dragging");
    focusCameraWindow(win);
    handle.setPointerCapture(event.pointerId);
    event.preventDefault();
  });

  handle.addEventListener("pointermove", (event) => {
    if (!drag || drag.pointerId !== event.pointerId) return;
    setCameraWindowPosition(
      win,
      event.clientX - drag.stageRect.left - drag.offsetX,
      event.clientY - drag.stageRect.top - drag.offsetY,
    );
    win.positioned = true;
  });

  const finishDrag = (event) => {
    if (!drag || drag.pointerId !== event.pointerId) return;
    if (handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId);
    drag = null;
    win.el.classList.remove("dragging");
  };
  handle.addEventListener("pointerup", finishDrag);
  handle.addEventListener("pointercancel", finishDrag);
}

function setCameraViewerState(win, state, message) {
  if (!win?.el) return;
  win.el.dataset.state = state;
  if (win.statusEl) win.statusEl.textContent = message || "";
  const hasFrame = Boolean(win.imageEl?.src);
  if (win.placeholderEl) {
    win.placeholderEl.hidden = state === "ready" || hasFrame;
    win.placeholderEl.textContent = message || "暂无画面";
  }
  if (win.imageEl) win.imageEl.hidden = state !== "ready" && !hasFrame;
}

function setCameraStreamActive(win, active) {
  if (!win) return;
  const next = Boolean(active);
  win.streamActive = next;
  if (!win.streamActive) win.frameSeq += 1;
  if (win.timer) {
    clearTimeout(win.timer);
    win.timer = null;
  }
  if (win.el) win.el.dataset.streaming = String(next);
  if (win.liveIndicator) {
    win.liveIndicator.innerHTML = next ? "<i></i>LIVE" : "<i></i>PAUSED";
  }
  if (!next) {
    const hasFrame = Boolean(win.imageEl?.src);
    setCameraViewerState(win, hasFrame ? "paused" : "idle", hasFrame ? "视频流已暂停" : "未连接视频流");
  }
}

function cameraStreamQuality(source = "airsim") {
  const count = Math.max(1, activeCameraStreams().length);
  // 本机摄像头: 不需要降频, 保持原生画质 + 短轮询间隔, 画面才流畅
  if (String(source || "").toLowerCase() === "local") {
    if (count <= 1) return { interval: 50, maxWidth: 1280, quality: 90 };
    if (count <= 2) return { interval: 70, maxWidth: 960, quality: 86 };
    return { interval: 90, maxWidth: 800, quality: 82 };
  }
  // AirSim / RTSP: 适度降频避免对仿真器/网络造成压力
  if (count <= 1) return { interval: CAMERA_STREAM_INTERVAL_MS, maxWidth: 560, quality: 54 };
  if (count === 2) return { interval: 220, maxWidth: 480, quality: 50 };
  return { interval: 420, maxWidth: 400, quality: 46 };
}

function scheduleCameraFrame(win, delay = null) {
  if (!win?.streamActive || !cameraViewerIsVisible(win)) return;
  if (win.timer) clearTimeout(win.timer);
  const source = win.sourceSelect?.value || win.settings?.source || "airsim";
  const quality = cameraStreamQuality(source);
  const wait = delay ?? (win.el?.dataset.state === "error" ? CAMERA_STREAM_ERROR_INTERVAL_MS : quality.interval);
  win.timer = setTimeout(() => {
    win.timer = null;
    captureCameraFrame({ notify: false, openViewer: false, windowId: win.id });
  }, wait);
}

function readCameraViewerSettings(win = primaryCameraWindow()) {
  return normalizeCameraSettings({
    ...(win?.settings || cameraSettings),
    source: win?.sourceSelect?.value || win?.settings?.source || cameraSettings.source,
    camera_name: win?.cameraSelect?.value || win?.settings?.camera_name || cameraSettings.camera_name,
    vehicle_name: win?.vehicleSelect?.value || win?.settings?.vehicle_name || cameraSettings.vehicle_name,
    image_type: win?.imageTypeSelect?.value || win?.settings?.image_type || cameraSettings.image_type,
  });
}

function cameraPreviewUrl(settings) {
  const source = String(settings.source || "airsim").toLowerCase();
  const quality = cameraStreamQuality(source);
  const params = new URLSearchParams({
    source,
    timeout_sec: String(Math.min(Number(settings.timeout_sec || 2), 2.5)),
    max_width: String(quality.maxWidth),
    quality: String(quality.quality),
    _: String(Date.now()),
  });
  if (source === "airsim") {
    params.set("camera_name", settings.camera_name || "0");
    params.set("vehicle_name", settings.vehicle_name || "");
    params.set("image_type", settings.image_type || "scene");
  }
  return `/api/camera/preview?${params.toString()}`;
}

function cameraWindowById(windowId) {
  return cameraWindows.get(windowId) || primaryCameraWindow();
}

async function captureCameraFrame({ notify = true, openViewer = true, windowId = "camera_1" } = {}) {
  const win = cameraWindowById(windowId);
  if (!win || win.captureInFlight) return;
  if (openViewer) setCameraViewerVisible(true, win);
  if (!cameraSettingsLoaded) await loadCameraSettings();
  win.settings = readCameraViewerSettings(win);
  syncCameraWindowControls(win);
  renderCameraMeta(null, win);

  if (!["airsim", "rtsp", "local"].includes(win.settings.source)) {
    const message = `暂未接入 ${win.settings.source} 图像源`;
    setCameraViewerState(win, "error", message);
    setCameraStreamActive(win, false);
    if (notify) showNotice(message, "error");
    return;
  }

  win.captureInFlight = true;
  const seq = ++win.frameSeq;
  const buttons = [els.cameraCaptureFromSettingsBtn].filter(Boolean);
  buttons.forEach((button) => { button.disabled = true; });
  if (!win.imageEl?.src) setCameraViewerState(win, "loading", "正在连接视频流");
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 3600);
  try {
    const response = await fetch(cameraPreviewUrl(win.settings), { cache: "no-store", signal: controller.signal });
    if (!response.ok) {
      let message = response.statusText || "获取摄像头画面失败";
      try {
        const data = await response.json();
        message = extractApiError(data, message);
      } catch (_) {
        try {
          message = (await response.text()) || message;
        } catch (_) {}
      }
      throw new Error(message);
    }
    const metaText = response.headers.get("X-Camera-Meta") || "";
    let data = {};
    if (metaText) {
      try { data = JSON.parse(metaText); } catch (_) { data = {}; }
    }
    const blob = await response.blob();
    if (!blob.size) throw new Error("未返回图像数据");
    const nextUrl = URL.createObjectURL(blob);
    if (seq !== win.frameSeq) {
      URL.revokeObjectURL(nextUrl);
      return;
    }
    if (win.imageEl) {
      const previousUrl = win.objectUrl;
      win.objectUrl = nextUrl;
      win.imageEl.src = nextUrl;
      win.imageEl.hidden = false;
      if (previousUrl) setTimeout(() => URL.revokeObjectURL(previousUrl), 250);
    }
    win.errorCount = 0;
    const timestamp = new Date().toLocaleTimeString("zh-CN", { hour12: false });
    setCameraViewerState(win, "ready", win.streamActive ? `视频流 · ${timestamp}` : (data.message || "画面已更新"));
    renderCameraMeta(data, win);
    win.lastSuccessSource = win.settings.source;
    if (notify) showNotice(data.message || "摄像头画面已更新", "success");
  } catch (error) {
    win.errorCount += 1;
    const message = error.name === "AbortError" ? "摄像头预览超时，正在降频重试" : (error.message || "获取摄像头画面失败");
    setCameraViewerState(win, "error", message);
    if (notify || win.errorCount === 1) showNotice(message, "error");
    // 持续失败达到阈值后停止轮询, 避免对已断开/不可达的源做无效重试 (例如 AirSim 端口未开)
    if (win.errorCount >= MAX_CAMERA_STREAM_ERRORS && win.streamActive) {
      setCameraStreamActive(win, false);
      setCameraViewerState(win, "error", `${message}（已停止重试，切换图像源或关闭后重开）`);
      showNotice("已自动停止摄像头重试", "warning");
    }
  } finally {
    clearTimeout(timeout);
    win.captureInFlight = false;
    buttons.forEach((button) => { button.disabled = false; });
    if (win.streamActive) scheduleCameraFrame(win);
  }
}

async function startCameraStream({ notify = true, windowId = "camera_1", settings = null } = {}) {
  const win = cameraWindowById(windowId);
  if (!win) return;
  if (settings) {
    win.settings = normalizeCameraSettings(settings);
    syncCameraWindowControls(win);
  }
  setCameraViewerVisible(true, win);
  await loadCameraSettings();
  setCameraStreamActive(win, true);
  await captureCameraFrame({ notify, openViewer: false, windowId: win.id });
}

function stopCameraStream({ hide = false, windowId = "camera_1" } = {}) {
  const win = cameraWindowById(windowId);
  if (!win) return;
  setCameraStreamActive(win, false);
  if (hide && win.objectUrl) {
    URL.revokeObjectURL(win.objectUrl);
    win.objectUrl = "";
    if (win.imageEl) win.imageEl.removeAttribute("src");
  }
  if (hide) {
    setCameraViewerVisible(false, win);
    if (!win.primary && win.el) {
      win.el.remove();
      cameraWindows.delete(win.id);
    }
  } else if (cameraViewerIsVisible(win)) {
    setCameraViewerState(win, win.imageEl?.src ? "ready" : "idle", "视频流已暂停");
  }
  syncCameraToolbarState();
}

function stopAllCameraStreams({ hide = false } = {}) {
  [...cameraWindows.values()].forEach((win) => stopCameraStream({ hide, windowId: win.id }));
}

function nextCameraName() {
  const used = new Set(visibleCameraWindows().map((win) => String(win.settings.camera_name || "0")));
  for (let index = 0; index <= 4; index += 1) {
    const name = String(index);
    if (!used.has(name)) return name;
  }
  return "0";
}

async function createAdditionalCameraWindow(sourceWindow = primaryCameraWindow()) {
  if (visibleCameraWindows().length >= MAX_CAMERA_WINDOWS) {
    showNotice(`最多同时打开 ${MAX_CAMERA_WINDOWS} 个摄像头窗口`, "error");
    return null;
  }
  prepareCameraTemplateRoles(els.cameraViewer);
  const clone = els.cameraViewer.cloneNode(true);
  stripCameraCloneIds(clone);
  clone.hidden = true;
  clone.classList.remove("dragging");
  clone.dataset.cameraWindow = "";
  els.cameraViewer.parentElement.appendChild(clone);
  const win = createCameraWindow({
    el: clone,
    settings: {
      ...(sourceWindow?.settings || cameraSettings),
      camera_name: nextCameraName(),
    },
    primary: false,
  });
  await startCameraStream({ notify: false, windowId: win.id });
  return win;
}

async function updateCameraViewerSelection(eventOrWindow = null) {
  const win = eventOrWindow?.el
    ? eventOrWindow
    : cameraWindowById(eventOrWindow?.target?.closest?.(".camera-viewer")?.dataset.cameraWindow || "camera_1");
  if (!win) return;
  win.settings = readCameraViewerSettings(win);
  if (win.primary) {
    cameraSettings = { ...win.settings };
    renderCameraSettings();
    await saveCameraSettings({ silent: true });
  }
  renderCameraMeta(null, win);
  if (cameraViewerIsVisible(win)) {
    if (win.settings.source !== (win.lastSuccessSource || "")) {
      clearCameraWindowImage(win);
    }
    setCameraViewerState(win, "loading", "正在切换图像源");
    if (!win.streamActive) setCameraStreamActive(win, true);
    await captureCameraFrame({ notify: false, openViewer: false, windowId: win.id });
  }
}

function setupCameraWindowEvents(win) {
  if (!win || win.eventsBound) return;
  win.eventsBound = true;
  win.el.addEventListener("pointerdown", () => focusCameraWindow(win));
  win.newBtn?.addEventListener("click", () => createAdditionalCameraWindow(win));
  win.closeBtn?.addEventListener("click", () => stopCameraStream({ hide: true, windowId: win.id }));
  win.sourceSelect?.addEventListener("change", (event) => {
    toggleCameraSourceSpecificFields(win);
    updateCameraViewerSelection(event);
  });
  [win.cameraSelect, win.vehicleSelect, win.imageTypeSelect]
    .filter(Boolean)
    .forEach((control) => control.addEventListener("change", updateCameraViewerSelection));
  setupCameraWindowDrag(win);
}

function setupCameraEventListeners() {
  const primary = ensurePrimaryCameraWindow();
  if (els.cameraViewBtn) {
    els.cameraViewBtn.addEventListener("click", () => {
      if (visibleCameraWindows().length) stopAllCameraStreams({ hide: true });
      else startCameraStream({ windowId: primary?.id || "camera_1" });
    });
  }
  if (els.cameraSaveSettingsBtn) {
    els.cameraSaveSettingsBtn.addEventListener("click", () => saveCameraSettings());
  }
  if (els.cameraCaptureFromSettingsBtn) {
    els.cameraCaptureFromSettingsBtn.addEventListener("click", () => {
      const win = primaryCameraWindow();
      if (win) win.settings = readCameraSettingsForm();
      startCameraStream({ windowId: win?.id || "camera_1", settings: win?.settings || cameraSettings });
    });
  }
  window.addEventListener("resize", () => clampCameraViewerPosition());
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      cameraWindows.forEach((win) => {
        if (win.timer) clearTimeout(win.timer);
        win.timer = null;
      });
      return;
    }
    cameraWindows.forEach((win) => {
      if (win.streamActive && cameraViewerIsVisible(win)) scheduleCameraFrame(win, 0);
    });
  });
  [els.cameraSource, els.cameraName, els.cameraVehicle, els.cameraImageType, els.cameraTimeout, els.cameraAutoSave]
    .filter(Boolean)
    .forEach((control) => {
      control.addEventListener("change", () => {
        cameraSettings = readCameraSettingsForm();
        const win = primaryCameraWindow();
        if (win) {
          win.settings = { ...cameraSettings };
          syncCameraWindowControls(win);
          renderCameraMeta(null, win);
        }
        renderCameraSettings();
      });
    });
}

function loadAutoConnectEnabled() {
  return autoConnectEnabled;
}

async function saveAutoConnectEnabled(enabled) {
  autoConnectEnabled = Boolean(enabled);
  await saveConnectionSettings();
}

function setupConnectionEventListeners() {
  if (els.addConnectionBtn) {
    els.addConnectionBtn.addEventListener("click", () => {
      renderConnectionDetail("");
      if (els.connectionDetailName) els.connectionDetailName.focus();
    });
  }
  if (els.connectionDetailType) {
    els.connectionDetailType.addEventListener("change", updateConnectionTypeFields);
  }
  if (els.connectionDetailForm) {
    els.connectionDetailForm.addEventListener("submit", submitConnectionDetail);
  }
  if (els.connectionDetailConnect) {
    els.connectionDetailConnect.addEventListener("click", activateSelectedConnection);
  }
  if (els.connectionDetailDelete) {
    els.connectionDetailDelete.addEventListener("click", deleteSelectedConnection);
  }
  if (els.connectionDetailCancel) {
    els.connectionDetailCancel.addEventListener("click", closeSystemSettings);
  }
  if (els.connectionsList) {
    els.connectionsList.addEventListener("click", (event) => {
      const item = event.target.closest(".connection-item");
      if (!item) return;
      const connId = item.dataset.connectionId;
      renderConnectionDetail(connId);
      // 保险: 切换预设后强制再渲染一次模板, 避免被中间步骤覆盖
      renderAirSimSettingsForConnection();
    });
    els.connectionsList.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      const item = event.target.closest(".connection-item");
      if (!item) return;
      event.preventDefault();
      const connId = item.dataset.connectionId;
      renderConnectionDetail(connId);
      renderAirSimSettingsForConnection();
    });
  }
}

async function loadSkills(force = false) {
  if (skillsLoaded && !force) return;
  try {
    const data = await api("/api/skills");
    skillsCache = data.skills || [];
    skillsLoaded = true;
  } catch (error) {
    skillsCache = [];
  }
}

async function fetchModels() {
  try {
    const data = await api("/api/models");
    if (data && Array.isArray(data.models)) {
      modelsCache = data.models.length ? data.models : [...DEFAULT_MODELS];
      backendDefaultModelId = data.default || "";
    }
  } catch (error) {
    showNotice("模型列表加载失败: " + (error.message || "未知错误"), "error");
  }
  renderModelSelector();
  return modelsCache;
}

function loadModels() {
  return modelsCache;
}

function getSelectedModelId() {
  const saved = localStorage.getItem("airsim-agent-model-selected");
  if (saved && modelsCache.some((m) => m.id === saved)) return saved;
  if (backendDefaultModelId && modelsCache.some((m) => m.id === backendDefaultModelId)) return backendDefaultModelId;
  return els.modelSelector.value || modelsCache[0]?.id || "deepseek";
}

function setSelectedModelId(id) {
  localStorage.setItem("airsim-agent-model-selected", id);
  els.modelSelector.value = id;
  renderModelSelector();
}

function renderModelSelector() {
  const models = loadModels();
  const selector = els.modelSelector;
  selector.innerHTML = "";
  models.forEach((m) => {
    const opt = document.createElement("option");
    opt.value = m.id;
    opt.textContent = m.name;
    selector.appendChild(opt);
  });
  const saved = getSelectedModelId();
  if (saved && models.some((m) => m.id === saved)) selector.value = saved;

  const selected = models.find((m) => m.id === selector.value) || models[0];
  if (selected && els.modelSelectorLabel) {
    const effortLabel = reasoningEffortLabel(selected);
    els.modelSelectorLabel.textContent = effortLabel ? `${selected.name} · ${effortLabel}` : selected.name;
  }
  renderModelMenu();
}

function reasoningEffortLabel(model) {
  const mode = model?.thinking_mode || "";
  const effort = model?.reasoning_effort || "";
  if (mode === "disabled") return "无思考";
  if (mode === "enabled" && !effort) return "思考";
  const names = { low: "低思考", medium: "中思考", high: "高思考", max: "最大思考" };
  return names[effort] || "";
}

function renderModelMenu() {
  if (!els.modelSelectorMenu) return;
  const models = loadModels();
  const selectedId = getSelectedModelId();
  const items = models.map((m) => `
    <button class="model-option ${m.id === selectedId ? "active" : ""}" data-model-id="${escapeHtml(m.id)}" type="button">
      <span>${escapeHtml(m.name)}${m.multimodal ? " · 视觉" : ""}</span>
      <span class="check">✓</span>
    </button>
  `).join("");
  const addButton = `
    <button class="model-option model-option-add" data-model-action="add" type="button">
      <span>＋ 添加模型</span>
    </button>
  `;
  els.modelSelectorMenu.innerHTML = items + addButton;
}

function toggleModelMenu() {
  if (!els.modelSelectorMenu) return;
  const hidden = els.modelSelectorMenu.hidden;
  closeAllDropdowns();
  els.modelSelectorMenu.hidden = !hidden;
  els.modelSelectorMenu.closest(".model-dropdown")?.classList.toggle("open", !hidden);
}

function closeAllDropdowns() {
  if (els.modelSelectorMenu) {
    els.modelSelectorMenu.hidden = true;
    els.modelSelectorMenu.closest(".model-dropdown")?.classList.remove("open");
  }
}

function onModelChange(id) {
  setSelectedModelId(id);
  closeAllDropdowns();
}

async function saveModelToBackend(payload) {
  const isEdit = Boolean(payload.id);
  if (isEdit) {
    const data = await post(`/api/models/${encodeURIComponent(payload.id)}`, payload);
    return data.model;
  }
  const data = await post("/api/models", payload);
  return data.model;
}

async function deleteModelFromBackend(modelId) {
  await post(`/api/models/${encodeURIComponent(modelId)}/delete`, {});
}

async function setDefaultModel(modelId) {
  await post("/api/models", { action: "default", id: modelId });
}

initLayoutPrefs();
initSplitters();
renderModelSelector();
fetchModels();
loadSkills();
setupConnectionEventListeners();
setupCameraEventListeners();
initSystemSettingsDrag();

// 异步加载后端连接设置（后端已在启动时根据 auto_connect 自动尝试连接）。
loadConnectionSettings();
loadCameraSettings();
loadApplicationSettings();

document.querySelectorAll(".application-settings-save").forEach((button) => {
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await saveApplicationSettings();
    } catch (error) {
      showNotice(error.message || "应用设置保存失败", "error");
    } finally {
      button.disabled = false;
    }
  });
});

els.modelSelector.addEventListener("change", () => onModelChange(els.modelSelector.value));

if (els.modelSelectorBtn) {
  els.modelSelectorBtn.addEventListener("click", (event) => {
    event.stopPropagation();
    toggleModelMenu();
  });
}

document.addEventListener("click", (event) => {
  const option = event.target.closest(".model-option");
  if (option) {
    const action = option.dataset.modelAction;
    if (action === "add") {
      event.stopPropagation();
      openModelModal();
      return;
    }
    const id = option.dataset.modelId;
    if (id) onModelChange(id);
    return;
  }
  if (!event.target.closest(".model-dropdown") && !event.target.closest("#modelModal")) {
    closeAllDropdowns();
  }
});

document.addEventListener("click", async (event) => {
  const sessionAction = event.target.closest("[data-session-action]");
  if (!sessionAction) return;

  const sessionId = sessionAction.dataset.sessionId;
  const action = sessionAction.dataset.sessionAction;
  if (!sessionId || !action) return;

  event.stopPropagation();

  if (action === "load") {
    await loadSession(sessionId);
    return;
  }

  if (action === "delete") {
    await deleteSession(sessionId);
    return;
  }

  if (action === "export") {
    const format = sessionAction.dataset.sessionFormat || "markdown";
    window.location.href = `/api/sessions/${encodeURIComponent(sessionId)}/export?format=${encodeURIComponent(format)}`;
    return;
  }
});

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(extractApiError(data, response.statusText));
  }
  if (data && data.ok === false) {
    throw new Error(extractApiError(data, "request failed"));
  }
  return data;
}

function post(path, payload) {
  return api(path, { method: "POST", body: JSON.stringify(payload) });
}

function extractApiError(data, fallback = "request failed") {
  const candidates = [
    data?.error,
    data?.message,
    data?.result?.message,
    data?.result?.data?.message,
    data?.result?.data?.data?.message,
  ];
  for (const item of candidates) {
    if (typeof item === "string" && item.trim()) return item;
  }
  const violations =
    data?.violations ||
    data?.result?.violations ||
    data?.result?.data?.violations ||
    data?.result?.data?.safety?.violations ||
    data?.result?.data?.data?.violations;
  if (Array.isArray(violations) && violations.length) {
    return violations.filter(Boolean).join("; ");
  }
  return fallback;
}

function setCommandMode(mode) {
  commandMode = mode === "execute" ? "execute" : "chat";
  localStorage.setItem("airsim-agent-command-mode", commandMode);
  if (els.chatModeBtn) els.chatModeBtn.classList.toggle("active", commandMode === "chat");
  if (els.executeModeBtn) els.executeModeBtn.classList.toggle("active", commandMode === "execute");
  if (els.commandInput) {
    els.commandInput.placeholder = commandMode === "execute"
      ? "Execute a flight command..."
      : "Chat, ask status, or clarify a plan...";
  }
  const submitButton = els.commandForm?.querySelector("button[type='submit']");
  if (submitButton) {
    submitButton.classList.toggle("execute", commandMode === "execute");
    submitButton.title = commandMode === "execute" ? "执行任务 (Enter)" : "发送聊天 (Enter)";
  }
  syncCommandSubmitState();
}

if (els.chatModeBtn) els.chatModeBtn.addEventListener("click", () => setCommandMode("chat"));
if (els.executeModeBtn) els.executeModeBtn.addEventListener("click", () => setCommandMode("execute"));
setCommandMode(commandMode);

const commandSubmitButton = els.commandForm?.querySelector("button[type='submit']");
if (commandSubmitButton) {
  commandSubmitButton.addEventListener("click", async (event) => {
    if (!isAgentWorkActive()) return;
    event.preventDefault();
    await cancelActiveWork();
  });
}

els.commandInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    els.commandForm.requestSubmit();
  }
});

if (els.attachImageBtn && els.imageInput) {
  els.attachImageBtn.addEventListener("click", () => els.imageInput.click());
  els.imageInput.addEventListener("change", async () => {
    await addImageFiles([...els.imageInput.files]);
    els.imageInput.value = "";
  });
}

els.commandInput.addEventListener("paste", async (event) => {
  const files = [...(event.clipboardData?.items || [])]
    .filter((item) => item.kind === "file" && item.type.startsWith("image/"))
    .map((item) => item.getAsFile())
    .filter(Boolean);
  if (!files.length) return;
  event.preventDefault();
  await addImageFiles(files);
});

if (els.imagePreview) {
  els.imagePreview.addEventListener("click", (event) => {
    const button = event.target.closest("[data-remove-image]");
    if (!button) return;
    pendingImages.splice(Number(button.dataset.removeImage), 1);
    renderImagePreview();
  });
}

els.commandForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  // 打断语义：任务执行中也允许发送，服务端会自动中断旧任务后执行新指令
  // （“打断对话即后台停止调用”）。输入框始终在提交后清空。
  const command = els.commandInput.value.trim();
  if (!command && !pendingImages.length) return;
  let selectedModel = modelsCache.find((model) => model.id === els.modelSelector.value);
  if (pendingImages.length && !selectedModel?.multimodal && applicationSettings.agent.auto_select_multimodal_model) {
    const visionModel = modelsCache.find((model) => model.multimodal && model.enabled);
    if (visionModel) {
      setSelectedModelId(visionModel.id);
      selectedModel = visionModel;
      showNotice(`已自动切换到图像模型 ${visionModel.name}`, "info");
    }
  }
  if (pendingImages.length && !selectedModel?.multimodal) {
    showNotice("未检测到可用图像模型，请在模型设置中修正模型 ID 或手动指定输入能力", "error");
    return;
  }
  const effectiveCommand = command || "请分析我提供的图片。";
  const attachments = pendingImages.map((item) => ({
    name: item.name,
    mime_type: item.mime_type,
    data_url: item.data_url,
  }));

  els.commandInput.value = "";
  const submitButton = els.commandForm.querySelector("button[type='submit']");
  submitButton.disabled = true;
  const mode = commandMode === "execute" ? "execute" : "chat";
  const pendingCommand = renderPendingCommand(effectiveCommand, mode, pendingImages);
  try {
    showNotice(mode === "execute" ? "任务已提交，正在执行..." : "正在回复...", "info");
    const resp = await post("/api/command", {
      command: effectiveCommand,
      mode,
      execute: mode === "execute",
      model: els.modelSelector.value,
      attachments,
    });
    if (resp?.run_id) bindPendingRunId(pendingCommand.agentId, resp.run_id);
    pendingImages = [];
    renderImagePreview();
    await refresh();
    if (resp && resp.ok) {
      showNotice(mode === "execute" ? "任务已进入执行流程" : "Chat 已提交，正在生成回复", "success");
    } else {
      // 服务端拒绝了提交：清掉“正在理解指令”的 pending 气泡，避免它
      // 与错误消息并存变红，让用户误以为任务失败后还会继续执行
      clearPendingCommand(pendingCommand);
      showNotice((resp && resp.result && resp.result.data && resp.result.data.message) || "指令处理失败", "error");
    }
  } catch (error) {
    clearPendingCommand(pendingCommand);
    // 输入框保持已清空状态：用户已按“发送”，指令不应再被放回框里
    await refresh().catch(() => {});
    showNotice(error.message || "任务提交失败", "error");
  } finally {
    submitButton.disabled = false;
    syncCommandSubmitState();
  }
});

function getCommandSubmitButton() {
  return els.commandForm?.querySelector("button[type='submit']") || null;
}

function isLiveRunStatus(status) {
  return ["queued", "running", "paused", "responding", "awaiting_approval"].includes(String(status || ""));
}

function isAgentWorkActive() {
  const run = latestState?.current_run;
  if (run && isLiveRunStatus(run.status)) return true;
  const messages = Array.isArray(latestState?.messages) ? latestState.messages : [];
  return messages.some((message) => (
    message?.role === "assistant"
    && message?.status === "running"
    && ["chat", "execute", "plan"].includes(String(message?.details?.mode || ""))
  ));
}

function syncCommandSubmitState() {
  const submitButton = getCommandSubmitButton();
  if (!submitButton) return;
  const active = isAgentWorkActive();
  submitButton.classList.toggle("execute", commandMode === "execute" && !active);
  submitButton.classList.toggle("busy", active);
  submitButton.disabled = false;
  submitButton.textContent = active ? "" : "↑";
  submitButton.title = active
    ? "任务执行中，发送将中断当前任务"
    : (commandMode === "execute" ? "执行任务 (Enter)" : "发送聊天 (Enter)");
  submitButton.setAttribute("aria-label", active ? "中断并发送新指令" : "发送");
}

async function cancelActiveWork() {
  const submitButton = getCommandSubmitButton();
  if (submitButton) submitButton.disabled = true;
  try {
    const result = await invokeFlightControl("cancel");
    await refresh().catch(() => {});
    showNotice(result?.ok ? "已发送中断请求" : (result?.error || "中断请求失败"), result?.ok ? "info" : "error");
  } catch (error) {
    showNotice(error.message || "中断请求失败", "error");
  } finally {
    if (submitButton) submitButton.disabled = false;
    syncCommandSubmitState();
  }
}

async function addImageFiles(files) {
  const allowed = new Set(["image/png", "image/jpeg", "image/webp", "image/gif"]);
  for (const file of files) {
    if (pendingImages.length >= 4) {
      showNotice("每条消息最多附加 4 张图片", "error");
      break;
    }
    if (!allowed.has(file.type)) {
      showNotice(`不支持的图片格式: ${file.type || file.name}`, "error");
      continue;
    }
    if (file.size > 5 * 1024 * 1024) {
      showNotice(`${file.name} 超过 5 MB`, "error");
      continue;
    }
    const total = pendingImages.reduce((sum, item) => sum + item.size, 0) + file.size;
    if (total > 12 * 1024 * 1024) {
      showNotice("单条消息图片总大小不能超过 12 MB", "error");
      break;
    }
    pendingImages.push({
      name: file.name || `clipboard-${Date.now()}.png`,
      mime_type: file.type,
      size: file.size,
      data_url: await fileToDataUrl(file),
    });
  }
  renderImagePreview();
}

function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error || new Error("图片读取失败"));
    reader.readAsDataURL(file);
  });
}

function renderImagePreview() {
  if (!els.imagePreview) return;
  els.imagePreview.hidden = pendingImages.length === 0;
  els.imagePreview.innerHTML = pendingImages.map((item, index) => `
    <figure class="composer-image-preview">
      <img src="${escapeHtml(item.data_url)}" alt="${escapeHtml(item.name)}">
      <button type="button" data-remove-image="${index}" title="移除图片">×</button>
      <figcaption>${escapeHtml(item.name)}</figcaption>
    </figure>
  `).join("");
}

document.addEventListener("click", async (event) => {
  const copyBtn = event.target.closest(".copy-btn");
  if (copyBtn) {
    const text = copyBtn.dataset.copy || "";
    if (text && navigator.clipboard) {
      await navigator.clipboard.writeText(text);
      copyBtn.textContent = "✓";
      setTimeout(() => { copyBtn.textContent = "⧉"; }, 1200);
    }
    return;
  }

  // P5: approval dialog buttons
  const approveBtn = event.target.closest("[data-approve-run]");
  if (approveBtn) {
    event.stopPropagation();
    approveBtn.disabled = true;
    await approveRun(approveBtn.dataset.approveRun);
    return;
  }
  const rejectBtn = event.target.closest("[data-reject-run]");
  if (rejectBtn) {
    event.stopPropagation();
    rejectBtn.disabled = true;
    await rejectRun(rejectBtn.dataset.rejectRun);
    return;
  }

  const settingsTab = event.target.closest("[data-settings-tab]");
  if (settingsTab) {
    const drawer = settingsTab.closest(".settings-drawer") || document;
    setSettingsTab(settingsTab.dataset.settingsTab, drawer);
    return;
  }

  const systemSection = event.target.closest("[data-system-section]");
  if (systemSection) {
    setSystemSettingsSection(systemSection.dataset.systemSection);
    return;
  }

  const pidView = event.target.closest("[data-pid-view]");
  if (pidView) {
    activePidTuningView = pidView.dataset.pidView || "rate_roll";
    renderVehiclePidPanel(false);
    return;
  }

  const sensorTab = event.target.closest("[data-sensor-setup-tab]");
  if (sensorTab) {
    activeSensorSetupTab = sensorTab.dataset.sensorSetupTab || "imu";
    renderVehicleSensorsPanel(true);
    return;
  }

  const waveWindow = event.target.closest("[data-wave-window]");
  if (waveWindow) {
    activeWaveformWindowSec = Number(waveWindow.dataset.waveWindow || 10) || 10;
    renderVehicleWaveformPanel(false);
    return;
  }

  const waveRate = event.target.closest("[data-wave-rate]");
  if (waveRate) {
    activeWaveformSampleHz = Number(waveRate.dataset.waveRate || 20) || 20;
    renderVehicleWaveformPanel(false);
    return;
  }

  const wavePause = event.target.closest("[data-wave-pause]");
  if (wavePause) {
    vehicleWaveformPaused = !vehicleWaveformPaused;
    vehicleWaveformFrozenHistory = vehicleWaveformPaused ? structuredClone(setupSnapshot().history || {}) : null;
    renderVehicleWaveformPanel(false);
    return;
  }

  const waveClear = event.target.closest("[data-wave-clear]");
  if (waveClear) {
    vehicleWaveformFrozenHistory = {};
    renderVehicleWaveformPanel(false);
    return;
  }

  const waveToggle = event.target.closest("[data-wave-key]");
  if (waveToggle) {
    const key = waveToggle.dataset.waveKey || "";
    if (key) {
      if (waveToggle.checked) selectedVehicleWaveformKeys.add(key);
      else selectedVehicleWaveformKeys.delete(key);
    }
    renderVehicleWaveformPanel(false);
    return;
  }

  const paramGroup = event.target.closest("[data-param-group]");
  if (paramGroup && els.vehicleParameterSearch) {
    els.vehicleParameterSearch.value = paramGroup.dataset.paramGroup || "";
    loadVehicleParameters(false).catch(() => {});
    return;
  }

  const paramSave = event.target.closest("[data-param-save]");
  if (paramSave) {
    await saveVehicleParameterFromRow(paramSave);
    return;
  }

  const button = event.target.closest("button");
  if (!button) return;

  if (button.dataset.command) {
    els.commandInput.value = button.dataset.command;
    els.commandInput.focus();
    return;
  }

  if (button.dataset.control) {
    await runButton(button, () => invokeFlightControl(button.dataset.control), "控制指令已执行");
    return;
  }

  if (button.dataset.tool) {
    const tool = button.dataset.tool;
    const params = parseParams(button.dataset.params);
    const targets = controlTargetList();
    await runButton(
      button,
      async () => {
        // 多选(或未选=全部)时:解锁/起飞逐台下发;起飞用非阻塞派发,多机同时升空
        if (targets.length > 1 && (tool === "drone_takeoff" || tool === "drone_arm")) {
          let last = null;
          for (const name of targets) {
            if (tool === "drone_takeoff") {
              last = await invokeFlightTool("drone_dispatch_takeoff", { ...params, vehicle_name: name });
            } else {
              last = await invokeFlightTool(tool, { ...params, vehicle_name: name });
            }
          }
          return last;
        }
        const targetParam = targets.length ? { vehicle_name: targets[0] } : {};
        return invokeFlightTool(tool, { ...params, ...targetParam });
      },
      `${tool} 已执行`,
    );
    return;
  }

  if (button.dataset.waypointAction) {
    handleWaypointAction(button.dataset.waypointAction, button);
    return;
  }

  if (button.dataset.zoom) {
    if (!maplibreMap) return;
    if (button.dataset.zoom === "in") maplibreMap.zoomIn();
    else maplibreMap.zoomOut();
  }
});

document.addEventListener("toggle", (event) => {
  const detail = event.target?.closest?.(".message-detail");
  if (!detail) return;
  const id = detail.dataset.detailId;
  if (!id) return;
  if (detail.open) openDetailIds.add(id);
  else openDetailIds.delete(id);
}, true);

els.settingsOpen.addEventListener("click", () => {
  openAgentSettings();
});
if (els.mapSettingsBtn) {
  els.mapSettingsBtn.addEventListener("click", () => {
    openSystemSettings();
  });
}
if (els.profileToggle) {
  els.profileToggle.addEventListener("click", () => {
    const collapsed = els.profileToggle.dataset.collapsed !== "true";
    els.profileToggle.dataset.collapsed = String(collapsed);
    document.body.dataset.profileCollapsed = String(collapsed);
    if (maplibreMap) maplibreMap.resize();
  });
}
if (els.agentSettingsClose) {
  els.agentSettingsClose.addEventListener("click", () => closeAgentSettings());
}
const SETTINGS_EXPAND_SVG = '<svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 6V2.5h3.5M13.5 6V2.5h-3.5M2.5 10v3.5h3.5M13.5 10v3.5h-3.5"/></svg>';
const SETTINGS_COLLAPSE_SVG = '<svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M5.5 2.5v3H2.5M10.5 2.5v3h3M5.5 13.5v-3H2.5M10.5 13.5v-3h3"/></svg>';
let settingsSavedSize = null;

function setupSystemSettingsResize() {
  const card = els.systemSettingsModal && els.systemSettingsModal.querySelector(".modal-card");
  if (!card) return;
  card.querySelectorAll(".resize-handle").forEach((handle) => {
    handle.addEventListener("mousedown", (e) => startSystemSettingsResize(handle, e, card));
  });
}

function startSystemSettingsResize(handle, e, card) {
  if (card.classList.contains("is-maximized")) return;
  e.preventDefault();
  e.stopPropagation();
  // 脱钩居中变换: 卡片之前用 transform: translate(-50%,-50%) 居中, 拖拽前要先切到显式 px 定位,
  // 否则 transform 仍会作用于盒子, 后续 left/top 改动会被先偏移再定位, 拖拽会跳.
  const rect = card.getBoundingClientRect();
  card.style.transition = "none";
  card.style.transform = "none";
  card.style.left = rect.left + "px";
  card.style.top = rect.top + "px";
  card.style.width = rect.width + "px";
  card.style.height = rect.height + "px";

  const startX = e.clientX;
  const startY = e.clientY;
  const startLeft = rect.left;
  const startTop = rect.top;
  const startWidth = rect.width;
  const startHeight = rect.height;
  const dir = handle.dataset.resize || "";
  const minW = 380;
  const minH = 300;

  function onMove(ev) {
    const dx = ev.clientX - startX;
    const dy = ev.clientY - startY;
    let newLeft = startLeft;
    let newTop = startTop;
    let newWidth = startWidth;
    let newHeight = startHeight;
    if (dir.includes("e")) newWidth = Math.max(minW, startWidth + dx);
    if (dir.includes("w")) {
      newWidth = Math.max(minW, startWidth - dx);
      newLeft = startLeft + (startWidth - newWidth);
    }
    if (dir.includes("s")) newHeight = Math.max(minH, startHeight + dy);
    if (dir.includes("n")) {
      newHeight = Math.max(minH, startHeight - dy);
      newTop = startTop + (startHeight - newHeight);
    }
    card.style.left = newLeft + "px";
    card.style.top = newTop + "px";
    card.style.width = newWidth + "px";
    card.style.height = newHeight + "px";
  }
  function onUp() {
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onUp);
    // 恢复 transition (留空让 CSS 规则重新生效, 不要写回具体值)
    card.style.transition = "";
  }
  document.addEventListener("mousemove", onMove);
  document.addEventListener("mouseup", onUp);
}

function toggleSystemSettingsMaximize() {
  const card = els.systemSettingsModal && els.systemSettingsModal.querySelector(".modal-card");
  if (!card) return;
  const btn = els.systemSettingsMaximize;
  if (card.classList.contains("is-maximized")) {
    card.classList.remove("is-maximized");
    if (settingsSavedSize) {
      card.style.width = settingsSavedSize.width || "";
      card.style.height = settingsSavedSize.height || "";
      card.style.left = settingsSavedSize.left || "";
      card.style.top = settingsSavedSize.top || "";
      card.style.transform = settingsSavedSize.transform || "";
    }
    btn.innerHTML = SETTINGS_EXPAND_SVG;
    btn.title = "全屏";
  } else {
    settingsSavedSize = {
      width: card.style.width,
      height: card.style.height,
      left: card.style.left,
      top: card.style.top,
      transform: card.style.transform,
    };
    card.classList.add("is-maximized");
    card.style.width = "100vw";
    card.style.height = "100vh";
    card.style.left = "50%";
    card.style.top = "50%";
    card.style.transform = "translate(-50%, -50%)";
    btn.innerHTML = SETTINGS_COLLAPSE_SVG;
    btn.title = "退出全屏";
  }
}

function resetSystemSettingsMaximize() {
  const card = els.systemSettingsModal && els.systemSettingsModal.querySelector(".modal-card");
  if (card) {
    card.classList.remove("is-maximized");
    card.style.width = "";
    card.style.height = "";
    card.style.left = "";
    card.style.top = "";
    card.style.transform = "";
  }
  settingsSavedSize = null;
  if (els.systemSettingsMaximize) {
    els.systemSettingsMaximize.innerHTML = SETTINGS_EXPAND_SVG;
    els.systemSettingsMaximize.title = "全屏";
  }
}

if (els.systemSettingsClose) {
  els.systemSettingsClose.addEventListener("click", () => closeSystemSettings());
}
if (els.systemSettingsMaximize) {
  els.systemSettingsMaximize.innerHTML = SETTINGS_EXPAND_SVG;
  els.systemSettingsMaximize.addEventListener("click", toggleSystemSettingsMaximize);
}
setupSystemSettingsResize();
if (els.refreshFirmwareInfoBtn) {
  els.refreshFirmwareInfoBtn.addEventListener("click", async () => {
    els.refreshFirmwareInfoBtn.disabled = true;
    const original = els.refreshFirmwareInfoBtn.textContent;
    els.refreshFirmwareInfoBtn.textContent = "读取中...";
    try {
      await loadVehicleInfo(true);
      showNotice("固件信息已刷新", "success");
    } finally {
      els.refreshFirmwareInfoBtn.disabled = false;
      els.refreshFirmwareInfoBtn.textContent = original || "刷新固件信息";
    }
  });
}
if (els.refreshVehicleParametersBtn) {
  els.refreshVehicleParametersBtn.addEventListener("click", async () => {
    els.refreshVehicleParametersBtn.disabled = true;
    const original = els.refreshVehicleParametersBtn.textContent;
    els.refreshVehicleParametersBtn.textContent = "读取中...";
    try {
      await loadVehicleParameters(true);
      const status = vehicleParametersCache?.status || "";
      showNotice(status === "ready" ? "参数已刷新" : "参数读取未完整完成，已显示当前收到的数据", status === "ready" ? "success" : "info");
    } finally {
      els.refreshVehicleParametersBtn.disabled = false;
      els.refreshVehicleParametersBtn.textContent = original || "刷新参数";
    }
  });
}
if (els.vehicleParameterSearch) {
  els.vehicleParameterSearch.addEventListener("input", () => {
    if (vehicleParameterSearchTimer) clearTimeout(vehicleParameterSearchTimer);
    vehicleParameterSearchTimer = setTimeout(() => {
      loadVehicleParameters(false).catch(() => {});
    }, 220);
  });
}
if (els.settingsBackdrop) {
  els.settingsBackdrop.addEventListener("click", () => {
    closeAgentSettings();
    closeSystemSettings();
  });
}
els.newSessionBtn.addEventListener("click", () => createSession());
if (els.sessionNavBtn) {
  els.sessionNavBtn.addEventListener("click", () => {
    if (els.sessionsPanel.classList.contains("is-open")) closeSessionsPanel();
    else openSessionsPanel();
  });
}
if (els.currentSessionLabel) {
  els.currentSessionLabel.addEventListener("dblclick", startHeaderSessionRename);
}
els.addModelBtn.addEventListener("click", () => openModelModal());
if (els.modelModalClose) els.modelModalClose.addEventListener("click", closeModelModal);
if (els.modelModalCancel) els.modelModalCancel.addEventListener("click", closeModelModal);
if (els.modelRevealKey) {
  els.modelRevealKey.addEventListener("click", async () => {
    const modelId = els.modelEditId?.value.trim();
    if (!modelId) return;
    if (els.modelApiKey.type === "text") {
      els.modelApiKey.type = "password";
      els.modelApiKey.value = "";
      els.modelRevealKey.textContent = "显示";
      return;
    }
    try {
      const data = await post(`/api/models/${encodeURIComponent(modelId)}/reveal-key`, {});
      els.modelApiKey.value = data.api_key || "";
      els.modelApiKey.type = "text";
      els.modelRevealKey.textContent = "隐藏";
    } catch (error) {
      showNotice(error.message || "读取密钥失败", "error");
    }
  });
}
if (els.modelForm) {
  els.modelForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    await submitModelForm();
  });
}

if (els.skillList) {
  els.skillList.addEventListener("click", (event) => {
    const item = event.target.closest("[data-skill-id]");
    if (!item) return;
    openSkillModal(item.dataset.skillId);
  });
}
if (els.skillModalClose) els.skillModalClose.addEventListener("click", closeSkillModal);
if (els.skillForm) {
  els.skillForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    await submitSkillForm();
  });
}
if (els.addSkillBtn) els.addSkillBtn.addEventListener("click", openNewSkillModal);
if (els.importSkillBtn && els.skillImportInput) {
  els.importSkillBtn.addEventListener("click", () => els.skillImportInput.click());
  els.skillImportInput.addEventListener("change", async () => {
    const file = els.skillImportInput.files?.[0];
    els.skillImportInput.value = "";
    if (!file) return;
    try {
      const markdown = await file.text();
      const name = markdown.match(/^name:\s*([^\r\n]+)/m)?.[1]?.trim().replace(/^["']|["']$/g, "");
      if (!name) throw new Error("SKILL.md 缺少 name 字段");
      await post("/api/skills", { action: "create", id: name, markdown });
      await loadSkills(true);
      renderSkills();
      showNotice(`已导入 skill:${name.replace(/^skill:/, "")}`, "success");
    } catch (error) {
      showNotice(error.message || "Skill 导入失败", "error");
    }
  });
}

[els.wpPropType, els.wpPropAlt, els.wpPropSpeed, els.wpPropHold, els.wpPropAccept].forEach((input) => {
  if (!input) return;
  input.addEventListener("input", () => applyWaypointProperties());
  input.addEventListener("change", () => applyWaypointProperties());
});

// 地图右侧航点面板折叠
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

// ---- 多选控制集：左侧小工具(起飞/降落/返航/悬停)作用于选中的机 ----
// 未选中任何机 = 作用于全部；最新点选的机同时成为航线规划目标。
let controlSelection = new Set();

function controlTargetList() {
  if (controlSelection.size) return [...controlSelection];
  const vehicles = Array.isArray(latestState?.tool_runtime?.vehicles) ? latestState.tool_runtime.vehicles : [];
  return vehicles.map((v) => String(v.vehicle_name || "")).filter(Boolean);
}

function controlTargetLabel() {
  if (!controlSelection.size) return "全部无人机";
  return [...controlSelection].join("、");
}

function toggleControlSelection(name) {
  if (controlSelection.has(name)) {
    controlSelection.delete(name);
    // 取消的正好是规划目标 → 目标移交给剩余选中中的最后一台
    if (missionTargetVehicle === name) {
      const next = [...controlSelection].pop() || "";
      if (next !== missionTargetVehicle) switchMissionTarget(next);
    }
  } else {
    controlSelection.add(name);
    if (missionTargetVehicle !== name) switchMissionTarget(name);
  }
  renderVehicleList();
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

// 多机模式：每机一个 marker（即时更新，不做插值动画，保持简单可靠）
function updateVehicleMarkers(vehicles, runtime) {
  if (!maplibreMap || !Array.isArray(vehicles) || vehicles.length === 0) return;
  const liveNames = new Set();
  for (const vehicle of vehicles) {
    const name = String(vehicle.vehicle_name || "");
    liveNames.add(name);
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
        .setLngLat(lngLat)
        .setRotation(heading)
        .addTo(maplibreMap);
      entry = { marker, lngLat, heading };
      vehicleMarkers.set(name, entry);
    }
    if (lngLat) {
      entry.marker.setLngLat(lngLat).setRotation(heading);
      entry.lngLat = lngLat;
      entry.heading = heading;
    }
    // 每机轨迹
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

function updateVehicleHomeMarkers(vehicles, runtime) {
  if (!maplibreMap || !Array.isArray(vehicles)) return;
  const liveNames = new Set();
  for (const vehicle of vehicles) {
    const name = String(vehicle.vehicle_name || "");
    if (!name) continue;
    const home = vehicle.home_position_ned;
    if (!home || !Number.isFinite(Number(home.x)) || !Number.isFinite(Number(home.y))) continue;
    const gps = nedToGps(Number(home.x), Number(home.y), Number(home.z || 0));
    const lngLat = [gps.lon, gps.lat];
    liveNames.add(name);
    let marker = vehicleHomeMarkers.get(name);
    if (!marker) {
      marker = new maplibregl.Marker({ element: createVehicleHomeElement(name, vehicleRouteColor(name)), anchor: "center" })
        .setLngLat(lngLat)
        .addTo(maplibreMap);
      vehicleHomeMarkers.set(name, marker);
    } else {
      marker.setLngLat(lngLat);
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
  const targetLabel = controlTargetLabel();
  if (["hover", "land", "return_home", "rtl"].includes(normalized)) {
    const runtime = requireLiveFlightLink();
    const capabilities = runtime.backend_profile?.capabilities || {};
    if (normalized === "land") {
      const approved = await confirmDialog({
        title: "确认降落",
        message: `将使 ${targetLabel} 就地降落，逐台确认落地后自动锁定。是否继续？`,
        confirmLabel: "确认降落",
        danger: true,
      });
      if (!approved) throw new Error("操作已取消");
    }
    if (["return_home", "rtl"].includes(normalized)) {
      const approved = await confirmDialog({
        title: "确认返航",
        message: `将使 ${targetLabel} 返回各自初始点，到位后自动降落锁定。是否继续？`,
        confirmLabel: "确认返航",
        danger: true,
      });
      if (!approved) throw new Error("操作已取消");
    }
  }
  return post("/api/control", {
    action: normalized,
    vehicles: targets,
    expected_backend: activeFlightRuntime().backend || "",
  });
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







function confirmDialog({ title, message, confirmLabel = "确认", danger = false }) {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "waypoint-editor-overlay";
    overlay.innerHTML = `
      <div class="waypoint-editor-card ${danger ? "danger-card" : ""}">
        <header><strong>${escapeHtml(title)}</strong></header>
        <div class="wp-editor-body"><p>${escapeHtml(message)}</p></div>
        <footer>
          <button class="secondary" data-confirm="cancel">取消</button>
          <button class="${danger ? "danger" : "primary"}" data-confirm="ok">${escapeHtml(confirmLabel)}</button>
        </footer>
      </div>
    `;
    document.body.appendChild(overlay);
    const cleanup = (result) => {
      overlay.remove();
      resolve(result);
    };
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) cleanup(false);
    });
    overlay.querySelector('[data-confirm="cancel"]').onclick = () => cleanup(false);
    overlay.querySelector('[data-confirm="ok"]').onclick = () => cleanup(true);
  });
}

async function refresh() {
  latestState = applyCachedSessionHistory(await api("/api/state"));
  render(latestState);
  loadCurrentSessionHistory().catch(() => {});
  syncRosTelemetryStream();
}

async function refreshTelemetryOnly() {
  syncRosTelemetryStream();
  if (rosTelemetryConnected) return;
  if (telemetryRefreshInFlight) return;
  telemetryRefreshInFlight = true;
  try {
    const frame = await api("/api/telemetry");
    latestState = latestState || {};
    latestState.runtime = frame.runtime || latestState.runtime;
    latestState.supervisor = frame.supervisor || latestState.supervisor;
    latestState.tool_runtime = frame.tool_runtime || latestState.tool_runtime;
    latestState.current_run = frame.current_run ?? latestState.current_run;
    latestState.llm = frame.llm || latestState.llm;
    const run = latestState.current_run;
    const toolRuntime = latestState.tool_runtime || {};
    const drone = toolRuntime.drone || {};
    renderTopbar(run, toolRuntime, latestState.supervisor || {}, latestState.llm || {});
    renderTelemetry(drone, toolRuntime);
    updateMapView(latestState);
    checkFlightTaskCompletion(toolRuntime);
  } finally {
    telemetryRefreshInFlight = false;
  }
}

// 多机航线完成监控：本轮派发的所有机都到达各自终点并悬停后提示一次，
// 并复位任务执行状态（missionExecutionActive）。
let activeFlightTaskVehicles = [];

function checkFlightTaskCompletion(toolRuntime = {}) {
  if (!activeFlightTaskVehicles.length) return;
  const tasks = toolRuntime.flight_tasks || {};
  const states = activeFlightTaskVehicles.map((name) => tasks[name]);
  // 派发瞬间遥测里还没有对应任务数据，先不判定
  if (states.some((s) => !s)) return;
  if (!states.every((s) => s.state === "done")) return;
  const names = activeFlightTaskVehicles.join("、");
  activeFlightTaskVehicles = [];
  markMissionEdited();
  showNotice(`✅ 航线飞行结束：${names} 已到达各自终点并悬停`, "success");
}

function restartMainTelemetryRefresh() {
  window.clearTimeout(telemetryRefreshTimer);
  const tick = async () => {
    try {
      await refreshTelemetryOnly();
    } catch (_) {
      // The next scheduled poll will retry.
    } finally {
      const interval = Math.max(100, Number(applicationSettings.telemetry.refresh_ms || 250));
      telemetryRefreshTimer = window.setTimeout(tick, interval);
    }
  };
  const interval = Math.max(100, Number(applicationSettings.telemetry.refresh_ms || 250));
  telemetryRefreshTimer = window.setTimeout(tick, interval);
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

function renderContextUsage(memory) {
  if (!els.contextUsage) return;
  const usage = memory?.conversation || {};
  const percent = Number(usage.context_percent);
  const used = Number(usage.estimated_context_tokens);
  const total = Number(usage.context_window);
  const visible = applicationSettings.agent.show_context_usage !== false;
  els.contextUsage.hidden = !visible;
  if (!visible) return;
  els.contextUsage.textContent = Number.isFinite(percent) ? `CTX ${percent.toFixed(percent >= 10 ? 0 : 1)}%` : "CTX --";
  els.contextUsage.title = Number.isFinite(used) && Number.isFinite(total)
    ? `本次模型上下文估算 ${used.toLocaleString()} / ${total.toLocaleString()} tokens；完整会话仍会保存`
    : "等待模型上下文统计";
  els.contextUsage.classList.toggle("warn", percent >= 70 && percent < 90);
  els.contextUsage.classList.toggle("danger", percent >= 90);
}

function renderApprovalDialog(run, pendingApprovals) {
  const el = document.getElementById("approvalDialog");
  if (!el) return;
  const pending = (pendingApprovals && pendingApprovals.length > 0)
    ? pendingApprovals
    : (run && run.status === "awaiting_approval" ? [{
        run_id: run.run_id,
        command: run.command,
        tool: (run.plan && run.plan.steps && run.plan.steps[0] && run.plan.steps[0].tool) || "",
        risk_level: run.risk_level || "high",
        reason: (run.plan && run.plan.risk_notes && run.plan.risk_notes.join("; ")) || "high-risk operation",
        status: "pending",
      }] : []);
  if (!pending.length) {
    el.classList.add("hidden");
    el.innerHTML = "";
    return;
  }
  el.classList.remove("hidden");
  el.innerHTML = pending.map((a) => `
    <div class="approval-card" data-run-id="${escapeHtml(a.run_id)}">
      <div class="approval-header">
        ${riskBadge(a.risk_level)}
        <strong>等待操作员确认</strong>
      </div>
      <div class="approval-body">
        <div><span>命令:</span> ${escapeHtml(a.command || "")}</div>
        <div><span>工具:</span> <code>${escapeHtml(a.tool || "")}</code></div>
        <div><span>原因:</span> ${escapeHtml(a.reason || "")}</div>
      </div>
      <div class="approval-actions">
        <button class="btn-approve" data-approve-run="${escapeHtml(a.run_id)}">确认执行</button>
        <button class="btn-reject" data-reject-run="${escapeHtml(a.run_id)}">拒绝</button>
      </div>
    </div>
  `).join("");
}

async function approveRun(runId) {
  try {
    const resp = await fetch("/api/approve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_id: runId }),
    });
    const data = await resp.json();
    if (!data.ok) {
      console.warn("approve failed:", data.error);
    }
  } catch (e) {
    console.error("approve error:", e);
  }
}

async function rejectRun(runId) {
  try {
    const resp = await fetch("/api/reject", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_id: runId }),
    });
    const data = await resp.json();
    if (!data.ok) {
      console.warn("reject failed:", data.error);
    }
  } catch (e) {
    console.error("reject error:", e);
  }
}

function renderSessions(sessions, currentSession) {
  if (!els.sessionsList) return;
  const currentId = currentSession?.id || "";
  if (!sessions.length) {
    els.sessionsList.innerHTML = `<div class="empty">暂无会话</div>`;
    return;
  }
  els.sessionsList.innerHTML = sessions.map((s) => {
    const isActive = s.id === currentId;
    const timeText = formatSessionTime(s.updated_at || s.created_at);
    return `
      <div class="session-item ${isActive ? "active" : ""}" data-session-id="${escapeHtml(s.id)}" data-session-action="load">
        <div class="session-name">${escapeHtml(s.name || "未命名对话")}</div>
        <div class="session-meta">
          <span>${timeText}</span>
          <span>${s.message_count || 0} 条消息</span>
        </div>
        <div class="session-actions" data-stop-propagation>
          <button data-session-id="${escapeHtml(s.id)}" data-session-action="export" data-session-format="markdown" title="导出完整会话">↓</button>
          <button class="delete-session" data-session-id="${escapeHtml(s.id)}" data-session-action="delete" title="删除">×</button>
        </div>
      </div>
    `;
  }).join("");
}

function renderCurrentSessionLabel(currentSession) {
  if (!els.currentSessionLabel) return;
  els.currentSessionLabel.textContent = currentSession?.name || "";
  els.currentSessionLabel.title = currentSession?.name || "";
}

function startHeaderSessionRename() {
  const currentSession = latestState?.current_session;
  if (!currentSession?.id || !els.currentSessionLabel) return;
  const sessionId = currentSession.id;
  const span = els.currentSessionLabel;
  const currentName = span.textContent;
  span.hidden = true;

  const input = document.createElement("input");
  input.type = "text";
  input.className = "current-session-input";
  input.value = currentName;
  span.parentNode.insertBefore(input, span.nextSibling);
  input.focus();
  input.select();

  const cleanup = () => {
    input.remove();
    span.hidden = false;
  };

  const finish = async (save) => {
    const newName = input.value.trim();
    if (save && newName && newName !== currentName) {
      span.textContent = newName;
      span.title = newName;
      cleanup();
      await renameSession(sessionId, newName);
    } else {
      span.textContent = currentName;
      span.title = currentName;
      cleanup();
    }
  };

  input.addEventListener("blur", () => finish(true));
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") input.blur();
    else if (event.key === "Escape") finish(false);
  });
}

function formatSessionTime(ts) {
  if (!ts) return "";
  const date = new Date(typeof ts === "number" && ts < 1e11 ? ts * 1000 : ts);
  if (Number.isNaN(date.getTime())) return "";
  const now = new Date();
  const diffSec = Math.floor((now - date) / 1000);
  if (diffSec < 60) return "刚刚";
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)} 分钟前`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)} 小时前`;
  if (diffSec < 604800) return `${Math.floor(diffSec / 86400)} 天前`;
  return date.toLocaleDateString("zh-CN", { month: "short", day: "numeric" });
}

function syncHeader() {
  const listOpen = els.sessionsPanel?.classList.contains("is-open") ?? false;
  if (els.sessionNavBtn) {
    if (listOpen) {
      els.sessionNavBtn.hidden = true;
    } else {
      els.sessionNavBtn.hidden = false;
      els.sessionNavBtn.textContent = "←";
      els.sessionNavBtn.title = "返回会话列表";
    }
  }
  if (els.currentSessionLabel) els.currentSessionLabel.hidden = listOpen;
}

function openSessionsPanel() {
  if (!els.sessionsPanel) return;
  els.sessionsPanel.classList.add("is-open");
  els.sessionsPanel.hidden = false;
  els.agentColumn?.classList.add("sessions-open");
  syncHeader();
}

function closeSessionsPanel() {
  if (!els.sessionsPanel) return;
  els.sessionsPanel.classList.remove("is-open");
  els.sessionsPanel.hidden = true;
  els.agentColumn?.classList.remove("sessions-open");
  syncHeader();
}

function backendDisplayName(toolRuntime = {}) {
  const profile = toolRuntime.backend_profile || {};
  return profile.name || profile.id || toolRuntime.backend || "Vehicle backend";
}

async function createSession() {
  try {
    await post("/api/sessions", { name: "新对话" });
    closeSessionsPanel();
    showNotice("新会话已创建", "success");
  } catch (error) {
    showNotice(error.message || "创建会话失败", "error");
  }
}

async function loadSession(sessionId) {
  try {
    const result = await post(`/api/sessions/${encodeURIComponent(sessionId)}/load`, {});
    const messages = Array.isArray(result?.session?.messages) ? result.session.messages : [];
    fullSessionMessageCache.set(sessionId, messages);
    if (latestState) {
      latestState.current_session = result.session;
      latestState.messages = messages;
      render(latestState);
    }
    closeSessionsPanel();
    showNotice("会话已切换", "info");
  } catch (error) {
    showNotice(error.message || "切换会话失败", "error");
  }
}

function mergeSessionMessages(completeMessages, recentMessages) {
  const byId = new Map();
  [...(completeMessages || []), ...(recentMessages || [])].forEach((message) => {
    if (message?.id) byId.set(message.id, message);
  });
  return [...byId.values()].sort((left, right) => (
    Number(left.created_at || 0) - Number(right.created_at || 0)
  ));
}

function applyCachedSessionHistory(snapshot) {
  const sessionId = snapshot?.current_session?.id || "";
  if (!sessionId || !fullSessionMessageCache.has(sessionId)) return snapshot;
  const merged = mergeSessionMessages(fullSessionMessageCache.get(sessionId), snapshot.messages);
  fullSessionMessageCache.set(sessionId, merged);
  snapshot.messages = merged;
  return snapshot;
}

async function loadCurrentSessionHistory(force = false) {
  const sessionId = latestState?.current_session?.id || "";
  if (!sessionId || sessionHistoryLoading.has(sessionId)) return;
  if (!force && fullSessionMessageCache.has(sessionId)) {
    applyCachedSessionHistory(latestState);
    renderChat(latestState.messages || [], latestState.current_run, latestState.llm || {});
    return;
  }
  sessionHistoryLoading.add(sessionId);
  try {
    const result = await api(`/api/sessions/${encodeURIComponent(sessionId)}/history`);
    const messages = Array.isArray(result?.session?.messages) ? result.session.messages : [];
    fullSessionMessageCache.set(sessionId, messages);
    if (latestState?.current_session?.id === sessionId) {
      latestState.messages = mergeSessionMessages(messages, latestState.messages);
      renderChat(latestState.messages || [], latestState.current_run, latestState.llm || {});
    }
  } catch (_) {
    // The live snapshot remains usable when persisted history cannot be loaded.
  } finally {
    sessionHistoryLoading.delete(sessionId);
  }
}

async function renameSession(sessionId, name) {
  try {
    await post(`/api/sessions/${encodeURIComponent(sessionId)}/rename`, { name });
  } catch (error) {
    showNotice(error.message || "重命名失败", "error");
  }
}

async function deleteSession(sessionId) {
  try {
    await post(`/api/sessions/${encodeURIComponent(sessionId)}/delete`, {});
    showNotice("会话已删除", "info");
  } catch (error) {
    showNotice(error.message || "删除会话失败", "error");
  }
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
    const disabled = !linked || (Boolean(tool) && Boolean(toolRuntime.busy));
    button.disabled = disabled;
    const channel = control === "return_home"
      ? contract.return_channel
      : contract.command_channel;
    button.title = disabled
      ? (!linked ? linkReason : "飞控正在执行任务")
      : `${button.dataset.baseTitle}${channel ? ` · ${channel}` : ""} · 目标: ${controlTargetLabel()}`;
  });
}

function renderTopbar(run, toolRuntime, supervisor, llm) {
  const connected = Boolean(toolRuntime.connected) && !toolRuntime.stale_connection;
  const backendName = backendDisplayName(toolRuntime);
  const backendState = connected ? "ONLINE" : "OFFLINE";
  if (els.connectionText) {
    els.connectionText.textContent = `${backendName} ${backendState}`;
    els.connectionText.title = `${backendName} ${backendState}`;
  }
  if (els.connectionDot) {
    els.connectionDot.classList.toggle("connected", connected);
  }
  const routeBadge = run?.route_strategy ? ` · ${run.route_strategy}` : "";
  if (els.plannerBadge) els.plannerBadge.textContent = `${run?.plan?.planner_source || "planner"}${routeBadge}`;
  updateFlightControlButtons(toolRuntime);
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

function renderTelemetry(drone, toolRuntime) {
  const pos = drone.position_ned || { x: 0, y: 0, z: 0 };
  const vel = drone.velocity_ned || { vx: 0, vy: 0, vz: 0 };
  const speed = Math.hypot(Number(vel.vx || 0), Number(vel.vy || 0), Number(vel.vz || 0));
  const altitude = Math.max(0, Math.abs(Number(pos.z || 0)));
  const connected = Boolean(toolRuntime.connected) && !toolRuntime.stale_connection;
  const realVehicle = isRealVehicleRuntime(toolRuntime);
  const reliableNavPosition = drone.navigation_position_valid === true || (!realVehicle && drone.navigation_position_valid !== false);

  renderVehicleList(toolRuntime);

  if (els.vehicleState) {
    els.vehicleState.textContent = drone.armed ? "ARMED" : "DISARMED";
    els.vehicleState.classList.toggle("armed", Boolean(drone.armed));
  }
  if (els.metricAltitude) els.metricAltitude.textContent = reliableNavPosition ? `${fmt(altitude)} m` : "--";
  if (els.metricPosition) {
    els.metricPosition.textContent = reliableNavPosition
      ? `N ${fmt(pos.x)} / E ${fmt(pos.y)} / D ${fmt(pos.z)}`
      : `NED RAW N ${fmt(pos.x)} / E ${fmt(pos.y)} / D ${fmt(pos.z)}`;
    els.metricPosition.title = reliableNavPosition
      ? "可用于导航的位置"
      : "真实飞控未解锁/未飞行时 LOCAL_POSITION_NED 可能漂移，当前不用于地图或距离计算";
  }
  if (els.metricVelocity) els.metricVelocity.textContent = `${fmt(speed)} m/s`;
  if (els.metricBattery) els.metricBattery.textContent = drone.battery_voltage != null ? `${fmt(drone.battery_voltage)} V` : (realVehicle ? "--" : "SIM");
  if (els.metricFlight) els.metricFlight.textContent = connected ? (drone.flying ? "空中" : "地面") : "离线";
  if (els.metricWaypoint) els.metricWaypoint.textContent = `#${missionWaypoints.length || extractPlanWaypoints(latestState?.current_run).length || 0}`;

  renderMissionMetrics(drone);
  renderSystemConnection(drone, toolRuntime);
}

// 多机列表：HUD 显示每架载具的简要状态（多机模式）
function renderVehicleList(toolRuntime = {}) {
  const container = els.vehicleList;
  if (!container) return;
  const vehicles = Array.isArray(toolRuntime.vehicles) ? toolRuntime.vehicles : [];
  // 相机设置页的车辆名建议（datalist）
  const datalist = document.getElementById("vehicleOptions");
  if (datalist) {
    datalist.textContent = "";
    for (const vehicle of vehicles) {
      const option = document.createElement("option");
      option.value = String(vehicle.vehicle_name || "");
      datalist.appendChild(option);
    }
  }
  // 多机模式：顶部 chips 即目标机选择器（点击切换规划目标，每机一色）
  if (vehicles.length <= 1) {
    // 退化为单机：清掉多机规划状态，航线回到默认单机流程
    if (missionTargetVehicle || Object.keys(missionPlans).length) {
      missionTargetVehicle = "";
      missionPlans = {};
      markMissionEdited();
      renderWaypoints();
      drawMissionPath();
    }
    updateMissionTargetBadge();
    container.hidden = true;
    container.textContent = "";
    syncCameraVehicleOptions(vehicles);
    return;
  }
  container.hidden = false;
  container.textContent = "";
  syncCameraVehicleOptions(vehicles);
  const missionTarget = currentMissionVehicleName();
  for (const vehicle of vehicles) {
    const name = String(vehicle.vehicle_name || "?");
    const state = vehicle.armed ? (vehicle.flying ? "空中" : "待飞") : "未解锁";
    const pos = vehicle.position_ned || {};
    const battery = vehicle.battery_voltage != null ? ` ${fmt(vehicle.battery_voltage)}V` : "";
    const routeColor = vehicleRouteColor(name);
    const planned = (currentMissionVehicleName() === name ? missionWaypoints.length : (missionPlans[name]?.length || 0));
    const inControlSet = controlSelection.has(name);
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = `hud-vehicle-chip${inControlSet ? " selected" : ""}${name === missionTarget ? " target" : ""}`;
    chip.title = `载具 ${name} · ${state}`
      + (planned ? ` · 已画 ${planned} 个航点` : "")
      + `\n点击 = 选中/取消控制目标(可多选);最新选中的为航线规划目标`;
    chip.dataset.vehicle = name;
    chip.innerHTML = `<span class="chip-dot" style="background:${routeColor};box-shadow:0 0 5px ${routeColor}"></span>${escapeHtml(name)}: ${state}${battery ? `<span class="chip-battery">${escapeHtml(battery.trim())}</span>` : ""}${planned ? `<span class="chip-plan-count">${planned}</span>` : ""}`;
    chip.addEventListener("click", () => toggleControlSelection(name));
    container.appendChild(chip);
  }
  updateMissionTargetBadge();
}

function syncCameraVehicleOptions(vehicles) {
  const list = Array.isArray(vehicles) ? vehicles : [];
  const names = list.map((vehicle) => String(vehicle?.vehicle_name || "").trim()).filter(Boolean);
  cameraWindows.forEach((win) => {
    if (!win.vehicleSelect) return;
    const previous = win.vehicleSelect.value || win.settings?.vehicle_name || "";
    win.vehicleSelect.textContent = "";
    if (!names.length) {
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = "等待 AirSim";
      win.vehicleSelect.appendChild(placeholder);
    } else {
      names.forEach((name) => {
        const option = document.createElement("option");
        option.value = name;
        option.textContent = name;
        win.vehicleSelect.appendChild(option);
      });
    }
    if (previous && names.includes(previous)) {
      win.vehicleSelect.value = previous;
    } else if (names.length) {
      win.vehicleSelect.value = names[0];
    }
  });
}



function renderToolCall(step) {
  const state = step.status || "pending";
  const mark = state === "completed" || state === "planned"
    ? "✓"
    : state === "failed" || state === "blocked"
      ? "!"
      : "•";
  const tool = step.tool || "tool";
  return `
    <div class="tool-call ${state}">
      <span class="tool-dot">${mark}</span>
      <div>
        <strong>${escapeHtml(humanToolLabel(tool, step.label))}</strong>
        <small>${escapeHtml(humanToolStatus(state))} · ${escapeHtml(tool)}</small>
      </div>
    </div>
  `;
}

function humanToolLabel(tool, label = "") {
  const cleanLabel = String(label || "").trim();
  if (cleanLabel && !/^Direct tool call:/i.test(cleanLabel) && cleanLabel !== tool) {
    return cleanLabel;
  }
  const labels = {
    drone_connect: "连接飞控链路",
    drone_disconnect: "断开飞控链路",
    drone_list_vehicles: "查看可用无人机",
    drone_get_status: "读取无人机状态",
    drone_arm: "解锁电机",
    drone_disarm: "锁定电机",
    drone_takeoff: "起飞",
    drone_land: "降落",
    drone_hover: "悬停",
    drone_fly_to: "飞往目标坐标",
    drone_move_relative: "按相对方向移动",
    drone_fly_velocity: "按速度飞行",
    drone_fly_path: "沿航线飞行",
    drone_upload_mission: "上传飞行任务",
    drone_download_mission: "下载飞行任务",
    drone_clear_mission: "清空飞行任务",
    drone_start_mission: "启动飞行任务",
    drone_get_mission_progress: "读取任务进度",
    drone_rotate_to: "调整朝向",
    drone_set_mode: "切换飞行模式",
    airsim_take_photo: "拍摄图像",
    airsim_vlm_analyze_image: "分析摄像头画面",
    airsim_vlm_confirm_target: "确认画面目标",
    airsim_get_sensors: "读取传感器",
    airsim_get_depth_map: "读取深度图",
    airsim_detect_objects: "识别画面目标",
    airsim_search_target: "搜索目标",
    airsim_approach_target: "接近目标",
    airsim_track_object: "跟踪目标",
    airsim_task_status: "读取后台任务状态",
    airsim_task_cancel: "取消后台任务",
    airsim_check_obstacle: "检查障碍物",
    memory_store: "记录任务记忆",
  };
  return labels[tool] || String(tool || "tool").replaceAll("_", " ");
}

function humanPlanSummary(plan, fallback = "") {
  const summary = String(plan?.summary || fallback || "").trim();
  const directMatch = summary.match(/^L0 direct route:\s*([\w_]+)/i);
  if (directMatch) return humanToolLabel(directMatch[1]);
  return summary || "任务处理中";
}

function visiblePlanReasoning(reasoning) {
  const text = String(reasoning || "").trim();
  if (!text) return "";
  if (/TaskRouter selected|no LLM planning|direct tool call/i.test(text)) return "";
  return text;
}

function humanToolStatus(status) {
  if (status === "completed") return "已完成";
  if (status === "failed") return "失败";
  if (status === "blocked") return "已阻止";
  if (status === "running") return "执行中";
  if (status === "planned") return "已规划";
  if (status === "awaiting_approval") return "等待确认";
  if (status === "cancelled") return "已取消";
  if (status === "paused") return "已暂停";
  return "等待中";
}

function riskBadge(riskLevel) {
  if (riskLevel === "high") return `<span class="risk-badge risk-high">高风险</span>`;
  if (riskLevel === "elevated") return `<span class="risk-badge risk-elevated">中风险</span>`;
  return "";
}

function renderPlan(run) {
  if (!run || !run.plan) {
    if (els.planSummary) els.planSummary.innerHTML = `<div class="empty">等待任务指令</div>`;
    if (els.runProgress) els.runProgress.style.width = "0%";
    return;
  }

  const planner = run.plan.planner_source || "planner";
  const model = run.plan.planner_model ? ` · ${run.plan.planner_model}` : "";
  const route = [run.task_level, run.route_strategy].filter(Boolean).join(" / ");
  const reasoningText = visiblePlanReasoning(run.plan.reasoning);
  const reasoning = reasoningText ? `<p>${escapeHtml(reasoningText)}</p>` : "";
  const risks = (run.plan.risk_notes || []).map((item) => `<span>${escapeHtml(item)}</span>`).join("");
  const badge = riskBadge(run.risk_level);

  if (els.planSummary) els.planSummary.innerHTML = `
    <strong>${escapeHtml(humanPlanSummary(run.plan, run.summary))} ${badge}</strong>
    <em>${escapeHtml([planner + model, route].filter(Boolean).join(" · "))}</em>
    ${reasoning}
    ${risks ? `<div class="risk-notes">${risks}</div>` : ""}
  `;
  if (els.runProgress) els.runProgress.style.width = `${Math.max(0, Math.min(100, run.progress || 0))}%`;
}

function humanRunStatus(status) {
  const labels = {
    completed: "完成",
    planned: "已规划",
    failed: "失败",
    blocked: "阻断",
    cancelled: "取消",
    running: "执行中",
    queued: "排队",
    responding: "汇总中",
    awaiting_approval: "待确认",
    paused: "暂停",
  };
  return labels[status] || status || "未知";
}

function renderTaskRuns(taskRuns) {
  if (!els.taskRunList) return;
  const runs = Array.isArray(taskRuns?.recent) ? taskRuns.recent : [];
  if (!runs.length) {
    els.taskRunList.innerHTML = `<div class="empty">暂无可复盘任务</div>`;
    return;
  }
  els.taskRunList.innerHTML = runs.slice(0, 8).map((run) => {
    const counters = run.counters || {};
    const title = run.summary || run.command || run.intent || "任务记录";
    const status = humanRunStatus(run.status);
    const time = run.started_at ? new Date(run.started_at * 1000).toLocaleString() : "";
    const meta = [
      status,
      run.route_strategy || run.task_level || "",
      `${counters.steps_ok || 0}/${counters.steps_total || 0} 步`,
      `${counters.events || 0} 事件`,
    ].filter(Boolean).join(" · ");
    const fail = run.failure_reason ? `<p class="task-run-failure">${escapeHtml(run.failure_reason)}</p>` : "";
    return `
      <article class="compact-item task-run-item ${escapeHtml(run.status || "")}" title="${escapeHtml(run.run_id || "")}">
        <div class="memory-item-head">
          <strong>${escapeHtml(title)}</strong>
          <small>${escapeHtml(status)}</small>
        </div>
        <p>${escapeHtml(meta)}</p>
        ${time ? `<span class="task-run-time">${escapeHtml(time)}</span>` : ""}
        ${fail}
      </article>
    `;
  }).join("");
}

function renderEvents(events) {
  const ordered = [...events].slice(-30).reverse();
  if (!ordered.length) {
    els.eventList.innerHTML = `<div class="empty">暂无事件</div>`;
    return;
  }
  els.eventList.innerHTML = ordered.map((event) => {
    const time = new Date((event.timestamp || 0) * 1000).toLocaleTimeString();
    return `
      <article class="event-item ${event.level}">
        <div class="event-line">
          <strong>${escapeHtml(event.source || "system")}</strong>
          <span>${time}</span>
        </div>
        <p class="event-message">${escapeHtml(event.message || "")}</p>
      </article>
    `;
  }).join("");
}







const WAYPOINT_TYPE_LABELS = {
  waypoint: "航点",
  takeoff: "起飞",
  land: "降落",
  rtl: "返航",
};

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
  if (els.wpPropAlt) els.wpPropAlt.value = wp.alt_m ?? 3;
  if (els.wpPropSpeed) els.wpPropSpeed.value = wp.speed_mps ?? 2;
  if (els.wpPropHold) els.wpPropHold.value = wp.hold_s ?? 0;
  if (els.wpPropAccept) els.wpPropAccept.value = wp.acceptance_radius_m ?? 2;
}

function applyWaypointProperties() {
  if (selectedWaypointIndex < 0 || selectedWaypointIndex >= missionWaypoints.length) return;
  const wp = missionWaypoints[selectedWaypointIndex];
  wp.type = els.wpPropType ? els.wpPropType.value : wp.type;
  wp.alt_m = Math.max(0, parseFloat(els.wpPropAlt?.value) || 0);
  wp.speed_mps = Math.max(0.1, parseFloat(els.wpPropSpeed?.value) || 2);
  wp.hold_s = Math.max(0, parseFloat(els.wpPropHold?.value) || 0);
  wp.acceptance_radius_m = Math.max(0.5, parseFloat(els.wpPropAccept?.value) || 2);
  markMissionEdited();
  renderWaypoints();
  drawMissionPath();
}

function buildWaypointCommand() {
  const route = missionWaypoints
    .map((wp, index) => `${index + 1}. lat ${wp.lat}, lon ${wp.lon}, alt ${wp.alt_m}m`)
    .join("；");
  return `按以下航点规划并执行飞行，速度2m/s，完成后悬停并报告状态：${route}`;
}

function updateMapView(state) {
  if (!maplibreMap) return;

  const runtime = state.tool_runtime || {};
  const drone = runtime.drone || {};
  const backendName = backendDisplayName(runtime);
  const linked = Boolean(runtime.connected) && !runtime.stale_connection;
  // function-scoped: used both inside the multi-vehicle branch and at the
  // status line below — a block-scoped declaration would throw
  // "gps is not defined" (ReferenceError) inside the else branch consumers
  const gps = droneGpsPosition(drone, runtime);

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
  const homeGps = homeGpsPosition(drone, runtime, state);
  if (homeGps && homeMarker) {
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

function drawMissionPath() {
  if (!maplibreMap) return;
  const pathSource = maplibreMap.getSource("path-source");
  const wpSource = maplibreMap.getSource("wp-source");
  if (!pathSource || !wpSource) return;

  // 航线统一由 plan-source 按机配色绘制（每机一色，当前目标机高亮），
  // 旧的白色 path-line 不再使用，保持 source 存在以兼容既有 layer 定义。
  pathSource.setData({ type: "FeatureCollection", features: [] });

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
    planSource.setData({ type: "FeatureCollection", features });
  }

  // 航点 GeoJSON features（id 用于 setFeatureState，type 用于 sprite 配色）
  const features = missionWaypoints.map((wp, index) => ({
    type: "Feature",
    id: index + 1,
    properties: { seq: index + 1, type: wp.type || "waypoint" },
    geometry: { type: "Point", coordinates: [wp.lon, wp.lat] },
  }));
  wpSource.setData({ type: "FeatureCollection", features });

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
    fenceSource.setData({ type: "FeatureCollection", features: [] });
    return;
  }

  const coords = missionFence.map((p) => [p.lon, p.lat]);
  if (missionFence.length >= 3) coords.push(coords[0]);

  fenceSource.setData({
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
  returnHomeGps = { lat: AIRSIM_HOME_LAT, lon: AIRSIM_HOME_LON, alt_m: 0, source: "AirSim origin", backend };
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

function updateVehicleTrack(lngLat, drone, linked, name = "") {
  const source = maplibreMap?.getSource("vehicle-track-source");
  if (!source || !linked || !Array.isArray(lngLat)) return;
  if (!drone?.armed) {
    vehicleTracks.delete(name);
    if (!name) {
      droneTrackActive = false;
      droneTrackLastAzimuth = null;
      droneTrackCoords = [];
    }
    updateVehicleTrackSource();
    return;
  }
  let track = vehicleTracks.get(name) || { coords: [], lastAzimuth: null };
  const last = track.coords[track.coords.length - 1];
  if (last) {
    const distance = haversineMeters(last[1], last[0], lngLat[1], lngLat[0]);
    if (distance < VEHICLE_TRACK_DISTANCE_TOLERANCE_M) return;
    if (distance > 300) {
      track = { coords: [lngLat], lastAzimuth: null };
    }
  }
  if (!track.coords.length) {
    track.coords.push(lngLat);
  } else {
    const prev = track.coords[track.coords.length - 1];
    const azimuth = calculateBearing(prev[1], prev[0], lngLat[1], lngLat[0]);
    const azimuthDelta = track.lastAzimuth == null ? Infinity : angleDeltaDeg(azimuth, track.lastAzimuth);
    if (track.coords.length < 2 || azimuthDelta > VEHICLE_TRACK_AZIMUTH_TOLERANCE_DEG) {
      track.coords.push(lngLat);
      track.lastAzimuth = azimuth;
    } else {
      track.coords[track.coords.length - 1] = lngLat;
    }
  }
  if (track.coords.length > VEHICLE_TRACK_MAX_POINTS) {
    track.coords = track.coords.slice(-VEHICLE_TRACK_MAX_POINTS);
  }
  vehicleTracks.set(name, track);
  if (!name) {
    // 兼容单机旧状态变量（clearVehicleTrack / 其他消费者）
    droneTrackCoords = track.coords;
    droneTrackActive = true;
    droneTrackLastAzimuth = track.lastAzimuth;
  }
  updateVehicleTrackSource();
}

function updateVehicleTrackSource() {
  const source = maplibreMap?.getSource("vehicle-track-source");
  if (!source) return;
  const features = [];
  for (const [name, track] of vehicleTracks.entries()) {
    if (track.coords.length >= 2) {
      features.push({
        type: "Feature",
        properties: { vehicle: name },
        geometry: { type: "LineString", coordinates: track.coords },
      });
    }
  }
  source.setData({ type: "FeatureCollection", features });
}

function clearVehicleTrack() {
  vehicleTracks.clear();
  droneTrackCoords = [];
  droneTrackActive = false;
  droneTrackLastAzimuth = null;
  const source = maplibreMap?.getSource("vehicle-track-source");
  if (source) source.setData({ type: "FeatureCollection", features: [] });
}

function angleDeltaDeg(a, b) {
  return Math.abs((((a - b) + 540) % 360) - 180);
}

function updateActiveLeg(lngLat, target = currentTargetWaypoint(lngLat)) {
  const source = maplibreMap?.getSource("active-leg-source");
  if (!source) return;
  if (!target) {
    source.setData({ type: "FeatureCollection", features: [] });
    return;
  }
  source.setData({
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
  if (source) source.setData({ type: "FeatureCollection", features: [] });
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
  const dLat = northM / EARTH_RADIUS_M * (180 / Math.PI);
  const dLon = eastM / (EARTH_RADIUS_M * Math.cos(AIRSIM_HOME_LAT * Math.PI / 180)) * (180 / Math.PI);
  return {
    lat: AIRSIM_HOME_LAT + dLat,
    lon: AIRSIM_HOME_LON + dLon,
    alt: -downM,
  };
}

// GPS → NED 反向转换
function gpsToNed(lat, lon, downM) {
  const dLat = (lat - AIRSIM_HOME_LAT) * Math.PI / 180;
  const dLon = (lon - AIRSIM_HOME_LON) * Math.PI / 180;
  const northM = dLat * EARTH_RADIUS_M;
  const eastM = dLon * EARTH_RADIUS_M * Math.cos(AIRSIM_HOME_LAT * Math.PI / 180);
  return { x: northM, y: eastM, z: downM };
}

function round6(value) {
  return Math.round(Number(value || 0) * 1e6) / 1e6;
}

function haversineMeters(lat1, lon1, lat2, lon2) {
  const toRad = (v) => (v * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function calculateBearing(lat1, lon1, lat2, lon2) {
  const toRad = (v) => (v * Math.PI) / 180;
  const dLon = toRad(lon2 - lon1);
  const y = Math.sin(dLon) * Math.cos(toRad(lat2));
  const x =
    Math.cos(toRad(lat1)) * Math.sin(toRad(lat2)) -
    Math.sin(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.cos(dLon);
  return (Math.atan2(y, x) * (180 / Math.PI) + 360) % 360;
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

function formatSigned(value, unit) {
  if (value == null || Number.isNaN(value)) return "--";
  const sign = value > 0 ? "+" : value < 0 ? "-" : "";
  return `${sign}${fmt(Math.abs(value))}${unit ? ` ${unit}` : ""}`;
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

function formatDistance(meters) {
  if (meters >= 1000) return `${(meters / 1000).toFixed(2)} km`;
  return `${Math.round(meters)} m`;
}

function formatDuration(seconds) {
  if (seconds >= 3600) return `${(seconds / 3600).toFixed(1)} h`;
  if (seconds >= 60) return `${Math.floor(seconds / 60)} m ${Math.round(seconds % 60)} s`;
  return `${Math.round(seconds)} s`;
}

function niceDistance(meters) {
  if (meters <= 0) return 10;
  const steps = [10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000, 100000];
  for (const s of steps) {
    if (s >= meters) return s;
  }
  return Math.pow(10, Math.ceil(Math.log10(meters)));
}

function drawMissionProfile() {
  const canvas = els.profileCanvas;
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(1, Math.floor(rect.width * dpr));
  canvas.height = Math.max(1, Math.floor(rect.height * dpr));
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const width = rect.width;
  const height = rect.height;

  ctx.clearRect(0, 0, width, height);

  // 折叠或高度不足时只清空，不重绘
  if (height < 60) return;

  const padding = { top: 16, right: 16, bottom: 38, left: 50 };
  const chartW = width - padding.left - padding.right;
  const chartH = height - padding.top - padding.bottom;

  // 动态 X 轴范围：使用当前地图可视宽度，让剖面图随卫星图缩放变化
  const visibleDist = getVisibleMapDistanceMeters();

  // 计算航点累计距离
  const points = [];
  let cumulative = 0;
  if (missionWaypoints.length >= 2) {
    points.push({ dist: 0, alt: missionWaypoints[0].alt_m || 0, seq: 1 });
    for (let i = 1; i < missionWaypoints.length; i++) {
      const a = missionWaypoints[i - 1];
      const b = missionWaypoints[i];
      const segment = haversineMeters(a.lat, a.lon, b.lat, b.lon);
      cumulative += segment;
      points.push({ dist: cumulative, alt: b.alt_m || 0, seq: i + 1 });
    }
  }

  const maxDist = Math.max(visibleDist, cumulative || 1);

  const alts = points.length ? points.map((p) => p.alt) : [0, 10];
  let minAlt = Math.min(...alts);
  let maxAlt = Math.max(...alts);
  if (maxAlt - minAlt < 5) {
    const mid = (minAlt + maxAlt) / 2;
    minAlt = mid - 2.5;
    maxAlt = mid + 2.5;
  }
  const altRange = Math.max(1, maxAlt - minAlt);

  const xFor = (d) => padding.left + (d / maxDist) * chartW;
  const yFor = (a) => padding.top + chartH - ((a - minAlt) / altRange) * chartH;

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

    // 航点标记
    points.forEach((p) => {
      const x = xFor(p.dist);
      const y = yFor(p.alt);
      ctx.beginPath();
      ctx.arc(x, y, 4, 0, Math.PI * 2);
      ctx.fillStyle = selectedWaypointIndex + 1 === p.seq ? "#f0b84a" : "#55dff4";
      ctx.fill();
      ctx.strokeStyle = "#06121a";
      ctx.lineWidth = 1.5;
      ctx.stroke();

      ctx.fillStyle = "rgba(237, 244, 255, 0.8)";
      ctx.font = "10px system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText(String(p.seq), x, y - 8);
    });
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

function initLayoutPrefs() {
  try {
    const saved = JSON.parse(localStorage.getItem("airsim-agent-layout") || "{}");
    if (saved.left) document.documentElement.style.setProperty("--left-pane", `${saved.left}px`);
    if (saved.right) document.documentElement.style.setProperty("--right-pane", `${saved.right}px`);
    if (saved.timeline) document.documentElement.style.setProperty("--timeline-height", `${saved.timeline}px`);
  } catch (_) {
    localStorage.removeItem("airsim-agent-layout");
  }
}

function saveLayoutPref(key, value) {
  let saved = {};
  try {
    saved = JSON.parse(localStorage.getItem("airsim-agent-layout") || "{}");
  } catch (_) {
    saved = {};
  }
  saved[key] = value;
  localStorage.setItem("airsim-agent-layout", JSON.stringify(saved));
}

function initSplitters() {
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
          const next = clamp(shellRect.right - moveEvent.clientX - 12, 310, 660);
          document.documentElement.style.setProperty("--right-pane", `${next}px`);
          saveLayoutPref("right", Math.round(next));
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

function openAgentSettings() {
  if (els.systemSettingsModal) els.systemSettingsModal.hidden = true;
  if (els.agentSettingsDrawer) els.agentSettingsDrawer.hidden = false;
  if (els.settingsBackdrop) els.settingsBackdrop.hidden = false;
  renderModelConfig();
  renderSkills();
}

function closeAgentSettings() {
  if (els.agentSettingsDrawer) els.agentSettingsDrawer.hidden = true;
  if (els.settingsBackdrop && (!els.systemSettingsModal || els.systemSettingsModal.hidden)) {
    els.settingsBackdrop.hidden = true;
  }
}

function initSystemSettingsDrag() {
  const modal = els.systemSettingsModal;
  const card = modal?.querySelector(".modal-card");
  const header = modal?.querySelector(".modal-head");
  if (!modal || !card || !header) return;

  header.addEventListener("pointerdown", (event) => {
    if (event.target.closest("button, input, select, textarea, a")) return;
    event.preventDefault();
    const startX = event.clientX;
    const startY = event.clientY;
    const startDx = parseFloat(card.dataset.dragX || "0");
    const startDy = parseFloat(card.dataset.dragY || "0");
    document.body.classList.add("dragging-system-settings");
    header.setPointerCapture(event.pointerId);

    const onMove = (moveEvent) => {
      const nx = startDx + (moveEvent.clientX - startX);
      const ny = startDy + (moveEvent.clientY - startY);
      card.style.transform = `translate(calc(-50% + ${nx}px), calc(-50% + ${ny}px))`;
      card.dataset.dragX = String(nx);
      card.dataset.dragY = String(ny);
    };

    const onUp = (upEvent) => {
      header.releasePointerCapture(upEvent.pointerId);
      header.removeEventListener("pointermove", onMove);
      header.removeEventListener("pointerup", onUp);
      document.body.classList.remove("dragging-system-settings");
    };

    header.addEventListener("pointermove", onMove);
    header.addEventListener("pointerup", onUp);
  });
}

async function openSystemSettings() {
  if (els.agentSettingsDrawer) els.agentSettingsDrawer.hidden = true;
  if (els.systemSettingsModal) els.systemSettingsModal.hidden = false;
  if (els.settingsBackdrop) els.settingsBackdrop.hidden = false;
  // 遮罩层显示后, 页面合成层变化可能清空 WebGL 缓冲; 强制地图重绘
  refreshMapAfterLayoutChange();
  const card = els.systemSettingsModal?.querySelector(".modal-card");
  if (card) {
    card.style.transform = "";
    card.dataset.dragX = "0";
    card.dataset.dragY = "0";
  }
  await Promise.all([
    loadApplicationSettings(true),
    loadConnectionSettings(true),
    loadCameraSettings(true),
    loadVehicleInfo(false),
  ]);
  updateVehicleSettingsAvailability();
  if (vehicleSettingsAvailable()) await loadVehicleSetup(false);
  selectedConnectionId = activeConnectionId || connectionsCache[0]?.id || "";
  renderConnectionsList();
  renderConnectionDetail(selectedConnectionId);
  renderVehicleSettingsPanel();
  setSystemSettingsSection(activeSystemSettingsSection || "links");
  renderSystemConnection();
  loadAirSimSettingsTemplates();
}

// ── AirSim settings.json（按连接类型联动，详情内嵌 + 共享 detail-footer 应用按钮） ──
let airsimTemplatesLoaded = false;
let airsimTemplatesCache = [];
let airsimTemplateSelected = "";

async function loadAirSimSettingsTemplates(force = false) {
  if (airsimTemplatesLoaded && !force) return;
  try {
    const data = await api("/api/airsim-settings");
    airsimTemplatesLoaded = true;
    airsimTemplatesCache = data.templates || [];
    renderAirSimSettingsForConnection();
  } catch (error) {
    console.warn("AirSim settings templates load failed:", error);
  }
}

function airsimTemplateForConnectionType(type) {
  // 连接预设 → 模板：AirSim → SimpleFlight; PX4 MAVLink(UDP/TCP/auto/serial) → UDP SITL; ROS2 → TCP 边端
  const mapping = {
    airsim: "airsim_simpleflight_multirotor",
    udp: "px4_mavlink_udp_sitl",
    tcp: "px4_mavlink_udp_sitl",
    auto: "px4_mavlink_udp_sitl",
    serial: "px4_mavlink_udp_sitl",
    px4_ros2: "px4_ros2_tcp_edge",
  };
  return mapping[String(type || "").toLowerCase()] || "";
}

function selectedConnectionTypeForTemplate() {
  // 1) cache 的 connection.type 最可靠 (持久化), 优先
  const detail = connectionsCache.find((c) => c.id === selectedConnectionId);
  if (detail?.type) return detail.type;
  // 2) 新建 (id 为空), 用表单 select 当前值
  if (els.connectionDetailType && els.connectionDetailType.value) {
    return els.connectionDetailType.value;
  }
  return latestState?.tool_runtime?.backend || "";
}

function renderAirSimSettingsForConnection() {
  const wrap = document.getElementById("airsimSettingsTemplates");
  const applyBtn = document.getElementById("airsimTemplateApply");
  const name = document.getElementById("airsimTemplateName");
  const code = document.getElementById("airsimTemplateCode");
  if (!wrap || !applyBtn) return;

  const rawType = selectedConnectionTypeForTemplate();
  const type = String(rawType || "").toLowerCase().trim();
  const matched = airsimTemplateForConnectionType(type);
  const template = airsimTemplatesCache.find((t) => t.id === matched) || null;
  airsimTemplateSelected = template?.id || "";

  // 给应用按钮 dataset 留一份最近一次的 (conn, type, template) 用于兜底/调试;
  // 不再把诊断信息写到可见 DOM 上.
  const connId = selectedConnectionId || "(无)";
  applyBtn.dataset.connId = connId;
  applyBtn.dataset.connectionType = type;
  applyBtn.dataset.templateId = airsimTemplateSelected;
  console.debug("[AirSim template] type=", type, "matched=", matched, "template=", template?.label, "connId=", connId);

  if (!template) {
    wrap.hidden = true;
    applyBtn.hidden = true;
    if (code) code.innerHTML = "";
    return;
  }
  wrap.hidden = false;
    applyBtn.hidden = false;
  if (name) name.textContent = template.label || "—";

  const raw = String(template.content || "");
  const formatted = formatAirSimSettingsJson(raw);
  if (code) {
    code.innerHTML = "";
    code.appendChild(buildHighlightedJsonLines(formatted));
  }
}

// ---- 配置预览美化: 行号 + 语法高亮 ----

function formatAirSimSettingsJson(raw) {
  if (!raw) return "";
  // 模板可能本来就是合法 JSON 字符串, 也可能是带注释或多余空格的近似 JSON.
  // 先尝试解析再 2 空格格式化; 失败则按原文逐行轻处理 (保留行结构, 但去掉空行).
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch (_) {
    return raw.replace(/\r\n/g, "\n").replace(/^\s*\n/gm, "").trimEnd();
  }
}

// 经典 JSON 语法高亮 (highlight.js 同款正则), 改为每行调用一次以保留行号结构.
const JSON_TOKEN_RE = /("(?:\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(?:\s*:)?|\b(?:true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+\-]?\d+)?|[{}\[\],])/g;

function highlightJsonLine(line) {
  if (!line) return "";
  // 先 HTML 转义, 再用正则匹配插入 span.
  const escaped = line
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  return escaped.replace(JSON_TOKEN_RE, (match) => {
    let cls = "";
    if (match.startsWith('"')) {
      cls = /:\s*$/.test(match) ? "json-key" : "json-string";
    } else if (/^(?:true|false)$/.test(match)) {
      cls = "json-boolean";
    } else if (match === "null") {
      cls = "json-null";
    } else if (/^-?\d/.test(match)) {
      cls = "json-number";
    } else {
      cls = "json-punct";
    }
    return `<span class="${cls}">${match}</span>`;
  });
}

function buildHighlightedJsonLines(text) {
  const ol = document.createElement("ol");
  ol.className = "airsim-template-lines";
  const lines = (text || "").split("\n");
  for (let i = 0; i < lines.length; i++) {
    const li = document.createElement("li");
    const ln = document.createElement("span");
    ln.className = "ln";
    ln.textContent = String(i + 1);
    const codeSpan = document.createElement("span");
    codeSpan.className = "code";
    const line = lines[i];
    codeSpan.innerHTML = line ? highlightJsonLine(line) : "<span class=\"empty\">·</span>";
    li.append(ln, codeSpan);
    ol.append(li);
  }
  return ol;
}

async function applyAirSimSettingsTemplate() {
  // 应用前再算一次, 同时从按钮 dataset 拉出最近一次 render 算出的 (type, templateId)
  // 任意两者任一非空都作为兜底, 拒绝使用完全 stale 的 airsimTemplateSelected.
  renderAirSimSettingsForConnection();

  const button = document.getElementById("airsimTemplateApply");
  const fallbackType = String(button?.dataset.connectionType || "").toLowerCase();
  const fallbackTemplate = String(button?.dataset.templateId || "");
  if (!airsimTemplateSelected) {
    if (fallbackTemplate && airsimTemplatesCache.some((t) => t.id === fallbackTemplate)) {
      airsimTemplateSelected = fallbackTemplate;
    } else {
      showNotice("当前连接类型没有可用的 AirSim settings 模板", "error");
      return;
    }
  }

  const type = String(selectedConnectionTypeForTemplate() || fallbackType || "").toLowerCase();
  const template = airsimTemplatesCache.find((t) => t.id === airsimTemplateSelected);
  const templateLabel = template?.label || airsimTemplateSelected;
  console.info(
    "[AirSim apply] connId=", button?.dataset.connId,
    "type=", type,
    "template=", airsimTemplateSelected
  );
  if (!confirm(`将备份当前 settings.json 并写入模板：${templateLabel}\n之后需重启 AirSim 生效。继续？`)) return;
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "写入中...";
  try {
    const result = await post("/api/airsim-settings/apply", { template: airsimTemplateSelected });
    const resultEl = document.getElementById("airsimTemplateResult");
    if (resultEl) {
      resultEl.hidden = false;
      resultEl.classList.toggle("error", !result.ok);
      resultEl.textContent = result.ok
        ? `已写入模板「${templateLabel}」到 Documents/AirSim/settings.json${result.backup_path ? "（原文件已备份）" : ""}`
        : `${result.error || "应用失败"}`;
    }
  } catch (error) {
    const resultEl = document.getElementById("airsimTemplateResult");
    if (resultEl) {
      resultEl.hidden = false;
      resultEl.classList.add("error");
      resultEl.textContent = `${error.message || "应用失败"}`;
    }
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

function initAirSimTemplatesEvents() {
  document.getElementById("airsimTemplateApply")?.addEventListener("click", applyAirSimSettingsTemplate);
}

function vehicleSettingsAvailable() {
  const runtime = latestState?.tool_runtime || {};
  const backend = String(runtime.backend || vehicleInfoCache?.backend || "");
  return Boolean(runtime.connected && !runtime.stale_connection && backend === "px4_mavlink");
}

function updateVehicleSettingsAvailability() {
  const runtime = latestState?.tool_runtime || {};
  const contract = runtime.operation_contract || {};
  const available = vehicleSettingsAvailable();
  const vehicleKind = String(contract.vehicle_kind || "");
  const source = available
    ? (vehicleKind === "real_px4" ? "REAL USB" : "PX4 SITL")
    : "OFFLINE";
  if (els.vehicleSettingsSource) {
    els.vehicleSettingsSource.textContent = source;
    els.vehicleSettingsSource.classList.toggle("connected", available);
  }
  (els.systemSettingsModal || document).querySelectorAll(".vehicle-only").forEach((button) => {
    button.disabled = !available;
    button.title = available ? `数据来源: ${source}` : "连接 MAVLink PX4 后可用（真实 USB 或 SITL）";
  });
  if (!available && isVehicleSetupSection(activeSystemSettingsSection)) {
    setSystemSettingsSection("links");
  }
}

function closeSystemSettings() {
  if (els.systemSettingsModal) els.systemSettingsModal.hidden = true;
  if (els.settingsBackdrop && (!els.agentSettingsDrawer || els.agentSettingsDrawer.hidden)) {
    els.settingsBackdrop.hidden = true;
  }
  resetSystemSettingsMaximize();
  stopVehicleSetupPolling();
  refreshMapAfterLayoutChange();
}

function setSettingsTab(tab, drawer = document) {
  drawer.querySelectorAll("[data-settings-tab]").forEach((button) => {
    button.classList.toggle("active", button.dataset.settingsTab === tab);
  });
  drawer.querySelectorAll("[data-settings-panel]").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.settingsPanel === tab);
  });
}

function setSystemSettingsSection(section) {
  if (isVehicleSetupSection(section) && !vehicleSettingsAvailable()) {
    showNotice("Vehicle Settings 需要已连接的 PX4 数据源", "error");
    section = "links";
  }
  activeSystemSettingsSection = section || "links";
  const modal = els.systemSettingsModal || document;
  modal.querySelectorAll("[data-system-section]").forEach((button) => {
    button.classList.toggle("active", button.dataset.systemSection === activeSystemSettingsSection);
  });
  modal.querySelectorAll("[data-system-panel]").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.systemPanel === activeSystemSettingsSection);
  });
  if (isVehicleSetupSection(activeSystemSettingsSection)) {
    renderActiveVehicleSetupPanel("section");
    startVehicleSetupPolling();
  } else {
    stopVehicleSetupPolling();
  }
  if (activeSystemSettingsSection === "vehicle") {
    renderVehicleSettingsPanel();
    loadVehicleInfo(false).catch(() => {});
  }
  if (activeSystemSettingsSection === "parameters") {
    renderVehicleParametersPanel();
    loadVehicleParameters(false).catch(() => {});
  }
  if (activeSystemSettingsSection === "links") {
    renderConnectionsList();
    renderConnectionDetail(selectedConnectionId);
  }
}

function screenToNed(px, py) {
  return {
    x: (mapTransform.cy - py) / mapTransform.scale,
    y: (px - mapTransform.cx) / mapTransform.scale,
  };
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

function findConstraints() {
  const run = latestState && latestState.current_run;
  const steps = run && run.plan ? run.plan.steps : [];
  for (const step of steps || []) {
    if (step.safety && step.safety.constraints) return step.safety.constraints;
  }
  return { max_altitude: 50, max_velocity: 8, geofence_radius: 100 };
}

function parseParams(raw) {
  if (!raw) return {};
  try {
    return JSON.parse(raw);
  } catch (_) {
    return {};
  }
}

function statusClass(status) {
  if (["failed", "blocked"].includes(status)) return "danger";
  if (["paused", "planned", "queued"].includes(status)) return "warn";
  if (["running", "responding", "completed"].includes(status)) return "strong";
  return "";
}

function fmt(value) {
  const n = Number(value || 0);
  return n.toFixed(Math.abs(n) >= 10 ? 0 : 1);
}

function round1(value) {
  return Math.round(Number(value || 0) * 10) / 10;
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function line(ctx, x1, y1, x2, y2) {
  ctx.beginPath();
  ctx.moveTo(x1, y1);
  ctx.lineTo(x2, y2);
  ctx.stroke();
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderMarkdown(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  const blocks = text.split(/\n{2,}/).map((block) => block.trim()).filter(Boolean);
  return blocks.map((block) => {
    const lines = block.split(/\n/).map((line) => line.trim()).filter(Boolean);
    if (lines.length && lines.every((line) => /^[-*]\s+/.test(line))) {
      return `<ul>${lines.map((line) => `<li>${renderInlineMarkdown(line.replace(/^[-*]\s+/, ""))}</li>`).join("")}</ul>`;
    }
    if (lines.length && lines.every((line) => /^\d+[.)]\s+/.test(line))) {
      return `<ol>${lines.map((line) => `<li>${renderInlineMarkdown(line.replace(/^\d+[.)]\s+/, ""))}</li>`).join("")}</ol>`;
    }
    // markdown 表格：首行表头，第二行 |---|---| 分隔，其余为数据行
    if (lines.length >= 2 && lines.every((line) => /^\|.*\|$/.test(line)) && /^\|[\s:|-]+\|$/.test(lines[1])) {
      const parseRow = (row) => row.replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim());
      const headers = parseRow(lines[0]);
      const rows = lines.slice(2).map(parseRow);
      const cell = (value, tag) => `<${tag}>${renderInlineMarkdown(value)}</${tag}>`;
      return `<table class="md-table"><thead><tr>${headers.map((h) => cell(h, "th")).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${headers.map((_, i) => cell(r[i] ?? "", "td")).join("")}</tr>`).join("")}</tbody></table>`;
    }
    const normalized = block.replace(/^#{1,6}\s+/gm, "");
    return `<p>${normalized.split(/\n/).map((line) => renderInlineMarkdown(line)).join("<br>")}</p>`;
  }).join("");
}

function renderInlineMarkdown(value) {
  return escapeHtml(value)
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
}

function showNotice(message, level = "info") {
  if (!els.notice) return;
  window.clearTimeout(noticeTimer);
  els.notice.hidden = false;
  els.notice.textContent = message;
  els.notice.className = `notice ${level}`;
  noticeTimer = window.setTimeout(() => {
    els.notice.hidden = true;
  }, level === "error" ? 6000 : 3200);
}

refresh().catch((error) => showNotice(error.message || "状态加载失败", "error"));

// ---------------------------------------------------------------------------
// 会话流渲染（增量持久节点版）
//
// 此前的全量 innerHTML 重建有两个致命问题：思考块的展开状态随重建丢失
// （"过一会自动折叠"）、整个对话闪烁。现在每条消息一个持久 DOM 节点
// （turnNodes 按 message.id 索引），增量更新内部区块：
//   ┌ turn ─────────────────────────────┐
//   │ ▸ 思考块（默认折叠，标题滚动最新一句，展开看全文）│
//   │ ✓ 工具/校验步骤（单行，追加式）              │
//   │ [最终回答 markdown（平滑分批释放）]           │
//   └───────────────────────────────────┘
// ---------------------------------------------------------------------------

const turnNodes = new Map();

function buildUserTurn(message) {
  const root = document.createElement("article");
  root.className = "chat-bubble user";
  root.dataset.messageId = message.id || "";
  const content = document.createElement("div");
  content.className = "bubble-content";
  const textEl = document.createElement("div");
  textEl.className = "bubble-text";
  content.appendChild(textEl);
  root.appendChild(content);
  return { root, textEl, kind: "user", lastContent: "" };
}

function updateUserTurn(entry, message) {
  const content = String(message.content || "");
  if (content === entry.lastContent) return;
  entry.lastContent = content;
  entry.textEl.innerHTML = `${renderMessageAttachments(message.attachments || [])}<p>${escapeHtml(content)}</p>`;
}

function buildAgentTurn(message) {
  const root = document.createElement("article");
  root.className = "chat-bubble agent turn";
  root.dataset.messageId = message.id || "";

  const errorPill = document.createElement("div");
  errorPill.className = "error-pill";
  errorPill.textContent = "任务执行失败，详见对话内容";
  errorPill.style.display = "none";

  // 思考块：默认折叠，标题行 = 状态 + 最新一句（两端渐隐由 CSS mask）
  const thinkFold = document.createElement("details");
  thinkFold.className = "think-fold";
  thinkFold.style.display = "none";
  const thinkSummary = document.createElement("summary");
  const thinkState = document.createElement("span");
  thinkState.className = "think-state";
  thinkState.textContent = "思考中…";
  const thinkLatest = document.createElement("span");
  thinkLatest.className = "think-latest";
  thinkSummary.appendChild(thinkState);
  thinkSummary.appendChild(thinkLatest);
  const thinkFull = document.createElement("pre");
  thinkFull.className = "think-full";
  thinkFold.appendChild(thinkSummary);
  thinkFold.appendChild(thinkFull);

  const toolLines = document.createElement("div");
  toolLines.className = "tool-lines";

  const answerBody = document.createElement("div");
  answerBody.className = "answer-body";

  root.appendChild(errorPill);
  root.appendChild(thinkFold);
  root.appendChild(toolLines);
  root.appendChild(answerBody);
  return {
    root,
    kind: "agent",
    errorPill,
    thinkFold,
    thinkState,
    thinkLatest,
    thinkFull,
    toolLines,
    answerBody,
    renderedTrace: 0,
    lastAnswer: "",
    lastReasoning: "",
    lastStatus: "",
  };
}

function latestThinkLine(text) {
  const t = String(text || "").trimEnd();
  const n = t.lastIndexOf("\n");
  const line = n === -1 ? t : t.slice(n + 1);
  return line.length > 90 ? line.slice(-90) : line;
}

function firstThinkLine(text) {
  const t = String(text || "").trim();
  const n = t.indexOf("\n");
  return (n === -1 ? t : t.slice(0, n)).slice(0, 90);
}

function toolLineNode(item) {
  const row = document.createElement("div");
  row.className = `tool-line ${item.status || "completed"} kind-${item.kind || "tool"}`;
  const badge = document.createElement("em");
  badge.className = "tool-badge";
  badge.textContent = processKindLabel(item.kind || "tool");
  const title = document.createElement("strong");
  title.textContent = item.tool ? humanToolLabel(item.tool, item.title) : humanThoughtTitle(item.title || "");
  const body = document.createElement("span");
  body.className = "tool-line-body";
  body.textContent = humanThoughtBody(item.body || "", item.tool || "");
  row.appendChild(badge);
  row.appendChild(title);
  if (body.textContent) row.appendChild(body);
  return row;
}

function updateAgentTurn(entry, message, run, llm) {
  const details = message.details || {};
  const reasoning = String(details.reasoning_text || "");
  const running = ["running", "responding", "queued"].includes(message.status);
  const isError = message.status === "error";

  // 错误徽标
  entry.errorPill.style.display = isError ? "" : "none";

  // 思考块：有推理内容才出现；运行中标题滚动最新一句，完成后定格首句
  if (reasoning) {
    entry.thinkFold.style.display = "";
    if (reasoning !== entry.lastReasoning) {
      entry.lastReasoning = reasoning;
      entry.thinkFull.textContent = reasoning;
      entry.thinkLatest.textContent = running ? latestThinkLine(reasoning) : firstThinkLine(reasoning);
      entry.thinkState.textContent = running ? "思考中…" : "已思考 · 点击查看全文";
      // 摘要行滚动到最新（running 时跟随句尾）
      entry.thinkLatest.scrollLeft = running ? entry.thinkLatest.scrollWidth : 0;
    }
  } else {
    entry.thinkFold.style.display = "none";
  }

  // 工具/校验步骤：增量追加（跳过推理类条目——已在思考块里）
  const linkedRun = run && message.run_id && run.run_id === message.run_id ? run : null;
  const processTrace = Array.isArray(linkedRun?.process_trace) && linkedRun.process_trace.length
    ? linkedRun.process_trace
    : (Array.isArray(details.process_trace) ? details.process_trace : []);
  const visible = processTrace.filter((item) => {
    if ((item.kind || "") === "reasoning") return false;
    if (item.tool === "memory_store") return false;
    const title = humanThoughtTitle(item.title || "");
    return Boolean(title) && !/模型思考|模型推理/.test(item.title || "");
  });
  while (entry.renderedTrace < visible.length) {
    entry.toolLines.appendChild(toolLineNode(visible[entry.renderedTrace]));
    entry.renderedTrace += 1;
  }

  // 正文：平滑分批释放（smoothShownContent），完成后为全量
  const text = smoothShownContent(message).trim();
  if (text !== entry.lastAnswer) {
    entry.lastAnswer = text;
    entry.answerBody.innerHTML = text ? renderMarkdown(text) : "";
    entry.answerBody.style.display = text ? "" : "none";
  }

  entry.root.classList.toggle("error", isError);
  entry.lastStatus = message.status;
}

function renderChat(messages, run, llm) {
  const serverMessages = Array.isArray(messages) ? messages : [];
  reconcilePendingMessages(serverMessages);
  const list = [...serverMessages, ...localPendingMessages];

  if (!list.length) {
    els.chatThread.innerHTML = `<div class="chat-empty">开始一段新的对话</div>`;
    turnNodes.clear();
    return;
  }
  if (els.chatThread.firstElementChild?.classList?.contains("chat-empty")) {
    els.chatThread.innerHTML = "";
  }

  const liveIds = new Set();
  for (const message of list) {
    const id = message.id || `idx_${message.role}_${list.indexOf(message)}`;
    liveIds.add(id);
    let entry = turnNodes.get(id);
    if (!entry) {
      entry = message.role === "user" ? buildUserTurn(message) : buildAgentTurn(message);
      entry.root.dataset.messageId = message.id || id;
      turnNodes.set(id, entry);
      els.chatThread.appendChild(entry.root);
    }
    if (entry.kind === "agent") updateAgentTurn(entry, message, run, llm);
    else updateUserTurn(entry, message);
  }
  for (const [id, entry] of [...turnNodes]) {
    if (!liveIds.has(id)) {
      entry.root.remove();
      turnNodes.delete(id);
    }
  }

  const scrollTargetId = pendingScrollTargetId;
  const shouldScroll = !scrollTargetId && (forceNextChatScroll || shouldStickToChatBottom());
  if (scrollTargetId) scrollMessageIntoView(scrollTargetId);
  else if (shouldScroll) scrollChatToEnd();
  forceNextChatScroll = false;
}

function scheduleChatRender() {
  if (chatRenderRafId) return;
  chatRenderRafId = window.requestAnimationFrame(() => {
    chatRenderRafId = 0;
    renderChat(latestState.messages || [], latestState.current_run, latestState.llm || {});
    syncCommandSubmitState();
  });
}

function reconcilePendingMessages(serverMessages = []) {
  if (!localPendingMessages.length) return;
  localPendingMessages = localPendingMessages.filter((pending) => {
    if (pending.role === "user") {
      return !serverMessages.some((message) => serverConfirmsPendingUser(message, pending));
    }
    if (pending.role === "assistant") {
      const pendingMode = pending.details?.mode || "";
      if (pending.run_id) {
        return !serverMessages.some((message) => message.role === "assistant" && message.run_id === pending.run_id);
      }
      return !serverMessages.some((message) =>
        message.role === "assistant" && (message.details?.mode || "") === pendingMode && message.status === "running"
      );
    }
    return true;
  });
}

function serverConfirmsPendingUser(message, pending) {
  if (!message || !pending || message.role !== "user" || pending.role !== "user") return false;
  if (String(message.content || "") !== String(pending.content || "")) return false;
  const pendingCreated = Number(pending.pending_created_at || pending.created_at || 0);
  const serverCreated = Number(message.created_at || message.updated_at || 0);
  if (!pendingCreated || !serverCreated) return false;
  return serverCreated >= pendingCreated - 0.001;
}

function removePendingForServerMessage(message) {
  if (!message || !localPendingMessages.length) return;
  if (message.role === "user") {
    const index = localPendingMessages.findIndex((pending) => serverConfirmsPendingUser(message, pending));
    if (index >= 0) {
      pendingScrollTargetId = message.id || pendingScrollTargetId;
      localPendingMessages.splice(index, 1);
    }
    return;
  }
  if (message.role === "assistant") {
    const mode = message.details?.mode || "";
    localPendingMessages = localPendingMessages.filter((pending) => {
      if (pending.role !== "assistant") return true;
      if (message.run_id && pending.run_id === message.run_id) return false;
      return pending.run_id || (pending.details?.mode || "") !== mode;
    });
  }
}

function renderChatMessage(message, run, llm) {
  const role = message.role === "user" ? "user" : "agent";
  if (role === "user") {
    return `
      <article class="chat-bubble user" data-message-id="${escapeHtml(message.id || "")}">
        <div class="bubble-content">
          <div class="bubble-text">
            ${renderMessageAttachments(message.attachments || [])}
            <p>${escapeHtml(message.content || "")}</p>
          </div>
        </div>
        <div class="bubble-actions">
          <button class="copy-btn" data-copy="${escapeHtml(message.content || "")}" title="复制">⧉</button>
        </div>
      </article>
    `;
  }

  const linkedRun = run && message.run_id && run.run_id === message.run_id ? run : null;
  const liveStatuses = ["running", "queued", "planned", "responding", "awaiting_approval"];
  const currentRunActive = linkedRun && liveStatuses.includes(linkedRun.status || "");
  const pendingActive = !message.run_id && String(message.id || "").startsWith("pending_agent_") && liveStatuses.includes(message.status || "");
  const chatActive = message.details?.mode === "chat" && liveStatuses.includes(message.status || "");
  const active = Boolean(currentRunActive || pendingActive || chatActive);
  const isError = message.status === "error";
  const thoughts = renderAgentThoughts(message, run, active);
  const details = renderAgentDetails(message, run, llm);
  // 平滑流式：流式中的消息只渲染已释放部分（分批淡入），完成后为全量
  const text = smoothShownContent(message).trim();
  const phase = linkedRun?.phase || message.details?.phase || "";
  const mode = linkedRun?.mode || message.details?.mode || "";
  const showThinkingPill = active && mode === "chat" && !thoughts;
  return `
    <article class="chat-bubble agent${isError ? " error" : ""}" data-message-id="${escapeHtml(message.id || "")}">
      ${isError ? `<div class="error-pill">任务执行失败，详见对话内容</div>` : ""}
      ${showThinkingPill ? `<div class="thinking-pill"><span class="live-dot"></span> ${escapeHtml(humanStatus(message.status, phase, mode))}</div>` : ""}
      ${thoughts}
      ${text ? `<div class="agent-message">${renderMarkdown(text)}</div>` : ""}
      ${details}
      <div class="bubble-actions">
        <button class="copy-btn" data-copy="${escapeHtml(message.content || "")}" title="复制">⧉</button>
      </div>
    </article>
  `;
}

function renderMessageAttachments(attachments) {
  if (!Array.isArray(attachments) || !attachments.length) return "";
  const images = attachments.map((item) => {
    const src = String(item.url || item.data_url || "");
    if (!(src.startsWith("/api/attachments/") || src.startsWith("data:image/"))) return "";
    return `<img src="${escapeHtml(src)}" alt="${escapeHtml(item.name || "attached image")}" loading="lazy">`;
  }).filter(Boolean).join("");
  return images ? `<div class="message-images">${images}</div>` : "";
}

function renderAgentThoughts(message, run, active) {
  const linkedRun = run && message.run_id && run.run_id === message.run_id ? run : null;
  const mode = linkedRun?.mode || message.details?.mode || "";
  const plan = linkedRun?.plan || message.details?.plan;
  const loopState = linkedRun?.loop_state || message.details?.loop_state || {};
  const processTrace = Array.isArray(linkedRun?.process_trace) && linkedRun.process_trace.length
    ? linkedRun.process_trace
    : (Array.isArray(message.details?.process_trace) ? message.details.process_trace : []);
  const decisions = Array.isArray(loopState?.decisions) ? loopState.decisions : [];
  const results = Array.isArray(loopState?.results) ? loopState.results : [];
  const processRows = [];
  if (processTrace.length) {
    processTrace.forEach((item) => {
      const toolName = String(item?.tool || "");
      if (toolName === "memory_store") return;
      const title = item?.tool ? humanToolLabel(item.tool, item.title) : humanThoughtTitle(item?.title || "");
      const body = humanThoughtBody(item?.body || "", item?.tool || "");
      if (title || body) {
        processRows.push({
          status: item?.status || "completed",
          kind: normalizeProcessKind(item),
          title: title || "处理任务",
          body,
        });
      }
    });
  }
  if (!processRows.length) {
    decisions.forEach((decision, index) => {
      const action = String(decision?.action || "");
      if (!action || action === "memory_store") return;
      const result = results.find((item) => Number(item.step_index || 0) === index + 1);
      const status = result ? (result.ok ? "completed" : "failed") : (active ? "running" : "planned");
      processRows.push({
        status,
        kind: "tool",
        title: humanToolLabel(action),
        body: humanDecisionReason(decision.reason || result?.data?.message || "", action),
      });
    });
  }
  const hasToolStep = (plan?.steps || []).some((step) => step?.tool && step.tool !== "memory_store");
  if (!processRows.length && hasToolStep) {
    (plan.steps || []).forEach((step) => {
      if (!step?.tool || step.tool === "memory_store") return;
      processRows.push({
        status: step.status || "planned",
        kind: "tool",
        title: humanToolLabel(step.tool, step.label),
        body: step.result?.message || step.result?.status || "",
      });
    });
  }
  if (!processRows.length) {
    const runTrace = linkedRun?.thought_trace;
    const trace = Array.isArray(runTrace) && runTrace.length ? runTrace : (message.details?.thought_trace || []);
    (Array.isArray(trace) ? trace : []).forEach((item) => {
      const tool = item?.tool || "";
      const title = tool ? humanToolLabel(tool) : humanThoughtTitle(item?.title || "");
      const body = humanThoughtBody(item?.body || item?.title || "", tool);
      if (title || body) {
        processRows.push({
          status: item?.status || "completed",
          kind: normalizeProcessKind(item),
          title: title || "处理任务",
          body,
        });
      }
    });
  }
  if (!processRows.length && message.status !== "error") return "";
  const detailId = `thought_${message.id || message.run_id || ""}`;
  // 过程细节默认始终展开（主流 Agent 风格：思考与工具过程直接可回看），
  // 用户可手动收起；不再在任务完成后自动折叠
  const openAttr = " open";
  const phase = linkedRun?.phase || message.details?.phase || "";
  const summary = active ? "正在处理..." : `已处理${humanRunDuration(linkedRun, message)}`;
  const rows = processRows.map((item) => {
    const status = item.status || "completed";
    const kind = item.kind || "reasoning";
    const badge = processKindLabel(kind);
    const bodyText = String(item.body || "");
    // 长推理文本保留换行（模型思考块可回看完整内容）
    // 长推理文本默认折叠（平铺全文刷屏难受）；短摘要照常平铺
    const isLongReasoning = kind === "reasoning" && (bodyText.length > 200 || bodyText.includes("\n"));
    const body = !bodyText
      ? ""
      : isLongReasoning
        ? `<details class="reasoning-fold"><summary>模型思考 · 点击展开全文</summary><pre class="fold-body">${escapeHtml(bodyText)}</pre></details>`
        : `<p class="${bodyText.length > 160 ? "long-text" : ""}">${escapeHtml(bodyText)}</p>`;
    return `
      <div class="thought-row ${escapeHtml(status)} kind-${escapeHtml(kind)}">
        <span></span>
        <div>
          <strong><em>${escapeHtml(badge)}</em>${escapeHtml(item.title || "思考")}</strong>
          ${body}
        </div>
      </div>
    `;
  }).join("");
  return `
    <details class="thought-block message-detail agent-thoughts" data-detail-id="${escapeHtml(detailId)}"${openAttr}>
      <summary>${active ? `<span class="live-dot"></span>` : ""}${escapeHtml(summary)}</summary>
      <div class="thought-body live-process">${rows}</div>
    </details>
  `;
}

function normalizeProcessKind(item) {
  const explicit = String(item?.kind || "").trim().toLowerCase();
  if (["reasoning", "tool", "verify", "memory", "system"].includes(explicit)) return explicit;
  const title = String(item?.title || "").toLowerCase();
  if (item?.tool) return "tool";
  if (/校验|verify|回读/.test(title)) return "verify";
  return "reasoning";
}

function processKindLabel(kind) {
  if (kind === "tool") return "工具";
  if (kind === "verify") return "校验";
  if (kind === "memory") return "记忆";
  if (kind === "system") return "系统";
  return "模型";
}

function hasProcessTrace(message, run) {
  const linkedRun = run && message.run_id && run.run_id === message.run_id ? run : null;
  return Boolean(
    (Array.isArray(linkedRun?.process_trace) && linkedRun.process_trace.length) ||
    (Array.isArray(message.details?.process_trace) && message.details.process_trace.length)
  );
}

function humanRunDuration(run, message) {
  const started = Number(run?.started_at || message?.details?.started_at || message?.created_at || 0);
  const finished = Number(run?.finished_at || message?.details?.finished_at || message?.updated_at || 0);
  if (!started || !finished || finished < started) return "";
  const seconds = Math.max(0, Math.round(finished - started));
  if (seconds < 1) return " <1s";
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  if (!minutes) return ` ${seconds}s`;
  return ` ${minutes}m ${rest}s`;
}

function humanThoughtTitle(title) {
  const text = String(title || "").trim();
  if (/循环决策/i.test(text)) return "选择下一步动作";
  if (/理解|understand/i.test(text)) return "理解任务";
  if (/工具|tool/i.test(text)) return "选择工具";
  if (/校验|verify/i.test(text)) return "校验结果";
  return text;
}

function humanThoughtBody(body, tool = "") {
  const text = String(body || "").trim();
  if (tool) return humanDecisionReason(text, tool);
  return humanDecisionReason(text);
}

function renderAgentDetails(message, run, llm) {
  return "";
}

function renderAgentState(agentState) {
  if (!agentState || typeof agentState !== "object") return "";
  const vehicle = agentState.vehicle || {};
  const pos = vehicle.position_ned || {};
  const rows = [
    ["后端", agentState.backend_name || agentState.backend || "-"],
    ["连接", agentState.connected ? "已连接" : (agentState.stale_connection ? "异常/过期" : "未连接")],
    ["Ready", String(agentState.ready === true)],
    ["Armed", valueOrDash(vehicle.armed)],
    ["Flying", valueOrDash(vehicle.flying)],
    ["位置", typeof pos === "object" && Object.keys(pos).length ? `x=${valueOrDash(pos.x)} y=${valueOrDash(pos.y)} z=${valueOrDash(pos.z)}` : "-"],
    ["碰撞", valueOrDash(vehicle.has_collided ?? vehicle.collision)],
  ];
  const activeRun = agentState.active_run;
  if (activeRun) {
    rows.push(["当前任务", `${activeRun.phase || activeRun.status || "-"} · ${valueOrDash(activeRun.progress)}%`]);
  }
  const activeOperation = agentState.active_operation;
  if (activeOperation) {
    rows.push([
      "后台操作",
      `${activeOperation.status || "running"}${activeOperation.task_id ? ` · ${activeOperation.task_id}` : ""}`,
    ]);
  }
  return `
    <div class="detail-note agent-state-note">
      <strong>Agent 状态感知</strong>
      <div class="agent-state-grid">
        ${rows.map(([label, value]) => `
          <span>${escapeHtml(label)}</span>
          <code>${escapeHtml(String(value))}</code>
        `).join("")}
      </div>
    </div>
  `;
}

function valueOrDash(value) {
  return value === undefined || value === null || value === "" ? "-" : value;
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

function humanDecisionReason(reason, action = "") {
  const text = String(reason || "").trim();
  const normalized = text.toLowerCase();
  if (action === "airsim_take_photo" || normalized.includes("capture the current camera frame")) {
    return "获取当前摄像头画面";
  }
  if (action === "airsim_vlm_analyze_image" || normalized.includes("analyze the captured camera frame")) {
    return "调用所选多模态模型分析画面";
  }
  if (action === "airsim_vlm_confirm_target" || normalized.includes("confirm the requested target")) {
    return "确认画面中是否存在目标";
  }
  if (normalized.includes("visual analysis/confirmation has completed")) {
    return "视觉分析已完成，准备输出结果";
  }
  if (normalized.includes("target is visible") && normalized.includes("2d image target")) {
    return "目标只在二维画面中确认，缺少安全飞行所需的三维位置";
  }
  return text;
}

function renderSkillSubTools(result) {
  const toolResults = result?.data?.tool_results || result?.data?.accepted_result?.tool_results;
  if (!Array.isArray(toolResults) || !toolResults.length) return "";
  return `
    <div class="sub-tool-list">
      ${toolResults.map((item) => {
        const ok = item.ok === true;
        const tool = item.tool || item.name || "tool";
        const message = item.data?.message || item.data?.status || "";
        return `<span class="${ok ? "ok" : "fail"}">${escapeHtml(tool)}${message ? ` · ${escapeHtml(message)}` : ""}</span>`;
      }).join("")}
    </div>
  `;
}



function renderPendingCommand(command, mode = "chat", attachments = []) {
  const serial = ++pendingMessageCounter;
  const isExecute = mode === "execute";
  const createdAt = Date.now() / 1000;
  const userId = `pending_user_${Date.now()}_${serial}`;
  const agentId = `pending_agent_${Date.now()}_${serial}`;
  localPendingMessages.push(
    {
      id: userId,
      role: "user",
      content: command,
      attachments,
      status: "complete",
      pending: true,
      created_at: createdAt,
      pending_created_at: createdAt,
    },
    {
      id: agentId,
      role: "assistant",
      content: isExecute ? "正在准备任务..." : "正在生成回复...",
      status: "running",
      pending: true,
      created_at: createdAt,
      pending_created_at: createdAt,
      details: {
        mode,
        phase: isExecute ? "understanding" : "responding",
        thought_trace: [{
          title: isExecute ? "理解指令" : "读取上下文",
          body: isExecute ? "正在准备进入规划与执行流程。" : "Chat 模式正在基于上下文生成回复。",
          status: "running",
        }],
      },
    },
  );
  pendingScrollTargetId = userId;
  forceNextChatScroll = true;
  renderChat(latestState?.messages || [], latestState?.current_run || null, latestState?.llm || {});
  return { userId, agentId };
}

function bindPendingRunId(agentId, runId) {
  const pending = localPendingMessages.find((message) => message.id === agentId);
  if (!pending) return;
  pending.run_id = runId;
  renderChat(latestState?.messages || [], latestState?.current_run || null, latestState?.llm || {});
}

function clearPendingCommand(pendingCommand = {}) {
  const ids = new Set([pendingCommand.userId, pendingCommand.agentId].filter(Boolean));
  if (!ids.size) return;
  localPendingMessages = localPendingMessages.filter((message) => !ids.has(message.id));
  renderChat(latestState?.messages || [], latestState?.current_run || null, latestState?.llm || {});
}

function humanStatus(status, phase = "", mode = "") {
  if (mode === "chat" && status === "running") return "Chat 回复中...";
  if (phase === "understanding") return "正在理解...";
  if (phase === "planning") return "正在规划...";
  if (phase === "executing") return "正在执行...";
  if (phase === "verifying") return "正在校验...";
  if (phase === "responding") return "正在整理结果...";
  if (phase === "awaiting_approval") return "等待确认...";
  if (status === "planned") return "正在规划...";
  if (status === "queued") return "等待执行...";
  if (status === "running") return "正在处理...";
  if (status === "responding") return "正在整理结果...";
  return "处理中...";
}

function openModelModal(modelId) {
  closeAllDropdowns();
  const model = modelId ? modelsCache.find((m) => m.id === modelId) : null;
  els.modelEditId.value = model ? model.id : "";
  els.modelModalTitle.textContent = model ? "编辑模型" : "添加模型";
  els.modelName.value = model ? model.name || "" : "";
  els.modelProvider.value = model ? model.provider || "" : "";
  els.modelModelId.value = model ? model.model || "" : "";
  els.modelApiType.value = model ? model.api_type || "openai" : "openai";
  els.modelBaseUrl.value = model ? model.base_url || "" : "";
  els.modelApiKey.value = "";
  els.modelApiKey.type = "password";
  els.modelApiKey.placeholder = model?.key_hint
    ? `已保存 ${model.key_hint}，留空保持不变`
    : "输入 API Key";
  if (els.modelReasoningEffort) els.modelReasoningEffort.value = model?.reasoning_effort || "";
  if (els.modelThinkingMode) els.modelThinkingMode.value = model?.thinking_mode || "";
  if (els.modelRevealKey) {
    els.modelRevealKey.hidden = !model?.enabled;
    els.modelRevealKey.textContent = "显示";
  }
  els.modelModal.hidden = false;
}

function closeModelModal() {
  els.modelModal.hidden = true;
  if (els.modelApiKey) {
    els.modelApiKey.type = "password";
    els.modelApiKey.value = "";
  }
  if (els.modelRevealKey) els.modelRevealKey.textContent = "显示";
  if (els.modelForm) els.modelForm.reset();
}

async function submitModelForm() {
  const isEdit = Boolean(els.modelEditId.value.trim());
  const payload = {
    id: els.modelEditId.value.trim(),
    name: els.modelName.value.trim(),
    provider: els.modelProvider.value.trim(),
    model: els.modelModelId.value.trim(),
    api_type: els.modelApiType.value,
    base_url: els.modelBaseUrl.value.trim(),
  };
  if (els.modelReasoningEffort) payload.reasoning_effort = els.modelReasoningEffort.value || "";
  if (els.modelThinkingMode) payload.thinking_mode = els.modelThinkingMode.value || "";
  const apiKey = els.modelApiKey.value.trim();
  if (apiKey || !isEdit) {
    payload.api_key = apiKey;
  }
  if (!payload.name || !payload.provider || !payload.model) {
    showNotice("请填写模型名称、Provider 和模型 ID", "error");
    return;
  }
  try {
    await saveModelToBackend(payload);
    await fetchModels();
    closeModelModal();
    if (els.agentSettingsDrawer && !els.agentSettingsDrawer.hidden) {
      renderModelConfig();
    }
    showNotice(isEdit ? "模型已更新" : "模型已添加", "success");
  } catch (error) {
    showNotice(error.message || "保存模型失败", "error");
  }
}

function renderModelConfig() {
  const models = loadModels();
  const list = document.getElementById("modelConfigList");
  if (!models.length) {
    list.innerHTML = `<div class="empty">暂无模型配置</div>`;
    return;
  }

  list.innerHTML = models.map((m) => `
    <div class="model-config-item" data-model-id="${escapeHtml(m.id)}">
      <div class="config-row">
        <label>名称</label>
        <span class="config-value">${escapeHtml(m.name)}</span>
      </div>
      <div class="config-row">
        <label>Provider</label>
        <span class="config-value">${escapeHtml(m.provider)}</span>
      </div>
      <div class="config-row">
        <label>Model</label>
        <span class="config-value">${escapeHtml(m.model)}</span>
      </div>
      <div class="config-row">
        <label>API 类型</label>
        <span class="config-value">${escapeHtml(m.api_type || "openai")}</span>
      </div>
      <div class="config-row">
        <label>Base URL</label>
        <span class="config-value">${escapeHtml(m.base_url || "—")}</span>
      </div>
      <div class="config-row">
        <label>状态</label>
        <span class="config-value ${m.enabled ? "enabled" : "disabled"}">${m.enabled ? `已配置 ${escapeHtml(m.key_hint || "Key")}` : "未配置 Key"}</span>
      </div>
      <div class="config-actions">
        <button class="edit-model" data-action="edit" data-model-id="${escapeHtml(m.id)}">编辑</button>
        <button class="delete-model" data-action="delete" data-model-id="${escapeHtml(m.id)}">删除</button>
      </div>
    </div>
  `).join("");

  list.querySelectorAll("[data-action='edit']").forEach((btn) => {
    btn.addEventListener("click", (event) => {
      const modelId = event.target.closest("[data-model-id]").dataset.modelId;
      openAgentSettings();
      setSettingsTab("llm", els.agentSettingsDrawer);
      openModelModal(modelId);
    });
  });

  list.querySelectorAll("[data-action='delete']").forEach((btn) => {
    btn.addEventListener("click", async (event) => {
      const modelId = event.target.closest("[data-model-id]").dataset.modelId;
      if (!modelId) return;
      if (modelsCache.length <= 1) {
        showNotice("至少保留一个模型配置", "error");
        return;
      }
      try {
        await deleteModelFromBackend(modelId);
        await fetchModels();
        renderModelConfig();
      } catch (error) {
        showNotice(error.message || "删除模型失败", "error");
      }
    });
  });
}

function scrollChatToEnd() {
  requestAnimationFrame(() => {
    els.chatThread.scrollTop = els.chatThread.scrollHeight;
  });
}

function scrollMessageIntoView(messageId) {
  requestAnimationFrame(() => {
    const target = [...els.chatThread.querySelectorAll("[data-message-id]")]
      .find((item) => item.getAttribute("data-message-id") === messageId);
    if (!target) {
      pendingScrollTargetId = "";
      scrollChatToEnd();
      return;
    }
    target.scrollIntoView({ block: "center", inline: "nearest" });
    pendingScrollTargetId = "";
  });
}

function shouldStickToChatBottom() {
  if (!els.chatThread) return true;
  const distance = els.chatThread.scrollHeight - els.chatThread.scrollTop - els.chatThread.clientHeight;
  return distance < 96;
}

// ---------------------------------------------------------------------------
// Smooth streaming（借鉴 dsh-plugin-smooth-stream 的分批呈现算法）
//
// LLM 的 delta 到达速度远快于人阅读速度。这里不再逐 delta 全量重绘，而是：
//   1. delta 只更新目标内容（targets），渲染循环每 160ms 释放一批；
//   2. 释放点选在段落/行边界，且绝不切在未闭合代码块或表格中间
//      （extendToSafeMarkdown），Markdown 永远不会渲染到一半；
//   3. 新释放的正文带淡入动画；流式期间匀速跟随滚动，用户上滚即交还控制。
// ---------------------------------------------------------------------------

const smoothStream = {
  targets: new Map(),
  shown: new Map(),
  timer: null,
};

function isFenceLine(line) {
  return /^\s*(```|~~~)/.test(line);
}

function isTableLine(line) {
  return /^\s*\|.*\|\s*$/.test(line);
}

function extendToSafeMarkdown(text, pos) {
  if (pos >= text.length) return pos;
  let fenceFrom = -1;
  let inFence = false;
  let inTable = false;
  let tableFrom = -1;
  const lines = text.split("\n");
  let offset = 0;
  for (const line of lines) {
    const start = offset;
    offset += line.length + 1;
    if (isFenceLine(line)) {
      inFence = !inFence;
      if (inFence) fenceFrom = start;
      else fenceFrom = -1;
      inTable = false;
      tableFrom = -1;
      if (start >= pos) break;
      continue;
    }
    if (!inFence && isTableLine(line)) {
      if (!inTable) {
        inTable = true;
        tableFrom = start;
      }
    } else if (inTable && line.trim() === "") {
      inTable = false;
      tableFrom = -1;
    } else if (inTable && !isTableLine(line)) {
      inTable = false;
      tableFrom = -1;
    }
    if (start >= pos) break;
  }
  if (inFence) return fenceFrom > 0 ? fenceFrom : pos;
  if (inTable) return tableFrom > 0 ? tableFrom : pos;
  return pos;
}

function smoothParagraphTarget(text, shown, minChars = 24) {
  const need = shown + minChars;
  if (text.length < need) return shown;
  let pos = -1;
  const para = text.indexOf("\n\n", need);
  if (para !== -1) pos = para + 2;
  else {
    const nl = text.indexOf("\n", need);
    if (nl !== -1) pos = nl + 1;
  }
  if (pos === -1) return shown;
  pos = extendToSafeMarkdown(text, pos);
  return pos > shown ? pos : shown;
}

function smoothQueueDelta(message) {
  const id = message.id || message.run_id;
  if (!id) return;
  smoothStream.targets.set(id, String(message.content || ""));
  if (!smoothStream.shown.has(id)) smoothStream.shown.set(id, 0);
  smoothStartLoop();
}

function smoothFlushMessage(id) {
  smoothStream.targets.delete(id);
  smoothStream.shown.delete(id);
}

// 渲染层取该消息当前应显示的内容（流式中的消息显示已释放部分）
function smoothShownContent(message) {
  const id = message.id || message.run_id;
  if (id && smoothStream.targets.has(id)) {
    const target = smoothStream.targets.get(id) || "";
    const shown = smoothStream.shown.get(id) || 0;
    return target.slice(0, shown);
  }
  return String(message.content || "");
}

function smoothStartLoop() {
  if (smoothStream.timer) return;
  smoothStream.timer = window.setInterval(() => {
    let active = false;
    for (const [id, target] of smoothStream.targets) {
      const shown = smoothStream.shown.get(id) || 0;
      if (shown >= target.length) continue;
      active = true;
      const pos = smoothParagraphTarget(target, shown);
      if (pos > shown) {
        smoothStream.shown.set(id, pos);
        scheduleChatRender();
        if (shouldStickToChatBottom() && els.chatThread) {
          els.chatThread.scrollTop = els.chatThread.scrollHeight;
        }
      }
    }
    if (!active) {
      window.clearInterval(smoothStream.timer);
      smoothStream.timer = null;
    }
  }, 160);
}

function connectEventStream() {
  if (!window.EventSource || streamSource) return;
  streamSource = new EventSource("/api/stream");

  ["snapshot", "message_create", "message_update", "message_delta", "run_update", "runtime_event", "task_runs_update"].forEach((name) => {
    streamSource.addEventListener(name, (event) => handleStreamEvent(name, parseStreamData(event)));
  });

  streamSource.onerror = () => {
    if (streamSource) {
      streamSource.close();
      streamSource = null;
    }
    window.clearTimeout(streamReconnectTimer);
    streamReconnectTimer = window.setTimeout(connectEventStream, 1200);
  };
}

function parseStreamData(event) {
  try {
    return JSON.parse(event.data || "{}");
  } catch (_) {
    return {};
  }
}

function rosTelemetryStreamBaseUrl() {
  const runtime = latestState?.tool_runtime || {};
  const backend = String(runtime.backend || runtime.backend_profile?.id || "").toLowerCase();
  if (backend !== "px4_ros2") return "";
  if (!runtime.connected || runtime.stale_connection) return "";
  const drone = runtime.drone || {};
  const activeLink = drone.active_link || currentActualLink() || {};
  const activeConnection = connectionsCache.find((connection) => connection.id === activeConnectionId) || null;
  const url =
    activeLink.url ||
    activeLink.ros_bridge_url ||
    runtime.backend_profile?.capabilities?.ros_bridge_url ||
    activeConnection?.params?.url ||
    "";
  return normalizeRosGatewayUrl(url);
}

function rosTelemetryStreamUrl() {
  const baseUrl = rosTelemetryStreamBaseUrl();
  if (!baseUrl) return "";
  return `${baseUrl.replace(/\/+$/, "")}/providers/px4/telemetry/stream?hz=20`;
}

function closeRosTelemetryStream() {
  window.clearTimeout(rosTelemetryReconnectTimer);
  rosTelemetryReconnectTimer = null;
  rosTelemetryConnected = false;
  rosTelemetryUrl = "";
  if (latestState?.tool_runtime) {
    latestState.tool_runtime.telemetry_stream_active = false;
  }
  if (rosTelemetrySource) {
    rosTelemetrySource.close();
    rosTelemetrySource = null;
  }
}

function syncRosTelemetryStream() {
  if (!window.EventSource) return;
  const url = rosTelemetryStreamUrl();
  if (!url) {
    closeRosTelemetryStream();
    return;
  }
  if (rosTelemetrySource && rosTelemetryUrl === url) return;
  closeRosTelemetryStream();
  rosTelemetryUrl = url;
  rosTelemetrySource = new EventSource(url);
  rosTelemetrySource.addEventListener("telemetry", handleRosTelemetryEvent);
  rosTelemetrySource.onerror = () => {
    if (rosTelemetrySource) {
      rosTelemetrySource.close();
      rosTelemetrySource = null;
    }
    rosTelemetryConnected = false;
    window.clearTimeout(rosTelemetryReconnectTimer);
    rosTelemetryReconnectTimer = window.setTimeout(syncRosTelemetryStream, 1500);
  };
}

function handleRosTelemetryEvent(event) {
  const payload = parseStreamData(event);
  const data = payload?.data && typeof payload.data === "object" ? payload.data : {};
  if (!latestState || !data) return;
  const runtime = latestState.tool_runtime || {};
  const backend = String(runtime.backend || runtime.backend_profile?.id || "").toLowerCase();
  if (backend !== "px4_ros2") return;
  const previousDrone = runtime.drone || {};
  latestState.tool_runtime = {
    ...runtime,
    connected: payload.ok !== false,
    stale_connection: false,
    telemetry_stream_active: true,
    telemetry_stream_received_at_ms: Date.now(),
    drone: {
      ...previousDrone,
      ...data,
      active_link: previousDrone.active_link || currentActualLink(),
    },
  };
  rosTelemetryConnected = true;
  const toolRuntime = latestState.tool_runtime || {};
  const drone = toolRuntime.drone || {};
  renderTopbar(latestState.current_run, toolRuntime, latestState.supervisor || {}, latestState.llm || {});
  renderTelemetry(drone, toolRuntime);
  updateMapView(latestState);
  renderActualLinkCard();
}

function handleStreamEvent(type, payload) {
  if (!payload) return;
  if (type === "snapshot") {
    latestState = applyCachedSessionHistory(payload);
    render(latestState);
    loadCurrentSessionHistory().catch(() => {});
    syncRosTelemetryStream();
    return;
  }

  if (!latestState) return;

  if (type === "message_create" || type === "message_update") {
    upsertMessage(payload);
    if (payload.message && ["complete", "error", "cancelled"].includes(payload.message.status)) {
      smoothFlushMessage(payload.message.id);
    }
    scheduleChatRender();
    return;
  }

  if (type === "message_delta") {
    if (payload.message) {
      upsertMessage(payload.message);
      smoothQueueDelta(payload.message);
    } else {
      updateMessageContent(payload.id, payload.content);
    }
    scheduleChatRender();
    return;
  }

  if (type === "run_update") {
    latestState.current_run = payload;
    latestState.runtime = latestState.runtime || {};
    latestState.runtime.status = payload.status || latestState.runtime.status;
    render(latestState);
    syncRosTelemetryStream();
    return;
  }

  if (type === "runtime_event") {
    latestState.events = latestState.events || [];
    latestState.events.push(payload);
    latestState.events = latestState.events.slice(-80);
    renderEvents(latestState.events);
    return;
  }

  if (type === "task_runs_update") {
    latestState.task_runs = payload;
    latestState.memory = latestState.memory || {};
    latestState.memory.task_runs = payload;
    renderTaskRuns(payload);
    renderMemory(latestState.memory);
  }
}

function upsertMessage(message) {
  if (!message || !message.id) return;
  removePendingForServerMessage(message);
  latestState.messages = latestState.messages || [];
  const index = latestState.messages.findIndex((item) => item.id === message.id);
  if (index >= 0) latestState.messages[index] = message;
  else latestState.messages.push(message);
  const sessionId = latestState?.current_session?.id || "";
  if (sessionId && fullSessionMessageCache.has(sessionId)) {
    fullSessionMessageCache.set(sessionId, mergeSessionMessages(fullSessionMessageCache.get(sessionId), [message]));
  }
}

function updateMessageContent(id, content) {
  if (!id || content == null || !latestState?.messages) return;
  const message = latestState.messages.find((item) => item.id === id);
  if (message) message.content = content;
}

function normalizeAgentSettingsCopy() {
  const drawer = els.agentSettingsDrawer;
  if (!drawer) return;
  const title = drawer.querySelector(".settings-head strong");
  if (title) title.textContent = "Agent 设置";
  const close = drawer.querySelector("#agentSettingsClose");
  if (close) {
    close.textContent = "×";
    close.title = "关闭";
  }
  const tabCopy = {
    plan: ["任务运行", "当前任务进度与历史复盘"],
    events: ["审计日志", "路由、工具、审批与错误记录"],
    tools: ["工具", "按能力域分类的可调用操作"],
    memory: ["记忆", "上下文、运行态与长期任务经验"],
    llm: ["模型", "LLM 与视觉模型配置"],
    skills: ["技能", "可导入和编辑的无人机操作规程"],
  };
  Object.entries(tabCopy).forEach(([key, copy]) => {
    const button = drawer.querySelector(`[data-settings-tab="${key}"]`);
    if (!button) return;
    button.title = copy[1];
    const span = button.querySelector("span");
    if (span) span.textContent = copy[0];
  });
  const panelCopy = {
    plan: [
      "任务运行",
      "展示当前 Execute 的计划、风险、进度与校验结果；最近任务记录可用于复盘，跨会话经验进入记忆。",
    ],
    events: [
      "审计日志",
      "记录本次服务运行中的模式路由、工具调用、人工审批、结果校验与错误，便于定位真实飞行和仿真问题。",
    ],
    tools: [
      "工具目录",
      "工具按链路、遥测、飞控、导航、任务、感知与安全分类；实际可用性由当前 AirSim、MAVLink 或 ROS2 后端决定。",
    ],
    memory: [
      "记忆管理",
      "完整会话持久化保存；模型上下文按所选模型 token 窗口动态装载。运行态、任务经验、风险与 Skill 候选分开管理。",
    ],
    llm: [
      "模型配置",
      "支持 OpenAI-compatible 与 Anthropic API。输入能力和上下文窗口默认按模型 ID 自动识别，也可手动覆盖。",
    ],
    skills: [
      "技能库",
      "Skill 是面向无人机任务的操作规程，可新建、导入、编辑和停用；执行时只允许使用当前后端具备的工具。",
    ],
  };
  Object.entries(panelCopy).forEach(([key, copy]) => {
    const panel = drawer.querySelector(`[data-settings-panel="${key}"]`);
    if (!panel) return;
    const strong = panel.querySelector(".settings-card > header strong");
    const help = panel.querySelector(".settings-help");
    if (strong) strong.textContent = copy[0];
    if (help) help.textContent = copy[1];
  });
}

function backendModeView(toolRuntime = {}) {
  const profile = toolRuntime.backend_profile || {};
  const capabilities = profile.capabilities || {};
  const mode = profile.mode || profile.id || toolRuntime.backend || "unknown";
  const settings = profile.agent_settings || {};
  if (mode === "px4_ros2") {
    return {
      title: "PX4 ROS2 网关模式",
      body: "系统通过 HTTP 连接 ROS Provider Gateway；网关运行在 WSL 或机载计算机上，负责 ROS2 话题并通过 PX4 /fmu 话题闭环。",
      tags: [
        "ROS2 网关",
        profile.requires_ros_gateway ? "必须连接网关" : "网关可选",
        settings.ros_gateway_url || capabilities.ros_bridge_url || "http://127.0.0.1:8766",
        settings.ros_workspace || "$HOME/ws_px4",
      ],
    };
  }
  if (mode === "px4_mavlink") {
    return {
      title: "PX4 MAVLink 模式",
      body: "Agent 仅通过 MAVLink 控制 PX4；此模式不会调用 ROS Provider、ROS 避障适配器或 ROS 规划适配器。",
      tags: ["MAVLink", "不使用 ROS 工具", "遥测与指令", "SITL 或真实飞控"],
    };
  }
  if (mode === "airsim") {
    return {
      title: "纯 AirSim 模式",
      body: "Agent 通过 AirSim RPC 使用仿真、相机、深度与本地感知能力；该控制链路不经过 PX4 或 ROS。",
      tags: ["AirSim RPC", "相机", "深度", "仅仿真"],
    };
  }
  return {
    title: "后端模式",
    body: profile.control_path || profile.description || "Backend capabilities are loaded from the runtime profile.",
    tags: [mode, profile.control_path || "", profile.requires_ros_gateway ? "必须连接网关" : ""].filter(Boolean),
  };
}

function renderBackendModeNote(toolRuntime = {}) {
  const view = backendModeView(toolRuntime);
  return `
    <section class="settings-architecture-note">
      <strong>${escapeHtml(view.title)}</strong>
      <p>${escapeHtml(view.body)}</p>
      <div class="tool-meta subtle">
        ${view.tags.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}
      </div>
    </section>
  `;
}

function renderTools(tools, toolCards = [], toolRuntime = {}) {
  normalizeAgentSettingsCopy();
  const rawTools = Array.isArray(tools) ? tools : [];
  const rawByName = new Map(rawTools.map((tool) => [tool.name, tool]));
  const cards = Array.isArray(toolCards) ? toolCards : [];
  const visibleTools = cards.length
    ? cards.map((card) => ({ ...card, manifest: card.manifest || rawByName.get(card.name)?.manifest || {} }))
    : rawTools.filter((tool) => !isWorkflowMigrationRecord(tool));
  const migrationTools = rawTools.filter(isWorkflowMigrationRecord);
  const internalTools = rawTools.filter((tool) => (tool.manifest || {}).kind === "internal");

  els.toolCount.textContent = `${visibleTools.length} atomic/provider`;
  if (!visibleTools.length && !migrationTools.length) {
    els.toolList.innerHTML = `<div class="empty">Tools are not loaded yet.</div>`;
    return;
  }

  const architecture = renderBackendModeNote(toolRuntime);
  const grouped = new Map();
  visibleTools.forEach((tool) => {
    const category = toolCategory(tool);
    if (!grouped.has(category)) grouped.set(category, []);
    grouped.get(category).push(tool);
  });
  const categoryOrder = ["链路", "遥测", "飞控", "导航", "任务", "感知", "安全", "系统", "其他"];
  const categorySections = [...grouped.entries()]
    .sort(([left], [right]) => categoryOrder.indexOf(left) - categoryOrder.indexOf(right))
    .map(([category, items]) => renderToolSection(category, items, "atomic"))
    .join("");
  els.toolList.innerHTML = [
    architecture,
    `<label class="tool-search"><span>搜索工具</span><input type="search" placeholder="名称、能力或说明"></label>`,
    `<div class="tool-category-groups">${categorySections}</div>`,
    migrationTools.length ? `<details class="settings-advanced"><summary>迁移记录 (${migrationTools.length})</summary>${renderToolSection("仅供兼容，不参与规划", migrationTools, "migration")}</details>` : "",
    internalTools.length ? `<details class="settings-advanced"><summary>运行时内部工具 (${internalTools.length})</summary>${renderToolSection("内部工具", internalTools, "internal")}</details>` : "",
  ].filter(Boolean).join("");
  const search = els.toolList.querySelector(".tool-search input");
  if (search) {
    search.addEventListener("input", () => {
      const query = search.value.trim().toLowerCase();
      els.toolList.querySelectorAll(".tool-card-item").forEach((card) => {
        card.hidden = Boolean(query) && !card.textContent.toLowerCase().includes(query);
      });
      els.toolList.querySelectorAll(".settings-list-section").forEach((section) => {
        const cardsInSection = [...section.querySelectorAll(".tool-card-item")];
        section.hidden = cardsInSection.length > 0 && cardsInSection.every((card) => card.hidden);
      });
    });
  }
}

function toolCategory(tool) {
  const key = tool?.name || "";
  const manifest = tool?.manifest || {};
  const localized = TOOL_LOCALE[key]?.category;
  if (localized) return localized;
  const raw = String(tool?.category || manifest.category || manifest.surface || "").toLowerCase();
  if (TOOL_CATEGORY_LOCALE[raw]) return TOOL_CATEGORY_LOCALE[raw];
  if (raw.includes("telemetry") || raw.includes("status")) return "遥测";
  if (raw.includes("mission")) return "任务";
  if (raw.includes("navigation") || raw.includes("position")) return "导航";
  if (raw.includes("camera") || raw.includes("image") || raw.includes("perception")) return "感知";
  if (raw.includes("safety")) return "安全";
  return "其他";
}

function renderToolSection(title, tools, mode) {
  if (!tools.length) return "";
  return `
    <section class="settings-list-section">
      <div class="settings-section-head"><strong>${escapeHtml(title)}</strong><span>${tools.length}</span></div>
      ${tools.map((tool) => renderToolCardItem(tool, mode)).join("")}
    </section>
  `;
}

function renderToolCardItem(tool, mode = "atomic") {
  const key = tool.name || "";
  const manifest = tool.manifest || {};
  const localized = TOOL_LOCALE[key] || {};
  const replacement = manifest.replacement_skill || "";
  const desc = mode === "migration"
    ? (manifest.notes || `Legacy workflow record. Use ${replacement || "the matching skill"} instead.`)
    : (localized.desc || tool.purpose || tool.description || manifest.notes || "");
  const badges = [];
  badges.push(mode === "migration" ? "skill migration" : (manifest.kind || tool.kind || "atomic"));
  if (manifest.surface) badges.push(manifest.surface);
  if (tool.execution_mode === "async") badges.push("async start only");
  if (replacement) badges.push(`replace: ${replacement}`);
  if (tool.risk) badges.push(`risk ${tool.risk}`);
  return `
    <article class="compact-item tool-card-item" title="${escapeHtml(key)}">
      <strong>${escapeHtml(localized.name || key)}</strong>
      ${localized.name ? `<code>${escapeHtml(key)}</code>` : ""}
      <div class="tool-meta">${badges.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}</div>
      <p>${escapeHtml(desc || "No description.")}</p>
    </article>
  `;
}

function isWorkflowMigrationRecord(tool) {
  const manifest = tool?.manifest || {};
  return manifest.kind === "workflow" && manifest.recommended_layer === "skill";
}

function renderMemory(memory) {
  normalizeAgentSettingsCopy();
  const lessons = memory.lessons || [];
  const risks = memory.risk_events || [];
  const missions = memory.missions || [];
  const candidates = memory.skill_candidates || [];
  const taskRuns = Array.isArray(memory.task_runs?.recent) ? memory.task_runs.recent : [];
  const conversation = memory.conversation || {};
  const working = memory.working_state || memory.session || {};
  const persistentCount = lessons.length + risks.length + missions.length + candidates.length;
  els.memoryCount.textContent = `${persistentCount} 条长期记忆 / ${conversation.messages_saved || 0} 条会话消息`;

  const modelContext = [
    {
      title: "当前会话",
      text: `完整保存 ${conversation.messages_saved || 0} 条消息；本次模型上下文装载 ${conversation.messages_sent_to_model || 0} 条，估算 ${Number(conversation.estimated_context_tokens || 0).toLocaleString()} / ${Number(conversation.context_window || 0).toLocaleString()} tokens (${Number(conversation.context_percent || 0).toFixed(1)}%)。`,
      tag: "上下文",
    },
    {
      title: "当前运行态",
      text: Object.keys(working).length ? compactJson(working, 220) : "尚未保存位置、任务起点或短期运行状态。",
      tag: "运行态",
    },
  ];
  const persistent = [
    ...missions.slice(0, 6).map((item) => ({
      title: item.summary || item.intent || "任务经验",
      text: `${item.status || "unknown"} · ${toolSequenceText(item.tool_sequence) || item.command || "无工具序列"}`,
      tag: "任务",
    })),
    ...lessons.slice(0, 5).map((item) => ({
      title: item.intent || "成功经验",
      text: item.summary || `success rate ${item.success_rate ?? "-"}`,
      tag: "经验",
    })),
    ...risks.slice(0, 5).map((item) => ({
      title: item.run_id || "风险记录",
      text: item.reason || item.command || "无风险详情。",
      tag: "风险",
    })),
  ];
  const skillCandidates = candidates.slice(0, 8).map((item) => ({
    title: item.intent || "Skill 候选",
    text: `${toolSequenceText(item.tool_sequence) || "无工具序列"} · ${item.successes || 0}/${item.runs || 0} 次成功${item.eligible_for_review ? " · 可评审" : " · 继续积累样本"}`,
    tag: "候选",
  }));
  const replays = taskRuns.slice(0, 6).map((item) => ({
    title: item.summary || item.command || "任务复盘",
    text: `${readableRunStatus(item.status)} · ${item.counters?.steps_ok || 0}/${item.counters?.steps_total || 0} 步完成 · ${item.counters?.events || 0} 个事件`,
    tag: "复盘",
  }));

  els.memoryList.innerHTML = [
    renderMemoryGroup("模型当前可用", modelContext),
    renderMemoryGroup("长期任务经验", persistent),
    renderMemoryGroup("待沉淀为 Skill", skillCandidates),
    renderMemoryGroup("任务复盘", replays),
  ].join("");
}

function renderMemoryGroup(title, items) {
  const rows = items.length
    ? items.map((item) => `
      <article class="compact-item memory-row">
        <div class="memory-item-head"><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.tag || "MEMORY")}</small></div>
        <p>${escapeHtml(item.text || "")}</p>
      </article>
    `).join("")
    : `<div class="empty small">暂无记录</div>`;
  return `
    <section class="memory-group">
      <div class="settings-section-head"><strong>${escapeHtml(title)}</strong><span>${items.length}</span></div>
      ${rows}
    </section>
  `;
}

function compactJson(value, maxLength = 180) {
  let text = "";
  try {
    text = JSON.stringify(value);
  } catch (_) {
    text = String(value || "");
  }
  return text.length > maxLength ? `${text.slice(0, maxLength)}...` : text;
}

function toolSequenceText(sequence) {
  return Array.isArray(sequence) ? sequence.filter(Boolean).join(" -> ") : "";
}

function readableRunStatus(status) {
  const value = String(status || "").toLowerCase();
  return {
    completed: "completed",
    failed: "failed",
    blocked: "blocked",
    planned: "planned",
    running: "running",
    cancelled: "cancelled",
    canceled: "cancelled",
  }[value] || value || "unknown";
}

function renderSkills() {
  normalizeAgentSettingsCopy();
  if (!els.skillList) return;
  const skills = Array.isArray(skillsCache) ? skillsCache : [];
  const active = skills.filter((s) => s.enabled !== false).length;
  const total = skills.length;
  els.skillCount.textContent = `${active} / ${total}`;

  if (!skills.length) {
    els.skillList.innerHTML = `<div class="empty">尚未添加领域 Skill。</div>`;
    return;
  }

  // Trae/Codex 风格：只展示名称与描述，由模型按 description 决定是否使用
  els.skillList.innerHTML = skills.map((s) => {
    const id = s.id || s.name || "";
    const title = normalizeSkillTitle(s);
    const desc = s.purpose || s.description || "";
    const enabled = s.enabled !== false;
    return `
      <article class="skill-item ${enabled ? "" : "disabled"}" data-skill-id="${escapeHtml(id)}" role="button" tabindex="0" title="编辑 ${escapeHtml(title)}">
        <header>
          <strong>${escapeHtml(title)}</strong>
          <span class="skill-status">${enabled ? "启用" : "已停用"}</span>
        </header>
        <p>${escapeHtml(desc || "定义 Agent 在特定无人机任务中的操作流程与安全边界。")}</p>
      </article>
    `;
  }).join("");
}

function normalizeSkillTitle(skill) {
  const display = String(skill.display_name || skill.id || skill.name || "");
  return display.startsWith("skill:") ? display.slice("skill:".length) : display;
}

function openNewSkillModal() {
  openSkillModal("", { create: true });
}

function openSkillModal(skillId, options = {}) {
  const skill = skillsCache.find((s) => (s.id || s.name) === skillId);
  const creating = Boolean(options.create);
  if ((!skill && !creating) || !els.skillModal || !els.skillForm) return;
  const title = skill ? normalizeSkillTitle(skill) : "";
  els.skillModalTitle.textContent = creating ? "新建 Skill" : "编辑 Skill";
  els.skillModalSubtitle.textContent = creating ? "创建可复用的领域操作规程（SKILL.md）" : skillId;
  if (els.skillModalClose) {
    els.skillModalClose.textContent = "×";
    els.skillModalClose.title = "关闭";
  }
  const initialMarkdown = creating
    ? defaultSkillMarkdown("", title)
    : String(skill?.markdown || "");
  // Trae/Codex 风格：直接编辑 SKILL.md，frontmatter 决定 name/description 等元数据
  els.skillForm.innerHTML = `
    <input type="hidden" id="skillEditId" value="${escapeHtml(skillId)}">
    <input type="hidden" id="skillEditCreating" value="${creating ? "1" : "0"}">
    <div class="form-row">
      <label for="skillEditSlug">标识</label>
      <input id="skillEditSlug" value="${escapeHtml(skillId.replace(/^skill:/, ""))}" placeholder="inspection_workflow" ${creating ? "" : "disabled"}>
    </div>
    <div class="form-row">
      <label for="skillEditMarkdown">SKILL.md（frontmatter 的 name/description 决定模型何时使用）</label>
      <textarea id="skillEditMarkdown" class="skill-markdown-editor" spellcheck="false" rows="16">${escapeHtml(initialMarkdown)}</textarea>
    </div>
    <div class="modal-actions">
      <button type="button" id="skillModalCancelInline" class="secondary">取消</button>
      <button type="submit" class="primary">${creating ? "创建" : "保存"}</button>
    </div>
  `;
  const cancel = document.getElementById("skillModalCancelInline");
  if (cancel) cancel.addEventListener("click", closeSkillModal, { once: true });
  els.skillModal.hidden = false;
}

function defaultSkillMarkdown(skillId, title) {
  const name = skillId.replace(/^skill:/, "") || title || "new_skill";
  return `---
name: ${name}
description: Describe when and how the Agent should use this skill.

---

# ${name}

## Purpose

Describe the workflow this skill handles.

## When to Use

Describe the situation in which the Agent should apply this skill.

## Operating Rules

- Read current vehicle state before issuing commands.
- Use only tools exposed by the active backend.
`;
}

function closeSkillModal() {
  if (els.skillModal) els.skillModal.hidden = true;
  if (els.skillForm) els.skillForm.reset();
}

async function submitSkillForm() {
  const idInput = document.getElementById("skillEditId");
  const creating = document.getElementById("skillEditCreating")?.value === "1";
  const slug = String(document.getElementById("skillEditSlug")?.value || "").trim().toLowerCase();
  const markdown = String(document.getElementById("skillEditMarkdown")?.value || "").trim();
  const skillId = creating ? `skill:${slug}` : String(idInput?.value || "").trim();
  if (!skillId || (creating && !slug)) {
    showNotice("请填写 Skill 标识", "error");
    return;
  }
  if (!markdown) {
    showNotice("SKILL.md 内容不能为空", "error");
    return;
  }
  try {
    const result = creating
      ? await post("/api/skills", { action: "create", id: slug, markdown })
      : await post("/api/skills", { id: skillId, markdown });
    if (!result.ok) throw new Error(result.error || "Save failed");
    await loadSkills(true);
    closeSkillModal();
    renderSkills();
    showNotice(creating ? "Skill 已创建并加载" : "Skill 已保存并重新加载", "success");
  } catch (error) {
    showNotice(error.message || "Skill 保存失败", "error");
  }
}

function normalizeSystemSettingsCopy() {
  const drawer = document.getElementById("systemSettingsModal");
  if (!drawer) return;
  const close = document.getElementById("systemSettingsClose");
  if (close) {
    close.textContent = "×";
    close.title = "关闭";
  }
  const add = document.getElementById("addConnectionBtn");
  if (add) add.textContent = "+ 添加连接";
  const setLabel = (id, text) => {
    const label = drawer.querySelector(`label[for="${id}"]`);
    if (label) label.textContent = text;
  };
  setLabel("connectionDetailName", "名称");
  setLabel("connectionDetailType", "类型");
  setLabel("connectionDetailPort", "串口");
  setLabel("connectionDetailHost", "PX4 主机");
  setLabel("connectionDetailAddress", "TCP 地址");
  setLabel("connectionDetailPortNumber", "端口 / 波特率");
  setLabel("connectionDetailRemotePort", "PX4 远端端口");
  setLabel("connectionDetailRealVehicle", "真实飞控");
  if (els.connectionDetailName) els.connectionDetailName.placeholder = "PX4 MAVLink";
  if (els.connectionDetailPort) els.connectionDetailPort.placeholder = "COM3 or /dev/ttyACM0";
  if (els.connectionDetailAddress) els.connectionDetailAddress.placeholder = "127.0.0.1";
  if (els.connectionDetailRemotePort) els.connectionDetailRemotePort.placeholder = "18570 (optional)";
  if (els.connectionDetailCancel) els.connectionDetailCancel.textContent = "取消";
  const submit = els.connectionDetailForm?.querySelector('button[type="submit"]');
  if (submit) submit.textContent = "保存设置";
  const options = {
    auto: "自动",
    serial: "Serial",
    udp: "UDP MAVLink",
    tcp: "TCP MAVLink",
    airsim: "AirSim",
    px4_ros2: "PX4 ROS2 Gateway",
  };
  Array.from(els.connectionDetailType?.options || []).forEach((option) => {
    option.textContent = options[option.value] || option.value;
  });
}

function connectionTypeLabel(type) {
  return {
    auto: "Auto MAVLink",
    serial: "Serial",
    udp: "UDP MAVLink",
    tcp: "TCP MAVLink",
    airsim: "AirSim",
    px4_ros2: "PX4 ROS2 Gateway",
    ros2: "PX4 ROS2 Gateway",
    ros: "PX4 ROS2 Gateway",
    px4_ros: "PX4 ROS2 Gateway",
  }[String(type || "").toLowerCase()] || String(type || "unknown");
}

function connectionParamsSummary(params = {}, type = "") {
  const normalizedType = String(type || "").toLowerCase();
  if (normalizedType === "auto") {
    const fallbackHost = params.host || "127.0.0.1";
    const fallbackPort = params.portNumber || "14550";
    return `USB auto first / fallback udp:${fallbackHost}:${fallbackPort}`;
  }
  if (normalizedType === "serial") {
    return `${params.port || "auto port"} / baud: ${params.baud || params.portNumber || "115200"}`;
  }
  const preferred = ["url", "host", "address", "port", "portNumber", "baud", "remotePort", "workspace"];
  const rows = [];
  preferred.forEach((key) => {
    const value = params[key];
    if (value === undefined || value === null || value === "") return;
    rows.push(`${key}: ${value}`);
  });
  return rows.join(" / ") || "default params";
}

function backendLabelFromId(backend) {
  return {
    airsim: "AirSim",
    px4_mavlink: "PX4 MAVLink",
    px4_ros2: "PX4 ROS2 Gateway",
  }[String(backend || "").toLowerCase()] || String(backend || "backend");
}

function currentVehicleInfo() {
  const drone = latestState?.tool_runtime?.drone || {};
  const info = vehicleInfoCache && typeof vehicleInfoCache === "object" ? vehicleInfoCache : {};
  const connection = info.connection || drone.active_link || {};
  const firmware = info.firmware || drone.firmware || {};
  return { ...info, connection, firmware };
}

function currentActualLink() {
  return currentVehicleInfo().connection || {};
}

function currentFirmwareInfo() {
  return currentVehicleInfo().firmware || {};
}

function actualLinkSummary(link = currentActualLink()) {
  const url = link.url || "";
  const detected = link.detected_link || {};
  if (url.startsWith("serial:")) {
    const board = detected.board_name || detected.board_type || "PX4 USB";
    const device = detected.device || url.split(":")[1] || "";
    const baud = detected.baud || url.split(":")[2] || "";
    return `${device} / ${baud} / ${board}`;
  }
  if (url) return url;
  return "未建立实际链路";
}

function mavlinkRemoteTargetSummary(link = currentActualLink()) {
  if (link.px4_remote_endpoint) return String(link.px4_remote_endpoint);
  if (link.px4_remote_host && link.px4_remote_port) {
    return `${link.px4_remote_host}:${link.px4_remote_port}`;
  }
  const target = Array.isArray(link.probe_targets) ? link.probe_targets[0] : null;
  if (target?.host && target?.port) return `${target.host}:${target.port}`;
  return "";
}

function firmwareVersionText(firmware = currentFirmwareInfo()) {
  const version = firmware.flight_version || {};
  if (version.text) return version.type_name ? `${version.text} ${version.type_name}` : version.text;
  const custom = firmware.px4_custom_version || {};
  if (custom.text) return custom.text;
  return "--";
}

function valueText(value, fallback = "--") {
  return value === undefined || value === null || value === "" ? fallback : String(value);
}

function isRealVehicleRuntime(runtime = latestState?.tool_runtime || {}) {
  const capabilities = runtime.backend_profile?.capabilities || {};
  const drone = runtime.drone || {};
  return Boolean(capabilities.real_vehicle || drone.real_vehicle);
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

function renderActualLinkCard() {
  const card = els.connectionActualLink;
  if (!card) return;
  const connected = Boolean(latestState?.tool_runtime?.connected) && !latestState?.tool_runtime?.stale_connection;
  const selectedIsActive = selectedConnectionId && selectedConnectionId === activeConnectionId;
  if (!connected || !selectedIsActive) {
    card.hidden = true;
    card.innerHTML = "";
    return;
  }
  const link = currentActualLink();
  const detected = link.detected_link || {};
  const rows = [];
  const remoteTarget = mavlinkRemoteTargetSummary(link);
  if (String(link.url || "").startsWith("udpin:")) {
    rows.push(["本地监听", link.local_listen_url || link.url]);
    if (remoteTarget) rows.push(["PX4 目标", remoteTarget]);
  } else {
    rows.push(["实际端点", actualLinkSummary(link)]);
    if (remoteTarget) rows.push(["PX4 目标", remoteTarget]);
  }
  rows.push(
    ["链路类型", link.real_vehicle ? "真实 USB 飞控" : "仿真/网络链路"],
    ["系统/组件", `${valueText(link.system_id)} / ${valueText(link.component_id)}`],
    ["心跳", link.heartbeat_age_s != null ? `${link.heartbeat_age_s}s` : "--"],
  );
  if (detected.vid != null || detected.pid != null) {
    rows.push(["VID/PID", `${valueText(detected.vid)} / ${valueText(detected.pid)}`]);
  }
  card.innerHTML = `
    <strong>当前实际连接</strong>
    <div class="vehicle-info-grid">
      ${rows.map(([label, value]) => `<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(String(value))}</dd>`).join("")}
    </div>
  `;
  card.hidden = false;
}

function renderDetectedMavlinkLinks() {
  const list = els.detectedMavlinkLinks;
  if (!list) return;
  const rawLinks = Array.isArray(detectedMavlinkLinksCache) ? detectedMavlinkLinksCache : [];
  const byDevice = new Map();
  rawLinks.forEach((item) => {
    const key = item.device || item.url || `link_${byDevice.size}`;
    const existing = byDevice.get(key);
    if (!existing) {
      byDevice.set(key, { ...item, baud_candidates: [item.baud].filter(Boolean) });
    } else {
      existing.baud_candidates = Array.from(new Set([...(existing.baud_candidates || []), item.baud].filter(Boolean)));
      if (Number(item.score || 0) > Number(existing.score || 0)) Object.assign(existing, item);
    }
  });
  const links = Array.from(byDevice.values());
  const activeUrl = currentActualLink().url || "";
  if (els.detectedMavlinkLinksCount) els.detectedMavlinkLinksCount.textContent = String(links.length);
  if (!links.length) {
    list.innerHTML = `<div class="empty small">未检测到 USB MAVLink 串口。</div>`;
    return;
  }
  list.innerHTML = links.map((item) => {
    const connected = item.url === activeUrl;
    return `
      <article class="detected-link-item ${connected ? "connected" : ""}">
        <strong>${escapeHtml(item.device || "Serial")} ${connected ? "· 已连接" : ""}</strong>
        <span>${escapeHtml(item.board_name || item.board_type || item.description || "MAVLink device")}</span>
        <div class="detected-link-meta">
          <code>${escapeHtml(item.url || "")}</code>
          <code>baud ${escapeHtml((item.baud_candidates || [item.baud]).filter(Boolean).join(", "))}</code>
          <code>VID ${escapeHtml(valueText(item.vid))}</code>
          <code>PID ${escapeHtml(valueText(item.pid))}</code>
          <code>score ${escapeHtml(valueText(item.score))}</code>
        </div>
      </article>
    `;
  }).join("");
}

function setupSnapshot() {
  return vehicleSetupCache && typeof vehicleSetupCache === "object" ? vehicleSetupCache : {};
}

function setupConnected() {
  const runtime = latestState?.tool_runtime || {};
  const setup = setupSnapshot();
  return Boolean(setup.connected || (runtime.connected && !runtime.stale_connection));
}

function setupStatusClass(status) {
  return {
    ok: "ready",
    ready: "ready",
    warning: "warning",
    needs_attention: "warning",
    partial: "warning",
    disabled: "muted",
    missing: "danger",
    error: "danger",
    disconnected: "muted",
  }[String(status || "").toLowerCase()] || "muted";
}

function setupStatusLabel(status) {
  return {
    ok: "就绪",
    ready: "就绪",
    warning: "需要设置",
    needs_attention: "需要检查",
    partial: "部分收到",
    disabled: "禁用",
    missing: "未检测",
    error: "错误",
    disconnected: "未连接",
  }[String(status || "").toLowerCase()] || valueText(status);
}

function formatNumber(value, digits = 1, unit = "") {
  if (value === null || value === undefined || value === "") return "--";
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  return `${number.toFixed(digits)}${unit}`;
}

function formatSignedNumber(value, digits = 1, unit = "") {
  if (value === null || value === undefined || value === "") return "--";
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  const prefix = number > 0 ? "+" : "";
  return `${prefix}${number.toFixed(digits)}${unit}`;
}

function setupRows(rows) {
  return `
    <dl class="vehicle-info-grid">
      ${rows.map(([label, value]) => `<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(valueText(value))}</dd>`).join("")}
    </dl>
  `;
}

function setupBadge(status, label = setupStatusLabel(status)) {
  return `<span class="setup-badge ${setupStatusClass(status)}">${escapeHtml(label)}</span>`;
}

function summaryCard(section, title, summary = {}, rows = []) {
  const status = summary.setup || (setupConnected() ? "warning" : "disconnected");
  return `
    <article class="setup-summary-card ${setupStatusClass(status)}" data-system-section="${escapeHtml(section)}" role="button" tabindex="0">
      <button type="button" class="setup-card-head" data-system-section="${escapeHtml(section)}">
        <span>${escapeHtml(title)}</span>
        <span class="setup-status-dot ${setupStatusClass(status)}"></span>
      </button>
      ${setupRows(rows)}
    </article>
  `;
}

function readOnlyRibbon() {
  const setup = setupSnapshot();
  const msg = setup.read_only?.message || "当前设置页已开放单参数写入；校准、电机测试和固件烧录暂未开放。";
  return `<div class="readonly-ribbon">${escapeHtml(msg)}</div>`;
}

function dataSourceRibbon(items = []) {
  const labels = items.filter(Boolean);
  if (!labels.length) return "";
  return `<div class="data-source-ribbon">${labels.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}</div>`;
}

function renderVehicleSettingsPanel() {
  const panel = els.vehicleInfoPanel;
  if (!panel) return;
  const runtime = latestState?.tool_runtime || {};
  const drone = runtime.drone || {};
  const setup = setupSnapshot();
  const info = currentVehicleInfo();
  const link = setup.connection || info.connection || {};
  const firmware = setup.firmware || info.firmware || {};
  const parameterStatus = setup.parameters || info.parameters || drone.parameter_status || vehicleParametersCache || {};
  const summary = setup.summary || {};
  const mapReliable = hasReliableVehicleMapPosition(setup.telemetry?.status || drone, runtime);
  const detected = link.detected_link || {};
  const connected = setupConnected();
  panel.innerHTML = `
    <div class="vehicle-config-banner ${connected ? "online" : "offline"}">
      <div>
        <strong>${connected ? "PX4 已连接" : "等待 PX4 连接"}</strong>
        <span>${escapeHtml(actualLinkSummary(link))}</span>
      </div>
      <div class="vehicle-config-badges">
        ${setupBadge(connected ? "ready" : "disconnected", connected ? "ONLINE" : "OFFLINE")}
        ${setupBadge(parameterStatus.ready ? "ready" : (parameterStatus.received_count ? "partial" : "warning"), `参数 ${parameterCountText(parameterStatus)}`)}
        ${setupBadge(mapReliable ? "ready" : "warning", mapReliable ? "地图位置可信" : "地图位置未采用")}
      </div>
    </div>
    <div class="setup-summary-flow">
      ${summaryCard("airframe", "机架", summary.airframe, [
        ["系统 ID", summary.airframe?.system_id || link.system_id],
        ["机型", summary.airframe?.vehicle_type || firmware.vehicle_type],
        ["固件版本", firmwareVersionText(firmware)],
        ["板卡", detected.board_name || detected.board_type],
      ])}
      ${summaryCard("sensors", "传感器", summary.sensors, [
        ["陀螺仪", setupStatusLabel(summary.sensors?.gyro)],
        ["加速度计", setupStatusLabel(summary.sensors?.accel)],
        ["磁罗盘", setupStatusLabel(summary.sensors?.mag)],
        ["气压计", setupStatusLabel(summary.sensors?.baro)],
      ])}
      ${summaryCard("radio", "遥控器", summary.radio, [
        ["通道数", summary.radio?.channels || "--"],
        ["RSSI", valueText(summary.radio?.rssi)],
        ["状态", setupStatusLabel(summary.radio?.sensor_state)],
      ])}
      ${summaryCard("flight_modes", "飞行模式", summary.flight_modes, [
        ["当前模式", summary.flight_modes?.current_mode || drone.mode],
        ["模式 1", summary.flight_modes?.flight_mode_1],
        ["模式 2", summary.flight_modes?.flight_mode_2],
        ["模式 3", summary.flight_modes?.flight_mode_3],
      ])}
      ${summaryCard("power", "电源", summary.power, [
        ["电压", formatNumber(summary.power?.voltage, 2, " V")],
        ["电流", formatNumber(summary.power?.current, 2, " A")],
        ["电量", summary.power?.remaining != null ? `${summary.power.remaining}%` : "--"],
        ["电芯", summary.power?.cells || "--"],
      ])}
      ${summaryCard("safety", "安全", summary.safety, [
        ["解锁", summary.safety?.armed ? "已解锁" : "未解锁"],
        ["飞行", summary.safety?.flying ? "空中" : "地面/未知"],
        ["低电量动作", summary.safety?.low_battery_action],
        ["遥控失联", summary.safety?.rc_loss_action],
      ])}
      ${summaryCard("actuators", "Actuators", summary.actuators, [
        ["输出数量", summary.actuators?.outputs || "--"],
        ["活跃输出", summary.actuators?.active_outputs || "--"],
        ["状态", setupStatusLabel(summary.actuators?.sensor_state)],
      ])}
      ${summaryCard("parameters", "参数", { setup: parameterStatus.ready ? "ok" : (parameterStatus.received_count ? "warning" : "missing") }, [
        ["下载状态", parameterStatusText(parameterStatus.status)],
        ["收到/总数", parameterCountText(parameterStatus)],
        ["进度", parameterProgressText(parameterStatus)],
        ["最近收到", timestampText(parameterStatus.last_message_at)],
      ])}
    </div>
    ${readOnlyRibbon()}
  `;
  renderDetectedMavlinkLinks();
}

function renderVehicleSetupPanels(forceAll = false) {
  if (!forceAll) {
    renderActiveVehicleSetupPanel("active");
    return;
  }
  renderVehicleSettingsPanel();
  renderVehicleAirframePanel();
  renderVehicleSensorsPanel(true);
  renderVehicleRadioPanel();
  renderVehicleFlightModesPanel();
  renderVehiclePowerPanel();
  renderVehicleActuatorsPanel();
  renderVehicleSafetyPanel();
  renderVehiclePidPanel(true);
  renderVehicleWaveformPanel(true);
  renderVehicleFlightBehaviorPanel();
  renderVehicleFirmwarePanel();
  renderVehicleParameterSummary(vehicleParametersCache || setupSnapshot().parameters || currentVehicleInfo().parameters || {});
}

function renderActiveVehicleSetupPanel(reason = "") {
  if (els.systemSettingsModal?.hidden || !isVehicleSetupSection(activeSystemSettingsSection)) return;
  if (activeSystemSettingsSection !== "pid_tuning") {
    const pidPanel = els.vehiclePidPanel;
    if (pidPanel) pidPanel.dataset.pidMounted = "";
  }
  switch (activeSystemSettingsSection) {
    case "vehicle":
      renderVehicleSettingsPanel();
      break;
    case "airframe":
      renderVehicleAirframePanel();
      break;
    case "sensors":
      renderVehicleSensorsPanel(reason === "section" || reason === "setup" || reason === "setup-error");
      break;
    case "radio":
      renderVehicleRadioPanel();
      break;
    case "flight_modes":
      renderVehicleFlightModesPanel();
      break;
    case "power":
      renderVehiclePowerPanel();
      break;
    case "actuators":
      renderVehicleActuatorsPanel();
      break;
    case "safety":
      renderVehicleSafetyPanel();
      break;
    case "pid_tuning":
      renderVehiclePidPanel(reason === "section");
      break;
    case "waveforms":
      renderVehicleWaveformPanel(reason === "section");
      break;
    case "flight_behavior":
      renderVehicleFlightBehaviorPanel();
      break;
    case "parameters":
      renderVehicleParameterSummary(vehicleParametersCache || setupSnapshot().parameters || currentVehicleInfo().parameters || {});
      break;
    case "firmware":
      renderVehicleFirmwarePanel();
      break;
    default:
      break;
  }
}

function renderUnavailableSetup(panel, title = "未连接 PX4") {
  if (!panel) return true;
  if (setupConnected()) return false;
  panel.dataset.pidMounted = "";
  panel.dataset.sensorMounted = "";
  panel.dataset.waveformMounted = "";
  panel.innerHTML = `<div class="setup-empty"><strong>${escapeHtml(title)}</strong><span>连接真实 PX4 后这里会显示实时配置与遥测。</span></div>`;
  return true;
}

function renderVehicleAirframePanel() {
  const panel = els.vehicleAirframePanel;
  if (!panel || renderUnavailableSetup(panel)) return;
  const setup = setupSnapshot();
  const airframe = setup.summary?.airframe || {};
  const firmware = setup.firmware || {};
  const link = setup.connection || {};
  panel.innerHTML = `
    <div class="airframe-layout">
      <section class="setup-detail-card">
        <div class="quad-visual" aria-hidden="true">
          <span class="quad-arm arm-a"></span><span class="quad-arm arm-b"></span>
          <span class="quad-body"></span>
          <span class="quad-rotor r1"></span><span class="quad-rotor r2"></span><span class="quad-rotor r3"></span><span class="quad-rotor r4"></span>
        </div>
        ${setupRows([
          ["系统 ID", airframe.system_id || link.system_id],
          ["组件 ID", link.component_id],
          ["机型", airframe.vehicle_type],
          ["Autopilot", airframe.autopilot],
          ["SYS_AUTOSTART", airframe.autostart],
          ["SYS_AUTOCONFIG", airframe.airframe_id],
        ])}
      </section>
      <section class="setup-detail-card">
        <strong>固件识别</strong>
        ${setupRows([
          ["PX4 版本", firmwareVersionText(firmware)],
          ["Vendor/Product", `${valueText(firmware.vendor_id)} / ${valueText(firmware.product_id)}`],
          ["UID", firmware.uid],
          ["Git hash", firmware.git_hash],
          ["MAVLink", link.mavlink_wire_protocol],
        ])}
      </section>
    </div>
    ${dataSourceRibbon(["HEARTBEAT", "AUTOPILOT_VERSION", "PARAM_VALUE"])}
    ${readOnlyRibbon()}
  `;
}

function axisMeter(label, value, min, max, unit = "", digits = 1) {
  const number = value === null || value === undefined || value === "" ? NaN : Number(value);
  const finite = Number.isFinite(number);
  const pct = finite ? Math.max(0, Math.min(100, ((number - min) / (max - min)) * 100)) : 0;
  return `
    <div class="axis-meter">
      <span>${escapeHtml(label)}</span>
      <div class="axis-track"><i style="width:${pct}%"></i></div>
      <strong>${finite ? `${number.toFixed(digits)}${unit}` : "--"}</strong>
    </div>
  `;
}

function axisPill(label, value, color, digits = 2) {
  const number = value === null || value === undefined || value === "" ? NaN : Number(value);
  return `
    <div class="axis-pill" style="--axis-color:${escapeHtml(color)}">
      <span>${escapeHtml(label)}</span>
      <strong>${Number.isFinite(number) ? number.toFixed(digits) : "--"}</strong>
    </div>
  `;
}

function drawVehicleSensorMiniCharts() {
  const history = setupSnapshot().history || {};
  drawMiniHistoryCanvas("sensorAccelCanvas", history.imu || [], [
    { field: "xacc", label: "X", color: WAVEFORM_COLORS[7] },
    { field: "yacc", label: "Y", color: WAVEFORM_COLORS[1] },
    { field: "zacc", label: "Z", color: WAVEFORM_COLORS[6] },
  ]);
  drawMiniHistoryCanvas("sensorGyroCanvas", history.imu || [], [
    { field: "xgyro", label: "X", color: WAVEFORM_COLORS[3] },
    { field: "ygyro", label: "Y", color: WAVEFORM_COLORS[4] },
    { field: "zgyro", label: "Z", color: WAVEFORM_COLORS[0] },
  ]);
  drawMiniHistoryCanvas("sensorMagCanvas", history.imu || [], [
    { field: "xmag", label: "X", color: WAVEFORM_COLORS[0] },
    { field: "ymag", label: "Y", color: WAVEFORM_COLORS[1] },
    { field: "zmag", label: "Z", color: WAVEFORM_COLORS[2] },
  ]);
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

function renderVehicleSensorsPanel(force = false) {
  const panel = els.vehicleSensorsPanel;
  if (!panel || renderUnavailableSetup(panel)) return;
  const now = performance.now();
  if (!force && panel.dataset.sensorMounted === "1" && now - vehicleSensorsLastRenderAt < VEHICLE_SENSOR_RENDER_THROTTLE_MS) {
    return;
  }
  vehicleSensorsLastRenderAt = now;
  panel.dataset.sensorMounted = "1";
  const setup = setupSnapshot();
  const telemetry = setup.telemetry || {};
  const attitude = telemetry.attitude || {};
  const imu = telemetry.imu || {};
  const vibration = telemetry.vibration || {};
  const gpsRaw = telemetry.gps_raw || {};
  const globalPosition = telemetry.global_position || {};
  const sensors = setup.summary?.sensors || {};
  const health = telemetry.sensor_health?.items || {};
  const roll = Number(attitude.roll_deg || 0);
  const pitch = Number(attitude.pitch_deg || 0);
  const horizonShift = Math.max(-36, Math.min(36, pitch * 1.4));
  const imuUnit = String(imu.unit || "");
  const scaledImu = imuUnit.includes("mG") || String(sensors.latest_imu_source || "").startsWith("SCALED") || sensors.latest_imu_source === "RAW_IMU";
  const accelRange = scaledImu ? 2200 : 22;
  const gyroRange = scaledImu ? 6000 : 7;
  const magRange = scaledImu ? 2000 : 2;
  const sensorTabs = [
    ["imu", "IMU"],
    ["mag", "罗盘"],
    ["gps", "GPS"],
    ["flow", "光流"],
    ["rangefinder", "测距仪"],
  ];
  const tabsHtml = `
    <div class="sensor-config-tabs">
      ${sensorTabs.map(([id, label]) => `<button type="button" data-sensor-setup-tab="${id}" class="${activeSensorSetupTab === id ? "active" : ""}">${escapeHtml(label)}</button>`).join("")}
    </div>
  `;
  const statusStrip = `
    <div class="sensor-status-strip">
      ${Object.entries({
        gyro: "陀螺仪",
        accel: "加速度计",
        mag: "磁罗盘",
        baro: "气压计",
        gps: "GPS",
      }).map(([key, label]) => `${setupBadge(health[key]?.healthy ? "ready" : (health[key]?.present ? "warning" : "missing"), label)}`).join("")}
    </div>
  `;
  const imuView = `
    <div class="sensor-mico-grid">
      <section class="setup-detail-card sensor-wave-card">
        <header><strong>加速度计</strong><span>${escapeHtml(imu.unit || sensors.latest_imu_source || "--")}</span></header>
        <div class="sensor-axis-readouts">
          ${axisPill("X", imu.xacc, WAVEFORM_COLORS[7])}
          ${axisPill("Y", imu.yacc, WAVEFORM_COLORS[1])}
          ${axisPill("Z", imu.zacc, WAVEFORM_COLORS[6])}
        </div>
        <canvas id="sensorAccelCanvas" class="sensor-wave-canvas"></canvas>
      </section>
      <section class="setup-detail-card sensor-wave-card">
        <header><strong>陀螺仪</strong><span>${scaledImu ? "raw / scaled" : "rad/s"}</span></header>
        <div class="sensor-axis-readouts">
          ${axisPill("X", imu.xgyro, WAVEFORM_COLORS[3])}
          ${axisPill("Y", imu.ygyro, WAVEFORM_COLORS[4])}
          ${axisPill("Z", imu.zgyro, WAVEFORM_COLORS[0])}
        </div>
        <canvas id="sensorGyroCanvas" class="sensor-wave-canvas"></canvas>
      </section>
      <section class="setup-detail-card sensor-orientation-card">
        <strong>姿态</strong>
        <div class="attitude-widget compact">
          <div class="attitude-horizon" style="transform: translateY(${horizonShift}px) rotate(${-roll}deg)"></div>
          <div class="attitude-aircraft"></div>
          <span class="attitude-readout roll">Roll ${escapeHtml(formatSignedNumber(roll, 1, "°"))}</span>
          <span class="attitude-readout pitch">Pitch ${escapeHtml(formatSignedNumber(pitch, 1, "°"))}</span>
        </div>
        ${statusStrip}
      </section>
      <section class="setup-detail-card">
        <strong>振动</strong>
        <div class="axis-grid">
          ${axisMeter("Vibration X", vibration.vibration_x, 0, 50, "", 2)}
          ${axisMeter("Vibration Y", vibration.vibration_y, 0, 50, "", 2)}
          ${axisMeter("Vibration Z", vibration.vibration_z, 0, 50, "", 2)}
        </div>
        ${setupRows([["Clip 0", vibration.clipping_0], ["Clip 1", vibration.clipping_1], ["Clip 2", vibration.clipping_2]])}
      </section>
    </div>
  `;
  const magView = `
    <div class="sensor-mico-grid">
      <section class="setup-detail-card sensor-wave-card">
        <header><strong>磁罗盘</strong><span>${escapeHtml(sensors.latest_imu_source || imu.source || "--")}</span></header>
        <div class="sensor-axis-readouts">
          ${axisPill("X", imu.xmag, WAVEFORM_COLORS[0])}
          ${axisPill("Y", imu.ymag, WAVEFORM_COLORS[1])}
          ${axisPill("Z", imu.zmag, WAVEFORM_COLORS[2])}
        </div>
        <canvas id="sensorMagCanvas" class="sensor-wave-canvas"></canvas>
      </section>
      <section class="setup-detail-card">
        <strong>罗盘状态</strong>
        ${setupRows([
          ["磁罗盘", setupStatusLabel(sensors.mag)],
          ["CAL_MAG0_ID", sensors.mag0_id],
          ["板载方向 SENS_BOARD_ROT", sensors.board_rotation],
          ["SYS_STATUS", health.mag?.healthy ? "healthy" : (health.mag?.present ? "present" : "missing")],
        ])}
      </section>
    </div>
  `;
  const gpsView = `
    <div class="sensor-mico-grid">
      <section class="setup-detail-card">
        <strong>GPS</strong>
        ${setupRows([
          ["状态", setupStatusLabel(sensors.gps)],
          ["Fix type", gpsRaw.fix_type],
          ["Satellites", gpsRaw.satellites_visible],
          ["Lat", globalPosition.lat ?? gpsRaw.lat],
          ["Lon", globalPosition.lon ?? gpsRaw.lon],
          ["Alt", formatNumber(globalPosition.relative_alt ?? gpsRaw.alt, 2, " m")],
          ["EPH", gpsRaw.eph],
          ["EPV", gpsRaw.epv],
        ])}
      </section>
      <section class="setup-detail-card">
        <strong>位置可信度</strong>
        ${setupRows([
          ["位置来源", telemetry.status?.position_source],
          ["地图位置", telemetry.status?.map_position_valid ? "可信" : "未采用"],
          ["导航位置", telemetry.status?.navigation_position_valid ? "可信" : "不可用于导航"],
          ["Global age", formatNumber(telemetry.status?.global_position_age_s, 2, " s")],
        ])}
      </section>
    </div>
  `;
  const unavailableView = (title, source) => `
    <section class="setup-detail-card wide">
      <strong>${escapeHtml(title)}</strong>
      <div class="setup-empty small">当前未收到 ${escapeHtml(source)} 对应 MAVLink 数据。连接支持该传感器的飞控后，这里会显示实时值和状态。</div>
    </section>
  `;
  const activeView = {
    imu: imuView,
    mag: magView,
    gps: gpsView,
    flow: unavailableView("光流", "OPTICAL_FLOW / OPTICAL_FLOW_RAD"),
    rangefinder: unavailableView("测距仪", "DISTANCE_SENSOR"),
  }[activeSensorSetupTab] || imuView;
  panel.innerHTML = `
    ${tabsHtml}
    ${activeView}
    <div class="sensor-config-footer">
      <section class="setup-detail-card">
        <strong>校准状态</strong>
        ${setupRows([
          ["陀螺仪", setupStatusLabel(sensors.gyro)],
          ["加速度计", setupStatusLabel(sensors.accel)],
          ["磁罗盘", setupStatusLabel(sensors.mag)],
          ["气压计", setupStatusLabel(sensors.baro)],
          ["板载方向 SENS_BOARD_ROT", sensors.board_rotation],
          ["CAL_GYRO0_ID", sensors.gyro0_id],
          ["CAL_ACC0_ID", sensors.acc0_id],
          ["CAL_MAG0_ID", sensors.mag0_id],
        ])}
      </section>
    </div>
    ${dataSourceRibbon(["ATTITUDE", sensors.latest_imu_source || "IMU", "SYS_STATUS", "VIBRATION"])}
    ${readOnlyRibbon()}
  `;
  requestAnimationFrame(drawVehicleSensorMiniCharts);
}

function channelBar(label, value, min = 900, max = 2100) {
  const number = value === null || value === undefined || value === "" ? NaN : Number(value);
  const finite = Number.isFinite(number);
  const pct = finite ? Math.max(0, Math.min(100, ((number - min) / (max - min)) * 100)) : 0;
  return `
    <div class="channel-bar">
      <span>${escapeHtml(label)}</span>
      <div><i style="width:${pct}%"></i></div>
      <strong>${finite ? String(Math.round(number)) : "--"}</strong>
    </div>
  `;
}

function renderVehicleRadioPanel() {
  const panel = els.vehicleRadioPanel;
  if (!panel || renderUnavailableSetup(panel)) return;
  const setup = setupSnapshot();
  const radio = setup.summary?.radio || {};
  const channels = setup.telemetry?.rc_channels?.channels || [];
  panel.innerHTML = `
    <div class="setup-two-col">
      <section class="setup-detail-card">
        <strong>遥控器输入</strong>
        ${setupRows([
          ["状态", setupStatusLabel(radio.sensor_state)],
          ["通道数", radio.channels],
          ["RSSI", radio.rssi],
          ["time_boot_ms", radio.last],
        ])}
      </section>
      <section class="setup-detail-card channel-list">
        ${channels.slice(0, 12).map((value, index) => channelBar(`CH${index + 1}`, value)).join("") || `<div class="setup-empty small">未收到 RC_CHANNELS。</div>`}
      </section>
    </div>
    ${dataSourceRibbon(["RC_CHANNELS", "SYS_STATUS"])}
  `;
}

function renderVehicleFlightModesPanel() {
  const panel = els.vehicleFlightModesPanel;
  if (!panel || renderUnavailableSetup(panel)) return;
  const modes = setupSnapshot().summary?.flight_modes || {};
  const rows = [["当前模式", modes.current_mode]];
  for (let i = 1; i <= 6; i += 1) rows.push([`飞行模式 ${i}`, modes[`flight_mode_${i}`]]);
  panel.innerHTML = `<section class="setup-detail-card wide">${setupRows(rows)}</section>${dataSourceRibbon(["HEARTBEAT", "COM_FLTMODE1-6"])}${readOnlyRibbon()}`;
}

function renderVehiclePowerPanel() {
  const panel = els.vehiclePowerPanel;
  if (!panel || renderUnavailableSetup(panel)) return;
  const setup = setupSnapshot();
  const power = setup.summary?.power || {};
  const battery = setup.telemetry?.battery || {};
  const remaining = Number(power.remaining ?? battery.battery_remaining);
  const pct = Number.isFinite(remaining) ? Math.max(0, Math.min(100, remaining)) : 0;
  panel.innerHTML = `
    <div class="setup-two-col">
      <section class="setup-detail-card power-gauge-card">
        <div class="battery-gauge"><i style="height:${pct}%"></i></div>
        ${setupRows([
          ["电压", formatNumber(power.voltage, 2, " V")],
          ["电流", formatNumber(power.current, 2, " A")],
          ["剩余", Number.isFinite(remaining) ? `${remaining}%` : "--"],
          ["电芯", power.cells],
          ["状态", setupStatusLabel(power.sensor_state)],
        ])}
      </section>
      <section class="setup-detail-card">
        <strong>电芯电压</strong>
        <div class="axis-grid">
          ${(battery.cell_voltages || []).map((value, index) => axisMeter(`Cell ${index + 1}`, value, 3.0, 4.4, " V", 2)).join("") || `<div class="setup-empty small">未收到 BATTERY_STATUS 电芯数据。</div>`}
        </div>
      </section>
    </div>
    ${dataSourceRibbon(["SYS_STATUS", "BATTERY_STATUS", "POWER_STATUS"])}
  `;
}

function renderVehicleActuatorsPanel() {
  const panel = els.vehicleActuatorsPanel;
  if (!panel || renderUnavailableSetup(panel)) return;
  const setup = setupSnapshot();
  const actuators = setup.summary?.actuators || {};
  const outputs = setup.telemetry?.servo_output?.outputs || [];
  panel.innerHTML = `
    <div class="setup-two-col">
      <section class="setup-detail-card">
        <strong>执行器状态</strong>
        ${setupRows([
          ["状态", setupStatusLabel(actuators.sensor_state)],
          ["输出数量", actuators.outputs],
          ["活跃输出", actuators.active_outputs],
        ])}
      </section>
      <section class="setup-detail-card channel-list">
        ${outputs.slice(0, 12).map((value, index) => channelBar(`PWM${index + 1}`, value, 900, 2100)).join("") || `<div class="setup-empty small">未收到 SERVO_OUTPUT_RAW。</div>`}
      </section>
    </div>
    ${dataSourceRibbon(["SERVO_OUTPUT_RAW", "SYS_STATUS"])}
    ${readOnlyRibbon()}
  `;
}

function renderVehicleSafetyPanel() {
  const panel = els.vehicleSafetyPanel;
  if (!panel || renderUnavailableSetup(panel)) return;
  const safety = setupSnapshot().summary?.safety || {};
  panel.innerHTML = `
    <section class="setup-detail-card wide">
      <div class="safety-state-row">
        ${setupBadge(safety.armed ? "warning" : "ready", safety.armed ? "已解锁" : "未解锁")}
        ${setupBadge(safety.flying ? "warning" : "ready", safety.flying ? "空中飞行" : "地面/未知")}
        ${setupBadge("ready", safety.mode || "--")}
      </div>
      ${setupRows([
        ["低电量保护", safety.low_battery_action],
        ["遥控信号丢失保护", safety.rc_loss_action],
        ["数据链路丢失保护", safety.data_link_loss],
        ["返航高度", safety.return_altitude],
      ])}
    </section>
    ${dataSourceRibbon(["HEARTBEAT", "EXTENDED_SYS_STATE", "COM_* / NAV_*"])}
    ${readOnlyRibbon()}
  `;
}

function renderVehiclePidPanel(force = false) {
  const panel = els.vehiclePidPanel;
  if (!panel || renderUnavailableSetup(panel)) return;
  const views = [
    ["rate_roll", "Rate Roll"],
    ["rate_pitch", "Rate Pitch"],
    ["rate_yaw", "Rate Yaw"],
    ["att_roll", "Att Roll"],
    ["att_pitch", "Att Pitch"],
    ["vel_xy", "Velocity"],
    ["pos_xy", "Position"],
  ];
  if (force || panel.dataset.pidMounted !== "1") {
    panel.innerHTML = `
      <div class="pid-layout">
        <section class="setup-detail-card pid-chart-card">
          <div class="pid-tab-row">
            ${views.map(([id, label]) => `<button type="button" data-pid-view="${id}" class="${activePidTuningView === id ? "active" : ""}">${escapeHtml(label)}</button>`).join("")}
          </div>
          <canvas id="vehiclePidCanvas" class="pid-canvas"></canvas>
          <div class="pid-legend">
            <span><i class="response"></i>Response</span>
            <span><i class="setpoint"></i>Setpoint</span>
          </div>
        </section>
        <aside class="setup-detail-card pid-side">
          <strong>只读调参观察 <span id="vehiclePidSource"></span></strong>
          <div id="vehiclePidReadouts"></div>
        </aside>
      </div>
      ${dataSourceRibbon(["ATTITUDE", "ATTITUDE_TARGET", "PARAM_VALUE"])}
      ${readOnlyRibbon()}
    `;
    panel.dataset.pidMounted = "1";
  }
  updateVehiclePidPanelChrome();
  requestAnimationFrame(drawPidTuningChart);
}

function parameterValueFromCache(name) {
  const params = vehicleParametersCache?.parameters || [];
  const found = params.find((param) => param.name === name);
  if (found) return valueText(found.value_text ?? found.value);
  const setup = setupSnapshot();
  return valueText((setup.parameter_highlights || {})[name], "--");
}

function updateVehiclePidPanelChrome() {
  const panel = els.vehiclePidPanel;
  if (!panel) return;
  panel.querySelectorAll("[data-pid-view]").forEach((button) => {
    button.classList.toggle("active", button.dataset.pidView === activePidTuningView);
  });
  const setup = setupSnapshot();
  const config = pidChartConfig(activePidTuningView);
  const history = setup.history || {};
  const points = Array.isArray(history[config.history]) ? history[config.history] : [];
  const source = document.getElementById("vehiclePidSource");
  if (source) source.textContent = `${config.title} / ${points.length} 点`;
  const rows = document.getElementById("vehiclePidReadouts");
  if (!rows) return;
  const live = currentPidLiveValues(config);
  rows.innerHTML = setupRows([
    ["数据源", config.history === "rate" ? "ATTITUDE + ATTITUDE_TARGET" : config.history.toUpperCase()],
    ["Response 当前值", formatNumber(live.response, 2, ` ${config.unit}`)],
    ["Setpoint 当前值", live.setpoint == null ? "--" : formatNumber(live.setpoint, 2, ` ${config.unit}`)],
    ["Airmode MC_AIRMODE", parameterValueFromCache("MC_AIRMODE")],
    ["Thrust curve THR_MDL_FAC", parameterValueFromCache("THR_MDL_FAC")],
    ["Roll rate K", parameterValueFromCache("MC_ROLLRATE_K")],
    ["Pitch rate K", parameterValueFromCache("MC_PITCHRATE_K")],
    ["Yaw rate K", parameterValueFromCache("MC_YAWRATE_K")],
  ]);
}

function currentPidLiveValues(config) {
  const telemetry = setupSnapshot().telemetry || {};
  const attitude = telemetry.attitude || {};
  const target = telemetry.attitude_target || {};
  const position = telemetry.position || {};
  const map = {
    roll: [attitude.rollspeed_deg_s, target.body_roll_rate_deg_s],
    pitch: [attitude.pitchspeed_deg_s, target.body_pitch_rate_deg_s],
    yaw: [attitude.yawspeed_deg_s, target.body_yaw_rate_deg_s],
    att_roll: [attitude.roll_deg, target.roll_deg],
    att_pitch: [attitude.pitch_deg, target.pitch_deg],
    vx: [position.vx, null],
    x: [position.x, null],
  };
  const key = config.liveKey || config.response;
  const values = map[key] || [null, null];
  const asFinite = (value) => (
    value === null || value === undefined || value === "" ? null : (Number.isFinite(Number(value)) ? Number(value) : null)
  );
  return {
    response: asFinite(values[0]),
    setpoint: asFinite(values[1]),
  };
}

function renderVehicleFlightBehaviorPanel() {
  const panel = els.vehicleFlightBehaviorPanel;
  if (!panel || renderUnavailableSetup(panel)) return;
  const rows = [
    ["MPC_XY_VEL_MAX", parameterValueFromCache("MPC_XY_VEL_MAX")],
    ["MPC_Z_VEL_MAX_UP", parameterValueFromCache("MPC_Z_VEL_MAX_UP")],
    ["MPC_Z_VEL_MAX_DN", parameterValueFromCache("MPC_Z_VEL_MAX_DN")],
    ["MPC_TKO_SPEED", parameterValueFromCache("MPC_TKO_SPEED")],
    ["NAV_ACC_RAD", parameterValueFromCache("NAV_ACC_RAD")],
    ["COM_RC_LOSS_T", parameterValueFromCache("COM_RC_LOSS_T")],
  ];
  panel.innerHTML = `<section class="setup-detail-card wide">${setupRows(rows)}</section>${dataSourceRibbon(["MPC_*", "NAV_*", "COM_*"])}${readOnlyRibbon()}`;
}

function renderVehicleFirmwarePanel() {
  const panel = els.vehicleFirmwarePanel;
  if (!panel || renderUnavailableSetup(panel)) return;
  const setup = setupSnapshot();
  const firmware = setup.firmware || currentFirmwareInfo();
  const capabilities = Array.isArray(firmware.capability_flags) ? firmware.capability_flags : [];
  panel.innerHTML = `
    <section class="setup-detail-card wide">
      ${setupRows([
        ["PX4 版本", firmwareVersionText(firmware)],
        ["Autopilot", firmware.autopilot],
        ["机型", firmware.vehicle_type],
        ["Vendor/Product", `${valueText(firmware.vendor_id)} / ${valueText(firmware.product_id)}`],
        ["UID", firmware.uid],
        ["Git hash", firmware.git_hash],
        ["Flight custom", firmware.flight_custom_version_hex],
      ])}
      <div class="capability-list">
        ${capabilities.map((item) => `<span>${escapeHtml(item)}</span>`).join("") || `<span>未收到能力标志</span>`}
      </div>
    </section>
    ${dataSourceRibbon(["AUTOPILOT_VERSION", "HEARTBEAT"])}
    ${readOnlyRibbon()}
  `;
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

function pidChartConfig(view) {
  return {
    rate_roll: { history: "rate", response: "roll", setpoint: "roll_setpoint", liveKey: "roll", title: "Roll Rate", unit: "deg/s", windowSec: 8, defaultRange: [-45, 45] },
    rate_pitch: { history: "rate", response: "pitch", setpoint: "pitch_setpoint", liveKey: "pitch", title: "Pitch Rate", unit: "deg/s", windowSec: 8, defaultRange: [-45, 45] },
    rate_yaw: { history: "rate", response: "yaw", setpoint: "yaw_setpoint", liveKey: "yaw", title: "Yaw Rate", unit: "deg/s", windowSec: 8, defaultRange: [-45, 45] },
    att_roll: { history: "attitude", response: "roll", setpoint: "roll_setpoint", liveKey: "att_roll", title: "Roll Attitude", unit: "deg", windowSec: 10, defaultRange: [-45, 45] },
    att_pitch: { history: "attitude", response: "pitch", setpoint: "pitch_setpoint", liveKey: "att_pitch", title: "Pitch Attitude", unit: "deg", windowSec: 10, defaultRange: [-45, 45] },
    vel_xy: { history: "velocity", response: "vx", setpoint: null, liveKey: "vx", title: "Velocity X", unit: "m/s", windowSec: 10, defaultRange: [-3, 3] },
    pos_xy: { history: "position", response: "x", setpoint: null, liveKey: "x", title: "Local Position X", unit: "m", windowSec: 12, defaultRange: [-5, 5] },
  }[view] || { history: "rate", response: "roll", setpoint: "roll_setpoint", liveKey: "roll", title: "Roll Rate", unit: "deg/s", windowSec: 8, defaultRange: [-45, 45] };
}

function decimateChartSeries(series, maxPoints) {
  const limit = Math.max(40, Math.floor(maxPoints || 600));
  if (!Array.isArray(series) || series.length <= limit) return series;
  const step = Math.ceil(series.length / limit);
  return series.filter((_, index) => index % step === 0 || index === series.length - 1);
}

function robustChartRange(values, fallback = [-1, 1]) {
  const finite = values.filter((value) => Number.isFinite(value)).sort((a, b) => a - b);
  if (!finite.length) return fallback;
  if (finite.length < 8) {
    const min = Math.min(...finite, fallback[0]);
    const max = Math.max(...finite, fallback[1]);
    return min === max ? [min - 1, max + 1] : [min, max];
  }
  const pick = (q) => finite[Math.max(0, Math.min(finite.length - 1, Math.floor((finite.length - 1) * q)))];
  let min = pick(0.04);
  let max = pick(0.96);
  const fallbackSpan = Math.abs((fallback[1] || 1) - (fallback[0] || -1));
  if (!Number.isFinite(min) || !Number.isFinite(max) || Math.abs(max - min) < fallbackSpan * 0.12) {
    min = Math.min(...finite, fallback[0]);
    max = Math.max(...finite, fallback[1]);
  }
  if (min === max) return [min - 1, max + 1];
  return [min, max];
}

const WAVEFORM_COLORS = ["#42d7ef", "#55dd83", "#ef6bb7", "#ff8a2a", "#9a5cf5", "#f4d35e", "#4ea1ff", "#ff5c7a", "#9fe870", "#d1d8e6"];

function vehicleWaveformGroups() {
  return [
    {
      id: "attitude",
      label: "姿态",
      channels: [
        { key: "attitude.roll", label: "Roll(°)", history: "attitude", field: "roll", unit: "°", color: WAVEFORM_COLORS[0] },
        { key: "attitude.pitch", label: "Pitch(°)", history: "attitude", field: "pitch", unit: "°", color: WAVEFORM_COLORS[1] },
        { key: "attitude.yaw", label: "Yaw(°)", history: "attitude", field: "yaw", unit: "°", color: WAVEFORM_COLORS[2] },
        { key: "rate.roll", label: "Roll Rate", history: "rate", field: "roll", unit: "°/s", color: WAVEFORM_COLORS[3] },
        { key: "rate.pitch", label: "Pitch Rate", history: "rate", field: "pitch", unit: "°/s", color: WAVEFORM_COLORS[4] },
        { key: "rate.yaw", label: "Yaw Rate", history: "rate", field: "yaw", unit: "°/s", color: WAVEFORM_COLORS[5] },
      ],
    },
    {
      id: "flight",
      label: "飞行数据",
      channels: [
        { key: "position.x", label: "Local X", history: "position", field: "x", unit: "m", color: WAVEFORM_COLORS[0] },
        { key: "position.y", label: "Local Y", history: "position", field: "y", unit: "m", color: WAVEFORM_COLORS[1] },
        { key: "position.z", label: "Local Z", history: "position", field: "z", unit: "m", color: WAVEFORM_COLORS[2] },
        { key: "velocity.vx", label: "Vel X", history: "velocity", field: "vx", unit: "m/s", color: WAVEFORM_COLORS[3] },
        { key: "velocity.vy", label: "Vel Y", history: "velocity", field: "vy", unit: "m/s", color: WAVEFORM_COLORS[4] },
        { key: "velocity.vz", label: "Vel Z", history: "velocity", field: "vz", unit: "m/s", color: WAVEFORM_COLORS[5] },
      ],
    },
    {
      id: "battery",
      label: "电池",
      channels: [
        { key: "battery.voltage", label: "Volt(V)", history: "battery", field: "voltage", unit: "V", color: WAVEFORM_COLORS[0] },
        { key: "battery.current", label: "Curr(A)", history: "battery", field: "current", unit: "A", color: WAVEFORM_COLORS[3] },
        { key: "battery.remaining", label: "Batt(%)", history: "battery", field: "remaining", unit: "%", color: WAVEFORM_COLORS[1] },
      ],
    },
    {
      id: "vibration",
      label: "振动",
      channels: [
        { key: "vibration.x", label: "Vibration X", history: "vibration", field: "x", unit: "", color: WAVEFORM_COLORS[0] },
        { key: "vibration.y", label: "Vibration Y", history: "vibration", field: "y", unit: "", color: WAVEFORM_COLORS[1] },
        { key: "vibration.z", label: "Vibration Z", history: "vibration", field: "z", unit: "", color: WAVEFORM_COLORS[2] },
      ],
    },
    {
      id: "accel",
      label: "加速度计",
      channels: [
        { key: "imu.xacc", label: "Accel X", history: "imu", field: "xacc", unit: "", color: WAVEFORM_COLORS[7] },
        { key: "imu.yacc", label: "Accel Y", history: "imu", field: "yacc", unit: "", color: WAVEFORM_COLORS[1] },
        { key: "imu.zacc", label: "Accel Z", history: "imu", field: "zacc", unit: "", color: WAVEFORM_COLORS[6] },
      ],
    },
    {
      id: "gyro",
      label: "陀螺仪",
      channels: [
        { key: "imu.xgyro", label: "Gyro X", history: "imu", field: "xgyro", unit: "", color: WAVEFORM_COLORS[3] },
        { key: "imu.ygyro", label: "Gyro Y", history: "imu", field: "ygyro", unit: "", color: WAVEFORM_COLORS[4] },
        { key: "imu.zgyro", label: "Gyro Z", history: "imu", field: "zgyro", unit: "", color: WAVEFORM_COLORS[0] },
      ],
    },
    {
      id: "mag",
      label: "磁力计",
      channels: [
        { key: "imu.xmag", label: "Mag X", history: "imu", field: "xmag", unit: "", color: WAVEFORM_COLORS[0] },
        { key: "imu.ymag", label: "Mag Y", history: "imu", field: "ymag", unit: "", color: WAVEFORM_COLORS[1] },
        { key: "imu.zmag", label: "Mag Z", history: "imu", field: "zmag", unit: "", color: WAVEFORM_COLORS[2] },
      ],
    },
    {
      id: "rc",
      label: "遥控器",
      channels: [1, 2, 3, 4, 5, 6].map((index, idx) => ({
        key: `rc.ch${index}`,
        label: `CH${index}`,
        history: "rc",
        field: `ch${index}`,
        unit: "",
        color: WAVEFORM_COLORS[idx % WAVEFORM_COLORS.length],
      })),
    },
    {
      id: "servo",
      label: "舵机输出",
      channels: [1, 2, 3, 4, 5, 6].map((index, idx) => ({
        key: `servo.out${index}`,
        label: `OUT${index}`,
        history: "servo",
        field: `out${index}`,
        unit: "",
        color: WAVEFORM_COLORS[(idx + 3) % WAVEFORM_COLORS.length],
      })),
    },
  ];
}

function selectedWaveformChannels() {
  return vehicleWaveformGroups()
    .flatMap((group) => group.channels)
    .filter((channel) => selectedVehicleWaveformKeys.has(channel.key));
}

function renderVehicleWaveformPanel(force = false) {
  const panel = els.vehicleWaveformPanel;
  if (!panel || renderUnavailableSetup(panel)) return;
  if (force || panel.dataset.waveformMounted !== "1") {
    panel.innerHTML = `
      <div class="waveform-layout">
        <aside class="setup-detail-card waveform-source-card">
          <div class="waveform-source-head">
            <strong>数据源</strong>
            <button type="button" class="icon-button" data-wave-clear title="清空当前显示">↻</button>
          </div>
          <div id="vehicleWaveformSources" class="waveform-source-list"></div>
        </aside>
        <section class="setup-detail-card waveform-chart-card">
          <div class="waveform-toolbar">
            <div class="segmented-row" id="vehicleWaveWindowControls">
              ${[5, 10, 30, 60].map((sec) => `<button type="button" data-wave-window="${sec}">${sec}s</button>`).join("")}
            </div>
            <button type="button" data-wave-pause title="暂停/继续">Ⅱ</button>
            <div class="segmented-row" id="vehicleWaveRateControls">
              ${[20, 50, 100].map((rate) => `<button type="button" data-wave-rate="${rate}">${rate}Hz</button>`).join("")}
              <span>仅为图表采样率</span>
            </div>
            <div id="vehicleWaveformStats" class="waveform-stats"></div>
          </div>
          <canvas id="vehicleWaveformCanvas" class="waveform-canvas"></canvas>
          <div id="vehicleWaveformLegend" class="waveform-legend"></div>
        </section>
      </div>
      ${dataSourceRibbon(["ATTITUDE", "LOCAL_POSITION_NED", "HIGHRES_IMU/SCALED_IMU", "BATTERY_STATUS", "RC_CHANNELS", "SERVO_OUTPUT_RAW"])}
    `;
    panel.dataset.waveformMounted = "1";
  }
  updateVehicleWaveformChrome();
  requestAnimationFrame(drawVehicleWaveformChart);
}

function updateVehicleWaveformChrome() {
  const panel = els.vehicleWaveformPanel;
  if (!panel) return;
  const sourceList = document.getElementById("vehicleWaveformSources");
  if (sourceList) {
    sourceList.innerHTML = vehicleWaveformGroups().map((group, groupIndex) => `
      <details class="waveform-group" ${groupIndex < 4 ? "open" : ""}>
        <summary>${escapeHtml(group.label)}</summary>
        ${group.channels.map((channel) => `
          <label class="waveform-channel">
            <input type="checkbox" data-wave-key="${escapeHtml(channel.key)}" ${selectedVehicleWaveformKeys.has(channel.key) ? "checked" : ""}>
            <i style="background:${escapeHtml(channel.color)}"></i>
            <span>${escapeHtml(channel.label)}</span>
            <strong>${escapeHtml(latestWaveformValue(channel))}</strong>
          </label>
        `).join("")}
      </details>
    `).join("");
  }
  panel.querySelectorAll("[data-wave-window]").forEach((button) => {
    button.classList.toggle("active", Number(button.dataset.waveWindow) === activeWaveformWindowSec);
  });
  panel.querySelectorAll("[data-wave-rate]").forEach((button) => {
    button.classList.toggle("active", Number(button.dataset.waveRate) === activeWaveformSampleHz);
  });
  const pause = panel.querySelector("[data-wave-pause]");
  if (pause) pause.textContent = vehicleWaveformPaused ? "▶" : "Ⅱ";
}

function waveformHistory() {
  return vehicleWaveformPaused && vehicleWaveformFrozenHistory ? vehicleWaveformFrozenHistory : (setupSnapshot().history || {});
}

function latestWaveformValue(channel) {
  const entries = waveformHistory()[channel.history] || [];
  for (let index = entries.length - 1; index >= 0; index -= 1) {
    const value = entries[index]?.[channel.field];
    if (value !== null && value !== undefined && Number.isFinite(Number(value))) {
      return `${Number(value).toFixed(Math.abs(Number(value)) >= 100 ? 0 : 2)}${channel.unit || ""}`;
    }
  }
  return "--";
}

function drawVehicleWaveformChart() {
  const canvas = document.getElementById("vehicleWaveformCanvas");
  if (!canvas) return;
  const channels = selectedWaveformChannels();
  const history = waveformHistory();
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(680, Math.floor(rect.width || 900));
  const height = Math.max(360, Math.floor(rect.height || 520));
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
  ctx.fillStyle = "#080a10";
  ctx.fillRect(0, 0, width, height);
  const plot = { x: 58, y: 20, w: width - 82, h: height - 54 };
  const lastSec = Math.max(0, ...channels.flatMap((channel) => (history[channel.history] || []).map((point) => Number(point.sec || 0))));
  const startSec = Math.max(0, lastSec - activeWaveformWindowSec);
  const series = channels.map((channel) => {
    const raw = Array.isArray(history[channel.history]) ? history[channel.history] : [];
    const points = raw
      .filter((point) => Number(point.sec || 0) >= startSec)
      .map((point) => ({ x: Number(point.sec || 0) - lastSec, y: Number(point[channel.field]) }))
      .filter((point) => Number.isFinite(point.x) && Number.isFinite(point.y));
    return { channel, points: decimateChartSeries(points, activeWaveformSampleHz * activeWaveformWindowSec) };
  });
  const values = series.flatMap((item) => item.points.map((point) => point.y));
  let [minY, maxY] = robustChartRange(values, [-1, 1]);
  const padY = Math.max(0.5, (maxY - minY) * 0.1);
  minY -= padY;
  maxY += padY;
  const xFor = (x) => plot.x + ((x + activeWaveformWindowSec) / activeWaveformWindowSec) * plot.w;
  const yFor = (y) => plot.y + plot.h - ((y - minY) / (maxY - minY)) * plot.h;

  ctx.strokeStyle = "rgba(255,255,255,0.07)";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 10; i += 1) {
    const x = plot.x + (plot.w * i) / 10;
    ctx.beginPath();
    ctx.moveTo(x, plot.y);
    ctx.lineTo(x, plot.y + plot.h);
    ctx.stroke();
  }
  for (let i = 0; i <= 5; i += 1) {
    const y = plot.y + (plot.h * i) / 5;
    ctx.beginPath();
    ctx.moveTo(plot.x, y);
    ctx.lineTo(plot.x + plot.w, y);
    ctx.stroke();
  }
  ctx.strokeStyle = "rgba(210,218,235,0.2)";
  ctx.strokeRect(plot.x, plot.y, plot.w, plot.h);
  ctx.save();
  ctx.beginPath();
  ctx.rect(plot.x, plot.y, plot.w, plot.h);
  ctx.clip();
  series.forEach(({ channel, points }) => {
    if (!points.length) return;
    ctx.strokeStyle = channel.color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    points.forEach((point, index) => {
      const x = xFor(point.x);
      const y = yFor(point.y);
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  });
  ctx.restore();

  ctx.fillStyle = "rgba(210,218,235,0.62)";
  ctx.font = "12px Inter, Segoe UI, sans-serif";
  ctx.textAlign = "right";
  ctx.fillText(maxY.toFixed(1), plot.x - 8, plot.y + 4);
  ctx.fillText(minY.toFixed(1), plot.x - 8, plot.y + plot.h);
  ctx.textAlign = "center";
  for (let i = 0; i <= 5; i += 1) {
    const seconds = -activeWaveformWindowSec + (activeWaveformWindowSec * i) / 5;
    ctx.fillText(`${seconds.toFixed(0)}s`, plot.x + (plot.w * i) / 5, height - 14);
  }
  if (!channels.length || !series.some((item) => item.points.length)) {
    ctx.fillStyle = "rgba(210,218,235,0.72)";
    ctx.fillText(channels.length ? "等待所选 MAVLink 数据..." : "在左侧选择要显示的通道", plot.x + plot.w / 2, plot.y + plot.h / 2);
  }
  const stats = document.getElementById("vehicleWaveformStats");
  if (stats) {
    const samples = series.reduce((sum, item) => sum + item.points.length, 0);
    stats.textContent = `${channels.length} 通道 · ${samples} 采样`;
  }
  const legend = document.getElementById("vehicleWaveformLegend");
  if (legend) {
    legend.innerHTML = channels.map((channel) => `<span><i style="background:${escapeHtml(channel.color)}"></i>${escapeHtml(channel.label)}</span>`).join("");
  }
}

function parameterStatusText(status) {
  return {
    disconnected: "未连接",
    not_requested: "未请求",
    idle: "未请求",
    receiving: "正在接收",
    downloading: "正在读取",
    ready: "已完成",
    partial: "部分收到",
    busy: "运行时忙",
    error: "读取失败",
  }[String(status || "").toLowerCase()] || valueText(status);
}

function parameterCountText(info = {}) {
  const received = Number(info.received_count || 0);
  const expected = Number(info.expected_count || 0);
  if (expected > 0) return `${received} / ${expected}`;
  return received ? `${received}` : "--";
}

function parameterProgressText(info = {}) {
  const progress = Number(info.progress);
  if (Number.isFinite(progress)) return `${Math.round(progress * 100)}%`;
  return "--";
}

function timestampText(value) {
  const seconds = Number(value || 0);
  if (!Number.isFinite(seconds) || seconds <= 0) return "--";
  return new Date(seconds * 1000).toLocaleTimeString();
}

function renderVehicleParameterSummary(info = vehicleParametersCache || currentVehicleInfo().parameters || {}) {
  if (!els.vehicleParameterSummary) return;
  const runtime = latestState?.tool_runtime || {};
  const connected = setupConnected() || (Boolean(runtime.connected) && !runtime.stale_connection);
  const statusClass = connected && info.status === "ready" ? "ready" : (connected ? "pending" : "offline");
  els.vehicleParameterSummary.innerHTML = `
    <div class="parameter-stat ${statusClass}">
      <span>状态</span>
      <strong>${escapeHtml(parameterStatusText(info.status || (connected ? "not_requested" : "disconnected")))}</strong>
    </div>
    <div class="parameter-stat">
      <span>收到/总数</span>
      <strong>${escapeHtml(parameterCountText(info))}</strong>
    </div>
    <div class="parameter-stat">
      <span>进度</span>
      <strong>${escapeHtml(parameterProgressText(info))}</strong>
    </div>
    <div class="parameter-stat">
      <span>最近收到</span>
      <strong>${escapeHtml(timestampText(info.last_message_at))}</strong>
    </div>
  `;
}

function renderParameterGroupChips() {
  const groups = setupSnapshot().parameter_groups || {};
  const entries = Object.entries(groups).slice(0, 18);
  if (!entries.length) return "";
  return `
    <div class="parameter-groups">
      <button type="button" data-param-group="">全部</button>
      ${entries.map(([name, count]) => `<button type="button" data-param-group="${escapeHtml(name)}">${escapeHtml(name)} <span>${escapeHtml(valueText(count))}</span></button>`).join("")}
    </div>
  `;
}

async function saveVehicleParameterFromRow(button) {
  const row = button.closest("[data-param-name]");
  if (!row) return;
  const input = row.querySelector("[data-param-input]");
  const name = row.dataset.paramName || "";
  const value = input ? input.value : "";
  const componentId = row.dataset.paramComponent || "";
  const paramType = row.dataset.paramType || "";
  const oldText = button.textContent;
  button.disabled = true;
  button.textContent = "写入中";
  try {
    const result = await post("/api/settings/vehicle-parameters/set", {
      name,
      value,
      component_id: componentId,
      param_type: paramType,
      timeout: 3.5,
    });
    const payload = result.parameter_write || result;
    if (!result.ok || payload.status !== "ok") {
      throw new Error(payload.message || result.error || "parameter write failed");
    }
    const updated = payload.parameter || {};
    if (vehicleParametersCache?.parameters && updated.name) {
      const idx = vehicleParametersCache.parameters.findIndex((param) =>
        param.name === updated.name && String(param.component_id || "") === String(updated.component_id || "")
      );
      if (idx >= 0) vehicleParametersCache.parameters[idx] = { ...vehicleParametersCache.parameters[idx], ...updated };
    }
    showNotice(`${name} 已写入并收到飞控确认`, "success");
    await loadVehicleParameters(false);
    await loadVehicleSetup(true);
  } catch (error) {
    showNotice(`参数写入失败: ${error.message || "未知错误"}`, "error");
  } finally {
    button.disabled = false;
    button.textContent = oldText || "保存";
  }
}

function renderVehicleParametersPanel() {
  const panel = els.vehicleParametersPanel;
  if (!panel) return;
  const runtime = latestState?.tool_runtime || {};
  const connected = setupConnected() || (Boolean(runtime.connected) && !runtime.stale_connection);
  const info = vehicleParametersCache || currentVehicleInfo().parameters || {};
  const parameters = Array.isArray(info.parameters) ? info.parameters : [];
  renderVehicleParameterSummary(info);

  if (vehicleParametersLoading && !parameters.length) {
    panel.innerHTML = `${renderParameterGroupChips()}<div class="parameter-empty">正在读取 PX4 参数...</div>`;
    return;
  }
  if (!connected) {
    panel.innerHTML = `${renderParameterGroupChips()}<div class="parameter-empty">未连接 PX4，连接后可读取参数。</div>`;
    return;
  }
  if (!parameters.length) {
    const status = parameterStatusText(info.status || "not_requested");
    const message = info.message ? ` · ${info.message}` : "";
    panel.innerHTML = `${renderParameterGroupChips()}<div class="parameter-empty">${escapeHtml(status + message)}</div>`;
    return;
  }

  panel.innerHTML = `
    ${renderParameterGroupChips()}
    <div class="parameter-edit-hint">逐项保存会发送 MAVLink PARAM_SET，并等待飞控回传 PARAM_VALUE 确认；不会批量写入，也不会自动重启飞控。</div>
    <div class="parameter-table-wrap">
      <table class="parameter-table">
        <thead>
          <tr>
            <th>名称</th>
            <th>值 / 写入</th>
            <th>类型</th>
            <th>组件</th>
            <th>Index</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          ${parameters.map((param) => `
            <tr data-param-name="${escapeHtml(param.name || "")}" data-param-component="${escapeHtml(valueText(param.component_id, ""))}" data-param-type="${escapeHtml(valueText(param.type, ""))}">
              <td><code>${escapeHtml(param.name || "")}</code></td>
              <td>
                <input class="parameter-value-input" data-param-input type="text" value="${escapeHtml(valueText(param.value_text ?? param.value, ""))}" spellcheck="false">
              </td>
              <td>${escapeHtml(valueText(param.type_name || param.type))}</td>
              <td>${escapeHtml(valueText(param.component_id))}</td>
              <td>${escapeHtml(valueText(param.index))}</td>
              <td><button type="button" class="parameter-save-btn" data-param-save>保存</button></td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
    <div class="parameter-footer">
      <span>${escapeHtml(`显示 ${parameters.length} / ${Number(info.total || parameters.length)} 条匹配参数`)}</span>
      ${vehicleParametersLoading ? "<span>读取中...</span>" : ""}
    </div>
  `;
}

function renderConnectionsList() {
  normalizeSystemSettingsCopy();
  const list = els.connectionsList;
  if (!list) return;
  const connections = Array.isArray(connectionsCache) ? connectionsCache : [];
  if (!connections.length) {
    list.innerHTML = `<div class="empty" style="padding:12px 4px;color:var(--faint);font-size:12px;text-align:center;">No links yet. Add one below.</div>`;
    return;
  }
  list.innerHTML = connections.map((conn) => {
    const activeClass = conn.id === selectedConnectionId ? "active" : "";
    const connectedClass = isConnectionActive(conn.id) ? "connected" : "";
    return `
      <div class="connection-item ${activeClass} ${connectedClass}" data-connection-id="${escapeHtml(conn.id)}" role="button" tabindex="0" aria-pressed="${conn.id === selectedConnectionId ? "true" : "false"}">
        <span class="link-dot"></span>
        <div class="connection-item-info">
          <strong>${escapeHtml(conn.name || "Unnamed link")}</strong>
          <span>${escapeHtml(connectionTypeLabel(conn.type))} / ${escapeHtml(connectionParamsSummary(conn.params || {}, conn.type))}</span>
        </div>
        <span class="link-chevron">&gt;</span>
      </div>
    `;
  }).join("");
}

function renderConnectionDetail(connectionId) {
  normalizeSystemSettingsCopy();
  selectedConnectionId = connectionId || "";
  renderConnectionsList();

  const conn = selectedConnectionId ? connectionsCache.find((c) => c.id === selectedConnectionId) : null;
  const form = els.connectionDetailForm;
  if (!form) return;

  if (!conn) {
    form.reset();
    if (els.connectionDetailId) els.connectionDetailId.value = "";
    if (els.connectionDetailType) els.connectionDetailType.value = "udp";
    updateConnectionDetailStatus(false);
    if (els.connectionDetailConnect) els.connectionDetailConnect.textContent = "连接";
    if (els.connectionDetailDelete) els.connectionDetailDelete.hidden = true;
    updateConnectionTypeFields();
    renderActualLinkCard();
    renderAirSimSettingsForConnection();
    return;
  }

  const params = conn.params || {};
  if (els.connectionDetailId) els.connectionDetailId.value = conn.id || "";
  if (els.connectionDetailName) els.connectionDetailName.value = conn.name || "";
  if (els.connectionDetailType) els.connectionDetailType.value = conn.type || "udp";
  if (els.connectionDetailPort) els.connectionDetailPort.value = params.port || "";
  if (els.connectionDetailHost) els.connectionDetailHost.value = params.host || params.url || "";
  if (els.connectionDetailAddress) els.connectionDetailAddress.value = params.address || "";
  if (els.connectionDetailPortNumber) els.connectionDetailPortNumber.value = params.portNumber || params.baud || "";
  if (els.connectionDetailRemotePort) els.connectionDetailRemotePort.value = params.remotePort || "";
  if (els.connectionDetailRealVehicle) els.connectionDetailRealVehicle.checked = Boolean(params.realVehicle);
  updateConnectionTypeFields();

  const actuallyActive = isConnectionActive(conn.id);
  updateConnectionDetailStatus(actuallyActive);
  if (els.connectionDetailConnect) els.connectionDetailConnect.textContent = actuallyActive ? "断开" : "连接";
  if (els.connectionDetailDelete) els.connectionDetailDelete.hidden = false;
  renderActualLinkCard();
  // 切换预设后, AirSim settings.json 模板按当前 type 重新计算
  renderAirSimSettingsForConnection();
}

function updateConnectionDetailStatus(connected) {
  if (!els.connectionDetailStatus) return;
  els.connectionDetailStatus.classList.toggle("connected", connected);
  const text = els.connectionDetailStatus.querySelector(".status-text");
  if (text) text.textContent = connected ? "已连接" : "未连接";
}

function updateConnectionTypeFields() {
  normalizeSystemSettingsCopy();
  const type = els.connectionDetailType ? els.connectionDetailType.value : "udp";
  const serialFields = document.getElementById("serialFieldsDetail");
  const udpFields = document.getElementById("udpFieldsDetail");
  const tcpFields = document.getElementById("tcpFieldsDetail");
  const portFields = document.getElementById("portFieldsDetail");
  const remotePortFields = document.getElementById("remotePortFieldsDetail");
  const realVehicleFields = document.getElementById("realVehicleFieldsDetail");
  const usesHostLikeField = ["udp", "airsim", "auto", "px4_ros2"].includes(type);
  if (serialFields) serialFields.hidden = type !== "serial";
  if (udpFields) udpFields.hidden = !usesHostLikeField;
  if (tcpFields) tcpFields.hidden = type !== "tcp";
  if (remotePortFields) remotePortFields.hidden = type !== "udp" && type !== "auto";
  if (realVehicleFields) realVehicleFields.hidden = type === "airsim" || type === "px4_ros2";
  const hostLabel = udpFields?.querySelector("label");
  if (hostLabel) {
    hostLabel.textContent = type === "px4_ros2"
      ? "ROS Gateway URL"
      : type === "airsim"
        ? "AirSim host"
        : "PX4 host";
  }
  if (els.connectionDetailHost) {
    els.connectionDetailHost.placeholder = type === "px4_ros2"
      ? "http://127.0.0.1:8766"
      : type === "airsim"
        ? "127.0.0.1"
        : "127.0.0.1 or vehicle IP";
  }
  if (portFields) {
    const label = portFields.querySelector("label");
    if (label) {
      label.textContent = type === "serial"
        ? "Baud"
        : type === "airsim"
          ? "AirSim port"
          : "Local port";
    }
    if (els.connectionDetailPortNumber) {
      els.connectionDetailPortNumber.placeholder = type === "serial"
        ? "57600, 115200, or 921600"
        : type === "airsim"
          ? "41452"
          : "14550, 14540, or 5760";
    }
    portFields.hidden = !["serial", "udp", "tcp", "airsim", "auto"].includes(type);
  }
  // 连接类型切换后, AirSim settings.json 模板区按当前 type 联动
  if (typeof renderAirSimSettingsForConnection === "function") {
    renderAirSimSettingsForConnection();
  }
}

function normalizeRosGatewayUrl(value) {
  const text = String(value || "").trim();
  if (!text) return "http://127.0.0.1:8766";
  if (/^https?:\/\//i.test(text)) return text;
  return `http://${text}`;
}

function readConnectionDetailForm() {
  const id = String(els.connectionDetailId?.value || "").trim();
  const name = String(els.connectionDetailName?.value || "").trim();
  const type = String(els.connectionDetailType?.value || "udp").trim();
  if (!name) {
    return { ok: false, error: "Connection name is required." };
  }
  const existing = id ? connectionsCache.find((c) => c.id === id) : null;
  const params = {};
  if (type === "serial") params.port = String(els.connectionDetailPort?.value || "").trim();
  if (type === "udp" || type === "airsim" || type === "auto") params.host = String(els.connectionDetailHost?.value || "").trim();
  if (type === "px4_ros2") {
    params.url = normalizeRosGatewayUrl(els.connectionDetailHost?.value || existing?.params?.url);
    params.workspace = String(existing?.params?.workspace || "$HOME/ws_px4").trim();
  }
  if (type === "tcp") params.address = String(els.connectionDetailAddress?.value || "").trim();
  if (type === "serial") params.baud = String(els.connectionDetailPortNumber?.value || "").trim();
  if (type === "udp" || type === "tcp" || type === "airsim" || type === "auto") {
    params.portNumber = String(els.connectionDetailPortNumber?.value || "").trim();
  }
  if (type === "udp" || type === "auto") params.remotePort = String(els.connectionDetailRemotePort?.value || "").trim();
  if (type !== "airsim" && type !== "px4_ros2") params.realVehicle = Boolean(els.connectionDetailRealVehicle?.checked);

  return {
    ok: true,
    connection: {
      id: id || `conn_${Date.now()}`,
      name,
      type,
      params,
    },
    isNew: !id,
  };
}

async function persistConnectionDetailFromForm({ notify = true } = {}) {
  const read = readConnectionDetailForm();
  if (!read.ok) {
    showNotice(read.error || "Connection settings are invalid.", "error");
    return null;
  }
  const connection = read.connection;
  const id = String(connection.id || "").trim();
  if (id) {
    const idx = connectionsCache.findIndex((c) => c.id === id);
    if (idx >= 0) {
      connectionsCache[idx] = { ...connectionsCache[idx], ...connection };
    } else {
      connectionsCache.push(connection);
    }
  }
  selectedConnectionId = id;
  const saved = await saveConnectionSettings();
  if (!saved) return null;
  renderConnectionsList();
  renderConnectionDetail(selectedConnectionId);
  if (notify) showNotice(read.isNew ? "Connection added." : "Connection updated.", "success");
  return connectionsCache.find((c) => c.id === selectedConnectionId) || connection;
}

async function submitConnectionDetail(event) {
  event.preventDefault();
  await persistConnectionDetailFromForm({ notify: true });
}

function connectionFailureHint(conn, backend, toolData = {}) {
  const params = conn?.params || {};
  const attemptedUrl = toolData.url || toolData.ros_bridge_url || params.url || params.host || "";
  if (backend === "px4_ros2") {
    const healthUrl = String(attemptedUrl || "http://127.0.0.1:8766").replace(/\/+$/, "") + "/health";
    return `检查 Windows 是否能访问 ${healthUrl}。`;
  }
  if (backend === "px4_mavlink") {
    const endpoint = toolData.requested_url || toolData.url || params.host || "selected endpoint";
    return `检查 PX4 SITL MAVLink 端口和所选端点 ${endpoint}。`;
  }
  if (backend === "airsim") {
    const host = params.host || "127.0.0.1";
    const port = params.portNumber || "41452";
    return `检查 AirSim 是否运行在 ${host}:${port}。`;
  }
  return "检查所选连接参数。";
}

async function activateSelectedConnection() {
  if (!selectedConnectionId) return;
  let conn = connectionsCache.find((c) => c.id === selectedConnectionId);
  if (!conn) return;

  const actuallyActive = isConnectionActive(conn.id);
  if (!actuallyActive) {
    const formId = String(els.connectionDetailId?.value || "").trim();
    if (!formId || formId === conn.id) {
      const savedConn = await persistConnectionDetailFromForm({ notify: false });
      if (!savedConn) return;
      conn = savedConn;
    }
  }
  showNotice(actuallyActive ? "Disconnecting..." : `Connecting ${conn.name}...`, "info");

  try {
    const resp = await fetch("/api/settings/connections/activate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ connection_id: conn.id }),
    });
    const result = await resp.json();
    const toolData = result?.result?.data || {};
    const backend = result?.backend || toolData.backend || "";
    const backendLabel = backendLabelFromId(backend);

    if (result && result.ok && result.action === "disconnect") {
      activeConnectionId = "";
      showNotice("Disconnected.", "info");
    } else if (result && result.ok && result.result && result.result.ok) {
      activeConnectionId = conn.id;
      showNotice(`Connected to ${conn.name}.`, "success");
    } else {
      activeConnectionId = "";
      const rawMsg = toolData.message || result?.error || "";
      const hint = connectionFailureHint(conn, backend, toolData);
      showNotice(rawMsg ? `${rawMsg} - ${hint}` : `${backendLabel} connection failed - ${hint}`, "error");
    }
  } catch (error) {
    activeConnectionId = "";
    showNotice(error.message || "Connection switch failed.", "error");
  }
  await loadConnectionSettings(true);
  if (activeSystemSettingsSection === "parameters") {
    await loadVehicleParameters(false);
  }
  renderConnectionsList();
  renderConnectionDetail(selectedConnectionId);
  await refresh();
}

function connectionMatchesBackend(connection, backend) {
  const type = String(connection?.type || "").toLowerCase();
  const normalized = String(backend || "").toLowerCase();
  if (normalized === "airsim") return type === "airsim";
  if (normalized === "px4_ros2") return ["px4_ros2", "ros2", "ros", "px4_ros"].includes(type);
  if (normalized === "px4_mavlink") return ["auto", "udp", "tcp", "serial", "mavlink", "px4"].includes(type);
  return false;
}

function renderSystemConnection(drone = {}, toolRuntime = {}) {
  const connected = Boolean(toolRuntime.connected) && !toolRuntime.stale_connection;
  let activeChanged = false;

  if (connected && connectionsCache.length) {
    const backend = toolRuntime.backend || toolRuntime.backend_profile?.id || "airsim";
    const current = connectionsCache.find((connection) => connection.id === activeConnectionId);
    const expected = current && connectionMatchesBackend(current, backend)
      ? current
      : connectionsCache.find((connection) => connectionMatchesBackend(connection, backend));
    if (expected && expected.id !== activeConnectionId) {
      activeConnectionId = expected.id;
      activeChanged = true;
    }
  } else if (activeConnectionId) {
    activeConnectionId = "";
    activeChanged = true;
  }

  if (activeChanged) {
    renderConnectionsList();
    if (selectedConnectionId) renderConnectionDetail(selectedConnectionId);
  }
  if (els.connectionDetailStatus) {
    const actuallyActive = selectedConnectionId ? isConnectionActive(selectedConnectionId) : connected;
    updateConnectionDetailStatus(actuallyActive);
  }
  renderActualLinkCard();
  renderActiveVehicleSetupPanel("runtime");
  if (activeSystemSettingsSection === "parameters") {
    renderVehicleParametersPanel();
  }
}

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

connectEventStream();
initAirSimTemplatesEvents();

// Initial render with defaults so the page is never blank
function renderInitialDefaults() {
  renderChat([], null, {});
  if (latestState) updateMapView(latestState);
}
renderInitialDefaults();

// Refresh state, but always render something even on failure
async function initialRefresh() {
  try {
    latestState = applyCachedSessionHistory(await api("/api/state"));
    render(latestState);
    await loadCurrentSessionHistory();
  } catch (_) {
    // Keep default render from above
  }
}
initialRefresh();

restartMainTelemetryRefresh();
setInterval(() => refresh().catch(() => {}), 6000);
window.addEventListener("resize", () => {
  if (maplibreMap) maplibreMap.resize();
});
