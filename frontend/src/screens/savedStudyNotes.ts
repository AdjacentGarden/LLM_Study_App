export type SavedStudyNote = {
  id: string;
  title: string;
  body: string;
  quote?: string;
  sourceLabel?: string;
  createdAt: number;
};

const storageKey = "bookcourse.saved-study-notes.v1";

function getStorage() {
  return typeof window === "undefined" ? null : window.localStorage;
}

export function loadSavedStudyNotes(): SavedStudyNote[] {
  const storage = getStorage();
  if (!storage) return [];

  try {
    const value: unknown = JSON.parse(storage.getItem(storageKey) ?? "[]");
    if (!Array.isArray(value)) return [];
    return value.filter((note): note is SavedStudyNote => (
      typeof note === "object"
      && note !== null
      && typeof note.id === "string"
      && typeof note.title === "string"
      && typeof note.body === "string"
      && typeof note.createdAt === "number"
    ));
  } catch {
    return [];
  }
}

export function saveStudyNote(note: Omit<SavedStudyNote, "id" | "createdAt">) {
  const savedNote: SavedStudyNote = {
    ...note,
    id: `study-note-${Date.now()}`,
    createdAt: Date.now()
  };
  const storage = getStorage();
  if (!storage) return savedNote;

  try {
    storage.setItem(storageKey, JSON.stringify([savedNote, ...loadSavedStudyNotes()]));
  } catch {
    // Saving the in-memory count and toast still provides feedback when a
    // browser blocks storage (for example, a strict private session).
  }
  return savedNote;
}
