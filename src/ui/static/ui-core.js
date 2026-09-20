// ui-core.js —— 共享状态、常量与通用工具（els 表、缓存、格式化、请求封装）
// 由 app.js 拆分而来；各文件共享同一份脚本作用域，按顺序加载。

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
  modelDropdown: $("modelDropdown"),
  reasoningBtn: $("reasoningBtn"),
  reasoningLabel: $("reasoningLabel"),
  reasoningMenu: $("reasoningMenu"),
  reasoningDropdown: $("reasoningDropdown"),
  contextUsage: $("contextUsage"),
  contextPopover: $("contextPopover"),
  contextDropdown: $("contextDropdown"),
  newSessionBtn: $("newSessionBtn"),
  sessionSwitcher: $("sessionSwitcher"),
  sessionSwitcherBtn: $("sessionSwitcherBtn"),
  sessionMenu: $("sessionMenu"),
  chatArea: $("chatArea"),
  chatRail: $("chatRail"),
  railTip: $("railTip"),
  sessionsSearch: $("sessionsSearch"),
  sessionsNewBtn: $("sessionsNewBtn"),
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
  cameraRtspTransportRow: $("cameraRtspTransportRow"),
  cameraRtspTransport: $("cameraRtspTransport"),
  cameraNameRow: $("cameraNameRow"),
  cameraVehicleRow: $("cameraVehicleRow"),
  cameraImageTypeRow: $("cameraImageTypeRow"),
  cameraSourceHint: $("cameraSourceHint"),
  cameraName: $("cameraName"),
  cameraVehicle: $("cameraVehicle"),
  cameraImageType: $("cameraImageType"),
  cameraTimeout: $("cameraTimeout"),
  cameraAutoSave: $("cameraAutoSave"),
  cameraSaveSettingsBtn: $("cameraSaveSettingsBtn"),
  cameraCaptureFromSettingsBtn: $("cameraCaptureFromSettingsBtn"),
  appDensity: $("appDensity"),
  appMapLayer: $("appMapLayer"),
  appTelemetryRefresh: $("appTelemetryRefresh"),
  appSetupRefresh: $("appSetupRefresh"),
  appHistorySeconds: $("appHistorySeconds"),
  appFollowVehicle: $("appFollowVehicle"),
  appShowTrack: $("appShowTrack"),
  appMissionAltitude: $("appMissionAltitude"),
  appMissionSpeed: $("appMissionSpeed"),
  appMissionHold: $("appMissionHold"),
  appMissionAccept: $("appMissionAccept"),
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
  importSkillBtn: $("importSkillBtn"),
  skillImportInput: $("skillImportInput"),
  modelEditId: $("modelEditId"),
  modelName: $("modelName"),
  modelModelId: $("modelModelId"),
  modelApiType: $("modelApiType"),
  modelBaseUrl: $("modelBaseUrl"),
  fetchModelListBtn: $("fetchModelListBtn"),
  providerModelOptions: $("providerModelOptions"),
  providerModelHint: $("providerModelHint"),
  modelApiKey: $("modelApiKey"),
  modelRevealKey: $("modelRevealKey"),
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
// 自动跟随最新输出：只要用户仍停在底部就继续跟随。用"更新 DOM 之前"的
// 位置判断，避免一次新增很多内容后距离超过阈值而停止跟随（表现为必须手动
// 往下滑才能看到最新输出）。
let chatAutoFollow = true;
let chatFollowBound = false;
let chatContentObserver = null;
// 记录"程序触发的贴底滚动"时间戳：滚动事件无法区分用户滚动和我们自己设置
// scrollTop，若不加区分，加载/流式过程中我们自己的滚动会被误判成"用户往上
// 滚了"，从而关闭自动跟随（表现为之后必须手动下滑）。
let chatProgrammaticScrollAt = 0;

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
// 高度剖面图交互：最近一次绘制的几何用于命中测试，拖拽时冻结 Y 轴范围避免抖动
let missionProfileView = null;
let missionProfileDrag = null;
let missionProfileHover = null;
let profileRedrawScheduled = false;
const PROFILE_ALT_MAX_M = 200;
const PROFILE_HIT_RADIUS_PX = 16;
// 航点类型配色（地图 sprite 与剖面图共用）
const WAYPOINT_TYPE_COLORS = {
  waypoint: "#55dff4",
  takeoff: "#4ee6a4",
  land: "#f0b84a",
  rtl: "#ff5b6e",
};

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
  map: { default_layer: "satellite", follow_vehicle: true, show_vehicle_track: false },
  telemetry: { refresh_ms: 250, setup_refresh_ms: 100, history_seconds: 60, chart_sample_hz: 20 },
  mission: { default_altitude_m: 3, default_speed_mps: 2, default_hold_s: 0, default_acceptance_radius_m: 2 },
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
  url: "",
  // RTSP 传输协议：真机走 WiFi/图传时 UDP 丢包会表现为花屏或卡死，
  // 所以默认 TCP（与 QGC 视频链路的可选项一致，只是默认值不同）。
  transport: "tcp",
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

let liveConnectionCache = { key: "", id: "" };

// ── 思考力度：档位来自模型能力（后端按厂商目录 / 模型族给出），不硬套四档 ──
const REASONING_LEVEL_META = {
  low: { label: "低", bars: 1 },
  medium: { label: "中", bars: 2 },
  high: { label: "高", bars: 3 },
  max: { label: "最大", bars: 4 },
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) {
    const failure = new Error(extractApiError(data, response.statusText));
    failure.data = data;
    throw failure;
  }
  if (data && data.ok === false) {
    throw new Error(extractApiError(data, "request failed"));
  }
  return data;
}

function post(path, payload) {
  return api(path, { method: "POST", body: JSON.stringify(payload) });
}

const commandSubmitButton = els.commandForm?.querySelector("button[type='submit']");
function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error || new Error("图片读取失败"));
    reader.readAsDataURL(file);
  });
}

const SETTINGS_EXPAND_SVG = '<svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 6V2.5h3.5M13.5 6V2.5h-3.5M2.5 10v3.5h3.5M13.5 10v3.5h-3.5"/></svg>';
const SETTINGS_COLLAPSE_SVG = '<svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M5.5 2.5v3H2.5M10.5 2.5v3h3M5.5 13.5v-3H2.5M10.5 13.5v-3h3"/></svg>';
let settingsSavedSize = null;

// 地图右侧航点面板折叠
const waypointPanelToggle = document.getElementById("waypointPanelToggle");
const waypointPanel = document.getElementById("waypointPanel");
// ---- 多机航线规划目标机：由左上角无人机 chips 点击切换（无下拉框）----
// 每架机一条航线一种颜色，一键派发后各机执行各自航线。

const VEHICLE_ROUTE_PALETTE = ["#55dff4", "#f0b84a", "#64e1ae", "#ff5b6e", "#b18cff", "#ffd166"];

// ---- 目标机选择（单选）：点芯片选中一台，再点一下取消 ----
// 未选中 = 左侧小工具作用于全部无人机；选中 = 只作用于该机。
// 选中的机同时就是航线规划目标。
let controlSelectionVehicle = "";

function easeInOutCubic(t) {
  return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
}

function currentDefaultAltitude() {
  const drone = latestState?.tool_runtime?.drone || {};
  const z = Number(drone.position_ned?.z || 0);
  if (z < -0.8) return Math.abs(z);
  return missionDefaults().altitude;
}

// 多机航线完成监控：本轮派发的所有机都到达各自终点并悬停后提示一次，
// 并复位任务执行状态（missionExecutionActive）。
let activeFlightTaskVehicles = [];

// 返航完成监控：派发返航后，等所有目标机落地锁定再提示
let activeReturnHomeVehicles = [];

let sessionFilter = "";
let renamingSession = false;

let railSyncRaf = 0;
// 审计日志：说清"什么时候该看它"，并按级别过滤 + 人话标签
const EVENT_LEVEL_LABELS = { error: "错误", warning: "警告", info: "信息" };
const EVENT_SOURCE_LABELS = {
  tool: "工具",
  llm: "模型",
  system: "系统",
  agent: "Agent",
  planner: "规划",
  safety: "安全",
  runtime: "运行时",
  ui: "界面",
};
let eventLevelFilter = "all";

const WAYPOINT_TYPE_LABELS = {
  waypoint: "航点",
  takeoff: "起飞",
  land: "降落",
  rtl: "返航",
};

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

// 设置面板只保留「通信链路 / 摄像头」：其余分区（通用、地图遥测、任务默认值、
// 安全、以及整组 Vehicle Settings）已移除，Vehicle Settings 交给 QGC。
const SYSTEM_SETTINGS_SECTIONS = ["links", "camera"];

function screenToNed(px, py) {
  return {
    x: (mapTransform.cy - py) / mapTransform.scale,
    y: (px - mapTransform.cx) / mapTransform.scale,
  };
}

function fmt(value) {
  const n = Number(value || 0);
  return n.toFixed(Math.abs(n) >= 10 ? 0 : 1);
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

// 统一节点外壳：图标 + 标题（模型思考 / 工具调用 / 技能调用 / 校验），
// 让思考块与工具块处于同一层级、同一左对齐线。
function nodeShell(cat, extraClass = "") {
  const node = document.createElement("div");
  node.className = `tl-node tl-${cat}${extraClass ? " " + extraClass : ""}`;
  const head = document.createElement("div");
  head.className = "tl-node-head";
  const icon = document.createElement("i");
  icon.className = "tl-node-icon";
  icon.textContent = cat === "reasoning" ? "🧠"
    : cat === "skill" ? "🧩"
    : cat === "verify" ? "📋"
    : "🛠";
  const label = document.createElement("span");
  label.className = "tl-node-label";
  label.textContent = cat === "reasoning" ? "模型思考"
    : cat === "skill" ? "技能调用"
    : cat === "verify" ? "校验"
    : "工具调用";
  head.appendChild(icon);
  head.appendChild(label);
  node.appendChild(head);
  return node;
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

function smoothFlushMessage(id) {
  smoothStream.targets.delete(id);
  smoothStream.shown.delete(id);
}

let skillFilter = "";

function formatNumber(value, digits = 1, unit = "") {
  if (value === null || value === undefined || value === "") return "--";
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  return `${number.toFixed(digits)}${unit}`;
}

const WAVEFORM_COLORS = ["#42d7ef", "#55dd83", "#ef6bb7", "#ff8a2a", "#9a5cf5", "#f4d35e", "#4ea1ff", "#ff5c7a", "#9fe870", "#d1d8e6"];

