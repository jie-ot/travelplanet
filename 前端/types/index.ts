/**
 * 全局类型定义
 *
 * 严格对齐《数据结构与通信接口规范》。
 * - 对外 DTO（PostcardGroup / Postcard / Report / Plan / FileAsset / UploadedPhoto / ReportChartPoint）统一 camelCase
 * - ItineraryData 一支（trip_info / preparations / bookings / itinerary / schedule）统一 snake_case
 * 不得新增、删除、改名或改变可选性。
 */

/* ---------------- 明信片组 (PostcardGroup) ---------------- */

export interface PostcardGroup {
  id: string
  location: string
  startDate: string | null
  endDate: string | null
  dateLabel: string
  coverImage: string
  postcards: Postcard[]
}

/* ---------------- 单张明信片 (Postcard) ---------------- */

export interface Postcard {
  id: string
  title: string
  imageUrl: string
}

/* ---------------- 前端交给后端的单张照片信息 (UploadedPhoto) ---------------- */

export interface UploadedPhoto {
  assetId: string
  imageUrl: string
  takenAt: string | null
  location: string | null
}

/* ---------------- 文件资源 (FileAsset) ---------------- */

export interface FileAsset {
  id: string
  relativePath: string
  mimeType: string
  sizeBytes: number
  usageType: "upload" | "generated_postcard" | "generated_report_cover" | "system"
  status: "temporary" | "attached" | "deleted"
  refCount: number
}

/* ---------------- 旅行人格报告 (Report) ---------------- */

export interface Report {
  id: string
  location: string
  startDate: string | null
  endDate: string | null
  dateLabel: string
  coverImage: string
  personalitySummary: string
  content: string
  chartData: ReportChartPoint[]
}

/* ---------------- 雷达图数据点 (ReportChartPoint) ---------------- */

export type RadarDimension = "自然探索" | "人文体验" | "美食偏好" | "慢节奏" | "社交意愿"

export interface ReportChartPoint {
  dimension: RadarDimension
  value: number
}

/* ---------------- 历史规划 (Plan) ---------------- */

export interface Plan {
  id: string
  location: string
  startDate: string | null
  endDate: string | null
  dateLabel: string
  content: string
  itineraryData: ItineraryData
}

/* ---------------- 结构化行程数据 (ItineraryData) ---------------- */

export interface ItineraryData {
  trip_info: TripInfo
  preparations: Preparation[]
  bookings: Booking[]
  food_recommendations: string[]
  itinerary: DailyItinerary[]
}

export interface TripInfo {
  destination: string
  start_date: string
  end_date: string
  date_label: string
}

export interface Preparation {
  category: string
  items: string
}

export interface Booking {
  type: string
  details: string
}

export interface DailyItinerary {
  id: string
  date: string
  title?: string
  schedules: Schedule[]
}

export interface Schedule {
  id: string
  time_period: string
  start_time?: string | null
  end_time?: string | null
  activity: string
  transport?: string | null
  note?: string | null
}

/* ---------------- 业务状态码（前端展示用，对齐 0.6） ---------------- */

export type BusinessCode = 0 | 1001 | 1002 | 1003 | 1004

/* ---------------- 前端页面目的地（映射到 Next.js App Router URL） ---------------- */

export type PageType =
  | "home"
  | "postcards"
  | "postcard-collection"
  | "reports"
  | "report-detail"
  | "planning"
  | "history"
  | "history-detail"
