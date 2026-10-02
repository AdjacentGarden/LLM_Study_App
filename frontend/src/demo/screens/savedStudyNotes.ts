import { putStudyNote } from "../features/studyNotes/repository";

export type SavedStudyNote = {
  id: string;
  title: string;
  body: string;
  quote?: string;
  sourceLabel?: string;
  createdAt: number;
};

export function loadSavedStudyNotes(): SavedStudyNote[] {return [];}
export async function saveStudyNote(note:Omit<SavedStudyNote,"id"|"createdAt"> & {anchor?: import("../features/studyNotes/types").NoteAnchor}) {
 const savedNote={...note,id:`study-note-${crypto.randomUUID()}`,createdAt:Date.now()};
 await putStudyNote({id:savedNote.id,kind:"text",title:savedNote.title,body:savedNote.body,anchor:note.anchor,createdAt:savedNote.createdAt,updatedAt:savedNote.createdAt,noteVersion:1,pipelinePhase:"complete"});
 return savedNote;
}
