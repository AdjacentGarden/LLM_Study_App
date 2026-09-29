import type { StudyPlan } from "../../types/studyWorkspace";

function escapeText(value: string): string {
  return value.replace(/\\/g, "\\\\").replace(/\r\n|\n|\r/g, "\\n").replace(/;/g, "\\;").replace(/,/g, "\\,");
}

function foldLine(line: string): string {
  const encoder = new TextEncoder();
  const parts: string[] = [];
  let current = "";
  let length = 0;
  for (const character of line) {
    const bytes = encoder.encode(character).length;
    if (length + bytes > 74) { parts.push(current); current = " "; length = 1; }
    current += character; length += bytes;
  }
  parts.push(current);
  return parts.join("\r\n");
}

export function studyCalendar(plan: StudyPlan, bookTitle: string, startDate: string, now = new Date()): string {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(startDate)) throw new Error("请选择有效的开始日期。");
  const start = new Date(`${startDate}T00:00:00Z`);
  if (!Number.isFinite(start.getTime()) || start.toISOString().slice(0, 10) !== startDate) throw new Error("请选择有效的开始日期。");
  const stamp = now.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}Z$/, "Z");
  const dateAt = (offset: number) => {
    const date = new Date(start); date.setUTCDate(date.getUTCDate() + offset);
    return date.toISOString().slice(0, 10).replace(/-/g, "");
  };
  const lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//CloudPath//Study Plan//ZH", "CALSCALE:GREGORIAN"];
  for (const day of plan.days) {
    if (!day.tasks.length) continue;
    // Identical exported tasks and start day have a stable UID when re-imported.
    const taskIdentity = day.tasks.map(task => task.task_id).join("|");
    let digest = 2166136261;
    for (const character of taskIdentity) digest = Math.imul(digest ^ character.charCodeAt(0), 16777619) >>> 0;
    lines.push("BEGIN:VEVENT", `UID:study-${digest.toString(16)}-${startDate}-${day.day_index}@cloudpath.local`,
      `DTSTAMP:${stamp}`, `DTSTART;VALUE=DATE:${dateAt(day.day_index - 1)}`, `DTEND;VALUE=DATE:${dateAt(day.day_index)}`,
      `SUMMARY:${escapeText(`${bookTitle} · 第 ${day.day_index} 天`)}`,
      `DESCRIPTION:${escapeText(`建议 ${day.estimated_minutes} 分钟\n${day.tasks.map(task => `${task.status === "done" ? "已完成" : "待学习"} · ${task.title.replace(/（原书正文缺失）/g, "").trim()} · ${task.estimated_minutes} 分钟`).join("\n")}\n任务状态以云径学习计划为准。`)}`,
      "TRANSP:TRANSPARENT", "END:VEVENT");
  }
  lines.push("END:VCALENDAR");
  return lines.map(foldLine).join("\r\n") + "\r\n";
}

export function downloadStudyCalendar(plan: StudyPlan, bookTitle: string, startDate: string): void {
  const file = new Blob([studyCalendar(plan, bookTitle, startDate)], { type: "text/calendar;charset=utf-8" });
  const url = URL.createObjectURL(file);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${bookTitle.replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_").slice(0, 70) || "云径"}-学习计划.ics`;
  document.body.appendChild(link); link.click(); link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
