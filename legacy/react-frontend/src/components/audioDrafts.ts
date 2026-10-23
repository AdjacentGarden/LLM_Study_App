import type { InkNote } from "../api/learningStudio";

export interface VoiceDraft {
  scope: string;
  note: InkNote;
  blob: Blob;
  duration: number;
  recording: boolean;
  updated: number;
  uploadBase?: InkNote;
}
export interface VoiceDraftStore {
  put(draft: VoiceDraft): Promise<void>;
  get(scope: string, noteId: string): Promise<VoiceDraft | null>;
  list(scope: string, bookId: string): Promise<VoiceDraft[]>;
  remove(scope: string, noteId: string): Promise<void>;
}
const id = (scope: string, noteId: string) => JSON.stringify([scope, noteId]);
const storageError = () => new Error("当前浏览器无法可靠地保存本地录音；请先同步或下载录音，不要刷新或关闭页面。");
type StoredVoiceDraft = Omit<VoiceDraft, "blob"> & { blob?: Blob; audioBytes?: ArrayBuffer; audioMime?: string };
function restore(value: StoredVoiceDraft | undefined): VoiceDraft | null {
  if (!value) return null;
  const blob = value.blob ?? (value.audioBytes ? new Blob([value.audioBytes], { type: value.audioMime || "audio/webm" }) : null);
  return blob ? { ...value, blob } : null;
}

export function browserVoiceDraftStore(factory?: IDBFactory): VoiceDraftStore {
  let opening: Promise<IDBDatabase> | null = null;
  function database(): Promise<IDBDatabase> {
    if (opening) return opening;
    opening = new Promise((resolve, reject) => {
      let request: IDBOpenDBRequest;
      try {
        const indexed = factory ?? globalThis.indexedDB;
        if (!indexed) throw storageError();
        request = indexed.open("cloudpath-voice-drafts", 1);
      } catch { reject(storageError()); return; }
      request.onupgradeneeded = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains("drafts")) {
          const store = db.createObjectStore("drafts", { keyPath: "id" });
          store.createIndex("scope", "scope");
        }
      };
      request.onerror = () => reject(storageError());
      request.onblocked = () => reject(new Error("另一个页面正在使用录音草稿，请关闭旧页面后重试。"));
      request.onsuccess = () => {
        const db = request.result;
        db.onversionchange = () => { db.close(); opening = null; };
        resolve(db);
      };
    });
    opening.catch(() => { opening = null; });
    return opening;
  }
  async function transaction<T>(mode: IDBTransactionMode, work: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
    const db = await database();
    return new Promise((resolve, reject) => {
      let result: T;
      const tx = db.transaction("drafts", mode);
      tx.oncomplete = () => resolve(result);
      tx.onabort = tx.onerror = () => reject(storageError());
      try {
        const request = work(tx.objectStore("drafts"));
        request.onsuccess = () => { result = request.result; };
        request.onerror = () => reject(storageError());
      } catch { tx.abort(); reject(storageError()); }
    });
  }
  return {
    async put(draft) {
      // WebKit can abort otherwise valid Blob writes with an UnknownError.
      // Store the exact bytes and MIME without transcoding; open the transaction
      // only after reading the bytes so it cannot expire during that await.
      const { blob, ...metadata } = draft;
      const audioBytes = await blob.arrayBuffer();
      await transaction("readwrite", store => store.put({ ...metadata, audioBytes, audioMime: blob.type, id: id(draft.scope, draft.note.id) }));
    },
    async get(scope, noteId) {
      const value = restore(await transaction("readonly", store => store.get(id(scope, noteId))) as StoredVoiceDraft | undefined);
      return value?.scope === scope && value.note.id === noteId ? value : null;
    },
    async list(scope, bookId) {
      const values = await transaction("readonly", store => store.index("scope").getAll(scope)) as StoredVoiceDraft[];
      return values.map(restore).filter((value): value is VoiceDraft => Boolean(value && value.scope === scope && value.note.book_id === bookId));
    },
    async remove(scope, noteId) { await transaction("readwrite", store => store.delete(id(scope, noteId))); },
  };
}

/** Serialize writes/deletion, so an earlier chunk cannot resurrect a synced draft. */
export class VoiceDraftWriter {
  private tail: Promise<void> = Promise.resolve();
  private store: VoiceDraftStore;
  constructor(store: VoiceDraftStore) { this.store = store; }
  put(draft: VoiceDraft): Promise<void> {
    const task = this.tail.catch(() => {}).then(() => this.store.put(draft));
    this.tail = task; return task;
  }
  remove(scope: string, noteId: string): Promise<void> {
    const task = this.tail.catch(() => {}).then(() => this.store.remove(scope, noteId));
    this.tail = task; return task;
  }
  flush(): Promise<void> { return this.tail; }
}

export async function stopAndWaitForRecording(
  recorder: { state: string; stop(): void } | null, finished: Promise<void> | null, timeoutMs = 8000,
): Promise<void> {
  if (recorder && recorder.state !== "inactive") recorder.stop();
  if (finished) {
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      await Promise.race([finished, new Promise<never>((_resolve, reject) => {
        timer = setTimeout(() => reject(new Error("录音尚未停止保存，请保持此页并再次点击停止。")), timeoutMs);
      })]);
    } finally { if (timer) clearTimeout(timer); }
  }
}
