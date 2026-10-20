// 摄像头设置表单的读取契约（2026-10-20 精简后）。
//
// 设置面板现在只保留 RTSP 视频流地址；相机/机型/图像类型/超时由摄像头面板
// 自己的控件维护并写进 cameraSettings。表单读取必须保留这些值，不能被已经
// 删除的输入框清空——否则在设置面板点一次保存就会把面板选的相机冲掉。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

function harness() {
  const context = vm.createContext({
    els: {
      cameraRtspUrl: { value: 'rtsp://192.168.144.11:8554/main' },
    },
    cameraSettings: {
      source: 'rtsp',
      transport: 'udp',
      url: '',
      camera_name: 'CameraImage',
      vehicle_name: 'PX4',
      image_type: 'depth',
      timeout_sec: 12,
    },
    DEFAULT_CAMERA_SETTINGS: {
      transport: 'tcp',
      source: 'airsim',
      camera_name: '0',
      vehicle_name: '',
      image_type: 'scene',
      timeout_sec: 30,
      url: '',
    },
  });
  vm.runInContext(
    fs.readFileSync(path.join(__dirname, '../src/ui/static/ui-camera.js'), 'utf8'),
    context,
  );
  return context;
}

test('设置面板只覆盖 RTSP 地址，其余字段沿用摄像头面板的值', () => {
  const settings = harness().readCameraSettingsForm();

  assert.equal(settings.url, 'rtsp://192.168.144.11:8554/main');
  assert.equal(settings.source, 'rtsp');
  assert.equal(settings.transport, 'udp');
  assert.equal(settings.camera_name, 'CameraImage');
  assert.equal(settings.vehicle_name, 'PX4');
  assert.equal(settings.image_type, 'depth');
  assert.equal(settings.timeout_sec, 12);
});

test('非法传输协议回落到默认值', () => {
  const context = harness();
  assert.equal(context.normalizeCameraSettings({ transport: 'rtsp' }).transport, 'tcp');
});
