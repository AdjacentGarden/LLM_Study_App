import type { StudyPlan } from "../types/api";
import type { Screen } from "../types/app";
import type { HomeBookListState } from "./homeBookModel";

export type HomeGlobalActionId = "plan" | "mistakes" | "upload";

export type HomeGlobalAction = Readonly<{
  id: HomeGlobalActionId;
  title: string;
  helper: string;
  target: Extract<Screen, "plan" | "mistakes" | "upload">;
}>;

export type BuildHomeGlobalActionsInput = Readonly<{
  listState: HomeBookListState;
  selectedBookId: string | null;
  selectedLoadedReady: boolean;
  plan: StudyPlan | null;
}>;

function isCompletedTaskStatus(status: string) {
  const normalizedStatus = status.trim().toLocaleLowerCase();
  return normalizedStatus === "done" || normalizedStatus === "completed";
}

export function buildHomeGlobalActions({
  listState,
  selectedBookId,
  selectedLoadedReady,
  plan
}: BuildHomeGlobalActionsInput): readonly HomeGlobalAction[] {
  if (listState !== "content") return [];

  const actions: HomeGlobalAction[] = [];
  const selectedPlan = selectedLoadedReady
    && selectedBookId
    && plan?.book_id === selectedBookId
    && plan.tasks.length > 0
      ? plan
      : null;

  if (selectedPlan) {
    const pendingTaskCount = selectedPlan.tasks.filter((task) => !isCompletedTaskStatus(task.status)).length;
    actions.push({
      id: "plan",
      title: "学习计划",
      helper: pendingTaskCount > 0
        ? `${pendingTaskCount} 项待完成`
        : `${selectedPlan.tasks.length} 项任务已完成`,
      target: "plan"
    });
  }

  if (selectedLoadedReady) {
    actions.push({
      id: "mistakes",
      title: "错题复习",
      helper: "按当前教材回看诊断记录",
      target: "mistakes"
    });
  }

  actions.push({
    id: "upload",
    title: "上传新书",
    helper: "添加另一份教材",
    target: "upload"
  });

  return actions;
}
