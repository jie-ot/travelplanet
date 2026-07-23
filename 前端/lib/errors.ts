/**
 * 业务错误与错误码映射。
 *
 * 严格对齐《数据结构与通信接口规范》0.6 与《前端真实能力接入规范》12。
 * - 后端标准响应体为 { code, message, data }，业务失败（code !== 0）多数仍是 HTTP 200。
 * - http-client 在 code !== 0 时抛出 AppError；裸 HTTP / 网络 / JSON 解析失败统一兜底为 AppError(1003)。
 * - 前端展示统一用 CODE_MESSAGE，不直接暴露后端 message 之外的内部细节、堆栈、网关错误。
 */

export type BusinessErrorCode = 1001 | 1002 | 1003 | 1004

/** 业务错误码 → 友好文案（对齐规范 0.6 / §12）。 */
export const CODE_MESSAGE: Record<BusinessErrorCode, string> = {
  1001: "生成失败，请重试",
  1002: "请求参数有误，请检查后重试",
  1003: "服务异常，请稍后再试",
  1004: "内容已被删除",
}

/** 统一业务错误：保留后端 code 与 message，供 UI 决定提示与列表行为。 */
export class AppError extends Error {
  readonly code: BusinessErrorCode

  constructor(code: BusinessErrorCode, message?: string) {
    super(message ?? CODE_MESSAGE[code])
    this.name = "AppError"
    this.code = code
  }
}

/** 任意异常归一化为 AppError；非 AppError（网络/解析/未知）统一视为服务内部错误 1003。 */
export function toAppError(err: unknown): AppError {
  if (err instanceof AppError) return err
  return new AppError(1003)
}

/**
 * 取面向用户的友好文案。
 * 1002 优先展示后端可读 message（字段级提示），其余统一走 CODE_MESSAGE，避免泄漏内部细节。
 */
export function friendlyMessage(err: unknown): string {
  const appErr = toAppError(err)
  if (appErr.code === 1002 && appErr.message) return appErr.message
  return CODE_MESSAGE[appErr.code]
}
