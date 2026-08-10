import {
  CheckCircle2
} from "lucide-react";
import {
  Button,
  Card,
  Metric,
  ProgressBar
} from "../components/ui";
import { useAppContext } from "../context/AppContext";
import { useLocalMotionItem } from "../motion";
import { resolveCommunityBook } from "./communityCatalog";

export function CommunityImportScreen() {
  const { go, selectedCommunityBookId } = useAppContext();
  const book = resolveCommunityBook(selectedCommunityBookId);
  const successMotion = useLocalMotionItem(`community-import:${book.id}:success`);

  return (
    <div className="screen-stack community-import-screen">
      <div className="community-import-workspace">
      <div {...successMotion.attributes} className="centered-flow community-import-success">
        <div className="success-mark glass-button">
          <CheckCircle2 size={34} aria-hidden="true" />
        </div>
        <h1>导入成功</h1>
        <p>已把「{book.title}」加入你的课程空间，并生成章节进度、闪卡、练习和笔记模块。</p>
      </div>

      <Card className="import-progress-card">
        <h3>章节同步</h3>
        <ProgressBar value={50} label="已完成 50%" />
        <ProgressBar value={12} label="闪卡生成 12%" />
        <ProgressBar value={12} label="测试生成 12%" />
      </Card>

      <div className="capability-grid">
        <Metric label="章节" value={`${book.chapters.length}`} helper="已导入" />
        <Metric label="闪卡" value={`${book.flashcardCount}`} helper="生成中" />
        <Metric label="测试" value="8" helper="可练习" />
      </div>
      </div>

      <div className="community-import-actions">
      <Button onClick={() => go("study")}>进入学习</Button>
      <Button variant="secondary" onClick={() => go("library")}>查看我的课程</Button>
      </div>
    </div>
  );
}
