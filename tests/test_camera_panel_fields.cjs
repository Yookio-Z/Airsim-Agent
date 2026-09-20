// 摄像头面板字段可见性：每种图像源只显示真正生效的字段，并把面板值
// 读成设置对象。跑了 ui-camera.js 的真实实现（vm 里只补最小依赖）。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

function field() {
  const label = { textContent: '' };
  return {
    hidden: false,
    textContent: '',
    value: '',
    placeholder: '',
    label,
    querySelector: (selector) => (selector === 'label' ? label : null),
  };
}

function cameraPanelHarness(source = 'airsim') {
  const els = {
    cameraSource: { value: source },
    cameraRtspUrlRow: field(),
    cameraRtspUrl: { value: '' },
    cameraRtspTransportRow: field(),
    cameraRtspTransport: { value: 'tcp' },
    cameraNameRow: field(),
    cameraName: { value: '0' },
    cameraVehicleRow: field(),
    cameraVehicle: { value: '' },
    cameraImageTypeRow: field(),
    cameraImageType: { value: 'scene' },
    cameraTimeout: { value: '30' },
    cameraAutoSave: { checked: false },
    cameraSourceHint: field(),
  };
  const context = vm.createContext({
    els,
    cameraSettings: { source, transport: 'tcp', url: '', camera_name: '0', vehicle_name: '', image_type: 'scene', timeout_sec: 30, auto_save: false },
    DEFAULT_CAMERA_SETTINGS: { transport: 'tcp', source: 'airsim', camera_name: '0', image_type: 'scene', timeout_sec: 30, url: '' },
  });
  vm.runInContext(
    fs.readFileSync(path.join(__dirname, '../src/ui/static/ui-camera.js'), 'utf8'),
    context,
  );
  return { context, els };
}

test('airsim 源只显示 AirSim 自己的字段', () => {
  const { context, els } = cameraPanelHarness('airsim');
  context.updateCameraSourceFields();

  assert.equal(els.cameraRtspUrlRow.hidden, true);
  assert.equal(els.cameraRtspTransportRow.hidden, true);
  assert.equal(els.cameraNameRow.hidden, false);
  assert.equal(els.cameraVehicleRow.hidden, false);
  assert.equal(els.cameraImageTypeRow.hidden, false);
  assert.equal(els.cameraSourceHint.hidden, true);
});

test('rtsp 源显示 URL 与传输协议，隐藏 AirSim 专属字段', () => {
  const { context, els } = cameraPanelHarness('rtsp');
  context.updateCameraSourceFields();

  assert.equal(els.cameraRtspUrlRow.hidden, false);
  assert.equal(els.cameraRtspTransportRow.hidden, false);
  assert.equal(els.cameraNameRow.hidden, true);
  assert.equal(els.cameraVehicleRow.hidden, true);
  assert.equal(els.cameraImageTypeRow.hidden, true);
  // 提示要讲清"连不上时换协议"，否则用户不知道该动哪个字段
  assert.equal(els.cameraSourceHint.hidden, false);
  assert.match(els.cameraSourceHint.textContent, /TCP/);
  assert.match(els.cameraSourceHint.textContent, /UDP/);
});

test('local 源只留下索引', () => {
  const { context, els } = cameraPanelHarness('local');
  context.updateCameraSourceFields();

  assert.equal(els.cameraNameRow.hidden, false);
  assert.equal(els.cameraNameRow.label.textContent, '摄像头索引');
  assert.equal(els.cameraRtspUrlRow.hidden, true);
  assert.equal(els.cameraRtspTransportRow.hidden, true);
  assert.equal(els.cameraVehicleRow.hidden, true);
  assert.equal(els.cameraImageTypeRow.hidden, true);
});

test('面板值读成设置对象，非法传输协议回落到默认', () => {
  const { context, els } = cameraPanelHarness('rtsp');
  els.cameraSource.value = 'rtsp';
  els.cameraRtspUrl.value = 'rtsp://192.168.144.11:8554/main';
  els.cameraRtspTransport.value = 'udp';
  const settings = context.readCameraSettingsForm();
  assert.equal(settings.source, 'rtsp');
  assert.equal(settings.url, 'rtsp://192.168.144.11:8554/main');
  assert.equal(settings.transport, 'udp');

  assert.equal(context.normalizeCameraSettings({ transport: 'rtsp' }).transport, 'tcp');
});
