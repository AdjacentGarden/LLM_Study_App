import { StickerIcon } from "../../components/icons/StickerIcon";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { useMotionPresence } from "../../motion/useMotionPresence";
import { useReducedMotion } from "../../motion/useReducedMotion";
import type { MotionAnimationEvent } from "../../motion/useMotionPresence";
import { textNotePosition } from "./textAnnotations";
import type { TextNotePosition, TextStudyNote } from "./types";

export type TextNoteLocation = {
  position: TextNotePosition;
  pageElement: HTMLElement;
};

export type TextNoteDraftMarker = { id: string; position: TextNotePosition };
type TextNoteMarker = TextNoteDraftMarker & { note?: TextStudyNote };
const markerAnimationNames = ["motion-text-note-marker-in", "motion-text-note-marker-out"] as const;
const markerKey = (marker: TextNoteMarker) => marker.id;

function SourceTextNoteMarker({ marker, present, active, reducedMotion, onOpen, onDraftActivate, onClosed }: {
  marker: TextNoteMarker;
  present: boolean;
  active: boolean;
  reducedMotion: boolean;
  onOpen: (note: TextStudyNote, location: TextNoteLocation) => void;
  onDraftActivate?: () => void;
  onClosed: (id: string) => void;
}) {
  const spanRef = useRef<HTMLSpanElement>(null);
  const motion = useMotionPresence({ requested: present ? marker : null, getKey: markerKey, reducedMotion, motionNames: markerAnimationNames });
  const expectedAnimation = motion.state === "entering" ? markerAnimationNames[0]
    : motion.state === "closing" ? markerAnimationNames[1] : null;

  const settleAnimation = (event: MotionAnimationEvent, settle: typeof motion.onAnimationEnd) => {
    if (event.animationName === expectedAnimation) settle(event);
  };

  useEffect(() => {
    const span = spanRef.current;
    if (!span) return;
    const handleCancel = (event: AnimationEvent) => settleAnimation(event, motion.onAnimationCancel);
    span.addEventListener("animationcancel", handleCancel);
    return () => span.removeEventListener("animationcancel", handleCancel);
  });

  useEffect(() => {
    if (!present && !motion.rendered) onClosed(marker.id);
  }, [marker.id, motion.rendered, onClosed, present]);

  const rendered = motion.rendered;
  if (!rendered) return null;
  const closing = !present || motion.state === "closing";
  return (
    <button
      className="source-text-note-marker"
      type="button"
      aria-label={rendered.note ? `查看文字批注：${rendered.note.body.slice(0, 32)}` : "正在编辑文字批注"}
      aria-expanded={active}
      aria-hidden={closing || undefined}
      disabled={closing}
      title={rendered.note?.body.slice(0, 80) ?? "正在编辑文字批注"}
      data-note-id={rendered.id}
      data-note-draft={!rendered.note}
      data-motion-state={motion.state}
      data-motion-presence={motion.presenceId}
      style={{
        left: `clamp(22px, ${rendered.position.x * 100}%, calc(100% - 22px))`,
        top: `clamp(22px, ${rendered.position.y * 100}%, calc(100% - 22px))`
      }}
      onPointerDown={(event) => event.stopPropagation()}
      onPointerUp={(event) => event.stopPropagation()}
      onClick={(event) => {
        event.stopPropagation();
        if (rendered.note) onOpen(rendered.note, { position: rendered.position, pageElement: event.currentTarget.parentElement!.parentElement! });
        else onDraftActivate?.();
      }}
    >
      <span ref={spanRef} onAnimationEnd={(event) => settleAnimation(event, motion.onAnimationEnd)}>
        <StickerIcon name="MessageSquareText" size={15} aria-hidden="true" />
      </span>
    </button>
  );
}

export function SourceTextAnnotationLayer({
  activeNoteId,
  draft,
  enabled,
  notes,
  placing,
  onOpen,
  onDraftActivate,
  onPlace
}: {
  activeNoteId?: string;
  draft?: TextNoteDraftMarker;
  enabled: boolean;
  notes: TextStudyNote[];
  placing: boolean;
  onOpen: (note: TextStudyNote, location: TextNoteLocation) => void;
  onDraftActivate?: () => void;
  onPlace: (location: TextNoteLocation) => void;
}) {
  const pointersRef = useRef(new Map<number, { x: number; y: number }>());
  const multiplePointersRef = useRef(false);
  const reducedMotion = useReducedMotion();
  const requested = useMemo<TextNoteMarker[]>(() => {
    if (!enabled) return [];
    const markers = notes.filter((note) => note.position && note.body.trim())
      .map((note) => ({ id: note.id, position: note.position!, note }));
    if (draft && !markers.some((marker) => marker.id === draft.id)) return [...markers, draft];
    return markers;
  }, [draft, enabled, notes]);
  const requestedIds = new Set(requested.map(markerKey));
  const requestedIdsRef = useRef(requestedIds);
  requestedIdsRef.current = requestedIds;
  const [retained, setRetained] = useState<TextNoteMarker[]>([]);
  const markers = [...requested, ...retained.filter((marker) => !requestedIds.has(marker.id))];

  // Include new drafts in this render, and retain removed markers until their
  // exit finishes. Saving a draft updates the same ID without replaying entry.
  useLayoutEffect(() => {
    const ids = new Set(requested.map(markerKey));
    setRetained((previous) => [...requested, ...previous.filter((marker) => !ids.has(marker.id))]);
  }, [requested]);
  const onClosed = useCallback((id: string) => {
    if (!requestedIdsRef.current.has(id)) setRetained((previous) => previous.filter((marker) => marker.id !== id));
  }, []);

  return (
    <div className="source-text-annotation-layer">
      {enabled && placing ? (
        <button
          className="source-text-placement-target"
          type="button"
          aria-label="点击原文添加文字批注"
          onPointerDown={(event) => {
            if (event.button !== 0) return;
            if (pointersRef.current.size === 0) multiplePointersRef.current = false;
            pointersRef.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
            if (pointersRef.current.size > 1) multiplePointersRef.current = true;
          }}
          onPointerUp={(event) => {
            const start = pointersRef.current.get(event.pointerId);
            pointersRef.current.delete(event.pointerId);
            if (!start || multiplePointersRef.current || Math.hypot(event.clientX - start.x, event.clientY - start.y) > 8) return;
            const pageElement = event.currentTarget.parentElement!.parentElement!;
            onPlace({ position: textNotePosition(event.clientX, event.clientY, pageElement.getBoundingClientRect()), pageElement });
          }}
          onPointerCancel={(event) => pointersRef.current.delete(event.pointerId)}
          onClick={(event) => {
            // Keyboard and assistive activation have no pointer coordinates.
            if (event.detail !== 0) return;
            onPlace({ position: { x: .5, y: .35 }, pageElement: event.currentTarget.parentElement!.parentElement! });
          }}
        />
      ) : null}
      {markers.map((marker) => (
        <SourceTextNoteMarker
          key={marker.id}
          marker={marker}
          present={requestedIds.has(marker.id)}
          active={activeNoteId === marker.id}
          reducedMotion={reducedMotion}
          onOpen={onOpen}
          onDraftActivate={onDraftActivate}
          onClosed={onClosed}
        />
      ))}
    </div>
  );
}
