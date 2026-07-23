/**
 * 统一 HTTP 客户端。
 *
 * 职责（对齐《前端真实能力接入规范》5.1）：
 * 1. 唯一负责拼接 NEXT_PUBLIC_API_BASE_URL，业务代码不得再拼 /api 前缀。
 * 2. 唯一负责后端标准响应体 { code, message, data } 的解包。
 * 3. code === 0 返回 data；code !== 0 抛出 AppError（保留 code 与 message）。
 * 4. 不靠 HTTP 状态判断成功；业务失败通常也是 HTTP 200。
 * 5. 裸 HTTP 错误、网络错误、JSON 解析失败统一兜底为 AppError(1003)。
 *
 * 仅本文件做解包；业务 API 只描述「请求什么」，不再触碰 code/message/data。
 */
import { AppError, type BusinessErrorCode } from "./errors"

interface ApiEnvelope<T> {
  code: number
  message: string
  data: T
}

const KNOWN_CODES: BusinessErrorCode[] = [1001, 1002, 1003, 1004]

function normalizeCode(code: number): BusinessErrorCode {
  return (KNOWN_CODES as number[]).includes(code) ? (code as BusinessErrorCode) : 1003
}

function getBaseUrl(): string {
  return (process.env.NEXT_PUBLIC_API_BASE_URL ?? "").replace(/\/$/, "")
}

function buildUrl(path: string): string {
  const base = getBaseUrl()
  const normalizedPath = path.startsWith("/") ? path : `/${path}`
  return `${base}${normalizedPath}`
}

/**
 * 发起 JSON / FormData 请求并解包标准响应体。
 * @param path 以 / 开头的接口路径（不含 base，不含 /api 重复前缀）
 */
export async function apiClient<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(buildUrl(path), init)
  } catch {
    // 网络错误 / CORS / DNS 等：兜底为服务内部错误
    throw new AppError(1003)
  }

  // 仅未捕获的框架级/网关级异常才会出现 4xx/5xx（规范 0.5）；此处统一兜底
  if (!res.ok) {
    throw new AppError(1003)
  }

  let envelope: ApiEnvelope<T>
  try {
    envelope = (await res.json()) as ApiEnvelope<T>
  } catch {
    throw new AppError(1003)
  }

  if (!envelope || typeof envelope.code !== "number") {
    throw new AppError(1003)
  }

  if (envelope.code === 0) {
    return envelope.data
  }

  throw new AppError(normalizeCode(envelope.code), envelope.message)
}

/** 发送 JSON Body 的便捷封装：自动设置 Content-Type 与序列化。 */
export function jsonInit(method: "POST" | "PUT" | "DELETE", body?: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json; charset=utf-8" },
    body: body === undefined ? undefined : JSON.stringify(body),
  }
}
