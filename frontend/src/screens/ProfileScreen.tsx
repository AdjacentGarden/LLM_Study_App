import { useEffect, useState } from "react";
import {
  Bell,
  BookOpen,
  NotebookPen,
  SearchCheck,
  Settings
} from "lucide-react";
import { runtimeConfig } from "../config/runtime";
import type {
  LearningState
} from "../types/api";
import {
  Card,
  Metric
} from "../components/ui";
import { useAppContext } from "../context/AppContext";
import { useBookCourseRepository } from "../context/BookCourseRepositoryContext";
import { useLocalMotionItem } from "../motion";
import {
  SettingsRow
} from "./shared";
import { resolveLatestCourseTitle } from "./courseResourceIdentity";

export function ProfileScreen() {
  const bookcourseRepository = useBookCourseRepository();
  const { courseSummaries, currentStudyPlan, go, showToast, uploadedFile } = useAppContext();
  const [learningState, setLearningState] = useState<LearningState | null>(null);
  const courseCount = courseSummaries.length + (
    uploadedFile
    && uploadedFile.origin !== "remote-course"
    && !courseSummaries.some((course) => course.book_id === uploadedFile.bookId)
      ? 1
      : 0
  );
  const latestCourseTitle = resolveLatestCourseTitle(uploadedFile, courseSummaries);
  const courseHelper = courseCount > 0
    ? `${courseCount} 门后端课程 · ${latestCourseTitle ?? "管理已导入内容"}`
    : "暂无后端课程 · 上传后自动生成";
  const profileMotion = useLocalMotionItem(`profile:${uploadedFile?.bookId ?? "guest"}:${learningState ? "loaded" : "baseline"}`);

  useEffect(() => {
    if (!uploadedFile) return;
    let active = true;
    bookcourseRepository.getLearningState(runtimeConfig.defaultUserId).then((state) => {
      if (active) setLearningState(state);
    });
    return () => {
      active = false;
    };
  }, [bookcourseRepository, uploadedFile]);

  return (
    <div className="screen-stack profile-screen">
      <div className="profile-workspace">
        <div className="profile-summary-column">
      <Card {...profileMotion.attributes} className="profile-card">
        <div className="avatar glass-button">BC</div>
        <div>
          <h2>我的学习</h2>
          <p>{uploadedFile ? `后端同步 · 待完成 ${learningState?.pending_tasks ?? currentStudyPlan?.tasks.length ?? 0} 项` : "连续学习 5 天 · 本周完成 3 节"}</p>
        </div>
      </Card>
      <div className="metric-grid">
        <Metric label="完成任务" value={`${learningState?.completed_tasks ?? 0}`} />
        <Metric label="平均分" value={learningState?.average_score ? `${Math.round(learningState.average_score)}` : uploadedFile ? "-" : "82"} />
        <Metric label="错题复习" value={`${learningState?.mistake_count ?? (uploadedFile ? 0 : 12)}`} />
      </div>
        </div>
        <div className="profile-settings-list">
      <SettingsRow icon={<BookOpen size={18} aria-hidden="true" />} title="我的课程" helper={courseHelper} onClick={() => go("library")} />
      <SettingsRow icon={<SearchCheck size={18} aria-hidden="true" />} title="今日复习计划" helper={uploadedFile ? `${currentStudyPlan?.tasks.length ?? 0} 个任务已归入课程计划` : "12 张闪卡已归入课程计划"} onClick={() => go("plan")} />
      <SettingsRow icon={<NotebookPen size={18} aria-hidden="true" />} title="导出记录" helper={uploadedFile ? `${uploadedFile.name}.notes` : "BookCourse-AI-遗传与进化.pdf"} onClick={() => go("notes")} />
      <SettingsRow icon={<Bell size={18} aria-hidden="true" />} title="学习提醒" helper="每天 20:30" onClick={() => showToast("学习提醒已开启")} />
      <SettingsRow icon={<Settings size={18} aria-hidden="true" />} title="偏好设置" helper="降低透明度、字体大小、隐私" onClick={() => showToast("偏好已保存")} />
        </div>
      </div>
    </div>
  );
}
