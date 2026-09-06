export class ApiError extends Error {
  readonly status: number;
  constructor(message: string, status = 0) { super(message); this.name = "ApiError"; this.status = status; }
}

export function errorMessage(detail: unknown, status: number): string {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (status === 422) return "输入内容不符合要求，请检查后重试。";
  if (status === 429 || status === 503) return "服务当前繁忙，请稍后重试。";
  if (status === 404) return "暂时找不到这份内容，请返回书架重新选择。";
  return "服务暂时无法完成请求，请稍后重试。";
}

/** Never retry mutations automatically: a timed-out write may already be saved. */
export async function request<T>(path: string, init: RequestInit = {}, timeoutMs = 180_000): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  init.signal?.addEventListener("abort", abort, { once: true });
  if (init.signal?.aborted) abort();
  const timer = setTimeout(abort, timeoutMs);
  try {
    const response = await fetch(path, {
      ...init, signal: controller.signal,
      headers: { "Content-Type": "application/json", ...init.headers },
    });
    const value: unknown = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = value && typeof value === "object" && "detail" in value ? value.detail : null;
      throw new ApiError(errorMessage(detail, response.status), response.status);
    }
    if (value === null) throw new ApiError("服务器返回的内容不完整，请重试。", response.status);
    return value as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (controller.signal.aborted) throw new ApiError("等待时间较长，请稍后重试；已保存的学习记录不会被清除。", 408);
    throw new ApiError("网络连接中断，请检查连接后重试。学习进度仍然保留。");
  } finally {
    clearTimeout(timer);
    init.signal?.removeEventListener("abort", abort);
  }
}
