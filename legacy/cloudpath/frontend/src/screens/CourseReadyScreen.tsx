import { useLayoutEffect, useRef, useState } from "react";
import {
  FileText,
  Upload
} from "lucide-react";
import {
  Button,
  Metric
} from "../components/ui";
import { useAppContext } from "../context/AppContext";
import { useMotionHistory, useReducedMotion, useStageThreeImageMotion } from "../motion";

const courseReadyHeroSource = "/assets/brand/cloud-mascot-success-transparent-v1.png";

function CourseReadySuccessMark({ motionKey }: { motionKey: string }) {
  const { consume } = useMotionHistory();
  const reducedMotion = useReducedMotion();
  const [motionState, setMotionState] = useState<"entering" | "idle">("idle");
  const appliedKeyRef = useRef<string | null>(null);

  useLayoutEffect(() => {
    if (appliedKeyRef.current === motionKey) {
      if (reducedMotion) setMotionState("idle");
      return;
    }
    appliedKeyRef.current = motionKey;
    setMotionState(consume(motionKey) && !reducedMotion ? "entering" : "idle");
  }, [consume, motionKey, reducedMotion]);

  return (
    <span
      className="course-ready-success-mark"
      data-motion-course-ready-key={motionKey}
      data-motion-course-ready-state={motionState}
      aria-hidden="true"
      onAnimationEnd={(event) => {
        if (event.animationName === "motion-course-ready-success-in" || event.animationName === "motion-course-ready-check-path") {
          setMotionState("idle");
        }
      }}
    >
      <svg className="course-ready-success-check" viewBox="0 0 32 32" width="24" height="24" focusable="false">
        <path className="course-ready-check-path" d="M8 16.5 13.5 22 24 10.5" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </span>
  );
}

function CourseReadyHeroImage({ statusTitle }: { statusTitle: string }) {
  const imageMotion = useStageThreeImageMotion(courseReadyHeroSource);
  if (imageMotion.state === "failed") {
    return (
      <span
        className="success-hero-image-fallback"
        data-motion-image-source={courseReadyHeroSource}
        data-motion-image-state="failed"
        role="img"
        aria-label={`${statusTitle}插图不可用`}
      >
        <FileText size={42} aria-hidden="true" />
      </span>
    );
  }

  return (
    <img
      className="success-hero-image"
      src={courseReadyHeroSource}
      alt="云朵举起完成标记和教材"
      ref={imageMotion.imageRef}
      data-motion-image-source={courseReadyHeroSource}
      data-motion-image-state={imageMotion.state}
      onLoad={imageMotion.onLoad}
      onError={imageMotion.onError}
      onAnimationEnd={(event) => {
        if (event.animationName === "motion-stage3-image-in") imageMotion.settleAnimation();
      }}
    />
  );
}

type CourseCompletionScreenProps = {
  assetCount: number;
  chapterCount: number;
  className?: string;
  courseTitle: string;
  lessonCount: number;
  motionKey: string;
  onEnterStudy: () => void;
  onViewPlan: () => void;
  ragChunkCount: number;
  statusTitle: string;
};

export function CourseCompletionScreen({
  assetCount,
  chapterCount,
  className,
  courseTitle,
  lessonCount,
  motionKey,
  onEnterStudy,
  onViewPlan,
  ragChunkCount,
  statusTitle
}: CourseCompletionScreenProps) {
  const moduleValues = [
    ["课程", `${lessonCount}`, `${chapterCount} 个目录项完成编排`],
    ["RAG 片段", `${ragChunkCount}`, "可用于问答检索"],
    ["课程插图", `${assetCount}`, "源文件抽取优先"],
    ["检索链路", "混合", "BM25 + pgvector + reranker"]
  ];

  return (
    <div className={`screen-stack centered-flow parse-complete-screen course-ready-screen${className ? ` ${className}` : ""}`}>
      <div className="course-ready-primary" data-brand-moment="course-ready">
        <CourseReadyHeroImage key={`${motionKey}:image`} statusTitle={statusTitle} />
        <div className="course-ready-success-heading">
          <h1>{statusTitle}</h1>
          <CourseReadySuccessMark key={motionKey} motionKey={motionKey} />
        </div>
        <p role="status" aria-live="polite">已将《{courseTitle}》编排为 {lessonCount} 节 AI 课程。</p>
      </div>
      <aside className="course-ready-support" aria-label={`${statusTitle}结果`}>
        <div className="module-grid">
          {moduleValues.map(([label, value, helper]) => (
            <Metric key={label} label={label} value={value} helper={helper} />
          ))}
        </div>
      </aside>
      <div className="course-ready-actions">
        <Button onClick={onEnterStudy}>进入学习</Button>
        <Button variant="secondary" onClick={onViewPlan}>查看学习计划</Button>
      </div>
    </div>
  );
}

export function CourseReadyScreen() {
  const { generatedLessons, go, lessonBuildJobId, parsedAssets, parsedChapters, parsedChunks, setActiveChapterId, uploadedFile } = useAppContext();
  const courseTitle = uploadedFile?.name ?? "未选择教材";
  const chapterCount = parsedChapters?.length ?? 0;
  const lessonCount = generatedLessons?.length ?? 0;

  if (!uploadedFile || lessonCount === 0) {
    return (
      <div className="screen-stack centered-flow parse-complete-screen course-ready-screen course-ready-empty">
        <FileText size={42} aria-hidden="true" />
        <h1>还没有可进入的课程</h1>
        <p>完成上传和后端解析后，课程目录、RAG 片段和学习计划会显示在这里。</p>
        <Button icon={<Upload size={18} aria-hidden="true" />} onClick={() => go("upload")}>上传教材</Button>
        <Button variant="secondary" onClick={() => go("library")}>查看课程库</Button>
      </div>
    );
  }

  return (
    <CourseCompletionScreen
      assetCount={parsedAssets?.length ?? 0}
      chapterCount={chapterCount}
      courseTitle={courseTitle}
      lessonCount={lessonCount}
      motionKey={`course-ready:${uploadedFile.bookId}:${lessonBuildJobId ?? "current"}`}
      onEnterStudy={() => {
        setActiveChapterId(generatedLessons?.[0]?.chapter_id ?? null);
        go("study");
      }}
      onViewPlan={() => go("plan")}
      ragChunkCount={parsedChunks?.length ?? 0}
      statusTitle="生成成功"
    />
  );
}
