import type { ReactNode } from "react";
import { DeviceChrome } from "./DeviceChrome";

export function PadChrome({ children }: Readonly<{ children?: ReactNode }>) {
  return <DeviceChrome layout="pad">{children}</DeviceChrome>;
}
