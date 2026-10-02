import type { ReactNode } from "react";
import { DeviceChrome } from "./DeviceChrome";

export function PhoneChrome({ children }: Readonly<{ children?: ReactNode }>) {
  return <DeviceChrome layout="phone">{children}</DeviceChrome>;
}
