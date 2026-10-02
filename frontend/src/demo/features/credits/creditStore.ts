import { request } from "../../api/transport";
import { useEffect, useSyncExternalStore } from "react";

export const creditStorageKey = "bookcourse.credits.v1";
export const initialCreditBalance = 100;
export const creditCosts = { chat: 1, video: 10 } as const;

export type CreditAction = keyof typeof creditCosts;
export type CreditTransaction = {
  id: string;
  action: CreditAction;
  amount: number;
  createdAt: number;
  status: "reserved" | "spent";
};
export type CreditState = {
  version: 1;
  balance: number;
  transactions: CreditTransaction[];
};

type CreditStorage = Pick<Storage, "getItem" | "setItem">;

const freshState: CreditState = { version: 1, balance: initialCreditBalance, transactions: [] };

function parseState(raw: string | null): CreditState {
  if (!raw) return freshState;
  try {
    const value: unknown = JSON.parse(raw);
    if (!value || typeof value !== "object") return freshState;
    const candidate = value as Partial<CreditState>;
    if (candidate.version !== 1 || !Number.isSafeInteger(candidate.balance)
      || (candidate.balance ?? -1) < 0 || !Array.isArray(candidate.transactions)) return freshState;
    const transactions = candidate.transactions.filter((item): item is CreditTransaction => (
      item && typeof item.id === "string" && (item.action === "chat" || item.action === "video")
      && Number.isSafeInteger(item.amount) && item.amount > 0
      && Number.isFinite(item.createdAt) && (item.status === "reserved" || item.status === "spent")
    ));
    return { version: 1, balance: candidate.balance!, transactions };
  } catch {
    return freshState;
  }
}

export function createCreditStore(storage: CreditStorage) {
  const listeners = new Set<() => void>();
  let state: CreditState;
  try {
    state = parseState(storage.getItem(creditStorageKey));
  } catch {
    state = freshState;
  }

  // A refresh ends in-flight AI work. Restore reservations left by that page
  // before a new request can consume credits.
  const abandoned = state.transactions.filter((item) => item.status === "reserved");
  if (abandoned.length > 0) {
    const recovered: CreditState = {
      ...state,
      balance: state.balance + abandoned.reduce((sum, item) => sum + item.amount, 0),
      transactions: state.transactions.filter((item) => item.status === "spent")
    };
    try {
      storage.setItem(creditStorageKey, JSON.stringify(recovered));
      state = recovered;
    } catch {
      // Keep the saved balance until storage becomes writable again.
    }
  }

  function publish(next: CreditState) {
    state = next;
    listeners.forEach((listener) => listener());
  }

  function persist(next: CreditState) {
    try {
      storage.setItem(creditStorageKey, JSON.stringify(next));
    } catch {
      throw new Error("积分保存失败，本次操作没有开始。请检查设备存储空间后重试。");
    }
    publish(next);
  }

  function reload() {
    let next: CreditState;
    try {
      next = parseState(storage.getItem(creditStorageKey));
    } catch {
      return;
    }
    if (JSON.stringify(next) !== JSON.stringify(state)) publish(next);
  }

  function reserve(action: CreditAction) {
    reload();
    const amount = creditCosts[action];
    if (state.balance < amount) {
      throw new Error(`积分不足：本次需要 ${amount} 积分，当前剩余 ${state.balance} 积分。`);
    }
    const id = crypto.randomUUID();
    persist({
      ...state,
      balance: state.balance - amount,
      transactions: [...state.transactions, { id, action, amount, createdAt: Date.now(), status: "reserved" as const }].slice(-100)
    });
    return id;
  }

  function complete(id: string) {
    const transaction = state.transactions.find((item) => item.id === id && item.status === "reserved");
    if (!transaction) return;
    persist({
      ...state,
      transactions: state.transactions.map((item) => item.id === id ? { ...item, status: "spent" } : item)
    });
  }

  function refund(id: string) {
    const transaction = state.transactions.find((item) => item.id === id);
    if (!transaction) return;
    persist({
      ...state,
      balance: state.balance + transaction.amount,
      transactions: state.transactions.filter((item) => item.id !== id)
    });
  }

  return {
    getSnapshot: () => state,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => { listeners.delete(listener); };
    },
    reload,
    reserve,
    complete,
    refund
  };
}

let state:CreditState={version:1,balance:0,transactions:[]};
const listeners=new Set<()=>void>();
function publish(next:CreditState){state=next;listeners.forEach(fn=>fn());}
let loaded=false;
let epoch=0;
async function reload(){const ticket=epoch;const next=await request<CreditState>("/api/demo/credits");if(ticket===epoch)publish(next);}
export function resetCredits(){epoch++;loaded=false;publish({version:1,balance:0,transactions:[]});}
export function useCredits(){
 const hookEpoch=epoch;
 const guard=()=>{if(hookEpoch!==epoch)throw new Error("账号已切换，旧积分操作已取消");};
 const current=useSyncExternalStore(fn=>{listeners.add(fn);return()=>{listeners.delete(fn);};},()=>state,()=>state);
 useEffect(()=>{if(!loaded){loaded=true;void reload().catch(()=>{loaded=false;});}},[]);
 return {...current,
 reserve:async(action:CreditAction)=>{guard();const ticket=epoch;const result=await request<{state:CreditState;reservationId:string}>("/api/demo/credits/reserve",{method:"POST",body:JSON.stringify({action,id:crypto.randomUUID()})});if(ticket===epoch)publish(result.state);return result.reservationId;},
 complete:async(id:string)=>{guard();const ticket=epoch;const next=await request<CreditState>("/api/demo/credits/complete",{method:"POST",body:JSON.stringify({id})});if(ticket===epoch)publish(next);},
 refund:async(id:string)=>{guard();const ticket=epoch;const next=await request<CreditState>("/api/demo/credits/refund",{method:"POST",body:JSON.stringify({id})});if(ticket===epoch)publish(next);}
 };
}
