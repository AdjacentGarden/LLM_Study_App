import {
  Button,
  Card,
  Metric,
  Pill
} from "../components/ui";
import { useAppContext } from "../context/AppContext";
import { useLocalMotionItem } from "../motion";

export function LessonReportScreen() {
  const { go, showToast } = useAppContext();
  const summaryMotion = useLocalMotionItem("lesson-report:summary");
  return (
    <div className="screen-stack report-screen">
      <div className="report-workspace">
        <div className="report-summary-column">
      <Card {...summaryMotion.attributes} surface="celebration" className="report-card">
        <p className="eyebrow">章节学习报告</p>
        <h2>太棒了！</h2>
        <p>第 2 章 基因和染色体的关系</p>
        <div className="report-score-ring">
          <strong>82%</strong>
        </div>
      </Card>
      <div className="metric-grid">
        <Metric label="正确率" value="82%" />
        <Metric label="用时" value="24 分" />
        <Metric label="错题" value="2 题" />
      </div>
        </div>
        <div className="report-guidance-column">
      <Card className="knowledge-map-card">
        <div className="section-head">
          <h3>知识点掌握情况</h3>
          <button className="inline-link" type="button" onClick={() => go("notes")}>查看详情</button>
        </div>
        <div className="chip-row static">
          <Pill tone="mint">综合推理</Pill>
          <Pill tone="mint">同源染色体</Pill>
          <Pill tone="mint">四分体形成</Pill>
          <Pill tone="orange">交叉互换机制</Pill>
          <Pill tone="orange">染色体分离异常</Pill>
        </div>
      </Card>
      <Card className="ai-suggestion-card">
        <h3>AI 学习建议</h3>
        <div className="suggestion-grid">
          {["背本节闪卡", "再做专项练习", "与 AI 深入问答"].map((item) => (
            <button type="button" key={item} onClick={() => item.includes("闪卡") ? go("flashcards") : item.includes("问答") ? showToast("可以打开右侧 AI 助手继续提问", "info") : go("assignment")}>
              {item}
            </button>
          ))}
        </div>
      </Card>
        </div>
      </div>
      <div className="report-actions">
      <Button onClick={() => go("study")}>返回学习目录·继续下一章</Button>
      <Button variant="secondary" onClick={() => go("mistakes")}>查看错题复习</Button>
      </div>
    </div>
  );
}
