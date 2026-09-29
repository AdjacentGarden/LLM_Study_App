import { describe, expect, it } from "vitest";
import { createPresenceSnapshot, reconcilePresence, settlePresence } from "./presenceMachine";

type Surface = {
  key: string;
  value: string;
};

const getKey = (surface: Surface) => surface.key;
const alpha: Surface = { key: "alpha", value: "first" };
const alphaUpdate: Surface = { key: "alpha", value: "updated" };
const beta: Surface = { key: "beta", value: "second" };

function request(requested: Surface | null, reducedMotion = false) {
  return { requested, getKey, reducedMotion };
}

describe("presenceMachine", () => {
  it("enters a requested surface and updates same-key idle content without replaying", () => {
    const initial = createPresenceSnapshot<Surface>();
    const entering = reconcilePresence(initial, request(alpha));

    expect(entering).toMatchObject({ rendered: alpha, key: "alpha", state: "entering", presenceId: 1 });

    const idle = settlePresence(entering, entering.presenceId);
    const updated = reconcilePresence(idle, request(alphaUpdate));

    expect(updated).toMatchObject({ rendered: alphaUpdate, key: "alpha", state: "idle", presenceId: entering.presenceId });
  });

  it("freezes the last rendered surface while closing and unmounts on completion", () => {
    const entering = reconcilePresence(createPresenceSnapshot<Surface>(), request(alpha));
    const idle = settlePresence(entering, entering.presenceId);
    const closing = reconcilePresence(idle, request(null));

    expect(closing).toMatchObject({ rendered: alpha, key: "alpha", state: "closing" });
    expect(closing.presenceId).toBeGreaterThan(idle.presenceId);

    const unmounted = settlePresence(closing, closing.presenceId);
    expect(unmounted).toMatchObject({ rendered: null, key: null, state: "idle", presenceId: closing.presenceId });
  });

  it("reopens a closing surface with a new generation and ignores stale completion", () => {
    const entering = reconcilePresence(createPresenceSnapshot<Surface>(), request(alpha));
    const idle = settlePresence(entering, entering.presenceId);
    const closing = reconcilePresence(idle, request(null));
    const reopened = reconcilePresence(closing, request(alphaUpdate));

    expect(reopened).toMatchObject({ rendered: alphaUpdate, key: "alpha", state: "entering" });
    expect(reopened.presenceId).toBeGreaterThan(closing.presenceId);
    expect(settlePresence(reopened, closing.presenceId)).toBe(reopened);
    expect(settlePresence(reopened, reopened.presenceId)).toMatchObject({ state: "idle", rendered: alphaUpdate });
  });

  it("replaces an entering or closing surface without retaining the prior surface", () => {
    const entering = reconcilePresence(createPresenceSnapshot<Surface>(), request(alpha));
    const replacingEntering = reconcilePresence(entering, request(beta));

    expect(replacingEntering).toMatchObject({ rendered: beta, key: "beta", state: "entering" });
    expect(replacingEntering.presenceId).toBeGreaterThan(entering.presenceId);

    const idle = settlePresence(replacingEntering, replacingEntering.presenceId);
    const closing = reconcilePresence(idle, request(null));
    const replacingClosing = reconcilePresence(closing, request(alpha));

    expect(replacingClosing).toMatchObject({ rendered: alpha, key: "alpha", state: "entering" });
    expect(replacingClosing.presenceId).toBeGreaterThan(closing.presenceId);
    expect(settlePresence(replacingClosing, closing.presenceId)).toBe(replacingClosing);
  });

  it("settles entering and closing identically for animation end, cancel, and fallback", () => {
    const entering = reconcilePresence(createPresenceSnapshot<Surface>(), request(alpha));
    for (const completion of ["end", "cancel", "fallback"]) {
      expect(settlePresence(entering, entering.presenceId), completion).toMatchObject({
        rendered: alpha,
        state: "idle"
      });
    }

    const closing = reconcilePresence(settlePresence(entering, entering.presenceId), request(null));
    for (const completion of ["end", "cancel", "fallback"]) {
      expect(settlePresence(closing, closing.presenceId), completion).toMatchObject({
        rendered: null,
        key: null,
        state: "idle"
      });
    }

    expect(settlePresence(closing, closing.presenceId - 1)).toBe(closing);
  });

  it("settles entering and closing synchronously when reduced motion is active", () => {
    const reducedInitial = reconcilePresence(createPresenceSnapshot<Surface>(), request(alpha, true));
    expect(reducedInitial).toMatchObject({ rendered: alpha, state: "idle", presenceId: 1 });

    const entering = reconcilePresence(createPresenceSnapshot<Surface>(), request(alpha));
    const reducedEntering = reconcilePresence(entering, request(alphaUpdate, true));
    expect(reducedEntering).toMatchObject({ rendered: alphaUpdate, state: "idle", presenceId: entering.presenceId });

    const closing = reconcilePresence(reducedEntering, request(null));
    const reducedClosing = reconcilePresence(closing, request(null, true));
    expect(reducedClosing).toMatchObject({ rendered: null, key: null, state: "idle" });
    expect(reducedClosing.presenceId).toBeGreaterThan(closing.presenceId);
    expect(settlePresence(reducedClosing, closing.presenceId)).toBe(reducedClosing);
  });
});
