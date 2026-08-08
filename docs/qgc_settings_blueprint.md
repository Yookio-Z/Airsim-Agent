# QGC-style settings and vehicle configuration blueprint

> 文档状态：QGC 风格设置页面的专题设计。它不是当前系统总体路线；阶段优先级以 [`system_upgrade_plan.md`](system_upgrade_plan.md) 为准。

Date: 2026-07-22

This blueprint is based on the local QGroundControl source under `third_party/qgroundcontrol`.
It is not a proposal to copy all of QGC at once. The immediate goal is to restructure our
settings system around the same concepts QGC uses, then implement the safe/read-only layers
before adding parameter writes, calibration, firmware flashing, or motor output operations.

## QGC source findings

### Entry points

- `src/MainWindow/MainWindow.qml`
  - `showSettingsTool()` opens `Application Settings`.
  - `showVehicleConfig()` opens `Vehicle Configuration`.
- `src/QmlControls/AppSettings.qml`
  - Application settings left navigation, search, expandable sections, and right panel loader.
- `src/Vehicle/VehicleSetup/VehicleConfigView.qml`
  - Vehicle setup left navigation, search, summary, component tree, parameter/firmware special pages, and right panel loader.

### Application Settings model

QGC does not hard-code the final Application Settings page by hand.

- `src/AppSettings/CMakeLists.txt` runs a generator:
  - `src/AppSettings/pages/SettingsPages.json`
  - `src/AppSettings/pages/*.SettingsUI.json`
  - `src/Settings/*.SettingsGroup.json`
  - generated QML pages
- `src/AppSettings/pages/SettingsPages.json` defines the left navigation:
  - General
  - Fly View
  - 3D View
  - Plan View
  - ADSB Server
  - Comm Links
  - App Logging
  - App Log Viewer
  - Maps
  - NTRIP/RTK
  - PX4 Log Transfer
  - Remote ID
  - Telemetry
  - Video
  - Help
  - debug-only pages
- Each `*.SettingsUI.json` defines visible sections and controls.
  - Example: `CommLinks.SettingsUI.json` has AutoConnect, NMEA GPS, Link Management.
  - Example: `General.SettingsUI.json` has General, Vehicle Preferences, Units.
- Each setting is backed by a Fact-style metadata definition under `src/Settings/*.SettingsGroup.json`.

Important lesson for us: the UI should be schema-driven. The current hard-coded HTML fields
are too shallow to grow into a QGC-like settings surface.

### Vehicle Settings model

QGC's Vehicle Settings are dynamic and depend on the connected vehicle, firmware, parameters,
and component metadata.

- `src/Vehicle/VehicleSetup/VehicleConfigView.qml`
  - Shows a Summary page first.
  - Only shows full setup pages after parameters are ready.
  - Uses `activeVehicle.autopilotPlugin.vehicleComponents` for the left navigation.
  - Shows per-component completion state and expandable sections.
  - Adds special pages for Parameters and Firmware.
- `src/AutoPilotPlugins/AutoPilotPlugin.h`
  - Defines `vehicleComponents` and `setupComplete`.
- `src/AutoPilotPlugins/VehicleComponent.h`
  - Each component provides:
    - `name`
    - `description`
    - `requiresSetup`
    - `setupComplete`
    - `iconResource`
    - `setupSource`
    - `summaryQmlSource`
    - optional `sections`
    - optional `vehicleConfigJson`
    - setup gating while armed/flying
- `src/AutoPilotPlugins/PX4/PX4AutoPilotPlugin.cc`
  - Builds PX4 component list after parameters are ready:
    - Airframe
    - Sensors
    - Radio
    - Flight Modes
    - Power
    - Actuators or legacy Motor
    - Safety
    - PID Tuning
    - Flight Behavior when `SYS_VEHICLE_RESP` exists
    - ESP8266 when bridge params exist
    - Joystick
    - Syslink when related params exist
  - Enforces prerequisites, for example Airframe before many other pages.

Important lesson for us: Vehicle Settings should be a runtime vehicle model, not a static
settings dialog.

### Parameter system

Most QGC Vehicle Settings depend on PX4 parameters.

- `src/FactSystem/ParameterManager.cc`
  - Pulls parameters with FTP `@PARAM/param.pck?withdefaults=1` when available, otherwise uses `_HASH_CHECK` and `PARAM_REQUEST_LIST`.
  - Parses `PARAM_VALUE`.
  - Writes parameters with `PARAM_SET` and waits for matching `PARAM_VALUE` or `PARAM_ERROR`.
  - Exposes `parametersReady` and `missingParameters`.
- `src/FirmwarePlugin/PX4/PX4ParameterMetaData.cc`
  - Parses PX4 parameter metadata into Fact metadata.

Important lesson for us: without a parameter manager, QGC-like pages cannot be meaningful.
We can show firmware and telemetry, but Airframe/Safety/Power/PID/Flight Modes need parameter
download, metadata, validation, and write acknowledgements.

### Firmware and initial connection

- `src/Vehicle/InitialConnectStateMachine.cc`
  - Connection initialization sequence:
    - request `AUTOPILOT_VERSION`
    - request standard modes
    - request component information
    - request parameters
    - request mission
    - request geofence
    - request rally points
  - `AUTOPILOT_VERSION` sets UID, vendor/product, firmware version, git hash, and capabilities.

Important lesson for us: our previous firmware-info addition is only the first state in QGC's
connection state machine. We still need standard modes, component information, parameters,
mission/geofence/rally loaders, and visible progress/errors.

### Telemetry and facts

QGC maps MAVLink messages into FactGroups.

- Examples under `src/Vehicle/FactGroups`:
  - Vehicle attitude/speeds/altitudes
  - GPS
  - battery
  - local position
  - vibration
  - estimator status
  - distance sensors
  - ESC status
  - radio status
- The UI consumes these facts rather than decoding raw messages directly in each page.

Important lesson for us: our frontend should not directly invent map/HUD values from raw partial telemetry.
The backend should expose source, age, validity, units, and confidence for every displayed value.

## Target information architecture for our system

We keep one bottom-left settings entry in the main app, but inside it we should model QGC's
two surfaces clearly.

### Application Settings

1. General
   - language/theme/UI density
   - units
   - app data paths
   - reset/clear local state
2. Fly View
   - HUD items
   - guided command behavior
   - map following behavior
   - virtual joystick/manual controls visibility
3. Plan / Mission
   - default altitude/speed
   - mission validation rules
   - waypoint display and upload/download behavior
4. Comm Links
   - auto-connect toggles
   - detected links
   - saved link configurations
   - actual active endpoint
   - MAVLink heartbeat/system/component details
5. Maps
   - map source
   - tile cache
   - offline cache status
   - coordinate display policy for real vehicle vs simulation
6. Telemetry / MAVLink
   - GCS system ID
   - heartbeat emission
   - message stream rates
   - MAVLink forwarding
   - raw link status
   - telemetry log recording
7. Video / Camera
   - AirSim camera source
   - future real camera/MAVLink camera support
   - capture/preview settings
8. AI Agent
   - command approval policy
   - real-vehicle safety gates
   - model/provider selection
   - tool visibility and read/write permissions
9. Logs / Diagnostics
   - app logs
   - MAVLink tlog/CSV logs
   - connection event history
10. Help
   - version, environment, dependency checks

### Vehicle Settings

These pages should appear only when there is an active vehicle model. When disconnected,
show a QGC-style state panel: "connect a vehicle, then parameters and setup pages will appear."

1. Summary
   - vehicle type
   - firmware version/git hash/UID/vendor/product
   - setup completion cards
   - health/prearm status
   - link and parameter download state
2. Airframe
   - read `SYS_AUTOSTART`
   - later load PX4 airframe metadata and default parameter application
3. Sensors
   - read `CAL_GYRO0_ID`, `CAL_ACC0_ID`, `CAL_MAG0_ID`, `SYS_HAS_MAG`
   - show compass/gyro/accel/level horizon status
   - calibration commands later, with strict safety workflow
4. Radio / RC
   - read RC channel telemetry and RC input parameters
   - calibration later
5. Flight Modes
   - read mode switch assignment parameters
   - expose standard/available modes
6. Power
   - read `BAT1_*`, `BAT2_*` repeated batteries
   - battery summary and parameter controls later
7. Actuators / Motors
   - read component information actuator metadata when available
   - show read-only mixer/output status first
   - motor testing/calibration later and only with propeller-removal gate
8. Safety / Failsafe
   - read low battery, RC loss, datalink loss, geofence, RTL, land-mode params
   - parameter editing later after ack/revert support
9. PID Tuning
   - first show live rate/attitude setpoint vs response charts
   - parameter editing and autotune later
10. Flight Behavior
   - show only if PX4 exposes `SYS_VEHICLE_RESP`
11. Parameters
   - searchable full parameter table
   - metadata descriptions, units, min/max, enums
   - change queue, write ack, revert
12. Firmware
   - firmware info first
   - flashing later, likely defer to QGC for a long time
13. MAVLink Inspector / Console
   - raw messages, rates, last seen, component IDs
   - command console only behind advanced mode

## Backend blueprint

### Layer 1: Vehicle runtime model

Create a backend model similar to QGC `Vehicle`:

- `VehicleIdentity`
  - system/component id
  - autopilot type
  - MAV type
  - firmware version/custom version/git hash
  - vendor/product/UID
  - board name from USB detection
- `VehicleLinkState`
  - configured preset
  - actual endpoint
  - link type: serial/udp/tcp
  - heartbeat age
  - mavlink protocol version
  - real/simulation classification
- `VehicleTelemetryFacts`
  - attitude
  - GPS/global position
  - local position
  - battery
  - flight mode
  - armed/flying/landed
  - health/prearm
  - source validity and age for every value
- `VehicleSetupState`
  - parameters ready/missing/progress
  - component completion states
  - unsupported/missing data reasons

### Layer 2: Initial connection state machine

Implement a Python state machine based on QGC's sequence:

1. heartbeat and endpoint lock
2. request `AUTOPILOT_VERSION`
3. request standard modes when supported
4. request component information
5. download/read parameters
6. load mission/geofence/rally later
7. mark vehicle model ready

The UI should show progress, retry/failure reasons, and whether pages are disabled because
parameters are missing.

### Layer 3: Parameter manager

Implement a PX4 parameter manager:

- full download with `PARAM_REQUEST_LIST`
- optional PX4 FTP/hashes later
- parse `PARAM_VALUE`
- expose search, categories, metadata, current values
- write with `PARAM_SET`
- wait for matching ack value
- timeout/retry/revert
- persist cache by vehicle UID + firmware hash

Until this exists, Airframe/Safety/Power/PID cannot be QGC-like beyond read-only snapshots.

### Layer 4: Settings schema renderer

Add our own settings schema format inspired by QGC:

- `src/ui/settings/pages/*.json`
- `src/ui/settings/groups/*.json`
- control types:
  - label
  - checkbox/toggle
  - combobox
  - number/text input
  - slider
  - action button
  - dialog button
  - status fact
  - live chart
- expressions:
  - `showWhen`
  - `enableWhen`
  - `requiresVehicle`
  - `requiresParameters`
  - `requiresRealVehicle`
  - `dangerLevel`

The frontend should render pages from this schema instead of hard-coded modal sections.

### Layer 5: Safety and authority gates

Real vehicle operations need stricter gating than simulation:

- read-only by default
- parameter writes require explicit confirmation
- actuator/motor tests require propeller-removal confirmation
- calibration commands require page-specific workflows
- firmware flashing remains disabled until a full bootloader implementation exists
- Agent write tools must route through the same safety gates as UI buttons

## Phased implementation plan

### Phase 0: Cleanup current panel

Purpose: remove misleading/low-value text and make current behavior honest.

- Rename `PX4 Auto` UI to show "USB auto, UDP fallback".
- Always show actual active endpoint separate from saved preset.
- Replace static "飞控信息" text blocks with connection-state cards.
- Show disconnected state clearly.
- Hide vehicle setup pages when no connected vehicle.

### Phase 1: Settings shell

Purpose: QGC-like navigation and schema foundation.

- Add top-level tabs or grouped left nav:
  - Application Settings
  - Vehicle Settings
- Add search box.
- Add section expansion.
- Add settings page registry JSON.
- Keep current Comm Links and Camera as first schema pages.

### Phase 2: Vehicle identity and telemetry facts

Purpose: make Summary credible.

- Finish `VehicleIdentity` API.
- Add `AUTOPILOT_VERSION` refresh/status.
- Add `SYS_STATUS`, `EXTENDED_SYS_STATE`, `GLOBAL_POSITION_INT`, `GPS_RAW_INT`, `BATTERY_STATUS`, `ATTITUDE`, `VFR_HUD`, `LOCAL_POSITION_NED` fact model.
- Every fact returns value, unit, age, valid flag, and source.
- Map/HUD use only valid global GPS for real-vehicle map position.

### Phase 3: Initial connect state machine

Purpose: match QGC connection confidence.

- Add visible connection progress:
  - heartbeat found
  - firmware info read
  - component info read
  - parameter download progress
  - ready/missing/failed
- Add retry and error reasons.
- Add backend event log for connection attempts.

### Phase 4: Read-only Vehicle Summary and setup status

Purpose: match QGC's overview page without writing anything.

- Summary cards:
  - Airframe from `SYS_AUTOSTART`
  - Sensors from `CAL_*`
  - Radio/RC from RC telemetry/params
  - Flight Modes from mode params/standard modes
  - Power from `BAT*`
  - Safety from failsafe params
  - Actuators from component metadata if available
  - PID availability from params
- Completion dots should use real parameter checks, not placeholder text.

### Phase 5: Parameter table

Purpose: unlock most QGC-like pages.

- Full parameter search and categories.
- Metadata from PX4 JSON when available.
- Read-only first.
- Then controlled writes with ack/revert.

### Phase 6: QGC-like component pages, safe subset

Purpose: useful configuration without dangerous workflows.

- Power: battery params
- Safety: low battery, RC loss, datalink loss, geofence, RTL, landing
- Flight Modes: mode assignment read/write where safe
- Airframe: read-only first, default application later
- PID: live chart + read-only params, write later

### Phase 7: Calibration and actuator workflows

Purpose: only after state machine and safety gates are mature.

- Sensor calibration:
  - compass
  - gyro
  - accelerometer
  - level horizon
- RC calibration.
- ESC/motor calibration and actuator tests.
- Must follow QGC-style command ack/text-message progress handling.

### Phase 8: Advanced QGC parity

Purpose: optional long-term parity.

- firmware flashing/bootloader
- onboard log download
- geofence/rally/mission sync
- MAVLink inspector
- MAVLink console
- component information metadata renderer
- Remote ID
- NTRIP/RTK

## What the current text blocks are useful for

The current "飞控信息" panel is only useful as a first diagnostic snapshot:

- it confirms actual endpoint and firmware identity
- it proves `AUTOPILOT_VERSION` can be read
- it confirms real-vs-simulation classification
- it exposes why map position is unavailable without GPS

It is not yet a QGC-like Vehicle Setup implementation. It should be treated as Phase 0/2
diagnostics, then replaced by the schema-driven Summary + Component pages above.

## Immediate next implementation recommendation

Do not start with sensor calibration or full PID tuning.

The next concrete task should be:

1. Build settings page registry/schema.
2. Build `VehicleRuntimeModel` and parameter manager read-only download.
3. Replace current settings modal with schema-rendered pages:
   - Application: General, Comm Links, Maps, Telemetry, Camera, Agent, Logs
   - Vehicle: Summary, Firmware, Parameters, MAVLink Inspector
4. Add read-only PX4 setup status cards from actual params.

Only after parameter reads are stable should we implement parameter writes and calibration.
