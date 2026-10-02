import { runtimeConfig } from "../config/runtime";
import { useEffect, useRef, useState } from "react";
import { App as CapacitorApp } from "@capacitor/app";
import type { PluginListenerHandle } from "@capacitor/core";
import {
  BookOpenCheck,
  CircleStop,
  LoaderCircle,
  Mic2,
  Pause,
  Play,
  RotateCcw,
  Save,
  Sparkles
} from "lucide-react";
import { Button, Card, Pill } from "../components/ui";
import { useAppContext } from "../context/AppContext";
import {
  processStudyNote as ownedProcessStudyNote,
  createStudyNoteId,
  deleteAudioBlob as ownedDeleteAudioBlob,
  getAudioBlob as ownedGetAudioBlob,
  getStudyNote as ownedGetStudyNote,
  putStudyNote as ownedPutStudyNote,
  saveAudioBlob as ownedSaveAudioBlob
} from "../features/studyNotes/repository";
import type { NoteCaptureIntent, NotePipelinePhase, VoiceStudyNote } from "../features/studyNotes/types";

type RecordingState = "idle" | "requesting" | "recording" | "paused" | "ready";

const maximumDurationMs = 10 * 60 * 1000;
const maximumSizeBytes = 20 * 1024 * 1024;

export function chooseRecordingMimeType() {
  if (typeof MediaRecorder === "undefined") return "";
  return ["audio/webm;codecs=opus", "audio/mp4;codecs=mp4a.40.2", "audio/mp4"]
    .find((type) => MediaRecorder.isTypeSupported(type)) ?? "";
}

export function formatDuration(durationMs: number) {
  const seconds = Math.max(0, Math.floor(durationMs / 1000));
  return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

function downsampleWaveform(values: number[], count = 48) {
  if (values.length <= count) return values;
  const result: number[] = [];
  const bucket = values.length / count;
  for (let index = 0; index < count; index += 1) {
    const start = Math.floor(index * bucket);
    const end = Math.max(start + 1, Math.floor((index + 1) * bucket));
    result.push(Math.max(...values.slice(start, end)));
  }
  return result;
}

export function VoiceNoteScreen({ embedded = false, captureIntent, onClose }: {
  embedded?: boolean;
  captureIntent?: NoteCaptureIntent;
  onClose?: () => void;
} = {}) {
  const owner=useRef(runtimeConfig.defaultUserId).current;
  const lifecycle=useRef(new AbortController());
  useEffect(()=>{lifecycle.current=new AbortController();return()=>lifecycle.current.abort();},[]);
  function assertActive(){if(lifecycle.current.signal.aborted)throw new Error("笔记页面已关闭");}

  const getStudyNote=(id:string)=>ownedGetStudyNote(id,owner);
  const putStudyNote=(note:Parameters<typeof ownedPutStudyNote>[0])=>{assertActive();return ownedPutStudyNote(note,owner);};
  const saveAudioBlob=(id:string,blob:Blob)=>{assertActive();return ownedSaveAudioBlob(id,blob,owner);};
  const deleteAudioBlob=(id:string)=>ownedDeleteAudioBlob(id,owner);
  const getAudioBlob=(id:string)=>ownedGetAudioBlob(id,owner);
  const processStudyNote=(id:string,action:Parameters<typeof ownedProcessStudyNote>[1],payload:Parameters<typeof ownedProcessStudyNote>[2]={})=>{assertActive();return ownedProcessStudyNote(id,action,payload,owner,lifecycle.current.signal);};
  const {
    activeChapterId,
    courses,
    noteCaptureIntent: contextNoteCaptureIntent,
    parsedChapters,
    showToast,
    uploadedFile
  } = useAppContext();
  const noteCaptureIntent = captureIntent ?? contextNoteCaptureIntent;
  const noteIdRef = useRef(noteCaptureIntent?.existingNoteId ?? createStudyNoteId("voice"));
  const audioIdRef = useRef<string | undefined>(undefined);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const animationFrameRef = useRef<number | null>(null);
  const timerRef = useRef<number | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const discardRecordingRef = useRef(false);
  const waveformRef = useRef<number[]>([]);
  const startedAtRef = useRef(0);
  const pausedAtRef = useRef(0);
  const totalPausedRef = useRef(0);
  const [recordingState, setRecordingState] = useState<RecordingState>("idle");
  const [durationMs, setDurationMs] = useState(0);
  const [audioBlob, setAudioBlob] = useState<Blob | null>(null);
  const [audioUrl, setAudioUrl] = useState("");
  const [waveform, setWaveform] = useState<number[]>(Array.from({ length: 32 }, () => 0.08));
  const [permissionError, setPermissionError] = useState("");
  const [pipelinePhase, setPipelinePhase] = useState<NotePipelinePhase>("idle");
  const [transcript, setTranscript] = useState("");
  const [rawTranscript,setRawTranscript]=useState("");
  const audioPickerRef=useRef<HTMLInputElement|null>(null);
  const [organizedText, setOrganizedText] = useState("");
  const [noteVersion, setNoteVersion] = useState(1);
  const currentChapter = parsedChapters?.find((chapter) => chapter.chapter_id === (noteCaptureIntent?.anchor?.chapterId ?? activeChapterId));
  const anchor = noteCaptureIntent?.anchor ?? (uploadedFile ? {
    courseId:courses.state.activeCourseId??undefined,
    bookId: uploadedFile.bookId,
    bookTitle: uploadedFile.name,
    chapterId: currentChapter?.chapter_id,
    chapterTitle: currentChapter?.source_title,
    pageStart: currentChapter?.page_start,
    pageEnd: currentChapter?.page_end
  } : undefined);

  function stopMediaTracks() {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    if (animationFrameRef.current !== null) cancelAnimationFrame(animationFrameRef.current);
    animationFrameRef.current = null;
    if (timerRef.current !== null) window.clearInterval(timerRef.current);
    timerRef.current = null;
    void audioContextRef.current?.close().catch(() => undefined);
    audioContextRef.current = null;
  }

  function buildVoiceNote(overrides: Partial<VoiceStudyNote> = {}): VoiceStudyNote {
    const now = Date.now();
    return {
      id: noteIdRef.current,
      kind: "voice",
      title: anchor?.chapterTitle ? `${anchor.chapterTitle} · 语音笔记` : "我的语音笔记",
      anchor,
      audioId: audioIdRef.current,
      mimeType: audioBlob?.type,
      durationMs,
      sizeBytes: audioBlob?.size ?? 0,
      waveform: downsampleWaveform(waveformRef.current.length ? waveformRef.current : waveform),
      transcript: rawTranscript || undefined,
      recognizedText: rawTranscript || undefined,
      confirmedTranscript:transcript || undefined,
      organizedText: organizedText || undefined,
      organizedFromVersion: organizedText ? noteVersion : undefined,

      createdAt: now,
      updatedAt: now,
      noteVersion,
      pipelinePhase,

      ...overrides
    };
  }

  async function saveVoiceDraft(overrides: Partial<VoiceStudyNote> = {}) {
    const existing = await getStudyNote(noteIdRef.current);
    const organizedVersions = [...(existing?.organizedVersions ?? [])];
    if (overrides.organizedText && typeof overrides.organizedFromVersion === "number") {
      const alreadySaved = organizedVersions.some((version) => (
        version.noteVersion === overrides.organizedFromVersion && version.text === overrides.organizedText
      ));
      if (!alreadySaved) organizedVersions.push({
        noteVersion: overrides.organizedFromVersion,
        text: overrides.organizedText,
        createdAt: Date.now()
      });
    }
    const next = buildVoiceNote({
      createdAt: existing?.createdAt ?? Date.now(),
      organizedVersions,
      ...overrides,
      updatedAt: Date.now()
    });
    await putStudyNote(next);
    return next;
  }

  async function acceptRecordedBlob(blob: Blob, nextDuration: number, nextWaveform: number[]) {
    if(nextDuration>maximumDurationMs)throw new Error("录音超过10分钟，请缩短后再导入");
    if (blob.size > maximumSizeBytes) {
      setPermissionError("录音超过 20MB，请缩短后重新录制。");
      return;
    }
    const previousAudioId = audioIdRef.current;
    const nextAudioId = `audio-${noteIdRef.current}-${Date.now()}`;
    audioIdRef.current = nextAudioId;
await saveVoiceDraft({audioId:nextAudioId,mimeType:blob.type,durationMs:nextDuration,sizeBytes:blob.size,waveform:nextWaveform,noteVersion:noteVersion+1,confirmedTranscript:undefined,transcript:undefined,recognizedText:undefined,organizedText:undefined,organizedFromVersion:undefined,evidence:undefined,pipelinePhase:"idle"});
    const persisted = await saveAudioBlob(nextAudioId, blob);
    if (!persisted) showToast("录音仅保留在当前会话，本设备音频存储暂不可用", "warning");
    if (previousAudioId) void deleteAudioBlob(previousAudioId);
    setAudioBlob(blob);
    setDurationMs(nextDuration);
    setWaveform(nextWaveform);
    waveformRef.current = nextWaveform;
    setRecordingState("ready");
    setPipelinePhase("idle");
    setTranscript("");setRawTranscript("");
    setOrganizedText("");
    const nextVersion = noteVersion + 1;
    setNoteVersion(nextVersion);
    await saveVoiceDraft({
      audioId: nextAudioId,
      mimeType: blob.type,
      durationMs: nextDuration,
      sizeBytes: blob.size,
      waveform: nextWaveform,
      confirmedTranscript:undefined,
      transcript: undefined,
      recognizedText: undefined,
      organizedText: undefined,
      organizedFromVersion: undefined,
      evidence: undefined,
      noteVersion: nextVersion,
      pipelinePhase: "idle"
    });
  }

  function stopRecording() {
    const recorder = recorderRef.current;
    if (!recorder || recorder.state === "inactive") return;
    if (recorder.state === "paused") recorder.resume();
    recorder.stop();
  }

  async function startRecording() {
    setPermissionError("");
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setPermissionError("当前 Android WebView 不支持网页录音，请导入真实录音。");
      return;
    }
    setRecordingState("requesting");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true }, video: false });
      streamRef.current = stream;
      const mimeType = chooseRecordingMimeType();
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType, audioBitsPerSecond: 96_000 } : undefined);
      recorderRef.current = recorder;
      chunksRef.current = [];
      discardRecordingRef.current = false;
      waveformRef.current = [];
      totalPausedRef.current = 0;
      startedAtRef.current = performance.now();
      recorder.addEventListener("dataavailable", (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data);
      });
      recorder.addEventListener("stop", () => {
        if (discardRecordingRef.current) {
          stopMediaTracks();
          return;
        }
        const elapsed = Math.min(maximumDurationMs, Math.max(0, performance.now() - startedAtRef.current - totalPausedRef.current));
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || mimeType || "audio/webm" });
        stopMediaTracks();
        void acceptRecordedBlob(blob, elapsed, downsampleWaveform(waveformRef.current)).catch(() => {
          setPermissionError("录音保存失败，请重新录制。");
          setRecordingState("idle");
        });
      });
      const audioContext = new AudioContext();
      audioContextRef.current = audioContext;
      const analyser = audioContext.createAnalyser();
      analyser.fftSize = 256;
      audioContext.createMediaStreamSource(stream).connect(analyser);
      const samples = new Uint8Array(analyser.frequencyBinCount);
      const readLevel = () => {
        analyser.getByteTimeDomainData(samples);
        const level = Math.max(0.05, Math.sqrt(samples.reduce((sum, sample) => sum + ((sample - 128) / 128) ** 2, 0) / samples.length));
        waveformRef.current.push(Math.min(1, level * 4));
        setWaveform((current) => [...current.slice(-47), Math.min(1, level * 4)]);
        animationFrameRef.current = requestAnimationFrame(readLevel);
      };
      readLevel();
      recorder.start(500);
      setRecordingState("recording");
      timerRef.current = window.setInterval(() => {
        const elapsed = performance.now() - startedAtRef.current - totalPausedRef.current;
        setDurationMs(Math.min(maximumDurationMs, elapsed));
        if (elapsed >= maximumDurationMs) stopRecording();
      }, 200);
    } catch (error) {
      stopMediaTracks();
      setRecordingState("idle");
      setPermissionError(error instanceof DOMException && error.name === "NotAllowedError"
        ? "麦克风权限未开启。请在 Android 设置中允许 BookCourse AI 使用麦克风，然后重新尝试。"
        : "无法启动麦克风，请检查系统权限或导入真实录音。");
    }
  }

  function togglePause() {
    const recorder = recorderRef.current;
    if (!recorder) return;
    if (recorder.state === "recording") {
      recorder.pause();
      pausedAtRef.current = performance.now();
      setRecordingState("paused");
      return;
    }
    if (recorder.state === "paused") {
      recorder.resume();
      totalPausedRef.current += performance.now() - pausedAtRef.current;
      setRecordingState("recording");
    }
  }

  async function useSampleRecording() { audioPickerRef.current?.click(); }
  async function importAudio(file:File){
    try {
      const context=new AudioContext();const decoded=await context.decodeAudioData(await file.arrayBuffer());
      const samples=decoded.getChannelData(0),size=Math.max(1,Math.floor(samples.length/48));
      const wave=Array.from({length:48},(_,i)=>{let square=0,count=0;for(let offset=i*size;offset<Math.min((i+1)*size,samples.length);offset++){square+=samples[offset]**2;count++;}return count?Math.min(1,Math.sqrt(square/count)*4):0;});
      await context.close();await acceptRecordedBlob(file,decoded.duration*1000,wave);setPermissionError("");
    }catch(error){setPermissionError(error instanceof Error?error.message:"录音文件无法读取");}
  }
  async function transcribeRecording() {
    if(!audioBlob)return;
    try {
      setPipelinePhase("transcribing"); await saveVoiceDraft({pipelinePhase:"transcribing"});
      const result=await processStudyNote(noteIdRef.current!,"transcribe");
      if(result.kind!=="voice")throw new Error("转写结果不完整");
      setRawTranscript(result.transcript??result.recognizedText??"");setTranscript(result.transcript??result.recognizedText??"");setPipelinePhase(result.pipelinePhase);
    } catch(error){setPipelinePhase("error");showToast(error instanceof Error?error.message:"转写失败", "warning");}
  }
  async function organizeTranscript() {
    if(!transcript.trim())return;
    try {
      setPipelinePhase("organizing");await saveVoiceDraft({pipelinePhase:"organizing",confirmedTranscript:transcript});
      const result=await processStudyNote(noteIdRef.current!,"organize",{transcript,consent:true});
      setOrganizedText(result.organizedText??"");setPipelinePhase(result.pipelinePhase);showToast("语音笔记已整理");
    } catch(error){setPipelinePhase("error");showToast(error instanceof Error?error.message:"整理失败", "warning");}
  }

  useEffect(() => {
    let cancelled = false;
    const existingId = noteCaptureIntent?.existingNoteId;
    if (!existingId) return;
    noteIdRef.current = existingId;
    void getStudyNote(existingId).then(async (note) => {
      if (cancelled || note?.kind !== "voice") return;
      audioIdRef.current = note.audioId;
      const blob = note.audioId ? await getAudioBlob(note.audioId) : null;
      if (cancelled) return;
      setAudioBlob(blob);
      setDurationMs(note.durationMs);
      setWaveform(note.waveform);
      waveformRef.current = note.waveform;
      setRawTranscript(note.transcript??note.recognizedText??"");setTranscript(note.confirmedTranscript??note.transcript??note.recognizedText??"");
      setOrganizedText(note.organizedText ?? "");
      setPipelinePhase(note.pipelinePhase);
      setNoteVersion(note.noteVersion);
      setRecordingState(blob ? "ready" : "idle");
    });
    return () => { cancelled = true; };
  }, [noteCaptureIntent?.existingNoteId]);

  useEffect(() => {
    if (!audioBlob) {
      setAudioUrl("");
      return;
    }
    const url = URL.createObjectURL(audioBlob);
    setAudioUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [audioBlob]);

  useEffect(() => {
    let handle: PluginListenerHandle | undefined;
    void CapacitorApp.addListener("appStateChange", ({ isActive }) => {
      if (!isActive && recorderRef.current?.state !== "inactive") stopRecording();
    }).then((next) => { handle = next; });
    const handleNativeBack = (event: Event) => {
      if (recorderRef.current?.state === "recording" || recorderRef.current?.state === "paused") {
        event.preventDefault();
        if (window.confirm("正在录音。是否保存已经录下的片段？")) {
          stopRecording();
          showToast("录音已结束并保留", "info");
        } else {
          const recorder = recorderRef.current;
          if (recorder && recorder.state !== "inactive") {
            discardRecordingRef.current = true;
            recorder.stop();
          }
          stopMediaTracks();
          chunksRef.current = [];
          setRecordingState("idle");
          setDurationMs(0);
          setWaveform([]);
          waveformRef.current = [];
          showToast("已放弃本次录音", "info");
        }
        return;
      }
      if (embedded) {
        event.preventDefault();
        onClose?.();
      }
    };
    window.addEventListener("bookcourse:native-back", handleNativeBack);
    return () => {
      void handle?.remove();
      window.removeEventListener("bookcourse:native-back", handleNativeBack);
      stopMediaTracks();
    };
  }, []);

  const processing = ["transcribing", "retrieving", "organizing", "reviewing"].includes(pipelinePhase);
  const processingLabel: Partial<Record<NotePipelinePhase, string>> = {
    transcribing: "正在生成逐字稿…",
    retrieving: "正在查找教材依据…",
    organizing: "正在整理笔记…",
    reviewing: "正在复核事实与引用…"
  };

  return (
    <div className={`screen-stack voice-note-screen${embedded ? " is-embedded" : ""}`}>
      <input ref={audioPickerRef} type="file" accept="audio/*,.wav,.mp3,.m4a,.ogg,.webm" hidden aria-label="导入真实录音" onChange={event=>{const file=event.target.files?.[0];event.target.value="";if(file)void importAudio(file);}} />
      <section className="voice-anchor-card">
        <Pill tone="purple">真实录音 · 服务端整理</Pill>
        <div>
          <BookOpenCheck size={20} aria-hidden="true" />
          <span>
            <strong>{anchor?.chapterTitle ?? anchor?.bookTitle ?? "自由语音笔记"}</strong>
            <small>{anchor?.pageStart ? `教材第 ${anchor.printedPageStart ?? anchor.pageStart}${anchor.pageEnd && anchor.pageEnd !== anchor.pageStart ? `–${anchor.printedPageEnd ?? anchor.pageEnd}` : ""} 页` : "可在整理前补充章节"}</small>
          </span>
        </div>
        {anchor?.quote ? <blockquote>{anchor.quote}</blockquote> : null}
      </section>

      <div className="voice-note-workspace">
        <Card className={`voice-recorder-card is-${recordingState}`}>
          <div className="voice-waveform" aria-label="实时录音波形">
            {waveform.map((value, index) => <i key={index} style={{ height: `${Math.max(8, value * 78)}%` }} />)}
          </div>
          <div className="voice-recorder-time">{formatDuration(durationMs)}</div>
          <h2>{recordingState === "recording" ? "正在听你说…" : recordingState === "paused" ? "录音已暂停" : recordingState === "ready" ? "这段想法已经记下" : "录一段语音笔记"}</h2>
          <p>{recordingState === "ready" ? "先回放确认，再生成可编辑逐字稿。" : "最长 10 分钟；原始录音会与整理版分别保留。"}</p>

          {permissionError ? (
            <div className="voice-permission-error" role="alert">
              <strong>无法使用麦克风</strong>
              <p>{permissionError}</p>
              <div>
                <Button variant="secondary" onClick={() => void startRecording()}>重新尝试</Button>
                <Button variant="text" onClick={() => void useSampleRecording()}>导入真实录音</Button>
              </div>
            </div>
          ) : null}

          <div className="voice-recorder-controls">
            {recordingState === "recording" || recordingState === "paused" ? (
              <>
                <button type="button" aria-label={recordingState === "paused" ? "继续录音" : "暂停录音"} onClick={togglePause}>
                  {recordingState === "paused" ? <Play size={24} /> : <Pause size={24} />}
                </button>
                <button className="voice-record-main is-stop" type="button" aria-label="结束录音" onClick={stopRecording}><CircleStop size={31} /></button>
              </>
            ) : recordingState === "ready" ? (
              <>
                <button type="button" aria-label="重新录制" onClick={() => { setAudioBlob(null); setDurationMs(0); setWaveform([]); setRecordingState("idle"); }}><RotateCcw size={22} /></button>
                {audioUrl ? <audio className="voice-audio-player" controls src={audioUrl} aria-label="语音笔记录音" /> : null}
              </>
            ) : (
              <button className="voice-record-main" type="button" aria-label="开始录音" disabled={recordingState === "requesting"} onClick={() => void startRecording()}>
                {recordingState === "requesting" ? <LoaderCircle className="spin" size={30} /> : <Mic2 size={30} />}
              </button>
            )}
          </div>

          {recordingState === "idle" && !permissionError ? <Button variant="text" onClick={() => void useSampleRecording()}>导入真实录音</Button> : null}
          {recordingState === "ready" && pipelinePhase === "idle" ? (
            <Button icon={<Sparkles size={18} />} onClick={() => void transcribeRecording()}>完成并整理</Button>
          ) : null}
          {processing ? (
            <div className="voice-processing" role="status"><LoaderCircle className="spin" size={20} />{processingLabel[pipelinePhase] ?? "正在处理…"}</div>
          ) : null}
        </Card>

        <div className="voice-note-result-column">
          {pipelinePhase === "needs_confirmation" ? (
            <Card className="voice-transcript-card">
              <Pill tone="sky">请确认逐字稿</Pill>
              <h2>AI 不会替你猜测原话</h2>
              <p>逐字稿来自你的原始录音。请回放核对并修正；确认稿与原始识别结果分别保留。</p>
              <textarea aria-label="语音笔记逐字稿" value={transcript} onChange={(event) => setTranscript(event.target.value)} />
              <Button icon={<Save size={18} />} disabled={!transcript.trim()} onClick={() => void organizeTranscript()}>确认并继续整理</Button>
            </Card>
          ) : null}

          {pipelinePhase === "complete" && organizedText ? (
            <Card className="voice-organized-card">
              <Pill tone="mint">已完成教材核验</Pill>
              <h2>独立整理版</h2>
              <p className="voice-organized-copy">{organizedText.replace(/^##\s*/u, "").replace(/\n[-\d.\s*]+/gu, " ")}</p>
              <div className="voice-evidence-list">
              </div>
              <small>原始录音与确认后的逐字稿均已保留，整理版不会覆盖它们。</small>
              <Button variant="secondary" onClick={() => showToast("整理版已保留")}>保留整理版</Button>
            </Card>
          ) : null}
        </div>
      </div>
    </div>
  );
}
