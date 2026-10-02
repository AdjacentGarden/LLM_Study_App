import { useLayoutEffect, useRef, type ReactNode, type RefObject } from "react";

export type CollapsibleRegionProps = Readonly<{
  expanded: boolean;
  id: string;
  labelledBy: string;
  focusFallbackRef?: RefObject<HTMLElement | null>;
  className?: string;
  children: ReactNode;
}>;

export function CollapsibleRegion({
  expanded,
  id,
  labelledBy,
  focusFallbackRef,
  className = "",
  children
}: CollapsibleRegionProps) {
  const regionRef = useRef<HTMLDivElement | null>(null);

  useLayoutEffect(() => {
    const region = regionRef.current;
    if (!region) return;
    if (!expanded && region.contains(document.activeElement)) {
      focusFallbackRef?.current?.focus({ preventScroll: true });
    }
    region.toggleAttribute("inert", !expanded);
  }, [expanded, focusFallbackRef]);

  return (
    <div
      ref={regionRef}
      id={id}
      className={`motion-collapsible ${className}`.trim()}
      data-motion-collapsible={expanded ? "expanded" : "collapsed"}
      aria-labelledby={labelledBy}
      aria-hidden={expanded ? undefined : true}
    >
      <div className="motion-collapsible-inner">{children}</div>
    </div>
  );
}
