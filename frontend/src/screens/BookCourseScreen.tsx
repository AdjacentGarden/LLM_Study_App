import { useAppContext } from "../context/AppContext";
import { StudyScreen } from "./StudyScreen";

// Compatibility wrapper for older navigation snapshots. The canonical
// learning destination is StudyScreen, while this name remains part of the
// public screen contract used by existing integrations.
export function BookCourseScreen() {
  useAppContext();
  return <StudyScreen />;
}
