import { useEffect, useState } from "react";
import "../styles/next/device-preview.css";

type Device = "iphone-16" | "iphone-17" | "ipad";
const devices: Record<Device, { label: string; width: number; height: number }> = {
  "iphone-16": { label: "iPhone 16", width: 393, height: 852 },
  "iphone-17": { label: "iPhone 17", width: 402, height: 874 },
  ipad: { label: "iPad", width: 834, height: 1112 },
};

export function DevicePreview({ device }: { device: Device }) {
  const { label, width, height } = devices[device];
  const [windowSize, setWindowSize] = useState(() => ({ width: window.innerWidth, height: window.innerHeight }));
  useEffect(() => {
    const resize = () => setWindowSize({ width: window.innerWidth, height: window.innerHeight });
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, []);

  const frameWidth = width + 14;
  const frameHeight = height + 14;
  const scale = Math.min(1, (windowSize.width - 24) / frameWidth, (windowSize.height - 24) / frameHeight);
  const source = new URL(window.location.href);
  source.searchParams.delete("device");
  source.searchParams.set("embedded", "1");

  return <main className="next-device-preview" aria-label={`${label} 预览`}>
    <div className="next-device-preview-slot" style={{ width: frameWidth * scale, height: frameHeight * scale }}>
      <div className="next-device-preview-frame" style={{ width: frameWidth, height: frameHeight, transform: `scale(${scale})` }}>
        <iframe title={`${label} 应用预览`} src={source.href} width={width} height={height} />
      </div>
    </div>
  </main>;
}
