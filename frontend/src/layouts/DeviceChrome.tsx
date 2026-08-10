import type { ReactNode } from "react";
import type { DeviceLayout } from "../preview/devicePreview";

export function DeviceChrome({
  children,
  layout
}: Readonly<{
  children: ReactNode;
  layout: DeviceLayout;
}>) {
  return (
    <div className={`${layout}-chrome`} data-device-chrome={layout}>
      {children}
    </div>
  );
}
