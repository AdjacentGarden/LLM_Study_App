import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  CollapsibleRegion,
  MotionErrorShake,
  MotionIconSwap,
  SkeletonReveal,
  SlidingFilterGroup,
  StateSwapText,
  useOneShotFeedback,
  type LoadState
} from "../src/motion";
import "../src/styles/tokens.css";
import "../src/styles/base.css";
import "../src/styles/responsive.css";
import "../src/styles/motion.css";

type FilterValue = "all" | "chapter" | "review";

type StateMotionHarness = {
  setText: (value: string) => void;
  setExpanded: (expanded: boolean) => void;
  setLoadState: (state: LoadState, readyKind: "content" | "empty") => void;
  setFilter: (value: FilterValue) => void;
  triggerError: () => void;
  unmount: () => void;
};

declare global {
  interface Window {
    __stateMotionHarness?: StateMotionHarness;
    __stateMotionEvents?: {
      animationEnd: number;
      transitionEnd: number;
    };
    __motionObserverAudit?: {
      created: number;
      disconnected: number;
    };
  }
}

const rootElement = document.getElementById("state-motion-root");
if (!rootElement) throw new Error("State motion harness root is missing.");

const root = createRoot(rootElement);
const filterOptions = [
  { value: "all" as const, label: "全部" },
  { value: "chapter" as const, label: "章节" },
  { value: "review" as const, label: "待复习" }
];

function StateMotionHarnessView() {
  const [text, setText] = useState("等待中");
  const [expanded, setExpanded] = useState(false);
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [readyKind, setReadyKind] = useState<"content" | "empty">("content");
  const [filter, setFilter] = useState<FilterValue>("all");
  const toggleRef = useRef<HTMLButtonElement | null>(null);
  const feedback = useOneShotFeedback();

  useEffect(() => {
    const rootNode = rootElement;
    if (!rootNode) return;
    const events = { animationEnd: 0, transitionEnd: 0 };
    window.__stateMotionEvents = events;
    const handleAnimationEnd = () => { events.animationEnd += 1; };
    const handleTransitionEnd = () => { events.transitionEnd += 1; };
    rootNode.addEventListener("animationend", handleAnimationEnd);
    rootNode.addEventListener("transitionend", handleTransitionEnd);
    window.__stateMotionHarness = {
      setText,
      setExpanded,
      setLoadState: (nextState, nextKind) => {
        setLoadState(nextState);
        setReadyKind(nextKind);
      },
      setFilter,
      triggerError: feedback.trigger,
      unmount: () => root.unmount()
    };
    return () => {
      rootNode.removeEventListener("animationend", handleAnimationEnd);
      rootNode.removeEventListener("transitionend", handleTransitionEnd);
      delete window.__stateMotionHarness;
    };
  }, [feedback.trigger]);

  return (
    <main id="state-motion-harness">
      <section aria-label="text swap">
        <StateSwapText value={text} motionKey={text} reserveValues={["等待中", "处理中", "完成"]} />
      </section>

      <section aria-label="accordion">
        <button
          ref={toggleRef}
          id="accordion-toggle"
          type="button"
          aria-expanded={expanded}
          aria-controls="accordion-region"
          onClick={() => setExpanded((current) => !current)}
        >
          切换折叠区
        </button>
        <CollapsibleRegion
          expanded={expanded}
          id="accordion-region"
          labelledBy="accordion-toggle"
          focusFallbackRef={toggleRef}
        >
          <div>
            <input id="accordion-focus-target" aria-label="折叠区输入" />
          </div>
        </CollapsibleRegion>
      </section>

      <section aria-label="skeleton">
        <SkeletonReveal
          state={loadState}
          readyKind={readyKind}
          skeleton={<div data-testid="skeleton-placeholder">加载骨架</div>}
          error={<div role="alert">加载失败</div>}
        >
          <div data-testid="skeleton-content">真实内容</div>
        </SkeletonReveal>
      </section>

      <section aria-label="filter">
        <SlidingFilterGroup
          value={filter}
          options={filterOptions}
          onChange={setFilter}
          ariaLabel="测试筛选"
        />
      </section>

      <section aria-label="icon swap">
        <MotionIconSwap
          state={expanded ? "expanded" : "collapsed"}
          firstState="collapsed"
          secondState="expanded"
          firstIcon={<span>›</span>}
          secondIcon={<span>⌄</span>}
        />
      </section>

      <MotionErrorShake
        state={feedback.state}
        sequence={feedback.sequence}
        onSettle={feedback.settle}
      >
        <span data-testid="error-target">错误反馈</span>
      </MotionErrorShake>
    </main>
  );
}

root.render(<StateMotionHarnessView />);
