export type MotionState = "entering" | "idle" | "closing";

export type PresenceSnapshot<T> = {
  rendered: T | null;
  state: MotionState;
  presenceId: number;
  key: string | null;
};

export type PresenceRequest<T> = {
  requested: T | null;
  getKey: (value: T) => string;
  reducedMotion: boolean;
};

export function createPresenceSnapshot<T>(): PresenceSnapshot<T> {
  return {
    rendered: null,
    state: "idle",
    presenceId: 0,
    key: null
  };
}

function replacePresence<T>(
  current: PresenceSnapshot<T>,
  rendered: T | null,
  key: string | null,
  state: MotionState
): PresenceSnapshot<T> {
  return {
    rendered,
    key,
    state,
    presenceId: current.presenceId + 1
  };
}

function updateRendered<T>(current: PresenceSnapshot<T>, rendered: T, state = current.state): PresenceSnapshot<T> {
  if (current.rendered === rendered && current.state === state) return current;
  return { ...current, rendered, state };
}

/**
 * Reconciles the requested surface with the one that may still be rendered.
 * Consumers must pass immutable view snapshots when their closing content needs
 * to remain frozen; the machine deliberately retains the last rendered value.
 */
export function reconcilePresence<T>(
  current: PresenceSnapshot<T>,
  { requested, getKey, reducedMotion }: PresenceRequest<T>
): PresenceSnapshot<T> {
  const requestedKey = requested === null ? null : getKey(requested);

  if (reducedMotion) {
    if (requested === null) {
      if (current.rendered === null && current.state === "idle") return current;
      return replacePresence(current, null, null, "idle");
    }

    if (current.rendered === null || current.key !== requestedKey || current.state === "closing") {
      return replacePresence(current, requested, requestedKey, "idle");
    }

    return updateRendered(current, requested, "idle");
  }

  if (requested === null) {
    if (current.rendered === null || current.state === "closing") return current;
    return replacePresence(current, current.rendered, current.key, "closing");
  }

  if (current.rendered === null || current.key !== requestedKey || current.state === "closing") {
    return replacePresence(current, requested, requestedKey, "entering");
  }

  return updateRendered(current, requested);
}

/**
 * Handles a valid animation end, animation cancel, or defensive fallback for
 * the supplied generation. Stale generations are intentionally ignored.
 */
export function settlePresence<T>(
  current: PresenceSnapshot<T>,
  presenceId: number
): PresenceSnapshot<T> {
  if (presenceId !== current.presenceId) return current;

  if (current.state === "entering") {
    return { ...current, state: "idle" };
  }

  if (current.state === "closing") {
    return {
      ...current,
      rendered: null,
      key: null,
      state: "idle"
    };
  }

  return current;
}
