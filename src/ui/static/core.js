/* // 核心：DOM 元素表(els)、全局状态、常量表、设置加载/保存、车辆信息轮询（在 camera/net 之前加载） */

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









