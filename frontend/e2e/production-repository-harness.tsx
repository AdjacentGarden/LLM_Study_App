import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "../src/App";
import { ErrorBoundary } from "../src/components/ErrorBoundary";
import { BookCourseRepositoryProvider } from "../src/context/BookCourseRepositoryContext";
import { MotionHistoryProvider } from "../src/motion/MotionHistoryContext";
import { DemoRepository } from "../src/services/DemoRepository";
import type {
  ApiAsset,
  AssignmentSubmitRequest,
  AssignmentSubmitResponse,
  CourseSummary,
  DiagnosisResponse,
  MistakeRecord,
  StudyPlan,
  StudyTask,
  StudyTaskUpdate
} from "../src/types/api";
import "../src/styles/tokens.css";
import "../src/styles/glass.css";
import "../src/styles/base.css";
import "../src/styles/responsive.css";
import "../src/styles/home.css";
import "../src/styles/chapter-tools.css";
import "../src/styles/study.css";
import "../src/styles/motion.css";
import "../src/styles/card-system.css";
import "../src/styles/community.css";
import "../src/styles/upload.css";
import "../src/styles/parse-ready.css";
import "../src/styles/processing.css";
import "../src/styles/chapter-confirm.css";

type ProductionScenario =
  | "default"
  | "library"
  | "empty"
  | "course-error"
  | "course-loading"
  | "image-failure"
  | "image-mixed"
  | "plan-sparse"
  | "plan-out-of-range"
  | "plan-custom"
  | "mistakes-error"
  | "mistakes-loading";

const supportedScenarios = new Set<ProductionScenario>([
  "default",
  "library",
  "empty",
  "course-error",
  "course-loading",
  "image-failure",
  "image-mixed",
  "plan-sparse",
  "plan-out-of-range",
  "plan-custom",
  "mistakes-error",
  "mistakes-loading"
]);

function readScenario(): ProductionScenario {
  const requested = new URL(window.location.href).searchParams.get("scenario") as ProductionScenario | null;
  return requested && supportedScenarios.has(requested) ? requested : "default";
}

function readPlanDays(): unknown {
  const value = new URL(window.location.href).searchParams.get("planDays");
  if (value === null) return undefined;
  try {
    return JSON.parse(value) as unknown;
  } catch {
    return value;
  }
}

class ProductionScenarioRepository extends DemoRepository {
  private readonly scenario = readScenario();
  private readonly planDays = readPlanDays();
  private readonly deletedBookIds = new Set<string>();
  private readonly callCounts = new Map<string, number>();
  private appendedCourseCount = 0;
  private mistakesRelease: (() => void) | null = null;
  private mistakesWait: Promise<void> | null = null;

  getScenario() {
    return this.scenario;
  }

  getCallCount(key: string) {
    return this.callCounts.get(key) ?? 0;
  }

  private recordCall(key: string) {
    this.callCounts.set(key, this.getCallCount(key) + 1);
  }

  appendCourse() {
    this.appendedCourseCount += 1;
  }

  releaseMistakes() {
    this.mistakesRelease?.();
    this.mistakesRelease = null;
    this.mistakesWait = null;
  }

  override async listCourses(): Promise<CourseSummary[]> {
    if (this.scenario === "course-loading") return new Promise<CourseSummary[]>(() => undefined);
    if (this.scenario === "course-error") throw new Error("Controlled production course list failed");
    if (this.scenario === "empty") return [];

    const [base] = await super.listCourses();
    const courses = this.scenario === "library"
      ? [
          base,
          {
            ...base,
            book_id: `${base.book_id}-long-title`,
            title: `${"用于验证窄屏两行截断与稳定卡片宽度的超长教材标题 ".repeat(3)}结尾`,
            filename: "production-long-title.pdf",
            chapter_count: 12,
            updated_at: base.updated_at + 1
          },
          {
            ...base,
            book_id: `${base.book_id}-secondary`,
            title: "第二本生产仓储教材",
            filename: "production-secondary.pdf",
            chapter_count: 7,
            updated_at: base.updated_at + 2
          }
        ]
      : [base];
    const appended = Array.from({ length: this.appendedCourseCount }, (_, index) => ({
      ...base,
      book_id: `${base.book_id}-appended-${index + 1}`,
      title: `新导入教材 ${index + 1}`,
      filename: `production-appended-${index + 1}.pdf`,
      updated_at: base.updated_at + 10 + index
    }));
    return [...courses, ...appended].filter((course) => !this.deletedBookIds.has(course.book_id));
  }

  override async deleteCourse(bookId: string) {
    this.deletedBookIds.add(bookId);
    await super.deleteCourse(bookId);
  }

  override async getAssets(bookId: string): Promise<ApiAsset[]> {
    const assets = await super.getAssets(bookId);
    if (this.scenario !== "image-failure" && this.scenario !== "image-mixed") {
      // The default production visual harness historically rendered the stable
      // full-page preview. Keep that deterministic source-page contract now
      // that SourceReader correctly consumes repository page-image metadata;
      // image fault scenarios below remain explicitly isolated.
      return assets.map((asset) => asset.source_type === "extracted"
        ? { ...asset, source_page_image_url: "/assets/textbook/biology-chapter-2-open.webp" }
        : asset);
    }
    return assets.map((asset, index) => {
      if (this.scenario === "image-mixed" && index > 0) return asset;
      const missingUrl = `/assets/textbook/__missing-production-${index + 1}.webp`;
      return {
        ...asset,
        image_url: missingUrl,
        thumbnail_url: missingUrl,
        source_page_image_url: missingUrl
      };
    });
  }

  override async getStudyPlan(bookId: string, userId = "local_user"): Promise<StudyPlan> {
    const plan = await super.getStudyPlan(bookId, userId);
    if (this.scenario === "plan-custom") {
      return {
        ...plan,
        days: this.planDays as number,
        tasks: plan.tasks.slice(0, 1).map((task) => ({ ...task, day: 4_294_967_297, status: "pending" }))
      };
    }
    if (this.scenario !== "plan-sparse" && this.scenario !== "plan-out-of-range") return plan;
    const tasks = plan.tasks.slice(0, 2).map((task, index) => ({
      ...task,
      day: this.scenario === "plan-out-of-range" && index === 0 ? 4_294_967_297 : index + 1,
      status: index === 1 ? "done" : "pending"
    }));
    return { ...plan, days: 7, tasks };
  }

  override async patchStudyTask(taskId: string, payload: StudyTaskUpdate): Promise<StudyTask> {
    this.recordCall(`patchStudyTask:${taskId}`);
    return super.patchStudyTask(taskId, payload);
  }

  override async submitAssignment(assignmentId: string, payload: AssignmentSubmitRequest): Promise<AssignmentSubmitResponse> {
    this.recordCall(`submitAssignment:${assignmentId}`);
    return super.submitAssignment(assignmentId, payload);
  }

  override async diagnoseAssignment(assignmentId: string, submissionId: string): Promise<DiagnosisResponse> {
    this.recordCall(`diagnoseAssignment:${assignmentId}`);
    return super.diagnoseAssignment(assignmentId, submissionId);
  }

  override async getMistakes(userId: string, bookId?: string): Promise<MistakeRecord[]> {
    if (this.scenario === "mistakes-error") throw new Error("Controlled production mistake list failed");
    if (this.scenario === "mistakes-loading") {
      if (!this.mistakesWait) {
        this.mistakesWait = new Promise<void>((resolve) => {
          this.mistakesRelease = resolve;
        });
      }
      await this.mistakesWait;
    }
    return super.getMistakes(userId, bookId);
  }
}

const repository = new ProductionScenarioRepository();

declare global {
  interface Window {
    __productionRepositoryHarness?: {
      appendCourse: () => void;
      getCallCount: (key: string) => number;
      getScenario: () => ProductionScenario;
      releaseMistakes: () => void;
    };
  }
}

window.__productionRepositoryHarness = {
  appendCourse: () => repository.appendCourse(),
  getCallCount: (key) => repository.getCallCount(key),
  getScenario: () => repository.getScenario(),
  releaseMistakes: () => repository.releaseMistakes()
};

const rootElement = document.getElementById("production-repository-root");
if (!rootElement) throw new Error("Production repository harness root is missing.");

createRoot(rootElement).render(
  <StrictMode>
    <ErrorBoundary>
      <BookCourseRepositoryProvider repository={repository}>
        <MotionHistoryProvider>
          <App />
        </MotionHistoryProvider>
      </BookCourseRepositoryProvider>
    </ErrorBoundary>
  </StrictMode>
);
