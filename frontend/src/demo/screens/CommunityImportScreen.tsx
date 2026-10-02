import { useAppContext } from "../context/AppContext";
import { resolveCommunityBook } from "./communityCatalog";
import { CourseCompletionScreen } from "./CourseReadyScreen";
import { preferredCourseSource } from "../features/courses/selectors";

export function CommunityImportScreen({ importGeneration }: { importGeneration: number }) {
  const { go, selectCourse, courses, sourceSummaries, selectedCommunityBookId } = useAppContext();
  const book = resolveCommunityBook(selectedCommunityBookId);

  return (
    <CourseCompletionScreen
      assetCount={book.chapters.length}
      chapterCount={book.chapters.length}
      className="community-import-screen"
      courseTitle={book.title}
      completionMessage={`《${book.title}》已加入你的课程，可在课程中继续添加教材和文件。`}
      lessonCount={0}
      motionKey={`community-import:${book.id}:${importGeneration}:ready`}
      onEnterStudy={() => {
        const course = courses.state.courses.find((item) => item.id === courses.state.activeCourseId);
        if (course) void selectCourse(course.id).then((selected) => {
          if (selected) go(preferredCourseSource(course, courses.state.resources, sourceSummaries) ? "study" : "courseDetail");
        });
      }}
      onViewPlan={() => go("plan")}
      ragChunkCount={book.flashcardCount}
      statusTitle="导入成功"
      focused
    />
  );
}
