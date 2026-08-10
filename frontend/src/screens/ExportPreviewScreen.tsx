import { useState } from "react";
import { Check, Download } from "lucide-react";
import {
  Button,
  Card
} from "../components/ui";
import { useAppContext } from "../context/AppContext";
import { useLocalMotionItem } from "../motion";

export function ExportPreviewScreen() {
  const { showToast } = useAppContext();
  const introMotion = useLocalMotionItem("export-preview:intro");
  const modules = ["本章学习目标", "AI 导学笔记", "原文引用页码", "重点概念", "小测与错题诊断", "复习建议", "用户个人笔记"];
  const [selectedModules, setSelectedModules] = useState<ReadonlySet<string>>(() => new Set(modules));
  const [interactedModules, setInteractedModules] = useState<ReadonlySet<string>>(() => new Set());

  function setModuleSelected(module: string, selected: boolean) {
    setInteractedModules((current) => {
      const next = new Set(current);
      next.add(module);
      return next;
    });
    setSelectedModules((current) => {
      const next = new Set(current);
      if (selected) next.add(module);
      else next.delete(module);
      return next;
    });
  }

  return (
    <div className="screen-stack export-preview-screen">
      <div className="export-workspace">
      <Card {...introMotion.attributes} className="export-intro-card">
        <h2>导出 PDF 预览</h2>
        <p>默认不导出整本原书内容，避免版权风险。</p>
      </Card>
      <div className="check-list export-module-list">
        {modules.map((module) => (
          <label key={module}>
            <span className="export-checkbox-control">
              <input
                type="checkbox"
                checked={selectedModules.has(module)}
                onChange={(event) => setModuleSelected(module, event.target.checked)}
              />
              <span
                className="export-check-visual"
                data-motion-checkbox-state={selectedModules.has(module)
                  ? interactedModules.has(module) ? "checked" : "initial"
                  : "unchecked"}
                aria-hidden="true"
              >
                <Check className="export-check-path" size={15} strokeWidth={3} />
              </span>
            </span>
            <span>{module}</span>
          </label>
        ))}
      </div>
      </div>
      <div className="export-actions">
      <Button icon={<Download size={18} aria-hidden="true" />} onClick={() => showToast("导学笔记 PDF 已生成")}>确认导出</Button>
      </div>
    </div>
  );
}
