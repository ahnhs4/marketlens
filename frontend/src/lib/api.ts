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
