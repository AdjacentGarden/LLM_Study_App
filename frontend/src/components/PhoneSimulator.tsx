import { useEffect, useState } from "react";
import { previewDevice, previewScale } from "./devicePreview";

export function PhoneSimulator() {
  const device = previewDevice(new URLSearchParams(window.location.search).get("device"));
  const [scale, setScale] = useState(() =>
    previewScale(window.innerWidth, window.innerHeight, device),
  );
  useEffect(() => {
    const resize = () =>
      setScale(previewScale(window.innerWidth, window.innerHeight, device));
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, [device]);
  const width = device.width + device.bezel * 2;
  const height = device.height + device.bezel * 2;
  const source = new URL(window.location.href);
  source.searchParams.set("embedded", "1");
  source.searchParams.set("device", device.id);
  return <main className="device-preview">
    <header className="device-preview-label"><strong>{device.label}</strong><span>{device.width} × {device.height} · 等比缩放 {Math.round(scale * 100)}%</span></header>
    <div className="device-preview-slot" style={{ width: width * scale, height: height * scale }}>
      <div className="device-preview-frame" style={{ width, height, transform: `scale(${scale})` }}>
        <iframe title={`${device.label} App 模拟屏幕`} src={source.href} width={device.width} height={device.height} style={{ width: device.width, height: device.height }}/>
      </div>
    </div>
  </main>;
}
