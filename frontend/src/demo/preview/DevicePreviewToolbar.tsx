import type { DeviceId, Orientation, PreviewSettings, QualityId } from "./devicePreview";
import { DEVICE_PRESETS, QUALITY_LABELS } from "./devicePreview";

type DevicePreviewToolbarProps = Readonly<{
  settings: PreviewSettings;
  onDeviceChange: (device: DeviceId) => void;
  onOrientationChange: (orientation: Orientation) => void;
  onQualityChange: (quality: QualityId) => void;
}>;

function ToggleButton({
  active,
  children,
  onClick,
  testId
}: Readonly<{
  active: boolean;
  children: string;
  onClick: () => void;
  testId: string;
}>) {
  return (
    <button
      className="device-preview-toggle"
      type="button"
      aria-pressed={active}
      data-testid={testId}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

export function DevicePreviewToolbar({
  settings,
  onDeviceChange,
  onOrientationChange,
  onQualityChange
}: DevicePreviewToolbarProps) {
  return (
    <header className="device-preview-toolbar" data-testid="device-preview-toolbar">
      <div className="device-preview-toolbar-heading">
        <div>
          <p className="device-preview-eyebrow">BookCourse AI</p>
          <h1>设备预览工作台</h1>
        </div>
      </div>
      <div className="device-preview-control-groups">
        <fieldset className="device-preview-control-group">
          <legend>设备</legend>
          <div className="device-preview-toggle-row">
            {(Object.values(DEVICE_PRESETS)).map((preset) => (
              <ToggleButton
                key={preset.id}
                active={settings.device === preset.id}
                testId={`device-preview-device-${preset.id}`}
                onClick={() => onDeviceChange(preset.id)}
              >
                {preset.label}
              </ToggleButton>
            ))}
          </div>
        </fieldset>
        <fieldset className="device-preview-control-group">
          <legend>方向</legend>
          <div className="device-preview-toggle-row">
            <ToggleButton
              active={settings.orientation === "portrait"}
              testId="device-preview-orientation-portrait"
              onClick={() => onOrientationChange("portrait")}
            >
              竖屏
            </ToggleButton>
            <ToggleButton
              active={settings.orientation === "landscape"}
              testId="device-preview-orientation-landscape"
              onClick={() => onOrientationChange("landscape")}
            >
              横屏
            </ToggleButton>
          </div>
        </fieldset>
        <fieldset className="device-preview-control-group">
          <legend>输出清晰度</legend>
          <div className="device-preview-toggle-row">
            {(Object.keys(QUALITY_LABELS) as QualityId[]).map((quality) => (
              <ToggleButton
                key={quality}
                active={settings.quality === quality}
                testId={`device-preview-quality-${quality}`}
                onClick={() => onQualityChange(quality)}
              >
                {QUALITY_LABELS[quality]}
              </ToggleButton>
            ))}
          </div>
        </fieldset>
      </div>
    </header>
  );
}
