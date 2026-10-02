import { describe, expect, it } from "vitest";
import {
  createCreditStore,
  creditStorageKey,
  initialCreditBalance
} from "./creditStore";

function memoryStorage() {
  const values = new Map<string, string>();
  return {
    getItem(key: string) { return values.get(key) ?? null; },
    setItem(key: string, value: string) { values.set(key, value); }
  };
}

describe("credit store", () => {
  it("persists completed chat and video charges across reloads", () => {
    const storage = memoryStorage();
    const store = createCreditStore(storage);
    expect(store.getSnapshot().balance).toBe(initialCreditBalance);

    const chat = store.reserve("chat");
    store.complete(chat);
    const video = store.reserve("video");
    store.complete(video);

    expect(store.getSnapshot().balance).toBe(89);
    expect(createCreditStore(storage).getSnapshot()).toMatchObject({
      balance: 89,
      transactions: [
        { action: "chat", amount: 1, status: "spent" },
        { action: "video", amount: 10, status: "spent" }
      ]
    });
  });

  it("returns credits after a failed request and recovers a pending charge after refresh", () => {
    const storage = memoryStorage();
    const store = createCreditStore(storage);
    const failed = store.reserve("chat");
    store.refund(failed);
    expect(store.getSnapshot().balance).toBe(100);

    store.reserve("video");
    expect(store.getSnapshot().balance).toBe(90);
    const reloaded = createCreditStore(storage);
    expect(reloaded.getSnapshot().balance).toBe(100);
    expect(reloaded.getSnapshot().transactions).toHaveLength(0);
  });

  it("blocks an unaffordable request without changing saved credits", () => {
    const storage = memoryStorage();
    storage.setItem(creditStorageKey, JSON.stringify({ version: 1, balance: 5, transactions: [] }));
    const store = createCreditStore(storage);
    expect(() => store.reserve("video")).toThrow("积分不足：本次需要 10 积分，当前剩余 5 积分。");
    expect(store.getSnapshot().balance).toBe(5);
    expect(JSON.parse(storage.getItem(creditStorageKey)!).transactions).toHaveLength(0);
  });

  it("does not start a charged action when persistence fails", () => {
    const store = createCreditStore({
      getItem: () => null,
      setItem: () => { throw new Error("quota"); }
    });
    expect(() => store.reserve("chat")).toThrow("积分保存失败");
    expect(store.getSnapshot().balance).toBe(100);
  });
});
