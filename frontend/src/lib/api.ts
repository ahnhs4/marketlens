import { BASE, STATIC, type Fallback, fallbacks, snapBody, snapKey, withView } from "./static";

// Thin fetch wrapper. Errors carry the backend's {code, message} so sections can show
// an accurate fallback ("Data unavailable", "Ticker not found", rate limit...).

export class ApiError extends Error {
  status: number;
  code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function parse(res: Response) {
  const text = await res.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = null;
  }
  if (!res.ok) {
    const d = (body as { detail?: unknown } | null)?.detail;
    if (d && typeof d === "object" && "message" in (d as Record<string, unknown>)) {
      const dd = d as { code?: string; message: string };
      throw new ApiError(res.status, dd.code ?? "error", dd.message);
    }
    const msg =
      res.status === 429
        ? "요청이 너무 많습니다 - 잠시 후 다시 시도하세요"
        : res.status >= 500
          ? "서버 오류 (Data unavailable)"
          : typeof d === "string"
            ? d
            : `요청 실패 (${res.status})`;
    throw new ApiError(res.status, res.status === 404 ? "not_found" : "error", msg);
  }
  return body;
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  if (STATIC) return snapshot<T>(path, init);
  let res: Response;
  try {
    res = await fetch(path, { ...init, credentials: "same-origin", headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) } });
  } catch {
    throw new ApiError(0, "network", "백엔드 서버에 연결할 수 없습니다 (Data unavailable)");
  }
  return (await parse(res)) as T;
}

export const post = <T>(path: string, body?: unknown) => api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
export const put = <T>(path: string, body?: unknown) => api<T>(path, { method: "PUT", body: JSON.stringify(body) });
export const patch = <T>(path: string, body?: unknown) => api<T>(path, { method: "PATCH", body: JSON.stringify(body) });
export const del = <T>(path: string) => api<T>(path, { method: "DELETE" });

/** Static demo: answer from the frozen snapshot (exact request first, then a compatible one), never the network API. */
async function snapshot<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method ?? "GET").toUpperCase();
  const body = snapBody(path, typeof init?.body === "string" ? init.body : null);
  const tries: (Fallback & { body?: string | null })[] = [{ path, body }];
  if (method === "GET") tries.push(...fallbacks(path));
  for (const t of tries) {
    try {
      const res = await fetch(`${BASE}/snapshot/${snapKey(t.path, t.body)}.json`, { signal: init?.signal });
      if (!res.ok) continue;
      const data = await res.json();
      const adapted = t.adapt ? t.adapt(data) : data;
      if (t.view) {
        const v = await fetch(`${BASE}/snapshot/${snapKey(t.view)}.json`, { signal: init?.signal });
        if (v.ok) return withView(adapted, await v.json()) as T;
      }
      return adapted as T;
    } catch (e) {
      if ((e as Error)?.name === "AbortError") throw e;
    }
  }
  if (method !== "GET") throw new ApiError(0, "static", "데모 페이지는 읽기 전용입니다 - 이 기능은 실제 앱에서만 동작합니다");
  throw new ApiError(404, "not_found", "데모 스냅샷에 없는 데이터입니다 (Data unavailable)");
}
