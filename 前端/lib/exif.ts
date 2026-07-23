/**
 * 照片 EXIF 元信息读取。
 *
 * 规范要求（《数据结构与通信接口规范》1.3 / 《前端真实能力接入规范》9）：
 * - takenAt：从 EXIF 拍摄时间读取，ISO 8601；读不到传 null。
 * - location：从 EXIF/GPS 获取；读不到传 null。
 *
 * 说明：前端仅能从 EXIF 拿到 GPS 经纬度，无法在不联网的前提下反查地名，
 * 故 location 在能读到坐标时返回 "纬度,经度" 字符串，读不到则 null。
 * 任何解析失败都返回 null，绝不抛错（不阻塞上传与生成流程）。
 */
import exifr from "exifr"

export interface PhotoMeta {
  takenAt: string | null
  location: string | null
}

function toIso(value: unknown): string | null {
  if (!value) return null
  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? null : value.toISOString()
  }
  if (typeof value === "string") {
    const d = new Date(value)
    return Number.isNaN(d.getTime()) ? null : d.toISOString()
  }
  return null
}

export async function readPhotoMeta(file: File): Promise<PhotoMeta> {
  let takenAt: string | null = null
  let location: string | null = null

  try {
    const tags = await exifr.parse(file, ["DateTimeOriginal", "CreateDate", "ModifyDate"])
    if (tags) {
      takenAt = toIso(tags.DateTimeOriginal) ?? toIso(tags.CreateDate) ?? toIso(tags.ModifyDate)
    }
  } catch {
    takenAt = null
  }

  try {
    const gps = await exifr.gps(file)
    if (gps && typeof gps.latitude === "number" && typeof gps.longitude === "number") {
      location = `${gps.latitude.toFixed(5)},${gps.longitude.toFixed(5)}`
    }
  } catch {
    location = null
  }

  return { takenAt, location }
}
