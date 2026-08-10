import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { DevicePreviewToolbar } from "./DevicePreviewToolbar";
import {
  buildPreviewSearch,
  calculateFitScale,
  DEFAULT_PREVIEW_SETTINGS,
  EMBEDDED_PREVIEW_SRC,
  getDeviceLayout,
  getLogicalViewport,
  getOutputGeometry,
  parsePreviewSettings,
  type DeviceId,
  type Orientation,
  type PreviewSettings,
  type QualityId
} from "./devicePreview";

type AvailableSize = Readonly<{ height: number; width: number }>;

function readInitialSettings(): PreviewSettings {
  if (typeof window === "undefined") return DEFAULT_PREVIEW_SETTINGS;
  return parsePreviewSettings(window.location.search);
}

function readAvailableSize(element: HTMLElement): AvailableSize {
  const styles = getComputedStyle(element);
  const paddingX = Number.parseFloat(styles.paddingLeft) + Number.parseFloat(styles.paddingRight);
  const paddingY = Number.parseFloat(styles.paddingTop) + Number.parseFloat(styles.paddingBottom);
  return {
    height: Math.max(0, element.clientHeight - paddingY),
    width: Math.max(0, element.clientWidth - paddingX)
  };
}

export function DevicePreviewStudio() {
  const [settings, setSettings] = useState<PreviewSettings>(readInitialSettings);
  const [availableSize, setAvailableSize] = useState<AvailableSize>({ height: 0, width: 0 });
  const [statusAnnouncement, setStatusAnnouncement] = useState("");
  const canvasAreaRef = useRef<HTMLDivElement | null>(null);
  const iframeRef = useRef<HTMLIFrameElement | null>(null);

  const logicalViewport = useMemo(
    () => getLogicalViewport(settings.device, settings.orientation),
    [settings.device, settings.orientation]
  );
  const fitScale = availableSize.width > 0 && availableSize.height > 0
    ? calculateFitScale(
      availableSize.width,
      availableSize.height,
      logicalViewport.width,
      logicalViewport.height
    )
    : 1;
  const geometry = useMemo(
    () => getOutputGeometry(settings.device, settings.orientation, settings.quality, fitScale),
    [fitScale, settings.device, settings.orientation, settings.quality]
  );
  const layout = getDeviceLayout(settings.device);
  const isIphonePortrait = settings.device === "iphone-17-pro" && settings.orientation === "portrait";

  const updateAvailableSize = useCallback(() => {
    const element = canvasAreaRef.current;
    if (!element) return;
    const next = readAvailableSize(element);
    setAvailableSize((current) => (
      current.width === next.width && current.height === next.height ? current : next
    ));
  }, []);

  useLayoutEffect(() => {
    updateAvailableSize();
    const element = canvasAreaRef.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(updateAvailableSize);
    observer.observe(element);
    return () => observer.disconnect();
  }, [updateAvailableSize]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const nextSearch = buildPreviewSearch(settings, window.location.search);
    if (nextSearch === window.location.search) return;
    window.history.replaceState(window.history.state, "", `${window.location.pathname}${nextSearch}${window.location.hash}`);
  }, [settings]);

  const restoreChromeFromEscape = useCallback((event: KeyboardEvent) => {
    if (event.key !== "Escape" || settings.chrome) return;
    event.preventDefault();
    setSettings((current) => (current.chrome ? current : { ...current, chrome: true }));
  }, [settings.chrome]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.addEventListener("keydown", restoreChromeFromEscape);
    return () => window.removeEventListener("keydown", restoreChromeFromEscape);
  }, [restoreChromeFromEscape]);

  useEffect(() => {
    const iframe = iframeRef.current;
    if (!iframe) return;
    let boundWindow: Window | null = null;

    const unbindContentWindow = () => {
      boundWindow?.removeEventListener("keydown", restoreChromeFromEscape);
      boundWindow = null;
    };
    const bindContentWindow = () => {
      unbindContentWindow();
      try {
        const contentWindow = iframe.contentWindow;
        if (!contentWindow) return;
        contentWindow.addEventListener("keydown", restoreChromeFromEscape);
        boundWindow = contentWindow;
      } catch {
        // The embedded route is same-origin in production. Ignore access while
        // a browser is between documents so the next load can bind normally.
      }
    };

    iframe.addEventListener("load", bindContentWindow);
    bindContentWindow();
    return () => {
      iframe.removeEventListener("load", bindContentWindow);
      unbindContentWindow();
    };
  }, [restoreChromeFromEscape]);

  const announcementText = useMemo(() => {
    const layoutLabel = layout === "pad" ? "Pad" : "Phone";
    const qualityLabel = settings.quality === "fit" ? `Fit ${Math.round(geometry.contentScale * 100)}%` : settings.quality;
    return `${logicalViewport.width} × ${logicalViewport.height} CSS px，输出 ${geometry.canvasWidth} × ${geometry.canvasHeight}，${qualityLabel}，${layoutLabel}`;
  }, [
    geometry.canvasHeight,
    geometry.canvasWidth,
    geometry.contentScale,
    layout,
    logicalViewport.height,
    logicalViewport.width,
    settings.device,
    settings.orientation,
    settings.quality
  ]);

  useEffect(() => {
    const delay = settings.quality === "fit" ? 250 : 0;
    const timer = window.setTimeout(() => {
      setStatusAnnouncement((current) => current === announcementText ? current : announcementText);
    }, delay);
    return () => window.clearTimeout(timer);
  }, [announcementText, settings.quality]);

  const updateSettings = useCallback((partial: Partial<PreviewSettings>) => {
    setSettings((current) => ({ ...current, ...partial }));
  }, []);
  const handleDeviceChange = useCallback((device: DeviceId) => updateSettings({ device }), [updateSettings]);
  const handleOrientationChange = useCallback((orientation: Orientation) => updateSettings({ orientation }), [updateSettings]);
  const handleQualityChange = useCallback((quality: QualityId) => updateSettings({ quality }), [updateSettings]);

  const frameStyle = {
    height: `${geometry.logicalHeight}px`,
    left: `${geometry.contentOffsetX}px`,
    top: `${geometry.contentOffsetY}px`,
    transform: `scale(${geometry.contentScale})`,
    width: `${geometry.logicalWidth}px`
  };

  return (
    <div
      className="device-preview-studio"
      data-preview-chrome={settings.chrome ? "1" : "0"}
      data-preview-quality={settings.quality}
      data-preview-layout={layout}
    >
      {settings.chrome ? (
        <DevicePreviewToolbar
          settings={settings}
          onDeviceChange={handleDeviceChange}
          onOrientationChange={handleOrientationChange}
          onQualityChange={handleQualityChange}
        />
      ) : null}
      {settings.chrome ? (
        <div className="device-preview-output-summary" data-testid="device-preview-summary">
          <span>逻辑视口：{logicalViewport.width} × {logicalViewport.height} CSS px</span>
          <span>输出画布：{geometry.canvasWidth} × {geometry.canvasHeight}</span>
          <span>缩放：{Math.round(geometry.contentScale * 100)}%</span>
          <span>布局族：{layout === "pad" ? "Pad" : "Phone"}</span>
        </div>
      ) : null}
      <div ref={canvasAreaRef} className="device-preview-canvas-area" data-testid="device-preview-canvas-area">
        <div
          className={`device-preview-canvas${isIphonePortrait ? " device-preview-canvas--iphone-17-pro" : ""}`}
          data-testid="device-preview-canvas"
          data-canvas-width={geometry.canvasWidth}
          data-canvas-height={geometry.canvasHeight}
          style={{ height: `${geometry.canvasHeight}px`, width: `${geometry.canvasWidth}px` }}
        >
          <div
            className={`device-preview-frame${isIphonePortrait ? " device-preview-frame--iphone-17-pro" : ""}`}
            data-testid="device-preview-frame"
            data-device={settings.device}
            data-orientation={settings.orientation}
            style={frameStyle}
          >
            {isIphonePortrait ? (
              <div
                className="device-preview-bezel"
                data-testid="device-preview-bezel"
                data-device-bezel="iphone-17-pro"
                aria-hidden="true"
              />
            ) : null}
            {isIphonePortrait ? (
              <div
                className="device-preview-hardware-controls"
                data-testid="device-preview-hardware-controls"
                aria-hidden="true"
              >
                <span className="device-preview-hardware-control device-preview-hardware-control--action" data-hardware-control="action" />
                <span className="device-preview-hardware-control device-preview-hardware-control--volume-up" data-hardware-control="volume-up" />
                <span className="device-preview-hardware-control device-preview-hardware-control--volume-down" data-hardware-control="volume-down" />
                <span className="device-preview-hardware-control device-preview-hardware-control--side" data-hardware-control="side" />
              </div>
            ) : null}
            <iframe
              ref={iframeRef}
              className="device-preview-iframe"
              title="BookCourse AI 设备预览内层应用"
              src={EMBEDDED_PREVIEW_SRC}
              width={geometry.logicalWidth}
              height={geometry.logicalHeight}
            />
            {isIphonePortrait ? (
              <div
                className="device-preview-dynamic-island"
                data-testid="device-preview-dynamic-island"
                data-dynamic-island="true"
                aria-hidden="true"
              />
            ) : null}
          </div>
        </div>
      </div>
      <p className="device-preview-status-announcer" data-testid="device-preview-status-announcer" aria-live="polite">{statusAnnouncement}</p>
    </div>
  );
}
