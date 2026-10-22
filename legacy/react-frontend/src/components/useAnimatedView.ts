import { useCallback, useState } from "react";

/** Commit navigation immediately. CSS animates the incoming panel independently,
 * so a resize, reduced-motion preference or rapid tap cannot cancel navigation. */
export function useAnimatedView<T extends string>(initial:T) {
  const [view,setView]=useState<T>(initial);
  const navigate=useCallback((next:T)=>setView(next),[]);
  return [view,navigate] as const;
}
