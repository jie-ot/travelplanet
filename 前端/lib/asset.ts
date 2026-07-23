/**
 * 资源路径工具。
 *
 * 规范要求（《数据结构与通信接口规范》0.1 / 《前端真实能力接入规范》6）：
 * - 接口/数据中的图片均为以 /static/ 开头的相对路径。
 * - 完整地址拼接只发生在「前端渲染图片」边界，统一通过 resolveAssetUrl 完成：
 *     (NEXT_PUBLIC_ASSET_BASE_URL || "") + 相对路径
 * - 严禁把拼接后的完整 URL 传回后端。
 *
 * 兼容性：
 * - 真实数据：/static/... → 用 NEXT_PUBLIC_ASSET_BASE_URL 拼接。
 * - 前端静态资源、上传预览、占位图：保持原地址，不拼接资源基址。
 * - 已是完整 http(s) URL 的（理论上不应出现）原样返回，避免二次拼接。
 */

/** 图片加载失败时的占位资源。 */
export const PLACEHOLDER_IMAGE = "/placeholder.svg"

export function resolveAssetUrl(relativePath: string | null | undefined): string {
  if (!relativePath) return ""

  // 本地预览 / data URL / 已完整 URL：不参与 base 拼接
  if (
    relativePath.startsWith("blob:") ||
    relativePath.startsWith("data:") ||
    /^https?:\/\//.test(relativePath)
  ) {
    return relativePath
  }

  // 前端静态资源与占位图
  if (relativePath.startsWith("/images/") || relativePath === PLACEHOLDER_IMAGE) {
    return relativePath
  }

  // 真实后端资源：/static/... 用资源基址拼接
  const base = (process.env.NEXT_PUBLIC_ASSET_BASE_URL ?? "").replace(/\/$/, "")
  const normalized = relativePath.startsWith("/") ? relativePath : `/${relativePath}`
  return `${base}${normalized}`
}

/**
 * 图片加载失败兜底：将 <img> 源替换为占位图，避免页面出现裂图。
 * 已替换过则不再处理，防止占位图本身失败时的死循环。
 */
export function handleImageError(e: { currentTarget: HTMLImageElement }): void {
  const img = e.currentTarget
  if (img.dataset.fallback === "1") return
  img.dataset.fallback = "1"
  img.src = PLACEHOLDER_IMAGE
}

/** 为待上传图片生成仅用于当前页面列表渲染的稳定键。 */
export function shortId(prefix: string): string {
  const code = Math.random().toString(36).slice(2, 8)
  return `${prefix}_${code}`
}
