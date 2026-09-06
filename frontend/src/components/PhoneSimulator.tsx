import { useEffect, useState } from "react";
import { IPHONE_16, previewScale } from "./devicePreview";

export function PhoneSimulator() {
  const [scale, setScale] = useState(() => previewScale(window.innerWidth, window.innerHeight));
  useEffect(() => {
    const resize = () => setScale(previewScale(window.innerWidth, window.innerHeight));
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, []);
  const width = IPHONE_16.width + IPHONE_16.bezel * 2;
  const height = IPHONE_16.height + IPHONE_16.bezel * 2;
  const source = new URL(window.location.href);
  source.searchParams.set("embedded", "1");
  source.searchParams.set("device", "iphone-16");
  return <main className="device-preview">
    <header className="device-preview-label"><strong>iPhone 16</strong><span>393 × 852 · 等比缩放 {Math.round(scale * 100)}%</span></header>
    <div className="device-preview-slot" style={{ width: width * scale, height: height * scale }}>
      <div className="device-preview-frame" style={{ width, height, transform: `scale(${scale})` }}>
        <iframe title="iPhone 16 App 模拟屏幕" src={source.href} width={IPHONE_16.width} height={IPHONE_16.height}/>
      </div>
    </div>
  </main>;
}
