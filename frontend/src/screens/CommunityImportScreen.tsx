import { useAppContext } from "../context/AppContext";
import { Button, Card } from "../components/ui";

export function CommunityImportScreen() {
  const { go } = useAppContext();

  return (
    <div className="screen-stack community-import-screen">
      <Card title="导入流程已经更新">
        <p className="muted">社区教材不会再显示虚假的成功结果。</p>
        <p>请从社区教材详情页发起导入。系统会在真实 PDF 下载、校验并保存完成后，才进入课程整理页面。</p>
        <Button onClick={() => go("community")}>返回社区</Button>
      </Card>
    </div>
  );
}
