import { createContext, useCallback, useContext, useMemo, useRef, type ReactNode } from "react";

export type MotionHistory = {
  hasSeen: (key: string) => boolean;
  markSeen: (key: string) => void;
  consume: (key: string) => boolean;
};

const MotionHistoryContext = createContext<MotionHistory | null>(null);

export function MotionHistoryProvider({ children }: { children: ReactNode }) {
  const seenKeysRef = useRef(new Set<string>());

  const hasSeen = useCallback((key: string) => seenKeysRef.current.has(key), []);
  const markSeen = useCallback((key: string) => {
    seenKeysRef.current.add(key);
  }, []);
  const consume = useCallback((key: string) => {
    if (seenKeysRef.current.has(key)) return false;
    seenKeysRef.current.add(key);
    return true;
  }, []);

  const value = useMemo(() => ({ hasSeen, markSeen, consume }), [consume, hasSeen, markSeen]);

  return <MotionHistoryContext.Provider value={value}>{children}</MotionHistoryContext.Provider>;
}

export function useMotionHistory() {
  const history = useContext(MotionHistoryContext);
  if (!history) {
    throw new Error("useMotionHistory must be used within a MotionHistoryProvider");
  }
  return history;
}
