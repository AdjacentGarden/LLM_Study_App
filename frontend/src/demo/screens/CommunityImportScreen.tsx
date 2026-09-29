import { useAppContext } from "../context/AppContext";
import { resolveCommunityBook } from "./communityCatalog";
import { CourseCompletionScreen } from "./CourseReadyScreen";

export function CommunityImportScreen({ importGeneration }: { importGeneration: number }) {
  const { go, selectedCommunityBookId } = useAppContext();
  const book = resolveCommunityBook(selectedCommunityBookId);

  return (
    <CourseCompletionScreen
      assetCount={book.chapters.length}
      chapterCount={book.chapters.length}
      className="community-import-screen"
      courseTitle={book.title}
      lessonCount={1}
      motionKey={`community-import:${book.id}:${importGeneration}:ready`}
      onEnterStudy={() => go("study")}
      onViewPlan={() => go("plan")}
      ragChunkCount={book.flashcardCount}
      statusTitle="导入成功"
      focused
    />
  );
}
